"""Tests für stimme_waechter.py: eine sicher fremde Äußerung erreicht das Sprachmodell nicht (Modus streng).

Ausführen:  python -m pytest -q test_stimme_waechter.py   (im Ordner voice-core/)
"""

import asyncio
import time
import unittest
from unittest import mock

import numpy as np

from pipecat.frames.frames import InputAudioRawFrame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language

import stimme_waechter
from stimme_gate import EIGEN, FREMD, KURZ, UNKLAR, StimmGate
from stimme_modell import DIM
from stimme_pruefer import Stimmpruefer, Urteil
from stimme_waechter import WARTEN_SECS, StimmWaechter

RATE = 16000
EIGENER = np.eye(DIM, dtype=np.float32)[0]
FREMDER = np.eye(DIM, dtype=np.float32)[1]


class Uhr:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def bereites_gate(modus="streng", uhr=None) -> StimmGate:
    gate = StimmGate(modus=modus, uhr=uhr or Uhr())
    gate.setze_abdruck(EIGENER)
    gate.modell_ok = True
    return gate


def urteil(art: str, secs: float = 2.0, wert: float = 0.12, nr: int = 1) -> Urteil:
    return Urteil(nr=nr, urteil=art, wert=wert, secs=secs, ueberlappung=False, um=1000.0)


class AttrappePruefer:
    """Gibt nach dauer Sekunden das vorbereitete Urteil zurück, oder None, wenn die Frist vorher abläuft."""

    def __init__(self, ergebnis: Urteil | None, dauer: float = 0.0, *, dauern: list[float] | None = None):
        """dauern: je Anfrage eine eigene Dauer (die erste für das erste Transkript usw.), sonst gilt dauer für alle."""
        self.ergebnis = ergebnis
        self.dauer = dauer
        self.dauern = dauern
        self.fragen: list[float] = []
        self.nummern: list[int | None] = []
        self.satzenden = 0
        self._zugeordnet = 0

    def satz_ende(self) -> None:
        self.satzenden += 1

    def aeusserung_zuordnen(self) -> int:
        self._zugeordnet += 1
        return self._zugeordnet

    async def urteil_abwarten(self, frist: float, nr: int | None = None):
        self.fragen.append(frist)
        self.nummern.append(nr)
        dauer = self.dauern[len(self.fragen) - 1] if self.dauern else self.dauer
        if dauer > frist:
            await asyncio.sleep(frist)
            return None
        await asyncio.sleep(dauer)
        return self.ergebnis


def final(text: str = "Mach die Heizung an") -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="u", timestamp="2026-11-14T10:00:00Z", language=Language.DE, finalized=True)


def zwischenstand(text: str = "Mach die") -> InterimTranscriptionFrame:
    return InterimTranscriptionFrame(text=text, user_id="u", timestamp="2026-11-14T10:00:00Z", language=Language.DE)


async def durchlassen(waechter, frames):
    unten, _ = await run_test(waechter, frames_to_send=frames)
    texte = [f for f in unten if isinstance(f, (TranscriptionFrame, InterimTranscriptionFrame))]
    meldungen = [f.data for f in unten if isinstance(f, RTVIServerMessageFrame)]
    return texte, meldungen


