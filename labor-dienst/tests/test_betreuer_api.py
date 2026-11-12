"""Betreuer-Funktionen unter /api/betreuer/ (D9): eigener Token, Wirkung auf V1, Protokoll nur mit Metadaten (D10)."""
from __future__ import annotations

import json

import pytest
from hilfen import BETREUER_TOKEN, KERN_TOKEN, anlegen_http, betreuer_kopf, kern_kopf, oeffnen, schreib_kopf

PLATZ = 3
AKTIONEN = ("durchlauf-beenden", "neuer-durchlauf", "zuruecksetzen", "daten-loeschen", "platz-token")


def _v1(client, platz: int = PLATZ) -> dict:
    return client.get("/api/kern/durchlauf", headers=kern_kopf(platz)).json()


def _zustand(client, platz: int = PLATZ) -> dict:
    return next(p for p in client.get("/api/betreuer/plaetze", headers=betreuer_kopf()).json()["plaetze"] if p["platz"] == platz)


# ---------- Zugang ----------
def test_ohne_token_ist_401_mit_falschem_token_403(client):
    for aufruf in (lambda h: client.get("/api/betreuer/plaetze", headers=h), *(lambda h, a=a: client.post(f"/api/betreuer/plaetze/{PLATZ}/{a}", headers=h, json={"einwilligung_version": 1}) for a in AKTIONEN)):
        assert aufruf({}).status_code == 401
        assert aufruf({"Authorization": "Basic abc"}).status_code == 401
        for falsch in ("0" * 40, KERN_TOKEN, BETREUER_TOKEN + "x", BETREUER_TOKEN[:-1], "' OR 1=1 --"):
            r = aufruf({"Authorization": f"Bearer {falsch}"})
            assert r.status_code == 403 and set(r.json()) == {"fehler", "text"}, falsch


