"""Tests für den Ja-Wächter (ja_waechter.py): Hinweis nur nach einem Ja auf eine echte Rückfrage, nie bei Freigaben."""

import unittest

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test

from ja_waechter import (
    HINWEIS,
    JaWaechter,
    freigabe_im_verlauf,
    hinweis_noetig,
    ist_kurzes_ja,
    ist_rueckfrage,
    ja_waechter_an,
)


def verlauf(frage, antwort="Ja"):
    return [
        {"role": "user", "content": "Mach das mit dem Projekt."},
        {"role": "assistant", "content": frage},
        {"role": "user", "content": antwort},
    ]


class KurzesJaTest(unittest.TestCase):
    def test_zustimmung(self):
        for t in ("Ja", "Ja.", "ja bitte", "Ja, gerne", "Mach das", "Okay, mach es", "Klar", "Genau", "Yes", "Ja ja", "Tu es"):
            self.assertTrue(ist_kurzes_ja(t), t)

    def test_mehrdeutiges_ist_kein_ja(self):
        for t in ("Sicher?", "Klar?", "Genau?", "Ja?", "Ja, aber nur den ersten", "Nein danke", "Ja, nein", "Doch", "Ja, warte kurz", "Ja lieber morgen",
                  "Ja aber nicht jetzt", "Mach das nicht", "Ja, und dann öffne bitte noch den Kalender", "Nein", "", "Hmm",
                  "Danke"):
            self.assertFalse(ist_kurzes_ja(t), t)

    def test_nachrichten_von_wamo_selbst_sind_kein_ja(self):
        for t in ("[PC-Freigabe: ja]", "[Hinweis für WAMO: ja]", "[ja]"):
            self.assertFalse(ist_kurzes_ja(t), t)


class RueckfrageTest(unittest.TestCase):
    def test_echte_rueckfragen(self):
        for t in ("Soll ich das Projekt öffnen?", "● Ich habe die Datei gefunden. Soll ich sie öffnen?", "Soll ich das tun",
                  "Möchtest du, dass ich weitersuche"):
            self.assertTrue(ist_rueckfrage(t), t)

    def test_keine_rueckfrage(self):
        for t in ("Ich habe das Projekt geöffnet.", "Die Suche ist fertig. Das Wetter wird gut.", "",
                  "Welche Datei meinst du?", "Chrome oder Edge?", "Wie soll das heißen?"):
            self.assertFalse(ist_rueckfrage(t), t)

    def test_freigabe_und_sicherheit_sind_keine_rueckfrage(self):
        for t in ("Soll ich die Freigabe in der Oberfläche anfordern?", "Wartet die Karte auf dich?",
                  "Soll ich den Code eingeben?", "Soll ich das Passwort tippen?", "Darf ich den Schlüssel nutzen?",
                  "Ist das aus Sicherheitsgründen in Ordnung?", "Soll ich die Mail an Herrn Meier senden?",
                  "Soll ich die Datei löschen?", "Soll ich das bestellen?", "Soll ich die Nachricht abschicken?",
                  "Darf ich das genehmigen?", "Soll ich das veröffentlichen?", "Soll ich das mit sudo ausführen?"):
            self.assertFalse(ist_rueckfrage(t), t)


