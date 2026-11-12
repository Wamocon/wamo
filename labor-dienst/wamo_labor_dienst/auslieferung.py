"""Auslieferung der Weboberflächen (Board-Seite und Betreuer-Seite): statische Dateien aus einer festen Liste, nur per GET.

Sicherheit: Die Liste ist fest (SEITEN und DATEIEN), keine Adresse nimmt einen Dateinamen aus der Eingabe auf: /web/<Name> schlägt den Namen
exakt in der Liste nach, alles andere ist 404 (kein Pfad-Trick mit .., %2e oder Schrägstrichen, kein Verzeichnislisting). Alle Dateien werden beim
Start einmal gelesen und liegen danach im Speicher, zur Laufzeit öffnet der Dienst keine Datei. Jede Antwort trägt die strenge Content-Security-
Policy ohne Inline-Skript und ohne Inline-Stil, nosniff, no-referrer, no-store (auch Token-Fragmente dürfen in keinen Cache) und eine leere
Permissions-Policy. Die Seiten enthalten weder Token noch Platzdaten, alle Daten kommen über die Schnittstellen mit Token.
Die Anmeldung der Schnittstellen bleibt unberührt: Wer die Seiten erreicht, wird in Caddy festgelegt.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from .regeln import DienstFehler
from .sicherheit import fehler_http, fehlerfrei

WEB = Path(__file__).resolve().parent / "web"
HTML = "text/html; charset=utf-8"
JS = "text/javascript; charset=utf-8"
CSS = "text/css; charset=utf-8"

# Adresse der Seite -> Datei im Ordner web/. Die feste Liste: Wer eine Seite hinzufügt, ändert sie bewusst.
SEITEN: Mapping[str, str] = {"/": "board.html", "/board": "board.html", "/betreuer": "betreuer.html"}
# Name unter /web/<Name> -> Inhaltstyp (der Dateiname ist der Name)
DATEIEN: Mapping[str, str] = {"stil.css": CSS, "dom.js": JS, "gemeinsam.js": JS, "board-logik.js": JS, "board.js": JS, "betreuer-logik.js": JS, "betreuer.js": JS}

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
KOEPFE = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), display-capture=(), interest-cohort=()",
}


def _laden() -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Liest alle Dateien der Liste (fehlt eine, startet der Dienst nicht): Seiten nach Adresse, Dateien nach Name."""
    ordner = Path(WEB)
    seiten = {adresse: (ordner / datei).read_bytes() for adresse, datei in SEITEN.items()}
    dateien = {name: (ordner / name).read_bytes() for name in DATEIEN}
    return seiten, dateien


def _antwort(inhalt: bytes, inhaltstyp: str) -> Response:
    return Response(inhalt, status_code=200, headers={"Content-Type": inhaltstyp, **KOEPFE})


def registrieren(app: FastAPI) -> None:
    seiten, dateien = _laden()

    def seite(adresse: str):
        @fehlerfrei
        async def endpunkt(_request: Request):
            return _antwort(seiten[adresse], HTML)

        endpunkt.__name__ = f"seite_{adresse.strip('/') or 'start'}"
        return endpunkt

    @fehlerfrei
    async def datei(request: Request):
        name = request.path_params["name"]
        if name not in dateien:  # exakter Treffer in der Liste, sonst nichts (auch kein Treffer über Groß- und Kleinschreibung)
            return fehler_http(DienstFehler(404, "nicht_erlaubt", "Diesen Pfad gibt es nicht."))
        return _antwort(dateien[name], DATEIEN[name])

    for adresse in SEITEN:
        app.add_api_route(adresse, seite(adresse), methods=["GET"], include_in_schema=False, response_model=None)
    app.add_api_route("/web/{name}", datei, methods=["GET"], include_in_schema=False, response_model=None)
