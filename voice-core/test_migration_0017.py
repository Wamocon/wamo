"""Prüft Migration 0017 (Vorgänge) als Text: Regeln aus CLAUDE.md für Migrationen (RLS, Policy core_all für wamo_core, nur die
nötigen Grants, kein delete, keine Rechte für anon, authenticated, service_role, partieller FK-Index, drop vor create policy,
Platzhalter) und dass die erlaubten Werte der Prüfungen zu vorgaenge.py passen. Die Wirkung in der Datenbank prüft
supabase/tests/0017_vorgaenge_test.sql gegen wamo_test oder wamo_dev; sie läuft nicht in diesem Test."""

from __future__ import annotations

import re
from pathlib import Path

import vorgaenge as vg

SUPABASE = Path(__file__).resolve().parent.parent / "supabase"
SQL = (SUPABASE / "migrations" / "0017_vorgaenge.sql").read_text(encoding="utf-8")
DOWN = (SUPABASE / "migrations" / "0017_vorgaenge.down.sql").read_text(encoding="utf-8")
TEST = (SUPABASE / "tests" / "0017_vorgaenge_test.sql").read_text(encoding="utf-8")
FREMDE = ("anon", "authenticated", "service_role", "public", "wamo_admin")
SPALTEN = ("id", "user_id", "session_id", "art", "titel", "status", "gesamt", "daten", "gestartet_at", "beendet_at")


def _ohne_kommentare(text: str) -> str:
    return "\n".join(z.split("--", 1)[0] for z in text.splitlines())


def _anweisungen(text: str, wort: str) -> list[str]:
    return [a.strip() for a in re.split(r";\s*\n", _ohne_kommentare(text)) if re.match(rf"\s*{wort}\b", a, re.I)]


def _werte(spalte: str) -> set[str]:
    treffer = re.search(rf"{spalte}\s+text not null check \({spalte} in \((.*?)\)\)", SQL, re.S)
    assert treffer, spalte
    return set(re.findall(r"'([a-z_]+)'", treffer.group(1)))


def test_schema_platzhalter_und_keine_festen_schemas():
    for text in (SQL, DOWN, TEST):
        code = _ohne_kommentare(text)
        assert "__SCHEMA__" in code
        assert not re.search(r"\bwamo_(dev|test|prod)\.", code)


def test_keine_gedankenstriche_und_nur_echte_umlaute_in_den_kommentaren():
    for text in (SQL, DOWN, TEST):
        assert chr(0x2013) not in text and chr(0x2014) not in text
        kommentare = [z for z in text.splitlines() if z.lstrip().startswith("--")]
        for zeile in kommentare:
            assert " - " not in zeile, zeile
        assert not re.search(r"\b(fuer|ueber|Pruefung|Rueckweg|koennen|waehrend|aendern|Aenderung|loeschen)\b",
                             " ".join(kommentare)), "Ersatzschreibweise für Umlaute im Kommentar"


def test_tabelle_idempotent_mit_den_vereinbarten_spalten():
    assert "create table if not exists __SCHEMA__.vorgaenge (" in SQL
    koerper = re.search(r"create table if not exists __SCHEMA__\.vorgaenge \((.*?)\n\);", SQL, re.S)
    assert koerper
    for spalte in SPALTEN:
        assert re.search(rf"^\s+{spalte}\s", koerper.group(1), re.M), spalte
    assert "id           uuid primary key default gen_random_uuid()" in SQL
    assert re.search(r"user_id\s+uuid not null references auth\.users\(id\) on delete cascade", SQL)
    assert re.search(r"session_id\s+uuid references __SCHEMA__\.sessions\(id\) on delete set null", SQL)
    assert re.search(r"daten\s+jsonb not null", SQL) and re.search(r"gestartet_at\s+timestamptz not null default now\(\)", SQL)
    assert re.search(r"beendet_at\s+timestamptz\b", SQL)


def test_erlaubte_werte_entsprechen_dem_kern():
    assert _werte("art") == set(vg.ARTEN)
    assert _werte("status") == set(vg.STATUS)
    code = _ohne_kommentare(SQL)
    assert "gesamt >= 0 and gesamt <= 1" in code and "jsonb_typeof(daten) = 'object'" in code
    assert "beendet_at is null or beendet_at >= gestartet_at" in code


def test_rls_policy_core_all_mit_drop_davor():
    code = _ohne_kommentare(SQL)
    assert "alter table __SCHEMA__.vorgaenge enable row level security" in code
    drop = code.index("drop policy if exists core_all on __SCHEMA__.vorgaenge")
    anlegen = code.index("create policy core_all on __SCHEMA__.vorgaenge for all to wamo_core using (true) with check (true)")
    assert drop < anlegen  # ein zweiter Lauf bricht nicht ab
    assert len(re.findall(r"create policy", code, re.I)) == 1


