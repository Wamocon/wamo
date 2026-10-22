"""Tests für den Wartesatz (wartesatz.py): sprechen nur bei langen Werkzeugaufrufen und nie doppelt."""

import asyncio
import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from wartesatz import SAETZE_STANDARD, SAETZE_SUCHE, Wartesatz, satz_fuer, wartesatz_an, wartesatz_secs


def aufruf(name="websuche", nr="t1"):
    return FunctionCallInProgressFrame(function_name=name, tool_call_id=nr, arguments={}, cancel_on_interruption=False)


def ergebnis(name="websuche", nr="t1"):
    return FunctionCallResultFrame(function_name=name, tool_call_id=nr, arguments={}, result="ok")


class WartesatzTest(unittest.IsolatedAsyncioTestCase):
    def wache(self, nach=0.05):
        w = Wartesatz(nach_secs=nach)
        self.gesendet = []

        async def push(frame, direction=FrameDirection.DOWNSTREAM):
            self.gesendet.append(frame)

        w.push_frame = push
        w.create_task = lambda coro, name=None: asyncio.ensure_future(coro)

        async def senden(frame, richtung=FrameDirection.DOWNSTREAM):
            await w.process_frame(frame, richtung)

        self.senden = senden
        return w

    def saetze(self):
        return [f for f in self.gesendet if isinstance(f, TTSSpeakFrame)]

    async def test_langer_aufruf_ohne_text_bekommt_einen_wartesatz(self):
        w = self.wache()
        await self.senden(LLMFullResponseStartFrame())
        await self.senden(aufruf())
        await asyncio.sleep(0.12)
        s = self.saetze()
        self.assertEqual(len(s), 1)
        self.assertIn(s[0].text, SAETZE_SUCHE)
        self.assertFalse(s[0].append_to_context, "der Wartesatz gehört nicht in den Verlauf")
        self.assertEqual(w.gesprochen, 1)

    async def test_schneller_aufruf_bleibt_still(self):
        self.wache(nach=0.1)
        await self.senden(aufruf("aktuelle_zeit"))
        await asyncio.sleep(0.02)
        await self.senden(ergebnis("aktuelle_zeit"))
        await asyncio.sleep(0.15)
        self.assertEqual(self.saetze(), [])

    async def test_hat_das_modell_schon_gesprochen_kein_wartesatz(self):
        self.wache()
        await self.senden(LLMFullResponseStartFrame())
        await self.senden(LLMTextFrame("● Moment, ich schau nach."))
        await self.senden(aufruf())
        await asyncio.sleep(0.12)
        self.assertEqual(self.saetze(), [])

    async def test_nur_der_marker_zaehlt_nicht_als_text(self):
        self.wache()
        await self.senden(LLMFullResponseStartFrame())
        await self.senden(LLMTextFrame("● "))
        await self.senden(aufruf())
        await asyncio.sleep(0.12)
        self.assertEqual(len(self.saetze()), 1)

    async def test_spricht_wamo_schon_kein_wartesatz(self):
        self.wache()
        await self.senden(BotStartedSpeakingFrame())
        await self.senden(aufruf())
        await asyncio.sleep(0.12)
        self.assertEqual(self.saetze(), [])

    async def test_hoechstens_einer_je_kette_und_nie_zweimal_derselbe(self):
        self.wache()
        await self.senden(LLMFullResponseStartFrame())
        await self.senden(aufruf(nr="a"))
        await self.senden(aufruf(nr="b"))
        await asyncio.sleep(0.12)
        self.assertEqual(len(self.saetze()), 1)

    async def test_unterbrechung_verwirft_den_wartesatz(self):
        self.wache()
        await self.senden(aufruf())
        await self.senden(InterruptionFrame())
        await asyncio.sleep(0.12)
        self.assertEqual(self.saetze(), [])

    async def test_alle_frames_gehen_weiter(self):
        self.wache(nach=5)
        frame = aufruf()
        await self.senden(frame)
        self.assertIn(frame, self.gesendet)

    def test_satzwahl_wiederholt_sich_nicht(self):
        for zuletzt in SAETZE_STANDARD:
            self.assertNotEqual(satz_fuer("powershell_lesen", zuletzt), zuletzt)
        self.assertIn(satz_fuer("websuche", None), SAETZE_SUCHE)

    def test_schalter(self):
        self.assertTrue(wartesatz_an("an"))
        self.assertFalse(wartesatz_an(""), "Standard aus")
        self.assertFalse(wartesatz_an("aus"))
        self.assertEqual(wartesatz_secs("1.5"), 1.5)
        self.assertEqual(wartesatz_secs("x"), 0.9)
        self.assertEqual(wartesatz_secs(""), 0.9)
        self.assertEqual(wartesatz_secs("0"), 0.1)


if __name__ == "__main__":
    unittest.main()
