"""Tests für den Lichtrahmen im Kern (activity.markierung_nachricht, ActivityReporter, PcFreigaben).

Ohne Netz, ohne Datenbank und ohne PC. Der Host liefert nur ganze Zahlen; der Kern prüft sie noch einmal, hängt die
Gerätekennung an und gibt nie etwas anderes weiter.
"""

from __future__ import annotations

import asyncio
import json
import secrets

import pytest
from unittest import mock

from pipecat.frames.frames import FunctionCallResultFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

import activity
import pc_host
from pc_freigaben import PcFreigaben

FENSTER = {"x": 100, "y": 50, "b": 800, "h": 600}
MONITOR = {"x": 0, "y": 0, "b": 1920, "h": 1080}
ERGEBNIS = {"status": "success", "summary": "ok", "markierung": {"fenster": FENSTER, "monitor": MONITOR}}
KLICK = {"status": "success", "markierung": {"fenster": FENSTER, "monitor": MONITOR, "punkt": {"x": 300, "y": 200}}}
KENNUNG = "pc-wmc-laptop"


def _nachricht(name="fenster_fokussieren", ergebnis=ERGEBNIS, geraet=KENNUNG, rechner="WMC-PC"):
    return activity.markierung_nachricht(name, json.dumps(ergebnis) if isinstance(ergebnis, dict) else ergebnis,
                                         geraet, rechner)


def test_nachricht_hat_feste_felder_und_die_geraetekennung():
    n = _nachricht()
    assert n == {"type": "markierung", "art": "fokus", "geraet": KENNUNG, "rechner": "WMC-PC",
                 "fenster": FENSTER, "monitor": MONITOR}


@pytest.mark.parametrize("name,art", [
    ("fenster_fokussieren", "fokus"), ("tastatur_tippen", "text"), ("element_text_setzen", "text"),
    ("tasten_druecken", "tasten"), ("maus_scrollen", "scrollen"), ("element_klicken", "klick"),
])
def test_arten(name, art):
    assert _nachricht(name)["art"] == art


def test_klick_mit_punkt_aus_dem_bildmodell():
    n = _nachricht("auf_bildschirm_klicken", {**KLICK, "gesehen": "Speichern-Knopf"})
    assert n["art"] == "klick" and n["punkt"] == {"x": 300, "y": 200}
    assert "gesehen" not in json.dumps(n)  # nichts außer den festen Feldern


def test_punkt_nur_bei_klicks_und_nur_im_monitor():
    assert "punkt" not in _nachricht("tastatur_tippen", KLICK)
    ausserhalb = {"status": "success", "markierung": {"fenster": FENSTER, "monitor": MONITOR, "punkt": {"x": 5000, "y": 5}}}
    assert "punkt" not in _nachricht("auf_bildschirm_klicken", ausserhalb)


@pytest.mark.parametrize("status", ["rejected", "locked", "wartet", "needs_approval", "error", "duplicate", "blocked"])
def test_nur_nach_erfolg(status):
    assert _nachricht(ergebnis={**ERGEBNIS, "status": status}) is None


def test_fremde_werkzeuge_und_fehlende_markierung():
    assert _nachricht("powershell_lesen") is None
    assert _nachricht("websuche") is None
    assert _nachricht(ergebnis={"status": "success"}) is None
    assert _nachricht(ergebnis="kein json") is None
    assert _nachricht(ergebnis="[1, 2]") is None


@pytest.mark.parametrize("fenster", [
    {"x": 1.5, "y": 0, "b": 10, "h": 10}, {"x": "1", "y": 0, "b": 10, "h": 10}, {"x": True, "y": 0, "b": 10, "h": 10},
    {"x": 0, "y": 0, "b": 0, "h": 10}, {"x": 0, "y": 0, "b": 10, "h": -1}, {"x": 40000, "y": 0, "b": 10, "h": 10},
    {"x": 0, "y": -40000, "b": 10, "h": 10}, {"x": 0, "y": 0, "b": 40000, "h": 10},
    {"x": 0, "y": 0, "b": 10}, {"x": 0, "y": 0, "b": 10, "h": 10, "titel": "Kundenliste"}, "text", None, [],
])
def test_ungueltige_zahlen_ergeben_keine_nachricht(fenster):
    assert _nachricht(ergebnis={**ERGEBNIS, "markierung": {"fenster": fenster, "monitor": MONITOR}}) is None
    assert _nachricht(ergebnis={**ERGEBNIS, "markierung": {"fenster": FENSTER, "monitor": fenster}}) is None


def test_zusatzfelder_im_ergebnis_wandern_nie_weiter():
    ergebnis = {**ERGEBNIS, "markierung": {"fenster": FENSTER, "monitor": MONITOR, "titel": "Geheim", "text": "Passwort"}}
    text = json.dumps(_nachricht(ergebnis=ergebnis))
    assert "Geheim" not in text and "Passwort" not in text


