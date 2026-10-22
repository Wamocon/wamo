"""Tests für den Start-Aggregator und die Schalter der Soniox-Sprachausgabe (soniox_tts.py)."""

import asyncio
import unittest

from pipecat.services.tts_service import TextAggregationMode
from pipecat.utils.text.base_text_aggregator import AggregationType

from soniox_tts import START_ZEICHEN_STANDARD, LazySonioxTTSService, StartAggregator, start_zeichen

ZAEHLEN = "Eins, zwei, drei, vier, fünf, sechs, sieben, acht, neun, zehn, elf, zwölf, dreizehn, vierzehn, fünfzehn."


async def teile(agg: StartAggregator, *tokens: str) -> list[str]:
    out = []
    for token in tokens:
        async for a in agg.aggregate(token):
            out.append(a.text)
    rest = await agg.flush()
    if rest:
        out.append(rest.text)
    return out


def tokens(text: str) -> list[str]:
    """Wie Claude: Wörter mit führendem Leerzeichen."""
    woerter = text.split(" ")
    return [woerter[0]] + [" " + w for w in woerter[1:]]


class StartAggregatorTest(unittest.IsolatedAsyncioTestCase):
    def neu(self, schwelle: int = 20) -> StartAggregator:
        return StartAggregator(schwelle=schwelle, aggregation_type=AggregationType.SENTENCE, language="de")

    async def test_langer_erster_satz_geht_an_der_wortgrenze_raus(self):
        out = await teile(self.neu(20), *tokens(ZAEHLEN))
        self.assertEqual(out[0], "Eins, zwei, drei, vier, ")
        self.assertEqual("".join(out), ZAEHLEN, "so wie Soniox den Text bekommt: nichts verloren, nichts doppelt, kein Wort verschmolzen")

    async def test_fuge_mitten_im_satz_verschmilzt_keine_woerter(self):
        text = "Das Dorf liegt mitten im Norden des Landes nahe der Grenze und ist klein."
        for schwelle in range(5, 60, 3):
            out = await teile(self.neu(schwelle), *tokens(text))
            self.assertEqual("".join(out), text, f"Schwelle {schwelle}")

    async def test_nur_ein_fruehes_stueck_je_antwort(self):
        out = await teile(self.neu(20), *tokens(ZAEHLEN))
        self.assertEqual(len(out), 2, "danach gilt wieder die Satzgrenze")

    async def test_kurze_saetze_bleiben_wie_bei_pipecat(self):
        out = await teile(self.neu(68), *tokens("Hmm, gute Frage. Die Hauptstadt von Frankreich ist Paris."))
        self.assertEqual(out, ["Hmm, gute Frage.", "Die Hauptstadt von Frankreich ist Paris."])

    async def test_schwelle_zaehlt_ueber_saetze_hinweg(self):
        text = "Die Hauptstadt von Frankreich ist Paris. Das ist eine wirklich schöne und sehr große Stadt an der Seine."
        out = await teile(self.neu(68), *tokens(text))
        self.assertEqual(out[0], "Die Hauptstadt von Frankreich ist Paris.")
        self.assertEqual("".join(out), text.replace("Paris. Das", "Paris.Das"), "Fuge am Satzende ohne Leerzeichen, wie bei Pipecat")
        self.assertGreater(len(out), 2, "der zweite Satz geht schon vor dem Satzende raus")
        self.assertLess(len(out[1]), len("Das ist eine wirklich schöne und sehr große Stadt an der Seine."))

    async def test_wort_wird_nie_geteilt(self):
        text = "Unglaublich zusammengesetzte Wortungetüme erreichen irgendwann die Schwelle und gehen heraus."
        out = await teile(self.neu(30), *tokens(text))
        self.assertEqual("".join(out), text)
        for stueck in out:
            self.assertFalse(stueck.startswith(" "), "kein Leerzeichen am Anfang")

    async def test_neue_antwort_nach_flush_beginnt_wieder_bei_null(self):
        agg = self.neu(20)
        await teile(agg, *tokens(ZAEHLEN))
        out = await teile(agg, *tokens(ZAEHLEN))
        self.assertEqual(out[0], "Eins, zwei, drei, vier, ")

    async def test_nach_unterbrechung_beginnt_wieder_bei_null(self):
        agg = self.neu(20)
        [a async for a in agg.aggregate("Eins, zwei, drei, vier, fünf, sechs,")]
        await agg.handle_interruption()
        out = await teile(agg, *tokens(ZAEHLEN))
        self.assertEqual(out[0], "Eins, zwei, drei, vier, ")

    async def test_leerraum_zaehlt_nicht(self):
        agg = self.neu(30)
        erste = [a.text async for a in agg.aggregate("Eins    zwei    drei    vier    fünf ")]
        self.assertEqual(erste, [], "20 Zeichen ohne Leerraum liegen unter der Schwelle")


class SchalterTest(unittest.TestCase):
    def test_start_zeichen(self):
        self.assertEqual(start_zeichen("50"), 50)
        self.assertEqual(start_zeichen("0"), 0)
        self.assertEqual(start_zeichen(""), START_ZEICHEN_STANDARD)
        self.assertEqual(start_zeichen("abc"), START_ZEICHEN_STANDARD)
        self.assertEqual(start_zeichen("-4"), 0)

    def test_dienst_tauscht_den_aggregator_nur_mit_schwelle(self):
        aus = LazySonioxTTSService(api_key="x", start=0)
        an = LazySonioxTTSService(api_key="x", start=68)
        self.assertNotIsInstance(aus._text_aggregator, StartAggregator)
        self.assertIsInstance(an._text_aggregator, StartAggregator)

    def test_tokenmodus_bleibt_unberuehrt(self):
        dienst = LazySonioxTTSService(api_key="x", start=68, text_aggregation_mode=TextAggregationMode.TOKEN)
        self.assertNotIsInstance(dienst._text_aggregator, StartAggregator)

    def test_stream_oeffnet_nicht_mit_dem_turn(self):
        dienst = LazySonioxTTSService(api_key="x", start=0)
        asyncio.run(dienst.on_turn_context_created("ctx"))
        self.assertNotIn("ctx", dienst._configured_contexts)


if __name__ == "__main__":
    unittest.main()
