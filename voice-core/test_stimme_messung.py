"""Tests für die Auswertung des Szenariotests (scripts/wamo_stimme.py) mit künstlichen Signalen."""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import wamo_stimme as w  # noqa: E402

RATE = 24000


def signal(abschnitte, dauer, pegel=0.1, fade=None):
    """Rauschen mit Pegel in den Abschnitten (Beginn, Ende in s), sonst Stille. fade: (Beginn, Ende) lineares Ausblenden."""
    x = np.zeros(int(dauer * RATE), dtype=np.float32)
    rng = np.random.default_rng(1)
    for a, b in abschnitte:
        i, j = int(round(a * RATE)), int(round(b * RATE))
        x[i:j] = rng.standard_normal(j - i) * pegel
    if fade:
        a, b = int(fade[0] * RATE), int(fade[1] * RATE)
        x[a:b] *= np.linspace(1.0, 0.0, b - a)
        x[b:] = 0
    return x


class HuellkurveTest(unittest.TestCase):
    def test_abschnitte_und_pausen(self):
        env = w.huellkurve(signal([(1.0, 2.0), (2.3, 3.0)], 4.0), RATE)
        segs = w.segmente(env, w.SCHWELLE_BOT)
        self.assertEqual([(round(a, 1), round(b, 1)) for a, b in segs], [(1.0, 2.0), (2.3, 3.0)])

    def test_kurze_luecken_gehoeren_zum_abschnitt(self):
        env = w.huellkurve(signal([(1.0, 2.0), (2.03, 3.0)], 4.0), RATE)
        self.assertEqual(len(w.segmente(env, w.SCHWELLE_BOT, min_luecke=0.06)), 1)
        self.assertEqual(len(w.segmente(env, w.SCHWELLE_BOT, min_luecke=0.01)), 2)

    def test_stille_hat_keine_abschnitte(self):
        self.assertEqual(w.segmente(w.huellkurve(np.zeros(RATE), RATE), w.SCHWELLE_BOT), [])
        self.assertEqual(len(w.huellkurve(np.zeros(5), RATE)), 0)


class ZeitenTest(unittest.TestCase):
    SEGS = [(1.0, 2.0), (2.4, 3.5), (3.9, 4.4), (9.0, 10.0)]

    def test_erster_ton_ignoriert_laufende_abschnitte(self):
        self.assertEqual(w.erster_ton(self.SEGS, 2.9), 3.9)
        self.assertEqual(w.erster_ton(self.SEGS, 4.5), 9.0)
        self.assertIsNone(w.erster_ton(self.SEGS, 9.5))

    def test_verstummen_nach_langer_stille(self):
        self.assertEqual(w.verstummen(self.SEGS, 2.6), 4.4, "Pausen unter 0,9 s sind Satzpausen")
        self.assertIsNone(w.verstummen([(1.0, 2.0), (2.4, 3.5)], 5.0))

    def test_aktiv_bei(self):
        self.assertTrue(w.aktiv_bei(self.SEGS, 2.2, 0.3), "kurze Wortpause")
        self.assertFalse(w.aktiv_bei(self.SEGS, 6.0, 0.3))

    def test_luecken_einer_antwort(self):
        lk = w.luecken(self.SEGS, 1.0, 5.0)
        self.assertEqual([round(x, 1) for x in lk], [0.4, 0.4])
        self.assertEqual([round(x, 1) for x in w.luecken(self.SEGS, 1.0, 10.5)], [0.4, 0.4], "ab 2,5 s Stille beginnt eine neue Antwort")

    def test_antwortbloecke(self):
        self.assertEqual(w.antwortbloecke(self.SEGS, 0.0, 20.0), [(1.0, 4.4), (9.0, 10.0)])


class SchnittTest(unittest.TestCase):
    def test_harter_schnitt(self):
        env = w.huellkurve(signal([(0.0, 3.0)], 4.0), RATE)
        info = w.schnitt_info(env, 3.0)
        self.assertTrue(info["hart"])
        self.assertLessEqual(info["fall_ms"], 30)

    def test_weiches_ausblenden(self):
        env = w.huellkurve(signal([(0.0, 3.2)], 4.0, fade=(3.0, 3.14)), RATE)
        ende = w.segmente(env, w.SCHWELLE_BOT)[-1][1]
        info = w.schnitt_info(env, ende)
        self.assertFalse(info["hart"], info)
        self.assertGreaterEqual(info["fall_ms"], 40)

    def test_ohne_vorherigen_ton_keine_aussage(self):
        self.assertEqual(w.schnitt_info(np.zeros(100), 0.5), {"fall_ms": None, "hart": None})


