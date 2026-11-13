"""Tests für scripts/wamo_labor_last_auswertung.py (Kennzahlen, Bestehen je Kriterium, JSON und Bericht des Lasttests).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q test_wamo_labor_last_auswertung.py

Reine Rechnung mit erfundenen Messwerten, keine Datei, kein Netz.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_last_auswertung as aw  # noqa: E402
import wamo_labor_last_plan as pl  # noqa: E402
import wamo_labor_last_server as sv  # noqa: E402

GIB = 1024 ** 3


def schritt(karte="G3", ton=1000.0, sitzung=0, **kw) -> aw.SchrittErgebnis:
    basis = dict(sitzung=sitzung, kennung=f"labor-0{sitzung + 1}", karte=karte, beginn_s=0.0, erster_ton_ms=ton, antwort_s=5.0)
    basis.update(kw)
    return aw.SchrittErgebnis(**basis)


def journal(**kw) -> sv.JournalZaehlung:
    basis = dict(zeilen=10, r429=0, soniox=0, anfragen=4, unterbrechungen=0, pipeline_fehler=0, token_zuege=0, token_eingabe=0,
                 token_cache_lesen=0, token_cache_schreiben=0, abgeschnitten=False, minuten={})
    basis.update(kw)
    return sv.JournalZaehlung(**basis)


def probe(zeit, cpu=None, speicher=1 * GIB, spitze=None, aufgaben=30, jr=None, lesbar=True) -> sv.Messung:
    return sv.Messung(zeit, True, 20, cpu, speicher, spitze, aufgaben, lesbar if jr is not None else None, jr, 0.0)


# ---------- Kennzahlen ----------

def test_quantil_nach_dem_naechsten_rang():
    werte = list(range(1, 21))
    assert aw.quantil(werte, 0.5) == 10 and aw.quantil(werte, 0.95) == 19 and aw.quantil(werte, 1.0) == 20
    assert aw.quantil([7], 0.95) == 7 and aw.quantil([], 0.5) is None
    assert aw.quantil([3, 1, 2], 0.5) == 2  # unsortiert


def test_kennzahlen_lassen_fehlende_werte_weg():
    k = aw.kennzahlen([100.0, None, 300.0, 200.0])
    assert k == {"n": 3, "p50": 200.0, "p95": 300.0, "max": 300.0}
    assert aw.kennzahlen([None]) == {"n": 0, "p50": None, "p95": None, "max": None}


def test_cpu_prozent_je_intervall_100_ist_ein_kern():
    proben = [probe(0.0, cpu=0), probe(10.0, cpu=5_000_000), probe(20.0, cpu=20_000_000)]
    assert aw.cpu_prozente(proben) == [pytest.approx(50.0), pytest.approx(150.0)]


def test_cpu_ueberspringt_luecken_ruecksetzer_und_stillstand_der_uhr():
    proben = [probe(0.0, cpu=100), probe(10.0, cpu=None), probe(20.0, cpu=50), probe(20.0, cpu=70), probe(30.0, cpu=20_000_070)]
    assert aw.cpu_prozente(proben) == [pytest.approx(200.0)]


# ---------- Journal ----------

def test_journal_wird_ueber_die_proben_summiert_und_je_minute_zusammengefuehrt():
    proben = [probe(1, jr=journal(r429=0, soniox=1, anfragen=2, minuten={60: (2, 100)}, token_zuege=1, token_eingabe=100)),
              probe(2, jr=journal(r429=2, soniox=0, anfragen=3, minuten={60: (1, 50), 120: (2, 70)}, token_zuege=2, token_eingabe=150)),
              probe(3)]  # ohne Journal: zählt nicht mit
    j = aw.journal_summe(proben)
    assert j["lesbar"] is True and j["r429"] == 2 and j["soniox"] == 1 and j["anfragen"] == 5
    assert j["anfragen_je_minute_max"] == 3 and j["tokens_je_minute_max"] == 150 and j["token_zuege"] == 3
    assert j["token_eingabe"] == 250


def test_ein_unlesbares_journal_macht_die_zaehlung_unvollstaendig():
    proben = [probe(1, jr=journal(r429=0)), sv.Messung(2.0, True, 20, None, 1, None, 1, False, None, 0.0)]
    j = aw.journal_summe(proben)
    assert j["lesbar"] is False
    assert aw.journal_summe([probe(1)])["lesbar"] is None  # nie gefragt


def test_abgeschnittene_zaehlung_wird_gemeldet():
    assert aw.journal_summe([probe(1, jr=journal(abgeschnitten=True))])["abgeschnitten"] is True


# ---------- Stufe auswerten ----------

def stufe_acht():
    return pl.stufen_bauen([1, 8], 15, [10])


def test_stufe_auswerten_fasst_schritte_und_proben_zusammen():
    s = stufe_acht()[1]
    schritte = [schritt("A1", 1000), schritt("G3", 2000, sitzung=1), schritt("J1", None, sitzung=2),
                schritt("A1", 1500, verstummen_ms=900.0, einwurf=True, einwurf_ausgeloest=True),
                schritt("A1", 1800, verstummen_ms=None, einwurf=True, einwurf_ausgeloest=True, verpasst=True),
                schritt("G3", 1200, falsche_unterbrechungen=2, abbruch="offline"),
                schritt("G3", 1300, zeitueberschreitung=True)]
    proben = [probe(0.0, cpu=0, speicher=1 * GIB, spitze=2 * GIB, aufgaben=30),
              probe(10.0, cpu=5_000_000, speicher=2 * GIB, spitze=2 * GIB, aufgaben=45, jr=journal(r429=0, soniox=3))]
    d = aw.stufe_auswerten(s, schritte, proben, browser_429=0, konsolenfehler=4)
    assert d["sitzungen"] == 8 and d["schritte"] == 7 and d["ohne_antwort"] == 1
    assert d["erster_ton_ms"]["n"] == 6 and d["erster_ton_ms"]["max"] == 2000
    assert set(d["erster_ton_je_karte"]) == {"A1", "G3", "J1"}
    assert d["verstummen_ms"]["n"] == 1 and d["verstummen_verpasst"] == 1 and d["einwuerfe"] == 2
    assert d["falsche_unterbrechungen"] == 2 and d["abbrueche"] == 1 and d["zeitueberschreitungen"] == 1
    assert d["cpu_prozent"]["max"] == pytest.approx(50.0) and d["cpu_prozent"]["proben"] == 1
    assert d["speicher_max"] == 2 * GIB and d["speicher_spitze"] == 2 * GIB and d["aufgaben_max"] == 45
    assert d["journal"]["soniox"] == 3 and d["konsolenfehler"] == 4 and d["browser_429"] == 0
    json.dumps(d)  # JSON-fähig


def test_stufe_ohne_daten_hat_leere_kennzahlen_und_keine_zahlen_aus_dem_nichts():
    d = aw.stufe_auswerten(stufe_acht()[0], [], [], 0, 0)
    assert d["erster_ton_ms"]["n"] == 0 and d["speicher_max"] is None and d["cpu_prozent"]["proben"] == 0


# ---------- Kriterien ----------

def stufen_dict(sitzungen=1, snr=None, p95=1500.0, cpu=40.0, speicher=int(1.5 * GIB), r429=0, br429=0, abbrueche=0, lesbar=True,
                anfragen_min=10, tokens_min=100000, proben=3, n=10):
    return {"sitzungen": sitzungen, "snr_db": snr, "name": f"{sitzungen} Sitzungen",
            "erster_ton_ms": {"n": n, "p50": p95 / 2, "p95": p95, "max": p95},
            "abbrueche": abbrueche, "cpu_prozent": {"proben": proben, "mittel": cpu / 2, "p95": cpu, "max": cpu},
            "speicher_max": speicher, "browser_429": br429,
            "journal": {"lesbar": lesbar, "r429": r429, "anfragen_je_minute_max": anfragen_min, "tokens_je_minute_max": tokens_min}}


def status(kriterien, name_teil):
    treffer = [k for k in kriterien if name_teil in k["name"]]
    assert len(treffer) == 1, [k["name"] for k in kriterien]
    return treffer[0]["status"]


def alle_bestehen():
    return [stufen_dict(1, p95=1500.0), stufen_dict(8, p95=2200.0, cpu=55.0)]


def test_alles_im_gruenen_bereich_besteht_ausser_dem_ratenlimit_ohne_angabe():
    k = aw.kriterien_pruefen(alle_bestehen(), abbruchgrund="", ratenlimit_anfragen=None, ratenlimit_tokens=None)
    for teil in ("p95 des ersten Tons bei 8", "1,5-fache", "Abbrüche", "CPU", "Speicher", "429"):
        assert status(k, teil) == "bestanden", teil
    assert status(k, "Anfragen je Minute") == "offen" and status(k, "Tokens je Minute") == "offen"
    assert aw.gesamturteil(k, "") == "offen"


def test_mit_ratenlimit_unter_70_prozent_ist_alles_bestanden():
    k = aw.kriterien_pruefen(alle_bestehen(), "", ratenlimit_anfragen=50, ratenlimit_tokens=200000)
    assert status(k, "Anfragen je Minute") == "bestanden" and status(k, "Tokens je Minute") == "bestanden"
    assert aw.gesamturteil(k, "") == "bestanden"


def test_ratenlimit_ab_70_prozent_faellt_durch():
    k = aw.kriterien_pruefen([stufen_dict(8, anfragen_min=35, tokens_min=140000)], "", 50, 200000)
    assert status(k, "Anfragen je Minute") == "durchgefallen" and status(k, "Tokens je Minute") == "durchgefallen"


def test_p95_ueber_3500_faellt_durch_genau_3500_besteht():
    assert status(aw.kriterien_pruefen([stufen_dict(8, p95=3501.0)], "", None, None), "bei 8") == "durchgefallen"
    assert status(aw.kriterien_pruefen([stufen_dict(8, p95=3500.0)], "", None, None), "bei 8") == "bestanden"


def test_faktor_zu_einer_sitzung():
    k = aw.kriterien_pruefen([stufen_dict(1, p95=1000.0), stufen_dict(8, p95=1501.0)], "", None, None)
    assert status(k, "1,5-fache") == "durchgefallen"
    k = aw.kriterien_pruefen([stufen_dict(1, p95=1000.0), stufen_dict(8, p95=1500.0)], "", None, None)
    assert status(k, "1,5-fache") == "bestanden"


def test_ohne_messung_ist_ein_kriterium_offen_nie_bestanden():
    k = aw.kriterien_pruefen([stufen_dict(8, n=0)], "", None, None)
    assert status(k, "bei 8") == "offen" and status(k, "1,5-fache") == "offen"
    k = aw.kriterien_pruefen([stufen_dict(1)], "", None, None)  # die Stufe mit 8 Sitzungen fehlt
    assert status(k, "bei 8") == "offen"
    assert status(aw.kriterien_pruefen([stufen_dict(8, proben=0)], "", None, None), "CPU") == "offen"
    assert status(aw.kriterien_pruefen([stufen_dict(8, speicher=None)], "", None, None), "Speicher") == "offen"
    assert aw.gesamturteil(aw.kriterien_pruefen([], "", None, None), "") == "offen"


def test_murmelstufen_zaehlen_nicht_fuer_den_p95_aber_fuer_cpu_speicher_und_429():
    k = aw.kriterien_pruefen([stufen_dict(8, p95=1500.0), stufen_dict(8, snr=5, p95=9000.0, cpu=90.0)], "", None, None)
    assert status(k, "bei 8") == "bestanden" and status(k, "CPU") == "durchgefallen"


@pytest.mark.parametrize("cpu,erwartet", [(69.9, "bestanden"), (70.0, "durchgefallen"), (120.0, "durchgefallen")])
def test_cpu_grenze_70_prozent_eines_kerns(cpu, erwartet):
    assert status(aw.kriterien_pruefen([stufen_dict(8, cpu=cpu)], "", None, None), "CPU") == erwartet


@pytest.mark.parametrize("speicher,erwartet", [(int(2.8 * GIB) - 1, "bestanden"), (int(2.8 * GIB), "durchgefallen"), (3 * GIB, "durchgefallen")])
def test_speichergrenze_2_8_gib(speicher, erwartet):
    assert status(aw.kriterien_pruefen([stufen_dict(8, speicher=speicher)], "", None, None), "Speicher") == erwartet


def test_429_im_journal_oder_im_browser_faellt_durch():
    assert status(aw.kriterien_pruefen([stufen_dict(8, r429=1)], "", None, None), "429") == "durchgefallen"
    assert status(aw.kriterien_pruefen([stufen_dict(8, br429=2)], "", None, None), "429") == "durchgefallen"


def test_unlesbares_journal_ist_offen_ausser_der_browser_sah_ein_429():
    assert status(aw.kriterien_pruefen([stufen_dict(8, lesbar=False)], "", None, None), "429") == "offen"
    assert status(aw.kriterien_pruefen([stufen_dict(8, lesbar=False, br429=1)], "", None, None), "429") == "durchgefallen"
    assert status(aw.kriterien_pruefen([stufen_dict(8, lesbar=None)], "", None, None), "429") == "offen"


def test_jeder_abbruch_faellt_durch():
    assert status(aw.kriterien_pruefen([stufen_dict(8, abbrueche=1)], "", None, None), "Abbrüche") == "durchgefallen"
    assert aw.gesamturteil(aw.kriterien_pruefen([stufen_dict(8, abbrueche=1)], "", None, None), "") == "durchgefallen"


def test_gesamturteil_durchgefallen_schlaegt_offen():
    k = aw.kriterien_pruefen([stufen_dict(8, p95=9000.0)], "", None, None)
    assert aw.gesamturteil(k, "") == "durchgefallen"


def test_vorzeitiges_ende_ohne_durchfall_ist_nur_offen():
    k = aw.kriterien_pruefen(alle_bestehen(), "", 50, 200000)
    assert aw.gesamturteil(k, "Abbruch mit Strg+C") == "offen"


# ---------- Bericht ----------

def ergebnis_fuer_bericht():
    stufen = [dict(nummer=1, name="1 Sitzung", sitzungen=1, minuten=15, snr_db=None, schritte=20, ohne_antwort=0, dauer_s=900,
                   erster_ton_ms={"n": 20, "p50": 800.0, "p95": 1500.0, "max": 1700.0}, erster_ton_je_karte={},
                   verstummen_ms={"n": 3, "p50": 700.0, "p95": 900.0, "max": 950.0}, verstummen_verpasst=0, einwuerfe=3,
                   falsche_unterbrechungen=0, abbrueche=0, zeitueberschreitungen=0,
                   cpu_prozent={"proben": 50, "mittel": 20.0, "p95": 35.0, "max": 40.0}, speicher_max=int(1.2 * GIB), speicher_spitze=int(1.3 * GIB),
                   aufgaben_max=40, browser_429=0, konsolenfehler=0,
                   journal={"lesbar": True, "r429": 0, "soniox": 0, "anfragen": 40, "unterbrechungen": 3, "pipeline_fehler": 0, "token_zuege": 0,
                            "token_eingabe": 0, "anfragen_je_minute_max": 6, "tokens_je_minute_max": 0, "abgeschnitten": False})]
    kriterien = aw.kriterien_pruefen([stufen_dict(1, p95=1500.0), stufen_dict(8, p95=2200.0)], "", None, None)
    return {"lauf": "last-test", "version": 1, "ausfuehrung": "echt", "seed": 7, "schema": "wamo_dev", "slot": "b", "beginn": "2026-11-24T09:00:00+01:00",
            "ende": "2026-11-24T10:50:00+01:00", "abbruchgrund": "", "stufen": stufen, "kriterien": kriterien, "gesamt": aw.gesamturteil(kriterien, ""),
            "annahmen": {"eingabe_tokens_je_zug": 17000, "ausgabe_tokens_je_zug": 300, "anfragen_je_zug": 2.5}, "kosten_schaetzung": {"zuege": 100,
            "eingabe_tokens": 1700000, "ausgabe_tokens": 30000, "anfragen": 250.0, "usd": None},
            "aufraeumen": [{"schritt": "Browser geschlossen", "ok": True, "text": ""}, {"schritt": "Profile gesperrt", "ok": False, "text": "ssh Zeitüberschreitung"}]}


def test_bericht_nennt_urteil_stufen_kriterien_annahmen_aufraeumen_und_grenzen():
    text = aw.bericht_text(ergebnis_fuer_bericht())
    for muss in ("# Lasttest", "last-test", "1 Sitzung", "p95", "bestanden", "offen", "Annahmen", "17000", "Aufräumen", "ssh Zeitüberschreitung",
                 "Was die Last nicht belegt", "nicht gemessen"):
        assert muss in text, muss
    assert "Seed" in text and "7" in text


def test_bericht_enthaelt_keine_gedankenstriche_und_kein_geheimnis():
    text = aw.bericht_text(ergebnis_fuer_bericht())
    assert not re.search(r"[\u2013\u2014]| - ", text)
    for verboten in ("passwort", "password", "bearer", "secret"):
        assert verboten not in text.lower()


def test_bericht_ohne_stufen_meldet_das_ehrlich():
    e = ergebnis_fuer_bericht()
    e["stufen"], e["abbruchgrund"] = [], "Abbruch vor der ersten Stufe"
    text = aw.bericht_text(e)
    assert "Abbruch vor der ersten Stufe" in text and "keine stufe" in text.lower()


def test_ergebnis_ist_json_faehig_und_ohne_geheimnisse():
    json.dumps(ergebnis_fuer_bericht())
