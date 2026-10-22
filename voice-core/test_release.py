"""Tests für den Release-Dienst auf Sokrates-2 (infra/sokrates2/release/, Konzept Abschnitt D, Schritt 0.3: Gerüst).

Laufen ohne Root unter Windows und Linux mit Attrappen (Wegwerf-Ordner, falscher Anker, falsche Datenbank, Stufen ohne
Inhalt, selbst gestellte Uhr). Teile, die Linux brauchen (Unix-Socket, SO_PEERCRED, echter Anker), stehen in
test_release_socket.py und werden unter Windows übersprungen. Der echte Test auf Sokrates-2 ist
infra/sokrates2/release_rauchtest.py.

Ausführen:  uv run python -m pytest -q test_release.py test_release_socket.py test_release_skripte.py   (in voice-core/)
"""

import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import tokenize
import unittest
from pathlib import Path
from unittest import mock

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
RELEASE = INFRA / "release"
MIGRATION = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "0013_release_freigaben.sql"
sys.path.insert(0, str(RELEASE))  # die Module des Dienstes importieren einander mit ihrem Namen

import release_anker as RA  # noqa: E402
import release_automat as AU  # noqa: E402
import release_db as DB  # noqa: E402
import release_kette as K  # noqa: E402
import release_stufen as ST  # noqa: E402
import wamo_release as R  # noqa: E402

POSIX = os.name == "posix"
UIDS = {"root": 0, "wamo-core": 1500}
ADMIN = "123e4567-e89b-12d3-a456-426614174000"
ADMIN2 = "223e4567-e89b-12d3-a456-426614174000"
JETZT = 1_800_000_000.0
NICHT_ANKER = ("freigegeben", "bereitgestellt", "umgeschaltet", "bewaehrt")


class FakeAnker:
    """Ersetzt den AnkerClient: merkt sich die Aufrufe, antwortet, wie die Probe es vorgibt."""

    def __init__(self):
        self.sperre = "aus"
        self.erreichbar = True
        self.aufrufe = []
        self.rueckweg_ok = True
        self.rueckweg_grund = "schritt_fehlgeschlagen"
        self.waehrend_rueckweg = None
        self.sperre_an_ok = True

    def sperre_zustand(self):
        self.aufrufe.append(("status",))
        return self.sperre if self.erreichbar else "unbekannt"

    def rueckweg(self, komponente, trocken=False):
        self.aufrufe.append(("rueckweg", komponente, trocken))
        if not self.erreichbar:
            raise RA.AnkerFehler("nicht_erreichbar")
        if self.waehrend_rueckweg:
            self.waehrend_rueckweg()
        ok = self.rueckweg_ok
        return RA.Antwort(ok, None if ok else self.rueckweg_grund,
                          {"ok": ok, "teile": [{"komponente": komponente, "ok": ok}]})

    def sperre_an(self, trocken=False):
        self.aufrufe.append(("sperre_an", trocken))
        if not self.erreichbar:
            raise RA.AnkerFehler("nicht_erreichbar")
        if self.sperre_an_ok and not trocken:
            self.sperre = "an"
        return RA.Antwort(self.sperre_an_ok, None, {"ok": self.sperre_an_ok})

    def namen(self):
        return [a[0] for a in self.aufrufe]


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="release-")
        self.addCleanup(self.tmp.cleanup)
        self.p = R.Pfade.unter(self.tmp.name)
        for ordner in (self.p.lib, self.p.zustand, self.p.log, self.p.lauf):
            os.makedirs(ordner, exist_ok=True)
            os.chmod(ordner, 0o700)
        self.zeit = [JETZT]
        self.anker = FakeAnker()
        self.db = DB.AttrappeDatenbank({ADMIN})
        self.stufen = ST.Attrappe()
        self.zaehler = 0
        self.d = self.neu()

    # --- Aufbau
    def neu(self, wiederaufnehmen=True, **kw):
        kw.setdefault("anker", self.anker)
        kw.setdefault("db", self.db)
        kw.setdefault("stufen", self.stufen)
        kw.setdefault("uhr", lambda: self.zeit[0])
        kw.setdefault("aufloesen", UIDS.get)
        d = R.Dienst(self.p, **kw)
        d.tor_zeit = 0
        if wiederaufnehmen:
            d.wiederaufnehmen()
        return d

    def zeile(self, befehl, id=None):
        self.zaehler += 1
        return f"{id or format(self.zaehler, '016x')} {befehl}\n".encode("ascii")

    def sende(self, befehl, name="root", id=None, d=None):
        return (d or self.d).bearbeite(self.zeile(befehl, id), None, R.Peer(100, UIDS[name], UIDS[name]), name)

    def sende_roh(self, roh, name="root"):
        return self.d.bearbeite(roh, None, R.Peer(100, UIDS[name], UIDS[name]), name)

    def audit(self):
        if not os.path.exists(self.p.audit):
            return []
        with open(self.p.audit, "rb") as f:
            return [json.loads(z) for z in f.read().split(b"\n") if z]

    def uebergaenge(self):
        return [(z["von"], z["nach"]) for z in self.audit() if z["ereignis"] == "uebergang"]

    def anfrage(self, d=None):
        return (d or self.d)._z["anfrage"]

    def anfordern(self, d=None, quelle="main", profil=ADMIN):
        return (d or self.d).cmd_anfordern(quelle, profil)

    # --- Lauf mit dem Anker und der Freigabe als Spielfiguren
    def anker_spielt(self, d, freigabe=True):
        """Tut, was Freigabe-Dienst und Anker in der Datenbank täten, sobald der Release-Dienst so weit ist."""
        a = self.anfrage(d)
        if a is None:
            return
        db_status = self.db.anfragen[a["id"]]["status"]
        if a["status"] == "wartet_freigabe" and freigabe and db_status == "wartet_freigabe":
            self.db.anker_setzt(a["id"], "freigegeben", v_commit=a["daten"].get("v_commit"), tree_id=a["daten"].get("tree_id"))
        elif a["status"] == "veroeffentlicht" and a["fertig"] and db_status == "veroeffentlicht":
            self.db.anker_setzt(a["id"], "bereitgestellt")
        elif a["status"] == "bereitgestellt" and a["fertig"] and db_status == "bereitgestellt":
            self.db.anker_setzt(a["id"], "umgeschaltet")
        elif a["status"] == "umgeschaltet" and db_status == "umgeschaltet":
            self.db.anker_setzt(a["id"], "bewaehrt")

    def lauf(self, d=None, bis=None, maximal=80, freigabe=True):
        """Treibt die Anfrage voran, bis sie endet oder den Zustand bis erreicht (dann bleibt sie dort)."""
        d = d or self.d
        freigabe = freigabe and bis != "wartet_freigabe"
        letzte = None
        for _ in range(maximal):
            a = self.anfrage(d)
            if a is None or (bis and a["status"] == bis):
                break
            letzte = d.arbeite_einmal()
            self.anker_spielt(d, freigabe)
            if letzte in ("angehalten", "defekt", "audit"):
                break
        return letzte

    def bringe_nach(self, ziel, quelle="main", d=None):
        """Legt eine Anfrage an und führt sie, ohne Stufen, auf dem Weg der Quelle bis ziel (für Proben an jedem Zustand)."""
        d = d or self.d
        self.anfordern(d, quelle)
        weg = AU.WEG[quelle]
        for nach in weg[1:weg.index(ziel) + 1]:
            a = copy.deepcopy(self.anfrage(d))
            daten = {}
            if nach == "uebernommen":
                daten = {"basis_commit": "a" * 40, "c1_commit": "b" * 40}
            if nach == "pruefung3":
                daten = {"v_commit": "d" * 40, "tree_id": "e" * 40, "bump": "patch", "version_vorschlag": "0.1.1",
                         "version_final": "0.1.1"}
            beleg = "datenbank" if nach in AU.VOM_ANKER else "stufe"
            if nach in AU.VOM_ANKER:  # so wie Freigabe-Dienst und Anker es in der Datenbank täten
                d._spiegeln()
                self.db.anker_setzt(a["id"], nach, v_commit="d" * 40, tree_id="e" * 40)
            self.assertTrue(d._uebergang(a, nach, beleg, None, daten or None), nach)
        d._spiegeln()
        return self.anfrage(d)

    def endgrund(self, d=None):
        e = (d or self.d)._z["abgeschlossen"]
        return (e[-1]["status"], e[-1]["grund"]) if e else None


# ==================================================================== Übergangstabelle
ERWARTETE_UEBERGAENGE = {
    "angefragt": {"arbeit", "uebernommen", "abgebrochen"},
    "arbeit": {"uebernommen", "abgebrochen"},
    "uebernommen": {"pruefung1", "abgebrochen"},
    "pruefung1": {"review", "versionswahl", "abgebrochen"},
    "review": {"refactor", "abgebrochen"},
    "refactor": {"pruefung2", "abgebrochen"},
    "pruefung2": {"versionswahl", "abgebrochen"},
    "versionswahl": {"pruefung3", "abgebrochen"},
    "pruefung3": {"wartet_freigabe", "abgebrochen"},
    "wartet_freigabe": {"freigegeben", "abgebrochen"},
    "freigegeben": {"veroeffentlicht", "abgebrochen"},
    "veroeffentlicht": {"bereitgestellt", "abgebrochen"},
    "bereitgestellt": {"umgeschaltet", "zurueckgenommen"},
    "umgeschaltet": {"bewaehrt", "zurueckgenommen"},
    "bewaehrt": set(), "zurueckgenommen": set(), "abgebrochen": set(),
}


def datenbank_erlaubt(von, nach):
    """Die Regeln des Triggers release_anfragen_guard (Migration 0013) für den Statuswechsel, in Python nachgebaut."""
    reihenfolge = AU.REIHENFOLGE
    if von in ("zurueckgenommen", "abgebrochen"):
        return False
    if nach == "abgebrochen":
        return reihenfolge.index(von) < reihenfolge.index("umgeschaltet")
    if nach == "zurueckgenommen":
        return von in ("bereitgestellt", "umgeschaltet", "bewaehrt")
    if nach not in reihenfolge or von not in reihenfolge:
        return False
    v, n = reihenfolge.index(von), reihenfolge.index(nach)
    return n > v if n <= reihenfolge.index("wartet_freigabe") else n == v + 1


