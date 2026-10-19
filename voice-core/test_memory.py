"""Tests für memory.py ohne Datenbank: Statusregel, Schema-Prüfung, No-Op und Turn-Puffer.

Ausführen:  uv run python -m pytest -q test_memory.py   (im Ordner voice-core/)
"""

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import asyncpg

import memory as m

USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
SESSION = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"


class StatusRuleTests(unittest.TestCase):
    def test_table(self):
        cases = [
            ("persoenlich", "auto", "admin", "aktiv"),
            ("persoenlich", "auto", "mitglied", "aktiv"),
            ("persoenlich", "ausdruecklich", "admin", "aktiv"),
            ("persoenlich", "ausdruecklich", "mitglied", "aktiv"),
            ("gemeinsam", "auto", "admin", "vorgeschlagen"),
            ("gemeinsam", "auto", "mitglied", "vorgeschlagen"),
            ("gemeinsam", "ausdruecklich", "admin", "aktiv"),
            ("gemeinsam", "ausdruecklich", "mitglied", "vorgeschlagen"),
            ("gemeinsam", "ausdruecklich", "unbekannt", "vorgeschlagen"),
        ]
        for bereich, quelle, rolle, erwartet in cases:
            with self.subTest(bereich=bereich, quelle=quelle, rolle=rolle):
                self.assertEqual(m.initial_status(bereich, quelle, rolle), erwartet)

    def test_unknown_values_raise(self):
        with self.assertRaises(ValueError):
            m.initial_status("privat", "auto", "admin")
        with self.assertRaises(ValueError):
            m.initial_status("gemeinsam", "geraten", "admin")


class SchemaTests(unittest.TestCase):
    def test_allowed(self):
        for s in ("wamo_dev", "wamo_test", "wamo_prod"):
            self.assertEqual(m.validate_schema(s), s)

    def test_rejected(self):
        for s in ("public", "wamo_dev; drop table x", "WAMO_DEV", "wamo_", "voice_dev", "", None, "auth"):
            with self.subTest(s=s), self.assertRaises(ValueError):
                m.validate_schema(s)

    def test_database_rejects_bad_schema(self):
        with mock.patch.dict(os.environ, {"WAMO_DB_SCHEMA": "public"}), self.assertRaises(ValueError):
            m.shared_database()

    def test_uuid(self):
        self.assertEqual(m.normalize_uuid(USER.upper()), USER)
        self.assertIsNone(m.normalize_uuid("x"))
        self.assertIsNone(m.normalize_uuid(None))


class NoDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_everything_is_noop(self):
        store = m.MemoryStore(USER, m.Database(None, "wamo_test"))
        self.assertFalse(store.enabled)
        await store.start_session()
        await store.log_turn("user", "hallo")
        await store.end_session()
        self.assertIsNone(await store.remember("x", "fact"))
        self.assertEqual(await store.recall(), m.Recall())
        self.assertIsNone(await store.log_action("t", {}, "r", "critical"))
        self.assertIsNone(await store.save_canvas("table", "t", {}))

    async def test_directory_raises_without_database(self):
        with self.assertRaises(m.DatabaseUnavailable):
            await m.ProfileDirectory(m.Database(None, "wamo_test")).get(USER)

    async def test_invalid_user_id_no_query(self):
        self.assertIsNone(await m.ProfileDirectory(m.Database(None, "wamo_test")).get("keine-uuid"))


class FakeDb:
    configured = True
    schema = "wamo_test"

    def __init__(self):
        self.calls = []
        self.session_gate = asyncio.Event()
        self.update_status = "UPDATE 1"

    def t(self, name):
        return f"wamo_test.{name}"

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "sessions" in sql:
            await self.session_gate.wait()
            return SESSION
        return "neue-id"

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return self.update_status if sql.startswith("update") else "INSERT 0 1"

    async def executemany(self, sql, rows):
        self.batches = getattr(self, "batches", 0) + 1
        for args in rows:
            self.calls.append(("executemany", sql, args))

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return getattr(self, "rows", [])

    def turns(self):
        return [c[2] for c in self.calls if "turns" in c[1]]


