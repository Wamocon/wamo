"""Alles, womit das Platz-Skript die Außenwelt berührt, an einer Stelle (nur Standardbibliothek).

`Umgebung` bündelt Registrierung, certutil, Ordner, Prozessstart, Verknüpfungen, Netzabruf, Uhr, Eingabe und Ausgabe. Im Betrieb baut
wamo_labor_platz_betrieb.echte_umgebung() die echten Teile, die Tests setzen Attrappen ein. So kann kein Test versehentlich den echten
Rechner ändern, und jede Außenwirkung ist an einer Stelle sichtbar.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from wamo_labor_platz_adressen import WEITER_PORT
from wamo_labor_platz_registrierung import Registrierung

WAMO_ORDNER = "WAMO"
PLATZ_DATEI = "platz.json"
SICHERUNG_DATEI = "platz-sicherung.json"
PROFIL_ORDNER = "wamo-chrome-labor"
VERKNUEPFUNG_STARTEN = "WAMO Labor starten.lnk"
VERKNUEPFUNG_PRUEFEN = "WAMO Labor pruefen.lnk"


@dataclass(frozen=True)
class Abruf:
    status: int
    datum: str | None  # Kopf Date der Antwort, falls vorhanden


class AbrufFehler(Exception):
    """Verbindung oder TLS-Prüfung gescheitert. `art` ist "tls", "frist" oder "verbindung", `text` ohne Zugangsdaten."""

    def __init__(self, art: str, text: str) -> None:
        super().__init__(text)
        self.art, self.text = art, text


@dataclass
class Umgebung:
    reg: Registrierung
    certutil: Callable[[list[str]], tuple[int, str]]
    lokal: Path  # %LOCALAPPDATA%
    desktop: Path  # Ordner der Verknüpfungen
    skript: Path  # Pfad von wamo_labor_platz.py (Ziel der Verknüpfungen)
    python: str  # Pfad der Python-Datei, mit der eingerichtet wurde
    chrome_kandidaten: tuple[Path, ...]
    ca_kandidaten: tuple[Path, ...]
    prozess_starten: Callable[[list[str]], None]
    verknuepfung: Callable[[Path, str, str, str], None]  # Pfad, Ziel, Argumente, Arbeitsordner
    abruf: Callable[[str, Path], Abruf]  # Adresse, CA-Datei
    jetzt: Callable[[], float]  # Sekunden seit 1970 (UTC)
    token_eingabe: Callable[[], str]
    warten: Callable[[float], None]
    arbeitsbereich: Callable[[], tuple[int, int, int, int]]  # x, y, Breite, Höhe ohne Taskleiste
    ausgabe: Callable[[str], None]
    weiter_port: int = WEITER_PORT
    weiter_frist_s: float = 10.0
    chrome_wartezeit_s: float = 1.5
    taste: Callable[[], None] = lambda: None  # wartet auf Enter, damit ein Fenster einer Verknüpfung nicht sofort schließt

    @property
    def platz_ordner(self) -> Path:
        return self.lokal / WAMO_ORDNER

    @property
    def platz_datei(self) -> Path:
        return self.platz_ordner / PLATZ_DATEI

    @property
    def sicherung_datei(self) -> Path:
        return self.platz_ordner / SICHERUNG_DATEI

    @property
    def profil(self) -> Path:
        return self.lokal / PROFIL_ORDNER

    @property
    def verknuepfungen(self) -> tuple[Path, Path]:
        return self.desktop / VERKNUEPFUNG_STARTEN, self.desktop / VERKNUEPFUNG_PRUEFEN


def echte_umgebung() -> Umgebung:
    """Die echte Umgebung für den Betrieb (Windows). Die Tests ersetzen diese Funktion durch eine Falle."""
    from wamo_labor_platz_betrieb import echte_umgebung as bauen
    return bauen()
