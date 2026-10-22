"""Bereinigung von Gerätedaten. Alles, was von einem Gerät kommt, ist unvertrauenswürdig: Namen, Meldungen, Dateinamen,
Dienstnamen können Text enthalten, der im Sprachmodell wie eine Anweisung wirkt. Deshalb nur strukturierte Werte,
enge Zeichensätze für Namen, kurze Texte ohne Steuer- und unsichtbare Zeichen."""

from __future__ import annotations

import re

_UNSICHTBAR = re.compile(r"[\x00-\x1f\x7f-\x9f­؜᠎​-‏ -‮⁠-⁯﻿￹-￻]")
_NAME_FREMD = re.compile(r"[^A-Za-z0-9 ._-]")
_LEER = re.compile(r"\s+")
_KLAMMERN = str.maketrans({"[": "(", "]": ")", "<": "(", ">": ")", "{": "(", "}": ")", "`": "'"})


def name(wert: object, grenze: int = 32) -> str:
    """Gerätename, Hostname, Dienstname: nur A-Z a-z 0-9 Leerzeichen . _ -, höchstens grenze Zeichen."""
    if not isinstance(wert, str):
        wert = "" if wert is None else str(wert)
    sauber = _LEER.sub(" ", _NAME_FREMD.sub("", _UNSICHTBAR.sub("", wert))).strip()
    return sauber[:grenze]


def text(wert: object, grenze: int = 200) -> str:
    """Kurzer Text vom Gerät (Statusmeldung): eine Zeile, ohne Steuer- und unsichtbare Zeichen, ohne Klammern, die eine
    Kennzeichnung im Kern schließen könnten, gekürzt."""
    if not isinstance(wert, str):
        wert = "" if wert is None else str(wert)
    sauber = _LEER.sub(" ", _UNSICHTBAR.sub(" ", wert)).strip().translate(_KLAMMERN)
    return sauber if len(sauber) <= grenze else sauber[: grenze - 1] + "…"


def dateiname(wert: object, grenze: int = 80) -> str:
    """Dateiname für Ablage und Anzeige: wie name(), zusätzlich ohne führende Punkte; leer wird "datei"."""
    sauber = name(wert, grenze).lstrip(". ")
    return sauber or "datei"


def zahl(wert: object, minimum: float | None = None, maximum: float | None = None) -> float | None:
    """Zahl vom Gerät oder None (keine bool, keine NaN, im Bereich)."""
    if isinstance(wert, bool):
        return None
    try:
        z = float(wert)
    except (TypeError, ValueError):
        return None
    if z != z or z in (float("inf"), float("-inf")):
        return None
    if (minimum is not None and z < minimum) or (maximum is not None and z > maximum):
        return None
    return z


def ganzzahl(wert: object, minimum: int | None = None, maximum: int | None = None) -> int | None:
    z = zahl(wert, minimum, maximum)
    return int(z) if z is not None else None
