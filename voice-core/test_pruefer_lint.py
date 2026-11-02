"""Tests des Migrations-Lints (infra/sokrates2/release/pruefer/migration_lint.py, Schritt 2.5a, Konzept Abschnitt C).

Laufen überall ohne Netz, ohne Datenbank und ohne Werkzeuge. Geprüft werden die erlaubten und die verbotenen Fälle, die Dateiregeln (unveränderlich,
Nummer, Rückweg) und dass die jüngeren Migrationen des Repos selbst durchgehen (das Lint soll die gelebte Praxis nicht verbieten).

Ausführen:  uv run python -m pytest -q test_pruefer_lint.py   (in voice-core/)
"""

import sys
import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
PRUEFER = WURZEL / "infra" / "sokrates2" / "release" / "pruefer"
sys.path.insert(0, str(PRUEFER))

import migration_lint as ML  # noqa: E402
import pruefer_scans as S  # noqa: E402
from pruefer_scans import Datei  # noqa: E402

KOPF = "set local lock_timeout = '5s';\n"
GUT = KOPF + """
create table if not exists __SCHEMA__.beispiel (
  id uuid primary key default gen_random_uuid(),
  profil_id uuid not null references __SCHEMA__.profiles(user_id) on delete restrict,
  name text not null
);
create index if not exists beispiel_profil_idx on __SCHEMA__.beispiel (profil_id);
alter table __SCHEMA__.beispiel enable row level security;
drop policy if exists core_all on __SCHEMA__.beispiel;
create policy core_all on __SCHEMA__.beispiel for all to wamo_core using (true) with check (true);
revoke all on __SCHEMA__.beispiel from public, anon, authenticated, service_role;
grant select, insert on __SCHEMA__.beispiel to wamo_core;
"""


def arten(sql: str, down: bool = False) -> set[str]:
    b = S.Befund(ML.NAME)
    ML.pruefe_sql("supabase/migrations/0017_x" + (".down" if down else "") + ".sql", sql, down, b)
    return {t.art for t in b.treffer}


def lint_dateien(dateien, baum=()):
    return ML.migration_lint(dateien, tuple(baum))


class Bereinigung(unittest.TestCase):
    def test_kommentare_und_zeichenketten_werden_leer_zeilen_bleiben(self):
        roh = "select 1; -- drop table x\n/* drop column\n y */ select 'drop table z', E'a\\'b drop' ;\nselect 2;"
        t = ML.bereinige(roh)
        self.assertNotIn("drop", t)
        self.assertEqual(t.count("\n"), roh.count("\n"))
        self.assertIn("select 2;", t)

    def test_dollar_koerper_bleiben_code(self):
        self.assertIn("delete from", ML.bereinige("do $$ begin delete from x; end $$;"))


