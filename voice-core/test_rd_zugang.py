"""Tests des Zugangsdienstes (infra/sokrates2/wamo-rd/zugang/wamo_rd_zugang.py) gegen eine Guacamole-Attrappe.

Die Befehle und der Aufseher laufen überall. Die Prüfung der Benutzerkennung am Unix-Socket (SO_PEERCRED) gibt es nur unter Linux
und läuft dort (auf dem Server oder in einem Container); unter Windows wird sie übersprungen.
Das Zusammenspiel mit dem echten Guacamole prüft infra/sokrates2/wamo-rd/zugang/pruefe_gegen_guacamole.py (Docker).

Ausführen:  uv run python -m pytest -q test_rd_zugang.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

PFAD = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "wamo-rd" / "zugang" / "wamo_rd_zugang.py"
spec = importlib.util.spec_from_file_location("wamo_rd_zugang", PFAD)
zugang = importlib.util.module_from_spec(spec)
sys.modules["wamo_rd_zugang"] = zugang
spec.loader.exec_module(zugang)

SITZUNG = "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d"
SITZUNG2 = "aaaaaaaa-1111-4222-8333-444444444444"
SITZUNG3 = "bbbbbbbb-1111-4222-8333-444444444444"
SITZUNG4 = "cccccccc-1111-4222-8333-444444444444"
GUTE_ANWEISUNG = "gut" + "A" * 200            # die Attrappe nimmt alles an, was mit "gut" beginnt
SCHLECHTE_ANWEISUNG = "bad" + "A" * 200


class Guacamole:
    """Attrappe der Webanwendung: nimmt Anweisungen an, vergibt Tokens, meldet aktive Verbindungen, löscht Tokens."""

    def __init__(self):
        self.tokens: dict[str, int] = {}          # Token -> Anzahl offener Verbindungen
        self.posts: list[str] = []
        self.deletes: list[str] = []
        self.zaehler = 0
        self.haenger = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keine Ausgabe
                pass

            def _antwort(self, code, body=b"", typ="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", typ)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                laenge = int(self.headers.get("Content-Length", "0"))
                form = parse_qs(self.rfile.read(laenge).decode())
                data = form.get("data", [""])[0]
                outer.posts.append(data)
                if not data.startswith("gut"):
                    return self._antwort(403, b'{"message":"Permission Denied."}')
                outer.zaehler += 1
                token = f"TOKEN{outer.zaehler:03d}" + "x" * 30
                outer.tokens[token] = 0
                self._antwort(200, json.dumps({"authToken": token, "username": "rd-test"}).encode())

            def do_DELETE(self):
                token = self.path.rsplit("/", 1)[-1]
                outer.deletes.append(token)
                if outer.tokens.pop(token, None) is None:
                    return self._antwort(404, b"{}")
                self._antwort(204)

            def do_GET(self):
                u = urlsplit(self.path)
                token = parse_qs(u.query).get("token", [""])[0]
                if outer.haenger:
                    time.sleep(5)
                if token not in outer.tokens:
                    return self._antwort(403, b"{}")
                n = outer.tokens[token]
                if u.path != "/api/session/tunnels":
                    return self._antwort(404, b"{}")
                self._antwort(200, json.dumps([f"tunnel-{i}" for i in range(n)]).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def schliessen(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def guac(monkeypatch):
    g = Guacamole()
    monkeypatch.setattr(zugang, "GUAC_HOST", "127.0.0.1")
    monkeypatch.setattr(zugang, "GUAC_PORT", g.port)
    monkeypatch.setattr(zugang, "HTTP_TIMEOUT", 1.0)
    yield g
    g.schliessen()


def anmelden(sitzung=SITZUNG, data=GUTE_ANWEISUNG, max_secs=3600):
    return {"cmd": "anmelden", "sitzung": sitzung, "data": data, "max_secs": max_secs}


# ======================================================================== Befehle
async def test_anmelden_gibt_das_sitzungstoken(guac, capsys):
    z = zugang.Zustand()
    a = await zugang.befehl(z, anmelden())
    assert a["ok"] is True and a["token"].startswith("TOKEN001")
    assert guac.posts == [GUTE_ANWEISUNG]
    assert z.sitzungen[SITZUNG].token == a["token"]
    protokoll = capsys.readouterr().out
    assert a["token"] not in protokoll and GUTE_ANWEISUNG not in protokoll and "9a1b2c3d" in protokoll


async def test_abgelehnte_anweisung_wird_nie_wiederholt(guac):
    z = zugang.Zustand()
    a = await zugang.befehl(z, anmelden(data=SCHLECHTE_ANWEISUNG))
    assert a == {"ok": False, "code": "abgelehnt"}
    assert len(guac.posts) == 1 and z.sitzungen == {}          # genau ein Versuch: Guacamole sperrt sonst die Absenderadresse


async def test_gateway_nicht_erreichbar(monkeypatch):
    monkeypatch.setattr(zugang, "GUAC_HOST", "127.0.0.1")
    monkeypatch.setattr(zugang, "GUAC_PORT", 1)
    a = await zugang.befehl(zugang.Zustand(), anmelden())
    assert a == {"ok": False, "code": "gateway_nicht_erreichbar"}


@pytest.mark.parametrize("aenderung", [
    {"data": ""}, {"data": "kurz"}, {"data": "gut" + "A" * 20000}, {"data": "gut!" + "A" * 200}, {"data": 5}, {"data": None},
    {"max_secs": 59}, {"max_secs": 14401}, {"max_secs": True}, {"max_secs": "3600"}, {"max_secs": None}, {"max_secs": 3600.5},
])
async def test_ungueltige_angaben(guac, aenderung):
    a = await zugang.befehl(zugang.Zustand(), {**anmelden(), **aenderung})
    assert a == {"ok": False, "code": "angaben_ungueltig"}
    assert guac.posts == []


@pytest.mark.parametrize("sitzung", [None, "", "abc", 5, "g" * 36, SITZUNG + "0", SITZUNG[:-1], ["x"]])
async def test_sitzungskennung_muss_eine_uuid_sein(guac, sitzung):
    for cmd in ("anmelden", "status", "beenden"):
        a = await zugang.befehl(zugang.Zustand(), {**anmelden(), "cmd": cmd, "sitzung": sitzung, "grund": "beendet"})
        assert a == {"ok": False, "code": "sitzung_ungueltig"}
    assert guac.posts == []


async def test_unbekannter_befehl(guac):
    assert (await zugang.befehl(zugang.Zustand(), {"cmd": "rm", "sitzung": SITZUNG}))["code"] == "unbekannter_befehl"
    assert (await zugang.befehl(zugang.Zustand(), {"cmd": None, "sitzung": SITZUNG}))["code"] == "unbekannter_befehl"


async def test_doppelte_sitzung_und_obergrenze(guac):
    z = zugang.Zustand()
    assert (await zugang.befehl(z, anmelden(SITZUNG)))["ok"]
    assert (await zugang.befehl(z, anmelden(SITZUNG)))["code"] == "schon_da"
    assert (await zugang.befehl(z, anmelden(SITZUNG2)))["ok"]
    assert (await zugang.befehl(z, anmelden(SITZUNG3)))["ok"]
    assert (await zugang.befehl(z, anmelden(SITZUNG4)))["code"] == "voll"
    assert len(guac.posts) == 3


async def test_status_und_beenden(guac):
    z = zugang.Zustand()
    token = (await zugang.befehl(z, anmelden()))["token"]
    s = await zugang.befehl(z, {"cmd": "status", "sitzung": SITZUNG})
    assert s == {"ok": True, "offen": True, "verbunden": False, "grund": None}
    e = await zugang.befehl(z, {"cmd": "beenden", "sitzung": SITZUNG, "grund": "beendet"})
    assert e["ok"] and e["offen"] is False and e["grund"] == "beendet"
    assert guac.deletes == [token] and token not in guac.tokens          # das Token ist bei Guacamole gelöscht
    s = await zugang.befehl(z, {"cmd": "status", "sitzung": SITZUNG})
    assert s == {"ok": True, "offen": False, "verbunden": False, "grund": "beendet"}
    # nochmaliges Beenden ist folgenlos
    await zugang.befehl(z, {"cmd": "beenden", "sitzung": SITZUNG, "grund": "beendet"})
    assert len(guac.deletes) == 1
    # unbekannte Sitzung: weder offen noch ein Grund
    assert (await zugang.befehl(z, {"cmd": "status", "sitzung": SITZUNG2}))["grund"] is None


async def test_beenden_nur_mit_bekanntem_grund(guac):
    z = zugang.Zustand()
    await zugang.befehl(z, anmelden())
    assert (await zugang.befehl(z, {"cmd": "beenden", "sitzung": SITZUNG, "grund": "weil ich will"}))["code"] == "grund_ungueltig"
    assert (await zugang.befehl(z, {"cmd": "beenden", "sitzung": SITZUNG}))["code"] == "grund_ungueltig"
    assert SITZUNG in z.sitzungen


async def test_beenden_mit_gateway_ausfall_gibt_die_sitzung_trotzdem_frei(guac):
    z = zugang.Zustand()
    await zugang.befehl(z, anmelden())
    guac.schliessen()
    e = await zugang.befehl(z, {"cmd": "beenden", "sitzung": SITZUNG, "grund": "zeitlimit"})
    assert e["ok"] and SITZUNG not in z.sitzungen


# ======================================================================== Ziele und Proben
def test_ziele_laden(tmp_path):
    p = tmp_path / "z.toml"
    p.write_text('''
[[ziel]]
name = "sokrates2"
adresse = "192.168.178.136"
[[ziel]]
name = "caesar"
adresse = "192.168.178.140"
port = 3390
[[ziel]]
name = "Falsch"
adresse = "192.168.178.1"
[[ziel]]
name = "host"
adresse = "example.org"
[[ziel]]
name = "port"
adresse = "192.168.178.2"
port = 70000
''', encoding="utf-8")
    assert zugang.ziele_laden(str(p)) == {"sokrates2": ("192.168.178.136", 3389), "caesar": ("192.168.178.140", 3390)}
    assert zugang.ziele_laden(str(tmp_path / "nein.toml")) == {}


async def test_proben_nur_gegen_eingetragene_ziele(tmp_path, monkeypatch):
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    p = tmp_path / "z.toml"
    p.write_text(f'[[ziel]]\nname = "offen"\nadresse = "192.0.2.1"\nport = {port}\n', encoding="utf-8")
    monkeypatch.setattr(zugang, "ZIELE_DATEI", str(p))
    monkeypatch.setattr(zugang, "ziele_laden", lambda pfad=str(p): {"offen": ("127.0.0.1", port), "zu": ("127.0.0.1", 1)})
    async with server:
        a = await zugang.befehl(zugang.Zustand(), {"cmd": "proben"})
    assert a == {"ok": True, "ziele": {"offen": True, "zu": False}}


# ======================================================================== Aufseher
async def test_aufseher_beendet_nach_der_hoechstdauer(guac):
    z = zugang.Zustand()
    token = (await zugang.befehl(z, anmelden()))["token"]
    z.sitzungen[SITZUNG].frist = time.monotonic() - 1
    await zugang.aufseher_runde(z)
    assert SITZUNG not in z.sitzungen and z.fertig[SITZUNG][0] == "zeitlimit"
    assert token in guac.deletes


async def test_aufseher_nicht_geoeffnet(guac, monkeypatch):
    z = zugang.Zustand()
    await zugang.befehl(z, anmelden())
    await zugang.aufseher_runde(z)
    assert SITZUNG in z.sitzungen                                      # frisch: noch Zeit
    z.sitzungen[SITZUNG].start -= zugang.NICHT_GEOEFFNET_SECS + 1
    await zugang.aufseher_runde(z)
    assert z.fertig[SITZUNG][0] == "nicht_geoeffnet"


async def test_aufseher_getrennt_nach_offener_verbindung(guac):
    z = zugang.Zustand()
    token = (await zugang.befehl(z, anmelden()))["token"]
    guac.tokens[token] = 1                                             # die Seite ist offen, eine Verbindung läuft
    await zugang.aufseher_runde(z)
    assert z.sitzungen[SITZUNG].verbunden is True
    guac.tokens[token] = 0                                             # Browser zu oder Leerlauf am Ziel
    await zugang.aufseher_runde(z)
    assert SITZUNG in z.sitzungen                                      # Wiederverbinden innerhalb der Frist bleibt möglich
    z.sitzungen[SITZUNG].zuletzt_aktiv -= zugang.GETRENNT_SECS + 1
    await zugang.aufseher_runde(z)
    assert z.fertig[SITZUNG][0] == "getrennt" and token in guac.deletes


async def test_zweiter_tunnel_mit_demselben_token_schliesst_nach_der_schonfrist(guac):
    z = zugang.Zustand()
    token = (await zugang.befehl(z, anmelden()))["token"]
    guac.tokens[token] = 2                                             # zweiter Tab oder zweites Gerät mit demselben Token
    await zugang.aufseher_runde(z)
    assert SITZUNG in z.sitzungen                                      # Schonfrist: ein Neuladen öffnet kurz zwei Tunnel
    guac.tokens[token] = 1
    await zugang.aufseher_runde(z)
    assert z.sitzungen[SITZUNG].mehrfach_seit is None                  # wieder ein Tunnel: die Frist beginnt neu
    guac.tokens[token] = 2
    await zugang.aufseher_runde(z)
    z.sitzungen[SITZUNG].mehrfach_seit -= zugang.MEHRFACH_SECS + 1
    await zugang.aufseher_runde(z)
    assert z.fertig[SITZUNG][0] == "beendet" and token in guac.deletes


async def test_paralleles_anmelden_mit_derselben_sitzung_ergibt_nur_ein_token(guac):
    z = zugang.Zustand()
    a, b = await asyncio.gather(zugang.befehl(z, anmelden()), zugang.befehl(z, anmelden()))
    assert sorted([a["ok"], b["ok"]]) == [False, True]
    assert (a if not a["ok"] else b)["code"] == "schon_da"
    assert len(guac.posts) == 1 and len(z.sitzungen) == 1


async def test_aufseher_token_nicht_mehr_gueltig(guac):
    z = zugang.Zustand()
    token = (await zugang.befehl(z, anmelden()))["token"]
    del guac.tokens[token]                                             # Guacamole hat das Token selbst verworfen
    await zugang.aufseher_runde(z)
    assert z.fertig[SITZUNG][0] == "getrennt"


async def test_aufseher_schliesst_nichts_wenn_guacamole_schweigt(guac):
    z = zugang.Zustand()
    await zugang.befehl(z, anmelden())
    guac.haenger = True
    await zugang.aufseher_runde(z)
    assert SITZUNG in z.sitzungen and z.fertig == {}                   # Zeitüberschreitung ist kein Grund zu schließen
    guac.haenger = False
    z.sitzungen[SITZUNG].frist = time.monotonic() - 1
    guac.schliessen()
    await zugang.aufseher_runde(z)
    assert z.fertig[SITZUNG][0] == "zeitlimit"                         # das Höchstende gilt immer


async def test_fertige_sitzungen_werden_nach_zehn_minuten_vergessen(guac):
    z = zugang.Zustand()
    z.fertig[SITZUNG] = ("beendet", time.monotonic() - zugang.FERTIG_BEHALTEN_SECS - 1)
    z.fertig[SITZUNG2] = ("beendet", time.monotonic())
    await zugang.aufseher_runde(z)
    assert list(z.fertig) == [SITZUNG2]


# ======================================================================== Socket (nur Linux)
linux = pytest.mark.skipif(not hasattr(os, "getuid") or sys.platform == "win32", reason="Unix-Socket mit SO_PEERCRED nur unter Linux")


async def _zeile(pfad, anfrage: bytes) -> bytes:
    leser, schreiber = await asyncio.open_unix_connection(pfad)
    schreiber.write(anfrage)
    await schreiber.drain()
    try:
        return await asyncio.wait_for(leser.readline(), 5)
    finally:
        schreiber.close()


@linux
async def test_socket_erlaubt_nur_die_benutzerkennung_des_kerns(guac, tmp_path):
    pfad = str(tmp_path / "ctl.sock")
    aufgabe = asyncio.create_task(zugang.dienen(pfad, os.getuid()))
    for _ in range(50):
        if os.path.exists(pfad):
            break
        await asyncio.sleep(0.05)
    try:
        assert oct(os.stat(pfad).st_mode & 0o777) == "0o660"
        antwort = json.loads(await _zeile(pfad, json.dumps(anmelden()).encode() + b"\n"))
        assert antwort["ok"] is True
        assert json.loads(await _zeile(pfad, b"kein json\n")) == {"ok": False, "code": "zeile_ungueltig"}
        assert json.loads(await _zeile(pfad, b"[1,2]\n")) == {"ok": False, "code": "zeile_ungueltig"}
        zu_gross = await _zeile(pfad, b"{" + b"x" * 40000 + b"}\n")
        assert zu_gross == b"" or json.loads(zu_gross)["ok"] is False
    finally:
        aufgabe.cancel()
    # eine andere Benutzerkennung bekommt keine Antwort
    pfad2 = str(tmp_path / "ctl2.sock")
    aufgabe = asyncio.create_task(zugang.dienen(pfad2, os.getuid() + 1))
    for _ in range(50):
        if os.path.exists(pfad2):
            break
        await asyncio.sleep(0.05)
    try:
        assert await _zeile(pfad2, json.dumps(anmelden(SITZUNG2)).encode() + b"\n") == b""
        assert len(guac.posts) == 1                                    # nur die erste Anmeldung kam durch
    finally:
        aufgabe.cancel()


def test_main_verlangt_die_kennung_des_kerns(monkeypatch):
    monkeypatch.delenv("WAMO_RD_KERN_UID", raising=False)
    assert zugang.main() == 2
    monkeypatch.setenv("WAMO_RD_KERN_UID", "0")
    assert zugang.main() == 2
    monkeypatch.setenv("WAMO_RD_KERN_UID", "abc")
    assert zugang.main() == 2


def test_quelltext_ohne_geheimnisse_und_ohne_netzport():
    text = PFAD.read_text(encoding="utf-8")
    assert "start_server(" not in text.replace("start_unix_server(", "")   # nur Unix-Socket, nie TCP
    assert "json.key" not in text and "PASSWORT" not in text and "hmac" not in text.lower()
