"""Tonausgang anhalten und fortsetzen, und wissen, wie weit der Ton wirklich ist (Innehalten statt Abbrechen).

Pipecat schreibt WAMOs Ton in Happen von 40 ms über write_audio_frame des Ausgangs (transport.output()) und wartet jeweils,
bis die Spur den Happen abgeholt hat; alles Weitere wartet in der Warteschlange des Ausgangs. Genau an dieser Stelle sitzt
AusgabeHalt:
  * anhalten(): der nächste Happen wartet, die Spur sendet Stille. Nichts geht verloren.
  * fortsetzen(): es geht an derselben Stelle weiter (falscher Alarm: die Person hat nur kurz etwas gesagt).
  * abschneiden(): nach einer echten Unterbrechung. Happen der alten Antwort, die danach noch durchrutschen, verwirft es; Ton, der
    erst nach dem Abbruch entsteht (ein Füllwort, eine Ansage), geht hinaus.
  * gespielt_bis_ns(): bis zu welcher Uhrzeit der Pipeline-Uhr (gleiche Skala wie TTSTextFrame.pts) der Ton dieser Antwort
    hinausgegangen ist.

Warum die Uhrzeit: Pipecat rechnet die Wort-Zeitstempel ab dem ersten Ton der Antwort, ohne Lücken. Liefert die Sprachausgabe
einmal langsamer, als gesprochen wird, oder steht der Ton in einer Pause, läuft die Uhr weiter, der Ton nicht. Die Wörter
eilten dem Ton dann voraus (gemessen etwa 0,5 s, siehe soft_interrupt.py). Gemessen am geschriebenen Ton meldet der
PlaybackTracker jedes Wort, wenn es wirklich hinausgeht.

Je Sitzung gibt es einen eigenen AusgabeHalt (er hängt am Ausgang dieser Sitzung), der Kern teilt nichts zwischen Sitzungen.
Die Sprachausgabe im Haus liefert ganze Sätze ohne Wortzeiten: dort gibt es keinen Halt, es gilt die Zeitgrenze des Gates.

Schalter: WAMO_INNEHALTEN (an/aus, Standard an), WAMO_INNEHALTEN_WEITER_SECS (Standard 0,7: so lange Stille, dann spricht WAMO
weiter), WAMO_INNEHALTEN_MAX_SECS (Standard 2,5: so lange hält WAMO höchstens inne).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from loguru import logger

from env_schalter import schalter_an, zahl_im_bereich

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.utils import obj_id

# Nach einem Abbruch noch so lange Happen der alten Antwort verwerfen, bis Pipecat die alte Warteschlange geleert hat. Alt heißt:
# vor dem Abbruch entstanden. Pipecat nummeriert jedes Frame fortlaufend (Frame.id), und der Ausgang schneidet den Ton beim Einreihen
# in neue Happen; ein Füllwort oder eine Ansage direkt nach dem Abbruch hat also eine höhere Nummer und geht hinaus.
ABBRUCH_NACHLAUF_SECS = 0.15
# Sicherung: Pipecat hält ein Schreiben, das länger als 10 s dauert (TransportParams.audio_out_write_timeout_secs), für eine tote
# Verbindung und beendet die Sitzung. Länger als das hier bleibt keine Pause stehen.
MAX_PAUSE_SECS = 4.0

WEITER_SECS_STANDARD = 0.7
MAX_SECS_STANDARD = 2.5
MAX_SECS_OBERGRENZE = 3.5  # darunter bleibt die Pause sicher unter MAX_PAUSE_SECS

Schreiben = Callable[[OutputAudioRawFrame], Awaitable[bool]]


def innehalten_an(wert: str | None = None) -> bool:
    """WAMO_INNEHALTEN: ein einzelnes echtes Wort der Person lässt WAMO innehalten, statt abzubrechen (Standard an)."""
    return schalter_an("WAMO_INNEHALTEN", wert)


def innehalten_weiter_secs(wert: str | None = None) -> float:
    """WAMO_INNEHALTEN_WEITER_SECS: so viel Stille nach dem letzten Wort, dann spricht WAMO an derselben Stelle weiter."""
    zahl = zahl_im_bereich("WAMO_INNEHALTEN_WEITER_SECS", 0.2, 3.0, wert)
    return WEITER_SECS_STANDARD if zahl is None else zahl


def innehalten_max_secs(wert: str | None = None) -> float:
    """WAMO_INNEHALTEN_MAX_SECS: so lange hält WAMO höchstens inne, auch wenn die Person weiterspricht."""
    zahl = zahl_im_bereich("WAMO_INNEHALTEN_MAX_SECS", 0.5, MAX_SECS_OBERGRENZE, wert)
    return MAX_SECS_STANDARD if zahl is None else zahl


def ton_ns(frame: OutputAudioRawFrame) -> int:
    """Spieldauer eines 16-Bit-PCM-Happens in Nanosekunden."""
    byte_je_sekunde = frame.sample_rate * frame.num_channels * 2
    return len(frame.audio) * 1_000_000_000 // byte_je_sekunde if byte_je_sekunde else 0


class AusgabeHalt:
    """Hält den Tonausgang einer Sitzung an der Stelle zwischen zwei Happen an und misst die Position des gespielten Tons."""

    def __init__(
        self,
        *,
        uhr_ns: Callable[[], int] | None = None,
        uhr: Callable[[], float] = time.monotonic,
    ):
        self._offen = asyncio.Event()
        self._offen.set()
        self._uhr_ns = uhr_ns
        self._uhr = uhr
        self._beginn_ns: int | None = None  # Uhrzeit, zu der der erste Ton dieser Antwort ankam
        self._gespielt_ns = 0  # so viel Sprachton dieser Antwort ist hinausgegangen
        self._verwerfen_bis = 0.0
        self._neu_ab_id = 0  # Frames mit kleinerer Nummer entstanden vor dem letzten Abbruch
        self._fortschritt = asyncio.Event()

    @property
    def haelt(self) -> bool:
        return not self._offen.is_set()

    def einhaengen(self, ausgang) -> bool:
        """Klinkt sich in write_audio_frame des Ausgangs ein (transport.output() dieser Sitzung). False: es gibt keine solche Stelle."""
        schreiben = getattr(ausgang, "write_audio_frame", None)
        if schreiben is None:
            logger.warning("AusgabeHalt: Ausgang ohne write_audio_frame, Innehalten bleibt aus")
            return False
        if self._uhr_ns is None:
            self._uhr_ns = lambda: ausgang.get_clock().get_time()

        async def mit_halt(frame: OutputAudioRawFrame) -> bool:
            return await self.schreiben(frame, schreiben)

        ausgang.write_audio_frame = mit_halt
        return True

    async def schreiben(self, frame: OutputAudioRawFrame, schreibe: Schreiben) -> bool:
        if not self._offen.is_set():
            try:
                await asyncio.wait_for(self._offen.wait(), MAX_PAUSE_SECS)
            except asyncio.TimeoutError:
                logger.warning("AusgabeHalt: Pause zu lang, der Ton läuft weiter")
                self.fortsetzen()
        if self._uhr() < self._verwerfen_bis and frame.id < self._neu_ab_id:
            return False  # Rest einer abgebrochenen Antwort
        if isinstance(frame, TTSAudioRawFrame):
            if self._beginn_ns is None:
                self.ersten_ton_merken()
            self._gespielt_ns += ton_ns(frame)
            self._fortschritt.set()
        return await schreibe(frame)

    def ersten_ton_merken(self) -> None:
        """Der erste Ton einer Antwort ist am Ausgang angekommen (WiedergabeStart, sonst der erste Happen)."""
        if self._beginn_ns is None and self._uhr_ns is not None:
            self._beginn_ns = self._uhr_ns()

    def gespielt_bis_ns(self) -> int | None:
        if self._beginn_ns is None:
            return None
        return self._beginn_ns + self._gespielt_ns

    def anhalten(self) -> None:
        self._offen.clear()

    def fortsetzen(self) -> None:
        self._offen.set()
        self._fortschritt.set()

    def abschneiden(self) -> None:
        """Echte Unterbrechung: Rest verwerfen (nur was vor dem Abbruch entstand), danach offen für die nächste Antwort."""
        self._verwerfen_bis = self._uhr() + ABBRUCH_NACHLAUF_SECS
        self._neu_ab_id = obj_id()
        self.neue_antwort()
        self.fortsetzen()

    def neue_antwort(self) -> None:
        self._beginn_ns = None
        self._gespielt_ns = 0
        self._fortschritt.set()

    async def warte_auf_fortschritt(self, frist: float) -> None:
        """Wartet, bis wieder Ton hinausging oder sich der Zustand änderte (höchstens frist Sekunden)."""
        self._fortschritt.clear()
        try:
            await asyncio.wait_for(self._fortschritt.wait(), frist)
        except asyncio.TimeoutError:
            pass


class WiedergabeStart(FrameProcessor):
    """Direkt vor transport.output(): merkt, wann der erste Ton einer Antwort am Ausgang ankommt.

    Ab diesem Moment zählt Pipecat die Wort-Zeitstempel (start_word_timestamps). Läuft vorher noch etwas anderes (ein Füllwort,
    eine Pause), wartet der Ton, die Wörter aber nicht.
    """

    def __init__(self, halt: AusgabeHalt, **kwargs):
        super().__init__(**kwargs)
        self._halt = halt

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            self._halt.ersten_ton_merken()
        elif isinstance(frame, BotStoppedSpeakingFrame) and direction == FrameDirection.UPSTREAM:
            self._halt.neue_antwort()  # Antwort fertig gespielt; die nächste zählt neu
        elif isinstance(frame, InterruptionFrame):
            self._halt.neue_antwort()
        await self.push_frame(frame, direction)


def halt_fuer(ausgang, *, haus: bool, wert: str | None = None) -> AusgabeHalt | None:
    """Der Halt einer Sitzung, oder None: Schalter aus, Sprachausgabe im Haus (ganze Sätze, keine Wortzeiten) oder ein Ausgang
    ohne write_audio_frame. Ohne Halt gilt das Verfahren ohne Innehalten (Zeitgrenze, Ausblenden in der Oberfläche)."""
    if haus or not innehalten_an(wert):
        return None
    halt = AusgabeHalt()
    return halt if halt.einhaengen(ausgang) else None
