"""Tests für das Innehalten statt Abbrechen (soft_interrupt.py mit wiedergabe.py).

Ein einzelnes echtes Wort der Person, während WAMO spricht ("Danke", "Du"), lässt WAMO an der nächsten Wortgrenze innehalten. Kommt
nach WEITER Sekunden Stille nichts nach (höchstens MAX Sekunden Pause), spricht WAMO an derselben Stelle weiter. Zwei Wörter oder ein
Stoppwort brechen ab wie bisher, Rückmeldelaute ("mhm") unterbrechen nie. Während eines Halts gibt es weder leiser_stopp noch eine
Dämpfung, die ausblendet; nach dem Weitersprechen gilt wieder alles wie vorher.
"""

import asyncio
import contextlib
import time
import unittest
from unittest import mock

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

import soft_interrupt
from soft_interrupt import (
    HALTEN,
    SCHNEIDEN,
    SPIELEN,
    MeaningfulWordsUserTurnStartStrategy,
    PlaybackTracker,
    SoftInterruptGate,
    hat_stoppwort,
)
from wiedergabe import AusgabeHalt


def interim(text):
    return InterimTranscriptionFrame(text=text, user_id="u", timestamp="")


def wort(text, pts):
    frame = TTSTextFrame(text=text, aggregated_by="word")
    frame.pts = pts
    return frame


class _HaltTest(unittest.IsolatedAsyncioTestCase):
    """Gate mit echtem AusgabeHalt, eigener Uhr und gesammelten Frames. Die Zeitgeber ruft der Test selbst auf (check_timers)."""

    def setUp(self):
        self._alt = (soft_interrupt.DUCK_VERZOEGERUNG_SECS, soft_interrupt.DUCK_NACH_VAD_ENDE_SECS, soft_interrupt.DUCK_MAX_SECS)
        soft_interrupt.DUCK_VERZOEGERUNG_SECS = 0.02
        soft_interrupt.DUCK_NACH_VAD_ENDE_SECS = 0.05
        soft_interrupt.DUCK_MAX_SECS = 5.0

    def tearDown(self):
        (soft_interrupt.DUCK_VERZOEGERUNG_SECS, soft_interrupt.DUCK_NACH_VAD_ENDE_SECS, soft_interrupt.DUCK_MAX_SECS) = self._alt

    def gate(self, **kwargs):
        self.t = 0.0
        self.halt = AusgabeHalt()
        kwargs.setdefault("halt", self.halt)
        kwargs.setdefault("grenze", True)  # wie im Betrieb (WAMO_STOPP_GRENZE, Standard an)
        g = SoftInterruptGate(
            uhr=lambda: self.t, max_wait_secs=0, weiter_secs=0.7, max_pause_secs=2.5, grenzwarte_secs=0.4, **kwargs
        )
        self.gesendet = []

        async def senden(frame, richtung=None):
            self.gesendet.append(frame)

        g.push_frame = senden
        g.create_task = lambda coro, name=None: asyncio.ensure_future(coro)
        g._ensure_watch = lambda: None
        g._bot_speaking = True
        return g

    def arten(self):
        return [type(f).__name__ for f in self.gesendet]

    def nachrichten(self):
        return [f.data for f in self.gesendet if isinstance(f, RTVIServerMessageFrame)]

    def anhalten_auf_ansatz(self, g, text=" Danke"):
        self.assertEqual(g.wort_beginnt("Ich"), SPIELEN)
        g.ansatz(text)
        self.assertFalse(self.halt.haelt, "erst an der nächsten Wortgrenze")
        self.assertEqual(g.wort_beginnt("lege"), HALTEN)
        self.assertTrue(self.halt.haelt)


