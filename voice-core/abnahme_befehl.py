"""Die Allowlist für Befehlskriterien (befehl:) der Abnahme. Nur Standardbibliothek, keine Kernabhängigkeit.

Ein Ort für die Regeln, zwei Verwender:

  * voice-core/abnahme.py (Parser im Kern): befehl_grund prüft, ob ein Befehl als Kriterium taugt.
  * der Auftragsdispatcher auf Sokrates-2 (infra/sokrates2/wamo_job_dispatch.py, Befehl pruefen): Er lädt eine Kopie aus
    /opt/wamo/waechter, ohne Pakete, und erzwingt die Prüfung ERNEUT mit argv_grund. Der Kern ist für den Dispatcher nicht
    vertrauenswürdig, deshalb zählt dort nur die eigene Prüfung.

Deshalb importiert dieses Modul nur die Standardbibliothek (kein loguru, kein persona, kein dev_guard); ein Test sichert das.
Wie die Wächter-Kopien (dev_guard.py, werkzeugkarte.py) wird es mit wamo-job-einrichten.sh nach /opt/wamo/waechter kopiert.

Die Regeln sind eine echte Allowlist je Programm: erlaubt ist nur, was ausdrücklich aufgeführt ist, jede andere Option und jedes
andere Argument wird abgewiesen (zum Beispiel pytest -p oder -c, go test -exec, node --import, dotnet -p:..., cargo --config).
Höchstens 12 Argumente zu je höchstens 200 Zeichen, keine @datei-Argumente, keine Netzwerkpfade, Laufwerke und Adressen, keine
Pfade außerhalb des Projektordners, keine Interpreter für freien Code (python -c, node app.js), kein Netz-, Installations- oder
Veröffentlichungsbefehl. uv ist nur in einer einzigen Form erlaubt: uv run --no-sync --offline pytest <pytest-Optionen>.

Die Allowlist ist eine Vorauswahl, keine Grenze: Gepflanzter Projektcode (conftest.py, Makefile, Skripte in package.json) läuft
bei jedem erlaubten Programm mit. Die Grenze ist die Umgebung, in der der Befehl läuft (auf Sokrates-2 die Sandbox ohne Netz).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

MAX_ARGUMENTE = 12
ARGUMENT_ZEICHEN = 200

# Zeichen für Verkettung, Umleitung, Ersetzung und Steuerung: auch ohne Shell ein Zeichen, das in einem Argument nichts zu suchen hat
VERBOTEN = re.compile(r"[;&|`$<>\\]")
_VERLAESST = re.compile(r"(?:^|[/=])\.\.(?:/|$)|(?:^|=)~")
_SKRIPT = re.compile(r"(?:test|build|lint|typecheck|check|verify)(?::[a-z0-9_-]{1,30})?")
# Netzwerkpfad (// oder \\), Adresse (://) und Laufwerksangabe (C:, auch c:x): Path.exists() darauf löst unter Windows eine
# SMB-Verbindung aus. "::" (pytest-Knoten) und Namen wie a:b mitten im Wort sind keine Laufwerke.
_FREMDPFAD = re.compile(r"//|\\\\|://|(?<![A-Za-z0-9_])[A-Za-z]:(?!:)")
_PFADZEICHEN = re.compile(r"[A-Za-z0-9_.+\-/]+")
_KNOTEN = re.compile(r"[A-Za-z0-9_.\-\[\]]+")
# unittest ruft jedes aufrufbare Ziel ohne Argumente auf (os.getcwd, sys.exit; test_x.sys.exit lief gemessen mit Exit 0 und ohne
# Test): erlaubt sind nur discover und Testnamen, die mit tests oder test_ beginnen (nicht das test-Paket der Standardbibliothek).
# Danach folgen nur Pakete und Module (klein geschrieben, nur mitten im Namen), Klassen (groß geschrieben) und Namen, die mit
# test beginnen. Der letzte Teil ist so nie ein beliebiges aufrufbares Ziel wie exit oder getcwd. Grenze: test_x.sys.test_y
# passt formal und scheitert beim Laden (kein Attribut), es läuft nie mit Exit 0.
_TESTWURZEL = re.compile(r"tests|test_[A-Za-z0-9_]+")
_TESTKLASSE = re.compile(r"[A-Z][A-Za-z0-9_]*")
_TESTFALL = re.compile(r"test[A-Za-z0-9_]*")
_TESTPAKET = re.compile(r"[a-z][a-z0-9_]*")
_TESTSEGMENTE_MAX = 12


def _testname(text: str) -> bool:
    """Ist text ein erlaubter unittest-Name? Segmentweise geprüft (linear): eine einzige verschachtelte Regex mit
    überlappenden Alternativen hätte bei langen Namen exponentielle Laufzeit (ReDoS)."""
    if text == "discover":
        return True
    teile = text.split(".")
    if len(teile) > _TESTSEGMENTE_MAX or _TESTWURZEL.fullmatch(teile[0]) is None:
        return False
    for i, teil in enumerate(teile[1:], start=1):
        letzter = i == len(teile) - 1
        if _TESTKLASSE.fullmatch(teil) or _TESTFALL.fullmatch(teil):
            continue
        if not letzter and _TESTPAKET.fullmatch(teil):  # Pakete und Module nur mitten im Namen
            continue
        return False
    return True
_GO_PAKET = re.compile(r"\.(?:/[A-Za-z0-9_.+\-]+)*(?:/\.\.\.)?")
# Was Unicode "unsichtbar" nennt: Steuer-, Format-, Ersatz-, private und nicht vergebene Zeichen, Zeilen- und Absatztrenner
_UNSICHTBAR = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def hat_steuerzeichen(text: str) -> bool:
    """Steht in text ein Steuer-, Format- oder unsichtbares Zeichen (auch Zeilen- und Absatztrenner, Tag-Zeichen)?"""
    return any(unicodedata.category(c) in _UNSICHTBAR for c in text)


def _kurz(text: object, grenze: int) -> str:
    """Text für eine Meldung: eine Zeile, ohne unsichtbare Zeichen, gekürzt."""
    sauber = " ".join("".join(" " if unicodedata.category(c) in _UNSICHTBAR else c for c in str(text)).split())
    return sauber if len(sauber) <= grenze else sauber[:grenze - 1].rstrip() + "…"


def _wert(muster: str) -> Callable[[str], bool]:
    regel = re.compile(muster)
    return lambda text: regel.fullmatch(text) is not None


@dataclass(frozen=True)
class _Wert:
    """Eine Option mit Wert: gueltig prüft den Wert, gleich erlaubt auch die Form --option=wert (sonst nur getrennt)."""

    gueltig: Callable[[str], bool]
    gleich: bool = False


def _pfad(arg: str) -> bool:
    """Ein relativer Pfad im Projekt: schlichte Zeichen, nicht absolut, nicht mit - beginnend, kein .. als Teil."""
    return _PFADZEICHEN.fullmatch(arg) is not None and not arg.startswith(("-", "/")) and ".." not in arg.split("/")


def _pytest_ziel(arg: str) -> bool:
    pfad, *knoten = arg.split("::")
    return _pfad(pfad) and all(_KNOTEN.fullmatch(k) for k in knoten)


def _go_paket(arg: str) -> bool:
    return _GO_PAKET.fullmatch(arg) is not None and ".." not in arg.split("/")


def _json_pfad(arg: str) -> bool:
    return _pfad(arg) and arg.endswith(".json")


def _zerlegen(args: Sequence[str], *, flags: Iterable[str] = (), werte: dict[str, _Wert] | None = None,
              kurz: str = "") -> tuple[str | None, list[str]]:
    """Zerlegt Argumente streng nach einer ausdrücklichen Liste: flags (ohne Wert), werte (Option mit geprüftem Wert), kurz
    (Buchstaben, die als -abc zusammengesetzt sein dürfen). Jede andere Option ist ein Fehler. Gibt (Fehler, Positionsargumente)."""
    flags, werte, positionen, i = frozenset(flags), werte or {}, [], 0
    while i < len(args):
        arg = args[i]
        i += 1
        if not arg.startswith("-"):
            positionen.append(arg)
            continue
        if arg in flags or (kurz and len(arg) > 1 and arg[1] != "-" and all(c in kurz for c in arg[1:])):
            continue
        name, gleich, wert = arg.partition("=")
        regel = werte.get(name)
        if regel is None or (gleich and not regel.gleich):
            return f"Option {_kurz(arg, 40)} ist als Kriterium nicht erlaubt", []
        if not gleich:
            if i >= len(args):
                return f"Option {name} braucht einen Wert", []
            wert, i = args[i], i + 1
        if not regel.gueltig(wert):
            return f"Der Wert für {name} ist nicht erlaubt", []
    return None, positionen


def _nur(positionen: Iterable[str], gueltig: Callable[[str], bool], erlaubt: str) -> str | None:
    for p in positionen:
        if not gueltig(p):
            return f"Argument {_kurz(p, 40)} ist nicht erlaubt (erlaubt: {erlaubt})"
    return None


def _mit_positionen(args: Sequence[str], gueltig: Callable[[str], bool], erlaubt: str, *, flags: Iterable[str] = (),
                    werte: dict[str, _Wert] | None = None, kurz: str = "") -> str | None:
    fehler, positionen = _zerlegen(args, flags=flags, werte=werte, kurz=kurz)
    return fehler or _nur(positionen, gueltig, erlaubt)


_RELATIV = "relative Pfade im Projektordner"
_AUSDRUCK = _Wert(_wert(r"[A-Za-z0-9_(][A-Za-z0-9_ .()-]{0,99}"))  # -k und -m: Namen mit and, or, not, sonst nichts


def _pytest(args: Sequence[str]) -> str | None:
    return _mit_positionen(args, _pytest_ziel, "Testpfade relativ im Projektordner", flags=("-q", "-x", "-v"),
                           werte={"-k": _AUSDRUCK, "-m": _AUSDRUCK, "--tb": _Wert(_wert("short|line|no"), True)})


# uv ist nur in dieser einen Form erlaubt: ohne Synchronisation (kein Installieren), ohne Netz, und das Programm ist pytest mit
# denselben Optionen wie oben. Alles andere an uv (sync, pip, add, tool, run mit anderem Programm, --with, uvx) bleibt verboten.
UV_VORSPANN = ("run", "--no-sync", "--offline", "pytest")


def _uv(args: Sequence[str]) -> str | None:
    if tuple(args[:len(UV_VORSPANN)]) != UV_VORSPANN:
        return f"uv nur als uv {' '.join(UV_VORSPANN)} mit den Optionen von pytest"
    return _pytest(args[len(UV_VORSPANN):])


def braucht_venv(argv: Sequence[str]) -> bool:
    """Ist es die uv-Form? Sie läuft nur, wenn im Projektordner schon eine .venv liegt (uv legt hier nichts an, es lädt nichts)."""
    return tuple(argv[:1 + len(UV_VORSPANN)]) == ("uv", *UV_VORSPANN)


def _unittest(args: Sequence[str]) -> str | None:
    return _mit_positionen(args, _testname, "discover oder Testnamen tests... und test_...", flags=("-v", "-q", "-f", "-b"),
                           werte={"-s": _Wert(_pfad), "-p": _Wert(_wert(r"[A-Za-z0-9_*?.][A-Za-z0-9_*?.\-]{0,59}"))})


def _mypy(args: Sequence[str]) -> str | None:
    return _mit_positionen(args, _pfad, _RELATIV, flags=("--strict", "--ignore-missing-imports"))


def _compileall(args: Sequence[str]) -> str | None:
    return _mit_positionen(args, _pfad, _RELATIV, flags=("-q",))


_PYTHON_MODULE: dict[str, Callable[[Sequence[str]], str | None]] = {
    "pytest": _pytest, "unittest": _unittest, "mypy": _mypy, "compileall": _compileall,
}


def _python(args: Sequence[str]) -> str | None:
    if len(args) >= 2 and args[0] == "-m" and args[1] in _PYTHON_MODULE:
        return _PYTHON_MODULE[args[1]](args[2:])
    return f"Python nur als python -m {', '.join(_PYTHON_MODULE)}"


_NACHLAUF = frozenset({"--run", "--ci", "--watchAll=false", "--passWithNoTests"})


def _paketmanager(args: Sequence[str]) -> str | None:
    if args[:1] == ("test",):
        rest = args[1:]
    elif args[:1] == ("run",) and len(args) > 1 and _SKRIPT.fullmatch(args[1]):
        rest = args[2:]
    else:
        return "npm, pnpm und yarn nur mit test oder run test, build, lint, typecheck, check, verify"
    if rest and not (rest[0] == "--" and len(rest) > 1 and all(a in _NACHLAUF for a in rest[1:])):
        return f"Nach dem Skript nur -- mit {', '.join(sorted(_NACHLAUF))}"
    return None


def _node(args: Sequence[str]) -> str | None:
    if args[:1] != ("--test",):
        return "node nur mit --test"
    return _nur(args[1:], _pfad, "Testpfade relativ im Projektordner")


def _go(args: Sequence[str]) -> str | None:
    unterbefehl, rest = args[:1], args[1:]
    if unterbefehl == ("test",):
        flags: tuple[str, ...] = ("-v", "-short", "-failfast")
        werte = {"-run": _Wert(_wert(r"[A-Za-z0-9_^(.][A-Za-z0-9_./^*+?()\-]{0,99}"), True), "-count": _Wert(_wert("[0-9]{1,3}"), True)}
    elif unterbefehl == ("build",):
        flags, werte = ("-v",), {}
    elif unterbefehl == ("vet",):
        flags, werte = (), {}
    else:
        return "go nur mit test, vet, build"
    return _mit_positionen(rest, _go_paket, "./... oder ein relativer Paketpfad", flags=flags, werte=werte)


def _cargo(args: Sequence[str]) -> str | None:
    if args[:1] not in (("test",), ("check",), ("build",), ("clippy",)):
        return "cargo nur mit test, check, build, clippy"
    fehler, positionen = _zerlegen(args[1:], flags=("--workspace", "--all-targets", "--locked", "--offline", "--release",
                                                     "--lib", "-q", "--quiet"))
    if fehler:
        return fehler
    if positionen and not (args[0] == "test" and len(positionen) == 1 and _wert(r"[A-Za-z0-9_:]{1,100}")(positionen[0])):
        return "Argumente nur bei cargo test, und dort ein einzelner Testname"
    return None


def _dotnet(args: Sequence[str]) -> str | None:
    if args[:1] not in (("test",), ("build",)):
        return "dotnet nur mit test oder build"
    fehler, positionen = _zerlegen(args[1:], flags=("--no-restore", "--nologo"), werte={
        "-c": _Wert(_wert("Debug|Release")), "--configuration": _Wert(_wert("Debug|Release"), True)})
    if fehler or len(positionen) > 1:
        return fehler or "dotnet nur mit einem Projekt oder einer Lösung"
    return _nur(positionen, _pfad, "ein relativer Pfad im Projektordner")


def _make(args: Sequence[str]) -> str | None:
    return None if len(args) == 1 and args[0] in ("test", "check", "lint", "build") else "make nur mit test, check, lint oder build"


def _ruff(args: Sequence[str]) -> str | None:
    if args[:1] != ("check",):
        return "ruff nur mit check"
    return _mit_positionen(args[1:], _pfad, _RELATIV, flags=("--no-cache", "-q", "--quiet"))  # kein --fix, kein --add-noqa


def _eslint(args: Sequence[str]) -> str | None:
    return _mit_positionen(args, _pfad, _RELATIV, flags=("--quiet",),
                           werte={"--max-warnings": _Wert(_wert("[0-9]{1,4}"), True)})  # kein --fix, keine Plugins


def _tsc(args: Sequence[str]) -> str | None:
    if "--noEmit" not in args:
        return "tsc nur mit --noEmit (prüfen, nichts schreiben)"
    fehler, positionen = _zerlegen(args, flags=("--noEmit",), werte={"-p": _Wert(_json_pfad), "--project": _Wert(_json_pfad, True)})
    return fehler or ("tsc ohne Dateiargumente, nur --noEmit und -p mit relativer tsconfig" if positionen else None)


def _grep(args: Sequence[str]) -> str | None:
    fehler, positionen = _zerlegen(args, kurz="qcrniEFwlsv")  # nur Kurzschalter ohne Dateizugriff; kein -R (folgt Links)
    if fehler:
        return fehler
    if not positionen:
        return "grep braucht ein Suchmuster"
    return _nur(positionen[1:], _pfad, "Dateien relativ im Projektordner")


PROGRAMME: dict[str, Callable[[Sequence[str]], str | None]] = {
    "pytest": _pytest, "python": _python, "python3": _python, "npm": _paketmanager, "pnpm": _paketmanager,
    "yarn": _paketmanager, "node": _node, "go": _go, "cargo": _cargo, "dotnet": _dotnet, "make": _make, "ruff": _ruff,
    "mypy": _mypy, "tsc": _tsc, "eslint": _eslint, "grep": _grep, "uv": _uv,
}


def pfadteile(arg: str) -> set[str]:
    """Die Stellen eines Arguments, an denen ein Pfad stehen kann: das ganze Argument, der Wert nach = und der Wert direkt
    hinter einer Kurzoption (-fPFAD)."""
    kurz = arg[2:] if arg.startswith("-") and not arg.startswith("--") else ""
    return {teil for teil in (arg, arg.partition("=")[2], kurz) if teil}


def fremdpfad(arg: str) -> bool:
    return any(_FREMDPFAD.search(teil) for teil in pfadteile(arg))


def befehl_grund(argv: Sequence[str]) -> str | None:
    """Warum ein Befehl nicht als Kriterium taugt, oder None. Prüft nur Form und Allowlist (der Wächter folgt später). Der
    Programmname gilt ohne Rücksicht auf Groß- und Kleinschreibung und ohne .exe (Windows im Kern); der Dispatcher prüft strenger."""
    name = argv[0]
    if "/" in name:
        return "Programm nur mit Namen, ohne Pfad"
    regel = PROGRAMME.get(name.lower().removesuffix(".exe"))
    if regel is None:
        return f"Programm {_kurz(name, 30)} ist als Kriterium nicht erlaubt (erlaubt: {', '.join(sorted(PROGRAMME))})"
    args = tuple(argv[1:])  # die Regeln vergleichen Anfänge mit Tupeln (args[:1] == ("test",)): eine Liste ergäbe nie einen Treffer
    if len(args) > MAX_ARGUMENTE:
        return f"höchstens {MAX_ARGUMENTE} Argumente"
    if any(len(a) > ARGUMENT_ZEICHEN for a in args):
        return f"jedes Argument höchstens {ARGUMENT_ZEICHEN} Zeichen"
    if any(a.startswith("@") for a in args):
        return "keine Argumentdateien (@datei)"
    if any(fremdpfad(a) for a in args):
        return "keine Netzwerkpfade (//), Laufwerksangaben (C:) oder Adressen (://)"
    if any(_VERLAESST.search(a) for a in args):
        return "Pfade nur im Projektordner (kein .. und kein ~)"
    return regel(args)


def argv_grund(argv: object) -> str | None:
    """Die strenge Fassung für den Dispatcher (der Kern ist dort nicht vertrauenswürdig): warum argv nicht ausgeführt wird, oder
    None. argv muss eine Liste aus 1 bis 13 Texten sein; jedes Argument ohne Steuerzeichen (auch unsichtbare) und ohne ein
    Zeichen aus VERBOTEN; das Programm mit genau dem Namen aus PROGRAMME (nicht in anderer Schreibweise, nicht .exe, nicht mit Pfad); danach
    befehl_grund. Meldungen sind kurz und einzeilig."""
    if not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= MAX_ARGUMENTE + 1:
        return f"argv: eine Liste mit 1 bis {MAX_ARGUMENTE + 1} Einträgen erwartet"
    if not all(type(a) is str for a in argv):
        return "argv: nur Texte erlaubt"
    for a in argv:
        if hat_steuerzeichen(a):
            return "Steuerzeichen oder unsichtbare Zeichen im Befehl"
        if VERBOTEN.search(a):
            return "Zeichen ; & | ` $ < > oder Backslash im Befehl"
    if argv[0] not in PROGRAMME:
        return f"Programm {_kurz(argv[0], 30)} ist nicht erlaubt (erlaubt: {', '.join(sorted(PROGRAMME))})"
    return befehl_grund(tuple(argv))
