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
SKRIPTE = []  # wächst mit den Teilen: EINRICHTEN, RUECKWEG, LOESCHLAUF
ALLE_DATEIEN = [LABOR / "docker-compose.yml", SITE]  # wächst mit den Teilen: Skripte und Rauchtest
VERBOTENE_WOERTER = ("nieeeki", "jarvis", "promptcontrol", "damicon")


def _bash():
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
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

    @unittest.skipUnless(__import__("importlib").util.find_spec("yaml"), "PyYAML fehlt")
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
        self.assertEqual(adressen.strip(), "remote_ip 127.0.0.0/8 172.24.0.1/32")  # Loopback, auch über den Docker-Proxy (Gateway von wamo-caddy_default)

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


if __name__ == "__main__":
    unittest.main()