class InnehaltenGateTest(_HaltTest):
    def test_ein_wort_haelt_an_der_wortgrenze_inne(self):
        g = self.gate()
        self.anhalten_auf_ansatz(g)
        self.assertEqual(g.wort_beginnt("lege"), HALTEN, "das Wort wartet, solange WAMO innehält")
        self.assertTrue(g.haelt_inne)

    def test_kommt_nichts_nach_spricht_wamo_dort_weiter(self):
        g = self.gate()
        self.anhalten_auf_ansatz(g)
        self.t = 0.5
        self.assertTrue(g.check_timers())
        self.assertTrue(self.halt.haelt)
        self.t = 0.8
        self.assertFalse(g.check_timers())
        self.assertFalse(self.halt.haelt)
        self.assertEqual(g.wort_beginnt("lege"), SPIELEN, "dasselbe Wort, jetzt hörbar")
        g.ansatz(" Danke.")  # derselbe Ansatz noch einmal: kein zweites Innehalten
        self.assertEqual(g.wort_beginnt("das"), SPIELEN)
        self.assertFalse(self.halt.haelt)

    def test_spricht_die_person_noch_wartet_wamo_hoechstens_die_maximale_pause(self):
        g = self.gate()
        self.anhalten_auf_ansatz(g)
        g._vad_spricht = True
        self.t = 1.5
        self.assertTrue(g.check_timers())
        self.assertTrue(self.halt.haelt)
        self.t = 2.6
        self.assertFalse(g.check_timers())
        self.assertFalse(self.halt.haelt)

    def test_ein_weiteres_zeichen_der_erkennung_verschiebt_das_weitersprechen(self):
        g = self.gate()
        self.anhalten_auf_ansatz(g)
        self.t = 0.5
        g.ansatz(" Dankeschön")
        self.t = 1.0
        self.assertTrue(g.check_timers(), "erst 0,5 s seit dem letzten Zeichen")
        self.t = 1.3
        self.assertFalse(g.check_timers())
        self.assertFalse(self.halt.haelt)

    def test_das_ende_der_sprache_zaehlt_als_beginn_der_stille(self):
        g = self.gate()
        self.anhalten_auf_ansatz(g)
        g._vad_spricht = True
        self.t = 1.0
        g._vad_spricht = False
        g._user_quiet_since = self.t  # was process_frame beim Ende der Sprache setzt
        self.t = 1.5
        self.assertTrue(g.check_timers(), "0,5 s Stille nach dem Ende der Sprache")
        self.t = 1.8
        self.assertFalse(g.check_timers())

    def test_ohne_wortgrenze_haelt_sie_trotzdem_inne(self):
        g = self.gate()
        g.ansatz(" Danke")  # zum Beispiel zwischen zwei Sätzen, kein Wort in Sicht
        self.t = 0.45
        self.assertTrue(g.check_timers())
        self.assertTrue(self.halt.haelt)

    def test_ohne_sprechen_kein_innehalten(self):
        g = self.gate()
        g._bot_speaking = False
        g.ansatz(" Danke")
        self.assertEqual(g.wort_beginnt("Ich"), SPIELEN)
        self.assertFalse(self.halt.haelt)

    def test_ohne_halt_gibt_es_kein_innehalten_und_nichts_wird_gehalten(self):
        g = self.gate(halt=None)
        g.ansatz(" Danke")
        for text in ("Ich", "lege", "das"):
            self.assertEqual(g.wort_beginnt(text), SPIELEN)
        self.assertFalse(g.haelt_inne)
        self.assertFalse(g.check_timers())

    async def test_zweites_wort_waehrend_des_halts_bricht_sofort_ab_ohne_ausblenden(self):
        g = self.gate(playback_lag_secs=5.0)
        self.anhalten_auf_ansatz(g)
        g._arm()  # die Erkennung bestätigt den Einwurf
        self.assertTrue(g._firing)
        await asyncio.wait_for(g._fire_task, 0.5)  # keine Wartezeit für den Vorsprung der Zeitstempel
        self.assertEqual(self.arten(), ["InterruptionFrame"], "der Ton steht schon: kein leiser_stopp")
        self.assertTrue(self.halt.haelt, "der Abbruch (Tracker) verwirft den Rest und öffnet den Ausgang")

    async def test_einwurf_ohne_ansatz_stoppt_exakt_an_der_wortgrenze(self):
        g = self.gate(playback_lag_secs=5.0, grenze=True)
        g.wort_beginnt("Ich")
        g._arm()
        self.assertTrue(g._pending and not g._firing)
        self.assertEqual(g.wort_beginnt("lege"), SCHNEIDEN, "das Wort hinter der Grenze gilt als nicht gesprochen")
        self.assertTrue(self.halt.haelt, "nichts mehr hinaus, bis der Abbruch den Rest verwirft")
        await asyncio.wait_for(g._fire_task, 0.5)
        self.assertEqual(self.arten(), ["InterruptionFrame"])
        self.assertEqual(g.ungehoerte_woerter(), 0, "der Tracker meldet nur, was hinausging: nichts zu kürzen")

    async def test_ohne_stoppgrenze_gelten_satzende_und_wortlimit_wie_bisher(self):
        g = self.gate(grenze=False, max_words=1)
        g.wort_beginnt("Das")
        g._arm()
        self.assertEqual(g.wort_beginnt("ist."), SPIELEN, "noch ein Wort erlaubt")
        self.assertEqual(g.wort_beginnt("Gut"), SCHNEIDEN, "hinter dem Satzende ist Schluss")
        self.assertTrue(self.halt.haelt)
        await asyncio.wait_for(g._fire_task, 0.5)
        self.assertEqual(self.arten(), ["InterruptionFrame"])

    async def test_bestaetigter_einwurf_ohne_wortgrenze_stoppt_nach_der_wartezeit(self):
        g = self.gate()
        g.wort_beginnt("Ich")
        g._arm()
        self.t = 0.3
        self.assertTrue(g.check_timers())
        self.assertFalse(self.halt.haelt)
        self.t = 0.45
        self.assertFalse(g.check_timers())
        self.assertTrue(self.halt.haelt)
        await asyncio.wait_for(g._fire_task, 0.5)
        self.assertEqual(self.arten(), ["InterruptionFrame"])

    async def test_ohne_halt_bleibt_der_stopp_wie_bisher(self):
        g = self.gate(halt=None, playback_lag_secs=0.01)
        g._arm()
        g._start_fire("Test")
        await asyncio.wait_for(g._fire_task, 0.5)
        self.assertEqual(self.arten(), ["RTVIServerMessageFrame", "InterruptionFrame"])
        self.assertEqual(self.nachrichten(), [{"type": "leiser_stopp"}])

    async def test_kein_leiser_stopp_waehrend_des_halts_und_danach(self):
        g = self.gate()
        self.anhalten_auf_ansatz(g)
        self.t = 0.8
        g.check_timers()
        await asyncio.sleep(0.02)
        self.assertEqual(self.nachrichten(), [])
        self.assertFalse(self.halt.haelt)

    async def test_nach_dem_weitersprechen_gilt_wieder_alles_wie_vorher(self):
        g = self.gate()
        self.anhalten_auf_ansatz(g)
        self.t = 0.8
        g.check_timers()
        g.wort_beginnt("lege")
        g.ansatz(" Hallo du")  # anderer Ansatz: hält wieder an
        self.assertEqual(g.wort_beginnt("das"), HALTEN)
        self.halt.fortsetzen()
        g._clear_ansatz(fortsetzen=True)
        g.wort_beginnt("das")
        g._arm()  # jetzt ein echter Einwurf
        self.assertEqual(g.wort_beginnt("Repository"), SCHNEIDEN)
        await asyncio.wait_for(g._fire_task, 0.5)
        self.assertEqual(self.arten(), ["InterruptionFrame"])

    async def test_je_sitzung_ein_halt(self):
        a = self.gate()
        halt_a = self.halt
        b = self.gate()
        halt_b = self.halt
        self.assertIsNot(halt_a, halt_b)
        self.t = 0.0
        a.wort_beginnt("Ich")
        a.ansatz(" Danke")
        self.assertEqual(a.wort_beginnt("lege"), HALTEN)
        self.assertTrue(halt_a.haelt)
        self.assertFalse(halt_b.haelt)
        self.assertEqual(b.wort_beginnt("Ich"), SPIELEN)
        self.assertEqual(b.wort_beginnt("lege"), SPIELEN)
        self.assertFalse(b.haelt_inne)

    async def test_beginn_und_ende_der_antwort_geben_den_halt_frei(self):
        for frame in (BotStartedSpeakingFrame(), BotStoppedSpeakingFrame()):
            g = self.gate()
            self.anhalten_auf_ansatz(g)
            with mock.patch.object(FrameProcessor, "process_frame", new=mock.AsyncMock()):
                await g.process_frame(frame, FrameDirection.DOWNSTREAM)
            self.assertFalse(self.halt.haelt, f"{type(frame).__name__}: nie in eine neue Antwort hinein stumm bleiben")
            self.assertFalse(g.haelt_inne)

    async def test_unterbrechung_von_aussen_loescht_den_ansatz(self):
        g = self.gate()
        self.anhalten_auf_ansatz(g)
        with mock.patch.object(FrameProcessor, "process_frame", new=mock.AsyncMock()):
            await g.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        self.assertFalse(g.haelt_inne)
        self.assertEqual(self.arten(), ["InterruptionFrame"], "die Unterbrechung geht weiter, der Tracker öffnet den Ausgang")


