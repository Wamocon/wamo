"""Tests des Fernzugriffs (rd_api.py), ohne Datenbank, ohne Netz und ohne Gateway.

Geprüft wird: wer darf (Rolle, Code der Anmeldung, Profil; die Stufen und den Wettlauf prüft test_rd_anmeldung.py), welche Ziele es gibt (feste Liste, nie frei wählbar), Belegung
und Rate-Grenzen, dass das Passwort nie in Antwort oder Log steht, dass die Anweisung für Guacamole korrekt signiert,
verschlüsselt und kurzlebig ist (unabhängig nachgerechnet), und wie der Aufseher Sitzungen beendet. Das Zusammenspiel mit dem echten
Guacamole prüft infra/sokrates2/wamo-rd/zugang/pruefe_gegen_guacamole.py (Docker).

Ausführen:  uv run python -m pytest -q test_rd_api.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import jwt
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from loguru import logger

import rd_api as api
from auth_gate import Identity

ADMIN = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
ANDERER = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"
SITZUNG = "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d"
ALTE_SITZUNG = "aaaaaaaa-1111-4222-8333-444444444444"
PASSWORT = "Zq7Lm2Xv9Rt4Bn8Kc3Hd6Wp5Ys1Jf0G"
SCHLUESSEL_HEX = "00112233445566778899aabbccddeeff"
TOKEN = "AB12cd34EF56gh78IJ90kl12MN34op56QR78st90"

ZIELE_TOML = """
[[ziel]]
name = "sokrates2"
anzeige = "Sokrates-2"
adresse = "192.168.178.136"

