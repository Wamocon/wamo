"""Einhängepunkte der Labor-Werkzeuge in bot.py (build_session): nur Laborprofile bekommen Werkzeuge und Freigabe, der Klick job "labor" wirkt nur dort,
und ein nicht erreichbarer Dienst heißt: keine Sitzung. Rauchtest ohne Netz wie test_smoke_bot.py, der Dienst ist die Attrappe.
"""

from __future__ import annotations

import asyncio
import os
import types
from dataclasses import dataclass
from unittest import mock

import httpx
import pytest

from auth_gate import Identity

DUMMY_ENV = {
    "SONIOX_API_KEY": "dummy-soniox", "ANTHROPIC_API_KEY": "dummy-anthropic", "WAMO_DATABASE_URL": "", "WAMO_DB_SCHEMA": "wamo_test", "WAMO_PC_HOST": "off",
    "WAMO_CLAUDE_SCHLUESSEL_PROFIL": "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c",
}
USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
SECHS = {"ticket_suchen", "ticket_anlegen", "ticket_aendern", "ticket_verschieben", "ticket_kommentieren", "mail_senden"}

with mock.patch.dict(os.environ, DUMMY_ENV):
    import bot

import labor_werkzeuge
from labor_attrappe import ATTRAPPE_URL, Attrappe
from labor_client import LaborClient, LaborFehler
from test_smoke_bot import FakeTransport


@dataclass(frozen=True)
class LaborIdentity(Identity):
    """Stand-in für die Identität eines Laborprofils (Schnittstelle getattr(identity, "labor_art", None))."""

    labor_art: str | None = "platz"
    labor_platz: int | None = 3


LABOR = LaborIdentity(USER, "Gast", "mitglied", "du")


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch):
    for name, wert in DUMMY_ENV.items():
        monkeypatch.setenv(name, wert)
    with mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)):
        yield


class Beobachter:
    """Reicht labor_sitzung an das Original weiter (mit der Attrappe als Dienst) und merkt sich Aufrufe."""

    def __init__(self, app=None, client=None):
        self.app = app or Attrappe()
        self.client = client or LaborClient(ATTRAPPE_URL, self.app.token, 3, transport=httpx.ASGITransport(app=self.app))
        self.sitzungen: list = []
        self.llms: list = []
        self.aufrufe = 0
        self.original = labor_werkzeuge.labor_sitzung

    async def __call__(self, identity, memory, llm, **kw):
        self.aufrufe += 1
        sitzung = await self.original(identity, memory, llm, client=self.client, **kw)
        if sitzung is not None:
            self.sitzungen.append(sitzung)
            self.llms.append(llm)
        return sitzung


async def bauen(identity, beobachter):
    with mock.patch.object(labor_werkzeuge, "labor_sitzung", beobachter):
        return await bot.build_session(FakeTransport(), identity)


async def klick(session, **data):
    """Der Klick wie von der Oberfläche. Pipecat führt Ereignisbehandler als eigene Aufgaben aus: erst abwarten, dann prüfen."""
    await session.task.rtvi._call_event_handler("on_client_message", types.SimpleNamespace(type="freigabe", data=data))
    for _ in range(200):
        await asyncio.sleep(0)


async def test_laborprofil_bekommt_die_sechs_werkzeuge_und_die_freigabe():
    b = Beobachter()
    session = await bauen(LABOR, b)
    labor = b.sitzungen[0]
    assert {s.name for s in labor.schemas} == SECHS
    registriert = set(getattr(b.llms[0], "_functions", {}))
    assert SECHS <= registriert
    assert labor.durchlauf_id == b.app.durchlauf_id(3)
    await session.cleanup()
    assert b.client._http.is_closed  # der Client des Dienstes ist beim Sitzungsende zu


@pytest.mark.parametrize("rolle", ["mitglied", "admin"])
async def test_normale_profile_haben_keine_laborlogik(rolle):
    b = Beobachter()
    session = await bauen(Identity(USER, "Erika", rolle, "sie"), b)
    assert b.aufrufe == 0 and b.sitzungen == []  # labor_sitzung wurde nie aufgerufen
    session_llm_funktionen = set(getattr(session.task, "_llm_functions", set()))
    assert not SECHS & session_llm_funktionen
    await session.cleanup()
    assert not b.client._http.is_closed  # es gab nichts zu schließen: der Client blieb unberührt
    await b.client.aclose()