class InnehaltenDaempfenTest(_HaltTest):
    """Dämpfen (WAMO_DUCKEN) und Innehalten: während eines Halts keine Dämpfung, beim Weitersprechen wieder normal."""

    async def test_waehrend_des_halts_wird_nicht_gedaempft(self):
        g = self.gate(ducken=True)
        g._vad_spricht = True
        self.anhalten_auf_ansatz(g)
        g._duck_planen()
        await asyncio.sleep(0.08)
        self.assertEqual(self.nachrichten(), [])

    async def test_ein_schon_geplantes_daempfen_entfaellt_wenn_der_ansatz_kommt(self):
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        g.wort_beginnt("Ich")
        g.ansatz(" Danke")
        await asyncio.sleep(0.08)
        self.assertEqual(self.nachrichten(), [])

    async def test_gedaempfte_stimme_wird_beim_weitersprechen_wieder_normal(self):
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.06)
        self.assertEqual(self.nachrichten(), [{"type": "stimme_leiser", "an": True}])
        self.anhalten_auf_ansatz(g)
        g._vad_spricht = False
        g._user_quiet_since = self.t
        self.t = 0.8
        self.assertFalse(g.check_timers())
        await asyncio.sleep(0.05)
        self.assertEqual(self.nachrichten()[-1], {"type": "stimme_leiser", "an": False})
        self.assertFalse(g._duckt)
        self.assertGreater(g._duck_gesperrt_bis, self.t, "wie nach jeder Entwarnung: eine Weile nicht erneut dämpfen")
        self.assertEqual(self.arten().count("InterruptionFrame"), 0)

    async def test_bestaetigter_einwurf_nach_dem_halt_bleibt_gedaempft_bis_zum_stopp(self):
        g = self.gate(ducken=True)
        g._vad_spricht = True
        g._duck_planen()
        await asyncio.sleep(0.06)
        self.anhalten_auf_ansatz(g)
        g._arm()
        await asyncio.wait_for(g._fire_task, 0.5)
        self.assertEqual([m["an"] for m in self.nachrichten() if "an" in m], [True], "kein Lauter zwischen Leiser und Stopp")


