"""Tests für stimme_einwilligung.py und stimme_speicher.py ohne Datenbank (Attrappe wie in test_memory.py).

Ausführen:  python -m pytest -q test_stimme_speicher.py   (im Ordner voice-core/)
"""

import unittest
from datetime import datetime, timezone

import asyncpg
import numpy as np

import memory as m
import stimme_einwilligung as se
import stimme_speicher as ss
from stimme_modell import DIM, als_vektor, kosinus

USER_A = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
USER_B = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"
ABDRUCK = np.eye(DIM, dtype=np.float32)[3]


class FakeDb:
    configured = True
    schema = "wamo_test"

    def __init__(self, zeilen: dict | None = None, fehler: Exception | None = None):
        self.zeilen = zeilen if zeilen is not None else {}  # user_id -> Text des Abdrucks
        self.fehler = fehler
        self.calls: list[tuple] = []

    def t(self, name):
        return f"wamo_test.{name}"

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if self.fehler:
            raise self.fehler
        return self.zeilen.get(args[0])

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        if self.fehler:
            raise self.fehler
        if sql.startswith("delete"):
            return f"DELETE {1 if self.zeilen.pop(args[0], None) is not None else 0}"
        self.zeilen[args[0]] = args[2]
        return "INSERT 0 1"


def speicher(user: str, db: FakeDb | None) -> m.MemoryStore:
    return m.MemoryStore(user, db)


class EinwilligungTests(unittest.TestCase):
    def test_version_und_nachricht(self):
        self.assertEqual(se.EINWILLIGUNG_VERSION, "stimme-1")
        self.assertEqual(
            se.einwilligung_nachricht(),
            {"type": "stimmabdruck_einwilligung", "version": "stimme-1", "text": se.EINWILLIGUNG_TEXT},
        )

    def test_nur_genau_diese_version_gilt(self):
        self.assertTrue(se.einwilligung_gilt("stimme-1"))
        for falsch in ("stimme-0", "stimme-2", "Stimme-1", " stimme-1", "", None, 1, True, ["stimme-1"]):
            self.assertFalse(se.einwilligung_gilt(falsch), falsch)

    def test_text_nennt_den_kern_der_einwilligung_und_hat_keinen_gedankenstrich(self):
        text = se.EINWILLIGUNG_TEXT
        for teil in ("192 Zahlen", "keine Aufnahme", "freiwillig", "jederzeit", "Einstellungen", "Widerruf", "Artikel 9"):
            self.assertIn(teil, text)
        self.assertNotIn("–", text)
        self.assertNotIn("—", text)
        self.assertNotIn(" - ", text)
        self.assertRegex(text, "[äöüß]")  # echte Umlaute


class LadenTests(unittest.IsolatedAsyncioTestCase):
    async def test_laedt_den_abdruck_des_eigenen_profils(self):
        db = FakeDb({USER_A: als_vektor(ABDRUCK)})
        ok, abdruck = await ss.abdruck_laden(speicher(USER_A, db))
        self.assertTrue(ok)
        self.assertAlmostEqual(kosinus(abdruck, ABDRUCK), 1.0, places=5)
        sql, args = db.calls[0][1], db.calls[0][2]
        self.assertIn("wamo_test.speakers", sql)
        self.assertIn("is_owner", sql)
        self.assertEqual(args, (USER_A,))

    async def test_kein_abdruck_ist_ok_und_leer(self):
        self.assertEqual(await ss.abdruck_laden(speicher(USER_A, FakeDb())), (True, None))

    async def test_zwei_profile_in_einem_prozess_bekommen_je_ihren_abdruck(self):
        andere = np.eye(DIM, dtype=np.float32)[7]
        db = FakeDb({USER_A: als_vektor(ABDRUCK), USER_B: als_vektor(andere)})
        _, a = await ss.abdruck_laden(speicher(USER_A, db))
        _, b = await ss.abdruck_laden(speicher(USER_B, db))
        self.assertAlmostEqual(kosinus(a, ABDRUCK), 1.0, places=5)
        self.assertAlmostEqual(kosinus(b, andere), 1.0, places=5)
        self.assertLess(kosinus(a, b), 0.01)
        _, drittes = await ss.abdruck_laden(speicher("11111111-2222-4333-8444-555555555555", db))
        self.assertIsNone(drittes)

    async def test_falsches_format_ist_ein_fehler_kein_abdruck(self):
        db = FakeDb({USER_A: "[0.1,0.2]"})
        self.assertEqual(await ss.abdruck_laden(speicher(USER_A, db)), (False, None))

    async def test_datenbankfehler_ist_ein_fehler_und_wirft_nicht(self):
        db = FakeDb(fehler=asyncpg.PostgresError("kaputt"))
        self.assertEqual(await ss.abdruck_laden(speicher(USER_A, db)), (False, None))

    async def test_ohne_datenbank_oder_profil_wird_nichts_gefragt(self):
        for store in (speicher(USER_A, None), speicher(None, FakeDb()), speicher("keine-uuid", FakeDb())):
            self.assertEqual(await ss.abdruck_laden(store), (False, None))


