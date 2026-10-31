"""Werkzeug-Wachen: abgebrochene Werkzeuge ehrlich melden und doppelte Aufrufe im selben Zug sauber behandeln.

Befund aus der Sitzung vom 31.10.2026 (Kern-Journal 12:43 bis 12:47, 15 Züge):

1. Abgebrochene Werkzeuge ("vergisst Befehle"). Beginnt die Person zu sprechen, während ein Werkzeug läuft (oder bevor die
   Antwort nach dem Ergebnis beginnt), bricht das Gate den Zug ab (soft_interrupt.py, "Zug bei laufendem Werkzeug") und
   Pipecat setzt im Verlauf das Ergebnis des Werkzeugs auf das Wort "CANCELLED". Das Modell liest das als "gescheitert",
   obwohl der Host die Aktion oft schon ausgeführt hat (maus_scrollen, Seite geöffnet), oder es vergisst den Auftrag,
   wenn der Einwurf nur die Fortsetzung des Satzes war (bildschirm_ansehen, 12:45:14, danach wurde der Bildschirm nicht
   mehr angesehen). AbbruchHinweis ersetzt das Wort durch einen Satz, der beides sagt: kein Ergebnis, Auftrag
   nicht erledigt, bei Aktionen am PC erst prüfen. Schalter WAMO_ABBRUCH_HINWEIS (Standard an, ändert nur Text im Verlauf).

2. Doppelter Aufruf desselben Werkzeugs im selben Zug (12:44:35, zwei Amazon-Suchen im Abstand von 1,2 s). Die Aufrufe
   gehören zu zwei aufeinanderfolgenden Antworten des Modells ("2 Antworten"), nicht zu zwei gleichzeitigen: Das Modell
   ruft nach dem Ergebnis der ersten Seite noch einmal auf. Ob die Person zwei Dinge wollte ("Elektrofahrzeuge und E-Bikes")
   oder das Modell sich selbst korrigiert hat, lässt sich aus dem Log ohne Inhalte nicht entscheiden. Deshalb wird nichts
   verboten, was ein echter Mehrfach-Auftrag sein kann:
     * dieselbe Aktion mit denselben Argumenten im selben Zug wird nicht noch einmal ausgeführt (der Host fängt das nur in
       6 Sekunden ab),
     * eine weitere Seite derselben Webseite bekommt im Ergebnis einen Hinweis (beide Tabs sind offen, nur kurz sagen,
       was offen ist, nichts weiter öffnen),
     * ab der vierten Seite derselben Webseite im selben Zug wird nicht mehr geöffnet,
     * ein neuer Beitrag der Person (neuer Zug) setzt alles zurück: dann ist es ein neuer Auftrag.
   Schalter WAMO_WERKZEUG_DOPPELT (Standard an: beseitigt nur Wiederholungen, die die Person nie verlangt hat).
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from loguru import logger

from env_schalter import schalter_an

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def abbruch_hinweis_an(wert: str | None = None) -> bool:
    """WAMO_ABBRUCH_HINWEIS: ein abgebrochenes Werkzeug wird dem Modell als "ohne Ergebnis, Auftrag offen" gemeldet (Standard an)."""
    return schalter_an("WAMO_ABBRUCH_HINWEIS", wert)


def werkzeug_doppelt_an(wert: str | None = None) -> bool:
    """WAMO_WERKZEUG_DOPPELT: doppelte Aufrufe desselben Werkzeugs im selben Zug behandeln (Standard an)."""
    return schalter_an("WAMO_WERKZEUG_DOPPELT", wert)


# --- 1. Abgebrochene Werkzeuge ------------------------------------------------------------------------------------------

CANCELLED = "CANCELLED"  # so schreibt Pipecat ein abgebrochenes Ergebnis in den Verlauf
# Werkzeuge, die nichts verändern: nach einem Abbruch einfach erneut ausführen, wenn der Auftrag noch gilt
LESE_WERKZEUGE = frozenset({
    "bildschirm_ansehen", "bildschirmfoto", "fenster_auflisten", "system_info", "powershell_lesen", "websuche",
    "aktuelle_zeit", "elemente_auflisten", "element_lesen",
})
HINWEIS_LESEN = (
    "ABGEBROCHEN ohne Ergebnis: Die Person hat gesprochen, bevor {name} fertig war. Der Auftrag ist nicht erledigt. "
    "War ihr Einwurf nur die Fortsetzung ihrer Bitte oder ein Laut der Zustimmung, ruf {name} jetzt noch einmal auf, "
    "bevor du antwortest. Hat sie etwas anderes verlangt, gilt das Neue."
)
HINWEIS_AKTION = (
    "ABGEBROCHEN, Ergebnis unbekannt: Die Person hat gesprochen, bevor {name} fertig war. Die Aktion kann schon "
    "gewirkt haben (Seite geöffnet, gescrollt, getippt). Wiederhol sie nicht blind: prüf zuerst kurz den Zustand "
    "(fenster_auflisten oder bildschirm_ansehen) und tu nur, was noch fehlt. Hat die Person etwas anderes verlangt, gilt das Neue."
)
HINWEIS_UNBEKANNT = (
    "ABGEBROCHEN ohne Ergebnis: Die Person hat gesprochen, bevor das Werkzeug fertig war. Ob es etwas bewirkt hat, ist "
    "unklar. Gilt der Auftrag noch, prüf kurz den Zustand, bevor du ihn wiederholst."
)


def _werkzeug_namen(messages: list) -> dict[str, str]:
    """tool_call_id -> Werkzeugname aus den Nachrichten des Assistenten im Verlauf."""
    namen: dict[str, str] = {}
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for aufruf in m.get("tool_calls") or []:
            try:
                namen[aufruf["id"]] = aufruf["function"]["name"]
            except (KeyError, TypeError):
                continue
    return namen


def abbruch_text(name: str | None) -> str:
    if not name:
        return HINWEIS_UNBEKANNT
    if name in LESE_WERKZEUGE:
        return HINWEIS_LESEN.format(name=name)
    return HINWEIS_AKTION.format(name=name)


def abbrueche_umschreiben(messages: list) -> int:
    """Ersetzt in den Ergebnissen abgebrochener Werkzeuge das Wort CANCELLED durch einen erklärenden Satz (in place).
    Gibt die Zahl der geänderten Ergebnisse zurück."""
    n = 0
    namen: dict[str, str] | None = None
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("content") == CANCELLED:
            if namen is None:
                namen = _werkzeug_namen(messages)
            m["content"] = abbruch_text(namen.get(str(m.get("tool_call_id"))))
            n += 1
    return n


class AbbruchHinweis(FrameProcessor):
    """Schreibt abgebrochene Werkzeugergebnisse im Verlauf um (siehe Moduldoc, Punkt 1). Anfragen an das Modell kommen von
    vorn (Nutzer-Aggregator) und von hinten (Assistent-Aggregator, z. B. nach dem Abbruch selbst): bot.py setzt je eine Instanz
    vor die Turn-Wache und hinter das Modell, damit der Verlauf in beiden Fällen umgeschrieben ist, bevor die Turn-Wache
    seinen Stand vergleicht."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.umgeschrieben = 0  # für Tests und das Log

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            try:
                n = abbrueche_umschreiben(frame.context.messages)
            except Exception as e:  # noqa: BLE001 (der Hinweis ist eine Hilfe, er darf nie die Anfrage verhindern)
                logger.warning("Abbruch-Hinweis nicht gesetzt ({})", type(e).__name__)
                n = 0
            if n:
                self.umgeschrieben += n
                logger.info(f"{self}: {n} abgebrochene(s) Werkzeug(e) im Verlauf erklärt")
        await self.push_frame(frame, direction)