def test_rechte_nur_der_kern_nur_lesen_einfuegen_und_gezielte_updates_kein_delete():
    code = _ohne_kommentare(SQL)
    grants = _anweisungen(code, "grant")
    assert grants, "keine Rechte vergeben"
    for g in grants:
        assert g.rstrip().endswith("to wamo_core"), g
        assert not any(re.search(rf"\b{f}\b", g, re.I) for f in FREMDE), g
        assert not re.search(r"\bdelete\b|\ball\b|\btruncate\b|\breferences\b|\btrigger\b", g, re.I), g
    tabellenrechte = [g for g in grants if re.match(r"grant select, insert on __SCHEMA__\.vorgaenge to wamo_core$", g)]
    assert len(tabellenrechte) == 1
    update = [g for g in grants if g.startswith("grant update (")]
    assert len(update) == 1
    spalten = set(re.findall(r"\b[a-z_]+\b", update[0].split("(", 1)[1].split(")", 1)[0]))
    assert spalten == {"session_id", "titel", "status", "gesamt", "daten", "beendet_at"}  # user_id, art, id, Beginn bleiben
    revoke = " ".join(_anweisungen(code, "revoke"))
    assert "revoke all on __SCHEMA__.vorgaenge from" in revoke
    for rolle in FREMDE:
        assert rolle in revoke
    assert code.index("revoke all on __SCHEMA__.vorgaenge") < code.index("grant select, insert on __SCHEMA__.vorgaenge")


def test_indizes_fuer_die_fremdschluessel_session_partiell():
    code = _ohne_kommentare(SQL)
    assert re.search(r"create index if not exists vorgaenge_session_idx on __SCHEMA__\.vorgaenge \(session_id\) "
                     r"where session_id is not null", code)
    assert re.search(r"create index if not exists vorgaenge_user_idx on __SCHEMA__\.vorgaenge \(user_id, gestartet_at desc\)", code)


def test_voraussetzung_selbsttest_und_eintrag():
    code = _ohne_kommentare(SQL)
    assert "set local lock_timeout" in code
    assert "version = '0001_wamo_init'" in code and "0017 braucht 0001" in code
    assert not re.search(r"^\s*(begin|commit)\s*;", code, re.I | re.M)  # läuft in EINER Transaktion von psql --single-transaction
    assert "has_table_privilege('wamo_core', '__SCHEMA__.vorgaenge', 'SELECT')" in code
    for rolle in ("anon", "authenticated", "service_role", "wamo_admin"):
        assert f"has_any_column_privilege('{rolle}', '__SCHEMA__.vorgaenge'" in code, rolle
    assert "relrowsecurity" in code and "policyname = 'core_all'" in code
    assert "vorgaenge_session_idx" in code
    assert "insert into __SCHEMA__.schema_migrations (version) values ('0017_vorgaenge') on conflict do nothing" in code


def test_rueckweg_gesperrt_in_prod_und_bei_folgemigrationen_und_wiederholbar():
    code = _ohne_kommentare(DOWN)
    assert "'__SCHEMA__' = 'wamo_prod'" in code and "wamo.allow_drop" in code
    assert "version > '0017_vorgaenge'" in code and "wamo.down_trotz_folgemigration" in code
    assert "drop table if exists __SCHEMA__.vorgaenge" in code
    assert "delete from __SCHEMA__.schema_migrations where version = '0017_vorgaenge'" in code
    assert "cascade" not in code.lower()


def test_sql_test_ist_eine_zurueckgerollte_transaktion_nie_in_prod_und_prueft_die_kernpunkte():
    code = _ohne_kommentare(TEST)
    assert code.index("begin;") < code.index("rollback;")
    assert "\\echo 0017 OK" in TEST
    assert "Test nie in wamo_prod" in code and "set role wamo_core" in code
    for rolle in ("anon", "authenticated", "service_role", "wamo_admin"):
        assert f"set role {rolle}" in code, rolle
    for punkt in ("delete from __SCHEMA__.vorgaenge", "truncate __SCHEMA__.vorgaenge", "on conflict (id) do update",
                  "update __SCHEMA__.vorgaenge set user_id", "update __SCHEMA__.vorgaenge set art",
                  "delete from __SCHEMA__.sessions", "delete from auth.users", "relrowsecurity"):
        assert punkt in code, punkt
