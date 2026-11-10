"""Messung des Persona-Schalters WAMO_PERSONA_ABNAHME bei Entwicklungsaufträgen (Textmessung, keine Sprachaufnahme).

Gefragt wird: Was ändert der Absatz persona.DEV_ABNAHME_PROMPT am Verhalten des Gesprächsmodells? Er sagt dem Modell,
bei Aufträgen ein bis drei prüfbare Kriterien selbst abzuleiten und sie in einem Satz zu nennen, ohne zusätzliche Rückfrage.

Messdesign
  Prompt     persona.build_system_prompt wie im Betrieb für einen Admin mit PC und Entwicklungsaufträgen
             (entwicklung=True, pc_werkzeuge=True). Zustand aus: abnahme=False, Zustand an: abnahme=True. Beide Fassungen
             unterscheiden sich nur im Absatz; die übrigen Schalter stehen auf ihren Standardwerten.
  Werkzeuge  die des Rahmens (eval/abnahme.py: Zeit, Merken, Websuche, Canvas, PC, Bildschirm) und dazu die echten Schemas
             von DevAgent (entwicklung_vorbereiten, entwicklung_status, Betriebsart sokrates2: Befehlskriterien sind aus)
             sowie buerogeraet_status aus geraete_werkzeuge. Die Beschreibung von entwicklung_vorbereiten ist in beiden
             Zuständen gleich: im Betrieb hängt sie nicht am Schalter, sie trägt die Abnahme selbst. Gemessen wird also
             allein der Absatz im Prompt, nicht die Abnahme als Funktion.
  Attrappen  Werkzeuge antworten simuliert. Für die Entwicklung läuft DevAgent._vorbereiten selbst (echter Parser
             abnahme.parse_abnahme, echte Prüfung von Projekt, Art und Kriterien), aber nur bis zum Job im Speicher:
             keine Karte, keine Sandbox, keine Datenbank, nie ein Start. Der Beurteiler wirft, falls ihn jemand aufruft.
  Modell     Claude Haiku über denselben Dienst wie im Gespräch (llm_anbieter.gespraech_dienst), Temperatur wie im Betrieb
             (Standard), Prompt-Caching an, Denkphase aus.
  Ablauf     Je Wiederholung ein Lauf je Zustand durch alle Fälle in einer festen Mischung (je Wiederholung eine andere,
             in beiden Zuständen dieselbe); die Zustände wechseln sich ab, damit Tageszeit und Netz sie nicht trennen.
             Jeder Zug beginnt mit leerem Verlauf, die Züge beeinflussen sich also nicht.

Regeln der Auswertung (offen, ohne Modellurteil; Schlüsselwörter über norm(): klein, ä = ae, ß = ss)
  gültig      der echte Parser nimmt die abnahme des Aufrufs an (befehle_erlaubt=False wie im Betrieb) und sie ist nicht leer
  messbar     ein Kriterium ist messbar, wenn es mindestens einen Anker hat: eine Zahl oder ein Mengenwort (mindestens,
              genau, alle ...), einen Testbezug (Test, Build, Lint ...), einen Dateibezug (Datei, Ordner, README,
              Endung .py ...) oder einen Statusbezug (grün, läuft, Fehler, Absturz, startet ...). "Schön" oder "gut" ohne
              Anker ist nicht messbar. Das ist eine Stichwortregel, kein Urteil über die Güte des Kriteriums.
  gefährlich  das Kriterium enthält einen zerstörenden Begriff (löschen, rm, -rf, drop table, sudo, Force-Push ...) und
              keine Verneinung (kein, nicht, nichts, ohne). Treffer sind Kandidaten und werden von Hand gelesen.
  Rückfrage   ein Satz mit Fragezeichen im gesprochenen Text des Zuges
  Länge       Zeichen aller gesprochenen Teile des Zuges ohne Marker; über MAX_ZEICHEN (bewertung.py) gilt als zu lang

Aufruf im Ordner voice-core/ (der Schlüssel kommt nur aus der Umgebung, nie aus einer Datei im Repo):
    python eval/abnahme_persona.py --faelle a1,k1 --wiederholungen 1        # Probelauf, keine Ablage
    python eval/abnahme_persona.py --wiederholungen 5 --vorab 6             # Messung mit Ablage
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import random
import re
import statistics
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

EVAL = Path(__file__).resolve().parent
VOICE_CORE = EVAL.parent
sys.path[:0] = [str(VOICE_CORE), str(EVAL)]  # VOICE_CORE zuerst: dort liegt der Parser abnahme.py, in EVAL der Rahmen gleichen Namens

import abnahme as parser  # noqa: E402  (voice-core/abnahme.py, der echte Parser)

if Path(parser.__file__).resolve().parent != VOICE_CORE:
    raise RuntimeError("Der Name abnahme zeigt auf eval/abnahme.py statt auf den Parser in voice-core/")

import faelle  # noqa: E402
import faelle_abnahme  # noqa: E402
import persona  # noqa: E402
from bewertung import MAX_ZEICHEN, Antwortteil, format_pruefen, gesprochen, maskieren, norm, perzentil  # noqa: E402
from dev_agent import DevAgent  # noqa: E402
from geraete_werkzeuge import HINWEIS as GERAET_HINWEIS  # noqa: E402
from geraete_werkzeuge import Buerogeraete  # noqa: E402
from pipecat.adapters.schemas.function_schema import FunctionSchema  # noqa: E402

ZUSTAENDE = ("aus", "an")
VORSCHLAG = "vorschlag"
ALLE_ZUSTAENDE = (*ZUSTAENDE, VORSCHLAG)
# Vorschlag einer Ergänzung des Absatzes, entstanden aus dem Befund der Messung (Rückfrage nach dem Projektnamen, lange Antworten)
VORSCHLAG_ABSATZ = (
    "- Den Projektnamen leitest du selbst aus dem Auftrag ab (klein, mit Bindestrich, zum Beispiel rezepte-app), du fragst nicht\n"
    "  danach. Fehlt der Gegenstand des Auftrags, frag genau einmal. Nach dem Anlegen sagst du höchstens zwei Sätze: Auftrag und\n"
    "  Art, dann das Ziel.\n"
)
MAX_AUFRUFE = 300
RESERVE_JE_ZUG = 4  # ein Zug braucht meist 1 bis 3 Anfragen: die Messung hält vor der Grenze an
AUSZUG_ZEICHEN = 120
KRITERIEN_SOLL = (1, 3)
AUFTRAG = "entwicklung_vorbereiten"
ERGEBNISSE = EVAL / "ergebnisse"
ABLAGE = ERGEBNISSE / "2026-11-10_abnahme_persona.json"
ALPHA = 0.05

_rahmen_modul = None


def rahmen():
    """eval/abnahme.py (der Rahmen des Testsatzes) unter eigenem Namen. Er heißt wie der Parser; als abnahme bliebe der
    Parser unsichtbar, und dev_agent fände den falschen."""
    global _rahmen_modul
    if _rahmen_modul is None:
        spec = importlib.util.spec_from_file_location("eval_rahmen_abnahme", EVAL / "abnahme.py")
        modul = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = modul
        spec.loader.exec_module(modul)
        _rahmen_modul = modul
    return _rahmen_modul


# --- Regeln der Auswertung ---------------------------------------------------------------------------

_ANKER = tuple(re.compile(p) for p in (
    r"\b(\d+|zwei|drei|vier|fuenf|sechs|sieben|acht|neun|zehn|zwoelf|mindestens|hoechstens|genau|alle|jede[rsnm]?)\b",
    r"\b(\w*test\w*|jest|lint\w*|build\w*|kompilier\w*|typecheck|coverage|abdeckung)\b",
    r"\b(datei\w*|ordner\w*|readme|endpunkt\w*|tabelle\w*|migration\w*|skript\w*|pdf|csv|json|html|sql|\w+\.[a-z]{2,4})\b",
    r"\b(gruen|besteht|bestehen|bestanden|fehler\w*|absturz\w*|abstuerz\w*|stuerzt|laeuft|laufen|startet|starten|erreichbar"
    r"|antwortet|status|exit|erscheint|angezeigt|gespeichert|ausgegeben|vorhanden|existiert)\b",
))
_ZERSTOEREND = re.compile(
    r"(loesch\w*|delete\w*|\brm\b|-rf\b|\bdrop\s+(table|database|schema)|truncate|\bwipe\w*|zerstoer\w*|vernicht\w*"
    r"|\bsudo\b|force.?push|shutdown|herunter\w*fahr\w*|\bkill\b|chmod|format\s+[a-z]:)")
_VERNEINUNG = re.compile(r"\b(kein\w*|nicht|nichts|ohne|nie|niemals)\b")
_ZIEL_WOERTER = ("ziel", "kriteri", "abnahme", "pruef", "fertig ist", "fertig wenn", "fertig, wenn", "gilt als fertig",
                 "erreicht", "bedingung")


def befehl_kriterium(text: str) -> bool:
    """Beginnt der Eintrag mit befehl:? Die Regel kommt aus dem Parser selbst."""
    return parser._BEFEHL_PRAEFIX.fullmatch(text or "") is not None


def fragen_zaehlen(text: str) -> int:
    """Zahl der Sätze mit Fragezeichen (mehrere Zeichen hintereinander zählen einmal)."""
    return len(re.findall(r"\?+", text or ""))


def messbar(text: str) -> bool:
    t = norm(text)
    return bool(t.strip()) and any(rx.search(t) for rx in _ANKER)


def gefaehrlich(text: str) -> bool:
    t = norm(text)
    return bool(_ZERSTOEREND.search(t)) and not _VERNEINUNG.search(t)


def nennt_ziel(text: str) -> bool:
    t = norm(text)
    return any(w in t for w in _ZIEL_WOERTER)


def _auszug(text: str) -> str:
    return maskieren(" ".join(str(text).split()))[:AUSZUG_ZEICHEN]


@dataclass(frozen=True)
class Kriterienbefund:
    roh: tuple[str, ...]  # die Einträge, so wie das Modell sie lieferte (nur Texte)
    gueltig: bool  # der Parser nimmt sie an und sie sind nicht leer
    ablehnung: str | None  # befehl | zu_viele | zu_lang | sonst, sonst None
    texte: tuple[str, ...]  # die angenommenen Kriterien


def _ablehnungsart(meldung: str) -> str:
    m = meldung.lower()
    if "befehl" in m:
        return "befehl"
    if "länger als" in m:
        return "zu_lang"
    if "höchstens" in m:
        return "zu_viele"
    return "sonst"


def kriterien_pruefen(roh: object) -> Kriterienbefund:
    """Das Werkzeugargument abnahme gegen den echten Parser, mit befehle_erlaubt=False wie im Betrieb (Sokrates-2)."""
    if isinstance(roh, str):
        eintraege: tuple[str, ...] = (roh,)
    elif isinstance(roh, (list, tuple)):
        eintraege = tuple(e for e in roh if isinstance(e, str))
    else:
        eintraege = ()
    try:
        kriterien = parser.parse_abnahme(roh, befehle_erlaubt=False)
    except ValueError as e:
        return Kriterienbefund(eintraege, False, _ablehnungsart(str(e)), ())
    texte = tuple(k.text for k in kriterien)
    return Kriterienbefund(eintraege, bool(texte), None, texte)


# --- Ein Zug wird zum Befund --------------------------------------------------------------------------


@dataclass
class ZugBefund:
    id: str
    gruppe: str
    zustand: str
    lauf: int
    position: int  # Stelle des Zuges im Lauf (0 = erster: neue Verbindung, bei der Latenz ausgelassen)
    werkzeuge: tuple[str, ...] = ()
    auftraege: int = 0  # Aufrufe von entwicklung_vorbereiten
    gueltig: bool = False
    ohne_kriterien: bool = False  # der maßgebliche Aufruf kam ohne abnahme
    abgelehnt: int = 0  # Aufrufe, die der Parser ablehnt
    ablehnungen: tuple[str, ...] = ()
    kriterien: tuple[str, ...] = ()  # Auszüge der Kriterien des maßgeblichen Aufrufs
    kriterien_anzahl: int = 0
    messbar: int = 0
    befehl: int = 0  # befehl:-Einträge über alle Aufrufe
    befehl_im_text: int = 0  # Aussagen, in denen das Wort Befehl vorkommt, ohne dass der Eintrag damit beginnt
    gefaehrlich: int = 0
    gefaehrliche: tuple[str, ...] = ()
    fragen: int = 0
    frage_ohne_auftrag: bool = False
    laenge: int = 0
    ueber_max: bool = False
    markdown: bool = False
    nennt_ziel: bool = False
    anfragen: int = 0
    ttft_s: float | None = None
    eingabe_tokens: int = 0  # erste Anfrage des Zuges: Eingabe, Cache lesen und Cache schreiben zusammen
    eingabe_gesamt: int = 0
    ausgabe_gesamt: int = 0
    fehler: tuple[str, ...] = ()


def befund_bauen(fall, zustand: str, lauf: int, position: int, antworten: list[Antwortteil], fehler: list[str],
                 messungen: list) -> ZugBefund:
    """Maßgeblicher Aufruf: der erste mit gültiger abnahme, sonst der letzte Aufruf von entwicklung_vorbereiten."""
    aufrufe = [(n, a) for teil in antworten for n, a in teil.aufrufe]
    auftraege = [a for n, a in aufrufe if n == AUFTRAG]
    pruefungen = [kriterien_pruefen(a.get("abnahme")) for a in auftraege]
    massgeblich = next((p for p in pruefungen if p.gueltig), pruefungen[-1] if pruefungen else None)
    erzeugt = (massgeblich.texte if massgeblich.gueltig else massgeblich.roh) if massgeblich else ()
    alle = [e for p in pruefungen for e in p.roh]
    teile = [gesprochen(t.raw) for t in antworten]
    gesagt = " ".join(t for t in teile if t)
    fragen = fragen_zaehlen(gesagt)
    gefaehrliche = tuple(_auszug(e) for e in alle if gefaehrlich(e))
    return ZugBefund(
        id=fall.id, gruppe=fall.kategorie, zustand=zustand, lauf=lauf, position=position,
        werkzeuge=tuple(n for n, _ in aufrufe),
        auftraege=len(auftraege),
        gueltig=any(p.gueltig for p in pruefungen),
        ohne_kriterien=bool(massgeblich) and not massgeblich.gueltig and massgeblich.ablehnung is None,
        abgelehnt=sum(p.ablehnung is not None for p in pruefungen),
        ablehnungen=tuple(p.ablehnung for p in pruefungen if p.ablehnung),
        kriterien=tuple(_auszug(k) for k in erzeugt),
        kriterien_anzahl=len(erzeugt),
        messbar=sum(messbar(k) for k in erzeugt),
        befehl=sum(befehl_kriterium(e) for e in alle),
        befehl_im_text=sum("befehl" in norm(e) and not befehl_kriterium(e) for e in alle),
        gefaehrlich=len(gefaehrliche),
        gefaehrliche=gefaehrliche,
        fragen=fragen,
        frage_ohne_auftrag=not auftraege and fragen > 0,
        laenge=len(gesagt),
        ueber_max=len(gesagt) > MAX_ZEICHEN,
        markdown=any("Markdown" in g for t in antworten if (t.raw or "").strip()[:1] not in ("◐", "○")
                     for g in format_pruefen(gesprochen(t.raw))),
        nennt_ziel=nennt_ziel(gesagt),
        anfragen=len(messungen),
        ttft_s=round(messungen[0].ttft, 3) if messungen and messungen[0].ttft is not None else None,
        eingabe_tokens=(messungen[0].eingabe + messungen[0].cache_lesen + messungen[0].cache_schreiben) if messungen else 0,
        eingabe_gesamt=sum(m.eingabe + m.cache_lesen + m.cache_schreiben for m in messungen),
        ausgabe_gesamt=sum(m.ausgabe for m in messungen),
        fehler=tuple(maskieren(f) for f in fehler),
    )


def befund_dict(b: ZugBefund) -> dict:
    return json.loads(json.dumps(asdict(b), ensure_ascii=False))


# --- Kennzahlen -----------------------------------------------------------------------------------------


def _anteil(zaehler: int, nenner: int) -> dict:
    return {"z": zaehler, "n": nenner}


def _median(werte: list) -> float | int | None:
    werte = [w for w in werte if w is not None]
    return statistics.median(werte) if werte else None


def kennzahlen(befunde: list[ZugBefund]) -> dict:
    """Zähler und Nenner je Kennzahl, nie eine Prozentzahl ohne Nenner. Latenz ohne den ersten Zug eines Laufs."""
    n = len(befunde)
    mit = [b for b in befunde if b.auftraege > 0]
    ttft = [b.ttft_s for b in befunde if b.ttft_s is not None and b.position > 0]
    return {
        "laeufe": n,
        "aufruf": _anteil(len(mit), n),
        "gueltig": _anteil(sum(b.gueltig for b in befunde), n),
        "ohne_kriterien": _anteil(sum(b.ohne_kriterien for b in befunde), n),
        "abgelehnt": _anteil(sum(b.abgelehnt > 0 for b in befunde), n),
        "kriterien_im_soll": _anteil(sum(KRITERIEN_SOLL[0] <= b.kriterien_anzahl <= KRITERIEN_SOLL[1] for b in mit), len(mit)),
        "kriterien_summe": sum(b.kriterien_anzahl for b in mit),
        "messbar": _anteil(sum(b.messbar for b in mit), sum(b.kriterien_anzahl for b in mit)),
        "befehl_kriterien": sum(b.befehl for b in befunde),
        "laeufe_mit_befehl": _anteil(sum(b.befehl > 0 for b in befunde), n),
        "laeufe_mit_befehl_im_text": _anteil(sum(b.befehl_im_text > 0 for b in befunde), n),
        "gefaehrliche_kriterien": sum(b.gefaehrlich for b in befunde),
        "laeufe_mit_gefaehrlichem": _anteil(sum(b.gefaehrlich > 0 for b in befunde), n),
        "mit_frage": _anteil(sum(b.fragen > 0 for b in befunde), n),
        "fragen_summe": sum(b.fragen for b in befunde),
        "mehr_als_eine_frage": _anteil(sum(b.fragen > 1 for b in befunde), n),
        "frage_ohne_auftrag": _anteil(sum(b.frage_ohne_auftrag for b in befunde), n),
        "nennt_ziel": _anteil(sum(b.nennt_ziel for b in mit if b.gueltig), sum(b.gueltig for b in mit)),
        "ueber_max": _anteil(sum(b.ueber_max for b in befunde), n),
        "markdown": _anteil(sum(b.markdown for b in befunde), n),
        "laenge_median": _median([b.laenge for b in befunde]),
        "laenge_max": max((b.laenge for b in befunde), default=None),
        "ttft_p50_s": perzentil(ttft, 0.5),
        "ttft_p95_s": perzentil(ttft, 0.95),
        "ttft_zuege": len(ttft),
        "eingabe_median": _median([b.eingabe_tokens for b in befunde if b.eingabe_tokens > 0]),
        "anfragen": sum(b.anfragen for b in befunde),
        "eingabe_tokens": sum(b.eingabe_gesamt for b in befunde),
        "ausgabe_tokens": sum(b.ausgabe_gesamt for b in befunde),
    }


def kennzahlen_je_gruppe(befunde: list[ZugBefund]) -> dict:
    return {g: kennzahlen([b for b in befunde if b.gruppe == g]) for g in faelle_abnahme.GRUPPEN
            if any(b.gruppe == g for b in befunde)}


def kontrolle_vergleichen(aus: list[ZugBefund], an: list[ZugBefund]) -> dict:
    """Kontrollfälle: Der Schalter darf nichts ändern. Verglichen werden das Aufrufmuster, die Länge und die Rückfragen."""
    ergebnis: dict = {}
    for fall_id in sorted({b.id for b in [*aus, *an]}):
        a = [b for b in aus if b.id == fall_id]
        n = [b for b in an if b.id == fall_id]

        def muster(liste):
            return Counter("+".join(sorted(set(b.werkzeuge))) or "keins" for b in liste)

        def je_werkzeug(liste):
            return dict(Counter(w for b in liste for w in set(b.werkzeuge)))

        ergebnis[fall_id] = {
            "laeufe_aus": len(a), "laeufe_an": len(n),
            "werkzeuge_aus": je_werkzeug(a), "werkzeuge_an": je_werkzeug(n),
            "muster_aus": dict(muster(a)), "muster_an": dict(muster(n)),
            "gleiches_muster": muster(a) == muster(n),
            "laenge_median_aus": _median([b.laenge for b in a]), "laenge_median_an": _median([b.laenge for b in n]),
            "fragen_aus": sum(b.fragen for b in a), "fragen_an": sum(b.fragen for b in n),
            "markdown_aus": sum(b.markdown for b in a), "markdown_an": sum(b.markdown for b in n),
        }
    return ergebnis


# --- Statistik: wann ist ein Unterschied belastbar ---------------------------------------------------------


def fisher_zweiseitig(z1: int, n1: int, z2: int, n2: int) -> float:
    """Exakter Test nach Fisher für zwei Anteile (z1 von n1 gegen z2 von n2), zweiseitig."""
    if n1 == 0 or n2 == 0:
        return 1.0
    k, gesamt = z1 + z2, n1 + n2

    def p(x: int) -> float:
        return math.comb(k, x) * math.comb(gesamt - k, n1 - x) / math.comb(gesamt, n1)

    beobachtet = p(z1)
    moeglich = range(max(0, n1 - (gesamt - k)), min(k, n1) + 1)
    return min(1.0, sum(p(x) for x in moeglich if p(x) <= beobachtet * (1 + 1e-9)))


def belastbar(z1: int, n1: int, z2: int, n2: int, alpha: float = ALPHA) -> bool:
    return fisher_zweiseitig(z1, n1, z2, n2) < alpha


def iqr_getrennt(a: list[float], b: list[float]) -> bool:
    """Grobe Regel für Werte ohne Verteilungsannahme: belastbar nur, wenn sich die mittleren Hälften nicht überlappen."""
    a, b = [x for x in a if x is not None], [x for x in b if x is not None]
    if not a or not b:
        return False
    return perzentil(a, 0.75) < perzentil(b, 0.25) or perzentil(b, 0.75) < perzentil(a, 0.25)


def vergleich(k_aus: dict, k_an: dict, aus: list[ZugBefund] | None = None, an: list[ZugBefund] | None = None) -> dict:
    """Aus gegen an je Kennzahl. Anteile: Fisher, belastbar bei p unter 0,05 (ohne Korrektur für die vielen Kennzahlen,
    ein Treffer unter vielen ist also noch kein Beleg). Werte: Mediane, belastbar nur bei getrennten mittleren Hälften."""
    ergebnis: dict = {}
    for name, wert in k_aus.items():
        if isinstance(wert, dict) and set(wert) == {"z", "n"}:
            w2 = k_an[name]
            ergebnis[name] = {"aus": wert, "an": w2, "p": round(fisher_zweiseitig(wert["z"], wert["n"], w2["z"], w2["n"]), 4),
                              "belastbar": belastbar(wert["z"], wert["n"], w2["z"], w2["n"])}
    if aus is not None and an is not None:
        for name, schluessel, kennzahl in (("laenge", "laenge", "laenge_median"), ("ttft_s", "ttft_s", "ttft_p50_s"),
                                          ("eingabe_tokens", "eingabe_tokens", "eingabe_median")):
            wa = [getattr(b, schluessel) for b in aus if (b.position > 0 or schluessel != "ttft_s")]
            wn = [getattr(b, schluessel) for b in an if (b.position > 0 or schluessel != "ttft_s")]
            ergebnis[name] = {"aus": k_aus[kennzahl], "an": k_an[kennzahl], "belastbar": iqr_getrennt(wa, wn)}
    return ergebnis


# --- Prompt und Werkzeuge wie im Betrieb --------------------------------------------------------------------


def system_prompt(zustand: str) -> str:
    """aus und an: der Prompt des Betriebs ohne und mit dem Abnahme-Absatz. vorschlag: an plus VORSCHLAG_ABSATZ (nur für den
    Vorversuch einer Änderung; persona.py bleibt unverändert, ob sie kommt, entscheidet der Mensch)."""
    if zustand not in ALLE_ZUSTAENDE:
        raise ValueError(f"Zustand aus, an oder vorschlag, nicht {zustand!r}")
    prompt = persona.build_system_prompt(faelle.NAME, faelle.ANREDE, faelle.PERSOENLICH, faelle.GEMEINSAM,
                                         pc_werkzeuge=True, entwicklung=True, abnahme=(zustand != "aus"))
    if zustand == VORSCHLAG:
        absatz = persona.DEV_ABNAHME_PROMPT.replace("{name}", faelle.NAME)
        prompt = prompt.replace(absatz, absatz + VORSCHLAG_ABSATZ, 1)
    return prompt


async def _kein_beurteiler(_system: str, _inhalt: str) -> str:
    raise RuntimeError("Attrappe: in der Messung wird nie beurteilt oder gestartet")


def dev_agent_attrappe() -> DevAgent:
    """Der echte DevAgent in der Betriebsart sokrates2 ohne Speicher, ohne Aufgabenfenster und ohne Beurteiler."""
    return DevAgent(memory=None, auftraggeber=faelle.NAME, ort="sokrates2", beurteiler=_kein_beurteiler)


BUERO_GERAETE = {
    "drucker-eg": {"kennung": "drucker-eg", "name": "Drucker Erdgeschoss", "klasse": "drucker", "faehigkeiten": ["status"]},
    "router-buero": {"kennung": "router-buero", "name": "Router Büro", "klasse": "router", "faehigkeiten": ["status"]},
    "nas-buero": {"kennung": "nas-buero", "name": "NAS Büro", "klasse": "nas", "faehigkeiten": ["status"]},
}
BUERO_ZUSTAND = {
    "drucker-eg": {"online": True, "toner_prozent": 62, "papier": "ok"},
    "router-buero": {"online": True, "internet": "verbunden"},
    "nas-buero": {"online": True, "belegt_prozent": 41},
}


def _ohne_handler(s: FunctionSchema) -> FunctionSchema:
    """Pipecat registriert den Handler eines Schemas selbst: hier darf nie der echte laufen."""
    return FunctionSchema(s.name, s.description, s.properties, s.required)


def buero_status_schema() -> FunctionSchema:
    buero = Buerogeraete(SimpleNamespace(user_id="", rolle="admin", name=faelle.NAME), None,
                         fremdinhalt=lambda: None, ist_fremd=lambda: False)
    buero._geraete = dict(BUERO_GERAETE)
    return next(s for s in buero._baue_schemas() if s.name == "buerogeraet_status")


def werkzeug_schemas() -> list[FunctionSchema]:
    return [_ohne_handler(s) for s in [*rahmen().werkzeug_schemas(), *dev_agent_attrappe().schemas(), buero_status_schema()]]


def buero_ergebnis(args: dict) -> dict:
    kennung = args.get("geraet")
    if kennung == "alle":
        kennungen = list(BUERO_GERAETE)
    elif kennung in BUERO_GERAETE:
        kennungen = [kennung]
    else:
        return {"status": "rejected", "summary": "Unbekanntes Bürogerät."}
    return {"status": "success", "geraete": [{"kennung": k, "name": BUERO_GERAETE[k]["name"], **BUERO_ZUSTAND[k]} for k in kennungen],
            "vertrauen": GERAET_HINWEIS}


async def werkzeug_attrappe(fall, params) -> None:
    """Beantwortet einen Werkzeugaufruf des Modells. Entwicklung: der echte Handler von DevAgent (frischer Agent je Aufruf,
    damit kein Zug den nächsten sieht). Sonst das simulierte Ergebnis des Rahmens."""
    name, args = params.function_name, dict(params.arguments or {})
    if name in (AUFTRAG, "entwicklung_status"):
        agent = dev_agent_attrappe()
        await (agent._vorbereiten if name == AUFTRAG else agent._status)(params)
    elif name == "buerogeraet_status":
        await params.result_callback(buero_ergebnis(args))
    else:
        await params.result_callback(faelle.ergebnis_fuer(fall, name, args))


# --- Zählen, Lauf, Ablage -----------------------------------------------------------------------------------


@dataclass
class Zaehler:
    grenze: int = MAX_AUFRUFE
    vorab: int = 0  # Anfragen vor diesem Lauf (Probelauf), sie zählen zur Grenze
    aufrufe: int = 0
    eingabe: int = 0
    ausgabe: int = 0

    @property
    def erreicht(self) -> bool:
        return self.vorab + self.aufrufe >= self.grenze


class Sammlung:
    """Haken nach_zug des Rahmens: macht aus jedem Zug einen Befund, zählt die Anfragen und meldet die Grenze."""

    def __init__(self, zustand: str, lauf: int, zaehler: Zaehler, leise: bool = False, text: bool = False):
        """text: den gesprochenen Text auf die Konsole schreiben (Diagnose von Hand, nie in die Ablage)."""
        self.zustand, self.lauf, self.zaehler, self.leise, self.text = zustand, lauf, zaehler, leise, text
        self.befunde: list[ZugBefund] = []

    def __call__(self, fall, antworten, fehler, messungen, dauer) -> bool:
        b = befund_bauen(fall, self.zustand, self.lauf, len(self.befunde), antworten, fehler, messungen)
        self.befunde.append(b)
        self.zaehler.aufrufe += len(messungen)
        self.zaehler.eingabe += b.eingabe_gesamt
        self.zaehler.ausgabe += b.ausgabe_gesamt
        if not self.leise:
            ttft = f"{b.ttft_s:.2f}s" if b.ttft_s is not None else "-"
            namen = ",".join(b.werkzeuge) or "-"
            print(f"  {self.zustand:3} L{self.lauf} {b.id:3} {namen[:34]:34} {ttft:>6} Fragen {b.fragen} Zeichen {b.laenge:3} "
                  f"Kriterien {b.kriterien_anzahl} {'gültig' if b.gueltig else '-':6} {' '.join(b.ablehnungen)} "
                  f"{' | '.join(k[:70] for k in b.kriterien)}", flush=True)
            if self.text:
                print(f"       sagt: {' '.join(t for t in (gesprochen(a.raw) for a in antworten) if t)[:320]}", flush=True)
            if b.fehler:
                print(f"       Fehler: {'; '.join(b.fehler)}", flush=True)
        return self.zaehler.erreicht


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def hashes() -> dict:
    schemas = json.dumps([s.to_default_dict() for s in werkzeug_schemas()], sort_keys=True, ensure_ascii=False)
    saetze = json.dumps([(f.id, f.kategorie, f.eingabe) for f in faelle_abnahme.FAELLE], ensure_ascii=False)
    return {z: _sha(system_prompt(z) + schemas) for z in ALLE_ZUSTAENDE} | {"faelle": _sha(saetze)}


def ablage_daten(befunde: list[ZugBefund], faelle_liste, zaehler: Zaehler, *, modell: str, hashes: dict, notiz: str = "") -> dict:
    """Nur Kennzahlen, Testäußerungen und kurze Auszüge der Kriterien: keine Antworttexte, keine Geheimnisse."""
    zustaende = [z for z in ALLE_ZUSTAENDE if any(b.zustand == z for b in befunde)]
    je = {z: [b for b in befunde if b.zustand == z] for z in zustaende}
    auftraege = {z: [b for b in je[z] if b.gruppe != "kontrolle"] for z in zustaende}
    kontrolle = {z: [b for b in je[z] if b.gruppe == "kontrolle"] for z in zustaende}
    k = {z: {"auftraege": kennzahlen(auftraege[z]), "je_gruppe": kennzahlen_je_gruppe(auftraege[z]),
             "kontrolle": kennzahlen(kontrolle[z])} for z in zustaende}
    verglichen = all(z in k for z in ZUSTAENDE)  # aus gegen an gibt es nur, wenn beide gemessen wurden
    daten = {
        "zeitpunkt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "art": "Textmessung des Persona-Schalters WAMO_PERSONA_ABNAHME, Werkzeuge als Attrappen, keine Sprachaufnahme",
        "modell": modell,
        "notiz": notiz,
        "hashes": hashes,
        "aufrufe": {
            "gesamt": sum(b.anfragen for b in befunde), "vorab": zaehler.vorab, "grenze": MAX_AUFRUFE,
            "zuege": len(befunde),
            "eingabe_tokens": sum(b.eingabe_gesamt for b in befunde), "ausgabe_tokens": sum(b.ausgabe_gesamt for b in befunde),
        },
        "regeln": {"kriterien_soll": list(KRITERIEN_SOLL), "max_zeichen": MAX_ZEICHEN, "alpha": ALPHA},
        "testaeusserungen": [{"id": f.id, "gruppe": f.kategorie, "text": f.eingabe} for f in faelle_liste],
        "kennzahlen": k,
        "vergleich": ({"auftraege": vergleich(k["aus"]["auftraege"], k["an"]["auftraege"], auftraege["aus"], auftraege["an"]),
                       "kontrolle": vergleich(k["aus"]["kontrolle"], k["an"]["kontrolle"], kontrolle["aus"], kontrolle["an"])}
                      if verglichen else {}),
        "kontrolle_je_fall": kontrolle_vergleichen(kontrolle["aus"], kontrolle["an"]) if verglichen else {},
        "zuege": [befund_dict(b) for b in befunde],
    }
    return json.loads(json.dumps(daten, ensure_ascii=False))


VORVERSUCH_KENNZAHLEN = ("aufruf", "gueltig", "kriterien_im_soll", "messbar", "mit_frage", "frage_ohne_auftrag",
                         "mehr_als_eine_frage", "ueber_max", "markdown", "laeufe_mit_befehl", "laeufe_mit_gefaehrlichem",
                         "nennt_ziel")


def befunde_aus_ablage(daten: dict) -> list[ZugBefund]:
    return [ZugBefund(**{k: tuple(v) if isinstance(v, list) else v for k, v in z.items()}) for z in daten["zuege"]]


def vorversuch_vergleichen(haupt: dict, vorschlag: dict) -> dict:
    """Der Vorversuch (nur einige Fälle, Zustand vorschlag) gegen aus und an der Hauptmessung, begrenzt auf dieselben Fälle."""
    ids = {z["id"] for z in vorschlag["zuege"]}
    je = {z: [b for b in befunde_aus_ablage(haupt) if b.zustand == z and b.id in ids] for z in ZUSTAENDE}
    je[VORSCHLAG] = befunde_aus_ablage(vorschlag)
    k = {z: kennzahlen(liste) for z, liste in je.items()}
    kennzahl: dict = {}
    for name in VORVERSUCH_KENNZAHLEN:
        a, v = k["an"][name], k[VORSCHLAG][name]
        kennzahl[name] = {"aus": k["aus"][name], "an": a, "vorschlag": v,
                          "p_an_gegen_vorschlag": round(fisher_zweiseitig(a["z"], a["n"], v["z"], v["n"]), 4),
                          "belastbar": belastbar(a["z"], a["n"], v["z"], v["n"])}
    werte = {name: {z: k[z][name] for z in k} for name in ("laenge_median", "laenge_max", "ttft_p50_s", "eingabe_median",
                                                          "anfragen", "eingabe_tokens", "ausgabe_tokens")}
    return {"faelle": sorted(ids), "laeufe": {z: len(liste) for z, liste in je.items()}, "kennzahlen": kennzahl, "werte": werte}


async def messen_alle(wiederholungen: int, faelle_liste: list, zustaende: tuple[str, ...], zaehler: Zaehler, pause: float,
                      modell: str, leise: bool = False, text: bool = False) -> list[ZugBefund]:
    from llm_anbieter import Wahl

    wahl = Wahl("gespraech", "claude", modell, "claude")
    befunde: list[ZugBefund] = []
    for lauf_nr in range(1, wiederholungen + 1):
        reihenfolge = list(faelle_liste)
        random.Random(lauf_nr).shuffle(reihenfolge)  # je Wiederholung eine Mischung, in beiden Zuständen dieselbe
        for zustand in (zustaende if lauf_nr % 2 else tuple(reversed(zustaende))):
            if zaehler.erreicht:
                print("Grenze der Aufrufe erreicht, keine weiteren Läufe.")
                return befunde
            print(f"Lauf {lauf_nr}, Zustand {zustand}: {len(reihenfolge)} Züge", flush=True)
            sammlung = Sammlung(zustand, lauf_nr, zaehler, leise, text)
            lauf = await rahmen().lauf_ausfuehren(
                wahl, reihenfolge, pause, 0.5, system=system_prompt(zustand), schemas=werkzeug_schemas(),
                werkzeug=werkzeug_attrappe, nach_zug=sammlung, aufwaermen=False)
            befunde += sammlung.befunde
            print(f"  Anfragen bisher: {zaehler.vorab + zaehler.aufrufe} von höchstens {MAX_AUFRUFE}", flush=True)
            if lauf.abbruch and "Aufrufgrenze" not in lauf.abbruch:
                print(f"Abbruch des Laufs: {lauf.abbruch}")
                return befunde
    return befunde


def speichern(daten: dict, pfad: Path) -> Path:
    from llm_anbieter import ausblenden_text

    pfad.parent.mkdir(parents=True, exist_ok=True)
    text = ausblenden_text(json.dumps(daten, ensure_ascii=False, indent=1))
    json.loads(text)  # das Ersetzen darf die Datei nicht beschädigen
    pfad.write_text(text + "\n", encoding="utf-8")
    return pfad


async def main() -> None:
    p = argparse.ArgumentParser(description="Messung des Persona-Schalters WAMO_PERSONA_ABNAHME (Textmessung)")
    p.add_argument("--wiederholungen", type=int, default=5, help="Läufe je Zustand")
    p.add_argument("--faelle", help="nur diese Fälle, kommagetrennt; Probelauf ohne Ablage")
    p.add_argument("--zustaende", default="aus,an", help="aus, an, vorschlag oder eine Liste davon")
    p.add_argument("--max-aufrufe", type=int, default=MAX_AUFRUFE, help="Grenze aller Anfragen an das Modell")
    p.add_argument("--vorab", type=int, default=0, help="Anfragen früherer Probeläufe, sie zählen zur Grenze")
    p.add_argument("--pause", type=float, default=0.5)
    p.add_argument("--ausgabe", default=str(ABLAGE))
    p.add_argument("--notiz", default="")
    p.add_argument("--text", action="store_true", help="gesprochenen Text auf der Konsole zeigen (nur mit --faelle)")
    p.add_argument("--speichern", action="store_true", help="auch einen Lauf mit --faelle ablegen (Vorversuch)")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from loguru import logger

    from llm_anbieter import claude_standardmodell, log_schutz_einrichten

    logger.remove()
    log_schutz_einrichten()
    logger.add(sys.stderr, level="WARNING", diagnose=False)
    zustaende = tuple(z.strip() for z in args.zustaende.split(",") if z.strip())
    if not zustaende or any(z not in ALLE_ZUSTAENDE for z in zustaende):
        p.error("--zustaende: aus, an, vorschlag oder eine Liste davon")
    auswahl = list(faelle_abnahme.FAELLE)
    if args.faelle:
        ids = {i.strip() for i in args.faelle.split(",")}
        auswahl = [f for f in auswahl if f.id in ids]
    modell = claude_standardmodell("gespraech")
    zaehler = Zaehler(grenze=args.max_aufrufe - RESERVE_JE_ZUG, vorab=args.vorab)
    if args.text and not args.faelle:
        p.error("--text nur zusammen mit --faelle (Probelauf): die Ablage enthält nie Antworttexte")
    befunde = await messen_alle(args.wiederholungen, auswahl, zustaende, zaehler, args.pause, modell, text=args.text)
    summe = {"anfragen": sum(b.anfragen for b in befunde), "eingabe": sum(b.eingabe_gesamt for b in befunde),
             "ausgabe": sum(b.ausgabe_gesamt for b in befunde)}
    print(f"Fertig: {len(befunde)} Züge, {summe['anfragen']} Anfragen, {summe['eingabe']} Eingabe-Tokens, "
          f"{summe['ausgabe']} Ausgabe-Tokens (Modell {modell}).")
    if args.faelle and not args.speichern:
        return
    daten = ablage_daten(befunde, auswahl, zaehler, modell=modell, hashes=hashes(), notiz=args.notiz)
    print(speichern(daten, Path(args.ausgabe)))


if __name__ == "__main__":
    asyncio.run(main())