class DipTest(unittest.TestCase):
    """Reaktion der Oberfläche aus dem Lautstärkeverlauf des Wiedergabeelements."""

    def test_daempfen_und_zurueck(self):
        vol = [[10.0, 1.0], [10.3, 0.6], [10.4, 0.3], [11.0, 0.3], [11.1, 0.7], [11.3, 1.0]]
        d = w.lautstaerke_dip(vol, 10.0, 14.0)
        self.assertAlmostEqual(d["erste"], 0.3, places=3)
        self.assertEqual(d["min"], 0.3)
        self.assertAlmostEqual(d["dauer"], 1.0, delta=0.05)

    def test_ohne_aenderung_keine_reaktion(self):
        d = w.lautstaerke_dip([[3.0, 1.0], [9.0, 1.0]], 10.0, 14.0)
        self.assertIsNone(d["erste"])
        self.assertEqual((d["dauer"], d["min"]), (0.0, 1.0))

    def test_schon_gedaempft_zu_beginn(self):
        d = w.lautstaerke_dip([[8.0, 0.3]], 10.0, 11.0)
        self.assertEqual(d["erste"], 0.0)
        self.assertAlmostEqual(d["dauer"], 1.0)

    def test_ausblenden_bis_null(self):
        vol = [[10.5, 0.8], [10.55, 0.4], [10.6, 0.0]]
        d = w.lautstaerke_dip(vol, 10.0, 12.0)
        self.assertAlmostEqual(d["erste"], 0.5, places=3)
        self.assertEqual(d["min"], 0.0)

    def test_werte_ohne_uhr_zaehlen_nicht(self):
        self.assertIsNone(w.lautstaerke_dip([[-1, 0.0]], 10.0, 12.0)["erste"])


class LautstaerkeTest(unittest.TestCase):
    def test_stufenverlauf(self):
        t = np.array([0.5, 1.0, 1.5, 2.5])
        v = w.lautstaerke_verlauf([[1.0, 0.5], [2.0, 0.0]], t)
        self.assertEqual(list(v), [1.0, 0.5, 0.5, 0.0])

    def test_ohne_ereignisse_laut(self):
        self.assertEqual(list(w.lautstaerke_verlauf([], np.array([1.0, 2.0]))), [1.0, 1.0])
        self.assertEqual(list(w.lautstaerke_verlauf([[-1, 0.2]], np.array([1.0]))), [1.0], "ohne Uhr gesetzte Werte zählen nicht")

    def test_gehoert_multipliziert(self):
        bot = np.ones(RATE, dtype=np.float32)
        h = w.gehoert(bot, RATE, 10.0, [[10.5, 0.0]])
        self.assertEqual(float(h[0]), 1.0)
        self.assertEqual(float(h[-1]), 0.0)


