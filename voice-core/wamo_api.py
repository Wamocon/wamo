"""Eigene HTTP-Schnittstellen des Kerns unter /wamo/api/ (neben den Routen des Pipecat-Runners).

Wofür: Dinge, die nicht in ein Gespräch gehören und auch ohne laufende WebRTC-Verbindung gehen müssen, zum Beispiel
die Bindung eines Laptops bei der Anmeldung in der Desktop-App (geraet_api.py), Hinweise an ein Profil
(hinweise_api.py), geteilte Arbeit (teilen_api.py) und Scans (geraete_api.py).

Sicherheit
  * Jede Anfrage läuft vorher durch auth_gate.AccessMiddleware: Token mit zweitem Faktor (aal2), Höchstalter der
    Anmeldung, aktives Profil. Ohne das kommt keine Anfrage hier an (401, 403, 503).
  * Die Routen holen die geprüfte Identität erneut über denselben AccessGate (Token-Cache, keine zweite Prüfung im
    Netz) mit identitaet(). Eine Identität aus dem Body oder aus Headern gibt es nicht.
  * Bodys liest jede Route selbst mit Obergrenze (lies_json, lies_bytes); die Middleware begrenzt nur Start und Offer.
  * Antworten sind JSON ohne Geheimnisse; Fehler nennen nie interne Pfade oder Datenbankmeldungen.
  * CORS kommt vom Runner (PIPECAT_ALLOWED_ORIGINS), gilt also auch hier.

Module: Jedes Modul in ROUTEN_MODULE stellt `router` (fastapi.APIRouter, Pfade relativ zu /wamo/api) und optional
`hintergrund` (Liste von Coroutine-Funktionen ohne Argumente, laufen solange der Server läuft) bereit. Fehlt ein
Modul, wird es übersprungen (Teilbereiche lassen sich so unabhängig ausliefern).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from auth_gate import AccessGate, Identity

PRAEFIX = "/wamo/api"
ROUTEN_MODULE = ("geraet_api", "hinweise_api", "teilen_api", "geraete_api", "einwilligung_api", "modellzugang_api",
                 "geraete_admin_api", "rd_api")
MAX_JSON_BYTES = 64 * 1024

_gate: AccessGate | None = None


def gate_setzen(gate: AccessGate | None) -> None:
    global _gate
    _gate = gate


def bearer(request: Request) -> str:
    werte = [v for k, v in request.headers.items() if k.lower() == "authorization"]
    if len(werte) != 1 or werte[0][:7].lower() != "bearer ":
        return ""
    return werte[0][7:].strip()


async def identitaet(request: Request) -> Identity:
    """Geprüfte Identität der Anfrage (wie in der Middleware). Wirft HTTPException mit 401, 403 oder 503."""
    if _gate is None:
        raise HTTPException(503, "Zugangskontrolle nicht eingerichtet")
    entscheidung = await _gate.authorize(bearer(request))
    if entscheidung.identity is None:
        raise HTTPException(entscheidung.status, entscheidung.reason or "nicht berechtigt")
    return entscheidung.identity


async def lies_bytes(request: Request, grenze: int) -> bytes:
    """Ganzen Body lesen, höchstens grenze Bytes (angekündigt oder gezählt); sonst 413."""
    laenge = request.headers.get("content-length", "")
    if laenge.isdigit() and int(laenge) > grenze:
        raise HTTPException(413, "Anfrage zu groß")
    teile: list[bytes] = []
    summe = 0
    async for stueck in request.stream():
        summe += len(stueck)
        if summe > grenze:
            raise HTTPException(413, "Anfrage zu groß")
        teile.append(stueck)
    return b"".join(teile)


async def lies_json(request: Request, grenze: int = MAX_JSON_BYTES) -> dict:
    """JSON-Objekt aus dem Body (höchstens grenze Bytes); sonst 400 oder 413."""
    roh = await lies_bytes(request, grenze)
    try:
        daten = json.loads(roh.decode("utf-8")) if roh.strip() else {}
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise HTTPException(400, "Body ist kein gültiges JSON") from e
    if not isinstance(daten, dict):
        raise HTTPException(400, "Body muss ein JSON-Objekt sein")
    return daten


def _module() -> list[object]:
    geladen = []
    for name in ROUTEN_MODULE:
        try:
            geladen.append(importlib.import_module(name))
        except ModuleNotFoundError as e:
            if e.name != name:  # ein fehlendes Paket im Modul selbst ist ein echter Fehler
                raise
            logger.debug("Kern-API: Modul {} fehlt, übersprungen", name)
    return geladen


def _hintergrund_lifespan(app, aufgaben: list[Callable[[], Awaitable[None]]]) -> None:
    """Hintergrundaufgaben an den Lebenszyklus der App hängen (wie pipecat.runner.run._add_lifespan_to_app)."""
    if not aufgaben:
        return
    bisher = getattr(app.router, "lifespan_context", None)

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async def schuetzen(aufgabe):
            while True:
                try:
                    await aufgabe()
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 (eine Aufgabe darf den Kern nicht beenden)
                    logger.warning("Kern-API: Hintergrundaufgabe {} fehlgeschlagen ({}), neu in 30 s",
                                   getattr(aufgabe, "__name__", "?"), type(e).__name__)
                    await asyncio.sleep(30)

        tasks = [asyncio.create_task(schuetzen(a_)) for a_ in aufgaben]
        try:
            if bisher is not None:
                async with bisher(a) as zustand:
                    yield zustand
            else:
                yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app.router.lifespan_context = lifespan


def installieren(app, gate: AccessGate | None) -> list[str]:
    """Hängt alle vorhandenen Module unter /wamo/api ein. Liefert die Namen der eingehängten Module."""
    gate_setzen(gate)
    haupt = APIRouter(prefix=PRAEFIX)
    namen: list[str] = []
    aufgaben: list[Callable[[], Awaitable[None]]] = []
    for modul in _module():
        router = getattr(modul, "router", None)
        if isinstance(router, APIRouter):
            haupt.include_router(router)
            namen.append(modul.__name__)
        aufgaben.extend(getattr(modul, "hintergrund", []) or [])
    app.include_router(haupt)
    _hintergrund_lifespan(app, aufgaben)
    logger.info("Kern-API: {}", ", ".join(namen) or "keine Module")
    return namen
