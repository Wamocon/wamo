"""TOTP-Berechnung für scripts/wamo_labor_faktoren.py (nur Standardbibliothek).

RFC 6238 mit SHA1, 30 Sekunden und 6 Stellen, wie es GoTrue und jede Authenticator-App rechnen. Dazu die Hilfen für den
Zeitversatz zwischen dem PC und dem Server: Die Uhr des Servers steht im Kopf Date jeder Antwort, verglichen wird sie mit der
lokalen Uhr, und nur dieser Unterschied fließt in die Berechnung ein.

Nichts in diesem Modul gibt etwas aus. Seeds und Codes bleiben beim Aufrufer.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import struct
from email.utils import parsedate_to_datetime
from typing import Callable

PERIODE = 30
STELLEN = 6
MIN_SEED_ZEICHEN = 16  # 80 Bit, GoTrue liefert 32 Zeichen
MAX_VERSATZ_S = 86_400  # ein größerer Unterschied stammt aus einem kaputten Kopf und wird nicht berücksichtigt
_SEED_FORM = re.compile(r"[A-Z2-7]+=*")


def seed_normalisieren(seed: str) -> str:
    return "".join(seed.split()).upper()


def seed_gueltig(seed: object) -> bool:
    """Ein Base32-Seed (A bis Z, 2 bis 7, Leerzeichen und Kleinschreibung erlaubt) mit mindestens 16 Zeichen."""
    if not isinstance(seed, str):
        return False
    sauber = seed_normalisieren(seed)
    if len(sauber.rstrip("=")) < MIN_SEED_ZEICHEN or not _SEED_FORM.fullmatch(sauber):
        return False
    try:
        base64.b32decode(sauber + "=" * (-len(sauber) % 8))
    except (binascii.Error, ValueError):
        return False
    return True


def totp_code(seed: str, zeit: float, stellen: int = STELLEN) -> str:
    """Der Code für den Zeitpunkt zeit (Sekunden seit 1970). Mit stellen=8 entstehen die Werte der Testvektoren aus dem RFC."""
    sauber = seed_normalisieren(seed)
    schluessel = base64.b32decode(sauber + "=" * (-len(sauber) % 8))
    zaehler = int(zeit // PERIODE)
    h = hmac.new(schluessel, struct.pack(">Q", zaehler), hashlib.sha1).digest()
    ab = h[-1] & 0x0F
    wert = struct.unpack(">I", h[ab:ab + 4])[0] & 0x7FFFFFFF
    return f"{wert % 10 ** stellen:0{stellen}d}"


def ablauf_sekunden(zeit: float) -> int:
    """Wie viele Sekunden der Code des Fensters, in dem zeit liegt, noch gilt (1 bis 30)."""
    return max(1, math.ceil(PERIODE - (zeit % PERIODE)))


def zeitversatz(date_kopf: str | None, lokal_mitte: float) -> float:
    """Server minus lokale Uhr in Sekunden (positiv: der Server geht vor).

    date_kopf ist der Kopf Date der Antwort (ganze Sekunden, abgeschnitten), lokal_mitte die lokale Zeit in der Mitte zwischen
    Senden der Anfrage und Empfang der Antwort. Ohne brauchbaren Kopf ist der Versatz 0. Die halbe Sekunde gleicht das
    Abschneiden aus.
    """
    if not date_kopf:
        return 0.0
    try:
        server = parsedate_to_datetime(date_kopf).timestamp() + 0.5
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0.0
    versatz = server - lokal_mitte
    return versatz if abs(versatz) <= MAX_VERSATZ_S else 0.0


def frisches_fenster(jetzt: Callable[[], float], warte: Callable[[float], None], reserve: float = 2.0) -> float:
    """Die Zeit für den nächsten Code. Endet das Fenster in weniger als reserve Sekunden, wird bis zum nächsten gewartet,
    damit der Code beim Server nicht gerade abläuft."""
    t = jetzt()
    rest = PERIODE - (t % PERIODE)
    if rest < reserve:
        warte(rest + 0.2)
        t = jetzt()
    return t


def jwt_claims(token: object) -> dict:
    """Die Angaben (Claims) eines JWT, ohne die Signatur zu prüfen (die Verbindung ist per TLS geprüft). Bei jedem Fehler leer."""
    if not isinstance(token, str):
        return {}
    teile = token.split(".")
    if len(teile) < 2:
        return {}
    try:
        roh = base64.urlsafe_b64decode(teile[1] + "=" * (-len(teile[1]) % 4))
        daten = json.loads(roh)
    except (ValueError, binascii.Error):
        return {}
    return daten if isinstance(daten, dict) else {}
