"""Ablauftests für scripts/wamo_labor_faktoren.py gegen die Attrappe von GoTrue (wamo_labor_faktoren_attrappe.py).

Aufruf (aus scripts):  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q
Kein Netz außer Loopback, nie die echte Umschlag-Datei (jeder Test hat eine Datei im temporären Ordner, USERPROFILE und HOME zeigen
ebenfalls dorthin). Die Bausteine (TOTP, Umschlag-Datei, Verbindung) prüft test_wamo_labor_faktoren.py einzeln, hier läuft alles
zusammen, mit Mitschnitt von Standardausgabe und Fehlerausgabe.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

import wamo_labor_faktoren as wlf
import wamo_labor_faktoren_umschlag as um
from test_wamo_labor_faktoren import (  # noqa: F401  (kein_echtes_home ist eine selbstwirkende Fixture)
    PW, RFC_SEED, SCHLUESSEL, Rechte, kein_echtes_home, seed_von, sicherungen, umschlag_datei, umschlag_text)
from wamo_labor_faktoren_attrappe import GoTrueAttrappe, totp_unabhaengig

KENNUNGEN = [f"labor-{n:02d}" for n in range(1, 10)]


def email(kennung: str) -> str:
    return f"{kennung}@wamo.test"


@pytest.fixture
def umgebung(monkeypatch, tmp_path):
    """Schlüssel und Einstellungen nur aus der Umgebung des Tests, kein Zugriff auf .env-Dateien und auf icacls."""
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", SCHLUESSEL)
    for name in ("SUPABASE_URL", "WAMO_CA_FILE", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(wlf, "ENV_DATEIEN", (tmp_path / "keine-1.env", tmp_path / "keine-2.env"))
    rechte = Rechte()
    monkeypatch.setattr(wlf, "RECHTE_SETZEN", rechte)
    return rechte


@pytest.fixture
def labor():
    a = GoTrueAttrappe(schluessel=SCHLUESSEL)
    for n, k in enumerate(KENNUNGEN, 1):
        a.nutzer_anlegen(email(k), PW[n])
    a.starten()
    yield a
    a.beenden()


class Uhr:
    """Eine Uhr, die beim Warten vorspringt, statt zu schlafen: Die Tests warten nie wirklich."""

    def __init__(self):
        self.vorsprung = 0.0
        self.gewartet: list[float] = []

    def zeit(self) -> float:
        return time.time() + self.vorsprung

    def warte(self, sekunden: float) -> None:
        self.gewartet.append(sekunden)
        self.vorsprung += sekunden


def lauf(capsys, argv, **kw):
    if "zeit" not in kw and "warte" not in kw:
        uhr = kw.pop("uhr", None) or Uhr()
        kw.update(zeit=uhr.zeit, warte=uhr.warte)
    code = wlf.main(argv, **kw)
    ausgabe = capsys.readouterr()
    return code, ausgabe.out, ausgabe.err


def einrichten(capsys, labor, datei, *extra, **kw):
    return lauf(capsys, ["einrichten", "--basis", labor.basis, "--umschlag-datei", str(datei), *extra], **kw)


def spalten(datei: Path) -> dict[str, list[str]]:
    return {z.split("\t")[0]: z.rstrip("\r").split("\t") for z in datei.read_text(encoding="utf-8").splitlines() if z.startswith("labor-")}


def seed_der_datei(datei: Path, kennung: str) -> str:
    felder = spalten(datei)[kennung]
    return felder[3] if len(felder) > 3 else ""


# ---------- Einrichten: der Normalfall ----------

def test_alle_neun_profile_werden_eingerichtet(capsys, umgebung, labor, tmp_path):
    original = umschlag_text(9)
    datei = umschlag_datei(tmp_path, "# Umschlag\n" + original)
    code, out, err = einrichten(capsys, labor, datei)
    assert code == 0 and err == ""
    for k in KENNUNGEN:
        assert f"{k}: Faktor eingerichtet, aal2 geprüft" in out
        faktoren = labor.faktoren_von(email(k))
        assert len(faktoren) == 1 and faktoren[0]["status"] == "verified"
        assert seed_der_datei(datei, k) == faktoren[0]["seed"]
    assert "Zusammenfassung: 9 eingerichtet, 0 übersprungen, 0 fehlgeschlagen" in out
    assert labor.sitzungen == {}, "jede Sitzung wurde beendet"
    ohne_seeds = "\n".join("\t".join(z.split("\t")[:3]) for z in datei.read_text(encoding="utf-8").split("\n"))
    assert ohne_seeds == "# Umschlag\n" + original
    assert len(sicherungen(datei)) == 1 and sicherungen(datei)[0].read_text(encoding="utf-8") == "# Umschlag\n" + original
    assert [p.name for p in datei.parent.iterdir() if ".sicherung-" not in p.name] == [datei.name]


def _alle_geheimnisse(labor, datei: Path) -> set[str]:
    werte = set(PW.values()) | {SCHLUESSEL} | set(labor.tokens) | set(labor.alle_seeds)
    werte |= {z[3] for z in spalten(datei).values() if len(z) > 3}
    jetzt = time.time() + labor.versatz
    for seed in labor.alle_seeds:
        werte |= {totp_unabhaengig(seed, jetzt + d * 30) for d in range(-3, 4)}
    return werte


def test_geheimnisse_erscheinen_nie_in_der_ausgabe(capsys, umgebung, labor, tmp_path):
    """Mitschnitt von stdout und stderr über einen Lauf mit Erfolgen, falschem Passwort, 429, Verify-Fehler und Abmeldefehler."""
    datei = umschlag_datei(tmp_path, umschlag_text(9).replace(PW[2], "falsch-" + PW[2], 1))
    labor.fehler("POST", "/token", 429, "over_request_rate_limit", email=email("labor-04"))
    labor.fehler("POST", "/verify", 400, "mfa_verification_failed", email=email("labor-05"))
    labor.fehler("POST", "/logout", 500, email=email("labor-06"))
    code, out, err = einrichten(capsys, labor, datei)
    assert code == 1
    for ziel in ("labor-02", "labor-04", "labor-05", "labor-06"):
        assert f"{ziel}: FEHLER:" in err
    text = out + err
    for wert in _alle_geheimnisse(labor, datei) | {"falsch-" + PW[2]}:
        assert wert not in text, "ein Geheimnis steht in der Ausgabe"
    assert "otpauth" not in text and "eyJ" not in text


def test_unerwartete_ausnahme_nennt_nur_den_typ_und_raeumt_auf(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)

    def kaputt(self, token, faktor_id):
        raise RuntimeError("Geheimnis " + PW[1] + " " + token)

    monkeypatch.setattr(wlf.api.Verbindung, "challenge", kaputt)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 1 and "unerwarteter Fehler (RuntimeError)" in err and "Faktor wieder entfernt" in err
    assert PW[1] not in out + err and "Traceback" not in out + err
    assert labor.faktoren_von(email("labor-01")) == [] and seed_der_datei(datei, "labor-01") == ""


def test_strg_c_raeumt_auf_und_endet_mit_130(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)

    def abbruch(self, *a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(wlf.api.Verbindung, "verify", abbruch)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 130 and "Abgebrochen" in err
    assert labor.faktoren_von(email("labor-01")) == [] and labor.sitzungen_von(email("labor-01")) == 0


# ---------- Wiederholung, --neu, vorhandene Faktoren ----------

def test_wiederholung_ueberspringt_vorhandene_faktoren(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path)
    assert einrichten(capsys, labor, datei, "--profile", "labor-01")[0] == 0
    fertig = datei.read_bytes()
    angelegt = labor.anfragen.count(("POST", "/auth/v1/factors"))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 0 and "labor-01: Faktor und Seed vorhanden, übersprungen" in out
    assert "0 eingerichtet, 1 übersprungen, 0 fehlgeschlagen" in out
    assert labor.anfragen.count(("POST", "/auth/v1/factors")) == angelegt
    assert datei.read_bytes() == fertig and len(sicherungen(datei)) == 1 and labor.sitzungen == {}


def test_neu_ersetzt_den_faktor_und_den_seed(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path)
    einrichten(capsys, labor, datei, "--profile", "labor-01")
    alt_id = labor.faktoren_von(email("labor-01"))[0]["id"]
    alt_seed = seed_der_datei(datei, "labor-01")
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01", "--neu")
    assert code == 0 and "labor-01: Faktor eingerichtet, aal2 geprüft" in out
    neu = labor.faktoren_von(email("labor-01"))
    assert len(neu) == 1 and neu[0]["id"] != alt_id and neu[0]["status"] == "verified"
    assert seed_der_datei(datei, "labor-01") == neu[0]["seed"] != alt_seed
    assert labor.sitzungen == {}


def test_faktor_ohne_seed_in_der_datei_wird_nicht_still_ersetzt(capsys, umgebung, labor, tmp_path):
    labor.faktor_anlegen(email("labor-01"), "verified")
    datei = umschlag_datei(tmp_path)
    vorher = datei.read_bytes()
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 1 and "labor-01: FEHLER: Der Faktor ist vorhanden, aber der Seed fehlt in der Datei" in err and "--neu" in err
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01", "--neu")
    assert code == 1 and "ohne Seed in der Datei nicht entfernen" in err and "Admin" in err
    assert len(labor.faktoren_von(email("labor-01"))) == 1 and datei.read_bytes() == vorher and sicherungen(datei) == []
    assert labor.sitzungen == {}


def test_falscher_seed_in_der_datei_laesst_alles_unveraendert(capsys, umgebung, labor, tmp_path):
    fid, _ = labor.faktor_anlegen(email("labor-01"), "verified")
    datei = umschlag_datei(tmp_path, umschlag_text(seeds={"labor-01": seed_von("anderer")}))
    vorher = datei.read_bytes()
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01", "--neu")
    assert code == 1 and "passt nicht zum vorhandenen Faktor" in err
    assert [f["id"] for f in labor.faktoren_von(email("labor-01"))] == [fid] and datei.read_bytes() == vorher


def test_liegengebliebener_unbestaetigter_faktor_wird_entfernt(capsys, umgebung, labor, tmp_path):
    labor.faktor_anlegen(email("labor-01"), "unverified", name="WAMO-liegengeblieben")
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    faktoren = labor.faktoren_von(email("labor-01"))
    assert code == 0 and [f["status"] for f in faktoren] == ["verified"] and faktoren[0]["name"] != "WAMO-liegengeblieben"


# ---------- --trocken ----------

def test_trocken_aendert_nichts_und_rueht_das_netz_nicht_an(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(9, seeds={"labor-02": seed_von("zwei")}))
    vorher = datei.read_bytes()
    code, out, err = einrichten(capsys, labor, datei, "--trocken")
    assert code == 0 and err == "" and labor.anfragen == []
    assert out.count("Trockenlauf, würde anmelden") == 9 and "Seed in der Datei: ja" in out
    assert "Zusammenfassung (Trockenlauf, nichts geändert)" in out
    assert datei.read_bytes() == vorher and [p.name for p in datei.parent.iterdir()] == [datei.name]
    assert umgebung.aufrufe == [], "keine Rechte gesetzt, nichts angelegt"


def test_trocken_meldet_fehlende_zeilen_und_einstellungen(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path, umschlag_text(2))
    code, out, err = einrichten(capsys, labor, datei, "--trocken", "--profile", "labor-02,labor-05")
    assert code == 1 and "labor-05: FEHLER: Für labor-05 gibt es keine Zeile" in err and "labor-02: Trockenlauf" in out
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY")
    code, out, err = einrichten(capsys, labor, datei, "--trocken")
    assert code == 2 and "SUPABASE_PUBLISHABLE_KEY fehlt" in err and labor.anfragen == []


# ---------- Fehler je Profil ----------

def test_abbruch_eines_profils_laesst_die_anderen_laufen(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(3).replace(PW[2], "falsch-" + PW[2], 1))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01,labor-02,labor-03")
    assert code == 1
    assert "labor-02: FEHLER: Anmeldung: abgelehnt (HTTP 400 invalid_credentials)" in err
    assert "labor-01: Faktor eingerichtet" in out and "labor-03: Faktor eingerichtet" in out
    assert "2 eingerichtet, 0 übersprungen, 1 fehlgeschlagen" in out
    assert seed_der_datei(datei, "labor-02") == "" and labor.faktoren_von(email("labor-02")) == []


def test_ratenbegrenzung_betrifft_nur_ein_profil(capsys, umgebung, labor, tmp_path):
    labor.fehler("POST", "/token", 429, "over_request_rate_limit", email=email("labor-02"))
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01,labor-02")
    assert code == 1 and "labor-02: FEHLER: Anmeldung: Ratenbegrenzung des Servers (HTTP 429" in err
    assert "labor-01: Faktor eingerichtet" in out


def test_verify_abgelehnt_raeumt_den_faktor_auf(capsys, umgebung, labor, tmp_path):
    labor.fehler("POST", "/verify", 400, "mfa_verification_failed", email=email("labor-02"))
    datei = umschlag_datei(tmp_path)
    vorher = datei.read_bytes()
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-02")
    assert code == 1 and "Code bestätigen: abgelehnt (HTTP 400 mfa_verification_failed); Faktor wieder entfernt" in err
    assert labor.faktoren_von(email("labor-02")) == [] and labor.sitzungen_von(email("labor-02")) == 0
    assert seed_der_datei(datei, "labor-02") == "" and datei.read_bytes() == vorher


def test_fehler_beim_abmelden_raeumt_den_bestaetigten_faktor_auf(capsys, umgebung, labor, tmp_path):
    labor.fehler("POST", "/logout", 500, email=email("labor-01"))
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 1 and "Abmeldung: Serverfehler (HTTP 500); Faktor wieder entfernt" in err
    assert labor.faktoren_von(email("labor-01")) == [] and seed_der_datei(datei, "labor-01") == ""


def test_fehler_beim_speichern_raeumt_mit_neuer_anmeldung_auf(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    vorher = datei.read_bytes()
    echt = os.replace

    def nur_umschlag_scheitert(quelle, ziel):
        if Path(ziel).resolve() == datei.resolve():
            raise OSError("Platte voll " + PW[1])
        return echt(quelle, ziel)

    monkeypatch.setattr(os, "replace", nur_umschlag_scheitert)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 1 and "konnte nicht geschrieben werden" in err and "Faktor wieder entfernt" in err and PW[1] not in err
    assert labor.faktoren_von(email("labor-01")) == [] and labor.sitzungen_von(email("labor-01")) == 0
    assert datei.read_bytes() == vorher


def test_neu_mit_fehler_nach_dem_entfernen_loescht_den_alten_seed(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    einrichten(capsys, labor, datei, "--profile", "labor-01")
    echt = wlf.api.Verbindung.verify
    alt_id = labor.faktoren_von(email("labor-01"))[0]["id"]

    def zweite_bestaetigung_scheitert(self, *a, **k):
        if a[1] != alt_id:  # nur die Bestätigung des neuen Faktors scheitert, die des alten (aal2 zum Entfernen) gelingt
            labor.fehler("POST", "/verify", 400, "mfa_verification_failed", email=email("labor-01"), einmal=True)
        return echt(self, *a, **k)

    monkeypatch.setattr(wlf.api.Verbindung, "verify", zweite_bestaetigung_scheitert)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01", "--neu")
    assert code == 1 and "Faktor wieder entfernt" in err and "alte Seed wurde aus der Datei gelöscht" in err
    assert labor.faktoren_von(email("labor-01")) == [] and seed_der_datei(datei, "labor-01") == ""
    assert len(spalten(datei)["labor-01"]) == 3 and labor.sitzungen_von(email("labor-01")) == 0


def test_umleitung_wird_nicht_befolgt(capsys, umgebung, labor, tmp_path):
    labor.umleiten = True
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 1 and "Umleitung nicht befolgt" in err and ("GET", "/ziel") not in labor.anfragen


def test_kein_proxy_aus_der_umgebung(capsys, umgebung, labor, tmp_path, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    datei = umschlag_datei(tmp_path)
    assert einrichten(capsys, labor, datei, "--profile", "labor-01")[0] == 0


# ---------- Zeitversatz ----------

def test_zeitversatz_der_serveruhr_wird_ausgeglichen(capsys, umgebung, labor, tmp_path):
    labor.versatz = 70.0  # mehr als zwei Fenster: ein Code der lokalen Uhr würde abgelehnt
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01,labor-02")
    assert code == 0 and err == "" and out.count("Faktor eingerichtet, aal2 geprüft") == 2
    assert re.search(r"Hinweis: Die lokale Uhr weicht um etwa (69|70|71) Sekunden", out) and "NTP" in out
    seed = labor.faktoren_von(email("labor-01"))[0]["seed"]
    assert totp_unabhaengig(seed, time.time()) != totp_unabhaengig(seed, labor.jetzt()), "die Voraussetzung des Tests"


def test_ohne_date_kopf_wird_ohne_versatz_gerechnet(capsys, umgebung, labor, tmp_path):
    labor.mit_datum = False
    code, out, err = einrichten(capsys, labor, umschlag_datei(tmp_path), "--profile", "labor-01")
    assert code == 0 and "Hinweis" not in out


# ---------- Rechte und Dateien ----------

def test_rechte_der_datei_und_der_sicherung_sind_gesetzt(capsys, monkeypatch, umgebung, labor, tmp_path):
    monkeypatch.setattr(wlf, "RECHTE_SETZEN", um.rechte_nur_benutzer)  # die echte Funktion
    datei = umschlag_datei(tmp_path)
    assert einrichten(capsys, labor, datei, "--profile", "labor-01")[0] == 0
    for p in (datei, *sicherungen(datei)):
        if os.name == "posix":
            assert (p.stat().st_mode & 0o777) == 0o600
        else:
            import subprocess
            ausgabe = subprocess.run([os.path.join(os.environ["SystemRoot"], "System32", "icacls.exe"), str(p)], capture_output=True, text=True).stdout
            assert ausgabe.count("(F)") == 1 and "Everyone" not in ausgabe and "BUILTIN" not in ausgabe, ausgabe


def test_rechte_werden_vor_dem_schreiben_geprueft(capsys, monkeypatch, umgebung, labor, tmp_path):
    def nein(pfad):
        raise um.UmschlagFehler("Die Rechte der Datei konnten nicht gesetzt werden (icacls)")

    monkeypatch.setattr(wlf, "RECHTE_SETZEN", nein)
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei)
    assert code == 2 and "Rechte der Datei" in err and labor.anfragen == [], "der Server wird nicht angefasst"


# ---------- Eingabe und Einstellung ----------

def test_adresse_nur_https_mit_ca_oder_loopback(capsys, umgebung, labor, tmp_path, monkeypatch):
    monkeypatch.setattr(wlf, "CA_STANDARD", tmp_path / "gibt-es-nicht.crt")
    datei = umschlag_datei(tmp_path)
    for basis, muster in (("https://192.168.178.136:55443", "CA-Datei"), ("http://192.168.178.136:55321", "nicht lokal"),
                          ("https://name:pw@192.168.178.136:55443", "Zugangsdaten")):
        code, out, err = lauf(capsys, ["einrichten", "--basis", basis, "--umschlag-datei", str(datei)])
        assert code == 2 and muster in err and "name:pw" not in err
    assert labor.anfragen == []


def test_einstellung_aus_env_datei_und_umgebung(capsys, umgebung, labor, tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY")
    env = tmp_path / "test.env"
    env.write_text(f"# Kommentar\nexport SUPABASE_PUBLISHABLE_KEY='{SCHLUESSEL}'\n", encoding="utf-8")
    datei = umschlag_datei(tmp_path)
    code, out, err = lauf(capsys, ["einrichten", "--env-datei", str(env), "--basis", labor.basis, "--umschlag-datei", str(datei),
                                   "--profile", "labor-01"])
    assert code == 0 and "labor-01: Faktor eingerichtet" in out


def test_umschlag_datei_fehlt_oder_liegt_im_repo(capsys, umgebung, labor, tmp_path):
    code, out, err = lauf(capsys, ["pruefen", "--umschlag-datei", str(tmp_path / "nicht-da.txt")])
    assert code == 2 and "fehlt" in err
    repo_datei = Path(__file__).resolve().parent / "test_wamo_labor_faktoren_lauf.py"
    code, out, err = lauf(capsys, ["pruefen", "--umschlag-datei", str(repo_datei)])
    assert code == 2 and "nicht im Repo" in err


def test_ohne_option_gilt_der_standardpfad_im_heimordner(capsys, umgebung, tmp_path):
    code, out, err = lauf(capsys, ["pruefen"])
    assert code == 2 and ".wamo-labor" in err and str((tmp_path / "heim").resolve()) in err


@pytest.mark.parametrize("profile", ["labor-17", "labor-00", "schau", "labor-01,,labor-02", "labor-1", "labor-01;labor-02"])
def test_ungueltige_profile(capsys, umgebung, tmp_path, profile):
    datei = umschlag_datei(tmp_path)
    code, out, err = lauf(capsys, ["pruefen", "--profile", profile, "--umschlag-datei", str(datei)])
    assert code == 2 and "Ungültiges Profil" in err and out == ""


# ---------- pruefen ----------

def test_pruefen_meldet_nur_ja_oder_nein(capsys, umgebung, tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY")
    seeds = {"labor-01": seed_von("eins"), "labor-02": "kein-seed"}
    datei = umschlag_datei(tmp_path, umschlag_text(3, seeds=seeds))
    code, out, err = lauf(capsys, ["pruefen", "--profile", "labor-01,labor-02,labor-03,labor-04", "--umschlag-datei", str(datei)])
    assert code == 1 and err == ""
    assert out.splitlines() == [f"labor-0{n}: Seed in der Datei: {w}" for n, w in ((1, "ja"), (2, "nein"), (3, "nein"), (4, "nein"))]
    assert "Faktor" not in out, "die Datei belegt nur den Seed, nichts über den Faktor am Server"
    assert seed_von("eins") not in out and PW[1] not in out


def test_pruefen_alle_neun_in_ordnung(capsys, umgebung, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(9, seeds={k: seed_von(k) for k in KENNUNGEN}))
    code, out, err = lauf(capsys, ["pruefen", "--umschlag-datei", str(datei)])
    assert code == 0 and out.count(": ja") == 9 and ": nein" not in out


# ---------- codes ----------

def test_codes_gibt_genau_einen_code_mit_ablaufsekunden_aus(capsys, umgebung, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(3, seeds={"labor-03": RFC_SEED}))
    code, out, err = lauf(capsys, ["codes", "--profil", "labor-03", "--umschlag-datei", str(datei)], zeit=lambda: 59.0)
    assert (code, out, err) == (0, "287082 (noch 1 s)\n", "")
    code, out, err = lauf(capsys, ["codes", "--profil", "labor-03", "--umschlag-datei", str(datei)], zeit=lambda: 1234567890.0)
    assert out == f"005924 (noch {30 - 1234567890 % 30} s)\n"
    assert RFC_SEED not in out and PW[3] not in out


def test_codes_ohne_seed_gibt_keinen_code_aus(capsys, umgebung, tmp_path):
    datei = umschlag_datei(tmp_path)
    code, out, err = lauf(capsys, ["codes", "--profil", "labor-03", "--umschlag-datei", str(datei)])
    assert code == 1 and out == "" and "kein Seed" in err and PW[3] not in err


@pytest.mark.parametrize("profil", ["labor-01,labor-02", "labor-30", "alle"])
def test_codes_nur_fuer_genau_ein_profil(capsys, umgebung, tmp_path, profil):
    datei = umschlag_datei(tmp_path, umschlag_text(3, seeds={"labor-01": RFC_SEED, "labor-02": RFC_SEED}))
    code, out, err = lauf(capsys, ["codes", "--profil", profil, "--umschlag-datei", str(datei)])
    assert code == 2 and out == ""


def test_token_ohne_aal2_nach_der_bestaetigung_ist_ein_fehler(capsys, umgebung, labor, tmp_path):
    labor.aal2_nie = True  # der Server bestätigt den Faktor, das Token trägt aber nur aal1
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 1 and "nicht aal2" in err
    assert "der neue Faktor konnte NICHT entfernt werden, bitte als Admin am Server prüfen" in err, "ein bestätigter Faktor braucht zum Löschen aal2"
    assert seed_der_datei(datei, "labor-01") == "" and labor.sitzungen_von(email("labor-01")) == 0


def test_neues_token_ohne_aal2_ist_ein_fehler_und_raeumt_auf(capsys, umgebung, labor, tmp_path):
    labor.erneuern_aal1 = True
    datei = umschlag_datei(tmp_path)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01")
    assert code == 1 and "Ein neues Token trägt nicht aal2" in err and "Faktor wieder entfernt" in err
    assert labor.faktoren_von(email("labor-01")) == [] and seed_der_datei(datei, "labor-01") == ""


def test_alter_seed_laesst_sich_nicht_loeschen_wird_gemeldet(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    einrichten(capsys, labor, datei, "--profile", "labor-01")

    def nein(self, kennung):
        raise um.UmschlagFehler("nicht schreibbar")

    monkeypatch.setattr(um.Umschlag, "seed_entfernen", nein)
    echt = wlf.api.Verbindung.verify
    alt_id = labor.faktoren_von(email("labor-01"))[0]["id"]

    def zweite_scheitert(self, *a, **k):
        if a[1] != alt_id:  # nur die Bestätigung des neuen Faktors scheitert
            labor.fehler("POST", "/verify", 400, "mfa_verification_failed", email=email("labor-01"), einmal=True)
        return echt(self, *a, **k)

    monkeypatch.setattr(wlf.api.Verbindung, "verify", zweite_scheitert)
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-01", "--neu")
    assert code == 1 and "der alte Seed steht noch in der Datei und gilt nicht mehr" in err


def test_fehlende_zeile_im_echten_lauf_stoppt_die_anderen_nicht(capsys, umgebung, labor, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(2))
    code, out, err = einrichten(capsys, labor, datei, "--profile", "labor-05,labor-02")
    assert code == 1 and "labor-05: FEHLER: Für labor-05 gibt es keine Zeile" in err and "labor-02: Faktor eingerichtet" in out
    assert "1 eingerichtet, 0 übersprungen, 1 fehlgeschlagen" in out


def test_codes_ohne_zeile_gibt_keinen_code_aus(capsys, umgebung, tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(2))
    code, out, err = lauf(capsys, ["codes", "--profil", "labor-07", "--umschlag-datei", str(datei)])
    assert code == 1 and out == "" and "keine Zeile" in err


# ---------- Reihenfolge und Befehlszeile ----------

def test_seed_wird_erst_nach_dem_ende_der_sitzung_geschrieben(capsys, umgebung, labor, tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    stand = {}
    echt = um.Umschlag.seed_speichern

    def pruefen_und_schreiben(self, kennung, seed):
        stand["sitzungen"] = labor.sitzungen_von(email(kennung))
        stand["abgemeldet"] = ("POST", "/auth/v1/logout") in labor.anfragen
        stand["aal2_pruefung"] = labor.anfragen.count(("POST", "/auth/v1/token")) >= 2  # Anmeldung und Erneuern
        return echt(self, kennung, seed)

    monkeypatch.setattr(um.Umschlag, "seed_speichern", pruefen_und_schreiben)
    assert einrichten(capsys, labor, datei, "--profile", "labor-01")[0] == 0
    assert stand == {"sitzungen": 0, "abgemeldet": True, "aal2_pruefung": True}


def test_unter_windows_gehen_nur_pfade_an_icacls(capsys, monkeypatch, umgebung, labor, tmp_path):
    import subprocess
    monkeypatch.setattr(wlf, "RECHTE_SETZEN", um.rechte_nur_benutzer)
    befehle = []
    echt = subprocess.run

    def merken(befehl, *a, **k):
        befehle.append(list(befehl))
        return echt(befehl, *a, **k)

    monkeypatch.setattr(subprocess, "run", merken)
    datei = umschlag_datei(tmp_path)
    assert einrichten(capsys, labor, datei, "--profile", "labor-01")[0] == 0
    geheim = _alle_geheimnisse(labor, datei)
    for befehl in befehle:
        assert not any(g in teil for teil in befehl for g in geheim), "ein Geheimnis steht in einer Befehlszeile"


def test_keine_option_nimmt_ein_geheimnis_entgegen():
    optionen = {o for a in wlf._parser()._actions for o in a.option_strings}
    for sub in wlf._parser()._subparsers._group_actions[0].choices.values():
        optionen |= {o for a in sub._actions for o in a.option_strings}
    assert optionen and not [o for o in optionen if re.search(r"pass|seed|token|secret|geheim|schluessel|key|code", o)]


# ---------- Quelltext ----------

QUELLEN = sorted(p.name for p in Path(__file__).resolve().parent.glob("*labor_faktoren*.py"))


def test_quelltext_ohne_gedankenstriche_fremde_namen_und_lange_geheimnisse():
    assert len(QUELLEN) >= 7, "die Quelldateien wurden nicht gefunden"
    verboten = ["\u2014", "\u2013", "nie" + "eeki", "jar" + "vis", "prompt" + "control", "dami" + "con", "WMC" + "NL"]
    for name in QUELLEN:
        text = (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")
        for v in verboten:
            assert v.lower() not in text.lower(), f"{name} enthält {v!r}"
        assert len(text.splitlines()) < 800, f"{name} ist zu lang"
        for zeile in text.splitlines():
            assert not re.search(r"[\"'][A-Za-z0-9+/=]{32,}[\"']", zeile), f"{name}: lange Zeichenfolge in einer Zeile: {zeile[:60]}"


def test_funktionen_unter_50_zeilen():
    import ast
    for name in (n for n in QUELLEN if not n.startswith("test_")):
        baum = ast.parse((Path(__file__).resolve().parent / name).read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert knoten.end_lineno - knoten.lineno + 1 < 50, f"{name}: {knoten.name} hat {knoten.end_lineno - knoten.lineno + 1} Zeilen"
