"""Stimmprüfung, Regelwerk: wer in dieser Sitzung Werkzeuge auslösen darf. Ein StimmGate gehört genau einer Sitzung (also einem Profil).

Es merkt sich, wie die letzte Äußerung eingestuft wurde (stimme_pruefer.py liefert die Werte). Jedes Werkzeug außer OFFENE_WERKZEUGE fragt
vor dem Ausführen das Gate (werkzeuge_absichern, schemas_absichern): geprüft wird im Code, nicht im Prompt.
- eigen (genug Sprache, Wert hoch): erlaubt, bis vertrauen_secs danach.
- kurz ("Ja", "mach", zu wenig Sprache für "eigen"): klingt es nach der Person (Wert ab eigen_min), gilt das Ergebnis davor weiter, aber
  nur kurz_vertrauen_secs (15 s) nach der letzten sicheren Erkennung. Klingt es nicht danach, ist es unklar (entzieht das Vertrauen,
  die Äußerung geht trotzdem ans Modell), klingt es klar fremd, fremd.
- fremd oder unklar (genug Sprache, aber nicht sicher; ebenso jede fehlgeschlagene Prüfung): gesperrt, bis die Person wieder sicher erkannt
  wird oder tippt. Spricht WAMO gleichzeitig (Echo), heißt ein niedriger Wert nur unklar.
- Getippter Text aus der Oberfläche zählt wie die eigene Stimme: die Oberfläche ist mit zweitem Faktor angemeldet (tippen_beobachten).
  Prüfergebnisse zu früheren Äußerungen überschreiben ihn nicht.

Modi (WAMO_STIMME_PRUEFUNG): aus | beobachten (messen, melden, ins Log schreiben, nichts sperren) | streng (Standard). Alles wirkt nur für ein
Profil mit eingelerntem Abdruck: ohne Abdruck wird nicht geprüft und nichts gesperrt (status fehlt), die Einwilligung ist freiwillig und
ihr Fehlen ohne Nachteil. Streng sperrt Werkzeuge außerdem, solange für ein Profil mit Abdruck oder bei unbekanntem Stand (Datenbank nicht
lesbar) Modell oder Abdruck nicht geladen sind oder die Prüfung länger als WARTEN_WERKZEUG_SECS dauert; getippter Text geht immer.

Schwellen (alle als Schalter in der Umgebung, siehe schwellen_aus_env): WAMO_STIMME_EIGEN_MIN (ab hier die eigene Stimme, 0,5),
WAMO_STIMME_FREMD_MAX (bis hier fremd, 0,3), WAMO_STIMME_MIN_SECS (kürzere Äußerungen reichen nicht für "eigen", 1 s),
WAMO_STIMME_VERTRAUEN_SECS (so lange gilt "eigen" für Folgesätze, 120 s), WAMO_STIMME_KURZ_VERTRAUEN_SECS (so lange darf das Vertrauen nur aus
kurzen Äußerungen weiterlaufen, 15 s nach der letzten sicheren Erkennung).
"""

from __future__ import annotations

import asyncio
import functools
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema

from env_schalter import zahl_im_bereich

EIGEN, FREMD, UNKLAR, KURZ = "eigen", "fremd", "unklar", "kurz"
GETIPPT = "getippt"
MODI = ("aus", "beobachten", "streng")
STANDARD_MODUS = "streng"
# Werkzeuge, die jede Stimme auslösen darf: nichts ändern, nichts Privates zeigen
OFFENE_WERKZEUGE = frozenset({"aktuelle_zeit", "websuche", "canvas_zeigen"})
WARTEN_WERKZEUG_SECS = 2.0  # so lange wartet ein Werkzeug höchstens auf die laufende Prüfung
MARKE = "_wamo_stimme_gate"  # am eingepackten Handler: welches Gate ihn schon prüft
_GEWARNT: set[str] = set()


@dataclass(frozen=True)
class Schwellen:
    eigen_min: float = 0.5
    fremd_max: float = 0.3
    min_secs: float = 1.0
    vertrauen_secs: float = 120.0
    kurz_vertrauen_secs: float = 15.0


def stand_nachricht(modus: str, status: str) -> dict:
    """Stand der Stimmprüfung für die Oberfläche, an einer Stelle für alle Absender (Sitzung, Einlernen, Labor)."""
    return {"type": "stimme", "status": status, "modus": modus}


