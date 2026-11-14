"""Ein "Ja", während WAMO gerade eine Frage stellt, ist die Antwort und kein Rückmeldelaut (soft_interrupt.py).

Ausführen:  uv run python -m pytest -q test_frage_antwort.py   (im Ordner voice-core/)
"""

import time
import unittest
from unittest import mock

from pipecat.frames.frames import BotStartedSpeakingFrame, FunctionCallInProgressFrame, InterimTranscriptionFrame

from soft_interrupt import MeaningfulWordsUserTurnStartStrategy, SoftInterruptGate, fragt_bisher, ist_antwort


def zwischenstand(text):
    return InterimTranscriptionFrame(text, "u", "t")


class AntwortTests(unittest.TestCase):
    def test_zustimmung_ist_eine_antwort(self):
        for text in ["Ja", " ja genau", "Okay", "Klar", "mach", "Ja bitte"]:
            with self.subTest(text=text):
                self.assertTrue(ist_antwort(text))

    def test_zuhoeren_aber_und_nachfragen_sind_keine_antwort(self):
        for text in ["mhm", "aha", "hmm", "Ja, aber", "Ja?", "Bitte?", "Nein", ""]:
            with self.subTest(text=text):
                self.assertFalse(ist_antwort(text))

    def test_frage_nach_dem_bisher_gesprochenen(self):
        self.assertTrue(fragt_bisher("Soll ich"))
        self.assertTrue(fragt_bisher("Möchtest du die Liste"))
        self.assertTrue(fragt_bisher("Passt dir das?"))
        self.assertFalse(fragt_bisher("Ich habe die Liste"))
        self.assertFalse(fragt_bisher(""))


