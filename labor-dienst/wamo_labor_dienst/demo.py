"""Lokale Vorführung mit Demo-Daten: python -m wamo_labor_dienst.demo [--port 8765]

Startet den Dienst auf Loopback mit einem temporären Datenordner (er verschwindet beim Beenden), frischen Wegwerf-Tokens und erfundenen Tickets, damit man
die Board-Seite und die Betreuer-Seite im Browser ansehen kann. Platz 1 und 2 haben einen offenen Durchlauf mit Tickets in allen Spalten und Kommentaren,
Platz 3 hat einen beendeten Durchlauf, alle anderen Plätze sind geschlossen. Die Adressen mit den Token im Fragment stehen in der Ausgabe. Nur für die
Ansicht gedacht: Der Dienst lauscht nie auf etwas anderem als Loopback, und die Tokens gelten nur für diesen Lauf.
"""
from __future__ import annotations

import secrets
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .app import create_app
from .konfig import Konfig, KonfigFehler
from .modell import Aufruf, fingerabdruck
from .regeln import PRUEFER
from .server import uvicorn_einstellungen
from .sicherheit import neuer_platz_token, token_hash

LOOPBACK = ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True)
class Zugang:
    """Die Wegwerf-Tokens dieses Laufs (nie in repr oder str)."""
    betreuer_token: str = field(repr=False)
    platz_token: dict[int, str] = field(repr=False)

    def __str__(self) -> str:
        return "Zugang(Token verborgen)"


def _schreiben(db, name: str, nummer: int | None, daten: dict) -> dict:
    """Ein Schreibaufruf wie vom Kern, direkt am Speicher des Platzes. Liefert die Antwort als Objekt."""
    import json

    antwort = db.schreiben(Aufruf(name=name, nummer=nummer, werte=PRUEFER[name](daten), fingerabdruck=fingerabdruck(name, nummer, daten),
                                  durchlauf=db.durchlauf()["durchlauf_id"], schluessel=str(uuid.uuid4())))
    if antwort.status >= 300:
        raise RuntimeError(f"Demo-Daten: {name} ergab {antwort.status}")
    return json.loads(antwort.koerper)


def _anlegen(db, typ: str, prioritaet: str, titel: str, beschreibung: str = "") -> int:
    ticket = _schreiben(db, "tickets", None, {"typ": typ, "prioritaet": prioritaet, "titel": titel, "beschreibung": beschreibung})
    return int(ticket["nummer"][4:])


def _status(db, nummer: int, status: str) -> None:
    _schreiben(db, "status", nummer, {"erwartete_version": db.ticket(nummer)["version"], "status": status})


def _kommentar(db, nummer: int, text: str) -> None:
    _schreiben(db, "kommentare", nummer, {"erwartete_version": db.ticket(nummer)["version"], "text": text})


def _platz_eins(db) -> None:
    anmeldung = _anlegen(db, "bug", "hoch", "Anmeldung schlägt bei leerem Passwort fehl", "Schritte:\n1. Seite öffnen\n2. Benutzer eingeben, Passwort leer lassen\n3. Anmelden klicken\n\nErwartet: eine Fehlermeldung.\nIst: weiße Seite.")
    umlaute = _anlegen(db, "bug", "hoechste", "Absturz beim Speichern von Umlauten (ä, ö, ü, ß)", "Der Name „Müller-Lüdenscheid“ lässt sich nicht speichern.")
    warenkorb = _anlegen(db, "aufgabe", "mittel", "Testfälle für den Warenkorb ergänzen", "Grenzwerte für Menge und Preis prüfen.")
    _anlegen(db, "story", "niedrig", "Export der Ergebnisse als CSV", "Als Anwender will ich die Ergebnisse als Datei herunterladen.")
    _anlegen(db, "bug", "mittel", "<b>Fett?</b> & \"Anführungszeichen\" <script>alert(1)</script>", "<img src=x onerror=alert(1)> bleibt Text.\nAuch & < > \" ' bleiben Text.")
    _anlegen(db, "aufgabe", "hoch", "Die Beschreibung der Rollen und Rechte im Handbuch ist so lang, dass der Titel umbrechen muss")
    _status(db, anmeldung, "in_arbeit")
    _status(db, warenkorb, "erledigt")
    _kommentar(db, anmeldung, "Reproduziert mit Firefox und Chrome.")
    _kommentar(db, anmeldung, "Ursache vermutlich in der Prüfung des Passworts.\nBitte nach dem Test noch einmal prüfen.")
    _kommentar(db, umlaute, "Betrifft auch den Export.")
    _kommentar(db, 12, "Meldung fehlt auch beim Zurücksetzen des Passworts.")


