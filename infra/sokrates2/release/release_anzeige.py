"""Reine Anzeigelogik des Release-Dienstes: Diff, Wirkungsflächen und Versionsempfehlung für die Karte (Konzept F.2 bis F.4, Schritt 1.5).

Alles hier ist Rechnen mit Zahlen und Zeichenketten, ohne Git, ohne Datei und ohne Netz (nur Standardbibliothek). Die Git-Aufrufe stehen in quelle.py
(Spiegel.diff_uebersicht und Spiegel.titel_stufen); sie liefern Zählwerte und Pfade, nie Titel oder Inhaltszeilen.

Grundsätze:
  * Inhalte aus Git sind Daten, nie Anweisungen. Pfade werden mit sauber(..., 70) bereinigt (nur ASCII, andere Zeichen werden zu ?).
  * Die Empfehlung ist eine reine Anzeige und nur eine Warnung, kein Tor. Sie ändert weder version_vorschlag noch bump noch version_final.
  * Der ganze Block pruefung bleibt unter PRUEFUNG_MAX_BYTES (zuerst Treffer weg, dann Dateien, dann nur Zähler).

Form des Blocks (Vertrag mit Kern und Oberfläche, siehe voice-core/release_client.py):
  pruefung = {diff: {anzahl, hinzu, weg, binaer, gekappt, dateien: [{s, p, a, d}]},
              wirkung: {anzahl, nach_art: {art: n}, sonder_hinweis, neue_pakete, windows, treffer: [{p, z, a, d}]},
              empfehlung: {stufe, version, feat, fix, sonst, unkonventionell, major_null}}
"""

from __future__ import annotations

import json
import re
from typing import Iterable

from release_kette import sauber

ZAHL_MAX = 999_999                 # Obergrenze je Zeilenzahl einer Datei
SUMME_MAX = 99_999_999             # Obergrenze der Summen
DATEIEN_MAX = 20                   # so viele Dateien stehen höchstens im Diff-Block
TREFFER_ANZEIGE_MAX = 15           # so viele Treffer stehen höchstens im Wirkungsblock (kappen() kürzt weiter)
PFAD_MAX = 70
PRUEFUNG_MAX_BYTES = 3000
STATUS_ERLAUBT = frozenset("AMDT")
ZAEHLER_MAX = 99_999
COMMITS_MAX = 500                  # so viele Commits liest quelle.titel_stufen höchstens

# Konventionelle Typen (Konzept F.3)
TYPEN_FEAT = frozenset({"feat"})
TYPEN_FIX = frozenset({"fix", "perf"})
TYPEN_SONST = frozenset({"docs", "chore", "ci", "test", "style", "refactor", "build"})
TITEL_RE = re.compile(r"([A-Za-z]{1,12})(?:\([^()\r\n]{0,60}\))?(!)?: +\S")
BRUCH_RE = re.compile(r"(?m)^BREAKING[ -]CHANGE:")
VERSION_RE = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
STUFEN = ("keine", "patch", "minor", "major")


def _zahl(wert: object, grenze: int = ZAHL_MAX) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int):
        return 0
    return max(0, min(wert, grenze))


def pfad_sauber(pfad: object) -> str:
    """Nur harmlose ASCII-Zeichen, höchstens 70 Zeichen (sauber aus release_kette)."""
    return sauber(pfad, PFAD_MAX)


