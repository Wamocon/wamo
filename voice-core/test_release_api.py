"""Tests des Weges vom Kern zum Release-Dienst (release_client.py) und der Route GET /wamo/api/release/status (release_api.py), Schritt 1.5.

Der Release-Dienst wird durch einen echten Unix-Socket mit Attrappe ersetzt (nur Linux). Geprüft werden die Zeile, die der Kern schickt, die streng
gefilterte Sicht (nichts anderes als bekannte Felder erreicht die Oberfläche), die Rolle admin, die Rate-Grenze und der Ausfall des Dienstes.

Ausführen:  uv run python -m pytest -q test_release_api.py   (im Ordner voice-core/)
"""

import json
import os
import re
import socket
import sys
import tempfile
import threading

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import release_api as api
import release_client as C
import wamo_api
from auth_gate import Identity

LINUX = hasattr(socket, "AF_UNIX") and sys.platform.startswith("linux")
ADMIN = "123e4567-e89b-12d3-a456-426614174000"
KENNUNG = "0b9f2c1e-4a6d-4f0e-9a1b-2c3d4e5f6a7b"

STATUS = {
    "ok": True, "text": "Release-Dienst läuft", "version": "0.1", "zeit": "2026-11-02T13:00:00Z", "modus": "geruest", "bereit": True, "defekt": None,
    "anfrage": {"id": KENNUNG, "status": "pruefung1", "quelle": "main", "profil": ADMIN, "seq": 3, "schritt": 2, "schritte": 11, "seit": "2026-11-02T12:00:00Z",
                "frist_bis": "2026-11-02T14:00:00Z", "versuch": 1, "fertig": False, "version": "0.1.1", "klasse": "normal"},
    "abgeschlossen": [{"id": KENNUNG, "status": "abgebrochen", "grund": "zeit"}], "angehalten": None,
    "anker": {"erreichbar": True, "sperre": "an"}, "datenbank": "angeschlossen", "arbeiter": "frei",
    "audit": {"kette": "ok", "zeilen": 70, "letzter_hash": "a" * 64, "grund": None, "fuellgrad_prozent": 0.1},
    "spiegel": {"rueckstand": 0, "defekt": None}, "abweichung": None, "rueckweg_offen": None, "speicherfehler": None,
    "installation": "ok", "installation_probleme": None,
}


