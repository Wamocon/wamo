"""bot.py und die Anbieter-Fabrik: Modellanzeige und Sitzungsende ohne Gesprächsmodell (ohne Netz)."""

import os
import unittest
from unittest import mock

DUMMY_ENV = {
    "SONIOX_API_KEY": "dummy-soniox",
    "ANTHROPIC_API_KEY": "dummy-anthropic",
    "WAMO_DATABASE_URL": "",
    "WAMO_DB_SCHEMA": "wamo_test",
    "WAMO_PC_HOST": "off",
}

with mock.patch.dict(os.environ, DUMMY_ENV):
    import bot  # noqa: E402  (Import mit Dummy-Umgebung wie in test_smoke_bot.py)

from auth_gate import Identity  # noqa: E402
from llm_anbieter import LLMNichtVerfuegbar, Wahl  # noqa: E402


class ModellAnzeigeTests(unittest.TestCase):
    def test_betrieb_zeigt_modell_der_sitzung(self):
        rueckfall = Wahl("gespraech", "claude", "claude-haiku-5-5", "litellm", "LITELLM_API_KEY fehlt")
        msg = bot.betrieb_message("aus", llm=rueckfall.anzeige)
        self.assertEqual(msg["modelle"]["llm"], "Claude Haiku 5.5 (Rückfall)")
        haus = Wahl("gespraech", "litellm", "qwen3.6:35b", "litellm")
        self.assertEqual(bot.betrieb_message("aus", llm=haus.anzeige)["modelle"]["llm"], "qwen3.6:35b (LiteLLM)")
        self.assertTrue(bot.betrieb_message("aus")["modelle"]["llm"].startswith("Claude"))


class ZugangNachLernenTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_schliesst_erst_nach_der_letzten_auswertung(self):
        import asyncio

        reihenfolge: list[str] = []

        async def lernen():
            await asyncio.sleep(0.05)
            reihenfolge.append("gelernt")

        class Zugang:
            async def schliessen(self):
                reihenfolge.append("geschlossen")

        await bot._zugang_nach_lernen(asyncio.create_task(lernen()), Zugang())
        self.assertEqual(reihenfolge, ["gelernt", "geschlossen"])

    async def test_fehler_beim_lernen_oder_schliessen_stoert_nicht(self):
        import asyncio

        async def kaputt():
            raise RuntimeError("Lernen fehlgeschlagen")

        class Zugang:
            async def schliessen(self):
                raise RuntimeError("Schließen fehlgeschlagen")

        await bot._zugang_nach_lernen(asyncio.create_task(kaputt()), Zugang())
        await bot._zugang_nach_lernen(None, Zugang())


class OhneGespraechsmodellTests(unittest.IsolatedAsyncioTestCase):
    async def test_sitzung_endet_sauber(self):
        class Conn:
            disconnected = False

            async def disconnect(self):
                Conn.disconnected = True

        args = mock.Mock(body={}, webrtc_connection=Conn())
        ident = Identity("0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c", "Erika", "admin", "du")
        with mock.patch.object(bot, "identity_from_body", return_value=ident), \
                mock.patch.object(bot, "create_transport", mock.AsyncMock()), \
                mock.patch.object(bot, "run_bot", mock.AsyncMock(side_effect=LLMNichtVerfuegbar("kein Gateway"))):
            await bot.bot(args)
        self.assertTrue(Conn.disconnected)