class StrategieTests(unittest.IsolatedAsyncioTestCase):
    async def ausloesen(self, text, *, fragt, spricht=True):
        s = MeaningfulWordsUserTurnStartStrategy(min_words=2, enable_interruptions=False, fragt=lambda: fragt)
        if spricht:
            await s.process_frame(BotStartedSpeakingFrame())
        with mock.patch.object(s, "trigger_user_turn_started") as gestartet, \
             mock.patch.object(s, "trigger_reset_aggregation") as verworfen:
            await s.process_frame(zwischenstand(text))
        return gestartet.await_count > 0, verworfen.await_count > 0

    async def test_ja_waehrend_der_frage_ist_die_antwort(self):
        for text in ["Ja", " ja genau", "Okay", "Klar", "mach"]:
            with self.subTest(text=text):
                gestartet, verworfen = await self.ausloesen(text, fragt=True)
                self.assertTrue(gestartet)
                self.assertFalse(verworfen)  # das Transkript bleibt erhalten

    async def ausloesen_mit_echo_schutz(self, text, gespielt, *, echo_modus=False):
        s = MeaningfulWordsUserTurnStartStrategy(min_words=2, enable_interruptions=False, fragt=lambda: True,
                                                 gesprochen=lambda sekunden: gespielt, echo_modus=True)
        if echo_modus:
            s._echo_zeiten.extend([time.monotonic(), time.monotonic()])  # zwei Einwürfe der letzten Sekunden waren Echo
        await s.process_frame(BotStartedSpeakingFrame())
        with mock.patch.object(s, "trigger_user_turn_started") as gestartet, \
             mock.patch.object(s, "trigger_reset_aggregation") as verworfen:
            await s.process_frame(zwischenstand(text))
        return gestartet.await_count > 0, verworfen.await_count > 0

    async def test_ja_das_wamo_gerade_selbst_gesprochen_hat_ist_keine_antwort(self):
        """Hört das Mikrofon WAMO selbst, ist sein eigenes "Ja" kein Ja der Person: es startet keinen Zug."""
        gespielt = ["ja", "das", "geht", "soll", "ich", "es", "tun"]
        for text in ["Ja", "ja."]:
            with self.subTest(text=text):
                self.assertEqual(await self.ausloesen_mit_echo_schutz(text, gespielt), (False, True))

    async def test_ja_nach_einer_frage_ohne_dieses_wort_bleibt_die_antwort(self):
        gespielt = ["soll", "ich", "das", "tun"]
        self.assertEqual(await self.ausloesen_mit_echo_schutz("Ja", gespielt), (True, False))
        self.assertEqual(await self.ausloesen_mit_echo_schutz("Ja genau", ["genau", "so", "geht", "es", "soll", "ich", "weiter"]), (True, False))

    async def test_im_echo_modus_ist_ein_ja_keine_antwort(self):
        gespielt = ["soll", "ich", "das", "tun"]
        self.assertEqual(await self.ausloesen_mit_echo_schutz("Ja", gespielt, echo_modus=True), (False, True))

    async def test_ohne_frage_bleibt_ja_ein_rueckmeldelaut(self):
        for text in ["Ja", "ja genau", "Okay"]:
            with self.subTest(text=text):
                self.assertEqual(await self.ausloesen(text, fragt=False), (False, True))

    async def test_zuhoeren_bleibt_auch_bei_einer_frage_zuhoeren(self):
        for text in ["mhm", "aha", "hmm"]:
            with self.subTest(text=text):
                self.assertEqual(await self.ausloesen(text, fragt=True), (False, True))

    async def test_aber_und_nachfragen_sind_keine_antwort(self):
        # "Bitte?" fehlt hier bewusst: es ist in WAMO ein Stoppwort (Nachfrage) und unterbricht deshalb ohnehin.
        for text in ["Ja, aber", "Ja?"]:
            with self.subTest(text=text):
                gestartet, _ = await self.ausloesen(text, fragt=True)
                self.assertFalse(gestartet)

    async def test_waehrend_eines_werkzeugs_bleibt_okay_zuhoeren(self):
        # WAMO schweigt, ein Werkzeug läuft, die letzte Frage steht noch da: "okay" darf das Werkzeug nicht abbrechen.
        s = MeaningfulWordsUserTurnStartStrategy(min_words=2, enable_interruptions=False, fragt=lambda: True,
                                                 werkzeug_laeuft=lambda: True)
        await s.process_frame(FunctionCallInProgressFrame(function_name="websuche", tool_call_id="t1", arguments={}))
        with mock.patch.object(s, "trigger_user_turn_started") as gestartet, \
             mock.patch.object(s, "trigger_reset_aggregation"):
            await s.process_frame(zwischenstand("okay"))
        self.assertEqual(gestartet.await_count, 0)

    async def test_ohne_fragt_wie_bisher(self):
        s = MeaningfulWordsUserTurnStartStrategy(min_words=2, enable_interruptions=False)
        await s.process_frame(BotStartedSpeakingFrame())
        with mock.patch.object(s, "trigger_user_turn_started") as gestartet, \
             mock.patch.object(s, "trigger_reset_aggregation"):
            await s.process_frame(zwischenstand("Ja"))
        self.assertEqual(gestartet.await_count, 0)


class GateTests(unittest.TestCase):
    def test_gate_weiss_ob_wamo_gerade_fragt(self):
        g = SoftInterruptGate()
        self.assertFalse(g.fragt())
        g.wort_beginnt("Soll")
        g.wort_beginnt("ich")
        self.assertTrue(g.fragt())  # Fragebeginn, das Fragezeichen fehlt noch
        g.wort_beginnt("loslegen?")
        g.wort_beginnt("Ich")  # nächster Satz beginnt: die Frage ist zu Ende gesprochen
        self.assertFalse(g.fragt())

    def test_frage_bleibt_bis_zum_naechsten_satz(self):
        g = SoftInterruptGate()
        for w in ["Passt", "dir", "das?"]:
            g.wort_beginnt(w)
        self.assertTrue(g.fragt())  # die Frage ist eben zu Ende, die Antwort kommt jetzt


if __name__ == "__main__":
    unittest.main()
