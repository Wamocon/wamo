"""Innehalten in der Sitzung (bot.py): je Sitzung ein eigener Halt am Tonausgang, Wiedergabe-Start vor dem Ausgang, der Tracker mit
Halt hinter ihm, die Start-Strategie meldet einzelne Wörter. WAMO_INNEHALTEN=aus und die Sprachausgabe im Haus lassen alles wie bisher."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import test_smoke_bot as smoke
from auth_gate import Identity
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
from soft_interrupt import MeaningfulWordsUserTurnStartStrategy, PlaybackTracker, SoftInterruptGate
from sprache_anbieter import Baustein, SprachWahl
from wiedergabe import AusgabeHalt, WiedergabeStart

bot = smoke.bot


class _Ausgang(smoke._Passthrough):
    """Ausgang mit write_audio_frame wie transport.output() von Pipecat."""

    async def write_audio_frame(self, frame):
        return True


class _Transport(smoke.FakeTransport):
    def __init__(self):
        super().__init__()
        self._out = _Ausgang()


class InnehaltenSitzungTest(unittest.IsolatedAsyncioTestCase):
    async def bauen(self, **umgebung):
        env = {**smoke.DUMMY_ENV, **umgebung}
        transport = _Transport()
        with mock.patch.dict(os.environ, env), \
             mock.patch("llm_anbieter.claude_problem", mock.AsyncMock(return_value=None)), \
             mock.patch.object(bot, "Pipeline", wraps=bot.Pipeline) as pipeline:
            session = await bot.build_session(transport, Identity(smoke.USER, "Erika Muster", "mitglied", "sie"))
        return session, pipeline.call_args.args[0], transport

    def stufen(self, stufen):
        gate = next(p for p in stufen if isinstance(p, SoftInterruptGate))
        tracker = next(p for p in stufen if isinstance(p, PlaybackTracker))
        starts = [p for p in stufen if isinstance(p, WiedergabeStart)]
        return gate, tracker, starts

    async def test_standard_ein_halt_am_ausgang_start_vor_dem_ausgang_tracker_dahinter(self):
        session, stufen, transport = await self.bauen()
        gate, tracker, starts = self.stufen(stufen)
        self.assertEqual(len(starts), 1)
        i = stufen.index(transport.output())
        self.assertIs(stufen[i - 1], starts[0], "WiedergabeStart sitzt direkt vor dem Ausgang")
        self.assertIs(stufen[i + 1], tracker, "der Tracker direkt dahinter")
        self.assertIsInstance(gate._halt, AusgabeHalt)
        self.assertIs(tracker._halt, gate._halt)
        self.assertIs(starts[0]._halt, gate._halt)
        self.assertIn("write_audio_frame", vars(transport.output()), "der Halt hängt in write_audio_frame dieses Ausgangs")
        self.assertEqual((gate._weiter_secs, gate._max_pause_secs), (0.7, 2.5))
        await session.cleanup()

    async def test_die_strategie_meldet_einzelne_woerter_an_das_gate(self):
        session, stufen, _transport = await self.bauen()
        gate, _tracker, _starts = self.stufen(stufen)
        strategien = [s for p in stufen if isinstance(p, LLMUserAggregator) for s in p._params.user_turn_strategies.start]
        strategie = next(s for s in strategien if isinstance(s, MeaningfulWordsUserTurnStartStrategy))
        self.assertEqual(strategie._on_partial, gate.ansatz)
        await session.cleanup()

    async def test_zeiten_aus_der_umgebung(self):
        session, stufen, _transport = await self.bauen(WAMO_INNEHALTEN_WEITER_SECS="1,1", WAMO_INNEHALTEN_MAX_SECS="3")
        gate, _tracker, _starts = self.stufen(stufen)
        self.assertEqual((gate._weiter_secs, gate._max_pause_secs), (1.1, 3.0))
        await session.cleanup()

    async def test_schalter_aus_alles_wie_bisher(self):
        session, stufen, transport = await self.bauen(WAMO_INNEHALTEN="aus")
        gate, tracker, starts = self.stufen(stufen)
        self.assertEqual(starts, [])
        self.assertIsNone(gate._halt)
        self.assertIsNone(tracker._halt)
        self.assertNotIn("write_audio_frame", vars(transport.output()), "der Ausgang bleibt unberührt")
        strategien = [s for p in stufen if isinstance(p, LLMUserAggregator) for s in p._params.user_turn_strategies.start]
        strategie = next(s for s in strategien if isinstance(s, MeaningfulWordsUserTurnStartStrategy))
        self.assertIsNone(strategie._on_partial)
        await session.cleanup()

    async def test_sprachausgabe_im_haus_hat_keinen_halt(self):
        """Das Haus-Modell liefert ganze Sätze ohne Wortzeiten: dort greift weiter die Zeitgrenze von heute."""
        wahl = SprachWahl(stt=Baustein("soniox", "soniox"), tts=Baustein("haus", "haus", "tts-haus"))
        with mock.patch.object(bot, "sprach_wahl", mock.AsyncMock(return_value=wahl)),              mock.patch.object(bot, "haus_tts", return_value=smoke._Passthrough()):
            session, stufen, transport = await self.bauen()
        gate, tracker, starts = self.stufen(stufen)
        self.assertEqual(starts, [])
        self.assertIsNone(gate._halt)
        self.assertIsNone(tracker._halt)
        self.assertNotIn("write_audio_frame", vars(transport.output()))
        await session.cleanup()

    async def test_zwei_sitzungen_haben_zwei_halte(self):
        a, stufen_a, _ = await self.bauen()
        b, stufen_b, _ = await self.bauen()
        halt_a = self.stufen(stufen_a)[0]._halt
        halt_b = self.stufen(stufen_b)[0]._halt
        self.assertIsNot(halt_a, halt_b, "kein gemeinsamer Zustand zwischen den Sitzungen")
        halt_a.anhalten()
        self.assertFalse(halt_b.haelt)
        await a.cleanup()
        await b.cleanup()


if __name__ == "__main__":
    unittest.main()
