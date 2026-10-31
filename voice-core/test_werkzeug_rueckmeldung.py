"""Paket B1: Rückmeldelaut bei laufendem Werkzeug (WAMO_RUECKMELDUNG_WERKZEUG).

Ein "mhm" der Person, während ein Werkzeug läuft und WAMO schweigt, darf keinen Zug starten (sonst bricht Pipecat das
Werkzeug ab). Stoppwörter und echte Einwürfe wirken weiter.
"""

import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from soft_interrupt import (
    WERKZEUG_MAX_SECS,
    WERKZEUG_NACHLAUF_SECS,
    MeaningfulWordsUserTurnStartStrategy,
    SoftInterruptGate,
    rueckmeldung_werkzeug_an,
)


def aufruf(tid="t1", cancel=True):
    return FunctionCallInProgressFrame(function_name="websuche", tool_call_id=tid, arguments={}, cancel_on_interruption=cancel)


def ergebnis(tid="t1"):
    return FunctionCallResultFrame(function_name="websuche", tool_call_id=tid, arguments={}, result="x")


class GateZustandTest(unittest.IsolatedAsyncioTestCase):
    def gate(self):
        self.t = 100.0
        g = SoftInterruptGate(uhr=lambda: self.t, max_wait_secs=0)

        async def push(frame, direction=None):
            pass

        g.push_frame = push
        return g

    async def senden(self, g, frame):
        g._werkzeug_verfolgen(frame)

    async def test_laeuft_ab_dem_aufruf_bis_zum_ergebnis_plus_nachlauf(self):
        g = self.gate()
        self.assertFalse(g.werkzeug_laeuft())
        await self.senden(g, aufruf())
        self.assertTrue(g.werkzeug_laeuft())
        await self.senden(g, ergebnis())
        self.assertTrue(g.werkzeug_laeuft(), "Nachlauf bis die Antwort beginnt")
        self.t += WERKZEUG_NACHLAUF_SECS + 0.1
        self.assertFalse(g.werkzeug_laeuft())

    async def test_antwort_beendet_den_nachlauf(self):
        g = self.gate()
        await self.senden(g, aufruf())
        await self.senden(g, ergebnis())
        await self.senden(g, BotStartedSpeakingFrame())
        self.assertFalse(g.werkzeug_laeuft())

    async def test_zwei_werkzeuge_laufen_bis_das_letzte_fertig_ist(self):
        g = self.gate()
        await self.senden(g, aufruf("a"))
        await self.senden(g, aufruf("b"))
        await self.senden(g, ergebnis("a"))
        self.t += WERKZEUG_NACHLAUF_SECS + 1
        self.assertTrue(g.werkzeug_laeuft(), "b läuft noch")
        await self.senden(g, FunctionCallCancelFrame(function_name="websuche", tool_call_id="b"))
        self.assertTrue(g.werkzeug_laeuft())  # Nachlauf nach dem Abbruch
        self.t += WERKZEUG_NACHLAUF_SECS + 0.1
        self.assertFalse(g.werkzeug_laeuft())

    async def test_unterbrechung_raeumt_auf(self):
        g = self.gate()
        await self.senden(g, aufruf())
        await self.senden(g, InterruptionFrame())
        self.assertFalse(g.werkzeug_laeuft())

    async def test_asynchrone_werkzeuge_zaehlen_nicht(self):
        g = self.gate()
        await self.senden(g, aufruf(cancel=False))
        self.assertFalse(g.werkzeug_laeuft(), "ein neuer Zug bricht sie nicht ab, es gibt nichts zu schützen")

    async def test_verwaistes_werkzeug_haelt_die_regel_nicht_ewig(self):
        g = self.gate()
        await self.senden(g, aufruf())
        self.t += WERKZEUG_MAX_SECS + 1
        self.assertFalse(g.werkzeug_laeuft())

    async def test_zustand_je_sitzung(self):
        a, b = self.gate(), SoftInterruptGate(uhr=lambda: self.t, max_wait_secs=0)
        await self.senden(a, aufruf())
        self.assertFalse(b.werkzeug_laeuft(), "kein gemeinsamer Zustand zwischen Sitzungen")

    async def test_process_frame_verfolgt_die_frames(self):
        g = self.gate()
        await g.process_frame(aufruf(), FrameDirection.DOWNSTREAM)
        self.assertTrue(g.werkzeug_laeuft())


