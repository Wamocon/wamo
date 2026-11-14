"""Stimmprüfung im Audiopfad: schneidet jede Äußerung aus einem Ringpuffer, vergleicht sie mit dem Stimmabdruck und lernt ihn auf Klick ein.

Der Prüfer sitzt direkt hinter dem Mikrofon (vor der Spracherkennung). Er hält die letzten Sekunden Ton vor, schneidet jede Äußerung anhand der
VAD-Meldungen heraus und vergleicht die laufende Äußerung (höchstens die letzten MAX_PRUEF_SECS) mit dem Stimmabdruck dieser Sitzung
(StimmGate.abdruck, stimme_gate.py). Ergebnis: eigen, fremd, unklar oder kurz. Die Prüfung beginnt beim Sprechende und läuft in einem Thread
parallel zur Spracherkennung. Wer ein Ergebnis zu dieser Äußerung braucht (stimme_waechter.py), fragt urteil_abwarten und wartet höchstens die
übergebene Frist; ein Ablauf der Frist gilt als unklar (kein Urteil). Rohton wird nie gespeichert, der Ringpuffer lebt nur im Arbeitsspeicher.

Das Modell ist je Prozess einmal vorhanden (stimme_modell.MODELL), der Prüfzustand und der Abdruck gehören je Sitzung zum Gate: zwei Profile
im selben Prozess teilen nur den Rechner. Gerechnet wird nur für ein Profil mit Abdruck und nur, wenn der Modus nicht aus ist.

Einlernen (stimme_einlernen.py) hängt am Prüfer, weil es den Ton braucht: Solange es läuft, sammelt es den Ton und der Prüfer ersetzt ihn
für Spracherkennung und VAD durch Stille. Meldungen an die Oberfläche: {"type": "stimme", "ergebnis": eigen | fremd | unklar, "wert",
"modus"} je Äußerung und {"type": "stimme", "status", "modus"} beim Stand (kurz zählt als unklar).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

from stimme_einlernen import Einlernen
from stimme_gate import KURZ, UNKLAR, StimmGate
from stimme_modell import kosinus

RING_SECS = 10.0
MAX_PRUEF_SECS = 8.0
ZUG_PAUSE_SECS = 3.0  # längere Stille: die nächste Äußerung wird für sich geprüft
VORLAUF_SECS = 0.25  # VAD meldet den Beginn erst nach start_secs, etwas Luft davor
NACHLAUF_SECS = 0.1
URTEIL_MAX_ALTER_SECS = 8.0  # so alt darf ein Urteil höchstens sein, damit ein Transkript sich darauf stützen darf


@dataclass(frozen=True)
class Urteil:
    """Ergebnis der Prüfung einer Äußerung. urteil: eigen, fremd, unklar oder kurz. secs: reine Sprache. um: Zeitpunkt (Uhr des Gates)."""

    nr: int
    urteil: str
    wert: float | None
    secs: float
    ueberlappung: bool
    um: float


class Stimmpruefer(FrameProcessor):
    """Prüft jede Äußerung gegen den Stimmabdruck und lernt ihn auf Klick ein (siehe oben)."""

    def __init__(
        self,
        gate: StimmGate,
        modell,
        *,
        speichern: Callable[[np.ndarray, str, datetime], Awaitable[bool]],
        sagen: Callable[[str], Awaitable[None]],
        uhr: Callable[[], float] = time.time,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._gate = gate
        self._modell = modell
        self._uhr = uhr  # Wanduhr wie VADUser...SpeakingFrame.timestamp
        self._einlernen = Einlernen(gate, modell, senden=self._senden, sagen=sagen, speichern=speichern, uhr=uhr)
        self._ring: deque[tuple[float, bytes]] = deque()
        self._ring_secs = 0.0
        self._rate = 16000
        self._segment_start: float | None = None  # mit Luft davor (zum Ausschneiden)
        self._sprache_beginn = 0.0  # wie VAD ihn meldet (zum Messen der reinen Sprache)
        self._segment_ueberlappung = False
        self._zug = b""  # laufende Äußerung, höchstens MAX_PRUEF_SECS
        self._zug_secs = 0.0
        self._zug_ueberlappung = False
        self._zug_zu = True
        self._letztes_ende = -math.inf
        self._bot_spricht = False
        self._sprechend = False  # laut VAD läuft eine Äußerung (die Prüfung beginnt erst mit ihrem Ende)
        self._task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._ruhe = asyncio.Event()  # gesetzt, solange keine Äußerung läuft und keine Prüfung aussteht
        self._ruhe.set()
        self._letztes: Urteil | None = None
        self._nr = 0

    # ---------- Stand und Einlernen ----------

    async def status_melden(self) -> None:
        await self._senden({"type": "stimme", "status": self._gate.status, "modus": self._gate.modus})

    async def _senden(self, daten: dict) -> None:
        await self.push_frame(RTVIServerMessageFrame(data=daten))

    @property
    def einlernen_aktiv(self) -> bool:
        return self._einlernen.beschaeftigt

    async def einlernen_starten(self, version: str, einwilligung_am: datetime) -> None:
        await self._einlernen.starten(version, einwilligung_am)

    async def einlernen_abbrechen(self) -> None:
        await self._einlernen.abbrechen()

    async def einlernen_warten(self) -> None:
        await self._einlernen.warten()

    # ---------- Frames ----------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            await self.on_audio(frame)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self.on_speech_start(frame.timestamp, frame.start_secs)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.on_speech_stop(frame.timestamp, frame.stop_secs)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self.on_turn_end()
        elif isinstance(frame, BotStartedSpeakingFrame):
            self.on_bot_speaking(True)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.on_bot_speaking(False)
        await self.push_frame(frame, direction)

    def on_turn_end(self) -> None:
        self._zug_zu = True  # die nächste Äußerung wird für sich geprüft

    def on_bot_speaking(self, spricht: bool) -> None:
        self._bot_spricht = spricht
        if spricht:
            self._zug_zu = True  # was danach kommt, ist eine neue Äußerung
        self._einlernen.bot_spricht(spricht)

    async def on_audio(self, frame: InputAudioRawFrame) -> None:
        pcm = frame.audio
        self._rate = frame.sample_rate
        self._ring.append((self._uhr(), pcm))
        self._ring_secs += _secs(pcm, self._rate)
        while self._ring_secs > RING_SECS and len(self._ring) > 1:
            self._ring_secs -= _secs(self._ring.popleft()[1], self._rate)
        if await self._einlernen.sammeln(pcm, self._rate):
            frame.audio = bytes(len(pcm))  # Spracherkennung und VAD hören Stille

    def on_speech_start(self, zeit: float, start_secs: float) -> None:
        if self._einlernen.aktiv:
            return
        self._sprechend = True
        self._ruhe.clear()
        self._sprache_beginn = zeit - start_secs
        if self._zug_zu or self._sprache_beginn - self._letztes_ende > ZUG_PAUSE_SECS:
            self._zug, self._zug_secs, self._zug_ueberlappung, self._zug_zu = b"", 0.0, False, False
        self._segment_start = self._sprache_beginn - VORLAUF_SECS
        self._segment_ueberlappung = self._bot_spricht

    def on_speech_stop(self, zeit: float, stop_secs: float) -> None:
        self._sprechend = False
        if self._einlernen.aktiv or self._segment_start is None:
            self._ruhe_aktualisieren()
            return
        sprache_ende = zeit - stop_secs
        segment = self._ausschneiden(self._segment_start, sprache_ende + NACHLAUF_SECS)
        self._zug_ueberlappung = self._zug_ueberlappung or self._segment_ueberlappung or self._bot_spricht
        self._segment_start = None
        self._letztes_ende = sprache_ende
        # Nur die letzten MAX_PRUEF_SECS behalten; gemessen wird die reine Sprache ohne die Luft drumherum
        self._zug = (self._zug + segment)[-int(MAX_PRUEF_SECS * self._rate) * 2 :]
        self._zug_secs = min(self._zug_secs + max(0.0, sprache_ende - self._sprache_beginn), MAX_PRUEF_SECS)
        vorherige = self._task
        self._task = self._starten(
            self._pruefen(self._zug, self._rate, self._zug_secs, self._zug_ueberlappung, self._gate.uhr(), vorherige)
        )
        self._gate.verfolgen(self._task)
        self._ruhe.clear()

    def _ausschneiden(self, start: float, ende: float) -> bytes:
        teile = []
        for t_ende, pcm in self._ring:
            t_start = t_ende - _secs(pcm, self._rate)
            if t_ende > start and t_start < ende:
                teile.append(pcm)
        return b"".join(teile)

    # ---------- Prüfen ----------

    async def urteil_abwarten(self, frist: float) -> Urteil | None:
        """Wartet höchstens frist Sekunden, bis keine Äußerung mehr läuft und keine Prüfung aussteht, und gibt das jüngste Urteil zurück.
        None: Frist abgelaufen (gilt als unklar), noch nichts geprüft oder das Urteil ist zu alt für das, wonach gefragt wird."""
        try:
            await asyncio.wait_for(self._ruhe.wait(), frist)
        except asyncio.TimeoutError:
            return None
        urteil = self._letztes
        if urteil is None or self._gate.uhr() - urteil.um > URTEIL_MAX_ALTER_SECS:
            return None
        return urteil

    async def _pruefen(
        self, pcm: bytes, rate: int, secs: float, ueberlappung: bool, gehoert_um: float, vorherige: asyncio.Task | None
    ) -> None:
        """secs: reine Sprache. gehoert_um: Ende der Äußerung (Uhr des Gates)."""
        if vorherige and not vorherige.done():
            await asyncio.wait({vorherige})  # der Reihe nach, sonst überholt ein altes Ergebnis
        gate = self._gate
        if gate.abdruck is None or gate.modus == "aus":
            return
        rechner = await self._modell.get()
        gate.modell_ok = rechner is not None
        abdruck = gate.abdruck
        try:
            if rechner is None or not pcm:
                raise RuntimeError("kein Modell" if rechner is None else "kein Ton ausgeschnitten")
            wert = kosinus(await asyncio.to_thread(rechner.embed, pcm, rate), abdruck)
        except Exception as e:  # nicht geprüft heißt nicht erkannt: nie das alte Ergebnis weiterverwenden
            if gate.abdruck is None:
                return  # der Abdruck wurde während des Rechnens gelöscht
            logger.warning("Stimmprüfung fehlgeschlagen ({}), gilt als unklar", e)
            await self._ergebnis(UNKLAR, None, secs, ueberlappung, gehoert_um)
            return
        if gate.abdruck is None:
            return
        await self._ergebnis(gate.einstufen(wert, secs, ueberlappung), wert, secs, ueberlappung, gehoert_um)

    async def _ergebnis(self, urteil: str, wert: float | None, secs: float, ueberlappung: bool, gehoert_um: float) -> None:
        self._gate.eintragen(urteil, gehoert_um)
        self._nr += 1
        self._letztes = Urteil(self._nr, urteil, wert, secs, ueberlappung, self._gate.uhr())
        if wert is not None:
            logger.info("Stimme: {:.2f} bei {:.1f} s{} -> {}", wert, secs, " (WAMO sprach)" if ueberlappung else "", urteil)
        meldung = {"type": "stimme", "ergebnis": UNKLAR if urteil == KURZ else urteil, "modus": self._gate.modus}
        if wert is not None:
            meldung["wert"] = round(float(wert), 2)
        await self._senden(meldung)

    # ---------- Aufräumen ----------

    def _starten(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._fertig)
        return task

    def _fertig(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        self._ruhe_aktualisieren()

    def _ruhe_aktualisieren(self) -> None:
        if not self._sprechend and (self._task is None or self._task.done()):
            self._ruhe.set()
        else:
            self._ruhe.clear()

    async def cleanup(self):
        await super().cleanup()
        self._einlernen.aufraeumen()
        for task in list(self._tasks):
            task.cancel()
        self._ruhe.set()  # wer noch wartet, bekommt kein Urteil statt ewig zu warten


def _secs(pcm: bytes, rate: int) -> float:
    return len(pcm) / 2 / rate if rate else 0.0
