"""Tests der Geräteverwaltung für Admins (geraete_admin_api.py), ohne Datenbank und ohne Netz.

Die Regeln der Übergänge selbst prüft supabase/tests/0014_geraete_verwaltung_test.sql gegen wamo_test. Hier: wer darf
(Rolle, frischer zweiter Faktor, Profil), welche Eingaben gelten, was an die Datenbank geht (nur die beiden Funktionen,
Akteur nur aus der Identität), wie Ergebnisse und Fehler gemeldet werden, Rate-Grenzen und Verbotsproben.

Ausführen:  uv run python -m pytest -q test_geraete_admin_api.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from loguru import logger

import geraete_admin_api as api
from auth_gate import Identity

ADMIN = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
ANDERER = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"
FP = "SHA256:" + "vxe+7I/Wv6VHjKAOQpvJtkZDcHvINq3CgURh2MotlRg"[:43]
KENNUNG = "pc-anna-pc"


def token(*, aal: str = "aal2", totp_vor: float | None = 10.0, passwort_vor: float = 3600.0, methode: str = "totp",
          amr: object = "auto") -> str:
    jetzt = time.time()
    if amr == "auto":
        amr = [{"method": "password", "timestamp": int(jetzt - passwort_vor)}]
        if totp_vor is not None:
            amr.append({"method": methode, "timestamp": int(jetzt - totp_vor)})
    return jwt.encode({"sub": ADMIN, "aal": aal, "amr": amr}, "nur-fuer-tests-nur-fuer-tests-123456", algorithm="HS256")


def zeile(**ueber) -> dict:
    z = {"kennung": KENNUNG, "name": "Annas Laptop", "hostname": "ANNA-PC", "profil_id": ANDERER, "profil_name": "Anna",
         "profil_aktiv": True, "status": "beantragt", "bindung": "admin", "fingerabdruck": FP,
         "erstellt_at": datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc), "freigegeben_at": None, "gesperrt_at": None,
         "gesperrt_grund": None, "zuletzt_gesehen": None, "ereignisse": "[]"}
    z.update(ueber)
    return z


class FakeDb:
    configured = True

    def __init__(self):
        self.sql: list[tuple[str, tuple]] = []
        self.liste: list[dict] = [zeile()]
        self.echter_fp: str | None = FP
        self.antwort: dict | None = {"kennung": KENNUNG, "vorher": "beantragt", "nachher": "aktiv", "ergebnis": "ok"}
        self.fehler: BaseException | None = None

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    async def fetch(self, sql, *args):
        self.sql.append((sql, args))
        if self.fehler:
            raise self.fehler
        return self.liste

    async def fetchval(self, sql, *args):
        self.sql.append((sql, args))
        if self.fehler:
            raise self.fehler
        return self.echter_fp

    async def fetchrow(self, sql, *args):
        self.sql.append((sql, args))
        if self.fehler:
            raise self.fehler
        return self.antwort


class Kontext:
    rolle = "admin"
    tok: str | None = None


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(api, "shared_database", lambda: fake)
    return fake


@pytest.fixture
def ctx(monkeypatch):
    k = Kontext()
    k.tok = token()

    async def identitaet(_request):
        return Identity(user_id=ADMIN, name="Admin", rolle=k.rolle, anrede="du", anmeldung=1.0)

    monkeypatch.setattr(api, "identitaet", identitaet)
    monkeypatch.setattr(api, "bearer", lambda _request: k.tok)
    api._versuche.clear()
    return k


@pytest.fixture
def client(db, ctx):
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return TestClient(app)


@pytest.fixture
def log():
    zeilen: list[str] = []
    senke = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    yield zeilen
    logger.remove(senke)


def _post(client, **body):
    base = {"kennung": KENNUNG, "aktion": "freigeben", "fingerabdruck": FP}
    base.update(body)
    return client.post("/wamo/api/geraete/verwaltung/aktion", json={k: v for k, v in base.items() if v is not None})


# ---------- reine Prüfungen ----------
def test_aal2_zeit_nimmt_den_letzten_code_nicht_das_passwort():
    jetzt = time.time()
    t = token(totp_vor=30, passwort_vor=7200)
    assert abs(api.aal2_zeit(t) - (jetzt - 30)) < 3
    zwei = token(amr=[{"method": "totp", "timestamp": 1000}, {"method": "totp", "timestamp": 2000},
                      {"method": "password", "timestamp": 5000}])
    assert api.aal2_zeit(zwei) == 2000
    assert api.aal2_zeit(token(methode="mfa/totp")) is not None


@pytest.mark.parametrize("t", [
    token(aal="aal1"), token(totp_vor=None), token(amr="x"), token(amr=[]), token(amr=[{"method": "totp"}]),
    token(amr=[{"method": "totp", "timestamp": "1"}]), token(amr=[{"method": "totp", "timestamp": True}]),
    token(amr=[{"method": "totp", "timestamp": -5}]), token(amr=[{"method": "password", "timestamp": 5}]),
    "kein.jwt", "", "a.b.c",
])
def test_aal2_zeit_fehlt_bei_allem_unklaren(t):
    assert api.aal2_zeit(t) is None


def test_aal2_frisch_grenzen():
    assert not api.aal2_frisch(None)
    assert api.aal2_frisch(1000.0, 1000.0 + 119)
    assert api.aal2_frisch(1000.0, 1000.0 + 120)
    assert not api.aal2_frisch(1000.0, 1000.0 + 121)
    assert api.aal2_frisch(1000.0, 1000.0 - 3)         # Uhren laufen etwas auseinander
    assert not api.aal2_frisch(1000.0, 1000.0 - 10)    # aber nicht weit in die Zukunft


@pytest.mark.parametrize("aktion,roh,erwartet", [
    ("freigeben", None, None), ("freigeben", "  ", None), ("sperren", "  Laptop   verloren ", "Laptop verloren"),
    ("widerrufen", "abc", "abc"),
])
def test_grund_gueltig(aktion, roh, erwartet):
    assert api.grund_pruefen(roh, aktion) == erwartet


@pytest.mark.parametrize("aktion,roh", [
    ("sperren", None), ("sperren", ""), ("widerrufen", "ab"), ("widerrufen", "  a  "), ("sperren", "x" * 201),
    ("freigeben", "x" * 201), ("sperren", "Test\x00"), ("sperren", "Test‮"), ("sperren", 5), ("sperren", ["x"]),
])
def test_grund_ungueltig(aktion, roh):
    with pytest.raises(HTTPException) as e:
        api.grund_pruefen(roh, aktion)
    assert e.value.status_code == 400


# ---------- Liste ----------
def test_liste_nur_fuer_admins(client, db, ctx):
    ctx.rolle = "mitglied"
    assert client.get("/wamo/api/geraete/verwaltung").status_code == 403
    assert db.sql == []


def test_liste_ohne_anmeldung(db, monkeypatch):
    async def abgelehnt(_request):
        raise HTTPException(401, "nicht angemeldet")

    monkeypatch.setattr(api, "identitaet", abgelehnt)
    api._versuche.clear()
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    c = TestClient(app)
    assert c.get("/wamo/api/geraete/verwaltung").status_code == 401
    assert c.post("/wamo/api/geraete/verwaltung/aktion", json={}).status_code == 401
    assert db.sql == []


def test_liste_felder_ohne_geheimnisse(client, db):
    db.liste = [zeile(), zeile(kennung="pc-niko-pc", profil_id=ADMIN, profil_name="Niko", status="aktiv", bindung="automatisch",
                               ereignisse=json.dumps([{"von": "beantragt", "nach": "aktiv", "durch": "Admin", "grund": None,
                                                       "at": "2026-10-30T09:00:00+00:00"}]))]
    r = client.get("/wamo/api/geraete/verwaltung")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    g = r.json()["geraete"]
    assert [x["kennung"] for x in g] == [KENNUNG, "pc-niko-pc"]
    assert g[0]["fingerabdruck"] == FP and g[0]["profil"] == {"name": "Anna", "aktiv": True}
    assert g[0]["eigen"] is False and g[1]["eigen"] is True      # eigenes Gerät des Admins ist markiert
    assert g[1]["ereignisse"][0]["nach"] == "aktiv" and g[1]["ereignisse"][0]["durch"] == "Admin"
    text = json.dumps(r.json())
    assert "ssh-ed25519" not in text and "pubkey" not in text
    # die Abfrage geht nur an die Funktion, mit der Profil-ID aus der Identität
    sql, args = db.sql[0]
    assert "geraete_verwaltung_liste" in sql and args == (ADMIN,)


def test_liste_laesst_kaputte_zeilen_weg(client, db):
    db.liste = [zeile(kennung="../etc"), zeile(status="irgendwas"), {"kennung": "pc-x"}, zeile(fingerabdruck="SHA256:kurz"),
                zeile(kennung="pc-ok", ereignisse="kein json")]
    g = client.get("/wamo/api/geraete/verwaltung").json()["geraete"]
    assert [x["kennung"] for x in g] == [KENNUNG, "pc-ok"]
    assert g[0]["fingerabdruck"] is None and g[1]["ereignisse"] == []


def test_liste_texte_sind_bereinigt_und_begrenzt(client, db):
    db.liste = [zeile(name="A\x00B" + "x" * 300, profil_name="<b>Anna</b>‮", gesperrt_grund="g\r\nh")]
    d = client.get("/wamo/api/geraete/verwaltung").json()["geraete"][0]
    assert len(d["name"]) == 100 and "\x00" not in d["name"]
    assert "‮" not in d["profil"]["name"] and "\n" not in d["grund"]


def test_liste_datenbankfehler(client, db):
    db.fehler = asyncpg.PostgresConnectionError("weg")
    r = client.get("/wamo/api/geraete/verwaltung")
    assert r.status_code == 503 and "nicht verfügbar" in r.json()["detail"] and "weg" not in r.json()["detail"]


def test_liste_ohne_recht_in_der_datenbank(client, db):
    fehler = asyncpg.InsufficientPrivilegeError("x")
    db.fehler = fehler
    assert client.get("/wamo/api/geraete/verwaltung").status_code == 403


def test_liste_ohne_datenbank(client, monkeypatch):
    kaputt = FakeDb()
    kaputt.configured = False
    monkeypatch.setattr(api, "shared_database", lambda: kaputt)
    assert client.get("/wamo/api/geraete/verwaltung").status_code == 503


def test_liste_rate_grenze(client):
    for _ in range(api.LISTE_GRENZE[0]):
        assert client.get("/wamo/api/geraete/verwaltung").status_code == 200
    assert client.get("/wamo/api/geraete/verwaltung").status_code == 429


# ---------- Aktion: Zugang ----------
def test_aktion_nur_fuer_admins(client, db, ctx):
    ctx.rolle = "mitglied"
    assert _post(client).status_code == 403
    assert db.sql == []


def test_aktion_ohne_frischen_zweiten_faktor(client, db, ctx):
    for t in (token(totp_vor=121), token(totp_vor=None), token(aal="aal1"), token(totp_vor=-30), "kaputt"):
        ctx.tok = t
        r = _post(client)
        assert r.status_code == 403 and r.json()["code"] == api.CODE_AAL2, t
        assert "Code" in r.json()["detail"]
    assert db.sql == []                  # nichts an die Datenbank, auch nicht die Fingerabdruck-Probe


def test_aktion_zweiter_faktor_knapp_genug(client, db, ctx):
    ctx.tok = token(totp_vor=100)
    assert _post(client).status_code == 200


def test_aktion_datenbank_lehnt_zu_alten_faktor_ab(client, db):
    e = asyncpg.CheckViolationError("geraet_entscheiden: der zweite Faktor ist älter als 120 Sekunden")
    db.fehler = None

    async def fetchrow(sql, *a):
        raise e

    db.fetchrow = fetchrow
    r = _post(client)
    assert r.status_code == 403 and r.json()["code"] == api.CODE_AAL2


def test_andere_pruefungsfehler_sind_kein_zweiter_faktor(client, db):
    async def fetchrow(sql, *a):
        raise asyncpg.CheckViolationError("devices: Statuswechsel nicht erlaubt")

    db.fetchrow = fetchrow
    r = _post(client)
    assert r.status_code == 503 and "code" not in r.json()


@pytest.mark.parametrize("fehler,status", [
    (asyncpg.InsufficientPrivilegeError("x"), 403), (asyncpg.InvalidParameterValueError("x"), 400),
    (asyncpg.PostgresConnectionError("weg"), 503), (OSError("weg"), 503),
])
def test_fehler_der_funktion_selbst(client, db, fehler, status):
    async def fetchrow(sql, *a):
        raise fehler

    db.fetchrow = fetchrow
    r = _post(client, aktion="sperren", grund="Laptop verloren", fingerabdruck=None)
    assert r.status_code == status
    if status == 503:
        assert "nichts geändert" not in r.json()["detail"]      # nach einem Verbindungsfehler ist offen, ob es gelaufen ist


def test_grund_darf_mehrzeilig_sein(client, db):
    db.antwort = {"kennung": KENNUNG, "vorher": "aktiv", "nachher": "gesperrt", "ergebnis": "ok"}
    r = _post(client, aktion="sperren", grund="Laptop verloren" + chr(10) + "und nicht gemeldet", fingerabdruck=None)
    assert r.status_code == 200
    assert db.sql[-1][1][3] == "Laptop verloren und nicht gemeldet"


# ---------- Aktion: Eingaben ----------
@pytest.mark.parametrize("body", [
    {"kennung": "PC-ANNA"}, {"kennung": "drucker-x"}, {"kennung": "pc-" + "a" * 30}, {"kennung": 5}, {"kennung": "pc-a'; drop"},
    {"kennung": "../pc-a"}, {"aktion": "loeschen"}, {"aktion": None}, {"aktion": ["freigeben"]},
])
def test_aktion_ungueltige_eingaben(client, db, body):
    r = _post(client, **body) if "aktion" not in body or body["aktion"] is not None else client.post(
        "/wamo/api/geraete/verwaltung/aktion", json={"kennung": KENNUNG})
    assert r.status_code == 400
    assert db.sql == []


def test_aktion_grund_ist_pflicht_bei_sperren_und_widerruf(client, db):
    for a in ("sperren", "widerrufen"):
        assert _post(client, aktion=a).status_code == 400
        assert _post(client, aktion=a, grund="ab").status_code == 400
    assert db.sql == []


def test_aktion_freigeben_und_entsperren_brauchen_den_fingerabdruck(client, db):
    for a in ("freigeben", "entsperren"):
        assert _post(client, aktion=a, fingerabdruck=None).status_code == 400
        assert _post(client, aktion=a, fingerabdruck="SHA256:zu-kurz").status_code == 400
        assert _post(client, aktion=a, fingerabdruck=5).status_code == 400
    assert db.sql == []


def test_aktion_body_grenzen(client):
    assert client.post("/wamo/api/geraete/verwaltung/aktion", content=b"kein json").status_code == 400
    assert client.post("/wamo/api/geraete/verwaltung/aktion", content=b"[1]").status_code == 400
    gross = json.dumps({"kennung": KENNUNG, "aktion": "sperren", "grund": "x" * (api.MAX_BODY + 10)})
    assert client.post("/wamo/api/geraete/verwaltung/aktion", content=gross.encode()).status_code == 413


# ---------- Aktion: Ablauf ----------
def test_freigeben_ruft_die_funktion_mit_dem_akteur_aus_der_identitaet(client, db):
    r = _post(client, admin=ANDERER, user_id=ANDERER, kennung=KENNUNG)    # fremde Felder im Body zählen nicht
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["ergebnis"] == "ok" and d["status"] == "aktiv" and d["vorher"] == "beantragt"
    assert KENNUNG in d["text"] and "freigegeben" in d["text"]
    probe, entscheiden = db.sql
    assert "ssh_fingerprint" in probe[0] and probe[1] == (KENNUNG,)
    sql, args = entscheiden
    assert "geraet_entscheiden" in sql
    assert args[0] == ADMIN and args[1] == KENNUNG and args[2] == "freigeben" and args[3] is None
    assert isinstance(args[4], datetime) and args[4].tzinfo is not None
    assert abs((datetime.now(timezone.utc) - args[4]).total_seconds() - 10) < 5   # die Zeit des Codes aus dem Token


def test_sperren_mit_grund_ohne_fingerabdruckprobe(client, db):
    db.antwort = {"kennung": KENNUNG, "vorher": "aktiv", "nachher": "gesperrt", "ergebnis": "ok"}
    r = _post(client, aktion="sperren", grund="  Laptop   verloren ", fingerabdruck=None)
    assert r.status_code == 200 and r.json()["status"] == "gesperrt" and "gesperrt" in r.json()["text"]
    assert len(db.sql) == 1 and db.sql[0][1][3] == "Laptop verloren"


def test_widerruf_und_ablehnen_haben_eigene_texte(client, db):
    db.antwort = {"kennung": KENNUNG, "vorher": "beantragt", "nachher": "widerrufen", "ergebnis": "ok"}
    assert "abgelehnt" in _post(client, aktion="widerrufen", grund="Unbekanntes Gerät").json()["text"]
    db.antwort = {"kennung": KENNUNG, "vorher": "aktiv", "nachher": "widerrufen", "ergebnis": "ok"}
    t = _post(client, aktion="widerrufen", grund="Laptop ersetzt").json()["text"]
    assert "widerrufen" in t and "endgültig" in t


def test_entsperren(client, db):
    db.antwort = {"kennung": KENNUNG, "vorher": "gesperrt", "nachher": "aktiv", "ergebnis": "ok"}
    r = _post(client, aktion="entsperren")
    assert r.status_code == 200 and "wieder freigegeben" in r.json()["text"]


def test_doppelte_aktion_ist_folgenlos_und_klar(client, db):
    assert _post(client).json()["ergebnis"] == "ok"
    db.antwort = {"kennung": KENNUNG, "vorher": "aktiv", "nachher": "aktiv", "ergebnis": "schon"}
    r = _post(client)
    assert r.status_code == 200 and r.json()["ergebnis"] == "schon"
    assert "nichts geändert" in r.json()["text"] and "freigegeben" in r.json()["text"]


def test_fingerabdruck_passt_nicht(client, db):
    db.echter_fp = "SHA256:" + "B" * 43
    r = _post(client)
    assert r.status_code == 409 and "Fingerabdruck" in r.json()["detail"]
    assert len(db.sql) == 1               # die Funktion wurde nicht aufgerufen


def test_unbekannte_kennung(client, db):
    db.echter_fp = None
    r = _post(client)
    assert r.status_code == 404 and "gibt es nicht" in r.json()["detail"]
    assert len(db.sql) == 1
    db.echter_fp = FP
    db.antwort = {"kennung": KENNUNG, "vorher": None, "nachher": None, "ergebnis": "unbekannt"}
    assert _post(client, aktion="sperren", grund="Test", fingerabdruck=None).status_code == 404


@pytest.mark.parametrize("ergebnis,vorher,teil", [
    ("nicht_moeglich", "gesperrt", "gesperrt"), ("nicht_moeglich", "widerrufen", "widerrufen"),
    ("konflikt", "beantragt", "anderes aktives Gerät"), ("besitzer_inaktiv", "beantragt", "deaktiviert"),
])
def test_abgelehnte_ergebnisse_sind_konflikte_mit_klarem_text(client, db, ergebnis, vorher, teil):
    db.antwort = {"kennung": KENNUNG, "vorher": vorher, "nachher": vorher, "ergebnis": ergebnis}
    r = _post(client)
    assert r.status_code == 409 and teil in r.json()["detail"]


def test_unerwartete_antwort_der_datenbank(client, db):
    for antwort in (None, {"kennung": KENNUNG, "vorher": "x", "nachher": "y", "ergebnis": "vielleicht"}):
        db.antwort = antwort
        assert _post(client).status_code == 503


def test_datenbankfehler_werden_konkret_gemeldet(client, db):
    for fehler, status in ((asyncpg.InsufficientPrivilegeError("x"), 403), (asyncpg.PostgresConnectionError("weg"), 503),
                           (asyncpg.InvalidParameterValueError("x"), 400), (OSError("weg"), 503)):
        db.fehler = fehler
        r = _post(client, fingerabdruck=FP)
        assert r.status_code == status, fehler
        assert "weg" not in r.json()["detail"]
    db.fehler = None


def test_rate_grenze_fuer_aktionen(client):
    for _ in range(api.AKTION_GRENZE[0]):
        assert _post(client).status_code == 200
    assert _post(client).status_code == 429
    # die Liste hat ihre eigene Grenze
    assert client.get("/wamo/api/geraete/verwaltung").status_code == 200


def test_rate_grenze_zaehlt_auch_abgelehnte(client, ctx):
    ctx.tok = token(totp_vor=500)
    for _ in range(api.AKTION_GRENZE[0]):
        assert _post(client).status_code == 403
    ctx.tok = token()
    assert _post(client).status_code == 429


def test_rate_grenze_je_admin(client):
    api._drosseln("anderer-admin", "aktion", api.AKTION_GRENZE)
    for _ in range(api.AKTION_GRENZE[0]):
        assert _post(client).status_code == 200


def test_grund_steht_nie_im_log(client, db, log):
    db.antwort = {"kennung": KENNUNG, "vorher": "aktiv", "nachher": "gesperrt", "ergebnis": "ok"}
    _post(client, aktion="sperren", grund="GEHEIMER-GRUND-4711", fingerabdruck=None)
    db.fehler = asyncpg.PostgresConnectionError("weg")
    _post(client, aktion="sperren", grund="GEHEIMER-GRUND-4711", fingerabdruck=None)
    assert log and not any("GEHEIMER-GRUND-4711" in z or FP in z for z in log)


# ---------- Verbotsproben ----------
def test_nur_die_beiden_funktionen_und_eine_lesende_probe(client, db):
    _post(client)
    client.get("/wamo/api/geraete/verwaltung")
    for sql, _args in db.sql:
        klein = sql.lower()
        assert not re.search(r"\b(update|insert|delete|truncate|drop|alter|grant)\b", klein), sql
        assert "ssh_pubkey" not in klein and "service_role" not in klein
        assert "geraet_entscheiden" in klein or "geraete_verwaltung_liste" in klein or "ssh_fingerprint" in klein


def test_alle_parameter_sind_gebunden(client, db):
    _post(client, kennung=KENNUNG, grund="x'; drop table devices;--", aktion="sperren", fingerabdruck=None)
    for sql, _args in db.sql:
        assert "drop table" not in sql and KENNUNG not in sql


def test_modul_ist_im_kern_eingehaengt():
    import wamo_api

    assert "geraete_admin_api" in wamo_api.ROUTEN_MODULE


def test_zeit_in_der_vergangenheit_wird_an_die_datenbank_gereicht(client, db, ctx):
    ctx.tok = token(totp_vor=110)
    _post(client)
    zeit = db.sql[-1][1][4]
    assert timedelta(seconds=100) < datetime.now(timezone.utc) - zeit < timedelta(seconds=125)
