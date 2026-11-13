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

Die Datei ist länger als 800 Zeilen (bewusste Ausnahme, Muster test_labor_betrieb_skripte.py): Bibliothek, Aktualisierer, Einrichtung, Unit und Dokumentation teilen
dieselbe Werkstatt (Wegwerf-Ordner, Attrappen, Protokoll, ein eingerichteter Betrieb) und dieselben Hilfen. Unter Windows laufen die Probeläufe mit der Git-Bash
ohne Startdatei (siehe test_labor_betrieb_skripte.py).
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_labor_betrieb_skripte import (
    ATTRAPPE, BASH, BASIS_IMAGE, EINRICHTEN, RUECKWEG, SITE, SOKRATES2, VERBOTENE_WOERTER, BetriebsstandWerkstatt, Werkstatt, code, funktionen, lies, ohne_kommentare,
    posix, schreibe, system_pfad,
)

SITE_BIB = SOKRATES2 / "wamo-labor-site.sh"
AKTUALISIERER = SOKRATES2 / "wamo-labor-geraete-aktualisieren.sh"
GERAETE_EINRICHTEN = SOKRATES2 / "wamo-labor-geraete-einrichten.sh"
DIENST = SOKRATES2 / "wamo-labor-geraete.service"
TIMER = SOKRATES2 / "wamo-labor-geraete.timer"
NEUE_DATEIEN = [SITE_BIB, AKTUALISIERER, GERAETE_EINRICHTEN, DIENST, TIMER]
NEUE_SKRIPTE = (SITE_BIB, AKTUALISIERER, GERAETE_EINRICHTEN)
INTERN = "127.0.0.0/8 172.24.0.1/32 192.168.178.136/32"
FRIST = 12 * 3600
GERAET = "WMC-H-04-NS"
STAND_REL = "etc/wamo-labor/betreuer-geraete.stand"  # der Stand im Probelauf (relativ zur Wurzel)


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
                    self.assertTrue(re.fullmatch(r"[A-Za-z_]+[_-][A-Za-z_-]+|[a-z0-9-]+|[A-Za-z_=]+", wort) or "/" in wort or "$" in wort, f"{f.name}: {wort[:20]}")

    def test_alle_skripte_die_site_oder_caddy_aendern_nehmen_dieselbe_sperre(self):
        for skript in (AKTUALISIERER, GERAETE_EINRICHTEN, EINRICHTEN, RUECKWEG):
            c = code(skript)
            self.assertIn("run/wamo-labor-geraete", c, skript.name)
            self.assertRegex(c, r"(sperre_nehmen|exec 9>)", skript.name)
        bib = code(SITE_BIB)
        self.assertIn("sperre_nehmen()", bib)
        self.assertIn("flock -n 9", bib)  # nicht warten: der Aktualisierer überspringt einen Lauf
        self.assertIn('flock -w "$2" 9', bib)

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


