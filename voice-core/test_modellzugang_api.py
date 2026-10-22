"""Tests der HTTP-Schnittstelle des Modellzugangs ohne Gespräch (modellzugang_api.py): geschützt durch die geprüfte
Identität, Profil nur aus der Identität, nie ein Schlüssel in Antworten oder Logs, konkrete Fehlermeldungen.
Datenbank und Anthropic sind durch Attrappen ersetzt."""

from __future__ import annotations

import json

import anthropic
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from loguru import logger

import modellzugang as mz
import modellzugang_api as api
from auth_gate import Identity
from schluessel import kek_erzeugen
from test_modellwahl import API, FakeDb, NIKO, TOKEN


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(mz, "shared_database", lambda: fake)
    monkeypatch.setenv("WAMO_SCHLUESSEL_KEK", kek_erzeugen())
    return fake


@pytest.fixture
def client(db, monkeypatch):
    async def identitaet(_request):
        return Identity(user_id=NIKO, name="Niko", rolle="mitglied", anrede="du", anmeldung=1.0)

    monkeypatch.setattr(api, "identitaet", identitaet)
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return TestClient(app)


@pytest.fixture
def log():
    zeilen: list[str] = []
    senke = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    yield zeilen
    logger.remove(senke)


def _anthropic(monkeypatch, fehler=None):
    class Client:
        def __init__(self, **kw):
            self.messages = self

        async def create(self, **kw):
            if fehler:
                raise fehler

        async def close(self):
            pass

    monkeypatch.setattr(mz.anthropic, "AsyncAnthropic", Client)


def test_stand_ohne_gespraech(client):
    r = client.get("/wamo/api/modellzugang")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert (d["type"], d["aktion"], d["art"], d["modus"], d["code_hinterlegt"]) == ("modellzugang", "status", "haus", "automatisch", False)


def test_modus_setzen(client):
    d = client.post("/wamo/api/modellzugang", json={"aktion": "modus", "modus": "nur_haus"}).json()
    assert (d["aktion"], d["modus"], d["quelle"], d["art"]) == ("modus", "nur_haus", "wahl", "haus")
    assert d["wirksam"] == "ab der nächsten Sitzung"
    assert client.get("/wamo/api/modellzugang").json()["modus"] == "nur_haus"


def test_token_hinterlegen_nur_format_keine_testanfrage(client, db, monkeypatch, log):
    def nie(**kw):
        raise AssertionError("für den Claude-Code-Token gibt es keine Testanfrage")

    monkeypatch.setattr(mz.anthropic, "AsyncAnthropic", nie)
    r = client.post("/wamo/api/modellzugang", json={"aktion": "hinterlegen", "schluesselart": "code", "schluessel": TOKEN})
    d = r.json()
    assert r.status_code == 200 and d["code_hinterlegt"] is True and d["code_ende"] == TOKEN[-4:] and "fehler" not in d
    assert TOKEN not in r.text and TOKEN not in json.dumps(db.zeilen[NIKO], default=str)
    assert all(TOKEN not in z for z in log)


def test_api_schluessel_mit_testanfrage(client, monkeypatch, log):
    _anthropic(monkeypatch)
    r = client.post("/wamo/api/modellzugang", json={"aktion": "hinterlegen", "schluesselart": "api", "schluessel": API})
    d = r.json()
    assert d["hinterlegt"] is True and d["ende"] == API[-4:] and d["art"] == "claude"
    assert API not in r.text and all(API not in z for z in log)


def test_abgelehnter_schluessel_hat_eine_konkrete_meldung(client, monkeypatch, log):
    _anthropic(monkeypatch, anthropic.AuthenticationError(
        "x", response=httpx.Response(401, request=httpx.Request("POST", "https://a.invalid")), body=None))
    d = client.post("/wamo/api/modellzugang", json={"aktion": "hinterlegen", "schluessel": API}).json()
    assert "401" in d["fehler"] and "ungültig, widerrufen" in d["fehler"] and d["hinterlegt"] is False
    assert API not in json.dumps(d) and all(API not in z for z in log)


def test_zeitueberschreitung_bei_anthropic(client, monkeypatch):
    _anthropic(monkeypatch, anthropic.APITimeoutError(request=httpx.Request("POST", "https://a.invalid")))
    d = client.post("/wamo/api/modellzugang", json={"aktion": "hinterlegen", "schluessel": API}).json()
    assert "Zeitüberschreitung" in d["fehler"] and "10 s" in d["fehler"]


def test_falsches_feld_sagt_welches_richtig_ist(client):
    d = client.post("/wamo/api/modellzugang", json={"aktion": "hinterlegen", "schluesselart": "api", "schluessel": TOKEN}).json()
    assert "Claude-Code-Token" in d["fehler"] and "Feld" in d["fehler"] and TOKEN not in json.dumps(d)
    d = client.post("/wamo/api/modellzugang", json={"aktion": "hinterlegen", "schluesselart": "code", "schluessel": API}).json()
    assert "API-Schlüssel" in d["fehler"] and API not in json.dumps(d)
    d = client.post("/wamo/api/modellzugang", json={"aktion": "hinterlegen", "schluesselart": "code", "schluessel": "quatsch"}).json()
    assert "kein Claude-Code-Token" in d["fehler"]


def test_loeschen(client):
    client.post("/wamo/api/modellzugang", json={"aktion": "hinterlegen", "schluesselart": "code", "schluessel": TOKEN})
    d = client.post("/wamo/api/modellzugang", json={"aktion": "loeschen", "schluesselart": "code"}).json()
    assert d["code_hinterlegt"] is False


def test_profil_kommt_nur_aus_der_identitaet(client, db):
    # eine fremde Profil-ID im Body wird ignoriert
    client.post("/wamo/api/modellzugang", json={"aktion": "modus", "modus": "nur_haus",
                                                 "user_id": "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"})
    assert list(db.zeilen) == [NIKO]


def test_ohne_identitaet_nichts(db, monkeypatch):
    async def abgelehnt(_request):
        raise HTTPException(401, "nicht angemeldet")

    monkeypatch.setattr(api, "identitaet", abgelehnt)
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    c = TestClient(app)
    assert c.get("/wamo/api/modellzugang").status_code == 401
    assert c.post("/wamo/api/modellzugang", json={"aktion": "modus", "modus": "nur_haus"}).status_code == 401
    assert db.zeilen == {} and db.sql == []


def test_body_grenzen(client):
    assert client.post("/wamo/api/modellzugang", content=b"kein json").status_code == 400
    assert client.post("/wamo/api/modellzugang", content=b"[1]").status_code == 400
    zu_gross = json.dumps({"aktion": "hinterlegen", "schluessel": "x" * (api.MAX_BODY_BYTES + 10)})
    assert client.post("/wamo/api/modellzugang", content=zu_gross.encode()).status_code == 413


def test_datenbank_weg_meldet_den_fehler_ohne_inhalt(client, db):
    import asyncpg

    db.fehler_bei_ausfuehren = None

    async def kaputt(sql, *args):
        raise asyncpg.PostgresConnectionError("weg")

    db.execute = kaputt
    d = client.post("/wamo/api/modellzugang", json={"aktion": "modus", "modus": "nur_haus"}).json()
    assert d["fehler"] and TOKEN not in json.dumps(d)


def test_modul_ist_im_kern_eingehaengt():
    import wamo_api

    assert "modellzugang_api" in wamo_api.ROUTEN_MODULE
