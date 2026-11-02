"""Tests der Bewährung (Schritt 3c): Anker-Befehl bewaehren, Stufe bewaehren im Release-Dienst und der ganze Weg bis bewaehrt.

Teil 1: Vorwaerts.bewaehren mit Attrappen und einer gestellten Uhr (30 Minuten, Probenabstand, drei schlechte Proben, Idempotenz). Teil 2: die Verdrahtung im Anker
mit echter Zustandsdatei (letzter guter Stand, Rückweg, Protokoll nur bei Entscheidungen, Rechte). Teil 3: die Stufe im Release-Dienst (wartet, ok, Fehler). Teil 4: der
Zustandsautomat läuft nach umgeschaltet in die Stufe und endet bei bewaehrt oder zurueckgenommen. Nichts berührt Sokrates-2.
"""

import calendar
import os
import sys
import time
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent
ANKER = HIER.parent / "infra" / "sokrates2" / "anker"
RELEASE = HIER.parent / "infra" / "sokrates2" / "release"
sys.path.insert(0, str(HIER))
sys.path.insert(0, str(ANKER))
sys.path.insert(0, str(RELEASE))
sys.path.insert(0, str(RELEASE / "pruefer"))

import release_anker as RA  # noqa: E402
import release_automat as AU  # noqa: E402
import release_stufen as ST  # noqa: E402
import stufen_echt as SE  # noqa: E402
import test_anker as TA  # noqa: E402
import test_release as TR  # noqa: E402
import test_stufen_echt as TS  # noqa: E402
import test_vorwaerts as V  # noqa: E402
from vorwaerts import BEWAEHRUNG_S, PROBE_ABSTAND_S, PROBEN_SCHLECHT_MAX, Vorwaerts, VorwaertsFehler  # noqa: E402

ID = V.ID
COMMIT = "c" * 40
JETZT = 1_800_000_000.0


def iso(sekunden):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sekunden))


class FakeDbBew:
    def __init__(self, status="umgeschaltet"):
        self.status = status
        self.gesetzt: list[tuple] = []

    def anfrage_stand(self, k):
        return {"status": self.status, "art": "normal", "basis_commit": None, "c1_commit": None, "r_commit": None, "v_commit": None} if k == ID else None

    def status_setzen(self, k, von, nach):
        if self.status != von:
            raise VorwaertsFehler("status_nicht_gesetzt")
        self.status = nach
        self.gesetzt.append((von, nach))


class FakeWerkProbe:
    def __init__(self, gesund=True):
        self.gesund, self.proben = gesund, 0

    def kern_probe(self):
        self.proben += 1
        return self.gesund


class FakeZugang:
    def __init__(self, seit, uhr, zurueck_ok=True):
        self.eintrag = {"version": "v0.2.0", "commit": COMMIT, "ziel": "b", "seit": iso(seit)} if seit is not None else None
        self.uhr, self.zurueck_ok = uhr, zurueck_ok
        self.gut = None
        self.zurueck_aufrufe = 0

    def aktiv(self):
        return self.eintrag

    def jetzt(self):
        return self.uhr[0]

    def festhalten(self, eintrag):
        self.gut = dict(eintrag)

    def zurueck(self):
        self.zurueck_aufrufe += 1
        return self.zurueck_ok


