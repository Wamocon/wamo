"""Modellkarte: welches Modell was kann, mit gemessenen Werten (Datum, Quelle), Latenzklasse und Datenschutz.

Reines Datenmodul wie werkzeugkarte.py. Der Boss (boss.py) wählt aus dieser Karte; hier steht keine Entscheidung. Jeder
Wert trägt Datum und Quelle (Ergebnisdateien unter voice-core/eval/ergebnisse/ oder eine Messung im Betrieb). Neu messen:
voice-core/eval/abnahme.py (Gespräch mit Werkzeugen), eval/bausteine.py (Lernen, Zusammenfassung, Bildschirm) und
eval/modellkarte_messen.py (Code, lange Texte und Analyse, Bewerter, Einbettung, Antwortzeit im Live-Gespräch).
test_modellkarte.py rechnet jede Zahl aus den Ergebnisdateien nach: Wer eine Messung ändert, ändert die Karte mit.

Güte (0 bis 1) ist der Anteil bestandener Fälle der jeweiligen Messung, bei Gespräch das Mittel aus Werkzeug-Treue,
Satzende-Marker und Format. n ist die Zahl der Fälle: Wenige Fälle zählen im Boss weniger (Glättung zur Mitte hin).
Latenzklasse je Fähigkeit: schnell, mittel, langsam; die Grenzen stehen bei LATENZ_GRENZEN.

Sprachmodelle (Spracherkennung, Sprachausgabe) stehen als Einträge mit Fähigkeit "sprache" und der Art stt oder tts.
Ihre Umsetzung im Gespräch gehört zu Phase P5; hier liegt nur die Schnittstelle (sprachmodelle()).

Status
  standard   darf der Boss von selbst wählen
  fachmodell nur auf ausdrücklichen Wunsch, nie von selbst (Spezialwissen, das WAMO im Alltag nicht braucht)
  gesperrt   unbekannt oder ungeprüft: nie im Boss ohne eigene Messung und Freigabe
  verweis    Verweis auf ein anderes Modell der Karte (Doppelung), der Boss nimmt das Original
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Anbieter = Literal["haus", "claude", "soniox"]
FAEHIGKEITEN = ("gespraech", "werkzeuge", "code", "bilder", "analyse", "zusammenfassung", "lernen", "einbettung",
                "bewertung", "sprache")
LATENZ = ("schnell", "mittel", "langsam")
STATUS = ("standard", "fachmodell", "gesperrt", "verweis")
# Obergrenzen in Sekunden für schnell und mittel (darüber: langsam), je Fähigkeit
LATENZ_GRENZEN: Mapping[str, tuple[float, float]] = {
    "gespraech": (1.5, 3.5),  # erstes Token, p95 über den Testsatz, Prompt warm
    "code": (5.0, 15.0),  # Zeit je Aufgabe
    "analyse": (5.0, 15.0),  # Zeit für einen Bericht mit 24 Abschnitten (kalter Prompt)
    "bilder": (8.0, 20.0),  # Beschreiben und Finden
    "zusammenfassung": (10.0, 25.0),
    "lernen": (10.0, 30.0),  # alle vier Fälle zusammen
    "bewertung": (3.0, 10.0),
    "einbettung": (1.0, 3.0),
}
# Ein Gesprächsmodell, dessen erstes Token bei einem Prompt ohne Cache länger dauert, ist nicht livetauglich
LIVE_NEU_MAX_S = 10.0


@dataclass(frozen=True)
class Messwert:
    name: str  # werkzeug, marker, format, ttft_p50_s, ttft_p95_s, ok, richtig ...
    wert: float
    einheit: str  # Anteil, s, Token/s, Dimension
    datum: str  # ISO, Tag der Messung
    quellen: tuple[str, ...]  # Dateien unter voice-core/eval/ergebnisse/ (.json) oder ein Text für Messungen im Betrieb
    n: int = 0  # Zahl der Fälle (0: nicht zutreffend)


@dataclass(frozen=True)
class Eignung:
    guete: float | None  # 0 bis 1; None: nicht bewertet (z. B. Sprache)
    latenz: str  # schnell | mittel | langsam
    messwerte: tuple[Messwert, ...] = ()
    n: int = 0  # Zahl der Fälle hinter guete
    hinweis: str = ""


@dataclass(frozen=True)
class Modell:
    alias: str  # Name beim Gateway (Hausmodelle), Claude-ID oder Dienstname
    anbieter: Anbieter
    anzeige: str  # kurzer Name für Oberfläche und Meldungen
    basis: str  # was dahinter steht (soweit bekannt)
    eignungen: Mapping[str, Eignung]
    datenschutz: Literal["haus", "cloud"]
    status: str = "standard"
    spezialist_fuer: tuple[str, ...] = ()  # Fähigkeiten, für die das Modell eigens abgestimmt ist
    live_neu_s: float | None = None  # erstes Token mit vollem Prompt ohne Cache (Gespräch)
    sprache: str | None = None  # stt | tts
    kosten: Literal["keine", "gering", "mittel", "hoch"] = "keine"
    hinweis: str = ""
    verweist_auf: str | None = None

    @property
    def faehigkeiten(self) -> tuple[str, ...]:
        return tuple(f for f in FAEHIGKEITEN if f in self.eignungen)


def _m(name: str, wert: float, einheit: str, datum: str, *quellen: str, n: int = 0) -> Messwert:
    return Messwert(name, wert, einheit, datum, quellen, n)


def _e(guete: float | None, latenz: str, *messwerte: Messwert, n: int = 0, hinweis: str = "") -> Eignung:
    return Eignung(guete, latenz, messwerte, n, hinweis)


# --- Quellen (Dateien unter voice-core/eval/ergebnisse/) -----------------------------------------------------

ABN_GEMMA = tuple(f"2026-10-20_{z}_litellm_gemma4-31b.json" for z in ("163647", "164324", "164958"))
ABN_Q36 = tuple(f"2026-10-20_{z}_litellm_qwen3.6-35b.json" for z in ("165549", "170100", "170641"))
ABN_Q38 = tuple(f"2026-10-21_{z}_litellm_qwen3.8-27b.json" for z in ("073356", "073826", "074308"))
ABN_HAIKU = ("2026-10-21_074542_claude_claude-haiku-5-5.json",)
MK = "modellkarte/"  # Messungen für die Karte
BS = "bausteine/"  # Lernen, Zusammenfassung, Bildschirm

def alle() -> tuple[Modell, ...]:
    return KARTE


def modell(alias: str) -> Modell | None:
    return next((m for m in KARTE if m.alias == alias), None)


def mit_faehigkeit(faehigkeit: str) -> list[Modell]:
    return [m for m in KARTE if faehigkeit in m.eignungen]


def sprachmodelle(art: str) -> list[Modell]:
    """Spracherkennung (stt) oder Sprachausgabe (tts): alle Einträge der Karte, Haus und Cloud."""
    if art not in ("stt", "tts"):
        raise ValueError("art: stt oder tts")
    return [m for m in KARTE if m.sprache == art]


def lesbar(alias: str) -> str:
    m = modell(alias)
    return m.anzeige if m else alias


def latenzklasse(faehigkeit: str, sekunden: float) -> str:
    """Latenzklasse für eine gemessene Zeit (Grenzen in LATENZ_GRENZEN)."""
    schnell, mittel = LATENZ_GRENZEN[faehigkeit]
    return "schnell" if sekunden <= schnell else "mittel" if sekunden <= mittel else "langsam"


# --- Gespräch: Messwerte aus dem Abnahme-Testsatz (eval/abnahme.py, 51 Züge je Lauf) ----------------------------


def _gespraech(quellen: tuple[str, ...], datum: str, werkzeug: float, marker: float, format_: float, p50: float,
               p95: float, tps: float, n: int, latenz: str, *live: Messwert, hinweis: str = "") -> dict[str, Eignung]:
    """Eignungen gespraech und werkzeuge aus den Kennzahlen der Abnahme; live: Messungen der Antwortzeit."""
    gesamt = round((werkzeug + marker + format_) / 3, 3)
    messwerte = (_m("werkzeug", werkzeug, "Anteil", datum, *quellen, n=n), _m("marker", marker, "Anteil", datum, *quellen, n=n),
                 _m("format", format_, "Anteil", datum, *quellen, n=n), _m("ttft_p50_s", p50, "s", datum, *quellen),
                 _m("ttft_p95_s", p95, "s", datum, *quellen), _m("tokens_pro_s", tps, "Token/s", datum, *quellen), *live)
    return {
        "gespraech": _e(gesamt, latenz, *messwerte, n=n, hinweis=hinweis),
        "werkzeuge": _e(werkzeug, latenz, messwerte[0], n=n),
    }


def _live(modell_alias: str, datum: str, kurz: float, neu: float, gleich: float, *dateien: str) -> tuple[Messwert, ...]:
    q = tuple(MK + d for d in dateien)
    return (_m("ttft_kurz_s", kurz, "s", datum, *q), _m("ttft_voll_neu_s", neu, "s", datum, *q),
            _m("ttft_voll_gleich_s", gleich, "s", datum, *q))


LAT_GEMMA = ("2026-10-22_054917_latenz_litellm_gemma4-31b.json", "2026-10-22_061157_latenz_litellm_gemma4-31b.json",
             "2026-10-22_063225_latenz_litellm_gemma4-31b.json")
LAT_Q36 = ("2026-10-22_055050_latenz_litellm_qwen3.6-35b.json",)
LAT_Q38 = ("2026-10-22_055125_latenz_litellm_qwen3.8-27b.json",)
T = "2026-10-22"  # Tag der Messungen für die Karte

KARTE: tuple[Modell, ...] = (
    # ---- Claude (Cloud, nur für Profile mit eigenem API-Schlüssel) -----------------------------------------
    Modell(
        "claude-haiku-5-5", "claude", "Claude Haiku 5.5", "Anthropic Claude Haiku 5.5 (Standardmodell, Sonnet kostet ein Vielfaches)",
        {
            **_gespraech(ABN_HAIKU, "2026-10-21", 0.961, 0.882, 0.980, 0.723, 1.040, 248.788, 51, "schnell",
                         hinweis="Marker 88 %: erfüllt die Freigabe des Testsatzes allein nicht"),
            "code": _e(1.0, "schnell", _m("code_ok", 1.0, "Anteil", T, MK + "2026-10-22_060314_code_claude_claude-haiku-5-5.json", n=8),
                       _m("code_s", 1.345, "s", T, MK + "2026-10-22_060314_code_claude_claude-haiku-5-5.json"), n=8),
            "analyse": _e(0.917, "schnell", _m("analyse_richtig", 0.917, "Anteil", T, MK + "2026-10-22_062937_analyse_claude_claude-haiku-5-5.json", n=12),
                          _m("analyse_s", 1.97, "s", T, MK + "2026-10-22_062937_analyse_claude_claude-haiku-5-5.json"), n=12,
                          hinweis="Eingabe zählt bei Claude rund 12 500 Token, im Haus rund 5 500 (anderer Tokenizer)"),
            "bilder": _e(1.0, "schnell", _m("vision_ok", 1.0, "Anteil", "2026-10-21", BS + "2026-10-20_133742_claude_claude-haiku-5-5.json",
                                            BS + "2026-10-21_080903_claude_claude-haiku-5-5.json", BS + "2026-10-21_082228_claude_claude-haiku-5-5.json", n=3),
                         _m("vision_s", 5.533, "s", "2026-10-21", BS + "2026-10-20_133742_claude_claude-haiku-5-5.json",
                            BS + "2026-10-21_080903_claude_claude-haiku-5-5.json", BS + "2026-10-21_082228_claude_claude-haiku-5-5.json", n=3), n=3),
            "lernen": _e(0.75, "schnell", _m("lernen_ok", 0.75, "Anteil", "2026-10-21", BS + "2026-10-20_133742_claude_claude-haiku-5-5.json",
                                             BS + "2026-10-20_133819_claude_claude-haiku-5-5.json", BS + "2026-10-21_080903_claude_claude-haiku-5-5.json", n=12),
                         _m("lernen_s", 7.43, "s", "2026-10-21", BS + "2026-10-20_133742_claude_claude-haiku-5-5.json",
                            BS + "2026-10-20_133819_claude_claude-haiku-5-5.json", BS + "2026-10-21_080903_claude_claude-haiku-5-5.json", n=3), n=12,
                         hinweis="verpasst in jedem Lauf einen Fall (Tee, Hamburg oder den Dienstag)"),
            "zusammenfassung": _e(1.0, "schnell", _m("zusammenfassung_ok", 1.0, "Anteil", "2026-10-21", BS + "2026-10-20_133742_claude_claude-haiku-5-5.json",
                                                     BS + "2026-10-21_080903_claude_claude-haiku-5-5.json", n=2),
                                  _m("zusammenfassung_s", 4.485, "s", "2026-10-21", BS + "2026-10-20_133742_claude_claude-haiku-5-5.json",
                                     BS + "2026-10-21_080903_claude_claude-haiku-5-5.json", n=2), n=2),
        },
        "cloud", kosten="gering",
        hinweis="Standard für Profile mit eigenem API-Schlüssel; Entwicklungsaufträge laufen mit Claude Code und dem Claude-Code-Token",
    ),
    # ---- Hausmodelle für das Gespräch ---------------------------------------------------------------------------
    Modell(
        "gemma4-31b", "haus", "Gemma 4 31B", "gemma4:31b (Ollama auf einer Spark, über LiteLLM)",
        {
            **_gespraech(ABN_GEMMA, "2026-10-20", 0.915, 0.967, 1.0, 0.630, 3.044, 9.806, 153, "mittel",
                         *_live("gemma4-31b", T, 0.557, 7.453, 1.437, *LAT_GEMMA),
                         hinweis="erstes Token mit neuem Prompt 7,5 s, mit warmem Prompt 1,4 s; Kaltstart nach Verdrängung 8,7 s; nur 10 Token/s"),
            "code": _e(1.0, "langsam", _m("code_ok", 1.0, "Anteil", T, MK + "2026-10-22_060227_code_litellm_gemma4-31b.json", n=8),
                       _m("code_s", 20.83, "s", T, MK + "2026-10-22_060227_code_litellm_gemma4-31b.json"), n=8),
            "analyse": _e(1.0, "langsam", _m("analyse_richtig", 1.0, "Anteil", T, MK + "2026-10-22_060519_analyse_litellm_gemma4-31b.json", n=12),
                          _m("analyse_s", 24.78, "s", T, MK + "2026-10-22_060519_analyse_litellm_gemma4-31b.json"), n=12),
            "bilder": _e(1.0, "mittel", _m("vision_ok", 1.0, "Anteil", T, BS + "2026-10-21_082133_litellm_gemma4-31b.json", BS + "2026-10-22_054319_litellm_gemma4-31b.json", n=2),
                         _m("vision_s", 14.42, "s", T, BS + "2026-10-21_082133_litellm_gemma4-31b.json", BS + "2026-10-22_054319_litellm_gemma4-31b.json", n=2), n=2),
            "lernen": _e(1.0, "langsam", _m("lernen_ok", 1.0, "Anteil", T, BS + "2026-10-21_080604_litellm_gemma4-31b.json", BS + "2026-10-22_054319_litellm_gemma4-31b.json", n=8),
                         _m("lernen_s", 47.045, "s", T, BS + "2026-10-21_080604_litellm_gemma4-31b.json", BS + "2026-10-22_054319_litellm_gemma4-31b.json", n=2), n=8),
            "zusammenfassung": _e(1.0, "langsam", _m("zusammenfassung_ok", 1.0, "Anteil", T, BS + "2026-10-21_081343_litellm_gemma4-31b.json", BS + "2026-10-22_054319_litellm_gemma4-31b.json", n=2),
                                  _m("zusammenfassung_s", 29.395, "s", T, BS + "2026-10-21_081343_litellm_gemma4-31b.json", BS + "2026-10-22_054319_litellm_gemma4-31b.json", n=2), n=2,
                                  hinweis="braucht 30 s; die Zeitgrenze für Hausmodelle liegt bei 60 s"),
        },
        "haus", live_neu_s=7.453, hinweis="bestes Hausmodell fürs Gespräch mit Werkzeugen; wird vom Ollama verdrängt, wenn andere Modelle laden",
    ),
    Modell(
        "qwen3.6-35b", "haus", "Qwen 3.6 35B", "qwen3.6:35b (Ollama, MoE, schnell)",
        {
            **_gespraech(ABN_Q36, "2026-10-20", 0.739, 0.732, 0.935, 2.682, 2.732, 74.001, 153, "mittel",
                         *_live("qwen3.6-35b", T, 0.36, 2.305, 2.35, *LAT_Q36),
                         hinweis="Werkzeug-Treue und Marker zu schwach fürs Gespräch, nur als Notlösung"),
            "code": _e(0.875, "schnell", _m("code_ok", 0.875, "Anteil", T, MK + "2026-10-22_055912_code_litellm_qwen3.6-35b.json", n=8),
                       _m("code_s", 3.59, "s", T, MK + "2026-10-22_055912_code_litellm_qwen3.6-35b.json"), n=8),
            "analyse": _e(1.0, "mittel", _m("analyse_richtig", 1.0, "Anteil", T, MK + "2026-10-22_060344_analyse_litellm_qwen3.6-35b.json", n=12),
                          _m("analyse_s", 11.06, "s", T, MK + "2026-10-22_060344_analyse_litellm_qwen3.6-35b.json"), n=12),
            "bilder": _e(1.0, "schnell", _m("vision_ok", 1.0, "Anteil", T, BS + "2026-10-22_054533_litellm_qwen3.6-35b.json", n=1),
                         _m("vision_s", 3.79, "s", T, BS + "2026-10-22_054533_litellm_qwen3.6-35b.json", n=1), n=1,
                         hinweis="ein einziger Fall: schnell, aber dünne Datenlage"),
            "lernen": _e(0.75, "mittel", _m("lernen_ok", 0.75, "Anteil", T, BS + "2026-10-22_054533_litellm_qwen3.6-35b.json", n=4),
                         _m("lernen_s", 14.17, "s", T, BS + "2026-10-22_054533_litellm_qwen3.6-35b.json", n=1), n=4),
            "zusammenfassung": _e(1.0, "schnell", _m("zusammenfassung_ok", 1.0, "Anteil", T, BS + "2026-10-22_054533_litellm_qwen3.6-35b.json", n=1),
                                  _m("zusammenfassung_s", 4.06, "s", T, BS + "2026-10-22_054533_litellm_qwen3.6-35b.json", n=1), n=1,
                                  hinweis="ein einziger Fall"),
        },
        "haus", live_neu_s=2.305, hinweis="schnell (74 Token/s), aber im Gespräch mit Werkzeugen nicht zuverlässig",
    ),
    Modell(
        "qwen3.8-27b", "haus", "Qwen 3.8 27B", "qwen3.8:27b (Ollama)",
        {
            **_gespraech(ABN_Q38, "2026-10-21", 0.804, 0.569, 0.974, 1.420, 1.593, 23.975, 153, "mittel",
                         *_live("qwen3.8-27b", T, 0.18, 3.61, 0.21, *LAT_Q38),
                         hinweis="Marker 57 %: Sätze enden zu spät oder gar nicht, nur als Notlösung"),
            "analyse": _e(0.917, "mittel", _m("analyse_richtig", 0.917, "Anteil", T, MK + "2026-10-22_060429_analyse_litellm_qwen3.8-27b.json", n=12),
                          _m("analyse_s", 8.38, "s", T, MK + "2026-10-22_060429_analyse_litellm_qwen3.8-27b.json"), n=12),
            "bilder": _e(1.0, "mittel", _m("vision_ok", 1.0, "Anteil", T, BS + "2026-10-22_054617_litellm_qwen3.8-27b.json", n=1),
                         _m("vision_s", 10.0, "s", T, BS + "2026-10-22_054617_litellm_qwen3.8-27b.json", n=1), n=1),
            "lernen": _e(0.75, "mittel", _m("lernen_ok", 0.75, "Anteil", T, BS + "2026-10-22_054617_litellm_qwen3.8-27b.json", n=4),
                         _m("lernen_s", 11.04, "s", T, BS + "2026-10-22_054617_litellm_qwen3.8-27b.json", n=1), n=4),
            "zusammenfassung": _e(1.0, "schnell", _m("zusammenfassung_ok", 1.0, "Anteil", T, BS + "2026-10-22_054617_litellm_qwen3.8-27b.json", n=1),
                                  _m("zusammenfassung_s", 9.11, "s", T, BS + "2026-10-22_054617_litellm_qwen3.8-27b.json", n=1), n=1),
        },
        "haus", live_neu_s=3.61, hinweis="taugt nicht fürs Gespräch mit Werkzeugen",
    ),
    Modell(
        "wamo-allgemein", "haus", "WAMO Allgemein", "neutraler Alias für ein allgemeines Hausmodell (Verhalten wie qwen3.8-27b)",
        {
            **_gespraech(("2026-10-22_064337_litellm_wamo-allgemein.json",), T, 0.824, 0.510, 0.980, 1.441, 1.519, 24.804, 51, "mittel",
                         *_live("wamo-allgemein", T, 0.19, 1.485, 0.20, "2026-10-22_063059_latenz_litellm_wamo-allgemein.json"),
                         hinweis="Marker 51 % und ein Muss-Fall verfehlt: nur als Notlösung (ein Lauf)"),
            "code": _e(1.0, "schnell", _m("code_ok", 1.0, "Anteil", T, MK + "2026-10-22_063704_code_litellm_wamo-allgemein.json", n=8),
                       _m("code_s", 4.995, "s", T, MK + "2026-10-22_063704_code_litellm_wamo-allgemein.json"), n=8),
            "analyse": _e(1.0, "schnell", _m("analyse_richtig", 1.0, "Anteil", T, MK + "2026-10-22_063836_analyse_litellm_wamo-allgemein.json", n=12),
                          _m("analyse_s", 1.96, "s", T, MK + "2026-10-22_063836_analyse_litellm_wamo-allgemein.json"), n=12),
            "bilder": _e(1.0, "schnell", _m("vision_ok", 1.0, "Anteil", T, BS + "2026-10-22_063919_litellm_wamo-allgemein.json", n=1),
                         _m("vision_s", 5.34, "s", T, BS + "2026-10-22_063919_litellm_wamo-allgemein.json", n=1), n=1,
                         hinweis="ein einziger Fall"),
            "lernen": _e(1.0, "mittel", _m("lernen_ok", 1.0, "Anteil", T, BS + "2026-10-22_063919_litellm_wamo-allgemein.json", n=4),
                         _m("lernen_s", 15.93, "s", T, BS + "2026-10-22_063919_litellm_wamo-allgemein.json", n=1), n=4),
            "zusammenfassung": _e(1.0, "mittel", _m("zusammenfassung_ok", 1.0, "Anteil", T, BS + "2026-10-22_063919_litellm_wamo-allgemein.json", n=1),
                                  _m("zusammenfassung_s", 10.29, "s", T, BS + "2026-10-22_063919_litellm_wamo-allgemein.json", n=1), n=1,
                                  hinweis="ein einziger Fall"),
        },
        "haus", live_neu_s=1.485, hinweis="schneller Allrounder für Aufgaben im Hintergrund (Analyse 12 von 12 in 2 s), kein Gesprächsmodell",
    ),
    Modell(
        "wamo-review", "haus", "WAMO Review", "neutraler Alias, vermutlich für Prüfungen; denkt vor der Antwort",
        {"code": _e(1.0, "schnell", _m("code_ok", 1.0, "Anteil", T, MK + "2026-10-22_063816_code_litellm_wamo-review.json", n=8),
                    _m("code_s", 4.64, "s", T, MK + "2026-10-22_063816_code_litellm_wamo-review.json"), n=8)},
        "haus", hinweis="nur Code gemessen (8 von 8); im Streaming kommt Inhalt erst nach der Denkphase: kein Gesprächsmodell",
    ),
    # ---- Spezialisten --------------------------------------------------------------------------------------------
    Modell(
        "wamo-vision", "haus", "WAMO Bildmodell", "neutraler Alias für die Bildbeschreibung (Antwortzeit wie gemma4-31b)",
        {"bilder": _e(1.0, "mittel", _m("vision_ok", 1.0, "Anteil", T, BS + "2026-10-21_082206_litellm_wamo-vision.json", BS + "2026-10-22_054457_litellm_wamo-vision.json", n=2),
                      _m("vision_s", 14.17, "s", T, BS + "2026-10-21_082206_litellm_wamo-vision.json", BS + "2026-10-22_054457_litellm_wamo-vision.json", n=2), n=2)},
        "haus", spezialist_fuer=("bilder",), hinweis="Standard für Hausprofile beim Bildschirm; Lernen 4/4 und Zusammenfassung 1/1 im Lauf vom 22.10. (nicht gelistet, das Original ist gemma4-31b)",
    ),
    Modell(
        "wamo-coder", "haus", "WAMO Coder", "wamocon-coder, feinabgestimmter Coder (Claude Code gegen LiteLLM mit MAX_THINKING_TOKENS=0)",
        {"code": _e(0.875, "mittel", _m("code_ok", 0.875, "Anteil", T, MK + "2026-10-22_055452_code_litellm_wamo-coder.json", n=8),
                    _m("code_s", 7.235, "s", T, MK + "2026-10-22_055452_code_litellm_wamo-coder.json"), n=8)},
        "haus", spezialist_fuer=("code",), hinweis="Standard für Code im Haus; die erste Anfrage nach Leerlauf lädt das Modell (bis 30 s)",
    ),
    Modell(
        "qwen3-coder-next-raw", "haus", "Qwen3 Coder Next (roh)", "qwen3-coder-next ohne Feinabstimmung",
        {"code": _e(0.875, "mittel", _m("code_ok", 0.875, "Anteil", T, MK + "2026-10-22_055638_code_litellm_qwen3-coder-next-raw.json", n=8),
                    _m("code_s", 7.53, "s", T, MK + "2026-10-22_055638_code_litellm_qwen3-coder-next-raw.json"), n=8)},
        "haus", spezialist_fuer=("code",), hinweis="Rückfall für Code",
    ),
    Modell(
        "qwen3-coder-next", "haus", "Qwen3 Coder Next", "qwen3-coder-next (Alias für Claude Code gegen LiteLLM)",
        {"code": _e(0.875, "mittel", _m("code_ok", 0.875, "Anteil", T, MK + "2026-10-22_055802_code_litellm_qwen3-coder-next.json", n=8),
                    _m("code_s", 6.745, "s", T, MK + "2026-10-22_055802_code_litellm_qwen3-coder-next.json"), n=8)},
        "haus", spezialist_fuer=("code",), hinweis="Rückfall für Code",
    ),
    Modell(
        "laaj-judge", "haus", "Bewertungsmodell", "eigener Bewertungsserver (LLM als Richter)",
        {"bewertung": _e(1.0, "schnell", _m("bewerter_ok", 1.0, "Anteil", T, MK + "2026-10-22_055254_bewerter_litellm_laaj-judge.json", n=9),
                         _m("bewerter_s", 1.265, "s", T, MK + "2026-10-22_055254_bewerter_litellm_laaj-judge.json"), n=9,
                         hinweis="9 von 9, auch bei einer Anweisung in der Kandidatenantwort")},
        "haus", spezialist_fuer=("bewertung",), hinweis="liefert JSON mit urteil und begruendung (plus eigene Felder)",
    ),
    Modell(
        "wamo-embed", "haus", "WAMO Einbettung", "neutraler Alias für die Einbettung, gleiche Werte wie qwen3-embedding",
        {"einbettung": _e(1.0, "schnell", _m("einbettung_treffer", 1.0, "Anteil", T, MK + "2026-10-22_063459_einbettung_litellm_wamo-embed.json", n=6),
                          _m("einbettung_dimension", 4096, "Dimension", T, MK + "2026-10-22_063459_einbettung_litellm_wamo-embed.json"),
                          _m("einbettung_s", 0.17, "s", T, MK + "2026-10-22_063459_einbettung_litellm_wamo-embed.json"), n=6)},
        "haus", spezialist_fuer=("einbettung",), hinweis="Standard für Einbettungen im Haus (neutraler Name statt des Modellnamens)",
    ),
    Modell(
        "qwen3-embedding", "haus", "Qwen3 Einbettung", "qwen3-embedding:8b, 4096 Dimensionen",
        {"einbettung": _e(1.0, "schnell", _m("einbettung_treffer", 1.0, "Anteil", T, MK + "2026-10-22_063427_einbettung_litellm_qwen3-embedding.json", n=6),
                          _m("einbettung_dimension", 4096, "Dimension", T, MK + "2026-10-22_063427_einbettung_litellm_qwen3-embedding.json"),
                          _m("einbettung_s", 0.18, "s", T, MK + "2026-10-22_063427_einbettung_litellm_qwen3-embedding.json"), n=6,
                          hinweis="das OpenAI-SDK schickt encoding_format, das Ollama ablehnt: Anfrage ohne dieses Feld")},
        "haus", spezialist_fuer=("einbettung",),
    ),
    # ---- Fachmodell, Unbekanntes, Verweise (nie von selbst gewählt) ---------------------------------------------
    Modell(
        "sokrates-steuer", "haus", "Fachmodell Recht", "feinabgestimmt auf Rechts- und Steuerthemen",
        {}, "haus", status="fachmodell",
        hinweis="Fachmodell Recht, im Alltag nicht verwenden; der Boss wählt es nie von selbst",
    ),
    Modell(
        "sokrates-fable-qwen27", "haus", "Unbekanntes Modell", "feinabgestimmtes Qwen 27B, Zweck unbekannt",
        {}, "haus", status="gesperrt",
        hinweis="unbekannt, nicht verwenden; kein Standard und nie im Boss ohne eigene Messung und Freigabe (harmlose Frage 22.10.: allgemeiner Assistent, 1,6 bis 50 s)",
    ),
    Modell("sokrates-fast", "haus", "Sokrates fast", "Verweis auf gemma4-31b (gleiche Antwortzeiten gemessen)", {}, "haus",
           status="verweis", verweist_auf="gemma4-31b", hinweis="Doppelung, der Boss nimmt gemma4-31b"),
    Modell("sokrates-pro", "haus", "Sokrates pro", "Verweis auf qwen3.6-35b", {}, "haus", status="verweis",
           verweist_auf="qwen3.6-35b", hinweis="Doppelung, der Boss nimmt qwen3.6-35b"),
    Modell("sokrates", "haus", "Sokrates", "Rückfallziel des Gateways für alle Hausmodelle (Verweis auf gemma4-31b)", {}, "haus",
           status="verweis", verweist_auf="gemma4-31b", hinweis="Doppelung, der Boss nimmt gemma4-31b"),
    # ---- Sprachmodelle (Spracherkennung und Sprachausgabe): nur Einträge und Schnittstelle, die Umsetzung ist Phase P5 --
    Modell("soniox-stt", "soniox", "Soniox Hören", "Soniox, Streaming mit Zwischenergebnissen und Wortzeitstempeln",
           {"sprache": _e(None, "schnell", n=0)}, "cloud", sprache="stt", kosten="mittel",
           hinweis="Standard heute; der Schlüssel gehört zu einem US-Projekt (EU-Endpunkt siehe README)"),
    Modell("soniox-tts", "soniox", "Soniox Stimme", "Soniox, Streaming mit Wortzeitstempeln (weiche Unterbrechung)",
           {"sprache": _e(None, "schnell", n=0)}, "cloud", sprache="tts", kosten="mittel", hinweis="Standard heute"),
    Modell("qwen3-asr", "haus", "Qwen3 ASR", "Qwen3-ASR (vLLM auf einer Spark)",
           {"sprache": _e(None, "schnell", _m("stt_s", 0.65, "s", "2026-10-19", "Messung im Betrieb 19.10.2026 (nicht im Repository)"), n=0)},
           "haus", sprache="stt",
           hinweis="ganze Äußerung nach dem Sprechende, keine Zwischenergebnisse; noch nicht über das Gateway (Phase P5)"),
    Modell("faster-whisper", "haus", "Faster Whisper", "faster-whisper auf CPU (eigener Dienst)",
           {"sprache": _e(None, "langsam", n=0)}, "haus", sprache="stt",
           hinweis="Rückfall für die Erkennung im Haus; nicht gemessen; noch nicht über das Gateway (Phase P5)"),
    Modell("speech-router", "haus", "Speech-Router", "Verteiler vor den Erkennungsdiensten im Haus",
           {"sprache": _e(None, "mittel", n=0)}, "haus", status="gesperrt", sprache="stt",
           hinweis="lieferte am 19.10.2026 HTTP 500: ohne neue Messung nicht verwenden"),
    Modell("qwen3-tts", "haus", "Qwen3 TTS", "Qwen3-TTS (vLLM auf einer Spark)",
           {"sprache": _e(None, "mittel", n=0)}, "haus", sprache="tts",
           hinweis="nicht gemessen; noch nicht über das Gateway (Phase P5)"),
    Modell("piper-de", "haus", "Piper Deutsch", "Piper, deutsche Stimme (eigener Dienst)",
           {"sprache": _e(None, "schnell", _m("tts_s", 0.15, "s", "2026-10-19", "Messung im Betrieb 19.10.2026 (nicht im Repository)"), n=0)},
           "haus", sprache="tts", hinweis="0,1 bis 0,2 s je Satz; einfachere Stimme; noch nicht über das Gateway (Phase P5)"),
)
