"""Tests für aussprache.py: Namen für die Stimme umschreiben, Verlauf und Oberfläche behalten die Kurzform.

Dazu der Schutz, dass gesprochene Texte im Kern nie "W.A.I.S." oder "WAMO" enthalten (die Stimme läse sonst Buchstaben oder
den früheren Namen) und die Kurzform aus persona.NAME_KURZ stammt.
"""

import ast
import io
import json
import re
import unittest
import wave
from pathlib import Path
from unittest import mock

import httpx

from pipecat.frames.frames import AggregatedTextFrame, AggregatedTextProgressFrame, TTSSpeakFrame, TTSTextFrame
from pipecat.tests.utils import run_test
from pipecat.utils.context.aggregated_frame_sequencer import AggregatedFrameSequencer
from pipecat.utils.text.base_text_aggregator import AggregationType

import aussprache
from aussprache import FIRMA_TTS, fuer_sprachausgabe
from persona import NAME_ANZEIGE, NAME_BUCHSTABIERT, NAME_KURZ, NAME_LANG, NAME_TTS

HIER = Path(__file__).resolve().parent
ALTER_NAME = re.compile(r"\bWAMO\b(?![_-])")


def wav(rate: int = 24000, sekunden: float = 0.1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x10\x00" * int(rate * sekunden))
    return buf.getvalue()


class FuerSprachausgabeTests(unittest.TestCase):
    def test_kurzform_wird_zur_schreibweise_fuer_die_stimme(self):
        self.assertEqual(fuer_sprachausgabe(f"Hallo Erika, {NAME_KURZ} ist bereit."), f"Hallo Erika, {NAME_TTS} ist bereit.")
        self.assertEqual(fuer_sprachausgabe("Ich bin Wais."), "Ich bin Ways.")
        self.assertEqual(fuer_sprachausgabe("wais und WAIS"), "Ways und Ways")
        self.assertEqual(fuer_sprachausgabe("Das Wais-Profil"), "Das Ways-Profil")

    def test_nur_ganze_woerter(self):
        for text in ("Die Waise und die Waisen.", "Ich weiß es nicht.", "Weisheit", "Kuwaiser", "Waize"):
            with self.subTest(text=text):
                self.assertEqual(fuer_sprachausgabe(text), text)

    def test_anzeigename_mit_punkten(self):
        self.assertEqual(fuer_sprachausgabe("W.A.I.S. hilft dir."), "Ways hilft dir.")
        self.assertEqual(fuer_sprachausgabe("Ich bin W.A.I.S."), "Ich bin Ways.")  # Satzende bleibt
        self.assertEqual(fuer_sprachausgabe("Ich bin W.A.I.S. "), "Ich bin Ways. ")
        self.assertEqual(fuer_sprachausgabe("Das W.A.I.S.-Profil ist aktiv."), "Das Ways-Profil ist aktiv.")
        self.assertEqual(fuer_sprachausgabe("W.A.I.S, kurz"), "Ways, kurz")
        self.assertEqual(fuer_sprachausgabe("W. A. I. S."), "W. A. I. S.")  # mit Leerzeichen kein Name

    def test_buchstabieren_bleibt(self):
        text = f"Man schreibt mich {NAME_BUCHSTABIERT}."
        self.assertEqual(fuer_sprachausgabe(text), text)

    def test_englischer_genitiv_der_firma(self):
        self.assertEqual(fuer_sprachausgabe(NAME_LANG), "Wamokons Artificial Intelligence System")
        self.assertEqual(fuer_sprachausgabe("WAMOCON's Artificial Intelligence System"), "Wamokons Artificial Intelligence System")
        self.assertEqual(FIRMA_TTS, "Wamokons")
        for text in ("Die WAMOCON GmbH in Eschborn.", "bei der WAMOCON", "WAMOCONs künstliches intelligentes System"):
            with self.subTest(text=text):
                self.assertEqual(fuer_sprachausgabe(text), text)

    def test_langform_komplett(self):
        satz = f"Ich bin {NAME_KURZ}, ausgeschrieben {NAME_LANG}, kurz {NAME_ANZEIGE}"
        self.assertEqual(fuer_sprachausgabe(satz), "Ich bin Ways, ausgeschrieben Wamokons Artificial Intelligence System, kurz Ways.")

    def test_leer_und_ohne_namen(self):
        self.assertEqual(fuer_sprachausgabe(""), "")
        self.assertEqual(fuer_sprachausgabe("Einen Moment."), "Einen Moment.")

    def test_schreibweise_kommt_aus_der_konstante(self):
        with mock.patch.object(aussprache, "NAME_TTS", "Weys"):
            self.assertEqual(fuer_sprachausgabe("Wais und W.A.I.S."), "Weys und Weys.")


class VerlaufBehaeltKurzformTests(unittest.IsolatedAsyncioTestCase):
    async def test_sprachausgabe_ohne_wortzeitstempel(self):
        """Sprachausgabe im Haus: die Stimme bekommt NAME_TTS, Verlauf und Oberfläche (TTSTextFrame) die Kurzform."""
        anfragen = []

        def handler(request: httpx.Request) -> httpx.Response:
            anfragen.append(json.loads(request.content))
            return httpx.Response(200, content=wav())

        import sprache_anbieter as sa

        dienst = sa.HausTTSService(modell="wamo-tts", stimme="thorsten", basis="http://litellm.test.invalid:4000/v1",
                                   schluessel="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                   sample_rate=24000)
        aussprache.anhaengen(dienst)
        unten, _ = await run_test(dienst, frames_to_send=[TTSSpeakFrame("Hallo Erika, Wais ist bereit.")])
        self.assertEqual([a["input"].strip() for a in anfragen], ["Hallo Erika, Ways ist bereit."])
        texte = [f.text for f in unten if isinstance(f, TTSTextFrame)]
        self.assertEqual(texte, ["Hallo Erika, Wais ist bereit."])

    async def test_sprachausgabe_mit_wortzeitstempeln(self):
        """Soniox: Pipecat ordnet das gesprochene Wort NAME_TTS der Kurzform zu. Der Verlauf nimmt raw_text, die Untertitel
        (bot-output) den Fortschritt im angezeigten Text."""
        original = f"Hallo Erika, {NAME_KURZ} ist bereit."
        seq = AggregatedFrameSequencer()
        quelle = AggregatedTextFrame(original, AggregationType.SENTENCE, raw_text=original)
        await seq.register_spoken(quelle, "k1", fuer_sprachausgabe(original), append_to_context=True)
        frames = []
        for wort in fuer_sprachausgabe(original).split():
            frames += seq.process_word(wort, 1000, "k1")
        verlauf = "".join(f.raw_text if f.raw_text else f.text for f in frames if isinstance(f, TTSTextFrame))
        self.assertEqual(verlauf.replace(" ", ""), original.replace(" ", ""))
        fortschritt = [f for f in frames if isinstance(f, AggregatedTextProgressFrame)]
        self.assertEqual(fortschritt[-1].accumulated_text, original)
        self.assertTrue(all(NAME_TTS not in f.accumulated_text for f in fortschritt))


class GesprocheneTexteTests(unittest.TestCase):
    """Was direkt an die Stimme geht, nennt den Assistenten nur mit der Kurzform."""

    def _ohne_anzeigenamen(self, text: str, wo: str):
        self.assertNotIn(NAME_ANZEIGE, text, wo)
        self.assertIsNone(ALTER_NAME.search(text), wo)

    def test_feste_ansagen(self):
        import bot
        import fuellwoerter
        import stimme_einlernen
        import wartesatz

        gruss = bot.greeting_text("Erika")
        self.assertEqual(gruss, f"Hallo Erika, {NAME_KURZ} ist bereit.")
        texte = {"Begrüßung": gruss, "Begrüßung Vorlage": bot.DEFAULT_GREETING}
        texte |= {f"Füllwort {t}": t for t in fuellwoerter.ALLE_SAETZE}
        for name in dir(wartesatz):
            if name.startswith("SAETZE_"):
                texte |= {f"Wartesatz {t}": t for t in getattr(wartesatz, name)}
        for name in ("ANLEITUNG", "FERTIG_TEXT", "ZU_WENIG", "ZU_UNRUHIG", "KEIN_MODELL"):
            texte[name] = getattr(stimme_einlernen, name)
        for wo, text in texte.items():
            self._ohne_anzeigenamen(text, wo)

    def test_aufrufe_der_sprachausgabe_im_quelltext(self):
        """Texte, die der Kern an die Stimme gibt (TTSSpeakFrame, Ansagen, Stimmprüfung) oder dem Modell zum Weitersagen
        (melde_llm), enthalten weder W.A.I.S. noch WAMO. Geprüft werden die Zeichenketten in den Aufrufen, nicht Kommentare."""
        sprechen = {"TTSSpeakFrame", "_ansage", "ansage", "melde_llm", "stimme_sagen", "_sagen", "sagen"}
        gefunden = 0
        for datei in sorted(HIER.glob("*.py")):
            if datei.name.startswith("test_"):
                continue
            for knoten in ast.walk(ast.parse(datei.read_text(encoding="utf-8"))):
                if not isinstance(knoten, ast.Call):
                    continue
                f = knoten.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if name not in sprechen:
                    continue
                for arg in knoten.args:
                    for teil in ast.walk(arg):
                        if isinstance(teil, ast.Constant) and isinstance(teil.value, str):
                            gefunden += 1
                            self._ohne_anzeigenamen(teil.value, f"{datei.name}:{knoten.lineno}")
        self.assertGreater(gefunden, 3)


if __name__ == "__main__":
    unittest.main()
