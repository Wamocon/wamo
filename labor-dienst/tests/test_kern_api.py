"""Die sieben Aufrufe V1 bis V7 unter /api/kern/ exakt nach Vertrag: Pfade, Methoden, Köpfe, Fehlercodes, Reihenfolge 2.2."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from hilfen import ANLEGEN, BETREUER_TOKEN, KERN_TOKEN, anlegen_http, betreuer_kopf, kern_kopf, oeffnen, schreib_kopf

from wamo_labor_dienst.datenbank import PlatzDB
from wamo_labor_dienst.regeln import UUID_RE

PLATZ = 3
TICKET_FELDER = {"nummer", "titel", "typ", "prioritaet", "status", "beschreibung", "version", "im_durchlauf"}


@pytest.fixture
def d(client) -> str:
    """Platz 3 mit offenem Durchlauf, liefert die durchlauf_id."""
    return oeffnen(client, PLATZ)


def _fehler(r, status: int, code: str) -> dict:
    assert (r.status_code, r.json().get("fehler")) == (status, code), r.text
    assert set(r.json()) == {"fehler", "text"}
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].split(";")[0] == "application/json"
    return r.json()


# ---------- V1 ----------
def test_v1_liefert_den_durchlauf(client, d):
    r = client.get("/api/kern/durchlauf", headers=kern_kopf())
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"durchlauf_id", "offen", "einwilligung_version", "einwilligung_zeit"}
    assert j["durchlauf_id"] == d and UUID_RE.fullmatch(j["durchlauf_id"]) and j["offen"] is True and j["einwilligung_version"] == 1
    assert j["einwilligung_zeit"] == "2026-11-28T09:30:00Z"


def test_v1_neuer_platz_ist_geschlossen_ohne_einwilligung(client):
    j = client.get("/api/kern/durchlauf", headers=kern_kopf(7)).json()
    assert j["offen"] is False and j["einwilligung_version"] is None and j["einwilligung_zeit"] is None


def test_jede_antwort_traegt_no_store_und_nosniff(client, d):
    for r in (client.get("/api/kern/durchlauf", headers=kern_kopf()), client.get("/api/kern/durchlauf"), client.get("/gibt-es-nicht"),
              anlegen_http(client, d), client.get("/api/kern/tickets/LAB-12", headers=kern_kopf())):
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-type"].startswith("application/json")


def test_v1_mit_abfrage_ist_422(client, d):
    _fehler(client.get("/api/kern/durchlauf?x=1", headers=kern_kopf()), 422, "eingabe_ungueltig")


# ---------- Pfade und Methoden ----------
@pytest.mark.parametrize("pfad", ["/", "/api", "/api/kern", "/api/kern/", "/api/kern/gibt-es-nicht", "/api/kern/durchlauf/1", "/api/kern/durchlauf/",
                                  "/api/kern/tickets/", "/api/kern/tickets/LAB-12/loeschen", "/api/kern/tickets/LAB-12/status/x", "/docs", "/openapi.json",
                                  "/api/kern//durchlauf", "/API/KERN/durchlauf"])
def test_unbekannter_pfad_ist_404_nicht_erlaubt_ohne_umleitung(client, pfad):
    r = client.get(pfad, headers=kern_kopf(), follow_redirects=False)
    _fehler(r, 404, "nicht_erlaubt")


@pytest.mark.parametrize("pfad,methode,erlaubt", [
    ("/api/kern/durchlauf", "POST", "GET"), ("/api/kern/durchlauf", "DELETE", "GET"), ("/api/kern/durchlauf", "PUT", "GET"),
    ("/api/kern/durchlauf", "PATCH", "GET"), ("/api/kern/durchlauf", "HEAD", "GET"), ("/api/kern/durchlauf", "OPTIONS", "GET"),
    ("/api/kern/tickets", "DELETE", "GET, POST"), ("/api/kern/tickets", "PUT", "GET, POST"), ("/api/kern/tickets", "PATCH", "GET, POST"),
    ("/api/kern/tickets/LAB-12", "DELETE", "GET, PATCH"), ("/api/kern/tickets/LAB-12", "POST", "GET, PATCH"), ("/api/kern/tickets/LAB-12", "PUT", "GET, PATCH"),
    ("/api/kern/tickets/LAB-12/status", "GET", "POST"), ("/api/kern/tickets/LAB-12/status", "DELETE", "POST"), ("/api/kern/tickets/LAB-12/status", "PATCH", "POST"),
    ("/api/kern/tickets/LAB-12/kommentare", "GET", "POST"), ("/api/kern/tickets/LAB-12/kommentare", "DELETE", "POST"),
    ("/api/kern/tickets/LAB-12", "TRACE", "GET, PATCH"), ("/api/kern/tickets/LAB-12", "PURGE", "GET, PATCH")])
def test_bekannter_pfad_mit_anderer_methode_ist_405_mit_allow(client, pfad, methode, erlaubt):
    r = client.request(methode, pfad, headers=kern_kopf())
    assert r.status_code == 405 and r.headers["allow"] == erlaubt
    if methode != "HEAD":
        _fehler(r, 405, "nicht_erlaubt")


def test_methode_vor_bearer_und_pfad_vor_bearer(client):
    _fehler(client.get("/api/kern/gibt-es-nicht"), 404, "nicht_erlaubt")
    _fehler(client.post("/api/kern/durchlauf"), 405, "nicht_erlaubt")


@pytest.mark.parametrize("name", ["X-HTTP-Method-Override", "X-HTTP-Method", "X-Method-Override"])
def test_method_override_wird_ignoriert(client, d, name):
    assert client.get("/api/kern/tickets/LAB-12", headers=kern_kopf(**{name: "DELETE"})).status_code == 200
    r = client.post("/api/kern/tickets", json=ANLEGEN, headers=schreib_kopf(d, **{name: "DELETE"}))
    assert r.status_code == 201
    assert client.get("/api/kern/tickets?limit=10", headers=kern_kopf()).json()["gesamt"] == 7


# ---------- Bearer und Platz ----------
@pytest.mark.parametrize("kopf", [{}, {"Authorization": "Bearer"}, {"Authorization": "Bearer "}, {"Authorization": f"Bearer {KERN_TOKEN}x"},
                                  {"Authorization": f"Bearer {KERN_TOKEN[:-1]}"}, {"Authorization": f"Basic {KERN_TOKEN}"}, {"Authorization": KERN_TOKEN},
                                  {"Authorization": f"Bearer  {KERN_TOKEN}"}, {"Authorization": f"Bearer {BETREUER_TOKEN}"},
                                  {"Authorization": "Bearer " + "0" * 32}, {"Authorization": "Bearer ' OR 1=1 --"}])
def test_falscher_bearer_ist_401_auf_allen_pfaden(client, d, kopf):
    for methode, pfad in (("GET", "/api/kern/durchlauf"), ("GET", "/api/kern/tickets"), ("GET", "/api/kern/tickets/LAB-12"), ("POST", "/api/kern/tickets"),
                          ("PATCH", "/api/kern/tickets/LAB-12"), ("POST", "/api/kern/tickets/LAB-12/status"), ("POST", "/api/kern/tickets/LAB-12/kommentare")):
        r = client.request(methode, pfad, headers={"X-WAMO-Platz": str(PLATZ), **kopf}, content=b"{}")
        _fehler(r, 401, "token_ungueltig")


def test_zwei_authorization_koepfe_sind_401(client):
    r = client.get("/api/kern/durchlauf", headers=[("Authorization", f"Bearer {KERN_TOKEN}"), ("Authorization", f"Bearer {KERN_TOKEN}"), ("X-WAMO-Platz", "3")])
    _fehler(r, 401, "token_ungueltig")


def test_platz_token_und_betreuer_token_oeffnen_den_kern_nie(client, d):
    token = client.post(f"/api/betreuer/plaetze/{PLATZ}/platz-token", headers=betreuer_kopf()).json()["platz_token"]
    for t in (token, BETREUER_TOKEN):
        _fehler(client.get("/api/kern/durchlauf", headers={"Authorization": f"Bearer {t}", "X-WAMO-Platz": str(PLATZ)}), 401, "token_ungueltig")
        _fehler(client.get("/api/kern/durchlauf", headers={"X-Platz-Token": t, "X-WAMO-Platz": str(PLATZ)}), 401, "token_ungueltig")


@pytest.mark.parametrize("platz", ["0", "17", "99", "03", "abc", " 3", "3 ", "-1", "1.5", "", "1" * 100])
def test_unbekannter_platz_ist_404_platz_unbekannt(client, platz):
    _fehler(client.get("/api/kern/durchlauf", headers=kern_kopf(platz)), 404, "platz_unbekannt")


def test_fehlender_oder_doppelter_platzkopf(client):
    _fehler(client.get("/api/kern/durchlauf", headers={"Authorization": f"Bearer {KERN_TOKEN}"}), 404, "platz_unbekannt")
    r = client.get("/api/kern/durchlauf", headers=[("Authorization", f"Bearer {KERN_TOKEN}"), ("X-WAMO-Platz", "3"), ("X-WAMO-Platz", "4")])
    _fehler(r, 404, "platz_unbekannt")


@pytest.mark.parametrize("platz", [1, 16])
def test_plaetze_1_und_16_gibt_es(client, platz):
    assert client.get("/api/kern/durchlauf", headers=kern_kopf(platz)).status_code == 200


def test_bearer_vor_platz(client):
    _fehler(client.get("/api/kern/durchlauf", headers={"X-WAMO-Platz": "99"}), 401, "token_ungueltig")


# ---------- V2 ----------
def test_v2_liste_neueste_zuerst_mit_allen_feldern(client, d):
    j = client.get("/api/kern/tickets", headers=kern_kopf()).json()
    assert set(j) == {"tickets", "gesamt"} and j["gesamt"] == 6
    assert [t["nummer"] for t in j["tickets"]] == ["LAB-25", "LAB-20", "LAB-17", "LAB-14", "LAB-12", "LAB-11"]
    assert all(set(t) == TICKET_FELDER for t in j["tickets"])


def test_v2_filter_limit_und_woertliche_suche(client, d):
    anlegen_http(client, d, titel="100% fertig")
    anlegen_http(client, d, titel="1000 fertig")
    j = client.get("/api/kern/tickets", params={"q": "%", "limit": "5"}, headers=kern_kopf()).json()
    assert [t["titel"] for t in j["tickets"]] == ["100% fertig"] and j["gesamt"] == 1
    assert client.get("/api/kern/tickets?q=%25&limit=5", headers=kern_kopf()).json()["gesamt"] == 1
    j = client.get("/api/kern/tickets?typ=bug&status=in_arbeit&nummer=LAB-12", headers=kern_kopf()).json()
    assert [t["nummer"] for t in j["tickets"]] == ["LAB-12"]
    assert client.get("/api/kern/tickets?nur_durchlauf=true", headers=kern_kopf()).json()["gesamt"] == 2
    j = client.get("/api/kern/tickets?limit=2", headers=kern_kopf()).json()
    assert len(j["tickets"]) == 2 and j["gesamt"] == 8


@pytest.mark.parametrize("abfrage", ["limit=0", "limit=11", "limit=abc", "typ=BUG", "typ=a&typ=b", "q=a&q=b", "foo=1", "nummer=LAB-012", "nummer=lab-12",
                                     "nur_durchlauf=ja", "q=" + "x" * 101, "q=%FF", "_method=DELETE", "limit="])
def test_v2_ungueltige_abfrage_ist_422(client, d, abfrage):
    _fehler(client.get(f"/api/kern/tickets?{abfrage}", headers=kern_kopf()), 422, "eingabe_ungueltig")


def test_v2_antwort_wird_ueber_60_kb_gekuerzt_und_ist_utf8(client, d):
    for _ in range(10):
        assert anlegen_http(client, d, titel="G" * 100, beschreibung="😀" * 1500).status_code == 201
    r = client.get("/api/kern/tickets", headers=kern_kopf())
    assert len(r.content) <= 60 * 1024 and b"\\u" not in r.content
    j = r.json()
    assert 0 < len(j["tickets"]) < 10 and j["gesamt"] == 16 and j["tickets"][0]["nummer"] == "LAB-39"


# ---------- V3 ----------
def test_v3_ticket_mit_kommentaren(client, d):
    r = client.get("/api/kern/tickets/LAB-12", headers=kern_kopf())
    assert r.status_code == 200
    j = r.json()
    assert set(j) == TICKET_FELDER | {"kommentare"} and j["status"] == "in_arbeit" and j["kommentare"] == [] and j["im_durchlauf"] is False


def test_v3_unbekannt_und_falsche_form(client, d):
    _fehler(client.get("/api/kern/tickets/LAB-9999", headers=kern_kopf()), 404, "ticket_unbekannt")
    for nummer in ("LAB-0", "lab-1", "LAB-1a", "LAB-01", "LAB-10000", "12", "LAB-"):
        _fehler(client.get(f"/api/kern/tickets/{nummer}", headers=kern_kopf()), 422, "eingabe_ungueltig")
    _fehler(client.get("/api/kern/tickets/LAB-12?x=1", headers=kern_kopf()), 422, "eingabe_ungueltig")


def test_v3_fehlertext_spiegelt_den_pfad_nie(client, d):
    r = client.get("/api/kern/tickets/<img src=x onerror=alert(1)>", headers=kern_kopf())
    _fehler(r, 422, "eingabe_ungueltig")
    assert "<img" not in r.text and "alert" not in r.text


# ---------- V4 bis V7 ----------
def test_v4_legt_an(client, d):
    r = client.post("/api/kern/tickets", json=ANLEGEN, headers=schreib_kopf(d))
    assert r.status_code == 201 and "idempotent-replayed" not in r.headers
    assert r.json() == {"nummer": "LAB-30", **{k: ANLEGEN[k] for k in ("titel", "typ", "prioritaet", "beschreibung")},
                        "status": "zu_erledigen", "version": 1, "im_durchlauf": True}


def test_v5_v6_v7_aendern_status_kommentar(client, d):
    r = client.patch("/api/kern/tickets/LAB-12", json={"erwartete_version": 1, "felder": {"titel": "Neu x", "prioritaet": "hoechste"}}, headers=schreib_kopf(d))
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["titel"] == "Neu x" and set(r.json()) == TICKET_FELDER
    r = client.post("/api/kern/tickets/LAB-12/status", json={"erwartete_version": 2, "status": "erledigt"}, headers=schreib_kopf(d))
    assert r.status_code == 200 and r.json()["status"] == "erledigt" and r.json()["version"] == 3
    r = client.post("/api/kern/tickets/LAB-12/kommentare", json={"erwartete_version": 3, "text": "Ein Kommentar"}, headers=schreib_kopf(d))
    assert r.status_code == 201 and set(r.json()) == {"kommentar_id", "ticket"} and r.json()["kommentar_id"] == 1 and r.json()["ticket"]["version"] == 4
    assert client.get("/api/kern/tickets/LAB-12", headers=kern_kopf()).json()["kommentare"] == [{"kommentar_id": 1, "text": "Ein Kommentar"}]


def test_idempotenz_gleiche_antwort_und_kopf(client, d):
    kopf = schreib_kopf(d)
    erst = client.post("/api/kern/tickets", json=ANLEGEN, headers=kopf)
    zweit = client.post("/api/kern/tickets", json=ANLEGEN, headers=kopf)
    assert (zweit.status_code, zweit.content) == (erst.status_code, erst.content) and zweit.headers["idempotent-replayed"] == "true"
    assert client.get("/api/kern/tickets", headers=kern_kopf()).json()["gesamt"] == 7
    anders = client.post("/api/kern/tickets", json={**ANLEGEN, "titel": "Anderer"}, headers=kopf)
    _fehler(anders, 422, "eingabe_ungueltig")


def test_409_durchlauf_beendet_und_ticket_veraendert(client, d):
    fremd = str(uuid.uuid4())
    for methode, pfad, koerper in (("POST", "/api/kern/tickets", ANLEGEN), ("PATCH", "/api/kern/tickets/LAB-12", {"erwartete_version": 1, "felder": {"titel": "Neu x"}}),
                                   ("POST", "/api/kern/tickets/LAB-12/status", {"erwartete_version": 1, "status": "erledigt"}),
                                   ("POST", "/api/kern/tickets/LAB-12/kommentare", {"erwartete_version": 1, "text": "x"})):
        r = client.request(methode, pfad, json=koerper, headers=schreib_kopf(fremd))
        assert _fehler(r, 409, "durchlauf_beendet")["text"] == "Der Durchlauf ist beendet."
    r = client.post("/api/kern/tickets/LAB-12/status", json={"erwartete_version": 7, "status": "erledigt"}, headers=schreib_kopf(d))
    assert _fehler(r, 409, "ticket_veraendert")["text"] == "Das Ticket hat sich geändert."
    _fehler(client.post("/api/kern/tickets/LAB-13/status", json={"erwartete_version": 1, "status": "erledigt"}, headers=schreib_kopf(d)), 404, "ticket_unbekannt")


def test_beendeter_durchlauf_laesst_das_schreiben_nicht_zu(client, d):
    client.post(f"/api/betreuer/plaetze/{PLATZ}/durchlauf-beenden", headers=betreuer_kopf())
    assert client.get("/api/kern/durchlauf", headers=kern_kopf()).json()["offen"] is False
    _fehler(anlegen_http(client, d), 409, "durchlauf_beendet")


@pytest.mark.parametrize("koerper", [{**ANLEGEN, "extra": 1}, {"typ": "bug"}, {**ANLEGEN, "typ": "BUG"}, {**ANLEGEN, "titel": "ab"}, {**ANLEGEN, "geloescht": True},
                                     [], "text", None, {**ANLEGEN, "titel": "a\nb"}, {**ANLEGEN, "beschreibung": "x" * 1501}])
def test_v4_form_fehler_sind_422(client, d, koerper):
    _fehler(client.post("/api/kern/tickets", json=koerper, headers=schreib_kopf(d)), 422, "eingabe_ungueltig")


def test_v5_v6_v7_form_fehler_sind_422(client, d):
    h = schreib_kopf(d)
    for pfad, methode, koerper in (("/api/kern/tickets/LAB-12", "PATCH", {"erwartete_version": 1, "felder": {"status": "erledigt"}}),
                                   ("/api/kern/tickets/LAB-12", "PATCH", {"erwartete_version": 1, "felder": {}}),
                                   ("/api/kern/tickets/LAB-12", "PATCH", {"erwartete_version": 0, "felder": {"titel": "Neu x"}}),
                                   ("/api/kern/tickets/LAB-12/status", "POST", {"erwartete_version": 1, "status": "offen"}),
                                   ("/api/kern/tickets/LAB-12/status", "POST", {"erwartete_version": "1", "status": "erledigt"}),
                                   ("/api/kern/tickets/LAB-12/kommentare", "POST", {"erwartete_version": 1, "text": ""}),
                                   ("/api/kern/tickets/LAB-12/kommentare", "POST", {"erwartete_version": 1, "text": "a\rb"}),
                                   ("/api/kern/tickets/LAB-1x/kommentare", "POST", {"erwartete_version": 1, "text": "ok"})):
        _fehler(client.request(methode, pfad, json=koerper, headers={**h, "Idempotency-Key": str(uuid.uuid4())}), 422, "eingabe_ungueltig")


def test_inhaltstyp_json_und_kaputter_inhalt_sind_422(client, d):
    kopf = schreib_kopf(d)
    _fehler(client.post("/api/kern/tickets", content=json.dumps(ANLEGEN), headers={**kopf, "Content-Type": "text/plain"}), 422, "eingabe_ungueltig")
    _fehler(client.post("/api/kern/tickets", content=json.dumps(ANLEGEN), headers={k: v for k, v in kopf.items() if k != "Content-Type"}), 422, "eingabe_ungueltig")
    for roh in (b"{nicht json", b"", b"\xff\xfe", b'{"typ": NaN}', b'{"titel": "a", "titel": "b"}'):
        _fehler(client.post("/api/kern/tickets", content=roh, headers={**kopf, "Content-Type": "application/json"}), 422, "eingabe_ungueltig")
    r = client.post("/api/kern/tickets", content=json.dumps(ANLEGEN), headers={**kopf, "Content-Type": "Application/JSON; charset=utf-8"})
    assert r.status_code == 201


@pytest.mark.parametrize("kopf", [{"X-WAMO-Durchlauf": None}, {"Idempotency-Key": None}, {"X-WAMO-Durchlauf": "nummer-eins"}, {"Idempotency-Key": "123"},
                                  {"X-WAMO-Durchlauf": "5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41a"}, {"Idempotency-Key": "' OR 1=1 --"},
                                  {"X-WAMO-Durchlauf": "5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41aaX"}])
def test_koepfe_durchlauf_und_schluessel_pflicht_und_form(client, d, kopf):
    h = {**schreib_kopf(d), **kopf}
    h = {k: v for k, v in h.items() if v is not None}
    _fehler(client.post("/api/kern/tickets", json=ANLEGEN, headers=h), 422, "eingabe_ungueltig")


def test_uuid_gross_und_klein_gleich(client, d):
    assert client.post("/api/kern/tickets", json=ANLEGEN, headers=schreib_kopf(d.upper(), schluessel=str(uuid.uuid4()).upper())).status_code == 201


# ---------- Größe (413 vor dem Lesen) ----------
def _koerper(laenge: int) -> bytes:
    rumpf = {"typ": "bug", "prioritaet": "hoch", "titel": "Zu gross", "beschreibung": ""}
    return json.dumps({**rumpf, "beschreibung": "x" * (laenge - len(json.dumps(rumpf).encode()))}).encode()


def test_anfrage_ueber_16_kb_ist_413_darunter_422(client, d):
    kopf = {**schreib_kopf(d), "Content-Type": "application/json"}
    _fehler(client.post("/api/kern/tickets", content=_koerper(16 * 1024 + 1), headers=kopf), 413, "zu_gross")
    _fehler(client.post("/api/kern/tickets", content=_koerper(16 * 1024), headers=kopf), 422, "eingabe_ungueltig")  # genau 16 KB werden gelesen
    _fehler(client.post("/api/kern/tickets", content=_koerper(70 * 1024), headers=kopf), 413, "zu_gross")
    _fehler(client.post("/api/kern/tickets", content=(chunk for chunk in (b"x" * 8192, b"y" * 8193)), headers=kopf), 413, "zu_gross")  # ohne Content-Length


def _asgi(app, methode: str, pfad: str, koepfe: dict[str, str], empfangen):
    antworten: list[dict] = []

    async def senden(nachricht):
        antworten.append(nachricht)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": methode, "scheme": "http", "path": pfad, "raw_path": pfad.encode(),
             "query_string": b"", "headers": [(k.lower().encode(), v.encode()) for k, v in koepfe.items()], "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1)}
    asyncio.run(app(scope, empfangen, senden))
    return antworten


def test_413_kommt_bevor_der_inhalt_gelesen_wird(app, d):
    gelesen: list[int] = []

    async def empfangen():
        gelesen.append(1)
        return {"type": "http.request", "body": b"x" * 20000, "more_body": False}

    kopf = {**schreib_kopf(d), "Content-Type": "application/json", "Content-Length": "20000"}
    antworten = _asgi(app, "POST", "/api/kern/tickets", kopf, empfangen)
    assert antworten[0]["status"] == 413 and gelesen == []


def test_platz_vor_groesse_und_groesse_vor_form(client, d):
    gross = {**schreib_kopf(d, platz=99), "Content-Type": "application/json"}
    _fehler(client.post("/api/kern/tickets", content=b"x" * 20000, headers=gross), 404, "platz_unbekannt")
    ok = {**schreib_kopf(d), "Content-Type": "application/json"}
    _fehler(client.post("/api/kern/tickets", content=b"x" * 20000, headers=ok), 413, "zu_gross")


def test_zu_grosse_koepfe_werden_abgelehnt(client, d):
    r = client.get("/api/kern/durchlauf", headers={**kern_kopf(), "X-Gross": "a" * 20000})
    assert r.status_code == 431 and r.json()["fehler"] == "zu_gross" and r.headers["cache-control"] == "no-store"


# ---------- Reihenfolge der Prüfungen 2.2 ----------
def test_reihenfolge_form_vor_durchlauf_vor_ticket_vor_version(client, d):
    fremd = str(uuid.uuid4())
    _fehler(client.patch("/api/kern/tickets/LAB-9999", json={"erwartete_version": 1, "felder": {"geloescht": True}}, headers=schreib_kopf(d)), 422, "eingabe_ungueltig")
    _fehler(client.post("/api/kern/tickets", json={**ANLEGEN, "titel": "x"}, headers=schreib_kopf(fremd)), 422, "eingabe_ungueltig")
    _fehler(client.post("/api/kern/tickets/LAB-9999/status", json={"erwartete_version": 1, "status": "erledigt"}, headers=schreib_kopf(fremd)), 409, "durchlauf_beendet")
    _fehler(client.post("/api/kern/tickets/LAB-9999/status", json={"erwartete_version": 7, "status": "erledigt"}, headers=schreib_kopf(d)), 404, "ticket_unbekannt")
    _fehler(client.post("/api/kern/tickets/LAB-12/status", json={"erwartete_version": 999, "status": "erledigt"}, headers=schreib_kopf(fremd)), 409, "durchlauf_beendet")


def test_reihenfolge_wiederholung_vor_schreibrate_vor_durchlauf(client, d):
    kopf = schreib_kopf(d)
    assert client.post("/api/kern/tickets", json=ANLEGEN, headers=kopf).status_code == 201
    for _ in range(29):
        assert anlegen_http(client, d).status_code == 201
    assert client.post("/api/kern/tickets", json=ANLEGEN, headers=kopf).headers["idempotent-replayed"] == "true"  # Wiederholung kommt bei voller Rate
    _fehler(anlegen_http(client, d), 429, "quote_erreicht")
    _fehler(client.post("/api/kern/tickets", json=ANLEGEN, headers=schreib_kopf(str(uuid.uuid4()))), 429, "quote_erreicht")  # Rate vor Durchlauf
    _fehler(client.post("/api/kern/tickets", json={**ANLEGEN, "titel": "x"}, headers=schreib_kopf(d)), 422, "eingabe_ungueltig")  # Form vor Rate


def test_lesende_aufrufe_zaehlen_nicht_zur_schreibrate(client, d):
    for _ in range(40):
        assert client.get("/api/kern/durchlauf", headers=kern_kopf()).status_code == 200
    assert anlegen_http(client, d).status_code == 201


def test_ticketquote_ist_429_nicht_abgestuerzt(client, d, uhr):
    for i in range(94):
        if i % 25 == 24:
            uhr.vor(61)
        assert anlegen_http(client, d, titel=f"Menge {i:03d}").status_code == 201
    _fehler(anlegen_http(client, d), 429, "quote_erreicht")
    assert client.get("/api/kern/durchlauf", headers=kern_kopf()).status_code == 200


# ---------- Platztrennung (D6) ----------
def test_platz_16_sieht_und_aendert_nichts_von_platz_15(client):
    d15, d16 = oeffnen(client, 15), oeffnen(client, 16)
    nummer = anlegen_http(client, d15, 15, titel="Nur auf Platz fünfzehn").json()["nummer"]
    assert nummer == "LAB-30"
    anlegen_http(client, d16, 16)  # Platz 16 hat eigene LAB-30 mit anderem Inhalt
    assert client.get(f"/api/kern/tickets/{nummer}", headers=kern_kopf(16)).json()["titel"] == ANLEGEN["titel"]
    assert client.get("/api/kern/tickets?q=fünfzehn", headers=kern_kopf(16)).json()["gesamt"] == 0
    anlegen_http(client, d15, 15, titel="Zweites auf Platz 15")  # LAB-31 gibt es nur auf Platz 15
    r = client.post("/api/kern/tickets/LAB-31/status", json={"erwartete_version": 1, "status": "erledigt"}, headers=schreib_kopf(d16, 16))
    _fehler(r, 404, "ticket_unbekannt")
    _fehler(client.get("/api/kern/tickets/LAB-31", headers=kern_kopf(16)), 404, "ticket_unbekannt")
    _fehler(client.patch("/api/kern/tickets/LAB-31", json={"erwartete_version": 1, "felder": {"titel": "Fremd"}}, headers=schreib_kopf(d16, 16)), 404, "ticket_unbekannt")
    _fehler(client.post("/api/kern/tickets/LAB-31/kommentare", json={"erwartete_version": 1, "text": "Fremd"}, headers=schreib_kopf(d16, 16)), 404, "ticket_unbekannt")
    assert client.get("/api/kern/tickets/LAB-31", headers=kern_kopf(15)).json()["version"] == 1


def test_durchlauf_eines_anderen_platzes_gilt_nicht(client):
    d15, _ = oeffnen(client, 15), oeffnen(client, 16)
    _fehler(client.post("/api/kern/tickets", json=ANLEGEN, headers=schreib_kopf(d15, 16)), 409, "durchlauf_beendet")


# ---------- Fehlerform ----------
def test_unerwarteter_fehler_ist_500_dienst_fehler_ohne_einzelheiten(client, d, monkeypatch):
    def kaputt(self):
        raise RuntimeError("Geheimnis /opt/wamo/pfad sqlite Traceback")

    monkeypatch.setattr(PlatzDB, "durchlauf", kaputt)
    r = client.get("/api/kern/durchlauf", headers=kern_kopf())
    assert r.status_code == 500 and r.json()["fehler"] == "dienst_fehler"
    assert "Geheimnis" not in r.text and "/opt" not in r.text and "sqlite" not in r.text and r.headers["cache-control"] == "no-store"


def test_fehlertexte_ohne_interna(client, d):
    for r in (client.get("/api/kern/tickets/zzz", headers=kern_kopf()), client.get("/api/kern/nix"), client.post("/api/kern/durchlauf"),
              client.get("/api/kern/durchlauf"), client.get("/api/kern/durchlauf", headers=kern_kopf(99))):
        text = r.json()["text"]
        assert 0 < len(text) <= 200 and not any(w in text for w in ("Traceback", ".py", "sqlite", "SELECT", "/usr/", "/opt/", "\\"))
