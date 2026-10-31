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


# ---------- Fenster statt Monitor, Hinweise zur Werkzeugwahl ----------
class _Params:
    def __init__(self, arguments):
        self.arguments, self.result = arguments, None

    async def result_callback(self, wert):
        self.result = wert


class _Llm:
    def __init__(self):
        self.handler = {}

    def register_function(self, name, handler, **_kw):
        self.handler[name] = handler


async def test_shot_gibt_fenster_an_den_host_nur_wenn_gesetzt(monkeypatch):
    aufrufe = []

    async def host(_client, name, args):
        aufrufe.append((name, args))
        return json.dumps({"status": "success", **SHOT})

    monkeypatch.setattr(screen, "call_host_tool", host)
    await screen._shot(None)
    await screen._shot(None, "Chrome")
    assert aufrufe == [("bildschirmfoto", {"monitor": 0}), ("bildschirmfoto", {"monitor": 0, "fenster": "Chrome"})]


async def test_describe_ohne_fenster_ruft_shot_wie_bisher(monkeypatch):
    gesehen = []

    async def foto(*args):
        gesehen.append(args)
        return SHOT

    async def anfrage(baustein, *, zugang, system, inhalt, max_tokens):
        return la.Antwort("Ein Browser.", "fertig", CLAUDE)

    monkeypatch.setattr(screen, "_shot", foto)
    monkeypatch.setattr(screen, "anfrage", anfrage)
    zugang = la.Zugang.haus("0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c")
    await screen.describe(None, "Was ist offen?", zugang)
    await screen.describe(None, "Was ist offen?", zugang, "Chrome")
    assert gesehen == [(None,), (None, "Chrome")]


async def test_host_lehnt_foto_ab_modell_bekommt_den_grund(monkeypatch):
    async def host(_client, name, args):
        return json.dumps({"status": "rejected", "summary": "Kein offenes Fenster passt zu 'Foo'"})

    monkeypatch.setattr(screen, "call_host_tool", host)
    llm = _Llm()
    screen.register_screen_tools(llm, lambda: object(), lambda: True, lambda: None,
                                 zugang=la.Zugang.haus("0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"))
    p = _Params({"frage": "Was steht da?", "fenster": "Foo"})
    await llm.handler["bildschirm_ansehen"](p)
    assert p.result["status"] == "rejected" and "Foo" in p.result["antwort"]


def test_bildschirm_ansehen_nennt_erst_text_dann_foto():
    schema = next(s for s in screen.SCHEMAS if s.name == "bildschirm_ansehen")
    for wort in ("browser_lesen", "elemente_auflisten", "Text nicht reicht", "fenster"):
        assert wort in schema.description
    assert "fenster" in schema.properties