# Attrappe für flock: Beim Aufruf hat gerade ein anderer Lauf (der Aktualisierer) einen neuen Stand geschrieben
FLOCK_SCHREIBT_STAND = r"""#!/bin/sh
echo "flock $*" >> "$ATTRAPPE_LOG"
printf '%s\n' "WMC-H-04-NS 192.168.178.99 $(date +%s)" > "$STAND_NEU"
exit 0
"""


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
        schreibe(self.p(STAND_REL),
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
        schreibe(self.p(STAND_REL), f"X 10.0.0.5 {int(time.time())}\n")
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

    def test_caddy_schritt_nimmt_die_gemeinsame_sperre_vor_der_pruefung(self):
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aufrufe = self.aufrufe()
        adapt = next(i for i, z in enumerate(aufrufe) if z.startswith("docker run") and "caddy adapt" in z)
        self.assertIn("flock -w 120 9", aufrufe)
        self.assertLess(aufrufe.index("flock -w 120 9"), adapt)
        self.assertTrue(self.p("run/wamo-labor-geraete").is_dir())

    def test_haelt_der_aktualisierer_die_sperre_aendert_einrichten_die_site_nicht(self):
        self.ausgabe("flock", "", rc=1)
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Aktualisierer", r.stderr)
        self.assertFalse((self.caddy / "etc" / "kern" / "wamo-labor.caddy").exists())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^docker (run|exec wamo-caddy caddy reload)|^docker compose up", z)

    def test_der_stand_wird_erst_unter_der_sperre_gelesen(self):
        """Ein Lauf des Aktualisierers, der zwischen Vorprüfung und Site fertig wird, darf nicht mit altem Stand überschrieben werden."""
        schreibe(self.p(STAND_REL), f"WMC-H-04-NS 192.168.178.78 {int(time.time())}\n")
        schreibe(self.bin / "flock", FLOCK_SCHREIBT_STAND, ausfuehrbar=True)
        r = self.lauf(EINRICHTEN, "einrichten", "--basis-image", BASIS_IMAGE, extra={"STAND_NEU": posix(self.p(STAND_REL))})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("192.168.178.99", self.site())  # der frische Stand gilt
        self.assertNotIn("192.168.178.78", self.site())

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


# ====================================================================================== Werkstatt für den Aktualisierer
IMAGE_SCHLUESSEL = "docker.inspect_{{.Config.Image}}"  # docker inspect -f {{.Config.Image}} wamo-caddy (das zweite Argument bleibt unverändert im Schlüssel)
RELOAD = "docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile"
TOKEN_KERN, TOKEN_BETREUER = "k" * 43, "b" * 43  # keine echten Werte, nur Marken: sie dürfen nirgends auftauchen


class GeraeteWerkstatt(Werkstatt):
    """Ein eingerichteter Betrieb in der Wurzel: Site mit den festen Adressen, Bibliothek und Vorlage des Aktualisierers, eine Liste mit einem Gerät."""

    JETZT = 1_800_000_000

    def setUp(self):
        super().setUp()
        for n in ("nmblookup", "avahi-resolve", "flock", "logger"):
            schreibe(self.bin / n, ATTRAPPE, ausfuehrbar=True)
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "# Betreuer\n192.168.178.50\n")
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), f"# Geräte\n{GERAET}\n")
        schreibe(self.p("etc/wamo-labor/kern-token"), TOKEN_KERN)
        schreibe(self.p("etc/wamo-labor/betreuer-token"), TOKEN_BETREUER)
        self.lib = self.p("usr/local/lib/wamo-labor-geraete")
        schreibe(self.lib / "wamo-labor-site.sh", lies(SITE_BIB))
        schreibe(self.lib / "wamo-labor.caddy", lies(SITE))
        schreibe(self.site_datei, lies(SITE).replace("__BETREUER_ADRESSEN__", f"{INTERN} 192.168.178.50"))
        self.site_vorher = self.site_datei.read_bytes()
        self.ausgabe(IMAGE_SCHLUESSEL, "caddy:2-test\n")
        self.antworten(GERAET)

    @property
    def site_datei(self):
        return self.caddy / "etc" / "kern" / "wamo-labor.caddy"

    def antworten(self, name: str, netbios: str = "", mdns: str = "", rc: int | None = None) -> None:
        """Was die Namensauflösung für einen Namen liefert (leer = keine Antwort)."""
        self.ausgabe(f"nmblookup.{name}", netbios, rc=rc)
        self.ausgabe(f"avahi-resolve.{name}.local", mdns, rc=rc)

    def netbios(self, *adressen: str, name: str = GERAET) -> str:
        return f"querying {name} on 192.168.178.255\n" + "".join(f"{a} {name}<00>\n" for a in adressen)

    def mdns(self, *adressen: str, name: str = GERAET) -> str:
        return "".join(f"{name}.local\t{a}\n" for a in adressen)

    def aktualisieren(self, *args: str, jetzt: int | None = None, extra: dict | None = None) -> subprocess.CompletedProcess:
        env = {"WAMO_LABOR_GERAETE_WURZEL": posix(self.wurzel), "WAMO_LABOR_GERAETE_JETZT": str(jetzt or self.JETZT)}
        env.update(extra or {})
        return self.lauf(AKTUALISIERER, *args, extra=env, wurzel=False)

    def site(self) -> str:
        return self.site_datei.read_text(encoding="utf-8")

    def remote_ip(self, *adressen: str) -> str:
        return f"remote_ip {INTERN} 192.168.178.50" + "".join(f" {a}" for a in adressen) + "\n"

    def stand(self) -> list[tuple[str, str, int]]:
        datei = self.p(STAND_REL)
        zeilen = [z.split() for z in datei.read_text(encoding="utf-8").splitlines() if z.strip() and not z.startswith("#")]
        return [(n, a, int(z)) for n, a, z in zeilen]

    def zaehle(self, anfang: str) -> int:
        return len([z for z in self.aufrufe() if z.startswith(anfang)])

    def reloads(self) -> int:
        return len([z for z in self.aufrufe() if z == RELOAD])

    def prueflaeufe(self) -> int:
        return len([z for z in self.aufrufe() if z.startswith("docker run") and "caddy adapt" in z])

    def arbeitsreste(self) -> list:
        return [d for d in self.caddy.iterdir() if d.name.startswith(".arbeit.")]


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class AufloesungTests(GeraeteWerkstatt):
    def test_vereinigung_aus_netbios_und_mdns_gefiltert_auf_das_buero_lan(self):
        """Gemessen am Server für Nikos PC: NetBIOS liefert .78 und virtuelle 172.x, mDNS liefert .67. Beides gilt, die virtuellen Adressen nicht."""
        self.antworten(GERAET, self.netbios("192.168.178.78", "172.17.0.1", "172.20.208.1"), self.mdns("192.168.178.67"))
        r = self.aktualisieren()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.site().count(self.remote_ip("192.168.178.67", "192.168.178.78")), 1, self.site())
        self.assertEqual(self.stand(), [(GERAET, "192.168.178.67", self.JETZT), (GERAET, "192.168.178.78", self.JETZT)])
        self.assertNotIn("172.17.0.1", self.site())
        self.assertNotIn("172.20.208.1", self.site())
        aufrufe = self.aufrufe()
        self.assertIn(f"nmblookup {GERAET}", aufrufe)
        self.assertIn(f"avahi-resolve -4 -n {GERAET}.local", aufrufe)

    def test_nur_eine_quelle_antwortet(self):
        self.antworten(GERAET, mdns=self.mdns("192.168.178.67"), netbios="name_query failed to find name WMC-H-04-NS\n")
        self.assertEqual(self.aktualisieren().returncode, 0)
        self.assertIn(self.remote_ip("192.168.178.67"), self.site())
        self.ausgabe(f"nmblookup.{GERAET}", self.netbios("192.168.178.78"))
        self.ausgabe(f"avahi-resolve.{GERAET}.local", "Failed to resolve host name\n", rc=1)  # avahi scheitert, nmblookup antwortet
        self.assertEqual(self.aktualisieren(jetzt=self.JETZT + 60).returncode, 0)
        self.assertIn(self.remote_ip("192.168.178.78"), self.site())

    def test_filter_nur_adressen_im_buero_lan(self):
        schlecht = ["10.0.0.5", "192.168.179.7", "192.168.178.0", "192.168.178.255", "169.254.1.1", "127.0.0.1", "192.168.178.07", "192.168.178.256",
                    "192.168.178.1/24", "192.168.178.1.2", "172.17.0.1", "::1", "fe80::1", "192.168.17.8", "8.8.8.8"]
        self.antworten(GERAET, self.netbios(*schlecht), self.mdns(*schlecht))
        r = self.aktualisieren()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)  # keine Adresse gilt: nichts ändert sich
        self.assertEqual(self.reloads(), 0)
        self.assertEqual(self.stand(), [])
        self.antworten(GERAET, self.netbios("192.168.178.1", "192.168.178.254"), "")
        self.assertEqual(self.aktualisieren(jetzt=self.JETZT + 60).returncode, 0)
        self.assertIn(self.remote_ip("192.168.178.1", "192.168.178.254"), self.site())  # die Grenzen .1 und .254 gelten

    def test_die_namensquellen_sind_genau_netbios_und_mdns(self):
        c = code(AKTUALISIERER)
        self.assertIn("nmblookup", c)
        self.assertIn("avahi-resolve", c)
        for nie in ("getent", "dig ", "nslookup", "host ", "resolvectl", "arp ", "ip neigh", "ping"):
            self.assertNotIn(nie, c, nie)  # das DNS der Fritz!Box liefert veraltete Adressen und gehört nicht dazu

    def test_hoechstens_vier_adressen_je_name(self):
        sechs = [f"192.168.178.{n}" for n in (90, 20, 30, 40, 50, 60)]
        self.antworten(GERAET, self.netbios(*sechs[:3]), self.mdns(*sechs[3:]))
        r = self.aktualisieren()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([a for _, a, _ in self.stand()], ["192.168.178.20", "192.168.178.30", "192.168.178.40", "192.168.178.50"])
        self.assertIn(self.remote_ip("192.168.178.20", "192.168.178.30", "192.168.178.40", "192.168.178.50"), self.site())
        self.assertNotIn("192.168.178.60", self.site())
        self.assertNotIn("192.168.178.90", self.site())
        self.assertIn("höchstens 4", r.stdout)  # die Kürzung steht in der Meldung

    def test_mehrere_geraete_gleichzeitig_und_namen_in_grossbuchstaben(self):
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), "pc-eins\nPC-ZWEI  # Kommentar\r\nPC-EINS\n")
        self.antworten("PC-EINS", self.netbios("192.168.178.21", name="PC-EINS"), self.mdns(name="PC-EINS"))
        self.antworten("PC-ZWEI", "", self.mdns("192.168.178.22", name="PC-ZWEI"))
        r = self.aktualisieren()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.stand(), [("PC-EINS", "192.168.178.21", self.JETZT), ("PC-ZWEI", "192.168.178.22", self.JETZT)])
        self.assertEqual(self.zaehle("nmblookup PC-EINS"), 1)  # der doppelte Name wird nur einmal gefragt
        self.assertIn(self.remote_ip("192.168.178.21", "192.168.178.22"), self.site())

    def test_dieselbe_adresse_bei_zwei_namen_steht_einmal_in_der_site(self):
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), "PC-EINS\nPC-ZWEI\n")
        self.antworten("PC-EINS", self.netbios("192.168.178.21", name="PC-EINS"))
        self.antworten("PC-ZWEI", self.netbios("192.168.178.21", name="PC-ZWEI"))
        self.assertEqual(self.aktualisieren().returncode, 0)
        zeile = re.search(r"^	remote_ip .*$", self.site(), re.M).group(0)  # die Kommentare der Vorlage nennen den Platzhalter auch und werden mit ersetzt
        self.assertEqual(zeile.count("192.168.178.21"), 1)
        self.assertEqual(len(self.stand()), 2)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class NamenListeTests(GeraeteWerkstatt):
    def test_ungueltige_namen_brechen_ab_und_aendern_nichts(self):
        for schlecht in ("WMC_H_04", "-WMC", "A" * 16, "WMC H", "WMC-H.local", "PÄ-PC", "pc;reboot", "'PC'", "PC$(reboot)", "192.168.178.78/24", "*"):
            self.log.write_text("", encoding="utf-8")
            schreibe(self.p("etc/wamo-labor/betreuer-geraete"), f"# Kopf\n{GERAET}\n{schlecht}\n")
            r = self.aktualisieren()
            self.assertNotEqual(r.returncode, 0, schlecht)
            self.assertIn("Ungültiger Gerätename", r.stderr, schlecht)
            self.assertIn("Zeile 3", r.stderr, schlecht)
            self.assertEqual(self.aufrufe(), [], schlecht)  # nichts gefragt, nichts geändert: auch der gute Name vorher wird nicht aufgelöst
            self.assertEqual(self.site_datei.read_bytes(), self.site_vorher, schlecht)
            self.assertFalse(self.p(STAND_REL).exists(), schlecht)

    def test_gueltige_grenzfaelle(self):
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), "# nur Kommentar\n\n  \t\n1PC\nA23456789012345\nwmc-h-04-ns\r\n")
        for n in ("1PC", "A23456789012345", GERAET):
            self.antworten(n, self.netbios("192.168.178.30", name=n))
        r = self.aktualisieren()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted({n for n, _, _ in self.stand()}), sorted(["1PC", "A23456789012345", GERAET]))

    def test_mehr_als_16_namen_brechen_ab(self):
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), "".join(f"PC-{i}\n" for i in range(17)))
        r = self.aktualisieren()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("16", r.stderr)
        self.assertEqual(self.aufrufe(), [])

    def test_ohne_datei_oder_ohne_namen_gibt_es_keine_geraete(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren().returncode, 0)
        self.assertIn("192.168.178.78", self.site())
        self.p("etc/wamo-labor/betreuer-geraete").unlink()
        self.log.write_text("", encoding="utf-8")
        r = self.aktualisieren(jetzt=self.JETZT + 60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)  # die Adresse des entfernten Namens fällt weg
        self.assertEqual(self.stand(), [])
        self.assertEqual(self.zaehle("nmblookup"), 0)  # ohne Namen wird nichts gefragt

    def test_name_aus_der_liste_entfernt_nimmt_die_adresse_sofort_zurueck(self):
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), "PC-EINS\nPC-ZWEI\n")
        self.antworten("PC-EINS", self.netbios("192.168.178.21", name="PC-EINS"))
        self.antworten("PC-ZWEI", self.netbios("192.168.178.22", name="PC-ZWEI"))
        self.assertEqual(self.aktualisieren().returncode, 0)
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), "PC-EINS\n")
        r = self.aktualisieren(jetzt=self.JETZT + 60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.remote_ip("192.168.178.21"), self.site())
        self.assertNotIn("192.168.178.22", self.site())
        self.assertIn("PC-ZWEI", r.stdout)
        self.assertIn("nicht mehr in der Liste", r.stdout)

    def test_ungueltige_feste_adresse_bricht_auch_ohne_aenderung_ab(self):
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "192.168.178.50\n1.2.3.4;reboot\n")
        r = self.aktualisieren()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Ungültige Adresse", r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class FristUndAenderungTests(GeraeteWerkstatt):
    def test_zwoelf_stunden_frist_bei_schweigendem_geraet(self):
        t0 = self.JETZT
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren(jetzt=t0).returncode, 0)
        self.assertIn(self.remote_ip("192.168.178.78"), self.site())
        self.assertEqual(self.reloads(), 1)
        self.antworten(GERAET)  # der PC ist im Ruhezustand
        r = self.aktualisieren(jetzt=t0 + FRIST)  # genau 12 Stunden: noch zugelassen
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((r.stdout, r.stderr), ("", ""))
        self.assertIn(self.remote_ip("192.168.178.78"), self.site())
        self.assertEqual(self.stand(), [(GERAET, "192.168.178.78", t0)])  # der Zeitpunkt der letzten Antwort bleibt
        self.assertEqual(self.reloads(), 1)
        r = self.aktualisieren(jetzt=t0 + FRIST + 1)  # eine Sekunde mehr: die Adresse fällt weg
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertEqual(self.reloads(), 2)
        self.assertEqual(self.stand(), [])
        self.assertIn(f"Gerät {GERAET}", r.stdout)
        self.assertIn("192.168.178.78 entfernt", r.stdout)
        self.assertIn("12 Stunden", r.stdout)

    def test_antwort_nach_dem_ruhezustand_laesst_das_geraet_sofort_wieder_zu(self):
        t0 = self.JETZT
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren(jetzt=t0).returncode, 0)
        self.antworten(GERAET)
        self.assertEqual(self.aktualisieren(jetzt=t0 + FRIST + 100).returncode, 0)
        self.assertNotIn("192.168.178.78", self.site())
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren(jetzt=t0 + FRIST + 160).returncode, 0)
        self.assertIn(self.remote_ip("192.168.178.78"), self.site())

    def test_neue_antwort_ersetzt_die_alte_adresse_sofort(self):
        t0 = self.JETZT
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren(jetzt=t0).returncode, 0)
        self.antworten(GERAET, self.netbios("192.168.178.79"))  # der PC hat eine neue Adresse (DHCP)
        r = self.aktualisieren(jetzt=t0 + 60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.remote_ip("192.168.178.79"), self.site())
        self.assertNotIn("192.168.178.78", self.site())  # nicht erst nach 12 Stunden
        self.assertEqual(self.stand(), [(GERAET, "192.168.178.79", t0 + 60)])
        self.assertIn("192.168.178.78 entfernt", r.stdout)
        self.assertIn("192.168.178.79 zugelassen", r.stdout)

    def test_bleibt_die_menge_gleich_gibt_es_weder_pruefung_noch_reload_noch_ausgabe(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"), self.mdns("192.168.178.67"))
        self.assertEqual(self.aktualisieren(jetzt=self.JETZT).returncode, 0)
        site, geladen = self.site_datei.read_bytes(), (self.reloads(), self.prueflaeufe(), self.zaehle("logger"))
        self.assertEqual(geladen[:2], (1, 1))
        for minute in (1, 2, 3):
            r = self.aktualisieren(jetzt=self.JETZT + 60 * minute)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((r.stdout, r.stderr), ("", ""), "ohne Änderung bleibt das Journal leer")
        self.assertEqual(self.site_datei.read_bytes(), site)
        self.assertEqual((self.reloads(), self.prueflaeufe(), self.zaehle("logger")), geladen)
        self.assertEqual(self.zaehle("docker exec"), 1)
        self.assertEqual(self.stand(), [(GERAET, "192.168.178.67", self.JETZT + 180), (GERAET, "192.168.178.78", self.JETZT + 180)])  # nur der Zeitpunkt zieht nach

    def test_reload_nur_bei_aenderung_der_menge(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.aktualisieren(jetzt=self.JETZT)
        self.antworten(GERAET, self.netbios("192.168.178.78", "192.168.178.80"))
        self.aktualisieren(jetzt=self.JETZT + 60)
        self.aktualisieren(jetzt=self.JETZT + 120)
        self.assertEqual(self.reloads(), 2)
        self.assertEqual(self.prueflaeufe(), 2)
        self.assertIn(self.remote_ip("192.168.178.78", "192.168.178.80"), self.site())

    def test_feste_adressen_und_der_server_bleiben_in_der_site(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren().returncode, 0)
        site = self.site()
        self.assertIn(self.remote_ip("192.168.178.78"), site)  # Server selbst, feste Adresse, Gerät
        liste = f"{INTERN} 192.168.178.50"
        self.assertEqual(site.replace(f"{liste} 192.168.178.78", liste), self.site_vorher.decode("utf-8"))  # sonst bleibt die Site, wie sie war (auch die Kommentare nennen die Liste)
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "192.168.178.50\n192.168.178.60/32\n")  # eine geänderte feste Liste gilt bei der nächsten Änderung mit
        self.antworten(GERAET, self.netbios("192.168.178.79"))
        self.aktualisieren(jetzt=self.JETZT + 60)
        self.assertIn(f"remote_ip {INTERN} 192.168.178.50 192.168.178.60/32 192.168.178.79\n", self.site())

    def test_gepruefte_site_wird_installiert_und_der_arbeitsordner_geraeumt(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren().returncode, 0)
        aufrufe = self.aufrufe()
        adapt = next(z for z in aufrufe if z.startswith("docker run"))
        for soll in ("--rm", "--network none", "--cap-drop ALL", "--read-only", "no-new-privileges:true", "caddy:2-test", "caddy adapt"):
            self.assertIn(soll, adapt)
        self.assertLess(aufrufe.index(adapt), aufrufe.index(RELOAD))  # erst prüfen, dann installieren und laden
        for port in (55443, 55444, 55445, 55446):
            self.assertTrue([z for z in aufrufe[aufrufe.index(RELOAD):] if z.startswith("curl") and f":{port}/" in z], port)
        self.assertEqual(self.arbeitsreste(), [])
        self.assertTrue(self.site().endswith("\n"))

    def test_stand_wird_atomar_geschrieben_und_hat_einen_kopf(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren().returncode, 0)
        datei = self.p(STAND_REL)
        text = datei.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# "), text)
        self.assertIn(f"{GERAET} 192.168.178.78 {self.JETZT}\n", text)
        self.assertEqual([f.name for f in self.p("etc/wamo-labor").iterdir() if ".neu" in f.name], [])  # keine Reste
        if os.name != "nt":
            self.assertEqual(datei.stat().st_mode & 0o777, 0o644)

    def test_kaputter_stand_wird_gemeldet_und_nie_eingesetzt(self):
        schreibe(self.p(STAND_REL), f"{GERAET} 10.0.0.5 {self.JETZT}\nMUELL\n")
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        r = self.aktualisieren()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("übersprungen", r.stderr)
        self.assertNotIn("10.0.0.5", self.site())
        self.assertEqual(self.stand(), [(GERAET, "192.168.178.78", self.JETZT)])


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class RueckwegBeiFehlernTests(GeraeteWerkstatt):
    """Schlägt eine Stufe fehl, bleibt die vorherige Site unverändert aktiv und der Stand unverändert."""

    def setUp(self):
        super().setUp()
        self.antworten(GERAET, self.netbios("192.168.178.78"))

    def pruefe_rueckweg(self, r, text):
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(text, r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertFalse(self.p(STAND_REL).exists())  # der Stand gilt nur für das, was wirklich geladen ist
        self.assertEqual(self.arbeitsreste(), [])
        self.assertNotIn("zugelassen", r.stdout)  # nichts melden, was nicht gilt

    def test_pruefung_mit_caddy_adapt_scheitert(self):
        self.ausgabe("docker.run", "Error: unrecognized directive\n", rc=1)
        r = self.aktualisieren()
        self.pruefe_rueckweg(r, "vorherige Site")
        self.assertEqual(self.reloads(), 0)  # nichts installiert, nichts geladen

    def test_reload_scheitert_die_alte_site_kommt_zurueck(self):
        self.ausgabe("docker.exec_wamo-caddy_caddy", "reload failed\n", rc=1)
        r = self.aktualisieren()
        self.pruefe_rueckweg(r, "zurückgenommen")
        self.assertGreaterEqual(self.reloads(), 2)  # nach der Rücknahme wird noch einmal geladen
        self.assertIn("reload failed", r.stderr)

    def test_ports_antworten_nach_dem_reload_nicht(self):
        self.ausgabe("curl", "000")
        r = self.aktualisieren()
        self.pruefe_rueckweg(r, "zurückgenommen")
        self.assertIn("55443 bis 55446", r.stderr)
        aufrufe = self.aufrufe()
        letzter = max(i for i, z in enumerate(aufrufe) if z == RELOAD)
        self.assertTrue([z for z in aufrufe[letzter:] if z.startswith("curl")])  # auch nach der Rücknahme wird geprüft

    def test_die_naechste_minute_versucht_es_erneut(self):
        self.ausgabe("docker.run", "", rc=1)
        self.assertNotEqual(self.aktualisieren().returncode, 0)
        self.ausgabe("docker.run", "", rc=0)
        r = self.aktualisieren(jetzt=self.JETZT + 60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.remote_ip("192.168.178.78"), self.site())
        self.assertEqual(self.stand(), [(GERAET, "192.168.178.78", self.JETZT + 60)])

    def test_site_fehlt(self):
        self.site_datei.unlink()
        r = self.aktualisieren()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wamo-labor-einrichten.sh", r.stderr)
        self.assertFalse(self.p(STAND_REL).exists())
        self.assertEqual(self.reloads(), 0)

    def test_caddy_laeuft_nicht(self):
        self.ausgabe("docker.ps", "", rc=0)
        r = self.aktualisieren()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wamo-caddy läuft nicht", r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertFalse(self.p(STAND_REL).exists())

    def test_ohne_vorlage_oder_bibliothek_kein_start(self):
        (self.lib / "wamo-labor.caddy").unlink()
        r = self.aktualisieren()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wamo-labor.caddy", r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        (self.lib / "wamo-labor-site.sh").unlink()
        r = self.aktualisieren()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wamo-labor-site.sh", r.stderr)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class SperreMeldungUndGrenzenTests(GeraeteWerkstatt):
    def test_sperrdatei_mit_flock_zwei_laeufe_ueberschneiden_sich_nie(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren().returncode, 0)
        self.assertIn("flock -n 9", self.aufrufe())
        self.assertTrue(self.p("run/wamo-labor-geraete").is_dir())
        # ein anderer Lauf hält die Sperre: dieser Lauf fasst nichts an
        self.log.write_text("", encoding="utf-8")
        self.ausgabe("flock", "", rc=1)
        self.antworten(GERAET, self.netbios("192.168.178.99"))
        r = self.aktualisieren(jetzt=self.JETZT + 60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("anderer Lauf", r.stdout)
        self.assertEqual([z for z in self.aufrufe() if not z.startswith("flock")], [])  # nichts gefragt, nichts geladen
        self.assertNotIn("192.168.178.99", self.site())
        self.assertIn("run/wamo-labor-geraete", code(AKTUALISIERER))

    def test_meldung_im_journal_nur_bei_aenderung_mit_name_und_adresse(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        r = self.aktualisieren()
        meldungen = [z for z in self.aufrufe() if z.startswith("logger")]
        self.assertEqual(len(meldungen), 1, meldungen)
        self.assertRegex(meldungen[0], r"^logger -t wamo-labor-geraete .*" + GERAET + r".*192\.168\.178\.78")
        self.assertIn(f"Gerät {GERAET}: 192.168.178.78 zugelassen", r.stdout)
        # unter systemd ist stdout das Journal (Kennung per SyslogIdentifier): dann kein zweiter Weg über logger
        self.log.write_text("", encoding="utf-8")
        self.antworten(GERAET, self.netbios("192.168.178.79"))
        r = self.aktualisieren(jetzt=self.JETZT + 60, extra={"JOURNAL_STREAM": "8:12345"})
        self.assertIn("192.168.178.79 zugelassen", r.stdout)
        self.assertEqual([z for z in self.aufrufe() if z.startswith("logger")], [])

    def test_kein_token_in_ausgabe_protokoll_oder_code(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        r = self.aktualisieren()
        for ort in (r.stdout, r.stderr, "\n".join(self.aufrufe()), self.site(), self.p(STAND_REL).read_text(encoding="utf-8")):
            self.assertNotIn(TOKEN_KERN, ort)
            self.assertNotIn(TOKEN_BETREUER, ort)
        for datei in NEUE_SKRIPTE:
            self.assertIsNone(re.search(r"token", code(datei), re.I), datei.name)  # die Skripte lesen und nennen nie ein Token

    def test_fasst_weder_dienst_noch_firewall_noch_kern_an(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.aktualisieren().returncode, 0)
        erlaubt = re.compile(r"^(nmblookup |avahi-resolve |flock |logger |curl |docker (ps|inspect|run|exec wamo-caddy caddy reload))")
        for z in self.aufrufe():
            self.assertRegex(z, erlaubt, z)
        c = code(AKTUALISIERER) + code(SITE_BIB)
        for nie in ("docker compose", "docker rm", "docker stop", "docker restart", "docker kill", "docker network", "docker volume", "iptables", "wamo-firewall",
                    "systemctl", "wamo-core", "wamo-labor-dienst", "wamo-core-env"):
            self.assertNotIn(nie, c, nie)
        self.assertFalse(self.p("var/lib/wamo-labor").exists())
        self.assertEqual((self.caddy / "docker-compose.yml").read_text(encoding="utf-8"), "name: wamo-caddy\n# alter Stand\n")

    def test_trockenlauf_zeigt_nur_an(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"), self.mdns("192.168.178.67"))
        r = self.aktualisieren("--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        self.assertIn("192.168.178.78", r.stdout)
        self.assertIn("192.168.178.67", r.stdout)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertFalse(self.p(STAND_REL).exists())
        for z in self.aufrufe():
            self.assertRegex(z, r"^(nmblookup|avahi-resolve) ", z)  # kein docker, kein flock, kein logger

    def test_falsche_aufrufe_und_nur_als_root_ohne_probelauf(self):
        for args in (["--unbekannt"], ["x"], ["--trocken", "y"]):
            self.assertEqual(self.aktualisieren(*args).returncode, 2, args)
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            r = self.lauf(AKTUALISIERER, wurzel=False)  # ohne Probelauf und ohne root
            self.assertEqual(r.returncode, 1)
            self.assertIn("sudo", r.stderr)
            self.assertEqual(self.aufrufe(), [])

    def test_wurzel_muss_ein_sauberer_pfad_sein(self):
        r = self.aktualisieren(extra={"WAMO_LABOR_GERAETE_WURZEL": "relativ/pfad"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("WAMO_LABOR_GERAETE_WURZEL", r.stderr)
        self.assertEqual(self.aufrufe(), [])

    def test_zeitangabe_nur_aus_ziffern(self):
        r = self.aktualisieren(extra={"WAMO_LABOR_GERAETE_JETZT": "morgen"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.aufrufe(), [])

    def test_namen_gehen_nur_geprueft_und_als_einzelnes_argument_an_die_werkzeuge(self):
        c = code(AKTUALISIERER)
        self.assertRegex(c, r'timeout \S+ nmblookup "\$\{?\w+\}?"')
        self.assertRegex(c, r'timeout \S+ avahi-resolve -4 -n "\$\{?\w+\}?\.local"')
        self.assertIn('sperre_nehmen "$SPERRE" 0', c)  # nicht warten: ein laufender Lauf wird übersprungen
        self.assertIn("logger -t wamo-labor-geraete", c)
        self.assertIn("run/wamo-labor-geraete", c)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class LaborRueckwegSperreTests(BetriebsstandWerkstatt):
    """wamo-labor-rueckweg.sh entfernt die Site und lädt Caddy: Das geschieht unter derselben Sperre wie der Lauf des Aktualisierers."""

    def test_die_sperre_kommt_vor_dem_entfernen_der_site(self):
        r = self.lauf_skript(RUECKWEG)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aufrufe = self.aufrufe()
        self.assertLess(aufrufe.index("flock -w 60 9"), aufrufe.index(RELOAD))
        self.assertFalse((self.caddy / "etc" / "kern" / "wamo-labor.caddy").exists())

    def test_haelt_ein_anderer_lauf_die_sperre_bleibt_die_site(self):
        self.ausgabe("flock", "", rc=1)
        r = self.lauf_skript(RUECKWEG)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Aktualisierer", r.stderr)
        self.assertTrue((self.caddy / "etc" / "kern" / "wamo-labor.caddy").exists())
        self.assertFalse([z for z in self.aufrufe() if z.startswith("docker exec wamo-caddy caddy reload")])

    def test_trockenlauf_nimmt_keine_sperre(self):
        self.assertEqual(self.lauf_skript(RUECKWEG, "--trocken").returncode, 0)
        self.assertFalse([z for z in self.aufrufe() if z.startswith("flock")])


# ====================================================================================== Teil 3: Unit und Zeitgeber
class UnitTests(unittest.TestCase):
    def setUp(self):
        self.dienst = ohne_kommentare(lies(DIENST))
        self.timer = ohne_kommentare(lies(TIMER))

    def optionen(self, text: str) -> dict[str, str]:
        return {m.group(1): m.group(2).strip() for m in re.finditer(r"(?m)^([A-Za-z]+)=(.*)$", text)}

    def test_dienst_ist_ein_gehaerteter_oneshot_mit_der_kennung_im_journal(self):
        o = self.optionen(self.dienst)
        self.assertEqual(o["Type"], "oneshot")
        self.assertEqual(o["ExecStart"], "/usr/local/sbin/wamo-labor-geraete-aktualisieren")
        self.assertEqual(o["SyslogIdentifier"], "wamo-labor-geraete")
        for gehaertet in ("NoNewPrivileges", "PrivateTmp", "ProtectKernelTunables", "ProtectKernelModules", "ProtectKernelLogs", "ProtectControlGroups",
                          "ProtectClock", "ProtectHostname", "RestrictRealtime", "RestrictSUIDSGID", "LockPersonality"):
            self.assertEqual(o[gehaertet], "yes", gehaertet)
        self.assertEqual(o["ProtectSystem"], "strict")
        self.assertEqual(o["SystemCallArchitectures"], "native")
        self.assertEqual(o["RuntimeDirectory"], "wamo-labor-geraete")  # dort liegt die Sperre
        self.assertEqual(o["RuntimeDirectoryPreserve"], "yes")  # die Sperrdatei bleibt zwischen zwei Läufen, sonst hielte ein Lauf von Hand sie nicht
        for grenze in ("TimeoutStartSec", "MemoryMax", "TasksMax"):
            self.assertIn(grenze, o)

    def test_schreibrechte_nur_auf_zwei_ordner_und_nur_drei_faehigkeiten(self):
        o = self.optionen(self.dienst)
        self.assertEqual(o["ReadWritePaths"].split(), ["/etc/wamo-labor", "__CADDY_DIR__"])
        self.assertEqual(o["Environment"], "WAMO_LABOR_CADDY_DIR=__CADDY_DIR__")
        self.assertEqual(o["CapabilityBoundingSet"].split(), ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER"])
        familien = o["RestrictAddressFamilies"].split()
        self.assertEqual(sorted(familien), ["AF_INET", "AF_INET6", "AF_NETLINK", "AF_UNIX"])  # Netz bleibt: die Namensauflösung im LAN braucht es
        self.assertNotIn("PrivateNetwork", self.dienst)
        self.assertNotIn("ProtectHome", self.dienst)  # ProtectSystem=strict schützt /home schon, der Ordner von wamo-caddy ist freigegeben
        self.assertNotIn("User=", self.dienst)  # läuft als root (Besitzer wamocon setzen, Docker-Socket)

    def test_abhaengigkeiten_ohne_zwang(self):
        o = self.optionen(self.dienst)
        self.assertEqual(o["After"].split(), ["docker.service", "avahi-daemon.service"])
        self.assertNotIn("Wants=", self.dienst)  # ein Lauf startet weder Docker noch avahi, er scheitert sichtbar
        self.assertNotIn("Requires=", self.dienst)

    def test_timer_jede_minute_dreissig_sekunden_nach_dem_start(self):
        o = self.optionen(self.timer)
        self.assertEqual(o["OnBootSec"], "30s")
        self.assertEqual(o["OnUnitActiveSec"], "1min")
        self.assertEqual(o["Unit"], "wamo-labor-geraete.service")
        self.assertEqual(o["WantedBy"], "timers.target")
        self.assertEqual(o["Persistent"], "no")

    def test_kein_platzhalter_ausser_dem_ordner_von_caddy(self):
        self.assertEqual(sorted(set(re.findall(r"__[A-Z_]+__", self.dienst + self.timer))), ["__CADDY_DIR__"])


# ====================================================================================== Teil 4: wamo-labor-geraete-einrichten.sh
SBIN = "usr/local/sbin/wamo-labor-geraete-aktualisieren"
DIENST_ZIEL = "etc/systemd/system/wamo-labor-geraete.service"
TIMER_ZIEL = "etc/systemd/system/wamo-labor-geraete.timer"
ENABLE = "systemctl enable --now wamo-labor-geraete.timer"
DISABLE = "systemctl disable --now wamo-labor-geraete.timer"


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class EinrichtungsWerkstatt(GeraeteWerkstatt):
    """Der Betrieb vor der Einrichtung: Die Site gilt, die Liste der Namen und die festen Adressen sind da, der Aktualisierer ist es nicht."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.lib)
        self.ausgabe("systemctl.is-active", "")
        self.ausgabe("systemctl.is-enabled", "enabled\n")

    def lauf_geraete(self, *args: str, extra: dict | None = None, skript: Path = GERAETE_EINRICHTEN) -> subprocess.CompletedProcess:
        env = {"WAMO_LABOR_GERAETE_WURZEL": posix(self.wurzel)}
        env.update(extra or {})
        return self.lauf(skript, *args, extra=env, wurzel=False)

    def einrichten(self, *args: str, extra: dict | None = None) -> subprocess.CompletedProcess:
        return self.lauf_geraete("einrichten", *args, extra=extra)

    def installiert(self) -> list[str]:
        ziele = (SBIN, DIENST_ZIEL, TIMER_ZIEL, "usr/local/lib/wamo-labor-geraete/wamo-labor-site.sh", "usr/local/lib/wamo-labor-geraete/wamo-labor.caddy")
        return [z for z in ziele if self.p(z).exists()]

    def sicherungen(self) -> list[Path]:
        basis = self.p("var/backups/wamo-labor")
        return sorted(basis.iterdir()) if basis.exists() else []


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class EinrichtenTests(EinrichtungsWerkstatt):
    def test_einrichten_installiert_alles_und_prueft_am_ende(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for schritt in ("== Vorprüfung", "== Sicherung", "== Installation", "== Aktivierung", "== Prüfung", "Fertig."):
            self.assertIn(schritt, r.stdout)
        self.assertEqual(self.p(SBIN).read_bytes(), AKTUALISIERER.read_bytes())
        lib = self.p("usr/local/lib/wamo-labor-geraete")
        self.assertEqual((lib / "wamo-labor-site.sh").read_bytes(), SITE_BIB.read_bytes())
        self.assertEqual((lib / "wamo-labor.caddy").read_bytes(), SITE.read_bytes())
        self.assertEqual(self.p(TIMER_ZIEL).read_bytes(), TIMER.read_bytes())
        dienst = self.p(DIENST_ZIEL).read_text(encoding="utf-8")
        self.assertEqual(dienst, lies(DIENST).replace("__CADDY_DIR__", posix(self.caddy)))  # der Ordner von wamo-caddy steht darin
        self.assertNotIn("__CADDY_DIR__", dienst)
        aufrufe = self.aufrufe()
        self.assertIn("systemctl daemon-reload", aufrufe)
        self.assertLess(aufrufe.index("systemctl daemon-reload"), aufrufe.index(ENABLE))
        # die Prüfung am Ende ließ den Aktualisierer einmal laufen: Stand und Site
        self.assertTrue(self.p(STAND_REL).is_file())
        self.assertIn(self.remote_ip("192.168.178.78"), self.site())
        self.assertIn("192.168.178.78", r.stdout)  # der Status am Ende nennt Name und Adresse

    def test_vorlage_der_namensliste_nur_wenn_sie_fehlt(self):
        liste = self.p("etc/wamo-labor/betreuer-geraete")
        eigene = liste.read_bytes()
        self.assertEqual(self.einrichten().returncode, 0)
        self.assertEqual(liste.read_bytes(), eigene)  # der Inhalt gehört dem Betreiber
        liste.unlink()
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = liste.read_text(encoding="utf-8")
        self.assertTrue(all(z.startswith("#") or not z.strip() for z in text.splitlines()), text)  # nur Kommentare, kein Gerät
        for soll in ("Gerätename", "192.168.178.0/24", "12 Stunden", "höchstens 4", "Minute", "wamo-labor-geraete"):
            self.assertIn(soll, text)
        self.assertGreater(liste.stat().st_size, 0)

    def test_zweiter_lauf_ist_wiederholbar_und_sichert_den_vorigen_stand(self):
        self.assertEqual(self.einrichten().returncode, 0)
        erste = self.sicherungen()
        self.assertEqual(len(erste), 1)
        self.assertRegex(erste[0].name, r"^\d{8}-\d{6}")
        self.assertEqual((erste[0] / "betreuer-geraete.vorher").read_bytes(), self.p("etc/wamo-labor/betreuer-geraete").read_bytes())
        self.assertEqual((erste[0] / "wamo-labor.caddy.site.vorher").read_bytes(), self.site_vorher)
        self.assertFalse((erste[0] / "wamo-labor-geraete.service.vorher").exists())  # damals gab es noch nichts
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        zweite = self.sicherungen()
        self.assertEqual(len(zweite), 2)
        neu = [d for d in zweite if d not in erste][0]
        for datei in ("wamo-labor-geraete-aktualisieren.vorher", "wamo-labor-geraete.service.vorher", "wamo-labor-geraete.timer.vorher", "wamo-labor-site.sh.vorher"):
            self.assertTrue((neu / datei).is_file(), datei)
        for d in zweite:  # keine Token in den Sicherungen
            for f in d.iterdir():
                self.assertNotIn("token", f.name)
                self.assertNotIn(TOKEN_KERN, f.read_text(encoding="utf-8", errors="replace"))

    def test_kein_token_in_ausgabe_oder_protokoll(self):
        r = self.einrichten()
        for ort in (r.stdout, r.stderr, "\n".join(self.aufrufe())):
            self.assertNotIn(TOKEN_KERN, ort)
            self.assertNotIn(TOKEN_BETREUER, ort)

    def test_trockenlauf_aendert_nichts(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        r = self.einrichten("--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        for erwartet in ("wamo-labor-geraete-aktualisieren", "wamo-labor-geraete.service", "wamo-labor-geraete.timer", "wamo-labor-site.sh", "betreuer-geraete"):
            self.assertIn(erwartet, r.stdout)
        self.assertEqual(self.installiert(), [])
        self.assertEqual(self.sicherungen(), [])
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertFalse(self.p(STAND_REL).exists())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(systemctl (daemon-reload|enable|disable|start|stop)|docker (run|exec)|flock|logger)", z)

    def test_vorbedingungen_vor_jeder_aenderung(self):
        def abbruch(text, site=True):
            r = self.einrichten()
            self.assertNotEqual(r.returncode, 0, text)
            self.assertEqual(self.installiert(), [], text)
            if site:
                self.assertEqual(self.site_datei.read_bytes(), self.site_vorher, text)
            self.assertFalse([z for z in self.aufrufe() if z.startswith(("systemctl daemon-reload", "systemctl enable", "docker run"))], text)
            return r

        self.ausgabe("docker.ps", "", rc=0)
        self.assertIn("wamo-caddy", abbruch("caddy aus").stderr)
        self.ausgabe("docker.ps", "4711cafe\n")
        self.site_datei.unlink()
        self.assertIn("wamo-labor-einrichten.sh", abbruch("site fehlt", site=False).stderr)
        schreibe(self.site_datei, self.site_vorher.decode("utf-8"))
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), f"{GERAET}\nPC_UNGUELTIG\n")
        self.assertIn("Ungültiger Gerätename", abbruch("name").stderr)
        schreibe(self.p("etc/wamo-labor/betreuer-geraete"), f"{GERAET}\n")
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "192.168.178.50\nunsinn\n")
        self.assertIn("Ungültige Adresse", abbruch("adresse").stderr)
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "192.168.178.50\n")
        shutil.rmtree(self.p("etc/wamo-labor"))
        self.assertIn("/etc/wamo-labor", abbruch("ordner").stderr)

    def test_fehlende_werkzeuge_werden_vorab_gemeldet(self):
        if any(shutil.which(w, path=system_pfad()) for w in ("nmblookup", "avahi-resolve", "flock")):
            self.skipTest("das System hat nmblookup, avahi-resolve oder flock selbst")
        for werkzeug in ("nmblookup", "avahi-resolve", "flock"):
            ziel = self.bin / werkzeug
            inhalt = ziel.read_bytes()
            ziel.unlink()
            r = self.einrichten()
            self.assertNotEqual(r.returncode, 0, werkzeug)
            self.assertIn(f"{werkzeug} fehlt", r.stderr)
            self.assertEqual(self.installiert(), [])
            schreibe(ziel, inhalt.decode("utf-8"), ausfuehrbar=True)

    def test_unsauberer_ordner_von_caddy_bricht_ab(self):
        for falsch in ("/tmp/a|b", "/tmp/a b", "/tmp/a&b", "/tmp/a;b", "relativ"):
            r = self.einrichten(extra={"WAMO_LABOR_CADDY_DIR": falsch})
            self.assertNotEqual(r.returncode, 0, falsch)
            self.assertIn("WAMO_LABOR_CADDY_DIR", r.stderr)
            self.assertEqual(self.installiert(), [])

    def test_avahi_daemon_aus_ist_eine_warnung_kein_abbruch(self):
        self.ausgabe("systemctl.is-active_avahi-daemon", "inactive\n", rc=3)
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("avahi-daemon", r.stderr)
        self.assertIn("WARNUNG", r.stderr)

    def test_gefuellte_plaetze_liste_ist_eine_warnung_weil_die_firewall_keine_namen_kennt(self):
        schreibe(self.p("etc/wamo-labor/plaetze-adressen"), "# Plätze\n192.168.178.50\n")
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNUNG", r.stderr)
        self.assertIn("plaetze-adressen", r.stderr)
        self.assertIn("kennt keine Namen", r.stderr)

    def test_leere_oder_fehlende_plaetze_liste_ist_keine_warnung(self):
        for inhalt in (None, "# nur Kommentare\n\n"):
            if inhalt is not None:
                schreibe(self.p("etc/wamo-labor/plaetze-adressen"), inhalt)
            r = self.einrichten()
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("plaetze-adressen", r.stderr)

    def test_einrichten_nimmt_die_gemeinsame_sperre_fuer_die_aenderung_und_gibt_sie_vor_dem_pruefen_frei(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.einrichten().returncode, 0)
        aufrufe = self.aufrufe()
        self.assertIn("flock -w 60 9", aufrufe)
        self.assertLess(aufrufe.index("flock -w 60 9"), aufrufe.index("systemctl daemon-reload"))
        self.assertLess(aufrufe.index("flock -w 60 9"), aufrufe.index("flock -n 9"))  # erst E, dann der Lauf des Aktualisierers (er bekäme die Sperre sonst nie)
        c = code(GERAETE_EINRICHTEN)
        self.assertLess(c.index("sperre_loesen"), c.index("    pruefen\n"))

    def test_haelt_ein_anderer_lauf_die_sperre_aendert_einrichten_nichts(self):
        self.ausgabe("flock", "", rc=1)
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Sperre", r.stderr)
        self.assertEqual(self.installiert(), [])
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertEqual(self.sicherungen(), [])

    def test_scheitert_die_pruefung_nach_dem_lauf_kommen_site_und_stand_zurueck(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.ausgabe("systemctl.is-active_wamo-labor-geraete.timer", "inactive\n", rc=3)  # nach dem Lauf läuft der Timer nicht: Fehler der Prüfung
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("zurückgenommen", r.stderr)
        self.assertEqual(self.installiert(), [])
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)  # der Lauf hatte die Site geändert: Sie ist wieder, wie sie war
        self.assertFalse(self.p(STAND_REL).exists())  # der Lauf hatte den Stand geschrieben: Er ist wieder weg
        self.assertGreaterEqual(self.reloads(), 2)  # der Lauf hat geladen und die Rücknahme lädt noch einmal

    def test_scheitert_die_pruefung_kommt_der_vorige_stand_zurueck(self):
        alt = f"# alt\nALT-PC 192.168.178.40 {int(time.time()) - 60}\n"
        schreibe(self.p(STAND_REL), alt)
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.ausgabe("systemctl.is-active_wamo-labor-geraete.timer", "inactive\n", rc=3)
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.p(STAND_REL).read_text(encoding="utf-8"), alt)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)

    def test_pruefung_scheitert_alles_wird_zurueckgenommen(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.ausgabe("docker.run", "", rc=1)  # die Prüfung der neuen Site schlägt fehl: der Aktualisierer endet mit einem Fehler
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("zurückgenommen", r.stderr)
        self.assertEqual(self.installiert(), [])
        self.assertIn(DISABLE, self.aufrufe())
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertFalse(self.p(STAND_REL).exists())
        self.assertTrue(self.p("etc/wamo-labor/betreuer-geraete").is_file())  # die Liste des Betreibers bleibt

    def test_scheitert_ein_zweiter_lauf_kommt_die_vorige_fassung_zurueck(self):
        self.assertEqual(self.einrichten().returncode, 0)
        vorher = {z: self.p(z).read_bytes() for z in self.installiert()}
        schreibe(self.p(SBIN), "#!/bin/sh\necho alte Fassung\n", ausfuehrbar=True)
        vorher[SBIN] = self.p(SBIN).read_bytes()
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.ausgabe("docker.run", "", rc=1)
        self.log.write_text("", encoding="utf-8")
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual({z: self.p(z).read_bytes() for z in self.installiert()}, vorher)  # genau wie vor dem zweiten Lauf
        self.assertNotIn(DISABLE, self.aufrufe())  # der Timer lief schon vorher und bleibt an

    def test_status_nennt_nur_namen_adressen_und_zustaende(self):
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.einrichten().returncode, 0)
        self.log.write_text("", encoding="utf-8")
        r = self.lauf_geraete("status")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== Status", r.stdout)
        for soll in (GERAET, "192.168.178.78", "wamo-labor-geraete.timer", "enabled", "betreuer-geraete"):
            self.assertIn(soll, r.stdout)
        for wert in (TOKEN_KERN, TOKEN_BETREUER):
            self.assertNotIn(wert, r.stdout + r.stderr)
        self.assertFalse([z for z in self.aufrufe() if re.match(r"^(systemctl (enable|disable|start|stop|daemon-reload)|docker (run|exec))", z)])
        self.assertEqual(len(self.sicherungen()), 1)  # status legt nichts an

    def test_status_ohne_einrichtung(self):
        r = self.lauf_geraete("status")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fehlt", r.stdout)

    def test_falsche_aufrufe_und_nur_als_root_ohne_probelauf(self):
        for args in (["--unbekannt"], ["x"], ["einrichten", "--basis-image"], ["einrichten", "extra"]):
            self.assertEqual(self.lauf_geraete(*args).returncode, 2, args)
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            r = self.lauf(GERAETE_EINRICHTEN, "einrichten", wurzel=False)
            self.assertEqual(r.returncode, 1)
            self.assertIn("sudo", r.stderr)
            self.assertEqual(self.aufrufe(), [])

    @unittest.skipIf(os.name == "nt", "Rechte der Gruppe sind unter Windows nicht prüfbar")
    def test_herkunftsschutz(self):
        ordner = self.t / "unsicher"
        ordner.mkdir()
        ziel = ordner / GERAETE_EINRICHTEN.name
        shutil.copy(GERAETE_EINRICHTEN, ziel)
        os.chmod(ordner, 0o777)
        for befehl in ("einrichten", "rueckweg"):
            r = self.lauf_geraete(befehl, extra={"WAMO_LABOR_HERKUNFT": "ja"}, skript=ziel)
            self.assertEqual(r.returncode, 1, (befehl, r.stdout, r.stderr))
            self.assertIn("Unsicherer Ablageort", r.stderr)
            self.assertEqual(self.aufrufe(), [])

    def test_form_und_grenzen_des_einrichtungsskripts(self):
        c = code(GERAETE_EINRICHTEN)
        self.assertIn('. "$HERE/wamo-labor-site.sh"', c)  # dieselbe Bibliothek wie wamo-labor-einrichten.sh und der Aktualisierer
        self.assertLess(c.index('pruefe_ordner "$HERE"'), c.index('. "$HERE/wamo-labor-site.sh"'))
        for nie in ("docker compose", "docker rm", "docker stop", "docker restart", "iptables", "wamo-firewall", "wamo-core", "wamo-labor-dienst", "docker network"):
            self.assertNotIn(nie, c, nie)  # Container des Dienstes, Firewall und Kern bleiben unberührt
        self.assertNotRegex(c, r"systemctl\s+(restart|reload)\s")
        self.assertIn("WAMO_LABOR_GERAETE_WURZEL", c)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class RueckwegTests(EinrichtungsWerkstatt):
    def setUp(self):
        super().setUp()
        self.antworten(GERAET, self.netbios("192.168.178.78"))
        self.assertEqual(self.einrichten().returncode, 0)
        self.assertIn("192.168.178.78", self.site())
        self.log.write_text("", encoding="utf-8")

    def rueckweg(self, *args: str, extra: dict | None = None) -> subprocess.CompletedProcess:
        return self.lauf_geraete("rueckweg", *args, extra=extra)

    def test_rueckweg_stellt_die_site_aus_betreuer_adressen_allein_wieder_her(self):
        r = self.rueckweg()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)  # Server selbst und feste Adresse, keine Gerätenamen
        self.assertEqual(self.installiert(), [])
        self.assertFalse(self.p(STAND_REL).exists())
        self.assertFalse(self.p("usr/local/lib/wamo-labor-geraete").exists())
        # die Listen des Betreibers bleiben
        self.assertTrue(self.p("etc/wamo-labor/betreuer-geraete").is_file())
        self.assertIn("192.168.178.50", self.p("etc/wamo-labor/betreuer-adressen").read_text(encoding="utf-8"))
        aufrufe = self.aufrufe()
        self.assertIn(DISABLE, aufrufe)
        self.assertLess(aufrufe.index(DISABLE), aufrufe.index("systemctl daemon-reload"))
        self.assertEqual(self.reloads(), 1)
        self.assertEqual(self.prueflaeufe(), 1)  # erst prüfen, dann laden
        for port in (55443, 55444, 55445, 55446):
            self.assertTrue([z for z in aufrufe[aufrufe.index(RELOAD):] if z.startswith("curl") and f":{port}/" in z], port)
        self.assertEqual(self.arbeitsreste(), [])

    def test_rueckweg_nimmt_nur_feste_adressen_aus_der_datei_und_nie_die_aus_dem_stand(self):
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "192.168.178.50\n192.168.178.60/32\n")
        r = self.rueckweg()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"remote_ip {INTERN} 192.168.178.50 192.168.178.60/32\n", self.site())
        self.assertNotIn("192.168.178.78", self.site())

    def test_rueckweg_sichert_vorher_und_ohne_token(self):
        self.assertEqual(self.rueckweg().returncode, 0)
        letzte = self.sicherungen()[-1]
        self.assertIn(self.remote_ip("192.168.178.78").encode("utf-8"), (letzte / "wamo-labor.caddy.site.vorher").read_bytes())
        self.assertTrue((letzte / "wamo-labor-geraete.service.vorher").is_file())
        self.assertTrue((letzte / "betreuer-geraete.stand.vorher").is_file())
        for f in letzte.iterdir():
            self.assertNotIn(TOKEN_KERN, f.read_text(encoding="utf-8", errors="replace"))

    def test_rueckweg_ist_wiederholbar(self):
        self.assertEqual(self.rueckweg().returncode, 0)
        r = self.rueckweg()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertEqual(self.reloads(), 1)  # beim zweiten Mal gibt es nichts mehr zu laden

    def test_scheitert_die_site_geht_nur_der_timer_aus_und_der_rueckweg_bleibt_wiederholbar(self):
        self.ausgabe("docker.run", "", rc=1)
        site_mit_geraet = self.site_datei.read_bytes()
        r = self.rueckweg()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), site_mit_geraet)  # nichts halb geändert
        self.assertEqual(self.reloads(), 0)
        self.assertIn(DISABLE, self.aufrufe())  # der Timer ist aus: nichts ändert die Site mehr
        self.assertEqual(len(self.installiert()), 5)  # Aktualisierer, Bibliothek, Vorlage, Unit und Timer bleiben für den zweiten Versuch
        self.assertTrue(self.p(STAND_REL).is_file())  # auch der Stand
        self.assertNotIn("systemctl reset-failed wamo-labor-geraete.service", self.aufrufe())
        self.assertIn("wamo-labor-geraete-einrichten.sh rueckweg", r.stderr)  # der Weg zum zweiten Versuch
        self.assertIn("Timer", r.stderr)
        # nach der Behebung läuft der Rückweg durch
        self.ausgabe("docker.run", "", rc=0)
        r = self.rueckweg()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.installiert(), [])
        self.assertEqual(self.site_datei.read_bytes(), self.site_vorher)
        self.assertFalse(self.p(STAND_REL).exists())

    def test_rueckweg_raeumt_den_laufzeitordner_auf_die_sicherungen_bleiben(self):
        self.assertTrue(self.p("run/wamo-labor-geraete").is_dir())
        sicherungen = len(self.sicherungen())
        r = self.rueckweg()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.p("run/wamo-labor-geraete").exists())  # Sperre gelöst, Datei und Ordner weg
        self.assertGreaterEqual(len(self.sicherungen()), sicherungen)
        self.assertTrue(self.p("var/backups/wamo-labor").is_dir())
        r = self.rueckweg()  # ein zweiter Lauf ohne Ordner geht ebenfalls durch
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.p("run/wamo-labor-geraete").exists())

    def test_rueckweg_nimmt_die_sperre_vor_dem_laden_der_site(self):
        self.assertEqual(self.rueckweg().returncode, 0)
        aufrufe = self.aufrufe()
        self.assertLess(aufrufe.index("flock -w 60 9"), aufrufe.index(RELOAD))
        self.assertLess(aufrufe.index("flock -w 60 9"), aufrufe.index("systemctl daemon-reload"))

    def test_reload_scheitert_die_site_vor_dem_rueckweg_kommt_zurueck(self):
        self.ausgabe("docker.exec_wamo-caddy_caddy", "kaputt\n", rc=1)
        site_mit_geraet = self.site_datei.read_bytes()
        r = self.rueckweg()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.site_datei.read_bytes(), site_mit_geraet)
        self.assertGreaterEqual(self.reloads(), 2)
        self.assertEqual(self.arbeitsreste(), [])
        self.assertEqual(len(self.installiert()), 5)  # wie bei jedem Fehler der Site: nur der Timer ist aus

    def test_laeuft_gerade_ein_lauf_wird_nichts_geaendert(self):
        self.ausgabe("flock", "", rc=1)
        r = self.rueckweg()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Lauf", r.stderr)
        self.assertIn("flock -w 60 9", self.aufrufe())
        self.assertEqual(len(self.installiert()), 5)
        self.assertIn("192.168.178.78", self.site())
        self.assertEqual(self.reloads(), 0)

    def test_trockenlauf_aendert_nichts(self):
        r = self.rueckweg("--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        self.assertEqual(len(self.installiert()), 5)
        self.assertIn("192.168.178.78", self.site())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(systemctl (disable|stop|daemon-reload)|docker (run|exec)|flock|curl)", z)

    def test_ohne_einrichtung_gibt_es_nichts_zu_tun(self):
        self.assertEqual(self.rueckweg().returncode, 0)
        self.log.write_text("", encoding="utf-8")
        r = self.rueckweg()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Fertig.", r.stdout)

    def test_fasst_weder_dienst_noch_firewall_noch_kern_an(self):
        self.assertEqual(self.rueckweg().returncode, 0)
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(docker (rm|stop|restart|kill|compose|network|volume)|iptables|systemctl (restart|reload|stop) wamo-(core|firewall)|wamo-core-env)", z)


# ====================================================================================== Teil 5: Namensvertrag, README, CHANGELOG
REPO = Path(__file__).resolve().parent.parent


class DokumentationTests(unittest.TestCase):
    def test_namensvertrag_in_claude_md(self):
        zeile = next((z for z in lies(REPO / "CLAUDE.md").splitlines() if z.startswith("| Labor-Dienst (Betrieb) |")), None)
        self.assertIsNotNone(zeile, "Zeile Labor-Dienst (Betrieb) fehlt in der Tabelle")
        for soll in ("/etc/wamo-labor/betreuer-geraete", "betreuer-geraete.stand", "wamo-labor-geraete-aktualisieren.sh", "/usr/local/sbin/wamo-labor-geraete-aktualisieren",
                     "wamo-labor-geraete.service", "wamo-labor-geraete.timer", "wamo-labor-geraete-einrichten.sh", "wamo-labor-site.sh", "test_labor_geraete_skripte.py",
                     "wamo-labor-einrichten.sh", "55447"):
            self.assertIn(soll, zeile, soll)
        self.assertNotIn(chr(0x2014), zeile)
        self.assertNotIn(chr(0x2013), zeile)

    def abschnitt(self) -> str:
        text = lies(SOKRATES2 / "README.md")
        return text.split("### 19.1 Betreuer-Geräte über den Gerätenamen zulassen", 1)[1].split("\n## 20.", 1)[0]

    def test_unterabschnitt_im_readme_von_sokrates2(self):
        text = self.abschnitt()
        for soll in ("wamo-labor-geraete-einrichten.sh", "wamo-labor-geraete-aktualisieren", "wamo-labor-geraete.service", "wamo-labor-geraete.timer", "wamo-labor-site.sh",
                     "betreuer-geraete", "betreuer-geraete.stand", "--trocken", "rueckweg", "einrichten", "status", "NetBIOS", "mDNS", "192.168.178.0/24", "Vereinigung",
                     "12 Stunden", "höchstens 4", "höchstens 16", "caddy adapt", "caddy reload", "flock", "journalctl -t wamo-labor-geraete", "Nur am Server prüfbar",
                     "Grenzen", "nicht kryptografisch", "vortäuschen", "Betreuer-Token", "eine Minute", "Windows-, Mac- und Linux-Geräte", "plaetze-adressen",
                     "WMC-H-04-NS", "WAMO_LABOR_GERAETE_WURZEL", "ReadWritePaths", "betreuer-adressen"):
            self.assertIn(soll, text, soll)
        for verboten in (chr(0x2014), chr(0x2013)):
            self.assertNotIn(verboten, text)
        for wort in VERBOTENE_WOERTER:
            self.assertNotIn(wort, text.lower())

    def test_readme_verweist_aus_abschnitt_19_auf_den_unterabschnitt(self):
        text = lies(SOKRATES2 / "README.md")
        abschnitt19 = text.split("## 19. Labor-Dienst", 1)[1].split("### 19.1", 1)[0]
        for soll in ("wamo-labor-geraete-einrichten.sh", "test_labor_geraete_skripte.py", "Unterabschnitt 19.1"):
            self.assertIn(soll, abschnitt19, soll)

    def test_changelog_unter_hinzugefuegt(self):
        text = lies(REPO / "CHANGELOG.md")
        offen = text.split("## [Unveröffentlicht]", 1)[1].split("\n## [", 1)[0]
        hinzugefuegt = offen.split("### Hinzugefügt", 1)[1]
        eintrag = next((b for b in hinzugefuegt.split("\n- ") if b.lstrip("- ").startswith("Betreuer-Geräte des Testlabors über den Gerätenamen")), None)
        self.assertIsNotNone(eintrag, "Eintrag zu den Betreuer-Geräten fehlt unter Hinzugefügt")
        for soll in ("wamo-labor-geraete-aktualisieren.sh", "wamo-labor-geraete-einrichten.sh", "wamo-labor-site.sh", "NetBIOS", "mDNS", "12 Stunden", "caddy adapt",
                     "nicht kryptografisch", "Betreuer-Token", "flock", "test_labor_geraete_skripte.py"):
            self.assertIn(soll, eintrag, soll)
        self.assertNotIn(chr(0x2014), eintrag)
        self.assertNotIn(chr(0x2013), eintrag)


if __name__ == "__main__":
    unittest.main()
