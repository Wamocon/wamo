"""Tests für den gemeinsamen Parser der an/aus-Schalter (env_schalter.py)."""

import os
import unittest
from unittest import mock

import env_schalter
from env_schalter import schalter_an


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


if __name__ == "__main__":
    unittest.main()
