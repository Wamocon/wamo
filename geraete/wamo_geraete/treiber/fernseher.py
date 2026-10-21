"""Fernseher Samsung (Tizen): Status über die REST-Schnittstelle (http://<ip>:8001/api/v2/), Lautstärke über
UPnP-RenderingControl (Port 9197, ohne Kopplung), Ein/Aus und Eingang über die Fernbedienungs-Schnittstelle
(wss://<ip>:8002, braucht eine Kopplung), Einschalten aus dem Tiefschlaf per Wake-on-LAN (MAC aus geraete.toml).

Kopplung (einmalig, nie automatisch): Ein Admin startet auf Sokrates-2
    sudo -u wamo-geraete /opt/wamo-geraete/venv/bin/python -m wamo_geraete.koppeln <kennung>
und bestätigt am Fernseher die Frage "WAMO zulassen?". Das Token und der SHA-256-Fingerabdruck des Zertifikats des
Fernsehers landen in /var/lib/wamo-geraete/kopplung/<kennung>.json (0600). Ohne Kopplung melden Ein/Aus und Eingang
"nicht eingerichtet". Firmware, Werkseinstellungen, Netzwerk und Apps gibt es nicht: nur die Tasten in TASTEN.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import socket
import ssl

from ..http_klein import HttpFehler, anfrage, tls_ohne_ca
from ..konfig import Geraet
from ..text import name
from . import Abgelehnt, GeraetFehler, NichtEingerichtet
from . import upnp

KOPPLUNG_ORDNER = os.getenv("WAMO_GERAETE_KOPPLUNG") or "/var/lib/wamo-geraete/kopplung"
APP_NAME = base64.b64encode(b"WAMO").decode()
RC_PFAD = "/upnp/control/RenderingControl1"
RC_PORT = 9197
WS_PORT = 8002
TASTEN = {"power": "KEY_POWER", "tv": "KEY_TV", "hdmi1": "KEY_HDMI1", "hdmi2": "KEY_HDMI2", "hdmi3": "KEY_HDMI3",
          "hdmi4": "KEY_HDMI4"}
STANDARD_MAX = 50
SCHRITT = 5
TOKEN_RE = re.compile(r"^[0-9A-Za-z]{4,64}$")


def _kopplung_datei(geraet: Geraet) -> str:
    return os.path.join(KOPPLUNG_ORDNER, f"{geraet.kennung}.json")


def kopplung_laden(geraet: Geraet) -> tuple[str, str] | None:
    try:
        fd = os.open(_kopplung_datei(geraet), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, encoding="utf-8") as f:
            d = json.load(f)
        token, pin = d.get("token"), d.get("tls_sha256")
        if isinstance(token, str) and TOKEN_RE.fullmatch(token) and isinstance(pin, str):
            return token, pin
    except (OSError, ValueError):
        pass
    return None


def kopplung_speichern(geraet: Geraet, token: str, pin: str) -> None:
    os.makedirs(KOPPLUNG_ORDNER, mode=0o700, exist_ok=True)
    tmp = _kopplung_datei(geraet) + ".neu"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"token": token, "tls_sha256": pin}, f)
    os.replace(tmp, _kopplung_datei(geraet))


async def _geraeteinfo(geraet: Geraet) -> dict | None:
    """Info des Fernsehers oder None, wenn er nicht antwortet (aus oder im Tiefschlaf)."""
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse des Fernsehers fehlt (Fernseher war bei der Erkundung aus)")
    try:
        antwort = await anfrage(geraet.adresse, geraet.port or 8001, "GET", "/api/v2/", timeout=2.5, max_bytes=64 * 1024)
    except HttpFehler:
        return None
    if antwort.status != 200:
        return None
    try:
        d = json.loads(antwort.rumpf.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return d.get("device") if isinstance(d, dict) and isinstance(d.get("device"), dict) else None


async def status(geraet: Geraet) -> dict:
    info = await _geraeteinfo(geraet)
    if info is None:
        return {"erreichbar": False, "an": False, "hinweis": "aus oder im Tiefschlaf",
                "gekoppelt": kopplung_laden(geraet) is not None}
    an = str(info.get("PowerState", "on")).lower() == "on"
    erg = {"erreichbar": True, "an": an, "modell": name(info.get("modelName"), 32), "name": name(info.get("name"), 32),
           "gekoppelt": kopplung_laden(geraet) is not None}
    if an:
        try:
            erg["lautstaerke"] = await upnp.lautstaerke_lesen(geraet, RC_PFAD, port=RC_PORT)
        except (GeraetFehler, NichtEingerichtet):
            erg["lautstaerke"] = None
    return erg


def _magisches_paket(mac: str) -> bytes:
    roh = bytes.fromhex(mac.replace(":", ""))
    return b"\xff" * 6 + roh * 16


async def wake_on_lan(mac: str) -> None:
    paket = _magisches_paket(mac)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            s.sendto(paket, ("192.168.178.255", 9))
            await asyncio.sleep(0.1)
    finally:
        s.close()


def _fingerabdruck(der: bytes) -> str:
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


async def _ws(geraet: Geraet, token: str | None, pin: str | None, timeout: float):
    import websockets  # erst hier: nur Fernseher brauchen es

    uri = f"wss://{geraet.adresse}:{WS_PORT}/api/v2/channels/samsung.remote.control?name={APP_NAME}"
    if token:
        uri += f"&token={token}"
    ws = await asyncio.wait_for(websockets.connect(uri, ssl=tls_ohne_ca(), open_timeout=timeout, max_size=64 * 1024,
                                                   ping_interval=None, proxy=None), timeout)
    ssl_obj = ws.transport.get_extra_info("ssl_object")
    der = ssl_obj.getpeercert(binary_form=True) if isinstance(ssl_obj, ssl.SSLObject) else b""
    fingerabdruck = _fingerabdruck(der or b"")
    if pin and not hmac.compare_digest(fingerabdruck, pin):
        await ws.close()
        raise GeraetFehler("Zertifikat des Fernsehers hat sich seit der Kopplung geändert")
    return ws, fingerabdruck


async def _verbunden(ws, timeout: float) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            nachricht = json.loads(await ws.recv())
            ereignis = nachricht.get("event") if isinstance(nachricht, dict) else None
            if ereignis == "ms.channel.connect":
                return nachricht.get("data") if isinstance(nachricht.get("data"), dict) else {}
            if ereignis in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                raise NichtEingerichtet("Fernseher hat die Verbindung nicht zugelassen (Kopplung nötig)")


async def taste(geraet: Geraet, schluessel: str) -> None:
    if schluessel not in TASTEN:
        raise Abgelehnt("Diese Taste gibt es nicht")
    kopplung = kopplung_laden(geraet)
    if kopplung is None:
        raise NichtEingerichtet("Fernseher ist nicht gekoppelt (Kopplung am Fernseher bestätigen, siehe Bericht)")
    token, pin = kopplung
    try:
        ws, _ = await _ws(geraet, token, pin, 5.0)
    except (OSError, TimeoutError, ssl.SSLError) as e:
        raise GeraetFehler("Fernseher nicht erreichbar") from e
    try:
        await _verbunden(ws, 5.0)
        await ws.send(json.dumps({"method": "ms.remote.control", "params": {
            "Cmd": "Click", "DataOfCmd": TASTEN[schluessel], "Option": "false", "TypeOfRemote": "SendRemoteKey"}}))
        await asyncio.sleep(0.3)
    except (OSError, TimeoutError, ValueError) as e:
        raise GeraetFehler("Fernseher hat die Taste nicht angenommen") from e
    finally:
        await ws.close()


async def koppeln(geraet: Geraet, timeout: float = 30.0) -> str:
    """Einmalige Kopplung (nur über wamo_geraete.koppeln). Der Nutzer bestätigt am Fernseher."""
    ws, fingerabdruck = await _ws(geraet, None, None, 10.0)
    try:
        daten = await _verbunden(ws, timeout)
    finally:
        await ws.close()
    token = daten.get("token")
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise GeraetFehler("Fernseher hat kein Token geliefert (Bestätigung am Fernseher?)")
    kopplung_speichern(geraet, token, fingerabdruck)
    return fingerabdruck


async def steuern(geraet: Geraet, aktion: str, wert: int | None, eingang: str | None) -> dict:
    grenze = int(geraet.opt("max_lautstaerke", STANDARD_MAX))
    if aktion in ("an", "aus"):
        info = await _geraeteinfo(geraet)
        an = info is not None and str(info.get("PowerState", "on")).lower() == "on"
        if (aktion == "an") == an:
            return {"an": an, "unveraendert": True}
        if aktion == "an" and info is None:
            mac = geraet.opt("mac")
            if not mac:
                raise NichtEingerichtet("Fernseher schläft tief, für Wake-on-LAN fehlt die MAC-Adresse")
            await wake_on_lan(mac)
            return {"an": True, "weg": "wake-on-lan"}
        await taste(geraet, "power")
        return {"an": aktion == "an"}
    if aktion == "eingang":
        erlaubt = geraet.opt("eingaenge", ("tv", "hdmi1", "hdmi2", "hdmi3", "hdmi4"))
        if eingang not in erlaubt:
            raise Abgelehnt(f"Eingang: {', '.join(erlaubt)}")
        await taste(geraet, eingang)
        return {"eingang": eingang}
    if aktion in ("stumm", "laut"):
        await upnp.stumm_setzen(geraet, RC_PFAD, aktion == "stumm", port=RC_PORT)
        return {"stumm": aktion == "stumm"}
    if aktion in ("lautstaerke", "lauter", "leiser"):
        if aktion == "lautstaerke":
            if wert is None or not 0 <= wert <= 100:
                raise Abgelehnt("Lautstärke 0 bis 100")
            ziel = wert
        else:
            jetzt = await upnp.lautstaerke_lesen(geraet, RC_PFAD, port=RC_PORT) or 0
            ziel = jetzt + SCHRITT if aktion == "lauter" else jetzt - SCHRITT
        ziel = max(0, min(grenze, ziel))
        await upnp.lautstaerke_setzen(geraet, RC_PFAD, ziel, port=RC_PORT)
        return {"lautstaerke": ziel}
    raise Abgelehnt("Diese Aktion gibt es für Fernseher nicht")
