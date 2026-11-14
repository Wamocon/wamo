"""Tests der Einrichtungsskripte, der Unit, der Netzregel und der Sperrdatei des Freigabe-Dienstes (infra/sokrates2/freigabe-einrichten.sh,
freigabe-rueckweg.sh, anker/wamo-freigabe*.{service,nft}, pg/requirements-freigabe.lock, freigabe_rauchtest.py; Schritt 1.4b).

Textprüfungen laufen überall, der Probelauf der Skripte (Wegwerf-Ordner, ohne root, ohne systemd, ohne pip) und Teil A des Rauchtests
nur unter Linux.

Ausführen:  uv run python -m pytest -q test_freigabe_skripte.py   (in voice-core/)
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
ANKER = INFRA / "anker"
LINUX = sys.platform.startswith("linux")
SKRIPTE = ("freigabe-einrichten.sh", "freigabe-rueckweg.sh")


def _bash():
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    for k in kandidaten:
        if k and os.path.exists(k):
            return k
    return None


BASH = _bash()


class TextTests(unittest.TestCase):
    def zeilen(self, name):
        text = (ANKER / name).read_text(encoding="utf-8")
        return [z.strip() for z in text.splitlines() if z.strip() and not z.startswith("#")]

    def test_unit_ist_gehaertet_und_fest_verdrahtet(self):
        zeilen = self.zeilen("wamo-freigabe.service")
        for soll in ("User=wamo-anker", "Group=wamo-anker", "CapabilityBoundingSet=", "AmbientCapabilities=", "NoNewPrivileges=yes",
                     "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "PrivateDevices=yes", "MemoryDenyWriteExecute=yes",
                     "RestrictAddressFamilies=AF_UNIX AF_INET", "IPAddressDeny=any",
                     "IPAddressAllow=192.168.178.136/32 172.23.0.0/16 172.24.0.0/24", "SystemCallFilter=@system-service",
                     "SystemCallFilter=~@privileged", "SystemCallArchitectures=native", "RestrictNamespaces=yes", "LockPersonality=yes",
                     "RestrictSUIDSGID=yes", "ProtectKernelTunables=yes", "ReadWritePaths=/run/wamo-freigabe",
                     "ExecStartPre=/usr/bin/python3 -E -s -B /opt/wamo-freigabe/app/freigabe_dienst.py selbstpruefung",
                     "ExecStart=/usr/bin/python3 -E -s -B /opt/wamo-freigabe/app/freigabe_dienst.py dienst",
                     "Requires=wamo-freigabe-ausgang.service", "After=wamo-freigabe-ausgang.service", "LimitCORE=0",
                     "InaccessiblePaths=-/etc/wamo -/etc/wamo-geraete -/etc/wamo-release",
                     "Environment=WAMO_FREIGABE_SCHEMA=wamo_dev", "Environment=WAMO_FREIGABE_PEERS=0,1000",
                     "Environment=WAMO_FREIGABE_JWKS_URL=https://192.168.178.136:55443/auth/v1/.well-known/jwks.json",
                     "Environment=WAMO_FREIGABE_ISS=https://192.168.178.136:55443/auth/v1", "Environment=WAMO_FREIGABE_AUD=authenticated"):
            self.assertIn(soll, zeilen)
        self.assertEqual(len([z for z in zeilen if z.startswith(("ExecStart", "ExecStop", "ExecReload"))]), 2)
        for verboten in ("EnvironmentFile", "ExecStartPre=+", "ExecStart=+", "AmbientCapabilities=CAP", "AF_INET6", "AF_NETLINK",
                         "AF_PACKET", "User=root", "PermissionsStartOnly", "RuntimeDirectory"):
            self.assertFalse(any(verboten in z for z in zeilen), verboten)   # RuntimeDirectory: der Ordner bleibt (tmpfiles.d), sonst verliert Caddy den Mount
        self.assertEqual([z for z in zeilen if z.startswith("ReadWritePaths")], ["ReadWritePaths=/run/wamo-freigabe"])   # schreiben darf er nur dort
        self.assertFalse(any(z.startswith("InaccessiblePaths=") and "/etc/wamo-anker" in z for z in zeilen))  # das Zertifikat liegt dort

    def test_ausgangsregel_erlaubt_genau_zwei_ziele(self):
        regeln = self.zeilen("wamo-freigabe-ausgang.nft")
        self.assertIn("meta skuid wamo-anker jump wamo_freigabe", regeln)
        erlaubt = [z for z in regeln if " accept" in z and "policy" not in z]
        self.assertEqual(len(erlaubt), 1)
        self.assertTrue(erlaubt[0].startswith("ct original ip daddr 192.168.178.136 ct original proto-dst { 55322, 55443 } "), erlaubt[0])
        self.assertTrue(any(z.startswith("counter drop") for z in regeln))
        self.assertLess([i for i, z in enumerate(regeln) if " accept" in z][0], [i for i, z in enumerate(regeln) if "drop" in z][0])
        einheit = (ANKER / "wamo-freigabe-ausgang.service").read_text(encoding="utf-8")
        self.assertIn("Before=wamo-freigabe.service", einheit)
        self.assertIn("ExecStart=/usr/sbin/nft -f /etc/wamo-freigabe-ausgang/regeln.nft", einheit)

    def test_sperrdatei_hat_nur_gepruefte_pakete_mit_pruefsummen(self):
        sperre = (INFRA / "pg" / "requirements-freigabe.lock").read_text(encoding="utf-8")
        pakete = re.findall(r"^([A-Za-z0-9_.-]+)==([0-9.]+) \\\n", sperre, re.M)
        self.assertEqual(sorted(p[0] for p in pakete),
                         ["cffi", "cryptography", "psycopg", "psycopg-binary", "pycparser", "pyjwt", "typing-extensions"])
        for block in re.split(r"^(?=[A-Za-z0-9_.-]+==)", sperre, flags=re.M)[1:]:
            hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})\b", block)
            self.assertGreaterEqual(len(hashes), 1, block[:40])
            self.assertEqual(len(hashes), block.count("--hash="), block[:40])
        for verboten in ("--index-url", "--extra-index-url", "--find-links", "git+", "http"):
            self.assertNotIn(verboten, "\n".join(z for z in sperre.splitlines() if not z.startswith("#")))
        # psycopg und seine Hashes stimmen mit der Sperrdatei des Release-Dienstes überein (eine Fassung für beide)
        release = (INFRA / "pg" / "requirements.lock").read_text(encoding="utf-8")
        for h in re.findall(r"--hash=sha256:([0-9a-f]{64})", release):
            self.assertIn(h, sperre)

    def test_modullisten_des_skripts_sind_vollstaendig(self):
        text = (INFRA / "freigabe-einrichten.sh").read_text(encoding="utf-8")
        m = re.search(r"^MODULE=\((.*?)\)", text, re.M)
        pg = re.search(r"^PG_MODULE=\((.*?)\)", text, re.M)
        self.assertEqual(sorted(m[1].split()), ["freigabe.py", "freigabe_dienst.py"])
        self.assertEqual(sorted(pg[1].split()), ["freigabe_pg.py", "wamo_pg.py"])
        for n in m[1].split():
            self.assertTrue((ANKER / n).is_file(), n)
        for n in pg[1].split():
            self.assertTrue((INFRA / "pg" / n).is_file(), n)

    def test_texte_ohne_gedankenstriche_eval_und_cr(self):
        dateien = [INFRA / n for n in SKRIPTE] + [ANKER / "wamo-freigabe.service", ANKER / "wamo-freigabe-ausgang.nft",
                                                  ANKER / "wamo-freigabe-ausgang.service", ANKER / "freigabe_dienst.py",
                                                  INFRA / "freigabe_rauchtest.py", INFRA / "pg" / "requirements-freigabe.lock"]
        for datei in dateien:
            text = datei.read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), text, datei.name)
            self.assertNotIn(chr(0x2013), text, datei.name)
            self.assertNotIn("\r", text, datei.name)
            if datei.suffix == ".sh":
                self.assertNotIn("eval ", text)
                self.assertIn("set -euo pipefail", text)

    def test_prozess_nutzt_nur_erlaubte_wege(self):
        text = (ANKER / "freigabe_dienst.py").read_text(encoding="utf-8")
        for verboten in ("subprocess", "os.system", "os.popen", "shell=True", "eval(", "exec(", "pickle", "__import__", "importlib",
                         "verify=False", "CERT_NONE", "check_hostname = False", "algorithms=[\"none\"]", "HS256"):
            self.assertNotIn(verboten, text, verboten)
        umgebung = re.findall(r"os\.environ", text)
        self.assertEqual(len(umgebung), 1)  # genau ein Zugriff: die Konfiguration beim Start

    def test_nur_wamo_pfade_und_keine_geheimnisse_in_den_skripten(self):
        erlaubt = ("/opt/wamo-freigabe", "/etc/wamo-freigabe", "/etc/wamo-freigabe-ausgang", "/etc/wamo-anker/db.zugang",
                   "/etc/wamo-anker/wamo-ca.crt", "/etc/systemd/system/", "/run/wamo-freigabe", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
                   "/dev/null", "/nonexistent", "/etc", "/opt", "/run", "/tmp/freigabe-probe", "/etc/systemd/system")
        for name in SKRIPTE:
            code = "\n".join(z for z in (INFRA / name).read_text(encoding="utf-8").splitlines() if not z.lstrip().startswith("#"))
            for pfad in set(re.findall(r"(?<![\w$.{}])/(?:opt|etc|var|usr|run|dev|tmp|bin|sbin)/[A-Za-z0-9_./-]*", code)):
                self.assertTrue(pfad.startswith(erlaubt), (name, pfad))
            for zeile in code.splitlines():
                for muster in (r"password", r"\btee\b", r"core\.env", r"cat .*db\.zugang", r"wamo-release\.service", r"wamo-anker\.service"):
                    self.assertIsNone(re.search(muster, zeile), (name, zeile))


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


@unittest.skipUnless(LINUX and BASH and os.geteuid() != 0, "Probelauf braucht Linux, GNU-Werkzeuge und läuft nie als root")
class ProbelaufTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="freigabe-skripte-")
        self.addCleanup(self.tmp.cleanup)
        self.wurzel = os.path.join(self.tmp.name, "wurzel")
        self.skripte = os.path.join(self.tmp.name, "infra")
        shutil.copytree(INFRA, self.skripte, ignore=shutil.ignore_patterns("__pycache__", "supabase-wamo", "wamo-ui", "claude-code"))
        os.chmod(self.skripte, 0o755)

    def lauf(self, skript, *args, wurzel=None):
        env = {"PATH": os.environ["PATH"], "WAMO_FREIGABE_WURZEL": self.wurzel if wurzel is None else wurzel,
               "WAMO_FREIGABE_PYTHON": sys.executable}
        return subprocess.run([BASH, os.path.join(self.skripte, skript), *args], capture_output=True, text=True, env=env, timeout=120)

    def pfad(self, *teile):
        return os.path.join(self.wurzel, *teile)

    def test_trockenlauf_legt_nichts_an(self):
        for skript in SKRIPTE:
            r = self.lauf(skript, "--trocken")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("[trocken]", r.stdout)
        self.assertFalse(os.path.exists(self.wurzel))

    def test_einrichtung_baut_den_baum_und_ist_wiederholbar(self):
        r = self.lauf("freigabe-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        app = self.pfad("opt", "wamo-freigabe", "app")
        self.assertEqual(sorted(os.listdir(app)), sorted(["SHA256SUMS", "freigabe_dienst.py", "freigabe.py", "freigabe_pg.py", "wamo_pg.py",
                                                          "freigabe_rauchtest.py", "wamo-freigabe-ausgang.nft"]))
        summen = subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=app, capture_output=True, text=True)
        self.assertEqual(summen.returncode, 0, summen.stderr)
        self.assertFalse(any("__pycache__" in str(p) for p in Path(self.wurzel).rglob("*")))
        zweiter = self.lauf("freigabe-einrichten.sh")
        self.assertEqual(zweiter.returncode, 0, zweiter.stdout + zweiter.stderr)

    def test_der_installierte_baum_besteht_die_selbstpruefung(self):
        self.lauf("freigabe-einrichten.sh")
        sys.path.insert(0, str(ANKER))
        import freigabe_dienst as D
        app = self.pfad("opt", "wamo-freigabe", "app")
        self.assertEqual(D.installation_pruefen(app, None, None, besitzer=os.getuid()), [])
        Path(app, "freigabe.py").write_text("# verändert\n")
        self.assertEqual(D.installation_pruefen(app, None, None, besitzer=os.getuid()), ["Prüfsumme weicht ab: freigabe.py"])

    def test_nichts_ausserhalb_der_eigenen_ordner(self):
        self.assertEqual(self.lauf("freigabe-einrichten.sh").returncode, 0)
        for p in Path(self.wurzel).rglob("*"):
            teile = p.relative_to(self.wurzel).parts
            self.assertTrue(teile[:2] in (("opt", "wamo-freigabe"), ("etc", "wamo-freigabe")) or teile in (("opt",), ("etc",)), teile)
        self.assertEqual(os.listdir(self.pfad("etc", "wamo-freigabe")), [])  # im Probelauf keine Zugangsdatei, keine Units

    def test_rueckweg_entfernt_nur_die_eigenen_ordner(self):
        self.lauf("freigabe-einrichten.sh")
        fremd = self.pfad("opt", "anderes")
        os.makedirs(fremd)
        Path(fremd, "datei").write_text("bleibt")
        r = self.lauf("freigabe-rueckweg.sh", "--entfernen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.pfad("opt", "wamo-freigabe", "app")))
        self.assertEqual(Path(fremd, "datei").read_text(), "bleibt")
        self.assertEqual(self.lauf("freigabe-rueckweg.sh", "--entfernen").returncode, 0)  # wiederholbar

    def test_rueckweg_ohne_entfernen_laesst_dateien_stehen(self):
        self.lauf("freigabe-einrichten.sh")
        self.assertEqual(self.lauf("freigabe-rueckweg.sh").returncode, 0)
        self.assertTrue(os.path.exists(self.pfad("opt", "wamo-freigabe", "app", "freigabe_dienst.py")))

    def test_rueckweg_folgt_keinem_link(self):
        self.lauf("freigabe-einrichten.sh")
        ziel = os.path.join(self.tmp.name, "wichtig")
        os.makedirs(ziel)
        Path(ziel, "datei").write_text("bleibt")
        os.makedirs(self.pfad("run"), exist_ok=True)
        os.symlink(ziel, self.pfad("run", "wamo-freigabe"))
        self.assertNotEqual(self.lauf("freigabe-rueckweg.sh", "--entfernen").returncode, 0)
        self.assertEqual(Path(ziel, "datei").read_text(), "bleibt")

    def test_fehlerfaelle(self):
        self.assertEqual(self.lauf("freigabe-einrichten.sh", "--unsinn").returncode, 2)
        self.assertEqual(self.lauf("freigabe-rueckweg.sh", "--unsinn").returncode, 2)
        for wurzel in ("relativ", "/", "../x", "//x", "/tmp//x", "/tmp/a/../b", "/tmp/./a", "/tmp/a b", "/tmp/x;y", "/tmp/$(id)"):
            with self.subTest(wurzel=wurzel):
                self.assertEqual(self.lauf("freigabe-einrichten.sh", wurzel=wurzel).returncode, 1)
                self.assertEqual(self.lauf("freigabe-rueckweg.sh", "--entfernen", wurzel=wurzel).returncode, 1)

    def test_fehlende_datei_und_syntaxfehler_brechen_ab(self):
        with open(os.path.join(self.skripte, "anker", "freigabe.py"), "a") as f:
            f.write("\ndef kaputt(:\n")
        r = self.lauf("freigabe-einrichten.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Syntaxfehler", r.stderr)
        self.assertFalse(os.path.exists(self.wurzel))
        os.unlink(os.path.join(self.skripte, "pg", "requirements-freigabe.lock"))
        r = self.lauf("freigabe-einrichten.sh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Fehlt", r.stderr)

    def test_geheimnisdatei_im_anker_ordner_bricht_ab(self):
        for name in (".env", "geheim.env", "x.pem", "x.key"):
            with self.subTest(name=name):
                datei = os.path.join(self.skripte, "anker", name)
                Path(datei).write_text("X=1\n")
                r = self.lauf("freigabe-einrichten.sh")
                self.assertEqual(r.returncode, 1)
                self.assertIn("Geheimnisdatei", r.stderr)
                os.unlink(datei)


class CaddyRouteTests(unittest.TestCase):
    """Schritt 1.4c: Die Route POST /wamo/release/<id>/freigeben auf 55444 geht am Kern vorbei an den Freigabe-Dienst."""

    def site(self) -> str:
        return (INFRA / "wamo-caddy" / "kern" / "wamo-core.caddy").read_text(encoding="utf-8")

    def code(self, text: str) -> list[str]:
        return [z.strip() for z in text.splitlines() if z.strip() and not z.strip().startswith("#")]

    def test_route_geht_nur_fuer_den_einen_pfad_an_den_freigabe_dienst(self):
        z = self.code(self.site())
        self.assertIn("method POST OPTIONS", z)
        self.assertIn("path_regexp ^/wamo/release/[0-9a-f-]+/freigeben$", z)
        self.assertIn("handle @freigeben {", z)
        self.assertEqual([x for x in z if "freigabe.sock" in x], ["reverse_proxy unix//run/wamo-freigabe/freigabe.sock {"])
        # Der Rest geht an den Kern. Seit den Kern-Slots (Schritt 2.2) steht die Standardroute nicht mehr in der Site, sondern in der
        # Include-Datei slot.inc (vor der Umstellung slot.inc.legacy mit dem Socket der alten Unit, test_kern_slots.py prüft beide Fassungen).
        self.assertEqual(z.count("handle {"), 0)
        self.assertEqual([x for x in z if "core.sock" in x or "core-" in x], [])   # kein Kern-Socket fest in der Site
        self.assertLess(z.index("handle @freigeben {"), z.index("import /etc/caddy/kern/slot.inc"))   # die Freigabe greift vor dem Kern
        legacy = self.code((INFRA / "wamo-caddy" / "kern" / "slot.inc.legacy").read_text(encoding="utf-8"))
        self.assertEqual(legacy.count("handle {"), 1)
        self.assertEqual([x for x in legacy if "core.sock" in x], ["import kern_weiter /run/wamo-core/core.sock"])   # der Kern bleibt, wie er war
        self.assertNotIn("{8}", self.site())   # geschweifte Klammern im Muster würde Caddy als Platzhalter lesen

    def test_die_route_faengt_nichts_anderes_ab(self):
        text = self.site()
        # das Muster der Freigabe-Route, nicht das der Dateigrenzen in (kern_weiter), das in der Datei weiter oben steht
        pfad = re.search(r"@freigeben \{[^}]*?path_regexp (\S+)", text)[1]
        muster = re.compile(pfad)
        gut = "/wamo/release/0b9f2c1e-4a6d-4f0e-9a1b-2c3d4e5f6a7b/freigeben"
        self.assertTrue(muster.search(gut))
        for fremd in ("/wamo/release/x/freigeben/mehr", "/wamo/release//freigeben", "/wamo/release/../start/freigeben", "/wamo/api/dateien", "/start",
                      "/wamo/release/abc", "/wamo/release/ABC/freigeben", "/x/wamo/release/ab/freigeben"):
            self.assertIsNone(muster.search(fremd), fremd)

    def test_cors_nur_fuer_die_feste_oberflaeche_und_kleine_anfragen(self):
        text = self.site()
        m = re.search(r"header_regexp ui Origin (\S+)", text)
        ursprung = re.compile(m[1])
        for ok in ("https://192.168.178.136:55445", "https://sokrates2:55445", "https://spark-651f.local:55445"):
            self.assertTrue(ursprung.fullmatch(ok), ok)
        for fremd in ("https://192.168.178.136:55444", "http://192.168.178.136:55445", "https://evil.example:55445", "https://192x168y178z136:55445",
                      "https://192.168.178.136:55445.evil.example", "null", "*"):
            self.assertIsNone(ursprung.fullmatch(fremd), fremd)
        z = self.code(text)
        self.assertIn('Access-Control-Allow-Origin "{http.regexp.ui.0}"', z)   # nie ein fester Stern
        self.assertNotIn("Access-Control-Allow-Credentials", text)
        self.assertNotIn('Access-Control-Allow-Origin "*"', text)
        self.assertIn("max_size 4KB", z)

    def test_compose_bindet_den_ordner_nur_lesend_ein(self):
        z = self.code((INFRA / "wamo-caddy" / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertIn("- /run/wamo-freigabe:/run/wamo-freigabe:ro", z)
        self.assertIn("- /run/wamo-core:/run/wamo-core:ro", z)
        self.assertIn('user: "1000:1000"', z)   # deshalb: Peers 0 und 1000 und die Gruppe wamocon am Ordner

    def test_caddy_skript_prueft_den_ordner_und_sichert_die_alte_site(self):
        text = (INFRA / "wamo-core-caddy.sh").read_text(encoding="utf-8")
        self.assertIn('"wamo-anker:wamocon:2750"', text)
        self.assertIn("wamo-core.caddy.vorher", text)
        self.assertIn("--route-zurueck", text)
        self.assertNotIn(chr(0x2014), text)
        if BASH:
            self.assertEqual(subprocess.run([BASH, "-n", str(INFRA / "wamo-core-caddy.sh")], capture_output=True, text=True).returncode, 0)

    def test_einrichtung_legt_den_ordner_mit_gruppe_und_setgid_an(self):
        text = (INFRA / "freigabe-einrichten.sh").read_text(encoding="utf-8")
        self.assertIn("d /run/wamo-freigabe 2750 $BENUTZER wamocon -", text)
        self.assertIn("systemd-tmpfiles --create", text)
        rueck = (INFRA / "freigabe-rueckweg.sh").read_text(encoding="utf-8")
        self.assertIn("$TMPFILES", rueck)

    def test_peers_enthalten_nur_root_und_wamocon(self):
        unit = (ANKER / "wamo-freigabe.service").read_text(encoding="utf-8")
        self.assertIn("Environment=WAMO_FREIGABE_PEERS=0,1000", unit)
        self.assertNotRegex(unit, r"WAMO_FREIGABE_PEERS=[^\n]*\b(?!0\b|1000\b)\d+")


@unittest.skipUnless(LINUX and os.geteuid() != 0, "Rauchtest Teil A braucht Linux")
class RauchtestTests(unittest.TestCase):
    def test_teil_a_besteht(self):
        try:
            import jwt  # noqa: F401
        except ImportError:
            self.skipTest("PyJWT fehlt")
        r = subprocess.run([sys.executable, str(INFRA / "freigabe_rauchtest.py")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 fehlgeschlagen", r.stdout)
        self.assertIn("gültige Freigabe", r.stdout)


if __name__ == "__main__":
    unittest.main()