class Logik(unittest.TestCase):
    def setUp(self):
        self.uhr = [JETZT]
        self.db, self.werk = FakeDbBew(), FakeWerkProbe()
        self.v = Vorwaerts(self.db, None, self.werk, "/tmp")
        self.z = FakeZugang(JETZT - 60, self.uhr)

    def probe(self, **kw):
        return self.v.bewaehren(ID, self.z, **kw)

    def schritt(self, s=PROBE_ABSTAND_S):
        self.uhr[0] += s

    def test_die_ersten_dreissig_minuten_laeuft_es_nur(self):
        erg = self.probe()
        self.assertEqual((erg["stand"], erg["rest_s"]), ("laeuft", BEWAEHRUNG_S - 60))
        self.assertEqual((self.werk.proben, self.db.gesetzt, self.z.gut), (1, [], None))

    def test_nach_dreissig_gesunden_minuten_ist_der_kern_bewaehrt(self):
        self.uhr[0] = JETZT - 60 + BEWAEHRUNG_S + 1
        self.assertEqual(self.probe()["stand"], "bewaehrt")
        self.assertEqual(self.z.gut, self.z.eintrag)   # letzte gute Stand := aktiver Stand
        self.assertEqual(self.db.gesetzt, [("umgeschaltet", "bewaehrt")])

    def test_genau_an_der_grenze_noch_nicht(self):
        self.uhr[0] = JETZT - 60 + BEWAEHRUNG_S - 1
        self.assertEqual(self.probe()["stand"], "laeuft")

    def test_proben_kommen_hoechstens_alle_zehn_sekunden(self):
        for _ in range(20):
            self.probe()   # der Release-Dienst fragt alle 5 Sekunden, hier ohne Zeitablauf
        self.assertEqual(self.werk.proben, 1)
        self.schritt()
        self.probe()
        self.assertEqual(self.werk.proben, 2)

    def test_drei_schlechte_proben_in_folge_loesen_den_rueckweg_aus(self):
        self.werk.gesund = False
        for i in range(PROBEN_SCHLECHT_MAX - 1):
            erg = self.probe()
            self.assertEqual((erg["stand"], erg["schlecht"]), ("laeuft", i + 1))
            self.schritt()
        self.assertEqual(self.probe()["stand"], "zurueckgenommen")
        self.assertEqual((self.z.zurueck_aufrufe, self.db.gesetzt, self.z.gut), (1, [("umgeschaltet", "zurueckgenommen")], None))

    def test_eine_gesunde_probe_setzt_die_zaehlung_zurueck(self):
        self.werk.gesund = False
        self.probe()
        self.schritt()
        self.probe()
        self.werk.gesund = True
        self.schritt()
        self.assertEqual(self.probe()["schlecht"], 0)
        self.werk.gesund = False
        for _ in range(PROBEN_SCHLECHT_MAX - 1):
            self.schritt()
            self.assertEqual(self.probe()["stand"], "laeuft")
        self.assertEqual(self.z.zurueck_aufrufe, 0)

    def test_scheitert_der_rueckweg_bleibt_der_status_und_es_ist_ein_fehler(self):
        self.werk.gesund = False
        self.z.zurueck_ok = False
        for _ in range(PROBEN_SCHLECHT_MAX - 1):
            self.probe()
            self.schritt()
        with self.assertRaises(VorwaertsFehler) as e:
            self.probe()
        self.assertEqual(e.exception.code, "rueckweg_fehlgeschlagen")
        self.assertEqual(self.db.gesetzt, [])

    def test_wiederholung_nach_einem_abbruch_zwischen_stand_und_status(self):
        self.uhr[0] = JETZT + BEWAEHRUNG_S
        self.db.status_setzen = lambda *a: (_ for _ in ()).throw(VorwaertsFehler("status_nicht_gesetzt"))
        with self.assertRaises(VorwaertsFehler):
            self.probe()
        self.assertIsNotNone(self.z.gut)   # der Stand steht schon
        del self.db.status_setzen
        self.schritt()
        self.assertEqual(self.probe()["stand"], "bewaehrt")   # der nächste Aufruf führt beides ohne Schaden zu Ende

    def test_schon_bewaehrt_ist_in_ordnung_und_andere_status_sind_ein_fehler(self):
        self.db.status = "bewaehrt"
        self.assertEqual(self.probe()["stand"], "bewaehrt")
        self.assertEqual(self.werk.proben, 0)
        for status in ("angefragt", "wartet_freigabe", "freigegeben", "veroeffentlicht", "bereitgestellt", "zurueckgenommen", "abgebrochen"):
            self.db.status = status
            with self.assertRaises(VorwaertsFehler) as e:
                self.probe()
            self.assertEqual(e.exception.code, "status_falsch", status)

    def test_ohne_aktiven_stand_und_mit_schlechter_kennung(self):
        for z in (FakeZugang(None, self.uhr),):
            with self.assertRaises(VorwaertsFehler) as e:
                self.v.bewaehren(ID, z)
            self.assertEqual(e.exception.code, "kein_aktiver_stand")
        for kennung, code in (("nix", "anfrage_ungueltig"), ("1" * 8 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 12, "anfrage_unbekannt")):
            with self.assertRaises(VorwaertsFehler) as e:
                self.v.bewaehren(kennung, self.z)
            self.assertEqual(e.exception.code, code)

    def test_trockenlauf_probt_nicht_und_aendert_nichts(self):
        self.uhr[0] = JETZT + BEWAEHRUNG_S
        erg = self.probe(trocken=True)
        self.assertEqual((erg["stand"], erg["trocken"]), ("laeuft", True))
        self.assertEqual((self.werk.proben, self.db.gesetzt, self.z.gut), (0, [], None))