class HinweisTest(unittest.TestCase):
    def test_ja_auf_rueckfrage_bekommt_den_hinweis(self):
        self.assertTrue(hinweis_noetig(verlauf("Soll ich das Projekt öffnen?")))

    def test_ja_ohne_rueckfrage_keiner(self):
        self.assertFalse(hinweis_noetig(verlauf("Ich habe das Projekt geöffnet.")))

    def test_kein_ja_keiner(self):
        self.assertFalse(hinweis_noetig(verlauf("Soll ich das Projekt öffnen?", "Nein danke")))
        self.assertFalse(hinweis_noetig(verlauf("Soll ich das Projekt öffnen?", "Ja, aber nur lesen")))

    def test_nur_einmal(self):
        m = verlauf("Soll ich das Projekt öffnen?", "Ja " + HINWEIS)
        self.assertFalse(hinweis_noetig(m))

    def test_offene_freigabe_sperrt_den_hinweis(self):
        self.assertFalse(hinweis_noetig(verlauf("Soll ich das Projekt öffnen?"), offen=True))

    def test_freigabe_meldung_im_verlauf_sperrt_den_hinweis(self):
        m = [
            {"role": "user", "content": "Schick die Mail ab."},
            {"role": "tool", "content": '{"status": "needs_approval", "approval_id": "x"}'},
            {"role": "assistant", "content": "Das muss in der Oberfläche freigegeben werden. Soll ich warten?"},
            {"role": "user", "content": "Ja"},
        ]
        self.assertTrue(freigabe_im_verlauf(m[:-1]))
        self.assertFalse(hinweis_noetig(m))
        m2 = [
            {"role": "user", "content": "Tipp das."},
            {"role": "tool", "content": "status=wartet"},
            {"role": "assistant", "content": "Soll ich es später noch einmal versuchen?"},
            {"role": "user", "content": "Ja"},
        ]
        self.assertFalse(hinweis_noetig(m2))

    def test_freigabe_meldung_weit_vor_dem_ja_sperrt_noch(self):
        m = [{"role": "user", "content": "Tipp das."}, {"role": "tool", "content": "status=wartet"}]
        m += [{"role": "assistant", "content": "Ich warte."}, {"role": "user", "content": "Hmm"}] * 4
        m += [{"role": "assistant", "content": "Soll ich weitermachen?"}, {"role": "user", "content": "Ja"}]
        self.assertFalse(hinweis_noetig(m))

    def test_zu_wenig_verlauf_und_falsche_rollen(self):
        self.assertFalse(hinweis_noetig([]))
        self.assertFalse(hinweis_noetig([{"role": "user", "content": "Ja"}]))
        self.assertFalse(hinweis_noetig([{"role": "user", "content": "Soll ich?"}, {"role": "user", "content": "Ja"}]))
        self.assertFalse(hinweis_noetig([{"role": "assistant", "content": "Soll ich?"}, {"role": "assistant", "content": "Ja"}]))
        self.assertFalse(hinweis_noetig([{"role": "assistant", "content": "Soll ich?"}, {"role": "user", "content": ["Ja"]}]))

    def test_schalter(self):
        self.assertTrue(ja_waechter_an(""))  # seit 14.11.2026 Standard an
        self.assertTrue(ja_waechter_an("an"))
        self.assertFalse(ja_waechter_an("aus"))


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def durchlauf(self, nachrichten, offen=None):
        wache = JaWaechter(offen=offen)
        ctx = LLMContext(messages=nachrichten)
        unten, _ = await run_test(wache, frames_to_send=[LLMContextFrame(ctx)])
        return wache, ctx, [f for f in unten if isinstance(f, LLMContextFrame)]

    async def test_hinweis_wird_an_das_ja_gehaengt_und_der_frame_geht_weiter(self):
        wache, ctx, frames = await self.durchlauf(verlauf("Soll ich das Projekt öffnen?"))
        self.assertEqual(len(frames), 1)
        self.assertTrue(ctx.messages[-1]["content"].startswith("Ja "))
        self.assertIn("frag nicht noch einmal", ctx.messages[-1]["content"])
        self.assertEqual(wache.hinweise, 1)
        self.assertEqual(len(ctx.messages), 3, "keine zusätzliche Nachricht")

    async def test_ohne_ja_bleibt_alles_unveraendert(self):
        nachrichten = verlauf("Soll ich das Projekt öffnen?", "Nein, lieber das andere")
        _, ctx, frames = await self.durchlauf(nachrichten)
        self.assertEqual(ctx.messages[-1]["content"], "Nein, lieber das andere")
        self.assertEqual(len(frames), 1)

    async def test_freigabe_wird_nie_uebersprungen_offene_karte(self):
        wache, ctx, frames = await self.durchlauf(verlauf("Soll ich das Projekt öffnen?"), offen=lambda: True)
        self.assertEqual(ctx.messages[-1]["content"], "Ja", "bei offener Freigabe-Karte kein Hinweis")
        self.assertEqual(wache.hinweise, 0)
        self.assertEqual(len(frames), 1, "der Zug läuft normal weiter, der Wächter verschluckt nichts")

    async def test_freigabe_wird_nie_uebersprungen_meldung_im_verlauf(self):
        nachrichten = [
            {"role": "user", "content": "Lösch die Datei."},
            {"role": "tool", "content": '{"status": "needs_approval"}'},
            {"role": "assistant", "content": "Das braucht eine Freigabe in der Oberfläche. Soll ich warten?"},
            {"role": "user", "content": "Ja"},
        ]
        wache, ctx, _ = await self.durchlauf(nachrichten)
        self.assertEqual(ctx.messages[-1]["content"], "Ja")
        self.assertEqual(wache.hinweise, 0)

    async def test_fremdinhalt_im_zug_sperrt_den_hinweis(self):
        wache = JaWaechter(gefaerbt=lambda: True)
        ctx = LLMContext(messages=verlauf("Soll ich das Projekt öffnen?"))
        await run_test(wache, frames_to_send=[LLMContextFrame(ctx)])
        self.assertEqual(ctx.messages[-1]["content"], "Ja", "die Rückfrage könnte aus einer Webseite oder vom Bildschirm stammen")
        self.assertEqual(wache.hinweise, 0)

    async def test_hinweis_nennt_die_ausnahmen_der_persona(self):
        self.assertIn("Nachrichten an andere (erst den Wortlaut nennen)", HINWEIS)
        self.assertIn("Kaufen, Löschen und Veröffentlichen", HINWEIS)

    async def test_freigabe_text_im_hinweis_ersetzt_keine_freigabe(self):
        # Der Hinweis selbst sagt ausdrücklich, dass Freigaben in der Oberfläche bleiben.
        self.assertIn("Freigaben", HINWEIS)
        self.assertIn("Oberfläche", HINWEIS)

    async def test_vorlaeufige_anfrage_bleibt_unberuehrt(self):
        wache = JaWaechter()
        ctx = LLMContext(messages=verlauf("Soll ich das Projekt öffnen?"))
        frame = LLMContextFrame(ctx)
        frame.speculation = True
        await run_test(wache, frames_to_send=[frame])
        self.assertEqual(ctx.messages[-1]["content"], "Ja", "vorläufige Anfragen verwaltet Pipecat selbst")


if __name__ == "__main__":
    unittest.main()
