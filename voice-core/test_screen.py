"""Tests für das Finden von Bildschirmelementen (screen.py): Koordinaten je Anbieter (Claude in Bildpixeln,
Hausmodelle auf 0 bis 1000) und dass unbrauchbare Antworten nie zu einem Klick an einer geratenen Stelle führen.
Ohne Netz und ohne PC: Foto und Modellaufruf sind ersetzt."""

from __future__ import annotations

import json

import pytest

import llm_anbieter as la
import screen

SHOT = {"jpeg_base64": "QUJD", "bild_breite": 1280, "bild_hoehe": 720, "left": 0, "top": 0, "scale": 1.0}
CLAUDE = la.Wahl("vision", "claude", "claude-haiku-5-5", "claude")
HAUS = la.Wahl("vision", "litellm", "gemma4-31b", "litellm")


@pytest.mark.parametrize("found,anbieter,erwartet", [
    ({"gefunden": True, "x": 1130, "y": 630}, "claude", (1130, 630)),
    ({"gefunden": True, "x": 883, "y": 875}, "litellm", (1130, 630)),       # 0 bis 1000 -> Pixel
    ({"gefunden": True, "x": 875.0, "y": 873.024}, "litellm", (1120, 629)),
    ({"gefunden": True, "x": 1000, "y": 1000}, "litellm", (1279, 719)),     # Rand bleibt im Bild
    ({"gefunden": True, "x": 1280, "y": 0}, "claude", (1279, 0)),
])
def test_in_bildpixel(found, anbieter, erwartet):
    ergebnis = screen.in_bildpixel(found, SHOT, anbieter)
    assert ergebnis["gefunden"] is True and (ergebnis["x"], ergebnis["y"]) == erwartet
    assert isinstance(ergebnis["x"], int) and isinstance(ergebnis["y"], int)


@pytest.mark.parametrize("found,anbieter", [
    ({"gefunden": True, "x": 875, "y": 8730241546793415}, "litellm"),  # Kommazahl ohne Punkt
    ({"gefunden": True, "x": -3, "y": 10}, "claude"),
    ({"gefunden": True, "x": 5000, "y": 10}, "claude"),                 # nie an den Rand ziehen
    ({"gefunden": True, "x": "links", "y": 10}, "claude"),
    ({"gefunden": True, "x": None, "y": 10}, "litellm"),
])
def test_unbrauchbare_koordinaten_heissen_nicht_gefunden(found, anbieter):
    assert screen.in_bildpixel(found, SHOT, anbieter)["gefunden"] is False


def test_systemtext_je_anbieter():
    text = screen._locate_system(SHOT)
    assert "Bildpixel" in text(CLAUDE) and "1280x720" in text(CLAUDE)
    assert "0 bis 1000" in text(HAUS) and "Bildpixel" not in text(HAUS)
    assert screen.DATA_RULE in text(CLAUDE) and screen.DATA_RULE in text(HAUS)


@pytest.mark.parametrize("wahl,antwort,erwartet", [
    (HAUS, {"gefunden": True, "x": 883, "y": 875, "was": "Senden"}, (True, 1130, 630)),
    (CLAUDE, {"gefunden": True, "x": 1130, "y": 630, "was": "Senden"}, (True, 1130, 630)),
    (HAUS, {"gefunden": False, "x": 0, "y": 0, "was": ""}, (False, 0, 0)),
])
async def test_locate_rechnet_nach_dem_tatsaechlichen_anbieter(monkeypatch, wahl, antwort, erwartet):
    gesehen = {}

    async def foto(_client):
        return SHOT

    async def falsche_anfrage(baustein, *, zugang, system, inhalt, max_tokens, json_schema=None, schema_name=""):
        gesehen["system"] = system(wahl)  # wie in llm_anbieter: Text erst mit der tatsächlichen Wahl
        gesehen["schema"] = json_schema
        return la.Antwort(json.dumps(antwort), "fertig", wahl)

    monkeypatch.setattr(screen, "_shot", foto)
    monkeypatch.setattr(screen, "anfrage", falsche_anfrage)
    found, _shot = await screen.locate(None, "Senden-Knopf", la.Zugang.haus("0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"))
    assert (found["gefunden"], found["x"], found["y"]) == erwartet
    assert gesehen["schema"]["properties"]["x"]["type"] == "number"
    assert ("0 bis 1000" in gesehen["system"]) is (wahl.anbieter == "litellm")
