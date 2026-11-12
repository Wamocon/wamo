"""Der Dienst als Ganzes: Konfiguration, Speicher der 16 Plätze, Token-Hashes, Protokoll und Aufbewahrung (D10)."""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from .datenbank import PlatzDB, ZentralDB
from .konfig import Konfig
from .protokoll import Protokoll
from .regeln import PLAETZE
from .sicherheit import digest


class Dienst:
    """Hält alles, was die Schnittstellen brauchen (app.state.dienst). uhr: Sekunden seit 1970, in Tests verstellbar."""

    def __init__(self, konfig: Konfig, uhr: Callable[[], float] = time.time):
        self.konfig, self.uhr = konfig, uhr
        self.protokoll = Protokoll(konfig.daten / "protokoll", uhr)
        self.zentral = ZentralDB(konfig.daten / "zentral.sqlite3")
        self.plaetze = {n: PlatzDB(n, konfig.daten / "plaetze" / f"platz-{n:02d}.sqlite3", konfig.platz_max_bytes, uhr) for n in PLAETZE}
        self.kern_digest = digest(konfig.kern_token)
        self.betreuer_digest = digest(konfig.betreuer_token)

    def einrichten(self) -> None:
        """Legt Dateien und Tabellen an und löscht Daten, die älter sind als die Aufbewahrung."""
        self.zentral.anlegen()
        for platz in self.plaetze.values():
            platz.anlegen()
        self.aufbewahrung_pruefen()

    def neuer_besucher(self, platz: int, einwilligung_version: int) -> None:
        """Besucherwechsel: Der alte Platz-Token verliert zuerst den Zugang (schlägt danach etwas fehl, bleibt der Platz eher zu als offen), dann beginnt
        ein neuer Durchlauf mit Seed. Den neuen Token holt der Betreuer mit platz-token."""
        self.zentral.token_loeschen(platz)
        self.plaetze[platz].neuer_durchlauf(einwilligung_version)

    def platz_leeren(self, platz: int) -> None:
        """Alle Daten des Platzes löschen. Auch hier verliert der Platz-Token den Zugang."""
        self.zentral.token_loeschen(platz)
        self.plaetze[platz].daten_loeschen()

    def aufbewahrung_pruefen(self) -> bool:
        """Liegt heute (UTC) nach dem Datum der Aufbewahrung (WAMO_LABOR_DIENST_BIS), werden alle Daten gelöscht: Tickets, Kommentare, Einwilligungen,
        Platz-Token und Protokoll. Liefert wahr, wenn die Frist abgelaufen ist."""
        if datetime.fromtimestamp(self.uhr(), UTC).date() <= self.konfig.bis:
            return False
        for platz in self.plaetze.values():
            platz.daten_loeschen()
        self.zentral.tokens_loeschen()
        self.protokoll.leeren()
        return True
