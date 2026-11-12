"""Tests für den Betrieb des Labor-Dienstes auf Sokrates-2 (Testlabor 28.11.2026), nur statische Prüfungen und Probeläufe mit Attrappen:

  infra/sokrates2/wamo-labor/docker-compose.yml          Container wamo-labor-dienst, Netze, Härtung
  infra/sokrates2/wamo-caddy/kern/wamo-labor.caddy       Site auf HTTPS 55447 (Vorlage mit Platzhalter für die Betreuer-Adressen)
  infra/sokrates2/wamo-caddy/docker-compose.yml          Port 55447 und Netz wamo-labor-netz
  infra/sokrates2/wamo-firewall.sh                       Kette für 55447 (Adressliste der Plätze) und Sperre des Ausgangs
  infra/sokrates2/wamo-labor-einrichten.sh, wamo-labor-rueckweg.sh, wamo-labor-loeschlauf.sh
  infra/sokrates2/wamo_labor_rauchtest.py                Rauchtest (Attrappen für den Lauf ohne Server)

Nichts hier läuft gegen ein System: Die Skripte laufen im Probemodus in einem Wegwerf-Ordner (WAMO_LABOR_WURZEL) mit Attrappen für alle
Befehle mit Wirkung (docker, iptables, systemctl, wamo-core-env ...). Die Attrappen schreiben jeden Aufruf in ein Protokoll, daran prüfen
die Tests, was ein echter Lauf tun WÜRDE und was nie. Token-Werte erscheinen in keinem Test als Literal: Sie entstehen im Probelauf und
werden nur gelesen, nie ausgegeben.

Ausführen:  python -m pytest -q test_labor_betrieb_skripte.py   (im Ordner voice-core/)
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOKRATES2 = REPO / "infra" / "sokrates2"
LABOR = SOKRATES2 / "wamo-labor"
CADDY = SOKRATES2 / "wamo-caddy"
SITE = CADDY / "kern" / "wamo-labor.caddy"
FIREWALL = SOKRATES2 / "wamo-firewall.sh"
EINRICHTEN = SOKRATES2 / "wamo-labor-einrichten.sh"
RUECKWEG = SOKRATES2 / "wamo-labor-rueckweg.sh"
LOESCHLAUF = SOKRATES2 / "wamo-labor-loeschlauf.sh"
RAUCHTEST = SOKRATES2 / "wamo_labor_rauchtest.py"
SKRIPTE = [EINRICHTEN, RUECKWEG, LOESCHLAUF]
ALLE_DATEIEN = [LABOR / "docker-compose.yml", SITE, EINRICHTEN, RUECKWEG, LOESCHLAUF, RAUCHTEST]
VERBOTENE_WOERTER = ("nieeeki", "jarvis", "promptcontrol", "damicon")


def _bash():
    # Unter Windows zuerst die Bash ohne Startdatei: Die Bash in Git\bin stellt ihre eigenen Ordner (zum Beispiel mit curl) vor den PATH, dann
    # fänden die Probeläufe die echten Programme statt der Attrappen.
    kandidaten = [r"C:\Program Files\Git\usr\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    for k in kandidaten:
        if k and os.path.exists(k):
            return k
    return None


BASH = _bash()


def ohne_kommentare(text: str) -> str:
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


def lies(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8")


def code(pfad: Path) -> str:
    return ohne_kommentare(lies(pfad))


def block(text: str, kopf: str, tiefe: int | None = None) -> str:
    """Inhalt des Blocks, der mit der Zeile `kopf` beginnt (Tabulator-Einrückung wie im Caddyfile), bis zur Klammer in gleicher Spalte.
    Mit `tiefe` zählt nur ein Block genau dieser Einrückung (sonst der erste, in dem Text vorkommende)."""
    einzug = r"(\t*)" if tiefe is None else r"(\t{%d})" % tiefe
    m = re.search(r"^" + einzug + re.escape(kopf) + r" \{\n(.*?)^\1\}", text, re.S | re.M)
    assert m, f"Block {kopf!r} nicht gefunden"
    return m.group(2)


# ====================================================================================== Allgemeine Textprüfungen
class AllgemeinTests(unittest.TestCase):
    def test_alle_dateien_sind_da(self):
        for f in ALLE_DATEIEN:
            self.assertTrue(f.is_file(), f)

    def test_ohne_gedankenstriche_cr_und_verbotene_woerter(self):
        for f in [p for p in ALLE_DATEIEN if p.is_file()]:
            roh = f.read_bytes()
            self.assertNotIn(b"\r", roh, f.name)
            text = roh.decode("utf-8")
            self.assertNotIn(chr(0x2014), text, f.name)
            self.assertNotIn(chr(0x2013), text, f.name)
            for wort in VERBOTENE_WOERTER:
                self.assertNotIn(wort, text.lower(), f.name)
            self.assertIsNone(re.search(r"\b[A-Z]{2,10}-\d{3,5}\b", text.replace("LABOR-LOESCHEN", "")), f.name)  # keine Vorgangsnummern

    def test_keine_langen_zufallszeichenfolgen_als_literal(self):
        """Die Secret-Prüfung (gitleaks) schlägt bei Token-ähnlichen Literalen an: Es darf keines im Betrieb stehen."""
        for f in [p for p in ALLE_DATEIEN if p.is_file()]:
            for zeile in ohne_kommentare(lies(f)).splitlines():
                for wort in re.findall(r"[A-Za-z0-9_+/=-]{40,}", zeile):
                    self.assertTrue(re.fullmatch(r"[A-Za-z_]+[_-][A-Za-z_-]+|.*sha256.*|[a-z0-9-]+", wort) or "/" in wort or "$" in wort,
                                    f"{f.name}: {wort[:20]}")


# ====================================================================================== Teil 1: Compose des Dienstes
class ComposeDienstTests(unittest.TestCase):
    def setUp(self):
        self.text = lies(LABOR / "docker-compose.yml")
        self.code = ohne_kommentare(self.text)

    def test_projekt_container_und_image(self):
        self.assertRegex(self.code, r"(?m)^name: wamo-labor$")
        self.assertIn("container_name: wamo-labor-dienst", self.code)
        self.assertRegex(self.code, r"(?m)^\s+image: wamo-labor-dienst:\$\{WAMO_LABOR_STAND:-1\}$")
        self.assertIn("pull_policy: never", self.code)  # das Image wird am Server gebaut, nie gezogen
        self.assertNotIn(":latest", self.code)
        # Bau aus labor-dienst/Dockerfile mit dem Basis-Image als Build-Argument (der Digest kommt vom Einrichtungsskript)
        self.assertRegex(self.code, r"(?m)^\s+BASIS: \$\{WAMO_LABOR_BASIS_IMAGE:-\}$")
        self.assertRegex(lies(REPO / "labor-dienst" / "Dockerfile"), r"(?m)^ARG BASIS$")
        self.assertNotIn("@sha256:", self.code)  # der Digest steht in /etc/wamo-labor/basis-image, nicht doppelt im Compose

    def test_haertung(self):
        for soll in ('user: "10001:10001"', "read_only: true", "- ALL", "no-new-privileges:true", "init: true", "restart: unless-stopped",
                     "mem_limit: 256m", "memswap_limit: 256m", "cpus: 0.5", "pids_limit: 128", "/tmp:rw,noexec,nosuid,nodev",
                     'max-size: "10m"', 'max-file: "3"', "driver: json-file"):
            self.assertIn(soll, self.code)
        self.assertEqual(self.code.count("cap_drop:"), 1)
        for verboten in ("privileged", "network_mode", "pid:", "ipc:", "docker.sock", "cap_add", "devices:", "extra_hosts", "host.docker.internal",
                         "env_file", "expose:", "dns:", "sysctls", "restart: always"):
            self.assertNotIn(verboten, self.code, verboten)

    def test_healthcheck_gegen_gesundheit(self):
        m = re.search(r"healthcheck:\n(.*?)\n    \w", self.code + "\n    x", re.S)
        self.assertIsNotNone(m)
        self.assertIn('["CMD", "python", "-m", "wamo_labor_dienst", "gesundheit"]', m.group(1))
        for soll in ("interval: 15s", "timeout: 5s", "retries: 3", "start_period: 10s"):
            self.assertIn(soll, m.group(1))

    def test_nur_ein_veroeffentlichter_port_nur_auf_loopback(self):
        self.assertEqual(re.findall(r'^\s+- "([^"]*:\d+:\d+)"$', self.code, flags=re.M), ["127.0.0.1:18765:8080"])
        self.assertEqual(self.code.count("ports:"), 1)
        self.assertNotIn("0.0.0.0", self.code)

    def test_umgebung_namen_gibt_es_im_dienst_und_werte_sind_klar(self):
        konfig = lies(REPO / "labor-dienst" / "wamo_labor_dienst" / "konfig.py")
        bekannt = set(re.findall(r"WAMO_LABOR_DIENST_[A-Z_]+", konfig)) | {"WAMO_LABOR_DIENST_PROXYS"}
        env = dict(re.findall(r"^\s+(WAMO_LABOR_DIENST_[A-Z_]+): (\S+)$", self.code, flags=re.M))
        self.assertTrue(env)
        for name in env:
            self.assertIn(name, bekannt, name)
        self.assertEqual(env["WAMO_LABOR_DIENST_BIS"], '"2026-12-11"')
        self.assertEqual(env["WAMO_LABOR_DIENST_PROXYS"], "172.28.0.2")  # genau die Adresse von Caddy im Netz wamo-labor-netz
        self.assertEqual(env["WAMO_LABOR_DIENST_KERN_TOKEN_DATEI"], "/run/wamo-labor/kern-token")
        self.assertEqual(env["WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI"], "/run/wamo-labor/betreuer-token")

    def test_kein_geheimnis_im_klartext(self):
        for wert in re.findall(r":\s+\"?([A-Za-z0-9_+/=.-]+)\"?\s*$", self.code, flags=re.M):
            self.assertFalse(re.fullmatch(r"[A-Za-z0-9_-]{32,}", wert), wert[:12])
        self.assertNotRegex(self.code, r"(?i)(TOKEN|SECRET|PASSWORD|PASSWORT)\w*:\s+[\"']?(?!/)[A-Za-z0-9_+/=-]{20,}")  # Pfade beginnen mit /
        # die Token-Dateien kommen als Bind aus /etc/wamo-labor, nur lesend, ohne das Anlegen eines fehlenden Ordners
        for datei in ("kern-token", "betreuer-token"):
            m = re.search(r"- type: bind\n\s+source: \$\{WAMO_LABOR_ETC:-/etc/wamo-labor\}/" + datei + r"\n\s+target: /run/wamo-labor/" + datei
                          + r"\n\s+read_only: true\n\s+bind:\n\s+create_host_path: false", self.code)
            self.assertIsNotNone(m, datei)
        m = re.search(r"- type: bind\n\s+source: \$\{WAMO_LABOR_DATEN:-/var/lib/wamo-labor\}\n\s+target: /data\n\s+bind:\n\s+create_host_path: false", self.code)
        self.assertIsNotNone(m)
        self.assertEqual(self.code.count("- type: bind"), 3)

    def test_netze(self):
        dienst = self.code.split("\n  dienst:\n")[1].split("\nnetworks:")[0]
        self.assertIn("ipv4_address: 172.28.0.10", dienst)
        self.assertRegex(self.code, r"labor:\n\s+name: wamo-labor-netz\n\s+external: true")  # legt wamo-caddy an (internal)
        self.assertRegex(self.code, r"lokal:\n\s+name: wamo-labor-lokal\n(?!\s+internal)(\s+.*\n)*?\s+- subnet: 172\.28\.1\.0/29")
        self.assertIn("ipv4_address: 172.28.1.2", dienst)
        self.assertEqual(len(re.findall(r"^\s+- subnet:", self.code, flags=re.M)), 1)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML fehlt")
    def test_yaml_ist_gueltig_und_hat_einen_dienst(self):
        import yaml

        d = yaml.safe_load(self.text)
        self.assertEqual(list(d["services"]), ["dienst"])
        s = d["services"]["dienst"]
        self.assertEqual(s["ports"], ["127.0.0.1:18765:8080"])
        self.assertTrue(s["read_only"])
        self.assertEqual(s["cap_drop"], ["ALL"])
        self.assertEqual(s["user"], "10001:10001")
        self.assertEqual(sorted(s["networks"]), ["labor", "lokal"])


# ====================================================================================== Teil 1: Compose von wamo-caddy
class ComposeCaddyTests(unittest.TestCase):
    def setUp(self):
        self.code = code(CADDY / "docker-compose.yml")

    def test_port_55447_und_netz_wamo_labor_netz(self):
        self.assertIn('"0.0.0.0:55447:55447"', self.code)
        self.assertEqual(len(re.findall(r'"0\.0\.0\.0:\d+:\d+"', self.code)), 5)
        self.assertRegex(self.code, r"labor:\n\s+name: wamo-labor-netz\n\s+internal: true\n\s+driver_opts:\n\s+com\.docker\.network\.bridge\.inhibit_ipv4: \"true\""
                                    r"\n\s+ipam:\n\s+config:\n\s+- subnet: 172\.28\.0\.0/24")
        self.assertRegex(self.code, r"labor:\n\s+(#[^\n]*\n\s+)*ipv4_address: 172\.28\.0\.2")  # feste Adresse von Caddy, nur ihr vertraut der Dienst

    def test_bestehende_netze_und_ports_bleiben(self):
        for port in (55443, 55444, 55445, 55446):
            self.assertIn(f'"0.0.0.0:{port}:{port}"', self.code)
        self.assertRegex(self.code, r"networks:\s+default: \{\}\s+ui: \{\}\s+rd:\s+(#[^\n]*\s+)?ipv4_address: 172\.26\.0\.2")
        for netz in ("172.24.0.0/24", "172.25.0.0/24", "172.26.0.0/24"):
            self.assertIn(netz, self.code)


# ====================================================================================== Teil 1: Caddy-Site auf 55447
class CaddySiteTests(unittest.TestCase):
    def setUp(self):
        self.text = lies(SITE)
        self.code = ohne_kommentare(self.text)
        m = re.search(r"^https://192\.168\.178\.136:55447[^\n]*\{\n(.*?)^\}", self.code, re.S | re.M)
        self.assertIsNotNone(m, "Site auf 55447 nicht gefunden")
        self.site = m.group(1)

    def muster(self, name: str) -> re.Pattern:
        """Das Muster des Matchers aus der Datei, so wie Caddy es bekommt (die Muster sind einfach: in RE2 und re gleich)."""
        teil = re.search(r"@" + re.escape(name) + r" \{\n(.*?)\n\t\}", self.site, re.S)
        self.assertIsNotNone(teil, name)
        zeile = next(z for z in teil.group(1).splitlines() if z.strip().startswith("path_regexp"))
        return re.compile(zeile.strip().split(None, 1)[1])

    def test_adresse_tls_und_groesse(self):
        koepfe = re.findall(r"^(https://[^\n]*)\{$", self.code, flags=re.M)
        self.assertEqual(len(koepfe), 1)
        for h in ("https://192.168.178.136:55447", "https://sokrates2:55447", "https://spark-651f.local:55447"):
            self.assertIn(h, koepfe[0])
        self.assertIn("tls internal", self.site)
        self.assertRegex(self.site, r"request_body \{\n\t\tmax_size 2MB\n\t\}")

    def test_nur_diese_site_und_nur_eigene_schnipsel(self):
        self.assertEqual(len(re.findall(r"^https?://", self.code, flags=re.M)), 1)
        namen = re.findall(r"^\((\w+)\) \{$", self.code, flags=re.M)
        self.assertTrue(namen)
        for n in namen:
            self.assertTrue(n.startswith("labor_"), n)  # keine Kollision mit kern_weiter und anderen
        caddyfile = lies(CADDY / "Caddyfile")
        self.assertIn("import /etc/caddy/kern/*.caddy", caddyfile)  # geladen wird die Datei über das vorhandene Muster
        self.assertNotIn("55447", caddyfile)  # das Caddyfile bleibt unverändert

    def test_kern_wird_nie_weitergereicht(self):
        kern = block(self.site, "handle @kern")
        self.assertIn("respond", kern)
        self.assertIn("404", kern)
        for verboten in ("reverse_proxy", "labor_weiter", "handle_response"):
            self.assertNotIn(verboten, kern)
        p = self.muster("kern")
        for pfad in ("/api/kern", "/api/kern/", "/api/kern/durchlauf", "/api/kern/tickets/LAB-12", "//api/kern/durchlauf", "/api//kern/x",
                     "/API/KERN/durchlauf", "/api/Kern/tickets"):
            self.assertIsNotNone(p.search(pfad), pfad)
        for pfad in ("/api/kernel", "/api/browser/board", "/board"):
            self.assertIsNone(p.search(pfad), pfad)
        # genau drei Wege führen zum Dienst, keiner davon ist der Kern
        self.assertEqual(self.site.count("import labor_weiter"), 3)

    def test_nur_diese_pfade_gehen_zum_dienst(self):
        """Weiterleitung nur nach einer Liste (alles andere ist 404), die Muster erlauben weder Punkte noch Prozentzeichen noch Großschreibung."""
        browser, betreuer, intern = self.muster("browser_pfad"), self.muster("betreuer_pfad"), self.muster("gesundheit_pfad")
        for pfad in ("/", "/board", "/web/stil.css", "/web/dom.js", "/web/board-logik.js", "/web/betreuer-logik.js", "/api/browser/board"):
            self.assertIsNotNone(browser.fullmatch(pfad), pfad)
        for pfad in ("/api/browser/../kern/durchlauf", "/web/../x", "/web/x.html", "/api/browser/board/x", "/board/", "/BOARD", "/api/browser/%62oard",
                     "/betreuer", "/api/betreuer/plaetze", "/gesundheit", "/api/kern/durchlauf", "", "/web/", "/web/.js"):
            self.assertIsNone(browser.fullmatch(pfad), pfad)
        for pfad in ("/betreuer", "/api/betreuer/plaetze", "/api/betreuer/plaetze/3/neuer-durchlauf", "/api/betreuer/plaetze/16/daten-loeschen",
                     "/api/betreuer/einwilligung-minimum-setzen"):
            self.assertIsNotNone(betreuer.fullmatch(pfad), pfad)
        for pfad in ("/api/betreuer", "/api/betreuer/", "/api/betreuer/../kern/x", "/api/betreuer/plaetze/3/x/y/z", "/betreuer/x", "/Betreuer",
                     "/api/betreuer/%2e%2e/kern", "/board"):
            self.assertIsNone(betreuer.fullmatch(pfad), pfad)
        self.assertIsNotNone(intern.fullmatch("/gesundheit"))
        self.assertIsNone(intern.fullmatch("/gesundheit/x"))
        self.assertIsNone(intern.fullmatch("/api/kern/gesundheit"))

    def test_standard_ist_ablehnen(self):
        """Der letzte handle ohne Matcher fängt alles Übrige ab und reicht nichts weiter."""
        rest = re.search(r"\n\thandle \{\n(.*?)\n\t\}\n", self.site, re.S)
        self.assertIsNotNone(rest)
        self.assertIn("404", rest.group(1))
        for verboten in ("reverse_proxy", "labor_weiter"):
            self.assertNotIn(verboten, rest.group(1))
        # kein reverse_proxy außerhalb des Schnipsels labor_weiter
        self.assertEqual(self.code.count("reverse_proxy"), 1)
        schnipsel = block(self.code, "(labor_weiter)")
        self.assertIn("reverse_proxy wamo-labor-dienst:8080", schnipsel)

    def _begrenzt(self, pfad_handle: str, schnipsel: str) -> str:
        """Ein handle mit Pfad, das nur von bestimmten Adressen weiterreicht: Fremde bekommen 403, nur der Rest geht zum Dienst."""
        aussen = block(self.site, pfad_handle)
        fremd_matcher = block(aussen, "@fremd")
        self.assertIn("not {", fremd_matcher)
        self.assertIn(f"import {schnipsel}", fremd_matcher)
        fremd = block(aussen, "handle @fremd")
        self.assertIn("403", fremd)
        self.assertIn("import labor_koepfe", fremd)
        for verboten in ("reverse_proxy", "labor_weiter"):
            self.assertNotIn(verboten, fremd)
        self.assertLess(aussen.index("handle @fremd"), aussen.index("labor_weiter"))  # erst abweisen, dann weiterreichen
        self.assertEqual(aussen.count("import labor_weiter"), 1)
        return aussen

    def test_betreuer_nur_von_den_betreuer_adressen(self):
        self.assertEqual(self.code.count("__BETREUER_ADRESSEN__"), 1)  # ein Platzhalter, ein Schnipsel
        adressen = block(self.code, "(labor_betreuer_adressen)")
        self.assertEqual(adressen.strip(), "remote_ip __BETREUER_ADRESSEN__")
        self._begrenzt("handle @betreuer_pfad", "labor_betreuer_adressen")
        # die Betreuer-Pfade gehen über keinen anderen Weg zum Dienst
        self.assertNotIn("betreuer", block(self.site, "handle @browser_pfad"))
        self.assertNotIn("0.0.0.0/0", self.code)

    def test_gesundheit_nur_intern(self):
        self._begrenzt("handle @gesundheit_pfad", "labor_intern_adressen")
        adressen = block(self.code, "(labor_intern_adressen)")
        self.assertEqual(adressen.strip(), "remote_ip 127.0.0.0/8 172.24.0.1/32 192.168.178.136/32")  # der Server selbst: Loopback, Docker-Proxy (Gateway von wamo-caddy_default), eigene LAN-Adresse

    def test_browser_pfade_fuer_das_lan(self):
        ok = block(self.site, "handle @browser_pfad")
        self.assertIn("labor_weiter", ok)
        self.assertNotIn("remote_ip", ok)  # die Quellen begrenzt die Firewall (Kette WAMO-FW-LABOR), nicht Caddy

    def test_methoden(self):
        m = block(self.site, "@methode")
        self.assertIn("not method GET HEAD POST", m)
        h = block(self.site, "handle @methode")
        self.assertIn("405", h)
        self.assertNotIn("labor_weiter", h)

    def test_schutzkoepfe_fuer_fehler_und_ueberlast(self):
        koepfe = block(self.code, "(labor_koepfe)")
        for soll in ('Cache-Control "no-store"', 'X-Content-Type-Options "nosniff"', 'Referrer-Policy "no-referrer"', 'X-Frame-Options "DENY"',
                     "frame-ancestors 'none'", "default-src 'none'"):
            self.assertIn(soll, koepfe)
        # 403, 404, 405, Fehler von Caddy und die rohe 503 von uvicorn tragen dieselben Köpfe und einen kurzen deutschen Text
        for kopf in ("handle @methode", "handle @kern", "handle"):
            self.assertIn("import labor_koepfe", block(self.site, kopf, tiefe=1), kopf)
        for kopf in ("handle @betreuer_pfad", "handle @gesundheit_pfad"):
            self.assertIn("import labor_koepfe", block(block(self.site, kopf), "handle @fremd"), kopf)
        fehler = block(self.site, "handle_errors")
        self.assertIn("import labor_koepfe", fehler)
        self.assertIn("{http.error.status_code}", fehler)
        weiter = block(self.code, "(labor_weiter)")
        self.assertIn("status 503", weiter)
        self.assertIn("header Content-Type text/plain*", weiter)  # die rohe Antwort von uvicorn (Klartext), nie ein JSON-Fehler des Dienstes
        self.assertIn("handle_response @ueberlast", weiter)
        self.assertIn("import labor_koepfe", block(weiter, "handle_response @ueberlast"))
        self.assertIn("überlastet", self.code)
        self.assertIn("nicht erreichbar", self.code)

    def test_koepfe_des_dienstes_werden_nie_ueberschrieben(self):
        site_ohne_schnipsel = self.site
        self.assertNotIn("header_down", self.code)
        self.assertNotRegex(self.code, r">[A-Z][A-Za-z-]+ ")  # kein ">"-Kopf (der würde nach dem Dienst setzen und überschreiben)
        # "header" steht nur in den Antworten, die Caddy selbst erzeugt (Schnipsel und Allow), nie auf Site-Ebene
        for treffer in re.finditer(r"^\t(header\b.*)$", site_ohne_schnipsel, flags=re.M):
            self.fail(f"header auf Site-Ebene: {treffer.group(1)}")
        self.assertNotIn("encode", self.code)

    def test_zeitgrenzen_zum_dienst(self):
        weiter = block(self.code, "(labor_weiter)")
        for soll in ("dial_timeout 3s", "response_header_timeout 10s", "read_timeout 15s"):
            self.assertIn(soll, weiter)


# ====================================================================================== Teil 2: Firewall (Kette für 55447)
@unittest.skipUnless(BASH, "keine Bash vorhanden")
class FirewallLaborTests(unittest.TestCase):
    """wamo-firewall.sh regeln gibt aus, was start setzen würde (ohne root, ohne iptables)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="labor-fw-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def regeln(self, quellen: str | None = None) -> subprocess.CompletedProcess:
        if quellen is not None:
            (self.dir / "plaetze-adressen").write_text(quellen, encoding="utf-8")
        env = {"PATH": os.environ["PATH"], "WAMO_FW_CONF": str(self.dir / "gibt-es-nicht.conf"), "WAMO_FW_RD_ZIELE": str(self.dir / "ziele.toml"),
               "WAMO_FW_LABOR_QUELLEN": str(self.dir / "plaetze-adressen")}
        r = subprocess.run([BASH, str(FIREWALL), "regeln"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    @staticmethod
    def kette(ausgabe: str, name: str) -> list[str]:
        return [z for z in ausgabe.splitlines() if z.startswith(f"-A {name} ")]

    def test_port_55447_geht_in_die_labor_kette_und_die_anderen_bleiben(self):
        out = self.regeln().stdout
        self.assertIn("-A WAMO-FW -p tcp -m conntrack --ctdir ORIGINAL --ctorigdstport 55447 -j WAMO-FW-LABOR", out)
        for kette in ("WAMO-FW-LABOR", "WAMO-FW-LABAUS"):
            self.assertIn(f":{kette} - [0:0]", out)
        for port in (55443, 55444, 55445, 55446, 55322, 56543):
            self.assertIn(f"-A WAMO-FW -p tcp -m conntrack --ctdir ORIGINAL --ctorigdstport {port} -j WAMO-FW-LAN", out)
        self.assertIn("--ctorigdstport 55321 -j WAMO-FW-HTTP", out)
        self.assertNotIn("--ctorigdstport 55447 -j WAMO-FW-LAN", out)  # eigene Kette: Die Adressliste der Plätze ist austauschbar (Frage Q13)
        self.assertEqual(out.count("55447"), 1)

    def test_ohne_datei_gilt_das_buero_lan(self):
        kette = self.kette(self.regeln().stdout, "WAMO-FW-LABOR")
        self.assertEqual(kette[0], "-A WAMO-FW-LABOR -s 127.0.0.0/8 -j RETURN")
        self.assertEqual(kette[1], "-A WAMO-FW-LABOR -s 192.168.178.0/24 -j RETURN")
        self.assertIn("-j LOG", kette[2])
        self.assertEqual(kette[-1], "-A WAMO-FW-LABOR -j DROP")
        self.assertEqual(len(kette), 4)

    def test_nur_kommentare_in_der_datei_heisst_standard(self):
        kette = self.kette(self.regeln("# Plätze (Frage Q13), eine Adresse oder ein Netz je Zeile\n\n").stdout, "WAMO-FW-LABOR")
        self.assertIn("-A WAMO-FW-LABOR -s 192.168.178.0/24 -j RETURN", kette)

    def test_die_datei_ersetzt_das_buero_lan(self):
        r = self.regeln("# Plätze\n192.168.178.50\n192.168.178.60/32  # Platz 2\n\n  192.168.178.64/29 \n")
        quellen = [z.split()[3] for z in self.kette(r.stdout, "WAMO-FW-LABOR") if z.endswith("-j RETURN")]
        self.assertEqual(quellen, ["127.0.0.0/8", "192.168.178.50", "192.168.178.60/32", "192.168.178.64/29"])
        self.assertNotIn("-A WAMO-FW-LABOR -s 192.168.178.0/24", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_ungueltige_eintraege_werden_ausgelassen_und_gemeldet(self):
        r = self.regeln("300.1.1.1\n0.0.0.0/0\n10.0.0.0/4\n1.2.3.4/33\n1.2.3.4;reboot\n127.0.0.1\n172.20.0.5\n224.0.0.1\n010.0.0.1\n"
                        "169.254.1.1\nabc\n192.168.178.77\n")
        quellen = [z.split()[3] for z in self.kette(r.stdout, "WAMO-FW-LABOR") if z.endswith("-j RETURN")]
        self.assertEqual(quellen, ["127.0.0.0/8", "192.168.178.77"])
        self.assertIn("ungültig", r.stderr)
        self.assertNotIn("reboot", r.stdout)

    def test_nur_ungueltige_eintraege_heisst_nur_loopback(self):
        """Eine kaputte Datei öffnet nie das ganze Büro-LAN: lieber zu streng als zu offen."""
        r = self.regeln("0.0.0.0/0\n")
        quellen = [z.split()[3] for z in self.kette(r.stdout, "WAMO-FW-LABOR") if z.endswith("-j RETURN")]
        self.assertEqual(quellen, ["127.0.0.0/8"])
        self.assertIn("ungültig", r.stderr)

    def test_hoechstens_64_adressen(self):
        r = self.regeln("".join(f"192.168.178.{n}\n" for n in range(1, 101)))
        self.assertEqual(len([z for z in self.kette(r.stdout, "WAMO-FW-LABOR") if z.endswith("-j RETURN")]), 1 + 64)

    def test_der_ausgang_des_labor_dienstes_ist_gesperrt(self):
        out = self.regeln().stdout
        self.assertIn("-A WAMO-FW -s 172.28.1.0/29 -j WAMO-FW-LABAUS", out)  # das Netz wamo-labor-lokal (nur für die Veröffentlichung auf Loopback)
        kette = self.kette(out, "WAMO-FW-LABAUS")
        self.assertEqual(len(kette), 2)
        self.assertIn("-j LOG", kette[0])
        self.assertEqual(kette[1], "-A WAMO-FW-LABAUS -j DROP")
        for z in kette:
            self.assertNotIn("RETURN", z)
            self.assertNotIn("ACCEPT", z)

    def test_nur_eigene_ketten_und_kein_flush(self):
        out = self.regeln().stdout
        for z in out.splitlines():
            if z.startswith(":"):
                self.assertTrue(z.startswith(":WAMO-FW"), z)
            if z.startswith("-A "):
                self.assertTrue(z.split()[1].startswith("WAMO-FW"), z)
        text = code(FIREWALL)
        self.assertNotIn("iptables -F", text)
        self.assertEqual(len(re.findall(r"ipt -I INPUT", text)), 1)  # kein neuer Sprung in INPUT

    def test_start_stop_und_status_kennen_die_neuen_ketten(self):
        text = code(FIREWALL)
        self.assertEqual(text.count('"$KETTE_LABOR" "$KETTE_LABAUS"'), 3)  # stop (leeren, löschen) und status
        self.assertRegex(text, r"lies_labor_quellen\n\s+ipt -n -L DOCKER-USER")  # start liest die Adressliste vor dem Setzen
        self.assertRegex(text, r"regeln\) .*lies_labor_quellen")  # auch der Probelauf

    def test_kopf_nennt_die_adressliste_und_die_offene_frage(self):
        kopf = "\n".join(lies(FIREWALL).splitlines()[:45])
        for soll in ("55447", "Q13", "/etc/wamo-labor/plaetze-adressen", "WAMO-FW-LABOR", "WAMO-FW-LABAUS", "systemctl reload wamo-firewall"):
            self.assertIn(soll, kopf)

    def test_bash_n(self):
        r = subprocess.run([BASH, "-n", str(FIREWALL)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)


# ====================================================================================== Werkstatt für die Probeläufe der Skripte
LINUX = sys.platform.startswith("linux")
# Nur diese Befehle mit Wirkung gibt es im Probelauf, und zwar als Attrappe (sie schreiben jeden Aufruf in ein Protokoll)
ATTRAPPEN = ("docker", "iptables", "iptables-save", "systemctl", "curl", "ss", "getent", "journalctl", "sudo", "useradd", "userdel", "shred",
             "chown")
BASIS_IMAGE = "python:3.11-slim@sha256:" + "ab" * 32  # berechnet: kein Literal, das die Secret-Prüfung stört
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
# Attrappe für /usr/local/sbin/wamo-core-env: hält fest, was auf stdin ankommt (das einzige Mal, dass ein Token durch eine Pipe geht)
CORE_ENV = r"""#!/bin/sh
echo "wamo-core-env $*" >> "$ATTRAPPE_LOG"
cat > "$ATTRAPPE_DIR/wamo-core-env.stdin"
echo "Übernommen: WAMO_LABOR_TOKEN, WAMO_LABOR_URL (Werte nicht angezeigt)"
"""


def posix(p) -> str:
    """Pfad in der Schreibweise der Bash (unter Git für Windows: C:\\x wird /c/x)."""
    s = str(p).replace("\\", "/")
    if os.name == "nt" and re.match(r"^[A-Za-z]:/", s):
        s = "/" + s[0].lower() + s[2:]
    return s


def schreibe(pfad: Path, text: str, ausfuehrbar: bool = False) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with open(pfad, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if ausfuehrbar:
        pfad.chmod(0o755)


def system_pfad() -> str:
    if os.name != "nt":
        return "/usr/bin:/bin"
    b = Path(BASH)
    return str(b.parent if b.parent.parent.name == "usr" else b.parent.parent / "usr" / "bin")


def token_ok(wert: str) -> bool:
    """Form und Vielfalt wie die Konfiguration des Dienstes verlangt (43 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-, mindestens 16 verschiedene)."""
    return re.fullmatch(r"[A-Za-z0-9._~+/=-]{43,200}", wert) is not None and len(set(wert)) >= 16


class Werkstatt(unittest.TestCase):
    """Wegwerf-Ordner mit Wurzel, Attrappen und Protokoll; nichts außerhalb davon wird angefasst."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="labor-skripte-")
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.wurzel, self.bin, self.att = self.t / "wurzel", self.t / "bin", self.t / "att"
        self.caddy, self.quelle, self.log = self.t / "caddy", self.t / "quelle", self.t / "aufrufe.log"
        for d in (self.wurzel, self.bin, self.att):
            d.mkdir()
        schreibe(self.log, "")
        for n in ATTRAPPEN:
            schreibe(self.bin / n, ATTRAPPE, ausfuehrbar=True)
        schreibe(self.bin / "python3", f'#!/bin/sh\nexec "{posix(sys.executable)}" "$@"\n', ausfuehrbar=True)
        # der Stand vor dem Lauf: Caddy mit altem Compose und leerem kern-Ordner, die alte Firewall, die Quellen des Dienstes
        schreibe(self.caddy / "docker-compose.yml", "name: wamo-caddy\n# alter Stand\n")
        schreibe(self.caddy / "etc" / "Caddyfile", "import /etc/caddy/kern/*.caddy\n")
        (self.caddy / "etc" / "kern").mkdir()
        schreibe(self.p("usr/local/sbin/wamo-firewall"), "#!/bin/sh\necho alte Firewall\n", ausfuehrbar=True)
        schreibe(self.p("usr/local/sbin/wamo-core-env"), CORE_ENV, ausfuehrbar=True)
        schreibe(self.quelle / "Dockerfile", "ARG BASIS\nFROM ${BASIS}\n")
        (self.quelle / "wamo_labor_dienst").mkdir()
        # Standardantworten der Attrappen: wamo-caddy läuft, nichts anderes ist belegt, das Image fehlt noch, alles wird gesund
        self.ausgabe("docker.ps", "4711cafe\n")
        self.ausgabe("docker.image_inspect", "", rc=1)
        self.ausgabe("docker.inspect", "healthy\n")
        self.ausgabe("curl", "200")
        self.ausgabe("systemctl.show", "0\n")

    def p(self, *teile) -> Path:
        return self.wurzel.joinpath(*[t.lstrip("/") for t in teile])

    def ausgabe(self, schluessel: str, text: str = "", rc: int | None = None) -> None:
        schreibe(self.att / f"{schluessel}.out", text)
        rcdatei = self.att / f"{schluessel}.rc"
        if rc is None:
            rcdatei.unlink(missing_ok=True)
        else:
            schreibe(rcdatei, str(rc))

    def aufrufe(self) -> list[str]:
        return self.log.read_text(encoding="utf-8", errors="replace").splitlines()

    def lauf(self, skript: Path, *args: str, extra: dict | None = None, wurzel: bool = True) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({"PATH": f"{self.bin}{os.pathsep}{system_pfad()}", "ATTRAPPE_LOG": posix(self.log), "ATTRAPPE_DIR": posix(self.att),
                    "HOME": posix(self.t), "LC_ALL": "C.UTF-8", "WAMO_LABOR_CADDY_DIR": posix(self.caddy), "WAMO_LABOR_QUELLE": posix(self.quelle),
                    "WAMO_LABOR_CA": posix(self.t / "wamo-ca.crt"), "WAMO_LABOR_WARTE_RUNDEN": "2", "WAMO_LABOR_WARTE_SEKUNDEN": "0"})
        if wurzel:
            env["WAMO_LABOR_WURZEL"] = posix(self.wurzel)
        env.update(extra or {})
        return subprocess.run([BASH, str(skript), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                              timeout=120, stdin=subprocess.DEVNULL)

    def tokens(self) -> tuple[str, str]:
        return (self.p("etc/wamo-labor/kern-token").read_text(encoding="utf-8"), self.p("etc/wamo-labor/betreuer-token").read_text(encoding="utf-8"))


def funktionen(text: str) -> list[tuple[str, int]]:
    """Alle Funktionen auf oberster Ebene (Kopf `name() {` in Spalte 0, Ende `}` in Spalte 0) mit ihrer Länge in Zeilen."""
    ergebnis, name, start = [], None, 0
    for i, z in enumerate(text.splitlines(), 1):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{", z)
        if m and name is None:
            name, start = m.group(1), i
        elif z == "}" and name:
            ergebnis.append((name, i - start + 1))
            name = None
    return ergebnis


# ====================================================================================== Teil 3: wamo-labor-einrichten.sh
@unittest.skipUnless(BASH, "keine Bash vorhanden")
class EinrichtenTextTests(unittest.TestCase):
    def setUp(self):
        self.text = lies(EINRICHTEN)
        self.code = ohne_kommentare(self.text)

    def test_syntax_und_form(self):
        r = subprocess.run([BASH, "-n", str(EINRICHTEN)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.text.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("set -euo pipefail", self.text)
        self.assertLess(len(self.text.splitlines()), 800)
        for name, laenge in funktionen(self.text):
            self.assertLessEqual(laenge, 50, f"Funktion {name} hat {laenge} Zeilen")

    def test_nie_ein_kern_neustart_und_kein_eval(self):
        self.assertIsNone(re.search(r"\beval\b", self.code))
        self.assertNotRegex(self.code, r"systemctl\s+(restart|reload|stop|kill)\s+wamo-core")
        self.assertNotRegex(self.code, r"(?m)^\s*(tu\s+)?systemctl restart")
        self.assertIn("nächsten Kern-Release", self.text)

    def test_tokens_nie_ausgeben_und_nie_als_argument(self):
        self.assertIsNone(re.search(r"(echo|printf)[^\n]*\$\{?(KERN_TOKEN|BETREUER_TOKEN|TOKEN)\b", self.code))
        self.assertNotRegex(self.code, r"\$\(\s*cat\s+[^)]*token")  # nie in eine Variable lesen
        self.assertNotRegex(self.code, r"\btoken\w*=\"?\$\(")
        # der Wert entsteht in einer Pipe aus /dev/urandom und geht nur in die Datei, die Kern-Einstellung bekommt ihn nur über stdin
        self.assertIn("head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\\n'", self.code)
        self.assertIn("tr -d '\\r\\n' < \"$KERN_TOKEN_DATEI\"", self.code)
        self.assertIn('"$CORE_ENV" setzen', self.code)
        self.assertNotRegex(self.code, r"docker[^\n]*(KERN_TOKEN|BETREUER_TOKEN)")  # nie in einem docker-Aufruf
        self.assertNotIn("set -x", self.code)
        self.assertNotIn("xtrace", self.code)

    def test_kein_befehl_zum_ausgeben_des_betreuer_tokens(self):
        """Der Betreuer-Token geht später über ein PC-Skript per ssh in eine lokale Datei (Folgeschritt, nur im Kopf genannt)."""
        kopf = "\n".join(z for z in self.text.splitlines() if z.startswith("#"))
        self.assertIn("Folgeschritt", kopf)
        self.assertIn("betreuer-token-ausgeben", kopf)
        self.assertNotIn("betreuer-token-ausgeben", self.code)
        m = re.search(r"case \"\$1\" in\n(.*?)\n\s+esac", self.code, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn("ausgeben", m.group(1))

    def test_namen_nach_dem_namensvertrag(self):
        for soll in ("wamo-labor-dienst", "wamo-labor-netz", "wamo-labor-lokal", "/etc/wamo-labor", "/var/lib/wamo-labor", "/opt/wamo-labor",
                     "WAMO_LABOR_URL", "WAMO_LABOR_TOKEN", "http://127.0.0.1:18765", "10001", "55447", "wamo-labor.caddy", "wamo_labor_rauchtest.py",
                     "plaetze-adressen", "betreuer-adressen", "basis-image", "/usr/local/sbin/wamo-core-env", "172.28.0.0/24", "172.28.1.0/29"):
            self.assertIn(soll, self.code, soll)

    def test_interne_adressen_wie_in_der_caddy_site(self):
        m = re.search(r'^INTERN="([^"]+)"', self.code, re.M)
        self.assertIsNotNone(m)
        snippet = block(code(SITE), "(labor_intern_adressen)")
        self.assertEqual(snippet.strip(), f"remote_ip {m.group(1)}")

    def test_firewall_und_caddy_werden_ueber_die_vorhandenen_skripte_gesetzt(self):
        self.assertIn("wamo-firewall-einrichten.sh", self.code)
        self.assertIn("caddy adapt", self.code)
        self.assertIn("--cap-drop ALL", self.code)
        self.assertIn("--network none", self.code)
        self.assertIn("kern_ruhig", self.code)  # Caddy wird nur ohne laufende Sitzung neu angelegt
        self.assertIn("up -d --no-build", self.code)


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class EinrichtenProbelaufTests(Werkstatt):
    def setUp(self):
        super().setUp()
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "# Betreuer\n192.168.178.50\n192.168.178.60/32  # zweiter\n")

    def einrichten(self, *args, extra=None, basis=True):
        return self.lauf(EINRICHTEN, "einrichten", *(["--basis-image", BASIS_IMAGE] if basis else []), *args, extra=extra)

    def test_einrichten_legt_alles_an(self):
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for schritt in ("== Vorprüfung", "== Sicherung", "== Ordner und Token", "== Image", "== Firewall", "== Caddy", "== Start", "== Kern", "Fertig."):
            self.assertIn(schritt, r.stdout)
        kern, betreuer = self.tokens()
        self.assertTrue(token_ok(kern))
        self.assertTrue(token_ok(betreuer))
        self.assertNotEqual(kern, betreuer)
        self.assertTrue(self.p("var/lib/wamo-labor").is_dir())
        self.assertEqual(self.p("etc/wamo-labor/basis-image").read_text(encoding="utf-8").strip(), BASIS_IMAGE)
        self.assertTrue(self.p("etc/wamo-labor/plaetze-adressen").is_file())
        self.assertEqual(self.p("opt/wamo-labor/docker-compose.yml").read_bytes(), (LABOR / "docker-compose.yml").read_bytes())

    def test_kein_token_in_ausgabe_oder_protokoll(self):
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for wert in self.tokens():
            for ort in (r.stdout, r.stderr, "\n".join(self.aufrufe())):
                self.assertNotIn(wert, ort)
        self.assertIsNone(re.search(r"[A-Za-z0-9_-]{43}", r.stdout + r.stderr))

    def test_image_wird_mit_digest_gebaut_und_gestartet(self):
        self.assertEqual(self.einrichten().returncode, 0)
        aufrufe = self.aufrufe()
        self.assertIn(f"docker pull {BASIS_IMAGE}", aufrufe)
        bau = [z for z in aufrufe if z.startswith("docker build")]
        self.assertEqual(len(bau), 1)
        self.assertEqual(bau[0], f"docker build --pull=false --build-arg BASIS={BASIS_IMAGE} --tag wamo-labor-dienst:1 {posix(self.quelle)}")
        compose = f"docker compose -f {posix(self.p('opt/wamo-labor/docker-compose.yml'))} up -d --no-build"
        self.assertIn(compose, aufrufe)
        self.assertLess(aufrufe.index(bau[0]), aufrufe.index(compose))
        # nichts, was Daten oder den Kern berührt
        for z in aufrufe:
            self.assertNotRegex(z, r"^(docker (rm|volume|system|network rm)|systemctl (restart|stop|disable)|userdel|useradd|shred)\b", z)

    def test_caddy_site_aus_der_vorlage_mit_den_adressen(self):
        self.assertEqual(self.einrichten().returncode, 0)
        site = (self.caddy / "etc" / "kern" / "wamo-labor.caddy").read_text(encoding="utf-8")
        self.assertNotIn("__BETREUER_ADRESSEN__", site)
        self.assertIn("remote_ip 127.0.0.0/8 172.24.0.1/32 192.168.178.136/32 192.168.178.50 192.168.178.60/32", site)
        self.assertEqual(site.replace("127.0.0.0/8 172.24.0.1/32 192.168.178.136/32 192.168.178.50 192.168.178.60/32", "__BETREUER_ADRESSEN__"),
                         lies(SITE))
        # die neue Compose von wamo-caddy kommt dazu (Port 55447, Netz), vor dem Anlegen wird die Konfiguration geprüft
        self.assertEqual((self.caddy / "docker-compose.yml").read_bytes(), (CADDY / "docker-compose.yml").read_bytes())
        aufrufe = self.aufrufe()
        adapt = [z for z in aufrufe if z.startswith("docker run") and "caddy adapt" in z]
        self.assertEqual(len(adapt), 1)
        for soll in ("--rm", "--network none", "--cap-drop ALL", "--read-only", "no-new-privileges:true"):
            self.assertIn(soll, adapt[0])
        self.assertIn("docker compose up -d", aufrufe)  # Compose von wamo-caddy hat sich geändert: einmal neu anlegen

    def test_gleiche_caddy_compose_heisst_nur_neu_laden(self):
        schreibe(self.caddy / "docker-compose.yml", (CADDY / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(self.einrichten().returncode, 0)
        aufrufe = self.aufrufe()
        self.assertIn("docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile", aufrufe)
        self.assertNotIn("docker compose up -d", aufrufe)

    def test_sicherung_vor_jeder_aenderung(self):
        self.assertEqual(self.einrichten().returncode, 0)
        ordner = list(self.p("var/backups/wamo-labor").iterdir())
        self.assertEqual(len(ordner), 1)
        self.assertRegex(ordner[0].name, r"^\d{8}-\d{6}")
        self.assertEqual((ordner[0] / "wamo-caddy-compose.vorher").read_text(encoding="utf-8"), "name: wamo-caddy\n# alter Stand\n")
        self.assertIn("alte Firewall", (ordner[0] / "wamo-firewall.vorher").read_text(encoding="utf-8"))
        for datei in ordner[0].iterdir():  # keine Token, keine Daten in der Sicherung
            self.assertNotIn("token", datei.name)
        r = self.einrichten()
        self.assertEqual(len(list(self.p("var/backups/wamo-labor").iterdir())), 2)

    def test_kern_bekommt_url_und_token_nur_ueber_stdin(self):
        r = self.einrichten()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        kern, _ = self.tokens()
        stdin = (self.att / "wamo-core-env.stdin").read_text(encoding="utf-8")
        self.assertEqual(stdin, f"WAMO_LABOR_URL=http://127.0.0.1:18765\nWAMO_LABOR_TOKEN={kern}\n")
        self.assertIn("wamo-core-env setzen", self.aufrufe())  # ohne ein weiteres Argument: nichts steht in der Prozessliste
        self.assertIn("nächsten Kern-Release", r.stdout)
        self.assertIn("umschalten-kern", r.stdout)
        self.assertNotIn("wamo-core neu", "\n".join(self.aufrufe()))
        self.assertFalse([z for z in self.aufrufe() if z.startswith("systemctl") and "wamo-core" in z and not z.startswith("systemctl show ")])  # nur lesen
        # der Kern bekommt den Dienst erst nach dem Start des Dienstes (die Reihenfolge der Schritte)
        self.assertLess(r.stdout.index("== Start"), r.stdout.index("== Kern"))

    def test_zweiter_lauf_aendert_die_token_nicht(self):
        self.assertEqual(self.einrichten().returncode, 0)
        vorher = self.tokens()
        r = self.einrichten(basis=False)  # das Basis-Image steht jetzt in /etc/wamo-labor/basis-image
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.tokens(), vorher)
        self.assertIn("vorhanden", r.stdout)
        self.assertFalse(any(f.name.startswith(".token") for f in self.p("etc/wamo-labor").iterdir()))  # keine Reste

    def test_trockenlauf_aendert_nichts(self):
        r = self.einrichten("--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        for erwartet in ("docker build", "wamo-firewall-einrichten.sh", "caddy adapt", "docker compose -f", "wamo-core-env setzen", "wamo_labor_rauchtest.py"):
            self.assertIn(erwartet, r.stdout)
        self.assertFalse(self.p("etc/wamo-labor/kern-token").exists())
        self.assertFalse(self.p("var").exists())
        self.assertFalse(self.p("opt").exists())
        self.assertEqual(list((self.caddy / "etc" / "kern").iterdir()), [])
        self.assertFalse((self.att / "wamo-core-env.stdin").exists())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(docker (pull|run|rm|build|exec|network (create|rm))|docker compose (-f|up|down|build)|systemctl (restart|stop|start)|wamo-core-env setzen)", z)

    def test_vorbedingungen(self):
        self.ausgabe("docker.ps", "", rc=0)  # wamo-caddy läuft nicht
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wamo-caddy", r.stderr)
        self.assertFalse(self.p("etc/wamo-labor/kern-token").exists())

    def test_ohne_basis_image_oder_mit_falscher_angabe(self):
        r = self.einrichten(basis=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--basis-image", r.stderr)
        for falsch in ("python:3.11-slim", "python:latest", "python:3.11-slim@sha256:abc", "a;b@sha256:" + "ab" * 32, "$(x)@sha256:" + "ab" * 32):
            r = self.lauf(EINRICHTEN, "einrichten", "--basis-image", falsch)
            self.assertNotEqual(r.returncode, 0, falsch)
        self.assertFalse(self.p("etc/wamo-labor/kern-token").exists())

    def test_fremder_benutzer_mit_der_kennung_10001_bricht_ab(self):
        self.ausgabe("getent", "fremd:x:10001:10001::/home/fremd:/bin/bash\n")
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("10001", r.stderr)

    def test_quelle_fehlt(self):
        (self.quelle / "Dockerfile").unlink()
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Dockerfile", r.stderr)

    def test_betreuer_adressen_werden_streng_geprueft(self):
        for falsch in ("0.0.0.0/0", "192.168.0.0/16", "192.168.178.500", "1.2.3.4;reboot", "abc", "127.0.0.1", "10.0.0.1/33"):
            schreibe(self.p("etc/wamo-labor/betreuer-adressen"), f"192.168.178.50\n{falsch}\n")
            r = self.einrichten()
            self.assertNotEqual(r.returncode, 0, falsch)
            self.assertIn("Ungültige Adresse", r.stderr)
            self.assertEqual(list((self.caddy / "etc" / "kern").iterdir()), [], falsch)  # nichts installiert
            self.assertNotIn("reboot", r.stdout)

    def test_ohne_adressdatei_gilt_nur_der_server_selbst(self):
        self.p("etc/wamo-labor/betreuer-adressen").unlink()
        self.assertEqual(self.einrichten().returncode, 0)
        site = (self.caddy / "etc" / "kern" / "wamo-labor.caddy").read_text(encoding="utf-8")
        self.assertIn("remote_ip 127.0.0.0/8 172.24.0.1/32 192.168.178.136/32\n", site)
        vorlage = self.p("etc/wamo-labor/betreuer-adressen").read_text(encoding="utf-8")  # eine Vorlage mit Kommentaren entsteht
        self.assertTrue(all(z.startswith("#") or not z.strip() for z in vorlage.splitlines()))

    def test_fehler_in_caddy_nimmt_alles_zurueck(self):
        self.ausgabe("docker.compose_up", "", rc=1)  # docker compose up -d in wamo-caddy schlägt fehl (nicht compose version und nicht -f ...)
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("zurückgenommen", r.stderr)
        self.assertEqual(list((self.caddy / "etc" / "kern").iterdir()), [])  # Site wieder weg
        self.assertEqual((self.caddy / "docker-compose.yml").read_text(encoding="utf-8"), "name: wamo-caddy\n# alter Stand\n")
        self.assertFalse((self.att / "wamo-core-env.stdin").exists())  # der Kern bekommt nichts
        self.assertNotIn("== Kern", r.stdout)

    def test_dienst_wird_nicht_gesund_nimmt_den_start_zurueck(self):
        self.ausgabe("docker.inspect", "starting\n")
        r = self.einrichten()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nicht gesund", r.stderr)
        down = [z for z in self.aufrufe() if " down" in z and "wamo-labor" in z]
        self.assertTrue(down, self.aufrufe())
        self.assertFalse((self.att / "wamo-core-env.stdin").exists())

    def test_status_zeigt_nur_namen_und_zustaende(self):
        self.assertEqual(self.einrichten().returncode, 0)
        self.ausgabe("docker.ps", "wamo-labor-dienst Up 2 minutes (healthy)\n")
        r = self.lauf(EINRICHTEN, "status")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("== Status", r.stdout)
        self.assertIn("kern-token", r.stdout)
        self.assertIn("betreuer-token", r.stdout)
        for wert in self.tokens():
            self.assertNotIn(wert, r.stdout + r.stderr)
        self.assertIsNone(re.search(r"[A-Za-z0-9_-]{43}", r.stdout))

    def test_falsche_aufrufe(self):
        for args in (["--unbekannt"], ["betreuer-token-ausgeben"], ["einrichten", "--basis-image"], ["einrichten", "--quelle"], ["x"]):
            self.assertEqual(self.lauf(EINRICHTEN, *args).returncode, 2, args)

    def test_ohne_probelauf_nur_als_root(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("läuft als root")
        r = self.lauf(EINRICHTEN, "einrichten", "--basis-image", BASIS_IMAGE, wurzel=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("sudo", r.stderr)
        self.assertEqual(self.aufrufe(), [])


# ====================================================================================== Teil 4: Rückweg und Löschlauf
WORT = "LABOR-LOESCHEN"
FIREWALL_ALT = "#!/bin/sh\necho Firewall vor dem Labor-Dienst\n"
FIREWALL_NEU = "#!/bin/sh\necho Firewall mit WAMO-FW-LABOR\n"
INHALT = "GEHEIMTEXT-eines-Besuchers"


class BetriebsstandWerkstatt(Werkstatt):
    """Ein eingerichteter Betrieb in der Wurzel: Compose, Site, Token, Daten, Sicherungen und die neue Firewall."""

    def setUp(self):
        super().setUp()
        schreibe(self.p("opt/wamo-labor/docker-compose.yml"), "name: wamo-labor\n")
        schreibe(self.caddy / "etc" / "kern" / "wamo-labor.caddy", "# Site\n")
        schreibe(self.p("etc/wamo-labor/kern-token"), "k" * 20 + "AbCdEfGhIjKlMnOpQrStUv123")
        schreibe(self.p("etc/wamo-labor/betreuer-token"), "b" * 20 + "ZyXwVuTsRqPoNmLkJiHgFe987")
        schreibe(self.p("etc/wamo-labor/betreuer-adressen"), "# Adressen\n192.168.178.50\n")
        schreibe(self.p("etc/wamo-labor/basis-image"), BASIS_IMAGE + "\n")
        schreibe(self.p("var/lib/wamo-labor/platz-03.sqlite3"), INHALT)
        schreibe(self.p("var/lib/wamo-labor/zentral.sqlite3"), INHALT)
        schreibe(self.p("var/lib/wamo-labor/protokoll/dienst.jsonl"), INHALT + "\n")
        schreibe(self.p("usr/local/sbin/wamo-firewall"), FIREWALL_NEU, ausfuehrbar=True)
        schreibe(self.p("var/backups/wamo-labor/20261112-100000/wamo-firewall.vorher"), FIREWALL_ALT)
        schreibe(self.p("var/backups/wamo-labor/20261112-110000/wamo-firewall.vorher"), FIREWALL_NEU)  # zweiter Lauf: schon neu
        self.ausgabe("docker.images", f"wamo-labor-dienst:1\nwamo-labor-dienst:alt\nfremd:1\n")

    def lauf_skript(self, skript, *args, heute: str | None = None):
        return self.lauf(skript, *args, extra={"WAMO_LABOR_HEUTE": heute} if heute else None)

    def daten_da(self) -> bool:
        return self.p("var/lib/wamo-labor").exists()


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class RueckwegTests(BetriebsstandWerkstatt):
    def test_entfernt_dienst_und_site_und_laesst_daten_und_token(self):
        r = self.lauf_skript(RUECKWEG)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aufrufe = self.aufrufe()
        compose = posix(self.p("opt/wamo-labor/docker-compose.yml"))
        self.assertIn(f"docker compose -f {compose} down --remove-orphans", aufrufe)
        self.assertIn("docker rm -f wamo-labor-dienst", aufrufe)
        self.assertIn("docker network rm wamo-labor-lokal", aufrufe)  # wamo-labor-netz gehört zu wamo-caddy und bleibt
        self.assertNotIn("docker network rm wamo-labor-netz", aufrufe)
        self.assertIn("docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile", aufrufe)
        self.assertFalse((self.caddy / "etc" / "kern" / "wamo-labor.caddy").exists())
        # Daten und Token bleiben
        self.assertTrue(self.daten_da())
        self.assertEqual(self.p("var/lib/wamo-labor/platz-03.sqlite3").read_text(encoding="utf-8"), INHALT)
        self.assertTrue(self.p("etc/wamo-labor/kern-token").exists())
        self.assertTrue(self.p("etc/wamo-labor/betreuer-token").exists())
        for z in aufrufe:
            self.assertNotRegex(z, r"^(shred|docker image rm|wamo-core-env|systemctl (restart|stop) )", z)

    def test_firewall_zurueck_auf_die_aelteste_fassung_ohne_labor_kette(self):
        r = self.lauf_skript(RUECKWEG)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.p("usr/local/sbin/wamo-firewall").read_text(encoding="utf-8"), FIREWALL_ALT)
        aufrufe = self.aufrufe()
        self.assertIn("systemctl reload wamo-firewall.service", aufrufe)
        self.assertIn("iptables -w 5 -F WAMO-FW-LABOR", aufrufe)
        self.assertIn("iptables -w 5 -X WAMO-FW-LABOR", aufrufe)
        self.assertIn("iptables -w 5 -X WAMO-FW-LABAUS", aufrufe)
        self.assertLess(aufrufe.index("systemctl reload wamo-firewall.service"), aufrufe.index("iptables -w 5 -F WAMO-FW-LABOR"))

    def test_ohne_alte_firewall_in_den_sicherungen_bleibt_sie_und_es_gibt_einen_hinweis(self):
        self.p("var/backups/wamo-labor/20261112-100000/wamo-firewall.vorher").unlink()
        r = self.lauf_skript(RUECKWEG)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.p("usr/local/sbin/wamo-firewall").read_text(encoding="utf-8"), FIREWALL_NEU)
        self.assertIn("wamo-firewall.sh", r.stdout)
        self.assertNotIn("systemctl reload wamo-firewall.service", self.aufrufe())

    def test_trockenlauf_aendert_nichts(self):
        r = self.lauf_skript(RUECKWEG, "--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        self.assertTrue((self.caddy / "etc" / "kern" / "wamo-labor.caddy").exists())
        self.assertEqual(self.p("usr/local/sbin/wamo-firewall").read_text(encoding="utf-8"), FIREWALL_NEU)
        self.assertTrue(self.daten_da())
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(docker (rm|network rm|exec|compose -f \S+ down)|systemctl reload|iptables -w 5 -[FX])", z)

    def test_daten_loeschen_braucht_das_bestaetigungswort(self):
        r = self.lauf_skript(RUECKWEG, "--daten-loeschen")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(self.daten_da())
        self.assertTrue(self.p("etc/wamo-labor/kern-token").exists())
        r = self.lauf_skript(RUECKWEG, "--daten-loeschen", "--bestaetigung", "falsch")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(self.daten_da())

    def test_daten_loeschen_mit_bestaetigung_ruft_den_loeschlauf(self):
        r = self.lauf_skript(RUECKWEG, "--daten-loeschen", "--bestaetigung", WORT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.daten_da())
        self.assertFalse(self.p("etc/wamo-labor/kern-token").exists())
        self.assertFalse((self.caddy / "etc" / "kern" / "wamo-labor.caddy").exists())
        self.assertIn("Nachweis", r.stdout)

    def test_falsche_aufrufe(self):
        for args in (["--unbekannt"], ["--bestaetigung"], ["x"]):
            self.assertEqual(self.lauf_skript(RUECKWEG, *args).returncode, 2, args)
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            self.assertEqual(self.lauf(RUECKWEG, wurzel=False).returncode, 1)  # ohne Wurzel und ohne root
            self.assertEqual(self.aufrufe(), [])


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class LoeschlaufTests(BetriebsstandWerkstatt):
    NACH_FRIST = "20261212"

    def loeschen(self, *args, heute=NACH_FRIST):
        return self.lauf_skript(LOESCHLAUF, *args, heute=heute)

    def test_ohne_bestaetigung_nur_der_plan(self):
        r = self.loeschen()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertTrue(self.daten_da())
        self.assertIn(WORT, r.stdout)
        for name in ("platz-03.sqlite3", "zentral.sqlite3", "dienst.jsonl", "kern-token", "betreuer-token"):
            self.assertIn(name, r.stdout)
        self.assertNotIn(INHALT, r.stdout + r.stderr)  # Namen und Größen, nie Inhalte
        self.assertFalse([z for z in self.aufrufe() if z.startswith(("docker rm", "docker image rm", "wamo-core-env", "shred"))])

    def test_falsches_wort(self):
        r = self.loeschen("--bestaetigung", "loeschen")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(self.daten_da())

    def test_vor_dem_ende_der_aufbewahrung_nur_mit_ausdruecklichem_schalter(self):
        r = self.loeschen("--bestaetigung", WORT, heute="20261211")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("11.12.2026", r.stdout + r.stderr)
        self.assertTrue(self.daten_da())
        r = self.loeschen("--bestaetigung", WORT, "--vor-der-frist", heute="20261211")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.daten_da())

    def test_loescht_daten_token_image_und_kern_einstellung(self):
        r = self.loeschen("--bestaetigung", WORT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.daten_da())
        self.assertFalse(self.p("etc/wamo-labor/kern-token").exists())
        self.assertFalse(self.p("etc/wamo-labor/betreuer-token").exists())
        # Konfiguration ohne personenbezogene Daten bleibt
        for bleibt in ("betreuer-adressen", "basis-image"):
            self.assertTrue(self.p("etc/wamo-labor", bleibt).exists(), bleibt)
        aufrufe = self.aufrufe()
        self.assertIn("docker compose -f " + posix(self.p("opt/wamo-labor/docker-compose.yml")) + " down --remove-orphans", aufrufe)
        self.assertIn("docker rm -f wamo-labor-dienst", aufrufe)
        self.assertIn("docker image rm wamo-labor-dienst:1", aufrufe)
        self.assertIn("docker image rm wamo-labor-dienst:alt", aufrufe)
        self.assertNotIn("docker image rm fremd:1", aufrufe)  # nur die eigenen Tags
        self.assertIn("wamo-core-env loeschen WAMO_LABOR_URL WAMO_LABOR_TOKEN", aufrufe)
        shred = [i for i, z in enumerate(aufrufe) if z.startswith("shred")]
        self.assertTrue(shred)  # Dateien werden vor dem Löschen überschrieben (wo das Werkzeug da ist)
        self.assertLess(aufrufe.index("docker rm -f wamo-labor-dienst"), shred[0])  # erst den Dienst stoppen, dann löschen

    def test_nachweis_mit_liste_ohne_inhalte(self):
        r = self.loeschen("--bestaetigung", WORT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        nachweise = list(self.p("var/log/wamo-labor").glob("loeschnachweis-*.txt"))
        self.assertEqual(len(nachweise), 1)
        text = nachweise[0].read_text(encoding="utf-8")
        for t in (text, r.stdout):
            for name in ("platz-03.sqlite3", "zentral.sqlite3", "protokoll/dienst.jsonl", "kern-token", "betreuer-token", "wamo-labor-dienst:1",
                         "WAMO_LABOR_TOKEN", "bleibt"):
                self.assertIn(name, t)
            self.assertNotIn(INHALT, t)
            self.assertIsNone(re.search(r"[A-Za-z0-9_-]{40}", t.replace("/", " ")), "Token-ähnliche Zeichenfolge im Nachweis")
        for soll in ("Größe", "Snapshots"):
            self.assertIn(soll, text)  # der Hinweis auf Sicherungen und Snapshots außerhalb des Dienstes (D10)

    def test_zweiter_lauf_hat_nichts_mehr_zu_loeschen(self):
        self.assertEqual(self.loeschen("--bestaetigung", WORT).returncode, 0)
        self.ausgabe("docker.images", "")  # das Image ist jetzt weg
        r = self.loeschen("--bestaetigung", WORT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nichts zu löschen", r.stdout)

    def test_trockenlauf_loescht_nichts(self):
        r = self.loeschen("--bestaetigung", WORT, "--trocken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[trocken]", r.stdout)
        self.assertTrue(self.daten_da())
        self.assertTrue(self.p("etc/wamo-labor/kern-token").exists())
        self.assertFalse(list(self.p("var/log/wamo-labor").glob("loeschnachweis-*.txt")) if self.p("var/log/wamo-labor").exists() else [])
        for z in self.aufrufe():
            self.assertNotRegex(z, r"^(shred|docker (rm|image rm|compose -f \S+ down|network rm)|wamo-core-env)", z)

    @unittest.skipIf(os.name == "nt", "Symbolische Links brauchen unter Windows besondere Rechte")
    def test_ein_symbolischer_link_als_datenordner_wird_abgelehnt(self):
        ziel = self.t / "fremd"
        ziel.mkdir()
        schreibe(ziel / "wichtig.txt", "bleibt")
        shutil.rmtree(self.p("var/lib/wamo-labor"))
        self.p("var/lib/wamo-labor").symlink_to(ziel)
        r = self.loeschen("--bestaetigung", WORT)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((ziel / "wichtig.txt").exists())

    def test_falsche_aufrufe(self):
        for args in (["--unbekannt"], ["--bestaetigung"], ["x"]):
            self.assertEqual(self.loeschen(*args).returncode, 2, args)
        if not (hasattr(os, "geteuid") and os.geteuid() == 0):
            self.assertEqual(self.lauf(LOESCHLAUF, "--bestaetigung", WORT, wurzel=False).returncode, 1)  # ohne Wurzel und ohne root


@unittest.skipUnless(BASH, "keine Bash vorhanden")
class RueckwegLoeschlaufTextTests(unittest.TestCase):
    def test_form_und_grenzen(self):
        for skript in (RUECKWEG, LOESCHLAUF):
            text = lies(skript)
            r = subprocess.run([BASH, "-n", str(skript)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(text.startswith("#!/usr/bin/env bash\n"), skript.name)
            self.assertIn("set -euo pipefail", text)
            self.assertLess(len(text.splitlines()), 800)
            for name, laenge in funktionen(text):
                self.assertLessEqual(laenge, 50, f"{skript.name}: {name} hat {laenge} Zeilen")
            c = ohne_kommentare(text)
            self.assertIsNone(re.search(r"\beval\b", c), skript.name)
            self.assertIsNone(re.search(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|\$\w+\s)", c), skript.name)
            self.assertNotRegex(c, r"systemctl\s+(restart|stop|kill)\s+wamo-core")

    def test_loeschlauf_loescht_nur_feste_pfade(self):
        c = code(LOESCHLAUF)
        self.assertIn(WORT, c)
        self.assertIn('DATEN="$W/var/lib/wamo-labor"', c)
        self.assertIn("pwd -P", c)  # Pfadkontrolle vor dem Löschen: physisch dort, wo der Pfad steht
        self.assertIn('[ ! -L "$pfad" ]', c)  # kein symbolischer Link
        self.assertNotRegex(c, r"rm\s+-[a-z]*r[a-z]*\s+\"?\$(DATEN|ETC)\b")  # kein rekursives Löschen eines Ordners per Variable
        self.assertIn("docker image rm", c)
        self.assertIn("wamo-core-env", c)
        self.assertIn("loeschen WAMO_LABOR_URL WAMO_LABOR_TOKEN", c)


# ====================================================================================== Teil 5: Rauchtest mit Attrappen
def rauchtest_laden():
    spez = importlib.util.spec_from_file_location("wamo_labor_rauchtest_t", RAUCHTEST)
    m = importlib.util.module_from_spec(spez)
    sys.modules["wamo_labor_rauchtest_t"] = m  # für dataclasses
    spez.loader.exec_module(m)
    return m


class RauchtestTests(unittest.TestCase):
    """Der Rauchtest läuft am Server. Hier läuft er gegen eine Attrappe der Welt: einmal in gutem Zustand (alles grün), dann je mit einem
    eingebauten Fehler (genau die zuständige Probe wird rot, die übrigen bleiben grün)."""

    # Fehler der Attrappe und die Probe, die ihn finden muss
    FEHLER = {
        "ungesund": "P01", "root": "P02", "schreibbar": "P03", "caps": "P04", "privilegiert": "P05", "ohne_nnp": "P05",
        "ohne_grenzen": "P06", "ohne_loggrenze": "P06", "netz_extern": "P07", "falscher_proxy": "P07", "port_offen": "P08", "port_zusatz": "P08",
        "token_modus": "P09", "token_besitzer": "P09", "daten_modus": "P09", "token_gleich": "P09", "token_kurz": "P09",
        "ca_falsch": "P10", "koepfe_fehlen": "P10", "kern_offen": "P11", "kern_variante_offen": "P11", "betreuer_offen": "P12",
        "betreuer_gegenprobe": "P12", "gesundheit_offen": "P13", "ausgang_internet": "P14", "ausgang_dns": "P14", "ausgang_kontrolle": "P14",
        "firewall_fehlt": "P15", "firewall_offen": "P15", "roundtrip_kaputt": "P16", "roundtrip_ohne_token": "P16", "env_url": "P17", "env_token": "P17",
        "token_in_env": "P18", "ende_falsch": "P18",
    }

    def setUp(self):
        self.m = rauchtest_laden()

    def lauf(self, welt):
        return self.m.alle_proben(welt)

    def ergebnisse(self, welt) -> dict:
        return {r.nr: r for r in self.lauf(welt)}

    def test_gute_attrappe_alles_gruen_und_jede_probe_sagt_was_sie_belegt(self):
        ergebnisse = self.lauf(self.m.Attrappe())
        self.assertEqual([r.nr for r in ergebnisse], [f"P{n:02d}" for n in range(1, 19)])
        for r in ergebnisse:
            self.assertEqual(r.ergebnis, "OK", f"{r.nr} {r.name}: {r.befunde}")
            self.assertGreater(len(r.belegt), 20, r.nr)
            self.assertGreater(len(r.nicht), 20, r.nr)  # und was sie NICHT belegt
            self.assertTrue(r.pruefungen, r.nr)

    def test_jeder_eingebaute_fehler_wird_von_genau_der_zustaendigen_probe_gefunden(self):
        for fehler, nr in self.FEHLER.items():
            with self.subTest(fehler=fehler):
                ergebnisse = self.ergebnisse(self.m.Attrappe(fehler={fehler}))
                self.assertEqual(ergebnisse[nr].ergebnis, "FEHLER", f"{fehler} wurde von {nr} nicht gefunden")
                self.assertTrue(ergebnisse[nr].befunde)
                for andere, r in ergebnisse.items():
                    if andere != nr:
                        self.assertEqual(r.ergebnis, "OK", f"{fehler} hat auch {andere} rot gemacht: {r.befunde}")

    def test_die_fehlerliste_deckt_alle_proben_ab(self):
        self.assertEqual(sorted(set(self.FEHLER.values())), [f"P{n:02d}" for n in range(1, 19)])
        self.assertEqual(set(self.FEHLER), set(self.m.Attrappe.BEKANNTE_FEHLER))

    def test_tabelle_und_ausgang(self):
        import contextlib
        import io

        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            rc = self.m.main(["--attrappe"])
        text = puffer.getvalue()
        self.assertEqual(rc, 0)
        for soll in ("Nr", "Probe", "Ergebnis", "belegt", "belegt nicht", "P01", "P18", "18 OK, 0 FEHLER"):
            self.assertIn(soll, text)
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            rc = self.m.main(["--attrappe", "--fehler", "kern_offen"])
        self.assertEqual(rc, 1)
        self.assertIn("FEHLER", puffer.getvalue())
        self.assertIn("kern_offen", puffer.getvalue())

    def test_tokens_stehen_nie_in_der_ausgabe(self):
        import contextlib
        import io

        welt = self.m.Attrappe()
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer), contextlib.redirect_stderr(puffer):
            self.m.ausgeben(self.lauf(welt))
        for wert in (welt.kern, welt.betreuer):
            self.assertTrue(token_ok(wert))
            self.assertNotIn(wert, puffer.getvalue())
        # auch nicht in den Befunden eines Fehlers (token_in_env legt den Wert in die Umgebung des Containers)
        welt = self.m.Attrappe(fehler={"token_in_env", "env_token"})
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer), contextlib.redirect_stderr(puffer):
            self.m.ausgeben(self.lauf(welt))
        self.assertNotIn(welt.kern, puffer.getvalue())
        self.assertNotIn(welt.betreuer, puffer.getvalue())

    def test_ausnahme_in_einer_probe_ist_ein_fehler_und_stoppt_die_anderen_nicht(self):
        welt = self.m.Attrappe()
        welt.inspect = lambda name: (_ for _ in ()).throw(RuntimeError("kaputt " + welt.kern))
        ergebnisse = self.ergebnisse(welt)
        self.assertEqual(ergebnisse["P01"].ergebnis, "FEHLER")
        self.assertNotIn(welt.kern, "\n".join(ergebnisse["P01"].befunde))  # nur der Typ der Ausnahme, nie ihr Text
        self.assertEqual(ergebnisse["P11"].ergebnis, "OK")

    def test_token_wird_nur_gelesen_nie_gesendet_ausser_an_loopback(self):
        welt = self.m.Attrappe()
        self.lauf(welt)
        gesendet = [(a["ziel"], a["pfad"]) for a in welt.anfragen if a["bearer"]]
        self.assertTrue(gesendet)
        for ziel, pfad in gesendet:
            self.assertEqual(ziel, "loopback", pfad)  # der Bearer geht nie über Caddy oder in einen fremden Container
            self.assertTrue(pfad.startswith("/api/kern/"), pfad)
        for a in welt.anfragen:
            self.assertEqual(a["methode"] if a["bearer"] else "GET", "GET")  # nur lesend (V1)

    def test_hilfen_antwort_und_env_datei(self):
        roh = b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\nX-Frame-Options: DENY\r\n\r\nnicht erlaubt"
        a = self.m.antwort_lesen(roh)
        self.assertEqual((a.status, a.kopf["content-type"], a.kopf["x-frame-options"], a.body), (403, "text/plain; charset=utf-8", "DENY", b"nicht erlaubt"))
        self.assertIsNone(self.m.antwort_lesen(b"Muell"))
        env = self.m.env_datei_lesen(b"# Kommentar\nA='eins'\nB=zwei\nC='mit=gleich'\n\nkaputt\n")
        self.assertEqual(env, {"A": "eins", "B": "zwei", "C": "mit=gleich"})

    def test_token_pruefung_wie_der_dienst(self):
        self.assertTrue(self.m.token_gueltig("A" * 20 + "bcdefghijklmnopqrstuvwxyz"))
        self.assertFalse(self.m.token_gueltig("a" * 60))  # zu wenig verschiedene Zeichen
        self.assertFalse(self.m.token_gueltig("Ab" * 10))  # zu kurz
        self.assertFalse(self.m.token_gueltig("Ab cdefghijklmnopqrstuvwxyz" * 3))  # Leerzeichen

    def test_form_und_text(self):
        text = lies(RAUCHTEST)
        self.assertTrue(text.startswith("#!/usr/bin/env python3\n"))
        self.assertLess(len(text.splitlines()), 800)
        self.assertNotIn("import requests", text)
        for erlaubt in re.findall(r"^(?:import|from) ([a-z_.]+)", text, flags=re.M):
            self.assertIn(erlaubt.split(".")[0], sys.stdlib_module_names | {"__future__"}, erlaubt)  # nur die Standardbibliothek
        import ast

        baum = ast.parse(text)
        for knoten in ast.walk(baum):
            if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                laenge = knoten.end_lineno - knoten.lineno + 1
                self.assertLessEqual(laenge, 50, f"{knoten.name} hat {laenge} Zeilen")
        self.assertNotIn("print(token", text)


# ====================================================================================== Teil 6: Namensvertrag, README, CHANGELOG
class DokumentationTests(unittest.TestCase):
    def test_namensvertrag_in_claude_md(self):
        zeile = next((z for z in lies(REPO / "CLAUDE.md").splitlines() if z.startswith("| Labor-Dienst (Betrieb) |")), None)
        self.assertIsNotNone(zeile, "Zeile Labor-Dienst (Betrieb) fehlt in der Tabelle")
        for soll in ("wamo-labor-dienst", "wamo-labor-netz", "wamo-labor-lokal", "55447", "127.0.0.1:18765", "/etc/wamo-labor", "/var/lib/wamo-labor", "10001",
                     "wamo-labor-einrichten.sh", "wamo-labor-rueckweg.sh", "wamo-labor-loeschlauf.sh", "wamo_labor_rauchtest.py", "WAMO_LABOR_DIENST_*",
                     "WAMO_LABOR_URL", "WAMO_LABOR_TOKEN", "WAMO-FW-LABOR", "WAMO-FW-LABAUS", "11.12.2026", "wamo-labor.caddy"):
            self.assertIn(soll, zeile, soll)
        self.assertNotIn(chr(0x2014), zeile)

    def test_abschnitt_im_readme_von_sokrates2(self):
        text = lies(SOKRATES2 / "README.md")
        self.assertIn("## 19. Labor-Dienst", text)
        abschnitt = text.split("## 19. Labor-Dienst", 1)[1]
        for soll in ("wamo-labor-einrichten.sh", "wamo-labor-rueckweg.sh", "wamo-labor-loeschlauf.sh", "wamo_labor_rauchtest.py", "--trocken", "Q13",
                     "betreuer-adressen", "plaetze-adressen", "11.12.2026", "Digest", "Offene Punkte", "Image-Bau", "echten Secrets", "LABOR-LOESCHEN",
                     "umschalten-kern", "Aufbewahrung"):
            self.assertIn(soll, abschnitt, soll)
        for verboten in (chr(0x2014), chr(0x2013), *VERBOTENE_WOERTER):
            self.assertNotIn(verboten, abschnitt.lower() if verboten in VERBOTENE_WOERTER else abschnitt)

    def test_changelog_unter_hinzugefuegt(self):
        text = lies(REPO / "CHANGELOG.md")
        offen = text.split("## [Unveröffentlicht]", 1)[1].split("\n## [", 1)[0]
        hinzugefuegt = offen.split("### Hinzugefügt", 1)[1]
        eintrag = next((b for b in hinzugefuegt.split("\n- ") if b.startswith("Betrieb des Labor-Dienstes")), None)
        self.assertIsNotNone(eintrag, "Eintrag zum Betrieb des Labor-Dienstes fehlt unter Hinzugefügt")
        for soll in ("wamo-labor-einrichten.sh", "wamo_labor_rauchtest.py", "55447", "Q13"):
            self.assertIn(soll, eintrag, soll)
        self.assertNotIn(chr(0x2014), eintrag)


if __name__ == "__main__":
    unittest.main()