[[ziel]]
name = "caesar"
anzeige = "Caesar"
adresse = "192.168.178.140"
port = 3390
"""


def token(*, aal: str = "aal2", totp_vor: float | None = 10.0, passwort_vor: float = 3600.0) -> str:
    jetzt = time.time()
    amr = [{"method": "password", "timestamp": int(jetzt - passwort_vor)}]
    if totp_vor is not None:
        amr.append({"method": "totp", "timestamp": int(jetzt - totp_vor)})
    return jwt.encode({"sub": ADMIN, "aal": aal, "amr": amr}, "nur-fuer-tests-nur-fuer-tests-123456", algorithm="HS256")


def entschluesseln(data: str, schluessel_hex: str = SCHLUESSEL_HEX) -> dict:
    """Unabhängige Gegenprobe zu anweisung_bauen: entschlüsseln, Signatur prüfen, JSON lesen (wie Guacamole es tut)."""
    schluessel = bytes.fromhex(schluessel_hex)
    dec = Cipher(algorithms.AES(schluessel), modes.CBC(bytes(16))).decryptor()
    klar = dec.update(base64.b64decode(data)) + dec.finalize()
    entpolstern = padding.PKCS7(128).unpadder()
    klar = entpolstern.update(klar) + entpolstern.finalize()
    signatur, roh = klar[:32], klar[32:]
    assert hmac.compare_digest(signatur, hmac.new(schluessel, roh, hashlib.sha256).digest()), "Signatur stimmt nicht"
    return json.loads(roh)


class FakeDb:
    configured = True

    def __init__(self):
        self.sql: list[tuple[str, tuple]] = []
        self.beginnen: dict = {"ergebnis": "ok", "sitzung_id": SITZUNG, "abgeloest_id": None, "belegt_von": None,
                               "belegt_seit": None, "bis_at": datetime.now(timezone.utc) + timedelta(hours=1)}
        self.offen: list[dict] = []
        self.admins: set[str] = {ADMIN}
        self.beenden_ergebnis = "ok"
        self.besitzer: str | None = ADMIN
        self.fehler: BaseException | None = None
        self.beendet: list[tuple[str, str, str | None]] = []

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    async def fetch(self, sql, *args):
        self.sql.append((sql, args))
        if self.fehler:
            raise self.fehler
        if "from wamo_test.profiles" in sql and "user_id = any" in sql:
            return [{"user_id": u} for u in self.admins]
        return self.offen

    async def fetchrow(self, sql, *args):
        self.sql.append((sql, args))
        if self.fehler:
            raise self.fehler
        return self.beginnen

    async def fetchval(self, sql, *args):
        self.sql.append((sql, args))
        if self.fehler:
            raise self.fehler
        if "rd_sitzung_beenden" in sql:
            self.beendet.append((args[0], args[1], args[2]))
            return self.beenden_ergebnis
        return self.besitzer


class FakeGateway:
    """Ersetzt den Unix-Socket zum Zugangsdienst."""

    def __init__(self):
        self.anfragen: list[dict] = []
        self.proben = {"sokrates2": True, "caesar": True}
        self.fehler: dict[str, str] = {}   # Befehl -> Fehlercode
        self.status = {"ok": True, "offen": True, "verbunden": True, "grund": None}

    async def __call__(self, anfrage: dict, zeitlimit: float = 20.0) -> dict:
        self.anfragen.append(anfrage)
        cmd = anfrage["cmd"]
        if cmd in self.fehler:
            raise api.GatewayFehler(self.fehler[cmd])
        if cmd == "proben":
            return {"ok": True, "ziele": self.proben}
        if cmd == "anmelden":
            return {"ok": True, "token": TOKEN}
        if cmd == "status":
            return self.status
        return {"ok": True, "offen": False, "grund": anfrage.get("grund")}

    def befehle(self, cmd: str) -> list[dict]:
        return [a for a in self.anfragen if a["cmd"] == cmd]


class Kontext:
    rolle = "admin"
    tok: str | None = None
    gesperrt = False


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(api, "shared_database", lambda: fake)
    return fake


@pytest.fixture
def gw(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(api, "gateway", fake)
    api._proben.clear()
    monkeypatch.setattr(api, "_proben_stand", -1e9)
    return fake


@pytest.fixture
def dateien(tmp_path, monkeypatch):
    (tmp_path / "ziele.toml").write_text(ZIELE_TOML, encoding="utf-8")
    (tmp_path / "ziele.env").write_text(
        f"WAMO_RD_PASSWORT_SOKRATES2={PASSWORT}\nWAMO_RD_PASSWORT_CAESAR=anderes-Passwort-12345\n", encoding="utf-8")
    (tmp_path / "json.key").write_text(SCHLUESSEL_HEX, encoding="ascii")
    monkeypatch.setenv("WAMO_RD_ZIELE", str(tmp_path / "ziele.toml"))
    monkeypatch.setenv("WAMO_RD_ENV", str(tmp_path / "ziele.env"))
    monkeypatch.setenv("WAMO_RD_KEY", str(tmp_path / "json.key"))
    monkeypatch.setenv("WAMO_RD_URL", "https://192.168.178.136:55446")
    monkeypatch.setenv("WAMO_RD_AKTIV", "ja")
    return tmp_path


@pytest.fixture
def ctx(monkeypatch):
    k = Kontext()
    k.tok = token()

    async def identitaet(_request):
        if k.gesperrt:
            raise HTTPException(403, "kein aktives WAMO-Profil oder zweiter Faktor fehlt")
        return Identity(user_id=ADMIN, name="Admin", rolle=k.rolle, anrede="du", anmeldung=1.0)

    monkeypatch.setattr(api, "identitaet", identitaet)
    monkeypatch.setattr(api, "bearer", lambda _request: k.tok)
    api._versuche.clear()
    return k


@pytest.fixture
def client(db, gw, dateien, ctx):
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return TestClient(app)


@pytest.fixture
def log():
    zeilen: list[str] = []
    senke = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    yield zeilen
    logger.remove(senke)


def _start(client, **body):
    return client.post("/wamo/api/rd/start", json={"ziel": "sokrates2", **body})


def beginnen_aufruf(db: FakeDb) -> tuple[str, tuple]:
    """Der letzte Aufruf von rd_sitzung_beginnen (danach folgt noch die Prüfung der Zeile nach dem Anmelden)."""
    return [(sql, args) for sql, args in db.sql if "rd_sitzung_beginnen" in sql][-1]


# ======================================================================== Anweisung
def test_anweisung_ist_korrekt_signiert_verschluesselt_und_endet_mit_der_sitzung():
    ziel = api.Ziel("sokrates2", "Sokrates-2", "192.168.178.136", 3389)
    jetzt = time.time()
    data = api.anweisung_bauen(bytes.fromhex(SCHLUESSEL_HEX), SITZUNG, ziel, PASSWORT, jetzt=jetzt)
    assert re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", data)
    assert len(base64.b64decode(data)) % 16 == 0            # AES-Blöcke
    d = entschluesseln(data)
    assert d["username"] == "rd-9a1b2c3d4e5f"               # Kurzform, kein Name einer Person
    assert abs(d["expires"] - (jetzt + api.DAUER_SECS) * 1000) < 2000          # expires = Höchstende der Sitzung (Guacamole beendet sie damit)
    assert d["expires"] <= (jetzt + 3600 + 2) * 1000                           # nie länger als die Höchstdauer
    verbindung = d["connections"][api.VERBINDUNG]
    assert verbindung["protocol"] == "rdp"
    p = verbindung["parameters"]
    assert (p["hostname"], p["port"], p["username"], p["password"]) == ("192.168.178.136", "3389", "wamo-rd", PASSWORT)
    # keine Zwischenablage, kein Laufwerk, kein Drucker, kein Ton, keine Aufzeichnung
    assert p["disable-copy"] == "true" and p["disable-paste"] == "true"
    assert p["enable-drive"] == "false" and p["enable-printing"] == "false" and p["disable-audio"] == "true"
    assert not set(api.VERBOTENE_PARAMETER) & set(p)
    assert not any(k.startswith("recording") for k in p)
    assert int(p["width"]) <= 1920 and int(p["height"]) <= 1200
    assert p["security"] == "tls"
    assert set(d) == {"username", "expires", "connections"} and list(d["connections"]) == [api.VERBINDUNG]


def test_abgelaufene_anweisung_traegt_eine_vergangene_zeit():
    ziel = api.Ziel("sokrates2", "Sokrates-2", "192.168.178.136", 3389)
    d = entschluesseln(api.anweisung_bauen(bytes.fromhex(SCHLUESSEL_HEX), SITZUNG, ziel, PASSWORT, jetzt=time.time() - 3600))
    assert d["expires"] < time.time() * 1000


def test_falscher_schluessel_besteht_die_signaturpruefung_nicht():
    ziel = api.Ziel("sokrates2", "Sokrates-2", "192.168.178.136", 3389)
    data = api.anweisung_bauen(bytes.fromhex(SCHLUESSEL_HEX), SITZUNG, ziel, PASSWORT)
    with pytest.raises((AssertionError, ValueError)):
        entschluesseln(data, "ffeeddccbbaa99887766554433221100")


def test_schluessel_muss_128_bit_haben():
    ziel = api.Ziel("sokrates2", "Sokrates-2", "192.168.178.136", 3389)
    for kurz in (b"", b"x" * 15, b"x" * 32):
        with pytest.raises(ValueError):
            api.anweisung_bauen(kurz, SITZUNG, ziel, PASSWORT)


def test_seiten_url_enthaelt_nur_das_sitzungstoken():
    url = api.seiten_url("https://192.168.178.136:55446", TOKEN)
    assert url.startswith(f"https://192.168.178.136:55446/?token={TOKEN}#/client/")
    kennung = url.split("/client/")[1]
    assert re.fullmatch(r"[A-Za-z0-9]+", kennung)             # kein Schrägstrich, der die Route zerlegt
    assert base64.b64decode(kennung) == b"fernsitzung\0c\0json"
    assert PASSWORT not in url and "192.168.178.140" not in url


# ======================================================================== Dateien und Einstellungen
def test_ziele_laden_nimmt_nur_gueltige_eintraege(tmp_path):
    p = tmp_path / "z.toml"
    p.write_text(ZIELE_TOML + """
