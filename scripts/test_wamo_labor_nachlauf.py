"""Tests für scripts/wamo_labor_nachlauf.py (Nachlauf des Testlabors: sperren, Gesprächsdaten und Dienstdaten löschen, Nachweis).

Aufruf (aus voice-core, dort liegt die Python-Umgebung):  python -m pytest -p no:cacheprovider ../scripts/test_wamo_labor_nachlauf.py
Kein ssh, kein Server, keine Datenbank: Der Weg zum Kern (wamo_labor.run_remote) und der Weg zum Dienst (dienst_remote) werden durch
zustandsbehaftete Attrappen ersetzt. Das Programm, das auf dem Server beim Dienst läuft (REMOTE_DIENST), wird in-process mit einer
Attrappe für HTTP und einmal gegen einen echten Server auf Loopback ausgeführt. Eine Falle bricht jeden Test ab, der ssh starten würde.
"""
from __future__ import annotations

import ast
import base64
import http.server
import io
import json
import re
import subprocess
import sys
import threading
import types
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor as wl  # noqa: E402
import wamo_labor_nachlauf as na  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
QUELLE = Path(na.__file__)
GEHEIM = "TOKEN-nie-ausgeben-0123456789abcdefghijklmnopqrstuvwxyz"
NACH_LABORTAG = date(2026, 11, 29)
VOR_LABORTAG = date(2026, 11, 27)


@pytest.fixture(autouse=True)
def keine_echten_aufrufe(monkeypatch):
    """Falle: Kein Test darf ssh oder einen anderen Prozess starten (die Tests ersetzen subprocess.run selbst, wo sie es prüfen)."""
    monkeypatch.delenv("WAMO_SSH_HOST", raising=False)

    def falle(*args, **kw):
        raise AssertionError("Der Test hat versucht, einen Prozess zu starten")
    monkeypatch.setattr(na.subprocess, "run", falle)
    monkeypatch.setattr(wl.subprocess, "run", falle)


# ---------- Attrappen ----------

def ergebnis(daten: dict) -> wl.Ergebnis:
    return wl.Ergebnis(0, json.dumps(daten), "")


class Kern:
    """Attrappe für wamo_labor.run_remote: Zustand je Laborprofil, merkt sich jede Nutzlast und den gemeinsamen Ablauf."""

    def __init__(self, profile: int = 9, zeilen: int = 40, fehler: dict | None = None, rest: int = 0, ablauf: list | None = None):
        self.profile = {f"labor-{n:02d}": {"frei": True, "zeilen": zeilen} for n in range(1, profile + 1)}
        self.fehler, self.rest = fehler or {}, rest
        self.aufrufe: list[dict] = []
        self.hosts: list[str] = []
        self.ablauf = ablauf if ablauf is not None else []

    @property
    def aktionen(self) -> list[str]:
        return [a["aktion"] for a in self.aufrufe]

    def __call__(self, host: str, nutzlast: dict) -> wl.Ergebnis:
        self.hosts.append(host)
        self.aufrufe.append(dict(nutzlast))
        aktion = nutzlast["aktion"]
        self.ablauf.append(("kern", aktion))
        if aktion in self.fehler:
            fehler = self.fehler[aktion]
            if isinstance(fehler, BaseException):
                raise fehler
            return wl.Ergebnis(*fehler)
        if aktion == "status":
            if not self.profile:
                return wl.Ergebnis(0, "Keine Laborprofile.", "")
            zeilen = [f"{k} | platz | {'freigeschaltet' if p['frei'] else 'gesperrt'} | {p['zeilen']} Zeilen" for k, p in self.profile.items()]
            return wl.Ergebnis(0, "\n".join(zeilen), "")
        if aktion == "sperren":
            for p in self.profile.values():
                p["frei"] = False
            return wl.Ergebnis(0, f"{len(self.profile)} Laborprofile gesperrt (labor_aktiv = false).", "")
        if aktion == "loeschen":
            for p in self.profile.values():
                p["zeilen"] = self.rest
            return wl.Ergebnis(0, "\n".join(f"{k}: geloescht: turns 3" for k in self.profile), "")
        raise AssertionError(f"unerwartete Aktion {aktion}")


class Dienst:
    """Attrappe für dienst_remote: Zustand der 16 Plätze. antworten[(platz, aktion)] ist eine Folge von Statuscodes (der letzte bleibt)."""

    def __init__(self, tickets: int = 5, kommentare: int = 2, offen: int = 8, ablauf: list | None = None):
        self.plaetze = {n: {"platz": n, "offen": n <= offen, "tickets": tickets if n <= offen else 0,
                            "kommentare": kommentare if n <= offen else 0, "einwilligung": n <= offen, "platz_token": n <= offen}
                        for n in range(1, 17)}
        self.antworten: dict[tuple[int, str], list[int]] = {}
        self.ausfall: wl.Ergebnis | None = None
        self.bleibt = 0
        self.aufrufe: list[dict] = []
        self.ablauf = ablauf if ablauf is not None else []

    def __call__(self, host: str, nutzlast: dict) -> wl.Ergebnis:
        self.aufrufe.append(dict(nutzlast))
        self.ablauf.append(("dienst", nutzlast["aktion"], nutzlast.get("platz")))
        if self.ausfall:
            return self.ausfall
        aktion = nutzlast["aktion"]
        if aktion == "plaetze":
            return ergebnis({"status": 200, "plaetze": [dict(p) for p in self.plaetze.values()]})
        platz = nutzlast["platz"]
        folge = self.antworten.get((platz, aktion), [200])
        status = folge.pop(0) if len(folge) > 1 else folge[0]
        if status != 200:
            return ergebnis({"status": status, "fehler": "dienst_fehler"})
        p = self.plaetze[platz]
        if aktion == "durchlauf-beenden":
            p["offen"] = False
        else:
            p.update(offen=False, tickets=self.bleibt, kommentare=0, einwilligung=False, platz_token=False)
        return ergebnis({"status": 200, "platz": dict(p)})


