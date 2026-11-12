"""Die sieben Aufrufe V1 bis V7 des Vertrags unter /api/kern/ (nur für den Kern, nur mit dem Bearer des Kerns).

Reihenfolge der Prüfungen (Vertrag 2.2): 1 Pfad und Methode (Router), 2 Bearer, 3 Platz, 4 Größe, 5 Form (Nummer, Abfrage, Inhaltstyp, JSON, Felder,
Werte, Köpfe), dann im Speicher 6 Wiederholung, 7 Schreibrate, 8 Durchlauf, 9 Ticket, 10 Version, 11 Mengen (alles in einer Transaktion, D13).
Der Platz kommt nur aus dem Kopf des Kerns (D6). Es gibt keinen Weg zum Löschen (D7): keine Methode DELETE und keine Route dafür.
"""
from __future__ import annotations

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from .modell import Aufruf, fingerabdruck
from .regeln import ANFRAGE_MAX, NUMMER_RE, PRUEFER, UUID_RE, eingabe, json_lesen, parse_suche
from .sicherheit import dienst_von, fehlerfrei, json_antwort, koerper_lesen, kopf_eins, kern_zugang


def _ohne_abfrage(request: Request) -> None:
    if request.scope.get("query_string"):
        raise eingabe("Dieser Aufruf nimmt keine Abfrage.")


def _nummer(request: Request) -> int:
    nummer = request.path_params["nummer"]
    if not NUMMER_RE.fullmatch(nummer):
        raise eingabe("Die Ticketnummer hat eine falsche Form.")
    return int(nummer[4:])


async def _v1(request: Request, platz: int):
    _ohne_abfrage(request)
    return json_antwort(200, await run_in_threadpool(dienst_von(request).plaetze[platz].durchlauf))


async def _v2(request: Request, platz: int):
    suche = parse_suche(request.scope.get("query_string", b""))
    return json_antwort(200, await run_in_threadpool(dienst_von(request).plaetze[platz].suchen, suche))


async def _v3(request: Request, platz: int):
    nummer = _nummer(request)
    _ohne_abfrage(request)
    ticket = await run_in_threadpool(dienst_von(request).plaetze[platz].ticket, nummer)
    if ticket is None:
        return json_antwort(404, {"fehler": "ticket_unbekannt", "text": "Dieses Ticket gibt es nicht."})
    return json_antwort(200, ticket)


def _kopf_uuid(request: Request, name: str) -> str:
    wert = kopf_eins(request, name)
    if wert is None or not UUID_RE.fullmatch(wert):
        raise eingabe("Die Köpfe X-WAMO-Durchlauf und Idempotency-Key fehlen oder sind ungültig.")
    return wert.lower()


async def _schreiben(request: Request, platz: int, name: str):
    roh = await koerper_lesen(request, ANFRAGE_MAX)  # 4. Größe
    nummer = _nummer(request) if name != "tickets" else None  # 5. Form
    _ohne_abfrage(request)
    if (kopf_eins(request, "content-type") or "").split(";")[0].strip().lower() != "application/json":
        raise eingabe("Der Inhalt muss JSON sein.")
    daten = json_lesen(roh)
    werte = PRUEFER[name](daten)
    durchlauf, schluessel = _kopf_uuid(request, "x-wamo-durchlauf"), _kopf_uuid(request, "idempotency-key")
    aufruf = Aufruf(name=name, nummer=nummer, werte=werte, fingerabdruck=fingerabdruck(name, nummer, daten), durchlauf=durchlauf, schluessel=schluessel)
    antwort = await run_in_threadpool(dienst_von(request).plaetze[platz].schreiben, aufruf)
    return json_antwort(antwort.status, antwort.koerper, extra={"Idempotent-Replayed": "true"} if antwort.wiederholt else None)


def _wahl(lesen, schreiben_name: str | None):
    """Ein Endpunkt je Pfad: GET liest, die anderen Methoden des Pfades schreiben (der Router lässt nur erlaubte Methoden durch)."""
    @fehlerfrei
    async def endpunkt(request: Request):
        platz = kern_zugang(request, dienst_von(request))
        if request.method == "GET":
            return await lesen(request, platz)
        return await _schreiben(request, platz, schreiben_name)

    return endpunkt


def registrieren(app: FastAPI) -> None:
    routen = (("/api/kern/durchlauf", ["GET"], _wahl(_v1, None)), ("/api/kern/tickets", ["GET", "POST"], _wahl(_v2, "tickets")),
              ("/api/kern/tickets/{nummer}", ["GET", "PATCH"], _wahl(_v3, "ticket")), ("/api/kern/tickets/{nummer}/status", ["POST"], _wahl(None, "status")),
              ("/api/kern/tickets/{nummer}/kommentare", ["POST"], _wahl(None, "kommentare")))
    for pfad, methoden, endpunkt in routen:
        app.add_api_route(pfad, endpunkt, methods=methoden, include_in_schema=False, response_model=None)
