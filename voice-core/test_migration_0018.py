"""Prüft Migration 0018 (Laborprofile, Testlabor) als Text: Spalten, Prüfungen, Trigger, Rechte für UPDATE UND INSERT (Befund 12),
Löschfunktion, Rückweg (bricht bei JEDEM Laborprofil ab, T7) und die Regeln aus CLAUDE.md (Platzhalter, drop vor create, keine
Rechte für anon, authenticated, service_role, keine neue Tabelle ohne RLS). Die Wirkung in der Datenbank prüft
supabase/tests/0018_labor_profile_test.sql gegen wamo_test oder wamo_dev; sie läuft nicht in diesem Test.
Die Vollständigkeit der Löschliste gegen alle Migrationen prüft test_labor_loeschen.py."""

from __future__ import annotations

import re
from pathlib import Path

import labor_loeschliste as ll

SUPABASE = Path(__file__).resolve().parent.parent / "supabase"
SQL = (SUPABASE / "migrations" / "0018_labor_profile.sql").read_text(encoding="utf-8")
DOWN = (SUPABASE / "migrations" / "0018_labor_profile.down.sql").read_text(encoding="utf-8")
TEST = (SUPABASE / "tests" / "0018_labor_profile_test.sql").read_text(encoding="utf-8")
FREMDE = ("anon", "authenticated", "service_role", "public")
LABOR_SPALTEN = ("labor_art", "labor_platz", "labor_aktiv")


def _ohne_kommentare(text: str) -> str:
    return "\n".join(z.split("--", 1)[0] for z in text.splitlines())


def _eine_zeile(text: str) -> str:
    return " ".join(text.split())


def _anweisungen(text: str, wort: str) -> list[str]:
    return [a.strip() for a in re.split(r";\s*\n", _ohne_kommentare(text)) if re.match(rf"\s*{wort}\b", a, re.I)]


def _funktion(name: str) -> str:
    treffer = re.search(rf"create or replace function __SCHEMA__\.{name}\((.*?)\n\$\$\s*;|"
                        rf"create or replace function __SCHEMA__\.{name}\((.*?)end \$\$;", _ohne_kommentare(SQL), re.S)
    assert treffer, name
    return treffer.group(0)


# ---------- Allgemeine Regeln ----------

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


def test_keine_neue_tabelle_keine_neue_rolle_und_keine_rechte_fuer_fremde():
    """Es entsteht keine Tabelle (sonst gälten RLS und core_all) und keine Rolle; anon, authenticated, service_role und PUBLIC
    bekommen nie etwas, sie werden nur entzogen."""
    code = _ohne_kommentare(SQL)
    assert not re.search(r"create\s+table\b", code, re.I) and not re.search(r"create\s+role\b", code, re.I)
    for g in _anweisungen(code, "grant"):
        assert not any(re.search(rf"\b{f}\b", g, re.I) for f in FREMDE), g
        assert not re.search(r"\bwamo_core\b", g), g  # wamo_core bekommt nichts, es liest über das Tabellenrecht aus 0001
        assert g.rstrip().endswith("to wamo_admin"), g
    assert not re.search(r"\bdelete\s+from\s+__SCHEMA__\.profiles\b", code)  # das Profil selbst löscht die Funktion nie


def test_voraussetzung_lock_timeout_eine_transaktion_und_eintrag():
    code = _ohne_kommentare(SQL)
    assert "set local lock_timeout" in code
    for version in ("0001_wamo_init", "0002_wamo_geraete", "0003_wamo_teilen", "0010_modellzugang", "0017_vorgaenge"):
        assert f"'{version}'" in code, version
    assert "<> 5" in code and "0018 braucht" in code
    assert not re.search(r"^\s*(begin|commit)\s*;", code, re.I | re.M)  # EINE Transaktion von psql --single-transaction
    assert "insert into __SCHEMA__.schema_migrations (version) values ('0018_labor_profile') on conflict do nothing" in code


# ---------- Spalten, Prüfungen, Index, Trigger ----------