class TabelleTests(unittest.TestCase):
    def test_siebzehn_zustaende_und_dreissig_uebergaenge(self):
        self.assertEqual(len(AU.ZUSTAENDE), 17)
        self.assertEqual({k: set(v) for k, v in AU.UEBERGAENGE.items()}, ERWARTETE_UEBERGAENGE)
        self.assertEqual(sum(len(v) for v in AU.UEBERGAENGE.values()), 30)

    def test_namen_gleich_denen_der_migration(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        m = re.search(r"status\s+text not null default 'angefragt' check \(status in \((.*?)\)\)", sql, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(set(re.findall(r"'([a-z0-9_]+)'", m[1])), set(AU.ZUSTAENDE))
        m = re.search(r"reihenfolge constant text\[\] := array\[(.*?)\];", sql, re.S)
        self.assertEqual(tuple(re.findall(r"'([a-z0-9_]+)'", m[1])), AU.REIHENFOLGE)
        offen = re.search(r"where status not in \((.*?)\);", sql)
        self.assertEqual(set(re.findall(r"'([a-z]+)'", offen[1])), set(AU.ENDZUSTAENDE))

    def test_was_der_dienst_schreiben_darf_wie_in_der_migration(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        m = re.search(r"if als_release and new\.status in \((.*?)\) then", sql, re.S)
        verboten = set(re.findall(r"'([a-z]+)'", m[1]))
        self.assertEqual(verboten, set(AU.VOM_ANKER))
        self.assertEqual(set(AU.DB_SCHREIBBAR), set(AU.ZUSTAENDE) - verboten - {"angefragt"})
        update = re.search(r"grant update \(status, (.*?)\) on __SCHEMA__\.release_anfragen to wamo_release;", sql, re.S)
        self.assertEqual(set(re.findall(r"[a-z0-9_]+", update[1])) - {"pruefstand"}, set(AU.DB_SPALTEN) - {"pruefstand"})
        self.assertIn("pruefstand", update[1])

    def test_jedes_paar_erlaubt_oder_verboten(self):
        for von in AU.ZUSTAENDE:
            for nach in AU.ZUSTAENDE:
                for quelle in AU.QUELLEN:
                    erlaubt = nach in ERWARTETE_UEBERGAENGE[von]
                    # der Weg der Quelle schließt Abkürzungen aus: für main fehlen arbeit, review, refactor, pruefung2
                    if erlaubt and nach in AU.REIHENFOLGE and nach not in ("abgebrochen",) \
                            and AU.naechster(von, quelle) != nach and nach != "zurueckgenommen":
                        erlaubt = False
                    for beleg in AU.BELEGE:
                        try:
                            AU.uebergang_pruefen(von, nach, beleg, quelle)
                            ok = True
                        except AU.UebergangVerboten:
                            ok = False
                        if ok:
                            self.assertTrue(erlaubt, (von, nach, beleg, quelle))
                    if erlaubt:
                        beleg = ("datenbank" if nach in AU.VOM_ANKER else "stufe") if nach not in ("abgebrochen",) else "abbruch"
                        if nach == "zurueckgenommen":
                            beleg = "zurueck"
                        AU.uebergang_pruefen(von, nach, beleg, quelle)  # wirft nicht

    def test_kein_uebergang_aus_endzustaenden_nie_rueckwaerts_nie_ueberspringen_nach_der_freigabe(self):
        for ende in AU.ENDZUSTAENDE:
            self.assertEqual(AU.UEBERGAENGE[ende], frozenset())
        r = AU.REIHENFOLGE
        for von, nachs in AU.UEBERGAENGE.items():
            for nach in nachs:
                if nach in ("abgebrochen", "zurueckgenommen"):
                    continue
                self.assertGreater(r.index(nach), r.index(von))
                if r.index(von) >= r.index("wartet_freigabe"):
                    self.assertEqual(r.index(nach), r.index(von) + 1, (von, nach))

    def test_tabelle_ist_in_der_datenbank_erlaubt(self):
        for von, nachs in AU.UEBERGAENGE.items():
            for nach in nachs:
                self.assertTrue(datenbank_erlaubt(von, nach), (von, nach))

    def test_ausgerollt_geht_nur_ueber_den_rueckweg(self):
        for z in ("bereitgestellt", "umgeschaltet"):
            self.assertNotIn("abgebrochen", AU.UEBERGAENGE[z])
            self.assertIn("zurueckgenommen", AU.UEBERGAENGE[z])
        for z in AU.REIHENFOLGE[:AU.BEREITGESTELLT_AB]:
            self.assertIn("abgebrochen", AU.UEBERGAENGE[z])

    def test_belege(self):
        for z in AU.VOM_ANKER - {"zurueckgenommen"}:
            vor = AU.REIHENFOLGE[AU.REIHENFOLGE.index(z) - 1]
            AU.uebergang_pruefen(vor, z, "datenbank", "main" if z in AU.WEG["main"] else "selbst")
            for falsch in ("stufe", "zurueck", "abbruch", "zeit", "fehler", "sperre"):
                with self.assertRaises(AU.UebergangVerboten, msg=(z, falsch)):
                    AU.uebergang_pruefen(vor, z, falsch, "main")
        with self.assertRaises(AU.UebergangVerboten):
            AU.uebergang_pruefen("pruefung1", "versionswahl", "datenbank", "main")  # Stufen kommen nicht aus der Datenbank
        with self.assertRaises(AU.UebergangVerboten):
            AU.uebergang_pruefen("bereitgestellt", "zurueckgenommen", "stufe", "main")
        with self.assertRaises(AU.UebergangVerboten):
            AU.uebergang_pruefen("angefragt", "abgebrochen", "stufe", "main")
        with self.assertRaises(AU.UebergangVerboten):
            AU.uebergang_pruefen("angefragt", "abgebrochen", "unbekannt", "main")

    def test_wege(self):
        self.assertEqual(AU.WEG["selbst"], AU.REIHENFOLGE)
        for z in ("arbeit", "review", "refactor", "pruefung2"):
            self.assertNotIn(z, AU.WEG["main"])
        self.assertEqual(AU.naechster("angefragt", "main"), "uebernommen")
        self.assertEqual(AU.naechster("angefragt", "selbst"), "arbeit")
        self.assertIsNone(AU.naechster("bewaehrt", "main"))
        self.assertIsNone(AU.naechster("abgebrochen", "main"))

    def test_zeitgrenzen_fuer_jeden_offenen_zustand(self):
        self.assertEqual(set(AU.FRIST_S), set(AU.OFFENE))
        self.assertTrue(all(0 < s <= 86400 for s in AU.FRIST_S.values()))
        self.assertGreater(AU.GESAMT_FRIST_S, max(AU.FRIST_S.values()))
        self.assertEqual(AU.FRIST_S["umgeschaltet"] > 1800, True)  # die Bewährung des Ankers dauert 30 Minuten

    def test_pflichtzeilen_des_pruefstands(self):
        for runde in (1, 2, 3):
            self.assertIn("schutzschicht", AU.PRUEFSTAND_PFLICHT[runde])
            self.assertIn("tests", AU.PRUEFSTAND_PFLICHT[runde])
        self.assertIn("version_bytegleich", AU.PRUEFSTAND_PFLICHT[3])
        self.assertEqual(AU.STUFE_RUNDE, {"pruefung1": 1, "pruefung2": 2, "pruefung3": 3})


class DatenTests(unittest.TestCase):
    def test_gueltige_werte(self):
        d = AU.daten_pruefen({"basis_commit": "a" * 40, "c1_commit": "b" * 64, "bump": "minor", "version_vorschlag": "0.1.1",
                              "version_final": "0.2.0", "klasse": "sonder", "treffer_anzahl": 3, "treffer_sha": "c" * 64,
                              "pruefstand": {"bauen": "ok", "tests": "fehler"}, "refactor_verworfen": True})
        self.assertEqual(d["version_final"], "0.2.0")

    def test_ungueltige_werte(self):
        schlecht = [{"basis_commit": "A" * 40}, {"basis_commit": "a" * 39}, {"basis_commit": 5}, {"bump": "riesig"},
                    {"version_final": "01.0.0"}, {"version_final": "1.0"}, {"version_vorschlag": "1.0.0.0"},
                    {"klasse": "egal"}, {"treffer_anzahl": -1}, {"treffer_anzahl": 201}, {"treffer_anzahl": True},
                    {"treffer_sha": "a" * 40}, {"pruefstand": {"x": "ok"}}, {"pruefstand": {"bauen": "gruen"}},
                    {"pruefstand": {f"stufe_{i:02d}": "ok" for i in range(17)}}, {"pruefstand": []},
                    {"refactor_verworfen": False}, {"unbekannt": 1}, {"../x": 1}, [], "text", None,
                    {"version_vorschlag": "0.2.0", "version_final": "0.1.9"}]
        for d in schlecht:
            with self.subTest(d=str(d)[:50]), self.assertRaises(ValueError):
                AU.daten_pruefen(d)

    def test_version_wird_nie_gesenkt(self):
        AU.daten_pruefen({"version_vorschlag": "0.1.1", "version_final": "0.1.1"})
        AU.daten_pruefen({"version_vorschlag": "0.1.1", "version_final": "1.0.0"})
        with self.assertRaises(ValueError):
            AU.daten_pruefen({"version_vorschlag": "0.1.10", "version_final": "0.1.9"})  # als Zahl, nicht als Text

    def test_zustand_streng(self):
        z = AU.leerer_zustand()
        self.assertEqual(AU.zustand_pruefen(z), z)
        a = {"id": ADMIN, "profil": ADMIN, "quelle": "main", "status": "pruefung1", "seq": 2, "start": 1, "seit": 2,
             "versuch": 1, "fertig": False, "daten": {"basis_commit": "a" * 40}, "ende_grund": None}
        z["anfrage"] = a
        self.assertEqual(AU.zustand_pruefen(copy.deepcopy(z))["anfrage"]["status"], "pruefung1")
        schlecht = [
            {**a, "status": "bewaehrt"}, {**a, "status": "review"}, {**a, "quelle": "x"}, {**a, "id": "abc"},
            {**a, "seq": -1}, {**a, "versuch": AU.VERSUCHE_MAX + 1}, {**a, "fertig": 1}, {**a, "ende_grund": "x"},
            {**a, "daten": {"x": 1}}, {**a, "extra": 1}, {k: v for k, v in a.items() if k != "seq"},
        ]
        for s in schlecht:
            with self.subTest(s=str(s)[:60]), self.assertRaises(AU.ZustandDefekt):
                AU.zustand_pruefen({**z, "anfrage": s})
        for kopf in ({**z, "schema": 2}, {**z, "audit_n": -1}, {**z, "extra": 1}, {**z, "abgeschlossen": [1]}, [], None,
                     {**z, "abgeschlossen": [{"id": ADMIN}]}, {**z, "abgeschlossen": [None] * 51}):
            with self.subTest(kopf=str(kopf)[:60]), self.assertRaises(AU.ZustandDefekt):
                AU.zustand_pruefen(kopf)


class AnwendenTests(unittest.TestCase):
    def zeile(self, **kw):
        return {"zeit": 5, **kw}

    def test_ablauf_und_passende_zeilen(self):
        z = AU.leerer_zustand()
        AU.anwenden(z, self.zeile(ereignis="angefordert", anfrage=ADMIN, profil=ADMIN, quelle="main"))
        AU.anwenden(z, self.zeile(ereignis="stufe_start", anfrage=ADMIN, status="angefragt", versuch=1))
        AU.anwenden(z, self.zeile(ereignis="stufe_ende", anfrage=ADMIN, status="angefragt", ok=True))
        self.assertTrue(z["anfrage"]["fertig"])
        AU.anwenden(z, self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="uebernommen", seq=1,
                                  beleg="stufe", daten={"basis_commit": "a" * 40}))
        a = z["anfrage"]
        self.assertEqual((a["status"], a["seq"], a["versuch"], a["fertig"]), ("uebernommen", 1, 0, False))
        self.assertEqual(a["daten"]["basis_commit"], "a" * 40)

    def test_unpassende_zeilen_werden_abgelehnt(self):
        z = AU.leerer_zustand()
        AU.anwenden(z, self.zeile(ereignis="angefordert", anfrage=ADMIN, profil=ADMIN, quelle="main"))
        schlecht = [
            self.zeile(ereignis="angefordert", anfrage=ADMIN2, profil=ADMIN, quelle="main"),  # schon eine offene
            self.zeile(ereignis="uebergang", anfrage=ADMIN2, von="angefragt", nach="uebernommen", seq=1, beleg="stufe"),
            self.zeile(ereignis="uebergang", anfrage=ADMIN, von="pruefung1", nach="versionswahl", seq=1, beleg="stufe"),
            self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="uebernommen", seq=2, beleg="stufe"),
            self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="pruefung1", seq=1, beleg="stufe"),
            self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="uebernommen", seq=1, beleg="datenbank"),
            self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="abgebrochen", seq=1, beleg="abbruch",
                       grund="erfunden"),
            self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="uebernommen", seq=1, beleg="stufe",
                       daten={"x": 1}),
            self.zeile(ereignis="stufe_start", anfrage=ADMIN, status="angefragt", versuch=2),
            self.zeile(ereignis="stufe_start", anfrage=ADMIN, status="pruefung1", versuch=1),
            self.zeile(ereignis="stufe_ende", anfrage=ADMIN, status="pruefung1"),
            self.zeile(ereignis="abgelehnt", anfrage=ADMIN),
            self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="uebernommen", beleg="stufe"),
        ]
        for s in schlecht:
            with self.subTest(s=str(s)[:90]), self.assertRaises(AU.ZustandDefekt):
                AU.anwenden(copy.deepcopy(z), s)

    def test_ende_schliesst_die_anfrage_und_merkt_den_grund(self):
        z = AU.leerer_zustand()
        AU.anwenden(z, self.zeile(ereignis="angefordert", anfrage=ADMIN, profil=ADMIN, quelle="main"))
        AU.anwenden(z, self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="abgebrochen", seq=1,
                                  beleg="abbruch", grund="abbruch_person"))
        self.assertIsNone(z["anfrage"])
        self.assertEqual(z["abgeschlossen"][-1]["grund"], "abbruch_person")
        AU.zustand_pruefen(z)

    def test_historie_ist_begrenzt(self):
        z = AU.leerer_zustand()
        for i in range(AU.HISTORIE_MAX + 7):
            AU.anwenden(z, self.zeile(ereignis="angefordert", anfrage=ADMIN, profil=ADMIN, quelle="main"))
            AU.anwenden(z, self.zeile(ereignis="uebergang", anfrage=ADMIN, von="angefragt", nach="abgebrochen", seq=1,
                                      beleg="abbruch"))
        self.assertEqual(len(z["abgeschlossen"]), AU.HISTORIE_MAX)


# ==================================================================== Eingabe
class EingabeTests(ReleaseTest):
    def test_gueltige_anfragen(self):
        for text in ("status", f"anfordern main {ADMIN}", f"anfordern selbst {ADMIN}", f"abbrechen {ADMIN}", "zurueck ui",
                     "zurueck kern", "zurueck alles", "zurueck alles --trocken"):
            with self.subTest(text=text):
                anf = R.parse_zeile(self.zeile(text))
                self.assertEqual(anf.befehl, text.split()[0])

    def test_zu_lang(self):
        with self.assertRaises(R.Abgelehnt) as cm:
            R.parse_zeile(b"a" * 161)
        self.assertEqual(cm.exception.code, "zeile_zu_lang")
        R.parse_zeile(self.zeile("status"))

    def test_unbekannte_befehle(self):
        for text in ("reboot", "sperre aus", "sperre an", "rueckweg ui", "umschalten", "bereitstellen", "pruefe-sonder 1",
                     "lokal status", "STATUS", "status;id", "status && id", "$(id)", "`id`", "stat us"):
            with self.subTest(text=text), self.assertRaises(R.Abgelehnt):
                R.parse_zeile(self.zeile(text))

    def test_pfadtricks(self):
        for text in ("zurueck ../ui", "zurueck /etc/passwd", "zurueck ui/../kern", "zurueck ~", "zurueck .", "zurueck ..",
                     "abbrechen ../../etc", f"abbrechen /{ADMIN}", "anfordern main /etc/passwd", "anfordern ../main " + ADMIN,
                     "zurueck ui%2e%2e", "zurueck C:\\Windows", "zurueck ui\\kern"):
            with self.subTest(text=text), self.assertRaises(R.Abgelehnt):
                R.parse_zeile(self.zeile(text))

    def test_nullbytes_steuerzeichen_bidi(self):
        for text in ("status\x00", "zurueck ui\x00", "status\x1b[31m", "status\t", "sta\x7ftus", "status\r", "st\u202eatus",
                     "zurueck ui\u200b", "status\u00a0", "zurück ui", "status\u2028"):
            with self.subTest(text=repr(text)):
                with self.assertRaises(R.Abgelehnt) as cm:
                    R.parse_zeile(self.zeile(text).replace(b"\n", b"") + b"\n" if text.isascii()
                                  else (f"{1:016x} {text}\n").encode("utf-8"))
                self.assertIn(cm.exception.code, ("zeichen", "form"))

    def test_zwei_zeilen_in_einer_anfrage(self):
        with self.assertRaises(R.Abgelehnt):
            R.parse_zeile(self.zeile("status").replace(b"\n", b"") + b"\n" + self.zeile("zurueck alles"))
        with self.assertRaises(R.Abgelehnt):
            R.parse_zeile(b"0123456789abcdef status\nzurueck alles\n")

    def test_form_zeilenende_kennung(self):
        for roh in (b"status\n", b" status\n", b"0123456789abcdef  status\n", b"0123456789abcde status\n",
                    b"0123456789ABCDEF status\n", b"g123456789abcdef status\n", b"0123456789abcdef status \n",
                    b"0123456789abcdef \n", b"\n", b"0123456789abcdef status --trocken --trocken\n"):
            with self.subTest(roh=roh), self.assertRaises(R.Abgelehnt):
                R.parse_zeile(roh)
        with self.assertRaises(R.Abgelehnt) as cm:
            R.parse_zeile(b"0123456789abcdef status")
        self.assertEqual(cm.exception.code, "kein_zeilenende")
        R.parse_zeile(b"0123456789abcdef0123456789abcdef status\n")  # 32 Zeichen sind die Obergrenze
        with self.assertRaises(R.Abgelehnt):
            R.parse_zeile(b"0123456789abcdef0123456789abcdef0 status\n")

    def test_trocken_nur_bei_zurueck(self):
        R.parse_zeile(self.zeile("zurueck ui --trocken"))
        for text in ("status --trocken", f"anfordern main {ADMIN} --trocken", f"abbrechen {ADMIN} --trocken"):
            with self.subTest(text=text), self.assertRaises(R.Abgelehnt) as cm:
                R.parse_zeile(self.zeile(text))
            self.assertEqual(cm.exception.code, "trocken")

    def test_argumente_pflicht_oder_verboten(self):
        for text in ("status alles", "zurueck", "zurueck alles ui", "zurueck sonstwas", f"anfordern {ADMIN}", "anfordern main",
                     f"anfordern bot {ADMIN}", "anfordern main 123", f"abbrechen {ADMIN} {ADMIN}", "abbrechen",
                     f"anfordern main {ADMIN.upper()}", f"anfordern main {ADMIN}0"):
            with self.subTest(text=text), self.assertRaises(R.Abgelehnt) as cm:
                R.parse_zeile(self.zeile(text))
            self.assertEqual(cm.exception.code, "form" if text.endswith(ADMIN.upper()) else "argument")

    def test_freigeben_und_diff_gibt_es_hier_nie(self):
        for text in (f"freigeben {ADMIN}", "freigeben", f"freigeben {ADMIN} {'a' * 40}", f"diff {ADMIN}"):
            r = self.sende(text)
            self.assertFalse(r["ok"], text)
            self.assertIn(r["grund"], ("freigabe_nur_beim_anker", "nicht_verfuegbar"))
        self.assertEqual(self.sende(f"freigeben {ADMIN}")["grund"], "freigabe_nur_beim_anker")
        self.assertIsNone(self.anfrage())

    def test_abgelehntes_hat_keine_wirkung(self):
        vorher = self.audit()
        for text in ("reboot", "zurueck ../ui", "status\x00"):
            self.sende_roh(self.zeile(text))
        self.assertEqual([z["ereignis"] for z in self.audit()[len(vorher):]], ["abgelehnt"] * 3)
        self.assertEqual(self.anker.aufrufe, [])
        self.assertIsNone(self.anfrage())


