"""Tests der Attrappe des Test-Jira-Dienstes (labor_attrappe.py) gegen den Vertrag Version 1 (docs/labor-dienst-vertrag.md).

Die Attrappe läuft im Prozess (ASGI mit httpx), nur für Tests. Diese Tests sprechen sie mit rohen HTTP-Aufrufen an, nicht über den
Dienstclient des Kerns, damit ein Fehler im Client keinen Fehler der Attrappe verdeckt.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from labor_attrappe import ATTRAPPE_URL, Attrappe, mit_client

DAUER_MIN = 60.0


class Uhr:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def uhr():
    return Uhr()


@pytest.fixture
def app(uhr):
    return Attrappe(uhr=uhr)


@pytest.fixture
def http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ATTRAPPE_URL)


def kopf(app, platz=3, *, schreiben=False, durchlauf=None, schluessel=None, token=None):
    k = {"Authorization": f"Bearer {token or app.token}", "X-WAMO-Platz": str(platz)}
    if schreiben:
        k["X-WAMO-Durchlauf"] = durchlauf or app.durchlauf_id(platz)
        k["Idempotency-Key"] = schluessel or str(uuid.uuid4())
    return k


async def anlegen(http, app, platz=3, **felder):
    body = {"typ": "bug", "prioritaet": "hoch", "titel": "Anmeldung schlägt fehl", "beschreibung": "Nach dem Klick passiert nichts."}
    body.update(felder)
    return await http.post("/api/kern/tickets", json=body, headers=kopf(app, platz, schreiben=True))


def fehler(r):
    return r.json()["fehler"]


# ---------- V1 ----------
async def test_v1_durchlauf(http, app):
    r = await http.get("/api/kern/durchlauf", headers=kopf(app))
    assert r.status_code == 200
    d = r.json()
    assert set(d) == {"durchlauf_id", "offen", "einwilligung_version", "einwilligung_zeit"}
    assert d["offen"] is True and d["einwilligung_version"] == 1 and d["einwilligung_zeit"].endswith("Z")
    assert uuid.UUID(d["durchlauf_id"]) and d["durchlauf_id"] == app.durchlauf_id(3)
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("application/json")


async def test_v1_ohne_einwilligung_und_nach_ende(http, app):
    app.neuer_durchlauf(4, einwilligung_version=None)
    d = (await http.get("/api/kern/durchlauf", headers=kopf(app, 4))).json()
    assert d["einwilligung_version"] is None and d["einwilligung_zeit"] is None and d["offen"] is True
    app.durchlauf_beenden(4)
    assert (await http.get("/api/kern/durchlauf", headers=kopf(app, 4))).json()["offen"] is False


# ---------- Zugang, Platz, Pfade ----------
@pytest.mark.parametrize("auth", [None, "", "Bearer", "Bearer falsch", "Basic abc", "bearer "])
async def test_bearer_fehlt_oder_falsch_ist_401(http, app, auth):
    k = kopf(app)
    if auth is None:
        del k["Authorization"]
    else:
        k["Authorization"] = auth
    r = await http.get("/api/kern/durchlauf", headers=k)
    assert r.status_code == 401 and fehler(r) == "token_ungueltig" and set(r.json()) == {"fehler", "text"}


@pytest.mark.parametrize("platz", [None, "0", "17", "abc", "1.5", "-1", "03", "1 ", "99999999999999999999"])
async def test_platz_fehlt_oder_unbekannt_ist_404(http, app, platz):
    k = kopf(app)
    if platz is None:
        del k["X-WAMO-Platz"]
    else:
        k["X-WAMO-Platz"] = platz
    r = await http.get("/api/kern/durchlauf", headers=k)
    assert r.status_code == 404 and fehler(r) == "platz_unbekannt"


async def test_erst_token_dann_platz(http, app):
    r = await http.get("/api/kern/durchlauf", headers={"Authorization": "Bearer x", "X-WAMO-Platz": "99"})
    assert r.status_code == 401


@pytest.mark.parametrize("methode", ["DELETE", "PUT", "OPTIONS", "TRACE"])
async def test_andere_methoden_sind_405(http, app, methode):
    for pfad in ("/api/kern/durchlauf", "/api/kern/tickets", "/api/kern/tickets/LAB-12"):
        r = await http.request(methode, pfad, headers=kopf(app))
        assert r.status_code == 405 and fehler(r) == "nicht_erlaubt" and "allow" in r.headers


async def test_unbekannte_pfade_sind_404(http, app):
    for pfad in ("/", "/api", "/api/kern", "/api/kern/", "/api/kern/loeschen", "/api/kern/tickets/LAB-12/loeschen",
                 "/api/kern/tickets/LAB-12/kommentare/5", "/api/kern/../durchlauf", "/admin"):
        r = await http.get(pfad, headers=kopf(app))
        assert r.status_code == 404 and fehler(r) == "nicht_erlaubt", pfad


async def test_method_override_wird_ignoriert(http, app):
    k = kopf(app, schreiben=True) | {"X-HTTP-Method-Override": "DELETE"}
    r = await http.post("/api/kern/tickets", json={"typ": "bug", "prioritaet": "hoch", "titel": "Drei", "beschreibung": ""}, headers=k)
    assert r.status_code == 201
    assert len(app.tickets(3)) == 7


# ---------- V4 anlegen ----------
async def test_v4_legt_an_mit_zaehler_ab_30(http, app):
    r = await anlegen(http, app)
    assert r.status_code == 201
    t = r.json()
    assert t == {"nummer": "LAB-30", "titel": "Anmeldung schlägt fehl", "typ": "bug", "prioritaet": "hoch",
                 "status": "zu_erledigen", "beschreibung": "Nach dem Klick passiert nichts.", "version": 1, "im_durchlauf": True}
    assert (await anlegen(http, app, titel="Zweites")).json()["nummer"] == "LAB-31"


async def test_seed(http, app):
    r = await http.get("/api/kern/tickets", headers=kopf(app))
    t = {x["nummer"]: x for x in r.json()["tickets"]}
    assert r.json()["gesamt"] == 6
    assert t["LAB-11"]["status"] == "erledigt" and t["LAB-12"]["status"] == "in_arbeit" and t["LAB-20"]["status"] == "zu_erledigen"
    assert all(x["im_durchlauf"] is False and x["version"] == 1 for x in t.values())


@pytest.mark.parametrize("aenderung,feld", [
    ({"typ": "fehler"}, "typ"), ({"prioritaet": "dringend"}, "prioritaet"), ({"titel": "ab"}, "titel"),
    ({"titel": "x" * 121}, "titel"), ({"beschreibung": "x" * 1501}, "beschreibung"), ({"titel": "a\nb c"}, "titel"),
    ({"titel": "Test\u202eTitel"}, "titel"), ({"beschreibung": "a\rb"}, "beschreibung"), ({"titel": "Cafe\u0301 Test"}, "titel"),
    ({"beschreibung": "a\x00b"}, "beschreibung"), ({"titel": 5}, "titel"), ({"beschreibung": None}, "beschreibung"),
    ({"titel": "\u200bAbc"}, "titel"), ({"beschreibung": "a\u2028b"}, "beschreibung"),
])
async def test_v4_ungueltige_werte_sind_422(http, app, aenderung, feld):
    r = await anlegen(http, app, **aenderung)
    assert r.status_code == 422 and fehler(r) == "eingabe_ungueltig" and feld in r.json()["text"]
    assert len(app.tickets(3)) == 6


async def test_v4_grenzen_gehen_durch(http, app):
    assert (await anlegen(http, app, titel="x" * 120, beschreibung="y" * 1500)).status_code == 201
    assert (await anlegen(http, app, titel="abc", beschreibung="")).status_code == 201
    assert (await anlegen(http, app, beschreibung="Zeile1\nZeile2")).status_code == 201


async def test_v4_felder_muessen_genau_passen(http, app):
    k = kopf(app, schreiben=True)
    for body in ({"typ": "bug"}, {"typ": "bug", "prioritaet": "hoch", "titel": "Drei", "beschreibung": "", "geloescht": True},
                 {"typ": "bug", "prioritaet": "hoch", "titel": "Drei"}, [], "text", None):
        r = await http.post("/api/kern/tickets", json=body, headers=k | {"Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 422 and fehler(r) == "eingabe_ungueltig", body


async def test_nur_json_wird_angenommen(http, app):
    r = await http.post("/api/kern/tickets", content=b"typ=bug", headers=kopf(app, schreiben=True) | {"Content-Type": "text/plain"})
    assert r.status_code == 422
    r = await http.post("/api/kern/tickets", content=b"{kaputt", headers=kopf(app, schreiben=True) | {"Content-Type": "application/json"})
    assert r.status_code == 422 and fehler(r) == "eingabe_ungueltig"


async def test_schreibkoepfe_sind_pflicht_und_muessen_uuid_sein(http, app):
    body = {"typ": "bug", "prioritaet": "hoch", "titel": "Drei", "beschreibung": ""}
    for entfernen in ("X-WAMO-Durchlauf", "Idempotency-Key"):
        k = kopf(app, schreiben=True)
        del k[entfernen]
        r = await http.post("/api/kern/tickets", json=body, headers=k)
        assert r.status_code == 422 and fehler(r) == "eingabe_ungueltig", entfernen
    for kaputt in ("kein-uuid", "123", "' OR 1=1 --", str(uuid.uuid4()) + "x"):
        r = await http.post("/api/kern/tickets", json=body, headers=kopf(app, schreiben=True, durchlauf=kaputt))
        assert r.status_code == 422
        r = await http.post("/api/kern/tickets", json=body, headers=kopf(app, schreiben=True, schluessel=kaputt))
        assert r.status_code == 422
    assert len(app.tickets(3)) == 6


# ---------- Größe ----------
async def test_zu_grosse_anfrage_ist_413_bevor_sie_gelesen_wird(http, app):
    r = await http.post("/api/kern/tickets", content=b"x" * (16 * 1024 + 1),
                        headers=kopf(app, schreiben=True) | {"Content-Type": "application/json"})
    assert r.status_code == 413 and fehler(r) == "zu_gross"
    ok = await anlegen(http, app, beschreibung="ä" * 1500)  # 3000 Byte, weit unter der Grenze
    assert ok.status_code == 201


# ---------- V3 lesen, V2 suchen ----------
async def test_v3_ticket_mit_kommentaren(http, app):
    r = await http.get("/api/kern/tickets/LAB-12", headers=kopf(app))
    assert r.status_code == 200
    d = r.json()
    assert d["nummer"] == "LAB-12" and d["kommentare"] == [] and d["version"] == 1
    for i in range(22):
        c = await http.post("/api/kern/tickets/LAB-12/kommentare",
                            json={"erwartete_version": d["version"] + i, "text": f"Kommentar {i}"}, headers=kopf(app, schreiben=True))
        assert c.status_code == 201, c.text
    d = (await http.get("/api/kern/tickets/LAB-12", headers=kopf(app))).json()
    assert [k["text"] for k in d["kommentare"]] == [f"Kommentar {i}" for i in range(2, 22)]  # die letzten 20, ältester zuerst
    assert set(d["kommentare"][0]) == {"kommentar_id", "text"}


@pytest.mark.parametrize("nummer,status,code", [("LAB-99", 404, "ticket_unbekannt"), ("LAB-012", 422, "eingabe_ungueltig"),
                                                ("lab-12", 422, "eingabe_ungueltig"),
                                                ("LAB-12%00", 422, "eingabe_ungueltig")])
async def test_v3_unbekannt_oder_falsche_form(http, app, nummer, status, code):
    r = await http.get(f"/api/kern/tickets/{nummer}", headers=kopf(app))
    assert r.status_code == status and fehler(r) == code


async def test_v3_platztrennung(http, app):
    neu = (await anlegen(http, app, platz=15, titel="Nur Platz fünfzehn")).json()["nummer"]
    for lesen in (lambda: http.get(f"/api/kern/tickets/{neu}", headers=kopf(app, 16)),):
        r = await lesen()
        assert r.status_code == 404 and fehler(r) == "ticket_unbekannt"
    r = await http.patch(f"/api/kern/tickets/{neu}", json={"erwartete_version": 1, "felder": {"titel": "Fremd"}},
                         headers=kopf(app, 16, schreiben=True))
    assert r.status_code == 404
    r = await http.post(f"/api/kern/tickets/{neu}/kommentare", json={"erwartete_version": 1, "text": "Fremd"},
                        headers=kopf(app, 16, schreiben=True))
    assert r.status_code == 404
    assert (await http.get(f"/api/kern/tickets/{neu}", headers=kopf(app, 15))).status_code == 200


async def test_v2_suche_ist_wörtlich(http, app):
    await anlegen(http, app, titel="100% fertig")
    await anlegen(http, app, titel="a_b gefunden")
    await anlegen(http, app, titel="axb nicht gemeint")
    for q, erwartet in (("%", {"100% fertig"}), ("_", {"a_b gefunden"}), ("100%", {"100% fertig"}), ("a_b", {"a_b gefunden"}),
                        ("\\", set()), ("' OR 1=1 --", set()), ("'; DROP TABLE tickets;--", set())):
        r = await http.get("/api/kern/tickets", params={"q": q}, headers=kopf(app))
        assert r.status_code == 200, q
        assert {t["titel"] for t in r.json()["tickets"]} == erwartet, q
    assert len((await http.get("/api/kern/tickets", headers=kopf(app))).json()["tickets"]) == 9  # die Tabelle ist noch da


async def test_v2_filter_sortierung_limit(http, app):
    r = await http.get("/api/kern/tickets", params={"limit": "2"}, headers=kopf(app))
    d = r.json()
    assert d["gesamt"] == 6 and [t["nummer"] for t in d["tickets"]] == ["LAB-25", "LAB-20"]  # neueste zuerst
    assert (await http.get("/api/kern/tickets", params={"nummer": "LAB-12"}, headers=kopf(app))).json()["gesamt"] == 1
    assert (await http.get("/api/kern/tickets", params={"status": "erledigt"}, headers=kopf(app))).json()["gesamt"] == 1
    assert (await http.get("/api/kern/tickets", params={"typ": "bug", "prioritaet": "hoch"}, headers=kopf(app))).status_code == 200
    assert (await http.get("/api/kern/tickets", params={"q": "LOGIN"}, headers=kopf(app))).json()["gesamt"] == 1  # Groß und klein gleich
    await anlegen(http, app)
    r = await http.get("/api/kern/tickets", params={"nur_durchlauf": "true"}, headers=kopf(app))
    assert [t["nummer"] for t in r.json()["tickets"]] == ["LAB-30"] and r.json()["gesamt"] == 1


@pytest.mark.parametrize("params", [{"limit": "0"}, {"limit": "11"}, {"limit": "x"}, {"typ": "fehler"}, {"status": "offen"},
                                    {"nummer": "LAB-0"}, {"nur_durchlauf": "ja"}, {"q": "x" * 101}, {"unbekannt": "1"},
                                    {"platz": "4"}, {"nummer": "LAB-1", "q": "a", "foo": "b"}])
async def test_v2_ungueltige_abfragen_sind_422(http, app, params):
    r = await http.get("/api/kern/tickets", params=params, headers=kopf(app))
    assert r.status_code == 422 and fehler(r) == "eingabe_ungueltig"


async def test_v2_doppelter_parameter_ist_422(http, app):
    r = await http.get("/api/kern/tickets?limit=1&limit=2", headers=kopf(app))
    assert r.status_code == 422


# ---------- V5 bis V7 ----------
async def test_v5_aendern_erhoeht_die_version(http, app):
    k = kopf(app, schreiben=True)
    r = await http.patch("/api/kern/tickets/LAB-12", json={"erwartete_version": 1, "felder": {"titel": "Neuer Titel", "prioritaet": "hoechste"}}, headers=k)
    assert r.status_code == 200
    t = r.json()
    assert t["titel"] == "Neuer Titel" and t["prioritaet"] == "hoechste" and t["version"] == 2 and t["status"] == "in_arbeit"
    # gleiche Werte erhöhen die Version trotzdem
    r = await http.patch("/api/kern/tickets/LAB-12", json={"erwartete_version": 2, "felder": {"titel": "Neuer Titel"}}, headers=kopf(app, schreiben=True))
    assert r.json()["version"] == 3


@pytest.mark.parametrize("felder", [{}, {"status": "erledigt"}, {"nummer": "LAB-99"}, {"geloescht": True}, {"version": 9},
                                    {"titel": "Gut", "geloescht": True}, [], None, "x"])
async def test_v5_nur_vier_felder_erlaubt(http, app, felder):
    r = await http.patch("/api/kern/tickets/LAB-12", json={"erwartete_version": 1, "felder": felder}, headers=kopf(app, schreiben=True))
    assert r.status_code == 422 and fehler(r) == "eingabe_ungueltig"
    assert app.ticket(3, "LAB-12")["version"] == 1


@pytest.mark.parametrize("version", [0, -1, "1", 1.5, None, True])
async def test_erwartete_version_muss_ganze_zahl_ab_1_sein(http, app, version):
    r = await http.patch("/api/kern/tickets/LAB-12", json={"erwartete_version": version, "felder": {"titel": "Neu drei"}}, headers=kopf(app, schreiben=True))
    assert r.status_code == 422


async def test_v6_status_und_v7_kommentar(http, app):
    r = await http.post("/api/kern/tickets/LAB-20/status", json={"erwartete_version": 1, "status": "erledigt"}, headers=kopf(app, schreiben=True))
    assert r.status_code == 200 and r.json()["status"] == "erledigt" and r.json()["version"] == 2
    r = await http.post("/api/kern/tickets/LAB-20/status", json={"erwartete_version": 2, "status": "erledigt"}, headers=kopf(app, schreiben=True))
    assert r.status_code == 200 and r.json()["version"] == 3  # alle Übergänge erlaubt, auch auf den gleichen Status
    r = await http.post("/api/kern/tickets/LAB-20/status", json={"erwartete_version": 3, "status": "geloescht"}, headers=kopf(app, schreiben=True))
    assert r.status_code == 422
    r = await http.post("/api/kern/tickets/LAB-20/kommentare", json={"erwartete_version": 3, "text": "Erster Kommentar"}, headers=kopf(app, schreiben=True))
    assert r.status_code == 201
    d = r.json()
    assert set(d) == {"kommentar_id", "ticket"} and d["ticket"]["version"] == 4 and isinstance(d["kommentar_id"], int)
    for text in ("", "x" * 501, "a\x00b", "a\u202eb"):
        r = await http.post("/api/kern/tickets/LAB-20/kommentare", json={"erwartete_version": 4, "text": text}, headers=kopf(app, schreiben=True))
        assert r.status_code == 422, text


async def test_ticket_veraendert_ist_409_und_aendert_nichts(http, app):
    r = await http.post("/api/kern/tickets/LAB-12/status", json={"erwartete_version": 5, "status": "erledigt"}, headers=kopf(app, schreiben=True))
    assert r.status_code == 409 and fehler(r) == "ticket_veraendert"
    assert app.ticket(3, "LAB-12")["status"] == "in_arbeit" and app.ticket(3, "LAB-12")["version"] == 1


async def test_durchlauf_beendet_ist_409_und_aendert_nichts(http, app):
    alt = app.durchlauf_id(3)
    app.durchlauf_beenden(3)
    r = await http.post("/api/kern/tickets", json={"typ": "bug", "prioritaet": "hoch", "titel": "Zu spät", "beschreibung": ""},
                        headers=kopf(app, schreiben=True, durchlauf=alt))
    assert r.status_code == 409 and fehler(r) == "durchlauf_beendet"
    app.neuer_durchlauf(3)  # der Nächste: neuer Durchlauf, Seed
    r = await http.post("/api/kern/tickets/LAB-12/status", json={"erwartete_version": 1, "status": "erledigt"},
                        headers=kopf(app, schreiben=True, durchlauf=alt))
    assert r.status_code == 409 and fehler(r) == "durchlauf_beendet"
    assert app.ticket(3, "LAB-12")["status"] == "in_arbeit"
    fremd = str(uuid.uuid4())  # eine ganz fremde Kennung
    r = await http.post("/api/kern/tickets", json={"typ": "bug", "prioritaet": "hoch", "titel": "Fremd", "beschreibung": ""},
                        headers=kopf(app, schreiben=True, durchlauf=fremd))
    assert r.status_code == 409


async def test_neuer_durchlauf_setzt_den_seed_zurueck(http, app):
    await anlegen(http, app)
    app.neuer_durchlauf(3)
    assert len(app.tickets(3)) == 6
    assert (await anlegen(http, app)).json()["nummer"] == "LAB-30"


async def test_prioritaet_der_pruefungen(http, app):
    """Reihenfolge aus Abschnitt 2.2: Form (422) vor Durchlauf (409) vor Ticket (404) vor Version (409)."""
    app.durchlauf_beenden(3)
    k = kopf(app, schreiben=True)
    r = await http.patch("/api/kern/tickets/LAB-99", json={"erwartete_version": 7, "felder": {"titel": "ab"}}, headers=k)
    assert r.status_code == 422  # Form zuerst
    r = await http.patch("/api/kern/tickets/LAB-99", json={"erwartete_version": 7, "felder": {"titel": "Gut"}}, headers=k | {"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 409 and fehler(r) == "durchlauf_beendet"  # vor 404
    app.neuer_durchlauf(3)
    k = kopf(app, schreiben=True)
    r = await http.patch("/api/kern/tickets/LAB-99", json={"erwartete_version": 7, "felder": {"titel": "Gut"}}, headers=k)
    assert r.status_code == 404  # vor der Version


# ---------- Idempotenz ----------
async def test_gleicher_schluessel_gleiche_antwort_nichts_doppelt(http, app):
    body = {"typ": "bug", "prioritaet": "hoch", "titel": "Nur einmal", "beschreibung": ""}
    k = kopf(app, schreiben=True)
    r1 = await http.post("/api/kern/tickets", json=body, headers=k)
    r2 = await http.post("/api/kern/tickets", json=body, headers=k)
    assert r1.status_code == r2.status_code == 201 and r1.json() == r2.json()
    assert "idempotent-replayed" not in r1.headers and r2.headers["idempotent-replayed"] == "true"
    assert len(app.tickets(3)) == 7


async def test_wiederholung_gilt_auch_nach_ende_des_durchlaufs(http, app):
    body = {"typ": "bug", "prioritaet": "hoch", "titel": "Nur einmal", "beschreibung": ""}
    k = kopf(app, schreiben=True)
    r1 = await http.post("/api/kern/tickets", json=body, headers=k)
    app.durchlauf_beenden(3)
    r2 = await http.post("/api/kern/tickets", json=body, headers=k)
    assert r2.status_code == 201 and r2.json() == r1.json() and r2.headers["idempotent-replayed"] == "true"


async def test_gleicher_schluessel_andere_anfrage_ist_422(http, app):
    k = kopf(app, schreiben=True)
    assert (await http.post("/api/kern/tickets", json={"typ": "bug", "prioritaet": "hoch", "titel": "Eins", "beschreibung": ""}, headers=k)).status_code == 201
    r = await http.post("/api/kern/tickets", json={"typ": "bug", "prioritaet": "hoch", "titel": "Anders", "beschreibung": ""}, headers=k)
    assert r.status_code == 422 and fehler(r) == "eingabe_ungueltig"
    assert len(app.tickets(3)) == 7


async def test_schluessel_gilt_je_platz_und_24_stunden(http, app, uhr):
    body = {"typ": "bug", "prioritaet": "hoch", "titel": "Nur einmal", "beschreibung": ""}
    schluessel = str(uuid.uuid4())
    assert (await http.post("/api/kern/tickets", json=body, headers=kopf(app, 3, schreiben=True, schluessel=schluessel))).status_code == 201
    r = await http.post("/api/kern/tickets", json=body, headers=kopf(app, 4, schreiben=True, schluessel=schluessel))
    assert r.status_code == 201 and "idempotent-replayed" not in r.headers  # anderer Platz: eigene Anfrage
    uhr.t += 24 * 3600 + 1
    r = await http.post("/api/kern/tickets", json=body, headers=kopf(app, 3, schreiben=True, schluessel=schluessel))
    assert r.status_code == 201 and "idempotent-replayed" not in r.headers  # abgelaufen: wird neu ausgeführt
    assert len(app.tickets(3)) == 8


async def test_fehler_5xx_und_429_werden_nicht_gespeichert(http, app, uhr):
    for i in range(30):
        assert (await anlegen(http, app, titel=f"Ticket {i:03d}")).status_code == 201
    schluessel = str(uuid.uuid4())
    body = {"typ": "bug", "prioritaet": "hoch", "titel": "Spaeter", "beschreibung": ""}
    r = await http.post("/api/kern/tickets", json=body, headers=kopf(app, schreiben=True, schluessel=schluessel))
    assert r.status_code == 429 and fehler(r) == "quote_erreicht"
    uhr.t += DAUER_MIN + 1
    r = await http.post("/api/kern/tickets", json=body, headers=kopf(app, schreiben=True, schluessel=schluessel))
    assert r.status_code == 201  # nach der Wartezeit wird die Wiederholung ausgeführt


# ---------- Quoten (D1) ----------
async def test_schreibrate_30_je_minute(http, app, uhr):
    for i in range(30):
        assert (await anlegen(http, app, titel=f"Ticket {i:03d}")).status_code == 201
    n = len(app.tickets(3))
    r = await anlegen(http, app, titel="Ticket 31")
    assert r.status_code == 429 and fehler(r) == "quote_erreicht"
    assert len(app.tickets(3)) == n  # der abgelehnte Aufruf hat nichts geschrieben
    assert (await http.get("/api/kern/tickets", headers=kopf(app))).status_code == 200  # Lesen zählt nicht
    uhr.t += DAUER_MIN + 1
    assert (await anlegen(http, app, titel="Ticket 32")).status_code == 201


async def test_mengenquote_100_tickets(http, app, uhr):
    while len(app.tickets(3)) < 100:
        for _ in range(25):
            if len(app.tickets(3)) >= 100:
                break
            assert (await anlegen(http, app)).status_code == 201
        uhr.t += DAUER_MIN + 1
    r = await anlegen(http, app)
    assert r.status_code == 429 and fehler(r) == "quote_erreicht" and len(app.tickets(3)) == 100


async def test_mengenquote_kommentare_und_bytes(http, app, uhr):
    for i in range(300):
        r = await http.post("/api/kern/tickets/LAB-12/kommentare", json={"erwartete_version": 1 + i, "text": "k"},
                            headers=kopf(app, schreiben=True))
        assert r.status_code == 201, i
        if i % 25 == 24:
            uhr.t += DAUER_MIN + 1
    r = await http.post("/api/kern/tickets/LAB-12/kommentare", json={"erwartete_version": 301, "text": "zu viel"}, headers=kopf(app, schreiben=True))
    assert r.status_code == 429 and fehler(r) == "quote_erreicht"
    # Nutztext insgesamt höchstens 1 MB (Titel, Beschreibungen, Kommentare in UTF-8): ein Konto kurz vor der Grenze
    app.setze_nutzbytes(6, 1024 * 1024 - 100)
    r = await anlegen(http, app, platz=6, beschreibung="ä" * 100)  # 200 Byte Beschreibung plus Titel: zu viel
    assert r.status_code == 429 and fehler(r) == "quote_erreicht"
    assert (await anlegen(http, app, platz=6, titel="abc", beschreibung="")).status_code == 201


# ---------- verwundbare Variante (nur für den Nachweis, dass die Abnahme rot wird) ----------
async def test_verwundbare_variante_laesst_schutz_weg(uhr):
    app = Attrappe(uhr=uhr, verwundbar=True)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ATTRAPPE_URL)
    # Maskierung: % und _ wirken als Platzhalter
    r = await http.get("/api/kern/tickets", params={"q": "%"}, headers=kopf(app))
    assert r.json()["gesamt"] == 6
    # keine Schreibrate
    for i in range(40):
        assert (await anlegen(http, app, titel=f"Ticket {i:03d}")).status_code == 201
    # keine atomare Prüfung von Durchlauf und Version
    app.durchlauf_beenden(3)
    r = await http.post("/api/kern/tickets/LAB-12/status", json={"erwartete_version": 99, "status": "erledigt"},
                        headers=kopf(app, schreiben=True, durchlauf=str(uuid.uuid4())))
    assert r.status_code == 200
    # die saubere Attrappe verhält sich in denselben Fällen anders (siehe die Tests oben)


async def test_attrappe_hat_kein_ausgehendes_netz_und_keinen_festen_token():
    a, b = Attrappe(), Attrappe()
    assert a.token != b.token and len(a.token) >= 32
    assert ATTRAPPE_URL.startswith("https://") and ATTRAPPE_URL.endswith(".invalid")


async def test_testhilfe_mit_client_verbindet_im_prozess():
    app, client = mit_client(5)
    assert (await client.durchlauf()).id == app.durchlauf_id(5)
    assert (await client.tickets())["gesamt"] == 6
    await client.aclose()
    app, client = mit_client(5, verwundbar=True)
    assert (await client.tickets(q="%"))["gesamt"] == 6  # die verwundbare Variante lässt % als Platzhalter wirken
    await client.aclose()
