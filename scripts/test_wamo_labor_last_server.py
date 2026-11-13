"""Tests für scripts/wamo_labor_last_server.py (lesende Messung am Kern und die festen Aufgaben am Labor-Dienst).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q test_wamo_labor_last_server.py

Kein ssh, kein Server: Die beiden Programme, die am Server laufen (REMOTE_MESSUNG und REMOTE_DIENST), werden in-process mit Attrappen für
Dateisystem, Journal und HTTP ausgeführt und einmal mit dem echten Interpreter gestartet (python -I -c, Eingabe über stdin) wie am Server.
Eine Falle bricht jeden Test ab, der ssh oder einen anderen Prozess starten würde.
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor as wl  # noqa: E402
import wamo_labor_last_server as sv  # noqa: E402

GEHEIM = "TOKEN-nie-ausgeben-0123456789abcdefghijklmnopqrstuvwxyz"


@pytest.fixture(autouse=True)
def keine_echten_aufrufe(monkeypatch):
    def falle(*args, **kw):
        raise AssertionError("Der Test hat versucht, einen Prozess zu starten")
    monkeypatch.setattr(sv.subprocess, "run", falle)


def programm(text: str) -> dict:
    ns: dict = {}
    exec(compile(text, "<remote>", "exec"), ns)  # noqa: S102 (das Programm, das auf dem Server läuft)
    return ns


# ---------- Programm der Messung am Server ----------

@pytest.fixture
def ns():
    return programm(sv.REMOTE_MESSUNG)


def cgroup(tmp_path: Path, cpu="usage_usec 5000000\nuser_usec 3000000\nsystem_usec 2000000\n", speicher="1234567890\n",
           spitze="2000000000\n", aufgaben="42\n") -> Path:
    ordner = tmp_path / "wamo-core@b.service"
    ordner.mkdir(exist_ok=True)
    for name, inhalt in (("cpu.stat", cpu), ("memory.current", speicher), ("memory.peak", spitze), ("pids.current", aufgaben)):
        if inhalt is not None:
            (ordner / name).write_text(inhalt)
    return tmp_path


def lesbar(zeilen):
    return lambda slot, seit, bis: (True, iter(zeilen))


def test_messung_liest_cgroup_werte(ns, tmp_path):
    aus = ns["messen"]({"slot": "b", "seit": None}, str(cgroup(tmp_path)), lesbar([]), lambda: 1000.5)
    assert aus["zeit"] == 1000.5 and aus["aktiv"] is True
    assert aus["cpu_usec"] == 5000000 and aus["speicher"] == 1234567890
    assert aus["speicher_spitze"] == 2000000000 and aus["aufgaben"] == 42
    assert isinstance(aus["kerne"], int) and aus["kerne"] >= 1
    assert aus["journal"] is None  # ohne Zeitpunkt wird das Journal nicht gelesen


def test_fehlende_oder_unbrauchbare_werte_sind_null_nie_null_als_zahl(ns, tmp_path):
    aus = ns["messen"]({"slot": "b", "seit": None}, str(cgroup(tmp_path, cpu="nichts\n", speicher="max\n", spitze=None, aufgaben="x")),
                           lesbar([]), lambda: 5.0)
    assert aus["cpu_usec"] is None and aus["speicher"] is None and aus["speicher_spitze"] is None and aus["aufgaben"] is None


def test_ohne_cgroup_ordner_ist_der_kern_nicht_aktiv(ns, tmp_path):
    aus = ns["messen"]({"slot": "a", "seit": None}, str(tmp_path), lesbar([]), lambda: 5.0)
    assert aus["aktiv"] is False and aus["cpu_usec"] is None


@pytest.mark.parametrize("slot", ["c", "", None, "b;rm", "../b", "ab", 1])
def test_nur_die_slots_a_und_b_werden_gelesen(ns, tmp_path, slot):
    assert ns["verarbeite"]({"slot": slot, "seit": None}, str(tmp_path), lesbar([]), lambda: 5.0) == 1


@pytest.mark.parametrize("seit", ["jetzt", -1, 0, 2000.0, True, [1]])
def test_unsinniger_zeitpunkt_ist_ein_fehler(ns, tmp_path, seit):
    assert ns["verarbeite"]({"slot": "b", "seit": seit}, str(cgroup(tmp_path)), lesbar([]), lambda: 1000.0) == 1


ZEILEN = [
    "1699999999.5 sokrates2 wamo-core[1]: Pipeline: Anfrage an das Modell beginnt (von Llm)",  # vor dem Fenster
    "1700000001.0 sokrates2 wamo-core[1]: Pipeline: Anfrage an das Modell beginnt (von Llm)",
    "1700000002.0 sokrates2 wamo-core[1]: Pipeline: Anfrage an das Modell beginnt (von Llm)",
    "1700000061.0 sokrates2 wamo-core[1]: Pipeline: Anfrage an das Modell beginnt (von Llm)",  # nächste Minute
    "1700000003.0 sokrates2 wamo-core[1]: Pipeline: Unterbrechung (von Transport)",
    "1700000004.0 sokrates2 wamo-core[1]: Pipeline: Fehler von SonioxSTTService#0: connection closed",
    "1700000005.0 sokrates2 wamo-core[1]: Anthropic Error code: 429 - {'error': {'type': 'rate_limit_error'}}",
    "1700000006.0 sokrates2 wamo-core[1]: Werkzeug fehlgeschlagen: Too Many Requests",
    "1700000007.0 sokrates2 wamo-core[1]: status_code=429 bei der Anfrage",
    "1700000008.0 sokrates2 wamo-core[1]: Soniox Stream konnte nicht geöffnet werden",
    "1700000009.0 sokrates2 wamo-core[1]: Soniox sendet Wörter: Hallo",  # Soniox ohne Fehler
    "1700000010.0 sokrates2 wamo-core[1]: Port 4290 und 4429 sind frei, 429 Zeichen gelesen",  # 429 ohne Fehlerbezug
    'LATENZ_TOKEN Zeile ohne Zeitstempel',
    '1700000011.0 sokrates2 wamo-core[1]: LATENZ_TOKEN {"nummer": 1, "eingabe": 17000, "cache_lesen": 15000, "cache_schreiben": 100}',
    '1700000012.0 sokrates2 wamo-core[1]: LATENZ_TOKEN {"nummer": 2, "eingabe": 18000, "cache_lesen": 16000, "cache_schreiben": 0}',
    '1700000013.0 sokrates2 wamo-core[1]: LATENZ_TOKEN {kaputt',
    "1700000999.0 sokrates2 wamo-core[1]: Pipeline: Anfrage an das Modell beginnt (von Llm)",  # nach dem Fenster
]


def zaehlen(ns, tmp_path, zeilen=ZEILEN, seit=1700000000.0, jetzt=1700000100.0):
    return ns["messen"]({"slot": "b", "seit": seit}, str(cgroup(tmp_path)), lesbar(zeilen), lambda: jetzt)


def test_journal_wird_im_fenster_gezaehlt(ns, tmp_path):
    aus = zaehlen(ns, tmp_path)
    j = aus["journal"]
    assert aus["journal_lesbar"] is True
    assert j["anfragen"] == 3 and j["unterbrechungen"] == 1 and j["pipeline_fehler"] == 1
    assert j["r429"] == 3  # Error code: 429, Too Many Requests, status_code=429, aber nicht 4290 und nicht "429 Zeichen"
    assert j["soniox"] == 2  # der Fehler der Pipeline und der Stream, nicht die Wörter
    assert j["token_zuege"] == 2 and j["token_eingabe"] == 35000
    assert j["token_cache_lesen"] == 31000 and j["token_cache_schreiben"] == 100


def test_anfragen_und_tokens_je_minute(ns, tmp_path):
    minuten = zaehlen(ns, tmp_path)["journal"]["minuten"]
    erste = str(1700000000 // 60 * 60)
    zweite = str(1700000061 // 60 * 60)
    assert minuten[erste] == {"anfragen": 2, "eingabe": 35000}
    assert minuten[zweite] == {"anfragen": 1, "eingabe": 0}


def test_ein_nicht_lesbares_journal_zaehlt_nie_als_null(ns, tmp_path):
    aus = ns["messen"]({"slot": "b", "seit": 1700000000.0}, str(cgroup(tmp_path)), lambda s, a, b: (False, iter([])), lambda: 1700000100.0)
    assert aus["journal_lesbar"] is False and aus["journal"] is None


def test_zu_viele_zeilen_werden_abgeschnitten_und_gemeldet(ns, tmp_path):
    ns["MAX_ZEILEN"] = 3
    j = zaehlen(ns, tmp_path, zeilen=[ZEILEN[1]] * 10)["journal"]
    assert j["anfragen"] == 3 and j["abgeschnitten"] is True


def test_nichts_aus_dem_journal_wird_zurueckgegeben_ausser_zahlen(ns, tmp_path):
    antwort = json.dumps(zaehlen(ns, tmp_path))
    assert "Hallo" not in antwort and "sokrates2" not in antwort and "rate_limit_error" not in antwort


# ---- Journal lesen: erst ohne, dann mit sudo, nur lesend ----

class Prozess:
    def __init__(self, zeilen, fehlertext="", code=0):
        self.aus = "".join(z + "\n" for z in zeilen)
        self.fehler = fehlertext
        self.returncode = code

    def communicate(self, timeout=None):
        return self.aus, self.fehler

    def kill(self):
        pass


def starter(antworten):
    aufrufe = []

    def starten(cmd, **kw):
        aufrufe.append(cmd)
        return antworten.pop(0)

    return starten, aufrufe


def test_journal_wird_zuerst_ohne_sudo_gelesen(ns):
    starten, aufrufe = starter([Prozess(["1.0 a b: x"])])
    ok, zeilen = ns["journal_holen"]("b", 100.0, 200.0, starten)
    assert ok is True and list(zeilen) == ["1.0 a b: x"]
    assert aufrufe[0][:2] == ["journalctl", "-u"] and "wamo-core@b.service" in aufrufe[0]
    assert "--since=@100" in aufrufe[0] and "--until=@201" in aufrufe[0] and "short-unix" in aufrufe[0]


def test_ohne_leserecht_wird_sudo_n_versucht(ns):
    starten, aufrufe = starter([Prozess([], "No journal files were opened due to insufficient permissions.", 0), Prozess(["2.0 a b: y"])])
    ok, zeilen = ns["journal_holen"]("a", 1.0, 2.0, starten)
    assert ok is True and list(zeilen) == ["2.0 a b: y"]
    assert aufrufe[1][:3] == ["sudo", "-n", "journalctl"] and "wamo-core@a.service" in aufrufe[1]


def test_ohne_sudo_ist_das_journal_nicht_lesbar(ns):
    starten, _ = starter([Prozess([], "insufficient permissions"), Prozess([], "sudo: a password is required", 1)])
    ok, zeilen = ns["journal_holen"]("b", 1.0, 2.0, starten)
    assert ok is False and list(zeilen) == []


def test_fehlendes_journalctl_ist_nicht_lesbar(ns):
    def starten(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    ok, zeilen = ns["journal_holen"]("b", 1.0, 2.0, starten)
    assert ok is False and list(zeilen) == []


def test_journal_befehle_enthalten_nur_feste_schlueter_und_keinen_schreibzugriff(ns):
    starten, aufrufe = starter([Prozess([], "insufficient permissions"), Prozess([])])
    ns["journal_holen"]("b", 1.0, 2.0, starten)
    erlaubt = {"journalctl", "-u", "wamo-core@b.service", "--since=@1", "--until=@3", "-o", "short-unix", "--no-pager", "sudo", "-n", "200000"}
    for cmd in aufrufe:
        assert set(cmd) <= erlaubt


# ---- das Programm als Ganzes, wie am Server ----

def test_programm_laeuft_mit_dem_echten_interpreter_wie_am_server(monkeypatch):
    monkeypatch.undo()  # die Falle ersetzt subprocess.run: hier ist der echte Start gewollt
    code = f"import base64;exec(base64.b64decode('{base64.b64encode(sv.REMOTE_MESSUNG.encode()).decode()}').decode('utf-8'))"
    r = subprocess.run([sys.executable, "-I", "-c", code], input=json.dumps({"slot": "b", "seit": None}), capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    daten = json.loads(r.stdout)
    assert daten["aktiv"] in (True, False) and isinstance(daten["kerne"], int) and "journal" in daten


def test_haupt_gibt_eine_zeile_json_und_lehnt_kaputte_eingabe_ab(ns, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ns["sys"], "stdin", io.StringIO("kein json"))
    assert ns["haupt"]() == 1
    monkeypatch.setattr(ns["sys"], "stdin", io.StringIO("[1, 2]"))
    assert ns["haupt"]() == 1
    ns["CGROUP_WURZEL"] = str(cgroup(tmp_path))
    monkeypatch.setattr(ns["sys"], "stdin", io.StringIO(json.dumps({"slot": "b", "seit": None})))
    capsys.readouterr()
    assert ns["haupt"]() == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["speicher"] == 1234567890


# ---------- Messung auf der Seite des Skripts ----------

def antwort_messung(**uebersteuert) -> dict:
    basis = {"zeit": 1000.0, "aktiv": True, "kerne": 20, "cpu_usec": 5_000_000, "speicher": 1_000_000_000, "speicher_spitze": 1_500_000_000,
             "aufgaben": 30, "journal_lesbar": True,
             "journal": {"zeilen": 10, "r429": 0, "soniox": 0, "anfragen": 4, "unterbrechungen": 1, "pipeline_fehler": 0, "token_zuege": 2,
                         "token_eingabe": 34000, "token_cache_lesen": 30000, "token_cache_schreiben": 0, "abgeschnitten": False,
                         "minuten": {"960": {"anfragen": 4, "eingabe": 34000}}}}
    basis.update(uebersteuert)
    return basis


class Ssh:
    """Attrappe für ssh_ausfuehren: merkt sich Befehl, Eingabe und Frist."""

    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.aufrufe: list[tuple] = []

    def __call__(self, host, befehl, eingabe, frist):
        self.aufrufe.append((host, befehl, eingabe, frist))
        a = self.antworten.pop(0) if len(self.antworten) > 1 else self.antworten[0]
        return a if isinstance(a, wl.Ergebnis) else wl.Ergebnis(0, json.dumps(a), "")


def test_server_messen_liest_und_prueft_die_antwort():
    ssh = Ssh(antwort_messung())
    server = sv.Server("wamo-sokrates2", ssh, uhr=lambda: 1000.0)
    m = server.messen(seit=900.0)
    assert m.zeit == 1000.0 and m.aktiv and m.kerne == 20 and m.cpu_usec == 5_000_000 and m.speicher == 1_000_000_000
    assert m.versatz_s == pytest.approx(0.0)
    assert m.journal.anfragen == 4 and m.journal.minuten[960] == (4, 34000) and m.journal_lesbar
    host, befehl, eingabe, frist = ssh.aufrufe[0]
    assert host == "wamo-sokrates2" and befehl.startswith("python3 -I -c") and "sudo" not in befehl
    assert json.loads(eingabe) == {"slot": "b", "seit": 900.0} and frist <= 60


def test_der_versatz_ist_serverzeit_minus_lokale_mitte():
    zeiten = iter([100.0, 102.0])
    server = sv.Server("h", Ssh(antwort_messung(zeit=110.0)), uhr=lambda: next(zeiten))
    assert server.messen(None).versatz_s == pytest.approx(110.0 - 101.0)


def test_nicht_lesbares_journal_bleibt_ohne_zaehlung():
    m = sv.Server("h", Ssh(antwort_messung(journal=None, journal_lesbar=False)), uhr=lambda: 1.0).messen(5.0)
    assert m.journal is None and m.journal_lesbar is False


@pytest.mark.parametrize("kaputt", [
    {"zeit": "x"}, {"aktiv": "ja"}, {"kerne": 0}, {"cpu_usec": -5}, {"speicher": 1.5}, {"aufgaben": "30"}, {"journal_lesbar": 1},
    {"journal": {"r429": "0"}}, {"journal": {**antwort_messung()["journal"], "minuten": {"x": {"anfragen": 1, "eingabe": 0}}}},
    {"journal": {**antwort_messung()["journal"], "anfragen": -1}},
])
def test_unerwartete_antworten_werden_abgelehnt(kaputt):
    with pytest.raises(sv.ServerFehler, match="unerwartet"):
        sv.Server("h", Ssh(antwort_messung(**kaputt)), uhr=lambda: 1.0).messen(5.0)


def test_antwort_die_kein_json_ist_wird_abgelehnt():
    with pytest.raises(sv.ServerFehler, match="unerwartet"):
        sv.Server("h", Ssh(wl.Ergebnis(0, "Hallo Welt", "")), uhr=lambda: 1.0).messen(None)


def test_ssh_fehler_werden_kurz_und_ohne_rohausgabe_gemeldet():
    lang = "x" * 5000
    with pytest.raises(sv.ServerFehler) as e:
        sv.Server("h", Ssh(wl.Ergebnis(255, "", "ssh: connect to host: Connection timed out\n" + lang)), uhr=lambda: 1.0).messen(None)
    assert "Connection timed out" in str(e.value) and len(str(e.value)) < 400


def test_ungueltiger_slot_und_host_werden_vor_dem_ersten_aufruf_abgelehnt():
    with pytest.raises(sv.ServerFehler, match="Slot"):
        sv.Server("h", Ssh({}), slot="c")
    for host in ("-oProxyCommand=x", "a b", "", "a;b"):
        with pytest.raises(sv.ServerFehler, match="Host"):
            sv.Server(host, Ssh({}))


# ---------- ssh_ausfuehren ----------

def test_ssh_ausfuehren_ruft_ssh_mit_batchmode_und_zeitlimit(monkeypatch):
    gesehen = {}

    def run(cmd, **kw):
        gesehen["cmd"], gesehen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, "aus", "fehler")

    monkeypatch.setattr(sv.subprocess, "run", run)
    e = sv.ssh_ausfuehren("wamo-sokrates2", "echo", "eingabe", 30)
    assert e == wl.Ergebnis(0, "aus", "fehler")
    assert gesehen["cmd"][0] == "ssh" and "BatchMode=yes" in gesehen["cmd"] and gesehen["cmd"][-2:] == ["wamo-sokrates2", "echo"]
    assert gesehen["kw"]["input"] == "eingabe" and gesehen["kw"]["timeout"] == 30 and "shell" not in gesehen["kw"]


def test_ssh_ausfuehren_meldet_zeitueberschreitung_und_fehlendes_ssh(monkeypatch):
    def zeit(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(sv.subprocess, "run", zeit)
    e = sv.ssh_ausfuehren("h", "x", "", 5)
    assert e.code == 1 and "Zeitüberschreitung" in e.stderr

    def weg(cmd, **kw):
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(sv.subprocess, "run", weg)
    assert sv.ssh_ausfuehren("h", "x", "", 5).code == 1


# ---------- Programm für den Dienst (nur feste Aufgaben, nur Plätze 1 bis 8) ----------

class Antwort:
    def __init__(self, status, daten):
        self.status, self._roh = status, json.dumps(daten).encode()

    def read(self, n=None):
        return self._roh


class Oeffner:
    def __init__(self, antwort):
        self.antwort, self.anfragen = antwort, []

    def __call__(self, anfrage, frist):
        self.anfragen.append((anfrage.get_method(), anfrage.full_url, anfrage.data, dict(anfrage.header_items()), frist))
        if isinstance(self.antwort, Exception):
            raise self.antwort
        return self.antwort


@pytest.fixture
def dienst_ns():
    return programm(sv.REMOTE_DIENST)


@pytest.fixture
def tokendatei(tmp_path):
    datei = tmp_path / "betreuer-token"
    datei.write_text(GEHEIM + "\n")
    return datei


def aufruf(dienst_ns, tokendatei, nutzlast, antwort):
    oeffner = Oeffner(antwort)
    return dienst_ns["verarbeite"](nutzlast, str(tokendatei), oeffner), oeffner


def test_neuer_durchlauf_sendet_die_einwilligungsversion(dienst_ns, tokendatei, capsys):
    code, oeffner = aufruf(dienst_ns, tokendatei, {"aktion": "neuer-durchlauf", "platz": 3, "version": 1},
                           Antwort(200, {"durchlauf_id": "x", "platz_token": "abcdef" * 6}))
    methode, url, daten, kopf, _ = oeffner.anfragen[0]
    assert code == 0 and methode == "POST" and url == "http://127.0.0.1:18765/api/betreuer/plaetze/3/neuer-durchlauf"
    assert json.loads(daten) == {"einwilligung_version": 1} and kopf["Authorization"] == "Bearer " + GEHEIM
    aus = capsys.readouterr().out
    assert json.loads(aus) == {"status": 200}  # kein Platz-Token, keine Kennung, nur der Status
    assert GEHEIM not in aus and "abcdef" not in aus


@pytest.mark.parametrize("aktion", ["durchlauf-beenden", "daten-loeschen"])
def test_beenden_und_loeschen_je_platz(dienst_ns, tokendatei, aktion):
    code, oeffner = aufruf(dienst_ns, tokendatei, {"aktion": aktion, "platz": 8}, Antwort(200, {}))
    assert code == 0 and oeffner.anfragen[0][1].endswith(f"/plaetze/8/{aktion}") and oeffner.anfragen[0][0] == "POST"


@pytest.mark.parametrize("nutzlast", [
    {"aktion": "neuer-durchlauf", "platz": 9, "version": 1}, {"aktion": "daten-loeschen", "platz": 0}, {"aktion": "daten-loeschen", "platz": "3"},
    {"aktion": "daten-loeschen", "platz": True}, {"aktion": "neuer-durchlauf", "platz": 1, "version": 0},
    {"aktion": "neuer-durchlauf", "platz": 1, "version": "1"}, {"aktion": "neuer-durchlauf", "platz": 1},
    {"aktion": "platz-token", "platz": 1}, {"aktion": "zuruecksetzen", "platz": 1}, {"aktion": "../x", "platz": 1}, {},
])
def test_unbekannte_aufgaben_und_plaetze_ausserhalb_1_bis_8_werden_abgelehnt(dienst_ns, tokendatei, nutzlast):
    code, oeffner = aufruf(dienst_ns, tokendatei, nutzlast, Antwort(200, {}))
    assert code == 1 and oeffner.anfragen == []


def test_plaetze_lesen_gibt_nur_zaehlungen_zurueck(dienst_ns, tokendatei, capsys):
    plaetze = [{"platz": n, "offen": False, "tickets": 0, "kommentare": 0, "hat_platz_token": False, "einwilligung_version": None,
                "text": "geheimer Gesprächstext"} for n in range(1, 17)]
    code, oeffner = aufruf(dienst_ns, tokendatei, {"aktion": "plaetze"}, Antwort(200, {"plaetze": plaetze}))
    aus = json.loads(capsys.readouterr().out)
    assert code == 0 and oeffner.anfragen[0][0] == "GET" and len(aus["plaetze"]) == 16
    assert "text" not in aus["plaetze"][0] and "geheimer" not in json.dumps(aus)


def test_fehlercode_des_dienstes_wird_als_kennung_weitergegeben(dienst_ns, tokendatei, capsys):
    code, _ = aufruf(dienst_ns, tokendatei, {"aktion": "neuer-durchlauf", "platz": 1, "version": 1}, Antwort(409, {"fehler": "version_zu_klein"}))
    assert code == 0 and json.loads(capsys.readouterr().out) == {"status": 409, "fehler": "version_zu_klein"}


def test_token_fehlt_oder_hat_falsche_form(dienst_ns, tmp_path, capsys):
    oeffner = Oeffner(Antwort(200, {}))
    assert dienst_ns["verarbeite"]({"aktion": "plaetze"}, str(tmp_path / "gibt-es-nicht"), oeffner) == 1
    kurz = tmp_path / "kurz"
    kurz.write_text("zu-kurz")
    assert dienst_ns["verarbeite"]({"aktion": "plaetze"}, str(kurz), oeffner) == 1
    assert oeffner.anfragen == [] and GEHEIM not in capsys.readouterr().err


def test_netzfehler_nennt_nie_den_token(dienst_ns, tokendatei, capsys):
    code, _ = aufruf(dienst_ns, tokendatei, {"aktion": "plaetze"}, urllib.error.URLError(GEHEIM))
    assert code == 1 and GEHEIM not in capsys.readouterr().err


def test_dienstprogramm_folgt_keiner_umleitung_und_nutzt_keinen_proxy(dienst_ns):
    assert dienst_ns["KeineUmleitung"]().redirect_request(None, None, 302, "", {}, "http://anderswo/") is None
    oeffner = dienst_ns["oeffner_bauen"]()
    assert callable(oeffner)


# ---------- Dienst auf der Seite des Skripts ----------

def plaetze_json(**je_platz) -> dict:
    liste = []
    for n in range(1, 17):
        p = {"platz": n, "offen": False, "tickets": 0, "kommentare": 0, "einwilligung": False, "platz_token": False}
        p.update(je_platz.get(str(n), {}))
        liste.append(p)
    return {"status": 200, "plaetze": liste}


def test_dienst_plaetze_lesen_streng():
    dienst = sv.Dienst("h", Ssh(plaetze_json(**{"2": {"offen": True, "tickets": 3}})))
    plaetze = dienst.plaetze()
    assert len(plaetze) == 16 and plaetze[1].offen and plaetze[1].tickets == 3 and plaetze[0].frei is True and plaetze[1].frei is False


@pytest.mark.parametrize("kaputt", [
    {"status": 200, "plaetze": []}, {"status": 200, "plaetze": "x"}, {"status": 500, "fehler": "intern"},
    {**plaetze_json(), "plaetze": [{"platz": 1}] * 16},
])
def test_dienst_antwort_unerwartet(kaputt):
    with pytest.raises(sv.ServerFehler):
        sv.Dienst("h", Ssh(kaputt)).plaetze()


def test_dienst_aufgaben_gehen_als_feste_nutzlast_ueber_sudo():
    ssh = Ssh({"status": 200})
    dienst = sv.Dienst("wamo-sokrates2", ssh)
    dienst.durchlauf_oeffnen(4, 1)
    dienst.durchlauf_beenden(4)
    dienst.daten_loeschen(4)
    nutzlasten = [json.loads(a[2]) for a in ssh.aufrufe]
    assert nutzlasten == [{"aktion": "neuer-durchlauf", "platz": 4, "version": 1}, {"aktion": "durchlauf-beenden", "platz": 4},
                          {"aktion": "daten-loeschen", "platz": 4}]
    assert all(a[1].startswith("sudo -n python3 -I -c") for a in ssh.aufrufe)


def test_dienst_meldet_fehlercode_und_lehnt_fremde_plaetze_ab():
    with pytest.raises(sv.ServerFehler, match="version_zu_klein"):
        sv.Dienst("h", Ssh({"status": 409, "fehler": "version_zu_klein"})).durchlauf_oeffnen(1, 1)
    with pytest.raises(sv.ServerFehler, match="Platz"):
        sv.Dienst("h", Ssh({"status": 200})).durchlauf_oeffnen(9, 1)
    with pytest.raises(sv.ServerFehler, match="Version"):
        sv.Dienst("h", Ssh({"status": 200})).durchlauf_oeffnen(1, 0)


def test_dienst_sudo_fehler_gibt_hinweis():
    with pytest.raises(sv.ServerFehler, match="sudo"):
        sv.Dienst("h", Ssh(wl.Ergebnis(1, "", "sudo: a password is required"))).plaetze()
