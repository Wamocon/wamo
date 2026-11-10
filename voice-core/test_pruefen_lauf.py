"""Prüflogik des Dispatcher-Befehls pruefen (infra/sokrates2/wamo_job_pruefen.py), nur Standardbibliothek.

Geprüft werden die Zeile mit argv (genau ein Feld, streng), die Antwort (eine JSON-Zeile, Ausgabe gekürzt, geschwärzt, ok nur bei
einem echten int 0) und der Lauf mit echten Prozessen (kleine Python-Aufrufe): Ausgabe als Ringpuffer, Zeitlimit, Abbruch von außen
(das Ende der Lebenslinie), nichts startet nach einem Stopp. Die Orchestrierung im Dispatcher (Sperren, Wächter, Selbsttest,
Audit) prüft test_sokrates2_pruefen.py. Läuft unter Windows und Linux; was nur unter Linux geht, ist übersprungen.

Ausführen:  .venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider test_pruefen_lauf.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
LINUX = sys.platform.startswith("linux")


def _laden():
    spec = importlib.util.spec_from_file_location("wamo_job_pruefen", INFRA / "wamo_job_pruefen.py")
    modul = importlib.util.module_from_spec(spec)
    sys.modules["wamo_job_pruefen"] = modul
    spec.loader.exec_module(modul)
    return modul


PM = sys.modules.get("wamo_job_pruefen") or _laden()  # ein Modul für alle Testdateien (der Dispatcher findet es in sys.modules)
PY = sys.executable


def zeile(**felder) -> bytes:
    return json.dumps(felder).encode() + b"\n"


def lauf(code: str, *, zeitlimit_s: float = 20, nachfrist_s: float = 5, stopps: list | None = None, env: dict | None = None):
    return PM.Lauf([PY, "-c", code], env if env is not None else None, (lambda: stopps.append(1)) if stopps is not None else (lambda: None),
                   zeitlimit_s=zeitlimit_s, nachfrist_s=nachfrist_s)


def schwaerzen_nichts(daten: bytes) -> bytes:
    return daten


# ---------------------------------------------------------------- Konsistenz
def test_das_modul_kennt_nur_die_standardbibliothek_und_die_allowlist():
    baum = ast.parse((INFRA / "wamo_job_pruefen.py").read_text(encoding="utf-8"))
    module = {n.module for n in ast.walk(baum) if isinstance(n, ast.ImportFrom) and n.level == 0}
    module |= {a.name for n in ast.walk(baum) if isinstance(n, ast.Import) for a in n.names}
    module = {m.split(".")[0] for m in module if m}
    assert module, "ohne Importe läse der Test nichts"
    assert module <= set(sys.stdlib_module_names) | {"__future__", "abnahme_befehl"}, module
    assert {"loguru", "persona", "dev_guard", "dev_agent", "abnahme", "pipecat"}.isdisjoint(module)


def test_die_festen_grenzen():
    assert PM.ZEILE_MAX_BYTES == 8 * 1024
    assert PM.AUSGABE_BYTES == 2048
    assert PM.ZEITLIMIT_S == 120
    assert PM.ANTWORT_VERSION == 1
    assert PM.FENSTER_BYTES >= 2 * PM.AUSGABE_BYTES  # genug Vorlauf, damit die Schwärzung auch am Rand greift


# ---------------------------------------------------------------- die eine stdin-Zeile
def test_gueltige_zeile_liefert_argv():
    assert PM.argv_aus_zeile(zeile(argv=["pytest", "-q"])) == ["pytest", "-q"]
    assert PM.argv_aus_zeile(zeile(argv=["uv", "run", "--no-sync", "--offline", "pytest", "-q"]))[0] == "uv"


@pytest.mark.parametrize("roh", [
    b'{"argv": ["pytest"]}',  # ohne Zeilenende
    b'{"argv": ["pytest"]}\n{"argv": ["npm", "test"]}\n',  # zwei Zeilen
    b"kein json\n", b"\n", b"", b"[]\n", b'"pytest"\n', b"null\n", b"5\n", b'{"argv": NaN}\n', b'{"argv": ["pytest"],}\n',
    b'\xef\xbb\xbf{"argv": ["pytest"]}\n', b'{"argv": ["pytest\xff"]}\n', b"[" * 5000 + b"\n", b"{" * 3000 + b"\n",
])
def test_zeile_ohne_gueltiges_json_objekt_wird_abgelehnt(roh):
    with pytest.raises(PM.Abgelehnt):
        PM.argv_aus_zeile(roh)


@pytest.mark.parametrize("felder", [
    {}, {"befehl": ["pytest"]}, {"argv": ["pytest"], "projekt": "rezepte-app"}, {"argv": ["pytest"], "cwd": "/"},
    {"argv": ["pytest"], "env": {}}, {"argv": ["pytest"], "zeitlimit": 5}, {"argv": ["pytest"], "stdin": ""},
    {"argv": ["pytest"], "argv2": []}, {"ARGV": ["pytest"]}, {"argv ": ["pytest"]},
])
def test_genau_das_feld_argv_und_kein_anderes(felder):
    with pytest.raises(PM.Abgelehnt, match="argv"):
        PM.argv_aus_zeile(zeile(**felder))


def test_doppeltes_feld_wird_abgelehnt():
    with pytest.raises(PM.Abgelehnt):
        PM.argv_aus_zeile(b'{"argv": ["pytest"], "argv": ["npm", "test"]}\n')


@pytest.mark.parametrize("argv", [
    None, "pytest -q", 5, {}, [], [None], [5], [["pytest"]], ["pytest", 5], [""], [True],
    ["bash", "-c", "id"], ["sh", "run.sh"], ["python", "-c", "print(1)"], ["npm", "install"], ["pytest", "-p", "plugin"],
    ["pytest", "../x"], ["pytest", "/etc/passwd"], ["pytest", "tests;id"], ["pytest", "$HOME"], ["pytest", "a|b"],
    ["pytest\n", "-q"], ["pytest", "-q\x00"], ["pytest", "‮"], ["/usr/bin/pytest"], ["PYTEST"], ["pytest.exe"],
    ["curl", "https://example.org"], ["uv", "run", "pytest"], ["uv", "sync"], ["uvx", "x"], ["make", "all"],
    ["pytest", *["-q"] * 13],
])
def test_die_allowlist_gilt_im_dispatcher_erneut(argv):
    with pytest.raises(PM.Abgelehnt):
        PM.argv_aus_zeile(zeile(argv=argv))


def test_die_zeile_hat_hoechstens_acht_kib_mit_zeilenende():
    gut = zeile(argv=["grep", "-q", "x" * 190, "README.md"])
    assert len(gut) < PM.ZEILE_MAX_BYTES
    assert PM.argv_aus_zeile(gut)
    zu_lang = b'{"argv": ["pytest"], "x": "' + b"a" * PM.ZEILE_MAX_BYTES + b'"}\n'
    with pytest.raises(PM.Abgelehnt, match="lang"):
        PM.argv_aus_zeile(zu_lang)
    genau = b'{"argv": ["pytest"]}' + b" " * (PM.ZEILE_MAX_BYTES - len(b'{"argv": ["pytest"]}') - 1) + b"\n"
    assert len(genau) == PM.ZEILE_MAX_BYTES
    assert PM.argv_aus_zeile(genau) == ["pytest"]
    with pytest.raises(PM.Abgelehnt, match="lang"):
        PM.argv_aus_zeile(b" " + genau)  # ein Byte zu viel


def test_die_meldung_nennt_nie_den_rohen_inhalt_unbegrenzt():
    with pytest.raises(PM.Abgelehnt) as fehler:
        PM.argv_aus_zeile(zeile(argv=["pytest", "-" + "x" * 150 + "‮"]))
    text = str(fehler.value)
    assert len(text) < 400 and "\n" not in text and "‮" not in text


# ---------------------------------------------------------------- .venv für die uv-Form
def test_venv_vorhanden_nur_mit_echtem_ordner_und_pyvenv_cfg(tmp_path):
    assert PM.venv_vorhanden(tmp_path) is False
    (tmp_path / ".venv").mkdir()
    assert PM.venv_vorhanden(tmp_path) is False  # Ordner ohne pyvenv.cfg ist keine virtuelle Umgebung
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert PM.venv_vorhanden(tmp_path) is True
    assert PM.venv_vorhanden(tmp_path / "gibt-es-nicht") is False


def test_venv_als_datei_ist_keine(tmp_path):
    (tmp_path / ".venv").write_text("x", encoding="utf-8")
    assert PM.venv_vorhanden(tmp_path) is False


@pytest.mark.skipif(not LINUX, reason="symbolische Links brauchen Rechte, die unter Windows fehlen")
def test_venv_als_link_ist_keine(tmp_path):
    ziel = tmp_path / "anderswo"
    ziel.mkdir()
    (ziel / "pyvenv.cfg").write_text("x", encoding="utf-8")
    projekt = tmp_path / "p"
    projekt.mkdir()
    (projekt / ".venv").symlink_to(ziel)
    assert PM.venv_vorhanden(projekt) is False


# ---------------------------------------------------------------- Ringpuffer
def test_der_puffer_behaelt_nur_das_ende_und_merkt_sich_das_verwerfen():
    p = PM.EndPuffer(100)
    p.add(b"a" * 50)
    assert p.ende() == (b"a" * 50, False)
    p.add(b"b" * 500)
    daten, verworfen = p.ende()
    assert verworfen is True and len(daten) == 100 and daten == b"b" * 100
    p = PM.EndPuffer(100)
    p.add(b"x" * 100)
    assert p.ende() == (b"x" * 100, False)  # genau das Fenster ist noch nichts verworfen
    p.add(b"y")
    daten, verworfen = p.ende()
    assert verworfen is True and daten == b"x" * 99 + b"y"


def test_der_puffer_waechst_nie_ueber_das_doppelte_fenster():
    p = PM.EndPuffer(1000)
    for _ in range(1000):
        p.add(b"z" * 997)
        assert len(p._daten) <= 2 * 1000


# ---------------------------------------------------------------- Ausgabe: kürzen, schwärzen, bereinigen
def test_ausgabe_hoechstens_zweitausendachtundvierzig_bytes_das_ende_bleibt():
    daten = b"Zeile\n" * 2000 + b"2 failed, 3 passed\n"
    text, gekuerzt = PM.ausgabe_text(daten, False, schwaerzen_nichts)
    assert gekuerzt is True and len(text.encode("utf-8")) <= PM.AUSGABE_BYTES
    assert text.endswith("2 failed, 3 passed\n")
    kurz, gekuerzt = PM.ausgabe_text(b"ok\n", False, schwaerzen_nichts)
    assert (kurz, gekuerzt) == ("ok\n", False)


def test_ausgabe_verworfen_im_puffer_heisst_gekuerzt_auch_wenn_der_rest_kurz_ist():
    assert PM.ausgabe_text(b"ende\n", True, schwaerzen_nichts) == ("ende\n", True)


def test_ausgabe_mit_mehrbytezeichen_bleibt_gueltig_und_unter_der_grenze():
    daten = ("ä" * 3000).encode("utf-8")
    text, gekuerzt = PM.ausgabe_text(daten, False, schwaerzen_nichts)
    assert gekuerzt and set(text) == {"ä"} and len(text.encode("utf-8")) <= PM.AUSGABE_BYTES
    text, _ = PM.ausgabe_text(daten[1:], False, schwaerzen_nichts)  # beginnt mitten in einem Zeichen
    assert "�" not in text or len(text.encode("utf-8")) <= PM.AUSGABE_BYTES
    kaputt, _ = PM.ausgabe_text(b"\xff" * 5000, False, schwaerzen_nichts)  # jedes ungültige Byte würde drei Bytes
    assert len(kaputt.encode("utf-8")) <= PM.AUSGABE_BYTES


def test_ausgabe_wird_mit_der_uebergebenen_schwaerzung_bereinigt_bevor_gekuerzt_wird():
    geheim = b"sk-ant-api03-" + b"G" * 40

    def schwaerzen(daten: bytes) -> bytes:
        return daten.replace(geheim, b"[GEHEIM]")
    # das Geheimnis liegt über der Schnittkante der letzten 2048 Bytes: nur der ganze Wert lässt sich ersetzen
    daten = b"x" * 3000 + geheim + b"y" * 2030
    text, gekuerzt = PM.ausgabe_text(daten, False, schwaerzen)
    assert gekuerzt and "G" * 5 not in text and "sk-ant" not in text and "[GEHEIM]" in text


def test_steuerzeichen_der_ausgabe_fallen_heraus_zeilenumbruch_und_tab_bleiben():
    text, _ = PM.ausgabe_text(b"a\x00b\x1b[31mrot\x1b[0m\tc\r\nd\x7fe\n", False, schwaerzen_nichts)
    assert "\x00" not in text and "\x1b" not in text and "\x7f" not in text and "\r" not in text
    assert "\n" in text and "\t" in text and "rot" in text


# ---------------------------------------------------------------- die Antwort
SCHLUESSEL = ["v", "ok", "exit", "abgebrochen", "grund", "dauer_s", "ausgabe", "gekuerzt"]


def ergebnis(**felder):
    return PM.Ergebnis(**{"rc": 0, "abgebrochen": False, "grund": None, "dauer_s": 1.23456, "ausgabe": b"", "verworfen": False, **felder})


def test_die_antwort_hat_genau_diese_felder_in_dieser_reihenfolge():
    a = PM.antwort(ergebnis(ausgabe=b"3 passed\n"), schwaerzen_nichts)
    assert list(a) == SCHLUESSEL
    assert a == {"v": 1, "ok": True, "exit": 0, "abgebrochen": False, "grund": None, "dauer_s": 1.23, "ausgabe": "3 passed\n",
                 "gekuerzt": False}
    zeile_ = json.dumps(a, ensure_ascii=False)
    assert "\n" not in zeile_ and json.loads(zeile_) == a  # eine Zeile, vollständig als JSON lesbar


def test_die_leere_antwort_fuer_nicht_ausgefuehrt():
    a = PM.antwort_leer("belegt: Dieser Auftrag läuft gerade")
    assert list(a) == SCHLUESSEL
    assert (a["ok"], a["exit"], a["abgebrochen"], a["dauer_s"], a["ausgabe"], a["gekuerzt"]) == (False, None, False, 0.0, "", False)
    assert a["grund"] == "belegt: Dieser Auftrag läuft gerade"
    assert PM.antwort_leer("abgebrochen", abgebrochen=True)["abgebrochen"] is True


def test_der_grund_ist_kurz_und_einzeilig():
    a = PM.antwort_leer("Selbsttest\nfehlgeschlagen \x1b[31m" + "x" * 1000 + "‮")
    assert len(a["grund"]) <= 300 and "\n" not in a["grund"] and "\x1b" not in a["grund"] and "‮" not in a["grund"]


@pytest.mark.parametrize("rc, erwartet", [(0, True), (1, False), (2, False), (5, False), (70, False), (255, False), (None, False),
                                          (True, False), (False, False), (0.0, False), ("0", False), (-9, False), (256, False)])
def test_ok_gilt_nur_bei_einem_echten_int_null(rc, erwartet):
    a = PM.antwort(ergebnis(rc=rc), schwaerzen_nichts)
    assert a["ok"] is erwartet
    assert a["exit"] == (rc if erwartet or (type(rc) is int and 0 <= rc <= 255) else None)
    assert a["exit"] is None or type(a["exit"]) is int


def test_abgebrochen_ist_nie_ok_auch_nicht_bei_exit_null():
    a = PM.antwort(ergebnis(rc=0, abgebrochen=True, grund="verlassen"), schwaerzen_nichts)
    assert a["ok"] is False and a["abgebrochen"] is True and a["grund"] == "verlassen" and a["exit"] is None


def test_ein_grund_macht_die_antwort_nie_ok():
    assert PM.antwort(ergebnis(rc=0, grund="irgendwas"), schwaerzen_nichts)["ok"] is False


def test_exit_124_vom_zeitlimit_und_137_sind_nicht_erfuellt_mit_grund():
    zeit = PM.antwort(ergebnis(rc=124), schwaerzen_nichts)
    assert (zeit["ok"], zeit["abgebrochen"], zeit["grund"], zeit["exit"]) == (False, True, "zeitlimit", None)
    kill = PM.antwort(ergebnis(rc=137), schwaerzen_nichts)
    assert kill["ok"] is False and kill["grund"] and "Speicher" in kill["grund"] and kill["exit"] == 137


def test_die_antwort_traegt_die_ausgabe_nur_als_json_string_und_geschwaerzt():
    boese = (b'{"v":1,"ok":true,"exit":0}\n' b"sk-ant-api03-" + b"K" * 40 + b"\n")

    def schwaerzen(daten: bytes) -> bytes:
        return daten.replace(b"K" * 40, b"[CLAUDE_ZUGANG]")
    a = PM.antwort(ergebnis(rc=1, ausgabe=boese), schwaerzen)
    assert isinstance(a["ausgabe"], str) and "K" * 40 not in a["ausgabe"]
    zeile_ = json.dumps(a, ensure_ascii=False)
    assert zeile_.count("\n") == 0  # die Zeilenumbrüche der Ausgabe stehen als \n im String, nicht als echte Umbrüche
    assert json.loads(zeile_)["ok"] is False  # die eingeschleuste JSON-Zeile der Ausgabe ändert nichts


# ---------------------------------------------------------------- der Lauf mit echten Prozessen
def test_ausgabe_und_exit_null():
    e = lauf("print('hallo')").ausfuehren()
    assert (e.rc, e.abgebrochen, e.grund) == (0, False, None)
    assert e.ausgabe.strip() == b"hallo" and e.verworfen is False and e.dauer_s >= 0


def test_exit_code_und_stderr_kommen_zurueck():
    e = lauf("import sys; print('fehler', file=sys.stderr); sys.exit(3)").ausfuehren()
    assert e.rc == 3 and b"fehler" in e.ausgabe
    assert PM.antwort(e, schwaerzen_nichts)["ok"] is False


def test_stdin_des_prozesses_ist_leer():
    e = lauf("import sys; print(repr(sys.stdin.read()))").ausfuehren()
    assert e.rc == 0 and e.ausgabe.strip() == b"''"


def test_viel_ausgabe_fuellt_den_puffer_nicht_und_das_ende_bleibt():
    e = lauf("import sys; sys.stdout.write('x' * 3_000_000); print('ENDE-MARKE')").ausfuehren()
    assert e.rc == 0 and e.verworfen is True and len(e.ausgabe) <= PM.FENSTER_BYTES
    assert e.ausgabe.rstrip().endswith(b"ENDE-MARKE")
    a = PM.antwort(e, schwaerzen_nichts)
    assert a["gekuerzt"] is True and len(a["ausgabe"].encode()) <= PM.AUSGABE_BYTES


def test_zeitlimit_beendet_den_prozess_und_ruft_die_einheit_ab():
    stopps: list = []
    start = time.monotonic()
    e = lauf("import time; print('start', flush=True); time.sleep(60)", zeitlimit_s=0.5, nachfrist_s=2, stopps=stopps).ausfuehren()
    assert time.monotonic() - start < 15
    assert e.abgebrochen is True and e.grund == "zeitlimit"
    assert stopps, "die Einheit des Auftrags muss beim Zeitlimit beendet werden"
    assert b"start" in e.ausgabe  # was bis dahin kam, bleibt
    a = PM.antwort(e, schwaerzen_nichts)
    assert a["ok"] is False and a["grund"] == "zeitlimit" and a["exit"] is None


def test_stoppen_von_aussen_beendet_den_prozess_sofort_und_ruft_die_einheit_ab():
    """Das Ende der Lebenslinie (EOF auf stdin des Dispatchers) ruft stoppen('verlassen')."""
    stopps: list = []
    l = lauf("import time; time.sleep(60)", zeitlimit_s=60, nachfrist_s=60, stopps=stopps)
    threading.Timer(0.5, l.stoppen, args=("verlassen",)).start()
    start = time.monotonic()
    e = l.ausfuehren()
    assert time.monotonic() - start < 15
    assert (e.abgebrochen, e.grund) == (True, "verlassen") and stopps
    assert l.gestoppt is True and l.grund == "verlassen"


def test_nach_einem_stopp_vor_dem_start_startet_nichts():
    popen = mock.Mock(side_effect=AssertionError("nichts starten"))
    stopps: list = []
    l = PM.Lauf(["x"], {}, lambda: stopps.append(1), popen=popen)
    l.stoppen("verlassen")
    e = l.ausfuehren()
    assert (e.rc, e.abgebrochen, e.grund) == (None, True, "verlassen") and stopps == [1]
    popen.assert_not_called()


def test_ein_spaetes_stoppen_nach_dem_ende_signalisiert_nichts_mehr():
    """Das Ende der Lebenslinie kommt oft kurz nach dem Lauf: dann darf keine Prozessgruppe mehr angefasst werden (die Kennung
    des eingesammelten Kindes könnte neu vergeben sein)."""
    stopps: list = []
    l = lauf("print('fertig')", stopps=stopps)
    assert l.ausfuehren().rc == 0
    with mock.patch.object(PM.os, "killpg", create=True, side_effect=AssertionError("keine Signale mehr")),          mock.patch.object(PM.subprocess.Popen, "terminate", side_effect=AssertionError("keine Signale mehr")),          mock.patch.object(PM.subprocess.Popen, "kill", side_effect=AssertionError("keine Signale mehr")):
        l.stoppen("verlassen")
    assert l.gestoppt and stopps == [1]  # die Einheit wird noch einmal beendet (harmlos), das Kind nicht


def test_stoppen_ist_idempotent_der_erste_grund_gilt():
    stopps: list = []
    l = PM.Lauf(["x"], {}, lambda: stopps.append(1))
    l.stoppen("verlassen")
    l.stoppen("zeitlimit")
    assert l.grund == "verlassen" and stopps == [1]


def test_nicht_startbar_heisst_nicht_erfuellt_nie_ein_wurf():
    e = PM.Lauf(["x"], {}, lambda: None, popen=mock.Mock(side_effect=FileNotFoundError("weg"))).ausfuehren()
    assert e.rc is None and e.abgebrochen is False and "nicht startbar" in (e.grund or "") and "FileNotFoundError" in e.grund
    assert PM.antwort(e, schwaerzen_nichts)["ok"] is False


def test_ein_fehler_beim_beenden_der_einheit_stoert_den_abschluss_nicht():
    def kaputt():
        raise RuntimeError("systemctl weg")
    l = lauf("import time; time.sleep(60)", zeitlimit_s=0.3, nachfrist_s=0.3)
    l._stop_einheit = kaputt
    e = l.ausfuehren()
    assert e.abgebrochen is True and e.grund == "zeitlimit"


def test_der_prozess_bekommt_genau_die_uebergebene_umgebung_und_argv():
    gesehen: list = []

    class Attrappe:
        pid = 4242
        returncode = 0
        stdout = None

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            pass

    def popen(argv, **kwargs):
        gesehen.append((argv, kwargs))
        return Attrappe()
    PM.Lauf(["/opt/wamo/bin/wamo-job.sh", "--modus", "pruefen"], {"PATH": "/usr/bin"}, lambda: None, popen=popen, cwd="/").ausfuehren()
    argv, kwargs = gesehen[0]
    assert argv == ["/opt/wamo/bin/wamo-job.sh", "--modus", "pruefen"]
    assert kwargs["env"] == {"PATH": "/usr/bin"} and kwargs["cwd"] == "/"
    assert kwargs["stdin"] is not None and kwargs["stdin"] == __import__("subprocess").DEVNULL
    assert kwargs["stderr"] == __import__("subprocess").STDOUT and kwargs["stdout"] == __import__("subprocess").PIPE
    assert "shell" not in kwargs or kwargs["shell"] is False


@pytest.mark.skipif(not LINUX, reason="Prozessgruppen und Signale nur unter Linux")
def test_das_zeitlimit_beendet_auch_die_enkel_des_prozesses():
    stopps: list = []
    code = ("import os, subprocess, sys, time; "
            "k = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "print(k.pid, flush=True); time.sleep(60)")
    e = lauf(code, zeitlimit_s=0.5, nachfrist_s=2, stopps=stopps).ausfuehren()
    enkel = int(e.ausgabe.split()[0])
    time.sleep(0.3)
    import os
    with pytest.raises(ProcessLookupError):
        os.kill(enkel, 0)


# ---------------------------------------------------------------- Protokollzeile aus freiem Inhalt
def test_die_protokollzeile_hat_nie_steuer_format_oder_trennzeichen():
    boese = "ok\r\nGEFAELSCHT pruefen_ende exit=0\x1b[31m‮​ zeile x\x00\x7f\x85﻿ende\U000e0041"
    text = PM.log_text(boese)
    assert text.startswith("ok GEFAELSCHT pruefen_ende exit=0") and text.endswith("ende")
    import unicodedata
    assert not [c for c in text if unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")], repr(text)
    assert "\n" not in text and "\r" not in text and "\t" not in text


def test_die_protokollzeile_wird_gekuerzt_und_normale_zeichen_bleiben():
    assert PM.log_text("Größe ä ö ü ß 2 GiB") == "Größe ä ö ü ß 2 GiB"
    lang = PM.log_text("x" * 5000)
    assert len(lang) == PM.LOG_ZEICHEN and lang.endswith("…")
    assert len(PM.log_text("y" * 50, 20)) == 20


def test_die_protokollzeile_schwaerzt_auch_ein_durch_unsichtbare_zeichen_zerlegtes_geheimnis():
    import re
    schluessel = "sk-ant-api03-" + "A" * 30
    getrennt = schluessel[:8] + "​" + schluessel[8:] + " ende"
    schwaerzen = lambda daten: re.sub(rb"sk-ant-[A-Za-z0-9_-]{20,}", b"[GESCHWAERZT]", daten)  # noqa: E731
    text = PM.log_text(f"Fehler: {getrennt}", schwaerzen=schwaerzen)
    assert "AAAA" not in text and "[GESCHWAERZT] ende" in text


# ---------------------------------------------------------------- Platte: Größe des Projekts begrenzt und mit Zeitbudget
def test_die_festen_grenzen_der_plattenpruefung():
    assert PM.PLATZ_MIN_BYTES == 2 * 1024**3 and PM.PROJEKT_MAX_BYTES == 2 * 1024**3 and PM.PLATTEN_ZEIT_S == 5.0
    for grund in (PM.PLATZ_ZU_WENIG, PM.PROJEKT_ZU_GROSS, PM.PLATTE_NICHT_MESSBAR):
        assert grund.startswith("nicht möglich") and "{" not in grund and "\n" not in grund  # feste Texte


def test_die_groesse_zaehlt_dateien_in_unterordnern(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x" * 1000)
    (tmp_path / "unter" / "tief").mkdir(parents=True)
    (tmp_path / "unter" / "tief" / "b.bin").write_bytes(b"y" * 2000)
    groesse = PM.ordner_bytes(tmp_path, 10**9, 5.0)
    assert groesse is not None and 3000 <= groesse < 3000 + 256 * 1024


def test_die_groesse_bricht_bei_ueberschreitung_ab(tmp_path):
    for i in range(20):
        (tmp_path / f"d{i}").write_bytes(b"x" * 1000)
    assert PM.ordner_bytes(tmp_path, 2500, 5.0) is None
    assert PM.ordner_bytes(tmp_path, 10**9, 5.0) is not None


def test_die_groesse_bricht_beim_zeitbudget_ab(tmp_path):
    for i in range(30):
        (tmp_path / f"d{i}").write_bytes(b"x")
    takt = iter(range(10**6))
    assert PM.ordner_bytes(tmp_path, 10**9, 3.0, uhr=lambda: float(next(takt))) is None  # jeder Aufruf der Uhr: eine Sekunde
    assert PM.ordner_bytes(tmp_path, 10**9, 5.0, uhr=lambda: 0.0) is not None  # Uhr steht: nie überschritten


def test_ein_nicht_lesbarer_ordner_heisst_nicht_messbar(tmp_path, monkeypatch):
    (tmp_path / "geheim").mkdir()
    echt = PM.os.scandir

    def verweigert(pfad):
        if str(pfad).endswith("geheim"):
            raise PermissionError(13, "verweigert")
        return echt(pfad)
    monkeypatch.setattr(PM.os, "scandir", verweigert)
    assert PM.ordner_bytes(tmp_path, 10**9, 5.0) is None  # fail-closed: was nicht messbar ist, gilt als zu groß


@pytest.mark.skipif(not LINUX, reason="symbolische Links brauchen Rechte, die unter Windows fehlen")
def test_die_groesse_folgt_keinem_link(tmp_path):
    gross = tmp_path / "gross"
    gross.mkdir()
    (gross / "riesig.bin").write_bytes(b"x" * 500_000)
    projekt = tmp_path / "projekt"
    projekt.mkdir()
    (projekt / "verweis").symlink_to(gross)
    groesse = PM.ordner_bytes(projekt, 10**9, 5.0)
    assert groesse is not None and groesse < 100_000  # der Link zählt als Link, nicht sein Ziel


def test_der_plattenbefund_lautet_ok_zu_wenig_platz_zu_gross_oder_nicht_messbar(tmp_path, monkeypatch):
    (tmp_path / "f").write_bytes(b"x" * 100)
    monkeypatch.setattr(PM, "freier_platz", lambda pfad: 10 * 1024**3)
    assert PM.platten_grund(tmp_path, tmp_path) is None
    monkeypatch.setattr(PM, "freier_platz", lambda pfad: 2 * 1024**3 - 1)
    assert PM.platten_grund(tmp_path, tmp_path) == PM.PLATZ_ZU_WENIG
    monkeypatch.setattr(PM, "freier_platz", lambda pfad: 2 * 1024**3)
    assert PM.platten_grund(tmp_path, tmp_path) is None  # genau die Mindestmenge reicht
    monkeypatch.setattr(PM, "ordner_bytes", lambda *a, **k: None)
    assert PM.platten_grund(tmp_path, tmp_path) == PM.PROJEKT_ZU_GROSS
    monkeypatch.setattr(PM, "freier_platz", lambda pfad: (_ for _ in ()).throw(OSError("weg")))
    assert PM.platten_grund(tmp_path, tmp_path) == PM.PLATTE_NICHT_MESSBAR


def test_der_plattenbefund_misst_mit_den_festen_grenzen(tmp_path, monkeypatch):
    gesehen: list = []
    monkeypatch.setattr(PM, "freier_platz", lambda pfad: gesehen.append(("platz", pfad)) or 10 * 1024**3)
    monkeypatch.setattr(PM, "ordner_bytes", lambda pfad, grenze, frist, **kw: gesehen.append(("ordner", pfad, grenze, frist)) or 1)
    projekt, heim = tmp_path / "p", tmp_path / "h"
    assert PM.platten_grund(projekt, heim) is None
    assert gesehen == [("platz", heim), ("ordner", projekt, PM.PROJEKT_MAX_BYTES, PM.PLATTEN_ZEIT_S)]


# ---------------------------------------------------------------- Pfade im Aufruf bleiben im Projektordner (auch über Links)
@pytest.mark.parametrize("argv", [
    ["make", "test"], ["pytest", "-q"], ["pytest", "tests/test_x.py::TestA::test_b"], ["grep", "-rn", "muster", "src", "README.md"],
    ["grep", "-q", "/etc/passwd", "a.txt"],  # ein Suchmuster, kein Pfad
    ["eslint", "src", "--max-warnings=0"], ["tsc", "--noEmit", "-p", "tsconfig.json"], ["go", "test", "./..."], ["npm", "test"],
])
def test_pfade_im_projekt_sind_in_ordnung(tmp_path, argv):
    assert PM.pfad_grund(argv, tmp_path) is None


@pytest.mark.parametrize("argv", [
    ["grep", "-r", "muster", "verweis"], ["pytest", "verweis/test_x.py::t"], ["eslint", "verweis"],
    ["tsc", "--noEmit", "--project=verweis/tsconfig.json"], ["pytest", "-q", "src/verweis"], ["grep", "-rn", "x", "ok.txt", "verweis"],
])
def test_ein_pfad_der_ueber_einen_link_hinausfuehrt_wird_abgewiesen(tmp_path, argv):
    """Mit ersetztem realpath, damit der Test überall läuft; der Test mit echtem Link steht darunter (nur Linux)."""
    def realpath(pfad):
        pfad = str(pfad)
        return "/etc/wamo-fremd" if "verweis" in pfad else pfad
    grund = PM.pfad_grund(argv, tmp_path, realpath=realpath)
    assert grund is not None and "Projektordner" in grund and "\n" not in grund
    assert PM.pfad_grund(argv, tmp_path, realpath=lambda p: str(p)) is None  # ohne Link derselbe Aufruf in Ordnung


@pytest.mark.skipif(not LINUX, reason="symbolische Links brauchen Rechte, die unter Windows fehlen")
def test_ein_echter_link_aus_dem_projekt_heraus_wird_abgewiesen_einer_nach_innen_nicht(tmp_path):
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (aussen / "x.txt").write_text("x", encoding="utf-8")
    projekt = tmp_path / "projekt"
    (projekt / "src").mkdir(parents=True)
    (projekt / "nach_aussen").symlink_to(aussen)
    (projekt / "relativ_aussen").symlink_to("../aussen")
    (projekt / "nach_etc").symlink_to("/etc")
    (projekt / "nach_innen").symlink_to("src")
    (projekt / "kette").symlink_to("nach_aussen")
    for ziel in ("nach_aussen", "relativ_aussen", "nach_etc", "kette"):
        assert PM.pfad_grund(["grep", "-r", "x", ziel], projekt), ziel
        assert PM.pfad_grund(["pytest", f"{ziel}/x.txt"], projekt), ziel
    assert PM.pfad_grund(["grep", "-r", "x", "nach_innen"], projekt) is None
    assert PM.pfad_grund(["grep", "-r", "x", "src", "gibt_es_nicht"], projekt) is None


def test_die_pfadpruefung_nimmt_das_programm_und_optionen_nicht_als_pfad(tmp_path):
    gesehen: list[str] = []

    def realpath(pfad):
        gesehen.append(str(pfad))
        return str(pfad)
    PM.pfad_grund(["make", "-q"], tmp_path, realpath=realpath)
    assert not any(s.endswith("make") or s.endswith("-q") for s in gesehen)
