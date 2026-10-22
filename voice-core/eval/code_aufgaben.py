"""Kleiner Code-Testsatz für Modelle, die Code schreiben (Modellkarte, Fähigkeit "code").

Acht kurze Aufgaben auf Deutsch. Das Ergebnis eines Modells zählt nur, wenn der Code läuft und alle Prüfungen der
Aufgabe bestehen (Ausführen, kein Vergleich von Text). Der Code des Modells läuft in einem eigenen Python-Prozess
(-I, eigener Ordner, Zeitgrenze) und erst nach einer Vorprüfung:
  * nur Importe aus einer kurzen Liste harmloser Standardmodule,
  * kein open, exec, eval, compile, __import__, input, breakpoint, globals, locals, vars, getattr, setattr,
  * kein Zugriff auf Attribute mit doppeltem Unterstrich (außer beim Definieren eigener Methoden).
Das ist eine Schutzschicht gegen Versehen bei vertrauten Hausmodellen, kein Sandkasten für fremden Code.

Die Prüfungen stehen hier; die Referenzlösungen, die sie bestehen müssen, stehen in test_modellkarte_messen.py.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ERLAUBTE_MODULE = frozenset({
    "math", "re", "itertools", "collections", "functools", "heapq", "bisect", "string", "decimal", "fractions",
    "datetime", "typing", "dataclasses", "operator", "unicodedata", "statistics", "enum", "textwrap", "json",
})
VERBOTENE_NAMEN = frozenset({
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "__builtins__", "exit", "quit",
})
LAUF_TIMEOUT_SECS = 10.0
SYSTEM = (
    "Du schreibst Python 3. Antworte nur mit dem Code in einem einzigen Codeblock (```python ... ```), ohne "
    "Erklärung, ohne Beispielaufrufe, ohne Tests. Nutze nur die Standardbibliothek."
)


@dataclass(frozen=True)
class Aufgabe:
    id: str
    auftrag: str
    pruefung: str  # Python-Anweisungen (assert), die nach dem Code des Modells laufen


AUFGABEN: tuple[Aufgabe, ...] = (
    Aufgabe(
        "roemisch",
        "Schreibe die Funktion roemisch_zu_int(s: str) -> int. Sie wandelt eine römische Zahl (Großbuchstaben) in "
        "eine ganze Zahl um, auch mit Subtraktionsregel (IV = 4, CM = 900). Enthält s ein Zeichen außerhalb von "
        "IVXLCDM oder ist s leer, wirf ValueError.",
        """
assert roemisch_zu_int("XIV") == 14
assert roemisch_zu_int("MCMXCIV") == 1994
assert roemisch_zu_int("IX") == 9
assert roemisch_zu_int("MMXXVI") == 2026
for schlecht in ("", "ABC", "XIa"):
    try:
        roemisch_zu_int(schlecht)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet: " + repr(schlecht))
""",
    ),
    Aufgabe(
        "dauer",
        "Schreibe die Funktion dauer_in_sekunden(text: str) -> int. Der Text besteht aus Teilen wie '1d', '2h', "
        "'30m', '15s' (Tage, Stunden, Minuten, Sekunden), durch Leerzeichen getrennt, in beliebiger Reihenfolge, "
        "jede Einheit höchstens einmal. Rückgabe ist die Summe in Sekunden. Leerer oder ungültiger Text "
        "(unbekannte Einheit, fehlende Zahl, doppelte Einheit): ValueError.",
        """
assert dauer_in_sekunden("1h 30m 15s") == 5415
assert dauer_in_sekunden("45s") == 45
assert dauer_in_sekunden("2h") == 7200
assert dauer_in_sekunden("1d 2h") == 93600
assert dauer_in_sekunden("15s 1m") == 75
for schlecht in ("", "5x", "m", "1h 2h", "abc"):
    try:
        dauer_in_sekunden(schlecht)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError erwartet: " + repr(schlecht))
""",
    ),
    Aufgabe(
        "intervalle",
        "Schreibe die Funktion intervalle_verschmelzen(liste) -> list. Eingabe ist eine Liste von [anfang, ende] "
        "(ganze Zahlen, anfang <= ende, unsortiert). Überlappende oder sich berührende Intervalle (Ende == Anfang "
        "des nächsten) werden zu einem verschmolzen. Rückgabe: sortierte Liste von [anfang, ende]-Listen. Leere "
        "Eingabe ergibt eine leere Liste. Die Eingabe darf nicht verändert werden.",
        """
