"""Zusammenspiel in einer echten Pipecat-Pipeline: Gate, Ausgang (MediaSender), AusgabeHalt, Tracker.

Der Ausgang taktet wie die WebRTC-Spur: jeder 40-ms-Happen braucht seine Spielzeit. Die Sprachausgabe ist nachgebaut wie bei
Pipecat und Soniox: Ton schneller als Echtzeit, Wörter mit pts = erster Ton plus Versatz. Läuft rund 10 Sekunden.
"""

import asyncio
import time
import unittest
from unittest import mock

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InterruptionFrame,
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

import soft_interrupt
import wiedergabe
from soft_interrupt import PlaybackTracker, SoftInterruptGate
from wiedergabe import AusgabeHalt, WiedergabeStart

RATE = 24000
HAPPEN = b"\x10\x27" * 960  # 40 ms Ton (nicht still)
HAPPEN_JE_WORT = 5  # jedes Wort 0,2 s
WEITER_SECS = 0.5  # Stille ab dem Ansatz, dann spricht WAMO weiter (frühestens MIN_HALT_SECS nach dem Anhalten)
# Gate und Ausgang messen mit derselben Uhr (time.monotonic); der Spielraum deckt nur Rundung ab, nicht die Last des Rechners
UHR_SPIEL = 0.02
# Nach einem Abbruch so lange auf Ton und Wörter schauen, die nicht mehr hinausgehen dürfen: mehrere Happen und die Zeit, in der
# der Halt den Rest der alten Antwort verwirft
NACHLAUF_SECS = 0.3
# So lange höchstens auf einen Zustand warten. Ein grüner Lauf wartet nie so lange; die Frist ist großzügig, damit ein stark
# belasteter Rechner (alle Kerne ausgelastet, Zeitgeber drei- bis viermal so langsam) nicht als Fehler gilt.
FRIST_SECS = 15.0


