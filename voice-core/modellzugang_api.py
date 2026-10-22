"""Modellzugang über HTTP, ohne laufendes Gespräch (neben der RTVI-Nachricht "modellzugang" in einer Sitzung).

Wofür: Wer seinen Claude-API-Schlüssel, seinen Claude-Code-Token oder die Modellwahl einstellen will, muss dafür kein
Gespräch starten. Die Oberfläche nutzt diese Routen, solange keine Sitzung läuft, und die Nachricht der Sitzung, sobald
eine läuft. Beide Wege rufen dieselbe Funktion (modellzugang.modellzugang_nachricht) und liefern dieselbe Antwort.

  GET  /wamo/api/modellzugang   Stand des eigenen Profils (nur "hinterlegt" und die letzten vier Zeichen)
  POST /wamo/api/modellzugang   {"aktion": "hinterlegen" | "loeschen" | "modus" | "status",
                                 "schluesselart": "api" | "code", "schluessel": ..., "workspace": ..., "modus": ...}

Sicherheit
  * Wie alle Routen unter /wamo/api: Token mit zweitem Faktor (aal2), Höchstalter der Anmeldung, aktives Profil
    (wamo_api.identitaet). Die Profil-ID kommt nur aus der geprüften Identität, nie aus dem Body.
  * Der Body (höchstens 8 KB) wird nie geloggt und nie zurückgegeben; Antworten enthalten weder Schlüssel noch Token.
    Fehler (Format, Anthropic lehnt ab, Datenbank) stehen als Text in "fehler" der Antwort, nicht als Ausnahme mit Inhalt.
  * Die Begrenzung der Testanfragen bei Anthropic (5 je 10 Minuten und Profil) gilt auch hier.
  * Cache-Control: no-store.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from loguru import logger

import modellzugang as mz
from wamo_api import identitaet, lies_json

router = APIRouter()
MAX_BODY_BYTES = 8 * 1024  # ein Schlüssel ist unter 300 Zeichen lang


async def _ausfuehren(user_id: str, data: dict) -> dict:
    """Führt die Aktion aus und liefert die Nachricht, die sonst über RTVI an die Oberfläche ginge."""
    antworten: list[dict] = []

    async def senden(nachricht: dict) -> None:
        antworten.append(nachricht)

    await mz.modellzugang_nachricht(user_id, data, senden)
    if not antworten:  # nie ohne Antwort
        logger.error("Modellzugang-API: keine Antwort für Profil {}", user_id[:8])
        return {"type": "modellzugang", "aktion": "status", "fehler": "Unerwarteter Fehler, bitte später noch einmal"}
    return antworten[-1]


@router.get("/modellzugang")
async def stand(request: Request, response: Response) -> dict:
    ich = await identitaet(request)
    response.headers["Cache-Control"] = "no-store"
    return await _ausfuehren(ich.user_id, {"aktion": "status"})


@router.post("/modellzugang")
async def aendern(request: Request, response: Response) -> dict:
    ich = await identitaet(request)
    response.headers["Cache-Control"] = "no-store"
    data = await lies_json(request, MAX_BODY_BYTES)
    return await _ausfuehren(ich.user_id, data)
