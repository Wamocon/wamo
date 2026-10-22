"""Prüft Migration 0013 (Freigaben und Rollen der Selbstüberarbeitung) als Text: Regeln aus CLAUDE.md für Migrationen,
Rechte, Trigger und die Regeln der Freigabe aus docs/selbstueberarbeitung.md (Abschnitte C, E, F). Die Wirkung in der
Datenbank prüft supabase/tests/0013_release_test.sql gegen wamo_test oder wamo_dev."""

from __future__ import annotations

import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
SUPABASE = WURZEL / "supabase"
SQL = (SUPABASE / "migrations" / "0013_release_freigaben.sql").read_text(encoding="utf-8")
DOWN = (SUPABASE / "migrations" / "0013_release_freigaben.down.sql").read_text(encoding="utf-8")
TEST = (SUPABASE / "tests" / "0013_release_test.sql").read_text(encoding="utf-8")
KONZEPT = (WURZEL / "docs" / "selbstueberarbeitung.md").read_text(encoding="utf-8")
TABELLEN = ("release_anfragen", "release_freigaben", "release_ereignisse", "release_stand")
FREMDE = ("anon", "authenticated", "service_role", "public", "wamo_admin")


def _ohne_kommentare(text: str) -> str:
    return "\n".join(z.split("--", 1)[0] for z in text.splitlines())


def _anweisungen(text: str, wort: str) -> list[str]:
    return [a.strip() for a in re.split(r";\s*\n", _ohne_kommentare(text)) if re.match(rf"\s*{wort}\b", a, re.I)]


def _tabelle(name: str) -> str:
    treffer = re.search(rf"create table if not exists __SCHEMA__\.{name} \((.*?)\n\);", SQL, re.S)
    assert treffer, name
    return treffer.group(1)


def _funktion(name: str) -> str:
    treffer = re.search(rf"create or replace function __SCHEMA__\.{name}\(.*?\n(?:end \$\$;|\$\$;)", SQL, re.S)
    assert treffer, name
    return treffer.group(0)


def test_schema_platzhalter_und_keine_festen_schemas():
    for text in (SQL, DOWN, TEST):
        code = _ohne_kommentare(text)
        assert "__SCHEMA__" in code
        assert not re.search(r"\bwamo_(dev|test|prod)\.", code)


def test_keine_gedankenstriche_und_nur_echte_umlaute_in_den_kommentaren():
    for text in (SQL, DOWN, TEST):
        assert "–" not in text and "—" not in text
        kommentare = [z for z in text.splitlines() if z.lstrip().startswith("--")]
        for zeile in kommentare:
            assert " - " not in zeile, zeile
        assert not re.search(r"\b(fuer|ueber|Pruefung|Rueckweg|koennen|waehrend|aendern|Aenderung|loeschen)\b",
                             " ".join(kommentare)), "Ersatzschreibweise für Umlaute im Kommentar"


def test_vier_tabellen_idempotent_mit_den_spalten_aus_dem_konzept():
    for name in TABELLEN:
        assert f"create table if not exists __SCHEMA__.{name} (" in SQL
    erwartet = {
        "release_anfragen": ("id", "angefordert_von", "art", "quelle", "status", "basis_commit", "c1_commit", "r_commit",
                             "v_commit", "tree_id", "bump", "version_vorschlag", "version_final", "schutz_treffer",
                             "pruefstand", "wartet_bis", "erstellt_at", "aktualisiert_at"),
        "release_freigaben": ("id", "anfrage_id", "freigeber", "commit_id", "tree_id", "aal2_zeit", "quell_ip", "erstellt_at",
                              "widerrufen_at"),
        "release_ereignisse": ("id", "anfrage_id", "zeit", "akteur", "ereignis", "daten", "vorher_hash", "hash"),
        "release_stand": ("komponente", "aktive_version", "aktiver_commit", "farbe_oder_slot", "seit",
                          "letzte_gute_version"),
    }
    for name, spalten in erwartet.items():
        koerper = _tabelle(name)
        for spalte in spalten:
            assert re.search(rf"^\s+{spalte}\s", koerper, re.M), (name, spalte)


