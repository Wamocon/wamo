"""Tests für scripts/wamo_labor_last_lauf.py und wamo_labor_last_vorbereitung.py (Ablauf des Lasttests gegen Attrappen).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q test_wamo_labor_last_lauf.py

Kein Browser, kein ssh, kein Dienst, keine echte Umschlag-Datei: Treiber, Server, Dienst, Kern und Umschlag sind Attrappen
(scripts/wamo_labor_last_attrappe.py), die Zeit läuft 2000-fach beschleunigt. Eine Falle bricht jeden Test ab, der einen Prozess starten würde.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor as wl  # noqa: E402
import wamo_labor_faktoren_totp as tt  # noqa: E402
import wamo_labor_last_attrappe as at  # noqa: E402
import wamo_labor_last_audio as au  # noqa: E402
import wamo_labor_last_auswertung as aw  # noqa: E402
import wamo_labor_last_lauf as la  # noqa: E402
import wamo_labor_last_plan as pl  # noqa: E402
import wamo_labor_last_umgebung as um  # noqa: E402
import wamo_labor_last_vorbereitung as vb  # noqa: E402

GIB = at.GIB


@pytest.fixture(autouse=True)
def keine_echten_aufrufe(monkeypatch):
    def falle(*args, **kw):
        raise AssertionError("Der Test hat versucht, einen Prozess zu starten")
    monkeypatch.setattr(subprocess, "Popen", falle)
    monkeypatch.setattr(subprocess, "run", falle)


@pytest.fixture
def clips(tmp_path) -> Path:
    ordner = tmp_path / "clips"
    ordner.mkdir()
    for i, name in enumerate(sorted(au.SPRECHPROBEN)):
        au.schreibe_wav(ordner / f"{name}.wav", 8000, [0.4 * math.sin(2 * math.pi * (150 + 20 * i) * k / 8000) for k in range(4000 + 200 * i)])
    return ordner


class Welt:
    """Alles, was ein Lauf braucht, mit Attrappen. Die Felder lassen sich vor dem Start ändern."""

    def __init__(self, tmp_path, clips, sitzungen=(1, 2), minuten=1, snr=(), uhr=None, **ein):
        self.uhr = uhr or at.Zeitraffer()
        self.protokoll = at.Protokoll()
        self.verhalten = at.Verhalten()
        self.treiber: dict[int, at.TreiberAttrappe] = {}
        self.ausgabe: list[str] = []
        self.gespeichert: list[dict] = []
        self.server_einstellung = at.ServerEinstellung()
        self.server = at.ServerAttrappe(self.uhr, self.protokoll, self.server_einstellung)
        self.dienst = at.DienstAttrappe(self.protokoll)
        self.kern = at.KernAttrappe(self.protokoll)
        self.umschlag = at.UmschlagAttrappe()
        self.tempordner: list[Path] = []
        self.tmp_path = tmp_path
        stufen = pl.stufen_bauen(list(sitzungen), minuten, list(snr))
        self.ein = um.Einstellungen(lauf="last-test", seed=7, stufen=stufen, clips_ordner=clips, ausgabe_ordner=tmp_path / "aus", **ein)

    def aktive(self) -> int:
        return sum(1 for t in self.treiber.values() if t.gespraech)

    def fabrik(self, sitzung: int):
        t = at.TreiberAttrappe(sitzung, self.uhr, self.protokoll, self.verhalten, self.aktive)
        self.treiber[sitzung] = t
        return t

    def tempfabrik(self) -> Path:
        ordner = self.tmp_path / f"temp{len(self.tempordner)}"
        ordner.mkdir()
        self.tempordner.append(ordner)
        return ordner

    def umgebung(self) -> um.Umgebung:
        return um.Umgebung(uhr=self.uhr, treiber_fabrik=self.fabrik, server=self.server, dienst=self.dienst, kern=self.kern, umschlag=self.umschlag,
                           tempordner_fabrik=self.tempfabrik, ausgabe=self.ausgabe.append, speichern=self.gespeichert.append)

    def lauf(self) -> dict:
        return la.Lauf(self.ein, self.umgebung()).ausfuehren()


@pytest.fixture
def welt(tmp_path, clips):
    return Welt(tmp_path, clips)


def aufraeumen_namen(e: dict) -> list[str]:
    return [a["schritt"] for a in e["aufraeumen"]]


# ---------- der gesunde Lauf ----------

def test_gesunder_lauf_durchlaeuft_alle_stufen_und_raeumt_auf(welt):
    e = welt.lauf()
    assert e["abbruchgrund"] == "" and e["ausfuehrung"] == "echt" and e["seed"] == 7
    assert [s["sitzungen"] for s in e["stufen"]] == [1, 2]
    assert all(s["schritte"] > 0 and s["erster_ton_ms"]["n"] > 0 for s in e["stufen"])
    assert all(a["ok"] for a in e["aufraeumen"]), e["aufraeumen"]
    assert e["gesamt"] in (aw.BESTANDEN, aw.OFFEN)


def test_ablauf_vorpruefung_vor_jeder_aenderung_dann_vorbereiten_dann_aufraeumen_in_der_richtigen_reihenfolge(welt):
    welt.lauf()
    p = welt.protokoll
    erste_aenderung = min(p.index("kern", "aktivieren"), p.index("dienst", "durchlauf_oeffnen"))
    assert p.index("kern", "status") < erste_aenderung and p.index("dienst", "plaetze") < erste_aenderung
    assert p.index("kern", "aktivieren") < p.index("dienst", "durchlauf_oeffnen") < p.index("treiber0", "starten") < p.index("treiber0", "anmelden")
    assert p.index("treiber0", "anmelden") < p.index("treiber0", "gespraech_starten") < p.index("treiber0", "schritt")
    kette = [p.index("treiber0", "gespraech_beenden"), p.index("treiber0", "schliessen"), p.index("kern", "sperren"),
             p.index("dienst", "durchlauf_beenden"), p.index("dienst", "daten_loeschen")]
    assert kette == sorted(kette)
    assert kette[0] > p.index("treiber0", "schritt")


def test_schreibende_aktionen_nur_fuer_die_gewaehlten_profile_und_plaetze(welt):
    welt.lauf()
    plaetze = {e[2] for e in welt.protokoll.eintraege if e[0] == "dienst" and e[1] in ("durchlauf_oeffnen", "durchlauf_beenden", "daten_loeschen")}
    assert plaetze == {1, 2}
    assert welt.kern.aktiv == set()
    assert {a.get("platz") for a in welt.kern.aufrufe if a["aktion"] in ("aktivieren", "sperren")} == {"1", "2"}


def test_browser_starten_einmal_fuer_die_groesste_stufe_und_jede_stufe_nutzt_die_ersten(welt):
    welt.lauf()
    assert sorted(welt.treiber) == [0, 1]
    assert welt.protokoll.namen("treiber0").count("starten") == 1 and welt.protokoll.namen("treiber0").count("anmelden") == 1
    assert welt.protokoll.namen("treiber0").count("gespraech_starten") == 2  # eine Unterhaltung je Stufe
    assert welt.protokoll.namen("treiber1").count("gespraech_starten") == 1  # nur in der Stufe mit 2 Sitzungen
    assert welt.treiber[0].geschlossen and welt.treiber[1].geschlossen


def test_einwilligung_version_geht_an_den_dienst(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(1,), einwilligung=3)
    w.lauf()
    assert w.dienst.versionen == {1: 3}


def test_temp_ordner_ist_nach_dem_lauf_weg_auch_mit_murmeln(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(1,), snr=(10,))
    e = w.lauf()
    assert w.tempordner and not any(p.exists() for p in w.tempordner)
    assert "Temp-Dateien gelöscht" in aufraeumen_namen(e)
    assert "murmeln" in w.protokoll.namen("treiber0")


def test_murmeln_stufe_setzt_die_verstaerkung_und_stellt_sie_wieder_ab(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2,), snr=(20, 5))
    e = w.lauf()
    m = [x[2] for x in w.protokoll.eintraege if x[0] == "treiber0" and x[1] == "murmeln"]
    assert [x for x in m if x is not None] and m.count(None) >= 2
    gains = [x for x in m if x is not None]
    assert len(gains) == 2 and gains[1] > gains[0]  # 5 dB Abstand braucht lautere Murmeln als 20 dB
    assert [s["snr_db"] for s in e["stufen"]] == [None, 20, 5]


def test_keine_schritte_nach_dem_ende_der_stufe_und_pausen_im_bereich(welt):
    e = welt.lauf()
    schritte = [s for liste in e["schritte"].values() for s in liste]
    assert schritte and all(s["beginn_s"] <= 60 + 1 for s in schritte)  # Stufe von einer Minute


def test_ergebnis_ist_json_faehig_hat_proben_und_der_bericht_entsteht(welt):
    e = welt.lauf()
    text = json.dumps(e)
    assert all(len(p) >= 2 for p in e["proben"].values())
    bericht = aw.bericht_text(e)
    assert "# Lasttest last-test" in bericht
    assert "Pw-labor" not in text and "Pw-labor" not in bericht
    assert welt.gespeichert and welt.gespeichert[-1]["lauf"] == "last-test"


def test_gleicher_seed_gleiche_folge_der_karten_und_clips(tmp_path, clips):
    folgen = []
    for n in range(2):
        w = Welt(tmp_path / f"w{n}", clips, sitzungen=(1,))
        (tmp_path / f"w{n}").mkdir()
        w.lauf()
        folgen.append([x[2] for x in w.protokoll.eintraege if x[0] == "treiber0" and x[1] == "schritt"][:6])
    assert folgen[0][:3] == folgen[1][:3] == ["A1", "J1", "G3"]


# ---------- Geheimnisse ----------

def test_passwort_und_code_stehen_nirgends_ausser_im_treiber(welt):
    e = welt.lauf()
    geheim = ["Pw-labor-01-geheim", at.SEED_B32]
    alles = json.dumps(e) + "\n".join(welt.ausgabe) + aw.bericht_text(e) + json.dumps([x for x in welt.protokoll.eintraege], default=str)
    for g in geheim:
        assert g not in alles
    code = tt.totp_code(at.SEED_B32, welt.uhr.jetzt())
    assert code not in "\n".join(welt.ausgabe)
    assert len(welt.treiber[0].geheim) == 2 and welt.treiber[0].geheim[0] == "Pw-labor-01-geheim"


def test_der_code_gehoert_zum_seed_und_zur_serveruhr(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(1,))
    w.server_einstellung.versatz_s = 100.0
    gesehen = {}
    alt = at.TreiberAttrappe.anmelden

    def anmelden(self, email, passwort, code):
        gesehen["code"], gesehen["zeit"] = code, w.uhr.jetzt()
        return alt(self, email, passwort, code)

    at.TreiberAttrappe.anmelden = anmelden
    try:
        w.lauf()
    finally:
        at.TreiberAttrappe.anmelden = alt
    ziele = {tt.totp_code(at.SEED_B32, gesehen["zeit"] + 100.0 + d) for d in range(-120, 5, 15)}
    assert gesehen["code"] in ziele and len(gesehen["code"]) == 6


# ---------- Vorprüfung ----------

def test_belegter_platz_stoppt_vor_jeder_aenderung(welt):
    import wamo_labor_last_server as sv
    welt.dienst.plaetze_zustand[2] = sv.DienstPlatz(2, True, 4, 0, True, True)
    e = welt.lauf()
    assert "Platz 2" in e["abbruchgrund"] and e["stufen"] == [] and e["aufraeumen"] == []
    assert "aktivieren" not in welt.protokoll.namen("kern") and "durchlauf_oeffnen" not in welt.protokoll.namen("dienst")


def test_freigeschaltetes_profil_stoppt_vor_jeder_aenderung(welt):
    welt.kern.aktiv.add("labor-02")
    e = welt.lauf()
    assert "labor-02" in e["abbruchgrund"] and "aktivieren" not in welt.protokoll.namen("kern")


def test_fehlendes_profil_im_kern_stoppt(tmp_path, clips):
    w = Welt(tmp_path, clips)
    w.kern.vorhanden = 1
    e = w.lauf()
    assert "labor-02" in e["abbruchgrund"] and w.protokoll.namen("dienst").count("durchlauf_oeffnen") == 0


@pytest.mark.parametrize("kw,teil", [({"ohne_seed": {"labor-01"}}, "Seed"), ({"fehlend": {"labor-02"}}, "labor-02")])
def test_umschlag_ohne_seed_oder_zeile_stoppt(tmp_path, clips, kw, teil):
    w = Welt(tmp_path, clips)
    w.umschlag = at.UmschlagAttrappe(**kw)
    e = w.lauf()
    assert teil in e["abbruchgrund"] and "aktivieren" not in w.protokoll.namen("kern")


def test_kern_nicht_aktiv_oder_speicher_schon_zu_hoch_stoppt(tmp_path, clips):
    w = Welt(tmp_path, clips)
    w.server_einstellung.aktiv = False
    assert "wamo-core@b" in w.lauf()["abbruchgrund"]
    w = Welt(tmp_path / "x", clips)
    (tmp_path / "x").mkdir()
    w.server_einstellung.speicher = lambda t: 4 * GIB
    assert "Speicher" in w.lauf()["abbruchgrund"]


def test_unzulaessige_adresse_stoppt(tmp_path, clips):
    w = Welt(tmp_path, clips, url="https://example.org")
    e = w.lauf()
    assert "Adresse" in e["abbruchgrund"] and w.protokoll.eintraege == []


def test_fehlende_sprechproben_stoppen_vor_dem_server(tmp_path):
    w = Welt(tmp_path, tmp_path / "gibt-es-nicht")
    e = w.lauf()
    assert "Sprechproben" in e["abbruchgrund"] and w.protokoll.eintraege == []


# ---------- Abbruch bei Grenzen ----------

def test_429_im_journal_bricht_ab_und_raeumt_auf(tmp_path, clips):
    w = Welt(tmp_path, clips)
    w.server_einstellung.journal = lambda seit, bis: {"r429": 1} if bis > w.uhr._start + 20 else {}
    e = w.lauf()
    assert "429" in e["abbruchgrund"] and len(e["stufen"]) == 1
    assert next(k for k in e["kriterien"] if k["name"] == "Kein 429")["status"] == aw.DURCHGEFALLEN
    assert e["gesamt"] == aw.DURCHGEFALLEN and all(a["ok"] for a in e["aufraeumen"])
    assert "daten_loeschen" in w.protokoll.namen("dienst")


def test_429_im_browser_bricht_ab(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2, 4))
    w.verhalten.r429_ab = {1: 2}
    e = w.lauf()
    assert "429" in e["abbruchgrund"] and len(e["stufen"]) == 1
    assert e["stufen"][0]["browser_429"] >= 1


def test_mehr_als_zwei_abbrueche_beenden_den_lauf_zwei_nicht(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(4, 8))
    w.verhalten.schritt_abbruch_ab = {0: 2, 1: 2}
    e = w.lauf()
    assert e["abbruchgrund"] == "" and len(e["stufen"]) == 2  # zwei Abbrüche je Stufe: Grenze nicht überschritten
    assert e["stufen"][0]["abbrueche"] == 2
    assert next(k for k in e["kriterien"] if "Abbrüche" in k["name"])["status"] == aw.DURCHGEFALLEN
    w2 = Welt(tmp_path / "z", clips, sitzungen=(4, 8))
    (tmp_path / "z").mkdir()
    w2.verhalten.schritt_abbruch_ab = {0: 2, 1: 2, 2: 2}
    e2 = w2.lauf()
    assert "Abbrüche" in e2["abbruchgrund"] and len(e2["stufen"]) == 1


def test_kernspeicher_ueber_3_gib_bricht_ab(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2, 4))
    w.server_einstellung.speicher = lambda t: int(1 * GIB) if t < w.uhr._start + 30 else int(3.2 * GIB)
    e = w.lauf()
    assert "Speicher" in e["abbruchgrund"] and len(e["stufen"]) == 1


def test_ausfall_der_messung_am_server_beendet_den_lauf_nach_drei_fehlern(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(1, 2))
    w.server_einstellung.fehler_ab = 3
    e = w.lauf()
    assert "Messung" in e["abbruchgrund"] and all(a["ok"] for a in e["aufraeumen"])


def test_abbruch_von_aussen_raeumt_auf_und_meldet_es(tmp_path, clips):
    class Uhr(at.Zeitraffer):
        aufrufe = 0

        def schlafen(self, s):
            if threading.current_thread() is threading.main_thread():
                Uhr.aufrufe += 1
                if Uhr.aufrufe == 12:
                    raise KeyboardInterrupt
            super().schlafen(s)

    w = Welt(tmp_path, clips, sitzungen=(2,), uhr=Uhr())
    e = w.lauf()
    assert "Strg+C" in e["abbruchgrund"] and e["von_aussen"] is True
    assert w.treiber[0].geschlossen and "daten_loeschen" in w.protokoll.namen("dienst") and w.kern.aktiv == set()


# ---------- Fehler in der Vorbereitung und beim Aufräumen ----------

def test_scheitert_ein_browser_wird_alles_zurueckgenommen(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2,))
    w.verhalten.anmelde_fehler = {1: "Anmeldung fehlgeschlagen: Zeitüberschreitung"}
    e = w.lauf()
    assert "Anmeldung" in e["abbruchgrund"] and e["stufen"] == []
    assert w.kern.aktiv == set() and w.dienst.plaetze_zustand[1].frei and w.dienst.plaetze_zustand[2].frei
    assert w.treiber[0].geschlossen and w.treiber[1].geschlossen


def test_scheitert_das_oeffnen_eines_durchlaufs_werden_die_profile_gesperrt(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2,))
    w.dienst.fehler = {"durchlauf_oeffnen": "Der Dienst lehnte neuer-durchlauf ab (HTTP 409, version_zu_klein)."}
    e = w.lauf()
    assert "neuer-durchlauf" in e["abbruchgrund"] and w.kern.aktiv == set()
    assert "sperren" in w.protokoll.namen("kern")


def test_scheitert_das_aktivieren_wird_trotzdem_gesperrt(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2,))
    w.kern.fehler = {"aktivieren": "Datenbankfehler"}
    e = w.lauf()
    assert e["abbruchgrund"] and "sperren" in w.protokoll.namen("kern")


def test_ein_scheiternder_aufraeumschritt_haelt_die_anderen_nicht_auf(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2,))
    w.dienst.fehler = {"durchlauf_beenden": "Der Weg zum Server meldete einen Fehler"}
    w.verhalten.schliessen_fehler = {0: "hängt"}
    e = w.lauf()
    schritte = {a["schritt"]: a for a in e["aufraeumen"]}
    assert schritte["Durchläufe beendet"]["ok"] is False and schritte["Browser geschlossen"]["ok"] is False
    assert schritte["Profile gesperrt"]["ok"] and schritte["Plätze geleert"]["ok"] and schritte["Temp-Dateien gelöscht"]["ok"]
    assert w.treiber[1].geschlossen and "daten_loeschen" in w.protokoll.namen("dienst")


def test_nachweis_meldet_wenn_ein_platz_nicht_leer_ist(tmp_path, clips):
    import wamo_labor_last_server as sv
    w = Welt(tmp_path, clips, sitzungen=(1,))
    w.dienst.daten_loeschen = lambda platz: w.dienst.plaetze_zustand.update({platz: sv.DienstPlatz(platz, False, 3, 0, False, False)})
    e = w.lauf()
    nachweis = next(a for a in e["aufraeumen"] if a["schritt"].startswith("Nachweis"))
    assert nachweis["ok"] is False and "Platz 1" in nachweis["text"]


def test_gespraech_startet_nach_zwei_fehlversuchen(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(1,))
    w.verhalten.gespraech_fehler = {0: 2}
    e = w.lauf()
    assert e["stufen"][0]["abbrueche"] == 0 and e["stufen"][0]["schritte"] > 0


def test_gespraech_das_nie_startet_zaehlt_als_abbruch_und_die_sitzung_faellt_aus(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2,))
    w.verhalten.gespraech_fehler = {1: 9}
    e = w.lauf()
    assert e["stufen"][0]["abbrueche"] == 1 and e["abbruchgrund"] == ""
    assert {s["sitzung"] for s in e["schritte"]["1"] if s["abbruch"] == ""} == {0}


def test_sitzung_ohne_antwort_ist_eine_zeitueberschreitung_und_kein_abbruch(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(2,))
    w.verhalten.keine_antwort = {1}
    e = w.lauf()
    assert e["stufen"][0]["zeitueberschreitungen"] > 0 and e["stufen"][0]["abbrueche"] == 0


def test_erster_ton_waechst_mit_der_zahl_der_sitzungen(tmp_path, clips):
    w = Welt(tmp_path, clips, sitzungen=(1, 4))
    w.verhalten.erster_ton_je_sitzungen = lambda n: 1.0 + 0.5 * n
    e = w.lauf()
    p1, p4 = (s["erster_ton_ms"]["p50"] for s in e["stufen"])
    assert p4 > p1 and 1400 <= p1 <= 1700


# ---------- Hilfen ----------

def test_parallel_sammelt_fehler_je_aufgabe():
    def gut():
        return None

    def schlecht():
        raise ValueError("kaputt")

    ergebnisse = um.parallel([gut, schlecht, gut])
    assert ergebnisse[0] is None and isinstance(ergebnisse[1], ValueError) and ergebnisse[2] is None


def test_echtzeit_hat_jetzt_und_schlafen():
    uhr = um.Echtzeit()
    t = uhr.jetzt()
    uhr.schlafen(0.01)
    assert uhr.jetzt() >= t


def test_profil_stand_liest_die_statuszeilen(welt):
    welt.kern.aktiv.add("labor-03")
    stand = vb.profil_stand(welt.ein, welt.umgebung())
    assert stand["labor-03"] is True and stand["labor-01"] is False and len(stand) == 9


def test_profil_stand_lehnt_unerwartete_zeilen_ab(welt):
    welt.kern = lambda host, nutzlast: wl.Ergebnis(0, "Hallo Welt", "")
    with pytest.raises(vb.VorpruefFehler, match="unerwartet"):
        vb.profil_stand(welt.ein, welt.umgebung())