class Dienst:
    """Ein Unix-Socket, der wie ctl.sock antwortet und die empfangenen Zeilen aufhebt."""

    def __init__(self, antwort: bytes | None = None):
        self.dir = tempfile.mkdtemp(prefix="release-api-")
        self.pfad = os.path.join(self.dir, "ctl.sock")
        self.zeilen: list[bytes] = []
        self.antwort = json.dumps(STATUS).encode() + b"\n" if antwort is None else antwort
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.pfad)
        self.srv.listen(8)
        self.t = threading.Thread(target=self._lauf, daemon=True)
        self.t.start()

    def _lauf(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                daten = b""
                while not daten.endswith(b"\n"):
                    block = conn.recv(4096)
                    if not block:
                        break
                    daten += block
                self.zeilen.append(daten)
                conn.sendall(self.antwort)

    def schliessen(self):
        self.srv.close()


@pytest.fixture
def dienst(monkeypatch):
    if not LINUX:
        pytest.skip("Unix-Sockets nur unter Linux")
    d = Dienst()
    monkeypatch.setenv("WAMO_RELEASE_SOCKET", d.pfad)
    yield d
    d.schliessen()


# ------------------------------------------------------------------------------------------------ Client
def test_anfrage_schickt_genau_eine_zeile_mit_frischer_kennung(dienst):
    C.anfrage("status")
    C.anfrage("status")
    a, b = dienst.zeilen
    for z in (a, b):
        assert re.fullmatch(rb"[0-9a-f]{32} status\n", z)
    assert a != b   # der Dienst lehnt eine wiederholte Kennung ab


def test_sicht_enthaelt_nur_bekannte_felder(dienst):
    s = C.status()
    assert set(s) == {"verfuegbar", "modus", "version", "bereit", "defekt", "angehalten", "anker", "datenbank", "arbeiter", "audit_kette", "installation",
                      "anfrage", "abgeschlossen", "zeit"}
    assert s["anker"] == {"erreichbar": True, "sperre": "an"}
    assert (s["modus"], s["datenbank"], s["arbeiter"], s["audit_kette"], s["installation"]) == ("geruest", "angeschlossen", "frei", "ok", "ok")
    assert s["anfrage"]["id"] == KENNUNG and s["anfrage"]["schritt"] == 2 and s["anfrage"]["version"] == "0.1.1"
    assert "profil" not in s["anfrage"]   # die Person hinter der Anfrage geht die Oberfläche nichts an
    text = json.dumps(s)
    assert "a" * 64 not in text   # kein Hash der Audit-Kette


def test_sicht_faengt_falsche_formen_ab():
    kaputt = {"ok": True, "modus": "Geruest!", "bereit": "ja", "defekt": "x" * 500, "anker": "text", "audit": [], "datenbank": "irgendwas",
              "arbeiter": 5, "installation": "abweichung", "anfrage": {"id": "../../etc/passwd"}, "abgeschlossen": [{"id": "x"}, 5, {"id": KENNUNG, "status": "A B"}],
              "version": ["0.1"], "zeit": 12}
    s = C.ansicht(kaputt)
    assert s["modus"] == "unbekannt" and s["bereit"] is False and len(s["defekt"]) == 80
    assert s["anker"] == {"erreichbar": False, "sperre": "unbekannt"}
    assert (s["datenbank"], s["arbeiter"], s["audit_kette"], s["installation"]) == ("unbekannt", "unbekannt", "defekt", "abweichung")
    assert s["anfrage"] is None
    assert s["abgeschlossen"] == [{"id": KENNUNG, "status": None, "grund": None}]
    assert s["version"] == "unbekannt" or s["version"] is None
    assert s["zeit"] is None


def test_texte_ohne_steuerzeichen_und_gekuerzt():
    s = C.ansicht({"ok": True, "defekt": "Fehler\x00\x1b[31m rot\nzweite Zeile", "angehalten": "a" * 300})
    assert "\x00" not in s["defekt"] and "\x1b" not in s["defekt"] and "\n" not in s["defekt"]
    assert len(s["angehalten"]) == 80


def test_abgelehnte_antwort_wird_zum_kurzen_grund():
    if not LINUX:
        pytest.skip("Unix-Sockets nur unter Linux")
    for roh, grund in ((b'{"ok": false, "grund": "gesperrt"}\n', "gesperrt"), (b'{"ok": false, "grund": "B\xc3\xb6ser Text mit Pfad /etc"}\n', "abgelehnt"),
                       (b'{"ok": false}\n', "abgelehnt"), (b"kein json\n", "antwort_ungueltig"), (b"[1]\n", "antwort_ungueltig"),
                       (b'{"ok": "ja"}\n', "antwort_ungueltig"), (b"", "keine_antwort")):
        d = Dienst(roh)
        try:
            with pytest.raises(C.ReleaseFehler) as e:
                C.anfrage("status", d.pfad)
            assert e.value.grund == grund, roh
        finally:
            d.schliessen()


def test_dienst_fehlt_oder_zu_gross(tmp_path):
    with pytest.raises(C.ReleaseFehler) as e:
        C.anfrage("status", str(tmp_path / "gibt-es-nicht.sock"))
    assert e.value.grund == "nicht_verfuegbar"
    if LINUX:
        d = Dienst(b'{"ok": true, "x": "' + b"a" * (C.ANTWORT_MAX + 10) + b'"}\n')
        try:
            with pytest.raises(C.ReleaseFehler) as e:
                C.anfrage("status", d.pfad)
            assert e.value.grund in ("keine_antwort", "antwort_ungueltig")
        finally:
            d.schliessen()


def test_befehl_wird_vor_dem_senden_geprueft():
    for befehl in ("status; rm", "Status", "a", "status\n", "x" * 30, ""):
        with pytest.raises(ValueError):
            C.anfrage(befehl, "/gibt/es/nicht")


def test_client_kennt_nur_lesen():
    quelle = open(C.__file__, encoding="utf-8").read()
    code = re.sub(r'""".*?"""', "", quelle, flags=re.S)
    assert "freigeben" not in code.lower()
    assert "anfordern" not in code   # vorschlagen kommt erst mit Schritt 3.4


# ------------------------------------------------------------------------------------------------ Route
class Kontext:
    rolle = "admin"
    gesperrt = False


@pytest.fixture
def ctx(monkeypatch):
    k = Kontext()

    async def identitaet(_request):
        if k.gesperrt:
            raise HTTPException(403, "kein aktives WAMO-Profil oder zweiter Faktor fehlt")
        return Identity(user_id=ADMIN, name="Admin", rolle=k.rolle, anrede="du", anmeldung=1.0)

    monkeypatch.setattr(api, "identitaet", identitaet)
    api._versuche.clear()
    return k


@pytest.fixture
def client(ctx):
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return TestClient(app)


def test_admin_bekommt_die_sicht(client, dienst):
    r = client.get("/wamo/api/release/status")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    j = r.json()
    assert j["verfuegbar"] is True and j["anker"]["sperre"] == "an" and j["anfrage"]["status"] == "pruefung1"


def test_mitglied_wird_abgewiesen_ohne_den_dienst_zu_fragen(client, ctx, dienst):
    ctx.rolle = "mitglied"
    assert client.get("/wamo/api/release/status").status_code == 403
    assert dienst.zeilen == []


def test_ohne_anmeldung_403(client, ctx, dienst):
    ctx.gesperrt = True
    assert client.get("/wamo/api/release/status").status_code == 403
    assert dienst.zeilen == []


def test_dienst_nicht_erreichbar_ist_eine_normale_antwort(client, monkeypatch, tmp_path):
    monkeypatch.setenv("WAMO_RELEASE_SOCKET", str(tmp_path / "weg.sock"))
    r = client.get("/wamo/api/release/status")
    assert r.status_code == 200
    assert r.json() == {"verfuegbar": False, "grund": "nicht_verfuegbar"}


def test_rate_grenze(client, dienst):
    for _ in range(api.STATUS_GRENZE[0]):
        assert client.get("/wamo/api/release/status").status_code == 200
    assert client.get("/wamo/api/release/status").status_code == 429


def test_nur_get_und_kein_schreibender_weg(client, dienst):
    for methode in ("post", "put", "delete", "patch"):
        assert getattr(client, methode)("/wamo/api/release/status").status_code == 405
    assert [r.path for r in api.router.routes] == ["/release/status"]


def test_route_ist_im_kern_registriert():
    assert "release_api" in wamo_api.ROUTEN_MODULE
