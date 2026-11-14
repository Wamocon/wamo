"""Tests für scripts/wamo_migrationen_pruefen.py: Migrations-Lint auf die Änderungen gegenüber einer Basis (Job Migrationen in ci.yml).

Jeder Test baut ein kleines Git-Repository im Temp-Ordner, kein Netz, keine Datenbank.
Aufruf (aus voice-core, dort liegt die Python-Umgebung):  uv run python -m pytest ../scripts/test_wamo_migrationen_pruefen.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_migrationen_pruefen as mp  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git ist nicht installiert")

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
GUT_DOWN = KOPF + "drop table if exists __SCHEMA__.beispiel;\n"
ORDNER = "supabase/migrations"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                           "-c", "commit.gpgsign=false", *args], check=True, capture_output=True, text=True).stdout.strip()


def _schreibe(repo: Path, name: str, text: str) -> None:
    pfad = repo / ORDNER / name
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(text.encode("utf-8"))


def _commit(repo: Path, titel: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", titel)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """Repository mit einer vorhandenen Migration samt Rückweg; liefert Pfad und Basis-Commit."""
    _git(tmp_path, "init", "-q")
    _schreibe(tmp_path, "0001_basis.sql", "-- Basis\n")
    _schreibe(tmp_path, "0001_basis.down.sql", "-- Rückweg\n")
    (tmp_path / "README.md").write_bytes(b"Test\n")
    return tmp_path, _commit(tmp_path, "chore: Basis")


def _lauf(repo: Path, basis: str, capsys) -> tuple[int, str]:
    code = mp.main(["--basis", basis, "--repo", str(repo)])
    aus = capsys.readouterr()
    return code, aus.out + aus.err


def test_ohne_geaenderte_migration_gibt_es_nichts_zu_pruefen(repo, capsys):
    pfad, basis = repo
    (pfad / "README.md").write_bytes(b"Neu\n")
    _commit(pfad, "docs: Text")
    code, aus = _lauf(pfad, basis, capsys)
    assert code == 0
    assert "nichts zu prüfen" in aus


def test_gute_neue_migration_mit_rueckweg_besteht(repo, capsys):
    pfad, basis = repo
    _schreibe(pfad, "0002_beispiel.sql", GUT)
    _schreibe(pfad, "0002_beispiel.down.sql", GUT_DOWN)
    _commit(pfad, "feat: Tabelle")
    code, aus = _lauf(pfad, basis, capsys)
    assert code == 0, aus
    assert "2 Dateien geprüft" in aus and "keine Treffer" in aus


def test_geaenderte_vorhandene_migration_wird_gemeldet(repo, capsys):
    pfad, basis = repo
    _schreibe(pfad, "0001_basis.sql", "-- Basis, nachträglich geändert\n")
    _commit(pfad, "fix: Migration angepasst")
    code, aus = _lauf(pfad, basis, capsys)
    assert code == 1
    assert "supabase/migrations/0001_basis.sql:0: migration_veraendert" in aus


def test_neue_migration_ohne_rueckweg_wird_gemeldet(repo, capsys):
    pfad, basis = repo
    _schreibe(pfad, "0002_beispiel.sql", GUT)
    _commit(pfad, "feat: Tabelle ohne Rückweg")
    code, aus = _lauf(pfad, basis, capsys)
    assert code == 1
    assert "down_fehlt" in aus


def test_treffer_nennen_nie_den_gefundenen_text(repo, capsys):
    pfad, basis = repo
    geheim = "drop table __SCHEMA__.geheimer_tabellenname_xyz;\n"
    _schreibe(pfad, "0002_weg.sql", KOPF + geheim)
    _schreibe(pfad, "0002_weg.down.sql", GUT_DOWN)
    _commit(pfad, "feat: weg")
    code, aus = _lauf(pfad, basis, capsys)
    assert code == 1
    assert "contract_drop" in aus
    assert "geheimer_tabellenname_xyz" not in aus


def test_basis_ist_die_merge_basis(repo, capsys):
    """Was nach dem Abzweig auf der Basis dazukommt, zählt nicht als Änderung des Zweigs."""
    pfad, _ = repo
    _git(pfad, "checkout", "-q", "-b", "zweig")
    _schreibe(pfad, "0002_beispiel.sql", GUT)
    _schreibe(pfad, "0002_beispiel.down.sql", GUT_DOWN)
    _commit(pfad, "feat: Tabelle")
    _git(pfad, "checkout", "-q", "-")
    _schreibe(pfad, "0001_basis.sql", "-- auf der Basis geändert\n")
    hauptstand = _commit(pfad, "chore: Basis weiter")
    _git(pfad, "checkout", "-q", "zweig")
    code, aus = _lauf(pfad, hauptstand, capsys)
    assert code == 0, aus
    assert "migration_veraendert" not in aus


def test_unbekannte_basis_ist_ein_bedienfehler(repo, capsys):
    pfad, _ = repo
    code, aus = _lauf(pfad, "gibt-es-nicht", capsys)
    assert code == 2
    assert "gibt-es-nicht" in aus


def test_auf_github_kommt_je_treffer_eine_anmerkung(repo, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    pfad, basis = repo
    _schreibe(pfad, "0002_beispiel.sql", GUT)
    _commit(pfad, "feat: Tabelle ohne Rückweg")
    code, aus = _lauf(pfad, basis, capsys)
    assert code == 1
    assert "::error file=supabase/migrations/0002_beispiel.sql,line=1::Migrations-Lint: down_fehlt" in aus


def test_keine_gedankenstriche_im_skript():
    text = Path(mp.__file__).read_text(encoding="utf-8")
    assert chr(0x2014) not in text and chr(0x2013) not in text
