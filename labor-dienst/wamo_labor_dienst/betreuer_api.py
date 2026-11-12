"""Betreuer-Funktionen unter /api/betreuer/ (D9), nur mit dem Betreuer-Token als Bearer.

GET  /api/betreuer/plaetze                          Zustand aller 16 Plätze ohne Texte
POST /api/betreuer/plaetze/<n>/durchlauf-beenden    V1 liefert offen falsch, Schreiben mit der alten durchlauf_id ist 409
POST /api/betreuer/plaetze/<n>/neuer-durchlauf      Körper {"einwilligung_version": <int>}: Seed, neue durchlauf_id, Einwilligung mit Zeit
POST /api/betreuer/plaetze/<n>/zuruecksetzen        Board und Ablage auf den Seed, Durchlauf bleibt
POST /api/betreuer/plaetze/<n>/daten-loeschen       alle Daten des Platzes, danach geschlossen und ohne Einwilligung
POST /api/betreuer/plaetze/<n>/platz-token          erzeugt oder erneuert den Platz-Token, gibt ihn EINMAL zurück, speichert nur den Hash
Ohne Token 401, mit falschem Token (auch Platz-Token und Bearer des Kerns) 403. Jeder Aufruf steht mit Zeit, Platz und Art im Protokoll, nie mit Texten.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from .dienst import Dienst
from .regeln import PLAETZE, PLATZ_RE, DienstFehler, eingabe, json_lesen, nur_felder
from .sicherheit import betreuer_zugang, dienst_von, fehlerfrei, json_antwort, koerper_lesen, kopf_eins, neuer_platz_token, token_hash

KOERPER_MAX = 1024
ABNAHME_PLAETZE = (15, 16)
EINWILLIGUNG_MAX = 2**31 - 1


def _platz(request: Request) -> int:
    roh = request.path_params["n"]
    if not PLATZ_RE.fullmatch(roh) or int(roh) not in PLAETZE:
        raise DienstFehler(404, "platz_unbekannt", "Dieser Platz ist nicht bekannt.")
    return int(roh)


def _platz_oder_none(request: Request) -> int | None:
    roh = request.path_params.get("n", "")
    return int(roh) if PLATZ_RE.fullmatch(roh) and int(roh) in PLAETZE else None


def _zustand(dienst: Dienst, platz: int) -> dict:
    return {**dienst.plaetze[platz].zustand(), "hat_platz_token": dienst.zentral.hat_token(platz), "reserviert": platz in ABNAHME_PLAETZE}


async def _ohne_inhalt(request: Request) -> None:
    """Aktionen ohne Eingabe: kein Inhalt oder ein leeres Objekt, alles andere ist 422."""
    if (await koerper_lesen(request, KOERPER_MAX)).strip() not in (b"", b"{}"):
        raise eingabe("Diese Aktion nimmt keine Eingabe an.")


async def _beenden(request: Request, dienst: Dienst, platz: int) -> dict:
    await _ohne_inhalt(request)
    await run_in_threadpool(dienst.plaetze[platz].durchlauf_beenden)
    return await run_in_threadpool(_zustand, dienst, platz)


async def _neuer_durchlauf(request: Request, dienst: Dienst, platz: int) -> dict:
    roh = await koerper_lesen(request, KOERPER_MAX)
    if (kopf_eins(request, "content-type") or "").split(";")[0].strip().lower() != "application/json":
        raise eingabe("Der Inhalt muss JSON sein.")
    version = nur_felder(json_lesen(roh), {"einwilligung_version"})["einwilligung_version"]
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= EINWILLIGUNG_MAX:
        raise eingabe("Das Feld einwilligung_version ist ungültig.")
    await run_in_threadpool(dienst.plaetze[platz].neuer_durchlauf, version)
    return await run_in_threadpool(_zustand, dienst, platz)


async def _zuruecksetzen(request: Request, dienst: Dienst, platz: int) -> dict:
    await _ohne_inhalt(request)
    await run_in_threadpool(dienst.plaetze[platz].zuruecksetzen)
    return await run_in_threadpool(_zustand, dienst, platz)


async def _daten_loeschen(request: Request, dienst: Dienst, platz: int) -> dict:
    await _ohne_inhalt(request)
    await run_in_threadpool(dienst.plaetze[platz].daten_loeschen)
    return await run_in_threadpool(_zustand, dienst, platz)


async def _platz_token(request: Request, dienst: Dienst, platz: int) -> dict:
    await _ohne_inhalt(request)
    token = neuer_platz_token()
    await run_in_threadpool(dienst.zentral.token_setzen, platz, token_hash(token))
    return {"platz": platz, "platz_token": token}


def _aktion(name: str, funktion: Callable[[Request, Dienst, int], Awaitable[dict]]):
    @fehlerfrei
    async def endpunkt(request: Request):
        dienst, status = dienst_von(request), 500
        try:
            betreuer_zugang(request, dienst)
            antwort = json_antwort(200, await funktion(request, dienst, _platz(request)))
            status = antwort.status_code
            return antwort
        except DienstFehler as e:
            status = e.status
            raise
        finally:
            dienst.protokoll.ereignis("betreuer", aktion=name, platz=_platz_oder_none(request), status=status)

    return endpunkt


@fehlerfrei
async def plaetze(request: Request):
    dienst, status = dienst_von(request), 500
    try:
        betreuer_zugang(request, dienst)
        liste = await run_in_threadpool(lambda: [_zustand(dienst, n) for n in PLAETZE])
        status = 200
        return json_antwort(200, {"plaetze": liste})
    except DienstFehler as e:
        status = e.status
        raise
    finally:
        dienst.protokoll.ereignis("betreuer", aktion="plaetze", platz=None, status=status)


def registrieren(app: FastAPI) -> None:
    app.add_api_route("/api/betreuer/plaetze", plaetze, methods=["GET"], include_in_schema=False, response_model=None)
    for pfad, name, funktion in (("durchlauf-beenden", "durchlauf_beenden", _beenden), ("neuer-durchlauf", "neuer_durchlauf", _neuer_durchlauf),
                                 ("zuruecksetzen", "zuruecksetzen", _zuruecksetzen), ("daten-loeschen", "daten_loeschen", _daten_loeschen),
                                 ("platz-token", "platz_token", _platz_token)):
        app.add_api_route(f"/api/betreuer/plaetze/{{n}}/{pfad}", _aktion(name, funktion), methods=["POST"], include_in_schema=False, response_model=None)
