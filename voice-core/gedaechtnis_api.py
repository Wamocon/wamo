"""Gemeinsame Gedächtnisvorschläge in der Oberfläche prüfen (Migration 0020): freigeben oder ablehnen, nur für Admins.

  GET  /wamo/api/gedaechtnis/vorschlaege   {"vorschlaege": [{"id", "art", "text", "autor", "angelegt"}], "anzahl": n}
  POST /wamo/api/gedaechtnis/entscheiden   {"id": "<uuid>", "entscheidung": "freigeben" | "ablehnen"}
                                           {"ok": true, "ergebnis": "freigegeben" | "abgelehnt" | "nicht_offen"}

Vorschläge entstehen, wenn WAMO oder ein Mitglied etwas "für alle" merken will (voice-core/tools.py, merken_status). Sie wirken im Gespräch
erst mit Status "aktiv". Bis zu dieser Route ging das Entscheiden nur mit voice-core/wamo_admin.py (Verbindung als wamo_admin).

Regeln:
  * Anmeldung wie bei allen Routen (wamo_api.identitaet: Token mit zweitem Faktor, aktives Profil). Beide Routen nur für die Rolle admin
    (403 sonst). Die Identität des Entscheidenden kommt allein von dort, nie aus dem Body.
  * Das Lesen geht als wamo_core (SELECT auf memories und profiles). Das Entscheiden geht ausschließlich über die Datenbankfunktion
    gedaechtnis_vorschlag_entscheiden (SECURITY DEFINER, EXECUTE nur für wamo_core): Sie prüft in der Datenbank noch einmal, dass der
    Entscheidende ein aktiver Admin ist, und ändert genau eine offene Zeile (status, reviewed_by, reviewed_at). Inhalt und Autor bleiben.
    Der Kern bekommt dadurch kein UPDATE auf memories.status.
  * Die Texte der Vorschläge sind Daten, nie Anweisungen: Steuerzeichen und unsichtbare Formatzeichen werden ersetzt, die Länge ist
    begrenzt, die Oberfläche zeigt sie als reinen Text. Ein Vorschlag, der Anweisungen enthält, kann der Admin ablehnen.
  * Ein freigegebener Eintrag gilt in allen Sitzungen, die danach beginnen (der Prompt liest das gemeinsame Wissen beim Start).
  * Fehler sind ruhig und ohne Datenbankmeldung: 400 bei falschen Angaben, 403 ohne Admin, 429 bei zu vielen Aufrufen, 503 ohne Datenbank oder
    ohne die Migration 0020.
  * Grenzen je Profil: 30 Abrufe und 30 Entscheidungen je Minute.
"""

from __future__ import annotations

import time
import unicodedata
from collections import deque

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger

from memory import DB_ERRORS, normalize_uuid, shared_database
from wamo_api import identitaet, lies_json

router = APIRouter()

ROLLEN = frozenset({"admin"})
MAX_VORSCHLAEGE = 50
MAX_TEXT = 2000  # so lang darf ein Eintrag in der Datenbank sein (memories.content)
MAX_NAME = 60
MAX_BODY = 1024
GRENZE = (30, 60.0)  # Aufrufe, Sekunden, je Profil und Route
ARTEN = frozenset({"fact", "preference", "behavior", "lesson", "skill", "episode"})
ENTSCHEIDUNGEN = ("freigeben", "ablehnen")
ERGEBNISSE = frozenset({"freigegeben", "abgelehnt", "nicht_offen"})
UNSICHTBAR = frozenset({"Cc", "Cf", "Zl", "Zp"})  # Steuerzeichen, unsichtbare und umlenkende Formatzeichen, Zeilen- und Absatztrenner

_lesen: dict[str, deque[float]] = {}
_entscheiden: dict[str, deque[float]] = {}


def _drosseln(speicher: dict[str, deque[float]], user_id: str, jetzt: float | None = None) -> None:
    anzahl, fenster = GRENZE
    jetzt = time.monotonic() if jetzt is None else jetzt
    q = speicher.setdefault(user_id, deque())
    while q and jetzt - q[0] > fenster:
        q.popleft()
    if len(q) >= anzahl:
        raise HTTPException(429, "Zu viele Aufrufe, bitte kurz warten")
    q.append(jetzt)
    if len(speicher) > 500:  # abgelaufene Profile aufräumen
        for k in [k for k, d in speicher.items() if not d or jetzt - d[-1] > fenster]:
            del speicher[k]