def test_spalten_idempotent_mit_standard_falsch():
    code = _ohne_kommentare(SQL)
    assert re.search(r"alter table __SCHEMA__\.profiles\s+add column if not exists labor_art\s+text,\s+"
                     r"add column if not exists labor_platz smallint,\s+"
                     r"add column if not exists labor_aktiv boolean not null default false;", code)


def test_pruefungen_mit_drop_davor_und_ohne_null_schlupfloch():
    code = _ohne_kommentare(SQL)
    for name in ("profiles_labor_art_chk", "profiles_labor_platz_chk", "profiles_labor_form_chk", "profiles_labor_streng_chk"):
        drop = code.index(f"drop constraint if exists {name}")
        add = code.index(f"add constraint {name}")
        assert drop < add, name
    assert "labor_art is null or labor_art in ('platz', 'schaufenster')" in code
    assert "labor_platz is null or labor_platz between 1 and 16" in code
    # die Form als case: ein null würde eine Prüfung bestehen (labor_art null mit gesetztem Platz)
    form = re.search(r"profiles_labor_form_chk\s+check \((case .*?end)\);", code, re.S)
    assert form, "Form der Laborspalten als case geschrieben"
    f = _eine_zeile(form.group(1))
    assert "when labor_art is null then (labor_platz is null and not labor_aktiv)" in f
    assert "when labor_art = 'platz' then labor_platz is not null" in f
    assert "else labor_platz is null end" in f
    # Befund 1 und 2: Laborprofil heißt immer Mitglied (auch das Schau-Profil) und dauerhaft aktiv = false
    assert "check (labor_art is null or (rolle = 'mitglied' and not aktiv))" in _eine_zeile(code)


def test_platz_ist_eindeutig_und_partiell():
    code = _ohne_kommentare(SQL)
    assert ("create unique index if not exists profiles_labor_platz_uni on __SCHEMA__.profiles (labor_platz) "
            "where labor_platz is not null") in code


def test_trigger_macht_art_und_platz_unveraenderlich_und_legt_nie_aktiv_an():
    code = _ohne_kommentare(SQL)
    funktion = _eine_zeile(_funktion("profiles_labor_guard"))
    assert "set search_path = ''" in funktion
    assert "tg_op = 'INSERT'" in funktion and "new.labor_art is not null and new.labor_aktiv" in funktion
    assert "new.labor_art is distinct from old.labor_art or new.labor_platz is distinct from old.labor_platz" in funktion
    assert funktion.count("errcode = '42501'") == 2
    drop = code.index("drop trigger if exists profiles_labor_guard on __SCHEMA__.profiles")
    anlegen = code.index("create trigger profiles_labor_guard before insert or update on __SCHEMA__.profiles")
    assert drop < anlegen
    assert "for each row execute function __SCHEMA__.profiles_labor_guard()" in code


# ---------- Rechte: UPDATE und INSERT (Befund 12) ----------

def test_rechte_laborspalten_nur_wamo_admin_beim_anlegen_und_nur_labor_aktiv_danach():
    code = _ohne_kommentare(SQL)
    revoke = _eine_zeile(" ".join(_anweisungen(code, "revoke")))
    assert ("revoke insert (labor_art, labor_platz, labor_aktiv), update (labor_art, labor_platz, labor_aktiv) "
            "on __SCHEMA__.profiles from public, anon, authenticated, service_role, wamo_core") in revoke
    assert "revoke update (labor_art, labor_platz) on __SCHEMA__.profiles from wamo_admin" in revoke
    grants = [_eine_zeile(g) for g in _anweisungen(code, "grant")]
    update = [g for g in grants if g.startswith("grant update (")]
    assert update == ["grant update (labor_aktiv) on __SCHEMA__.profiles to wamo_admin"]
    assert not any("insert" in g.lower() for g in grants)  # das Tabellenrecht insert für wamo_admin steht seit 0001


