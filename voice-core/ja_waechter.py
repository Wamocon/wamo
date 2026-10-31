"""Ja-Wächter: ein kurzes "Ja" auf eine echte Rückfrage führt den Auftrag aus, statt eine neue Rückfrage auszulösen.

Befund: Antwortet die Person auf "Soll ich ...?" mit "Ja", fragt das Modell manchmal erneut ("Soll ich wirklich ...?"), im
schlimmsten Fall mehrfach hintereinander. Die Regel in der Persona (persona.py, Rückfragen) reicht nicht immer.

Der Wächter sitzt vor dem Modell (nach dem Gate, vor der Turn-Wache). Er prüft den Verlauf, der an das Modell geht:
  * die letzte Nachricht ist ein kurzes Ja der Person (höchstens vier Wörter, nur Zustimmung, kein "aber", "nein", "lieber"),
  * die Nachricht davor ist eine Rückfrage des Assistenten ("?" am Ende oder "soll ich" und Verwandte),
  * es ist keine Freigabe im Spiel.
Dann hängt er einen festen Hinweis an die Nachricht der Person: Auftrag jetzt ausführen, nicht erneut fragen.

Grenzen (bewusst, belegt in test_ja_waechter.py):
  * Der Wächter ersetzt nie eine Freigabe. Freigabe-Karten, Karten im Kern (PC-Freigaben, Entwicklungsaufträge) und
    Sicherheitsabfragen entscheidet allein die Oberfläche, geprüft im Host und im Kern. Ein "Ja" im Gespräch ist keine
    Freigabe. Deshalb greift der Wächter nicht, wenn eine Freigabe offen ist (offen), im Verlauf eine Freigabe-Meldung steht
    (needs_approval, wartet, PC-Freigabe) oder die Rückfrage von Freigabe, Karte, Oberfläche, Code, Passwort, Schlüssel oder
    Sicherheit handelt.
  * Der Hinweis ändert nichts an Werkzeugen und Rechten: Der Host prüft jede Aktion selbst, ein Hinweis im Text des Modells
    überspringt keine Freigabe.
  * Nachrichten, die mit "[" beginnen (Hinweise von WAMO selbst, Freigabe-Meldungen, Unterbrechungs-Hinweise), sind kein "Ja".
  * Der Hinweis steht nur im Verlauf, nie im Transkript, im Lernen oder im Protokoll der Gespräche.

Schalter: WAMO_JA_WAECHTER (Standard aus, bis der Messweg die Kennzahlen bestätigt hat).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from loguru import logger

from env_schalter import schalter_an

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

HINWEIS = (
    "[Hinweis für WAMO: Die Person hat deine Rückfrage mit Ja beantwortet. Führ den Auftrag jetzt aus und frag nicht "
    "noch einmal. Das gilt nicht für Freigaben: Was in der Oberfläche freigegeben werden muss, bleibt dort.]"
)
MAX_WOERTER = 4
# Zustimmung (deutsch und englisch). KERN: mindestens eines davon muss vorkommen; FUELL: darf zusätzlich vorkommen.
JA_KERN = frozenset(
    {"ja", "jo", "jaja", "klar", "genau", "gern", "gerne", "mach", "machs", "okay", "ok", "sicher", "natürlich", "yes", "yep",
     "yeah", "sure", "tu", "los", "einverstanden", "passt"}
)
JA_FUELL = frozenset({"bitte", "das", "es", "ruhig", "mal", "gerne", "gern", "na", "dann", "schon", "do", "it", "go", "ahead",
                      "please", "bitteschön", "gut", "so", "bitte"})
# Wörter, die aus dem Ja ein Aber machen oder etwas anderes verlangen
GEGEN = frozenset({"aber", "nein", "nicht", "kein", "keine", "lieber", "erst", "vorher", "warte", "warten", "stopp", "stop",
                   "moment", "halt", "doch", "no", "but", "wait"})
FRAGE_ANFAENGE = ("soll ich", "soll ich's", "möchtest du", "möchten sie", "willst du", "wollen sie", "darf ich", "wollen wir",
                  "soll das", "do you want", "shall i", "should i")
# Rückfragen, die um eine Freigabe oder Sicherheit gehen: dort gibt es keinen Hinweis
FREIGABE_RE = re.compile(r"freigab|freigeb|karte|oberfläche|bestätig|code|passwort|schlüssel|token|sicherheit|zugangsdaten",
                         re.IGNORECASE)
FREIGABE_MELDUNG_RE = re.compile(r"needs_approval|status=wartet|\"wartet\"|PC-Freigabe|freigegeben werden", re.IGNORECASE)
MARKER = "●◐○ "
RUECKSCHAU = 6  # so viele Nachrichten vor dem Ja werden auf Freigabe-Meldungen geprüft


def ja_waechter_an(wert: str | None = None) -> bool:
    """WAMO_JA_WAECHTER: festen Hinweis an ein Ja auf eine echte Rückfrage hängen (Standard aus, bis gemessen)."""
    return schalter_an("WAMO_JA_WAECHTER", wert, standard=False)


def _woerter(text: str) -> list[str]:
    return [w for w in re.split(r"[\s,.;:!?…\-–—\"'()]+", text.lower()) if w]


def ist_kurzes_ja(text: str) -> bool:
    """Besteht die Äußerung nur aus Zustimmung ("Ja", "Ja bitte", "Mach das", "Okay, gerne")? Nie bei "[" am Anfang, nie bei
    "Ja, aber ...", "Ja, nein", "Doch", "Ja, warte kurz"."""
    text = text.strip()
    if not text or text.startswith("["):
        return False
    w = _woerter(text)
    if not w or len(w) > MAX_WOERTER:
        return False
    if any(x in GEGEN for x in w):
        return False
    return any(x in JA_KERN for x in w) and all(x in JA_KERN or x in JA_FUELL for x in w)


def ist_rueckfrage(text: str) -> bool:
    """Ist das die Rückfrage des Assistenten an die Person? Der letzte Satz endet mit "?" oder beginnt mit "Soll ich" und
    Verwandten. Eine Freigabe-Rückfrage zählt nicht (FREIGABE_RE)."""
    t = text.strip().lstrip(MARKER).strip()
    if not t or FREIGABE_RE.search(t):
        return False
    saetze = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", t) if s.strip()]
    if not saetze:
        return False
    letzter = saetze[-1].lower()
    return letzter.endswith("?") or any(letzter.startswith(a) or f" {a} " in f" {letzter} " for a in FRAGE_ANFAENGE)


def _inhalt(nachricht: object) -> str | None:
    if isinstance(nachricht, dict) and isinstance(nachricht.get("content"), str):
        return nachricht["content"]
    return None


def freigabe_im_verlauf(messages: list) -> bool:
    """Steht in den letzten Nachrichten eine Freigabe-Meldung (needs_approval, wartet, PC-Freigabe)?"""
    for m in messages[-RUECKSCHAU:]:
        text = _inhalt(m)
        if text is None and isinstance(m, dict):
            text = str(m.get("content", ""))
        if text and FREIGABE_MELDUNG_RE.search(text):
            return True
    return False


def hinweis_noetig(messages: list, offen: bool = False) -> bool:
    """Gehört an die letzte Nachricht der Hinweis? Nur nach einem kurzen Ja auf eine echte Rückfrage, ohne Freigabe im Spiel."""
    if offen or len(messages) < 2:
        return False
    ja, davor = messages[-1], messages[-2]
    if not (isinstance(ja, dict) and ja.get("role") == "user" and isinstance(davor, dict) and davor.get("role") == "assistant"):
        return False
    ja_text, frage = _inhalt(ja), _inhalt(davor)
    if ja_text is None or frage is None or HINWEIS in ja_text:
        return False
    if not ist_kurzes_ja(ja_text) or not ist_rueckfrage(frage):
        return False
    return not freigabe_im_verlauf(messages[:-1])


class JaWaechter(FrameProcessor):
    """Hängt nach einem Ja auf eine echte Rückfrage den Hinweis an die Nachricht der Person (siehe Moduldoc)."""

    def __init__(self, *, offen: Callable[[], bool] | None = None, **kwargs):
        """offen: sagt, ob eine Freigabe (PC-Freigabe, Entwicklungsauftrag) offen ist; dann greift der Wächter nie."""
        super().__init__(**kwargs)
        self._offen = offen
        self.hinweise = 0  # für Tests und das Log

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM and not getattr(frame, "speculation", False):
            messages = frame.context.messages
            offen = bool(self._offen()) if self._offen is not None else False
            if hinweis_noetig(messages, offen):
                messages[-1]["content"] = f"{messages[-1]['content']} {HINWEIS}"
                self.hinweise += 1
                logger.info(f"{self}: Ja auf Rückfrage, Hinweis angehängt (insgesamt {self.hinweise})")
        await self.push_frame(frame, direction)
