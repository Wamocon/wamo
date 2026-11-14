"""Sicherheitstests fuer den Host: Validatoren direkt vor der Aktion.

Ausfuehren:  uv run python -m unittest -v test_security.py   (im Ordner host/)
Keiner dieser Tests startet ein Programm oder eine Webseite.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import wamo_host as host


def _interaktiv() -> bool:
    """Interaktive Windows-Sitzung mit einem Fenster im Vordergrund (sonst kein Echttest der Sperre)."""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetForegroundWindow())
    except (AttributeError, OSError):
        return False

# DNS in Tests fest vorgeben (kein Netz nötig, Ergebnis immer gleich)
FAKE_DNS = {"127.0.0.1.nip.io": ["127.0.0.1"], "localtest.me": ["127.0.0.1", "::1"],
            "intern.example": ["10.0.0.5"], "nirgends.invalid": []}
_dns_patch = mock.patch.object(host, "_resolve_host", side_effect=lambda h: FAKE_DNS.get(h, ["93.184.216.34"]))
# Sperre in Tests fest vorgeben: entsperrt (die Tests dürfen nicht vom echten Bildschirm abhängen)
_lock_patch = mock.patch.object(host.wc, "is_locked", return_value=False)
ECHT_IS_LOCKED = host.wc.is_locked  # die echte Prüfung für IsLockedTests


def setUpModule():
    _dns_patch.start()
    _lock_patch.start()


def tearDownModule():
    _lock_patch.stop()
    _dns_patch.stop()


class NamingTests(unittest.TestCase):
    def test_contract_names(self):
        self.assertEqual(host.mcp.name, "wamo-host")
        self.assertEqual(host.wc.OVERLAY_TITLE, "WAMO Stimmen-Rand")
        self.assertEqual(Path(host.__file__).name, "wamo_host.py")

    def test_default_audit_file(self):
        import os
        if not os.getenv("WAMO_AUDIT_LOG"):
            self.assertEqual(host.AUDIT_LOG.name, "wamo_audit.jsonl")


class UrlTests(unittest.TestCase):
    def test_accepts_public_https(self):
        for url in ["https://www.amazon.de/s?k=wohnzimmer+lampe", "https://wamocon.de/", "http://8.8.8.8/",
                    "https://xn--mnchen-3ya.de/"]:
            with self.subTest(url=url):
                self.assertEqual(host.validate_url(url), url)

    def test_rejects_bad_urls(self):
        bad = [
            "", "   ", None, 42,
            "javascript:alert(1)", "file:///C:/Windows/System32", "ftp://example.com",
            "ms-settings:", "https://", "https://user:pass@example.com",
            "http://localhost:3000", "http://127.0.0.1", "http://192.168.0.1/admin",
            "http://10.0.0.5", "http://[::1]/", "http://169.254.169.254/latest/meta-data",
            "http://printer.local", 'https://example.com/" & calc.exe',
            "https://example.com/a b", "https://example.com/" + "a" * 3000,
            "http://127.1/", "http://2130706433/", "http://0x7f.0.0.1/", "http://0177.0.0.1/", "http://0/",
            "http://localhost./", "http://intranet/",
            "http://127.0.0.1.nip.io/", "https://localtest.me/", "https://intern.example/x",
            "https://nirgends.invalid/",
        ]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ValueError):
                host.validate_url(url)


class AppTests(unittest.TestCase):
    def test_rejects_unknown_or_path(self):
        for name in ["cmd", "powershell", r"C:\Windows\System32\cmd.exe", "chrome; calc", "", None]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                host.resolve_app(name)

    def test_uri_apps_resolve(self):
        self.assertEqual(host.resolve_app("einstellungen"), "ms-settings:")


class PowershellTests(unittest.TestCase):
    def test_read_only(self):
        for cmd in [
            "Get-Process",
            "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5",
            f"Get-ChildItem '{host.HOME}' | Measure-Object",
            f"Get-Content \"{host.HOME}\\Projekte\\notizen.txt\"",
            "Get-ChildItem ~/Projekte",
            "Get-Date",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(host.classify(cmd), "read")

    def test_network_unc_and_paths_outside_home_are_critical(self):
        """M8: Netzwerk-Abfragen, UNC-Pfade, Pfade außerhalb des Benutzerordners, relative Pfade."""
        for cmd in [
            "Test-Connection google.com -Count 1", "Test-NetConnection x.example -Port 443",
            "Resolve-DnsName geheim.attacker.example", "Invoke-RestMethod https://x.example",
            "Get-ChildItem \\\\server\\share", "Get-Content //server/share/a.txt",
            "Get-ChildItem C:\\Windows", "Get-ChildItem C:\\Users", "Get-Content notizen.txt",
            "Get-ChildItem", "Get-Content D:\\IDEA\\Projekt\\x.txt",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(host.classify(cmd), "critical")

    def test_harmlose_lesebefehle_brauchen_keine_freigabe(self):
        """Sitzung 04.11.2026: diese vier Befehle verlangten je eine Freigabe, obwohl sie nur lesen."""
        for cmd in [
            'Get-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server" -Name fDenyTSConnections'
            ' | Select-Object fDenyTSConnections',
            'Get-ItemProperty -Path "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Policies" '
            '-ErrorAction SilentlyContinue | Select-Object *',
            'Get-ChildItem -Path "$env:USERPROFILE\\Projekte" -Recurse -Filter "*hook*" -ErrorAction SilentlyContinue'
            ' | Select-Object FullName',
            'Get-ChildItem -Path "$env:USERPROFILE\\Projekte\\x" -Recurse -Include "*hook*","*pre-commit*" '
            '-ErrorAction SilentlyContinue | Select-Object FullName',
            "Get-Service TermService | Select-Object Status, StartType",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(host.classify(cmd), "read")

    def test_erweiterungen_lassen_keine_umgehung_zu(self):
        for cmd in [
            "Get-ChildItem $env:PATH", "Get-ChildItem $env:USERPROFILEX", "Get-Content $env:USERPROFILE\\.ssh\\id_rsa",
            'Get-ChildItem -Path "$env:USERPROFILE" -Recurse -Filter "id_*" | Get-Content',   # Inhalt über Pipe
            'Get-ChildItem -Path "$env:USERPROFILE" -Filter "*.pem"',                        # Zugangsdaten
            "Get-Content \"$env:USERPROFILE\\WAM*\\host-token\"",                            # Stern im Pfad
            "Get-ChildItem -Path ~\\Pro* ",
            'Get-ItemProperty "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" | Select-Object *',
            'Get-ItemProperty "HKCU:\\Software\\Microsoft\\Office" | Select-Object *',
            'Get-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server\\..\\..\\Lsa"',
            "Get-ItemProperty HKLM:\\SAM\\SAM", "Get-Item Registry::HKEY_LOCAL_MACHINE\\SAM",
            'Set-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server" -Name fDenyTSConnections -Value 0',
            'Get-Process | Select-Object * | Remove-Item',
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(host.classify(cmd), "critical")

    def test_feste_variablen_werden_eingesetzt(self):
        self.assertEqual(host.expand_safe_vars("Get-ChildItem $env:USERPROFILE\\x"), f"Get-ChildItem {host.HOME}\\x")
        self.assertEqual(host.expand_safe_vars("$env:PATH"), "$env:PATH")

    def test_pending_is_bounded(self):
        host.PENDING.clear()
        for i in range(host.MAX_PENDING + 5):
            host.powershell_lesen(f"Stop-Process -Id {i}", "Test")
        self.assertEqual(len(host.PENDING), host.MAX_PENDING)
        host.PENDING.clear()

    def test_powershell_timeout_is_caught(self):
        import subprocess
        with mock.patch.object(host.subprocess, "run", side_effect=subprocess.TimeoutExpired("powershell", 1)), \
             mock.patch.object(host.platform, "system", return_value="Windows"):
            result = host._powershell("Get-Process", timeout=1)
        self.assertEqual(result["status"], "error")
        self.assertIn("Zeitlimit", result["summary"])

    def test_needs_approval(self):
        for cmd in [
            "Remove-Item C:\\temp -Recurse", "ri C:\\temp", "Stop-Process -Name chrome",
            "Get-Process; Remove-Item x", "Get-ChildItem | Remove-Item", "Get-Date & calc",
            "Get-Process `; calc", "Get-Item $(calc)", "iex (Get-Content x)",
            "Invoke-Expression 'calc'", "powershell -enc ZQBjAGgAbwA=", "Start-Process calc",
            "Get-Process > C:\\x.txt", "Set-ItemProperty HKCU:\\x -Name a -Value 1",
            "winget install vlc", "Invoke-WebRequest https://evil.example",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(host.classify(cmd), "critical")

    def test_blocked(self):
        for cmd in ["bcdedit /set x", "net user hacker /add", "Get-Credential",
                    "Set-MpPreference -DisableRealtimeMonitoring $true", "", "x" * 2000]:
            with self.subTest(cmd=cmd):
                self.assertEqual(host.classify(cmd), "blocked")

    def test_review_bypasses_are_not_read(self):
        """Fälle aus dem Review: Zeilenumbruch, Skriptblock, Typzugriff, Ausdruck, sensible Daten."""
        for cmd in [
            "Get-Date\ncalc.exe", "Get-Date\r\nAdd-Content C:/x/a.txt hi",
            "Get-Process | Where-Object { [System.Diagnostics.Process]::Start('calc.exe') }",
            "Get-Date (calc.exe)", "Get-Process | % { $_ }", "Get-Process | ? Name -eq x",
            "Get-Content C:\\Users\\x\\.env", "Get-Content C:\\Users\\x\\.ssh\\id_ed25519",
            "Get-ChildItem Env:", "Get-Clipboard", "Get-Item @args", "Get-Date\tcalc",
            "Get-Content D:\\IDEA\\Projekt\\wamo\\voice-core\\bot.py",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(host.classify(cmd), "critical")

    def test_critical_never_executes(self):
        with mock.patch.object(host, "_powershell") as run:
            result = host.powershell_lesen("Remove-Item C:\\wichtig -Recurse", "Test")
        run.assert_not_called()
        self.assertEqual(result["status"], "needs_approval")

    def test_no_approve_tool_for_model(self):
        import asyncio
        names = {t.name for t in asyncio.run(host.mcp.list_tools())}
        self.assertNotIn("approve", names)
        self.assertNotIn("execute_approved", names)
        self.assertNotIn("run_powershell", names)
        # freigabe_entscheiden gibt es nur mit dem zweiten Geheimnis; im Kern steht es nie in LLM_TOOLS
        self.assertIn("freigabe_entscheiden", names)


SCHLUESSEL = "freigabe-" + "k" * 40  # Test-Sentinel: darf nie im Audit oder in einer Antwort stehen


class ApprovalTests(unittest.TestCase):
    """Freigabeweg für kritische PC-Aktionen: nur mit dem zweiten Geheimnis, genau einmal, mit Ablaufzeit."""

    def setUp(self):
        host.PENDING.clear()
        host._recent_actions.clear()
        self.audit: list[dict] = []
        for p in (mock.patch.object(host, "FREIGABE_SCHLUESSEL", SCHLUESSEL),
                  mock.patch.object(host, "_audit", side_effect=self.audit.append)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(host.PENDING.clear)
        self.addCleanup(host._recent_actions.clear)

    def _anfrage(self, befehl="Stop-Process -Name notepad", grund="Notepad schließen"):
        with mock.patch.object(host, "_powershell") as run:
            result = host.powershell_lesen(befehl, grund)
        run.assert_not_called()
        self.assertEqual(result["status"], "needs_approval")
        self.assertRegex(result["approval_id"], r"^[0-9a-f]{16}$")
        self.assertEqual(result["aktion"], "powershell")
        self.assertEqual(result["grund"], "PowerShell-Befehl ausführen")  # feste Überschrift, nicht vom Modell
        self.assertEqual(result["detail"], befehl)  # genau das, was ausgeführt wird
        self.assertEqual(result["anlass"], grund)  # Anlass des Modells getrennt davon
        self.assertEqual(result["gueltig_s"], host.FREIGABE_GUELTIG_SECS)
        return result["approval_id"]

    def test_ja_fuehrt_genau_einmal_aus(self):
        aid = self._anfrage()
        with mock.patch.object(host, "_powershell", return_value={"status": "success", "stdout": "ok"}) as run:
            erstes = host.entscheiden(aid, True, SCHLUESSEL)
            zweites = host.entscheiden(aid, True, SCHLUESSEL)
        run.assert_called_once_with("Stop-Process -Name notepad", timeout=host.FREIGABE_TIMEOUT_SECS)
        self.assertEqual(erstes["status"], "success")
        self.assertEqual(zweites["status"], "error")
        self.assertIn("Unbekannte oder abgelaufene", zweites["summary"])

    def test_nein_fuehrt_nichts_aus(self):
        aid = self._anfrage()
        with mock.patch.object(host, "_powershell") as run:
            nein = host.entscheiden(aid, False, SCHLUESSEL)
            danach = host.entscheiden(aid, True, SCHLUESSEL)
        run.assert_not_called()
        self.assertEqual(nein["status"], "rejected")
        self.assertEqual(danach["status"], "error")

    def test_ohne_oder_mit_falschem_schluessel_passiert_nichts(self):
        aid = self._anfrage()
        with mock.patch.object(host, "_powershell") as run:
            for schluessel in ("", "falsch", None, 123, SCHLUESSEL[:-1] + "x", SCHLUESSEL + "x", "ä" * 49):
                with self.subTest(schluessel=str(schluessel)[:12]):
                    self.assertEqual(host.entscheiden(aid, True, schluessel)["status"], "blocked")
            with mock.patch.object(host, "FREIGABE_SCHLUESSEL", ""):  # Host ohne Schlüssel: nie
                self.assertEqual(host.entscheiden(aid, True, "")["status"], "blocked")
        run.assert_not_called()
        self.assertIn(aid, host.PENDING)  # die Anfrage bleibt offen

    def test_unbekannte_oder_abgelaufene_id(self):
        with mock.patch.object(host, "_powershell") as run:
            for aid in ("deadbeefdeadbeef", "../x", "", None, "DEADBEEFDEADBEEF", "a" * 17):
                with self.subTest(aid=aid):
                    self.assertEqual(host.entscheiden(aid, True, SCHLUESSEL)["status"], "error")
            uhr = [1000.0]
            with mock.patch.object(host, "_jetzt", side_effect=lambda: uhr[0]):
                aid = self._anfrage()
                uhr[0] += host.FREIGABE_GUELTIG_SECS + 1
                self.assertEqual(host.entscheiden(aid, True, SCHLUESSEL)["status"], "error")
            self.assertNotIn(aid, host.PENDING)
        run.assert_not_called()

    def test_nur_ja_oder_nein(self):
        aid = self._anfrage()
        with mock.patch.object(host, "_powershell") as run:
            for ja in ("ja", 1, None):
                self.assertEqual(host.entscheiden(aid, ja, SCHLUESSEL)["status"], "error")
        run.assert_not_called()
        self.assertIn(aid, host.PENDING)

    def test_schluessel_nie_im_audit_oder_in_antworten(self):
        aid = self._anfrage()
        with mock.patch.object(host, "_powershell", return_value={"status": "success"}):
            antworten = [host.entscheiden(aid, True, "falsch"), host.entscheiden(aid, True, SCHLUESSEL)]
        self.assertTrue(any(e.get("tool") == "freigabe_entscheiden" for e in self.audit))
        self.assertNotIn(SCHLUESSEL, json.dumps(self.audit, ensure_ascii=False) + json.dumps(antworten))

    def test_schluessel_wird_aus_der_umgebung_entfernt(self):
        with mock.patch.dict(os.environ, {"WAMO_HOST_FREIGABE": SCHLUESSEL}):
            self.assertEqual(host._lade_freigabe_schluessel(), SCHLUESSEL)
            self.assertNotIn("WAMO_HOST_FREIGABE", os.environ)  # Kindprozesse (PowerShell) erben ihn nicht
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            datei = Path(tmp) / "host-freigabe"
            datei.write_text(SCHLUESSEL + "\n", encoding="ascii")
            with mock.patch.dict(os.environ, {"WAMO_HOST_FREIGABE_DATEI": str(datei)}):
                self.assertEqual(host._lade_freigabe_schluessel(), SCHLUESSEL)
            datei.write_text("zu-kurz", encoding="ascii")
            with mock.patch.dict(os.environ, {"WAMO_HOST_FREIGABE_DATEI": str(datei)}):
                self.assertEqual(host._lade_freigabe_schluessel(), "")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WAMO_HOST_FREIGABE", None)
            os.environ.pop("WAMO_HOST_FREIGABE_DATEI", None)
            self.assertEqual(host._lade_freigabe_schluessel(), "")

    def test_offene_anfragen_begrenzt(self):
        with mock.patch.object(host, "_powershell"):
            for i in range(host.MAX_PENDING + 5):
                host.powershell_lesen(f"Stop-Process -Id {i}", "Test")
        self.assertEqual(len(host.PENDING), host.MAX_PENDING)

    # --- Senden in Mail- und Messenger-Fenstern ---
    TEAMS = {"hwnd": 42, "titel": "Chat | Microsoft Teams", "programm": "ms-teams.exe"}

    def _senden_anfrage(self):
        with mock.patch.object(host.wc, "foreground", return_value=dict(self.TEAMS)), \
             mock.patch.object(host.wc, "type_text") as typed:
            result = host.tastatur_tippen("Bin gleich da\n")
        typed.assert_not_called()
        self.assertEqual(result["status"], "needs_approval")
        self.assertEqual(result["aktion"], "senden")
        self.assertEqual(result["detail"], "Bin gleich da\n")
        self.assertIn("Chat | Microsoft Teams", result["ziel"])
        self.assertIn("ms-teams.exe", result["ziel"])
        return result["approval_id"]

    def test_senden_nach_freigabe_genau_einmal_ins_selbe_fenster(self):
        aid = self._senden_anfrage()
        with mock.patch.object(host.wc, "window_info", return_value=dict(self.TEAMS)), \
             mock.patch.object(host.wc, "focus") as focus, \
             mock.patch.object(host.wc, "foreground", return_value=dict(self.TEAMS)), \
             mock.patch.object(host.wc, "type_text") as typed:
            erstes = host.entscheiden(aid, True, SCHLUESSEL)
            zweites = host.entscheiden(aid, True, SCHLUESSEL)
        self.assertEqual(erstes["status"], "success", erstes)
        self.assertEqual(zweites["status"], "error")
        focus.assert_called_once_with(42)
        typed.assert_called_once()
        self.assertEqual(typed.call_args.args[0], "Bin gleich da\n")

    def test_senden_in_anderes_fenster_wird_nicht_ausgefuehrt(self):
        aid = self._senden_anfrage()
        fremd = {"hwnd": 42, "titel": "Bank", "programm": "chrome.exe"}  # Handle neu vergeben
        with mock.patch.object(host.wc, "window_info", return_value=fremd), \
             mock.patch.object(host.wc, "focus") as focus, \
             mock.patch.object(host.wc, "type_text") as typed:
            result = host.entscheiden(aid, True, SCHLUESSEL)
        self.assertEqual(result["status"], "error")
        focus.assert_not_called()
        typed.assert_not_called()

    def test_senden_bei_gesperrtem_bildschirm_bleibt_offen(self):
        aid = self._senden_anfrage()
        with mock.patch.object(host.wc, "is_locked", return_value=True), \
             mock.patch.object(host.wc, "type_text") as typed:
            self.assertEqual(host.entscheiden(aid, True, SCHLUESSEL)["status"], "locked")
        typed.assert_not_called()
        self.assertIn(aid, host.PENDING)

    def test_tasten_senden_nach_freigabe(self):
        outlook = {"hwnd": 7, "titel": "Neue Nachricht - Outlook", "programm": "olk.exe"}
        with mock.patch.object(host.wc, "foreground", return_value=dict(outlook)), \
             mock.patch.object(host.wc, "press") as press:
            result = host.tasten_druecken("ctrl+enter")
            press.assert_not_called()
            self.assertEqual((result["status"], result["aktion"]), ("needs_approval", "senden"))
            with mock.patch.object(host.wc, "window_info", return_value=dict(outlook)), \
                 mock.patch.object(host.wc, "focus"):
                self.assertEqual(host.entscheiden(result["approval_id"], True, SCHLUESSEL)["status"], "success")
        press.assert_called_once_with([0x11, 0x0D])

    def test_freigabe_nur_an_dasselbe_gespraech_gebunden(self):
        # Review: gleiches Fenster, gleiches Programm, aber inzwischen ein anderer Chat (Titel) -> nicht senden
        aid = self._senden_anfrage()
        anderer_chat = {"hwnd": 42, "titel": "Chat mit Bank | Microsoft Teams", "programm": "ms-teams.exe"}
        with mock.patch.object(host.wc, "window_info", return_value=anderer_chat), \
             mock.patch.object(host.wc, "focus") as focus, mock.patch.object(host.wc, "type_text") as typed:
            self.assertEqual(host.entscheiden(aid, True, SCHLUESSEL)["status"], "error")
        focus.assert_not_called()
        typed.assert_not_called()

    def test_eingabe_anfragen_verfallen_schneller(self):
        # Review: eine Anfrage legt Eingaben lahm; für Senden und Klicks deshalb kürzer als für Befehle
        uhr = [1000.0]
        with mock.patch.object(host, "_jetzt", side_effect=lambda: uhr[0]):
            aid = self._senden_anfrage()
            self.assertEqual(host.PENDING[aid]["frist"], host.FREIGABE_EINGABE_SECS)
            uhr[0] += host.FREIGABE_EINGABE_SECS + 1
            self.assertEqual(host.entscheiden(aid, True, SCHLUESSEL)["status"], "error")
        self.assertLess(host.FREIGABE_EINGABE_SECS, host.FREIGABE_GUELTIG_SECS)

    def test_ungelesen_marker_im_titel_stoeren_nicht(self):
        for titel in ("* Chat | Microsoft Teams", "• Chat | Microsoft Teams", "[2] Chat | Microsoft Teams"):
            host.PENDING.clear()
            with self.subTest(titel=titel):
                aid = self._senden_anfrage()
                mit = {**self.TEAMS, "titel": titel}
                with mock.patch.object(host.wc, "window_info", return_value=mit), mock.patch.object(host.wc, "focus"), \
                     mock.patch.object(host.wc, "foreground", return_value=mit), mock.patch.object(host.wc, "type_text"):
                    self.assertEqual(host.entscheiden(aid, True, SCHLUESSEL)["status"], "success")

    def test_zaehler_im_titel_stoert_nicht(self):
        aid = self._senden_anfrage()
        mit_zaehler = {**self.TEAMS, "titel": "(3) Chat | Microsoft Teams"}
        with mock.patch.object(host.wc, "window_info", return_value=mit_zaehler), mock.patch.object(host.wc, "focus"), \
             mock.patch.object(host.wc, "foreground", return_value=mit_zaehler), \
             mock.patch.object(host.wc, "type_text") as typed:
            self.assertEqual(host.entscheiden(aid, True, SCHLUESSEL)["status"], "success")
        typed.assert_called_once()

    def test_eingaben_ruhen_solange_eine_freigabe_offen_ist(self):
        # Review (Selbstfreigabe): solange eine Anfrage offen ist, kann das Modell die Karte nicht anklicken oder
        # per Tastatur bedienen
        aid = self._anfrage()
        fenster = {"hwnd": 9, "titel": "Notiz", "programm": "notepad.exe"}
        with mock.patch.object(host.wc, "foreground", return_value=fenster), \
             mock.patch.object(host.wc, "find_window", return_value=fenster), \
             mock.patch.object(host.wc, "window_at", return_value=fenster), \
             mock.patch.object(host.wc, "screen_bounds", return_value=(0, 0, 100, 100)), \
             mock.patch.object(host.wc, "_win32") as win32, \
             mock.patch.object(host.wc, "press") as press, mock.patch.object(host.wc, "type_text") as typed, \
             mock.patch.object(host.wc, "click") as click, mock.patch.object(host.wc, "focus") as focus, \
             mock.patch.object(host.wc, "scroll") as scroll:
            win32.return_value[0].GetWindowRect.return_value = (0, 0, 10, 10)
            for aufruf in (lambda: host.tasten_druecken("tab"), lambda: host.tastatur_tippen("x"),
                           lambda: host.maus_klicken(5, 5), lambda: host.fenster_fokussieren("Notiz"),
                           lambda: host.maus_scrollen("runter", 1)):
                self.assertEqual(aufruf()["status"], "wartet")
            for effekt in (press, typed, click, focus, scroll):
                effekt.assert_not_called()
            host.entscheiden(aid, False, SCHLUESSEL)
            self.assertEqual(host.tasten_druecken("tab")["status"], "success")  # danach wieder frei
        press.assert_called_once()

    def test_klick_in_messenger_braucht_freigabe(self):
        teams = dict(self.TEAMS)
        with mock.patch.object(host.wc, "window_at", return_value=teams), \
             mock.patch.object(host.wc, "screen_bounds", return_value=(0, 0, 1000, 1000)), \
             mock.patch.object(host.wc, "click") as click:
            result = host.maus_klicken(500, 600, beschreibung="Senden-Knopf")
            click.assert_not_called()
            self.assertEqual((result["status"], result["aktion"]), ("needs_approval", "klicken"))
            self.assertIn("Senden-Knopf", result["detail"])
            ok = host.entscheiden(result["approval_id"], True, SCHLUESSEL)
        self.assertEqual(ok["status"], "success", ok)
        click.assert_called_once_with(500, 600, "links", False)

    def test_zeilenenden_und_tabs_senden_nicht_heimlich(self):
        with mock.patch.object(host.wc, "foreground", return_value=dict(self.TEAMS)), \
             mock.patch.object(host.wc, "type_text") as typed:
            for text in ("Hallo\rJonas", "Hallo Jonas", "Hallo\x0bJonas", "Hallo\r\n"):
                host.PENDING.clear()
                with self.subTest(text=repr(text)):
                    self.assertEqual(host.tastatur_tippen(text)["status"], "needs_approval")
            host.PENDING.clear()
            self.assertEqual(host.tastatur_tippen("Hallo\tJonas")["status"], "rejected")
        typed.assert_not_called()

    def test_zu_lange_anfrage_wird_abgelehnt(self):
        with mock.patch.object(host.wc, "foreground", return_value=dict(self.TEAMS)), \
             mock.patch.object(host.wc, "type_text") as typed:
            result = host.tastatur_tippen("a\n" * 950)  # 1900 Zeichen, angezeigt mit ⏎ über der Kartengrenze
        typed.assert_not_called()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(host.PENDING, {})

    def test_gleiche_anfrage_wird_nicht_verdoppelt(self):
        erste, zweite = self._anfrage(), self._anfrage()
        self.assertEqual(erste, zweite)
        self.assertEqual(len(host.PENDING), 1)

    def test_volle_warteschlange_lehnt_neue_ab_statt_alte_zu_verdraengen(self):
        ids = [self._anfrage(f"Stop-Process -Id {i}") for i in range(host.MAX_PENDING)]
        with mock.patch.object(host, "_powershell") as run:
            result = host.powershell_lesen("Stop-Process -Id 999", "noch eine")
        run.assert_not_called()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(list(host.PENDING), ids)  # die erste Anfrage ist noch da

    def test_audit_nennt_was_freigegeben_wurde(self):
        aid = self._anfrage("Stop-Process -Name notepad")
        with mock.patch.object(host, "_powershell", return_value={"status": "success"}):
            host.entscheiden(aid, True, SCHLUESSEL)
        ausgefuehrt = [e for e in self.audit if e.get("tool") == "freigabe_entscheiden" and e.get("entscheidung") == "ja"]
        self.assertEqual(ausgefuehrt[0]["befehl"], "Stop-Process -Name notepad")
        aid = self._senden_anfrage()
        anfrage = [e for e in self.audit if e.get("approval_id") == aid and e.get("risk") == "critical"][0]
        self.assertEqual(anfrage["zeichen"], len("Bin gleich da\n"))
        self.assertNotIn("Bin gleich da", json.dumps(anfrage, ensure_ascii=False))  # nur Länge und Prüfsumme

    def test_gesperrter_bildschirm_haelt_jede_freigabe_an(self):
        aid = self._anfrage()
        with mock.patch.object(host.wc, "is_locked", return_value=True), mock.patch.object(host, "_powershell") as run:
            self.assertEqual(host.entscheiden(aid, True, SCHLUESSEL)["status"], "locked")
        run.assert_not_called()
        self.assertIn(aid, host.PENDING)

    def test_freigabe_werkzeug_laeuft_ueber_mcp_im_thread(self):
        import asyncio
        aid = self._anfrage()
        with mock.patch.object(host, "_powershell", return_value={"status": "success"}) as run:
            result = asyncio.run(host.freigabe_entscheiden(aid, True, SCHLUESSEL))
        self.assertEqual(result["status"], "success")
        run.assert_called_once()

    def test_lange_suchanfrage_nach_freigabe(self):
        url = "https://www.example.com/s?q=" + "a" * 250
        with mock.patch.object(host, "_launch") as launch, mock.patch.object(host, "resolve_app", return_value="c.exe"):
            result = host.webseite_oeffnen(url)
            launch.assert_not_called()
            self.assertEqual((result["status"], result["aktion"], result["detail"]), ("needs_approval", "webseite", url))
            self.assertEqual(host.entscheiden(result["approval_id"], True, SCHLUESSEL)["status"], "success")
        launch.assert_called_once_with("c.exe", [url])


class KeyboardAndWindowTests(unittest.TestCase):
    def test_combos(self):
        self.assertEqual(host.wc.parse_combo("ctrl+t"), [0x11, ord("T")])
        self.assertEqual(host.wc.parse_combo("Enter"), [0x0D])
        for bad in ["win+r", "windows+x", "ctrl+alt", "", "ctrl+t+w", "rm -rf", None, "x" * 50, "ctrl+esc",
                    "strg+escape"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                host.wc.parse_combo(bad)

    def test_blocked_targets(self):
        for exe, title in [("powershell.exe", "Windows PowerShell"), ("cmd.exe", "x"),
                           ("WindowsTerminal.exe", "x"), ("regedit.exe", "Registrierungs-Editor"),
                           ("consent.exe", "x"), ("chrome.exe", "Anmelden – Google-Konten"),
                           ("explorer.exe", "Ausführen"), ("code.exe", "Administrator: x"),
                           ("", "Unbekannt"), ("bash.exe", "x"), ("wsl.exe", "x"), ("mintty.exe", "x"),
                           ("SearchHost.exe", "Suche"), ("StartMenuExperienceHost.exe", "Start")]:
            with self.subTest(exe=exe, title=title), self.assertRaises(ValueError):
                host.wc.check_target(exe, title)
        host.wc.check_target("WhatsApp.exe", "WhatsApp")  # erlaubt
        host.wc.check_target("Code.exe", "wamo - Visual Studio Code")  # erlaubt

    def test_start_app_pick(self):
        apps = [{"Name": "WhatsApp", "AppID": "wa"}, {"Name": "Windows PowerShell", "AppID": "ps"},
                {"Name": "Visual Studio Code", "AppID": "code"}, {"Name": "Registrierungs-Editor", "AppID": "reg"}]
        self.assertEqual(host.wc.pick_start_app("whatsapp", apps)["AppID"], "wa")
        self.assertEqual(host.wc.pick_start_app("visual studio", apps)["AppID"], "code")
        for bad in ["powershell", "registrierungs-editor", "gibtsnicht", ""]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                host.wc.pick_start_app(bad, apps)

    def test_typing_into_console_never_types(self):
        with mock.patch.object(host.wc, "foreground", return_value={"hwnd": 1, "titel": "PowerShell", "programm": "powershell.exe"}), \
             mock.patch.object(host.wc, "type_text") as typed:
            result = host.tastatur_tippen("Remove-Item C:\\ -Recurse\n")
        typed.assert_not_called()
        self.assertEqual(result["status"], "rejected")


class OpenWithTests(unittest.TestCase):
    def test_paths(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=host.HOME) as tmp:
            ok = Path(tmp)
            self.assertEqual(host.validate_local_path(str(ok)), ok.resolve())
            (ok / ".ssh").mkdir()
            for bad in ["", None, "Projekte\\radar", "C:\\Windows\\System32", "C:\\",
                        str(ok / ".ssh"), str(ok / "gibtsnicht"), str(host.HOME / ".." / ".." / "Windows")]:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    host.validate_local_path(bad)

    def test_executables_not_started_via_explorer(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=host.HOME) as tmp:
            for name in ["setup.exe", "run.bat", "link.lnk", "x.ps1"]:
                f = Path(tmp) / name
                f.write_text("x", encoding="utf-8")
                with self.subTest(name=name), mock.patch.object(host, "_launch") as launch:
                    result = host.oeffnen_mit(str(f), "explorer")
                    launch.assert_not_called()
                    self.assertEqual(result["status"], "rejected")

    def test_no_terminal_app(self):
        self.assertNotIn("terminal", host.APPS)

    def test_open_allowlist(self):
        """M7: Ordner und harmlose Dateitypen; der Explorer nur Dokumente und Bilder."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=host.HOME) as tmp:
            ok = {("notiz.txt", "explorer"), ("bericht.pdf", "explorer"), ("tabelle.xlsx", "explorer"),
                  ("app.py", "vscode"), ("Dockerfile", "vscode"), ("liesmich.md", "notepad")}
            bad = {("app.py", "explorer"), ("skript.js", "explorer"), ("geheim.env", "vscode"),
                   ("setup.exe", "vscode"), ("seite.html", "explorer"), ("x.svg", "explorer")}
            for name, app in ok | bad:
                (Path(tmp) / name).write_text("x", encoding="utf-8")
            for name, app in ok:
                with self.subTest(name=name, app=app), mock.patch.object(host, "_launch") as launch, \
                     mock.patch.object(host, "resolve_app", return_value="prog.exe"):
                    self.assertEqual(host.oeffnen_mit(str(Path(tmp) / name), app)["status"], "success")
                    launch.assert_called_once()
                host._recent_actions.clear()
            for name, app in bad:
                with self.subTest(name=name, app=app), mock.patch.object(host, "_launch") as launch:
                    self.assertEqual(host.oeffnen_mit(str(Path(tmp) / name), app)["status"], "rejected")
                    launch.assert_not_called()
            with mock.patch.object(host, "_launch"), mock.patch.object(host, "resolve_app", return_value="c.exe"):
                self.assertEqual(host.oeffnen_mit(tmp, "vscode")["status"], "success")
            host._recent_actions.clear()

    def test_rejected_path_launches_nothing(self):
        with mock.patch.object(host, "_launch") as launch:
            result = host.oeffnen_mit("C:\\Windows\\System32", "explorer")
        launch.assert_not_called()
        self.assertEqual(result["status"], "rejected")


