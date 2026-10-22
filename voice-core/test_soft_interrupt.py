"""Tests für die weiche Unterbrechung (soft_interrupt.py), vor allem die Zeitgrenze ohne Wort-Zeitstempel."""

import asyncio
import time
import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test

import soft_interrupt
from soft_interrupt import (
    MeaningfulWordsUserTurnStartStrategy,
    PlaybackTracker,
    SoftInterruptGate,
    echo_filter_an,
    echo_urteil,
    ducken_an,
    ist_echo,
    ist_rueckmeldung,
    kuerze_auf_gehoertes,
    meaningful_word_count,
    ohne_letzte_woerter,
    rueckmeldung_an,
    stopp_grenze_an,
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


class RueckmeldungTest(unittest.TestCase):
    """Erweiterte Erkennung kurzer Zustimmung (WAMO_RUECKMELDUNG)."""

    def test_bindestrich_schreibweisen_der_erkennung(self):
        for text in ("Mm-hmm.", "Mm-hm", "Uh-huh", "mmh", "Mhm, mhm.", "Ahja"):
            self.assertEqual(meaningful_word_count(text, True), 0, text)
            self.assertTrue(ist_rueckmeldung(text, True), text)
        self.assertEqual(meaningful_word_count("Mm-hmm.", False), 1, "ohne die Erweiterung zählte der Laut als Wort")

    def test_wendungen_als_ganze_aeusserung(self):
        for text in ("Alles klar.", "Na klar", "Ja, genau.", "Ach so, ja", "Ich verstehe.", "Sehr gut", "Ganz genau"):
            self.assertEqual(meaningful_word_count(text, True), 0, text)
        self.assertTrue(ist_rueckmeldung("Alles klar.", True))

    def test_einwuerfe_bleiben_einwuerfe(self):
        for text in ("Doch", "Stopp", "Nein", "Alles", "Ja aber", "Genau, und die Kosten?", "Warte mal", "Na", "Ich"):
            self.assertGreaterEqual(meaningful_word_count(text, True), 1, text)
            self.assertFalse(ist_rueckmeldung(text, True), text)

    def test_wortanfang_eines_rueckmeldelauts_im_zwischenstand_zaehlt_noch_nicht(self):
        for anfang in (" Gen", "Mh", "Ric"):
            self.assertEqual(meaningful_word_count(anfang, True, interim=True), 0, anfang)
            self.assertEqual(meaningful_word_count(anfang, True, interim=False), 1, "der endgültige Text zählt")
        self.assertEqual(meaningful_word_count("Stopp, Ge", True, interim=True), 1, "\"Stopp\" zählt, der Anfang noch nicht")
        self.assertEqual(meaningful_word_count("Gestern", True, interim=True), 1, "kein Anfang eines Rückmeldelauts")
        self.assertEqual(meaningful_word_count(" Gen", False, interim=True), 1, "ohne die Erweiterung wie früher")

    def test_unentschiedenes_ist_keine_rueckmeldung(self):
        self.assertFalse(ist_rueckmeldung("", True))
        self.assertFalse(ist_rueckmeldung("J", True), "Bruchstück der Live-Erkennung")
        self.assertFalse(ist_rueckmeldung("  ...  ", True))

    def test_schalter(self):
        self.assertTrue(rueckmeldung_an("an"))
        self.assertTrue(rueckmeldung_an(""))
        self.assertFalse(rueckmeldung_an("aus"))
        self.assertFalse(rueckmeldung_an("0"))


class RueckmeldungStrategieTest(unittest.IsolatedAsyncioTestCase):
    async def beurteilung(self, text):
        bekannt = []
        s = MeaningfulWordsUserTurnStartStrategy(min_words=1, enable_interruptions=False, beurteilt=bekannt.append)
        s._bot_speaking = True
        ausgeloest = []

        async def start():
            ausgeloest.append(True)

        async def reset():
            pass

        s.trigger_user_turn_started = start
        s.trigger_reset_aggregation = reset
        await s.process_frame(InterimTranscriptionFrame(text, "u", "t"))
        return ausgeloest, bekannt

    async def test_rueckmeldung_wird_gemeldet_und_loest_nichts_aus(self):
        ausgeloest, bekannt = await self.beurteilung("Mhm")
        self.assertEqual(ausgeloest, [])
        self.assertEqual(bekannt, ["rueckmeldung"])

    async def test_einwurf_wird_nicht_als_rueckmeldung_gemeldet(self):
        ausgeloest, bekannt = await self.beurteilung("Stopp")
        self.assertEqual(ausgeloest, [True])
        self.assertEqual(bekannt, [])

    async def test_bruchstueck_ist_noch_unentschieden(self):
        ausgeloest, bekannt = await self.beurteilung("J")
        self.assertEqual((ausgeloest, bekannt), ([], []))


class _GateTest(unittest.IsolatedAsyncioTestCase):
    """Gate ohne Pipeline: eigene Uhr, gesendete Frames werden gesammelt, Aufgaben laufen in der Schleife."""

    def gate(self, **kwargs):
        self.t = 0.0
        g = SoftInterruptGate(uhr=lambda: self.t, max_wait_secs=0, **kwargs)
        self.gesendet = []

        async def push(frame, direction=None):
            self.gesendet.append(frame)

        g.push_frame = push
        g.create_task = lambda coro, name=None: asyncio.ensure_future(coro)
        g._bot_speaking = True
        return g

    def worte(self, g, *zeitwort):
        for t, wort in zeitwort:
            self.t = t
            g.on_word_played(wort)


class GrenzeTest(_GateTest):
    def test_fruehestes_erreichbares_wort_ist_die_grenze(self):
        g = self.gate(grenze=True, playback_lag_secs=0.5)
        self.worte(g, (0.0, "Das"), (0.5, "ist"), (0.9, "gut"))
        self.t = 0.95  # Grenzen vor 0,95 - 0,5 + 0,19 = 0,64 sind nicht mehr zu erreichen
        self.assertEqual(g._naechste_grenze(self.t), (0.9, False))

    def test_zu_knappe_grenzen_fallen_weg(self):
        g = self.gate(grenze=True, playback_lag_secs=0.5)
        self.worte(g, (0.0, "Das"), (0.5, "ist"))
        self.assertIsNone(g._naechste_grenze(1.0), "beide Grenzen sind zu spät zu erreichen: das nächste Wort bringt eine")

    def test_satzende_kurz_danach_geht_vor(self):
        g = self.gate(grenze=True, playback_lag_secs=0.5)
        self.worte(g, (0.1, "Das"), (0.2, "ist."), (0.3, "Gut"), (0.45, "so"))
        self.assertEqual(g._naechste_grenze(0.35), (0.3, True), "Grenze nach dem Satzende, 0,2 s nach der frühesten (0,1)")

    def test_satzende_zu_spaet_geht_nicht_vor(self):
        g = self.gate(grenze=True, playback_lag_secs=0.5)
        self.worte(g, (0.0, "Das"), (0.2, "ist"), (0.3, "gut"), (0.6, "und"), (0.9, "so."), (1.0, "Dann"))
        self.assertEqual(g._naechste_grenze(0.35), (0.2, False))

    async def test_arm_nimmt_eine_schon_gemeldete_grenze_und_stoppt_sofort_dann_serverseitig(self):
        g = self.gate(grenze=True, playback_lag_secs=0.3)  # erreichbar: Grenzen ab 0,95 - 0,3 + 0,19 = 0,84
        self.worte(g, (0.0, "Das"), (0.5, "ist"), (0.9, "gut"))
        self.t = 0.95
        ziele = []

        async def warte(tracker_zeit):
            ziele.append(tracker_zeit)

        g._warte_bis = warte
        g._arm()
        self.assertTrue(g._firing)
        self.assertEqual(g.ungehoerte_woerter(), 1, "\"gut\" war gemeldet, liegt aber hinter der Grenze")
        self.assertEqual(g.ungehoerte_woerter(), 0, "einmal abholen")
        await asyncio.wait_for(g._fire_task, 2)
        arten = [type(f).__name__ for f in self.gesendet]
        self.assertEqual(arten, ["RTVIServerMessageFrame", "InterruptionFrame"])
        self.assertEqual(self.gesendet[0].data, {"type": "leiser_stopp", "sofort": True})
        ton = 0.9 + 0.3  # Grenze plus Vorsprung der Zeitstempel vor dem Ton
        self.assertAlmostEqual(ziele[0], ton - soft_interrupt.STOPP_FADE_SECS - soft_interrupt.STOPP_VORLAUF_SECS)
        self.assertAlmostEqual(ziele[1], ton, msg="der Server bricht erst ab, wenn die Person die Grenze hört")

    async def test_ohne_gemeldete_grenze_bringt_das_naechste_wort_sie_und_wird_abgeschnitten(self):
        g = self.gate(grenze=True, playback_lag_secs=0.2)
        self.worte(g, (0.0, "Das"), (0.5, "ist"))
        self.t = 1.0
        g._arm()
        self.assertTrue(g._pending and not g._firing)
        self.t = 1.2
        self.assertTrue(g.on_word_played("gut"), "das Wort liegt hinter der Grenze und gilt als nicht gesprochen")
        self.assertTrue(g._firing)
        self.assertEqual(g.ungehoerte_woerter(), 0)
        await asyncio.wait_for(g._fire_task, 2)
        self.assertEqual([type(f).__name__ for f in self.gesendet][-1], "InterruptionFrame")

    def test_alter_modus_unveraendert(self):
        g = self.gate(grenze=False)
        g._pending, g._words_left = True, 1
        self.assertFalse(g.on_word_played("Das"))
        self.assertEqual(len(g._grenzen), 0)

    def test_alter_modus_haelt_am_satzende(self):
        g = self.gate(grenze=False)
        gruende = []
        g._start_fire = lambda grund, **_: gruende.append(grund)
        g._pending, g._words_left = True, 3
        self.assertFalse(g.on_word_played("ist."))
        self.assertTrue(g.on_word_played("Gut"), "das Wort nach dem Satzende wird abgeschnitten")
        self.assertEqual(gruende, ["Satzende"])

    def test_nicht_abgeholte_zahl_geht_nicht_in_die_naechste_antwort(self):
        g = self.gate(grenze=True, playback_lag_secs=0.3)
        self.worte(g, (0.0, "Das"), (0.9, "gut"))
        self.t = 0.95
        g._arm()
        self.assertEqual(g._ungehoert, 1)
        g._firing = False  # der Stopp ist durch, die Antwort stand schon im Verlauf: nichts wurde abgeholt
        g.antwort_beginnt()
        self.assertEqual(g.ungehoerte_woerter(), 0, "ein neuer Sprachabschnitt beginnt: die alte Zahl gilt nicht mehr")
        g._fire_task.cancel()

    def test_woerter_ausserhalb_des_verlaufs_zaehlen_nicht_als_ungehoert(self):
        g = self.gate(grenze=True, playback_lag_secs=0.3)
        g.on_word_played("Moment", False)  # Wartesatz: nicht im Verlauf
        self.t = 0.5
        g.on_word_played("schau", False)
        self.t = 0.9
        g.on_word_played("gut")
        self.assertEqual(g.kuerzlich_gesprochen(10), ["moment", "schau", "gut"], "der Echo-Filter kennt alle")
        self.t = 0.95
        g._arm()
        self.assertEqual(g.ungehoerte_woerter(), 1, "nur das Wort aus dem Verlauf")
        g._fire_task.cancel()


class VerlaufKuerzenTest(unittest.TestCase):
    def test_ohne_letzte_woerter(self):
        self.assertEqual(ohne_letzte_woerter("Das ist ein langer Satz.", 2), "Das ist ein")
        self.assertEqual(ohne_letzte_woerter("Das ist", 0), "Das ist")
        self.assertEqual(ohne_letzte_woerter("Das ist", 5), "")
        self.assertEqual(ohne_letzte_woerter("Ja, genau, also", 1), "Ja, genau,")

    def test_kuerzt_die_letzte_antwort_im_verlauf(self):
        ctx = LLMContext(messages=[{"role": "user", "content": "Hallo"}, {"role": "assistant", "content": "● Das ist ein Test"}])
        kuerze_auf_gehoertes(ctx, 2)
        self.assertEqual(ctx.messages[-1]["content"], "● Das ist")

    def test_leere_antwort_wird_entfernt(self):
        ctx = LLMContext(messages=[{"role": "user", "content": "Hallo"}, {"role": "assistant", "content": "● Das ist"}])
        kuerze_auf_gehoertes(ctx, 2)
        self.assertEqual([m["role"] for m in ctx.messages], ["user"])

    def test_fasst_nur_die_letzte_antwort_an(self):
        ctx = LLMContext(messages=[{"role": "assistant", "content": "● Eins zwei"}, {"role": "user", "content": "Hm"}])
        self.assertFalse(kuerze_auf_gehoertes(ctx, 1), "die letzte Nachricht ist keine Antwort: nichts verändert")
        self.assertEqual(ctx.messages[0]["content"], "● Eins zwei")

    def test_rueckgabewert_sagt_ob_gekuerzt_wurde(self):
        ctx = LLMContext(messages=[{"role": "assistant", "content": "● Eins zwei drei"}])
        self.assertTrue(kuerze_auf_gehoertes(ctx, 1))
        blocks = LLMContext(messages=[{"role": "assistant", "content": [{"type": "text", "text": "Eins zwei"}]}])
        self.assertFalse(kuerze_auf_gehoertes(blocks, 1), "Inhalt aus Blöcken wird nicht angefasst")


class DuckenTest(_GateTest):
    """Vorsorgliches Dämpfen: leiser, sobald die Person spricht; zurück bei Rückmeldung, Echo oder Sprachende."""

    def setUp(self):
        self._alt = (soft_interrupt.DUCK_VERZOEGERUNG_SECS, soft_interrupt.DUCK_NACH_VAD_ENDE_SECS, soft_interrupt.DUCK_MAX_SECS,
                     soft_interrupt.DUCK_BEURTEILT_SECS)
        soft_interrupt.DUCK_VERZOEGERUNG_SECS = 0.02
        soft_interrupt.DUCK_NACH_VAD_ENDE_SECS = 0.05
        soft_interrupt.DUCK_MAX_SECS = 0.3

    def tearDown(self):
        (soft_interrupt.DUCK_VERZOEGERUNG_SECS, soft_interrupt.DUCK_NACH_VAD_ENDE_SECS, soft_interrupt.DUCK_MAX_SECS,
         soft_interrupt.DUCK_BEURTEILT_SECS) = self._alt

    def nachrichten(self):
        return [f.data for f in self.gesendet if isinstance(f, RTVIServerMessageFrame)]

    async def test_sprache_daempft_und_das_ende_der_sprache_nimmt_zurueck(self):
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.08)
        self.assertEqual(self.nachrichten(), [{"type": "stimme_leiser", "an": True}])
        g._vad_spricht = False
        g._frist("_duck_ende_task", 0.03, "Sprache zu Ende", 0.0)
        await asyncio.sleep(0.1)
        self.assertEqual(self.nachrichten()[-1], {"type": "stimme_leiser", "an": False})

    async def test_pause_in_der_rede_nimmt_die_daempfung_nicht_zurueck(self):
        soft_interrupt.DUCK_MAX_SECS = 1.0
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.06)
        g._vad_spricht = False
        g._frist("_duck_ende_task", 0.1, "Sprache zu Ende", 0.0)  # Sprachende gemeldet ...
        await asyncio.sleep(0.03)
        g._vad_spricht = True
        g._fristen_abbrechen("_duck_ende_task")  # ... aber die Person redet weiter
        await asyncio.sleep(0.2)
        self.assertEqual([m["an"] for m in self.nachrichten()], [True])

    async def test_rueckmeldung_nimmt_zurueck_und_sperrt_das_erneute_daempfen(self):
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.06)
        soft_interrupt.DUCK_BEURTEILT_SECS = 0.02
        g.beurteilt("rueckmeldung")
        await asyncio.sleep(0.08)
        self.assertEqual([m["an"] for m in self.nachrichten()], [True, False])
        g._duck_planen()
        await asyncio.sleep(0.06)
        self.assertEqual(len(self.nachrichten()), 2, "in der Sperrzeit wird nicht erneut gedämpft")

    async def test_rueckmeldung_vor_dem_daempfen_verhindert_es(self):
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        g.beurteilt("echo")
        await asyncio.sleep(0.08)
        self.assertEqual(self.nachrichten(), [])

    async def test_ohne_bestaetigung_endet_das_daempfen_nach_der_hoechstzeit(self):
        g = self.gate(ducken=True)
        g._vad_spricht = True  # Echo hält die Sprachaktivität dauerhaft an
        g._duck_planen()
        await asyncio.sleep(0.5)
        self.assertEqual([m["an"] for m in self.nachrichten()], [True, False])

    async def test_bestaetigter_einwurf_bleibt_gedaempft_bis_zum_stopp(self):
        g = self.gate(ducken=True, playback_lag_secs=0.05)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.06)
        g._arm()  # die Spracherkennung hat den Einwurf bestätigt
        await asyncio.sleep(0.4)
        self.assertEqual([m["an"] for m in self.nachrichten() if "an" in m], [True], "keine Entwarnung vor dem Stopp")

    async def test_frames_daempfen_und_geben_die_vad_frames_weiter(self):
        gate = SoftInterruptGate(ducken=True, max_wait_secs=0)
        unten, _ = await run_test(
            gate,
            frames_to_send=[
                BotStartedSpeakingFrame(), SleepFrame(0.05), VADUserStartedSpeakingFrame(start_secs=0.2),
                SleepFrame(0.1), VADUserStoppedSpeakingFrame(stop_secs=0.15), SleepFrame(0.3),
            ],
        )
        nachrichten = [f.data for f in unten if isinstance(f, RTVIServerMessageFrame)]
        self.assertEqual([m["an"] for m in nachrichten], [True, False])
        self.assertTrue(any(isinstance(f, VADUserStartedSpeakingFrame) for f in unten), "VAD-Frames gehen weiter")
        self.assertTrue(any(isinstance(f, VADUserStoppedSpeakingFrame) for f in unten))

    async def test_ende_der_antwort_nimmt_die_daempfung_zurueck(self):
        gate = SoftInterruptGate(ducken=True, max_wait_secs=0)
        unten, _ = await run_test(
            gate,
            frames_to_send=[
                BotStartedSpeakingFrame(), SleepFrame(0.05), VADUserStartedSpeakingFrame(start_secs=0.2), SleepFrame(0.1),
                BotStoppedSpeakingFrame(), SleepFrame(0.1),
            ],
        )
        nachrichten = [f.data for f in unten if isinstance(f, RTVIServerMessageFrame)]
        self.assertEqual([m["an"] for m in nachrichten], [True, False])

    async def test_bestaetigung_innerhalb_der_beurteilungsfrist_nimmt_nicht_zurueck(self):
        soft_interrupt.DUCK_BEURTEILT_SECS = 0.15
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.06)
        g.beurteilt("rueckmeldung")  # "Ja" ...
        await asyncio.sleep(0.03)
        g._arm()  # ... "aber": der Einwurf ist bestätigt
        await asyncio.sleep(0.3)
        self.assertEqual([m["an"] for m in self.nachrichten() if "an" in m], [True], "kein Lauter zwischen Leiser und Stopp")

    async def test_rueckmeldung_nach_der_beurteilungsfrist_nimmt_zurueck(self):
        soft_interrupt.DUCK_BEURTEILT_SECS = 0.05
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.06)
        g.beurteilt("rueckmeldung")
        await asyncio.sleep(0.15)
        self.assertEqual([m["an"] for m in self.nachrichten()], [True, False])

    async def test_aus_daempft_nie(self):
        g = self.gate(ducken=False)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.08)
        self.assertEqual(self.nachrichten(), [])

    def test_schalter(self):
        self.assertTrue(ducken_an("an"))
        self.assertFalse(ducken_an("aus"))
        self.assertTrue(stopp_grenze_an(""))
        self.assertFalse(stopp_grenze_an("0"))


if __name__ == "__main__":
    unittest.main()
