"""Tests für die Werkzeug-Wachen (werkzeug_wache.py), nachgestellt nach der Sitzung vom 31.10.2026:

* 12:45:14 bildschirm_ansehen und 12:45:41 maus_scrollen wurden durch einen Einwurf abgebrochen, das Modell sah "CANCELLED".
* 12:44:34 und 12:44:35 zwei Amazon-Suchen im selben Zug (zwei Antworten des Modells hintereinander).
"""

import asyncio
import json
import unittest
from unittest import mock

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import LLMContextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams
from pipecat.tests.utils import run_test

import bot
from tools import TurnState
from werkzeug_wache import (
    CANCELLED,
    ERGEBNIS_SCHON_ERLEDIGT,
    SEITEN_MAX_JE_ZUG,
    AbbruchHinweis,
    DoppelWache,
    abbruch_hinweis_an,
    abbruch_text,
    abbrueche_umschreiben,
    hinweis_anhaengen,
    ist_misserfolg,
    webseite_von,
    werkzeug_doppelt_an,
)


def verlauf_mit_abbruch(name: str = "bildschirm_ansehen", ergebnis: str = CANCELLED) -> list[dict]:
    return [
        {"role": "user", "content": "Schau mal auf den Bildschirm"},
        {"role": "assistant", "tool_calls": [{"id": "t1", "type": "function",
                                              "function": {"name": name, "arguments": "{}"}}]},
        {"role": "tool", "content": ergebnis, "tool_call_id": "t1"},
        {"role": "user", "content": "und sag mir, was im Browser steht"},
    ]


class SchalterTest(unittest.TestCase):
    def test_standard_an_und_abschaltbar(self):
        self.assertTrue(abbruch_hinweis_an(""))
        self.assertFalse(abbruch_hinweis_an("aus"))
        self.assertTrue(werkzeug_doppelt_an(""))
        self.assertFalse(werkzeug_doppelt_an("0"))


class AbbruchTest(unittest.TestCase):
    def test_lesewerkzeug_soll_erneut_aufgerufen_werden(self):
        m = verlauf_mit_abbruch("bildschirm_ansehen")
        self.assertEqual(abbrueche_umschreiben(m), 1)
        text = m[2]["content"]
        self.assertNotEqual(text, CANCELLED)
        self.assertIn("ABGEBROCHEN", text)
        self.assertIn("noch einmal auf", text)
        self.assertIn("bildschirm_ansehen", text)

    def test_aktion_wird_nicht_blind_wiederholt(self):
        m = verlauf_mit_abbruch("maus_scrollen")
        abbrueche_umschreiben(m)
        self.assertIn("Ergebnis unbekannt", m[2]["content"])
        self.assertIn("nicht blind", m[2]["content"])
        self.assertIn("gewirkt haben", m[2]["content"])

    def test_unbekanntes_werkzeug_gilt_vorsichtshalber_als_aktion(self):
        m = verlauf_mit_abbruch("gibts_nicht")
        abbrueche_umschreiben(m)
        self.assertIn("nicht blind", m[2]["content"])

    def test_ohne_namen_im_verlauf_der_allgemeine_satz(self):
        self.assertIn("Ob es etwas bewirkt hat, ist unklar", abbruch_text(None))

    def test_ordentliche_ergebnisse_bleiben_unberuehrt(self):
        m = verlauf_mit_abbruch("maus_scrollen", ergebnis='{"status": "success"}')
        self.assertEqual(abbrueche_umschreiben(m), 0)
        self.assertEqual(m[2]["content"], '{"status": "success"}')
        m2 = verlauf_mit_abbruch("maus_scrollen", ergebnis="IN_PROGRESS")
        self.assertEqual(abbrueche_umschreiben(m2), 0)

    def test_nur_das_genaue_wort_zaehlt(self):
        m = verlauf_mit_abbruch(ergebnis="Die Suche wurde CANCELLED nach dem Wort")
        self.assertEqual(abbrueche_umschreiben(m), 0)

    def test_fremde_nachrichten_und_fehlende_aufrufe_werfen_nicht(self):
        m = [{"role": "tool", "content": CANCELLED, "tool_call_id": "x"}, object(), {"role": "assistant", "tool_calls": [{"id": 1}]}]
        self.assertEqual(abbrueche_umschreiben(m), 1)
        self.assertIn("ABGEBROCHEN", m[0]["content"])