class DuplicateTests(unittest.TestCase):
    def test_same_action_twice_runs_once(self):
        host._recent_actions.clear()
        with mock.patch.object(host, "_launch") as launch,              mock.patch.object(host, "resolve_app", return_value="chrome.exe"),              mock.patch.object(host.wc, "list_windows", return_value=[]):
            first = host.webseite_oeffnen("https://www.amazon.de/s?k=e-bike")
            second = host.webseite_oeffnen("https://www.amazon.de/s?k=e-bike")
            third = host.webseite_oeffnen("https://www.amazon.de/s?k=lampe")
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(third["status"], "success")
        self.assertEqual(launch.call_count, 2)

    def tearDown(self):
        host._recent_actions.clear()  # andere Tests duerfen dieselben Aktionen ausfuehren


class InputLockTests(unittest.TestCase):
    """M9 und L8: Adressleiste, Explorer, Senden in Mail/Messenger, Zielprüfung beim Tippen."""

    def tearDown(self):
        host._recent_actions.clear()

    def fg(self, exe, title, hwnd=1):
        return {"hwnd": hwnd, "titel": title, "programm": exe}

    def test_address_bar_combos_blocked(self):
        for combo in ["ctrl+l", "strg+l", "alt+d", "f6"]:
            with self.subTest(combo=combo), self.assertRaises(ValueError):
                host.wc.parse_combo(combo)
        with mock.patch.object(host.wc, "foreground", return_value=self.fg("chrome.exe", "Google")), \
             mock.patch.object(host.wc, "press") as press:
            self.assertEqual(host.tasten_druecken("ctrl+l")["status"], "rejected")
        press.assert_not_called()

    def test_no_typing_in_explorer(self):
        with mock.patch.object(host.wc, "foreground", return_value=self.fg("explorer.exe", "Dokumente")), \
             mock.patch.object(host.wc, "type_text") as typed:
            self.assertEqual(host.tastatur_tippen("powershell\n")["status"], "rejected")
        typed.assert_not_called()
        host.wc.check_target("explorer.exe", "Dokumente")  # Klicks im Explorer bleiben möglich

    def test_send_in_messaging_needs_approval(self):
        for exe, title in [("OUTLOOK.EXE", "Posteingang - Outlook"), ("ms-teams.exe", "Chat | Microsoft Teams"),
                           ("WhatsApp.exe", "WhatsApp"), ("slack.exe", "Slack"), ("chrome.exe", "Gmail - E-Mail")]:
            for combo in ["enter", "ctrl+enter", "alt+s"]:
                host.PENDING.clear()  # offene Anfragen lassen Eingaben ruhen
                with self.subTest(exe=exe, combo=combo), \
                     mock.patch.object(host.wc, "foreground", return_value=self.fg(exe, title)), \
                     mock.patch.object(host.wc, "press") as press:
                    self.assertEqual(host.tasten_druecken(combo)["status"], "needs_approval")
                    press.assert_not_called()
            host.PENDING.clear()
            with self.subTest(exe=exe, text="mit Zeilenumbruch"), \
                 mock.patch.object(host.wc, "foreground", return_value=self.fg(exe, title)), \
                 mock.patch.object(host.wc, "type_text") as typed:
                self.assertEqual(host.tastatur_tippen("Hallo zusammen\n")["status"], "needs_approval")
                typed.assert_not_called()
        host.PENDING.clear()
        with mock.patch.object(host.wc, "foreground", return_value=self.fg("ms-teams.exe", "Teams")), \
             mock.patch.object(host.wc, "type_text") as typed:
            self.assertEqual(host.tastatur_tippen("Hallo ohne Senden")["status"], "success")
        typed.assert_called_once()
        with mock.patch.object(host.wc, "foreground", return_value=self.fg("notepad.exe", "Unbenannt")), \
             mock.patch.object(host.wc, "press") as press:
            self.assertEqual(host.tasten_druecken("enter")["status"], "success")  # kein Messenger

    def test_type_text_rechecks_every_100_chars(self):
        calls = []

        def recheck():
            calls.append(1)
            if len(calls) == 3:
                raise ValueError("Das Zielfenster hat gewechselt")

        with mock.patch.object(host.wc, "_send") as send, mock.patch.object(host.wc.time, "sleep"):
            with self.assertRaises(ValueError):
                host.wc.type_text("x" * 450, recheck=recheck)
        self.assertEqual(len(calls), 3)
        self.assertEqual(send.call_count, 200)  # nach 200 Zeichen abgebrochen

    def test_typing_stops_when_window_changes(self):
        windows = iter([self.fg("notepad.exe", "Notiz", 1), self.fg("notepad.exe", "Notiz", 1),
                        self.fg("chrome.exe", "Bank", 2)])
        with mock.patch.object(host.wc, "foreground", side_effect=lambda: next(windows)), \
             mock.patch.object(host.wc, "_send"), mock.patch.object(host.wc.time, "sleep"):
            result = host.tastatur_tippen("y" * 250)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("gewechselt", result["summary"])

    def test_emoji_as_surrogate_pair(self):
        with mock.patch.object(host.wc, "_send") as send, mock.patch.object(host.wc.time, "sleep"):
            host.wc.type_text("a😀")
        self.assertEqual(send.call_count, 3)  # a + zwei UTF-16-Einheiten


