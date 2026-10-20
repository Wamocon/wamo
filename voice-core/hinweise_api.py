"""Hinweise an das eigene Profil (Tabelle hinweise, Migration 0002): Karten in der Oberfläche.

Beispiele: Für Admins "Neues Gerät gebunden" oder "Gerät wartet auf Freigabe", für alle "Dein Gerät ist gesperrt"
oder "Anna hat ein Dokument mit dir geteilt". Wer einen Hinweis bekommt, legt die Datenbank fest (Funktionen und
Trigger der Migrationen); hier liest jedes Profil nur seine eigenen Hinweise und quittiert sie.

  GET  /wamo/api/hinweise                 die neuesten 50 eigenen Hinweise und die Zahl der ungelesenen
  POST /wamo/api/hinweise/{id}/gelesen    einen eigenen Hinweis quittieren
  POST /wamo/api/hinweise/gelesen         alle eigenen quittieren
Inhalte von Hinweisen sind Daten (Titel und Text stammen aus festen Vorlagen der Migrationen plus Namen); die
Oberfläche zeigt sie als Text, nie als HTML.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from memory import DB_ERRORS, DatabaseUnavailable, normalize_uuid, shared_database
from wamo_api import identitaet

router = APIRouter()
MAX_HINWEISE = 50


def _db():
    try:
        db = shared_database()
    except ValueError as e:
        raise HTTPException(503, "Datenbank nicht eingerichtet") from e
    if not db.configured:
        raise HTTPException(503, "Datenbank nicht eingerichtet")
    return db


def _zeile(r) -> dict:
    daten = r["daten"]
    if isinstance(daten, str):
        try:
            daten = json.loads(daten)
        except ValueError:
            daten = {}
    return {
        "id": r["id"], "art": r["art"], "titel": r["titel"], "text": r["text"] or "", "bezug": r["bezug"],
        "daten": daten if isinstance(daten, dict) else {}, "erstellt_at": r["erstellt_at"].isoformat(),
        "gelesen": r["gelesen_at"] is not None,
    }


@router.get("/hinweise")
async def hinweise(request: Request) -> dict:
    ich = await identitaet(request)
    db = _db()
    try:
        zeilen = await db.fetch(
            f"select id::text as id, art, titel, text, bezug::text as bezug, daten, erstellt_at, gelesen_at "
            f"from {db.t('hinweise')} where empfaenger = $1::uuid order by erstellt_at desc limit $2",
            ich.user_id, MAX_HINWEISE)
        offen = await db.fetchval(
            f"select count(*) from {db.t('hinweise')} where empfaenger = $1::uuid and gelesen_at is null", ich.user_id)
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        logger.warning("Hinweise: Abfrage fehlgeschlagen ({})", type(e).__name__)
        raise HTTPException(503, "Hinweise gerade nicht verfügbar") from e
    return {"hinweise": [_zeile(r) for r in zeilen], "ungelesen": int(offen or 0)}


@router.post("/hinweise/gelesen")
async def alle_gelesen(request: Request) -> dict:
    ich = await identitaet(request)
    db = _db()
    try:
        status = await db.execute(
            f"update {db.t('hinweise')} set gelesen_at = now() where empfaenger = $1::uuid and gelesen_at is null",
            ich.user_id)
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        raise HTTPException(503, "Hinweise gerade nicht verfügbar") from e
    return {"ok": True, "anzahl": int(status.rsplit(" ", 1)[-1]) if status else 0}


@router.post("/hinweise/{hinweis_id}/gelesen")
async def gelesen(hinweis_id: str, request: Request) -> dict:
    ich = await identitaet(request)
    hid = normalize_uuid(hinweis_id)
    if hid is None:
        raise HTTPException(404, "Unbekannter Hinweis")
    db = _db()
    try:
        status = await db.execute(
            f"update {db.t('hinweise')} set gelesen_at = now() "
            "where id = $1::uuid and empfaenger = $2::uuid and gelesen_at is null", hid, ich.user_id)
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        raise HTTPException(503, "Hinweise gerade nicht verfügbar") from e
    return {"ok": status.endswith(" 1")}
