"""Die Laborsitzung in bot.build_session, im Zusammenspiel von K1 (Annahme, Erlaubnisliste, Nachrichtenfilter) und K2 (Werkzeuge, Freigabe, Dienstclient).

Nur Laborprofile bekommen Werkzeuge und Freigabe, der Klick job "labor" wirkt nur dort, und ein nicht erreichbarer Dienst heißt: keine Sitzung. Der Dienst
ist die Attrappe (labor_testhilfe.dienst): dieselbe für die Durchlauf-Prüfung beim Start und für die Werkzeuge. Rauchtest ohne Netz wie test_smoke_bot.py.
Der Test am Ende läuft eine ganze Sitzung von der Annahme bis zum Ende des Durchlaufs.
"""

from __future__ import annotations

import asyncio
import os
import time
import types
from unittest import mock

import httpx
import pytest

import labor
import labor_durchlauf
import labor_testhilfe
import labor_werkzeuge
from auth_gate import Identity
from labor_attrappe import ATTRAPPE_URL, Attrappe
from labor_client import LaborClient
from test_smoke_bot import FakeTransport

DUMMY_ENV = {
    "SONIOX_API_KEY": "dummy-soniox", "ANTHROPIC_API_KEY": "dummy-anthropic", "WAMO_DATABASE_URL": "", "WAMO_DB_SCHEMA": "wamo_test", "WAMO_PC_HOST": "off",
    "WAMO_CLAUDE_SCHLUESSEL_PROFIL": "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c",
}
USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
SECHS = {"ticket_suchen", "ticket_anlegen", "ticket_aendern", "ticket_verschieben", "ticket_kommentieren", "mail_senden"}
# Nichts davon darf eine Laborsitzung je haben: Gedächtnis, PC, Bildschirm, Fernzugriff, Entwicklung, Boss, Geräte, geteilte Arbeit, Nachladen
INTERN = ("merken", "vergessen", "fernzugriff_oeffnen", "ausgabe_nachladen", "entwicklung_starten", "aufgabe_delegieren", "system_info",
          "bildschirm_ansehen", "browser_lesen", "geraet_liste", "teilen_anlegen")

with mock.patch.dict(os.environ, DUMMY_ENV):
    import bot


def laborprofil(art: str = "platz", platz: int | None = 3) -> Identity:
    """Die Identität, die die Zugangskontrolle für ein freigeschaltetes Laborprofil baut: Rolle mitglied, labor_aktiv, Platz nur beim Platzprofil."""
    return Identity(USER, "Gast", "mitglied", "du", labor_art=art, labor_platz=platz if art == "platz" else None, labor_aktiv=True)


LABOR = laborprofil()


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch):
    for name, wert in DUMMY_ENV.items():
        monkeypatch.setenv(name, wert)
    with mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)):
        yield


class Beobachter:
    """Reicht labor_sitzung an das Original weiter (mit der Attrappe als Dienst) und merkt sich Aufrufe."""

    def __init__(self, app=None):
        self.app = app or Attrappe()
        self.client = LaborClient(ATTRAPPE_URL, self.app.token, 3, transport=httpx.ASGITransport(app=self.app))
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
    with labor_testhilfe.dienst(beobachter.app), mock.patch.object(labor_werkzeuge, "labor_sitzung", beobachter):
        return await bot.build_session(FakeTransport(), identity)


async def klick(session, **data):
    """Der Klick wie von der Oberfläche. Pipecat führt Ereignisbehandler als eigene Aufgaben aus: erst abwarten, dann prüfen."""
    await session.task.rtvi._call_event_handler("on_client_message", types.SimpleNamespace(type="freigabe", data=data))
    for _ in range(200):
        await asyncio.sleep(0)


async def test_laborprofil_bekommt_die_sechs_werkzeuge_und_die_freigabe():
    b = Beobachter()
    session = await bauen(LABOR, b)
    labor_werkz = b.sitzungen[0]
    assert {s.name for s in labor_werkz.schemas} == SECHS
    registriert = set(getattr(b.llms[0], "_functions", {}))
    assert SECHS <= registriert
    assert labor_werkz.durchlauf_id == b.app.durchlauf_id(3)
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
    session = await bauen(laborprofil("schaufenster"), b)
    assert b.aufrufe == 1 and b.sitzungen == []  # labor_sitzung lieferte None
    assert not SECHS & set(getattr(session.task, "_llm_functions", set()))
    await session.cleanup()
    await b.client.aclose()


