"""Tests für die Turn-Wache gegen doppelte Antworten (turn_wache.py).

Nachgestellt wird die Ursache vom 20.10.2026 mit dem echten Assistent-Aggregator von Pipecat: Werkzeugergebnis,
während WAMO spricht, die Person redet weiter, danach ein zweites "Bot stopped speaking". Ohne Wache schiebt der
Aggregator dann den unveränderten Verlauf noch einmal zum Modell; mit Wache kommt diese Anfrage nicht an.
"""

import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallFromLLM,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import SleepFrame, run_test

from turn_wache import TurnWache, verlauf_stand


def kontext() -> LLMContext:
    return LLMContext(messages=[
        {"role": "assistant", "content": "Hallo, WAMO ist bereit."},
        {"role": "user", "content": "Welches Betriebssystem läuft auf meinem PC?"},
    ])


class VerlaufStandTest(unittest.TestCase):
    def test_gleicher_verlauf_gleicher_stand_neue_nachricht_neuer_stand(self):
        ctx = kontext()
        a = verlauf_stand(ctx)
        self.assertEqual(a, verlauf_stand(ctx))
        ctx.add_message({"role": "user", "content": "Schau bitte nach."})
        self.assertNotEqual(a, verlauf_stand(ctx))

    def test_geaenderter_inhalt_derselben_nachricht_zaehlt_als_neu(self):
        ctx = kontext()
        a = verlauf_stand(ctx)
        ctx.messages[-1]["content"] += " Schau bitte nach."
        self.assertNotEqual(a, verlauf_stand(ctx))

    def test_leerer_verlauf(self):
        self.assertIsNone(verlauf_stand(LLMContext()))


