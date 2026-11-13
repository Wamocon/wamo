"""Plan, Höchstgrenzen, Kosten und Schrittfolge des Lasttests (scripts/wamo_labor_last.py, nur Standardbibliothek).

Der Lasttest misst, wie sich WAMO mit 1, 2, 4 und 8 gleichzeitigen Sitzungen verhält (docs/testlabor-wamo.md, 3.7, und docs/labor-lasttest.md).
Dieses Modul rechnet nur: Es öffnet keine Datei, startet keinen Prozess und spricht mit keinem Server. Alle Höchstgrenzen stehen hier an
einer Stelle, damit weder ein Aufruf noch ein Fehler im Ablauf sie überschreiten kann.

Die Zufallsfolge der Denkpausen ist mit einem festen Seed je Lauf, je Stufe und je Sitzung wiederholbar (Zeichenfolge als Seed: Python
leitet daraus bei jedem Start dieselbe Folge ab).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Iterator, Sequence

GIB = 1024 ** 3
BESTAETIGUNG = "LASTTEST"  # zweites Schloss für den Echtlauf (neben --ausfuehren)

# Höchstgrenzen im Code (Auftrag): keine Option hebt sie auf
MAX_SITZUNGEN = 8
MAX_STUFE_MINUTEN = 20
STANDARD_STUFE_MINUTEN = 15
MAX_STUFEN = 10
ERLAUBTE_SITZUNGEN = (1, 2, 4, 8)
SNR_MIN_DB, SNR_MAX_DB = 0, 40

# Abbruchgrenzen: Der Lauf endet, sobald eine erreicht ist (der Abbruch bei 429 gilt schon beim ersten)
MAX_ABBRUECHE = 2  # mehr als zwei Abbrüche beenden den Lauf
ABBRUCH_SPEICHER_BYTES = 3 * GIB

# Bestehen (docs/testlabor-wamo.md, 3.7)
P95_ERSTER_TON_MS = 3500
FAKTOR_ZU_EINER_SITZUNG = 1.5
CPU_MAX_PROZENT = 70.0
SPEICHER_MAX_BYTES = int(2.8 * GIB)  # 70 Prozent von MemoryMax=4G
RATENLIMIT_ANTEIL = 0.7

# Kette und Takt
KETTE = ("A1", "J1", "G3")
DENKPAUSE_MIN_S, DENKPAUSE_MAX_S = 8.0, 20.0
EINWURF_JEDE = 3  # in jeder dritten A1-Runde ein Einwurf (Messung des Verstummens), 0 schaltet ihn ab
G3_FRAGEN = 8
EINWURF_CLIPS = ("einwurf_a", "einwurf_b")
CLIP_JE_KARTE = {"A1": "a1", "J1": "j1"}

# Zeiten für die Schätzung der Gesamtdauer
VORBEREITUNG_S = 600  # Profile freischalten, Durchläufe öffnen, acht Anmeldungen mit Code, Clips laden
ZWISCHEN_STUFEN_S = 60  # Gespräche beenden und neu starten
AUFRAEUMEN_S = 420  # Dienst und Profile aufräumen, dazu die Sperrfrist von 3 Minuten vor dem Löschen im Kern

_LAUF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
_KENNUNG_RE = re.compile(r"labor-0([1-8])")


class EingabeFehler(Exception):
    """Falsche Eingabe oder Einstellung, vor dem ersten Aufruf von Server oder Browser erkannt."""


@dataclass(frozen=True)
class Stufe:
    nummer: int
    sitzungen: int
    minuten: int
    snr_db: int | None = None  # None: ohne Murmeln

    @property
    def name(self) -> str:
        zusatz = "" if self.snr_db is None else f", Murmeln {self.snr_db} dB"
        return f"{self.sitzungen} Sitzung{'en' if self.sitzungen != 1 else ''}{zusatz}"


@dataclass(frozen=True)
class Annahmen:
    """Annahmen der Kostenschätzung. Sie sind nicht gemessen (docs/testlabor-wamo.md, 3.7: "A, nicht geprüft") und stehen im Bericht."""
    eingabe_tokens_je_zug: int = 17000  # Büro-Prompt laut docs/gespraech-optimierung.md, der Labor-Prompt ist kürzer (Obergrenze)
    ausgabe_tokens_je_zug: int = 300  # Annahme, in den Unterlagen nicht belegt
    anfragen_je_zug: float = 2.5  # 2 bis 3 Anfragen je Zug mit Werkzeugen (3.7)
    sprechzeit_s: float = 5.0  # Dauer der Sprechprobe
    antwortzeit_s: float = 20.0  # Dauer bis die Antwort fertig ist (A1 länger, G3 kürzer)


@dataclass(frozen=True)
class Kosten:
    zuege: int
    eingabe_tokens: int
    ausgabe_tokens: int
    anfragen: float
    usd: float | None


def lauf_pruefen(name: str) -> str:
    """Der Laufname wird Teil von Dateinamen: nie ein Pfad."""
    if not isinstance(name, str) or not _LAUF_RE.fullmatch(name):
        raise EingabeFehler("Der Laufname darf nur Buchstaben, Ziffern, Punkt, Unterstrich und Strich enthalten (höchstens 40 Zeichen)")
    return name


def profile_fuer(anzahl: int) -> tuple[str, ...]:
    """Die ersten anzahl Laborprofile (labor-01 bis labor-08). Reserve und Schaufenster gehören nie zur Last."""
    if not isinstance(anzahl, int) or not 1 <= anzahl <= MAX_SITZUNGEN:
        raise EingabeFehler(f"Es gibt höchstens {MAX_SITZUNGEN} Lastprofile (labor-01 bis labor-08)")
    return tuple(f"labor-{n:02d}" for n in range(1, anzahl + 1))


def platz_von(kennung: str) -> int:
    treffer = _KENNUNG_RE.fullmatch(kennung) if isinstance(kennung, str) else None
    if not treffer:
        raise EingabeFehler(f"{kennung!r} ist kein Lastprofil (erlaubt: labor-01 bis labor-08)")
    return int(treffer.group(1))


def stufen_bauen(sitzungen: Sequence[int], minuten: int, snr: Sequence[int]) -> tuple[Stufe, ...]:
    """Die Stufen des Laufs: erst die Sitzungszahlen aufsteigend, dann je Signal-Rausch-Abstand eine Stufe mit acht Sitzungen."""
    if not isinstance(minuten, int) or not 1 <= minuten <= MAX_STUFE_MINUTEN:
        raise EingabeFehler(f"Eine Stufe dauert 1 bis {MAX_STUFE_MINUTEN} Minuten")
    zahlen = list(sitzungen)
    if not zahlen or len(set(zahlen)) != len(zahlen) or any(z not in ERLAUBTE_SITZUNGEN for z in zahlen):
        raise EingabeFehler("Sitzungszahlen: jede von 1, 2, 4 und 8 höchstens einmal")
    abstaende = list(snr)
    if len(set(abstaende)) != len(abstaende) or any(not isinstance(a, int) or not SNR_MIN_DB <= a <= SNR_MAX_DB for a in abstaende):
        raise EingabeFehler(f"Signal-Rausch-Abstand: ganze Zahlen von {SNR_MIN_DB} bis {SNR_MAX_DB} dB, jeder höchstens einmal")
    if len(zahlen) + len(abstaende) > MAX_STUFEN:
        raise EingabeFehler(f"Höchstens {MAX_STUFEN} Stufen je Lauf")
    folge = [(z, None) for z in sorted(zahlen)] + [(MAX_SITZUNGEN, a) for a in abstaende]
    return tuple(Stufe(i, z, minuten, a) for i, (z, a) in enumerate(folge, 1))


# ---------- Schrittfolge ----------

@dataclass(frozen=True)
class SchrittPlan:
    karte: str
    clip: str
    pause_s: float
    einwurf: bool = False
    einwurf_clip: str = ""


def schritt_folge(seed: int, stufe: int, sitzung: int, einwurf_jede: int = EINWURF_JEDE) -> Iterator[SchrittPlan]:
    """Die endlose, wiederholbare Folge der Schritte einer Sitzung: A1, J1, G3 im Wechsel, je Sitzung versetzt begonnen."""
    zufall = random.Random(f"wamo-last:{seed}:{stufe}:{sitzung}")
    a1_runde = g3_runde = 0
    schritt = sitzung % len(KETTE)
    while True:
        karte = KETTE[schritt % len(KETTE)]
        schritt += 1
        pause = round(zufall.uniform(DENKPAUSE_MIN_S, DENKPAUSE_MAX_S), 1)
        if karte == "G3":
            clip = f"fakt_{g3_runde % G3_FRAGEN + 1}"
            g3_runde += 1
            yield SchrittPlan(karte, clip, pause)
            continue
        einwurf = karte == "A1" and einwurf_jede > 0 and a1_runde % einwurf_jede == einwurf_jede - 1
        einwurf_clip = EINWURF_CLIPS[(a1_runde // max(1, einwurf_jede)) % len(EINWURF_CLIPS)] if einwurf else ""
        a1_runde += karte == "A1"
        yield SchrittPlan(karte, CLIP_JE_KARTE[karte], pause, einwurf, einwurf_clip)


# ---------- Kosten und Dauer ----------

def zuege_je_sitzung(minuten: int, annahmen: Annahmen = Annahmen()) -> int:
    """Wie viele Züge eine Sitzung in einer Stufe etwa schafft: Sprechprobe, Antwort und die mittlere Denkpause je Zug."""
    mittel = (DENKPAUSE_MIN_S + DENKPAUSE_MAX_S) / 2 + annahmen.sprechzeit_s + annahmen.antwortzeit_s
    return max(1, int(minuten * 60 // mittel))


def kosten_schaetzen(stufen: Sequence[Stufe], annahmen: Annahmen = Annahmen(), preis_ein_usd_mio: float | None = None,
                     preis_aus_usd_mio: float | None = None) -> Kosten:
    """Tokens und Anfragen aus den Annahmen. Dollar nur mit beiden Preisen (Dollar je Million Tokens): Preise erfindet das Skript nicht."""
    if (preis_ein_usd_mio is None) != (preis_aus_usd_mio is None):
        raise EingabeFehler("Der Preis für Eingabe und der Preis für Ausgabe gehören zusammen (beide angeben oder keinen)")
    zuege = sum(s.sitzungen * zuege_je_sitzung(s.minuten, annahmen) for s in stufen)
    eingabe, ausgabe = zuege * annahmen.eingabe_tokens_je_zug, zuege * annahmen.ausgabe_tokens_je_zug
    usd = None if preis_ein_usd_mio is None else eingabe / 1e6 * preis_ein_usd_mio + ausgabe / 1e6 * preis_aus_usd_mio
    return Kosten(zuege, eingabe, ausgabe, zuege * annahmen.anfragen_je_zug, usd)


def kosten_pruefen(kosten: Kosten, obergrenze_usd: float | None) -> None:
    """Lehnt einen Plan ab, dessen Schätzung über der Kostenobergrenze liegt. Ohne Preise lässt sich keine Grenze prüfen."""
    if obergrenze_usd is None:
        return
    if kosten.usd is None:
        raise EingabeFehler("Eine Kostenobergrenze braucht beide Preise (--preis-eingabe und --preis-ausgabe)")
    if kosten.usd > obergrenze_usd:
        raise EingabeFehler(f"Die geschätzten Kosten ({kosten.usd:.2f} USD) liegen über der Kostenobergrenze ({obergrenze_usd:.2f} USD)")


def dauer_schaetzen(stufen: Sequence[Stufe]) -> int:
    """Gesamtdauer in Sekunden: Stufen, Pausen dazwischen, Vorbereitung und Aufräumen."""
    return sum(s.minuten * 60 for s in stufen) + max(0, len(stufen) - 1) * ZWISCHEN_STUFEN_S + VORBEREITUNG_S + AUFRAEUMEN_S
