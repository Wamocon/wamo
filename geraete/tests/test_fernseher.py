"""Fernseher: LG webOS (SSAP) und Samsung, ohne Gerät (WebSocket als Attrappe). Nur feste Befehle, Kopplung nötig,
Lautstärke begrenzt, Wake-on-LAN-Paket."""

import asyncio
import json

import pytest

from wamo_geraete.konfig import geraet_aus
from wamo_geraete.treiber import Abgelehnt, NichtEingerichtet, fernseher, lg_tv

LG = geraet_aus({"kennung": "fernseher", "klasse": "fernseher", "treiber": "lg-webos", "adresse": "192.168.178.125",
                 "mac": "00:a1:59:72:7a:92", "max_lautstaerke": 40})


class WsAttrappe:
    def __init__(self, antworten):
        self.gesendet = []
        self.antworten = antworten

    async def send(self, text):
        n = json.loads(text)
        self.gesendet.append(n)
        for a in self.antworten(n):
            self._warte.put_nowait(json.dumps(a))

    async def recv(self):
        return await self._warte.get()

    async def close(self):
        pass

    def start(self):
        self._warte = asyncio.Queue()
        return self


def test_nur_feste_befehle():
    assert set(lg_tv.BEFEHLE) == {"lautstaerke_lesen", "lautstaerke_setzen", "stumm_setzen", "ausschalten", "eingang",
                                  "fernsehen"}
    for uri in lg_tv.BEFEHLE.values():
        assert uri.startswith("ssap://") and "update" not in uri and "settings" not in uri
    with pytest.raises(KeyError):
        lg_tv.anfrage("ssap://system/factoryReset")
    anmeldung = lg_tv.anmeldung(None)
    assert anmeldung["payload"]["manifest"]["permissions"] == lg_tv.RECHTE and "client-key" not in anmeldung["payload"]
    assert lg_tv.anmeldung("abcdef0123456789")["payload"]["client-key"] == "abcdef0123456789"


async def test_lg_sitzung_anmelden_und_lautstaerke_begrenzt(monkeypatch):
    def antworten(n):
        if n["type"] == "register":
            return [{"type": "response", "id": "anmeldung", "payload": {"pairingType": "PROMPT"}},
                    {"type": "registered", "id": "anmeldung", "payload": {"client-key": "0123456789abcdef"}}]
        if n["uri"] == lg_tv.BEFEHLE["lautstaerke_lesen"]:
            return [{"type": "response", "id": n["id"], "payload": {"volumeStatus": {"volume": 38, "muteStatus": False},
                                                                     "returnValue": True}}]
        return [{"type": "response", "id": n["id"], "payload": {"returnValue": True}}]
    ws = WsAttrappe(antworten).start()

    async def verbinden(_g, _pin):
        return ws, "AA"
    monkeypatch.setattr(lg_tv, "_verbinden", verbinden)
    monkeypatch.setattr(lg_tv, "kopplung_laden", lambda _g: ("0123456789abcdef", "AA"))
    erg = await lg_tv.steuern(LG, "lauter", None, None)
    assert erg == {"lautstaerke": 40}  # 38 + 5, begrenzt auf 40
    setzen = [n for n in ws.gesendet if n.get("uri") == lg_tv.BEFEHLE["lautstaerke_setzen"]]
    assert setzen[-1]["payload"] == {"volume": 40}
    await lg_tv.steuern(LG, "eingang", None, "hdmi2")
    assert ws.gesendet[-1]["payload"] == {"inputId": "HDMI_2"}
    with pytest.raises(Abgelehnt):
        await lg_tv.steuern(LG, "eingang", None, "usb")
    with pytest.raises(Abgelehnt):
        await lg_tv.steuern(LG, "firmware", None, None)


async def test_lg_ohne_kopplung(monkeypatch):
    monkeypatch.setattr(lg_tv, "kopplung_laden", lambda _g: None)
    with pytest.raises(NichtEingerichtet):
        await lg_tv.steuern(LG, "lautstaerke", 10, None)


async def test_lg_einschalten_mit_wake_on_lan(monkeypatch):
    pakete = []

    async def nicht_erreichbar(_g):
        return False

    async def wol(mac):
        pakete.append(mac)
    monkeypatch.setattr(lg_tv, "_erreichbar", nicht_erreichbar)
    monkeypatch.setattr(lg_tv, "wake_on_lan", wol)
    assert (await lg_tv.steuern(LG, "an", None, None))["weg"] == "wake-on-lan"
    assert pakete == ["00:a1:59:72:7a:92"]


def test_magisches_paket():
    p = fernseher._magisches_paket("00:a1:59:72:7a:92")
    assert p[:6] == b"\xff" * 6 and len(p) == 102 and p[6:12] == bytes.fromhex("00a159727a92")


async def test_samsung_ohne_kopplung(monkeypatch):
    samsung = geraet_aus({"kennung": "fernseher-2", "klasse": "fernseher", "treiber": "samsung-tv",
                          "adresse": "192.168.178.99"})
    monkeypatch.setattr(fernseher, "kopplung_laden", lambda _g: None)
    with pytest.raises(NichtEingerichtet):
        await fernseher.taste(samsung, "hdmi1")
    with pytest.raises(Abgelehnt):
        await fernseher.taste(samsung, "KEY_FACTORY")


async def test_samsung_name_mit_html_zeichen_wird_lesbar(monkeypatch):
    """Am Fernseher beobachtet (11.11.2026): Tizen liefert den Namen als "85&quot; QLED", WAMO sagte "85quot QLED"."""
    samsung = geraet_aus({"kennung": "fernseher-2", "klasse": "fernseher", "treiber": "samsung-tv", "adresse": "192.168.178.45",
                          "mac": "f4:dd:06:13:6c:44", "max_lautstaerke": 50})

    async def info(_g):
        return {"PowerState": "on", "modelName": "GQ85Q70DATXZG", "name": "85&quot; QLED"}

    async def lautstaerke(*_a, **_k):
        return 18

    monkeypatch.setattr(fernseher, "_geraeteinfo", info)
    monkeypatch.setattr(fernseher.upnp, "lautstaerke_lesen", lautstaerke)
    erg = await fernseher.status(samsung)
    assert erg["name"] == "85 QLED" and erg["modell"] == "GQ85Q70DATXZG" and erg["lautstaerke"] == 18
    for roh, erwartet in (("Wohnzimmer &amp; Küche", "Wohnzimmer Kche"), ("&lt;script&gt;", "script"), ("A&#34;B", "AB")):
        async def anders(_g, roh=roh):
            return {"PowerState": "on", "name": roh}

        monkeypatch.setattr(fernseher, "_geraeteinfo", anders)
        assert (await fernseher.status(samsung))["name"] == erwartet  # nach dem Entschlüsseln gilt weiter nur die enge Zeichenliste
