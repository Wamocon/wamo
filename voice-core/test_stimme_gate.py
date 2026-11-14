"""Tests für stimme_gate.py: wer Werkzeuge auslösen darf, Einstufung, Schalter, Absicherung der Werkzeuge.

Ausführen:  python -m pytest -q test_stimme_gate.py   (im Ordner voice-core/)
"""

import asyncio
import unittest
from unittest import mock

import numpy as np

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.processors.frameworks.rtvi import RTVIProcessor

import stimme_gate
from stimme_gate import (
    EIGEN,
    FREMD,
    KURZ,
    OFFENE_WERKZEUGE,
    UNKLAR,
    Schwellen,
    StimmGate,
    abgesichert,
    pruefmodus,
    schemas_absichern,
    schwellen_aus_env,
    tippen_beobachten,
    werkzeuge_absichern,
)
from stimme_modell import DIM

ABDRUCK = np.eye(DIM, dtype=np.float32)[0]


class Uhr:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def bereites_gate(modus="streng", uhr=None) -> StimmGate:
    gate = StimmGate(modus=modus, uhr=uhr or Uhr())
    gate.setze_abdruck(ABDRUCK)
    gate.modell_ok = True
    return gate


class Params:
    def __init__(self):
        self.result_callback = mock.AsyncMock()


class EinstufungTests(unittest.TestCase):
    def test_einstufung(self):
        gate = StimmGate()
        self.assertEqual(gate.einstufen(0.7, 2.0, False), EIGEN)
        self.assertEqual(gate.einstufen(0.9, 0.5, False), KURZ)  # zu kurz für "eigen", klingt aber nach der Person
        self.assertEqual(gate.einstufen(0.4, 0.5, False), UNKLAR)  # kurz und nicht nach der Person: das Vertrauen geht
        self.assertEqual(gate.einstufen(0.4, 0.5, True), UNKLAR)
        self.assertEqual(gate.einstufen(0.1, 0.5, False), FREMD)  # auch kurz klar fremd
        self.assertEqual(gate.einstufen(0.1, 2.0, False), FREMD)
        self.assertEqual(gate.einstufen(0.1, 2.0, True), UNKLAR)  # WAMO sprach gleichzeitig, es kann Echo sein
        self.assertEqual(gate.einstufen(0.4, 2.0, False), UNKLAR)

    def test_grenzen_gehoeren_zur_besseren_seite(self):
        gate = StimmGate()
        self.assertEqual(gate.einstufen(0.5, 1.0, False), EIGEN)
        self.assertEqual(gate.einstufen(0.3, 1.0, False), FREMD)

    def test_eigene_schwellen(self):
        gate = StimmGate(schwellen=Schwellen(eigen_min=0.6, fremd_max=0.2, min_secs=2.0))
        self.assertEqual(gate.einstufen(0.55, 3.0, False), UNKLAR)
        self.assertEqual(gate.einstufen(0.25, 3.0, False), UNKLAR)
        self.assertEqual(gate.einstufen(0.65, 1.5, False), KURZ)