def test_status_und_arten_entsprechen_dem_konzept():
    zustaende = re.search(r"Zustände: ([a-z0-9_, \n]+)\.", KONZEPT)
    assert zustaende
    soll = {z.strip() for z in zustaende.group(1).replace("\n", " ").split(",") if z.strip()}
    check = re.search(r"status\s+text not null default 'angefragt' check \(status in \((.*?)\)\)", SQL, re.S)
    assert check
    assert set(re.findall(r"'([a-z0-9_]+)'", check.group(1))) == soll
    assert len(soll) == 17
    anfragen = _tabelle("release_anfragen")
    assert "check (art in ('normal', 'sonder'))" in anfragen and "check (quelle in ('selbst', 'main'))" in anfragen
    assert "bump in ('patch', 'minor', 'major')" in anfragen
    assert "komponente in ('ui', 'kern')" in _tabelle("release_stand")


def test_commit_und_tree_nur_als_hex_und_freigabe_nur_mit_beiden():
    freigaben = _tabelle("release_freigaben")
    assert re.search(r"commit_id\s+text not null check \(commit_id ~ '\^\(\[0-9a-f\]\{40\}\|\[0-9a-f\]\{64\}\)\$'\)", freigaben)
    assert re.search(r"tree_id\s+text not null check \(tree_id ~ ", freigaben)
    assert "aal2_zeit     timestamptz not null" in freigaben
    guard = _funktion("release_freigaben_guard")
    assert "a.v_commit is null or a.tree_id is null" in guard
    assert "new.commit_id <> a.v_commit or new.tree_id <> a.tree_id" in guard


