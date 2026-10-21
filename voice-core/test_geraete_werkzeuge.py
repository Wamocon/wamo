"""Bürogeräte im Gespräch: Enum aus Register und Dienst, Rollen, Fremdinhalt-Regel, Drucken nur mit Karte und nur
eigene Dokumente, genau einmal entscheiden, Protokoll (Gerätedienst und Datenbank als Attrappen)."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import geraete_client as gc
import geraete_werkzeuge as gw

GERAETE = [
    {"kennung": "drucker", "name": "Drucker", "klasse": "drucker", "faehigkeiten": ["status", "drucken"]},
    {"kennung": "scanner", "name": "Scanner", "klasse": "scanner", "faehigkeiten": ["status", "scannen"]},
    {"kennung": "router", "name": "FRITZ!Box", "klasse": "router", "faehigkeiten": ["status"]},
    {"kennung": "steckdose-1", "name": "Steckdose 1", "klasse": "steckdose", "faehigkeiten": ["status", "steuern"],
     "aktionen": ["an", "aus"]},
]


class Params:
    def __init__(self, **arguments):
        self.arguments = arguments
        self.ergebnis = None

    async def result_callback(self, ergebnis):
        self.ergebnis = ergebnis


class Memory:
    enabled = False
    session_id = None
    user_id = "00000000-0000-4000-8000-0000000000b1"

    def __init__(self):
        self.status = []

    async def log_action(self, **kwargs):
        return "aktion-1"

    async def update_action(self, aid, status):
        self.status.append(status)
        return True


@pytest.fixture
def dienst(monkeypatch):
    aufrufe = []

    async def aufruf(werkzeug, kontext, *, geraet=None, argumente=None, timeout=20.0, **_k):
        aufrufe.append((werkzeug, geraet, argumente, kontext))
        if werkzeug == "geraete":
            liste = [dict(g) for g in GERAETE]
            if kontext.rolle == "admin":
                liste[2]["faehigkeiten"] = ["status", "router_geraete"]
            return {"status": "success", "geraete": liste}, b""
        if werkzeug == "router_geraete":
            return {"status": "success", "anzahl": 1, "aktiv": 1,
                    "geraete": [{"name": "telefon", "adresse": "192.168.178.50", "aktiv": True, "verbindung": "WLAN"}]}, b""
        return {"status": "success", "werkzeug": werkzeug}, b""
    monkeypatch.setattr(gc, "aufruf", aufruf)
    monkeypatch.setattr(gc, "verfuegbar", lambda: True)
    gw._liste.clear()
    return aufrufe


def buero(rolle="mitglied", fremd=False, memory=None):
    identity = SimpleNamespace(user_id="00000000-0000-4000-8000-0000000000b1", rolle=rolle, name="Anna")
    zaehler = {"taint": 0}
    b = gw.Buerogeraete(identity, memory or Memory(), fremdinhalt=lambda: zaehler.__setitem__("taint", zaehler["taint"] + 1),
                        ist_fremd=lambda: fremd)
    b.zaehler = zaehler
    return b


async def test_schemas_nach_rolle(dienst):
    namen = [s.name for s in await buero().schemas()]
    assert namen == ["buerogeraet_status", "buerogeraet_steuern", "drucken", "scannen"]
    admin = [s.name for s in await buero("admin").schemas()]
    assert "router_geraete" in admin
    status = (await buero().schemas())[0]
    assert status.properties["geraet"]["enum"] == ["drucker", "scanner", "router", "steckdose-1", "alle"]


async def test_ohne_dienst_keine_werkzeuge(monkeypatch):
    monkeypatch.setattr(gc, "verfuegbar", lambda: False)
    assert await buero().schemas() == []


def test_register_schneidet():
    dienst = [{"kennung": "drucker"}, {"kennung": "scanner"}, {"kennung": "fernseher"}]
    assert [g["kennung"] for g in gw.erlaubte_geraete(dienst, {"drucker": "aktiv", "scanner": "gesperrt"}, False)] \
        == ["drucker"]
    assert len(gw.erlaubte_geraete(dienst, None, False)) == 3
    assert len(gw.erlaubte_geraete(dienst, {}, False)) == 3
    assert gw.erlaubte_geraete(dienst, None, True) == []


async def test_status_ist_fremdinhalt(dienst):
    b = buero()
    await b.schemas()
    p = Params(geraet="drucker")
    await b._status(p)
    assert p.ergebnis["status"] == "success" and p.ergebnis["vertrauen"] == gw.HINWEIS
    assert b.zaehler["taint"] == 1
    assert dienst[-1][3].user_id == "00000000-0000-4000-8000-0000000000b1"  # Kontext aus der Identität
    p = Params(geraet="laptop-anna")
    await b._status(p)
    assert p.ergebnis["status"] == "rejected"


async def test_steuern_direkt_und_mit_fremdinhalt(dienst):
    b = buero()
    await b.schemas()
    p = Params(geraet="steckdose-1", aktion="an")
    await b._steuern(p)
    assert p.ergebnis["status"] == "success" and dienst[-1][:3] == ("steuern", "steckdose-1", {"aktion": "an"})
    p = Params(geraet="steckdose-1", aktion="lautstaerke", wert=90)
    await b._steuern(p)
    assert p.ergebnis["status"] == "rejected"
    vorsichtig = buero(fremd=True)
    karten = []

    async def senden(n):
        karten.append(n)

    async def melden(_t):
        pass
    vorsichtig.anbinden(senden=senden, melden=melden)
    await vorsichtig.schemas()
    anzahl = len(dienst)
    p = Params(geraet="steckdose-1", aktion="aus")
    await vorsichtig._steuern(p)
    await asyncio.sleep(0)
    assert p.ergebnis["status"] == "needs_approval" and len(dienst) == anzahl  # noch nichts geschaltet
    assert karten and karten[0]["job"] == "geraet"
    aid = karten[0]["items"][0]["id"]
    await vorsichtig.entscheiden({"job": "geraet", "id": aid, "entscheidung": "ja"})
    assert dienst[-1][:3] == ("steuern", "steckdose-1", {"aktion": "aus"})
    anzahl = len(dienst)
    await vorsichtig.entscheiden({"job": "geraet", "id": aid, "entscheidung": "ja"})  # genau einmal
    assert len(dienst) == anzahl


async def test_drucken_nur_eigenes_dokument_mit_karte(dienst):
    zeilen = {}

    class DB:
        def t(self, name):
            return f"wamo_test.{name}"

        async def fetchrow(self, sql, *args):
            zeilen["sql"], zeilen["args"] = sql, args
            if "Fremd" in (args[1] if len(args) > 1 else ""):
                return None
            return {"id": "c1", "title": "Angebot Alpha",
                    "data": json.dumps({"kind": "dokument", "title": "Angebot Alpha", "text": "# Angebot\nText"})}

        async def execute(self, *_a):
            return "INSERT 0 1"

        async def fetch(self, *_a):
            return []  # noch keine gemeinsamen Geräte im Register

    mem = Memory()
    mem.enabled = True
    mem.db = DB()
    b = buero(memory=mem)
    karten = []

    async def senden(n):
        karten.append(n)

    async def melden(_t):
        pass
    b.anbinden(senden=senden, melden=melden)
    await b.schemas()
    p = Params(titel="Fremd")
    await b._drucken(p)
    assert p.ergebnis["status"] == "rejected"
    p = Params(titel="Angebot", kopien=9, farbe=True)
    await b._drucken(p)
    await asyncio.sleep(0)
    assert p.ergebnis["status"] == "needs_approval"
    assert "user_id = $1::uuid" in zeilen["sql"] and zeilen["args"][0] == mem.user_id
    assert not any(a[0] == "drucken" for a in dienst)
    aid = karten[-1]["items"][0]["id"]
    await b.entscheiden({"job": "geraet", "id": aid, "entscheidung": "nein"})
    assert not any(a[0] == "drucken" for a in dienst) and "rejected" in mem.status
    p = Params(titel="Angebot")
    await b._drucken(p)
    await asyncio.sleep(0)
    aid = karten[-1]["items"][0]["id"]
    await b.entscheiden({"job": "geraet", "id": aid, "entscheidung": "ja"})
    druck = [a for a in dienst if a[0] == "drucken"][-1]
    assert druck[2]["kopien"] == 1 and druck[2]["farbe"] is False
    assert druck[2]["dokument"] == {"art": "dokument", "titel": "Angebot Alpha", "text": "# Angebot\nText"}
    assert mem.status[-3:] == ["approved", "running", "done"]


async def test_router_liste_nur_admin_und_nur_canvas(dienst):
    b = buero("admin")
    gesendet = []

    async def senden(n):
        gesendet.append(n)

    async def melden(_t):
        pass
    b.anbinden(senden=senden, melden=melden)
    await b.schemas()
    p = Params()
    await b._router_geraete(p)
    assert "geraete" not in p.ergebnis and p.ergebnis["anzahl"] == 1
    assert gesendet[-1]["type"] == "canvas" and "id" not in gesendet[-1]["item"]
    mitglied = buero()
    await mitglied.schemas()
    p = Params()
    await mitglied._router_geraete(p)
    assert p.ergebnis["status"] == "rejected"


async def test_unbekannte_entscheidung_tut_nichts(dienst):
    b = buero()
    await b.schemas()
    assert await b.entscheiden({"job": "geraet", "id": "nicht-hex", "entscheidung": "ja"}) is None
    assert await b.entscheiden({"job": "geraet", "id": "0123456789abcdef", "entscheidung": "ja"}) is None
