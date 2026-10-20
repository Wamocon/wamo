"""Tests für den HTTP-Betrieb des Hosts (Tunnel, Betriebsart Büro): Token-Pflicht vor allem anderen.

Ausführen:  uv run python -m pytest -q   (im Ordner host/)
Die Tests sprechen die echte FastMCP-App über Starlettes TestClient an (kein Netz, kein Port). Gerufen wird nur
system_info (read, startet nichts).
"""

import json
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import host_http
import wamo_host as host

TOKEN = secrets.token_urlsafe(32)
BASIS = "http://127.0.0.1:8765"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
audit_eintraege: list[dict] = []
_client: TestClient | None = None


def _rpc(method: str, params: dict | None = None, rpc_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}


def _init_params() -> dict:
    return {"protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "wamo-voice-core", "version": "test"}}


def setUpModule():
    global _client
    # Der Sitzungsmanager von FastMCP startet nur einmal je Prozess: eine App und ein Client für alle Tests
    app = host_http.baue_app(host.mcp, TOKEN, audit=audit_eintraege.append)
    _client = TestClient(app, base_url=BASIS)
    _client.__enter__()


def tearDownModule():
    if _client is not None:
        _client.__exit__(None, None, None)


def auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TokenDateiTests(unittest.TestCase):
    def _datei(self, inhalt: str) -> str:
        fd, pfad = tempfile.mkstemp()
        os.close(fd)
        Path(pfad).write_text(inhalt, encoding="ascii")
        self.addCleanup(os.remove, pfad)
        return pfad

    def test_gutes_token(self):
        self.assertEqual(host_http.lade_token(self._datei(TOKEN + "\n")), TOKEN)

    def test_fehlende_angabe_oder_datei(self):
        for pfad in (None, "", "   ", str(Path(tempfile.gettempdir()) / "gibt-es-nicht-wamo-token")):
            with self.subTest(pfad=pfad), self.assertRaises(host_http.TokenFehler):
                host_http.lade_token(pfad)

    def test_schwaches_oder_kaputtes_token(self):
        for inhalt in ("kurz", "a" * 42, TOKEN + " x", TOKEN[:-1] + "$", "ä" * 50):
            pfad = self._datei(inhalt) if inhalt.isascii() else self._datei_bytes(inhalt.encode("utf-8"))
            with self.subTest(inhalt=inhalt[:10]), self.assertRaises(host_http.TokenFehler) as ctx:
                host_http.lade_token(pfad)
            self.assertNotIn(inhalt, str(ctx.exception))  # der Inhalt steht nie in der Meldung

    def _datei_bytes(self, inhalt: bytes) -> str:
        fd, pfad = tempfile.mkstemp()
        os.write(fd, inhalt)
        os.close(fd)
        self.addCleanup(os.remove, pfad)
        return pfad

    def test_waechter_lehnt_schwaches_token_ab(self):
        with self.assertRaises(host_http.TokenFehler):
            host_http.TokenWaechter(app=None, token="zu-kurz")


class TokenOrdnerTests(unittest.TestCase):
    def test_token_ordner_ist_fuer_werkzeuge_gesperrt(self):
        ordner = host.HOME / "AppData" / "Local" / "WAMO"
        # Öffnen (Notepad, Explorer) und Lesen per PowerShell gehen beide nicht
        with mock.patch.object(Path, "exists", return_value=True), self.assertRaises(ValueError):
            host.validate_local_path(str(ordner / "host-token"))
        self.assertNotEqual(host.classify(f"Get-Content '{ordner / 'host-token'}'"), "read")

    def test_platzhalter_umgehen_die_sperrliste_nicht(self):
        # Review 19.10.2026: mit * passte der Klarname nicht mehr, der Befehl lief als "read"
        for befehl in (
            f"Get-Content '{host.HOME}\\AppData\\Local\\WAM*\\host-token'",
            f"Get-Content '{host.HOME}\\.ss*\\id_*'",
            f"Get-ChildItem '{host.HOME}\\AppData\\Local\\*'",
            f"Get-Content {host.HOME}\\.ssh\\id_ed25519_wamo_tunnel",
        ):
            with self.subTest(befehl=befehl):
                self.assertNotEqual(host.classify(befehl), "read")


class BindungTests(unittest.TestCase):
    def test_nur_loopback(self):
        self.assertEqual(host_http.pruefe_bindung("127.0.0.1"), "127.0.0.1")
        self.assertEqual(host_http.pruefe_bindung("[::1]"), "::1")
        for wert in ("0.0.0.0", "::", "[::]", "", "localhost", "192.168.178.20", "127.0.0.2", "10.0.0.1"):
            with self.subTest(wert=wert), self.assertRaises(ValueError):
                host_http.pruefe_bindung(wert)

    def test_start_ohne_token_bricht_vor_dem_server_ab(self):
        with mock.patch("uvicorn.run") as run, self.assertRaises(host_http.TokenFehler):
            host_http.starte(host.mcp, "127.0.0.1", 8765, None)
        run.assert_not_called()

    def test_start_an_alle_adressen_bricht_ab(self):
        with mock.patch("uvicorn.run") as run, self.assertRaises(ValueError):
            host_http.starte(host.mcp, "0.0.0.0", 8765, "egal")
        run.assert_not_called()


class AnmeldungTests(unittest.TestCase):
    def test_ohne_token_alles_401(self):
        anfragen = [
            ("POST", "/mcp", json.dumps(_rpc("tools/list"))),
            ("GET", "/mcp", None),
            ("OPTIONS", "/mcp", None),
            ("DELETE", "/mcp", None),
            ("GET", "/.well-known/oauth-protected-resource", None),
            ("GET", "/.well-known/oauth-authorization-server", None),
            ("GET", "/", None),
        ]
        for methode, pfad, body in anfragen:
            with self.subTest(methode=methode, pfad=pfad):
                r = _client.request(methode, pfad, content=body, headers=MCP_HEADERS)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.headers.get("www-authenticate"), "Bearer")
                self.assertNotIn("tools", r.text)

    def test_falsches_oder_unsauberes_token_401(self):
        kaputt = [
            {"Authorization": f"Bearer {TOKEN[:-1]}x"},
            {"Authorization": f"Basic {TOKEN}"},
            {"Authorization": TOKEN},
            {"Authorization": "Bearer "},
            {"Authorization": f"Bearer {TOKEN}{TOKEN}"},
        ]
        for headers in kaputt:
            with self.subTest(headers=list(headers.values())[0][:12]):
                r = _client.post("/mcp", content=json.dumps(_rpc("tools/list")), headers={**MCP_HEADERS, **headers})
                self.assertEqual(r.status_code, 401)

    def test_doppelter_auth_header_401(self):
        r = _client.post("/mcp", content=json.dumps(_rpc("tools/list")),
                         headers=[("accept", MCP_HEADERS["Accept"]), ("content-type", "application/json"),
                                  ("authorization", f"Bearer {TOKEN}"), ("authorization", "Bearer x")])
        self.assertEqual(r.status_code, 401)

    def test_browser_mit_origin_403(self):
        r = _client.post("/mcp", content=json.dumps(_rpc("tools/list")),
                         headers={**MCP_HEADERS, **auth(), "Origin": "http://localhost:3000"})
        self.assertEqual(r.status_code, 403)

    def test_unbekannter_pfad_404_nur_mit_token(self):
        self.assertEqual(_client.get("/.well-known/oauth-protected-resource", headers=auth()).status_code, 404)

    def test_audit_ohne_tokenwerte(self):
        audit_eintraege.clear()
        falsch = secrets.token_urlsafe(32)
        _client.post("/mcp", content="{}", headers={**MCP_HEADERS, "Authorization": f"Bearer {falsch}"})
        self.assertTrue(audit_eintraege)
        text = json.dumps(audit_eintraege)
        self.assertNotIn(falsch, text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual(audit_eintraege[-1]["risk"], "rejected")

    def test_fehlversuche_gedrosselt(self):
        uhr = [0.0]
        eintraege: list[dict] = []
        waechter = host_http.TokenWaechter(app=None, token=TOKEN, audit=eintraege.append, clock=lambda: uhr[0])
        for _ in range(50):
            waechter._vermerke_fehlversuch({"method": "POST", "path": "/mcp"}, "test")
        self.assertEqual(len(eintraege), host_http.FEHLVERSUCHE_JE_MINUTE)
        uhr[0] = 61.0
        waechter._vermerke_fehlversuch({"method": "POST", "path": "/mcp"}, "test")
        self.assertEqual(eintraege[-2]["anzahl"], 50 - host_http.FEHLVERSUCHE_JE_MINUTE)


class McpMitTokenTests(unittest.TestCase):
    def _post(self, body: dict) -> dict:
        r = _client.post("/mcp", content=json.dumps(body), headers={**MCP_HEADERS, **auth()})
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertTrue(r.headers["content-type"].startswith("application/json"))
        return r.json()

    def test_initialize_nennt_den_wamo_host(self):
        antwort = self._post(_rpc("initialize", _init_params()))
        self.assertEqual(antwort["result"]["serverInfo"]["name"], "wamo-host")

    def test_werkzeugliste_und_lesender_aufruf(self):
        namen = {t["name"] for t in self._post(_rpc("tools/list", rpc_id=2))["result"]["tools"]}
        self.assertTrue({"system_info", "fenster_auflisten", "powershell_lesen"} <= namen)
        self.assertNotIn("execute_approved", namen)  # Freigaben gibt es nie als Werkzeug
        audit_eintraege.clear()
        with mock.patch.object(host, "AUDIT_LOG", Path(tempfile.gettempdir()) / f"wamo-test-{secrets.token_hex(4)}.jsonl") as log:
            self.addCleanup(lambda: log.unlink(missing_ok=True))
            antwort = self._post(_rpc("tools/call", {"name": "system_info", "arguments": {}}, rpc_id=3))
            text = antwort["result"]["content"][0]["text"]
            self.assertEqual(json.loads(text)["status"], "success")
            zeile = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual((zeile["tool"], zeile["risk"]), ("system_info", "read"))

    def test_kritischer_befehl_wird_auch_ueber_http_nicht_ausgefuehrt(self):
        with mock.patch.object(host, "_powershell") as ps, \
                mock.patch.object(host, "AUDIT_LOG", Path(tempfile.gettempdir()) / f"wamo-test-{secrets.token_hex(4)}.jsonl") as log:
            self.addCleanup(lambda: log.unlink(missing_ok=True))
            antwort = self._post(_rpc("tools/call", {"name": "powershell_lesen",
                                                     "arguments": {"command": "Remove-Item C:\\x", "reason": "t"}}, 4))
            self.assertEqual(json.loads(antwort["result"]["content"][0]["text"])["status"], "needs_approval")
            ps.assert_not_called()


if __name__ == "__main__":
    unittest.main()
