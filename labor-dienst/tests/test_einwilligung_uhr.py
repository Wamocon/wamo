"""N4 des zweiten Reviews: Obergrenze der Einwilligungsversion (1000) mit Rücksetzweg, Untergrenze der Aufbewahrung (Labortag) und Uhrsprung vor der Löschung."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from hilfen import BETREUER_TOKEN, KERN_TOKEN, Uhr, anlegen_http, betreuer_kopf, kern_kopf, oeffnen

from wamo_labor_dienst.app import create_app
from wamo_labor_dienst.datenbank import ZentralDB
from wamo_labor_dienst.konfig import Konfig, KonfigFehler, aus_umgebung

SENKEN = "/api/betreuer/einwilligung-minimum-setzen"


def _senken(client, version, bestaetigung="senken", **kopf):
    koerper = {"einwilligung_version": version, **({"bestaetigung": bestaetigung} if bestaetigung is not None else {})}
    return client.post(SENKEN, json=koerper, headers=kopf or betreuer_kopf())


def _neuer(client, platz, version):
    return client.post(f"/api/betreuer/plaetze/{platz}/neuer-durchlauf", json={"einwilligung_version": version}, headers=betreuer_kopf())


# ---------- (a) Obergrenze und Rücksetzweg ----------
def test_einwilligungsversion_hat_die_obergrenze_1000(client):
    assert _neuer(client, 3, 1000).status_code == 200
    for zu_gross in (1001, 5000, 2**31 - 1, 2**31):
        r = _neuer(client, 4, zu_gross)
        assert r.status_code == 422 and r.json()["fehler"] == "eingabe_ungueltig", zu_gross


def test_zentral_senken_nur_wenn_es_ein_maximum_gibt_und_der_wert_kleiner_ist(zentral):
    assert zentral.einwilligung_hoechste() is None
    assert zentral.einwilligung_senken(1) is False  # nichts erfasst, nichts zu senken
    zentral.einwilligung_anheben(7)
    assert zentral.einwilligung_hoechste() == 7
    assert zentral.einwilligung_senken(7) is False and zentral.einwilligung_senken(9) is False
    assert zentral.einwilligung_senken(3) is True and zentral.einwilligung_hoechste() == 3
    assert zentral.einwilligung_anheben(2) is False and zentral.einwilligung_anheben(3) is True


def test_senken_stellt_das_minimum_zurueck_wenn_kein_durchlauf_laeuft(client):
    oeffnen(client, 3, version=7)
    client.post("/api/betreuer/plaetze/3/durchlauf-beenden", headers=betreuer_kopf())
    assert _neuer(client, 4, 5).status_code == 422  # zu klein: Das Maximum steht bei 7 (ein Tippfehler 77 statt 7 wäre so ein Fall)
    r = _senken(client, 3)
    assert r.status_code == 200 and r.json() == {"einwilligung_hoechste": 3}
    client.post("/api/betreuer/plaetze/4/durchlauf-beenden", headers=betreuer_kopf())
    assert _neuer(client, 4, 2).status_code == 422 and _neuer(client, 4, 3).status_code == 200


def test_senken_nicht_waehrend_ein_durchlauf_laeuft(client):
    oeffnen(client, 3, version=7)  # Platz 3 ist offen
    r = _senken(client, 3)
    assert r.status_code == 409 and r.json()["fehler"] == "durchlauf_laeuft" and set(r.json()) == {"fehler", "text"}
    assert _neuer(client, 4, 3).status_code == 422  # das Maximum ist unverändert


def test_senken_nur_zum_senken(client):
    oeffnen(client, 3, version=7)
    client.post("/api/betreuer/plaetze/3/durchlauf-beenden", headers=betreuer_kopf())
    for nicht_kleiner in (7, 8, 1000):
        assert _senken(client, nicht_kleiner).status_code == 422, nicht_kleiner


def test_senken_ohne_erfasste_version_ist_422(client):
    assert _senken(client, 1).status_code == 422


@pytest.mark.parametrize("version", [0, -1, "3", True, 1.5, None, 1001, 2**40])
def test_senken_prueft_die_version(client, version):
    oeffnen(client, 3, version=500)
    client.post("/api/betreuer/plaetze/3/durchlauf-beenden", headers=betreuer_kopf())
    assert _senken(client, version).status_code == 422


@pytest.mark.parametrize("bestaetigung", [None, "", "ja", "SENKEN", "senken ", 1, True])
def test_senken_verlangt_die_ausdrueckliche_bestaetigung(client, bestaetigung):
    oeffnen(client, 3, version=7)
    client.post("/api/betreuer/plaetze/3/durchlauf-beenden", headers=betreuer_kopf())
    assert _senken(client, 3, bestaetigung).status_code == 422
    assert _neuer(client, 4, 3).status_code == 422  # nichts wurde gesenkt


def test_senken_nimmt_keine_unbekannten_felder_und_nur_json(client):
    oeffnen(client, 3, version=7)
    client.post("/api/betreuer/plaetze/3/durchlauf-beenden", headers=betreuer_kopf())
    assert client.post(SENKEN, json={"einwilligung_version": 3, "bestaetigung": "senken", "x": 1}, headers=betreuer_kopf()).status_code == 422
    assert client.post(SENKEN, content=b'{"einwilligung_version": 3, "bestaetigung": "senken"}', headers=betreuer_kopf(**{"Content-Type": "text/plain"})).status_code == 422
    assert client.post(SENKEN, headers=betreuer_kopf()).status_code == 422
    assert client.post(SENKEN, content=b"x" * 5000, headers=betreuer_kopf(**{"Content-Type": "application/json"})).status_code == 413


def test_senken_nur_mit_betreuer_token_und_nur_post(client):
    oeffnen(client, 3, version=7)
    client.post("/api/betreuer/plaetze/3/durchlauf-beenden", headers=betreuer_kopf())
    assert client.post(SENKEN, json={"einwilligung_version": 3, "bestaetigung": "senken"}).status_code == 401
    for falsch in (f"Bearer {KERN_TOKEN}", "Bearer " + "x" * 50):
        assert client.post(SENKEN, json={"einwilligung_version": 3, "bestaetigung": "senken"}, headers={"Authorization": falsch}).status_code == 403
    r = client.get(SENKEN, headers=betreuer_kopf())
    assert r.status_code == 405 and r.headers["allow"] == "POST"
    assert _neuer(client, 4, 3).status_code == 422  # nichts hat sich geändert


def test_senken_steht_mit_zeit_art_und_wert_in_der_betreuer_spur(client):
    oeffnen(client, 3, version=7)
    client.post("/api/betreuer/plaetze/3/durchlauf-beenden", headers=betreuer_kopf())
    _senken(client, 3)
    _senken(client, 9)  # abgelehnt
    dienst = client.app.state.dienst
    dienst.protokoll.beenden()
    zeilen = [json.loads(z) for z in (dienst.protokoll.ordner / "betreuer.jsonl").read_text(encoding="utf-8").splitlines()]
    senken = [z for z in zeilen if z["aktion"] == "einwilligung_minimum_setzen"]
    assert [(z["status"], z.get("wert"), z["platz"]) for z in senken] == [(200, 3, None), (422, 9, None)]
    assert all(z["zeit"] == "2026-11-28T09:30:00Z" for z in senken)


# ---------- (b) Untergrenze der Aufbewahrung ----------
def _env(tmp_path, **extra):
    kern, betreuer = tmp_path / "k", tmp_path / "b"
    kern.write_text(KERN_TOKEN, encoding="utf-8")
    betreuer.write_text(BETREUER_TOKEN, encoding="utf-8")
    return {"WAMO_LABOR_DIENST_KERN_TOKEN_DATEI": str(kern), "WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI": str(betreuer), **extra}


def test_bis_hat_die_untergrenze_labortag_28_11_2026(tmp_path):
    assert aus_umgebung(_env(tmp_path, WAMO_LABOR_DIENST_BIS="2026-11-28")).bis == date(2026, 11, 28)
    for zu_frueh in ("2026-11-27", "2026-11-12", "2020-01-01"):
        with pytest.raises(KonfigFehler, match="WAMO_LABOR_DIENST_BIS.*2026-11-28.*2026-12-11"):
            aus_umgebung(_env(tmp_path, WAMO_LABOR_DIENST_BIS=zu_frueh))


# ---------- (b) Uhrsprung vor der Löschung ----------
class Wand:
    """Wanduhr und monotone Uhr: vor() lässt beide laufen (richtige Zeit), springen() stellt nur die Wanduhr (ein Sprung)."""

    def __init__(self, start: datetime):
        self.t, self.mono = start.timestamp(), 1000.0

    def uhr(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.mono

    def vor(self, sekunden: float) -> None:
        self.t += sekunden
        self.mono += sekunden

    def springen(self, sekunden: float) -> None:
        self.t += sekunden


def _dienst(konfig, wand):
    app = create_app(konfig, uhr=wand.uhr, monotonic=wand.monotonic)
    dienst = app.state.dienst
    dienst.neuer_besucher(3, 1)
    dienst.token_vergeben(3, "ab" * 32)
    return app, dienst


def _noch_da(dienst) -> bool:
    return dienst.plaetze[3].zustand()["offen"] is True and dienst.zentral.hat_token(3)


def test_ein_uhrsprung_nach_vorn_leert_keine_plaetze(konfig):
    wand = Wand(datetime(2026, 12, 10, 12, tzinfo=UTC))
    _, dienst = _dienst(konfig, wand)
    wand.vor(3600)
    wand.springen(5 * 86400)  # die Wanduhr springt fünf Tage, die monotone Uhr nicht: Das ist kein echter Zeitablauf
    assert dienst.aufbewahrung_pruefen() is False
    assert _noch_da(dienst)
    dienst.protokoll.abwarten()
    assert "Uhrsprung" in dienst.protokoll.datei.read_text(encoding="utf-8")


def test_echter_zeitablauf_leert_nach_dem_ende_der_aufbewahrung(konfig):
    wand = Wand(datetime(2026, 12, 10, 12, tzinfo=UTC))
    _, dienst = _dienst(konfig, wand)
    wand.vor(3 * 86400)  # drei Tage laufen wirklich ab: der 13.12. liegt nach dem 11.12.
    assert dienst.aufbewahrung_pruefen() is True
    assert not dienst.zentral.hat_token(3) and dienst.plaetze[3].zustand()["tickets"] == 0


@pytest.mark.parametrize("sprung,geleert", [(86400, True), (86401, False), (-100, True)])
def test_ein_sprung_bis_zu_einem_tag_ist_plausibel(konfig, sprung, geleert):
    wand = Wand(datetime(2026, 12, 11, 12, tzinfo=UTC))  # der letzte Tag der Aufbewahrung
    _, dienst = _dienst(konfig, wand)
    wand.vor(13 * 3600)  # 01:00 am 12.12.: nach dem Ende
    wand.springen(sprung)
    assert dienst.aufbewahrung_pruefen() is geleert
    assert _noch_da(dienst) is (not geleert)


def test_ohne_ende_der_aufbewahrung_wird_nie_geleert_auch_nicht_nach_einem_sprung(konfig):
    wand = Wand(datetime(2026, 11, 28, 9, 30, tzinfo=UTC))
    _, dienst = _dienst(konfig, wand)
    wand.springen(-3 * 86400)
    assert dienst.aufbewahrung_pruefen() is False and _noch_da(dienst)


def test_ein_neustart_nach_dem_ende_leert_wie_bisher(konfig):
    wand = Wand(datetime(2026, 11, 28, 9, 30, tzinfo=UTC))
    _, dienst = _dienst(konfig, wand)
    spaeter = Wand(datetime(2026, 12, 20, 12, tzinfo=UTC))  # der Dienst wird nach dem Ende neu gestartet: Die Uhr gilt ab diesem Start
    app2 = create_app(konfig, uhr=spaeter.uhr, monotonic=spaeter.monotonic)
    assert app2.state.dienst.plaetze[3].zustand()["tickets"] == 0 and not app2.state.dienst.zentral.hat_token(3)


def test_die_stuendliche_pruefung_nutzt_dieselbe_regel(konfig, monkeypatch):
    import asyncio

    from wamo_labor_dienst.app import _aufraeumen_schleife

    wand = Wand(datetime(2026, 12, 10, 12, tzinfo=UTC))
    _, dienst = _dienst(konfig, wand)
    wand.springen(10 * 86400)
    pausen = []

    async def schlafen(s):
        pausen.append(s)
        if len(pausen) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr("wamo_labor_dienst.app.asyncio.sleep", schlafen)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_aufraeumen_schleife(dienst))
    assert _noch_da(dienst)
