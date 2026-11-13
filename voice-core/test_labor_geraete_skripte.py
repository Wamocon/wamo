"""Tests für die Betreuer-Geräte des Testlabors: Zulassung über den Gerätenamen statt über feste Adressen (Abschnitt 19.1 in infra/sokrates2/README.md).

  infra/sokrates2/wamo-labor-site.sh                     gemeinsame Bausteine der Caddy-Site (Adressen, Vorlage, Prüfung, Reload), mit source geladen
  infra/sokrates2/wamo-labor-geraete-aktualisieren.sh    Aktualisierer (installiert nach /usr/local/sbin), löst die Namen auf und setzt die Site neu zusammen
  infra/sokrates2/wamo-labor-geraete-einrichten.sh       einrichten, status, rueckweg (Unit, Timer, Dateien)
  infra/sokrates2/wamo-labor-geraete.service, .timer     systemd-Unit (oneshot, gehärtet) und Zeitgeber (jede Minute)

Nichts hier läuft gegen ein System. Die Skripte laufen im Probemodus in einem Wegwerf-Ordner (WAMO_LABOR_GERAETE_WURZEL) mit Attrappen für nmblookup,
avahi-resolve, docker, curl, flock, logger und systemctl (dieselbe Werkstatt wie test_labor_betrieb_skripte.py). Die Attrappen schreiben jeden Aufruf in ein
Protokoll, daran prüfen die Tests, was ein echter Lauf tun WÜRDE und was nie. Was nur am Server prüfbar ist (echte Namensantworten im Büro-LAN, die gehärtete
Unit unter systemd, ein echter caddy reload, ein echtes flock), steht im README von infra/sokrates2 (Abschnitt 19.1).

Ausführen:  python -m pytest -q -p no:cacheprovider test_labor_geraete_skripte.py   (im Ordner voice-core/)
"""

import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_labor_betrieb_skripte import (
    BASH, BASIS_IMAGE, EINRICHTEN, SITE, SOKRATES2, VERBOTENE_WOERTER, Werkstatt, code, funktionen, lies, ohne_kommentare, posix, schreibe,
)

SITE_BIB = SOKRATES2 / "wamo-labor-site.sh"
AKTUALISIERER = SOKRATES2 / "wamo-labor-geraete-aktualisieren.sh"
GERAETE_EINRICHTEN = SOKRATES2 / "wamo-labor-geraete-einrichten.sh"
DIENST = SOKRATES2 / "wamo-labor-geraete.service"
TIMER = SOKRATES2 / "wamo-labor-geraete.timer"
NEUE_DATEIEN = [SITE_BIB]
NEUE_SKRIPTE = (SITE_BIB,)
INTERN = "127.0.0.0/8 172.24.0.1/32 192.168.178.136/32"
FRIST = 12 * 3600
GERAET = "WMC-H-04-NS"


