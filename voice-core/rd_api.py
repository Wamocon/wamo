"""Fernzugriff: Remote Desktop der Sparks über das Gateway auf Sokrates-2 (docs/remote-desktop.md, Stufe 3).

  GET  /wamo/api/rd/ziele   Ziele mit Anzeigename und Zustand (frei | belegt | nicht_erreichbar), eigene Sitzung (nur Rolle admin)
  POST /wamo/api/rd/start   {"ziel": "sokrates2"}  startet eine Fernsitzung und liefert die Adresse der Gateway-Seite
  POST /wamo/api/rd/ende    {"sitzung": "<uuid>"}  beendet die eigene Fernsitzung

Wer eine Sitzung starten darf (alles muss stimmen, geprüft in dieser Reihenfolge):
  * Anmeldung wie bei allen Routen: Token mit zweitem Faktor, Höchstalter, aktives Profil (wamo_api.identitaet).
  * Rolle admin. Die Datenbank prüft sie in rd_sitzung_beginnen noch einmal (aktives Profil, Rolle admin).
  * Der Code beim Anmelden genügt (Entscheidung vom 11.11.2026, KP-2): Das Token trägt aal2 und einen totp-Eintrag im Claim amr
    (zweiter Faktor der Anmeldung, geprüft von der Zugangskontrolle samt Höchstalter WAMO_ANMELDUNG_MAX_STUNDEN). Ein frischer Code
    je Start ist nicht mehr nötig. Der Stufenschalter WAMO_RD_ANMELDUNG_MAX_MIN begrenzt das Alter dieses Codes nur für den
    Fernzugriff: ist er älter, lehnt der Kern mit 403 und dem Code zweiter_faktor_zu_alt ab, und die Oberfläche fragt den Code
    wieder ab (frisch heißt höchstens 120 Sekunden, wie in geraete_admin_api.py, das unverändert bleibt). Die Datenbank prüft die
    Zeit des Codes nur noch gegen die Zukunft und 31 Tage (Migration 0019).
    Das Restrisiko ist ein gestohlenes Token: Es startet Sitzungen, bis die Anmeldung abläuft (docs/remote-desktop.md). Dagegen
    meldet die Datenbank jeden Start allen Admins (Hinweis, Migration 0019), und die Stufe lässt sich zuschalten.
  * Das Ziel steht in der festen Zielliste /etc/wamo-rd/ziele.toml (root-Datei, nie aus der Anfrage). Ein frei gewählter Host,
    Port, Benutzer oder ein Protokoll existiert nicht: Aus der Anfrage kommt nur der Name, der in der Liste vorkommen muss.
  * Ein Zuschauer je Ziel, höchstens 3 Sitzungen gleichzeitig und 6 Starts je Stunde und Profil: die Datenbank hält das fest
    (Migration 0015: teilweise eindeutiger Index, Funktion rd_sitzung_beginnen). Ablösen kann nur dasselbe Profil.
  * Rate-Grenzen je Profil: Liste 60 je Minute, Start und Ende 10 beziehungsweise 20 je 10 Minuten (auch abgelehnte zählen).

Wie die Anmeldung am Gateway entsteht: Der Kern baut die Anweisung für Guacamoles "Encrypted JSON authentication" selbst
(base64 von AES-128-CBC, Null-IV, über Signatur HMAC-SHA256 und JSON; Schlüssel aus /etc/wamo-rd/json.key). Sie enthält die
Verbindung (RDP, Zielhost, Konto wamo-rd, Passwort aus /etc/wamo-rd/ziele.env). Ihr Feld expires ist das Höchstende der Sitzung
(Start + 60 Minuten): Gemessen gegen Guacamole 1.6.0 gilt expires nicht nur für das Einreichen, sondern für die ganze Sitzung, das
Sitzungstoken wird mit Ablauf ungültig (pruefe_gegen_guacamole.py). Eine Anweisung nur für wenige Sekunden würde die Sitzung nach
wenigen Sekunden unbenutzbar machen. Ein schon offener Tunnel läuft nach dem Ablauf allerdings weiter; ihn schließt der Zugangsdienst
zur Frist (Token löschen, gemessen: schließt den Tunnel) und der Aufseher in diesem Modul. Das Zeitlimit hat damit drei Stellen.
Die Anweisung verlässt den Kern nur über den Unix-Socket zum Zugangsdienst (infra/sokrates2/wamo-rd/zugang/), der sie bei Guacamole
einreicht und das Sitzungstoken zurückgibt; sie liegt danach nirgends mehr (keine Datei, kein Log, kein Browser). Der Browser bekommt NUR die Adresse der Gateway-Seite mit diesem Sitzungstoken; Passwort, Schlüssel,
Zielhost und Anweisung erreichen ihn nie und stehen in keinem Log und keiner Antwort.

Verbindung (fest, nicht änderbar): RDP, Konto wamo-rd, keine Zwischenablage in beide Richtungen, kein Laufwerk, kein Drucker, kein
Ton, keine Aufzeichnung (kein recording-path), begrenzte Auflösung. Protokoll (Tabelle rd_sitzungen) nur: wer, Ziel, Start, Ende,
Grund. Ende der Sitzung: Zeitlimit (60 Minuten) und Trennung erzwingen der Zugangsdienst und der Aufseher in diesem Modul;
Leerlauf (10 Minuten ohne Eingabe) erzwingt das Ziel (xrdp IdleTimeLimit), danach beendet der Aufseher die Sitzung hier.

Schalter: WAMO_RD_AKTIV (Standard nein). Ohne "ja" melden die Routen, dass der Fernzugriff nicht eingerichtet ist.
Stufenschalter: WAMO_RD_ANMELDUNG_MAX_MIN, Höchstalter des Codes der Anmeldung in Minuten (1 bis 43200). Leer oder 0 heißt aus
(Standard). Jeder andere Wert, auch ein Tippfehler, gilt als strengste Stufe: frischer Code bei jedem Start, dazu eine Fehlerzeile
im Journal (fail-closed, Befund M2).
Weitere Einstellungen (Standard in Klammern): WAMO_RD_SOCKET (/run/wamo-rd/ctl.sock), WAMO_RD_ZIELE (/etc/wamo-rd/ziele.toml),
WAMO_RD_ENV (/etc/wamo-rd/ziele.env), WAMO_RD_KEY (/etc/wamo-rd/json.key), WAMO_RD_URL (https://192.168.178.136:55446).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import tomllib
import uuid
import weakref
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, time

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from geraete_admin_api import CODE_AAL2, ZUKUNFT_SECS, aal2_frisch, aal2_zeit
from memory import DB_ERRORS, DatabaseUnavailable, shared_database
from wamo_api import bearer, identitaet, lies_json

router = APIRouter()

MAX_BODY = 2 * 1024
DAUER_SECS = 3600                 # Höchstdauer einer Sitzung
MAX_GLEICHZEITIG = 3
MAX_JE_STUNDE = 6
LEERLAUF_MINUTEN = 10             # Hinweis für die Oberfläche; durchgesetzt am Ziel
KONTO = "wamo-rd"                 # das einzige Konto, mit dem das Gateway an einem Spark anmeldet
VERBINDUNG = "fernsitzung"        # Name der einen Verbindung in der Anweisung
NAME_RE = re.compile(r"^[a-z0-9]{1,16}$")
PASSWORT_PRAEFIX = "WAMO_RD_PASSWORT_"
AUFSEHER_TAKT_SECS = 15.0
NEUE_SITZUNG_SCHONZEIT_SECS = 60.0  # frisch gestartete Sitzungen fasst der Aufseher nicht an (der Start läuft noch)
ZEITLIMIT_NACHFRIST_SECS = 5.0
OHNE_GATEWAY_NACHFRIST_SECS = 120.0
PROBEN_GUELTIG_SECS = 15.0
RELAY_TIMEOUT_SECS = 20.0
RELAY_MAX_ANTWORT = 64 * 1024
STUFE_VARIABLE = "WAMO_RD_ANMELDUNG_MAX_MIN"
STUFE_MAX_MIN = 43200             # 30 Tage; die Zugangskontrolle lässt ohnehin höchstens 720 Stunden zu (auth_gate.py)
_STUFE_RE = re.compile(r"[1-9][0-9]{0,4}")

LISTE_GRENZE = (60, 60.0)       # Aufrufe, Sekunden
START_GRENZE = (10, 600.0)
ENDE_GRENZE = (20, 600.0)
_versuche: dict[tuple[str, str], deque[float]] = {}


# Ein Lock je Profil für "Sitzung in der Datenbank beginnen und am Gateway anmelden". Schwach gehalten: Ohne Wartende und Halter
# räumt der Speicher ihn selbst auf. Er schützt nur innerhalb dieses Kerns; für Slot a und b prüft _noch_offen die Datenbank.
_start_sperren: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _start_sperre(profil: str) -> asyncio.Lock:
    sperre = _start_sperren.get(profil)
    if sperre is None:
        sperre = _start_sperren[profil] = asyncio.Lock()
    return sperre

# Verbindungsparameter von Guacamole (RDP). Alles hier ist fest; nichts davon stammt aus einer Anfrage.
VERBINDUNGS_PARAMETER = {
    "protocol": "rdp",
    "security": "tls",             # xrdp: TLS; ohne Rückfall auf Standard-RDP
    "ignore-cert": "true",         # xrdp stellt ein selbstsigniertes Zertifikat aus; Pinning ist offen (README, Abschnitt 16)
    "disable-copy": "true",        # keine Zwischenablage vom Ziel zum Handy
    "disable-paste": "true",       # keine Zwischenablage vom Handy zum Ziel
    "enable-drive": "false",
    "enable-printing": "false",
    "enable-sftp": "false",
    "disable-audio": "true",
    "enable-audio-input": "false",
    "enable-wallpaper": "false",
    "enable-theming": "false",
    "enable-font-smoothing": "true",
    "color-depth": "24",
    "resize-method": "display-update",
    "width": "1280",               # Obergrenze der Auflösung; der Browser passt die Größe im Rahmen der Anzeige an
    "height": "800",
    "dpi": "96",
    "server-layout": "de-de-qwertz",
    "read-only": "false",
}
# Diese Parameter dürfen nie in der Anweisung stehen (Aufzeichnung, Laufwerksfreigabe, Übertragung von Dateien)
VERBOTENE_PARAMETER = ("recording-path", "recording-name", "recording-include-keys", "recording-exclude-output",
                       "recording-exclude-mouse", "recording-exclude-touch", "create-recording-path", "drive-path",
                       "sftp-hostname", "printer-name", "typescript-path", "wol-send-packet")


# ======================================================================== Einstellungen
def aktiv() -> bool:
    return (os.getenv("WAMO_RD_AKTIV") or "nein").strip().lower() == "ja"


def _pfad(name: str, standard: str) -> Path:
    return Path(os.getenv(name) or standard)


def socket_pfad() -> str:
    return os.getenv("WAMO_RD_SOCKET") or "/run/wamo-rd/ctl.sock"


def basis_url() -> str:
    """Adresse der Gateway-Seite ohne Pfad. Nur https (oder http über Loopback für Proben); sonst gilt der Standard nicht."""
    roh = (os.getenv("WAMO_RD_URL") or "https://192.168.178.136:55446").strip().rstrip("/")
    if re.fullmatch(r"https://[A-Za-z0-9.\-]{1,100}(:\d{1,5})?", roh) or re.fullmatch(r"http://(127\.0\.0\.1|localhost)(:\d{1,5})?", roh):
        return roh
    raise HTTPException(503, "Die Adresse des Gateways ist nicht richtig eingerichtet")


# ======================================================================== Anmeldung genügt (Stufenschalter)
def anmeldung_max_secs(melden: bool = True) -> float | None:
    """Höchstalter des Codes der Anmeldung für den Fernzugriff in Sekunden (WAMO_RD_ANMELDUNG_MAX_MIN), bei jedem Aufruf neu gelesen.

    None: keine Grenze. Das gilt NUR für leer oder 0 (Standard). Jeder andere Wert muss eine ganze Zahl von 1 bis 43200 sein.
    Alles Ungültige (Text, negativ, Dezimalzahl, nan, zu groß, Ziffern anderer Schriften) gilt als strengste Stufe, 0.0 Sekunden:
    Dann genügt nur ein frischer Code wie vor KP-2, und das Journal bekommt eine Fehlerzeile (melden). Ein Tippfehler macht den
    Fernzugriff nie lockerer (fail-closed). Der Wert wird im Log nur gekürzt genannt."""
    roh = (os.getenv(STUFE_VARIABLE) or "").strip()
    if roh in ("", "0"):
        return None
    if _STUFE_RE.fullmatch(roh) and int(roh) <= STUFE_MAX_MIN:
        return int(roh) * 60.0
    if melden:
        logger.error("{} {!r} ungültig (leer oder 0 für aus, sonst 1 bis {} Minuten), es gilt die strengste Stufe: "
                     "frischer Code bei jedem Start", STUFE_VARIABLE, roh[:20], STUFE_MAX_MIN)
    return 0.0


def anmeldung_genuegt(zeit: float | None, jetzt: float | None = None) -> bool:
    """Darf ein Start auf Grund dieses Codes (Zeitpunkt aus dem Claim amr, None ohne aal2 oder totp) erfolgen?

    Ein frischer Code (höchstens 120 Sekunden, aal2_frisch) genügt immer. Ein älterer Code, der Code der Anmeldung, genügt in der
    Stufe aus und sonst bis zum Höchstalter. Ein Code aus der Zukunft (mehr als 5 Sekunden) genügt nie."""
    if zeit is None or not math.isfinite(zeit):
        return False
    jetzt = time() if jetzt is None else jetzt
    if aal2_frisch(zeit, jetzt):
        return True
    alter = jetzt - zeit
    if alter < -ZUKUNFT_SECS:
        return False
    grenze = anmeldung_max_secs()
    return grenze is None or alter <= grenze


def _dauer_text(secs: float) -> str:
    minuten = int(secs // 60)
    if minuten >= 60 and minuten % 60 == 0:
        stunden = minuten // 60
        return f"{stunden} Stunde" if stunden == 1 else f"{stunden} Stunden"
    return f"{minuten} Minute" if minuten == 1 else f"{minuten} Minuten"


# ======================================================================== Ziele und Zugangsdaten
@dataclass(frozen=True)
class Ziel:
    name: str
    anzeige: str
    adresse: str
    port: int


def ziele_laden(pfad: Path | None = None) -> dict[str, Ziel]:
    """Feste Zielliste (Name -> Ziel). Fehlerhafte oder doppelte Einträge fallen weg. Fehlt die Datei, gibt es keine Ziele."""
    pfad = pfad or _pfad("WAMO_RD_ZIELE", "/etc/wamo-rd/ziele.toml")
    try:
        with open(pfad, "rb") as f:
            daten = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    roh = daten.get("ziel")
    aus: dict[str, Ziel] = {}
    for z in roh if isinstance(roh, list) else []:
        if not isinstance(z, dict):
            continue
        name, anzeige, adresse, port = z.get("name"), z.get("anzeige"), z.get("adresse"), z.get("port", 3389)
        if not (isinstance(name, str) and NAME_RE.fullmatch(name) and name not in aus):
            continue
        if not (isinstance(adresse, str) and isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535):
            continue
        try:
            ip = ipaddress.IPv4Address(adresse)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local:
            continue
        if any(vorhanden.adresse == adresse for vorhanden in aus.values()):
            continue  # ein Rechner, ein Name: sonst hebelten zwei Namen die Grenze "ein Zuschauer je Ziel" aus
        titel = " ".join(anzeige.split())[:40] if isinstance(anzeige, str) and anzeige.strip() else name
        aus[name] = Ziel(name=name, anzeige=titel, adresse=adresse, port=port)
    return aus


def passwort_fuer(name: str, pfad: Path | None = None) -> str | None:
    """Passwort des Kontos wamo-rd am Ziel aus ziele.env. Nur im Kern, nie ausgeben oder loggen. None, wenn es fehlt."""
    pfad = pfad or _pfad("WAMO_RD_ENV", "/etc/wamo-rd/ziele.env")
    gesucht = PASSWORT_PRAEFIX + name.upper() + "="
    try:
        text = pfad.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for zeile in text.splitlines():
        if zeile.startswith(gesucht):
            wert = zeile[len(gesucht):].strip()
            if 8 <= len(wert) <= 128 and wert.isprintable() and wert == wert.strip() and '"' not in wert:
                return wert
            return None
    return None


def schluessel_lesen(pfad: Path | None = None) -> bytes | None:
    """128-Bit-Schlüssel der JSON-Anmeldung (32 Hexzeichen in /etc/wamo-rd/json.key). None, wenn er fehlt oder ungültig ist."""
    pfad = pfad or _pfad("WAMO_RD_KEY", "/etc/wamo-rd/json.key")
    try:
        text = pfad.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not re.fullmatch(r"[0-9a-f]{32}", text):
        return None
    return bytes.fromhex(text)


# ======================================================================== Anweisung für Guacamole
def anweisung_bauen(schluessel: bytes, sitzung: str, ziel: Ziel, passwort: str, jetzt: float | None = None,
                    gueltig_secs: float = DAUER_SECS) -> str:
    """Signierte und verschlüsselte Anmeldung für Guacamoles "Encrypted JSON authentication" (base64).

    Format laut Handbuch von Guacamole: AES-128-CBC (Null-IV, PKCS7) über [HMAC-SHA256(JSON) || JSON], danach base64. expires
    (Millisekunden) ist das Höchstende der Sitzung (jetzt + gueltig_secs, Vorgabe 60 Minuten): Das Sitzungstoken wird mit Ablauf
    ungültig, nicht erst das Einreichen. Der Benutzername ist nur eine Kurzform der Sitzung."""
    if len(schluessel) != 16:
        raise ValueError("Schlüssel muss 128 Bit haben")
    jetzt = time() if jetzt is None else jetzt
    parameter = dict(VERBINDUNGS_PARAMETER)
    parameter.update({"hostname": ziel.adresse, "port": str(ziel.port), "username": KONTO, "password": passwort})
    if any(p in parameter for p in VERBOTENE_PARAMETER):  # nur gegen künftige Änderungen an der Tabelle oben
        raise ValueError("verbotener Verbindungsparameter")
    daten = {
        "username": "rd-" + sitzung.replace("-", "")[:12],
        "expires": int((jetzt + gueltig_secs) * 1000),
        "connections": {VERBINDUNG: {"protocol": parameter.pop("protocol"), "parameters": parameter}},
    }
    roh = json.dumps(daten, separators=(",", ":")).encode("utf-8")
    signatur = hmac.new(schluessel, roh, hashlib.sha256).digest()
    polster = padding.PKCS7(128).padder()
    klar = polster.update(signatur + roh) + polster.finalize()
    verschluesseln = Cipher(algorithms.AES(schluessel), modes.CBC(bytes(16))).encryptor()
    return base64.b64encode(verschluesseln.update(klar) + verschluesseln.finalize()).decode("ascii")


def seiten_url(basis: str, token: str) -> str:
    """Adresse der Gateway-Seite: das Sitzungstoken als Abfrage, die Verbindung im Fragment.

    Gemessen im Browser gegen Guacamole 1.6.0: Nur ein Token VOR dem Fragment (/?token=...#/client/...) nimmt die Seite an und öffnet
    die Verbindung sofort; ein Token hinter dem Fragment (#/client/...?token=...) zeigt die Anmeldeseite. Das Token gelangt so als
    Abfrage an den Server des Gateways (Caddy protokolliert keine Zugriffe, Referrer-Policy no-referrer); es gilt nur für diese
    Sitzung und wird mit ihrem Ende ungültig."""
    kennung = base64.b64encode(f"{VERBINDUNG}\0c\0json".encode()).decode("ascii")
    return f"{basis}/?token={token}#/client/{kennung}"


# ======================================================================== Zugangsdienst
class GatewayFehler(Exception):
    """Der Zugangsdienst ist nicht erreichbar oder hat die Anfrage abgelehnt. code: kurzer Grund ohne Details."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def gateway(anfrage: dict, zeitlimit: float = RELAY_TIMEOUT_SECS) -> dict:
    """Eine Zeile JSON an den Zugangsdienst (Unix-Socket) und die Antwortzeile zurück. Wirft GatewayFehler."""
    try:
        async with asyncio.timeout(zeitlimit):
            leser, schreiber = await asyncio.open_unix_connection(socket_pfad(), limit=RELAY_MAX_ANTWORT)
            try:
                schreiber.write(json.dumps(anfrage, separators=(",", ":")).encode() + b"\n")
                await schreiber.drain()
                zeile = await leser.readline()
            finally:
                schreiber.close()
    except (OSError, TimeoutError, ValueError, asyncio.LimitOverrunError) as e:
        raise GatewayFehler("nicht_erreichbar") from e
    try:
        antwort = json.loads(zeile.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise GatewayFehler("antwort_ungueltig") from e
    if not isinstance(antwort, dict):
        raise GatewayFehler("antwort_ungueltig")
    if antwort.get("ok") is not True:
        code = antwort.get("code")
        raise GatewayFehler(code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,40}", code) else "abgelehnt")
    return antwort


_proben: dict[str, bool] = {}
_proben_stand = -1e9


async def erreichbarkeit(ziele: dict[str, Ziel]) -> dict[str, bool]:
    """Welche Ziele lassen sich vom Gateway aus erreichen (TCP-Probe des Zugangsdienstes, 15 Sekunden gemerkt)."""
    global _proben, _proben_stand
    if monotonic() - _proben_stand < PROBEN_GUELTIG_SECS and set(_proben) == set(ziele):
        return _proben
    try:
        antwort = await gateway({"cmd": "proben"}, zeitlimit=10.0)
        roh = antwort.get("ziele")
        neu = {n: bool(roh.get(n)) for n in ziele} if isinstance(roh, dict) else {n: False for n in ziele}
    except GatewayFehler:
        neu = {n: False for n in ziele}
    _proben, _proben_stand = neu, monotonic()
    return neu


# ======================================================================== Allgemeines
def _drosseln(user_id: str, art: str, grenze: tuple[int, float], jetzt: float | None = None) -> None:
    anzahl, fenster = grenze
    jetzt = monotonic() if jetzt is None else jetzt
    q = _versuche.setdefault((user_id, art), deque())
    while q and jetzt - q[0] > fenster:
        q.popleft()
    if len(q) >= anzahl:
        raise HTTPException(429, "Zu viele Anfragen, bitte in ein paar Minuten noch einmal")
    q.append(jetzt)
    if len(_versuche) > 200:
        for k in [k for k, d in _versuche.items() if not d or jetzt - d[-1] > 600.0]:
            del _versuche[k]


async def _admin(request: Request):
    ich = await identitaet(request)
    if not ich.is_admin:
        raise HTTPException(403, "Der Fernzugriff ist nur für Admins")
    return ich


def _db():
    try:
        db = shared_database()
    except ValueError as e:
        raise HTTPException(503, "Datenbank nicht eingerichtet") from e
    if not db.configured:
        raise HTTPException(503, "Datenbank nicht eingerichtet")
    return db


def _zeit(wert: object) -> str | None:
    return wert.isoformat() if isinstance(wert, datetime) else None


def _kurz(wert: str) -> str:
    return wert[:8]


def _aal2_abgelehnt(zeit: float | None = None) -> JSONResponse:
    """403 mit dem Code, nach dem die Oberfläche den Codefeld-Dialog zeigt. Ist die Stufe die Ursache (die Anmeldung ist älter als
    das Höchstalter), nennt der Satz das Alter, damit die Person den Grund sieht."""
    grenze = anmeldung_max_secs(melden=False)
    satz = f"Deine Anmeldung ist älter als {_dauer_text(grenze)}. " if zeit is not None and grenze else ""
    return JSONResponse({"detail": satz + "Bitte gib den Code deiner Authenticator-App noch einmal ein. "
                                           "Er darf höchstens zwei Minuten alt sein.", "code": CODE_AAL2},
                        status_code=403, headers={"Cache-Control": "no-store"})


def _nicht_aktiv() -> HTTPException:
    return HTTPException(503, "Der Fernzugriff ist auf diesem Kern nicht eingeschaltet")


def _sitzung_id(wert: object) -> str:
    try:
        if isinstance(wert, str) and len(wert) == 36:
            return str(uuid.UUID(wert))
    except ValueError:
        pass
    raise HTTPException(400, "Sitzung ungültig")


# ======================================================================== Routen
@router.get("/rd/ziele")
async def liste(request: Request, response: Response) -> dict:
    ich = await _admin(request)
    _drosseln(ich.user_id, "liste", LISTE_GRENZE)
    response.headers["Cache-Control"] = "no-store"
    if not aktiv():
        return {"aktiv": False, "ziele": [], "meine": None, "max_minuten": DAUER_SECS // 60,
                "leerlauf_minuten": LEERLAUF_MINUTEN}
    ziele = ziele_laden()
    db = _db()
    try:
        offen = await db.fetch(
            f"select s.id::text as id, s.profil_id::text as profil_id, s.ziel, s.start_at, s.bis_at, p.display_name "
            f"from {db.t('rd_sitzungen')} s left join {db.t('profiles')} p on p.user_id = s.profil_id "
            f"where s.ende_at is null")
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        logger.warning("Fernzugriff: Liste fehlgeschlagen ({})", type(e).__name__)
        raise HTTPException(503, "Die Liste der Ziele ist gerade nicht verfügbar") from e
    belegung = {r["ziel"]: r for r in offen}
    erreichbar = await erreichbarkeit(ziele)
    meine = None
    aus = []
    for z in ziele.values():
        b = belegung.get(z.name)
        gehoert_mir = b is not None and b["profil_id"] == ich.user_id
        if gehoert_mir:
            meine = {"sitzung": b["id"], "ziel": z.name, "anzeige": z.anzeige, "start": _zeit(b["start_at"]),
                     "endet": _zeit(b["bis_at"])}
        if b is not None:
            status = "belegt"
        elif erreichbar.get(z.name) and passwort_fuer(z.name) is not None:
            status = "frei"
        else:
            status = "nicht_erreichbar"
        aus.append({"name": z.name, "anzeige": z.anzeige, "status": status, "eigene": gehoert_mir,
                    "belegt_von": (b["display_name"] if b is not None and not gehoert_mir else None),
                    "seit": _zeit(b["start_at"]) if b is not None else None})
    return {"aktiv": True, "ziele": aus, "meine": meine, "max_minuten": DAUER_SECS // 60,
            "leerlauf_minuten": LEERLAUF_MINUTEN}


@router.post("/rd/start", response_model=None)
async def start(request: Request, response: Response) -> dict | JSONResponse:
    ich = await _admin(request)
    _drosseln(ich.user_id, "start", START_GRENZE)
    response.headers["Cache-Control"] = "no-store"
    if not aktiv():
        raise _nicht_aktiv()
    body = await lies_json(request, MAX_BODY)
    name = body.get("ziel")
    ziele = ziele_laden()
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name not in ziele:
        raise HTTPException(404, "Dieses Ziel gibt es nicht")
    ziel = ziele[name]

    zeit = aal2_zeit(bearer(request))
    if not anmeldung_genuegt(zeit):
        logger.info("Fernzugriff: Start {} abgelehnt, Code der Anmeldung fehlt oder ist zu alt (Profil {})", name, _kurz(ich.user_id))
        return _aal2_abgelehnt(zeit)

    passwort = passwort_fuer(name)
    schluessel = schluessel_lesen()
    if passwort is None or schluessel is None:
        logger.error("Fernzugriff: Zugangsdaten für {} unvollständig (Passwort {}, Schlüssel {})", name,
                     "da" if passwort else "fehlt", "da" if schluessel else "fehlt")
        raise HTTPException(503, "Dieses Ziel ist noch nicht eingerichtet")
    if not (await erreichbarkeit(ziele)).get(name):
        raise HTTPException(409, f"{ziel.anzeige} ist gerade nicht erreichbar")

    db = _db()
    # Befund M3: Datenbank und Gateway je Profil nacheinander. Ohne "ein Code, ein Start" löst ein zweiter Start die Zeile des
    # ersten ab; geschähe das vor dessen Anmeldung am Gateway, liefe das Beenden dort ins Leere (Zuschauer ohne offene Zeile).
    async with _start_sperre(ich.user_id):
        zeile = await _sitzung_beginnen(db, ich.user_id, ziel, zeit)
        if isinstance(zeile, JSONResponse):
            return zeile
        sitzung = zeile["sitzung_id"]
        if zeile["abgeloest_id"]:
            # dieselbe Person startet neu: die alte Sitzung am Gateway schließen (Fehler sind folgenlos, sie läuft dort aus)
            try:
                await gateway({"cmd": "beenden", "sitzung": zeile["abgeloest_id"], "grund": "abgeloest"})
            except GatewayFehler:
                logger.warning("Fernzugriff: alte Sitzung {} am Gateway nicht bestätigt", _kurz(zeile["abgeloest_id"]))
        url = await _am_gateway_anmelden(db, sitzung, ziel, passwort, schluessel, ich.user_id)
    logger.info("Fernzugriff: gestartet ziel={} sitzung={} profil={}", name, _kurz(sitzung), _kurz(ich.user_id))
    return {"ok": True, "sitzung": sitzung, "ziel": name, "anzeige": ziel.anzeige, "url": url,
            "endet": _zeit(zeile["bis_at"]), "max_minuten": DAUER_SECS // 60, "leerlauf_minuten": LEERLAUF_MINUTEN}


async def _sitzung_beginnen(db, profil: str, ziel: Ziel, zeit: float):
    """Datenbank: Sitzung anlegen (rd_sitzung_beginnen). Gibt die Zeile bei "ok" zurück, sonst wird mit dem passenden Status
    abgelehnt (HTTPException) oder bei zu altem oder fehlendem Code die Codeabfrage geliefert (JSONResponse)."""
    try:
        zeile = await db.fetchrow(
            f"select ergebnis, sitzung_id::text as sitzung_id, abgeloest_id::text as abgeloest_id, belegt_von, belegt_seit, bis_at "
            f"from {db.t('rd_sitzung_beginnen')}($1::uuid, $2, $3, $4::timestamptz, $5, $6)",
            profil, ziel.name, DAUER_SECS, datetime.fromtimestamp(zeit, tz=timezone.utc), MAX_GLEICHZEITIG, MAX_JE_STUNDE)
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        code = getattr(e, "sqlstate", None)
        logger.warning("Fernzugriff: Start {} fehlgeschlagen ({}, {})", ziel.name, type(e).__name__, code)
        if code == "42501":
            raise HTTPException(403, "Dir fehlt die Berechtigung: Nur aktive Admins nutzen den Fernzugriff") from e
        if code == "23514":
            return _aal2_abgelehnt()
        if code in ("22023", "22004"):
            raise HTTPException(400, "Die Angaben wurden von der Datenbank abgelehnt") from e
        raise HTTPException(503, "Die Datenbank hat nicht sauber geantwortet. Bitte gleich noch einmal versuchen.") from e
    ergebnis = zeile["ergebnis"] if zeile is not None else None
    if ergebnis == "belegt":
        seit = zeile["belegt_seit"]
        wer = zeile["belegt_von"] or "einer anderen Person"
        wann = f" seit {seit.astimezone().strftime('%H:%M')} Uhr" if isinstance(seit, datetime) else ""
        raise HTTPException(409, f"{ziel.anzeige} ist belegt von {wer}{wann}. Bitte warte, bis die Sitzung endet.")
    if ergebnis == "rate":
        raise HTTPException(429, f"Du hast in der letzten Stunde schon {MAX_JE_STUNDE} Fernsitzungen gestartet. Bitte später noch einmal.")
    if ergebnis == "voll":
        raise HTTPException(409, f"Es laufen schon {MAX_GLEICHZEITIG} Fernsitzungen. Bitte warte, bis eine endet.")
    if ergebnis != "ok" or zeile["sitzung_id"] is None:
        logger.error("Fernzugriff: unerwartete Antwort der Datenbank")
        raise HTTPException(503, "Unerwartete Antwort der Datenbank")
    return zeile


async def _am_gateway_anmelden(db, sitzung: str, ziel: Ziel, passwort: str, schluessel: bytes, profil: str) -> str:
    """Anweisung bauen, am Gateway anmelden und die Adresse der Gateway-Seite liefern. Danach prüft die Datenbankzeile noch einmal:
    Hat ein Start über einen anderen Kern (Slot a oder b, kein gemeinsamer Speicher und Lock) sie inzwischen abgelöst, wird die
    Gateway-Sitzung sofort beendet und die Adresse nie ausgeliefert. So bleibt höchstens ein Zuschauer je Ziel (Befund M3)."""
    try:
        data = anweisung_bauen(schluessel, sitzung, ziel, passwort)
        antwort = await gateway({"cmd": "anmelden", "sitzung": sitzung, "data": data, "max_secs": DAUER_SECS})
        token = antwort.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
            raise GatewayFehler("antwort_ungueltig")
        url = seiten_url(basis_url(), token)
    except (GatewayFehler, HTTPException, ValueError) as e:
        grund = e.code if isinstance(e, GatewayFehler) else type(e).__name__
        logger.warning("Fernzugriff: Anmeldung am Gateway für {} fehlgeschlagen ({})", ziel.name, grund)
        await _beenden_still(db, sitzung, "fehler")
        raise HTTPException(502 if isinstance(e, GatewayFehler) and e.code != "nicht_erreichbar" else 503,
                            "Das Gateway hat die Verbindung nicht angenommen. Bitte gleich noch einmal versuchen.") from e
    offen = await _noch_offen(db, sitzung, profil)
    if offen is True:
        return url
    grund = "abgeloest" if offen is False else "fehler"
    logger.warning("Fernzugriff: Sitzung {} nach dem Anmelden {}, Gateway-Sitzung wird beendet", _kurz(sitzung),
                   "abgelöst" if offen is False else "nicht prüfbar")
    try:
        await gateway({"cmd": "beenden", "sitzung": sitzung, "grund": grund})
    except GatewayFehler:
        logger.warning("Fernzugriff: Ende von {} am Gateway nicht bestätigt", _kurz(sitzung))
    if offen is None:
        await _beenden_still(db, sitzung, "fehler")
        raise HTTPException(503, "Die Datenbank hat nicht geantwortet. Bitte gleich noch einmal versuchen.")
    raise HTTPException(409, "Dieser Start wurde durch einen neueren Start abgelöst. Bitte starte die Verbindung nur einmal.")


async def _noch_offen(db, sitzung: str, profil: str) -> bool | None:
    """Ist die Zeile dieser Sitzung beim selben Profil noch offen? None, wenn die Datenbank nicht antwortet (dann gilt: nicht offen)."""
    try:
        return await db.fetchval(
            f"select 1 from {db.t('rd_sitzungen')} where id = $1::uuid and profil_id = $2::uuid and ende_at is null",
            sitzung, profil) is not None
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        logger.warning("Fernzugriff: Zeile von {} nicht prüfbar ({})", _kurz(sitzung), type(e).__name__)
        return None


async def _beenden_still(db, sitzung: str, grund: str, profil: str | None = None) -> str | None:
    """Sitzung in der Datenbank beenden; gibt das Ergebnis zurück oder None bei einem Fehler (wird geloggt)."""
    try:
        return await db.fetchval(f"select {db.t('rd_sitzung_beenden')}($1::uuid, $2, $3::uuid)", sitzung, grund, profil)
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        logger.warning("Fernzugriff: Ende von {} nicht gespeichert ({})", _kurz(sitzung), type(e).__name__)
        return None


@router.post("/rd/ende", response_model=None)
async def ende(request: Request, response: Response) -> dict:
    ich = await _admin(request)
    _drosseln(ich.user_id, "ende", ENDE_GRENZE)
    response.headers["Cache-Control"] = "no-store"
    if not aktiv():
        raise _nicht_aktiv()
    sitzung = _sitzung_id((await lies_json(request, MAX_BODY)).get("sitzung"))
    db = _db()
    try:
        besitzer = await db.fetchval(
            f"select profil_id::text from {db.t('rd_sitzungen')} where id = $1::uuid and ende_at is null", sitzung)
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        logger.warning("Fernzugriff: Ende nicht lesbar ({})", type(e).__name__)
        raise HTTPException(503, "Die Datenbank hat nicht geantwortet. Bitte gleich noch einmal versuchen.") from e
    if besitzer is not None and besitzer != ich.user_id:
        raise HTTPException(403, "Diese Sitzung gehört einer anderen Person")
    bestaetigt = True
    if besitzer is not None:
        try:
            await gateway({"cmd": "beenden", "sitzung": sitzung, "grund": "beendet"})
        except GatewayFehler as e:
            bestaetigt = False
            logger.warning("Fernzugriff: Ende von {} am Gateway nicht bestätigt ({})", _kurz(sitzung), e.code)
    ergebnis = await _beenden_still(db, sitzung, "beendet", ich.user_id)
    if ergebnis is None:
        raise HTTPException(503, "Das Ende konnte nicht gespeichert werden. Bitte gleich noch einmal versuchen.")
    if ergebnis == "fremd":
        raise HTTPException(403, "Diese Sitzung gehört einer anderen Person")
    if ergebnis == "unbekannt":
        raise HTTPException(404, "Diese Sitzung gibt es nicht")
    logger.info("Fernzugriff: beendet sitzung={} profil={} ergebnis={}", _kurz(sitzung), _kurz(ich.user_id), ergebnis)
    return {"ok": True, "ergebnis": ergebnis, "gateway_bestaetigt": bestaetigt}


# ======================================================================== Aufseher (Hintergrund)
async def aufseher_runde(db=None, jetzt: datetime | None = None) -> int:
    """Eine Runde: offene Sitzungen mit dem Gateway abgleichen und beenden, was abgelaufen, getrennt oder nicht mehr erlaubt ist.

    Gibt die Anzahl der beendeten Sitzungen zurück. Das Gateway (Zugangsdienst) beendet Zeitlimit und Trennung selbst; hier
    wird nur das Protokoll nachgeführt und die Belegung freigegeben. Fehler eines Teils (Gateway, Datenbank) schließen nie
    eine Sitzung ungeprüft, nur das Höchstende plus Nachfrist gilt immer."""
    db = db or _db()
    jetzt = jetzt or datetime.now(timezone.utc)
    offen = await db.fetch(
        f"select id::text as id, profil_id::text as profil_id, ziel, start_at, bis_at from {db.t('rd_sitzungen')} "
        f"where ende_at is null")
    if not offen:
        return 0
    admins = {r["user_id"] for r in await db.fetch(
        f"select user_id::text as user_id from {db.t('profiles')} where aktiv and rolle = 'admin' and user_id = any($1::uuid[])",
        list({r["profil_id"] for r in offen}))}
    beendet = 0
    for r in offen:
        sitzung, alter = r["id"], (jetzt - r["start_at"]).total_seconds()
        grund: str | None = None
        gateway_gesehen: bool | None = None
        if (jetzt - r["bis_at"]).total_seconds() > ZEITLIMIT_NACHFRIST_SECS:
            grund = "zeitlimit"
        elif r["profil_id"] not in admins:
            grund = "profil_inaktiv"
        elif alter >= NEUE_SITZUNG_SCHONZEIT_SECS:
            try:
                status = await gateway({"cmd": "status", "sitzung": sitzung}, zeitlimit=10.0)
                gateway_gesehen = True
                if status.get("offen") is False:
                    gr = status.get("grund")
                    grund = gr if gr in ("beendet", "zeitlimit", "getrennt", "nicht_geoeffnet", "abgeloest",
                                         "profil_inaktiv", "fehler") else "fehler"
            except GatewayFehler:
                gateway_gesehen = False
        if grund is None and gateway_gesehen is False and (jetzt - r["bis_at"]).total_seconds() > OHNE_GATEWAY_NACHFRIST_SECS:
            grund = "zeitlimit"
        if grund is None:
            continue
        if grund in ("zeitlimit", "profil_inaktiv"):
            try:  # das Gateway soll den Tunnel schließen; fällt es aus, gilt seine eigene Frist
                await gateway({"cmd": "beenden", "sitzung": sitzung, "grund": grund}, zeitlimit=10.0)
            except GatewayFehler:
                logger.warning("Fernzugriff: Ende von {} am Gateway nicht bestätigt", _kurz(sitzung))
        if await _beenden_still(db, sitzung, grund) == "ok":
            beendet += 1
            logger.info("Fernzugriff: beendet sitzung={} ziel={} grund={}", _kurz(sitzung), r["ziel"], grund)
    return beendet


async def aufseher() -> None:
    """Hintergrundaufgabe des Kerns (wamo_api: hintergrund). Läuft nur, wenn der Fernzugriff eingeschaltet ist."""
    while True:
        await asyncio.sleep(AUFSEHER_TAKT_SECS)
        if not aktiv():
            continue
        try:
            await aufseher_runde()
        except (HTTPException, *DB_ERRORS, DatabaseUnavailable) as e:
            logger.warning("Fernzugriff: Aufseher fehlgeschlagen ({})", type(e).__name__)


hintergrund = [aufseher]
