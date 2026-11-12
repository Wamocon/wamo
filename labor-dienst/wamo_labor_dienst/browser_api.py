"""Schnittstelle für die Board-Seite im Browser: nur lesend (GET), nur mit dem Platz-Token im Kopf X-Platz-Token (D6, D8, D9).

Der Platz kommt immer aus dem Token, nie aus der Eingabe: Es gibt keinen Pfad und keinen Parameter mit einer Platznummer. Der Token steht nie
in der Adresszeile (eine Abfrage ist 422) und nie im Protokoll. Der Bearer des Kerns und der Betreuer-Token öffnen diesen Pfad nicht.
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from .regeln import DienstFehler, eingabe
from .sicherheit import dienst_von, fehlerfrei, fehlversuch, json_antwort, kopf_eins


@fehlerfrei
async def board(request: Request):
    dienst = dienst_von(request)
    platz = dienst.platz_von_token(kopf_eins(request, "x-platz-token") or "")  # nur im Speicher: ein unbekannter Token kostet keinen Faden und keine Abfrage
    if platz is None:
        raise fehlversuch(request, dienst, DienstFehler(401, "token_ungueltig", "Der Zugang ist nicht gültig."))
    request.state.platz = platz
    if not dienst.board_je_token.erlaubt(platz) or not dienst.board_global.erlaubt("alle"):  # erst die Grenze des Tokens, damit einer nicht alle sperrt
        raise DienstFehler(429, "quote_erreicht", "Zu viele Anfragen, bitte später.", retry_after=1)
    if request.scope.get("query_string"):
        raise eingabe("Dieser Aufruf nimmt keine Abfrage.")
    return json_antwort(200, await run_in_threadpool(dienst.plaetze[platz].board_bytes))


def registrieren(app: FastAPI) -> None:
    app.add_api_route("/api/browser/board", board, methods=["GET"], include_in_schema=False, response_model=None)
