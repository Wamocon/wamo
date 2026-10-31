"""Tests der Skripte für den Remote Desktop der Sparks (docs/remote-desktop.md, Stufen 0 bis 2):

  Stufe 0  infra/sparks/xrdp-einhegen.sh
  Stufe 1  infra/sokrates2/wamo-rd-einrichten.sh, wamo-rd-rueckweg.sh, wamo-rd/ (Compose, Caddy-Vorlage, Dockerfile), Regeln in wamo-firewall.sh
  Stufe 2  infra/sparks/wamo-rd-konto-einrichten.sh, wamo-rd-ziel-einrichten.sh

Die Skripte laufen hier NICHT gegen ein System. Sie laufen im Probemodus in einem Wegwerf-Ordner (WAMO_*_WURZEL) mit Attrappen
für alle Befehle mit Wirkung (ufw, nft, systemctl, useradd, docker, iptables ...): kein root, nichts unter /etc, /usr oder /var.
Die Attrappen schreiben jeden Aufruf in ein Protokoll, daran prüfen die Tests, was ein echter Lauf tun WÜRDE und was nie.
Der Probelauf braucht GNU-Werkzeuge und läuft deshalb nur unter Linux; bash -n, shellcheck und die Textprüfungen laufen überall,
wo eine Bash (bzw. shellcheck) vorhanden ist.

Ausführen:  uv run python -m pytest -q test_rd_skripte.py   (im Ordner voice-core/)
"""

import gzip
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOKRATES2 = REPO / "infra" / "sokrates2"
SPARKS = REPO / "infra" / "sparks"
LINUX = sys.platform.startswith("linux")


def _bash():
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    for k in kandidaten:
        if k and os.path.exists(k):
            return k
    return None


BASH = _bash()

# Alle Skripte dieser Funktion (Pfad relativ zum Repo). Stufe 1 und 2 ergänzen die Liste, sobald es sie gibt.
SKRIPTE = [p for p in (
    "infra/sparks/xrdp-einhegen.sh",
    "infra/sparks/wamo-rd-konto-einrichten.sh",
    "infra/sparks/wamo-rd-ziel-einrichten.sh",
    "infra/sokrates2/wamo-rd-einrichten.sh",
    "infra/sokrates2/wamo-rd-rueckweg.sh",
) if (REPO / p).exists()]

# Die Attrappe schreibt jeden Aufruf in $ATTRAPPE_LOG. Ausgabe und Rückgabewert kommen aus Dateien im Ordner
# $ATTRAPPE_DIR: NAME.UNTERBEFEHL.out / .rc (zuerst mit zwei Argumenten, dann mit einem, dann nur der Name).
ATTRAPPE = r"""#!/bin/sh
n="$(basename "$0")"
echo "$n $*" >> "$ATTRAPPE_LOG"
a1=""; a2=""
for a in "$@"; do
  case "$a" in -*) continue ;; esac
  if [ -z "$a1" ]; then a1="$a"; elif [ -z "$a2" ]; then a2="$a"; break; fi
done
for k in "$n.${a1}_${a2}" "$n.${a1}" "$n"; do
  if [ -f "$ATTRAPPE_DIR/$k.out" ]; then cat "$ATTRAPPE_DIR/$k.out"; fi
  if [ -f "$ATTRAPPE_DIR/$k.rc" ] || [ -f "$ATTRAPPE_DIR/$k.out" ]; then
    [ -f "$ATTRAPPE_DIR/$k.rc" ] && exit "$(cat "$ATTRAPPE_DIR/$k.rc")"
    exit 0
  fi
done
exit 0
"""

