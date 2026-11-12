"""Schreibpfad V4 bis V7 in einer Transaktion (D13): Wiederholung, Schreibrate, Durchlauf, Ticket, Version und Mengenquoten.

Reihenfolge nach Vertrag 2.2 (die Schritte 1 bis 5 liegen davor in der Schnittstelle):
  6 Wiederholung (gespeicherte Antwort oder 422), 7 Schreibrate (429), 8 Durchlauf (409), 9 Ticket (404), 10 Version (409), 11 Mengen (429).
Die Funktion läuft in einer offenen Transaktion (BEGIN IMMEDIATE) der Aufrufer. Eine Ausnahme rollt alles zurück (alles oder nichts):
auch die Schreibrate, damit ein abgelehnter oder abgebrochener Aufruf nie mitzählt. Alle Abfragen sind parametrisiert (D3).
"""
from __future__ import annotations

import sqlite3

from .modell import Antwort, Aufruf, json_bytes, ticket_ausgabe
from .regeln import (
    IDEM_MAX,
    IDEM_SECS,
    QUOTE_BYTES,
    QUOTE_KOMMENTARE,
    QUOTE_RATE,
    QUOTE_TICKETS,
    RATE_FENSTER_SECS,
    RATE_ZUKUNFT_SECS,
    TICKET_NUMMER_MAX,
    DienstFehler,
    eingabe,
)

_TICKET = "SELECT nummer, titel, typ, prioritaet, status, beschreibung, version, im_durchlauf FROM ticket WHERE nummer = ?"


def _quote(text: str) -> DienstFehler:
    return DienstFehler(429, "quote_erreicht", text)


