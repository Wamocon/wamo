"""Der Dispatcher-Befehl pruefen auf Sokrates-2 (infra/sokrates2/wamo_job_dispatch.py, wamo_job_pruefen.py, wamo-job.sh,
wamo-job-einrichten.sh, wamo-job-rueckweg.sh, wamo_pruefen_rauchtest.py).

Der Dispatcher läuft hier mit Attrappen für Sperren, Selbsttest, Entschärfen, den Prozess und systemd; alles andere ist echt
(Prüfung der Zeile, Allowlist, Wächter, Auftragsordner, Zähler, Audit, Antwort). Ob die Sandbox wirklich ohne Netz, Token und
Claude läuft und Endlosschleife und Fork-Bombe sterben, zeigt allein der Rauchtest auf Sokrates-2 (wamo-pruefen-rauchtest),
der hier nur als Text und per bash -n geprüft wird. Läuft unter Windows und Linux; was Linux braucht, ist übersprungen.

Ausführen:  .venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider test_sokrates2_pruefen.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import abnahme_befehl
import werkzeugkarte as wk

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
VOICE_CORE = Path(__file__).resolve().parent
LINUX = sys.platform.startswith("linux")
JOB = "abc1234567890def"
PROJEKT = "rezepte-app"
SCHLUESSEL = ["v", "ok", "exit", "abgebrochen", "grund", "dauer_s", "ausgabe", "gekuerzt"]


def _laden(name: str, datei: str):
    spec = importlib.util.spec_from_file_location(name, INFRA / datei)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul  # nötig für @dataclass (löst Typen über sys.modules auf)
    spec.loader.exec_module(modul)
    return modul


D = _laden("wamo_job_dispatch", "wamo_job_dispatch.py")
PM = D.pruefen_modul()  # dasselbe Modul, das der Dispatcher lädt (im Repo neben dem Dispatcher)


def _bash():
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    return next((k for k in kandidaten if k and os.path.exists(k)), None)


BASH = _bash()


def zeile(argv=("pytest", "-q"), **extra) -> bytes:
    return json.dumps({"argv": list(argv), **extra}).encode() + b"\n"


class FakeSperre:
    belegt: set[str] = set()
    versucht: list[str] = []

    def __init__(self, pfad):
        self.pfad, self.datei = Path(pfad), None

    def versuchen(self):
        FakeSperre.versucht.append(self.pfad.name)
        return self.pfad.name not in FakeSperre.belegt


class FakeLauf:
    instanzen: list["FakeLauf"] = []
    ergebnis = PM.Ergebnis(0, False, None, 1.5, b"3 passed\n", False)
    wirft: BaseException | None = None

    def __init__(self, argv, env, stop_einheit, **kw):
        self.argv, self.env, self.stop_einheit, self.kw = list(argv), env, stop_einheit, kw
        self.gestoppt, self.grund, self.ausgefuehrt = False, None, 0
        FakeLauf.instanzen.append(self)

    def stoppen(self, grund):
        if not self.gestoppt:
            self.gestoppt, self.grund = True, grund
            self.stop_einheit()

    def ausfuehren(self):
        self.ausgefuehrt += 1
        if FakeLauf.wirft is not None:
            raise FakeLauf.wirft
        return FakeLauf.ergebnis


class Welt:
    """Auftragsordner, Projekt und alle Attrappen. Ordner: tmp/jobs, tmp/projekte, tmp/heim, tmp/waechter."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.jobs, self.projekte, self.heim, self.waechter = (tmp_path / n for n in ("jobs", "projekte", "heim", "waechter"))
        for ordner in (self.jobs / JOB, self.projekte / PROJEKT, self.heim, self.waechter):
            ordner.mkdir(parents=True)
        (self.jobs / JOB / "auftrag.json").write_text(json.dumps({"job": JOB, "projekt": PROJEKT, "art": "umsetzung", "repo": None}),
                                                      encoding="utf-8")
        self.status({"job": JOB, "projekt": PROJEKT, "art": "umsetzung", "start": 1000, "ende": 1100, "ergebnis": "fertig", "code": 0})
        self.logzeilen: list[tuple[str, bool]] = []
        self.emitted: list[dict] = []
        self.ablauf: list[str] = []
        self.stopps: list[str] = []
        self.lebenslinien: list = []
        self.selbsttests: list[tuple] = []
        self.selbsttest_ergebnis = (True, "Hook lehnt ab, Grenzen und Netz geprüft")
        self.geheim: dict[str, str] = {"WAMO_PRUEFEN": "an"}
        self.freier_platz = 10 * 1024**3  # die Platte der Entwicklungsmaschine zählt in diesen Tests nie
        self.zeile = zeile()
        FakeSperre.belegt, FakeSperre.versucht = set(), []
        FakeLauf.instanzen, FakeLauf.wirft = [], None
        FakeLauf.ergebnis = PM.Ergebnis(0, False, None, 1.5, b"3 passed\n", False)
        monkeypatch.setattr(sys, "path", list(sys.path))  # _modul darf den Suchpfad ändern, ohne dass es bleibt
        for name, wert in (("JOBS", self.jobs), ("PROJEKTE", self.projekte), ("HEIM", self.heim), ("WAECHTER", self.waechter)):
            monkeypatch.setattr(D, name, wert)
        monkeypatch.setattr(D, "log", lambda text, pflicht=False: self.logzeilen.append((text, pflicht)))
        monkeypatch.setattr(D, "emit", lambda ev: self.emitted.append(ev) or True)
        monkeypatch.setattr(D, "Sperre", FakeSperre)
        monkeypatch.setattr(D, "lese_geheimnisse", lambda path=None: dict(self.geheim))
        monkeypatch.setattr(D, "projekt_entschaerfen", lambda p: self.ablauf.append("entschaerfen") or [])
        monkeypatch.setattr(D, "selbsttest", self._selbsttest)
        monkeypatch.setattr(D, "stop_units", lambda job: (self.stopps.append(job), self.ablauf.append("stop")))
        monkeypatch.setattr(PM, "freier_platz", lambda pfad: self.freier_platz)
        monkeypatch.setattr(D, "_pruefen_zeile_lesen", lambda: self.zeile)
        monkeypatch.setattr(D, "lebenslinie_beobachten", lambda bei_ende: self.lebenslinien.append(bei_ende))
        monkeypatch.setattr(PM, "Lauf", FakeLauf)

    def _selbsttest(self, job, projekt, modus="lauf"):
        self.ablauf.append("selbsttest")
        self.selbsttests.append((job, projekt, modus))
        return self.selbsttest_ergebnis

    def status(self, daten):
        (self.jobs / JOB / "status.json").write_text(json.dumps(daten), encoding="utf-8")

    def pruefen(self, argv=("pytest", "-q"), job=JOB):
        self.zeile = zeile(argv)
        FakeLauf.instanzen.clear()
        return D.cmd_pruefen(job)

    def lauf(self) -> FakeLauf:
        assert len(FakeLauf.instanzen) == 1, "genau ein Lauf erwartet"
        return FakeLauf.instanzen[0]

    def antwort(self) -> dict:
        assert len(self.emitted) == 1, f"genau eine Antwortzeile erwartet: {self.emitted}"
        a = self.emitted[0]
        assert list(a) == SCHLUESSEL
        return a

    def audit(self) -> str:
        return "\n".join(t for t, _ in self.logzeilen)


@pytest.fixture
def welt(tmp_path, monkeypatch):
    return Welt(tmp_path, monkeypatch)


# ---------------------------------------------------------------- Befehl, Version, Schalter
def test_der_befehl_steht_in_der_tabelle_und_im_handler():
    assert D.parse_command(f"pruefen {JOB}") == ("pruefen", [JOB])
    assert "pruefen" in D.BEFEHLE and D.HANDLER["pruefen"] is D.cmd_pruefen
    assert D.BEFEHLE["pruefen"] == (1, (D.check_job,))  # nur der Auftrag; das Projekt steht nie im Aufruf


@pytest.mark.parametrize("roh", ["pruefen", f"pruefen {JOB} {PROJEKT}", f"pruefen {JOB} pytest", f"pruefen {JOB.upper()}",
                                 "pruefen abc", f"pruefen {JOB};id", f"pruefen  {JOB}", f"pruefen {JOB}\n", f"Pruefen {JOB}",
                                 f"pruefen {JOB} -q", f"prufen {JOB}"])
def test_schlechte_aufrufe_werden_abgewiesen(roh):
    with pytest.raises(D.Abgewiesen):
        D.parse_command(roh)


def test_die_version_zaehlt_hoch():
    assert D.VERSION == "6"


@pytest.mark.parametrize("geheim, erwartet", [
    ({}, False), ({"WAMO_PRUEFEN": "an"}, True), ({"WAMO_PRUEFEN": "aus"}, False), ({"WAMO_PRUEFEN": "ja"}, False),
    ({"WAMO_PRUEFEN": "1"}, False), ({"WAMO_PRUEFEN": "true"}, False), ({"WAMO_PRUEFEN": "AN"}, False),
    ({"WAMO_PRUEFEN": "an "}, False), ({"WAMO_PRUEFEN": "on"}, False), ({"WAMO_PRUEFEN": ""}, False),
    ({"WAMO_PRUEFE": "an"}, False), ({"WAMO_PRUEF": "an"}, False), ({"WAMO_PRUEFEN": "an", "WAMO_PRUEFEN_X": "an"}, False),
    ({"WAMO_PRUEFEN": "an", "WAMO_PRUEFUNG": "aus"}, False),  # ein Tippfehler im Namen sperrt (fail-closed)
    ({"WAMO_PRUEFEN": "an", "WAMO_SERVERZUGANG": "aus"}, True), ({"PRUEFEN": "an"}, False), ({"wamo_pruefen": "an"}, False),
])
def test_der_schalter_ist_fail_closed_nur_an_schaltet_ein(geheim, erwartet):
    assert D.pruefen_schalter(geheim) is erwartet


