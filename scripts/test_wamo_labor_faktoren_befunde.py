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


# ---------- Befund 3: normalisierter Seed, nur [A-Z2-7=] in der Datei ----------

LEERRAUM = "\n\r\t "


def mit_leerraum(seed: str) -> str:
    """Wie ein Server, der den Seed gruppiert und teils klein schreibt: Zeilenumbruch, Wagenrücklauf, Tabulator, U+2028."""
    return seed[:8] + "\n" + seed[8:16].lower() + "\r" + seed[16:24] + "\t" + seed[24:28] + " " + seed[28:]


def test_seed_des_servers_wird_normalisiert_gespeichert(capsys, umgebung, labor, tmp_path):
    labor.seed_darstellung = mit_leerraum
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    server = labor.faktoren_von(email("labor-01"))[0]["seed"]
    gespeichert = seed_der_datei(datei, "labor-01")
    assert code == 0 and gespeichert == server
    assert not any(z in gespeichert for z in LEERRAUM) and gespeichert == gespeichert.upper()
    assert len(datei.read_text(encoding="utf-8").splitlines()) == 3, "die Datei hat keine zusätzliche Zeile bekommen"


def test_api_liefert_den_seed_normalisiert(labor):
    import wamo_labor_faktoren_api as api
    labor.seed_darstellung = mit_leerraum
    v = api.Verbindung(labor.basis, "anon-" + "a" * 8, None)
    s = v.anmelden(email("labor-01"), PW[1])
    neu = v.faktor_anlegen(s.access_token, "WAMO-test")
    assert neu.seed == [f for f in labor.faktoren.values()][0]["seed"]


@pytest.mark.parametrize("seed", ["AB\nCDEFGHIJKLMNOP", "ABCDEFGH\rIJKLMNOP", "ABCDEFGH\tIJKLMNOP", "ABCDEFGH IJKLMNOP",
                                  "abcdefghijklmnop", "ABCDEFGH IJKLMNOP", "ABCDEFGH1JKLMNOP", "ABCDEFGH\x00IJKLMNOP", ""])
def test_umschlag_schreibt_nur_zeichen_aus_a_bis_z_und_2_bis_7(tmp_path, seed):
    original = umschlag_text()
    datei = umschlag_datei(tmp_path, original)
    with pytest.raises(um.UmschlagFehler, match="Seed") as e:
        um.Umschlag(datei, rechte=Rechte()).seed_speichern("labor-01", seed)
    assert datei.read_text(encoding="utf-8") == original and not [p for p in datei.parent.iterdir() if ".sicherung-" in p.name]
    assert seed.strip() == "" or seed not in str(e.value)


def test_umschlag_nimmt_einen_gueltigen_seed_mit_auffuellzeichen(tmp_path):
    datei = umschlag_datei(tmp_path)
    um.Umschlag(datei, rechte=Rechte()).seed_speichern("labor-01", "ABCDEFGHIJKLMNOP" + "======")
    assert seed_der_datei(datei, "labor-01") == "ABCDEFGHIJKLMNOP======"


# ---------- Befund 4: nur labor-01 bis labor-09, E-Mail der Zeile gegen die Kennung ----------

@pytest.mark.parametrize("profil", ["labor-10", "labor-16", "labor-17", "labor-00", "labor-schau"])
def test_nur_labor_01_bis_09_sind_zulaessig(capsys, umgebung, tmp_path, profil):
    datei = umschlag_datei(tmp_path)
    code, out, err = lauf(capsys, ["pruefen", "--profile", profil, "--umschlag-datei", str(datei)])
    assert code == 2 and "labor-01 bis labor-09" in err and out == ""
    assert not um.KENNUNG_RE.fullmatch(profil)


@pytest.mark.parametrize("zeile", [
    "labor-03\tlabor-04@wamo.test\tPw-ababab", "labor-03\tlabor-03@example.org\tPw-ababab", "labor-03\tLABOR-03@wamo.test\tPw-ababab",
    "labor-03\tlabor-03@wamo.test.evil\tPw-ababab", "labor-03\tx@labor-03@wamo.test\tPw-ababab", "labor-03\t labor-03@wamo.test x\tPw-ababab"])