def test_platz_token_oeffnet_den_betreuer_nie_mit_403(client):
    oeffnen(client, PLATZ)
    token = client.post(f"/api/betreuer/plaetze/{PLATZ}/platz-token", headers=betreuer_kopf()).json()["platz_token"]
    assert client.get("/api/betreuer/plaetze", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert client.post(f"/api/betreuer/plaetze/{PLATZ}/daten-loeschen", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert client.get("/api/betreuer/plaetze", headers={"X-Platz-Token": token}).status_code == 401
    assert _zustand(client)["tickets"] == 6


def test_kern_bearer_oeffnet_den_betreuer_nie_und_wirkt_nicht(client):
    d = oeffnen(client, PLATZ)
    anlegen_http(client, d)
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/daten-loeschen", headers={**kern_kopf(), "X-WAMO-Durchlauf": d})
    assert r.status_code == 403 and _zustand(client)["tickets"] == 7


def test_betreuer_token_oeffnet_den_kern_nie(client):
    r = client.get("/api/kern/durchlauf", headers={"Authorization": f"Bearer {BETREUER_TOKEN}", "X-WAMO-Platz": "3"})
    assert r.status_code == 401


# ---------- Pfade ----------
@pytest.mark.parametrize("platz", ["0", "17", "99", "03", "abc", "1.5"])
def test_unbekannter_platz_ist_404(client, platz):
    r = client.post(f"/api/betreuer/plaetze/{platz}/zuruecksetzen", headers=betreuer_kopf())
    assert r.status_code == 404 and r.json()["fehler"] == "platz_unbekannt"


@pytest.mark.parametrize("methode,pfad,erlaubt", [("POST", "/api/betreuer/plaetze", "GET"), ("DELETE", "/api/betreuer/plaetze", "GET"),
                                                  ("GET", "/api/betreuer/plaetze/3/zuruecksetzen", "POST"), ("DELETE", "/api/betreuer/plaetze/3/daten-loeschen", "POST"),
                                                  ("PUT", "/api/betreuer/plaetze/3/platz-token", "POST")])
def test_405_mit_allow(client, methode, pfad, erlaubt):
    r = client.request(methode, pfad, headers=betreuer_kopf())
    assert r.status_code == 405 and r.headers["allow"] == erlaubt


def test_unbekannte_betreuer_pfade_sind_404(client):
    for pfad in ("/api/betreuer", "/api/betreuer/", "/api/betreuer/plaetze/3", "/api/betreuer/plaetze/3/loeschen", "/api/betreuer/plaetze/3/zuruecksetzen/x"):
        assert client.post(pfad, headers=betreuer_kopf()).status_code == 404


# ---------- Wirkung ----------
def test_plaetze_liste_zeigt_alle_16_ohne_texte(client):
    d = oeffnen(client, PLATZ)
    anlegen_http(client, d, titel="Geheimer Titel")
    client.post("/api/kern/tickets/LAB-12/kommentare", json={"erwartete_version": 1, "text": "Geheimer Kommentar"}, headers=schreib_kopf(d))
    r = client.get("/api/betreuer/plaetze", headers=betreuer_kopf())
    assert r.status_code == 200 and "Geheim" not in r.text
    plaetze = r.json()["plaetze"]
    assert [p["platz"] for p in plaetze] == list(range(1, 17))
    p3 = plaetze[2]
    assert set(p3) == {"platz", "durchlauf_id", "offen", "einwilligung_version", "einwilligung_zeit", "tickets", "kommentare", "hat_platz_token", "reserviert"}
    assert (p3["offen"], p3["einwilligung_version"], p3["tickets"], p3["kommentare"], p3["hat_platz_token"], p3["reserviert"]) == (True, 1, 7, 1, False, False)
    assert plaetze[0]["offen"] is False and plaetze[0]["einwilligung_version"] is None and plaetze[0]["tickets"] == 6
    assert [p["reserviert"] for p in plaetze if p["reserviert"]] == [True, True] and plaetze[14]["reserviert"] and plaetze[15]["reserviert"]


def test_neuer_durchlauf_setzt_zurueck_und_erfasst_die_einwilligung(client):
    alt = oeffnen(client, PLATZ, version=1)
    anlegen_http(client, alt)
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/neuer-durchlauf", json={"einwilligung_version": 4}, headers=betreuer_kopf())
    assert r.status_code == 200
    z = r.json()
    assert z["platz"] == PLATZ and z["offen"] is True and z["einwilligung_version"] == 4 and z["tickets"] == 6 and z["durchlauf_id"] != alt
    v1 = _v1(client)
    assert v1 == {"durchlauf_id": z["durchlauf_id"], "offen": True, "einwilligung_version": 4, "einwilligung_zeit": "2026-11-28T09:30:00Z"}
    assert anlegen_http(client, alt).status_code == 409
    assert anlegen_http(client, z["durchlauf_id"]).json()["nummer"] == "LAB-30"


@pytest.mark.parametrize("koerper", [None, {}, {"einwilligung_version": 0}, {"einwilligung_version": -1}, {"einwilligung_version": "1"}, {"einwilligung_version": True},
                                     {"einwilligung_version": 1.5}, {"einwilligung_version": None}, {"einwilligung_version": 1, "extra": 1}, {"version": 1},
                                     {"einwilligung_version": 2 ** 40}, [], "x"])
def test_neuer_durchlauf_verlangt_eine_gueltige_einwilligung(client, koerper):
    vorher = _v1(client)
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/neuer-durchlauf", json=koerper, headers=betreuer_kopf()) if koerper is not None else \
        client.post(f"/api/betreuer/plaetze/{PLATZ}/neuer-durchlauf", headers=betreuer_kopf())
    assert r.status_code == 422 and r.json()["fehler"] == "eingabe_ungueltig"
    assert _v1(client) == vorher  # ohne gültige Einwilligung bleibt alles, wie es war


def test_neuer_durchlauf_verlangt_json(client):
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/neuer-durchlauf", content=b'{"einwilligung_version": 1}', headers=betreuer_kopf(**{"Content-Type": "text/plain"}))
    assert r.status_code == 422


def test_durchlauf_beenden(client):
    d = oeffnen(client, PLATZ)
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/durchlauf-beenden", headers=betreuer_kopf())
    assert r.status_code == 200 and r.json()["offen"] is False and r.json()["durchlauf_id"] == d
    v1 = _v1(client)
    assert v1["offen"] is False and v1["durchlauf_id"] == d and v1["einwilligung_version"] == 1
    assert anlegen_http(client, d).status_code == 409


def test_zuruecksetzen_stellt_den_seed_wieder_her(client):
    d = oeffnen(client, PLATZ)
    anlegen_http(client, d)
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/zuruecksetzen", headers=betreuer_kopf())
    assert r.status_code == 200 and r.json()["tickets"] == 6 and r.json()["durchlauf_id"] == d and r.json()["offen"] is True
    assert client.get("/api/kern/tickets/LAB-30", headers=kern_kopf()).status_code == 404


def test_daten_loeschen(client):
    d = oeffnen(client, PLATZ)
    anlegen_http(client, d, titel="Löschen")
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/daten-loeschen", headers=betreuer_kopf())
    z = r.json()
    assert r.status_code == 200 and z["tickets"] == 0 and z["kommentare"] == 0 and z["offen"] is False and z["einwilligung_version"] is None and z["durchlauf_id"] != d
    assert _v1(client) == {"durchlauf_id": z["durchlauf_id"], "offen": False, "einwilligung_version": None, "einwilligung_zeit": None}
    assert client.get("/api/kern/tickets", headers=kern_kopf()).json() == {"tickets": [], "gesamt": 0}
    assert anlegen_http(client, d).status_code == 409


def test_aktionen_wirken_nur_auf_den_genannten_platz(client):
    d3, d4 = oeffnen(client, 3), oeffnen(client, 4)
    anlegen_http(client, d4, 4)
    client.post("/api/betreuer/plaetze/3/daten-loeschen", headers=betreuer_kopf())
    assert _zustand(client, 4)["tickets"] == 7 and _zustand(client, 4)["offen"] is True


def test_platz_token_wird_einmal_ausgegeben(client):
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/platz-token", headers=betreuer_kopf())
    assert r.status_code == 200 and set(r.json()) == {"platz", "platz_token"} and r.json()["platz"] == PLATZ
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["platz_token"] not in client.get("/api/betreuer/plaetze", headers=betreuer_kopf()).text
    assert _zustand(client)["hat_platz_token"] is True


@pytest.mark.parametrize("inhalt", [b"", b"{}", b" {} "])
def test_aktionen_ohne_koerper_nehmen_keinen_inhalt_an(client, inhalt):
    assert client.post(f"/api/betreuer/plaetze/{PLATZ}/zuruecksetzen", content=inhalt, headers=betreuer_kopf()).status_code == 200


def test_aktionen_mit_unbekanntem_inhalt_sind_422(client):
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/daten-loeschen", content=b'{"alles": true}', headers=betreuer_kopf(**{"Content-Type": "application/json"}))
    assert r.status_code == 422


def test_zu_grosser_betreuer_inhalt_ist_413(client):
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/neuer-durchlauf", content=b"x" * 5000, headers=betreuer_kopf(**{"Content-Type": "application/json"}))
    assert r.status_code == 413 and r.json()["fehler"] == "zu_gross"


def test_antworten_tragen_die_schutzkoepfe(client):
    for r in (client.get("/api/betreuer/plaetze", headers=betreuer_kopf()), client.get("/api/betreuer/plaetze")):
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"


# ---------- Protokoll (D10) ----------
def _protokoll(client, datei: str = "dienst.jsonl") -> list[dict]:
    dienst = client.app.state.dienst
    dienst.protokoll.abwarten()
    pfad = dienst.protokoll.ordner / datei
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines()] if pfad.exists() else []


def test_betreuer_aufrufe_stehen_mit_zeit_platz_und_art_im_protokoll(client, konfig):
    oeffnen(client, PLATZ)
    client.post(f"/api/betreuer/plaetze/{PLATZ}/durchlauf-beenden", headers=betreuer_kopf())
    client.post(f"/api/betreuer/plaetze/{PLATZ}/platz-token", headers=betreuer_kopf())
    client.get("/api/betreuer/plaetze", headers=betreuer_kopf())
    ereignisse = _protokoll(client, "betreuer.jsonl")  # eigene Datei, die nie rotiert
    assert [(e.get("aktion"), e.get("platz")) for e in ereignisse] == [("neuer_durchlauf", 3), ("durchlauf_beenden", 3), ("platz_token", 3), ("plaetze", None)]
    assert all(e["art"] == "betreuer" and e["zeit"] == "2026-11-28T09:30:00Z" and e["status"] == 200 for e in ereignisse)


def test_abgelehnte_betreuer_aufrufe_stehen_ohne_token_im_protokoll(client, konfig):
    client.post(f"/api/betreuer/plaetze/{PLATZ}/daten-loeschen", headers={"Authorization": f"Bearer {KERN_TOKEN}"})
    ereignisse = _protokoll(client)
    roh = (konfig.daten / "protokoll" / "dienst.jsonl").read_text(encoding="utf-8")
    assert KERN_TOKEN not in roh and BETREUER_TOKEN not in roh
    assert any(e["art"] == "betreuer_abgelehnt" and e["status"] == 403 for e in ereignisse)  # abgelehnte Aufrufe gehören nie in die Betreuer-Spur
    assert _protokoll(client, "betreuer.jsonl") == []


def test_protokoll_enthaelt_nie_ticket_kommentar_oder_einwilligungstexte_und_tokens(client, konfig):
    d = oeffnen(client, PLATZ, version=7)
    token = client.post(f"/api/betreuer/plaetze/{PLATZ}/platz-token", headers=betreuer_kopf()).json()["platz_token"]
    anlegen_http(client, d, titel="Streng geheimer Titel", beschreibung="Streng geheime Beschreibung")
    client.post("/api/kern/tickets/LAB-12/kommentare", json={"erwartete_version": 1, "text": "Streng geheimer Kommentar"}, headers=schreib_kopf(d))
    client.get("/api/kern/tickets?q=Streng+geheim", headers=kern_kopf())
    client.get("/api/kern/tickets/Streng-geheim", headers=kern_kopf())
    client.get("/api/browser/board", headers={"X-Platz-Token": token})
    client.get(f"/api/browser/board?token={token}")
    client.app.state.dienst.protokoll.beenden()
    roh = "\n".join(p.read_text(encoding="utf-8") for p in (konfig.daten / "protokoll").glob("*") if p.is_file())
    for verboten in ("Streng", "geheim", token, KERN_TOKEN, BETREUER_TOKEN, d, "Bearer", "Authorization"):
        assert verboten not in roh, verboten
    assert roh.count("\n") >= 6
    for zeile in (z for z in roh.splitlines() if z.strip()):
        e = json.loads(zeile)
        assert set(e) <= {"zeit", "art", "bereich", "methode", "status", "platz", "ms", "aktion", "abgelehnt", "anzahl"}, e


def test_anfragen_stehen_als_metadaten_im_protokoll(client, konfig):
    client.get("/api/kern/durchlauf", headers=kern_kopf(5))
    e = [x for x in _protokoll(client) if x["art"] == "anfrage"][-1]
    assert (e["bereich"], e["methode"], e["status"], e["platz"]) == ("kern", "GET", 200, 5) and isinstance(e["ms"], (int, float))


# ---------- Platz-Token und Besucherwechsel (Befund 1 des Reviews) ----------
def _neuer_platz_token(client, platz: int = PLATZ) -> str:
    r = client.post(f"/api/betreuer/plaetze/{platz}/platz-token", headers=betreuer_kopf())
    assert r.status_code == 200
    return r.json()["platz_token"]


def _board_status(client, token: str) -> int:
    return client.get("/api/browser/board", headers={"X-Platz-Token": token}).status_code


def test_neuer_durchlauf_entzieht_dem_alten_platz_token_den_zugang(client):
    oeffnen(client, PLATZ)
    alt = _neuer_platz_token(client)
    assert _board_status(client, alt) == 200
    oeffnen(client, PLATZ)  # der nächste Besucher
    assert _board_status(client, alt) == 401
    assert _zustand(client)["hat_platz_token"] is False
    neu = _neuer_platz_token(client)
    assert neu != alt and _board_status(client, neu) == 200 and _board_status(client, alt) == 401


def test_daten_loeschen_entzieht_dem_platz_token_den_zugang(client):
    oeffnen(client, PLATZ)
    alt = _neuer_platz_token(client)
    assert client.post(f"/api/betreuer/plaetze/{PLATZ}/daten-loeschen", headers=betreuer_kopf()).status_code == 200
    assert _board_status(client, alt) == 401 and _zustand(client)["hat_platz_token"] is False


def test_durchlauf_beenden_und_zuruecksetzen_behalten_den_platz_token(client):
    oeffnen(client, PLATZ)
    token = _neuer_platz_token(client)
    client.post(f"/api/betreuer/plaetze/{PLATZ}/zuruecksetzen", headers=betreuer_kopf())
    client.post(f"/api/betreuer/plaetze/{PLATZ}/durchlauf-beenden", headers=betreuer_kopf())
    assert _board_status(client, token) == 200


def test_besucherwechsel_trifft_nur_den_eigenen_platz(client):
    oeffnen(client, 3)
    oeffnen(client, 4)
    t3, t4 = _neuer_platz_token(client, 3), _neuer_platz_token(client, 4)
    oeffnen(client, 3)
    assert _board_status(client, t3) == 401 and _board_status(client, t4) == 200


def test_ungueltiger_neuer_durchlauf_laesst_den_token_stehen(client):
    oeffnen(client, PLATZ)
    token = _neuer_platz_token(client)
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/neuer-durchlauf", json={"einwilligung_version": 0}, headers=betreuer_kopf())
    assert r.status_code == 422 and _board_status(client, token) == 200


# ---------- Einwilligungsversion nur wachsend (Befund 7 des Reviews, Vertrag Abschnitt 8) ----------
def _neuer_durchlauf(client, platz: int, version: int):
    return client.post(f"/api/betreuer/plaetze/{platz}/neuer-durchlauf", json={"einwilligung_version": version}, headers=betreuer_kopf())


def test_einwilligungsversion_darf_nur_wachsen(client):
    oeffnen(client, PLATZ, version=5)
    alt = _zustand(client)
    token = _neuer_platz_token(client)
    r = _neuer_durchlauf(client, PLATZ, 4)
    assert r.status_code == 422 and r.json()["fehler"] == "eingabe_ungueltig" and "Einwilligungsversion" in r.json()["text"]
    assert _zustand(client) == {**alt, "hat_platz_token": True} and _board_status(client, token) == 200  # nichts hat sich geändert, auch der Token nicht
    assert _neuer_durchlauf(client, PLATZ, 5).status_code == 200  # dieselbe Version gilt für den nächsten Besucher
    assert _neuer_durchlauf(client, PLATZ, 6).status_code == 200
    assert _neuer_durchlauf(client, PLATZ, 5).status_code == 422


def test_einwilligungsversion_gilt_fuer_alle_plaetze_zusammen(client):
    oeffnen(client, 3, version=6)
    assert _neuer_durchlauf(client, 4, 5).status_code == 422
    assert _neuer_durchlauf(client, 4, 6).status_code == 200
    assert _zustand(client, 4)["einwilligung_version"] == 6


def test_hoechste_einwilligungsversion_bleibt_nach_dem_neustart(konfig, uhr):
    from fastapi.testclient import TestClient

    from wamo_labor_dienst.app import create_app

    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:
        assert _neuer_durchlauf(c, 3, 7).status_code == 200
    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:
        assert _neuer_durchlauf(c, 3, 6).status_code == 422
        assert _neuer_durchlauf(c, 3, 8).status_code == 200


def test_abgelehnte_einwilligung_steht_in_der_betreuer_spur_mit_status(client):
    oeffnen(client, PLATZ, version=5)
    _neuer_durchlauf(client, PLATZ, 4)
    assert [e["status"] for e in _protokoll(client, "betreuer.jsonl")] == [200, 422]