SESMAN_INI = """[Globals]
ListenAddress=127.0.0.1
ListenPort=3350
EnableUserWindowManager=true

[Security]
AllowRootLogin=true
MaxLoginRetry=4
AlwaysGroupCheck=false

[Sessions]
X11DisplayOffset=10
MaxSessions=50
KillDisconnected=false
IdleTimeLimit=0
"""


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Werkstatt(unittest.TestCase):
    """Wegwerf-Ordner mit Wurzel, Attrappen und Protokoll."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rd-skripte-")
        self.addCleanup(self.tmp.cleanup)
        self.wurzel = Path(self.tmp.name) / "wurzel"
        self.wurzel.mkdir()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.att = Path(self.tmp.name) / "att"
        self.att.mkdir()
        self.log = Path(self.tmp.name) / "aufrufe.log"
        self.log.write_text("")

    def attrappe(self, *namen):
        for n in namen:
            p = self.bin / n
            p.write_text(ATTRAPPE)
            p.chmod(0o755)

    def ausgabe(self, schluessel, text="", rc=None):
        (self.att / f"{schluessel}.out").write_text(text)
        if rc is not None:
            (self.att / f"{schluessel}.rc").write_text(str(rc))

    def aufrufe(self):
        return self.log.read_text().splitlines()

    def lauf(self, skript, *args, extra=None, pfad_nur_attrappen=False):
        pfad = str(self.bin) + os.pathsep + os.environ["PATH"]
        env = {"PATH": pfad, "ATTRAPPE_LOG": str(self.log), "ATTRAPPE_DIR": str(self.att),
               "HOME": self.tmp.name, "LC_ALL": "C", **(extra or {})}
        return subprocess.run([BASH, str(REPO / skript), *args], capture_output=True, text=True, env=env, timeout=120)

    def p(self, *teile):
        return self.wurzel.joinpath(*[t.lstrip("/") for t in teile])


# ====================================================================================== Allgemeine Textprüfungen
class AllgemeinTests(unittest.TestCase):
    def test_skripte_ohne_gedankenstriche_eval_cr_und_selbsttaetiges_update(self):
        self.assertTrue(SKRIPTE)
        for name in SKRIPTE:
            text = (REPO / name).read_text(encoding="utf-8")
            code = "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))
            self.assertNotIn(chr(0x2014), text, name)
            self.assertNotIn(chr(0x2013), text, name)
            self.assertNotIn("\r", text, name)
            self.assertIn("set -euo pipefail", text, name)
            self.assertIsNone(re.search(r"\beval\b", code), name)
            self.assertIsNone(re.search(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|\$\w+\s)", code), name)
            # Nichts wird von selbst aktualisiert (Hinweise in echo-Zeilen sind erlaubt, Aufrufe nicht)
            ohne_echo = "\n".join(z for z in code.splitlines() if not z.lstrip().startswith("echo"))
            self.assertIsNone(re.search(r"\bapt(-get)?\s+(install|upgrade|dist-upgrade|full-upgrade)\b", ohne_echo), name)
            self.assertIsNone(re.search(r"\bunattended", code), name)
            # kein Passwort in Argumenten oder Ausgabe
            self.assertIsNone(re.search(r"echo[^\n]*\$\{?(PASSWORT|PW|passwort)", code), name)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class SyntaxTests(unittest.TestCase):
    def test_bash_n(self):
        for name in SKRIPTE + ["infra/sokrates2/wamo-firewall.sh"]:
            with self.subTest(skript=name):
                r = subprocess.run([BASH, "-n", str(REPO / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck nicht vorhanden")
    def test_shellcheck(self):
        for name in SKRIPTE:
            with self.subTest(skript=name):
                r = subprocess.run(["shellcheck", "-S", "warning", str(REPO / name)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout)


# ====================================================================================== Stufe 0: xrdp-einhegen.sh
XRDP = "infra/sparks/xrdp-einhegen.sh"


@unittest.skipUnless(LINUX and BASH, "Probelauf braucht Linux und GNU-Werkzeuge")
class XrdpEinhegenTests(Werkstatt):
    def setUp(self):
        super().setUp()
        self.attrappe("ufw", "nft", "systemctl", "dpkg-query", "apt-cache", "apt", "ss", "pgrep")
        os.makedirs(self.p("etc/xrdp"))
        os.makedirs(self.p("etc/ufw"))
        os.makedirs(self.p("usr/share/doc/xrdp"))
        self.sesman = self.p("etc/xrdp/sesman.ini")
        self.sesman.write_text(SESMAN_INI)
        self.sesman.chmod(0o640)
        self.p("etc/xrdp/xrdp.ini").write_text("[Globals]\nsecurity_layer=negotiate\n[Channels]\ncliprdr=true\nrdpdr=true\n")
        self.p("etc/ufw/user.rules").write_text("*filter\n-A ufw-user-input -s 192.168.178.0/24 -j ACCEPT\nCOMMIT\n")
        self.ausgabe("dpkg-query", "0.9.24-4ubuntu1+esm1")
        self.ausgabe("apt-cache.policy", "xrdp:\n  Installed: 0.9.24-4ubuntu1+esm1\n  Candidate: 0.9.24-4ubuntu1+esm1\n")
        self.ausgabe("ss", "LISTEN 0 2 *:3389 *:*\n")
        self.ausgabe("nft.list_table", "", rc=1)  # eigene Tabelle steht noch nicht

    def xrdp(self, *args, extra=None):
        return self.lauf(XRDP, *args, extra={"WAMO_XRDP_WURZEL": str(self.wurzel), **(extra or {})})

    def ufw_aktiv(self):
        self.ausgabe("ufw.status", "Status: active\n\nTo   Action  From\n3389 ALLOW  Anywhere\n")
        self.ausgabe("ufw.status_numbered", "Status: active\n\n     To      Action  From\n[ 1] 22/tcp   ALLOW IN  Anywhere\n")

    def test_trockenlauf_aendert_nichts_und_ruft_nichts_mit_wirkung_auf(self):
        self.ufw_aktiv()
        vorher = sha(self.sesman)
        r = self.xrdp("--trocken")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[trocken]", r.stdout)
        self.assertEqual(sha(self.sesman), vorher)
        self.assertEqual(sorted(os.listdir(self.p("etc/xrdp"))), ["sesman.ini", "xrdp.ini"])
        self.assertFalse(self.p("var").exists())
        self.assertFalse(self.p("etc/wamo-xrdp").exists())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(ufw (insert|allow|deny|delete|enable)|nft -f|systemctl (enable|restart|disable|daemon-reload))", z)
        # die geplanten Regeln werden gezeigt
        self.assertIn("ufw insert 1 allow from 192.168.178.136 to any port 3389 proto tcp comment wamo-xrdp", r.stdout)
        self.assertIn("AllowRootLogin=false", r.stdout)

    def test_einhegen_mit_ufw_setzt_nur_zwei_regeln_und_eine_zeile(self):
        self.ufw_aktiv()
        r = self.xrdp()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        neu = self.sesman.read_text()
        erwartet = SESMAN_INI.replace("AllowRootLogin=true", "AllowRootLogin=false")
        self.assertEqual(neu, erwartet)
        self.assertEqual(stat.S_IMODE(os.stat(self.sesman).st_mode), 0o640)
        sicherungen = [f for f in os.listdir(self.p("etc/xrdp")) if f.startswith("sesman.ini.vor-wamo-")]
        self.assertEqual(len(sicherungen), 1)
        self.assertEqual(self.p("etc/xrdp", sicherungen[0]).read_text(), SESMAN_INI)
        self.assertIn("allowrootlogin_vorher=true", self.p("var/lib/wamo-xrdp/zustand").read_text())
        aufrufe = self.aufrufe()
        self.assertIn("ufw insert 1 allow from 192.168.178.136 to any port 3389 proto tcp comment wamo-xrdp", aufrufe)
        self.assertIn("ufw insert 2 deny proto tcp from any to any port 3389 comment wamo-xrdp", aufrufe)
        for z in aufrufe:
            self.assertNotRegex(z, r"^(apt(-get)? (install|upgrade|update)|nft -f|systemctl (restart|enable|stop))", z)
            self.assertNotIn("reset", z)
            self.assertNotIn("disable", z)
        self.assertTrue(any(f.startswith("user.rules.") for f in os.listdir(self.p("var/backups/wamo-xrdp"))))

    def test_zweiter_lauf_aendert_nichts_mehr(self):
        self.ufw_aktiv()
        self.assertEqual(self.xrdp().returncode, 0)
        self.ausgabe("ufw.show_added", "ufw allow from 192.168.178.136 to any port 3389 proto tcp comment 'wamo-xrdp'\n"
                                       "ufw deny proto tcp from any to any port 3389 comment 'wamo-xrdp'\n")
        self.log.write_text("")
        n_sicherungen = len(os.listdir(self.p("etc/xrdp")))
        r = self.xrdp()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(os.listdir(self.p("etc/xrdp"))), n_sicherungen)
        self.assertFalse([z for z in self.aufrufe() if z.startswith(("ufw insert", "ufw allow", "ufw deny"))])
        self.assertIn("schon gesetzt", r.stdout)
        self.assertIn("Regeln stehen schon", r.stdout)

    def test_ohne_ufw_eigene_nft_tabelle_und_unit(self):
        r = self.xrdp()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        regeln = self.p("etc/wamo-xrdp/sperre.nft").read_text()
        self.assertIn("table inet wamo_xrdp", regeln)
        self.assertIn("ip saddr { 127.0.0.1, 192.168.178.136 } accept", regeln)
        self.assertIn("tcp dport 3389 counter drop", regeln)
        # nur die eigene Tabelle wird gelöscht, nie flush ruleset
        self.assertNotIn("flush", regeln)
        self.assertEqual(regeln.count("delete table"), 1)
        unit = self.p("etc/systemd/system/wamo-xrdp-sperre.service").read_text()
        self.assertIn("ExecStart=/usr/sbin/nft -f /etc/wamo-xrdp/sperre.nft", unit)
        aufrufe = self.aufrufe()
        self.assertTrue(any(z.startswith("nft -c -f") for z in aufrufe))
        self.assertIn("systemctl enable --now wamo-xrdp-sperre.service", aufrufe)
        for z in aufrufe:
            self.assertNotIn("flush", z)
            self.assertNotRegex(z, r"^nft (add|delete|insert)", z)

    def test_zurueck_stellt_den_alten_stand_her(self):
        self.assertEqual(self.xrdp().returncode, 0)
        self.assertNotEqual(self.sesman.read_text(), SESMAN_INI)
        self.log.write_text("")
        r = self.xrdp("--zurueck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.sesman.read_text(), SESMAN_INI)
        self.assertFalse(self.p("etc/wamo-xrdp/sperre.nft").exists())
        self.assertFalse(self.p("etc/systemd/system/wamo-xrdp-sperre.service").exists())
        self.assertFalse(self.p("var/lib/wamo-xrdp/zustand").exists())
        self.assertIn("systemctl disable --now wamo-xrdp-sperre.service", self.aufrufe())
        self.assertTrue([f for f in os.listdir(self.p("etc/xrdp")) if f.startswith("sesman.ini.vor-wamo-")])

    def test_zurueck_mit_ufw_entfernt_nur_die_eigenen_regeln(self):
        self.ufw_aktiv()
        self.assertEqual(self.xrdp().returncode, 0)
        self.ausgabe("ufw.show_added", "ufw allow from 192.168.178.136 to any port 3389 proto tcp comment 'wamo-xrdp'\n")
        self.log.write_text("")
        self.assertEqual(self.xrdp("--zurueck").returncode, 0)
        loesch = [z for z in self.aufrufe() if z.startswith("ufw --force delete")]
        self.assertEqual(loesch, ["ufw --force delete allow from 192.168.178.136 to any port 3389 proto tcp",
                                  "ufw --force delete deny proto tcp from any to any port 3389"])

    def test_abschnitt_ohne_zeile_und_datei_ohne_abschnitt(self):
        self.sesman.write_text("[Globals]\nA=1\n\n[Security]\nMaxLoginRetry=4\n\n[Sessions]\nB=2\n")
        self.assertEqual(self.xrdp().returncode, 0)
        self.assertEqual(self.sesman.read_text(),
                         "[Globals]\nA=1\n\n[Security]\nMaxLoginRetry=4\n\nAllowRootLogin=false\n[Sessions]\nB=2\n")
        self.sesman.write_text("[Globals]\nA=1\n")
        self.assertEqual(self.xrdp().returncode, 0)
        self.assertEqual(self.sesman.read_text(), "[Globals]\nA=1\n\n[Security]\nAllowRootLogin=false\n")
        self.assertIn("allowrootlogin_vorher=<fehlt>", self.p("var/lib/wamo-xrdp/zustand").read_text())
        self.assertEqual(self.xrdp("--zurueck").returncode, 0)
        self.assertNotIn("AllowRootLogin", self.sesman.read_text())

    def test_update_hinweis_ohne_selbsttaetiges_update(self):
        self.ausgabe("apt-cache.policy", "xrdp:\n  Installed: 0.9.24-4ubuntu1+esm1\n  Candidate: 0.9.24-4ubuntu1+esm2\n")
        r = self.xrdp("--status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("UPDATE STEHT AN", r.stdout)
        self.assertIn("CVE-2025-68670", r.stdout)
        self.assertIn("apt-get install --only-upgrade xrdp", r.stdout)
        self.assertEqual([z for z in self.aufrufe() if re.match(r"^apt(-get)? (install|upgrade|update)", z)], [])
        self.assertIn("xrdp 0.9.24-4ubuntu1+esm1", r.stdout)

    def test_changelog_nennt_die_cve_oder_nicht(self):
        cl = self.p("usr/share/doc/xrdp/changelog.Debian.gz")
        cl.write_bytes(gzip.compress(b"xrdp (0.9.24-4ubuntu1+esm1) noble; urgency=high\n * SECURITY UPDATE: CVE-2025-68670\n"))
        self.assertIn("nennt CVE-2025-68670: ja", self.xrdp("--status").stdout)
        cl.write_bytes(gzip.compress(b"xrdp (0.9.24-4) noble; urgency=low\n * nichts\n"))
        self.assertIn("NICHT belegt", self.xrdp("--status").stdout)

    def test_neustart_nur_ohne_laufende_sitzung(self):
        self.ausgabe("pgrep", "4711\n", rc=0)
        r = self.xrdp("--neustart")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("systemctl restart xrdp-sesman", self.aufrufe())
        self.assertIn("xrdp-sesman", r.stdout)
        self.ausgabe("pgrep", "", rc=1)
        self.xrdp("--neustart")
        self.assertIn("systemctl restart xrdp-sesman", self.aufrufe())

    def test_falsche_aufrufe(self):
        self.assertEqual(self.xrdp("--unbekannt").returncode, 2)
        self.assertEqual(self.xrdp("--trocken", extra={"WAMO_XRDP_QUELLE": "kein;ip"}).returncode, 2)
        self.assertEqual(self.xrdp("--trocken", extra={"WAMO_XRDP_QUELLE": "192.168.178.136; rm"}).returncode, 2)
        self.sesman.unlink()
        self.assertNotEqual(self.xrdp().returncode, 0)

    def test_quelladresse_ist_umlenkbar_und_default_sokrates2(self):
        self.ufw_aktiv()
        self.assertEqual(self.xrdp("--trocken", extra={"WAMO_XRDP_QUELLE": "10.1.2.3"}).returncode, 0)
        r = self.xrdp("--trocken")
        self.assertIn("192.168.178.136", r.stdout)
        self.assertNotIn("10.1.2.3", r.stdout)


# ====================================================================================== Stufe 1: Gateway, Caddy, Firewall
RD = SOKRATES2 / "wamo-rd"


def _ohne_kommentare(text):
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


class GatewayDateiTests(unittest.TestCase):
    def setUp(self):
        self.compose = _ohne_kommentare((RD / "docker-compose.yml").read_text(encoding="utf-8"))

    def test_images_nur_ueber_digest(self):
        bilder = re.findall(r"^\s+image:\s+(\S+)", self.compose, flags=re.M)
        self.assertEqual(len(bilder), 2)
        for b in bilder:
            self.assertRegex(b, r"^guacamole/(guacd|guacamole):1\.6\.0@sha256:[0-9a-f]{64}$")

    def test_haertung_wie_wamo_ui(self):
        for soll in ("pull_policy: never", "read_only: true", "- ALL", "no-new-privileges:true", "init: true",
                     "restart: unless-stopped", "mem_limit: 768m", "mem_limit: 256m", "pids_limit: 256", "pids_limit: 128",
                     "cpus: 1.0", "noexec", "max-size", 'user: "${WAMO_RD_UID:-65534}:${WAMO_RD_GID:-65534}"'):
            self.assertIn(soll, self.compose)
        self.assertEqual(self.compose.count("cap_drop:"), 1)  # im gemeinsamen Anker beider Dienste
        self.assertEqual(self.compose.count("<<: *wamo-rd"), 2)
        for verboten in ("privileged", "network_mode", "pid:", "ipc:", "docker.sock", "cap_add", "ports:", "expose:", "devices:",
                         "extra_hosts", "host.docker.internal", "JSON_SECRET_KEY:", "POSTGRESQL_", "MYSQL_", "LDAP_", "env_file",
                         "TOTP_", "OPENID_", "SAML_"):
            self.assertNotIn(verboten, self.compose, verboten)

    def test_anmeldung_nur_ueber_json_aus_einer_adresse(self):
        self.assertIn("JSON_SECRET_KEY_FILE: /run/wamo-rd/json.key", self.compose)
        self.assertIn("${WAMO_RD_KEY:-/etc/wamo-rd/json.key}:/run/wamo-rd/json.key:ro", self.compose)
        self.assertRegex(self.compose, r"JSON_TRUSTED_NETWORKS: \$\{WAMO_RD_VERTRAUT:-172\.26\.0\.10/32\}")
        self.assertIn('BAN_ENABLED: "true"', self.compose)
        # echte Absenderadresse hinter Caddy; vertraut wird nur Caddy (feste Adresse), nicht jedes private Netz
        self.assertIn('REMOTE_IP_VALVE_ENABLED: "true"', self.compose)
        self.assertIn("REMOTE_IP_VALVE_INTERNAL_PROXIES: ${WAMO_RD_PROXY_REGEX:-172[.]26[.]0[.]2}", self.compose)
        # ein Bind-Mount, nämlich der Schlüssel (lesend)
        self.assertEqual(self.compose.count("volumes:"), 1)
        self.assertEqual(len(re.findall(r"^\s+- \$\{WAMO_RD_KEY:[^}]*\}:/run/wamo-rd/json\.key:ro$", self.compose, flags=re.M)), 1)

    def test_netze(self):
        self.assertRegex(self.compose, r"guacd:\s+name: \$\{WAMO_RD_GUACD_NETZ:-wamo-rd-guacd-netz\}\s+internal: true")
        self.assertIn("172.26.1.0/29", self.compose)
        self.assertIn("172.27.0.0/24", self.compose)
        self.assertRegex(self.compose, r"rd:\s+name: \$\{WAMO_RD_NETZ:-wamo-rd-netz\}\s+external: true")
        ziel = self.compose.split("  ziel:\n", 2)[-1]
        self.assertNotIn("internal", ziel)
        # guacd allein hat das Netz mit Ausgang; die Webanwendung hängt nur in rd und guacd
        web = self.compose.split("  web:\n")[1].split("\nnetworks:")[0]
        self.assertNotIn("ziel", web)
        guacd = self.compose.split("  guacd:\n")[1].split("  web:\n")[0]
        self.assertIn("- ziel", guacd)
        self.assertNotIn("- rd", guacd)

    def test_caddy_compose_veroeffentlicht_genau_einen_neuen_port_und_ein_netz(self):
        c = _ohne_kommentare((SOKRATES2 / "wamo-caddy" / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertIn('"0.0.0.0:55446:55446"', c)
        self.assertRegex(c, r"rd:\s+name: wamo-rd-netz\s+internal: true\s+driver_opts:\s+com\.docker\.network\.bridge\.inhibit_ipv4: \"true\"\s+ipam:\s+config:\s+- subnet: 172\.26\.0\.0/24")
        self.assertRegex(c, r"networks:\s+default: \{\}\s+ui: \{\}\s+rd:\s+(#[^\n]*\s+)?ipv4_address: 172\.26\.0\.2")
        self.assertEqual(len(re.findall(r'"0\.0\.0\.0:\d+:\d+"', c)), 4)

    def test_caddy_vorlage(self):
        v = (RD / "wamo-rd.caddy.vorlage").read_text(encoding="utf-8")
        code = _ohne_kommentare(v)
        for platzhalter in ("__ADRESSE__", "__TLS__", "__WSS__"):
            self.assertIn(platzhalter, code)
        self.assertIn("frame-ancestors 'none'", code)
        self.assertIn("default-src 'none'", code)
        self.assertIn("script-src 'self' 'unsafe-eval';", code)  # AngularJS von Guacamole braucht eval, sonst bleibt die Seite leer
        self.assertNotIn("script-src 'self' 'unsafe-inline'", code)
        self.assertEqual(code.count("unsafe-eval"), 1)
        self.assertIn("path /app.css", code)
        for kopf in ("Referrer-Policy", "X-Content-Type-Options", "X-Frame-Options", "Permissions-Policy", "Cross-Origin-Opener-Policy",
                     "Cross-Origin-Resource-Policy", "Cache-Control"):
            self.assertIn(">" + kopf, code)
        self.assertIn("clipboard-read=()", code)
        self.assertIn("not method GET HEAD POST DELETE", code)
        self.assertNotIn("Access-Control", code)
        self.assertNotIn("iframe", code.lower())
        # die Abweisungen stehen VOR dem Durchreichen (route erzwingt die Reihenfolge)
        self.assertLess(code.index("respond @methode"), code.index("reverse_proxy"))
        self.assertIn("route {", code)

    def test_beispiel_zielliste_ist_leer(self):
        b = (RD / "ziele.toml.beispiel").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^[^#\n]*adresse", b, flags=re.M))
        self.assertIsNone(re.search(r"(?i)passw|kennwort\s*=|secret\s*=|token\s*=", _ohne_kommentare(b)))

    def test_alle_dateien_ohne_gedankenstriche_und_cr(self):
        for f in list(RD.iterdir()) + [SOKRATES2 / "wamo_rd_rauchtest.py", SOKRATES2 / "wamo-firewall.sh"]:
            t = f.read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), t, f.name)
            self.assertNotIn(chr(0x2013), t, f.name)
            self.assertNotIn("\r", t, f.name)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class FirewallRegelnTests(Werkstatt):
    """wamo-firewall.sh regeln gibt aus, was start setzen würde (ohne root, ohne iptables)."""

    def regeln(self, ziele_text=None):
        if ziele_text is not None:
            (self.wurzel / "ziele.toml").write_text(ziele_text, encoding="utf-8")
        env = {"PATH": os.environ["PATH"], "WAMO_FW_RD_ZIELE": str(self.wurzel / "ziele.toml"),
               "WAMO_FW_CONF": str(self.wurzel / "gibt-es-nicht.conf")}
        return subprocess.run([BASH, str(SOKRATES2 / "wamo-firewall.sh"), "regeln"], capture_output=True, text=True, env=env, timeout=60)

    def test_port_55446_wie_die_anderen_lan_ports(self):
        r = self.regeln()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-A WAMO-FW -p tcp -m conntrack --ctdir ORIGINAL --ctorigdstport 55446 -j WAMO-FW-LAN", r.stdout)
        # die bisherigen Ports bleiben unverändert
        for port in (55443, 55444, 55445, 55322, 56543):
            self.assertIn(f"--ctorigdstport {port} -j WAMO-FW-LAN", r.stdout)
        self.assertIn("--ctorigdstport 55321 -j WAMO-FW-HTTP", r.stdout)

    def test_ohne_ziele_geht_nichts_hinaus(self):
        r = self.regeln()  # Datei fehlt
        self.assertEqual(r.returncode, 0, r.stderr)
        rd = [z for z in r.stdout.splitlines() if z.startswith("-A WAMO-FW-RD ")]
        self.assertEqual(rd[0], "-A WAMO-FW-RD ! -i br-+ -j DROP")
        self.assertEqual(rd[-1], "-A WAMO-FW-RD -j DROP")
        self.assertFalse([z for z in rd if "RETURN" in z])
        self.assertIn("-A WAMO-FW -s 172.27.0.0/24 -j WAMO-FW-RD", r.stdout)
        r = self.regeln("# nur Kommentar\n")
        self.assertFalse([z for z in r.stdout.splitlines() if z.startswith("-A WAMO-FW-RD ") and "RETURN" in z])

    def test_ziele_werden_gelesen_und_ungueltige_ausgelassen(self):
        r = self.regeln('[[ziel]]\nname = "a"\nadresse = "192.168.178.136"  # Sokrates-2\n[[ziel]]\nadresse="10.0.0.5"\n'
                        '# adresse = "1.1.1.1"\nadresse = "300.1.1.1"\nadresse = "127.0.0.1"\nadresse = "0.0.0.0"\nadresse = "a.b.c.d"\n'
                        'adresse = "1.2.3.4; reboot"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        rd = [z for z in r.stdout.splitlines() if z.startswith("-A WAMO-FW-RD ") and "RETURN" in z]
        self.assertEqual(rd, ["-A WAMO-FW-RD -d 192.168.178.136/32 -p tcp --dport 3389 -j RETURN",
                              "-A WAMO-FW-RD -d 10.0.0.5/32 -p tcp --dport 3389 -j RETURN"])
        self.assertIn("ungültig", r.stderr)
        self.assertNotIn("1.1.1.1", r.stdout)
        self.assertNotIn("reboot", r.stdout)

    def test_hoechstens_acht_ziele(self):
        r = self.regeln("".join(f'adresse = "10.9.9.{n}"\n' for n in range(1, 13)))
        self.assertEqual(len([z for z in r.stdout.splitlines() if z.startswith("-A WAMO-FW-RD -d ")]), 8)

    def test_host_kette_laesst_nur_3389_zur_entscheidung_durch(self):
        r = self.regeln()
        rdin = [z for z in r.stdout.splitlines() if z.startswith("-A WAMO-FW-RDIN ")]
        self.assertEqual(rdin[0], "-A WAMO-FW-RDIN -p tcp --dport 3389 -j RETURN")
        self.assertEqual(rdin[-1], "-A WAMO-FW-RDIN -j DROP")
        self.assertEqual(len(rdin), 3)

    def test_nur_eigene_ketten_und_kein_flush(self):
        r = self.regeln()
        for z in r.stdout.splitlines():
            if z.startswith(":"):
                self.assertTrue(z.startswith(":WAMO-FW"), z)
            if z.startswith("-A "):
                self.assertTrue(z.split()[1].startswith("WAMO-FW"), z)
        code = _ohne_kommentare((SOKRATES2 / "wamo-firewall.sh").read_text(encoding="utf-8"))
        self.assertNotIn("iptables -F", code)
        self.assertNotIn("-P INPUT", code)
        self.assertNotIn("-P FORWARD", code)
        # INPUT wird nur um genau einen Sprung für das Netz des Gateways ergänzt
        self.assertEqual(len(re.findall(r"ipt -I INPUT", code)), 1)
        self.assertIn('ipt -I INPUT 1 -s "$NETZ_RD_ZIEL" -j "$KETTE_RDIN"', code)

    def test_regeln_laden_in_einem_wegwerf_namensraum(self):
        # Mit echtem iptables nur in einem eigenen Netzwerk-Namensraum (unshare), nie am Host
        if not (LINUX and shutil.which("iptables-restore") and shutil.which("unshare") and os.geteuid() == 0):
            self.skipTest("braucht Linux, root und iptables (siehe Rauchtest im Container)")
        r = self.regeln()
        pfad = self.wurzel / "regeln.txt"
        pfad.write_text(r.stdout)
        p = subprocess.run(["unshare", "-n", "sh", "-c", f"iptables -N DOCKER-USER && iptables-restore --noflush < {pfad} && iptables -S WAMO-FW-RD"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


@unittest.skipUnless(LINUX and BASH, "Probelauf braucht Linux und GNU-Werkzeuge")
class EinrichtenTrockenTests(Werkstatt):
    def setUp(self):
        super().setUp()
        self.attrappe("docker", "sudo", "ss", "systemctl", "curl", "iptables", "openssl", "useradd", "userdel")
        self.caddy = Path(self.tmp.name) / "caddy"
        (self.caddy / "etc" / "kern").mkdir(parents=True)
        self.ausgabe("docker.image_inspect", "", rc=1)  # Images fehlen noch: docker pull wird gezeigt

    def trocken(self, skript, *args):
        return self.lauf(skript, "--trocken", *args, extra={"WAMO_RD_CADDY_DIR": str(self.caddy),
                                                             "WAMO_RD_BASIS": str(Path(self.tmp.name) / "wamo-rd")})

    def test_einrichten_trocken_aendert_nichts(self):
        r = self.trocken("infra/sokrates2/wamo-rd-einrichten.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        for erwartet in ("useradd --system --user-group --no-create-home", "install -d -m 0750 -o root -g wamo-rd-gateway /etc/wamo-rd",
                         "docker pull guacamole/guacd:1.6.0@sha256:", "docker pull guacamole/guacamole:1.6.0@sha256:",
                         "wamo-firewall-einrichten.sh", "Compose von wamo-caddy ändern", "docker compose -f", "wamo_rd_rauchtest.py --betrieb"):
            self.assertIn(erwartet, r.stdout)
        # nichts mit Wirkung wurde wirklich aufgerufen
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(sudo|useradd|userdel|openssl|curl)\b", z)
            self.assertNotRegex(z, r"^docker (pull|run|rm|network (create|rm|connect)|compose (up|down)|exec|stop|start|restart|cp|build|volume)\b", z)
        self.assertEqual(list((self.caddy / "etc" / "kern").iterdir()), [])
        self.assertFalse((Path(self.tmp.name) / "wamo-rd").exists())

    def test_trockenlauf_zeigt_den_schluessel_nie(self):
        r = self.trocken("infra/sokrates2/wamo-rd-einrichten.sh", "konto")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNone(re.search(r"\b[0-9a-f]{32}\b", r.stdout + r.stderr))
        self.assertIn("wird nie ausgegeben", r.stdout)
        code = _ohne_kommentare((SOKRATES2 / "wamo-rd-einrichten.sh").read_text(encoding="utf-8"))
        # der Schlüssel läuft nur durch eine Pipe in die Datei: nie als Argument, nie in einer Variablen, nie per echo/printf
        self.assertIn("openssl rand -hex 16 | tr -d '\\n' | sudo -n bash -c 'umask 0137; cat > \"$1\"' _ \"$SCHLUESSEL\"", code)
        self.assertNotRegex(code, r"(echo|printf)[^\n]*\$\(?openssl")
        self.assertNotRegex(code, r"\w+=\"?\$\(openssl rand")

    def test_einzelne_befehle_und_falsche_aufrufe(self):
        for befehl in ("konto", "bilder", "firewall", "caddy", "start", "pruefen", "status"):
            r = self.trocken("infra/sokrates2/wamo-rd-einrichten.sh", befehl)
            self.assertEqual(r.returncode, 0, (befehl, r.stdout, r.stderr))
        self.assertEqual(self.lauf("infra/sokrates2/wamo-rd-einrichten.sh", "--unbekannt").returncode, 2)
        self.assertEqual(self.lauf("infra/sokrates2/wamo-rd-rueckweg.sh", "--unbekannt").returncode, 2)

    def test_ohne_trocken_nur_als_wamocon(self):
        if subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip() == "wamocon":
            self.skipTest("läuft als wamocon")
        r = self.lauf("infra/sokrates2/wamo-rd-einrichten.sh", "status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("als wamocon", r.stderr)
        self.assertEqual(self.aufrufe(), [])

    def test_rueckweg_trocken(self):
        (self.caddy / "etc" / "kern" / "wamo-rd.caddy").write_text("x")
        basis = Path(self.tmp.name) / "wamo-rd"
        basis.mkdir()
        (basis / "docker-compose.yml").write_text("name: x\n")
        for args in ((), ("--entfernen",)):
            r = self.trocken("infra/sokrates2/wamo-rd-rueckweg.sh", *args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("docker compose -f", r.stdout)
            self.assertIn("docker rm -f wamo-rd-web wamo-rd-guacd", r.stdout)
            self.assertIn("caddy reload", r.stdout)
            if args:
                self.assertIn("userdel", r.stdout)
                self.assertIn("docker image rm guacamole/guacd:1.6.0@sha256:", r.stdout)
                self.assertIn("rm -rf " + str(basis), r.stdout)
        self.assertTrue((self.caddy / "etc" / "kern" / "wamo-rd.caddy").exists())
        self.assertTrue(basis.exists())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(sudo|userdel)\b", z)
            self.assertNotRegex(z, r"^docker (rm|network rm|image rm|compose|exec)\b", z)

    def test_rueckweg_fasst_nichts_ausserhalb_an(self):
        code = _ohne_kommentare((SOKRATES2 / "wamo-rd-rueckweg.sh").read_text(encoding="utf-8"))
        # nur die festen Namen, nie ein Muster
        self.assertIn("docker rm -f wamo-rd-web wamo-rd-guacd", code)
        self.assertNotRegex(code, r"docker (ps|container ls|network ls|image ls)[^\n]*\|\s*xargs")
        self.assertNotIn("docker system prune", code)
        self.assertNotIn("docker volume prune", code)
        self.assertNotIn("docker network prune", code)
        self.assertNotIn("wamo-caddy_default", code)


if __name__ == "__main__":
    unittest.main()
