"""Einstieg: python -m wamo_labor_dienst (Dienst starten) und python -m wamo_labor_dienst gesundheit (Healthcheck des Containers).

Der Dienst liest nur Umgebungsvariablen und Dateien (konfig.py). Das Zugriffsprotokoll des Webservers ist aus: Es enthielte Abfragen mit Suchtext.
Ein Fehler der Einstellung beendet den Start mit Ausgang 2 und einer Meldung ohne Werte.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import urllib.error
import urllib.request

import uvicorn

from .app import create_app
from .konfig import KonfigFehler, aus_umgebung


def uvicorn_einstellungen() -> dict:
    """Einstellungen des Webservers (auch die Integrationstests starten mit ihnen): h11, kein Zugriffsprotokoll, kein Server-Kopf, keine Weiterleitungs-
    köpfe, Köpfe bis 128 KB werden gelesen (der Dienst lehnt über 16 KB mit 431 und einer Fehlerantwort des Vertrags ab, erst darüber lehnt h11
    mit 400 ab, das einen bekannten Randfall von uvicorn auslöst: eine Ausnahme im Ereignisschleifen-Rückruf, der Dienst läuft weiter)."""
    return {"log_level": "warning", "access_log": False, "http": "h11", "server_header": False, "proxy_headers": False,
            "h11_max_incomplete_event_size": 128 * 1024, "timeout_keep_alive": 5}


def gesundheit_pruefen() -> int:
    """Fragt den lokalen Dienst nach /gesundheit. Ausgang 0 nur bei 200 mit dem Zustand ok."""
    port = os.environ.get("WAMO_LABOR_DIENST_PORT", "8080")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/gesundheit", timeout=3) as r:  # noqa: S310 (feste lokale Adresse)
            return 0 if r.status == 200 and b'"ok"' in r.read() else 1
    except (OSError, ValueError, urllib.error.URLError):
        return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["gesundheit"]:
        return gesundheit_pruefen()
    try:
        konfig = aus_umgebung()
    except KonfigFehler as e:
        print(f"Konfiguration: {e}", file=sys.stderr)
        return 2
    try:
        app = create_app(konfig)
    except (OSError, sqlite3.Error) as e:  # Datenordner nicht beschreibbar oder Datenbank unbrauchbar: klare Meldung statt Stapelspur
        print(f"Speicher nicht nutzbar ({type(e).__name__}): WAMO_LABOR_DIENST_DATEN muss ein beschreibbarer Ordner sein.", file=sys.stderr)
        return 3
    print(f"WAMO Labor-Dienst lauscht auf {konfig.host}:{konfig.port}", flush=True)
    uvicorn.run(app, host=konfig.host, port=konfig.port, **uvicorn_einstellungen())
    return 0


if __name__ == "__main__":
    sys.exit(main())
