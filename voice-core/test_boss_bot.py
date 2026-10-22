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