class VertrauenTests(unittest.TestCase):
    def test_vertrauen_haelt_ueber_kurze_folgesaetze_und_laeuft_ab(self):
        uhr = Uhr()
        gate = bereites_gate(uhr=uhr)
        self.assertIsNotNone(gate.grund_der_stimme())  # noch nichts gehört
        gate.eintragen(EIGEN)
        gate.eintragen(KURZ)  # kurzes "Ja"
        self.assertIsNone(gate.grund_der_stimme())
        uhr.t += gate.schwellen.vertrauen_secs + 1
        self.assertIsNotNone(gate.grund_der_stimme())

    def test_vertrauen_aus_kurzen_aeusserungen_gilt_hoechstens_15_sekunden(self):
        uhr = Uhr()
        gate = bereites_gate(uhr=uhr)
        gate.eintragen(EIGEN)
        uhr.t += 10
        gate.eintragen(KURZ)
        self.assertIsNone(gate.grund_der_stimme())
        uhr.t += 4  # 14 s nach der sicheren Erkennung
        gate.eintragen(KURZ)  # eine weitere kurze Äußerung verlängert nichts
        self.assertIsNone(gate.grund_der_stimme())
        uhr.t += 2  # 16 s
        self.assertEqual(gate.grund_der_stimme(), "Stimme nur kurz gehört, nicht sicher erkannt")
        gate.eintragen(EIGEN)  # eine sichere Erkennung erneuert das Vertrauen
        self.assertIsNone(gate.grund_der_stimme())

    def test_ohne_kurze_aeusserung_gilt_das_vertrauen_bis_zur_langen_frist(self):
        uhr = Uhr()
        gate = bereites_gate(uhr=uhr)
        gate.eintragen(EIGEN)
        uhr.t += 60
        self.assertIsNone(gate.grund_der_stimme())

    def test_kurze_aeusserung_nach_getipptem_text_gilt_ebenfalls_hoechstens_15_sekunden(self):
        uhr = Uhr()
        gate = bereites_gate(uhr=uhr)
        gate.getippt()
        gate.eintragen(KURZ)
        uhr.t += 16
        self.assertIsNotNone(gate.grund_der_stimme())

    def test_kurz_ohne_vorheriges_vertrauen_gibt_keines(self):
        gate = bereites_gate()
        gate.eintragen(KURZ)
        self.assertEqual(gate.grund_der_stimme(), "Stimme noch nicht erkannt")

    def test_getippt_erneuert_das_kurz_vertrauen(self):
        uhr = Uhr()
        gate = bereites_gate(uhr=uhr)
        gate.eintragen(EIGEN)
        gate.eintragen(KURZ)
        uhr.t += 20
        self.assertIsNotNone(gate.grund_der_stimme())
        gate.getippt()
        self.assertIsNone(gate.grund_der_stimme())

    def test_loeschen_des_abdrucks_loescht_auch_den_kurz_zustand(self):
        uhr = Uhr()
        gate = bereites_gate(uhr=uhr)
        gate.eintragen(EIGEN)
        gate.eintragen(KURZ)
        gate.setze_abdruck(None)
        gate.setze_abdruck(ABDRUCK)
        gate.eintragen(EIGEN)
        uhr.t += 20
        self.assertIsNone(gate.grund_der_stimme())  # ein neuer Abdruck beginnt ohne Rest des alten Zustands

    def test_unklar_mit_genug_sprache_entzieht_das_vertrauen(self):
        gate = bereites_gate()
        gate.eintragen(EIGEN)
        gate.eintragen(UNKLAR)
        self.assertEqual(gate.grund_der_stimme(), "Stimme nicht sicher erkannt")

    def test_aeltere_aeusserung_ueberschreibt_getippten_text_nicht(self):
        uhr = Uhr()
        gate = bereites_gate(uhr=uhr)
        gehoert = uhr()  # ein Gast spricht ...
        uhr.t += 1
        gate.getippt()  # ... die Person tippt, während die Prüfung noch läuft
        gate.eintragen(FREMD, gehoert)
        self.assertIsNone(gate.grund_der_stimme())
        gate.eintragen(FREMD, uhr())  # spricht der Gast danach, zählt es
        self.assertEqual(gate.grund_der_stimme(), "fremde Stimme")

    def test_fremde_stimme_sperrt_bis_die_person_wieder_erkannt_wird_oder_tippt(self):
        gate = bereites_gate()
        gate.eintragen(EIGEN)
        gate.eintragen(FREMD)
        self.assertEqual(gate.grund_der_stimme(), "fremde Stimme")
        gate.getippt()
        self.assertIsNone(gate.grund_der_stimme())
        gate.eintragen(FREMD)
        gate.eintragen(EIGEN)
        self.assertIsNone(gate.grund_der_stimme())

    def test_stimme_vertraut_nur_nach_sicherer_erkennung_nicht_nach_tippen(self):
        gate = bereites_gate()
        gate.getippt()
        self.assertFalse(gate.stimme_vertraut())
        gate.eintragen(EIGEN)
        self.assertTrue(gate.stimme_vertraut())

    def test_zwei_sitzungen_teilen_keinen_zustand(self):
        a, b = bereites_gate(), bereites_gate()
        a.eintragen(FREMD)
        self.assertEqual(a.grund_der_stimme(), "fremde Stimme")
        self.assertEqual(b.grund_der_stimme(), "Stimme noch nicht erkannt")


class StatusTests(unittest.TestCase):
    def test_status_je_zustand(self):
        gate = StimmGate(modus="streng")
        self.assertEqual(gate.status, "laedt")
        gate.setze_abdruck(None)
        self.assertEqual(gate.status, "fehlt")
        gate.setze_abdruck(ABDRUCK)
        self.assertEqual(gate.status, "laedt")  # Abdruck da, Modell noch nicht geladen
        gate.modell_ok = True
        self.assertEqual(gate.status, "bereit")
        gate.modell_ok = False
        self.assertEqual(gate.status, "kaputt")
        gate.setze_abdruck(None, ok=False)
        self.assertEqual(gate.status, "kaputt")
        gate.modus = "aus"
        self.assertEqual(gate.status, "aus")

    def test_wirkt_nur_mit_abdruck(self):
        gate = StimmGate(modus="streng")
        self.assertFalse(gate.hat_abdruck)
        gate.setze_abdruck(ABDRUCK)
        self.assertTrue(gate.hat_abdruck)
        gate.setze_abdruck(None)
        self.assertFalse(gate.hat_abdruck)


