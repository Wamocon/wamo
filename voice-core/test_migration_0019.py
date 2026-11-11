"""Prüft Migration 0019 (Fernzugriff startet mit dem Code der Anmeldung) als Text: Die Funktion rd_sitzung_beginnen verliert genau die
beiden Sperren aus 0015 (Code höchstens 120 Sekunden alt, ein Code für einen Start), bekommt die Grenze von 31 Tagen und den Hinweis
an alle Admins (KP-2b), sonst bleibt sie Zeile für Zeile gleich. Dazu Rechte, Selbsttest, der Rückweg (wortgleich 0015) und die Regeln
aus CLAUDE.md (Platzhalter, keine Rechte für anon, authenticated, service_role, keine neue Tabelle). Die Wirkung in der Datenbank prüft
supabase/tests/0019_rd_anmeldung_genuegt_test.sql gegen wamo_test oder wamo_dev; sie läuft nicht in diesem Test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import auth_gate
import rd_api
from test_migration_0018 import VERBEN, _zerlegen

SUPABASE = Path(__file__).resolve().parent.parent / "supabase"
SQL = (SUPABASE / "migrations" / "0019_rd_anmeldung_genuegt.sql").read_text(encoding="utf-8")
DOWN = (SUPABASE / "migrations" / "0019_rd_anmeldung_genuegt.down.sql").read_text(encoding="utf-8")
TEST = (SUPABASE / "tests" / "0019_rd_anmeldung_genuegt_test.sql").read_text(encoding="utf-8")
ALT = (SUPABASE / "migrations" / "0015_rd_sitzungen.sql").read_text(encoding="utf-8")
ALT_TEST = (SUPABASE / "tests" / "0015_rd_sitzungen_test.sql").read_text(encoding="utf-8")
FREMDE = ("anon", "authenticated", "service_role", "public")
SIGNATUR = "uuid, text, integer, timestamptz, integer, integer"


def _ohne_kommentare(text: str) -> str:
    return "\n".join(z.split("--", 1)[0] for z in text.splitlines())


def _eine_zeile(text: str) -> str:
    return " ".join(text.split())


def _funktion(text: str) -> str:
    anfang = text.index("create or replace function __SCHEMA__.rd_sitzung_beginnen(")
    return text[anfang:text.index("end $$;", anfang) + len("end $$;")]


def _anweisungen(text: str, wort: str) -> list[str]:
    return [a.strip() for a in re.split(r";\s*\n", _ohne_kommentare(text)) if re.match(rf"\s*{wort}\b", a, re.I)]


NEU = _eine_zeile(_ohne_kommentare(_funktion(SQL)))
VORHER = _eine_zeile(_ohne_kommentare(_funktion(ALT)))


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
        assert not re.search(r"\b(fuer|ueber|Pruefung|Rueckweg|koennen|waehrend|aendern|Aenderung|loeschen|geaendert|gueltig)\b",
                             " ".join(kommentare)), "Ersatzschreibweise für Umlaute im Kommentar"


def test_keine_neue_tabelle_keine_neue_rolle_und_keine_rechte_fuer_fremde():
    """Es entsteht keine Tabelle (sonst gälten RLS und core_all) und keine Rolle. Gewährt wird nur EXECUTE an wamo_core; anon,
    authenticated, service_role, PUBLIC und wamo_admin werden nur entzogen."""
    for text in (SQL, DOWN):
        code = _ohne_kommentare(text)
        assert not re.search(r"create\s+table\b", code, re.I) and not re.search(r"create\s+role\b", code, re.I)
        assert not re.search(r"\b(drop|alter)\s+table\b", code, re.I)
        grants = _anweisungen(code, "grant")
        assert len(grants) == 1, grants
        assert _eine_zeile(grants[0]) == f"grant execute on function __SCHEMA__.rd_sitzung_beginnen({SIGNATUR}) to wamo_core"
        revokes = [_eine_zeile(r) for r in _anweisungen(code, "revoke")]
        assert revokes == [f"revoke all on function __SCHEMA__.rd_sitzung_beginnen({SIGNATUR}) "
                           "from public, anon, authenticated, service_role, wamo_admin"]


def test_voraussetzung_lock_timeout_eine_transaktion_und_eintrag():
    code = _ohne_kommentare(SQL)
    assert "set local lock_timeout = '5s'" in code
    for version in ("0001_wamo_init", "0002_wamo_geraete", "0015_rd_sitzungen"):
        assert f"'{version}'" in code, version
    assert "0019 braucht 0001, 0002 und 0015" in code
    assert not re.search(r"^\s*(begin|commit)\s*;", code, re.I | re.M)  # EINE Transaktion von psql --single-transaction
    assert code.rstrip().endswith(
        "insert into __SCHEMA__.schema_migrations (version) values ('0019_rd_anmeldung_genuegt') on conflict do nothing;")


def test_nur_diese_eine_funktion_wird_ersetzt_und_sie_ist_zweimal_anwendbar():
    code = _ohne_kommentare(SQL)
    erstellt = re.findall(r"create\s+(?:or\s+replace\s+)?function\s+(\S+?)\(", code, re.I)
    assert erstellt == ["__SCHEMA__.rd_sitzung_beginnen"]
    assert not re.search(r"create\s+function\b", code, re.I)  # nur "create or replace": ein zweiter Lauf bricht nicht ab
    assert not re.search(r"\b(create|drop)\s+(trigger|index|policy|type|view)\b", code, re.I)


# ---------- Die Funktion: genau die beschlossene Änderung ----------

def test_signatur_rueckgabe_und_eigenschaften_bleiben_wie_in_0015():
    kopf_neu = _eine_zeile(_funktion(SQL).split("as $$", 1)[0])
    kopf_alt = _eine_zeile(_funktion(ALT).split("as $$", 1)[0])
    assert kopf_neu == kopf_alt
    assert "security definer" not in kopf_neu.lower()  # läuft mit den Rechten des Aufrufers (wamo_core)
    assert "set search_path = ''" in kopf_neu  # search_path fest und leer


def test_beide_sperren_sind_entfallen():
    assert "120 seconds" in VORHER and "schon für einen Start verwendet" in VORHER  # die Gegenprobe: 0015 hatte sie
    assert "120 seconds" not in NEU
    assert "schon für einen Start verwendet" not in NEU and "s.aal2_at >= p_aal2_zeit" not in NEU
    assert "'fehler'" not in NEU  # die Ausnahme der Sperre (Start am Gateway gescheitert) gehört mit ihr weg


def test_neue_obergrenze_31_tage_und_zukunft_bleibt():
    zukunft = "if p_aal2_zeit > clock_timestamp() + interval '5 seconds' then raise exception"
    grenze = "if clock_timestamp() - p_aal2_zeit > interval '31 days' then raise exception"
    assert zukunft in NEU and grenze in NEU
    assert NEU.index(zukunft) < NEU.index(grenze) < NEU.index("pg_advisory_xact_lock")  # vor dem Lock und vor jeder Zeile
    for text in re.findall(r"interval '31 days' then raise exception '[^']+' using errcode = '(\d+)'", NEU):
        assert text == "23514"  # derselbe Code wie bei der Zukunft: der Kern bildet ihn auf die Codeabfrage ab (rd_api.py)


def test_der_rest_der_funktion_bleibt_zeile_fuer_zeile_gleich():
    """Ersetzt NUR die beiden Sperren: Nimmt man aus 0015 die zwei Sperren heraus und aus 0019 die Grenze von 31 Tagen und den
    Hinweis, bleibt dieselbe Funktion (Eingaben, Admin, Zukunft, Lock, Aufräumen, rate, belegt, Ablösung, voll, Anlegen, Rückgabe)."""
    alt = re.sub(r"if clock_timestamp\(\) - p_aal2_zeit > interval '120 seconds' then raise exception '[^']+' "
                 r"using errcode = '23514'; end if; ", "", VORHER)
    alt = re.sub(r"if exists \(select 1 from __SCHEMA__\.rd_sitzungen s where s\.profil_id = p_profil and s\.aal2_at >= p_aal2_zeit "
                 r"and coalesce\(s\.grund, ''\) <> 'fehler'\) then raise exception '[^']+' using errcode = '23514'; end if; ", "", alt)
    neu = re.sub(r"if clock_timestamp\(\) - p_aal2_zeit > interval '31 days' then raise exception '[^']+' "
                 r"using errcode = '23514'; end if; ", "", NEU)
    neu = re.sub(r"v_alter := clock_timestamp\(\).*?where a\.aktiv and a\.rolle = 'admin'; ", "", neu)
    neu = neu.replace("v_alter interval; v_wann text; ", "")
    assert alt != VORHER and neu != NEU
    assert neu == alt, "0019 ändert mehr als die beschlossenen Stellen"


# ---------- Hinweis an alle Admins (KP-2b) ----------

def _hinweis_block() -> str:
    treffer = re.search(r"v_alter := clock_timestamp\(\).*?where a\.aktiv and a\.rolle = 'admin';", NEU)
    assert treffer
    return treffer.group(0)


def test_hinweis_entsteht_nur_beim_erfolgreichen_start_in_derselben_transaktion():
    anlegen = NEU.index("insert into __SCHEMA__.rd_sitzungen")
    hinweis = NEU.index("insert into __SCHEMA__.hinweise")
    ok = NEU.index("return query select 'ok'")
    assert anlegen < hinweis < ok
    for ergebnis in ("'rate'", "'belegt'", "'voll'"):
        assert NEU.index(f"return query select {ergebnis}") < hinweis  # abgelehnte Starts melden nichts
    assert NEU.count("insert into __SCHEMA__.hinweise") == 1
    assert "hinweis_an_admins" not in NEU  # für wamo_core nicht ausführbar, die Funktion bliebe sonst ohne Wirkung


def test_hinweis_geht_an_alle_aktiven_admins_mit_art_system():
    block = _hinweis_block()
    assert re.search(r"insert into __SCHEMA__\.hinweise \(empfaenger, art, titel, text, bezug, daten\) "
                     r"select a\.user_id, 'system', ", block)
    assert "from __SCHEMA__.profiles a where a.aktiv and a.rolle = 'admin'" in block
    assert "p_profil" in block and "a.user_id <> p_profil" not in block  # auch der Startende bekommt ihn
    assert "v_neu, jsonb_build_object('quelle', 'fernzugriff', 'sitzung', v_neu, 'ziel', p_ziel, 'profil', p_profil)" in block


def test_hinweis_text_ohne_zugangsdaten_und_ohne_ziel_interna():
    """Im Text stehen nur: Person (Anzeigename), Kurzname des Ziels, Alter des Codes, Höchstdauer und ein fester Satz. Nichts aus
    Zugangsdaten, Zielliste oder Gateway."""
    block = _hinweis_block()
    texte = " ".join(re.findall(r"'([^']*)'", block))
    for verboten in ("passwort", "password", "schlüssel", "schluessel", "token", "konto", "wamo-rd", "hostname", "adresse", "port",
                     "json", "gateway", "guacamole", "secret", "geheim"):
        assert verboten not in texte.lower(), verboten
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", texte)
    # die einzigen Werte, die in den Text einfließen
    werte = set(re.findall(r"\b(p_[a-z0-9_]+|v_[a-z0-9_]+)\b", block.split("'system',", 1)[1].split("v_neu, jsonb_build_object")[0]))
    assert werte <= {"p_ziel", "p_profil", "p_dauer_secs", "v_wann"}, werte
    assert "display_name" in block and "left(" in block  # Name gekürzt, der Text selbst auf 1000 Zeichen begrenzt
    assert re.search(r"1000\), v_neu", block)


def test_hinweis_wortlaut_deutsch_ohne_gedankenstrich():
    block = _hinweis_block()
    for satz in ("hat eine Fernsitzung auf", "gestartet. Der Code der Anmeldung wurde", "eingegeben. Die Sitzung endet nach höchstens",
                 "Warst du das nicht, melde dich an allen Geräten ab und sag es den anderen Admins.", "Fernzugriff gestartet: "):
        assert satz in block, satz
    texte = " ".join(re.findall(r"'([^']*)'", block))
    assert chr(0x2013) not in texte and chr(0x2014) not in texte and " - " not in texte


# ---------- Selbsttest ----------

def test_selbsttest_prueft_den_neuen_stand_und_die_rechte():
    selbsttest = _eine_zeile(_ohne_kommentare(SQL).split("-- Selbsttest")[-1])
    teile = _ohne_kommentare(SQL).rsplit("do $$", 1)[1]
    t = _eine_zeile(teile)
    assert "raise exception" in t and "errcode = '42501'" in t
    for muss in ("like '%120 seconds%'", "like '%schon für einen Start verwendet%'", "not like '%31 days%'", "not like '%__SCHEMA__.hinweise%'",
                 "prosecdef", "search_path=%", "has_table_privilege('wamo_core', '__SCHEMA__.hinweise', 'INSERT')",
                 "has_column_privilege('wamo_core', '__SCHEMA__.hinweise', 'gelesen_at', 'UPDATE')", "relrowsecurity", "core_all",
                 "hinweise_guard", "rd_sitzungen_ziel_offen_idx", "has_table_privilege('wamo_core', '__SCHEMA__.rd_sitzungen', 'INSERT,UPDATE,DELETE,TRUNCATE')"):
        assert muss in t, muss
    for rolle in ("wamo_admin", "anon", "authenticated", "service_role"):
        assert f"has_function_privilege('{rolle}', '__SCHEMA__.rd_sitzung_beginnen({SIGNATUR})', 'EXECUTE')" in t, rolle
    assert "has_function_privilege('wamo_core'" in t
    assert selbsttest  # der Block steht nach dem Kommentar "Selbsttest"


# ---------- Rückweg ----------

def test_rueckweg_stellt_die_funktion_aus_0015_wortgleich_wieder_her():
    assert _funktion(DOWN) == _funktion(ALT)
    code = _ohne_kommentare(DOWN)
    assert re.findall(r"create\s+(?:or\s+replace\s+)?function\s+(\S+?)\(", code, re.I) == ["__SCHEMA__.rd_sitzung_beginnen"]
    assert "delete from __SCHEMA__.schema_migrations where version = '0019_rd_anmeldung_genuegt'" in code
    assert len(_anweisungen(code, "delete")) == 1  # nur der Versionseintrag, nie Sitzungen oder Hinweise
    assert not re.search(r"\b(drop|truncate)\b", code, re.I)
    assert "set local lock_timeout = '5s'" in code


def test_rueckweg_prueft_folgemigrationen_und_den_stand_danach():
    code = _eine_zeile(_ohne_kommentare(DOWN))
    assert "version > '0019_rd_anmeldung_genuegt'" in code and "wamo.down_trotz_folgemigration" in code
    assert "0019 down: erst die späteren Migrationen zurücknehmen" in code
    for muss in ("not like '%120 seconds%'", "not like '%schon für einen Start verwendet%'", "like '%31 days%'"):
        assert muss in code, muss
    for rolle in ("wamo_admin", "anon", "authenticated", "service_role"):
        assert f"has_function_privilege('{rolle}', '__SCHEMA__.rd_sitzung_beginnen({SIGNATUR})', 'EXECUTE')" in code


def test_rueckweg_kopf_nennt_aufruf_und_reihenfolge():
    kopf = "\n".join(z for z in DOWN.splitlines() if z.startswith("--"))
    assert "render-migration.sh <schema> supabase/migrations/0019_rd_anmeldung_genuegt.down.sql" in kopf
    assert "ON_ERROR_STOP=1 --single-transaction" in kopf
    assert "rueckweg-kern" in kopf and "wortgleich" in kopf


# ---------- Zusammenspiel mit dem Kern ----------

def test_grenze_der_datenbank_liegt_ueber_allem_was_der_kern_zulaesst():
    """Die 31 Tage der Datenbank dürfen nie einen Start ablehnen, den die Zugangskontrolle und der Stufenschalter zulassen."""
    tage31 = 31 * 86400
    assert re.search(r"interval '31 days'", NEU)
    assert auth_gate.ANMELDUNG_MAX_STUNDEN_GRENZE * 3600 + 3600 < tage31
    assert rd_api.STUFE_MAX_MIN * 60 + 3600 < tage31


def test_kern_bildet_23514_auf_die_codeabfrage_ab():
    quelle = Path(rd_api.__file__).read_text(encoding="utf-8")
    assert re.search(r'if code == "23514":\s+return _aal2_abgelehnt\(\)', quelle)


# ---------- SQL-Test ----------

def test_sql_test_ist_eine_zurueckgerollte_transaktion_nie_in_prod_und_prueft_0019():
    code = _ohne_kommentare(TEST)
    assert "\\set ON_ERROR_STOP 1" in code and code.index("begin;") < code.index("select '0019 OK';") < code.rindex("rollback;")
    assert "if '__SCHEMA__' = 'wamo_prod' then raise exception 'Test nie in wamo_prod'" in code
    assert "'0019_rd_anmeldung_genuegt'" in code and "ist in __SCHEMA__ nicht angewendet" in code
    assert "Es laufen echte Fernsitzungen" in code  # Vorbedingung


def test_sql_test_deckt_alle_beschlossenen_faelle_ab():
    code = _eine_zeile(_ohne_kommentare(TEST))
    for muss in (
        "interval '3 days'", "'drei Tage alter Code startet'", "'zweiter Start mit demselben Code'", "'erste Sitzung abgeloest'",
        "interval '10 minutes'", "interval '30 days 23 hours'", "interval '31 days' - interval '1 second'", "interval '90 days'",
        "interval '30 seconds'", "'23514'", "'42501'", "'22004'", "'22023'",
        "'belegt'", "'rate'", "'voll'", "'Hinweis je aktivem Admin (erster Start)'", "'Mitglied und inaktiver Admin bekommen keinen'",
        "'belegt, rate und voll melden nichts'", "'abgelehnte Starts melden nichts'", "wurde vor 72 Stunden eingegeben",
        "wurde vor 10 Minuten eingegeben", "wurde gerade eben eingegeben", "'keine Zugangsdaten und keine Adressen im Text'",
        "'genau eine offene Sitzung je Ziel'", "'zeitlimit'", "hinweis_an_admins", "'wamo_admin nicht'", "'anon nicht'",
        "'authenticated nicht'", "'service_role nicht'", "prosecdef", "search_path=%", "select '0019 OK';",
    ):
        assert muss in code, muss


def test_sql_test_prueft_nach_jedem_abgelehnten_start_dass_nichts_entstand():
    code = _eine_zeile(_ohne_kommentare(TEST))
    assert "'abgelehnte Starts legen nichts an'" in code and "'abgelehnte Starts melden nichts'" in code


def test_der_test_von_0015_kennt_beide_staende():
    """Der Test von 0015 prüft die beiden Sperren nur, solange 0019 nicht angewendet ist (Merker wamo_test.neu19), und lässt dann
    nichts aus: Beide Fälle stehen weiter im Test, nur hinter dem Merker."""
    code = _ohne_kommentare(ALT_TEST)
    assert "set_config('wamo_test.neu19'" in code and "'0019_rd_anmeldung_genuegt'" in code
    assert code.count("current_setting('wamo_test.neu19') = 'nein'") == 2
    for sperre in ("interval '121 seconds'", "interval '100 seconds'"):
        zeile = code.index(sperre)
        davor = code[:zeile]
        assert davor.rindex("current_setting('wamo_test.neu19') = 'nein'") > davor.rindex("end if;") if "end if;" in davor else True, sperre
    assert "'verbrauchter Code lässt die laufende Sitzung unberührt'" in code
    assert "'23514'" in code  # die Zukunft bleibt in beiden Ständen abgelehnt


# ---------- Lexikalische Prüfung der gerenderten Dateien (wie bei 0018) ----------

DATEIEN = {"0019_rd_anmeldung_genuegt.sql": SQL, "0019_rd_anmeldung_genuegt.down.sql": DOWN,
           "0019_rd_anmeldung_genuegt_test.sql": TEST, "0015_rd_sitzungen_test.sql": ALT_TEST}


@pytest.mark.parametrize("name", sorted(DATEIEN))
def test_gerenderte_sql_dateien_sind_lexikalisch_in_ordnung(name):
    gerendert = DATEIEN[name].replace("__SCHEMA__", "wamo_test")
    assert "__SCHEMA__" not in gerendert, name
    anweisungen = _zerlegen(gerendert, streng=True)
    assert anweisungen, name
    for a in anweisungen:
        assert a.split(None, 1)[0].lower() in VERBEN, (name, a[:60])
