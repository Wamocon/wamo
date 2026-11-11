"""Tests von KP-2: Fernzugriff ohne frischen Code je Start (rd_api.py, Migration 0019).

Entscheidung vom 11.11.2026: Der Code beim Anmelden genügt. Geprüft wird hier, was der Kern daran ändert und was bleibt:

  * Stufe aus (Standard): Jedes Token mit aal2 und einem totp-Eintrag genügt, auch wenn der Code Stunden oder Tage alt ist.
    Ohne totp, mit aal1 oder mit einer Zeit aus der Zukunft bleibt es bei 403 und dem Code zweiter_faktor_zu_alt.
  * Stufenschalter WAMO_RD_ANMELDUNG_MAX_MIN: ist die Anmeldung älter, verlangt der Kern wieder einen frischen Code (Befund M2:
    nur leer oder 0 heißt aus, jeder ungültige Wert gilt als strengste Stufe und schreibt eine Fehlerzeile).
  * Wettlauf (Befund M3): Ohne "ein Code, ein Start" darf ein zweiter Start den ersten ablösen, bevor dieser am Gateway
    angemeldet hat. Es bleibt trotzdem genau ein Zuschauer: ein Lock je Profil im Kern und, für zwei Kerne (Slots a und b),
    die Prüfung der Datenbankzeile nach dem Anmelden.

Ohne Datenbank, ohne Netz, ohne Gateway. Ausführen:  uv run python -m pytest -q test_rd_anmeldung.py   (in voice-core/)
"""

from __future__ import annotations

import asyncio
import gc
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

import rd_api as api
from auth_gate import Identity
from test_rd_api import ADMIN, SCHLUESSEL_HEX, TOKEN, ZIELE_TOML, FakeDb, FakeGateway, token

STUFE = "WAMO_RD_ANMELDUNG_MAX_MIN"
ERSTE = "00000000-0000-4000-8000-000000000001"
ZWEITE = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def rd(tmp_path, monkeypatch):
    """Kern mit gefälschter Datenbank, gefälschtem Gateway und fester Zielliste (wie test_rd_api.py, in einem Stück)."""
    (tmp_path / "ziele.toml").write_text(ZIELE_TOML, encoding="utf-8")
    (tmp_path / "ziele.env").write_text("WAMO_RD_PASSWORT_SOKRATES2=Zq7Lm2Xv9Rt4Bn8Kc3Hd6Wp5Ys1Jf0G\n", encoding="utf-8")
    (tmp_path / "json.key").write_text(SCHLUESSEL_HEX, encoding="ascii")
    for name, wert in (("WAMO_RD_ZIELE", "ziele.toml"), ("WAMO_RD_ENV", "ziele.env"), ("WAMO_RD_KEY", "json.key")):
        monkeypatch.setenv(name, str(tmp_path / wert))
    monkeypatch.setenv("WAMO_RD_URL", "https://192.168.178.136:55446")
    monkeypatch.setenv("WAMO_RD_AKTIV", "ja")
    monkeypatch.delenv(STUFE, raising=False)
    db, gw, kontext = FakeDb(), FakeGateway(), SimpleNamespace(tok=token())

    async def identitaet(_request):
        return Identity(user_id=ADMIN, name="Admin", rolle="admin", anrede="du", anmeldung=1.0)

    monkeypatch.setattr(api, "shared_database", lambda: db)
    monkeypatch.setattr(api, "gateway", gw)
    monkeypatch.setattr(api, "identitaet", identitaet)
    monkeypatch.setattr(api, "bearer", lambda _request: kontext.tok)
    monkeypatch.setattr(api, "_proben_stand", -1e9)
    api._proben.clear()
    api._versuche.clear()
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return SimpleNamespace(db=db, gw=gw, ctx=kontext, app=app, client=TestClient(app))


@pytest.fixture
def log():
    zeilen: list[str] = []
    senke = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    yield zeilen
    logger.remove(senke)


