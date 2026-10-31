"""Tests für den Wartesatz (wartesatz.py): sprechen nur bei langen Werkzeugaufrufen und nie doppelt."""

import asyncio
import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMarkerFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from wartesatz import (
    SAETZE_MODELL,
    SAETZE_STANDARD,
    SAETZE_SUCHE,
    Wartesatz,
    satz_fuer,
    wartesatz_an,
    wartesatz_modell_an,
    wartesatz_modell_secs,
    wartesatz_secs,
)


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


class WartesatzModellTest(unittest.IsolatedAsyncioTestCase):
    """WAMO_WARTESATZ_MODELL: ein Satz, wenn das Modell nach der Anfrage kein einziges Zeichen liefert."""

    def wache(self, modell=0.05, werkzeug=False):
        w = Wartesatz(nach_secs=0.05, werkzeug=werkzeug, modell_secs=modell)
        self.gesendet = []

        async def push(frame, direction=FrameDirection.DOWNSTREAM):
            self.gesendet.append(frame)

        w.push_frame = push
        w.create_task = lambda coro, name=None: asyncio.ensure_future(coro)

        async def senden(frame, richtung=FrameDirection.DOWNSTREAM):
            await w.process_frame(frame, richtung)

        self.senden = senden
        self.w = w
        return w

    def saetze(self):
        return [f for f in self.gesendet if isinstance(f, TTSSpeakFrame)]

    async def test_schweigt_das_modell_kommt_ein_satz_nicht_im_verlauf(self):
        self.wache()
        await self.senden(LLMFullResponseStartFrame())
        await asyncio.sleep(0.12)
        s = self.saetze()
        self.assertEqual(len(s), 1)
        self.assertIn(s[0].text, SAETZE_MODELL)
        self.assertFalse(s[0].append_to_context)

    async def test_schnelle_antwort_bleibt_still(self):
        self.wache(modell=0.1)
        await self.senden(LLMFullResponseStartFrame())
        await asyncio.sleep(0.02)
        await self.senden(LLMTextFrame("● Paris."))
        await asyncio.sleep(0.15)
        self.assertEqual(self.saetze(), [])

    async def test_unvollstaendiger_satz_der_person_loest_nichts_aus(self):
        # Marker ◐ oder ○: das Modell antwortet ohne Text (nur ein Marker-Frame), die Person denkt noch
        self.wache(modell=0.1)
        await self.senden(LLMFullResponseStartFrame())
        await self.senden(LLMMarkerFrame("○", append_to_context_immediately=True))
        await self.senden(LLMFullResponseEndFrame())
        await asyncio.sleep(0.15)
        self.assertEqual(self.saetze(), [])

    async def test_antwortende_stoppt_die_uhr(self):
        self.wache(modell=0.1)
        await self.senden(LLMFullResponseStartFrame())
        await self.senden(LLMFullResponseEndFrame())
        await asyncio.sleep(0.15)
        self.assertEqual(self.saetze(), [])

    async def test_werkzeugaufruf_stoppt_die_uhr_und_ohne_werkzeugschalter_kein_werkzeugsatz(self):
        self.wache(modell=0.1, werkzeug=False)
        await self.senden(LLMFullResponseStartFrame())
        await self.senden(aufruf("aktuelle_zeit"))
        await asyncio.sleep(0.15)
        self.assertEqual(self.saetze(), [])

    async def test_spricht_wamo_schon_kein_satz(self):
        self.wache()
        await self.senden(BotStartedSpeakingFrame())
        await self.senden(LLMFullResponseStartFrame())
        await asyncio.sleep(0.12)
        self.assertEqual(self.saetze(), [])

    async def test_unterbrechung_verwirft_den_satz(self):
        self.wache()
        await self.senden(LLMFullResponseStartFrame())
        await self.senden(InterruptionFrame())
        await asyncio.sleep(0.12)
        self.assertEqual(self.saetze(), [])

    async def test_hoechstens_einer_je_zug(self):
        self.wache()
        await self.senden(LLMFullResponseStartFrame())
        await asyncio.sleep(0.1)
        await self.senden(LLMFullResponseStartFrame())  # zweite Anfrage ohne Text dazwischen
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.saetze()), 1)

    async def test_ohne_modell_secs_bleibt_es_still(self):
        self.wache(modell=None)
        await self.senden(LLMFullResponseStartFrame())
        await asyncio.sleep(0.15)
        self.assertEqual(self.saetze(), [])

    def test_schalter_und_zeit(self):
        self.assertFalse(wartesatz_modell_an(""), "Standard aus")
        self.assertTrue(wartesatz_modell_an("an"))
        self.assertFalse(wartesatz_modell_an("aus"))
        self.assertEqual(wartesatz_modell_secs(""), 2.5)
        self.assertEqual(wartesatz_modell_secs("1,2"), 1.2)
        self.assertEqual(wartesatz_modell_secs("x"), 2.5)
        self.assertEqual(wartesatz_modell_secs("0"), 0.5)

    def test_satzwahl_modell_ohne_wiederholung_und_ohne_gedankenstrich(self):
        for zuletzt in SAETZE_MODELL:
            self.assertNotEqual(satz_fuer("modell", zuletzt), zuletzt)
        for satz in SAETZE_MODELL:
            self.assertNotIn(" - ", satz)
            self.assertNotIn("–", satz)


if __name__ == "__main__":
    unittest.main()
