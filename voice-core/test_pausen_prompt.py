"""Tests für die Texte nach einer Pause (bot.pause_prompts, WAMO_PAUSEN_PROMPT).

Ziel: keine Floskeln wie "Ich bin noch da", nach der kurzen Pause erledigen oder still weiter warten (nur ○), erst die lange
Stille bekommt genau eine konkrete Frage. Die Kette darf nicht endlos laufen: der lange Text verbietet ◐ und ○.
"""

import asyncio
import re
import unittest
from unittest import mock

from pipecat.processors.frame_processor import FrameProcessor
from pipecat.turns.user_turn_completion_mixin import (
    IncompleteType,
    UserTurnCompletionConfig,
    UserTurnCompletionLLMServiceMixin,
)

from bot import pause_prompts, pausen_prompt_an

FLOSKELN = re.compile(r"bin noch da|ich höre zu|sag ruhig weiter|ich hör zu", re.IGNORECASE)


class _Modell(UserTurnCompletionLLMServiceMixin, FrameProcessor):
    """Nur der Teil von Pipecat, der Marker liest und die Fristen setzt."""


class PausenPromptTest(unittest.TestCase):
    def test_standard_ist_aus_und_liefert_die_alten_texte(self):
        self.assertFalse(pausen_prompt_an(""))
        kurz, lang = pause_prompts("Max", neu=False)
        self.assertIn("Beispiel: ● Bin noch da, sag einfach Bescheid.", lang)
        self.assertIn("Nie ◐ oder ○", kurz)
        self.assertEqual(pause_prompts("Max"), (kurz, lang), "ohne gesetzte Umgebungsvariable gilt aus")

    def test_schalter(self):
        self.assertTrue(pausen_prompt_an("an"))
        self.assertFalse(pausen_prompt_an("aus"))

    def test_neu_ohne_floskeln_und_ohne_beispielsatz(self):
        kurz, lang = pause_prompts("Max", neu=True)
        for text in (kurz, lang):
            self.assertIsNone(FLOSKELN.search(text.replace("noch da bist", "").replace("zuhörst", "")))
            self.assertNotIn("Beispiel", text, "ein Beispielsatz wird vom Modell wörtlich nachgesprochen")
            self.assertIn("Max", text)
        self.assertIn("Sag nie, dass du noch da bist", kurz)
        self.assertIn("Sag nicht, dass du noch da bist", lang)

    def test_kurze_pause_erledigt_oder_wartet_still(self):
        kurz, _ = pause_prompts("Max", neu=True)
        self.assertIn("erledige es jetzt", kurz)
        self.assertIn("schreib nur ○ und sonst nichts", kurz)
        self.assertIn("Nie ◐", kurz)
        self.assertNotIn("Nie ◐ oder ○", kurz, "○ ist hier ausdrücklich erlaubt")

    def test_lange_stille_genau_eine_konkrete_frage_und_kein_weiteres_warten(self):
        _, lang = pause_prompts("Max", neu=True)
        self.assertIn("genau eine konkrete Frage", lang)
        self.assertIn("Beginne mit ● und einem Leerzeichen", lang)
        self.assertIn("Nie ◐ oder ○, nie ● allein", lang)

    def test_keine_gedankenstriche(self):
        for text in pause_prompts("Max", neu=True):
            self.assertNotIn("–", text)
            self.assertNotIn("—", text)
            self.assertNotIn(" - ", text)


class PausenKetteTest(unittest.IsolatedAsyncioTestCase):
    """Das Verhalten von Pipecat 1.12 mit den neuen Texten: ○ nach der kurzen Pause wartet still und fragt später genau einmal."""

    def modell(self):
        kurz, lang = pause_prompts("Max", neu=True)
        m = _Modell()
        m.set_user_turn_completion_config(
            UserTurnCompletionConfig(
                incomplete_short_timeout=1.5, incomplete_long_timeout=3.0,
                incomplete_short_prompt=kurz, incomplete_long_prompt=lang,
            )
        )
        m.fristen = []
        m.gepusht = []

        async def frist(art):
            m.fristen.append(art)

        async def push(frame, direction=None):
            m.gepusht.append(frame)

        m._start_incomplete_timeout = frist
        m.push_frame = push
        return m

    async def test_runde_marker_startet_die_lange_frist_und_spricht_nichts(self):
        m = self.modell()
        await m._push_turn_text("○")
        self.assertEqual(m.fristen, [IncompleteType.LONG])
        self.assertFalse([f for f in m.gepusht if f.__class__.__name__ == "LLMTextFrame"], "nichts wird gesprochen")

    async def test_nach_der_langen_frist_kommt_der_lange_text_und_die_antwort_wird_gesprochen(self):
        m = self.modell()
        gesendet = []

        async def push(frame, direction=None):
            gesendet.append(frame)

        m.push_frame = push
        m._turn_reset = mock.AsyncMock()
        m._incomplete_timeout_handler.__func__  # Methode existiert in Pipecat 1.12
        await UserTurnCompletionLLMServiceMixin._incomplete_timeout_handler(m, IncompleteType.LONG, 0.0)
        nachrichten = [m for f in gesendet for m in getattr(f, "messages", [])]
        self.assertEqual(len(nachrichten), 1)
        _, lang = pause_prompts("Max", neu=True)
        self.assertEqual(nachrichten[0]["content"], lang)
        # die Antwort mit ● wird gesprochen, danach folgt keine weitere Frist
        m2 = self.modell()
        await m2._push_turn_text("● Welche Datei meinst du?")
        self.assertEqual(m2.fristen, [], "nach ● läuft keine Frist")
        self.assertTrue([f for f in m2.gepusht if f.__class__.__name__ == "LLMTextFrame"])

    async def test_ein_gebrochener_marker_in_der_langen_antwort_wuerde_nur_still_halten(self):
        # Fällt das Modell trotz Verbot auf ○, startet Pipecat wieder eine Frist; der Text verbietet es ausdrücklich.
        m = self.modell()
        await m._push_turn_text("○")
        await m._push_turn_text("○")
        self.assertEqual(m.fristen, [IncompleteType.LONG], "ein zweiter Marker derselben Antwort wird unterdrückt")


if __name__ == "__main__":
    unittest.main()
