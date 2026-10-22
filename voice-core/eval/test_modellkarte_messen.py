"""Tests der Messwerkzeuge für die Modellkarte: Code-Aufgaben (Referenzlösungen müssen bestehen, falsche und
gefährliche Lösungen nicht), Analyse-Probe (Text, Fragen, Bewertung), Einbettung (Kosinus) und Bericht."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parent
sys.path[:0] = [str(EVAL.parent), str(EVAL)]

import analyse_probe as ap  # noqa: E402
import code_aufgaben as ca  # noqa: E402
import modellkarte_messen as mm  # noqa: E402

LOESUNGEN = {
    "roemisch": '''
def roemisch_zu_int(s):
    werte = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if not s or any(c not in werte for c in s):
        raise ValueError(s)
    summe = 0
    for i, c in enumerate(s):
        if i + 1 < len(s) and werte[c] < werte[s[i + 1]]:
            summe -= werte[c]
        else:
            summe += werte[c]
    return summe
''',
    "dauer": '''
import re
def dauer_in_sekunden(text):
    faktor = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    teile = text.split()
    if not teile:
        raise ValueError(text)
    gesehen, summe = set(), 0
    for t in teile:
        m = re.fullmatch(r"(\\d+)([dhms])", t)
        if not m or m.group(2) in gesehen:
            raise ValueError(t)
        gesehen.add(m.group(2))
        summe += int(m.group(1)) * faktor[m.group(2)]
    return summe
''',
    "intervalle": '''
def intervalle_verschmelzen(liste):
    ergebnis = []
    for a, b in sorted((list(x) for x in liste)):
        if ergebnis and a <= ergebnis[-1][1]:
            ergebnis[-1][1] = max(ergebnis[-1][1], b)
        else:
            ergebnis.append([a, b])
    return ergebnis
''',
    "luhn": '''
def luhn_gueltig(ziffern):
    if any(not (c.isdigit() or c == " ") for c in ziffern):
        return False
    z = [int(c) for c in ziffern if c != " "]
    if len(z) < 2:
        return False
    summe = 0
    for i, d in enumerate(reversed(z)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        summe += d
    return summe % 10 == 0
''',
    "lru": '''
from collections import OrderedDict
class LRUCache:
    def __init__(self, kapazitaet):
        self.k, self.d = kapazitaet, OrderedDict()
    def get(self, schluessel):
        if schluessel not in self.d:
            return -1
        self.d.move_to_end(schluessel)
        return self.d[schluessel]
    def put(self, schluessel, wert):
        self.d[schluessel] = wert
        self.d.move_to_end(schluessel)
        if len(self.d) > self.k:
            self.d.popitem(last=False)
''',
    "flach": '''
def flach(verschachtelt):
    ergebnis = []
    for x in verschachtelt:
        if isinstance(x, (list, tuple)):
            ergebnis.extend(flach(x))
        else:
            ergebnis.append(x)
    return ergebnis
''',
    "woerter": '''
import re
from collections import Counter
def top_woerter(text, n):
    woerter = re.findall(r"[^\\W\\d_]+", text.lower())
    return sorted(Counter(woerter).items(), key=lambda p: (-p[1], p[0]))[:n]
''',
    "iban": '''
def iban_de_gueltig(iban):
    s = iban.replace(" ", "").upper()
    if len(s) != 22 or not s.startswith("DE") or not s[2:].isdigit():
        return False
    umgestellt = s[4:] + s[:4]
    zahl = "".join(str(int(c, 36)) for c in umgestellt)
    return int(zahl) % 97 == 1
''',
}


def test_jede_aufgabe_hat_eine_loesung():
    assert {a.id for a in ca.AUFGABEN} == set(LOESUNGEN)
    assert 5 <= len(ca.AUFGABEN) <= 8


@pytest.mark.parametrize("aufgabe", ca.AUFGABEN, ids=lambda a: a.id)
def test_referenzloesung_besteht(aufgabe):
    antwort = f"Hier ist die Lösung:\n```python\n{LOESUNGEN[aufgabe.id]}\n```\nViel Erfolg."
    ok, grund = ca.bewerten(aufgabe, antwort)
    assert ok, grund


@pytest.mark.parametrize("aufgabe", ca.AUFGABEN, ids=lambda a: a.id)
def test_falsche_loesung_faellt_durch(aufgabe):
    leer = {"roemisch": "def roemisch_zu_int(s):\n    return 0",
            "dauer": "def dauer_in_sekunden(text):\n    return 0",
            "intervalle": "def intervalle_verschmelzen(liste):\n    return liste",
            "luhn": "def luhn_gueltig(ziffern):\n    return True",
            "lru": "class LRUCache:\n    def __init__(self, k): pass\n    def get(self, s): return -1\n    def put(self, s, w): pass",
            "flach": "def flach(v):\n    return list(v)",
            "woerter": "def top_woerter(text, n):\n    return []",
            "iban": "def iban_de_gueltig(iban):\n    return True"}[aufgabe.id]
    ok, _ = ca.bewerten(aufgabe, f"```python\n{leer}\n```")
    assert not ok


def test_ohne_codezaun_und_mit_offenem_zaun():
    a = next(x for x in ca.AUFGABEN if x.id == "flach")
    assert ca.bewerten(a, LOESUNGEN["flach"])[0]
    assert ca.bewerten(a, "```python\n" + LOESUNGEN["flach"])[0]


def test_laengster_codeblock_gewinnt():
    text = "```\nx = 1\n```\nund\n```python\ndef f():\n    return 2\n```"
    assert "def f" in ca.code_aus_antwort(text)


@pytest.mark.parametrize("code,grund", [
    ("import os\nos.remove('x')", "Import nicht erlaubt: os"),
    ("import subprocess", "Import nicht erlaubt: subprocess"),
    ("from socket import socket", "Import nicht erlaubt: socket"),
    ("x = open('datei').read()", "Name nicht erlaubt: open"),
    ("exec('1')", "Name nicht erlaubt: exec"),
    ("eval('1')", "Name nicht erlaubt: eval"),
    ("__import__('os')", "Name nicht erlaubt: __import__"),
    ("().__class__.__bases__", "Attribut nicht erlaubt: "),
    ("from . import x", "Import nicht erlaubt: ."),
    ("def f(:", "Syntaxfehler"),
])
def test_vorpruefung_lehnt_ab(code, grund):
    assert (ca.vorpruefung(code) or "").startswith(grund)


def test_vorpruefung_laesst_harmloses_zu():
    assert ca.vorpruefung("import re\nfrom collections import Counter\nclass A:\n    def __init__(self):\n        pass") is None


def test_endlosschleife_wird_abgebrochen(monkeypatch):
    monkeypatch.setattr(ca, "LAUF_TIMEOUT_SECS", 1.0)
    ok, grund = ca.ausfuehren("while True:\n    pass", "")
    assert not ok and "Zeitgrenze" in grund


def test_abgelehnter_code_wird_nie_ausgefuehrt(tmp_path):
    marke = tmp_path / "marke.txt"
    ok, grund = ca.ausfuehren(f"import os\nopen(r'{marke}', 'w').write('x')", "")
    assert not ok and not marke.exists()


# --- Analyse-Probe ---------------------------------------------------------------------------------------


def test_bericht_ist_lang_und_stabil():
    text = ap.bericht()
    assert text == ap.bericht()
    assert 20_000 < len(text) < 45_000
    assert text.count("Abschnitt ") >= 24
    for fakt in ("Kerstin Albrecht", "240.000 Euro", "36.000 Euro", "21. Juli", "12. Oktober 2026"):
        assert fakt in text


def test_erwartete_tage():
    assert ap.TAGE_BIS_GO_LIVE == 223
    assert ap.GO_LIVE.weekday() == 0  # Montag, wie im Bericht behauptet


def test_kritische_risiken_sind_fuenf():
    import re

    text = ap.bericht()
    kritisch = [n for n in range(1, 10) if re.search(rf"R{n} [^.]*Einstufung kritisch", text)]
    assert kritisch == [1, 3, 5, 7, 8]  # das Vorgängerprojekt (Ablenkung) hat keine R-Nummern


GUTE_ANTWORT = "1: Frau Albrecht\n2: 276.000 Euro\n3: 8 Personen, davon 3 extern\n4: 5\n5: 21. Juli\n6: 223 Tage"


def test_gute_antwort_bekommt_alle_punkte():
    assert ap.bewerten(GUTE_ANTWORT) == (6, [])


def test_antwortformate():
    assert ap.antworten_zerlegen("1. A\n2) B\n3 - C\nirgendwas")[2] == "B"
    assert ap.bewerten("1: Albrecht\n2: 240.000\n3: 7 Personen\n4: vier\n5: 30. Juni\n6: 100")[0] == 1


def test_vorgaengerprojekt_und_alte_zahl_sind_falsch():
    punkte, gruende = ap.bewerten("1: Albrecht\n2: 276.000\n3: 7 davon 2 extern\n4: 5\n5: 21.07.\n6: 223")
    assert punkte == 5 and any("Frage 3" in g for g in gruende)
    assert ap.bewerten("4: 5 kritisch und 4 kritische")[0] == 0  # keine Frage 1 bis 3 beantwortet; 4 mit falscher Zahl daneben


# --- Einbettung und Bewertung ---------------------------------------------------------------------


def test_kosinus_und_treffer():
    assert mm.kosinus([1, 0], [1, 0]) == pytest.approx(1.0)
    assert mm.kosinus([1, 0], [0, 1]) == pytest.approx(0.0)
    assert mm.kosinus([0, 0], [1, 1]) == 0.0
    assert mm.bester_treffer([1, 0.1], [[0, 1], [1, 0], [0.5, 0.5]]) == 1


def test_bewerter_faelle_sind_ausgewogen():
    urteile = [f[4] for f in mm.BEWERTER_FAELLE]
    assert set(urteile) == {"richtig", "falsch"} and len(urteile) == 9
    assert any("Ignoriere" in f[3] for f in mm.BEWERTER_FAELLE)


def test_messdateien_haben_keine_geheimnisse():
    ordner = EVAL / "ergebnisse" / "modellkarte"
    for datei in ordner.glob("*.json"):
        text = datei.read_text(encoding="utf-8")
        assert "sk-ant-" not in text and "Bearer" not in text and "192.168." not in text, datei.name
        json.loads(text)