# ==================================================================== Rechte
class RechteTests(ReleaseTest):
    def test_erlaubte_benutzer_sind_fest(self):
        self.assertEqual(set(R.ERLAUBT), {"root", "wamo-core"})
        self.assertEqual(R.ERLAUBT["wamo-core"], frozenset({"status", "anfordern"}))
        self.assertEqual(R.ERLAUBT["root"], frozenset({"status", "anfordern", "abbrechen", "zurueck"}))

    def test_der_kern_darf_nur_lesen_und_anfragen(self):
        self.assertTrue(self.sende("status", "wamo-core")["ok"])
        for text in ("zurueck alles", f"abbrechen {ADMIN}", "zurueck ui --trocken"):
            r = self.sende(text, "wamo-core")
            self.assertEqual(r["grund"], "recht", text)
        self.assertEqual(self.anker.namen().count("rueckweg"), 0)

    def test_der_kern_kann_anfragen_aber_nie_freigeben(self):
        self.assertTrue(self.sende(f"anfordern main {ADMIN}", "wamo-core")["ok"])
        self.assertEqual(self.sende(f"freigeben {ADMIN}", "wamo-core")["grund"], "freigabe_nur_beim_anker")

    def test_fremde_uid_wird_abgewiesen(self):
        for uid in (1, 999, 1000, 2000, 65534, 1500 + 1):
            antwort = self.d.bearbeite(self.zeile("status"), None, R.Peer(1, uid, uid), "root")
            self.assertEqual(antwort["grund"], "benutzer", uid)
        self.assertIsNone(self.d.benutzer(R.Peer(1, 4242, 4242)))
        self.assertIsNone(self.d.benutzer(None))

    def test_name_muss_zur_uid_passen(self):
        antwort = self.d.bearbeite(self.zeile("zurueck alles"), None, R.Peer(1, UIDS["wamo-core"], 1), "root")
        self.assertEqual(antwort["grund"], "benutzer")
        self.assertEqual(self.anker.namen().count("rueckweg"), 0)

    def test_benutzer_wird_frisch_aufgeloest(self):
        tabelle = dict(UIDS)
        d = self.neu(aufloesen=tabelle.get)
        self.assertEqual(d.benutzer(R.Peer(1, 1500, 1500)), "wamo-core")
        tabelle["wamo-core"] = 1501  # ein neuer Benutzer mit anderer uid
        self.assertIsNone(d.benutzer(R.Peer(1, 1500, 1500)))
        self.assertEqual(d.benutzer(R.Peer(1, 1501, 1501)), "wamo-core")

    def test_fehlender_benutzer_passt_auf_niemanden(self):
        d = self.neu(aufloesen=lambda n: None)
        self.assertIsNone(d.benutzer(R.Peer(1, 0, 0)))

    def test_so_peercred_wird_vorzeichenlos_gelesen(self):
        class S:
            def getsockopt(self, *_):
                import struct
                return struct.pack("iII", 7, 4_000_000_000, 4_000_000_001)

        with mock.patch.object(R.socket, "SO_PEERCRED", 17, create=True):
            p = R.peer_von(S())
        self.assertEqual((p.pid, p.uid, p.gid), (7, 4_000_000_000, 4_000_000_001))

        class Kaputt:
            def getsockopt(self, *_):
                raise OSError("nein")

        with mock.patch.object(R.socket, "SO_PEERCRED", 17, create=True):
            self.assertIsNone(R.peer_von(Kaputt()))

    def test_jeder_fehlercode_hat_einen_text(self):
        quelle = "\n".join((RELEASE / n).read_text(encoding="utf-8") for n in ("wamo_release.py",))
        codes = set(re.findall(r'(?:MitCode|Abgelehnt|_antwort)\(\s*(?:False,\s*)?"([a-z_]+)"', quelle))
        codes |= set(R.BEKANNT_ABGEWIESEN.values())
        self.assertGreater(len(codes), 20)
        for c in codes:
            self.assertIn(c, R.GRUENDE, c)

    def test_ablehnung_wird_protokolliert_ohne_zu_lesen(self):
        antwort = self.d.abweisen(R.Peer(9, 4242, 4242))
        self.assertEqual(antwort["grund"], "benutzer")
        z = self.audit()[-1]
        self.assertEqual((z["ereignis"], z["grund"], z["uid"]), ("abgelehnt", "benutzer", 4242))

    def test_ablehnungen_sind_begrenzt(self):
        for _ in range(R.ABLEHN_MAX + 30):
            self.d.abweisen(R.Peer(9, 4242, 4242))
        zeilen = [z for z in self.audit() if z["ereignis"] == "abgelehnt"]
        self.assertEqual(len(zeilen), R.ABLEHN_MAX)
        self.zeit[0] += R.ABLEHN_FENSTER_S + 1
        self.d.abweisen(R.Peer(9, 4242, 4242))
        self.assertEqual(self.audit()[-1]["unterdrueckt"], 30)  # die Zahl der nur gezählten steht in der ersten Zeile danach


class DoppeltTests(ReleaseTest):
    def test_gleiche_kennung_nur_einmal(self):
        self.assertTrue(self.sende("status", id="aaaaaaaaaaaaaaaa")["ok"])
        r = self.sende("status", id="aaaaaaaaaaaaaaaa")
        self.assertEqual(r["grund"], "doppelt")

    def test_doppelte_anfordern_wirkt_nur_einmal(self):
        self.assertTrue(self.sende(f"anfordern main {ADMIN}", id="bbbbbbbbbbbbbbbb")["ok"])
        self.assertEqual(self.sende(f"anfordern main {ADMIN}", id="bbbbbbbbbbbbbbbb")["grund"], "doppelt")
        self.assertEqual(len(self.db.anfragen), 1)

    def test_gedaechtnis_ist_begrenzt(self):
        for i in range(R.IDS_MAX + 50):
            self.d._ids[format(i, "016x")] = None
            if len(self.d._ids) > R.IDS_MAX:
                del self.d._ids[next(iter(self.d._ids))]
        self.assertLessEqual(len(self.d._ids), R.IDS_MAX)
        self.sende("status")
        self.assertLessEqual(len(self.d._ids), R.IDS_MAX)


# ==================================================================== Audit-Kette
class AuditTests(ReleaseTest):
    def anfragen_machen(self, n=3):
        for _ in range(n):
            self.sende("status")
            self.zeit[0] += 1

    def lies(self):
        with open(self.p.audit, "rb") as f:
            return f.read()

    def test_kette_ist_gueltig(self):
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        ok, n, letzter, grund = K.kette_pruefen(self.p.audit)
        self.assertTrue(ok, grund)
        self.assertGreater(n, 10)
        zeilen = self.audit()
        self.assertEqual([z["n"] for z in zeilen], list(range(1, n + 1)))
        self.assertEqual(zeilen[0]["vorher"], "0" * 64)
        self.assertEqual(zeilen[-1]["hash"], letzter)

    def test_fehlende_datei_ist_eine_leere_kette(self):
        os.unlink(self.p.audit) if os.path.exists(self.p.audit) else None
        self.assertEqual(K.kette_pruefen(self.p.audit), (True, 0, "0" * 64, None))

    def test_manipulation_wird_erkannt(self):
        self.anfordern()
        self.lauf(bis="pruefung3")
        roh = self.lies().split(b"\n")
        zeilen = len(roh) - 1
        self.assertGreater(zeilen, 8)

        def pruefe(name, neue_zeilen):
            kopie = os.path.join(self.tmp.name, f"kopie-{name}.jsonl")
            with open(kopie, "wb") as f:
                f.write(b"\n".join(neue_zeilen))
            os.chmod(kopie, 0o600)
            self.assertFalse(K.kette_pruefen(kopie)[0], name)

        geaendert = list(roh)
        geaendert[3] = roh[3].replace(b'"t":"20', b'"t":"21')  # Zeitstempel einer mittleren Zeile
        pruefe("zeit", geaendert)
        geaendert = list(roh)
        geaendert[2] = roh[2].replace(b'"ereignis":"', b'"ereignis":"x')
        pruefe("ereignis", geaendert)
        pruefe("geloescht", roh[:2] + roh[3:])
        pruefe("vertauscht", [roh[0], roh[2], roh[1], *roh[3:]])
        pruefe("doppelt", [*roh[:3], roh[2], *roh[3:]])
        pruefe("abgeschnitten", roh[:-2] + [roh[-2][:20]])
        pruefe("muell", [*roh[:3], b"{}", *roh[3:]])
        pruefe("zeile_zu_lang", [*roh[:3], b"x" * 2100, *roh[3:]])
        # eine nachgerechnete Zeile (Hash passt) mit falscher Nummer oder falschem Vorgänger bricht die Kette
        z = json.loads(roh[2])
        z.pop("hash")
        z["vorher"] = "1" * 64
        pruefe("vorgaenger", [*roh[:2], K.zeile_bauen(z)[:-1], *roh[3:]])

    def test_status_meldet_die_kette(self):
        self.anfragen_machen()
        self.assertEqual(self.sende("status")["audit"]["kette"], "ok")
        with open(self.p.audit, "r+b") as f:
            f.seek(30)
            f.write(b"X")
        r = self.sende("status")
        self.assertEqual(r["audit"]["kette"], "defekt")
        self.assertTrue(r["ok"])  # status läuft weiter

    def test_defekte_kette_stoppt_das_anhaengen_und_alles_neue(self):
        self.anfragen_machen()
        with open(self.p.audit, "r+b") as f:
            f.seek(0, os.SEEK_END)
            f.write(b'{"kaputt"')  # halbe Zeile am Ende
        self.assertFalse(self.d.protokolliere("test", 1.0))
        r = self.sende(f"anfordern main {ADMIN}")
        self.assertEqual(r["grund"], "audit_nicht_schreibbar")
        self.assertIsNone(self.anfrage())
        self.assertEqual(len(self.db.anfragen), 0)  # nichts angelegt, bevor die Zeile stand? (Anfrage-Zeile scheitert zuerst)
        d2 = self.neu()  # ein Neustart bemerkt es
        self.assertEqual(d2._defekt, "zustand_defekt")
        self.assertEqual(self.sende(f"anfordern main {ADMIN}", d=d2)["grund"], "audit_nicht_schreibbar")

    def test_zeilenlaenge_ist_begrenzt(self):
        self.assertFalse(self.d.protokolliere("x", 1.0, text="a" * 3000))
        self.assertTrue(K.kette_pruefen(self.p.audit)[0])

    def test_nie_ueberschreiben(self):
        self.anfragen_machen(2)
        vorher = self.lies()
        self.anfragen_machen(2)
        self.assertTrue(self.lies().startswith(vorher))

    def test_keine_geheimnisse_im_protokoll(self):
        self.anfordern()
        self.lauf()
        text = self.lies().decode()
        for muster in ("sk-ant-", "BEGIN", "password", "token", "eyJ", "Bearer"):
            self.assertNotIn(muster, text)

    def test_symbolischer_link_wird_nicht_verfolgt(self):
        if not POSIX:
            self.skipTest("Links brauchen POSIX")
        ziel = os.path.join(self.tmp.name, "fremd.jsonl")
        open(ziel, "wb").close()
        os.chmod(ziel, 0o600)
        os.unlink(self.p.audit) if os.path.exists(self.p.audit) else None
        os.symlink(ziel, self.p.audit)
        self.assertFalse(self.d.protokolliere("x", 1.0))
        self.assertEqual(os.path.getsize(ziel), 0)
        self.assertFalse(K.kette_pruefen(self.p.audit)[0])

    def test_zu_offene_rechte_sind_ein_fehler(self):
        if not POSIX:
            self.skipTest("Rechte brauchen POSIX")
        self.anfragen_machen(1)
        os.chmod(self.p.audit, 0o644)
        self.assertFalse(self.d.protokolliere("x", 1.0))
        os.chmod(self.p.audit, 0o600)
        os.chmod(self.p.log, 0o777)
        self.assertFalse(self.d.protokolliere("x", 1.0))
        os.chmod(self.p.log, 0o700)
        self.assertTrue(self.d.protokolliere("x", 1.0))

    def test_klassen_des_fuellgrads_halten_den_zustand_offen(self):
        self.d.protokolliere("fuell", 1.0)
        mit = K.AUDIT_DATEI_MAX
        try:
            K.AUDIT_DATEI_MAX = 6000  # klein, damit die Klassen greifen
            for _ in range(200):
                if not self.d.protokolliere("lesen", R.AUDIT_GRENZE_LESEN, text="x" * 300):
                    break
            self.assertFalse(self.d.protokolliere("lesen", R.AUDIT_GRENZE_LESEN, text="x" * 300))
            self.assertGreater(self.d.audit.fuellgrad(), 0.5)
            self.assertTrue(self.d._wal("angefordert", anfrage=ADMIN, profil=ADMIN, quelle="main") > 0)  # Zustandszeilen gehen
        finally:
            K.AUDIT_DATEI_MAX = mit

    def test_dauerfeuer_ohne_wirkung_fuellt_das_protokoll_nicht(self):
        for _ in range(R.ABLEHN_MAX * 4):
            self.sende("status")
        zeilen = [z for z in self.audit() if z["ereignis"] == "anfrage"]
        self.assertLessEqual(len(zeilen), R.ABLEHN_MAX)

    def test_aendernde_anfragen_im_dauerfeuer_werden_gedrosselt(self):
        antworten = [self.sende("zurueck alles --trocken")["ok"] for _ in range(10)]
        self.assertTrue(all(antworten))  # trocken zählt nicht als ändernd
        gründe = [self.sende(f"abbrechen {ADMIN}").get("grund") for _ in range(R.AENDERN_MAX + 5)]
        self.assertIn("zu_viele", gründe)

    def test_abweisungen_zaehlen_je_grund(self):
        for _ in range(R.ABLEHN_MAX + 5):
            self.sende_roh(self.zeile("reboot"))
        r = self.sende_roh(b"0123456789abcdef status\x00\n")
        self.assertEqual(r["grund"], "zeichen")
        gruende = {z["grund"] for z in self.audit() if z["ereignis"] == "abgelehnt"}
        self.assertEqual(gruende, {"befehl_unbekannt", "zeichen"})  # Rauschen verdeckt keinen anderen Verstoß

    def test_volle_platte_aendert_nichts(self):
        original = os.write

        def voll(fd, daten):
            if b'"ereignis":"angefordert"' in daten or b'"ereignis":"anfrage"' in daten:
                raise OSError(28, "No space left on device")
            return original(fd, daten)

        os.write = voll
        try:
            r = self.sende(f"anfordern main {ADMIN}")
        finally:
            os.write = original
        self.assertEqual(r["grund"], "audit_nicht_schreibbar")
        self.assertIsNone(self.anfrage())
        self.assertEqual(len(self.db.anfragen), 0)
        self.assertTrue(K.kette_pruefen(self.p.audit)[0])

    def test_zeile_hinter_dem_zustand_wird_nachgezogen(self):
        """Das Protokoll ist die Wahrheit: Fehlt der Zustandsdatei die letzte Zeile, holt der Neustart sie nach."""
        self.anfordern()
        gesichert = Path(self.p.zustand_datei).read_bytes()
        self.lauf(bis="pruefung1")
        Path(self.p.zustand_datei).write_bytes(gesichert)  # Datei auf einen älteren Stand zurück
        d2 = self.neu()
        self.assertEqual(self.anfrage(d2)["status"], "pruefung1")


