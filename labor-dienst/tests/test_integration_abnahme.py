"""Das Abnahmeskript von WAMO (scripts/wamo_labor_abnahme.py) gegen den echten Dienst auf Loopback (HTTP, uvicorn).

Die Uhr des Dienstes ist die falsche Uhr des Skripts: Wartezeiten der Schreibrate kosten nichts, und beide sehen dieselbe Zeit. Dadurch läuft
auch A4 (Quoten) vollständig in Sekunden. Mit WAMO_LABOR_ECHTZEIT=1 läuft die Abnahme zusätzlich mit echter Uhr (20 bis 25 Minuten, Test
test_abnahme_in_echtzeit); das Ergebnis dieses Laufs steht in der Lieferung.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx
import pytest
from echter_dienst import abnahmeplaetze_vorbereiten, laufender_dienst
from hilfen import BETREUER_TOKEN, KERN_TOKEN, LABORTAG

import wamo_labor_abnahme as ab
import wamo_labor_abnahme_basis as basis
from wamo_labor_dienst.app import create_app
from wamo_labor_dienst.konfig import Konfig

AUTOMATISCH = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A11")
SKRIPT = Path(ab.__file__)


def _optionen(adresse: str, platz_token: str) -> basis.Optionen:
    """Board-Seite: die Köpfe aller Antworten des Dienstes (die Seite selbst baut der zweite Auftrag). Platzpfad: Der Dienst kennt keinen Pfad mit
    Platznummer (D6), die Probe ist deshalb ein Pfad, den es nicht gibt (404). Betreuer-Pfade: lesend und schreibend mit dem Platz-Token (403)."""
    return basis.Optionen(board_url=f"{adresse}/gesundheit", platz_token=platz_token,
                          betreuer_pfade=("GET /api/betreuer/plaetze", "POST /api/betreuer/plaetze/15/zuruecksetzen", "POST /api/betreuer/plaetze/16/daten-loeschen"),
                          platz_pfade=("/api/browser/plaetze/{platz}/board",), nicht_get_frei=True)


async def _abnahme(adresse: str, takt, optionen: basis.Optionen, auswahl: list[str] | None = None) -> ab.LaufErgebnis:
    client = httpx.AsyncClient(base_url=adresse, follow_redirects=False, trust_env=False, timeout=httpx.Timeout(8.0, connect=3.0))
    async with client:
        return await ab.lauf(basis.Zugang(client, KERN_TOKEN, takt, basis=adresse), optionen, auswahl)


def _meldungen(lauf: ab.LaufErgebnis) -> str:
    return "\n".join(f"{e.nr} {e.status}: {e.meldung}" for e in lauf.ergebnisse)


@pytest.fixture
def dienst(tmp_path):
    takt = basis.FalscherTakt(LABORTAG)
    konfig = Konfig(daten=tmp_path / "daten", kern_token=KERN_TOKEN, betreuer_token=BETREUER_TOKEN)
    with laufender_dienst(create_app(konfig, uhr=takt.jetzt)) as adresse:
        yield adresse, takt, abnahmeplaetze_vorbereiten(adresse)


def test_alle_automatischen_pruefungen_bestehen(dienst):
    adresse, takt, platz_token = dienst
    lauf = asyncio.run(_abnahme(adresse, takt, _optionen(adresse, platz_token)))
    assert lauf.abbruch is None, lauf.abbruch
    for nr in AUTOMATISCH:
        assert lauf.nach_nr[nr].status == basis.BESTANDEN, f"{nr}: {lauf.nach_nr[nr].meldung}\n{_meldungen(lauf)}"
    assert lauf.nach_nr["A10"].status == basis.VON_HAND
    assert ab.exit_code(lauf) == ab.EXIT_OK, _meldungen(lauf)


def test_a4_quoten_greifen_genau_an_der_grenze_des_vertrags(dienst):
    adresse, takt, platz_token = dienst
    lauf = asyncio.run(_abnahme(adresse, takt, _optionen(adresse, platz_token), ["A4"]))
    a4 = lauf.nach_nr["A4"]
    assert a4.status == basis.BESTANDEN, a4.meldung
    for stueck in ("Schreibrate: 429 bei 30", "Tickets: 429 bei 100", "Kommentare auf Platz 16"):
        assert stueck in a4.meldung, a4.meldung


def test_skript_als_programm_gegen_den_dienst_mit_auswahl(dienst, tmp_path):
    adresse, takt, platz_token = dienst
    datei = tmp_path / "kern.token"
    datei.write_text(KERN_TOKEN, encoding="utf-8")
    env = {**os.environ, "WAMO_LABOR_PLATZ_TOKEN": platz_token}
    env.pop("WAMO_LABOR_TOKEN", None)
    r = subprocess.run([sys.executable, str(SKRIPT), adresse, "--ja", "--token-datei", str(datei), "--auswahl", "A2", "A3", "A5", "A7", "A9"],
                       capture_output=True, text=True, encoding="utf-8", env=env, timeout=600)
    assert r.returncode == ab.EXIT_TEILLAUF, r.stdout + r.stderr  # ein Teillauf gilt nie als bestanden
    assert "Zusammenfassung: 5 bestanden, 0 durchgefallen" in r.stdout and "Teillauf" in r.stdout, r.stdout
    assert KERN_TOKEN not in r.stdout + r.stderr and platz_token not in r.stdout + r.stderr


def test_abnahme_nach_dem_zuruecksetzen_wiederholbar(dienst):
    """Die Betreuer setzen die Plätze nach einem Lauf zurück: Ein zweiter Lauf besteht wieder (Ausgangsstand wie beim ersten)."""
    adresse, takt, platz_token = dienst
    optionen = _optionen(adresse, platz_token)
    erst = asyncio.run(_abnahme(adresse, takt, optionen, ["A2", "A3", "A6", "A8"]))
    assert erst.nach_nr["A8"].status == basis.BESTANDEN, _meldungen(erst)
    with pytest.raises(basis.LaufAbbruch, match="benutzt"):  # ein benutzter Platz wird nicht noch einmal geprüft
        asyncio.run(_abnahme(adresse, takt, optionen, ["A2"]))
    abnahmeplaetze_vorbereiten(adresse)
    zweit = asyncio.run(_abnahme(adresse, takt, optionen, ["A2", "A3", "A6", "A8"]))
    assert all(zweit.nach_nr[nr].status == basis.BESTANDEN for nr in ("A2", "A3", "A6", "A8")), _meldungen(zweit)


@pytest.mark.langsam
@pytest.mark.skipif(os.environ.get("WAMO_LABOR_ECHTZEIT") != "1", reason="läuft nur mit WAMO_LABOR_ECHTZEIT=1 (20 bis 25 Minuten)")
def test_abnahme_in_echtzeit(tmp_path):
    takt = basis.EchterTakt()
    konfig = Konfig(daten=tmp_path / "daten", kern_token=KERN_TOKEN, betreuer_token=BETREUER_TOKEN, bis=date(2099, 1, 1))
    with laufender_dienst(create_app(konfig)) as adresse:
        platz_token = abnahmeplaetze_vorbereiten(adresse)
        start = time.monotonic()
        lauf = asyncio.run(_abnahme(adresse, takt, _optionen(adresse, platz_token)))
        print(f"Echtzeit-Abnahme in {time.monotonic() - start:.0f} s:\n{ab.tabelle(lauf, adresse)}")
    assert ab.exit_code(lauf) == ab.EXIT_OK, _meldungen(lauf)
