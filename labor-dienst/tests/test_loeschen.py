"""Löschweg mit Nachprüfung (Befund 4 des Sicherheitsreviews): Wird der Erfolg gemeldet, steht der alte Text in keiner Datei mehr."""
from __future__ import annotations

import sqlite3

import pytest
from hilfen import anlegen, betreuer_kopf, inhalt, kommentieren, oeffnen

from wamo_labor_dienst import datenbank
from wamo_labor_dienst.datenbank import NichtBereinigt, PlatzDB

MARKE = "MARKE-GEHEIM-4711-Ärger"
PLATZ = 3


@pytest.fixture(autouse=True)
def _temp_im_testordner(tmp_path, monkeypatch):
    """Jede Temp-Datei von SQLite landet im Testordner und wird vom Markertest mit geprüft."""
    temp = tmp_path / "sqlite-temp"
    temp.mkdir()
    for name in ("TMP", "TEMP", "TMPDIR", "SQLITE_TMPDIR"):
        monkeypatch.setenv(name, str(temp))


@pytest.fixture(autouse=True)
def _kurze_wartezeiten(monkeypatch):
    monkeypatch.setattr(datenbank, "VERDICHTEN_PAUSE_SECS", 0.01)
    monkeypatch.setattr(datenbank, "VERDICHTEN_VERSUCHE", 5)


def _markierte_daten(db: PlatzDB) -> None:
    nr = int(inhalt(anlegen(db, titel=f"Titel {MARKE}", beschreibung=f"Text {MARKE}" * 20))["nummer"][4:])
    kommentieren(db, nr, 1, f"Kommentar {MARKE}")
    for i in range(10):
        anlegen(db, titel=f"Weitere {i} {MARKE}", beschreibung=MARKE * 30)


def _dateien_mit_marke(ordner) -> list[str]:
    treffer = []
    for datei in ordner.rglob("*"):
        if datei.is_file() and MARKE.encode() in datei.read_bytes():
            treffer.append(datei.name)
    return treffer


def _leser(pfad) -> sqlite3.Connection:
    """Ein offener Lesesnapshot: Er hindert das Zurückschreiben und Leeren der WAL-Datei."""
    r = sqlite3.connect(pfad, isolation_level=None)
    r.execute("BEGIN")
    r.execute("SELECT COUNT(*) FROM ticket").fetchone()
    return r


def test_ohne_leser_steht_der_alte_text_in_keiner_datei_mehr(db, tmp_path):
    _markierte_daten(db)
    assert _dateien_mit_marke(tmp_path)  # Vorbedingung: Der Text steht in den Dateien
    db.daten_loeschen()
    assert _dateien_mit_marke(tmp_path) == []
    wal = db.pfad.with_name(db.pfad.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def test_mit_offenem_lesesnapshot_wird_nicht_erfolg_gemeldet_sondern_ein_fehler(db, tmp_path):
    _markierte_daten(db)
    leser = _leser(db.pfad)
    try:
        with pytest.raises(NichtBereinigt):
            db.daten_loeschen()
        assert db.zustand()["tickets"] == 0  # logisch ist alles gelöscht, die Datei ist aber noch nicht bereinigt
    finally:
        leser.close()
    db.daten_loeschen()  # der Betreuer wiederholt: jetzt gelingt es
    assert _dateien_mit_marke(tmp_path) == []


def test_aufruf_ueber_die_schnittstelle_ist_503_bis_die_bereinigung_gelingt(client, konfig):
    d = oeffnen(client, PLATZ)
    dienst = client.app.state.dienst
    _markierte_daten(dienst.plaetze[PLATZ])
    leser = _leser(dienst.plaetze[PLATZ].pfad)
    try:
        r = client.post(f"/api/betreuer/plaetze/{PLATZ}/daten-loeschen", headers=betreuer_kopf())
        assert r.status_code == 503 and r.json()["fehler"] == "dienst_fehler" and MARKE not in r.text
        assert "bereinigt" in r.json()["text"]
    finally:
        leser.close()
    r = client.post(f"/api/betreuer/plaetze/{PLATZ}/daten-loeschen", headers=betreuer_kopf())
    assert r.status_code == 200 and r.json()["tickets"] == 0 and d != r.json()["durchlauf_id"]
    assert _dateien_mit_marke(konfig.daten) == []


def test_aufbewahrung_meldet_nie_erfolg_wenn_die_bereinigung_scheiterte_und_wiederholt_sie(app, konfig, uhr):
    from datetime import UTC, datetime

    dienst = app.state.dienst
    dienst.plaetze[PLATZ].neuer_durchlauf(1)
    _markierte_daten(dienst.plaetze[PLATZ])
    leser = _leser(dienst.plaetze[PLATZ].pfad)
    uhr.vor(datetime(2026, 12, 12, 12, tzinfo=UTC).timestamp() - uhr.t)
    try:
        assert dienst.aufbewahrung_pruefen() is False  # nicht vollständig bereinigt: keine Erfolgsmeldung
    finally:
        leser.close()
    assert dienst.aufbewahrung_pruefen() is True
    assert _dateien_mit_marke(konfig.daten) == []


def test_wiederholtes_loeschen_ist_unschaedlich(db):
    db.daten_loeschen()
    db.daten_loeschen()
    assert db.zustand()["tickets"] == 0 and db.durchlauf()["offen"] is False


def test_checkpoint_ergebnis_wird_ausgewertet_nicht_nur_ausgefuehrt(db, monkeypatch):
    """Meldet SQLite busy, ist das Löschen nicht gelungen, auch wenn kein Fehler geworfen wird."""
    echte = datenbank.verbinden

    class Huelle:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a):
            if sql.startswith("PRAGMA wal_checkpoint"):
                return self._c.execute("SELECT 1, 10, 3")  # busy=1: nicht vollständig zurückgeschrieben
            return self._c.execute(sql, *a)

        def close(self):
            self._c.close()

    monkeypatch.setattr(datenbank, "verbinden", lambda *a, **k: Huelle(echte(*a, **k)))
    with pytest.raises(NichtBereinigt):
        db.daten_loeschen()