class AbbruchProzessorTest(unittest.IsolatedAsyncioTestCase):
    async def test_modell_sieht_nicht_mehr_cancelled(self):
        ctx = LLMContext(messages=verlauf_mit_abbruch("bildschirm_ansehen"))
        prozessor = AbbruchHinweis()
        unten, _ = await run_test(Pipeline([prozessor]), frames_to_send=[LLMContextFrame(context=ctx)])
        self.assertEqual(sum(isinstance(f, LLMContextFrame) for f in unten), 1)
        self.assertNotIn(CANCELLED, [m.get("content") for m in ctx.messages])
        self.assertEqual(prozessor.umgeschrieben, 1)

    async def test_ohne_abbruch_geht_der_verlauf_unveraendert_durch(self):
        ctx = LLMContext(messages=[{"role": "user", "content": "Hallo"}])
        vorher = json.dumps(ctx.messages)
        unten, _ = await run_test(Pipeline([AbbruchHinweis()]), frames_to_send=[LLMContextFrame(context=ctx)])
        self.assertEqual(sum(isinstance(f, LLMContextFrame) for f in unten), 1)
        self.assertEqual(json.dumps(ctx.messages), vorher)


class ZugUhr:
    def __init__(self):
        self.nr = 1
        self.t = 100.0


def wache() -> tuple[DoppelWache, ZugUhr]:
    z = ZugUhr()
    return DoppelWache(turn_nr=lambda: z.nr, uhr=lambda: z.t), z