def pruefmodus(wert: str | None = None) -> str:
    """Modus aus WAMO_STIMME_PRUEFUNG (oder wert). Leer oder unbekannt heißt streng, ein unbekannter Wert wird einmal gewarnt."""
    roh = ((os.getenv("WAMO_STIMME_PRUEFUNG") if wert is None else wert) or "").strip().lower()
    if not roh:
        return STANDARD_MODUS
    if roh in MODI:
        return roh
    if roh not in _GEWARNT:
        _GEWARNT.add(roh)
        logger.warning("WAMO_STIMME_PRUEFUNG hat den unbekannten Wert {!r}: es gilt {} (erlaubt: aus, beobachten, streng)", roh, STANDARD_MODUS)
    return STANDARD_MODUS


def schwellen_aus_env() -> Schwellen:
    """Schwellen aus der Umgebung. Unbrauchbares (keine Zahl, außerhalb der Grenzen) heißt Standard, und fremd_max muss unter eigen_min
    liegen, sonst gelten beide Standardwerte."""
    standard = Schwellen()
    eigen = zahl_im_bereich("WAMO_STIMME_EIGEN_MIN", 0.35, 0.95)
    fremd = zahl_im_bereich("WAMO_STIMME_FREMD_MAX", 0.0, 0.8)
    s = Schwellen(
        eigen_min=standard.eigen_min if eigen is None else eigen,
        fremd_max=standard.fremd_max if fremd is None else fremd,
        min_secs=zahl_im_bereich("WAMO_STIMME_MIN_SECS", 0.3, 10.0) or standard.min_secs,
        vertrauen_secs=zahl_im_bereich("WAMO_STIMME_VERTRAUEN_SECS", 10.0, 3600.0) or standard.vertrauen_secs,
        kurz_vertrauen_secs=zahl_im_bereich("WAMO_STIMME_KURZ_VERTRAUEN_SECS", 1.0, 120.0) or standard.kurz_vertrauen_secs,
    )
    if s.fremd_max >= s.eigen_min:
        logger.warning("WAMO_STIMME_FREMD_MAX ({}) muss unter WAMO_STIMME_EIGEN_MIN ({}) liegen: es gelten die Standardwerte",
                       s.fremd_max, s.eigen_min)
        return Schwellen(min_secs=s.min_secs, vertrauen_secs=s.vertrauen_secs, kurz_vertrauen_secs=s.kurz_vertrauen_secs)
    return s


