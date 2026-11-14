"""PlaybackTracker: sitzt hinter transport.output() und meldet dem Gate (soft_interrupt.SoftInterruptGate) jedes Wort, das
hinausgeht. Ausgelagert aus soft_interrupt.py, das ihn weiter anbietet (from soft_interrupt import PlaybackTracker).

Das Gate entscheidet für jedes Wort (SoftInterruptGate.wort_beginnt): SPIELEN, das Wort geht hinaus; SCHNEIDEN, es ist
abgeschnitten und gilt als nicht gesprochen; HALTEN (nur mit Halt), WAMO hält vor diesem Wort inne und es kommt später wieder.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
    SystemFrame,
    TTSStartedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

if TYPE_CHECKING:
    from soft_interrupt import SoftInterruptGate

# Was mit einem Wort geschieht, wenn sein Ton hinausgehen soll (SoftInterruptGate.wort_beginnt)
SPIELEN, HALTEN, SCHNEIDEN = "spielen", "halten", "schneiden"

POLL_SECS = 0.02  # so oft schaut der Tracker höchstens nach, ob ein Wort an der Reihe ist
TOLERANZ_NS = 30_000_000  # ein Wort gilt als erreicht, wenn der Ton bis auf 30 ms dort ist


class PlaybackTracker(FrameProcessor):
    """Sitzt hinter transport.output() und meldet dem Gate jedes abgespielte Wort.

    Ohne Halt gilt der Zeitstempel: Pipecat schickt das Wort, wenn seine Uhrzeit erreicht ist, und das Gate rechnet den Vorsprung
    vor dem hörbaren Ton ein (playback_lag_secs). Mit Halt (AusgabeHalt, Innehalten) wartet ein Wort, bis der geschriebene Ton
    seine Stelle erreicht hat, und alles danach wartet mit (die Reihenfolge für den Verlauf bleibt). Hält WAMO inne, warten die
    Wörter mit; ein abgeschnittenes Wort und alles Wartende gelten nach einem Abbruch als nicht gesprochen.
    """

    def __init__(
        self,
        gate: SoftInterruptGate,
        halt=None,
        *,
        max_wait_secs: float = 3.0,
        uhr: Callable[[], float] = time.monotonic,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._gate = gate
        self._halt = halt
        self._max_wait = max_wait_secs  # Sicherung, falls nie Ton kommt
        self._uhr = uhr
        self._queue: deque[tuple[float, Frame]] = deque()
        self._task: asyncio.Task | None = None
        self._sperre = asyncio.Lock()  # eine Zustellung nach der anderen: ein neues Wort überholt kein wartendes

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if self._halt is not None:
            await self._mit_halt(frame, direction)
            return
        if isinstance(frame, TTSStartedFrame) and direction == FrameDirection.DOWNSTREAM:
            if getattr(frame, "append_to_context", True):
                self._gate.antwort_beginnt()
        elif isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._gate.on_word_played(frame.text, getattr(frame, "append_to_context", True)):
                return  # abgeschnitten -> nicht in den Gespraechsverlauf
        await self.push_frame(frame, direction)

    async def _mit_halt(self, frame: Frame, direction: FrameDirection):
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, InterruptionFrame):
            self._queue.clear()  # nicht mehr gespielt -> nicht in den Verlauf
            self._halt.abschneiden()
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, BotStoppedSpeakingFrame):
            await self._leeren()  # der Ton ist durch, alles Wartende auch
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSStartedFrame) and getattr(frame, "append_to_context", True):
            self._gate.antwort_beginnt()  # zählt, wenn der Abschnitt ankommt, auch wenn Wörter davor noch warten
        if isinstance(frame, (SystemFrame, OutputAudioRawFrame)):
            await self.push_frame(frame, direction)
            return
        async with self._sperre:
            if self._queue or self._muss_warten(frame):
                self._queue.append((self._uhr(), frame))
                self._task_sichern()
                return
            if not await self._zustellen(frame):
                self._queue.appendleft((self._uhr(), frame))
                self._task_sichern()

    def _muss_warten(self, frame: Frame) -> bool:
        if not isinstance(frame, TTSTextFrame) or not frame.pts:
            return False
        if self._halt.haelt:
            return True
        gespielt = self._halt.gespielt_bis_ns()
        return gespielt is None or gespielt + TOLERANZ_NS < frame.pts

    async def _zustellen(self, frame: Frame) -> bool:
        """False: das Wort wartet, weil WAMO davor innehält."""
        if isinstance(frame, TTSTextFrame):
            entscheidung = self._gate.wort_beginnt(frame.text, getattr(frame, "append_to_context", True))
            if entscheidung == HALTEN:
                return False
            if entscheidung == SCHNEIDEN:
                return True  # abgeschnitten -> nicht in den Gespraechsverlauf
        await self.push_frame(frame)
        return True

    def _task_sichern(self):
        if self._task is None or self._task.done():
            self._task = self.create_task(self._leeren_nach_ton(), name="playback_tracker")

    async def _warten(self):
        await self._halt.warte_auf_fortschritt(POLL_SECS)

    async def _leeren_nach_ton(self):
        try:
            while self._queue:
                seit, frame = self._queue[0]
                haelt = self._halt.haelt
                ueberfaellig = self._uhr() - seit > self._max_wait and not haelt
                if self._muss_warten(frame) and not ueberfaellig:
                    await self._warten()
                    continue
                async with self._sperre:
                    if not self._queue or self._queue[0][1] is not frame:
                        continue  # inzwischen geleert (Abbruch, Ende der Antwort)
                    self._queue.popleft()
                    zugestellt = await self._zustellen(frame)
                    if not zugestellt:
                        self._queue.appendleft((seit, frame))
                if not zugestellt:
                    await self._warten()
        finally:
            self._task = None

    async def _leeren(self):
        if self._task:
            await self.cancel_task(self._task)
            self._task = None
        while self._queue:
            _, frame = self._queue.popleft()
            if isinstance(frame, TTSTextFrame) and self._gate.wort_beginnt(frame.text, getattr(frame, "append_to_context", True)) == SCHNEIDEN:
                continue
            await self.push_frame(frame)

    async def cleanup(self):
        await super().cleanup()
        if self._task:
            await self.cancel_task(self._task)
            self._task = None