class LockTests(unittest.TestCase):
    """Gesperrter Bildschirm: Eingaben, Klicks und Fotos laufen nie ins Leere, sondern melden status=locked."""

    def setUp(self):
        self.audit: list[dict] = []
        patches = [
            mock.patch.object(host.wc, "is_locked", return_value=True),
            mock.patch.object(host, "_audit", side_effect=self.audit.append),
            mock.patch.object(host.wc, "foreground", return_value={"hwnd": 1, "titel": "Notiz", "programm": "notepad.exe"}),
            mock.patch.object(host.wc, "find_window", return_value={"hwnd": 1, "titel": "Notiz", "programm": "notepad.exe"}),
        ]
        effekte = {name: mock.patch.object(host.wc, name) for name in
                   ("focus", "type_text", "press", "screenshot", "click", "scroll", "_win32")}
        self.mocks = {name: p.start() for name, p in effekte.items()}
        for p in patches:
            p.start()
        for p in [*patches, *effekte.values()]:
            self.addCleanup(p.stop)  # nicht stopall: das beendete auch die Modul-Patches

    def test_eingaben_klicks_und_fotos_melden_gesperrt(self):
        aufrufe = {
            "fenster_fokussieren": lambda: host.fenster_fokussieren("Notiz"),
            "tastatur_tippen": lambda: host.tastatur_tippen("Hallo"),
            "tasten_druecken": lambda: host.tasten_druecken("ctrl+t"),
            "bildschirmfoto": lambda: host.bildschirmfoto(0),
            "maus_klicken": lambda: host.maus_klicken(10, 10),
            "maus_scrollen": lambda: host.maus_scrollen("runter", 3),
        }
        for name, aufruf in aufrufe.items():
            with self.subTest(werkzeug=name):
                ergebnis = aufruf()
                self.assertEqual(ergebnis["status"], "locked")
                self.assertIn("gesperrt", ergebnis["summary"])
                self.assertIn({"tool": name, "risk": "locked"}, [{k: e.get(k) for k in ("tool", "risk")} for e in self.audit])
        for name, effekt in self.mocks.items():
            effekt.assert_not_called()  # keine Eingabe, kein Foto, kein Klick, kein Fensterwechsel

    def test_system_info_meldet_sperre(self):
        self.assertIs(host.system_info()["gesperrt"], True)
        with mock.patch.object(host.wc, "is_locked", return_value=False):
            self.assertIs(host.system_info()["gesperrt"], False)

    def test_nicht_pruefbar_gilt_als_gesperrt(self):
        with mock.patch.object(host.wc, "is_locked", side_effect=OSError("kein Desktop")):
            self.assertEqual(host.tastatur_tippen("Hallo")["status"], "locked")
            self.assertIs(host.system_info()["gesperrt"], True)
        self.mocks["type_text"].assert_not_called()

    def test_sperr_probe_protokolliert_nur_wechsel(self):
        host._letzte_sperre = None
        for gesperrt in (True, True, True, False, False, True):
            with mock.patch.object(host.wc, "is_locked", return_value=gesperrt):
                self.assertEqual(host.sperre_pruefen(), {"status": "success", "gesperrt": gesperrt})
        wechsel = [e["gesperrt"] for e in self.audit if e["tool"] == "sperre_pruefen"]
        self.assertEqual(wechsel, [True, False, True])


