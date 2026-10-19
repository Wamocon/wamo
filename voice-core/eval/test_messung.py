"""Tests der Messung am Stream und des Sammlers (eval/messung.py) mit nachgebauten Stream-Ereignissen."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

import messung
from bewertung import Antwortteil
from messung import GemessenerStrom, Messung, Sammler
from pipecat.frames.frames import (
    ErrorFrame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMarkerResponseFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection


class _Uhr:
    def __init__(self, monkeypatch):
        self.t = 100.0
        monkeypatch.setattr(messung.time, "perf_counter", lambda: self.t)

    def weiter(self, sekunden: float) -> None:
        self.t += sekunden


async def _abspielen(ereignisse, art, uhr, schritte):
    async def strom():
        for e, s in zip(ereignisse, schritte, strict=True):
            uhr.weiter(s)
            yield e

    m = Messung(uhr.t)
    async for _ in GemessenerStrom(strom(), m, art):
        pass
    return m


def _chunk(content=None, tool_calls=None, reasoning=None, usage=None, model="qwen3.6:35b"):
    delta = NS(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    return NS(model=model, usage=usage, choices=[NS(delta=delta)])


async def test_openai_erstes_token_text_und_rate(monkeypatch):
    uhr = _Uhr(monkeypatch)
    usage = NS(prompt_tokens=900, completion_tokens=61, completion_tokens_details=NS(reasoning_tokens=0),
               prompt_tokens_details=NS(cached_tokens=800))
    ende = NS(model="qwen3.6:35b", usage=usage, choices=[])
    ereignisse = [_chunk("●"), _chunk(" Es"), _chunk(" ist halb drei."), ende]
    m = await _abspielen(ereignisse, "litellm", uhr, [0.4, 0.1, 0.5, 0.0])
    assert m.ttft == pytest.approx(0.4)  # der Marker ist das erste Token
    assert m.ttfat == pytest.approx(0.5)  # gesprochener Text erst danach
    assert (m.eingabe, m.ausgabe, m.cache_lesen, m.modell) == (900, 61, 800, "qwen3.6:35b")
    tokens, sekunden = m.gen
    assert tokens == 60 and sekunden == pytest.approx(0.6)


async def test_openai_denkphase_zaehlt_nicht_als_erstes_token(monkeypatch):
    uhr = _Uhr(monkeypatch)
    usage = NS(prompt_tokens=10, completion_tokens=200, completion_tokens_details=NS(reasoning_tokens=150),
               prompt_tokens_details=None)
    ereignisse = [_chunk(reasoning="hmm"), _chunk("● Ja, mache ich."), NS(model="m", usage=usage, choices=[])]
    m = await _abspielen(ereignisse, "litellm", uhr, [0.2, 1.8, 0.0])
    assert m.t_denken is not None and m.ttft == pytest.approx(2.0)
    assert m.gen == (0, 0.0)  # 200 - 150 - 1 = 49 Tokens, aber ohne messbare Dauer: keine Rate


async def test_openai_werkzeugaufruf_ist_ausgabe(monkeypatch):
    uhr = _Uhr(monkeypatch)
    m = await _abspielen([_chunk(tool_calls=[NS(index=0)])], "litellm", uhr, [0.3])
    assert m.ttft == pytest.approx(0.3) and m.ttfat is None


def _ev(typ, **kw):
    return NS(type=typ, **kw)


async def test_anthropic_ereignisse(monkeypatch):
    uhr = _Uhr(monkeypatch)
    usage = NS(input_tokens=50, output_tokens=1, cache_read_input_tokens=7000, cache_creation_input_tokens=0)
    ereignisse = [
        _ev("message_start", message=NS(model="claude-haiku-5-5", usage=usage)),
        _ev("content_block_start", content_block=NS(type="text")),
        _ev("content_block_delta", delta=NS(type="text_delta", text="●")),
        _ev("content_block_delta", delta=NS(type="text_delta", text=" Hallo")),
        _ev("content_block_start", content_block=NS(type="tool_use")),
        _ev("message_delta", usage=NS(output_tokens=42)),
    ]
    m = await _abspielen(ereignisse, "claude", uhr, [0.5, 0.1, 0.1, 0.1, 0.2, 0.0])
    assert m.ttft == pytest.approx(0.7) and m.ttfat == pytest.approx(0.8)
    assert (m.eingabe, m.cache_lesen, m.ausgabe, m.modell) == (50, 7000, 42, "claude-haiku-5-5")


async def test_messfehler_wird_gezaehlt_und_stoert_nicht(monkeypatch):
    uhr = _Uhr(monkeypatch)
    kaputt = NS(type="message_start", message=None)  # usage fehlt
    m = await _abspielen([kaputt, _ev("content_block_delta", delta=NS(type="text_delta", text="● ok"))],
                         "claude", uhr, [0.1, 0.1])
    assert m.fehler == 1 and m.ttft == pytest.approx(0.2)


def test_messen_meldet_fehlende_interne_methode():
    with pytest.raises(RuntimeError, match="_create_message_stream"):
        messung.messen(object(), "claude", [])


class _Llm:
    pass


def _push(sammler, frame, quelle, richtung=FrameDirection.DOWNSTREAM):
    return sammler.on_push_frame(FramePushed(source=quelle, destination=None, frame=frame, direction=richtung,
                                             timestamp=0))


async def test_sammler_zug_mit_werkzeug():
    llm = _Llm()
    s = Sammler(llm)
    s.neuer_fall(NS(id="z01"))
    aufruf = NS(function_name="aktuelle_zeit", arguments={})
    await _push(s, LLMFullResponseStartFrame(), llm)
    await _push(s, FunctionCallsStartedFrame(function_calls=[aufruf]), llm)
    await _push(s, FunctionCallsStartedFrame(function_calls=[aufruf]), llm, FrameDirection.UPSTREAM)  # Kopie
    await _push(s, LLMFullResponseEndFrame(), llm)
    assert not s.fertig.is_set()  # nach einem Werkzeug kommt noch eine Antwort
    await _push(s, LLMFullResponseStartFrame(), llm)
    await _push(s, LLMMarkerResponseFrame(raw="● Halb drei.", marker="●", kind="complete", markers=[]), llm)
    await _push(s, LLMFullResponseStartFrame(), _Llm())  # fremde Quelle zählt nicht
    await _push(s, LLMFullResponseEndFrame(), llm)
    assert s.fertig.is_set()
    assert s.antworten == [Antwortteil("", [("aktuelle_zeit", {})]), Antwortteil("● Halb drei.", [])]


async def test_sammler_fehler_beendet_zug_und_maskiert(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "geheim-999")
    llm = _Llm()
    s = Sammler(llm)
    s.neuer_fall(NS(id="x"))
    await _push(s, ErrorFrame(error="401 von http://192.168.178.75:4000 mit geheim-999"), llm, FrameDirection.UPSTREAM)
    assert s.fertig.is_set()
    assert "geheim-999" not in s.fehler[0] and "192.168" not in s.fehler[0]
