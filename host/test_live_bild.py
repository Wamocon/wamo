"""Tests für das Live-Bild im Host: Schalter am Laptop, Verbotsproben, Takt, Größe, Zuschauer, Audit ohne Bilder.

Ausführen:  uv run python -m pytest -q test_live_bild.py   (im Ordner host/)
Es wird nie der echte Bildschirm aufgenommen: die Aufnahme ist durch eine Attrappe ersetzt (außer in den reinen
Tests der Bildverkleinerung, die mit einem erzeugten Bild arbeiten).
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import live_bild
import wamo_host as host

KENNUNG = "pc-test"
BESITZER = "11111111-2222-3333-4444-555555555555"
ZUSCHAUER = "a" * 16
ANDERER = "b" * 16
FALSCHES_BILD = "QkxPQg=="  # kein echtes Bild, nur ein Platzhalter


class FesteBindung:
    def __init__(self, stufe="admin", kennung=KENNUNG, besitzer=BESITZER):
        self._werte = (kennung, besitzer, stufe) if stufe else None

    def werte(self):
        return self._werte


class Uhr:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def schalter_inhalt(uhr, **aenderungen):
    daten = {"v": 1, "an": True, "bis": uhr.t + 600, "herzschlag": uhr.t - 1, "sichtbar": True,
             "einwilligung": live_bild.EINWILLIGUNG_VERSION, "kennung": KENNUNG, "besitzer": BESITZER}
    daten.update(aenderungen)
    return daten


class LiveTestBasis(unittest.TestCase):
    def setUp(self):
        self.ordner = tempfile.TemporaryDirectory()
        self.addCleanup(self.ordner.cleanup)
        self.datei = Path(self.ordner.name) / live_bild.DATEI_NAME
        self.uhr = Uhr()
        self.takt = Uhr(100.0)
        self.bindung = FesteBindung()
        self.neu()

    def neu(self, bindung=None):
        self.live = live_bild.Live(self.datei, bindung or self.bindung, uhr=self.uhr, takt=self.takt)

    def schreibe(self, **aenderungen):
        self.datei.write_text(json.dumps(schalter_inhalt(self.uhr, **aenderungen)), encoding="utf-8")


class SchalterTests(LiveTestBasis):
    def test_ohne_datei_ist_aus(self):
        s = self.live.schalter()
        self.assertEqual((s["bereit"], s["grund"]), (False, "schalter_aus"))

    def test_eingeschaltet_ist_bereit(self):
        self.schreibe()
        s = self.live.schalter()
        self.assertTrue(s["bereit"])
        self.assertEqual(s["rest_s"], 600)

    def test_ohne_geraetedatei_oder_bindung_nie(self):
        self.schreibe()
        for live in (live_bild.Live(None, self.bindung), live_bild.Live(self.datei, None),
                     live_bild.Live(self.datei, FesteBindung(stufe=None))):
            self.assertEqual(live.schalter()["grund"], "nicht_eingerichtet")

    def test_mitglied_nie(self):
        self.schreibe()
        self.neu(FesteBindung(stufe="mitglied"))
        self.assertEqual(self.live.schalter()["grund"], "stufe")

    def test_fremdes_geraet_oder_profil_nie(self):
        for aenderung in ({"kennung": "pc-anderer"}, {"besitzer": "99999999-2222-3333-4444-555555555555"}):
            with self.subTest(aenderung=aenderung):
                self.schreibe(**aenderung)
                self.assertFalse(self.live.schalter()["bereit"])

    def test_ohne_einwilligung_nie(self):
        for wert in (None, 0, 2, "1", True):
            with self.subTest(wert=wert):
                self.schreibe(einwilligung=wert)
                self.assertEqual(self.live.schalter()["grund"], "keine_einwilligung")

    def test_aus_ist_aus(self):
        for wert in (False, None, "true", 1):
            with self.subTest(wert=wert):
                self.schreibe(an=wert)
                self.assertEqual(self.live.schalter()["grund"], "schalter_aus")

    def test_abgelaufen_oder_zu_weit_in_der_zukunft(self):
        for bis in (self.uhr.t - 1, self.uhr.t, self.uhr.t + 99999, "bald", None, True):
            with self.subTest(bis=bis):
                self.schreibe(bis=bis)
                self.assertEqual(self.live.schalter()["grund"], "abgelaufen")

    def test_hinweis_muss_leben_und_sichtbar_sein(self):
        for aenderung in ({"herzschlag": self.uhr.t - 30}, {"herzschlag": self.uhr.t + 60}, {"herzschlag": None},
                          {"sichtbar": False}, {"sichtbar": "ja"}):
            with self.subTest(aenderung=aenderung):
                self.schreibe(**aenderung)
                self.assertEqual(self.live.schalter()["grund"], "hinweis_fehlt")

    def test_kaputte_oder_zu_grosse_datei(self):
        self.datei.write_text("{kaputt", encoding="utf-8")
        self.assertFalse(self.live.schalter()["bereit"])
        self.datei.write_text(json.dumps({"x": "a" * 5000}), encoding="utf-8")
        self.assertFalse(self.live.schalter()["bereit"])
        self.datei.write_text("[1]", encoding="utf-8")
        self.assertFalse(self.live.schalter()["bereit"])


class FernanfrageTests(LiveTestBasis):
    """Fernanfrage vom Handy: der Host schreibt nur die Anfragedatei, nie den Schalter."""

    def anfrage(self):
        return self.datei.with_name(live_bild.ANFRAGE_DATEI_NAME)

    def test_anfrage_schreibt_nur_die_anfragedatei(self):
        r = self.live.anfrage_stellen()
        self.assertEqual(r, {"ok": True, "grund": None})
        daten = json.loads(self.anfrage().read_text(encoding="utf-8"))
        self.assertEqual((daten["v"], daten["kennung"], daten["besitzer"], daten["ts"]), (1, KENNUNG, BESITZER, self.uhr.t))
        self.assertRegex(daten["id"], r"^[0-9a-f]{16}$")
        self.assertFalse(self.datei.exists(), "den Schalter schreibt nur die Desktop-App")
        self.assertFalse(self.anfrage().with_name(live_bild.ANFRAGE_DATEI_NAME + ".neu").exists())

    def test_schon_an_schreibt_nichts(self):
        self.schreibe()
        self.assertEqual(self.live.anfrage_stellen(), {"ok": True, "grund": "schon_an"})
        self.assertFalse(self.anfrage().exists())

    def test_abstand_zwischen_anfragen(self):
        self.assertTrue(self.live.anfrage_stellen()["ok"])
        self.assertEqual(self.live.anfrage_stellen()["grund"], "zu_schnell")
        self.takt.t += live_bild.ANFRAGE_ABSTAND_SECS + 1
        self.assertTrue(self.live.anfrage_stellen()["ok"])

    def test_nie_fuer_mitglieder_ungebundene_oder_ohne_datei(self):
        self.neu(FesteBindung(stufe="mitglied"))
        self.assertEqual(self.live.anfrage_stellen()["grund"], "stufe")
        self.neu(FesteBindung(stufe=None))
        self.assertEqual(self.live.anfrage_stellen()["grund"], "nicht_eingerichtet")
        self.assertEqual(live_bild.Live(None, None).anfrage_stellen()["grund"], "nicht_eingerichtet")
        self.assertFalse(self.anfrage().exists())

    def test_nicht_beschreibbar_ist_kein_absturz(self):
        self.datei = Path(self.ordner.name) / "fehlt" / live_bild.DATEI_NAME  # Ordner gibt es nicht
        self.neu()
        self.assertEqual(self.live.anfrage_stellen()["grund"], "schreibfehler")

    def test_host_werkzeug_prueft_gesperrten_bildschirm_und_meldet_text(self):
        with mock.patch.object(host, "LIVE", self.live), mock.patch.object(host, "_gesperrt", return_value=None):
            r = host.live_bild_anfordern()
        self.assertEqual((r["status"], r["grund"]), ("success", None))
        with mock.patch.object(host, "LIVE", self.live), \
                mock.patch.object(host, "_gesperrt", return_value={"status": "locked", "summary": "gesperrt"}):
            self.assertEqual(host.live_bild_anfordern()["status"], "locked")


class ZuschauerTaktTests(LiveTestBasis):
    def test_zweiter_zuschauer_wird_abgewiesen(self):
        self.assertTrue(self.live.zuschauer(ZUSCHAUER))
        self.assertFalse(self.live.zuschauer(ANDERER))
        self.assertTrue(self.live.zuschauer(ZUSCHAUER))

    def test_platz_wird_frei_nach_stille_oder_ende(self):
        self.live.zuschauer(ZUSCHAUER)
        self.takt.t += live_bild.ZUSCHAUER_FREI_SECS + 1
        self.assertTrue(self.live.zuschauer(ANDERER))
        self.live.beenden(ANDERER)
        self.assertTrue(self.live.zuschauer(ZUSCHAUER))

    def test_ungueltige_kennung(self):
        for k in (None, "", "kurz", "G" * 16, "a" * 33, 5, "a" * 15 + "\n"):
            self.assertFalse(self.live.zuschauer(k), k)

    def test_abstand_zwischen_bildern(self):
        self.assertEqual(self.live.takt(), 0.0)
        self.assertGreater(self.live.takt(), 0.0)
        self.takt.t += live_bild.MIN_ABSTAND_SECS + 0.01
        self.assertEqual(self.live.takt(), 0.0)

    def test_bilder_je_minute_begrenzt(self):
        angenommen = 0
        for _ in range(live_bild.BILDER_JE_MINUTE + 20):
            self.takt.t += live_bild.MIN_ABSTAND_SECS + 0.001
            if self.live.takt() == 0.0:
                angenommen += 1
            if self.takt.t - 100.0 > 59:
                break
        self.assertLessEqual(angenommen, live_bild.BILDER_JE_MINUTE)
        self.takt.t += 61
        self.assertEqual(self.live.takt(), 0.0)  # nach einer Minute geht es wieder


class BildGroesseTests(unittest.TestCase):
    def test_grenze_wird_eingehalten(self):
        import os

        from PIL import Image

        rauschen = Image.frombytes("RGB", (2560, 1440), os.urandom(2560 * 1440 * 3))  # schlimmster Fall für JPEG
        daten, klein = host.wc.jpeg_begrenzt(rauschen, live_bild.BILD_BYTES_MAX, live_bild.BILD_KANTE_MAX)
        self.assertLessEqual(len(daten), live_bild.BILD_BYTES_MAX)
        self.assertLessEqual(max(klein.size), live_bild.BILD_KANTE_MAX)
        self.assertEqual(daten[:3], b"\xff\xd8\xff")

    def test_unmoegliche_grenze_gibt_kein_bild(self):
        from PIL import Image
        import os

        rauschen = Image.frombytes("RGB", (800, 600), os.urandom(800 * 600 * 3))
        with self.assertRaises(ValueError):
            host.wc.jpeg_begrenzt(rauschen, 10, 800)

    def test_vollbild_erkennung(self):
        monitor = (0, 0, 1920, 1080)
        self.assertTrue(host.wc.ist_vollbild((0, 0, 1920, 1080), monitor, "Chrome_WidgetWin_1"))
        self.assertTrue(host.wc.ist_vollbild((-8, -8, 1928, 1088), monitor, "X"))
        self.assertFalse(host.wc.ist_vollbild((0, 0, 1920, 1040), monitor, "X"))  # Taskleiste bleibt sichtbar
        self.assertFalse(host.wc.ist_vollbild((0, 0, 1920, 1080), monitor, "Progman"))  # Desktop
        self.assertFalse(host.wc.ist_vollbild((100, 100, 900, 700), monitor, "X"))

    def test_monitore_von_links(self):
        class Sct:
            monitors = [{"left": 0, "top": 0, "width": 4480, "height": 1440},
                        {"left": 2560, "top": 0, "width": 1920, "height": 1080},
                        {"left": 0, "top": 0, "width": 2560, "height": 1440}]
        self.assertEqual([m["left"] for m in host.wc.monitore_von_links(Sct())], [0, 2560])


class WerkzeugTests(LiveTestBasis):
    """bild_holen im Host mit Attrappen. Kein echter Bildschirm."""

    def setUp(self):
        super().setUp()
        self.audit: list[dict] = []
        self.aufnahme = mock.Mock(return_value={"monitor": 1, "monitore": 2, "breite": 800, "hoehe": 450, "bytes": 1234,
                                                "jpeg_base64": FALSCHES_BILD})
        patches = [
            mock.patch.object(host, "LIVE", self.live),
            mock.patch.object(host, "_audit", side_effect=self.audit.append),
            mock.patch.object(host, "_letzter_bild_grund", None),
            mock.patch.object(host.wc, "screenshot_live", self.aufnahme),
            mock.patch.object(host.wc, "foreground", return_value={"hwnd": 1, "titel": "Notizen", "programm": "notepad.exe"}),
            mock.patch.object(host.wc, "foreground_vollbild", return_value=False),
            mock.patch.object(host.wc, "monitor_anzahl", return_value=2),
            mock.patch.object(host.wc, "hinweisfenster_zahl", return_value=2),
            mock.patch.object(host.wc, "verborgene_fenster_offen", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        host.PENDING.clear()
        self.schreibe()

    def holen(self, zuschauer=ZUSCHAUER, monitor=1):
        return asyncio.run(host.bild_holen(zuschauer, monitor))

    def nach_takt(self):
        self.takt.t += 1.0

    def test_eingeschaltet_liefert_bild(self):
        e = self.holen()
        self.assertEqual(e["status"], "success")
        self.assertEqual(e["jpeg_base64"], FALSCHES_BILD)
        self.assertEqual(e["monitore"], 2)

    def test_schalter_aus_liefert_nie_ein_bild(self):
        self.schreibe(an=False)
        e = self.holen()
        self.assertEqual((e["status"], e["grund"]), ("aus", "schalter_aus"))
        self.assertNotIn("jpeg_base64", e)
        self.aufnahme.assert_not_called()

    def test_mitglied_und_fremdes_geraet_nie(self):
        self.live._bindung = FesteBindung(stufe="mitglied")
        self.assertEqual(self.holen()["grund"], "stufe")
        self.live._bindung = FesteBindung(kennung="pc-fremd")
        self.assertEqual(self.holen()["status"], "aus")
        self.live._bindung = FesteBindung(besitzer="99999999-2222-3333-4444-555555555555")
        self.assertEqual(self.holen()["status"], "aus")
        self.aufnahme.assert_not_called()

    def test_gesperrter_bildschirm(self):
        with mock.patch.object(host.wc, "is_locked", return_value=True):
            e = self.holen()
        self.assertEqual(e["status"], "locked")
        self.aufnahme.assert_not_called()

    def test_nicht_pruefbare_sperre_gilt_als_gesperrt(self):
        with mock.patch.object(host.wc, "is_locked", side_effect=OSError):
            self.assertEqual(self.holen()["status"], "locked")
        self.aufnahme.assert_not_called()

    def test_offene_freigabe_blendet_aus(self):
        host.PENDING["0123456789abcdef"] = {"art": "tippen", "ablauf": host._jetzt() + 100, "bis": host._jetzt() + 100}
        with mock.patch.object(host, "_freigabe_offen", return_value=True):
            e = self.holen()
        self.assertEqual((e["status"], e["grund"]), ("pausiert", "freigabe"))
        self.aufnahme.assert_not_called()

    def test_vollbild_blendet_aus(self):
        with mock.patch.object(host.wc, "foreground_vollbild", return_value=True):
            e = self.holen()
        self.assertEqual((e["status"], e["grund"]), ("pausiert", "vollbild"))
        self.aufnahme.assert_not_called()

    def test_passwortfenster_blendet_aus(self):
        for ziel in ({"hwnd": 1, "titel": "Passwort eingeben", "programm": "x.exe"},
                     {"hwnd": 1, "titel": "KeePass", "programm": "keepass.exe"},
                     {"hwnd": 1, "titel": "", "programm": ""}):
            self.nach_takt()
            with mock.patch.object(host.wc, "foreground", return_value=ziel):
                self.assertEqual(self.holen()["grund"], "geschuetzt")
        self.aufnahme.assert_not_called()

    def test_ohne_hinweisfenster_je_monitor_kein_bild_auch_bei_gueltiger_datei(self):
        for anzahl in (0, 1):
            with mock.patch.object(host.wc, "hinweisfenster_zahl", return_value=anzahl):
                e = self.holen()
            self.assertEqual((e["status"], e["grund"]), ("aus", "hinweis_fehlt"))
        self.aufnahme.assert_not_called()

    def test_passwortmanager_im_hintergrund_blendet_aus(self):
        with mock.patch.object(host.wc, "verborgene_fenster_offen", return_value=True):
            e = self.holen()
        self.assertEqual((e["status"], e["grund"]), ("pausiert", "geschuetzt"))
        self.aufnahme.assert_not_called()

    def test_aenderung_waehrend_der_aufnahme_verwirft_das_bild(self):
        def aufnahme(*_a):
            self.schreibe(an=False)  # die Person schaltet gerade aus
            return {"monitor": 1, "monitore": 2, "breite": 8, "hoehe": 8, "bytes": 5, "jpeg_base64": FALSCHES_BILD}
        self.aufnahme.side_effect = aufnahme
        e = self.holen()
        self.assertNotEqual(e["status"], "success")
        self.assertNotIn("jpeg_base64", e)

    def test_hinweis_ausgefallen_stoppt_das_bild(self):
        self.schreibe(herzschlag=self.uhr.t - 60)
        self.assertEqual(self.holen()["grund"], "hinweis_fehlt")
        self.aufnahme.assert_not_called()

    def test_zu_schnell(self):
        self.assertEqual(self.holen()["status"], "success")
        e = self.holen()
        self.assertEqual(e["status"], "langsamer")
        self.assertGreater(e["warte_ms"], 0)
        self.assertEqual(self.aufnahme.call_count, 1)

    def test_zweiter_zuschauer(self):
        self.assertEqual(self.holen()["status"], "success")
        self.nach_takt()
        e = self.holen(zuschauer=ANDERER)
        self.assertEqual((e["status"], e["grund"]), ("belegt", "zuschauer"))
        self.assertEqual(self.aufnahme.call_count, 1)

    def test_ungueltige_eingaben(self):
        for z, m in (("", 1), ("x", 1), (ZUSCHAUER, 0), (ZUSCHAUER, 17), (ZUSCHAUER, True), (ZUSCHAUER, "1")):
            self.nach_takt()
            self.assertNotEqual(self.holen(z, m)["status"], "success", (z, m))
        self.aufnahme.assert_not_called()

    def test_bild_zu_gross_ist_kein_bild(self):
        self.aufnahme.side_effect = ValueError("Das Bild passt nicht in die Größengrenze")
        e = self.holen()
        self.assertEqual(e["status"], "rejected")
        self.assertNotIn("jpeg_base64", e)

    def test_aufnahmefehler_wird_gemeldet(self):
        self.aufnahme.side_effect = RuntimeError("boom")
        self.assertEqual(self.holen()["status"], "error")

    def test_audit_enthaelt_nie_ein_bild(self):
        self.holen()
        self.nach_takt()
        self.schreibe(an=False)
        self.holen()
        self.assertTrue(self.audit)
        text = json.dumps(self.audit)
        for verboten in ("jpeg", FALSCHES_BILD, "base64", "/9j/"):
            self.assertNotIn(verboten, text)
        self.assertTrue(all(e["tool"] == "bild_holen" for e in self.audit))

    def test_status_ohne_bild(self):
        e = host.live_bild_status()
        self.assertTrue(e["bereit"])
        self.assertEqual(e["monitore"], 2)
        self.assertNotIn("jpeg_base64", e)
        self.schreibe(an=False)
        e = host.live_bild_status()
        self.assertEqual((e["bereit"], e["grund"]), (False, "schalter_aus"))

    def test_ende_gibt_den_platz_frei(self):
        self.holen()
        host.live_bild_beenden(ZUSCHAUER)
        self.nach_takt()
        self.assertEqual(self.holen(zuschauer=ANDERER)["status"], "success")

    def test_werkzeuge_sind_registriert_aber_nicht_fuer_das_modell(self):
        namen = {t.name for t in asyncio.run(host.mcp.list_tools())}
        self.assertTrue({"bild_holen", "live_bild_status", "live_bild_beenden"} <= namen)


class FensterTests(unittest.TestCase):
    def test_hinweisfenster_werden_gezaehlt_nur_von_der_eigenen_app(self):
        fenster = [("WAMO Live-Bild Hinweis", "WAMO.exe"), ("WAMO Live-Bild Hinweis", "electron.exe"),
                   ("WAMO Live-Bild Hinweis", "notepad.exe"), ("Notizen", "WAMO.exe")]
        with mock.patch.object(host.wc, "_sichtbare_fenster_aller_art", return_value=fenster):
            self.assertEqual(host.wc.hinweisfenster_zahl(), 2)
            self.assertFalse(host.wc.verborgene_fenster_offen())
        with mock.patch.object(host.wc, "_sichtbare_fenster_aller_art", return_value=[("KeePass", "KeePass.exe")]):
            self.assertTrue(host.wc.verborgene_fenster_offen())

    def test_fehler_beim_auflisten_gilt_als_unsicher(self):
        with mock.patch.object(host.wc, "_sichtbare_fenster_aller_art", side_effect=OSError):
            self.assertEqual(host.wc.hinweisfenster_zahl(), 0)
            self.assertTrue(host.wc.verborgene_fenster_offen())


class SchalterSchutzTests(unittest.TestCase):
    def test_befehle_zum_schalter_sind_gesperrt(self):
        for cmd in (r"Set-Content $env:LOCALAPPDATA\WAMO\live-bild.json '{}'", "Get-Content live-bild.json",
                    r"Remove-Item C:\x\live-bild.json"):
            self.assertEqual(host.classify(cmd), "blocked", cmd)

    def test_eigene_app_ist_kein_ziel_fuer_klicks(self):
        for exe in ("WAMO.exe", "wamo.exe", "electron.exe"):
            with self.assertRaises(ValueError):
                host.wc.check_input_target(exe, "")

    def test_datei_liegt_neben_geraet_json(self):
        self.assertEqual(live_bild.datei_neben(r"C:\x\WAMO\geraet.json"), Path(r"C:\x\WAMO") / "live-bild.json")
        self.assertIsNone(live_bild.datei_neben(None))
        self.assertIsNone(live_bild.datei_neben("  "))

    def test_ohne_geraetedatei_gibt_es_kein_live_bild(self):
        self.assertEqual(live_bild.Live(None, None).schalter()["grund"], "nicht_eingerichtet")


if __name__ == "__main__":
    unittest.main()
