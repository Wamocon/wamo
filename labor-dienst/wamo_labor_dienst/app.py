"""Die Anwendung: FastAPI mit den Schnittstellen für Kern, Browser und Betreuer, Gesundheit, Fehlerformat und Schutzschicht.

Es gibt keine Oberfläche der Schnittstellen-Dokumentation (kein /docs, kein /openapi.json). Jeder unbekannte Pfad ist 404 und jede falsche Methode
405 mit Allow, beides im Fehlerformat des Vertrags (nicht_erlaubt). Es gibt keine Umleitungen (auch nicht für einen Schrägstrich am Ende).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request

from . import auslieferung, betreuer_api, browser_api, kern_api
from .dienst import Dienst
from .konfig import Konfig
from .protokoll import MAX_BYTES
from .regeln import DienstFehler
from .sicherheit import SchutzMiddleware, dienst_von, fehler_http, fehlerfrei, json_antwort

AUFRAEUMEN_SECS = 3600


async def gesundheit(request: Request):
    """Nur der Zustand, keine Daten, ohne Token (Healthcheck des Containers)."""
    dienst = dienst_von(request)
    ok = dienst.gesundheit_gecacht()  # höchstens eine Sekunde alt: dann kein Faden und keine Datenbankverbindung
    if ok is None:
        ok = await run_in_threadpool(dienst.gesundheit_pruefen)
    return json_antwort(200 if ok else 503, {"zustand": "ok" if ok else "gestoert"})


def _http_fehler(_request: Request, e: HTTPException):
    """404 und 405 des Routers im Fehlerformat des Vertrags. Die erlaubten Methoden stehen sortiert in Allow."""
    if e.status_code == 405:
        erlaubt = sorted(m.strip() for m in (e.headers or {}).get("Allow", "").split(",") if m.strip())
        return fehler_http(DienstFehler(405, "nicht_erlaubt", "Diese Methode gibt es hier nicht.", allow=", ".join(erlaubt)))
    if e.status_code == 404:
        return fehler_http(DienstFehler(404, "nicht_erlaubt", "Diesen Pfad gibt es nicht."))
    return fehler_http(DienstFehler(500 if e.status_code >= 500 else 400, "dienst_fehler", "Interner Fehler."))


def _unerwartet(_request: Request, _e: Exception):
    return fehler_http(DienstFehler(500, "dienst_fehler", "Interner Fehler."))


async def _aufraeumen_schleife(dienst: Dienst) -> None:
    """Prüft stündlich die Aufbewahrung, auch wenn der Dienst nie neu gestartet wird (D10)."""
    while True:
        await asyncio.sleep(AUFRAEUMEN_SECS)
        try:
            await run_in_threadpool(dienst.aufbewahrung_pruefen)
        except Exception as e:  # noqa: BLE001 (der Dienst läuft weiter, im Protokoll steht nur der Name)
            dienst.protokoll.ereignis("fehler", fehlerart=type(e).__name__)


def create_app(konfig: Konfig, *, uhr: Callable[[], float] = time.time, protokoll_max_bytes: int = MAX_BYTES,
               monotonic: Callable[[], float] = time.monotonic) -> FastAPI:
    """Baut die Anwendung und richtet den Speicher ein (Dateien, Tabellen, Aufbewahrung). uhr ist für Tests."""
    dienst = Dienst(konfig, uhr, protokoll_max_bytes=protokoll_max_bytes, monotonic=monotonic)
    dienst.einrichten()

    @contextlib.asynccontextmanager
    async def lebenszeit(_app: FastAPI) -> AsyncIterator[None]:
        aufgabe = asyncio.create_task(_aufraeumen_schleife(dienst))
        try:
            yield
        finally:
            aufgabe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await aufgabe
            await run_in_threadpool(dienst.protokoll.beenden)  # wartende Zeilen und offene Zähler schreiben

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=lebenszeit)
    app.state.dienst = dienst
    app.add_middleware(SchutzMiddleware)
    app.add_exception_handler(HTTPException, _http_fehler)
    app.add_exception_handler(Exception, _unerwartet)
    kern_api.registrieren(app)
    browser_api.registrieren(app)
    betreuer_api.registrieren(app)
    auslieferung.registrieren(app)  # die Seiten der Oberflächen: feste Liste, nur GET, strenge Köpfe
    app.add_api_route("/gesundheit", fehlerfrei(gesundheit), methods=["GET"], include_in_schema=False, response_model=None)
    return app
