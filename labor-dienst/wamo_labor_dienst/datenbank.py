"""Speicher des Dienstes: je Platz eine SQLite-Datei mit hartem max_page_count (D1) und eine kleine zentrale Datei für die Hashes der Platz-Token.

Jede Datei gehört genau einem Platz: Die Platztrennung (D6) ergibt sich aus der Datei, nie aus einer Spalte in der Abfrage. Alle Abfragen sind
parametrisiert (D3). Die Suche ist wörtlich und ohne Beachtung der Groß- und Kleinschreibung (auch bei Umlauten) über eine SQL-Funktion,
nicht über LIKE: Prozent, Unterstrich und Gegenschrägstrich sind gewöhnliche Zeichen. Jeder Aufruf öffnet eine eigene Verbindung (WAL,
Schreiben mit BEGIN IMMEDIATE), daher sind Aufrufe aus mehreren Threads und sogar Prozessen unteilbar.
"""
from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

from . import schreiben as schreibpfad
from .modell import Antwort, Aufruf, fehler_antwort, iso_zeit, json_bytes, ticket_ausgabe
from .regeln import KOMMENTARE_V3, LISTE_MAX, PRIORITAETEN, STATUS, TYPEN, ZAEHLER_START, DienstFehler, Suche
from .seed import SEED

SEITE_BYTES = 4096
BUSY_SECS = 10.0
VERDICHTEN_VERSUCHE = 20  # so oft wird das Zurückschreiben der WAL-Datei versucht (ein offener Lesesnapshot kann es blockieren)
VERDICHTEN_PAUSE_SECS = 0.1
BOARD_CACHE_SECS = 1.0  # so lange darf die Antwort der Board-Schnittstelle höchstens alt sein (jede Änderung macht sie vorher ungültig)
ZENTRAL_MAX_BYTES = 1024 * 1024


