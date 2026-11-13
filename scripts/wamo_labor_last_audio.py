"""Sprechproben und Murmeln für den Lasttest (scripts/wamo_labor_last.py, nur Standardbibliothek).

Das virtuelle Mikrofon jeder Browserinstanz spielt Sprechproben aus WAV-Dateien (16 Bit, ein Kanal, wie sie scripts/wamo_stimme.py clips
erzeugt). Dazu kann ein Murmeln als Hintergrund laufen, die Aufnahme mehrerer Stimmen. Der Abstand zur Sprache ist reproduzierbar und
braucht keinen Schallpegelmesser: Er ist das Verhältnis der Effektivwerte (RMS) von Sprache und Murmeln am Mikrofoneingang, gerechnet nur
über die Abschnitte, in denen wirklich gesprochen wird (Stille zählt nicht mit). Dieses Modul liefert dafür die Verstärkung des Murmelns.

Ohne eigene Aufnahme (--murmeln DATEI) entsteht das Murmeln aus den Sprechproben selbst: sechs Stimmen, jede mit eigener Abspielgeschwindigkeit
(Tonhöhe) und eigenen Pausen, überlagert. Es ist mit festem Seed wiederholbar, aber nur ein Ersatz: eine echte Mehrstimmenaufnahme klingt
anders (Sprecher, Räume, Konsonanten). Die Grenzen stehen in docs/labor-lasttest.md.
"""
from __future__ import annotations

import array
import math
import random
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from wamo_labor_last_plan import EingabeFehler, G3_FRAGEN

MURMELN_DAUER_S = 30.0
MURMEL_STIMMEN = 6
FENSTER_S = 0.02  # Fenster für die Pegelmessung
AKTIV_ANTEIL = 0.1  # ein Fenster zählt als Sprache, wenn sein Pegel mindestens 10 Prozent des lautesten Fensters hat (minus 20 dB)
MINDEST_PEGEL = 1e-4
SPITZE = 0.9

# Texte der Sprechproben: Karte A1 und J1 wie in voice-core/eval/faelle_labor.py, Einwurf und Faktenfragen wie in scripts/wamo_stimme.py.
# Tests halten beide Quellen mit dieser Liste zusammen.
SPRECHPROBEN = {
    "a1": "Kunden sollen ihr Passwort selbst zurücksetzen können. Mach daraus Anforderungen.",
    "j1": "Leg im Testprojekt einen Bug an: Login-Knopf reagiert nicht in Firefox, Priorität hoch.",
    "einwurf_a": "Moment, stopp, ich habe da eine andere Frage.",
    "einwurf_b": "Warte mal, was kostet das eigentlich?",
    "fakt_1": "Wie heißt die Hauptstadt von Frankreich?",
    "fakt_2": "Was ist zwei plus drei?",
    "fakt_3": "Nenn mir bitte eine Farbe.",
    "fakt_4": "Wie viele Tage hat eine Woche?",
    "fakt_5": "Welcher Planet ist der größte?",
    "fakt_6": "Wie heißt die Hauptstadt von Spanien?",
    "fakt_7": "Wie viele Beine hat eine Spinne?",
    "fakt_8": "Was ist das Gegenteil von warm?",
}
assert sum(1 for n in SPRECHPROBEN if n.startswith("fakt_")) == G3_FRAGEN


@dataclass(frozen=True)
class Murmeln:
    datei: Path
    gain: float
    quelle: str  # "datei" (eigene Aufnahme) oder "erzeugt" (aus den Sprechproben)
    snr_db: int


# ---------- WAV ----------

def lade_wav(pfad: Path) -> tuple[int, list[float]]:
    """Abtastrate und Werte (-1 bis 1) einer WAV-Datei mit 16 Bit und einem Kanal."""
    try:
        with wave.open(str(pfad), "rb") as w:
            kanaele, breite, rate, anzahl = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            roh = w.readframes(anzahl)
    except (wave.Error, EOFError):
        raise EingabeFehler(f"{Path(pfad).name} ist keine gültige WAV-Datei") from None
    except OSError:
        raise EingabeFehler(f"{Path(pfad).name} ist nicht lesbar") from None
    if kanaele != 1 or breite != 2:
        raise EingabeFehler(f"{Path(pfad).name}: erwartet wird WAV mit 16 Bit und einem Kanal (mono)")
    werte = array.array("h")
    werte.frombytes(roh)
    if sys.byteorder == "big":
        werte.byteswap()
    return rate, [w / 32768.0 for w in werte]


def schreibe_wav(pfad: Path, rate: int, daten: list[float]) -> None:
    ganz = array.array("h", (int(round(max(-1.0, min(1.0, d)) * 32767)) for d in daten))
    if sys.byteorder == "big":
        ganz.byteswap()
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(ganz.tobytes())


# ---------- Pegel ----------

def _fenster_energien(werte: list[float], rate: int) -> list[tuple[float, int]]:
    n = max(1, int(rate * FENSTER_S))
    return [(sum(x * x for x in werte[i:i + n]), len(werte[i:i + n])) for i in range(0, len(werte), n)]


def rms_aktiv(werte: list[float], rate: int) -> float:
    """Effektivwert über die Fenster, in denen gesprochen wird. Reine Stille ergibt 0."""
    fenster = _fenster_energien(werte, rate)
    pegel = [math.sqrt(e / n) for e, n in fenster if n]
    if not pegel or max(pegel) < MINDEST_PEGEL:
        return 0.0
    schwelle = max(pegel) * AKTIV_ANTEIL
    aktiv = [(e, n) for (e, n), p in zip(fenster, pegel) if p >= schwelle]
    return math.sqrt(sum(e for e, _ in aktiv) / sum(n for _, n in aktiv))


