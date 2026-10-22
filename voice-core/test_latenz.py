"""Tests für die Latenzmessung je Zug (latenz.py)."""

import unittest
from types import SimpleNamespace

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    FunctionCallInProgressFrame,
    LLMFullResponseStartFrame,
    LLMMarkerFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from latenz import LatenzObserver, api_messen, latenz_log_an


class Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def push(frame, richtung=FrameDirection.DOWNSTREAM):
    return SimpleNamespace(frame=frame, direction=richtung, source=None, destination=None, timestamp=0)


class LatenzObserverTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.uhr = Uhr()
        self.zuege = []
        self.obs = LatenzObserver(uhr=self.uhr, melden=self.zuege.append)

    async def schritt(self, dt: float, frame):
        self.uhr.t += dt
        await self.obs.on_push_frame(push(frame))

    async def zug(self, werkzeug: str | None = None):
        await self.schritt(0.0, VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=self.uhr.t + 0.2))
        self.uhr.t += 0.2
        await self.schritt(0.1, TranscriptionFrame("Wie spät ist es?", "u", "t"))
        await self.schritt(0.5, UserStoppedSpeakingFrame())
        await self.schritt(0.01, LLMFullResponseStartFrame())
        await self.schritt(0.3, LLMTextFrame("●"))
        await self.schritt(0.05, LLMTextFrame(" Es ist"))
        if werkzeug:
            await self.schritt(0.01, FunctionCallInProgressFrame(function_name=werkzeug, tool_call_id="1", arguments={}))
        await self.schritt(0.2, TTSStartedFrame())
        await self.schritt(0.15, TTSAudioRawFrame(b"\x00\x00", 24000, 1))
        await self.schritt(0.02, BotStartedSpeakingFrame())

    async def test_abschnitte_relativ_zum_sprechende(self):
        await self.zug()
        self.assertEqual(len(self.zuege), 1)
        ms = self.zuege[0].ms()
        self.assertEqual(ms["stt_final"], 300)
        self.assertEqual(ms["turn_ende"], 800)
        self.assertEqual(ms["llm_start"], 810)
        self.assertEqual(ms["llm_token"], 1160, "der Marker allein zählt nicht als erstes Token")
        self.assertEqual(ms["tts_satz"], 1360)
        self.assertEqual(ms["tts_audio"], 1510)
        self.assertEqual(ms["ausgabe"], 1530)

    async def test_begruessung_ohne_zug_wird_nicht_gemeldet(self):
        await self.schritt(0.0, TTSStartedFrame())
        await self.schritt(0.1, TTSAudioRawFrame(b"\x00\x00", 24000, 1))
        await self.schritt(0.0, BotStartedSpeakingFrame())
        self.assertEqual(self.zuege, [])

    async def test_zuege_werden_gezaehlt_und_nur_einmal_gemeldet(self):
        await self.zug()
        await self.schritt(0.5, BotStartedSpeakingFrame())
        await self.zug(werkzeug="aktuelle_zeit")
        self.assertEqual([z.nummer for z in self.zuege], [1, 2])
        self.assertEqual(self.zuege[1].werkzeuge, ["aktuelle_zeit"])

    async def test_weitersprechen_nach_turn_ende_wird_markiert(self):
        await self.schritt(0.0, VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=self.uhr.t))
        await self.schritt(0.5, UserStoppedSpeakingFrame())
        await self.schritt(0.01, LLMFullResponseStartFrame())
        await self.schritt(0.2, VADUserStartedSpeakingFrame())
        await self.schritt(0.5, VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=self.uhr.t))
        await self.schritt(0.5, UserStoppedSpeakingFrame())
        await self.schritt(0.01, LLMFullResponseStartFrame())
        await self.schritt(0.3, BotStartedSpeakingFrame())
        self.assertEqual(len(self.zuege), 1)
        self.assertTrue(self.zuege[0].nachgesprochen)
        self.assertEqual(self.zuege[0].antworten, 2)

    async def test_pause_vor_dem_turn_ende_nimmt_das_spaetere_sprechende(self):
        await self.schritt(0.0, VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=self.uhr.t))
        await self.schritt(0.3, VADUserStartedSpeakingFrame())
        self.uhr.t += 1.0
        await self.schritt(0.0, VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=self.uhr.t))
        start = self.uhr.t - 0.2
        await self.schritt(0.4, UserStoppedSpeakingFrame())
        await self.schritt(0.0, LLMFullResponseStartFrame())
        await self.schritt(0.0, BotStartedSpeakingFrame())
        self.assertEqual(self.zuege[0].nummer, 1)
        self.assertEqual(self.zuege[0].ms()["turn_ende"], round((self.uhr.t - start) * 1000))

    async def test_upstream_wird_ignoriert(self):
        await self.obs.on_push_frame(push(VADUserStoppedSpeakingFrame(stop_secs=0.2), FrameDirection.UPSTREAM))
        self.assertIsNone(self.obs._zug)

    async def test_marker_und_api_marken(self):
        await self.schritt(0.0, VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=self.uhr.t + 0.2))
        self.uhr.t += 0.2
        await self.schritt(0.1, TranscriptionFrame("Wie spät ist es?", "u", "t"))
        self.uhr.t += 0.03
        self.obs.marke("api_senden")
        await self.schritt(0.0, LLMFullResponseStartFrame())
        self.uhr.t += 0.25
        self.obs.marke("api_antwort")
        await self.schritt(0.4, LLMMarkerFrame("●"))
        await self.schritt(0.0, UserStoppedSpeakingFrame())
        await self.schritt(0.0, LLMTextFrame(" Es ist"))
        await self.schritt(0.01, TTSStartedFrame())
        await self.schritt(0.3, TTSAudioRawFrame(b"\x00\x00", 24000, 1))
        await self.schritt(0.0, BotStartedSpeakingFrame())
        ms = self.zuege[0].ms()
        self.assertEqual([ms[k] for k in ("llm_start", "api_senden", "api_antwort", "llm_marker")], [330, 330, 580, 980])
        self.assertEqual(ms["llm_token"], 980)
        self.assertEqual(list(ms), ["stt_final", "llm_start", "api_senden", "api_antwort", "llm_marker", "turn_ende",
                                    "llm_token", "tts_satz", "tts_audio", "ausgabe"])

    async def test_api_marke_ohne_zug_wird_ignoriert(self):
        self.obs.marke("api_senden")
        self.assertIsNone(self.obs._zug)

    async def test_api_messen_umhuellt_nur_streams(self):
        aufrufe = []

        class Nachrichten:
            async def create(self, **kw):
                aufrufe.append(kw)
                return "antwort"

        llm = SimpleNamespace(_client=SimpleNamespace(beta=SimpleNamespace(messages=Nachrichten())))
        self.assertTrue(api_messen(llm, self.obs))
        await self.schritt(0.0, VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=self.uhr.t))
        await self.schritt(0.1, TranscriptionFrame("Hallo", "u", "t"))
        self.assertEqual(await llm._client.beta.messages.create(stream=False, model="m"), "antwort")
        self.assertNotIn("api_senden", self.obs._zug.marken)
        self.assertEqual(await llm._client.beta.messages.create(stream=True, model="m"), "antwort")
        self.assertIn("api_senden", self.obs._zug.marken)
        self.assertIn("api_antwort", self.obs._zug.marken)
        self.assertEqual([a["stream"] for a in aufrufe], [False, True])

    def test_api_messen_ohne_client(self):
        self.assertFalse(api_messen(SimpleNamespace(), self.obs))

    def test_schalter(self):
        self.assertTrue(latenz_log_an("1"))
        self.assertTrue(latenz_log_an("an"))
        self.assertFalse(latenz_log_an(""))
        self.assertFalse(latenz_log_an("aus"))


if __name__ == "__main__":
    unittest.main()