async def test_schau_profil_ohne_platz_startet_ohne_ticketwerkzeuge():
    b = Beobachter()
    session = await bauen(LaborIdentity(USER, "Schau", "mitglied", "du", labor_art="schaufenster", labor_platz=None), b)
    assert b.aufrufe == 1 and b.sitzungen == []  # labor_sitzung lieferte None
    assert not SECHS & set(getattr(session.task, "_llm_functions", set()))
    await session.cleanup()
    await b.client.aclose()


async def test_ist_der_dienst_nicht_erreichbar_gibt_es_keine_sitzung():
    def weg(request):
        raise httpx.ConnectError("keine Verbindung", request=request)

    client = LaborClient(ATTRAPPE_URL, "x" * 43, 3, transport=httpx.MockTransport(weg))
    with pytest.raises(LaborFehler) as e:
        await bauen(LABOR, Beobachter(client=client))
    assert e.value.code == "nicht_erreichbar"


async def test_beendeter_durchlauf_oder_fehlende_einwilligung_ist_kein_start():
    app = Attrappe()
    app.durchlauf_beenden(3)
    with pytest.raises(labor_werkzeuge.LaborNichtBereit):
        await bauen(LABOR, Beobachter(app=app))
    app = Attrappe()
    app.neuer_durchlauf(3, einwilligung_version=None)
    with pytest.raises(labor_werkzeuge.LaborNichtBereit):
        await bauen(LABOR, Beobachter(app=app))


async def test_klick_job_labor_wirkt_nur_in_einer_laborsitzung():
    # ein Admin ohne Labor: der Klick mit job "labor" bleibt ohne Wirkung, es gibt kein LaborFreigaben und nichts, das ihn annimmt
    with mock.patch.object(labor_werkzeuge.LaborSitzung, "entscheiden", mock.AsyncMock()) as entscheiden:
        b = Beobachter()
        admin = await bauen(Identity(USER, "Niko", "admin", "du"), b)
        await klick(admin, job="labor", id="0" * 32, entscheidung="ja")
        entscheiden.assert_not_called()
        assert b.aufrufe == 0 and b.sitzungen == []
        await admin.cleanup()
    # eine Laborsitzung: der Klick mit job "labor" kommt bei LaborFreigaben an, mit job "pc" nicht
    b = Beobachter()
    session = await bauen(LABOR, b)
    labor = b.sitzungen[0]
    labor.freigaben.entscheiden = mock.AsyncMock(return_value=None)
    await klick(session, job="pc", id="0" * 16, entscheidung="ja")
    labor.freigaben.entscheiden.assert_not_called()
    await klick(session, job="labor", id="0" * 32, entscheidung="ja")
    labor.freigaben.entscheiden.assert_awaited_once()
    assert labor.freigaben.entscheiden.await_args.args[0]["job"] == "labor"
    await session.cleanup()


async def test_pc_freigabe_im_laborprofil_ist_wirkungslos():
    """Ein Laborprofil hat kein PcFreigaben: der Klick job "pc" erreicht keine PC-Freigabe (und kein LaborFreigaben)."""
    with mock.patch("pc_freigaben.PcFreigaben.entscheiden", mock.AsyncMock()) as pc:
        b = Beobachter()
        session = await bauen(LABOR, b)
        labor = b.sitzungen[0]
        labor.freigaben.entscheiden = mock.AsyncMock(return_value=None)
        await klick(session, job="pc", id="0" * 16, entscheidung="ja")
        pc.assert_not_called()
        labor.freigaben.entscheiden.assert_not_called()
        await session.cleanup()


async def test_ja_waechter_zaehlt_offene_labor_karten_mit():
    b = Beobachter()
    session = await bauen(LABOR, b)
    labor = b.sitzungen[0]
    assert labor.freigaben.offen is False
    await labor.freigaben.anfrage("ticket_anlegen", {"typ": "bug", "prioritaet": "hoch", "titel": "Anmeldung schlägt fehl", "beschreibung": ""})
    assert labor.freigaben.offen is True
    await session.cleanup()
    assert labor.freigaben.offen is False  # beim Sitzungsende lehnt sie alles ab