class ImAnker(unittest.TestCase):
    """Die Verdrahtung in wamo_anker.py mit einer echten Zustandsdatei."""

    def setUp(self):
        class Fall(TA.AnkerTest):
            def runTest(self):
                pass
        self.f = Fall()
        self.f.setUp()
        self.addCleanup(self.f.tmp.cleanup)
        self.f.schritte_anlegen(namen=("rueckweg-ui", "rueckweg-kern", "kern-slots"))
        self.db, self.werk = FakeDbBew(), FakeWerkProbe()
        self.echt = Vorwaerts(self.db, None, self.werk, str(self.f.tmp.name))
        self.f.a = self.f.neuer_anker(vorwaerts_fabrik=lambda: self.echt)
        self.aktiv = {"version": "v0.2.0", "commit": COMMIT, "ziel": "b", "seit": iso(self.f.zeit[0] - 120)}
        self.gut = {"version": "v0.1.0", "commit": "a" * 40, "ziel": "a", "seit": iso(self.f.zeit[0] - 86400)}
        self.f.stand_setzen(kern_aktiv=self.aktiv, kern_letzte_gute=self.gut)

    def stand(self):
        return TA.A.stand_laden(self.f.p.stand, self.f.p.vertrauens_uid)["komponenten"]["kern"]

    def test_die_ersten_minuten_aendern_weder_stand_noch_protokoll(self):
        r = self.f.sende(f"bewaehren {ID}", "wamo-release")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["ergebnis"]["stand"], "laeuft")
        self.assertEqual(self.stand()["letzte_gute"], self.gut)
        self.assertNotIn("bewaehren", [z["ereignis"] for z in self.f.audit()])   # nur Entscheidungen kommen ins begrenzte, nur anhängbare Protokoll

    def test_nach_dreissig_minuten_wird_der_aktive_stand_zum_letzten_guten(self):
        self.f.zeit[0] += BEWAEHRUNG_S
        r = self.f.sende(f"bewaehren {ID}", "wamo-release")
        self.assertEqual(r["ergebnis"]["stand"], "bewaehrt")
        k = self.stand()
        self.assertEqual((k["letzte_gute"], k["aktiv"]), (self.aktiv, self.aktiv))
        self.assertEqual(self.db.gesetzt, [("umgeschaltet", "bewaehrt")])
        z = [z for z in self.f.audit() if z["ereignis"] == "bewaehren"]
        self.assertEqual([(x["ok"], x["stand"]) for x in z], [(True, "bewaehrt")])

    def test_drei_schlechte_proben_rufen_den_rueckweg_des_kerns(self):
        self.werk.gesund = False
        for _ in range(PROBEN_SCHLECHT_MAX):
            r = self.f.sende(f"bewaehren {ID}", "wamo-release")
            self.f.zeit[0] += PROBE_ABSTAND_S
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["ergebnis"]["stand"], "zurueckgenommen")
        self.assertEqual(self.db.gesetzt, [("umgeschaltet", "zurueckgenommen")])
        schritte = [a[0][0] for a in self.f.aufrufe]
        self.assertTrue(any(s.endswith("rueckweg-kern") for s in schritte), schritte)   # der Rückweg lief über das geprüfte Schritt-Skript

    def test_antwort_hat_nur_bekannte_felder_und_der_trockenlauf_aendert_nichts(self):
        r = self.f.sende(f"bewaehren {ID} --trocken", "wamo-release")
        self.assertTrue(r["ok"])
        self.assertEqual(sorted(r["ergebnis"]), ["rest_s", "stand"])
        self.assertEqual((self.werk.proben, self.db.gesetzt), (0, []))

    def test_fehler_kommen_als_code(self):
        self.db.status = "veroeffentlicht"
        r = self.f.sende(f"bewaehren {ID}", "wamo-release")
        self.assertEqual((r["ok"], r["grund"], r["detail"]), (False, "vorwaerts_fehlgeschlagen", "status_falsch veroeffentlicht"))

    def test_rechte_und_argumente(self):
        self.assertTrue(self.f.sende(f"bewaehren {ID}", "wamocon")["ok"])
        for arg in ("", "x y", "../x"):
            self.assertFalse(self.f.sende(f"bewaehren {arg}".strip(), "wamo-release")["ok"], arg)

    def test_ohne_fabrik_nicht_verfuegbar_und_das_skript_muss_vertrauenswuerdig_sein(self):
        a = self.f.neuer_anker()
        r = a.bearbeite(self.f.zeile(f"bewaehren {ID}"), None, TA.A.Peer(100, TA.UIDS["wamo-release"], TA.UIDS["wamo-release"]), "wamo-release")
        self.assertEqual(r["grund"], "nicht_verfuegbar")
        with open(os.path.join(self.f.p.schritte, "kern-slots"), "ab") as fh:
            fh.write(b"# manipuliert\n")
        r = self.f.sende(f"bewaehren {ID}", "wamo-release")
        self.assertFalse(r["ok"])


