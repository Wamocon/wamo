"""Tests für die weiche Unterbrechung (soft_interrupt.py), vor allem die Zeitgrenze ohne Wort-Zeitstempel."""

import asyncio
import time
import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test

from soft_interrupt import (
    MeaningfulWordsUserTurnStartStrategy,
    PlaybackTracker,
    SoftInterruptGate,
    echo_filter_an,
    echo_urteil,
    ist_echo,
    meaningful_word_count,
)


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


class VerlaufWartenTest(unittest.IsolatedAsyncioTestCase):
    """Das Gate wartet nur auf einen Text, der noch nicht im Verlauf steht (früher: eine Sekunde und eine Warnung)."""

    def gate(self) -> SoftInterruptGate:
        gate = SoftInterruptGate(max_wait_secs=0)
        gate._bot_speaking = True
        return gate

    async def test_text_schon_im_verlauf_kein_warten(self):
        gate = self.gate()
        gate.antwort_beginnt()
        gate.assistant_turn_recorded()  # die Antwort ist fertig, nur der Ton läuft noch
        gate._arm()  # jetzt spricht die Person
        start = time.monotonic()
        await asyncio.wait_for(gate._wait_for_assistant_turn(), 0.5)
        self.assertLess(time.monotonic() - start, 0.2)

    async def test_nichts_gesprochen_kein_warten(self):
        gate = self.gate()
        gate._arm()
        start = time.monotonic()
        await asyncio.wait_for(gate._wait_for_assistant_turn(), 0.5)
        self.assertLess(time.monotonic() - start, 0.2)

    async def test_text_noch_nicht_im_verlauf_wartet_darauf(self):
        gate = self.gate()
        gate.antwort_beginnt()
        gate._arm()
        warten = asyncio.create_task(gate._wait_for_assistant_turn())
        await asyncio.sleep(0.1)
        self.assertFalse(warten.done(), "der Text von WAMO fehlt noch, das Gate wartet")
        gate.assistant_turn_recorded()
        await asyncio.wait_for(warten, 0.5)

    async def test_zweiter_abschnitt_nach_dem_ersten_text_wartet_wieder(self):
        gate = self.gate()
        gate.antwort_beginnt()
        gate.assistant_turn_recorded()
        gate.antwort_beginnt()  # Text, Werkzeug, Text: der zweite Abschnitt läuft noch
        gate._arm()
        warten = asyncio.create_task(gate._wait_for_assistant_turn())
        await asyncio.sleep(0.1)
        self.assertFalse(warten.done())
        gate.assistant_turn_recorded()
        await asyncio.wait_for(warten, 0.5)

    async def test_ohne_text_endet_das_warten_nach_der_frist_und_merkt_es_sich(self):
        import soft_interrupt

        alt = soft_interrupt.ASSISTANT_RECORD_TIMEOUT_SECS
        soft_interrupt.ASSISTANT_RECORD_TIMEOUT_SECS = 0.2
        try:
            gate = self.gate()
            gate.antwort_beginnt()
            gate._arm()
            await gate._wait_for_assistant_turn()
            start = time.monotonic()
            await gate._wait_for_assistant_turn()  # zweiter Einwurf: nicht erneut warten
            self.assertLess(time.monotonic() - start, 0.1)
        finally:
            soft_interrupt.ASSISTANT_RECORD_TIMEOUT_SECS = alt

    async def test_tracker_meldet_den_beginn_eines_sprachabschnitts(self):
        gate = SoftInterruptGate(max_wait_secs=0)
        unten, _ = await run_test(PlaybackTracker(gate), frames_to_send=[TTSStartedFrame(), TTSTextFrame("Hallo", "t")])
        self.assertTrue(gate._tts_offen)
        self.assertEqual(sum(isinstance(f, TTSTextFrame) for f in unten), 1)


