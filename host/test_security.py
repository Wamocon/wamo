"""Sicherheitstests fuer den Host: Validatoren direkt vor der Aktion.

Ausfuehren:  uv run python -m unittest -v test_security.py   (im Ordner host/)
Keiner dieser Tests startet ein Programm oder eine Webseite.
"""

import unittest
from pathlib import Path
from unittest import mock

import wamo_host as host

# DNS in Tests fest vorgeben (kein Netz nötig, Ergebnis immer gleich)
FAKE_DNS = {"127.0.0.1.nip.io": ["127.0.0.1"], "localtest.me": ["127.0.0.1", "::1"],
            "intern.example": ["10.0.0.5"], "nirgends.invalid": []}
_dns_patch = mock.patch.object(host, "_resolve_host", side_effect=lambda h: FAKE_DNS.get(h, ["93.184.216.34"]))


def setUpModule():
    _dns_patch.start()


def tearDownModule():
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
                with self.subTest(exe=exe, combo=combo), \
                     mock.patch.object(host.wc, "foreground", return_value=self.fg(exe, title)), \
                     mock.patch.object(host.wc, "press") as press:
                    self.assertEqual(host.tasten_druecken(combo)["status"], "needs_approval")
                    press.assert_not_called()
            with self.subTest(exe=exe, text="mit Zeilenumbruch"), \
                 mock.patch.object(host.wc, "foreground", return_value=self.fg(exe, title)), \
                 mock.patch.object(host.wc, "type_text") as typed:
                self.assertEqual(host.tastatur_tippen("Hallo zusammen\n")["status"], "needs_approval")
                typed.assert_not_called()
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


if __name__ == "__main__":
    unittest.main()
