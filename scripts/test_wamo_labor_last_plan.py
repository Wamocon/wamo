"""Tests für scripts/wamo_labor_last_plan.py (Plan, Grenzen, Kosten und Schrittfolge des Lasttests).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q test_wamo_labor_last_plan.py

Reine Rechnung, kein Netz, kein Prozess, keine Datei.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_last_plan as pl  # noqa: E402


def namen(stufen):
    return [(s.sitzungen, s.minuten, s.snr_db) for s in stufen]


# ---------- Stufen ----------

def test_standardstufen_sind_1_2_4_8_je_15_minuten_ohne_murmeln():
    stufen = pl.stufen_bauen([1, 2, 4, 8], 15, [])
    assert namen(stufen) == [(1, 15, None), (2, 15, None), (4, 15, None), (8, 15, None)]
    assert [s.nummer for s in stufen] == [1, 2, 3, 4]


def test_murmeln_haengt_stufen_mit_acht_sitzungen_in_der_genannten_reihenfolge_an():
    stufen = pl.stufen_bauen([1, 8], 10, [20, 10, 5])
    assert namen(stufen) == [(1, 10, None), (8, 10, None), (8, 10, 20), (8, 10, 10), (8, 10, 5)]
    assert "Murmeln 10 dB" in stufen[3].name and "Murmeln" not in stufen[0].name


def test_sitzungen_werden_aufsteigend_sortiert():
    assert [s.sitzungen for s in pl.stufen_bauen([8, 1, 4], 15, [])] == [1, 4, 8]


@pytest.mark.parametrize("sitzungen", [[3], [9], [0], [16], [1, 1], [], [-1]])
def test_unzulaessige_sitzungszahlen_werden_abgelehnt(sitzungen):
    with pytest.raises(pl.EingabeFehler):
        pl.stufen_bauen(sitzungen, 15, [])


@pytest.mark.parametrize("minuten", [0, -5, 21, 120])
def test_stufendauer_ist_auf_zwanzig_minuten_begrenzt(minuten):
    with pytest.raises(pl.EingabeFehler, match="Minuten"):
        pl.stufen_bauen([1], minuten, [])


def test_zwanzig_minuten_sind_erlaubt():
    assert pl.stufen_bauen([8], 20, [])[0].minuten == 20


@pytest.mark.parametrize("snr", [[-1], [41], [10, 10]])
def test_unzulaessige_abstaende_werden_abgelehnt(snr):
    with pytest.raises(pl.EingabeFehler):
        pl.stufen_bauen([8], 15, snr)


def test_stufenzahl_ist_begrenzt_damit_kein_dauerlauf_entsteht():
    with pytest.raises(pl.EingabeFehler, match="Stufen"):
        pl.stufen_bauen([1, 2, 4, 8], 15, list(range(0, 12)))


# ---------- Profile und Plätze ----------

def test_profile_sind_die_ersten_n_laborprofile():
    assert pl.profile_fuer(1) == ("labor-01",)
    assert pl.profile_fuer(8) == tuple(f"labor-0{n}" for n in range(1, 9))


def test_mehr_als_acht_profile_gibt_es_nicht():
    with pytest.raises(pl.EingabeFehler):
        pl.profile_fuer(9)


@pytest.mark.parametrize("kennung,platz", [("labor-01", 1), ("labor-08", 8)])
def test_platznummer_aus_der_kennung(kennung, platz):
    assert pl.platz_von(kennung) == platz


@pytest.mark.parametrize("kennung", ["labor-09", "labor-schau", "labor-00", "x", "labor-1"])
def test_reserve_und_schaufenster_sind_kein_lastprofil(kennung):
    with pytest.raises(pl.EingabeFehler):
        pl.platz_von(kennung)


@pytest.mark.parametrize("name,gut", [("last-2026-11-24", True), ("a", True), ("", False), ("../x", False), ("a b", False), ("x" * 41, False)])
def test_laufname_ist_nie_ein_pfad(name, gut):
    if gut:
        assert pl.lauf_pruefen(name) == name
    else:
        with pytest.raises(pl.EingabeFehler):
            pl.lauf_pruefen(name)


# ---------- Schrittfolge ----------

def erste(n, seed=1, stufe=1, sitzung=0, jede=pl.EINWURF_JEDE):
    folge = pl.schritt_folge(seed, stufe, sitzung, jede)
    return [next(folge) for _ in range(n)]


def test_folge_ist_mit_festem_seed_wiederholbar():
    assert erste(40, seed=7) == erste(40, seed=7)


def test_anderer_seed_oder_andere_sitzung_ergibt_andere_pausen():
    assert [s.pause_s for s in erste(20, seed=7)] != [s.pause_s for s in erste(20, seed=8)]
    assert [s.pause_s for s in erste(20, sitzung=0)] != [s.pause_s for s in erste(20, sitzung=1)]
    assert [s.pause_s for s in erste(20, stufe=1)] != [s.pause_s for s in erste(20, stufe=2)]


def test_denkpausen_liegen_zwischen_acht_und_zwanzig_sekunden():
    pausen = [s.pause_s for s in erste(300)]
    assert min(pausen) >= 8.0 and max(pausen) <= 20.0
    assert max(pausen) - min(pausen) > 8  # sie streuen wirklich
    assert all(round(p, 1) == p for p in pausen)


def test_kette_wechselt_a1_j1_g3_und_beginnt_je_sitzung_versetzt():
    assert [s.karte for s in erste(6, sitzung=0)] == ["A1", "J1", "G3", "A1", "J1", "G3"]
    assert [s.karte for s in erste(3, sitzung=1)] == ["J1", "G3", "A1"]
    assert [s.karte for s in erste(3, sitzung=2)] == ["G3", "A1", "J1"]
    assert [s.karte for s in erste(3, sitzung=3)] == ["A1", "J1", "G3"]


def test_sprechproben_je_karte():
    schritte = erste(24)
    assert {s.clip for s in schritte if s.karte == "A1"} == {"a1"}
    assert {s.clip for s in schritte if s.karte == "J1"} == {"j1"}
    g3 = [s.clip for s in schritte if s.karte == "G3"]
    assert g3 == [f"fakt_{n}" for n in range(1, 9)]  # acht Fragen, danach beginnt es von vorn
    assert [s.clip for s in erste(27) if s.karte == "G3"][8] == "fakt_1"


def test_einwurf_nur_bei_a1_und_jede_dritte_a1_runde():
    schritte = erste(30)
    a1 = [s for s in schritte if s.karte == "A1"]
    assert [s.einwurf for s in a1] == [False, False, True] * 3 + [False]
    assert not any(s.einwurf for s in schritte if s.karte != "A1")
    assert {s.einwurf_clip for s in a1 if s.einwurf} <= {"einwurf_a", "einwurf_b"}
    assert all(s.einwurf_clip == "" for s in schritte if not s.einwurf)


def test_einwurf_laesst_sich_abschalten():
    assert not any(s.einwurf for s in erste(60, jede=0))


# ---------- Kosten und Dauer ----------

def test_zuege_je_sitzung_aus_den_annahmen():
    a = pl.Annahmen()
    mittel = (pl.DENKPAUSE_MIN_S + pl.DENKPAUSE_MAX_S) / 2 + a.sprechzeit_s + a.antwortzeit_s
    assert pl.zuege_je_sitzung(15, a) == int(900 // mittel)
    assert pl.zuege_je_sitzung(1, a) >= 1


def test_kosten_sind_ohne_preise_nur_tokens_und_anfragen():
    stufen = pl.stufen_bauen([1, 2], 15, [])
    k = pl.kosten_schaetzen(stufen)
    zuege = pl.zuege_je_sitzung(15, pl.Annahmen()) * 3
    assert k.zuege == zuege
    assert k.eingabe_tokens == zuege * pl.Annahmen().eingabe_tokens_je_zug
    assert k.ausgabe_tokens == zuege * pl.Annahmen().ausgabe_tokens_je_zug
    assert k.anfragen == pytest.approx(zuege * pl.Annahmen().anfragen_je_zug)
    assert k.usd is None


def test_kosten_mit_preisen_in_dollar_je_million_tokens():
    stufen = pl.stufen_bauen([1], 15, [])
    k = pl.kosten_schaetzen(stufen, preis_ein_usd_mio=2.0, preis_aus_usd_mio=10.0)
    erwartet = k.eingabe_tokens / 1e6 * 2.0 + k.ausgabe_tokens / 1e6 * 10.0
    assert k.usd == pytest.approx(erwartet)


def test_ein_preis_allein_reicht_nicht():
    with pytest.raises(pl.EingabeFehler, match="Preis"):
        pl.kosten_schaetzen(pl.stufen_bauen([1], 15, []), preis_ein_usd_mio=2.0)


def test_kostenobergrenze_lehnt_zu_teuren_plan_ab():
    k = pl.kosten_schaetzen(pl.stufen_bauen([8], 15, []), preis_ein_usd_mio=3.0, preis_aus_usd_mio=15.0)
    with pytest.raises(pl.EingabeFehler, match="Kostenobergrenze"):
        pl.kosten_pruefen(k, 1.0)
    pl.kosten_pruefen(k, k.usd + 1)


def test_kostenobergrenze_ohne_preise_ist_ein_fehler():
    k = pl.kosten_schaetzen(pl.stufen_bauen([1], 15, []))
    with pytest.raises(pl.EingabeFehler, match="Preise"):
        pl.kosten_pruefen(k, 5.0)
    pl.kosten_pruefen(k, None)


def test_dauer_enthaelt_stufen_pausen_vorbereitung_und_aufraeumen():
    stufen = pl.stufen_bauen([1, 2], 15, [])
    sekunden = pl.dauer_schaetzen(stufen)
    assert sekunden >= 2 * 900 + pl.ZWISCHEN_STUFEN_S + pl.VORBEREITUNG_S + pl.AUFRAEUMEN_S
    assert pl.dauer_schaetzen(pl.stufen_bauen([1, 2, 4], 15, [])) > sekunden


def test_hoechstgrenzen_stehen_im_code_wie_im_auftrag():
    assert pl.MAX_SITZUNGEN == 8 and pl.MAX_STUFE_MINUTEN == 20
    assert pl.MAX_ABBRUECHE == 2
    assert pl.ABBRUCH_SPEICHER_BYTES == 3 * 1024 ** 3
    assert pl.BESTAETIGUNG == "LASTTEST"
    assert pl.P95_ERSTER_TON_MS == 3500 and pl.FAKTOR_ZU_EINER_SITZUNG == 1.5
    assert pl.CPU_MAX_PROZENT == 70.0 and pl.SPEICHER_MAX_BYTES == int(2.8 * 1024 ** 3)
