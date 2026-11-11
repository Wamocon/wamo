"""Integrationstest gegen die echte Datenbank, ausschließlich im Schema wamo_test.

Die Tests legen nichts dauerhaft an und brauchen kein delete-Recht:
  * Schreibversuche des Voice-Cores mit einer erfundenen user_id scheitern am Fremdschlüssel auf
    auth.users oder am Trigger memories_guard, bevor eine Zeile entsteht.
  * Proben der Rechtegrenzen laufen in einer Transaktion, die immer zurückgerollt wird.
Belegt wird die Rechtetrennung der Migration 0001: wamo_core darf keine Profile anlegen, nichts
löschen und nichts freigeben; wamo_admin darf Profile verwalten, aber keine Gesprächsdaten schreiben.

Übersprungen, wenn WAMO_DATABASE_URL (bzw. für die Admin-Tests WAMO_ADMIN_DATABASE_URL) fehlt.
Ausführen:  uv run python -m pytest -q test_integration_db.py   (im Ordner voice-core/)
"""

import json
import os
import ssl
import unittest
import uuid
from pathlib import Path
from unittest import mock

import asyncpg
import certifi
from dotenv import dotenv_values

import memory as m
import wamo_tls

HERE = Path(__file__).resolve().parent
SCHEMA = "wamo_test"


def _env(name: str) -> str | None:
    """Wert aus der Umgebung oder der .env lesen, ohne os.environ für andere Tests zu verändern."""
    if os.getenv(name):
        return os.environ[name]
    for env_file in (HERE / ".env", HERE.parent / ".env"):
        if env_file.is_file():
            value = dotenv_values(env_file).get(name)
            if value:
                return value
    return None


CORE_DSN = _env("WAMO_DATABASE_URL")
ADMIN_DSN = _env("WAMO_ADMIN_DATABASE_URL")
CA_FILE = _env(wamo_tls.CA_ENV)


def _test_env() -> dict[str, str]:
    """Schema wamo_test, dazu die WAMO-CA für DSNs mit sslmode=verify-full."""
    env = {"WAMO_DB_SCHEMA": SCHEMA}
    if CA_FILE:
        env[wamo_tls.CA_ENV] = CA_FILE
    return env


class _SchemaTest(unittest.IsolatedAsyncioTestCase):
    dsn: str | None = None

    async def asyncSetUp(self):
        self._env = mock.patch.dict(os.environ, _test_env())
        self._env.start()
        self.db = m.Database(self.dsn, os.environ["WAMO_DB_SCHEMA"])
        self.assertEqual(self.db.schema, SCHEMA)  # niemals ein anderes Schema
        self.user = str(uuid.uuid4())

    async def asyncTearDown(self):
        await self.db.close()
        self._env.stop()

    async def expect_denied(self, sql: str, *args) -> None:
        """Führt sql in einer Transaktion aus, die immer zurückgerollt wird; erwartet Fehler 42501."""
        pool = await self.db.pool()
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                with self.assertRaises(asyncpg.InsufficientPrivilegeError, msg=sql):
                    await conn.execute(sql, *args)
            finally:
                await tr.rollback()