@dataclass
class StimmGate:
    modus: str = STANDARD_MODUS
    schwellen: Schwellen = field(default_factory=Schwellen)
    uhr: Callable[[], float] = time.monotonic
    abdruck: np.ndarray | None = None
    zustand: str = "laedt"  # laedt | da | fehlt | kaputt (Stand des Abdrucks in der Datenbank)
    modell_ok: bool | None = None  # nur für ein Profil mit Abdruck von Belang
    _wer: str | None = None
    _um: float = 0.0  # Zeitpunkt der letzten sicheren Erkennung (oder des letzten Tippens)
    _nur_kurz: bool = False  # seither kamen nur kurze Äußerungen, die nach der Person klangen
    _geloescht: bool = False  # die Person hat ihren Abdruck in dieser Sitzung gelöscht (setze_abdruck mit geloescht)
    _getippt_um: float = -math.inf
    _ausstehend: asyncio.Task | None = field(default=None, repr=False)

    @classmethod
    def aus_umgebung(cls) -> StimmGate:
        return cls(modus=pruefmodus(), schwellen=schwellen_aus_env())

    @property
    def hat_abdruck(self) -> bool:
        return self.abdruck is not None

    @property
    def status(self) -> str:
        """aus | fehlt (nicht eingelernt) | laedt | kaputt | bereit"""
        if self.modus == "aus":
            return "aus"
        if self.zustand == "fehlt":
            return "fehlt"
        if self.zustand == "kaputt" or self.modell_ok is False:
            return "kaputt"
        if self.zustand == "laedt" or self.modell_ok is None:
            return "laedt"
        return "bereit"

    def stand(self, status: str | None = None) -> dict:
        """Nachricht an die Oberfläche mit dem Stand dieses Gates (oder einem vorgegebenen, zum Beispiel laedt beim Modellstart)."""
        return stand_nachricht(self.modus, status or self.status)

    def setze_abdruck(self, abdruck: np.ndarray | None, ok: bool = True, geloescht: bool = False) -> None:
        """Stand des Abdrucks nach dem Laden, Einlernen oder Löschen. ok False: nicht lesbar, es ist unbekannt, ob es einen gibt.
        geloescht: die Person hat ihn gerade gelöscht, das neue Einlernen in dieser Sitzung braucht dann denselben Nachweis wie ein Ersetzen."""
        self.abdruck = abdruck if ok else None
        if geloescht and self.abdruck is None:
            self._geloescht = True
        self.zustand = "kaputt" if not ok else "da" if abdruck is not None else "fehlt"
        if abdruck is None:
            self._wer = None  # ein gelöschter Abdruck lässt kein Vertrauen zurück
            self._nur_kurz = False

    @property
    def abdruck_geschuetzt(self) -> bool:
        """Es gibt einen Abdruck oder er wurde in dieser Sitzung gelöscht: Ersetzen, Löschen und neu Einlernen brauchen einen Nachweis (die erkannte
        Stimme oder eine frische Anmeldung, siehe stimme_sitzung.py), sonst könnte jemand an der angemeldeten Oberfläche den Schutz aushebeln."""
        return self.abdruck is not None or self._geloescht

    def einstufen(self, wert: float, secs: float, ueberlappung: bool) -> str:
        """eigen: sicher die Person. fremd: sicher nicht. unklar: nicht sicher (entzieht das Vertrauen), auch eine kurze Äußerung, die nicht
        nach der Person klingt. kurz: zu wenig Sprache zum Urteilen ("Ja"), klingt aber nach der Person (das Vertrauen läuft nur begrenzt
        weiter, siehe grund_der_stimme). ueberlappung: WAMO sprach gleichzeitig, es kann Echo sein."""
        s = self.schwellen
        if wert >= s.eigen_min:
            return EIGEN if secs >= s.min_secs else KURZ
        if wert <= s.fremd_max:
            return UNKLAR if ueberlappung else FREMD
        return UNKLAR

    def eintragen(self, urteil: str, gehoert_um: float | None = None) -> None:
        """gehoert_um: Ende der Äußerung (Uhr des Gates). Was vor getipptem Text gesagt wurde, zählt nicht mehr.
        kurz ändert das Urteil nicht, merkt sich aber, dass das Vertrauen seither nur aus kurzen Äußerungen kommt."""
        if gehoert_um is not None and gehoert_um < self._getippt_um:
            return
        if urteil == KURZ:
            self._nur_kurz = True
            return
        self._wer, self._um, self._nur_kurz = urteil, self.uhr(), False

    def getippt(self) -> None:
        self._wer = GETIPPT
        self._um = self._getippt_um = self.uhr()
        self._nur_kurz = False

    def grund_der_stimme(self) -> str | None:
        """None, wenn zuletzt die Person sprach oder tippte, sonst warum nicht."""
        if self._wer == FREMD:
            return "fremde Stimme"
        if self._wer == UNKLAR:
            return "Stimme nicht sicher erkannt"
        if self._wer is None:
            return "Stimme noch nicht erkannt"
        seit = self.uhr() - self._um
        if seit > self.schwellen.vertrauen_secs:
            return "Stimme zu lange nicht mehr erkannt"
        if self._nur_kurz and seit > self.schwellen.kurz_vertrauen_secs:
            return "Stimme nur kurz gehört, nicht sicher erkannt"
        return None

    def nicht_sicher_eigen(self) -> bool:
        """Stammt der Zug nicht sicher von der Person? Nur im Modus streng bei bereiter Prüfung (Profil mit Abdruck, Modell geladen):
        die Stimme gilt nicht als sicher erkannt (grund_der_stimme) oder die Prüfung läuft noch. Dann färbt bot.py den Zug wie
        Fremdinhalte (kein Lernen, kein Hinweis des Ja-Wächters, Markierung im Protokoll). Ohne Abdruck oder im Modus beobachten
        ändert sich nichts."""
        if self.modus != "streng" or self.status != "bereit":
            return False
        return (self._ausstehend is not None and not self._ausstehend.done()) or self.grund_der_stimme() is not None

    def stimme_vertraut(self) -> bool:
        """Zuletzt sicher an der Stimme erkannt (nicht nur getippt)."""
        return self._wer == EIGEN and self.grund_der_stimme() is None

    def verfolgen(self, task: asyncio.Task) -> None:
        self._ausstehend = task

    def _sperrgrund(self) -> str | None:
        if self._ausstehend and not self._ausstehend.done():
            return "Stimmprüfung dauert zu lange"
        status = self.status
        if status == "fehlt":
            return None  # ohne Abdruck wird nichts geprüft und nichts gesperrt
        if status == "bereit":
            return self.grund_der_stimme()
        if self._wer == GETIPPT and self.grund_der_stimme() is None:
            return None  # getippt geht auch, wenn die Prüfung nicht bereit ist
        return "Stimmprüfung nicht bereit"

    async def pruefen(self, werkzeug: str) -> dict | None:
        """Vor jedem Werkzeug: None = ausführen, sonst das Ergebnis für das Modell."""
        if self.modus == "aus" or werkzeug in OFFENE_WERKZEUGE:
            return None
        if self._ausstehend and not self._ausstehend.done():
            await asyncio.wait({self._ausstehend}, timeout=WARTEN_WERKZEUG_SECS)
        grund = self._sperrgrund()
        if grund is None:
            return None
        if self.modus == "beobachten":
            logger.info("Stimmprüfung (beobachten): {} wäre gesperrt ({})", werkzeug, grund)
            return None
        logger.warning("Stimmprüfung: {} gesperrt ({})", werkzeug, grund)
        return {
            "status": "blocked",
            "grund": f"Stimmprüfung: {grund}",
            "hinweis": "Nur die eingelernte Stimme der Person darf das auslösen. Nicht erneut versuchen.",
        }


