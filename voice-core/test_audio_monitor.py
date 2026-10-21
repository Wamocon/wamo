"""Tests für den Stille-Wächter des Mikrofons im Kern (audio_monitor.py)."""

import math
import time
import unittest
from array import array

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test

from audio_monitor import STILLE_DBFS, AudioInputMonitor, MikrofonAusfall, MikrofonStille, _dbfs, mikrofon_message

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


class MikrofonAusfallTest(unittest.TestCase):
    def test_meldet_einmal_nach_laufendem_ton_und_dann_ok(self):
        a = MikrofonAusfall(0.0, ausfall_secs=5.0, start_secs=20.0)
        self.assertIsNone(a.paket(1.0))
        self.assertIsNone(a.pruefen(5.9))
        self.assertEqual(a.pruefen(6.0), ("fehlt", 5.0))
        self.assertIsNone(a.pruefen(30.0), "nur einmal je Ausfall")
        self.assertEqual(a.paket(31.0), "ok")
        self.assertIsNone(a.paket(31.02))
        self.assertIsNone(a.pruefen(35.0))
        self.assertEqual(a.pruefen(36.1)[0], "fehlt")

    def test_nie_ein_paket_gilt_erst_nach_der_startfrist(self):
        a = MikrofonAusfall(100.0, ausfall_secs=5.0, start_secs=20.0)
        self.assertIsNone(a.pruefen(110.0), "Verbindungsaufbau ist kein Ausfall")
        self.assertEqual(a.pruefen(120.0), ("fehlt", 20.0))

    def test_steter_strom_meldet_nie(self):
        a = MikrofonAusfall(0.0)
        for i in range(1, 2000):
            a.paket(i * 0.02)
            self.assertIsNone(a.pruefen(i * 0.02))

    def test_nachricht_hat_mindestens_eine_sekunde(self):
        self.assertEqual(mikrofon_message("fehlt", 0.2)["sekunden"], 1)
        self.assertEqual(mikrofon_message("fehlt", 5)["zustand"], "fehlt")


def schnelle_uhr(faktor: float = 20.0):
    """Echtzeit mal faktor: 5 Sekunden Ausfall dauern im Test 250 ms (Spielraum für einen ausgelasteten Rechner)."""
    start = time.monotonic()
    return lambda: (time.monotonic() - start) * faktor


class AusfallWaechterTest(unittest.IsolatedAsyncioTestCase):
    async def test_beendete_spur_ergibt_fehlt_und_danach_ok(self):
        # Pakete, dann Ausfall (wie nach einem Gerätewechsel mit toter Sendespur), dann wieder Pakete
        frames = [paket(3000) for _ in range(5)] + [SleepFrame(0.9)] + [paket(3000) for _ in range(5)]
        monitor = AudioInputMonitor(clock=schnelle_uhr(), wache_takt_secs=0.005)
        unten, _ = await run_test(monitor, frames_to_send=frames)
        meldungen = [f.data for f in unten if isinstance(f, RTVIServerMessageFrame)]
        self.assertEqual([m["zustand"] for m in meldungen], ["fehlt", "ok"])
        self.assertGreaterEqual(meldungen[0]["sekunden"], 5)
        self.assertEqual(sum(isinstance(f, InputAudioRawFrame) for f in unten), 10)

    async def test_stetiger_strom_ohne_meldung(self):
        frames = []
        for _ in range(10):
            frames += [paket(3000), SleepFrame(0.005)]
        monitor = AudioInputMonitor(clock=schnelle_uhr(), wache_takt_secs=0.005)
        unten, _ = await run_test(monitor, frames_to_send=frames)
        self.assertFalse([f for f in unten if isinstance(f, RTVIServerMessageFrame)])

    def test_nach_ausfall_bleibt_still_gemeldet_statt_ok(self):
        # Stille war gemeldet, dann Ausfall, dann wieder nur Stille: kein falsches ok
        monitor = AudioInputMonitor()
        self.assertEqual(monitor._zustand_nach_ausfall(), "ok")
        monitor._stille.gemeldet = True
        self.assertEqual(monitor._zustand_nach_ausfall(), "still")


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
