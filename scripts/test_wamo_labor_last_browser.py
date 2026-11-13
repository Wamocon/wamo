"""Tests für scripts/wamo_labor_last_browser.py (Treiber zum Browser-Worker: JSON-Zeilen über Pipes).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q test_wamo_labor_last_browser.py

Kein Browser, kein Node: Der Worker ist ein kleines Python-Programm im temporären Ordner, das dasselbe Protokoll spricht (eine JSON-Zeile je
Anfrage und je Antwort) und sich nach Stichwörtern in den Anfragen verhält (Antwort, Fehler, Hängen, Absturz, Lärm auf stdout).
"""
from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_last_browser as br  # noqa: E402
import wamo_labor_last_plan as pl  # noqa: E402

WORKER = textwrap.dedent('''
    import json, sys, threading, time
    sperre = threading.Lock()
    def antworten(d):
        with sperre:
            sys.stdout.write(json.dumps(d) + "\\n")
            sys.stdout.flush()
    def bearbeiten(a):
        cmd = a.get("cmd")
        if cmd == "haengen":
            time.sleep(60)
        elif cmd == "abbrechen":
            antworten({"id": a["id"], "ok": True, "daten": {"abgebrochen": True}})
        elif cmd == "lang":
            time.sleep(a.get("sekunden", 0.5))
            antworten({"id": a["id"], "ok": True, "daten": {"lang": True}})
        elif cmd == "fehler":
            antworten({"id": a["id"], "ok": False, "fehler": "Anmeldung scheiterte mit " + a.get("passwort", "") + " und " + a.get("code", "")})
        elif cmd == "absturz":
            sys.stderr.write("Absturz mit " + a.get("passwort", "") + "\\n")
            sys.stderr.flush()
            import os
            os._exit(3)
        elif cmd == "laerm":
            sys.stdout.write("kein json, nur Lärm\\n")
            sys.stdout.write(json.dumps({"id": 99999, "ok": True}) + "\\n")
            sys.stdout.flush()
            antworten({"id": a["id"], "ok": True, "daten": {"empfangen": sorted(a)}})
        elif cmd == "schliessen":
            antworten({"id": a["id"], "ok": True, "daten": {}})
            sys.stdout.flush()
            time.sleep(0.05)
            import os
            os._exit(0)
        else:
            antworten({"id": a["id"], "ok": True, "daten": {"cmd": cmd, "empfangen": sorted(a)}})
    for zeile in sys.stdin:
        threading.Thread(target=bearbeiten, args=(json.loads(zeile),), daemon=True).start()
''')


@pytest.fixture
def worker(tmp_path):
    datei = tmp_path / "worker.py"
    datei.write_text(WORKER, encoding="utf-8")
    return [sys.executable, "-u", str(datei)]


@pytest.fixture
def treiber(worker):
    t = br.NodeTreiber(worker, name="test")
    yield t
    t.schliessen()


# ---------- Protokoll ----------

def test_anfrage_und_antwort_laufen_ueber_json_zeilen(treiber):
    daten = treiber.anfrage("beliebig", {"a": 1}, frist_s=10)
    assert daten["cmd"] == "beliebig" and set(daten["empfangen"]) == {"id", "cmd", "a"}


def test_die_befehle_des_treibers_tragen_die_erwarteten_felder(treiber):
    assert treiber.starten("https://192.168.178.136:55445", Path("clips")) is None
    plan = pl.SchrittPlan("A1", "a1", 10.0, einwurf=True, einwurf_clip="einwurf_a")
    roh = treiber.schritt(plan, start_frist_s=45.0, ende_frist_s=90.0, einwurf_ab_s=3.0, freigabe="ablehnen")
    assert roh["cmd"] == "schritt" and {"clip", "einwurf_clip", "start_frist_s", "ende_frist_s", "einwurf_ab_s", "freigabe"} <= set(roh["empfangen"])


def test_fremde_antworten_und_laerm_auf_stdout_werden_ueberlesen(treiber):
    assert treiber.anfrage("laerm", {}, frist_s=10)["empfangen"] == ["cmd", "id"]


def test_ein_fehler_des_workers_wird_ein_treiberfehler(treiber):
    with pytest.raises(br.TreiberFehler) as e:
        treiber.anfrage("fehler", {}, frist_s=10)
    assert "Anmeldung scheiterte" in str(e.value)


def test_geheimnisse_werden_aus_fehlertexten_geschwaerzt(treiber):
    treiber.merke("Gehe1m-Passwort", "123456")
    with pytest.raises(br.TreiberFehler) as e:
        treiber.anfrage("fehler", {"passwort": "Gehe1m-Passwort", "code": "123456"}, frist_s=10)
    assert "Gehe1m" not in str(e.value) and "123456" not in str(e.value) and "***" in str(e.value)


