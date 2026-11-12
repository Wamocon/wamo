"""Schnittstelle für die Board-Seite im Browser: nur lesend (GET), nur mit dem Platz-Token im Kopf X-Platz-Token (D6, D8, D9).

Der Platz kommt immer aus dem Token, nie aus der Eingabe: Es gibt keinen Pfad und keinen Parameter mit einer Platznummer. Der Token steht nie
in der Adresszeile (eine Abfrage ist 422) und nie im Protokoll. Der Bearer des Kerns und der Betreuer-Token öffnen diesen Pfad nicht.
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from .regeln import DienstFehler, eingabe
from .sicherheit import PLATZ_TOKEN_RE, dienst_von, fehlerfrei, json_antwort, kopf_eins, token_hash


@fehlerfrei
async def board(request: Request):
    dienst = dienst_von(request)
    token = kopf_eins(request, "x-platz-token") or ""
    platz = await run_in_threadpool(dienst.zentral.platz_von_hash, token_hash(token)) if PLATZ_TOKEN_RE.fullmatch(token) else None
    if platz is None:
        raise DienstFehler(401, "token_ungueltig", "Der Zugang ist nicht gültig.")
    request.state.platz = platz
    if request.scope.get("query_string"):
        raise eingabe("Dieser Aufruf nimmt keine Abfrage.")
    return json_antwort(200, await run_in_threadpool(dienst.plaetze[platz].board))


def registrieren(app: FastAPI) -> None:
    app.add_api_route("/api/browser/board", board, methods=["GET"], include_in_schema=False, response_model=None)