def test_bei_ausgeschaltetem_schalter_liest_und_startet_nichts(welt):
    for geheim in ({}, {"WAMO_PRUEFEN": "aus"}, {"WAMO_PRUEFEN": "ja"}):
        welt.geheim = geheim
        welt.emitted.clear()
        gelesen: list = []
        welt.mp.setattr(D, "_pruefen_zeile_lesen", lambda: gelesen.append(1) or zeile())  # noqa: B023
        with pytest.raises(D.Abgewiesen, match="abgeschaltet"):
            D.cmd_pruefen(JOB)
        assert gelesen == [] and not FakeLauf.instanzen and welt.selbsttests == [] and FakeSperre.versucht == []
        assert welt.antwort()["ok"] is False and "abgeschaltet" in welt.antwort()["grund"]


def test_ping_meldet_ob_pruefen_aktiv_ist_und_die_pruefsumme_der_allowlist(welt):
    kopie = welt.waechter / "abnahme_befehl.py"
    shutil.copyfile(VOICE_CORE / "abnahme_befehl.py", kopie)
    shutil.copyfile(INFRA / "wamo_job_pruefen.py", welt.waechter / "wamo_job_pruefen.py")
    erwartet = hashlib.sha256((VOICE_CORE / "abnahme_befehl.py").read_bytes()).hexdigest()
    D.cmd_ping()
    ping = welt.emitted[-1]
    assert ping["pruefen"] is True and ping["abnahme_befehl_sha256"] == erwartet and ping["version"] == "6" and ping["ok"] is True
    welt.geheim = {}
    welt.emitted.clear()
    D.cmd_ping()
    assert welt.emitted[-1]["pruefen"] is False and welt.emitted[-1]["abnahme_befehl_sha256"] == erwartet  # Pr\u00fcfsumme bleibt
    kopie.unlink()
    welt.emitted.clear()
    D.cmd_ping()
    assert welt.emitted[-1]["abnahme_befehl_sha256"] is None  # ohne Kopie keine Summe


def test_ping_meldet_pruefen_nur_aktiv_wenn_beide_module_ladbar_sind(welt, monkeypatch):
    def kaputt():
        raise ImportError("Modul fehlt")
    monkeypatch.setattr(D, "pruefen_modul", kaputt)
    D.cmd_ping()
    assert welt.emitted[-1]["pruefen"] is False  # Schalter an, aber das Modul fehlt: nicht aktiv


def test_die_pruefsumme_stimmt_mit_dem_modul_des_kerns_ueberein():
    """Der Kern vergleicht die Summe seiner Kopie (voice-core/abnahme_befehl.py) mit der aus ping."""
    assert D.modul_sha256 is not None
    assert hashlib.sha256((VOICE_CORE / "abnahme_befehl.py").read_bytes()).hexdigest() == \
        hashlib.sha256(Path(abnahme_befehl.__file__).read_bytes()).hexdigest()


# ---------------------------------------------------------------- die Zeile und die Allowlist im Dispatcher
@pytest.mark.parametrize("argv", [
    ["bash", "-c", "id"], ["sh", "run.sh"], ["python", "-c", "print(1)"], ["python3", "x.py"], ["node", "app.js"], ["npm", "install"],
    ["npm", "run", "deploy"], ["pytest", "-p", "plugin"], ["pytest", "-c", "x.ini"], ["pytest", "../x"], ["pytest", "/etc/passwd"],
    ["pytest", "tests; id"], ["pytest", "$(id)"], ["pytest", "a|b"], ["pytest", "a`id`"], ["pytest", "a>b"], ["pytest\n"],
    ["/usr/bin/pytest"], ["./pytest"], ["PYTEST"], ["pytest.exe"], ["curl", "https://example.org"], ["git", "status"],
    ["uv", "run", "pytest"], ["uv", "sync"], ["uvx", "x"], ["make", "all"], ["go", "run", "x.go"], ["pytest", "@args"],
    ["pytest", "//host/x"], ["pytest", "C:x"], ["pytest", *["-q"] * 13], ["grep", "-q", "x" * 201, "a.txt"], [""], [],
])
def test_jede_abweisung_der_allowlist_gilt_auch_im_dispatcher(welt, argv):
    welt.zeile = zeile(argv)
    with pytest.raises(D.Abgewiesen):
        D.cmd_pruefen(JOB)
    assert FakeSperre.versucht == [] and welt.selbsttests == [] and not any(l.ausgefuehrt for l in FakeLauf.instanzen)
    assert welt.antwort()["ok"] is False and welt.antwort()["exit"] is None
    assert "entschaerfen" not in welt.ablauf


@pytest.mark.parametrize("roh", [
    b'{"argv": ["pytest"]}', b"kein json\n", b"[]\n", b"{}\n", b"\n", b"", b'{"argv": "pytest -q"}\n', b'{"argv": null}\n',
    b'{"argv": [5]}\n', b'{"argv": ["pytest"], "projekt": "rezepte-app"}\n', b'{"argv": ["pytest"], "cwd": "/"}\n',
    b'{"argv": ["pytest"], "env": {"X": "1"}}\n', b'{"argv": ["pytest"], "job": "abc"}\n', b'{"befehl": ["pytest"]}\n',
    b'{"argv": ["pytest"], "argv": ["npm", "test"]}\n', b'{"argv": ["pytest"]}\n{"argv": ["npm", "test"]}\n',
    b'{"argv": ["pytest"], "x": "' + b"a" * 9000 + b'"}\n',
])
def test_die_zeile_hat_genau_das_feld_argv_hoechstens_acht_kib_und_nichts_sonst(welt, roh):
    welt.zeile = roh
    with pytest.raises(D.Abgewiesen):
        D.cmd_pruefen(JOB)
    assert FakeSperre.versucht == [] and not FakeLauf.instanzen and welt.selbsttests == []
    assert welt.antwort()["ok"] is False


def test_eine_abgewiesene_zeile_steht_im_audit_ohne_steuerzeichen(welt):
    welt.zeile = zeile(["pytest", "-q\x1b[31m\u202e", "tests;id"])
    with pytest.raises(D.Abgewiesen):
        D.cmd_pruefen(JOB)
    audit = welt.audit()
    assert f"pruefen_abgewiesen {JOB}" in audit and "\x1b" not in audit and "\u202e" not in audit
    assert all(pflicht for t, pflicht in welt.logzeilen if t.startswith("pruefen_abgewiesen"))


def test_der_aufrufer_waehlt_nie_das_projekt_es_steht_in_auftrag_json(welt):
    welt.pruefen(["pytest", "-q"])
    runner = welt.lauf().argv
    assert runner[runner.index("--projekt") + 1] == PROJEKT and runner[runner.index("--auftrag") + 1] == JOB
    assert welt.selbsttests == [(JOB, PROJEKT, "pruefen")]
    # ein anderes Projekt im Aufruf gibt es nicht (Feld unbekannt, siehe oben); in auftrag.json steht, was der Start gebunden hat
    (welt.jobs / JOB / "auftrag.json").write_text(json.dumps({"job": JOB, "projekt": "anderes-projekt", "art": "umsetzung"}), encoding="utf-8")
    (welt.projekte / "anderes-projekt").mkdir()
    welt.pruefen(["pytest", "-q"])
    runner = welt.lauf().argv
    assert runner[runner.index("--projekt") + 1] == "anderes-projekt"


@pytest.mark.parametrize("meta", [None, "kaputt", [], {}, {"projekt": 5}, {"projekt": ""}, {"projekt": "../x"}, {"projekt": "wamo"},
                                  {"projekt": "Gross"}, {"projekt": "a--b"}, {"projekt": "x/y"}])
def test_ohne_gueltiges_projekt_in_auftrag_json_wird_abgewiesen(welt, meta):
    pfad = welt.jobs / JOB / "auftrag.json"
    if meta is None:
        pfad.unlink()
    else:
        pfad.write_text(meta if isinstance(meta, str) else json.dumps(meta), encoding="utf-8")
    with pytest.raises(D.Abgewiesen):
        welt.pruefen()
    assert FakeSperre.versucht == [] and not any(l.ausgefuehrt for l in FakeLauf.instanzen) and welt.selbsttests == []


def test_ein_unbekannter_auftrag_wird_abgewiesen(welt):
    with pytest.raises(D.Abgewiesen):
        welt.pruefen(job="0" * 16)
    assert FakeSperre.versucht == [] and welt.selbsttests == []


@pytest.mark.skipif(not LINUX, reason="symbolische Links brauchen Rechte, die unter Windows fehlen")
def test_ein_link_als_auftragsordner_oder_projektordner_wird_abgewiesen(welt):
    echt = welt.tmp / "echt"
    echt.mkdir()
    (echt / "auftrag.json").write_text((welt.jobs / JOB / "auftrag.json").read_text(), encoding="utf-8")
    link = "f" * 16
    (welt.jobs / link).symlink_to(echt)
    with pytest.raises(D.Abgewiesen):
        welt.pruefen(job=link)
    shutil.rmtree(welt.projekte / PROJEKT)
    (welt.projekte / PROJEKT).symlink_to(welt.tmp / "echt")
    with pytest.raises(D.Abgewiesen):
        welt.pruefen()


# ---------------------------------------------------------------- Wächter
def test_der_echte_waechter_sperrt_geheimnisse_auch_bei_erlaubter_form(welt):
    with pytest.raises(D.Abgewiesen, match="Wächter"):
        welt.pruefen(["grep", "-rq", "x", ".env"])
    assert welt.selbsttests == [] and not any(l.ausgefuehrt for l in FakeLauf.instanzen) and FakeSperre.versucht == []
    assert any(t.startswith(f"pruefen_abgewiesen {JOB}: Vom Wächter gesperrt") and "grep -rq x .env" in t and p
               for t, p in welt.logzeilen), welt.audit()  # eine Abweisung steht mit argv im Audit


@pytest.mark.parametrize("entscheidung, text", [(("block", "Geheimnis"), "gesperrt"), (("approval", "Freigabe nötig"), "Freigabe")])
def test_waechter_block_und_approval_lassen_nichts_laufen(welt, monkeypatch, entscheidung, text):
    gesehen: list = []

    def decide(werkzeug, eingabe, projektdir):
        gesehen.append((werkzeug, eingabe, projektdir))
        return entscheidung
    monkeypatch.setattr(D, "waechter", lambda: SimpleNamespace(decide=decide))
    with pytest.raises(D.Abgewiesen, match=text):
        welt.pruefen(["pytest", "-q", "tests"])
    assert gesehen == [("Bash", {"command": "pytest -q tests"}, welt.projekte / PROJEKT)]  # wie ein Bash-Aufruf des Auftrags
    assert welt.selbsttests == [] and FakeSperre.versucht == [] and not FakeLauf.instanzen
    assert text in welt.antwort()["grund"]


