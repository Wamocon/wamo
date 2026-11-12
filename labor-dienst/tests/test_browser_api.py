"""Schnittstelle für die Board-Seite im Browser: nur lesend, der Platz kommt immer aus dem Platz-Token (D6, D8, D9)."""
from __future__ import annotations

import pytest
from hilfen import ANLEGEN, BETREUER_TOKEN, KERN_TOKEN, anlegen_http, betreuer_kopf, oeffnen, schreib_kopf

PLATZ = 3


def _token(client, platz: int = PLATZ) -> str:
    r = client.post(f"/api/betreuer/plaetze/{platz}/platz-token", headers=betreuer_kopf())
    assert r.status_code == 200
    return r.json()["platz_token"]


def _board(client, token: str, **kw):
    return client.get("/api/browser/board", headers={"X-Platz-Token": token}, **kw)


@pytest.fixture
def d(client) -> str:
    return oeffnen(client, PLATZ)


def test_board_zeigt_genau_das_board_des_eigenen_platzes(client, d):
    token = _token(client)
    nummer = anlegen_http(client, d).json()["nummer"]
    client.post("/api/kern/tickets/LAB-12/kommentare", json={"erwartete_version": 1, "text": "Ein Kommentar"}, headers=schreib_kopf(d))
    r = _board(client, token)
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"platz", "durchlauf", "tickets"} and j["platz"] == PLATZ
    assert j["durchlauf"] == {"offen": True, "einwilligung_version": 1}
    assert [t["nummer"] for t in j["tickets"]] == ["LAB-11", "LAB-12", "LAB-14", "LAB-17", "LAB-20", "LAB-25", nummer]
    t12 = next(t for t in j["tickets"] if t["nummer"] == "LAB-12")
    assert set(t12) == {"nummer", "titel", "typ", "prioritaet", "status", "beschreibung", "version", "im_durchlauf", "angelegt", "geaendert", "kommentare"}
    assert t12["kommentare"][0]["text"] == "Ein Kommentar" and t12["angelegt"] == "2026-11-28T09:30:00Z" and t12["version"] == 2


def test_board_sieht_nie_ein_anderer_platz(client, d):
    d16 = oeffnen(client, 16)
    anlegen_http(client, d16, 16, titel="Geheim auf Platz sechzehn")
    j = _board(client, _token(client)).json()
    assert j["platz"] == PLATZ and "Geheim auf Platz sechzehn" not in str(j)
    j16 = _board(client, _token(client, 16)).json()
    assert j16["platz"] == 16 and "Geheim auf Platz sechzehn" in str(j16)


def test_platz_kommt_nur_aus_dem_token_nie_aus_der_eingabe(client, d):
    token = _token(client)
    for kopf in ({"X-WAMO-Platz": "16"}, {"X-Platz": "16"}, {"X-Platz-Nummer": "16"}):
        assert _board(client, token).json()["platz"] == PLATZ
        assert client.get("/api/browser/board", headers={"X-Platz-Token": token, **kopf}).json()["platz"] == PLATZ
    assert client.get("/api/browser/board?platz=16", headers={"X-Platz-Token": token}).status_code == 422
    assert client.get("/api/browser/plaetze/16/board", headers={"X-Platz-Token": token}).status_code == 404


def test_board_antwortkoepfe(client, d):
    r = _board(client, _token(client))
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("kopf", [{}, {"X-Platz-Token": ""}, {"X-Platz-Token": "abc"}, {"X-Platz-Token": "0" * 32}, {"X-Platz-Token": "' OR 1=1 --"},
                                  {"X-Platz-Token": "A" * 32}, {"Authorization": "Bearer " + "0" * 32}])
def test_ohne_oder_mit_falschem_token_ist_401(client, d, kopf):
    r = client.get("/api/browser/board", headers=kopf)
    assert r.status_code == 401 and r.json()["fehler"] == "token_ungueltig" and set(r.json()) == {"fehler", "text"}


def test_kern_bearer_und_betreuer_token_oeffnen_den_browser_nie(client, d):
    for t in (KERN_TOKEN, BETREUER_TOKEN):
        assert client.get("/api/browser/board", headers={"X-Platz-Token": t}).status_code == 401
        assert client.get("/api/browser/board", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_token_in_der_adresszeile_gilt_nie(client, d):
    token = _token(client)
    r = client.get(f"/api/browser/board?token={token}")
    assert r.status_code == 401
    r = client.get(f"/api/browser/board?token={token}", headers={"X-Platz-Token": token})
    assert r.status_code == 422 and token not in r.text


def test_doppelter_tokenkopf_ist_401(client, d):
    token = _token(client)
    r = client.get("/api/browser/board", headers=[("X-Platz-Token", token), ("X-Platz-Token", token)])
    assert r.status_code == 401


@pytest.mark.parametrize("methode", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_browser_kann_nicht_schreiben_und_nicht_loeschen(client, d, methode):
    token = _token(client)
    r = client.request(methode, "/api/browser/board", headers={"X-Platz-Token": token}, content=b"{}")
    assert r.status_code == 405 and r.headers["allow"] == "GET"
    assert _board(client, token).json()["tickets"][0]["version"] == 1


def test_neuer_token_macht_den_alten_ungueltig(client, d):
    alt = _token(client)
    neu = _token(client)
    assert alt != neu
    assert _board(client, alt).status_code == 401 and _board(client, neu).status_code == 200


def test_token_hat_128_bit_und_wird_nur_als_hash_gespeichert(client, d, konfig):
    token = _token(client)
    assert len(token) == 32 and int(token, 16) >= 0
    for datei in konfig.daten.rglob("*"):
        if datei.is_file():
            assert token.encode() not in datei.read_bytes(), datei.name


def test_board_zeigt_den_zustand_des_durchlaufs(client, d):
    token = _token(client)
    client.post(f"/api/betreuer/plaetze/{PLATZ}/durchlauf-beenden", headers=betreuer_kopf())
    assert _board(client, token).json()["durchlauf"] == {"offen": False, "einwilligung_version": 1}


def test_board_nutztext_bleibt_text(client, d):
    token = _token(client)
    anlegen_http(client, d, titel="<script>alert(1)</script>", beschreibung='"><img src=x onerror=alert(1)>')
    r = _board(client, token)
    assert r.headers["content-type"].startswith("application/json") and "<script>alert(1)</script>" in r.json()["tickets"][-1]["titel"]
    assert ANLEGEN["titel"] not in r.text