def test_anmelden_schickt_die_zugangsdaten_nur_ueber_stdin_und_merkt_sie_sich(treiber):
    treiber.anmelden("labor-01@wamo.test", "Gehe1m-Passwort", "654321")
    with pytest.raises(br.TreiberFehler) as e:
        treiber.anfrage("fehler", {"passwort": "Gehe1m-Passwort", "code": "654321"}, frist_s=10)
    assert "Gehe1m-Passwort" not in str(e.value) and "654321" not in str(e.value)


def test_absturz_des_workers_meldet_sich_sofort_und_schwaerzt_stderr(treiber):
    treiber.merke("Gehe1m-Passwort")
    t0 = time.monotonic()
    with pytest.raises(br.TreiberFehler) as e:
        treiber.anfrage("absturz", {"passwort": "Gehe1m-Passwort"}, frist_s=30)
    assert time.monotonic() - t0 < 10
    assert "beendet" in str(e.value) and "Gehe1m" not in str(e.value) and "Ausgang 3" in str(e.value)


def test_antwort_nach_ablauf_der_frist_ist_ein_treiberfehler(treiber):
    with pytest.raises(br.TreiberFehler, match="Frist"):
        treiber.anfrage("haengen", {}, frist_s=0.5)


def test_mehrere_anfragen_gleichzeitig_werden_richtig_zugeordnet(treiber):
    ergebnisse = {}

    def lang():
        ergebnisse["lang"] = treiber.anfrage("lang", {"sekunden": 0.6}, frist_s=10)

    import threading
    t = threading.Thread(target=lang)
    t.start()
    time.sleep(0.1)
    assert treiber.anfrage("abbrechen", {}, frist_s=5) == {"abgebrochen": True}  # läuft, während "lang" noch wartet
    t.join(10)
    assert ergebnisse["lang"] == {"lang": True}


def test_abbrechen_ist_still_wenn_der_worker_schon_weg_ist(treiber):
    treiber.schliessen()
    treiber.abbrechen()  # kein Fehler


# ---------- Schließen ----------

def test_schliessen_beendet_den_prozess_und_ist_wiederholbar(worker):
    t = br.NodeTreiber(worker, name="x")
    t.anfrage("beliebig", {}, frist_s=10)
    t.schliessen()
    assert t.beendet
    t.schliessen()


def test_schliessen_erzwingt_das_ende_wenn_der_worker_nicht_antwortet(tmp_path):
    datei = tmp_path / "taub.py"
    datei.write_text("import sys, time\nsys.stdin.readline()\ntime.sleep(120)\n", encoding="utf-8")
    t = br.NodeTreiber([sys.executable, "-u", str(datei)], name="taub", schliess_frist_s=0.5)
    t.starten_prozess()
    t0 = time.monotonic()
    t.schliessen()
    assert time.monotonic() - t0 < 20 and t.beendet


def test_nach_dem_schliessen_gibt_es_keine_anfragen_mehr(worker):
    t = br.NodeTreiber(worker, name="x")
    t.schliessen()
    with pytest.raises(br.TreiberFehler, match="beendet"):
        t.anfrage("beliebig", {}, frist_s=5)


def test_start_scheitert_klar_wenn_das_programm_fehlt():
    t = br.NodeTreiber(["gibt-es-wirklich-nicht-wamo-xyz"], name="x")
    with pytest.raises(br.TreiberFehler, match="nicht gestartet"):
        t.anfrage("beliebig", {}, frist_s=5)


# ---------- Eingaben pruefen ----------

@pytest.mark.parametrize("url", ["https://192.168.178.136:55445", "https://192.168.178.136:55445/"])
def test_feste_adresse_wird_akzeptiert(url, worker):
    br.NodeTreiber(worker, name="x").starten(url, Path("clips"))


@pytest.mark.parametrize("url", ["https://example.org", "http://192.168.178.136:55445", "https://192.168.178.136:55445/x", "https://192.168.178.136:9",
                                 "https://192.168.178.136:55445@example.org", "", None, "https://127.0.0.1:55445"])
def test_jede_andere_adresse_wird_vor_dem_start_abgelehnt(url, worker):
    t = br.NodeTreiber(worker, name="x")
    with pytest.raises(br.TreiberFehler, match="Adresse"):
        t.starten(url, Path("clips"))
    assert not t.gestartet


def test_standardbefehl_nennt_node_und_den_worker_im_repo():
    befehl = br.standard_befehl()
    assert befehl[0] == "node" and befehl[1].endswith("wamo-last-browser.cjs") and Path(befehl[1]).is_file()


def test_json_aus_der_antwort_enthaelt_nie_die_anfrage_zurueck(treiber):
    treiber.merke("Gehe1m-Passwort")
    daten = treiber.anfrage("beliebig", {"x": 1}, frist_s=5)
    assert "Gehe1m" not in json.dumps(daten)
