"""Speicher einer Laborsitzung (Testlabor 3.1, Variante B): Gesprächstext ja, Gedächtnis nie.

LaborSpeicher ist ein MemoryStore, der nichts liest und kein Gedächtnis schreibt: recall() ist leer, remember() und forget() haben keinen
Datenbankzugriff, Lernen gibt es in der Sitzung nicht. Geschrieben werden nur sessions, turns, actions (alle Freigabeschritte und der
Einwilligungsvermerk) und canvas_items, alle mit der Profilkennung des Laborprofils, damit die Löschfunktion (labor_loeschen) sie findet.

Geschrieben wird nur, wenn alles zutrifft: WAMO_LABOR_TEXT_SPEICHERN=an (Standard aus, das ist Variante A und kein Text), Art platz,
der Durchlauf ist offen und seine einwilligung_version ist mindestens WAMO_LABOR_EINWILLIGUNG_MIN. Das Schaufenster hat keinen Platz und
keine Einwilligung und speichert nie. Beim Sitzungsstart steht zuerst die Sitzung, dann der Vermerk "labor_einwilligung" (Version und
Durchlaufkennung, ohne Text) in actions, erst danach Text.

Zweite Sicherung: Die Datenbank des Speichers ist in LaborDatenbank gehüllt. Die Hülle lässt nur insert und update auf die vier Tabellen
zu und weist jede andere Anweisung ab (auch select, mehrere Anweisungen, andere Tabellen), selbst wenn jemand eine geerbte Methode von
MemoryStore umgeht. Einen Pool gibt die Hülle nicht heraus.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from loguru import logger

import labor
from labor_durchlauf import Durchlauf
from memory import (
    DB_ERRORS,
    MAX_PENDING_TURNS,
    MAX_TURN_CHARS,
    TURN_ROLES,
    Database,
    MemoryStore,
    Recall,
    _PendingTurn,
    shared_database,
)
from schluessel import schwaerzen

VERMERK_WERKZEUG = "labor_einwilligung"
_TABELLEN = frozenset({"sessions", "turns", "actions", "canvas_items"})
_TYPEN = frozenset({"turn_role", "risk_class", "action_status", "canvas_type"})  # Enum-Typen der vier Tabellen (Casts im SQL)
_START = re.compile(r"\s*(?:insert\s+into|update)\s+wamo_(?:dev|test|prod)\.([a-z_]+)\b", re.IGNORECASE)
_QUALIFIZIERT = re.compile(r"\bwamo_(?:dev|test|prod)\.([a-z_]+)\b", re.IGNORECASE)
_VERBOTEN = re.compile(r"\b(?:select|from|using|join|delete|truncate|drop|alter|create|copy|grant|with|merge|call|do)\b|;", re.IGNORECASE)


class LaborSqlGesperrt(ValueError):
    """Eine Anweisung außerhalb der vier erlaubten Tabellen oder Anweisungsarten. (ValueError: MemoryStore protokolliert und schluckt sie.)"""


class LaborDatenbank:
    """Hülle um die Datenbank des Kerns: nur insert und update auf sessions, turns, actions und canvas_items."""

    def __init__(self, innen: Database):
        self._innen = innen
        self.verletzungen = 0

    @property
    def configured(self) -> bool:
        return self._innen.configured

    @property
    def schema(self) -> str:
        return self._innen.schema

    def t(self, name: str) -> str:
        return self._innen.t(name)

    def _pruefen(self, sql: str) -> None:
        treffer = _START.match(sql) if isinstance(sql, str) else None
        if treffer is None or treffer.group(1).lower() not in _TABELLEN or _VERBOTEN.search(sql) or any(
                n.lower() not in _TABELLEN | _TYPEN for n in _QUALIFIZIERT.findall(sql)):
            self.verletzungen += 1
            logger.error("Labor: Datenbankanweisung außerhalb der Laborliste abgewiesen")
            raise LaborSqlGesperrt("Anweisung nicht erlaubt")

    async def fetch(self, sql: str, *args: Any):
        self._pruefen(sql)
        return await self._innen.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any):
        self._pruefen(sql)
        return await self._innen.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any):
        self._pruefen(sql)
        return await self._innen.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        self._pruefen(sql)
        return await self._innen.execute(sql, *args)

    async def executemany(self, sql: str, args: list[tuple]) -> None:
        self._pruefen(sql)
        await self._innen.executemany(sql, args)

    async def close(self) -> None:
        await self._innen.close()


class LaborSpeicher(MemoryStore):
    """MemoryStore für Laborsitzungen. Bau über LaborSpeicher.fuer(identity, stand); from_env gibt es nicht."""

    def __init__(self, user_id: str | None, db: LaborDatenbank | None, *, schreiben: bool, stand: Durchlauf | None = None):
        super().__init__(user_id, db)
        self._schreiben = bool(schreiben and stand is not None and stand.einwilligung_version is not None)
        self.stand = stand
        self._vermerkt = False
        self._text_frei = False  # wird wahr, sobald der Einwilligungsvermerk geschrieben ist
        self._wartend: list[_PendingTurn] = []
        self._gestoppt = False  # der Durchlauf ist zu Ende: kein Text, keine Aktion und kein Canvas mehr (stoppen)

    @classmethod
    def from_env(cls, user_id: str | None) -> MemoryStore:
        raise labor.LaborPolitikFehler("LaborSpeicher nur über LaborSpeicher.fuer(identity, stand)")

    @classmethod
    def fuer(cls, identity: object, stand: Durchlauf | None, *, db: Any = None, min_version: int | None = None,
             text_speichern: bool | None = None) -> LaborSpeicher:
        """db: die Datenbank (Tests); ohne Angabe die des Kerns (wamo_core). min_version und text_speichern lesen sonst die Umgebung."""
        art = labor.art_normalisieren(getattr(identity, "labor_art", None))
        if art is None:
            raise labor.LaborPolitikFehler("LaborSpeicher ohne Laborprofil")
        if db is None:
            try:
                db = shared_database()
            except ValueError as e:
                logger.error("Gedächtnis aus: {}", e)
        huelle = None if db is None else db if isinstance(db, LaborDatenbank) else LaborDatenbank(db)
        minimum = min_version if min_version is not None else labor.einwilligung_min()
        text = labor.text_speichern_an() if text_speichern is None else text_speichern
        gueltig = bool(art == "platz" and text and stand is not None and stand.offen and minimum is not None
                       and stand.einwilligung_version is not None and stand.einwilligung_version >= minimum)
        return cls(getattr(identity, "user_id", None), huelle, schreiben=gueltig, stand=stand)

    @property
    def enabled(self) -> bool:
        return self._schreiben and super().enabled

    @property
    def gestoppt(self) -> bool:
        return self._gestoppt

    def stoppen(self) -> None:
        """Der Durchlauf ist zu Ende, die Einwilligung gilt nicht mehr: ab jetzt schreibt der Speicher weder Gesprächstext noch neue Aktionen noch Canvas,
        sofort und ohne auf das Ende der Pipeline zu warten (schlägt deren Abbruch still fehl, liefe sie sonst weiter). Wartende Zeilen verfallen.
        Nicht gesperrt sind update_action, aktion_abschliessen und end_session: sie schließen Protokollzeilen ab und enthalten keinen Gesprächstext."""
        self._gestoppt = True
        self._wartend.clear()

    # ---------- Gedächtnis: gibt es nie ----------
    async def recall(self, limit_personal: int = 40, limit_shared: int = 20, *, include_proposed: bool = False) -> Recall:
        return Recall()

    async def remember(self, content: str, kind: str, importance: int = 3, bereich: str = "persoenlich",
                       status: str = "aktiv", embedding: list[float] | None = None) -> str | None:
        return None

    async def forget(self, memory_id: str) -> bool:
        return False

    async def tageskosten(self, user_id: str | None = None, laufend_usd: float = 0.0) -> float | None:
        return None

    async def veraltete_schliessen(self, user_id: str | None = None, nach_stunden: int = 2, kosten_usd: float = 0.0) -> int | None:
        return None

    # ---------- Sitzung, Vermerk und Text ----------
    async def start_session(self, channel: str = "web") -> None:
        """Legt die Sitzung an und schreibt als Nächstes den Einwilligungsvermerk. Erst danach darf Text in die Datenbank: Zeilen, die vorher
        eintreffen, warten im Arbeitsspeicher. Gelingt der Vermerk nicht, wird nie Text geschrieben (fail-closed)."""
        await super().start_session(channel)
        if not self.session_id or self._gestoppt:
            self._wartend.clear()
            return
        if not self._vermerkt:
            self._vermerkt = True
            self._text_frei = await self._vermerk()
        wartend, self._wartend = self._wartend, []
        if not (self._text_frei and wartend):
            return
        try:
            await self.db.executemany(self._turn_sql(), [self._turn_args(self.session_id, t) for t in wartend])
        except DB_ERRORS as e:
            logger.warning("Gedächtnis: {} gepufferte Zeilen nicht gespeichert ({})", len(wartend), type(e).__name__)

    async def _vermerk(self) -> bool:
        """Einwilligungsvermerk in actions: Version und Durchlaufkennung, ohne Text. Steht vor jedem Gesprächstext, den der Speicher schreibt."""
        stand = self.stand
        aktion = await MemoryStore.log_action(
            self, VERMERK_WERKZEUG, {"version": stand.einwilligung_version, "durchlauf_id": stand.durchlauf_id}, "", "read")
        if aktion is None:
            logger.warning("Labor: Einwilligungsvermerk nicht geschrieben, es wird kein Text gespeichert")
            return False
        for status in ("approved", "running"):
            await self.update_action(aktion, status)
        await self.aktion_abschliessen(aktion, "done", {"ok": True})
        return True

    async def log_turn(self, role: str, content: str, interrupted: bool = False, speaker_label: str | None = None) -> None:
        if self._gestoppt or not self.enabled or not content or role not in TURN_ROLES or self._session_state in ("fehlgeschlagen", "beendet"):
            return
        if self._text_frei:
            await super().log_turn(role, content, interrupted, speaker_label)
        elif len(self._wartend) < MAX_PENDING_TURNS:  # noch kein Vermerk: nur im Arbeitsspeicher halten
            self._wartend.append(_PendingTurn(role, schwaerzen(content[:MAX_TURN_CHARS]), bool(interrupted), speaker_label,
                                              datetime.now(timezone.utc)))

    async def log_action(self, tool: str, params: dict, reason: str, risk: str) -> str | None:
        return await super().log_action(tool, params, reason, risk) if self._text_frei and not self._gestoppt else None

    async def save_canvas(self, type_: str, title: str, data: dict) -> str | None:
        return await super().save_canvas(type_, title, data) if self._text_frei and not self._gestoppt else None
