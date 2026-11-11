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

    def test_probe_ist_eine_einzelne_gesundheitsprobe_des_aktiven_slots(self):
        t = SKRIPT.read_text(encoding="utf-8")
        p = t[t.index("cmd_probe() {"):t.index("cmd_rueckweg_kern() {")]
        for muss in ("root_pflicht", 'akt="$(aktiver_slot)"', "--unix-socket", "http://localhost/start", 'systemctl is-active --quiet "wamo-core@$akt.service"', 'echo "SLOT $akt"'):
            self.assertIn(muss, p, muss)
        self.assertNotIn("sleep", p)   # eine Probe, keine Schleife: die Wiederholung gehört dem Anker
        self.assertNotIn("cmd_rueckweg_kern", p)   # und sie ändert nichts
        self.assertIn("  probe) cmd_probe ;;", t)

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


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "braucht bash unter Linux")
class DropinsSpiegeln(unittest.TestCase):
    """Fehler der ersten Umstellung: Die Drop-ins der alten Unit (Ursprung der Oberfläche, Fernzugriff) fehlten den Slots, die Oberfläche bekam 400."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.alt = Path(self.tmp.name) / "alt.d"
        self.neu = Path(self.tmp.name) / "neu.d"
        self.alt.mkdir()

    def lauf(self):
        env = {"PATH": os.environ["PATH"], "WAMO_DROPIN_ALT": str(self.alt), "WAMO_DROPIN_NEU": str(self.neu)}
        return subprocess.run(["bash", "wamo-core-slots.sh", "dropins-spiegeln"], cwd=str(INFRA), env=env, capture_output=True, text=True)

    def test_dateien_werden_mit_marke_gespiegelt(self):
        (self.alt / "wamo-ui.conf").write_text("[Service]\nEnvironment=PIPECAT_ALLOWED_ORIGINS=https://x:55445\n", encoding="utf-8")
        (self.alt / "wamo-rd.conf").write_text("[Service]\nSupplementaryGroups=wamo-rd-gateway\n", encoding="utf-8")
        (self.alt / "kein-conf.txt").write_text("nicht", encoding="utf-8")
        p = self.lauf()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(sorted(os.listdir(self.neu)), ["wamo-rd.conf", "wamo-ui.conf"])
        t = (self.neu / "wamo-ui.conf").read_text(encoding="utf-8")
        self.assertTrue(t.startswith("# gespiegelt von wamo-core.service.d"))
        self.assertIn("PIPECAT_ALLOWED_ORIGINS=https://x:55445", t)

    def test_geaenderte_und_entfernte_dropins_folgen_eigene_bleiben(self):
        (self.alt / "wamo-ui.conf").write_text("[Service]\nEnvironment=A=1\n", encoding="utf-8")
        (self.alt / "wamo-rd.conf").write_text("[Service]\nEnvironment=B=1\n", encoding="utf-8")
        self.lauf()
        self.neu.joinpath("eigen.conf").write_text("[Service]\nEnvironment=C=1\n", encoding="utf-8")   # gehört nur der Vorlage
        (self.alt / "wamo-ui.conf").write_text("[Service]\nEnvironment=A=2\n", encoding="utf-8")
        (self.alt / "wamo-rd.conf").unlink()
        self.assertEqual(self.lauf().returncode, 0)
        self.assertIn("A=2", (self.neu / "wamo-ui.conf").read_text(encoding="utf-8"))
        self.assertEqual(sorted(os.listdir(self.neu)), ["eigen.conf", "wamo-ui.conf"])   # gespiegeltes weg, eigenes bleibt

    def test_ohne_dropins_der_alten_unit_bleibt_alles_ruhig(self):
        shutil.rmtree(self.alt)
        self.assertEqual(self.lauf().returncode, 0)

    def test_umstellen_und_umschalten_kern_spiegeln_vor_dem_start(self):
        t = SKRIPT.read_text(encoding="utf-8")
        for funktion in ("cmd_umstellen", "cmd_umschalten_kern", "cmd_einrichten"):
            kopf = t[t.index(funktion + "() {"):][:200]
            self.assertIn("dropins_spiegeln", kopf, funktion)


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


class AltpfadeAufgeraeumt(unittest.TestCase):
    """Paket R11: Im Slot-Betrieb startet keines der Einrichtungsskripte die alte Unit wamo-core.service neu. Sie melden den Hinweis auf
    umschalten-kern und enden mit Exit 0 (Schnittstelle I11). Der Entwicklungsschalter bleibt aus der Vorprüfung, der Unit und aus core.env."""

    REPO = INFRA.parent.parent
    # Skripte, die die alte Unit nur im Betrieb ohne Slots neu starten (hinter dem Slot-Schutz slot_betrieb)
    BEDINGT = {"infra/sokrates2/wamo-core-einrichten.sh", "infra/sokrates2/wamo-rd-einrichten.sh", "infra/sokrates2/wamo-ui-einrichten.sh",
               "infra/sokrates2/wamo-ui-rueckweg.sh"}
    # Bewusste Treffer: das Slot-Skript startet die Instanzen der Vorlage, der Aufruf vom PC prüft den Slot-Betrieb selbst (Paket R6)
    BEWUSST = {"infra/sokrates2/wamo-core-slots.sh", "scripts/wamo_kern_sokrates2.py"}
    MUSTER = re.compile(r"systemctl (restart|start) wamo-core")

    def treffer(self) -> dict[str, list[int]]:
        gefunden: dict[str, list[int]] = {}
        for ordner in ("infra", "scripts"):
            for pfad in sorted((self.REPO / ordner).rglob("*")):
                if not pfad.is_file() or "__pycache__" in pfad.parts or pfad.suffix in (".pyc", ".crt", ".png", ".gz"):
                    continue
                try:
                    zeilen = pfad.read_text(encoding="utf-8").splitlines()
                except (UnicodeDecodeError, OSError):
                    continue
                for nr, zeile in enumerate(zeilen, 1):
                    if self.MUSTER.search(zeile):
                        gefunden.setdefault(pfad.relative_to(self.REPO).as_posix(), []).append(nr)
        return gefunden

    def test_nur_bewusste_treffer_fuer_systemctl_restart_oder_start_wamo_core(self):
        unerwartet = {k: v for k, v in self.treffer().items() if k not in self.BEDINGT | self.BEWUSST}
        self.assertEqual(unerwartet, {}, "Neustart oder Start der alten Unit ohne Schutz: bitte über umschalten-kern oder hinter slot_betrieb")

    def test_jeder_bedingte_treffer_steht_hinter_dem_slot_schutz(self):
        gefunden = self.treffer()
        for datei in sorted(self.BEDINGT & set(gefunden)):
            zeilen = (self.REPO / datei).read_text(encoding="utf-8").splitlines()
            with self.subTest(datei=datei):
                self.assertTrue(any(z.startswith("slot_betrieb()") for z in zeilen), "slot_betrieb() fehlt")
                erster_aufruf = next((i for i, z in enumerate(zeilen, 1) if re.search(r"(?<![\w(])slot_betrieb(?![\w(])", z) and not z.startswith("slot_betrieb()")), None)
                self.assertIsNotNone(erster_aufruf, "slot_betrieb wird nie abgefragt")
                for nr in gefunden[datei]:
                    self.assertGreater(nr, erster_aufruf, f"Zeile {nr} startet die alte Unit vor der Abfrage des Slot-Betriebs")
                self.assertIn("umschalten-kern", "\n".join(zeilen))

    def test_die_vier_skripte_haben_dieselbe_slot_betrieb_funktion_mit_zustand_statt_is_active(self):
        texte = {}
        for datei in sorted(self.BEDINGT):
            text = (self.REPO / datei).read_text(encoding="utf-8")
            m = re.search(r"^slot_betrieb\(\) \{\n.*?^\}$", text, re.M | re.S)
            self.assertIsNotNone(m, f"{datei}: slot_betrieb fehlt")
            texte[datei] = m.group(0)
        self.assertEqual(len(set(texte.values())), 1, "die Kopien der Funktion unterscheiden sich")
        funktion = next(iter(texte.values()))
        self.assertIn("systemctl show -p ActiveState --value", funktion)
        self.assertIn("active|activating|reloading|deactivating", funktion)
        self.assertNotIn("is-active", funktion)  # is-active versagt im Fenster activating (auto-restart)
        self.assertIn('/opt/wamo-core/slots/$s', funktion)
        self.assertIn("sudo -n test -e", funktion)  # der Ordner ist root:wamo-core 0750: ohne sudo ist ein -e falsch negativ
        self.assertIn("return 0", funktion)

    @staticmethod
    def _bash():
        """Unter Windows die Git Bash (shutil.which fände dort das WSL-Programm bash.exe in System32), sonst die erste bash im Pfad."""
        kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
        kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
        return next((k for k in kandidaten if k and os.path.exists(k)), None)

    def test_der_hinweis_in_caddy_und_rd_rueckweg_nennt_umschalten_kern(self):
        caddy = (INFRA / "wamo-core-caddy.sh").read_text(encoding="utf-8")
        self.assertNotIn("systemctl start wamo-core", caddy)
        self.assertIn("umschalten-kern", caddy)
        rd_rueck = (INFRA / "wamo-rd-rueckweg.sh").read_text(encoding="utf-8")
        self.assertNotIn("systemctl restart wamo-core", rd_rueck)
        self.assertIn("umschalten-kern", rd_rueck)

    def test_wamo_core_env_dokumentiert_den_schalter_im_dropin_und_erlaubt_ihn_nicht(self):
        import importlib.util
        import sys
        import types
        from unittest import mock
        spec = importlib.util.spec_from_file_location("wamo_core_env_r11", INFRA / "wamo_core_env.py")
        modul = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"grp": types.ModuleType("grp")}):  # grp gibt es nur unter Linux; hier genügt der Import
            spec.loader.exec_module(modul)
        self.assertFalse([n for n in modul.ERLAUBT if n.startswith("WAMO_ENTWICKLUNG")])
        self.assertFalse(modul.erlaubt("WAMO_ENTWICKLUNG") or modul.erlaubt("WAMO_ENTWICKLUNG_BUERO") or modul.erlaubt("WAMO_ENTWICKLUNG_PROFILE"))
        text = (INFRA / "wamo_core_env.py").read_text(encoding="utf-8")
        self.assertIn("wamo-entwicklung-buero.conf", text)  # Docstring und Kommentar nennen den Weg
        self.assertIn("WAMO_ENTWICKLUNG_BEFEHLE_BUERO", text)
        self.assertNotIn("ohne Wirkung auf Sokrates-2", text)  # der alte Kommentar stimmt im Betrieb nicht mehr

    def test_vorpruefung_und_unit_kennen_den_buero_schalter_nicht(self):
        vor = VORPRUEFUNG.read_text(encoding="utf-8")
        self.assertIn('[ "${WAMO_ENTWICKLUNG:-aus}" = aus ] || fehler "WAMO_ENTWICKLUNG muss aus sein"', vor)
        self.assertNotIn("WAMO_ENTWICKLUNG_BUERO", vor)
        for datei in (VORLAGE, ALT):
            text = datei.read_text(encoding="utf-8")
            self.assertIn("Environment=WAMO_ENTWICKLUNG=aus", text)
            self.assertNotIn("WAMO_ENTWICKLUNG_BUERO", text)
        # die Vorprüfung darf keinen weiteren Entwicklungsschalter lesen: sie bleibt in Zeile 25, wie sie war
        zeilen = vor.splitlines()
        self.assertEqual([i for i, z in enumerate(zeilen, 1) if "WAMO_ENTWICKLUNG" in z and not z.startswith("#")], [25])


class SlotBetriebNachbesserung(unittest.TestCase):
    """Nachbesserung nach der Sicherheitsprüfung: kern_ruhig kennt die Slots, slot_betrieb erkennt auch das Fenster ohne laufende Instanz."""

    REPO = INFRA.parent.parent
    BEDINGT = {"infra/sokrates2/wamo-core-einrichten.sh", "infra/sokrates2/wamo-rd-einrichten.sh", "infra/sokrates2/wamo-ui-einrichten.sh",
               "infra/sokrates2/wamo-ui-rueckweg.sh"}
    SKRIPTE = ("infra/sokrates2/wamo-ui-einrichten.sh", "infra/sokrates2/wamo-rd-einrichten.sh")
    SLOT_SKRIPTE = SKRIPTE + ("infra/sokrates2/wamo-core-einrichten.sh", "infra/sokrates2/wamo-ui-rueckweg.sh")

    @staticmethod
    def _bash():
        kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
        kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
        return next((k for k in kandidaten if k and os.path.exists(k)), None)

    def _funktion(self, datei: str, name: str) -> str:
        text = (self.REPO / datei).read_text(encoding="utf-8")
        m = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.M | re.S)
        self.assertIsNotNone(m, f"{datei}: {name} fehlt")
        return m.group(0)

    def _kern_ruhig(self, datei: str, **umgebung: str) -> subprocess.CompletedProcess:
        """Die echte Funktion kern_ruhig mit Attrappen für systemctl, sudo, ss und journalctl. PID_*: MainPID, SS: Ausgabe von ss, LOG_*: Journal."""
        bash = self._bash()
        if bash is None:
            self.skipTest("braucht bash")
        snippet = r'''
systemctl() { case "$2" in wamo-core) echo "${PID_ALT:-0}" ;; wamo-core@a) echo "${PID_A:-0}" ;; wamo-core@b) echo "${PID_B:-0}" ;; *) echo 0 ;; esac; }
sudo() { [ -z "${KEIN_SUDO:-}" ] || return 1; shift; "$@"; }
ss() { printf '%s' "${SS:-}"; }
journalctl() { case "$2" in wamo-core) printf '%s' "${LOG_ALT:-}" ;; wamo-core@a) printf '%s' "${LOG_A:-}" ;; wamo-core@b) printf '%s' "${LOG_B:-}" ;; esac; }
''' + self._funktion(datei, "kern_ruhig") + "\nkern_ruhig\n"
        env = {**os.environ, **umgebung}
        return subprocess.run([bash, "-c", snippet], capture_output=True, text=True, env=env, timeout=30)

    def test_ohne_laufende_instanz_ist_der_kern_ruhig(self):
        for datei in self.SKRIPTE:
            with self.subTest(datei=datei):
                self.assertEqual(self._kern_ruhig(datei).returncode, 0)

    def test_eine_sitzung_im_slot_b_oder_a_ist_nicht_ruhig(self):
        for datei in self.SKRIPTE:
            with self.subTest(datei=datei, fall="udp_b"):
                p = self._kern_ruhig(datei, PID_B="4711", SS="UNCONN 0 0 0.0.0.0:50000 users:((\"python\",pid=4711,fd=7))\n")
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertIn("wamo-core@b", p.stderr)
            with self.subTest(datei=datei, fall="journal_a"):
                p = self._kern_ruhig(datei, PID_A="4712", LOG_A="Nov 11 12:00:01 sokrates2 python[4712]: Sitzung gestartet\n")
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertIn("wamo-core@a", p.stderr)
            with self.subTest(datei=datei, fall="alte_unit"):
                self.assertEqual(self._kern_ruhig(datei, PID_ALT="100", LOG_ALT="Sitzung\n").returncode, 1)

    def test_ein_ruhiger_slot_ist_ruhig_auch_wenn_die_alte_unit_nicht_laeuft(self):
        for datei in self.SKRIPTE:
            with self.subTest(datei=datei):
                p = self._kern_ruhig(datei, PID_B="4711", SS="UNCONN 0 0 0.0.0.0:50000 users:((\"python\",pid=9999,fd=7))\n",
                                     LOG_B="Zugang abgelehnt\n401 Unauthorized\n")
                self.assertEqual(p.returncode, 0, p.stderr)  # abgewiesene Anfragen ohne Anmeldung sind keine Sitzung

    def test_ein_nicht_lesbarer_zustand_gilt_als_nicht_ruhig_fail_closed(self):
        for datei in self.SKRIPTE:
            with self.subTest(datei=datei):
                self.assertEqual(self._kern_ruhig(datei, PID_B="4711", KEIN_SUDO="1").returncode, 1)

    def _slot_betrieb(self, datei: str, ordner: str | None = None, **umgebung: str) -> subprocess.CompletedProcess:
        """Die echte Funktion slot_betrieb mit Attrappen für systemctl show (STATE_A, STATE_B, SHOW_FEHLER) und sudo (KEIN_SUDO). Ordner ersetzt
        /opt/wamo-core/slots."""
        bash = self._bash()
        if bash is None:
            self.skipTest("braucht bash")
        text = (self.REPO / datei).read_text(encoding="utf-8")
        funktion = re.search(r"^slot_betrieb\(\) \{\n.*?^\}$", text, re.M | re.S).group(0)
        if ordner is not None:
            funktion = funktion.replace("/opt/wamo-core/slots", ordner)
        snippet = r'''
systemctl() {
  [ -z "${SHOW_FEHLER:-}" ] || return 1
  [ "$1 $2 $3 $4" = "show -p ActiveState --value" ] || return 9
  case "$5" in wamo-core@a) printf '%s\n' "${STATE_A-inactive}" ;; wamo-core@b) printf '%s\n' "${STATE_B-inactive}" ;; *) return 9 ;; esac
}
sudo() { [ -z "${KEIN_SUDO:-}" ] || return 1; shift; "$@"; }
''' + funktion + "\nslot_betrieb\n"
        return subprocess.run([bash, "-c", snippet], capture_output=True, text=True, env={**os.environ, **umgebung}, timeout=30)

    def test_slot_betrieb_erkennt_jede_instanz_in_gang_auch_im_fenster_activating(self):
        """is-active ist im Fenster activating (auto-restart) falsch, dann würde die alte Unit gestartet. Der Zustand zählt, nicht is-active."""
        for datei in sorted(self.BEDINGT):
            for slot in ("A", "B"):
                for zustand in ("active", "activating", "reloading", "deactivating"):
                    with self.subTest(datei=datei, slot=slot, zustand=zustand):
                        p = self._slot_betrieb(datei, **{f"STATE_{slot}": zustand})
                        self.assertEqual(p.returncode, 0, p.stderr)

    def test_slot_betrieb_ohne_instanz_und_ohne_verweise_ist_kein_slot_betrieb(self):
        with tempfile.TemporaryDirectory() as t:
            ordner = f"/tmp/{Path(t).name}" if os.name == "nt" else t
            for datei in sorted(self.BEDINGT):
                for zustand in ("inactive", "failed"):
                    with self.subTest(datei=datei, zustand=zustand):
                        self.assertEqual(self._slot_betrieb(datei, ordner, STATE_A=zustand, STATE_B=zustand).returncode, 1)

    def test_slot_betrieb_erkennt_die_slot_verweise_ohne_laufende_instanz(self):
        for datei in sorted(self.BEDINGT):
            with tempfile.TemporaryDirectory() as t:
                ordner = f"/tmp/{Path(t).name}" if os.name == "nt" else t
                for name in ("a", "b"):
                    (Path(t) / name).mkdir()
                    with self.subTest(datei=datei, verweis=name):
                        self.assertEqual(self._slot_betrieb(datei, ordner).returncode, 0)
                    (Path(t) / name).rmdir()
                if os.name != "nt":
                    (Path(t) / "a").symlink_to(Path(t) / "gibt-es-nicht")  # ein Verweis ins Leere zählt ebenfalls
                    with self.subTest(datei=datei, verweis="ins Leere"):
                        self.assertEqual(self._slot_betrieb(datei, ordner).returncode, 0)

    def test_ein_fehler_beim_pruefen_gilt_als_slot_betrieb_fail_closed(self):
        with tempfile.TemporaryDirectory() as t:
            ordner = f"/tmp/{Path(t).name}" if os.name == "nt" else t
            for datei in sorted(self.BEDINGT):
                for fall, umgebung in (("systemctl scheitert", {"SHOW_FEHLER": "1"}), ("leere Antwort", {"STATE_A": ""}),
                                       ("leere Antwort b", {"STATE_B": ""}), ("kein sudo", {"KEIN_SUDO": "1"})):
                    with self.subTest(datei=datei, fall=fall):
                        self.assertEqual(self._slot_betrieb(datei, ordner, **umgebung).returncode, 0)


if __name__ == "__main__":
    unittest.main()