class VerwerfenTests(unittest.IsolatedAsyncioTestCase):
    async def test_sicher_fremd_ersetzt_das_transkript_durch_nichts(self):
        gate = bereites_gate()
        waechter = StimmWaechter(gate, AttrappePruefer(urteil(FREMD)))
        texte, meldungen = await durchlassen(waechter, [final()])
        self.assertEqual(len(texte), 1)
        self.assertEqual(texte[0].text.strip(), "")  # der Zug endet leer, das Modell sieht keinen Satz
        self.assertTrue(texte[0].finalized)  # ... damit der Zug normal endet
        self.assertEqual(meldungen, [{"type": "stimme", "ergebnis": "fremd", "wert": 0.12, "modus": "streng", "ignoriert": True}])

    async def test_eigen_unklar_und_kurz_gehen_unveraendert_durch(self):
        for art in (EIGEN, UNKLAR, KURZ):
            waechter = StimmWaechter(bereites_gate(), AttrappePruefer(urteil(art)))
            texte, meldungen = await durchlassen(waechter, [final("Hallo WAMO")])
            self.assertEqual([t.text for t in texte], ["Hallo WAMO"], art)
            self.assertEqual(meldungen, [], art)

    async def test_endgueltiges_transkript_mit_satzzeichen_meldet_das_satzende(self):
        for text, erwartet in (("Mach die Heizung an.", 1), ("Wie spät ist es?", 1), ("Stopp!", 1), ("Mach die Heizung", 0),
                               ("  ", 0), ("Mach die Heizung an. Und dann", 0)):
            pruefer = AttrappePruefer(urteil(EIGEN))
            await durchlassen(StimmWaechter(bereites_gate(), pruefer), [final(text)])
            self.assertEqual(pruefer.satzenden, erwartet, text)

    async def test_zwischenstaende_und_verworfene_saetze_melden_das_satzende_richtig(self):
        pruefer = AttrappePruefer(urteil(EIGEN))
        await durchlassen(StimmWaechter(bereites_gate(), pruefer), [zwischenstand("Mach die Heizung an.")])
        self.assertEqual(pruefer.satzenden, 0)  # nur das endgültige Transkript zählt
        pruefer = AttrappePruefer(urteil(FREMD))
        await durchlassen(StimmWaechter(bereites_gate(), pruefer), [final("Mach die Heizung an.")])
        self.assertEqual(pruefer.satzenden, 1)  # auch ein verworfener Satz ist zu Ende

    async def test_fremd_aber_zu_wenig_sprache_geht_durch(self):
        waechter = StimmWaechter(bereites_gate(), AttrappePruefer(urteil(FREMD, secs=0.6)))
        texte, _ = await durchlassen(waechter, [final("Ja")])
        self.assertEqual([t.text for t in texte], ["Ja"])

    async def test_keine_antwort_in_der_frist_gilt_als_unklar_und_geht_durch(self):
        pruefer = AttrappePruefer(urteil(FREMD), dauer=5.0)
        waechter = StimmWaechter(bereites_gate(), pruefer, warten=0.05)
        start = time.monotonic()
        texte, _ = await durchlassen(waechter, [final("Hallo")])
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual([t.text for t in texte], ["Hallo"])
        self.assertEqual(pruefer.fragen, [0.05])

    async def test_kein_urteil_geht_durch(self):
        waechter = StimmWaechter(bereites_gate(), AttrappePruefer(None))
        texte, _ = await durchlassen(waechter, [final("Hallo")])
        self.assertEqual([t.text for t in texte], ["Hallo"])

    async def test_standardfrist_ist_begrenzt(self):
        self.assertLessEqual(WARTEN_SECS, 2.0)
        pruefer = AttrappePruefer(urteil(EIGEN))
        await durchlassen(StimmWaechter(bereites_gate(), pruefer), [final()])
        self.assertEqual(pruefer.fragen, [WARTEN_SECS])

    async def test_zwischenstaende_werden_nie_angehalten_oder_veraendert(self):
        pruefer = AttrappePruefer(urteil(FREMD))
        waechter = StimmWaechter(bereites_gate(), pruefer)
        texte, _ = await durchlassen(waechter, [zwischenstand("Mach"), zwischenstand("Mach die")])
        self.assertEqual([t.text for t in texte], ["Mach", "Mach die"])
        self.assertEqual(pruefer.fragen, [])  # kein Warten: Unterbrechen durch die Person bleibt so schnell wie bisher

    async def test_leeres_transkript_bleibt_unberuehrt(self):
        pruefer = AttrappePruefer(urteil(FREMD))
        texte, _ = await durchlassen(StimmWaechter(bereites_gate(), pruefer), [final("  ")])
        self.assertEqual([t.text for t in texte], ["  "])
        self.assertEqual(pruefer.fragen, [])

    async def test_jedes_transkript_fragt_nach_dem_urteil_seiner_aeusserung(self):
        pruefer = AttrappePruefer(urteil(EIGEN))
        await durchlassen(StimmWaechter(bereites_gate(), pruefer), [final("eins"), final("zwei")])
        self.assertEqual(pruefer.nummern, [1, 2])

    async def test_mehrere_transkripte_zu_einem_urteil_nur_eine_meldung(self):
        waechter = StimmWaechter(bereites_gate(), AttrappePruefer(urteil(FREMD)))
        texte, meldungen = await durchlassen(waechter, [final("eins"), final("zwei")])
        self.assertEqual([t.text.strip() for t in texte], ["", ""])
        self.assertEqual(len(meldungen), 1)

    async def test_eine_unterbrechung_waehrend_des_wartens_verliert_das_transkript_nicht(self):
        """Pipecat bricht bei einer Unterbrechung das Frame ab, das ein Verarbeiter gerade bearbeitet. Das gehaltene Transkript der Person darf
        dabei nicht verloren gehen (zum Beispiel wenn sie WAMO ins Wort fällt, während das Urteil noch aussteht)."""
        from pipecat.frames.frames import InterruptionFrame
        from pipecat.tests.utils import SleepFrame

        pruefer = AttrappePruefer(urteil(EIGEN), dauer=0.2)
        waechter = StimmWaechter(bereites_gate(), pruefer)
        unten, _ = await run_test(waechter, frames_to_send=[final("Mach die Heizung an"), SleepFrame(0.05), InterruptionFrame(), SleepFrame(0.4)])
        texte = [f.text for f in unten if isinstance(f, TranscriptionFrame)]
        self.assertEqual(texte, ["Mach die Heizung an"])

    async def test_nur_in_fahrtrichtung_abwaerts(self):
        pruefer = AttrappePruefer(urteil(FREMD))
        waechter = StimmWaechter(bereites_gate(), pruefer)
        waechter.push_frame = mock.AsyncMock()
        frame = final("hoch")
        with mock.patch("pipecat.processors.frame_processor.FrameProcessor.process_frame", mock.AsyncMock()):
            await waechter.process_frame(frame, FrameDirection.UPSTREAM)
        waechter.push_frame.assert_awaited_once_with(frame, FrameDirection.UPSTREAM)
        self.assertEqual(pruefer.fragen, [])


