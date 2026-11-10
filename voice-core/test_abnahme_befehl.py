"""Die gemeinsame Allowlist für Befehlskriterien (abnahme_befehl.py), nur Standardbibliothek.

Das Modul gilt an zwei Orten: im Kern (abnahme.py, Parser) und auf Sokrates-2 (Dispatcher, Befehl pruefen), wo es in
/opt/wamo/waechter ohne Pakete läuft und die Prüfung erneut erzwingt. Geprüft werden die Konsistenz (nur Standardbibliothek,
eine Quelle für beide Orte), die neue Form uv run --no-sync --offline pytest und die strenge Fassung für den Dispatcher
(argv_grund). Die übrige Allowlist deckt test_abnahme.py über den Parser des Kerns ab.

Ausführen:  .venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider test_abnahme_befehl.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import abnahme
import abnahme_befehl as ab

QUELLE = Path(ab.__file__)


# ---------------------------------------------------------------- Konsistenz
def _importierte_module(pfad: Path) -> set[str]:
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    module = {n.module for n in ast.walk(baum) if isinstance(n, ast.ImportFrom) and n.level == 0}
    module |= {a.name for n in ast.walk(baum) if isinstance(n, ast.Import) for a in n.names}
    return {m.split(".")[0] for m in module if m}


def test_das_modul_importiert_nur_die_standardbibliothek():
    """Auf Sokrates-2 liegt es ohne Pakete in /opt/wamo/waechter: loguru, persona, dev_guard und der Rest des Kerns fehlen dort."""
    module = _importierte_module(QUELLE)
    assert module, "ohne Importe läse der Test nichts"
    assert module <= set(sys.stdlib_module_names) | {"__future__"}, module - set(sys.stdlib_module_names)
    for kern in ("loguru", "persona", "dev_guard", "schluessel", "abnahme", "pipecat", "dev_agent"):
        assert kern not in module


def test_der_parser_des_kerns_benutzt_dasselbe_modul():
    assert abnahme._befehl_grund is ab.befehl_grund
    assert abnahme._PROGRAMME is ab.PROGRAMME
    assert abnahme._pfadteile is ab.pfadteile
    assert abnahme._fremdpfad is ab.fremdpfad
    assert abnahme._VERBOTEN is ab.VERBOTEN


def test_abnahme_py_bringt_keine_zweite_allowlist_mit():
    """Die Regeln stehen nur im Modul: abnahme.py importiert sie und definiert keine eigene Regel je Programm."""
    baum = ast.parse(Path(abnahme.__file__).read_text(encoding="utf-8"))
    funktionen = {n.name for n in ast.walk(baum) if isinstance(n, ast.FunctionDef)}
    for regel in ("_pytest", "_python", "_paketmanager", "_node", "_go", "_cargo", "_dotnet", "_make", "_ruff", "_eslint",
                  "_tsc", "_grep", "_zerlegen", "_uv"):
        assert regel not in funktionen, regel


def test_die_obergrenzen_sind_unveraendert():
    assert (ab.MAX_ARGUMENTE, ab.ARGUMENT_ZEICHEN) == (12, 200)
    assert abnahme.MAX_ARGUMENTE == 12 and abnahme.ARGUMENT_ZEICHEN == 200


# ---------------------------------------------------------------- uv run --no-sync --offline pytest
UV = ("uv", "run", "--no-sync", "--offline", "pytest")


@pytest.mark.parametrize("rest", [
    (), ("-q",), ("-q", "-x", "-v"), ("--tb=short",), ("--tb", "no"), ("-k", "foo and not bar"), ("-m", "not slow", "-q"),
    ("tests",), ("tests/test_x.py::TestA::test_b[1-2]",), ("-q", "tests", "tests2"), (".",),
])
def test_uv_run_pytest_ohne_sync_und_ohne_netz_ist_erlaubt(rest):
    assert ab.befehl_grund(UV + rest) is None
    assert ab.befehl_grund(list(UV + rest)) is None  # Liste und Tupel: dasselbe


@pytest.mark.parametrize("argv", [
    # uv in jeder anderen Form
    ("uv",), ("uv", "run"), ("uv", "run", "pytest"), ("uv", "run", "--no-sync", "pytest"), ("uv", "run", "--offline", "pytest"),
    ("uv", "run", "--offline", "--no-sync", "pytest"),  # nur genau diese Reihenfolge
    ("uv", "sync"), ("uv", "pip", "install", "x"), ("uv", "add", "x"), ("uv", "tool", "run", "x"), ("uvx", "pytest"),
    ("uv", "run", "--no-sync", "--offline", "python", "-c", "print(1)"), ("uv", "run", "--no-sync", "--offline", "python"),
    ("uv", "run", "--no-sync", "--offline", "ruff", "check"), ("uv", "run", "--no-sync", "--offline", "sh"),
    ("uv", "run", "--no-sync", "--offline"), ("uv", "run", "--no-sync", "--offline", "pytest", "-p", "plugin"),
    ("uv", "run", "--no-sync", "--offline", "pytest", "-c", "x.ini"), ("uv", "run", "--no-sync", "--offline", "pytest", "--rootdir=x"),
    ("uv", "run", "--no-sync", "--offline", "pytest", "../x"), ("uv", "run", "--no-sync", "--offline", "pytest", "/etc/passwd"),
    ("uv", "run", "--no-sync", "--offline", "pytest", "--with", "evil"), ("uv", "run", "--no-sync", "--offline", "pytest", "--"),
    ("uv", "run", "--no-sync", "--offline", "pytest", "-vv"), ("uv", "run", "--no-sync", "--offline", "pytest", "--co"),
    ("uv", "run", "--no-sync", "--offline", "--with", "x", "pytest"), ("uv", "run", "--no-sync", "--offline", "pytest", "@a"),
    ("uv", "run", "--no-sync", "--offline", "pytest", "//host/x"), ("uv", "run", "--no-sync", "--offline", "pytest", "C:x"),
    ("uv", "run", "--no-sync", "--offline", "pytest", *["-q"] * 9),  # zusammen mehr als 12 Argumente
])
def test_uv_in_jeder_anderen_form_bleibt_verboten(argv):
    assert ab.befehl_grund(argv) is not None, argv


def test_der_hinweis_zu_uv_nennt_die_einzige_form():
    assert "--no-sync --offline pytest" in ab.befehl_grund(("uv", "run", "pytest"))


def test_pfade_mit_uv_pytest_bleiben_im_projektordner_wie_bei_pytest():
    assert ab.befehl_grund(UV + ("tests/..",)) is not None
    assert ab.befehl_grund(UV + ("~/x",)) is not None
    assert ab.befehl_grund(("pytest", "tests/..")) is not None


def test_der_parser_des_kerns_nimmt_die_uv_form_an_und_nur_diese():
    (k,) = abnahme.parse_abnahme(["befehl: uv run --no-sync --offline pytest -q tests"])
    assert k.befehl == ("uv", "run", "--no-sync", "--offline", "pytest", "-q", "tests")
    for schlecht in ("uv run pytest", "uv sync", "uvx pytest", "uv run --no-sync --offline pytest -p x", "uv"):
        with pytest.raises(ValueError):
            abnahme.parse_abnahme([f"befehl: {schlecht}"])


def test_braucht_venv_nur_bei_der_uv_form():
    assert ab.braucht_venv(UV) is True
    assert ab.braucht_venv(UV + ("-q",)) is True
    assert ab.braucht_venv(("pytest", "-q")) is False
    assert ab.braucht_venv(("npm", "test")) is False
    assert ab.braucht_venv(("python", "-m", "pytest")) is False


# ---------------------------------------------------------------- argv_grund: die strenge Fassung für den Dispatcher
@pytest.mark.parametrize("argv", [
    ["pytest", "-q"], ["python3", "-m", "pytest", "-x", "tests"], ["npm", "test"], ["npm", "run", "build"], ["make", "test"],
    ["grep", "-q", "hello world", "README.md"], ["node", "--test"], ["go", "test", "./..."], list(UV), ["eslint", "."],
])
def test_argv_grund_laesst_die_allowlist_zu(argv):
    assert ab.argv_grund(argv) is None


@pytest.mark.parametrize("argv", [
    None, "pytest -q", 5, {}, [], (), [None], [5], [["pytest"]], ["pytest", 5], ["pytest", None], [b"pytest"], [""],
    ["pytest", "-q", b"x"],
])
def test_argv_grund_verlangt_eine_nichtleere_liste_von_texten(argv):
    assert ab.argv_grund(argv) is not None


def test_argv_grund_zaehlt_programm_und_zwoelf_argumente():
    assert ab.argv_grund(["pytest", *["-q"] * 12]) is None
    assert ab.argv_grund(["pytest", *["-q"] * 13]) is not None
    assert ab.argv_grund(["grep", "-q", "x" * 200, "a.txt"]) is None
    assert ab.argv_grund(["grep", "-q", "x" * 201, "a.txt"]) is not None


@pytest.mark.parametrize("zeichen", [";", "&", "|", "`", "$", "<", ">", "\\", "\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85",
                                     " ", " ", "‮", "​", "﻿", "\U000e0041"])
def test_argv_grund_weist_steuerzeichen_und_shell_metazeichen_je_argument_ab(zeichen):
    for stelle in range(3):
        argv = ["grep", "-q", "x", "README.md"]
        argv[stelle + 1] = argv[stelle + 1] + zeichen
        assert ab.argv_grund(argv) is not None, (stelle, repr(zeichen))
    assert ab.argv_grund(["pytest" + zeichen, "-q"]) is not None


@pytest.mark.parametrize("name", ["PYTEST", "Pytest", "pytest.exe", "NPM", "Make", "python3.exe", "/usr/bin/pytest", "./pytest",
                                  "../pytest", "uv.exe", "UV", "bash", "sh", "env", "curl", "git", "pytest "])
def test_argv_grund_nimmt_nur_den_exakten_programmnamen(name):
    """Der Parser des Kerns toleriert Groß- und Kleinschreibung und .exe (Windows); der Dispatcher läuft unter Linux und nicht."""
    assert ab.argv_grund([name, "-q"]) is not None
    assert ab.argv_grund(["python", "-m", "pytest"]) is None  # Gegenprobe: der exakte Name geht


def test_der_parser_des_kerns_bleibt_bei_gross_und_kleinschreibung_und_exe_tolerant():
    assert ab.befehl_grund(("PYTEST.EXE", "-q")) is None
    assert ab.befehl_grund(("Python3", "-m", "pytest")) is None


def test_argv_grund_fuehrt_die_regeln_je_programm_weiter():
    for argv in (["pytest", "-p", "plugin"], ["npm", "install"], ["python", "-c", "x"], ["make", "all"], ["go", "run", "x.go"],
                 ["grep", "-q", "x", "../y"], ["pytest", "/etc/passwd"], ["pytest", "@a"], ["pytest", "//h/x"]):
        assert ab.argv_grund(argv) is not None, argv


def test_argv_grund_und_befehl_grund_stimmen_bei_gueltigem_argv_ueberein():
    for argv in (["pytest", "-q"], ["npm", "test"], list(UV)):
        assert ab.argv_grund(argv) is None and ab.befehl_grund(argv) is None


def test_meldungen_sind_kurz_einzeilig_und_ohne_steuerzeichen():
    boese = "pytest" + "\x1b[31m" * 40 + "‮"
    for argv in ([boese], ["pytest", "-" + "x" * 500], ["grep", "-q", "x y"], ["pytest", "-p\x00x"]):
        grund = ab.argv_grund(argv)
        assert grund and "\n" not in grund and "\x1b" not in grund and "\x00" not in grund and len(grund) < 300, argv


# ---------------------------------------------------------------- unittest: nur Modulpfade, Klassen und Testmethoden
@pytest.mark.parametrize("name", [
    "discover", "tests", "test_x", "tests.test_x", "tests.unit.test_x", "test_x.TestA", "test_x.TestA.test_b", "tests.test_x.TestA.testB",
    "tests.unit.api.test_x.TestA.test_b", "tests.test_x.TestA",
])
def test_unittest_erlaubt_modulpfade_klassen_und_testmethoden(name):
    assert ab.befehl_grund(("python", "-m", "unittest", name)) is None, name
    assert ab.argv_grund(["python", "-m", "unittest", "-v", name]) is None, name


@pytest.mark.parametrize("name", [
    # ein aufrufbares Ziel am Ende: unittest ruft es ohne Argumente auf, sys.exit beendet den Lauf mit 0 und ohne Test
    "test_x.sys.exit", "tests.sys.exit", "test_x.exit", "test_x.quit", "test_x.os._exit", "test_x.os.getcwd", "test_x.TestA.exit",
    "test_x.TestA.setUp", "tests.unit.sys.exit", "test_x.sys", "tests.unit", "test_x.os.abort",
    # nicht Test-Module der Allowlist
    "os.getcwd", "sys.exit", "test.support", "tmp.x", "unittest.main",
])
def test_unittest_ruft_kein_aufrufbares_ziel_ohne_test_auf(name):
    assert ab.befehl_grund(("python", "-m", "unittest", name)) is not None, name
    assert ab.argv_grund(["python", "-m", "unittest", name]) is not None, name


def test_unittest_mit_einem_punktierten_namen_wie_sys_exit_laeuft_mit_exit_0_ohne_test(tmp_path):
    """Der Anlass der Verschärfung, gemessen: Der Name test_x.sys.exit läuft mit Exit 0, ohne dass ein Test lief. Die Allowlist
    lässt ihn deshalb nicht zu (siehe oben)."""
    import subprocess

    (tmp_path / "test_x.py").write_text("import sys\n", encoding="utf-8")
    p = subprocess.run([sys.executable, "-m", "unittest", "test_x.sys.exit"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "Ran" not in p.stderr
    assert ab.befehl_grund(("python", "-m", "unittest", "test_x.sys.exit")) is not None


# ---------------------------------------------------------------- lokal läuft die uv-Form nie
@pytest.mark.parametrize("name", ["uv", "UV", "uv.exe", "Uv.EXE"])
async def test_der_lokale_ausfuehrer_startet_uv_nie(tmp_path, name):
    """Lokal gibt es keine Sandbox, und uv führte pytest aus der .venv des Projektordners aus: dort startet nichts."""
    from unittest import mock

    argv = (name, *UV[1:])
    with mock.patch.object(abnahme.asyncio, "create_subprocess_exec", side_effect=AssertionError("nichts starten")), \
         mock.patch.object(abnahme.shutil, "which", side_effect=AssertionError("nicht einmal suchen")):
        rc, aus = await abnahme.befehl_ausfuehren(argv, tmp_path)
    assert rc is None and "nur im Dispatcher" in aus and "nicht ausgeführt" in aus
