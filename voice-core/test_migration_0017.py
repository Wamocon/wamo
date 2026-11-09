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
    assert re.search(r"session_id\s+uuid references __SCHEMA__\.sessions\(id\) on delete set null", SQL)
    assert re.search(r"daten\s+jsonb not null", SQL) and re.search(r"gestartet_at\s+timestamptz not null default now\(\)", SQL)
    assert re.search(r"beendet_at\s+timestamptz\b", SQL)


def test_fremdschluessel_user_id_zeigt_auf_profiles_nicht_auf_auth_users():
    """Wie 0003, 0010, 0013 und 0015: ein Fremdschlüssel auf auth.users würde die Tabelle der Anmeldung sperren und GoTrue stauen."""
    code = _ohne_kommentare(SQL)
    assert re.search(r"user_id\s+uuid not null references __SCHEMA__\.profiles\(user_id\) on delete cascade", code)
    assert "auth.users" not in code
    kopf = SQL.split("set local lock_timeout", 1)[0]
    assert "profiles" in kopf and "0001" in kopf  # die Abhängigkeit steht im Kopfkommentar


def test_erlaubte_werte_entsprechen_dem_kern():
    assert _werte("art") == set(vg.ARTEN)
    assert _werte("status") == set(vg.STATUS)
    code = _ohne_kommentare(SQL)
    assert "gesamt >= 0 and gesamt <= 1" in code and "jsonb_typeof(daten) = 'object'" in code
    assert "beendet_at is null or beendet_at >= gestartet_at" in code


def test_ende_und_endstatus_gehoeren_zusammen_und_entsprechen_dem_kern():
    code = _ohne_kommentare(SQL)
    treffer = re.search(r"constraint vorgaenge_ende_chk check \(\(status in \((.*?)\)\) = \(beendet_at is not null\)\)", code)
    assert treffer, "Prüfung vorgaenge_ende_chk fehlt"
    assert set(re.findall(r"'([a-z_]+)'", treffer.group(1))) == set(vg.ENDSTATUS)
    assert vg.ENDSTATUS < set(vg.STATUS)


def test_obergrenze_des_standes_passt_zur_pruefung_der_datenbank():
    grenze = int(re.search(r"pg_column_size\(daten\) <= (\d+)", _ohne_kommentare(SQL)).group(1))
    assert grenze == 262144
    assert vg.DATEN_MAX_ZEICHEN * 4 < grenze  # UTF-8 braucht bis zu 4 Byte je Zeichen


def test_aufbewahrung_steht_im_kopfkommentar_und_stimmt_mit_dem_kern_ueberein():
    kopf = "\n".join(z for z in SQL.splitlines() if z.startswith("--"))
    assert f"{vg.AUFBEWAHRUNG_TAGE} Tage" in kopf and f"{vg.OFFEN_MAX_STUNDEN} Stunden" in kopf
    assert "aufraeumen" in kopf and "Kern" in kopf
    assert re.search(r"[Ll]öschen von Zeilen ist bewusst nicht erlaubt", kopf)


def test_aufraeumen_setzt_nur_spalten_die_der_kern_aendern_darf_und_die_tabelle_erlaubt_es():
    erlaubt = set(re.findall(r"\b[a-z_]+\b", re.search(r"grant update \(([^)]*)\)", _ohne_kommentare(SQL)).group(1)))
    for sql in (vg._AUFRAEUMEN_OFFEN, vg._AUFRAEUMEN_STAND):
        text = sql.format(t="__SCHEMA__.vorgaenge")
        assert text.startswith("update __SCHEMA__.vorgaenge set ")
        zuweisungen = re.search(r"\bset (.*?) where\b", text).group(1)
        spalten = set(re.findall(r"(?:^|, )([a-z_]+) = ", zuweisungen))
        assert spalten and spalten <= erlaubt, (spalten, erlaubt)
        assert not re.search(r"\b(delete|truncate)\b", text)
    # der Endstatus aus dem Aufräumen erfüllt die Prüfung vorgaenge_ende_chk (Status und Ende zusammen) und vorgaenge_zeit_chk
    assert "status = 'abgebrochen', beendet_at = now()" in vg._AUFRAEUMEN_OFFEN
    assert "gestartet_at < now()" in vg._AUFRAEUMEN_OFFEN


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
    for rolle in (*FREMDE, "wamo_core"):  # auch wamo_core zuerst entziehen, dann neu vergeben: ein Nachlauf konvergiert
        assert re.search(rf"\b{rolle}\b", revoke), rolle
    assert len(_anweisungen(code, "revoke")) == 1
    assert code.index("revoke all on __SCHEMA__.vorgaenge") < code.index("grant select, insert on __SCHEMA__.vorgaenge")
    assert code.index("revoke all on __SCHEMA__.vorgaenge") < code.index("grant update (")


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


def test_sql_test_legt_profile_an_prueft_grenze_ende_fremdschluessel_und_aufraeumen():
    code = _ohne_kommentare(TEST)
    assert re.search(r"insert into __SCHEMA__\.profiles \(user_id, display_name\)", code)  # der neue Fremdschlüssel braucht sie
    assert code.index("insert into auth.users") < code.index("insert into __SCHEMA__.profiles") \
        < code.index("insert into __SCHEMA__.vorgaenge")
    assert "repeat('a', 300000)" in code and "'23514'" in code  # Stand über 262144 Byte
    assert "repeat(chr(128512), 60000)" in code  # 60000 Zeichen zu 4 Byte passen noch
    assert "confrelid = '__SCHEMA__.profiles'::regclass" in code and "confrelid = 'auth.users'::regclass" in code
    assert "delete from __SCHEMA__.profiles" in code  # ein gelöschtes Profil nimmt seine Vorgänge mit
    assert "'fertig')$s$, '23514'" in code and "set beendet_at = null" in code  # Endstatus ohne Ende und Ende ohne Endstatus
    assert "has_column_privilege('wamo_core'" in code and "6::bigint" in code  # genau sechs änderbare Spalten
    assert "zweimal anwenden" in TEST.split("\\set ON_ERROR_STOP", 1)[0]  # der Nachlauf der Migration: Hinweis im Kopfkommentar


def _eine_zeile(text: str) -> str:
    return " ".join(text.split())


def test_sql_test_enthaelt_den_upsert_und_die_aufraeumanweisungen_des_kerns_woertlich():
    """Der Test prüft, was der Kern wirklich schickt: ändert sich vorgaenge.py, muss der SQL-Test mit."""
    sql_test = _eine_zeile(_ohne_kommentare(TEST))
    ziel = "__SCHEMA__.vorgaenge"
    ende = _eine_zeile(vg._UPSERT.split("on conflict", 1)[1].format(t=ziel))
    assert "on conflict" + " " + ende in sql_test
    kopf = _eine_zeile(vg._UPSERT.split(" values ", 1)[0].format(t=ziel))
    assert kopf in sql_test  # gleiche Spaltenliste
    assert "to_timestamp(" in sql_test and "coalesce(" in sql_test
    for anweisung in (vg._AUFRAEUMEN_OFFEN, vg._AUFRAEUMEN_STAND):
        assert _eine_zeile(anweisung.format(t=ziel)) in sql_test