def test_selbsttest_prueft_update_und_insert_fuer_alle_rollen():
    code = _ohne_kommentare(SQL)
    test = _eine_zeile(code[code.rindex("do $$\ndeclare\n  r name"):])
    # F9: keine feste Rollenliste, sondern alle Rollen aus pg_roles außer Superusern, wamo_admin, dem Tabellenbesitzer und pg_-Rollen
    assert "array['wamo_core', 'wamo_release', 'wamo_anker', 'anon', 'authenticated', 'service_role']" not in test
    assert test.count("from pg_roles o where not o.rolsuper and o.rolname <> 'wamo_admin' and o.rolname !~ '^pg_'") == 2
    assert test.count("o.oid <> (select t.relowner from pg_class t where t.oid = '__SCHEMA__.profiles'::regclass)") == 2
    # einmal für die Spaltenrechte (INSERT, UPDATE), einmal für EXECUTE der Funktionen; wamo_core darf dabei nicht durchrutschen
    assert "v_geprueft := v_geprueft || r::text" in test and "not ('wamo_core' = any(v_geprueft))" in test
    assert "has_column_privilege(r, '__SCHEMA__.profiles', c, 'INSERT')" in test
    assert "has_column_privilege(r, '__SCHEMA__.profiles', c, 'UPDATE')" in test
    assert "array['labor_art', 'labor_platz', 'labor_aktiv']" in test
    # PUBLIC über die ACL (grantee 0), Tabellen- und Spaltenrecht
    assert "aclexplode(t.relacl)" in test and "aclexplode(a.attacl)" in test and "x.grantee = 0" in test
    assert "x.privilege_type in ('INSERT', 'UPDATE')" in test
    # wamo_admin: INSERT auf alle drei, UPDATE nur auf labor_aktiv
    assert "has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_art', 'UPDATE')" in test
    assert "has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_platz', 'UPDATE')" in test
    assert "not has_column_privilege('wamo_admin', '__SCHEMA__.profiles', 'labor_aktiv', 'UPDATE')" in test
    assert "has_table_privilege('wamo_core', '__SCHEMA__.profiles', 'INSERT,UPDATE,DELETE,TRUNCATE')" in test
    for stueck in ("profiles_labor_art_chk", "profiles_labor_platz_uni", "profiles_labor_guard", "<> 4"):
        assert stueck in test, stueck
    assert "p.proacl is null" in test and "aclexplode(p.proacl)" in test  # null wäre der Standard PUBLIC
    assert "p.prosecdef" in test and "search_path=\"\"" in test
    assert "to_regrole" not in test  # eine Rolle, die es nicht gibt, kommt in pg_roles gar nicht vor


# ---------- Löschfunktion ----------

def test_funktionen_security_definer_leerer_search_path_nur_wamo_admin():
    code = _ohne_kommentare(SQL)
    for name in ("labor_loeschen", "labor_zaehlen"):
        kopf = _eine_zeile(_funktion(name))
        assert "security definer set search_path = ''" in kopf, name
    assert "labor_loeschen(\n  p_profil uuid, p_ab timestamptz default null, p_bis timestamptz default null\n) returns jsonb" in code
    revoke = _eine_zeile(" ".join(_anweisungen(code, "revoke")))
    for ziel in ("labor_loeschen(uuid, timestamptz, timestamptz)", "labor_zaehlen(uuid)", "profiles_labor_guard()"):
        assert re.search(rf"revoke all on function __SCHEMA__\.{re.escape(ziel)} from public, anon, authenticated, "
                         rf"service_role, wamo_core, wamo_admin", revoke), ziel
    grants = [_eine_zeile(g) for g in _anweisungen(code, "grant")]
    assert "grant execute on function __SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz) to wamo_admin" in grants
    assert "grant execute on function __SCHEMA__.labor_zaehlen(uuid) to wamo_admin" in grants
    assert code.index("revoke all on function __SCHEMA__.labor_loeschen") < code.index("grant execute on function __SCHEMA__.labor_loeschen")


