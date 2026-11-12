"""Gemeinsame Typen und Hilfen: Schreibaufruf, Antwort, Ticketform, JSON ohne Maskierung, Zeitformat."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from .regeln import DienstFehler


@dataclass(frozen=True)
class Aufruf:
    """Ein geprüfter Schreibaufruf (V4 bis V7). name: tickets, ticket, status oder kommentare. durchlauf und schluessel in Kleinbuchstaben."""
    name: str
    nummer: int | None
    werte: dict
    fingerabdruck: str
    durchlauf: str
    schluessel: str


@dataclass(frozen=True)
class Antwort:
    """Status und fertiger JSON-Körper einer Antwort. wiederholt: gespeicherte Antwort einer Wiederholung (Kopf Idempotent-Replayed)."""
    status: int
    koerper: bytes
    wiederholt: bool = False


def json_bytes(obj: object) -> bytes:
    """UTF-8 ohne Maskierung als \\uXXXX (Vertrag V2)."""
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def iso_zeit(t: float) -> str:
    """ISO 8601, UTC, mit Z, auf die Sekunde."""
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerabdruck(name: str, nummer: int | None, daten: object) -> str:
    """Kennzeichnet eine Anfrage für die Idempotenz: Aufruf, Ticket und Inhalt (Schlüsselreihenfolge ohne Belang)."""
    return f"{name}|{nummer}|{json.dumps(daten, sort_keys=True, ensure_ascii=False)}"


def ticket_ausgabe(zeile: sqlite3.Row) -> dict:
    """Das Ticket in genau den Feldern des Vertrags."""
    return {"nummer": f"LAB-{zeile['nummer']}", "titel": zeile["titel"], "typ": zeile["typ"], "prioritaet": zeile["prioritaet"],
            "status": zeile["status"], "beschreibung": zeile["beschreibung"], "version": zeile["version"], "im_durchlauf": bool(zeile["im_durchlauf"])}


def fehler_antwort(e: DienstFehler) -> Antwort:
    return Antwort(e.status, json_bytes({"fehler": e.code, "text": e.text}))
