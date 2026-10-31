"""Fernzugriff: Remote Desktop der Sparks über das Gateway auf Sokrates-2 (docs/remote-desktop.md, Stufe 3).

  GET  /wamo/api/rd/ziele   Ziele mit Anzeigename und Zustand (frei | belegt | nicht_erreichbar), eigene Sitzung (nur Rolle admin)
  POST /wamo/api/rd/start   {"ziel": "sokrates2"}  startet eine Fernsitzung und liefert die Adresse der Gateway-Seite
  POST /wamo/api/rd/ende    {"sitzung": "<uuid>"}  beendet die eigene Fernsitzung

Wer eine Sitzung starten darf (alles muss stimmen, geprüft in dieser Reihenfolge):
  * Anmeldung wie bei allen Routen: Token mit zweitem Faktor, Höchstalter, aktives Profil (wamo_api.identitaet).
  * Rolle admin. Die Datenbank prüft sie in rd_sitzung_beginnen noch einmal (aktives Profil, Rolle admin).
  * Frischer zweiter Faktor bei JEDEM Start: Der Code der Authenticator-App ist höchstens 120 Sekunden alt (Claim amr, wie in
    geraete_admin_api.py; die Oberfläche fragt den Code im Dialog ab und bestätigt ihn bei Supabase). Auch die Datenbank prüft die Zeit.
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
import os
import re
import tomllib
import uuid
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

from geraete_admin_api import CODE_AAL2, aal2_frisch, aal2_zeit
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

LISTE_GRENZE = (60, 60.0)       # Aufrufe, Sekunden
START_GRENZE = (10, 600.0)
ENDE_GRENZE = (20, 600.0)
_versuche: dict[tuple[str, str], deque[float]] = {}

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


def _aal2_abgelehnt() -> JSONResponse:
    return JSONResponse({"detail": "Bitte gib den Code deiner Authenticator-App noch einmal ein. "
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
    if not aal2_frisch(zeit):
        logger.info("Fernzugriff: Start {} abgelehnt, zweiter Faktor zu alt (Profil {})", name, _kurz(ich.user_id))
        return _aal2_abgelehnt()

    passwort = passwort_fuer(name)
    schluessel = schluessel_lesen()
    if passwort is None or schluessel is None:
        logger.error("Fernzugriff: Zugangsdaten für {} unvollständig (Passwort {}, Schlüssel {})", name,
                     "da" if passwort else "fehlt", "da" if schluessel else "fehlt")
        raise HTTPException(503, "Dieses Ziel ist noch nicht eingerichtet")
    if not (await erreichbarkeit(ziele)).get(name):
        raise HTTPException(409, f"{ziel.anzeige} ist gerade nicht erreichbar")

    db = _db()
    try:
        zeile = await db.fetchrow(
            f"select ergebnis, sitzung_id::text as sitzung_id, abgeloest_id::text as abgeloest_id, belegt_von, belegt_seit, bis_at "
            f"from {db.t('rd_sitzung_beginnen')}($1::uuid, $2, $3, $4::timestamptz, $5, $6)",
            ich.user_id, name, DAUER_SECS, datetime.fromtimestamp(zeit, tz=timezone.utc), MAX_GLEICHZEITIG, MAX_JE_STUNDE)
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        code = getattr(e, "sqlstate", None)
        logger.warning("Fernzugriff: Start {} fehlgeschlagen ({}, {})", name, type(e).__name__, code)
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
    sitzung = zeile["sitzung_id"]
    if zeile["abgeloest_id"]:
        # dieselbe Person startet neu: die alte Sitzung am Gateway schließen (Fehler sind folgenlos, sie läuft dort aus)
        try:
            await gateway({"cmd": "beenden", "sitzung": zeile["abgeloest_id"], "grund": "abgeloest"})
        except GatewayFehler:
            logger.warning("Fernzugriff: alte Sitzung {} am Gateway nicht bestätigt", _kurz(zeile["abgeloest_id"]))

    try:
        data = anweisung_bauen(schluessel, sitzung, ziel, passwort)
        antwort = await gateway({"cmd": "anmelden", "sitzung": sitzung, "data": data, "max_secs": DAUER_SECS})
        token = antwort.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
            raise GatewayFehler("antwort_ungueltig")
        url = seiten_url(basis_url(), token)
    except (GatewayFehler, HTTPException, ValueError) as e:
        grund = e.code if isinstance(e, GatewayFehler) else type(e).__name__
        logger.warning("Fernzugriff: Anmeldung am Gateway für {} fehlgeschlagen ({})", name, grund)
        await _beenden_still(db, sitzung, "fehler")
        raise HTTPException(502 if isinstance(e, GatewayFehler) and e.code != "nicht_erreichbar" else 503,
                            "Das Gateway hat die Verbindung nicht angenommen. Bitte gleich noch einmal versuchen.") from e
    logger.info("Fernzugriff: gestartet ziel={} sitzung={} profil={}", name, _kurz(sitzung), _kurz(ich.user_id))
    return {"ok": True, "sitzung": sitzung, "ziel": name, "anzeige": ziel.anzeige, "url": url,
            "endet": _zeit(zeile["bis_at"]), "max_minuten": DAUER_SECS // 60, "leerlauf_minuten": LEERLAUF_MINUTEN}


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