class SperreWeitereWerkzeugeTests(unittest.TestCase):
    """Review: am gesperrten PC auch keine Programme, Webseiten, Ordner und kein Dark Mode."""

    def test_aktionen_am_gesperrten_pc(self):
        with mock.patch.object(host.wc, "is_locked", return_value=True), \
             mock.patch.object(host, "_launch") as launch, mock.patch.object(host, "_powershell") as ps, \
             mock.patch.object(host.wc, "launch_start_app") as start, \
             mock.patch.object(host.wc, "list_windows") as fenster:
            for aufruf in (lambda: host.webseite_oeffnen("https://wamocon.de/"), lambda: host.programm_oeffnen("notepad"),
                           lambda: host.oeffnen_mit(str(host.HOME), "explorer"), lambda: host.dark_mode(True),
                           lambda: host.powershell_lesen("Get-Date", "Uhrzeit"), lambda: host.fenster_auflisten()):
                self.assertEqual(aufruf()["status"], "locked")
            # Nur die Basisinfos (mit dem Stand der Sperre) und die Probe bleiben
            self.assertEqual(host.system_info()["status"], "success")
            self.assertEqual(host.sperre_pruefen()["gesperrt"], True)
        fenster.assert_not_called()
        launch.assert_not_called()
        ps.assert_not_called()
        start.assert_not_called()