def test_loeschfunktion_wirft_in_allen_faellen_mit_dem_vereinbarten_code():
    f = _eine_zeile(_funktion("labor_loeschen"))
    assert "p_profil is null" in f and "errcode = '22004'" in f
    assert "p_ab > p_bis" in f and "errcode = '22023'" in f  # verkehrtes Fenster
    # F6: for no key update sperrt die Zeile gegen labor_aktiv, blockiert aber keine Fremdschlüsselprüfung des Kerns auf das Profil
    assert "for no key update" in f and not re.search(r"for update\b", f)
    assert "if v_art is null then raise exception 'labor_loeschen: kein Laborprofil' using errcode = '42501'" in f
    assert "if v_aktiv then raise exception" in f and "errcode = '55000'" in f
    # F2: Sperrfrist nach jeder Änderung des Profils (profiles_touch setzt updated_at bei jedem Update)
    assert "p.updated_at" in f
    assert f"v_geaendert > clock_timestamp() - interval '{ll.SPERRFRIST_MINUTEN} minutes'" in f
    assert "Sperrfrist, noch etwa %" in f and f.count("errcode = '55000'") == 4  # freigeschaltet, Sperrfrist, Sitzung vorher und nachher
    # Sitzungsprüfung vor dem ersten delete und noch einmal nach dem letzten, vor dem return
    pruefung = f"s.ended_at is null and s.started_at > clock_timestamp() - interval '{ll.SITZUNG_MAX_MINUTEN} minutes'"
    assert f.count(pruefung) == 2
    erstes, letztes = f.index("delete from"), f.rindex("delete from")
    assert f.index(pruefung) < erstes and letztes < f.rindex(pruefung) < f.index("return v_ergebnis")
    # alle Prüfungen stehen vor jedem delete
    for text in ("kein Laborprofil", "noch freigeschaltet", "Sperrfrist", "läuft noch eine Sitzung"):
        assert f.index(text) < erstes, text


def test_die_meldung_der_sperrfrist_passt_zum_muster_im_skript():
    """scripts/wamo_labor.py wartet, wenn die Meldung 'Sperrfrist, noch etwa N Sekunden' enthält: beide Texte müssen zusammenpassen."""
    meldung = re.search(r"'(labor_loeschen: Sperrfrist, noch etwa % Sekunden[^']*)'", SQL)
    assert meldung, "Meldung der Sperrfrist in der Löschfunktion"
    skript = (SUPABASE.parent / "scripts" / "wamo_labor.py").read_text(encoding="utf-8")
    muster = re.search(r'SPERRFRIST = re\.compile\(r"([^"]+)"\)', skript)
    assert muster, "Muster der Sperrfrist im Skript"
    assert re.search(muster.group(1), meldung.group(1).replace("%", "95", 1))


def test_feedback_geht_ueber_die_turns_der_sitzungen_im_fenster_mit():
    """feedback hat keinen Sitzungsbezug, nur turn_id: eine später entstandene Rückmeldung zu einem Turn der Sitzung bliebe stehen."""
    f = _eine_zeile(_funktion("labor_loeschen"))
    feedback = next(o for o in ll.LOESCHLISTE if o.tabelle == "feedback")
    assert feedback.turn == "turn_id"
    assert "x.turn_id in (select t.id from __SCHEMA__.turns t where t.session_id = any(v_sitzungen))" in f


def test_zaehlen_und_loeschen_behandeln_device_events_gleich():
    """F7: labor_zaehlen zählte device_events nur über durch, labor_loeschen löscht auch über das Gerät des Profils."""
    z = _eine_zeile(_funktion("labor_zaehlen"))
    assert ("'device_events', (select count(*) from __SCHEMA__.device_events x where x.durch = p_profil "
            "or x.device_id in (select d.id from __SCHEMA__.devices d where d.user_id = p_profil))") in z
    assert "x.durch = p_profil or x.device_id = any(v_geraete)" in _eine_zeile(_funktion("labor_loeschen"))


# ---------- Kopplung an den Kern (F8) ----------

VOICE_CORE = Path(__file__).resolve().parent
BOT = (VOICE_CORE / "bot.py").read_text(encoding="utf-8")
AUTH_GATE = (VOICE_CORE / "auth_gate.py").read_text(encoding="utf-8")
ENV_BEISPIEL = (VOICE_CORE.parent / ".env.example").read_text(encoding="utf-8")
LOESCHLISTE_TEXT = (VOICE_CORE / "labor_loeschliste.py").read_text(encoding="utf-8")