class KernLogTest(unittest.TestCase):
    def test_ereignisse_mit_zeit(self):
        text = (
            "\x1b[32m2026-10-22 07:20:49.083\x1b[0m | INFO     | x - weiche Unterbrechung (Wortgrenze)\n"
            "2026-10-22 07:20:50.000 | INFO | y - Rückmeldung erkannt, WAMO redet weiter (insgesamt 1)\n"
            "2026-10-22 07:20:51.250 | INFO | z - Stimme gedämpft\n"
            "2026-10-22 07:20:52.000 | INFO | l - LATENZ_JSON {\"nummer\": 1, \"ms\": {\"ausgabe\": 1800}}\n"
            "kein Zeitstempel weiche Unterbrechung (Satzende)\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.log"
            p.write_text(text, encoding="utf-8")
            ev = w.kern_ereignisse(p)
        arten = [e["art"] for e in ev]
        self.assertEqual(arten, ["weiche_unterbrechung", "rueckmeldung", "ducken", "latenz"])
        self.assertEqual(ev[0]["grund"], "Wortgrenze")
        self.assertAlmostEqual(ev[2]["t"] - ev[0]["t"], 2.167, places=2)
        self.assertEqual(ev[3]["zug"]["ms"]["ausgabe"], 1800)

    def test_werkzeug_ereignisse(self):
        text = (
            "2026-10-31 10:00:00.100 | INFO | g - Zug bei laufendem Werkzeug, es wird abgebrochen\n"
            "2026-10-31 10:00:01.100 | INFO | s - Rückmeldung bei laufendem Werkzeug, kein Zug, das Werkzeug läuft weiter (insgesamt 1)\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.log"
            p.write_text(text, encoding="utf-8")
            arten = [e["art"] for e in w.kern_ereignisse(p)]
        self.assertEqual(arten, ["zug_bei_werkzeug", "rueckmeldung_werkzeug"])

    def test_werkzeug_mhm_kennzahlen(self):
        daten = {"werkzeug_mhm": [
            {"ok": True, "ergebnis_nach_mhm": True, "ereignisse": [
                {"rolle": "werkzeug_mhm", "name": "mhm", "zug_gestartet": False, "als_rueckmeldung": True},
                {"rolle": "werkzeug_mhm", "name": "ja_genau", "zug_gestartet": False, "als_rueckmeldung": True}]},
            {"ok": True, "ergebnis_nach_mhm": False, "ereignisse": [
                {"rolle": "werkzeug_mhm", "name": "mhm", "zug_gestartet": True, "als_rueckmeldung": False}]},
        ]}
        z = w.zusammenfassen(daten)
        self.assertEqual((z["werkzeug_mhm_ereignisse"], z["werkzeug_mhm_zug"], z["werkzeug_mhm_rueckmeldung"]), (3, 1, 2))
        self.assertEqual(z["werkzeug_mhm_ergebnis"], [True, False])

    def test_innehalten_ereignisse(self):
        text = (
            "2026-11-14 10:00:00.100 | INFO | g - SoftInterruptGate#3: WAMO hält inne (Wortgrenze, Ansatz 1 Wort)\n"
            "2026-11-14 10:00:01.900 | INFO | g - SoftInterruptGate#3: WAMO spricht weiter (kein weiteres Wort, Ansatz 1 Wort)\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.log"
            p.write_text(text, encoding="utf-8")
            arten = [e["art"] for e in w.kern_ereignisse(p)]
        self.assertEqual(arten, ["innehalten", "weitersprechen"])

    def test_fehlende_datei(self):
        self.assertEqual(w.kern_ereignisse(Path("gibt-es-nicht.log")), [])

    def test_im_fenster(self):
        ev = [{"t": 1.0, "art": "a"}, {"t": 2.0, "art": "b"}, {"t": 3.0, "art": "a"}]
        self.assertEqual(len(w.im_fenster(ev, ("a",), 0.5, 2.5)), 1)


class SzenarioTest(unittest.TestCase):
    """Ganzer Durchlauf mit künstlichem Einwurf: Stimme läuft, Person spricht bei 7 s, WAMO verstummt bei 8,6 s."""

    def test_einwurf(self):
        dauer = 20.0
        bot = signal([(2.0, 8.6), (13.0, 16.0)], dauer, fade=None)
        bot[int(8.6 * RATE):int(13.0 * RATE)] = 0
        mic = signal([(0.5, 1.5), (7.0, 9.0)], dauer, pegel=0.1)
        sz = {
            "sr": RATE, "von": 100.0, "bis": 120.0, "wallDiff": 1_000_000.0, "vol": [], "name": "einwurf",
            "events": [
                {"typ": "rep", "rep": 0, "t0": 100.0},
                {"typ": "clip", "name": "frage", "t0": 100.5, "dauer": 1.0, "rolle": "frage", "rep": 0},
                {"typ": "clip", "name": "einwurf", "t0": 107.0, "dauer": 2.0, "rolle": "einwurf", "rep": 0},
            ],
            "ergebnisse": [{"rep": 0, "ok": True}],
        }
        kern = [{"t": 1000.0 + 107.5, "art": "weiche_unterbrechung", "grund": "Wortgrenze"}]  # wallDiff 1e6 ms = 1000 s
        r = w.analysiere_szenario(sz, bot, mic, kern)[0]
        self.assertTrue(r["ok"])
        ev = r["ereignisse"][0]
        self.assertTrue(ev["bot_sprach"])
        self.assertFalse(ev["verpasst"])
        self.assertAlmostEqual(ev["verstummen_ms"], 1600, delta=60)
        self.assertEqual(ev["grund"], "Wortgrenze")
        self.assertTrue(ev["hart"], "das künstliche Signal endet abrupt")
        self.assertAlmostEqual(r["erster_ton_ms"], 500, delta=60)
        z = w.zusammenfassen({"einwurf": [r]})
        self.assertEqual((z["einwurf_gueltig"], z["einwurf_verpasst"]), (1, 0))
        self.assertEqual(z["gruende"], {"Wortgrenze": 1})

    def test_einwurf_ohne_laufende_stimme_ist_ungueltig(self):
        bot = signal([(1.0, 2.0)], 20.0)
        mic = signal([(7.0, 9.0)], 20.0)
        sz = {
            "sr": RATE, "von": 0.0, "bis": 20.0, "wallDiff": 0.0, "vol": [], "name": "einwurf",
            "events": [{"typ": "rep", "rep": 0, "t0": 0.0},
                       {"typ": "clip", "name": "e", "t0": 7.0, "dauer": 2.0, "rolle": "einwurf", "rep": 0}],
            "ergebnisse": [{"rep": 0, "ok": True}],
        }
        r = w.analysiere_szenario(sz, bot, mic, [])[0]
        self.assertFalse(r["ereignisse"][0]["bot_sprach"])
        z = w.zusammenfassen({"einwurf": [r]})
        self.assertEqual((z["einwurf_gueltig"], z["einwurf_ungueltig"]), (0, 1))

    def ein_wort(self, bot, kern):
        """Szenario ein_wort: lange Antwort, die Person sagt bei 7 s "Danke" (0,5 s lang), Aufnahme bis 20 s."""
        mic = signal([(0.5, 1.5), (7.0, 7.5)], 20.0, pegel=0.1)
        sz = {
            "sr": RATE, "von": 100.0, "bis": 120.0, "wallDiff": 1_000_000.0, "vol": [], "name": "ein_wort",
            "events": [
                {"typ": "rep", "rep": 0, "t0": 100.0},
                {"typ": "clip", "name": "frage", "t0": 100.5, "dauer": 1.0, "rolle": "frage", "rep": 0},
                {"typ": "clip", "name": "ein_wort", "t0": 107.0, "dauer": 0.5, "rolle": "ein_wort", "rep": 0},
            ],
            "ergebnisse": [{"rep": 0, "ok": True}],
        }
        return w.analysiere_szenario(sz, bot, mic, kern)[0]

    def test_ein_wort_haelt_inne_und_spricht_weiter(self):
        bot = signal([(2.0, 7.4), (8.6, 14.0)], 20.0)  # 1,2 s Stille mitten in der Antwort
        kern = [{"t": 1000.0 + 107.6, "art": "innehalten", "grund": ""}, {"t": 1000.0 + 108.6, "art": "weitersprechen", "grund": ""}]
        ev = self.ein_wort(bot, kern)["ereignisse"][0]
        self.assertEqual(ev["rolle"], "ein_wort")
        self.assertTrue(ev["bot_sprach"])
        self.assertTrue(ev["innegehalten"])
        self.assertTrue(ev["weiter"])
        self.assertAlmostEqual(ev["pause_ms"], 1200, delta=60)
        self.assertAlmostEqual(ev["innehalten_ms"], 400, delta=60)
        self.assertFalse(ev["abgebrochen_log"])
        self.assertTrue(ev["innehalten_log"] and ev["weiter_log"])

    def test_ein_wort_das_zum_abbruch_fuehrte(self):
        bot = signal([(2.0, 7.4)], 20.0)  # danach nichts mehr
        kern = [{"t": 1000.0 + 107.6, "art": "weiche_unterbrechung", "grund": "Wortgrenze"}]
        ev = self.ein_wort(bot, kern)["ereignisse"][0]
        self.assertFalse(ev["innegehalten"], "WAMO hat danach nicht weitergesprochen")
        self.assertFalse(ev["weiter"])
        self.assertTrue(ev["abgebrochen_log"])

    def test_ein_wort_ohne_pause_ist_ein_fehlschlag_und_zaehlt_nicht_als_innehalten(self):
        bot = signal([(2.0, 14.0)], 20.0)  # WAMO redet einfach weiter
        ev = self.ein_wort(bot, [])["ereignisse"][0]
        self.assertFalse(ev["innegehalten"])
        self.assertFalse(ev["innehalten_log"])

    def test_ein_wort_kennzahlen(self):
        gut = {"rolle": "ein_wort", "name": "ein_wort", "bot_sprach": True, "innegehalten": True, "weiter": True, "pause_ms": 1200,
               "innehalten_ms": 400, "abgebrochen_log": False, "innehalten_log": True, "weiter_log": True}
        schlecht = {**gut, "innegehalten": False, "weiter": False, "pause_ms": None, "innehalten_ms": None, "abgebrochen_log": True,
                    "innehalten_log": False, "weiter_log": False}
        ungueltig = {**gut, "bot_sprach": False}
        z = w.zusammenfassen({"ein_wort": [{"ok": True, "ereignisse": [gut]}, {"ok": True, "ereignisse": [schlecht]},
                                           {"ok": True, "ereignisse": [ungueltig]}, {"ok": False}]})
        self.assertEqual(z["ein_wort_ereignisse"], 2)
        self.assertEqual((z["ein_wort_innegehalten"], z["ein_wort_abgebrochen"], z["ein_wort_log"], z["ein_wort_weiter_log"]), (1, 1, 1, 1))
        self.assertEqual(z["ein_wort_pause_ms"], [1200])
        self.assertEqual(z["ein_wort_ungueltig"], 2, "keine laufende Stimme und eine fehlgeschlagene Wiederholung")

    def test_pause_dazwischen(self):
        bot = signal([(3.5, 5.0)], 12.0)
        mic = signal([(0.5, 2.0), (6.0, 8.0)], 12.0)
        sz = {
            "sr": RATE, "von": 0.0, "bis": 12.0, "wallDiff": 0.0, "vol": [], "name": "pause",
            "events": [{"typ": "rep", "rep": 0, "t0": 0.0},
                       {"typ": "clip", "name": "a", "t0": 0.5, "dauer": 1.5, "rolle": "pause_a", "rep": 0},
                       {"typ": "clip", "name": "b", "t0": 6.0, "dauer": 2.0, "rolle": "pause_b", "rep": 0}],
            "ergebnisse": [{"rep": 0, "ok": True, "luecke": 2.5}],
        }
        r = w.analysiere_szenario(sz, bot, mic, [])[0]
        self.assertTrue(r["dazwischen"], "WAMO sprach in die Pause")
        self.assertAlmostEqual(r["dazwischen_nach_ms"], 1500, delta=60)


class WanduhrTest(unittest.TestCase):
    def test_zeit_haengt_am_naechsten_stempel(self):
        sz = {"wallDiff": 5000.0, "events": [
            {"typ": "rep", "rep": 0, "t0": 100.0, "wall": 2_000_000},  # 2000 s bei 100 s Kontextzeit
            {"typ": "clip", "t0": 130.03, "jetzt": 130.0, "wall": 2_030_100},  # die Uhren laufen um 0,1 s auseinander
        ]}
        wall = w.wanduhr(sz)
        self.assertAlmostEqual(wall(101.0), 2001.0)
        self.assertAlmostEqual(wall(131.0), 2031.1, msg="am Stempel der Sprechprobe, nicht am Beginn der Wiederholung")

    def test_ohne_stempel_wallDiff(self):
        self.assertAlmostEqual(w.wanduhr({"wallDiff": 5000.0, "events": []})(10.0), 15.0)


class ClipsTest(unittest.TestCase):
    def test_zuschneiden(self):
        x = np.concatenate([np.zeros(RATE), np.ones(100) * 0.5, np.zeros(RATE)]).astype(np.float32)
        y = w.zuschneiden(x, RATE)
        self.assertLess(len(y), len(x))
        self.assertEqual(len(y), 100 + 2 * int(0.03 * RATE))
        self.assertEqual(len(w.zuschneiden(np.zeros(10, dtype=np.float32), RATE)), 10)

    def test_wav_hin_und_zurueck(self):
        x = (np.sin(np.arange(RATE) / 20) * 0.5).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.wav"
            w.schreibe_wav(p, x, RATE)
            rate, y = w.lade_wav(p)
        self.assertEqual(rate, RATE)
        self.assertLess(float(np.max(np.abs(x - y))), 1e-3)


class FolgeTonTest(unittest.TestCase):
    """Antwortzeit nach dem Überlappen: vom Ende des Einwurfs bis zum Beginn der Folgeantwort."""

    def test_folgeantwort_nach_dem_einwurf(self):
        segs = [(1.0, 6.0), (9.5, 12.0)]  # WAMO redet, verstummt bei 6,0 s, antwortet ab 9,5 s
        self.assertAlmostEqual(w.folge_ton(segs, 6.0, 8.0), 1.5)

    def test_keine_folgeantwort(self):
        self.assertIsNone(w.folge_ton([(1.0, 6.0)], 6.0, 8.0))

    def test_aus_dem_signal(self):
        env = w.huellkurve(signal([(1.0, 3.0), (6.0, 8.0)], 10.0), RATE)
        segs = w.segmente(env, w.SCHWELLE_BOT)
        tv = w.verstummen(segs, 1.5)
        self.assertAlmostEqual(tv, 3.0, delta=0.05)
        self.assertAlmostEqual(w.folge_ton(segs, tv, 4.5), 1.5, delta=0.05)


if __name__ == "__main__":
    unittest.main()