class DoppelWacheTest(unittest.TestCase):
    def test_zwei_suchen_im_selben_zug_laufen_beide_die_zweite_mit_hinweis(self):
        """Das Protokoll vom 31.10.: Elektrofahrzeuge, 1,2 s später E-Bike Fahrrad."""
        w, z = wache()
        a = w.pruefen("webseite_oeffnen", {"url": "https://www.amazon.de/s?k=Elektrofahrzeuge", "browser": "chrome"})
        self.assertEqual((a.aktion, a.hinweis), ("ausfuehren", None))
        z.t += 1.2
        b = w.pruefen("webseite_oeffnen", {"url": "https://www.amazon.de/s?k=E-Bike+Fahrrad", "browser": "chrome"})
        self.assertEqual(b.aktion, "ausfuehren")
        self.assertIn("1 Seite(n)", b.hinweis)
        self.assertIn("Alle Tabs sind offen", b.hinweis)
        self.assertEqual(w.hinweise, 1)

    def test_andere_webseite_ist_ein_echter_zweiter_auftrag(self):
        w, _ = wache()
        w.pruefen("webseite_oeffnen", {"url": "https://www.amazon.de"})
        b = w.pruefen("webseite_oeffnen", {"url": "https://www.ebay.de"})
        self.assertEqual((b.aktion, b.hinweis), ("ausfuehren", None))

    def test_dieselbe_adresse_im_selben_zug_wird_nicht_wiederholt(self):
        w, _ = wache()
        w.pruefen("webseite_oeffnen", {"url": "https://www.amazon.de/s?k=Fahrrad"})
        # www., Schrägstrich und Großschreibung am Ende ändern nichts
        b = w.pruefen("webseite_oeffnen", {"url": "https://amazon.de/s?k=Fahrrad#top"})
        self.assertEqual(b.aktion, "ueberspringen")
        self.assertEqual(b.ergebnis, ERGEBNIS_SCHON_ERLEDIGT)
        self.assertEqual(w.uebersprungen, 1)

    def test_ergebnis_ist_eine_kopie(self):
        w, _ = wache()
        w.pruefen("programm_oeffnen", {"name": "Outlook"})
        b = w.pruefen("programm_oeffnen", {"name": "outlook "})
        b.ergebnis["summary"] = "geändert"
        self.assertNotEqual(ERGEBNIS_SCHON_ERLEDIGT["summary"], "geändert")

    def test_neuer_beitrag_der_person_setzt_alles_zurueck(self):
        w, z = wache()
        w.pruefen("webseite_oeffnen", {"url": "https://www.amazon.de/s?k=Fahrrad"})
        z.nr += 1  # die Person hat etwas Neues gesagt
        b = w.pruefen("webseite_oeffnen", {"url": "https://www.amazon.de/s?k=Fahrrad"})
        self.assertEqual((b.aktion, b.hinweis), ("ausfuehren", None))

    def test_mehrere_programme_im_selben_zug_sind_erlaubt(self):
        w, _ = wache()
        for name in ("Outlook", "Teams", "VS Code"):
            self.assertEqual(w.pruefen("programm_oeffnen", {"name": name}).aktion, "ausfuehren")

    def test_ab_der_vierten_seite_derselben_webseite_wird_nicht_mehr_geoeffnet(self):
        w, _ = wache()
        for i in range(SEITEN_MAX_JE_ZUG):
            self.assertEqual(w.pruefen("webseite_oeffnen", {"url": f"https://www.amazon.de/s?k=t{i}"}).aktion, "ausfuehren")
        v = w.pruefen("webseite_oeffnen", {"url": "https://www.amazon.de/s?k=zu_viel"})
        self.assertEqual(v.aktion, "ueberspringen")
        self.assertEqual(v.grund, "zu_viele")

    def test_andere_werkzeuge_werden_nie_beeinflusst(self):
        w, _ = wache()
        for _ in range(5):
            u = w.pruefen("tasten_druecken", {"kombination": "enter"})
            self.assertEqual((u.aktion, u.hinweis), ("ausfuehren", None))
            self.assertEqual(w.pruefen("maus_scrollen", {"richtung": "runter"}).aktion, "ausfuehren")

    def test_fehlende_argumente_werfen_nicht(self):
        w, _ = wache()
        self.assertEqual(w.pruefen("webseite_oeffnen", None).aktion, "ausfuehren")
        self.assertEqual(w.pruefen("webseite_oeffnen", {"url": 5}).aktion, "ausfuehren")

    def test_webseite_von(self):
        self.assertEqual(webseite_von({"url": "https://WWW.Amazon.de/s?k=1"}), "amazon.de")
        self.assertIsNone(webseite_von({"url": "kein url"}))
        self.assertIsNone(webseite_von({}))


class HinweisAnhaengenTest(unittest.TestCase):
    def test_dict_jsontext_und_text(self):
        self.assertEqual(hinweis_anhaengen({"status": "success"}, "H")["hinweis"], "H")
        self.assertEqual(json.loads(hinweis_anhaengen('{"status": "success"}', "H"))["hinweis"], "H")
        self.assertEqual(hinweis_anhaengen("geöffnet", "H"), "geöffnet H")
        self.assertEqual(hinweis_anhaengen(5, "H"), 5)


class _ParamsMixin:
    def params(self, name: str, url: str, ergebnisse: list) -> FunctionCallParams:
        async def callback(ergebnis, *args, **kwargs):
            ergebnisse.append(ergebnis)

        return FunctionCallParams(function_name=name, tool_call_id=f"id-{url}", arguments={"url": url}, llm=mock.Mock(),
                                  pipeline_worker=mock.Mock(), context=LLMContext(), result_callback=callback)


