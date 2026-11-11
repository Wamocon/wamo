"""Vertragstest zwischen dem Kern (voice-core) und dem Kern-Kanal des Dispatchers (infra/sokrates2), Paket R13.

Beide Seiten wurden getrennt gebaut und getestet. Dieser Test prüft sie gegeneinander und gegen die festgelegten
Schnittstellen der Arbeitsliste (docs/entwicklung-im-betrieb-arbeitsliste.md, Abschnitt 1). Die Tabellen unten stehen
bewusst als Wortlaut im Test: Weicht eine Seite ab, wird dieser Test rot, nicht erst der Betrieb.

    I2  Standardwerte und Bereiche der Datei kernkanal.conf (Skript und Dispatcher)
    I3  Büro-Variablen und Release-Nachweis SCHRANKEN_VERSION (Kern und Skript)
    I5  Projektname (Kern und Dispatcher, dieselbe Wertetabelle)
    I6  Grund-Codes des Ereignisses kanal_gesperrt (Dispatcher und Kern)
    ssh-Optionen des Rauchtests gegen dev_agent.ssh_argv
    unveränderte Dateien (Prüfsummen von Slot-Skript, Vorprüfung und Slot-Vorlage), SELF_REPO, Namensvertrag

Alles läuft ohne Netz, ohne Server, ohne ssh und ohne git. Ausführen (im Ordner voice-core/):  python -m pytest -q test_kernkanal_vertrag.py
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import dev_agent
import dev_guard
import entwicklung_betrieb as eb

VOICE_CORE = Path(__file__).resolve().parent
WURZEL = VOICE_CORE.parent
INFRA = WURZEL / "infra" / "sokrates2"


def _lade(name: str, datei: str):
    spec = importlib.util.spec_from_file_location(name, INFRA / datei)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul  # nötig für @dataclass
    spec.loader.exec_module(modul)
    return modul


K = _lade("wamo_job_kernkanal", "wamo_job_kernkanal.py")  # das Modul des Dispatchers
D = _lade("wamo_job_dispatch_vertrag", "wamo_job_dispatch.py")
R = _lade("wamo_job_kern_rauchtest_vertrag", "wamo_job_kern_rauchtest.py")


def _text(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8").replace("\r\n", "\n")


SKRIPT = _text(INFRA / "wamo-job-kern-einrichten.sh")
RUECKWEG = _text(INFRA / "wamo-job-kern-rueckweg.sh")
EB_QUELLE = _text(VOICE_CORE / "entwicklung_betrieb.py")
NAMENSVERTRAG = _text(WURZEL / "CLAUDE.md")

# Synthetische Kennung, keine echte user_id
PROFIL = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def test_geprueft_wird_der_code_dieses_arbeitsbaums():
    """Die Module kommen aus demselben Baum wie der Test (kein alter Stand aus einer anderen Installation)."""
    assert Path(eb.__file__).resolve() == VOICE_CORE / "entwicklung_betrieb.py"
    assert Path(dev_agent.__file__).resolve() == VOICE_CORE / "dev_agent.py"
    assert Path(K.__file__).resolve() == INFRA / "wamo_job_kernkanal.py"


# ================================================================== I6: Grund-Codes
I6_CODES = ("notaus", "konfig_ungueltig", "kanal_zu", "abgelaufen", "befehl", "fremd", "art", "repo", "repo_fehlt", "dienst",
            "projekt", "zugang", "fork", "herkunft_unklar", "grenze_start", "grenze_laufzeit", "grenze_folgen", "abstand", "platz")

# Funktionen von wamo_job_kernkanal.py, deren Rückgabe ein Code aus KANAL_GRUENDE ist
CODE_FUNKTIONEN = {"pruefe_befehl", "pruefe_eigener_auftrag", "projekt_pruefen", "pruefe_grenzen", "pruefe_start", "fork_pruefen",
                   "kanal_zustand", "kern_projektname"}


def test_i6_die_feste_liste_gilt_auf_beiden_seiten():
    assert K.KANAL_GRUENDE == I6_CODES
    assert tuple(eb.GRUND_SAETZE) == I6_CODES
    assert len(set(K.KANAL_GRUENDE)) == len(K.KANAL_GRUENDE)


def test_jeder_code_des_dispatchers_hat_im_kern_einen_satz():
    assert set(K.KANAL_GRUENDE) == set(eb.GRUND_SAETZE)
    for code, satz in eb.GRUND_SAETZE.items():
        assert isinstance(satz, str) and satz.strip(), code


def _konstanten(ausdruck: ast.AST) -> set[str]:
    if isinstance(ausdruck, ast.Constant) and isinstance(ausdruck.value, str):
        return {ausdruck.value}
    if isinstance(ausdruck, ast.IfExp):
        return _konstanten(ausdruck.body) | _konstanten(ausdruck.orelse)
    return set()


def _codes_der_regeln(quelle: str) -> set[str]:
    """Die Textwerte, die eine Regel zurückgibt (return, grund = ..., ProjektnameFehler("...")) in den Prüffunktionen."""
    codes: set[str] = set()
    for funktion in ast.walk(ast.parse(quelle)):
        if not (isinstance(funktion, ast.FunctionDef) and funktion.name in CODE_FUNKTIONEN):
            continue
        for knoten in ast.walk(funktion):
            if isinstance(knoten, ast.Return) and knoten.value is not None:
                codes |= _konstanten(knoten.value)
            elif isinstance(knoten, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "grund" for t in knoten.targets):
                codes |= _konstanten(knoten.value)
            elif isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name) and knoten.func.id == "ProjektnameFehler" and knoten.args:
                codes |= _konstanten(knoten.args[0])
    return codes


def _abweisungen(quelle: str) -> set[str]:
    """Die Codes, die der Dispatcher selbst in kanal_abweisen("...") schreibt."""
    codes: set[str] = set()
    for knoten in ast.walk(ast.parse(quelle)):
        if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name) and knoten.func.id == "kanal_abweisen" and knoten.args:
            codes |= _konstanten(knoten.args[0])
    return codes


def test_der_dispatcher_nennt_nur_codes_aus_der_festen_liste():
    regeln = _codes_der_regeln(_text(INFRA / "wamo_job_kernkanal.py"))
    abweisungen = _abweisungen(_text(INFRA / "wamo_job_dispatch.py"))
    assert len(regeln) >= 10 and len(abweisungen) >= 3, "die Quelltextprüfung findet nichts mehr (Aufbau geändert?)"
    assert regeln <= set(K.KANAL_GRUENDE), regeln - set(K.KANAL_GRUENDE)
    assert abweisungen <= set(K.KANAL_GRUENDE), abweisungen - set(K.KANAL_GRUENDE)
    assert not (regeln | abweisungen) - set(eb.GRUND_SAETZE)


@pytest.mark.parametrize("code", I6_CODES)
def test_das_ereignis_des_dispatchers_wird_im_kern_zum_satz(code, monkeypatch, capsys):
    """Der Weg einer Abweisung: kanal_abweisen schreibt die Zeile, der Kern macht daraus Quittung und Status (I6, Exit 2)."""
    zeilen: list[bytes] = []
    monkeypatch.setattr(D, "log", lambda *args, **kw: None)
    monkeypatch.setattr(D, "emit_raw", lambda zeile: zeilen.append(zeile) or True)
    assert D.kanal_abweisen(code) == D.EXIT_ABGEWIESEN == 2
    assert code in capsys.readouterr().err  # die Meldung auf stderr nennt nur den Code
    assert len(zeilen) == 1
    zeile = zeilen[0].decode("utf-8")
    ereignis = json.loads(zeile)
    assert ereignis == {"type": "wamo", "ereignis": "kanal_gesperrt", "grund": code}  # das Format aus I6, sonst nichts
    satz = eb.GRUND_SAETZE[code]
    assert eb.grund_aus_ausgabe(zeile) == satz
    assert eb.ablehn_grund(2, {"kanal_gesperrt": ereignis}) == satz
    status = dev_agent.remote_status(2, {"kanal_gesperrt": ereignis})
    assert status.startswith("nicht gestartet") and satz.rstrip(".") in status


def test_der_kern_bildet_kanal_gesperrt_ab_und_vertraegt_einen_neuen_code():
    assert dev_agent.REMOTE_EREIGNIS_STATUS["kanal_gesperrt"].startswith("nicht gestartet")
    assert eb.kanal_status({"grund": "ein_neuer_code"}).startswith("nicht gestartet")
    assert eb.grund_satz("ein_neuer_code") == eb.GRUND_UNBEKANNT


# ================================================================== I5: Projektname
# (Repo, Projektwort, Ergebnis). Die ersten Zeilen stehen so in der Arbeitsliste, die übrigen setzen dieselbe Regel fort:
# alles außerhalb von a-z0-9- wird verworfen, auch Umlaute, ß und Leerzeichen (keine Umschrift).
I5_GUT = (
    ("Acme/Test-Repo", None, "kern-test-repo"),
    ("acme/my_repo.js", None, "kern-my-repo-js"),
    ("acme/a__b", None, "kern-a-b"),
    ("acme/-x-", None, "kern-x"),
    (None, "Marktstudie", "kern-marktstudie"),
    (None, "Märkte", "kern-mrkte"),  # die Zeile mit Umlaut: kein ae, sondern verworfen
    (None, "Größe", "kern-gre"),
    (None, "Öl und Fuß", "kern-lundfu"),
    (None, "Markt Studie", "kern-marktstudie"),
    (None, "  Markt/Studie! ", "kern-marktstudie"),
    (None, "a..b__c", "kern-a-b-c"),
    ("acme/" + "a" * 45, None, "kern-" + "a" * 45),  # genau 50 Zeichen mit Präfix
    ("acme/x", "ignoriert", "kern-x"),  # mit Repo zählt nur das Repo
)
I5_ABGELEHNT = (
    ("acme/wamo", None, "repo"),
    ("acme/WAMO", None, "repo"),
    ("acme/Wamo", None, "repo"),
    ("acme/" + "a" * 46, None, "projekt"),  # länger als 50 mit Präfix
    ("acme/" + "a" * 80, None, "projekt"),
    (None, "äöü", "projekt"),  # nach dem Verwerfen bleibt nichts
    (None, "---", "projekt"),
    (None, "", "projekt"),
    (None, None, "projekt"),
    (None, "wamo", "projekt"),
)


def seite_kern(repo, wort):
    try:
        return eb.kern_projektname(repo, wort)
    except eb.ProjektnameFehler as e:
        return ("abgelehnt", e.grund)


def seite_dispatcher(repo, wort):
    try:
        return K.kern_projektname(repo, wort)
    except K.ProjektnameFehler as e:
        return ("abgelehnt", e.grund)


SEITEN = {"kern": seite_kern, "dispatcher": seite_dispatcher}


@pytest.mark.parametrize("seite", SEITEN)
@pytest.mark.parametrize("repo, wort, erwartet", I5_GUT)
def test_i5_gute_zeilen_auf_beiden_seiten(seite, repo, wort, erwartet):
    assert SEITEN[seite](repo, wort) == erwartet


@pytest.mark.parametrize("seite", SEITEN)
@pytest.mark.parametrize("repo, wort, grund", I5_ABGELEHNT)
def test_i5_abgelehnte_zeilen_auf_beiden_seiten(seite, repo, wort, grund):
    assert SEITEN[seite](repo, wort) == ("abgelehnt", grund)


def _zufaellige_eingaben(anzahl: int = 2500) -> list[tuple[str | None, str | None]]:
    """Wörter mit Umlauten, Leerraum und Sonderzeichen sowie Repos in der Form, die beide Seiten vorher prüfen (REPO_RE, ohne
    .git und ohne . oder .. als Namen: check_repo im Dispatcher und repo_pruefen im Kern lassen nichts anderes durch)."""
    zufall = random.Random(20261111)
    wort_zeichen = "abcxyzABCXYZ0189-._ /!äöüÄÖÜßéñ\t €"
    repo_zeichen = "abcxyzABCXYZ0189-._"
    laengen = (1, 2, 3, 5, 8, 20, 44, 45, 46, 47, 60)
    eingaben: list[tuple[str | None, str | None]] = [(None, wort) for wort in ("wamo", "WAMO", "wamo_", "-wamo-", "Wamo-Extra", "wamo.")]
    eingaben += [(f"acme/{name}", None) for name in ("wamo", "WAMO", "-wamo-", "wamo_", "Wamo-Extra", "a.b-c_d", "..", "___", "-")]
    for _ in range(anzahl):
        if zufall.random() < 0.5:
            name = "".join(zufall.choice(repo_zeichen) for _ in range(zufall.choice(laengen)))
            if name in (".", "..") or name.lower().endswith(".git"):
                continue
            eingaben.append((f"acme/{name}", None))
        else:
            eingaben.append((None, "".join(zufall.choice(wort_zeichen) for _ in range(zufall.choice(laengen)))))
    return eingaben


def test_i5_beide_seiten_geben_bei_jeder_eingabe_dasselbe():
    """Das Ergebnis ist ein Name oder eine Ablehnung mit Grund (repo oder projekt); der Kern leitet ab, der Dispatcher erzwingt."""
    eingaben = _zufaellige_eingaben()
    for repo, _wort in eingaben:
        if repo:
            assert dev_agent.REPO_RE.fullmatch(repo) and K.REPO_RE.fullmatch(repo) and D.REPO_RE.fullmatch(repo), repo
    abweichungen = [(e, seite_kern(*e), seite_dispatcher(*e)) for e in eingaben if seite_kern(*e) != seite_dispatcher(*e)]
    assert not abweichungen, abweichungen[:5]


def test_i5_der_dispatcher_nimmt_jeden_namen_des_kerns_an():
    for repo, wort in _zufaellige_eingaben():
        name = seite_kern(repo, wort)
        if isinstance(name, tuple):
            continue
        assert K.projekt_pruefen(name, repo) is None, (repo, wort, name)
        assert K.pc_projekt_erlaubt(name) is False, name  # der Namensraum kern- gehört dem Kern-Kanal


def test_i5_praefix_und_laenge_sind_auf_allen_seiten_gleich():
    assert eb.KERN_PRAEFIX == K.PRAEFIX == D.KERN_PRAEFIX == "kern-"
    assert eb.PROJEKT_MAX == K.MAX_PROJEKT_ZEICHEN == 50
    assert dev_agent.REPO_RE.pattern == K.REPO_RE.pattern == D.REPO_RE.pattern


def test_i5_zeilen_mit_dem_praefix_kern():
    # Projekt test-repo aus dem Kern-Kanal: abgelehnt (projekt, Präfix fehlt)
    assert K.projekt_pruefen("test-repo", "acme/test-repo") == "projekt"
    assert K.projekt_pruefen("test-repo", None) == "projekt"
    # Projekt kern-test-repo aus dem PC-Kanal: abgelehnt (Regel im Dispatcher, Namensraum des Kern-Kanals)
    assert K.pc_projekt_erlaubt("kern-test-repo") is False
    assert 'KANAL == "pc" and projekt.startswith(KERN_PRAEFIX)' in _text(INFRA / "wamo_job_dispatch.py")
    # mit gesetztem Repo muss das Projekt genau kern-<abgeleiteter Name> sein
    assert K.projekt_pruefen("kern-test-repo", "Acme/Test-Repo") is None
    assert K.projekt_pruefen("kern-anderes", "acme/test-repo") == "projekt"


# ================================================================== I3: Release-Nachweis SCHRANKEN_VERSION
def _muster_von_schalter() -> str:
    treffer = re.findall(r"grep -Eq '(\^SCHRANKEN_VERSION[^']*)'", SKRIPT)
    assert len(treffer) == 1, "das Skript prüft den Release-Nachweis nicht mehr mit genau einem grep -Eq"
    return treffer[0]


def _zeilen_im_muster(muster: str, quelle: str) -> list[str]:
    return [zeile for zeile in quelle.split("\n") if re.search(muster, zeile)]


def test_i3_genau_eine_zeile_passt_zum_muster_von_schalter():
    muster = _muster_von_schalter()
    assert muster == "^SCHRANKEN_VERSION = [1-9][0-9]*$"
    assert _zeilen_im_muster(muster, EB_QUELLE) == ["SCHRANKEN_VERSION = 1"]
    assert eb.SCHRANKEN_VERSION == 1
    assert "\r" not in (VOICE_CORE / "entwicklung_betrieb.py").read_bytes().decode("utf-8"), "ein Zeilenende mit CR bricht das $ im grep"


def test_i3_das_skript_liest_den_nachweis_im_zielrelease():
    assert '"$KERNAPP/releases/$RELEASE/voice-core/entwicklung_betrieb.py"' in SKRIPT
    assert 'KERNAPP="$WURZEL/opt/wamo-core"' in SKRIPT


@pytest.mark.parametrize("zeile", ["SCHRANKEN_VERSION = 0", "SCHRANKEN_VERSION = 01", "SCHRANKEN_VERSION = 1  # Stand",
                                   "SCHRANKEN_VERSION=1", " SCHRANKEN_VERSION = 1", "SCHRANKEN_VERSION = 1.5", "SCHRANKEN_VERSION = 'x'"])
def test_i3_das_muster_nimmt_nur_die_genaue_form_an(zeile):
    assert not _zeilen_im_muster(_muster_von_schalter(), zeile)


def test_i3_das_muster_nimmt_jede_spaetere_version_an():
    for zeile in ("SCHRANKEN_VERSION = 1", "SCHRANKEN_VERSION = 2", "SCHRANKEN_VERSION = 17"):
        assert _zeilen_im_muster(_muster_von_schalter(), zeile) == [zeile]


@pytest.mark.skipif(not shutil.which("grep"), reason="grep nicht vorhanden")
def test_i3_auch_das_echte_grep_nimmt_die_datei_an():
    muster = _muster_von_schalter()
    ergebnis = subprocess.run(["grep", "-Eq", muster, str(VOICE_CORE / "entwicklung_betrieb.py")], check=False)
    assert ergebnis.returncode == 0


# ================================================================== I2: Standardwerte und Bereiche der kernkanal.conf
I2_STANDARD = {"KANAL": "zu", "MAX_TURNS": "40", "MAX_BUDGET_USD": "2.00", "MAX_START_STUNDE": "4", "MAX_START_TAG": "8",
               "MAX_LAUFZEIT_TAG_S": "14400", "MAX_FOLGEN": "5", "MIN_ABSTAND_S": "60", "MIN_PLATZ_MB": "2048",
               "MAX_VERSUCH_STUNDE": "20"}  # der letzte Schlüssel kam mit der Umsetzung (Versuche je Stunde, nur nach unten einstellbar)
I2_ABLAUF_TAGE = 30
I2_BEREICHE = {"MAX_TURNS": (1, 200), "MAX_START_STUNDE": (1, 60), "MAX_START_TAG": (1, 200), "MAX_LAUFZEIT_TAG_S": (60, 86400),
               "MAX_FOLGEN": (0, 20), "MIN_ABSTAND_S": (0, 3600), "MIN_PLATZ_MB": (100, 1000000), "MAX_VERSUCH_STUNDE": (1, 20)}
I2_SCHLUESSEL = {"KANAL", "ABLAUF", "ARTEN", "REPOS", "DIENSTE", "MAX_BUDGET_USD", *I2_BEREICHE}


def _std_werte(skript: str) -> dict[str, str]:
    return dict(re.findall(r"^STD_([A-Z_]+)=(\S+)$", skript, flags=re.M))


def test_i2_standardwerte_des_skripts():
    std = _std_werte(SKRIPT)
    for schluessel, wert in I2_STANDARD.items():
        assert std.get(schluessel) == wert, schluessel
    assert std.get("ABLAUF_TAGE") == str(I2_ABLAUF_TAGE)


def test_i2_der_dispatcher_kennt_dieselben_schluessel_und_bereiche():
    schluessel = re.search(r"^KONFIG_SCHLUESSEL=\(([^)]*)\)", SKRIPT, flags=re.M | re.S)
    assert schluessel
    assert set(schluessel.group(1).split()) == set(K.BEKANNTE_SCHLUESSEL) == I2_SCHLUESSEL
    assert dict(K.ZAHLEN) == I2_BEREICHE
    assert K.MAX_ABLAUF_TAGE == 400 and K.MAX_REPOS == 10 and K.MAX_VERSUCH_STUNDE_FEST == 20


def test_i2_die_standardwerte_des_dispatchers_gleichen_denen_des_skripts():
    standard = K.Konfig()
    assert standard.kanal is None  # fehlt der Schlüssel, gilt der Kanal als zu
    for schluessel in I2_BEREICHE:
        assert str(getattr(standard, schluessel.lower())) == I2_STANDARD[schluessel], schluessel
    assert standard.max_budget_usd == I2_STANDARD["MAX_BUDGET_USD"]


def _attrappe(text: str):
    daten = text.encode("utf-8")
    return lambda pfad, limit: (daten, os.stat_result((0o100640, 0, 0, 1, 0, 0, len(daten), 0, 0, 0)))  # root, 0640


def test_i2_die_angelegte_datei_wird_vom_dispatcher_angenommen():
    """Was das Skript beim Anlegen schreibt (Standardwerte, Kanal zu, Ablauf in 30 Tagen), liest der Dispatcher ohne Fehler."""
    jetzt = datetime(2026, 11, 11, 12, 0, 0).timestamp()
    std = _std_werte(SKRIPT)
    ablauf = (date(2026, 11, 11) + timedelta(days=int(std["ABLAUF_TAGE"]))).strftime("%Y%m%d")
    werte = {"KANAL": std["KANAL"], "ABLAUF": ablauf, "ARTEN": "", "REPOS": "", "DIENSTE": "",
             **{schluessel: std[schluessel] for schluessel in (*I2_BEREICHE, "MAX_BUDGET_USD")}}
    konfig = K.konfig_lesen("/t/kernkanal.conf", jetzt=jetzt, lesen=_attrappe("\n".join(f"{k}={v}" for k, v in werte.items()) + "\n"))
    assert konfig == K.Konfig(kanal="zu", ablauf=date(2026, 12, 11))
    zustand = K.kanal_zustand(K.Pfade(konfig=Path("/t/kernkanal.conf"), notaus=Path("/t/notaus")), jetzt=jetzt,
                              lesen=_attrappe("\n".join(f"{k}={v}" for k, v in werte.items()) + "\n"), vorhanden=lambda p: False)
    assert zustand.grund == "kanal_zu" and zustand.tage_rest == 30


@pytest.mark.parametrize("schluessel, wert", [("MAX_TURNS", "0"), ("MAX_TURNS", "201"), ("MAX_BUDGET_USD", "0.00"),
                                              ("MAX_BUDGET_USD", "20.01"), ("MAX_BUDGET_USD", "2.005"), ("MAX_START_STUNDE", "61"),
                                              ("MAX_VERSUCH_STUNDE", "21"), ("MAX_FOLGEN", "21"), ("MIN_PLATZ_MB", "99")])
def test_i2_werte_ausserhalb_des_bereichs_lehnt_der_dispatcher_ab(schluessel, wert):
    with pytest.raises(K.KonfigFehler):
        K.konfig_lesen("/t/kernkanal.conf", lesen=_attrappe(f"{schluessel}={wert}\n"))


def test_i2_das_skript_prueft_dieselben_grenzen_wie_der_dispatcher():
    assert 'pruefe_zahl --max-turns "$2" 1 200' in SKRIPT
    assert 'pruefe_betrag --max-budget-usd "$2" 0.01 20' in SKRIPT
    assert '[ "$tage" -le 400 ]' in SKRIPT
    assert 'pruefe_zahl --max-turns' in SKRIPT and K.ZAHLEN["MAX_TURNS"] == (1, 200)


# ================================================================== Kostenvariablen im Büro (I3)
def _buero(monkeypatch, **extra):
    umgebung = {"WAMO_BETRIEB": "buero", "WAMO_ENTWICKLUNG_BUERO": "an", "WAMO_ENTWICKLUNG_ORT": "sokrates2",
                "WAMO_ENTWICKLUNG_PROFILE": PROFIL, "WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD": "3", "WAMO_ENTWICKLUNG_KOSTEN_TAG_USD": "10"}
    umgebung.update(extra)
    for name, wert in umgebung.items():
        if wert is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, wert)


@pytest.mark.parametrize("name", [eb.ENV_KOSTEN_AUFTRAG, eb.ENV_KOSTEN_TAG])
def test_im_buero_sind_die_kostenvariablen_pflicht(name, monkeypatch):
    _buero(monkeypatch)
    assert eb.buero_problem() is None
    _buero(monkeypatch, **{name: None})
    assert name in eb.buero_problem()
    _buero(monkeypatch, **{name: ""})
    assert name in eb.buero_problem()


def test_fehlende_kosten_heissen_im_buero_aus_auch_in_der_vorpruefung_des_kerns(monkeypatch):
    _buero(monkeypatch, WAMO_ENTWICKLUNG_KOSTEN_TAG_USD=None)
    monkeypatch.setattr(dev_agent, "ssh_problem", lambda: None)
    assert "WAMO_ENTWICKLUNG_KOSTEN_TAG_USD" in dev_agent.entwicklung_problem()
    _buero(monkeypatch)
    assert dev_agent.entwicklung_problem() is None


def _funktion(skript: str, name: str) -> str:
    treffer = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}$", skript, flags=re.M | re.S)
    assert treffer, f"Funktion {name} fehlt im Skript"
    return treffer.group(1)


def test_das_skript_schreibt_beide_kostenvariablen_immer():
    rumpf = _funktion(SKRIPT, "drop_in_text")
    assert "Environment=WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD=${KOSTEN_AUFTRAG:-$STD_KOSTEN_AUFTRAG_USD}" in rumpf
    assert "Environment=WAMO_ENTWICKLUNG_KOSTEN_TAG_USD=${KOSTEN_TAG:-$STD_KOSTEN_TAG_USD}" in rumpf
    assert not re.search(r"\bif\b|\[\s|&&|\|\|", rumpf), "die Zeilen des Drop-ins dürfen nicht an eine Bedingung hängen"
    std = _std_werte(SKRIPT)
    assert std["KOSTEN_AUFTRAG_USD"] and std["KOSTEN_TAG_USD"]


def test_die_standardkosten_des_skripts_nimmt_der_kern_an(monkeypatch):
    std = _std_werte(SKRIPT)
    _buero(monkeypatch, WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD=std["KOSTEN_AUFTRAG_USD"], WAMO_ENTWICKLUNG_KOSTEN_TAG_USD=std["KOSTEN_TAG_USD"])
    assert eb.buero_problem() is None
    assert eb.kosten_auftrag() == float(std["KOSTEN_AUFTRAG_USD"]) and eb.kosten_tag() == float(std["KOSTEN_TAG_USD"])


def test_das_dropin_setzt_genau_die_variablen_die_der_kern_liest():
    namen = re.findall(r"echo \"Environment=([A-Z_]+)=", _funktion(SKRIPT, "drop_in_text"))
    assert set(namen) == {eb.ENV_SCHALTER, eb.ENV_ORT, eb.ENV_PROFILE, eb.ENV_KOSTEN_AUFTRAG, eb.ENV_KOSTEN_TAG}
    assert len(namen) == len(set(namen))
    assert "WAMO_ENTWICKLUNG" not in namen and eb.ENV_BEFEHLE not in namen  # Standard aus, nie nebenbei an
    assert set(eb.ALLE_VARIABLEN) - set(namen) == {eb.ENV_BEFEHLE}


# ================================================================== ssh-Optionen des Rauchtests gegen dev_agent.ssh_argv
def test_die_ssh_optionen_des_rauchtests_gleichen_dev_agent_ssh_argv(monkeypatch):
    monkeypatch.setattr(dev_agent.shutil, "which", lambda name: "/usr/bin/ssh")
    for remote in (["ping"], ["start", "0123456789abcdef", "kern-x", "recherche"], ["status", "0123456789abcdef"]):
        erwartet = dev_agent.ssh_argv(remote)
        ist = R.ssh_argv(" ".join(remote), schluessel=dev_agent.ssh_schluessel(), known_hosts=dev_agent.ssh_known_hosts(),
                         ziel=dev_agent.ssh_ziel(), exe="/usr/bin/ssh")
        assert ist == erwartet, remote
    mit_tty = R.ssh_argv("ping", schluessel=dev_agent.ssh_schluessel(), known_hosts=dev_agent.ssh_known_hosts(), ziel=dev_agent.ssh_ziel(),
                         exe="/usr/bin/ssh", tty=True)
    assert [("-tt" if a == "-T" else a) for a in dev_agent.ssh_argv(["ping"])] == mit_tty


def test_ziel_schluessel_und_hostdatei_des_rauchtests_sind_die_standards_des_kerns(monkeypatch):
    kern_home = R.KERN_HOME.as_posix()  # /var/lib/wamo-core: das Home von wamo-core, in dem der Kern die Standards auflöst
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(kern_home)))
    monkeypatch.setenv("HOME", kern_home)
    monkeypatch.setenv("USERPROFILE", kern_home)
    for name in ("WAMO_ENTWICKLUNG_SSH_ZIEL", "WAMO_ENTWICKLUNG_SSH_SCHLUESSEL", "WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    assert R.ZIEL == dev_agent.ssh_ziel() == dev_agent.SSH_ZIEL_STANDARD
    assert R.SCHLUESSEL.as_posix() == dev_agent.ssh_schluessel().as_posix()
    assert R.KNOWN_HOSTS.as_posix() == dev_agent.ssh_known_hosts().as_posix()
    assert R.HOST == re.search(r"^KERN_IP=(\S+)$", SKRIPT, flags=re.M).group(1) == R.ZIEL.split("@", 1)[1]


def test_die_pfade_des_rauchtests_gleichen_denen_der_skripte():
    pfade = {"KERN_DATEI": "etc/ssh/authorized_keys.d/wamo-kern", "PC_DATEI": "etc/ssh/authorized_keys.d/wamo",
             "KONFIG": "etc/wamo/kernkanal.conf", "NOTAUS": "etc/wamo/kernkanal.notaus", "RAUCHTEST": "opt/wamo-core/bin/wamo-job-kern-rauchtest"}
    for name, relativ in pfade.items():
        assert getattr(R, name).as_posix() == "/" + relativ, name
    assert f'KERN_DATEI="$WURZEL/{pfade["KERN_DATEI"]}"' in SKRIPT and f'KERN_DATEI="$WURZEL/{pfade["KERN_DATEI"]}"' in RUECKWEG
    assert f'KONFIG="$WURZEL/{pfade["KONFIG"]}"' in SKRIPT and f'NOTAUS="$WURZEL/{pfade["NOTAUS"]}"' in RUECKWEG
    assert f'PC_DATEI="$WURZEL/{pfade["PC_DATEI"]}"' in SKRIPT
    assert 'RAUCHTEST="$KERNAPP/bin/wamo-job-kern-rauchtest"' in SKRIPT and 'KERNAPP="$WURZEL/opt/wamo-core"' in SKRIPT
    assert K.PFADE.kern_schluessel.as_posix() == "/" + pfade["KERN_DATEI"] and K.PFADE.notaus.as_posix() == "/" + pfade["NOTAUS"]
    assert K.PFADE.konfig.as_posix() == "/" + pfade["KONFIG"] and K.PFADE.pc_schluessel.as_posix() == "/" + pfade["PC_DATEI"]


def test_die_schluesselzeile_hat_in_skript_und_rauchtest_dieselbe_form():
    treffer = re.search(r'^\s*zeile="(restrict,.*)"$', SKRIPT, flags=re.M)
    assert treffer, "die Schlüsselzeile steht nicht mehr als zeile=... im Skript"
    zeile = treffer.group(1).replace('\\"', '"')
    # I1 mit F7: expiry-time steht zwischen from= und command= und trägt den Tag nach ABLAUF (nur ein Datum)
    assert zeile == 'restrict,from="$KERN_IP",expiry-time="$expiry",command="/opt/wamo/bin/wamo-job-dispatch" $typ $blob wamo-kern-jobs'
    kern_ip = re.search(r"^KERN_IP=(\S+)$", SKRIPT, flags=re.M).group(1)
    blob = "AAAAC3NzaC1lZDI1NTE5AAAAI" + "A" * 43
    gebaut = zeile.replace("$KERN_IP", kern_ip).replace("$expiry", "20270101").replace("$typ", "ssh-ed25519").replace("$blob", blob)
    assert R.zeile_ok(gebaut, "20261231")[0], R.zeile_ok(gebaut, "20261231")[1]  # der Rauchtest nimmt genau die Zeile an, die das Skript schreibt
    assert not R.zeile_ok(gebaut, "20270101")[0] and not R.zeile_ok(gebaut.replace(',expiry-time="20270101"', ""), "20261231")[0]


def test_expiry_time_ist_in_skript_und_rauchtest_derselbe_tag_nach_ablauf():
    """Das Skript rechnet mit date, der Rauchtest mit datetime: beide müssen am Monats- und Jahresende und im Schaltjahr gleich rechnen."""
    assert re.search(r'^folgetag\(\) \{.*date -d "\$1 \+1 day" \+%Y%m%d', SKRIPT, flags=re.M), "der Tag nach ABLAUF kommt aus folgetag()"
    for tag in ("20261231", "20260228", "20240228", "20240229", "20261111"):
        erwartet = (datetime.strptime(tag, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        assert R.folgetag(tag) == erwartet, tag
        if shutil.which("bash") and os.name != "nt":
            ist = subprocess.run(["bash", "-c", f'date -d "{tag} +1 day" +%Y%m%d'], capture_output=True, text=True).stdout.strip()
            assert ist == erwartet, tag


# ================================================================== unveränderte Dateien (Prüfsummen), SELF_REPO
# SHA256 der Dateien, die dieses Vorhaben nicht ändert: Der Anker prüft die Prüfsumme des Slot-Skripts (wamo_anker.py), Vorprüfung und
# Slot-Vorlage gehören zum selben Vertrag. Zeilenenden sind auf LF vereinheitlicht (ein Checkout unter Windows ändert die Summe nicht).
# Es gibt keinen Vergleich mit einem Git-Zweig: Nach dem Merge bliebe er leer, und jede spätere Änderung würde ihn rot färben.
PRUEFSUMMEN = {
    "infra/sokrates2/wamo-core-slots.sh": "e725f4e515dda4ce6f72fa39a5346ba132b19f39f8babc3867be590fc577f150",
    "infra/sokrates2/wamo-core-vorpruefung.sh": "32235d048877a3e2626530537c60e03dabf1ee667ef85c01e6055b32c81673c6",
    "infra/sokrates2/wamo-core@.service": "46b22cc0bfdf9000eb08431a61537ede65b3b3d7ab15f452cf3deca3bf239953",
}


def _summe(inhalt: bytes) -> str:
    return hashlib.sha256(inhalt.replace(b"\r\n", b"\n")).hexdigest()


def test_die_pruefsumme_ignoriert_die_zeilenenden_aber_sonst_nichts():
    assert _summe(b"a\r\nb\r\n") == _summe(b"a\nb\n") != _summe(b"a\nb")
    assert _summe(b"a\nb\n") != _summe(b"a\nc\n")


@pytest.mark.parametrize("datei", sorted(PRUEFSUMMEN))
def test_der_anker_prueft_diese_dateien_ihre_pruefsumme_ist_festgeschrieben(datei):
    ist = _summe((WURZEL / datei).read_bytes())
    assert ist == PRUEFSUMMEN[datei], (
        f"{datei} hat sich geändert (SHA256 jetzt {ist}). Die Datei gehört zum Vertrag mit dem Anker (wamo_anker.py prüft die Prüfsumme des "
        "Slot-Skripts) und wird von diesem Vorhaben nie geändert. Ein bewusster Wechsel sieht so aus: die Datei ändern, die neue Prüfsumme in "
        "PRUEFSUMMEN dieses Tests und im Anker eintragen und die Begründung im selben Commit nennen. War es ein Versehen: die Datei zurücksetzen.")


def test_die_vorpruefung_und_die_slot_vorlage_kennen_den_buero_schalter_nicht():
    vorpruefung = _text(INFRA / "wamo-core-vorpruefung.sh")
    einheit = _text(INFRA / "wamo-core@.service")
    assert '[ "${WAMO_ENTWICKLUNG:-aus}" = aus ]' in vorpruefung
    assert "WAMO_ENTWICKLUNG=aus" in einheit
    assert "WAMO_ENTWICKLUNG_BUERO" not in vorpruefung + einheit


def test_self_repo_bleibt_wamo_ueberall():
    assert [z for z in _text(VOICE_CORE / "dev_guard.py").split("\n") if z.startswith("SELF_REPO")] == ['SELF_REPO = "wamo"']
    assert dev_guard.SELF_REPO == dev_agent.SELF_REPO == K.SELF_REPO == "wamo"


# ================================================================== Namensvertrag (CLAUDE.md)
# Die Namen aus der Arbeitsliste (R13) und was die Umsetzung zusätzlich eingeführt hat
NAMEN_AUS_DER_ARBEITSLISTE = (
    "wamo-kern-jobs", "authorized_keys.d/wamo-kern", "kernkanal.conf", "kernkanal.notaus", "wamo-entwicklung-buero.conf",
    "wamo-job-kern-einrichten.sh", "wamo-job-kern-rueckweg.sh", "wamo_job_kern_rauchtest.py", "wamo_job_kernkanal.py",
    "WAMO_ENTWICKLUNG_BUERO", "WAMO_ENTWICKLUNG_PROFILE", "WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD", "WAMO_ENTWICKLUNG_KOSTEN_TAG_USD",
    "WAMO_ENTWICKLUNG_BEFEHLE_BUERO", "Dispatcher 7", "/var/backups/wamo-job-kern", "/var/log/wamo-job-kern", "kernkanal.jsonl",
)
NAMEN_DER_UMSETZUNG = (
    "MAX_VERSUCH_STUNDE", "`versuch`", ".notaus-", "als_kern", "wamo-job-kern-rauchtest", "entwicklung_betrieb.py", "SCHRANKEN_VERSION",
    "KANAL_GRUENDE", "GRUND_SAETZE", "kanal_gesperrt", "auftrag_quittung", "entwicklung_grund", "WAMO_JOB_KERN_WURZEL",
    "WAMO_JOB_KERN_PYTHON", "WAMO_JOB_KERN_SUCHFRIST_S", "auftragStart.ts", "entwicklungsauftrag", "aktionen.jsonl", "BueroZulassung",
    "ExposeAuthInfo", "%u-kern", "/opt/wamo/waechter", "WAMO_ENTWICKLUNG_ORT", "wamo.conf.vorher", "slot_betrieb",
    "test_kernkanal_vertrag.py", "test_entwicklung_betrieb.py", "test_kernkanal.py", "test_job_kern_skripte.py",
    "test_wamo_kern_sokrates2.py", "auftragStart.test.ts", "docs/entwicklung-im-betrieb.md",
)
# Variablen, die es schon vor dem Vorhaben gab (stehen in der README und in den .env.example, nicht im Namensvertrag)
VORHANDENE_VARIABLEN = {"WAMO_BETRIEB", "WAMO_ENTWICKLUNG", "WAMO_ABNAHME_BEFEHLE", "WAMO_CLAUDE_SCHLUESSEL_PROFIL"}


@pytest.mark.parametrize("name", NAMEN_AUS_DER_ARBEITSLISTE + NAMEN_DER_UMSETZUNG)
def test_der_namensvertrag_nennt_den_namen(name):
    assert name in NAMENSVERTRAG, f"{name} fehlt im Namensvertrag (CLAUDE.md)"


def test_jede_variable_der_neuen_dateien_steht_im_namensvertrag():
    neue_dateien = (VOICE_CORE / "entwicklung_betrieb.py", INFRA / "wamo-job-kern-einrichten.sh", INFRA / "wamo-job-kern-rueckweg.sh",
                    INFRA / "wamo_job_kern_rauchtest.py", INFRA / "wamo_job_kernkanal.py")
    gefunden = {n for datei in neue_dateien for n in re.findall(r"\bWAMO_[A-Z0-9]+(?:_[A-Z0-9]+)*\b", _text(datei))}
    assert len(gefunden) >= 8
    fehlen = sorted(n for n in gefunden - VORHANDENE_VARIABLEN if n not in NAMENSVERTRAG)
    assert not fehlen, f"nicht im Namensvertrag: {fehlen}"