class Erlaubt(unittest.TestCase):
    def test_sauberes_muster_hat_keinen_treffer(self):
        self.assertEqual(arten(GUT), set())

    def test_delete_mit_where_und_update_sind_erlaubt(self):
        self.assertEqual(arten(KOPF + "delete from __SCHEMA__.t where id = 1;\nupdate __SCHEMA__.t set a = 1;\n"), set())

    def test_revoke_auf_eigene_neue_objekte_und_neue_spalten(self):
        self.assertEqual(arten(KOPF + "create or replace function __SCHEMA__.f(a int, b text) returns int language sql as $$ select 1 $$;\n"
                               "revoke all on function __SCHEMA__.f(int, text) from public;\n"), set())
        self.assertEqual(arten(KOPF + "alter table __SCHEMA__.t add column if not exists geheim text;\n"
                               "revoke all (geheim) on __SCHEMA__.t from wamo_core;\n"), set())

    def test_truncate_in_rechteliste_und_ausloeser_ist_keine_anweisung(self):
        sql = (KOPF + "drop trigger if exists nicht_leeren on __SCHEMA__.t;\n"
               "create trigger nicht_leeren before truncate on __SCHEMA__.t for each statement execute function __SCHEMA__.f();\n"
               "grant select, truncate on __SCHEMA__.t to wamo_core;\n")
        self.assertNotIn("contract_truncate", arten(sql))

    def test_not_null_mit_vorgabe_und_nachfuellung(self):
        sql = (KOPF + "alter table __SCHEMA__.t add column if not exists a int;\nalter table __SCHEMA__.t alter column a set default 0;\n"
               "update __SCHEMA__.t set a = 0 where a is null;\nalter table __SCHEMA__.t alter column a set not null;\n")
        self.assertNotIn("contract_not_null_ohne_vorgabe", arten(sql))
        self.assertEqual(arten(KOPF + "alter table __SCHEMA__.t add column if not exists b int not null default 0;\n"), set())

    def test_schleife_mit_execute_format_zaehlt_als_rls_und_policy(self):
        sql = (KOPF + "create table if not exists __SCHEMA__.a_tab (id uuid primary key);\n"
               "do $$ declare t text; begin foreach t in array array['a_tab'] loop\n"
               "  execute format('alter table __SCHEMA__.%I enable row level security', t);\n"
               "  execute format('create policy core_all on __SCHEMA__.%I for all to wamo_core using (true)', t);\n end loop; end $$;\n")
        self.assertNotIn("pflicht_rls_fehlt", arten(sql))
        self.assertNotIn("pflicht_core_all_fehlt", arten(sql))

    def test_down_datei_darf_aufraeumen(self):
        sql = "drop table if exists __SCHEMA__.beispiel;\nrevoke all on __SCHEMA__.alt from public;\ntruncate __SCHEMA__.x;\ndelete from __SCHEMA__.y;\n"
        self.assertEqual(arten(sql, down=True), set())
        self.assertEqual(arten("drop table wamo_prod.x;", down=True), {"pflicht_festes_schema"})


