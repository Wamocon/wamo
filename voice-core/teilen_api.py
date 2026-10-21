"""REST-Schnittstelle für geteilte Arbeit (teilen.py), eingehängt unter /wamo/api von wamo_api.py.

Alle Routen verlangen die geprüfte Identität (wamo_api.identitaet); die user_id kommt nie aus der Anfrage.
  GET  /profile                       aktive Profile außer dem eigenen (Empfängerwahl: user_id und Name)
  GET  /teilen                        laufende Teilungen: von_mir, mit_mir
  POST /teilen                        {art: canvas|auftrag|datei|scan, objekt, empfaenger, recht, hinweis?}
  POST /teilen/{id}/recht             {recht} (nur Besitzer)
  POST /teilen/{id}/widerrufen        nur Besitzer
  POST /teilen/{id}/zurueckgeben      nur Empfänger
  GET  /teilen/{id}/protokoll         nur Besitzer
  GET  /teilen/{id}/dokumente         Ergebnisdokumente eines geteilten Projekts
  GET  /geteilt/canvas/{id}           neueste Fassung (eigen oder geteilt), mit Fassungsnummer
  PUT  /geteilt/canvas/{id}           {inhalt, basis} neue Fassung (Besitzer oder Recht bearbeiten); 409 bei Konflikt
  GET  /dateien                       eigene Dateien (ohne Inhalt)
  POST /dateien                       Rohdaten im Body, Name im Header X-Dateiname (URL-kodiert), höchstens 25 MB
  GET  /dateien/{id}                  Herunterladen (Besitzer oder Empfänger), immer als Anhang
  POST /dateien/{id}/fassung          neue Fassung (Besitzer oder Recht bearbeiten), wie POST /dateien
  POST /dateien/{id}/loeschen         nur Besitzer, widerruft laufende Teilungen (endgültig nach 7 Tagen)
Uploads: Ratengrenze und Recht werden vor dem Lesen des Bodys geprüft, Content-Length ist Pflicht (411), Lesen
höchstens 120 s (408), eine Übertragung je Profil und zwei im Kern (teilen.uebertragung). Caddy erlaubt nur für
/wamo/api/dateien größere Bodys (26 MiB), sonst 512 KB.
Hintergrund: einmal täglich weich gelöschte Dateien nach 7 Tagen endgültig entfernen.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from loguru import logger

from memory import DB_ERRORS, DatabaseUnavailable, shared_database
from teilen import MAX_DATEI_BYTES, Teilen, TeilenFehler, dateien_aufraeumen, uebertragung
from wamo_api import identitaet, lies_bytes, lies_json

router = APIRouter()
LESEN_TIMEOUT_SECS = 120.0
AUFRAEUMEN_ERSTER_LAUF_SECS = 300.0
AUFRAEUMEN_ABSTAND_SECS = 24 * 3600.0


async def _teilen(request: Request) -> Teilen:
    ich = await identitaet(request)
    try:
        db = shared_database()
    except ValueError as e:
        raise HTTPException(503, "Datenbank nicht eingerichtet") from e
    try:
        return Teilen(db, ich.user_id)
    except TeilenFehler as e:
        raise HTTPException(e.status, e.text) from e


async def _ausfuehren(aufruf):
    try:
        return await aufruf
    except TeilenFehler as e:
        raise HTTPException(e.status, e.text) from e


def _dateiname(request: Request) -> str:
    roh = request.headers.get("x-dateiname", "")
    if not roh or len(roh) > 1000:
        raise HTTPException(400, "Dateiname fehlt (Header X-Dateiname)")
    return unquote(roh)


async def _datei_body(request: Request) -> bytes:
    """Upload lesen: Länge angekündigt und nicht zu groß, höchstens 120 s."""
    laenge = request.headers.get("content-length", "")
    if not laenge.isdigit():
        raise HTTPException(411, "Länge der Datei fehlt (Content-Length)")
    if int(laenge) > MAX_DATEI_BYTES:
        raise HTTPException(413, "Die Datei ist größer als 25 MB")
    try:
        async with asyncio.timeout(LESEN_TIMEOUT_SECS):
            return await lies_bytes(request, MAX_DATEI_BYTES)
    except TimeoutError as e:
        raise HTTPException(408, "Das Hochladen dauert zu lange") from e


async def _mit_uebertragung(t: Teilen, aufruf):
    """Eine Übertragung je Profil, zwei im Kern (Body lesen und Datenbank zusammen)."""
    try:
        async with uebertragung(t.user_id):
            return await aufruf()
    except TeilenFehler as e:
        raise HTTPException(e.status, e.text) from e


@router.get("/profile")
async def profile(request: Request) -> dict:
    t = await _teilen(request)
    return {"profile": await _ausfuehren(t.profile())}


@router.get("/teilen")
async def liste(request: Request) -> dict:
    t = await _teilen(request)
    return await _ausfuehren(t.liste())


@router.post("/teilen")
async def teilen(request: Request) -> dict:
    t = await _teilen(request)
    daten = await lies_json(request)
    return await _ausfuehren(t.teilen(daten.get("art"), daten.get("objekt"), daten.get("empfaenger"),
                                      daten.get("recht", "lesen"), daten.get("hinweis")))


@router.post("/teilen/{teilung_id}/recht")
async def recht(teilung_id: str, request: Request) -> dict:
    t = await _teilen(request)
    daten = await lies_json(request)
    return await _ausfuehren(t.recht_aendern(teilung_id, daten.get("recht")))


@router.post("/teilen/{teilung_id}/widerrufen")
async def widerrufen(teilung_id: str, request: Request) -> dict:
    t = await _teilen(request)
    return await _ausfuehren(t.widerrufen(teilung_id))


@router.post("/teilen/{teilung_id}/zurueckgeben")
async def zurueckgeben(teilung_id: str, request: Request) -> dict:
    t = await _teilen(request)
    return await _ausfuehren(t.zurueckgeben(teilung_id))


@router.get("/teilen/{teilung_id}/protokoll")
async def protokoll(teilung_id: str, request: Request) -> dict:
    t = await _teilen(request)
    return {"ereignisse": await _ausfuehren(t.protokoll(teilung_id))}


@router.get("/teilen/{teilung_id}/dokumente")
async def dokumente(teilung_id: str, request: Request) -> dict:
    t = await _teilen(request)
    return {"dokumente": await _ausfuehren(t.projekt_dokumente(teilung_id))}


@router.get("/geteilt/canvas/{canvas_id}")
async def canvas_lesen(canvas_id: str, request: Request) -> dict:
    t = await _teilen(request)
    return await _ausfuehren(t.canvas_lesen(canvas_id))


@router.put("/geteilt/canvas/{canvas_id}")
async def canvas_bearbeiten(canvas_id: str, request: Request) -> dict:
    t = await _teilen(request)
    daten = await lies_json(request, 256 * 1024)
    return await _ausfuehren(t.canvas_bearbeiten(canvas_id, daten.get("inhalt"), daten.get("basis")))


@router.get("/dateien")
async def dateien(request: Request) -> dict:
    t = await _teilen(request)
    return {"dateien": await _ausfuehren(t.dateien())}


@router.post("/dateien")
async def datei_ablegen(request: Request) -> dict:
    t = await _teilen(request)
    name = _dateiname(request)
    try:
        t.upload_vorab()  # Ratengrenze vor dem Lesen von bis zu 25 MB
    except TeilenFehler as e:
        raise HTTPException(e.status, e.text) from e

    async def ablegen():
        return await t.datei_ablegen(name, await _datei_body(request))

    return {"id": await _mit_uebertragung(t, ablegen)}


@router.get("/dateien/{datei_id}")
async def datei_lesen(datei_id: str, request: Request) -> Response:
    t = await _teilen(request)
    datei = await _mit_uebertragung(t, lambda: t.datei_lesen(datei_id))
    # immer als Anhang, nie im Browser ausgeführt oder angezeigt; der Name ist schon bereinigt (teilen.py)
    ascii_name = datei.name.encode("ascii", "replace").decode("ascii").replace('"', "_").replace("?", "_")
    return Response(
        content=datei.inhalt,
        media_type=datei.mime,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(datei.name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/dateien/{datei_id}/fassung")
async def datei_fassung(datei_id: str, request: Request) -> dict:
    t = await _teilen(request)
    name = _dateiname(request)
    did = await _ausfuehren(t.fassung_vorab(datei_id))  # Recht und Ratengrenze vor dem Lesen des Bodys

    async def fassung():
        return await t.datei_fassung(did, name, await _datei_body(request))

    return {"id": await _mit_uebertragung(t, fassung)}


@router.post("/dateien/{datei_id}/loeschen")
async def datei_loeschen(datei_id: str, request: Request) -> dict:
    t = await _teilen(request)
    return {"geloescht": await _ausfuehren(t.datei_loeschen(datei_id))}


async def dateien_taeglich_aufraeumen(sleep=asyncio.sleep) -> None:
    """Hintergrund (wamo_api): einmal täglich weich gelöschte Dateien nach 7 Tagen endgültig entfernen."""
    try:
        db = shared_database()
    except ValueError:
        return
    if not db.configured:
        return
    await sleep(AUFRAEUMEN_ERSTER_LAUF_SECS)
    while True:
        try:
            anzahl = await dateien_aufraeumen(db)
            if anzahl:
                logger.info("Teilen: {} gelöschte Dateien endgültig entfernt", anzahl)
        except (*DB_ERRORS, DatabaseUnavailable) as e:
            logger.warning("Teilen: Aufräumen fehlgeschlagen ({})", type(e).__name__)
        await sleep(AUFRAEUMEN_ABSTAND_SECS)


hintergrund = [dateien_taeglich_aufraeumen]