class WamoOberflaecheTests(unittest.TestCase):
    """Review (Selbstfreigabe): WAMO tippt, drückt, klickt und fokussiert nie in der eigenen Oberfläche."""

    def tearDown(self):
        host._recent_actions.clear()

    def test_eigene_oberflaeche_ist_gesperrt(self):
        for exe, titel in [("electron.exe", "WAMO"), ("chrome.exe", "WAMO - Google Chrome"),
                           ("msedge.exe", "WAMO und 2 weitere Seiten - Persönlich – Microsoft Edge"),
                           ("chrome.exe", "WAMO"), ("chrome.exe", "W.A.I.S. - Google Chrome"),
                           ("msedge.exe", "W.A.I.S. und 2 weitere Seiten - Persönlich – Microsoft Edge"),
                           ("chrome.exe", "Einstellungen | W.A.I.S.")]:
            with self.subTest(exe=exe, titel=titel), self.assertRaises(ValueError):
                host.wc.check_input_target(exe, titel)
        for exe, titel in [("Code.exe", "wamo - Visual Studio Code"), ("WINWORD.EXE", "WAMO-Konzept.docx - Word"),
                           ("Code.exe", "WAMO.md - wamo - Visual Studio Code"),
                           ("WINWORD.EXE", "W.A.I.S.-Konzept.docx - Word")]:
            with self.subTest(exe=exe, titel=titel):
                host.wc.check_input_target(exe, titel)  # andere Fenster mit "wamo" im Namen bleiben erlaubt
        host.wc.check_target("electron.exe", "WAMO")  # ansehen (Bildschirmfoto) bleibt erlaubt

    def test_tasten_und_klicks_in_der_oberflaeche_laufen_nicht(self):
        ui = {"hwnd": 3, "titel": "WAMO", "programm": "electron.exe"}
        with mock.patch.object(host.wc, "foreground", return_value=ui), mock.patch.object(host.wc, "find_window", return_value=ui), \
             mock.patch.object(host.wc, "window_at", return_value=ui), \
             mock.patch.object(host.wc, "screen_bounds", return_value=(0, 0, 100, 100)), \
             mock.patch.object(host.wc, "press") as press, mock.patch.object(host.wc, "click") as click, \
             mock.patch.object(host.wc, "focus") as focus:
            self.assertEqual(host.tasten_druecken("enter")["status"], "rejected")
            self.assertEqual(host.maus_klicken(5, 5)["status"], "rejected")
            self.assertEqual(host.fenster_fokussieren("WAMO")["status"], "rejected")
        press.assert_not_called()
        click.assert_not_called()
        focus.assert_not_called()

    def test_vor_jedem_enter_wird_das_ziel_geprueft(self):
        calls = []
        with mock.patch.object(host.wc, "_send"), mock.patch.object(host.wc.time, "sleep"):
            host.wc.type_text("ab\ncd\n", recheck=lambda: calls.append(1))
        self.assertEqual(len(calls), 3)  # am Anfang und vor beiden Enter

    def test_allgemeine_woerter_im_titel_sind_kein_messenger(self):
        for titel in ("Element - Wikipedia", "Zoom - Bildbearbeitung"):
            with self.subTest(titel=titel):
                self.assertFalse(host.wc.is_messaging("chrome.exe", titel))
        self.assertTrue(host.wc.is_messaging("zoom.exe", "Meeting"))  # das Programm selbst schon

    def test_leertaste_sendet_im_messenger_nur_nach_freigabe(self):
        with mock.patch.object(host.wc, "foreground", return_value={"hwnd": 1, "titel": "Slack", "programm": "slack.exe"}), \
             mock.patch.object(host.wc, "press") as press:
            for kombi in ("space", "ctrl+shift+enter"):
                host.PENDING.clear()
                with self.subTest(kombi=kombi):
                    self.assertEqual(host.tasten_druecken(kombi)["status"], "needs_approval")
        press.assert_not_called()
        host.PENDING.clear()