class GateMitWacheTest(_ParamsMixin, unittest.IsolatedAsyncioTestCase):
    """gate_pc_tools mit Wache: der Host-Handler wird beim Überspringen nicht aufgerufen, ein Hinweis erreicht das Ergebnis."""

    async def test_ablauf_wie_im_protokoll(self):
        aufrufe = []

        async def host(params):
            aufrufe.append(params.arguments["url"])
            await params.result_callback(json.dumps({"status": "success", "summary": f"{params.arguments['url']} geoeffnet"}))

        turn = TurnState()
        turn.new_user_turn("Such bei Amazon")
        doppel = DoppelWache(turn_nr=lambda: turn.turn_nr)
        schema = FunctionSchema("webseite_oeffnen", "x", {}, [], handler=host)
        gated = bot.gate_pc_tools([schema], lambda: True, lambda: None, doppel)[0]
        ergebnisse: list = []
        await gated.handler(self.params("webseite_oeffnen", "https://www.amazon.de/s?k=Elektrofahrzeuge", ergebnisse))
        await gated.handler(self.params("webseite_oeffnen", "https://www.amazon.de/s?k=E-Bike+Fahrrad", ergebnisse))
        await gated.handler(self.params("webseite_oeffnen", "https://www.amazon.de/s?k=E-Bike+Fahrrad", ergebnisse))
        self.assertEqual(len(aufrufe), 2)  # die dritte (gleiche) Adresse läuft nicht noch einmal
        self.assertNotIn("hinweis", json.loads(ergebnisse[0]))
        self.assertIn("Alle Tabs sind offen", json.loads(ergebnisse[1])["hinweis"])
        self.assertEqual(ergebnisse[2]["status"], "duplicate")
        # neuer Beitrag der Person: dieselbe Adresse darf wieder
        turn.new_user_turn("Mach das nochmal")
        await gated.handler(self.params("webseite_oeffnen", "https://www.amazon.de/s?k=E-Bike+Fahrrad", ergebnisse))
        self.assertEqual(len(aufrufe), 3)

    async def test_ohne_wache_bleibt_alles_wie_vorher(self):
        aufrufe = []

        async def host(params):
            aufrufe.append(1)
            await params.result_callback({"status": "success"})

        gated = bot.gate_pc_tools([FunctionSchema("webseite_oeffnen", "x", {}, [], handler=host)], lambda: True, lambda: None)[0]
        ergebnisse: list = []
        for _ in range(2):
            await gated.handler(self.params("webseite_oeffnen", "https://www.amazon.de", ergebnisse))
        self.assertEqual(len(aufrufe), 2)

    async def test_abgelehnte_steuerung_zaehlt_nicht_als_aufruf(self):
        """Schlägt ein Aufruf wegen ausgeschalteter PC-Steuerung fehl (Zug 3), darf er danach laufen."""
        aufrufe = []

        async def host(params):
            aufrufe.append(1)
            await params.result_callback({"status": "success"})

        an = {"wert": False}
        doppel = DoppelWache(turn_nr=lambda: 1)
        gated = bot.gate_pc_tools([FunctionSchema("webseite_oeffnen", "x", {}, [], handler=host)], lambda: an["wert"],
                                  lambda: None, doppel)[0]
        ergebnisse: list = []
        await gated.handler(self.params("webseite_oeffnen", "https://www.amazon.de", ergebnisse))
        an["wert"] = True
        await gated.handler(self.params("webseite_oeffnen", "https://www.amazon.de", ergebnisse))
        self.assertEqual(len(aufrufe), 1)


class MisserfolgTest(unittest.TestCase):
    def test_erkennung(self):
        for ergebnis in ({"status": "error"}, '{"status": "rejected", "summary": "x"}', {"status": "needs_approval"},
                         {"status": "locked"}, {"ok": False}, {"status": "BLOCKED"}):
            self.assertTrue(ist_misserfolg(ergebnis), ergebnis)
        for ergebnis in ({"status": "success"}, '{"status": "duplicate"}', "geöffnet", None, 5, "{kaputt"):
            self.assertFalse(ist_misserfolg(ergebnis), ergebnis)


