"""Tests der Modellwahl je Profil (Automatisch oder Nur Hausmodelle, Migration 0011) und des zweiten Schlüsseltyps
(Claude-Code-Token): Reihenfolge Zwang des Admins vor Wahl der Person vor Schlüssel, Token nur verschlüsselt und nur
mit dem richtigen Zweck, nie in Antworten oder Logs, Format statt Testanfrage. Datenbank durch eine Attrappe ersetzt."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import asyncpg
import pytest
from loguru import logger

import modellzugang as mz
from llm_anbieter import LLMNichtVerfuegbar
from schluessel import CODE_TOKEN, SCHLUESSEL, SchluesselFehler, Tresor, kek_erzeugen, token_format_pruefen

NIKO = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
ANDERE = "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"
API = "sk-ant-api03-EIGENeigen-0123456789abcdefghij-Ab12"
TOKEN = "sk-ant-oat01-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99"


def _zeile(**werte) -> dict:
    basis = {"nur_haus": False, "claude_schluessel": None, "claude_schluessel_ende": None, "claude_workspace": None,
             "claude_modell": None, "claude_geprueft_at": None, "stt": "soniox", "tts": "soniox",
             "modell_modus": "automatisch", "claude_code_token": None, "claude_code_token_ende": None,
             "claude_code_token_at": None}
    return basis | werte


class FakeDb:
    configured = True

    def __init__(self, ohne_0011: bool = False):
        self.zeilen: dict[str, dict] = {}
        self.sql: list[str] = []
        self.ohne_0011 = ohne_0011

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    async def fetchrow(self, sql: str, *args):
        self.sql.append(sql)
        if self.ohne_0011 and ("modell_modus" in sql or "claude_code_token" in sql):
            raise asyncpg.UndefinedColumnError("column does not exist")
        z = self.zeilen.get(args[0])
        return dict(z) if z is not None else None

    async def execute(self, sql: str, *args):
        self.sql.append(sql)
        if self.ohne_0011 and ("modell_modus" in sql or "claude_code_token" in sql):
            raise asyncpg.UndefinedColumnError("column does not exist")
        z = self.zeilen.setdefault(args[0], _zeile())
        if sql.startswith("insert") and "modell_modus" in sql:
            z["modell_modus"] = args[1]
        elif sql.startswith("insert") and "claude_code_token" in sql:
            z.update(claude_code_token=args[1], claude_code_token_ende=args[2],
                     claude_code_token_at=datetime(2026, 10, 22, 8, 30, tzinfo=UTC))
        elif sql.startswith("update") and "claude_code_token = null" in sql:
            z.update(claude_code_token=None, claude_code_token_ende=None, claude_code_token_at=None)
        return "OK"


@pytest.fixture
def tresor():
    return Tresor(base64.urlsafe_b64decode(kek_erzeugen()))


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def log():
    zeilen: list[str] = []
    sink = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    yield zeilen
    logger.remove(sink)


def _mit_api_schluessel(db, tresor, uid=NIKO, **werte):
    db.zeilen[uid] = _zeile(claude_schluessel=tresor.verschluesseln(uid, API, SCHLUESSEL),
                            claude_schluessel_ende=API[-4:], **werte)


def _mit_token(db, tresor, uid=NIKO, **werte):
    db.zeilen[uid] = db.zeilen.get(uid, _zeile()) | {
        "claude_code_token": tresor.verschluesseln(uid, TOKEN, CODE_TOKEN), "claude_code_token_ende": TOKEN[-4:],
        "claude_code_token_at": datetime(2026, 10, 22, 8, 30, tzinfo=UTC)} | werte


# --- Reihenfolge: Zwang des Admins, Wahl der Person, Schlüssel ---------------------------------------


async def test_automatisch_mit_schluessel_ist_claude(db, tresor):
    _mit_api_schluessel(db, tresor)
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.art, z.quelle) == ("claude", "profil") and z.claude_schluessel == API


async def test_wahl_nur_haus_gilt_trotz_schluessel_und_traegt_keinen(db, tresor):
    _mit_api_schluessel(db, tresor, modell_modus="nur_haus")
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.art, z.quelle) == ("haus", "wahl")
    assert z.claude_schluessel is None  # der Schlüssel bleibt in der Datenbank, im Zugang ist er nicht
    with pytest.raises(LLMNichtVerfuegbar):
        z.anthropic()


async def test_wahl_nur_haus_gilt_auch_fuer_den_kernschluessel(db, tresor, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-KERNkern-0123456789abcdefghij-K3rn")
    monkeypatch.setenv(mz.ZUORDNUNG_ENV, NIKO)
    db.zeilen[NIKO] = _zeile(modell_modus="nur_haus")
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.art, z.quelle, z.claude_schluessel) == ("haus", "wahl", None)


async def test_zwang_des_admins_ist_staerker_als_die_wahl(db, tresor):
    db.zeilen[NIKO] = _zeile(nur_haus=True, modell_modus="automatisch")
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.art, z.quelle) == ("haus", "festgelegt")
    stand = await mz.stand_laden(NIKO, db=db)
    assert stand.festgelegt and stand.art == "haus" and stand.quelle == "festgelegt"


async def test_ohne_zeile_oder_unbekannter_wert_ist_automatisch(db, tresor):
    assert (await mz.stand_laden(NIKO, db=db)).modus == "automatisch"
    db.zeilen[NIKO] = _zeile(modell_modus="irgendwas")
    assert (await mz.stand_laden(NIKO, db=db)).modus == "automatisch"


async def test_stand_zeigt_wahl_und_behaelt_den_schluessel_im_blick(db, tresor):
    _mit_api_schluessel(db, tresor, modell_modus="nur_haus")
    s = await mz.stand_laden(NIKO, db=db)
    assert (s.art, s.quelle, s.modus) == ("haus", "wahl", "nur_haus")
    assert s.hinterlegt and s.ende == API[-4:] and not s.festgelegt
    n = s.nachricht()
    assert n["modus"] == "nur_haus" and API not in json.dumps(n)


async def test_fehlende_migration_wirft_nicht_alle_auf_hausmodelle(tresor):
    db = FakeDb(ohne_0011=True)
    _mit_api_schluessel(db, tresor)
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.art, z.quelle) == ("claude", "profil")  # Stand von 0010 bleibt wirksam
    assert any("modell_modus" in s for s in db.sql) and any("modell_modus" not in s for s in db.sql)


# --- Wahl setzen ------------------------------------------------------------------------------------------


async def test_modus_setzen_und_zurueck(db, tresor):
    s = await mz.modus_setzen(NIKO, "nur_haus", db=db)
    assert s.modus == "nur_haus" and s.art == "haus"
    s = await mz.modus_setzen(NIKO, "automatisch", db=db)
    assert s.modus == "automatisch"


@pytest.mark.parametrize("wert", ["", None, "alles", "NUR_HAUS", 1, True])
async def test_modus_ungueltig(db, wert):
    with pytest.raises(mz.ZugangFehler, match="Unbekannte Wahl"):
        await mz.modus_setzen(NIKO, wert, db=db)
    assert NIKO not in db.zeilen


async def test_modus_bei_zwang_des_admins_gesperrt(db):
    db.zeilen[NIKO] = _zeile(nur_haus=True)
    with pytest.raises(mz.ZugangFehler, match="gesperrt"):
        await mz.modus_setzen(NIKO, "automatisch", db=db)
    assert db.zeilen[NIKO]["modell_modus"] == "automatisch"  # unverändert, nichts geschrieben
    assert not any(s.startswith("insert") for s in db.sql)


async def test_modus_ohne_migration_klare_meldung():
    with pytest.raises(mz.ZugangFehler, match="Migration 0011"):
        await mz.modus_setzen(NIKO, "nur_haus", db=FakeDb(ohne_0011=True))


async def test_modus_ungueltige_profil_id(db):
    with pytest.raises(mz.ZugangFehler, match="Profil ungültig"):
        await mz.modus_setzen("kein-uuid", "nur_haus", db=db)


# --- Claude-Code-Token ---------------------------------------------------------------------------------------


def test_token_format():
    assert token_format_pruefen(f"  {TOKEN}\n") == TOKEN
    for schlecht in ("", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "sk-ant-oat01-kurz", "token", None, TOKEN + " x"):
        with pytest.raises(SchluesselFehler):
            token_format_pruefen(schlecht)
    with pytest.raises(SchluesselFehler, match="API-Schlüssel"):
        token_format_pruefen(API)


def test_api_schluessel_feld_lehnt_den_token_ab():
    from schluessel import format_pruefen

    with pytest.raises(SchluesselFehler, match="Claude-Code-Token"):
        format_pruefen(TOKEN)
    assert format_pruefen(API) == API


async def test_token_hinterlegen_ohne_testanfrage_verschluesselt(db, tresor, log):
    s = await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=tresor)
    assert s.code_hinterlegt and s.code_ende == TOKEN[-4:] and s.code_gesetzt.startswith("2026-10-22T08:30")
    gespeichert = db.zeilen[NIKO]["claude_code_token"]
    assert TOKEN.encode() not in bytes(gespeichert) and b"oat01" not in bytes(gespeichert)
    assert tresor.entschluesseln(NIKO, gespeichert, CODE_TOKEN) == TOKEN
    n = json.dumps(s.nachricht())
    assert TOKEN not in n and TOKEN[:20] not in n and TOKEN[-4:] in n
    assert all(TOKEN not in z and TOKEN[:20] not in z for z in log)


async def test_chiffrat_gilt_nur_fuer_seinen_zweck_und_sein_profil(db, tresor):
    await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=tresor)
    chiffre = db.zeilen[NIKO]["claude_code_token"]
    with pytest.raises(SchluesselFehler):
        tresor.entschluesseln(NIKO, chiffre, SCHLUESSEL)  # als API-Schlüssel gelesen: nein
    with pytest.raises(SchluesselFehler):
        tresor.entschluesseln(ANDERE, chiffre, CODE_TOKEN)  # in der Zeile eines anderen Profils: nein
    with pytest.raises(SchluesselFehler):
        tresor.verschluesseln(NIKO, "x", "unbekannt")


async def test_token_hinterlegen_lehnt_ab(db, tresor):
    for roh, text in ((API, "API-Schlüssel"), ("quatsch", "kein Claude-Code-Token"), ("", "kein Claude-Code-Token")):
        with pytest.raises(mz.ZugangFehler, match=text):
            await mz.code_token_hinterlegen(NIKO, roh, db=db, tresor=tresor)
    assert NIKO not in db.zeilen
    with pytest.raises(mz.ZugangFehler, match="Hauptschlüssel"):
        await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=None)
    db.zeilen[NIKO] = _zeile(nur_haus=True)
    with pytest.raises(mz.ZugangFehler, match="festgelegt"):
        await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=tresor)


async def test_token_und_api_schluessel_sind_unabhaengig(db, tresor):
    _mit_api_schluessel(db, tresor)
    await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=tresor)
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert z.art == "claude" and z.claude_schluessel == API and z.claude_code is True
    s = await mz.code_token_loeschen(NIKO, db=db)
    assert not s.code_hinterlegt and s.hinterlegt  # der API-Schlüssel bleibt
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert z.claude_code is False


async def test_nur_token_gibt_kein_claude_gespraech(db, tresor):
    await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=tresor)
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.art, z.claude_schluessel, z.claude_code) == ("haus", None, True)  # Gespräch im Haus, Token für Aufträge
    assert (await mz.stand_laden(NIKO, db=db)).art == "haus"


async def test_wahl_nur_haus_nimmt_dem_zugang_auch_das_claude_code_merkmal(db, tresor):
    await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=tresor)
    await mz.modus_setzen(NIKO, "nur_haus", db=db)
    z = await mz.zugang_laden(NIKO, db=db, tresor=tresor)
    assert (z.art, z.claude_code) == ("haus", False)
    assert await mz.claude_code_token_laden(NIKO, db=db, tresor=tresor) is None


async def test_token_fuer_den_lauf(db, tresor, log):
    assert await mz.claude_code_token_laden(NIKO, db=db, tresor=tresor) is None  # keiner da
    await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=tresor)
    assert await mz.claude_code_token_laden(NIKO, db=db, tresor=tresor) == TOKEN
    assert await mz.claude_code_token_laden(ANDERE, db=db, tresor=tresor) is None  # nur das eigene Profil
    assert await mz.claude_code_token_laden("kein-uuid", db=db, tresor=tresor) is None
    assert await mz.claude_code_token_laden(NIKO, db=db, tresor=None) is None  # ohne Hauptschlüssel
    assert all(TOKEN not in z and TOKEN[:20] not in z for z in log)


async def test_token_fuer_den_lauf_nicht_bei_zwang_des_admins(db, tresor):
    await mz.code_token_hinterlegen(NIKO, TOKEN, db=db, tresor=tresor)
    db.zeilen[NIKO]["nur_haus"] = True  # (die Datenbank räumt ihn dabei, hier zählt die zweite Sperre im Kern)
    assert await mz.claude_code_token_laden(NIKO, db=db, tresor=tresor) is None


async def test_token_fuer_den_lauf_chiffrat_eines_anderen_profils(db, tresor):
    db.zeilen[NIKO] = _zeile(claude_code_token=tresor.verschluesseln(ANDERE, TOKEN, CODE_TOKEN),
                             claude_code_token_ende="Zz99", claude_code_token_at=datetime(2026, 10, 22, tzinfo=UTC))
    assert await mz.claude_code_token_laden(NIKO, db=db, tresor=tresor) is None


async def test_token_fuer_den_lauf_ohne_migration():
    assert await mz.claude_code_token_laden(NIKO, db=FakeDb(ohne_0011=True)) is None


# --- Nachrichten der Oberfläche -----------------------------------------------------------------------------


async def _nachricht(db, tresor, **data):
    gesendet: list[dict] = []

    async def senden(n):
        gesendet.append(n)

    await mz.modellzugang_nachricht(NIKO, data, senden, db=db, tresor=tresor)
    return gesendet[-1]


async def test_nachricht_modus(db, tresor):
    n = await _nachricht(db, tresor, aktion="modus", modus="nur_haus")
    assert (n["aktion"], n["modus"], n["art"], n["quelle"]) == ("modus", "nur_haus", "haus", "wahl")
    assert n["wirksam"] == "ab der nächsten Sitzung" and "fehler" not in n
    n = await _nachricht(db, tresor, aktion="modus", modus="egal")
    assert n["fehler"].startswith("Unbekannte Wahl") and n["modus"] == "nur_haus"


async def test_nachricht_token_hinterlegen_und_loeschen(db, tresor, log):
    n = await _nachricht(db, tresor, aktion="hinterlegen", schluesselart="code", schluessel=TOKEN)
    assert n["code_hinterlegt"] is True and n["code_ende"] == TOKEN[-4:] and not n["hinterlegt"]
    assert TOKEN not in json.dumps(n)
    n = await _nachricht(db, tresor, aktion="loeschen", schluesselart="code")
    assert n["code_hinterlegt"] is False
    assert all(TOKEN not in z for z in log)


async def test_nachricht_token_falsches_feld(db, tresor):
    n = await _nachricht(db, tresor, aktion="hinterlegen", schluesselart="code", schluessel=API)
    assert "API-Schlüssel" in n["fehler"] and n["code_hinterlegt"] is False
    n = await _nachricht(db, tresor, aktion="hinterlegen", schluesselart="api", schluessel=TOKEN)
    assert "Claude-Code-Token" in n["fehler"]


async def test_nachricht_ohne_art_ist_der_api_schluessel(db, tresor):
    # alte Oberfläche ohne schluesselart: wie bisher (hier lehnt der Formatcheck den Token ab)
    n = await _nachricht(db, tresor, aktion="hinterlegen", schluessel=TOKEN)
    assert "Claude-Code-Token" in n["fehler"]
