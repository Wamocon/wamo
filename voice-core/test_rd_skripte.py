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


if __name__ == "__main__":
    unittest.main()
