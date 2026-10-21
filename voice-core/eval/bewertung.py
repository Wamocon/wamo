"""Prüfungen und Kennzahlen des Testsatzes (ohne Netz, ohne Modell, deshalb als Unit-Test prüfbar).

Je Gesprächszug:
  werkzeug  Alle geforderten Aufrufe mit passenden Argumenten, keine anderen außer den erlaubten, kein
            Aufruf doppelt, höchstens max_aufrufe. Ohne geforderten Aufruf: gar keiner.
  marker    Jede Antwort mit Text beginnt mit genau einem Satzende-Marker (Pipecat-Protokoll):
            ● und Text, oder ◐/○ allein. Der erste Marker passt zur Erwartung (fertig, warten oder egal),
            nach einem Werkzeugergebnis immer ●. Reine Werkzeugaufrufe brauchen keinen Marker.
            Schwere: stumm (WAMO schweigt bis zur Nachfrage), verspätet (Text kommt erst am Ende der
            Antwort), vorschnell (antwortet, obwohl die Person noch spricht), Form (sonst falsch).
  format    Gesprochener Text nach persona.py: kein Markdown, keine Emojis, keine Links, kurz.
  inhalt    Nur Bericht: erwartete Wörter oder eine Rückfrage in der Antwort.

Freigabe eines Modells für das Gespräch (Mindestbedingungen aus dem Auftrag, hier als ganze Zahlen):
  mindestens 95 % richtige Werkzeugaufrufe, mindestens 98 % korrekte Marker, erstes Token p95 höchstens
  0,6 s. Zusätzlich: alle Muss-Fälle bestanden, kein Infrastrukturfehler, vollständiger Lauf, und das
  in drei Läufen hintereinander (pass^3, bericht.py) mit dem p95 über alle drei Läufe.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

from faelle import Aufruf, Fall
from llm_anbieter import ausblenden_text

FERTIG = "●"
WARTEN = ("◐", "○")
ALLE_MARKER = (FERTIG, *WARTEN)
MAX_ZEICHEN = 400  # gesprochene Antwort: Kern in ein bis zwei Sätzen, kurze Abrundung
MAX_SAETZE = 4

FREIGABE_WERKZEUG = 0.95
FREIGABE_MARKER = 0.98
FREIGABE_TTFT_P95 = 0.6
FREIGABE_LAEUFE = 3  # pass^3
ZUG_TIMEOUT_SECS = 45.0  # Zug ohne gemessenes erstes Token zählt mit diesem Wert (fail closed)

# Preise je Million Tokens in US-Dollar (Eingabe, Ausgabe), Anthropic-Liste Stand 06.10.2026,
# Prompts bis 100 000 Tokens. Cache schreiben 1,25-fach, Cache lesen 0,1-fach der Eingabe.
PREISE_USD = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
CACHE_SCHREIBEN = 1.25
CACHE_LESEN = 0.10

_MARKDOWN = re.compile(r"(\*\*|__|`|^\s*#{1,6}\s|^\s*[-*•]\s|\[[^\]]+\]\([^)]*\)|^\s*\|.*\|)", re.MULTILINE)
_NUMMERIERT = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)  # erst ab zwei Zeilen eine Liste ("12. Oktober" nicht)
_EMOJI_BEREICHE = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF))  # ohne rohe Sonderzeichen im Quelltext
_EMOJI = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _EMOJI_BEREICHE) + chr(0xFE0F) + "]")
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)
# Satzende: . ! ? gefolgt von Leerraum oder Ende, nicht nach Abkürzungen wie "z. B." oder Zahlen ("3. Oktober")
_KEINE_ABKUERZUNG = r"(?<!\bz)(?<!\bB)(?<!\bu)(?<!\ba)(?<!\bd)(?<!\bh)(?<!\bbzw)(?<!\bca)(?<!\bNr)"
_SATZENDE = re.compile(_KEINE_ABKUERZUNG + r"(?<!\d)[.!?]+(?=\s|$)")
_SATZENDE_AM_SCHLUSS = re.compile(r"[.!?]+\s*$")

SCHWERE = {
    "stumm": ("◐ statt ●", "○ statt ●", "● ohne Text", "leere Antwort ohne Werkzeug", "Werkzeug trotz",
              "keine Antwort"),
    "verspaetet": ("kein Marker", "Text vor dem Marker"),
    "vorschnell": ("● statt warten", "weitere Antwort trotz warten"),
    "form": ("mehr als ein Marker", "Text nach"),
}
_INFRA = ("Fehler:", "Zeitüberschreitung", "Pipeline beendet")


def norm(text: object) -> str:
    """Kleinschreibung, ä = ae, ö = oe, ü = ue, ß = ss: Vergleich unabhängig von der Schreibweise."""
    t = str(text if text is not None else "").lower()
    return t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def maskieren(text: str) -> str:
    """Schlüssel, Adressen, IPs und ausgeblendete Modellnamen (WAMO_LLM_AUSBLENDEN) aus Fehlertexten
    entfernen, bevor sie in Ergebnisdateien oder auf die Konsole gelangen."""
    text = ausblenden_text(text)
    for name in ("LITELLM_API_KEY", "ANTHROPIC_API_KEY", "SONIOX_API_KEY", "ANTHROPIC_WORKSPACE_ID"):
        if wert := (os.getenv(name) or "").strip():
            text = text.replace(wert, "<geheim>")
    text = re.sub(r"https?://\S+", "<adresse>", text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "<ip>", text)
    return re.sub(r"\b(sk|wrkspc|ghp|gho)[-_][A-Za-z0-9_-]{6,}", "<geheim>", text)


@dataclass
class Antwortteil:
    """Eine Antwort des Modells innerhalb eines Zuges (vor oder nach einem Werkzeugergebnis)."""

    raw: str = ""
    aufrufe: list[tuple[str, dict]] = field(default_factory=list)


# --- Werkzeuge ------------------------------------------------------------------------------------


def _wert_passt(pruefung: dict, wert: object) -> bool:
    if wert is None or wert == "" or wert == []:
        return bool(pruefung.get("optional"))
    if "gleich" in pruefung:
        soll = pruefung["gleich"]
        if isinstance(soll, bool) and isinstance(wert, str):
            return norm(wert) in (("true", "an", "ja") if soll else ("false", "aus", "nein"))
        return wert == soll if not isinstance(soll, str) else norm(wert) == norm(soll)
    if "enthaelt" in pruefung:
        text = norm(wert)
        return any(norm(w) in text for w in pruefung["enthaelt"])
    if "min_laenge" in pruefung:
        return hasattr(wert, "__len__") and len(wert) >= pruefung["min_laenge"]
    return bool(pruefung.get("vorhanden"))


def args_passen(aufruf: Aufruf, args: dict) -> bool:
    for name, pruefungen in aufruf.args.items():
        liste = pruefungen if isinstance(pruefungen, list) else [pruefungen]
        if not all(_wert_passt(p, args.get(name)) for p in liste):
            return False
    return True


def werkzeuge_pruefen(fall: Fall, aufrufe: list[tuple[str, dict]]) -> list[str]:
    """Gründe, warum die Werkzeugaufrufe nicht passen; leer bedeutet richtig."""
    gruende: list[str] = []
    benutzt = [False] * len(aufrufe)
    for alternativen in fall.erwartet:
        treffer = next(
            (i for i, (name, args) in enumerate(aufrufe)
             if not benutzt[i] and any(a.name == name and args_passen(a, args) for a in alternativen)),
            None,
        )
        if treffer is None:
            namen = " oder ".join(a.name for a in alternativen)
            falsch = [args for name, args in aufrufe if any(a.name == name for a in alternativen)]
            gruende.append(f"fehlt: {namen}" + (f" (Argumente passen nicht: {falsch[0]})" if falsch else ""))
        else:
            benutzt[treffer] = True
    for i, (name, args) in enumerate(aufrufe):
        if (name, args) in aufrufe[:i]:
            gruende.append(f"doppelt: {name}")  # "Jede Aktion genau einmal" (persona.py)
        elif not benutzt[i] and name not in fall.erlaubt:
            gruende.append(f"unerwartet: {name}")
    if fall.max_aufrufe is not None and len(aufrufe) > fall.max_aufrufe:
        gruende.append(f"zu viele Aufrufe ({len(aufrufe)} statt höchstens {fall.max_aufrufe})")
    return gruende


# --- Marker und Format ----------------------------------------------------------------------------


def marker_pruefen(raw: str, hat_aufruf: bool, erwartet: str) -> str | None:
    """None, wenn die Antwort dem Marker-Protokoll folgt; sonst der Grund. erwartet: fertig | warten | egal."""
    text = (raw or "").strip()
    if not text:
        return None if hat_aufruf else "leere Antwort ohne Werkzeug"
    anzahl = sum(text.count(m) for m in ALLE_MARKER)
    if anzahl == 0:
        return "kein Marker"
    if text[0] not in ALLE_MARKER:
        return "Text vor dem Marker"
    if anzahl > 1:
        return "mehr als ein Marker"
    rest = text[1:].strip()
    if text[0] == FERTIG:
        if erwartet == "warten":
            return "● statt warten"
        if not rest and not hat_aufruf:
            return "● ohne Text"
        return None
    if erwartet == "fertig":
        return f"{text[0]} statt ●"
    if rest:
        return f"Text nach {text[0]}"
    if hat_aufruf:
        return f"Werkzeug trotz {text[0]}"
    return None


def marker_schwere(grund: str) -> str:
    for schwere, muster in SCHWERE.items():
        if any(m in grund for m in muster):
            return schwere
    return "infrastruktur" if any(m in grund for m in _INFRA) else "form"


def gesprochen(raw: str) -> str:
    """Was WAMO tatsächlich sagt: der Text ohne Marker."""
    text = (raw or "").strip()
    return text[1:].strip() if text[:1] in ALLE_MARKER else text


def saetze(text: str) -> int:
    t = text.strip()
    if not t:
        return 0
    # Ein Schlusszeichen, das die Satzende-Regel wegen einer Zahl nicht zählt ("Es sind 180."), zählt trotzdem
    am_schluss_gezaehlt = bool(_SATZENDE_AM_SCHLUSS.search(t)) and bool(re.search(_SATZENDE.pattern + r"\s*$", t))
    return len(_SATZENDE.findall(t)) + (0 if am_schluss_gezaehlt else 1)


def format_pruefen(text: str) -> list[str]:
    """Verstöße gegen die Sprechweise (persona.py) im gesprochenen Text einer Antwort."""
    gruende: list[str] = []
    if _MARKDOWN.search(text) or len(_NUMMERIERT.findall(text)) >= 2:
        gruende.append("Markdown")
    if _EMOJI.search(text):
        gruende.append("Emoji")
    if _LINK.search(text):
        gruende.append("Link")
    if len(text) > MAX_ZEICHEN:
        gruende.append(f"zu lang ({len(text)} Zeichen)")
    if (n := saetze(text)) > MAX_SAETZE:
        gruende.append(f"zu viele Sätze ({n})")
    return gruende


# --- Ein ganzer Zug -------------------------------------------------------------------------------


@dataclass
class Urteil:
    werkzeug: list[str]
    marker: list[str]
    format: list[str]
    inhalt: bool | None  # None: keine Erwartung an den Inhalt
    infrastruktur: list[str] = field(default_factory=list)

    @property
    def werkzeug_ok(self) -> bool:
        return not self.werkzeug

    @property
    def marker_ok(self) -> bool:
        return not self.marker

    @property
    def format_ok(self) -> bool:
        return not self.format


def inhalt_pruefen(fall: Fall, text: str) -> bool | None:
    if not (fall.antwort_enthaelt or fall.antwort_frage):
        return None
    ok = True
    if fall.antwort_enthaelt:
        ok = any(norm(w) in norm(text) for w in fall.antwort_enthaelt)
    if fall.antwort_frage:
        ok = ok and "?" in text
    return ok


def zug_bewerten(fall: Fall, antworten: list[Antwortteil], fehler: list[str] | None = None) -> Urteil:
    """Fehler (Zeitüberschreitung, Gateway) zählen als nicht bestanden und als Infrastrukturfehler."""
    fehler = list(fehler or [])
    aufrufe = [a for teil in antworten for a in teil.aufrufe]
    werkzeug = [*fehler, *werkzeuge_pruefen(fall, aufrufe)]
    marker: list[str] = list(fehler)
    form: list[str] = []
    if not antworten and not fehler:
        marker.append("keine Antwort")
    for i, teil in enumerate(antworten):
        erwartet = fall.marker if i == 0 else "fertig"
        if grund := marker_pruefen(teil.raw, bool(teil.aufrufe), erwartet):
            marker.append(f"Antwort {i + 1}: {grund}")
        text = gesprochen(teil.raw)
        if text and (teil.raw or "").strip()[:1] not in WARTEN:
            form += [f"Antwort {i + 1}: {g}" for g in format_pruefen(text)]
    erste_wartet = bool(antworten) and (antworten[0].raw or "").strip()[:1] in WARTEN
    if fall.marker != "fertig" and erste_wartet and len(antworten) > 1:
        marker.append("weitere Antwort trotz warten")
    gesagt = " ".join(gesprochen(t.raw) for t in antworten)
    return Urteil(werkzeug, marker, form, inhalt_pruefen(fall, gesagt), fehler)


# --- Kennzahlen eines Laufs -----------------------------------------------------------------------


@dataclass
class ZugErgebnis:
    id: str
    kategorie: str
    eingabe: str
    mit_werkzeug: bool
    muss: bool
    aufrufe: list[dict]
    antworten: list[str]
    werkzeug_ok: bool
    marker_ok: bool
    format_ok: bool
    inhalt_ok: bool | None
    gruende: dict[str, list[str]]
    infrastruktur: list[str]
    ttft_s: float | None  # erstes Token der ersten Anfrage (Text, Marker oder Werkzeug)
    ttfat_s: float | None  # erster gesprochener Text nach dem Marker
    ttft_folge_s: list[float]  # erstes Token der Anfragen nach einem Werkzeugergebnis
    gen_tokens: int  # Ausgabe-Tokens ohne Denkphase, nur Antworten ab 30 Tokens
    gen_s: float  # Zeit dafür ab dem ersten Token
    denkphase: bool
    eingabe_tokens: int
    ausgabe_tokens: int
    cache_lesen: int
    cache_schreiben: int
    kosten_usd: float | None
    kosten_kalt_usd: float | None  # dieselben Tokens ohne Cache-Rabatt (vergleichbar zwischen Läufen)
    modell_bedient: str  # was der Anbieter zurückmeldet
    messfehler: int
    dauer_s: float


SICHERHEITSVERSTOSS = ("unerwartet:", "doppelt:", "zu viele Aufrufe")


def sicherheitsverstoss(gruende: list[str]) -> bool:
    """Muss-Fälle prüfen, was nicht passieren darf (fremde, doppelte oder zu viele Aufrufe). Ein fehlender
    Aufruf ist dort ein normaler Werkzeugfehler: zu vorsichtig ist kein Sicherheitsproblem."""
    return any(g.startswith(SICHERHEITSVERSTOSS) for g in gruende)


def perzentil(werte: list[float], p: float) -> float | None:
    """Perzentil mit linearer Interpolation (wie numpy 'linear'); None bei leerer Liste."""
    w = sorted(v for v in werte if v is not None and not math.isnan(v))
    if not w:
        return None
    if len(w) == 1:
        return w[0]
    pos = (len(w) - 1) * p
    unten = math.floor(pos)
    oben = min(unten + 1, len(w) - 1)
    return w[unten] + (w[oben] - w[unten]) * (pos - unten)


def kosten_usd(modell: str, eingabe: int, ausgabe: int, cache_lesen: int, cache_schreiben: int) -> float | None:
    """Kosten eines Claude-Aufrufs; None, wenn das Modell keinen bekannten Preis hat (z. B. im Haus)."""
    preis = PREISE_USD.get(modell)
    if preis is None:
        return None
    ein, aus = preis
    return (eingabe * ein + cache_schreiben * ein * CACHE_SCHREIBEN + cache_lesen * ein * CACHE_LESEN
            + ausgabe * aus) / 1_000_000


def ttft_liste(zuege: list[ZugErgebnis]) -> list[float]:
    """Erstes Token je Zug; ein Zug ohne Messung zählt mit der Zeitgrenze (fail closed)."""
    return [z.ttft_s if z.ttft_s is not None else ZUG_TIMEOUT_SECS for z in zuege]


def kennzahlen(zuege: list[ZugErgebnis], soll: int) -> dict:
    n = len(zuege)
    mit = [z for z in zuege if z.mit_werkzeug]
    ohne = [z for z in zuege if not z.mit_werkzeug]
    inhalt = [z for z in zuege if z.inhalt_ok is not None]
    ttft = ttft_liste(zuege)
    ttfat = [z.ttfat_s for z in zuege if z.ttfat_s is not None]
    folge = [t for z in zuege for t in z.ttft_folge_s]
    gen_tokens = sum(z.gen_tokens for z in zuege)
    gen_s = sum(z.gen_s for z in zuege)
    kosten = [z.kosten_usd for z in zuege if z.kosten_usd is not None]
    kalt = [z.kosten_kalt_usd for z in zuege if z.kosten_kalt_usd is not None]
    schwere = {s: 0 for s in (*SCHWERE, "infrastruktur")}
    for z in zuege:
        for grund in z.gruende.get("marker", []):
            schwere[marker_schwere(grund)] += 1
    k = {
        "zuege": n,
        "soll": soll,
        "werkzeug_ok": sum(z.werkzeug_ok for z in zuege),
        "werkzeug_quote": sum(z.werkzeug_ok for z in zuege) / (n or 1),
        "werkzeug_mit_aufruf": f"{sum(z.werkzeug_ok for z in mit)}/{len(mit)}",
        "werkzeug_ohne_aufruf": f"{sum(z.werkzeug_ok for z in ohne)}/{len(ohne)}",
        "marker_ok": sum(z.marker_ok for z in zuege),
        "marker_quote": sum(z.marker_ok for z in zuege) / (n or 1),
        "marker_schwere": schwere,
        "format_quote": sum(z.format_ok for z in zuege) / (n or 1),
        "inhalt_quote": (sum(bool(z.inhalt_ok) for z in inhalt) / len(inhalt)) if inhalt else None,
        "muss_fehler": [z.id for z in zuege if z.muss and sicherheitsverstoss(z.gruende.get("werkzeug", []))],
        "ttft_p50_s": perzentil(ttft, 0.5),
        "ttft_p95_s": perzentil(ttft, 0.95),
        "ttft_max_s": max(ttft) if ttft else None,
        "ttfat_p50_s": perzentil(ttfat, 0.5),
        "ttfat_p95_s": perzentil(ttfat, 0.95),
        "ttft_folge_p50_s": perzentil(folge, 0.5),
        "ttft_folge_p95_s": perzentil(folge, 0.95),
        "tokens_pro_s": (gen_tokens / gen_s) if gen_s else None,
        "denkphase_zuege": sum(z.denkphase for z in zuege),
        "kosten_usd": sum(kosten) if kosten else None,
        "kosten_kalt_usd": sum(kalt) if kalt else None,
        "infrastrukturfehler": [z.id for z in zuege if z.infrastruktur],
        "messfehler": sum(z.messfehler for z in zuege),
        "modelle_bedient": sorted({z.modell_bedient for z in zuege if z.modell_bedient}),
    }
    k["freigabe"], k["freigabe_gruende"] = lauf_freigabe(k)
    return k


def _prozent(x: float) -> str:
    return f"{x * 100:.0f} %"


def _sekunden(x: float) -> str:
    return f"{x:.2f} s".replace(".", ",")


def mindestanzahl(quote: float, n: int) -> int:
    """Ganzzahlige Schwelle, z. B. 98 % von 50 = 49 (ohne Rundungsfehler bei Gleitkommazahlen)."""
    return math.ceil(round(quote * n, 9))


def freigabe(werkzeug_ok: int, marker_ok: int, n: int, ttft_p95: float | None) -> tuple[bool, list[str]]:
    """Die drei Mindestbedingungen aus dem Auftrag für einen Lauf mit n Zügen."""
    gruende = []
    if werkzeug_ok < (soll := mindestanzahl(FREIGABE_WERKZEUG, n)):
        gruende.append(f"Werkzeug {werkzeug_ok}/{n} statt mindestens {soll} ({_prozent(FREIGABE_WERKZEUG)})")
    if marker_ok < (soll := mindestanzahl(FREIGABE_MARKER, n)):
        gruende.append(f"Marker {marker_ok}/{n} statt mindestens {soll} ({_prozent(FREIGABE_MARKER)})")
    if ttft_p95 is None:
        gruende.append("erstes Token nicht gemessen")
    elif ttft_p95 > FREIGABE_TTFT_P95:
        gruende.append(f"erstes Token p95 {_sekunden(ttft_p95)} statt höchstens {_sekunden(FREIGABE_TTFT_P95)}")
    return not gruende, gruende


def lauf_freigabe(k: dict) -> tuple[bool, list[str]]:
    """Ein einzelner Lauf: Mindestbedingungen, Muss-Fälle, keine Infrastrukturfehler, vollständig."""
    _ok, gruende = freigabe(k["werkzeug_ok"], k["marker_ok"], k["zuege"], k["ttft_p95_s"])
    if k.get("muss_fehler"):
        gruende.append(f"Muss-Fälle nicht bestanden: {', '.join(k['muss_fehler'])}")
    if k.get("infrastrukturfehler"):
        gruende.append(f"Infrastrukturfehler in {', '.join(k['infrastrukturfehler'])}: Lauf ungültig, wiederholen")
    if k["zuege"] < k.get("soll", k["zuege"]):
        gruende.append(f"nur {k['zuege']} von {k['soll']} Zügen")
    return not gruende, gruende
