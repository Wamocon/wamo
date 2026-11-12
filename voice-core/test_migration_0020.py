"""Prüft Migration 0020 (Gedächtnisvorschläge in der Oberfläche entscheiden) als Text: eine einzige Funktion, die genau eine offene Zeile von
memories in genau drei Spalten ändert, nur für wamo_core aufrufbar, ohne neue Tabelle, ohne neues Recht auf memories, mit Selbsttest und
Rückweg. Dazu die Regeln aus CLAUDE.md (Platzhalter, keine Rechte für anon, authenticated, service_role). Die Wirkung in der Datenbank prüft
supabase/tests/0020_gedaechtnis_vorschlaege_test.sql gegen wamo_test oder wamo_dev; sie läuft nicht in diesem Test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SUPABASE = Path(__file__).resolve().parent.parent / "supabase"
SQL = (SUPABASE / "migrations" / "0020_gedaechtnis_vorschlaege.sql").read_text(encoding="utf-8")
DOWN = (SUPABASE / "migrations" / "0020_gedaechtnis_vorschlaege.down.sql").read_text(encoding="utf-8")
TEST = (SUPABASE / "tests" / "0020_gedaechtnis_vorschlaege_test.sql").read_text(encoding="utf-8")
FREMDE = ("anon", "authenticated", "service_role", "public")
FUNKTION = "gedaechtnis_vorschlag_entscheiden"


def _ohne_kommentare(text: str) -> str:
    return "\n".join(z.split("--", 1)[0] for z in text.splitlines())


def _eine_zeile(text: str) -> str:
    return " ".join(text.split())


CODE = _eine_zeile(_ohne_kommentare(SQL))
DOWN_CODE = _eine_zeile(_ohne_kommentare(DOWN))


def _funktion() -> str:
    anfang = CODE.index(f"create or replace function __SCHEMA__.{FUNKTION}(")
    return CODE[anfang:CODE.index("end $$;", anfang) + len("end $$;")]


# ---------- Allgemeine Regeln ----------
@pytest.mark.parametrize("text", [SQL, DOWN, TEST], ids=["migration", "rueckweg", "sql-test"])
def test_nur_der_platzhalter_nie_ein_festes_schema(text):
    code = _ohne_kommentare(text)
    assert "__SCHEMA__" in code
    # der SQL-Test nennt wamo_prod genau einmal: als Sperre (er läuft nie dort)
    code = code.replace("'__SCHEMA__' = 'wamo_prod'", "").replace("'Test nie in wamo_prod'", "").replace("wamo_testhilfe", "")
    assert not re.search(r"\bwamo_(dev|test|prod)\b", code)


def test_zwei_dateien_wie_bei_jeder_migration_und_ein_test():
    assert "0020_gedaechtnis_vorschlaege.down.sql" in SQL and "render-migration.sh" in SQL
    assert "render-migration.sh" in DOWN
    assert "select '0020 OK';" in TEST and "rollback;" in TEST and "wamo_prod" in TEST  # der Test verweigert wamo_prod


def test_keine_neue_tabelle_keine_neue_rolle_kein_neuer_trigger():
    for wort in ("create table", "create role", "alter role", "create trigger", "create policy", "alter table", "create index"):
        assert wort not in CODE, wort


def test_kein_recht_auf_memories_wird_vergeben():
    assert not re.search(r"grant\s+(select|insert|update|delete|all)[^;]*\bon\b[^;]*memories", CODE)
    assert "grant update" not in CODE and "grant delete" not in CODE


# ---------- Die Funktion ----------
def test_security_definer_mit_leerem_search_path():
    f = _funktion()
    assert "security definer" in f
    assert "set search_path = ''" in f


def test_signatur_und_rueckgabe():
    f = _funktion()
    assert f"{FUNKTION}(p_id uuid, p_admin uuid, p_entscheidung text) returns text" in f


def test_prueft_eingaben_und_admin_in_der_datenbank():
    f = _funktion()
    assert "errcode = '22004'" in f and "errcode = '22023'" in f and "errcode = '42501'" in f
    assert "p_entscheidung not in ('freigeben', 'ablehnen')" in f
    assert "p.aktiv and p.rolle = 'admin'" in f and "p.user_id = p_admin" in f


def test_aendert_genau_drei_spalten_und_nur_eine_offene_gemeinsame_zeile():
    f = _funktion()
    updates = re.findall(r"update __SCHEMA__\.memories m set (.*?) where (.*?);", f)
    assert len(updates) == 1
    gesetzt, bedingung = updates[0]
    assert [s.split("=")[0].strip() for s in re.split(r",\s*(?=\w+\s*=)", gesetzt)] == ["status", "reviewed_by", "reviewed_at"]
    for teil in ("m.id = p_id", "m.bereich = 'gemeinsam'", "m.status = 'vorgeschlagen'", "m.is_active"):
        assert teil in bedingung
    assert "insert into" not in f and "delete from" not in f


def test_ergebnisse_sind_fest():
    f = _funktion()
    for wort in ("'nicht_offen'", "'freigegeben'", "'abgelehnt'"):
        assert wort in f


# ---------- Rechte und Selbsttest ----------
def test_nur_wamo_core_darf_aufrufen():
    rechte = f"revoke all on function __SCHEMA__.{FUNKTION}(uuid, uuid, text) from public, anon, authenticated, service_role, wamo_admin;"
    assert rechte in CODE
    assert f"grant execute on function __SCHEMA__.{FUNKTION}(uuid, uuid, text) to wamo_core;" in CODE
    for fremd in FREMDE:
        assert not re.search(rf"grant[^;]*\bto\b[^;]*\b{fremd}\b", CODE), fremd


def test_selbsttest_haelt_die_rechte_fest():
    for teil in ("prosecdef", "search_path=%", "has_function_privilege('wamo_core'", "has_function_privilege('anon'",
                 "has_function_privilege('authenticated'", "has_function_privilege('service_role'", "has_function_privilege('wamo_admin'",
                 "has_column_privilege('wamo_core', '__SCHEMA__.memories', 'status', 'UPDATE')",
                 "has_table_privilege('wamo_core', '__SCHEMA__.memories', 'DELETE')"):
        assert teil in CODE, teil


def test_vorbedingung_und_eintrag_in_schema_migrations():
    assert "to_regclass('__SCHEMA__.memories') is null" in CODE and "to_regprocedure('__SCHEMA__.memories_guard()') is null" in CODE
    assert "insert into __SCHEMA__.schema_migrations (version) values ('0020_gedaechtnis_vorschlaege') on conflict do nothing;" in CODE
    assert "set local lock_timeout" in CODE


# ---------- Rückweg ----------
def test_rueckweg_entfernt_nur_die_funktion_und_den_eintrag():
    assert f"drop function if exists __SCHEMA__.{FUNKTION}(uuid, uuid, text);" in DOWN_CODE
    assert "delete from __SCHEMA__.schema_migrations where version = '0020_gedaechtnis_vorschlaege';" in DOWN_CODE
    assert "update" not in DOWN_CODE.replace("on update", "") and "memories" not in DOWN_CODE  # an den Einträgen ändert er nichts
    assert "wamo.down_trotz_folgemigration" in DOWN_CODE  # späteren Migrationen den Vortritt lassen


def test_das_sql_test_prueft_die_wichtigen_faelle():
    for teil in ("'freigegeben'", "'abgelehnt'", "'nicht_offen'", "'42501'", "'22004'", "'22023'",
                 "set role wamo_core", "set role wamo_admin", "kein direktes UPDATE auf status"):
        assert teil in TEST, teil
