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
        self.assertNotIn("wamo-core.service restart", t)   # die laufende alte Unit wird nie angefasst
        self.assertNotIn("systemctl restart", t)
        self.assertNotIn("systemctl start", t)

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
