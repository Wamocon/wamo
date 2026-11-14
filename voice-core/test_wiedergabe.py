"""Tests für wiedergabe.py: Innehalten am Tonausgang, echte Abspielposition, Schalter.

Pipecat schreibt WAMOs Ton in 40-ms-Happen über write_audio_frame des Ausgangs und wartet jeweils, bis die Spur den Happen
abgeholt hat. AusgabeHalt klinkt sich dort ein: anhalten lässt den nächsten Happen warten (Stille auf der Spur), fortsetzen
macht an derselben Stelle weiter, abschneiden verwirft den Rest einer abgebrochenen Antwort.
"""

import asyncio
import unittest
from unittest import mock

from pipecat.frames.frames import BotStoppedSpeakingFrame, InterruptionFrame, OutputAudioRawFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import wiedergabe
from wiedergabe import (
    ABBRUCH_NACHLAUF_SECS,
    AusgabeHalt,
    WiedergabeStart,
    halt_fuer,
    innehalten_an,
    innehalten_max_secs,
    innehalten_weiter_secs,
    ton_ns,
)

HAPPEN = bytes(1920)  # 40 ms bei 24 kHz, 16 Bit, mono


def sprache():
    return TTSAudioRawFrame(audio=HAPPEN, sample_rate=24000, num_channels=1)


class Ausgang:
    """Ersatz für transport.output(): schreibt nichts, merkt sich die Happen."""

    def __init__(self):
        self.geschrieben = []

    async def write_audio_frame(self, frame):
        self.geschrieben.append(frame)
        return True


class Uhr:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class AusgabeHaltTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.uhr = Uhr()
        self.halt = AusgabeHalt(uhr_ns=lambda: 5_000_000_000, uhr=self.uhr)
        self.ausgang = Ausgang()
        self.assertTrue(self.halt.einhaengen(self.ausgang))

    def test_dauer_eines_happens(self):
        self.assertEqual(ton_ns(sprache()), 40_000_000)

    def test_ausgang_ohne_write_audio_frame_bleibt_ohne_halt(self):
        self.assertFalse(AusgabeHalt().einhaengen(object()))

    async def test_position_zaehlt_nur_sprachausgabe(self):
        self.assertIsNone(self.halt.gespielt_bis_ns())
        await self.ausgang.write_audio_frame(sprache())
        await self.ausgang.write_audio_frame(sprache())
        # Füllwörter gehen als einfacher Ton hinaus und verschieben die Wort-Zeitstempel nicht
        await self.ausgang.write_audio_frame(OutputAudioRawFrame(audio=HAPPEN, sample_rate=24000, num_channels=1))
        self.assertEqual(self.halt.gespielt_bis_ns(), 5_000_000_000 + 80_000_000)
        self.assertEqual(len(self.ausgang.geschrieben), 3)

    async def test_anhalten_haelt_den_naechsten_happen_zurueck(self):
        self.halt.anhalten()
        self.assertTrue(self.halt.haelt)
        schreiben = asyncio.create_task(self.ausgang.write_audio_frame(sprache()))
        await asyncio.sleep(0.05)
        self.assertFalse(schreiben.done())
        self.assertEqual(self.ausgang.geschrieben, [])
        self.halt.fortsetzen()
        self.assertTrue(await schreiben)
        self.assertEqual(len(self.ausgang.geschrieben), 1)
        self.assertFalse(self.halt.haelt)

    async def test_nach_abbruch_wird_der_rest_verworfen(self):
        await self.ausgang.write_audio_frame(sprache())
        self.halt.anhalten()
        schreiben = asyncio.create_task(self.ausgang.write_audio_frame(sprache()))
        await asyncio.sleep(0)
        self.halt.abschneiden()
        self.assertFalse(await schreiben)  # gehört zur abgebrochenen Antwort
        self.assertIsNone(self.halt.gespielt_bis_ns())  # die nächste Antwort zählt neu
        self.uhr.t += ABBRUCH_NACHLAUF_SECS + 0.01
        self.assertTrue(await self.ausgang.write_audio_frame(sprache()))
        self.assertEqual(len(self.ausgang.geschrieben), 2)

    async def test_eine_pause_ueber_die_hoechstdauer_laeuft_weiter(self):
        """Pipecat hält ein Schreiben von über 10 s für eine tote Verbindung: nie länger als MAX_PAUSE_SECS stehen bleiben."""
        with mock.patch.object(wiedergabe, "MAX_PAUSE_SECS", 0.05):
            self.halt.anhalten()
            self.assertTrue(await asyncio.wait_for(self.ausgang.write_audio_frame(sprache()), 1.0))
        self.assertFalse(self.halt.haelt)
        self.assertEqual(len(self.ausgang.geschrieben), 1)

    def test_die_hoechstdauer_liegt_unter_der_schreibgrenze_von_pipecat(self):
        from pipecat.transports.base_transport import TransportParams

        self.assertLess(wiedergabe.MAX_PAUSE_SECS, TransportParams().audio_out_write_timeout_secs)
        self.assertLessEqual(wiedergabe.MAX_PAUSE_SECS, 4.0)

    async def test_warten_auf_fortschritt_endet_mit_dem_naechsten_happen(self):
        warten = asyncio.create_task(self.halt.warte_auf_fortschritt(2.0))
        await asyncio.sleep(0.01)
        self.assertFalse(warten.done())
        await self.ausgang.write_audio_frame(sprache())
        await asyncio.wait_for(warten, 0.5)

    async def test_zwei_sitzungen_halten_unabhaengig(self):
        """Der Kern bedient mehrere Sitzungen: der Halt einer Sitzung lässt die andere weitersprechen."""
        andere = AusgabeHalt(uhr_ns=lambda: 7_000_000_000, uhr=self.uhr)
        anderer_ausgang = Ausgang()
        andere.einhaengen(anderer_ausgang)
        self.halt.anhalten()
        self.assertTrue(await asyncio.wait_for(anderer_ausgang.write_audio_frame(sprache()), 0.5))
        self.assertFalse(andere.haelt)
        schreiben = asyncio.create_task(self.ausgang.write_audio_frame(sprache()))
        await asyncio.sleep(0.03)
        self.assertFalse(schreiben.done())
        self.halt.fortsetzen()
        await schreiben
        self.assertEqual(andere.gespielt_bis_ns(), 7_000_000_000 + 40_000_000)
        self.assertEqual(self.halt.gespielt_bis_ns(), 5_000_000_000 + 40_000_000)


class WiedergabeStartTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        patch = mock.patch.object(FrameProcessor, "process_frame", new=mock.AsyncMock())
        patch.start()
        self.addCleanup(patch.stop)
        self.halt = AusgabeHalt(uhr_ns=lambda: 9_000_000_000)
        self.start = WiedergabeStart(self.halt)
        self.weitergegeben = []

        async def weiter(frame, richtung=FrameDirection.DOWNSTREAM):
            self.weitergegeben.append(frame)

        self.start.push_frame = weiter

    async def test_erster_ton_der_antwort_setzt_den_beginn(self):
        self.assertIsNone(self.halt.gespielt_bis_ns())
        ton = sprache()
        await self.start.process_frame(ton, FrameDirection.DOWNSTREAM)
        self.assertEqual(self.halt.gespielt_bis_ns(), 9_000_000_000)
        self.assertEqual(self.weitergegeben, [ton])

    async def test_ein_einfacher_ausgangston_setzt_ihn_nicht(self):
        await self.start.process_frame(OutputAudioRawFrame(audio=HAPPEN, sample_rate=24000, num_channels=1), FrameDirection.DOWNSTREAM)
        self.assertIsNone(self.halt.gespielt_bis_ns())

    async def test_ende_der_antwort_und_abbruch_beginnen_neu(self):
        await self.start.process_frame(sprache(), FrameDirection.DOWNSTREAM)
        await self.start.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        self.assertIsNone(self.halt.gespielt_bis_ns())
        await self.start.process_frame(sprache(), FrameDirection.DOWNSTREAM)
        await self.start.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        self.assertIsNone(self.halt.gespielt_bis_ns())


class SchalterTest(unittest.TestCase):
    def test_standard_an(self):
        self.assertTrue(innehalten_an(""))
        self.assertTrue(innehalten_an("an"))
        self.assertFalse(innehalten_an("aus"))
        self.assertFalse(innehalten_an("0"))

    def test_zeiten_standard_und_grenzen(self):
        self.assertEqual(innehalten_weiter_secs(""), 0.7)
        self.assertEqual(innehalten_max_secs(""), 2.5)
        self.assertEqual(innehalten_weiter_secs("1,2"), 1.2)
        self.assertEqual(innehalten_max_secs("3"), 3.0)

    def test_ungueltige_zeiten_fallen_auf_den_standard(self):
        for wert in ("abc", "-1", "0", "99"):
            with self.subTest(wert=wert):
                self.assertEqual(innehalten_weiter_secs(wert), 0.7)
                self.assertEqual(innehalten_max_secs(wert), 2.5)

    def test_die_hoechstpause_bleibt_unter_der_sicherung_des_ausgangs(self):
        self.assertEqual(innehalten_max_secs("3.9"), 2.5, "über 3,5 s würde die Pause an der Sicherung von 4 s enden")


class HaltFuerTest(unittest.TestCase):
    def test_normal_haengt_sich_in_den_ausgang_ein(self):
        ausgang = Ausgang()
        halt = halt_fuer(ausgang, haus=False, wert="an")
        self.assertIsInstance(halt, AusgabeHalt)
        self.assertIn("write_audio_frame", vars(ausgang), "write_audio_frame dieser Ausgangsinstanz ist ersetzt")

    def test_schalter_aus_gibt_keinen_halt(self):
        ausgang = Ausgang()
        original = ausgang.write_audio_frame
        self.assertIsNone(halt_fuer(ausgang, haus=False, wert="aus"))
        self.assertEqual(ausgang.write_audio_frame, original, "der Ausgang bleibt unberührt")

    def test_hausmodell_liefert_ganze_saetze_kein_halt(self):
        """Die Sprachausgabe im Haus liefert keine Wörter mit Zeitstempel: dort greift weiter die Zeitgrenze von heute."""
        ausgang = Ausgang()
        original = ausgang.write_audio_frame
        self.assertIsNone(halt_fuer(ausgang, haus=True, wert="an"))
        self.assertEqual(ausgang.write_audio_frame, original)

    def test_ausgang_ohne_write_audio_frame_gibt_keinen_halt(self):
        self.assertIsNone(halt_fuer(object(), haus=False, wert="an"))


if __name__ == "__main__":
    unittest.main()
