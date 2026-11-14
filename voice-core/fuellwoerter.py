"""Kurze, vorab gesprochene Füllwörter: keine tote Stille, während WAMO arbeitet.

Menschen sagen "Moment." und fangen dann an, sie schweigen nicht drei Sekunden. Startet das Modell ein Werkzeug, ohne
vorher etwas zu sagen, kommt der erste Ton sonst erst nach dem Modell, dem Werkzeug, dem zweiten Modellaufruf und der
Sprachausgabe.

  * Werkzeug: hat das Modell ein Werkzeug gestartet, ohne vorher etwas zu sagen, und ist nach nach_secs noch kein Ton
    da, sagt WAMO "Moment." (vorab aufgenommen, also sofort hörbar, ohne auf die Sprachausgabe zu warten). Läuft das
    Werkzeug dann noch lang_secs, einmal "Dauert noch einen Moment."
  * Rückmeldelaut (WAMO_MHM, Standard aus): hält das Modell den Satz der Person für unfertig (Marker ◐), ein kurzes
    "Mhm" wie beim Zuhören. Selten (Ruhezeit, Zufall) und nie, wenn die Person schon weiterspricht.

Die Füllwörter gehen als einfacher Ton hinaus (OutputAudioRawFrame, kein TTSAudioRawFrame): der Ausgang meldet dafür
kein "WAMO spricht", nichts davon landet im Verlauf, und Unterbrechungen laufen wie immer. Die Aufnahmen macht die
Sprachausgabe einmal in derselben Stimme und Einstellung wie im Gespräch, danach liegen sie in der Ablage (ablage()).
Der Kern bedient mehrere Sitzungen: Je Stimme und Einstellung gibt es eine Bank (bank_fuer), je Sitzung einen Spieler.
Fehlende Aufnahmen holt der Spieler erst beim Start der Pipeline, der Aufbau einer Sitzung greift nicht ins Netz.

Schalter: WAMO_FUELLWOERTER (an/aus, Standard an), WAMO_FUELLWORT_SECS (Standard 0,4), WAMO_MHM (an/aus, Standard aus).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import numpy as np
from loguru import logger

from env_schalter import schalter_an, zahl_im_bereich

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMMarkerFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    StartFrame,
    UserStartedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

WERKZEUG_SAETZE = ("Moment.", "Einen Moment.", "Sekunde.")
LANG_SAETZE = ("Dauert noch einen Moment.", "Gleich hab ich's.")
MHM_SAETZE = ("Mhm.", "Hm-hm.")
ALLE_SAETZE = WERKZEUG_SAETZE + LANG_SAETZE + MHM_SAETZE
MARKER_UNFERTIG_KURZ = "◐"

ABTASTRATE = 24000  # wie der Ausgang (PipelineParams.audio_out_sample_rate): kein Umrechnen
HAPPEN_BYTES = ABTASTRATE * 40 // 1000 * 2  # Pipecat schreibt Happen von 40 ms (audio_out_10ms_chunks=4)
MODELL = "tts-rt-v2"  # wie der Soniox-Dienst von Pipecat
AUFNAHME_FRIST_SECS = 15.0
NACH_SECS_STANDARD = 0.4
LANG_SECS_STANDARD = 6.0

Sprechen = Callable[[str], Awaitable[bytes]]


def fuellwoerter_an() -> bool:
    return schalter_an("WAMO_FUELLWOERTER")


def mhm_an() -> bool:
    return schalter_an("WAMO_MHM")


def fuellwort_secs() -> float:
    wert = zahl_im_bereich("WAMO_FUELLWORT_SECS", 0.1, 5.0)
    return NACH_SECS_STANDARD if wert is None else wert


def ablage() -> Path:
    """Ordner der Aufnahmen: im Cache-Ordner des Dienstes (XDG_CACHE_HOME, am Server /var/cache/wamo-core), sonst neben dem Code."""
    cache = (os.getenv("XDG_CACHE_HOME") or "").strip()
    return (Path(cache) / "wamo" if cache else Path(__file__).parent / ".cache") / "fuellwoerter"


def stille_abschneiden(pcm: bytes, abtastrate: int = ABTASTRATE, *, schwelle: int = 500,
                       vorn_ms: int = 30, hinten_ms: int = 80) -> bytes:
    """Stille vorn und hinten abschneiden. Die Sprachausgabe setzt vor und nach jedem Satz eine Pause."""
    werte = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16)
    laut = np.flatnonzero(np.abs(werte.astype(np.int32)) > schwelle)
    if laut.size == 0:
        return b""
    start = max(0, int(laut[0]) - abtastrate * vorn_ms // 1000)
    ende = min(werte.size, int(laut[-1]) + 1 + abtastrate * hinten_ms // 1000)
    return werte[start:ende].tobytes()


def auf_happen(pcm: bytes, happen_bytes: int = HAPPEN_BYTES) -> bytes:
    """Mit Stille auf ganze Happen von 40 ms auffüllen. Ein angefangener Happen bliebe sonst im Ausgang liegen, bis der
    nächste Ton kommt, und klebte dann vor der Antwort."""
    rest = len(pcm) % happen_bytes
    return pcm + bytes(happen_bytes - rest) if rest else pcm


async def soniox_sprechen(api_key: str, url: str, stimme: str, einstellung: dict, text: str) -> bytes:
    """Ein kurzer Satz als 16-Bit-PCM (24 kHz, mono) über den Websocket der Soniox-Sprachausgabe."""
    import websockets

    kennung = "fuellwort-" + hashlib.sha1(text.encode()).hexdigest()[:8]
    konfig = {
        "api_key": api_key,
        "stream_id": kennung,
        "model": MODELL,
        "voice": stimme,
        "language": "de",
        "audio_format": "pcm_s16le",
        "sample_rate": ABTASTRATE,
        **einstellung,
    }
    ton = bytearray()
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps(konfig))
        await ws.send(json.dumps({"text": text + " ", "text_end": False, "stream_id": kennung}))
        await ws.send(json.dumps({"text": "", "text_end": True, "stream_id": kennung}))
        async for roh in ws:
            nachricht = json.loads(roh)
            if nachricht.get("error_code"):
                raise RuntimeError(f"Soniox {nachricht.get('error_code')}: {nachricht.get('error_message')}")
            if nachricht.get("audio"):
                ton += base64.b64decode(nachricht["audio"])
            if nachricht.get("terminated"):
                break
    return bytes(ton)


class FuellwortBank:
    """Die Aufnahmen der Füllwörter, je Stimme und Einstellung zwischengespeichert."""

    def __init__(self, stimme: str, einstellung: dict | None = None, *, ordner: Path | None = None,
                 saetze: Sequence[str] = ALLE_SAETZE):
        self.abtastrate = ABTASTRATE
        self._stimme = stimme
        self._einstellung = dict(einstellung or {})
        self._ordner = ordner if ordner is not None else ablage()
        self._saetze = tuple(saetze)
        self._ton: dict[str, bytes] = {}
        self._zuletzt: dict[tuple[str, ...], str] = {}
        self._aufgabe: asyncio.Task | None = None

    @property
    def bereit(self) -> bool:
        return bool(self._ton)

    def _pfad(self, text: str) -> Path:
        schluessel = json.dumps(
            {"voice": self._stimme, "tuning": self._einstellung, "text": text, "model": MODELL, "rate": ABTASTRATE},
            sort_keys=True,
        )
        return self._ordner / f"{hashlib.sha1(schluessel.encode()).hexdigest()[:16]}.pcm"

    def aus_ablage_laden(self) -> list[str]:
        """Lädt, was schon aufgenommen ist, und gibt zurück, was fehlt."""
        fehlt = []
        for text in self._saetze:
            pfad = self._pfad(text)
            if pfad.is_file() and pfad.stat().st_size > 0:
                self._ton[text] = pfad.read_bytes()
            else:
                fehlt.append(text)
        return fehlt

    async def vorbereiten(self, sprechen: Sprechen) -> None:
        """Fehlende Aufnahmen einmal bei der Sprachausgabe holen. Fehler kosten nur das Füllwort, nie das Gespräch."""
        fehlt = self.aus_ablage_laden()
        if not fehlt:
            return

        async def eines(text: str) -> None:
            try:
                pcm = auf_happen(stille_abschneiden(await asyncio.wait_for(sprechen(text), AUFNAHME_FRIST_SECS)))
            except Exception as e:  # Netz, Sprachausgabe, Zeitlimit
                logger.warning("Füllwort {!r} nicht aufgenommen: {}", text, type(e).__name__)
                return
            if not pcm:
                logger.warning("Füllwort {!r}: die Sprachausgabe lieferte nur Stille", text)
                return
            self._ton[text] = pcm
            try:
                self._ordner.mkdir(parents=True, exist_ok=True)
                self._pfad(text).write_bytes(pcm)
            except OSError as e:
                logger.warning("Füllwort {!r} nicht gespeichert: {}", text, type(e).__name__)

        # Nacheinander, nie parallel: die Sprachausgabe begrenzt gleichzeitige Verbindungen, und das laufende Gespräch
        # braucht auch eine.
        for text in fehlt:
            await eines(text)
        logger.info("Füllwörter bereit: {} von {}", len(self._ton), len(self._saetze))

    def im_hintergrund_vorbereiten(self, sprechen: Sprechen) -> None:
        """Startet das Vorbereiten einmal, ohne das Gespräch aufzuhalten. Bis die Aufnahmen da sind, gibt es kein Füllwort."""
        if self._aufgabe is None or (self._aufgabe.done() and not self.bereit):
            self._aufgabe = asyncio.create_task(self.vorbereiten(sprechen))

    def waehlen(self, saetze: Sequence[str]) -> tuple[str, bytes] | None:
        """Ein vorhandenes Füllwort der Gruppe, möglichst nicht dasselbe wie beim letzten Mal."""
        gruppe = tuple(saetze)
        vorhanden = [s for s in gruppe if s in self._ton]
        if not vorhanden:
            return None
        frisch = [s for s in vorhanden if s != self._zuletzt.get(gruppe)] or vorhanden
        text = random.choice(frisch)
        self._zuletzt[gruppe] = text
        return text, self._ton[text]


_BAENKE: dict[str, FuellwortBank] = {}


def bank_fuer(stimme: str, einstellung: dict | None = None) -> FuellwortBank:
    """Eine Bank je Stimme und Einstellung für den ganzen Prozess: Sitzungen mit derselben Stimme teilen sich die Aufnahmen."""
    schluessel = json.dumps({"voice": stimme, "tuning": dict(einstellung or {})}, sort_keys=True)
    bank = _BAENKE.get(schluessel)
    if bank is None:
        bank = _BAENKE[schluessel] = FuellwortBank(stimme, einstellung)
    return bank


class FuellwortSpieler(FrameProcessor):
    """Zwischen Modell und Sprachausgabe: spielt Füllwörter, wenn sonst Stille wäre. Ein Spieler je Sitzung."""

    def __init__(
        self,
        bank: FuellwortBank,
        *,
        werkzeug: bool = True,
        nach_secs: float = NACH_SECS_STANDARD,
        lang_secs: float = LANG_SECS_STANDARD,
        mhm: bool = False,
        mhm_ruhe_secs: float = 20.0,
        mhm_anteil: float = 0.6,
        sprechen: Sprechen | None = None,
        zufall: Callable[[], float] = random.random,
        uhr: Callable[[], float] = time.monotonic,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._bank = bank
        self._werkzeug = werkzeug
        self._nach = nach_secs
        self._lang = lang_secs
        self._mhm = mhm
        self._mhm_ruhe = mhm_ruhe_secs
        self._mhm_anteil = mhm_anteil
        self._sprechen = sprechen  # holt fehlende Aufnahmen, erst beim Start der Pipeline (nie beim Aufbau)
        self._zufall = zufall
        self._uhr = uhr
        self._wamo_spricht = False
        self._person_spricht = False
        self._text_in_antwort = False  # das Modell hat in dieser Antwort schon etwas zu sagen
        self._sprach_im_zug = False  # seit dem letzten Satz der Person hat WAMO schon gesprochen
        self._werkzeuge: set[str] = set()
        self._werkzeug_seit: float | None = None
        self._gespielt_kurz = False
        self._gespielt_lang = False
        self._ton_bis = 0.0  # bis dann klingt Ton von WAMO (Füllwort oder Antwort)
        self._mhm_zuletzt = float("-inf")
        self._waechter: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            if self._sprechen is not None:
                self._bank.im_hintergrund_vorbereiten(self._sprechen)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._wamo_spricht = True
            self._sprach_im_zug = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._wamo_spricht = False
            self._ton_bis = max(self._ton_bis, self._uhr())
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._person_spricht = True
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._person_spricht = False
        elif direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame)):
                await self._neuer_zug()
            elif isinstance(frame, LLMFullResponseStartFrame):
                self._text_in_antwort = False
            elif isinstance(frame, LLMTextFrame) and frame.text.strip():
                self._text_in_antwort = True
            elif isinstance(frame, FunctionCallsStartedFrame):
                self._werkzeuge_gestartet([c.tool_call_id for c in frame.function_calls])
            elif isinstance(frame, FunctionCallInProgressFrame):
                self._werkzeuge_gestartet([frame.tool_call_id])
            elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
                self._werkzeuge.discard(frame.tool_call_id)
            elif isinstance(frame, LLMMarkerFrame) and frame.marker == MARKER_UNFERTIG_KURZ:
                await self.vielleicht_mhm()

        await self.push_frame(frame, direction)

    async def _neuer_zug(self) -> None:
        await self._waechter_beenden()
        self._sprach_im_zug = False
        self._text_in_antwort = False
        self._werkzeuge.clear()
        self._werkzeug_seit = None
        self._gespielt_kurz = False
        self._gespielt_lang = False

    def _werkzeuge_gestartet(self, kennungen: list[str]) -> None:
        self._werkzeuge.update(k for k in kennungen if k)
        if self._werkzeug and self._werkzeug_seit is None:
            self._werkzeug_seit = self._uhr()
            self._waechter = self.create_task(self._werkzeuge_beobachten(), name="fuellwort")

    def soll_fuellen(self) -> bool:
        """Erstes Füllwort: noch nichts gesagt, nichts unterwegs, niemand spricht."""
        return (
            self._bank.bereit
            and not self._gespielt_kurz
            and not self._wamo_spricht
            and not self._person_spricht
            and not self._text_in_antwort
            and not self._sprach_im_zug
        )

    def soll_lang_sagen(self) -> bool:
        """Zweites Füllwort: das Werkzeug läuft noch und es ist seit lang_secs still."""
        seit = self._werkzeug_seit or self._uhr()
        still_seit = max(self._ton_bis, seit)
        return (
            self._bank.bereit
            and not self._gespielt_lang
            and bool(self._werkzeuge)
            and not self._wamo_spricht
            and not self._person_spricht
            and self._uhr() - still_seit >= self._lang
        )

    async def _werkzeuge_beobachten(self) -> None:
        try:
            await asyncio.sleep(self._nach)
            if self.soll_fuellen():
                self._gespielt_kurz = await self._spielen(WERKZEUG_SAETZE, "Werkzeug")
            while self._werkzeuge and not self._gespielt_lang:
                await asyncio.sleep(0.25)
                if self.soll_lang_sagen():
                    self._gespielt_lang = await self._spielen(LANG_SAETZE, "Werkzeug dauert")
        finally:
            self._waechter = None

    async def vielleicht_mhm(self) -> None:
        if not self._mhm or not self._bank.bereit:
            return
        jetzt = self._uhr()
        if self._person_spricht or self._wamo_spricht or jetzt - self._mhm_zuletzt < self._mhm_ruhe:
            return
        if self._zufall() > self._mhm_anteil:
            return
        self._mhm_zuletzt = jetzt
        await self._spielen(MHM_SAETZE, "Rückmeldung")

    async def _spielen(self, saetze: Sequence[str], anlass: str) -> bool:
        gewaehlt = self._bank.waehlen(saetze)
        if not gewaehlt:
            return False
        text, ton = gewaehlt
        await self.push_frame(OutputAudioRawFrame(audio=ton, sample_rate=self._bank.abtastrate, num_channels=1))
        self._ton_bis = self._uhr() + len(ton) / (2 * self._bank.abtastrate)
        logger.info("{}: Füllwort ({}): {!r}", self, anlass, text)
        return True

    async def _waechter_beenden(self) -> None:
        if self._waechter:
            await self.cancel_task(self._waechter)
            self._waechter = None

    async def cleanup(self):
        await super().cleanup()
        await self._waechter_beenden()