@unittest.skipUnless(CORE_DSN, "WAMO_DATABASE_URL fehlt")
class CoreRoleTests(_SchemaTest):
    dsn = CORE_DSN

    async def test_connection_role_and_schema(self):
        self.assertEqual(await self.db.fetchval("select current_user"), "wamo_core")
        tables = await self.db.fetch("select table_name from information_schema.tables where table_schema = $1", SCHEMA)
        self.assertTrue({"profiles", "memories", "sessions", "turns", "actions"} <= {r["table_name"] for r in tables})
        self.assertEqual(await self.db.fetchval(
            f"select version from {self.db.t('schema_migrations')} where version = '0001_wamo_init'"), "0001_wamo_init")

    async def test_read_functions(self):
        self.assertIsNone(await m.ProfileDirectory(self.db).get(self.user))
        recall = await m.MemoryStore(self.user, self.db).recall(include_proposed=True)
        self.assertEqual(recall.persoenlich, [])
        self.assertIsInstance(recall.gemeinsam, list)

    async def test_writes_with_unknown_user_leave_nothing(self):
        store = m.MemoryStore(self.user, self.db)
        self.assertIsNone(await store.remember("Testeintrag", "fact"))  # Fremdschlüssel
        self.assertIsNone(await store.remember("Firmenwissen", "fact", bereich="gemeinsam", status="aktiv"))  # Trigger
        await store.log_turn("user", "vor der Sitzung")
        await store.start_session(channel="test")
        self.assertIsNone(store.session_id)
        await store.log_turn("user", "Hallo")
        await store.end_session()
        self.assertFalse(await store.forget(str(uuid.uuid4())))
        self.assertFalse(await store.update_action(str(uuid.uuid4()), "approved"))
        for table in ("memories", "sessions", "turns"):
            with self.subTest(table=table):
                count = await self.db.fetchval(
                    f"select count(*) from {self.db.t(table)} where user_id = $1::uuid", self.user)
                self.assertEqual(count, 0)

    async def test_guard_trigger_rejects_shared_active_from_non_admin(self):
        pool = await self.db.pool()
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                with self.assertRaises(asyncpg.InsufficientPrivilegeError):
                    await conn.execute(
                        f"insert into {self.db.t('memories')} (user_id, bereich, status, kind, content) "
                        "values ($1::uuid, 'gemeinsam', 'aktiv', 'fact', 'Firmenwissen')", self.user)
            finally:
                await tr.rollback()

    async def test_core_cannot_create_profiles(self):
        await self.expect_denied(
            f"insert into {self.db.t('profiles')} (user_id, display_name) values ($1::uuid, 'Test')", self.user)

    async def test_core_cannot_change_profiles_or_review(self):
        await self.expect_denied(f"update {self.db.t('profiles')} set rolle = 'admin' where false")
        await self.expect_denied(f"update {self.db.t('memories')} set status = 'aktiv' where false")
        await self.expect_denied(f"update {self.db.t('memories')} set content = 'x' where false")
        await self.expect_denied(f"update {self.db.t('actions')} set tool = 'x' where false")

    async def test_core_cannot_delete(self):
        for table in ("profiles", "memories", "sessions", "turns", "actions", "canvas_items", "feedback", "devices"):
            with self.subTest(table=table):
                await self.expect_denied(f"delete from {self.db.t(table)} where false")


class _TxDb:
    """Dieselbe Schnittstelle wie memory.Database, aber alles über eine Verbindung in einer offenen Transaktion: der Test rollt sie
    am Ende zurück und hinterlässt nichts."""

    configured = True

    def __init__(self, conn, schema: str):
        self._conn, self.schema = conn, schema

    def t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    async def fetch(self, sql, *args):
        return await self._conn.fetch(sql, *args)

    async def fetchrow(self, sql, *args):
        return await self._conn.fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        return await self._conn.fetchval(sql, *args)

    async def execute(self, sql, *args):
        return await self._conn.execute(sql, *args)