class ModusTests(unittest.IsolatedAsyncioTestCase):
    async def test_beobachten_und_aus_verwerfen_nie_und_warten_nie(self):
        for modus in ("beobachten", "aus"):
            pruefer = AttrappePruefer(urteil(FREMD))
            waechter = StimmWaechter(bereites_gate(modus), pruefer)
            texte, meldungen = await durchlassen(waechter, [final("Hallo")])
            self.assertEqual([t.text for t in texte], ["Hallo"], modus)
            self.assertEqual((meldungen, pruefer.fragen), ([], []), modus)

    async def test_ohne_abdruck_wird_nichts_verworfen_und_nicht_gewartet(self):
        gate = bereites_gate()
        gate.setze_abdruck(None)
        pruefer = AttrappePruefer(urteil(FREMD))
        texte, _ = await durchlassen(StimmWaechter(gate, pruefer), [final("Hallo")])
        self.assertEqual([t.text for t in texte], ["Hallo"])
        self.assertEqual(pruefer.fragen, [])

    async def test_ist_das_modell_nicht_bereit_geht_alles_durch(self):
        gate = bereites_gate()
        gate.modell_ok = False
        pruefer = AttrappePruefer(urteil(FREMD))
        texte, _ = await durchlassen(StimmWaechter(gate, pruefer), [final("Hallo")])
        self.assertEqual([t.text for t in texte], ["Hallo"])

    async def test_eigene_schwellen_gelten_fuer_die_mindestdauer(self):
        gate = bereites_gate()
        gate.schwellen = type(gate.schwellen)(min_secs=3.0)
        waechter = StimmWaechter(gate, AttrappePruefer(urteil(FREMD, secs=2.0)))
        texte, _ = await durchlassen(waechter, [final("Hallo")])
        self.assertEqual([t.text for t in texte], ["Hallo"])