def _zahl(conn: sqlite3.Connection, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def genutzter_text(conn: sqlite3.Connection) -> int:
    """Bytes (UTF-8) aller Titel, Beschreibungen und Kommentare des Platzes."""
    ticket = _zahl(conn, "SELECT COALESCE(SUM(LENGTH(CAST(titel AS BLOB)) + LENGTH(CAST(beschreibung AS BLOB))), 0) FROM ticket")
    return ticket + _zahl(conn, "SELECT COALESCE(SUM(LENGTH(CAST(text AS BLOB))), 0) FROM kommentar")


def _text_quote(conn: sqlite3.Connection, kosten: int) -> None:
    """Nur Wachstum prüft die Quote: wer Text kürzt, darf immer ändern."""
    if kosten > 0 and genutzter_text(conn) + kosten > QUOTE_BYTES:
        raise _quote("Das Limit für Text ist erreicht.")


def _ticket(conn: sqlite3.Connection, nummer: int) -> dict:
    return ticket_ausgabe(conn.execute(_TICKET, (nummer,)).fetchone())


def _anlegen(conn: sqlite3.Connection, w: dict, meta: sqlite3.Row, iso: str) -> tuple[int, object]:
    if _zahl(conn, "SELECT COUNT(*) FROM ticket") >= QUOTE_TICKETS:
        raise _quote("Das Limit für Tickets ist erreicht.")
    _text_quote(conn, len(w["titel"].encode()) + len(w["beschreibung"].encode()))
    nummer = meta["ticket_zaehler"]
    if nummer > TICKET_NUMMER_MAX:
        raise _quote("Das Limit für Tickets ist erreicht.")
    conn.execute("INSERT INTO ticket(nummer, titel, typ, prioritaet, status, beschreibung, version, im_durchlauf, angelegt, geaendert) "
                 "VALUES (?, ?, ?, ?, 'zu_erledigen', ?, 1, 1, ?, ?)", (nummer, w["titel"], w["typ"], w["prioritaet"], w["beschreibung"], iso, iso))
    conn.execute("UPDATE meta SET ticket_zaehler = ?", (nummer + 1,))
    return 201, _ticket(conn, nummer)


def _aendern(conn: sqlite3.Connection, w: dict, zeile: sqlite3.Row, iso: str) -> tuple[int, object]:
    felder = w["felder"]
    _text_quote(conn, sum(len(v.encode()) - len(zeile[f].encode()) for f, v in felder.items() if f in ("titel", "beschreibung")))
    neu = {f: felder.get(f, zeile[f]) for f in ("titel", "beschreibung", "typ", "prioritaet")}
    conn.execute("UPDATE ticket SET titel = ?, beschreibung = ?, typ = ?, prioritaet = ?, version = version + 1, geaendert = ? WHERE nummer = ?",
                 (neu["titel"], neu["beschreibung"], neu["typ"], neu["prioritaet"], iso, zeile["nummer"]))
    return 200, _ticket(conn, zeile["nummer"])


def _status(conn: sqlite3.Connection, w: dict, zeile: sqlite3.Row, iso: str) -> tuple[int, object]:
    conn.execute("UPDATE ticket SET status = ?, version = version + 1, geaendert = ? WHERE nummer = ?", (w["status"], iso, zeile["nummer"]))
    return 200, _ticket(conn, zeile["nummer"])


def _kommentar(conn: sqlite3.Connection, w: dict, zeile: sqlite3.Row, iso: str) -> tuple[int, object]:
    if _zahl(conn, "SELECT COUNT(*) FROM kommentar") >= QUOTE_KOMMENTARE:
        raise _quote("Das Limit für Kommentare ist erreicht.")
    _text_quote(conn, len(w["text"].encode()))
    kommentar_id = _zahl(conn, "SELECT kommentar_zaehler FROM meta") + 1
    conn.execute("INSERT INTO kommentar(kommentar_id, nummer, text, angelegt) VALUES (?, ?, ?, ?)", (kommentar_id, zeile["nummer"], w["text"], iso))
    conn.execute("UPDATE meta SET kommentar_zaehler = ?", (kommentar_id,))
    conn.execute("UPDATE ticket SET version = version + 1, geaendert = ? WHERE nummer = ?", (iso, zeile["nummer"]))
    return 201, {"kommentar_id": kommentar_id, "ticket": _ticket(conn, zeile["nummer"])}


_AENDERN = {"ticket": _aendern, "status": _status, "kommentare": _kommentar}


def _ausfuehren(conn: sqlite3.Connection, a: Aufruf, meta: sqlite3.Row, iso: str) -> tuple[int, object]:
    if a.durchlauf != meta["durchlauf_id"] or not meta["offen"]:  # 8. Durchlauf
        raise DienstFehler(409, "durchlauf_beendet", "Der Durchlauf ist beendet.")
    if a.name == "tickets":
        return _anlegen(conn, a.werte, meta, iso)
    zeile = conn.execute(_TICKET, (a.nummer,)).fetchone()
    if zeile is None:  # 9. Ticket (der Platz ist durch die eigene Datei des Platzes gegeben, D6)
        raise DienstFehler(404, "ticket_unbekannt", "Dieses Ticket gibt es nicht.")
    if zeile["version"] != a.werte["erwartete_version"]:  # 10. Version
        raise DienstFehler(409, "ticket_veraendert", "Das Ticket hat sich geändert.")
    return _AENDERN[a.name](conn, a.werte, zeile, iso)  # 11. Mengen (in den Funktionen, vor dem ersten Schreiben)


def _rate_pruefen(conn: sqlite3.Connection, jetzt: float) -> None:
    """7. Schreibrate: höchstens 30 Schreibaufrufe in den letzten 60 Sekunden. Der abgelehnte Aufruf zählt nicht mit (die Ausnahme rollt zurück)."""
    # jetzt ist erst nach BEGIN IMMEDIATE gelesen: Es ist nie älter als der Eintrag eines Aufrufs, der vor uns die Sperre hatte. Verworfen werden nur Einträge,
    # die deutlich in der Zukunft liegen (Uhr zurückgestellt), nie die eines Aufrufs, der bloß einen Wimpernschlag später dran war.
    conn.execute("DELETE FROM schreibzeit WHERE t <= ? OR t > ?", (jetzt - RATE_FENSTER_SECS, jetzt + RATE_ZUKUNFT_SECS))
    if _zahl(conn, "SELECT COUNT(*) FROM schreibzeit") >= QUOTE_RATE:
        raise _quote("Zu viele Schreibaufrufe, bitte später.")
    conn.execute("INSERT INTO schreibzeit(t) VALUES (?)", (jetzt,))


def _merken(conn: sqlite3.Connection, a: Aufruf, jetzt: float, status: int, koerper: bytes) -> None:
    conn.execute("INSERT INTO idempotenz(schluessel, zeit, fingerabdruck, status, antwort) VALUES (?, ?, ?, ?, ?)",
                 (a.schluessel, jetzt, a.fingerabdruck, status, koerper))
    conn.execute("DELETE FROM idempotenz WHERE schluessel IN (SELECT schluessel FROM idempotenz ORDER BY zeit DESC LIMIT -1 OFFSET ?)", (IDEM_MAX,))


def ausfuehren(conn: sqlite3.Connection, a: Aufruf, jetzt: float, iso: str) -> Antwort:
    """Führt einen Schreibaufruf in der offenen Transaktion aus. Liefert die Antwort, auch bei 404, 409 und 429 der Mengenquoten.
    DienstFehler (422 bei einem Schlüssel einer anderen Anfrage, 429 der Schreibrate) verlässt die Funktion: Die Aufrufer rollen zurück."""
    conn.execute("DELETE FROM idempotenz WHERE zeit < ?", (jetzt - IDEM_SECS,))
    alt = conn.execute("SELECT fingerabdruck, status, antwort FROM idempotenz WHERE schluessel = ?", (a.schluessel,)).fetchone()
    if alt is not None:  # 6. Wiederholung
        if alt["fingerabdruck"] != a.fingerabdruck:
            raise eingabe("Der Idempotency-Key gehört zu einer anderen Anfrage.")
        return Antwort(alt["status"], bytes(alt["antwort"]), wiederholt=True)
    _rate_pruefen(conn, jetzt)
    try:
        status, obj = _ausfuehren(conn, a, conn.execute("SELECT durchlauf_id, offen, ticket_zaehler FROM meta").fetchone(), iso)
    except DienstFehler as e:
        status, obj = e.status, {"fehler": e.code, "text": e.text}
    koerper = json_bytes(obj)
    if status < 500 and status != 429:  # gespeichert werden Erfolge und 404 und 409
        _merken(conn, a, jetzt, status, koerper)
    return Antwort(status, koerper)