def lauf(argv, kern=None, dienst=None, heute=NACH_LABORTAG, tmp_path=None):
    """Führt main() mit Attrappen aus und gibt (Ausgang, Ausgabe, Fehlerausgabe, Kern, Dienst) zurück."""
    kern = kern if kern is not None else Kern()
    dienst = dienst if dienst is not None else Dienst()
    zusatz = ["--umschlag-datei", str(tmp_path / "umschlag-labor.txt")] if tmp_path is not None and "--umschlag-datei" not in argv else []
    aus, fehl = io.StringIO(), io.StringIO()
    with redirect_stdout(aus), redirect_stderr(fehl):
        try:
            code = na.main([*argv, *zusatz], kern=kern, dienst=dienst, heute=lambda: heute, schlafen=lambda s: None)
        except SystemExit as e:
            code = e.code
    return code, aus.getvalue(), fehl.getvalue(), kern, dienst


# ---------- Trockenlauf ----------

def test_ohne_schalter_ist_es_ein_trockenlauf_der_nur_liest(tmp_path):
    code, aus, _, kern, dienst = lauf(["nachlauf"], tmp_path=tmp_path)
    assert code == 0 and "Trockenlauf" in aus
    assert kern.aktionen == ["status"] and kern.aufrufe[0]["ausfuehren"] is False
    assert [a["aktion"] for a in dienst.aufrufe] == ["plaetze"]
    assert all(p["frei"] for p in kern.profile.values()) and all(p["zeilen"] == 40 for p in kern.profile.values())
    assert dienst.plaetze[1]["tickets"] == 5


def test_trockenlauf_zeigt_alle_schritte_die_passieren_wuerden(tmp_path):
    _, aus, _, _, _ = lauf(["nachlauf"], tmp_path=tmp_path)
    for teil in ("Zählung davor", "alle Laborprofile sperren", "16 Plätze", "Durchlauf beenden", "Gesprächsdaten", "Zählung danach",
                 "Erinnerungen", "--ausfuehren"):
        assert teil in aus, teil


def test_trockenlauf_nennt_die_zahlen_die_verschwinden_wuerden(tmp_path):
    _, aus, _, _, _ = lauf(["nachlauf"], tmp_path=tmp_path)
    assert "9 Laborprofile" in aus and "360 Zeilen" in aus and "40 Tickets" in aus and "16 Kommentare" in aus


# ---------- Ausführen ----------

def test_ausfuehren_haelt_die_feste_reihenfolge_ein(tmp_path):
    ablauf: list = []
    code, _, _, kern, dienst = lauf(["nachlauf", "--ausfuehren"], Kern(ablauf=ablauf), Dienst(ablauf=ablauf), tmp_path=tmp_path)
    assert code == 0
    erwartet = [("kern", "status"), ("dienst", "plaetze", None), ("kern", "sperren")]
    erwartet += [("dienst", aktion, n) for n in range(1, 17) for aktion in ("durchlauf-beenden", "daten-loeschen")]
    erwartet += [("kern", "loeschen"), ("kern", "status"), ("dienst", "plaetze", None)]
    assert ablauf == erwartet


def test_ausfuehren_sperrt_und_loescht_alles_mit_den_vorhandenen_wegen(tmp_path):
    code, _, _, kern, dienst = lauf(["nachlauf", "--ausfuehren"], tmp_path=tmp_path)
    assert code == 0
    sperren = next(a for a in kern.aufrufe if a["aktion"] == "sperren")
    loeschen = next(a for a in kern.aufrufe if a["aktion"] == "loeschen")
    assert sperren == {"aktion": "sperren", "schema": "wamo_dev", "alle": True, "ausfuehren": True}
    assert loeschen == {"aktion": "loeschen", "schema": "wamo_dev", "alle": True, "ausfuehren": True}
    assert set(kern.hosts) == {"wamo-sokrates2"}
    assert all(not p["frei"] and p["zeilen"] == 0 for p in kern.profile.values())
    assert all(p["tickets"] == 0 and not p["offen"] and not p["platz_token"] for p in dienst.plaetze.values())


def test_schema_und_host_sind_einstellbar(tmp_path):
    _, _, _, kern, _ = lauf(["nachlauf", "--ausfuehren", "--schema", "wamo_test", "--host", "sokrates2"], tmp_path=tmp_path)
    assert {a["schema"] for a in kern.aufrufe} == {"wamo_test"} and set(kern.hosts) == {"sokrates2"}