class TurnWacheLogikTest(unittest.TestCase):
    def test_zweite_gleiche_anfrage_wird_verworfen_auch_aus_der_anderen_richtung(self):
        w, ctx = TurnWache(), kontext()
        self.assertTrue(w.durchlassen(LLMContextFrame(context=ctx)))  # vom Nutzer-Aggregator (vorn)
        self.assertFalse(w.durchlassen(LLMContextFrame(context=ctx)))  # vom Assistent-Aggregator (hinten)
        self.assertEqual(w.verworfen, 1)

    def test_werkzeugergebnis_ist_neue_lage(self):
        w, ctx = TurnWache(), kontext()
        w.durchlassen(LLMContextFrame(context=ctx))
        ctx.add_message({"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "system_info", "input": {}}]})
        ctx.add_message({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Windows"}]})
        self.assertTrue(w.durchlassen(LLMContextFrame(context=ctx)))

    def test_verlauf_mit_wamos_antwort_am_ende_wird_nicht_angefragt(self):
        w, ctx = TurnWache(), kontext()
        self.assertTrue(w.durchlassen(LLMContextFrame(context=ctx)))
        ctx.add_message({"role": "assistant", "content": "Auf deinem PC läuft Windows 11."})
        self.assertFalse(w.durchlassen(LLMContextFrame(context=ctx)))
        ctx.add_message({"role": "user", "content": "Danke. Und wie spät ist es?"})
        self.assertTrue(w.durchlassen(LLMContextFrame(context=ctx)))

    def test_nach_unterbrechung_darf_derselbe_stand_wieder(self):
        w, ctx = TurnWache(), kontext()
        w.durchlassen(LLMContextFrame(context=ctx))
        w.unterbrochen()
        self.assertTrue(w.durchlassen(LLMContextFrame(context=ctx)))


class TurnWacheProzessorTest(unittest.IsolatedAsyncioTestCase):
    async def test_doppelte_anfrage_kommt_nicht_beim_modell_an(self):
        w, ctx = TurnWache(), kontext()
        unten, _ = await run_test(
            Pipeline([w.vorne(), w.hinten()]),
            frames_to_send=[LLMContextFrame(context=ctx), LLMContextFrame(context=ctx)],
        )
        self.assertEqual(sum(isinstance(f, LLMContextFrame) for f in unten), 1)

    async def test_unterbrechung_laesst_neue_anfrage_durch(self):
        w, ctx = TurnWache(), kontext()
        unten, _ = await run_test(
            Pipeline([w.vorne(), w.hinten()]),
            frames_to_send=[LLMContextFrame(context=ctx), SleepFrame(0.05), InterruptionFrame(), SleepFrame(0.05),
                            LLMContextFrame(context=ctx)],
        )
        self.assertEqual(sum(isinstance(f, LLMContextFrame) for f in unten), 2)


def werkzeug_frames(ctx: LLMContext) -> list:
    aufruf = FunctionCallFromLLM(function_name="system_info", tool_call_id="t1", arguments={}, context=ctx)
    return [
        FunctionCallsStartedFrame(function_calls=[aufruf]),
        FunctionCallInProgressFrame(function_name="system_info", tool_call_id="t1", arguments={}),
        FunctionCallResultFrame(function_name="system_info", tool_call_id="t1", arguments={}, result={"os": "Windows"}),
    ]


class UrsacheTest(unittest.IsolatedAsyncioTestCase):
    """Nachstellung mit dem echten Assistent-Aggregator: Wie oft wird der Verlauf zum Modell geschoben?"""

    def ablauf(self, ctx: LLMContext, antwort_im_verlauf: bool = False) -> list:
        antwort = (
            [LLMFullResponseStartFrame(), LLMTextFrame("Auf deinem PC läuft Windows 11."), LLMFullResponseEndFrame()]
            if antwort_im_verlauf else []
        )
        schritte = [
            BotStartedSpeakingFrame(),  # WAMO sagt "Moment, ich schau nach."
            *werkzeug_frames(ctx),  # Ergebnis kommt, während WAMO spricht: Anschieben wird vorgemerkt
            UserStartedSpeakingFrame(),  # die Person redet weiter: "Schau bitte nach."
            BotStoppedSpeakingFrame(),  # WAMO fertig, Person spricht noch: nicht angeschoben, Marke bleibt
            UserStoppedSpeakingFrame(),  # neuer Zug der Person, der eine eigene Anfrage auslöst
            BotStartedSpeakingFrame(),  # WAMO beantwortet den neuen Zug
            *antwort,  # wahlweise steht die Antwort schon im Verlauf, bevor WAMO zu Ende gesprochen hat
            BotStoppedSpeakingFrame(),  # alte Marke wirkt: Verlauf wird noch einmal angeschoben
        ]
        # Systemframes überholen sonst die übrigen; im Gespräch liegen zwischen den Schritten echte Pausen
        mit_pausen = []
        for s in schritte:
            mit_pausen += [s, SleepFrame(0.1)]
        return mit_pausen

    async def test_ohne_wache_schiebt_der_aggregator_nach_der_antwort_noch_einmal_an(self):
        ctx = kontext()
        _, assistent = LLMContextAggregatorPair(ctx)
        _, oben = await run_test(assistent, frames_to_send=self.ablauf(ctx))
        anfragen = [f for f in oben if isinstance(f, LLMContextFrame)]
        self.assertEqual(len(anfragen), 1, "Ursache: verspätetes Anschieben nach dem zweiten Bot stopped speaking")

    async def test_mit_wache_kommt_die_doppelte_anfrage_nicht_an(self):
        ctx = kontext()
        _, assistent = LLMContextAggregatorPair(ctx)
        w = TurnWache()
        neuer_zug = NeuerZug(ctx, w)  # stellt die Anfrage des neuen Zugs nach, wie sie vorn an der Wache vorbeigeht
        _, oben = await run_test(Pipeline([neuer_zug, w.hinten(), assistent]), frames_to_send=self.ablauf(ctx))
        self.assertTrue(neuer_zug.angefragt)
        self.assertFalse([f for f in oben if isinstance(f, LLMContextFrame)])
        self.assertEqual(w.verworfen, 1)

    async def test_mit_wache_auch_wenn_die_antwort_schon_im_verlauf_steht(self):
        ctx = kontext()
        _, assistent = LLMContextAggregatorPair(ctx)
        w = TurnWache()
        neuer_zug = NeuerZug(ctx, w)
        _, oben = await run_test(Pipeline([neuer_zug, w.hinten(), assistent]),
                                 frames_to_send=self.ablauf(ctx, antwort_im_verlauf=True))
        self.assertEqual(ctx.messages[-1]["role"], "assistant", "Antwort steht vor dem verspäteten Anschieben im Verlauf")
        # Hier löscht Pipecat die Marke beim Eintragen der Antwort selbst; die Wache muss nichts verwerfen, und es
        # entsteht trotzdem keine zweite Anfrage
        self.assertFalse([f for f in oben if isinstance(f, LLMContextFrame)])


class NeuerZug(FrameProcessor):
    """Beim Ende des neuen Zugs: Beitrag der Person in den Verlauf und Anfrage an der Wache vorbei (wie vorn)."""

    def __init__(self, ctx: LLMContext, wache: TurnWache):
        super().__init__()
        self._ctx = ctx
        self._wache = wache
        self.angefragt = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStoppedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
            self._ctx.add_message({"role": "user", "content": "Schau bitte nach."})
            self.angefragt = self._wache.durchlassen(LLMContextFrame(context=self._ctx))
        await self.push_frame(frame, direction)


if __name__ == "__main__":
    unittest.main()
