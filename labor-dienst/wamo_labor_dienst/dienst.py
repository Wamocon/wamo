"""Der Dienst als Ganzes: Konfiguration, Speicher der 16 Plätze, Token-Hashes, Protokoll und Aufbewahrung (D10)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from .datenbank import NichtBereinigt, PlatzDB, ZentralDB
from .grenzen import Begrenzer, FehlerZaehler
from .konfig import FRUEHESTES_DATUM, Konfig, KonfigFehler
from .protokoll import MAX_BYTES, Protokoll
from .regeln import PLAETZE, eingabe
from .sicherheit import PLATZ_TOKEN_RE, digest, token_hash

BOARD_RATE, BOARD_SCHUB = 2.0, 5  # je Platz-Token: etwa 2 Abfragen je Sekunde, kurzzeitig 5
BOARD_GLOBAL_RATE, BOARD_GLOBAL_SCHUB = 20.0, 40  # alle Plätze zusammen (8 Plätze mit je 2 je Sekunde, dazu Luft)
FEHLVERSUCHE_GRENZE, FEHLVERSUCHE_FENSTER_SECS = 120, 60.0  # je Quelle und Minute


class Dienst:
    """Hält alles, was die Schnittstellen brauchen (app.state.dienst). uhr: Sekunden seit 1970, in Tests verstellbar."""

    def __init__(self, konfig: Konfig, uhr: Callable[[], float] = time.time, *, protokoll_max_bytes: int = MAX_BYTES):
        self.konfig, self.uhr = konfig, uhr
        self.protokoll = Protokoll(konfig.daten / "protokoll", uhr, protokoll_max_bytes)
        self.zentral = ZentralDB(konfig.daten / "zentral.sqlite3")
        self.plaetze = {n: PlatzDB(n, konfig.daten / "plaetze" / f"platz-{n:02d}.sqlite3", konfig.platz_max_bytes, uhr) for n in PLAETZE}
        self.kern_digest = digest(konfig.kern_token)
        self.betreuer_digest = digest(konfig.betreuer_token)
        self.board_je_token = Begrenzer(BOARD_RATE, BOARD_SCHUB, uhr)
        self.board_global = Begrenzer(BOARD_GLOBAL_RATE, BOARD_GLOBAL_SCHUB, uhr)
        self.fehlversuche = FehlerZaehler(FEHLVERSUCHE_GRENZE, FEHLVERSUCHE_FENSTER_SECS, uhr)
        self._tokens: dict[str, int] = {}  # Hash -> Platz: unbekannte Token werden hier erkannt, ohne Datenbank und ohne Faden
        self._token_sperre = threading.Lock()

    def einrichten(self) -> None:
        """Legt Dateien und Tabellen an und löscht Daten, die älter sind als die Aufbewahrung. Eine Uhr vor dem Stand des Baus ist ein Konfigurationsfehler."""
        if datetime.fromtimestamp(self.uhr(), UTC).date() < FRUEHESTES_DATUM:
            raise KonfigFehler(f"Die Uhr des Rechners geht falsch (vor dem {FRUEHESTES_DATUM.isoformat()}): Die Aufbewahrung würde nie greifen")
        self.zentral.anlegen()
        for platz in self.plaetze.values():
            platz.anlegen()
        with self._token_sperre:
            self._tokens = self.zentral.alle_hashes()
        self.aufbewahrung_pruefen()

    def platz_von_token(self, token: str) -> int | None:
        """Der Platz zu einem Platz-Token oder None. Nur im Speicher: kein Datenbankzugriff, kein Faden aus dem gemeinsamen Pool."""
        if not PLATZ_TOKEN_RE.fullmatch(token):
            return None
        with self._token_sperre:
            return self._tokens.get(token_hash(token))

    def token_vergeben(self, platz: int, hash_: str) -> None:
        """Speichert den Hash des Platz-Tokens (Datei und Index). Ein früherer Token des Platzes verliert den Zugang."""
        self.zentral.token_setzen(platz, hash_)
        with self._token_sperre:
            self._tokens = {h: p for h, p in self._tokens.items() if p != platz} | {hash_: platz}

    def token_entziehen(self, platz: int) -> None:
        self.zentral.token_loeschen(platz)
        with self._token_sperre:
            self._tokens = {h: p for h, p in self._tokens.items() if p != platz}

    def neuer_besucher(self, platz: int, einwilligung_version: int) -> None:
        """Besucherwechsel: Zuerst muss die Einwilligungsversion passen (sie darf nur wachsen, sonst 422 und nichts ändert sich). Dann verliert der alte
        Platz-Token den Zugang (schlägt danach etwas fehl, bleibt der Platz eher zu als offen), dann beginnt ein neuer Durchlauf mit Seed. Den neuen
        Token holt der Betreuer mit platz-token."""
        if not self.zentral.einwilligung_anheben(einwilligung_version):
            raise eingabe("Die Einwilligungsversion darf nicht kleiner sein als die zuletzt erfasste.")
        self.token_entziehen(platz)
        self.plaetze[platz].neuer_durchlauf(einwilligung_version)

    def platz_leeren(self, platz: int) -> None:
        """Alle Daten des Platzes löschen. Auch hier verliert der Platz-Token den Zugang."""
        self.token_entziehen(platz)
        self.plaetze[platz].daten_loeschen()

    def aufbewahrung_pruefen(self) -> bool:
        """Liegt heute (UTC) nach dem Datum der Aufbewahrung (WAMO_LABOR_DIENST_BIS), werden alle Daten gelöscht: Tickets, Kommentare, Einwilligungen,
        Platz-Token und Protokoll. Liefert wahr nur, wenn die Frist abgelaufen ist und alle Dateien bereinigt sind. Scheitert die Bereinigung eines Platzes
        (NichtBereinigt), steht im Protokoll ein Fehler, der Rest wird trotzdem gelöscht, das Ergebnis ist falsch und die stündliche Prüfung wiederholt es."""
        if datetime.fromtimestamp(self.uhr(), UTC).date() <= self.konfig.bis:
            return False
        vollstaendig = True
        for platz in self.plaetze.values():
            try:
                platz.daten_loeschen()
            except NichtBereinigt:
                vollstaendig = False
                self.protokoll.ereignis("fehler", fehlerart="NichtBereinigt", platz=platz.platz)
        self.zentral.tokens_loeschen()
        with self._token_sperre:
            self._tokens = {}
        self.protokoll.leeren()
        return vollstaendig