class EchoTest(unittest.TestCase):
    GESPIELT = "die hauptstadt von frankreich ist paris eine schöne stadt".split()

    def test_folge_aus_dem_gespielten_ist_echo(self):
        self.assertTrue(ist_echo("Die Hauptstadt von Frankreich", self.GESPIELT))
        self.assertTrue(ist_echo("ist Paris", self.GESPIELT))
        self.assertTrue(ist_echo("Frankreichs Hauptstadt", ["frankreichs", "hauptstadt"] + self.GESPIELT[:0]))

    def test_ueberhoerte_woerter_stoeren_nicht(self):
        self.assertTrue(ist_echo("Hauptstadt Frankreich", self.GESPIELT), "ein überhörtes Wort dazwischen")
        self.assertFalse(ist_echo("Hauptstadt Paris", self.GESPIELT), "drei Wörter dazwischen sind keine Folge")

    def test_woerter_in_anderer_reihenfolge_sind_kein_echo(self):
        self.assertFalse(ist_echo("Paris ist Hauptstadt", self.GESPIELT))
        self.assertFalse(ist_echo("Stadt schöne eine", self.GESPIELT))

    def test_bruchstueck_am_ende(self):
        self.assertTrue(ist_echo("Die Haupt", self.GESPIELT))
        self.assertFalse(ist_echo("Haupt Die", self.GESPIELT), "ein Anfang gilt nur am Ende")

    def test_echte_kurze_einwuerfe_bleiben_einwuerfe(self):
        gespielt = "wer war das was ist passiert das stimmt nicht ich verstehe das nicht montag oder dienstag 15".split()
        for text in ("Was?", "Wie?", "Wer?", "Das stimmt nicht", "Ich verstehe das nicht", "Nicht", "Warum", "Stopp",
                     "Doch", "Warte kurz", "Nein, Paris", "Wie bitte"):
            self.assertEqual(echo_urteil(text, gespielt), "einwurf", text)

    def test_einzelnes_gespieltes_wort_ist_unklar_nicht_echo(self):
        gespielt = "soll ich montag oder dienstag buchen".split()
        self.assertEqual(echo_urteil("Montag", gespielt), "unklar")
        self.assertEqual(echo_urteil("Mon", gespielt), "unklar", "Bruchstück der Live-Erkennung")
        self.assertEqual(echo_urteil("Freitag", gespielt), "einwurf")

    LANG = (
        "eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn vierzehn fünfzehn sechzehn siebzehn"
    ).split()

    def test_langes_echo_mit_verhoerern_und_ziffern_ist_echo(self):
        self.assertTrue(ist_echo("1 2 3 4 5 sechs sieben acht neun 10 elf", self.LANG[:2], self.LANG))
        text = "Die Hauptstadt von Frankreich ist Paris und die schöne Stadt liegt an der Seine"
        gespielt = "die hauptstadt von frankreich ist paris und die schöne stadt liegt an der seine".split()
        verhoert = text.replace("Frankreich", "Frankreisch").replace("schöne", "Schöner").replace("liegt", "liegen")
        self.assertTrue(ist_echo(verhoert, [], gespielt), "ein Fünftel Verhörer stört nicht")

    def test_langer_echter_einwurf_ist_kein_echo(self):
        gespielt = "die hauptstadt von frankreich ist paris und die schöne stadt liegt an der seine".split()
        text = "Ja aber ich wollte eigentlich wissen wie das mit dem Preis für die Reise nach Paris ist"
        self.assertFalse(ist_echo(text, [], gespielt))

    def test_langes_echo_braucht_das_lange_fenster(self):
        self.assertFalse(ist_echo("Eins zwei drei vier fünf sechs", self.LANG[:3], []), "ohne Text im langen Fenster nichts")
        self.assertFalse(ist_echo("Eins zwei drei vier fünf sechs", [], []))

    def test_kurzer_einwurf_gilt_nur_gegen_das_kurze_fenster(self):
        lang = "ich weiß nicht ob das stimmt".split()
        self.assertFalse(ist_echo("ich weiß", [], lang), "älter als das kurze Fenster: eine echte Antwort")
        self.assertTrue(ist_echo("ich weiß", lang, lang))

    def test_bruchstueck_am_ende_wird_ignoriert(self):
        gespielt = "gern ich zähle wieder langsam von eins an".split()
        self.assertEqual(echo_urteil("Gern, ich z", gespielt), "echo")
        self.assertEqual(echo_urteil("Im Mittelalter w", "im mittelalter wurde die stadt groß".split()), "echo")
        self.assertEqual(echo_urteil("Gern, du z", gespielt), "einwurf")

    def test_wortanfang_allein_ist_unklar(self):
        self.assertEqual(echo_urteil("En", "die entstehung der stadt".split()), "unklar")
        self.assertEqual(echo_urteil("Ke", "ein keltischer stamm".split()), "unklar")

    def test_einzelne_zahl_waehrend_wamo_zahlen_spricht_ist_echo(self):
        self.assertEqual(echo_urteil("10.", "neun zehn elf zwölf".split()), "echo")
        self.assertEqual(echo_urteil("10.", "die hauptstadt von frankreich".split()), "einwurf")

    def test_kurze_woerter_passen_nur_genau(self):
        self.assertEqual(echo_urteil("Wer sagt", ["werden", "sagen"]), "einwurf")
        self.assertEqual(echo_urteil("Darum geht", ["warum", "geht"]), "einwurf")

    def test_ziffern_passen_zu_gespielten_zahlwoertern(self):
        gespielt = "eins zwei dreizehn vierzehn fünfzehn".split()
        self.assertTrue(ist_echo("13 14", gespielt))
        self.assertTrue(ist_echo("Eins, 2", gespielt))
        self.assertFalse(ist_echo("13 14", "paris stadt und".split()), "ohne gespieltes Zahlwort sind Ziffern ein Einwurf")

    def test_rueckmeldelaute_und_leeres_sind_kein_echo(self):
        self.assertEqual(echo_urteil("mhm", self.GESPIELT), "einwurf")
        self.assertEqual(echo_urteil("", self.GESPIELT), "einwurf")
        self.assertEqual(echo_urteil("Paris Stadt", []), "einwurf", "nichts gespielt, nichts zu vergleichen")

    def test_schalter(self):
        self.assertTrue(echo_filter_an("an"))
        self.assertTrue(echo_filter_an(""))
        self.assertFalse(echo_filter_an("aus"))
        self.assertFalse(echo_filter_an("0"))
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"WAMO_ECHO_FILTER": "aus"}):
            self.assertFalse(echo_filter_an())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WAMO_ECHO_FILTER", None)
            self.assertTrue(echo_filter_an())

    def test_gate_merkt_die_gespielten_woerter_in_reihenfolge_je_fenster(self):
        uhr = [100.0]
        gate = SoftInterruptGate(uhr=lambda: uhr[0])
        gate.on_word_played("Die")
        uhr[0] += 3
        gate.on_word_played("Hauptstadt,")
        self.assertEqual(gate.kuerzlich_gesprochen(), ["die", "hauptstadt"])
        uhr[0] += 4
        self.assertEqual(gate.kuerzlich_gesprochen(6.0), ["hauptstadt"], "nach dem kurzen Fenster zählt ein Wort dort nicht mehr")
        self.assertEqual(gate.kuerzlich_gesprochen(30.0), ["die", "hauptstadt"], "im langen Fenster noch")

    def test_ganze_saetze_vom_haus_tts_werden_in_woerter_zerlegt(self):
        gate = SoftInterruptGate()
        gate.on_word_played("Die Hauptstadt von Frankreich ist Paris.")
        self.assertEqual(gate.kuerzlich_gesprochen(), ["die", "hauptstadt", "von", "frankreich", "ist", "paris"])

    def test_abgeschnittene_woerter_zaehlen_nicht(self):
        gate = SoftInterruptGate()
        gate._firing = True
        self.assertTrue(gate.on_word_played("weg"))
        self.assertEqual(gate.kuerzlich_gesprochen(), [])


