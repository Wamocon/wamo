"""Tests der echten Stufen des Release-Dienstes (infra/sokrates2/release/stufen_echt.py und wamo_release_dienst.py, Schritt 3b).

Teil 1: Hilfen (Versionsnummern lesen, Art der Erhöhung). Teil 2: jede Stufe einzeln mit einem echten Git-Repository (lokale Quelle), einer Attrappe für die Prüfer-Sandbox und
einem falschen Anker. Teil 3: der ganze Weg einer Änderung von main durch den Zustandsautomaten des Dienstes bis bewaehrt (mit der Attrappe der Datenbank und dem Spiel von
Freigabe-Dienst und Anker aus test_release.py). Teil 4: der Start (Quelle fehlt, Aufbau scheitert). Nichts berührt Sokrates-2.
"""

import copy
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent
RELEASE = HIER.parent / "infra" / "sokrates2" / "release"
sys.path.insert(0, str(HIER))
sys.path.insert(0, str(RELEASE))
sys.path.insert(0, str(RELEASE / "pruefer"))

import release_anker as RA  # noqa: E402
import release_automat as AU  # noqa: E402
import release_stufen as ST  # noqa: E402
import stufen_echt as SE  # noqa: E402
import test_quelle as TQ  # noqa: E402
import test_release as TR  # noqa: E402
import quelle as Q  # noqa: E402
from release_stufen import Kontext  # noqa: E402

GIT = shutil.which("git")
KERN_V = "v0.1.0"

PAKETE = {
    "ui/package.json": '{"name": "wamo-ui", "version": "%s"}\n',
    "desktop/package.json": '{"name": "wamo-desktop", "version": "%s"}\n',
    "voice-core/pyproject.toml": '[project]\nname = "wamo-voice-core"\nversion = "%s"\nrequires-python = ">=3.12"\n\n[tool.x]\nversion = "9.9.9"\n',
    "host/pyproject.toml": '[project]\nname = "wamo-host"\nversion = "%s"\n',
}


def pakete(repo, version="0.1.0", **abweichend):
    for pfad, vorlage in PAKETE.items():
        TQ.schreibe(repo, pfad, abweichend.get(pfad, vorlage % version))


def kontext(daten=None, anfrage="0b9f2c1e-4a6d-4f0e-9a1b-2c3d4e5f6a7b", quelle="main", status="uebernommen", abgebrochen=False):
    ereignis = threading.Event()
    if abgebrochen:
        ereignis.set()
    return Kontext(anfrage=anfrage, quelle=quelle, profil="p", status=status, versuch=1, daten=dict(daten or {}),
                   frist=time.monotonic() + 600, abbruch=ereignis)


class StufenAnker:
    """Der Anker aus Sicht der Stufen: status und vorwaerts, mit dem, was die Probe vorgibt."""

    def __init__(self, kern_commit: str | None, version: str = KERN_V):
        self.kern_commit, self.version = kern_commit, version
        self.klasse = {"klasse": "normal", "art": "normal", "treffer_anzahl": 0, "treffer_sha": "e" * 64}
        self.antworten: dict[str, object] = {}
        self.aufrufe: list[tuple] = []
        self.status_ok = True

    def status(self):
        self.aufrufe.append(("status",))
        if not self.status_ok:
            raise RA.AnkerFehler("nicht_erreichbar")
        aktiv = {"version": self.version, "commit": self.kern_commit, "ziel": "a", "seit": "2026-11-02T12:00:00Z"} if self.kern_commit else None
        return RA.Antwort(True, None, {"ok": True, "komponenten": {"kern": {"aktiv": aktiv, "letzte_gute": aktiv}, "ui": {"aktiv": None, "letzte_gute": None}}})

    def vorwaerts(self, befehl, anfrage, trocken=False):
        self.aufrufe.append((befehl, anfrage, trocken))
        wert = self.antworten.get(befehl)
        if isinstance(wert, BaseException):
            raise wert
        if wert is not None:
            return wert
        if befehl == "klassifiziere":
            return RA.Antwort(True, None, {"ok": True, "ergebnis": dict(self.klasse)})
        if befehl == "bewaehren":
            return RA.Antwort(True, None, {"ok": True, "ergebnis": {"stand": "bewaehrt"}})
        return RA.Antwort(True, None, {"ok": True})

    def namen(self):
        return [a[0] for a in self.aufrufe]


def pruefen_attrappe(zeilen_fehler=(), aufrufe=None):
    """Ersetzt pruefer_client.pruefen_mit: schreibt das Archiv wirklich (git archive aus der Quelle) und meldet die Zeilen des Läufers (ohne schutzschicht und version_bytegleich)."""
    def f(schreiber, runde, sockpfad):
        puffer = io.BytesIO()
        schreiber(puffer)
        if aufrufe is not None:
            aufrufe.append((runde, len(puffer.getvalue())))
        namen = ("bauen", "tests", "typen_lint", "secret_scan", "fremdverweis", "abhaengigkeiten", "migrations_lint", "wirkungsflaechen") if runde in (1, 2) \
            else ("bauen", "tests", "secret_scan", "fremdverweis")
        return {"runde": runde, "pruefstand": {n: ("fehler" if n in zeilen_fehler else "ok") for n in namen}}
    return f


