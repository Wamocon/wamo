"""Prüft Migration 0011 (Modellwahl und Claude-Code-Token) als Text: Regeln aus CLAUDE.md für Migrationen und die
Rechte der neuen Spalten. Die Wirkung in der Datenbank prüft supabase/tests/0011_modellmodus_test.sql gegen wamo_test."""

from __future__ import annotations

import re
from pathlib import Path

import modellzugang

WURZEL = Path(__file__).resolve().parent.parent / "supabase"
SQL = (WURZEL / "migrations" / "0011_modellmodus_und_codetoken.sql").read_text(encoding="utf-8")
DOWN = (WURZEL / "migrations" / "0011_modellmodus_und_codetoken.down.sql").read_text(encoding="utf-8")
TEST = (WURZEL / "tests" / "0011_modellmodus_test.sql").read_text(encoding="utf-8")
NEUE_SPALTEN = ("modell_modus", "claude_code_token", "claude_code_token_ende", "claude_code_token_at")


def _ohne_kommentare(text: str) -> str:
    return "\n".join(z.split("--", 1)[0] for z in text.splitlines())


def _anweisungen(text: str, wort: str) -> list[str]:
    return [a.strip() for a in re.split(r";\s*\n", _ohne_kommentare(text)) if re.match(rf"\s*{wort}\b", a, re.I)]


def test_schema_platzhalter_und_keine_festen_schemas():
    for text in (SQL, DOWN, TEST):
        code = _ohne_kommentare(text)
        assert "__SCHEMA__" in code
        assert not re.search(r"\bwamo_(dev|test|prod)\.", code)


def test_spalten_idempotent_mit_pruefungen():
    code = _ohne_kommentare(SQL)
    for spalte in NEUE_SPALTEN:
        assert re.search(rf"add column if not exists {spalte}\b", code), spalte
    assert "check (modell_modus in ('automatisch', 'nur_haus'))" in code
    assert "default 'automatisch'" in code
    assert "model_access_nur_haus_ohne_codetoken" in code and "model_access_codetoken_vollstaendig" in code
    assert "drop constraint if exists model_access_codetoken_vollstaendig" in code  # zweiter Lauf bricht nicht ab
    assert "to_regclass('__SCHEMA__.model_access') is null" in code  # braucht 0010
    assert "on conflict do nothing" in code


def test_rechte_nur_der_kern_und_nur_spaltenweise():
    code = _ohne_kommentare(SQL)
    for grant in _anweisungen(code, "grant"):
        assert re.search(r"\((?:[a-z_]+,\s*)*[a-z_]+\)\s+on __SCHEMA__\.model_access to wamo_core$", grant, re.I | re.S), grant
        assert "wamo_admin" not in grant and "anon" not in grant and "service_role" not in grant and "public" not in grant.lower()
        assert not re.search(r"\bdelete\b|\ball\b|\btruncate\b", grant, re.I)
    gewaehrt = {m for g in _anweisungen(code, "grant") for m in re.findall(r"\b[a-z_]+\b", g.split("on __SCHEMA__")[0])}
    assert set(NEUE_SPALTEN) <= gewaehrt
    revoke = " ".join(_anweisungen(code, "revoke"))
    for rolle in ("public", "anon", "authenticated", "service_role", "wamo_admin"):
        assert rolle in revoke


def test_wachter_deckt_den_token_und_nur_haus_ab():
    code = _ohne_kommentare(SQL)
    assert "create or replace function __SCHEMA__.model_access_guard()" in code
    assert "set search_path = ''" in code
    assert "wamo_admin darf keinen Claude-Code-Token setzen" in code
    assert "wamo_admin darf einen Claude-Code-Token nicht ändern" in code
    assert "new.claude_code_token := null" in code  # nur_haus räumt den Token
    # die Schutzregeln von 0010 bleiben erhalten
    assert "wamo_admin darf keinen Claude-Schlüssel setzen" in code


def test_selbsttest_prueft_jedes_recht_und_jede_spalte_einzeln():
    code = _ohne_kommentare(SQL)
    assert "raise exception 'model_access (0011)" in code
    for spalte in NEUE_SPALTEN:
        assert f"'{spalte}'" in code
    assert "array['SELECT', 'INSERT', 'UPDATE']" in code  # nie 'SELECT,INSERT,UPDATE' in einem Aufruf (wäre schon bei einem wahr)
    assert "'SELECT,INSERT,UPDATE'" not in code
    assert "array['wamo_admin', 'anon', 'authenticated', 'service_role']" in code
    assert "not has_column_privilege('wamo_core'" in code  # der Kern braucht jedes Recht
    assert "tgname = 'model_access_guard'" in code and "position('claude_code_token' in p.prosrc)" in code


def test_wachter_prueft_auch_das_leeren_durch_die_verwaltung():
    code = _ohne_kommentare(SQL)
    assert "old.claude_code_token is not null" in code and "old.claude_schluessel is not null" in code


def test_sperre_gegen_haengende_migration():
    assert "set local lock_timeout" in _ohne_kommentare(SQL) and "set local lock_timeout" in _ohne_kommentare(DOWN)


def test_down_entzieht_dem_wahl_nur_haus_den_schluessel_statt_ihn_wieder_zu_aktivieren():
    code = _ohne_kommentare(DOWN)
    update = code.index("set claude_schluessel = null")
    assert update < code.index("drop column if exists modell_modus")
    assert "modell_modus = 'nur_haus'" in code[update:update + 200]


def test_down_prod_sperre_und_vollstaendig():
    code = _ohne_kommentare(DOWN)
    assert "'__SCHEMA__' = 'wamo_prod'" in code and "wamo.allow_drop" in code
    for spalte in NEUE_SPALTEN:
        assert f"drop column if exists {spalte}" in code
    assert "delete from __SCHEMA__.schema_migrations where version = '0011_modellmodus_und_codetoken'" in code
    assert "drop constraint if exists model_access_nur_haus_ohne_codetoken" in code
    # der Wächter geht zurück auf den Stand von 0010 (ohne Token)
    assert "claude_code_token" not in code.split("create or replace function")[1].split("$$;")[0] if "$$;" in code else True


def test_sql_test_laeuft_in_einer_zurueckgerollten_transaktion_und_nie_in_prod():
    assert "begin;" in TEST and re.search(r"^rollback;", TEST, re.M) and "Test nie in wamo_prod" in TEST
    assert "0011 OK" in TEST


def test_kern_benutzt_nur_spalten_die_die_migration_anlegt():
    """Jede Spalte, die modellzugang.py für 0011 liest oder schreibt, kommt in der Migration vor."""
    quelle = Path(modellzugang.__file__).read_text(encoding="utf-8")
    benutzt = set(re.findall(r"\b(modell_modus|claude_code_token(?:_ende|_at)?)\b", quelle))
    assert benutzt <= set(NEUE_SPALTEN)
    assert benutzt == set(NEUE_SPALTEN)
    # und er schreibt nie in Spalten, auf die wamo_core kein Recht hat
    inserts = re.findall(r"insert into \{db\.t\('model_access'\)\} \((user_id[^)]*)\)", quelle)
    assert len(inserts) >= 3  # API-Schlüssel, Claude-Code-Token, Wahl
    erlaubt = {"user_id", "claude_schluessel", "claude_schluessel_ende", "claude_workspace", "claude_geprueft_at",
               *NEUE_SPALTEN}
    for spalten in inserts:
        spalten = re.sub(r'"\s*"', "", spalten)  # zusammengesetzte Zeichenketten im Quelltext
        assert {s.strip() for s in spalten.split(",")} <= erlaubt, spalten
