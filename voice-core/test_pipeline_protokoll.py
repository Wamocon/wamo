"""Protokoll der Sitzungsereignisse (pipeline_protokoll.py): welche Frames eine Logzeile bekommen, nie ein Inhalt des Gesprächs.

Anlass: Der Hänger im Chat vom 12.11.2026 hinterließ im Journal keine Zeile. Diese Zeilen sollen beim nächsten Mal zeigen, wo eine Nachricht stehen bleibt.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger
from pipecat.frames import frames as pf
from pipecat.processors.frame_processor import FrameDirection

from pipeline_protokoll import PipelineProtokoll, beschreibe

QUELLE = SimpleNamespace(name="LLMAssistantAggregator#0")
GEHEIM = "mein Passwort ist hunter2 und der Schlüssel sk-ant-abc123"
AB, AUF = FrameDirection.DOWNSTREAM, FrameDirection.UPSTREAM


def test_eingabe_zeigt_nur_die_laenge_nie_den_text():
    frame = pf.LLMMessagesAppendFrame(messages=[{"role": "user", "content": GEHEIM}], run_llm=True)
    zeile = beschreibe(frame, QUELLE, AB)
    assert zeile == f"Pipeline: Eingabe angekommen ({len(GEHEIM)} Zeichen, Modell sofort: True) von LLMAssistantAggregator#0"
    assert "hunter2" not in zeile and "sk-ant" not in zeile


def test_eingabe_ohne_inhalt_oder_mit_liste_bricht_nicht():
    assert "0 Zeichen" in beschreibe(pf.LLMMessagesAppendFrame(messages=[]), QUELLE, AB)
    assert "0 Zeichen" in beschreibe(pf.LLMMessagesAppendFrame(messages=[{"role": "user", "content": [{"type": "text"}]}]), QUELLE, AB)


def test_modell_beginn_und_ende():
    assert beschreibe(pf.LLMFullResponseStartFrame(), QUELLE, AB).startswith("Pipeline: Anfrage an das Modell beginnt")
    assert beschreibe(pf.LLMFullResponseEndFrame(), QUELLE, AB).startswith("Pipeline: Antwort des Modells beendet")


def test_werkzeuge_nur_mit_namen():
    laeuft = pf.FunctionCallInProgressFrame(function_name="buerogeraet_steuern", tool_call_id="t1", arguments={"geraet": "fernseher-2", "geheim": GEHEIM})
    zeile = beschreibe(laeuft, QUELLE, AB)
    assert zeile == "Pipeline: Werkzeug buerogeraet_steuern läuft"
    assert "hunter2" not in zeile
    fertig = pf.FunctionCallResultFrame(function_name="buerogeraet_steuern", tool_call_id="t1", arguments={}, result={"x": GEHEIM})
    zeile = beschreibe(fertig, QUELLE, AB)
    assert zeile == "Pipeline: Werkzeug buerogeraet_steuern fertig" and "hunter2" not in zeile


def test_unterbrechung_und_fehler():
    assert beschreibe(pf.InterruptionFrame(), QUELLE, AB).startswith("Pipeline: Unterbrechung")
    assert beschreibe(pf.ErrorFrame(error="Zeitüberschreitung beim Modell"), QUELLE, AB) == "Pipeline: Fehler von LLMAssistantAggregator#0: Zeitüberschreitung beim Modell"
    assert len(beschreibe(pf.ErrorFrame(error="x" * 1000), QUELLE, AB)) < 300


def test_nur_die_richtung_zum_ausgang_zaehlt_bei_nachricht_modell_und_unterbrechung():
    assert beschreibe(pf.LLMMessagesAppendFrame(messages=[]), QUELLE, AUF) is None
    assert beschreibe(pf.LLMFullResponseStartFrame(), QUELLE, AUF) is None
    assert beschreibe(pf.InterruptionFrame(), QUELLE, AUF) is None


@pytest.mark.parametrize("frame", [pf.TTSSpeakFrame(text=GEHEIM), pf.TextFrame(text=GEHEIM), pf.UserStartedSpeakingFrame(), pf.BotSpeakingFrame()])
def test_andere_frames_bleiben_still(frame):
    assert beschreibe(frame, QUELLE, AB) is None


def test_name_der_quelle_wird_begrenzt_und_fehlt_notfalls():
    zeile = beschreibe(pf.LLMFullResponseStartFrame(), SimpleNamespace(name="x" * 200), AB)
    assert len(zeile) < 100
    assert "object" in beschreibe(pf.LLMFullResponseStartFrame(), object(), AB)


@pytest.mark.asyncio
async def test_beobachter_schreibt_die_zeile_ins_journal_und_ignoriert_den_rest():
    zeilen: list[str] = []
    kennung = logger.add(lambda m: zeilen.append(str(m)), level="INFO")
    try:
        beobachter = PipelineProtokoll()
        await beobachter.on_push_frame(SimpleNamespace(frame=pf.LLMFullResponseStartFrame(), source=QUELLE, direction=AB))
        await beobachter.on_push_frame(SimpleNamespace(frame=pf.TTSSpeakFrame(text=GEHEIM), source=QUELLE, direction=AB))
    finally:
        logger.remove(kennung)
    assert len(zeilen) == 1 and "Pipeline: Anfrage an das Modell beginnt" in zeilen[0]
    assert "hunter2" not in "".join(zeilen)


def test_bot_haengt_den_beobachter_in_die_pipeline():
    baum = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    aufrufe = [k for k in ast.walk(baum) if isinstance(k, ast.Call) and isinstance(k.func, ast.Name) and k.func.id == "PipelineTask"]
    assert len(aufrufe) == 1
    beobachter = {k.arg: k.value for k in aufrufe[0].keywords}["observers"]
    namen = [n.func.id for n in ast.walk(beobachter) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "PipelineProtokoll" in namen
