"""Tests für den Start-Aggregator und die Schalter der Soniox-Sprachausgabe (soniox_tts.py)."""

import asyncio
import unittest

from pipecat.services.tts_service import TextAggregationMode
from pipecat.utils.text.base_text_aggregator import AggregationType

import os
from unittest import mock

from pipecat.frames.frames import TTSStartedFrame
from pipecat.services.soniox.tts import SonioxTTSService

import soniox_tts
from soniox_tts import (
    START_ZEICHEN_STANDARD,
    LazySonioxTTSService,
    StartAggregator,
    fuer_soniox,
    satz_leerzeichen_an,
    start_zeichen,
    stimme_einstellung,
)

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


class SatzLeerzeichenTest(unittest.TestCase):
    """A2: Leerzeichen am Satzende, damit Soniox das Satzzeichen nicht vorliest."""

    def test_standard_aus_und_text_bleibt_unveraendert(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WAMO_TTS_SATZLEERZEICHEN", None)
            self.assertFalse(satz_leerzeichen_an())
            self.assertEqual(fuer_soniox("Das ist ein Satz."), "Das ist ein Satz.")

    def test_schalter_an_haengt_ein_leerzeichen_an(self):
        self.assertTrue(satz_leerzeichen_an("an"))
        self.assertEqual(fuer_soniox("Das ist ein Satz.", an=True), "Das ist ein Satz. ")
        self.assertEqual(fuer_soniox("Was ist das?", an=True), "Was ist das? ")

    def test_kein_doppeltes_leerzeichen_und_kein_leerer_text(self):
        self.assertEqual(fuer_soniox("Eins, zwei, ", an=True), "Eins, zwei, ")
        self.assertEqual(fuer_soniox("Satz." + chr(10), an=True), "Satz." + chr(10))
        self.assertEqual(fuer_soniox("", an=True), "")

    def test_umgebungsvariable_wirkt(self):
        with mock.patch.dict(os.environ, {"WAMO_TTS_SATZLEERZEICHEN": "an"}):
            self.assertEqual(fuer_soniox("Hallo."), "Hallo. ")
        with mock.patch.dict(os.environ, {"WAMO_TTS_SATZLEERZEICHEN": "aus"}):
            self.assertEqual(fuer_soniox("Hallo."), "Hallo.")

    def test_aggregator_fugen_bleiben_unveraendert(self):
        # der Verlauf kommt aus den Wort-Zeitstempeln; der Aggregator reicht weiter ohne Leerzeichen am Satzende
        out = asyncio.run(teile(StartAggregator(schwelle=68, aggregation_type=AggregationType.SENTENCE, language="de"),
                                *tokens("Hallo, wie geht es dir. Gut danke.")))
        self.assertEqual(out, ["Hallo, wie geht es dir.", "Gut danke."])

    def test_run_tts_gibt_den_text_mit_leerzeichen_weiter(self):
        gesendet: list[str] = []

        async def basis(self, text, context_id):
            gesendet.append(text)
            yield TTSStartedFrame(context_id=context_id)

        async def lauf(an: str):
            dienst = LazySonioxTTSService(api_key="x", start=0)
            dienst._websocket = mock.Mock()
            dienst._websocket.state = soniox_tts.State.OPEN
            dienst._send_config = mock.AsyncMock()
            umgebung = mock.patch.dict(os.environ, {"WAMO_TTS_SATZLEERZEICHEN": an})
            with umgebung, mock.patch.object(SonioxTTSService, "run_tts", basis):
                return [f async for f in dienst.run_tts("Das war der Satz.", "ctx")]

        asyncio.run(lauf("an"))
        asyncio.run(lauf("aus"))
        self.assertEqual(gesendet, ["Das war der Satz. ", "Das war der Satz."])


class StimmeEinstellungTest(unittest.TestCase):
    """A3: Tempo und Pausen per Umgebung, Standard wie bisher (nichts gesetzt)."""

    def test_nichts_gesetzt_aendert_nichts(self):
        self.assertEqual(stimme_einstellung("", ""), {})
        with mock.patch.dict(os.environ, {"SONIOX_SPEED": "", "SONIOX_REDUCE_SILENCE": ""}):
            self.assertEqual(stimme_einstellung(), {})

    def test_tempo_im_bereich(self):
        self.assertEqual(stimme_einstellung("1.1", ""), {"speed": 1.1})
        self.assertEqual(stimme_einstellung("0,9", ""), {"speed": 0.9})
        self.assertEqual(stimme_einstellung("0.7", ""), {"speed": 0.7})
        self.assertEqual(stimme_einstellung("1.3", ""), {"speed": 1.3})

    def test_ungueltiges_tempo_wird_ignoriert(self):
        for wert in ("0.5", "1.4", "schnell", "nan", "inf", "-1"):
            self.assertEqual(stimme_einstellung(wert, ""), {}, wert)

    def test_pausen_kuerzen_an_aus_leer(self):
        self.assertEqual(stimme_einstellung("", "an"), {"reduce_silence": True})
        self.assertEqual(stimme_einstellung("", "aus"), {"reduce_silence": False})
        self.assertEqual(stimme_einstellung("", ""), {})
        self.assertEqual(stimme_einstellung("", "vielleicht"), {}, "unbekannt: nichts einstellen, nicht raten")

    def test_umgebungsvariablen(self):
        with mock.patch.dict(os.environ, {"SONIOX_SPEED": "1.05", "SONIOX_REDUCE_SILENCE": "ja"}):
            self.assertEqual(stimme_einstellung(), {"speed": 1.05, "reduce_silence": True})

    def test_settings_des_dienstes_uebernehmen_die_werte(self):
        einst = SonioxTTSService.Settings(voice="Bryce", **stimme_einstellung("1.1", "an"))
        self.assertEqual((einst.voice, einst.speed, einst.reduce_silence), ("Bryce", 1.1, True))
        ohne = SonioxTTSService.Settings(voice="Bryce", **stimme_einstellung("", ""))
        self.assertEqual(ohne.voice, "Bryce")

    def test_dienst_nimmt_die_werte_in_die_konfiguration(self):
        dienst = LazySonioxTTSService(
            api_key="x", start=0, settings=SonioxTTSService.Settings(voice="Bryce", **stimme_einstellung("1.1", "an"))
        )
        self.assertEqual(dienst._settings.speed, 1.1)
        self.assertTrue(dienst._settings.reduce_silence)


if __name__ == "__main__":
    unittest.main()
