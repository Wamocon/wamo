"""Rauchtest: bot.py mit Dummy-Schlüsseln importieren und die Sitzung bauen, ohne Netz.

build_session erzeugt alle Dienste, die Pipeline und den PipelineTask. Es baut keine Verbindung
auf: Soniox und Anthropic verbinden sich erst beim Start der Pipeline, die Datenbank ist hier
abgeschaltet (WAMO_DATABASE_URL leer) und der PC-Host aus (WAMO_PC_HOST=off).

Ausführen:  uv run python -m pytest -q test_smoke_bot.py   (im Ordner voice-core/)
"""

import os
import unittest
from unittest import mock

DUMMY_ENV = {
    "SONIOX_API_KEY": "dummy-soniox",
    "ANTHROPIC_API_KEY": "dummy-anthropic",
    "WAMO_DATABASE_URL": "",
    "WAMO_DB_SCHEMA": "wamo_test",
    "WAMO_PC_HOST": "off",
    # Der Schlüssel des Kerns gilt nur für dieses Profil (Modellzugang, Übergang über die Zuordnung)
    "WAMO_CLAUDE_SCHLUESSEL_PROFIL": "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c",
}

with mock.patch.dict(os.environ, DUMMY_ENV):
    import bot  # noqa: E402  (Import mit Dummy-Umgebung ist Teil des Tests)

from pipecat.frames.frames import Frame  # noqa: E402
from pipecat.pipeline.task import PipelineTask  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.transports.base_transport import BaseTransport  # noqa: E402

from auth_gate import Identity  # noqa: E402

USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
ANDERES_PROFIL = "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"
_patches: list = []


def setUpModule():
    # Ohne Netz: die kurze Prüfung, ob Claude mit dem Schlüssel des Profils erreichbar ist, entfällt
    p = mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None))
    p.start()
    _patches.append(p)


def tearDownModule():
    while _patches:
        _patches.pop().stop()


