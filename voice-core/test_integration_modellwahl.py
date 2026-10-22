"""Integrationstest der Modellwahl und des Claude-Code-Tokens gegen die echte Datenbank, nur im Schema wamo_test
(Migration 0011). Nichts bleibt zurück: Die Schreibversuche nutzen eine erfundene user_id und scheitern am
Fremdschlüssel auf profiles, nachdem die Rechte geprüft sind (ein fehlendes Spaltenrecht gäbe stattdessen
InsufficientPrivilegeError). Gelesen wird nur.

Übersprungen, wenn WAMO_DATABASE_URL fehlt oder die Migration 0011 in wamo_test nicht angewendet ist.
Ausführen:  uv run python -m pytest -q test_integration_modellwahl.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import unittest
import uuid

import asyncpg

import modellzugang as mz
from test_integration_db import CORE_DSN, _SchemaTest


@unittest.skipUnless(CORE_DSN, "WAMO_DATABASE_URL fehlt")
class ModellwahlDatenbankTests(_SchemaTest):
    dsn = CORE_DSN

    async def asyncSetUp(self):
        await super().asyncSetUp()
        try:
            await self.db.fetchrow(f"select modell_modus, claude_code_token_ende from {self.db.t('model_access')} limit 1")
        except asyncpg.UndefinedColumnError:
            self.skipTest("Migration 0011 ist in wamo_test nicht angewendet")

    async def test_der_kern_liest_die_neuen_spalten(self):
        zeile = await mz._zeile(self.db, self.user)  # unbekanntes Profil: keine Zeile, aber die Abfrage läuft
        self.assertIsNone(zeile)
        stand = await mz.stand_laden(self.user, db=self.db)
        self.assertEqual((stand.modus, stand.code_hinterlegt), ("automatisch", False))
        zugang = await mz.zugang_laden(self.user, db=self.db)
        self.assertEqual((zugang.art, zugang.claude_code), ("haus", False))

    async def test_der_kern_darf_wahl_und_token_schreiben(self):
        # Fremdschlüssel statt Rechtefehler: die Spaltenrechte stimmen, die erfundene Person gibt es nicht
        for sql, args in (
            ("insert into {t} (user_id, modell_modus) values ($1::uuid, 'nur_haus') on conflict (user_id) "
             "do update set modell_modus = excluded.modell_modus", ()),
            ("insert into {t} (user_id, claude_code_token, claude_code_token_ende, claude_code_token_at) values "
             "($1::uuid, decode(repeat('ab', 60), 'hex'), 'wxyz', now()) on conflict (user_id) do update set "
             "claude_code_token = excluded.claude_code_token, claude_code_token_ende = excluded.claude_code_token_ende, "
             "claude_code_token_at = excluded.claude_code_token_at", ()),
        ):
            with self.assertRaises(asyncpg.ForeignKeyViolationError):
                await self.db.execute(sql.format(t=self.db.t("model_access")), self.user, *args)
        # Löschen des Tokens einer unbekannten Person: erlaubt, aber ohne Wirkung
        await mz.code_token_loeschen(self.user, db=self.db)

    async def test_der_kern_darf_nur_haus_und_loeschen_nicht(self):
        t = self.db.t("model_access")
        with self.assertRaises(asyncpg.InsufficientPrivilegeError):
            await self.db.execute(f"update {t} set nur_haus = true where user_id = $1::uuid", self.user)
        with self.assertRaises(asyncpg.InsufficientPrivilegeError):
            await self.db.execute(f"delete from {t} where user_id = $1::uuid", self.user)

    async def test_ungueltige_wahl_scheitert_an_der_pruefung(self):
        with self.assertRaises(asyncpg.CheckViolationError):
            await self.db.execute(
                f"insert into {self.db.t('model_access')} (user_id, modell_modus) values ($1::uuid, 'egal')",
                str(uuid.uuid4()))

    async def test_kein_token_fuer_eine_unbekannte_person(self):
        self.assertIsNone(await mz.claude_code_zugang_fuer(self.user, db=self.db))


if __name__ == "__main__":
    unittest.main()
