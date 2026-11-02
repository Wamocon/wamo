"""Tests der Einrichtung der Prüfer-Sandbox (infra/sokrates2/pruefer-einrichten.sh, pruefer-rueckweg.sh, pruefer/*, pruefer_rauchtest.py).

Die Skripte werden hier NICHT gegen das echte System ausgeführt, sondern im Probemodus in einem Wegwerf-Ordner
(WAMO_PRUEFER_WURZEL): kein root, kein systemd, kein Benutzer, kein Netz, nichts unter /usr, /etc, /opt oder /srv. Der Probelauf
braucht GNU-Werkzeuge und läuft deshalb nur unter Linux; die Syntaxprüfung (bash -n) und die Textprüfungen laufen überall, wo eine
Bash vorhanden ist. Die Einrichtung mit root, bubblewrap und systemd übt der Serverlauf aus (pruefer_rauchtest.py).

Ausführen:  uv run python -m pytest -q test_pruefer_skripte.py   (im Ordner voice-core/)
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
PRUEFER = INFRA / "pruefer"
QUELLE = INFRA / "release" / "pruefer"
LINUX = sys.platform.startswith("linux")
SKRIPTE = ("pruefer-einrichten.sh", "pruefer-rueckweg.sh", "pruefer/wamo-pruefer-lauf.sh")


def _bash():
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    for k in kandidaten:
        if k and os.path.exists(k):
            return k
    return None


BASH = _bash()


def _code(pfad: Path) -> str:
    return "\n".join(z for z in pfad.read_text(encoding="utf-8").splitlines() if not z.lstrip().startswith("#"))


class TextTests(unittest.TestCase):
    def test_ausgangsregeln_sandbox_weist_alles_ab(self):
        nft = (PRUEFER / "wamo-pruefer-ausgang.nft").read_text(encoding="utf-8")
        regeln = [z.strip() for z in nft.splitlines() if z.strip() and not z.lstrip().startswith("#")]
        self.assertIn("meta skuid wamo-pruefer jump sandbox", regeln)
        self.assertIn("meta skuid wamo-pruefer-proxy jump proxy", regeln)
        kette = nft[nft.index("chain sandbox"):nft.index("chain proxy")]
        self.assertNotIn("accept", kette)   # im Netz des Servers darf der Benutzer nichts: Die Sandbox hat ein eigenes Netz
        self.assertIn("reject", kette)

    def test_ausgangsregeln_proxy_sperrt_private_netze(self):
        nft = (PRUEFER / "wamo-pruefer-ausgang.nft").read_text(encoding="utf-8")
        kette = nft[nft.index("chain proxy"):]
        for netz in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10", "fc00::/7", "fe80::/10"):
            self.assertIn(netz, kette)
        self.assertNotIn("127.0.0.0/8", kette)  # Antworten an die Sandbox und der Resolver bleiben möglich

    def test_proxy_unit_ist_gehaertet(self):
        unit = _code(PRUEFER / "wamo-pruefer-proxy@.service")
        zeilen = [z.strip() for z in unit.splitlines() if z.strip()]
        for soll in ("User=wamo-pruefer-proxy", "Group=wamo-pruefer-proxy", "CapabilityBoundingSet=", "AmbientCapabilities=", "NoNewPrivileges=yes",
                     "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "PrivateDevices=yes", "MemoryDenyWriteExecute=yes",
                     "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX", "SystemCallFilter=@system-service", "SystemCallFilter=~@privileged @resources",
                     "IPAddressAllow=127.0.0.0/8 ::1/128", "ExecStartPre=/usr/bin/sha256sum -c --quiet SHA256SUMS",
                     "ExecStart=/usr/bin/python3 -I -B /opt/wamo-pruefer/app/pruefer_proxy.py --name %i --listen ${LISTEN} --unix /run/wamo-pruefer-proxy/%i.sock --erlaubt ${ERLAUBT}",
                     "RuntimeDirectory=wamo-pruefer-proxy",
                     "Requires=wamo-pruefer-ausgang.service", "EnvironmentFile=/etc/wamo-pruefer/proxy-%i.env"):
            self.assertIn(soll, zeilen)
        deny = [z for z in zeilen if z.startswith("IPAddressDeny=")][0]
        for netz in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7"):
            self.assertIn(netz, deny)
        self.assertFalse(any(v in unit for v in ("User=root", "PermissionsStartOnly", "ExecStart=+", "ExecStartPre=+")))

    def test_lauf_skript_ist_fest_verdrahtet(self):
        code = _code(PRUEFER / "wamo-pruefer-lauf.sh")
        for soll in ("--unshare-net", "pruefer_bruecke.py", "--ro-bind \"$PROXY_SOCKET\" /proxy/pakete.sock", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--new-session", "--cap-drop ALL", "--clearenv", "--remount-ro /",
                     "--ro-bind /usr /usr", "--ro-bind \"$APP/app\" \"$APP/app\"", "--ro-bind \"$APP/werkzeuge\" \"$APP/werkzeuge\"",
                     "--ro-bind \"$dir/ro/basis\" /basis", "IPAddressDeny=any", "IPAddressAllow=127.0.0.1 ::1", "MemorySwapMax=0", "RuntimeMaxSec",
                     "--uid=$BENUTZER", "flock -n 9", "pkill -KILL -u \"$BENUTZER\"", "--one-file-system"):
            self.assertIn(soll, code)
        for verboten in ("--share-net", "--bind / ", "--bind /etc", "--bind /home", "--bind /srv", "--bind /run", "--bind /var",
                         "--setenv ANTHROPIC", "--setenv TOKEN", "eval ", "--cap-add", "--dev-bind"):
            self.assertNotIn(verboten, code)
        self.assertNotIn("/etc/wamo", code.replace("/etc/wamo-anker/fremdverweise.txt", "").replace("/etc/wamo-pruefer", ""))
        self.assertNotIn("wamo-release", code)
        self.assertEqual(code.count("/etc/wamo-anker"), 1)   # nur die Festlegung des Ortes der Mustervorlage; alles andere nutzt die Variable

    def test_lauf_skript_gibt_nur_ein_gepruftes_ergebnis_weiter(self):
        code = _code(PRUEFER / "wamo-pruefer-lauf.sh")
        self.assertIn('"pruefstand" in d', code)
        self.assertIn("-le 2097152", code)
        self.assertIn('[ ! -L "$erg" ]', code)

    def test_sperrdatei_hat_fuer_jedes_paket_pruefsummen(self):
        sperre = (PRUEFER / "requirements-werkzeuge.lock").read_text(encoding="utf-8")
        bloecke = re.split(r"\n(?=[A-Za-z0-9_.-]+==)", sperre.strip())
        namen = []
        for b in bloecke:
            m = re.match(r"([A-Za-z0-9_.-]+)==([0-9][A-Za-z0-9.+!-]*)", b)
            self.assertIsNotNone(m, b[:60])
            namen.append(m[1].lower())
            self.assertGreaterEqual(len(re.findall(r"--hash=sha256:[0-9a-f]{64}", b)), 1, m[1])
        for pflicht in ("uv", "ruff", "pip-audit"):
            self.assertIn(pflicht, namen)
        self.assertNotIn("--index-url", sperre)
        self.assertNotIn("--extra-index-url", sperre)
        self.assertNotIn("git+", sperre)
        self.assertNotIn("http://", sperre)

    def test_node_ist_fest_und_wie_die_oberflaeche(self):
        text = (INFRA / "pruefer-einrichten.sh").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^NODE_VERSION=v22\.\d+\.\d+$")
        self.assertRegex(text, r"(?m)^NODE_SHA256=[0-9a-f]{64}$")
        self.assertIn("https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-arm64.tar.xz", text)
        self.assertIn("--proto '=https'", text)
        dockerfile = (INFRA / "wamo-ui" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("node:22", dockerfile)   # Prüfung und Produktion nutzen dieselbe Hauptfassung

    def test_proxyliste_ist_die_des_konzepts(self):
        text = (INFRA / "pruefer-einrichten.sh").read_text(encoding="utf-8")
        m = re.search(r"^ERLAUBT=(.*)$", text, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(sorted(m[1].split(",")), ["api.github.com", "files.pythonhosted.org", "fonts.googleapis.com", "fonts.gstatic.com", "github.com", "pypi.org",
                                                  "registry.npmjs.org"])   # die zwei Schrift-Hosts: next/font/google holt Roboto Condensed zur Bauzeit
        self.assertIn("LISTEN=127.0.0.1:18080", text)
        # der Claude-Proxy kennt genau einen Namen, und die Paketquellen kennen Claude nicht
        alle = re.findall(r"^ERLAUBT=(.*)$", text, re.M)
        self.assertEqual(alle, [m[1], "api.anthropic.com"])
        self.assertNotIn("anthropic", m[1])
        self.assertIn("LISTEN=127.0.0.1:18081", text)
        # die Sandbox der Prüfstrecke bindet den Claude-Proxy nie ein: Code ausführen und Token halten nie im selben Prozess
        lauf = (INFRA / "pruefer" / "wamo-pruefer-lauf.sh").read_text(encoding="utf-8")
        self.assertNotIn("18081", lauf)
        self.assertNotIn("claude.sock", lauf)

    def test_modulliste_des_skripts_ist_vollstaendig(self):
        text = (INFRA / "pruefer-einrichten.sh").read_text(encoding="utf-8")
        m = re.search(r"^PRUEFER_PY=\((.*?)\)", text, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(sorted(m[1].split()), sorted(p.name for p in QUELLE.glob("*.py")))

    def test_skripte_ohne_gedankenstriche_eval_und_cr(self):
        for name in SKRIPTE + ("pruefer_rauchtest.py", "pruefer/wamo-pruefer-ausgang.nft", "pruefer/wamo-pruefer-proxy@.service",
                               "pruefer/wamo-pruefer-ausgang.service", "pruefer/wamo-pruefer-bwrap.apparmor", "pruefer/pruefer.conf.beispiel"):
            text = (INFRA / name).read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), text, name)
            self.assertNotIn(chr(0x2013), text, name)
            self.assertNotIn("\r", text, name)
            if name.endswith(".sh"):
                self.assertNotIn("eval ", text, name)
        for name in SKRIPTE:
            self.assertIn("set -euo pipefail", (INFRA / name).read_text(encoding="utf-8"))

    def test_keine_woertlichen_zeilenumbrueche_im_code(self):
        """Ein wörtliches \\n zwischen Befehl und Argument ist ein Heredoc-Überbleibsel (siehe release-rueckweg.sh) und macht Befehle wirkungslos."""
        for name in SKRIPTE + ("release-einrichten.sh", "release-rueckweg.sh", "freigabe-einrichten.sh", "freigabe-rueckweg.sh"):
            for nr, zeile in enumerate(_code(INFRA / name).splitlines(), 1):
                self.assertIsNone(re.search(r"\s\\n\s", zeile), (name, nr, zeile))

    def test_apparmor_erlaubt_nur_die_kopie(self):
        text = (PRUEFER / "wamo-pruefer-bwrap.apparmor").read_text(encoding="utf-8")
        self.assertIn("profile wamo-pruefer-bwrap /usr/local/lib/wamo-pruefer/bwrap flags=(unconfined)", text)
        self.assertEqual(len(re.findall(r"^\s*userns,", text, re.M)), 1)

    def test_keine_geheimnisse_und_keine_fremden_dienste(self):
        for name in SKRIPTE:
            # einzige erlaubte Ausnahme: der feste Ort der Mustervorlage des Fremdverweis-Greps (Anker-Ordner, nicht im Repo)
            code = chr(10).join(z for z in _code(INFRA / name).splitlines() if not z.startswith("FREMD_STANDARD=/etc/wamo-anker/fremdverweise.txt"))
            for muster in (r"/etc/wamo[/\s\"]", r"/etc/wamo-anker", r"/etc/wamo-release", r"core\.env", r"password", r"\btee\b",
                           r"/usr/local/lib/wamo-anker", r"wamo-core\.service", r"wamo-geraete\.service", r"wamo-release\.service", r"/home/"):
                for zeile in code.splitlines():
                    self.assertIsNone(re.search(muster, zeile), (name, zeile))
            for zeile in code.splitlines():
                self.assertNotRegex(zeile, r"systemctl .*(wamo-anker|wamo-release|wamo-core|wamo-geraete|wamo-freigabe)", (name, zeile))

    def test_rauchtest_prueft_die_wichtigen_dinge(self):
        text = (INFRA / "pruefer_rauchtest.py").read_text(encoding="utf-8")
        for soll in ("db_supavisor_lan", "internet", "dns_udp", '("example.com:443"', '("192.168.178.136:55322"', "Zeitlimit", "untergeschobener Schlüssel",
                     "kaputter Test", "pip-audit", "link_im_baum", "test_inneres_und_internet_sind_nicht_erreichbar", "SHA256SUMS"):
            self.assertIn(soll, text)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class SyntaxTests(unittest.TestCase):
    def test_bash_n(self):
        for name in SKRIPTE:
            with self.subTest(skript=name):
                r = subprocess.run([BASH, "-n", str(INFRA / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck nicht vorhanden")
    def test_shellcheck(self):
        for name in SKRIPTE:
            with self.subTest(skript=name):
                r = subprocess.run(["shellcheck", "-S", "warning", str(INFRA / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout)

    def test_python_syntax(self):
        import ast
        ast.parse((INFRA / "pruefer_rauchtest.py").read_text(encoding="utf-8"))


@unittest.skipUnless(LINUX and BASH and os.geteuid() != 0, "Probelauf braucht Linux, GNU-Werkzeuge und läuft nie als root")
class ProbelaufTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pruefer-skripte-")
        self.addCleanup(self.tmp.cleanup)
        self.wurzel = os.path.join(self.tmp.name, "wurzel")
        self.skripte = os.path.join(self.tmp.name, "infra")
        ignorieren = shutil.ignore_patterns("__pycache__", "supabase-wamo", "wamo-ui", "claude-code")
        shutil.copytree(INFRA, self.skripte, ignore=ignorieren)
        os.chmod(self.skripte, 0o755)

    def lauf(self, skript, *args, wurzel=None):
        env = {"PATH": os.environ["PATH"], "WAMO_PRUEFER_WURZEL": self.wurzel if wurzel is None else wurzel, "WAMO_PRUEFER_PYTHON": sys.executable}
        return subprocess.run([BASH, os.path.join(self.skripte, skript), *args], capture_output=True, text=True, env=env, timeout=120)

    def pfad(self, *teile):
        return os.path.join(self.wurzel, *teile)

    def test_trockenlauf_legt_nichts_an(self):
        for skript, args in (("pruefer-einrichten.sh", ("--trocken",)), ("pruefer-rueckweg.sh", ("--entfernen", "--trocken"))):
            r = self.lauf(skript, *args)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("[trocken]", r.stdout)
        self.assertFalse(os.path.exists(self.wurzel))

    def test_einrichtung_baut_den_baum_und_ist_wiederholbar(self):
        r = self.lauf("pruefer-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        app = self.pfad("opt", "wamo-pruefer", "app")
        self.assertEqual(sorted(os.listdir(app)), sorted(["SHA256SUMS", "pruefer_rauchtest.py", "wamo-pruefer-lauf.sh", "wamo-pruefer-ausgang.nft",
                                                          *[p.name for p in QUELLE.glob("*.py")]]))
        summen = subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=app, capture_output=True, text=True)
        self.assertEqual(summen.returncode, 0, summen.stderr)
        self.assertTrue(os.access(os.path.join(app, "wamo-pruefer-lauf.sh"), os.X_OK))
        self.assertTrue(os.path.isdir(self.pfad("srv", "wamo-pruefer", "lauf")))
        self.assertFalse(any("__pycache__" in str(p) for p in Path(self.wurzel).rglob("*")))
        zweiter = self.lauf("pruefer-einrichten.sh")
        self.assertEqual(zweiter.returncode, 0, zweiter.stdout + zweiter.stderr)

    def test_aktualisierung_ersetzt_den_baum_und_raeumt_den_rueckfall_ab(self):
        self.lauf("pruefer-einrichten.sh")
        app = self.pfad("opt", "wamo-pruefer", "app")
        alt = Path(app, "SHA256SUMS").read_text()
        with open(os.path.join(self.skripte, "release", "pruefer", "pruefer_lauf.py"), "a") as f:
            f.write("\n# neue Fassung\n")
        r = self.lauf("pruefer-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(Path(app, "SHA256SUMS").read_text(), alt)
        self.assertIn("# neue Fassung", Path(app, "pruefer_lauf.py").read_text())
        self.assertFalse(os.path.exists(app + ".vorher"))

    def test_nichts_ausserhalb_der_eigenen_ordner(self):
        r = self.lauf("pruefer-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        erlaubt = (("opt", "wamo-pruefer"), ("srv", "wamo-pruefer"), ("etc", "wamo-pruefer"), ("usr", "local", "lib", "wamo-pruefer"))
        for p in Path(self.wurzel).rglob("*"):
            teile = p.relative_to(self.wurzel).parts
            self.assertTrue(any(teile[:len(e)] == e or teile == e[:len(teile)] for e in erlaubt), teile)
        self.assertFalse(os.path.exists(self.pfad("etc", "systemd")))  # keine Units im Probelauf

    def test_rueckweg_entfernt_und_ist_wiederholbar(self):
        self.lauf("pruefer-einrichten.sh")
        r = self.lauf("pruefer-rueckweg.sh", "--entfernen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for teile in (("opt", "wamo-pruefer"), ("srv", "wamo-pruefer"), ("etc", "wamo-pruefer")):
            self.assertFalse(os.path.exists(self.pfad(*teile)), teile)
        self.assertEqual(self.lauf("pruefer-rueckweg.sh", "--entfernen").returncode, 0)

    def test_rueckweg_ohne_entfernen_laesst_dateien_stehen(self):
        self.lauf("pruefer-einrichten.sh")
        r = self.lauf("pruefer-rueckweg.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.pfad("opt", "wamo-pruefer", "app", "pruefer_lauf.py")))

    def test_fehlerfaelle(self):
        self.assertEqual(self.lauf("pruefer-einrichten.sh", "--unsinn").returncode, 2)
        self.assertEqual(self.lauf("pruefer-rueckweg.sh", "--unsinn").returncode, 2)
        for wurzel in ("relativ", "/", "../x", "//x", "/tmp//x", "/tmp/a/../b", "/tmp/a/..", "/tmp/./a", "/tmp/a b", "/tmp/x;y", "/tmp/$(id)", "/tmp/x\ny"):
            with self.subTest(wurzel=wurzel):
                self.assertEqual(self.lauf("pruefer-einrichten.sh", wurzel=wurzel).returncode, 1)
                self.assertEqual(self.lauf("pruefer-rueckweg.sh", "--entfernen", wurzel=wurzel).returncode, 1)

    def test_geheimnisdatei_im_pruefer_ordner_bricht_ab(self):
        for ordner, name in (("pruefer", ".env"), ("pruefer", "geheim.env"), ("pruefer", "x.pem"), ("release/pruefer", "x.key"), ("release/pruefer", "pruefer.env")):
            with self.subTest(name=name):
                datei = os.path.join(self.skripte, ordner, name)
                Path(datei).write_text("X=1\n")
                r = self.lauf("pruefer-einrichten.sh")
                self.assertEqual(r.returncode, 1)
                self.assertIn("Geheimnisdatei", r.stderr)
                self.assertFalse(os.path.exists(self.wurzel))
                os.unlink(datei)

    def test_syntaxfehler_bricht_ab(self):
        with open(os.path.join(self.skripte, "release", "pruefer", "pruefer_scans.py"), "a") as f:
            f.write("\ndef kaputt(:\n")
        r = self.lauf("pruefer-einrichten.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Syntaxfehler", r.stderr)
        self.assertFalse(os.path.exists(self.wurzel))

    def test_fehlende_datei_bricht_ab(self):
        os.unlink(os.path.join(self.skripte, "pruefer", "wamo-pruefer-ausgang.nft"))
        r = self.lauf("pruefer-einrichten.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Fehlt", r.stderr)
        self.assertFalse(os.path.exists(self.wurzel))

    def test_rueckweg_loescht_nur_feste_pfade(self):
        """Ein Link statt des Arbeitsordners wird nie verfolgt oder gelöscht."""
        self.lauf("pruefer-einrichten.sh")
        ziel = os.path.join(self.tmp.name, "wichtig")
        os.makedirs(ziel)
        Path(ziel, "datei").write_text("bleibt")
        shutil.rmtree(self.pfad("srv", "wamo-pruefer"))
        os.symlink(ziel, self.pfad("srv", "wamo-pruefer"))
        r = self.lauf("pruefer-rueckweg.sh", "--entfernen")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(Path(ziel, "datei").read_text(), "bleibt")

    def test_fremde_dateien_bleiben_unberuehrt(self):
        anker = self.pfad("usr", "local", "lib", "wamo-anker")
        os.makedirs(anker)
        Path(anker, "wamo_anker.py").write_text("anker")
        release = self.pfad("opt", "wamo-release", "app")
        os.makedirs(release)
        Path(release, "wamo_release.py").write_text("release")
        self.lauf("pruefer-einrichten.sh")
        self.lauf("pruefer-rueckweg.sh", "--entfernen")
        self.assertEqual(Path(anker, "wamo_anker.py").read_text(), "anker")
        self.assertEqual(Path(release, "wamo_release.py").read_text(), "release")


if __name__ == "__main__":
    unittest.main()