eingabe = [[8, 10], [1, 3], [2, 6], [15, 18]]
kopie = [list(x) for x in eingabe]
assert intervalle_verschmelzen(eingabe) == [[1, 6], [8, 10], [15, 18]]
assert eingabe == kopie
assert intervalle_verschmelzen([[1, 4], [4, 5]]) == [[1, 5]]
assert intervalle_verschmelzen([]) == []
assert intervalle_verschmelzen([[5, 5]]) == [[5, 5]]
assert intervalle_verschmelzen([[1, 10], [2, 3], [4, 5]]) == [[1, 10]]
assert intervalle_verschmelzen([[1, 2], [3, 4]]) == [[1, 2], [3, 4]]
""",
    ),
    Aufgabe(
        "luhn",
        "Schreibe die Funktion luhn_gueltig(ziffern: str) -> bool, die eine Zahl mit der Luhn-Prüfsumme prüft. "
        "Leerzeichen werden ignoriert. Enthält der Text andere Zeichen als Ziffern und Leerzeichen oder hat er "
        "weniger als zwei Ziffern, ist das Ergebnis False.",
        """
assert luhn_gueltig("4539578763621486") is True
assert luhn_gueltig("4539 5787 6362 1486") is True
assert luhn_gueltig("4539578763621487") is False
assert luhn_gueltig("79927398713") is True
assert luhn_gueltig("0") is False
assert luhn_gueltig("") is False
assert luhn_gueltig("12a4") is False
assert luhn_gueltig("00") is True
""",
    ),
    Aufgabe(
        "lru",
        "Schreibe die Klasse LRUCache. Konstruktor LRUCache(kapazitaet: int). Methode get(schluessel) gibt den Wert "
        "zurück oder -1, wenn er fehlt, und macht den Schlüssel zum zuletzt benutzten. Methode put(schluessel, "
        "wert) legt den Wert ab (auch zum Überschreiben, dann zuletzt benutzt). Ist die Kapazität überschritten, "
        "fliegt der am längsten nicht benutzte Schlüssel raus.",
        """
c = LRUCache(2)
c.put(1, "a")
c.put(2, "b")
assert c.get(1) == "a"
c.put(3, "c")
assert c.get(2) == -1
assert c.get(3) == "c"
c.put(1, "neu")
c.put(4, "d")
assert c.get(3) == -1
assert c.get(1) == "neu"
assert c.get(4) == "d"
k = LRUCache(1)
k.put("x", 1)
k.put("y", 2)
assert k.get("x") == -1 and k.get("y") == 2
""",
    ),
    Aufgabe(
        "flach",
        "Schreibe die Funktion flach(verschachtelt) -> list. Sie macht aus beliebig tief verschachtelten Listen "
        "und Tupeln eine flache Liste in der ursprünglichen Reihenfolge. Zeichenketten bleiben ganze Elemente "
        "(werden nicht in Buchstaben zerlegt), None bleibt erhalten.",
        """
assert flach([1, [2, [3, [4]], 5]]) == [1, 2, 3, 4, 5]
assert flach([]) == []
assert flach([[], [[]]]) == []
assert flach(["ab", ["cd", ("e", None)]]) == ["ab", "cd", "e", None]
assert flach((1, (2, 3))) == [1, 2, 3]
assert flach([1, 2]) == [1, 2]
""",
    ),
    Aufgabe(
        "woerter",
        "Schreibe die Funktion top_woerter(text: str, n: int) -> list. Gezählt werden Wörter ohne Beachtung der "
        "Groß- und Kleinschreibung (Ergebnis in Kleinbuchstaben). Ein Wort besteht aus Buchstaben (auch ä, ö, ü, "
        "ß); alles andere trennt Wörter. Rückgabe: Liste von (wort, anzahl)-Tupeln, höchstens n Stück, sortiert "
        "nach Anzahl absteigend, bei gleicher Anzahl alphabetisch aufsteigend.",
        """
