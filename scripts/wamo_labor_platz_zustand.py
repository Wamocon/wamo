"""Platzdatei und Sicherung des Platz-Skripts (nur Standardbibliothek).

Die Platzdatei %LOCALAPPDATA%\\WAMO\\platz.json trägt die Platznummer und die festen Adressen, nie ein Token. Die Sicherung
%LOCALAPPDATA%\\WAMO\\platz-sicherung.json hält den Zustand VOR der ersten Änderung: vorige Richtlinienwerte, ob die CA schon im Speicher
war, ob Ordner und Verknüpfungen schon bestanden. Sie wird vor jeder Änderung geschrieben und danach nie überschrieben, außer um neue
Einträge zu ergänzen (der erste Stand gewinnt). So führt der Rückweg auch nach mehreren Läufen zum echten Ausgangszustand.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from wamo_labor_platz_adressen import BOARD_URL, WAMO_URL, adresse_zulaessig
from wamo_labor_platz_registrierung import CHROME_PFAD, GOOGLE_PFAD, HIVE, Richtlinie, zustand_sichern
from wamo_labor_platz_umgebung import Umgebung

PLATZ_MIN, PLATZ_MAX = 1, 16  # der Dienst führt 16 Plätze, 15 und 16 sind für die Abnahme des Dienstes reserviert
SICHERUNG_VERSION = 1
_LISTEN_ABSCHNITTE = ("richtlinien", "verknuepfungen")


class ZustandFehler(Exception):
    """Platzdatei oder Sicherung sind unbrauchbar (Text für die Person)."""


def json_lesen(pfad: Path) -> dict | None:
    """None, wenn die Datei fehlt. Eine unlesbare oder fremd aufgebaute Datei ist ein Fehler, nie ein stiller Ersatz."""
    try:
        text = pfad.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        raise ZustandFehler(f"Datei nicht lesbar: {pfad}") from None
    try:
        daten = json.loads(text)
    except ValueError:
        raise ZustandFehler(f"Datei ist kein gültiges JSON: {pfad}") from None
    if not isinstance(daten, dict):
        raise ZustandFehler(f"Datei hat nicht den erwarteten Aufbau: {pfad}")
    return daten


def json_text(daten: dict) -> str:
    return json.dumps(daten, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def datei_schreiben(pfad: Path, text: str) -> bool:
    """Schreibt atomar (Wegwerf-Datei, dann ersetzen, LF). Gibt False zurück, wenn der Inhalt schon genau so da ist."""
    try:
        if pfad.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    pfad.parent.mkdir(parents=True, exist_ok=True)
    temp = pfad.with_name(pfad.name + ".neu")
    with open(temp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(temp, pfad)
    return True


def platz_inhalt(umg: Umgebung, platz: int, ca_sha1: str) -> dict:
    return {"version": 1, "platz": platz, "wamo_url": WAMO_URL, "board_url": BOARD_URL, "ca_sha1": ca_sha1,
            "profil": str(umg.profil), "start": {"python": umg.python, "skript": str(umg.skript)}}


def platz_lesen(umg: Umgebung) -> dict | None:
    """Die Platzdatei, geprüft: Nummer im Bereich und beide Adressen aus der festen Liste (sonst ginge das Token an einen fremden Ort)."""
    daten = json_lesen(umg.platz_datei)
    if daten is None:
        return None
    platz = daten.get("platz")
    if not isinstance(platz, int) or isinstance(platz, bool) or not PLATZ_MIN <= platz <= PLATZ_MAX:
        raise ZustandFehler("platz.json: ungültige Platznummer")
    for schluessel in ("wamo_url", "board_url"):
        if not adresse_zulaessig(daten.get(schluessel)):
            raise ZustandFehler(f"platz.json: {schluessel} gehört nicht zu den festen Adressen des Labors (Datei verändert?)")
    return daten


def sicherung_erfassen(umg: Umgebung, richtlinien: tuple[Richtlinie, ...], sha1: str, ca_vorhanden: bool) -> dict:
    """Der Zustand jetzt, vor jeder Änderung. Nur lesen."""
    try:
        vorher_text = umg.platz_datei.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        vorher_text = None
    return {
        "version": SICHERUNG_VERSION,
        "richtlinien": {r.name: zustand_sichern(umg.reg, r) for r in richtlinien},
        "schluessel": {"google": umg.reg.schluessel_vorhanden(HIVE, GOOGLE_PFAD), "chrome": umg.reg.schluessel_vorhanden(HIVE, CHROME_PFAD)},
        "ca": {"sha1": sha1, "vorher_vorhanden": ca_vorhanden},
        "platz_json_vorher": vorher_text,
        "wamo_ordner_vorher": umg.platz_ordner.exists(),
        "verknuepfungen": {p.name: p.exists() for p in umg.verknuepfungen},
        "profil_vorher": umg.profil.exists(),
    }


def sicherung_zusammenfuehren(alt: dict | None, neu: dict) -> dict:
    """Der erste Stand gewinnt: Vorhandenes bleibt, nur fehlende Einträge kommen dazu."""
    if alt is None:
        return neu
    ergebnis = dict(alt)
    for schluessel, wert in neu.items():
        if schluessel in _LISTEN_ABSCHNITTE and isinstance(alt.get(schluessel), dict):
            ergebnis[schluessel] = {**wert, **alt[schluessel]}
        else:
            ergebnis.setdefault(schluessel, wert)
    return ergebnis
