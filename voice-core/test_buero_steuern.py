"""Schalten mit dem Knopf in der Büro-Karte (buero_api.py, POST /wamo/api/buero/steuern) und die Aktionsliste im Stand.

Ohne Netz und ohne Datenbank: der Gerätedienst ist eine Attrappe (test_buero_api.Dienst), das Protokoll in actions wird abgefangen.
Gilt seit 12.11.2026: Admins können Fernseher, Lautsprecher und Steckdosen auch ohne Sprechen schalten.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import buero_api as api
import geraete_client as gc
from auth_gate import Identity
from test_buero_api import IP, PFAD, USER, Dienst, roh

STEUERN = "/wamo/api/buero/steuern"


class SteuerDienst(Dienst):
    """Wie die Attrappe aus test_buero_api, dazu die Antwort auf das Werkzeug steuern."""

    def __init__(self):
        super().__init__()
        self.steuern: dict = {"status": "success", "aktion": "an", "an": True}

    async def aufruf(self, werkzeug, kontext, **kw):
        if werkzeug == "steuern":
            self.aufrufe.append({"werkzeug": werkzeug, "kontext": kontext, "geraet": kw.get("geraet"),
                                 "argumente": kw.get("argumente"), "timeout": kw.get("timeout")})
            if self.fehler:
                raise self.fehler
            return self.steuern, b""
        return await super().aufruf(werkzeug, kontext, **kw)

    def schaltungen(self) -> list[dict]:
        return [a for a in self.aufrufe if a["werkzeug"] == "steuern"]


class Rolle:
    rolle = "admin"


@pytest.fixture
def dienst(monkeypatch):
    d = SteuerDienst()
    monkeypatch.setattr(gc, "aufruf", d.aufruf)
    monkeypatch.setattr(gc, "verfuegbar", lambda: d.verfuegbar)
    return d


@pytest.fixture
def ctx(monkeypatch):
    k = Rolle()

    async def identitaet(_request):
        return Identity(user_id=USER, name="Niko", rolle=k.rolle, anrede="du")

    monkeypatch.setattr(api, "identitaet", identitaet)
    api._versuche.clear()
    api._schaltungen.clear()
    return k


@pytest.fixture
def client(dienst, ctx):
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return TestClient(app)


@pytest.fixture
def protokoll(monkeypatch):
    eintraege: list[tuple] = []

    async def merken(user_id, kennung, aktion, status, ergebnis):
        eintraege.append((user_id, kennung, aktion, status, ergebnis))

    monkeypatch.setattr(api, "_protokollieren", merken)
    return eintraege


def schalten(client, geraet="fernseher", aktion="an"):
    return client.post(STEUERN, json={"geraet": geraet, "aktion": aktion})


# ---------- Wer darf schalten ----------
def test_admin_schaltet_mit_seiner_identitaet(client, dienst, protokoll):
    r = schalten(client)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "geraet": "fernseher", "aktion": "an"}
    assert r.headers["cache-control"] == "no-store"
    (aufruf,) = dienst.schaltungen()
    assert aufruf["geraet"] == "fernseher" and aufruf["argumente"] == {"aktion": "an"}
    assert aufruf["kontext"].user_id == USER and aufruf["kontext"].rolle == "admin" and aufruf["kontext"].session_id is None
    assert protokoll == [(USER, "fernseher", "an", "done", "success")]


@pytest.mark.parametrize("rolle", ["mitglied", "gast", ""])
def test_andere_rollen_duerfen_nicht_schalten(client, dienst, ctx, protokoll, rolle):
    ctx.rolle = rolle
    assert schalten(client).status_code == 403
    assert dienst.schaltungen() == [] and protokoll == []


def test_schalten_ohne_anmeldung(dienst, monkeypatch, protokoll):
    async def abgelehnt(_request):
        raise HTTPException(401, "nicht angemeldet")

    monkeypatch.setattr(api, "identitaet", abgelehnt)
    api._schaltungen.clear()
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    assert schalten(TestClient(app)).status_code == 401
    assert dienst.schaltungen() == [] and protokoll == []


def test_die_ansicht_bleibt_nur_lesen(client):
    assert client.post(PFAD, json={}).status_code == 405
    assert client.delete(STEUERN).status_code == 405
    assert client.get(STEUERN).status_code == 405


# ---------- Eingaben ----------
@pytest.mark.parametrize("body", [
    {}, {"geraet": "fernseher"}, {"aktion": "an"},
    {"geraet": "fernseher", "aktion": "lautstaerke"}, {"geraet": "fernseher", "aktion": "eingang"},
    {"geraet": "fernseher", "aktion": "AN"}, {"geraet": "fernseher", "aktion": ["an"]}, {"geraet": "fernseher", "aktion": None},
    {"geraet": "../drucker", "aktion": "an"}, {"geraet": "Fernseher", "aktion": "an"}, {"geraet": "", "aktion": "an"},
    {"geraet": 5, "aktion": "an"}, {"geraet": "a" * 40, "aktion": "an"}, {"geraet": "fernseher;aus", "aktion": "an"},
])
def test_ungueltige_angaben_erreichen_den_dienst_nie(client, dienst, protokoll, body):
    assert client.post(STEUERN, json=body).status_code == 400
    assert dienst.schaltungen() == [] and protokoll == []


@pytest.mark.parametrize("aktion", ["an", "aus", "lauter", "leiser", "stumm", "laut"])
def test_genau_sechs_aktionen_gehen_durch(client, dienst, protokoll, aktion):
    assert schalten(client, "steckdose-1", aktion).status_code == 200
    assert dienst.schaltungen()[-1]["argumente"] == {"aktion": aktion}


def test_kein_gueltiges_json_und_zu_grosser_body(client, dienst, protokoll):
    assert client.post(STEUERN, content=b"{kaputt").status_code == 400
    assert client.post(STEUERN, content=b"[1]").status_code == 400
    assert client.post(STEUERN, json={"geraet": "fernseher", "aktion": "an", "x": "y" * 2000}).status_code == 413
    assert dienst.schaltungen() == []


# ---------- Antworten des Dienstes ----------
@pytest.mark.parametrize("antwort, status", [
    ({"status": "rejected", "summary": "Fernseher 192.168.178.125 will das nicht"}, 409),
    ({"status": "nicht_eingerichtet", "summary": "Passwort fehlt (geraete.env)"}, 409),
    ({"status": "grenze", "summary": "20 je Minute"}, 429),
    ({"status": "error", "summary": "Fernseher nicht erreichbar 192.168.178.125"}, 502),
    ({"status": "irgendwas"}, 502),
    ({}, 502),
])
def test_antworten_des_dienstes_werden_ruhig_abgebildet(client, dienst, protokoll, antwort, status):
    dienst.steuern = antwort
    r = schalten(client)
    assert r.status_code == status
    assert not IP.search(r.text) and "geraete.env" not in r.text and "Passwort" not in r.text  # nie Gerätetexte durchreichen
    assert protokoll[-1][3] == "failed"


def test_dienst_nicht_erreichbar_ist_ein_503_und_steht_im_protokoll(client, dienst, protokoll):
    dienst.fehler = gc.GeraeteNichtVerfuegbar("Der Gerätedienst ist gerade nicht erreichbar.")
    assert schalten(client).status_code == 503
    assert protokoll == [(USER, "fernseher", "an", "failed", "nicht_erreichbar")]


def test_ohne_geraetedienst_503(client, dienst, protokoll):
    dienst.verfuegbar = False
    assert schalten(client).status_code == 503
    assert dienst.schaltungen() == []


# ---------- Grenzen und Protokoll ----------
def test_zwanzig_schaltvorgaenge_je_minute_und_profil(client, dienst, protokoll):
    for _ in range(20):
        assert schalten(client, "steckdose-1").status_code == 200
    assert schalten(client, "steckdose-1").status_code == 429
    assert len(dienst.schaltungen()) == 20
    assert client.get(PFAD).status_code == 200  # die Ansicht hat ihre eigene Grenze


def test_ansicht_und_schalten_zaehlen_getrennt(client, dienst, protokoll):
    for _ in range(6):
        assert client.get(PFAD).status_code == 200
    assert client.get(PFAD).status_code == 429
    assert schalten(client, "steckdose-1").status_code == 200


@pytest.mark.asyncio
async def test_protokollfehler_haelt_den_schaltvorgang_nie_auf(monkeypatch):
    def kaputt():
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(api, "shared_database", kaputt)
    await api._protokollieren(USER, "fernseher", "an", "done", "success")  # wirft nicht


# ---------- Aktionen im Stand (nur Admins) ----------
def aktionen_von(client) -> dict[str, list[str]]:
    return {g["id"]: g["aktionen"] for g in client.get(PFAD).json()["geraete"] if "aktionen" in g}


def test_admin_sieht_die_aktionen_je_geraet(client, dienst):
    dienst.antwort = {"status": "success", "geraete": [
        roh("fernseher", "fernseher", "LG", erreichbar=True, an=True, gekoppelt=True, lautstaerke=7),
        roh("fernseher-2", "fernseher", "Samsung", erreichbar=True, an=False, gekoppelt=True),
        roh("fernseher-3", "fernseher", "LG im Standby", erreichbar=False, an=False, gekoppelt=True),
        roh("steckdose-1", "steckdose", "S1", erreichbar=True, an=True, leistung_w=5.0),
        roh("steckdose-2", "steckdose", "S2", erreichbar=True, an=False),
        roh("lautsprecher", "lautsprecher", "Box", erreichbar=True),
        roh("drucker", "drucker", "Drucker", erreichbar=True, zustand="bereit"),
        roh("sokrates-2", "server", "Sokrates-2", erreichbar=True),
    ]}
    assert aktionen_von(client) == {
        "fernseher": ["aus", "leiser", "lauter", "stumm", "laut"],
        "fernseher-2": ["an"],
        "fernseher-3": ["an"],
        "steckdose-1": ["aus"],
        "steckdose-2": ["an"],
        "lautsprecher": ["leiser", "lauter", "stumm", "laut"],
    }


def test_keine_aktionen_ohne_werte_nicht_gekoppelt_oder_unklar(client, dienst):
    dienst.antwort = {"status": "success", "geraete": [
        roh("fernseher", "fernseher", "LG", erreichbar=True, an=True, gekoppelt=False),
        roh("fernseher-2", "fernseher", "Samsung", status="error", summary="weg"),
        roh("steckdose-1", "steckdose", "S1", erreichbar=True),
        roh("steckdose-2", "steckdose", "S2", erreichbar=False),
        roh("steckdose-3", "steckdose", "S3", status="nicht_eingerichtet"),
        roh("lautsprecher", "lautsprecher", "Box", erreichbar=False),
    ]}
    assert aktionen_von(client) == {}


@pytest.mark.parametrize("rolle", ["mitglied", "gast", ""])
def test_andere_rollen_sehen_nie_aktionen(client, dienst, ctx, rolle):
    ctx.rolle = rolle
    dienst.antwort = {"status": "success", "geraete": [roh("fernseher", "fernseher", "LG", erreichbar=True, an=True, gekoppelt=True)]}
    r = client.get(PFAD)
    assert r.status_code == 200 and "aktionen" not in r.text


def test_aktionen_sind_immer_aus_den_sechs(client):
    erlaubt = set(api.STEUER_AKTIONEN)
    for g in client.get(PFAD).json()["geraete"]:
        assert set(g.get("aktionen", [])) <= erlaubt