class StrategieTest(unittest.IsolatedAsyncioTestCase):
    async def lauf(self, text, laeuft, sprechend=False, interim=True, mit_hook=True):
        bekannt, ausgeloest, zurueck = [], [], []
        s = MeaningfulWordsUserTurnStartStrategy(
            min_words=1, enable_interruptions=False, beurteilt=bekannt.append,
            werkzeug_laeuft=(lambda: laeuft) if mit_hook else None,
        )
        s._bot_speaking = sprechend

        async def start():
            ausgeloest.append(True)

        async def reset():
            zurueck.append(True)

        s.trigger_user_turn_started = start
        s.trigger_reset_aggregation = reset
        frame = InterimTranscriptionFrame(text, "u", "t") if interim else TranscriptionFrame(text, "u", "t")
        await s.process_frame(frame)
        return s, ausgeloest, bekannt, zurueck

    async def test_mhm_bei_laufendem_werkzeug_startet_keinen_zug(self):
        for text in ("Mhm", "Ja genau", "Okay", "Aha, ach so"):
            for interim in (True, False):
                s, ausgeloest, bekannt, zurueck = await self.lauf(text, True, interim=interim)
                self.assertEqual(ausgeloest, [], text)
                self.assertEqual(zurueck, [True], "der Laut wird aus der Aufnahme verworfen")
                self.assertEqual(bekannt, ["rueckmeldung"])
                self.assertEqual(s.werkzeug_rueckmeldungen, 1)

    async def test_stoppwort_und_echter_einwurf_wirken_weiter(self):
        for text in ("Stopp", "Warte mal", "Doch, such lieber nach dem Wetter in Berlin", "Nein"):
            _, ausgeloest, bekannt, _ = await self.lauf(text, True, interim=False)
            self.assertEqual(ausgeloest, [True], text)
            self.assertEqual(bekannt, [])

    async def test_ohne_laufendes_werkzeug_alles_wie_bisher(self):
        # Wer schweigt und kein Werkzeug läuft, startet mit jedem Wort einen Zug, auch mit "Mhm" (Pipecat-Standard)
        _, ausgeloest, _, _ = await self.lauf("Mhm", False)
        self.assertEqual(ausgeloest, [True])

    async def test_ohne_schalter_bleibt_die_luecke_wie_bisher(self):
        _, ausgeloest, _, _ = await self.lauf("Mhm", True, mit_hook=False)
        self.assertEqual(ausgeloest, [True], "Schalter aus: unverändertes Verhalten")

    async def test_beim_sprechen_bleibt_alles_wie_gehabt(self):
        s, ausgeloest, bekannt, _ = await self.lauf("Mhm", False, sprechend=True)
        self.assertEqual((ausgeloest, bekannt, s.werkzeug_rueckmeldungen), ([], ["rueckmeldung"], 0))

    async def test_bruchstueck_ist_noch_unentschieden(self):
        _, ausgeloest, bekannt, _ = await self.lauf("J", True)
        self.assertEqual((ausgeloest, bekannt), ([], []))

    async def test_wortanfang_eines_rueckmeldelauts_zaehlt_im_zwischenstand_nicht(self):
        _, ausgeloest, _, _ = await self.lauf("Gen", True, interim=True)
        self.assertEqual(ausgeloest, [])


class SchalterTest(unittest.TestCase):
    def test_standard_aus(self):
        self.assertFalse(rueckmeldung_werkzeug_an(""))
        self.assertTrue(rueckmeldung_werkzeug_an("an"))
        self.assertFalse(rueckmeldung_werkzeug_an("aus"))


if __name__ == "__main__":
    unittest.main()
