"""Tests des Läufers der Prüfstrecke (infra/sokrates2/release/pruefer/pruefer_lauf.py, Schritt 1.2b, Konzept D.3).

Die Werkzeuge (uv, npm, ruff ...) laufen hier nie: Der Ausführer ist ersetzt. Geprüft werden der Baumvergleich, die Reihenfolge und Abhängigkeit
der Stufen, die Form des Ergebnisses (gegen release_automat, das der Release-Dienst später anwendet) und der Plan der Befehle gegen das
echte Repo (Ordner, Skripte, Sperrdateien).

Ausführen:  uv run python -m pytest -q test_pruefer_lauf.py   (in voice-core/)
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent
REPO = HIER.parent
INFRA = REPO / "infra" / "sokrates2"
sys.path.insert(0, str(INFRA / "release" / "pruefer"))
sys.path.insert(0, str(INFRA / "release"))

import pruefer_lauf as L  # noqa: E402
import release_automat as AU  # noqa: E402

MUSTER = ["fremdname"]


class Baeume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pruefer-lauf-")
        self.addCleanup(self.tmp.cleanup)
        self.basis = os.path.join(self.tmp.name, "basis")
        self.neu = os.path.join(self.tmp.name, "neu")
        for d in (self.basis, self.neu):
            os.makedirs(d)

    def schreibe(self, wurzel, pfad, inhalt):
        p = Path(wurzel, pfad)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(inhalt if isinstance(inhalt, bytes) else inhalt.encode())

    def beide(self, pfad, inhalt):
        self.schreibe(self.basis, pfad, inhalt)
        self.schreibe(self.neu, pfad, inhalt)


class FakeAusfuehrer:
    """Zeichnet jeden Aufruf auf und liefert je Befehl, was die Probe vorgibt (sonst Exit 0)."""

    def __init__(self, ergebnisse=None, panne=False):
        self.aufrufe: list[tuple[str, tuple[str, ...], dict]] = []
        self.ergebnisse, self.panne = ergebnisse or {}, panne

    def __call__(self, befehl, wurzel, env):
        self.aufrufe.append((befehl.ordner, befehl.argv, env))
        if self.panne:
            raise RuntimeError("Panne mit geheimem Text")
        return self.ergebnisse.get((befehl.ordner, befehl.argv[0] + " " + " ".join(befehl.argv[1:2])), L.Ausgang(0))


class BaumvergleichTests(Baeume):
    def test_neue_geaenderte_geloeschte_und_unveraenderte(self):
        self.beide("gleich.py", "x = 1\n")
        self.schreibe(self.basis, "alt.py", "a\n")
        self.schreibe(self.basis, "geaendert.py", "a\nb\n")
        self.schreibe(self.neu, "geaendert.py", "a\nc\n")
        self.schreibe(self.neu, "neu.py", "n\n")
        baum = L.vergleiche_baeume(self.basis, self.neu)
        self.assertEqual({d.pfad: (d.alt is not None, d.neu is not None) for d in baum.dateien},
                         {"alt.py": (True, False), "geaendert.py": (True, True), "neu.py": (False, True)})
        self.assertEqual(baum.pfade, ("geaendert.py", "gleich.py", "neu.py"))

    def test_binaerdateien_und_grosse_dateien_werden_ueber_die_pruefsumme_verglichen(self):
        self.schreibe(self.basis, "bild.png", b"\x89PNG\x00\x01")
        self.schreibe(self.neu, "bild.png", b"\x89PNG\x00\x02")
        gross = b"x" * (L.DATEI_MAX + 1)
        self.schreibe(self.basis, "gross.bin", gross)
        self.schreibe(self.neu, "gross.bin", gross[:-1] + b"y")
        self.beide("unveraendert.bin", b"\x00\x01")
        baum = L.vergleiche_baeume(self.basis, self.neu)
        self.assertEqual(sorted(d.pfad for d in baum.dateien), ["bild.png", "gross.bin"])
        self.assertTrue(all(d.neu == b"" for d in baum.dateien))  # kein Inhalt zum Scannen, der Name zählt

    def test_ausgelassene_ordner_und_links(self):
        self.schreibe(self.neu, "node_modules/x/index.js", "eval('x')")
        self.schreibe(self.neu, ".git/config", "x")
        self.schreibe(self.neu, "ui/.next/cache.js", "x")
        self.schreibe(self.neu, "echt.py", "x")
        try:
            os.symlink(self.neu, os.path.join(self.neu, "verweis"))
            os.symlink(os.path.join(self.neu, "echt.py"), os.path.join(self.neu, "dateilink"))
            hat_links = True
        except (OSError, NotImplementedError):
            hat_links = False
        baum = L.vergleiche_baeume(self.basis, self.neu)
        self.assertEqual([d.pfad for d in baum.dateien], ["echt.py"])
        if hat_links:
            self.assertEqual(sorted(baum.links), ["dateilink", "verweis"])  # beide gemeldet, der Ordnerlink wird nie betreten

    def test_zu_viele_dateien(self):
        for i in range(5):
            self.schreibe(self.neu, f"d{i}.txt", "x")
        alt = L.DATEIEN_MAX
        L.DATEIEN_MAX = 3
        try:
            self.assertEqual(L.vergleiche_baeume(self.basis, self.neu).fehler, "zu_viele_dateien")
        finally:
            L.DATEIEN_MAX = alt


class LaufTests(Baeume):
    def starte(self, runde=1, ausfuehrer=None, **kw):
        os.makedirs(os.path.join(self.neu, "voice-core"), exist_ok=True)
        for p in L.PYTHON_PAKETE + L.NODE_PAKETE:
            os.makedirs(os.path.join(self.neu, p), exist_ok=True)
        ausfuehrer = ausfuehrer or FakeAusfuehrer()
        return L.lauf(self.basis, self.neu, runde, MUSTER, ausfuehrer, {"PATH": "/usr/bin", "HTTPS_PROXY": L.PROXY_PAKETE}, **kw), ausfuehrer

    def test_alles_in_ordnung(self):
        self.schreibe(self.neu, "voice-core/neu.py", "x = 1\n")
        erg, aus = self.starte()
        self.assertEqual(erg["pruefstand"], dict.fromkeys(L.RUNDEN[1], "ok"))
        self.assertFalse(erg["sonder"])
        self.assertGreater(len(aus.aufrufe), 8)

    def test_ergebnis_entspricht_dem_vertrag_des_release_dienstes(self):
        erg, _ = self.starte()
        for runde in (1, 2, 3):
            e, _ = self.starte(runde)
            for name, wert in e["pruefstand"].items():
                self.assertRegex(name, AU.PRUEFSTAND_NAME_RE)
                self.assertIn(wert, AU.PRUEFSTAND_WERTE)
            # alle Zeilen, die der Läufer verantwortet, stehen unter den Pflichtzeilen der Runde
            self.assertLessEqual(set(e["pruefstand"]), set(AU.PRUEFSTAND_PFLICHT[runde]))
        fehlt = set(AU.PRUEFSTAND_PFLICHT[1]) - set(erg["pruefstand"])
        self.assertEqual(fehlt, {"schutzschicht"})  # die Zeile liefert der Anker, nie dieser Läufer
        json.dumps(erg)

    def test_runde_3_hat_weniger_stufen(self):
        erg, aus = self.starte(3)
        self.assertEqual(set(erg["pruefstand"]), {"bauen", "tests", "secret_scan", "fremdverweis"})
        self.assertFalse(any(a[1][:2] == ("npx", "--no-install") for a in aus.aufrufe))  # kein Lint in Runde 3

    def test_bauen_fehler_ueberspringt_tests_und_lint(self):
        erg, aus = self.starte(ausfuehrer=FakeAusfuehrer({("ui", "npm run"): L.Ausgang(1, "Build kaputt")}))
        self.assertEqual((erg["pruefstand"]["bauen"], erg["pruefstand"]["tests"], erg["pruefstand"]["typen_lint"]),
                         ("fehler", "uebersprungen", "uebersprungen"))
        self.assertIn("Build kaputt", erg["details"]["bauen"]["ende"])
        self.assertEqual(erg["pruefstand"]["secret_scan"], "ok")  # die Scanner laufen trotzdem
        self.assertFalse(any(a[1][:3] == ("uv", "run", "--frozen") for a in aus.aufrufe))

    def test_test_fehler_und_zeitlimit(self):
        erg, _ = self.starte(ausfuehrer=FakeAusfuehrer({("geraete", "uv run"): L.Ausgang(1, "2 failed")}))
        self.assertEqual(erg["pruefstand"]["tests"], "fehler")
        self.assertTrue(erg["details"]["tests"]["fehler"].startswith("exit_1:geraete:"))
        erg, _ = self.starte(ausfuehrer=FakeAusfuehrer({("ui", "npm test"): L.Ausgang(None, "haengt")}))
        self.assertTrue(erg["details"]["tests"]["fehler"].startswith("zeitlimit:ui:"))

    def test_panne_des_ausfuehrers_ist_ein_fehler_ohne_text(self):
        erg, _ = self.starte(ausfuehrer=FakeAusfuehrer(panne=True))
        self.assertEqual(erg["pruefstand"]["bauen"], "fehler")
        self.assertNotIn("geheim", json.dumps(erg))

    def test_link_im_baum_ist_ein_fehler_beim_bauen(self):
        try:
            os.symlink("/etc", os.path.join(self.neu, "boese"))
        except (OSError, NotImplementedError):
            self.skipTest("keine Links")
        erg, aus = self.starte()
        self.assertEqual((erg["pruefstand"]["bauen"], erg["details"]["bauen"]["fehler"]), ("fehler", "link_im_baum"))
        self.assertEqual(aus.aufrufe, [])  # nichts gebaut, kein Befehl gestartet

    def test_secret_im_diff_macht_die_stufe_rot(self):
        self.schreibe(self.neu, "voice-core/x.py", "k = '" + "sk-ant-api03-" + "aB3_" * 25 + "'\n")
        erg, _ = self.starte()
        self.assertEqual((erg["pruefstand"]["secret_scan"], erg["details"]["secret_scan"]["treffer_anzahl"]), ("fehler", 1))
        self.assertEqual(erg["treffer"]["secret_scan"], [{"pfad": "voice-core/x.py", "zeile": 1, "art": "anthropic_schluessel", "detail": ""}])
        self.assertNotIn("aB3_aB3_", json.dumps(erg))

    def test_fremdverweis_und_fehlende_mustervorlage(self):
        self.schreibe(self.neu, "README.md", "ein Fremdname\n")
        erg, _ = self.starte()
        self.assertEqual(erg["pruefstand"]["fremdverweis"], "fehler")
        e = L.lauf(self.basis, self.neu, 1, [], FakeAusfuehrer(), {})
        self.assertEqual((e["pruefstand"]["fremdverweis"], e["details"]["fremdverweis"]["fehler"]), ("fehler", "mustervorlage"))

    def test_wirkungsflaechen_werden_angezeigt_und_sonder_gesetzt(self):
        self.schreibe(self.neu, "voice-core/x.py", "import subprocess\nk = os.environ['WAMO_SCHLUESSEL_KEK']\n")
        erg, _ = self.starte()
        self.assertEqual(erg["pruefstand"]["wirkungsflaechen"], "ok")
        self.assertTrue(erg["sonder"])

    def test_aenderung_an_host_oder_desktop_verlangt_die_windows_pruefung(self):
        for pfad in ("host/tools.py", "desktop/main.js", "host/test_tab.py"):
            self.setUp()
            self.schreibe(self.neu, pfad, "x\n")
            erg, aus = self.starte()
            self.assertTrue(erg["windows_pruefung_noetig"], pfad)
            self.assertNotIn(("host", ("uv", "run")), [(o, a[:2]) for o, a, _ in aus.aufrufe])   # host wird hier nicht getestet
        self.setUp()
        self.schreibe(self.neu, "voice-core/x.py", "x = 1\n")
        self.assertFalse(self.starte()[0]["windows_pruefung_noetig"])

    def test_migration_geaendert_ist_ohne_lint_ein_fehler(self):
        self.schreibe(self.neu, "supabase/migrations/0017_x.sql", "select 1;")
        erg, _ = self.starte()
        self.assertEqual((erg["pruefstand"]["migrations_lint"], erg["details"]["migrations_lint"]["fehler"]), ("fehler", "migrations_lint_fehlt"))

    def test_lockfile_aenderung_loest_den_audit_aus(self):
        alt = 'version = 1\n[[package]]\nname = "a"\nversion = "1"\nsource = { registry = "https://pypi.org/simple" }\n'
        self.schreibe(self.basis, "voice-core/uv.lock", alt)
        self.schreibe(self.neu, "voice-core/uv.lock", alt + '[[package]]\nname = "b"\nversion = "2"\nsource = { registry = "https://pypi.org/simple" }\n')
        erg, aus = self.starte()
        self.assertEqual(erg["neue_pakete"], [["b", "2"]])
        self.assertTrue(any(a[1][:2] == ("npm", "audit") for a in aus.aufrufe))
        erg, aus = self.starte(ausfuehrer=FakeAusfuehrer({("ui", "npm audit"): L.Ausgang(1, "high")}))
        self.assertEqual(erg["pruefstand"]["abhaengigkeiten"], "fehler")
        erg, aus = self.starte(auch_audit=False)
        self.assertFalse(any(a[1][:2] == ("npm", "audit") for a in aus.aufrufe))

    def test_unbekannte_runde(self):
        with self.assertRaises(ValueError):
            L.lauf(self.basis, self.neu, 4, MUSTER, FakeAusfuehrer())

    def test_zu_grosser_baum_laesst_die_scanner_nicht_gruen_durch(self):
        for i in range(4):
            self.schreibe(self.neu, f"d{i}.txt", "x")
        alt = L.DATEIEN_MAX
        L.DATEIEN_MAX = 2
        try:
            erg, _ = self.starte()
        finally:
            L.DATEIEN_MAX = alt
        for stufe in ("secret_scan", "fremdverweis", "wirkungsflaechen", "abhaengigkeiten"):
            self.assertEqual(erg["pruefstand"][stufe], "fehler", stufe)

    def test_fehlende_pakete_werden_uebergangen(self):
        e = L.werkzeugstufe("bauen", L.plan_bauen(), self.neu, FakeAusfuehrer(), {})
        self.assertEqual(e.wert, "ok")  # in einem leeren Baum gibt es nichts zu bauen


class UmgebungTests(unittest.TestCase):
    def test_nur_erlaubte_namen_und_keine_geheimnisse(self):
        b = L.Befehl("ui", ("npm", "run", "build"), env=(("NEXT_PUBLIC_SUPABASE_URL", L.PLATZHALTER_URL),))
        env = L.umgebung(b, {"PATH": "/usr/bin", "HOME": "/tmp/h", "ANTHROPIC_API_KEY": "darf-nie-durch", "GITHUB_TOKEN": "x"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["HTTPS_PROXY"], L.PROXY_PAKETE)
        self.assertEqual((env["HTTP_PROXY"], env["NO_PROXY"]), ("", ""))  # kein Klartext-HTTP, kein Umweg an den Proxys vorbei
        self.assertEqual(env["NEXT_PUBLIC_SUPABASE_URL"], L.PLATZHALTER_URL)
        self.assertEqual(env["UV_PYTHON_DOWNLOADS"], "never")

    def test_ausgabe_wird_gekuerzt_und_gereinigt(self):
        roh = ("\x1b[31mrot\x1b[0m " + "z" * 5000 + "\x07\x00ende").encode()
        text = L.kuerzen(roh)
        self.assertLessEqual(len(text), L.ENDE_ZEICHEN)
        self.assertTrue(text.endswith("??ende"))
        self.assertNotIn("\x1b", text)


class PlanTests(unittest.TestCase):
    def alle(self):
        return [*L.plan_bauen(), *L.plan_tests(), *L.plan_typen_lint(), *L.AUDITS]

    def test_nur_feste_werkzeuge_ohne_shell(self):
        for b in self.alle():
            self.assertIn(b.argv[0], {"uv", "npm", "npx", "pip-audit"}, b)
            self.assertTrue(all(isinstance(a, str) and a for a in b.argv))
            self.assertFalse(any(z in " ".join(b.argv) for z in (";", "&&", "|", "`", "$(", "sudo", "curl", "bash", "sh -c")), b)
            self.assertNotIn("..", b.ordner)
            self.assertGreater(b.zeit_s, 0)

    def test_npm_installation_ohne_skripte_und_lockfile_pflicht(self):
        for b in L.plan_bauen():
            if b.argv[:2] == ("npm", "ci"):
                self.assertIn("--ignore-scripts", b.argv)
            if b.argv[:2] == ("uv", "sync"):
                self.assertIn("--frozen", b.argv)
        for b in L.plan_tests():
            if b.argv[0] == "uv":
                self.assertIn("--frozen", b.argv)
                self.assertIn("--ignore-glob=test_integration_*", b.argv)

    def test_windows_pakete_werden_unter_linux_nicht_getestet_aber_gebaut_und_geaudittet(self):
        getestet = {b.ordner for b in L.plan_tests() if b.argv[0] == "uv"}
        self.assertEqual(getestet, {"voice-core", "geraete"})
        self.assertEqual({b.ordner for b in L.plan_tests() if b.argv[0] == "npm"}, {"ui"})   # desktop ist eine Windows-Anwendung: gebaut, nicht getestet
        self.assertNotIn("host", getestet)
        self.assertIn("host", {b.ordner for b in L.plan_bauen()})
        self.assertIn("host", {b.ordner for b in L.AUDITS})
        self.assertEqual(L.WINDOWS_PFADE, ("host/", "desktop/"))

    def test_ordner_skripte_und_sperrdateien_gibt_es_im_repo(self):
        for p in L.PYTHON_PAKETE:
            self.assertTrue((REPO / p / "uv.lock").is_file(), p)
            self.assertTrue((REPO / p / "pyproject.toml").is_file(), p)
        for p in L.NODE_PAKETE:
            self.assertTrue((REPO / p / "package.json").is_file(), p)
            self.assertTrue((REPO / p / "package-lock.json").is_file(), p)
        ui = json.loads((REPO / "ui" / "package.json").read_text(encoding="utf-8"))["scripts"]
        for skript in ("build", "lint", "test"):
            self.assertIn(skript, ui)
        self.assertIn("test", json.loads((REPO / "desktop" / "package.json").read_text(encoding="utf-8"))["scripts"])
        for b in self.alle():
            self.assertTrue((REPO / b.ordner).is_dir(), b)

    def test_bauplatzhalter_decken_die_pflichtvariablen_des_dockerfiles(self):
        text = (INFRA / "wamo-ui" / "Dockerfile").read_text(encoding="utf-8")
        build = next(b for b in L.plan_bauen() if b.argv[:3] == ("npm", "run", "build"))
        gesetzt = {k for k, _ in build.env}
        for name in ("NEXT_PUBLIC_SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "NEXT_PUBLIC_WAMO_KERN_BUERO"):
            self.assertIn(f"ARG {name}", text)
            self.assertIn(name, gesetzt)

    def test_quelltext(self):
        import ast
        text = (INFRA / "release" / "pruefer" / "pruefer_lauf.py").read_text(encoding="utf-8")
        for zeichen in (chr(0x2014), chr(0x2013), "\r", "\x00"):
            self.assertNotIn(zeichen, text)
        self.assertNotIn("shell=True", text)
        self.assertNotIn("os.system", text)
        ast.parse(text)


class AufrufTests(Baeume):
    @unittest.skipIf(os.name == "nt", "Prozessgruppen und Zeitlimit brauchen Linux")
    def test_echter_ausfuehrer_zeitlimit_und_exitcode(self):
        os.makedirs(os.path.join(self.neu, "x"))
        ok = L.ausfuehren_echt(L.Befehl("x", (sys.executable, "-c", "print('hallo')"), 30), self.neu, {"PATH": os.environ["PATH"]})
        self.assertEqual((ok.code, ok.ende), (0, "hallo"))
        kaputt = L.ausfuehren_echt(L.Befehl("x", (sys.executable, "-c", "import sys; print('fehler'); sys.exit(3)"), 30), self.neu, {"PATH": os.environ["PATH"]})
        self.assertEqual((kaputt.code, kaputt.ende), (3, "fehler"))
        lang = L.ausfuehren_echt(L.Befehl("x", (sys.executable, "-c", "import time; time.sleep(30)"), 1), self.neu, {"PATH": os.environ["PATH"]})
        self.assertIsNone(lang.code)
        fehlt = L.ausfuehren_echt(L.Befehl("x", ("/gibt/es/nicht",), 5), self.neu, {})
        self.assertEqual(fehlt.code, 127)

    def test_kommandozeile_schreibt_das_ergebnis(self):
        alt = L.ausfuehren_echt
        L.ausfuehren_echt = FakeAusfuehrer()
        muster = os.path.join(self.tmp.name, "muster.txt")
        Path(muster).write_text("# nichts Wichtiges\nfremdname\n", encoding="utf-8")
        ausgabe = os.path.join(self.tmp.name, "erg.json")
        try:
            for p in L.PYTHON_PAKETE + L.NODE_PAKETE:
                os.makedirs(os.path.join(self.neu, p))
            # main bindet lauf() mit dem Standard-Ausführer zur Definitionszeit: darum hier lauf() direkt über die Kommandozeile mit leerem Baum
            code = L.main(["--basis", self.basis, "--neu", self.neu, "--runde", "3", "--fremdverweise", muster, "--ausgabe", ausgabe, "--ohne-audit"])
        finally:
            L.ausfuehren_echt = alt
        erg = json.loads(Path(ausgabe).read_text(encoding="utf-8"))
        self.assertEqual(erg["runde"], 3)
        self.assertIn(code, (0, 1))
        self.assertIn("dauer_s", erg)


if __name__ == "__main__":
    unittest.main()