t = "Der Hund und die Katze. Die Katze, der Hund! Und die Maus? Äpfel äpfel ÄPFEL, äpfel Straße straße."
assert top_woerter(t, 3) == [("äpfel", 4), ("die", 3), ("der", 2)]
assert top_woerter("", 5) == []
assert top_woerter("a b a", 1) == [("a", 2)]
assert top_woerter("b a", 5) == [("a", 1), ("b", 1)]
assert top_woerter("Straße, STRASSE straße", 2) == [("straße", 2), ("strasse", 1)]
assert top_woerter("eins zwei", 0) == []
""",
    ),
    Aufgabe(
        "iban",
        "Schreibe die Funktion iban_de_gueltig(iban: str) -> bool. Sie prüft eine deutsche IBAN: Leerzeichen "
        "werden entfernt, Kleinbuchstaben sind erlaubt, sie beginnt mit DE, hat genau 22 Zeichen (nach DE nur "
        "Ziffern) und besteht die Prüfziffernrechnung (Modulo 97, die ersten vier Zeichen ans Ende, Buchstaben "
        "D = 13, E = 14).",
        """
assert iban_de_gueltig("DE89370400440532013000") is True
assert iban_de_gueltig("de89 3704 0044 0532 0130 00") is True
assert iban_de_gueltig("DE89370400440532013001") is False
assert iban_de_gueltig("DE8937040044053201300") is False
assert iban_de_gueltig("FR1420041010050500013M02606") is False
assert iban_de_gueltig("DE12ABCD00440532013000") is False
assert iban_de_gueltig("") is False
assert iban_de_gueltig("DE02120300000000202051") is True
""",
    ),
)

_CODEBLOCK = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def code_aus_antwort(text: str) -> str:
    """Der Code aus der Antwort: der längste Codeblock, sonst der ganze Text (ohne öffnenden Zaun ohne Ende)."""
    blocke = _CODEBLOCK.findall(text or "")
    if blocke:
        return max(blocke, key=len).strip("\n")
    roh = re.sub(r"^```(?:python|py)?[ \t]*\n", "", (text or "").strip(), flags=re.IGNORECASE)
    return roh.rstrip("`").strip("\n")


def vorpruefung(code: str) -> str | None:
    """None, wenn der Code ausgeführt werden darf; sonst der Grund (kurz, ohne den Code)."""
    try:
        baum = ast.parse(code)
    except SyntaxError as e:
        return f"Syntaxfehler (Zeile {e.lineno})"
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            namen = [a.name.split(".")[0] for a in knoten.names]
        elif isinstance(knoten, ast.ImportFrom):
            namen = [(knoten.module or "").split(".")[0]] if knoten.level == 0 else ["."]
        else:
            namen = []
        for name in namen:
            if name not in ERLAUBTE_MODULE:
                return f"Import nicht erlaubt: {name or '.'}"
        if isinstance(knoten, ast.Name) and knoten.id in VERBOTENE_NAMEN:
            return f"Name nicht erlaubt: {knoten.id}"
        if isinstance(knoten, ast.Attribute) and knoten.attr.startswith("__") and knoten.attr.endswith("__"):
            return f"Attribut nicht erlaubt: {knoten.attr}"
    return None


def ausfuehren(code: str, pruefung: str) -> tuple[bool, str]:
    """(bestanden, Grund). Der Code läuft nur nach der Vorprüfung, in einem eigenen Prozess mit Zeitgrenze."""
    if grund := vorpruefung(code):
        return False, grund
    with tempfile.TemporaryDirectory(prefix="wamo-code-") as ordner:
        datei = Path(ordner) / "lauf.py"
        datei.write_text(code + "\n\n" + pruefung + "\nprint('BESTANDEN')\n", encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", str(datei)], cwd=ordner, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=LAUF_TIMEOUT_SECS, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, f"Zeitgrenze von {LAUF_TIMEOUT_SECS:.0f} s überschritten"
    if r.returncode == 0 and "BESTANDEN" in r.stdout:
        return True, ""
    letzte = [z for z in (r.stderr or "").strip().splitlines() if z.strip()]
    return False, (letzte[-1] if letzte else f"Exit-Code {r.returncode}")[:160]


def bewerten(aufgabe: Aufgabe, antwort: str) -> tuple[bool, str]:
    code = code_aus_antwort(antwort)
    if not code.strip():
        return False, "keine Antwort"
    return ausfuehren(code, aufgabe.pruefung)
