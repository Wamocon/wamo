"""Änderungen: Stand der Selbstüberarbeitung für die Oberfläche (Schritt 1.5, docs/selbstueberarbeitung.md D.5).

  GET /wamo/api/release/status   Stand des Release-Dienstes in einer geprüften Sicht (nur Rolle admin)

Der Kern liest den Stand über den Socket des Release-Dienstes (release_client.py) und gibt nur die Sicht weiter, nie die rohe Antwort. Er kann hier
nichts ändern: Anfragen vorschlagen kommt mit den Werkzeugen (Schritt 3.4), Freigaben nimmt allein der Freigabe-Dienst des Ankers an (die Oberfläche
ruft ihn über Caddy direkt, am Kern vorbei, Schritt 1.4c).

Ist der Release-Dienst nicht erreichbar, antwortet die Route trotzdem mit 200 und {"verfuegbar": false, "grund": "<Schlüsselwort>"}: Die Karte zeigt dann
"nicht erreichbar" statt eines Fehlers. Rate-Grenze: 60 Abfragen je Minute und Profil.
"""

from __future__ import annotations

import asyncio
from collections import deque
from time import monotonic

from fastapi import APIRouter, HTTPException, Request, Response

import release_client
from wamo_api import identitaet

router = APIRouter()

STATUS_GRENZE = (60, 60.0)
_versuche: dict[str, deque] = {}


def _drosseln(user_id: str, jetzt: float | None = None) -> None:
    anzahl, fenster = STATUS_GRENZE
    jetzt = monotonic() if jetzt is None else jetzt
    q = _versuche.setdefault(user_id, deque())
    while q and jetzt - q[0] > fenster:
        q.popleft()
    if len(q) >= anzahl:
        raise HTTPException(429, "Zu viele Anfragen, bitte in ein paar Minuten noch einmal")
    q.append(jetzt)
    if len(_versuche) > 200:
        for k in [k for k, d in _versuche.items() if not d or jetzt - d[-1] > 600.0]:
            del _versuche[k]


@router.get("/release/status")
async def status(request: Request, response: Response) -> dict:
    ich = await identitaet(request)
    if not ich.is_admin:
        raise HTTPException(403, "Die Änderungen sind nur für Admins")
    _drosseln(ich.user_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await asyncio.to_thread(release_client.status)
    except release_client.ReleaseFehler as e:
        return {"verfuegbar": False, "grund": e.grund}
