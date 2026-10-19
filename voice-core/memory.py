"""Gedächtnis von WAMO: Sitzungen, Gesprächszeilen, Lernspeicher und Profile in Postgres.

Zwei Datenbankrollen mit getrennten Verbindungen (Migration 0001_wamo_init.sql):
  wamo_core   WAMO_DATABASE_URL        Voice-Core und Anmeldung: lesen, einfügen, wenige gezielte
                                       Updates (is_active von memories, Status von actions, Sitzungsende).
                                       Kein delete, keine Profile anlegen, nichts freigeben.
  wamo_admin  WAMO_ADMIN_DATABASE_URL  nur wamo_admin.py: Profile anlegen und pflegen, gemeinsame
                                       Vorschläge freigeben oder ablehnen (AdminDirectory).
Beide über Supavisor im Session-Modus, ohne service_role und ohne PostgREST. Das Schema kommt aus
WAMO_DB_SCHEMA und darf nur wamo_dev, wamo_test oder wamo_prod heißen.

Nutzergrenzen setzt dieser Code selbst (wamo_core sieht alle Zeilen):
  * Ein MemoryStore gehört zu genau einer Sitzung und einer user_id. Jede Abfrage filtert darauf.
  * Gemeinsame Einträge (bereich = gemeinsam) liest ein Store nur mit status = aktiv.
  * Welchen Status ein neuer Eintrag bekommt, entscheidet initial_status() (reine Funktion). Die
    Datenbank prüft zusätzlich (Trigger memories_guard): gemeinsam und aktiv nur mit einem aktiven
    Admin als Autor; sonst Fehler 42501, den remember() als Warnung behandelt.
  * Aktionen folgen dem Statusautomaten des Triggers actions_guard; decided_at setzt die Datenbank.

Ohne WAMO_DATABASE_URL sind alle Gedächtnisfunktionen No-Op. Die Anmeldung ist dann gesperrt
(auth_gate.py), weil ohne Datenbank kein Profil geprüft werden kann.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg
from loguru import logger

SCHEMA_PATTERN = re.compile(r"^wamo_(dev|test|prod)$")
DEFAULT_SCHEMA = "wamo_dev"
CORE_DSN_ENV = "WAMO_DATABASE_URL"
ADMIN_DSN_ENV = "WAMO_ADMIN_DATABASE_URL"
CONNECT_TIMEOUT_SECS = 5.0
COMMAND_TIMEOUT_SECS = 5.0
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5
MAX_PENDING_TURNS = 200  # Gesprächszeilen, die vor dem Anlegen der Sitzung gepuffert werden
MAX_CONTENT_CHARS = 2000  # wie der Check in memories.content
MAX_TURN_CHARS = 20000  # wie der Check in turns.content
MAX_CHANNEL_CHARS = 20  # wie der Check in sessions.channel
EMBED_DIM = 1024

ROLLEN = ("admin", "mitglied")
ANREDEN = ("du", "sie")
BEREICHE = ("persoenlich", "gemeinsam")
STATUS_WERTE = ("aktiv", "vorgeschlagen", "abgelehnt")
QUELLEN = ("auto", "ausdruecklich")
KINDS = ("fact", "preference", "behavior", "lesson", "skill", "episode")
PROMPT_KINDS = ("fact", "preference", "behavior", "lesson")  # landen im System-Prompt
TURN_ROLES = ("user", "assistant", "system", "tool")
RISKS = ("read", "change", "critical", "blocked")
CANVAS_TYPES = ("table", "document", "bpmn", "mermaid", "flow", "pipeline", "whiteboard", "code", "chart")
ENTSCHEIDUNGEN = {"freigeben": "aktiv", "ablehnen": "abgelehnt"}
# Statusautomat der Tabelle actions (wie Trigger actions_guard): Ziel -> erlaubter Vorgänger
ACTION_TRANSITIONS = {
    "approved": "pending", "rejected": "pending", "blocked": "pending",
    "running": "approved", "done": "running", "failed": "running",
}
# Konstante SQL-Liste der Prompt-Arten (feste Literale aus dem Code, kein Cast auf der Spalte)
_PROMPT_KINDS_SQL = ", ".join(f"'{k}'" for k in PROMPT_KINDS)


class DatabaseUnavailable(RuntimeError):
    """Datenbank nicht konfiguriert oder nicht erreichbar."""


class ProfileError(ValueError):
    """Profil kann nicht angelegt oder geändert werden (Meldung ist für Menschen gedacht)."""


# Fehler, die das Gespräch nie aufhalten dürfen: Verbindung, Zeitlimit, SQL, ungültige Werte.
DB_ERRORS: tuple[type[BaseException], ...] = (
    DatabaseUnavailable,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
    asyncio.TimeoutError,
    ValueError,
)


def validate_schema(name: object) -> str:
    """Nur wamo_dev, wamo_test oder wamo_prod. Der Name landet in SQL, daher streng."""
    if not isinstance(name, str) or not SCHEMA_PATTERN.fullmatch(name):
        raise ValueError(f"WAMO_DB_SCHEMA muss wamo_dev, wamo_test oder wamo_prod sein, nicht {name!r}")
    return name


def normalize_uuid(value: object) -> str | None:
    """Kanonische UUID als Text oder None, wenn der Wert keine UUID ist."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def initial_status(bereich: str, quelle: str, rolle: str) -> str:
    """Status eines neuen Gedächtniseintrags (Architektur-Entscheidung 3).

    persoenlich                      immer aktiv
    gemeinsam, automatisch gelernt   immer vorgeschlagen
    gemeinsam, ausdrücklich gemerkt  aktiv bei admin, sonst vorgeschlagen (auch bei unbekannter Rolle)
    """
    if bereich == "persoenlich":
        return "aktiv"
    if bereich != "gemeinsam":
        raise ValueError(f"unbekannter bereich {bereich!r}")
    if quelle == "auto":
        return "vorgeschlagen"
    if quelle != "ausdruecklich":
        raise ValueError(f"unbekannte quelle {quelle!r}")
    return "aktiv" if rolle == "admin" else "vorgeschlagen"


