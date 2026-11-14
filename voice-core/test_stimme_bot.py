"""Stimmabdruck in der Sitzung (bot.py): Pipeline, Werkzeugsperre, Client-Nachricht, Meldungen, verworfene Züge, Labor nie.

Rauchtest ohne Netz wie test_smoke_bot.py und test_labor_bot.py (Datenbank und Modell sind Ersatz).
Ausführen:  python -m pytest -q test_stimme_bot.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
from pipecat.processors.frameworks.rtvi import RTVIProcessor, RTVIServerMessageFrame

import labor_limit
import labor_testhilfe
import labor_werkzeuge
import memory as m
import test_smoke_bot as smoke
from audio_monitor import AudioInputMonitor
from labor_attrappe import Attrappe
from ja_waechter import JaWaechter
from stimme_gate import EIGEN, FREMD, MARKE, UNKLAR
from stimme_modell import DIM
from stimme_pruefer import Stimmpruefer
from stimme_sitzung import SPRECHER_UNSICHER, StimmSitzung
from stimme_waechter import StimmWaechter
from test_labor_bot import Beobachter, laborprofil
from test_stimme_speicher import FakeDb
from auth_gate import Identity

bot = smoke.bot
USER = smoke.USER
BOT_QUELLE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


class BotDb(FakeDb):
    """Datenbank-Ersatz für build_session: Gedächtnis leer, Abdruck wie in FakeDb."""

    async def fetch(self, sql, *args):
        return []

    async def fetchrow(self, sql, *args):
        return None

    async def executemany(self, sql, rows):
        return None


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch):
    for name, wert in smoke.DUMMY_ENV.items():
        monkeypatch.setenv(name, wert)
    monkeypatch.delenv("WAMO_STIMME_PRUEFUNG", raising=False)
    with mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)), \
         mock.patch.object(labor_limit, "LIMITS", labor_limit.LimitRegister()):
        yield


async def bauen(identity, *, store: m.MemoryStore | None = None):
    llms = []
    echtes = bot.gespraech_llm

    async def llm_merken(*args, **kwargs):
        ergebnis = await echtes(*args, **kwargs)
        llms.append(ergebnis[0])
        return ergebnis

    patches = [mock.patch.object(bot, "Pipeline", wraps=bot.Pipeline), mock.patch.object(bot, "gespraech_llm", side_effect=llm_merken)]
    if store is not None:
        patches.append(mock.patch.object(bot.MemoryStore, "from_env", return_value=store))
    with patches[0] as pipeline, patches[1]:
        if store is not None:
            with patches[2]:
                session = await bot.build_session(smoke.FakeTransport(), identity)
        else:
            session = await bot.build_session(smoke.FakeTransport(), identity)
    return session, pipeline.call_args.args[0], llms[0]


async def abwarten():
    for _ in range(200):
        await asyncio.sleep(0)


async def nachricht(session, typ: str, daten):
    await session.task.rtvi._call_event_handler("on_client_message", types.SimpleNamespace(type=typ, data=daten))
    await abwarten()


def gesendet(queue_frames: mock.AsyncMock) -> list[dict]:
    return [f.data for aufruf in queue_frames.await_args_list for f in aufruf.args[0] if isinstance(f, RTVIServerMessageFrame)]


@pytest.mark.parametrize("rolle", ["mitglied", "admin"])
async def test_pruefer_sitzt_hinter_dem_mikrofon_und_waechter_vor_dem_aggregator(rolle):
    session, stufen, _llm = await bauen(Identity(USER, "Erika", rolle, "sie"))
    pruefer = [i for i, p in enumerate(stufen) if isinstance(p, Stimmpruefer)]
    waechter = [i for i, p in enumerate(stufen) if isinstance(p, StimmWaechter)]
    nutzer = [i for i, p in enumerate(stufen) if isinstance(p, LLMUserAggregator)]
    monitor = [i for i, p in enumerate(stufen) if isinstance(p, AudioInputMonitor)]
    assert len(pruefer) == len(waechter) == len(nutzer) == 1
    assert pruefer[0] == monitor[0] + 1  # direkt hinter dem Mikrofon, vor der Spracherkennung
    assert waechter[0] == nutzer[0] - 1  # direkt vor dem Aggregator: hinter der Spracherkennung und allen Textfiltern
    assert stufen[pruefer[0] + 1] is not stufen[waechter[0]]
    assert session.stimme is not None and session.stimme.pruefer is stufen[pruefer[0]] and session.stimme.waechter is stufen[waechter[0]]
    await session.cleanup()


@pytest.mark.parametrize("art", ["platz", "schaufenster"])
async def test_laborsitzung_hat_weder_pruefer_noch_waechter_noch_stimmsitzung(art):
    b = Beobachter()
    with labor_testhilfe.dienst(b.app), mock.patch.object(labor_werkzeuge, "labor_sitzung", b), \
            mock.patch.object(bot, "Pipeline", wraps=bot.Pipeline) as pipeline:
        session = await bot.build_session(smoke.FakeTransport(), laborprofil(art))
    stufen = pipeline.call_args.args[0]
    assert not [p for p in stufen if isinstance(p, (Stimmpruefer, StimmWaechter))]
    assert session.stimme is None
    await session.cleanup()
    await b.client.aclose()


async def test_laborprofil_bekommt_auf_stimmabdruck_keine_antwort_und_keinen_einlernschritt():
    b = Beobachter()
    with labor_testhilfe.dienst(b.app), mock.patch.object(labor_werkzeuge, "labor_sitzung", b):
        session = await bot.build_session(smoke.FakeTransport(), laborprofil())
    with mock.patch.object(StimmSitzung, "nachricht", mock.AsyncMock()) as stimme_nachricht:
        session.task.queue_frames = mock.AsyncMock()
        for aktion in ({"aktion": "start", "einwilligung": "stimme-1"}, {"aktion": "loeschen"}, {"aktion": "abbrechen"}):
            await nachricht(session, "stimmabdruck", aktion)
        stimme_nachricht.assert_not_awaited()
        assert [x for x in gesendet(session.task.queue_frames) if x.get("type") in ("stimmabdruck", "stimme")] == []
    await session.cleanup()
    await b.client.aclose()


async def test_laborprofil_bekommt_im_labor_den_status_aus():
    b = Beobachter()
    with labor_testhilfe.dienst(b.app), mock.patch.object(labor_werkzeuge, "labor_sitzung", b):
        session = await bot.build_session(smoke.FakeTransport(), laborprofil())
    session.task.queue_frames = mock.AsyncMock()
    await session.task.rtvi._call_event_handler("on_client_ready")
    await abwarten()
    assert {"type": "stimme", "status": "aus", "modus": "aus"} in gesendet(session.task.queue_frames)
    assert not [x for x in gesendet(session.task.queue_frames) if x.get("type") == "stimmabdruck_einwilligung"]
    await session.cleanup()
    await b.client.aclose()


async def test_werkzeuge_des_modells_laufen_durch_das_gate_ausser_den_offenen():
    session, _stufen, llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"))
    gate = session.stimme.gate
    funktionen = llm._functions
    assert getattr(funktionen["merken"].handler, MARKE) is gate
    assert getattr(funktionen["websuche"].handler, MARKE, None) is None  # offen: jede Stimme darf suchen
    assert getattr(funktionen["aktuelle_zeit"].handler, MARKE, None) is None
    assert getattr(funktionen["canvas_zeigen"].handler, MARKE, None) is None
    await session.cleanup()


BUERO_ENV = {  # Büro-Kern mit Entwicklungsaufträgen für dieses Profil (wie test_entwicklung_betrieb.py)
    "WAMO_BETRIEB": "buero", "WAMO_ENTWICKLUNG_BUERO": "an", "WAMO_ENTWICKLUNG_ORT": "sokrates2", "WAMO_ENTWICKLUNG_PROFILE": USER,
    "WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD": "3", "WAMO_ENTWICKLUNG_KOSTEN_TAG_USD": "10",
}


async def test_jedes_werkzeug_einer_voll_gebauten_sitzung_fragt_das_gate(monkeypatch):
    """Admin im Büro mit PC, Bildschirm, Bürogeräten, Entwicklung, Boss und Fernzugriff: jeder Eintrag in llm._functions und jeder Handler
    im Schema trägt die Marke des Gates dieser Sitzung oder steht in OFFENE_WERKZEUGE. Ein neues Werkzeug, das am Gate vorbei registriert
    wird, fällt hier auf."""
    import geraete_werkzeuge
    import rd_api
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from stimme_gate import OFFENE_WERKZEUGE

    for name, wert in BUERO_ENV.items():
        monkeypatch.setenv(name, wert)

    async def host_handler(params):
        await params.result_callback({"status": "success"})

    pc_werkzeuge = ToolsSchema(standard_tools=[FunctionSchema(n, "PC", {}, [], handler=host_handler)
                                               for n in ("programm_oeffnen", "fenster_auflisten", "datei_schreiben")])

    class Client:
        async def close(self):
            pass

    async def geraete(self, timeout=1.5):
        alles = ["steuern", "drucken", "scannen", "nas_suchen", "router_geraete", "unbekannte_geraete"]
        self._geraete = {"buero-1": {"kennung": "buero-1", "name": "Alles", "klasse": "drucker", "faehigkeiten": alles}}
        return self._baue_schemas()

    store = m.MemoryStore(USER, BotDb())
    with mock.patch.object(bot, "pc_host_fuer", return_value=True), mock.patch.object(bot, "host_client", return_value=Client()), \
            mock.patch.object(bot, "host_tools", mock.AsyncMock(return_value=pc_werkzeuge)), \
            mock.patch.object(bot, "entwicklung_problem", return_value=None), \
            mock.patch.object(bot, "entwicklung_quelle", return_value="api"), \
            mock.patch.object(geraete_werkzeuge.Buerogeraete, "schemas", geraete), mock.patch.object(rd_api, "aktiv", return_value=True):
        session, stufen, llm = await bauen(Identity(USER, "Erika", "admin", "sie"), store=store)
        await abwarten()  # die PC-Werkzeuge kommen im Hintergrund dazu (start_pc)
        gate = session.stimme.gate
        nutzer = next(p for p in stufen if isinstance(p, LLMUserAggregator))
        schemas = [s for s in nutzer.context.tools.standard_tools if isinstance(s, FunctionSchema) and s.handler is not None]
        namen = set(llm._functions) | {s.name for s in schemas}
        erwartet = {"merken", "fernzugriff_oeffnen", "aufgabe_verteilen", "entwicklung_vorbereiten", "bildschirm_ansehen",
                    "buerogeraet_status", "programm_oeffnen", "datei_schreiben"}
        assert erwartet <= namen, erwartet - namen  # die Sitzung ist wirklich voll gebaut
        offen = [n for n, f in llm._functions.items() if n not in OFFENE_WERKZEUGE and getattr(f.handler, MARKE, None) is not gate]
        offen += [s.name for s in schemas if s.name not in OFFENE_WERKZEUGE and getattr(s.handler, MARKE, None) is not gate]
        assert offen == []
        await session.cleanup()


def test_host_und_geraete_werkzeuge_mit_handler_im_schema_werden_eingepackt():
    """Pipecat registriert Handler aus dem Schema selbst: Host- und Bürogeräte-Werkzeuge müssen vor dem Kontext durch das Gate."""
    assert "schemas_absichern(pc_schemas, stimme.gate)" in BOT_QUELLE
    assert "schemas_absichern(geraete_schemas, stimme.gate)" in BOT_QUELLE


async def test_client_nachricht_stimmabdruck_geht_an_die_stimmsitzung():
    session, _stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"))
    daten = {"aktion": "start", "einwilligung": "stimme-1"}
    with mock.patch.object(StimmSitzung, "nachricht", mock.AsyncMock()) as stimme_nachricht:
        await nachricht(session, "stimmabdruck", daten)
        stimme_nachricht.assert_awaited_once_with(daten)
    await session.cleanup()


async def test_oberflaeche_bekommt_stand_und_einwilligungstext_beim_verbinden():
    store = m.MemoryStore(USER, BotDb())
    session, _stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"), store=store)
    session.task.queue_frames = mock.AsyncMock()
    await session.stimme.vorbereiten()  # läuft im Betrieb im Hintergrund nach dem Verbinden
    await session.task.rtvi._call_event_handler("on_client_ready")
    await abwarten()
    meldungen = gesendet(session.task.queue_frames)
    assert {"type": "stimme", "status": "fehlt", "modus": "streng"} in meldungen
    einwilligung = [x for x in meldungen if x.get("type") == "stimmabdruck_einwilligung"]
    assert len(einwilligung) == 1 and einwilligung[0]["version"] == "stimme-1" and einwilligung[0]["text"]
    await session.cleanup()


async def test_ohne_datenbank_ist_die_stimmpruefung_aus_und_es_gibt_keine_einwilligung():
    session, _stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"))
    session.task.queue_frames = mock.AsyncMock()
    await session.task.rtvi._call_event_handler("on_client_ready")
    await abwarten()
    meldungen = gesendet(session.task.queue_frames)
    assert {"type": "stimme", "status": "aus", "modus": "aus"} in meldungen
    assert not [x for x in meldungen if x.get("type") == "stimmabdruck_einwilligung"]
    await session.cleanup()


async def test_verworfener_zug_kommt_nicht_in_verlauf_und_gedaechtnis():
    store = m.MemoryStore(USER, BotDb())
    session, stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"), store=store)
    nutzer = next(p for p in stufen if isinstance(p, LLMUserAggregator))
    with mock.patch.object(m.MemoryStore, "log_turn", mock.AsyncMock()) as log_turn:
        session.stimme.waechter._verworfen = True  # der Wächter hat das Transkript einer fremden Stimme ersetzt
        await nutzer._call_event_handler("on_user_turn_stopped", None, types.SimpleNamespace(content=" "))
        await abwarten()  # Pipecat führt Ereignisbehandler als eigene Aufgaben aus
        log_turn.assert_not_awaited()
        assert session.stimme.zug_verworfen() is False  # die Markierung ist verbraucht
        await nutzer._call_event_handler("on_user_turn_stopped", None, types.SimpleNamespace(content="Hallo WAMO"))
        await abwarten()
        log_turn.assert_awaited_once_with(role="user", content="Hallo WAMO")
    await session.cleanup()


async def test_ein_zug_mit_text_trotz_markierung_wird_normal_behandelt():
    """Fremde und eigene Stimme im selben Zug: der Satz der Person bleibt erhalten, die Markierung wird verbraucht."""
    store = m.MemoryStore(USER, BotDb())
    session, stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"), store=store)
    nutzer = next(p for p in stufen if isinstance(p, LLMUserAggregator))
    with mock.patch.object(m.MemoryStore, "log_turn", mock.AsyncMock()) as log_turn:
        session.stimme.waechter._verworfen = True
        await nutzer._call_event_handler("on_user_turn_stopped", None, types.SimpleNamespace(content="Mach das Licht an"))
        await abwarten()
        log_turn.assert_awaited_once_with(role="user", content="Mach das Licht an")
        assert session.stimme.zug_verworfen() is False
    await session.cleanup()


def bereit_machen(session):
    """Profil mit Abdruck, Modell geladen, Modus streng: die Prüfung ist bereit."""
    gate = session.stimme.gate
    gate.setze_abdruck(np.eye(DIM, dtype=np.float32)[0])
    gate.modell_ok = True
    return gate


async def zug_beenden(stufen, text: str):
    nutzer = next(p for p in stufen if isinstance(p, LLMUserAggregator))
    await nutzer._call_event_handler("on_user_turn_stopped", None, types.SimpleNamespace(content=text))
    await abwarten()


async def test_nicht_sicher_eigener_zug_ist_gefaerbt_und_im_protokoll_markiert():
    """Unklar erkannte Äußerung: kein Lernen daraus, kein Hinweis des Ja-Wächters, im Protokoll mit Markierung."""
    store = m.MemoryStore(USER, BotDb())
    session, stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"), store=store)
    bereit_machen(session).eintragen(UNKLAR)
    with mock.patch.object(m.MemoryStore, "log_turn", mock.AsyncMock()) as log_turn:
        await zug_beenden(stufen, "Merk dir meine Nummer")
        log_turn.assert_awaited_once_with(role="user", content="Merk dir meine Nummer", speaker_label=SPRECHER_UNSICHER)
    assert session.turn.tainted is True
    assert session.turn.section_tainted(0, 5) is True  # das Lernen überspringt den Abschnitt
    ja = next(p for p in stufen if isinstance(p, JaWaechter))
    assert ja._gefaerbt() is True  # kein Hinweis "Ja auf Rückfrage"
    await session.cleanup()


async def test_sicher_eigener_zug_bleibt_ungefaerbt_und_ohne_markierung():
    store = m.MemoryStore(USER, BotDb())
    session, stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"), store=store)
    bereit_machen(session).eintragen(EIGEN)
    with mock.patch.object(m.MemoryStore, "log_turn", mock.AsyncMock()) as log_turn:
        await zug_beenden(stufen, "Mach das Licht an")
        log_turn.assert_awaited_once_with(role="user", content="Mach das Licht an")
    assert session.turn.tainted is False and session.turn.taint_marks == []
    ja = next(p for p in stufen if isinstance(p, JaWaechter))
    assert not ja._gefaerbt()
    await session.cleanup()


async def test_ohne_abdruck_aendert_sich_nichts_am_zug():
    store = m.MemoryStore(USER, BotDb())
    session, stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"), store=store)
    session.stimme.gate.setze_abdruck(None)
    with mock.patch.object(m.MemoryStore, "log_turn", mock.AsyncMock()) as log_turn:
        await zug_beenden(stufen, "Hallo WAMO")
        log_turn.assert_awaited_once_with(role="user", content="Hallo WAMO")
    assert session.turn.tainted is False
    await session.cleanup()


async def test_getippter_text_zaehlt_wie_die_eigene_stimme():
    with mock.patch.object(RTVIProcessor, "_handle_send_text", mock.AsyncMock()) as original:
        session, _stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"))
        gate = session.stimme.gate
        gate.eintragen(FREMD)
        assert gate.grund_der_stimme() == "fremde Stimme"
        await session.task.rtvi._handle_send_text("daten")
        original.assert_awaited_once()
        assert gate.grund_der_stimme() is None
    await session.cleanup()


async def test_startstrategie_fragt_bei_laufender_arbeit_den_waechter():
    """Werkzeug läuft oder Modell denkt, Modus streng mit Abdruck: erst das endgültige Transkript startet den Zug (stimme_waechter.nur_endgueltig)."""
    from pipecat.frames.frames import FunctionCallInProgressFrame, InterruptionFrame, LLMContextFrame
    from pipecat.processors.aggregators.llm_context import LLMContext
    from soft_interrupt import SoftInterruptGate

    store = m.MemoryStore(USER, BotDb())
    session, stufen, _llm = await bauen(Identity(USER, "Erika", "mitglied", "sie"), store=store)
    nutzer = next(p for p in stufen if isinstance(p, LLMUserAggregator))
    abfrage = nutzer._params.user_turn_strategies.start[0]._nur_endgueltig
    weiche = next(p for p in stufen if isinstance(p, SoftInterruptGate))
    wache = next(p for p in stufen if type(p).__name__ == "_WachTeil")._wache
    weiche._werkzeug_verfolgen(FunctionCallInProgressFrame(function_name="merken", tool_call_id="m1", arguments={},
                                                           cancel_on_interruption=True))
    assert abfrage is not None and abfrage() is False  # Abdruck noch nicht geladen: der Wächter verwirft nichts
    bereit_machen(session)
    assert abfrage() is True
    weiche._werkzeug_verfolgen(InterruptionFrame())
    assert abfrage() is False  # nichts läuft: der schnelle Weg über Zwischenstände bleibt
    assert wache.durchlassen(LLMContextFrame(context=LLMContext(messages=[{"role": "user", "content": "Wetter?"}])))
    assert abfrage() is True
    await session.cleanup()


def test_bot_laedt_den_abdruck_bis_der_stand_bekannt_ist():
    """Ein Fehler beim ersten Laden darf Werkzeuge nicht bis zum Sitzungsende sperren (stimme_sitzung.laden_bis_bekannt)."""
    assert "asyncio.create_task(stimme.laden_bis_bekannt())" in BOT_QUELLE
    assert "stimme.vorbereiten()" not in BOT_QUELLE


def test_bot_nennt_die_neuen_nachrichten_in_der_kopfzeile():
    for text in ("stimmabdruck_einwilligung", '"type": "stimme"', '"type": "stimmabdruck"'):
        assert text in BOT_QUELLE.split("Start lokal:")[0], text
