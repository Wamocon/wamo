"""Konfiguration des Labor-Dienstes: nur Umgebungsvariablen und Dateien, nie Argumente.

Die Tokens liegen in Dateien (Rechte 0600 oder ein Docker-Secret), nie in der Umgebung, nie im Repo. Jede Fehlermeldung nennt die
Variable, nie einen Wert. Ein unbrauchbarer Token ist ein Fehler beim Start (der Dienst fährt nicht ohne Zugangsschutz hoch).
"""
from __future__ import annotations

import ipaddress
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{43,200}")  # 43 Zeichen sind 256 Bit in Base64
TOKEN_MIN_VERSCHIEDEN = 16  # ein Token aus wenigen Zeichen ist nie zufällig
STANDARD_BIS = date(2026, 12, 11)  # Aufbewahrung nach D10: 14 Tage nach dem Labortag
HOECHSTES_BIS = STANDARD_BIS  # später darf die Aufbewahrung nie enden (D10), eine Einstellung darf nur früher löschen
FRUEHESTES_BIS = date(2026, 11, 28)  # der Labortag: davor darf die Aufbewahrung nicht enden (sonst würden die Daten des Labortags gelöscht)
FRUEHESTES_DATUM = date(2026, 11, 12)  # Stand des Baus: Eine Uhr davor geht falsch (sonst würde die Aufbewahrung nie greifen)
PLATZ_MB_MIN, PLATZ_MB_MAX, PLATZ_MB_STANDARD = 2, 64, 16

V_DATEN = "WAMO_LABOR_DIENST_DATEN"
V_KERN = "WAMO_LABOR_DIENST_KERN_TOKEN_DATEI"
V_BETREUER = "WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI"
V_HOST = "WAMO_LABOR_DIENST_HOST"
V_PORT = "WAMO_LABOR_DIENST_PORT"
V_BIS = "WAMO_LABOR_DIENST_BIS"
V_PLATZ_MB = "WAMO_LABOR_DIENST_PLATZ_MB"
V_PROXYS = "WAMO_LABOR_DIENST_PROXYS"


class KonfigFehler(ValueError):
    """Eine Einstellung fehlt oder ist unbrauchbar. Die Meldung nennt die Variable, nie einen Wert."""


@dataclass(frozen=True)
class Konfig:
    daten: Path
    kern_token: str = field(repr=False)
    betreuer_token: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 8080
    bis: date = STANDARD_BIS
    platz_mb: int = PLATZ_MB_STANDARD
    proxys: frozenset[str] = frozenset()  # vertraute Proxys (Adressen): nur von ihnen zählt X-Forwarded-For

    @property
    def platz_max_bytes(self) -> int:
        return self.platz_mb * 1024 * 1024


def _ist_windows() -> bool:
    return sys.platform == "win32"


def _modus(pfad: str) -> int:
    return os.stat(pfad).st_mode


def token_aus_datei(pfad: str | None, variable: str) -> str:
    """Der Token aus einer UTF-8-Datei (BOM und Leerraum am Rand werden abgestreift): 43 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-, mindestens 16
    verschiedene Zeichen. Auf Linux darf die Datei nur dem Besitzer gehören (Rechte 0600), sonst liest der Dienst sie nicht."""
    if not pfad:
        raise KonfigFehler(f"{variable} fehlt (Pfad der Datei mit dem Token)")
    try:
        if not _ist_windows() and _modus(pfad) & 0o077:
            raise KonfigFehler(f"{variable}: die Datei darf nur dem Besitzer gehören (Rechte 0600), sie ist für Gruppe oder andere zugänglich")
        text = Path(pfad).read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        raise KonfigFehler(f"{variable}: die Datei kann nicht gelesen werden") from None
    if not TOKEN_RE.fullmatch(text) or len(set(text)) < TOKEN_MIN_VERSCHIEDEN:
        raise KonfigFehler(f"{variable}: der Token ist unbrauchbar (43 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-, mindestens "
                           f"{TOKEN_MIN_VERSCHIEDEN} verschiedene Zeichen)")
    return text


def _ganz(env: Mapping[str, str], name: str, standard: int, kleinster: int, groesster: int) -> int:
    roh = env.get(name)
    if roh is None:
        return standard
    if not roh.isascii() or not roh.isdigit() or not kleinster <= int(roh) <= groesster:
        raise KonfigFehler(f"{name} muss eine ganze Zahl von {kleinster} bis {groesster} sein")
    return int(roh)


def _datum(env: Mapping[str, str], name: str, standard: date) -> date:
    roh = env.get(name)
    if roh is None:
        return standard
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", roh):
            raise ValueError
        return date.fromisoformat(roh)
    except ValueError:
        raise KonfigFehler(f"{name} muss ein Datum der Form JJJJ-MM-TT sein") from None


def _proxys(env: Mapping[str, str]) -> frozenset[str]:
    roh = env.get(V_PROXYS)
    if roh is None:
        return frozenset()
    try:
        return frozenset(str(ipaddress.ip_address(teil.strip())) for teil in roh.split(","))
    except ValueError:
        raise KonfigFehler(f"{V_PROXYS} muss eine Liste von IP-Adressen sein (durch Komma getrennt)") from None


def aus_umgebung(env: Mapping[str, str] | None = None) -> Konfig:
    """Liest alle Einstellungen. env ist für Tests (Standard os.environ)."""
    env = os.environ if env is None else env
    kern = token_aus_datei(env.get(V_KERN), V_KERN)
    betreuer = token_aus_datei(env.get(V_BETREUER), V_BETREUER)
    if kern == betreuer:
        raise KonfigFehler(f"{V_KERN} und {V_BETREUER} müssen verschieden sein (getrennte Tokens, D8)")
    host = env.get(V_HOST, "127.0.0.1")
    if not host.strip():
        raise KonfigFehler(f"{V_HOST} darf nicht leer sein")
    bis = _datum(env, V_BIS, STANDARD_BIS)
    if not FRUEHESTES_BIS <= bis <= HOECHSTES_BIS:
        raise KonfigFehler(f"{V_BIS} muss zwischen {FRUEHESTES_BIS.isoformat()} (Labortag) und {HOECHSTES_BIS.isoformat()} liegen (Aufbewahrung nach D10)")
    return Konfig(
        daten=Path(env.get(V_DATEN) or "/data"), kern_token=kern, betreuer_token=betreuer, host=host.strip(),
        port=_ganz(env, V_PORT, 8080, 1, 65535), bis=bis,
        platz_mb=_ganz(env, V_PLATZ_MB, PLATZ_MB_STANDARD, PLATZ_MB_MIN, PLATZ_MB_MAX), proxys=_proxys(env),
    )