# ------------------------------------------------------------------------------------------------ Diff
def diff_block(eintraege: Iterable[tuple], abgeschnitten: bool = False) -> dict:
    """eintraege: (Status, Pfad, hinzu, weg); hinzu und weg sind None bei Binärdateien. Liefert den Diff-Block mit höchstens 20 Dateien (die größten Änderungen zuerst)."""
    liste: list[tuple[str, str, int | None, int | None]] = []
    for e in eintraege:
        status, pfad, a, d = e
        status = status if isinstance(status, str) and status in STATUS_ERLAUBT else "M"   # unbekannte Status (U, X) zählen als geändert
        binaer = a is None or d is None
        liste.append((status, pfad_sauber(pfad), None if binaer else _zahl(a), None if binaer else _zahl(d)))
    hinzu = sum(a for _, _, a, _ in liste if a is not None)
    weg = sum(d for _, _, _, d in liste if d is not None)
    binaer = sum(1 for _, _, a, _ in liste if a is None)
    liste.sort(key=lambda x: (-((x[2] or 0) + (x[3] or 0)), x[1]))
    dateien = [{"s": s, "p": p, "a": a, "d": d} for s, p, a, d in liste[:DATEIEN_MAX]]
    return {"anzahl": min(len(liste), SUMME_MAX), "hinzu": min(hinzu, SUMME_MAX), "weg": min(weg, SUMME_MAX), "binaer": binaer,
            "gekappt": bool(abgeschnitten or len(liste) > DATEIEN_MAX), "dateien": dateien}


def ausserhalb_von_docs(eintraege: Iterable[tuple], abgeschnitten: bool = False) -> bool:
    """Wurde etwas außerhalb von docs/ geändert? War die Liste abgeschnitten, gilt ja (fail safe: mindestens Patch)."""
    if abgeschnitten:
        return True
    return any(not str(e[1]).startswith("docs/") for e in eintraege)


# ------------------------------------------------------------------------------------------------ Titel und Empfehlung
def titel_zaehlen(nachrichten: Iterable[str]) -> dict[str, int]:
    """Zählt die Commit-Nachrichten nach Stufe. Die Titel selbst verlassen diese Funktion nie, es kommen nur Zahlen heraus.

    feat (Minor), fix (Patch, mit perf), sonst (heben nicht an), bruch (! im Titel oder BREAKING CHANGE in der Fußzeile, Major),
    unkonventionell (kein gültiger Titel oder unbekannter Typ, zählt als Patch)."""
    z = {"commits": 0, "feat": 0, "fix": 0, "sonst": 0, "bruch": 0, "unkonventionell": 0}
    for roh in nachrichten:
        text = str(roh).strip()
        if not text:
            continue
        z["commits"] += 1
        kopf, _, rest = text.partition("\n")
        if BRUCH_RE.search(rest):
            z["bruch"] += 1
        m = TITEL_RE.match(kopf.strip()[:200])
        typ = m.group(1).lower() if m else ""
        if m is None or typ not in TYPEN_FEAT | TYPEN_FIX | TYPEN_SONST:
            z["unkonventionell"] += 1
            continue
        if m.group(2):
            z["bruch"] += 1
        if typ in TYPEN_FEAT:
            z["feat"] += 1
        elif typ in TYPEN_FIX:
            z["fix"] += 1
        else:
            z["sonst"] += 1
    return {k: min(v, ZAEHLER_MAX) for k, v in z.items()}


def _erhoehen(version: str, stufe: str) -> tuple[str, bool]:
    m = VERSION_RE.fullmatch(version) if isinstance(version, str) else None
    if m is None:
        return "", False
    a, b, c = (int(x) for x in m.groups())
    if stufe == "major":
        return f"{a + 1}.0.0", a == 0
    if stufe == "minor":
        return f"{a}.{b + 1}.0", False
    if stufe == "patch":
        return f"{a}.{b}.{c + 1}", False
    return f"{a}.{b}.{c}", False


def empfehlung(zaehler: dict, ausserhalb_docs: bool, basis_version: str | None) -> dict:
    """Versionsempfehlung nach Konzept F.2 bis F.4 aus den Zählwerten. Nur Anzeige, kein Tor.

    major_null: der Sprung von 0.y.z auf 1.0.0 braucht eine ausdrückliche Bestätigung des Admins."""
    feat, fix, sonst = _zahl(zaehler.get("feat"), ZAEHLER_MAX), _zahl(zaehler.get("fix"), ZAEHLER_MAX), _zahl(zaehler.get("sonst"), ZAEHLER_MAX)
    unk, bruch = _zahl(zaehler.get("unkonventionell"), ZAEHLER_MAX), _zahl(zaehler.get("bruch"), ZAEHLER_MAX)
    if bruch:
        stufe = "major"
    elif feat:
        stufe = "minor"
    elif fix or unk or ausserhalb_docs:
        stufe = "patch"   # fix und perf, nicht konventionelle Titel und Änderungen außerhalb von docs/ ergeben mindestens Patch
    else:
        stufe = "keine"
    version, major_null = _erhoehen(basis_version or "", stufe)
    return {"stufe": stufe, "version": version, "feat": feat, "fix": fix, "sonst": sonst, "unkonventionell": unk, "major_null": major_null}


