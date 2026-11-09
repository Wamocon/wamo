"""Ausgaben kürzen in der Sitzung (bot.py): der Kürzer sitzt hinter der Aktivitätsanzeige, ausgabe_nachladen steht im Kontext,
die Schwelle 0 schaltet beides ab, und am Sitzungsende steht eine Zeile mit Zahlen im Log (keine Inhalte)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from loguru import logger

import test_smoke_bot as smoke
from activity import ActivityReporter
from auth_gate import Identity
from ausgaben_kuerzen import AusgabenKuerzer

bot = smoke.bot


class AusgabenSitzungTests(unittest.IsolatedAsyncioTestCase):
    async def build(self, schwelle: str | None):
        env = {**smoke.DUMMY_ENV}
        if schwelle is not None:
            env["WAMO_AUSGABE_SCHWELLE"] = schwelle
        with mock.patch.dict(os.environ, env), \
             mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)), \
             mock.patch.object(bot, "Pipeline", wraps=bot.Pipeline) as pipeline, \
             mock.patch.object(bot, "register_nachladen", wraps=bot.register_nachladen) as nachladen:
            session = await bot.build_session(smoke.FakeTransport(), Identity(smoke.USER, "Erika Muster", "mitglied", "sie"))
        return session, pipeline, nachladen

    async def test_kuerzer_steht_direkt_hinter_der_aktivitaetsanzeige(self):
        session, pipeline, nachladen = await self.build(None)
        stufen = pipeline.call_args.args[0]
        i = next(n for n, p in enumerate(stufen) if isinstance(p, ActivityReporter))
        self.assertIsInstance(stufen[i + 1], AusgabenKuerzer)  # Zeitleiste und Freigabekarten sehen das Original
        self.assertEqual(sum(isinstance(p, AusgabenKuerzer) for p in stufen), 1)
        self.assertEqual(nachladen.call_count, 1)
        await session.cleanup()

    async def test_schwelle_null_schaltet_kuerzer_und_werkzeug_ab(self):
        session, pipeline, nachladen = await self.build("0")
        self.assertFalse(any(isinstance(p, AusgabenKuerzer) for p in pipeline.call_args.args[0]))
        self.assertEqual(nachladen.call_count, 0)
        await session.cleanup()

    async def test_sitzungsende_schreibt_eine_zeile_ohne_inhalte(self):
        zeilen: list[str] = []
        kennung = logger.add(lambda m: zeilen.append(str(m)), level="INFO", format="{message}")
        try:
            session, _pipeline, _nachladen = await self.build(None)
            await session.cleanup()
            await session.cleanup()  # idempotent: die Zeile kommt nur einmal
        finally:
            logger.remove(kennung)
        treffer = [z for z in zeilen if "Ausgaben kürzen:" in z]
        self.assertEqual(len(treffer), 1)
        self.assertIn("0 Ergebnisse gekürzt", treffer[0])
        self.assertIn("Schwelle 3000", treffer[0])


if __name__ == "__main__":
    unittest.main()
