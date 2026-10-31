"""Wartesatz: ein kurzer gesprochener Satz, wenn ein Werkzeug lange dauert und WAMO sonst schweigen würde.

Befund (22.10.2026, Szenariotest scripts/wamo-stimme-messen.cjs): Zwischen der Frage und dem ersten Ton liegen bei einer
Websuche mehrere Sekunden Stille, wenn das Modell vor dem Werkzeugaufruf nichts sagt. Ein Mensch sagt dann "Moment, ich
schaue nach". Das Modell tut es nach der Persona oft, aber nicht immer; dieser Wächter fängt den Rest auf.

Der Wächter sitzt direkt hinter dem Modell. Läuft ein Werkzeugaufruf länger als nach_secs und hat in diesem Zug weder das
Modell Text gesprochen noch WAMO schon gesprochen, geht ein TTSSpeakFrame (append_to_context=False, der Satz kommt nicht
in den Verlauf) an die Sprachausgabe. Schnelle Werkzeuge (Uhrzeit, Merken) sind fertig, bevor die Zeit abläuft, und
lösen nichts aus. Kein Dauergeschwätz: höchstens ein Wartesatz je Zug, nie zwei gleiche hintereinander, und eine
Unterbrechung der Person verwirft ihn.

Schalter: WAMO_WARTESATZ (an/aus, Standard aus), WAMO_WARTESATZ_SECS (Standard 0,9).

Langsames Modell (WAMO_WARTESATZ_MODELL, Standard aus, WAMO_WARTESATZ_MODELL_SECS Standard 2,5; docs/gespraech-optimierung.md):
Totzeit über etwa 1,5 s wirkt wie ein Aussetzer. Beginnt eine Modellantwort (LLMFullResponseStartFrame) und kommt danach
2,5 s lang kein einziges Zeichen, kein Marker, kein Werkzeugaufruf und kein Antwortende, sagt WAMO einmal "Einen Moment.".
Ein unvollständiger Satz der Person (Marker ◐ oder ○) liefert einen LLMMarkerFrame und löst nichts aus: dort wartet WAMO still,
weil die Person noch denkt. Sprachaktivität der Person (Unterbrechung) verwirft den Satz.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Callable

from loguru import logger

from env_schalter import schalter_an

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMarkerFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

NACH_SECS_STANDARD = 0.9
MODELL_SECS_STANDARD = 2.5

SAETZE_SUCHE = ("Moment, ich suche das kurz.", "Ich schaue kurz nach.", "Einen Augenblick, ich suche.")
SAETZE_BILDSCHIRM = ("Moment, ich schaue mal.", "Ich sehe kurz nach.")
SAETZE_STANDARD = ("Einen Moment.", "Moment, bin gleich so weit.", "Ich bin gleich so weit.")
SAETZE_MODELL = ("Einen Moment.", "Moment, ich überlege kurz.", "Ich muss kurz nachdenken.")
WERKZEUG_SAETZE = {
    "websuche": SAETZE_SUCHE,
    "bildschirm_ansehen": SAETZE_BILDSCHIRM,
    "auf_bildschirm_klicken": SAETZE_BILDSCHIRM,
}


def wartesatz_an(wert: str | None = None) -> bool:
    """WAMO_WARTESATZ, Standard aus: In der Messung (22.10.2026) sagte das Modell den Füllsatz vor der Websuche selbst
    (4 von 5 Aufrufen), der Wächter löste nie aus. Wer ein Modell nutzt, das vor Werkzeugen schweigt, schaltet ihn an."""
    return schalter_an("WAMO_WARTESATZ", wert, standard=False)


def wartesatz_modell_an(wert: str | None = None) -> bool:
    """WAMO_WARTESATZ_MODELL, Standard aus bis zur Messung: ein Satz, wenn das Modell zu lange kein Zeichen liefert."""
    return schalter_an("WAMO_WARTESATZ_MODELL", wert, standard=False)


def wartesatz_modell_secs(wert: str | None = None) -> float:
    """WAMO_WARTESATZ_MODELL_SECS: so lange darf das Modell nach der Anfrage schweigen (Standard 2,5, mindestens 0,5)."""
    w = os.getenv("WAMO_WARTESATZ_MODELL_SECS") if wert is None else wert
    try:
        return max(0.5, float(w.replace(",", "."))) if w and w.strip() else MODELL_SECS_STANDARD
    except ValueError:
        return MODELL_SECS_STANDARD


def wartesatz_secs(wert: str | None = None) -> float:
    w = os.getenv("WAMO_WARTESATZ_SECS") if wert is None else wert
    try:
        return max(0.1, float(w.replace(",", "."))) if w and w.strip() else NACH_SECS_STANDARD
    except ValueError:
        return NACH_SECS_STANDARD


def satz_fuer(werkzeug: str, zuletzt: str | None, wuerfel: Callable[[tuple[str, ...]], str] = random.choice) -> str:
    """Wartesatz zum Werkzeug (Name "modell": langsames Modell), nie derselbe wie zuletzt."""
    saetze = SAETZE_MODELL if werkzeug == "modell" else WERKZEUG_SAETZE.get(werkzeug, SAETZE_STANDARD)
    wahl = [s for s in saetze if s != zuletzt] or list(saetze)
    return wuerfel(tuple(wahl))


class Wartesatz(FrameProcessor):
    def __init__(self, *, nach_secs: float = NACH_SECS_STANDARD, werkzeug: bool = True,
                 modell_secs: float | None = None, **kwargs):
        """werkzeug: Wartesatz bei langen Werkzeugaufrufen (WAMO_WARTESATZ). modell_secs: Wartesatz, wenn das Modell so
        lange schweigt (WAMO_WARTESATZ_MODELL), None = aus."""
        super().__init__(**kwargs)
        self._nach_secs = nach_secs
        self._werkzeug = werkzeug
        self._modell_secs = modell_secs
        self._modell_task: asyncio.Task | None = None
        self._laufend: dict[str, asyncio.Task] = {}  # tool_call_id -> Zeitgeber
        self._zug_text = False  # das Modell hat in diesem Zug Text geliefert (es spricht oder hat gesprochen)
        self._spricht = False
        self._gesagt_im_zug = False
        self._zuletzt: str | None = None
        self.gesprochen = 0  # für Tests und das Log

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame) and direction == FrameDirection.DOWNSTREAM:
            self._zug_text = False
            self._modell_starten()
        elif isinstance(frame, (LLMMarkerFrame, LLMFullResponseEndFrame, FunctionCallsStartedFrame))                 and direction == FrameDirection.DOWNSTREAM:
            self._modell_abbrechen()  # das Modell hat geantwortet (auch nur mit Marker) oder ist fertig
        elif isinstance(frame, LLMTextFrame) and direction == FrameDirection.DOWNSTREAM:
            self._modell_abbrechen()
            if frame.text.strip(" ●◐○"):
                self._zug_text = True
                self._gesagt_im_zug = False  # ein neuer Satz des Modells: ein späterer Wartesatz ist wieder erlaubt
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._spricht = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._spricht = False
        elif isinstance(frame, InterruptionFrame):
            self._alle_verwerfen()
            self._gesagt_im_zug = False
        elif isinstance(frame, FunctionCallInProgressFrame) and direction == FrameDirection.DOWNSTREAM:
            self._modell_abbrechen()
            if self._werkzeug:
                self._starten(frame)
        elif isinstance(frame, FunctionCallResultFrame) and direction == FrameDirection.DOWNSTREAM:
            task = self._laufend.pop(frame.tool_call_id, None)
            if task and not task.done():
                task.cancel()
        await self.push_frame(frame, direction)

    def _modell_starten(self):
        self._modell_abbrechen()
        if self._modell_secs is None:
            return
        self._modell_task = self.create_task(self._modell_nach_zeit(), name="wartesatz_modell")

    def _modell_abbrechen(self):
        task, self._modell_task = self._modell_task, None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _modell_nach_zeit(self):
        await asyncio.sleep(self._modell_secs)
        if self._modell_task is None or self._spricht or self._gesagt_im_zug:
            return  # es klingt schon etwas, oder ein Wartesatz war in diesem Zug schon da
        self._gesagt_im_zug = True
        satz = satz_fuer("modell", self._zuletzt)
        self._zuletzt = satz
        self.gesprochen += 1
        logger.info(f"{self}: Wartesatz, Modell antwortet seit {self._modell_secs} s nicht ({self.gesprochen})")
        await self.push_frame(TTSSpeakFrame(satz, append_to_context=False))

    def _starten(self, frame: FunctionCallInProgressFrame):
        if frame.tool_call_id in self._laufend:
            return
        self._laufend[frame.tool_call_id] = self.create_task(
            self._nach_zeit(frame.tool_call_id, frame.function_name), name="wartesatz"
        )

    async def _nach_zeit(self, tool_call_id: str, werkzeug: str):
        try:
            await asyncio.sleep(self._nach_secs)
            if self._laufend.get(tool_call_id) is None:
                return
            if self._zug_text or self._spricht or self._gesagt_im_zug:
                return  # das Modell oder ein früherer Wartesatz hat schon etwas gesagt: nicht doppelt
            self._gesagt_im_zug = True
            satz = satz_fuer(werkzeug, self._zuletzt)
            self._zuletzt = satz
            self.gesprochen += 1
            logger.info(f"{self}: Wartesatz bei Werkzeug {werkzeug} ({self.gesprochen})")
            await self.push_frame(TTSSpeakFrame(satz, append_to_context=False))
        finally:
            self._laufend.pop(tool_call_id, None)

    def _alle_verwerfen(self):
        self._modell_abbrechen()
        for task in self._laufend.values():
            if not task.done():
                task.cancel()
        self._laufend.clear()

    async def cleanup(self):
        await super().cleanup()
        self._alle_verwerfen()