def test_sitzungsgrenze_der_loeschfunktion_liegt_ueber_dem_standard_des_kerns():
    """Der Kern begrenzt eine Sitzung auf WAMO_MAX_SITZUNG_MIN (bot.py, Standard 120, per Umgebung änderbar). Die Löschfunktion hält
    eine offene Sitzung bis zu SITZUNG_MAX_MINUTEN für laufend: der Wert muss mehr als 10 Minuten über dem Standard liegen."""
    standard = float(re.search(r'MAX_SITZUNG_MIN = float\(os\.getenv\("WAMO_MAX_SITZUNG_MIN"\) or (\d+(?:\.\d+)?)\)', BOT).group(1))
    beispiel = float(re.search(r"^WAMO_MAX_SITZUNG_MIN=(\d+(?:\.\d+)?)", ENV_BEISPIEL, re.M).group(1))
    assert ll.SITZUNG_MAX_MINUTEN > max(standard, beispiel) + 10
    assert f"interval '{ll.SITZUNG_MAX_MINUTEN} minutes'" in SQL
    # die Kopplung steht an beiden Stellen, damit niemand die Umgebungsvariable erhöht, ohne die Grenze anzufassen
    assert "WAMO_MAX_SITZUNG_MIN" in SQL and "WAMO_MAX_SITZUNG_MIN" in LOESCHLISTE_TEXT


def test_sperrfrist_ist_laenger_als_zwischenspeicher_plus_pruefintervall_des_kerns():
    """Nach labor_aktiv = false kennt der Kern das Profil noch PROFILE_HIT_SECS und prüft laufende Sitzungen alle PROFILE_RECHECK_SECS."""
    zwischenspeicher = float(re.search(r"^PROFILE_HIT_SECS = ([\d.]+)", AUTH_GATE, re.M).group(1))
    pruefintervall = float(re.search(r"^PROFILE_RECHECK_SECS = ([\d.]+)", BOT, re.M).group(1))
    assert ll.SPERRFRIST_MINUTEN * 60 > zwischenspeicher + pruefintervall
    assert f"interval '{ll.SPERRFRIST_MINUTEN} minutes'" in SQL
    assert "PROFILE_HIT_SECS" in SQL and "PROFILE_HIT_SECS" in LOESCHLISTE_TEXT


def test_loeschfunktion_loescht_genau_die_loeschliste_in_dieser_reihenfolge_und_nie_profiles():
    f = _eine_zeile(_funktion("labor_loeschen"))
    geloescht = re.findall(r"delete from __SCHEMA__\.(\w+) x", f)
    assert tuple(geloescht) == ll.tabellen()
    assert geloescht[-1] == "sessions"  # zuletzt: die abhängigen Zeilen vorher
    schluessel = re.findall(r"jsonb_build_object\('(\w+)', v_n\)", f)
    assert tuple(schluessel) == ll.tabellen()
    assert "return v_ergebnis" in f


def test_loeschfunktion_nutzt_besitzerspalten_und_zeitspalten_der_liste():
    f = _eine_zeile(_funktion("labor_loeschen"))
    for o in ll.LOESCHLISTE:
        stueck = re.search(rf"delete from __SCHEMA__\.{o.tabelle} x where (.*?)(?=; get diagnostics)", f)
        assert stueck, o.tabelle
        bedingung = stueck.group(1)
        for spalte in o.besitzer:
            assert f"x.{spalte} = p_profil" in bedingung, (o.tabelle, spalte)
        if o.tabelle != "sessions":  # sessions löscht über die vorab gesammelten Kennungen des Fensters
            assert f"x.{o.zeit} >= v_ab and x.{o.zeit} <= v_bis" in bedingung, (o.tabelle, o.zeit)
        assert "v_voll" in bedingung, o.tabelle  # ohne Fenster: alles des Profils
        if o.sitzung:
            assert f"x.{o.sitzung} = any(v_sitzungen)" in bedingung, (o.tabelle, o.sitzung)


def test_zaehlfunktion_zaehlt_dieselben_tabellen_und_nur_laborprofile():
    f = _eine_zeile(_funktion("labor_zaehlen"))
    schluessel = re.findall(r"'(\w+)',\s+\(select count\(\*\) from __SCHEMA__\.(\w+) x", f)
    assert tuple(s for s, _ in schluessel) == ll.tabellen()
    assert all(s == t for s, t in schluessel)
    assert "v_art is null" in f and "kein Laborprofil" in f and "errcode = '42501'" in f
    assert "language plpgsql stable security definer" in f


