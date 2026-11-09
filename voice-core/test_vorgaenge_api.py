"""Tests der Route GET /wamo/api/vorgaenge (vorgaenge_api.py): die letzten 20 Vorgänge des eigenen Profils, nur eigene Zeilen,
Token und aktives Profil über die Zugangskontrolle, tolerante Form, keine Datenbankmeldungen nach außen. Keine echte Datenbank."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from loguru import logger

import vorgaenge_api as api
import wamo_api
from auth_gate import Identity

ICH = "123e4567-e89b-12d3-a456-426614174000"
ANDERER = "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"
FELDER = {"id", "titel", "art", "status", "gestartet_ms", "beendet_ms", "gesamt", "restzeit_s", "schritte", "kriterien",
          "runde", "max_runden", "freigabe_id", "ergebnis"}


def zeile(i: int, user: str = ICH, **extra) -> dict:
    daten = {"schritte": [{"nr": 1, "titel": "Bauen", "status": "fertig", "fortschritt": 1.0, "detail": None}],
             "kriterien": [{"text": "Tests", "erfuellt": True, "hinweis": None}], "gestartet_ms": 1_700_000_000_000 + i}
    z = {"id": f"00000000-0000-4000-8000-{i:012d}", "user_id": user, "art": "entwicklung", "titel": f"Auftrag {i}",
         "status": "fertig", "gesamt": 1.0, "daten": json.dumps(daten),
         "gestartet_at": datetime(2026, 11, 9, 12, i % 60, tzinfo=timezone.utc),
         "beendet_at": datetime(2026, 11, 9, 12, i % 60, 30, tzinfo=timezone.utc)}
    z.update(extra)
    return z


class FalscheDatenbank:
    """Tabelle vorgaenge im Speicher; wertet nur das aus, was die Route fragt (Profil und Grenze)."""

    configured = True

    def __init__(self, zeilen: list[dict]):
        self.zeilen = zeilen
        self.abfragen: list[tuple[str, tuple]] = []
        self.fehler: Exception | None = None

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    async def fetch(self, sql: str, *args):
        self.abfragen.append((sql, args))
        if self.fehler:
            raise self.fehler
        eigene = [z for z in self.zeilen if z["user_id"] == args[0]]
        # die Sortierung der Abfrage nachbilden: bei gleichem Beginn entscheidet nur eine genannte zweite Spalte (sonst gilt die
        # Reihenfolge der Ablage, wie bei einer Datenbank ohne eindeutige Sortierung)
        ordnung = sql.split(" order by ", 1)[1].split(" limit", 1)[0]
        schluessel = {"gestartet_at desc, id desc": lambda z: (z["gestartet_at"], z["id"]),
                      "gestartet_at desc": lambda z: z["gestartet_at"]}[ordnung]
        return sorted(eigene, key=schluessel, reverse=True)[: args[1]]


class Kontext:
    gesperrt = False
    user = ICH


@pytest.fixture
def ctx(monkeypatch):
    k = Kontext()

    async def identitaet(_request):
        if k.gesperrt:
            raise HTTPException(403, "kein aktives WAMO-Profil oder zweiter Faktor fehlt")
        return Identity(user_id=k.user, name="Ich", rolle="mitglied", anrede="du", anmeldung=1.0)

    monkeypatch.setattr(api, "identitaet", identitaet)
    return k


@pytest.fixture
def db(monkeypatch):
    fake = FalscheDatenbank([zeile(i) for i in range(1, 31)] + [zeile(99, ANDERER, titel="Fremd")])
    monkeypatch.setattr(api, "shared_database", lambda: fake)
    return fake


@pytest.fixture
def client(ctx, db):
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return TestClient(app)


def test_route_ist_in_der_modulliste_der_kern_api():
    assert "vorgaenge_api" in wamo_api.ROUTEN_MODULE


def test_letzte_zwanzig_eigene_vorgaenge_neueste_zuerst(client, db):
    r = client.get("/wamo/api/vorgaenge")
    assert r.status_code == 200
    liste = r.json()["vorgaenge"]
    assert len(liste) == 20 == api.MAX_VORGAENGE
    assert [v["titel"] for v in liste][:2] == ["Auftrag 30", "Auftrag 29"]
    assert all(set(v) == FELDER for v in liste)
    assert all(v["restzeit_s"] is None for v in liste)
    assert liste[0]["schritte"][0]["titel"] == "Bauen" and liste[0]["kriterien"][0]["erfuellt"] is True
    assert liste[0]["gestartet_ms"] == 1_700_000_000_030


def test_nur_eigene_zeilen_das_profil_kommt_aus_der_zugangskontrolle_nie_aus_der_anfrage(client, db, ctx):
    r = client.get("/wamo/api/vorgaenge", params={"user_id": ANDERER}, headers={"X-User": ANDERER})
    assert all(v["titel"] != "Fremd" for v in r.json()["vorgaenge"])
    sql, args = db.abfragen[0]
    assert "where user_id = $1::uuid" in sql and "limit $2" in sql and args == (ICH, 20)
    assert "wamo_test.vorgaenge" in sql
    ctx.user = ANDERER
    assert [v["titel"] for v in client.get("/wamo/api/vorgaenge").json()["vorgaenge"]] == ["Fremd"]


def test_ohne_zugang_kommt_nichts_und_die_datenbank_wird_nicht_gefragt(client, db, ctx):
    ctx.gesperrt = True
    r = client.get("/wamo/api/vorgaenge")
    assert r.status_code == 403 and db.abfragen == []


def test_stabile_reihenfolge_bei_gleichem_beginn_neueste_zuerst_dann_kennung_absteigend(client, db):
    gleich = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)
    db.zeilen = [zeile(5, gestartet_at=gleich, titel="Fuenf"), zeile(6, gestartet_at=gleich, titel="Sechs"),
                 zeile(7, gestartet_at=gleich, titel="Sieben"), zeile(1, titel="Alt")]
    liste = client.get("/wamo/api/vorgaenge").json()["vorgaenge"]
    assert [v["titel"] for v in liste] == ["Sieben", "Sechs", "Fuenf", "Alt"]
    sql, _ = db.abfragen[0]
    assert "order by gestartet_at desc, id desc limit $2" in sql


def test_nur_get_ist_erlaubt(client):
    for methode in ("post", "put", "delete", "patch"):
        assert getattr(client, methode)("/wamo/api/vorgaenge").status_code == 405


def test_datenbankfehler_wird_zu_503_ohne_interne_meldung(client, db):
    zeilen: list[str] = []
    senke = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    try:
        db.fehler = ConnectionError("Passwort hunter2 für postgres://wamo_core@host")
        r = client.get("/wamo/api/vorgaenge")
    finally:
        logger.remove(senke)
    assert r.status_code == 503
    assert "hunter2" not in r.text and "postgres" not in r.text
    assert not any("hunter2" in z for z in zeilen) and any("ConnectionError" in z for z in zeilen)


def test_ohne_eingerichtete_datenbank_503(client, monkeypatch):
    class Aus:
        configured = False

    monkeypatch.setattr(api, "shared_database", lambda: Aus())
    assert client.get("/wamo/api/vorgaenge").status_code == 503

    def kaputt():
        raise ValueError("WAMO_DB_SCHEMA ungültig")

    monkeypatch.setattr(api, "shared_database", kaputt)
    assert client.get("/wamo/api/vorgaenge").status_code == 503


def test_kaputte_daten_in_einer_zeile_ergeben_trotzdem_eine_sichere_form(client, db):
    db.zeilen = [zeile(1, daten="{kein json", art="böse", status="x", titel="T\x00x")]
    (v,) = client.get("/wamo/api/vorgaenge").json()["vorgaenge"]
    assert v["art"] == "sonstiges" and v["status"] == "fehler" and v["schritte"] == [] and "\x00" not in v["titel"]


def test_antwort_wird_nicht_zwischengespeichert(client):
    assert client.get("/wamo/api/vorgaenge").headers["cache-control"] == "no-store"
