"""Tests der Einrichtungsskripte und der Unit des Release-Dienstes (infra/sokrates2/release-einrichten.sh, release-rueckweg.sh,
release/wamo-release.service, release/wamo-release.sh).

Die Skripte werden hier NICHT gegen das echte System ausgeführt, sondern im Probemodus in einem Wegwerf-Ordner
(WAMO_RELEASE_WURZEL): kein root, kein systemd, kein Benutzer, nichts unter /usr, /etc oder /var. Der Probelauf braucht
GNU-Werkzeuge und läuft deshalb nur unter Linux; die Syntaxprüfung (bash -n) und die Textprüfungen laufen überall, wo eine
Bash vorhanden ist. Die Einrichtung mit root und systemd übt der Container- und der Serverlauf aus der Arbeitsnotiz zu 0.3.

Ausführen:  uv run python -m pytest -q test_release_skripte.py   (im Ordner voice-core/)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
RELEASE = INFRA / "release"
LINUX = sys.platform.startswith("linux")
SKRIPTE = ("release-einrichten.sh", "release-rueckweg.sh")


def _bash():
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    for k in kandidaten:
        if k and os.path.exists(k):
            return k
    return None


BASH = _bash()


class TextTests(unittest.TestCase):
    def test_unit_ist_gehaertet_und_fest_verdrahtet(self):
        unit = (RELEASE / "wamo-release.service").read_text(encoding="utf-8")
        zeilen = [z.strip() for z in unit.splitlines() if z.strip() and not z.startswith("#")]
        for soll in ("User=wamo-release", "Group=wamo-release", "CapabilityBoundingSet=", "AmbientCapabilities=",
                     "NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "PrivateDevices=yes",
                     "RestrictAddressFamilies=AF_UNIX AF_INET", "IPAddressDeny=any", "IPAddressAllow=192.168.178.136/32", "SystemCallFilter=@system-service",
                     "SystemCallFilter=~@privileged", "SystemCallArchitectures=native", "MemoryDenyWriteExecute=yes",
                     "RestrictNamespaces=yes", "LockPersonality=yes", "RestrictSUIDSGID=yes", "ProtectKernelTunables=yes",
                     "ReadWritePaths=/var/lib/wamo-release /run/wamo-release /var/log/wamo-release",
                     "ExecStartPre=/usr/bin/python3 -E -s -B /opt/wamo-release/app/wamo_release.py selbstpruefung",
                     "ExecStart=/usr/bin/python3 -E -s -B /opt/wamo-release/app/wamo_release.py dienst",
                     "Requires=wamo-anker.service wamo-release-ausgang.service", "After=wamo-anker.service wamo-release-ausgang.service",
                     "PartOf=wamo-anker.service", "Environment=WAMO_RELEASE_SCHEMA=wamo_dev",
                     "SupplementaryGroups=wamocon", "LimitCORE=0",
                     "InaccessiblePaths=-/etc/wamo -/etc/wamo-geraete -/etc/wamo-anker"):
            self.assertIn(soll, zeilen)
        self.assertEqual(len([z for z in zeilen if z.startswith(("ExecStart", "ExecStop", "ExecReload"))]), 2)
        for verboten in ("EnvironmentFile", "ExecStartPre=+", "ExecStart=+", "AmbientCapabilities=CAP", "AF_INET6", "AF_NETLINK",
                         "AF_PACKET", "User=root", "PermissionsStartOnly"):
            self.assertFalse(any(verboten in z for z in zeilen), verboten)
        self.assertEqual([z for z in zeilen if z.startswith(("Environment=WAMO", "IPAddressAllow"))],
                         ["Environment=WAMO_RELEASE_SCHEMA=wamo_dev", "IPAddressAllow=192.168.178.136/32"])
        self.assertNotIn("/etc/wamo-release", [p for z in zeilen if z.startswith("InaccessiblePaths=") for p in z[18:].split()
                                                if p.lstrip("-") == "/etc/wamo-release"])  # der Dienst muss db.zugang lesen können

    def test_ausgangsregel_erlaubt_nur_die_datenbank(self):
        nft = (RELEASE / "wamo-release-ausgang.nft").read_text(encoding="utf-8")
        regeln = [z.strip() for z in nft.splitlines() if z.strip() and not z.lstrip().startswith("#")]
        self.assertIn("meta skuid wamo-release jump wamo_release", regeln)
        erlaubt = [z for z in regeln if " accept" in z and "policy" not in z]
        self.assertEqual(len(erlaubt), 1)
        self.assertTrue(erlaubt[0].startswith("ip daddr 192.168.178.136 tcp dport 55322 "), erlaubt[0])
        self.assertTrue(any(z.startswith("counter drop") for z in regeln))
        self.assertLess([i for i, z in enumerate(regeln) if " accept" in z][0], [i for i, z in enumerate(regeln) if "drop" in z][0])
        einheit = (RELEASE / "wamo-release-ausgang.service").read_text(encoding="utf-8")
        self.assertIn("Before=wamo-release.service", einheit)
        self.assertIn("ExecStart=/usr/sbin/nft -f /etc/wamo-release-ausgang/regeln.nft", einheit)
        for text in (nft, einheit):
            self.assertNotIn(chr(0x2014), text)
            self.assertNotIn("\r", text)

    def test_sperrdatei_nennt_nur_psycopg_mit_pruefsummen(self):
        sperre = (INFRA / "pg" / "requirements.lock").read_text(encoding="utf-8")
        eintraege = re.findall(r"^([A-Za-z0-9_.-]+)==([0-9.]+) \\\n\s+--hash=sha256:([0-9a-f]{64})$", sperre, re.M)
        self.assertEqual(sorted(e[0] for e in eintraege), ["psycopg", "psycopg-binary", "typing-extensions"])
        self.assertEqual(len(re.findall(r"--hash=", sperre)), 3)
        self.assertNotIn("--index-url", sperre)
        self.assertNotIn("--extra-index-url", sperre)

    def test_wrapper_ist_fest_verdrahtet(self):
        wrapper = (RELEASE / "wamo-release.sh").read_text(encoding="utf-8")
        self.assertIn('exec /usr/bin/python3 -E -s -B /opt/wamo-release/app/wamo_release.py "$@"', wrapper)

    def test_modulliste_des_skripts_ist_vollstaendig(self):
        text = (INFRA / "release-einrichten.sh").read_text(encoding="utf-8")
        m = re.search(r"^MODULE=\((.*?)\)", text, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(sorted(m[1].split()), sorted(p.name for p in RELEASE.glob("*.py")))
        pg = re.search(r"^PG_MODULE=\((.*?)\)", text, re.M)
        self.assertIsNotNone(pg)
        self.assertEqual(sorted(pg[1].split()), ["release_pg.py", "wamo_pg.py"])
        for name in pg[1].split():
            self.assertTrue((INFRA / "pg" / name).is_file(), name)

    def test_skripte_ohne_gedankenstriche_eval_und_cr(self):
        for name in SKRIPTE + ("release/wamo-release.sh",):
            text = (INFRA / name).read_text(encoding="utf-8")
            self.assertNotIn("eval ", text)
            self.assertNotIn(chr(0x2014), text)
            self.assertNotIn(chr(0x2013), text)
            self.assertNotIn("\r", text)
        for name in SKRIPTE:
            self.assertIn("set -euo pipefail", (INFRA / name).read_text(encoding="utf-8"))

    def test_nur_wamo_pfade_und_keine_geheimnisse(self):
        erlaubt = ("/opt/wamo-release", "/usr/local/bin/wamo-release", "/var/lib/wamo-release", "/var/log/wamo-release",
                   "/var/backups/wamo-release", "/run/wamo-release", "/etc/tmpfiles.d/wamo-release.conf",
                   "/etc/systemd/system/wamo-release.service", "/etc/systemd/system/wamo-anker.service",
                   "/etc/systemd/system/wamo-release-ausgang.service", "/etc/wamo-release-ausgang", "/etc/wamo-release/db.zugang",
                   "/usr/sbin", "/usr/bin", "/dev/null", "/var/lib", "/var/log", "/var/backups", "/run",
                   "/opt", "/etc/tmpfiles.d/", "/etc/systemd/system/", "/usr/local/bin", "/tmp/release-probe")
        for name in SKRIPTE:
            code = "\n".join(z for z in (INFRA / name).read_text(encoding="utf-8").splitlines() if not z.lstrip().startswith("#"))
            for pfad in set(re.findall(r"(?<![\w$.{}])/(?:opt|etc|var|usr|run|dev|tmp)/[A-Za-z0-9_./-]*", code)):
                self.assertTrue(pfad.startswith(erlaubt), (name, pfad))
            for muster in (r"/etc/wamo[/\s\"]", r"/etc/wamo-anker", r"core\.env", r"password", r"token", r"\btee\b",
                           r"/usr/local/lib/wamo-anker", r"wamo-core\.service", r"wamo-geraete\.service"):
                for zeile in code.splitlines():
                    self.assertIsNone(re.search(muster, zeile), (name, zeile))

    def test_skripte_aendern_nichts_am_anker(self):
        for name in SKRIPTE:
            text = (INFRA / name).read_text(encoding="utf-8")
            for zeile in text.splitlines():
                if zeile.lstrip().startswith("#"):
                    continue
                if "wamo-anker" in zeile:
                    self.assertTrue(re.search(r"(-f|\[) .*wamo-anker\.service", zeile) or "Notanker" in zeile
                                    or "anker-einrichten" in zeile, (name, zeile))


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class SyntaxTests(unittest.TestCase):
    def test_bash_n(self):
        for name in (*SKRIPTE, "release/wamo-release.sh"):
            with self.subTest(skript=name):
                r = subprocess.run([BASH, "-n", str(INFRA / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck nicht vorhanden")
    def test_shellcheck(self):
        for name in (*SKRIPTE, "release/wamo-release.sh"):
            with self.subTest(skript=name):
                r = subprocess.run(["shellcheck", "-S", "warning", str(INFRA / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout)


@unittest.skipUnless(LINUX and BASH and os.geteuid() != 0, "Probelauf braucht Linux, GNU-Werkzeuge und läuft nie als root")
class ProbelaufTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="release-skripte-")
        self.addCleanup(self.tmp.cleanup)
        self.wurzel = os.path.join(self.tmp.name, "wurzel")
        self.skripte = os.path.join(self.tmp.name, "infra")
        ignorieren = shutil.ignore_patterns("__pycache__", "supabase-wamo", "wamo-ui", "claude-code")
        shutil.copytree(INFRA, self.skripte, ignore=ignorieren)
        os.chmod(self.skripte, 0o755)

    def lauf(self, skript, *args, wurzel=None, extra=None):
        env = {"PATH": os.environ["PATH"], "WAMO_RELEASE_WURZEL": self.wurzel if wurzel is None else wurzel,
               "WAMO_RELEASE_PYTHON": sys.executable, **(extra or {})}
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
        r = self.lauf("release-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        app = self.pfad("opt", "wamo-release", "app")
        self.assertEqual(sorted(os.listdir(app)), sorted(["SHA256SUMS", "release_rauchtest.py", "wamo-release.sh",
                                                          "release_pg.py", "wamo_pg.py", *[p.name for p in RELEASE.glob("*.py")]]))
        summen = subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=app, capture_output=True, text=True)
        self.assertEqual(summen.returncode, 0, summen.stderr)
        self.assertEqual(oct(os.stat(self.pfad("var", "lib", "wamo-release")).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(self.pfad("var", "log", "wamo-release")).st_mode & 0o777), "0o750")
        audit = self.pfad("var", "log", "wamo-release", "audit.jsonl")
        self.assertEqual(oct(os.stat(audit).st_mode & 0o777), "0o600")
        self.assertEqual(os.path.getsize(audit), 0)
        self.assertTrue(os.access(os.path.join(app, "wamo-release.sh"), os.X_OK))
        self.assertTrue(os.path.islink(self.pfad("usr", "local", "bin", "wamo-release")))
        Path(audit).write_bytes(b'{"n":1}\n')  # der Dienst hat etwas geschrieben
        zweiter = self.lauf("release-einrichten.sh")
        self.assertEqual(zweiter.returncode, 0, zweiter.stdout + zweiter.stderr)
        self.assertEqual(Path(audit).read_bytes(), b'{"n":1}\n')  # Protokoll bleibt unverändert
        self.assertFalse(any("__pycache__" in str(p) for p in Path(self.wurzel).rglob("*")))  # sonst wäre die Prüfsumme hinfällig

    def test_der_installierte_baum_besteht_die_selbstpruefung_des_dienstes(self):
        self.lauf("release-einrichten.sh")
        app = self.pfad("opt", "wamo-release", "app")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import wamo_release as R; "
                "p = R.Pfade.unter(sys.argv[2]); p = R.Pfade(lib=sys.argv[1], lib_uid=p.lib_uid, wurzel=sys.argv[2]); "
                "print(R.installation_pruefen(p))")
        r = subprocess.run([sys.executable, "-B", "-c", code, app, self.wurzel], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "[]", r.stdout + r.stderr)

    def test_aktualisierung_ersetzt_den_baum_und_raeumt_den_rueckfall_ab(self):
        self.lauf("release-einrichten.sh")
        app = self.pfad("opt", "wamo-release", "app")
        alt = Path(app, "SHA256SUMS").read_text()
        with open(os.path.join(self.skripte, "release", "wamo_release.py"), "a") as f:
            f.write("\n# neue Fassung\n")
        r = self.lauf("release-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(Path(app, "SHA256SUMS").read_text(), alt)
        self.assertIn("# neue Fassung", Path(app, "wamo_release.py").read_text())
        self.assertFalse(os.path.exists(app + ".vorher"))

    def test_systemordner_werden_nicht_angefasst(self):
        os.makedirs(self.pfad("usr", "local", "bin"), mode=0o775)
        os.chmod(self.pfad("usr", "local", "bin"), 0o2775)
        os.makedirs(self.pfad("opt"), exist_ok=True)
        os.chmod(self.pfad("opt"), 0o755)
        r = self.lauf("release-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(oct(os.stat(self.pfad("usr", "local", "bin")).st_mode & 0o7777), "0o2775")
        self.assertEqual(oct(os.stat(self.pfad("opt")).st_mode & 0o7777), "0o755")

    def test_nichts_ausserhalb_der_eigenen_ordner(self):
        r = self.lauf("release-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        erlaubt = (("opt", "wamo-release"), ("usr", "local", "bin", "wamo-release"), ("var", "lib", "wamo-release"),
                   ("var", "log", "wamo-release"))
        alle = [Path(p).relative_to(self.wurzel).parts for p in Path(self.wurzel).rglob("*")]
        for teile in alle:
            ok = any(teile[:len(e)] == e or teile == e[:len(teile)] for e in erlaubt)
            self.assertTrue(ok, teile)
        self.assertFalse(os.path.exists(self.pfad("etc")))  # weder Unit noch tmpfiles im Probelauf

    def test_rueckweg_entfernt_und_sichert_audit(self):
        self.lauf("release-einrichten.sh")
        Path(self.pfad("var", "lib", "wamo-release", "zustand.json")).write_text("{}")
        Path(self.pfad("var", "log", "wamo-release", "audit.jsonl")).write_text("zeile\n")
        r = self.lauf("release-rueckweg.sh", "--entfernen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.pfad("opt", "wamo-release", "app")))
        self.assertFalse(os.path.exists(self.pfad("var", "lib", "wamo-release")))
        self.assertFalse(os.path.lexists(self.pfad("usr", "local", "bin", "wamo-release")))
        gesichert = sorted(os.listdir(self.pfad("var", "backups", "wamo-release")))
        self.assertEqual([n.split(".")[0] for n in gesichert], ["audit", "zustand"])
        audit = [n for n in gesichert if n.startswith("audit")][0]
        self.assertEqual(Path(self.pfad("var", "backups", "wamo-release", audit)).read_text(), "zeile\n")
        zweiter = self.lauf("release-rueckweg.sh", "--entfernen")  # wiederholbar
        self.assertEqual(zweiter.returncode, 0, zweiter.stdout + zweiter.stderr)

    def test_rueckweg_ohne_entfernen_laesst_dateien_stehen(self):
        self.lauf("release-einrichten.sh")
        r = self.lauf("release-rueckweg.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.pfad("opt", "wamo-release", "app", "wamo_release.py")))

    def test_fehlerfaelle(self):
        self.assertEqual(self.lauf("release-einrichten.sh", "--unsinn").returncode, 2)
        self.assertEqual(self.lauf("release-rueckweg.sh", "--unsinn").returncode, 2)
        for wurzel in ("relativ", "/", "../x", "//x", "/tmp//x", "/tmp/a/../b", "/tmp/a/..", "/tmp/./a", "/tmp/a b", "/tmp/x;y",
                       "/tmp/$(id)", "/tmp/x\ny"):
            with self.subTest(wurzel=wurzel):
                self.assertEqual(self.lauf("release-einrichten.sh", wurzel=wurzel).returncode, 1)
                self.assertEqual(self.lauf("release-rueckweg.sh", "--entfernen", wurzel=wurzel).returncode, 1)

    def test_geheimnisdatei_im_release_ordner_bricht_ab(self):
        for name in (".env", ".env.local", "geheim.env", "x.pem", "x.key", "release.env"):
            with self.subTest(name=name):
                datei = os.path.join(self.skripte, "release", name)
                Path(datei).write_text("X=1\n")
                r = self.lauf("release-einrichten.sh")
                self.assertEqual(r.returncode, 1)
                self.assertIn("Geheimnisdatei", r.stderr)
                self.assertFalse(os.path.exists(self.wurzel))
                os.unlink(datei)

    def test_syntaxfehler_bricht_ab(self):
        with open(os.path.join(self.skripte, "release", "release_automat.py"), "a") as f:
            f.write("\ndef kaputt(:\n")
        r = self.lauf("release-einrichten.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Syntaxfehler", r.stderr)
        self.assertFalse(os.path.exists(self.wurzel))

    def test_fehlende_datei_bricht_ab(self):
        os.unlink(os.path.join(self.skripte, "release_rauchtest.py"))
        r = self.lauf("release-einrichten.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Fehlt", r.stderr)
        self.assertFalse(os.path.exists(self.wurzel))

    def test_rueckweg_loescht_nur_feste_pfade(self):
        """Ein Symlink statt des Laufordners wird nie verfolgt oder gelöscht."""
        self.lauf("release-einrichten.sh")
        ziel = os.path.join(self.tmp.name, "wichtig")
        os.makedirs(ziel)
        Path(ziel, "datei").write_text("bleibt")
        os.makedirs(self.pfad("run"), exist_ok=True)
        os.symlink(ziel, self.pfad("run", "wamo-release"))
        r = self.lauf("release-rueckweg.sh", "--entfernen")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(Path(ziel, "datei").read_text(), "bleibt")

    def test_anker_dateien_bleiben_unberuehrt(self):
        anker = self.pfad("usr", "local", "lib", "wamo-anker")
        os.makedirs(anker)
        Path(anker, "wamo_anker.py").write_text("anker")
        konfig = self.pfad("etc", "wamo-anker")
        os.makedirs(konfig)
        Path(konfig, "selbstaenderung.aus").write_text("gesperrt")
        self.lauf("release-einrichten.sh")
        self.lauf("release-rueckweg.sh", "--entfernen")
        self.assertEqual(Path(anker, "wamo_anker.py").read_text(), "anker")
        self.assertEqual(Path(konfig, "selbstaenderung.aus").read_text(), "gesperrt")


if __name__ == "__main__":
    unittest.main()