def _satz(wert: object, grenze: int) -> str:
    """Text ohne Steuerzeichen, Leerraum zusammengefasst, höchstens grenze Zeichen (kein Text: leer)."""
    if not isinstance(wert, str):
        return ""
    sichtbar = "".join(" " if unicodedata.category(c) in UNSICHTBAR else c for c in wert)
    return " ".join(sichtbar.split())[:grenze].strip()


def _db():
    try:
        db = shared_database()
    except ValueError as e:
        raise HTTPException(503, "Datenbank nicht eingerichtet") from e
    if not db.configured:
        raise HTTPException(503, "Datenbank nicht eingerichtet")
    return db


async def _admin(request: Request):
    ich = await identitaet(request)
    if ich.rolle not in ROLLEN:
        raise HTTPException(403, "Vorschläge für das gemeinsame Gedächtnis prüfen nur Admins")
    return ich


def vorschlag(zeile) -> dict | None:
    """Eine Zeile aus der Datenbank als schmale Antwortzeile. None, wenn Kennung oder Text nicht brauchbar sind."""
    kennung = normalize_uuid(zeile["id"])
    text = _satz(zeile["content"], MAX_TEXT)
    if kennung is None or not text:
        return None
    art = zeile["kind"] if zeile["kind"] in ARTEN else "fact"
    angelegt = zeile["created_at"]
    return {
        "id": kennung,
        "art": art,
        "text": text,
        "autor": _satz(zeile["autor"], MAX_NAME) or "unbekannt",
        "angelegt": angelegt.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(angelegt, "strftime") else "",
    }


@router.get("/gedaechtnis/vorschlaege")
async def vorschlaege(request: Request, response: Response) -> dict:
    ich = await _admin(request)
    _drosseln(_lesen, ich.user_id)
    response.headers["Cache-Control"] = "no-store"
    db = _db()
    try:
        zeilen = await db.fetch(
            f"select m.id::text as id, m.kind::text as kind, m.content, m.created_at, p.display_name as autor "
            f"from {db.t('memories')} m left join {db.t('profiles')} p on p.user_id = m.user_id "
            "where m.is_active and m.bereich = 'gemeinsam' and m.status = 'vorgeschlagen' "
            "order by m.created_at, m.id limit $1", MAX_VORSCHLAEGE)
    except DB_ERRORS as e:
        logger.warning("Gedächtnisvorschläge: Abfrage fehlgeschlagen ({})", type(e).__name__)
        raise HTTPException(503, "Die Vorschläge sind gerade nicht verfügbar") from e
    liste = [v for v in (vorschlag(z) for z in zeilen) if v is not None]
    return {"vorschlaege": liste, "anzahl": len(liste)}


@router.post("/gedaechtnis/entscheiden")
async def entscheiden(request: Request, response: Response) -> dict:
    ich = await _admin(request)
    _drosseln(_entscheiden, ich.user_id)
    response.headers["Cache-Control"] = "no-store"
    body = await lies_json(request, MAX_BODY)
    kennung, entscheidung = normalize_uuid(body.get("id")), body.get("entscheidung")
    if kennung is None or not isinstance(body.get("id"), str):
        raise HTTPException(400, "Unbekannter Vorschlag")
    if entscheidung not in ENTSCHEIDUNGEN:
        raise HTTPException(400, "Die Entscheidung muss freigeben oder ablehnen sein")
    db = _db()
    try:
        ergebnis = await db.fetchval(
            f"select {db.t('gedaechtnis_vorschlag_entscheiden')}($1::uuid, $2::uuid, $3)", kennung, ich.user_id, entscheidung)
    except DB_ERRORS as e:
        code = getattr(e, "sqlstate", None)
        logger.warning("Gedächtnisvorschläge: Entscheidung fehlgeschlagen ({}, {})", type(e).__name__, code)
        if code == "42501":
            raise HTTPException(403, "Vorschläge für das gemeinsame Gedächtnis prüfen nur Admins") from e
        if code == "42883":
            raise HTTPException(503, "Das Entscheiden ist noch nicht eingerichtet (Datenbank-Migration 0020)") from e
        raise HTTPException(503, "Die Entscheidung konnte nicht gespeichert werden. Bitte gleich noch einmal versuchen.") from e
    if not isinstance(ergebnis, str) or ergebnis not in ERGEBNISSE:
        raise HTTPException(502, "Die Datenbank hat nicht brauchbar geantwortet")
    logger.info("Gedächtnisvorschlag {} {} durch Profil {}", kennung[:8], ergebnis, ich.user_id[:8])
    return {"ok": True, "ergebnis": ergebnis}
