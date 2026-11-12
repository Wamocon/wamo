"""Betreuer-Funktionen unter /api/betreuer/ (D9), nur mit dem Betreuer-Token als Bearer.

GET  /api/betreuer/plaetze                          Zustand aller 16 Plätze ohne Texte
POST /api/betreuer/plaetze/<n>/durchlauf-beenden    V1 liefert offen falsch, Schreiben mit der alten durchlauf_id ist 409
POST /api/betreuer/plaetze/<n>/neuer-durchlauf      Körper {"einwilligung_version": <int>}: Seed, neue durchlauf_id, Einwilligung mit Zeit,
                                                    der alte Platz-Token verliert den Zugang (neuen holt der Betreuer mit platz-token)
POST /api/betreuer/plaetze/<n>/zuruecksetzen        Board und Ablage auf den Seed, Durchlauf bleibt
POST /api/betreuer/plaetze/<n>/daten-loeschen       alle Daten des Platzes, danach geschlossen und ohne Einwilligung, Platz-Token entzogen
POST /api/betreuer/plaetze/<n>/platz-token          erzeugt oder erneuert den Platz-Token, gibt ihn EINMAL zurück, speichert nur den Hash
Ohne Token 401, mit falschem Token (auch Platz-Token und Bearer des Kerns) 403. Jeder Aufruf steht mit Zeit, Platz und Art im Protokoll, nie mit Texten.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from .datenbank import NichtBereinigt
from .dienst import Dienst
from .regeln import PLAETZE, PLATZ_RE, DienstFehler, eingabe, json_lesen, nur_felder
from .sicherheit import betreuer_zugang, dienst_von, fehlerfrei, json_antwort, koerper_lesen, kopf_eins, neuer_platz_token, token_hash

KOERPER_MAX = 1024
ABNAHME_PLAETZE = (15, 16)
EINWILLIGUNG_MAX = 1000  # eine vernünftige Obergrenze: Ein Tippfehler (zum Beispiel 77777) sperrt nicht für immer alle kleineren Versionen


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


def _einwilligung(version: object) -> int:
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= EINWILLIGUNG_MAX:
        raise eingabe("Das Feld einwilligung_version ist ungültig.")
    return version


async def _beenden(request: Request, dienst: Dienst, platz: int) -> dict:
    await _ohne_inhalt(request)
    await run_in_threadpool(dienst.plaetze[platz].durchlauf_beenden)
    return await run_in_threadpool(_zustand, dienst, platz)


async def _neuer_durchlauf(request: Request, dienst: Dienst, platz: int) -> dict:
    roh = await koerper_lesen(request, KOERPER_MAX)
    if (kopf_eins(request, "content-type") or "").split(";")[0].strip().lower() != "application/json":
        raise eingabe("Der Inhalt muss JSON sein.")
    version = _einwilligung(nur_felder(json_lesen(roh), {"einwilligung_version"})["einwilligung_version"])
    await run_in_threadpool(dienst.neuer_besucher, platz, version)
    return await run_in_threadpool(_zustand, dienst, platz)


async def _zuruecksetzen(request: Request, dienst: Dienst, platz: int) -> dict:
    await _ohne_inhalt(request)
    await run_in_threadpool(dienst.plaetze[platz].zuruecksetzen)
    return await run_in_threadpool(_zustand, dienst, platz)


async def _daten_loeschen(request: Request, dienst: Dienst, platz: int) -> dict:
    await _ohne_inhalt(request)
    try:
        await run_in_threadpool(dienst.platz_leeren, platz)
    except NichtBereinigt:  # kein Erfolg melden: Die Daten sind gelöscht, die Datei aber noch nicht bereinigt (der Betreuer wiederholt den Aufruf)
        raise DienstFehler(503, "dienst_fehler", "Das Löschen ist noch nicht abgeschlossen, die Datei ist nicht bereinigt. Bitte wiederholen.") from None
    return await run_in_threadpool(_zustand, dienst, platz)


async def _platz_token(request: Request, dienst: Dienst, platz: int) -> dict:
    await _ohne_inhalt(request)
    token = neuer_platz_token()
    await run_in_threadpool(dienst.token_vergeben, platz, token_hash(token))
    return {"platz": platz, "platz_token": token}


def _spur(dienst: Dienst, aktion: str, platz: int | None, status: int, angemeldet: bool, **zusatz: object) -> None:
    """Mit gültigem Token: Betreuer-Spur (eigene Datei, nie abgetastet, nie rotiert). Ohne: abgetastet in der allgemeinen Datei (D10)."""
    if angemeldet:
        dienst.protokoll.betreuer(aktion=aktion, platz=platz, status=status, **zusatz)
    else:
        dienst.protokoll.abgetastet("betreuer_abgelehnt", aktion=aktion, bereich="betreuer", platz=platz, status=status)


def _aktion(name: str, funktion: Callable[[Request, Dienst, int], Awaitable[dict]]):
    @fehlerfrei
    async def endpunkt(request: Request):
        dienst, status, angemeldet = dienst_von(request), 500, False
        try:
            betreuer_zugang(request, dienst)
            angemeldet = True
            antwort = json_antwort(200, await funktion(request, dienst, _platz(request)))
            status = antwort.status_code
            return antwort
        except DienstFehler as e:
            status = e.status
            raise
        finally:
            _spur(dienst, name, _platz_oder_none(request), status, angemeldet)

    return endpunkt


@fehlerfrei
async def plaetze(request: Request):
    dienst, status, angemeldet = dienst_von(request), 500, False
    try:
        betreuer_zugang(request, dienst)
        angemeldet = True
        liste = await run_in_threadpool(lambda: [_zustand(dienst, n) for n in PLAETZE])
        status = 200
        return json_antwort(200, {"plaetze": liste})
    except DienstFehler as e:
        status = e.status
        raise
    finally:
        _spur(dienst, "plaetze", None, status, angemeldet)


@fehlerfrei
async def einwilligung_minimum_setzen(request: Request):
    """Rücksetzweg für das Maximum der Einwilligungsversion (zum Beispiel nach einem Tippfehler): Körper {"einwilligung_version": <int>, "bestaetigung":
    "senken"}, nur zum Senken, nur wenn kein Durchlauf läuft (409 durchlauf_laeuft), mit Eintrag in der Betreuer-Spur."""
    dienst, status, angemeldet, wert = dienst_von(request), 500, False, None
    try:
        betreuer_zugang(request, dienst)
        angemeldet = True
        roh = await koerper_lesen(request, KOERPER_MAX)
        if (kopf_eins(request, "content-type") or "").split(";")[0].strip().lower() != "application/json":
            raise eingabe("Der Inhalt muss JSON sein.")
        daten = nur_felder(json_lesen(roh), {"einwilligung_version", "bestaetigung"})
        wert = _einwilligung(daten["einwilligung_version"])
        if daten["bestaetigung"] != "senken":
            raise eingabe("Das Feld bestaetigung muss senken lauten.")
        await run_in_threadpool(dienst.einwilligung_minimum_setzen, wert)
        status = 200
        return json_antwort(200, {"einwilligung_hoechste": wert})
    except DienstFehler as e:
        status = e.status
        raise
    finally:
        _spur(dienst, "einwilligung_minimum_setzen", None, status, angemeldet, wert=wert)


def registrieren(app: FastAPI) -> None:
    app.add_api_route("/api/betreuer/einwilligung-minimum-setzen", einwilligung_minimum_setzen, methods=["POST"], include_in_schema=False, response_model=None)
    app.add_api_route("/api/betreuer/plaetze", plaetze, methods=["GET"], include_in_schema=False, response_model=None)
    for pfad, name, funktion in (("durchlauf-beenden", "durchlauf_beenden", _beenden), ("neuer-durchlauf", "neuer_durchlauf", _neuer_durchlauf),
                                 ("zuruecksetzen", "zuruecksetzen", _zuruecksetzen), ("daten-loeschen", "daten_loeschen", _daten_loeschen),
                                 ("platz-token", "platz_token", _platz_token)):
        app.add_api_route(f"/api/betreuer/plaetze/{{n}}/{pfad}", _aktion(name, funktion), methods=["POST"], include_in_schema=False, response_model=None)