def sprach_pegel(clips: dict[str, tuple[int, list[float]]]) -> float:
    """Der Sprachpegel aller Clips: Wurzel aus dem Mittel der Quadrate der aktiven Effektivwerte."""
    pegel = [rms_aktiv(werte, rate) for rate, werte in clips.values()]
    pegel = [p for p in pegel if p > 0]
    if not pegel:
        raise EingabeFehler("Die Sprechproben enthalten keine Sprache (nur Stille)")
    return math.sqrt(sum(p * p for p in pegel) / len(pegel))


def murmel_gain(sprach_rms: float, murmel_rms: float, snr_db: float) -> float:
    """Verstärkung, bei der Sprache zu Murmeln genau snr_db Dezibel Abstand hat (Verhältnis der Effektivwerte)."""
    if murmel_rms <= 0:
        raise EingabeFehler("Das Murmeln enthält keinen Ton (nur Stille)")
    return sprach_rms / (murmel_rms * 10 ** (snr_db / 20))


# ---------- Murmeln erzeugen ----------

def _mit_tempo(werte: list[float], faktor: float) -> list[float]:
    """Das Stück mit anderer Abspielgeschwindigkeit (lineare Interpolation): schneller klingt höher, langsamer tiefer."""
    n = max(2, int(len(werte) / faktor))
    aus = []
    for i in range(n):
        pos = i * faktor
        k = int(pos)
        if k + 1 >= len(werte):
            break
        t = pos - k
        aus.append(werte[k] * (1 - t) + werte[k + 1] * t)
    return aus


def murmeln_erzeugen(clips: list[tuple[int, list[float]]], dauer_s: float, seed: int, stimmen: int = MURMEL_STIMMEN) -> tuple[int, list[float]]:
    """Überlagert mehrere Stimmen aus den Clips zu einem Murmeln. Wiederholbar mit festem Seed, Spitze 0,9."""
    if len(clips) < 2:
        raise EingabeFehler("Für das Murmeln braucht es mindestens zwei Sprechproben")
    rate = clips[0][0]
    if any(r != rate for r, _ in clips):
        raise EingabeFehler("Die Sprechproben haben verschiedene Abtastraten")
    zufall = random.Random(f"wamo-last-murmeln:{seed}")
    gesamt = int(rate * dauer_s)
    mix = [0.0] * gesamt
    for _ in range(stimmen):
        tempo, pegel, pos = zufall.uniform(0.85, 1.2), zufall.uniform(0.6, 1.0), 0
        while pos < gesamt:
            stueck = _mit_tempo(zufall.choice(clips)[1], tempo)
            for i, x in enumerate(stueck[:gesamt - pos]):
                mix[pos + i] += x * pegel
            pos += len(stueck) + int(rate * zufall.uniform(0.1, 0.8))
    spitze = max((abs(x) for x in mix), default=0.0)
    faktor = SPITZE / spitze if spitze > 0 else 0.0
    return rate, [x * faktor for x in mix]


# ---------- Clips ----------

def benoetigte_clips() -> list[str]:
    return sorted(SPRECHPROBEN)


def clips_pruefen(ordner: Path) -> dict[str, Path]:
    """Die WAV-Dateien aller benötigten Sprechproben im Ordner. Fehlt eine, steht ihr Name in der Fehlermeldung."""
    pfade = {name: Path(ordner) / f"{name}.wav" for name in benoetigte_clips()}
    fehlend = [n for n, p in pfade.items() if not p.is_file()]
    if fehlend:
        raise EingabeFehler(f"Es fehlen Sprechproben: {', '.join(fehlend)}. Ordner: {ordner}. "
                            "Erzeugen mit: python scripts/wamo_labor_last.py clips --clips-ordner <Ordner>")
    return pfade


def clips_erzeugen(ordner: Path, sprechen: Callable[[str, Path], None]) -> list[str]:
    """Erzeugt die fehlenden Sprechproben mit der übergebenen Stimme (Text, Zieldatei). Vorhandene bleiben unberührt."""
    Path(ordner).mkdir(parents=True, exist_ok=True)
    neu = []
    for name in benoetigte_clips():
        ziel = Path(ordner) / f"{name}.wav"
        if not ziel.is_file():
            sprechen(SPRECHPROBEN[name], ziel)
            neu.append(name)
    return neu


def murmeln_vorbereiten(clips_ordner: Path, murmeln_datei: Path | None, snr_db: int, seed: int, ziel: Path,
                        dauer_s: float = MURMELN_DAUER_S) -> Murmeln:
    """Murmeln und Verstärkung für einen Abstand: aus der eigenen Datei oder aus den Sprechproben erzeugt (dann nach ziel/murmeln.wav)."""
    geladen = {n: lade_wav(p) for n, p in clips_pruefen(clips_ordner).items()}
    pegel = sprach_pegel(geladen)
    if murmeln_datei is not None:
        datei = Path(murmeln_datei)
        if not datei.is_file():
            raise EingabeFehler(f"Die Murmeldatei fehlt: {datei}")
        quelle = "datei"
    else:
        rate, daten = murmeln_erzeugen([geladen[n] for n in sorted(geladen)], dauer_s, seed)
        datei, quelle = Path(ziel) / "murmeln.wav", "erzeugt"
        schreibe_wav(datei, rate, daten)
    rate, werte = lade_wav(datei)
    return Murmeln(datei, murmel_gain(pegel, rms_aktiv(werte, rate), snr_db), quelle, snr_db)
