"""Tests für scripts/wamo_labor_last_audio.py (Sprechproben, Murmeln und der Signal-Rausch-Abstand).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q test_wamo_labor_last_audio.py

Nur Rechnung mit kurzen, erzeugten Tönen in einem temporären Ordner. Die Windows-Stimme wird durch eine Attrappe ersetzt.
"""
from __future__ import annotations

import math
import re
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_last_audio as au  # noqa: E402
import wamo_labor_last_plan as pl  # noqa: E402

RATE = 8000
WURZEL = Path(__file__).resolve().parent.parent


def ton(sekunden: float, amplitude: float = 0.5, hz: float = 220.0, rate: int = RATE) -> list[float]:
    return [amplitude * math.sin(2 * math.pi * hz * i / rate) for i in range(int(sekunden * rate))]


def rms(werte: list[float]) -> float:
    return math.sqrt(sum(w * w for w in werte) / len(werte))


# ---------- WAV ----------

def test_wav_hin_und_zurueck_bleibt_bis_auf_die_quantisierung_gleich(tmp_path):
    daten = ton(0.5, 0.4)
    pfad = tmp_path / "x.wav"
    au.schreibe_wav(pfad, RATE, daten)
    rate, zurueck = au.lade_wav(pfad)
    assert rate == RATE and len(zurueck) == len(daten)
    assert max(abs(a - b) for a, b in zip(daten, zurueck)) < 1e-4


def test_zu_laute_werte_werden_begrenzt(tmp_path):
    pfad = tmp_path / "laut.wav"
    au.schreibe_wav(pfad, RATE, [3.0, -3.0, 0.0])
    _, zurueck = au.lade_wav(pfad)
    assert zurueck[0] == pytest.approx(1.0, abs=1e-3) and zurueck[1] == pytest.approx(-1.0, abs=1e-3)


def test_stereo_oder_acht_bit_werden_abgelehnt(tmp_path):
    for kanaele, breite in ((2, 2), (1, 1)):
        pfad = tmp_path / f"k{kanaele}b{breite}.wav"
        with wave.open(str(pfad), "wb") as w:
            w.setnchannels(kanaele)
            w.setsampwidth(breite)
            w.setframerate(RATE)
            w.writeframes(b"\x00" * 100 * kanaele * breite)
        with pytest.raises(pl.EingabeFehler, match="16 Bit"):
            au.lade_wav(pfad)


def test_keine_wav_datei_wird_abgelehnt(tmp_path):
    pfad = tmp_path / "kaputt.wav"
    pfad.write_bytes(b"das ist keine wav datei")
    with pytest.raises(pl.EingabeFehler, match="WAV"):
        au.lade_wav(pfad)


# ---------- Pegel und Abstand ----------

def test_aktiver_pegel_ignoriert_stille():
    sprache = ton(1.0, 0.5)
    mit_stille = [0.0] * RATE + sprache + [0.0] * RATE
    assert au.rms_aktiv(mit_stille, RATE) == pytest.approx(rms(sprache), rel=0.05)
    assert rms(mit_stille) < 0.7 * rms(sprache)  # Gegenprobe: der gewöhnliche Pegel wäre viel zu niedrig


def test_pegel_von_reiner_stille_ist_null():
    assert au.rms_aktiv([0.0] * 1000, RATE) == 0.0


@pytest.mark.parametrize("snr_db", [20, 10, 5, 0])
def test_murmel_verstaerkung_ergibt_genau_den_abstand(snr_db):
    sprache, murmeln = 0.12, 0.04
    gain = au.murmel_gain(sprache, murmeln, snr_db)
    abstand = 20 * math.log10(sprache / (gain * murmeln))
    assert abstand == pytest.approx(snr_db, abs=1e-6)


def test_leiser_abstand_bedeutet_lautere_murmeln():
    assert au.murmel_gain(0.1, 0.1, 5) > au.murmel_gain(0.1, 0.1, 20)


def test_stilles_murmeln_ist_ein_fehler():
    with pytest.raises(pl.EingabeFehler, match="Murmeln"):
        au.murmel_gain(0.1, 0.0, 10)


def test_sprachpegel_ueber_mehrere_clips():
    clips = {"a": (RATE, ton(1.0, 0.5)), "b": (RATE, ton(1.0, 0.25))}
    erwartet = math.sqrt((0.5 ** 2 / 2 + 0.25 ** 2 / 2) / 2)
    assert au.sprach_pegel(clips) == pytest.approx(erwartet, rel=0.05)


# ---------- Murmeln erzeugen ----------

def clipliste():
    return [(RATE, ton(1.2, 0.5, 200)), (RATE, ton(0.8, 0.4, 310)), (RATE, ton(1.0, 0.3, 140))]


def test_murmeln_sind_mit_festem_seed_wiederholbar_und_pegelbegrenzt():
    a = au.murmeln_erzeugen(clipliste(), 4.0, seed=3)
    b = au.murmeln_erzeugen(clipliste(), 4.0, seed=3)
    c = au.murmeln_erzeugen(clipliste(), 4.0, seed=4)
    assert a == b and a != c
    rate, daten = a
    assert rate == RATE and len(daten) == 4 * RATE
    assert 0.5 < max(abs(x) for x in daten) <= 0.9 + 1e-9


