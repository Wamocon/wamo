"""Beweistests der Laborsitzung in bot.build_session (Testlabor 3.1, P1b und P1d): T1 (Werkzeugmatrix), T3 Schicht a, T5, T12, SessionGuard.

Die Sitzung wird wirklich gebaut (ohne Netz, wie in test_smoke_bot.py). Beweis ist nicht nur, dass die Namen stimmen: PC-Host, Gerätezugang,
DevAgent, Boss und Fernzugriff werden in einer Laborsitzung nie berührt, auch wenn Umgebung und Rolle sie hergäben.

Ausführen:  python -m pytest -p no:cacheprovider -q test_labor_sitzung.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import contextlib
import itertools
import os
import re
import unittest
from types import SimpleNamespace
from unittest import mock

import labor
import labor_durchlauf
import labor_testhilfe
import test_smoke_bot as smoke
from auth_gate import Identity
from labor_attrappe import Attrappe
from labor_durchlauf import Durchlauf
from memory import Profile

bot = smoke.bot
USER = smoke.USER
DURCHLAUF = "5b0c7a0e-1111-4222-8333-444455556666"
ENV = {**smoke.DUMMY_ENV, "WAMO_LABOR_EINWILLIGUNG_MIN": "2", "WAMO_LABOR_TEXT_SPEICHERN": "aus"}


class Quelle:
    def __init__(self, offen=True, version=2):
        self.offen, self.version = offen, version

    async def durchlauf(self, platz):
        return {"durchlauf_id": DURCHLAUF, "offen": self.offen, "einwilligung_version": self.version}


def platz_identitaet(art="platz"):
    return Identity(USER, "Gast", "mitglied", "du", labor_art=art, labor_platz=3 if art == "platz" else None, labor_aktiv=True)


class Beobachtet:
    """Alles, was eine gebaute Sitzung nach außen zeigt: Werkzeugnamen im Kontext, am LLM, Prompt, Task und die Berührungen."""

    def __init__(self):
        self.session = None
        self.llm = None
        self.prompt = None
        self.context = None
        self.zugang = None
        self.labor = None
        self.beruehrt: dict[str, mock.Mock] = {}
        self.transport = None
        self.app = None  # der Dienst der Kollegen (Attrappe), den Annahme und Werkzeuge gemeinsam nutzen

    @property
    def kontext_namen(self) -> list[str]:
        return [s.name for s in self.context.tools.standard_tools]

    @property
    def llm_namen(self) -> set:
        return set(self.llm._functions)

    def handler(self, name):
        return self.session.task.rtvi._event_handlers[name].handlers[0]


async def bauen(identity, env=None, quelle=None, *, pc_waere_da=True, freigabe=None):
    """Baut eine Sitzung. pc_waere_da: PC-Host, Gerätezugang und Fernzugriff wären verfügbar, wenn der Weg nicht gesperrt wäre.

    Der Dienst der Kollegen ist die Attrappe (labor_testhilfe.dienst) mit Durchlauf und Einwilligung Version 2, für die Annahme und für die Werkzeuge.
    quelle: nur für die Annahme, ersetzt die Durchlauf-Quelle (Tests der Ablehnung, ohne Attrappe). freigabe: ersetzt nach dem Bau den Entscheider,
    den bot.py für die Freigabeklicks der Laborsitzung einhängt."""
    b = Beobachtet()
    b.app = Attrappe()
    b.app.neuer_durchlauf(3, einwilligung_version=2)
    echt_llm = bot.gespraech_llm

    async def llm_merken(system_instruction, zugang):
        b.prompt, b.zugang = system_instruction, zugang
        b.llm = (await echt_llm(system_instruction, zugang))[0]
        return b.llm, SimpleNamespace(anzeige="Test", anbieter="claude", modell="test", grund=None)

    echt_paar = bot.LLMContextAggregatorPair

    def paar(context, **kw):
        b.context = context
        return echt_paar(context, **kw)

    echt_starten = bot.LaborSitzung.starten

    async def starten(ident, **kw):
        b.labor = await echt_starten(ident, **kw)
        return b.labor

    beruehrt = {n: mock.Mock(side_effect=AssertionError(f"{n} darf in der Laborsitzung nie berührt werden"))
                for n in ("host_client", "DevAgent", "Boss", "PcFreigaben")}
    pc_zugang = mock.AsyncMock(return_value=object())
    pc_host_fuer = mock.Mock(return_value=True)
    b.beruehrt = {**beruehrt, "pc_zugang": pc_zugang, "pc_host_fuer": pc_host_fuer}
    with contextlib.ExitStack() as stack:
        stack.enter_context(labor_testhilfe.dienst(b.app, einwilligung_min=None))
        stack.enter_context(mock.patch.dict(os.environ, {**ENV, **(env or {})}))
        if quelle is not None:
            stack.enter_context(mock.patch.object(labor_durchlauf, "_quelle", quelle))
        with mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)), \
             mock.patch.object(bot, "LaborSitzung", mock.Mock(starten=starten)), \
             mock.patch.object(bot, "gespraech_llm", llm_merken), mock.patch.object(bot, "LLMContextAggregatorPair", paar), \
             mock.patch.object(bot, "pc_zugang", pc_zugang), mock.patch.object(bot, "pc_host_fuer", pc_host_fuer), \
             mock.patch.object(bot, "host_client", beruehrt["host_client"]), mock.patch.object(bot, "DevAgent", beruehrt["DevAgent"]), \
             mock.patch.object(bot, "Boss", beruehrt["Boss"]), mock.patch.object(bot, "PcFreigaben", beruehrt["PcFreigaben"]):
            b.transport = smoke.FakeTransport()
            b.session = await bot.build_session(b.transport, identity)
    if freigabe is not None:
        b.labor.freigabe = freigabe
    return b


UMGEBUNGEN = [
    {},
    {"WAMO_BETRIEB": "buero"},
    {"WAMO_BETRIEB": "buero", "WAMO_ENTWICKLUNG_BUERO": "an", "WAMO_ENTWICKLUNG_PROFILE": USER},
    {"WAMO_ENTWICKLUNG": "an", "WAMO_RD_AKTIV": "ja"},
    {"WAMO_PC_HOST": "an", "WAMO_PC_REGISTER": "an", "WAMO_RD_AKTIV": "ja"},
    {"WAMO_BETRIEB": "buero", "WAMO_PC_REGISTER": "an", "WAMO_ENTWICKLUNG_BUERO": "an", "WAMO_ENTWICKLUNG_PROFILE": USER,
     "WAMO_RD_AKTIV": "ja", "WAMO_PC_HOST": "an", "WAMO_AUSGABE_SCHWELLE": "500"},
]


class WerkzeugMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def test_t1_namen_im_kontext_und_am_llm_sind_gleich_der_erlaubnisliste(self):
        for art, env in itertools.product(("platz", "schaufenster"), UMGEBUNGEN):
            with self.subTest(art=art, env=env):
                # Ticket und Mail (labor_werkzeuge, Strang K2) gibt es nur mit einem Platz: das Schaufenster hat laut Migration keinen Platz,
                # also kein Test-Jira, und bleibt bei den Basiswerkzeugen
                erwartet = set(labor.WERKZEUGE_BASIS) | (set(labor.WERKZEUGE_TICKET) if art == "platz" else set())
                b = await bauen(platz_identitaet(art), env)
                self.assertEqual(set(b.kontext_namen), erwartet)
                self.assertEqual(len(b.kontext_namen), len(set(b.kontext_namen)))
                self.assertEqual(b.llm_namen, erwartet)  # auch der Sammelhandler None wäre ein Fremdkörper
                self.assertNotIn(None, b.llm_namen)
                for gesperrt in ("merken", "fernzugriff_oeffnen", "ausgabe_nachladen", "entwicklung_starten", "aufgabe_delegieren",
                                 "system_info", "bildschirm_ansehen", "geraet_liste", "teilen_anlegen"):
                    self.assertNotIn(gesperrt, b.kontext_namen)
                await b.session.cleanup()

    async def test_t1_nichts_ausser_dem_gespraech_wird_beruehrt(self):
        for art, env in itertools.product(("platz", "schaufenster"), UMGEBUNGEN):
            with self.subTest(art=art, env=env):
                b = await bauen(platz_identitaet(art), env)
                b.beruehrt["pc_zugang"].assert_not_called()
                b.beruehrt["pc_host_fuer"].assert_not_called()
                await b.session.cleanup()
                b.beruehrt["pc_zugang"].assert_not_called()

    async def test_kontrolle_ein_admin_mit_pc_beruehrt_den_pc_weg(self):
        # Ohne diese Kontrolle bewiese der Test oben nichts: derselbe Aufbau mit einem Admin ruft den PC-Weg tatsächlich auf
        b = Beobachtet()
        pc_host_fuer = mock.Mock(return_value=True)

        class Client:
            async def close(self):
                pass

        with mock.patch.dict(os.environ, ENV), mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)), \
             mock.patch.object(bot, "pc_host_fuer", pc_host_fuer), mock.patch.object(bot, "host_client", return_value=Client()), \
             mock.patch.object(bot, "host_tools", mock.AsyncMock(return_value=None)):
            b.session = await bot.build_session(smoke.FakeTransport(), Identity(USER, "Erika", "admin", "du"))
        pc_host_fuer.assert_called()
        await b.session.cleanup()

    async def test_die_sitzung_hat_keinen_pc_und_keine_entwicklung(self):
        b = await bauen(platz_identitaet(), UMGEBUNGEN[5])
        betrieb = bot.betrieb_message("aus")
        self.assertEqual(betrieb["pc_steuerung"], "aus")
        self.assertIs(b.labor.identity.is_admin, False)
        await b.session.cleanup()

    async def test_ausgaben_nachladen_gibt_es_nicht_und_der_kuerzer_ist_aus(self):
        from ausgaben_kuerzen import AusgabenKuerzer

        with mock.patch.object(bot, "Pipeline", wraps=bot.Pipeline) as pipeline:
            b = await bauen(platz_identitaet(), {"WAMO_AUSGABE_SCHWELLE": "500"})
        self.assertFalse(any(isinstance(p, AusgabenKuerzer) for p in pipeline.call_args.args[0]))
        self.assertNotIn("ausgabe_nachladen", b.kontext_namen)
        await b.session.cleanup()

    async def test_t1_ein_fremdes_werkzeug_bricht_die_sitzung_ab(self):
        from pipecat.adapters.schemas.function_schema import FunctionSchema

        echt = bot.register_tools

        def mit_fremdem(llm, memory, identity, turn=None, **kw):
            schema = echt(llm, memory, identity, turn, **kw)
            llm.register_function("merken", mock.AsyncMock())
            schema.standard_tools.append(FunctionSchema(name="merken", description="x", properties={}, required=[]))
            return schema

        with mock.patch.object(bot, "register_tools", mit_fremdem), self.assertRaises(labor.LaborPolitikFehler):
            await bauen(platz_identitaet())

    async def test_t1_ein_nur_am_llm_registrierter_fremder_name_bricht_ab(self):
        echt = bot.register_tools

        def heimlich(llm, memory, identity, turn=None, **kw):
            llm.register_function("fernzugriff_oeffnen", mock.AsyncMock())  # im Kontext steht er nicht, am LLM schon
            return echt(llm, memory, identity, turn, **kw)

        with mock.patch.object(bot, "register_tools", heimlich), self.assertRaises(labor.LaborPolitikFehler):
            await bauen(platz_identitaet())

    async def test_t1_ein_sammelhandler_am_llm_bricht_ab(self):
        echt = bot.register_tools

        def sammler(llm, memory, identity, turn=None, **kw):
            llm.register_function(None, mock.AsyncMock())
            return echt(llm, memory, identity, turn, **kw)

        with mock.patch.object(bot, "register_tools", sammler), self.assertRaises(labor.LaborPolitikFehler):
            await bauen(platz_identitaet())


class EntwicklungGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_t12_kein_entwicklungsagent_im_labor_auch_nicht_fuer_das_schaufenster(self):
        env = {"WAMO_BETRIEB": "buero", "WAMO_ENTWICKLUNG_BUERO": "an", "WAMO_ENTWICKLUNG_PROFILE": USER}
        for art in ("platz", "schaufenster"):
            with self.subTest(art=art):
                b = await bauen(platz_identitaet(art), env)  # DevAgent wirft AssertionError, wenn er gebaut würde
                self.assertNotIn("entwicklung_starten", b.kontext_namen)
                await b.session.cleanup()


class PromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_t5_der_prompt_enthaelt_nichts_internes(self):
        for art in ("platz", "schaufenster"):
            with self.subTest(art=art):
                b = await bauen(platz_identitaet(art), UMGEBUNGEN[5])
                prompt = b.prompt
                self.assertIn("WAMO", prompt)
                for wort in ("Sokrates", "Spark", "Supabase", "Caesar", "LiteLLM", "Fernzugriff", "Modellzugang", "Entwicklungsauf",
                             "Claude Code", "Claude-Schlüssel", "system_info", "browser_lesen", "PC-Werkzeug", "Gedächtnis",
                             "bereich=", "merken", "ausgabe_nachladen", "Dispatcher", "Repo"):
                    self.assertNotIn(wort, prompt, wort)
                self.assertIsNone(re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", prompt))
                self.assertIsNone(re.search(r"https?://", prompt))
                await b.session.cleanup()

    async def test_t5_der_prompt_kommt_nie_aus_dem_normalen_zweig(self):
        b = await bauen(platz_identitaet(), UMGEBUNGEN[5])
        for normal in ("Standard: kein Entwicklungsauftrag", "Schlüssel und Modellzugang"):
            self.assertNotIn(normal, b.prompt)
        await b.session.cleanup()


class NachrichtenSchichtATests(unittest.IsolatedAsyncioTestCase):
    async def test_nur_die_labor_freigabe_wirkt(self):
        freigabe = mock.AsyncMock()
        b = await bauen(platz_identitaet(), freigabe=freigabe)
        handler = b.handler("on_client_message")
        wirkungen = {
            "modellzugang_nachricht": mock.AsyncMock(),
            "gespeichert_nachricht": mock.AsyncMock(),
            "forget": mock.AsyncMock(return_value=True),
        }
        with mock.patch.object(bot, "modellzugang_nachricht", wirkungen["modellzugang_nachricht"]), \
             mock.patch.object(bot.kontingent, "gespeichert_nachricht", wirkungen["gespeichert_nachricht"]), \
             mock.patch.object(b.labor.memory, "forget", wirkungen["forget"]):
            for typ, data in (("vergessen", {"id": "x"}), ("pc_steuerung", {"an": True}), ("bildstrom", {"aktion": "an"}),
                              ("modellzugang", {"aktion": "status"}), ("kontingent", {}), ("auftrag_start", {"job": "j"}),
                              ("freigabe", {"job": "pc", "id": "a"}), ("freigabe", {"job": "abc", "id": "a"}),
                              ("freigabe", {}), ("freigabe", None), ("unbekannt", {"x": 1})):
                with self.subTest(typ=typ, data=data):
                    await handler(None, SimpleNamespace(type=typ, data=data))
            freigabe.assert_not_called()
            for name, w in wirkungen.items():
                w.assert_not_called()
            await handler(None, SimpleNamespace(type="freigabe", data={"job": "labor", "id": "a", "entscheidung": "ja"}))
            freigabe.assert_awaited_once_with({"job": "labor", "id": "a", "entscheidung": "ja"})
        await b.session.cleanup()

    async def test_labor_freigabe_ohne_entscheider_ist_wirkungslos_und_wirft_nicht(self):
        # Das Schaufenster hat keinen Platz, also keine Labor-Freigabe (labor_werkzeuge liefert None): der Klick findet keinen Entscheider
        b = await bauen(platz_identitaet("schaufenster"))
        self.assertIsNone(b.labor.freigabe)
        await b.handler("on_client_message")(None, SimpleNamespace(type="freigabe", data={"job": "labor", "id": "a"}))
        await b.session.cleanup()

    async def test_beim_platzprofil_haengt_bot_die_labor_freigabe_als_entscheider_ein(self):
        b = await bauen(platz_identitaet())
        self.assertIsNotNone(b.labor.freigabe)
        self.assertEqual(b.labor.freigabe.__self__.platz, 3)  # die Freigabe der Werkzeuge dieses Platzes
        await b.session.cleanup()


class AnnahmeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ohne_offenen_durchlauf_keine_sitzung(self):
        for quelle, code in ((Quelle(offen=False), labor_durchlauf.CODE_BEENDET), (Quelle(version=1), labor_durchlauf.CODE_EINWILLIGUNG),
                             (Quelle(version=None), labor_durchlauf.CODE_EINWILLIGUNG)):
            with self.subTest(code=code):
                with self.assertRaises(labor.LaborAbgelehnt) as ctx:
                    await bauen(platz_identitaet(), quelle=quelle)
                self.assertEqual(ctx.exception.code, code)

    async def test_ohne_dienst_keine_sitzung(self):
        class Weg:
            async def durchlauf(self, platz):
                raise OSError("weg")

        with self.assertRaises(labor.LaborAbgelehnt) as ctx:
            await bauen(platz_identitaet(), quelle=Weg())
        self.assertEqual((ctx.exception.status, ctx.exception.code), (503, labor_durchlauf.CODE_DIENST))

    async def test_bot_meldet_die_ablehnung_statt_still_zu_haengen(self):
        gesendet, getrennt = [], []

        class Verbindung:
            def send_app_message(self, nachricht):
                gesendet.append(nachricht)

            async def disconnect(self):
                getrennt.append(True)

        args = mock.Mock(body={"wamo": platz_identitaet().to_body() | {"anmeldung": 1760000000.0}}, webrtc_connection=Verbindung())
        with mock.patch.object(bot, "create_transport", mock.AsyncMock(return_value=smoke.FakeTransport())), \
             mock.patch.object(bot, "run_bot", mock.AsyncMock(side_effect=labor.LaborAbgelehnt("Der Durchlauf ist beendet.", 403, "x"))):
            await bot.bot(args)
        self.assertEqual(getrennt, [True])
        self.assertEqual(gesendet[0]["data"]["type"], "sitzung_beendet")
        self.assertEqual(gesendet[0]["data"]["grund"], "Der Durchlauf ist beendet.")


class SessionGuardLaborTests(unittest.IsolatedAsyncioTestCase):
    class Lookup:
        def __init__(self, profil):
            self.profil = profil

        async def get(self, user_id):
            return self.profil

    def profil(self, **kw):
        werte = dict(user_id=USER, name="Gast", rolle="mitglied", aktiv=False, anrede="du", labor_art="platz", labor_platz=3,
                     labor_aktiv=True)
        werte.update(kw)
        return Profile(**werte)

    async def pruefen(self, profil, identity=None):
        guard = bot.SessionGuard(identity or platz_identitaet(), self.Lookup(profil), wanduhr=lambda: 1.0)
        return await guard.check()

    async def test_unveraendert_laeuft_weiter(self):
        self.assertIsNone(await self.pruefen(self.profil()))

    async def test_labor_aktiv_weg_beendet(self):
        self.assertIn("nicht mehr", await self.pruefen(self.profil(labor_aktiv=False)))

    async def test_art_platz_rolle_und_aktiv_aenderungen_beenden(self):
        for kw in ({"labor_art": "schaufenster", "labor_platz": None}, {"labor_art": None, "labor_platz": None, "labor_aktiv": False},
                   {"labor_platz": 4}, {"rolle": "admin"}, {"aktiv": True}, {"labor_art": "irgendwas"} | {"labor_platz": 3}):
            with self.subTest(kw=kw):
                grund = await self.pruefen(self.profil(**kw))
                if kw == {"labor_art": "irgendwas", "labor_platz": 3}:
                    self.assertIsNone(grund)  # unbekannte Art heißt platz: dieselbe Art wie die der Sitzung
                else:
                    self.assertTrue(grund, kw)

    async def test_gewoehnliche_sitzung_wird_laborprofil_beendet(self):
        identity = Identity(USER, "Erika", "mitglied", "du")
        profil = Profile(user_id=USER, name="Erika", rolle="mitglied", aktiv=True, anrede="du", labor_art="platz", labor_platz=1)
        self.assertTrue(await self.pruefen(profil, identity))

    async def test_gewoehnliche_sitzung_unveraendert(self):
        identity = Identity(USER, "Erika", "admin", "du")
        self.assertIsNone(await self.pruefen(Profile(user_id=USER, name="Erika", rolle="admin", aktiv=True, anrede="du"), identity))
        self.assertTrue(await self.pruefen(Profile(user_id=USER, name="Erika", rolle="admin", aktiv=False, anrede="du"), identity))


class LaborSitzungTests(unittest.IsolatedAsyncioTestCase):
    async def test_starten_liefert_speicher_wache_und_stand(self):
        from labor_sitzung import LaborSitzung

        with mock.patch.dict(os.environ, {**ENV, "WAMO_LABOR_TEXT_SPEICHERN": "an"}):
            s = await LaborSitzung.starten(platz_identitaet(), quelle=Quelle(), db=None)
        self.assertEqual(s.stand, Durchlauf(DURCHLAUF, True, 2))
        self.assertEqual(s.wache.durchlauf_id, DURCHLAUF)
        self.assertEqual(s.wache.platz, 3)
        self.assertFalse(s.memory.enabled)  # ohne Datenbank nichts

    async def test_schaufenster_hat_keine_wache(self):
        from labor_sitzung import LaborSitzung

        s = await LaborSitzung.starten(platz_identitaet("schaufenster"), quelle=Quelle(offen=False))
        self.assertIsNone(s.wache)
        self.assertIsNone(s.stand)
        self.assertFalse(s.memory.enabled)

    async def test_starten_ohne_laborprofil_ist_ein_fehler(self):
        from labor_sitzung import LaborSitzung

        with self.assertRaises(labor.LaborPolitikFehler):
            await LaborSitzung.starten(Identity(USER, "Erika", "admin", "du"))

    async def test_lernen_gibt_es_nicht(self):
        b = await bauen(platz_identitaet())
        with mock.patch.object(bot, "learn_from_session", mock.AsyncMock(side_effect=AssertionError("Lernen im Labor"))):
            await b.session.cleanup()  # cleanup startet sonst das Lernen des Rests


if __name__ == "__main__":
    unittest.main()


async def normal_bauen():
    """Kontrolle: eine gewöhnliche Sitzung ohne Labor."""
    with mock.patch.dict(os.environ, ENV), mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)):
        return await bot.build_session(smoke.FakeTransport(), Identity(USER, "Erika", "mitglied", "du"))


class RtviUndWacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_der_rtvi_verarbeiter_der_laborsitzung_ist_der_filter(self):
        from labor_rtvi import LaborRTVIProcessor

        b = await bauen(platz_identitaet())
        self.assertIsInstance(b.session.task.rtvi, LaborRTVIProcessor)
        await b.session.cleanup()
        normal = await normal_bauen()
        self.assertNotIsInstance(normal.task.rtvi, LaborRTVIProcessor)
        await normal.cleanup()

    async def test_t14_das_ende_des_durchlaufs_beendet_die_sitzung_und_meldet_es(self):
        class Beendet:
            async def durchlauf(self, platz):
                return {"durchlauf_id": DURCHLAUF, "offen": False, "einwilligung_version": 2}

        b = await bauen(platz_identitaet())
        b.labor.wache = labor_durchlauf.LaborWache(3, b.labor.stand, quelle=Beendet(), min_version=2, interval=0.01)
        frames = []

        async def merken(liste, *a, **k):
            frames.extend(liste)

        b.session.task.queue_frames = merken
        with mock.patch.object(bot, "vorwaermen", mock.AsyncMock()), mock.patch.object(bot, "ENDE_MELDUNG_SECS", 0.01):
            await b.transport._event_handlers["on_client_connected"].handlers[0](b.transport, None)
            for _ in range(200):
                await __import__("asyncio").sleep(0.02)
                if any(getattr(f, "data", {}).get("type") == "sitzung_beendet" for f in frames):
                    break
        daten = [getattr(f, "data", None) for f in frames if getattr(f, "data", None)]
        typen = [d["type"] for d in daten]
        self.assertIn("labor_durchlauf_ende", typen)
        self.assertIn("sitzung_beendet", typen)
        self.assertLess(typen.index("labor_durchlauf_ende"), typen.index("sitzung_beendet"))
        await b.session.cleanup()

    async def test_gewoehnliche_sitzung_hat_keine_wache(self):
        # Kontrolle: das Labor hängt nichts an eine normale Sitzung
        normal = await normal_bauen()
        await normal.cleanup()


class ZugangParameterTests(unittest.TestCase):
    def test_kein_labor_kein_zusatzargument(self):
        self.assertEqual(labor.zugang_parameter(Identity(USER, "Erika", "admin", "du")), {})

    def test_labor_art_nur_aus_der_identitaet_und_nur_wenn_zugang_laden_es_kennt(self):
        import modellzugang

        ident = platz_identitaet("schaufenster")

        async def alt(user_id):  # Stand ohne Quelle labor
            return None

        async def neu(user_id, labor_art=None):
            return None

        with mock.patch.object(modellzugang, "zugang_laden", alt):
            self.assertEqual(labor.zugang_parameter(ident), {})
        with mock.patch.object(modellzugang, "zugang_laden", neu):
            self.assertEqual(labor.zugang_parameter(ident), {"labor_art": "schaufenster"})


class DatenInDerSitzungTests(unittest.IsolatedAsyncioTestCase):
    async def test_regeln_und_daten_kommen_in_den_prompt_der_sitzung_und_die_pruefsumme_in_den_log(self):
        from labor_daten import LaborDaten
        from labor_sitzung import LaborSitzung
        from loguru import logger

        zeilen = []
        kennung = logger.add(lambda m: zeilen.append(str(m)), level="INFO", format="{message}")
        try:
            daten = LaborDaten("Nichts erfinden.", (("kontakte.md", "Anna GEHEIMTEXT"),), {"kontakte.md": "abcd1234abcd1234"}, {"ausgang.md": "fehlt"})
            s = await LaborSitzung.starten(platz_identitaet(), quelle=Quelle(), min_version=2, daten=daten)
        finally:
            logger.remove(kennung)
        prompt = s.prompt()
        self.assertIn("Nichts erfinden.", prompt)
        self.assertIn("Anna GEHEIMTEXT", prompt)
        start = [z for z in zeilen if "Labor: Sitzung" in z]
        self.assertEqual(len(start), 1)
        self.assertIn("kontakte.md=abcd1234abcd1234", start[0])
        self.assertIn("ausgang.md(fehlt)", start[0])
        self.assertNotIn("GEHEIMTEXT", start[0])

    async def test_ohne_lesbare_daten_ist_der_prompt_der_reine_laborprompt(self):
        from labor_prompt import build_labor_prompt
        from labor_sitzung import LaborSitzung

        with mock.patch.dict(os.environ, {"WAMO_LABOR_DATEN": "/etc/wamo"}):  # verbotenes Verzeichnis: nichts wird gelesen
            s = await LaborSitzung.starten(platz_identitaet(), quelle=Quelle(), min_version=2)
        self.assertEqual(s.prompt(), build_labor_prompt("Gast", "du", "platz"))
        self.assertIn("Testlabor", s.prompt())
        self.assertNotIn("Gedächtnis", s.prompt())


class StartPruefungTests(unittest.IsolatedAsyncioTestCase):
    async def test_unstimmige_oder_nicht_freigeschaltete_identitaet_startet_nicht(self):
        from labor_sitzung import LaborSitzung

        faelle = {
            "nicht freigeschaltet": Identity(USER, "G", "mitglied", "du", labor_art="platz", labor_platz=3, labor_aktiv=False),
            "admin": Identity(USER, "G", "admin", "du", labor_art="platz", labor_platz=3, labor_aktiv=True),
            "platz fehlt": Identity(USER, "G", "mitglied", "du", labor_art="platz", labor_platz=None, labor_aktiv=True),
            "platz 17": Identity(USER, "G", "mitglied", "du", labor_art="platz", labor_platz=17, labor_aktiv=True),
            "schaufenster mit platz": Identity(USER, "G", "mitglied", "du", labor_art="schaufenster", labor_platz=3, labor_aktiv=True),
        }
        for name, identity in faelle.items():
            with self.subTest(name), self.assertRaises(labor.LaborAbgelehnt) as ctx:
                await LaborSitzung.starten(identity, quelle=Quelle(), min_version=2)
            self.assertEqual(ctx.exception.code, labor.CODE_PROFIL)
