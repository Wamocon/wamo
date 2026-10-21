"""Spracherkennung (STT) und Sprachausgabe (TTS) je Sitzung: Soniox (Standard) oder im Haus über LiteLLM (Phase P5).

Wahl je Baustein, in dieser Reihenfolge:
  1. WAMO_STT_ANBIETER bzw. WAMO_TTS_ANBIETER (soniox | haus), wenn gesetzt: gilt für alle Sitzungen dieses Kerns,
     z. B. für Tests oder um bei einer Störung im Haus alle auf Soniox zu stellen.
  2. die Wahl des Profils (Modellzugang, Felder stt und tts; Profile ohne Claude-Konto laufen später ganz im Haus).
  3. sonst soniox.

Im Haus geht alles über das LiteLLM-Gateway (LITELLM_BASE_URL, LITELLM_API_KEY), nie direkt an die Knoten:
  * STT: /v1/audio/transcriptions mit WAMO_STT_HAUS_MODELL (OpenAI-kompatibel, ganze Äußerung nach dem Sprechende,
    keine Zwischenergebnisse, keine Sprechertrennung, keine Wortzeitstempel).
  * TTS: /v1/audio/speech mit WAMO_TTS_HAUS_MODELL und WAMO_TTS_HAUS_STIMME, Antwort als WAV (Abtastrate aus dem Kopf,
    wird auf die Ausgaberate umgerechnet). Ohne Wortzeitstempel: Die weiche Unterbrechung greift dann über die
    Zeitgrenze in soft_interrupt.py, die Untertitel kommen satzweise.

Rückfall: Fehlt Modell, Schlüssel oder Adresse, ist das Gateway nicht erreichbar oder bietet es das Modell nicht an,
steht der Grund im Log und der Baustein nutzt Soniox. Die Oberfläche zeigt das am Namen ("Soniox (Rückfall)").
Stand 21.10.2026: Das Gateway bietet noch kein Audiomodell an; "haus" fällt deshalb heute immer auf Soniox zurück.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language

from llm_anbieter import litellm_basis, litellm_problem

ANBIETER = ("soniox", "haus")
HAUS_TIMEOUT_SECS = 20.0  # eine Äußerung bzw. ein Satz; danach Fehler statt Hänger


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def gewuenscht(baustein: str, profil_wert: str | None = None) -> str:
    """Gewünschter Anbieter für "stt" oder "tts": Umgebung vor Profil vor Standard (soniox)."""
    umgebung = _env(f"WAMO_{baustein.upper()}_ANBIETER").lower()
    if umgebung in ANBIETER:
        return umgebung
    if umgebung:
        logger.warning("WAMO_{}_ANBIETER={!r} unbekannt (soniox oder haus), nutze die Wahl des Profils", baustein.upper(), umgebung)
    wert = (profil_wert or "").strip().lower()
    return wert if wert in ANBIETER else "soniox"


@dataclass(frozen=True)
class Baustein:
    gewuenscht: str  # soniox | haus
    anbieter: str  # wirksam: soniox | haus
    modell: str | None = None
    problem: str | None = None  # Grund für den Rückfall

    @property
    def rueckfall(self) -> bool:
        return self.gewuenscht == "haus" and self.anbieter == "soniox"


@dataclass(frozen=True)
class SprachWahl:
    stt: Baustein
    tts: Baustein

    def anzeige(self, soniox_stimme: str) -> dict[str, str]:
        """Namen für die Oberfläche (betrieb.modelle.stt/tts)."""
        def name(b: Baustein, soniox: str) -> str:
            if b.anbieter == "haus":
                return f"Haus ({b.modell})"
            return f"{soniox} (Rückfall)" if b.rueckfall else soniox
        return {"stt": name(self.stt, "Soniox"), "tts": name(self.tts, f"Soniox {soniox_stimme}")}

    @property
    def im_haus(self) -> bool:
        return self.stt.anbieter == "haus" and self.tts.anbieter == "haus"


async def _baustein(baustein: str, profil_wert: str | None) -> Baustein:
    wunsch = gewuenscht(baustein, profil_wert)
    if wunsch == "soniox":
        return Baustein("soniox", "soniox")
    modell = _env(f"WAMO_{baustein.upper()}_HAUS_MODELL")
    problem = None if modell else f"WAMO_{baustein.upper()}_HAUS_MODELL fehlt"
    if not problem and baustein == "tts" and not _env("WAMO_TTS_HAUS_STIMME"):
        problem = "WAMO_TTS_HAUS_STIMME fehlt"
    if not problem:
        problem = await litellm_problem(modell)
    if problem:
        logger.error("{} im Haus nicht nutzbar ({}), Rückfall auf Soniox", baustein.upper(), problem)
        return Baustein("haus", "soniox", modell or None, problem)
    logger.info("{} im Haus über LiteLLM: {}", baustein.upper(), modell)
    return Baustein("haus", "haus", modell)


async def sprach_wahl(zugang: object | None = None) -> SprachWahl:
    """Wahl für eine Sitzung. zugang: Modellzugang des Profils (Attribute stt und tts), fehlt er, gilt die Umgebung."""
    return SprachWahl(
        stt=await _baustein("stt", getattr(zugang, "stt", None)),
        tts=await _baustein("tts", getattr(zugang, "tts", None)),
    )


def haus_stt(b: Baustein, begriffe: list[str]) -> OpenAISTTService:
    """STT im Haus: OpenAI-kompatibel über LiteLLM; Fachbegriffe als Hinweis (prompt) wie bei Whisper."""
    return OpenAISTTService(
        api_key=_env("LITELLM_API_KEY"),
        base_url=litellm_basis(),
        settings=OpenAISTTService.Settings(
            model=b.modell,
            language=Language.DE,
            prompt=", ".join(begriffe) if begriffe else None,
        ),
    )


class HausTTSService(TTSService):
    """TTS im Haus über LiteLLM (/v1/audio/speech, WAV). Satzweise, ohne Wortzeitstempel."""

    def __init__(self, *, modell: str, stimme: str, basis: str | None = None, schluessel: str | None = None,
                 client: httpx.AsyncClient | None = None, **kwargs):
        super().__init__(push_text_frames=True, push_start_frame=True, push_stop_frames=True, **kwargs)
        self._modell = modell
        self._stimme = stimme
        self._basis = (basis or litellm_basis()).rstrip("/")
        self._schluessel = schluessel if schluessel is not None else _env("LITELLM_API_KEY")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(HAUS_TIMEOUT_SECS, connect=3.0))

    def can_generate_metrics(self) -> bool:
        return True

    async def cleanup(self):
        await super().cleanup()
        await self._client.aclose()

    async def run_tts(self, text: str, context_id: str) -> AsyncIterator[Frame | None]:
        anfrage = {"model": self._modell, "input": text, "voice": self._stimme, "response_format": "wav"}
        kopf = {"Authorization": f"Bearer {self._schluessel}"}
        try:
            async with self._client.stream("POST", f"{self._basis}/audio/speech", json=anfrage, headers=kopf) as r:
                if r.status_code != 200:
                    await r.aread()
                    logger.error("{}: Sprachausgabe im Haus fehlgeschlagen (HTTP {})", self, r.status_code)
                    yield ErrorFrame(error=f"Sprachausgabe im Haus: HTTP {r.status_code}")
                    return
                await self.start_tts_usage_metrics(text)
                async for frame in self._stream_audio_frames_from_iterator(
                    r.aiter_bytes(), strip_wav_header=True, context_id=context_id
                ):
                    await self.stop_ttfb_metrics()
                    yield frame
        except httpx.HTTPError as e:
            logger.error("{}: Sprachausgabe im Haus nicht erreichbar ({})", self, type(e).__name__)
            yield ErrorFrame(error=f"Sprachausgabe im Haus nicht erreichbar ({type(e).__name__})")


def haus_tts(b: Baustein) -> HausTTSService:
    return HausTTSService(modell=b.modell or "", stimme=_env("WAMO_TTS_HAUS_STIMME"))
