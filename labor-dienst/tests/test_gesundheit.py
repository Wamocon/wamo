"""/gesundheit ohne Token belastet den Dienst kaum (N2 des zweiten Reviews): Der Zustand wird eine Sekunde zwischengespeichert (je Sekunde höchstens eine
Prüfung der Datei), die schnelle Antwort braucht keinen Faden aus dem gemeinsamen Pool, und Antworten 200 werden nur gezählt, nie je Anfrage protokolliert."""
from __future__ import annotations

import json

import pytest
from hilfen import Uhr

from wamo_labor_dienst import app as app_modul
from wamo_labor_dienst.datenbank import ZentralDB
from wamo_labor_dienst.protokoll import Protokoll


@pytest.fixture
def pruefungen(monkeypatch):
    """Zählt, wie oft die Datei wirklich geprüft wird."""
    aufrufe = {"n": 0, "ergebnis": True}
    original = ZentralDB.pruefen

    def gezaehlt(self):
        aufrufe["n"] += 1
        return aufrufe["ergebnis"] and original(self)

    monkeypatch.setattr(ZentralDB, "pruefen", gezaehlt)
    return aufrufe


def test_viele_aufrufe_pruefen_die_datei_nur_einmal_je_sekunde(client, uhr, pruefungen):
    for _ in range(100):
        assert client.get("/gesundheit").json() == {"zustand": "ok"}
    assert pruefungen["n"] == 1
    uhr.vor(1.1)
    assert client.get("/gesundheit").status_code == 200
    assert pruefungen["n"] == 2


def test_die_zwischengespeicherte_antwort_braucht_keinen_faden_aus_dem_pool(client, monkeypatch, pruefungen):
    assert client.get("/gesundheit").status_code == 200  # die erste Antwort prüft die Datei

    def verboten(*_a, **_k):
        raise AssertionError("kein Faden aus dem gemeinsamen Pool für eine zwischengespeicherte Antwort")

    monkeypatch.setattr(app_modul, "run_in_threadpool", verboten)
    for _ in range(20):
        assert client.get("/gesundheit").status_code == 200
    assert pruefungen["n"] == 1


def test_eine_stoerung_wird_gemeldet_und_nach_einer_sekunde_neu_geprueft(client, uhr, pruefungen):
    pruefungen["ergebnis"] = False
    for _ in range(5):
        r = client.get("/gesundheit")
        assert r.status_code == 503 and r.json() == {"zustand": "gestoert"}
    assert pruefungen["n"] == 1
    pruefungen["ergebnis"] = True
    assert client.get("/gesundheit").status_code == 503  # innerhalb der Sekunde gilt der alte Zustand
    uhr.vor(1.1)
    assert client.get("/gesundheit").status_code == 200 and pruefungen["n"] == 2


def _zeilen(client) -> list[dict]:
    dienst = client.app.state.dienst
    dienst.protokoll.beenden()
    datei = dienst.protokoll.datei
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()] if datei.exists() else []


def test_antworten_200_auf_gesundheit_werden_nur_gezaehlt_nicht_einzeln_protokolliert(client, pruefungen):
    for _ in range(100):
        client.get("/gesundheit")
    client.get("/api/kern/durchlauf", headers={"X-WAMO-Platz": "3"})  # andere Anfragen werden weiter protokolliert
    zeilen = _zeilen(client)
    einzeln = [z for z in zeilen if z.get("bereich") == "gesundheit" and "ms" in z]
    assert einzeln == []
    zaehler = [z for z in zeilen if z.get("gezaehlt") == "anfrage" and z.get("bereich") == "gesundheit"]
    assert len(zaehler) == 1 and zaehler[0]["anzahl"] == 100 and zaehler[0]["status"] == 200 and zaehler[0]["art"] == "abgetastet"
    assert any(z.get("bereich") == "kern" for z in zeilen)


def test_stoerungen_auf_gesundheit_stehen_einzeln_im_protokoll(client, pruefungen):
    pruefungen["ergebnis"] = False
    client.get("/gesundheit")
    zeilen = _zeilen(client)
    assert any(z.get("bereich") == "gesundheit" and z.get("status") == 503 and "ms" in z for z in zeilen)


def test_gezaehlt_schreibt_keine_zeile_je_ereignis_und_eine_zusammenfassung_je_fenster(tmp_path):
    uhr = Uhr()
    p = Protokoll(tmp_path, uhr)
    for _ in range(500):
        p.gezaehlt("anfrage", bereich="gesundheit", status=200)
    p.abwarten()
    assert not (tmp_path / "dienst.jsonl").exists()
    uhr.vor(61)
    p.gezaehlt("anfrage", bereich="gesundheit", status=200)
    p.abwarten()
    zeilen = [json.loads(z) for z in (tmp_path / "dienst.jsonl").read_text(encoding="utf-8").splitlines()]
    assert zeilen == [{"zeit": "2026-11-28T09:31:01Z", "art": "abgetastet", "gezaehlt": "anfrage", "bereich": "gesundheit", "status": 200, "anzahl": 500}]
    p.beenden()
    assert json.loads((tmp_path / "dienst.jsonl").read_text(encoding="utf-8").splitlines()[-1])["anzahl"] == 1


def test_gesundheit_bleibt_ohne_token_ohne_daten_und_nur_get(client):
    r = client.get("/gesundheit")
    assert r.json() == {"zustand": "ok"} and r.headers["cache-control"] == "no-store"
    assert client.post("/gesundheit").status_code == 405
