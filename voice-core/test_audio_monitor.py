"""Tests für den Stille-Wächter des Mikrofons im Kern (audio_monitor.py)."""

import math
import unittest
from array import array

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.tests.utils import run_test

from audio_monitor import STILLE_DBFS, AudioInputMonitor, MikrofonStille, _dbfs, mikrofon_message

RATE = 16000
SAMPLES = 320  # 20 ms


def paket(amplitude: int) -> InputAudioRawFrame:
    werte = array("h", (int(amplitude * math.sin(i / 5)) for i in range(SAMPLES)))
    return InputAudioRawFrame(audio=werte.tobytes(), sample_rate=RATE, num_channels=1)


def uhr(schritt: float = 0.02):
    t = [0.0]

    def jetzt() -> float:
        t[0] += schritt
        return t[0]

    return jetzt


class MikrofonStilleTest(unittest.TestCase):
    def test_meldet_einmal_nach_zehn_sekunden(self):
        w = MikrofonStille()
        self.assertIsNone(w.pruefen(-120, 0.0))
        self.assertIsNone(w.pruefen(-120, 9.99))
        self.assertEqual(w.pruefen(-120, 10.0), "still")
        self.assertIsNone(w.pruefen(-120, 30.0), "nur einmal je stiller Phase")

    def test_ton_setzt_zurueck_und_meldet_ok(self):
        w = MikrofonStille()
        w.pruefen(-120, 0.0)
        self.assertEqual(w.pruefen(-120, 10.0), "still")
        self.assertEqual(w.pruefen(-60, 10.5), "ok")
        self.assertIsNone(w.pruefen(-60, 11.0))
        self.assertIsNone(w.pruefen(-120, 12.0))
        self.assertIsNone(w.pruefen(-120, 21.9))
        self.assertEqual(w.pruefen(-120, 22.0), "still")

    def test_leises_raumrauschen_ist_keine_stille(self):
        w = MikrofonStille()
        for i in range(2000):
            self.assertIsNone(w.pruefen(STILLE_DBFS + 10, i * 0.02))

    def test_stille_spur_aus_dem_feld_liegt_unter_der_schwelle(self):
        # Gemessen am 20.10.: -112 dBFS (dekodierte Stille), echtes Mikrofon im leisen Raum etwa -70 dBFS
        self.assertLess(_dbfs(paket(0).audio), STILLE_DBFS)
        self.assertLess(-112, STILLE_DBFS)
        self.assertGreater(_dbfs(paket(20).audio), STILLE_DBFS)

    def test_nachricht(self):
        self.assertEqual(mikrofon_message("still"), {"type": "mikrofon", "zustand": "still", "sekunden": 10})


class AudioInputMonitorTest(unittest.IsolatedAsyncioTestCase):
    async def test_stille_spur_ergibt_genau_eine_meldung_und_danach_ok(self):
        frames = [paket(0) for _ in range(600)] + [paket(3000) for _ in range(10)]  # 12 s Stille, dann Ton
        unten, _ = await run_test(AudioInputMonitor(clock=uhr()), frames_to_send=frames)
        meldungen = [f.data for f in unten if isinstance(f, RTVIServerMessageFrame)]
        self.assertEqual(meldungen, [mikrofon_message("still"), mikrofon_message("ok")])
        self.assertEqual(sum(isinstance(f, InputAudioRawFrame) for f in unten), 610, "Audio läuft unverändert weiter")

    async def test_echtes_mikrofon_ohne_meldung(self):
        frames = [paket(40) for _ in range(700)]
        unten, _ = await run_test(AudioInputMonitor(clock=uhr()), frames_to_send=frames)
        self.assertFalse([f for f in unten if isinstance(f, RTVIServerMessageFrame)])


if __name__ == "__main__":
    unittest.main()