[[ziel]]
name = "Gross"
adresse = "192.168.178.5"
[[ziel]]
name = "lokal"
adresse = "127.0.0.1"
[[ziel]]
name = "host"
adresse = "ziel.example.org"
[[ziel]]
name = "sokrates2"
adresse = "192.168.178.99"
[[ziel]]
name = "zweitname"
adresse = "192.168.178.136"
[[ziel]]
name = "port0"
adresse = "192.168.178.7"
port = 0
[[ziel]]
name = "mc"
adresse = "224.0.0.1"
[[ziel]]
name = "ohnetext"
adresse = "192.168.178.8"
anzeige = "   "
""", encoding="utf-8")
    ziele = api.ziele_laden(p)
    assert set(ziele) == {"sokrates2", "caesar", "ohnetext"}
    assert ziele["sokrates2"].adresse == "192.168.178.136"   # der erste Eintrag gilt, ein doppelter fällt weg
    assert ziele["caesar"].port == 3390
    assert ziele["ohnetext"].anzeige == "ohnetext"


def test_ziele_laden_ohne_datei_oder_mit_muell(tmp_path):
    assert api.ziele_laden(tmp_path / "gibt-es-nicht.toml") == {}
    kaputt = tmp_path / "k.toml"
    kaputt.write_text("das ist [ kein toml", encoding="utf-8")
    assert api.ziele_laden(kaputt) == {}


def test_passwort_fuer(tmp_path):
    p = tmp_path / "ziele.env"
    p.write_text(f"# Kommentar\nWAMO_RD_PASSWORT_SOKRATES2={PASSWORT}\nWAMO_RD_PASSWORT_KURZ=abc\n"
                 'WAMO_RD_PASSWORT_ANF="mit-anfuehrungszeichen"\nANDERE=x\n', encoding="utf-8")
    assert api.passwort_fuer("sokrates2", p) == PASSWORT
    assert api.passwort_fuer("kurz", p) is None              # zu kurz
    assert api.passwort_fuer("anf", p) is None               # Anführungszeichen sind in der Datei nicht vorgesehen
    assert api.passwort_fuer("fehlt", p) is None
    assert api.passwort_fuer("sokrates2", tmp_path / "nein.env") is None


def test_schluessel_lesen(tmp_path):
    p = tmp_path / "k"
    p.write_text(SCHLUESSEL_HEX, encoding="ascii")
    assert api.schluessel_lesen(p) == bytes.fromhex(SCHLUESSEL_HEX)
    p.write_text(SCHLUESSEL_HEX + "\n", encoding="ascii")
    assert api.schluessel_lesen(p) == bytes.fromhex(SCHLUESSEL_HEX)
    for schlecht in ("", "zz" * 16, SCHLUESSEL_HEX[:-2], SCHLUESSEL_HEX.upper(), SCHLUESSEL_HEX + "00"):
        p.write_text(schlecht, encoding="ascii")
        assert api.schluessel_lesen(p) is None
    assert api.schluessel_lesen(tmp_path / "nein") is None


def test_basis_url_nur_https_oder_loopback(monkeypatch):
    for gut in ("https://192.168.178.136:55446", "https://wamo.example/", "http://127.0.0.1:18080"):
        monkeypatch.setenv("WAMO_RD_URL", gut)
        assert api.basis_url() == gut.rstrip("/")
    for schlecht in ("http://192.168.178.136:55446", "javascript:alert(1)", "https://a b", "ftp://x"):
        monkeypatch.setenv("WAMO_RD_URL", schlecht)
        with pytest.raises(HTTPException):
            api.basis_url()


# ======================================================================== Zugang
def test_mitglied_darf_nichts(client, ctx, db, gw):
    ctx.rolle = "mitglied"
    assert client.get("/wamo/api/rd/ziele").status_code == 403
    assert _start(client).status_code == 403
    assert client.post("/wamo/api/rd/ende", json={"sitzung": SITZUNG}).status_code == 403
    assert db.sql == [] and gw.anfragen == []


def test_gesperrtes_profil_kommt_nicht_durch(client, ctx, db, gw):
    ctx.gesperrt = True
    assert client.get("/wamo/api/rd/ziele").status_code == 403
    assert _start(client).status_code == 403
    assert db.sql == [] and gw.anfragen == []


def test_nicht_eingeschaltet(client, monkeypatch, db, gw):
    monkeypatch.setenv("WAMO_RD_AKTIV", "nein")
    r = client.get("/wamo/api/rd/ziele")
    assert r.status_code == 200 and r.json()["aktiv"] is False and r.json()["ziele"] == []
    assert _start(client).status_code == 503
    assert client.post("/wamo/api/rd/ende", json={"sitzung": SITZUNG}).status_code == 503
    assert db.sql == [] and gw.anfragen == []


@pytest.mark.parametrize("totp_vor", [None, -60.0])
def test_zweiter_faktor_fehlend_oder_aus_der_zukunft(client, ctx, db, gw, totp_vor):
    """Seit KP-2 genügt der Code der Anmeldung (auch ein alter, test_rd_anmeldung.py); ohne totp-Eintrag oder aus der Zukunft nie."""
    ctx.tok = token(totp_vor=totp_vor)
    r = _start(client)
    assert r.status_code == 403 and r.json()["code"] == "zweiter_faktor_zu_alt"
    assert db.sql == [] and gw.befehle("anmelden") == []


def test_zweiter_faktor_aal1_wird_abgelehnt(client, ctx, db, gw):
    ctx.tok = token(aal="aal1")
    assert _start(client).status_code == 403
    assert db.sql == [] and gw.befehle("anmelden") == []


def test_grenze_fuer_den_zweiten_faktor_110_sekunden_geht(client, ctx):
    ctx.tok = token(totp_vor=110.0)
    assert _start(client).status_code == 200


@pytest.mark.parametrize("ziel", ["sokrates3", "", "../etc", "SOKRATES2", "sokrates2 ", 5, None, ["sokrates2"], {"a": 1}, "x" * 40,
                                  "192.168.178.136", "wamo-rd"])
def test_ziel_nur_aus_der_festen_liste(client, db, gw, ziel):
    r = client.post("/wamo/api/rd/start", json={"ziel": ziel})
    assert r.status_code == 404
    assert db.sql == [] and gw.befehle("anmelden") == []


def test_host_port_benutzer_und_protokoll_aus_der_anfrage_zaehlen_nicht(client, db, gw):
    r = _start(client, host="10.0.0.1", hostname="10.0.0.1", port=22, protocol="ssh", username="root", password="x",
               parameters={"recording-path": "/tmp"}, adresse="10.0.0.1")
    assert r.status_code == 200
    d = entschluesseln(gw.befehle("anmelden")[0]["data"])
    p = d["connections"][api.VERBINDUNG]["parameters"]
    assert d["connections"][api.VERBINDUNG]["protocol"] == "rdp"
    assert (p["hostname"], p["port"], p["username"], p["password"]) == ("192.168.178.136", "3389", "wamo-rd", PASSWORT)
    assert "recording-path" not in p


def test_start_ok_gibt_nur_die_seite_mit_dem_sitzungstoken(client, db, gw, log):
    r = _start(client)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["sitzung"] == SITZUNG and j["ziel"] == "sokrates2" and j["anzeige"] == "Sokrates-2"
    assert j["url"] == api.seiten_url("https://192.168.178.136:55446", TOKEN)
    assert j["max_minuten"] == 60 and j["leerlauf_minuten"] == 10 and j["endet"]
    assert set(j) == {"ok", "sitzung", "ziel", "anzeige", "url", "endet", "max_minuten", "leerlauf_minuten"}
    # das Passwort, der Schlüssel, die Zielhost-Adresse und die Anweisung stehen nirgends in der Antwort oder im Log
    text = r.text + "".join(log)
    for geheim in (PASSWORT, SCHLUESSEL_HEX, "hostname", "3389", gw.befehle("anmelden")[0]["data"]):
        assert geheim not in text
    assert TOKEN not in "".join(log)
    # an die Datenbank gingen Profil, Ziel, Dauer, die Zeit des Codes und die Grenzen; der Akteur kommt aus der Identität
    sql, args = beginnen_aufruf(db)
    assert args[0] == ADMIN and args[1] == "sokrates2" and args[2] == 3600 and args[4] == 3 and args[5] == 6
    assert abs(args[3].timestamp() - (time.time() - 10)) < 5
    # an das Gateway ging die Anweisung mit der Höchstdauer
    a = gw.befehle("anmelden")[0]
    assert a["sitzung"] == SITZUNG and a["max_secs"] == 3600
    assert entschluesseln(a["data"])["connections"][api.VERBINDUNG]["parameters"]["password"] == PASSWORT


def test_akteur_nie_aus_der_anfrage(client, db):
    _start(client, profil=ANDERER, user_id=ANDERER, akteur=ANDERER, admin=ANDERER)
    assert beginnen_aufruf(db)[1][0] == ADMIN
    assert ANDERER not in str(db.sql)


def test_anderes_ziel_hat_eigene_zugangsdaten(client, gw):
    r = client.post("/wamo/api/rd/start", json={"ziel": "caesar"})
    assert r.status_code == 200
    p = entschluesseln(gw.befehle("anmelden")[0]["data"])["connections"][api.VERBINDUNG]["parameters"]
    assert p["hostname"] == "192.168.178.140" and p["port"] == "3390" and p["password"] == "anderes-Passwort-12345"


def test_belegtes_ziel_gibt_409_ohne_gateway(client, db, gw):
    db.beginnen = {"ergebnis": "belegt", "sitzung_id": None, "abgeloest_id": None, "belegt_von": "Bernd",
                   "belegt_seit": datetime(2026, 10, 31, 12, 30, tzinfo=timezone.utc), "bis_at": None}
    r = _start(client)
    assert r.status_code == 409
    assert "Sokrates-2 ist belegt von Bernd" in r.json()["detail"]
    assert gw.befehle("anmelden") == [] and gw.befehle("beenden") == []


@pytest.mark.parametrize("ergebnis,status", [("rate", 429), ("voll", 409)])
def test_grenzen_der_datenbank(client, db, gw, ergebnis, status):
    db.beginnen = {"ergebnis": ergebnis, "sitzung_id": None, "abgeloest_id": None, "belegt_von": None, "belegt_seit": None,
                   "bis_at": None}
    assert _start(client).status_code == status
    assert gw.befehle("anmelden") == []


def test_unerwartetes_ergebnis_der_datenbank(client, db, gw):
    db.beginnen = {"ergebnis": "wasauchimmer", "sitzung_id": None, "abgeloest_id": None, "belegt_von": None,
                   "belegt_seit": None, "bis_at": None}
    assert _start(client).status_code == 503
    assert gw.befehle("anmelden") == []


def test_eigene_laufende_sitzung_wird_abgeloest(client, db, gw):
    db.beginnen = {**db.beginnen, "abgeloest_id": ALTE_SITZUNG}
    assert _start(client).status_code == 200
    b = gw.befehle("beenden")
    assert b == [{"cmd": "beenden", "sitzung": ALTE_SITZUNG, "grund": "abgeloest"}]
    # erst die alte beenden, dann die neue anmelden
    assert [a["cmd"] for a in gw.anfragen].index("beenden") < [a["cmd"] for a in gw.anfragen].index("anmelden")


def test_rate_grenze_der_anfragen(client, db, gw):
    codes = [_start(client).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_rate_grenze_zaehlt_auch_abgelehnte(client, ctx, db):
    ctx.tok = token(totp_vor=None)
    codes = [_start(client).status_code for _ in range(11)]
    assert codes[:10] == [403] * 10 and codes[10] == 429


def test_ziel_ohne_passwort_ist_nicht_eingerichtet(client, dateien, db, gw):
    (dateien / "ziele.env").write_text("WAMO_RD_PASSWORT_CAESAR=anderes-Passwort-12345\n", encoding="utf-8")
    r = _start(client)
    assert r.status_code == 503 and db.sql == [] and gw.befehle("anmelden") == []
    assert PASSWORT not in r.text


def test_fehlender_schluessel(client, dateien, db, gw):
    (dateien / "json.key").unlink()
    assert _start(client).status_code == 503
    assert db.sql == [] and gw.befehle("anmelden") == []


def test_ziel_nicht_erreichbar(client, gw, db):
    gw.proben["sokrates2"] = False
    r = _start(client)
    assert r.status_code == 409 and "nicht erreichbar" in r.json()["detail"]
    assert db.sql == []


def test_gateway_aus_dann_nicht_erreichbar(client, gw, db):
    gw.fehler["proben"] = "nicht_erreichbar"
    assert _start(client).status_code == 409
    assert db.sql == []


@pytest.mark.parametrize("code", ["nicht_erreichbar", "abgelehnt", "gateway_fehler", "voll"])
def test_gateway_lehnt_ab_sitzung_wird_freigegeben_antwort_ohne_details(client, db, gw, log, code):
    gw.fehler["anmelden"] = code
    r = _start(client)
    assert r.status_code in (502, 503)
    assert r.json()["detail"] == "Das Gateway hat die Verbindung nicht angenommen. Bitte gleich noch einmal versuchen."
    assert db.beendet == [(SITZUNG, "fehler", None)]
    assert PASSWORT not in r.text + "".join(log)


def test_gateway_antwortet_mit_unbrauchbarem_token(client, db, gw, monkeypatch):
    async def schlecht(anfrage, zeitlimit=20.0):
        return {"ok": True, "token": "../../x"} if anfrage["cmd"] == "anmelden" else {"ok": True, "ziele": {"sokrates2": True}}

    monkeypatch.setattr(api, "gateway", schlecht)
    r = _start(client)
    assert r.status_code in (502, 503) and "token" not in r.text.lower()
    assert db.beendet == [(SITZUNG, "fehler", None)]


@pytest.mark.parametrize("sqlstate,status", [("42501", 403), ("22023", 400), ("23514", 403), ("99999", 503)])
def test_fehler_der_datenbank(client, db, gw, sqlstate, status):
    class Fehler(asyncpg.PostgresError):
        pass

    e = Fehler("Meldung mit Geheimnissen: " + PASSWORT)
    e.sqlstate = sqlstate
    db.fehler = e
    r = _start(client)
    assert r.status_code == status
    assert PASSWORT not in r.text and "Geheimnis" not in r.text
    assert gw.befehle("anmelden") == []


# ======================================================================== Liste
def test_liste_zeigt_zustand_ohne_zugangsdaten(client, db, gw):
    db.offen = [{"id": ALTE_SITZUNG, "profil_id": ANDERER, "ziel": "caesar", "display_name": "Bernd",
                 "start_at": datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
                 "bis_at": datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc)}]
    r = client.get("/wamo/api/rd/ziele")
    assert r.status_code == 200
    j = r.json()
    assert j["aktiv"] is True and j["meine"] is None and j["max_minuten"] == 60
    z = {x["name"]: x for x in j["ziele"]}
    assert z["sokrates2"]["status"] == "frei" and z["sokrates2"]["anzeige"] == "Sokrates-2"
    assert z["caesar"]["status"] == "belegt" and z["caesar"]["belegt_von"] == "Bernd" and z["caesar"]["eigene"] is False
    for geheim in (PASSWORT, "anderes-Passwort", "192.168.178", "3389", SCHLUESSEL_HEX, "wamo-rd\""):
        assert geheim not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_liste_nicht_erreichbar_und_eigene_sitzung(client, db, gw):
    gw.proben["sokrates2"] = False
    db.offen = [{"id": SITZUNG, "profil_id": ADMIN, "ziel": "caesar", "display_name": "Admin",
                 "start_at": datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
                 "bis_at": datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc)}]
    j = client.get("/wamo/api/rd/ziele").json()
    z = {x["name"]: x for x in j["ziele"]}
    assert z["sokrates2"]["status"] == "nicht_erreichbar"
    assert z["caesar"]["status"] == "belegt" and z["caesar"]["eigene"] is True and z["caesar"]["belegt_von"] is None
    assert j["meine"]["sitzung"] == SITZUNG and j["meine"]["ziel"] == "caesar" and j["meine"]["endet"].startswith("2026-10-31T13:00")


def test_liste_ohne_passwort_gilt_als_nicht_erreichbar(client, dateien):
    (dateien / "ziele.env").write_text("WAMO_RD_PASSWORT_CAESAR=anderes-Passwort-12345\n", encoding="utf-8")
    z = {x["name"]: x for x in client.get("/wamo/api/rd/ziele").json()["ziele"]}
    assert z["sokrates2"]["status"] == "nicht_erreichbar" and z["caesar"]["status"] == "frei"


def test_liste_bei_gateway_ausfall(client, gw):
    gw.fehler["proben"] = "nicht_erreichbar"
    z = client.get("/wamo/api/rd/ziele").json()["ziele"]
    assert [x["status"] for x in z] == ["nicht_erreichbar", "nicht_erreichbar"]


def test_liste_bei_datenbankfehler(client, db):
    db.fehler = asyncpg.PostgresError("x")
    assert client.get("/wamo/api/rd/ziele").status_code == 503


def test_liste_rate_grenze(client):
    codes = [client.get("/wamo/api/rd/ziele").status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


# ======================================================================== Ende
def test_ende_der_eigenen_sitzung(client, db, gw):
    r = client.post("/wamo/api/rd/ende", json={"sitzung": SITZUNG})
    assert r.status_code == 200 and r.json() == {"ok": True, "ergebnis": "ok", "gateway_bestaetigt": True}
    assert gw.befehle("beenden") == [{"cmd": "beenden", "sitzung": SITZUNG, "grund": "beendet"}]
    assert db.beendet == [(SITZUNG, "beendet", ADMIN)]   # nur mit Profil: fremde Sitzungen beendet die Datenbank nicht


def test_ende_einer_fremden_sitzung_wird_abgewiesen(client, db, gw):
    db.besitzer = ANDERER
    r = client.post("/wamo/api/rd/ende", json={"sitzung": SITZUNG})
    assert r.status_code == 403
    assert gw.befehle("beenden") == [] and db.beendet == []


def test_ende_ohne_offene_sitzung(client, db, gw):
    db.besitzer = None
    db.beenden_ergebnis = "schon"
    r = client.post("/wamo/api/rd/ende", json={"sitzung": SITZUNG})
    assert r.status_code == 200 and r.json()["ergebnis"] == "schon"
    assert gw.befehle("beenden") == []
    db.beenden_ergebnis = "unbekannt"
    assert client.post("/wamo/api/rd/ende", json={"sitzung": SITZUNG}).status_code == 404


def test_ende_wenn_das_gateway_nicht_antwortet(client, db, gw):
    gw.fehler["beenden"] = "nicht_erreichbar"
    r = client.post("/wamo/api/rd/ende", json={"sitzung": SITZUNG})
    assert r.status_code == 200 and r.json()["gateway_bestaetigt"] is False
    assert db.beendet == [(SITZUNG, "beendet", ADMIN)]


@pytest.mark.parametrize("sitzung", [None, "", "abc", 5, "g" * 36, SITZUNG + "0", ["x"]])
def test_ende_mit_ungueltiger_sitzung(client, db, sitzung):
    assert client.post("/wamo/api/rd/ende", json={"sitzung": sitzung}).status_code == 400
    assert db.sql == []


def test_body_nur_json_objekt_und_klein(client):
    assert client.post("/wamo/api/rd/start", content=b"kein json").status_code == 400
    assert client.post("/wamo/api/rd/start", content=b"[1,2]").status_code == 400
    assert client.post("/wamo/api/rd/start", content=b'{"ziel":"' + b"x" * 3000 + b'"}').status_code == 413


# ======================================================================== Aufseher
def offen(profil=ADMIN, ziel="sokrates2", alter=600, rest=3000, sitzung=SITZUNG):
    jetzt = datetime.now(timezone.utc)
    return {"id": sitzung, "profil_id": profil, "ziel": ziel, "start_at": jetzt - timedelta(seconds=alter),
            "bis_at": jetzt + timedelta(seconds=rest)}


async def test_aufseher_ohne_offene_sitzungen_tut_nichts(db, gw):
    assert await api.aufseher_runde(db) == 0 and gw.anfragen == [] and db.beendet == []


async def test_aufseher_laesst_gesunde_sitzungen_in_ruhe(db, gw):
    db.offen = [offen()]
    assert await api.aufseher_runde(db) == 0
    assert db.beendet == [] and gw.befehle("beenden") == []


async def test_aufseher_beendet_nach_dem_hoechstende(db, gw):
    db.offen = [offen(alter=3700, rest=-60)]
    assert await api.aufseher_runde(db) == 1
    assert db.beendet == [(SITZUNG, "zeitlimit", None)]
    assert gw.befehle("beenden") == [{"cmd": "beenden", "sitzung": SITZUNG, "grund": "zeitlimit"}]


async def test_aufseher_beendet_wenn_das_profil_nicht_mehr_admin_ist(db, gw):
    db.offen = [offen()]
    db.admins = set()
    assert await api.aufseher_runde(db) == 1
    assert db.beendet == [(SITZUNG, "profil_inaktiv", None)]
    assert gw.befehle("beenden")[0]["grund"] == "profil_inaktiv"


@pytest.mark.parametrize("grund", ["getrennt", "nicht_geoeffnet", "zeitlimit", "beendet"])
async def test_aufseher_uebernimmt_das_ende_vom_gateway(db, gw, grund):
    db.offen = [offen()]
    gw.status = {"ok": True, "offen": False, "verbunden": False, "grund": grund}
    assert await api.aufseher_runde(db) == 1
    assert db.beendet == [(SITZUNG, grund, None)]


async def test_aufseher_unbekannte_sitzung_am_gateway_wird_beendet(db, gw):
    db.offen = [offen()]
    gw.status = {"ok": True, "offen": False, "verbunden": False, "grund": None}
    assert await api.aufseher_runde(db) == 1
    assert db.beendet == [(SITZUNG, "fehler", None)]


async def test_aufseher_fasst_frische_sitzungen_nicht_an(db, gw):
    db.offen = [offen(alter=10)]
    gw.status = {"ok": True, "offen": False, "verbunden": False, "grund": None}
    assert await api.aufseher_runde(db) == 0
    assert db.beendet == [] and gw.befehle("status") == []


async def test_aufseher_schliesst_nichts_wenn_das_gateway_schweigt(db, gw):
    db.offen = [offen()]
    gw.fehler["status"] = "nicht_erreichbar"
    assert await api.aufseher_runde(db) == 0
    assert db.beendet == []


async def test_aufseher_ohne_gateway_gilt_das_hoechstende_mit_nachfrist(db, gw):
    gw.fehler["status"] = "nicht_erreichbar"
    gw.fehler["beenden"] = "nicht_erreichbar"
    db.offen = [offen(alter=3650, rest=-50)]      # über das Höchstende, aber das Gateway schweigt
    assert await api.aufseher_runde(db) == 1      # Höchstende plus 5 Sekunden reicht für zeitlimit auch ohne Gateway
    assert db.beendet == [(SITZUNG, "zeitlimit", None)]


async def test_aufseher_mehrere_sitzungen_unabhaengig(db, gw):
    db.offen = [offen(), offen(ziel="caesar", sitzung=ALTE_SITZUNG, alter=3700, rest=-100)]
    assert await api.aufseher_runde(db) == 1
    assert db.beendet == [(ALTE_SITZUNG, "zeitlimit", None)]


def test_modul_ist_registriert_und_hat_den_aufseher():
    import wamo_api

    assert "rd_api" in wamo_api.ROUTEN_MODULE
    assert api.hintergrund == [api.aufseher]


def test_kein_geheimnis_im_modul_selbst():
    quelltext = Path(api.__file__).read_text(encoding="utf-8")
    assert "WAMO_RD_PASSWORT_" in quelltext                         # der Name der Variablen, nie ein Wert
    assert not re.search(r"password[\"']?\s*[:=]\s*[\"'][^\"']{8,}[\"']", quelltext)
