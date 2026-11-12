"""Fremdinhalt bei Bürogeräten (geraete_werkzeuge.py, HARMLOSE_KLASSEN): Der Zustand eines Fernsehers oder einer Steckdose macht den Zug nicht zum
Fremdinhalt, sonst brauchte "Fernseher an" nach einem Blick auf den Zustand jedes Mal eine Freigabekarte (Beschwerde vom 12.11.2026). Anderes bleibt
Fremdinhalt: NAS, Router, Drucker, Scanner, die Gesamtliste. Dazu die Grenze bei der Projektion in der Beschreibung des Schaltwerkzeugs.
"""

from __future__ import annotations

import asyncio

import pytest

import geraete_werkzeuge as gw
import test_geraete_werkzeuge as basis
from test_geraete_werkzeuge import Memory, Params, dienst  # noqa: F401  (dienst ist eine Fixture der Basisdatei)

pytestmark = pytest.mark.asyncio

LISTE = [
    {"kennung": "fernseher", "name": "LG", "klasse": "fernseher", "faehigkeiten": ["status", "steuern"], "aktionen": ["an", "aus", "eingang"],
     "eingaenge": ["hdmi1", "hdmi2"]},
    {"kennung": "box", "name": "Box", "klasse": "lautsprecher", "faehigkeiten": ["status", "steuern"], "aktionen": ["stumm"]},
    {"kennung": "steckdose-1", "name": "Steckdose 1", "klasse": "steckdose", "faehigkeiten": ["status", "steuern"], "aktionen": ["an", "aus"]},
    {"kennung": "sokrates-2", "name": "Sokrates-2", "klasse": "server", "faehigkeiten": ["status"]},
    {"kennung": "drucker", "name": "Drucker", "klasse": "drucker", "faehigkeiten": ["status", "drucken"]},
    {"kennung": "scanner", "name": "Scanner", "klasse": "scanner", "faehigkeiten": ["status", "scannen"]},
    {"kennung": "router", "name": "FRITZ!Box", "klasse": "router", "faehigkeiten": ["status"]},
    {"kennung": "nas-1", "name": "NAS", "klasse": "nas", "faehigkeiten": ["status", "nas_suchen"]},
]


def buero():
    """Wie in bot.py: ist_fremd gilt, sobald im Zug etwas als Fremdinhalt gemeldet wurde (TurnState.tainted)."""
    from types import SimpleNamespace

    zug = {"taint": 0}
    identity = SimpleNamespace(user_id="00000000-0000-4000-8000-0000000000b1", rolle="mitglied", name="Anna")
    b = gw.Buerogeraete(identity, Memory(), fremdinhalt=lambda: zug.__setitem__("taint", zug["taint"] + 1), ist_fremd=lambda: zug["taint"] > 0)
    b.zug = zug
    b.karten = []

    async def senden(n):
        b.karten.append(n)

    async def melden(_t):
        pass

    b.anbinden(senden=senden, melden=melden)
    return b


@pytest.fixture
def mit_liste(dienst, monkeypatch):  # noqa: F811
    monkeypatch.setattr(basis, "GERAETE", LISTE)
    return dienst


@pytest.mark.parametrize("kennung", ["fernseher", "box", "steckdose-1", "sokrates-2"])
async def test_status_eines_harmlosen_geraets_markiert_den_zug_nicht(mit_liste, kennung):
    b = buero()
    await b.schemas()
    await b._status(Params(geraet=kennung))
    assert b.zug["taint"] == 0


@pytest.mark.parametrize("kennung", ["drucker", "scanner", "router", "nas-1", "alle"])
async def test_status_von_nas_router_drucker_scanner_und_der_gesamtliste_bleibt_fremdinhalt(mit_liste, kennung):
    b = buero()
    await b.schemas()
    await b._status(Params(geraet=kennung))
    assert b.zug["taint"] == 1


async def test_fernseher_an_nach_dem_blick_auf_den_zustand_braucht_keine_karte(mit_liste):
    b = buero()
    await b.schemas()
    await b._status(Params(geraet="fernseher"))
    p = Params(geraet="fernseher", aktion="an")
    await b._steuern(p)
    assert p.ergebnis["status"] == "success"
    assert b.karten == []
    p = Params(geraet="fernseher", aktion="eingang", eingang="hdmi1")
    await b._steuern(p)  # auch ein zweiter Schaltvorgang im selben Zug geht direkt
    assert p.ergebnis["status"] == "success" and b.karten == []
    assert b.zug["taint"] == 0
    assert [a[0] for a in mit_liste if a[0] == "steuern"] == ["steuern", "steuern"]


async def test_nach_der_gesamtliste_verlangt_das_schalten_weiter_eine_karte(mit_liste):
    b = buero()
    await b.schemas()
    await b._status(Params(geraet="alle"))
    p = Params(geraet="fernseher", aktion="an")
    await b._steuern(p)
    await asyncio.sleep(0)  # die Karte geht als Hintergrundaufgabe an die Oberfläche
    assert p.ergebnis["status"] == "needs_approval"
    assert len(b.karten) == 1 and b.karten[0]["job"] == gw.JOB
    assert [a for a in mit_liste if a[0] == "steuern"] == []  # nichts wurde geschaltet


async def test_nach_einem_nas_treffer_verlangt_das_schalten_weiter_eine_karte(mit_liste):
    b = buero()
    await b.schemas()
    await b._status(Params(geraet="nas-1"))
    p = Params(geraet="steckdose-1", aktion="aus")
    await b._steuern(p)
    assert p.ergebnis["status"] == "needs_approval"


async def test_das_freigegebene_schalten_macht_den_zug_nicht_zum_fremdinhalt(mit_liste):
    b = buero()
    await b.schemas()
    await b._status(Params(geraet="alle"))
    p = Params(geraet="fernseher", aktion="an")
    await b._steuern(p)
    await asyncio.sleep(0)
    kennung = b.karten[0]["items"][0]["id"]
    vorher = b.zug["taint"]
    await b.entscheiden({"job": gw.JOB, "id": kennung, "entscheidung": "ja"})
    assert b.zug["taint"] == vorher  # die Entscheidung über das Schalten fügt keinen Fremdinhalt hinzu
    assert next(a for a in mit_liste if a[0] == "steuern")[1] == "fernseher"


async def test_beschreibung_nennt_die_grenze_bei_der_projektion(mit_liste):
    b = buero()
    schemas = await b.schemas()
    beschreibung = next(s for s in schemas if s.name == "buerogeraet_steuern").description
    for wort in ("spiegeln", "projizieren", "Miracast", "kann WAMO noch nicht", "Windows-Taste", "versprich es nie"):
        assert wort in beschreibung, wort
    assert "–" not in beschreibung and "—" not in beschreibung  # kein Gedankenstrich
