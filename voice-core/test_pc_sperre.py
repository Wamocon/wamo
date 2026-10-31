"""Tests für den gesperrten PC im Kern: Zeitleiste, Zustand betrieb.pc, Tunnel-Probe, Bildschirm und Prompt.

Ohne Netz und ohne PC: Der Host wird mit httpx.MockTransport bzw. Attrappen nachgebildet.
"""

from __future__ import annotations

import asyncio
import json
import secrets

import httpx
import pytest

import activity
import pc_host
import pc_tunnel
import screen
from llm_anbieter import Zugang
from persona import build_system_prompt

TOKEN = secrets.token_urlsafe(32)
GESPERRT = json.dumps({"status": "locked", "summary": "Der Bildschirm ist gesperrt."})


def test_browser_lesen_ist_modellwerkzeug_und_beweist_entsperrt():
    assert "browser_lesen" in pc_host.LLM_TOOLS  # damit auch gate_pc_tools: Seiteninhalt gilt als Fremdinhalt (turn.taint)
    assert pc_host.sperre_aus_ergebnis("browser_lesen", {"status": "success", "text": "x"}) is False
    assert pc_host.sperre_aus_ergebnis("browser_lesen", {"status": "locked"}) is True


async def test_browser_lesen_faerbt_den_zug_als_fremdinhalt():
    """Der vorhandene Weg (gate_pc_tools, turn.taint) gilt für jedes Host-Werkzeug aus LLM_TOOLS: kein Lernen und kein
    sofort wirksames gemeinsames Wissen aus einem Zug, in dem Seiteninhalt gelesen wurde."""
    import bot
    from pipecat.adapters.schemas.function_schema import FunctionSchema

    class Params:
        arguments: dict = {}

        async def result_callback(self, wert):
            self.wert = wert

    async def original(params):
        await params.result_callback({"status": "success", "vertrauen": "unvertrauenswuerdig"})

    gefaerbt: list[int] = []
    schema = FunctionSchema("browser_lesen", "x", {}, [], handler=original)
    gate = bot.gate_pc_tools([schema], lambda: True, lambda: gefaerbt.append(1))[0]
    await gate.handler(Params())
    assert gefaerbt == [1]
    aus = bot.gate_pc_tools([schema], lambda: False, lambda: gefaerbt.append(1))[0]
    p = Params()
    await aus.handler(p)
    assert gefaerbt == [1] and p.wert["status"] == "rejected"  # PC-Steuerung aus: nichts gelesen, nichts gefärbt


# ---------- Zeitleiste und Auswertung der Werkzeugergebnisse ----------
def test_locked_ist_in_der_zeitleiste_abgelehnt():
    assert activity._status(GESPERRT) == "abgelehnt"
    assert activity._status({"status": "locked"}) == "abgelehnt"


def test_sperre_aus_ergebnis():
    assert pc_host.sperre_aus_ergebnis("tastatur_tippen", GESPERRT) is True
    assert pc_host.sperre_aus_ergebnis("bildschirm_ansehen", {"status": "locked", "antwort": "x"}) is True
    assert pc_host.sperre_aus_ergebnis("system_info", json.dumps({"status": "success", "gesperrt": False})) is False
    assert pc_host.sperre_aus_ergebnis("system_info", json.dumps({"status": "success", "gesperrt": True})) is True
    # Ein erfolgreicher Eingabe-Aufruf beweist: entsperrt
    assert pc_host.sperre_aus_ergebnis("fenster_fokussieren", json.dumps({"status": "success"})) is False
    assert pc_host.sperre_aus_ergebnis("bildschirm_ansehen", {"status": "success", "antwort": "Teams"}) is False
    # Nichts Belastbares: unverändert lassen
    for name, ergebnis in [("webseite_oeffnen", json.dumps({"status": "success"})),
                           ("tastatur_tippen", json.dumps({"status": "rejected"})),
                           ("system_info", json.dumps({"status": "success", "gesperrt": "ja"})),
                           ("tastatur_tippen", "kein json"), ("merken", {"gespeichert": True})]:
        assert pc_host.sperre_aus_ergebnis(name, ergebnis) is None, name


def test_sperr_probe_nicht_fuers_modell():
    assert "sperre_pruefen" not in pc_host.LLM_TOOLS


