"""Betrieb: Gesundheit, Aufbewahrung bis 11.12.2026 (D10), Einstieg, Neustart ohne Datenverlust."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hilfen import BETREUER_TOKEN, KERN_TOKEN, Uhr, anlegen_http, betreuer_kopf, kern_kopf, oeffnen

from wamo_labor_dienst.app import create_app
from wamo_labor_dienst.konfig import Konfig

PAKET_ORDNER = Path(__file__).resolve().parents[1]


def _tag(jahr: int, monat: int, tag: int, stunde: int = 12) -> float:
    return datetime(jahr, monat, tag, stunde, tzinfo=UTC).timestamp()


def test_gesundheit_ohne_token_nur_zustand_keine_daten(client):
    oeffnen(client, 3)
    r = client.get("/gesundheit")
    assert r.status_code == 200 and r.json() == {"zustand": "ok"}
    assert r.headers["cache-control"] == "no-store"


def test_gesundheit_nur_get(client):
    r = client.post("/gesundheit")
    assert r.status_code == 405 and r.headers["allow"] == "GET"


def test_gesundheit_meldet_stoerung_ohne_einzelheiten(client, monkeypatch):
    from wamo_labor_dienst.datenbank import ZentralDB

    monkeypatch.setattr(ZentralDB, "pruefen", lambda self: False)
    r = client.get("/gesundheit")
    assert r.status_code == 503 and r.json() == {"zustand": "gestoert"}


def test_neustart_behaelt_die_daten(konfig, uhr):
    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:
        d = oeffnen(c, 3, version=2)
        anlegen_http(c, d, titel="Bleibt nach dem Neustart")
        token = c.post("/api/betreuer/plaetze/3/platz-token", headers=betreuer_kopf()).json()["platz_token"]
    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:
        assert c.get("/api/kern/durchlauf", headers=kern_kopf()).json()["durchlauf_id"] == d
        assert c.get("/api/kern/tickets?q=Neustart", headers=kern_kopf()).json()["gesamt"] == 1
        assert c.get("/api/browser/board", headers={"X-Platz-Token": token}).status_code == 200


@pytest.mark.parametrize("jetzt,bleibt", [(_tag(2026, 11, 28), True), (_tag(2026, 12, 11, 23), True), (_tag(2026, 12, 12, 0), False), (_tag(2027, 1, 5), False)])
def test_beim_start_werden_daten_nach_der_aufbewahrung_geloescht(konfig, jetzt, bleibt):
    uhr = Uhr(_tag(2026, 11, 28))
    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:
        d = oeffnen(c, 3)
        anlegen_http(c, d, titel="Daten des Labortags")
        token = c.post("/api/betreuer/plaetze/3/platz-token", headers=betreuer_kopf()).json()["platz_token"]
    uhr.t = jetzt
    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:
        j = c.get("/api/kern/tickets?q=Labortags", headers=kern_kopf()).json()
        board = c.get("/api/browser/board", headers={"X-Platz-Token": token})
        assert (j["gesamt"] == 1) is bleibt and (board.status_code == 200) is bleibt
        protokoll = konfig.daten / "protokoll" / "dienst.jsonl"
        if not bleibt:
            assert c.get("/api/kern/durchlauf", headers=kern_kopf()).json()["offen"] is False
            assert c.get("/api/kern/tickets", headers=kern_kopf()).json()["gesamt"] == 0
            assert '"betreuer"' not in (protokoll.read_text(encoding="utf-8") if protokoll.exists() else "")  # das Protokoll des Labortags ist weg
            assert not (konfig.daten / "protokoll" / "betreuer.jsonl").exists()  # auch die Betreuer-Spur


def test_aufbewahrung_loescht_auch_das_protokoll_und_die_tokens(konfig):
    uhr = Uhr(_tag(2026, 11, 28))
    app = create_app(konfig, uhr=uhr)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        oeffnen(c, 3)
        c.post("/api/betreuer/plaetze/3/platz-token", headers=betreuer_kopf())
    assert (konfig.daten / "protokoll" / "dienst.jsonl").stat().st_size > 0
    uhr.t = _tag(2026, 12, 12)
    assert app.state.dienst.aufbewahrung_pruefen() is True
    assert not (konfig.daten / "protokoll" / "dienst.jsonl").exists() and not (konfig.daten / "protokoll" / "betreuer.jsonl").exists()
    assert app.state.dienst.zentral.hat_token(3) is False
    assert app.state.dienst.aufbewahrung_pruefen() is True  # wiederholbar


def test_aufbewahrung_bis_ist_einstellbar(konfig, uhr):
    k = Konfig(daten=konfig.daten, kern_token=KERN_TOKEN, betreuer_token=BETREUER_TOKEN, bis=date(2026, 11, 27))
    app = create_app(k, uhr=uhr)  # der Labortag liegt nach dem Datum: alles wird geleert
    assert app.state.dienst.aufbewahrung_pruefen() is True


def test_einstieg_gesundheit_ohne_dienst_ist_ungleich_null():
    r = subprocess.run([sys.executable, "-m", "wamo_labor_dienst", "gesundheit"], cwd=PAKET_ORDNER, capture_output=True, text=True, timeout=30,
                       env={"PATH": "", "WAMO_LABOR_DIENST_PORT": "9", "SYSTEMROOT": "C:\\Windows"})
    assert r.returncode != 0


def test_einstieg_ohne_konfiguration_nennt_die_variable_und_startet_nicht():
    r = subprocess.run([sys.executable, "-m", "wamo_labor_dienst"], cwd=PAKET_ORDNER, capture_output=True, text=True, timeout=30,
                       env={"PATH": "", "SYSTEMROOT": "C:\\Windows"})
    assert r.returncode == 2 and "WAMO_LABOR_DIENST_KERN_TOKEN_DATEI" in r.stderr and "Traceback" not in r.stderr


def test_einstieg_mit_unbrauchbarem_datenordner_meldet_klar_und_startet_nicht(tmp_path):
    datei = tmp_path / "ist-eine-datei"
    datei.write_text("x", encoding="utf-8")
    kern, betreuer = tmp_path / "kern.token", tmp_path / "betreuer.token"
    kern.write_text(KERN_TOKEN, encoding="utf-8")
    betreuer.write_text(BETREUER_TOKEN, encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "wamo_labor_dienst"], cwd=PAKET_ORDNER, capture_output=True, text=True, timeout=30,
                       env={"PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "WAMO_LABOR_DIENST_DATEN": str(datei), "WAMO_LABOR_DIENST_KERN_TOKEN_DATEI": str(kern),
                            "WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI": str(betreuer)})
    assert r.returncode == 3 and "Speicher" in r.stderr and "Traceback" not in r.stderr
    assert KERN_TOKEN not in r.stdout + r.stderr and BETREUER_TOKEN not in r.stdout + r.stderr


def test_stuendliche_pruefung_laeuft_nach_einem_fehler_weiter(app, monkeypatch):
    import asyncio

    from wamo_labor_dienst.app import _aufraeumen_schleife

    dienst = app.state.dienst
    aufrufe: list[int] = []

    def pruefen() -> bool:
        aufrufe.append(1)
        if len(aufrufe) == 1:
            raise RuntimeError("Fehler beim Aufräumen")
        return False

    pausen: list[float] = []

    async def schlafen(sekunden: float) -> None:
        pausen.append(sekunden)
        if len(pausen) > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(dienst, "aufbewahrung_pruefen", pruefen)
    monkeypatch.setattr("wamo_labor_dienst.app.asyncio.sleep", schlafen)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_aufraeumen_schleife(dienst))
    assert len(aufrufe) == 2 and pausen == [3600, 3600, 3600]
    assert "RuntimeError" in (konfig_protokoll(dienst))


def konfig_protokoll(dienst) -> str:
    dienst.protokoll.abwarten()
    return dienst.protokoll.datei.read_text(encoding="utf-8")


def test_zentraldatei_unbrauchbar_ist_nicht_gesund(tmp_path):
    from wamo_labor_dienst.datenbank import ZentralDB

    assert ZentralDB(tmp_path).pruefen() is False  # ein Ordner statt einer Datei