@unittest.skipUnless(CORE_DSN, "WAMO_DATABASE_URL fehlt")
class EntwicklungsAuftragAuditTests(_SchemaTest):
    """Audit der Entwicklungsaufträge im Büro über actions (Folge pending, approved, running, done mit result, Tageskosten,
    veraltete Zeilen). Braucht ein vorhandenes Profil im Schema (Fremdschlüssel auf auth.users) und legt nichts dauerhaft an."""

    dsn = CORE_DSN

    async def test_folge_kosten_und_veraltete_zeilen_in_einer_zurueckgerollten_transaktion(self):
        pool = await self.db.pool()
        async with pool.acquire() as conn:
            user = await conn.fetchval(f"select user_id::text from {self.db.t('profiles')} limit 1")
            if user is None:
                self.skipTest("kein Profil im Schema wamo_test")
            tr = conn.transaction()
            await tr.start()
            try:
                store = m.MemoryStore(user, _TxDb(conn, SCHEMA))
                vorher = await store.tageskosten()
                self.assertIsNotNone(vorher)
                aktion = await store.log_action("entwicklungsauftrag", {"job": "probe", "art": "analyse"}, "Probe", "read")
                self.assertIsNotNone(aktion)
                for status in ("approved", "running"):
                    self.assertTrue(await store.update_action(aktion, status), status)
                self.assertFalse(await store.aktion_abschliessen(aktion, "approved", {}))  # nur done oder failed
                ergebnis = {"job": "probe", "art": "analyse", "status": "fertig", "dauer_s": 5, "kosten_usd": 1.25, "runden": 1}
                self.assertTrue(await store.aktion_abschliessen(aktion, "done", ergebnis))
                zeile = await conn.fetchrow(
                    f"select status::text as status, result, finished_at from {self.db.t('actions')} where id = $1::uuid", aktion)
                self.assertEqual(zeile["status"], "done")
                self.assertIsNotNone(zeile["finished_at"])
                self.assertEqual(json.loads(zeile["result"]), ergebnis)
                self.assertFalse(await store.aktion_abschliessen(aktion, "failed", {}))  # schon abgeschlossen
                self.assertAlmostEqual(await store.tageskosten() - vorher, 1.25, places=4)

                # eine seit drei Stunden laufende Zeile wird geschlossen und mit der Auftragsgrenze gezählt
                alt = await conn.fetchval(
                    f"insert into {self.db.t('actions')} (user_id, tool, params, risk, created_at) "
                    "values ($1::uuid, 'entwicklungsauftrag', '{}'::jsonb, 'read', now() - interval '3 hours') returning id::text",
                    user)
                for status in ("approved", "running"):
                    self.assertTrue(await store.update_action(alt, status), status)
                self.assertEqual(await store.veraltete_schliessen(nach_stunden=2, kosten_usd=3.0), 1)
                geschlossen = await conn.fetchrow(
                    f"select status::text as status, result from {self.db.t('actions')} where id = $1::uuid", alt)
                self.assertEqual(geschlossen["status"], "failed")
                self.assertEqual(json.loads(geschlossen["result"])["grund"], "abgelaufen")
                self.assertEqual(await store.veraltete_schliessen(nach_stunden=2, kosten_usd=3.0), 0)  # nichts mehr offen
            finally:
                await tr.rollback()

    async def test_tageskosten_mit_unsinnigen_und_offenen_zeilen_in_einer_zurueckgerollten_transaktion(self):
        """Die kritischen Teile der Abfrage von tageskosten einmal gegen echtes Postgres: negative Kosten, NaN, absurd große Werte,
        eine laufende Zeile ohne result und offene Zeilen ohne result (pending, approved, rejected), die nie mitzählen dürfen.
        Die Auftragsgrenze steht als Ersatzwert (laufend_usd) im Test auf 3 USD."""
        grenze = 3.0
        pool = await self.db.pool()
        async with pool.acquire() as conn:
            user = await conn.fetchval(f"select user_id::text from {self.db.t('profiles')} limit 1")
            if user is None:
                self.skipTest("kein Profil im Schema wamo_test")
            tr = conn.transaction()
            await tr.start()
            try:
                store = m.MemoryStore(user, _TxDb(conn, SCHEMA))
                vorher = await store.tageskosten(laufend_usd=grenze)
                vorher_ohne = await store.tageskosten()
                self.assertIsNotNone(vorher)
                self.assertIsNotNone(vorher_ohne)

                async def neue_zeile(status: str, ergebnis: dict | None = None) -> str:
                    """Eine Zeile entwicklungsauftrag, die nach dem Automaten bis zu diesem Status geführt wird."""
                    aktion = await store.log_action("entwicklungsauftrag", {"job": "probe"}, "Probe", "read")
                    self.assertIsNotNone(aktion)
                    if status == "pending":
                        return aktion
                    if status == "rejected":
                        self.assertTrue(await store.update_action(aktion, "rejected"))
                        return aktion
                    self.assertTrue(await store.update_action(aktion, "approved"))
                    if status == "approved":
                        return aktion
                    self.assertTrue(await store.update_action(aktion, "running"))
                    if status != "running":
                        self.assertTrue(await store.aktion_abschliessen(aktion, status, ergebnis or {}))
                    return aktion

                await neue_zeile("done", {"kosten_usd": -5})  # nie unter null: senkt die Summe nicht, zählt 0
                await neue_zeile("failed", {"kosten_usd": "NaN"})  # NaN ist keine Zahl: zählt mit der Auftragsgrenze
                await neue_zeile("done", {"kosten_usd": 1e9})  # nach oben begrenzt auf m.KOSTEN_JE_ZEILE_MAX
                await neue_zeile("done", {})  # ohne verwertbare Kosten: zählt mit der Auftragsgrenze
                await neue_zeile("running")  # läuft noch, kein result: mindestens die Auftragsgrenze
                for offen in ("pending", "approved", "rejected"):
                    await neue_zeile(offen)  # nie ein Lauf, kein result: zählen nicht
                erwartet = grenze + m.KOSTEN_JE_ZEILE_MAX + grenze + grenze  # NaN, 1e9 (begrenzt), ohne Kosten, laufend; -5 und offene: 0
                self.assertAlmostEqual(await store.tageskosten(laufend_usd=grenze) - vorher, erwartet, places=4)

                # ohne Untergrenze zählen die Zeilen ohne Kosten null, nur die große Zeile bleibt bei der Obergrenze
                self.assertAlmostEqual(await store.tageskosten() - vorher_ohne, m.KOSTEN_JE_ZEILE_MAX, places=4)

                # ein Text, der keine Zahl ist, bricht die Abfrage: tageskosten meldet None (im Büro startet dann nichts)
                class _Zurueck(Exception):
                    pass

                with self.assertRaises(_Zurueck):
                    async with conn.transaction():  # Sicherungspunkt: der Fehler zerstört nicht die äußere Transaktion
                        await neue_zeile("done", {"kosten_usd": "abc"})
                        self.assertIsNone(await store.tageskosten(laufend_usd=grenze))
                        raise _Zurueck
            finally:
                await tr.rollback()