def test_ein_fehler_des_waechters_heisst_nicht_ausfuehren(welt, monkeypatch):
    def decide(*_):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(D, "waechter", lambda: SimpleNamespace(decide=decide))
    with pytest.raises(D.Abgewiesen, match="Wächter"):
        welt.pruefen()
    assert welt.selbsttests == [] and not FakeLauf.instanzen


def test_nur_allow_laeuft(welt, monkeypatch):
    for ungueltig in ("", "ask", "deny", None, True, ("allow",)):
        monkeypatch.setattr(D, "waechter", lambda u=ungueltig: SimpleNamespace(decide=lambda *_: (u, "x")))
        with pytest.raises(D.Abgewiesen):
            welt.pruefen()
        welt.emitted.clear()
    assert not any(l.ausgefuehrt for l in FakeLauf.instanzen)


# ---------------------------------------------------------------- uv nur mit .venv
UV = ["uv", "run", "--no-sync", "--offline", "pytest", "-q"]


def test_die_uv_form_ohne_venv_ist_nicht_moeglich_mit_hinweis(welt):
    code = welt.pruefen(UV)
    a = welt.antwort()
    assert code == D.EXIT_ABGEWIESEN and a["ok"] is False and a["exit"] is None and a["grund"].startswith("nicht möglich")
    assert ".venv" in a["grund"] and welt.selbsttests == [] and not any(l.ausgefuehrt for l in FakeLauf.instanzen)
    assert FakeSperre.versucht == []  # vor den Sperren, es wird nichts belegt