def _liste(werte: tuple[str, ...]) -> str:
    return ",".join(f"'{w}'" for w in werte)  # nur die festen Wertebereiche des Vertrags (regeln.py), nie eine Eingabe


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  durchlauf_id TEXT NOT NULL,
  offen INTEGER NOT NULL CHECK (offen IN (0, 1)),
  einwilligung_version INTEGER,
  einwilligung_zeit TEXT,
  ticket_zaehler INTEGER NOT NULL,
  kommentar_zaehler INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket (
  nummer INTEGER PRIMARY KEY CHECK (nummer BETWEEN 1 AND 9999),
  titel TEXT NOT NULL,
  typ TEXT NOT NULL CHECK (typ IN ({_liste(TYPEN)})),
  prioritaet TEXT NOT NULL CHECK (prioritaet IN ({_liste(PRIORITAETEN)})),
  status TEXT NOT NULL CHECK (status IN ({_liste(STATUS)})),
  beschreibung TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  im_durchlauf INTEGER NOT NULL CHECK (im_durchlauf IN (0, 1)),
  angelegt TEXT NOT NULL,
  geaendert TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kommentar (
  kommentar_id INTEGER PRIMARY KEY,
  nummer INTEGER NOT NULL REFERENCES ticket(nummer),
  text TEXT NOT NULL,
  angelegt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS kommentar_nummer ON kommentar(nummer, kommentar_id);
CREATE TABLE IF NOT EXISTS idempotenz (
  schluessel TEXT PRIMARY KEY,
  zeit REAL NOT NULL,
  fingerabdruck TEXT NOT NULL,
  status INTEGER NOT NULL,
  antwort BLOB NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idempotenz_zeit ON idempotenz(zeit);
CREATE TABLE IF NOT EXISTS schreibzeit (t REAL NOT NULL);
CREATE INDEX IF NOT EXISTS schreibzeit_t ON schreibzeit(t);
"""

_TICKET_SPALTEN = "nummer, titel, typ, prioritaet, status, beschreibung, version, im_durchlauf"
_FILTER = {"typ": "typ = ?", "prioritaet": "prioritaet = ?", "status": "status = ?"}


def _enthaelt(text: str, gesucht: str) -> int:
    return int(gesucht.casefold() in text.casefold())


def verbinden(pfad: Path, max_bytes: int) -> sqlite3.Connection:
    """Neue Verbindung: Autocommit (Transaktionen steuern die Aufrufer), Zeilen mit Namen, harte Obergrenze der Dateigröße."""
    conn = sqlite3.connect(pfad, timeout=BUSY_SECS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")  # gelöschter Text wird überschrieben (D10)
    conn.execute("PRAGMA temp_store = MEMORY")  # keine Temp-Dateien (read_only-Dateisystem)
    conn.execute("PRAGMA synchronous = NORMAL")
    seiten = max(max_bytes // conn.execute("PRAGMA page_size").fetchone()[0], 1)
    conn.execute(f"PRAGMA max_page_count = {int(seiten)}")  # PRAGMA nimmt keine Parameter: eine Zahl aus der Konfiguration (D1)
    conn.create_function("wamo_enthaelt", 2, _enthaelt, deterministic=True)
    return conn


class NichtBereinigt(RuntimeError):
    """Die Daten sind gelöscht, aber die Datei ist noch nicht bereinigt (WAL-Datei nicht zurückgeschrieben): Es darf kein Erfolg gemeldet werden."""


def _ist_voll(e: sqlite3.OperationalError) -> bool:
    return getattr(e, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL or "full" in str(e).lower()


class _Basis:
    """Gemeinsame Handgriffe: eine Datei, Verbindungen für Lesen und Schreiben."""

    def __init__(self, pfad: Path, max_bytes: int):
        self.pfad, self._max = Path(pfad), max_bytes

    @contextlib.contextmanager
    def _lesen(self) -> Iterator[sqlite3.Connection]:
        """Ein Schnappschuss: alle Abfragen im Block sehen denselben Stand."""
        conn = verbinden(self.pfad, self._max)
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def _transaktion(self) -> Iterator[sqlite3.Connection]:
        """Schreiben: alles oder nichts. BEGIN IMMEDIATE nimmt die Schreibsperre vor der ersten Prüfung (D13)."""
        conn = verbinden(self.pfad, self._max)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


class PlatzDB(_Basis):
    """Der Speicher eines Platzes (Tickets, Kommentare, Durchlauf, Idempotenz, Schreibrate)."""

    def __init__(self, platz: int, pfad: Path, max_bytes: int, uhr: Callable[[], float] = time.time):
        super().__init__(pfad, max_bytes)
        self.platz, self._uhr = platz, uhr
        self._stand = 0  # zählt jede Änderung des Platzes hoch: Der Zwischenspeicher des Boards gilt nur für denselben Stand
        self._cache_sperre = threading.Lock()
        self._board_cache: tuple[int, float, bytes] | None = None

    # ---------- Einrichten ----------
    def anlegen(self) -> None:
        """Legt Datei und Tabellen an, falls es sie noch nicht gibt. Ein neuer Platz hat den Seed, ist geschlossen und ohne Einwilligung."""
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.pfad, timeout=BUSY_SECS, isolation_level=None)
        try:
            conn.execute(f"PRAGMA page_size = {SEITE_BYTES}")
            conn.execute("PRAGMA journal_mode = WAL")
        finally:
            conn.close()
        with self._transaktion() as conn:
            for anweisung in SCHEMA.split(";"):
                if anweisung.strip():
                    conn.execute(anweisung)
            if conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0:
                conn.execute("INSERT INTO meta(id, durchlauf_id, offen, einwilligung_version, einwilligung_zeit, ticket_zaehler, kommentar_zaehler) "
                             "VALUES (1, ?, 0, NULL, NULL, ?, 0)", (str(uuid.uuid4()), ZAEHLER_START))
                self._seed(conn)

    def _jetzt(self) -> str:
        return iso_zeit(self._uhr())

    def _seed(self, conn: sqlite3.Connection) -> None:
        jetzt = self._jetzt()
        conn.executemany("INSERT INTO ticket(nummer, typ, prioritaet, status, titel, beschreibung, version, im_durchlauf, angelegt, geaendert) "
                         "VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?)", [(*s, jetzt, jetzt) for s in SEED])

    @staticmethod
    def _leeren(conn: sqlite3.Connection) -> None:
        for anweisung in ("DELETE FROM kommentar", "DELETE FROM ticket", "DELETE FROM idempotenz", "DELETE FROM schreibzeit"):
            conn.execute(anweisung)

    # ---------- Lesen ----------
    def durchlauf(self) -> dict:
        """V1: der Durchlauf des Platzes."""
        with self._lesen() as conn:
            m = conn.execute("SELECT durchlauf_id, offen, einwilligung_version, einwilligung_zeit FROM meta").fetchone()
        return {"durchlauf_id": m["durchlauf_id"], "offen": bool(m["offen"]), "einwilligung_version": m["einwilligung_version"],
                "einwilligung_zeit": m["einwilligung_zeit"]}

    def suchen(self, suche: Suche) -> dict:
        """V2: {"tickets": [...], "gesamt": n}, nach Nummer absteigend. Die Liste wird bei über 60 KB gekürzt (den Anfang behalten)."""
        bedingungen, parameter = ["1 = 1"], []  # nur feste Bausteine, die Werte gehen als Parameter
        if suche.nummer is not None:
            bedingungen.append("nummer = ?")
            parameter.append(suche.nummer)
        for feld, baustein in _FILTER.items():
            if getattr(suche, feld) is not None:
                bedingungen.append(baustein)
                parameter.append(getattr(suche, feld))
        if suche.nur_durchlauf:
            bedingungen.append("im_durchlauf = 1")
        if suche.q:
            bedingungen.append("wamo_enthaelt(titel || char(10) || beschreibung, ?)")
            parameter.append(suche.q)
        where = " AND ".join(bedingungen)
        with self._lesen() as conn:
            gesamt = conn.execute(f"SELECT COUNT(*) FROM ticket WHERE {where}", parameter).fetchone()[0]  # noqa: S608 (feste Bausteine)
            zeilen = conn.execute(f"SELECT {_TICKET_SPALTEN} FROM ticket WHERE {where} ORDER BY nummer DESC LIMIT ?", [*parameter, suche.limit]).fetchall()  # noqa: S608
        tickets = [ticket_ausgabe(z) for z in zeilen]
        while tickets and len(json_bytes({"tickets": tickets, "gesamt": gesamt})) > LISTE_MAX:
            tickets.pop()
        return {"tickets": tickets, "gesamt": gesamt}

    def ticket(self, nummer: int) -> dict | None:
        """V3: das Ticket mit den letzten 20 Kommentaren (ältester zuerst) oder None."""
        with self._lesen() as conn:
            zeile = conn.execute(f"SELECT {_TICKET_SPALTEN} FROM ticket WHERE nummer = ?", (nummer,)).fetchone()  # noqa: S608
            if zeile is None:
                return None
            kommentare = conn.execute("SELECT kommentar_id, text FROM (SELECT kommentar_id, text FROM kommentar WHERE nummer = ? "
                                      "ORDER BY kommentar_id DESC LIMIT ?) ORDER BY kommentar_id", (nummer, KOMMENTARE_V3)).fetchall()
        return {**ticket_ausgabe(zeile), "kommentare": [{"kommentar_id": k["kommentar_id"], "text": k["text"]} for k in kommentare]}

    def board(self) -> dict:
        """Das ganze Board des Platzes für die Board-Seite (nur lesend): Tickets mit Kommentaren und Zeitpunkten, dazu der Zustand."""
        with self._lesen() as conn:
            m = conn.execute("SELECT offen, einwilligung_version FROM meta").fetchone()
            tickets = conn.execute(f"SELECT {_TICKET_SPALTEN}, angelegt, geaendert FROM ticket ORDER BY nummer").fetchall()  # noqa: S608
            kommentare = conn.execute("SELECT kommentar_id, nummer, text, angelegt FROM kommentar ORDER BY kommentar_id").fetchall()
        je_ticket: dict[int, list[dict]] = {}
        for k in kommentare:
            je_ticket.setdefault(k["nummer"], []).append({"kommentar_id": k["kommentar_id"], "text": k["text"], "angelegt": k["angelegt"]})
        return {"platz": self.platz, "durchlauf": {"offen": bool(m["offen"]), "einwilligung_version": m["einwilligung_version"]},
                "tickets": [{**ticket_ausgabe(t), "angelegt": t["angelegt"], "geaendert": t["geaendert"], "kommentare": je_ticket.get(t["nummer"], [])}
                            for t in tickets]}

    def _geaendert(self) -> None:
        with self._cache_sperre:
            self._stand += 1

    def board_bytes(self) -> bytes:
        """Das Board als fertiges JSON, höchstens BOARD_CACHE_SECS alt und nie älter als die letzte Änderung dieses Platzes."""
        jetzt = self._uhr()
        with self._cache_sperre:
            stand, cache = self._stand, self._board_cache
            if cache is not None and cache[0] == stand and 0 <= jetzt - cache[1] < BOARD_CACHE_SECS:
                return cache[2]
        daten = json_bytes(self.board())  # mit dem Stand von vorher abgelegt: Ändert sich der Platz währenddessen, gilt der Eintrag nicht
        with self._cache_sperre:
            self._board_cache = (stand, jetzt, daten)
        return daten

    def zustand(self) -> dict:
        """Für die Betreuer: Zustand ohne Texte."""
        with self._lesen() as conn:
            m = conn.execute("SELECT durchlauf_id, offen, einwilligung_version, einwilligung_zeit FROM meta").fetchone()
            tickets = conn.execute("SELECT COUNT(*) FROM ticket").fetchone()[0]
            kommentare = conn.execute("SELECT COUNT(*) FROM kommentar").fetchone()[0]
        return {"platz": self.platz, "durchlauf_id": m["durchlauf_id"], "offen": bool(m["offen"]), "einwilligung_version": m["einwilligung_version"],
                "einwilligung_zeit": m["einwilligung_zeit"], "tickets": tickets, "kommentare": kommentare}

    # ---------- Schreiben (V4 bis V7) ----------
    def schreiben(self, aufruf: Aufruf) -> Antwort:
        """Ein Schreibaufruf in einer Transaktion. Jede Antwort des Vertrags kommt als Antwort zurück (auch 409, 422 und 429).
        Nur ein unerwarteter Fehler wird zur Ausnahme (nach dem Rückrollen ist der Platz unverändert)."""
        jetzt = self._uhr()
        try:
            with self._transaktion() as conn:
                return schreibpfad.ausfuehren(conn, aufruf, jetzt, iso_zeit(jetzt))
        except DienstFehler as e:
            return fehler_antwort(e)
        except sqlite3.OperationalError as e:
            if _ist_voll(e):  # max_page_count erreicht: nie ein Absturz, sondern 429
                return fehler_antwort(DienstFehler(429, "quote_erreicht", "Das Limit für diesen Platz ist erreicht."))
            raise
        finally:
            self._geaendert()

    # ---------- Betreuer ----------
    def durchlauf_beenden(self) -> None:
        with self._transaktion() as conn:
            conn.execute("UPDATE meta SET offen = 0")
        self._geaendert()

    def neuer_durchlauf(self, einwilligung_version: int) -> None:
        """Neuer Teilnehmer, Einwilligung liegt vor: neue durchlauf_id, Seed, Zähler ab 30, frische Schlüssel und Schreibrate."""
        with self._transaktion() as conn:
            self._leeren(conn)
            self._seed(conn)
            conn.execute("UPDATE meta SET durchlauf_id = ?, offen = 1, einwilligung_version = ?, einwilligung_zeit = ?, ticket_zaehler = ?, kommentar_zaehler = 0",
                         (str(uuid.uuid4()), einwilligung_version, self._jetzt(), ZAEHLER_START))
        self._geaendert()

    def zuruecksetzen(self) -> None:
        """Board und Ablage zurück auf den Seed. Durchlauf und Einwilligung bleiben, die Schreibrate auch (sie hängt nicht am Board)."""
        with self._transaktion() as conn:
            rate = conn.execute("SELECT t FROM schreibzeit").fetchall()
            self._leeren(conn)
            self._seed(conn)
            conn.executemany("INSERT INTO schreibzeit(t) VALUES (?)", [(r["t"],) for r in rate])
            conn.execute("UPDATE meta SET ticket_zaehler = ?, kommentar_zaehler = 0", (ZAEHLER_START,))
        self._geaendert()

    def daten_loeschen(self) -> None:
        """Alle Daten des Platzes löschen (D9, D10): Tickets, Kommentare, Einwilligung, Schlüssel. Der Platz ist danach geschlossen, mit neuer
        durchlauf_id und ohne Seed, bis ein neuer Durchlauf beginnt. Gelöschter Text wird überschrieben, die Datei verkleinert."""
        with self._transaktion() as conn:
            self._leeren(conn)
            conn.execute("UPDATE meta SET durchlauf_id = ?, offen = 0, einwilligung_version = NULL, einwilligung_zeit = NULL, ticket_zaehler = ?, kommentar_zaehler = 0",
                         (str(uuid.uuid4()), ZAEHLER_START))
        self._geaendert()
        self._verdichten()

    def _verdichten(self) -> None:
        """Verkleinert die Datei (VACUUM) und schreibt die WAL-Datei vollständig zurück (wal_checkpoint TRUNCATE). Das Ergebnis wird ausgewertet:
        busy, ein nicht vollständig zurückgeschriebener Stand oder eine nicht leere WAL-Datei heißen, dass der alte Text noch in einer Datei stehen
        kann. Nach VERDICHTEN_VERSUCHEN Versuchen gilt das Löschen als nicht gelungen (NichtBereinigt), nie als Erfolg."""
        conn = verbinden(self.pfad, self._max)
        wal = self.pfad.with_name(self.pfad.name + "-wal")
        try:
            conn.execute("PRAGMA busy_timeout = 100")
            verdichtet = False
            for versuch in range(VERDICHTEN_VERSUCHE):
                try:
                    if not verdichtet:
                        conn.execute("VACUUM")
                        verdichtet = True
                    busy, protokolliert, zurueckgeschrieben = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if not busy and zurueckgeschrieben >= protokolliert and (not wal.exists() or wal.stat().st_size == 0):
                        return
                except sqlite3.OperationalError:
                    pass  # gesperrt: wiederholen
                time.sleep(VERDICHTEN_PAUSE_SECS)
            raise NichtBereinigt(self.platz)
        finally:
            conn.close()


class ZentralDB(_Basis):
    """Die Hashes der Platz-Token (D8): nur der Hash, nie der Token. Ein Token gehört genau einem Platz."""

    def __init__(self, pfad: Path):
        super().__init__(pfad, ZENTRAL_MAX_BYTES)

    def anlegen(self) -> None:
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.pfad, timeout=BUSY_SECS, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        finally:
            conn.close()
        with self._transaktion() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS platz_token (platz INTEGER PRIMARY KEY CHECK (platz BETWEEN 1 AND 16), hash TEXT NOT NULL UNIQUE)")
            conn.execute("CREATE TABLE IF NOT EXISTS einwilligung_max (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)")

    def token_setzen(self, platz: int, token_hash: str) -> None:
        """Erzeugt oder erneuert den Hash des Platzes. Ein Hash eines anderen Platzes ergibt sqlite3.IntegrityError."""
        with self._transaktion() as conn:
            conn.execute("INSERT INTO platz_token(platz, hash) VALUES (?, ?) ON CONFLICT(platz) DO UPDATE SET hash = excluded.hash", (platz, token_hash))

    def platz_von_hash(self, token_hash: str) -> int | None:
        with self._lesen() as conn:
            zeile = conn.execute("SELECT platz FROM platz_token WHERE hash = ?", (token_hash,)).fetchone()
        return zeile["platz"] if zeile else None

    def alle_hashes(self) -> dict[str, int]:
        """Alle Hashes mit ihrem Platz (für den Index im Speicher des Prozesses)."""
        with self._lesen() as conn:
            return {z["hash"]: z["platz"] for z in conn.execute("SELECT platz, hash FROM platz_token").fetchall()}

    def hat_token(self, platz: int) -> bool:
        with self._lesen() as conn:
            return conn.execute("SELECT 1 FROM platz_token WHERE platz = ?", (platz,)).fetchone() is not None

    def einwilligung_anheben(self, version: int) -> bool:
        """Die Versionsnummer des Datenschutzhinweises darf nur wachsen (Vertrag Abschnitt 8). Prüft und merkt in einer Transaktion: wahr, wenn version
        mindestens so groß ist wie die höchste bisher erfasste (dann ist sie die neue höchste), falsch, wenn sie kleiner ist (nichts ändert sich)."""
        with self._transaktion() as conn:
            zeile = conn.execute("SELECT version FROM einwilligung_max").fetchone()
            if zeile is not None and version < zeile["version"]:
                return False
            conn.execute("INSERT INTO einwilligung_max(id, version) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET version = excluded.version", (version,))
            return True

    def token_loeschen(self, platz: int) -> None:
        """Entfernt den Hash des Platzes: Der alte Token öffnet nichts mehr. Ohne Hash ist das Löschen kein Fehler."""
        with self._transaktion() as conn:
            conn.execute("DELETE FROM platz_token WHERE platz = ?", (platz,))

    def tokens_loeschen(self) -> None:
        with self._transaktion() as conn:
            conn.execute("DELETE FROM platz_token")

    def pruefen(self) -> bool:
        """Gesundheit: Die Datei lässt sich lesen."""
        try:
            with self._lesen() as conn:
                conn.execute("SELECT COUNT(*) FROM platz_token").fetchone()
        except sqlite3.Error:
            return False
        return True
