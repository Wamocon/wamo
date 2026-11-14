"""Turn-Wache: verhindert, dass das Sprachmodell dieselbe Lage zweimal beantwortet (doppelte Antwort).

Befund (20.10.2026, nachgestellt am 21.10.): Spricht die Person weiter, während WAMO schon antwortet und ein Werkzeug
läuft ("Welches Betriebssystem läuft auf meinem PC?" ... "Schau bitte nach."), kommen zwei Anfragen an das Modell mit
demselben Verlauf:

  * Pipecats Assistent-Aggregator schiebt nach dem Werkzeugergebnis den Verlauf erneut an. Spricht WAMO in dem
    Moment, merkt er sich das und schiebt beim nächsten "Bot stopped speaking" nach, auch wenn inzwischen der neue
    Zug der Person schon eine Anfrage ausgelöst hat (die Marke wird erst beim nächsten Anschieben gelöscht).
  * Der neue Zug der Person löst seine eigene Anfrage aus (vom Gate bis zum Ende der Antwort zurückgehalten).

Beide sehen denselben Verlauf, denn die Antwort der ersten steht erst nach dem Sprechen im Kontext. Ergebnis: zwei
Antworten, zweimal dasselbe Werkzeug (system_info).

Lösung: Vor und hinter dem Modell sitzt je ein Teil der Wache (Anfragen kommen von vorn, vom Nutzer-Aggregator, und
von hinten, vom Assistent-Aggregator). Verworfen wird
  * eine Anfrage mit genau demselben Stand wie die zuletzt weitergegebene (Anzahl der Nachrichten, letzte Nachricht
    als Objekt und Inhalt): Jede echte neue Lage ändert den Stand (neuer Beitrag der Person, Werkzeugergebnis,
    Hinweis nach Unterbrechung). Nach einer Unterbrechung darf derselbe Stand wieder angefragt werden, die alte
    Antwort wurde ja abgebrochen.
  * eine Anfrage, deren Verlauf mit WAMOs eigener Antwort endet: Steht die Antwort auf den neuen Zug schon im
    Verlauf, wenn das verspätete Anschieben kommt, gäbe es sonst eine Fortsetzung ohne neuen Anlass.

Weil alle Anfragen hier vorbeikommen, weiß die Wache auch, ob das Modell an einer Antwort arbeitet, die noch nicht zu hören ist
(modell_beschaeftigt, für die Stimmprüfung in stimme_waechter.py): ab einer durchgelassenen Anfrage, bis die Antwort zu hören ist (Beginn
oder Ende der Rede nach ihrem Text; ein Wartesatz davor zählt nicht), eine Unterbrechung kommt oder die Antwort ohne Text endet (das Modell
hält den Zug für unfertig), höchstens MODELL_MAX_SECS.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

MODELL_MAX_SECS = 30.0  # eine Antwort, die nie zu hören war, hält "Modell beschäftigt" nicht länger fest


def verlauf_stand(context) -> tuple[int, int, str] | None:
    """Stand eines Verlaufs: Anzahl, Objekt und Inhalt der letzten Nachricht (None bei leerem Verlauf)."""
    messages = context.messages
    if not messages:
        return None
    letzte = messages[-1]
    try:
        inhalt = json.dumps(letzte, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        inhalt = repr(letzte)
    return len(messages), id(letzte), hashlib.sha256(inhalt.encode("utf-8", "replace")).hexdigest()


class TurnWache:
    """Gemeinsamer Zustand; vorne() vor das Modell, hinten() direkt dahinter in die Pipeline."""

    def __init__(self, uhr: Callable[[], float] = time.monotonic):
        self._zuletzt: tuple[int, int, str] | None = None
        self.verworfen = 0
        self._uhr = uhr
        self._anfrage_um: float | None = None  # Zeit der letzten durchgelassenen Anfrage, deren Antwort noch nicht zu hören ist
        self._antwort_text = False  # die laufende Antwort hat schon Text für die Sprachausgabe
        self._vorne = _WachTeil(self, FrameDirection.DOWNSTREAM, name="TurnWacheVorne")
        self._hinten = _WachTeil(self, FrameDirection.UPSTREAM, name="TurnWacheHinten")

    def vorne(self) -> FrameProcessor:
        return self._vorne

    def hinten(self) -> FrameProcessor:
        return self._hinten

    def durchlassen(self, frame: LLMContextFrame) -> bool:
        if getattr(frame, "speculation", False):
            return True  # vorläufige Anfragen verwaltet Pipecat selbst
        messages = frame.context.messages
        if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "assistant":
            # Endet der Verlauf mit WAMOs eigener Antwort, gibt es nichts Neues zu beantworten: das ist das verspätete
            # Anschieben nach einem Werkzeugergebnis, nachdem der neue Zug schon beantwortet wurde
            self.verworfen += 1
            logger.warning("Turn-Wache: Anfrage ohne neuen Beitrag verworfen (Verlauf endet mit WAMOs Antwort)")
            return False
        stand = verlauf_stand(frame.context)
        if stand is not None and stand == self._zuletzt:
            self.verworfen += 1
            logger.warning("Turn-Wache: doppelte Anfrage an das Modell verworfen (gleicher Verlauf, {} Nachrichten)",
                           stand[0])
            return False
        self._zuletzt = stand
        self._anfrage_um, self._antwort_text = self._uhr(), False
        return True

    def unterbrochen(self) -> None:
        self._zuletzt = None
        self._anfrage_um = None

    def modell_beschaeftigt(self) -> bool:
        """Arbeitet das Modell an einer Antwort, die noch nicht zu hören ist (siehe oben)?"""
        return self._anfrage_um is not None and self._uhr() - self._anfrage_um < MODELL_MAX_SECS

    def _ausgabe(self, frame: Frame) -> None:
        """Was hinter dem Modell vorbeikommt: Text für die Sprachausgabe, Ende der Antwort, Beginn und Ende der Rede."""
        if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
            if self._antwort_text:
                self._anfrage_um = None  # die Antwort ist zu hören; ein Wartesatz vorher (wartesatz.py) beendet das Denken nicht
        elif isinstance(frame, LLMTextFrame):
            self._antwort_text = True
        elif isinstance(frame, LLMFullResponseEndFrame) and not self._antwort_text:
            self._anfrage_um = None  # nichts zu sagen (zum Beispiel ein unfertiger Zug): das Modell wartet, statt zu arbeiten


class _WachTeil(FrameProcessor):
    def __init__(self, wache: TurnWache, richtung: FrameDirection, **kwargs):
        super().__init__(**kwargs)
        self._wache = wache
        self._richtung = richtung

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            self._wache.unterbrochen()
        elif isinstance(frame, LLMContextFrame) and direction == self._richtung:
            if not self._wache.durchlassen(frame):
                return
        elif isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)) or (
                self._richtung == FrameDirection.UPSTREAM and direction == FrameDirection.DOWNSTREAM):
            self._wache._ausgabe(frame)  # hinter dem Modell: seine Ausgabe; der Beginn der Rede kommt von hinten an beiden Teilen vorbei
        await self.push_frame(frame, direction)