class MitEchtemPrueferTests(unittest.IsolatedAsyncioTestCase):
    """Prüfer und Wächter zusammen: dieselben Äußerungen wie im Gespräch, das Modell ist ein Ersatz."""

    def setUp(self):
        self.uhr = Uhr()
        self.gate = bereites_gate(uhr=self.uhr)
        rechner = mock.Mock()
        rechner.embed = lambda pcm, rate: EIGENER if np.frombuffer(pcm, dtype=np.int16).astype(np.int64).sum() > 0 else FREMDER
        modell = mock.Mock()
        modell.get = mock.AsyncMock(return_value=rechner)
        self.pruefer = Stimmpruefer(self.gate, modell, speichern=mock.AsyncMock(), sagen=mock.AsyncMock(), uhr=self.uhr)
        self.pruefer.push_frame = mock.AsyncMock()
        self.waechter = StimmWaechter(self.gate, self.pruefer)

    async def sprechen(self, wert: int, secs: float):
        self.pruefer.on_speech_start(self.uhr.t + 0.2, 0.2)
        for _ in range(int(secs / 0.02)):
            self.uhr.t += 0.02
            await self.pruefer.on_audio(InputAudioRawFrame(audio=np.full(320, wert, dtype=np.int16).tobytes(), sample_rate=RATE, num_channels=1))
        for _ in range(10):
            self.uhr.t += 0.02
            await self.pruefer.on_audio(InputAudioRawFrame(audio=bytes(640), sample_rate=RATE, num_channels=1))
        self.pruefer.on_speech_stop(self.uhr.t, 0.2)

    async def test_fremde_aeusserung_wird_verworfen_die_eigene_geht_durch(self):
        await self.sprechen(-8000, 2.0)  # ein Gast; das Transkript kommt, während die Prüfung noch läuft
        texte, meldungen = await durchlassen(self.waechter, [final("Schließ die Tür")])
        self.assertEqual(texte[0].text.strip(), "")
        self.assertEqual(meldungen[0]["ergebnis"], "fremd")
        self.assertTrue(meldungen[0]["ignoriert"])
        self.pruefer.on_turn_end()
        await self.sprechen(8000, 2.0)  # danach die Person
        texte, meldungen = await durchlassen(self.waechter, [final("Mach die Heizung an")])
        self.assertEqual([t.text for t in texte], ["Mach die Heizung an"])
        self.assertEqual(meldungen, [])

    async def test_spaetes_transkript_bekommt_das_urteil_seiner_aeusserung(self):
        """Das Transkript eines Gastes kommt erst, als die Person schon wieder spricht: es gilt das Urteil der Äußerung des Gastes. Es wartet
        nicht auf das Ende der laufenden (sonst käme es erst nach deren Ende beim Aggregator an, und der Zug endete ohne sie)."""
        await self.sprechen(-8000, 2.0)
        self.pruefer.on_speech_start(self.uhr.t + 0.2, 0.2)
        texte, meldungen = await durchlassen(self.waechter, [final("Schließ die Tür")])
        self.assertEqual(texte[0].text.strip(), "")
        self.assertEqual(meldungen[0]["ergebnis"], "fremd")

    async def test_laufende_aeusserung_ohne_ende_haelt_hoechstens_die_frist_auf(self):
        self.pruefer.on_speech_start(self.uhr.t + 0.2, 0.2)  # VAD meldet nie ein Ende
        waechter = StimmWaechter(self.gate, self.pruefer, warten=0.05)
        start = time.monotonic()
        texte, _ = await durchlassen(waechter, [final("Hallo")])
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual([t.text for t in texte], ["Hallo"])  # kein Urteil: unklar, geht durch

    async def test_zwei_profile_im_selben_prozess_urteilen_getrennt(self):
        zweites_gate = bereites_gate(uhr=self.uhr)
        zweiter = StimmWaechter(zweites_gate, AttrappePruefer(None))
        await self.sprechen(-8000, 2.0)
        texte, _ = await durchlassen(zweiter, [final("Hallo")])
        self.assertEqual([t.text for t in texte], ["Hallo"])  # das Urteil der ersten Sitzung gilt nicht für die zweite
        texte, _ = await durchlassen(self.waechter, [final("Hallo")])
        self.assertEqual(texte[0].text.strip(), "")


