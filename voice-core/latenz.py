"""Latenz je Gesprächszug, vom Sprechende bis zur Ausgabe (WAMO_LATENZ_LOG=1, Standard aus).

Ein Beobachter (Pipecat-Observer) stempelt je Zug die Abschnitte mit der Wanduhr, alle relativ zum geschätzten
Sprechende (VAD-Entscheidung minus stop_secs):

  turn_ende   UserStoppedSpeakingFrame: Turn-Ende erkannt (VAD, Smart-Turn, ggf. Satzende-Prüfung des Modells)
  stt_final   letztes endgültiges Transkript vor der Anfrage an das Modell
  llm_start   Anfrage an das Modell (LLMFullResponseStartFrame)
  llm_token   erstes Text-Token des Modells (ohne Satzende-Marker)
  tts_satz    erster Satzteil an die Sprachausgabe (TTSStartedFrame)
  tts_audio   erstes Audio der Sprachausgabe
  ausgabe     BotStartedSpeakingFrame: der Kern beginnt, Ton zu senden (im Browser kommt Netz und Puffer dazu)

Je Zug eine Logzeile "Latenz Zug n: ..." und eine maschinenlesbare Zeile "LATENZ_JSON {...}" für Auswertungen
(p50/p95 je Abschnitt). Inhalte des Gesprächs stehen nicht darin, nur Zeiten, Werkzeugnamen und Zähler.
Zusätzlich wird gezählt, wie viele Antworten das Modell je Zug beginnt und welche Werkzeuge es aufruft; ruft es
dasselbe Werkzeug in einem Zug mehrfach auf, steht das als Warnung im Log (Hinweis auf eine doppelte Antwort).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallInProgressFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection

ABSCHNITTE = ("turn_ende", "stt_final", "llm_start", "llm_token", "tts_satz", "tts_audio", "ausgabe")


def latenz_log_an(wert: str | None = None) -> bool:
    w = os.getenv("WAMO_LATENZ_LOG") if wert is None else wert
    return (w or "").strip().lower() in ("1", "an", "ja", "true")


@dataclass
class Zug:
    nummer: int
    sprechende: float
    vad_stop_secs: float
    marken: dict[str, float] = field(default_factory=dict)
    antworten: int = 0
    werkzeuge: list[str] = field(default_factory=list)
    nachgesprochen: bool = False  # Person sprach nach dem Turn-Ende weiter, bevor WAMO antwortete
    # Token der ersten Modellantwort im Zug: eingabe (ohne Cache), cache_lesen, cache_schreiben
    token: dict[str, int] = field(default_factory=dict)

    def markiere(self, name: str, jetzt: float, *, ersetzen: bool = False) -> None:
        if ersetzen or name not in self.marken:
            self.marken[name] = jetzt

    def ms(self) -> dict[str, int]:
        return {k: round((self.marken[k] - self.sprechende) * 1000) for k in ABSCHNITTE if k in self.marken}

    def ergebnis(self) -> dict:
        d = asdict(self)
        d.pop("marken")
        d.pop("sprechende")
        d["ms"] = self.ms()
        return d


class LatenzObserver(BaseObserver):
    """Stempelt die Abschnitte je Zug und schreibt sie beim Beginn der Ausgabe ins Log."""

    def __init__(self, *, uhr: Callable[[], float] = time.time, melden: Callable[[Zug], None] | None = None, **kwargs):
        super().__init__(observe_every_push=False, **kwargs)
        self._uhr = uhr
        self._melden = melden or zug_loggen
        self._zug: Zug | None = None
        self._nummer = 0

    async def on_push_frame(self, data: FramePushed):
        if data.direction != FrameDirection.DOWNSTREAM:
            return
        f = data.frame
        zug = self._zug
        jetzt = self._uhr()
        if isinstance(f, VADUserStoppedSpeakingFrame):
            # Neuer Zug, oder die Person hat vor dem Turn-Ende weitergesprochen: dann gilt das spätere Sprechende
            if zug is None or "turn_ende" not in zug.marken or "ausgabe" in zug.marken:
                if zug is None or "turn_ende" in zug.marken:
                    self._nummer += 1
                self._zug = Zug(self._nummer, f.timestamp - f.stop_secs, f.stop_secs)
        elif zug is None:
            return
        elif isinstance(f, VADUserStartedSpeakingFrame):
            if "turn_ende" in zug.marken and "ausgabe" not in zug.marken:
                zug.nachgesprochen = True
        elif isinstance(f, UserStoppedSpeakingFrame):
            zug.markiere("turn_ende", jetzt)
        elif isinstance(f, TranscriptionFrame):
            if "llm_start" not in zug.marken:
                zug.markiere("stt_final", jetzt, ersetzen=True)
        elif isinstance(f, LLMFullResponseStartFrame):
            zug.antworten += 1
            zug.markiere("llm_start", jetzt)
        elif isinstance(f, LLMTextFrame):
            if f.text.strip(" ●◐○"):
                zug.markiere("llm_token", jetzt)
        elif isinstance(f, MetricsFrame):
            # Token kommen erst am Ende der Antwort, also meist nach dem Beginn der Ausgabe: eigene Logzeile
            for d in f.data:
                if isinstance(d, LLMUsageMetricsData) and not zug.token and "llm_start" in zug.marken:
                    u = d.value
                    zug.token = {"eingabe": u.prompt_tokens or 0, "cache_lesen": u.cache_read_input_tokens or 0,
                                 "cache_schreiben": u.cache_creation_input_tokens or 0}
                    logger.info("LATENZ_TOKEN {}", json.dumps({"nummer": zug.nummer, **zug.token}))
        elif isinstance(f, FunctionCallInProgressFrame):
            if f.function_name in zug.werkzeuge:
                logger.warning("Latenz Zug {}: Werkzeug {} im selben Zug erneut aufgerufen", zug.nummer, f.function_name)
            zug.werkzeuge.append(f.function_name)
        elif isinstance(f, TTSStartedFrame):
            if "llm_start" in zug.marken:
                zug.markiere("tts_satz", jetzt)
        elif isinstance(f, TTSAudioRawFrame):
            if "llm_start" in zug.marken:
                zug.markiere("tts_audio", jetzt)
        elif isinstance(f, BotStartedSpeakingFrame):
            if "llm_start" in zug.marken and "ausgabe" not in zug.marken:
                zug.markiere("ausgabe", jetzt)
                self._melden(zug)


def zug_loggen(zug: Zug) -> None:
    ms = zug.ms()
    teile = " | ".join(f"{k} {v:+d}" for k, v in ms.items())
    extra = []
    if zug.werkzeuge:
        extra.append("Werkzeuge " + ",".join(zug.werkzeuge))
    if zug.antworten > 1:
        extra.append(f"{zug.antworten} Antworten")
    if zug.nachgesprochen:
        extra.append("weitergesprochen")
    logger.info("Latenz Zug {} (ms ab Sprechende, VAD {:.0f} ms): {}{}", zug.nummer, zug.vad_stop_secs * 1000, teile,
                f" | {'; '.join(extra)}" if extra else "")
    logger.info("LATENZ_JSON {}", json.dumps(zug.ergebnis(), ensure_ascii=False))