@pytest.mark.parametrize("geraet", ["PC-1", "pc-", "pc-Groß", "pc-a b", "x" * 40, "pc-" + "a" * 30, 42, ""])
def test_ungueltige_geraetekennung_ergibt_keine_nachricht(geraet):
    assert _nachricht(geraet=geraet) is None  # kein "unbekannt": dann zeichnet kein Laptop


def test_ohne_kennung_bleibt_der_rechnername():
    n = _nachricht(geraet=None, rechner="WMC-PC")
    assert n["geraet"] is None and n["rechner"] == "WMC-PC"
    assert _nachricht(geraet=None, rechner="böse; name")["rechner"] is None


# ---------- ActivityReporter ----------
async def _gesendet(reporter, name, ergebnis, error=None):
    """Nachrichten, die der Reporter für ein Werkzeugergebnis an die Oberfläche schickt (Pipeline als Attrappe)."""
    gesendet: list[dict] = []

    async def push(frame, *_args, **_kwargs):
        if isinstance(frame, RTVIServerMessageFrame):
            gesendet.append(frame.data)

    frame = FunctionCallResultFrame(function_name=name, tool_call_id="t-1", arguments={}, result=ergebnis, error=error)
    with mock.patch.object(FrameProcessor, "process_frame", new=mock.AsyncMock()):
        reporter.push_frame = push
        await reporter.process_frame(frame, FrameDirection.DOWNSTREAM)
    return [m for m in gesendet if m.get("type") == "markierung"]


async def test_reporter_ohne_rahmen_ist_stumm():
    r = activity.ActivityReporter()
    assert r.markierung_von is None
    assert await _gesendet(r, "fenster_fokussieren", json.dumps(ERGEBNIS)) == []


async def test_reporter_mit_rahmen_meldet_die_kennung_des_laptops():
    r = activity.ActivityReporter(markierung_von=lambda: (KENNUNG, "WMC-PC"))
    marken = await _gesendet(r, "fenster_fokussieren", json.dumps(ERGEBNIS))
    assert len(marken) == 1 and marken[0]["geraet"] == KENNUNG and marken[0]["fenster"] == FENSTER
    assert await _gesendet(r, "fenster_fokussieren", json.dumps(ERGEBNIS), error="boom") == []
    assert await _gesendet(r, "fenster_fokussieren", json.dumps({**ERGEBNIS, "status": "locked"})) == []


async def test_reporter_stoert_sich_nicht_an_einer_kaputten_kennung_quelle():
    def kaputt():
        raise RuntimeError("weg")

    r = activity.ActivityReporter(markierung_von=kaputt)
    assert await _gesendet(r, "fenster_fokussieren", json.dumps(ERGEBNIS)) == []


# ---------- PcFreigaben: freigegebene Aktion ----------
AID = "0123456789abcdef"
SCHLUESSEL = secrets.token_urlsafe(32)
ANFRAGE = json.dumps({
    "status": "needs_approval", "approval_id": AID, "aktion": "senden", "grund": "Nachricht tippen und senden",
    "detail": "Bin gleich da\n", "ziel": "Chat | Microsoft Teams (ms-teams.exe)", "gueltig_s": 300, "summary": "x",
})


class _Client:
    pass


class _Gedaechtnis:
    async def log_action(self, tool, params, reason, risk):
        return "aktion-1"

    async def update_action(self, action_id, status):
        return True


async def _freigeben(monkeypatch, antwort, markierung_von):
    async def host(_client, _name, _args):
        return json.dumps(antwort)

    monkeypatch.setattr(pc_host, "call_host_tool", host)
    client = _Client()
    pc_host._FREIGABE_SCHLUESSEL[client] = SCHLUESSEL
    gesendet: list[dict] = []

    async def senden(n):
        gesendet.append(n)

    async def melden(_t):
        return None

    f = PcFreigaben(_Gedaechtnis(), client=lambda: client, darf=lambda: True, melden=melden, fremdinhalt=lambda: None,
                    senden=senden, von="Niko", pc_name=lambda: "WMC-PC", markierung_von=markierung_von)
    f.anfrage(activity.freigabe_anfrage("tastatur_tippen", {}, ANFRAGE, "call-1"))
    await f.entscheiden({"job": "pc", "id": AID, "entscheidung": "ja"})
    await asyncio.sleep(0)
    return [n for n in gesendet if n.get("type") == "markierung"]


async def test_freigegebene_aktion_zeigt_den_rahmen(monkeypatch):
    marken = await _freigeben(monkeypatch, ERGEBNIS, lambda: (KENNUNG, "WMC-PC"))
    assert len(marken) == 1 and marken[0]["geraet"] == KENNUNG and marken[0]["art"] == "text"


async def test_fehlschlag_und_ohne_rahmen_zeigen_nichts(monkeypatch):
    assert await _freigeben(monkeypatch, {**ERGEBNIS, "status": "error"}, lambda: (KENNUNG, "WMC-PC")) == []
    assert await _freigeben(monkeypatch, ERGEBNIS, None) == []
