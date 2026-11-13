"""Die festen Adressen des Testlabors für das Platz-Skript (scripts/wamo_labor_platz.py), nur Standardbibliothek.

Es gibt genau einen Server (Sokrates-2) und vier Herkünfte: Anmeldung 55443, Kern 55444, Oberfläche 55445 und der Labor-Dienst 55447
(Board). Alles läuft über https mit dem Zertifikat der WAMO-CA. Die einzige Ausnahme ist die Weiterleitung auf Loopback, mit der das
Platz-Token ohne Befehlszeile und ohne Datei ins Board-Fenster kommt (siehe wamo_labor_platz_start.py).

Wichtig für die Sicherheit: Das Platz-Token wird an die Board-Adresse gehängt. Deshalb prüft `adresse_zulaessig` jede Adresse, die das
Skript benutzt, gegen diese feste Liste. Eine veränderte Platzdatei kann das Token also nie an einen fremden Rechner schicken.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

HOST = "192.168.178.136"
PORT_ANMELDUNG, PORT_KERN, PORT_OBERFLAECHE, PORT_LABOR = 55443, 55444, 55445, 55447
PORTS = (PORT_ANMELDUNG, PORT_KERN, PORT_OBERFLAECHE, PORT_LABOR)
HERKUENFTE = tuple(f"https://{HOST}:{p}" for p in PORTS)
WAMO_URL = f"https://{HOST}:{PORT_OBERFLAECHE}"
BOARD_URL = f"https://{HOST}:{PORT_LABOR}/board"
# Weiterleitung auf Loopback: fester Port und fester Pfad, damit die Chrome-Richtlinie genau diese Adresse erlauben kann
WEITER_PORT = 55489
WEITER_PFAD = "/weiter/"
WEITER_ADRESSE = f"http://127.0.0.1:{WEITER_PORT}{WEITER_PFAD}"
TOKEN_MUSTER = re.compile(r"[0-9a-f]{32}")  # Platz-Token: 128 Bit als 32 Hexzeichen (labor-dienst/README.md)
_UNERLAUBT = re.compile(r"[\s\\\x00-\x1f\x7f]")


def adresse_zulaessig(url: object) -> bool:
    """Nur https, genau der feste Rechner, einer der vier Ports, keine Zugangsdaten, keine Abfrage, kein Fragment."""
    if not isinstance(url, str) or not url or _UNERLAUBT.search(url):
        return False
    try:
        teile = urlsplit(url)
        port = teile.port
    except ValueError:
        return False
    return (teile.scheme == "https" and teile.hostname == HOST and port in PORTS and teile.username is None
            and teile.password is None and not teile.query and not teile.fragment)


def token_gueltig(wert: object) -> bool:
    return isinstance(wert, str) and TOKEN_MUSTER.fullmatch(wert) is not None
