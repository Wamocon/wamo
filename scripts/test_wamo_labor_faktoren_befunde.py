"""Tests zu den Befunden der unabhängigen Prüfung von scripts/wamo_labor_faktoren.py (Signale, Zwischendatei, Abbruchtext, Seed,
Whitelist, Adressausgabe, Sicherungen, Zeit, Antwortgrenzen). Kein Netz außer Loopback, nie die echte Umschlag-Datei, kein langes
Zufallsliteral: Seeds und Passwörter kommen aus den Hilfen der anderen Testdateien."""
from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest

import wamo_labor_faktoren as wlf
import wamo_labor_faktoren_signale as sg
import wamo_labor_faktoren_umschlag as um
from test_wamo_labor_faktoren import PW, Rechte, seed_von, umschlag_datei, umschlag_text
from test_wamo_labor_faktoren_lauf import (  # noqa: F401  (Fixtures)
    einrichten, email, kein_echtes_home, labor, lauf, seed_der_datei, umgebung)

windows_nur = pytest.mark.skipif(os.name != "nt", reason="nur unter Windows")


class Hart(BaseException):
    """Ein harter Abbruch, bei dem kein Aufräumen mehr läuft (der Prozess wird beendet)."""


def zwischen_datei(datei: Path) -> Path:
    return Path(str(datei) + ".neu")


# ---------- Befund 1: Signale ----------

def test_signale_werden_zur_ausnahme_und_danach_zurueckgesetzt():
    vorher = {s: signal.getsignal(s) for s in sg.SIGNALE}
    alt = sg.umsetzen()
    try:
        with pytest.raises(sg.Signal) as e:
            signal.raise_signal(signal.SIGTERM)
        assert e.value.nummer == signal.SIGTERM
    finally:
        sg.zuruecksetzen(alt)
    assert {s: signal.getsignal(s) for s in sg.SIGNALE} == vorher


def test_waehrend_des_aufraeumens_werden_signale_ignoriert():
    alt = sg.umsetzen()
    try:
        mitte = sg.ignorieren()
        signal.raise_signal(signal.SIGINT)  # ein zweites Strg+C: kein Abbruch
        signal.raise_signal(signal.SIGTERM)
        sg.zuruecksetzen(mitte)
        with pytest.raises(sg.Signal):
            signal.raise_signal(signal.SIGINT)
    finally:
        sg.zuruecksetzen(alt)


def test_gesetzt_werden_sigint_sigterm_und_je_nach_system_sighup_oder_sigbreak():
    namen = {s.name for s in sg.SIGNALE}
    assert {"SIGINT", "SIGTERM"} <= namen
    assert ("SIGBREAK" in namen) if os.name == "nt" else ("SIGHUP" in namen)


