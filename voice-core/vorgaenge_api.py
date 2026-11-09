"""Die letzten Vorgänge des eigenen Profils (Tabelle vorgaenge, Migration 0017): Verlauf der Tafel "Vorgänge".

  GET  /wamo/api/vorgaenge    die letzten 20 eigenen Vorgänge, neueste zuerst, in der Form der Servernachricht "vorgang"

Sicherheit: Token mit zweitem Faktor und aktives Profil prüft die Zugangskontrolle (wamo_api.identitaet). Das Profil kommt nur
von dort, nie aus der Anfrage; gelesen werden ausschließlich Zeilen mit dieser user_id. Fehler nennen keine Datenbankmeldung.
Die Texte stammen aus dem gespeicherten Stand und laufen durch vorgaenge.aus_zeile (Steuerzeichen, Längen, Werte). Ein
Vorgang, der beim Absturz des Kerns noch "laeuft" war, bleibt so stehen (der Verlauf zeigt den letzten bekannten Stand), bis
vorgaenge.aufraeumen ihn nach 24 Stunden auf "abgebrochen" setzt; nach 30 Tagen ist sein Stand geleert. Die Reihenfolge ist
stabil: neueste zuerst, bei gleichem Beginn nach der Kennung absteigend.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger

from memory import DB_ERRORS, shared_database
from vorgaenge import aus_zeile
from wamo_api import identitaet

router = APIRouter()
MAX_VORGAENGE = 20


def _db():
    try:
        db = shared_database()
    except ValueError as e:
        raise HTTPException(503, "Datenbank nicht eingerichtet") from e
    if not db.configured:
        raise HTTPException(503, "Datenbank nicht eingerichtet")
    return db


@router.get("/vorgaenge")
async def vorgaenge(request: Request, response: Response) -> dict:
    ich = await identitaet(request)
    db = _db()
    try:
        zeilen = await db.fetch(
            f"select id, art, titel, status, gesamt, daten, gestartet_at, beendet_at "
            f"from {db.t('vorgaenge')} where user_id = $1::uuid order by gestartet_at desc, id desc limit $2",
            ich.user_id, MAX_VORGAENGE)
    except DB_ERRORS as e:
        logger.warning("Vorgänge: Abfrage fehlgeschlagen ({})", type(e).__name__)
        raise HTTPException(503, "Vorgänge gerade nicht verfügbar") from e
    response.headers["Cache-Control"] = "no-store"
    return {"vorgaenge": [aus_zeile(z) for z in zeilen]}