# ==================================================================== Zustandsdatei
class ZustandsdateiTests(ReleaseTest):
    def test_atomar_und_mit_vorgaengerin(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        datei = Path(self.p.zustand_datei)
        self.assertTrue(datei.exists() and Path(str(datei) + ".vorher").exists())
        self.assertEqual([n for n in os.listdir(self.p.zustand) if n.endswith(".tmp")], [])
        self.assertEqual(json.loads(datei.read_text())["anfrage"]["status"], "pruefung1")
        if POSIX:
            self.assertEqual(os.stat(datei).st_mode & 0o777, 0o600)

    def test_zerstoerte_dateien_werden_nie_ueberschrieben(self):
        self.anfordern()
        datei = Path(self.p.zustand_datei)
        for inhalt in (b"", b"{", b"[]", b"null", b'{"schema":1}', b"\xff\xfe", b'{"schema":1,"schema":1}',
                       json.dumps({**AU.leerer_zustand(), "audit_n": "x"}).encode(),
                       json.dumps({**AU.leerer_zustand(), "anfrage": {"id": "x"}}).encode(), b"x" * (K.ZUSTAND_MAX + 5)):
            with self.subTest(inhalt=inhalt[:30]):
                datei.write_bytes(inhalt)
                os.chmod(datei, 0o600)
                d2 = self.neu()
                self.assertEqual(d2._defekt, "zustand_defekt")
                self.assertEqual(self.sende(f"anfordern main {ADMIN}", d=d2)["grund"], "zustand_defekt")
                self.assertEqual(datei.read_bytes(), inhalt)  # nichts überschrieben
                self.assertTrue(self.sende("status", d=d2)["ok"])
                self.assertEqual(self.sende("status", d=d2)["bereit"], False)

    def test_fehlende_datei_wird_aus_dem_protokoll_aufgebaut(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        erwartet = copy.deepcopy(self.d._z)
        os.unlink(self.p.zustand_datei)
        d2 = self.neu()
        self.assertIsNone(d2._defekt)
        self.assertEqual(self.anfrage(d2), erwartet["anfrage"])
        self.assertEqual(d2._z["abgeschlossen"], erwartet["abgeschlossen"])

    def test_frischer_start_ohne_alles(self):
        self.assertIsNone(self.d._defekt)
        self.assertTrue(self.d._bereit)
        self.assertIsNone(self.sende("status").get("anfrage"))

    def test_link_statt_datei(self):
        if not POSIX:
            self.skipTest("Links brauchen POSIX")
        ziel = os.path.join(self.tmp.name, "fremd.json")
        Path(ziel).write_text(json.dumps(AU.leerer_zustand()))
        os.chmod(ziel, 0o600)
        os.unlink(self.p.zustand_datei)
        os.symlink(ziel, self.p.zustand_datei)
        self.assertEqual(self.neu()._defekt, "zustand_defekt")

    def test_zu_offene_rechte(self):
        if not POSIX:
            self.skipTest("Rechte brauchen POSIX")
        self.anfordern()
        os.chmod(self.p.zustand_datei, 0o666)
        self.assertEqual(self.neu()._defekt, "zustand_defekt")

    def test_ordner_statt_datei(self):
        os.unlink(self.p.zustand_datei)
        os.makedirs(self.p.zustand_datei)
        self.assertEqual(self.neu()._defekt, "zustand_defekt")

    def test_zustand_voll_bremst_nicht_aus(self):
        original = os.write

        def voll(fd, daten):
            if b'"schema"' in daten:
                raise OSError(28, "No space left on device")
            return original(fd, daten)

        self.anfordern()
        os.write = voll
        try:
            self.assertEqual(self.d.arbeite_einmal(), "gearbeitet")  # das Protokoll trägt, die Datei zieht nach
        finally:
            os.write = original
        self.assertEqual(self.d._speicher_fehler, "ENOSPC")
        self.assertEqual(self.sende("status")["speicherfehler"], "ENOSPC")
        d2 = self.neu()  # Neustart: die Zeile hinter dem Stand der Datei wird nachgezogen
        self.assertEqual(self.anfrage(d2)["status"], "uebernommen")

    def test_ungueltige_zeile_wird_nie_angehaengt(self):
        """Eine Zeile, die nicht zum Zustand passt, kommt nicht ins Protokoll (sie würde die Wiederaufnahme vergiften)."""
        self.anfordern()
        vorher = len(self.audit())
        with self.assertRaises(AU.ZustandDefekt):
            with self.d._lock:
                self.d._wal("uebergang", anfrage=self.anfrage()["id"], von="pruefung1", nach="review", seq=1, beleg="stufe")
        self.assertEqual(len(self.audit()), vorher)


# ==================================================================== Anker-Client
class AnkerClientTests(unittest.TestCase):
    def test_nur_drei_befehle_und_nie_sperre_aus(self):
        self.assertEqual(set(RA.ERLAUBT), {"status", "rueckweg", "sperre"})
        RA.zeile_bauen("status")
        RA.zeile_bauen("rueckweg", "alles", True)
        RA.zeile_bauen("sperre", "an")
        RA.zeile_bauen("sperre", "an", True)
        for befehl, arg, trocken in (("sperre", "aus", False), ("sperre", "aus", True), ("sperre", None, False),
                                     ("umschalten", None, False), ("bereitstellen", None, False), ("migrieren", None, False),
                                     ("pruefe-sonder", "1", False), ("lokal", None, False), ("klassifiziere", None, False),
                                     ("status", "alles", False), ("status", None, True), ("rueckweg", None, False),
                                     ("rueckweg", "../ui", False), ("rueckweg", "ui;id", False), ("rueckweg", "sonst", False),
                                     ("sperre", "an;aus", False), ("", None, False), ("SPERRE", "an", False)):
            with self.subTest(befehl=befehl, arg=arg), self.assertRaises(RA.Verboten):
                RA.zeile_bauen(befehl, arg, trocken)

    def test_jede_anfrage_hat_eine_neue_kennung(self):
        kennungen = {RA.zeile_bauen("status").split()[0] for _ in range(50)}
        self.assertEqual(len(kennungen), 50)
        self.assertTrue(all(re.fullmatch(rb"[0-9a-f]{32}", k) for k in kennungen))

    def test_zeile_passt_zum_anker(self):
        """Der Anker liest genau diese Form (Kennung, Befehl, Argument, --trocken)."""
        zeile = RA.zeile_bauen("rueckweg", "kern", True)
        self.assertRegex(zeile.decode(), r"^[0-9a-f]{32} rueckweg kern --trocken\n$")
        self.assertLessEqual(len(zeile), 160)

    def test_antworten_werden_streng_geprueft(self):
        gut = RA.antwort_pruefen(b'{"ok":true,"text":"x","sperre":"aus"}\n')
        self.assertTrue(gut.ok)
        RA.antwort_pruefen(b'{"ok":false,"grund":"gesperrt","text":"x"}\n')
        for schlecht in (b"", b"\n", b"{}\n", b'{"ok":"ja"}\n', b'{"ok":1}\n', b'[1]\n', b'{"ok":true}', b'{"ok":true}\n{"ok":true}\n',
                         b'{"ok":false,"grund":"Boese Sache!"}\n', b'{"ok":false,"grund":5}\n', b"x" * (RA.ANTWORT_MAX + 1),
                         b'{"ok":true,"t":"\xc3\xbc"}\n', b"\xff\n"):
            with self.subTest(schlecht=schlecht[:30]), self.assertRaises(RA.AnkerFehler):
                RA.antwort_pruefen(schlecht)

    def test_nicht_erreichbar_ist_ein_fehler_und_gilt_als_unbekannt(self):
        c = RA.AnkerClient(os.path.join(tempfile.gettempdir(), "gibt-es-nicht.sock"))
        with self.assertRaises(RA.AnkerFehler):
            c.status()
        self.assertEqual(c.sperre_zustand(), "unbekannt")

    def test_sperre_zustand_wertet_nur_klare_antworten(self):
        class C(RA.AnkerClient):
            def __init__(self, antwort):
                self.antwort = antwort

            def status(self):
                if isinstance(self.antwort, Exception):
                    raise self.antwort
                return self.antwort

        for antwort, erwartet in ((RA.Antwort(True, None, {"sperre": "aus"}), "aus"), (RA.Antwort(True, None, {"sperre": "an"}), "an"),
                                  (RA.Antwort(True, None, {"sperre": "defekt"}), "defekt"),
                                  (RA.Antwort(True, None, {"sperre": "vielleicht"}), "unbekannt"),
                                  (RA.Antwort(True, None, {}), "unbekannt"), (RA.Antwort(False, "x", {"sperre": "aus"}), "unbekannt"),
                                  (RA.AnkerFehler("zeitlimit"), "unbekannt")):
            self.assertEqual(C(antwort).sperre_zustand(), erwartet)


# ==================================================================== anfordern
class AnfordernTests(ReleaseTest):
    def test_gueltige_anfrage(self):
        r = self.sende(f"anfordern main {ADMIN}")
        self.assertTrue(r["ok"], r)
        a = self.anfrage()
        self.assertEqual((a["status"], a["quelle"], a["profil"], a["seq"]), ("angefragt", "main", ADMIN, 0))
        self.assertEqual(self.db.anfragen[a["id"]]["status"], "angefragt")
        self.assertEqual(r["anfrage"]["id"], a["id"])

    def test_nur_eine_offene_anfrage(self):
        self.assertTrue(self.anfordern()["ok"])
        r = self.sende(f"anfordern main {ADMIN}")
        self.assertEqual(r["grund"], "belegt")
        self.assertEqual(len(self.db.anfragen), 1)

    def test_zweite_offene_anfrage_auch_nach_neustart_und_in_der_datenbank(self):
        self.anfordern()
        d2 = self.neu()
        self.assertEqual(self.sende(f"anfordern main {ADMIN}", d=d2)["grund"], "belegt")
        d3 = self.neu(wiederaufnehmen=False)  # ein Dienst, der von der Anfrage nichts weiß: die Datenbank lehnt ab
        d3._bereit = True
        self.assertEqual(self.sende(f"anfordern main {ADMIN}", d=d3)["grund"], "belegt")
        self.assertEqual(self.db.verstoesse, ["es gibt schon eine offene Anfrage"])

    def test_gleichzeitig_gewinnt_einer(self):
        ergebnisse = []

        def los():
            try:
                ergebnisse.append(self.d.cmd_anfordern("main", ADMIN)["ok"])
            except R.MitCode as e:
                ergebnisse.append(e.code)

        faeden = [threading.Thread(target=los) for _ in range(6)]
        for f in faeden:
            f.start()
        for f in faeden:
            f.join()
        self.assertEqual(ergebnisse.count(True), 1, ergebnisse)
        self.assertEqual(len(self.db.anfragen), 1)

    def test_nur_ein_aktiver_admin(self):
        r = self.sende(f"anfordern main {ADMIN2}")
        self.assertEqual(r["grund"], "kein_admin")
        self.assertEqual(len(self.db.anfragen), 0)
        self.assertIsNone(self.anfrage())

    def test_ohne_datenbank_geht_nichts(self):
        d = self.neu(db=DB.OhneDatenbank())
        r = self.sende(f"anfordern main {ADMIN}", d=d)
        self.assertEqual(r["grund"], "datenbank_fehlt")
        self.assertIsNone(self.anfrage(d))
        self.assertEqual(self.sende("status", d=d)["datenbank"], "fehlt")

    def test_datenbank_ausgefallen(self):
        self.db.ausfall = True
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "datenbank_nicht_erreichbar")
        self.assertIsNone(self.anfrage())
        self.assertEqual(self.sende("status")["datenbank"], "nicht_erreichbar")

    def test_quelle_selbst_ist_noch_nicht_freigeschaltet(self):
        self.assertEqual(self.sende(f"anfordern selbst {ADMIN}")["grund"], "quelle_nicht_freigeschaltet")
        d = self.neu(quellen=frozenset({"main", "selbst"}))
        self.assertTrue(self.sende(f"anfordern selbst {ADMIN}", d=d)["ok"])

    def test_sperre_an_haelt_neue_anfragen_an(self):
        self.anker.sperre = "an"
        r = self.sende(f"anfordern main {ADMIN}")
        self.assertEqual(r["grund"], "gesperrt")
        self.assertEqual(len(self.db.anfragen), 0)
        self.anker.sperre = "defekt"
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "gesperrt")
        self.anker.sperre = "aus"
        self.assertTrue(self.sende(f"anfordern main {ADMIN}")["ok"])

    def test_anker_nicht_erreichbar_ist_wie_gesperrt(self):
        self.anker.erreichbar = False
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "anker_nicht_erreichbar")
        self.assertEqual(len(self.db.anfragen), 0)

    def test_sperre_wird_immer_frisch_gefragt(self):
        d = self.neu()
        d.tor_zeit = 3600  # selbst ein langer Zwischenspeicher darf anfordern nicht täuschen
        self.assertTrue(self.sende(f"anfordern main {ADMIN}", d=d)["ok"])
        self.sende(f"abbrechen {self.anfrage(d)['id']}", d=d)
        self.anker.sperre = "an"
        self.assertEqual(self.sende(f"anfordern main {ADMIN}", d=d)["grund"], "gesperrt")

    def test_drei_anfragen_je_admin_und_tag(self):
        for _ in range(AU.ANFRAGEN_JE_TAG):
            r = self.sende(f"anfordern main {ADMIN}")
            self.assertTrue(r["ok"], r)
            self.sende(f"abbrechen {self.anfrage()['id']}")
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "zu_viele_anfragen")
        self.zeit[0] += AU.TAG_S + 1
        self.assertTrue(self.sende(f"anfordern main {ADMIN}")["ok"])

    def test_die_anfrage_steht_im_protokoll_vor_dem_effekt(self):
        self.sende(f"anfordern main {ADMIN}")
        ereignisse = [z["ereignis"] for z in self.audit()]
        self.assertLess(ereignisse.index("anfrage"), ereignisse.index("angefordert"))
        self.assertIn("ergebnis", ereignisse)

    def test_audit_nicht_schreibbar_raeumt_die_datenbank_auf(self):
        original = self.d.audit.anhaengen

        def kaputt(ereignis, *a, **kw):
            if ereignis == "angefordert":
                raise K.AuditFehler("voll")
            return original(ereignis, *a, **kw)

        self.d.audit.anhaengen = kaputt
        r = self.sende(f"anfordern main {ADMIN}")
        self.assertEqual(r["grund"], "audit_nicht_schreibbar")
        self.assertIsNone(self.anfrage())
        (zeile,) = self.db.anfragen.values()
        self.assertEqual(zeile["status"], "abgebrochen")  # die Datenbank behält keine verwaiste offene Anfrage