def test_jeder_schritt_wird_einzeln_protokolliert(tmp_path):
    _, aus, _, _, _ = lauf(["nachlauf", "--ausfuehren"], tmp_path=tmp_path)
    for nr in range(1, 6):
        assert f"Schritt {nr} von 5" in aus
    assert len(re.findall(r"^\s+Platz \d+: Durchlauf beendet", aus, re.M)) == 16
    assert len(re.findall(r"^\s+Platz \d+: Daten gelöscht", aus, re.M)) == 16


def test_der_nachweis_nennt_zaehlung_davor_und_danach(tmp_path):
    _, aus, _, _, _ = lauf(["nachlauf", "--ausfuehren"], tmp_path=tmp_path)
    davor = next(z for z in aus.splitlines() if z.startswith("Nachweis davor"))
    danach = next(z for z in aus.splitlines() if z.startswith("Nachweis danach"))
    assert "9 Laborprofile, 9 freigeschaltet, 360 Zeilen" in davor and "8 offen, 40 Tickets, 16 Kommentare" in davor
    assert "9 Laborprofile, 0 freigeschaltet, 0 Zeilen" in danach and "0 offen, 0 Tickets, 0 Kommentare" in danach
    assert "Nachweis erbracht" in aus


# ---------- Fehler: Abbruch mit klarer Meldung, nie ein Traceback ----------

def test_fehler_beim_sperren_bricht_ab_und_loescht_nichts(tmp_path):
    kern = Kern(fehler={"sperren": (1, "", "Datenbankfehler: die Verbindung wurde abgelehnt")})
    code, aus, fehl, _, dienst = lauf(["nachlauf", "--ausfuehren"], kern, tmp_path=tmp_path)
    assert code == 1 and "Abbruch" in fehl and "Schritt 2" in fehl and "Datenbankfehler" in fehl
    assert [a["aktion"] for a in dienst.aufrufe] == ["plaetze"] and "loeschen" not in kern.aktionen
    assert "Traceback" not in aus + fehl


def test_fehler_im_dienst_nennt_den_platz_und_loescht_im_kern_nichts(tmp_path):
    dienst = Dienst()
    dienst.antworten[(3, "durchlauf-beenden")] = [403]
    code, _, fehl, kern, _ = lauf(["nachlauf", "--ausfuehren"], dienst=dienst, tmp_path=tmp_path)
    assert code == 1 and "Platz 3" in fehl and "Betreuer-Token" in fehl
    assert dienst.aufrufe[-1] == {"aktion": "durchlauf-beenden", "platz": 3} and "loeschen" not in kern.aktionen
    assert dienst.plaetze[2]["tickets"] == 0 and dienst.plaetze[4]["tickets"] == 5  # Platz 1 und 2 sind fertig, ab Platz 3 ist nichts passiert


def test_loeschen_im_dienst_wird_bei_503_wiederholt(tmp_path):
    dienst = Dienst()
    dienst.antworten[(2, "daten-loeschen")] = [503, 200]
    code, aus, _, _, _ = lauf(["nachlauf", "--ausfuehren"], dienst=dienst, tmp_path=tmp_path)
    assert code == 0 and [a for a in dienst.aufrufe if a == {"aktion": "daten-loeschen", "platz": 2}].__len__() == 2
    assert "neuer Versuch" in aus


def test_dauerhaftes_503_bricht_nach_drei_versuchen_ab(tmp_path):
    dienst = Dienst()
    dienst.antworten[(2, "daten-loeschen")] = [503]
    code, _, fehl, kern, _ = lauf(["nachlauf", "--ausfuehren"], dienst=dienst, tmp_path=tmp_path)
    assert code == 1 and "Platz 2" in fehl and "nicht bereinigt" in fehl and "erneut" in fehl
    assert [a for a in dienst.aufrufe if a == {"aktion": "daten-loeschen", "platz": 2}].__len__() == 3 and "loeschen" not in kern.aktionen


@pytest.mark.parametrize("status,teil", [(404, "nicht bekannt"), (401, "Betreuer-Token"), (500, "Status 500")])
def test_andere_dienstfehler_haben_klare_saetze(tmp_path, status, teil):
    dienst = Dienst()
    dienst.antworten[(1, "durchlauf-beenden")] = [status]
    code, _, fehl, _, _ = lauf(["nachlauf", "--ausfuehren"], dienst=dienst, tmp_path=tmp_path)
    assert code == 1 and teil in fehl


def test_bleibt_nach_dem_loeschen_etwas_im_kern_ist_der_nachweis_nicht_erbracht(tmp_path):
    code, aus, fehl, _, _ = lauf(["nachlauf", "--ausfuehren"], Kern(rest=3), tmp_path=tmp_path)
    assert code == 1 and "Nachweis nicht erbracht" in fehl and "27 Zeilen" in fehl
    assert "Nachweis danach" in aus


def test_bleibt_nach_dem_loeschen_etwas_im_dienst_ist_der_nachweis_nicht_erbracht(tmp_path):
    dienst = Dienst()
    dienst.bleibt = 2
    code, _, fehl, _, _ = lauf(["nachlauf", "--ausfuehren"], dienst=dienst, tmp_path=tmp_path)
    assert code == 1 and "Nachweis nicht erbracht" in fehl and "Tickets" in fehl