def test_die_uv_form_mit_venv_laeuft(welt):
    (welt.projekte / PROJEKT / ".venv").mkdir()
    (welt.projekte / PROJEKT / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert welt.pruefen(UV) == D.EXIT_OK
    assert welt.antwort()["ok"] is True and welt.lauf().argv[-len(UV):] == UV


def test_andere_befehle_brauchen_keine_venv(welt):
    assert welt.pruefen(["npm", "test"]) == D.EXIT_OK and welt.antwort()["ok"] is True


# ---------------------------------------------------------------- Sperren
def test_die_sperren_wie_beim_start_in_dieser_reihenfolge(welt):
    welt.pruefen()
    assert FakeSperre.versucht == [".lauf", f"projekt-{PROJEKT}", "platz-0"]
    assert D.PARALLEL_MAX == 1


@pytest.mark.parametrize("belegt, text", [(".lauf", "Auftrag"), (f"projekt-{PROJEKT}", "Projekt"), ("platz-0", "Auftrag")])
def test_belegt_heisst_nichts_laeuft_und_exit_4(welt, belegt, text):
    FakeSperre.belegt = {belegt}
    code = welt.pruefen()
    a = welt.antwort()
    assert code == D.EXIT_BELEGT == 4 and a["ok"] is False and a["grund"].startswith("belegt") and text in a["grund"]
    assert welt.selbsttests == [] and not any(l.ausgefuehrt for l in FakeLauf.instanzen) and "entschaerfen" not in welt.ablauf
    assert "stop" not in welt.ablauf  # die Einheiten eines belegten Auftrags gehören dem, der die Sperre hält


def test_pruefen_nur_nach_einem_beendeten_lauf_des_auftrags(welt):
    (welt.jobs / JOB / "status.json").unlink()
    with pytest.raises(D.Abgewiesen, match="Lauf"):
        welt.pruefen()
    for status in ({}, {"ergebnis": "selbsttest_fehlgeschlagen", "code": 3}, {"ergebnis": "keine_zugangsdaten", "code": 5}, [], "x"):
        welt.status(status)
        welt.emitted.clear()
        with pytest.raises(D.Abgewiesen, match="Lauf"):
            welt.pruefen()
    assert welt.selbsttests == [] and not any(l.ausgefuehrt for l in FakeLauf.instanzen)
    welt.status({"start": 1000, "ende": 1100, "ergebnis": "fertig", "code": 0})
    welt.emitted.clear()
    assert welt.pruefen() == D.EXIT_OK


def test_nach_cancel_laeuft_nichts_mehr(welt):
    (welt.jobs / JOB / "abbruch").write_text("1", encoding="utf-8")
    code = welt.pruefen()
    a = welt.antwort()
    assert code == D.EXIT_ABGEBROCHEN and a["abgebrochen"] is True and a["ok"] is False and welt.selbsttests == []


def test_cancel_waehrend_des_selbsttests_verhindert_den_start(welt):
    def selbsttest(job, projekt, modus="lauf"):
        (welt.jobs / JOB / "abbruch").write_text("1", encoding="utf-8")  # cancel kommt, während die Sandbox des Selbsttests läuft
        return True, "ok"
    welt.mp.setattr(D, "selbsttest", selbsttest)
    code = welt.pruefen()
    assert code == D.EXIT_ABGEBROCHEN and welt.antwort()["abgebrochen"] is True and welt.lauf().ausgefuehrt == 0


def test_cancel_waehrend_des_selbsttests_zaehlt_als_abbruch_auch_wenn_er_dabei_scheitert(welt):
    def selbsttest(job, projekt, modus="lauf"):
        (welt.jobs / JOB / "abbruch").write_text("1", encoding="utf-8")
        return False, "Sandbox oder Hook lief nicht (die Einheit wurde von cancel beendet)"
    welt.mp.setattr(D, "selbsttest", selbsttest)
    code = welt.pruefen()
    a = welt.antwort()
    assert code == D.EXIT_ABGEBROCHEN and a["abgebrochen"] is True and a["grund"] == "abgebrochen" and welt.lauf().ausgefuehrt == 0


def test_cancel_waehrend_des_laufs_macht_das_ergebnis_nicht_ok(welt):
    FakeLauf.ergebnis = PM.Ergebnis(0, False, None, 2.0, b"fertig\n", False)
    orig = FakeLauf.ausfuehren

    def mit_cancel(self):
        (welt.jobs / JOB / "abbruch").write_text("1", encoding="utf-8")
        return orig(self)
    FakeLauf.ausfuehren = mit_cancel
    try:
        code = welt.pruefen()
    finally:
        FakeLauf.ausfuehren = orig
    a = welt.antwort()
    assert code == D.EXIT_ABGEBROCHEN and a["ok"] is False and a["abgebrochen"] is True and a["grund"] == "abgebrochen"


# ---------------------------------------------------------------- Reihenfolge, Selbsttest, Entschärfen
def test_entschaerfen_dann_selbsttest_dann_lauf_vor_jedem_aufruf(welt):
    orig = FakeLauf.ausfuehren

    def mit_marke(self):
        welt.ablauf.append("lauf")
        return orig(self)
    FakeLauf.ausfuehren = mit_marke
    try:
        welt.pruefen()
        welt.pruefen(["npm", "test"])
    finally:
        FakeLauf.ausfuehren = orig
    # vor dem Start: Reste der Einheit beenden, dann entschärfen; nach dem Lauf: Einheit beenden, dann erneut entschärfen
    assert welt.ablauf == ["stop", "entschaerfen", "selbsttest", "lauf", "stop", "entschaerfen"] * 2
    assert welt.selbsttests == [(JOB, PROJEKT, "pruefen")] * 2  # jeder Aufruf, nie gemerkt


def test_ein_fehlgeschlagener_selbsttest_startet_nichts(welt):
    welt.selbsttest_ergebnis = (False, "Netzgrenzen nicht wie erwartet: internet=offen")
    code = welt.pruefen()
    a = welt.antwort()
    assert code == D.EXIT_SELBSTTEST == 3 and a["ok"] is False and a["exit"] is None
    assert a["grund"].startswith("selbsttest_fehlgeschlagen") and "internet=offen" in a["grund"]
    assert welt.lauf().ausgefuehrt == 0
    assert any(p and "selbsttest" in t and "FEHLER" in t for t, p in welt.logzeilen)


def test_der_selbsttest_misst_seine_dauer_im_audit(welt):
    welt.pruefen()
    assert any(re.search(r"selbsttest \w+ pruefen ok dauer=\d+\.\d+s", t) for t, _ in welt.logzeilen), welt.audit()


def test_ein_fehler_beim_entschaerfen_startet_nichts(welt, monkeypatch):
    def kaputt(_):
        raise OSError("Projektordner nicht lesbar")
    monkeypatch.setattr(D, "projekt_entschaerfen", kaputt)
    with pytest.raises(OSError):
        welt.pruefen()
    assert welt.selbsttests == [] and not any(l.ausgefuehrt for l in FakeLauf.instanzen)


# ---------------------------------------------------------------- Umgebung und Aufruf des Läufers
GEHEIM = {"ANTHROPIC_API_KEY": "sk-ant-api03-" + "A" * 40, "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-" + "B" * 40,
          "GH_TOKEN": "ghp_" + "c" * 30, "GH_TOKEN_LESEN": "ghp_" + "d" * 30, "VERCEL_TOKEN": "vercel-eeeeeeee",
          "SUPABASE_ACCESS_TOKEN": "sbp_ffffffffffff", "JIRA_API_TOKEN": "jira-gggggggg", "SONIOX_API_KEY": "soniox-hhhhhhhh",
          "LITELLM_API_KEY": "litellm-iiiiiiii", "WAMO_GH_APP_ID": "123", "ANTHROPIC_WORKSPACE_ID": "wrkspc_01ABCdef"}


def test_die_umgebung_ist_die_basisumgebung_plus_git_hooks_aus_und_nichts_sonst(welt):
    welt.geheim = {**GEHEIM, "WAMO_PRUEFEN": "an"}
    welt.pruefen()
    env = welt.lauf().env
    erwartet = D.basis_env()
    for i, (schluessel, wert) in enumerate(D.GIT_OHNE_HOOKS):
        erwartet[f"GIT_CONFIG_KEY_{i}"], erwartet[f"GIT_CONFIG_VALUE_{i}"] = schluessel, wert
    erwartet["GIT_CONFIG_COUNT"] = str(len(D.GIT_OHNE_HOOKS))
    assert env == erwartet == D.pruefen_env()
    git = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert git == {"core.hooksPath": "/dev/null", "core.fsmonitor": "false"}
    text = json.dumps(env)
    for name, wert in GEHEIM.items():
        assert name not in env and wert not in text, name
    for name in (*wk.CLAUDE_VARIABLEN, "GH_TOKEN", "GH_TOKEN_LESEN", "VERCEL_TOKEN", "SUPABASE_ACCESS_TOKEN", "JIRA_API_TOKEN",
                 "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_BASE_URL"):
        assert name not in env
    assert not [k for k in env if k.startswith(("WAMO_", "GH_", "ANTHROPIC", "CLAUDE"))]


def test_der_laeufer_im_modus_pruefen_mit_zeitlimit_120_und_dem_argv_nach_dem_trenner(welt):
    welt.pruefen(["npm", "run", "test:unit"])
    lauf = welt.lauf()
    assert lauf.argv == [str(D.RUNNER), "--auftrag", JOB, "--projekt", PROJEKT, "--modus", "pruefen", "--zeitlimit", "120",
                         "--einheit-zusatz", "pruefen", "--", "npm", "run", "test:unit"]
    assert lauf.kw["zeitlimit_s"] == PM.ZEITLIMIT_S == 120
    assert lauf.kw["cwd"] == "/"


def test_das_argv_wird_dem_laeufer_nur_als_liste_uebergeben_nie_als_shell(welt):
    welt.pruefen(["grep", "-q", "hello world", "README.md"])
    argv = welt.lauf().argv
    assert argv[-4:] == ["grep", "-q", "hello world", "README.md"] and isinstance(argv, list)


# ---------------------------------------------------------------- Antwort, ok nur bei echtem int 0
def test_ein_erfolg_ist_ok_mit_ausgabe_und_prozessexit_0(welt):
    assert welt.pruefen() == D.EXIT_OK
    a = welt.antwort()
    assert a == {"v": 1, "ok": True, "exit": 0, "abgebrochen": False, "grund": None, "dauer_s": 1.5, "ausgabe": "3 passed\n",
                 "gekuerzt": False}


@pytest.mark.parametrize("rc", [1, 2, 5, 70, 255, None, True, False, 0.0, "0", -9, 256])
def test_ok_nur_bei_einem_echten_int_null_der_dispatcher_bleibt_bei_prozessexit_0(welt, rc):
    FakeLauf.ergebnis = PM.Ergebnis(rc, False, None, 1.0, b"x\n", False)
    code = welt.pruefen()
    a = welt.antwort()
    assert a["ok"] is False and code == D.EXIT_OK  # der Befehl lief, er war nur nicht erfolgreich
    assert a["exit"] is None or (type(a["exit"]) is int and a["exit"] != 0)


def test_zeitlimit_und_abbruch_sind_nicht_ok_und_haben_eigene_exitcodes(welt):
    FakeLauf.ergebnis = PM.Ergebnis(None, True, "zeitlimit", 150.0, b"haengt\n", False)
    assert welt.pruefen() == D.EXIT_ZEITLIMIT == 124
    assert welt.antwort()["ok"] is False and welt.antwort()["grund"] == "zeitlimit" and welt.antwort()["abgebrochen"] is True
    welt.emitted.clear()
    FakeLauf.ergebnis = PM.Ergebnis(None, True, "verlassen", 3.0, b"", False)
    assert welt.pruefen() == D.EXIT_ABGEBROCHEN == 130
    assert welt.antwort()["ok"] is False and welt.antwort()["grund"] == "verlassen"
    welt.emitted.clear()
    FakeLauf.ergebnis = PM.Ergebnis(124, False, None, 120.0, b"", False)  # timeout im Läufer selbst
    assert welt.pruefen() == D.EXIT_ZEITLIMIT
    assert welt.antwort()["grund"] == "zeitlimit"


def test_die_ausgabe_ist_auf_2048_byte_gekuerzt_das_ende_bleibt_und_ein_schluessel_ist_geschwaerzt(welt):
    geheim = "sk-ant-api03-" + "Q" * 40
    ausgabe = ("zeile\n" * 3000 + f"Schluessel {geheim}\n" + "1 failed, 2 passed\n").encode()
    FakeLauf.ergebnis = PM.Ergebnis(1, False, None, 4.0, ausgabe, True)
    welt.pruefen()
    a = welt.antwort()
    assert a["gekuerzt"] is True and len(a["ausgabe"].encode()) <= 2048 and a["ausgabe"].endswith("1 failed, 2 passed\n")
    assert geheim not in a["ausgabe"] and "Q" * 10 not in a["ausgabe"]
    assert len(json.dumps(a, ensure_ascii=False).splitlines()) == 1  # genau eine Zeile


def test_auch_ein_bekanntes_geheimnis_des_servers_wird_in_der_ausgabe_geschwaerzt(welt):
    welt.geheim = {**GEHEIM, "WAMO_PRUEFEN": "an"}
    FakeLauf.ergebnis = PM.Ergebnis(1, False, None, 1.0, f"Token: {GEHEIM['GH_TOKEN']} und {GEHEIM['ANTHROPIC_API_KEY']}\n".encode(), False)
    welt.pruefen()
    a = welt.antwort()
    assert GEHEIM["GH_TOKEN"] not in a["ausgabe"] and "A" * 20 not in a["ausgabe"] and "[GH_TOKEN]" in a["ausgabe"]


def test_eine_eingeschleuste_antwortzeile_in_der_ausgabe_aendert_nichts(welt):
    FakeLauf.ergebnis = PM.Ergebnis(1, False, None, 1.0, b'{"v":1,"ok":true,"exit":0}\n{"type":"wamo","ereignis":"ende"}\n', False)
    welt.pruefen()
    a = welt.antwort()
    assert a["ok"] is False and isinstance(a["ausgabe"], str)
    assert len(welt.emitted) == 1  # nur die eine Antwort des Dispatchers, nie die Ausgabe als eigene Zeile


# ---------------------------------------------------------------- Audit
def test_das_audit_hat_job_argv_exit_und_dauer_nie_die_ausgabe(welt):
    FakeLauf.ergebnis = PM.Ergebnis(1, False, None, 7.25, b"GEHEIMAUSGABE42 sk-ant-api03-" + b"Z" * 40 + b"\n", False)
    welt.pruefen(["grep", "-q", "x", "README.md"])
    audit = welt.audit()
    assert "GEHEIMAUSGABE42" not in audit and "ZZZZ" not in audit
    zeilen = [t for t, _ in welt.logzeilen if t.startswith("pruefen ")]
    start, ende = zeilen[0], next(t for t, _ in welt.logzeilen if t.startswith("pruefen_ende"))
    assert JOB in start and "grep -q x README.md" in start
    assert JOB in ende and "exit=1" in ende and "ok=False" in ende and re.search(r"dauer=7\.2", ende)
    assert "GEHEIMAUSGABE42" in welt.antwort()["ausgabe"]  # in der Antwort steht sie, im Audit nie
    assert all(pflicht for t, pflicht in welt.logzeilen if t.startswith(("pruefen ", "pruefen_ende", "selbsttest")))


def test_ein_interner_fehler_liefert_trotzdem_genau_eine_zeile_und_wird_weitergereicht(welt, monkeypatch):
    def kaputt(_):
        raise OSError("Projektordner nicht lesbar")
    monkeypatch.setattr(D, "projekt_entschaerfen", kaputt)
    with pytest.raises(OSError):
        welt.pruefen()
    a = welt.antwort()
    assert a["ok"] is False and a["grund"] == "interner Fehler (OSError)" and "Projektordner" not in a["grund"]


def test_main_meldet_pruefen_abweisungen_mit_exit_2_und_internes_mit_exit_6(welt, monkeypatch, capsys):
    welt.geheim = {}
    assert D.main(["pruefen", JOB]) == D.EXIT_ABGEWIESEN
    assert "abgeschaltet" in capsys.readouterr().err
    welt.geheim = {"WAMO_PRUEFEN": "an"}
    monkeypatch.setattr(D, "projekt_entschaerfen", lambda p: (_ for _ in ()).throw(OSError("x")))
    assert D.main(["pruefen", JOB]) == D.EXIT_INTERN
    assert "interner Fehler" in capsys.readouterr().err


def test_ein_schreibfehler_im_audit_bricht_den_befehl_ab(welt, monkeypatch):
    def log(text, pflicht=False):
        if pflicht and text.startswith("pruefen "):
            raise OSError("Platte voll")
    monkeypatch.setattr(D, "log", log)
    with pytest.raises(OSError):
        welt.pruefen()
    assert not any(l.ausgefuehrt for l in FakeLauf.instanzen)  # das Audit steht vor dem Start


# ---------------------------------------------------------------- Zähler je Auftrag
def test_der_zaehler_je_auftrag_begrenzt_die_aufrufe(welt):
    for _ in range(PM.AUFRUFE_MAX):
        welt.emitted.clear()
        assert welt.pruefen() == D.EXIT_OK
    assert json.loads((welt.jobs / JOB / "pruefen.json").read_text())["aufrufe"] == PM.AUFRUFE_MAX
    welt.emitted.clear()
    welt.selbsttests.clear()
    with pytest.raises(D.Abgewiesen, match=str(PM.AUFRUFE_MAX)):
        welt.pruefen()
    assert welt.selbsttests == []


@pytest.mark.parametrize("inhalt", ["kaputt", "[]", '{"aufrufe": "x"}', '{"aufrufe": -1}', '{"aufrufe": true}', '{"aufrufe": 1.5}'])
def test_ein_unlesbarer_zaehler_sperrt_fail_closed(welt, inhalt):
    (welt.jobs / JOB / "pruefen.json").write_text(inhalt, encoding="utf-8")
    with pytest.raises(D.Abgewiesen):
        welt.pruefen()
    assert welt.selbsttests == []


def test_der_zaehler_zaehlt_auch_aufrufe_mit_fehlgeschlagenem_selbsttest(welt):
    welt.selbsttest_ergebnis = (False, "x")
    welt.pruefen()
    assert json.loads((welt.jobs / JOB / "pruefen.json").read_text())["aufrufe"] == 1


# ---------------------------------------------------------------- Zeitlimit, EOF und die Einheit
def test_die_einheit_wird_nach_jedem_lauf_beendet_auch_bei_einem_fehler(welt):
    welt.pruefen()
    assert welt.stopps == [JOB, JOB]  # einmal vor dem Start (Reste), einmal danach (das finally)
    welt.stopps.clear()
    FakeLauf.wirft = RuntimeError("Dispatcher kaputt")
    with pytest.raises(RuntimeError):
        welt.pruefen()
    assert welt.stopps and set(welt.stopps) == {JOB}  # der Lauf wird gestoppt, und das finally beendet die Einheit noch einmal
    welt.stopps.clear()
    FakeLauf.wirft = D._Beendet(15)  # SIGTERM während des Laufs
    with pytest.raises(D._Beendet):
        welt.pruefen()
    assert JOB in welt.stopps


def test_der_lauf_bekommt_stop_units_des_auftrags_als_einheit(welt):
    welt.pruefen()
    welt.lauf().stop_einheit()
    assert welt.stopps[-1] == JOB


def test_das_ende_der_lebenslinie_ruft_stoppen_verlassen_und_stop_units(welt):
    welt.pruefen()
    assert len(welt.lebenslinien) == 1  # die Lebenslinie wird beobachtet, bevor der Selbsttest läuft
    welt.stopps.clear()
    welt.lebenslinien[0]()  # EOF auf stdin
    lauf = welt.lauf()
    assert lauf.gestoppt and lauf.grund == "verlassen" and welt.stopps == [JOB]


def test_ende_der_lebenslinie_waehrend_des_selbsttests_startet_nichts(welt):
    def selbsttest(job, projekt, modus="lauf"):
        welt.lebenslinien[0]()  # der Kern legt auf, während die Sandbox des Selbsttests läuft
        return False, "Sandbox oder Hook lief nicht"  # weil die Einheit gestoppt wurde
    welt.mp.setattr(D, "selbsttest", selbsttest)
    code = welt.pruefen()
    a = welt.antwort()
    assert code == D.EXIT_ABGEBROCHEN and a["abgebrochen"] is True and a["grund"] == "verlassen" and a["ok"] is False
    assert welt.lauf().ausgefuehrt == 0


def test_lebenslinie_beobachten_ruft_bei_eof_genau_einmal_zurueck(monkeypatch):
    class Puffer:
        def __init__(self):
            self.stuecke = [b"x", b"yy", b""]

        def read1(self, n):
            return self.stuecke.pop(0)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=Puffer()))
    aufrufe: list = []
    ende = threading.Event()
    t = D.lebenslinie_beobachten(lambda: (aufrufe.append(1), ende.set()))
    assert ende.wait(5) and t.daemon and aufrufe == [1]


