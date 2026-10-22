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
    async def build(self, zugang, rolle="admin", token_laden=None):
        token = token_laden or mock.AsyncMock(return_value="sk-ant-oat01-TESTtest-0123456789abcdefghijklmnop-Zz99")
        with mock.patch.dict(os.environ, ENV), \
             mock.patch.object(bot, "zugang_laden", mock.AsyncMock(return_value=zugang)), \
             mock.patch.object(bot, "claude_code_token_laden", token), \
             mock.patch.object(bot, "DevAgent", wraps=bot.DevAgent) as dev, \
             mock.patch.object(bot, "Boss", wraps=bot.Boss) as boss, \
             mock.patch.object(bot, "entwicklung_problem", return_value=None), \
             mock.patch("llm_anbieter.litellm_problem", mock.AsyncMock(return_value=None)), \
             mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)):
            session = await bot.build_session(smoke.FakeTransport(), Identity(USER, "Erika", rolle, "du"))
        await session.cleanup()
        return dev, boss, token

    async def test_jede_sitzung_hat_den_boss_und_seine_werkzeuge(self):
        for zugang in (Zugang.haus(USER), Zugang(USER, "claude", "profil", claude_schluessel=KEY)):
            _dev, boss, _ = await self.build(zugang, rolle="mitglied")
            self.assertEqual(boss.call_count, 1)
            self.assertIs(boss.call_args.args[0], zugang)
            self.assertIsNone(boss.call_args.kwargs["bild_quelle"])  # ohne PC-Steuerung keine Bildbeschreibung

    async def test_ohne_token_kein_entwicklungsauftrag_fuer_profile_mit_eigenem_schluessel(self):
        dev, _, _ = await self.build(Zugang(USER, "claude", "profil", claude_schluessel=KEY))
        self.assertFalse(dev.called)
        dev, _, _ = await self.build(Zugang.haus(USER, "keiner"))
        self.assertFalse(dev.called)

    async def test_nur_hausmodelle_gewaehlt_oder_festgelegt_keine_entwicklung_auch_mit_token(self):
        for quelle in ("wahl", "festgelegt"):
            dev, _, _ = await self.build(Zugang.haus(USER, quelle, claude_code=True))
            self.assertFalse(dev.called, quelle)

    async def test_mit_token_laeuft_der_auftrag_mit_dem_token_dieser_sitzung(self):
        for zugang in (Zugang.haus(USER, "keiner", claude_code=True),
                       Zugang(USER, "claude", "profil", claude_schluessel=KEY, claude_code=True)):
            dev, _, token = await self.build(zugang)
            self.assertTrue(dev.called)
            quelle = dev.call_args.kwargs["token_quelle"]
            self.assertIsNotNone(quelle)
            with mock.patch.object(bot, "claude_code_token_laden", token):  # die Quelle schlägt erst beim Lauf nach
                self.assertTrue((await quelle()).startswith("sk-ant-oat01-"))
            token.assert_awaited_once_with(USER)  # gebunden an die Profil-ID dieser Sitzung

    async def test_uebergang_fuer_das_profil_des_kernschluessels(self):
        dev, _, token = await self.build(Zugang(USER, "claude", "zuordnung", claude_schluessel=KEY))
        self.assertTrue(dev.called)
        self.assertIsNone(dev.call_args.kwargs["token_quelle"])  # Zugang der Sandbox wie bisher
        token.assert_not_awaited()

    def test_betrieb_meldung_mit_modus_quelle_und_hinweis(self):
        m = bot.betrieb_message("aus", modell_modus="nur_haus", modell_quelle="wahl",
                                modell_hinweis="Gemma 4 31B: antwortet zu langsam" + "x" * 300)
        self.assertEqual((m["modell_modus"], m["modell_quelle"]), ("nur_haus", "wahl"))
        self.assertEqual(len(m["modell_hinweis"]), 200)
        ohne = bot.betrieb_message("aus")
        self.assertNotIn("modell_modus", ohne)
        self.assertNotIn("modell_hinweis", ohne)