def test_scheitert_die_zaehlung_davor_wird_nichts_geaendert(tmp_path):
    dienst = Dienst()
    dienst.ausfall = wl.Ergebnis(255, "", "ssh: connect to host wamo-sokrates2: Connection refused")
    code, _, fehl, kern, _ = lauf(["nachlauf", "--ausfuehren"], dienst=dienst, tmp_path=tmp_path)
    assert code == 1 and "Schritt 1" in fehl and "nichts geändert" in fehl
    assert kern.aktionen == ["status"]


def test_auch_der_trockenlauf_meldet_einen_fehler_beim_lesen(tmp_path):
    kern = Kern(fehler={"status": (1, "", "WAMO_ADMIN_DATABASE_URL fehlt")})
    code, _, fehl, _, _ = lauf(["nachlauf"], kern, tmp_path=tmp_path)
    assert code == 1 and "WAMO_ADMIN_DATABASE_URL" in fehl


def test_ohne_laborprofile_ist_es_ein_fehler_vor_jeder_aenderung(tmp_path):
    kern = Kern(profile=0)
    code, _, fehl, _, dienst = lauf(["nachlauf", "--ausfuehren"], kern, tmp_path=tmp_path)
    assert code == 1 and "keine laborprofile" in fehl.lower() and "Schema" in fehl
    assert kern.aktionen == ["status"] and [a["aktion"] for a in dienst.aufrufe] == ["plaetze"]


def test_fehlt_sudo_ohne_passwort_nennt_die_meldung_den_grund(tmp_path):
    dienst = Dienst()
    dienst.ausfall = wl.Ergebnis(1, "", "sudo: a password is required")
    code, _, fehl, _, _ = lauf(["nachlauf"], dienst=dienst, tmp_path=tmp_path)
    assert code == 1 and "sudo" in fehl and "ohne Passwort" in fehl


def test_ein_unerwarteter_fehler_ist_nie_ein_traceback_und_nennt_keine_einzelheiten(tmp_path):
    kern = Kern(fehler={"sperren": RuntimeError("geheime Einzelheit " + GEHEIM)})
    code, aus, fehl, _, _ = lauf(["nachlauf", "--ausfuehren"], kern, tmp_path=tmp_path)
    assert code == 1 and "RuntimeError" in fehl and "wiederholbar" in fehl
    assert "Traceback" not in aus + fehl and GEHEIM not in aus + fehl and "geheime" not in aus + fehl


def test_strg_c_endet_ohne_traceback(tmp_path):
    kern = Kern(fehler={"sperren": KeyboardInterrupt()})
    code, aus, fehl, _, _ = lauf(["nachlauf", "--ausfuehren"], kern, tmp_path=tmp_path)
    assert code == 130 and "Abgebrochen" in fehl and "Traceback" not in aus + fehl


def test_eine_unlesbare_antwort_des_dienstes_ist_ein_klarer_fehler(tmp_path):
    dienst = Dienst()
    dienst.ausfall = wl.Ergebnis(0, "kein json " + GEHEIM, "")
    code, aus, fehl, _, _ = lauf(["nachlauf"], dienst=dienst, tmp_path=tmp_path)
    assert code == 1 and "unerwartet" in fehl and GEHEIM not in aus + fehl


# ---------- Schutz vor falschem Aufruf ----------

def test_ausfuehren_vor_dem_labortag_ist_gesperrt_ohne_den_ausdruecklichen_schalter(tmp_path):
    code, _, fehl, kern, dienst = lauf(["nachlauf", "--ausfuehren"], heute=VOR_LABORTAG, tmp_path=tmp_path)
    assert code == 2 and "28.11.2026" in fehl and "--vor-dem-labortag" in fehl
    assert kern.aufrufe == [] and dienst.aufrufe == []
    code, _, _, kern, _ = lauf(["nachlauf", "--ausfuehren", "--vor-dem-labortag"], heute=VOR_LABORTAG, tmp_path=tmp_path)
    assert code == 0 and "sperren" in kern.aktionen


def test_der_trockenlauf_geht_auch_vor_dem_labortag(tmp_path):
    assert lauf(["nachlauf"], heute=VOR_LABORTAG, tmp_path=tmp_path)[0] == 0


@pytest.mark.parametrize("argv", [
    ["nachlauf", "--schema", "wamo_x"], ["nachlauf", "--schema", "public"], ["nachlauf", "--host", "-oProxyCommand=calc"],
    ["nachlauf", "--host", "host name"], ["nachlauf", "--host", ""], ["nachlauf", "--host", "a;b"],
])
def test_falsche_angaben_werden_vor_dem_ersten_aufruf_abgelehnt(tmp_path, argv):
    code, _, fehl, kern, dienst = lauf(argv, tmp_path=tmp_path)
    assert code == 2 and fehl.strip() and kern.aufrufe == [] and dienst.aufrufe == []


def test_ohne_befehl_gibt_es_den_aufrufhinweis_von_argparse(tmp_path):
    code, _, fehl, kern, _ = lauf([], tmp_path=tmp_path)
    assert code == 2 and "usage" in fehl.lower() and kern.aufrufe == []


# ---------- Protokolldatei ----------