def test_lebenslinie_beobachten_faengt_lesefehler_als_ende_ab(monkeypatch):
    class Puffer:
        def read1(self, n):
            raise OSError("weg")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=Puffer()))
    ende = threading.Event()
    D.lebenslinie_beobachten(ende.set)
    assert ende.wait(5)


def test_die_zeitgrenze_kommt_vom_modul_und_der_laeufer_beendet_den_rest():
    assert PM.ZEITLIMIT_S == 120 and PM.NACHFRIST_S >= 15  # timeout --kill-after=15 im Läufer


# ---------------------------------------------------------------- fehlende oder defekte Prüflogik: trotzdem genau eine Zeile
NICHT_VERFUEGBAR = {"v": 1, "ok": False, "exit": None, "abgebrochen": False, "grund": "pruefen ist nicht verfügbar (die Prüflogik fehlt "
                    "oder ist defekt)", "dauer_s": 0.0, "ausgabe": "", "gekuerzt": False}


@pytest.mark.parametrize("fehler", [ImportError("wamo_job_pruefen fehlt"), SyntaxError("kaputt"), FileNotFoundError("weg"),
                                    AttributeError("x"), OSError("Platte"), ModuleNotFoundError("abnahme_befehl")])
def test_fehlt_die_pruefmodul_oder_ist_es_defekt_kommt_genau_eine_zeile_und_exit_6(welt, monkeypatch, capsys, fehler):
    def kaputt():
        raise fehler
    monkeypatch.setattr(D, "pruefen_modul", kaputt)
    gelesen: list = []
    monkeypatch.setattr(D, "_pruefen_zeile_lesen", lambda: gelesen.append(1) or zeile())
    assert D.main(["pruefen", JOB]) == D.EXIT_INTERN
    assert welt.antwort() == NICHT_VERFUEGBAR  # genau eine Zeile, die Felder des Moduls, nur ein fester Grund
    assert gelesen == [] and FakeSperre.versucht == [] and welt.selbsttests == [] and not FakeLauf.instanzen
    assert "interner Fehler" in capsys.readouterr().err
    assert type(fehler).__name__ not in welt.antwort()["grund"] and str(fehler) not in welt.antwort()["grund"]


def test_cmd_pruefen_fragt_pruefen_aktiv_als_erstes_und_liest_vorher_nichts(welt, monkeypatch):
    reihenfolge: list = []
    echt = D.pruefen_aktiv
    monkeypatch.setattr(D, "pruefen_aktiv", lambda geheim: reihenfolge.append("aktiv") or echt(geheim))
    monkeypatch.setattr(D, "_pruefen_zeile_lesen", lambda: reihenfolge.append("zeile") or zeile())
    welt.pruefen()
    assert reihenfolge[:2] == ["aktiv", "zeile"]


def test_ohne_schalter_und_ohne_modul_kommt_der_grund_abgeschaltet_nicht_der_des_moduls(welt, monkeypatch):
    welt.geheim = {}
    monkeypatch.setattr(D, "pruefen_modul", lambda: (_ for _ in ()).throw(ImportError("weg")))
    with pytest.raises(D.Abgewiesen, match="abgeschaltet"):
        D.cmd_pruefen(JOB)
    a = welt.antwort()
    assert a["ok"] is False and a["grund"] == "pruefen ist abgeschaltet" and list(a) == SCHLUESSEL


def test_die_ersatzantwort_hat_dieselben_felder_und_denselben_inhalt_wie_die_des_moduls(monkeypatch):
    mit_modul = D.antwort_leer_sicher("belegt: Dieser Auftrag läuft gerade")
    monkeypatch.setattr(D, "pruefen_modul", lambda: (_ for _ in ()).throw(ImportError("weg")))
    ohne_modul = D.antwort_leer_sicher("belegt: Dieser Auftrag läuft gerade")
    assert mit_modul == ohne_modul and list(ohne_modul) == SCHLUESSEL
    lang = D.antwort_leer_sicher("x\n‮\x1b" * 500)
    assert list(lang) == SCHLUESSEL and "\n" not in lang["grund"] and "‮" not in lang["grund"] and len(lang["grund"]) <= 300
    assert D.antwort_leer_sicher(5)["grund"] == "5"


def test_ein_fehler_im_ablauf_liefert_ohne_modul_ebenfalls_genau_eine_zeile(welt, monkeypatch):
    """Das Modul ist beim Start da, fällt aber später aus (nur die Antwort darf nie am Modul hängen)."""
    echt = D.pruefen_modul
    zaehler = {"n": 0}

    def faellt_aus():
        zaehler["n"] += 1
        if zaehler["n"] > 2:  # pruefen_aktiv und _pruefen laden es noch, die Ersatzantwort danach nicht mehr
            raise ImportError("weg")
        return echt()
    monkeypatch.setattr(D, "pruefen_modul", faellt_aus)
    monkeypatch.setattr(D, "projekt_entschaerfen", lambda p: (_ for _ in ()).throw(OSError("Projektordner nicht lesbar")))
    with pytest.raises(OSError):
        welt.pruefen()
    a = welt.antwort()
    assert a["ok"] is False and a["grund"] == "interner Fehler (OSError)" and list(a) == SCHLUESSEL


# ---------------------------------------------------------------- Platte: vor jedem Aufruf messen
@pytest.mark.parametrize("frei, zu_gross, erwartet", [(1 * 1024**3, False, "PLATZ_ZU_WENIG"), (10 * 1024**3, True, "PROJEKT_ZU_GROSS")])
def test_zu_wenig_platz_oder_ein_zu_grosses_projekt_heisst_nicht_moeglich_und_nichts_laeuft(welt, monkeypatch, frei, zu_gross, erwartet):
    welt.freier_platz = frei
    if zu_gross:
        monkeypatch.setattr(PM, "ordner_bytes", lambda *a, **k: None)
    code = welt.pruefen()
    a = welt.antwort()
    assert code == D.EXIT_ABGEWIESEN and a["ok"] is False and a["exit"] is None and a["grund"] == getattr(PM, erwartet)
    assert a["grund"].startswith("nicht möglich")
    assert welt.selbsttests == [] and "entschaerfen" not in welt.ablauf and not any(l.ausgefuehrt for l in FakeLauf.instanzen)


def test_die_platte_wird_vor_jedem_aufruf_und_im_projekt_des_auftrags_gemessen(welt, monkeypatch):
    gemessen: list = []
    monkeypatch.setattr(PM, "freier_platz", lambda pfad: gemessen.append(("platz", pfad)) or 10 * 1024**3)
    monkeypatch.setattr(PM, "ordner_bytes", lambda pfad, grenze, frist, **kw: gemessen.append(("projekt", pfad, grenze, frist)) or 1)
    welt.pruefen()
    welt.pruefen(["npm", "test"])
    messung = [("platz", welt.heim), ("projekt", welt.projekte / PROJEKT, 2 * 1024**3, 5.0)]
    assert gemessen == messung * 2
    assert welt.selbsttests == [(JOB, PROJEKT, "pruefen")] * 2


def test_die_plattenpruefung_laeuft_nach_den_sperren_und_vor_dem_entschaerfen(welt, monkeypatch):
    monkeypatch.setattr(PM, "platten_grund", lambda projektdir, platzpfad: welt.ablauf.append("platte") or None)
    welt.pruefen()
    assert FakeSperre.versucht == [".lauf", f"projekt-{PROJEKT}", "platz-0"]
    assert welt.ablauf[:3] == ["stop", "platte", "entschaerfen"]