class Verboten(unittest.TestCase):
    FAELLE = {
        "contract_drop_table": "drop table __SCHEMA__.t;",
        "contract_drop_column": "alter table __SCHEMA__.t drop column a;",
        "contract_drop_schema": "drop schema __SCHEMA__ cascade;",
        "contract_drop_function": "drop function __SCHEMA__.f();",
        "contract_rename": "alter table __SCHEMA__.t rename to u;",
        "contract_typaenderung": "alter table __SCHEMA__.t alter column a type bigint;",
        "contract_not_null_ohne_vorgabe": "alter table __SCHEMA__.t alter column a set not null;",
        "contract_truncate": "truncate __SCHEMA__.t;",
        "contract_delete_ohne_where": "delete from __SCHEMA__.t;",
        "contract_revoke": "revoke insert on __SCHEMA__.t from wamo_core;",
        "pflicht_grant_fremdrolle": "grant select on __SCHEMA__.t to anon;",
        "pflicht_rolle_aendern": "create role boese login;",
        "pflicht_rls_deaktiviert": "alter table __SCHEMA__.t disable row level security;",
        "pflicht_festes_schema": "select 1 from wamo_prod.t;",
        "pflicht_definer_ohne_search_path": "create or replace function __SCHEMA__.f() returns int language sql security definer as $$ select 1 $$;",
        "wiederholung_drop_ohne_if_exists": "drop policy x on __SCHEMA__.t;",
        "wiederholung_create_ohne_if_not_exists": "create index t_idx on __SCHEMA__.t (a);",
        "wiederholung_create_ohne_replace": "create function __SCHEMA__.f() returns int language sql as $$ select 1 $$;",
        "wiederholung_spalte_ohne_if_not_exists": "alter table __SCHEMA__.t add column a int;",
        "wiederholung_policy_ohne_drop": "create policy p on __SCHEMA__.t for select to wamo_core using (true);",
        "wiederholung_trigger_ohne_drop": "create trigger tr before update on __SCHEMA__.t for each row execute function __SCHEMA__.f();",
        "wiederholung_constraint_ohne_drop": "alter table __SCHEMA__.t add constraint c check (a > 0);",
    }

    def test_jeder_verbotene_fall_wird_gemeldet(self):
        for art, sql in self.FAELLE.items():
            with self.subTest(art=art):
                self.assertIn(art, arten(KOPF + sql + "\n"))

    def test_add_column_not_null_ohne_vorgabe(self):
        self.assertIn("contract_not_null_ohne_vorgabe", arten(KOPF + "alter table __SCHEMA__.t add column if not exists a int not null;\n"))

    def test_lock_timeout_fehlt(self):
        self.assertIn("pflicht_lock_timeout_fehlt", arten("select 1;"))

    def test_neue_tabelle_braucht_rls_policy_und_fk_index(self):
        sql = KOPF + "create table if not exists __SCHEMA__.t (id uuid primary key, p uuid references __SCHEMA__.profiles(user_id));\n"
        a = arten(sql)
        self.assertTrue({"pflicht_rls_fehlt", "pflicht_core_all_fehlt", "pflicht_fk_ohne_index"} <= a, a)
        self.assertNotIn("pflicht_fk_ohne_index", arten(sql + "create index if not exists t_p_idx on __SCHEMA__.t (p);\n"))
        # Fremdschlüssel als erste Spalte des Primärschlüssels braucht keinen eigenen Index
        pk = KOPF + "create table if not exists __SCHEMA__.t (p uuid references __SCHEMA__.profiles(user_id), n int, primary key (p, n));\n"
        self.assertNotIn("pflicht_fk_ohne_index", arten(pk))

    def test_core_all_muss_wamo_core_meinen(self):
        self.assertIn("pflicht_core_all_fehlt", arten(GUT.replace("to wamo_core using", "to postgres using")))

    def test_fremdschluessel_einer_neuen_spalte_braucht_index(self):
        sql = KOPF + "alter table __SCHEMA__.t add column if not exists p uuid references __SCHEMA__.profiles(user_id);\n"
        self.assertIn("pflicht_fk_ohne_index", arten(sql))

    def test_text_in_kommentar_und_zeichenkette_zaehlt_nicht(self):
        self.assertEqual(arten(KOPF + "-- drop table x; truncate y;\nselect 'drop table x', 'delete from y';\n"), set())

    def test_treffer_nennen_nie_den_gefundenen_text(self):
        b = S.Befund(ML.NAME)
        ML.pruefe_sql("supabase/migrations/0017_x.sql", KOPF + "grant select on __SCHEMA__.t to anon; -- GEHEIM-HINWEIS\n", False, b)
        self.assertGreater(b.treffer_anzahl, 0)
        for t in b.treffer:
            self.assertNotIn("GEHEIM", f"{t.pfad} {t.art} {t.detail}")
            self.assertRegex(t.detail, r"^[a-z0-9_]{0,40}$")