class StrategieInnehaltenTest(unittest.IsolatedAsyncioTestCase):
    """MeaningfulWordsUserTurnStartStrategy mit on_partial: ein einzelnes echtes Wort meldet einen Ansatz."""

    def strategie(self, *, mit_ansatz=True, **kwargs):
        self.angesetzt, self.ausgeloest, self.zurueck = [], [], []
        s = MeaningfulWordsUserTurnStartStrategy(
            min_words=2,
            enable_interruptions=False,
            on_partial=self.angesetzt.append if mit_ansatz else None,
            **kwargs,
        )
        s._bot_speaking = True

        async def start():
            self.ausgeloest.append(True)

        async def zuruecksetzen():
            self.zurueck.append(True)

        s.trigger_user_turn_started = start
        s.trigger_reset_aggregation = zuruecksetzen
        return s

    async def test_ein_echtes_wort_meldet_einen_ansatz_und_unterbricht_nicht(self):
        for text in (" Du", "Hier", " Danke", "Fertig."):
            with self.subTest(text=text):
                s = self.strategie()
                await s.process_frame(interim(text))
                self.assertEqual(self.angesetzt, [text])
                self.assertEqual(self.ausgeloest, [])
                self.assertEqual(self.zurueck, [True], "der Zwischenstand wird verworfen wie bisher")

    async def test_danke_haelt_nur_inne_und_bricht_nicht_ab(self):
        s = self.strategie()
        await s.process_frame(TranscriptionFrame("Danke", "u", "t"))
        self.assertEqual((self.angesetzt, self.ausgeloest), (["Danke"], []))

    async def test_ohne_innehalten_ist_danke_ein_stoppwort_wie_bisher(self):
        s = self.strategie(mit_ansatz=False)
        await s.process_frame(interim("Danke"))
        self.assertEqual((self.angesetzt, self.ausgeloest), ([], [True]))

    async def test_ohne_innehalten_aendert_sich_nichts_fuer_ein_einzelnes_wort(self):
        for text in ("Hier", "Fertig"):  # kein Stoppwort: bei min_words=2 unterbricht ein einzelnes Wort nicht
            with self.subTest(text=text):
                s = self.strategie(mit_ansatz=False)
                await s.process_frame(interim(text))
                self.assertEqual((self.angesetzt, self.ausgeloest, self.zurueck), ([], [], [True]))

    async def test_ohne_innehalten_brechen_abbrechen_nee_und_ruhe_ab(self):
        """Ohne Halt (WAMO_INNEHALTEN=aus, Haus-Sprachausgabe, Ausgang ohne write_audio_frame) ist MIN_INTERRUPT_WORDS
        seit 14.11.2026 2: Die Zusatz-Stoppwörter müssen dann genauso einzeln unterbrechen wie mit Halt."""
        for text in ("Abbrechen", "Nee", "Ruhe", "Abbruch", "Wart"):
            for frame in (interim(text), TranscriptionFrame(text, "u", "t")):
                with self.subTest(text=text, art=type(frame).__name__):
                    s = self.strategie(mit_ansatz=False)
                    await s.process_frame(frame)
                    self.assertEqual((self.angesetzt, self.ausgeloest), ([], [True]))

    async def test_mit_innehalten_brechen_auch_abbrechen_nee_und_ruhe_ab(self):
        for text in ("Abbrechen", "Nee", "Ruhe", "Abbruch"):
            with self.subTest(text=text):
                s = self.strategie()
                await s.process_frame(interim(text))
                self.assertEqual((self.angesetzt, self.ausgeloest), ([], [True]))

    async def test_im_echo_modus_unterbricht_ein_zusatz_stoppwort_weiter(self):
        s = self.strategie(gesprochen=lambda sekunden: ["das", "ist", "ein", "test"], echo_modus=True)
        jetzt = time.monotonic()
        s._echo_zeiten.extend([jetzt, jetzt])
        await s.process_frame(interim("Abbrechen"))
        self.assertEqual(self.ausgeloest, [True])

    async def test_stoppwoerter_unterbrechen_weiter_sofort(self):
        for text in ("Stopp", "Moment", "Warte", "Nein", "Halt", "Was", "Doch", "Bitte", "Hallo", "Abbrechen"):
            with self.subTest(text=text):
                s = self.strategie()
                await s.process_frame(interim(text))
                self.assertEqual((self.angesetzt, self.ausgeloest), ([], [True]))

    async def test_zwei_echte_woerter_unterbrechen(self):
        for text in (" Leg das", "Du hast", "Danke dir"):
            with self.subTest(text=text):
                s = self.strategie()
                await s.process_frame(interim(text))
                self.assertEqual((self.angesetzt, self.ausgeloest), ([], [True]))

    async def test_rueckmeldelaute_und_wortanfaenge_melden_nichts(self):
        for text in ("mhm ja genau", "okay", "Genau so", "Gen", "Mh", " J"):
            with self.subTest(text=text):
                s = self.strategie()
                await s.process_frame(interim(text))
                self.assertEqual((self.angesetzt, self.ausgeloest), ([], []))

    async def test_schweigt_wamo_gibt_es_keinen_ansatz(self):
        s = self.strategie()
        s._bot_speaking = False
        await s.process_frame(TranscriptionFrame("Hier", "u", "t"))
        self.assertEqual((self.angesetzt, self.ausgeloest), ([], [True]), "wie bisher: schweigt WAMO, startet jedes Wort den Zug")

    async def test_ein_gerade_gespieltes_wort_ist_vielleicht_echo_kein_ansatz(self):
        s = self.strategie(gesprochen=lambda sekunden: ["montag", "oder", "dienstag"])
        await s.process_frame(interim("Montag"))
        self.assertEqual((self.angesetzt, self.ausgeloest), ([], []))
        await s.process_frame(interim("Danke"))
        self.assertEqual(self.angesetzt, ["Danke"])

    async def test_bei_erkanntem_echo_gibt_es_keinen_ansatz(self):
        s = self.strategie(gesprochen=lambda sekunden: ["das", "ist", "ein", "test"], echo_modus=True)
        jetzt = time.monotonic()
        s._echo_zeiten.extend([jetzt, jetzt])
        await s.process_frame(interim("Fertig"))
        self.assertEqual((self.angesetzt, self.ausgeloest), ([], []))

    def test_hat_stoppwort_bleibt_unveraendert(self):
        self.assertTrue(hat_stoppwort("Danke"))
        self.assertTrue(hat_stoppwort("Moment, ich habe eine Frage"))
        self.assertFalse(hat_stoppwort("Fertig"))