class SperrTests(unittest.IsolatedAsyncioTestCase):
    async def test_streng_sperrt_mit_ergebnis_fuer_das_modell(self):
        gate = bereites_gate()
        gate.eintragen(FREMD)
        gesperrt = await gate.pruefen("programm_oeffnen")
        self.assertEqual(gesperrt["status"], "blocked")
        self.assertTrue(gesperrt["grund"].startswith("Stimmprüfung"))
        self.assertIn("Nicht erneut", gesperrt["hinweis"])

    async def test_offene_werkzeuge_und_modus_beobachten_und_aus_sperren_nie(self):
        self.assertEqual(OFFENE_WERKZEUGE, {"aktuelle_zeit", "websuche", "canvas_zeigen"})
        gate = bereites_gate()
        gate.eintragen(FREMD)
        for name in OFFENE_WERKZEUGE:
            self.assertIsNone(await gate.pruefen(name))
        gate.modus = "beobachten"
        self.assertIsNone(await gate.pruefen("programm_oeffnen"))
        gate.modus = "aus"
        self.assertIsNone(await gate.pruefen("programm_oeffnen"))

    async def test_ohne_abdruck_wird_nichts_gesperrt(self):
        gate = StimmGate(modus="streng", uhr=Uhr())
        gate.setze_abdruck(None)
        self.assertEqual(gate.status, "fehlt")
        self.assertIsNone(await gate.pruefen("programm_oeffnen"))
        self.assertIsNone(await gate.pruefen("merken"))

    async def test_streng_sperrt_solange_ein_vorhandener_abdruck_nicht_pruefbar_ist(self):
        gate = StimmGate(modus="streng")  # Abdruck lädt noch
        self.assertEqual(gate.status, "laedt")
        self.assertIsNotNone(await gate.pruefen("programm_oeffnen"))
        gate.setze_abdruck(None, ok=False)  # Datenbank nicht lesbar: unbekannt, ob es einen Abdruck gibt
        self.assertEqual(gate.status, "kaputt")
        self.assertIsNotNone(await gate.pruefen("programm_oeffnen"))
        gate.setze_abdruck(ABDRUCK)
        gate.modell_ok = False  # Abdruck da, Modell fehlt
        self.assertIsNotNone(await gate.pruefen("programm_oeffnen"))

    async def test_getippter_text_geht_auch_wenn_die_pruefung_nicht_bereit_ist(self):
        gate = StimmGate(modus="streng", uhr=Uhr())
        gate.setze_abdruck(None, ok=False)
        gate.getippt()
        self.assertIsNone(await gate.pruefen("programm_oeffnen"))

    async def test_wartet_auf_die_laufende_pruefung(self):
        gate = bereites_gate()

        async def pruefen():
            await asyncio.sleep(0.05)
            gate.eintragen(EIGEN)

        gate.verfolgen(asyncio.create_task(pruefen()))
        self.assertIsNone(await gate.pruefen("programm_oeffnen"))

    async def test_zu_lange_pruefung_sperrt_statt_altes_ergebnis(self):
        gate = bereites_gate()
        gate.eintragen(EIGEN)
        langsam = asyncio.create_task(asyncio.sleep(1))
        gate.verfolgen(langsam)
        with mock.patch.object(stimme_gate, "WARTEN_WERKZEUG_SECS", 0.02):
            gesperrt = await gate.pruefen("programm_oeffnen")
        langsam.cancel()
        self.assertIn("dauert zu lange", gesperrt["grund"])


