"""Durchlauf und Einwilligung eines Laborplatzes (Testlabor, Abschnitt 3.1): Der Dienst der Kollegen führt den Durchlauf, der Kern prüft ihn.

Vertrag Version 1, Aufruf V1: GET /api/kern/durchlauf mit Bearer WAMO_LABOR_TOKEN und Kopf X-WAMO-Platz liefert
{"durchlauf_id": "<UUID>", "offen": true|false, "einwilligung_version": ganze Zahl oder null, "einwilligung_zeit": ...}.
Der Kern nimmt eine Sitzung nur an, wenn der Durchlauf offen ist und einwilligung_version mindestens WAMO_LABOR_EINWILLIGUNG_MIN ist.
Er prüft das bei /start, bei jedem Offer (AccessMiddleware) und beim Sitzungsstart; danach fragt LaborWache alle 5 Sekunden und beendet
die Sitzung, wenn der Durchlauf endet oder wechselt. Ist der Dienst nicht erreichbar, gibt es keine neue Sitzung (503, fail-closed).
Eine laufende Sitzung läuft bei kurzem Ausfall weiter: ein Betreuer kann ohne Dienst auch keinen Durchlauf beenden.

Die Quelle (Dienstclient) wird gesetzt (quelle_setzen) oder aus WAMO_LABOR_URL und WAMO_LABOR_TOKEN gebaut (quelle_aus_env). Das Schaufenster
hat keinen Platz und keinen Besucher: es braucht keinen Durchlauf.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import ssl
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from loguru import logger

import labor
from wamo_tls import https_context

DURCHLAUF_PFAD = "/api/kern/durchlauf"
CODE_BEENDET = "labor_durchlauf_beendet"
CODE_EINWILLIGUNG = "labor_keine_einwilligung"
CODE_DIENST = "labor_dienst_nicht_erreichbar"
POLL_SECS = 5.0
LESE_TIMEOUT_SECS = 5.0
MAX_ANTWORT_BYTES = 64 * 1024
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
def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class HttpQuelle:
    """Aufruf V1 über HTTPS (mit der WAMO-CA aus WAMO_CA_FILE) gegen den Dienst der Kollegen. Der Token geht nur im Kopf Authorization."""

    def __init__(self, url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 ssl_context: ssl.SSLContext | None = None):
        teile = urlsplit(url.strip())
        if not ((teile.scheme == "https" and teile.hostname) or (teile.scheme == "http" and _loopback(teile.hostname or ""))):
            raise ValueError("WAMO_LABOR_URL braucht https (http nur über Loopback)")
        if not token:
            raise ValueError("WAMO_LABOR_TOKEN fehlt")
        self._basis = url.strip().rstrip("/")
        self._token = token
        self._transport = transport
        self._verify: ssl.SSLContext | bool = ssl_context if ssl_context is not None else (https_context() or True)

    async def durchlauf(self, platz: int) -> Mapping[str, Any]:
        async with httpx.AsyncClient(timeout=LESE_TIMEOUT_SECS, transport=self._transport, verify=self._verify) as client:
            antwort = await client.get(f"{self._basis}{DURCHLAUF_PFAD}",
                                       headers={"Authorization": f"Bearer {self._token}", "X-WAMO-Platz": str(int(platz))})
        if antwort.status_code != 200:
            raise DurchlaufFehler(f"Dienst antwortet {antwort.status_code}")
        if len(antwort.content) > MAX_ANTWORT_BYTES:
            raise DurchlaufFehler("Antwort zu groß")
        try:
            return antwort.json()
        except ValueError as e:
            raise DurchlaufFehler("Antwort ist kein JSON") from e


def quelle_aus_env() -> DurchlaufQuelle | None:
    url, token = os.getenv("WAMO_LABOR_URL") or "", os.getenv("WAMO_LABOR_TOKEN") or ""
    if not url or not token:
        return None
    try:
        return HttpQuelle(url, token)
    except ValueError as e:
        logger.error("Labor-Dienst nicht nutzbar: {}", e)
        return None


_quelle: DurchlaufQuelle | None = None


def quelle_setzen(quelle: DurchlaufQuelle | None) -> None:
    """Hängt die Quelle für den ganzen Prozess ein (Dienstclient oder Test). None löscht sie, dann gilt wieder die Umgebung."""
    global _quelle
    _quelle = quelle


def quelle_holen() -> DurchlaufQuelle | None:
    global _quelle
    if _quelle is None:
        _quelle = quelle_aus_env()
    return _quelle


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
    if quelle is None or min_version is None or not labor.laborfelder_stimmig("platz", "mitglied", platz, False):
        logger.error("Labor: Dienst oder Einwilligungsversion nicht eingerichtet, keine Sitzung")
        return Urteil(503, CODE_DIENST, GRUND_DIENST)
    try:
        stand = await _lesen(quelle, platz)
    except Exception as e:  # fail-closed: jeder Fehler des Dienstes heißt keine Sitzung (nur der Typ ins Log)
        logger.warning("Labor: Durchlauf nicht lesbar ({})", type(e).__name__)
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
            if quelle is None:
                raise DurchlaufFehler("keine Quelle")
            neu = await _lesen(quelle, self.platz)
        except Exception as e:
            self.fehler += 1
            if self.fehler in (1, 12) or self.fehler % 60 == 0:
                logger.warning("Labor: Durchlauf nicht lesbar ({}), Versuch {}", type(e).__name__, self.fehler)
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