class Host:
    """Antwortet auf initialize und tools/call sperre_pruefen; gesperrt lässt sich umschalten."""

    def __init__(self):
        self.gesperrt: bool | None = False
        self.down = False
        self.aufrufe: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("weg", request=request)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "wamo-host"}, "capabilities": {}}
        else:
            self.aufrufe.append(body["params"]["name"])
            daten = {"status": "success"} if self.gesperrt is None else {"status": "success", "gesperrt": self.gesperrt}
            result = {"content": [{"type": "text", "text": json.dumps(daten)}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


async def test_pc_sperre_pruefen_ueber_den_host():
    fake = Host()
    host = pc_tunnel.TunnelHost("/x", TOKEN, tools_filter=[], transport=httpx.MockTransport(fake))
    assert await pc_host.pc_sperre_pruefen(host) is False
    fake.gesperrt = True
    assert await pc_host.pc_sperre_pruefen(host) is True
    fake.gesperrt = None  # älterer Host ohne Angabe
    assert await pc_host.pc_sperre_pruefen(host) is None
    fake.down = True
    assert await pc_host.pc_sperre_pruefen(host) is None  # nicht erreichbar: unbekannt, nie eine Ausnahme
    assert fake.aufrufe[0] == "sperre_pruefen"
    await host.close()


async def test_tunnel_probe_meldet_sperre_und_entsperren(monkeypatch):
    monkeypatch.setattr(pc_tunnel, "UEBERWACHUNG_SECS", 0.01)
    monkeypatch.setattr(pc_tunnel, "NACHPRUEFEN_SECS", 0.01)
    fake = Host()
    host = pc_tunnel.TunnelHost("/x", TOKEN, tools_filter=[], transport=httpx.MockTransport(fake))
    await host.pruefen()
    erreichbar: list[bool] = []
    sperren: list[bool] = []

    async def on_change(ok, _latenz):
        erreichbar.append(ok)

    async def on_sperre(gesperrt):
        sperren.append(gesperrt)

    waechter = asyncio.create_task(host.ueberwache(on_change, on_sperre))

    async def warte(bedingung):
        for _ in range(300):
            if bedingung():
                return
            await asyncio.sleep(0.01)

    def wechsel():  # jede Probe meldet ihren Stand, die Sitzung entprellt selbst (bot.set_sperre)
        return [s for i, s in enumerate(sperren) if i == 0 or sperren[i - 1] != s]

    await warte(lambda: wechsel() == [False])  # erste Probe meldet den Ausgangszustand
    fake.gesperrt = True
    await warte(lambda: wechsel() == [False, True])
    fake.gesperrt = False
    await warte(lambda: wechsel() == [False, True, False])
    waechter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waechter
    assert wechsel() == [False, True, False] and erreichbar == []  # Sperre ist kein Verbindungsausfall
    await host.close()


async def test_alte_ueberwachung_ohne_sperr_rueckruf_fragt_nicht():
    fake = Host()
    host = pc_tunnel.TunnelHost("/x", TOKEN, tools_filter=[], transport=httpx.MockTransport(fake))
    await host.pruefen()
    task = asyncio.create_task(host.ueberwache(lambda *_: asyncio.sleep(0)))
    await asyncio.sleep(0.05)
    task.cancel()
    assert fake.aufrufe == []
    await host.close()


# ---------- Bildschirm ansehen und klicken ----------
class Params:
    def __init__(self, arguments):
        self.arguments = arguments
        self.result = None

    async def result_callback(self, value):
        self.result = value


class Llm:
    def __init__(self):
        self.handler = {}

    def register_function(self, name, handler, **_kw):
        self.handler[name] = handler


async def test_bildschirm_bei_sperre(monkeypatch):
    async def gesperrt(_client, name, _args):
        assert name == "bildschirmfoto"
        return GESPERRT

    monkeypatch.setattr(screen, "call_host_tool", gesperrt)
    llm = Llm()
    fremd: list[int] = []
    screen.register_screen_tools(llm, lambda: object(), lambda: True, lambda: fremd.append(1),
                                 zugang=Zugang.haus("0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"))
    ansehen = Params({"frage": "Was ist offen?"})
    await llm.handler["bildschirm_ansehen"](ansehen)
    assert ansehen.result["status"] == "locked" and "gesperrt" in ansehen.result["antwort"]
    klicken = Params({"ziel": "Senden-Knopf"})
    await llm.handler["auf_bildschirm_klicken"](klicken)
    assert klicken.result["status"] == "locked"
    assert activity._status(klicken.result) == "abgelehnt"


# ---------- Anzeige und Prompt ----------
def test_betrieb_zeigt_gesperrt_nur_bei_verbundenem_pc():
    import bot

    assert bot.pc_anzeige("verbunden", True) == "gesperrt"
    assert bot.pc_anzeige("verbunden", False) == "verbunden"
    for status in ("getrennt", "aus", "verbindet"):
        assert bot.pc_anzeige(status, True) == status  # getrennt bleibt getrennt


def test_prompt_kennt_den_gesperrten_bildschirm():
    text = build_system_prompt("Niko", "du", pc_werkzeuge=True)
    assert "status=locked" in text and "entsperren kann nur Niko" in text
    assert "status=locked" not in build_system_prompt("Niko", "du", pc_werkzeuge=False)
