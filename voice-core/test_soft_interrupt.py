"""Tests für die weiche Unterbrechung (soft_interrupt.py), vor allem die Zeitgrenze ohne Wort-Zeitstempel."""

import unittest

from pipecat.frames.frames import BotStartedSpeakingFrame, InterruptionFrame, UserStartedSpeakingFrame
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test

from soft_interrupt import SoftInterruptGate, meaningful_word_count


def leiser_stopp(frames) -> int:
    return sum(1 for f in frames if isinstance(f, RTVIServerMessageFrame) and f.data == {"type": "leiser_stopp"})


class SoftInterruptGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_ohne_wortgrenzen_unterbricht_die_zeitgrenze(self):
        gate = SoftInterruptGate(max_wait_secs=0.2, playback_lag_secs=0.05)
        unten, _ = await run_test(
            gate,
            frames_to_send=[BotStartedSpeakingFrame(), SleepFrame(0.05), UserStartedSpeakingFrame(), SleepFrame(0.6)],
        )
        self.assertEqual(leiser_stopp(unten), 1)
        self.assertEqual(sum(isinstance(f, InterruptionFrame) for f in unten), 1)

    async def test_vor_der_zeitgrenze_keine_unterbrechung_ohne_wortgrenze(self):
        gate = SoftInterruptGate(max_words=1, max_wait_secs=5.0, playback_lag_secs=0.05)
        unten, _ = await run_test(
            gate,
            frames_to_send=[BotStartedSpeakingFrame(), SleepFrame(0.05), UserStartedSpeakingFrame(), SleepFrame(0.05)],
        )
        # ohne Wörter: innerhalb von 0,1 s noch keine Unterbrechung (Zeitgrenze 5 s)
        self.assertEqual(leiser_stopp(unten), 0)

    async def test_zeitgrenze_aus(self):
        gate = SoftInterruptGate(max_wait_secs=0, playback_lag_secs=0.05)
        unten, _ = await run_test(
            gate,
            frames_to_send=[BotStartedSpeakingFrame(), SleepFrame(0.05), UserStartedSpeakingFrame(), SleepFrame(0.3)],
        )
        self.assertEqual(leiser_stopp(unten), 0)

    async def test_wortlimit_unterbricht_an_wortgrenze(self):
        gate = SoftInterruptGate(max_words=1, max_wait_secs=5.0)
        gate._bot_speaking = True
        gate._arm_count = 0
        gate._pending, gate._words_left = True, 1
        self.assertFalse(gate.on_word_played("Das"))  # noch ein Wort erlaubt
        gate._start_fire = lambda reason, **_: setattr(gate, "_grund", reason)  # Feuern nur beobachten
        self.assertTrue(gate.on_word_played("ist"), "dieses Wort wird abgeschnitten und gilt als nicht gesprochen")
        self.assertEqual(getattr(gate, "_grund", None), "Wortlimit")


class WoerterTest(unittest.TestCase):
    def test_rueckmeldelaute_zaehlen_nicht(self):
        self.assertEqual(meaningful_word_count("mhm ja genau"), 0)
        self.assertEqual(meaningful_word_count("Doch, stopp"), 2)
        self.assertEqual(meaningful_word_count("J"), 0)


if __name__ == "__main__":
    unittest.main()