class GescheitertZaehltNichtTest(_ParamsMixin, unittest.IsolatedAsyncioTestCase):
    async def test_abgelehnter_aufruf_darf_wiederholt_werden(self):
        """Freigabe offen oder Fehler: die Aktion hat nicht stattgefunden, ein zweiter Versuch ist kein Doppelaufruf."""
        antworten = [{"status": "needs_approval", "approval_id": "x"}, {"status": "success"}, {"status": "success"}]
        aufrufe = []

        async def host(params):
            aufrufe.append(1)
            await params.result_callback(antworten[len(aufrufe) - 1])

        turn = TurnState()
        turn.new_user_turn("Öffne Amazon")
        doppel = DoppelWache(turn_nr=lambda: turn.turn_nr)
        gated = bot.gate_pc_tools([FunctionSchema("webseite_oeffnen", "x", {}, [], handler=host)], lambda: True,
                                  lambda: None, doppel)[0]
        ergebnisse: list = []
        url = "https://www.amazon.de/s?k=Fahrrad"
        await gated.handler(self.params("webseite_oeffnen", url, ergebnisse))  # wartet auf Freigabe
        await gated.handler(self.params("webseite_oeffnen", url, ergebnisse))  # gelingt
        await gated.handler(self.params("webseite_oeffnen", url, ergebnisse))  # jetzt wirklich doppelt
        self.assertEqual(len(aufrufe), 2)
        self.assertEqual(ergebnisse[2]["status"], "duplicate")
        self.assertNotIn("hinweis", ergebnisse[1])


class AusnahmeTest(_ParamsMixin, unittest.IsolatedAsyncioTestCase):
    async def test_ausnahme_oder_abbruch_im_handler_streicht_den_aufruf(self):
        """Review: ohne Ergebnis darf ein zweiter Versuch im selben Zug nicht als Doppelaufruf gelten."""
        fehler = [RuntimeError("Host weg"), asyncio.CancelledError()]
        aufrufe = []

        async def host(params):
            aufrufe.append(1)
            if fehler:
                raise fehler.pop(0)
            await params.result_callback({"status": "success"})

        doppel = DoppelWache(turn_nr=lambda: 1)
        gated = bot.gate_pc_tools([FunctionSchema("webseite_oeffnen", "x", {}, [], handler=host)], lambda: True,
                                  lambda: None, doppel)[0]
        ergebnisse: list = []
        url = "https://www.amazon.de"
        with self.assertRaises(RuntimeError):
            await gated.handler(self.params("webseite_oeffnen", url, ergebnisse))
        with self.assertRaises(asyncio.CancelledError):
            await gated.handler(self.params("webseite_oeffnen", url, ergebnisse))
        await gated.handler(self.params("webseite_oeffnen", url, ergebnisse))
        self.assertEqual(len(aufrufe), 3)
        self.assertEqual(ergebnisse, [{"status": "success"}])


class RandfaelleTest(unittest.TestCase):
    def test_adresse_ohne_schema_zaehlt_zur_webseite(self):
        self.assertEqual(webseite_von({"url": "amazon.de/s?k=x"}), "amazon.de")
        w, _ = wache()
        for i in range(SEITEN_MAX_JE_ZUG):
            w.pruefen("webseite_oeffnen", {"url": f"amazon.de/s?k=t{i}"})
        self.assertEqual(w.pruefen("webseite_oeffnen", {"url": "amazon.de/s?k=viel"}).aktion, "ueberspringen")

    def test_cancelled_mit_zusatztext_wird_auch_umgeschrieben(self):
        m = verlauf_mit_abbruch("maus_scrollen", ergebnis="CANCELLED: this tool call was cancelled before it returned a result")
        self.assertEqual(abbrueche_umschreiben(m), 1)


class TurnStateTest(unittest.TestCase):
    def test_zaehler_wechselt_mit_jedem_beitrag(self):
        t = TurnState()
        self.assertEqual(t.turn_nr, 0)
        t.new_user_turn("a")
        t.new_user_turn("b")
        self.assertEqual(t.turn_nr, 2)


if __name__ == "__main__":
    unittest.main()
