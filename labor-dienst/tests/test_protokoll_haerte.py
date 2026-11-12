"""Härtung des Protokolls (Befund 2 des Sicherheitsreviews): Betreuer-Spur in eigener Datei ohne Rotation, Abtastung abgelehnter Anfragen,
Schreiben außerhalb der Ereignisschleife."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from hilfen import BETREUER_TOKEN, KERN_TOKEN, Uhr, betreuer_kopf, kern_kopf, oeffnen

from wamo_labor_dienst.app import create_app
from wamo_labor_dienst.protokoll import Protokoll


def _zeilen(datei) -> list[dict]:
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()] if datei.exists() else []


def test_betreuer_ereignisse_liegen_in_eigener_datei_und_rotieren_nie(tmp_path):
    p = Protokoll(tmp_path, Uhr(), max_bytes=300)
    for i in range(5):
        p.betreuer(aktion="neuer_durchlauf", platz=i + 1, status=200)
    for i in range(60):  # die allgemeine Datei rotiert viele Male
        p.ereignis("anfrage", nr=i)
    p.abwarten()
    assert [e["platz"] for e in _zeilen(tmp_path / "betreuer.jsonl")] == [1, 2, 3, 4, 5]
    assert all(e["art"] == "betreuer" and e["zeit"] == "2026-11-28T09:30:00Z" for e in _zeilen(tmp_path / "betreuer.jsonl"))
    assert sorted(f.name for f in tmp_path.iterdir()) == ["betreuer.jsonl", "dienst.jsonl", "dienst.jsonl.1"]


def test_betreuer_datei_hat_eine_obergrenze(tmp_path):
    p = Protokoll(tmp_path, Uhr(), betreuer_max_bytes=400)
    for i in range(100):
        p.betreuer(aktion="zuruecksetzen", platz=3, status=200, nr=i)
    p.abwarten()
    datei = tmp_path / "betreuer.jsonl"
    assert 0 < datei.stat().st_size <= 400
    assert _zeilen(datei)[0]["nr"] == 0  # die ältesten Einträge bleiben, nichts wird überschrieben


def test_leeren_loescht_auch_die_betreuer_datei(tmp_path):
    p = Protokoll(tmp_path, Uhr())
    p.betreuer(aktion="daten_loeschen", platz=1, status=200)
    p.ereignis("anfrage", status=200)
    p.leeren()
    assert list(tmp_path.iterdir()) == []


def test_abgelehnte_anfragen_werden_abgetastet_und_der_rest_gezaehlt(tmp_path):
    uhr = Uhr()
    p = Protokoll(tmp_path, uhr, abtast_je_minute=3)
    for _ in range(100):
        p.abgetastet("anfrage", bereich="kern", status=401)
    p.abgetastet("anfrage", bereich="kern", status=404)  # anderer Schlüssel, eigene Zählung
    p.abwarten()
    zeilen = _zeilen(tmp_path / "dienst.jsonl")
    assert [z["status"] for z in zeilen] == [401, 401, 401, 404]
    uhr.vor(61)
    p.abgetastet("anfrage", bereich="kern", status=401)
    p.abwarten()
    zusammen = [z for z in _zeilen(tmp_path / "dienst.jsonl") if z["art"] == "abgetastet"]
    assert zusammen == [{"zeit": "2026-11-28T09:31:01Z", "art": "abgetastet", "abgelehnt": "anfrage", "bereich": "kern", "status": 401, "anzahl": 97}]


def test_beenden_schreibt_die_offenen_zaehler(tmp_path):
    p = Protokoll(tmp_path, Uhr(), abtast_je_minute=1)
    for _ in range(10):
        p.abgetastet("anfrage", bereich="browser", status=401)
    p.beenden()
    zusammen = [z for z in _zeilen(tmp_path / "dienst.jsonl") if z["art"] == "abgetastet"]
    assert len(zusammen) == 1 and zusammen[0]["anzahl"] == 9 and zusammen[0]["bereich"] == "browser"
    p.beenden()  # ein zweites Mal schreibt nichts mehr
    assert len([z for z in _zeilen(tmp_path / "dienst.jsonl") if z["art"] == "abgetastet"]) == 1


def test_ereignis_blockiert_die_ereignisschleife_nicht(tmp_path, monkeypatch):
    p = Protokoll(tmp_path, Uhr())
    original = p._schreiben

    def langsam(datei, zeile):
        time.sleep(0.05)
        original(datei, zeile)

    monkeypatch.setattr(p, "_schreiben", langsam)
    start = time.perf_counter()
    for i in range(40):
        p.ereignis("anfrage", nr=i)
    assert time.perf_counter() - start < 0.5  # das Schreiben dauert 2 Sekunden, der Aufrufer wartet nicht
    p.abwarten()
    assert len(_zeilen(tmp_path / "dienst.jsonl")) == 40


def test_volle_warteschlange_verwirft_und_zaehlt(tmp_path, monkeypatch):
    p = Protokoll(tmp_path, Uhr(), warteschlange_max=5)
    original = p._schreiben
    frei = __import__("threading").Event()

    def gesperrt(datei, zeile):
        frei.wait(5)
        original(datei, zeile)

    monkeypatch.setattr(p, "_schreiben", gesperrt)
    for i in range(30):
        p.ereignis("anfrage", nr=i)  # kein Aufruf blockiert, auch wenn der Schreiber hängt
    frei.set()
    p.abwarten()
    zeilen = _zeilen(tmp_path / "dienst.jsonl")
    assert len([z for z in zeilen if z["art"] == "anfrage"]) <= 7
    p.ereignis("anfrage", nr=99)
    p.abwarten()
    assert any(z["art"] == "verworfen" and z["anzahl"] >= 20 for z in _zeilen(tmp_path / "dienst.jsonl"))


def test_schreiber_beendet_sich_im_leerlauf_und_startet_neu(tmp_path):
    p = Protokoll(tmp_path, Uhr(), leerlauf_secs=0.05)
    p.ereignis("anfrage", nr=1)
    p.abwarten()
    time.sleep(0.3)
    assert p._faden is None or not p._faden.is_alive()
    p.ereignis("anfrage", nr=2)
    p.abwarten()
    assert [z["nr"] for z in _zeilen(tmp_path / "dienst.jsonl")] == [1, 2]


# ---------- Schnittstelle ----------
@pytest.fixture
def klein(konfig, uhr):
    """Dienst mit winziger Rotationsgrenze: Die allgemeine Datei rotiert nach wenigen Zeilen."""
    with TestClient(create_app(konfig, uhr=uhr, protokoll_max_bytes=1500), base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        yield c


def test_unauthentifizierte_flut_spuelt_das_betreuer_protokoll_nicht_weg(klein, konfig):
    oeffnen(klein, 3, version=7)
    klein.post("/api/betreuer/plaetze/3/durchlauf-beenden", headers=betreuer_kopf())
    for i in range(600):  # die Flut: kein Token, falscher Token, unbekannte Pfade, fremde Methoden
        klein.get("/api/kern/durchlauf", headers={"X-WAMO-Platz": "3"})
        klein.get(f"/gibt-es-nicht-{i % 7}")
        klein.get("/api/browser/board", headers={"X-Platz-Token": "0" * 32})
        klein.post("/api/betreuer/plaetze/3/daten-loeschen", headers={"Authorization": f"Bearer {KERN_TOKEN}"})
        klein.delete("/api/kern/tickets")
    dienst = klein.app.state.dienst
    dienst.protokoll.beenden()
    betreuer = _zeilen(konfig.daten / "protokoll" / "betreuer.jsonl")
    assert [e["aktion"] for e in betreuer] == ["neuer_durchlauf", "durchlauf_beenden"] and all(e["status"] == 200 for e in betreuer)
    allgemein = _zeilen(konfig.daten / "protokoll" / "dienst.jsonl")
    assert len(allgemein) < 120  # 3000 Anfragen ergeben nur eine Handvoll Zeilen und Zähler
    assert any(z["art"] == "abgetastet" and z["anzahl"] > 100 for z in allgemein + _zeilen(konfig.daten / "protokoll" / "dienst.jsonl.1"))
    assert klein.get("/api/kern/durchlauf", headers=kern_kopf()).status_code == 200  # der Dienst arbeitet normal weiter


def test_authentifizierte_betreuer_aufrufe_werden_nie_abgetastet(klein, konfig):
    for _ in range(20):
        assert klein.post("/api/betreuer/plaetze/3/zuruecksetzen", headers=betreuer_kopf()).status_code == 200
    klein.app.state.dienst.protokoll.beenden()
    assert len(_zeilen(konfig.daten / "protokoll" / "betreuer.jsonl")) == 20


def test_betreuer_fehler_mit_gueltigem_token_stehen_in_der_betreuer_spur(klein, konfig):
    assert klein.post("/api/betreuer/plaetze/99/zuruecksetzen", headers=betreuer_kopf()).status_code == 404
    assert klein.post("/api/betreuer/plaetze/3/neuer-durchlauf", json={"einwilligung_version": 0}, headers=betreuer_kopf()).status_code == 422
    klein.app.state.dienst.protokoll.beenden()
    assert [e["status"] for e in _zeilen(konfig.daten / "protokoll" / "betreuer.jsonl")] == [404, 422]


def test_ende_der_anwendung_schreibt_die_wartenden_zeilen(konfig, uhr):
    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:
        oeffnen(c, 3)
    assert [e["aktion"] for e in _zeilen(konfig.daten / "protokoll" / "betreuer.jsonl")] == ["neuer_durchlauf"]
    assert BETREUER_TOKEN not in (konfig.daten / "protokoll" / "betreuer.jsonl").read_text(encoding="utf-8")
