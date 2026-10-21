"""Bindung des eigenen Laptops bei der Anmeldung in der Desktop-App (Büro-Kern).

  GET  /wamo/api/geraet/herausforderung   Einmal-Herausforderung für die Bindung (an das Profil gebunden, 5 Minuten)
  POST /wamo/api/geraet/binden            Gerät binden: Schlüssel, Rechnername, Signatur der Herausforderung

Ablauf: Die Desktop-App erzeugt einen eigenen ed25519-Geräteschlüssel, holt eine Herausforderung und signiert
    "wamo-geraet-binden\\n" + nonce + "\\n" + user_id + "\\n" + hostname
mit dem privaten Schlüssel. Der Kern prüft die Anmeldung (Token mit zweitem Faktor, aktives Profil: wamo_api.identitaet),
die Herausforderung (HMAC, an die user_id gebunden, einmal benutzbar, 5 Minuten) und die Signatur (Besitz des
Schlüssels) und ruft erst dann geraet_binden der Datenbank auf. Die Regeln der Erstbindung stehen dort (Migration 0002):
automatisch aktiv nur für ein Mitglied ohne früheres Gerät mit neuem Schlüssel und freiem Rechnernamen, sonst wartet
die Bindung auf einen Admin; jede Bindung meldet sich bei allen Admins. Neubindung und Gerätewechsel macht nur ein Admin.

Antwort bei aktivem Gerät enthält das Tunnelziel (Benutzer wamo-t-NN, Socket, Hostschlüssel von Sokrates-2 aus
/etc/ssh/ssh_host_ed25519_key.pub, über das HTTPS des Kerns mit der WAMO-CA geliefert). IP und Rechnername sind nur
Anzeige; die Identität ist Schlüssel plus Anmeldung. Nur im Büro-Kern (WAMO_BETRIEB=buero).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections import deque
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from geraete_register import GeraeteRegister, RegisterFehler
from memory import shared_database
from wamo_api import identitaet, lies_json

router = APIRouter()

GUELTIG_SECS = 300
MAX_BODY = 8 * 1024
NACHRICHT_PRAEFIX = b"wamo-geraet-binden\n"
PUBKEY_RE = re.compile(r"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-P][A-Za-z0-9+/]{42}")  # wie devices_pubkey_chk
HOSTNAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}")
B64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
HOSTSCHLUESSEL_RE = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]{68}")
OKTETT = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"  # keine führenden Nullen (inet_aton läse sie oktal)
HOST_RE = re.compile(rf"{OKTETT}(?:\.{OKTETT}){{3}}")
STEUER_RE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁠-⁩﻿]")
HOSTSCHLUESSEL_DATEI = "/etc/ssh/ssh_host_ed25519_key.pub"
SSH_HOST_STANDARD = "192.168.178.136"
VERSUCHE = 20  # je Profil, Schritt (Herausforderung, Bindung) und Zeitfenster; die Oberfläche fragt höchstens jede Minute
VERSUCHE_FENSTER_SECS = 600.0

_GEHEIMNIS = secrets.token_bytes(32)  # je Prozess; nach einem Neustart sind alte Herausforderungen ungültig
_benutzt: dict[str, int] = {}  # nonce -> Ablauf (Unix-Sekunden)
_versuche: dict[tuple[str, str], deque[float]] = {}


def _b64url(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).decode("ascii").rstrip("=")


def _b64url_dec(text: str) -> bytes | None:
    if not B64URL_RE.fullmatch(text or ""):
        return None
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        return None


def _mac(user_id: str, kern: bytes) -> bytes:
    return hmac.new(_GEHEIMNIS, b"wamo-geraet-nonce\0" + user_id.encode("ascii") + b"\0" + kern, hashlib.sha256).digest()[:16]


def nonce_erzeugen(user_id: str, jetzt: float | None = None) -> str:
    ablauf = int((time.time() if jetzt is None else jetzt) + GUELTIG_SECS)
    kern = secrets.token_bytes(16) + ablauf.to_bytes(8, "big")
    return _b64url(kern + _mac(user_id, kern))


def nonce_verbrauchen(nonce: str, user_id: str, jetzt: float | None = None) -> bool:
    """Gültig (Form, MAC für dieses Profil, nicht abgelaufen) und noch nicht benutzt? Verbraucht sie dabei."""
    jetzt = int(time.time() if jetzt is None else jetzt)
    roh = _b64url_dec(nonce)
    if roh is None or len(roh) != 40:
        return False
    if _b64url(roh) != nonce:  # nur die kanonische Schreibweise (die letzte Base64-Stelle hat freie Bits)
        return False
    kern, mac = roh[:24], roh[24:]
    if not hmac.compare_digest(mac, _mac(user_id, kern)):
        return False
    ablauf = int.from_bytes(kern[16:], "big")
    for alt in [n for n, a in _benutzt.items() if a < jetzt]:
        del _benutzt[alt]
    if ablauf < jetzt or nonce in _benutzt:
        return False
    if len(_benutzt) >= 5000:  # nie unbegrenzt wachsen
        return False
    _benutzt[nonce] = ablauf
    return True


def signatur_gueltig(pubkey: str, nachricht: bytes, signatur: bytes) -> bool:
    """Ed25519-Signatur mit dem öffentlichen Schlüssel aus "ssh-ed25519 AAAA..." prüfen."""
    teile = pubkey.split(" ")
    if len(teile) != 2 or not PUBKEY_RE.fullmatch(pubkey):
        return False
    try:
        blob = base64.b64decode(teile[1], validate=True)
    except ValueError:
        return False
    if len(blob) != 51 or blob[:15] != b"\x00\x00\x00\x0bssh-ed25519" or blob[15:19] != b"\x00\x00\x00\x20" or len(signatur) != 64:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(blob[19:]).verify(signatur, nachricht)
    except (InvalidSignature, ValueError):
        return False
    return True


def nachricht_bauen(nonce: str, user_id: str, hostname: str) -> bytes:
    return NACHRICHT_PRAEFIX + f"{nonce}\n{user_id}\n{hostname}".encode("utf-8")


def _drosseln(user_id: str, schritt: str, jetzt: float | None = None) -> None:
    jetzt = time.monotonic() if jetzt is None else jetzt
    q = _versuche.setdefault((user_id, schritt), deque())
    while q and jetzt - q[0] > VERSUCHE_FENSTER_SECS:
        q.popleft()
    if len(q) >= VERSUCHE:
        raise HTTPException(429, "Zu viele Versuche, bitte in ein paar Minuten noch einmal")
    q.append(jetzt)
    if len(_versuche) > 2000:  # abgelaufene Profile aufräumen
        for k in [k for k, d in _versuche.items() if not d or jetzt - d[-1] > VERSUCHE_FENSTER_SECS]:
            del _versuche[k]


def _nur_im_buero() -> None:
    if (os.getenv("WAMO_BETRIEB") or "").strip().lower() != "buero":
        raise HTTPException(404, "Die Gerätebindung gibt es nur im Kern im Büro")


def _register() -> GeraeteRegister:
    try:
        db = shared_database()
    except ValueError as e:
        raise HTTPException(503, "Datenbank nicht eingerichtet") from e
    if not db.configured:
        raise HTTPException(503, "Datenbank nicht eingerichtet")
    return GeraeteRegister(db)


_hostschluessel_cache: tuple[float, str] | None = None


def hostschluessel() -> str:
    """Öffentlicher ED25519-Hostschlüssel von Sokrates-2 (eine Zeile, ohne Kommentar). Wirft HTTPException 503."""
    global _hostschluessel_cache
    pfad = Path(os.getenv("WAMO_PC_SSH_HOSTKEY_DATEI") or HOSTSCHLUESSEL_DATEI)
    try:
        mtime = pfad.stat().st_mtime
        if _hostschluessel_cache and _hostschluessel_cache[0] == mtime:
            return _hostschluessel_cache[1]
        teile = pfad.read_text(encoding="ascii").split()
        wert = f"{teile[0]} {teile[1]}" if len(teile) >= 2 else ""
    except (OSError, UnicodeDecodeError, IndexError) as e:
        logger.warning("Gerätebindung: Hostschlüssel nicht lesbar ({})", type(e).__name__)
        raise HTTPException(503, "Hostschlüssel von Sokrates-2 gerade nicht lesbar") from e
    if not HOSTSCHLUESSEL_RE.fullmatch(wert):
        logger.error("Gerätebindung: Hostschlüssel hat ein unerwartetes Format")
        raise HTTPException(503, "Hostschlüssel von Sokrates-2 gerade nicht lesbar")
    _hostschluessel_cache = (mtime, wert)
    return wert


def ssh_host() -> str:
    wert = (os.getenv("WAMO_PC_SSH_HOST") or SSH_HOST_STANDARD).strip()
    if not HOST_RE.fullmatch(wert) or any(int(t) > 255 for t in wert.split(".")):
        logger.error("WAMO_PC_SSH_HOST ist keine IPv4-Adresse")
        raise HTTPException(503, "Tunnelziel nicht eingerichtet")
    return wert


def ziel_fuer(slot: int) -> dict:
    return {"benutzer": f"wamo-t-{slot:02d}", "host": ssh_host(), "socket": f"/run/wamo-pc/t{slot:02d}/pc.sock",
            "hostschluessel": hostschluessel()}


@router.get("/geraet/herausforderung")
async def herausforderung(request: Request) -> dict:
    _nur_im_buero()
    ich = await identitaet(request)
    _drosseln(ich.user_id, "herausforderung")
    return {"nonce": nonce_erzeugen(ich.user_id), "gueltig_s": GUELTIG_SECS}


def _text(wert: object, regex: re.Pattern, was: str) -> str:
    if not isinstance(wert, str) or not regex.fullmatch(wert):
        raise HTTPException(400, f"{was} ungültig")
    return wert


@router.post("/geraet/binden")
async def binden(request: Request) -> dict:
    _nur_im_buero()
    ich = await identitaet(request)
    body = await lies_json(request, MAX_BODY)
    _drosseln(ich.user_id, "binden")
    pubkey = _text(body.get("pubkey"), PUBKEY_RE, "Schlüssel")
    hostname = _text(body.get("hostname"), HOSTNAME_RE, "Rechnername")
    nonce = body.get("nonce")
    signatur = _b64url_dec(body.get("signatur") if isinstance(body.get("signatur"), str) else "")
    name = body.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 100 or STEUER_RE.search(name)):
        raise HTTPException(400, "Name ungültig")
    if not isinstance(nonce, str) or signatur is None:
        raise HTTPException(400, "Herausforderung oder Signatur fehlt")
    # Erst die Herausforderung verbrauchen (einmal benutzbar), dann die Signatur prüfen: wer den Schlüssel nicht besitzt,
    # kommt nicht zur Datenbank
    if not nonce_verbrauchen(nonce, ich.user_id):
        raise HTTPException(400, "Herausforderung abgelaufen oder schon benutzt, bitte noch einmal versuchen")
    if not signatur_gueltig(pubkey, nachricht_bauen(nonce, ich.user_id, hostname), signatur):
        logger.warning("Gerätebindung: Signatur ungültig (Profil {})", ich.user_id[:8])
        raise HTTPException(400, "Signatur ungültig: Der Schlüssel gehört nicht zu diesem Gerät")
    try:
        zeile = await _register().binden(ich.user_id, pubkey, hostname, name)
    except RegisterFehler as e:
        logger.warning("Gerätebindung: {}", e)
        raise HTTPException(503, "Die Bindung ist gerade nicht möglich. Bitte später noch einmal versuchen.") from e
    ergebnis, status = zeile.get("ergebnis"), zeile.get("status")
    if ergebnis == "fremd":
        raise HTTPException(403, "Dieses Gerät gehört einem anderen Profil. Ein Admin wurde informiert.")
    if ergebnis == "zu_viele":
        raise HTTPException(429, "Es gibt schon zu viele Geräte oder offene Anträge. Bitte einen Admin fragen.")
    if ergebnis not in ("neu_aktiv", "neu_beantragt", "bekannt") or status not in ("aktiv", "beantragt", "gesperrt", "widerrufen"):
        logger.error("Gerätebindung: unerwartete Antwort der Datenbank")
        raise HTTPException(503, "Die Bindung ist gerade nicht möglich. Bitte später noch einmal versuchen.")
    antwort = {"ergebnis": ergebnis, "status": status, "name": str(zeile.get("name") or "")[:100], "kennung": None,
               "slot": None, "stufe": "admin" if ich.is_admin else "mitglied", "user_id": ich.user_id, "ziel": None}
    if status == "aktiv":
        slot = zeile.get("tunnel_slot")
        kennung = zeile.get("kennung")
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 64 or not isinstance(kennung, str):
            raise HTTPException(503, "Die Bindung ist gerade nicht möglich. Bitte später noch einmal versuchen.")
        antwort.update(kennung=kennung, slot=slot, ziel=ziel_fuer(slot))
    logger.info("Gerätebindung: {} ({}, Profil {})", ergebnis, status, ich.user_id[:8])
    return antwort
