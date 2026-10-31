"""Tests für den Marker-Fallback (marker_wache.py) mit dem echten Claude-Dienst (ohne Netz): Antworten ohne ●, wie am
31.10.2026 um 12:44:36 nach der Werkzeugrunde, werden sofort freigegeben statt erst am Ende der Antwort."""

from __future__ import annotations

from unittest import mock

import pytest

import llm_anbieter as la
from marker_wache import MARKER_MIN_ZEICHEN, marker_fallback_an
from pipecat.frames.frames import LLMMarkerFrame, LLMTextFrame
from pipecat.turns.user_turn_completion_mixin import UserTurnCompletionConfig

USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"


def dienst():
    """Echter Claude-Dienst der Fabrik; Frames werden aufgezeichnet statt in eine Pipeline geschoben."""
    zugang = la.Zugang(USER, "claude", "profil", claude_schluessel="sk-ant-api03-PROFILeins-0123456789abcdefghijkl")
    d = la.gespraech_dienst(la.Wahl("gespraech", "claude", "claude-haiku-5-5", "claude"), "sys", zugang)
    frames: list = []

    async def push(frame, direction=None):
        frames.append(frame)

    d.push_frame = push
    d.broadcast_frame = mock.AsyncMock()
    d._start_incomplete_timeout = mock.AsyncMock()  # braucht sonst den Task-Manager einer laufenden Pipeline
    d.set_user_turn_completion_config(UserTurnCompletionConfig())
    return d, frames


def texte(frames) -> list[str]:
    return [f.text for f in frames if isinstance(f, LLMTextFrame)]


def test_schalter_standard_an():
    assert marker_fallback_an("")
    assert not marker_fallback_an("aus")


async def test_text_ohne_marker_wird_sofort_freigegeben():
    d, frames = dienst()
    await d._push_turn_text("Die ")
    assert texte(frames) == []  # unter MARKER_MIN_ZEICHEN: noch offen
    await d._push_turn_text("Seite ist offen.")
    assert "".join(texte(frames)) == "Die Seite ist offen."  # ohne Warten auf das Antwortende, ohne Textverlust
    assert any(isinstance(f, LLMMarkerFrame) and f.marker == "●" for f in frames)
    assert d._wamo_marker_ergaenzt == 1


async def test_ohne_fallback_wird_der_text_bis_zum_ende_zurueckgehalten(monkeypatch):
    monkeypatch.setenv("WAMO_MARKER_FALLBACK", "aus")
    d, frames = dienst()
    await d._push_turn_text("Die Seite ")
    await d._push_turn_text("ist offen.")
    assert texte(frames) == []  # Pipecat wie bisher: erst am Ende der Antwort (_turn_reset)
    await d._turn_reset()
    assert "".join(texte(frames)) == "Die Seite ist offen."


async def test_antwort_mit_marker_bleibt_unberuehrt():
    d, frames = dienst()
    await d._push_turn_text("● ")
    await d._push_turn_text("Die Seite ist offen.")
    assert "".join(texte(frames)) == "Die Seite ist offen."
    assert d._wamo_marker_ergaenzt == 0


async def test_unvollstaendig_marker_wird_nie_zu_vollstaendig():
    d, frames = dienst()
    await d._push_turn_text("◐")
    await d._push_turn_text(" Ich warte noch")
    assert texte(frames) == []
    assert d._wamo_marker_ergaenzt == 0


async def test_kurzer_anfang_wartet_auf_mehr_text():
    d, frames = dienst()
    await d._push_turn_text("Ja")
    assert len("Ja") < MARKER_MIN_ZEICHEN and texte(frames) == []
    await d._push_turn_text("wohl.")
    assert "".join(texte(frames)) == "Jawohl."  # Text steht dann vollständig


async def test_antwort_nach_gesprochenem_marker_im_selben_zug_wird_weiter_verworfen():
    """Pipecat verwirft eine zweite Antwort im selben Redebeitrag mit Absicht: der Wächter mischt sich nicht ein."""
    d, frames = dienst()
    d._user_turn_completion_voiced = True
    await d._push_turn_text("Noch eine Antwort ohne Marker.")
    assert texte(frames) == [] and d._wamo_marker_ergaenzt == 0


async def test_mehrere_antworten_nacheinander():
    d, frames = dienst()
    await d._push_turn_text("Erste Antwort.")
    await d._turn_reset()  # Antwortende
    d._user_turn_completion_voiced = False  # ein Werkzeugaufruf zwischen den Antworten setzt das zurück (Pipecat)
    frames.clear()
    await d._push_turn_text("Zweite Antwort.")
    assert "".join(texte(frames)).startswith("Zweite")
    assert d._wamo_marker_ergaenzt == 2


@pytest.mark.parametrize("klasse", ["claude", "litellm"])
def test_beide_dienste_haben_den_wachter(klasse):
    kl = la.ClaudeService if klasse == "claude" else la.LiteLLMService
    assert kl._push_turn_text.__qualname__.startswith("MarkerFallback")