def test_loeschfunktion_ruft_nichts_aus_der_liste_der_nicht_loeschbaren_auf():
    """Die Protokolltabellen der Selbständerung und die Konfiguration (AUSNAHMEN) fasst sie nie an."""
    f = _funktion("labor_loeschen")
    for tabelle in ll.AUSNAHMEN:
        assert f"__SCHEMA__.{tabelle} " not in f.replace("__SCHEMA__.profiles p where", ""), tabelle


# ---------- Rückweg ----------

def test_rueckweg_bricht_bei_jedem_laborprofil_ab_gesperrt_in_prod_und_wiederholbar():
    code = _ohne_kommentare(DOWN)
    assert "'__SCHEMA__' = 'wamo_prod'" in code and "wamo.allow_drop" in code
    assert "version > '0018_labor_profile'" in code and "wamo.down_trotz_folgemigration" in code
    # JEDES Profil mit labor_art zählt, auch ein gesperrtes: keine Bedingung auf aktiv oder labor_aktiv
    zaehlen = re.search(r"execute '(select count\(\*\) from __SCHEMA__\.profiles where [^']*)' into v_n", code)
    assert zaehlen and _eine_zeile(zaehlen.group(1)) == "select count(*) from __SCHEMA__.profiles where labor_art is not null"
    assert "if v_n > 0 then" in code and "errcode = '55000'" in code
    # wiederholbar: die Spalte wird dynamisch geprüft, alles andere mit if exists
    assert "information_schema.columns" in code and "column_name = 'labor_art'" in code
    for ziel in ("drop trigger if exists profiles_labor_guard", "drop function if exists __SCHEMA__.profiles_labor_guard()",
                 "drop function if exists __SCHEMA__.labor_loeschen(uuid, timestamptz, timestamptz)",
                 "drop function if exists __SCHEMA__.labor_zaehlen(uuid)", "drop index if exists __SCHEMA__.profiles_labor_platz_uni",
                 "drop column if exists labor_aktiv", "drop column if exists labor_platz", "drop column if exists labor_art",
                 "delete from __SCHEMA__.schema_migrations where version = '0018_labor_profile'"):
        assert ziel in code, ziel
    assert "cascade" not in code.lower()
    assert code.index("if v_n > 0 then") < code.index("drop column")  # erst die Prüfung, dann der Abbau


def test_rueckweg_ist_eine_einzige_anweisung_damit_der_abbruch_auch_ohne_psql_optionen_greift():
    """F9: Ohne -v ON_ERROR_STOP=1 und --single-transaction liefe psql nach einem Fehler weiter. Darum stehen Prüfung und Abbau in EINEM
    do-Block: ein raise exception in der Prüfung nimmt den ganzen Block zurück, es kann kein Abbau danach laufen."""
    code = _ohne_kommentare(DOWN).strip()
    assert code.startswith("do $$") and code.endswith("end $$;")
    assert code.count("do $$") == 1 and code.count("end $$;") == 1
    assert code.index("raise exception") < code.index("drop trigger") < code.index("drop column")
    # jede der drei Prüfungen bricht mit raise exception ab, bevor etwas verändert wird
    vor_abbau = code[: code.index("drop trigger")]
    assert vor_abbau.count("raise exception") == 3 and vor_abbau.count("errcode = '55000'") == 3
    # die Sperrzeit für den Abbau gilt für die Transaktion des Blocks (set local ohne Transaktionsblock wäre wirkungslos)
    assert "perform set_config('lock_timeout', '5s', true)" in code and "set local" not in code


def test_rueckweg_kopf_nennt_die_aufrufform_fuer_jedes_schema_und_die_reihenfolge_mit_dem_kern():
    kopf = " ".join(z.lstrip("- ").strip() for z in DOWN.splitlines() if z.lstrip().startswith("--"))
    assert "psql -U postgres -v ON_ERROR_STOP=1 --single-transaction" in kopf
    assert "jedes Schema" in kopf and "wamo_prod" in kopf and "wamo.allow_drop" in kopf
    assert "erst nach der Rückstellung des Kerns" in kopf  # F4
    assert "503" in kopf and "ALLE" in kopf
    # und die Migration selbst: vor dem Release des Kerns
    anfang = " ".join(z.lstrip("- ").strip() for z in SQL.splitlines()[:20] if z.lstrip().startswith("--"))
    assert "VOR dem Release des Kerns" in anfang and "503" in anfang and "ALLE" in anfang


