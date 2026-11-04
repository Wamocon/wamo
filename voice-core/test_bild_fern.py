"""Tests der Prüfung für die Fernanfrage des Live-Bilds (bild_fern.py): Token passt zur Sitzung, Admin, frischer Code."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt

import bild_fern

USER = "11111111-2222-3333-4444-555555555555"


def token(aal="aal2", alter=30.0, methode="totp"):
    claims = {"sub": USER, "aal": aal, "amr": [{"method": methode, "timestamp": time.time() - alter}]}
    return jwt.encode(claims, "x" * 32, algorithm="HS256")


class Tor:
    def __init__(self, identity):
        self.identity = identity
        self.gefragt = []

    async def authorize(self, tok):
        self.gefragt.append(tok)
        return SimpleNamespace(identity=self.identity, status=401 if self.identity is None else 200, reason="x")


def ich(user=USER, admin=True):
    return SimpleNamespace(user_id=user, is_admin=admin)


async def pruefen(tok, tor):
    return await bild_fern.pruefer(USER, lambda: tor)(tok)


async def test_frischer_code_des_besitzers_passt():
    tor = Tor(ich())
    t = token()
    assert await pruefen(t, tor) is None
    assert tor.gefragt == [t]


async def test_zu_alter_code_ohne_aal2_oder_ohne_totp_wird_abgelehnt():
    assert await pruefen(token(alter=500), Tor(ich())) == bild_fern.TEXT_ALT
    assert await pruefen(token(aal="aal1"), Tor(ich())) == bild_fern.TEXT_ALT
    assert await pruefen(token(methode="password"), Tor(ich())) == bild_fern.TEXT_ALT


async def test_fremdes_token_nicht_admin_oder_abgelehnt():
    assert await pruefen(token(), Tor(ich(user="99999999-2222-3333-4444-555555555555"))) == bild_fern.TEXT_FALSCH
    assert await pruefen(token(), Tor(ich(admin=False))) == bild_fern.TEXT_FALSCH
    assert await pruefen(token(), Tor(None)) == bild_fern.TEXT_FALSCH


async def test_ungueltige_eingaben_und_fehlendes_tor():
    tor = Tor(ich())
    for roh in (None, 5, "", "kurz", "x" * 20, "a.b", "a." * 9000):
        assert await pruefen(roh, tor) == bild_fern.TEXT_CODE
    assert tor.gefragt == [], "ungültige Eingaben erreichen die Zugangskontrolle nie"
    assert "nicht erreichbar" in await bild_fern.pruefer(USER, lambda: None)(token())
