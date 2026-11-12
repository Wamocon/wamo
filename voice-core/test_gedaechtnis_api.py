"""Gedächtnisvorschläge in der Oberfläche prüfen (gedaechtnis_api.py, Migration 0020): nur Admins, feste Eingaben, ruhige Fehler.

Ohne Netz und ohne Datenbank: die Datenbank ist eine Attrappe. Die Wirkung der Funktion gedaechtnis_vorschlag_entscheiden selbst prüft
supabase/tests/0020_gedaechtnis_vorschlaege_test.sql gegen wamo_test oder wamo_dev.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import asyncpg
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import gedaechtnis_api as api
from auth_gate import Identity

ADMIN = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
VORSCHLAG = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"
LISTE = "/wamo/api/gedaechtnis/vorschlaege"
ENTSCHEIDEN = "/wamo/api/gedaechtnis/entscheiden"
ZEIT = datetime(2026, 11, 12, 6, 58, 48, tzinfo=UTC)


# echte asyncpg-Klassen: sie tragen ihren SQLSTATE selbst (42501, 42883, XX000)
Fehler42501 = asyncpg.exceptions.InsufficientPrivilegeError
Fehler42883 = asyncpg.exceptions.UndefinedFunctionError
FehlerOhneCode = asyncpg.exceptions.InternalServerError


def zeile(**ueber) -> dict:
    return {"id": VORSCHLAG, "kind": "fact", "content": "WAMO kann Fernseher schalten.", "created_at": ZEIT, "autor": "Anna", **ueber}


class Db:
    """Attrappe der gemeinsamen Datenbank: merkt sich die Aufrufe und liefert feste Antworten."""

    configured = True

    def __init__(self):
        self.zeilen: list[dict] = [zeile()]
        self.ergebnis: object = "freigegeben"
        self.fehler: BaseException | None = None
        self.aufrufe: list[tuple] = []

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    async def fetch(self, sql: str, *args):
        self.aufrufe.append(("fetch", sql, args))
        if self.fehler:
            raise self.fehler
        return self.zeilen

    async def fetchval(self, sql: str, *args):
        self.aufrufe.append(("fetchval", sql, args))
        if self.fehler:
            raise self.fehler
        return self.ergebnis


class Rolle:
    rolle = "admin"


@pytest.fixture
def db(monkeypatch):
    d = Db()
    monkeypatch.setattr(api, "shared_database", lambda: d)
    return d


@pytest.fixture
def ctx(monkeypatch):
    k = Rolle()

    async def identitaet(_request):
        return Identity(user_id=ADMIN, name="Niko", rolle=k.rolle, anrede="du")

    monkeypatch.setattr(api, "identitaet", identitaet)
    api._lesen.clear()
    api._entscheiden.clear()
    return k


@pytest.fixture
def client(db, ctx):
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return TestClient(app)


def entscheide(client, **body):
    return client.post(ENTSCHEIDEN, json={"id": VORSCHLAG, "entscheidung": "freigeben", **body})


# ---------- Wer darf ----------
@pytest.mark.parametrize("rolle", ["mitglied", "gast", ""])
def test_nur_admins_lesen_und_entscheiden(client, db, ctx, rolle):
    ctx.rolle = rolle
    assert client.get(LISTE).status_code == 403
    assert entscheide(client).status_code == 403
    assert db.aufrufe == []


def test_ohne_anmeldung(db, monkeypatch):
    async def abgelehnt(_request):
        raise HTTPException(401, "nicht angemeldet")

    monkeypatch.setattr(api, "identitaet", abgelehnt)
    api._lesen.clear()
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    c = TestClient(app)
    assert c.get(LISTE).status_code == 401
    assert entscheide(c).status_code == 401
    assert db.aufrufe == []


def test_modul_ist_im_kern_eingehaengt():
    import wamo_api

    assert "gedaechtnis_api" in wamo_api.ROUTEN_MODULE


# ---------- Liste ----------
def test_liste_hat_nur_das_schmale_modell(client, db):
    r = client.get(LISTE)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.json() == {"anzahl": 1, "vorschlaege": [
        {"id": VORSCHLAG, "art": "fact", "text": "WAMO kann Fernseher schalten.", "autor": "Anna", "angelegt": "2026-11-12T06:58:48Z"}]}
    _art, sql, args = db.aufrufe[0]
    assert "m.bereich = 'gemeinsam'" in sql and "m.status = 'vorgeschlagen'" in sql and "m.is_active" in sql
    assert args == (api.MAX_VORSCHLAEGE,)


def test_texte_sind_daten_gesaeubert_und_begrenzt(client, db):
    db.zeilen = [zeile(content="Zeile eins\nZeile\tzwei\u202egedreht\u0000\u200b   Ende", autor="Evil\u202e\nName" + "x" * 200),
                 zeile(id="9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d", content="y" * 5000)]
    liste = client.get(LISTE).json()["vorschlaege"]
    assert liste[0]["text"] == "Zeile eins Zeile zwei gedreht Ende"
    assert len(liste[0]["autor"]) <= api.MAX_NAME and "\u202e" not in liste[0]["autor"] and "\n" not in liste[0]["autor"]
    assert len(liste[1]["text"]) == api.MAX_TEXT


def test_unbrauchbare_zeilen_fallen_weg_und_unbekannte_art_wird_fakt(client, db):
    db.zeilen = [zeile(id="kaputt"), zeile(content="   "), zeile(content=None), zeile(kind="irgendwas", autor=None)]
    liste = client.get(LISTE).json()["vorschlaege"]
    assert len(liste) == 1 and liste[0]["art"] == "fact" and liste[0]["autor"] == "unbekannt"


@pytest.mark.parametrize("art", ["fact", "preference", "behavior", "lesson", "skill", "episode"])
def test_alle_arten_gehen_durch(client, db, art):
    db.zeilen = [zeile(kind=art)]
    assert client.get(LISTE).json()["vorschlaege"][0]["art"] == art


def test_liste_bei_datenbankfehler_ruhig(client, db):
    db.fehler = FehlerOhneCode("relation wamo_test.memories does not exist 192.168.178.136")
    r = client.get(LISTE)
    assert r.status_code == 503
    assert "memories" not in r.text and not re.search(r"\d+\.\d+\.\d+\.\d+", r.text)


def test_ohne_datenbank_503(client, monkeypatch):
    class Unfertig:
        configured = False

    monkeypatch.setattr(api, "shared_database", lambda: Unfertig())
    assert client.get(LISTE).status_code == 503
    assert entscheide(client).status_code == 503

    def kaputt():
        raise ValueError("WAMO_DB_SCHEMA falsch")

    monkeypatch.setattr(api, "shared_database", kaputt)
    r = client.get(LISTE)
    assert r.status_code == 503 and "WAMO_DB_SCHEMA" not in r.text


# ---------- Entscheiden ----------
@pytest.mark.parametrize("entscheidung, ergebnis", [("freigeben", "freigegeben"), ("ablehnen", "abgelehnt")])
def test_entscheiden_ruft_die_funktion_mit_der_identitaet_des_admins(client, db, entscheidung, ergebnis):
    db.ergebnis = ergebnis
    r = entscheide(client, entscheidung=entscheidung)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ergebnis": ergebnis}
    assert r.headers["cache-control"] == "no-store"
    (art, sql, args) = db.aufrufe[0]
    assert art == "fetchval" and "gedaechtnis_vorschlag_entscheiden" in sql
    assert args == (VORSCHLAG, ADMIN, entscheidung)  # der Admin kommt aus dem Token, nicht aus dem Body


def test_der_admin_im_body_zaehlt_nie(client, db):
    entscheide(client, admin="00000000-0000-4000-8000-0000000000ff", user_id="00000000-0000-4000-8000-0000000000ff")
    assert db.aufrufe[0][2][1] == ADMIN


def test_die_kennung_wird_kanonisch_weitergegeben(client, db):
    entscheide(client, id=VORSCHLAG.upper())
    assert db.aufrufe[0][2][0] == VORSCHLAG


def test_nicht_offen_ist_ein_gueltiges_ergebnis(client, db):
    db.ergebnis = "nicht_offen"
    r = entscheide(client)
    assert r.status_code == 200 and r.json() == {"ok": True, "ergebnis": "nicht_offen"}


@pytest.mark.parametrize("body", [
    {}, {"id": VORSCHLAG}, {"entscheidung": "freigeben"},
    {"id": "kaputt", "entscheidung": "freigeben"}, {"id": 12345, "entscheidung": "freigeben"}, {"id": None, "entscheidung": "freigeben"},
    {"id": VORSCHLAG, "entscheidung": "aktiv"}, {"id": VORSCHLAG, "entscheidung": "Freigeben"}, {"id": VORSCHLAG, "entscheidung": ["freigeben"]},
    {"id": VORSCHLAG, "entscheidung": None}, {"id": VORSCHLAG, "entscheidung": ""},
    {"id": "'; drop table memories; --", "entscheidung": "freigeben"},
])
def test_ungueltige_angaben_erreichen_die_datenbank_nie(client, db, body):
    assert client.post(ENTSCHEIDEN, json=body).status_code == 400
    assert db.aufrufe == []


def test_kein_gueltiges_json_und_zu_grosser_body(client, db):
    assert client.post(ENTSCHEIDEN, content=b"{kaputt").status_code == 400
    assert client.post(ENTSCHEIDEN, content=b"[1]").status_code == 400
    assert entscheide(client, x="y" * 2000).status_code == 413
    assert db.aufrufe == []


@pytest.mark.parametrize("fehler, status", [
    (Fehler42501("nur ein aktiver Admin"), 403),
    (Fehler42883("function wamo_test.gedaechtnis_vorschlag_entscheiden does not exist"), 503),
    (FehlerOhneCode("irgendwas mit 192.168.178.136"), 503),
    (OSError("Verbindung getrennt"), 503),
    (TimeoutError(), 503),
])
def test_datenbankfehler_werden_ruhig_abgebildet(client, db, fehler, status):
    db.fehler = fehler
    r = entscheide(client)
    assert r.status_code == status
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", r.text) and "wamo_test" not in r.text and "function" not in r.text


def test_fehlende_migration_wird_beim_namen_genannt(client, db):
    db.fehler = Fehler42883("function does not exist")
    assert "0020" in entscheide(client).json()["detail"]


@pytest.mark.parametrize("ergebnis", [None, "ok", "", 5, True, ["freigegeben"]])
def test_unbrauchbare_antwort_der_datenbank_ist_502(client, db, ergebnis):
    db.ergebnis = ergebnis
    assert entscheide(client).status_code == 502


# ---------- Grenzen ----------
def test_dreissig_abrufe_und_dreissig_entscheidungen_je_minute_getrennt(client, db):
    for _ in range(30):
        assert client.get(LISTE).status_code == 200
    assert client.get(LISTE).status_code == 429
    for _ in range(30):
        assert entscheide(client).status_code == 200
    assert entscheide(client).status_code == 429
    assert len([a for a in db.aufrufe if a[0] == "fetchval"]) == 30


def test_grenze_laeuft_ab():
    speicher: dict = {}
    for _ in range(30):
        api._drosseln(speicher, "x", 100.0)
    with pytest.raises(HTTPException):
        api._drosseln(speicher, "x", 100.0)
    api._drosseln(speicher, "x", 161.0)  # nach 60 Sekunden wieder frei