class MitEchtemAggregatorTests(unittest.IsolatedAsyncioTestCase):
    """Der Zug des Nutzers endet auch ohne Satz: der echte Aggregator von Pipecat ruft das Modell dann nicht auf."""

    async def lauf(self, ergebnis: Urteil | None, text: str):
        from pipecat.frames.frames import LLMContextFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
        from pipecat.tests.utils import SleepFrame
        from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
        from soft_interrupt import MeaningfulWordsUserTurnStartStrategy

        kontext = LLMContext()
        nutzer, _ = LLMContextAggregatorPair(kontext, user_params=LLMUserAggregatorParams(
            # wie in bot.py: die Startstrategie unterbricht nie selbst (das entscheidet das SoftInterruptGate)
            user_turn_strategies=UserTurnStrategies(
                start=[MeaningfulWordsUserTurnStartStrategy(min_words=1, enable_interruptions=False)],
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.05)])))
        zuege: list[str] = []

        @nutzer.event_handler("on_user_turn_stopped")
        async def _zug_zu(_agg, _strategie, nachricht):
            zuege.append(nachricht.content)

        waechter = StimmWaechter(bereites_gate(), AttrappePruefer(ergebnis))
        frames = [VADUserStartedSpeakingFrame(start_secs=0.2, timestamp=time.time()), zwischenstand(text), SleepFrame(0.05),
                  VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=time.time()), final(text), SleepFrame(0.6)]
        unten, _ = await run_test(Pipeline([waechter, nutzer]), frames_to_send=frames)
        return zuege, [f for f in unten if isinstance(f, LLMContextFrame)], kontext, waechter

    async def test_eigene_aeusserung_ruft_das_modell_auf(self):
        zuege, kontextframes, kontext, _ = await self.lauf(urteil(EIGEN), "Mach die Heizung an")
        self.assertEqual(zuege, ["Mach die Heizung an"])
        self.assertEqual(len(kontextframes), 1)

    async def test_fremde_aeusserung_beendet_den_zug_leer_ohne_modellaufruf(self):
        zuege, kontextframes, kontext, _ = await self.lauf(urteil(FREMD), "Schließ die Tür")
        self.assertEqual([z.strip() for z in zuege], [""])  # genau ein Zug, ohne Satz: bot.py kehrt bei leerem Inhalt zurück
        self.assertEqual(kontextframes, [])
        self.assertEqual(kontext.messages, [])  # nichts im Verlauf


class FesteMarke:
    """Smart-Turn-Ersatz für die Stop-Strategie: liefert der Reihe nach die vorgegebenen Urteile zum Satzende."""

    def __init__(self, *urteile):
        from pipecat.audio.turn.base_turn_analyzer import BaseTurnParams, EndOfTurnState

        self.urteile = list(urteile)
        self.unfertig = EndOfTurnState.INCOMPLETE
        self.speech_triggered = False
        self.params = BaseTurnParams()

    def set_sample_rate(self, rate):
        pass

    def update_vad_start_secs(self, secs):
        pass

    def append_audio(self, buffer, is_speech):
        return self.unfertig

    async def analyze_end_of_turn(self):
        return self.urteile.pop(0), None

    def clear(self):
        pass

    async def cleanup(self):
        pass


class VadStempel(FrameProcessor):
    """Setzt den Zeitstempel der VAD-Meldungen beim Durchgang, wie VAD ihn im Betrieb setzt (die Stop-Strategie rechnet ihre Frist ab dort)."""

    async def process_frame(self, frame, direction):
        from pipecat.frames.frames import VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame

        await super().process_frame(frame, direction)
        if isinstance(frame, (VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)):
            frame.timestamp = time.time()
        await self.push_frame(frame, direction)


