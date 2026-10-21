"""Tests des Berichts (eval/bericht.py): pass^3, veraltete Läufe, wechselnde Ergebnisse."""

from __future__ import annotations

import json

import bericht
import bewertung as bw

JETZT = {"testsatz": "t1", "prompt": "p1", "bewertung": "b1", "pc_werkzeuge": "w1"}


def _lauf(ordner, name, *, ok_bis=50, ttft=0.3, hashes=JETZT, sauber=True, abbruch=None, modell="qwen", flacker=False,
          anbieter="litellm"):
    zuege = []
    for i in range(50):
        ok = i < ok_bis
        if flacker and i == 0:
            ok = name.endswith("2")
        zuege.append({"id": f"t{i:02}", "werkzeug_ok": ok, "marker_ok": ok, "ttft_s": ttft})
    k = {"zuege": 50, "soll": 50, "werkzeug_ok": sum(z["werkzeug_ok"] for z in zuege),
         "marker_ok": sum(z["marker_ok"] for z in zuege), "werkzeug_quote": 1.0, "marker_quote": 1.0,
         "format_quote": 1.0, "inhalt_quote": None, "werkzeug_mit_aufruf": "30/30", "werkzeug_ohne_aufruf": "20/20",
         "ttft_p50_s": ttft, "ttft_p95_s": ttft, "muss_fehler": [], "infrastrukturfehler": [], "kosten_usd": None}
    daten = {"anbieter": anbieter, "modell": modell, "hashes": hashes, "stand": {"git_sauber": sauber},
             "testsatz": {"zuege": 50, "hash": hashes["testsatz"]}, "abbruch": abbruch, "kennzahlen": k,
             "zuege": zuege}
    (ordner / f"{name}.json").write_text(json.dumps(daten), encoding="utf-8")


def _gruppe(ordner):
    return [d for d in bericht.laeufe_laden(ordner) if bericht.aktuell(d, JETZT)]


def test_freigabe_erst_nach_drei_laeufen(tmp_path):
    _lauf(tmp_path, "1")
    _lauf(tmp_path, "2")
    assert bericht.gruppen_freigabe(_gruppe(tmp_path))[0] == "offen (2 von 3 Läufen)"
    _lauf(tmp_path, "3")
    assert bericht.gruppen_freigabe(_gruppe(tmp_path))[0] == "ja"


def test_ein_schwacher_lauf_sperrt(tmp_path):
    _lauf(tmp_path, "1")
    _lauf(tmp_path, "2", ok_bis=47)
    _lauf(tmp_path, "3")
    status, gruende, _ = bericht.gruppen_freigabe(_gruppe(tmp_path))
    assert status == "nein" and any("Werkzeug 47/50" in g for g in gruende)


def test_p95_ueber_alle_laeufe(tmp_path):
    _lauf(tmp_path, "1", ttft=0.5)
    _lauf(tmp_path, "2", ttft=0.5)
    _lauf(tmp_path, "3", ttft=0.7)  # einzeln zu langsam
    status, gruende, _ = bericht.gruppen_freigabe(_gruppe(tmp_path))
    assert status == "nein" and any("über 3 Läufe" in g for g in gruende)


def test_nicht_committet_nur_vorlaeufig(tmp_path):
    for n in "123":
        _lauf(tmp_path, n, sauber=n != "2")
    assert bericht.gruppen_freigabe(_gruppe(tmp_path))[0] == "ja, vorläufig (Stand nicht committet)"


def test_veraltete_und_abgebrochene_laeufe_zaehlen_nicht(tmp_path):
    alt = JETZT | {"prompt": "p0"}
    _lauf(tmp_path, "0", hashes=alt)
    _lauf(tmp_path, "1")
    _lauf(tmp_path, "2", abbruch="3 Fehler in Folge")
    _lauf(tmp_path, "3")
    gruppe = _gruppe(tmp_path)
    assert [d["_datei"] for d in gruppe] == ["1.json", "2.json", "3.json"]
    assert bericht.gruppen_freigabe(gruppe)[0] == "offen (2 von 3 Läufen)"
    pfad = bericht.bericht_schreiben(tmp_path, JETZT)
    text = pfad.read_text(encoding="utf-8")
    assert "veraltet" in text and "abgebrochen" in text and "WAMOCON GmbH" in text


def test_wechselnde_ergebnisse(tmp_path):
    for n in "123":
        _lauf(tmp_path, n, flacker=True)
    _status, _gruende, gewertet = bericht.gruppen_freigabe(_gruppe(tmp_path))
    assert bericht.flackernd(gewertet) == ["t00"]


def test_alte_dateien_ohne_neue_felder(tmp_path):
    alt = {"anbieter": "claude", "modell": "claude-haiku-5-5", "testsatz": {"zuege": 50, "hash": "x"},
           "prompt_hash": None, "kennzahlen": {"zuege": 50, "werkzeug_quote": 0.96, "marker_quote": 0.74,
                                                "format_quote": 0.98, "ttft_p95_s": 1.37}, "zuege": []}
    (tmp_path / "alt.json").write_text(json.dumps(alt), encoding="utf-8")
    text = bericht.bericht_schreiben(tmp_path, JETZT).read_text(encoding="utf-8")
    assert "alt.json" in text and "veraltet" in text
    assert bw.FREIGABE_LAEUFE == 3


def test_latenzschwelle_nur_fuer_hausmodelle(tmp_path):
    """Claude ist Referenz und Rückfall: seine Zeiten zählen nicht gegen die Freigabe, Qualität schon."""
    claude, haus = tmp_path / "claude", tmp_path / "haus"
    claude.mkdir()
    haus.mkdir()
    for i in "123":
        _lauf(claude, i, ttft=1.1, anbieter="claude", modell="claude-haiku-5-5")
        _lauf(haus, i, ttft=1.1)
    assert bericht.gruppen_freigabe(_gruppe(claude))[0] == "ja"
    status, gruende, _ = bericht.gruppen_freigabe(_gruppe(haus))
    assert status == "nein" and any("erstes Token p95" in g for g in gruende)
    _lauf(claude, "4", ttft=1.1, ok_bis=47, anbieter="claude", modell="claude-haiku-5-5")
    status, gruende, _ = bericht.gruppen_freigabe(_gruppe(claude))
    assert status == "nein" and not any("erstes Token" in g for g in gruende)
