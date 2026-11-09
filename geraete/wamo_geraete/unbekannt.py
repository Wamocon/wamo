"""Passive Erkennung unbekannter Geräte im Büro-LAN: Abgleich der Hostliste der FRITZ!Box mit den Geräten des Dienstes.

Bekannt ist ein Gerät, wenn
  * seine Adresse zu einem Gerät aus geraete.toml gehört (oder Sokrates-2 ist) oder in bekannt.toml steht,
  * seine MAC-Adresse als mac bei einem Gerät aus geraete.toml oder in bekannt.toml steht,
  * sein Name (ohne .local und .fritz.box, Groß- und Kleinschreibung egal) als mdns_name aus geraete.toml oder als name in
    bekannt.toml steht.
Alles andere ist unbekannt. Es wird nichts gescannt: Die einzige Quelle ist die Hostliste des Routers (treiber/hostliste.py).
Hat die Box mehr Geräte, als gelesen werden, trägt das Ergebnis abgeschnitten: true (in beiden Werkzeugen).

Zwischenspeicher: Die Liste wird höchstens einmal in 60 Sekunden neu geholt, auch wenn mehrere Profile fragen. Ist die letzte
Messung älter, aber nicht älter als 10 Minuten, bekommt der Aufrufer sie sofort und die neue Messung läuft im Hintergrund
(so bleibt die Büro-Karte schnell und die Zahl flackert nicht). Nach einem Fehler wartet der Dienst 30 Sekunden, bevor er die
Box erneut fragt. Eine abgebrochene Anfrage (Zeitgrenze des Aufrufers) bricht die laufende Messung nicht ab.
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .konfig import SOKRATES2, Konfig
from .text import name
from .treiber import GeraetFehler, NichtEingerichtet
from .treiber.hostliste import Host, Hostliste

FRISCH_SECS = 60.0
ALT_SECS = 600.0
FEHLER_PAUSE_SECS = 30.0
_NAMENSENDEN = (".fritz.box", ".local")


def _norm(wert: object) -> str:
    """Name für den Vergleich: gesäubert, klein geschrieben, ohne .local und .fritz.box."""
    n = name(wert, 63).lower()
    for ende in _NAMENSENDEN:
        if n.endswith(ende):
            n = n[: -len(ende)]
    return n


def vergleichen(hosts: list[Host], konfig: Konfig) -> list[Host]:
    """Die unbekannten Hosts, sortiert nach Adresse (ohne Adresse zuerst) und Name."""
    geraete = konfig.geraete.values()
    adressen = {SOKRATES2, *(g.adresse for g in geraete if g.adresse), *konfig.bekannt.adressen}
    macs = {g.opt("mac") for g in geraete if g.opt("mac")} | set(konfig.bekannt.macs)
    namen = {_norm(g.opt("mdns_name")) for g in geraete if g.opt("mdns_name")} | {_norm(n) for n in konfig.bekannt.namen}
    namen.discard("")

    def bekannt(h: Host) -> bool:
        return bool((h.ip and h.ip in adressen) or (h.mac and h.mac in macs) or (h.name and _norm(h.name) in namen))

    rest = [h for h in hosts if not bekannt(h)]
    return sorted(rest, key=lambda h: (int(ipaddress.ip_address(h.ip)) if h.ip else -1, h.name))


@dataclass(frozen=True)
class Ergebnis:
    unbekannte: tuple[Host, ...]
    stand: str  # Zeitpunkt der Messung, ISO 8601 in UTC
    abgeschnitten: bool = False  # die Box meldet mehr Geräte, als gelesen werden (hostliste.MAX_HOSTS)


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


class Erkennung:
    def __init__(self, lesen: Callable[[], Awaitable[Hostliste]], konfig: Konfig, *,
                 uhr: Callable[[], float] = time.monotonic, jetzt: Callable[[], datetime] = _jetzt,
                 frisch: float = FRISCH_SECS, alt: float = ALT_SECS, fehler_pause: float = FEHLER_PAUSE_SECS):
        self._lesen, self._konfig, self._uhr, self._jetzt = lesen, konfig, uhr, jetzt
        self._frisch, self._alt, self._pause = frisch, alt, fehler_pause
        self._ergebnis: Ergebnis | None = None
        self._geholt = 0.0
        self._aufgabe: asyncio.Task | None = None
        self._fehler: tuple[type[Exception], str] | None = None
        self._fehler_um = 0.0

    async def ergebnis(self) -> Ergebnis:
        """Frisch, veraltet (mit Messung im Hintergrund) oder, wenn nichts Brauchbares da ist, nach einer neuen Messung.
        Wirft NichtEingerichtet oder GeraetFehler, wenn es kein Ergebnis gibt."""
        jetzt = self._uhr()
        alter = jetzt - self._geholt
        if self._ergebnis is not None and alter <= self._frisch:
            return self._ergebnis
        pause = self._fehler is not None and jetzt - self._fehler_um < self._pause
        if self._ergebnis is not None and alter <= self._alt:
            if self._aufgabe is None and not pause:
                self._starten()
            return self._ergebnis
        if self._aufgabe is None:
            if pause:
                typ, text = self._fehler  # type: ignore[misc]
                raise typ(text)
            self._starten()
        return await asyncio.shield(self._aufgabe)  # type: ignore[arg-type]

    async def warten(self) -> None:
        """Wartet auf eine laufende Messung (ohne ihren Fehler zu werfen)."""
        aufgabe = self._aufgabe
        if aufgabe is not None:
            await asyncio.wait([aufgabe])

    def _starten(self) -> None:
        self._aufgabe = asyncio.create_task(self._messen())
        self._aufgabe.add_done_callback(self._fertig)

    def _fertig(self, aufgabe: asyncio.Task) -> None:
        if self._aufgabe is aufgabe:
            self._aufgabe = None
        if not aufgabe.cancelled():
            aufgabe.exception()  # abholen, damit niemand "nie abgeholte Ausnahme" meldet

    async def _messen(self) -> Ergebnis:
        try:
            gelesen = await self._lesen()
        except (GeraetFehler, NichtEingerichtet) as e:
            self._fehler, self._fehler_um = (type(e), str(e)), self._uhr()
            raise
        except Exception:
            self._fehler, self._fehler_um = (GeraetFehler, "Interner Fehler beim Lesen der Hostliste"), self._uhr()
            raise
        self._ergebnis = Ergebnis(tuple(vergleichen(list(gelesen.hosts), self._konfig)),
                                  self._jetzt().strftime("%Y-%m-%dT%H:%M:%SZ"), gelesen.abgeschnitten)
        self._geholt, self._fehler = self._uhr(), None
        return self._ergebnis