def _affected(status: str) -> int:
    """'UPDATE 1' -> 1. asyncpg liefert bei execute() den Befehlsstatus als Text."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError, IndexError):
        return 0


def _vector_literal(embedding: list[float] | None) -> str | None:
    if embedding is None:
        return None
    if len(embedding) != EMBED_DIM:
        raise ValueError(f"Embedding braucht {EMBED_DIM} Werte, nicht {len(embedding)}")
    return "[" + ",".join(f"{float(v):.7g}" for v in embedding) + "]"


class Database:
    """Kleiner asyncpg-Pool für ein Schema. Der Pool hängt an der Event-Loop, die ihn öffnet;
    läuft Code in einer neuen Loop (z. B. in Tests), wird ein neuer Pool geöffnet."""

    def __init__(self, dsn: str | None, schema: str = DEFAULT_SCHEMA):
        self._dsn = dsn or None
        self.schema = validate_schema(schema)
        self._pool: asyncpg.Pool | None = None
        self._pool_loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_env(cls, dsn_env: str = CORE_DSN_ENV) -> Database:
        return cls(os.getenv(dsn_env), os.getenv("WAMO_DB_SCHEMA") or DEFAULT_SCHEMA)

    @property
    def configured(self) -> bool:
        return self._dsn is not None

    def t(self, name: str) -> str:
        """Voll qualifizierter Name (Tabelle oder Typ). name kommt nur aus Konstanten im Code."""
        return f"{self.schema}.{name}"

    def _pool_ready(self, loop: asyncio.AbstractEventLoop) -> bool:
        return self._pool is not None and self._pool_loop is loop and not self._pool.is_closing()

    async def pool(self) -> asyncpg.Pool:
        if not self.configured:
            raise DatabaseUnavailable("Datenbankverbindung ist nicht gesetzt")
        loop = asyncio.get_running_loop()
        if self._pool_ready(loop):
            return self._pool
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        async with self._lock:
            if self._pool_ready(loop):
                return self._pool
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=POOL_MIN_SIZE,
                    max_size=POOL_MAX_SIZE,
                    statement_cache_size=0,  # Supavisor/PgBouncer: keine benannten Statements
                    timeout=CONNECT_TIMEOUT_SECS,
                    command_timeout=COMMAND_TIMEOUT_SECS,
                    max_inactive_connection_lifetime=120,
                )
            except (OSError, asyncio.TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
                # Bewusst nur der Fehlertyp: die Meldung könnte Teile der Verbindungsdaten enthalten.
                raise DatabaseUnavailable(f"Datenbank nicht erreichbar ({type(e).__name__})") from e
            self._pool_loop = loop
            return self._pool

    async def fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        return await (await self.pool()).fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> asyncpg.Record | None:
        return await (await self.pool()).fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await (await self.pool()).fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        return await (await self.pool()).execute(sql, *args)

    async def executemany(self, sql: str, args: list[tuple]) -> None:
        await (await self.pool()).executemany(sql, args)

    async def close(self) -> None:
        if self._pool is not None and not self._pool.is_closing():
            await self._pool.close()
        self._pool = None


_shared: dict[tuple[str | None, str], Database] = {}


def _database_for(dsn_env: str) -> Database:
    dsn = os.getenv(dsn_env) or None
    schema = validate_schema(os.getenv("WAMO_DB_SCHEMA") or DEFAULT_SCHEMA)
    key = (dsn, schema)
    if key not in _shared:
        _shared[key] = Database(dsn, schema)
    return _shared[key]


def shared_database() -> Database:
    """Verbindung als wamo_core (WAMO_DATABASE_URL) für Anmeldung und alle Sitzungen.
    Wirft ValueError, wenn WAMO_DB_SCHEMA ungültig ist."""
    return _database_for(CORE_DSN_ENV)


def admin_database() -> Database:
    """Verbindung als wamo_admin (WAMO_ADMIN_DATABASE_URL), nur für wamo_admin.py."""
    return _database_for(ADMIN_DSN_ENV)


@dataclass(frozen=True)
class Recall:
    """Was ein Profil im System-Prompt sieht. vorgeschlagen nur fürs Lernen (Doppelungen)."""

    persoenlich: list[str] = field(default_factory=list)
    gemeinsam: list[str] = field(default_factory=list)
    vorgeschlagen: list[str] = field(default_factory=list)


@dataclass
class _PendingTurn:
    role: str
    content: str
    interrupted: bool
    speaker_label: str | None
    created_at: datetime  # Zeitpunkt von log_turn, auch wenn die Zeile erst später gespeichert wird


class MemoryStore:
    """Gedächtnis einer Sitzung, fest an eine user_id gebunden. Fehler werden protokolliert,
    nie geworfen: das Gedächtnis darf das Gespräch nicht aufhalten."""

    def __init__(self, user_id: str | None, db: Database | None):
        self.user_id = normalize_uuid(user_id) if user_id else None
        self._db = db
        self.session_id: str | None = None
        self._pending: list[_PendingTurn] = []
        self._session_state = "neu"  # neu | startet | offen | fehlgeschlagen | beendet
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls, user_id: str | None) -> MemoryStore:
        try:
            db = shared_database()
        except ValueError as e:
            logger.error("Gedächtnis aus: {}", e)
            db = None
        return cls(user_id, db)

    @property
    def enabled(self) -> bool:
        return bool(self._db is not None and self._db.configured and self.user_id)

    @property
    def db(self) -> Database:
        """Die Datenbank; nur nach einer Prüfung von enabled aufrufen."""
        if self._db is None:
            raise DatabaseUnavailable("Gedächtnis ohne Datenbank")
        return self._db

    def _turn_sql(self) -> str:
        db = self.db
        return (
            f"insert into {db.t('turns')} (session_id, user_id, role, content, interrupted, speaker_label, created_at) "
            f"values ($1::uuid, $2::uuid, $3::text::{db.t('turn_role')}, $4, $5, $6, $7)"
        )

    def _turn_args(self, session_id: str, turn: _PendingTurn) -> tuple:
        return (session_id, self.user_id, turn.role, turn.content, turn.interrupted, turn.speaker_label,
                turn.created_at)

    # ---------- Sitzung und Gesprächszeilen ----------
    async def start_session(self, channel: str = "web") -> None:
        if not self.enabled or self._session_state != "neu":
            return
        self._session_state = "startet"
        async with self._lock:
            db = self.db
            sid: str | None = None
            try:
                sid = await db.fetchval(
                    f"insert into {db.t('sessions')} (user_id, channel) values ($1::uuid, $2) returning id::text",
                    self.user_id, (str(channel).strip() or "web")[:MAX_CHANNEL_CHARS],
                )
                # Gepufferte Zeilen in Reihenfolge und mit ihrem eigenen Zeitstempel nachtragen, je
                # Durchgang in einem Statement. Neue Zeilen landen bis dahin weiter im Puffer, weil
                # session_id erst danach gesetzt wird (kein await zwischen Prüfung und Setzen).
                while self._pending:
                    batch, self._pending = self._pending, []
                    try:
                        await db.executemany(self._turn_sql(), [self._turn_args(sid, t) for t in batch])
                    except DB_ERRORS as e:
                        logger.warning("Gedächtnis: {} gepufferte Zeilen nicht gespeichert ({})",
                                       len(batch), type(e).__name__)
                self.session_id = sid
                self._session_state = "offen"
            except DB_ERRORS as e:
                logger.warning("Gedächtnis: Sitzung nicht angelegt ({})", type(e).__name__)
            finally:
                if self._session_state != "offen":
                    # Fehler oder Abbruch: nichts mehr puffern; eine angelegte Sitzung merken,
                    # damit end_session sie noch schließen kann.
                    self._pending.clear()
                    self.session_id = sid
                    self._session_state = "offen" if sid else "fehlgeschlagen"

    async def end_session(self) -> None:
        if not self.enabled:
            return
        async with self._lock:  # wartet, falls start_session noch läuft
            if self._session_state != "offen" or not self.session_id:
                self._session_state = "beendet"
                self._pending.clear()
                return
            self._session_state = "beendet"
            db = self.db
            try:
                status = await db.execute(
                    f"update {db.t('sessions')} set ended_at = now() "
                    "where id = $1::uuid and user_id = $2::uuid and ended_at is null",
                    self.session_id, self.user_id,
                )
            except DB_ERRORS as e:
                logger.warning("Gedächtnis: Sitzungsende nicht gespeichert ({})", type(e).__name__)
                return
            if _affected(status) != 1:
                logger.warning("Gedächtnis: Sitzung {} nicht beendet (Status {!r})", self.session_id, status)

    async def log_turn(
        self,
        role: str,
        content: str,
        interrupted: bool = False,
        speaker_label: str | None = None,
    ) -> None:
        if not self.enabled or not content or role not in TURN_ROLES:
            return
        turn = _PendingTurn(role, content[:MAX_TURN_CHARS], bool(interrupted), speaker_label,
                            datetime.now(timezone.utc))
        if self.session_id is None:
            # Sitzung wird gerade angelegt (oder gleich): puffern statt verwerfen.
            if self._session_state in ("neu", "startet") and len(self._pending) < MAX_PENDING_TURNS:
                self._pending.append(turn)
            return
        try:
            await self.db.execute(self._turn_sql(), *self._turn_args(self.session_id, turn))
        except DB_ERRORS as e:
            logger.warning("Gedächtnis: Gesprächszeile nicht gespeichert ({})", type(e).__name__)

    # ---------- Lernspeicher ----------
    async def remember(
        self,
        content: str,
        kind: str,
        importance: int = 3,
        bereich: str = "persoenlich",
        status: str = "aktiv",
        embedding: list[float] | None = None,
    ) -> str | None:
        """Speichert einen Eintrag und liefert seine id (None bei Fehler oder ohne Datenbank)."""
        if not self.enabled:
            return None
        content = (content or "").strip()[:MAX_CONTENT_CHARS]
        if not content or kind not in KINDS or bereich not in BEREICHE or status not in STATUS_WERTE:
            logger.warning("Gedächtnis: ungültiger Eintrag verworfen (kind={}, bereich={}, status={})",
                           kind, bereich, status)
            return None
        if bereich == "persoenlich" and status != "aktiv":
            logger.warning("Gedächtnis: persönliche Einträge sind immer aktiv, Eintrag verworfen")
            return None
        db = self.db
        try:
            importance = max(1, min(5, int(importance)))
            return await db.fetchval(
                f"insert into {db.t('memories')} "
                "(user_id, bereich, status, kind, content, importance, source_session_id, embedding) "
                f"values ($1::uuid, $2::text::{db.t('memory_scope')}, $3::text::{db.t('memory_status')}, "
                f"$4::text::{db.t('memory_kind')}, $5, $6::smallint, $7::uuid, $8::text::extensions.vector) "
                "returning id::text",
                self.user_id, bereich, status, kind, content, importance, self.session_id,
                _vector_literal(embedding),
            )
        except asyncpg.InsufficientPrivilegeError:
            # Trigger memories_guard: gemeinsam und aktiv nur mit aktivem Admin als Autor (z. B. wenn das
            # Profil seit der Anmeldung zurückgestuft wurde). Nichts gespeichert.
            logger.warning("Gedächtnis: gemeinsamer Eintrag abgelehnt, Autor ist kein aktiver Admin")
            return None
        except (*DB_ERRORS, TypeError) as e:
            logger.warning("Gedächtnis: Eintrag nicht gespeichert ({})", type(e).__name__)
            return None

    async def forget(self, memory_id: str) -> bool:
        """Deaktiviert einen eigenen Eintrag (is_active = false). Erlaubt für eigene persönliche
        Einträge und für eigene gemeinsame Vorschläge, solange sie noch nicht entschieden sind.
        Freigegebenes gemeinsames Wissen kann nur ein Admin zurücknehmen."""
        mid = normalize_uuid(memory_id)
        if not self.enabled or mid is None:
            return False
        db = self.db
        try:
            status = await db.execute(
                f"update {db.t('memories')} set is_active = false "
                "where id = $1::uuid and user_id = $2::uuid and is_active "
                "and (bereich = 'persoenlich' or (bereich = 'gemeinsam' and status = 'vorgeschlagen'))",
                mid, self.user_id,
            )
        except DB_ERRORS as e:
            logger.warning("Gedächtnis: Eintrag nicht vergessen ({})", type(e).__name__)
            return False
        return _affected(status) == 1

    async def recall(
        self, limit_personal: int = 40, limit_shared: int = 20, *, include_proposed: bool = False
    ) -> Recall:
        """Eigene persönliche plus aktive gemeinsame Einträge, je nach Wichtigkeit und Aktualität,
        in einer Abfrage (passend zu memories_recall_personal_idx und memories_recall_shared_idx).
        include_proposed liefert zusätzlich die EIGENEN offenen gemeinsamen Vorschläge (nur fürs
        Lernen, gegen Doppelungen); Vorschläge anderer Profile sieht niemand außer dem Admin."""
        if not self.enabled:
            return Recall()
        db = self.db
        m = db.t("memories")
        order = "order by importance desc, updated_at desc"
        kinds = f"kind in ({_PROMPT_KINDS_SQL})"
        try:
            rows = await db.fetch(
                f"(select 'persoenlich' as gruppe, content, importance, updated_at from {m} "
                f" where user_id = $1::uuid and is_active and bereich = 'persoenlich' and {kinds} {order} limit $2) "
                f"union all "
                f"(select 'gemeinsam', content, importance, updated_at from {m} "
                f" where is_active and bereich = 'gemeinsam' and status = 'aktiv' and {kinds} {order} limit $3) "
                f"union all "
                f"(select 'vorgeschlagen', content, importance, updated_at from {m} "
                f" where user_id = $1::uuid and is_active and bereich = 'gemeinsam' and status = 'vorgeschlagen' "
                f" {order} limit $4)",
                self.user_id, max(0, int(limit_personal)), max(0, int(limit_shared)),
                max(0, int(limit_shared)) if include_proposed else 0,
            )
        except DB_ERRORS as e:
            logger.warning("Gedächtnis: Abruf fehlgeschlagen ({})", type(e).__name__)
            return Recall()
        groups: dict[str, list[asyncpg.Record]] = {"persoenlich": [], "gemeinsam": [], "vorgeschlagen": []}
        for r in rows:
            groups[r["gruppe"]].append(r)
        # Reihenfolge je Gruppe ausdrücklich herstellen (union all garantiert sie nicht)
        ordered = {
            g: [r["content"] for r in sorted(rs, key=lambda r: (-r["importance"], -r["updated_at"].timestamp()))]
            for g, rs in groups.items()
        }
        return Recall(persoenlich=ordered["persoenlich"], gemeinsam=ordered["gemeinsam"],
                      vorgeschlagen=ordered["vorgeschlagen"])

    # ---------- PC-Aktionen und Canvas ----------
    async def log_action(self, tool: str, params: dict, reason: str, risk: str) -> str | None:
        """Audit-Eintrag für eine Aktion, die freigegeben werden muss (Tabelle actions, Status pending)."""
        if not self.enabled or risk not in RISKS:
            return None
        db = self.db
        try:
            return await db.fetchval(
                f"insert into {db.t('actions')} (user_id, session_id, tool, params, reason, risk) "
                f"values ($1::uuid, $2::uuid, $3, $4::text::jsonb, $5, $6::text::{db.t('risk_class')}) "
                "returning id::text",
                self.user_id, self.session_id, str(tool)[:200],
                json.dumps(params, ensure_ascii=False, default=str), str(reason)[:1000], risk,
            )
        except DB_ERRORS as e:
            logger.warning("Gedächtnis: Aktion nicht protokolliert ({})", type(e).__name__)
            return None

    async def update_action(self, action_id: str | None, status: str) -> bool:
        """Statuswechsel nach dem Automaten von actions_guard (decided_at setzt die Datenbank).
        Ungültige Übergänge werden gar nicht erst geschickt; True, wenn genau eine Zeile wechselte."""
        previous = ACTION_TRANSITIONS.get(status)
        if not (self.enabled and action_id) or previous is None:
            return False
        db = self.db
        try:
            result = await db.execute(
                f"update {db.t('actions')} set status = $2::text::{db.t('action_status')} "
                f"where id = $1::uuid and user_id = $3::uuid and status = $4::text::{db.t('action_status')}",
                action_id, status, self.user_id, previous,
            )
        except DB_ERRORS as e:
            logger.warning("Gedächtnis: Aktion nicht aktualisiert ({})", type(e).__name__)
            return False
        if _affected(result) != 1:
            logger.warning("Gedächtnis: Aktion {} nicht im Status {} (Ergebnis {!r})", action_id, previous, result)
            return False
        return True

    async def save_canvas(self, type_: str, title: str, data: dict) -> str | None:
        """Canvas-Inhalt ablegen (canvas_items), damit er später wieder aufrufbar ist."""
        if not self.enabled or type_ not in CANVAS_TYPES:
            return None
        db = self.db
        try:
            return await db.fetchval(
                f"insert into {db.t('canvas_items')} (user_id, session_id, type, title, data) "
                f"values ($1::uuid, $2::uuid, $3::text::{db.t('canvas_type')}, $4, $5::text::jsonb) "
                "returning id::text",
                self.user_id, self.session_id, type_, str(title)[:300],
                json.dumps(data, ensure_ascii=False, default=str),
            )
        except DB_ERRORS as e:
            logger.warning("Gedächtnis: Canvas nicht gespeichert ({})", type(e).__name__)
            return None


# ---------- Profile (Anmeldung, wamo_core) ----------
@dataclass(frozen=True)
class Profile:
    user_id: str
    name: str
    rolle: str
    aktiv: bool
    anrede: str
    voice_id: str | None = None


@dataclass(frozen=True)
class SharedProposal:
    id: str
    autor_id: str
    autor_name: str | None
    kind: str
    content: str
    importance: int
    created_at: str


_PROFILE_COLUMNS = "user_id::text as user_id, display_name, rolle::text as rolle, aktiv, anrede, voice_id"


def _profile(row: asyncpg.Record) -> Profile:
    return Profile(
        user_id=row["user_id"], name=row["display_name"], rolle=row["rolle"], aktiv=row["aktiv"],
        anrede=row["anrede"], voice_id=row["voice_id"],
    )


class ProfileDirectory:
    """Profil nachschlagen für die Anmeldung (Rolle wamo_core, nur lesen). Anders als MemoryStore
    wirft diese Klasse Fehler: die Anmeldung muss bei Datenbankproblemen ablehnen (fail-closed)."""

    def __init__(self, db: Database):
        self._db = db

    @classmethod
    def from_env(cls) -> ProfileDirectory:
        return cls(shared_database())

    @property
    def schema(self) -> str:
        return self._db.schema

    @property
    def configured(self) -> bool:
        return self._db.configured

    async def get(self, user_id: str) -> Profile | None:
        """Profil zur user_id oder None. Wirft DatabaseUnavailable, wenn die Datenbank fehlt
        oder nicht antwortet (auch SQL-Fehler, damit die Anmeldung sicher ablehnt)."""
        uid = normalize_uuid(user_id)
        if uid is None:
            return None
        db = self._db
        try:
            row = await db.fetchrow(f"select {_PROFILE_COLUMNS} from {db.t('profiles')} where user_id = $1::uuid", uid)
        except DatabaseUnavailable:
            raise
        except DB_ERRORS as e:
            raise DatabaseUnavailable(f"Profilabfrage fehlgeschlagen ({type(e).__name__})") from e
        return _profile(row) if row else None


# ---------- Verwaltung (wamo_admin, nur wamo_admin.py) ----------
class AdminDirectory:
    """Profile anlegen und auflisten, gemeinsame Vorschläge prüfen. Läuft ausschließlich über die
    Verbindung als wamo_admin (WAMO_ADMIN_DATABASE_URL); wamo_core hat diese Rechte nicht."""

    def __init__(self, db: Database):
        self._db = db

    @classmethod
    def from_env(cls) -> AdminDirectory:
        return cls(admin_database())

    @property
    def schema(self) -> str:
        return self._db.schema

    @property
    def configured(self) -> bool:
        return self._db.configured

    async def list_profiles(self) -> list[Profile]:
        db = self._db
        rows = await db.fetch(f"select {_PROFILE_COLUMNS} from {db.t('profiles')} order by display_name")
        return [_profile(r) for r in rows]

    async def create_profile(self, user_id: str, name: str, rolle: str, anrede: str = "du") -> Profile:
        uid = normalize_uuid(user_id)
        name = " ".join(str(name or "").split())
        if uid is None:
            raise ProfileError("user_id ist keine gültige UUID")
        if not 1 <= len(name) <= 60:
            raise ProfileError("Der Name muss 1 bis 60 Zeichen lang sein")
        if rolle not in ROLLEN:
            raise ProfileError("Rolle muss admin oder mitglied sein")
        if anrede not in ANREDEN:
            raise ProfileError("Anrede muss du oder sie sein")
        db = self._db
        try:
            row = await db.fetchrow(
                f"insert into {db.t('profiles')} (user_id, display_name, rolle, anrede) "
                f"values ($1::uuid, $2, $3::text::{db.t('profile_role')}, $4) returning {_PROFILE_COLUMNS}",
                uid, name, rolle, anrede,
            )
        except asyncpg.ForeignKeyViolationError as e:
            raise ProfileError("Zu dieser user_id gibt es kein Anmeldekonto (auth.users)") from e
        except asyncpg.UniqueViolationError as e:
            raise ProfileError("Für diese user_id gibt es schon ein Profil") from e
        return _profile(row)

    async def list_proposed_shared(self, limit: int = 100) -> list[SharedProposal]:
        db = self._db
        rows = await db.fetch(
            f"select m.id::text as id, m.user_id::text as autor_id, p.display_name as autor_name, "
            "m.kind::text as kind, m.content, m.importance, m.created_at "
            f"from {db.t('memories')} m left join {db.t('profiles')} p on p.user_id = m.user_id "
            "where m.is_active and m.bereich = 'gemeinsam' and m.status = 'vorgeschlagen' "
            "order by m.created_at limit $1",
            max(1, int(limit)),
        )
        return [
            SharedProposal(
                id=r["id"], autor_id=r["autor_id"], autor_name=r["autor_name"], kind=r["kind"],
                content=r["content"], importance=r["importance"], created_at=r["created_at"].isoformat(),
            )
            for r in rows
        ]

    async def review_shared(self, memory_id: str, admin_id: str, entscheidung: str) -> bool:
        """Gemeinsamen Vorschlag freigeben (aktiv) oder ablehnen. True, wenn genau ein offener,
        aktiver Vorschlag geändert wurde. Nur aktive Admins dürfen entscheiden (prüft auch der
        Trigger memories_guard beim Freigeben)."""
        mid, aid = normalize_uuid(memory_id), normalize_uuid(admin_id)
        if mid is None or aid is None:
            raise ProfileError("memory-id und admin-id müssen UUIDs sein")
        if entscheidung not in ENTSCHEIDUNGEN:
            raise ProfileError("Entscheidung muss freigeben oder ablehnen sein")
        db = self._db
        pool = await db.pool()
        async with pool.acquire() as conn, conn.transaction():
            is_admin = await conn.fetchval(
                f"select exists (select 1 from {db.t('profiles')} "
                "where user_id = $1::uuid and aktiv and rolle = 'admin')",
                aid,
            )
            if not is_admin:
                raise PermissionError("Die admin-id gehört zu keinem aktiven Admin-Profil")
            status = await conn.execute(
                f"update {db.t('memories')} set status = $2::text::{db.t('memory_status')}, "
                "reviewed_by = $3::uuid, reviewed_at = now() "
                "where id = $1::uuid and bereich = 'gemeinsam' and status = 'vorgeschlagen' and is_active",
                mid, ENTSCHEIDUNGEN[entscheidung], aid,
            )
        return _affected(status) == 1