def _start(rd, **body):
    return rd.client.post("/wamo/api/rd/start", json={"ziel": "sokrates2", **body})


def _zeit_an_der_datenbank(rd) -> datetime:
    return [args for sql, args in rd.db.sql if "rd_sitzung_beginnen" in sql][-1][3]


# ======================================================================== Stufe aus: der Code der Anmeldung genügt
@pytest.mark.parametrize("alter", [121.0, 600.0, 3600.0, 3 * 86400.0, 167 * 3600.0])
def test_der_code_der_anmeldung_genuegt_auch_wenn_er_alt_ist(rd, alter):
    rd.ctx.tok = token(totp_vor=alter)
    r = _start(rd)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(rd.gw.befehle("anmelden")) == 1
    # an die Datenbank geht die Zeit dieses Codes, nicht die Zeit des Starts
    assert abs(_zeit_an_der_datenbank(rd).timestamp() - (time.time() - alter)) < 5


@pytest.mark.parametrize("tok_args", [{"totp_vor": None}, {"aal": "aal1"}, {"totp_vor": -60.0}])
def test_ohne_code_der_anmeldung_bleibt_es_bei_403(rd, tok_args):
    """Ohne totp-Eintrag, mit aal1 und mit einer Zeit aus der Zukunft gibt es keinen Start, auch nicht in der Stufe aus."""
    rd.ctx.tok = token(**tok_args)
    r = _start(rd)
    assert r.status_code == 403 and r.json()["code"] == "zweiter_faktor_zu_alt"
    assert rd.db.sql == [] and rd.gw.befehle("anmelden") == []


def test_zweiter_start_nach_derselben_anmeldung_geht(rd):
    """Der Code gilt nicht mehr für genau einen Start: dieselbe Anmeldung startet zweimal, die Datenbank bekommt beide Male dieselbe Zeit."""
    rd.ctx.tok = token(totp_vor=7200.0)
    erste, zweite = _start(rd), _start(rd)
    assert (erste.status_code, zweite.status_code) == (200, 200)
    zeiten = [args[3] for sql, args in rd.db.sql if "rd_sitzung_beginnen" in sql]
    assert len(zeiten) == 2 and zeiten[0] == zeiten[1]


def test_die_antwort_bei_zu_alt_nennt_den_code_der_oberflaeche(rd):
    rd.ctx.tok = token(totp_vor=None)
    r = _start(rd)
    assert r.json()["code"] == api.CODE_AAL2 == "zweiter_faktor_zu_alt"  # die Oberfläche fragt nur danach den Code ab
    assert "Authenticator-App" in r.json()["detail"] and r.headers["cache-control"] == "no-store"


# ======================================================================== Stufenschalter
@pytest.mark.parametrize("wert", ["60", " 60 "])
def test_stufe_60_minuten_grenze(rd, monkeypatch, wert):
    monkeypatch.setenv(STUFE, wert)
    rd.ctx.tok = token(totp_vor=59 * 60 + 50)   # zehn Sekunden Luft: token() schneidet die Zeit auf ganze Sekunden, der Lauf braucht etwas
    assert _start(rd).status_code == 200
    rd.ctx.tok = token(totp_vor=60 * 60 + 1)    # nie früher als eine Sekunde zu alt: das Token ist nie jünger als angegeben
    r = _start(rd)
    assert r.status_code == 403 and r.json()["code"] == "zweiter_faktor_zu_alt"
    assert "älter als 1 Stunde" in r.json()["detail"]
    assert len(rd.gw.befehle("anmelden")) == 1  # der abgelehnte Start erreichte weder Datenbank noch Gateway


def test_stufe_mit_frischem_code_geht_wieder(rd, monkeypatch):
    monkeypatch.setenv(STUFE, "60")
    rd.ctx.tok = token(totp_vor=100.0, passwort_vor=7200.0)  # Anmeldung vor zwei Stunden, Code eben erst eingegeben
    assert _start(rd).status_code == 200