class SchalterTests(unittest.TestCase):
    def test_modus_standard_ist_streng_und_unbekanntes_auch(self):
        self.assertEqual(pruefmodus(""), "streng")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(pruefmodus(None), "streng")
        for modus in ("aus", "beobachten", "streng"):
            self.assertEqual(pruefmodus(modus), modus)
            self.assertEqual(pruefmodus(f"  {modus.upper()} "), modus)
        self.assertEqual(pruefmodus("strenge"), "streng")

    def test_unbekannter_modus_wird_einmal_gewarnt(self):
        stimme_gate._GEWARNT.clear()
        with mock.patch.object(stimme_gate.logger, "warning") as warnung:
            pruefmodus("vielleicht")
            pruefmodus("vielleicht")
        self.assertEqual(warnung.call_count, 1)

    def test_modus_aus_der_umgebung(self):
        with mock.patch.dict("os.environ", {"WAMO_STIMME_PRUEFUNG": "beobachten"}):
            self.assertEqual(StimmGate.aus_umgebung().modus, "beobachten")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(StimmGate.aus_umgebung().modus, "streng")

    def test_schwellen_standard(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            s = schwellen_aus_env()
        self.assertEqual((s.eigen_min, s.fremd_max, s.min_secs, s.vertrauen_secs), (0.5, 0.3, 1.0, 120.0))

    def test_schwellen_aus_der_umgebung_mit_komma(self):
        umgebung = {"WAMO_STIMME_EIGEN_MIN": "0,6", "WAMO_STIMME_FREMD_MAX": "0.25", "WAMO_STIMME_MIN_SECS": "1.5",
                    "WAMO_STIMME_VERTRAUEN_SECS": "60"}
        with mock.patch.dict("os.environ", umgebung, clear=True):
            s = schwellen_aus_env()
        self.assertEqual((s.eigen_min, s.fremd_max, s.min_secs, s.vertrauen_secs), (0.6, 0.25, 1.5, 60.0))

    def test_unbrauchbare_zahlen_heissen_standard(self):
        umgebung = {"WAMO_STIMME_VERTRAUEN_SECS": "nan", "WAMO_STIMME_EIGEN_MIN": "x", "WAMO_STIMME_MIN_SECS": "inf",
                    "WAMO_STIMME_FREMD_MAX": "9"}
        with mock.patch.dict("os.environ", umgebung, clear=True):
            s = schwellen_aus_env()
        self.assertEqual(s, Schwellen())

    def test_fremd_muss_unter_eigen_liegen_sonst_gelten_beide_standardwerte(self):
        with mock.patch.dict("os.environ", {"WAMO_STIMME_EIGEN_MIN": "0.4", "WAMO_STIMME_FREMD_MAX": "0.45"}, clear=True):
            self.assertEqual(schwellen_aus_env(), Schwellen())

    def test_kurz_vertrauen_standard_und_schalter(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(schwellen_aus_env().kurz_vertrauen_secs, 15.0)
        with mock.patch.dict("os.environ", {"WAMO_STIMME_KURZ_VERTRAUEN_SECS": "30"}, clear=True):
            self.assertEqual(schwellen_aus_env().kurz_vertrauen_secs, 30.0)
        for roh in ("0", "-5", "x", "nan", "9999"):
            with mock.patch.dict("os.environ", {"WAMO_STIMME_KURZ_VERTRAUEN_SECS": roh}, clear=True):
                self.assertEqual(schwellen_aus_env().kurz_vertrauen_secs, 15.0, roh)

    def test_kurz_vertrauen_bleibt_wenn_fremd_und_eigen_nicht_zusammenpassen(self):
        umgebung = {"WAMO_STIMME_EIGEN_MIN": "0.4", "WAMO_STIMME_FREMD_MAX": "0.45", "WAMO_STIMME_KURZ_VERTRAUEN_SECS": "30"}
        with mock.patch.dict("os.environ", umgebung, clear=True):
            s = schwellen_aus_env()
        self.assertEqual((s.eigen_min, s.fremd_max, s.kurz_vertrauen_secs), (0.5, 0.3, 30.0))

    def test_vertrauen_hat_eine_grenze_nach_unten(self):
        with mock.patch.dict("os.environ", {"WAMO_STIMME_VERTRAUEN_SECS": "0"}, clear=True):
            self.assertEqual(schwellen_aus_env().vertrauen_secs, 120.0)


class WerkzeugTests(unittest.IsolatedAsyncioTestCase):
    async def test_gesperrt_ruft_den_handler_nicht_auf(self):
        gate = bereites_gate()
        gate.eintragen(FREMD)
        handler = mock.AsyncMock()
        params = Params()
        await abgesichert("programm_oeffnen", handler, gate)(params)
        handler.assert_not_awaited()
        self.assertEqual(params.result_callback.await_args.args[0]["status"], "blocked")

    async def test_handler_fuer_alle_werkzeuge_prueft_den_aufgerufenen_namen(self):
        gate = bereites_gate()
        gate.eintragen(FREMD)
        handler = mock.AsyncMock()
        eingepackt = abgesichert(None, handler, gate)
        params = Params()
        params.function_name = "programm_oeffnen"
        await eingepackt(params)
        handler.assert_not_awaited()
        params.function_name = "aktuelle_zeit"
        await eingepackt(params)
        handler.assert_awaited_once()

    async def test_erlaubt_ruft_den_handler_auf(self):
        gate = bereites_gate()
        gate.eintragen(EIGEN)
        handler = mock.AsyncMock()
        params = Params()
        await abgesichert("programm_oeffnen", handler, gate)(params)
        handler.assert_awaited_once_with(params)

    def test_offen_und_schon_geprueft_bleiben_unveraendert(self):
        gate = bereites_gate()
        handler = mock.AsyncMock()
        self.assertIs(abgesichert("aktuelle_zeit", handler, gate), handler)
        eingepackt = abgesichert("merken", handler, gate)
        self.assertIs(abgesichert("merken", eingepackt, gate), eingepackt)
        anderes = bereites_gate()
        self.assertIsNot(abgesichert("merken", eingepackt, anderes), eingepackt)  # ein anderes Gate prüft selbst

    def test_tool_options_bleiben_erhalten(self):
        @tool_options(timeout_secs=30)
        async def handler(params):
            pass

        self.assertEqual(abgesichert("bildschirm_ansehen", handler, bereites_gate())._pipecat_timeout_secs, 30)

    def test_registrierung_am_llm_laeuft_durch_das_gate(self):
        gate = bereites_gate()
        llm = mock.Mock()
        original = llm.register_function
        werkzeuge_absichern(llm, gate)
        handler = mock.AsyncMock()
        llm.register_function("merken", handler, timeout_secs=5)
        name, registriert = original.call_args.args
        self.assertEqual(name, "merken")
        self.assertIs(registriert._wamo_stimme_gate, gate)
        self.assertEqual(original.call_args.kwargs, {"timeout_secs": 5})
        llm.register_function("aktuelle_zeit", handler)
        self.assertIs(original.call_args.args[1], handler)

    def test_echtes_llm_registriert_eingepackte_handler(self):
        from pipecat.services.anthropic.llm import AnthropicLLMService

        gate = bereites_gate()
        llm = AnthropicLLMService(api_key="test")
        werkzeuge_absichern(llm, gate)
        llm.register_function("merken", mock.AsyncMock())
        self.assertIs(llm._functions["merken"].handler._wamo_stimme_gate, gate)
        # Host-Werkzeuge kommen mit Handler im Schema; Pipecat gleicht sie bei jedem Kontext ab
        from pipecat.adapters.schemas.tools_schema import ToolsSchema

        tools = ToolsSchema(standard_tools=schemas_absichern(
            [FunctionSchema("programm_oeffnen", "x", {}, [], handler=mock.AsyncMock())], gate))
        llm._sync_registered_tool_handlers(tools)
        erster = llm._functions["programm_oeffnen"].handler
        llm._sync_registered_tool_handlers(tools)
        self.assertIs(llm._functions["programm_oeffnen"].handler, erster)
        self.assertIs(erster._wamo_stimme_gate, gate)

    def test_host_werkzeuge_werden_genau_einmal_eingepackt(self):
        gate = bereites_gate()
        roh = mock.AsyncMock()
        schemas = [FunctionSchema("programm_oeffnen", "x", {}, [], handler=roh), FunctionSchema("ohne", "y", {}, [])]
        neu = schemas_absichern(schemas, gate)
        self.assertEqual([s.name for s in neu], ["programm_oeffnen", "ohne"])
        self.assertIsNot(neu[0].handler, roh)
        self.assertIsNone(neu[1].handler)
        self.assertIs(abgesichert(neu[0].name, neu[0].handler, gate), neu[0].handler)  # nicht noch einmal einpacken
        self.assertEqual(neu[0].required, [])
        self.assertEqual(neu[0].description, "x")


class GetipptTests(unittest.IsolatedAsyncioTestCase):
    def test_pipecat_hat_den_haken_noch(self):
        self.assertTrue(hasattr(RTVIProcessor, "_handle_send_text"))

    async def test_getippter_text_zaehlt_wie_die_eigene_stimme(self):
        gate = bereites_gate()
        gate.eintragen(FREMD)
        rtvi = mock.Mock()
        original = mock.AsyncMock()
        rtvi._handle_send_text = original
        self.assertTrue(tippen_beobachten(rtvi, gate))
        await rtvi._handle_send_text("daten")
        original.assert_awaited_once_with("daten")
        self.assertIsNone(gate.grund_der_stimme())

    def test_fehlender_haken_wird_gemeldet(self):
        self.assertFalse(tippen_beobachten(object(), bereites_gate()))


if __name__ == "__main__":
    unittest.main()
