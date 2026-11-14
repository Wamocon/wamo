"""Stimmabdruck in der Datenbank: speakers (Migration 0001), nur das eigene Profil, nur über die Rolle wamo_core (asyncpg wie memory.py).

Eine Zeile je Profil (is_owner), darin der Abdruck (extensions.vector(192)), Zeitpunkt und Version der Einwilligung. Die Tabelle erzwingt
selbst, dass ein Abdruck nur mit Einwilligung und nur für den Profilinhaber gespeichert wird (Constraints speakers_voiceprint_consent_chk und
speakers_voiceprint_owner_chk). Löschen entfernt die ganze Zeile (DSGVO Art. 17). Es gibt keinen Zwischenspeicher über Profile hinweg: jeder
Aufruf fragt mit der user_id des Speichers, den die Sitzung hält.

Alle Funktionen werfen nie. Ein Fehler der Datenbank heißt: laden gibt (False, None), speichern und löschen geben False.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
from loguru import logger

from memory import DB_ERRORS, MemoryStore
from stimme_einwilligung import einwilligung_gilt
from stimme_modell import als_vektor, aus_vektor

MAX_NAME = 60  # speakers.name: 1 bis 60 Zeichen


def _bereit(speicher: MemoryStore) -> bool:
    return bool(speicher.enabled)


async def abdruck_laden(speicher: MemoryStore) -> tuple[bool, np.ndarray | None]:
    """(ok, Abdruck). ok False: nicht lesbar (Datenbank, Format), dann ist unbekannt, ob es einen Abdruck gibt. (True, None): sicher keiner."""
    if not _bereit(speicher):
        return False, None
    db = speicher.db
    try:
        text = await db.fetchval(
            f"select voiceprint::text from {db.t('speakers')} where user_id = $1::uuid and is_owner and voiceprint is not null limit 1",
            speicher.user_id,
        )
    except DB_ERRORS as e:
        logger.warning("Stimmabdruck: nicht gelesen ({})", type(e).__name__)
        return False, None
    if text is None:
        return True, None
    abdruck = aus_vektor(text)
    if abdruck is None:
        logger.warning("Stimmabdruck in der Datenbank hat das falsche Format")
        return False, None
    return True, abdruck


async def abdruck_speichern(
    speicher: MemoryStore, abdruck: np.ndarray, *, version: str | None, name: str, einwilligung_am: datetime | None
) -> bool:
    """Legt den Abdruck des Profils an oder ersetzt ihn, mit Zeitpunkt und Version der Einwilligung. Ohne beides geschieht nichts."""
    if not _bereit(speicher) or not einwilligung_gilt(version) or einwilligung_am is None:
        return False
    db = speicher.db
    anzeige = (name or "").strip()[:MAX_NAME] or "Profil"
    try:
        antwort = await db.execute(
            f"insert into {db.t('speakers')} (user_id, name, is_owner, voiceprint, consent_at, consent_text_version, enrolled_at) "
            "values ($1::uuid, $2, true, $3::text::extensions.vector, $5::timestamptz, $4, now()) "
            "on conflict (user_id) where is_owner do update set name = excluded.name, voiceprint = excluded.voiceprint, "
            "consent_at = excluded.consent_at, consent_text_version = excluded.consent_text_version, enrolled_at = excluded.enrolled_at",
            speicher.user_id, anzeige, als_vektor(abdruck), version, einwilligung_am,
        )
    except DB_ERRORS as e:
        logger.warning("Stimmabdruck: nicht gespeichert ({})", type(e).__name__)
        return False
    return str(antwort).startswith("INSERT")


async def abdruck_loeschen(speicher: MemoryStore) -> bool:
    """Entfernt die Zeile des Profils vollständig (Abdruck, Einwilligung, Name). Nichts zu löschen zählt als Erfolg."""
    if not _bereit(speicher):
        return False
    db = speicher.db
    try:
        await db.execute(f"delete from {db.t('speakers')} where user_id = $1::uuid and is_owner", speicher.user_id)
    except DB_ERRORS as e:
        logger.warning("Stimmabdruck: nicht gelöscht ({})", type(e).__name__)
        return False
    logger.info("Stimmabdruck gelöscht")
    return True