def test_stufe_lehnt_eine_zeit_aus_der_zukunft_ab(rd, monkeypatch):
    monkeypatch.setenv(STUFE, "60")
    rd.ctx.tok = token(totp_vor=-60.0)
    assert _start(rd).status_code == 403 and rd.db.sql == []


@pytest.mark.parametrize("minuten,satz", [("1440", "älter als 24 Stunden"), ("120", "älter als 2 Stunden"),
                                          ("90", "älter als 90 Minuten"), ("1", "älter als 1 Minute")])
def test_stufe_nennt_die_dauer_im_satz(rd, monkeypatch, minuten, satz):
    monkeypatch.setenv(STUFE, minuten)
    rd.ctx.tok = token(totp_vor=40 * 86400.0)
    assert satz in _start(rd).json()["detail"]


@pytest.mark.parametrize("wert", [None, "", "   ", "0"])
def test_stufe_aus_nur_bei_leer_oder_null(rd, monkeypatch, log, wert):
    if wert is not None:
        monkeypatch.setenv(STUFE, wert)
    rd.ctx.tok = token(totp_vor=5 * 86400.0)
    assert _start(rd).status_code == 200
    assert not [z for z in log if STUFE in z]  # aus ist kein Fehler: keine Zeile im Log


@pytest.mark.parametrize("wert", ["abc", "-5", "-0", "nan", "NaN", "inf", "1e3", "60.5", "60,5", "43201", "99999999999999999999",
                                  "00", "060", "0x10", "+60", "1_0", "٣٠", "60 min", "aus", "nein", "ja"])
def test_ungueltiger_wert_gilt_als_strengste_stufe(rd, monkeypatch, log, wert):
    """Befund M2 (fail-closed): Ein Tippfehler macht den Fernzugriff nie lockerer. Ein Code von zehn Sekunden geht wie vor KP-2,
    ein Code von zehn Minuten nicht, und das Journal bekommt eine Fehlerzeile."""
    monkeypatch.setenv(STUFE, wert)
    rd.ctx.tok = token(totp_vor=600.0)
    r = _start(rd)
    assert r.status_code == 403 and r.json()["code"] == "zweiter_faktor_zu_alt"
    assert rd.db.sql == [] and rd.gw.befehle("anmelden") == []
    rd.ctx.tok = token(totp_vor=10.0)
    assert _start(rd).status_code == 200
    zeilen = [z for z in log if STUFE in z and "ERROR" in z]
    assert zeilen, "keine Fehlerzeile im Log"
    assert "strengste Stufe" in zeilen[0]


def test_ungueltiger_wert_wird_auch_bei_frischem_code_gemeldet(rd, monkeypatch, log):
    """Auch wenn der Start durchgeht (frischer Code), steht der Fehler der Einstellung im Journal: Wer nur mit frischen Codes startet,
    soll den Tippfehler trotzdem finden."""
    monkeypatch.setenv(STUFE, "sechzig")
    rd.ctx.tok = token(totp_vor=10.0)
    assert _start(rd).status_code == 200
    assert [z for z in log if STUFE in z and "ERROR" in z]


def test_stufe_obergrenze_43200_minuten_ist_gueltig(rd, monkeypatch, log):
    monkeypatch.setenv(STUFE, "43200")
    rd.ctx.tok = token(totp_vor=43199 * 60.0)
    assert _start(rd).status_code == 200
    rd.ctx.tok = token(totp_vor=43201 * 60.0)
    assert _start(rd).status_code == 403
    assert not [z for z in log if "ERROR" in z and STUFE in z]


def test_lange_ungueltige_werte_stehen_nur_gekuerzt_im_log(rd, monkeypatch, log):
    monkeypatch.setenv(STUFE, "x" * 500)
    rd.ctx.tok = token(totp_vor=600.0)
    _start(rd)
    zeilen = [z for z in log if STUFE in z]
    assert zeilen and "x" * 40 not in zeilen[0]