class Dateiregeln(unittest.TestCase):
    ALT = ["supabase/migrations/0016_a.sql", "supabase/migrations/0016_a.down.sql"]

    def neu(self, nr="0017_b", sql=GUT, down="select 1;\n"):
        d = [Datei(f"supabase/migrations/{nr}.sql", sql.encode())]
        baum = list(self.ALT) + [d[0].pfad]
        if down is not None:
            d.append(Datei(f"supabase/migrations/{nr}.down.sql", down.encode()))
            baum.append(d[1].pfad)
        return d, baum

    def test_gute_neue_migration_besteht(self):
        d, baum = self.neu()
        b = lint_dateien(d, baum)
        self.assertEqual((b.treffer_anzahl, b.zeile), (0, "ok"), [t.art for t in b.treffer])

    def test_ohne_migrationen_nichts_zu_tun(self):
        b = lint_dateien([Datei("ui/x.ts", b"a")], ["ui/x.ts"])
        self.assertEqual((b.treffer_anzahl, b.zeile), (0, "ok"))

    def test_vorhandene_migration_ist_unveraenderlich(self):
        geaendert = [Datei(self.ALT[0], b"neu", b"alt")]
        self.assertEqual([t.art for t in lint_dateien(geaendert, self.ALT).treffer], ["migration_veraendert"])
        geloescht = [Datei(self.ALT[1], None, b"alt")]
        self.assertEqual([t.art for t in lint_dateien(geloescht, self.ALT[:1]).treffer], ["migration_veraendert"])

    def test_nummer_muss_hoeher_und_eindeutig_sein(self):
        d, baum = self.neu(nr="0015_zu_alt")
        self.assertIn("nummer_nicht_hoeher", [t.art for t in lint_dateien(d, baum).treffer])
        d1, b1 = self.neu(nr="0017_a")
        d2, b2 = self.neu(nr="0017_b")
        self.assertIn("nummer_doppelt", [t.art for t in lint_dateien(d1 + d2, b1 + b2).treffer])

    def test_down_datei_ist_pflicht_und_umgekehrt(self):
        d, baum = self.neu(down=None)
        self.assertIn("down_fehlt", [t.art for t in lint_dateien(d, baum).treffer])
        nur_down = [Datei("supabase/migrations/0017_b.down.sql", b"select 1;")]
        self.assertIn("down_ohne_migration", [t.art for t in lint_dateien(nur_down, self.ALT + [nur_down[0].pfad]).treffer])

    def test_ungueltiger_name(self):
        d = [Datei("supabase/migrations/neu.sql", b"select 1;"), Datei("supabase/migrations/readme.md", b"x")]
        self.assertEqual([t.art for t in lint_dateien(d, self.ALT).treffer], ["name_ungueltig", "name_ungueltig"])

    def test_alte_dateien_im_baum_zaehlen_bei_der_nummer(self):
        d, baum = self.neu(nr="0014_nachtrag")
        self.assertIn("nummer_nicht_hoeher", [t.art for t in lint_dateien(d, baum).treffer])


class Bestand(unittest.TestCase):
    """Das Lint soll die gelebte Praxis nicht verbieten: die jüngeren Migrationen des Repos gehen sauber durch."""

    @staticmethod
    def befund(p: Path) -> set[str]:
        b = S.Befund(ML.NAME)
        ML.pruefe_sql(f"supabase/migrations/{p.name}", p.read_text(encoding="utf-8"), p.name.endswith(".down.sql"), b)
        return {t.art for t in b.treffer}

    def test_juengere_migrationen_ohne_besondere_eingriffe_sind_sauber(self):
        ordner = WURZEL / "supabase" / "migrations"
        for name in ("0011_modellmodus_und_codetoken", "0012_kontingent", "0015_rd_sitzungen", "0016_release_freigaben_lesen"):
            with self.subTest(datei=name):
                self.assertEqual(self.befund(ordner / f"{name}.sql"), set())

    def test_rueckwege_sind_nie_ein_treffer_ausser_beim_festen_schema(self):
        for p in sorted((WURZEL / "supabase" / "migrations").glob("*.down.sql")):
            with self.subTest(datei=p.name):
                self.assertEqual(self.befund(p), set())

    def test_bewusste_eingriffe_werden_gemeldet(self):
        ordner = WURZEL / "supabase" / "migrations"
        self.assertEqual(self.befund(ordner / "0013_release_freigaben.sql"), {"pflicht_rolle_aendern"})   # legt Rollen an
        self.assertEqual(self.befund(ordner / "0014_geraete_verwaltung.sql"), {"contract_revoke"})        # nimmt bestehende Rechte zurück

    def test_keine_gedankenstriche_im_modul(self):
        text = (PRUEFER / "migration_lint.py").read_text(encoding="utf-8")
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)


if __name__ == "__main__":
    unittest.main()