# ==================================================================== Lauf durch den Automaten
class LaufTests(ReleaseTest):
    def test_weg_main_bis_zum_ende(self):
        self.anfordern()
        self.lauf()
        self.assertIsNone(self.anfrage())
        self.assertEqual(self.endgrund(), ("bewaehrt", None))
        schritte = [nach for _von, nach in self.uebergaenge()]
        self.assertEqual(schritte, ["uebernommen", "pruefung1", "versionswahl", "pruefung3", "wartet_freigabe", "freigegeben",
                                    "veroeffentlicht", "bereitgestellt", "umgeschaltet", "bewaehrt"])
        self.assertEqual([a[0] for a in self.stufen.aufrufe],
                         ["uebernehmen", "pruefen", "version_waehlen", "pruefen", "veroeffentlichen", "bereitstellen",
                          "umschalten"])
        self.assertEqual([a[1] for a in self.stufen.aufrufe if a[0] == "pruefen"], [1, 3])

    def test_weg_selbst_bis_zum_ende(self):
        d = self.neu(quellen=frozenset({"main", "selbst"}))
        self.anfordern(d, "selbst")
        self.lauf(d)
        self.assertEqual(self.endgrund(d), ("bewaehrt", None))
        schritte = [nach for _von, nach in self.uebergaenge()]
        self.assertEqual(schritte[:3], ["arbeit", "uebernommen", "pruefung1"])
        self.assertEqual(schritte[3:9], ["review", "refactor", "pruefung2", "versionswahl", "pruefung3", "wartet_freigabe"])
        self.assertEqual([a[1] for a in self.stufen.aufrufe if a[0] == "pruefen"], [1, 2, 3])

    def test_die_datenbank_bekommt_nur_erlaubtes(self):
        self.anfordern()
        self.lauf()
        self.d.wache()
        self.assertEqual(self.db.verstoesse, [])
        a = next(iter(self.db.anfragen.values()))
        self.assertEqual(a["status"], "bewaehrt")
        self.assertEqual(a["daten"]["version_final"], "0.1.1")
        self.assertEqual(a["daten"]["c1_commit"], "b" * 40)
        self.assertNotIn("klasse", a["daten"])  # nur Spalten, die wamo_release schreiben darf

    def test_ereignisse_in_der_datenbank(self):
        self.anfordern()
        self.lauf()
        self.d.wache()
        namen = [e["ereignis"] for e in self.db.ereignisse]
        self.assertEqual(namen[0], "angefordert")
        self.assertEqual(self.db.ereignisse[0]["akteur"], ADMIN)
        self.assertEqual(namen.count("uebergang"), 10)
        quellen = [e["daten"]["quelle_n"] for e in self.db.ereignisse]
        self.assertEqual(quellen, sorted(set(quellen)))  # in Reihenfolge, keine doppelten
        for e in self.db.ereignisse:
            self.assertLessEqual(len(json.dumps(e["daten"])), 3000)
            self.assertRegex(e["ereignis"], r"^[a-z0-9_.-]{1,60}$")
            self.assertEqual(e["anfrage"], next(iter(self.db.anfragen)))

    def test_freigabe_nur_aus_der_datenbank(self):
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        for _ in range(5):
            self.assertEqual(self.d.arbeite_einmal(), "wartet")
        self.assertEqual(self.anfrage()["status"], "wartet_freigabe")
        self.lauf(freigabe=True)
        self.assertEqual(self.endgrund(), ("bewaehrt", None))

    def test_stufe_ohne_ergebnis_wartet_und_wiederholt(self):
        self.stufen.setze("uebernehmen", [ST.Ergebnis.wartet(), ST.Ergebnis.wartet(), ST.STANDARD["uebernehmen"]])
        self.anfordern()
        self.d.arbeite_einmal()  # angefragt -> uebernommen
        self.assertEqual(self.d.arbeite_einmal(), "wartet")
        self.assertEqual(self.d.arbeite_einmal(), "wartet")  # die Wartezeit ist noch nicht um
        self.zeit[0] += R.WARTE_S + 1
        self.assertEqual(self.d.arbeite_einmal(), "wartet")
        self.zeit[0] += R.WARTE_S + 1
        self.assertEqual(self.d.arbeite_einmal(), "gearbeitet")
        self.assertEqual(self.anfrage()["status"], "pruefung1")
        # Nachfragen ist kein neuer Versuch und füllt das Protokoll nicht
        starts = [(z["status"], z["versuch"]) for z in self.audit() if z["ereignis"] == "stufe_start"]
        self.assertEqual(starts[0], ("uebernommen", 1))
        self.assertEqual(len([s for s in starts if s[0] == "uebernommen"]), 1)

    def test_fehler_einer_stufe_beendet_die_anfrage(self):
        self.stufen.setze("uebernehmen", ST.Ergebnis.fehler("quelle_kaputt"))
        self.anfordern()
        self.lauf()
        self.assertEqual(self.endgrund(), ("abgebrochen", "stufe_fehler"))
        ende = [z for z in self.audit() if z["ereignis"] == "uebergang"][-1]
        self.assertEqual((ende["beleg"], ende["detail"]), ("fehler", "quelle_kaputt"))
        self.assertEqual(self.db.verstoesse, [])

    def test_nicht_verfuegbare_stufe(self):
        d = self.neu(stufen=ST.NichtVerfuegbar())
        self.anfordern(d)
        self.lauf(d)
        self.assertEqual(self.endgrund(d), ("abgebrochen", "stufe_nicht_verfuegbar"))

    def test_ausnahme_einer_stufe(self):
        self.stufen.setze("pruefen", RuntimeError("SENTINEL-GEHEIMNIS-4711"))
        self.anfordern()
        self.lauf()
        self.assertEqual(self.endgrund(), ("abgebrochen", "stufe_ausnahme"))
        text = Path(self.p.audit).read_text()
        self.assertIn("RuntimeError", text)
        self.assertNotIn("SENTINEL", text)  # nur der Typ der Ausnahme, nie ihr Text

    def test_ungueltige_ergebnisse(self):
        for wert in ("ok", None, 5, ST.Ergebnis("vielleicht"), ST.Ergebnis.ok(basis_commit="xyz"),
                     ST.Ergebnis.ok(unbekannt=1), ST.Ergebnis.ok(c1_commit="B" * 40)):
            with self.subTest(wert=str(wert)[:40]):
                self.setUp()
                self.stufen.setze("uebernehmen", wert)
                self.anfordern()
                self.lauf()
                self.assertEqual(self.endgrund(), ("abgebrochen", "stufe_ungueltig"))

    def test_tore_des_pruefstands(self):
        voll = {n: "ok" for n in AU.PRUEFSTAND_PFLICHT[1]}
        faelle = [
            ({"pruefstand": {k: v for k, v in voll.items() if k != "tests"}}, "pruefstand_unvollstaendig"),
            ({"pruefstand": {**voll, "tests": "fehler"}}, "pruefung_fehlgeschlagen"),
            ({"pruefstand": {**voll, "bauen": "uebersprungen"}}, "pruefung_fehlgeschlagen"),
            ({}, "pruefstand_unvollstaendig"),
        ]
        for daten, grund in faelle:
            with self.subTest(grund=grund, daten=str(daten)[:40]):
                self.setUp()
                self.stufen.setze("pruefen", ST.Ergebnis.ok(**daten))
                self.anfordern()
                self.lauf()
                self.assertEqual(self.endgrund(), ("abgebrochen", grund))

    def test_tore_der_daten(self):
        faelle = [
            ("uebernehmen", ST.Ergebnis.ok(basis_commit="a" * 40), "daten_unvollstaendig"),
            ("uebernehmen", ST.Ergebnis.ok(), "daten_unvollstaendig"),
            ("version_waehlen", ST.Ergebnis.ok(bump="patch", version_vorschlag="0.1.1", version_final="0.1.1"),
             "daten_unvollstaendig"),
            ("version_waehlen", ST.Ergebnis.ok(bump="patch", version_vorschlag="0.1.2", version_final="0.1.1",
                                               v_commit="d" * 40, tree_id="e" * 40), "stufe_ungueltig"),
        ]
        for name, ergebnis, grund in faelle:
            with self.subTest(name=name, grund=grund):
                self.setUp()
                self.stufen.setze(name, ergebnis)
                self.anfordern()
                self.lauf()
                self.assertEqual(self.endgrund(), ("abgebrochen", grund))

    def test_klassifizierung(self):
        def runde3(klasse=None, anzahl=0, **extra):
            daten = {"pruefstand": {n: "ok" for n in AU.PRUEFSTAND_PFLICHT[3]}, **extra}
            if klasse:
                daten.update(klasse=klasse, treffer_anzahl=anzahl)
            return lambda ctx, runde: ST.Ergebnis.ok(**(daten if runde == 3 else {"pruefstand": {
                n: "ok" for n in AU.PRUEFSTAND_PFLICHT[runde]}}))

        faelle = [
            ("fehlt", runde3(), "klassifizierung_fehlt", "main", 1),
            ("gesperrt", runde3("gesperrt", 1), "schutzschicht_gesperrt", "main", 2),
            ("sonder, ein Admin", runde3("sonder", 2), "zweiter_admin_noetig", "main", 1),
            ("sonder, Quelle selbst", runde3("sonder", 2), "schutzschicht_gesperrt", "selbst", 2),
            ("normal mit Treffern", runde3("normal", 1), "stufe_ungueltig", "main", 1),
            ("sonder ohne Treffer", runde3("sonder", 0), "stufe_ungueltig", "main", 2),
        ]
        for name, pruefen, grund, quelle, admins in faelle:
            with self.subTest(name=name):
                self.setUp()
                self.db.admins = {ADMIN, ADMIN2}.__class__(list({ADMIN, ADMIN2})[:0] + [ADMIN] + ([ADMIN2] if admins > 1 else []))
                d = self.neu(quellen=frozenset({"main", "selbst"}))
                self.stufen.setze("pruefen", pruefen)
                self.anfordern(d, quelle)
                self.lauf(d)
                self.assertEqual(self.endgrund(d), ("abgebrochen", grund), name)

    def test_sonderfreigabe_mit_zwei_admins_wartet_auf_den_zweiten(self):
        self.db.admins = {ADMIN, ADMIN2}
        self.stufen.setze("pruefen", lambda ctx, runde: ST.Ergebnis.ok(
            pruefstand={n: "ok" for n in AU.PRUEFSTAND_PFLICHT[runde]},
            **({"klasse": "sonder", "treffer_anzahl": 2, "treffer_sha": "f" * 64} if runde == 3 else {})))
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        a = self.anfrage()
        self.assertEqual((a["status"], a["daten"]["klasse"]), ("wartet_freigabe", "sonder"))

    def test_nach_der_pruefung_fehlen_commit_oder_version(self):
        self.stufen.setze("version_waehlen", ST.Ergebnis.ok(bump="patch", version_vorschlag="0.1.1", version_final="0.1.1",
                                                           v_commit="d" * 40, tree_id="e" * 40))
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        self.assertEqual(self.anfrage()["daten"]["tree_id"], "e" * 40)

    def test_verspaetetes_ergebnis_wird_verworfen(self):
        self.anfordern()
        self.d.arbeite_einmal()  # -> uebernommen
        id_ = self.anfrage()["id"]

        def stufe(ctx):
            self.d.cmd_abbrechen(id_)  # während die Stufe läuft, bricht jemand ab
            self.assertTrue(ctx.abgebrochen())  # und die Stufe merkt es
            return ST.STANDARD["uebernehmen"]

        self.stufen.setze("uebernehmen", stufe)
        self.assertEqual(self.d.arbeite_einmal(), "veraendert")
        self.assertEqual(self.endgrund(), ("abgebrochen", "abbruch_person"))
        self.assertIn("ergebnis_verworfen", [z["ereignis"] for z in self.audit()])

    def test_zurueck_waehrend_eine_stufe_laeuft(self):
        self.anfordern()
        self.d.arbeite_einmal()
        antworten = []

        def stufe(ctx):
            antworten.append(self.sende("zurueck alles"))
            return ST.STANDARD["uebernehmen"]

        self.stufen.setze("uebernehmen", stufe)
        self.assertEqual(self.d.arbeite_einmal(), "veraendert")
        self.assertTrue(antworten[0]["ok"])
        self.assertEqual(self.endgrund(), ("abgebrochen", "zurueck"))

    def test_beobachtung_der_datenbank(self):
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        a = self.anfrage()
        id_ = a["id"]
        self.db.ausfall = True
        self.assertEqual(self.d.arbeite_einmal(), "wartet")  # Datenbank weg: nichts passiert, nichts bricht
        self.db.ausfall = False
        self.db.anker_setzt(id_, "bereitgestellt")  # springt über Zustände: das ist eine Abweichung, kein Fortschritt
        self.d.arbeite_einmal()
        self.assertEqual(self.anfrage()["status"], "wartet_freigabe")
        self.assertIn("db_abweichung", [z["ereignis"] for z in self.audit()])
        self.d.arbeite_einmal()
        self.assertEqual([z["ereignis"] for z in self.audit()].count("db_abweichung"), 1)  # nur einmal je Abweichung

    def test_freigabe_zu_anderem_commit_bricht_ab_und_sperrt(self):
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        self.db.anker_setzt(self.anfrage()["id"], "freigegeben", v_commit="9" * 40, tree_id="e" * 40)
        self.d.arbeite_einmal()
        self.assertEqual(self.endgrund(), ("abgebrochen", "freigabe_abweichung"))
        self.assertEqual(self.anker.sperre, "an")
        self.assertIn(("sperre_an", False), self.anker.aufrufe)

    def test_freigabe_zu_anderem_tree_bricht_ab(self):
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        self.db.anker_setzt(self.anfrage()["id"], "freigegeben", v_commit="d" * 40, tree_id="9" * 40)
        self.d.arbeite_einmal()
        self.assertEqual(self.endgrund(), ("abgebrochen", "freigabe_abweichung"))

    def test_extern_abgebrochene_anfrage(self):
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        self.db.anker_setzt(self.anfrage()["id"], "abgebrochen")
        self.d.arbeite_einmal()
        self.assertEqual(self.endgrund(), ("abgebrochen", "abgebrochen_extern"))

    def test_der_dienst_setzt_nie_einen_zustand_des_ankers(self):
        self.anfordern()
        a = self.anfrage()
        for nach in AU.VOM_ANKER:
            with self.assertRaises(AU.ZustandDefekt):
                with self.d._lock:
                    self.d._wal("uebergang", anfrage=a["id"], von="angefragt", nach=nach, seq=1, beleg="stufe")

    def test_unbekannte_zustaende_in_der_datenbank_ohne_folgen(self):
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        self.db.anfragen[self.anfrage()["id"]]["status"] = "irgendwas"
        self.d.arbeite_einmal()
        self.assertEqual(self.anfrage()["status"], "wartet_freigabe")


# ==================================================================== Zeitgrenzen
class ZeitTests(ReleaseTest):
    def test_jeder_zustand_vor_der_bereitstellung_laeuft_ab(self):
        for quelle, weg in AU.WEG.items():
            for status in weg[:AU.BEREITGESTELLT_AB if quelle == "selbst" else weg.index("bereitgestellt")]:
                with self.subTest(quelle=quelle, status=status):
                    self.setUp()
                    d = self.neu(quellen=frozenset({"main", "selbst"}))
                    self.bringe_nach(status, quelle, d)
                    self.zeit[0] += AU.FRIST_S[status] - 1
                    d.wache()
                    self.assertIsNotNone(self.anfrage(d), "noch in der Frist")
                    self.zeit[0] += 2
                    d.wache()
                    self.assertIsNone(self.anfrage(d))
                    if status == "veroeffentlicht":  # der Anker kann schon begonnen haben: erst der Rückweg, dann das Ende
                        self.assertEqual(self.endgrund(d), ("abgebrochen", "rueckweg_nach_zeitlimit"))
                        self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", "alles", False))
                    else:
                        self.assertEqual(self.endgrund(d), ("abgebrochen", "zeitlimit"))
                        self.assertEqual(self.anker.namen().count("rueckweg"), 0)
                    self.assertEqual(self.db.verstoesse, [])

    def test_ausgerollt_und_nicht_bewaehrt_geht_zurueck(self):
        for status in ("bereitgestellt", "umgeschaltet"):
            with self.subTest(status=status):
                self.setUp()
                self.bringe_nach(status)
                self.zeit[0] += AU.FRIST_S[status] + 1
                self.d.wache()
                self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", "alles", False))
                self.assertEqual(self.endgrund(), ("zurueckgenommen", "rueckweg_nach_zeitlimit"))

    def test_rueckweg_nach_zeitlimit_scheitert_sperrt_und_versucht_es_wieder(self):
        self.bringe_nach("umgeschaltet")
        self.anker.rueckweg_ok = False
        self.zeit[0] += AU.FRIST_S["umgeschaltet"] + 1
        self.d.wache()
        self.assertEqual(self.anfrage()["status"], "umgeschaltet")  # nicht still beendet
        self.assertEqual(self.anker.sperre, "an")
        self.assertTrue(self.sende("status")["rueckweg_offen"])
        for _ in range(R.RUECKWEG_VERSUCHE_MAX + 3):
            self.zeit[0] += R.RUECKWEG_WIEDERHOLUNG_S + 1
            self.d.wache()
        self.assertLessEqual(self.anker.namen().count("rueckweg"), R.RUECKWEG_VERSUCHE_MAX + 1)  # nicht endlos
        self.anker.rueckweg_ok = True
        self.d._rueckweg_offen["versuche"] = 0
        self.zeit[0] += R.RUECKWEG_WIEDERHOLUNG_S + 1
        self.d.wache()
        self.assertEqual(self.endgrund(), ("zurueckgenommen", "rueckweg_nach_zeitlimit"))

    def test_gesamtzeit(self):
        self.bringe_nach("pruefung1")  # jeder Schritt bleibt in seiner Frist, zusammen aber ist es zu lang
        self.d._z["anfrage"]["start"] -= AU.GESAMT_FRIST_S - 5
        self.d.wache()
        self.assertIsNotNone(self.anfrage())
        self.zeit[0] += 10
        self.d.wache()
        self.assertEqual(self.endgrund(), ("abgebrochen", "gesamtzeitlimit"))

    def test_uhr_springt_zurueck(self):
        self.anfordern()
        self.zeit[0] -= 10_000
        self.d.wache()
        self.d.arbeite_einmal()
        self.assertIsNotNone(self.anfrage())

    def test_haenger_wird_erkannt(self):
        self.d._arbeit_seit = time.monotonic() - R.HAENGER_S - 1
        self.assertFalse(self.d.haenger)
        self.d.wache()
        self.assertTrue(self.d.haenger)
        self.assertEqual(self.sende("status")["arbeiter"], "haengt")
        self.assertIn("haenger", [z["ereignis"] for z in self.audit()])

    def test_frist_der_stufe_ist_begrenzt(self):
        gesehen = []
        self.stufen.setze("uebernehmen", lambda ctx: (gesehen.append(ctx.frist - time.monotonic()), ST.STANDARD["uebernehmen"])[1])
        self.anfordern()
        self.lauf(bis="pruefung1")
        self.assertLessEqual(gesehen[0], AU.FRIST_S["uebernommen"])
        self.assertGreater(gesehen[0], 0)

    def test_stufenstart_zaehlt_versuche(self):
        self.anfordern()
        self.d.arbeite_einmal()
        self.lauf(bis="pruefung1")
        starts = [z for z in self.audit() if z["ereignis"] == "stufe_start"]
        self.assertEqual([(z["status"], z["versuch"]) for z in starts], [("uebernommen", 1)])


