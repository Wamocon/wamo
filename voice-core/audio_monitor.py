"""Diagnose: wie gleichmaessig und wie laut kommt das Mikrofon-Audio vom Browser an?

Sitzt direkt hinter transport.input() und schreibt alle REPORT_SECS eine Zeile ins Log:
Pegel (dBFS, Median/Spitze), Anzahl Luecken > GAP_MS zwischen Audio-Paketen und die laengste
Luecke. Damit lassen sich Browser vergleichen (Chrome gegen VS-Code-Vorschau): Luecken deuten auf
gedrosselte Tabs, Netz oder CPU; sehr leise Pegel auf Mikrofon- oder Verstaerkungsprobleme.

Dazu der Stille-Wächter (MikrofonStille): Kommt STILLE_SECS lang nur digitale Stille an (jedes Paket unter
STILLE_DBFS), meldet der Kern das der Oberfläche ({"type": "mikrofon", "zustand": "still", "sekunden": 10}) und
schreibt eine Warnung ins Log; kommt wieder Ton, folgt einmal "ok". Typische Ursache: Das Standardgerät von Windows
liefert Nullen (Laptop zugeklappt am Dock, Dock- oder Headset-Mikrofon ohne Signal). Echte Mikrofone liegen auch im
leisen Raum deutlich darüber (gemessen etwa -70 dBFS im Median), die stille Spur bei -112 dBFS.
"""

from __future__ import annotations

import math
import statistics
import time
from array import array
from collections.abc import Callable

from loguru import logger

from pipecat.frames.frames import Frame, InputAudioRawFrame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

REPORT_SECS = 10.0
GAP_MS = 60.0
STILLE_DBFS = -100.0
STILLE_SECS = 10.0


class MikrofonStille:
    """Zustand des Stille-Wächters: "still" einmal je stiller Phase, "ok" einmal, wenn danach wieder Ton kommt."""

    def __init__(self, secs: float = STILLE_SECS, schwelle_dbfs: float = STILLE_DBFS):
        self.secs = secs
        self.schwelle = schwelle_dbfs
        self._seit: float | None = None
        self.gemeldet = False

    def pruefen(self, dbfs: float, jetzt: float) -> str | None:
        if dbfs > self.schwelle:
            self._seit = None
            if self.gemeldet:
                self.gemeldet = False
                return "ok"
            return None
        if self._seit is None:
            self._seit = jetzt
        if not self.gemeldet and jetzt - self._seit >= self.secs:
            self.gemeldet = True
            return "still"
        return None


def mikrofon_message(zustand: str, sekunden: float = STILLE_SECS) -> dict:
    return {"type": "mikrofon", "zustand": zustand, "sekunden": round(sekunden)}


class AudioInputMonitor(FrameProcessor):
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, stille_secs: float = STILLE_SECS, **kwargs):
        super().__init__(**kwargs)
        self._clock = clock
        self._stille = MikrofonStille(secs=stille_secs)
        self._reset(clock())

    def _reset(self, now: float):
        self._window_start = now
        self._last = None
        self._gaps = 0
        self._max_gap_ms = 0.0
        self._levels: list[float] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            now = self._clock()
            if self._last is not None:
                gap_ms = (now - self._last) * 1000
                expected_ms = len(frame.audio) / 2 / frame.sample_rate * 1000  # 16-bit mono
                if gap_ms > max(GAP_MS, expected_ms * 3):
                    self._gaps += 1
                    self._max_gap_ms = max(self._max_gap_ms, gap_ms)
            self._last = now
            level = _dbfs(frame.audio)
            self._levels.append(level)
            stille = self._stille.pruefen(level, now)
            if stille == "still":
                logger.warning(
                    "Mikrofon: seit {:.0f} s nur digitale Stille (unter {:.0f} dBFS). Gerät, Stummschaltung oder "
                    "Spur im Browser prüfen; die Oberfläche bekommt eine Meldung", self._stille.secs, STILLE_DBFS,
                )
                await self.push_frame(RTVIServerMessageFrame(data=mikrofon_message("still", self._stille.secs)))
            elif stille == "ok":
                logger.info("Mikrofon: wieder Ton nach Stille")
                await self.push_frame(RTVIServerMessageFrame(data=mikrofon_message("ok", self._stille.secs)))
            if now - self._window_start >= REPORT_SECS:
                self._report(now)
        await self.push_frame(frame, direction)

    def _report(self, now: float):
        if self._levels:
            logger.info(
                "Mikrofon: Pegel Median {:.0f} dBFS, Spitze {:.0f} dBFS | Luecken >{:.0f} ms: {} "
                "(laengste {:.0f} ms) | {} Pakete in {:.0f} s",
                statistics.median(self._levels), max(self._levels), GAP_MS, self._gaps,
                self._max_gap_ms, len(self._levels), now - self._window_start,
            )
        last = self._last
        self._reset(now)
        self._last = last


class SpeakerTracker(FrameProcessor):
    """Sitzt hinter der Spracherkennung und meldet der Oberflaeche, wer gerade spricht.

    Soniox liefert pro Wort eine Sprecher-Kennung ("1", "2", ...). Ohne Stimmabdruck gilt: wer in der
    Sitzung zuerst spricht, ist die angemeldete Person (Index 0), weitere Stimmen bekommen Index 1, 2, ...
    Der Stimmen-Rand der Desktop-App faerbt sich danach.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._order: dict[str, int] = {}
        self._current: int | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            speaker = _last_speaker(frame.result)
            if speaker is not None:
                index = self._order.setdefault(speaker, len(self._order))
                if index != self._current:
                    self._current = index
                    await self.push_frame(RTVIServerMessageFrame(data={"type": "sprecher", "index": index}))
        await self.push_frame(frame, direction)


def _last_speaker(result) -> str | None:
    if not isinstance(result, list):
        return None
    for token in reversed(result):
        if isinstance(token, dict) and token.get("speaker"):
            return str(token["speaker"])
    return None


def _dbfs(pcm: bytes) -> float:
    samples = array("h", pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return -120.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0
