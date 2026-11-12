"""Auslieferung der Weboberflächen: feste Liste von Dateien, nur GET, strenge Köpfe, kein Pfad-Trick (Jira-Unteraufgabe Board-Seite und Betreuer-Seite)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from echter_dienst import laufender_dienst
from hilfen import BETREUER_TOKEN, KERN_TOKEN, LABORTAG, Uhr, anlegen_http, oeffnen

from wamo_labor_dienst import auslieferung
from wamo_labor_dienst.app import create_app
from wamo_labor_dienst.konfig import Konfig

WEB = Path(auslieferung.__file__).resolve().parent / "web"
HTML, JS, CSS = "text/html; charset=utf-8", "text/javascript; charset=utf-8", "text/css; charset=utf-8"
# Die feste Liste: Seite (Adresse) und Datei (unter /web/<Name>). Wer eine Datei hinzufügt, ändert diese Liste bewusst.
SEITEN = {"/": "board.html", "/board": "board.html", "/betreuer": "betreuer.html"}
DATEIEN = {"stil.css": CSS, "dom.js": JS, "gemeinsam.js": JS, "board-logik.js": JS, "board.js": JS, "betreuer-logik.js": JS, "betreuer.js": JS}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
ALLE_ADRESSEN = [*SEITEN, *(f"/web/{n}" for n in DATEIEN)]


def anfrage(app, pfad: str, methode: str = "GET") -> tuple[int, dict[str, str], bytes]:
    """Eine Anfrage direkt an die Anwendung, mit dem Pfad so, wie uvicorn ihn liefert (schon entschlüsselt: aus %2e%2e wird ..)."""
    start: dict = {}
    teile: list[bytes] = []

    async def empfangen():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def senden(nachricht):
        if nachricht["type"] == "http.response.start":
            start.update(status=nachricht["status"], koepfe={k.decode().lower(): v.decode() for k, v in nachricht["headers"]})
        else:
            teile.append(nachricht.get("body", b""))

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": methode, "scheme": "http", "path": pfad, "raw_path": pfad.encode(),
             "query_string": b"", "headers": [], "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1)}
    asyncio.run(app(scope, empfangen, senden))
    return start["status"], start["koepfe"], b"".join(teile)


def test_die_liste_ist_fest():
    assert dict(auslieferung.SEITEN) == SEITEN and dict(auslieferung.DATEIEN) == DATEIEN


@pytest.mark.parametrize("adresse", ALLE_ADRESSEN)
def test_jede_adresse_der_liste_liefert_die_datei_mit_dem_richtigen_inhaltstyp(client, adresse):
    r = client.get(adresse)
    name = SEITEN.get(adresse) or adresse.removeprefix("/web/")
    erwartet = HTML if name.endswith(".html") else DATEIEN[name]
    assert r.status_code == 200 and r.headers["content-type"] == erwartet
    assert r.content == (WEB / name).read_bytes() and len(r.content) > 100


@pytest.mark.parametrize("adresse", ALLE_ADRESSEN)
def test_strenge_koepfe_auf_jeder_ausgelieferten_datei(client, adresse):
    k = client.get(adresse).headers
    assert k["content-security-policy"] == CSP
    assert k["x-content-type-options"] == "nosniff" and k["referrer-policy"] == "no-referrer" and k["cache-control"] == "no-store"
    assert k["x-frame-options"] == "DENY"
    policy = k["permissions-policy"]  # alle mächtigen Funktionen mit leerer Liste (niemand darf sie nutzen)
    assert all(f"{f}=()" in policy for f in ("camera", "microphone", "geolocation", "payment", "usb"))


def test_csp_hat_keine_unsicheren_quellen_und_kein_blau_in_der_richtlinie(client):
    csp = client.get("/board").headers["content-security-policy"]
    for verboten in ("unsafe-inline", "unsafe-eval", "http:", "https:", "*", "blob:", "ws:"):
        assert verboten not in csp
    assert csp.startswith("default-src 'none';") and "script-src 'self';" in csp and "frame-ancestors 'none'" in csp


@pytest.mark.parametrize("adresse", ALLE_ADRESSEN)
@pytest.mark.parametrize("methode", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"])
def test_nur_get_alles_andere_ist_405_mit_allow(client, adresse, methode):
    r = client.request(methode, adresse)
    assert r.status_code == 405 and r.headers["allow"] == "GET"
    if methode != "HEAD":
        assert r.json()["fehler"] == "nicht_erlaubt"


@pytest.mark.parametrize("pfad", [
    "/web", "/web/", "/web/gibt-es-nicht.js", "/web/BOARD.JS", "/web/board.js/", "/web/board.js%00", "/web/board.js.map", "/web/app.py", "/web/__init__.py",
    "/web/../board.html", "/web/../betreuer.html", "/web/./board.js", "/web/a/../board.js", "/web/..", "/web/../wamo_labor_dienst/app.py",
    "/web/../../pyproject.toml", "/web/\\..\\app.py", "/web/..%2fapp.py", "/web//board.js", "//web/board.js", "/web/board.html", "/web/betreuer.html",
    "/index.html", "/board.html", "/board/", "/betreuer/", "/Board", "/BETREUER", "/static/board.js", "/web/package.json", "/web/.env", "/web/%2e%2e/app.py",
    "/web/ board.js", "/web/board.js ", "/web/board.js;x", "/web/stil.css/../board.js"])
def test_alles_ausserhalb_der_liste_ist_404_ohne_inhalt(app, pfad):
    status, koepfe, koerper = anfrage(app, pfad)
    assert status == 404 and koepfe["content-type"].startswith("application/json")
    assert b'"nicht_erlaubt"' in koerper and b"import " not in koerper and b"<html" not in koerper and b"def " not in koerper


def test_kein_verzeichnislisting_auch_nicht_fuer_den_ordner(client):
    for pfad in ("/web", "/web/", "/web/.", "/web/%2e"):
        r = client.get(pfad)
        assert r.status_code == 404 and "board.js" not in r.text


def test_zur_laufzeit_wird_nichts_von_der_platte_gelesen(konfig, uhr, monkeypatch, tmp_path):
    """Alles liegt nach dem Start im Speicher: Zeigt der Ordner der Seiten danach ins Leere, wird trotzdem ausgeliefert (kein Pfad-Trick kann etwas öffnen)."""
    from fastapi.testclient import TestClient

    app = create_app(konfig, uhr=uhr)
    monkeypatch.setattr(auslieferung, "WEB", tmp_path / "gibt-es-nicht")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/board").status_code == 200 and c.get("/web/board.js").status_code == 200 and c.get("/web/x.js").status_code == 404


def test_fehlt_eine_datei_der_liste_startet_der_dienst_nicht(konfig, uhr, monkeypatch):
    monkeypatch.setitem(auslieferung.DATEIEN, "fehlt-in-wirklichkeit.js", JS)
    with pytest.raises(FileNotFoundError):
        create_app(konfig, uhr=uhr)


def test_die_seiten_aendern_die_schutzschicht_der_schnittstelle_nicht(client):
    api = client.get("/gesundheit").headers
    assert api["content-security-policy"] == "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    assert client.get("/api/kern/durchlauf").status_code == 401  # die Anmeldung der Schnittstelle bleibt, wie sie war
    assert client.get("/api/betreuer/plaetze").status_code == 401
    assert client.get("/api/browser/board").status_code == 401


def test_seiten_brauchen_keinen_token_und_zeigen_keine_platzdaten(client, konfig):
    d = oeffnen(client, 3)
    anlegen_http(client, d, titel="Streng geheimer Seitentitel", beschreibung="Streng geheime Seitenbeschreibung")
    for adresse in ALLE_ADRESSEN:
        r = client.get(adresse)
        assert r.status_code == 200
        for verboten in ("Streng geheim", d, KERN_TOKEN, BETREUER_TOKEN, "Anmeldung schlägt fehl", "Login schlägt bei falschem Passwort fehl"):
            assert verboten not in r.text, (adresse, verboten)


def test_ausgelieferte_seiten_werden_protokolliert_als_bereich_web_ohne_pfad(client, konfig):
    client.get("/board")
    client.get("/web/board.js")
    dienst = client.app.state.dienst
    dienst.protokoll.abwarten()
    roh = (dienst.protokoll.ordner / "dienst.jsonl").read_text(encoding="utf-8")
    assert roh.count('"bereich":"web"') >= 2 and "board.js" not in roh and "/board" not in roh


# ---------- echter Server: kein Pfad-Trick über %2e und Schrägstriche ----------
def _roh_anfrage(adresse: str, zeile: str) -> bytes:
    import socket

    host, port = adresse.removeprefix("http://").split(":")
    s = socket.create_connection((host, int(port)), timeout=5)
    s.sendall(f"GET {zeile} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    teile = b""
    while stueck := s.recv(65536):
        teile += stueck
    s.close()
    return teile


def test_ueber_echtes_http_gibt_es_keinen_pfad_trick(tmp_path):
    konfig = Konfig(daten=tmp_path / "daten", kern_token=KERN_TOKEN, betreuer_token=BETREUER_TOKEN)
    with laufender_dienst(create_app(konfig, uhr=Uhr(LABORTAG))) as adresse:
        ok = _roh_anfrage(adresse, "/web/board.js")
        assert ok.startswith(b"HTTP/1.1 200") and b"content-type: text/javascript; charset=utf-8" in ok.lower()
        for zeile in ("/web/%2e%2e/board.html", "/web/%2E%2E/app.py", "/web/..%2fapp.py", "/web/%2e%2e%2f%2e%2e%2fpyproject.toml", "/web/board.js%2f..%2f..",
                      "/web/%2e", "/web//board.js", "/web/board.js%00.html", "/web/%5c..%5capp.py", "/web/board.js/%2e%2e/app.py"):
            antwort = _roh_anfrage(adresse, zeile)
            assert antwort.split(b"\r\n")[0] in (b"HTTP/1.1 404 Not Found", b"HTTP/1.1 400 Bad Request"), (zeile, antwort[:60])
            assert b"import " not in antwort and b"def " not in antwort
