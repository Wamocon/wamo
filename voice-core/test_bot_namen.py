"""Kein lokaler Name in bot.py darf ein Modul überdecken, das im Modulkopf importiert wird.

Anlass (11.11.2026, Zusammenführung der Testlabor-Stränge): Ein Strang benannte in build_session eine lokale Variable `labor`, ein
anderer benutzte in derselben Funktion früher das Modul `labor`. Git führte beides ohne Konflikt zusammen, Python macht aus `labor` dann
eine lokale Variable der ganzen Funktion, und jede Sitzung hätte mit UnboundLocalError geendet. Die Einzeltests beider Stränge sahen das
nicht. Dieser Test prüft die Namensräume von bot.py statisch."""
from __future__ import annotations

import ast
from pathlib import Path

BOT = Path(__file__).with_name("bot.py")


def _modulnamen(baum: ast.Module) -> set[str]:
    namen: set[str] = set()
    for knoten in baum.body:
        if isinstance(knoten, ast.Import):
            namen.update((a.asname or a.name).split(".")[0] for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom):
            namen.update(a.asname or a.name for a in knoten.names)
    return namen


def _ziele(ziel: ast.AST) -> set[str]:
    if isinstance(ziel, ast.Name):
        return {ziel.id}
    if isinstance(ziel, (ast.Tuple, ast.List)):
        return set().union(*(_ziele(e) for e in ziel.elts)) if ziel.elts else set()
    if isinstance(ziel, ast.Starred):
        return _ziele(ziel.value)
    return set()


class _Lokale(ast.NodeVisitor):
    """Namen, die eine Funktion selbst bindet (ohne die Rümpfe verschachtelter Funktionen), abzüglich global und nonlocal."""

    def __init__(self) -> None:
        self.gebunden: set[str] = set()
        self.frei: set[str] = set()

    def visit_FunctionDef(self, knoten: ast.FunctionDef) -> None:  # der Name der verschachtelten Funktion ist lokal, ihr Rumpf nicht
        self.gebunden.add(knoten.name)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, knoten: ast.ClassDef) -> None:
        self.gebunden.add(knoten.name)

    def visit_Lambda(self, knoten: ast.Lambda) -> None:
        return None

    def visit_Global(self, knoten: ast.Global) -> None:
        self.frei.update(knoten.names)

    def visit_Nonlocal(self, knoten: ast.Nonlocal) -> None:
        self.frei.update(knoten.names)

    def visit_Assign(self, knoten: ast.Assign) -> None:
        for z in knoten.targets:
            self.gebunden |= _ziele(z)
        self.generic_visit(knoten)

    def visit_AnnAssign(self, knoten: ast.AnnAssign) -> None:
        if knoten.value is not None:
            self.gebunden |= _ziele(knoten.target)
        self.generic_visit(knoten)

    def visit_AugAssign(self, knoten: ast.AugAssign) -> None:
        self.gebunden |= _ziele(knoten.target)
        self.generic_visit(knoten)

    def visit_For(self, knoten: ast.For) -> None:
        self.gebunden |= _ziele(knoten.target)
        self.generic_visit(knoten)

    visit_AsyncFor = visit_For  # type: ignore[assignment]

    def visit_With(self, knoten: ast.With) -> None:
        for e in knoten.items:
            if e.optional_vars is not None:
                self.gebunden |= _ziele(e.optional_vars)
        self.generic_visit(knoten)

    visit_AsyncWith = visit_With  # type: ignore[assignment]

    def visit_NamedExpr(self, knoten: ast.NamedExpr) -> None:
        self.gebunden |= _ziele(knoten.target)
        self.generic_visit(knoten)

    def visit_ExceptHandler(self, knoten: ast.ExceptHandler) -> None:
        if knoten.name:
            self.gebunden.add(knoten.name)
        self.generic_visit(knoten)

    def visit_Import(self, knoten: ast.Import) -> None:
        self.gebunden.update((a.asname or a.name).split(".")[0] for a in knoten.names)

    def visit_ImportFrom(self, knoten: ast.ImportFrom) -> None:
        self.gebunden.update(a.asname or a.name for a in knoten.names)


def _ueberdeckungen() -> list[tuple[str, int, str]]:
    baum = ast.parse(BOT.read_text(encoding="utf-8"))
    module = _modulnamen(baum)
    funde: list[tuple[str, int, str]] = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            besucher = _Lokale()
            for kind in knoten.body:
                besucher.visit(kind)
            parameter = {a.arg for a in [*knoten.args.posonlyargs, *knoten.args.args, *knoten.args.kwonlyargs]}
            parameter |= {a.arg for a in (knoten.args.vararg, knoten.args.kwarg) if a is not None}
            for name in sorted((besucher.gebunden | parameter) - besucher.frei):
                if name in module:
                    funde.append((knoten.name, knoten.lineno, name))
    return funde


def test_keine_funktion_in_bot_py_ueberdeckt_ein_importiertes_modul():
    funde = _ueberdeckungen()
    assert not funde, "lokale Namen überdecken Importe von bot.py: " + ", ".join(f"{f} (Zeile {z}) bindet {n}" for f, z, n in funde)


def test_der_test_erkennt_den_fehler_von_der_zusammenfuehrung():
    """Gegenprobe: derselbe Aufbau wie damals (Modul im Kopf, später lokale Zuweisung in der Funktion) wird gefunden."""
    quelle = "import labor\nasync def build_session():\n    x = labor.ist_labor(1)\n    labor = None\n"
    baum = ast.parse(quelle)
    besucher = _Lokale()
    for kind in baum.body[1].body:
        besucher.visit(kind)
    assert "labor" in besucher.gebunden and "labor" in _modulnamen(baum)
