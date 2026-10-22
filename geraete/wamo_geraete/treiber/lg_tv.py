"""Fernseher LG webOS (LG OLED83B49LA, 192.168.178.125, gefunden am 21.10.2026 über mDNS und SSDP).

Steuerung über die SSAP-Schnittstelle von webOS (wss://<ip>:3001, JSON über WebSocket). Sie braucht eine einmalige
Kopplung, die die Person am Fernseher bestätigt; danach liegt ein client-key in
/var/lib/wamo-geraete/kopplung/<kennung>.json (0600, mit dem SHA-256-Fingerabdruck des Fernseher-Zertifikats).
Einschalten aus dem Standby über Wake-on-LAN (MAC aus geraete.toml; am Fernseher muss "Einschalten über
Mobilgerät/LAN" an sein).

Nur feste Befehle (BEFEHLE): Lautstärke lesen und setzen, stumm, ausschalten, Eingang wählen. Apps, Browser, Texte,
Einstellungen, Updates und Werkseinstellungen gibt es nicht. Die Kopplung fragt nur die Rechte in RECHTE an; ein
Admin kann ein anderes Manifest in WAMO_GERAETE_LG_MANIFEST hinterlegen, falls der Fernseher das einfache ablehnt.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
import os
import re
import ssl

from ..http_klein import tls_ohne_ca
from ..konfig import Geraet
from . import Abgelehnt, GeraetFehler, NichtEingerichtet
from .fernseher import wake_on_lan

PORT = 3001
KOPPLUNG_ORDNER = os.getenv("WAMO_GERAETE_KOPPLUNG") or "/var/lib/wamo-geraete/kopplung"
RECHTE = ["CONTROL_POWER", "CONTROL_AUDIO", "CONTROL_INPUT_TV", "READ_INPUT_DEVICE_LIST", "LAUNCH"]
BEFEHLE = {
    "lautstaerke_lesen": "ssap://audio/getVolume",
    "lautstaerke_setzen": "ssap://audio/setVolume",
    "stumm_setzen": "ssap://audio/setMute",
    "ausschalten": "ssap://system/turnOff",
    "eingang": "ssap://tv/switchInput",
    "fernsehen": "ssap://system.launcher/launch",
}
EINGAENGE = {"hdmi1": "HDMI_1", "hdmi2": "HDMI_2", "hdmi3": "HDMI_3", "hdmi4": "HDMI_4"}
STANDARD_MAX = 50
SCHRITT = 5
KEY_RE = re.compile(r"^[0-9a-f]{16,128}$")
_ids = itertools.count(1)


def _datei(geraet: Geraet) -> str:
    return os.path.join(KOPPLUNG_ORDNER, f"{geraet.kennung}.json")


def kopplung_laden(geraet: Geraet) -> tuple[str, str] | None:
    try:
        fd = os.open(_datei(geraet), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    schluessel, pin = d.get("client_key"), d.get("tls_sha256")
    if isinstance(schluessel, str) and KEY_RE.fullmatch(schluessel) and isinstance(pin, str):
        return schluessel, pin
    return None


def kopplung_speichern(geraet: Geraet, schluessel: str, pin: str) -> None:
    os.makedirs(KOPPLUNG_ORDNER, mode=0o700, exist_ok=True)
    tmp = _datei(geraet) + ".neu"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"client_key": schluessel, "tls_sha256": pin}, f)
    os.replace(tmp, _datei(geraet))


def manifest() -> dict:
    pfad = os.getenv("WAMO_GERAETE_LG_MANIFEST")
    if pfad:
        try:
            with open(pfad, encoding="utf-8") as f:
                roh = json.load(f)
            if isinstance(roh, dict):
                return roh
        except (OSError, ValueError):
            pass
    return {"manifestVersion": 1, "appVersion": "1.0", "permissions": list(RECHTE)}


def anmeldung(client_key: str | None) -> dict:
    payload: dict = {"forcePairing": False, "pairingType": "PROMPT", "manifest": manifest()}
    if client_key:
        payload["client-key"] = client_key
    return {"type": "register", "id": "anmeldung", "payload": payload}


def anfrage(befehl: str, nutzlast: dict | None = None) -> dict:
    """SSAP-Anfrage nur für Befehle aus BEFEHLE (KeyError sonst)."""
    return {"type": "request", "id": f"w{next(_ids)}", "uri": BEFEHLE[befehl], "payload": nutzlast or {}}


def _fingerabdruck(der: bytes) -> str:
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


class Sitzung:
    """Eine WebSocket-Verbindung zum Fernseher, angemeldet mit client-key (oder zur Kopplung ohne)."""

    def __init__(self, ws):
        self.ws = ws

    async def _empfangen(self, kennung: str, timeout: float) -> dict:
        async with asyncio.timeout(timeout):
            while True:
                n = json.loads(await self.ws.recv())
                if isinstance(n, dict) and n.get("id") == kennung:
                    return n

    async def anmelden(self, client_key: str | None, timeout: float) -> str:
        await self.ws.send(json.dumps(anmeldung(client_key)))
        async with asyncio.timeout(timeout):
            while True:
                n = await self._empfangen("anmeldung", timeout)
                if n.get("type") == "registered":
                    key = (n.get("payload") or {}).get("client-key")
                    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
                        raise GeraetFehler("Fernseher liefert keinen gültigen Schlüssel")
                    return key
                if n.get("type") == "error":
                    raise NichtEingerichtet("Fernseher hat die Anmeldung abgelehnt (Kopplung nötig)")

    async def befehl(self, befehl: str, nutzlast: dict | None = None, timeout: float = 5.0) -> dict:
        nachricht = anfrage(befehl, nutzlast)
        await self.ws.send(json.dumps(nachricht))
        antwort = await self._empfangen(nachricht["id"], timeout)
        if antwort.get("type") == "error" or (antwort.get("payload") or {}).get("returnValue") is False:
            raise GeraetFehler("Fernseher lehnt den Befehl ab")
        return antwort.get("payload") or {}


async def _verbinden(geraet: Geraet, pin: str | None):
    import websockets

    ws = await asyncio.wait_for(websockets.connect(f"wss://{geraet.adresse}:{PORT}", ssl=tls_ohne_ca(),
                                                   open_timeout=5, max_size=256 * 1024, ping_interval=None,
                                                   proxy=None), 6)
    ssl_obj = ws.transport.get_extra_info("ssl_object")
    der = ssl_obj.getpeercert(binary_form=True) if isinstance(ssl_obj, ssl.SSLObject) else b""
    fingerabdruck = _fingerabdruck(der or b"")
    if pin and not hmac.compare_digest(fingerabdruck, pin):
        await ws.close()
        raise GeraetFehler("Zertifikat des Fernsehers hat sich seit der Kopplung geändert")
    return ws, fingerabdruck


async def _erreichbar(geraet: Geraet) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(geraet.adresse, PORT), 2)
        w.close()
        return True
    except (OSError, TimeoutError):
        return False


async def _mit_sitzung(geraet: Geraet, arbeit):
    kopplung = kopplung_laden(geraet)
    if kopplung is None:
        raise NichtEingerichtet("Fernseher ist nicht gekoppelt (Kopplung am Fernseher bestätigen, siehe Bericht)")
    key, pin = kopplung
    try:
        ws, _ = await _verbinden(geraet, pin)
    except (OSError, TimeoutError, ssl.SSLError) as e:
        raise GeraetFehler("Fernseher nicht erreichbar (aus?)") from e
    try:
        sitzung = Sitzung(ws)
        await sitzung.anmelden(key, 5.0)
        return await arbeit(sitzung)
    except (OSError, ValueError, TimeoutError) as e:
        raise GeraetFehler("Fernseher antwortet nicht wie erwartet") from e
    finally:
        await ws.close()


async def status(geraet: Geraet) -> dict:
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse des Fernsehers fehlt")
    if not await _erreichbar(geraet):
        return {"erreichbar": False, "an": False, "hinweis": "aus oder im Standby",
                "gekoppelt": kopplung_laden(geraet) is not None}
    erg = {"erreichbar": True, "an": True, "gekoppelt": kopplung_laden(geraet) is not None}
    if erg["gekoppelt"]:
        try:
            laut = await _mit_sitzung(geraet, lambda s: s.befehl("lautstaerke_lesen"))
            stand = laut.get("volumeStatus") if isinstance(laut.get("volumeStatus"), dict) else laut
            wert = stand.get("volume")
            erg["lautstaerke"] = wert if isinstance(wert, int) and 0 <= wert <= 100 else None
            erg["stumm"] = stand.get("muteStatus", stand.get("mute")) is True
        except (GeraetFehler, NichtEingerichtet):
            erg["lautstaerke"] = None
    return erg


async def koppeln(geraet: Geraet, timeout: float = 30.0) -> str:
    ws, fingerabdruck = await _verbinden(geraet, None)
    try:
        key = await Sitzung(ws).anmelden(None, timeout)
    finally:
        await ws.close()
    kopplung_speichern(geraet, key, fingerabdruck)
    return fingerabdruck


async def steuern(geraet: Geraet, aktion: str, wert: int | None, eingang: str | None) -> dict:
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse des Fernsehers fehlt")
    grenze = int(geraet.opt("max_lautstaerke", STANDARD_MAX))
    if aktion == "an":
        if await _erreichbar(geraet):
            return {"an": True, "unveraendert": True}
        mac = geraet.opt("mac")
        if not mac:
            raise NichtEingerichtet("Für das Einschalten fehlt die MAC-Adresse (Wake-on-LAN)")
        await wake_on_lan(mac)
        return {"an": True, "weg": "wake-on-lan"}
    if aktion == "aus":
        if not await _erreichbar(geraet):
            return {"an": False, "unveraendert": True}
        await _mit_sitzung(geraet, lambda s: s.befehl("ausschalten"))
        return {"an": False}
    if aktion in ("stumm", "laut"):
        await _mit_sitzung(geraet, lambda s: s.befehl("stumm_setzen", {"mute": aktion == "stumm"}))
        return {"stumm": aktion == "stumm"}
    if aktion in ("lautstaerke", "lauter", "leiser"):
        async def arbeit(s: Sitzung) -> int:
            if aktion == "lautstaerke":
                if wert is None or not 0 <= wert <= 100:
                    raise Abgelehnt("Lautstärke 0 bis 100")
                ziel = wert
            else:
                laut = await s.befehl("lautstaerke_lesen")
                stand = laut.get("volumeStatus") if isinstance(laut.get("volumeStatus"), dict) else laut
                jetzt = stand.get("volume") if isinstance(stand.get("volume"), int) else 0
                ziel = jetzt + SCHRITT if aktion == "lauter" else jetzt - SCHRITT
            ziel = max(0, min(grenze, ziel))
            await s.befehl("lautstaerke_setzen", {"volume": ziel})
            return ziel
        return {"lautstaerke": await _mit_sitzung(geraet, arbeit)}
    if aktion == "eingang":
        erlaubt = geraet.opt("eingaenge", ("tv", "hdmi1", "hdmi2", "hdmi3", "hdmi4"))
        if eingang not in erlaubt:
            raise Abgelehnt(f"Eingang: {', '.join(erlaubt)}")
        if eingang == "tv":
            await _mit_sitzung(geraet, lambda s: s.befehl("fernsehen", {"id": "com.webos.app.livetv"}))
        else:
            await _mit_sitzung(geraet, lambda s: s.befehl("eingang", {"inputId": EINGAENGE[eingang]}))
        return {"eingang": eingang}
    raise Abgelehnt("Diese Aktion gibt es für Fernseher nicht")
