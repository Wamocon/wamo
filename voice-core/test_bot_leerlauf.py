"""Leerlauf einer Sitzung (bot.py, IDLE_FRAMES): Eine Sitzung nur mit Text lebt, solange geschrieben und geantwortet wird.

Fehler vom 12.11.2026: Pipecat beendet eine Pipeline nach 300 Sekunden ohne ein Lebenszeichen (Sprache). Eine Sitzung ohne Sprache (stiller Start
beim Tippen) hat nie eines, sie wurde also fünf Minuten nach dem Start beendet, auch mitten im Schreiben (Journal 08:49:34 und 08:55:08, mit
Antworten des Modells davor). Der Test prüft die Verdrahtung in bot.py und, mit der Beobachterklasse von Pipecat, welche Frames als Lebenszeichen gelten.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames import frames as pf
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.worker import IdleFrameObserver

BOT = Path(__file__).with_name("bot.py")
BAUM = ast.parse(BOT.read_text(encoding="utf-8"))


def _idle_frames() -> tuple[type, ...]:
    for knoten in ast.walk(BAUM):
        if isinstance(knoten, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "IDLE_FRAMES" for t in knoten.targets):
            return tuple(getattr(pf, e.id) for e in knoten.value.elts)
    raise AssertionError("IDLE_FRAMES fehlt in bot.py")


IDLE_FRAMES = _idle_frames()


def test_bot_uebergibt_die_frames_an_die_pipeline():
    aufrufe = [k for k in ast.walk(BAUM) if isinstance(k, ast.Call) and isinstance(k.func, ast.Name) and k.func.id == "PipelineTask"]
    assert len(aufrufe) == 1
    stichworte = {k.arg: k.value for k in aufrufe[0].keywords}
    assert isinstance(stichworte["idle_timeout_frames"], ast.Name) and stichworte["idle_timeout_frames"].id == "IDLE_FRAMES"


def test_der_standard_von_pipecat_bleibt_enthalten():
    standard = inspect.signature(PipelineTask.__init__).parameters["idle_timeout_frames"].default
    fehlend = [f.__name__ for f in standard if f not in IDLE_FRAMES]
    assert not fehlend, f"Pipecat zählt diese Frames als Lebenszeichen, bot.py nicht mehr: {fehlend}"


@pytest.mark.parametrize("name", ["InputTextRawFrame", "LLMMessagesAppendFrame", "LLMFullResponseStartFrame", "LLMFullResponseEndFrame",
                                  "FunctionCallInProgressFrame", "FunctionCallResultFrame"])
def test_text_antworten_und_werkzeuge_zaehlen_als_lebenszeichen(name):
    assert getattr(pf, name) in IDLE_FRAMES


async def _zaehlt_als_lebenszeichen(frames, frame) -> bool:
    """Setzt die Beobachtung von Pipecat (IdleFrameObserver, dieselbe Klasse wie in der Pipeline) das Ereignis für diesen Frame?"""
    ereignis = asyncio.Event()
    beobachter = IdleFrameObserver(idle_event=ereignis, idle_timeout_frames=frames)
    await beobachter.on_push_frame(SimpleNamespace(frame=frame))
    return ereignis.is_set()


STANDARD = inspect.signature(PipelineTask.__init__).parameters["idle_timeout_frames"].default


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", [pf.LLMFullResponseStartFrame(), pf.LLMFullResponseEndFrame(), pf.InputTextRawFrame(text="Hallo"),
                                   pf.LLMMessagesAppendFrame(messages=[])])
async def test_text_und_antworten_halten_die_sitzung_am_leben(frame):
    assert await _zaehlt_als_lebenszeichen(IDLE_FRAMES, frame) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", [pf.LLMFullResponseStartFrame(), pf.LLMFullResponseEndFrame(), pf.InputTextRawFrame(text="Hallo")])
async def test_gegenprobe_der_pipecat_standard_zaehlt_text_nicht(frame):
    assert await _zaehlt_als_lebenszeichen(STANDARD, frame) is False  # der alte Fehler: Text allein hielt die Sitzung nicht am Leben


@pytest.mark.asyncio
async def test_sprache_zaehlt_weiter_und_beliebige_frames_nicht():
    assert await _zaehlt_als_lebenszeichen(IDLE_FRAMES, pf.UserStartedSpeakingFrame()) is True
    assert await _zaehlt_als_lebenszeichen(IDLE_FRAMES, pf.TranscriptionFrame(text="x", user_id="u", timestamp="t")) is True
    assert await _zaehlt_als_lebenszeichen(IDLE_FRAMES, pf.TTSSpeakFrame(text="x")) is False  # nicht jeder Frame ist ein Lebenszeichen
