"""Tests für scripts/wamo_labor_faktoren.py (zweiter Faktor der Laborprofile automatisch einrichten).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q

Kein Netz außer einer Attrappe auf Loopback, kein ssh, nie die echte Umschlag-Datei: Jeder Test arbeitet mit einer Datei in einem
temporären Ordner, und USERPROFILE und HOME zeigen für die Dauer der Tests ebenfalls dorthin. Testwerte für Passwörter und Seeds
sind kurz und erfunden oder werden aus Zeichen berechnet, nie als lange Zeichenfolge eingetragen.
"""
from __future__ import annotations

import base64
import calendar
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_faktoren_totp as tt  # noqa: E402


@pytest.fixture(autouse=True)
def kein_echtes_home(monkeypatch, tmp_path):
    """Selbst ein Fehler im Skript trifft nie das echte Verzeichnis .wamo-labor des Benutzers."""
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("USERPROFILE", str(heim))
    monkeypatch.setenv("HOME", str(heim))


# ---------- TOTP nach RFC 6238 ----------

RFC_SEED = base64.b32encode(b"1234567890" * 2).decode()  # der SHA1-Schlüssel der Testvektoren, berechnet statt eingetragen


@pytest.mark.parametrize("zeit,erwartet", [
    (59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130"),
])
def test_totp_rfc6238_vektoren_acht_stellen(zeit, erwartet):
    assert tt.totp_code(RFC_SEED, zeit, stellen=8) == erwartet


@pytest.mark.parametrize("zeit,erwartet", [(59, "287082"), (1111111109, "081804"), (1234567890, "005924")])
def test_totp_sechs_stellen_sind_die_letzten_sechs_des_vektors(zeit, erwartet):
    assert tt.totp_code(RFC_SEED, zeit) == erwartet


def test_totp_fenstergrenzen_und_kleinschreibung():
    assert tt.totp_code(RFC_SEED, 59.9) == tt.totp_code(RFC_SEED, 30.0)
    assert tt.totp_code(RFC_SEED, 60.0) != tt.totp_code(RFC_SEED, 59.9)
    zerlegt = " ".join(RFC_SEED[i:i + 4] for i in range(0, len(RFC_SEED), 4)).lower()
    assert tt.totp_code(zerlegt, 1234567890) == "005924"


@pytest.mark.parametrize("seed,gueltig", [
    (RFC_SEED, True), (RFC_SEED.lower(), True), ("ABCD EFGH IJKL MNOP", True),
    ("", False), ("ABC", False), ("1" * 20, False), ("A" * 15, False), ("AB!D" * 6, False), (None, False),
])
def test_seed_gueltig(seed, gueltig):
    assert tt.seed_gueltig(seed) is gueltig


def test_ablauf_sekunden():
    assert tt.ablauf_sekunden(59.0) == 1
    assert tt.ablauf_sekunden(60.0) == 30
    assert tt.ablauf_sekunden(75.5) == 15


def _datum(sekunden: int) -> str:
    from email.utils import formatdate
    return formatdate(sekunden, usegmt=True)


def test_zeitversatz_server_vor_der_lokalen_uhr():
    lokal = float(calendar.timegm((2026, 11, 12, 10, 0, 0)))
    assert tt.zeitversatz(_datum(int(lokal) + 40), lokal + 0.5) == pytest.approx(40.0, abs=0.6)


def test_zeitversatz_server_hinter_der_lokalen_uhr():
    lokal = float(calendar.timegm((2026, 11, 12, 10, 0, 0)))
    assert tt.zeitversatz(_datum(int(lokal) - 90), lokal + 0.5) == pytest.approx(-90.0, abs=0.6)


@pytest.mark.parametrize("kopf", [None, "", "kein Datum", "Thu, 99 Foo 2026 10:00:00 GMT", "Thu, 01 Jan 1970 00:00:00 GMT"])
def test_zeitversatz_ohne_brauchbaren_kopf_ist_null(kopf):
    assert tt.zeitversatz(kopf, float(calendar.timegm((2026, 11, 12, 10, 0, 0)))) == 0.0


def test_frisches_fenster_wartet_am_fensterende():
    uhr = {"t": 59.0}
    waren = []

    def warte(s):
        waren.append(s)
        uhr["t"] += s

    t = tt.frisches_fenster(lambda: uhr["t"], warte)
    assert len(waren) == 1 and 1.0 <= waren[0] <= 2.5
    assert t >= 60.0


def test_frisches_fenster_wartet_nicht_mitten_im_fenster():
    waren = []
    assert tt.frisches_fenster(lambda: 70.0, waren.append) == 70.0
    assert waren == []


def _jwt(nutzlast: dict) -> str:
    def teil(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return ".".join((teil({"alg": "ES256"}), teil(nutzlast), "u"))


def test_jwt_claims_liest_aal():
    assert tt.jwt_claims(_jwt({"aal": "aal2", "sub": "x"}))["aal"] == "aal2"


@pytest.mark.parametrize("kaputt", ["", "a", "a.b", "a.%%%.c", ".".join(("a", base64.urlsafe_b64encode(b"[1]").decode(), "c")), None])
def test_jwt_claims_kaputt_ist_leer(kaputt):
    assert tt.jwt_claims(kaputt) == {}


def test_seed_aus_hash_berechnet():
    """Hilfe für spätere Tests: aus einem Namen wird ein 32 Zeichen langer Base32-Seed berechnet, nie eingetragen."""
    seed = base64.b32encode(hashlib.sha1(b"labor-01").digest()).decode()
    assert len(seed) == 32 and tt.seed_gueltig(seed)