class FakeHalt:
    def __init__(self):
        self.haelt = False
        self.gespielt = None
        self.abgeschnitten = 0

    def anhalten(self):
        self.haelt = True

    def fortsetzen(self):
        self.haelt = False

    def abschneiden(self):
        self.abgeschnitten += 1
        self.haelt = False

    def gespielt_bis_ns(self):
        return self.gespielt

    async def warte_auf_fortschritt(self, frist):
        await asyncio.sleep(0.005)


class TrackerHaltTest(unittest.IsolatedAsyncioTestCase):
    """PlaybackTracker mit Halt: ein Wort kommt erst, wenn sein Ton hinausgeht; hält WAMO inne, warten die Wörter mit."""

    async def asyncSetUp(self):
        patch = mock.patch.object(FrameProcessor, "process_frame", new=mock.AsyncMock())
        patch.start()
        self.addCleanup(patch.stop)
        self.halt = FakeHalt()
        self.gate = SoftInterruptGate(halt=self.halt, max_wait_secs=0, grenze=True)
        self.gate._bot_speaking = True
        self.tracker = PlaybackTracker(self.gate, self.halt)
        self.weitergegeben = []

        async def senden(frame, richtung=FrameDirection.DOWNSTREAM):
            self.weitergegeben.append(frame)

        async def abbrechen(task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self.tracker.push_frame = senden
        self.tracker.create_task = lambda coro, name=None: asyncio.get_running_loop().create_task(coro)
        self.tracker.cancel_task = abbrechen
        self.gate.push_frame = mock.AsyncMock()
        self.gate.create_task = lambda coro, name=None: asyncio.get_running_loop().create_task(coro)

    async def senden(self, frame, richtung=FrameDirection.DOWNSTREAM):
        await self.tracker.process_frame(frame, richtung)

    async def test_wort_kommt_erst_wenn_sein_ton_hinausgeht(self):
        erstes, ende = wort("Ich", 1_000_000_000), LLMFullResponseEndFrame()
        await self.senden(erstes)
        await self.senden(ende)  # wartet hinter dem Wort, die Reihenfolge bleibt
        await asyncio.sleep(0.03)
        self.assertEqual(self.weitergegeben, [])
        self.halt.gespielt = 1_000_000_000
        await asyncio.sleep(0.03)
        self.assertEqual(self.weitergegeben, [erstes, ende])

    async def test_abbruch_verwirft_nicht_gespielte_woerter_und_schneidet_den_ausgang_ab(self):
        await self.senden(wort("noch", 2_000_000_000))
        abbruch = InterruptionFrame()
        await self.senden(abbruch)
        self.halt.gespielt = 3_000_000_000
        await asyncio.sleep(0.03)
        self.assertEqual(self.weitergegeben, [abbruch])
        self.assertEqual(self.halt.abgeschnitten, 1)

    async def test_ende_der_antwort_gibt_alles_frei(self):
        letztes = wort("fertig.", 2_000_000_000)
        await self.senden(letztes)
        ende = BotStoppedSpeakingFrame()
        await self.senden(ende)
        self.assertEqual(self.weitergegeben, [letztes, ende])

    async def test_haelt_wamo_inne_warten_die_woerter_mit_und_kommen_danach_in_ordnung(self):
        self.halt.gespielt = 5_000_000_000
        self.gate.wort_beginnt("Ich")
        self.gate.ansatz(" Danke")
        erstes, zweites = wort("lege", 1_000_000_000), wort("das", 1_200_000_000)
        await self.senden(erstes)
        await self.senden(zweites)
        await asyncio.sleep(0.05)
        self.assertEqual(self.weitergegeben, [], "WAMO hält vor diesem Wort inne")
        self.assertTrue(self.halt.haelt)
        self.gate._fortsetzen("Test")
        await asyncio.sleep(0.05)
        self.assertEqual(self.weitergegeben, [erstes, zweites])

    async def test_ein_wort_hinter_der_stoppgrenze_wird_abgeschnitten(self):
        self.halt.gespielt = 5_000_000_000
        self.gate.wort_beginnt("Ich")
        self.gate._arm()
        await self.senden(wort("lege", 1_000_000_000))
        await asyncio.sleep(0.03)
        self.assertEqual(self.weitergegeben, [], "gilt als nicht gesprochen, nicht im Verlauf")
        self.assertTrue(self.halt.haelt)

    async def test_der_beginn_eines_sprachabschnitts_wird_sofort_gemeldet_auch_wenn_woerter_davor_warten(self):
        gestartet = TTSStartedFrame()
        await self.senden(wort("Ich", 1_000_000_000))
        await self.senden(gestartet)
        self.assertTrue(self.gate._tts_offen, "für den Verlauf zählt der Moment, in dem der Abschnitt ankommt")
        self.assertEqual(self.weitergegeben, [])
        self.halt.gespielt = 1_000_000_000
        await asyncio.sleep(0.03)
        self.assertEqual(self.weitergegeben[-1], gestartet, "weitergegeben wird in der alten Reihenfolge")

    async def test_ton_und_woerter_ohne_zeitstempel_gehen_sofort_durch(self):
        await self.senden(wort("Ich", 9_000_000_000))  # wartet
        ton = TTSAudioRawFrame(audio=bytes(1920), sample_rate=24000, num_channels=1)
        ohne_zeit = wort("Moment", 0)
        await self.senden(ton)
        self.assertEqual(self.weitergegeben, [ton], "Ton wartet nie hinter einem Wort")
        self.weitergegeben.clear()
        self.tracker._queue.clear()
        await self.senden(ohne_zeit)
        self.assertEqual(self.weitergegeben, [ohne_zeit])

    async def test_ohne_ton_gibt_die_sicherung_das_wort_frei(self):
        tracker = PlaybackTracker(self.gate, self.halt, max_wait_secs=0.05)
        tracker.push_frame = self.tracker.push_frame
        tracker.create_task = self.tracker.create_task
        tracker.cancel_task = self.tracker.cancel_task
        erstes = wort("Ich", 1_000_000_000)
        await tracker.process_frame(erstes, FrameDirection.DOWNSTREAM)  # halt.gespielt bleibt None
        await asyncio.sleep(0.15)
        self.assertEqual(self.weitergegeben, [erstes])


if __name__ == "__main__":
    unittest.main()
