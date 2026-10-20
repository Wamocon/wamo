"""Messung am Stream und Sammeln der Modellausgaben für den Testsatz (eval/abnahme.py).

Gemessen wird direkt am Stream des Anbieters, ab dem Absenden der Anfrage:
  erstes Token     erste Text- oder Werkzeug-Ausgabe (bei Text meist der Marker ●)
  erster Text      erster gesprochener Text nach dem Marker (das hört die Person zuerst)
  Tokens/s         Ausgabe-Tokens ohne Denkphase und ohne das erste Token, geteilt durch die Zeit vom
                   ersten bis zum letzten Token; nur Antworten ab 30 Tokens (kürzere sind Netz-Rauschen)

Die Messung hängt sich an zwei interne Methoden von Pipecat 1.12 (AnthropicLLMService._create_message_stream,
BaseOpenAILLMService.get_chat_completions). Fehlen sie nach einem Update, bricht messen() mit einer klaren
Meldung ab, statt still nichts zu messen.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from loguru import logger

from pipecat.frames.frames import (
    ErrorFrame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMarkerResponseFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection

import bewertung

MIN_TPS_TOKENS = 30
MAX_ANTWORTEN = 6  # Schutz vor Werkzeug-Schleifen
_MARKER = "".join(bewertung.ALLE_MARKER)


@dataclass
class Messung:
    """Zeiten und Tokens einer Modellanfrage, direkt am Stream gemessen."""

    t0: float
    t_erst: float | None = None  # erste Text- oder Werkzeug-Ausgabe
    t_text: float | None = None  # erster gesprochener Text nach dem Marker
    t_letzt: float | None = None
    t_denken: float | None = None  # erste Denk-Ausgabe (Modelle mit Denkphase)
    eingabe: int = 0
    ausgabe: int = 0
    cache_lesen: int = 0
    cache_schreiben: int = 0
    denken_tokens: int = 0
    modell: str = ""  # vom Anbieter gemeldet
    fehler: int = 0  # Messfehler (die Antwort selbst läuft weiter)
    _text: str = ""

    def ausgabe_jetzt(self, text: str = "") -> None:
        jetzt = time.perf_counter()
        self.t_erst = self.t_erst or jetzt
        self.t_letzt = jetzt
        if text and self.t_text is None:
            self._text += text
            if self._text.strip().strip(_MARKER).strip():
                self.t_text = jetzt

    @property
    def ttft(self) -> float | None:
        return None if self.t_erst is None else self.t_erst - self.t0

    @property
    def ttfat(self) -> float | None:
        return None if self.t_text is None else self.t_text - self.t0

    @property
    def gen(self) -> tuple[int, float]:
        """(Tokens, Sekunden) für Tokens pro Sekunde; (0, 0) bei zu kurzer Antwort."""
        tokens = self.ausgabe - self.denken_tokens - 1
        if self.t_erst is None or self.t_letzt is None or tokens < MIN_TPS_TOKENS:
            return 0, 0.0
        dauer = self.t_letzt - self.t_erst
        return (tokens, dauer) if dauer > 0.05 else (0, 0.0)


class GemessenerStrom:
    """Reicht den Stream unverändert durch und notiert dabei Zeiten, Tokens und das bediente Modell."""

    def __init__(self, strom, messung: Messung, art: str):
        self._strom = strom
        self._m = messung
        self._art = art

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        async for teil in self._strom:
            try:
                (self.anthropic if self._art == "claude" else self.openai)(teil)
            except Exception as e:  # Messung darf das Gespräch nie stören; gezählt und gemeldet wird sie
                self._m.fehler += 1
                logger.warning("Messung: {}", type(e).__name__)
            yield teil

    def openai(self, chunk) -> None:
        if model := getattr(chunk, "model", None):
            self._m.modell = model
        if usage := getattr(chunk, "usage", None):
            self._m.eingabe = usage.prompt_tokens or 0
            self._m.ausgabe = usage.completion_tokens or 0
            if (details := getattr(usage, "completion_tokens_details", None)) and details.reasoning_tokens:
                self._m.denken_tokens = details.reasoning_tokens
            if (details := getattr(usage, "prompt_tokens_details", None)) and details.cached_tokens:
                self._m.cache_lesen = details.cached_tokens
        if not chunk.choices or not chunk.choices[0].delta:
            return
        delta = chunk.choices[0].delta
        if delta.content or delta.tool_calls:
            self._m.ausgabe_jetzt(delta.content or "")
        elif getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None):
            self._m.t_denken = self._m.t_denken or time.perf_counter()

    def anthropic(self, event) -> None:
        if event.type == "message_start":
            self._m.modell = getattr(event.message, "model", "") or ""
            u = event.message.usage
            self._m.eingabe = u.input_tokens or 0
            self._m.cache_lesen = getattr(u, "cache_read_input_tokens", 0) or 0
            self._m.cache_schreiben = getattr(u, "cache_creation_input_tokens", 0) or 0
            self._m.ausgabe = u.output_tokens or 0
        elif event.type == "content_block_start":
            if event.content_block.type == "tool_use":
                self._m.ausgabe_jetzt()
            elif "thinking" in event.content_block.type:
                self._m.t_denken = self._m.t_denken or time.perf_counter()
        elif event.type == "content_block_delta":
            d = event.delta
            if d.type == "text_delta" and d.text:
                self._m.ausgabe_jetzt(d.text)
            elif d.type == "input_json_delta":
                self._m.ausgabe_jetzt()
            elif "thinking" in d.type:
                self._m.t_denken = self._m.t_denken or time.perf_counter()
        elif event.type == "message_delta" and getattr(event, "usage", None):
            self._m.ausgabe = event.usage.output_tokens or self._m.ausgabe

    async def close(self):
        for name in ("close", "aclose"):
            if callable(fn := getattr(self._strom, name, None)):
                await fn()
                return

    def __getattr__(self, name):
        return getattr(self._strom, name)


def messen(llm, art: str, messungen: list[Messung]) -> None:
    """Hängt die Messung an den Stream des Dienstes, ohne den Dienst selbst zu ändern."""
    methode = "_create_message_stream" if art == "claude" else "get_chat_completions"
    original = getattr(llm, methode, None)
    if not callable(original):
        raise RuntimeError(
            f"Pipecat hat {type(llm).__name__}.{methode} nicht mehr: Messung in eval/messung.py anpassen"
        )

    if art == "claude":
        async def gemessen(api_call, params):
            m = Messung(time.perf_counter())
            strom = await original(api_call, params)
            messungen.append(m)
            return GemessenerStrom(strom, m, art)
    else:
        async def gemessen(context):  # type: ignore[misc]
            m = Messung(time.perf_counter())
            strom = await original(context)
            messungen.append(m)
            return GemessenerStrom(strom, m, art)

    setattr(llm, methode, gemessen)


class Sammler(BaseObserver):
    """Sammelt, was das Sprachmodell in einem Zug ausgibt: Rohtext mit Marker und Werkzeugaufrufe."""

    def __init__(self, llm):
        super().__init__(observe_every_push=False)
        self._llm = llm
        self.fall = None
        self.antworten: list[bewertung.Antwortteil] = []
        self.fehler: list[str] = []
        self.fertig = asyncio.Event()

    def neuer_fall(self, fall) -> None:
        self.fall = fall
        self.antworten = []
        self.fehler = []
        self.fertig = asyncio.Event()

    def abbrechen(self, grund: str) -> None:
        """Von außen, z. B. wenn die Pipeline endet: der wartende Zug hört sofort auf."""
        self.fehler.append(grund)
        self.fertig.set()

    async def on_push_frame(self, data: FramePushed):
        if self.fall is None or data.source is not self._llm:
            return
        f = data.frame
        if isinstance(f, ErrorFrame):
            self.abbrechen(bewertung.maskieren(f"Fehler: {str(f.error)[:160]}"))
        elif isinstance(f, LLMFullResponseStartFrame):
            self.antworten.append(bewertung.Antwortteil())
        elif not self.antworten:
            return
        elif isinstance(f, LLMMarkerResponseFrame):
            self.antworten[-1].raw = f.raw or ""
        elif isinstance(f, FunctionCallsStartedFrame) and data.direction == FrameDirection.DOWNSTREAM:
            # Pipecat schickt den Rahmen in beide Richtungen; gezählt wird er nur einmal.
            for fc in f.function_calls:
                self.antworten[-1].aufrufe.append((fc.function_name, dict(fc.arguments or {})))
        elif isinstance(f, LLMFullResponseEndFrame):
            if not self.antworten[-1].aufrufe or len(self.antworten) >= MAX_ANTWORTEN:
                self.fertig.set()