def test_rueckweg_pruefblock_steht_woertlich_im_sql_test():
    """Der Test beweist den Abbruch des echten Blocks: er steht dort unverändert und wird gegen ein Laborprofil ausgeführt."""
    blocks = re.findall(r"do \$\$\ndeclare\n  v_n bigint := 0;.*?end \$\$;", DOWN, re.S)
    assert len(blocks) == 1
    assert _eine_zeile(blocks[0]) in _eine_zeile(TEST)


# ---------- SQL-Test ----------

def test_sql_test_ist_eine_zurueckgerollte_transaktion_nie_in_prod():
    code = _ohne_kommentare(TEST)
    assert code.index("begin;") < code.index("rollback;")
    assert "\\echo 0018 OK" in TEST
    assert "Test nie in wamo_prod" in code
    for rolle in ("wamo_core", "wamo_admin", "anon", "authenticated", "service_role"):
        assert f"set role {rolle}" in code, rolle


def test_sql_test_deckt_prufungen_trigger_rechte_und_loeschen_ab():
    code = _eine_zeile(_ohne_kommentare(TEST))
    for punkt in (
        "'23514'",  # Prüfungen
        "'23505'",  # Platz eindeutig
        "set labor_art = null", "set labor_platz = 9", "set labor_art = 'schaufenster'",  # Trigger
        "set rolle = 'admin'", "set aktiv = true",  # Befund 1 und 2
        "release_anzahl_admins()", "release_ist_admin(",  # T12
        "labor_loeschen(", "labor_zaehlen(",
        "pg_get_functiondef", "disable trigger user",
        "has_column_privilege(",
        "'55000'", "'42501'", "'22023'",
    ):
        assert punkt in code, punkt
    # insert UND update als wamo_core, anon, authenticated, service_role verweigert
    assert code.count("insert into __SCHEMA__.profiles") >= 4 and code.count("update __SCHEMA__.profiles set labor_aktiv") >= 4
    # Rolle mitglied auch für das Schau-Profil
    assert re.search(r"labor_art.*'schaufenster'", code) and "'mitglied'" in code


def test_sql_test_braucht_keinen_superuser_fuer_die_fixture():
    """F3: session_replication_role verlangt Superuser, postgres ist im Supabase-Image keiner. Die Schwestertests schalten die Trigger mit
    alter table ... disable trigger user ab (der Tabellenbesitzer darf das); die Fremdschlüssel bleiben aktiv und sind erfüllt."""
    code = _ohne_kommentare(TEST)
    assert "session_replication_role" not in code and "set_config('session_replication" not in code
    for tabelle in ("hinweise", "dateien", "teilungen", "teilung_events", "devices", "device_events"):
        assert f"alter table __SCHEMA__.{tabelle} disable trigger user;" in code, tabelle
        assert f"alter table __SCHEMA__.{tabelle} enable trigger user;" in code, tabelle
    assert code.count("disable trigger user") == code.count("enable trigger user") == 6
    # erst abschalten, dann füllen, dann wieder anschalten
    assert code.index("disable trigger user") < code.index("select wamo_testhilfe.fuellen(") < code.index("enable trigger user")
    assert code.rindex("select wamo_testhilfe.fuellen(") < code.rindex("enable trigger user")


def test_sql_test_datiert_updated_at_ohne_superuser_zurueck_und_prueft_die_sperrfrist():
    """Die Sperrfrist (F2) lehnt jedes jüngst geänderte Profil ab, auch die frisch angelegten Testprofile: der Test prüft sie und datiert
    updated_at dann mit abgeschaltetem profiles_touch zurück (der Tabellenbesitzer darf es)."""
    code = _ohne_kommentare(TEST)
    assert "'Sperrfrist, noch etwa'" in code and "'noch freigeschaltet'" in code and "'läuft noch eine Sitzung'" in code
    aus, an = code.index("alter table __SCHEMA__.profiles disable trigger profiles_touch;"), code.index(
        "alter table __SCHEMA__.profiles enable trigger profiles_touch;")
    assert aus < code.index("set updated_at = now() - interval", aus) < an
    # die Sperrfrist wird geprüft, bevor zurückdatiert wird; die erste erfolgreiche Löschung kommt danach
    assert code.index("'Sperrfrist, noch etwa'") < aus < code.index("labor_loeschen('00000000-0000-4000-8000-0000000018b1', now() - interval '6 hours'")