def test_email_der_zeile_muss_zur_kennung_passen(tmp_path, zeile):
    datei = umschlag_datei(tmp_path, zeile + "\n")
    u = um.Umschlag(datei, rechte=Rechte())
    with pytest.raises(um.UmschlagFehler, match="E-Mail") as e:
        u.zugang("labor-03")
    assert "Pw-ababab" not in str(e.value)
    with pytest.raises(um.UmschlagFehler, match="E-Mail"):
        u.seed_speichern("labor-03", seed_von("a"))
    assert datei.read_text(encoding="utf-8") == zeile + "\n"


def test_zeile_mit_falscher_email_scheitert_ohne_aufruf_des_servers(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(3).replace("labor-02@wamo.test", "labor-01@wamo.test"))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01,labor-02,labor-03")
    assert code == 1 and "labor-02: FEHLER: Die E-Mail in der Zeile für labor-02 passt nicht" in err
    assert "labor-01: Faktor eingerichtet" in out and "labor-03: Faktor eingerichtet" in out
    assert [f for f in labor.faktoren_von(email("labor-02"))] == [] and len(labor.faktoren_von(email("labor-01"))) == 1


# ---------- Befund 5: Host:Port als erste Zeile, Loopback-http nur mit ausdrücklichem --basis ----------

@pytest.mark.parametrize("quelle,erwartet", [
    ("basis", "Anmeldeserver: 192.168.178.136:55443 (aus --basis)"),
    ("env", "Anmeldeserver: 192.168.178.136:55443 (aus SUPABASE_URL)"),
    ("standard", "Anmeldeserver: 192.168.178.136:55443 (Standard)")])
def test_host_und_port_stehen_als_erste_zeile(capsys, umgebung, tmp_path, monkeypatch, quelle, erwartet):
    datei = umschlag_datei(tmp_path)
    argv = ["einrichten", "--trocken", "--profile", "labor-01", "--umschlag-datei", str(datei)]
    if quelle == "basis":
        argv += ["--basis", "https://192.168.178.136:55443"]
    if quelle == "env":
        monkeypatch.setenv("SUPABASE_URL", "https://192.168.178.136:55443")
    code, out, err = lauf(capsys, argv)
    assert code == 0 and out.splitlines()[0] == erwartet, out


def test_host_und_port_stehen_auch_im_echten_lauf_vor_allem_anderen(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path)
    um.Umschlag(datei, rechte=Rechte()).zwischen_schreiben("labor-02", "faktor-0123456789ab", seed_von("zwei"))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert out.splitlines()[0] == f"Anmeldeserver: {labor.basis.removeprefix('http://')} (aus --basis)"
    assert "Zwischendatei" in out.splitlines()[1]


