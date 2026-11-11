"""Der Boss in der Sitzung (bot.py): Werkzeuge im Kontext, Anzeige in der Meldung betrieb, Entwicklungsaufträge je
Zugang (Claude-Code-Token, Übergang, keine) und dass der Token nur für genau diese Sitzung gebunden wird."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import test_smoke_bot as smoke
from auth_gate import Identity
from llm_anbieter import Zugang

bot = smoke.bot
USER = smoke.USER
KEY = "sk-ant-api03-PROFILeins-0123456789abcdefghijkl"
ENV = {**smoke.DUMMY_ENV, "LITELLM_BASE_URL": "http://gateway.test.invalid:4000/v1", "LITELLM_API_KEY": "sk-gateway-test",
       "WAMO_LLM_MODELL": "gemma4-31b", "WAMO_ENTWICKLUNG": "an"}


class BossSitzungTests(unittest.IsolatedAsyncioTestCase):
    async def build(self, zugang, rolle="admin"):
        with mock.patch.dict(os.environ, ENV), \
             mock.patch.object(bot, "zugang_laden", mock.AsyncMock(return_value=zugang)), \
             mock.patch.object(bot, "DevAgent", wraps=bot.DevAgent) as dev, \
             mock.patch.object(bot, "Boss", wraps=bot.Boss) as boss, \
             mock.patch.object(bot, "entwicklung_problem", return_value=None), \
             mock.patch("llm_anbieter.litellm_problem", mock.AsyncMock(return_value=None)), \
             mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)):
            session = await bot.build_session(smoke.FakeTransport(), Identity(USER, "Erika", rolle, "du"))
        await session.cleanup()
        return dev, boss

    async def test_jede_sitzung_hat_den_boss_und_seine_werkzeuge(self):
        for zugang in (Zugang.haus(USER), Zugang(USER, "claude", "profil", claude_schluessel=KEY)):
            _dev, boss = await self.build(zugang, rolle="mitglied")
            self.assertEqual(boss.call_count, 1)
            self.assertIs(boss.call_args.args[0], zugang)
            self.assertIsNone(boss.call_args.kwargs["bild_quelle"])  # ohne PC-Steuerung keine Bildbeschreibung

    async def test_ohne_eigenen_zugang_kein_entwicklungsauftrag(self):
        dev, _ = await self.build(Zugang.haus(USER, "keiner"))
        self.assertFalse(dev.called)

    async def test_eigener_api_schluessel_reicht_fuer_entwicklungsauftraege(self):
        dev, _ = await self.build(Zugang(USER, "claude", "profil", claude_schluessel=KEY))
        self.assertTrue(dev.called)  # Guthaben, Standardmodell Haiku; den Zugang holt der Auftrag erst beim Lauf

    async def test_nur_hausmodelle_gewaehlt_oder_festgelegt_keine_entwicklung_auch_mit_token(self):
        for quelle in ("wahl", "festgelegt"):
            dev, _ = await self.build(Zugang.haus(USER, quelle, claude_code=True))
            self.assertFalse(dev.called, quelle)

    async def test_mit_token_gibt_es_entwicklungsauftraege(self):
        for zugang in (Zugang.haus(USER, "keiner", claude_code=True),
                       Zugang(USER, "claude", "profil", claude_schluessel=KEY, claude_code=True)):
            dev, _ = await self.build(zugang)
            self.assertTrue(dev.called)

    async def test_uebergang_fuer_das_profil_des_kernschluessels(self):
        dev, _ = await self.build(Zugang(USER, "claude", "zuordnung", claude_schluessel=KEY))
        self.assertTrue(dev.called)  # Zugang der Sandbox, solange dev_agent.serverzugang_erlaubt gilt

    def test_betrieb_meldung_mit_modus_quelle_und_hinweis(self):
        m = bot.betrieb_message("aus", modell_modus="nur_haus", modell_quelle="wahl",
                                modell_hinweis="Gemma 4 31B: antwortet zu langsam" + "x" * 300)
        self.assertEqual((m["modell_modus"], m["modell_quelle"]), ("nur_haus", "wahl"))
        self.assertEqual(len(m["modell_hinweis"]), 200)
        ohne = bot.betrieb_message("aus")
        self.assertNotIn("modell_modus", ohne)
        self.assertNotIn("modell_hinweis", ohne)


ANDERES_PROFIL = smoke.ANDERES_PROFIL
BUERO_ENV = {**ENV, "WAMO_BETRIEB": "buero", "WAMO_ENTWICKLUNG": "aus", "WAMO_ENTWICKLUNG_BUERO": "an",
             "WAMO_ENTWICKLUNG_ORT": "sokrates2", "WAMO_ENTWICKLUNG_PROFILE": USER,
             "WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD": "3", "WAMO_ENTWICKLUNG_KOSTEN_TAG_USD": "10"}


class BueroEntwicklungTests(unittest.IsolatedAsyncioTestCase):
    """Büro-Schranken in build_session: Admin, Profilbindung, eigener Zugang, eine Logzeile je Sitzung (entwicklung_betrieb.py).
    Außerhalb des Büros bleibt alles wie bisher (BossSitzungTests)."""

    async def build(self, zugang=None, rolle="admin", benutzer=USER, **extra):
        from loguru import logger

        zugang = zugang or Zugang(benutzer, "claude", "profil", claude_schluessel=KEY)
        meldungen: list[str] = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="INFO")
        try:
            with mock.patch.dict(os.environ, {**BUERO_ENV, **extra}), \
                 mock.patch.object(bot, "zugang_laden", mock.AsyncMock(return_value=zugang)), \
                 mock.patch.object(bot, "DevAgent", wraps=bot.DevAgent) as dev, \
                 mock.patch.object(bot, "entwicklung_problem", return_value=None), \
                 mock.patch("llm_anbieter.litellm_problem", mock.AsyncMock(return_value=None)), \
                 mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)):
                session = await bot.build_session(smoke.FakeTransport(), Identity(benutzer, "Niko", rolle, "du"))
            await session.cleanup()
        finally:
            logger.remove(sink)
        return dev, "\n".join(meldungen)

    async def test_admin_im_profil_mit_eigenem_zugang_bekommt_entwicklung(self):
        for zugang in (Zugang(USER, "claude", "profil", claude_schluessel=KEY),
                       Zugang.haus(USER, "keiner", claude_code=True)):
            dev, log = await self.build(zugang)
            self.assertTrue(dev.called)
            self.assertIn("entwicklung=an", log)
            self.assertNotIn("Entwicklungsaufträge aus", log)
            zulassung = dev.call_args.kwargs["zulassung"]  # entsteht nur hier, aus der geprüften Identität
            self.assertEqual((type(zulassung).__name__, zulassung.user_id), ("BueroZulassung", USER))

    async def test_admin_ausserhalb_des_profils_bekommt_keine_entwicklung(self):
        dev, log = await self.build(benutzer=ANDERES_PROFIL)
        self.assertFalse(dev.called)
        self.assertIn("Entwicklungsaufträge aus: Dieses Profil ist für Entwicklungsaufträge im Büro nicht freigegeben", log)
        dev, _ = await self.build(benutzer=ANDERES_PROFIL, WAMO_ENTWICKLUNG_PROFILE=f"{ANDERES_PROFIL},{USER}")
        self.assertTrue(dev.called)  # weitere Admins stehen einzeln in der Liste

    async def test_mitglied_nie_auch_nicht_im_profil(self):
        dev, log = await self.build(rolle="mitglied")
        self.assertFalse(dev.called)
        self.assertIn("Entwicklungsaufträge aus: Entwicklungsaufträge gibt es nur für Admins", log)

    async def test_uebergang_zaehlt_im_buero_wie_keine(self):
        zugang = Zugang(USER, "claude", "zuordnung", claude_schluessel=KEY)
        dev, log = await self.build(zugang)
        self.assertFalse(dev.called)
        self.assertIn("kein eigener Zugang", log)
        dev, log = await self.build(Zugang.haus(USER, "keiner"))
        self.assertFalse(dev.called)
        self.assertIn("Entwicklungsaufträge aus: kein eigener Zugang", log)

    async def test_schalter_aus_oder_nur_der_alte_schalter_gibt_keine_entwicklung_und_eine_logzeile(self):
        for extra in ({"WAMO_ENTWICKLUNG_BUERO": "aus"}, {"WAMO_ENTWICKLUNG_BUERO": "", "WAMO_ENTWICKLUNG": "an"},
                      {"WAMO_ENTWICKLUNG_BUERO": "vielleicht"}):
            with self.subTest(extra=extra):
                dev, log = await self.build(**extra)
                self.assertFalse(dev.called)
                self.assertEqual(log.count("Entwicklungsaufträge aus:"), 1)  # eine Zeile je Sitzung, auch bei Schalter aus

    async def test_problem_der_einstellung_schaltet_nur_die_entwicklung_ab(self):
        dev, log = await self.build(WAMO_ENTWICKLUNG_PROFILE="kaputt")  # entwicklung_problem ist hier ersetzt: der Grund kommt aus der Zulassung
        self.assertFalse(dev.called)
        self.assertIn("Entwicklungsaufträge aus:", log)

    async def test_ausserhalb_des_buero_keine_zeile_und_keine_bindung(self):
        dev, log = await self.build(WAMO_BETRIEB="", WAMO_ENTWICKLUNG="an", WAMO_ENTWICKLUNG_PROFILE="", benutzer=ANDERES_PROFIL)
        self.assertTrue(dev.called)  # Unterwegs: wie bisher (nur admin und WAMO_ENTWICKLUNG)
        self.assertIsNone(dev.call_args.kwargs["zulassung"])
        self.assertNotIn("entwicklung=an", log)
        self.assertNotIn("Entwicklungsaufträge aus", log)


class KeinModellTests(unittest.IsolatedAsyncioTestCase):
    """Ist kein Gesprächsmodell nutzbar, sagt die Sitzung der Person, warum sie endet (statt stumm zu trennen)."""

    def test_text_nennt_den_grund_nur_dem_admin_und_bleibt_kurz(self):
        fehler = bot.LLMNichtVerfuegbar("Kein Modell nutzbar für gespraech: Gemma 4 31B: " + "x" * 400)
        admin = bot.kein_modell_text(fehler, admin=True)
        self.assertTrue(admin.startswith("Kein Gesprächsmodell erreichbar."))
        self.assertIn("Gemma 4 31B", admin)
        self.assertLess(len(admin), 330)
        person = bot.kein_modell_text(fehler)
        self.assertNotIn("Gemma", person)  # keine Modell- und Einstellungsnamen für Mitglieder
        self.assertTrue(person.startswith("Kein Gesprächsmodell erreichbar."))

    async def test_meldung_vor_dem_trennen(self):
        gesendet, getrennt = [], []

        class Verbindung:
            def send_app_message(self, nachricht):
                gesendet.append(nachricht)

            async def disconnect(self):
                getrennt.append(True)

        from types import SimpleNamespace

        args = SimpleNamespace(body={}, webrtc_connection=Verbindung())
        with mock.patch.object(bot, "identity_from_body", return_value=Identity(USER, "Erika", "mitglied", "du")), \
             mock.patch.object(bot, "create_transport", mock.AsyncMock(return_value=smoke.FakeTransport())), \
             mock.patch.object(bot, "run_bot", mock.AsyncMock(side_effect=bot.LLMNichtVerfuegbar("Kein Modell nutzbar"))), \
             mock.patch.object(bot, "ENDE_MELDUNG_SECS", 0.0):
            await bot.bot(args)
        self.assertEqual(len(gesendet), 1)
        nachricht = gesendet[0]
        self.assertEqual((nachricht["label"], nachricht["type"]), ("rtvi-ai", "server-message"))
        self.assertEqual(nachricht["data"]["type"], "sitzung_beendet")
        self.assertIn("Kein Gesprächsmodell erreichbar", nachricht["data"]["grund"])
        self.assertEqual(getrennt, [True])