def test_version_nie_unter_den_vorschlag_und_nur_x_y_z():
    anfragen = _tabelle("release_anfragen")
    assert "release_anfragen_version_nicht_gesenkt" in anfragen
    assert "string_to_array(version_final, '.')::int[] >= string_to_array(version_vorschlag, '.')::int[]" in anfragen
    # zwei Spaltenchecks und die beiden Prüfungen im CASE (ein kaputter Wert gibt 23514 statt eines Umwandlungsfehlers)
    assert anfragen.count(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$") == 4
    assert "case when version_final ~" in anfragen and "else false end" in anfragen
    # der Vorschlag lässt sich nur erhöhen, und eine Version gibt es höchstens einmal
    guard = _funktion("release_anfragen_guard")
    assert "der Versionsvorschlag lässt sich nur erhöhen" in guard
    assert re.search(r"create unique index if not exists release_anfragen_version_eindeutig_idx\s+on __SCHEMA__\.release_anfragen "
                     r"\(version_final\)\s+where version_final is not null and status <> 'abgebrochen'", SQL)


def test_art_und_treffer_klassifiziert_nur_der_anker_und_die_wartezeit_setzt_die_datenbank():
    code = _ohne_kommentare(SQL)
    assert re.search(r"grant insert \(angefordert_von, quelle\) on __SCHEMA__\.release_anfragen to wamo_release;", code)
    release_update = re.search(r"grant update \(([^)]*)\) on __SCHEMA__\.release_anfragen to wamo_release;", code)
    anker_update = re.search(r"grant update \(([^)]*)\) on __SCHEMA__\.release_anfragen to wamo_anker;", code)
    assert release_update and anker_update
    for spalte in ("art", "schutz_treffer", "wartet_bis", "angefordert_von", "quelle"):
        assert spalte not in {s.strip() for s in release_update.group(1).split(",")}, spalte
    assert {s.strip() for s in anker_update.group(1).split(",")} == {"status", "art", "schutz_treffer"}
    assert "wartet_bis" not in anker_update.group(1)
    guard = _funktion("release_anfragen_guard")
    assert "if als_release and (new.art, new.schutz_treffer) is distinct from (old.art, old.schutz_treffer)" in guard
    assert "klassifiziert nur der Anker" in guard
    assert "new.schutz_treffer is null" in guard and "noch nicht vom Anker klassifiziert" in guard
    assert "jsonb_array_length(new.schutz_treffer) > 0 and new.art <> 'sonder'" in guard
    assert "new.wartet_bis := clock_timestamp() + interval '15 minutes'" in guard
    # die Invarianten gelten auch als CHECK, falls ein Trigger fehlt
    tabelle = _tabelle("release_anfragen")
    assert "release_anfragen_ab_freigabe_vollstaendig" in tabelle
    assert "schutz_treffer is not null and (art <> 'sonder' or wartet_bis is not null)" in tabelle


def test_freigegeben_prueft_die_freigabe_erneut_und_ein_widerruf_wirkt_danach():
    guard = _funktion("release_anfragen_guard")
    freigegeben = guard.split("if new.status = 'freigegeben'")[1].split("if new.status in ('veroeffentlicht'")[0]
    assert "f.widerrufen_at is null" in freigegeben and "for share" in freigegeben
    assert "interval '24 hours'" in freigegeben and "interval '60 minutes'" in freigegeben          # Verfall (Konzept E.2)
    assert "__SCHEMA__.release_ist_admin(f.freigeber)" in freigegeben
    assert "f.freigeber <> new.angefordert_von" in freigegeben and "release_anzahl_admins() >= 2" in freigegeben
    danach = guard.split("if new.status in ('veroeffentlicht'")[1]
    assert "'bereitgestellt', 'umgeschaltet')" in danach and "nicht widerrufenen Freigabe" in danach
    # Rückweg und Abbruch bleiben frei: sie stehen nicht in dieser Liste
    assert "zurueckgenommen" not in danach.split("raise exception")[0]


def test_freigabe_regeln_stehen_in_der_datenbank():
    guard = _funktion("release_freigaben_guard")
    assert "a.status <> 'wartet_freigabe'" in guard
    assert "not __SCHEMA__.release_ist_admin(new.freigeber)" in guard                       # Freigeber ist aktiver Admin
    assert "interval '60 seconds'" in guard and "interval '5 seconds'" in guard             # frischer zweiter Faktor
    sonder = guard.split("if a.art = 'sonder' then")[1]
    assert "new.freigeber = a.angefordert_von" in sonder                                    # nie selbst
    assert "not __SCHEMA__.release_ist_admin(a.angefordert_von)" in sonder                  # beide aktive Admins
    assert "__SCHEMA__.release_anzahl_admins() < 2" in sonder and "Braucht einen zweiten Admin" in sonder  # fail closed
    assert "clock_timestamp() < a.wartet_bis" in sonder and "a.wartet_bis is null" in sonder
    assert "for share" in guard                                                              # Anfrage gesperrt
    assert "release_freigaben_eine_je_person unique (anfrage_id, freigeber)" in SQL
    # eine Freigabe lässt sich nur widerrufen
    assert "old.widerrufen_at is not null and new.widerrufen_at is distinct from old.widerrufen_at" in guard


def test_beauftragen_nur_ein_aktiver_admin_und_ein_offener_release():
    guard = _funktion("release_anfragen_guard")
    einfuegen = guard.split("if tg_op = 'INSERT' then")[1].split("return new;")[0]
    assert "new.status <> 'angefragt'" in einfuegen and "not __SCHEMA__.release_ist_admin(new.angefordert_von)" in einfuegen
    assert re.search(r"create unique index if not exists release_anfragen_ein_offener_idx\s+on __SCHEMA__\.release_anfragen \(\(true\)\)"
                     r"\s+where status not in \('bewaehrt', 'zurueckgenommen', 'abgebrochen'\)", SQL)


def test_statusweg_art_und_einfrieren():
    guard = _funktion("release_anfragen_guard")
    assert "old.art = 'sonder' and new.art <> 'sonder'" in guard                            # Art nur anheben
    assert "if von >= grenze" in guard and "new.v_commit, new.tree_id" in guard             # eingefroren ab wartet_freigabe
    assert "erlaubt := nach = von + 1" in guard                                              # ab der Freigabe lückenlos
    assert "erlaubt := nach > von" in guard                                                  # davor nur vorwärts
    assert "old.status in ('zurueckgenommen', 'abgebrochen')" in guard                      # abgeschlossen bleibt zu
    assert "als_release and new.status in ('freigegeben', 'bereitgestellt', 'umgeschaltet', 'bewaehrt', 'zurueckgenommen')" in guard
    assert "als_anker and (nach <= grenze)" in guard                                         # der Anker setzt nichts vor der Freigabe
    assert "new.status = 'freigegeben'" in guard and "f.widerrufen_at is null" in guard      # freigegeben nur mit gültiger Freigabe
    assert "f.commit_id = new.v_commit and f.tree_id = new.tree_id" in guard


def test_ereignisse_nur_anfuegen_und_kette_per_hash():
    tabelle = _tabelle("release_ereignisse")
    assert "release_ereignisse_hash_eindeutig unique (hash)" in tabelle
    assert "release_ereignisse_kette_linear unique (vorher_hash)" in tabelle
    assert "octet_length(daten::text) <= 4096" in tabelle
    kette = _funktion("release_ereignisse_kette")
    assert "pg_advisory_xact_lock" in kette and "new.vorher_hash := coalesce(letzter.hash, repeat('0', 64))" in kette
    assert "new.id := coalesce(letzter.id, 0) + 1" in kette and "new.zeit := clock_timestamp()" in kette
    assert "sha256(" in _funktion("release_ereignis_hash")
    assert "release_kette_pruefen() returns bigint" in SQL
    code = _ohne_kommentare(SQL)
    assert "create trigger release_ereignisse_nicht_aendern before update or delete on __SCHEMA__.release_ereignisse" in code
    assert "before truncate on __SCHEMA__.release_ereignisse" in code
    assert "before delete on __SCHEMA__.release_freigaben" in code and "before delete on __SCHEMA__.release_anfragen" in code


def test_pruefsumme_in_schema_migrations_einmal_gesetzt_unveraenderlich():
    code = _ohne_kommentare(SQL)
    assert "add column if not exists sha256 text" in code
    assert "drop constraint if exists schema_migrations_sha256_form" in code
    assert "sha256 ~ '^[0-9a-f]{64}$'" in code
    guard = _funktion("schema_migrations_guard")
    assert "old.sha256 is not null and new.sha256 is distinct from old.sha256" in guard
    assert "wamo_admin darf keine Prüfsumme eintragen" in guard                              # die Verwaltung trägt keine ein
    assert "drop trigger if exists schema_migrations_guard on __SCHEMA__.schema_migrations" in code
    assert "create trigger schema_migrations_guard before insert or update on __SCHEMA__.schema_migrations" in code


def test_jeder_trigger_wird_vorher_mit_drop_if_exists_entfernt():
    code = _ohne_kommentare(SQL)
    erzeugt = re.findall(r"create trigger (\w+) ", code)
    assert len(erzeugt) == 10
    for name in erzeugt:
        assert re.search(rf"drop trigger if exists {name} on ", code), name
    assert code.count("drop policy if exists core_all") == 1
    for policy in ("core_all", "release_all", "anker_all"):
        assert f"drop policy if exists {policy} on __SCHEMA__.%I" in code
        assert f"create policy {policy} on __SCHEMA__.%I" in code


def test_rechte_nur_das_noetige_und_nie_delete():
    code = _ohne_kommentare(SQL)
    for grant in _anweisungen(code, "grant"):
        assert not re.search(r"\b(delete|truncate|references|trigger)\b", grant, re.I), grant
        assert not re.search(r"\bto\b[^;]*\b(" + "|".join(FREMDE) + r")\b", grant, re.I), grant
        if re.match(r"grant (select|insert|update)\b", grant, re.I) and "execute" not in grant.lower():
            assert not re.match(r"grant (insert|update) on ", grant, re.I), grant       # nie Tabellenrecht, nur Spalten
    kern = [g for g in _anweisungen(code, "grant") if re.search(r"\bto wamo_core\b", g)]
    for g in kern:
        assert re.match(r"grant (select on|execute on function)", g), g                 # der Kern liest nur
    revoke = " ".join(_anweisungen(code, "revoke"))
    for rolle in ("public", "anon", "authenticated", "service_role", "wamo_core", "wamo_admin", "wamo_release", "wamo_anker"):
        assert rolle in revoke
    for tabelle in TABELLEN:
        assert tabelle in revoke


def test_rls_und_core_all_auf_jeder_tabelle():
    code = _ohne_kommentare(SQL)
    loop = re.search(r"foreach t in array array\[(.*?)\] loop(.*?)end loop;", code, re.S)
    assert loop
    assert set(re.findall(r"'(release_\w+)'", loop.group(1))) == set(TABELLEN)
    assert "alter table __SCHEMA__.%I enable row level security" in loop.group(2)
    assert "create policy core_all on __SCHEMA__.%I for all to wamo_core using (true) with check (true)" in loop.group(2)


def test_neue_rollen_ohne_login_und_ohne_erhoehte_rechte():
    code = _ohne_kommentare(SQL)
    for rolle in ("wamo_release", "wamo_anker"):
        assert f"create role {rolle} nologin nosuperuser nocreatedb nocreaterole nobypassrls" in code
        assert f"if not exists (select 1 from pg_roles where rolname = '{rolle}')" in code
        assert f"alter role {rolle} set statement_timeout" in code
    assert "password" not in code.lower()
    assert "drop role" not in _ohne_kommentare(DOWN).lower()                                # Rollen gelten je Cluster
    assert "security definer set search_path = ''" in code                                    # Hilfsfunktionen
    assert "set search_path = ''" in _funktion("release_anfragen_guard")


def test_selbsttest_prueft_jedes_recht_jeder_rolle_einzeln():
    code = _ohne_kommentare(SQL)
    assert "raise exception 'release (0013)" in code
    assert "array['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']" in code                      # nie mehrere Rechte in einem Aufruf
    assert "array['DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']" in code
    assert "'SELECT,INSERT,UPDATE'" not in code
    assert "array['wamo_core', 'wamo_release', 'wamo_anker', 'wamo_admin', 'anon', 'authenticated'," in code
    assert "has_column_privilege(r, '__SCHEMA__.' || t, s, recht) is distinct from erwartet" in code
    assert "x.grantee = 0" in code                                                            # nichts für PUBLIC
    assert "hat Rechte für eine unbekannte Rolle" in code                                     # und für keine fremde Rolle
    assert "eine Release-Funktion ist für PUBLIC ausführbar" in code and "p.proacl is null" in code
    assert "tgenabled = 'O'" in code and ") <> 10 then" in code                              # alle Trigger hängen
    assert "rolbypassrls or rolreplication" in code
    soll = re.search(r"soll constant jsonb := \$j\$(.*?)\$j\$::jsonb", code, re.S)
    assert soll
    for rolle in ('"wamo_core"', '"wamo_release"', '"wamo_anker"'):
        assert rolle in soll.group(1)
    for fremd in ('"wamo_admin"', '"anon"', '"authenticated"', '"service_role"'):
        assert fremd not in soll.group(1)                                                    # für sie ist nichts vorgesehen


def test_sperre_gegen_haengende_migration_und_eintrag():
    assert "set local lock_timeout" in _ohne_kommentare(SQL) and "set local lock_timeout" in _ohne_kommentare(DOWN)
    assert "to_regclass('__SCHEMA__.profiles') is null" in SQL
    assert "insert into __SCHEMA__.schema_migrations (version) values ('0013_release_freigaben') on conflict do nothing;" in SQL
    assert SQL.rstrip().endswith("on conflict do nothing;")


def test_down_prod_sperre_und_vollstaendig():
    code = _ohne_kommentare(DOWN)
    assert "'__SCHEMA__' = 'wamo_prod'" in code and "wamo.allow_drop" in code
    for tabelle in TABELLEN:
        assert f"drop table if exists __SCHEMA__.{tabelle};" in code
    # Tabellen mit Fremdschlüsseln zuerst: erst Stand, Ereignisse, Freigaben, dann Anfragen
    reihenfolge = [code.index(f"drop table if exists __SCHEMA__.{t};") for t in
                   ("release_stand", "release_ereignisse", "release_freigaben", "release_anfragen")]
    assert reihenfolge == sorted(reihenfolge)
    funktionen = set(re.findall(r"create or replace function __SCHEMA__\.(\w+)\(", SQL))
    for name in funktionen:
        assert re.search(rf"drop function if exists __SCHEMA__\.{name}\(", code), name
    assert "Down gesperrt: % offene Release-Anfragen" in code                              # nie bei einer offenen Anfrage
    assert "drop column if exists sha256" in code and "drop constraint if exists schema_migrations_sha256_form" in code
    assert "drop trigger if exists schema_migrations_guard" in code
    assert "delete from __SCHEMA__.schema_migrations where version = '0013_release_freigaben'" in code


def test_sql_test_laeuft_in_einer_zurueckgerollten_transaktion_und_nie_in_prod():
    assert "begin;" in TEST and re.search(r"^rollback;", TEST, re.M) and "Test nie in wamo_prod" in TEST
    assert "0013 OK" in TEST


def test_sql_test_deckt_jede_regel_ab():
    # Freigabe
    for muster in ("%Commit-ID oder Tree-ID%", "%kein aktiver Admin%", "%60 Sekunden%", "%Zukunft%", "%nicht selbst%",
                   "%Wartezeit%", "%Beauftragte%", "%nur der Anker%", "%eingefroren%",
                   "%nicht auf normal senken%", "%nur anfügen%", "%unveränderlich%", "%aktiver Admin%",
                   "%klassifiziert%", "%klassifiziert nur der Anker%", "%verlangen die Art sonder%", "%nur erhöhen%",
                   "%nicht widerrufenen%", "%gültigen, frischen Freigabe%", "%keine Prüfsumme%", "%version_nicht_gesenkt%"):
        assert muster in TEST, muster
    assert "'die Datenbank setzt 15 Minuten Wartezeit'" in TEST and "wartezeit_vorbei" in TEST and "freigabe_altern" in TEST
    # nur ein Admin bleibt gesperrt, zwei Admins gehen
    assert "'nur noch ein Admin'" in TEST and "'wieder zwei Admins'" in TEST
    # Kette und Rechtedrift
    assert "release_kette_pruefen()" in TEST and "'Verfälschung wird gefunden'" in TEST
    assert "'Vorgabe der id entfällt'" in TEST and "'Kette: vorher_hash ist der Hash des Vorgängers'" in TEST
    for rolle in ("wamo_core", "wamo_release", "wamo_anker", "wamo_admin", "anon", "authenticated", "service_role"):
        assert f"set role {rolle};" in TEST, rolle
    for tabelle in TABELLEN:
        assert f"__SCHEMA__.{tabelle}" in TEST
    # Test-Fehlercodes: Rechte 42501, Regeln 23514, doppelt 23505, Pflichtfeld 23502
    for code in ("'42501'", "'23514'", "'23505'", "'23502'", "'23503'"):
        assert code in TEST, code