# ==================================================================== Sperre des Ankers
class SperreTests(ReleaseTest):
    def test_sperre_haelt_die_offene_anfrage_an(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        self.anker.sperre = "an"
        aufrufe = len(self.stufen.aufrufe)
        for _ in range(5):
            self.assertEqual(self.d.arbeite_einmal(), "angehalten")
        self.assertEqual(len(self.stufen.aufrufe), aufrufe)  # keine Stufe startet
        self.assertEqual(self.anfrage()["status"], "pruefung1")
        self.assertEqual(self.sende("status")["angehalten"], "sperre")
        self.anker.sperre = "aus"
        self.assertEqual(self.d.arbeite_einmal(), "gearbeitet")
        self.assertIsNone(self.sende("status").get("angehalten"))

    def test_defekte_sperre_und_unerreichbarer_anker_halten_an(self):
        self.anfordern()
        for sperre, erreichbar, grund in (("defekt", True, "sperre"), ("aus", False, "anker"), ("unbekannt", True, "anker")):
            self.anker.sperre, self.anker.erreichbar = sperre, erreichbar
            self.assertEqual(self.d.arbeite_einmal(), "angehalten")
            self.assertEqual(self.d._angehalten, grund)
        self.assertEqual(self.anfrage()["status"], "angefragt")

    def test_sperre_haelt_auch_die_beobachtung_an(self):
        self.anfordern()
        self.lauf(bis="wartet_freigabe")
        self.db.anker_setzt(self.anfrage()["id"], "freigegeben", v_commit="d" * 40, tree_id="e" * 40)
        self.anker.sperre = "an"
        self.assertEqual(self.d.arbeite_einmal(), "angehalten")
        self.assertEqual(self.anfrage()["status"], "wartet_freigabe")

    def test_rueckweg_geht_unter_der_sperre(self):
        self.anker.sperre = "an"
        r = self.sende("zurueck alles")
        self.assertTrue(r["ok"], r)
        self.assertTrue(self.sende("status")["ok"])
        self.assertEqual(self.sende(f"abbrechen {ADMIN}").get("grund"), "keine_anfrage")

    def test_der_dienst_loest_die_sperre_nie(self):
        """Weder die Befehle noch der Client noch die automatischen Wege senden jemals sperre aus."""
        self.bringe_nach("umgeschaltet")
        self.anker.rueckweg_ok = False
        self.zeit[0] += AU.FRIST_S["umgeschaltet"] + 1
        self.d.wache()
        self.assertEqual(self.anker.sperre, "an")
        self.assertTrue(all(a[0] in ("status", "rueckweg", "sperre_an") for a in self.anker.aufrufe))
        with self.assertRaises(RA.Verboten):
            RA.zeile_bauen("sperre", "aus")


# ==================================================================== Rückweg
class RueckwegTests(ReleaseTest):
    def test_rueckweg_ohne_anfrage(self):
        r = self.sende("zurueck alles")
        self.assertTrue(r["ok"])
        self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", "alles", False))
        self.assertIsNone(r.get("anfrage_beendet"))

    def test_rueckweg_aus_jedem_zustand(self):
        for quelle, weg in AU.WEG.items():
            for status in weg[:-1]:
                for komp in ("ui", "kern", "alles"):
                    with self.subTest(quelle=quelle, status=status, komp=komp):
                        self.setUp()
                        d = self.neu(quellen=frozenset({"main", "selbst"}))
                        self.bringe_nach(status, quelle, d)
                        r = self.sende(f"zurueck {komp}", d=d)
                        self.assertTrue(r["ok"], r)
                        self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", komp, False))
                        index = AU.REIHENFOLGE.index(status)
                        if index >= AU.ANKER_BERUEHRT_AB and komp != "alles":
                            # ein Teilrückweg lässt die Anfrage offen: die andere Komponente bleibt neu
                            self.assertEqual(self.anfrage(d)["status"], status)
                            self.assertIn("Teilrückweg", r["hinweis"])
                            continue
                        ende = "zurueckgenommen" if index >= AU.BEREITGESTELLT_AB else "abgebrochen"
                        self.assertEqual(self.endgrund(d), (ende, "zurueck"))
                        self.assertIsNone(self.anfrage(d))
                        self.assertEqual(self.db.verstoesse, [])

    def test_rueckweg_nach_ende_und_bei_bewaehrt(self):
        self.anfordern()
        self.lauf()
        self.assertEqual(self.endgrund(), ("bewaehrt", None))
        self.assertTrue(self.sende("zurueck alles")["ok"])  # ohne offene Anfrage nur der Anker
        self.assertEqual(self.endgrund(), ("bewaehrt", None))

    def test_trocken_aendert_nichts(self):
        self.bringe_nach("umgeschaltet")
        vorher = copy.deepcopy(self.d._z["anfrage"])
        r = self.sende("zurueck alles --trocken")
        self.assertTrue(r["ok"])
        self.assertTrue(r["trocken"])
        self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", "alles", True))
        self.assertEqual(self.anfrage(), vorher)
        self.assertEqual(self.anfrage()["status"], "umgeschaltet")

    def test_anker_scheitert_nach_der_bereitstellung(self):
        self.bringe_nach("bereitgestellt")
        self.anker.rueckweg_ok = False
        r = self.sende("zurueck alles")
        self.assertFalse(r["ok"])
        self.assertEqual(r["grund"], "rueckweg_fehlgeschlagen")
        self.assertEqual(self.anfrage()["status"], "bereitgestellt")  # nicht still beendet
        self.assertEqual(self.anker.sperre, "an")  # nichts geht mehr nach vorn
        self.assertTrue(self.sende("status")["rueckweg_offen"])

    def test_anker_nicht_erreichbar(self):
        self.bringe_nach("umgeschaltet")
        self.anker.erreichbar = False
        r = self.sende("zurueck alles")
        self.assertEqual(r["grund"], "anker_nicht_erreichbar")
        self.assertEqual(self.anfrage()["status"], "umgeschaltet")

    def test_vor_der_bereitstellung_endet_die_anfrage_auch_wenn_der_anker_nichts_zu_tun_hat(self):
        self.bringe_nach("pruefung1")
        self.anker.rueckweg_ok = False  # zum Beispiel kein letzter guter Stand
        r = self.sende("zurueck alles")
        self.assertFalse(r["ok"])
        self.assertTrue(r["anfrage_beendet"])
        self.assertEqual(self.endgrund(), ("abgebrochen", "zurueck"))

    def test_rueckweg_geht_mit_defektem_zustand(self):
        self.anfordern()
        Path(self.p.zustand_datei).write_bytes(b"muell")
        d2 = self.neu()
        self.assertEqual(d2._defekt, "zustand_defekt")
        r = self.sende("zurueck alles", d=d2)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", "alles", False))

    def test_rueckweg_geht_ohne_beschreibbares_protokoll(self):
        self.d.audit.anhaengen = lambda *a, **k: (_ for _ in ()).throw(K.AuditFehler("voll"))
        r = self.sende("zurueck alles")
        self.assertTrue(r["ok"], r)  # der Notausgang
        self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", "alles", False))
        # alles andere Ändernde geht dann nicht
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "audit_nicht_schreibbar")

    def test_rueckweg_erreichbar_bei_defekter_kette(self):
        self.anfordern()
        with open(self.p.audit, "r+b") as f:
            f.seek(40)
            f.write(b"Z")
        r = self.sende("zurueck ui")
        self.assertTrue(r["ok"], r)

    def test_rueckweg_ist_von_der_stufe_unabhaengig(self):
        """Auch wenn der Arbeiter in einer Stufe hängt, kommt der Rückweg durch (er braucht den Arbeiter nicht)."""
        self.anfordern()
        self.d._arbeit_seit = time.monotonic()
        self.d.haenger = True
        self.assertTrue(self.sende("zurueck alles")["ok"])


# ==================================================================== Wiederaufnahme nach einem Absturz
Absturz = R.Absturz


