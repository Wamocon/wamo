"""Rohdaten eines Browser-Schritts in Kennzahlen umrechnen (scripts/wamo_labor_last.py, nur Standardbibliothek).

Der Browser (scripts/wamo-last-browser.cjs) meldet je Schritt nur Rohdaten auf seiner Audio-Uhr: Anfang und Ende der Sprechprobe, die Blöcke, in
denen WAMO hörbar war, und einen eventuellen Einwurf. Die Kennzahlen entstehen hier, damit sie ohne Browser prüfbar sind. Die Definitionen folgen
scripts/wamo_stimme.py (erster Ton, Verstummen), ohne dessen Abhängigkeit von numpy:

  erster Ton   Beginn des ersten hörbaren Abschnitts nach dem Ende der Sprechprobe (50 ms Toleranz davor), in Millisekunden
  Antwortdauer vom ersten Ton bis zu einer Stille von mindestens 2,5 s
  Verstummen   vom Einwurf bis zum Ende der Wiedergabe, die mindestens 0,9 s still bleibt (Pausen in Sätzen sind kürzer)
  verpasst     WAMO sprach nach dem Einwurf länger als 4 s weiter oder verstummte nie
  falsche Unterbrechung  ein Schnitt der Wiedergabe in der Oberfläche (data-cut), den kein Einwurf des Werkzeugs erklärt
"""
from __future__ import annotations

import math
from typing import Sequence

from wamo_labor_last_auswertung import SchrittErgebnis
from wamo_labor_last_plan import EingabeFehler, SchrittPlan

LUECKE_S = 0.8  # Pausen bis dahin gehören zur selben Rede (Satzpausen der Stimme liegen bei 0,4 bis 0,65 s)
ANTWORT_ENDE_S = 2.5
STILLE_ENDE_S = 0.9
VERPASST_NACH_S = 4.0
TOLERANZ_S = 0.05
MAX_ABBRUCH_ZEICHEN = 80


def _zahl(wert: object) -> bool:
    return type(wert) in (int, float) and math.isfinite(wert)


def segmente(bloecke: Sequence) -> list[tuple[float, float]]:
    """Hörbare Abschnitte aus den aktiven Blöcken: sortiert, Lücken unter 0,8 s verbunden. Unbrauchbare Einträge fallen weg."""
    geordnet = sorted((float(b[0]), float(b[1])) for b in bloecke
                      if isinstance(b, (list, tuple)) and len(b) == 2 and _zahl(b[0]) and _zahl(b[1]) and b[1] >= b[0])
    aus: list[list[float]] = []
    for anfang, ende in geordnet:
        if aus and anfang - aus[-1][1] < LUECKE_S:
            aus[-1][1] = max(aus[-1][1], ende)
        else:
            aus.append([anfang, ende])
    return [(a, e) for a, e in aus]


def _erster_ton(segs: list[tuple[float, float]], ab: float) -> tuple[float, float] | None:
    return next(((a, e) for a, e in segs if a >= ab - TOLERANZ_S), None)


def _antwort_ende(segs: list[tuple[float, float]], erster: tuple[float, float]) -> float:
    ende = erster[1]
    for a, e in segs:
        if a >= erster[0] and a - ende < ANTWORT_ENDE_S:
            ende = max(ende, e)
        elif a > ende:
            break
    return ende


def _verstummen(segs: list[tuple[float, float]], ab: float, ende_t: float) -> float | None:
    """Ende der ersten Wiedergabe nach ab, auf die mindestens 0,9 s Stille folgen (None: nie). Nach dem letzten Abschnitt gilt das Ende
    der Aufnahme als Beobachtungsgrenze: Spricht WAMO bis dahin noch, ist es nicht verstummt."""
    for i, (_, ende) in enumerate(segs):
        if ende <= ab:
            continue
        folgt = segs[i + 1][0] if i + 1 < len(segs) else ende_t
        if folgt - ende >= STILLE_ENDE_S:
            return ende
    return None


def _einwurf(segs: list[tuple[float, float]], roh_einwurf: object, ende_t: float) -> tuple[bool, float | None, bool]:
    """(ausgelöst, Verstummen in ms, verpasst)."""
    if not isinstance(roh_einwurf, dict) or not _zahl(roh_einwurf.get("t0")):
        return False, None, False
    onset = float(roh_einwurf["t0"])
    ende = _verstummen(segs, onset, ende_t)
    if ende is None:
        return True, None, True
    dauer = max(0.0, ende - onset)
    return True, round(dauer * 1000), dauer > VERPASST_NACH_S


def schritt_auswerten(roh: object, plan: SchrittPlan, sitzung: int, kennung: str, beginn_s: float) -> SchrittErgebnis:
    """Die Kennzahlen eines Schritts. Rohdaten ohne die erwarteten Felder sind ein Fehler im Werkzeug und werden nie als Messwert gelesen."""
    if not isinstance(roh, dict) or not _zahl(roh.get("clip_ende")) or not isinstance(roh.get("bot"), list):
        raise EingabeFehler("Rohdaten des Browsers unbrauchbar (clip_ende oder bot fehlen)")
    segs = segmente(roh["bot"])
    ende_clip = float(roh["clip_ende"])
    erster = _erster_ton(segs, ende_clip)
    ende_t = float(roh["ende_t"]) if _zahl(roh.get("ende_t")) else (segs[-1][1] if segs else ende_clip)
    ausgeloest, verstummen_ms, verpasst = _einwurf(segs, roh.get("einwurf"), ende_t)
    geschnitten = roh.get("geschnitten") if type(roh.get("geschnitten")) is int else 0
    return SchrittErgebnis(
        sitzung=sitzung, kennung=kennung, karte=plan.karte, beginn_s=beginn_s,
        erster_ton_ms=None if erster is None else round((erster[0] - ende_clip) * 1000, 1),
        antwort_s=None if erster is None else round(_antwort_ende(segs, erster) - erster[0], 1),
        verstummen_ms=verstummen_ms, einwurf=plan.einwurf, einwurf_ausgeloest=ausgeloest, verpasst=verpasst,
        falsche_unterbrechungen=max(0, geschnitten - (1 if ausgeloest else 0)),
        abbruch=str(roh.get("abbruch") or "")[:MAX_ABBRUCH_ZEICHEN], zeitueberschreitung=bool(roh.get("zeitueberschreitung")),
        freigabe_geklickt=roh.get("freigabe") == "freigegeben")


def browser_zaehler(rohdaten: Sequence[dict]) -> tuple[int, int]:
    """Summe der 429-Antworten und der Konsolenfehler, die der Browser in den Schritten sah."""
    return (sum(r.get("r429", 0) for r in rohdaten if type(r.get("r429")) is int),
            sum(r.get("konsole", 0) for r in rohdaten if type(r.get("konsole")) is int))