@unittest.skipUnless(GIT and os.name == "posix", "braucht git und Linux")
class Grundlage(unittest.TestCase):
    """Eine lokale Quelle (origin, Spiegel) mit zwei Ständen: der laufende (c1) und die Änderung (c2)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stufen-")
        self.addCleanup(self.tmp.cleanup)
        self.origin = TQ.neues_repo(self.tmp.name)
        pakete(self.origin)
        TQ.schreibe(self.origin, "voice-core/bot.py", "x = 1\n")
        self.c1 = TQ.commit(self.origin, "laufend")
        TQ.schreibe(self.origin, "voice-core/bot.py", "x = 2\n")
        self.c2 = TQ.commit(self.origin, "aenderung")
        self.quelle = Q.Quelle(Q.Spiegel(os.path.join(self.tmp.name, "spiegel.git"), self.origin, datei_erlaubt=True))
        self.quelle.aktualisieren()   # der Spiegel kennt c1 und c2 (der Dienst holt ihn in der Stufe uebernehmen)
        self.anker = StufenAnker(self.c1)
        self.pruef_aufrufe: list[tuple] = []
        self.stufen = SE.EchteStufen(self.quelle, self.anker, pruefen_mit=pruefen_attrappe(aufrufe=self.pruef_aufrufe), sockpfad="/nix")

    def weiter(self, **dateien):
        """Ein weiterer Commit auf origin/main; liefert seine Kennung."""
        for pfad, inhalt in dateien.items():
            TQ.schreibe(self.origin, pfad.replace("__", "/"), inhalt)
        return TQ.commit(self.origin, "weiter")


class Hilfen(unittest.TestCase):
    def test_version_aus_paketdateien(self):
        self.assertEqual(SE._version_aus("ui/package.json", b'{"version": "1.2.3"}'), "1.2.3")
        self.assertEqual(SE._version_aus("voice-core/pyproject.toml", b'[project]\nname = "x"\nversion = "0.4.5"  # Kommentar\n'), "0.4.5")
        self.assertEqual(SE._version_aus("host/pyproject.toml", b'[project]\r\nversion = "0.1.0"\r\n'), "0.1.0")
        # nur der Abschnitt [project] zählt, nicht ein anderer Abschnitt mit demselben Schlüssel
        self.assertEqual(SE._version_aus("a/pyproject.toml", b'[tool.x]\nversion = "9.9.9"\n[project]\nversion = "0.1.0"\n'), "0.1.0")
        self.assertIsNone(SE._version_aus("a/pyproject.toml", b'[tool.x]\nversion = "9.9.9"\n'))

    def test_ungueltige_versionen_gelten_als_fehlend(self):
        for roh in (b'{"version": "1.2"}', b'{"version": "v1.2.3"}', b'{"version": "01.2.3"}', b'{"version": 1}', b'{"version": null}', b"{}", b"[]", b"kein json", b"\xff\xfe",
                    b'{"version": "' + b"1" * 30 + b'.0.0"}'):
            self.assertIsNone(SE._version_aus("ui/package.json", roh), roh)
        for roh in (b'[project]\nversion = "1.2"\n', b"[project]\nversion = 1.2.3\n", b'[project]\nversion = "1.0.0-rc1"\n'):
            self.assertIsNone(SE._version_aus("a/pyproject.toml", roh), roh)

    def test_art_der_erhoehung(self):
        for alt, neu, erwartet in (("0.1.0", "0.1.1", "patch"), ("0.1.0", "0.2.0", "minor"), ("0.1.5", "1.0.0", "major"), ("0.1.0", "0.1.0", "patch"),
                                   (None, "3.2.1", "patch"), ("quark", "3.2.1", "patch")):
            self.assertEqual(SE.bump_art(alt, neu), erwartet, (alt, neu))

    def test_fehlercodes_sind_kurz_und_klein(self):
        for roh in ("Quelle: Fehler/\n", "x" * 100, "ÄÖÜ", ""):
            c = SE._code("anker_", roh)
            self.assertRegex(c, AU.GRUND_RE.pattern if len(c) > 1 else ".*")
            self.assertLessEqual(len(c), 40)


class Uebernehmen(Grundlage):
    def test_basis_ist_der_laufende_kern_und_c1_der_kopf(self):
        erg = self.stufen.uebernehmen(kontext())
        self.assertEqual((erg.art, erg.daten), ("ok", {"basis_commit": self.c1, "c1_commit": self.c2}))
        AU.daten_pruefen(erg.daten)   # genau so prüft der Dienst die Werte

    def test_nichts_zu_tun_wenn_der_kopf_schon_laeuft(self):
        self.anker.kern_commit = self.c2
        self.assertEqual(self.stufen.uebernehmen(kontext()).grund, "nichts_zu_tun")

    def test_basis_muss_ein_vorfahre_sein(self):
        andere = "f" * 40
        self.anker.kern_commit = andere
        self.assertEqual(self.stufen.uebernehmen(kontext()).grund, "basis_nicht_vorfahre")   # ein Commit, den die Quelle nicht kennt
        TQ.git(self.origin, "reset", "-q", "--hard", self.c1)
        TQ.schreibe(self.origin, "voice-core/bot.py", "x = 3\n")
        TQ.git(self.origin, "add", "-A")
        TQ.git(self.origin, "commit", "-q", "--amend", "-m", "umgeschrieben")
        self.anker.kern_commit = self.c2
        self.assertEqual(self.stufen.uebernehmen(kontext()).grund, "basis_nicht_vorfahre")   # die Geschichte wurde umgeschrieben

    def test_ohne_bekannten_kern_keine_basis(self):
        self.anker.kern_commit = None
        self.assertEqual(self.stufen.uebernehmen(kontext()).grund, "basis_unbekannt")
        self.anker.kern_commit = self.c1
        self.anker.status_ok = False
        self.assertEqual(self.stufen.uebernehmen(kontext()).grund, "basis_unbekannt")

    def test_nur_die_quelle_main_und_nie_nach_abbruch(self):
        self.assertEqual(self.stufen.uebernehmen(kontext(quelle="selbst")).grund, "stufe_nicht_verfuegbar")
        self.assertEqual(self.stufen.uebernehmen(kontext(abgebrochen=True)).grund, "abgebrochen")
        self.assertEqual(self.anker.namen(), [])   # nicht einmal der Anker wurde gefragt


class Pruefen(Grundlage):
    DATEN = None

    def daten(self, **mehr):
        return {"basis_commit": self.c1, "c1_commit": self.c2, **mehr}

    def test_runde_1_ergaenzt_schutzschicht_und_schickt_das_archiv(self):
        erg = self.stufen.pruefen(kontext(self.daten(), status="pruefung1"), 1)
        self.assertEqual(erg.art, "ok")
        self.assertEqual(erg.daten["pruefstand"]["schutzschicht"], "ok")
        self.assertTrue(all(erg.daten["pruefstand"][n] == "ok" for n in AU.PRUEFSTAND_PFLICHT[1]), erg.daten)
        self.assertEqual([r for r, _ in self.pruef_aufrufe], [1])
        self.assertGreater(self.pruef_aufrufe[0][1], 0)
        self.assertEqual(self.anker.namen(), ["klassifiziere"])
        AU.daten_pruefen(erg.daten)

    def test_gesperrt_ist_ein_fehler_der_zeile_nicht_der_stufe(self):
        self.anker.klasse = {"klasse": "gesperrt", "art": "sonder", "treffer_anzahl": 1, "treffer_sha": "a" * 64}
        erg = self.stufen.pruefen(kontext(self.daten(), status="pruefung1"), 1)
        self.assertEqual(erg.daten["pruefstand"]["schutzschicht"], "fehler")

    def test_sonder_ist_in_den_runden_1_und_2_in_ordnung(self):
        self.anker.klasse = {"klasse": "sonder", "art": "sonder", "treffer_anzahl": 2, "treffer_sha": "a" * 64}
        self.assertEqual(self.stufen.pruefen(kontext(self.daten(), status="pruefung1"), 1).daten["pruefstand"]["schutzschicht"], "ok")

    def test_fehlschlag_der_sandbox_geht_ohne_anker_zurueck(self):
        stufen = SE.EchteStufen(self.quelle, self.anker, pruefen_mit=pruefen_attrappe(zeilen_fehler=("tests",)))
        erg = stufen.pruefen(kontext(self.daten(), status="pruefung1"), 1)
        self.assertEqual(erg.daten["pruefstand"]["tests"], "fehler")   # der Dienst beendet die Anfrage am Tor, die Stufe meldet nur ehrlich

    def test_antwort_des_ankers_muss_genau_die_form_haben(self):
        schlecht = [None, {}, {"klasse": "normal"}, {"klasse": "egal", "art": "normal", "treffer_anzahl": 0, "treffer_sha": "e" * 64},
                    {"klasse": "normal", "art": "normal", "treffer_anzahl": True, "treffer_sha": "e" * 64},
                    {"klasse": "normal", "art": "normal", "treffer_anzahl": 201, "treffer_sha": "e" * 64},
                    {"klasse": "normal", "art": "normal", "treffer_anzahl": 0, "treffer_sha": "kurz"},
                    {"klasse": "normal", "art": "gesperrt", "treffer_anzahl": 0, "treffer_sha": "e" * 64}]
        for k in schlecht:
            self.anker.antworten["klassifiziere"] = RA.Antwort(True, None, {"ok": True, "ergebnis": k})
            erg = self.stufen.pruefen(kontext(self.daten(), status="pruefung1"), 1)
            self.assertEqual((erg.art, erg.grund), ("fehler", "anker_antwort_form"), k)

    def test_anker_nicht_erreichbar_oder_abgelehnt(self):
        self.anker.antworten["klassifiziere"] = RA.AnkerFehler("nicht_erreichbar")
        self.assertEqual(self.stufen.pruefen(kontext(self.daten(), status="pruefung1"), 1).grund, "anker_nicht_erreichbar")
        self.anker.antworten["klassifiziere"] = RA.Antwort(False, "vorwaerts_fehlgeschlagen", {"ok": False, "detail": "status_falsch angefragt"})
        self.assertEqual(self.stufen.pruefen(kontext(self.daten(), status="pruefung1"), 1).grund, "anker_vorwaerts_fehlgeschlagen")

    def test_runde_3_prueft_v_und_liefert_klasse_und_treffer(self):
        d = self.daten(v_commit=self.c2, tree_id=self.quelle.spiegel.baum(self.c2), version_final="0.1.0")
        self.stufen.quelle.aktualisieren()
        erg = self.stufen.pruefen(kontext(d, status="pruefung3"), 3)
        self.assertEqual(erg.art, "ok", erg)
        self.assertTrue(all(erg.daten["pruefstand"][n] == "ok" for n in AU.PRUEFSTAND_PFLICHT[3]), erg.daten)
        self.assertEqual((erg.daten["klasse"], erg.daten["treffer_anzahl"]), ("normal", 0))
        self.assertNotIn("treffer_sha", erg.daten)   # ohne Treffer kein Prüfwert
        AU.daten_pruefen(erg.daten)

    def test_runde_3_mit_treffern_nennt_die_pruefsumme(self):
        self.anker.klasse = {"klasse": "sonder", "art": "sonder", "treffer_anzahl": 3, "treffer_sha": "b" * 64}
        d = self.daten(v_commit=self.c2, tree_id=self.quelle.spiegel.baum(self.c2), version_final="0.1.0")
        self.stufen.quelle.aktualisieren()
        erg = self.stufen.pruefen(kontext(d, status="pruefung3"), 3)
        self.assertEqual((erg.daten["klasse"], erg.daten["treffer_anzahl"], erg.daten["treffer_sha"]), ("sonder", 3, "b" * 64))
        AU.daten_pruefen(erg.daten)

    def test_runde_3_version_bytegleich_faellt_bei_jeder_abweichung(self):
        self.stufen.quelle.aktualisieren()
        for d in ({"version_final": "0.2.0"}, {"version_final": "0.1.0", "v_commit": self.weiter(**{"desktop__package.json": '{"version": "0.1.1"}'})}):
            daten = self.daten(**d)
            daten.setdefault("v_commit", self.c2)
            daten["tree_id"] = self.quelle.spiegel.baum(daten["v_commit"]) if self.stufen.quelle.aktualisieren() else ""
            erg = self.stufen.pruefen(kontext(daten, status="pruefung3"), 3)
            self.assertEqual(erg.daten["pruefstand"]["version_bytegleich"], "fehler", d)

    def test_runde_3_ohne_v_commit_ist_ein_fehler(self):
        self.assertEqual(self.stufen.pruefen(kontext(self.daten(version_final="0.1.0"), status="pruefung3"), 3).grund, "commit_fehlt")

    def test_unbekannte_runde(self):
        self.assertEqual(self.stufen.pruefen(kontext(self.daten()), 4).grund, "stufe_nicht_verfuegbar")


class VersionWaehlen(Grundlage):
    def daten(self):
        return {"basis_commit": self.c1, "c1_commit": self.c2}

    def test_version_der_paketdateien_ist_vorschlag_und_wahl(self):
        self.stufen.quelle.aktualisieren()
        erg = self.stufen.version_waehlen(kontext(self.daten(), status="versionswahl"))
        self.assertEqual(erg.art, "ok", erg)
        self.assertEqual(erg.daten, {"bump": "patch", "version_vorschlag": "0.1.0", "version_final": "0.1.0", "v_commit": self.c2,
                                     "tree_id": self.quelle.spiegel.baum(self.c2)})
        AU.daten_pruefen(erg.daten)

    def test_erhoehte_version_im_pull_request_wird_uebernommen_und_eingestuft(self):
        pakete(self.origin, "0.2.0")
        c3 = TQ.commit(self.origin, "version")
        self.stufen.quelle.aktualisieren()
        erg = self.stufen.version_waehlen(kontext({"basis_commit": self.c1, "c1_commit": c3}, status="versionswahl"))
        self.assertEqual((erg.daten["version_final"], erg.daten["bump"]), ("0.2.0", "minor"))
        pakete(self.origin, "1.0.0")
        c4 = TQ.commit(self.origin, "gross")
        self.stufen.quelle.aktualisieren()
        self.assertEqual(self.stufen.version_waehlen(kontext({"basis_commit": self.c1, "c1_commit": c4}, status="versionswahl")).daten["bump"], "major")

    def test_uneinheitliche_versionen_stoppen(self):
        c3 = self.weiter(**{"host__pyproject.toml": '[project]\nname = "wamo-host"\nversion = "0.1.1"\n'})
        self.stufen.quelle.aktualisieren()
        self.assertEqual(self.stufen.version_waehlen(kontext({"basis_commit": self.c1, "c1_commit": c3}, status="versionswahl")).grund, "versionen_uneinheitlich")

    def test_fehlende_pflichtdatei_und_ungueltige_nummer(self):
        os.remove(os.path.join(self.origin, "ui", "package.json"))
        c3 = TQ.commit(self.origin, "ohne ui")
        self.stufen.quelle.aktualisieren()
        self.assertEqual(self.stufen.version_waehlen(kontext({"basis_commit": self.c1, "c1_commit": c3}, status="versionswahl")).grund, "quelle_versionsdatei_fehlt")
        TQ.schreibe(self.origin, "ui/package.json", '{"version": "kaputt"}')
        c4 = TQ.commit(self.origin, "kaputt")
        self.stufen.quelle.aktualisieren()
        self.assertEqual(self.stufen.version_waehlen(kontext({"basis_commit": self.c1, "c1_commit": c4}, status="versionswahl")).grund, "quelle_version_ungueltig")

    def test_optionale_dateien_duerfen_fehlen(self):
        os.remove(os.path.join(self.origin, "desktop", "package.json"))
        os.remove(os.path.join(self.origin, "host", "pyproject.toml"))
        c3 = TQ.commit(self.origin, "schlank")
        self.stufen.quelle.aktualisieren()
        self.assertEqual(self.stufen.version_waehlen(kontext({"basis_commit": self.c1, "c1_commit": c3}, status="versionswahl")).art, "ok")

    def test_nur_main_und_ein_gueltiger_commit(self):
        self.assertEqual(self.stufen.version_waehlen(kontext(self.daten(), quelle="selbst")).grund, "stufe_nicht_verfuegbar")
        self.assertEqual(self.stufen.version_waehlen(kontext({"basis_commit": self.c1})).grund, "commit_fehlt")


class Veroeffentlichen(Grundlage):
    def test_v_liegt_in_main_und_der_tree_stimmt(self):
        self.stufen.quelle.aktualisieren()
        d = {"v_commit": self.c2, "tree_id": self.quelle.spiegel.baum(self.c2)}
        self.assertEqual(self.stufen.veroeffentlichen(kontext(d, status="freigegeben")).art, "ok")
        self.weiter(**{"docs__x.md": "x"})   # main darf weitergehen: V bleibt Vorfahre
        self.assertEqual(self.stufen.veroeffentlichen(kontext(d, status="freigegeben")).art, "ok")

    def test_abweichender_tree_oder_umgeschriebene_geschichte(self):
        self.stufen.quelle.aktualisieren()
        d = {"v_commit": self.c2, "tree_id": "0" * 40}
        self.assertEqual(self.stufen.veroeffentlichen(kontext(d, status="freigegeben")).grund, "tree_abweichung")
        d["tree_id"] = self.quelle.spiegel.baum(self.c2)
        TQ.git(self.origin, "reset", "-q", "--hard", self.c1)
        TQ.schreibe(self.origin, "voice-core/bot.py", "x = 9\n")
        TQ.git(self.origin, "add", "-A")
        TQ.git(self.origin, "commit", "-q", "--amend", "-m", "umgeschrieben")
        self.assertEqual(self.stufen.veroeffentlichen(kontext(d, status="freigegeben")).grund, "nicht_in_main")

    def test_ohne_daten_und_nach_abbruch(self):
        self.assertEqual(self.stufen.veroeffentlichen(kontext({}, status="freigegeben")).grund, "daten_fehlen")
        d = {"v_commit": self.c2, "tree_id": "0" * 40}
        self.assertEqual(self.stufen.veroeffentlichen(kontext(d, status="freigegeben", abgebrochen=True)).grund, "abgebrochen")


class BereitstellenUmschalten(Grundlage):
    def test_die_stufe_gibt_dem_anker_nur_die_kennung(self):
        k = kontext(status="veroeffentlicht")
        self.assertEqual(self.stufen.bereitstellen(k).art, "ok")
        self.assertEqual(self.stufen.umschalten(kontext(status="bereitgestellt")).art, "ok")
        self.assertEqual(self.anker.aufrufe, [("bereitstellen", k.anfrage, False), ("umschalten", k.anfrage, False)])

    def test_ein_frueherer_lauf_war_schon_weiter(self):
        weiter = RA.Antwort(False, "vorwaerts_fehlgeschlagen", {"ok": False, "detail": "status_falsch bereitgestellt statt veroeffentlicht"})
        self.anker.antworten["bereitstellen"] = weiter
        self.assertEqual(self.stufen.bereitstellen(kontext(status="veroeffentlicht")).art, "ok")
        self.anker.antworten["umschalten"] = RA.Antwort(False, "vorwaerts_fehlgeschlagen", {"ok": False, "detail": "status_falsch umgeschaltet statt bereitgestellt"})
        self.assertEqual(self.stufen.umschalten(kontext(status="bereitgestellt")).art, "ok")
        # nach hinten gilt das nicht: ein Anker, der noch bei angefragt steht, ist ein Fehler
        self.anker.antworten["bereitstellen"] = RA.Antwort(False, "vorwaerts_fehlgeschlagen", {"ok": False, "detail": "status_falsch wartet_freigabe statt veroeffentlicht"})
        self.assertEqual(self.stufen.bereitstellen(kontext(status="veroeffentlicht")).art, "fehler")

    def test_fehler_des_ankers_kommen_als_kurzer_code(self):
        fall = [(RA.AnkerFehler("zeitlimit"), "anker_zeitlimit"),
                (RA.Antwort(False, "gesperrt", {"ok": False}), "anker_gesperrt"),
                (RA.Antwort(False, "vorwaerts_fehlgeschlagen", {"ok": False, "detail": "freigabe_ungueltig x"}), "anker_freigabe_ungueltig"),
                (RA.Antwort(False, "vorwaerts_fehlgeschlagen", {"ok": False, "detail": "migration_fehlgeschlagen 0017_x"}), "anker_migration_fehlgeschlagen")]
        for antwort, code in fall:
            self.anker.antworten["bereitstellen"] = antwort
            erg = self.stufen.bereitstellen(kontext(status="veroeffentlicht"))
            self.assertEqual((erg.art, erg.grund), ("fehler", code))
            self.assertRegex(erg.grund, AU.GRUND_RE.pattern)

    def test_nach_abbruch_wird_der_anker_nicht_gerufen(self):
        self.assertEqual(self.stufen.bereitstellen(kontext(status="veroeffentlicht", abgebrochen=True)).grund, "abgebrochen")
        self.assertEqual(self.anker.aufrufe, [])


class WegGrundlage(TR.ReleaseTest):
    """Der Zustandsautomat des Dienstes mit den echten Stufen, einer echten Quelle und einem falschen Anker (ohne eigene Tests)."""

    def setUp(self):
        super().setUp()
        self.tmp2 = tempfile.TemporaryDirectory(prefix="weg-")
        self.addCleanup(self.tmp2.cleanup)
        self.origin = TQ.neues_repo(self.tmp2.name)
        pakete(self.origin)
        TQ.schreibe(self.origin, "voice-core/bot.py", "x = 1\n")
        self.c1 = TQ.commit(self.origin, "laufend")
        TQ.schreibe(self.origin, "voice-core/bot.py", "x = 2\n")
        self.c2 = TQ.commit(self.origin, "aenderung")
        quelle = Q.Quelle(Q.Spiegel(os.path.join(self.tmp2.name, "spiegel.git"), self.origin, datei_erlaubt=True))
        self.stufen_anker = StufenAnker(self.c1)
        self.stufen = SE.EchteStufen(quelle, self.stufen_anker, pruefen_mit=pruefen_attrappe())
        self.d = self.neu()


class GanzerWeg(WegGrundlage):
    def test_von_main_bis_bewaehrt(self):
        self.anfordern(self.d)
        self.lauf(self.d)
        self.assertEqual(self.endgrund(self.d), ("bewaehrt", None), self.audit()[-3:])
        wege = self.uebergaenge()
        self.assertEqual([n for _, n in wege], list(AU.WEG["main"][1:]))
        befehle = [a[0] for a in self.stufen_anker.aufrufe if a[0] != "status"]
        self.assertEqual(befehle, ["klassifiziere", "klassifiziere", "bereitstellen", "umschalten", "bewaehren"])   # Runde 1, Runde 3, die Anker-Schritte, die Bewährung

    def test_sonder_ohne_zweiten_admin_bleibt_gesperrt(self):
        self.stufen_anker.klasse = {"klasse": "sonder", "art": "sonder", "treffer_anzahl": 2, "treffer_sha": "c" * 64}
        self.anfordern(self.d)
        self.lauf(self.d)
        self.assertEqual(self.endgrund(self.d), ("abgebrochen", "zweiter_admin_noetig"))

    def test_gesperrt_endet_vor_der_freigabe(self):
        self.stufen_anker.klasse = {"klasse": "gesperrt", "art": "sonder", "treffer_anzahl": 1, "treffer_sha": "d" * 64}
        self.anfordern(self.d)
        self.lauf(self.d)
        status, grund = self.endgrund(self.d)
        self.assertEqual(status, "abgebrochen")
        self.assertIn(grund, ("pruefung_fehlgeschlagen", "schutzschicht_gesperrt"))
        self.assertNotIn("bereitstellen", [a[0] for a in self.stufen_anker.aufrufe])

    def test_nichts_zu_tun_beendet_ehrlich(self):
        self.stufen_anker.kern_commit = self.c2
        self.anfordern(self.d)
        self.lauf(self.d)
        self.assertEqual(self.endgrund(self.d), ("abgebrochen", "stufe_fehler"))

    def test_fehler_der_migration_beendet_vor_dem_umschalten(self):
        self.stufen_anker.antworten["bereitstellen"] = RA.Antwort(False, "vorwaerts_fehlgeschlagen", {"ok": False, "detail": "migration_fehlgeschlagen 0017_x"})
        self.anfordern(self.d)
        self.lauf(self.d)
        self.assertEqual(self.endgrund(self.d)[0], "abgebrochen")
        self.assertNotIn("umschalten", [a[0] for a in self.stufen_anker.aufrufe])


class Start(unittest.TestCase):
    def test_ohne_quelle_sind_die_stufen_nicht_verfuegbar(self):
        import wamo_release_dienst as D
        from unittest import mock
        with mock.patch.object(D, "QUELLE_DATEI", "/gibt/es/nicht"), mock.patch("sys.stderr", new=io.StringIO()) as err:
            stufen = D.stufen_bauen()
        self.assertIsInstance(stufen, ST.NichtVerfuegbar)
        self.assertIn("Keine Quelle", err.getvalue())

    def test_kaputte_quelle_ist_kein_absturz(self):
        import wamo_release_dienst as D
        from unittest import mock
        with tempfile.TemporaryDirectory() as t:
            datei = os.path.join(t, "quelle.env")
            Path(datei).write_text("WAMO_QUELLE_URL=ftp://nein\n")
            with mock.patch.object(D, "QUELLE_DATEI", datei), mock.patch("sys.stderr", new=io.StringIO()) as err:
                stufen = D.stufen_bauen()
        self.assertIsInstance(stufen, ST.NichtVerfuegbar)
        self.assertNotIn("ftp", err.getvalue())   # nur die Art des Fehlers, nie der Inhalt

    def test_lokale_quelle_ergibt_echte_stufen(self):
        import wamo_release_dienst as D
        from unittest import mock
        with tempfile.TemporaryDirectory() as t:
            datei = os.path.join(t, "quelle.env")
            Path(datei).write_text("WAMO_QUELLE_URL=/var/lib/wamo-quelle/wamo.git\n")
            with mock.patch.object(D, "QUELLE_DATEI", datei), mock.patch.dict("sys.modules"):
                try:
                    stufen = D.stufen_bauen()
                except Exception as e:   # pruefer_client fehlt im Testordner nicht: er liegt in release/pruefer
                    self.fail(repr(e))
        self.assertIsInstance(stufen, SE.EchteStufen)

    def test_andere_aufrufe_gehen_an_den_kern(self):
        import wamo_release_dienst as D
        from unittest import mock
        with mock.patch("wamo_release.main", return_value=7) as m:
            self.assertEqual(D.main(["x", "status"]), 7)
        m.assert_called_once_with(["x", "status"])

    def test_der_kern_importiert_die_echten_stufen_nie(self):
        t = (RELEASE / "wamo_release.py").read_text(encoding="utf-8")
        for name in ("stufen_echt", "stufen_quelle", "wamo_release_dienst", "import quelle"):
            self.assertNotIn(name, t.replace("wamo_release_dienst.py setzt", ""), name)


B_COMMIT, C1_COMMIT, ANDERER = "a" * 40, "b" * 40, "c" * 40


def laeufer_ergebnis(runde=1, wirkung=True):
    """Antwort des Läufers (pruefer_lauf.py) mit Wirkungsflächen; Runde 3 hat sie nicht."""
    erg = {"runde": runde, "pruefstand": {"bauen": "ok", "tests": "ok", "secret_scan": "ok", "fremdverweis": "ok"}, "details": {}, "sonder": False, "neue_pakete": [],
           "geaenderte_dateien": 4, "windows_pruefung_noetig": False, "treffer": {}}
    if wirkung:
        erg["pruefstand"]["wirkungsflaechen"] = "ok"
        erg["details"]["wirkungsflaechen"] = {"treffer_anzahl": 3, "fehler": ""}
        erg["sonder"], erg["neue_pakete"], erg["windows_pruefung_noetig"] = True, [["rich", "13.0.0"]], True
        erg["treffer"] = {"wirkungsflaechen": [{"pfad": "host/a.py", "zeile": 3, "art": "netzziel", "detail": "example.org"},
                                               {"pfad": "host/b.py", "zeile": 9, "art": "geheimnisname", "detail": "core.env"}],
                          "secret_scan": [{"pfad": "x/geheim.py", "zeile": 1, "art": "schluessel", "detail": ""}]}
    return erg


class Wirkungsflaechen(unittest.TestCase):
    """Die Funde der Wirkungsflächen für die Karte (stufen_quelle.py): laufen ohne Git und ohne Sandbox, mit einer Attrappe des Läufers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="anzeige-")
        self.addCleanup(self.tmp.cleanup)
        self.ordner = os.path.join(self.tmp.name, "anzeige")

    def stufen(self, ergebnisse=None, ordner="standard"):
        import stufen_quelle as SQ
        antworten = list(ergebnisse or [laeufer_ergebnis(1)])

        def pruefen_mit(schreiber, runde, sockpfad):
            return antworten.pop(0) if len(antworten) > 1 else antworten[0]
        return SQ.QuellenStufen(object(), pruefen_mit, "/x", self.ordner if ordner == "standard" else ordner)

    def lauf(self, stufen, runde=1, neu=C1_COMMIT):
        return stufen.pruefen(kontext({"basis_commit": B_COMMIT, "c1_commit": C1_COMMIT, "v_commit": neu}, status="pruefung%d" % runde), runde)

    def test_wirkungsflaechen_werden_fuer_die_karte_gemerkt(self):
        s = self.stufen()
        erg = self.lauf(s)
        self.assertEqual(erg.art, "ok")
        self.assertEqual(set(erg.daten), {"pruefstand"})   # die Hashkette und der Automat sehen davon nichts
        f = s.funde_fuer(B_COMMIT, C1_COMMIT)
        self.assertEqual((f["treffer_anzahl"], f["sonder"], f["neue_pakete"], f["geaenderte_dateien"], f["windows_pruefung_noetig"]), (3, True, 1, 4, True))
        self.assertEqual([t["pfad"] for t in f["treffer"]], ["host/a.py", "host/b.py"])   # nur die Funde der Wirkungsflächen, nicht die des Geheimnis-Scans
        self.assertEqual(set(f["treffer"][0]), {"pfad", "zeile", "art", "detail"})
        f["treffer"].clear()
        self.assertEqual(len(s.funde_fuer(B_COMMIT, C1_COMMIT)["treffer"]), 2)   # der Aufrufer ändert nichts am Gemerkten
        self.assertEqual(os.listdir(self.ordner), [f"{B_COMMIT}-{C1_COMMIT}.json"])
        self.assertIsNone(s.funde_fuer(B_COMMIT, ANDERER))
        self.assertIsNone(s.funde_fuer("kein sha", C1_COMMIT))
        self.assertIsNone(s.funde_fuer(None, None))

    def test_ohne_ordner_bleibt_es_im_speicher(self):
        s = self.stufen(ordner=None)
        self.lauf(s)
        self.assertEqual(s.funde_fuer(B_COMMIT, C1_COMMIT)["treffer_anzahl"], 3)
        self.assertFalse(os.path.exists(self.ordner))

    def test_anzeige_nach_neustart_aus_datei(self):
        self.lauf(self.stufen())
        neu = self.stufen()   # ein frischer Dienst: leerer Speicher, dieselbe Datei
        f = neu.funde_fuer(B_COMMIT, C1_COMMIT)
        self.assertEqual((f["treffer_anzahl"], f["sonder"], len(f["treffer"])), (3, True, 2))
        d = json.loads(Path(self.ordner, f"{B_COMMIT}-{C1_COMMIT}.json").read_text(encoding="ascii"))
        self.assertEqual(set(d), {"v", "basis", "neu", "treffer", "treffer_anzahl", "sonder", "neue_pakete", "geaenderte_dateien", "windows_pruefung_noetig"})
        if os.name == "posix":
            self.assertEqual(os.stat(Path(self.ordner, f"{B_COMMIT}-{C1_COMMIT}.json")).st_mode & 0o777, 0o600)
        self.assertEqual([n for n in os.listdir(self.ordner) if n.endswith(".tmp")], [])   # nichts Halbes bleibt liegen

    def test_anzeige_nur_wenn_v_gleich_c1(self):
        s = self.stufen([laeufer_ergebnis(1), laeufer_ergebnis(3, wirkung=False)])
        self.lauf(s, 1)
        self.assertIsNotNone(s.funde_fuer(B_COMMIT, C1_COMMIT))   # V gleich C1: die Funde von Runde 1 gelten auch für V
        self.assertIsNone(s.funde_fuer(B_COMMIT, ANDERER))   # V ungleich C1: unbekannt
        self.lauf(s, 3, neu=C1_COMMIT)   # Runde 3 hat keine Wirkungsflächen und überschreibt nichts
        self.assertEqual(s.funde_fuer(B_COMMIT, C1_COMMIT)["treffer_anzahl"], 3)
        self.lauf(s, 3, neu=ANDERER)
        self.assertIsNone(s.funde_fuer(B_COMMIT, ANDERER))   # auch ein Lauf auf einem anderen V erfindet keine Funde

    def test_fremde_oder_zu_grosse_datei_wird_verworfen(self):
        import stufen_quelle as SQ
        self.lauf(self.stufen())
        gut = json.loads(Path(self.ordner, f"{B_COMMIT}-{C1_COMMIT}.json").read_text(encoding="ascii"))
        datei = Path(self.ordner, f"{B_COMMIT}-{ANDERER}.json")
        faelle = {
            "zu gross": json.dumps({**gut, "neu": ANDERER, "treffer": [{"pfad": "x" * 300, "zeile": 1, "art": "a", "detail": "d"}] * 60}).replace('"v"', ' ' * SQ.ANZEIGE_DATEI_MAX + '"v"'),
            "anderes Paar": json.dumps({**gut, "neu": C1_COMMIT}),
            "zusätzlicher Schlüssel": json.dumps({**gut, "neu": ANDERER, "befehl": "x"}),
            "fehlender Schlüssel": json.dumps({k: v for k, v in {**gut, "neu": ANDERER}.items() if k != "sonder"}),
            "falscher Typ": json.dumps({**gut, "neu": ANDERER, "treffer_anzahl": "3"}),
            "Zahl zu groß": json.dumps({**gut, "neu": ANDERER, "treffer_anzahl": 10**9}),
            "Wahrheitswert als Zahl": json.dumps({**gut, "neu": ANDERER, "neue_pakete": True}),
            "falsche Fassung": json.dumps({**gut, "neu": ANDERER, "v": 2}),
            "Treffer mit Zusatz": json.dumps({**gut, "neu": ANDERER, "treffer": [{"pfad": "a", "zeile": 1, "art": "a", "detail": "d", "x": 1}]}),
            "zu viele Treffer": json.dumps({**gut, "neu": ANDERER, "treffer": [{"pfad": "a", "zeile": 1, "art": "a", "detail": "d"}] * 51}),
            "kein Objekt": "[1, 2, 3]",
            "kein JSON": "{kaputt",
            "leer": "",
            "tief verschachtelt": "[" * 5000,
        }
        for name, inhalt in faelle.items():
            datei.write_text(inhalt, encoding="ascii")
            s = self.stufen()
            self.assertIsNone(s.funde_fuer(B_COMMIT, ANDERER), name)
        datei.write_bytes(b'{"v": 1, "x": "\xff\xfe"}')   # kein ASCII
        self.assertIsNone(self.stufen().funde_fuer(B_COMMIT, ANDERER))
        datei.unlink()
        datei.mkdir()   # ein Ordner an der Stelle der Datei
        self.assertIsNone(self.stufen().funde_fuer(B_COMMIT, ANDERER))
        self.assertIsNotNone(self.stufen().funde_fuer(B_COMMIT, C1_COMMIT))   # die gute Datei daneben bleibt gültig

    def test_fremde_form_aus_dem_laeufer_wird_nicht_gemerkt(self):
        import stufen_quelle as SQ
        for roh in (None, "text", [], {"pruefstand": "x"}, {"pruefstand": {"bauen": "ok"}}, {"pruefstand": {"wirkungsflaechen": "ok"}, "treffer": "x", "neue_pakete": "x"}):
            f = SQ.funde_aus_lauf(roh)
            self.assertTrue(f is None or f["treffer"] == [] and f["neue_pakete"] == 0, roh)
        gross = laeufer_ergebnis(1)
        gross["treffer"]["wirkungsflaechen"] = [{"pfad": "p" * 1000, "zeile": -5, "art": "a" * 100, "detail": "d" * 1000}] * 500
        gross["neue_pakete"] = [["p", "1"]] * 900
        gross["details"]["wirkungsflaechen"]["treffer_anzahl"] = 10**6
        f = SQ.funde_aus_lauf(gross)
        self.assertEqual((len(f["treffer"]), len(f["treffer"][0]["pfad"]), f["treffer"][0]["zeile"], f["neue_pakete"], f["treffer_anzahl"]), (50, 300, 0, 200, 50))

    def test_alte_dateien_werden_aufgeraeumt(self):
        import stufen_quelle as SQ
        s = self.stufen()
        os.makedirs(self.ordner)
        for i in range(SQ.ANZEIGE_AUFBEWAHREN + 6):
            p = Path(self.ordner, f"{i:040x}-{C1_COMMIT}.json")
            p.write_text("{}")
            os.utime(p, (1000 + i, 1000 + i))
        Path(self.ordner, "fremd.txt").write_text("bleibt")
        self.lauf(s)
        namen = os.listdir(self.ordner)
        self.assertEqual(len([n for n in namen if n.endswith(".json")]), SQ.ANZEIGE_AUFBEWAHREN)
        self.assertIn("fremd.txt", namen)   # nur Dateien unseres Namensmusters werden angefasst
        self.assertIn(f"{B_COMMIT}-{C1_COMMIT}.json", namen)


class Quelltext(unittest.TestCase):
    def test_keine_gedankenstriche_keine_shell_kein_netz(self):
        for n in ("stufen_echt.py", "wamo_release_dienst.py"):
            t = (RELEASE / n).read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), t, n)
            self.assertNotIn(chr(0x2013), t, n)
            for verboten in ("shell=True", "os.system", "subprocess", "urllib", "import socket", "AF_INET", "eval(", "exec(", "pickle", "os.environ"):
                self.assertNotIn(verboten, t, (n, verboten))


if __name__ == "__main__":
    unittest.main()