# --- 2. Doppelte Aufrufe im selben Zug ---------------------------------------------------------------------------------

# Werkzeuge, bei denen dieselbe Aktion im selben Zug nie ein zweites Mal sinnvoll ist (Öffnen)
OEFFNEN_WERKZEUGE = frozenset({"webseite_oeffnen", "programm_oeffnen", "oeffnen_mit"})
SEITEN_MAX_JE_ZUG = 3  # so viele Seiten derselben Webseite öffnet WAMO in einem Zug
HINWEIS_WEITERE_SEITE = (
    "Hinweis: In diesem Zug hast du auf derselben Webseite schon {n} Seite(n) geöffnet. Alle Tabs sind offen. Sag der "
    "Person in einem Satz, was du geöffnet hast, und öffne nichts weiter, wenn sie es nicht ausdrücklich verlangt."
)
ERGEBNIS_SCHON_ERLEDIGT = {
    "status": "duplicate",
    "summary": "Genau das hast du in diesem Zug schon getan und es wird nicht wiederholt. Sag der Person kurz, was offen ist. "
               "Soll es noch einmal passieren, muss sie es erneut sagen.",
}
ERGEBNIS_ZU_VIELE = {
    "status": "blocked",
    "summary": "In diesem Zug sind auf derselben Webseite schon mehrere Seiten geöffnet worden, eine weitere öffnet WAMO nicht. "
               "Sag der Person, was offen ist, und frag, ob sie noch etwas Bestimmtes meint.",
}