def test_sigterm_im_lauf_raeumt_auf_und_endet_mit_143(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    vorher = {s: signal.getsignal(s) for s in sg.SIGNALE}
    monkeypatch.setattr(wlf.api.Verbindung, "erneuern", lambda self, *a, **k: signal.raise_signal(signal.SIGTERM))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert {s: signal.getsignal(s) for s in sg.SIGNALE} == vorher, "die Handler sind nach dem Lauf wieder zurückgesetzt"
    assert code == 128 + signal.SIGTERM and "labor-01: abgebrochen; Faktor wieder entfernt" in err and "Abgebrochen" in err
    assert labor.faktoren_von(email("labor-01")) == [] and labor.sitzungen_von(email("labor-01")) == 0
    assert seed_der_datei(datei, "labor-01") == "" and not zwischen_datei(datei).exists()


@windows_nur
def test_sigbreak_unter_windows_raeumt_ebenfalls_auf(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    monkeypatch.setattr(wlf.api.Verbindung, "erneuern", lambda self, *a, **k: signal.raise_signal(signal.SIGBREAK))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 128 + signal.SIGBREAK and labor.faktoren_von(email("labor-01")) == []


def test_zweites_strg_c_waehrend_des_aufraeumens_bricht_nicht_ab(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    monkeypatch.setattr(wlf.api.Verbindung, "erneuern", lambda self, *a, **k: signal.raise_signal(signal.SIGINT))
    echt = wlf.api.Verbindung.faktor_loeschen

    def loeschen_nach_zweitem_signal(self, token, faktor_id):
        signal.raise_signal(signal.SIGINT)  # kommt mitten im Aufräumen
        return echt(self, token, faktor_id)

    monkeypatch.setattr(wlf.api.Verbindung, "faktor_loeschen", loeschen_nach_zweitem_signal)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 130 and "Faktor wieder entfernt" in err
    assert labor.faktoren_von(email("labor-01")) == [] and labor.sitzungen_von(email("labor-01")) == 0


def test_signal_im_ersten_profil_laesst_die_weiteren_nicht_mehr_laufen(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    monkeypatch.setattr(wlf.api.Verbindung, "erneuern", lambda self, *a, **k: signal.raise_signal(signal.SIGTERM))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01,labor-02")
    assert code == 143 and labor.faktoren_von(email("labor-02")) == [] and "labor-02" not in out + err


# ---------- Befund 1: Zwischendatei ----------

def test_zwischendatei_schreiben_lesen_loeschen(tmp_path):
    datei = umschlag_datei(tmp_path)
    rechte = Rechte()
    u = um.Umschlag(datei, rechte=rechte)
    assert u.zwischen_lesen() is None
    u.zwischen_schreiben("labor-02", "faktor-0123456789ab", seed_von("zwei"))
    ziel = zwischen_datei(datei)
    assert u.zwischen_pfad == ziel and ziel.read_text(encoding="utf-8") == f"labor-02\tfaktor-0123456789ab\t{seed_von('zwei')}\n"
    temporaer = [g for p, g in rechte.aufrufe if p.name.startswith(".")]
    assert temporaer and temporaer[0] == 0, "die Rechte werden gesetzt, solange die Wegwerf-Datei leer ist"
    z = u.zwischen_lesen()
    assert (z.kennung, z.faktor, z.seed) == ("labor-02", "faktor-0123456789ab", seed_von("zwei")) and seed_von("zwei") not in repr(z)
    u.zwischen_schreiben("labor-03", "faktor-ba9876543210", seed_von("drei"))
    assert u.zwischen_lesen().kennung == "labor-03", "es gibt nur einen Eintrag"
    u.zwischen_loeschen()
    u.zwischen_loeschen()  # zweimal ist harmlos
    assert u.zwischen_lesen() is None and [p.name for p in datei.parent.iterdir()] == [datei.name]


@pytest.mark.parametrize("inhalt", ["", "labor-02\n", f"labor-02\tf\t{'A' * 20}\n", "labor-99\tfaktor-0123456789ab\tAAAAAAAAAAAAAAAA\n",
                                    "labor-02\tfaktor-0123456789ab\tkein seed\n", "a\tb\tc\td\n"])
def test_ungueltige_zwischendatei_ist_ein_fehler_ohne_inhalt(tmp_path, inhalt):
    datei = umschlag_datei(tmp_path)
    zwischen_datei(datei).write_text(inhalt, encoding="utf-8")
    with pytest.raises(um.UmschlagFehler, match="Zwischendatei") as e:
        um.Umschlag(datei, rechte=Rechte()).zwischen_lesen()
    assert "kein seed" not in str(e.value)


def test_normaler_lauf_schreibt_die_zwischendatei_sofort_nach_verify_und_raeumt_sie_weg(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    stand = {}
    echt = wlf.api.Verbindung.erneuern

    def pruefen(self, *a, **k):  # der erste Schritt nach verify
        faktor = labor.faktoren_von(email("labor-01"))[0]
        stand["inhalt"] = zwischen_datei(datei).read_text(encoding="utf-8")
        stand["erwartet"] = f"labor-01\t{faktor['id']}\t{faktor['seed']}\n"
        stand["in_datei"] = seed_der_datei(datei, "labor-01")
        return echt(self, *a, **k)

    monkeypatch.setattr(wlf.api.Verbindung, "erneuern", pruefen)
    assert einrichten(capsys, labor, datei, "--profile", "labor-01")[0] == 0
    assert stand["inhalt"] == stand["erwartet"] and stand["in_datei"] == ""
    assert not zwischen_datei(datei).exists() and seed_der_datei(datei, "labor-01") != ""


def test_harter_abbruch_nach_verify_verliert_den_seed_nicht(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    echt = wlf.api.Verbindung.erneuern

    def hart(self, *a, **k):
        raise Hart

    monkeypatch.setattr(wlf.api.Verbindung, "erneuern", hart)
    with pytest.raises(Hart):
        einrichten(capsys, labor, datei, "--profile", "labor-01")
    capsys.readouterr()
    faktor = labor.faktoren_von(email("labor-01"))[0]
    assert faktor["status"] == "verified" and seed_der_datei(datei, "labor-01") == ""
    assert faktor["seed"] in zwischen_datei(datei).read_text(encoding="utf-8")
    monkeypatch.setattr(wlf.api.Verbindung, "erneuern", echt)
    anfragen = labor.anfragen.count(("POST", "/auth/v1/factors"))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 0 and err == "" and "labor-01: Seed aus der Zwischendatei" in out and "übersprungen" in out
    assert seed_der_datei(datei, "labor-01") == faktor["seed"] and not zwischen_datei(datei).exists()
    assert labor.anfragen.count(("POST", "/auth/v1/factors")) == anfragen and len(labor.faktoren_von(email("labor-01"))) == 1
    assert faktor["seed"] not in out + err


def test_zwischendatei_gilt_auch_wenn_ein_anderes_profil_gewaehlt_ist(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path)
    u = um.Umschlag(datei, rechte=Rechte())
    u.zwischen_schreiben("labor-01", "faktor-0123456789ab", seed_von("eins"))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-02")
    assert code == 0 and "labor-01: Seed aus der Zwischendatei" in out
    assert seed_der_datei(datei, "labor-01") == seed_von("eins") and not zwischen_datei(datei).exists()


def test_veraltete_zwischendatei_wird_vom_naechsten_einrichten_geheilt(capsys, umgebung, labor, tmp_path):
    """Der Faktor zur Zwischendatei existiert am Server nicht mehr: einrichten ersetzt den übernommenen Seed durch einen neuen."""
    datei = umschlag_datei(tmp_path)
    um.Umschlag(datei, rechte=Rechte()).zwischen_schreiben("labor-01", "faktor-0123456789ab", seed_von("alt"))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    neu = labor.faktoren_von(email("labor-01"))
    assert code == 0 and "labor-01: Faktor eingerichtet, aal2 geprüft" in out and len(neu) == 1
    assert seed_der_datei(datei, "labor-01") == neu[0]["seed"] != seed_von("alt")


def test_ungueltige_zwischendatei_stoppt_vor_dem_server_und_bleibt_liegen(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path)
    zwischen_datei(datei).write_text("Unsinn\n", encoding="utf-8")
    code, out, err = einrichten(capsys, labor, datei)
    assert code == 2 and "Zwischendatei" in err and labor.anfragen == [] and zwischen_datei(datei).exists()


def test_zwischendatei_ohne_zeile_im_umschlag_bleibt_liegen(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(2))
    um.Umschlag(datei, rechte=Rechte()).zwischen_schreiben("labor-07", "faktor-0123456789ab", seed_von("sieben"))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 2 and "keine Zeile" in err and zwischen_datei(datei).exists() and labor.anfragen == []


def test_trocken_uebernimmt_nichts_und_meldet_die_zwischendatei(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path)
    um.Umschlag(datei, rechte=Rechte()).zwischen_schreiben("labor-01", "faktor-0123456789ab", seed_von("eins"))
    vorher = datei.read_bytes()
    code, out, err = einrichten(capsys, labor, datei, "--trocken", "--profile", "labor-02")
    assert code == 0 and "labor-01: Zwischendatei eines abgebrochenen Laufs vorhanden" in out
    assert datei.read_bytes() == vorher and zwischen_datei(datei).exists()


def test_pruefen_weist_auf_die_zwischendatei_hin(capsys, umgebung, tmp_path):
    datei = umschlag_datei(tmp_path)
    um.Umschlag(datei, rechte=Rechte()).zwischen_schreiben("labor-01", "faktor-0123456789ab", seed_von("eins"))
    code, out, err = lauf(capsys, ["pruefen", "--profile", "labor-01", "--umschlag-datei", str(datei)])
    assert code == 1 and "Zwischendatei" in err and seed_von("eins") not in out + err


def test_zwischendatei_bleibt_wenn_der_faktor_nicht_entfernt_werden_kann(capsys, umgebung, labor, tmp_path, monkeypatch):
    labor.fehler("POST", "/logout", 500, email=email("labor-01"))  # Fehler nach verify, vor dem Speichern

    def netzfehler(self, token, faktor_id):
        raise wlf.api.ApiFehler("Faktor löschen: keine Verbindung zum Anmeldeserver")

    monkeypatch.setattr(wlf.api.Verbindung, "faktor_loeschen", netzfehler)
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    faktor = labor.faktoren_von(email("labor-01"))[0]
    assert code == 1 and "NICHT entfernt" in err
    assert faktor["seed"] in zwischen_datei(datei).read_text(encoding="utf-8"), "der Seed bleibt erhalten"
    assert seed_der_datei(datei, "labor-01") == "" and faktor["seed"] not in out + err


def test_zwischendatei_wird_nach_erfolgreichem_aufraeumen_geloescht(capsys, umgebung, labor, tmp_path):
    labor.fehler("POST", "/logout", 500, email=email("labor-01"))
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 1 and "Faktor wieder entfernt" in err and not zwischen_datei(datei).exists()


# ---------- Befund 2: Text bei Strg+C ----------

def test_strg_c_gibt_den_aufraeumtext_vor_dem_beenden_aus(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)

    def abbruch(self, *a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(wlf.api.Verbindung, "verify", abbruch)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 130 and "labor-01: abgebrochen; Faktor wieder entfernt" in err and "Abgebrochen." in err
    assert PW[1] not in out + err


def test_strg_c_mit_netzfehler_beim_aufraeumen_sagt_nicht_entfernt(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)

    def abbruch(self, *a, **k):
        raise KeyboardInterrupt

    def netzfehler(self, token, faktor_id):
        raise wlf.api.ApiFehler("Faktor löschen: keine Verbindung zum Anmeldeserver")

    monkeypatch.setattr(wlf.api.Verbindung, "verify", abbruch)
    monkeypatch.setattr(wlf.api.Verbindung, "faktor_loeschen", netzfehler)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 130
    assert "labor-01: abgebrochen; der neue Faktor konnte NICHT entfernt werden" in err and "--neu" in err and "Admin am Server" in err
    assert PW[1] not in out + err and "Traceback" not in err