def an_wortgrenze(happen: int) -> bool:
    """Liegt die Stelle nach so vielen Happen an einer Wortgrenze, auf einen Happen genau? Später: die Entscheidung fällt zwischen
    zwei Happen, auf einem belasteten Rechner (Windows: Takt von 16 ms) einen Happen danach. Früher: Die Wortzeiten zählen ab dem
    ersten Ton der Sprachausgabe, der Halt ab seiner Ankunft am Ausgang; braucht der Weg dorthin unter Last mehr als TOLERANZ_NS,
    erreicht der Ton ein Wort einen Happen eher."""
    return happen % HAPPEN_JE_WORT in (HAPPEN_JE_WORT - 1, 0, 1)


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
        self.unterbrochen = asyncio.Event()  # der Abbruch ist durch die ganze Pipeline gelaufen (Gate, Tracker, bis hierher)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.gestartet.set()
        if isinstance(frame, InterruptionFrame):
            self.unterbrochen.set()
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
        # Jedes Anhalten zählen: Ein Abbruch hält den Ton erst an (Gate) und öffnet ihn dann wieder (Tracker). Gewartet wird auf
        # dieses Ereignis, nicht auf einen Ruhezustand, der auch vor dem Abbruch schon gilt.
        self.angehalten = 0
        anhalten = self.halt.anhalten

        def anhalten_zaehlen():
            self.angehalten += 1
            anhalten()

        self.halt.anhalten = anhalten_zaehlen
        # grenzwarte_secs: Ohne Wortgrenze hält das Gate nach dieser Zeit an einer beliebigen Stelle an (geprüft in test_innehalten.py).
        # Hier kommt alle 0,2 s eine Wortgrenze; die Frist liegt so weit darüber, dass auch ein stark belasteter Rechner die Grenze
        # erreicht, bevor der Rückfall greift.
        self.gate = SoftInterruptGate(
            halt=self.halt, weiter_secs=WEITER_SECS, max_pause_secs=2.0, grenze=True, ducken=True, max_wait_secs=1.5,
            grenzwarte_secs=1.0,
        )
        self.gruende: list[str] = []  # warum das Gate abbrach (für die Meldung, wenn eine Prüfung scheitert)
        start_fire = self.gate._start_fire

        def start_fire_merken(grund, **kwargs):
            self.gruende.append(grund)
            start_fire(grund, **kwargs)

        self.gate._start_fire = start_fire_merken
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
        await asyncio.wait_for(self.sammler.gestartet.wait(), FRIST_SECS)
        # Die Uhr der Pipeline läuft ab dem Aufbau; in den ersten Millisekunden steht sie auf 0 (pts 0 gilt als "ohne Zeitstempel")
        await asyncio.sleep(0.2)

    async def asyncTearDown(self):
        await self.task.queue_frame(EndFrame())
        await asyncio.wait_for(self.runner, 10)

    def zeit_des_wortes(self, text):
        return next(t for t, w in self.sammler.woerter if w == text)

    def gesprochen(self):
        return [w for _, w in self.sammler.woerter]

    async def warte_bis(self, bedingung, frist=FRIST_SECS):
        """Wartet auf einen Zustand statt auf eine feste Zeit: die Zeitgeber der Pipeline laufen auf einem belasteten Rechner ungleich."""
        ende = time.monotonic() + frist
        while not bedingung():
            self.assertLess(time.monotonic(), ende, f"Zustand nicht rechtzeitig erreicht: {self.stand()}")
            await asyncio.sleep(0.01)

    def stand(self) -> str:
        return (f"{len(self.ausgang.log)} Happen, Wörter {self.gesprochen()}, haelt={self.halt.haelt}, angehalten={self.angehalten}, "
                f"pending={self.gate._pending}, firing={self.gate._firing}, Gründe {self.gruende}")

    async def warte_auf_happen(self, anzahl):
        await self.warte_bis(lambda: len(self.ausgang.log) >= anzahl)

    def ohne_leiser_stopp(self):
        return not [d for d in self.nachrichten.daten if d.get("type") == "leiser_stopp"]

    async def einwerfen(self):
        """Die Person fällt WAMO ins Wort. Gibt den Stand des Zählers für das Anhalten vor dem Einwurf zurück."""
        vorher = self.angehalten
        await self.task.queue_frame(UserStartedSpeakingFrame())
        return vorher

    async def warte_auf_unterbrechung(self, angehalten_vorher, frist=FRIST_SECS):
        """Wartet auf den Abbruch selbst, nicht auf einen Zustand, der schon vor ihm gilt: Direkt nach queue_frame ist das Frame der
        Person noch nicht verarbeitet, also sind firing, pending und halt.haelt noch in Ruhe. Erst wenn das Gate den Ton angehalten
        hat und der InterruptionFrame den Sammler am Ende der Pipeline erreicht hat (Gate und Tracker waren daran), ist der Abbruch
        geschehen; danach schließt das Gate ihn ab und der Ausgang ist wieder offen."""
        await self.warte_bis(lambda: self.angehalten > angehalten_vorher and self.sammler.unterbrochen.is_set(), frist)
        await self.warte_bis(lambda: not self.gate._firing and not self.gate._pending and not self.halt.haelt, frist)

    async def test_woerter_kommen_mit_ihrem_ton_auch_wenn_die_sprachausgabe_stockt(self):
        woerter = ["Eins", "zwei", "drei", "vier"]
        # Nach "zwei" 0,8 s nichts von der Sprachausgabe: der Ton steht 0,4 s still, die Uhr läuft weiter.
        await self.tts.sprechen(woerter, luecke_nach={1: 0.8})
        await self.warte_bis(lambda: len(self.gesprochen()) == len(woerter) and len(self.ausgang.log) == len(woerter) * HAPPEN_JE_WORT)
        self.assertEqual(self.gesprochen(), woerter)
        # Nach der Uhr käme "vier" mitten in der Stille, bevor der Ton von "drei" überhaupt beginnt; gemessen am geschriebenen Ton
        # kommt es erst, wenn "drei" schon klingt. Eine Obergrenze für den Abstand zum eigenen Ton entfällt bewusst: Auf einem
        # belasteten Rechner kommt jedes Wort etwas später, nie früher.
        drei_ton = self.ausgang.log[2 * HAPPEN_JE_WORT]  # erster Happen von "drei", nach der Stille
        self.assertGreater(self.zeit_des_wortes("vier"), drei_ton, "vier wartet auf seinen Ton")

    async def test_innehalten_und_an_derselben_stelle_weiter(self):
        woerter = ["Ich", "lege", "das", "Repository", "jetzt", "an."]
        sprechen = asyncio.create_task(self.tts.sprechen(woerter))
        await self.warte_auf_happen(6)  # "lege" hat begonnen, die nächste Wortgrenze liegt vor "das"
        ansatz_um = time.monotonic()
        self.gate.ansatz(" Danke")  # ein Wort der Person, sonst nichts
        await sprechen
        await self.warte_bis(lambda: len(self.gesprochen()) == len(woerter) and len(self.ausgang.log) == len(woerter) * HAPPEN_JE_WORT)
        self.assertEqual(self.gesprochen(), woerter, "nichts verloren")
        self.assertEqual(len(self.ausgang.log), len(woerter) * HAPPEN_JE_WORT)
        self.assertEqual(self.angehalten, 1, "genau einmal innegehalten")
        luecken = [(b - a, i + 1) for i, (a, b) in enumerate(zip(self.ausgang.log, self.ausgang.log[1:]))]
        laengste, stelle = max(luecken)
        # Die Schwellen folgen aus dem Gate, nicht aus einer geschätzten Zahl: Der Halt beginnt erst nach dem letzten Happen vor der
        # Lücke und dauert mindestens MIN_HALT_SECS; weiter geht es frühestens WEITER_SECS nach dem Ansatz. Ein belasteter Rechner
        # macht die Pause nur länger, nie kürzer.
        self.assertGreaterEqual(laengste, soft_interrupt.MIN_HALT_SECS - UHR_SPIEL, "WAMO hat innegehalten ...")
        self.assertTrue(an_wortgrenze(stelle), f"... an einer Wortgrenze (auf einen Happen genau): Lücke vor Happen {stelle}")
        pause_ende = self.ausgang.log[stelle]
        self.assertGreaterEqual(pause_ende - ansatz_um, WEITER_SECS - UHR_SPIEL, "... und erst nach der Stille wieder angefangen")
        weiter_mit = woerter[(stelle + 1) // HAPPEN_JE_WORT]  # das Wort an der nächsten Grenze
        # Nicht während der Pause; nach dem Fortsetzen kommen Ausgang und Tracker in beliebiger Reihenfolge dran (Spielraum 0,1 s)
        self.assertGreaterEqual(self.zeit_des_wortes(weiter_mit), pause_ende - 0.1, "das nächste Wort wartet mit dem Ton")
        self.assertTrue(self.ohne_leiser_stopp(), "während des Halts kein Ausblenden")

    async def test_zweites_wort_waehrend_des_innehaltens_bricht_ab(self):
        woerter = ["Ich", "lege", "das", "Repository", "jetzt", "an."]
        sprechen = asyncio.create_task(self.tts.sprechen(woerter))
        await self.warte_auf_happen(6)
        self.gate.ansatz(" Das")
        await self.warte_bis(lambda: self.halt.haelt)
        vorher = await self.einwerfen()  # "Das ist falsch": Einwurf
        await sprechen
        await self.warte_auf_unterbrechung(vorher)
        await asyncio.sleep(NACHLAUF_SECS)  # nichts Verworfenes darf noch hinausgehen
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
        vorher = await self.einwerfen()  # zwei Wörter erkannt: Einwurf ohne vorheriges Innehalten
        await sprechen
        await self.warte_auf_unterbrechung(vorher)
        await asyncio.sleep(NACHLAUF_SECS)  # nichts Verworfenes darf noch hinausgehen
        gesprochen = self.gesprochen()
        self.assertEqual(gesprochen, woerter[: len(gesprochen)])
        self.assertLessEqual(len(gesprochen), 3)
        self.assertTrue(
            an_wortgrenze(len(self.ausgang.log)),
            f"der Ton endet an einer Wortgrenze (auf einen Happen genau): {len(self.ausgang.log)} Happen, Gründe {self.gruende}",
        )
        self.assertFalse(self.halt.haelt)
        self.assertTrue(self.ohne_leiser_stopp())

    async def test_nach_dem_abbruch_spricht_die_naechste_antwort_normal(self):
        # Das Zeitfenster des Verwerfens ist hier länger als im Betrieb, damit die nächste Antwort auch auf einem belasteten Rechner
        # sicher hineinfällt
        verwerfen = mock.patch.object(wiedergabe, "ABBRUCH_NACHLAUF_SECS", 3.0)
        verwerfen.start()
        self.addCleanup(verwerfen.stop)
        sprechen = asyncio.create_task(self.tts.sprechen(["Ich", "lege", "das", "an."]))
        await self.warte_auf_happen(6)
        vorher = await self.einwerfen()
        await sprechen
        await self.warte_auf_unterbrechung(vorher)
        # Die nächste Antwort beginnt sofort, noch während der Halt Reste der alten verwirft: wie ein Füllwort oder eine Ansage direkt
        # nach dem Abbruch. Sie muss trotzdem ganz zu hören sein.
        self.assertLess(time.monotonic(), self.halt._verwerfen_bis, "die Probe liegt im Zeitfenster des Verwerfens")
        vorher, ton_vorher = len(self.sammler.woerter), len(self.ausgang.log)
        await self.tts.sprechen(["Alles", "klar."])
        await self.warte_bis(lambda: len(self.sammler.woerter) >= vorher + 2 and len(self.ausgang.log) >= ton_vorher + 2 * HAPPEN_JE_WORT)
        self.assertEqual([w for _, w in self.sammler.woerter][vorher:], ["Alles", "klar."])
        self.assertEqual(len(self.ausgang.log) - ton_vorher, 2 * HAPPEN_JE_WORT, "die ganze neue Antwort ist zu hören")

    async def test_nach_dem_weitersprechen_ist_ein_neuer_einwurf_wieder_ein_einwurf(self):
        woerter = ["Ich", "lege", "das", "Repository", "jetzt", "an", "und", "dann", "ist", "es", "fertig."]
        sprechen = asyncio.create_task(self.tts.sprechen(woerter))
        await self.warte_auf_happen(6)
        self.gate.ansatz(" Danke")
        await self.warte_bis(lambda: self.halt.haelt)
        await self.warte_bis(lambda: not self.halt.haelt)  # Pause, dann geht es weiter
        self.assertFalse(self.sammler.unterbrochen.is_set(), "Weitersprechen ist kein Abbruch")
        self.assertTrue(self.ohne_leiser_stopp())
        vorher = await self.einwerfen()  # jetzt ein richtiger Einwurf
        await sprechen
        await self.warte_auf_unterbrechung(vorher)
        await asyncio.sleep(NACHLAUF_SECS)  # nichts Verworfenes darf noch hinausgehen
        gesprochen = self.gesprochen()
        self.assertEqual(gesprochen, woerter[: len(gesprochen)])
        self.assertLess(len(gesprochen), len(woerter))
        self.assertFalse(self.halt.haelt)


if __name__ == "__main__":
    unittest.main()
