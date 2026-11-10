"""Tests für den gemeinsamen Parser der an/aus-Schalter (env_schalter.py)."""

import os
import unittest
from pathlib import Path
from unittest import mock

import env_schalter
from env_schalter import schalter_an, schalter_oder_none, zahl_im_bereich


class SchalterTest(unittest.TestCase):
    def test_leer_und_nicht_gesetzt_heisst_an(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WAMO_TEST_SCHALTER", None)
            self.assertTrue(schalter_an("WAMO_TEST_SCHALTER"))
        self.assertTrue(schalter_an("X", ""))
        self.assertTrue(schalter_an("X", "   "))

    def test_standard_aus(self):
        self.assertFalse(schalter_an("X", "", standard=False))
        self.assertTrue(schalter_an("X", "an", standard=False))
        self.assertFalse(schalter_an("X", "aus", standard=False))

    def test_aus_in_allen_schreibweisen(self):
        for wert in ("aus", "AUS", " 0 ", "nein", "false", "off", "no", "n", "disabled", "deaktiviert", "inaktiv"):
            self.assertFalse(schalter_an("X", wert), wert)

    def test_an_in_allen_schreibweisen(self):
        for wert in ("an", "ein", "1", "ja", "true", "on", "yes", "aktiv"):
            self.assertTrue(schalter_an("X", wert), wert)

    def test_unbekannter_wert_bleibt_aus_bei_standard_aus(self):
        env_schalter._GEWARNT.clear()
        with mock.patch.object(env_schalter.logger, "warning") as warnung:
            self.assertFalse(schalter_an("WAMO_Z", "disable", standard=False))
            self.assertFalse(schalter_an("WAMO_Z", "disable", standard=False))
        self.assertEqual(warnung.call_count, 1)
        self.assertTrue(schalter_an("WAMO_Z", "disable", standard=True), "Notaus-Schalter bleiben bei Tippfehlern an")

    def test_umgebungsvariable(self):
        with mock.patch.dict(os.environ, {"WAMO_TEST_SCHALTER": "aus"}):
            self.assertFalse(schalter_an("WAMO_TEST_SCHALTER"))
        with mock.patch.dict(os.environ, {"WAMO_TEST_SCHALTER": "aus"}):
            self.assertTrue(schalter_an("WAMO_TEST_SCHALTER", "an"), "ein übergebener Wert geht vor")

    def test_unbekannter_wert_bleibt_an_und_wird_einmal_gewarnt(self):
        env_schalter._GEWARNT.clear()
        with mock.patch.object(env_schalter.logger, "warning") as warnung:
            self.assertTrue(schalter_an("WAMO_X", "abgeschaltet"))
            self.assertTrue(schalter_an("WAMO_X", "abgeschaltet"))
            self.assertTrue(schalter_an("WAMO_X", "anders"))
        self.assertEqual(warnung.call_count, 2, "je Name und Wert einmal")


class EinstellungTest(unittest.TestCase):
    def test_schalter_oder_none(self):
        self.assertIsNone(schalter_oder_none("X", ""))
        self.assertIsNone(schalter_oder_none("X", "  "))
        self.assertIs(schalter_oder_none("X", "an"), True)
        self.assertIs(schalter_oder_none("X", "Aus"), False)

    def test_schalter_oder_none_unbekannt_wird_einmal_gewarnt(self):
        env_schalter._GEWARNT.clear()
        with mock.patch.object(env_schalter.logger, "warning") as warnung:
            self.assertIsNone(schalter_oder_none("WAMO_Y", "vielleicht"))
            self.assertIsNone(schalter_oder_none("WAMO_Y", "vielleicht"))
        self.assertEqual(warnung.call_count, 1)

    def test_zahl_im_bereich(self):
        self.assertEqual(zahl_im_bereich("X", 0.7, 1.3, "1,2"), 1.2)
        self.assertIsNone(zahl_im_bereich("X", 0.7, 1.3, ""))
        for wert in ("0.69", "1.31", "abc", "nan", "inf"):
            env_schalter._GEWARNT.clear()
            with mock.patch.object(env_schalter.logger, "warning") as warnung:
                self.assertIsNone(zahl_im_bereich("X", 0.7, 1.3, wert), wert)
            self.assertEqual(warnung.call_count, 1, wert)


class StandardwerteTest(unittest.TestCase):
    """Die Standardwerte der Sprachpakete A und B stehen an einer Stelle und bleiben abschaltbar."""

    ERWARTET = {
        "WAMO_TTS_SATZLEERZEICHEN": False, "WAMO_PERSONA_RUECKFRAGEN": True, "WAMO_PERSONA_PC_ORDNER": False,
        "WAMO_PAUSEN_PROMPT": True, "WAMO_RUECKMELDUNG_WERKZEUG": True, "WAMO_JA_WAECHTER": False,
        "WAMO_PERSONA_GESPRAECH": False, "WAMO_WARTESATZ_MODELL": False,
    }

    def test_standardwerte_nach_der_messung(self):
        self.assertEqual(env_schalter.STANDARDWERTE, self.ERWARTET)
        for name, soll in self.ERWARTET.items():
            self.assertEqual(schalter_an(name, ""), soll, name)

    def test_jeder_schalter_ist_in_beide_richtungen_umschaltbar(self):
        for name in self.ERWARTET:
            for wert in ("aus", "0", "nein"):
                self.assertFalse(schalter_an(name, wert), f"{name}={wert}")
            for wert in ("an", "1", "ja"):
                self.assertTrue(schalter_an(name, wert), f"{name}={wert}")

    def test_ein_expliziter_standard_geht_vor(self):
        self.assertTrue(schalter_an("WAMO_PAUSEN_PROMPT", "", standard=True))
        self.assertFalse(schalter_an("WAMO_PAUSEN_PROMPT", "", standard=False))


NEUE_NAMEN = (
    "SONIOX_SPEED", "SONIOX_REDUCE_SILENCE", "WAMO_TTS_SATZLEERZEICHEN", "WAMO_PERSONA_RUECKFRAGEN", "WAMO_PERSONA_PC_ORDNER",
    "WAMO_PAUSEN_PROMPT", "WAMO_LERNEN_OHNE_MOMENT", "WAMO_RUECKMELDUNG_WERKZEUG", "WAMO_JA_WAECHTER", "WAMO_ECHO_MODUS",
)


# Prüfbares Ziel für Entwicklungsaufträge (Standard an), Befehlskriterien (Standard aus) und Schwelle zum Kürzen langer
# Werkzeugausgaben (Standard 3000, 0 = aus), alle seit 09.11.2026
ABNAHME_UND_AUSGABEN = ("WAMO_PERSONA_ABNAHME", "WAMO_ABNAHME_BEFEHLE", "WAMO_AUSGABE_SCHWELLE")


class ErlaubnislisteTest(unittest.TestCase):
    """Ein Schalter, der nicht in der Liste erlaubter Werte steht, bleibt auf Sokrates-2 wirkungslos."""

    def test_neue_namen_stehen_in_beiden_listen(self):
        wurzel = Path(__file__).resolve().parent.parent
        for datei in ("infra/sokrates2/wamo_core_env.py", "scripts/wamo_kern_sokrates2.py"):
            text = (wurzel / datei).read_text(encoding="utf-8")
            for name in NEUE_NAMEN:
                self.assertIn(f'"{name}"', text, f"{name} fehlt in {datei}")

    def test_neue_namen_stehen_in_der_beschreibung_der_schalter(self):
        for name in NEUE_NAMEN:
            if name != "WAMO_ECHO_MODUS":
                self.assertIn(name, env_schalter.__doc__)

    def test_abnahme_und_ausgaben_stehen_in_beiden_listen(self):
        """Der Hilfsdienst auf Sokrates-2 lehnt unbekannte Schlüssel ab (nichts wird übernommen): beide Listen müssen gleich sein."""
        wurzel = Path(__file__).resolve().parent.parent
        for datei in ("infra/sokrates2/wamo_core_env.py", "scripts/wamo_kern_sokrates2.py"):
            text = (wurzel / datei).read_text(encoding="utf-8")
            for name in ABNAHME_UND_AUSGABEN:
                self.assertIn(f'"{name}"', text, f"{name} fehlt in {datei}")

    def test_abnahme_und_ausgaben_stehen_leer_in_den_vorlagen(self):
        """Leer heißt Standard: Befehlskriterien aus, prüfbares Ziel an, Schwelle 3000 (die Vorlage schaltet nie etwas ein)."""
        wurzel = Path(__file__).resolve().parent.parent
        for datei in (".env.example", "voice-core/.env.example"):
            text = (wurzel / datei).read_text(encoding="utf-8")
            for name in ABNAHME_UND_AUSGABEN:
                self.assertRegex(text, rf"(?m)^{name}=$", f"{name} fehlt oder hat einen Wert in {datei}")

    def test_leerer_wert_gibt_den_sicheren_standard(self):
        import abnahme
        import ausgaben_kuerzen
        import persona

        with mock.patch.dict(os.environ, {name: "" for name in ABNAHME_UND_AUSGABEN}):
            self.assertFalse(abnahme.befehle_eingeschaltet(), "Befehlskriterien bleiben ohne ausdrückliches ja aus")
            self.assertTrue(persona.persona_abnahme_an())
            self.assertEqual(ausgaben_kuerzen.schwelle_aus_umgebung(), ausgaben_kuerzen.SCHWELLE_STANDARD)


if __name__ == "__main__":
    unittest.main()
