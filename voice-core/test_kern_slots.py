"""Tests der Kern-Slots (infra/sokrates2/wamo-core@.service, wamo-core-slots.sh, wamo-core-vorpruefung.sh, Schritt 2.1).

Statische Prüfungen ohne systemd: Die Vorlage darf sich von der bestehenden Unit nur im Slot unterscheiden (gleiche Härtung, gleiche Grenzen), das
Skript muss gültiges bash sein und seine Schutzregeln enthalten, die Vorprüfung kennt den Slot-Betrieb. Die echte Probe (zwei Instanzen nebeneinander,
Vorprüfung mit zwei Slots) läuft auf Sokrates-2.

Ausführen:  uv run python -m pytest -q test_kern_slots.py   (in voice-core/)
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
ALT = INFRA / "wamo-core.service"
VORLAGE = INFRA / "wamo-core@.service"
SKRIPT = INFRA / "wamo-core-slots.sh"
VORPRUEFUNG = INFRA / "wamo-core-vorpruefung.sh"


def abschnitt(pfad: Path, name: str) -> list[tuple[str, str]]:
    """Schlüssel-Wert-Paare eines Abschnitts (Kommentare und Leerzeilen entfallen, Mehrfachschlüssel bleiben in der Reihenfolge)."""
    aus: list[tuple[str, str]] = []
    aktuell = ""
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#"):
            continue
        if zeile.startswith("["):
            aktuell = zeile.strip("[]")
            continue
        if aktuell == name and "=" in zeile:
            k, v = zeile.split("=", 1)
            aus.append((k, v))
    return aus


class Vorlage(unittest.TestCase):
    ERLAUBT_ANDERS = {"ExecStart", "ExecStopPost", "WorkingDirectory", "Environment"}

    def test_haertung_und_grenzen_wie_die_bestehende_unit(self):
        alt = {k: v for k, v in abschnitt(ALT, "Service") if k not in self.ERLAUBT_ANDERS}
        neu = {k: v for k, v in abschnitt(VORLAGE, "Service") if k not in self.ERLAUBT_ANDERS}
        # SystemCallFilter steht zweimal (erlauben, verbieten): beide Zeilen müssen übereinstimmen
        alt_sf = [v for k, v in abschnitt(ALT, "Service") if k == "SystemCallFilter"]
        neu_sf = [v for k, v in abschnitt(VORLAGE, "Service") if k == "SystemCallFilter"]
        self.assertEqual(alt_sf, neu_sf)
        self.assertEqual(neu, alt)

    def test_umgebung_unterscheidet_sich_nur_im_slot(self):
        def umgebung(p):
            return {v.split("=", 1)[0]: v.split("=", 1)[1] for k, v in abschnitt(p, "Service") if k == "Environment"}
        alt, neu = umgebung(ALT), umgebung(VORLAGE)
        self.assertEqual(set(neu) - set(alt), {"WAMO_SLOT"})
        self.assertEqual(set(alt) - set(neu), set())
        for name in set(alt):
            if name == "WAMO_CORE_UDS":
                self.assertEqual((alt[name], neu[name]), ("/run/wamo-core/core.sock", "/run/wamo-core/core-%i.sock"))
            else:
                self.assertEqual(neu[name], alt[name], name)
        self.assertEqual(neu["WAMO_SLOT"], "%i")

    def test_pfade_laufen_ueber_den_slot(self):
        s = dict(abschnitt(VORLAGE, "Service"))
        self.assertEqual(s["WorkingDirectory"], "/opt/wamo-core/slots/%i/voice-core")
        self.assertEqual(s["ExecStart"], "/opt/wamo-core/slots/%i/venv/bin/python bot.py -t webrtc")
        self.assertEqual(s["ExecStartPre"], "/opt/wamo-core/bin/wamo-core-vorpruefung")
        self.assertEqual(s["ExecStopPost"], "-/usr/bin/rm -f /run/wamo-core/core-%i.sock")
        self.assertIn(("ConditionPathExists", "/opt/wamo-core/slots/%i/voice-core/bot.py"), abschnitt(VORLAGE, "Unit"))

    def test_keine_festen_pfade_der_alten_unit_und_keine_gedankenstriche(self):
        text = VORLAGE.read_text(encoding="utf-8")
        self.assertNotIn("/opt/wamo-core/app", text)
        self.assertNotIn("/opt/wamo-core/venv", text)
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)

    def test_nur_die_alte_unit_hat_den_festen_socket(self):
        self.assertIn("core.sock", ALT.read_text(encoding="utf-8"))
        self.assertNotIn("core.sock", "\n".join(v for _, v in abschnitt(VORLAGE, "Service")))


class Skript(unittest.TestCase):
    def test_gueltiges_bash(self):
        if os.name != "posix" or not shutil.which("bash"):
            self.skipTest("bash-Syntaxprüfung nur unter Linux (unter Windows liefert bash.exe die WSL-Fassung)")
        for p in (SKRIPT, VORPRUEFUNG):
            with self.subTest(datei=p.name):
                self.assertEqual(subprocess.run(["bash", "-n", p.name], cwd=str(INFRA), capture_output=True).returncode, 0)

    def test_schutzregeln(self):
        t = SKRIPT.read_text(encoding="utf-8")
        for muss in ("set -euo pipefail", "Releases werden nie überschrieben", "läuft: erst stoppen", "mv -T", "NAME_RE=", "Import-Probe",
                     "go-w", "chown -R root:root"):
            self.assertIn(muss, t, muss)
        self.assertNotIn(chr(0x2014), t)
        self.assertNotIn("systemctl restart", t)   # nichts wird je neu gestartet, nur gestartet und gestoppt
        # die alte Unit wird nur von den beiden Umstellungs-Befehlen angefasst, und erst nachdem Slot a antwortet
        for zeile in t.splitlines():
            if re.search(r"systemctl (disable|enable|start|stop)", zeile) and "wamo-core.service" in zeile:
                self.assertTrue(zeile.strip().startswith(("systemctl disable --now wamo-core.service", "systemctl enable --now wamo-core.service")), zeile)
        umstellen = t[t.index("cmd_umstellen() {"):t.index("cmd_umstellung_zurueck() {")]
        self.assertLess(umstellen.index("ruhe_pruefen wamo-core.service"), umstellen.index("systemctl start wamo-core@a.service"))
        self.assertLess(umstellen.index("warte_auf_slot a"), umstellen.index("cmd_inc a"))
        self.assertLess(umstellen.index("cmd_inc a"), umstellen.index("systemctl disable --now wamo-core.service"))   # Caddy zeigt auf a, bevor die alte Unit endet
        self.assertIn("RUHE_MIN", t)

    def test_kern_release_ohne_luecke_pruefen_zuerst_schalten_dann_nachlauf_dann_auslaufen(self):
        t = SKRIPT.read_text(encoding="utf-8")
        f = t[t.index("cmd_umschalten_kern() {"):t.index("# Bewährung:")]
        reihenfolge = ["wurde nicht gesund", 'cmd_inc "$neu"\n', "sleep \"${NACHLAUF_S:-60}\"", 'cmd_inc "$neu" auslaufend "$alt"']
        positionen = [f.index(x) for x in reihenfolge]
        self.assertEqual(positionen, sorted(positionen))
        self.assertLess(f.index("warte_auf_slot"), f.index('cmd_inc "$neu"\n'))   # erst gesund, dann geschaltet
        self.assertIn("nichts umgeschaltet", f)   # ein ungesunder neuer Slot lässt den alten unberührt
        self.assertIn("erst ausleiten", f)          # ein laufender freier Slot wird nie überfahren

    def test_nach_einem_neustart_des_servers_laeuft_der_aktive_slot(self):
        # Praxisprobe: Aktiviert war nur wamo-core@a; nach einem Reboot hätte Caddy auf b gezeigt und a wäre gelaufen
        t = SKRIPT.read_text(encoding="utf-8")
        u = t[t.index("cmd_umschalten_kern() {"):t.index("# Bewährung:")]
        self.assertLess(u.index('systemctl enable "wamo-core@$neu.service"'), u.index('cmd_inc "$neu"\n'))   # erst aktiviert, dann geschaltet
        r = t[t.index("cmd_rueckweg_kern() {"):t.index("# Beendet die auslaufende Instanz")]
        self.assertIn('systemctl enable "wamo-core@$alt.service"', r)
        a = t[t.index("cmd_ausleiten() {"):t.index("\ncase \"$befehl\" in")]
        self.assertIn('systemctl disable "wamo-core@$alt.service"', a)
        self.assertIn('systemctl enable "wamo-core@$akt.service"', a)

    def test_befehle_ueberschreiben_keine_globalen_variablen_des_aufrufers(self):
        # Fehler der ersten Praxisprobe: cmd_inc setzte die globalen Namen neu und alt und zerstörte damit die Werte von umschalten-kern
        t = SKRIPT.read_text(encoding="utf-8")
        inc = t[t.index("cmd_inc() {"):][:1500]
        self.assertIn("local tmp vorher", inc)
        self.assertNotRegex(inc[:inc.index("echo \"Caddy schaltet")], r"\b(neu|alt)=")
        for funktion, namen in (("cmd_umschalten_kern", "name alt neu"), ("cmd_rueckweg_kern", "alt neu"), ("cmd_ausleiten", "akt alt alter"),
                                ("cmd_bewaehren", "min akt ende schlecht code"), ("cmd_umstellen", "name")):
            kopf = t[t.index(funktion + "() {"):][:120]
            self.assertIn("local " + namen, kopf, funktion)

    def test_bewaehrung_rueckweg_und_ausleiten(self):
        t = SKRIPT.read_text(encoding="utf-8")
        b = t[t.index("cmd_bewaehren() {"):t.index("cmd_rueckweg_kern() {")]
        self.assertIn('[ "$schlecht" -ge 3 ]', b)   # dreimal hintereinander nicht gesund
        self.assertIn("cmd_rueckweg_kern", b)
        r = t[t.index("cmd_rueckweg_kern() {"):t.index("# Beendet die auslaufende Instanz")]
        self.assertIn("kein schneller Rückweg", r)   # läuft die vorige Instanz nicht mehr, wird nicht geraten
        self.assertIn('cmd_inc "$alt" auslaufend "$neu"', r)
        a = t[t.index("cmd_ausleiten() {"):]
        for muss in ("BEWAEHRUNG_S:-1800", 'sitzungen_von "$alt")" = 0', "ruhe_pruefen", "Die Bewährung läuft noch"):
            self.assertIn(muss, a, muss)
        self.assertLess(a.index("Die Bewährung läuft noch"), a.index("systemctl stop"))   # beendet wird zuletzt

    def test_release_name_wird_streng_geprueft(self):
        m = re.search(r"NAME_RE='([^']+)'", SKRIPT.read_text(encoding="utf-8"))
        muster = re.compile(m.group(1))
        for gut in ("v0.1.0", "v0.1.0-abc123", "v1_2.3"):
            self.assertTrue(muster.match(gut), gut)
        for schlecht in ("0.1.0", "v", "../v1", "v1/../x", "v1 x", "v1;ls", "v" + "a" * 60, "V1", ""):
            self.assertFalse(muster.match(schlecht), schlecht)

    def test_zeilenenden_lf(self):
        for p in (SKRIPT, VORLAGE, VORPRUEFUNG):
            self.assertNotIn(b"\r\n", p.read_bytes(), p.name)


CADDY = INFRA / "wamo-caddy" / "kern"


def inc_text(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "wamo-core-slots.sh", "inc-text", *args], cwd=str(INFRA), capture_output=True, text=True)


def code(text: str) -> str:
    """Der Text ohne Kommentarzeilen."""
    return chr(10).join(z for z in text.splitlines() if not z.lstrip().startswith("#")) + chr(10)


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "braucht bash unter Linux")
class IncludeDatei(unittest.TestCase):
    def test_legacy_ist_nur_die_standardroute_der_alten_unit(self):
        t = code(inc_text("legacy").stdout)
        self.assertIn("import kern_weiter /run/wamo-core/core.sock", t)
        self.assertNotIn("/wamo/slot", t)
        self.assertNotIn("/i/a/", t)
        self.assertEqual(t.count("handle"), 1)

    def test_aktiver_slot_a(self):
        t = code(inc_text("a").stdout)
        self.assertIn('respond "a" 200', t)
        self.assertIn("Cache-Control no-store", t)
        self.assertIn("handle /i/a/* {", t)
        self.assertIn("handle /i/b/* {", t)
        self.assertIn("uri strip_prefix /i/a", t)
        self.assertIn("uri strip_prefix /i/b", t)
        self.assertRegex(t, r"handle \{\n\timport kern_weiter /run/wamo-core/core-a\.sock\n\}")
        self.assertNotIn("409", t)   # ohne auslaufende Instanz kein Abweisen
        self.assertNotIn("@", t.replace("@ui", "").replace("@vorab", ""))   # kein offener Platzhalter

    def test_auslaufende_instanz_bekommt_409_mit_dem_aktiven_slot_und_cors(self):
        t = code(inc_text("b", "auslaufend", "a").stdout)
        self.assertIn("handle /i/a/start {", t)
        self.assertIn('respond `{"slot":"b"}` 409', t)
        self.assertIn("respond @vorab 204", t)   # der Preflight des Browsers muss durchgehen, sonst sieht die Oberfläche den Status nie
        self.assertIn("Access-Control-Allow-Headers \"Authorization, Content-Type\"", t)
        self.assertLess(t.index("handle /i/a/start"), t.index("handle /i/a/*"))   # die engere Route zuerst
        self.assertRegex(t, r"handle \{\n\timport kern_weiter /run/wamo-core/core-b\.sock\n\}")

    def test_ungueltige_aufrufe(self):
        for args in (("c",), ("a", "auslaufend", "a"), ("a", "auslaufend", "x"), ("a", "stoppen", "b"), ("legacy", "auslaufend", "b"), ("",)):
            with self.subTest(args=args):
                p = inc_text(*args)
                self.assertNotEqual(p.returncode, 0)
                self.assertEqual(p.stdout, "")

    def test_slot_antwort_darf_jeder_ursprung_lesen_aber_nur_dort(self):
        # Die Desktop-App läuft auf localhost, die Oberfläche im LAN auf 55445: beide müssen den Slot lesen können, sonst startete die App
        # ohne Slot-Pfad und ihre Sitzung geriete beim nächsten Umschalten auf die falsche Instanz.
        t = code(inc_text("a", "auslaufend", "b").stdout)
        slot = t[t.index("handle /wamo/slot {"):t.index("handle /i/b/start {")]
        self.assertIn('Access-Control-Allow-Origin "*"', slot)
        for geschuetzt in ("handle /i/a/* {", "handle /i/b/* {"):   # alles andere bleibt bei der Zugangskontrolle des Kerns (kein CORS-Kopf von Caddy)
            block = t[t.index(geschuetzt):]
            block = block[:block.index("}\n")]
            self.assertNotIn("Access-Control", block)
        self.assertNotIn("Access-Control", t[t.index("handle {\n"):])


class CaddySite(unittest.TestCase):
    def test_site_bindet_die_include_datei_ein_und_behaelt_die_grenzen(self):
        t = (CADDY / "wamo-core.caddy").read_text(encoding="utf-8")
        self.assertIn("import /etc/caddy/kern/slot.inc", t)
        self.assertIn("(kern_weiter) {", t)
        self.assertIn("max_size 26MiB", t)
        self.assertIn("max_size 512KB", t)
        self.assertIn("reverse_proxy unix/{args[0]}", t)
        self.assertIn("flush_interval -1", t)
        self.assertIn("response_header_timeout 60s", t)
        self.assertNotIn("core.sock", t.split("(kern_weiter)")[1].split("https://")[0])   # der Socket kommt nur über das Argument
        self.assertIn("@freigeben", t)   # die Freigabe-Route bleibt und steht vor der Include-Datei
        self.assertLess(t.index("handle @freigeben"), t.index("import /etc/caddy/kern/slot.inc"))

    def test_grenze_gilt_auch_hinter_dem_slot_pfad(self):
        t = (CADDY / "wamo-core.caddy").read_text(encoding="utf-8")
        muster = re.search(r"@dateien path_regexp (\S+)", t).group(1)
        r = re.compile(muster)
        for gut in ("/wamo/api/dateien", "/wamo/api/dateien/abc", "/i/a/wamo/api/dateien", "/i/b/wamo/api/dateien/x"):
            self.assertTrue(r.match(gut), gut)
        for schlecht in ("/wamo/api/dateienxyz", "/i/c/wamo/api/dateien", "/wamo/api/andere", "/start"):
            self.assertFalse(r.match(schlecht), schlecht)

    def test_caddy_skript_legt_die_include_datei_an_und_ueberschreibt_keine_auswahl(self):
        t = (INFRA / "wamo-core-caddy.sh").read_text(encoding="utf-8")
        self.assertIn("slot.inc.legacy", t)
        self.assertIn('[ -f "$CADDY_DIR/etc/kern/slot.inc" ] || install', t)

    def test_legacy_datei_und_vorlage_ohne_gedankenstriche(self):
        for n in ("slot.inc.legacy", "slot.inc.vorlage"):
            t = (CADDY / n).read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), t)
            self.assertNotIn(chr(0x2013), t)


class Vorpruefung(unittest.TestCase):
    def test_slot_betrieb_ist_streng_und_der_alte_betrieb_bleibt(self):
        t = VORPRUEFUNG.read_text(encoding="utf-8")
        self.assertIn('case "$slot" in a|b) ;; *) fehler "WAMO_SLOT muss a oder b sein" ;; esac', t)
        self.assertIn('[ "${WAMO_CORE_UDS:-}" = "/run/wamo-core/core-$slot.sock" ]', t)
        self.assertIn("/opt/wamo-core/releases/?*", t)
        self.assertIn("gehört nicht root", t)
        self.assertIn('[ "${WAMO_CORE_UDS:-}" = /run/wamo-core/core.sock ] || fehler "WAMO_CORE_UDS unerwartet"', t)   # ohne Slot wie bisher

    @unittest.skipUnless(os.name == "posix" and shutil.which("sh"), "braucht sh")
    def test_unzulaessiger_slot_wird_abgelehnt(self):
        # läuft bis zur Slot-Prüfung: die Pflichtwerte werden gesetzt, damit der Slot der erste Fehler ist
        with tempfile.TemporaryDirectory() as t:
            env = {"PATH": "/usr/bin:/bin", "SONIOX_API_KEY": "x", "SUPABASE_PUBLISHABLE_KEY": "x",
                   "WAMO_DATABASE_URL": "postgresql://x?sslmode=verify-full", "WAMO_ENTWICKLUNG": "aus", "WAMO_SLOT": "c",
                   "WAMO_CORE_UDS": "/run/wamo-core/core-c.sock"}
            if (Path("/opt/wamo-core").exists()):
                self.skipTest("läuft nur auf einem Rechner ohne /opt/wamo-core")
            p = subprocess.run(["sh", str(VORPRUEFUNG)], env=env, capture_output=True, text=True, cwd=t)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("WAMO_SLOT muss a oder b sein", p.stderr)


if __name__ == "__main__":
    unittest.main()