class HaltezeitTests(unittest.IsolatedAsyncioTestCase):
    """Der Wächter hält das endgültige Transkript bis zum Urteil an. Die Stop-Strategie wartet nach dem Sprechende aber nur so lange auf ein
    Transkript, wie die Spracherkennung laut ihrer Angabe braucht (STTMetadataFrame, ttfs_p99_latency). Ohne Ausgleich beendete sie einen Zug
    aus zwei Äußerungen mit dem Text der ersten, während die zweite noch beim Wächter stand."""

    async def metadaten(self, modus: str) -> float:
        from pipecat.frames.frames import STTMetadataFrame

        unten, _ = await run_test(StimmWaechter(bereites_gate(modus), AttrappePruefer(None)),
                                  frames_to_send=[STTMetadataFrame(service_name="stt", ttfs_p99_latency=0.35)])
        return next(f.ttfs_p99_latency for f in unten if isinstance(f, STTMetadataFrame))

    async def test_die_stop_strategie_kennt_die_haltezeit(self):
        self.assertAlmostEqual(await self.metadaten("streng"), 0.35 + WARTEN_SECS)

    async def test_ohne_verwerfen_bleibt_die_frist_der_spracherkennung(self):
        for modus in ("beobachten", "aus"):
            self.assertAlmostEqual(await self.metadaten(modus), 0.35, msg=modus)

    async def test_zug_endet_erst_mit_dem_gehaltenen_transkript(self):
        """Zwei Äußerungen in einem Zug, das Urteil zur zweiten kommt 0,8 s nach ihrem Transkript (Smart-Turn: erst unfertig, dann fertig)."""
        from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
        from pipecat.frames.frames import STTMetadataFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
        from pipecat.tests.utils import SleepFrame
        from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import TurnAnalyzerUserTurnStopStrategy
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
        from soft_interrupt import MeaningfulWordsUserTurnStartStrategy

        marke = FesteMarke(EndOfTurnState.INCOMPLETE, EndOfTurnState.COMPLETE)
        nutzer, _ = LLMContextAggregatorPair(LLMContext(), user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[MeaningfulWordsUserTurnStartStrategy(min_words=2, enable_interruptions=False)],
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=marke)])))
        zuege: list[str] = []

        @nutzer.event_handler("on_user_turn_stopped")
        async def _zug_zu(_agg, _strategie, nachricht):
            zuege.append(nachricht.content)

        waechter = StimmWaechter(bereites_gate(), AttrappePruefer(urteil(EIGEN), dauern=[0.0, 0.8]))
        frames = [STTMetadataFrame(service_name="stt", ttfs_p99_latency=0.35),
                  VADUserStartedSpeakingFrame(start_secs=0.2), zwischenstand("Mach die"), SleepFrame(0.05),
                  VADUserStoppedSpeakingFrame(stop_secs=0.2), final("Mach die"), SleepFrame(0.2),
                  VADUserStartedSpeakingFrame(start_secs=0.2), zwischenstand("Heizung an"), SleepFrame(0.05),
                  VADUserStoppedSpeakingFrame(stop_secs=0.2), final("Heizung an"), SleepFrame(2.0)]
        await run_test(Pipeline([VadStempel(), waechter, nutzer]), frames_to_send=frames)
        self.assertEqual(zuege, ["Mach die Heizung an"])