def _schluessel(argumente: Mapping) -> str:
    """Vergleichbarer Schlüssel der Argumente: Groß- und Kleinschreibung und Leerraum zählen nicht, Adressen werden
    vereinheitlicht (ohne www., ohne Fragment, ohne Schrägstrich am Ende)."""
    teile = {}
    for k, v in sorted(argumente.items()):
        if isinstance(v, str):
            v = v.strip()
            if "://" in v:
                v = _url_schluessel(v)
            else:
                v = " ".join(v.lower().split())
        teile[str(k)] = v
    return json.dumps(teile, sort_keys=True, ensure_ascii=False, default=str)


def _url_schluessel(url: str) -> str:
    try:
        t = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (t.hostname or "").lower().removeprefix("www.")
    pfad = t.path.rstrip("/")
    return f"{t.scheme.lower()}://{host}{pfad}{'?' + t.query if t.query else ''}"


def webseite_von(argumente: Mapping) -> str | None:
    """Rechnername der Adresse in den Argumenten (ohne www.), sonst None."""
    url = argumente.get("url")
    if not isinstance(url, str):
        return None
    try:
        host = (urlsplit(url.strip()).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return None
    return host or None


@dataclass
class Urteil:
    """aktion: "ausfuehren" oder "ueberspringen"; ergebnis: Antwort an das Modell beim Überspringen; hinweis: Text, der an
    das Ergebnis der Ausführung gehängt wird."""

    aktion: str = "ausfuehren"
    ergebnis: dict | None = None
    hinweis: str | None = None
    grund: str = ""
    eintrag: tuple | None = None  # der gemerkte Aufruf; DoppelWache.erledigt() streicht ihn, wenn die Aktion scheiterte


@dataclass
class DoppelWache:
    """Merkt sich die Öffnen-Aufrufe des laufenden Zugs der Person (turn_nr wechselt mit jedem neuen Beitrag der Person)."""

    turn_nr: Callable[[], int]
    uhr: Callable[[], float] = time.monotonic
    uebersprungen: int = 0  # für Tests und das Log
    hinweise: int = 0
    _zug: int = field(default=-1, init=False)
    _aufrufe: list[tuple[str, str, str | None, float]] = field(default_factory=list, init=False)

    def pruefen(self, name: str, argumente: Mapping | None) -> Urteil:
        """Darf dieser Aufruf laufen? Merkt ihn sich, wenn ja. Fremde Werkzeuge werden nie beeinflusst."""
        if name not in OEFFNEN_WERKZEUGE:
            return Urteil()
        argumente = argumente or {}
        nr = self.turn_nr()
        if nr != self._zug:
            self._zug, self._aufrufe = nr, []
        jetzt = self.uhr()
        schluessel = _schluessel(argumente)
        if any(n == name and s == schluessel for n, s, _w, _t in self._aufrufe):
            self.uebersprungen += 1
            logger.info("Werkzeug-Wache: {} im selben Zug mit denselben Argumenten nicht wiederholt", name)
            return Urteil("ueberspringen", dict(ERGEBNIS_SCHON_ERLEDIGT), grund="gleich")
        urteil = Urteil()
        seite = webseite_von(argumente) if name == "webseite_oeffnen" else None
        if seite is not None:
            frueher = [t for n, _s, w, t in self._aufrufe if n == name and w == seite]
            if len(frueher) >= SEITEN_MAX_JE_ZUG:
                self.uebersprungen += 1
                logger.info("Werkzeug-Wache: {} zum {}. Mal auf derselben Webseite im Zug, nicht geöffnet", name, len(frueher) + 1)
                return Urteil("ueberspringen", dict(ERGEBNIS_ZU_VIELE), grund="zu_viele")
            if frueher:
                self.hinweise += 1
                urteil = Urteil(hinweis=HINWEIS_WEITERE_SEITE.format(n=len(frueher)), grund="weitere_seite")
                logger.info("Werkzeug-Wache: weitere Seite derselben Webseite im selben Zug, {:.1f} s nach der vorigen",
                            jetzt - frueher[-1])
        eintrag = (name, schluessel, seite, jetzt)
        self._aufrufe.append(eintrag)
        urteil.eintrag = eintrag
        return urteil

    def erledigt(self, urteil: Urteil, ergebnis: object) -> None:
        """Meldet das Ergebnis eines ausgeführten Aufrufs. Ist die Aktion gescheitert, abgelehnt oder wartet auf eine Freigabe,
        zählt sie nicht als getan: Der Aufruf wird gestrichen und darf wiederholt werden."""
        if urteil.eintrag is not None and ist_misserfolg(ergebnis):
            try:
                self._aufrufe.remove(urteil.eintrag)
            except ValueError:
                pass  # schon gestrichen oder der Zug hat gewechselt


MISSERFOLG = frozenset({"error", "rejected", "blocked", "needs_approval", "locked", "wartet", "nicht_gefunden"})


def ist_misserfolg(ergebnis: object) -> bool:
    """Hat die Aktion nicht stattgefunden (Fehler, abgelehnt, gesperrt, wartet auf Freigabe)? Unlesbares gilt als Erfolg,
    damit eine echte Wiederholung nicht fälschlich erlaubt wird."""
    daten = ergebnis
    if isinstance(ergebnis, str):
        try:
            daten = json.loads(ergebnis)
        except ValueError:
            return False
    if not isinstance(daten, dict):
        return False
    return str(daten.get("status", "")).lower() in MISSERFOLG or daten.get("ok") is False


def hinweis_anhaengen(ergebnis: object, hinweis: str) -> object:
    """Hängt den Hinweis an ein Werkzeugergebnis (dict, JSON-Text oder Text)."""
    if isinstance(ergebnis, dict):
        return {**ergebnis, "hinweis": hinweis}
    if isinstance(ergebnis, str):
        try:
            daten = json.loads(ergebnis)
        except ValueError:
            return f"{ergebnis} {hinweis}".strip()
        if isinstance(daten, dict):
            return json.dumps({**daten, "hinweis": hinweis}, ensure_ascii=False)
        return f"{ergebnis} {hinweis}".strip()
    return ergebnis


def umhuellen(params, wache: DoppelWache, urteil: Urteil):
    """Kopie der Aufrufparameter, deren Ergebnis die Wache erfährt (gescheiterte Aufrufe zählen nicht) und gegebenenfalls
    den Hinweis trägt (result_callback wird umhüllt)."""
    original = params.result_callback

    async def result_callback(ergebnis, *args, **kwargs):
        wache.erledigt(urteil, ergebnis)
        if urteil.hinweis:
            ergebnis = hinweis_anhaengen(ergebnis, urteil.hinweis)
        await original(ergebnis, *args, **kwargs)

    return dataclasses.replace(params, result_callback=result_callback)