def test_protokolldatei_enthaelt_jeden_schritt_mit_zeit_und_nie_ein_geheimnis(tmp_path):
    datei = tmp_path / "nachlauf-protokoll.txt"
    code, _, _, _, _ = lauf(["nachlauf", "--ausfuehren", "--protokoll", str(datei)], tmp_path=tmp_path)
    inhalt = datei.read_text(encoding="utf-8")
    zeilen = [z for z in inhalt.splitlines() if z.strip()]
    assert code == 0 and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", zeilen[0])
    assert "Platz 16: Daten gelöscht" in inhalt and "Nachweis danach" in inhalt and len(zeilen) > 35 and GEHEIM not in inhalt


def test_protokolldatei_im_repo_wird_abgelehnt(tmp_path):
    im_repo = WURZEL / "docs" / "nachlauf-protokoll.txt"
    code, _, fehl, kern, _ = lauf(["nachlauf", "--protokoll", str(im_repo)], tmp_path=tmp_path)
    assert code == 2 and "Repo" in fehl and kern.aufrufe == [] and not im_repo.exists()


def test_protokolldatei_in_einem_fehlenden_ordner_wird_abgelehnt(tmp_path):
    code, _, fehl, kern, _ = lauf(["nachlauf", "--protokoll", str(tmp_path / "fehlt" / "p.txt")], tmp_path=tmp_path)
    assert code == 2 and "Ordner" in fehl and kern.aufrufe == []


# ---------- Erinnerungen ----------

def test_erinnerungen_nennen_sicherungen_der_umschlagdatei_nur_mit_namen(tmp_path):
    (tmp_path / "umschlag-labor.txt").write_text("labor-01\tlabor-01@wamo.test\tPASSWORT-GEHEIM-1\tSEEDGEHEIM\n", encoding="utf-8")
    (tmp_path / "umschlag-labor.txt.sicherung-20261125-101010").write_text("PASSWORT-GEHEIM-2", encoding="utf-8")
    (tmp_path / "umschlag-labor.txt.neu").write_text("SEEDGEHEIM-3", encoding="utf-8")
    (tmp_path / "andere-datei.txt").write_text("nichts", encoding="utf-8")
    _, aus, _, _, _ = lauf(["nachlauf"], tmp_path=tmp_path)
    assert "umschlag-labor.txt.sicherung-20261125-101010" in aus and "umschlag-labor.txt.neu" in aus and "andere-datei" not in aus
    assert "GEHEIM" not in aus


def test_erinnerungen_nennen_platz_skript_loeschlauf_und_frist_auch_beim_ausfuehren(tmp_path):
    for argv in (["nachlauf"], ["nachlauf", "--ausfuehren"]):
        _, aus, _, _, _ = lauf(argv, tmp_path=tmp_path)
        assert "wamo_labor_platz.py rueckweg --profil-loeschen" in aus
        assert "wamo-labor-loeschlauf.sh --bestaetigung LABOR-LOESCHEN" in aus and "11.12.2026" in aus
        assert "Umschlag-Datei" in aus


def test_erinnerungen_kommen_auch_ohne_umschlagordner(tmp_path):
    _, aus, _, _, _ = lauf(["nachlauf"], tmp_path=tmp_path / "gibt-es-nicht")
    assert "keine dateien gefunden" in aus.lower()


# ---------- Lesen der Zählung ----------

def test_kern_stand_summiert_zeilen_und_zaehlt_freigeschaltete():
    stand = na.kern_stand_lesen("labor-01 | platz | gesperrt | 4 Zeilen\nlabor-02 | platz | freigeschaltet | 6 Zeilen\n"
                                "labor-schau | schaufenster | gesperrt | 0 Zeilen\n")
    assert (stand.profile, stand.freigeschaltet, stand.zeilen) == (3, 1, 10)


def test_kern_stand_ohne_laborprofile_ist_leer():
    assert na.kern_stand_lesen("Keine Laborprofile.").profile == 0


@pytest.mark.parametrize("text", ["", "irgendwas", "labor-01 | platz | gesperrt | viele Zeilen", "labor-99 | platz | gesperrt | 1 Zeilen",
                                  "labor-01 | platz | gesperrt | 1 Zeilen\nTEXT-DES-GESPRAECHS"])
def test_kern_stand_lehnt_unerwartetes_ab_ohne_es_auszugeben(text):
    with pytest.raises(na.NachlaufFehler) as e:
        na.kern_stand_lesen(text)
    assert "TEXT-DES-GESPRAECHS" not in str(e.value)


def test_dienst_stand_summiert_und_verlangt_16_plaetze():
    plaetze = [{"platz": n, "offen": n == 1, "tickets": 2, "kommentare": 1, "einwilligung": n == 1, "platz_token": n <= 2} for n in range(1, 17)]
    stand = na.dienst_stand_lesen(plaetze)
    assert (stand.plaetze, stand.offen, stand.tickets, stand.kommentare, stand.einwilligungen, stand.tokens) == (16, 1, 32, 16, 1, 2)
    for kaputt in (plaetze[:15], [], "text", [{"platz": 1}], [dict(p, tickets="viele") for p in plaetze], [dict(p, tickets=-1) for p in plaetze]):
        with pytest.raises(na.NachlaufFehler):
            na.dienst_stand_lesen(kaputt)