class EchoStrategieTest(unittest.IsolatedAsyncioTestCase):
    def strategie(self, gespielt):
        s = MeaningfulWordsUserTurnStartStrategy(min_words=1, enable_interruptions=False, gesprochen=lambda sekunden: gespielt)
        s._bot_speaking = True
        self.ausgeloest = []
        self.zurueck = []

        async def start():
            self.ausgeloest.append(True)

        async def reset():
            self.zurueck.append(True)

        s.trigger_user_turn_started = start
        s.trigger_reset_aggregation = reset
        return s

    GESPIELT = "die hauptstadt von frankreich ist paris".split()

    async def test_echo_loest_keinen_einwurf_aus(self):
        s = self.strategie(self.GESPIELT)
        ergebnis = await s.process_frame(InterimTranscriptionFrame("Die Hauptstadt von", "u", "t"))
        self.assertEqual(self.ausgeloest, [])
        self.assertEqual(len(self.zurueck), 1)
        self.assertEqual(s.echo_verworfen, 1)
        self.assertEqual(ergebnis.name, "CONTINUE")

    async def test_echter_einwurf_loest_aus(self):
        s = self.strategie(self.GESPIELT)
        ergebnis = await s.process_frame(TranscriptionFrame("Stopp, Spanien", "u", "t"))
        self.assertEqual(self.ausgeloest, [True])
        self.assertEqual(ergebnis.name, "STOP")
        self.assertEqual(s.echo_verworfen, 0)

    async def test_erstes_echowort_wartet_und_waechst_zur_folge(self):
        s = self.strategie(self.GESPIELT)
        await s.process_frame(InterimTranscriptionFrame("Die", "u", "t"))
        await s.process_frame(InterimTranscriptionFrame("Hauptstadt", "u", "t"))
        self.assertEqual(self.ausgeloest, [], "ein Wort aus WAMOs Text wartet auf den endgültigen Text")
        await s.process_frame(InterimTranscriptionFrame("Hauptstadt von Frankreich", "u", "t"))
        self.assertEqual(self.ausgeloest, [])
        self.assertEqual(s.echo_verworfen, 1)

    async def test_knappe_antwort_mit_einem_gespielten_wort_loest_mit_dem_endgueltigen_text_aus(self):
        s = self.strategie("soll ich montag oder dienstag buchen".split())
        await s.process_frame(InterimTranscriptionFrame("Montag", "u", "t"))
        self.assertEqual(self.ausgeloest, [])
        ergebnis = await s.process_frame(TranscriptionFrame("Montag", "u", "t"))
        self.assertEqual(self.ausgeloest, [True])
        self.assertEqual(ergebnis.name, "STOP")

    async def test_fragewort_loest_sofort_aus(self):
        s = self.strategie("was möchtest du wissen".split())
        await s.process_frame(InterimTranscriptionFrame("Was", "u", "t"))
        self.assertEqual(self.ausgeloest, [True])

    async def test_ohne_filter_wie_bisher(self):
        s = MeaningfulWordsUserTurnStartStrategy(min_words=1, enable_interruptions=False)
        s._bot_speaking = True
        ausgeloest = []

        async def start():
            ausgeloest.append(True)

        s.trigger_user_turn_started = start
        await s.process_frame(InterimTranscriptionFrame("Die Hauptstadt", "u", "t"))
        self.assertEqual(ausgeloest, [True])


class WoerterTest(unittest.TestCase):
    def test_rueckmeldelaute_zaehlen_nicht(self):
        self.assertEqual(meaningful_word_count("mhm ja genau"), 0)
        self.assertEqual(meaningful_word_count("Doch, stopp"), 2)
        self.assertEqual(meaningful_word_count("J"), 0)


if __name__ == "__main__":
    unittest.main()