async def test_ist_der_dienst_nicht_erreichbar_gibt_es_keine_sitzung():
    def weg(request):
        raise httpx.ConnectError("keine Verbindung", request=request)

    b = Beobachter()
    with labor_testhilfe.dienst(b.app, transport=httpx.MockTransport(weg)), mock.patch.object(labor_werkzeuge, "labor_sitzung", b):
        with pytest.raises(labor.LaborAbgelehnt) as e:
            await bot.build_session(FakeTransport(), LABOR)
    # die Annahme der Zugangskontrolle ist die erste Stufe: ohne Dienst kommt es nicht einmal zu den Werkzeugen
    assert (e.value.status, e.value.code) == (503, labor_durchlauf.CODE_DIENST)
    assert b.aufrufe == 0
    await b.client.aclose()


async def test_beendeter_durchlauf_oder_fehlende_einwilligung_ist_kein_start():
    app = Attrappe()
    app.durchlauf_beenden(3)
    with pytest.raises(labor.LaborAbgelehnt) as e:
        await bauen(LABOR, Beobachter(app=app))
    assert (e.value.status, e.value.code) == (403, labor_durchlauf.CODE_BEENDET)
    app = Attrappe()
    app.neuer_durchlauf(3, einwilligung_version=None)
    with pytest.raises(labor.LaborAbgelehnt) as e:
        await bauen(LABOR, Beobachter(app=app))
    assert (e.value.status, e.value.code) == (403, labor_durchlauf.CODE_EINWILLIGUNG)


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
    labor_werkz = b.sitzungen[0]
    labor_werkz.freigaben.entscheiden = mock.AsyncMock(return_value=None)
    await klick(session, job="pc", id="0" * 16, entscheidung="ja")
    labor_werkz.freigaben.entscheiden.assert_not_called()
    await klick(session, job="labor", id="0" * 32, entscheidung="ja")
    labor_werkz.freigaben.entscheiden.assert_awaited_once()
    assert labor_werkz.freigaben.entscheiden.await_args.args[0]["job"] == "labor"
    await session.cleanup()


async def test_pc_freigabe_im_laborprofil_ist_wirkungslos():
    """Ein Laborprofil hat kein PcFreigaben: der Klick job "pc" erreicht keine PC-Freigabe (und kein LaborFreigaben)."""
    with mock.patch("pc_freigaben.PcFreigaben.entscheiden", mock.AsyncMock()) as pc:
        b = Beobachter()
        session = await bauen(LABOR, b)
        labor_werkz = b.sitzungen[0]
        labor_werkz.freigaben.entscheiden = mock.AsyncMock(return_value=None)
        await klick(session, job="pc", id="0" * 16, entscheidung="ja")
        pc.assert_not_called()
        labor_werkz.freigaben.entscheiden.assert_not_called()
        await session.cleanup()


async def test_ja_waechter_zaehlt_offene_labor_karten_mit():
    b = Beobachter()
    session = await bauen(LABOR, b)
    labor_werkz = b.sitzungen[0]
    assert labor_werkz.freigaben.offen is False
    await labor_werkz.freigaben.anfrage("ticket_anlegen", {"typ": "bug", "prioritaet": "hoch", "titel": "Anmeldung schlägt fehl", "beschreibung": ""})
    assert labor_werkz.freigaben.offen is True
    await session.cleanup()
    assert labor_werkz.freigaben.offen is False  # beim Sitzungsende lehnt sie alles ab


