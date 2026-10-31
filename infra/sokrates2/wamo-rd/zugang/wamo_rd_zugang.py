#!/usr/bin/env python3
"""Zugangsdienst des Remote-Desktop-Gateways (Container wamo-rd-zugang, Stufe 3 des Konzepts docs/remote-desktop.md).

Ein kleiner Vermittler zwischen dem Kern (voice-core/rd_api.py) und der Webanwendung von Apache Guacamole, die in einem
internen Docker-Netz ohne Adresse am Host liegt. Er kennt KEINE Geheimnisse: Weder den Schlüssel der JSON-Anmeldung noch ein
Passwort eines Ziels. Die signierte und verschlüsselte Anweisung baut allein der Kern; der Zugangsdienst reicht sie unverändert
bei Guacamole ein (POST /api/tokens) und gibt das Sitzungstoken zurück. Ohne Schlüssel kann er selbst keine Verbindung erzeugen.

Schnittstelle: ausschließlich ein Unix-Socket (Standard /run/wamo-rd/ctl.sock, Modus 0660), kein Netzport. Jede Verbindung
prüft per SO_PEERCRED die Benutzerkennung des Gegenübers; zugelassen ist nur WAMO_RD_KERN_UID (der Benutzer wamo-core).
Protokoll: je Verbindung EINE Zeile JSON hinein (höchstens 16 KiB) und EINE Zeile JSON zurück.

  {"cmd": "proben"}                                  -> {"ok": true, "ziele": {"sokrates2": true, ...}}
  {"cmd": "anmelden", "sitzung": "<uuid>", "data": "<base64>", "max_secs": 3600}
                                                     -> {"ok": true, "token": "..."} | {"ok": false, "code": "..."}
  {"cmd": "status", "sitzung": "<uuid>"}             -> {"ok": true, "offen": bool, "verbunden": bool, "grund": null|"..."}
  {"cmd": "beenden", "sitzung": "<uuid>", "grund": "beendet"}  -> {"ok": true, "offen": false, "grund": "..."}

Durchgesetzt wird hier unabhängig vom Kern (der Kern erzwingt dieselben Grenzen noch einmal):
  * Höchstdauer: nach max_secs wird das Sitzungstoken bei Guacamole gelöscht (DELETE /api/tokens/<token>); das schließt auch
    laufende Tunnel. Fällt der Kern aus, endet die Sitzung trotzdem.
  * Nicht geöffnet: Wird die Gateway-Seite innerhalb von 180 Sekunden nach dem Start nicht benutzt, endet die Sitzung.
  * Getrennt: War die Verbindung offen und ist sie 60 Sekunden lang nicht mehr aktiv (Browser zu, Leerlauf am Ziel mit
    xrdp IdleTimeLimit, Netz weg), endet die Sitzung. Ein Neuladen der Seite innerhalb der 60 Sekunden bleibt möglich.
  * Höchstens 3 Sitzungen gleichzeitig. Eine Anweisung wird nie wiederholt (Guacamole sperrt die Absenderadresse nach 3 Fehlversuchen).

Das Sitzungstoken liegt nur im Speicher dieses Prozesses, nie in einer Datei, nie im Protokoll. Das Protokoll (stdout) nennt
nur Ereignis, Kurzform der Sitzungskennung und Grund. Bewusst nur Standardbibliothek (Python 3.11 oder neuer).
"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import socket
import stat
import struct
import sys
import time
import tomllib
import urllib.parse
import uuid
from dataclasses import dataclass, field

SOCKET = os.environ.get("WAMO_RD_SOCKET", "/run/wamo-rd/ctl.sock")
GUAC_HOST = os.environ.get("WAMO_RD_GUAC_HOST", "wamo-rd-web")
GUAC_PORT = int(os.environ.get("WAMO_RD_GUAC_PORT", "8080"))
ZIELE_DATEI = os.environ.get("WAMO_RD_ZIELE", "/etc/wamo-rd-ziele.toml")

MAX_ZEILE = 16 * 1024
MAX_SITZUNGEN = 3
MAX_SECS = 4 * 3600
HTTP_TIMEOUT = 10.0
PROBE_TIMEOUT = 2.0
AUFSEHER_TAKT = float(os.environ.get("WAMO_RD_AUFSEHER_TAKT", "10"))
NICHT_GEOEFFNET_SECS = float(os.environ.get("WAMO_RD_NICHT_GEOEFFNET_SECS", "180"))
GETRENNT_SECS = float(os.environ.get("WAMO_RD_GETRENNT_SECS", "60"))
FERTIG_BEHALTEN_SECS = 600.0
MAX_FERTIG = 64
GRUENDE = ("beendet", "abgeloest", "zeitlimit", "getrennt", "nicht_geoeffnet", "profil_inaktiv", "kern_neustart", "fehler")
NAME_RE = re.compile(r"^[a-z0-9]{1,16}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def protokoll(text: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {text}", flush=True)


@dataclass
class Sitzung:
    token: str
    start: float
    frist: float
    verbunden: bool = False
    zuletzt_aktiv: float = 0.0


@dataclass
class Zustand:
    sitzungen: dict[str, Sitzung] = field(default_factory=dict)
    fertig: dict[str, tuple[str, float]] = field(default_factory=dict)  # Sitzung -> (Grund, Zeitpunkt)


def kurz(sitzung: str) -> str:
    return sitzung[:8]


# ---------- Guacamole (blockierend, wird mit to_thread aufgerufen) ----------
def _guac(methode: str, pfad: str, body: bytes | None = None, kopf: dict[str, str] | None = None) -> tuple[int, bytes]:
    verbindung = http.client.HTTPConnection(GUAC_HOST, GUAC_PORT, timeout=HTTP_TIMEOUT)
    try:
        verbindung.request(methode, pfad, body=body, headers=kopf or {})
        antwort = verbindung.getresponse()
        return antwort.status, antwort.read(256 * 1024)
    finally:
        verbindung.close()


def guac_anmelden(data: str) -> tuple[int, str | None]:
    """Anweisung einreichen. (Status, Token). Nie wiederholen."""
    form = urllib.parse.urlencode({"data": data}).encode()
    status, roh = _guac("POST", "/api/tokens", form, {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        return status, None
    try:
        token = json.loads(roh.decode("utf-8")).get("authToken")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return 502, None
    return (200, token) if isinstance(token, str) and TOKEN_RE.fullmatch(token) else (502, None)


def guac_abmelden(token: str) -> int:
    return _guac("DELETE", "/api/tokens/" + urllib.parse.quote(token, safe=""))[0]


def guac_aktive(token: str) -> int | None:
    """Anzahl der offenen Tunnel dieses Tokens; None, wenn das Token nicht mehr gilt oder Guacamole nicht antwortet.

    Gemessen gegen Guacamole 1.6.0: Das Modul der JSON-Anmeldung führt kein Verzeichnis aktiver Verbindungen
    (activeConnections ist immer leer). Verlässlich ist /api/session/tunnels, die Liste der Tunnel der Sitzung."""
    status, roh = _guac("GET", "/api/session/tunnels?token=" + urllib.parse.quote(token, safe=""))
    if status != 200:
        return None
    try:
        daten = json.loads(roh.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return len(daten) if isinstance(daten, list) else None


# ---------- Ziele ----------
def ziele_laden(pfad: str = ZIELE_DATEI) -> dict[str, tuple[str, int]]:
    """Name -> (Adresse, Port) aus der Zielliste (keine Geheimnisse darin). Fehlerhafte Einträge fallen weg."""
    try:
        with open(pfad, "rb") as f:
            daten = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    aus: dict[str, tuple[str, int]] = {}
    for z in daten.get("ziel", []) if isinstance(daten.get("ziel"), list) else []:
        if not isinstance(z, dict):
            continue
        name, adresse, port = z.get("name"), z.get("adresse"), z.get("port", 3389)
        if (isinstance(name, str) and NAME_RE.fullmatch(name) and isinstance(adresse, str)
                and isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535):
            try:
                socket.inet_pton(socket.AF_INET, adresse)
            except OSError:
                continue
            aus[name] = (adresse, port)
    return aus


async def _probe(adresse: str, port: int) -> bool:
    try:
        _, schreiber = await asyncio.wait_for(asyncio.open_connection(adresse, port), PROBE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return False
    schreiber.close()
    try:
        await schreiber.wait_closed()
    except OSError:
        pass
    return True


# ---------- Befehle ----------
def _sitzung_id(anfrage: dict) -> str | None:
    wert = anfrage.get("sitzung")
    try:
        return str(uuid.UUID(wert)) if isinstance(wert, str) and len(wert) == 36 else None
    except ValueError:
        return None


def _beendet_vermerken(z: Zustand, sitzung: str, grund: str) -> None:
    z.fertig[sitzung] = (grund, time.monotonic())
    while len(z.fertig) > MAX_FERTIG:
        z.fertig.pop(next(iter(z.fertig)))


async def sitzung_beenden(z: Zustand, sitzung: str, grund: str) -> bool:
    """Token bei Guacamole löschen und die Sitzung vermerken. True, wenn es sie gab."""
    s = z.sitzungen.pop(sitzung, None)
    if s is None:
        return False
    _beendet_vermerken(z, sitzung, grund)
    try:
        code = await asyncio.to_thread(guac_abmelden, s.token)
    except OSError:
        code = 0
    protokoll(f"beendet sitzung={kurz(sitzung)} grund={grund} abmelden={code}")
    return True


async def befehl(z: Zustand, anfrage: dict) -> dict:
    cmd = anfrage.get("cmd")
    if cmd == "proben":
        ziele = ziele_laden()
        ergebnisse = await asyncio.gather(*(_probe(a, p) for a, p in ziele.values()))
        return {"ok": True, "ziele": dict(zip(ziele, ergebnisse, strict=True))}

    sitzung = _sitzung_id(anfrage)
    if sitzung is None:
        return {"ok": False, "code": "sitzung_ungueltig"}

    if cmd == "anmelden":
        data, max_secs = anfrage.get("data"), anfrage.get("max_secs")
        if (not isinstance(data, str) or not 100 <= len(data) <= MAX_ZEILE - 512 or not BASE64_RE.fullmatch(data)
                or isinstance(max_secs, bool) or not isinstance(max_secs, int) or not 60 <= max_secs <= MAX_SECS):
            return {"ok": False, "code": "angaben_ungueltig"}
        if sitzung in z.sitzungen:
            return {"ok": False, "code": "schon_da"}
        if len(z.sitzungen) >= MAX_SITZUNGEN:
            return {"ok": False, "code": "voll"}
        try:
            status, token = await asyncio.to_thread(guac_anmelden, data)
        except OSError:
            protokoll(f"anmelden sitzung={kurz(sitzung)} gateway_nicht_erreichbar")
            return {"ok": False, "code": "gateway_nicht_erreichbar"}
        if token is None:
            protokoll(f"anmelden sitzung={kurz(sitzung)} abgelehnt status={status}")
            return {"ok": False, "code": "abgelehnt" if status in (401, 403, 429) else "gateway_fehler"}
        jetzt = time.monotonic()
        z.sitzungen[sitzung] = Sitzung(token=token, start=jetzt, frist=jetzt + max_secs)
        protokoll(f"angemeldet sitzung={kurz(sitzung)} max_secs={max_secs}")
        return {"ok": True, "token": token}

    if cmd == "status":
        s = z.sitzungen.get(sitzung)
        if s is not None:
            return {"ok": True, "offen": True, "verbunden": s.verbunden, "grund": None}
        grund = z.fertig.get(sitzung, (None, 0.0))[0]
        return {"ok": True, "offen": False, "verbunden": False, "grund": grund}

    if cmd == "beenden":
        grund = anfrage.get("grund")
        if grund not in GRUENDE:
            return {"ok": False, "code": "grund_ungueltig"}
        await sitzung_beenden(z, sitzung, grund)
        return {"ok": True, "offen": False, "grund": z.fertig.get(sitzung, (grund, 0.0))[0]}

    return {"ok": False, "code": "unbekannter_befehl"}


# ---------- Aufseher ----------
async def aufseher_runde(z: Zustand) -> None:
    jetzt = time.monotonic()
    for sitzung, s in list(z.sitzungen.items()):
        if jetzt >= s.frist:
            await sitzung_beenden(z, sitzung, "zeitlimit")
            continue
        try:
            offen = await asyncio.to_thread(guac_aktive, s.token)
        except OSError:
            continue  # Guacamole antwortet gerade nicht: nichts schließen, die Frist gilt weiter
        if offen is None:
            await sitzung_beenden(z, sitzung, "getrennt")  # Token gilt nicht mehr
        elif offen > 0:
            s.verbunden = True
            s.zuletzt_aktiv = jetzt
        elif s.verbunden and jetzt - s.zuletzt_aktiv >= GETRENNT_SECS:
            await sitzung_beenden(z, sitzung, "getrennt")
        elif not s.verbunden and jetzt - s.start >= NICHT_GEOEFFNET_SECS:
            await sitzung_beenden(z, sitzung, "nicht_geoeffnet")
    for sitzung, (_, zeit) in list(z.fertig.items()):
        if jetzt - zeit > FERTIG_BEHALTEN_SECS:
            del z.fertig[sitzung]


async def aufseher(z: Zustand) -> None:
    while True:
        await asyncio.sleep(AUFSEHER_TAKT)
        try:
            await aufseher_runde(z)
        except Exception as e:  # noqa: BLE001 (der Aufseher darf nie enden)
            protokoll(f"aufseher fehler={type(e).__name__}")


# ---------- Socket ----------
def gegenueber_uid(schreiber: asyncio.StreamWriter) -> int | None:
    sock = schreiber.get_extra_info("socket")
    if sock is None:
        return None
    try:
        _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    except (OSError, struct.error):
        return None
    return uid


def verbindung_behandeln(z: Zustand, erlaubte_uid: int):
    async def lauf(leser: asyncio.StreamReader, schreiber: asyncio.StreamWriter) -> None:
        try:
            if gegenueber_uid(schreiber) != erlaubte_uid:
                protokoll("abgewiesen: fremde Benutzerkennung")
                return
            try:
                zeile = await asyncio.wait_for(leser.readline(), 10)
            except (asyncio.TimeoutError, ValueError, asyncio.LimitOverrunError):
                antwort: dict = {"ok": False, "code": "zeile_ungueltig"}
            else:
                try:
                    anfrage = json.loads(zeile.decode("utf-8")) if 0 < len(zeile) <= MAX_ZEILE else None
                except (ValueError, UnicodeDecodeError, RecursionError):
                    anfrage = None
                if not isinstance(anfrage, dict):
                    antwort = {"ok": False, "code": "zeile_ungueltig"}
                else:
                    try:
                        antwort = await befehl(z, anfrage)
                    except Exception as e:  # noqa: BLE001
                        protokoll(f"befehl fehler={type(e).__name__}")
                        antwort = {"ok": False, "code": "intern"}
            schreiber.write(json.dumps(antwort, separators=(",", ":")).encode() + b"\n")
            await schreiber.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            schreiber.close()

    return lauf


async def dienen(pfad: str, erlaubte_uid: int, z: Zustand | None = None) -> None:
    z = z or Zustand()
    try:
        if stat.S_ISSOCK(os.lstat(pfad).st_mode):
            os.unlink(pfad)
    except FileNotFoundError:
        pass
    alt = os.umask(0o117)  # Socket mit Modus 0660
    try:
        server = await asyncio.start_unix_server(verbindung_behandeln(z, erlaubte_uid), path=pfad, limit=MAX_ZEILE + 1024)
    finally:
        os.umask(alt)
    protokoll(f"bereit socket={pfad} kern_uid={erlaubte_uid}")
    wache = asyncio.create_task(aufseher(z))
    try:
        async with server:
            await server.serve_forever()
    finally:
        wache.cancel()


def main() -> int:
    try:
        uid = int(os.environ["WAMO_RD_KERN_UID"])
    except (KeyError, ValueError):
        print("WAMO_RD_KERN_UID fehlt oder ist keine Zahl", file=sys.stderr)
        return 2
    if uid < 100:
        print("WAMO_RD_KERN_UID muss eine Benutzerkennung ab 100 sein", file=sys.stderr)
        return 2
    try:
        asyncio.run(dienen(SOCKET, uid))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