def test_sql_test_nimmt_nur_werte_die_kein_skript_vergibt_und_prueft_die_vorbedingung():
    """F5: Feste Plätze 1, 2, 5, Tunnelplätze 51 bis 54 und Kennungen pc-lab* kollidierten (23505) mit Probe-Laborprofilen oder echten
    Geräten in wamo_test. Jetzt Plätze 14 bis 16, Tunnelplätze 60 bis 63 und die Kennung pc-t0018-<n>; eine Vorbedingung bricht klar ab."""
    code = _ohne_kommentare(TEST)
    plaetze = {int(p) for p in re.findall(r"'platz',\s*(\d+)", code)} | {int(p) for p in re.findall(r"'schaufenster',\s*(\d+)", code)}
    assert plaetze == {14, 15, 16, 17}, plaetze  # 17 ist der bewusst ungültige Platz
    assert "59 + p_nr" in code and "50 + p_nr" not in code
    assert "'pc-t0018-' || p_nr" in code and "pc-lab" not in code
    vorbedingung = code[: code.index("grant wamo_core, wamo_admin to postgres")]
    assert "labor_platz in (14, 15, 16)" in vorbedingung and "tunnel_slot between 60 and 63" in vorbedingung
    assert "like 'pc-t0018-%'" in vorbedingung and vorbedingung.count("raise exception 'Test: ") == 2


def _array(text: str, name: str) -> list[str]:
    treffer = re.findall(rf"\b{name} text\[\] := array\[(.*?)\];", text, re.S)
    assert treffer, name
    gelesen = [sorted(re.findall(r"'([^']+)'", t)) for t in treffer]
    assert all(g == gelesen[0] for g in gelesen), f"{name} steht an mehreren Stellen mit verschiedenem Inhalt"
    return gelesen[0]


def test_sql_test_leitet_tabellenzahl_und_listen_aus_der_loeschliste_ab():
    """Die 14 stand fest im SQL-Test. Jetzt steht dort die Liste der Tabellen, und dieser Test hält sie gleich der Löschliste."""
    code = _ohne_kommentare(TEST)
    assert "14::bigint" not in code and "liefert 14 Tabellen" not in code
    assert _array(code, "erwartet") == sorted(ll.tabellen())
    assert _array(code, "ausnahmen") == sorted(ll.AUSNAHMEN)
    assert _array(code, "fremde") == sorted(f"{t}.{s}" for t, s in ll.FREMDE_VERWEISE)


def test_sql_test_prueft_device_events_fremder_profile_und_spaete_rueckmeldungen():
    """F7 in der Datenbank: ein Ereignis eines fremden Profils zu einem Gerät des Profils wird gezählt und gelöscht, und eine Rückmeldung
    zu einem Turn der Sitzung im Fenster verschwindet auch, wenn sie erst nach dem Fenster entstand."""
    code = _eine_zeile(_ohne_kommentare(TEST))
    assert "device_events zählt auch Ereignisse fremder Profile" in code
    assert "späte Rückmeldung zu einem Turn der Sitzung ist weg" in code


def test_sql_test_prueft_vollstaendigkeit_dynamisch_aus_dem_katalog():
    code = _eine_zeile(_ohne_kommentare(TEST))
    assert "pg_constraint" in code and "confrelid = '__SCHEMA__.profiles'::regclass" in code
    assert "confrelid = 'auth.users'::regclass" in code
    assert "position(" in code and "delete from __SCHEMA__." in code
    for tabelle in ll.AUSNAHMEN:
        assert f"'{tabelle}'" in code, tabelle
    for tabelle in ll.tabellen():
        assert tabelle in code, tabelle