@unittest.skipUnless(ADMIN_DSN, "WAMO_ADMIN_DATABASE_URL fehlt")
class AdminRoleTests(_SchemaTest):
    dsn = ADMIN_DSN

    async def test_connection_role(self):
        self.assertEqual(await self.db.fetchval("select current_user"), "wamo_admin")

    async def test_admin_functions(self):
        admin = m.AdminDirectory(self.db)
        self.assertIsInstance(await admin.list_profiles(), list)
        self.assertIsInstance(await admin.list_proposed_shared(), list)
        with self.assertRaises(m.ProfileError):  # kein Anmeldekonto: Fremdschlüssel, nichts angelegt
            await admin.create_profile(self.user, "Test", "mitglied")
        with self.assertRaises(PermissionError):
            await admin.review_shared(str(uuid.uuid4()), self.user, "freigeben")
        self.assertFalse(any(p.user_id == self.user for p in await admin.list_profiles()))

    async def test_admin_cannot_touch_conversation_data_or_delete(self):
        await self.expect_denied(
            f"insert into {self.db.t('sessions')} (user_id) values ($1::uuid)", self.user)
        await self.expect_denied(f"select count(*) from {self.db.t('turns')}")
        await self.expect_denied(f"delete from {self.db.t('profiles')} where false")
        await self.expect_denied(f"delete from {self.db.t('memories')} where false")
        await self.expect_denied(f"update {self.db.t('memories')} set content = 'x' where false")


@unittest.skipUnless(
    CORE_DSN and CA_FILE and wamo_tls.sslmode(CORE_DSN) == "verify-full",
    "WAMO_DATABASE_URL ohne sslmode=verify-full oder WAMO_CA_FILE fehlt",
)
class PoolerTlsTests(unittest.IsolatedAsyncioTestCase):
    """Supavisor spricht TLS mit einem Zertifikat der WAMO-CA; ein fremder Vertrauensanker scheitert."""

    async def test_verbindung_mit_tls_und_gepruefter_adresse(self):
        with mock.patch.dict(os.environ, {wamo_tls.CA_ENV: CA_FILE}):
            ctx = wamo_tls.postgres_ssl(CORE_DSN)
        self.assertTrue(ctx.check_hostname)
        # Mit einem SSLContext verlangt asyncpg TLS (kein Rückfall auf unverschlüsselt)
        conn = await asyncpg.connect(dsn=CORE_DSN, ssl=ctx, statement_cache_size=0, timeout=10)
        try:
            self.assertEqual(await conn.fetchval("select current_user"), "wamo_core")
        finally:
            await conn.close()

    async def test_pool_des_voice_cores_ist_verschluesselt(self):
        """Der Weg des Voice-Cores (Database.pool) nutzt TLS; geprüft am ssl_object der Verbindung."""
        with mock.patch.dict(os.environ, _test_env()):
            db = m.Database(CORE_DSN, SCHEMA)
            try:
                pool = await db.pool()
                async with pool.acquire() as conn:
                    # asyncpg bietet dafür keine öffentliche Schnittstelle; der Proxy reicht _transport weiter
                    ssl_object = conn._transport.get_extra_info("ssl_object")
                    self.assertIsNotNone(ssl_object)
                    self.assertIn(ssl_object.version(), ("TLSv1.2", "TLSv1.3"))
                    self.assertEqual(await conn.fetchval("select current_user"), "wamo_core")
            finally:
                await db.close()

    async def test_ohne_wamo_ca_wird_das_zertifikat_abgelehnt(self):
        ctx = ssl.create_default_context(cafile=certifi.where())  # nur öffentliche Zertifikate
        with self.assertRaises(ssl.SSLCertVerificationError):
            await asyncpg.connect(dsn=CORE_DSN, ssl=ctx, statement_cache_size=0, timeout=10)


if __name__ == "__main__":
    unittest.main()
