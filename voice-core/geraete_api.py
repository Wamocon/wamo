"""Ablage je Person aus dem Gerätedienst in der Oberfläche: Scans und vom NAS abgerufene Dateien (F8).

  GET    /wamo/api/scans          eigene Einträge (id, art scan oder nas, name, mime, groesse, erstellt, quelle)
  GET    /wamo/api/scans/{id}     eine eigene Datei herunterladen (Content-Disposition: attachment)
  DELETE /wamo/api/scans/{id}     einen eigenen Eintrag löschen
  GET    /wamo/api/buerogeraete   Bürogeräte mit Fähigkeiten (für Anzeigen in der Oberfläche)

Die Identität kommt aus wamo_api.identitaet (Token mit zweitem Faktor, aktives Profil); die user_id geht als Kontext
an den Gerätedienst, der nur Einträge dieser Person liefert. Ohne Gerätedienst (Unterwegs) gibt es 503.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response

import geraete_client as gc
from wamo_api import identitaet

router = APIRouter()
ID_RE = re.compile(r"^[0-9a-f]{32}$")
MIME = {"application/pdf", "image/jpeg", "image/png", "text/plain", "application/octet-stream"}


def _kontext(ich) -> gc.Kontext:
    return gc.Kontext(ich.user_id, ich.rolle, None, ich.name)


async def _rufen(werkzeug: str, ich, **argumente) -> tuple[dict, bytes]:
    if not gc.verfuegbar():
        raise HTTPException(503, gc.NUR_IM_BUERO)
    try:
        return await gc.aufruf(werkzeug, _kontext(ich), argumente=argumente, timeout=20)
    except gc.GeraeteNichtVerfuegbar as e:
        raise HTTPException(503, str(e)) from e


@router.get("/scans")
async def scans(request: Request) -> dict:
    ich = await identitaet(request)
    antwort, _ = await _rufen("ablage_liste", ich)
    if antwort.get("status") != "success":
        raise HTTPException(502, "Ablage gerade nicht verfügbar")
    return {"eintraege": antwort.get("eintraege", [])}


def _dateiname_kopf(name: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9 ._-]", "_", name)[:80] or "datei"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name[:80], safe='')}"


@router.get("/scans/{eintrag_id}")
async def scan_holen(eintrag_id: str, request: Request) -> Response:
    ich = await identitaet(request)
    if not ID_RE.fullmatch(eintrag_id):
        raise HTTPException(404, "Unbekannter Eintrag")
    antwort, daten = await _rufen("ablage_holen", ich, id=eintrag_id)
    if antwort.get("status") != "success" or not isinstance(antwort.get("eintrag"), dict):
        raise HTTPException(404, "Unbekannter Eintrag")
    eintrag = antwort["eintrag"]
    mime = eintrag.get("mime") if eintrag.get("mime") in MIME else "application/octet-stream"
    return Response(content=daten, media_type=mime, headers={
        "Content-Disposition": _dateiname_kopf(str(eintrag.get("name") or "datei")),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.delete("/scans/{eintrag_id}")
async def scan_loeschen(eintrag_id: str, request: Request) -> dict:
    ich = await identitaet(request)
    if not ID_RE.fullmatch(eintrag_id):
        raise HTTPException(404, "Unbekannter Eintrag")
    antwort, _ = await _rufen("ablage_loeschen", ich, id=eintrag_id)
    return {"ok": antwort.get("geloescht") is True}


@router.get("/buerogeraete")
async def buerogeraete(request: Request) -> dict:
    ich = await identitaet(request)
    antwort, _ = await _rufen("geraete", ich)
    return {"geraete": antwort.get("geraete", [])}