class StufeBewaehren(unittest.TestCase):
    def setUp(self):
        self.anker = TS.StufenAnker(COMMIT)
        self.stufen = SE.EchteStufen(None, self.anker, pruefen_mit=lambda *a: None)

    def antwort(self, stand):
        self.anker.antworten["bewaehren"] = RA.Antwort(True, None, {"ok": True, "ergebnis": {"stand": stand}})

    def test_bewaehrt_ist_ok_laeuft_ist_warten(self):
        k = TS.kontext(status="umgeschaltet")
        self.antwort("bewaehrt")
        self.assertEqual(self.stufen.bewaehren(k).art, "ok")
        self.antwort("laeuft")
        self.assertEqual(self.stufen.bewaehren(k).art, "wartet")
        self.assertEqual(self.anker.aufrufe[0], ("bewaehren", k.anfrage, False))

    def test_zurueckgenommen_und_unbekanntes_sind_ein_fehler(self):
        for stand in ("zurueckgenommen", "komisch", None):
            self.antwort(stand)
            erg = self.stufen.bewaehren(TS.kontext(status="umgeschaltet"))
            self.assertEqual((erg.art, erg.grund), ("fehler", "bewaehrung_gescheitert"), stand)

    def test_vorubergehendes_heisst_warten_nie_aufgeben(self):
        self.anker.antworten["bewaehren"] = RA.AnkerFehler("nicht_erreichbar")
        self.assertEqual(self.stufen.bewaehren(TS.kontext(status="umgeschaltet")).art, "wartet")
        for grund in ("belegt", "nicht_verfuegbar"):
            self.anker.antworten["bewaehren"] = RA.Antwort(False, grund, {"ok": False})
            self.assertEqual(self.stufen.bewaehren(TS.kontext(status="umgeschaltet")).art, "wartet", grund)

    def test_andere_fehler_des_ankers_beenden(self):
        self.anker.antworten["bewaehren"] = RA.Antwort(False, "vorwaerts_fehlgeschlagen", {"ok": False, "detail": "rueckweg_fehlgeschlagen"})
        erg = self.stufen.bewaehren(TS.kontext(status="umgeschaltet"))
        self.assertEqual((erg.art, erg.grund), ("fehler", "anker_rueckweg_fehlgeschlagen"))

    def test_nach_abbruch_wird_der_anker_nicht_gefragt(self):
        self.assertEqual(self.stufen.bewaehren(TS.kontext(status="umgeschaltet", abgebrochen=True)).grund, "abgebrochen")
        self.assertEqual(self.anker.aufrufe, [])


class Weg(TS.WegGrundlage):
    """Nach umgeschaltet fragt der Dienst die Stufe, bis der Anker bewaehrt oder zurueckgenommen meldet."""

    def anker_spielt(self, d, freigabe=True):
        a = self.anfrage(d)
        if a is not None and a["status"] == "umgeschaltet" and a["fertig"]:
            self.db.anker_setzt(a["id"], self.ausgang)   # der Anker setzt den Status, sobald er bewaehrt oder zurueckgenommen gemeldet hat
            return
        super().anker_spielt(d, freigabe)

    def setUp(self):
        super().setUp()
        self.ausgang = "bewaehrt"
        self.antworten = []

    def laeuft_dann(self, *staende):
        self.antworten = list(staende)

        def antwort(befehl, anfrage, trocken=False):
            if befehl != "bewaehren":
                return TS.StufenAnker.vorwaerts(self.stufen_anker, befehl, anfrage, trocken)
            self.stufen_anker.aufrufe.append((befehl, anfrage, trocken))
            stand = self.antworten.pop(0) if len(self.antworten) > 1 else self.antworten[0]
            return RA.Antwort(True, None, {"ok": True, "ergebnis": {"stand": stand}})
        self.stufen_anker.vorwaerts = antwort

    def zeitablauf(self):
        self.zeit[0] += 10

    def test_zwei_mal_laeuft_dann_bewaehrt(self):
        self.laeuft_dann("laeuft", "laeuft", "bewaehrt")
        self.anfordern(self.d)
        for _ in range(120):
            if self.anfrage(self.d) is None:
                break
            self.lauf(self.d, maximal=1)
            self.zeitablauf()
        self.assertEqual(self.endgrund(self.d), ("bewaehrt", None))
        fragen = [a for a in self.stufen_anker.aufrufe if a[0] == "bewaehren"]
        self.assertGreaterEqual(len(fragen), 3)

    def test_zurueckgenommen_beendet_mit_dem_rueckweg(self):
        self.laeuft_dann("laeuft", "zurueckgenommen")
        self.ausgang = "zurueckgenommen"
        self.anfordern(self.d)
        for _ in range(120):
            if self.anfrage(self.d) is None:
                break
            self.lauf(self.d, maximal=1)
            self.zeitablauf()
        self.assertEqual(self.endgrund(self.d)[0], "zurueckgenommen")

    def test_der_automat_kennt_die_stufe_fuer_umgeschaltet(self):
        self.assertIs(ST.aufruf(ST.Attrappe(), "umgeschaltet").__name__, "bewaehren")
        self.assertNotIn("umgeschaltet", AU.BEOBACHTEN)
        self.assertIn("umgeschaltet", AU.ANKER_FOLGE)


if __name__ == "__main__":
    unittest.main()