class IsLockedTests(unittest.TestCase):
    """windows_control.is_locked über OpenInputDesktop (DESKTOP_READOBJECTS) und den Desktopnamen."""

    class Api:
        def __init__(self, handle=7, name="Default", info_ok=True):
            self.handle, self.name, self.info_ok = handle, name, info_ok
            self.geschlossen, self.zugriff = [], None

        def OpenInputDesktop(self, flags, inherit, access):  # noqa: N802 (Windows-API-Name)
            self.zugriff = access
            return self.handle

        def GetUserObjectInformationW(self, handle, index, buf, size, needed):  # noqa: N802
            if self.info_ok:
                buf.value = self.name
            return self.info_ok

        def CloseDesktop(self, handle):  # noqa: N802
            self.geschlossen.append(handle)
            return True

    def test_normaler_desktop_ist_entsperrt(self):
        api = self.Api()
        self.assertFalse(ECHT_IS_LOCKED(api))
        self.assertEqual(api.zugriff, host.wc.DESKTOP_READOBJECTS)
        self.assertEqual(api.geschlossen, [7])  # Handle immer schließen

    def test_sperrbildschirm_und_fehler_sind_gesperrt(self):
        for api in (self.Api(handle=0), self.Api(name="Winlogon"), self.Api(info_ok=False)):
            with self.subTest(handle=api.handle, name=api.name, info=api.info_ok):
                self.assertTrue(ECHT_IS_LOCKED(api))

    def test_sperrbildschirm_lockapp_vorne_ist_gesperrt(self):
        """31.10.2026: LockApp.exe liegt auf dem Desktop "Default"; allein der Desktopname meldete "entsperrt"."""
        for programm, erwartet in (("LockApp.exe", True), ("lockapp.exe", True), ("chrome.exe", False), ("", False)):
            with self.subTest(programm=programm), \
                 mock.patch.object(host.wc, "_desktop_api", return_value=self.Api()), \
                 mock.patch.object(host.wc, "foreground", return_value={"hwnd": 1, "titel": "x", "programm": programm}):
                self.assertIs(ECHT_IS_LOCKED(), erwartet)

    def test_fenster_nicht_lesbar_aendert_den_desktopbefund_nicht(self):
        with mock.patch.object(host.wc, "_desktop_api", return_value=self.Api()), \
             mock.patch.object(host.wc, "foreground", side_effect=OSError):
            self.assertFalse(ECHT_IS_LOCKED())

    @unittest.skipUnless(sys.platform == "win32" and os.environ.get("SESSIONNAME") and _interaktiv(),
                         "nur in einer interaktiven Windows-Sitzung mit Fenster im Vordergrund")
    def test_echter_desktop_dieser_sitzung(self):
        # Echtes Windows: die laufende, entsperrte Sitzung (Vordergrundfenster vorhanden) ist nicht gesperrt
        self.assertIs(ECHT_IS_LOCKED(), False)


class OpenTests(unittest.TestCase):
    def test_long_query_needs_approval(self):
        with mock.patch.object(host, "_launch") as launch, mock.patch.object(host, "resolve_app", return_value="c.exe"):
            result = host.webseite_oeffnen("https://www.example.com/s?q=" + "a" * 250)
        launch.assert_not_called()
        self.assertEqual(result["status"], "needs_approval")

    def test_rejected_url_launches_nothing(self):
        with mock.patch.object(host, "_launch") as launch:
            result = host.webseite_oeffnen("file:///C:/Windows/System32/cmd.exe")
        launch.assert_not_called()
        self.assertEqual(result["status"], "rejected")

    def test_url_passed_as_single_argv(self):
        with mock.patch.object(host, "_launch") as launch, \
             mock.patch.object(host, "resolve_app", return_value="chrome.exe"):
            host.webseite_oeffnen("https://www.amazon.de/s?k=lampe")
        launch.assert_called_once_with("chrome.exe", ["https://www.amazon.de/s?k=lampe"])


