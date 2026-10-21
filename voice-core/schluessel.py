"""Verschlüsselung der eigenen Claude-Schlüssel je Profil (AES-256-GCM) und Schwärzen in Logs.

Der Hauptschlüssel WAMO_SCHLUESSEL_KEK (32 Byte, Base64url) liegt nur in der Umgebung des Kerns (core.env bzw.
.env des Kerns), nie in der Datenbank. Ein Chiffrat sieht so aus:

    b"w1" | Kennung des Hauptschlüssels (4 Byte) | Nonce (12 Byte) | GCM-Chiffrat mit Prüfsumme

Zusätzliche Daten (AAD) sind die Profil-ID: ein Chiffrat aus der Zeile eines anderen Profils lässt sich nicht
entschlüsseln, auch nicht von jemandem mit Schreibrecht auf die Tabelle. Für einen Wechsel des Hauptschlüssels
liest WAMO_SCHLUESSEL_KEK_ALT (kommagetrennt) die alten Chiffrate weiter; neue entstehen immer mit dem aktuellen.

Kein Text dieses Moduls (Fehler, Logs) enthält je einen Schlüssel; Fehler nennen nur die Art des Problems.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEK_ENV = "WAMO_SCHLUESSEL_KEK"
KEK_ALT_ENV = "WAMO_SCHLUESSEL_KEK_ALT"
KOPF = b"w1"
_KENNUNG_LAENGE = 4
_NONCE_LAENGE = 12
_AAD_PRAEFIX = b"wamo-claude-schluessel:"
_FORMAT = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,250}")
# Alles, was wie ein Anthropic-Schlüssel aussieht, in jeder Log-Zeile schwärzen (auch Teile ab 8 Zeichen)
_IM_TEXT = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")


class SchluesselFehler(Exception):
    """Schlüssel unbrauchbar oder nicht lesbar. Der Text enthält nie den Schlüssel."""


def kek_erzeugen() -> str:
    """Neuer Hauptschlüssel für WAMO_SCHLUESSEL_KEK (32 zufällige Byte, Base64url)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def _kek_lesen(wert: str) -> bytes:
    try:
        # streng: fremde Zeichen nicht stillschweigend überspringen (ein verstümmelter Wert fällt sofort auf)
        roh = base64.b64decode(wert.strip().encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise SchluesselFehler(f"{KEK_ENV} ist kein gültiges Base64") from None
    if len(roh) != 32:
        raise SchluesselFehler(f"{KEK_ENV} muss 32 Byte lang sein")
    return roh


def _kennung(kek: bytes) -> bytes:
    return hashlib.sha256(b"wamo-kek:" + kek).digest()[:_KENNUNG_LAENGE]


def _aad(user_id: str) -> bytes:
    try:
        return _AAD_PRAEFIX + str(uuid.UUID(str(user_id))).encode("ascii")
    except ValueError:
        raise SchluesselFehler("Profil-ID ist keine UUID") from None


class Tresor:
    """Ver- und Entschlüsseln mit dem Hauptschlüssel des Kerns."""

    def __init__(self, kek: bytes, alte: tuple[bytes, ...] = ()):
        self._aktuell = kek
        self._alle = {_kennung(k): k for k in (kek, *alte)}

    @classmethod
    def from_env(cls) -> Tresor | None:
        """None, wenn kein Hauptschlüssel gesetzt ist (dann gibt es keine eigenen Schlüssel)."""
        wert = (os.getenv(KEK_ENV) or "").strip()
        if not wert:
            return None
        alte = tuple(_kek_lesen(w) for w in (os.getenv(KEK_ALT_ENV) or "").split(",") if w.strip())
        return cls(_kek_lesen(wert), alte)

    @property
    def kennung(self) -> str:
        return _kennung(self._aktuell).hex()

    def verschluesseln(self, user_id: str, klartext: str) -> bytes:
        nonce = secrets.token_bytes(_NONCE_LAENGE)
        chiffre = AESGCM(self._aktuell).encrypt(nonce, klartext.encode("utf-8"), _aad(user_id))
        return KOPF + _kennung(self._aktuell) + nonce + chiffre

    def entschluesseln(self, user_id: str, daten: bytes) -> str:
        daten = bytes(daten or b"")
        kopf_ende = len(KOPF) + _KENNUNG_LAENGE
        if len(daten) < kopf_ende + _NONCE_LAENGE + 16 or not daten.startswith(KOPF):
            raise SchluesselFehler("Chiffrat hat ein unbekanntes Format")
        kek = self._alle.get(daten[len(KOPF):kopf_ende])
        if kek is None:
            raise SchluesselFehler("Chiffrat stammt von einem unbekannten Hauptschlüssel")
        nonce, chiffre = daten[kopf_ende:kopf_ende + _NONCE_LAENGE], daten[kopf_ende + _NONCE_LAENGE:]
        try:
            return AESGCM(kek).decrypt(nonce, chiffre, _aad(user_id)).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError):
            raise SchluesselFehler("Chiffrat ist beschädigt oder gehört zu einem anderen Profil") from None


def format_pruefen(schluessel: object) -> str:
    """Der Schlüssel ohne Rand, wenn er wie ein Anthropic-API-Schlüssel aussieht; sonst SchluesselFehler."""
    wert = str(schluessel or "").strip()
    if not _FORMAT.fullmatch(wert):
        raise SchluesselFehler("Das ist kein Anthropic-API-Schlüssel (er beginnt mit sk-ant-)")
    return wert


def ende(schluessel: str) -> str:
    """Die letzten vier Zeichen, für die Anzeige "hinterlegt, endet auf …abcd"."""
    return schluessel[-4:]


def schwaerzen(text: str) -> str:
    """Ersetzt alles, was wie ein Anthropic-Schlüssel aussieht (für Logs und Fehlertexte)."""
    return _IM_TEXT.sub("sk-ant-…", text) if text and "sk-ant-" in text else text