def test_murmeln_brauchen_mindestens_zwei_clips_gleicher_abtastrate():
    with pytest.raises(pl.EingabeFehler, match="mindestens zwei"):
        au.murmeln_erzeugen([(RATE, ton(1))], 2.0, seed=1)
    with pytest.raises(pl.EingabeFehler, match="Abtastrate"):
        au.murmeln_erzeugen([(RATE, ton(1)), (16000, ton(1, rate=16000))], 2.0, seed=1)


# ---------- Clips ----------

def test_sprechproben_decken_die_kette_und_den_einwurf_ab():
    erwartet = {"a1", "j1", "einwurf_a", "einwurf_b"} | {f"fakt_{n}" for n in range(1, 9)}
    assert set(au.SPRECHPROBEN) == erwartet
    assert set(au.benoetigte_clips()) == erwartet


def test_texte_stimmen_mit_den_karten_und_dem_messskript_ueberein():
    faelle = (WURZEL / "voice-core" / "eval" / "faelle_labor.py").read_text(encoding="utf-8")
    assert au.SPRECHPROBEN["a1"] in faelle and au.SPRECHPROBEN["j1"] in faelle
    stimme = (WURZEL / "scripts" / "wamo_stimme.py").read_text(encoding="utf-8")
    for name in ("einwurf_a", "einwurf_b") + tuple(f"fakt_{n}" for n in range(1, 9)):
        assert re.search(rf'"{name}":\s*"{re.escape(au.SPRECHPROBEN[name])}"', stimme), name


def test_clips_pruefen_nennt_fehlende_namen(tmp_path):
    au.schreibe_wav(tmp_path / "a1.wav", RATE, ton(0.2))
    with pytest.raises(pl.EingabeFehler) as e:
        au.clips_pruefen(tmp_path)
    liste = str(e.value).split("Sprechproben: ")[1].split(". ")[0].split(", ")
    assert "j1" in liste and "a1" not in liste
    assert "clips" in str(e.value)


def test_clips_pruefen_liefert_alle_pfade(tmp_path):
    for name in au.SPRECHPROBEN:
        au.schreibe_wav(tmp_path / f"{name}.wav", RATE, ton(0.2))
    pfade = au.clips_pruefen(tmp_path)
    assert set(pfade) == set(au.SPRECHPROBEN) and all(p.is_file() for p in pfade.values())


def test_clips_erzeugen_ruft_die_stimme_nur_fuer_fehlende_clips(tmp_path):
    au.schreibe_wav(tmp_path / "a1.wav", RATE, ton(0.2))
    aufrufe = []

    def sprechen(text: str, ziel: Path) -> None:
        aufrufe.append(ziel.name)
        au.schreibe_wav(ziel, RATE, ton(0.2))

    neu = au.clips_erzeugen(tmp_path, sprechen)
    assert "a1.wav" not in aufrufe and len(aufrufe) == len(au.SPRECHPROBEN) - 1
    assert sorted(neu) == sorted(n for n in au.SPRECHPROBEN if n != "a1")
    assert au.clips_erzeugen(tmp_path, sprechen) == []  # alles da: nichts zu tun
    au.clips_pruefen(tmp_path)


# ---------- Murmeln für den Lauf vorbereiten ----------

def clips_im_ordner(ordner: Path) -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(sorted(au.SPRECHPROBEN)):
        au.schreibe_wav(ordner / f"{name}.wav", RATE, ton(0.6 + 0.05 * i, 0.4, 150 + 20 * i))
    return ordner


def test_murmeln_ohne_datei_werden_aus_den_clips_erzeugt(tmp_path):
    clips = clips_im_ordner(tmp_path / "clips")
    ziel = tmp_path / "temp"
    ziel.mkdir()
    m = au.murmeln_vorbereiten(clips, None, 10, seed=5, ziel=ziel, dauer_s=3.0)
    assert m.quelle == "erzeugt" and m.snr_db == 10 and m.datei.parent == ziel and m.datei.is_file()
    _, daten = au.lade_wav(m.datei)
    pegel = au.sprach_pegel({n: au.lade_wav(p) for n, p in au.clips_pruefen(clips).items()})
    ergebnis_abstand = 20 * math.log10(pegel / (m.gain * au.rms_aktiv(daten, RATE)))
    assert ergebnis_abstand == pytest.approx(10, abs=0.2)


def test_murmeln_aus_eigener_datei(tmp_path):
    clips = clips_im_ordner(tmp_path / "clips")
    eigene = tmp_path / "babble.wav"
    au.schreibe_wav(eigene, RATE, ton(2.0, 0.2, 400))
    ziel = tmp_path / "temp"
    ziel.mkdir()
    m = au.murmeln_vorbereiten(clips, eigene, 20, seed=5, ziel=ziel)
    assert m.quelle == "datei" and m.datei == eigene and m.gain > 0


def test_murmeldatei_die_fehlt_ist_ein_fehler(tmp_path):
    clips = clips_im_ordner(tmp_path / "clips")
    with pytest.raises(pl.EingabeFehler, match="Murmeldatei"):
        au.murmeln_vorbereiten(clips, tmp_path / "gibt-es-nicht.wav", 10, seed=1, ziel=tmp_path)
