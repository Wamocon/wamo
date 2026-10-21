"""Prompt-Cache von Claude beim Sitzungsstart vorwärmen (WAMO_CACHE_VORWAERMEN, Standard an).

Problem: Systemprompt und Werkzeuge sind etwa 9000 Token lang. Claude liest sie aus dem Prompt-Cache (5 Minuten
Lebensdauer), aber die erste Frage einer Sitzung trifft meist einen kalten Cache und schreibt ihn erst: Gemessen am
21.10.2026 kam der erste Ton der ersten Frage 100 bis 1100 ms später als bei den folgenden (Zug 1 p50 2,0 bis 2,3 s
gegenüber 1,3 s).

Lösung: Während die Begrüßung läuft (ein bis zwei Sekunden), schickt der Kern eine winzige Anfrage (max_tokens=1) mit
genau demselben Präfix (Werkzeuge, Systemprompt) und einer Cache-Marke am Ende des Systemprompts. Die erste echte
Frage findet dann den Präfix im Cache. Kosten: ein Schreiben des Caches, das sonst die erste Frage zahlt.

Wichtig: Die Wärm-Anfrage muss alle Parameter des Gesprächswegs teilen, die den Cache-Schlüssel bestimmen (gemessen:
ohne den Beta-Weg und die abgeschaltete Denkphase gibt es keinen Treffer). Sie wird deshalb wie
AnthropicLLMService.run_inference gebaut, nur mit max_tokens=1 und der Cache-Marke am System. Läuft die Sitzung über ein
Hausmodell, passiert nichts. Jeder Fehler wird nur protokolliert, nie an die Person gegeben.
"""

from __future__ import annotations

import asyncio
import os
import time

from loguru import logger

from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.anthropic.llm import AnthropicLLMService, _apply_sampling_settings
from pipecat.utils.types import assert_given

WARM_TIMEOUT_SECS = 15.0
BETAS = ["interleaved-thinking-2025-05-14"]  # wie der Gesprächsweg in pipecat (anthropic/llm.py)


def vorwaermen_an(wert: str | None = None) -> bool:
    w = os.getenv("WAMO_CACHE_VORWAERMEN") if wert is None else wert
    return (w or "an").strip().lower() not in ("aus", "0", "nein", "false")


def mit_cache_marke(system: object) -> object:
    """System als Textblock mit Cache-Marke am Ende (der Cache-Eintrag liegt dann genau hinter Werkzeugen und
    Systemprompt, wo jede echte Anfrage der Sitzung ihren gemeinsamen Anfang hat)."""
    if isinstance(system, str):
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if isinstance(system, list) and system and isinstance(system[-1], dict):
        blocks = [dict(b) if isinstance(b, dict) else b for b in system]
        blocks[-1]["cache_control"] = {"type": "ephemeral"}
        return blocks
    return system


def anfrage_parameter(llm: AnthropicLLMService, tools) -> dict | None:
    """Parameter der Wärm-Anfrage oder None, wenn nichts zu wärmen ist (kein Systemprompt)."""
    kontext = LLMContext(messages=[{"role": "user", "content": "Hallo."}], tools=tools)
    params = llm.get_llm_adapter().get_llm_invocation_params(
        kontext,
        enable_prompt_caching=assert_given(llm._settings.enable_prompt_caching),
        system_instruction=assert_given(llm._settings.system_instruction),
        ensure_last_message_is_user=llm._should_inject_trailing_user_message(),
    )
    system = params["system"]
    if not isinstance(system, (str, list)) or not system:
        return None
    anfrage = {
        "model": llm._settings.model,
        "max_tokens": 1,
        "stream": False,
        "messages": params["messages"],
        "system": mit_cache_marke(system),
        "tools": params["tools"],
        "betas": list(BETAS),
    }
    thinking = assert_given(llm._settings.thinking)
    if thinking:
        anfrage["thinking"] = thinking.model_dump(exclude_unset=True)
    anfrage.update(llm._settings.extra)
    _apply_sampling_settings(anfrage, llm._settings)
    llm._maybe_disable_thinking(anfrage)
    return anfrage


async def vorwaermen(llm: object, tools) -> dict | None:
    """Wärm-Anfrage senden. Ergebnis: Token-Zähler (cache_schreiben, cache_lesen) oder None."""
    if not vorwaermen_an() or not isinstance(llm, AnthropicLLMService):
        return None
    start = time.monotonic()
    try:
        params = anfrage_parameter(llm, tools)
        if params is None:
            return None
        antwort = await asyncio.wait_for(llm._client.beta.messages.create(**params), WARM_TIMEOUT_SECS)
        u = antwort.usage
        zaehler = {"cache_schreiben": getattr(u, "cache_creation_input_tokens", 0) or 0,
                   "cache_lesen": getattr(u, "cache_read_input_tokens", 0) or 0}
        logger.info("Prompt-Cache vorgewärmt in {:.0f} ms (geschrieben {}, gelesen {} Token)",
                    (time.monotonic() - start) * 1000, zaehler["cache_schreiben"], zaehler["cache_lesen"])
        return zaehler
    except Exception as e:  # Wärmen ist nur eine Beschleunigung, nie ein Grund für einen Fehler
        logger.warning("Prompt-Cache nicht vorgewärmt ({})", type(e).__name__)
        return None