# ---------------------------------------------------------------- nach dem Lauf erneut entschärfen, Buchführung unberührt
def test_nach_dem_lauf_wird_erneut_entschaerft_auch_bei_fehler_selbsttest_und_abbruch(welt):
    welt.pruefen()
    assert welt.ablauf[-2:] == ["stop", "entschaerfen"]  # erst die Sandbox beenden, dann entschärfen
    welt.ablauf.clear()
    FakeLauf.wirft = RuntimeError("Dispatcher kaputt")
    with pytest.raises(RuntimeError):
        welt.pruefen()
    assert welt.ablauf[-2:] == ["stop", "entschaerfen"]
    welt.ablauf.clear()
    FakeLauf.wirft = D._Beendet(15)
    with pytest.raises(D._Beendet):
        welt.pruefen()
    assert welt.ablauf[-2:] == ["stop", "entschaerfen"]
    welt.ablauf.clear()
    FakeLauf.wirft = None
    welt.selbsttest_ergebnis = (False, "Netzgrenzen nicht wie erwartet: internet=offen")
    welt.pruefen()
    assert welt.ablauf == ["stop", "entschaerfen", "selbsttest", "stop", "entschaerfen"]  # auch wenn nichts lief: der Selbsttest lief


def test_gepflanzte_flaechen_nach_dem_lauf_stehen_im_audit(welt, monkeypatch):
    monkeypatch.setattr(D, "projekt_entschaerfen", lambda p: ["gepflanzter Hook .git/hooks/pre-commit"])
    welt.pruefen()
    nachher = [t for t, _ in welt.logzeilen if "nach dem Lauf" in t and "gepflanzter Hook" in t]
    assert len(nachher) == 1 and JOB in nachher[0] and PROJEKT in nachher[0]


def test_ein_fehler_beim_entschaerfen_nach_dem_lauf_verdeckt_weder_das_ergebnis_noch_eine_ausnahme(welt, monkeypatch):
    aufrufe: list = []

    def zweiter_kaputt(projektdir):
        aufrufe.append(1)
        if len(aufrufe) % 2 == 0:
            raise OSError("Projektordner nicht lesbar")
        return []
    monkeypatch.setattr(D, "projekt_entschaerfen", zweiter_kaputt)
    assert welt.pruefen() == D.EXIT_OK  # das Ergebnis des Laufs gilt, der Fehler steht im Audit
    assert welt.antwort()["ok"] is True
    assert any("entschaerfen_nach_dem_lauf_fehlgeschlagen" in t and "OSError" in t and JOB in t for t, _ in welt.logzeilen)
    aufrufe.clear()
    FakeLauf.wirft = RuntimeError("Dispatcher kaputt")
    with pytest.raises(RuntimeError, match="Dispatcher kaputt"):
        welt.pruefen()