# ---------- Ende zu Ende: eine echte Laborsitzung durch build_session ----------
class Mitschnitt:
    """Baut eine Sitzung mit echten Bausteinen (Annahme, Werkzeuge, Freigabe, Wache) und hält fest, was sie nach außen zeigt: Kontext, LLM, beide
    Laborobjekte, den Zugang, alles, was an die Oberfläche und an das Sprachmodell geht."""

    def __init__(self):
        self.session = self.transport = self.context = self.llm = self.zugang = None
        self.annahme = self.werkz = None  # K1: LaborSitzung der Annahme, K2: LaborSitzung der Werkzeuge
        self.frames: list = []

    async def aufbauen(self, identity):
        echt_llm, echt_paar, echt_starten, echt_werkz = bot.gespraech_llm, bot.LLMContextAggregatorPair, bot.LaborSitzung.starten, labor_werkzeuge.labor_sitzung

        async def llm_merken(system_instruction, zugang):
            self.zugang = zugang
            antwort = await echt_llm(system_instruction, zugang)
            self.llm = antwort[0]
            return antwort

        def paar(context, **kw):
            self.context = context
            return echt_paar(context, **kw)

        async def starten(ident, **kw):
            self.annahme = await echt_starten(ident, **kw)
            return self.annahme

        async def werkzeuge(*a, **kw):
            self.werkz = await echt_werkz(*a, **kw)
            return self.werkz

        self.transport = FakeTransport()
        with mock.patch.object(bot, "gespraech_llm", llm_merken), mock.patch.object(bot, "LLMContextAggregatorPair", paar), \
             mock.patch.object(bot, "LaborSitzung", mock.Mock(starten=starten)), mock.patch.object(labor_werkzeuge, "labor_sitzung", werkzeuge):
            self.session = await bot.build_session(self.transport, identity)
        self.session.task.queue_frames = self._erfassen
        return self

    async def _erfassen(self, frames, *a, **kw):
        self.frames.extend(frames)

    def kontext_namen(self) -> set[str]:
        return {s.name for s in self.context.tools.standard_tools}

    def nachrichten(self, typ: str) -> list[dict]:
        """Server-Nachrichten an die Oberfläche."""
        return [f.data for f in self.frames if isinstance(getattr(f, "data", None), dict) and f.data.get("type") == typ]

    def meldungen(self) -> list[str]:
        """Nachrichten an das Sprachmodell (melde_llm)."""
        return [m["content"] for f in self.frames for m in getattr(f, "messages", []) if isinstance(m, dict)]

    async def rufen(self, name: str, **argumente) -> dict:
        ergebnisse: list = []

        async def antwort(wert, **_kw):
            ergebnisse.append(wert)

        await self.llm._functions[name].handler(types.SimpleNamespace(arguments=argumente, result_callback=antwort))
        assert len(ergebnisse) == 1, (name, ergebnisse)
        return ergebnisse[0]

    async def klick(self, **data):
        await klick(self.session, **data)


async def warten(bedingung, was: str, frist: float = 5.0):
    ende = time.monotonic() + frist
    while not bedingung():
        assert time.monotonic() < ende, f"nicht eingetreten: {was}"
        await asyncio.sleep(0.01)


