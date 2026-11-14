"""Zusammenspiel in einer echten Pipecat-Pipeline: Gate, Ausgang (MediaSender), AusgabeHalt, Tracker.

Der Ausgang taktet wie die WebRTC-Spur: jeder 40-ms-Happen braucht seine Spielzeit. Die Sprachausgabe ist nachgebaut wie bei
Pipecat und Soniox: Ton schneller als Echtzeit, Wörter mit pts = erster Ton plus Versatz. Läuft rund 10 Sekunden.
"""

import asyncio
import time
import unittest

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

from soft_interrupt import PlaybackTracker, SoftInterruptGate
from wiedergabe import AusgabeHalt, WiedergabeStart

RATE = 24000
HAPPEN = b"\x10\x27" * 960  # 40 ms Ton (nicht still)
HAPPEN_JE_WORT = 5  # jedes Wort 0,2 s


class GetakteterAusgang(BaseOutputTransport):
    def __init__(self):
        super().__init__(TransportParams(audio_out_enabled=True, audio_out_sample_rate=RATE))
        self.log: list[float] = []  # Zeitpunkt jedes geschriebenen Happens

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame) -> bool:
        self.log.append(time.monotonic())
        await asyncio.sleep(len(frame.audio) / (RATE * 2))
        return True


class FalscheSprachausgabe(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    async def sprechen(self, woerter: list[str], luecke_nach: dict[int, float] | None = None):
        luecke_nach = luecke_nach or {}
        beginn = None
        for i, text in enumerate(woerter):
            for _ in range(HAPPEN_JE_WORT):
                await self.push_frame(TTSAudioRawFrame(audio=HAPPEN, sample_rate=RATE, num_channels=1))
                if beginn is None:
                    beginn = self.get_clock().get_time()
            wort = TTSTextFrame(text=text, aggregated_by="word")
            # Wörter beginnen nie genau an einer Happengrenze: 20 ms hinter ihr (Soniox liefert Millisekunden, der Ausgang schreibt 40 ms)
            wort.pts = beginn + i * HAPPEN_JE_WORT * 40_000_000 + 20_000_000
            await self.push_frame(wort)
            if i in luecke_nach:
                await asyncio.sleep(luecke_nach[i])  # die Sprachausgabe liefert langsamer, als gesprochen wird
        await self.push_frame(TTSStoppedFrame())


class Sammler(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.woerter: list[tuple[float, str]] = []
        self.gestartet = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.gestartet.set()
        if isinstance(frame, TTSTextFrame):
            self.woerter.append((time.monotonic(), frame.text))
        await self.push_frame(frame, direction)


class Nachrichten(FrameProcessor):
    """Merkt sich, was das Gate an die Oberfläche schickt (leiser_stopp, stimme_leiser)."""

    def __init__(self):
        super().__init__()
        self.daten: list[dict] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, RTVIServerMessageFrame):
            self.daten.append(frame.data)
        await self.push_frame(frame, direction)


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ausgang = GetakteterAusgang()
        self.halt = AusgabeHalt()
        self.assertTrue(self.halt.einhaengen(self.ausgang))
        self.gate = SoftInterruptGate(
            halt=self.halt, weiter_secs=0.5, max_pause_secs=2.0, grenze=True, ducken=True, max_wait_secs=1.5
        )
        self.tts = FalscheSprachausgabe()
        self.sammler = Sammler()
        self.nachrichten = Nachrichten()
        self.task = PipelineTask(
            Pipeline(
                [
                    self.gate,
                    self.nachrichten,
                    self.tts,
                    WiedergabeStart(self.halt),
                    self.ausgang,
                    PlaybackTracker(self.gate, self.halt),
                    self.sammler,
                ]
            ),
            params=PipelineParams(audio_out_sample_rate=RATE),
            enable_rtvi=False,
        )
        self.runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(self.task))
        await asyncio.wait_for(self.sammler.gestartet.wait(), 5)
        # Die Uhr der Pipeline läuft ab dem Aufbau; in den ersten Millisekunden steht sie auf 0 (pts 0 gilt als "ohne Zeitstempel")
        await asyncio.sleep(0.2)

    async def asyncTearDown(self):
        await self.task.queue_frame(EndFrame())
        await asyncio.wait_for(self.runner, 10)

    def zeit_des_wortes(self, text):
        return next(t for t, w in self.sammler.woerter if w == text)

    def gesprochen(self):
        return [w for _, w in self.sammler.woerter]

    async def warte_bis(self, bedingung, frist=3.0):
        """Wartet auf einen Zustand statt auf eine feste Zeit: die Zeitgeber der Pipeline laufen auf einem belasteten Rechner ungleich."""
        ende = time.monotonic() + frist
        while not bedingung():
            self.assertLess(time.monotonic(), ende, "Zustand nicht rechtzeitig erreicht")
            await asyncio.sleep(0.01)

    async def warte_auf_happen(self, anzahl):
        await self.warte_bis(lambda: len(self.ausgang.log) >= anzahl)

    def ohne_leiser_stopp(self):
        return not [d for d in self.nachrichten.daten if d.get("type") == "leiser_stopp"]

    async def test_woerter_kommen_mit_ihrem_ton_auch_wenn_die_sprachausgabe_stockt(self):
        woerter = ["Eins", "zwei", "drei", "vier"]
        # Nach "zwei" 0,8 s nichts von der Sprachausgabe: der Ton steht 0,4 s still, die Uhr läuft weiter.
        await self.tts.sprechen(woerter, luecke_nach={1: 0.8})
        await asyncio.sleep(1.4)
        self.assertEqual(self.gesprochen(), woerter)
        vier_ton = self.ausgang.log[3 * HAPPEN_JE_WORT]  # erster Happen von "vier"
        # Nach der Uhr käme "vier" 0,4 s vor seinem Ton; gemessen am geschriebenen Ton nur ein Happen Vorlauf plus die Ungenauigkeit
        # des Takts (Windows: Schritte von 16 ms)
        self.assertLess(abs(self.zeit_des_wortes("vier") - vier_ton), 0.15)

    async def test_innehalten_und_an_derselben_stelle_weiter(self):
        woerter = ["Ich", "lege", "das", "Repository", "jetzt", "an."]
        sprechen = asyncio.create_task(self.tts.sprechen(woerter))
        await self.warte_auf_happen(6)  # "lege" hat begonnen, die nächste Wortgrenze liegt vor "das"
        self.gate.ansatz(" Danke")  # ein Wort der Person, sonst nichts
        await sprechen
        await self.warte_bis(lambda: len(self.gesprochen()) == len(woerter) and len(self.ausgang.log) == len(woerter) * HAPPEN_JE_WORT, 5.0)
        self.assertEqual(self.gesprochen(), woerter, "nichts verloren")
        self.assertEqual(len(self.ausgang.log), len(woerter) * HAPPEN_JE_WORT)
        luecken = [(b - a, i + 1) for i, (a, b) in enumerate(zip(self.ausgang.log, self.ausgang.log[1:]))]
        laengste, stelle = max(luecken)
        self.assertGreater(laengste, 0.25, "WAMO hat innegehalten ...")
        # Die Entscheidung fällt zwischen zwei Happen; auf einem belasteten Rechner (Windows: Takt von 16 ms) kann sie einen Happen
        # (40 ms) später kommen
        self.assertIn(stelle % HAPPEN_JE_WORT, (0, 1), "... an einer Wortgrenze (auf einen Happen genau)")
        pause_ende = self.ausgang.log[stelle]
        weiter_mit = woerter[stelle // HAPPEN_JE_WORT]
        self.assertGreaterEqual(self.zeit_des_wortes(weiter_mit), pause_ende - 0.01)
        self.assertTrue(self.ohne_leiser_stopp(), "während des Halts kein Ausblenden")

    async def test_zweites_wort_waehrend_des_innehaltens_bricht_ab(self):
        woerter = ["Ich", "lege", "das", "Repository", "jetzt", "an."]
        sprechen = asyncio.create_task(self.tts.sprechen(woerter))
        await self.warte_auf_happen(6)
        self.gate.ansatz(" Das")
        await self.warte_bis(lambda: self.halt.haelt)
        await self.task.queue_frame(UserStartedSpeakingFrame())  # "Das ist falsch": Einwurf
        await sprechen
        await self.warte_bis(lambda: not self.halt.haelt)
        await asyncio.sleep(0.5)
        gesprochen = self.gesprochen()
        self.assertEqual(gesprochen, woerter[: len(gesprochen)])
        self.assertLessEqual(len(gesprochen), 3, "nur, was vor dem Innehalten hinausging")
        self.assertLessEqual(len(self.ausgang.log), len(gesprochen) * HAPPEN_JE_WORT + 1)
        self.assertFalse(self.halt.haelt, "offen für die nächste Antwort")
        self.assertTrue(self.ohne_leiser_stopp(), "der Ton stand schon")

    async def test_einwurf_ohne_ansatz_stoppt_an_der_naechsten_wortgrenze(self):
        woerter = ["Ich", "lege", "das", "Repository", "jetzt", "an."]
        sprechen = asyncio.create_task(self.tts.sprechen(woerter))
        await self.warte_auf_happen(6)
        await self.task.queue_frame(UserStartedSpeakingFrame())  # zwei Wörter erkannt: Einwurf ohne vorheriges Innehalten
        await sprechen
        await self.warte_bis(lambda: self.halt.haelt is False and self.gate._firing is False and self.gate._pending is False)
        await asyncio.sleep(0.6)
        gesprochen = self.gesprochen()
        self.assertEqual(gesprochen, woerter[: len(gesprochen)])
        self.assertLessEqual(len(gesprochen), 3)
        self.assertIn(len(self.ausgang.log) % HAPPEN_JE_WORT, (0, 1), "der Ton endet an einer Wortgrenze (auf einen Happen genau)")
        self.assertFalse(self.halt.haelt)
        self.assertTrue(self.ohne_leiser_stopp())

    async def test_nach_dem_abbruch_spricht_die_naechste_antwort_normal(self):
        sprechen = asyncio.create_task(self.tts.sprechen(["Ich", "lege", "das", "an."]))
        await self.warte_auf_happen(6)
        await self.task.queue_frame(UserStartedSpeakingFrame())
        await sprechen
        await self.warte_bis(lambda: self.gate._firing is False and self.gate._pending is False)
        await asyncio.sleep(0.7)
        vorher = len(self.sammler.woerter)
        await self.tts.sprechen(["Alles", "klar."])
        await self.warte_bis(lambda: len(self.sammler.woerter) >= vorher + 2, 4.0)
        self.assertEqual([w for _, w in self.sammler.woerter][vorher:], ["Alles", "klar."])

    async def test_nach_dem_weitersprechen_ist_ein_neuer_einwurf_wieder_ein_einwurf(self):
        woerter = ["Ich", "lege", "das", "Repository", "jetzt", "an", "und", "dann", "ist", "es", "fertig."]
        sprechen = asyncio.create_task(self.tts.sprechen(woerter))
        await self.warte_auf_happen(6)
        self.gate.ansatz(" Danke")
        await self.warte_bis(lambda: self.halt.haelt)
        await self.warte_bis(lambda: not self.halt.haelt, 4.0)  # Pause, dann geht es weiter
        self.assertTrue(self.ohne_leiser_stopp())
        await self.task.queue_frame(UserStartedSpeakingFrame())  # jetzt ein richtiger Einwurf
        await sprechen
        await self.warte_bis(lambda: self.gate._firing is False and self.gate._pending is False)
        await asyncio.sleep(0.6)
        gesprochen = self.gesprochen()
        self.assertEqual(gesprochen, woerter[: len(gesprochen)])
        self.assertLess(len(gesprochen), len(woerter))
        self.assertFalse(self.halt.haelt)


if __name__ == "__main__":
    unittest.main()