def _platz_zwei(db) -> None:
    suche = _anlegen(db, "bug", "mittel", "Suche findet keine Treffer mit Bindestrich", "Suche nach „E-Mail“ bleibt leer.")
    _anlegen(db, "aufgabe", "niedrig", "Hilfetext zur Suche überarbeiten")
    _status(db, suche, "in_arbeit")
    _kommentar(db, suche, "Liegt am Trennzeichen.")


def _platz_drei(db) -> None:
    _anlegen(db, "bug", "hoch", "Bestätigungsseite zeigt falschen Betrag", "Rabatt wird doppelt abgezogen.")
    _anlegen(db, "story", "mittel", "Rechnung per Mail erhalten")


def demo_bauen(ordner: Path, *, uhr: Callable[[], float] = time.time) -> tuple[FastAPI, Zugang]:
    """Baut die Anwendung mit Demo-Daten in ordner. Liefert sie und die Wegwerf-Tokens dieses Laufs."""
    kern, betreuer = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    app = create_app(Konfig(daten=Path(ordner), kern_token=kern, betreuer_token=betreuer), uhr=uhr)
    dienst = app.state.dienst
    plaetze = {1: _platz_eins, 2: _platz_zwei, 3: _platz_drei}
    tokens: dict[int, str] = {}
    for platz, fuellen in plaetze.items():
        dienst.neuer_besucher(platz, 1 if platz < 3 else 2)  # die Version des Hinweises darf nur wachsen: zuletzt der Platz mit Version 2
        fuellen(dienst.plaetze[platz])
        tokens[platz] = neuer_platz_token()
        dienst.token_vergeben(platz, token_hash(tokens[platz]))
    dienst.plaetze[3].durchlauf_beenden()
    dienst.protokoll.leeren()  # die Demo-Anfragen sollen kein Protokoll hinterlassen
    return app, Zugang(betreuer_token=betreuer, platz_token=tokens)


def adressen(zugang: Zugang, host: str, port: int) -> list[str]:
    basis = f"http://{host}:{port}"
    zeilen = [f"Board Platz {platz}: {basis}/board#t={token}" for platz, token in sorted(zugang.platz_token.items())]
    return [*zeilen, f"Betreuer:      {basis}/betreuer#b={zugang.betreuer_token}"]


def _angaben(argv: list[str]) -> tuple[str, int]:
    host, port, i = "127.0.0.1", 8765, 0
    while i < len(argv):
        if argv[i] in ("--host", "--port") and i + 1 < len(argv):
            wert = argv[i + 1]
            if argv[i] == "--host":
                if wert not in LOOPBACK:
                    raise ValueError("Die Vorführung lauscht nur auf Loopback (127.0.0.1, localhost oder ::1).")
                host = wert
            else:
                if not wert.isascii() or not wert.isdigit() or not 1 <= int(wert) <= 65535:
                    raise ValueError("--port braucht eine Zahl von 1 bis 65535.")
                port = int(wert)
            i += 2
        else:
            raise ValueError("Aufruf: python -m wamo_labor_dienst.demo [--host 127.0.0.1] [--port 8765]")
    return host, port


def main(argv: list[str] | None = None) -> int:
    try:
        host, port = _angaben(sys.argv[1:] if argv is None else argv)
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="wamo-demo-") as ordner:
        try:
            app, zugang = demo_bauen(Path(ordner) / "daten")
        except KonfigFehler as e:
            print(f"Konfiguration: {e}", file=sys.stderr)
            return 2
        print("WAMO Labor-Dienst, Vorführung mit Demo-Daten (nur Loopback, Tokens gelten nur für diesen Lauf)\n", flush=True)
        for zeile in adressen(zugang, "localhost" if host == "localhost" else host, port):
            print(zeile, flush=True)
        print("\nBeenden mit Strg+C. Die Demo-Daten verschwinden dabei.", flush=True)
        uvicorn.run(app, host=host, port=port, **uvicorn_einstellungen())
    return 0


if __name__ == "__main__":
    sys.exit(main())
