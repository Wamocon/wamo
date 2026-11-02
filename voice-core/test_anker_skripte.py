"""Tests der Einrichtungsskripte des Notankers (infra/sokrates2/anker-einrichten.sh und anker-rueckweg.sh).

Die Skripte werden hier NICHT gegen das echte System ausgeführt, sondern im Probemodus in einem Wegwerf-Ordner
(WAMO_ANKER_WURZEL): kein root, kein systemd, nichts unter /usr, /etc oder /var. Der Probelauf braucht GNU-Werkzeuge und
läuft deshalb nur unter Linux; die Syntaxprüfung (bash -n) läuft überall, wo eine Bash vorhanden ist.
Die Einrichtung mit root und systemd (Attrappen) übt der Containerlauf aus der Arbeitsnotiz zu Schritt 0.1.

Ausführen:  uv run python -m pytest -q test_anker_skripte.py   (im Ordner voice-core/)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
LINUX = sys.platform.startswith("linux")
SKRIPTE = ("anker-einrichten.sh", "anker-rueckweg.sh")


def _bash():
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    for k in kandidaten:
        if k and os.path.exists(k):
            return k
    return None


BASH = _bash()


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class SyntaxTests(unittest.TestCase):
    def test_bash_n(self):
        for name in (*SKRIPTE, "anker/wamo-anker.sh"):
            with self.subTest(skript=name):
                r = subprocess.run([BASH, "-n", str(INFRA / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_set_euo_pipefail_und_keine_eval(self):
        for name in SKRIPTE:
            text = (INFRA / name).read_text(encoding="utf-8")
            self.assertIn("set -euo pipefail", text)
            self.assertNotIn("eval ", text)
            self.assertNotIn("\u2014", text)
            self.assertNotIn("\u2013", text)
            self.assertNotIn("\r", text)

    def test_unit_und_wrapper_sind_fest_verdrahtet(self):
        unit = (INFRA / "anker" / "wamo-anker.service").read_text(encoding="utf-8")
        for zeile in ("User=root", "CapabilityBoundingSet=", "NoNewPrivileges=yes", "ProtectSystem=strict",
                      "RestrictAddressFamilies=AF_UNIX", "IPAddressDeny=any", "ExecStartPre=/usr/bin/python3 -I "
                      "/usr/local/lib/wamo-anker/wamo_anker.py selbstpruefung"):
            self.assertIn(zeile, unit)
        wrapper = (INFRA / "anker" / "wamo-anker.sh").read_text(encoding="utf-8")
        self.assertIn('exec /usr/bin/python3 -I /usr/local/lib/wamo-anker/wamo_anker.py "$@"', wrapper)


@unittest.skipUnless(LINUX and BASH and os.geteuid() != 0, "Probelauf braucht Linux, GNU-Werkzeuge und läuft nie als root")
class ProbelaufTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="anker-skripte-")
        self.addCleanup(self.tmp.cleanup)
        self.wurzel = os.path.join(self.tmp.name, "wurzel")
        self.skripte = os.path.join(self.tmp.name, "infra")
        ignorieren = shutil.ignore_patterns("__pycache__", "supabase-wamo", "wamo-ui", "claude-code")
        shutil.copytree(INFRA, self.skripte, ignore=ignorieren)
        os.chmod(self.skripte, 0o755)

    def lauf(self, skript, *args, wurzel=None, extra=None):
        env = {"PATH": os.environ["PATH"], "WAMO_ANKER_WURZEL": self.wurzel if wurzel is None else wurzel,
               "WAMO_ANKER_PYTHON": sys.executable, **(extra or {})}
        return subprocess.run([BASH, os.path.join(self.skripte, skript), *args], capture_output=True, text=True,
                              env=env, timeout=120)

    def pfad(self, *teile):
        return os.path.join(self.wurzel, *teile)

    def test_trockenlauf_legt_nichts_an(self):
        for skript in SKRIPTE:
            r = self.lauf(skript, "--trocken")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("[trocken]", r.stdout)
        self.assertFalse(os.path.exists(self.wurzel))

    def test_einrichtung_baut_den_baum_und_ist_wiederholbar(self):
        r = self.lauf("anker-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lib = self.pfad("usr", "local", "lib", "wamo-anker")
        self.assertEqual(sorted(os.listdir(lib)),
                         ["SHA256SUMS", "anker_rauchtest.py", "schritte", "wamo-anker.sh", "wamo_anker.py"])
        summen = subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=lib, capture_output=True, text=True)
        self.assertEqual(summen.returncode, 0, summen.stderr)
        self.assertEqual(oct(os.stat(self.pfad("var", "lib", "wamo-anker")).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(self.pfad("var", "lib", "wamo-anker", "stand.json")).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(self.pfad("var", "log", "wamo-anker", "audit.jsonl")).st_mode & 0o777), "0o600")
        sperre = self.pfad("etc", "wamo-anker", "selbstaenderung.aus")
        self.assertTrue(os.path.exists(sperre), "Erstinstallation setzt die Sperre")
        self.assertTrue(os.path.islink(self.pfad("usr", "local", "bin", "wamo-anker")))
        vorher = {p: Path(p).read_bytes() for p in (sperre, self.pfad("var", "lib", "wamo-anker", "stand.json"))}
        zweiter = self.lauf("anker-einrichten.sh")
        self.assertEqual(zweiter.returncode, 0, zweiter.stdout + zweiter.stderr)
        self.assertIn("Erstinstallation: nein", zweiter.stdout)
        self.assertEqual({p: Path(p).read_bytes() for p in vorher}, vorher)  # Zustand und Sperre bleiben unverändert
        self.assertFalse(any("__pycache__" in str(p) for p in Path(self.wurzel).rglob("*")))  # sonst wäre die Prüfsumme hinfällig

    def test_aktualisierung_ersetzt_den_baum_und_raeumt_den_rueckfall_ab(self):
        self.lauf("anker-einrichten.sh")
        lib = self.pfad("usr", "local", "lib", "wamo-anker")
        alt = Path(lib, "SHA256SUMS").read_text()
        with open(os.path.join(self.skripte, "anker", "wamo_anker.py"), "a") as f:
            f.write("\n# neue Fassung\n")
        r = self.lauf("anker-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(Path(lib, "SHA256SUMS").read_text(), alt)
        self.assertIn("# neue Fassung", Path(lib, "wamo_anker.py").read_text())
        self.assertFalse(os.path.exists(lib + ".vorher"))  # der Rückfall wird erst nach bestandener Prüfung entfernt
        self.assertTrue(os.path.isdir(self.pfad("usr", "local", "lib")))

    def test_systemordner_werden_nicht_angefasst(self):
        os.makedirs(self.pfad("usr", "local", "lib"), mode=0o775)
        os.chmod(self.pfad("usr", "local", "lib"), 0o2775)
        r = self.lauf("anker-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(oct(os.stat(self.pfad("usr", "local", "lib")).st_mode & 0o7777), "0o2775")  # wie auf Ubuntu

    def test_schritt_skripte_kommen_ins_manifest(self):
        schritte = os.path.join(self.skripte, "anker", "schritte")
        os.makedirs(schritte, exist_ok=True)
        with open(os.path.join(schritte, "rueckweg-ui"), "w", newline="\n") as f:  # newline= bei Path.write_text erst ab 3.10
            f.write("#!/bin/sh\nexit 0\n")
        r = self.lauf("anker-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        manifest = Path(self.pfad("usr", "local", "lib", "wamo-anker", "SHA256SUMS")).read_text()
        self.assertIn("./schritte/rueckweg-ui", manifest)
        self.assertTrue(os.access(self.pfad("usr", "local", "lib", "wamo-anker", "schritte", "rueckweg-ui"), os.X_OK))

    def test_rueckweg_schritte_sind_root_eigene_kopien_der_betriebsskripte_mit_vorlagen(self):
        r = self.lauf("anker-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lib = self.pfad("usr", "local", "lib", "wamo-anker")
        schritte = os.path.join(lib, "schritte")
        self.assertEqual(sorted(os.listdir(schritte)), ["rueckweg-kern", "rueckweg-ui", "slot.inc.legacy", "slot.inc.vorlage"])
        for schritt, quelle in (("rueckweg-ui", "wamo-ui-umschalten.sh"), ("rueckweg-kern", "wamo-core-slots.sh")):
            self.assertEqual(Path(os.path.join(schritte, schritt)).read_bytes(), Path(INFRA / quelle).read_bytes().replace(b"\r\n", b"\n"), schritt)
            self.assertTrue(os.access(os.path.join(schritte, schritt), os.X_OK), schritt)
        manifest = Path(os.path.join(lib, "SHA256SUMS")).read_text()
        for n in ("rueckweg-ui", "rueckweg-kern", "slot.inc.vorlage", "slot.inc.legacy"):
            self.assertIn(f"./schritte/{n}", manifest, n)

    def test_rueckweg_kern_als_schritt_prueft_die_parameter_streng(self):
        self.lauf("anker-einrichten.sh")
        schritt = self.pfad("usr", "local", "lib", "wamo-anker", "schritte", "rueckweg-kern")
        gut = "a" * 40
        for argv, text in ((["v1.2", gut, "a"], "Version ungültig"), (["v1.2.3", "xyz", "a"], "Commit ungültig"), (["v1.2.3", gut, "c"], "Slot muss a oder b sein"),
                           (["v1.2.3; ls", gut, "a"], "Version ungültig"), (["v1.2.3", gut.upper(), "a"], "Commit ungültig"), ([], "Version ungültig")):
            with self.subTest(argv=argv):
                p = subprocess.run([BASH, schritt, *argv], capture_output=True, text=True, timeout=30)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(text, p.stderr)
        p = subprocess.run([BASH, schritt, "v1.2.3", gut, "a"], capture_output=True, text=True, timeout=30)
        self.assertIn("Bitte als root", p.stderr)   # gültige Parameter kommen bis zur Root-Pflicht, nie weiter

    def test_rueckweg_entfernt_behaelt_aber_sperre_und_sichert_audit(self):
        self.lauf("anker-einrichten.sh")
        r = self.lauf("anker-rueckweg.sh", "--entfernen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.pfad("usr", "local", "lib", "wamo-anker")))
        self.assertFalse(os.path.exists(self.pfad("var", "lib", "wamo-anker")))
        self.assertFalse(os.path.lexists(self.pfad("usr", "local", "bin", "wamo-anker")))
        self.assertTrue(os.path.exists(self.pfad("etc", "wamo-anker", "selbstaenderung.aus")))
        gesichert = sorted(os.listdir(self.pfad("var", "backups", "wamo-anker")))
        self.assertEqual([n.split(".")[0] for n in gesichert], ["audit", "stand"])

    def test_rueckweg_ohne_entfernen_laesst_dateien_stehen(self):
        self.lauf("anker-einrichten.sh")
        r = self.lauf("anker-rueckweg.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.pfad("usr", "local", "lib", "wamo-anker", "wamo_anker.py")))

    def test_fehlerfaelle(self):
        self.assertEqual(self.lauf("anker-einrichten.sh", "--unsinn").returncode, 2)
        self.assertEqual(self.lauf("anker-rueckweg.sh", "--unsinn").returncode, 2)
        for wurzel in ("relativ", "/", "../x", "//x", "/tmp//x", "/tmp/a/../b", "/tmp/a/..", "/tmp/./a", "/tmp/a b", "/tmp/x;y",
                       "/tmp/$(id)", "/tmp/x\ny"):
            with self.subTest(wurzel=wurzel):
                self.assertEqual(self.lauf("anker-einrichten.sh", wurzel=wurzel).returncode, 1)
                self.assertEqual(self.lauf("anker-rueckweg.sh", "--entfernen", wurzel=wurzel).returncode, 1)

    def test_geheimnisdatei_im_ankerordner_bricht_ab(self):
        for name in (".env", ".env.local", "geheim.env", "x.pem", "x.key"):
            with self.subTest(name=name):
                datei = os.path.join(self.skripte, "anker", name)
                Path(datei).write_text("X=1\n")
                r = self.lauf("anker-einrichten.sh")
                self.assertEqual(r.returncode, 1)
                self.assertIn("Geheimnisdatei", r.stderr)
                self.assertFalse(os.path.exists(self.wurzel))
                os.unlink(datei)

    def test_syntaxfehler_im_anker_bricht_ab(self):
        with open(os.path.join(self.skripte, "anker", "wamo_anker.py"), "a") as f:
            f.write("\ndef kaputt(:\n")
        r = self.lauf("anker-einrichten.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Syntaxfehler", r.stderr)
        self.assertFalse(os.path.exists(self.wurzel))

    def test_rueckweg_loescht_nur_feste_pfade(self):
        """Ein Symlink statt des Laufordners wird nie verfolgt oder gelöscht."""
        self.lauf("anker-einrichten.sh")
        ziel = os.path.join(self.tmp.name, "wichtig")
        os.makedirs(ziel)
        Path(ziel, "datei").write_text("bleibt")
        os.makedirs(self.pfad("run"), exist_ok=True)
        os.symlink(ziel, self.pfad("run", "wamo-anker"))
        r = self.lauf("anker-rueckweg.sh", "--entfernen")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(Path(ziel, "datei").read_text(), "bleibt")


if __name__ == "__main__":
    unittest.main()