def test_anmeldung_genuegt_als_einzelne_funktion():
    jetzt = 1_000_000.0
    assert api.anmeldung_genuegt(None, jetzt) is False
    assert api.anmeldung_genuegt(jetzt - 30 * 86400, jetzt) is True
    assert api.anmeldung_genuegt(jetzt + 6, jetzt) is False          # Zukunft (mehr als 5 Sekunden)
    assert api.anmeldung_genuegt(jetzt + 4, jetzt) is True
    assert api.anmeldung_genuegt(float("nan"), jetzt) is False
    assert api.anmeldung_genuegt(float("inf"), jetzt) is False


def test_stufe_max_secs_liest_die_variable_bei_jedem_aufruf(monkeypatch):
    monkeypatch.delenv(STUFE, raising=False)
    assert api.anmeldung_max_secs() is None
    monkeypatch.setenv(STUFE, "30")
    assert api.anmeldung_max_secs() == 1800.0
    monkeypatch.setenv(STUFE, "ups")
    assert api.anmeldung_max_secs() == 0.0
    monkeypatch.setenv(STUFE, "0")
    assert api.anmeldung_max_secs() is None


def test_datenbankfehler_23514_bleibt_eine_codeabfrage(rd):
    """Ein alter Stand der Datenbank (120 Sekunden, ein Code ein Start) lehnt mit 23514 ab: Der Dialog fragt dann den Code ab."""
    class Fehler(asyncpg.PostgresError):
        pass

    e = Fehler("zu alt")
    e.sqlstate = "23514"
    rd.db.fehler = e
    r = _start(rd)
    assert r.status_code == 403 and r.json()["code"] == "zweiter_faktor_zu_alt"
    assert rd.gw.befehle("anmelden") == []


# ======================================================================== Wettlauf (Befund M3)
class ZustandsDb(FakeDb):
    """Bildet rd_sitzung_beginnen nach: ein Zuschauer je Ziel, dieselbe Person löst die eigene Sitzung ab (Grund abgeloest)."""

    def __init__(self):
        super().__init__()
        self.zeilen: dict[str, dict] = {}
        self.zaehler = 0
        self.pruefung_fehler: BaseException | None = None

    async def fetchrow(self, sql, *args):
        self.sql.append((sql, args))
        profil, ziel = args[0], args[1]
        abgeloest = None
        for sid, z in self.zeilen.items():
            if z["offen"] and z["ziel"] == ziel:
                if z["profil"] != profil:
                    return {"ergebnis": "belegt", "sitzung_id": None, "abgeloest_id": None, "belegt_von": "Bernd",
                            "belegt_seit": None, "bis_at": None}
                z["offen"], z["grund"] = False, "abgeloest"
                abgeloest = sid
        self.zaehler += 1
        neu = f"00000000-0000-4000-8000-{self.zaehler:012d}"
        self.zeilen[neu] = {"profil": profil, "ziel": ziel, "offen": True, "grund": None}
        return {"ergebnis": "ok", "sitzung_id": neu, "abgeloest_id": abgeloest, "belegt_von": None, "belegt_seit": None,
                "bis_at": datetime.now(timezone.utc) + timedelta(hours=1)}

    async def fetchval(self, sql, *args):
        self.sql.append((sql, args))
        if "rd_sitzung_beenden" in sql:
            z = self.zeilen.get(args[0])
            if z is not None and z["offen"]:
                z["offen"], z["grund"] = False, args[1]
            return "ok"
        if "profil_id = $2" in sql:                       # die Prüfung nach dem Anmelden: ist die Zeile noch offen?
            if self.pruefung_fehler:
                raise self.pruefung_fehler
            z = self.zeilen.get(args[0])
            return 1 if z is not None and z["offen"] and z["profil"] == args[1] else None
        return self.besitzer