def abgesichert(name: str | None, handler, gate: StimmGate):
    """Handler, der vorher das Gate fragt. Offene Werkzeuge und vom selben Gate schon geprüfte bleiben, wie sie sind.
    name None (ein Handler für alle Werkzeuge): geprüft wird der Name des Aufrufs."""
    if name in OFFENE_WERKZEUGE or getattr(handler, MARKE, None) is gate:
        return handler

    @functools.wraps(handler)  # übernimmt auch @tool_options (Zeitlimit usw.)
    async def geprueft(params):
        gesperrt = await gate.pruefen(name or params.function_name)
        if gesperrt is not None:
            await params.result_callback(gesperrt)
            return
        await handler(params)

    setattr(geprueft, MARKE, gate)
    return geprueft


def werkzeuge_absichern(llm, gate: StimmGate) -> None:
    """Jedes Werkzeug, das ab jetzt am LLM registriert wird, läuft durch das Gate.
    Direkt nach dem Anlegen des LLM aufrufen, vor allen register_function."""
    registrieren = llm.register_function

    def register_function(function_name, handler, *args, **kwargs):
        return registrieren(function_name, abgesichert(function_name, handler, gate), *args, **kwargs)

    llm.register_function = register_function


def schemas_absichern(schemas: list[FunctionSchema], gate: StimmGate) -> list[FunctionSchema]:
    """Werkzeuge mit eigenem Handler im Schema (Host, Bürogeräte) einmal einpacken. Pipecat registriert sie bei jedem Kontext neu;
    derselbe eingepackte Handler bleibt dabei derselbe."""
    return [
        FunctionSchema(s.name, s.description, s.properties, s.required, handler=abgesichert(s.name, s.handler, gate))
        if s.handler is not None else s
        for s in schemas
    ]


def tippen_beobachten(rtvi, gate: StimmGate) -> bool:
    """Getippter Text aus der Oberfläche (RTVI send-text) zählt wie die eigene Stimme. False, wenn Pipecat den Haken nicht mehr hat."""
    bisher = getattr(rtvi, "_handle_send_text", None)
    if bisher is None:
        logger.warning("Stimmprüfung: getippter Text nicht erkennbar (Pipecat geändert?)")
        return False

    async def send_text_behandeln(daten):
        gate.getippt()
        await bisher(daten)

    rtvi._handle_send_text = send_text_behandeln
    return True