class FokusTests(unittest.TestCase):
    """Fenster vorholen ohne Alt-Druck, mit Nachprüfung (windows_control.focus) und die Aufrufer im Host."""

    def _focus(self, vorne, iconic=False):
        """focus() gegen Attrappen: vorne = Liste der Antworten von GetForegroundWindow, Rest bleibt bei der letzten."""
        gui = mock.MagicMock()
        gui.IsIconic.return_value = iconic
        antworten = list(vorne)
        gui.GetForegroundWindow.side_effect = lambda: antworten.pop(0) if len(antworten) > 1 else antworten[0]
        user32, kernel32 = mock.MagicMock(), mock.MagicMock()
        user32.GetWindowThreadProcessId.return_value = 11
        kernel32.GetCurrentThreadId.return_value = 22
        user32.AttachThreadInput.return_value = 1
        con = mock.Mock(SW_RESTORE=9)
        with mock.patch.object(host.wc, "_win32", return_value=(gui, None, con)), \
             mock.patch.object(host.wc, "_thread_apis", return_value=(user32, kernel32)), \
             mock.patch.object(host.wc.time, "sleep"):
            ergebnis = host.wc.focus(42)
        return ergebnis, gui, user32

    @staticmethod
    def _tasten(user32):
        return [c.args[0] for c in user32.keybd_event.call_args_list]

    def test_schon_vorne_tut_nichts(self):
        ok, gui, user32 = self._focus([42])
        self.assertTrue(ok)
        gui.SetForegroundWindow.assert_not_called()
        user32.keybd_event.assert_not_called()  # kein Alt: der Fokus im Fenster bleibt, wo er ist

    def test_vorholen_ueber_eingabewarteschlange_ohne_taste(self):
        # Antworten von GetForegroundWindow: Prüfung "schon vorne", Fenster vorne für die Verbindung, nach dem Vorholen
        ok, gui, user32 = self._focus([7, 7, 42])
        self.assertTrue(ok)
        gui.SetForegroundWindow.assert_called_once_with(42)
        user32.keybd_event.assert_not_called()
        self.assertEqual([c.args for c in user32.AttachThreadInput.call_args_list], [(22, 11, True), (22, 11, False)])

    def test_nie_alt_selbst_im_notfall(self):
        ok, gui, user32 = self._focus([7])  # Windows lässt das Fenster nie nach vorne
        self.assertFalse(ok)
        tasten = self._tasten(user32)
        self.assertEqual(tasten, [0x10, 0x10])  # nur Shift (Druck und Loslassen), nie Alt (0x12)
        self.assertNotIn(0x12, tasten)

    def test_minimiertes_fenster_wird_wiederhergestellt(self):
        ok, gui, user32 = self._focus([7, 7, 42], iconic=True)
        gui.ShowWindow.assert_called_once_with(42, 9)
        self.assertTrue(ok)

    def test_quelltext_hat_kein_alt(self):
        import inspect
        quelle = inspect.getsource(host.wc.focus)
        self.assertNotIn("0x12", quelle)
        self.assertNotIn("VK_MENU", quelle)

    def test_fenster_fokussieren_meldet_verweigerung_statt_erfolg(self):
        w = {"hwnd": 5, "titel": "Steuerung", "programm": "steuerung.exe"}
        audit = []
        with mock.patch.object(host.wc, "find_window", return_value=w), \
             mock.patch.object(host.wc, "focus", return_value=False), \
             mock.patch.object(host, "_audit", side_effect=audit.append):
            ergebnis = host.fenster_fokussieren("Steuerung")
        self.assertEqual(ergebnis["status"], "rejected")
        self.assertNotIn("ist vorne", ergebnis["summary"])
        self.assertTrue(any(e.get("grund") == "Fokus verweigert" for e in audit))

    def test_fenster_fokussieren_erfolg_nur_bei_nachweis(self):
        w = {"hwnd": 5, "titel": "Notiz", "programm": "notepad.exe"}
        with mock.patch.object(host.wc, "find_window", return_value=w), mock.patch.object(host.wc, "focus", return_value=True):
            self.assertEqual(host.fenster_fokussieren("Notiz")["status"], "success")

    def test_tippen_mit_fenster_bricht_ab_wenn_fokus_verweigert(self):
        w = {"hwnd": 5, "titel": "Notiz", "programm": "notepad.exe"}
        with mock.patch.object(host.wc, "find_window", return_value=w), \
             mock.patch.object(host.wc, "focus", return_value=False), \
             mock.patch.object(host.wc, "foreground", return_value=dict(w)), \
             mock.patch.object(host.wc, "type_text") as typed:
            ergebnis = host.tastatur_tippen("Hallo", fenster="Notiz")
        typed.assert_not_called()
        self.assertEqual(ergebnis["status"], "rejected")

    def test_tippen_mit_fenster_bricht_ab_wenn_anderes_fenster_vorne_liegt(self):
        # focus meldet True, aber vorne liegt ein anderes Fenster: der Text darf dort nicht landen
        w = {"hwnd": 5, "titel": "Notiz", "programm": "notepad.exe"}
        with mock.patch.object(host.wc, "find_window", return_value=w), \
             mock.patch.object(host.wc, "focus", return_value=True), \
             mock.patch.object(host.wc, "foreground", return_value={"hwnd": 1, "titel": "Bank", "programm": "chrome.exe"}), \
             mock.patch.object(host.wc, "type_text") as typed:
            ergebnis = host.tastatur_tippen("Hallo", fenster="Notiz")
        typed.assert_not_called()
        self.assertEqual(ergebnis["status"], "rejected")

    def test_tasten_mit_fenster_bricht_ab_wenn_fokus_verweigert(self):
        w = {"hwnd": 5, "titel": "Notiz", "programm": "notepad.exe"}
        with mock.patch.object(host.wc, "find_window", return_value=w), \
             mock.patch.object(host.wc, "focus", return_value=False), \
             mock.patch.object(host.wc, "foreground", return_value=dict(w)), mock.patch.object(host.wc, "press") as press:
            self.assertEqual(host.tasten_druecken("tab", fenster="Notiz")["status"], "rejected")
        press.assert_not_called()

    def test_freigegebenes_senden_prueft_den_fokus(self):
        # Vor einem freigegebenen Senden muss der Fokus nachweislich im Zielfenster liegen
        teams = {"hwnd": 42, "titel": "Chat | Microsoft Teams", "programm": "ms-teams.exe"}
        host.PENDING.clear()
        with mock.patch.object(host.wc, "foreground", return_value=dict(teams)):
            anfrage = host.tastatur_tippen("Bin gleich da\n")
        with mock.patch.object(host, "FREIGABE_SCHLUESSEL", SCHLUESSEL), \
             mock.patch.object(host.wc, "window_info", return_value=dict(teams)), \
             mock.patch.object(host.wc, "focus", return_value=False), \
             mock.patch.object(host.wc, "foreground", return_value=dict(teams)), \
             mock.patch.object(host.wc, "type_text") as typed:
            ergebnis = host.entscheiden(anfrage["approval_id"], True, SCHLUESSEL)
        typed.assert_not_called()
        self.assertEqual(ergebnis["status"], "error")
        self.assertIn("nicht nach vorne", ergebnis["summary"])


