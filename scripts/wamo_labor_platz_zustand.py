"""Platzdatei und Sicherung des Platz-Skripts (nur Standardbibliothek).

Die Platzdatei %LOCALAPPDATA%\\WAMO\\platz.json trägt die Platznummer und die festen Adressen, nie ein Token. Die Sicherung
%LOCALAPPDATA%\\WAMO\\platz-sicherung.json hält den Zustand VOR der ersten Änderung: vorige Richtlinienwerte, ob die CA schon im Speicher
war, ob Ordner und Verknüpfungen schon bestanden. Sie wird vor jeder Änderung geschrieben und danach nie überschrieben, außer um neue
Einträge zu ergänzen (der erste Stand gewinnt). So führt der Rückweg auch nach mehreren Läufen zum echten Ausgangszustand.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from wamo_labor_platz_adressen import BOARD_URL, WAMO_URL
from wamo_labor_platz_registrierung import CHROME_PFAD, GOOGLE_PFAD, HIVE, Richtlinie, richtlinien, zustand_sichern
from wamo_labor_platz_umgebung import Umgebung

PLATZ_MIN, PLATZ_MAX = 1, 16  # der Dienst führt 16 Plätze, 15 und 16 sind für die Abnahme des Dienstes reserviert
SICHERUNG_VERSION = 1
_LISTEN_ABSCHNITTE = ("richtlinien", "verknuepfungen")
_SHA1_FORM = re.compile(r"[0-9a-f]{40}")
MAX_TEXT = 200_000  # größter Inhalt einer vorigen platz.json, den die Sicherung aufnimmt


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


def _ist_int(wert: object) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool)


def _platz_pruefen(umg: Umgebung, daten: dict) -> None:
    if not _ist_int(daten["version"]) or daten["version"] != 1:
        raise ZustandFehler("platz.json: unbekannte Fassung der Datei")
    if not _ist_int(daten["platz"]) or not PLATZ_MIN <= daten["platz"] <= PLATZ_MAX:
        raise ZustandFehler("platz.json: ungültige Platznummer")
    if daten["wamo_url"] != WAMO_URL or daten["board_url"] != BOARD_URL:
        raise ZustandFehler("platz.json: Die Adressen gehören nicht zu den festen Adressen des Labors (Datei verändert?)")
    sha1 = daten["ca_sha1"]
    if not isinstance(sha1, str) or not _SHA1_FORM.fullmatch(sha1) or sha1 != umg.ca_sha1:
        raise ZustandFehler("platz.json: ca_sha1 ist nicht der Fingerabdruck der WAMO-CA (Datei verändert?)")
    start = daten["start"]
    if not isinstance(daten["profil"], str) or not isinstance(start, dict) or not all(isinstance(start[k], str) for k in ("python", "skript")):
        raise ZustandFehler("platz.json: Profil oder Startangaben fehlen oder haben die falsche Form")


def platz_lesen(umg: Umgebung) -> dict | None:
    """Die Platzdatei, vollständig geprüft (Schlüssel, Typen, Nummer, feste Adressen, Fingerabdruck der CA). Nie ein KeyError oder
    TypeError: Jede Abweichung ist ein ZustandFehler mit Text. Sonst ginge das Token an einen Ort, den die Datei bestimmt."""
    daten = json_lesen(umg.platz_datei)
    if daten is None:
        return None
    try:
        _platz_pruefen(umg, daten)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ZustandFehler("platz.json hat nicht den erwarteten Aufbau (Datei verändert?)") from None
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


def _sicherung_eintrag_pruefen(eintrag: dict) -> None:
    if not isinstance(eintrag["da"], bool) or eintrag["typ"] not in ("dword", "sz", "liste"):
        raise ValueError("Eintrag")
    if not eintrag["da"]:
        return
    wert, typ = eintrag["wert"], eintrag["typ"]
    if not ((typ == "dword" and _ist_int(wert)) or (typ == "sz" and isinstance(wert, str))
            or (typ == "liste" and isinstance(wert, list) and all(isinstance(w, str) for w in wert))):
        raise ValueError("Wert")


def _sicherung_pruefen(umg: Umgebung, daten: dict) -> None:
    if not _ist_int(daten["version"]) or daten["version"] != SICHERUNG_VERSION:
        raise ValueError("version")
    bekannt = {r.name for r in richtlinien()}
    for name, eintrag in daten["richtlinien"].items():
        if name not in bekannt or not isinstance(eintrag, dict):
            raise ValueError("richtlinien")
        _sicherung_eintrag_pruefen(eintrag)
    ca = daten["ca"]
    if not isinstance(ca["sha1"], str) or not _SHA1_FORM.fullmatch(ca["sha1"]) or ca["sha1"] != umg.ca_sha1:
        raise ValueError("ca.sha1")
    vorher = daten["platz_json_vorher"]
    wahrheitswerte = (ca["vorher_vorhanden"], daten["wamo_ordner_vorher"], daten["profil_vorher"], *daten["schluessel"].values(),
                      daten["schluessel"]["google"], daten["schluessel"]["chrome"], *daten["verknuepfungen"].values())
    if not all(isinstance(w, bool) for w in wahrheitswerte) or not all(isinstance(k, str) for k in daten["verknuepfungen"]):
        raise ValueError("wahrheitswerte")
    if vorher is not None and (not isinstance(vorher, str) or len(vorher) > MAX_TEXT):
        raise ValueError("platz_json_vorher")


def sicherung_lesen(umg: Umgebung) -> dict | None:
    """Die Sicherung, vollständig geprüft. Eine beschädigte oder veränderte Datei ist ein ZustandFehler: Es wird nie ein Wert daraus
    benutzt, bevor er geprüft ist (der Fingerabdruck der CA muss der festgelegte sein, bevor etwas aus dem Speicher entfernt wird)."""
    text = f"Die Sicherung {umg.sicherung_datei.name} ist beschädigt oder verändert"
    try:
        daten = json_lesen(umg.sicherung_datei)
    except ZustandFehler:
        raise ZustandFehler(f"{text} (kein gültiges JSON-Objekt)") from None
    if daten is None:
        return None
    try:
        _sicherung_pruefen(umg, daten)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ZustandFehler(f"{text} ({e if isinstance(e, ValueError) else 'Aufbau'})") from None
    return daten


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
