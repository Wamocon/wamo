"""Tests der Modellkarte (modellkarte.py): Aufbau, Regeln für Status und Datenschutz, und dass jede Zahl der Karte aus
den Ergebnisdateien unter eval/ergebnisse/ nachgerechnet werden kann (die Karte erfindet nichts)."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest

import modellkarte as mk

ERG = Path(__file__).resolve().parent / "eval" / "ergebnisse"
# Namen anderer Systeme dürfen nie in Karte, Ergebnissen oder Skripten stehen (rückwärts geschrieben, damit auch dieser
# Test sie nicht ausschreibt)
VERBOTEN = [w[::-1] for w in ("sivraj", "ikeeein", "lortnoctpmorp", "nocimad")]


def _lade(name: str) -> dict:
    return json.loads((ERG / name).read_text(encoding="utf-8"))


def _mittel(werte) -> float:
    return statistics.fmean(werte)


def _bausteine(name: str):
    def rechnen(dateien: list[dict]) -> tuple[float, int]:
        faelle = [f for d in dateien for f in d["bausteine"].get(name, [])]
        return sum(f["ok"] for f in faelle) / len(faelle), len(faelle)
    return rechnen


def _bausteine_zeit(name: str):
    def rechnen(dateien: list[dict]) -> tuple[float, int]:
        zeiten = [sum(f["dauer_s"] for f in d["bausteine"][name]) for d in dateien if name in d["bausteine"]]
        return _mittel(zeiten), len(zeiten)
    return rechnen


def _latenz(*schritte: str):
    def rechnen(dateien: list[dict]) -> tuple[float, int]:
        werte = [d[s]["erstes_token_s"] for d in dateien for s in schritte if s in d]
        return _mittel(werte), len(werte)
    return rechnen


# Name des Messwerts -> Rechnung aus den Ergebnisdateien: (Wert, Zahl der Fälle)
RECHNER = {
    "werkzeug": lambda ds: (_mittel(d["kennzahlen"]["werkzeug_quote"] for d in ds), sum(d["kennzahlen"]["zuege"] for d in ds)),
    "marker": lambda ds: (_mittel(d["kennzahlen"]["marker_quote"] for d in ds), sum(d["kennzahlen"]["zuege"] for d in ds)),
    "format": lambda ds: (_mittel(d["kennzahlen"]["format_quote"] for d in ds), sum(d["kennzahlen"]["zuege"] for d in ds)),
    "ttft_p50_s": lambda ds: (_mittel(d["kennzahlen"]["ttft_p50_s"] for d in ds), len(ds)),
    "ttft_p95_s": lambda ds: (max(d["kennzahlen"]["ttft_p95_s"] for d in ds), len(ds)),
    "tokens_pro_s": lambda ds: (_mittel(d["kennzahlen"]["tokens_pro_s"] for d in ds), len(ds)),
    "code_ok": lambda ds: (sum(f["ok"] for d in ds for f in d["faelle"]) / sum(len(d["faelle"]) for d in ds),
                           sum(len(d["faelle"]) for d in ds)),
    "code_s": lambda ds: (statistics.median(f["dauer_s"] for d in ds for f in d["faelle"]),
                          sum(len(d["faelle"]) for d in ds)),
    "analyse_richtig": lambda ds: (sum(x["richtig"] for d in ds for x in d["laeufe"])
                                   / sum(x["von"] for d in ds for x in d["laeufe"]),
                                   sum(x["von"] for d in ds for x in d["laeufe"])),
    "analyse_s": lambda ds: (_mittel(d["laeufe"][0]["dauer_s"] for d in ds), len(ds)),  # erster Lauf: kalter Prompt
    "lernen_ok": _bausteine("lernen"), "zusammenfassung_ok": _bausteine("zusammenfassung"),
    "vision_ok": _bausteine("vision"),
    "lernen_s": _bausteine_zeit("lernen"), "zusammenfassung_s": _bausteine_zeit("zusammenfassung"),
    "vision_s": _bausteine_zeit("vision"),
    "bewerter_ok": lambda ds: (sum(f["ok"] for d in ds for f in d["faelle"]) / sum(len(d["faelle"]) for d in ds),
                               sum(len(d["faelle"]) for d in ds)),
    "bewerter_s": lambda ds: (statistics.median(f["dauer_s"] for d in ds for f in d["faelle"][1:]), len(ds)),
    "einbettung_treffer": lambda ds: (_mittel(d["treffer"] / d["von"] for d in ds), sum(d["von"] for d in ds)),
    "einbettung_dimension": lambda ds: (ds[0]["dimension"], 1),
    "einbettung_s": lambda ds: (_mittel(d["einzel_p50_s"] for d in ds), len(ds)),
    "ttft_kurz_s": _latenz("kurz_2"),
    "ttft_voll_neu_s": _latenz("voll_neu_1", "voll_neu_2"),
    "ttft_voll_gleich_s": _latenz("voll_gleich"),
}
TOLERANZ = 0.006  # die Karte nennt drei Nachkommastellen


def _alle_messwerte():
    for m in mk.alle():
        for faehigkeit, e in m.eignungen.items():
            for w in e.messwerte:
                yield pytest.param(m, faehigkeit, w, id=f"{m.alias}-{faehigkeit}-{w.name}")


def test_aufbau():
    aliase = [m.alias for m in mk.alle()]
    assert len(aliase) == len(set(aliase))
    for m in mk.alle():
        assert m.status in mk.STATUS and m.anbieter in ("haus", "claude", "soniox")
        assert m.datenschutz == ("haus" if m.anbieter == "haus" else "cloud"), m.alias
        assert set(m.eignungen) <= set(mk.FAEHIGKEITEN), m.alias
        assert m.anzeige and m.basis
        for f, e in m.eignungen.items():
            assert e.latenz in mk.LATENZ, (m.alias, f)
            assert e.guete is None or 0.0 <= e.guete <= 1.0, (m.alias, f)
            assert e.guete is None or e.n > 0, (m.alias, f)
        assert set(m.spezialist_fuer) <= set(m.eignungen), m.alias
        if m.verweist_auf:
            assert m.status == "verweis" and mk.modell(m.verweist_auf), m.alias
        if m.sprache:
            assert m.sprache in ("stt", "tts") and set(m.eignungen) == {"sprache"}, m.alias


def test_karte_enthaelt_alle_bekannten_modelle():
    erwartet = {"gemma4-31b", "qwen3.6-35b", "qwen3.8-27b", "wamo-coder", "qwen3-coder-next", "qwen3-coder-next-raw",
                "qwen3-embedding", "laaj-judge", "sokrates-fable-qwen27", "sokrates-steuer", "sokrates", "sokrates-fast",
                "sokrates-pro", "wamo-vision", "wamo-allgemein", "wamo-review", "wamo-embed", "claude-haiku-5-5"}
    assert erwartet <= {m.alias for m in mk.alle()}
    sprache = {m.alias for m in mk.alle() if m.sprache}
    assert {"qwen3-asr", "faster-whisper", "qwen3-tts", "piper-de", "soniox-stt", "soniox-tts"} <= sprache


def test_fachmodell_und_unbekanntes():
    steuer, fable = mk.modell("sokrates-steuer"), mk.modell("sokrates-fable-qwen27")
    assert steuer.status == "fachmodell" and "Recht" in steuer.hinweis and "Alltag" in steuer.hinweis
    assert fable.status == "gesperrt" and "unbekannt" in fable.hinweis and "nicht verwenden" in fable.hinweis
    assert fable.datenschutz == "haus"
    assert mk.modell("sokrates-fast").verweist_auf == "gemma4-31b"
    assert mk.modell("sokrates-pro").verweist_auf == "qwen3.6-35b"


def test_nur_claude_ist_cloud_unter_den_sprachmodellen_ist_soniox_cloud():
    assert {m.datenschutz for m in mk.alle() if m.anbieter == "claude"} == {"cloud"}
    assert {m.datenschutz for m in mk.sprachmodelle("stt") if m.anbieter == "soniox"} == {"cloud"}
    assert {m.datenschutz for m in mk.sprachmodelle("tts") if m.anbieter == "haus"} == {"haus"}
    with pytest.raises(ValueError):
        mk.sprachmodelle("llm")


def test_latenzklassen():
    assert mk.latenzklasse("gespraech", 1.0) == "schnell"
    assert mk.latenzklasse("gespraech", 3.0) == "mittel"
    assert mk.latenzklasse("gespraech", 7.5) == "langsam"
    assert mk.latenzklasse("bilder", 14.0) == "mittel"


@pytest.mark.parametrize("m,faehigkeit,w", list(_alle_messwerte()))
def test_messwert_stimmt_mit_den_ergebnisdateien(m, faehigkeit, w):
    json_quellen = [q for q in w.quellen if q.endswith(".json")]
    for q in json_quellen:
        assert (ERG / q).is_file(), f"{q} fehlt"
    assert w.datum.startswith("2026-")
    if not json_quellen:
        pytest.skip("Messung im Betrieb, keine Datei im Repository")
    rechner = RECHNER[w.name]
    wert, n = rechner([_lade(q) for q in json_quellen])
    assert w.wert == pytest.approx(wert, abs=TOLERANZ), (m.alias, w.name, wert)
    if w.n:
        assert w.n == n, (m.alias, w.name, n)


def test_gueten_folgen_den_messwerten():
    """Güte = Anteil bestandener Fälle bzw. Mittel aus Werkzeug, Marker und Format; n = Zahl der Fälle."""
    for m in mk.alle():
        for f, e in m.eignungen.items():
            by = {w.name: w for w in e.messwerte}
            if f == "gespraech" and "werkzeug" in by:
                assert e.guete == pytest.approx(_mittel(by[k].wert for k in ("werkzeug", "marker", "format")), abs=TOLERANZ)
                assert e.n == by["werkzeug"].n
            elif f == "werkzeuge" and "werkzeug" in by:
                assert e.guete == pytest.approx(by["werkzeug"].wert, abs=TOLERANZ)
            elif f == "code" and "code_ok" in by:
                assert (e.guete, e.n) == (pytest.approx(by["code_ok"].wert, abs=TOLERANZ), by["code_ok"].n)
            elif f == "analyse" and "analyse_richtig" in by:
                assert (e.guete, e.n) == (pytest.approx(by["analyse_richtig"].wert, abs=TOLERANZ), by["analyse_richtig"].n)
            elif f in ("lernen", "zusammenfassung") and f"{f}_ok" in by:
                assert (e.guete, e.n) == (pytest.approx(by[f"{f}_ok"].wert, abs=TOLERANZ), by[f"{f}_ok"].n)
            elif f == "bilder" and "vision_ok" in by:
                assert (e.guete, e.n) == (pytest.approx(by["vision_ok"].wert, abs=TOLERANZ), by["vision_ok"].n)
            elif f == "bewertung" and "bewerter_ok" in by:
                assert (e.guete, e.n) == (pytest.approx(by["bewerter_ok"].wert, abs=TOLERANZ), by["bewerter_ok"].n)
            elif f == "einbettung" and "einbettung_treffer" in by:
                assert (e.guete, e.n) == (pytest.approx(by["einbettung_treffer"].wert, abs=TOLERANZ), by["einbettung_treffer"].n)


def test_live_neu_aus_den_latenzmessungen():
    for m in mk.alle():
        if m.live_neu_s is None:
            continue
        gespraech = m.eignungen.get("gespraech")
        assert gespraech is not None, m.alias
        by = {w.name: w for w in gespraech.messwerte}
        assert m.live_neu_s == pytest.approx(by["ttft_voll_neu_s"].wert, abs=TOLERANZ), m.alias


def test_keine_namen_anderer_systeme_in_karte_und_ergebnissen():
    dateien = [Path(mk.__file__), Path(__file__).with_name("boss.py")]
    dateien += sorted((ERG / "modellkarte").glob("*")) + sorted((ERG.parent).glob("code_aufgaben.py"))
    dateien += [ERG.parent / "analyse_probe.py", ERG.parent / "modellkarte_messen.py"]
    for datei in dateien:
        text = datei.read_text(encoding="utf-8").lower()
        for wort in VERBOTEN:
            assert wort not in text, f"{wort} in {datei.name}"


def test_ergebnisdateien_ohne_geheimnisse():
    for datei in sorted((ERG / "modellkarte").glob("*.json")):
        text = datei.read_text(encoding="utf-8")
        assert "sk-ant-" not in text and "Bearer" not in text and "192.168." not in text and "http://" not in text, datei.name