class _Passthrough(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class FakeTransport(BaseTransport):
    def __init__(self):
        super().__init__(name="fake")
        self._in, self._out = _Passthrough(), _Passthrough()
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    def input(self):
        return self._in

    def output(self):
        return self._out


class SmokeTests(unittest.IsolatedAsyncioTestCase):
    async def build(self, rolle):
        with mock.patch.dict(os.environ, DUMMY_ENV):
            return await bot.build_session(FakeTransport(), Identity(USER, "Erika Muster", rolle, "sie"))

    async def test_member_session_builds(self):
        session = await self.build("mitglied")
        self.assertIsInstance(session.task, PipelineTask)
        await session.cleanup()
        await session.cleanup()  # idempotent

    async def test_admin_session_builds(self):
        session = await self.build("admin")
        self.assertIsInstance(session.task, PipelineTask)
        await session.cleanup()

    async def test_session_ohne_eigenen_schluessel_nur_hausmodelle(self):
        """Ein Profil ohne eigenen Schlüssel bekommt das Hausmodell, nie den Schlüssel des Kerns."""
        import llm_anbieter

        env = {**DUMMY_ENV, "LITELLM_BASE_URL": "http://gateway.test.invalid:4000/v1",
               "LITELLM_API_KEY": "sk-gateway-test", "WAMO_LLM_MODELL": "gemma4-31b"}
        gebaut = []

        async def merken(system_instruction, zugang):
            gebaut.append((zugang, await llm_anbieter.gespraech_llm(system_instruction, zugang)))
            return gebaut[-1][1]

        with mock.patch.dict(os.environ, env), mock.patch.object(bot, "gespraech_llm", merken),              mock.patch("llm_anbieter.litellm_problem", mock.AsyncMock(return_value=None)),              mock.patch.object(llm_anbieter.Zugang, "anthropic", side_effect=AssertionError("Anthropic gesperrt")):
            session = await bot.build_session(FakeTransport(), Identity(ANDERES_PROFIL, "Max", "mitglied", "du"))
        self.assertIsInstance(session.task, PipelineTask)
        await session.cleanup()
        zugang, (llm, wahl) = gebaut[0]
        self.assertEqual((zugang.art, zugang.quelle), ("haus", "keiner"))
        self.assertIsInstance(llm, llm_anbieter.LiteLLMService)
        self.assertEqual((wahl.anbieter, wahl.modell), ("litellm", "gemma4-31b"))

    async def test_session_ohne_gateway_und_ohne_schluessel_bricht_ab(self):
        """Ohne eigenen Schlüssel und ohne Hausmodell gibt es keine Sitzung (kein stiller Rückfall auf Claude)."""
        import llm_anbieter

        with mock.patch.dict(os.environ, DUMMY_ENV), self.assertRaises(llm_anbieter.LLMNichtVerfuegbar):
            await bot.build_session(FakeTransport(), Identity(ANDERES_PROFIL, "Max", "mitglied", "du"))

    async def test_bot_without_identity_ends_immediately(self):
        class Conn:
            disconnected = False

            async def disconnect(self):
                Conn.disconnected = True

        args = mock.Mock(body={"wamo": {"user_id": "gefaelscht"}}, webrtc_connection=Conn())
        with mock.patch.object(bot, "create_transport") as create:
            await bot.bot(args)
        create.assert_not_called()
        self.assertTrue(Conn.disconnected)

    def test_messages(self):
        ident = Identity(USER, "Erika", "admin", "du")
        self.assertEqual(bot.profil_message(ident), {"type": "profil", "name": "Erika", "rolle": "admin"})
        msg = bot.betrieb_message("aus")
        self.assertEqual(msg["type"], "betrieb")
        self.assertEqual(msg["modus"], "lokal")
        self.assertEqual(msg["ort"], "cloud")
        self.assertEqual(msg["modelle"]["stt"], "Soniox")
        self.assertTrue(msg["modelle"]["tts"].startswith("Soniox "))
        self.assertEqual(msg["pc"], "aus")
        self.assertNotIn("pc_latenz_ms", msg)
        self.assertEqual(bot.betrieb_message("verbunden", 42)["pc_latenz_ms"], 42)

    def test_gemerkt_message_with_id(self):
        from tools import gemerkt_message
        self.assertEqual(gemerkt_message("persoenlich", "aktiv", "x", "abc"),
                         {"type": "gemerkt", "bereich": "persoenlich", "status": "aktiv", "text": "x", "id": "abc"})
        self.assertNotIn("id", gemerkt_message("gemeinsam", "vorgeschlagen", "x"))

    def test_readable_model_name(self):
        self.assertEqual(bot.lesbarer_modellname("claude-haiku-5-5"), "Claude Haiku 5.5")
        self.assertEqual(bot.lesbarer_modellname("claude-sonnet-4-5-20250929"), "Claude Sonnet 4.5")
        self.assertEqual(bot.lesbarer_modellname("claude-opus-5"), "Claude Opus 5")
        self.assertEqual(bot.lesbarer_modellname("gpt-x"), "gpt-x")

    def test_greeting(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WAMO_GREETING", None)
            self.assertEqual(bot.greeting_text("Erika"), "Hallo Erika, WAMO ist bereit.")
        with mock.patch.dict(os.environ, {"WAMO_GREETING": ""}):
            self.assertEqual(bot.greeting_text("Erika"), "")
        with mock.patch.dict(os.environ, {"WAMO_GREETING": "Moin {name}!"}):
            self.assertEqual(bot.greeting_text("Erika"), "Moin Erika!")


class SwitchTests(unittest.IsolatedAsyncioTestCase):
    async def build(self, rolle, entwicklung):
        env = {**DUMMY_ENV, "WAMO_ENTWICKLUNG": entwicklung}
        # Nur die Schalterlogik: ob SSH-Schlüssel oder Hostschlüssel auf diesem Rechner liegen, spielt hier keine Rolle
        with mock.patch.dict(os.environ, env), mock.patch.object(bot, "DevAgent", wraps=bot.DevAgent) as dev, \
             mock.patch.object(bot, "entwicklung_problem", return_value=None):
            session = await bot.build_session(FakeTransport(), Identity(USER, "Erika", rolle, "du"))
        await session.cleanup()
        return dev

    async def test_entwicklung_default_off_even_for_admin(self):
        self.assertFalse((await self.build("admin", "")).called)
        self.assertFalse((await self.build("admin", "aus")).called)
        self.assertFalse((await self.build("mitglied", "an")).called)
        self.assertTrue((await self.build("admin", "an")).called)


class EntwicklungGrundTests(unittest.TestCase):
    """Statusnachricht betrieb: das optionale Feld entwicklung_grund (nur Admins, höchstens 120 Zeichen)."""

    def test_feld_nur_mit_grund_und_gekuerzt(self):
        self.assertNotIn("entwicklung_grund", bot.betrieb_message("aus"))
        self.assertNotIn("entwicklung_grund", bot.betrieb_message("aus", entwicklung_grund=None))
        self.assertNotIn("entwicklung_grund", bot.betrieb_message("aus", entwicklung_grund=""))
        msg = bot.betrieb_message("aus", entwicklung_grund="Der Schalter ist aus")
        self.assertEqual(msg["entwicklung_grund"], "Der Schalter ist aus")
        self.assertEqual(msg["entwicklung"], "aus")
        self.assertEqual(len(bot.betrieb_message("aus", entwicklung_grund="x" * 500)["entwicklung_grund"]), 120)

    def test_mitglieder_bekommen_den_grund_nie(self):
        from entwicklung_betrieb import anzeige_grund

        self.assertIsNone(anzeige_grund(False, "Der Schalter ist aus"))
        self.assertEqual(anzeige_grund(True, "Der Schalter ist aus"), "Der Schalter ist aus")


class PcFreigabeWiringTests(unittest.IsolatedAsyncioTestCase):
    """Freigabeweg für PC-Aktionen: nur Sitzungen mit PC-Werkzeugen (admin) bekommen ihn."""

    async def build(self, rolle, pc):
        class Client:
            async def close(self):
                pass

        gebaut = []
        echt = bot.PcFreigaben

        def merken(*args, **kwargs):
            gebaut.append(echt(*args, **kwargs))
            return gebaut[-1]

        with mock.patch.dict(os.environ, DUMMY_ENV), mock.patch.object(bot, "pc_host_fuer", return_value=pc), \
             mock.patch.object(bot, "host_client", return_value=Client()), \
             mock.patch.object(bot, "host_tools", mock.AsyncMock(return_value=None)), \
             mock.patch.object(bot, "PcFreigaben", side_effect=merken):
            session = await bot.build_session(FakeTransport(), Identity(USER, "Erika", rolle, "du"))
        await session.cleanup()
        return gebaut

    async def test_admin_mit_pc_hat_den_freigabeweg(self):
        self.assertEqual(len(await self.build("admin", True)), 1)

    async def test_ohne_pc_werkzeuge_kein_freigabeweg(self):
        self.assertEqual(await self.build("mitglied", False), [])


class PcGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_gate_refuses_when_off_and_marks_foreign_content(self):
        from pipecat.adapters.schemas.function_schema import FunctionSchema
        calls, foreign = [], []

        async def original(params):
            calls.append(params)
            await params.result_callback({"status": "success"})

        schema = FunctionSchema("fenster_auflisten", "x", {}, [], handler=original)
        state = {"an": False}
        gated = bot.gate_pc_tools([schema], lambda: state["an"], lambda: foreign.append(1))[0]
        self.assertEqual(gated.name, "fenster_auflisten")
        results = []
        params = mock.Mock()
        params.result_callback = mock.AsyncMock(side_effect=lambda r: results.append(r))
        await gated.handler(params)
        self.assertEqual(results[-1]["status"], "rejected")
        self.assertIn("PC-Steuerung ist aus", results[-1]["summary"])
        self.assertEqual(calls, [])
        state["an"] = True
        await gated.handler(params)
        self.assertEqual(len(calls), 1)
        self.assertEqual(foreign, [1])

    def test_betrieb_reports_switch(self):
        self.assertEqual(bot.betrieb_message("verbunden")["pc_steuerung"], "aus")
        self.assertEqual(bot.betrieb_message("verbunden", pc_steuerung=True)["pc_steuerung"], "an")


class SessionGuardTests(unittest.IsolatedAsyncioTestCase):
    """M5: Neuprüfung des Profils mit Fake-Uhr."""

    class Lookup:
        def __init__(self, profile=None, error=None):
            self.profile, self.error, self.calls = profile, error, 0

        async def get(self, user_id):
            self.calls += 1
            if self.error:
                raise self.error
            return self.profile

    def profile(self, aktiv=True, rolle="admin"):
        from memory import Profile
        return Profile(user_id=USER, name="Erika", rolle=rolle, aktiv=aktiv, anrede="du")

    def guard(self, lookup, now):
        return bot.SessionGuard(Identity(USER, "Erika", "admin", "du"), lookup, interval=60, max_secs=120 * 60,
                                clock=lambda: now[0], sleep=self.fake_sleep(now))

    def fake_sleep(self, now):
        async def sleep(secs):
            now[0] += secs
        return sleep

    async def test_active_profile_keeps_session(self):
        now = [0.0]
        self.assertIsNone(await self.guard(self.Lookup(self.profile()), now).check())

    async def test_deactivated_or_role_change_ends(self):
        now = [0.0]
        self.assertIn("nicht mehr aktiv", await self.guard(self.Lookup(self.profile(aktiv=False)), now).check())
        self.assertIn("nicht mehr aktiv", await self.guard(self.Lookup(None), now).check())
        self.assertIn("Rolle", await self.guard(self.Lookup(self.profile(rolle="mitglied")), now).check())

    async def test_max_duration(self):
        now = [0.0]
        g = self.guard(self.Lookup(self.profile()), now)
        now[0] = 120 * 60 - 1
        self.assertIsNone(await g.check())
        now[0] = 120 * 60
        self.assertIn("Sitzungsdauer", await g.check())

    async def test_login_age_ends_session_and_asks_for_new_login(self):
        now = [0.0]
        wand = [1_760_000_000.0]
        ident = Identity(USER, "Erika", "admin", "du", anmeldung=wand[0] - 167 * 3600)
        g = bot.SessionGuard(ident, self.Lookup(self.profile()), interval=60, max_secs=120 * 60,
                             max_anmeldung_secs=168 * 3600, clock=lambda: now[0], wanduhr=lambda: wand[0])
        self.assertIsNone(await g.check())
        self.assertFalse(g.neu_anmelden)
        wand[0] += 3601
        reason = await g.check()
        self.assertIn("168 Stunden", reason)
        self.assertTrue(g.neu_anmelden)

    async def test_db_errors_tolerated_three_times(self):
        now = [0.0]
        g = self.guard(self.Lookup(error=RuntimeError("weg")), now)
        self.assertIsNone(await g.check())
        self.assertIsNone(await g.check())
        self.assertIn("nicht mehr prüfen", await g.check())

    async def test_run_rechecks_every_minute_and_ends(self):
        now = [0.0]
        lookup = self.Lookup(self.profile())
        g = self.guard(lookup, now)
        ended = []

        async def on_end(reason):
            ended.append((now[0], reason))

        original_get = lookup.get

        async def get(user_id):
            if now[0] >= 180:
                lookup.profile = self.profile(aktiv=False)
            return await original_get(user_id)

        lookup.get = get
        await g.run(on_end)
        self.assertEqual(ended[0][0], 180)  # nach drei Prüfungen im Minutentakt
        self.assertEqual(lookup.calls, 3)


class MerkenRuleTests(unittest.TestCase):
    """M4: gemeinsam sofort aktiv nur bei Admin, ausdrücklichem Auslöser und ohne Fremdinhalte."""

    def test_trigger(self):
        from tools import has_shared_trigger
        for text in ["Merk dir für alle: Kunde X zahlt per Rechnung", "Das gilt fürs Team", "gemeinsam merken bitte",
                     "für das ganze Team: Daily um neun", "firmenweit gilt Arial Narrow"]:
            with self.subTest(text=text):
                self.assertTrue(has_shared_trigger(text))
        for text in ["Merk dir, dass ich Tee mag", "alle Dateien löschen", ""]:
            with self.subTest(text=text):
                self.assertFalse(has_shared_trigger(text))

    def test_status(self):
        from tools import merken_status
        trigger = "Merk dir für alle: Projekt Alpha nutzt Supabase"
        self.assertEqual(merken_status("gemeinsam", "admin", trigger, False), "aktiv")
        self.assertEqual(merken_status("gemeinsam", "admin", "Merk dir das", False), "vorgeschlagen")
        self.assertEqual(merken_status("gemeinsam", "admin", trigger, True), "vorgeschlagen")  # Websuche im Zug
        self.assertEqual(merken_status("gemeinsam", "mitglied", trigger, False), "vorgeschlagen")
        self.assertEqual(merken_status("persoenlich", "mitglied", "", True), "aktiv")

    def test_turn_state_marks_sections(self):
        from tools import TurnState
        transcript = [("user", "a"), ("assistant", "b")]
        turn = TurnState(transcript_len=lambda: len(transcript))
        turn.new_user_turn("Merk dir für alle")
        self.assertFalse(turn.tainted)
        turn.taint()
        self.assertTrue(turn.tainted)
        self.assertTrue(turn.section_tainted(0, 2))
        self.assertFalse(turn.section_tainted(3, 6))
        turn.new_user_turn("neuer Satz")
        self.assertFalse(turn.tainted)


class LoggingTests(unittest.TestCase):
    def test_log_level_default_info(self):
        from loguru import logger
        with mock.patch.dict(os.environ, {"WAMO_LOG_LEVEL": ""}):
            bot._configure_logging()
        handler = next(iter(logger._core.handlers.values()))
        self.assertEqual(handler.levelno, 20)  # INFO
        with mock.patch.dict(os.environ, {"WAMO_LOG_LEVEL": "quatsch"}):
            bot._configure_logging()
        self.assertEqual(next(iter(logger._core.handlers.values())).levelno, 20)


if __name__ == "__main__":
    unittest.main()
