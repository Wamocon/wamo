"""Durchlauf und Einwilligung eines Laborplatzes (Testlabor, Abschnitt 3.1): Der Dienst der Kollegen führt den Durchlauf, der Kern prüft ihn.

Vertrag Version 1, Aufruf V1: GET /api/kern/durchlauf mit Bearer WAMO_LABOR_TOKEN und Kopf X-WAMO-Platz liefert
{"durchlauf_id": "<UUID>", "offen": true|false, "einwilligung_version": ganze Zahl oder null, "einwilligung_zeit": ...}.
Der Kern nimmt eine Sitzung nur an, wenn der Durchlauf offen ist und einwilligung_version mindestens WAMO_LABOR_EINWILLIGUNG_MIN ist.
Er prüft das bei /start, bei jedem Offer (AccessMiddleware) und beim Sitzungsstart; danach fragt LaborWache alle 5 Sekunden und beendet
die Sitzung, wenn der Durchlauf endet oder wechselt. Ist der Dienst nicht erreichbar, gibt es keine neue Sitzung (503, fail-closed).
Eine laufende Sitzung läuft bei kurzem Ausfall weiter: ein Betreuer kann ohne Dienst auch keinen Durchlauf beenden.

Die Quelle wird gesetzt (quelle_setzen) oder ist der Dienstclient des Kerns (ClientQuelle, quelle_aus_env). Es gibt nur diesen einen Weg zum Dienst:
labor_client.client_aus_umgebung prüft Adresse (HTTPS, nie die Attrappe), Token, Zertifikat und die Antwort streng, für den Start, die Wache und
die Werkzeuge (labor_werkzeuge.py) gleich. Das Schaufenster hat keinen Platz und keinen Besucher: es braucht keinen Durchlauf.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from loguru import logger

import labor
import labor_client

CODE_BEENDET = "labor_durchlauf_beendet"
CODE_EINWILLIGUNG = "labor_keine_einwilligung"
CODE_DIENST = "labor_dienst_nicht_erreichbar"
POLL_SECS = 5.0
LESE_TIMEOUT_SECS = 5.0
GRUND_BEENDET = "Der Durchlauf ist beendet. Bitte den Betreuer am Platz fragen."
GRUND_EINWILLIGUNG = "Für diesen Platz liegt keine gültige Einwilligung vor. Bitte den Betreuer am Platz fragen."
GRUND_DIENST = "Der Labor-Dienst ist gerade nicht erreichbar. Bitte den Betreuer am Platz fragen."
GRUND_ENDE = "Der Durchlauf ist beendet."


class DurchlaufFehler(Exception):
    """Der Dienst antwortet nicht oder nicht nach dem Vertrag."""


@dataclass(frozen=True)
class Durchlauf:
    durchlauf_id: str
    offen: bool
    einwilligung_version: int | None


@dataclass(frozen=True)
class Urteil:
    """Ergebnis der Prüfung für die Middleware: status 200 oder 403 und 503 mit code und Grund für die Oberfläche."""

    status: int = 200
    code: str | None = None
    grund: str = ""
    stand: Durchlauf | None = None


class DurchlaufQuelle(Protocol):
    async def durchlauf(self, platz: int) -> Mapping[str, Any]: ...


def durchlauf_lesen(roh: object) -> Durchlauf:
    """Strenge Auswertung der Antwort V1. Alles, was vom Vertrag abweicht, ist ein Fehler (nie ein Durchlass)."""
    if not isinstance(roh, Mapping):
        raise DurchlaufFehler("Antwort ist kein Objekt")
    kennung, offen, version = roh.get("durchlauf_id"), roh.get("offen"), roh.get("einwilligung_version")
    try:
        kennung = str(uuid.UUID(kennung)) if isinstance(kennung, str) else None
    except ValueError:
        kennung = None
    if kennung is None or not isinstance(offen, bool):
        raise DurchlaufFehler("durchlauf_id oder offen ungültig")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 0):
        raise DurchlaufFehler("einwilligung_version ungültig")
    return Durchlauf(kennung, offen, version)


def _einwilligung_gueltig(stand: Durchlauf, min_version: int) -> bool:
    return stand.einwilligung_version is not None and stand.einwilligung_version >= min_version


# ---------- Quelle ----------
class ClientQuelle:
    """Aufruf V1 über den Dienstclient des Kerns (labor_client.py): derselbe Weg wie bei den Werkzeugen, nur dass hier der Durchlauf gefragt wird.
    Der Client wird bei jeder Frage aus WAMO_LABOR_URL und WAMO_LABOR_TOKEN gebaut und danach geschlossen (die Wache fragt nur alle 5 Sekunden).
    Ist die Einstellung unbrauchbar, wirft die Frage LaborKonfigFehler: die Prüfung macht daraus 503 (fail-closed). transport: nur für Tests."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    async def durchlauf(self, platz: int) -> Mapping[str, Any]:
        client = labor_client.client_aus_umgebung(platz, transport=self._transport)
        try:
            stand = await client.durchlauf()
        finally:
            await client.aclose()
        return {"durchlauf_id": stand.id, "offen": stand.offen, "einwilligung_version": stand.einwilligung_version}


