"""T13 (Testlabor): Zugangsquelle "labor". Laborprofile führen das Gespräch über den Server-Schlüssel (ANTHROPIC_API_KEY), Modell Haiku,
nie für Entwicklungsaufträge. Die Quelle hängt allein an labor_art aus der Identität des Kerns (Parameter von zugang_laden), nie am
Gespräch. Der Zwang des Admins (nur_haus) und die Wahl der Person schlagen sie, ein eigener Schlüssel des Profils hat Vorrang."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from loguru import logger

import boss
import entwicklung_betrieb
import llm_anbieter
import modellzugang as mz
from llm_anbieter import STANDARD_CLAUDE, Zugang
from schluessel import Tresor, kek_erzeugen
from test_modellzugang import FakeDb

LABOR = "00000000-0000-4000-8000-00000000a001"
SERVER_SCHLUESSEL = "sk-ant-api03-SERVERserver-0123456789abcdefghij-S3rv"
EIGENER = "sk-ant-api03-EIGENeigen-0123456789abcdefghij-Ab12"
WORKSPACE = "wrkspc_labor12345"


@pytest.fixture
def tresor():
    return Tresor(base64.urlsafe_b64decode(kek_erzeugen()))


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SERVER_SCHLUESSEL)
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", WORKSPACE)
    monkeypatch.delenv(mz.ZUORDNUNG_ENV, raising=False)  # niemand hat den Kernschlüssel zugeordnet


@pytest.fixture
def log():
    zeilen: list[str] = []
    sink = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    yield zeilen
    logger.remove(sink)


# --- die Quelle labor ------------------------------------------------------------------------------


@pytest.mark.parametrize("art", ["platz", "schaufenster"])
async def test_laborprofil_fuehrt_das_gespraech_ueber_den_server_schluessel(db, tresor, server, art):
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art=art)
    assert (z.art, z.quelle) == ("claude", "labor")
    assert z.claude_schluessel == SERVER_SCHLUESSEL
    assert z.claude_workspace == WORKSPACE
    assert z.claude_code is False  # kein Claude-Code-Token, nie ein Entwicklungslauf über diesen Zugang
    assert z.claude_modell is None  # Haiku: die Voreinstellung (llm_anbieter.STANDARD_CLAUDE), nie ein teureres Modell
    assert STANDARD_CLAUDE.startswith("claude-haiku")
    assert SERVER_SCHLUESSEL not in repr(z)


async def test_modell_des_profils_gilt_wenn_der_admin_eines_festgelegt_hat(db, tresor, server):
    db.zeile(LABOR, claude_modell="claude-sonnet-5-5", stt="haus")
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="platz")
    assert (z.quelle, z.claude_modell, z.stt, z.tts) == ("labor", "claude-sonnet-5-5", "haus", "soniox")


async def test_ohne_server_schluessel_bleibt_es_bei_hausmodellen(monkeypatch, db, tresor):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(mz.ZUORDNUNG_ENV, raising=False)
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="platz")
    assert (z.art, z.quelle, z.claude_schluessel) == ("haus", "keiner", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="platz")
    assert (z.art, z.quelle) == ("haus", "keiner")


async def test_nur_haus_des_admins_schlaegt_labor(db, tresor, server):
    db.zeile(LABOR, nur_haus=True, stt="haus", tts="haus")
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="platz")
    assert (z.art, z.quelle, z.claude_schluessel) == ("haus", "festgelegt", None)
    assert (z.stt, z.tts) == ("haus", "haus")


async def test_wahl_nur_haus_der_person_schlaegt_labor(db, tresor, server):
    db.zeile(LABOR, modell_modus="nur_haus")
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="schaufenster")
    assert (z.art, z.quelle) == ("haus", "wahl")


async def test_eigener_schluessel_des_profils_hat_vorrang(db, tresor, server):
    """Das Schau-Profil bekommt einmal einen eigenen Zugang (Entwicklungsaufträge): dann gilt die Quelle profil."""
    db.zeile(LABOR, claude_schluessel=tresor.verschluesseln(LABOR, EIGENER), claude_schluessel_ende="Ab12")
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="schaufenster")
    assert (z.quelle, z.claude_schluessel) == ("profil", EIGENER)


async def test_kern_zuordnung_wird_vom_laborprofil_nicht_ausgenutzt(monkeypatch, db, tresor, server):
    monkeypatch.setenv(mz.ZUORDNUNG_ENV, LABOR)  # selbst bei einer Zuordnung auf dieses Profil gilt die Quelle labor
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="platz")
    assert z.quelle == "labor" and z.claude_code is False


# --- nie für andere ------------------------------------------------------------------------------


@pytest.mark.parametrize("art", [None, "", "  ", "beobachter", "Platz", "x" * 40])
async def test_ohne_gueltige_labor_art_nie_die_quelle_labor(db, tresor, server, art):
    """Ein Nicht-Laborprofil bekommt nie labor. Ein unbekannter Wert ist hier kein Laborprofil: Der Kern normiert ihn vorher in der
    Identität auf platz (strengster Fall), diese Funktion gibt dafür nie den Server-Schlüssel frei."""
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art=art)
    assert (z.art, z.quelle, z.claude_schluessel) == ("haus", "keiner", None)


async def test_ohne_den_parameter_gilt_der_alte_ablauf(db, tresor, server):
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor)
    assert (z.art, z.quelle) == ("haus", "keiner")


async def test_labor_art_ist_nur_als_schluesselwort_moeglich(db, tresor, server):
    """Es gibt keinen Weg, die Art aus dem Gespräch oder einer Nachricht in einen Aufruf zu schieben, der nicht ausdrücklich
    labor_art=... schreibt."""
    with pytest.raises(TypeError):
        await mz.zugang_laden(LABOR, "platz", db=db, tresor=tresor)  # type: ignore[misc]


async def test_datenbankfehler_bleibt_fail_closed(tresor, server):
    from memory import DatabaseUnavailable

    z = await mz.zugang_laden(LABOR, db=FakeDb(fehler=DatabaseUnavailable("weg")), tresor=tresor, labor_art="platz")
    assert (z.art, z.quelle) == ("haus", "fehler")


async def test_ungueltige_profil_id_mit_labor_art(db, tresor, server):
    assert (await mz.zugang_laden("kein-uuid", db=db, tresor=tresor, labor_art="platz")).quelle == "fehler"


async def test_kein_schluessel_im_log(db, tresor, server, log):
    await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="platz")
    assert all(SERVER_SCHLUESSEL not in zeile and SERVER_SCHLUESSEL[:20] not in zeile for zeile in log)


# --- nie für Entwicklungsaufträge -------------------------------------------------------------------


async def test_entwicklung_quelle_ist_keine_fuer_labor(db, tresor, server):
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="platz")
    assert boss.entwicklung_quelle(z) == "keine"
    # selbst mit einem (nie vergebenen) Claude-Code-Merkmal gäbe die Quelle labor keinen Entwicklungslauf frei
    mit_token = Zugang(LABOR, "claude", "labor", claude_schluessel=SERVER_SCHLUESSEL, claude_code=True)
    assert boss.entwicklung_quelle(mit_token) == "keine"


async def test_im_buero_gilt_labor_wie_kein_zugang_fuer_entwicklung(db, tresor, server):
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="platz")
    grund = entwicklung_betrieb.grund_quelle(boss.entwicklung_quelle(z))
    assert grund == entwicklung_betrieb.GRUND_KEIN_ZUGANG


async def test_das_schau_profil_mit_eigenem_zugang_behaelt_den_entwicklungsweg(db, tresor, server):
    db.zeile(LABOR, claude_schluessel=tresor.verschluesseln(LABOR, EIGENER))
    z = await mz.zugang_laden(LABOR, db=db, tresor=tresor, labor_art="schaufenster")
    assert boss.entwicklung_quelle(z) == "api"  # Quelle profil, wie jedes Profil mit eigenem Schlüssel


def test_modus_bleibt_automatisch_und_die_quelle_ist_dokumentiert():
    z = Zugang(LABOR, "claude", "labor", claude_schluessel=SERVER_SCHLUESSEL)
    assert boss.modus_wirksam(z) == "automatisch"
    quelle = Path(llm_anbieter.__file__).read_text(encoding="utf-8")
    assert "profil | zuordnung | labor |" in quelle  # die Liste der Quellen im Kommentar nennt labor