class NurEndgueltigTests(unittest.IsolatedAsyncioTestCase):
    """Befund: Ein Zwischenstand einer fremden Stimme startete den Zug, das SoftInterruptGate brach sofort hart ab (Antwort oder Werkzeug
    weg), danach verwarf der Wächter das endgültige Transkript und WAMO schwieg. Im Modus streng mit Abdruck zählt deshalb, solange WAMO
    nicht spricht, aber Modell oder Werkzeug arbeiten, erst das endgültige Transkript nach dem Urteil des Wächters."""

    async def lauf(self, ergebnis: Urteil | None, *, werkzeug: bool = False, denkt: bool = False, gate: StimmGate | None = None,
                   text: str = "Schließ mal die Tür"):
        from pipecat.frames.frames import (FunctionCallInProgressFrame, InterruptionFrame, LLMContextFrame, VADUserStartedSpeakingFrame,
                                           VADUserStoppedSpeakingFrame)
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
        from pipecat.tests.utils import SleepFrame
        from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
        from soft_interrupt import MeaningfulWordsUserTurnStartStrategy, SoftInterruptGate
        from stimme_waechter import nur_endgueltig
        from turn_wache import TurnWache

        waechter = StimmWaechter(gate or bereites_gate(), AttrappePruefer(ergebnis))
        weiche = SoftInterruptGate()
        wache = TurnWache()
        if denkt:  # eine Anfrage ist beim Modell, die Antwort ist noch nicht zu hören
            self.assertTrue(wache.durchlassen(LLMContextFrame(context=LLMContext(messages=[{"role": "user", "content": "Wetter?"}]))))
        nutzer, _ = LLMContextAggregatorPair(LLMContext(), user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[MeaningfulWordsUserTurnStartStrategy(
                    min_words=2, enable_interruptions=False, werkzeug_laeuft=weiche.werkzeug_laeuft,
                    nur_endgueltig=nur_endgueltig(waechter, weiche.werkzeug_laeuft, wache.modell_beschaeftigt))],
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.05)])))
        frames = [FunctionCallInProgressFrame(function_name="websuche", tool_call_id="w1", arguments={}, cancel_on_interruption=True),
                  SleepFrame(0.05)] if werkzeug else []  # das Werkzeug läuft schon, bevor jemand spricht
        frames += [VADUserStartedSpeakingFrame(start_secs=0.2, timestamp=time.time()), zwischenstand(text), SleepFrame(0.05),
                   VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=time.time()), final(text), SleepFrame(0.6)]
        unten, _ = await run_test(Pipeline([waechter, nutzer, weiche]), frames_to_send=frames)
        abbrueche = sum(isinstance(f, InterruptionFrame) for f in unten)
        kontexte = [f for f in unten if isinstance(f, LLMContextFrame)]
        return abbrueche, kontexte, weiche, wache

    async def test_fremde_stimme_bricht_ein_laufendes_werkzeug_nicht_ab(self):
        abbrueche, kontexte, weiche, _ = await self.lauf(urteil(FREMD), werkzeug=True)
        self.assertEqual(abbrueche, 0)
        self.assertEqual(kontexte, [])
        self.assertTrue(weiche.werkzeug_laeuft())  # das Werkzeug läuft weiter

    async def test_fremde_stimme_bricht_das_denkende_modell_nicht_ab(self):
        abbrueche, kontexte, _, wache = await self.lauf(urteil(FREMD), denkt=True)
        self.assertEqual((abbrueche, kontexte), (0, []))
        self.assertTrue(wache.modell_beschaeftigt())

    async def test_eigene_stimme_bricht_nach_dem_endgueltigen_transkript_ab(self):
        abbrueche, kontexte, _, _ = await self.lauf(urteil(EIGEN), werkzeug=True)
        self.assertEqual(abbrueche, 1)
        self.assertEqual(len(kontexte), 1)
        self.assertEqual(kontexte[0].context.messages[-1]["content"], "Schließ mal die Tür")

    async def test_ohne_laufende_arbeit_bleibt_der_schnelle_weg(self):
        """Nichts läuft: der Zwischenstand startet den Zug wie bisher (ein Abbruch schadet dort nicht, die Erkennung des Satzendes bleibt)."""
        abbrueche, kontexte, _, _ = await self.lauf(urteil(FREMD))
        self.assertEqual((abbrueche, kontexte), (1, []))

    async def test_ohne_abdruck_bleibt_alles_wie_bisher(self):
        gate = bereites_gate()
        gate.setze_abdruck(None)
        abbrueche, kontexte, _, _ = await self.lauf(None, werkzeug=True, gate=gate)
        self.assertEqual((abbrueche, len(kontexte)), (1, 1))

    def strategie(self, *, endgueltig: bool, spricht: bool):
        from soft_interrupt import MeaningfulWordsUserTurnStartStrategy

        s = MeaningfulWordsUserTurnStartStrategy(min_words=2, enable_interruptions=False, nur_endgueltig=lambda: endgueltig)
        s._bot_speaking = spricht
        self.ausgeloest, self.zurueck = [], []

        async def start():
            self.ausgeloest.append(True)

        async def reset():
            self.zurueck.append(True)

        s.trigger_user_turn_started, s.trigger_reset_aggregation = start, reset
        return s

    async def test_zwischenstand_startet_keinen_zug_das_endgueltige_transkript_schon(self):
        s = self.strategie(endgueltig=True, spricht=False)
        ergebnis = await s.process_frame(zwischenstand("Schließ mal die Tür"))
        self.assertEqual((ergebnis.name, self.ausgeloest, self.zurueck), ("CONTINUE", [], []))
        ergebnis = await s.process_frame(final("Schließ mal die Tür"))
        self.assertEqual((ergebnis.name, self.ausgeloest), ("STOP", [True]))

    async def test_beim_sprechen_bleibt_der_schnelle_weg_ueber_zwischenstaende(self):
        s = self.strategie(endgueltig=True, spricht=True)
        ergebnis = await s.process_frame(zwischenstand("Stopp, warte mal"))
        self.assertEqual((ergebnis.name, self.ausgeloest), ("STOP", [True]))

    async def test_ohne_abfrage_zaehlt_der_zwischenstand_wie_bisher(self):
        s = self.strategie(endgueltig=False, spricht=False)
        ergebnis = await s.process_frame(zwischenstand("Schließ"))
        self.assertEqual((ergebnis.name, self.ausgeloest), ("STOP", [True]))


if __name__ == "__main__":
    unittest.main()
