"""Die PC-Freigabe bleibt verhaltensgleich (Testlabor, docs/testlabor-wamo.md 3.2, "Trennung von der PC-Freigabe").

Der Beweis hat drei Teile: Dateien, die sich nicht ändern dürfen, per Hash (pc_freigaben.py, pc_host.py), die gemeinsame Datei bot.py nur für die
Funktion darf_pc_freigeben per Hash ihres Quelltexts (bot.py ändert sich sonst durch Einhängepunkte), und ein Verhaltenstest. Ändert jemand mit
Absicht eine dieser Stellen, ändert er den Hash hier im selben Commit und sagt im Review, warum.

Zeilenenden werden vor dem Hash vereinheitlicht (Windows und Linux).
"""

from __future__ import annotations

import ast
import hashlib
import os
import pathlib
from dataclasses import dataclass
from unittest import mock

import pytest

from auth_gate import Identity

HIER = pathlib.Path(__file__).parent
HASH_PC_FREIGABEN = "1f3146778ea20ea0367773f55c9284ba570e7066cb08badee096e6e724f5a044"
HASH_PC_HOST = "b05e608cf90697588505b436872b10212fc7df16f9e9db3e962ba6cb480b5460"
HASH_DARF_PC_FREIGEBEN = "28a646d4f986c04ec1ca31a75ce5baeb4dd8ce9d938e381583f749384c5d005f"

DUMMY_ENV = {"SONIOX_API_KEY": "dummy-soniox", "ANTHROPIC_API_KEY": "dummy-anthropic", "WAMO_DATABASE_URL": "",
             "WAMO_DB_SCHEMA": "wamo_test", "WAMO_PC_HOST": "off"}


def _sha(daten: bytes) -> str:
    return hashlib.sha256(daten.replace(b"\r\n", b"\n")).hexdigest()


@pytest.fixture(scope="module")
def bot():
    with mock.patch.dict(os.environ, DUMMY_ENV):
        import bot as modul
    return modul


@dataclass(frozen=True)
class LaborIdentity(Identity):
    """Stand-in für eine Laboridentität: dieselbe Identität mit der Art des Laborprofils (die Schnittstelle getattr(identity, "labor_art"))."""

    labor_art: str | None = "platz"


def test_pc_freigaben_und_pc_host_sind_unveraendert():
    assert _sha((HIER / "pc_freigaben.py").read_bytes()) == HASH_PC_FREIGABEN
    assert _sha((HIER / "pc_host.py").read_bytes()) == HASH_PC_HOST


def test_darf_pc_freigeben_ist_im_quelltext_unveraendert():
    text = (HIER / "bot.py").read_bytes().decode("utf-8").replace("\r\n", "\n")
    knoten = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "darf_pc_freigeben"]
    assert len(knoten) == 1
    assert hashlib.sha256(ast.get_source_segment(text, knoten[0]).encode("utf-8")).hexdigest() == HASH_DARF_PC_FREIGEBEN


ADMIN = Identity("11111111-1111-4111-8111-111111111111", "Niko", "admin", "du")
MITGLIED = Identity("11111111-1111-4111-8111-111111111111", "Niko", "mitglied", "du")


def test_darf_pc_freigeben_verhalten(bot):
    assert bot.darf_pc_freigeben(ADMIN, True, True, True) is True  # Admin mit PC-Werkzeugen, Steuerung an, Sperre gehalten
    for pc, steuerung, sperre in ((False, True, True), (True, False, True), (True, True, False), (False, False, False)):
        assert not bot.darf_pc_freigeben(ADMIN, pc, steuerung, sperre)
    assert not bot.darf_pc_freigeben(MITGLIED, True, True, True)


@pytest.mark.parametrize("art", ["platz", "schaufenster", "unbekannt", ""])
def test_keine_laboridentitaet_darf_pc_freigeben(bot, art):
    # Laborprofile sind immer Mitglieder (Migration 0018): auch bei vollem Schalterstand gibt es keine PC-Freigabe
    labor = LaborIdentity(MITGLIED.user_id, "Gast", "mitglied", "du", labor_art=art)
    assert not bot.darf_pc_freigeben(labor, True, True, True)


def test_ein_admin_ohne_labor_hat_kein_labor_werkzeug_und_keine_labor_freigabe():
    import labor_werkzeuge

    assert labor_werkzeuge.laborart(ADMIN) is None and labor_werkzeuge.laborart(MITGLIED) is None
    assert labor_werkzeuge.laborart(LaborIdentity(ADMIN.user_id, "Niko", "admin", "du", labor_art=None)) is None
    assert labor_werkzeuge.laborart(LaborIdentity(MITGLIED.user_id, "Gast", "mitglied", "du", labor_art="platz")) == "platz"
