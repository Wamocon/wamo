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
from persona import NAME_ANZEIGE, NAME_BUCHSTABIERT, NAME_BUCHSTABIERT_TTS, NAME_KURZ, NAME_LANG, NAME_LANG_DE, NAME_TTS

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

    def test_buchstabiert_bekommt_die_stimme_den_eigenen_wert(self):
        self.assertEqual(NAME_BUCHSTABIERT, "W, A, I, S")
        self.assertEqual(NAME_BUCHSTABIERT_TTS, "Weh Ej, Ei Es")
        self.assertEqual(fuer_sprachausgabe(f"Man schreibt mich {NAME_BUCHSTABIERT}."), "Man schreibt mich Weh Ej, Ei Es.")
        self.assertEqual(fuer_sprachausgabe("„W, A, I, S“, also Wais."), "„Weh Ej, Ei Es“, also Ways.")
        self.assertEqual(fuer_sprachausgabe("Das ist W. A. I. S. Das war es."), "Das ist Weh Ej, Ei Es. Das war es.")
        self.assertEqual(fuer_sprachausgabe("Erst W. A. I. S. und dann"), "Erst Weh Ej, Ei Es und dann")
        for variante in ("W-A-I-S", "W. A. I. S.", "W A I S"):
            with self.subTest(variante=variante):
                self.assertEqual(fuer_sprachausgabe(f"Buchstabiert {variante} und fertig"), "Buchstabiert Weh Ej, Ei Es und fertig")
                self.assertEqual(fuer_sprachausgabe(f"Buchstabiert {variante}"),
                                 "Buchstabiert Weh Ej, Ei Es" + ("." if variante.endswith(".") else ""))

    def test_buchstabiert_nur_genau_diese_folge(self):
        for text in ("W, A, I, T", "W, A, I, S, T", "W-A-I", "W, A-I S", "AW A I S", "W A I Sonne", "W-A-I-S-Profil"):
            with self.subTest(text=text):
                self.assertEqual(fuer_sprachausgabe(text), text)

    def test_buchstabiert_wert_kommt_aus_der_konstante(self):
        with mock.patch.object(aussprache, "NAME_BUCHSTABIERT_TTS", "W-Ey-Ai-S"):
            self.assertEqual(fuer_sprachausgabe("W, A, I, S"), "W-Ey-Ai-S")

    def test_englischer_genitiv_der_firma(self):
        self.assertEqual(fuer_sprachausgabe(NAME_LANG), "Wamokons Artificial Intelligence System")
        self.assertEqual(fuer_sprachausgabe("WAMOCON's Artificial Intelligence System"), "Wamokons Artificial Intelligence System")
        self.assertEqual(FIRMA_TTS, "Wamokons")
        for text in ("Die WAMOCON GmbH in Eschborn.", "bei der WAMOCON", "WAMOCON-Projekte"):
            with self.subTest(text=text):
                self.assertEqual(fuer_sprachausgabe(text), text)

    def test_deutscher_genitiv_der_firma(self):
        # Hörprobe: die deutsche Langform klang mit "Wamokons" richtig
        self.assertEqual(fuer_sprachausgabe(NAME_LANG_DE), "Wamokons künstliches intelligentes System")
        self.assertEqual(fuer_sprachausgabe("Ich bin WAMOCONs System."), "Ich bin Wamokons System.")
        self.assertEqual(fuer_sprachausgabe("WAMOCONSULT"), "WAMOCONSULT")

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
        saetze = ["Hallo Erika, Wais ist bereit.", "Man schreibt mich W, A, I, S."]
        unten, _ = await run_test(dienst, frames_to_send=[TTSSpeakFrame(s) for s in saetze])
        self.assertEqual([a["input"].strip() for a in anfragen],
                         ["Hallo Erika, Ways ist bereit.", "Man schreibt mich Weh Ej, Ei Es."])
        texte = [f.text for f in unten if isinstance(f, TTSTextFrame)]
        self.assertEqual(texte, saetze)  # angezeigt und im Verlauf bleibt Wais und W, A, I, S

    async def test_sprachausgabe_mit_wortzeitstempeln(self):
        """Soniox: Pipecat ordnet das gesprochene Wort NAME_TTS der Kurzform zu. Der Verlauf nimmt raw_text, die Untertitel
        (bot-output) den Fortschritt im angezeigten Text."""
        for original in (f"Hallo Erika, {NAME_KURZ} ist bereit.", f"Man schreibt mich {NAME_BUCHSTABIERT}."):
            with self.subTest(original=original):
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
                for tts_wert in (NAME_TTS, NAME_BUCHSTABIERT_TTS):
                    self.assertTrue(all(tts_wert not in f.accumulated_text for f in fortschritt))


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