class LesezeitTests(unittest.TestCase):
    def test_lesen_hat_20_sekunden(self):
        self.assertEqual(host.READ_TIMEOUT_SECS, 20)
        with mock.patch.object(host.platform, "system", return_value="Windows"), \
             mock.patch.object(host.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            host.powershell_lesen("Get-Date", "Test")
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_zeitlimit_meldet_klar_und_heisst_nicht_wiederholen(self):
        import subprocess
        with mock.patch.object(host.platform, "system", return_value="Windows"), \
             mock.patch.object(host.subprocess, "run", side_effect=subprocess.TimeoutExpired("powershell", 20)):
            ergebnis = host.powershell_lesen("Get-ChildItem ~/Projekte", "Test")
        self.assertEqual(ergebnis["status"], "error")
        self.assertTrue(ergebnis["zeitlimit"])
        self.assertIn("20 s", ergebnis["summary"])
        self.assertIn("Nicht wiederholen", ergebnis["summary"])

    def test_freigegebene_befehle_behalten_120_sekunden(self):
        self.assertEqual(host.FREIGABE_TIMEOUT_SECS, 120)


class OrdnerAnlegenTests(unittest.TestCase):
    """ordner_anlegen: feste Wurzeln, Verbotsproben, Audit. Läuft in einem Wegwerf-Benutzerordner."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve()
        self.projekte = self.home / "Projekte"
        wurzeln = tuple(self.home / n for n in ("Projekte", "Documents", "Dokumente", "Desktop", "Downloads"))
        for p in (mock.patch.object(host, "HOME", self.home), mock.patch.object(host, "PROJEKTE_DIR", self.projekte),
                  mock.patch.object(host, "ORDNER_WURZELN", wurzeln)):
            p.start()
            self.addCleanup(p.stop)
        self.audit: list[dict] = []
        p = mock.patch.object(host, "_audit", side_effect=self.audit.append)
        p.start()
        self.addCleanup(p.stop)
        host._rate_zeiten.clear()
        self.addCleanup(host._rate_zeiten.clear)

    def test_legt_ordner_unter_projekte_an_mit_audit(self):
        ziel = self.projekte / "radar"
        ergebnis = host.ordner_anlegen(str(ziel))
        self.assertEqual(ergebnis["status"], "success", ergebnis)
        self.assertTrue(ziel.is_dir())
        eintrag = [e for e in self.audit if e.get("tool") == "ordner_anlegen"][-1]
        self.assertEqual((eintrag["risk"], eintrag["pfad"]), ("change", str(ziel)))

    def test_projekte_selbst_und_tiefere_ebenen(self):
        self.assertEqual(host.ordner_anlegen(str(self.projekte))["status"], "success")
        self.assertEqual(host.ordner_anlegen(str(self.projekte / "a" / "b" / "c"))["status"], "success")
        self.assertTrue((self.projekte / "a" / "b" / "c").is_dir())

    def test_vorhandener_ordner_ist_kein_fehler(self):
        (self.projekte / "radar").mkdir(parents=True)
        ergebnis = host.ordner_anlegen(str(self.projekte / "radar"))
        self.assertEqual(ergebnis["status"], "success")
        self.assertIn("gibt es schon", ergebnis["summary"])

    def test_verbotsproben(self):
        nie = [
            None, 42, "", "   ", "x" * 500, "Projekte\\radar", "radar", "~/../../Windows", "Projekte/../radar",
            str(self.home / "AppData" / "Local" / "x"), str(self.home / "AppData"),
            str(self.projekte / ".versteckt"), str(self.projekte / ".ssh"), str(self.projekte / "x" / ".git2" / ".y"),
            str(self.home / ".ssh" / "neu"), str(self.home / ".aws"), str(self.projekte / "credentials"),
            str(self.projekte / "wamo"), str(self.projekte / "wamo" / "neu"),
            str(self.home / "neu"), str(self.home),
            str(self.projekte / ".." / "AppData" / "x"), str(self.projekte / ".." / ".ssh" / "x"),
            "C:\\Windows\\Temp\\x", "C:\\x", "D:\\x", "\\\\server\\share\\x", "//server/share/x",
            str(self.projekte / "CON"), str(self.projekte / "nul.txt"), str(self.projekte / "LPT1"),
            str(self.projekte / "ende."), str(self.projekte / "leer " / "x"),str(self.projekte / "a:b"),
            str(self.projekte / "a<b"), str(self.projekte / "a|b"), str(self.projekte / ("n" * 101)),
            str(self.projekte / "a\x00b"), str(self.projekte / "a\nb"),
            str(self.projekte.joinpath(*["t"] * 7)),
        ]
        for pfad in nie:
            with self.subTest(pfad=repr(pfad)[:60]):
                ergebnis = host.ordner_anlegen(pfad)
                self.assertEqual(ergebnis["status"], "rejected", ergebnis)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [])  # nichts angelegt
        abgelehnt = [e for e in self.audit if e.get("tool") == "ordner_anlegen" and e.get("risk") == "rejected"]
        self.assertEqual(len(abgelehnt), len(nie))

    def test_datei_ist_kein_ordner(self):
        self.projekte.mkdir()
        (self.projekte / "datei").write_text("x")
        self.assertEqual(host.ordner_anlegen(str(self.projekte / "datei"))["status"], "rejected")

    def test_verknuepfung_nach_draussen_wird_nicht_gefolgt(self):
        import subprocess
        self.projekte.mkdir()
        aussen = Path(self._tmp.name).parent / "wamo-ordner-test-aussen"
        aussen.mkdir(exist_ok=True)
        self.addCleanup(lambda: aussen.rmdir() if aussen.exists() else None)
        link = self.projekte / "weg"
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(aussen)], capture_output=True)
        if r.returncode != 0:
            self.skipTest("Verzeichnisverknüpfung nicht möglich")
        self.addCleanup(lambda: subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True))
        ergebnis = host.ordner_anlegen(str(link / "neu"))
        self.assertEqual(ergebnis["status"], "rejected")
        self.assertFalse((aussen / "neu").exists())

    def test_gesperrter_bildschirm_legt_nichts_an(self):
        with mock.patch.object(host.wc, "is_locked", return_value=True):
            ergebnis = host.ordner_anlegen(str(self.projekte / "radar"))
        self.assertEqual(ergebnis["status"], "locked")
        self.assertFalse(self.projekte.exists())

    def test_rate_grenze(self):
        for i in range(host.ORDNER_PRO_MINUTE):
            self.assertEqual(host.ordner_anlegen(str(self.projekte / f"o{i}"))["status"], "success")
        self.assertEqual(host.ordner_anlegen(str(self.projekte / "zuviel"))["status"], "rejected")
        self.assertFalse((self.projekte / "zuviel").exists())

    def test_keine_shell_im_spiel(self):
        with mock.patch.object(host, "_powershell") as ps, mock.patch.object(host.subprocess, "Popen") as popen:
            host.ordner_anlegen(str(self.projekte / "radar"))
        ps.assert_not_called()
        popen.assert_not_called()

    def test_ist_kein_freigabe_werkzeug_und_steht_in_der_toolliste(self):
        import asyncio
        namen = {t.name for t in asyncio.run(host.mcp.list_tools())}
        self.assertIn("ordner_anlegen", namen)
        host.ordner_anlegen(str(self.projekte / "radar"))
        self.assertEqual(host.PENDING, {})

    def test_system_info_nennt_feste_pfade(self):
        info = host.system_info()
        self.assertEqual(info["status"], "success")
        self.assertEqual(info["benutzerordner"], str(self.home))
        self.assertEqual(info["benutzer"], self.home.name)
        self.assertEqual(info["projekte_ordner"], str(self.projekte))
        self.assertFalse(info["projekte_ordner_vorhanden"])
        self.assertEqual(info["rechnername"], host.platform.node())
        self.assertEqual(info["machine"], host.platform.node())
        self.assertIn(str(self.projekte), info["ordner_anlegen_unter"])  # Projekte immer, auch wenn noch nicht da
        self.assertNotIn(str(self.home / "Desktop"), info["ordner_anlegen_unter"])  # gibt es hier nicht
        host.ordner_anlegen(str(self.projekte / "x"))
        self.assertTrue(host.system_info()["projekte_ordner_vorhanden"])


class SystemeigenschaftenTests(unittest.TestCase):
    def test_umgebungsvariablen_und_systemeigenschaften(self):
        for name in ("Systemeigenschaften", "umgebungsvariablen", "Umgebungsvariablen bearbeiten",
                     "systemeigenschaften   öffnen"):
            with self.subTest(name=name), mock.patch.object(host.Path, "is_file", return_value=True):
                self.assertTrue(host.resolve_app(name).lower().endswith("systempropertiesadvanced.exe"))

    def test_zusatzwoerter_gelten_nur_dort(self):
        for name in ("chrome öffnen", "edge calc", "powershell starten", "notepad bitte", "umgebungsvariablen; calc",
                     "umgebungsvariablen & calc", "umgebungsvariablen\ncalc", "systemeigenschaften/../x",
                     "umgebungsvariablenx", "umgebungsvariablen " + "a" * 80):
            with self.subTest(name=name), self.assertRaises(ValueError):
                host.resolve_app(name)

    def test_nicht_installiert_wird_gemeldet(self):
        with mock.patch.object(host.Path, "is_file", return_value=False), \
             mock.patch.object(host.shutil, "which", return_value=None):
            with self.assertRaises(ValueError):
                host.resolve_app("umgebungsvariablen")

    def test_claude_code_findet_app_claude(self):
        apps = [{"Name": "Claude", "AppID": "c"}, {"Name": "Visual Studio Code", "AppID": "v"}]
        self.assertEqual(host.wc.pick_start_app("Claude Code", apps)["AppID"], "c")
        self.assertEqual(host.wc.pick_start_app("claude", apps)["AppID"], "c")
        # eine App mit genau diesem Namen schlägt das Kürzere
        apps.append({"Name": "Claude Code", "AppID": "cc"})
        self.assertEqual(host.wc.pick_start_app("Claude Code", apps)["AppID"], "cc")

    def test_wortabgleich_umgeht_die_sperre_fuer_systemwerkzeuge_nicht(self):
        apps = [{"Name": "Windows PowerShell", "AppID": "ps"}, {"Name": "PowerShell", "AppID": "p"},
                {"Name": "Dienste", "AppID": "d"}]
        for anfrage in ("PowerShell ISE", "Windows PowerShell x64", "Dienste verwalten"):
            with self.subTest(anfrage=anfrage), self.assertRaises(ValueError):
                host.wc.pick_start_app(anfrage, apps)
        with self.assertRaises(ValueError):
            host.wc.pick_start_app("Code Editor", [{"Name": "Kaffee", "AppID": "k"}])


if __name__ == "__main__":
    unittest.main()