# ---------- Verdrahtung mit dem vorhandenen Kern-Weg ----------

def test_standardwege_sind_der_vorhandene_kern_weg_und_der_dienstweg():
    voreinstellung = na.main.__kwdefaults__
    assert voreinstellung["kern"] is wl.run_remote and voreinstellung["dienst"] is na.dienst_remote


class EchtesKernProgramm:
    """Führt das echte Serverprogramm von wamo_labor.py in-process aus (mit Attrappe für psql): So fällt jede Abweichung der Nutzlasten auf."""

    UID = "00000000-0000-4000-8000-00000000b001"

    def __init__(self):
        self.namen: list[str] = []
        self.frei = True
        self.ns: dict = {"__name__": "remote_test"}
        exec(compile(wl.remote_programm(), "<remote>", "exec"), self.ns)  # noqa: S102 (das Programm, das auf dem Server läuft)

    def psql(self, sql: str, **vars):
        name = next(n for n, s in wl.SQL.items() if s == sql)
        self.namen.append(name)
        if name == "status":
            zeilen = 0 if "loeschen" in self.namen else 7
            return f"labor-01 | platz | {'freigeschaltet' if self.frei else 'gesperrt'} | {zeilen} Zeilen"
        if name == "sperren_alle":
            self.frei = False
            return "1"
        if name == "ids_alle":
            return f"labor-01|{self.UID}"
        if name == "loeschen":
            return '{"turns": 7}'
        if name == "zaehlen":
            return '{"turns": 0}'
        raise AssertionError(name)

    def __call__(self, host: str, nutzlast: dict) -> wl.Ergebnis:
        aus, fehl = io.StringIO(), io.StringIO()
        with redirect_stdout(aus), redirect_stderr(fehl):
            code = self.ns["verarbeite"](nutzlast, self.psql, lambda email, passwort: self.UID, lambda sekunden: None)
        return wl.Ergebnis(code, aus.getvalue(), fehl.getvalue())


def test_die_nutzlasten_passen_zum_echten_serverprogramm_von_wamo_labor(tmp_path):
    echt = EchtesKernProgramm()
    code, aus, fehl, _, _ = lauf(["nachlauf", "--ausfuehren"], echt, tmp_path=tmp_path)
    assert code == 0, fehl
    assert echt.namen == ["status", "sperren_alle", "ids_alle", "loeschen", "zaehlen", "status"]
    assert "Nachweis erbracht" in aus


# ---------- ssh-Schicht zum Dienst ----------

class Lauf:
    """Attrappe für subprocess.run: merkt sich Befehl und Eingabe."""

    def __init__(self, stdout: str = '{"status": 200, "plaetze": []}', stderr: str = "", returncode: int = 0, ausnahme: BaseException | None = None):
        self.aufrufe: list[tuple[list[str], dict]] = []
        self.antwort = types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
        self.ausnahme = ausnahme

    def __call__(self, cmd, **kw):
        self.aufrufe.append((list(cmd), kw))
        if self.ausnahme:
            raise self.ausnahme
        return self.antwort


