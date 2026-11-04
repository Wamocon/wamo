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
import shlex
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

# Nur diese echten Werkzeuge sieht ein Skript im Probelauf; alles andere (ufw, nft, systemctl, docker, apt, useradd, sudo ...)
# gibt es nur als Attrappe, sonst fehlt es ganz. So kann ein Probelauf nie etwas Echtes tun, auch nicht als root im Container.
HARMLOS = ("bash sh sed awk grep egrep cat install mkdir cp mv rm rmdir chmod chown date tr head tail cut sort find stat mktemp "
           "cmp diff paste dirname basename ls touch zcat gzip env seq wc sleep uname readlink ln tee true false").split()

# Alle Skripte dieser Funktion (Pfad relativ zum Repo). Stufe 1 und 2 ergänzen die Liste, sobald es sie gibt.
SKRIPTE = [p for p in (
    "infra/sparks/xrdp-einhegen.sh",
    "infra/sparks/wamo-rd-konto-einrichten.sh",
    "infra/sparks/wamo-rd-ziel-einrichten.sh",
    "infra/sparks/wamo-rd-ausgang-einrichten.sh",
    "infra/sparks/wamo-sudo-einrichten.sh",
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
        for werkzeug in HARMLOS:
            echt = shutil.which(werkzeug)
            if echt and not (self.bin / werkzeug).exists():
                (self.bin / werkzeug).symlink_to(echt)
        (self.bin / "python3").symlink_to(sys.executable)

    def attrappe(self, *namen):
        for n in namen:
            p = self.bin / n
            p.write_text(ATTRAPPE)
            p.chmod(0o755)

    def ausgabe(self, schluessel, text="", rc=None):
        (self.att / f"{schluessel}.out").write_text(text)
        if rc is not None:
            (self.att / f"{schluessel}.rc").write_text(str(rc))
        else:
            (self.att / f"{schluessel}.rc").unlink(missing_ok=True)

    def aufrufe(self):
        return self.log.read_text().splitlines()

    def lauf(self, skript, *args, extra=None, eingabe=None):
        env = {"PATH": str(self.bin), "ATTRAPPE_LOG": str(self.log), "ATTRAPPE_DIR": str(self.att),
               "HOME": self.tmp.name, "LC_ALL": "C", **(extra or {})}
        return subprocess.run([BASH, str(REPO / skript), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=120, input=eingabe if eingabe is not None else "")

    def p(self, *teile):
        return self.wurzel.joinpath(*[t.lstrip("/") for t in teile])


# ====================================================================================== Allgemeine Textprüfungen
class AllgemeinTests(unittest.TestCase):
    def test_skripte_ohne_gedankenstriche_eval_cr_und_selbsttaetiges_update(self):
        self.assertTrue(SKRIPTE)
        for name in SKRIPTE:
            roh = (REPO / name).read_bytes()
            self.assertNotIn(b"\r", roh, name)  # Textmodus würde CRLF verdecken
            text = roh.decode("utf-8")
            code = "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))
            self.assertNotIn(chr(0x2014), text, name)
            self.assertNotIn(chr(0x2013), text, name)
            self.assertIn("set -euo pipefail", text, name)
            self.assertIsNone(re.search(r"\beval\b", code), name)
            self.assertIsNone(re.search(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|\$\w+\s)", code), name)
            # Nichts wird von selbst aktualisiert (Hinweise in echo-Zeilen sind erlaubt, Aufrufe nicht)
            ohne_echo = "\n".join(z for z in code.splitlines() if not z.lstrip().startswith(("echo", "fehler")))
            # ein Aufruf steht am Zeilenanfang oder nach ; && | sudo tu; Hinweistexte in Anführungszeichen zählen nicht
            self.assertIsNone(re.search(r"(^|[;&|]\s*|\bsudo\s+|\btu\s+)apt(-get)?\s+(install|upgrade|dist-upgrade|full-upgrade)\b",
                                        re.sub(r'"[^"\n]*"', '""', ohne_echo), flags=re.M), name)
            self.assertIsNone(re.search(r"\bunattended", code), name)
            # kein Passwort in Argumenten oder Ausgabe
            self.assertIsNone(re.search(r"echo[^\n]*\$\{?(PASSWORT|PW|passwort)", code), name)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class SyntaxTests(unittest.TestCase):
    def test_bash_n(self):
        for name in SKRIPTE + ["infra/sokrates2/wamo-firewall.sh"]:
            with self.subTest(skript=name):
                r = subprocess.run([BASH, "-n", str(REPO / name)], capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck nicht vorhanden")
    def test_shellcheck(self):
        for name in SKRIPTE:
            with self.subTest(skript=name):
                r = subprocess.run(["shellcheck", "-S", "warning", str(REPO / name)], capture_output=True, text=True, encoding="utf-8", errors="replace")
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

    def test_ufw_mit_deutscher_ausgabe_gilt_als_aktiv(self):
        # Auf Sokrates-2 meldet ufw "Status: Aktiv": Das Skript darf deshalb nicht auf nft ausweichen (dort griffe danach iptables)
        self.ausgabe("ufw.status", "Status: Aktiv\n\nZu   Aktion  Von\n22/tcp ALLOW  Anywhere\n")
        self.ausgabe("ufw.status_numbered", "Status: Aktiv\n\n     Zu      Aktion  Von\n[ 1] 22/tcp   ALLOW IN  Anywhere\n")
        r = self.xrdp("--trocken", "--nur-sperre")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("über ufw", r.stdout)
        self.assertNotIn("nftables", r.stdout)

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
        # fail-closed: xrdp startet nur mit geladener Sperre
        dropin = self.p("etc/systemd/system/xrdp.service.d/wamo-sperre.conf").read_text()
        self.assertIn("Requires=wamo-xrdp-sperre.service", dropin)
        self.assertIn("After=wamo-xrdp-sperre.service", dropin)
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
        self.assertFalse(self.p("etc/systemd/system/xrdp.service.d/wamo-sperre.conf").exists())
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


# ====================================================================================== Ausgangsregel für wamo-rd
AUSGANG = "infra/sparks/wamo-rd-ausgang-einrichten.sh"


@unittest.skipUnless(LINUX and BASH, "Probelauf braucht Linux und GNU-Werkzeuge")
class SudoFreigabeTests(Werkstatt):
    SKRIPT = "infra/sparks/wamo-sudo-einrichten.sh"

    def sudo(self, *args):
        return self.lauf(self.SKRIPT, *args, extra={"WAMO_SUDO_WURZEL": str(self.wurzel)})

    def test_trockenlauf_zeigt_feste_befehlszeilen_ohne_platzhalter_und_aendert_nichts(self):
        r = self.sudo("--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NOPASSWD: WAMO_RD_ZIEL", r.stdout)
        befehle = [z.strip().rstrip(",\\ ").strip() for z in r.stdout.splitlines() if "/usr/local/lib/wamo-rd/" in z and "--" in z]
        self.assertGreaterEqual(len(befehle), 9)
        for b in befehle:
            self.assertTrue(b.startswith("/usr/local/lib/wamo-rd/wamo-rd-") or b.startswith("/usr/local/lib/wamo-rd/xrdp-"), b)
            self.assertNotIn("*", b)
            self.assertNotIn("bash ", b)
            self.assertNotRegex(b, r"--(entfernen|zurueck|rotieren|sperren)", b)
        self.assertFalse(self.p("etc/sudoers.d").exists())
        self.assertFalse(self.p("usr").exists())

    def test_einrichten_legt_root_skripte_und_freigabe_an_und_der_rueckweg_raeumt_auf(self):
        r = self.sudo("--benutzer", "dienst", "--ziel", "ziel2", "--quelle", "10.1.2.3/32")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.p("etc/sudoers.d/wamo-rd-ziel").read_text()
        self.assertIn("dienst ALL=(root) NOPASSWD: WAMO_RD_ZIEL", text)
        self.assertIn("wamo-rd-konto-einrichten.sh --fern --ziel ziel2", text)
        self.assertIn("wamo-rd-ziel-einrichten.sh --quelle 10.1.2.3/32 --neustart", text)
        self.assertEqual(stat.S_IMODE(os.stat(self.p("etc/sudoers.d/wamo-rd-ziel")).st_mode), 0o440)
        for s in ("wamo-rd-konto-einrichten.sh", "wamo-rd-ziel-einrichten.sh", "wamo-rd-ausgang-einrichten.sh", "xrdp-einhegen.sh"):
            self.assertEqual(stat.S_IMODE(os.stat(self.p("usr/local/lib/wamo-rd", s)).st_mode), 0o755, s)
        r = self.sudo("--zurueck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.p("etc/sudoers.d/wamo-rd-ziel").exists())
        self.assertFalse(self.p("usr/local/lib/wamo-rd").exists())

    def test_ungueltige_werte_werden_abgewiesen(self):
        for args in (("--quelle", "0.0.0.0/0"), ("--quelle", "8.8.8.8/24"), ("--quelle", "192.168.0.0/8"), ("--ziel", "Gross"),
                     ("--ziel", "a b"), ("--benutzer", "root;x"), ("--benutzer", "Gross"), ("--unbekannt",)):
            r = self.sudo("--trocken", *args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertFalse(self.p("etc/sudoers.d").exists(), args)


@unittest.skipUnless(LINUX and BASH, "Probelauf braucht Linux und GNU-Werkzeuge")
class AusgangsregelTests(Werkstatt):
    def setUp(self):
        super().setUp()
        self.attrappe("nft", "systemctl")
        # Das Skript prüft nach dem Laden, ob die Tabelle steht. Die Attrappe kennt nur eine feste Antwort, darum gilt sie von Anfang
        # an als geladen (Zustand nach dem Laden); Tests für den Fall "Tabelle fehlt" setzen rc=1 selbst.
        self.ausgabe("nft.list_table", "table inet wamo_rd_ausgang {\n}\n", rc=0)
        os.makedirs(self.p("etc"))
        self.p("etc/resolv.conf").write_text("nameserver 127.0.0.53\noptions edns0 trust-ad\n")
        self.regeln = self.p("etc/wamo-rd-ausgang/regeln.nft")

    def ausgang(self, *args, extra=None):
        return self.lauf(AUSGANG, *args, extra={"WAMO_RD_AUSGANG_WURZEL": str(self.wurzel), **(extra or {})})

    def test_trockenlauf_zeigt_die_regel_und_aendert_nichts(self):
        r = self.ausgang("--trocken")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("meta skuid wamo-rd jump wamo_rd", r.stdout)
        self.assertIn("[trocken]", r.stdout)
        self.assertFalse(self.p("etc/wamo-rd-ausgang").exists())
        self.assertFalse(self.p("etc/systemd").exists())
        self.assertFalse(self.p("var").exists())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(nft -f|systemctl (enable|restart|disable|daemon-reload))", z)

    def test_regeltext_beschraenkt_nur_das_konto_und_erlaubt_nur_das_internet(self):
        r = self.ausgang()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        t = self.regeln.read_text()
        # eigene Tabelle, nie die Regeln anderer löschen
        self.assertIn("table inet wamo_rd_ausgang", t)
        self.assertEqual(t.count("delete table"), 1)
        self.assertNotIn("flush", t)
        # nur Pakete des Kontos laufen in die Kette; sonst keine Sprungregel
        self.assertEqual(len(re.findall(r"meta skuid (\S+) jump", t)), 1)
        self.assertIn("meta skuid wamo-rd jump wamo_rd", t)
        self.assertIn("type filter hook output", t)
        self.assertIn("policy accept", t)  # alle anderen Benutzer bleiben unberührt
        for netz in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10", "224.0.0.0/3"):
            self.assertIn(netz, t)
        self.assertIn("meta nfproto ipv6 counter drop", t)
        kette = t.split("chain wamo_rd {")[1]
        zeilen = [z.strip() for z in kette.splitlines() if z.strip() and not z.strip().startswith(("}", "#"))]
        # Reihenfolge: Rückantworten Gateway, DNS-Stub, IPv6 weg, LAN und Loopback weg, zuletzt Internet
        self.assertTrue(zeilen[0].startswith("ip daddr 172.27.0.0/24 ct state established,related"), zeilen[0])
        self.assertTrue(zeilen[1].startswith("ip daddr 127.0.0.53"), zeilen[1])
        self.assertIn("dport 53", zeilen[1])
        self.assertTrue(zeilen[2].startswith("meta nfproto ipv6"), zeilen[2])
        self.assertTrue(zeilen[3].startswith("ip daddr @gesperrt4 counter drop"), zeilen[3])
        self.assertEqual(zeilen[4].split(" comment")[0], "counter accept")
        self.assertEqual(len(zeilen), 5)  # ohne --loopback-port keine Loopback-Freigabe
        # das Gateway bekommt nur Antworten bestehender Verbindungen, nie neue Verbindungen
        self.assertEqual(len(re.findall(r"ip daddr 172\.27\.0\.0/24", t)), 1)

    def test_einrichten_legt_unit_und_dropins_an_und_prueft_vor_dem_laden(self):
        r = self.ausgang()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        unit = self.p("etc/systemd/system/wamo-rd-ausgang.service").read_text()
        self.assertIn("ExecStart=/usr/sbin/nft -f /etc/wamo-rd-ausgang/regeln.nft", unit)
        self.assertIn("ExecStop=-/usr/sbin/nft delete table inet wamo_rd_ausgang", unit)
        self.assertIn("Before=network-pre.target xrdp.service xrdp-sesman.service", unit)
        # fail-closed: xrdp und xrdp-sesman starten nur mit geladener Regel
        for d in ("xrdp-sesman.service.d", "xrdp.service.d"):
            dropin = self.p("etc/systemd/system", d, "wamo-rd-ausgang.conf").read_text()
            self.assertIn("Requires=wamo-rd-ausgang.service", dropin)
            self.assertIn("After=wamo-rd-ausgang.service", dropin)
        aufrufe = self.aufrufe()
        pruef = [i for i, z in enumerate(aufrufe) if z.startswith("nft -c -f")]
        laden = [i for i, z in enumerate(aufrufe) if z == "systemctl restart wamo-rd-ausgang.service"]
        self.assertTrue(pruef and laden and pruef[0] < laden[0], aufrufe)
        self.assertIn("systemctl enable wamo-rd-ausgang.service", aufrufe)
        self.assertIn("systemctl daemon-reload", aufrufe)
        for z in aufrufe:
            self.assertNotRegex(z, r"^nft (add|delete|insert|flush)", z)
            self.assertNotIn("flush", z)
            self.assertNotRegex(z, r"^systemctl (stop|disable|mask)", z)
        self.assertIn("gateway=172.27.0.0/24", self.p("var/lib/wamo-rd-ausgang/zustand").read_text())

    def test_lehnt_nft_den_text_ab_bleibt_alles_wie_es_war(self):
        self.ausgabe("nft", "", rc=1)
        r = self.ausgang()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("lehnt", r.stderr)
        self.assertFalse(self.regeln.exists())
        self.assertFalse(self.p("etc/systemd/system/wamo-rd-ausgang.service").exists())
        self.assertFalse([z for z in self.aufrufe() if z.startswith("systemctl")])

    def test_zweiter_lauf_aendert_nichts(self):
        self.assertEqual(self.ausgang().returncode, 0)
        self.ausgabe("nft.list_table", "table inet wamo_rd_ausgang {\n}\n", rc=0)  # jetzt steht sie
        self.log.write_text("")
        vorher = sha(self.regeln)
        r = self.ausgang()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unverändert", r.stdout)
        self.assertEqual(sha(self.regeln), vorher)
        self.assertFalse(self.p("var/backups").exists())
        self.assertFalse([z for z in self.aufrufe() if z.startswith(("systemctl", "nft -c", "nft -f"))])

    def test_geaenderte_angaben_sichern_die_alte_datei_und_laden_neu(self):
        self.assertEqual(self.ausgang().returncode, 0)
        alt = self.regeln.read_text()
        self.ausgabe("nft.list_table", "x", rc=0)
        self.log.write_text("")
        r = self.ausgang("--loopback-port", "3350", "--loopback-port", "3350", "--loopback-port", "55")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        neu = self.regeln.read_text()
        self.assertIn("ip daddr 127.0.0.1 tcp dport { 55, 3350 } counter accept", neu)
        # die Freigabe steht VOR der Sperre für Loopback
        self.assertLess(neu.index("tcp dport { 55, 3350 }"), neu.index("@gesperrt4 counter drop"))
        sicherungen = os.listdir(self.p("var/backups/wamo-rd-ausgang"))
        self.assertEqual(len(sicherungen), 1)
        self.assertEqual(self.p("var/backups/wamo-rd-ausgang", sicherungen[0]).read_text(), alt)
        self.assertIn("systemctl restart wamo-rd-ausgang.service", self.aufrufe())

    def test_anderes_gateway_netz(self):
        self.assertEqual(self.ausgang("--gateway", "172.30.0.0/24").returncode, 0)
        t = self.regeln.read_text()
        self.assertIn("ip daddr 172.30.0.0/24 ct state established,related", t)
        self.assertNotIn("172.27.0.0/24", t)

    def test_ungueltige_angaben(self):
        for args in (["--gateway", "kein;netz"], ["--gateway", "10.0.0.0"], ["--gateway", "10.0.0.0/33"], ["--gateway"],
                     ["--loopback-port", "0"], ["--loopback-port", "70000"], ["--loopback-port", "x"], ["--loopback-port", "22;rm"],
                     ["--loopback-port"], ["--unbekannt"],
                     sum((["--loopback-port", str(p)] for p in range(1, 10)), [])):
            r = self.ausgang(*args)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
        self.assertFalse(self.p("etc/wamo-rd-ausgang").exists())
        r = self.lauf(AUSGANG, "--trocken", extra={"WAMO_RD_AUSGANG_WURZEL": "relativ/../pfad"})
        self.assertEqual(r.returncode, 2)

    def test_warnung_bei_fremdem_nameserver(self):
        self.p("etc/resolv.conf").write_text("nameserver 192.168.178.1\n")
        r = self.ausgang("--trocken")
        self.assertEqual(r.returncode, 0)
        self.assertIn("WARNUNG", r.stdout)
        self.assertIn("192.168.178.1", r.stdout)

    def test_zurueck_entfernt_nur_das_eigene(self):
        self.assertEqual(self.ausgang().returncode, 0)
        self.log.write_text("")
        r = self.ausgang("--zurueck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.regeln.exists())
        self.assertFalse(self.p("etc/systemd/system/wamo-rd-ausgang.service").exists())
        for d in ("xrdp-sesman.service.d", "xrdp.service.d"):
            self.assertFalse(self.p("etc/systemd/system", d, "wamo-rd-ausgang.conf").exists())
        self.assertFalse(self.p("var/lib/wamo-rd-ausgang/zustand").exists())
        aufrufe = self.aufrufe()
        self.assertIn("systemctl disable --now wamo-rd-ausgang.service", aufrufe)
        self.assertIn("nft delete table inet wamo_rd_ausgang", aufrufe)
        for z in aufrufe:
            self.assertNotIn("flush", z)
            self.assertNotRegex(z, r"^nft delete table inet (?!wamo_rd_ausgang$)", z)

    def test_zurueck_im_trockenlauf_aendert_nichts(self):
        self.assertEqual(self.ausgang().returncode, 0)
        vorher = sha(self.regeln)
        self.log.write_text("")
        r = self.ausgang("--zurueck", "--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sha(self.regeln), vorher)
        self.assertTrue(self.p("etc/systemd/system/wamo-rd-ausgang.service").exists())
        self.assertFalse([z for z in self.aufrufe() if z.startswith(("systemctl disable", "nft delete"))])

    def test_status_aendert_nichts(self):
        self.assertEqual(self.ausgang().returncode, 0)
        self.log.write_text("")
        r = self.ausgang("--status")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Regeldatei", r.stdout)
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(nft (-f|delete|add)|systemctl (enable|restart|disable|stop|daemon-reload))", z)

    def test_regeltext_ist_deterministisch(self):
        self.assertEqual(self.ausgang("--trocken").stdout, self.ausgang("--trocken").stdout)


# ====================================================================================== Stufe 1: Gateway, Caddy, Firewall
RD = SOKRATES2 / "wamo-rd"


def _ohne_kommentare(text):
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


class GatewayDateiTests(unittest.TestCase):
    def setUp(self):
        self.compose = _ohne_kommentare((RD / "docker-compose.yml").read_text(encoding="utf-8"))

    def test_images_nur_ueber_digest(self):
        bilder = re.findall(r"^\s+image:\s+(\S+)", self.compose, flags=re.M)
        self.assertEqual(len(bilder), 3)
        gezogen = [b for b in bilder if "@" in b]
        self.assertEqual(len(gezogen), 2)
        for b in gezogen:
            self.assertRegex(b, r"^guacamole/(guacd|guacamole):1\.6\.0@sha256:[0-9a-f]{64}$")
        # der Zugangsdienst wird gebaut, nie gezogen (pull_policy: never aus dem Anker)
        self.assertEqual([b for b in bilder if "@" not in b], ["${WAMO_RD_PRAEFIX:-wamo-}rd-zugang:1"])
        dockerfile = (RD / "zugang" / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"(?m)^FROM python:3\.13-alpine@sha256:[0-9a-f]{64}$")

    def test_haertung_wie_wamo_ui(self):
        for soll in ("pull_policy: never", "read_only: true", "- ALL", "no-new-privileges:true", "init: true",
                     "restart: unless-stopped", "mem_limit: 768m", "mem_limit: 256m", "pids_limit: 256", "pids_limit: 128",
                     "cpus: 1.0", "noexec", "max-size", 'user: "${WAMO_RD_UID:-65534}:${WAMO_RD_GID:-65534}"',
                     "mem_limit: 128m", "pids_limit: 64", "cpus: 0.5"):
            self.assertIn(soll, self.compose)
        self.assertEqual(self.compose.count("cap_drop:"), 1)  # im gemeinsamen Anker beider Dienste
        self.assertEqual(self.compose.count("<<: *wamo-rd"), 3)
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
        # Die Webanwendung mountet genau den Schlüssel (lesend); den Schlüssel sieht kein anderer Container
        self.assertEqual(self.compose.count("volumes:"), 2)
        self.assertEqual(len(re.findall(r"^\s+- \$\{WAMO_RD_KEY:[^}]*\}:/run/wamo-rd/json\.key:ro$", self.compose, flags=re.M)), 1)
        web = self.compose.split("\n  web:\n")[1].split("\n  zugang:\n")[0]
        self.assertIn("json.key:ro", web)
        zugang = self.compose.split("\n  zugang:\n")[1].split("\nnetworks:")[0]
        self.assertNotIn("json.key", zugang)
        self.assertNotIn("ziele.env", zugang)
        self.assertNotIn("PASSWORT", zugang.upper())

    def test_zugangsdienst(self):
        zugang = self.compose.split("\n  zugang:\n")[1].split("\nnetworks:")[0]
        # genau zwei Bind-Mounts: das Socket-Verzeichnis und die Zielliste (lesend, ohne Geheimnisse)
        mounts = re.findall(r"^\s+- (\$\{WAMO_RD_[A-Z_]+:-[^}]*\}:\S+)$", zugang, flags=re.M)
        self.assertEqual(mounts, ["${WAMO_RD_RUN:-/run/wamo-rd}:/run/wamo-rd",
                                  "${WAMO_RD_ZIELE_DATEI:-/etc/wamo-rd/ziele.toml}:/etc/wamo-rd-ziele.toml:ro"])
        # feste Adresse = vertraute Adresse der Webanwendung (JSON_TRUSTED_NETWORKS)
        self.assertIn("ipv4_address: ${WAMO_RD_ZUGANG_IP:-172.26.0.10}", zugang)
        self.assertIn("172.26.0.10/32", self.compose)
        self.assertIn("WAMO_RD_KERN_UID: ${WAMO_RD_KERN_UID:?", zugang)   # ohne Benutzerkennung des Kerns startet er nicht
        self.assertIn("service_healthy", zugang)
        for verboten in ("ports:", "expose:", "privileged", "docker.sock", "cap_add", "env_file", "JSON_SECRET"):
            self.assertNotIn(verboten, zugang, verboten)
        # im Netz mit Ausgang nur für die Erreichbarkeitsprobe; die Firewall lässt dort nur TCP 3389 zu den Zielen durch
        self.assertIn("ziel: {}", zugang)
        self.assertIn("rd:\n        ipv4_address", zugang)

    def test_netze(self):
        self.assertRegex(self.compose, r"guacd:\s+name: \$\{WAMO_RD_GUACD_NETZ:-wamo-rd-guacd-netz\}\s+internal: true")
        self.assertIn("172.26.1.0/29", self.compose)
        self.assertIn("172.27.0.0/24", self.compose)
        self.assertRegex(self.compose, r"rd:\s+name: \$\{WAMO_RD_NETZ:-wamo-rd-netz\}\s+external: true")
        ziel = self.compose.split("  ziel:\n", 2)[-1]
        self.assertNotIn("internal", ziel)
        # guacd allein hat das Netz mit Ausgang; die Webanwendung hängt nur in rd und guacd
        web = self.compose.split("\n  web:\n")[1].split("\n  zugang:\n")[0]
        self.assertNotIn("ziel", web)
        guacd = self.compose.split("\n  guacd:\n")[1].split("\n  web:\n")[0]
        self.assertIn("ziel: {}", guacd)
        self.assertNotIn("rd:", guacd.replace("guacd:", ""))
        # guacd lauscht nur auf seiner Adresse im internen Netz, nie auf 0.0.0.0 (der Host erreicht ihn über die Brücke sonst)
        self.assertIn('entrypoint: ["/opt/guacamole/sbin/guacd", "-f", "-b", "${WAMO_RD_GUACD_IP:-172.26.1.3}"', guacd)
        self.assertNotIn("0.0.0.0", guacd)
        self.assertIn("ipv4_address: ${WAMO_RD_GUACD_IP:-172.26.1.3}", guacd)
        self.assertIn("GUACD_HOSTNAME: ${WAMO_RD_GUACD_IP:-172.26.1.3}", web)

    def test_caddy_compose_veroeffentlicht_genau_einen_neuen_port_und_ein_netz(self):
        c = _ohne_kommentare((SOKRATES2 / "wamo-caddy" / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertIn('"0.0.0.0:55446:55446"', c)
        self.assertRegex(c, r"rd:\s+name: wamo-rd-netz\s+internal: true\s+driver_opts:\s+com\.docker\.network\.bridge\.inhibit_ipv4: \"true\"\s+ipam:\s+config:\s+- subnet: 172\.26\.0\.0/24")
        self.assertRegex(c, r"networks:\s+default: \{\}\s+ui: \{\}\s+rd:\s+(#[^\n]*\s+)?ipv4_address: 172\.26\.0\.2")
        self.assertEqual(len(re.findall(r'"0\.0\.0\.0:\d+:\d+"', c)), 4)

    def test_eingabe_erweiterung_wird_gebaut_und_eingehaengt(self):
        """Tastaturleiste für Handy und Laptop (Test 04.11.2026): Jar mit Manifest, nur lesend in den Webcontainer eingehängt."""
        import json
        import subprocess
        import tempfile
        import zipfile

        erw = RD / "erweiterung"
        with tempfile.TemporaryDirectory() as t:
            jar = Path(t) / "wamo-eingabe.jar"
            subprocess.run([sys.executable, "-I", str(erw / "jar_bauen.py"), str(jar)], check=True)
            erneut = Path(t) / "zweites.jar"
            subprocess.run([sys.executable, "-I", str(erw / "jar_bauen.py"), str(erneut)], check=True)
            self.assertEqual(jar.read_bytes(), erneut.read_bytes(), "gleicher Inhalt gibt gleiche Datei")
            with zipfile.ZipFile(jar) as z:
                self.assertEqual(sorted(z.namelist()), ["guac-manifest.json", "wamo-eingabe.css", "wamo-eingabe.js"])
                manifest = json.loads(z.read("guac-manifest.json"))
        self.assertEqual(manifest["js"], ["wamo-eingabe.js"])
        self.assertEqual(manifest["css"], ["wamo-eingabe.css"])
        self.assertEqual(manifest["namespace"], "wamo-eingabe")
        compose = (RD / "docker-compose.yml").read_text(encoding="utf-8")
        code = _ohne_kommentare(compose)
        self.assertIn(":/etc/guacamole:ro", code)  # Der Entrypoint nimmt GUACAMOLE_HOME als Vorlage (die Variable selbst überschreibt er)
        self.assertNotIn("GUACAMOLE_HOME_TEMPLATE:", code)
        skript = (RD.parent / "wamo-rd-einrichten.sh").read_text(encoding="utf-8")
        self.assertIn("jar_bauen.py", skript)
        self.assertNotIn("http://", (erw / "wamo-eingabe.js").read_text(encoding="utf-8"), "kein Weg nach draußen")
        self.assertNotIn("fetch(", (erw / "wamo-eingabe.js").read_text(encoding="utf-8"))
        self.assertNotIn("XMLHttpRequest", (erw / "wamo-eingabe.js").read_text(encoding="utf-8"))

    def test_eingabe_erweiterung_reine_teile(self):
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            self.skipTest("node fehlt")
        erw = RD / "erweiterung"
        r = subprocess.run([node, "--test", str(erw / "wamo-eingabe.test.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-400:])

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
        # Guacamole misst die Größe der Anzeige mit einem <object> auf die eigene leere Seite (app/element/templates/blank.html).
        # Bei object-src 'none' bleibt die Anzeige 0x0, der Tunnel wird nie geöffnet und das Fenster bleibt schwarz (Test 04.11.2026).
        self.assertIn("object-src 'self'; frame-src 'self'", code)
        self.assertNotIn("object-src 'none'", code)
        # nur die leere Seite darf eingebettet werden, jede andere Seite bleibt gegen Einbetten geschützt
        self.assertIn("@blank path /app/element/templates/blank.html", code)
        self.assertEqual(code.count("SAMEORIGIN"), 1)
        self.assertIn('>X-Frame-Options "DENY"', code)
        self.assertIn("@normal not path /app/element/templates/blank.html", code)  # alles außer der leeren Seite bleibt streng
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
        dateien = [f for f in RD.rglob("*") if f.is_file() and "__pycache__" not in f.parts]
        self.assertIn("wamo_rd_zugang.py", [f.name for f in dateien])
        for f in dateien + [SOKRATES2 / "wamo_rd_rauchtest.py", SOKRATES2 / "wamo-firewall.sh"]:
            roh = f.read_bytes()
            self.assertNotIn(b"\r", roh, f.name)
            t = roh.decode("utf-8")
            self.assertNotIn(chr(0x2014), t, f.name)
            self.assertNotIn(chr(0x2013), t, f.name)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class FirewallRegelnTests(Werkstatt):
    """wamo-firewall.sh regeln gibt aus, was start setzen würde (ohne root, ohne iptables)."""

    def regeln(self, ziele_text=None):
        if ziele_text is not None:
            (self.wurzel / "ziele.toml").write_text(ziele_text, encoding="utf-8")
        env = {"PATH": os.environ["PATH"], "WAMO_FW_RD_ZIELE": str(self.wurzel / "ziele.toml"),
               "WAMO_FW_CONF": str(self.wurzel / "gibt-es-nicht.conf")}
        return subprocess.run([BASH, str(SOKRATES2 / "wamo-firewall.sh"), "regeln"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)

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
                        'adresse = "1.2.3.4; reboot"\nadresse = "010.0.0.1"\nadresse = "172.20.0.5"\nadresse = "169.254.1.1"\n'
                        'adresse = "224.0.0.1"\n')
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
        if not (LINUX and shutil.which("iptables-restore") and shutil.which("unshare") and os.geteuid() == 0
                and subprocess.run(["unshare", "-n", "true"], capture_output=True).returncode == 0):
            self.skipTest("braucht Linux, root, iptables und unshare -n (siehe Rauchtest im Container)")
        r = self.regeln()
        pfad = self.wurzel / "regeln.txt"
        pfad.write_text(r.stdout)
        p = subprocess.run(["unshare", "-n", "sh", "-c", f"iptables -N DOCKER-USER && iptables-restore --noflush < {shlex.quote(str(pfad))} && iptables -S WAMO-FW-RD"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
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
        if subprocess.run(["id", "-un"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip() == "wamocon":
            self.skipTest("läuft als wamocon")
        r = self.lauf("infra/sokrates2/wamo-rd-einrichten.sh", "status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("als wamocon", r.stderr)
        self.assertEqual(self.aufrufe(), [])

    def test_rueckweg_trocken(self):
        (self.caddy / "etc" / "kern" / "wamo-rd.caddy").write_text("x")
        basis = Path(self.tmp.name) / "wamo-rd"
        basis.mkdir()
        (basis / "docker-compose.yml").write_text("name: wamo-rd\n")
        for args in ((), ("--entfernen",)):
            r = self.trocken("infra/sokrates2/wamo-rd-rueckweg.sh", *args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("docker compose -f", r.stdout)
            self.assertIn("docker rm -f wamo-rd-web wamo-rd-guacd", r.stdout)
            self.assertIn("caddy reload", r.stdout)
            if args:
                self.assertIn("sudo -n rm -f /etc/wamo-rd/ziele.env", r.stdout)  # Passwörter nicht an einer verwaisten Gruppe lassen
                self.assertIn("userdel", r.stdout)
                self.assertIn("docker image rm guacamole/guacd:1.6.0@sha256:", r.stdout)
                self.assertIn("rm -rf " + str(basis), r.stdout)
        self.assertTrue((self.caddy / "etc" / "kern" / "wamo-rd.caddy").exists())
        self.assertTrue(basis.exists())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(sudo|userdel)\b", z)
            self.assertNotRegex(z, r"^docker (rm|network rm|image rm|compose|exec)\b", z)

    def test_rueckweg_loescht_nur_ein_compose_projekt_wamo_rd(self):
        basis = Path(self.tmp.name) / "wamo-rd"
        basis.mkdir()
        (basis / "docker-compose.yml").write_text("name: anderes\n")
        r = self.trocken("infra/sokrates2/wamo-rd-rueckweg.sh", "--entfernen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("rm -rf", r.stdout)
        self.assertIn("bleibt", r.stdout)
        # ein Pfad, der nicht auf /wamo-rd endet, wird gar nicht angenommen
        r = self.lauf("infra/sokrates2/wamo-rd-rueckweg.sh", "--trocken", extra={"WAMO_RD_BASIS": "/home"})
        self.assertEqual(r.returncode, 2)

    def test_rueckweg_fasst_nichts_ausserhalb_an(self):
        code = _ohne_kommentare((SOKRATES2 / "wamo-rd-rueckweg.sh").read_text(encoding="utf-8"))
        # nur die festen Namen, nie ein Muster
        self.assertIn("docker rm -f wamo-rd-web wamo-rd-guacd", code)
        self.assertNotRegex(code, r"docker (ps|container ls|network ls|image ls)[^\n]*\|\s*xargs")
        self.assertNotIn("docker system prune", code)
        self.assertNotIn("docker volume prune", code)
        self.assertNotIn("docker network prune", code)
        self.assertNotIn("wamo-caddy_default", code)


# ====================================================================================== Stufe 2: Konto und Ziel (Sokrates-2)
KONTO_SKRIPT = "infra/sparks/wamo-rd-konto-einrichten.sh"
ZIEL_SKRIPT = "infra/sparks/wamo-rd-ziel-einrichten.sh"

# id: das Konto "existiert", sobald die Datei konto.da im Attrappen-Ordner liegt (useradd legt sie an)
ID_ATTRAPPE = r"""#!/bin/sh
echo "id $*" >> "$ATTRAPPE_LOG"
case "$*" in "-u"|"-un"|"-g") echo 1000; exit 0 ;; esac
[ -f "$ATTRAPPE_DIR/konto.da" ] || exit 1
case "$*" in
  "-nG wamo-rd") cat "$ATTRAPPE_DIR/id.gruppen" 2>/dev/null || echo "wamo-rd wamo-rd-ts" ;;
  *) echo "uid=998(wamo-rd) gid=998(wamo-rd) groups=998(wamo-rd),999(wamo-rd-ts)" ;;
esac
exit 0
"""
USERADD_ATTRAPPE = r"""#!/bin/sh
echo "useradd $*" >> "$ATTRAPPE_LOG"
touch "$ATTRAPPE_DIR/konto.da"
"""
# chpasswd: legt nur ab, WAS auf stdin ankam (der Test prüft damit, dass das Passwort nie anderswo auftaucht)
CHPASSWD_ATTRAPPE = r"""#!/bin/sh
echo "chpasswd" >> "$ATTRAPPE_LOG"
cat > "$ATTRAPPE_DIR/chpasswd.stdin"
"""


@unittest.skipUnless(LINUX and BASH, "Probelauf braucht Linux und GNU-Werkzeuge")
class KontoTests(Werkstatt):
    def setUp(self):
        super().setUp()
        self.attrappe("groupadd", "usermod", "userdel", "groupdel", "pkill", "getent", "sudo", "sshd")
        for name, text in (("id", ID_ATTRAPPE), ("useradd", USERADD_ATTRAPPE), ("chpasswd", CHPASSWD_ATTRAPPE)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        os.makedirs(self.p("etc/wamo-rd"))
        os.makedirs(self.p("home/wamo-rd"))
        self.env = self.p("etc/wamo-rd/ziele.env")
        self.ausgabe("getent.group_wamo-rd-gateway", "wamo-rd-gateway:x:996:\n")
        self.ausgabe("getent.group_wamo-rd-ts", "", rc=2)  # Gruppe fehlt noch
        self.ausgabe("sudo", "User wamo-rd is not allowed to run sudo on sokrates2.\n")
        self.ausgabe("sshd", "port 22\nallowusers wamocon alice\n")

    def konto(self, *args, eingabe=None):
        return self.lauf(KONTO_SKRIPT, *args, extra={"WAMO_RD_KONTO_WURZEL": str(self.wurzel)}, eingabe=eingabe)

    def test_fern_gibt_das_passwort_nur_auf_dem_standardausgang_aus(self):
        shutil.rmtree(self.p("etc/wamo-rd"))  # am Ziel gibt es weder ziele.env noch die Gruppe des Gateways
        r = self.konto("--fern", "--ziel", "sokrates1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pw = self.stdin_pw()
        self.assertEqual(r.stdout, pw + "\n")                      # stdout enthält nichts als das Passwort
        self.assertNotIn(pw, r.stderr)                             # die Meldungen stehen auf dem Fehlerkanal, ohne Passwort
        self.assertIn("Passwort am Konto gesetzt", r.stderr)
        self.assertNotIn(pw, "\n".join(self.aufrufe()))
        self.assertFalse(self.p("etc/wamo-rd").exists())           # nichts bleibt am Ziel
        self.assertIn("groupadd --system wamo-rd-ts", self.aufrufe())
        self.assertTrue([z for z in self.aufrufe() if z.startswith("useradd ")])

    def test_fern_setzt_bei_jedem_lauf_ein_neues_passwort(self):
        shutil.rmtree(self.p("etc/wamo-rd"))
        erstes = self.konto("--fern").stdout
        self.konto_vorhanden()
        (self.att / "chpasswd.stdin").unlink()
        zweites = self.konto("--fern").stdout
        self.assertRegex(erstes, r"^[A-Za-z0-9]{32}\n$")
        self.assertRegex(zweites, r"^[A-Za-z0-9]{32}\n$")
        self.assertNotEqual(erstes, zweites)

    def test_fern_im_trockenlauf_aendert_nichts(self):
        shutil.rmtree(self.p("etc/wamo-rd"))
        r = self.konto("--fern", "--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertFalse((self.att / "chpasswd.stdin").exists())

    def test_uebernehmen_traegt_das_passwort_in_ziele_env_ein(self):
        self.env.write_text("WAMO_RD_PASSWORT_SOKRATES2=bleibtbleibtbleibtbleibtbleibt12\n")
        pw = "Ab3" * 10 + "Zz"
        r = self.konto("--uebernehmen", "--ziel", "sokrates1", eingabe=pw + "\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.env.read_text().splitlines(),
                         ["WAMO_RD_PASSWORT_SOKRATES2=bleibtbleibtbleibtbleibtbleibt12", f"WAMO_RD_PASSWORT_SOKRATES1={pw}"])
        self.assertEqual(stat.S_IMODE(os.stat(self.env).st_mode), 0o640)
        for quelle in (r.stdout, r.stderr, "\n".join(self.aufrufe())):
            self.assertNotIn(pw, quelle)
        self.assertEqual([f for f in os.listdir(self.p("etc/wamo-rd")) if f != "ziele.env"], [])

    def test_uebernehmen_ersetzt_ein_altes_passwort_desselben_ziels(self):
        self.env.write_text("WAMO_RD_PASSWORT_SOKRATES1=altaltaltaltaltaltaltaltaltalt12\n")
        pw = "Qq9" * 10 + "Xx"
        self.assertEqual(self.konto("--uebernehmen", "--ziel", "sokrates1", eingabe=pw + "\n").returncode, 0)
        self.assertEqual(self.env.read_text().splitlines(), [f"WAMO_RD_PASSWORT_SOKRATES1={pw}"])

    def test_uebernehmen_lehnt_falsche_eingaben_ab_und_aendert_nichts(self):
        vorher = "WAMO_RD_PASSWORT_SOKRATES2=bleibtbleibtbleibtbleibtbleibt12\n"
        self.env.write_text(vorher)
        for eingabe in ("", "zukurz\n", "a" * 33 + "\n", "a" * 31 + "!\n", "a b" * 11 + "\n"):
            r = self.konto("--uebernehmen", "--ziel", "sokrates1", eingabe=eingabe)
            self.assertNotEqual(r.returncode, 0, eingabe)
            self.assertEqual(self.env.read_text(), vorher)
            self.assertNotIn("a" * 31, r.stdout + r.stderr)

    def test_uebernehmen_im_trockenlauf_aendert_nichts(self):
        r = self.konto("--uebernehmen", "--trocken", "--ziel", "sokrates1", eingabe="Ab3" * 10 + "Zz\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        self.assertFalse(self.env.exists())

    def stdin_pw(self):
        text = (self.att / "chpasswd.stdin").read_text()
        m = re.fullmatch(r"wamo-rd:([A-Za-z0-9]{32})\n", text)
        self.assertIsNotNone(m, "chpasswd bekam kein Passwort der Länge 32 über stdin")
        return m[1]

    def konto_vorhanden(self):
        (self.att / "konto.da").write_text("")

    def test_trockenlauf_aendert_nichts(self):
        r = self.konto("--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        self.assertFalse(self.env.exists())
        self.assertEqual([z for z in self.aufrufe() if z.split()[0] in ("useradd", "groupadd", "chpasswd", "usermod", "userdel")], [])
        self.assertFalse((self.att / "chpasswd.stdin").exists())

    def test_anlegen_passwort_nur_in_der_datei_und_per_stdin(self):
        r = self.konto()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        useradd = [z for z in self.aufrufe() if z.startswith("useradd ")][0]
        for soll in ("--create-home", "--home-dir /home/wamo-rd", "--shell /bin/bash", "--user-group", "--groups wamo-rd-ts", "-K UMASK=077"):
            self.assertIn(soll, useradd)
        for verboten in ("sudo", "docker", "adm", "wheel", "lpadmin"):
            self.assertNotIn(verboten, useradd.replace("wamo-rd", ""))
        self.assertIn("groupadd --system wamo-rd-ts", self.aufrufe())
        pw = self.stdin_pw()
        zeilen = self.env.read_text().splitlines()
        self.assertEqual(zeilen, [f"WAMO_RD_PASSWORT_SOKRATES2={pw}"])
        self.assertEqual(stat.S_IMODE(os.stat(self.env).st_mode), 0o640)
        # das Passwort steht NUR in der Datei und auf stdin von chpasswd
        for quelle in (r.stdout, r.stderr, "\n".join(self.aufrufe())):
            self.assertNotIn(pw, quelle)
        self.assertEqual([f for f in os.listdir(self.p("etc/wamo-rd")) if f != "ziele.env"], [])
        self.assertEqual(stat.S_IMODE(os.stat(self.p("home/wamo-rd")).st_mode), 0o700)

    def test_zweiter_lauf_laesst_das_passwort(self):
        self.assertEqual(self.konto().returncode, 0)
        pw = self.stdin_pw()
        (self.att / "chpasswd.stdin").unlink()
        self.log.write_text("")
        r = self.konto()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.att / "chpasswd.stdin").exists())
        self.assertIn(pw, self.env.read_text())
        self.assertIn("Passwort unverändert", r.stdout)

    def test_rotieren_ersetzt_nur_die_eigene_zeile(self):
        self.assertEqual(self.konto().returncode, 0)
        alt = self.stdin_pw()
        self.env.write_text(self.env.read_text() + "ANDERES=bleibt\n")
        r = self.konto("--rotieren")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        neu = self.stdin_pw()
        self.assertNotEqual(alt, neu)
        zeilen = self.env.read_text().splitlines()
        self.assertEqual(sorted(zeilen), sorted(["ANDERES=bleibt", f"WAMO_RD_PASSWORT_SOKRATES2={neu}"]))
        self.assertNotIn(alt, self.env.read_text())
        self.assertNotIn(neu, r.stdout + r.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(self.env).st_mode), 0o640)

    def test_ziel_name_bestimmt_den_schluessel(self):
        self.assertEqual(self.konto("--ziel", "caesar").returncode, 0)
        self.assertIn("WAMO_RD_PASSWORT_CAESAR=", self.env.read_text())
        self.assertEqual(self.konto("--ziel", "Gross;x").returncode, 2)

    def test_weitere_gruppe_bricht_ab(self):
        self.konto_vorhanden()
        (self.att / "id.gruppen").write_text("wamo-rd wamo-rd-ts docker\n")
        r = self.konto("--rotieren")
        self.assertEqual(r.returncode, 1)  # auch die Rotation prüft vorher Gruppen, sudo und SSH
        self.assertFalse((self.att / "chpasswd.stdin").exists())
        r = self.konto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("docker", r.stderr)
        r = self.konto("--status")
        self.assertEqual(r.returncode, 1)

    def test_sudo_erlaubt_bricht_ab(self):
        self.konto_vorhanden()
        self.ausgabe("sudo", "User wamo-rd may run the following commands on sokrates2:\n    (ALL : ALL) ALL\n")
        r = self.konto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("sudo", r.stderr)

    def test_ssh_muss_gesperrt_sein(self):
        self.konto_vorhanden()
        self.ausgabe("sshd", "port 22\nallowusers wamocon wamo-rd\n")
        self.assertEqual(self.konto("--status").returncode, 1)
        self.ausgabe("sshd", "port 22\npasswordauthentication no\n")  # weder AllowUsers noch DenyUsers
        self.assertEqual(self.konto("--status").returncode, 1)
        self.ausgabe("sshd", "port 22\ndenyusers bob wamo-rd\n")
        self.assertEqual(self.konto("--status").returncode, 0)
        self.ausgabe("sshd", "port 22\nallowusers wamocon wamo-rd-gateway\n")  # Namensteil zählt nicht als Treffer
        self.assertEqual(self.konto("--status").returncode, 0)

    def test_ssh_muster_gilt_nicht_als_gesperrt(self):
        self.konto_vorhanden()
        self.ausgabe("sshd", "port 22\nallowusers wamo*\n")
        r = self.konto("--status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Muster", r.stderr)
        self.ausgabe("sshd", "port 22\nallowgroups admins\n")
        self.assertEqual(self.konto("--status").returncode, 1)

    def test_ohne_gateway_gruppe_kein_passwort(self):
        self.ausgabe("getent.group_wamo-rd-gateway", "", rc=2)
        r = self.konto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("wamo-rd-einrichten.sh", r.stderr)
        self.assertFalse(self.env.exists())
        self.assertFalse((self.att / "chpasswd.stdin").exists())

    def test_entfernen_raeumt_nur_das_eigene_weg(self):
        self.assertEqual(self.konto().returncode, 0)
        self.env.write_text(self.env.read_text() + "ANDERES=bleibt\n")
        self.ausgabe("getent.group_wamo-rd-ts", "wamo-rd-ts:x:999:wamo-rd\n")
        self.log.write_text("")
        r = self.konto("--entfernen")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aufrufe = self.aufrufe()
        self.assertIn("userdel --remove wamo-rd", aufrufe)
        self.assertIn("groupdel wamo-rd-ts", aufrufe)
        self.assertEqual(self.env.read_text(), "ANDERES=bleibt\n")

    def test_sperren(self):
        self.konto_vorhanden()
        r = self.konto("--sperren")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("usermod --lock --expiredate 1 wamo-rd", self.aufrufe())
        self.assertIn("pkill -u wamo-rd", self.aufrufe())

    def test_skript_zeigt_passwort_nie(self):
        code = _ohne_kommentare((REPO / KONTO_SKRIPT).read_text(encoding="utf-8"))
        self.assertIn("printf '%s:%s\\n' \"$KONTO\" \"$pw\" | chpasswd", code)
        self.assertNotRegex(code, r"(echo|printf)[^\n]*\$\{?pw\}?[^\n]*>&?2")
        for z in code.splitlines():
            if re.search(r"\$\{?pw\}?", z):
                self.assertNotRegex(z, r"^\s*(echo|tee|logger)\b", z)
            self.assertNotIn("set -x", z)
            self.assertNotRegex(z, r"\|\s*chpasswd\s+\S", z)  # kein Argument mit Klartext
        self.assertNotIn("passwd --stdin", code)
        self.assertNotIn("openssl passwd", code)


@unittest.skipUnless(LINUX and BASH, "Probelauf braucht Linux und GNU-Werkzeuge")
class ZielEinrichtenTests(Werkstatt):
    def setUp(self):
        super().setUp()
        self.attrappe("ufw", "nft", "systemctl", "ss", "pgrep", "getent", "id")
        os.makedirs(self.p("etc/xrdp"))
        os.makedirs(self.p("etc/ufw"))
        os.makedirs(self.p("home/wamo-rd"))
        self.sesman = self.p("etc/xrdp/sesman.ini")
        self.xrdp_ini = self.p("etc/xrdp/xrdp.ini")
        self.sesman.write_text(SESMAN_INI)
        self.sesman.chmod(0o640)
        self.xrdp_ini.write_text("[Globals]\nport=3389\nsecurity_layer=negotiate\ncrypt_level=high\n\n[Channels]\nrdpdr=true\nrdpsnd=true\ncliprdr=true\ndrdynvc=true\n")
        self.ausgabe("getent.group_wamo-rd-ts", "wamo-rd-ts:x:999:wamo-rd\n")
        self.ausgabe("ufw.status", "Status: active\n")
        self.ausgabe("ufw.status_numbered", "Status: active\n\n[ 1] 22/tcp ALLOW IN Anywhere\n")
        self.ausgabe("nft.list_table", "", rc=1)
        self.xsession = self.p("home/wamo-rd/startwm.sh")
        self.alte_xsessionrc = self.p("home/wamo-rd/.xsessionrc")

    def ziel(self, *args):
        return self.lauf(ZIEL_SKRIPT, *args, extra={"WAMO_RD_ZIEL_WURZEL": str(self.wurzel)})

    def ini(self, datei, abschnitt, schluessel):
        aktuell = None
        wert = None
        for z in datei.read_text().splitlines():
            if z.strip().startswith("["):
                aktuell = z.strip().strip("[]")
            elif aktuell == abschnitt and z.split("=", 1)[0].strip() == schluessel:
                wert = z.split("=", 1)[1].strip()
        return wert

    def schluessel(self, modus=0o640):
        key = self.p("etc/xrdp/key.pem")
        key.write_text("schluessel")
        key.chmod(modus)
        return key

    def test_tls_schluessel_ohne_datei_aendert_nichts(self):
        r = self.ziel()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("kein Schlüssel unter /etc/xrdp/key.pem", r.stdout)
        self.assertFalse([z for z in self.aufrufe() if z.startswith("usermod")])

    def test_tls_schluessel_nimmt_xrdp_in_die_gruppe_ssl_cert_auf(self):
        self.attrappe("usermod")
        self.schluessel()
        r = self.lauf(ZIEL_SKRIPT, extra={"WAMO_RD_ZIEL_WURZEL": str(self.wurzel), "WAMO_RD_ZIEL_SCHLUESSELGRUPPE": "ssl-cert"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("usermod -aG ssl-cert xrdp", self.aufrufe())
        self.assertIn("xrdp in die Gruppe ssl-cert aufgenommen", r.stdout)

    def test_tls_schluessel_im_trockenlauf_zeigt_nur_an(self):
        self.attrappe("usermod")
        self.schluessel()
        r = self.lauf(ZIEL_SKRIPT, "--trocken", extra={"WAMO_RD_ZIEL_WURZEL": str(self.wurzel), "WAMO_RD_ZIEL_SCHLUESSELGRUPPE": "ssl-cert"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken] usermod -aG ssl-cert xrdp", r.stdout)
        self.assertFalse([z for z in self.aufrufe() if z.startswith("usermod")])

    def test_tls_schluessel_mit_fremder_gruppe_oder_ohne_gruppenrecht_bricht_ab(self):
        self.attrappe("usermod")
        self.schluessel()
        r = self.lauf(ZIEL_SKRIPT, extra={"WAMO_RD_ZIEL_WURZEL": str(self.wurzel), "WAMO_RD_ZIEL_SCHLUESSELGRUPPE": "andere"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nicht ssl-cert", r.stderr)
        self.schluessel(0o600)
        r = self.lauf(ZIEL_SKRIPT, extra={"WAMO_RD_ZIEL_WURZEL": str(self.wurzel), "WAMO_RD_ZIEL_SCHLUESSELGRUPPE": "ssl-cert"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ohne Leserecht für die Gruppe", r.stderr)
        self.assertFalse([z for z in self.aufrufe() if z.startswith("usermod")])

    def test_trockenlauf_aendert_nichts(self):
        vorher = (sha(self.sesman), sha(self.xrdp_ini))
        r = self.ziel("--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((sha(self.sesman), sha(self.xrdp_ini)), vorher)
        self.assertFalse(self.xsession.exists())
        self.assertFalse(self.p("var").exists())
        self.assertIn("[trocken]", r.stdout)
        self.assertIn("ufw insert 1 allow from 172.27.0.0/24 to any port 3389 proto tcp comment wamo-xrdp", r.stdout)
        self.assertIn("AlwaysGroupCheck=true", r.stdout)
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(ufw (insert|allow|deny|delete)|nft -f|systemctl (enable|restart|disable))", z)
        self.assertEqual(sorted(os.listdir(self.p("etc/xrdp"))), ["sesman.ini", "xrdp.ini"])

    def test_einrichten_setzt_genau_die_vorgesehenen_werte(self):
        r = self.ziel()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        erwartet_sesman = {("Security", "AllowRootLogin"): "false", ("Security", "AlwaysGroupCheck"): "true",
                           ("Security", "TerminalServerUsers"): "wamo-rd-ts", ("Sessions", "KillDisconnected"): "true",
                           ("Sessions", "DisconnectedTimeLimit"): "900", ("Sessions", "IdleTimeLimit"): "600"}
        for (ab, k), w in erwartet_sesman.items():
            self.assertEqual(self.ini(self.sesman, ab, k), w, (ab, k))
        erwartet_xrdp = {("Globals", "security_layer"): "tls", ("Globals", "crypt_level"): "high",
                         ("Globals", "ssl_protocols"): "TLSv1.2, TLSv1.3", ("Channels", "rdpdr"): "false",
                         ("Channels", "rdpsnd"): "false", ("Channels", "cliprdr"): "false", ("Channels", "drdynvc"): "true",
                         ("Globals", "port"): "3389"}
        for (ab, k), w in erwartet_xrdp.items():
            self.assertEqual(self.ini(self.xrdp_ini, ab, k), w, (ab, k))
        # Nebenwirkungen: übrige Zeilen unverändert (sesman.ini: Mehrzeilenvergleich ohne die geänderten Schlüssel)
        geaendert = {k for (_, k) in erwartet_sesman}
        alt = [z for z in SESMAN_INI.splitlines() if z.split("=")[0] not in geaendert]
        neu = [z for z in self.sesman.read_text().splitlines() if z.split("=")[0] not in geaendert]
        self.assertEqual(neu, alt)
        self.assertEqual(stat.S_IMODE(os.stat(self.sesman).st_mode), 0o640)
        # eine Sicherung je Datei, mit dem alten Inhalt
        s = [f for f in os.listdir(self.p("etc/xrdp")) if f.startswith("sesman.ini.vor-wamo-rd-")]
        x = [f for f in os.listdir(self.p("etc/xrdp")) if f.startswith("xrdp.ini.vor-wamo-rd-")]
        self.assertEqual((len(s), len(x)), (1, 1))
        self.assertEqual(self.p("etc/xrdp", s[0]).read_text(), SESMAN_INI)
        # nur die eine Datei im Home des Kontos
        text = self.xsession.read_text()
        self.assertIn("export XDG_CURRENT_DESKTOP=ubuntu:GNOME", text)
        self.assertTrue(text.endswith("exec gnome-session --session=ubuntu\n"), text)   # startet den Desktop selbst, nicht über Xsession
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertEqual(stat.S_IMODE(os.stat(self.xsession).st_mode), 0o755)
        self.assertEqual(os.listdir(self.p("home/wamo-rd")), ["startwm.sh"])
        # Sperre nur für das Netz des Gateways
        aufrufe = self.aufrufe()
        self.assertIn("ufw insert 1 allow from 172.27.0.0/24 to any port 3389 proto tcp comment wamo-xrdp", aufrufe)
        # kein Paket, kein Neustart ohne Option, nichts mit GRD oder sshd
        for z in aufrufe:
            self.assertNotRegex(z, r"^(apt|dpkg|systemctl (restart|enable|stop|disable)|grdctl|sshd|ssh)", z)
        self.assertIn("sudo systemctl restart xrdp-sesman", r.stdout)

    def test_zweiter_lauf_aendert_nichts(self):
        self.assertEqual(self.ziel().returncode, 0)
        self.ausgabe("ufw.show_added", "ufw allow from 172.27.0.0/24 to any port 3389 proto tcp comment 'wamo-xrdp'\n"
                                       "ufw deny proto tcp from any to any port 3389 comment 'wamo-xrdp'\n")
        n = len(os.listdir(self.p("etc/xrdp")))
        r = self.ziel()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(os.listdir(self.p("etc/xrdp"))), n)
        self.assertIn("schon eingerichtet", r.stdout)

    def test_neustart_nur_ohne_sitzung(self):
        self.ausgabe("pgrep", "123\n", rc=0)
        self.ziel("--neustart")
        self.assertNotIn("systemctl restart xrdp-sesman", self.aufrufe())
        self.ausgabe("pgrep", "", rc=1)
        self.ziel("--neustart")
        self.assertIn("systemctl restart xrdp-sesman", self.aufrufe())

    def test_zurueck_stellt_beide_dateien_her(self):
        self.assertEqual(self.ziel().returncode, 0)
        self.ausgabe("ufw.show_added", "ufw allow from 172.27.0.0/24 to any port 3389 proto tcp comment 'wamo-xrdp'\n")
        r = self.ziel("--zurueck")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.sesman.read_text(), SESMAN_INI)
        self.assertIn("security_layer=negotiate", self.xrdp_ini.read_text())
        self.assertFalse(self.xsession.exists())
        self.assertIn("ufw --force delete allow from 172.27.0.0/24 to any port 3389 proto tcp", self.aufrufe())

    def test_ohne_xrdp_bricht_vor_der_sperre_ab(self):
        self.sesman.unlink()
        r = self.ziel()
        self.assertEqual(r.returncode, 1)
        self.assertIn("apt-get install xrdp xorgxrdp", r.stderr)
        self.assertFalse([z for z in self.aufrufe() if z.startswith("ufw insert")])

    def test_gruppe_mit_zweitem_mitglied_bricht_ab(self):
        self.ausgabe("getent.group_wamo-rd-ts", "wamo-rd-ts:x:999:wamo-rd,bob\n")
        r = self.ziel()
        self.assertEqual(r.returncode, 1)
        self.assertIn("genau ein Mitglied", r.stderr)

    def test_alte_xsessionrc_dieses_skripts_wird_entfernt_eine_fremde_bleibt(self):
        self.alte_xsessionrc.write_text("# WAMO Remote Desktop (wamo-rd-ziel-einrichten.sh)\nexport GNOME_SHELL_SESSION_MODE=ubuntu\n")
        self.assertEqual(self.ziel().returncode, 0)
        self.assertFalse(self.alte_xsessionrc.exists())
        self.assertTrue(self.xsession.exists())
        self.xsession.unlink()
        self.alte_xsessionrc.write_text("export MEINS=1\n")
        self.assertEqual(self.ziel().returncode, 0)
        self.assertEqual(self.alte_xsessionrc.read_text(), "export MEINS=1\n")

    def test_startwm_als_link_wird_abgewiesen(self):
        ziel = Path(self.tmp.name) / "fremd.txt"
        ziel.write_text("unberuehrt\n")
        self.xsession.symlink_to(ziel)
        r = self.ziel()
        self.assertEqual(r.returncode, 1)
        self.assertIn("symbolischer Link", r.stderr)
        self.assertEqual(ziel.read_text(), "unberuehrt\n")

    def test_fremde_startwm_wird_nicht_ueberschrieben(self):
        self.xsession.write_text("export MEINS=1\n")
        r = self.ziel()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.xsession.read_text(), "export MEINS=1\n")

    def test_quelle_umlenkbar_und_geprueft(self):
        r = self.ziel("--trocken", "--quelle", "192.168.178.136/32")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("from 192.168.178.136/32 to any port 3389", r.stdout)
        self.assertEqual(self.ziel("--quelle", "alle; reboot").returncode, 2)
        self.assertEqual(self.ziel("--quelle").returncode, 2)


@unittest.skipUnless(sys.version_info >= (3, 10), "Rauchtest braucht Python 3.10 oder neuer")
class RauchtestHilfenTests(unittest.TestCase):
    """Die Logik des Rauchtests ohne Docker: sh wird durch eine Attrappe ersetzt."""

    def laden(self):
        import importlib.util
        spez = importlib.util.spec_from_file_location("wamo_rd_rauchtest_t", SOKRATES2 / "wamo_rd_rauchtest.py")
        m = importlib.util.module_from_spec(spez)
        spez.loader.exec_module(m)
        return m

    def antwort(self, cmd, rc=0, out="", err=""):
        return subprocess.CompletedProcess(list(cmd), rc, out, err)

    def test_aufraeumen_entfernt_nur_den_eigenen_praefix(self):
        m = self.laden()
        aufrufe = []

        def falsch(*cmd, **kw):
            aufrufe.append(cmd)
            if cmd[:2] == ("docker", "ps"):
                return self.antwort(cmd, out="wamo-rd-web\nwamo-caddy\nwamo-test-rd-t12-x\nwamo-test-rd-t1-a\nwamo-test-ui-t1-a\n")
            if cmd[:3] == ("docker", "network", "ls"):
                return self.antwort(cmd, out="bridge\nwamo-rd-netz\nwamo-test-rd-t1-netz\n")
            if cmd[:3] == ("docker", "volume", "ls"):
                return self.antwort(cmd, out="wamo-test-rd-t1-v\nsupabase_db\n")
            return self.antwort(cmd)

        m.sh = falsch
        m.aufraeumen_praefix("wamo-test-rd-t1-")
        entfernt = [c[-1] for c in aufrufe if c[:2] == ("docker", "rm") or c[:3] in (("docker", "network", "rm"), ("docker", "volume", "rm"))]
        self.assertEqual(sorted(entfernt), ["wamo-test-rd-t1-a", "wamo-test-rd-t1-netz", "wamo-test-rd-t1-v"])

    def test_ungueltige_praefixe_werden_verweigert(self):
        m = self.laden()
        m.sh = lambda *c, **k: self.antwort(c)
        for p in ("wamo-", "wamo-test-rd-", "wamo-test-rd-t1", "wamo-test-ui-t1-", "wamo-test-rd-T1-", "wamo-test-rd-a.b-", ""):
            with self.assertRaises(RuntimeError, msg=p):
                m.aufraeumen_praefix(p)
        self.assertIsNone(m.NAME_RE.fullmatch("t1; rm"))
        self.assertIsNotNone(m.NAME_RE.fullmatch("t1"))

    def test_zielliste_wie_in_der_firewall(self):
        m = self.laden()
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "ziele.toml"
            f.write_text('adresse = "192.168.178.136"\nadresse = "10.0.0.5" # x\nadresse = "127.0.0.1"\nadresse = "0.0.0.0"\n'
                         'adresse = "172.20.0.5"\nadresse = "169.254.0.1"\nadresse = "224.0.0.1"\nadresse = "010.0.0.1"\nadresse = "abc"\n')
            self.assertEqual(m.lies_ziele(str(f)), ["192.168.178.136", "10.0.0.5"])
            self.assertIsNone(m.lies_ziele(str(Path(d) / "gibt-es-nicht")))
            f.write_text("".join(f'adresse = "10.9.9.{n}"\n' for n in range(1, 15)))
            self.assertEqual(len(m.lies_ziele(str(f))), 8)

    def test_tcp_probe_unterscheidet_zu_und_defekt(self):
        m = self.laden()
        for rc, err, soll in ((0, "", "offen"), (1, "bash: connect: Connection refused", "zu"), (124, "", "zu"),
                              (1, "Error response from daemon: container x is not running", "defekt"), (127, "", "defekt"),
                              (126, "", "defekt"), (1, "OCI runtime exec failed", "defekt")):
            m.sh = lambda *c, rc=rc, err=err, **k: self.antwort(c, rc=rc, err=err)
            self.assertEqual(m.tcp_von("wamo-rd-web", "1.2.3.4", 80), soll, (rc, err))
            self.assertEqual(m.tcp_von("wamo-rd-guacd", "1.2.3.4", 80), soll, (rc, err))
        self.assertEqual(m.tcp_von("wamo-rd-web", "", 80), "defekt")

    def test_sh_meldet_fehlendes_programm_und_zeitueberschreitung(self):
        m = self.laden()
        r = m.sh("gibt-es-bestimmt-nicht-xyz")
        self.assertEqual(r.returncode, 127)
        r = m.sh(sys.executable, "-c", "import time; time.sleep(5)", timeout=0.2)
        self.assertEqual(r.returncode, 124)


if __name__ == "__main__":
    unittest.main()