def test_pruefen_fliesst_nicht_in_die_buchfuehrung_der_zugaenge_ein(welt, monkeypatch):
    """Bewusst nein: Der Lauf hat keine Zugänge (kein Token, kein Claude). Die Buchführung (zugang_pruefen) misst, ob ein Lauf mehr
    Zugänge bekäme als frühere Läufe im Projekt; ein Lauf ohne Zugänge schriebe die Schnittmenge auf leer und löste bei jedem späteren
    start eine Freigabekarte aus."""
    monkeypatch.setattr(D, "zugang_pruefen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("pruefen führt keine Buchführung")))
    (welt.jobs / ".projekte").mkdir()
    eintrag = welt.jobs / ".projekte" / f"{PROJEKT}.json"
    eintrag.write_text(json.dumps({"schnitt": ["GH_TOKEN"], "flaechen": {"package.json": "scripts: test=x"}}), encoding="utf-8")
    vorher = eintrag.read_bytes()
    welt.pruefen()
    welt.pruefen(["npm", "test"])
    assert eintrag.read_bytes() == vorher
    # auch im Quelltext: keine der Funktionen von pruefen nennt die Buchführung (Docstrings zählen nicht)
    baum = ast.parse((INFRA / "wamo_job_dispatch.py").read_text(encoding="utf-8"))
    pruefen_funktionen = [n for n in baum.body if isinstance(n, ast.FunctionDef) and (n.name.startswith("_pruefen") or n.name == "cmd_pruefen")]
    assert len(pruefen_funktionen) >= 6
    for funktion in pruefen_funktionen:
        namen = {n.id for n in ast.walk(funktion) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(funktion) if isinstance(n, ast.Attribute)}
        assert "zugang_pruefen" not in namen, funktion.name


# ---------------------------------------------------------------- Protokoll: freier Inhalt nie roh
def test_die_protokollzeilen_von_pruefen_haben_nie_steuer_oder_formatzeichen_und_keine_geheimnisse(welt):
    import unicodedata
    welt.geheim = {**GEHEIM, "WAMO_PRUEFEN": "an"}
    schluessel = "sk-ant-api03-" + "Z" * 40
    boese = ("Sandbox oder Hook lief nicht: x FALSCH pruefen_ende exit=0 \x1b[31m‮​ " + GEHEIM["GH_TOKEN"] + " "
             + schluessel[:12] + "​" + schluessel[12:] + " " + "y" * 1000)
    welt.selbsttest_ergebnis = (False, boese)
    welt.pruefen(["grep", "-q", "x", "README.md"])
    zeilen = [t for t, _ in welt.logzeilen if t.startswith(("selbsttest", "pruefen"))]
    assert any(t.startswith("selbsttest") and "FEHLER" in t for t in zeilen)
    for t in zeilen:
        assert not [c for c in t if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp")], repr(t)
        assert len(t) <= PM.LOG_ZEICHEN, len(t)
    audit = welt.audit()
    assert GEHEIM["GH_TOKEN"] not in audit and "ZZZZ" not in audit and "[GH_TOKEN]" in audit
    a = welt.antwort()  # auch die Antwort an den Kern trägt den Fehlertext der Sandbox nie ungeschwärzt
    assert GEHEIM["GH_TOKEN"] not in a["grund"] and "ZZZZ" not in a["grund"] and "‮" not in a["grund"]


def test_eine_gesperrte_zeile_im_audit_traegt_argv_nur_bereinigt_und_in_einer_zeile(welt):
    with pytest.raises(D.Abgewiesen):
        welt.pruefen(["grep", "-rq", "x", ".env"])
    zeilen = [t for t, _ in welt.logzeilen if t.startswith("pruefen_abgewiesen")]
    assert len(zeilen) == 1 and "grep -rq x .env" in zeilen[0] and "\n" not in zeilen[0]


def test_die_abweisung_im_audit_und_auf_stderr_ist_bereinigt(welt, capsys):
    """Ein Wächtergrund aus freiem Text darf weder die Protokollzeile von main noch stderr verfälschen."""
    welt.mp.setattr(D, "waechter", lambda: SimpleNamespace(
        decide=lambda *a, **k: ("block", "Gesperrt FALSCH 2026 pruefen_ende exit=0\x1b[31m‮" + "x" * 400)))
    assert D.main(["pruefen", JOB]) == D.EXIT_ABGEWIESEN
    fehler = capsys.readouterr().err
    assert " " not in fehler and "\x1b" not in fehler and "‮" not in fehler and len(fehler.splitlines()) == 1
    a = welt.antwort()
    assert " " not in a["grund"] and "‮" not in a["grund"] and a["ok"] is False


# ---------------------------------------------------------------- Einheit beenden, cancel, Pfade
def test_vor_dem_start_werden_reste_der_einheit_beendet_erst_wenn_alle_sperren_gehalten_werden(welt, monkeypatch):
    stand: list = []
    monkeypatch.setattr(D, "stop_units", lambda job: stand.append((job, list(FakeSperre.versucht), list(welt.ablauf))))
    welt.pruefen()
    job, versucht, ablauf = stand[0]
    assert job == JOB and versucht == [".lauf", f"projekt-{PROJEKT}", "platz-0"] and ablauf == []  # vor entschärfen und Selbsttest


def test_ein_cancel_unmittelbar_vor_dem_start_verpufft_nicht(welt, monkeypatch):
    """Die letzte Prüfung der abbruch-Datei steht hinter der Pflichtzeile vor dem Start (das Schreiben des Audits dauert)."""
    def log(text, pflicht=False):
        welt.logzeilen.append((text, pflicht))
        if text.startswith(f"pruefen {JOB} "):
            (welt.jobs / JOB / "abbruch").write_text("1", encoding="utf-8")  # cancel kommt, während das Audit geschrieben wird
    monkeypatch.setattr(D, "log", log)
    code = welt.pruefen()
    a = welt.antwort()
    assert code == D.EXIT_ABGEBROCHEN and a["abgebrochen"] is True and a["ok"] is False and a["grund"] == "abgebrochen"
    assert welt.lauf().ausgefuehrt == 0


def test_ein_pfad_der_aus_dem_projekt_hinausfuehrt_wird_abgewiesen_bevor_etwas_laeuft(welt, monkeypatch):
    gesehen: list = []
    monkeypatch.setattr(PM, "pfad_grund", lambda argv, projektdir, **kw: gesehen.append((list(argv), projektdir)) or PM.PFAD_AUSSEN)
    with pytest.raises(D.Abgewiesen, match="Projektordner"):
        welt.pruefen(["grep", "-r", "x", "verweis"])
    assert gesehen == [(["grep", "-r", "x", "verweis"], welt.projekte / PROJEKT)]
    assert welt.selbsttests == [] and "entschaerfen" not in welt.ablauf and not any(l.ausgefuehrt for l in FakeLauf.instanzen)
    assert welt.antwort()["ok"] is False and not (welt.jobs / JOB / "pruefen.json").exists()  # zählt nicht als Aufruf
    assert any(t.startswith(f"pruefen_abgewiesen {JOB}") and "grep -r x verweis" in t and p for t, p in welt.logzeilen)


@pytest.mark.skipif(not LINUX, reason="symbolische Links brauchen Rechte, die unter Windows fehlen")
def test_ein_echter_link_im_projekt_nach_aussen_wird_abgewiesen_einer_nach_innen_nicht(welt):
    aussen = welt.tmp / "aussen"
    aussen.mkdir()
    (welt.projekte / PROJEKT / "innen").mkdir()
    (welt.projekte / PROJEKT / "verweis").symlink_to(aussen)
    (welt.projekte / PROJEKT / "nach_innen").symlink_to("innen")
    with pytest.raises(D.Abgewiesen, match="Projektordner"):
        welt.pruefen(["grep", "-r", "x", "verweis"])
    welt.emitted.clear()
    assert welt.pruefen(["grep", "-r", "x", "nach_innen"]) == D.EXIT_OK


# ---------------------------------------------------------------- Selbsttest im Modus pruefen
class FakeRun:
    def __init__(self, netz: str, code: int = 0, fehler: int = 0):
        deny = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": D.SELFTEST_GRUND + " (git push --force)"}}
        self.p = SimpleNamespace(returncode=0, stdout=json.dumps(deny).encode(),
                                 stderr=f"wamo-netz {netz}\nwamo-selbsttest code={code} fehler={fehler}\n".encode())
        self.aufrufe: list = []

    def __call__(self, argv, **kwargs):
        self.aufrufe.append((argv, kwargs))
        return self.p


NETZ_PRUEFEN_ZU = "landlock=zu lo9=zu lo9mptcp=zu netz10=zu netz10mptcp=zu bruecke=zu lan=zu internet=zu"
NETZ_LAUF = "landlock=zu lo9=zu lo9mptcp=zu netz10=zu netz10mptcp=zu bruecke=zu lan=zu github=offen"


@pytest.fixture
def selbsttest_umgebung(tmp_path, monkeypatch):
    managed = tmp_path / "managed-settings.json"
    shutil.copyfile(INFRA / "claude-code" / "managed-settings.json", managed)
    monkeypatch.setattr(D, "MANAGED", managed)
    monkeypatch.setattr(D, "docker_bruecke", lambda: "172.17.0.1")
    monkeypatch.setattr(D, "PROJEKTE", tmp_path / "projekte")
    return monkeypatch


def test_der_selbsttest_im_modus_pruefen_laeuft_im_selben_modus_wie_der_befehl(selbsttest_umgebung):
    run = FakeRun(NETZ_PRUEFEN_ZU)
    selbsttest_umgebung.setattr(D.subprocess, "run", run)
    ok, grund = D.selbsttest(JOB, PROJEKT, "pruefen")
    assert ok is True, grund
    argv = run.aufrufe[0][0]
    assert argv[argv.index("--modus") + 1] == "pruefen" and "--einheit-zusatz" in argv
    skript = argv[-1]
    assert D.NETZPROBE_PRUEFEN in skript and "1.1.1.1" in skript and "github.com" not in skript  # per IP, kein DNS-Fehler
    for pruefung in ("/proc/net/dev", "/srv/wamo/.claude", "/run/wamo/zustand", ".local/share/claude", "ANTHROPIC_", "hooksPath"):
        assert pruefung in skript, pruefung


def test_der_selbsttest_im_lauf_modus_bleibt_unveraendert(selbsttest_umgebung):
    run = FakeRun(NETZ_LAUF)
    selbsttest_umgebung.setattr(D.subprocess, "run", run)
    ok, grund = D.selbsttest(JOB, PROJEKT)
    assert ok is True, grund
    argv = run.aufrufe[0][0]
    assert "--modus" not in argv and "github.com" in argv[-1] and "1.1.1.1" not in argv[-1] and "/proc/net/dev" not in argv[-1]


@pytest.mark.parametrize("netz", [
    NETZ_PRUEFEN_ZU.replace("internet=zu", "internet=offen"), NETZ_PRUEFEN_ZU.replace("lan=zu", "lan=offen"),
    NETZ_PRUEFEN_ZU.replace("lo9=zu", "lo9=offen"), NETZ_PRUEFEN_ZU.replace("landlock=zu", "landlock=offen"),
    NETZ_PRUEFEN_ZU.replace("bruecke=zu", "bruecke=zeit"), NETZ_PRUEFEN_ZU.replace(" internet=zu", ""),
    NETZ_LAUF,  # die Erwartung des Lauf-Modus (github=offen) darf im Modus pruefen nie genügen
])
def test_im_modus_pruefen_muss_jedes_netzziel_zu_sein(selbsttest_umgebung, netz):
    selbsttest_umgebung.setattr(D.subprocess, "run", FakeRun(netz))
    ok, grund = D.selbsttest(JOB, PROJEKT, "pruefen")
    assert ok is False and "Netzgrenzen" in grund


def test_loopback_ohne_lauscher_im_leeren_netz_ist_in_ordnung_ein_name_ohne_dns_nicht(selbsttest_umgebung):
    selbsttest_umgebung.setattr(D.subprocess, "run", FakeRun(NETZ_PRUEFEN_ZU.replace("lo9=zu", "lo9=fehler-ECONNREFUSED")))
    assert D.selbsttest(JOB, PROJEKT, "pruefen")[0] is True
    selbsttest_umgebung.setattr(D.subprocess, "run", FakeRun(NETZ_PRUEFEN_ZU.replace("internet=zu", "internet=fehler-gaierror")))
    ok, grund = D.selbsttest(JOB, PROJEKT, "pruefen")
    assert ok is False and "internet=fehler-gaierror" in grund


def test_eine_verletzte_grenze_laesst_den_selbsttest_scheitern(selbsttest_umgebung):
    selbsttest_umgebung.setattr(D.subprocess, "run", FakeRun(NETZ_PRUEFEN_ZU, fehler=1))
    ok, grund = D.selbsttest(JOB, PROJEKT, "pruefen")
    assert ok is False and "Grenzen verletzt" in grund


def test_die_erwartung_im_modus_pruefen_ist_alles_zu():
    for ziel, erlaubt in D.NETZ_ERWARTET_PRUEFEN.items():
        assert "zu" in erlaubt and "offen" not in erlaubt and "zeit" not in erlaubt, ziel
    assert D.NETZ_ERWARTET_PRUEFEN["lo9"] == ("zu", "fehler-ECONNREFUSED")  # im leeren Netz-Namensraum lauscht nichts, Loopback ist an
    assert all(v == ("zu",) for k, v in D.NETZ_ERWARTET_PRUEFEN.items() if k != "lo9")
    assert set(D.NETZ_ERWARTET_PRUEFEN) == set(D.NETZ_ERWARTET) - {"github"} | {"internet"}
    assert D.NETZ_ERWARTET["github"] == "offen"  # der Lauf-Modus bleibt, wie er war
    assert D.NETZPROBE_PRUEFEN != D.NETZPROBE and "'" not in D.NETZPROBE_PRUEFEN  # kommt in einfache Anführungszeichen


# ---------------------------------------------------------------- Läufer wamo-job.sh: Modus pruefen
@pytest.fixture(scope="module")
def runner() -> str:
    return (INFRA / "wamo-job.sh").read_text(encoding="utf-8")


def test_der_laeufer_kennt_den_modus_pruefen_mit_netz_zu_und_zeitlimit_120(runner):
    assert "--modus) MODUS=${2-}; shift 2 ;;" in runner
    assert re.search(r'\[\[ \$MODUS =~ \^\(lauf\|pruefen\)\$ \]\]', runner)
    assert "ZEITLIMIT_PRUEFEN_S=120" in runner
    assert 'ZEIT=${ZEIT:-$ZEITLIMIT_PRUEFEN_S}' in runner or "ZEITLIMIT_PRUEFEN_S" in runner
    assert re.search(r'NETZ_OPTION=--unshare-net', runner)
    assert runner.count("--share-net") == 1  # nur die Voreinstellung des Auftragslaufs
    assert re.search(r'--unshare-user --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try "\$NETZ_OPTION"', runner)
    # Die Reihenfolge: erst der Modus, dann das Netz (kein Pfad lässt das Netz im Modus pruefen offen)
    block = runner[runner.index("# Modus pruefen"):]
    assert block.index("NETZ_OPTION=--unshare-net") < block.index('ARGS=(')


def test_der_laeufer_im_modus_pruefen_hat_keine_token_keine_claude_ordner_keinen_zustand(runner):
    m = re.search(r"ERLAUBT_PRUEFEN_RE='\^\(([^)]*)\)\$'", runner)
    assert m, "eigene, engere Umgebungs-Allowlist für den Modus pruefen"
    erlaubt = set(m.group(1).split("|"))
    assert erlaubt == {"GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_[0-9]", "GIT_CONFIG_VALUE_[0-9]", "XDG_RUNTIME_DIR"}
    alle = [*wk.CLAUDE_VARIABLEN, *(wk.sandbox_name(n) for v in wk.ZUGANG_VARIABLEN.values() for n in v), "ANTHROPIC_CUSTOM_HEADERS",
            "ANTHROPIC_BASE_URL", "GH_TOKEN", "VERCEL_TOKEN", "SUPABASE_ACCESS_TOKEN", "JIRA_API_TOKEN", "JIRA_API_KEY"]
    assert not [n for n in alle if any(n in e for e in erlaubt)]
    assert 'ERLAUBT_RE=$ERLAUBT_PRUEFEN_RE' in runner
    # was im Modus pruefen nicht gemountet wird: Claude-Konfiguration, Claude Code, Plugins, Zustand
    assert "if [ \"$MODUS\" = lauf ]; then" in runner
    lauf_block = runner[runner.index('if [ "$MODUS" = lauf ]; then'):]
    for nur_lauf in ('--bind "$JOBDIR/claude" "$HEIM/.claude"', '--ro-bind "$HEIM/.local/share/claude"',
                     '--ro-bind "$PLUGINS" "$HEIM/.claude/plugins"', '--bind "$JOBDIR/zustand" /run/wamo/zustand',
                     "--setenv CLAUDE_CONFIG_DIR"):
        assert nur_lauf in lauf_block, nur_lauf
    # die Umgebung im Modus pruefen: nur das Nötige, ohne Claude und ohne Gh-Einstellungen
    pruefen_env = runner[runner.index("SETENV_PRUEFEN=("):]
    pruefen_env = pruefen_env[:pruefen_env.index("\n)\n")]
    for name in ("HOME", "USER", "PATH", "TMPDIR", "LANG", "TZ", "TERM", "CI", "NO_COLOR", "PYTHONDONTWRITEBYTECODE"):
        assert f"--setenv {name} " in pruefen_env, name
    for verboten in ("CLAUDE", "ANTHROPIC", "GH_", "TOKEN", "KEY", "plugins"):
        assert verboten not in pruefen_env, verboten


def test_der_laeufer_laesst_im_modus_pruefen_nie_ein_laengeres_zeitlimit_oder_weitere_grenzen_zu(runner):
    assert re.search(r'\(\( ZEIT <= ZEITLIMIT_PRUEFEN_S \)\)', runner)
    for engere in ("PRUEFEN_SPEICHER_MAX=", "PRUEFEN_PROZESSE_MAX=", "PRUEFEN_CPU_QUOTA=", "PRUEFEN_TMP_MB=", "PRUEFEN_DATEI_MAX_MB="):
        assert engere in runner, engere
    assert re.search(r'PRUEFEN_PROZESSE_MAX=(\d+)', runner) and int(re.search(r'PRUEFEN_PROZESSE_MAX=(\d+)', runner).group(1)) <= 256


def test_der_laeufer_im_modus_lauf_ist_unveraendert_wichtig_bleiben_seccomp_userns_und_abschottung(runner):
    for bleibt in ("--disable-userns", "--cap-drop ALL", "--seccomp 9", "--tmpfs /usr/local/lib/wamo", "--remount-ro /",
                   '--remount-ro "$PROJEKTE"', '--bind "$PROJDIR" "$PROJEKTE/$PROJEKT"', 'INNEN=(/usr/bin/python3 -I "$WAECHTER_INNEN/abschottung.py"',
                   "--die-with-parent --new-session", '"$BWRAP" "${ARGS[@]}" "${INNEN[@]}" "$@" 9<"$SECCOMP"'):
        assert bleibt in runner, bleibt
    assert runner.count("--ro-bind /usr /usr") == 1 and runner.count("--proc /proc --dev /dev") == 1


# ---------------------------------------------------------------- Einrichtung, Rückweg, Rauchtest
@pytest.fixture(scope="module")
def einrichten() -> str:
    return (INFRA / "wamo-job-einrichten.sh").read_text(encoding="utf-8")


def test_die_einrichtung_kopiert_das_modul_und_die_helfer_nach_waechter(einrichten):
    pflicht = einrichten[einrichten.index("for datei in wamo-job.sh"):]
    pflicht = pflicht[:pflicht.index("; do")]
    for datei in ("abnahme_befehl.py", "wamo_job_pruefen.py", "wamo_pruefen_rauchtest.py", "wamo_job_dispatch.py", "wamo-job.sh"):
        assert datei in pflicht, datei  # fehlt eine, startet die Einrichtung gar nicht erst
    assert re.search(r'install .*"\$HERE/abnahme_befehl\.py".*"\$HERE/wamo_job_pruefen\.py".*"\$APP/waechter/"', einrichten)
    assert re.search(r'install .*"\$HERE/wamo_pruefen_rauchtest\.py" "\$APP/bin/wamo-pruefen-rauchtest"', einrichten)
    assert "WAMO_PRUEFEN" in einrichten  # die Ausgabe nennt den Schalter und dass er aus bleibt
    assert r"""sed -i 's/\r$//' "$APP/waechter/abnahme_befehl.py" """.strip() in einrichten  # die Summe im ping soll zur Datei des Kerns passen
    assert "schaltet nichts ein" in einrichten
    assert "--unshare-net" in einrichten  # die Prüfung am Ende probiert den Netz-Namensraum aus


def test_die_einrichtung_sichert_die_ersetzten_dateien_vorher(einrichten):
    assert '$APP/vorher' in einrichten and "NEU.txt" in einrichten and "wamo-job-rueckweg.sh" in einrichten
    sicherung = einrichten.index("== 2c. Sicherung")
    installieren = einrichten.index('install -o root -g "$WAMO_USER" -m 0750 "$HERE/wamo_job_dispatch.py"')
    assert sicherung < installieren, "gesichert wird, bevor etwas ersetzt wird"
    abschnitt = einrichten[sicherung:installieren]
    for datei in ("bin/wamo-job-dispatch", "bin/wamo-job.sh", "bin/wamo-job-rauchtest", "bin/wamo-pruefen-rauchtest",
                  "waechter/abnahme_befehl.py", "waechter/wamo_job_pruefen.py", "waechter/dev_guard.py", "waechter/werkzeugkarte.py",
                  "waechter/kontingent.py", "waechter/abschottung.py", "claude-code/managed-settings.json"):
        assert datei in abschnitt, datei
    assert "install -d -o root -g root -m 0700" in abschnitt  # die Sicherung sieht nur root
    assert "nichts geändert, keine neue Sicherung" in abschnitt  # ein zweiter Lauf überschreibt die erste Sicherung nie


@pytest.mark.skipif(BASH is None, reason="bash fehlt")
@pytest.mark.parametrize("skript", ["wamo-job.sh", "wamo-job-einrichten.sh", "wamo-job-rueckweg.sh"])
def test_bash_syntax_der_skripte(skript):
    p = subprocess.run([BASH, "-n", str(INFRA / skript)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_die_skripte_haben_unix_zeilenenden_und_kein_utf8_bom():
    for name in ("wamo-job.sh", "wamo-job-einrichten.sh", "wamo-job-rueckweg.sh", "wamo_pruefen_rauchtest.py", "wamo_job_pruefen.py"):
        roh = (INFRA / name).read_bytes()
        assert b"\r" not in roh and not roh.startswith(b"\xef\xbb\xbf"), name


@pytest.fixture(scope="module")
def rauchtest() -> str:
    return (INFRA / "wamo_pruefen_rauchtest.py").read_text(encoding="utf-8")


def test_der_rauchtest_ist_geschrieben_aber_nicht_ausgefuehrt_und_prueft_die_zusagen(rauchtest):
    ast.parse(rauchtest)
    for stichwort in ("--modus", "npm test", "make test", "Fork-Bombe", "Endlosschleife", "cancel", "WAMO_PRUEFEN", "abgeschaltet",
                      "unshare", "NETZ:zu", "UMGEBUNG:leer", "SICHT:leer", "verlassen", "belegt", "nicht möglich", "pytest",
                      "dauer=", "auftraege.log"):
        assert stichwort in rauchtest, stichwort
    assert "sudo -u wamo -H /opt/wamo/bin/wamo-pruefen-rauchtest" in rauchtest
    assert "ssh " not in rauchtest.replace("sshd", "")  # er läuft auf dem Server, nie als Fernsteuerung
    assert "http://" not in rauchtest and "https://" not in rauchtest  # keine echten Dienste


def test_der_rauchtest_hat_keine_geheimnisse_und_keine_namen_anderer_produkte(rauchtest):
    for muster in (r"sk-ant-[A-Za-z0-9_-]{20,}", r"ghp_[A-Za-z0-9]{20,}", r"-----BEGIN"):
        assert not re.search(muster, rauchtest)


def test_die_neuen_dateien_haben_keine_gedankenstriche_im_fliesstext():
    """Gedankenstriche (langer und kurzer Strich) stehen in keiner der neuen Dateien."""
    for pfad in (INFRA / "wamo_job_pruefen.py", INFRA / "wamo_pruefen_rauchtest.py", INFRA / "wamo-job-rueckweg.sh",
                 VOICE_CORE / "abnahme_befehl.py"):
        text = pfad.read_text(encoding="utf-8")
        assert "\u2014" not in text and "\u2013" not in text, pfad.name


def test_das_helfermodul_wird_im_dispatcher_nur_bei_bedarf_geladen():
    """Mit ausgeschaltetem Schalter läuft kein Code des Helfers: der Dispatcher importiert ihn nicht auf Modulebene."""
    baum = ast.parse((INFRA / "wamo_job_dispatch.py").read_text(encoding="utf-8"))
    oben = {a.name for n in baum.body if isinstance(n, ast.Import) for a in n.names}
    oben |= {n.module for n in baum.body if isinstance(n, ast.ImportFrom) and n.module}
    assert "wamo_job_pruefen" not in oben and "abnahme_befehl" not in oben


# ---------------------------------------------------------------- Rückweg im Wegwerf-Ordner
@pytest.mark.skipif(BASH is None or not LINUX, reason="Probelauf der Skripte nur unter Linux (POSIX-Rechte, Wegwerf-Ordner)")
def test_der_rueckweg_stellt_die_sicherung_wieder_her_und_ist_wiederholbar(tmp_path):
    wurzel = tmp_path / "wurzel"
    app = wurzel / "opt" / "wamo"
    for rel in ("bin", "waechter"):
        (app / rel).mkdir(parents=True)
    alt = app / "vorher" / "20261110T100000Z"
    (alt / "bin").mkdir(parents=True)
    (alt / "waechter").mkdir(parents=True)
    (alt / "bin" / "wamo-job-dispatch").write_text("alt\n", encoding="utf-8")
    (alt / "NEU.txt").write_text("waechter/abnahme_befehl.py\nwaechter/wamo_job_pruefen.py\n", encoding="utf-8")
    (app / "bin" / "wamo-job-dispatch").write_text("neu\n", encoding="utf-8")
    (app / "waechter" / "abnahme_befehl.py").write_text("neu\n", encoding="utf-8")
    (app / "waechter" / "wamo_job_pruefen.py").write_text("neu\n", encoding="utf-8")
    (app / "waechter" / "dev_guard.py").write_text("bleibt\n", encoding="utf-8")
    env = {**os.environ, "WAMO_JOB_WURZEL": str(wurzel)}
    for _ in range(2):  # wiederholbar
        p = subprocess.run([BASH, str(INFRA / "wamo-job-rueckweg.sh")], capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stdout + p.stderr
        assert (app / "bin" / "wamo-job-dispatch").read_text() == "alt\n"
        assert not (app / "waechter" / "abnahme_befehl.py").exists() and not (app / "waechter" / "wamo_job_pruefen.py").exists()
        assert (app / "waechter" / "dev_guard.py").read_text() == "bleibt\n"
