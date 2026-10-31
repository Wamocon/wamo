"""Hausmodell im Hintergrund: kurzer Vorversuch und Pause (Befund 31.10.2026: lernen mit gemma4-31b lief zweimal in die
volle Zeitgrenze von 45 s, 12:44 und 12:47, erst danach übernahm Claude). Das Netz ist ersetzt, kein Test spricht mit
dem Gateway oder mit Anthropic."""

from __future__ import annotations

import pytest

import llm_anbieter as la
from test_llm_anbieter import _gateway, _hausmodelle, _liste, _litellm, _sauber, cz, hz  # noqa: F401 (Fixtures)


def _hintergrund(monkeypatch, modell="gemma4-31b"):
    """lernen auf einem Hausmodell; das Gateway kennt es. Ersetzt beide Anfragen und merkt sich Zeitgrenzen und Reihenfolge."""
    _litellm(monkeypatch, modell=modell, baustein="lernen")
    _gateway(monkeypatch, _liste(modell))
    la._haus_pause.clear()
    protokoll: list[tuple[str, float]] = []
    zustand = {"litellm_zeitlimit": True}

    async def litellm(wahl, auftrag, zugang):
        protokoll.append(("litellm", auftrag.timeout))
        if zustand["litellm_zeitlimit"]:
            raise la.LLMFehler("LiteLLM: Zeitüberschreitung", verbindung=True, zeitlimit=True)
        return la.Antwort("{}", "fertig", wahl)

    async def claude(wahl, auftrag, zugang):
        protokoll.append(("claude", auftrag.timeout))
        return la.Antwort("{}", "fertig", wahl)

    monkeypatch.setattr(la, "_litellm_anfrage", litellm)
    monkeypatch.setattr(la, "_claude_anfrage", claude)
    return protokoll, zustand


async def test_lernen_hausmodell_bekommt_nur_den_kurzen_vorversuch(monkeypatch, cz):
    protokoll, _ = _hintergrund(monkeypatch)
    antwort = await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert protokoll == [("litellm", la.HAUS_VORVERSUCH_SECS["lernen"]), ("claude", la.ANFRAGE_TIMEOUT_SECS["lernen"])]
    assert la.HAUS_VORVERSUCH_SECS["lernen"] < la.ANFRAGE_TIMEOUT_SECS["lernen"]
    assert antwort.wahl.anbieter == "claude" and antwort.wahl.rueckfall


async def test_lernen_nach_zeitueberschreitung_gleich_claude_dann_wieder_haus(monkeypatch, cz):
    protokoll, zustand = _hintergrund(monkeypatch)
    await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert la.haus_pausiert("gemma4-31b")
    protokoll.clear()
    await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert [a for a, _ in protokoll] == ["claude"]  # das Hausmodell wird nicht noch einmal abgewartet
    la._haus_pause["gemma4-31b"] = 0.0  # Pause vorbei
    zustand["litellm_zeitlimit"] = False
    protokoll.clear()
    antwort = await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert [a for a, _ in protokoll] == ["litellm"] and antwort.wahl.anbieter == "litellm"


async def test_lernen_haus_secs_aus_der_umgebung(monkeypatch, cz):
    protokoll, _ = _hintergrund(monkeypatch)
    monkeypatch.setenv("WAMO_LERNEN_HAUS_SECS", "6")
    await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert protokoll[0] == ("litellm", 6.0)
    la._haus_pause.clear()
    protokoll.clear()
    monkeypatch.setenv("WAMO_LERNEN_HAUS_SECS", "0")  # 0: volle Zeitgrenze wie früher
    await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert protokoll[0] == ("litellm", la.ANFRAGE_TIMEOUT_SECS["lernen"])
    assert not la.haus_pausiert("gemma4-31b")  # ohne Verkürzung keine Pause


async def test_haus_profil_behaelt_die_volle_zeitgrenze_und_keine_pause(monkeypatch, hz):
    """Ein Profil mit nur Hausmodellen hat keinen Rückfall: die Verkürzung gilt nie."""
    _hausmodelle(monkeypatch)
    _gateway(monkeypatch, _liste("gemma4-31b"))
    la._haus_pause.clear()
    zeiten = []

    async def litellm(wahl, auftrag, zugang):
        zeiten.append(auftrag.timeout)
        raise la.LLMFehler("LiteLLM: Zeitüberschreitung", verbindung=True, zeitlimit=True)

    monkeypatch.setattr(la, "_litellm_anfrage", litellm)
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.anfrage("lernen", zugang=hz, system="s", inhalt="i", max_tokens=10)
    assert zeiten == [la.ANFRAGE_TIMEOUT_SECS["lernen"]]
    assert not la._haus_pause


def test_vorversuch_nur_mit_rueckfall(monkeypatch, cz, hz):
    assert la.haus_vorversuch_secs("lernen", cz) == la.HAUS_VORVERSUCH_SECS["lernen"]
    assert la.haus_vorversuch_secs("lernen", hz) is None
    assert la.haus_vorversuch_secs("vision", cz) is None  # nur Hintergrundbausteine
    monkeypatch.setenv("WAMO_LLM_RUECKFALL", "aus")
    assert la.haus_vorversuch_secs("lernen", cz) is None