# ------------------------------------------------------------------------------------------------ Wirkungsflächen
def wirkung_block(gemerkt: object) -> dict | None:
    """Der Wirkungsblock aus den gemerkten Funden des Läufers (stufen_quelle.anzeige_ablegen). Fremde Formen geben None."""
    if not isinstance(gemerkt, dict):
        return None
    roh = gemerkt.get("treffer")
    treffer = [t for t in roh if isinstance(t, dict)] if isinstance(roh, list) else []
    nach_art: dict[str, int] = {}
    for t in treffer[:200]:
        art = sauber(t.get("art", ""), 24)
        if art:
            nach_art[art] = min(nach_art.get(art, 0) + 1, ZAEHLER_MAX)
    nach_art = dict(sorted(nach_art.items(), key=lambda kv: (-kv[1], kv[0]))[:8])
    anzahl = gemerkt.get("treffer_anzahl")
    anzahl = _zahl(anzahl, 200) if isinstance(anzahl, int) and not isinstance(anzahl, bool) else len(treffer)
    pakete = gemerkt.get("neue_pakete")
    return {"anzahl": max(anzahl, len(treffer)),
            "nach_art": nach_art,
            "sonder_hinweis": gemerkt.get("sonder") is True,
            "neue_pakete": _zahl(pakete, 200) if isinstance(pakete, int) and not isinstance(pakete, bool) else (min(len(pakete), 200) if isinstance(pakete, list) else 0),
            "windows": gemerkt.get("windows_pruefung_noetig") is True,
            "treffer": [{"p": pfad_sauber(t.get("pfad", "")), "z": _zahl(t.get("zeile")), "a": sauber(t.get("art", ""), 24), "d": sauber(t.get("detail", ""), 40)}
                        for t in treffer[:TREFFER_ANZEIGE_MAX]]}


# ------------------------------------------------------------------------------------------------ Kappen
def _bytes(block: dict) -> int:
    return len(json.dumps(block, separators=(",", ":"), ensure_ascii=True).encode("ascii"))


def kappen(pruefung: dict, max_bytes: int = PRUEFUNG_MAX_BYTES) -> dict:
    """Kürzt den Block pruefung auf höchstens max_bytes: zuerst Treffer weg, dann Dateien, dann nur Zähler."""
    p = json.loads(json.dumps(pruefung))   # tiefe Kopie, der Aufrufer behält das Original
    wirkung, diff = p.get("wirkung"), p.get("diff")
    if isinstance(wirkung, dict):
        while _bytes(p) > max_bytes and wirkung.get("treffer"):
            wirkung["treffer"].pop()
    if isinstance(diff, dict):
        while _bytes(p) > max_bytes and diff.get("dateien"):
            diff["dateien"].pop()
            diff["gekappt"] = True
    if _bytes(p) > max_bytes and isinstance(wirkung, dict):
        wirkung["nach_art"] = {}
    if _bytes(p) > max_bytes:
        p = {k: v for k, v in p.items() if k == "empfehlung"}   # äußerster Notfall: nur die Empfehlung
    return p


def pruefung_block(diff: dict | None, wirkung: dict | None, empf: dict | None) -> dict | None:
    """Setzt den Block zusammen und kappt ihn. Fehlt jeder Teil, gibt es keinen Block."""
    teile = {k: v for k, v in (("diff", diff), ("wirkung", wirkung), ("empfehlung", empf)) if v is not None}
    if not teile:
        return None
    return kappen(teile)