class ZustandsGateway(FakeGateway):
    """Der Zugangsdienst im Kleinen: merkt sich angemeldete Sitzungen, beenden einer unbekannten ist ein No-op. Der erste Aufruf
    von anmelden hält an, bis die Probe ihn freigibt (so lässt sich die Lücke zwischen Datenbank und Gateway offen halten)."""

    def __init__(self, bremsen: bool = True):
        super().__init__()
        self.offen: set[str] = set()
        self.bremse = asyncio.Event()
        self.angehalten = asyncio.Event()
        self._bremsen = bremsen

    async def __call__(self, anfrage: dict, zeitlimit: float = 20.0) -> dict:
        self.anfragen.append(anfrage)
        cmd = anfrage["cmd"]
        if cmd in self.fehler:
            raise api.GatewayFehler(self.fehler[cmd])
        if cmd == "proben":
            return {"ok": True, "ziele": self.proben}
        if cmd == "anmelden":
            if self._bremsen:
                self._bremsen = False
                self.angehalten.set()
                await self.bremse.wait()
            self.offen.add(anfrage["sitzung"])
            return {"ok": True, "token": TOKEN}
        if cmd == "beenden":
            self.offen.discard(anfrage["sitzung"])
            return {"ok": True, "offen": False, "grund": anfrage.get("grund")}
        return self.status


@pytest.fixture
def wettlauf(rd, monkeypatch):
    rd.db = ZustandsDb()
    rd.gw = ZustandsGateway()
    monkeypatch.setattr(api, "shared_database", lambda: rd.db)
    monkeypatch.setattr(api, "gateway", rd.gw)
    rd.ctx.tok = token(totp_vor=7200.0)

    @asynccontextmanager
    async def sitzung_des_clients():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=rd.app), base_url="http://kern") as c:
            yield c

    rd.client_async = sitzung_des_clients
    return rd


async def _post(c: httpx.AsyncClient) -> httpx.Response:
    return await c.post("/wamo/api/rd/start", json={"ziel": "sokrates2"})


async def test_zwei_gleichzeitige_starts_ein_zuschauer_bleibt(wettlauf):
    """Start 2 darf die Datenbankzeile von Start 1 erst ablösen, wenn Start 1 am Gateway angemeldet ist (Lock je Profil). Danach
    beendet er die Gateway-Sitzung von Start 1: Es bleibt genau ein Zuschauer, und er gehört zur offenen Datenbankzeile."""
    rd = wettlauf
    async with rd.client_async() as c:
        erster = asyncio.create_task(_post(c))
        await asyncio.wait_for(rd.gw.angehalten.wait(), 5)            # Start 1 steckt zwischen Datenbank und Gateway
        zweiter = asyncio.create_task(_post(c))
        await asyncio.sleep(0.1)
        assert not zweiter.done()                                      # wartet auf den Lock des Profils
        assert [z["offen"] for z in rd.db.zeilen.values()] == [True]   # Start 2 hat Start 1 noch nicht abgelöst
        rd.gw.bremse.set()
        antworten = await asyncio.gather(erster, zweiter)
    assert [a.status_code for a in antworten] == [200, 200]
    assert {s for s, z in rd.db.zeilen.items() if z["offen"]} == {ZWEITE}
    assert rd.db.zeilen[ERSTE]["grund"] == "abgeloest"
    assert rd.gw.offen == {ZWEITE}, "genau ein Zuschauer am Gateway, und es ist die offene Zeile"