async def test_ende_zu_ende_start_werkzeuge_freigabeklick_und_durchlaufende():
    with labor_testhilfe.dienst() as app, mock.patch.object(bot, "vorwaermen", mock.AsyncMock()), mock.patch.object(bot, "ENDE_MELDUNG_SECS", 0.01):
        m = await Mitschnitt().aufbauen(LABOR)
        vorher = len(app.tickets(3))

        # 1. Start: angenommen mit dem Durchlauf des Dienstes, die Werkzeuge sind genau die Erlaubnisliste und nichts Internes
        assert m.annahme.stand.durchlauf_id == m.werkz.durchlauf_id == app.durchlauf_id(3)
        assert m.kontext_namen() == set(m.llm._functions) == labor.WERKZEUGE_BASIS | labor.WERKZEUGE_TICKET
        assert SECHS <= m.kontext_namen()
        assert not set(INTERN) & (m.kontext_namen() | set(m.llm._functions))
        assert None not in m.llm._functions  # auch kein Sammelhandler

        # 2. Ein schreibendes Werkzeug erzeugt nur die Karte: ohne Klick geschieht im Dienst nichts
        antwort = await m.rufen("ticket_anlegen", typ="bug", prioritaet="hoch", titel="Anmeldung schlägt fehl", beschreibung="Der Knopf reagiert nicht.")
        assert antwort["status"] == "wartet_auf_klick"
        karte = m.nachrichten("freigaben")[-1]
        assert karte["job"] == "labor"
        karten_id = karte["items"][0]["id"]
        assert len(app.tickets(3)) == vorher and m.werkz.freigaben.offen

        # 3. Der Klick: ein anderer job und ein anderer Typ wirken nicht, die Freigabe mit job labor führt genau den Antrag aus
        await m.klick(job="pc", id=karten_id, entscheidung="ja")
        assert len(app.tickets(3)) == vorher and m.werkz.freigaben.offen
        await m.klick(job="labor", id=karten_id, entscheidung="ja")
        await warten(lambda: len(app.tickets(3)) == vorher + 1, "Ticket angelegt")
        assert [t["titel"] for t in app.tickets(3)].count("Anmeldung schlägt fehl") == 1
        await warten(lambda: any("angelegt" in text for text in m.meldungen()), "Meldung an das Sprachmodell")
        assert not m.werkz.freigaben.offen

        # 4. Eine zweite Karte bleibt offen, dann endet der Durchlauf: die Wache schließt die Sitzung, die Karte ist erledigt, kein Klick wirkt mehr
        await m.rufen("ticket_anlegen", typ="aufgabe", prioritaet="mittel", titel="Zweites Ticket", beschreibung="")
        zweite = m.nachrichten("freigaben")[-1]["items"][0]["id"]
        assert zweite != karten_id and m.werkz.freigaben.offen
        m.annahme.wache.interval = 0.01  # statt 5 Sekunden
        await m.transport._event_handlers["on_client_connected"].handlers[0](m.transport, None)
        app.durchlauf_beenden(3)
        await warten(lambda: m.nachrichten("sitzung_beendet"), "sitzung_beendet")
        typen = [f.data["type"] for f in m.frames if isinstance(getattr(f, "data", None), dict)]
        assert typen.index("labor_durchlauf_ende") < typen.index("sitzung_beendet")
        assert m.werkz.freigaben.offen is False
        await m.klick(job="labor", id=zweite, entscheidung="ja")
        assert len(app.tickets(3)) == vorher + 1  # nach dem Ende wird nichts mehr geschrieben
        await m.session.cleanup()
        assert m.werkz.client._http.is_closed


# ---------- Reihenfolge der Starts: nichts bleibt halb offen ----------
class NachErstemAufruf(httpx.AsyncBaseTransport):
    """Der Dienst antwortet einmal (die Prüfung der Annahme) und ist dann weg (die Werkzeuge): der Start darf nicht halb gelingen."""

    def __init__(self, app, erlaubt: int = 1):
        self.innen, self.rest = httpx.ASGITransport(app=app), erlaubt

    async def handle_async_request(self, request):
        if self.rest <= 0:
            raise httpx.ConnectError("weg", request=request)
        self.rest -= 1
        return await self.innen.handle_async_request(request)


async def test_faellt_der_dienst_zwischen_annahme_und_werkzeugen_aus_gibt_es_eine_klare_ablehnung():
    app = Attrappe()
    m = Mitschnitt()
    with labor_testhilfe.dienst(app, transport=NachErstemAufruf(app)):
        with pytest.raises(labor.LaborAbgelehnt) as e:
            await m.aufbauen(LABOR)
    assert (e.value.status, e.value.code) == (503, labor_durchlauf.CODE_DIENST)  # die Person bekommt einen Satz, keine Ausnahme aus dem Dienstclient
    assert m.zugang._zu  # der bei der Prüfung gebaute Modellclient ist wieder zu


async def test_ein_fremdes_werkzeug_der_labor_werkzeuge_bricht_die_sitzung_ab():
    """Die Erlaubnisliste gilt für das Endergebnis, auch für das, was labor_werkzeuge nach der ersten Prüfung dazutut."""
    echt = labor_werkzeuge.LaborSitzung.registrieren

    def mit_fremdem(self, llm):
        echt(self, llm)
        llm.register_function("fernzugriff_oeffnen", mock.AsyncMock())

    m = Mitschnitt()
    with labor_testhilfe.dienst(), mock.patch.object(labor_werkzeuge.LaborSitzung, "registrieren", mit_fremdem):
        with pytest.raises(labor.LaborPolitikFehler):
            await m.aufbauen(LABOR)
    assert m.zugang._zu and m.werkz.client._http.is_closed  # nichts bleibt offen