class SpeichernTests(unittest.IsolatedAsyncioTestCase):
    async def test_speichert_mit_einwilligung_und_version(self):
        db = FakeDb()
        am = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)
        ok = await ss.abdruck_speichern(speicher(USER_A, db), ABDRUCK, version="stimme-1", name="Erika", einwilligung_am=am)
        self.assertTrue(ok)
        art, sql, args = db.calls[0]
        self.assertIn("wamo_test.speakers", sql)
        self.assertIn("on conflict (user_id) where is_owner do update", sql)
        self.assertIn("extensions.vector", sql)
        self.assertEqual(args[0], USER_A)
        self.assertEqual(args[1], "Erika")
        self.assertEqual(args[2], als_vektor(ABDRUCK))
        self.assertEqual(args[3], "stimme-1")
        self.assertEqual(args[4], am)
        self.assertIn(USER_A, db.zeilen)

    async def test_ohne_gueltige_einwilligung_wird_nichts_gespeichert(self):
        db = FakeDb()
        am = datetime.now(timezone.utc)
        for version in ("", "stimme-0", None):
            self.assertFalse(await ss.abdruck_speichern(speicher(USER_A, db), ABDRUCK, version=version, name="E", einwilligung_am=am))
        self.assertFalse(await ss.abdruck_speichern(speicher(USER_A, db), ABDRUCK, version="stimme-1", name="E", einwilligung_am=None))
        self.assertEqual(db.calls, [])

    async def test_name_wird_auf_die_erlaubte_laenge_gebracht(self):
        db = FakeDb()
        am = datetime.now(timezone.utc)
        await ss.abdruck_speichern(speicher(USER_A, db), ABDRUCK, version="stimme-1", name="x" * 200, einwilligung_am=am)
        self.assertEqual(len(db.calls[0][2][1]), 60)
        await ss.abdruck_speichern(speicher(USER_A, db), ABDRUCK, version="stimme-1", name="  ", einwilligung_am=am)
        self.assertTrue(db.calls[1][2][1])

    async def test_datenbankfehler_ist_kein_erfolg(self):
        db = FakeDb(fehler=asyncpg.PostgresError("kaputt"))
        am = datetime.now(timezone.utc)
        self.assertFalse(await ss.abdruck_speichern(speicher(USER_A, db), ABDRUCK, version="stimme-1", name="E", einwilligung_am=am))

    async def test_ohne_datenbank_kein_erfolg(self):
        am = datetime.now(timezone.utc)
        self.assertFalse(await ss.abdruck_speichern(speicher(USER_A, None), ABDRUCK, version="stimme-1", name="E", einwilligung_am=am))


class LoeschenTests(unittest.IsolatedAsyncioTestCase):
    async def test_loescht_nur_den_eigenen_abdruck_vollstaendig(self):
        db = FakeDb({USER_A: als_vektor(ABDRUCK), USER_B: als_vektor(ABDRUCK)})
        self.assertTrue(await ss.abdruck_loeschen(speicher(USER_A, db)))
        sql, args = db.calls[0][1], db.calls[0][2]
        self.assertTrue(sql.startswith("delete from wamo_test.speakers"))
        self.assertIn("is_owner", sql)
        self.assertEqual(args, (USER_A,))
        self.assertNotIn(USER_A, db.zeilen)
        self.assertIn(USER_B, db.zeilen)

    async def test_nichts_zu_loeschen_ist_auch_in_ordnung(self):
        self.assertTrue(await ss.abdruck_loeschen(speicher(USER_A, FakeDb())))

    async def test_datenbankfehler_ist_kein_erfolg(self):
        self.assertFalse(await ss.abdruck_loeschen(speicher(USER_A, FakeDb(fehler=asyncpg.PostgresError("kaputt")))))

    async def test_ohne_datenbank_kein_erfolg(self):
        self.assertFalse(await ss.abdruck_loeschen(speicher(USER_A, None)))


if __name__ == "__main__":
    unittest.main()