class BufferTests(unittest.IsolatedAsyncioTestCase):
    async def test_turns_before_session_are_buffered_in_order(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)
        start = asyncio.create_task(store.start_session())
        await asyncio.sleep(0)
        await store.log_turn("assistant", "Hallo Erika")
        await store.log_turn("user", "Hi")
        self.assertEqual(db.turns(), [])  # noch keine Sitzung
        db.session_gate.set()
        await start
        await store.log_turn("assistant", "Wie kann ich helfen?")
        turns = db.turns()
        self.assertEqual([t[3] for t in turns], ["Hallo Erika", "Hi", "Wie kann ich helfen?"])
        self.assertTrue(all(t[0] == SESSION and t[1] == USER for t in turns))

    async def test_buffered_turns_keep_timestamp_and_flush_in_one_statement(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)
        start = asyncio.create_task(store.start_session())
        await asyncio.sleep(0)
        await store.log_turn("assistant", "eins")
        await store.log_turn("user", "zwei")
        await asyncio.sleep(0.05)
        before_flush = datetime.now(timezone.utc)
        db.session_gate.set()
        await start
        self.assertEqual(db.batches, 1)  # ein executemany für den ganzen Puffer
        stamps = [t[6] for t in db.turns()]
        self.assertTrue(all(s < before_flush for s in stamps))  # Zeit von log_turn, nicht vom Nachtragen
        self.assertLessEqual(stamps[0], stamps[1])

    async def test_turns_before_start_call_are_buffered(self):
        db = FakeDb()
        db.session_gate.set()
        store = m.MemoryStore(USER, db)
        await store.log_turn("assistant", "früh")
        await store.start_session()
        self.assertEqual([t[3] for t in db.turns()], ["früh"])

    async def test_end_session_waits_and_checks_status(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)
        start = asyncio.create_task(store.start_session())
        await asyncio.sleep(0)
        end = asyncio.create_task(store.end_session())
        await asyncio.sleep(0)
        db.session_gate.set()
        await start
        await end
        updates = [c for c in db.calls if c[1].startswith("update")]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][2], (SESSION, USER))

    async def test_end_session_logs_unexpected_status(self):
        db = FakeDb()
        db.session_gate.set()
        db.update_status = "UPDATE 0"
        store = m.MemoryStore(USER, db)
        await store.start_session()
        with mock.patch.object(m.logger, "warning") as warn:
            await store.end_session()
        warn.assert_called_once()

    async def test_forget_only_own_and_open(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)
        self.assertFalse(await store.forget("keine-uuid"))
        db.update_status = "UPDATE 1"
        self.assertTrue(await store.forget(SESSION))
        sql, args = db.calls[-1][1], db.calls[-1][2]
        self.assertIn("user_id = $2::uuid", sql)
        self.assertIn("status = 'vorgeschlagen'", sql)
        self.assertEqual(args, (SESSION, USER))
        db.update_status = "UPDATE 0"  # fremder, freigegebener oder schon vergessener Eintrag
        self.assertFalse(await store.forget(SESSION))

    async def test_cancelled_start_keeps_session_closable(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)
        start = asyncio.create_task(store.start_session())
        await asyncio.sleep(0)
        start.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await start
        await store.log_turn("user", "danach")  # darf weder hängen noch puffern
        await store.end_session()

    async def test_remember_rejects_personal_proposal(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)
        self.assertIsNone(await store.remember("x", "fact", bereich="persoenlich", status="vorgeschlagen"))
        self.assertIsNone(await store.remember("x", "unbekannt"))
        self.assertEqual(await store.remember("Projekt Alpha nutzt Supabase.", "fact", bereich="gemeinsam",
                                              status="vorgeschlagen"), "neue-id")

    async def test_remember_handles_guard_trigger_42501(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)

        async def refuse(sql, *args):
            raise asyncpg.InsufficientPrivilegeError("gemeinsamer Eintrag wird nur durch einen aktiven Admin wirksam")

        db.fetchval = refuse
        with mock.patch.object(m.logger, "warning") as warn:
            self.assertIsNone(await store.remember("Firmenwissen", "fact", bereich="gemeinsam", status="aktiv"))
        warn.assert_called_once()

    async def test_update_action_follows_state_machine(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)
        for status in ("pending", "unbekannt", ""):
            with self.subTest(status=status):
                self.assertFalse(await store.update_action(SESSION, status))
        self.assertEqual(db.calls, [])  # ungültige Übergänge gehen gar nicht erst raus
        expected = {"approved": "pending", "rejected": "pending", "blocked": "pending",
                    "running": "approved", "done": "running", "failed": "running"}
        for status, previous in expected.items():
            with self.subTest(status=status):
                self.assertTrue(await store.update_action(SESSION, status))
                sql, args = db.calls[-1][1], db.calls[-1][2]
                self.assertNotIn("decided_at", sql)  # setzt der Trigger actions_guard
                self.assertEqual(args, (SESSION, status, USER, previous))
        db.update_status = "UPDATE 0"
        self.assertFalse(await store.update_action(SESSION, "approved"))

    async def test_recall_one_query_sorted_per_group(self):
        db = FakeDb()
        store = m.MemoryStore(USER, db)
        t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
        db.rows = [
            {"gruppe": "gemeinsam", "content": "G alt", "importance": 3, "updated_at": t0},
            {"gruppe": "persoenlich", "content": "P wichtig", "importance": 5, "updated_at": t0},
            {"gruppe": "gemeinsam", "content": "G neu", "importance": 3, "updated_at": t0 + timedelta(days=1)},
            {"gruppe": "persoenlich", "content": "P normal", "importance": 3, "updated_at": t0},
        ]
        r = await store.recall(10, 5)
        self.assertEqual(r.persoenlich, ["P wichtig", "P normal"])
        self.assertEqual(r.gemeinsam, ["G neu", "G alt"])
        fetches = [c for c in db.calls if c[0] == "fetch"]
        self.assertEqual(len(fetches), 1)
        sql, args = fetches[0][1], fetches[0][2]
        self.assertIn("union all", sql)
        self.assertNotIn("::text = any", sql)
        self.assertIn("kind in ('fact', 'preference', 'behavior', 'lesson')", sql)
        self.assertEqual(args, (USER, 10, 5, 0))  # ohne include_proposed keine Vorschläge


class AdminDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_functions_need_admin_connection(self):
        admin = m.AdminDirectory(m.Database(None, "wamo_test"))
        self.assertFalse(admin.configured)
        with self.assertRaises(m.DatabaseUnavailable):
            await admin.list_profiles()

    def test_admin_database_reads_own_variable(self):
        with mock.patch.dict(os.environ, {"WAMO_ADMIN_DATABASE_URL": "", "WAMO_DB_SCHEMA": "wamo_test"}):
            self.assertFalse(m.admin_database().configured)

    def test_core_directory_has_no_admin_functions(self):
        for name in ("create_profile", "list_profiles", "list_proposed_shared", "review_shared"):
            self.assertFalse(hasattr(m.ProfileDirectory, name), name)
            self.assertTrue(hasattr(m.AdminDirectory, name), name)


if __name__ == "__main__":
    unittest.main()
