"""Verwaltung der Laborprofile: Zeilen zählen und den gesamten Text eines Laborprofils löschen (Testlabor, Datenschutz).

Die Löschung liegt in der Datenbank: die Funktionen labor_loeschen und labor_zaehlen aus Migration 0018 (security definer, nur die
Verwaltungsrolle wamo_admin darf sie aufrufen, wamo_core nie). Dieses Modul schickt nur den Aufruf, prüft das Ergebnis gegen die
Löschliste (labor_loeschliste.py) und verlangt nach einer vollständigen Löschung null Zeilen. Es löscht selbst nichts, formuliert kein
SQL mit Werten und gibt nie Text aus den Zeilen zurück, nur Zahlen je Tabelle.

Aufruf über voice-core/wamo_admin.py (Unterbefehle labor-zaehlen und labor-loeschen) oder scripts/wamo_labor.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

import labor_loeschliste as ll
from memory import normalize_uuid


class LaborFehler(Exception):
    """Zählen oder Löschen ist nicht möglich oder unvollständig. Der Text ist für die Person gedacht, ohne Inhalte."""


@dataclass(frozen=True)
class LoeschErgebnis:
    geloescht: dict[str, int]  # Zeilen je Tabelle, die diese Löschung entfernt hat
    rest: dict[str, int] | None  # Zeilen je Tabelle danach; None bei einem Zeitfenster (dort bleibt der Rest berechtigt stehen)

    def zusammenfassung(self) -> str:
        teile = ", ".join(f"{t} {n}" for t, n in self.geloescht.items() if n) or "nichts"
        return f"gelöscht: {teile}"


def zeit_pruefen(text: str | None, name: str) -> datetime | None:
    """ISO 8601 mit Zeitzone oder None. Eine Angabe ohne Zeitzone wäre mehrdeutig und wird abgelehnt."""
    if text is None or str(text).strip() == "":
        return None
    try:
        wert = datetime.fromisoformat(str(text).strip())
    except ValueError as e:
        raise LaborFehler(f"{name}: kein gültiger Zeitpunkt (ISO 8601 mit Zeitzone erwartet)") from e
    if wert.tzinfo is None:
        raise LaborFehler(f"{name}: die Zeitzone fehlt (zum Beispiel +01:00)")
    return wert


def _uuid(profil: object) -> str:
    uid = normalize_uuid(profil)
    if uid is None:
        raise LaborFehler("Das Profil ist keine gültige UUID")
    return uid


def _tabellen(roh: Any) -> dict[str, int]:
    """Die Antwort der Funktion als Zahlen je Tabelle. Sie muss genau die Tabellen der Löschliste nennen, nicht mehr und nicht weniger:
    Weicht die Funktion in der Datenbank von der Liste ab, ist die Löschung nicht belegbar vollständig."""
    try:
        werte = json.loads(roh) if isinstance(roh, (str, bytes)) else dict(roh)
        zahlen = {str(k): int(v) for k, v in werte.items()}
    except (TypeError, ValueError, AttributeError) as e:
        raise LaborFehler("Die Antwort der Datenbank ist unlesbar") from e
    if set(zahlen) != set(ll.tabellen()):
        raise LaborFehler("Die Tabellen der Datenbankfunktion stimmen nicht mit der Löschliste überein "
                          f"(Unterschied: {sorted(set(zahlen) ^ set(ll.tabellen()))}). Migration und Liste abgleichen")
    return zahlen


def _fehler(e: asyncpg.PostgresError) -> LaborFehler:
    text = (getattr(e, "message", None) or str(e)).strip()
    if isinstance(e, asyncpg.InsufficientPrivilegeError):
        return LaborFehler("Kein Laborprofil oder keine Berechtigung (" + text + ")")
    if isinstance(e, (asyncpg.ObjectNotInPrerequisiteStateError, asyncpg.InvalidParameterValueError)):
        return LaborFehler(text)
    return LaborFehler(f"Datenbankfehler ({type(e).__name__})")


async def zaehlen(db, profil: object) -> dict[str, int]:
    """Zeilen je Tabelle der Löschliste für ein Laborprofil (nur lesen)."""
    uid = _uuid(profil)
    try:
        roh = await db.fetchval(f"select {db.t('labor_zaehlen')}($1::uuid)", uid)
    except asyncpg.PostgresError as e:
        raise _fehler(e) from e
    return _tabellen(roh)


async def loeschen(db, profil: object, ab: datetime | None = None, bis: datetime | None = None) -> LoeschErgebnis:
    """Löscht den gesamten Text eines Laborprofils oder nur das Zeitfenster eines Durchlaufs.

    Ohne ab und bis prüft die Verwaltung danach, dass keine Zeile mehr da ist, und schlägt sonst an (T10)."""
    uid = _uuid(profil)
    if ab is not None and bis is not None and ab > bis:
        raise LaborFehler("Das Zeitfenster ist verkehrt: ab liegt nach bis")
    try:
        roh = await db.fetchval(f"select {db.t('labor_loeschen')}($1::uuid, $2::timestamptz, $3::timestamptz)", uid, ab, bis)
    except asyncpg.PostgresError as e:
        raise _fehler(e) from e
    geloescht = _tabellen(roh)
    if ab is not None or bis is not None:
        return LoeschErgebnis(geloescht, None)
    rest = await zaehlen(db, uid)
    uebrig = {t: n for t, n in rest.items() if n}
    if uebrig:
        raise LaborFehler(f"Nach dem Löschen sind noch Zeilen vorhanden: {uebrig}. Die Löschung ist unvollständig")
    return LoeschErgebnis(geloescht, rest)
