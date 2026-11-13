"""Tests für scripts/wamo_labor_last_messung.py (Rohdaten eines Browser-Schritts in Kennzahlen umrechnen).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q test_wamo_labor_last_messung.py

Reine Rechnung mit erfundenen Zeitverläufen (Sekunden der Audio-Uhr des Browsers), kein Browser, kein Netz.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_last_messung as ms  # noqa: E402
import wamo_labor_last_plan as pl  # noqa: E402

PLAN_G3 = pl.SchrittPlan("G3", "fakt_1", 10.0)
PLAN_A1 = pl.SchrittPlan("A1", "a1", 10.0, einwurf=True, einwurf_clip="einwurf_a")


def bloecke(von: float, bis: float, schritt: float = 0.04) -> list[list[float]]:
    """Aktive Blöcke wie der Aufnahme-Worklet sie meldet (je 40 ms)."""
    n = int(round((bis - von) / schritt))
    return [[round(von + i * schritt, 3), round(von + (i + 1) * schritt, 3)] for i in range(n)]


def roh(**kw) -> dict:
    basis = {"clip": "fakt_1", "clip_t0": 100.0, "clip_ende": 102.0, "bot": [], "einwurf": None, "ende_t": 120.0, "abbruch": "",
             "zeitueberschreitung": False, "geschnitten": 0, "r429": 0, "freigabe": "", "konsole": 0}
    basis.update(kw)
    return basis


def auswerten(r: dict, plan=PLAN_G3, beginn=5.0):
    return ms.schritt_auswerten(r, plan, sitzung=2, kennung="labor-03", beginn_s=beginn)


# ---------- Segmente ----------

def test_segmente_verbinden_luecken_unter_0_8_sekunden():
    segs = ms.segmente([[1.0, 1.04], [1.5, 1.54], [3.0, 3.04]])
    assert segs == [(1.0, 1.54), (3.0, 3.04)]


def test_segmente_sortieren_und_ignorieren_unsinn():
    assert ms.segmente([[3.0, 3.04], [1.0, 1.04]]) == [(1.0, 1.04), (3.0, 3.04)]
    assert ms.segmente([]) == []
    assert ms.segmente([[2.0, 1.0], ["x", 1], [1.0], 5]) == []


# ---------- erster Ton ----------

def test_erster_ton_ist_der_abstand_vom_ende_der_sprechprobe():
    e = auswerten(roh(bot=bloecke(103.2, 108.0)))
    assert e.erster_ton_ms == pytest.approx(1200.0, abs=1.0)
    assert e.antwort_s == pytest.approx(4.8, abs=0.1)
    assert e.karte == "G3" and e.sitzung == 2 and e.kennung == "labor-03" and e.beginn_s == 5.0


def test_ein_ton_vor_dem_ende_der_frage_zaehlt_nicht_als_antwort():
    e = auswerten(roh(bot=bloecke(99.0, 101.5)))  # lief schon vor der Frage und endete davor
    assert e.erster_ton_ms is None


def test_ein_ton_kurz_vor_dem_ende_der_frage_zaehlt_mit_null():
    e = auswerten(roh(bot=bloecke(101.97, 106.0)))  # Beginn 30 ms vor dem Ende, innerhalb der Toleranz von 50 ms
    assert e.erster_ton_ms == pytest.approx(0.0, abs=1.0) or e.erster_ton_ms < 0


def test_ohne_antwort_gibt_es_keinen_ton_und_keine_antwortdauer():
    e = auswerten(roh(zeitueberschreitung=True))
    assert e.erster_ton_ms is None and e.antwort_s is None and e.zeitueberschreitung is True and e.abbruch == ""


def test_antwort_endet_bei_einer_stille_ab_2_5_sekunden():
    e = auswerten(roh(bot=bloecke(103.0, 105.0) + bloecke(108.0, 110.0)))
    assert e.antwort_s == pytest.approx(2.0, abs=0.1)


# ---------- Einwurf und Verstummen ----------

def mit_einwurf(onset=106.0, bot_bis=106.9, wieder=None):
    teile = bloecke(103.0, bot_bis)
    if wieder:
        teile += bloecke(*wieder)
    return roh(clip="a1", clip_ende=102.0, bot=teile, einwurf={"clip": "einwurf_a", "t0": onset, "ende": onset + 2.5, "bot_seit": 3.1})


def test_verstummen_ist_die_zeit_vom_einwurf_bis_zum_ende_der_wiedergabe():
    e = auswerten(mit_einwurf(onset=106.0, bot_bis=106.8), PLAN_A1)
    assert e.einwurf and e.einwurf_ausgeloest
    assert e.verstummen_ms == pytest.approx(800.0, abs=60.0)
    assert e.verpasst is False


def test_weiterreden_nach_dem_einwurf_ist_verpasst():
    e = auswerten(mit_einwurf(onset=106.0, bot_bis=112.0), PLAN_A1)
    assert e.verpasst is True and e.verstummen_ms is not None and e.verstummen_ms > ms.VERPASST_NACH_S * 1000


def test_nie_verstummt_ist_verpasst_ohne_zeit():
    r = mit_einwurf(onset=106.0, bot_bis=140.0)
    r["ende_t"] = 140.2  # die Aufnahme endet, während WAMO noch spricht
    e = auswerten(r, PLAN_A1)
    assert e.verpasst is True and e.verstummen_ms is None


def test_geplanter_aber_nicht_gespielter_einwurf_wird_gemeldet():
    e = auswerten(roh(clip="a1", bot=bloecke(103.0, 104.0)), PLAN_A1)
    assert e.einwurf is True and e.einwurf_ausgeloest is False and e.verstummen_ms is None and e.verpasst is False


def test_ohne_geplanten_einwurf_gibt_es_kein_verstummen():
    e = auswerten(roh(bot=bloecke(103.0, 105.0)))
    assert e.einwurf is False and e.einwurf_ausgeloest is False and e.verstummen_ms is None


# ---------- falsche Unterbrechungen, Abbrüche, Sonstiges ----------

@pytest.mark.parametrize("geschnitten,einwurf,erwartet", [(0, False, 0), (2, False, 2), (1, True, 0), (3, True, 2)])
def test_falsche_unterbrechungen_sind_die_nicht_erklaerten_schnitte(geschnitten, einwurf, erwartet):
    r = mit_einwurf(bot_bis=106.8) if einwurf else roh(bot=bloecke(103.0, 105.0))
    r["geschnitten"] = geschnitten
    assert auswerten(r, PLAN_A1 if einwurf else PLAN_G3).falsche_unterbrechungen == erwartet


def test_abbruch_wird_mit_dem_grund_uebernommen_und_nur_kurz():
    e = auswerten(roh(abbruch="Gespräch beendet" + "x" * 200))
    assert e.abbruch.startswith("Gespräch beendet") and len(e.abbruch) <= 80


def test_freigabe_und_konsole_gehen_in_das_ergebnis():
    e = auswerten(roh(freigabe="abgelehnt"))
    assert e.freigabe_geklickt is False  # abgelehnt ist kein Klick auf Freigeben
    assert auswerten(roh(freigabe="freigegeben")).freigabe_geklickt is True


def test_zaehler_des_browsers_werden_summiert():
    a, b = roh(r429=1, konsole=2), roh(r429=0, konsole=3)
    assert ms.browser_zaehler([a, b]) == (1, 5)


@pytest.mark.parametrize("kaputt", [None, [], "x", {"bot": "nein"}, {"clip_ende": "x", "bot": []}, {"clip_ende": 1.0}])
def test_unbrauchbare_rohdaten_werden_abgelehnt(kaputt):
    with pytest.raises(pl.EingabeFehler, match="Rohdaten"):
        ms.schritt_auswerten(kaputt, PLAN_G3, 0, "labor-01", 0.0)