def quelle_aus_env() -> DurchlaufQuelle:
    return ClientQuelle()


_quelle: DurchlaufQuelle | None = None


def quelle_setzen(quelle: DurchlaufQuelle | None) -> None:
    """Hängt die Quelle für den ganzen Prozess ein (Dienstclient oder Test). None löscht sie, dann gilt wieder die Umgebung."""
    global _quelle
    _quelle = quelle


def quelle_holen() -> DurchlaufQuelle:
    global _quelle
    if _quelle is None:
        _quelle = quelle_aus_env()
    return _quelle


def _ursache(e: Exception) -> str:
    """Für das Log: der Typ des Fehlers, bei einer unbrauchbaren Einstellung auch ihr Satz (er nennt nie einen Wert)."""
    return f"{type(e).__name__}: {e}" if isinstance(e, labor_client.LaborKonfigFehler) else type(e).__name__


# ---------- Prüfung ----------
async def _lesen(quelle: DurchlaufQuelle, platz: int) -> Durchlauf:
    return durchlauf_lesen(await asyncio.wait_for(quelle.durchlauf(platz), LESE_TIMEOUT_SECS + 1))


async def pruefen(identity: object, *, quelle: DurchlaufQuelle | None = None, min_version: int | None = None) -> Urteil:
    """Darf diese Laborsitzung beginnen? Der Platz kommt aus der Identität. Das Schaufenster hat keinen Platz und keinen Durchlauf."""
    if labor.art_normalisieren(getattr(identity, "labor_art", None)) != "platz":
        return Urteil()
    quelle = quelle if quelle is not None else quelle_holen()
    min_version = min_version if min_version is not None else labor.einwilligung_min()
    platz = getattr(identity, "labor_platz", None)
    if min_version is None or not labor.laborfelder_stimmig("platz", "mitglied", platz, False):
        logger.error("Labor: Einwilligungsversion oder Platz nicht eingerichtet, keine Sitzung")
        return Urteil(503, CODE_DIENST, GRUND_DIENST)
    try:
        stand = await _lesen(quelle, platz)
    except Exception as e:  # fail-closed: jeder Fehler des Dienstes heißt keine Sitzung (ins Log nur der Typ, bei der Einstellung ihr Satz)
        logger.warning("Labor: Durchlauf nicht lesbar ({})", _ursache(e))
        return Urteil(503, CODE_DIENST, GRUND_DIENST)
    if not stand.offen:
        return Urteil(403, CODE_BEENDET, GRUND_BEENDET, stand)
    if not _einwilligung_gueltig(stand, min_version):
        return Urteil(403, CODE_EINWILLIGUNG, GRUND_EINWILLIGUNG, stand)
    return Urteil(200, None, "", stand)


class LaborWache:
    """Fragt den Durchlauf einer laufenden Laborsitzung alle 5 Sekunden und liefert den Grund für das Ende. Wechselt die durchlauf_id,
    wird der Durchlauf beendet oder fällt die Einwilligung weg, endet die Sitzung. Ein kurzer Ausfall des Dienstes beendet sie nicht."""

    def __init__(self, platz: int, stand: Durchlauf, *, quelle: DurchlaufQuelle | None = None, min_version: int | None = None,
                 interval: float = POLL_SECS, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.platz = platz
        self.stand = stand
        self._quelle = quelle
        self._min = min_version if min_version is not None else labor.einwilligung_min()
        self.interval = interval
        self._sleep = sleep
        self.fehler = 0

    @property
    def durchlauf_id(self) -> str:
        return self.stand.durchlauf_id

    async def check(self) -> str | None:
        quelle = self._quelle if self._quelle is not None else quelle_holen()
        try:
            neu = await _lesen(quelle, self.platz)
        except Exception as e:
            self.fehler += 1
            if self.fehler in (1, 12) or self.fehler % 60 == 0:
                logger.warning("Labor: Durchlauf nicht lesbar ({}), Versuch {}", _ursache(e), self.fehler)
            return None
        self.fehler = 0
        if neu.durchlauf_id != self.stand.durchlauf_id or not neu.offen:
            return GRUND_ENDE
        if self._min is None or not _einwilligung_gueltig(neu, self._min):
            return GRUND_ENDE
        return None

    async def run(self, on_end: Callable[[str], Awaitable[None]]) -> None:
        while True:
            await self._sleep(self.interval)
            grund = await self.check()
            if grund:
                with contextlib.suppress(Exception):
                    await on_end(grund)
                return