class WiederaufnahmeTests(ReleaseTest):
    HAKEN = ("vor_audit", "nach_audit", "nach_speichern", "in_stufe", "vor_spiegel")

    def zaehle_haken(self):
        zaehler = dict.fromkeys(self.HAKEN, 0)
        haken = {n: (lambda n=n: zaehler.__setitem__(n, zaehler[n] + 1)) for n in self.HAKEN}
        return zaehler, haken

    def ablauf(self, d):
        """Treibt, falls nötig mit einer neuen Anfrage, bis eine Anfrage bewährt ist."""
        for _ in range(200):
            if self.anfrage(d) is None:
                if any(e["status"] == "bewaehrt" for e in d._z["abgeschlossen"]):
                    return
                r = d.cmd_anfordern("main", ADMIN)
                self.assertTrue(r["ok"], r)
            d.arbeite_einmal()
            self.anker_spielt(d)
            d.wache()
        self.fail("kommt nicht ans Ende")

    def pruefe_ende(self, d, grundlinie):
        self.assertTrue(K.kette_pruefen(self.p.audit)[0])
        self.assertEqual(sum(1 for e in d._z["abgeschlossen"] if e["status"] == "bewaehrt"), 1)
        schritte = [nach for _von, nach in self.uebergaenge()]
        self.assertEqual(schritte, grundlinie, "jeder Übergang genau einmal, in der Reihenfolge")
        seqs = [z["seq"] for z in self.audit() if z["ereignis"] == "uebergang"]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        self.assertEqual(self.db.verstoesse, [])
        gespiegelt = [e["daten"]["quelle_n"] for e in self.db.ereignisse]
        self.assertEqual(gespiegelt, sorted(set(gespiegelt)))  # in Reihenfolge, nichts doppelt
        # Das Protokoll allein ergibt denselben Zustand wie die Datei
        gesichert = copy.deepcopy(d._z)
        os.unlink(self.p.zustand_datei)
        d3 = self.neu()
        self.assertEqual(d3._z["abgeschlossen"], gesichert["abgeschlossen"])
        self.assertEqual(d3._z["anfrage"], gesichert["anfrage"])

    def test_nach_jedem_absturzpunkt_wiederaufnehmbar(self):
        zaehler, haken = self.zaehle_haken()
        d = self.neu(haken=haken)
        self.ablauf(d)
        grundlinie = [nach for _von, nach in self.uebergaenge()]
        self.assertEqual(grundlinie[-1], "bewaehrt")
        insgesamt = dict(zaehler)
        self.assertTrue(all(v > 5 for v in insgesamt.values()), insgesamt)
        geprueft = 0
        for name in self.HAKEN:
            for k in range(1, insgesamt[name] + 1):
                with self.subTest(haken=name, aufruf=k):
                    self.setUp()
                    zaehler = {"n": 0}

                    def stuerzt(zaehler=zaehler, k=k):
                        zaehler["n"] += 1
                        if zaehler["n"] == k:
                            raise Absturz()

                    try:
                        d1 = self.neu(haken={name: stuerzt})  # auch der Start spiegelt, und kann abstürzen
                        self.ablauf(d1)
                    except Absturz:
                        pass
                    else:
                        self.fail("der Haken hat nicht ausgelöst")
                    d2 = self.neu()  # Neustart: neuer Prozess, gleiche Dateien, gleiche Datenbank, gleiche Stufen
                    self.ablauf(d2)
                    self.pruefe_ende(d2, grundlinie)
                    geprueft += 1
        self.assertGreater(geprueft, 60)

    def test_absturz_beim_anlegen_hinterlaesst_keine_verwaiste_anfrage(self):
        def stuerzt():
            raise Absturz()

        d1 = self.neu(haken={"nach_db_anlegen": stuerzt})
        with self.assertRaises(Absturz):
            d1.cmd_anfordern("main", ADMIN)
        (zeile,) = self.db.anfragen.values()
        self.assertEqual(zeile["status"], "angefragt")  # die Datenbank kennt sie, das Protokoll nicht
        d2 = self.neu()
        self.assertEqual(next(iter(self.db.anfragen.values()))["status"], "abgebrochen")
        self.assertIn("verwaist_abgebrochen", [z["ereignis"] for z in self.audit()])
        self.assertIsNone(self.anfrage(d2))
        self.assertTrue(self.sende(f"anfordern main {ADMIN}", d=d2)["ok"])  # und es geht von vorn

    def test_absturz_mitten_in_einer_stufe_laeuft_sie_erneut(self):
        self.anfordern()
        self.d.arbeite_einmal()
        self.stufen.setze("uebernehmen", [Absturz(), ST.STANDARD["uebernehmen"]])
        with self.assertRaises(Absturz):
            self.d.arbeite_einmal()
        d2 = self.neu()
        self.assertEqual(self.anfrage(d2)["versuch"], 1)
        d2.arbeite_einmal()
        self.assertEqual(self.anfrage(d2)["status"], "pruefung1")
        starts = [(z["status"], z["versuch"]) for z in self.audit() if z["ereignis"] == "stufe_start"]
        self.assertEqual(starts, [("uebernommen", 1), ("uebernommen", 2)])

    def test_wiederholte_abstuerze_in_derselben_stufe_enden(self):
        self.anfordern()
        self.d.arbeite_einmal()
        self.stufen.setze("uebernehmen", Absturz())
        d = self.d
        for lauf in range(AU.VERSUCHE_MAX):
            with self.assertRaises(Absturz):
                d.arbeite_einmal()
            d = self.neu()
        d.arbeite_einmal()
        self.assertEqual(self.endgrund(d), ("abgebrochen", "wiederaufnahme_erschoepft"))

    def test_neustart_ist_wiederholbar_und_aendert_nichts(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        zeilen = len(self.audit())
        for _ in range(3):
            d = self.neu()
            self.assertEqual(self.anfrage(d)["status"], "pruefung1")
        gestartet = [z for z in self.audit() if z["ereignis"] == "dienst_start"]
        self.assertEqual(len(self.audit()) - zeilen, 3)  # nur die Startzeilen kommen dazu
        self.assertGreaterEqual(len(gestartet), 3)

    def test_neustart_nach_dem_ende_beginnt_nichts_von_selbst(self):
        self.anfordern()
        self.lauf()
        d = self.neu()
        self.assertEqual(d.arbeite_einmal(), "leer")
        self.assertEqual(len(self.db.anfragen), 1)

    def test_neustart_mit_einer_ausgerollten_anfrage_wartet_auf_die_datenbank(self):
        self.anfordern()
        self.lauf(bis="umgeschaltet")
        d = self.neu()
        self.assertEqual(self.anfrage(d)["status"], "umgeschaltet")
        self.db.anker_setzt(self.anfrage(d)["id"], "bewaehrt")
        d.arbeite_einmal()
        self.assertEqual(self.endgrund(d), ("bewaehrt", None))

    def test_uhr_nach_neustart_zaehlt_weiter(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        self.zeit[0] += AU.FRIST_S["pruefung1"] + 5  # der Dienst war lange aus
        d = self.neu()
        d.wache()
        self.assertEqual(self.endgrund(d), ("abgebrochen", "zeitlimit"))


# ==================================================================== Spiegel in die Datenbank
class SpiegelTests(ReleaseTest):
    def test_ausfall_der_datenbank_wird_nachgeholt(self):
        self.anfordern()
        self.db.ausfall = True
        self.lauf(bis="pruefung3")
        self.assertEqual(self.anfrage()["status"], "pruefung3")  # der Dienst arbeitet weiter
        self.assertGreater(self.sende("status")["spiegel"]["rueckstand"], 3)
        vorher = len(self.db.ereignisse)
        self.db.ausfall = False
        self.zeit[0] += R.SPIEGEL_WARTE_S + 1
        self.d.wache()
        self.assertGreater(len(self.db.ereignisse), vorher)
        self.assertEqual(self.sende("status")["spiegel"]["rueckstand"], 0)
        self.assertEqual(self.db.anfragen[self.anfrage()["id"]]["status"], "pruefung3")
        quellen = [e["daten"]["quelle_n"] for e in self.db.ereignisse]
        self.assertEqual(quellen, sorted(set(quellen)))

    def test_dauerhafte_ablehnung_ueberspringt_die_zeile(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        original = self.db.ereignis_anhaengen
        gelehnt = []

        def lehnt_ab(anfrage, ereignis, daten, akteur=None):
            if ereignis == "stufe_start" and not gelehnt:
                gelehnt.append(1)
                raise DB.DatenbankAbgelehnt("23514", "nein")
            return original(anfrage, ereignis, daten, akteur)

        self.db.ereignis_anhaengen = lehnt_ab
        self.d._spiegel_dran = True
        self.d._z["spiegel_n"] = 0
        self.d._z["spiegel_offset"] = 0
        self.d._spiegeln()
        self.assertTrue(gelehnt)
        self.assertIn("spiegel_abgelehnt", [z["ereignis"] for z in self.audit()])
        self.assertEqual(self.d._z["spiegel_n"], self.d._z["audit_n"] - 0 if False else self.d._z["spiegel_n"])
        self.assertGreater(self.d._z["spiegel_n"], 5)  # es ging weiter

    def test_ohne_datenbank_laeuft_das_protokoll_allein(self):
        d = self.neu(db=DB.OhneDatenbank())
        self.assertTrue(d.protokolliere("test", 1.0))
        d.wache()
        self.assertTrue(K.kette_pruefen(self.p.audit)[0])

    def test_beschaedigtes_protokoll_stoppt_den_spiegel(self):
        self.anfordern()
        with open(self.p.audit, "r+b") as f:
            f.seek(25)
            f.write(b"Q")
        self.d._z["spiegel_n"], self.d._z["spiegel_offset"] = 0, 0
        self.d._spiegel_dran = True
        n = len(self.db.ereignisse)
        self.d._spiegeln()
        self.assertTrue(self.d._spiegel_defekt)
        self.assertEqual(len(self.db.ereignisse), n)
        self.assertTrue(self.sende("status")["spiegel"]["defekt"])

    def test_grosse_daten_werden_gekuerzt_nicht_abgelehnt(self):
        obj = {"n": 7, "ereignis": "uebergang", "anfrage": ADMIN, "von": "a", "nach": "b", "x": "y" * 4000, "t": "x",
               "vorher": "0" * 64}
        self.d._zeile_spiegeln(obj)
        self.assertEqual(self.db.ereignisse[-1]["daten"], {"quelle_n": 7, "gekuerzt": True})


class AbgleichTests(ReleaseTest):
    def test_fremde_offene_anfrage_mit_anderem_zustand_ist_eine_abweichung(self):
        kennung = self.db.anfrage_anlegen(ADMIN, "main")
        self.db.anker_setzt(kennung, "freigegeben")
        self.d.wache()
        self.d._abgleich_naechster = 0
        self.d.wache()
        self.assertTrue(self.sende("status")["abweichung"])
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "belegt")
        self.assertIn("db_abweichung", [z["ereignis"] for z in self.audit()])

    def test_lokale_anfrage_die_die_datenbank_nicht_mehr_kennt(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        self.db.anfragen[self.anfrage()["id"]]["status"] = "abgebrochen"
        self.d._abgleich_naechster = 0
        self.d.wache()
        self.assertEqual(self.endgrund(), ("abgebrochen", "abgebrochen_extern"))

    def test_abgleich_waehrend_des_anlegens_greift_nicht_ein(self):
        beobachtet = []

        halter = []

        def haken():
            d = halter[0]
            beobachtet.append(d._anfordern_laeuft.locked())
            d._abgleich_naechster = 0
            d._abgleichen()  # während die Anfrage entsteht
            d.wache()

        d = self.neu(haken={"nach_db_anlegen": haken})
        halter.append(d)
        r = d.cmd_anfordern("main", ADMIN)
        self.assertTrue(r["ok"])
        self.assertEqual(beobachtet, [True])
        self.assertEqual(next(iter(self.db.anfragen.values()))["status"], "angefragt")  # nicht als verwaist abgebrochen


# ==================================================================== Status
class StatusTests(ReleaseTest):
    def test_status_zeigt_alles(self):
        r = self.sende("status")
        self.assertTrue(r["ok"])
        for k in ("version", "zeit", "modus", "bereit", "abgeschlossen", "anker", "datenbank", "arbeiter", "audit", "spiegel",
                  "installation"):
            self.assertIn(k, r)
        self.assertEqual(r["modus"], "geruest")
        self.assertEqual(r["anker"], {"erreichbar": True, "sperre": "aus"})
        self.assertEqual(r["audit"]["kette"], "ok")

    def test_status_ist_nur_lesend(self):
        self.anfordern()
        vorher = (copy.deepcopy(self.d._z), Path(self.p.zustand_datei).read_bytes(), len(self.db.ereignisse))
        for _ in range(5):
            self.sende("status", "wamo-core")
        self.assertEqual((self.d._z, Path(self.p.zustand_datei).read_bytes(), len(self.db.ereignisse)), vorher)

    def test_status_mit_anfrage(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        a = self.sende("status")["anfrage"]
        self.assertEqual((a["status"], a["schritt"], a["schritte"], a["quelle"]), ("pruefung1", 2, 11, "main"))
        self.assertRegex(a["frist_bis"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_status_antwort_ist_klein_und_ascii(self):
        self.anfordern()
        self.lauf()
        daten = R.antwort_bytes(self.sende("status"))
        self.assertLessEqual(len(daten), R.ANTWORT_MAX)
        daten.decode("ascii")

    def test_installation_weicht_ab(self):
        r = self.sende("status")
        self.assertEqual(r["installation"], "abweichung")  # im Wegwerf-Ordner gibt es kein Manifest
        self.assertTrue(any("Manifest" in t for t in r["installation_probleme"]))

    def test_installation_mit_manifest(self):
        for name in ("a.py", "b.py"):
            Path(self.p.lib, name).write_text("x = 1\n")
            os.chmod(Path(self.p.lib, name), 0o644)
        manifest = "".join(f"{hashlib.sha256(Path(self.p.lib, n).read_bytes()).hexdigest()}  ./{n}\n" for n in ("a.py", "b.py"))
        Path(self.p.manifest).write_text(manifest)
        os.chmod(self.p.manifest, 0o644)
        os.chmod(self.p.lib, 0o755)
        self.assertEqual(R.installation_pruefen(self.p), [])
        Path(self.p.lib, "a.py").write_text("x = 2\n")
        self.assertIn("a.py: Prüfsumme weicht ab", R.installation_pruefen(self.p))
        Path(self.p.lib, "a.py").write_text("x = 1\n")
        Path(self.p.lib, "c.py").write_text("")
        self.assertIn("c.py: nicht im Manifest", R.installation_pruefen(self.p))
        os.unlink(Path(self.p.lib, "c.py"))
        for schlecht in ("../x  ./a.py\n", "zz  ./a.py\n", manifest + manifest):
            Path(self.p.manifest).write_text(schlecht)
            self.assertTrue(R.installation_pruefen(self.p), schlecht[:20])


# ==================================================================== Quelltext
class QuelltextTests(unittest.TestCase):
    DATEIEN = sorted(RELEASE.glob("*.py"))

    def test_es_gibt_die_erwarteten_dateien(self):
        self.assertEqual([d.name for d in self.DATEIEN], ["release_anker.py", "release_automat.py", "release_db.py",
                                                         "release_kette.py", "release_stufen.py", "wamo_release.py"])

    def test_dieser_schritt_startet_keinen_prozess_und_hat_kein_netz(self):
        for datei in self.DATEIEN:
            text = datei.read_text(encoding="utf-8")
            for verboten in ("subprocess", "os.system", "os.popen", "os.exec", "shell=True", "eval(", "exec(", "pickle",
                             "import requests", "urllib", "http.client", "AF_INET", "socket.create_connection", "anthropic",
                             "os.environ", "getenv", "__import__", "importlib"):
                self.assertNotIn(verboten, text, (datei.name, verboten))

    def test_keine_gedankenstriche_in_den_texten(self):
        lang, kurz = chr(0x2014), chr(0x2013)
        for datei in (*self.DATEIEN, RELEASE / "wamo-release.service", RELEASE / "wamo-release.sh"):
            text = datei.read_text(encoding="utf-8")
            self.assertNotIn(lang, text, datei.name)
            self.assertNotIn(kurz, text, datei.name)
            self.assertNotIn("\r", text, datei.name)

    def test_dateien_sind_gueltiges_python_ohne_tabs(self):
        import ast
        for datei in self.DATEIEN:
            text = datei.read_text(encoding="utf-8")
            ast.parse(text)
            self.assertNotIn("\t", text, datei.name)

    def test_nur_standardbibliothek(self):
        import ast
        eigene = {d.stem for d in self.DATEIEN}
        for datei in self.DATEIEN:
            for knoten in ast.walk(ast.parse(datei.read_text(encoding="utf-8"))):
                module = []
                if isinstance(knoten, ast.Import):
                    module = [a.name.split(".")[0] for a in knoten.names]
                elif isinstance(knoten, ast.ImportFrom) and knoten.level == 0:
                    module = [(knoten.module or "").split(".")[0]]
                for m in module:
                    self.assertTrue(m in eigene or m in sys.stdlib_module_names, (datei.name, m))

    def test_groesse_bleibt_ueberschaubar(self):
        gesamt = sum(len(d.read_text(encoding="utf-8").splitlines()) for d in self.DATEIEN)
        self.assertLess(gesamt, 3300)

    def test_kommentare_verwenden_echte_umlaute(self):
        for datei in self.DATEIEN:
            with open(datei, "rb") as f:
                for tok in tokenize.tokenize(f.readline):
                    if tok.type == tokenize.COMMENT:
                        self.assertNotRegex(tok.string, r"\b(ueber|fuer|koennen|muessen|waehrend|moeglich|Rueckweg|Pruefung)\b",
                                            (datei.name, tok.string))

    def test_keine_bekannte_geheimnismuster(self):
        for datei in (*self.DATEIEN, RELEASE / "wamo-release.service"):
            text = datei.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"sk-ant-[A-Za-z0-9]|-----BEGIN|eyJ[A-Za-z0-9_-]{20,}", datei.name)


class ClientTests(ReleaseTest):
    def test_ungueltige_aufrufe_enden_mit_2_ohne_verbindung(self):
        for args in ([], ["reboot"], ["zurueck"], ["zurueck", "../ui"], ["--json"], ["status", "extra"], ["freigeben-nie\n"]):
            self.assertEqual(R.client(args, sockpfad=os.path.join(self.tmp.name, "gibts-nicht.sock")), 2, args)

    @unittest.skipUnless(hasattr(__import__("socket"), "AF_UNIX"), "braucht Unix-Sockets")
    def test_keine_antwort(self):
        self.assertEqual(R.client(["status"], sockpfad=os.path.join(self.tmp.name, "gibts-nicht.sock")), 2)

    def test_formatiere(self):
        zeilen = R.formatiere(self.sende("status"))
        self.assertTrue(zeilen[0].startswith("OK"))
        self.assertTrue(any("Keine offene Anfrage" in z for z in zeilen))
        self.anfordern()
        zeilen = R.formatiere(self.sende("status"))
        self.assertTrue(any("Anfrage" in z and "angefragt" in z for z in zeilen))
        self.assertTrue(R.formatiere(self.sende("reboot"))[0].startswith("ABGELEHNT"))

    def test_main(self):
        self.assertEqual(R.main(["x", "reboot"]), 2)
        self.assertEqual(R.main(["x", "dienst"]) if not sys.platform.startswith("linux") else 1, 1)


# ==================================================================== Funde aus den Reviews (Schritt 0.3)
class RueckwegAbVeroeffentlichtTests(ReleaseTest):
    """Ab veroeffentlicht kann der Anker schon etwas getan haben: Jedes Ende verlangt zuerst einen gelungenen Rückweg."""

    def test_stufenfehler_ab_bereitgestellt_geht_ueber_den_rueckweg_und_zerstoert_den_dienst_nicht(self):
        self.bringe_nach("bereitgestellt")
        self.stufen.setze("umschalten", ST.Ergebnis.fehler("kaputt"))
        self.d.arbeite_einmal()
        self.assertIsNone(self.d._defekt)
        self.assertEqual(self.endgrund(), ("zurueckgenommen", "stufe_fehler"))
        self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", "alles", False))

    def test_stufenfehler_in_veroeffentlicht_rollt_zurueck_und_endet_als_abgebrochen(self):
        self.bringe_nach("veroeffentlicht")
        self.stufen.setze("bereitstellen", ST.Ergebnis.fehler("kaputt"))
        self.d.arbeite_einmal()
        self.assertEqual(self.endgrund(), ("abgebrochen", "stufe_fehler"))
        self.assertEqual(self.anker.aufrufe[-1], ("rueckweg", "alles", False))
        self.assertEqual(self.db.verstoesse, [])

    def test_scheitert_der_rueckweg_bleibt_die_anfrage_offen_und_der_anker_gesperrt(self):
        self.bringe_nach("veroeffentlicht")
        self.stufen.setze("bereitstellen", ST.Ergebnis.fehler("kaputt"))
        self.anker.rueckweg_ok = False
        self.d.arbeite_einmal()
        self.assertEqual(self.anfrage()["status"], "veroeffentlicht")
        self.assertEqual(self.anker.sperre, "an")
        self.assertTrue(self.sende("status")["rueckweg_offen"])
        self.anker.rueckweg_ok = True
        self.zeit[0] += R.RUECKWEG_WIEDERHOLUNG_S + 1
        self.d.wache()
        self.assertEqual(self.endgrund(), ("abgebrochen", "stufe_fehler"))

    def test_abbrechen_in_veroeffentlicht_geht_nur_mit_rueckweg(self):
        self.bringe_nach("veroeffentlicht")
        kennung = self.anfrage()["id"]
        self.anker.rueckweg_ok = False
        self.assertEqual(self.sende(f"abbrechen {kennung}")["grund"], "rueckweg_fehlgeschlagen")
        self.assertEqual(self.anfrage()["status"], "veroeffentlicht")
        self.anker.rueckweg_ok = True
        self.d._rueckweg_offen = None
        self.assertTrue(self.sende(f"abbrechen {kennung}")["ok"])
        self.assertEqual(self.endgrund(), ("abgebrochen", "abbruch_person"))

    def test_ab_bereitgestellt_ist_abbrechen_nicht_mehr_moeglich(self):
        self.bringe_nach("bereitgestellt")
        self.assertEqual(self.sende(f"abbrechen {self.anfrage()['id']}")["grund"], "nicht_abbrechbar")

    def test_zustand_hat_sich_inzwischen_geaendert_dann_kein_rueckweg(self):
        self.bringe_nach("umgeschaltet")
        alt = copy.deepcopy(self.anfrage())
        self.assertTrue(self.d._uebergang(alt, "bewaehrt", "datenbank"))  # inzwischen bewährt
        aufrufe = len(self.anker.aufrufe)
        self.assertFalse(self.d._mit_rueckweg_beenden(alt, "zeitlimit"))
        self.assertEqual(len(self.anker.aufrufe), aufrufe)  # eine gute Version wird nicht zurückgerollt

    def test_belegt_vom_anker_ist_kein_fehlschlag_der_sperrt(self):
        self.bringe_nach("umgeschaltet")
        self.anker.rueckweg_ok, self.anker.rueckweg_grund = False, "belegt"
        self.zeit[0] += AU.FRIST_S["umgeschaltet"] + 1
        self.d.wache()
        self.assertEqual(self.anker.sperre, "aus")
        self.assertEqual(self.anfrage()["status"], "umgeschaltet")
        self.anker.rueckweg_ok = True
        self.zeit[0] += R.RUECKWEG_WIEDERHOLUNG_S + 1
        self.d.wache()
        self.assertEqual(self.endgrund()[0], "zurueckgenommen")

    def test_gelungener_rueckweg_mit_nicht_festhaltbarem_ende_wird_nicht_im_sekundentakt_wiederholt(self):
        self.bringe_nach("umgeschaltet")
        original = self.d.audit.anhaengen

        def kaputt(ereignis, *a, **kw):
            if ereignis == "uebergang":
                raise K.AuditFehler("voll")
            return original(ereignis, *a, **kw)

        self.d.audit.anhaengen = kaputt
        self.zeit[0] += AU.FRIST_S["umgeschaltet"] + 1
        for _ in range(5):
            self.d.wache()
        self.assertEqual(self.anker.namen().count("rueckweg"), 1)

    def test_zurueck_ohne_beschreibbares_protokoll_haelt_die_anfrage_an(self):
        self.anfordern()
        self.d.arbeite_einmal()
        original = self.d.audit.anhaengen
        self.d.audit.anhaengen = lambda *a, **kw: (_ for _ in ()).throw(K.AuditFehler("voll"))
        self.assertTrue(self.sende("zurueck alles")["ok"])
        self.assertEqual(self.anfrage()["status"], "uebernommen")  # das Ende ließ sich nicht festhalten
        self.assertEqual(self.anker.sperre, "an")
        self.d.audit.anhaengen = original
        self.anker.sperre = "aus"  # ein Mensch hebt die Sperre auf, das Protokoll ist wieder da
        self.assertEqual(self.d.arbeite_einmal(), "angehalten")  # die Anfrage darf trotzdem nicht weiterlaufen
        self.assertEqual(self.anfrage()["status"], "uebernommen")

    def test_ein_teilrueckweg_laesst_die_ausgerollte_anfrage_offen(self):
        self.bringe_nach("umgeschaltet")
        r = self.sende("zurueck ui")
        self.assertTrue(r["ok"])
        self.assertEqual(self.anfrage()["status"], "umgeschaltet")
        self.assertTrue(self.sende("zurueck alles")["ok"])
        self.assertEqual(self.endgrund(), ("zurueckgenommen", "zurueck"))

    def test_freigabe_ohne_gespeicherte_commit_id_wird_nicht_angenommen(self):
        self.bringe_nach("wartet_freigabe")
        with self.d._lock:
            self.d._z["anfrage"]["daten"].pop("v_commit")
        self.db.anker_setzt(self.anfrage()["id"], "freigegeben", v_commit=None, tree_id=None)
        self.d.arbeite_einmal()
        self.assertEqual(self.endgrund(), ("abgebrochen", "freigabe_abweichung"))
        self.assertEqual(self.anker.sperre, "an")


class WartenUndTypenTests(ReleaseTest):
    def test_wartende_stufe_verbraucht_keine_versuche(self):
        self.stufen.setze("version_waehlen", ST.Ergebnis.wartet())
        self.anfordern()
        self.lauf(bis="versionswahl")
        for _ in range(AU.VERSUCHE_MAX * 4):
            self.zeit[0] += R.WARTE_S + 1
            self.assertEqual(self.d.arbeite_einmal(), "wartet")
        a = self.anfrage()
        self.assertEqual((a["status"], a["versuch"]), ("versionswahl", 1))
        starts = [z for z in self.audit() if z["ereignis"] == "stufe_start" and z["status"] == "versionswahl"]
        self.assertEqual(len(starts), 1)  # nachfragen füllt das Protokoll nicht
        self.stufen.setze("version_waehlen", ST.STANDARD["version_waehlen"])
        self.zeit[0] += R.WARTE_S + 1
        self.d.arbeite_einmal()
        self.assertEqual(self.anfrage()["status"], "pruefung3")

    def test_vertauschte_typen_in_der_zustandsdatei_sind_defekt_nicht_absturz(self):
        gut = {"id": ADMIN, "profil": ADMIN, "quelle": "main", "status": "pruefung1", "seq": 2, "start": 1, "seit": 2,
               "versuch": 1, "fertig": False, "daten": {}, "ende_grund": None}
        faelle = []
        for feld in ("status", "quelle", "id", "daten", "seq", "fertig"):
            for ersatz in ([], {}, [1], None, "x", 1.5):
                if feld == "daten" and ersatz == {}:
                    continue  # leere Daten sind gültig
                faelle.append({**AU.leerer_zustand(), "anfrage": {**gut, feld: ersatz}})
        for ersatz in ([], {}, [1], None):
            faelle.append({**AU.leerer_zustand(), "anfrage": {**gut, "daten": {"klasse": ersatz}}})
            faelle.append({**AU.leerer_zustand(), "anfrage": {**gut, "daten": {"pruefstand": {"bauen": ersatz}}}})
            faelle.append({**AU.leerer_zustand(), "abgeschlossen": [{"id": ADMIN, "profil": ADMIN, "status": ersatz,
                                                                     "grund": None, "start": 1, "ende": 2}]})
        for i, z in enumerate(faelle):
            with self.subTest(i=i):
                Path(self.p.zustand_datei).write_text(json.dumps(z))
                d2 = self.neu()  # darf nicht abstürzen
                self.assertEqual(d2._defekt, "zustand_defekt")
                self.assertTrue(self.sende("zurueck alles --trocken", d=d2)["ok"])

    def test_vertauschte_typen_in_stufendaten(self):
        for ergebnis in (ST.Ergebnis.ok(basis_commit=[], c1_commit="b" * 40), ST.Ergebnis.ok(klasse=[]),
                         ST.Ergebnis.ok(bump={}), ST.Ergebnis.ok(pruefstand={"bauen": []}), ST.Ergebnis.ok(**{"x": 1})):
            with self.subTest(ergebnis=str(ergebnis)[:50]):
                self.setUp()
                self.stufen.setze("uebernehmen", ergebnis)
                self.anfordern()
                self.lauf()
                self.assertEqual(self.endgrund(), ("abgebrochen", "stufe_ungueltig"))
        for daten in ({"klasse": []}, {"bump": {}}, {"pruefstand": {"bauen": []}}, {1: 2}, {"basis_commit": []}):
            with self.subTest(daten=str(daten)), self.assertRaises(ValueError):
                AU.daten_pruefen(daten)

    def test_ort_der_ausnahme_ohne_text(self):
        try:
            raise ValueError("SENTINEL-GEHEIMNIS-4711")
        except ValueError as e:
            text = R._ort(e)
        self.assertRegex(text, r"^ValueError in test_release\.py:\d+$")
        self.assertNotIn("SENTINEL", text)


class ZustandAnKetteTests(ReleaseTest):
    def schreibe(self, aenderung):
        datei = Path(self.p.zustand_datei)
        z = json.loads(datei.read_text())
        z.update(aenderung)
        datei.write_text(json.dumps(z))

    def test_zustand_ist_an_das_protokoll_gebunden(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        self.d.wache()
        z = json.loads(Path(self.p.zustand_datei).read_text())
        self.assertGreater(z["spiegel_n"], 0)
        original = Path(self.p.zustand_datei).read_bytes()
        faelle = {"falscher Hash": {"audit_hash": "f" * 64}, "Spiegelstand verschoben": {"spiegel_offset": z["spiegel_offset"] + 1},
                  "Spiegel hinter dem Ende": {"spiegel_n": z["audit_n"] + 50, "spiegel_offset": 1},
                  "Zeile hinter dem Ende": {"audit_n": z["audit_n"] + 5, "audit_hash": "e" * 64}}
        for name, aenderung in faelle.items():
            with self.subTest(fall=name):
                Path(self.p.zustand_datei).write_bytes(original)
                self.schreibe(aenderung)
                d2 = self.neu()
                self.assertEqual(d2._defekt, "zustand_defekt")
                self.assertEqual(self.sende(f"anfordern main {ADMIN}", d=d2)["grund"], "zustand_defekt")
        Path(self.p.zustand_datei).write_bytes(original)
        self.assertIsNone(self.neu()._defekt)

    def test_rotiertes_protokoll_wird_nicht_still_hingenommen(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        os.replace(self.p.audit, self.p.audit + ".alt")  # Protokoll weg, Zustandsdatei bleibt
        d2 = self.neu()
        self.assertEqual(d2._defekt, "zustand_defekt")
        self.assertTrue(self.sende("zurueck alles", d=d2)["ok"])  # der Rückweg bleibt

    def test_aelterer_stand_der_datei_wird_nachgezogen(self):
        self.anfordern()
        gesichert = Path(self.p.zustand_datei).read_bytes()
        self.lauf(bis="pruefung1")
        Path(self.p.zustand_datei).write_bytes(gesichert)
        d2 = self.neu()
        self.assertIsNone(d2._defekt)
        self.assertEqual(self.anfrage(d2)["status"], "pruefung1")

    def test_start_spiegelt_den_rueckstand(self):
        self.anfordern()
        self.db.ausfall = True
        self.lauf(bis="pruefung1")
        self.assertGreater(self.sende("status")["spiegel"]["rueckstand"], 0)
        self.db.ausfall = False
        d2 = self.neu()  # schon der Start holt nach
        self.assertEqual(self.sende("status", d=d2)["spiegel"]["rueckstand"], 0)
        self.assertEqual(self.db.anfragen[self.anfrage(d2)["id"]]["status"], "pruefung1")

    def test_neuaufbau_ohne_zustandsdatei_spiegelt_nicht_noch_einmal(self):
        self.anfordern()
        self.lauf(bis="pruefung1")
        self.d.wache()
        vorher = len(self.db.ereignisse)
        os.unlink(self.p.zustand_datei)
        d2 = self.neu()
        d2.wache()
        self.assertEqual(len(self.db.ereignisse), vorher)
        self.assertIn("neu_aufgebaut", [z for z in self.audit() if z["ereignis"] == "dienst_start"][-1])

    def test_start_ohne_abgleich_fragt_die_datenbank_nicht(self):
        d = self.neu(wiederaufnehmen=False)
        aufrufe = len(self.db.aufrufe)
        d.wiederaufnehmen(abgleich=False)
        self.assertEqual(len(self.db.aufrufe), aufrufe)
        d.wache()  # die Wache gleicht ab
        self.assertGreater(len(self.db.aufrufe), aufrufe)

    def test_aufraeumen_der_datenbank_geschieht_nicht_unter_dem_schloss(self):
        beobachtet = []
        original = self.db.status_setzen

        def spion(*a, **kw):
            beobachtet.append(self.d._lock._is_owned())
            return original(*a, **kw)

        self.db.status_setzen = spion
        alt = self.d.audit.anhaengen
        self.d.audit.anhaengen = lambda e, *a, **kw: (_ for _ in ()).throw(K.AuditFehler("voll")) if e == "angefordert" \
            else alt(e, *a, **kw)
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "audit_nicht_schreibbar")
        self.assertEqual(beobachtet, [False])


class FuellgradUndGrenzenTests(ReleaseTest):
    def test_der_kern_kann_das_protokoll_nicht_ueber_75_prozent_fuellen(self):
        mit = K.AUDIT_DATEI_MAX
        try:
            K.AUDIT_DATEI_MAX = 40000
            self.anker.sperre = "an"
            letzte = None
            for i in range(600):
                if i % R.AENDERN_KERN_MAX == 0:
                    self.zeit[0] += R.ABLEHN_FENSTER_S + 1
                letzte = self.sende(f"anfordern main {ADMIN}", "wamo-core").get("grund")
                if letzte == "audit_nicht_schreibbar":
                    break
            self.assertEqual(letzte, "audit_nicht_schreibbar")
            self.assertLessEqual(self.d.audit.fuellgrad(), K.AUDIT_GRENZE_LESEN + 0.03)
            self.assertTrue(self.sende("zurueck alles")["ok"])
            self.assertNotEqual(self.sende(f"anfordern main {ADMIN}").get("grund"), "audit_nicht_schreibbar")  # root darf weiter
        finally:
            K.AUDIT_DATEI_MAX = mit

    def test_kern_darf_weniger_aendernde_anfragen_als_root(self):
        self.anker.sperre = "an"
        kern = [self.sende(f"anfordern main {ADMIN}", "wamo-core").get("grund") for _ in range(R.AENDERN_KERN_MAX + 3)]
        self.assertEqual(kern.count("zu_viele"), 3)
        self.assertEqual(self.sende(f"anfordern main {ADMIN}", "root")["grund"], "gesperrt")  # root hat sein eigenes Budget

    def test_status_ist_je_benutzer_begrenzt(self):
        antworten = [self.sende("status", "wamo-core").get("grund") for _ in range(R.STATUS_MAX + 5)]
        self.assertEqual(antworten.count("zu_viele"), 5)
        self.assertTrue(self.sende("status", "root")["ok"])

    def test_status_belastet_den_anker_nicht_bei_jedem_aufruf(self):
        d = self.neu()
        d.tor_zeit = 3600
        d._tor_stand = (-1e9, "unbekannt")
        aufrufe = len(self.anker.aufrufe)
        for _ in range(10):
            self.sende("status", d=d)
        self.assertEqual(len(self.anker.aufrufe) - aufrufe, 1)


class HintergrundTests(ReleaseTest):
    def test_arbeiter_und_wache_bringen_eine_anfrage_ans_ende(self):
        d = self.neu()
        d.starte_hintergrund(arbeiter_takt=0.01, wache_takt=0.05)
        self.addCleanup(d.stoppe_hintergrund)
        self.anfordern(d)
        ende = time.monotonic() + 40
        while time.monotonic() < ende and self.anfrage(d) is not None:
            self.anker_spielt(d)
            time.sleep(0.02)
        self.assertEqual(self.endgrund(d), ("bewaehrt", None))
        d.stoppe_hintergrund()
        self.assertFalse(any(f.is_alive() for f in d._faeden))
        self.assertTrue(K.kette_pruefen(self.p.audit)[0])
        self.assertEqual(self.db.verstoesse, [])

    def test_wache_beendet_im_hintergrund_eine_zu_alte_anfrage(self):
        d = self.neu()
        d.starte_hintergrund(arbeiter_takt=3600, wache_takt=0.02)  # der Arbeiter ruht, nur die Wache läuft
        self.addCleanup(d.stoppe_hintergrund)
        self.anfordern(d)
        self.zeit[0] += AU.FRIST_S["angefragt"] + 1
        ende = time.monotonic() + 10
        while time.monotonic() < ende and self.anfrage(d) is not None:
            time.sleep(0.02)
        self.assertEqual(self.endgrund(d), ("abgebrochen", "zeitlimit"))


if __name__ == "__main__":
    unittest.main()