def test_loopback_http_aus_umgebung_wird_abgelehnt(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    monkeypatch.setenv("SUPABASE_URL", labor.basis)
    code, out, err = lauf(capsys, ["einrichten", "--umschlag-datei", str(datei)])
    assert code == 2 and "--basis" in err and "SUPABASE_URL" in err and out == "" and labor.anfragen == []


def test_loopback_http_aus_env_datei_wird_abgelehnt_und_mit_basis_erlaubt(capsys, umgebung, labor, tmp_path):
    env = tmp_path / "test.env"
    env.write_text(f"SUPABASE_URL={labor.basis}\n", encoding="utf-8")
    datei = umschlag_datei(tmp_path)
    code, out, err = lauf(capsys, ["einrichten", "--trocken", "--env-datei", str(env), "--umschlag-datei", str(datei)])
    assert code == 2 and "--basis" in err
    argv = ["einrichten", "--trocken", "--profile", "labor-01", "--env-datei", str(env), "--basis", labor.basis, "--umschlag-datei", str(datei)]
    code, out, err = lauf(capsys, argv)
    assert code == 0 and "(aus --basis)" in out.splitlines()[0]


# ---------- Befund 6: Sicherungen begrenzen und in der Zusammenfassung nennen ----------

def alte_sicherungen(datei: Path, stempel: list[str]) -> list[Path]:
    kopien = []
    for s in stempel:
        p = Path(str(datei) + f".sicherung-{s}")
        p.write_text("alt\n", encoding="utf-8")
        kopien.append(p)
    return kopien


def test_nur_die_letzten_drei_sicherungen_bleiben(tmp_path):
    datei = umschlag_datei(tmp_path)
    alt = alte_sicherungen(datei, ["20260101-000001", "20260102-000001", "20260103-000001", "20260104-000001", "20260105-000001"])
    fremd = [tmp_path / "umschlag" / n for n in ("umschlag-labor.txt.sicherung-xyz", "andere.txt.sicherung-20260101-000001",
                                                 "umschlag-labor.txt.neu", "umschlag-labor.txt.sicherung-20260101-000001.kopie")]
    for f in fremd:
        f.write_text("fremd\n", encoding="utf-8")
    u = um.Umschlag(datei, rechte=Rechte(), jetzt=lambda: 1_794_000_000.0)
    u.seed_speichern("labor-01", seed_von("a"))
    bleibt = sorted(p.name for p in datei.parent.iterdir() if p.name.startswith("umschlag-labor.txt.sicherung-") and p not in fremd)
    assert u.sicherung is not None and u.sicherung.name in bleibt and len(bleibt) == 3
    assert [p.exists() for p in alt] == [False, False, False, True, True], "die ältesten sind weg, die zwei neuesten alten bleiben"
    assert all(f.exists() for f in fremd), "fremde Dateien bleiben unberührt"


def test_die_neue_sicherung_bleibt_auch_bei_rueckwaerts_laufender_uhr(tmp_path):
    datei = umschlag_datei(tmp_path)
    alt = alte_sicherungen(datei, ["20260101-000001", "20260102-000001", "20260103-000001"])
    u = um.Umschlag(datei, rechte=Rechte(), jetzt=lambda: 1_000_000_000.0)  # 2001, älter als alle vorhandenen
    u.seed_speichern("labor-01", seed_von("a"))
    assert u.sicherung.exists() and [p.exists() for p in alt] == [False, True, True]


def test_ohne_schreiben_gibt_es_keine_sicherung(tmp_path):
    datei = umschlag_datei(tmp_path)
    assert um.Umschlag(datei, rechte=Rechte()).sicherung is None


def test_zusammenfassung_nennt_den_namen_der_sicherung(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    name = [p.name for p in datei.parent.iterdir() if ".sicherung-" in p.name][0]
    zeilen = [z for z in out.splitlines() if z.startswith("Sicherung")]
    assert zeilen == [f"Sicherung der Umschlag-Datei: {name} (enthält Passwörter, nach dem Labortag löschen, es bleiben die letzten 3)"]
    assert "Passwort" not in name and PW[1] not in out


def test_ohne_schreibvorgang_keine_sicherungszeile(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--trocken", "--profile", "labor-01")
    assert "Sicherung" not in out
    einrichten(capsys, labor, datei, "--profile", "labor-01")
    capsys.readouterr()
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")  # übersprungen: nichts geschrieben
    assert "übersprungen" in out and "Sicherung" not in out


# ---------- Befund 7: Zeitversatz begrenzt und als UTC gedeutet, kein Code zweimal im selben Fenster ----------

import calendar  # noqa: E402
import time  # noqa: E402
from email.utils import formatdate  # noqa: E402

import wamo_labor_faktoren_totp as tt  # noqa: E402
from test_wamo_labor_faktoren_lauf import Uhr  # noqa: E402

LOKAL = float(calendar.timegm((2026, 11, 12, 10, 0, 0)))


def test_hoechstversatz_sind_300_sekunden():
    assert tt.MAX_VERSATZ_S == 300
    assert tt.zeitversatz(formatdate(LOKAL + 250, usegmt=True), LOKAL) == pytest.approx(250.5, abs=0.6)
    assert tt.zeitversatz(formatdate(LOKAL + 400, usegmt=True), LOKAL) == 0.0
    assert tt.zeitversatz(formatdate(LOKAL - 400, usegmt=True), LOKAL) == 0.0


@pytest.mark.parametrize("kopf", ["Thu, 12 Nov 2026 10:00:40 -0000", "Thu, 12 Nov 2026 10:00:40", "Thu, 12 Nov 2026 10:00:40 +0000",
                                  "Thu, 12 Nov 2026 10:00:40 GMT"])
def test_date_kopf_ohne_oder_mit_unbestimmter_zeitzone_gilt_als_utc(kopf, monkeypatch):
    if hasattr(time, "tzset"):  # unter Linux die Ortszeit auf eine Zone mit großem Abstand zu UTC stellen
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        time.tzset()
    try:
        assert tt.zeitversatz(kopf, LOKAL) == pytest.approx(40.5, abs=0.6)
    finally:
        if hasattr(time, "tzset"):
            monkeypatch.delenv("TZ", raising=False)
            time.tzset()


def test_frisches_fenster_wartet_nach_einem_schon_benutzten_fenster():
    uhr = {"t": 70.0}  # Fenster 2 (60 bis 90)
    waren = []

    def warte(s):
        waren.append(s)
        uhr["t"] += s

    t = tt.frisches_fenster(lambda: uhr["t"], warte, nach=2)
    assert len(waren) == 1 and 19.9 < waren[0] < 21 and int(t // 30) == 3
    assert tt.frisches_fenster(lambda: 95.0, waren.append, nach=2) == 95.0 and len(waren) == 1, "ein neueres Fenster braucht keine Wartezeit"
    assert tt.frisches_fenster(lambda: 70.0, waren.append) == 70.0 and len(waren) == 1, "ohne nach gilt nur die Reserve am Fensterende"


@pytest.mark.parametrize("start,nach", [(59.5, 2), (89.9, 2), (60.0, 2), (150.0, 4)])
def test_frisches_fenster_liegt_nach_einer_zeitschwankung_immer_hinter_dem_benutzten_fenster(start, nach):
    """Die Schätzung der Serveruhr schwankt um etwa eine Sekunde (Date-Kopf in ganzen Sekunden): Liegt sie wieder vor dem Ende des
    benutzten Fensters, muss der nächste Code trotzdem aus einem späteren Fenster stammen, sonst wäre es derselbe Code."""
    uhr = {"t": start}

    def warte(s):
        uhr["t"] += s

    t = tt.frisches_fenster(lambda: uhr["t"], warte, nach=nach)
    assert int(t // 30) > nach and (t == start or (t % 30) >= 0.19)


def test_aufraeumen_nach_der_abmeldung_nutzt_keinen_code_zweimal(capsys, umgebung, labor, tmp_path, monkeypatch):
    """Der Seed lässt sich nicht speichern: Das Aufräumen meldet sich neu an und bestätigt den Faktor mit demselben Seed noch einmal.
    Im selben 30-Sekunden-Fenster käme derselbe Code, den der Server ablehnt, also wartet das Skript auf das nächste Fenster."""
    datei = umschlag_datei(tmp_path)
    echt = os.replace

    def nur_umschlag_scheitert(quelle, ziel):
        if Path(ziel).resolve() == datei.resolve():
            raise OSError("Platte voll")
        return echt(quelle, ziel)

    monkeypatch.setattr(os, "replace", nur_umschlag_scheitert)
    uhr = Uhr(labor)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01", uhr=uhr)
    assert code == 1 and "Faktor wieder entfernt" in err and labor.faktoren_von(email("labor-01")) == []
    assert any(0.1 < s <= 32 for s in uhr.gewartet), "das Skript hat auf das nächste Fenster gewartet"
    assert len(labor.benutzte_codes) == 2 and len({c for _, c in labor.benutzte_codes}) == 2, "zwei verschiedene Codes für denselben Faktor"


class FesteUhr(Uhr):
    """Steht beim Start still und springt nur beim Warten: zwei Läufe liegen so sicher im selben 30-Sekunden-Fenster."""

    def __init__(self, start: float, server=None):
        super().__init__(server)
        self.start = start

    def zeit(self) -> float:
        return self.start + self.vorsprung


def test_neu_gleich_nach_dem_einrichten_wartet_auf_das_naechste_fenster(capsys, umgebung, labor, tmp_path):
    """Der Code des alten Faktors wäre im selben Fenster ein zweites Mal derselbe und würde abgelehnt: Das Skript nimmt das nächste Fenster,
    statt zu melden, der Seed passe nicht."""
    datei = umschlag_datei(tmp_path)
    start = time.time()
    assert einrichten(capsys, labor, datei, "--profile", "labor-01", uhr=FesteUhr(start, labor))[0] == 0
    zweite = FesteUhr(start, labor)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01", "--neu", uhr=zweite)
    assert code == 0 and err == "" and "Faktor eingerichtet" in out and any(0.1 < s <= 32 for s in zweite.gewartet)
    assert len(labor.faktoren_von(email("labor-01"))) == 1


# ---------- Befund 8: repr, Wortlaut von pruefen, Gesamtlimit je Anfrage, OSError ----------

import wamo_labor_faktoren_api as api  # noqa: E402

SCHLUESSEL_B = "anon-" + "a" * 8


def test_antwort_zeigt_ihre_daten_nicht_in_der_darstellung():
    a = api.Antwort(200, {"access_token": "T" * 8, "totp": {"secret": "S" * 8}}, "Thu, 12 Nov 2026 10:00:00 GMT")
    assert "access_token" not in repr(a) and "T" * 8 not in repr(a) and "S" * 8 not in repr(a)


def test_pruefen_belegt_nur_den_seed(capsys, umgebung, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(2, seeds={"labor-01": seed_von("eins")}))
    code, out, err = lauf(capsys, ["pruefen", "--profile", "labor-01,labor-02", "--umschlag-datei", str(datei)])
    assert out.splitlines() == ["labor-01: Seed in der Datei: ja", "labor-02: Seed in der Datei: nein"] and code == 1


def test_gesamtlimit_je_anfrage_bricht_eine_traege_antwort_ab(labor):
    assert api.GESAMTLIMIT_S == 60
    labor.verzoegerung = 3.0  # die Antwort kommt später als das Gesamtlimit, jede einzelne Wartezeit bliebe unter dem Zeitlimit
    v = api.Verbindung(labor.basis, SCHLUESSEL_B, None, zeitlimit=10.0, gesamtlimit=0.5)
    beginn = time.monotonic()
    with pytest.raises(api.ApiFehler, match="Zeitüberschreitung"):
        v.anmelden(email("labor-01"), PW[1])
    assert time.monotonic() - beginn < 2.5


def test_gesamtlimit_gilt_auch_fuer_einen_tropfenweise_gelieferten_antworttext(labor):
    labor.tropfen = 0.1  # jedes Byte einzeln: kein einzelner Lesevorgang erreicht das Zeitlimit von 2 Sekunden
    v = api.Verbindung(labor.basis, SCHLUESSEL_B, None, zeitlimit=2.0, gesamtlimit=0.6)
    beginn = time.monotonic()
    with pytest.raises(api.ApiFehler, match="Zeitüberschreitung"):
        v.anmelden(email("labor-01"), PW[1])
    assert time.monotonic() - beginn < 3.0


def test_nach_einem_abbruch_durch_das_gesamtlimit_geht_die_naechste_anfrage(labor):
    v = api.Verbindung(labor.basis, SCHLUESSEL_B, None, zeitlimit=10.0, gesamtlimit=0.5)
    labor.verzoegerung = 1.5
    with pytest.raises(api.ApiFehler, match="Zeitüberschreitung"):
        v.anmelden(email("labor-01"), PW[1])
    labor.verzoegerung = 0.0
    assert v.anmelden(email("labor-01"), PW[1]).access_token


def test_oserror_in_main_wird_zu_ausgang_2_ohne_werte(capsys, umgebung, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)

    def kaputt(*a, **k):
        raise PermissionError(13, "Zugriff verweigert " + PW[1], str(datei))

    monkeypatch.setattr(wlf, "befehl_pruefen", kaputt)
    code, out, err = lauf(capsys, ["pruefen", "--umschlag-datei", str(datei)])
    assert code == 2 and "Ein-/Ausgabefehler (PermissionError)" in err and PW[1] not in out + err and "Traceback" not in err
    assert str(datei) not in err