# ====================================================================================== Allgemeine Textprüfungen
class AllgemeinTests(unittest.TestCase):
    def test_alle_dateien_sind_da(self):
        for f in NEUE_DATEIEN:
            self.assertTrue(f.is_file(), f)

    def test_ohne_gedankenstriche_cr_verbotene_woerter_und_zufallsfolgen(self):
        for f in [p for p in NEUE_DATEIEN if p.is_file()]:
            roh = f.read_bytes()
            self.assertNotIn(b"\r", roh, f.name)
            text = roh.decode("utf-8")
            self.assertNotIn(chr(0x2014), text, f.name)
            self.assertNotIn(chr(0x2013), text, f.name)
            for wort in VERBOTENE_WOERTER:
                self.assertNotIn(wort, text.lower(), f.name)
            self.assertIsNone(re.search(r"\b[A-Z]{2,10}-\d{3,5}\b", text), f.name)  # keine Vorgangsnummern
            for zeile in ohne_kommentare(text).splitlines():
                for wort in re.findall(r"[A-Za-z0-9_+/=-]{40,}", zeile):
                    self.assertTrue(re.fullmatch(r"[A-Za-z_]+[_-][A-Za-z_-]+|[a-z0-9-]+", wort) or "/" in wort or "$" in wort, f"{f.name}: {wort[:20]}")

    def test_skripte_form_und_grenzen(self):
        for skript in NEUE_SKRIPTE:
            text = lies(skript)
            r = subprocess.run([BASH, "-n", str(skript)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(text.startswith("#!/usr/bin/env bash\n"), skript.name)
            self.assertLess(len(text.splitlines()), 800, skript.name)
            for name, laenge in funktionen(text):
                self.assertLessEqual(laenge, 50, f"{skript.name}: {name} hat {laenge} Zeilen")
            c = ohne_kommentare(text)
            self.assertIsNone(re.search(r"\beval\b", c), skript.name)
            self.assertNotIn("set -x", c, skript.name)
            self.assertNotRegex(c, r"systemctl\s+(restart|stop|kill|reload)\s+wamo-core")
        for skript in NEUE_SKRIPTE[1:]:
            self.assertIn("set -euo pipefail", lies(skript), skript.name)


# ====================================================================================== Teil 1: die gemeinsame Bibliothek
@unittest.skipUnless(BASH, "keine Bash vorhanden")
class BibliothekTests(unittest.TestCase):
    """Die Funktionen der Bibliothek einzeln, in einer Bash mit der Fehlerfunktion des Aufrufers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="labor-geraete-bib-")
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)

    def bash(self, text: str) -> subprocess.CompletedProcess:
        kopf = f'set -euo pipefail\nfehler() {{ echo "$*" >&2; exit 1; }}\n. "{posix(SITE_BIB)}"\n'
        return subprocess.run([BASH, "-c", kopf + text], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                              stdin=subprocess.DEVNULL)

    def test_laden_hat_keine_nebenwirkung_und_setzt_die_konstanten(self):
        r = self.bash('echo "$INTERN|$LAN_PRAEFIX|$GERAETE_FRIST_S|$CADDY_PORTS"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), f"{INTERN}|192.168.178.|{FRIST}|55443 55444 55445 55446")

    def test_adresse_gueltig(self):
        for gut in ("192.168.178.50", "10.0.0.1/24", "192.168.178.60/32", "8.8.8.8/32"):
            self.assertEqual(self.bash(f'adresse_gueltig "{gut}"').returncode, 0, gut)
        for schlecht in ("0.0.0.0/0", "192.168.0.0/16", "192.168.178.500", "1.2.3.4;reboot", "abc", "127.0.0.1", "10.0.0.1/33", "192.168.178.07", "224.0.0.1",
                         "169.254.1.1", "1.2.3", "1.2.3.4.5", ""):
            self.assertNotEqual(self.bash(f'adresse_gueltig "{schlecht}"').returncode, 0, schlecht)

    def test_adressen_lesen_bricht_bei_einer_ungueltigen_zeile_ab(self):
        datei = self.t / "adressen"
        schreibe(datei, "# Kopf\n192.168.178.50\n  192.168.178.60/32  # zweiter\r\n\n\"192.168.178.70\"\n")
        r = self.bash(f'adressen_lesen "{posix(datei)}"')
        self.assertEqual(r.stdout.strip(), "192.168.178.50 192.168.178.60/32 192.168.178.70", r.stderr)
        schreibe(datei, "192.168.178.50\n1.2.3.4;reboot\n")
        r = self.bash(f'adressen_lesen "{posix(datei)}"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Ungültige Adresse", r.stderr)
        self.assertEqual(self.bash(f'adressen_lesen "{posix(self.t / "gibt-es-nicht")}"').stdout.strip(), "")

    def test_liste_bilden_ohne_doppelte_leerzeichen(self):
        r = self.bash('liste_bilden "a b" "" "c" ""')
        self.assertEqual(r.stdout, "a b c\n")
        self.assertEqual(self.bash('liste_bilden "" ""').stdout, "\n")

    def test_site_erzeugen_setzt_den_platzhalter_ein(self):
        vorlage = self.t / "vorlage"
        schreibe(vorlage, "a\nremote_ip __BETREUER_ADRESSEN__\nb\n")
        r = self.bash(f'site_erzeugen "{posix(vorlage)}" "1.2.3.4 5.6.7.8/32"')
        self.assertEqual(r.stdout, "a\nremote_ip 1.2.3.4 5.6.7.8/32\nb\n")
        echte = self.bash(f'site_erzeugen "{posix(SITE)}" "{INTERN}"').stdout
        self.assertEqual(echte, lies(SITE).replace("__BETREUER_ADRESSEN__", INTERN))

    def test_namen_lesen_strenge_regeln(self):
        datei = self.t / "namen"
        schreibe(datei, "# Kopf\nwmc-h-04-ns\r\n  PC-2  # Kommentar\n1PC\nWMC-H-04-NS\n\nA23456789012345\n")
        r = self.bash(f'namen_lesen "{posix(datei)}"')
        self.assertEqual(r.stdout.split(), ["WMC-H-04-NS", "PC-2", "1PC", "A23456789012345"], r.stderr)  # groß, ohne Doppelte, 15 Zeichen sind erlaubt
        for schlecht in ("A234567890123456", "-PC", "PC_2", "PC 2", "PC.local", "PÄ", "pc;reboot", "'PC'", "PC$(x)", "*", "PC\x01"):
            schreibe(datei, f"GUT-1\n{schlecht}\n")
            r = self.bash(f'namen_lesen "{posix(datei)}"')
            self.assertNotEqual(r.returncode, 0, schlecht)
            self.assertIn("Ungültiger Gerätename", r.stderr, schlecht)
            self.assertIn("Zeile 2", r.stderr, schlecht)
        self.assertEqual(self.bash(f'namen_lesen "{posix(self.t / "gibt-es-nicht")}"').returncode, 0)

    def test_namen_lesen_hoechstens_16_namen(self):
        datei = self.t / "namen"
        schreibe(datei, "".join(f"PC-{i}\n" for i in range(16)))
        self.assertEqual(self.bash(f'namen_lesen "{posix(datei)}"').returncode, 0)
        schreibe(datei, "".join(f"PC-{i}\n" for i in range(17)))
        r = self.bash(f'namen_lesen "{posix(datei)}"')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("16", r.stderr)

    def test_stand_lesen_und_frist(self):
        datei = self.t / "stand"
        jetzt = 2_000_000_000
        schreibe(datei, f"# Kopf\n{GERAET} 192.168.178.78 {jetzt - 60}\n{GERAET} 192.168.178.67 {jetzt - FRIST}\nALT 192.168.178.99 {jetzt - FRIST - 1}\n"
                        f"KAPUTT 10.0.0.5 {jetzt}\nKAPUTT2 192.168.178.5\nKAPUTT3 192.168.178.6 x\nZU VIEL 192.168.178.7 1 2\n")
        r = self.bash(f'stand_lesen "{posix(datei)}"')
        self.assertEqual(r.stdout.splitlines(), [f"{GERAET} 192.168.178.78 {jetzt - 60}", f"{GERAET} 192.168.178.67 {jetzt - FRIST}",
                                                 f"ALT 192.168.178.99 {jetzt - FRIST - 1}"])
        self.assertEqual(r.stderr.count("übersprungen"), 4)  # kaputte Zeilen werden gemeldet, nie stillschweigend gelesen
        r = self.bash(f'geraete_adressen "{posix(datei)}" {jetzt} 2>/dev/null')
        self.assertEqual(r.stdout.strip(), "192.168.178.67 192.168.178.78")  # genau 12 Stunden gilt noch, eine Sekunde mehr nicht, sortiert
        self.assertEqual(self.bash(f'geraete_adressen "{posix(self.t / "gibt-es-nicht")}" {jetzt}').stdout.strip(), "")


# ====================================================================================== Teil 2: wamo-labor-einrichten.sh mit dem Stand
@unittest.skipUnless(BASH, "keine Bash vorhanden")
class EinrichtenMitStandTests(Werkstatt):
    """Das Einrichtungsskript des Labor-Dienstes setzt die Adressen der Betreuer-Geräte aus dem Stand mit ein (sonst fielen sie bei jedem Lauf für eine Minute weg)."""

    def setUp(self):
        super().setUp()
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "# Betreuer\n192.168.178.50\n")

    def einrichten(self):
        return self.lauf(EINRICHTEN, "einrichten", "--basis-image", BASIS_IMAGE)

    def site(self) -> str:
        return (self.caddy / "etc" / "kern" / "wamo-labor.caddy").read_text(encoding="utf-8")

    def test_gueltiger_stand_kommt_in_die_site(self):
        jetzt = int(time.time())
        schreibe(self.p("etc/wamo-labor/betreuer-geraete.stand"),
                 f"# Kopf\nWMC-H-04-NS 192.168.178.78 {jetzt - 60}\nWMC-H-04-NS 192.168.178.67 {jetzt - FRIST - 3600}\nALT-PC 192.168.178.99 {jetzt - 100000}\n"
                 f"ZWEITER 192.168.178.20 {jetzt - 10}\n")
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"remote_ip {INTERN} 192.168.178.50 192.168.178.20 192.168.178.78\n", self.site())
        for abgelaufen in ("192.168.178.67", "192.168.178.99"):
            self.assertNotIn(abgelaufen, self.site())

    def test_ohne_stand_bleibt_die_site_wie_bisher(self):
        self.assertEqual(self.einrichten().returncode, 0)
        self.assertIn(f"remote_ip {INTERN} 192.168.178.50\n", self.site())

    def test_kaputter_stand_wird_gemeldet_und_nie_eingesetzt(self):
        schreibe(self.p("etc/wamo-labor/betreuer-geraete.stand"), f"X 10.0.0.5 {int(time.time())}\n")
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("übersprungen", r.stderr)
        self.assertNotIn("10.0.0.5", self.site())

    def test_vorlage_und_bibliothek_werden_fuer_den_aktualisierer_nachgezogen(self):
        lib = self.p("usr/local/lib/wamo-labor-geraete")
        schreibe(lib / "wamo-labor.caddy", "alt\n")
        schreibe(lib / "wamo-labor-site.sh", "alt\n")
        self.assertEqual(self.einrichten().returncode, 0)
        self.assertEqual((lib / "wamo-labor.caddy").read_bytes(), SITE.read_bytes())
        self.assertEqual((lib / "wamo-labor-site.sh").read_bytes(), SITE_BIB.read_bytes())

    def test_ohne_den_aktualisierer_wird_nichts_angelegt(self):
        self.assertEqual(self.einrichten().returncode, 0)
        self.assertFalse(self.p("usr/local/lib/wamo-labor-geraete").exists())

    def test_trockenlauf_zieht_nichts_nach(self):
        lib = self.p("usr/local/lib/wamo-labor-geraete")
        schreibe(lib / "wamo-labor.caddy", "alt\n")
        r = self.lauf(EINRICHTEN, "einrichten", "--trocken", "--basis-image", BASIS_IMAGE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((lib / "wamo-labor.caddy").read_text(encoding="utf-8"), "alt\n")

    def test_einrichten_nutzt_die_bibliothek_statt_eigener_kopien(self):
        c = code(EINRICHTEN)
        self.assertIn("wamo-labor-site.sh", c)
        for verschoben in ("adresse_gueltig()", "site_erzeugen()", "antwortet()", "caddy_installieren()", "caddy_neu_laden()", "caddy_ports_pruefen()"):
            self.assertNotIn(verschoben, c, verschoben)
            self.assertIn(verschoben, code(SITE_BIB), verschoben)
        self.assertNotRegex(c, r'(?m)^INTERN=')
        self.assertRegex(code(SITE_BIB), r'(?m)^INTERN="127\.0\.0\.0/8 172\.24\.0\.1/32 192\.168\.178\.136/32"$')
        # die Bibliothek wird erst nach dem Herkunftsschutz geladen (das Skript allein im unsicheren Ordner bricht davor ab)
        quelle = c.index('. "$HERE/wamo-labor-site.sh"')
        self.assertLess(c.index('pruefe_ordner "$HERE"'), quelle)
        self.assertEqual(c.count('. "$HERE/wamo-labor-site.sh"'), 1)
        self.assertEqual(c.count("__BETREUER_ADRESSEN__"), 0)  # der Platzhalter wird nur in der Bibliothek eingesetzt


if __name__ == "__main__":
    unittest.main()