def test_dienst_remote_startet_nur_ssh_mit_sudo_und_gibt_das_programm_als_text(monkeypatch):
    lauf_ = Lauf()
    monkeypatch.setattr(na.subprocess, "run", lauf_)
    e = na.dienst_remote("wamo-sokrates2", {"aktion": "daten-loeschen", "platz": 5})
    cmd, kw = lauf_.aufrufe[0]
    assert cmd[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"] and cmd[5] == "wamo-sokrates2" and len(cmd) == 7
    assert cmd[6].startswith("sudo -n python3 -c ") and kw["input"] == json.dumps({"aktion": "daten-loeschen", "platz": 5})
    programm = base64.b64decode(re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", cmd[6]).group(1)).decode("utf-8")
    assert programm == na.REMOTE_DIENST
    assert (e.code, json.loads(e.stdout)["status"]) == (0, 200)


def test_dienst_remote_uebergibt_nie_ein_geheimnis(monkeypatch):
    lauf_ = Lauf()
    monkeypatch.setattr(na.subprocess, "run", lauf_)
    monkeypatch.setenv("WAMO_LABOR_TOKEN", GEHEIM)
    na.dienst_remote("wamo-sokrates2", {"aktion": "plaetze"})
    cmd, kw = lauf_.aufrufe[0]
    assert GEHEIM not in " ".join(cmd) and GEHEIM not in kw["input"] and "env" not in kw


@pytest.mark.parametrize("ausnahme,teil", [(subprocess.TimeoutExpired("ssh", 180), "Zeitüberschreitung"), (FileNotFoundError("ssh"), "ssh")])
def test_dienst_remote_meldet_zeitueberschreitung_und_fehlendes_ssh_ohne_traceback(monkeypatch, ausnahme, teil):
    monkeypatch.setattr(na.subprocess, "run", Lauf(ausnahme=ausnahme))
    e = na.dienst_remote("wamo-sokrates2", {"aktion": "plaetze"})
    assert e.code != 0 and teil in e.stderr


def test_dienst_remote_schneidet_lange_fehlertexte_nicht_ab_aber_kuerzt_die_ausgabe(monkeypatch):
    monkeypatch.setattr(na.subprocess, "run", Lauf(stdout="x" * 200000, returncode=0))
    assert len(na.dienst_remote("wamo-sokrates2", {"aktion": "plaetze"}).stdout) <= 70000


# ---------- Das Programm auf dem Server (REMOTE_DIENST) in-process ----------

def remote_namensraum() -> dict:
    ns: dict = {"__name__": "remote_dienst_test"}
    exec(compile(na.REMOTE_DIENST, "<remote-dienst>", "exec"), ns)  # noqa: S102 (das Programm, das auf dem Server läuft)
    return ns


class Antwort:
    def __init__(self, status: int, daten: object):
        self.status, self._roh = status, json.dumps(daten).encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        return self._roh if n < 0 else self._roh[:n]


class Oeffner:
    """Attrappe für das Öffnen einer Anfrage: merkt sich Anfrage und Frist, antwortet mit einer Antwort oder wirft einen Fehler."""

    def __init__(self, antwort=None, fehler: BaseException | None = None):
        self.antwort, self.fehler, self.anfragen = antwort, fehler, []

    def __call__(self, anfrage, frist):
        self.anfragen.append((anfrage, frist))
        if self.fehler:
            raise self.fehler
        return self.antwort


def serverlauf(nutzlast: dict, tmp_path: Path, oeffner: Oeffner, token: str | None = GEHEIM):
    ns = remote_namensraum()
    datei = tmp_path / "betreuer-token"
    if token is not None:
        datei.write_text(token + "\n", encoding="utf-8")
    aus, fehl = io.StringIO(), io.StringIO()
    with redirect_stdout(aus), redirect_stderr(fehl):
        code = ns["verarbeite"](nutzlast, str(datei), oeffner)
    return code, aus.getvalue(), fehl.getvalue()


def vollstaendige_plaetze() -> dict:
    return {"plaetze": [{"platz": n, "durchlauf_id": "5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41aa", "offen": n == 1, "einwilligung_version": 1 if n == 1 else None,
                         "einwilligung_zeit": "2026-11-28T09:30:00Z", "tickets": 6, "kommentare": 2, "hat_platz_token": n == 1, "reserviert": n > 14}
                        for n in range(1, 17)]}


def test_server_plaetze_liest_mit_dem_betreuer_token_und_gibt_nur_zaehlungen_zurueck(tmp_path):
    oeffner = Oeffner(Antwort(200, vollstaendige_plaetze()))
    code, aus, fehl = serverlauf({"aktion": "plaetze"}, tmp_path, oeffner)
    anfrage, frist = oeffner.anfragen[0]
    assert code == 0 and fehl == ""
    assert anfrage.get_method() == "GET" and anfrage.full_url == "http://127.0.0.1:18765/api/betreuer/plaetze" and frist > 0
    assert anfrage.get_header("Authorization") == "Bearer " + GEHEIM
    daten = json.loads(aus)
    assert daten["status"] == 200 and len(daten["plaetze"]) == 16
    assert daten["plaetze"][0] == {"platz": 1, "offen": True, "tickets": 6, "kommentare": 2, "einwilligung": True, "platz_token": True}
    assert "durchlauf_id" not in aus and "einwilligung_zeit" not in aus and GEHEIM not in aus


@pytest.mark.parametrize("aktion", ["durchlauf-beenden", "daten-loeschen"])
def test_server_aktion_geht_als_post_mit_leerem_objekt_an_genau_einen_pfad(tmp_path, aktion):
    zustand = {"platz": 5, "durchlauf_id": "x", "offen": False, "einwilligung_version": None, "einwilligung_zeit": None, "tickets": 0,
               "kommentare": 0, "hat_platz_token": False, "reserviert": False}
    oeffner = Oeffner(Antwort(200, zustand))
    code, aus, _ = serverlauf({"aktion": aktion, "platz": 5}, tmp_path, oeffner)
    anfrage, _ = oeffner.anfragen[0]
    assert code == 0 and anfrage.get_method() == "POST" and anfrage.data == b"{}"
    assert anfrage.full_url == f"http://127.0.0.1:18765/api/betreuer/plaetze/5/{aktion}"
    assert json.loads(aus) == {"status": 200, "platz": {"platz": 5, "offen": False, "tickets": 0, "kommentare": 0, "einwilligung": False, "platz_token": False}}


@pytest.mark.parametrize("nutzlast", [
    {"aktion": "neuer-durchlauf", "platz": 3}, {"aktion": "platz-token", "platz": 3}, {"aktion": "zuruecksetzen", "platz": 3},
    {"aktion": "daten-loeschen", "platz": 0}, {"aktion": "daten-loeschen", "platz": 17}, {"aktion": "daten-loeschen", "platz": True},
    {"aktion": "daten-loeschen", "platz": "3"}, {"aktion": "daten-loeschen", "platz": "3/../4"}, {"aktion": "daten-loeschen"},
    {"aktion": "durchlauf-beenden", "platz": None}, {"aktion": ""}, {}, {"aktion": "plaetze/../kern"},
])
def test_server_fuehrt_nur_die_festen_aufgaben_aus(tmp_path, nutzlast):
    oeffner = Oeffner(Antwort(200, {}))
    code, aus, fehl = serverlauf(nutzlast, tmp_path, oeffner)
    assert code == 1 and aus == "" and "Ungueltige Aufgabe" in fehl and oeffner.anfragen == []


def test_server_token_fehlt_oder_hat_falsche_form_ohne_den_wert_zu_nennen(tmp_path):
    code, _, fehl = serverlauf({"aktion": "plaetze"}, tmp_path, Oeffner(Antwort(200, {})), token=None)
    assert code == 1 and "nicht lesbar" in fehl and "sudo" in fehl
    code, _, fehl = serverlauf({"aktion": "plaetze"}, tmp_path, Oeffner(Antwort(200, {})), token="zu kurz mit Leerzeichen")
    assert code == 1 and "Form" in fehl and "zu kurz" not in fehl


def test_server_gibt_bei_fehlerstatus_nur_den_fehlercode_weiter_nie_den_text(tmp_path):
    fehler = urllib.error.HTTPError("http://x", 403, "x", {}, io.BytesIO(json.dumps({"fehler": "zugriff_verweigert", "text": "geheim " + GEHEIM}).encode()))
    code, aus, fehl = serverlauf({"aktion": "plaetze"}, tmp_path, Oeffner(fehler=fehler))
    assert code == 0 and json.loads(aus) == {"status": 403, "fehler": "zugriff_verweigert"} and GEHEIM not in aus + fehl


def test_server_ignoriert_ungueltige_fehlercodes(tmp_path):
    fehler = urllib.error.HTTPError("http://x", 500, "x", {}, io.BytesIO(json.dumps({"fehler": "<script>" + GEHEIM}).encode()))
    _, aus, _ = serverlauf({"aktion": "plaetze"}, tmp_path, Oeffner(fehler=fehler))
    assert json.loads(aus) == {"status": 500} and GEHEIM not in aus


def test_server_antwort_ohne_json_ist_nur_der_status(tmp_path):
    fehler = urllib.error.HTTPError("http://x", 503, "x", {}, io.BytesIO(b"Service Unavailable"))
    code, aus, _ = serverlauf({"aktion": "daten-loeschen", "platz": 1}, tmp_path, Oeffner(fehler=fehler))
    assert code == 0 and json.loads(aus) == {"status": 503}


def test_server_unerwartete_antwortform_ist_ein_fehler(tmp_path):
    code, _, fehl = serverlauf({"aktion": "plaetze"}, tmp_path, Oeffner(Antwort(200, {"plaetze": [{"platz": 1}]})))
    assert code == 1 and "unerwartet" in fehl


def test_server_dienst_nicht_erreichbar_ohne_geheimnis(tmp_path):
    fehler = urllib.error.URLError("Connection refused " + GEHEIM)
    code, aus, fehl = serverlauf({"aktion": "plaetze"}, tmp_path, Oeffner(fehler=fehler))
    assert code == 1 and "antwortet nicht" in fehl and GEHEIM not in aus + fehl


def test_server_folgt_keiner_umleitung_und_sendet_den_bearer_nur_an_loopback(tmp_path):
    """Echter Server auf Loopback: Der Bearer kommt an, eine Umleitung wird nie verfolgt (ein zweiter Server bekäme sonst den Bearer)."""
    empfangen: list[str] = []

    class Ziel(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            empfangen.append("ziel:" + str(self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    class Quelle(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            empfangen.append("quelle:" + str(self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{ziel.server_address[1]}/ziel")
            self.end_headers()

        def log_message(self, *a):
            pass

    ziel = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ziel)
    quelle = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quelle)
    for server in (ziel, quelle):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ns = remote_namensraum()
        ns["BASIS"] = f"http://127.0.0.1:{quelle.server_address[1]}"
        datei = tmp_path / "betreuer-token"
        datei.write_text(GEHEIM, encoding="utf-8")
        aus = io.StringIO()
        with redirect_stdout(aus):
            code = ns["verarbeite"]({"aktion": "plaetze"}, str(datei))
    finally:
        for server in (ziel, quelle):
            server.shutdown()
            server.server_close()
    assert code == 0 and json.loads(aus.getvalue()) == {"status": 302}
    assert empfangen == ["quelle:Bearer " + GEHEIM]


def test_server_haupt_liest_die_aufgabe_von_stdin_und_meldet_unlesbares_ohne_traceback(monkeypatch):
    ns = remote_namensraum()
    monkeypatch.setattr(ns["sys"], "stdin", io.StringIO("kein json"))
    fehl = io.StringIO()
    with redirect_stderr(fehl):
        assert ns["haupt"]() == 1
    assert "Traceback" not in fehl.getvalue() and fehl.getvalue().strip()


# ---------- Regeln des Projekts ----------

def test_quelle_hat_keine_gedankenstriche_und_bleibt_unter_den_grenzen():
    text = QUELLE.read_text(encoding="utf-8")
    assert "–" not in text and "—" not in text, "kein Gedankenstrich im Text"
    assert len(text.splitlines()) < 800
    for knoten in ast.walk(ast.parse(text)):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert knoten.end_lineno - knoten.lineno + 1 <= 50, f"{knoten.name} ist länger als 50 Zeilen"
