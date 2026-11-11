"""Tests für LaborSpeicher (Testlabor 3.1, P1c, T4): Gesprächstext der Laborprofile wird gespeichert (Variante B), aber nur mit gültiger
Einwilligung und nur in sessions, turns, actions und canvas_items. Gedächtnis gibt es nie: kein Lesen, kein Schreiben, kein Lernen.

Ausführen:  python -m pytest -p no:cacheprovider -q test_labor_speicher.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

from loguru import logger

import labor
from auth_gate import Identity
from labor_durchlauf import Durchlauf
from labor_speicher import LaborDatenbank, LaborSpeicher, LaborSqlGesperrt
from memory import MemoryStore, Recall

USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
DURCHLAUF = "5b0c7a0e-1111-4222-8333-444455556666"
ERLAUBTE_TABELLEN = {"sessions", "turns", "actions", "canvas_items"}
ERLAUBTE_TYPEN = {"turn_role", "risk_class", "action_status", "canvas_type"}


class StrengeDb:
    """Test-Datenbank: lässt nur insert und update auf sessions, turns, actions und canvas_items zu und lehnt jede andere Anweisung mit
    AssertionError ab (auch jedes select). Merkt sich alles, was durchkam."""

    configured = True
    schema = "wamo_test"

    def __init__(self):
        self.anweisungen: list[tuple[str, tuple]] = []

    def t(self, name):
        return f"wamo_test.{name}"

    def _pruefen(self, sql):
        m = re.match(r"\s*(insert\s+into|update)\s+wamo_test\.(\w+)", sql, re.I)
        assert m and m.group(2) in ERLAUBTE_TABELLEN, f"nicht erlaubt: {sql[:80]}"
        for tabelle in re.findall(r"wamo_test\.(\w+)", sql):
            assert tabelle in ERLAUBTE_TABELLEN | ERLAUBTE_TYPEN, f"fremde Tabelle {tabelle}"
        assert not re.search(r"\b(select|from|using|join|delete|with)\b", sql, re.I), sql[:80]

    async def fetchval(self, sql, *args):
        self._pruefen(sql)
        self.anweisungen.append((sql, args))
        return f"00000000-0000-4000-8000-{len(self.anweisungen):012d}"

    async def execute(self, sql, *args):
        self._pruefen(sql)
        self.anweisungen.append((sql, args))
        return "UPDATE 1" if sql.lstrip().lower().startswith("update") else "INSERT 0 1"

    async def executemany(self, sql, args):
        self._pruefen(sql)
        self.anweisungen.append((sql, tuple(args)))

    async def fetch(self, sql, *args):
        raise AssertionError(f"select nicht erlaubt: {sql[:80]}")

    fetchrow = fetch

    async def close(self):
        pass

    def tabellen(self):
        return [re.match(r"\s*(?:insert\s+into|update)\s+wamo_test\.(\w+)", s, re.I).group(1) for s, _ in self.anweisungen]


def identitaet(art="platz", platz=3):
    return Identity(USER, "Gast", "mitglied", "du", labor_art=art, labor_platz=platz if art == "platz" else None, labor_aktiv=True)


def speicher(db, *, version=2, offen=True, art="platz", text=True, minimum=2):
    stand = Durchlauf(DURCHLAUF, offen, version) if art == "platz" else None
    with mock.patch.dict("os.environ", {"WAMO_LABOR_TEXT_SPEICHERN": "an" if text else "aus"}):
        return LaborSpeicher.fuer(identitaet(art), stand, db=db, min_version=minimum)


class KeinSchreibenOhneBedingungen(unittest.IsolatedAsyncioTestCase):
    async def alles_versuchen(self, s):
        await s.start_session("web")
        await s.log_turn("user", "Mein Name ist Erika Beispiel")
        await s.log_turn("assistant", "Hallo")
        self.assertIsNone(await s.log_action("ticket_anlegen", {"titel": "Test"}, "weil", "change"))
        self.assertIsNone(await s.save_canvas("document", "Titel", {"kind": "document", "text": "Inhalt"}))
        await s.end_session()

    async def test_ohne_einwilligungsstand_nichts(self):
        db = StrengeDb()
        s = LaborSpeicher.fuer(identitaet(), None, db=db, min_version=2)
        self.assertFalse(s.enabled)
        await self.alles_versuchen(s)
        self.assertEqual(db.anweisungen, [])

    async def test_zu_niedrige_version_nichts(self):
        for version in (None, 0, 1):
            with self.subTest(version=version):
                db = StrengeDb()
                s = speicher(db, version=version)
                self.assertFalse(s.enabled)
                await self.alles_versuchen(s)
                self.assertEqual(db.anweisungen, [])

    async def test_beendeter_durchlauf_nichts(self):
        db = StrengeDb()
        await self.alles_versuchen(speicher(db, offen=False))
        self.assertEqual(db.anweisungen, [])

    async def test_schalter_aus_ist_variante_a_nichts(self):
        for wert in ("aus", "", "ja", "true", "AN ", "1", None):
            with self.subTest(wert=wert):
                db = StrengeDb()
                env = {"WAMO_LABOR_TEXT_SPEICHERN": wert} if wert is not None else {}
                with mock.patch.dict("os.environ", env, clear=True):
                    s = LaborSpeicher.fuer(identitaet(), Durchlauf(DURCHLAUF, True, 2), db=db, min_version=2)
                    expect = wert is not None and wert.strip().lower() == "an"
                    self.assertEqual(s.enabled, expect)
                    if not expect:
                        await self.alles_versuchen(s)
                        self.assertEqual(db.anweisungen, [])

    async def test_schaufenster_hat_keine_einwilligung_und_speichert_nie(self):
        db = StrengeDb()
        await self.alles_versuchen(speicher(db, art="schaufenster"))
        self.assertEqual(db.anweisungen, [])

    async def test_ohne_datenbank_nichts_und_kein_fehler(self):
        s = LaborSpeicher.fuer(identitaet(), Durchlauf(DURCHLAUF, True, 2), db=None, min_version=2)
        self.assertFalse(s.enabled)
        await self.alles_versuchen(s)


class SchreibenMitEinwilligung(unittest.IsolatedAsyncioTestCase):
    async def test_t4_nur_die_vier_tabellen_und_der_vermerk_zuerst(self):
        db = StrengeDb()
        s = speicher(db)
        self.assertTrue(s.enabled)
        await s.start_session("web")
        await s.log_turn("user", "Hallo WAMO")
        await s.log_turn("assistant", "Hallo")
        aktion = await s.log_action("ticket_anlegen", {"nummer": "LAB-1"}, "Freigabe", "change")
        self.assertTrue(aktion)
        self.assertTrue(await s.update_action(aktion, "approved"))
        self.assertTrue(await s.update_action(aktion, "running"))
        self.assertTrue(await s.aktion_abschliessen(aktion, "done", {"nummer": "LAB-1"}))
        self.assertTrue(await s.save_canvas("document", "Bericht", {"kind": "document", "text": "x"}))
        await s.end_session()
        tabellen = db.tabellen()
        self.assertEqual(tabellen[0], "sessions")  # die Sitzung zuerst
        self.assertEqual(tabellen[1], "actions")  # dann der Einwilligungsvermerk, noch vor jedem Gesprächstext
        self.assertEqual(set(tabellen), ERLAUBTE_TABELLEN)
        self.assertIn("turns", tabellen)
        vermerk = db.anweisungen[1][1]
        self.assertEqual(vermerk[2], "labor_einwilligung")
        self.assertEqual(json.loads(vermerk[3]), {"version": 2, "durchlauf_id": DURCHLAUF})  # ohne Text
        self.assertEqual(vermerk[5], "read")

    async def test_vermerk_vor_dem_ersten_text_auch_bei_vorab_gepufferter_zeile(self):
        db = StrengeDb()
        s = speicher(db)
        await s.log_turn("user", "ganz früh gesagt")  # vor start_session: gepuffert
        await s.start_session("web")
        reihenfolge = db.tabellen()
        self.assertLess(reihenfolge.index("actions"), reihenfolge.index("turns"))

    async def test_das_gespraech_steht_in_turns_mit_der_profilkennung(self):
        db = StrengeDb()
        s = speicher(db)
        await s.start_session("web")
        await s.log_turn("user", "Hallo WAMO")
        sql, args = next((sql, args) for sql, args in db.anweisungen if "turns" in sql.split("(")[0])
        self.assertEqual(args[1], USER)
        self.assertEqual(args[3], "Hallo WAMO")

    async def test_ein_schluessel_im_text_wird_geschwaerzt(self):
        db = StrengeDb()
        s = speicher(db)
        await s.start_session("web")
        await s.log_turn("user", "mein Schluessel ist sk-ant-api03-" + "a" * 40)
        text = next(args[3] for sql, args in db.anweisungen if "turns" in sql.split("(")[0])
        self.assertNotIn("sk-ant-api03-" + "a" * 40, text)


class GedaechtnisGibtEsNie(unittest.IsolatedAsyncioTestCase):
    async def test_recall_ist_leer_auch_bei_gefuellter_datenbank(self):
        class Gefuellt(StrengeDb):
            async def fetch(self, sql, *args):
                return [{"gruppe": "persoenlich", "content": "Geheim", "importance": 5, "updated_at": None}]

        db = Gefuellt()
        s = speicher(db)
        self.assertEqual(await s.recall(), Recall())
        self.assertEqual(await s.recall(include_proposed=True), Recall())
        self.assertEqual(db.anweisungen, [])

    async def test_remember_forget_und_kosten_ohne_datenbankzugriff(self):
        db = StrengeDb()
        s = speicher(db)
        self.assertIsNone(await s.remember("Ein Fakt", "fact", bereich="gemeinsam", status="aktiv"))
        self.assertIsNone(await s.remember("Persönlich", "preference"))
        self.assertFalse(await s.forget("5b0c7a0e-1111-4222-8333-444455556666"))
        self.assertIsNone(await s.tageskosten())
        self.assertIsNone(await s.veraltete_schliessen())
        self.assertEqual(db.anweisungen, [])

    async def test_von_memorystore_geerbte_wege_erreichen_andere_tabellen_nie(self):
        # Selbst wenn jemand die Überschreibung umgeht (ungebundener Aufruf der Basisklasse): die Hülle lässt nur die vier Tabellen zu
        db = StrengeDb()
        s = speicher(db)
        self.assertIsNone(await MemoryStore.remember(s, "Ein Fakt", "fact"))
        self.assertIsNone(await MemoryStore.remember(s, "Gemeinsam", "fact", bereich="gemeinsam", status="vorgeschlagen"))
        self.assertFalse(await MemoryStore.forget(s, "5b0c7a0e-1111-4222-8333-444455556666"))
        self.assertEqual(await MemoryStore.recall(s), Recall())
        self.assertIsNone(await MemoryStore.tageskosten(s))
        self.assertEqual(db.anweisungen, [])

    async def test_from_env_ist_gesperrt(self):
        with self.assertRaises(labor.LaborPolitikFehler):
            LaborSpeicher.from_env(USER)


class DatenbankHuelleTests(unittest.IsolatedAsyncioTestCase):
    async def test_nur_erlaubte_anweisungen_kommen_durch(self):
        innen = StrengeDb()
        db = LaborDatenbank(innen)
        gut = [
            "insert into wamo_test.sessions (user_id, channel) values ($1::uuid, $2) returning id::text",
            "insert into wamo_test.turns (session_id, user_id, role) values ($1::uuid, $2::uuid, $3::text::wamo_test.turn_role)",
            "update wamo_test.actions set status = $2::text::wamo_test.action_status where id = $1::uuid",
            "insert into wamo_prod.canvas_items (user_id) values ($1)",
        ]
        for sql in gut[:3]:
            await db.execute(sql, 1)
        schlecht = [
            "select * from wamo_test.memories",
            "insert into wamo_test.memories (user_id) values ($1)",
            "update wamo_test.memories set is_active = false",
            "insert into wamo_test.turns select * from wamo_test.memories",
            "insert into wamo_test.turns (a) values (1); delete from wamo_test.memories",
            "insert into wamo_test.turns (a) values ((select content from wamo_test.memories limit 1))",
            "update wamo_test.actions set status = 'x' from wamo_test.memories where true",
            "delete from wamo_test.turns",
            "with x as (select 1) insert into wamo_test.turns (a) values (1)",
            "insert into wamo_test.profiles (user_id) values ($1)",
            "insert into wamo_test.speakers (user_id) values ($1)",
            "insert into wamo_test.feedback (user_id) values ($1)",
            "insert into wamo_test.vorgaenge (user_id) values ($1)",
            "insert into wamo_test.model_access (user_id) values ($1)",
            "insert into public.turns (a) values (1)",
            "insert into turns (a) values (1)",
            "insert into wamo_test.turns (a) values (1) returning (select 1 from wamo_test.memories)",
            "  INSERT  INTO wamo_test.MEMORIES (a) values (1)",
            "truncate wamo_test.turns",
            "",
        ]
        for sql in schlecht:
            for aufruf in (db.execute, db.fetchval, db.fetch, db.fetchrow):
                with self.subTest(sql=sql, aufruf=aufruf.__name__), self.assertRaises(LaborSqlGesperrt):
                    await aufruf(sql)
            with self.subTest(sql=sql, aufruf="executemany"), self.assertRaises(LaborSqlGesperrt):
                await db.executemany(sql, [(1,)])
        self.assertEqual(len(innen.anweisungen), 3)
        self.assertGreaterEqual(db.verletzungen, len(schlecht))
        self.assertNotIn("pool", dir(db))  # kein Weg an den Pool vorbei an der Prüfung


class LogUndUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_der_gespraechstext_steht_nicht_im_log(self):
        zeilen: list[str] = []
        kennung = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG", format="{message}")
        try:
            db = StrengeDb()
            s = speicher(db)
            await s.start_session("web")
            await s.log_turn("user", "GEHEIMER-TESTTEXT-4711")
            await s.log_action("ticket_anlegen", {"titel": "GEHEIMER-TESTTEXT-4711"}, "Grund", "change")
            await s.save_canvas("document", "GEHEIMER-TESTTEXT-4711", {"kind": "document", "text": "GEHEIMER-TESTTEXT-4711"})
            await s.end_session()
        finally:
            logger.remove(kennung)
        self.assertFalse([z for z in zeilen if "GEHEIMER-TESTTEXT-4711" in z])

    def test_die_unit_setzt_die_log_stufe_info(self):
        wurzel = Path(__file__).resolve().parent.parent
        for name in ("wamo-core@.service", "wamo-core.service"):
            text = (wurzel / "infra" / "sokrates2" / name).read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^Environment=.*WAMO_LOG_LEVEL=INFO", name)

    def test_die_debug_stellen_stehen_noch_wo_der_plan_sie_nennt(self):
        # Bei INFO kein Text, bei DEBUG Text: der Test hält fest, dass der Text nur hinter logger.debug steht (bot.py, tools.py)
        for datei in ("bot.py", "tools.py"):
            quelle = (Path(__file__).resolve().parent / datei).read_text(encoding="utf-8")
            for zeile in quelle.splitlines():
                if "logger.info" in zeile:
                    self.assertNotIn("{!r}", zeile, f"{datei}: Text im INFO-Log: {zeile.strip()}")


if __name__ == "__main__":
    unittest.main()