async def test_zwei_kerne_ohne_gemeinsamen_lock_ein_zuschauer_bleibt(wettlauf, monkeypatch):
    """Slots a und b teilen keinen Speicher (kein gemeinsamer Lock). Start 2 löst die Zeile von Start 1 ab, das Beenden am
    Gateway läuft ins Leere, weil Start 1 dort noch nicht angemeldet ist. Start 1 prüft deshalb nach dem Anmelden die
    Datenbankzeile, findet sie beendet und schließt seine Gateway-Sitzung sofort wieder."""
    rd = wettlauf
    monkeypatch.setattr(api, "_start_sperre", lambda profil: asyncio.Lock())      # jeder Kern hat seinen eigenen Lock
    async with rd.client_async() as c:
        erster = asyncio.create_task(_post(c))
        await asyncio.wait_for(rd.gw.angehalten.wait(), 5)
        zweite_antwort = await _post(c)                                # Start 2 läuft vollständig durch
        assert zweite_antwort.status_code == 200
        assert rd.gw.offen == {ZWEITE}                                  # das Beenden von Start 1 war ein No-op
        rd.gw.bremse.set()
        erste_antwort = await erster
    assert erste_antwort.status_code == 409 and "abgelöst" in erste_antwort.json()["detail"]
    assert TOKEN not in erste_antwort.text                              # der abgelöste Start bekommt keine Adresse
    assert rd.gw.offen == {ZWEITE}, "der abgelöste Start wurde am Gateway sofort beendet"
    assert {"cmd": "beenden", "sitzung": ERSTE, "grund": "abgeloest"} in rd.gw.befehle("beenden")
    assert {s for s, z in rd.db.zeilen.items() if z["offen"]} == {ZWEITE}


async def test_pruefung_nach_dem_anmelden_ohne_wettlauf_laesst_alles_offen(wettlauf):
    rd = wettlauf
    rd.gw.bremse.set()
    async with rd.client_async() as c:
        r = await _post(c)
    assert r.status_code == 200
    assert rd.gw.offen == {ERSTE} and rd.gw.befehle("beenden") == []


async def test_pruefung_nach_dem_anmelden_fail_closed_bei_datenbankfehler(wettlauf):
    """Kann die Datenbankzeile nicht geprüft werden, bleibt keine Gateway-Sitzung offen, und die Antwort enthält keine Adresse."""
    rd = wettlauf
    rd.gw.bremse.set()
    rd.db.pruefung_fehler = asyncpg.PostgresError("weg")
    async with rd.client_async() as c:
        r = await _post(c)
    assert r.status_code == 503 and TOKEN not in r.text and "url" not in r.json()
    assert rd.gw.offen == set()
    assert {"cmd": "beenden", "sitzung": ERSTE, "grund": "fehler"} in rd.gw.befehle("beenden")


async def test_verschiedene_profile_blockieren_sich_nicht():
    """Der Lock gilt je Profil: ein zweites Profil wartet nicht auf das erste, dasselbe Profil schon."""
    async with api._start_sperre("a"):
        async with asyncio.timeout(1):
            async with api._start_sperre("b"):
                pass
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                async with api._start_sperre("a"):
                    pass


async def test_lock_je_profil_raeumt_sich_auf(wettlauf):
    rd = wettlauf
    rd.gw.bremse.set()
    async with rd.client_async() as c:
        assert (await _post(c)).status_code == 200
    gc.collect()
    assert len(api._start_sperren) == 0


async def test_fehler_im_start_gibt_den_lock_frei(wettlauf):
    """Ein Start, der mit einem Fehler endet, darf das Profil nicht für immer sperren."""
    rd = wettlauf
    rd.gw.fehler["anmelden"] = "abgelehnt"
    rd.gw.bremse.set()
    async with rd.client_async() as c:
        assert (await _post(c)).status_code in (502, 503)
        del rd.gw.fehler["anmelden"]
        assert (await asyncio.wait_for(_post(c), 5)).status_code == 200


def test_ergebnisse_der_datenbank_sind_unveraendert(rd):
    """Gegenprobe zu den Umbauten: belegt bleibt 409, rate 429, voll 409."""
    for ergebnis, status in (("belegt", 409), ("rate", 429), ("voll", 409)):
        rd.db.beginnen = {"ergebnis": ergebnis, "sitzung_id": None, "abgeloest_id": None, "belegt_von": "Bernd",
                          "belegt_seit": None, "bis_at": None}
        assert _start(rd).status_code == status
    assert rd.gw.befehle("anmelden") == []
