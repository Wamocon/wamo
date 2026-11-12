"""Tests für scripts/wamo_labor_faktoren.py (zweiter Faktor der Laborprofile automatisch einrichten).

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q

Kein Netz außer einer Attrappe auf Loopback, kein ssh, nie die echte Umschlag-Datei: Jeder Test arbeitet mit einer Datei in einem
temporären Ordner, und USERPROFILE und HOME zeigen für die Dauer der Tests ebenfalls dorthin. Testwerte für Passwörter und Seeds
sind kurz und erfunden oder werden aus Zeichen berechnet, nie als lange Zeichenfolge eingetragen.
"""
from __future__ import annotations

import base64
import calendar
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_faktoren_totp as tt  # noqa: E402


@pytest.fixture(autouse=True)
def kein_echtes_home(monkeypatch, tmp_path):
    """Selbst ein Fehler im Skript trifft nie das echte Verzeichnis .wamo-labor des Benutzers."""
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("USERPROFILE", str(heim))
    monkeypatch.setenv("HOME", str(heim))


# ---------- TOTP nach RFC 6238 ----------

RFC_SEED = base64.b32encode(b"1234567890" * 2).decode()  # der SHA1-Schlüssel der Testvektoren, berechnet statt eingetragen


@pytest.mark.parametrize("zeit,erwartet", [
    (59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130"),
])
def test_totp_rfc6238_vektoren_acht_stellen(zeit, erwartet):
    assert tt.totp_code(RFC_SEED, zeit, stellen=8) == erwartet


@pytest.mark.parametrize("zeit,erwartet", [(59, "287082"), (1111111109, "081804"), (1234567890, "005924")])
def test_totp_sechs_stellen_sind_die_letzten_sechs_des_vektors(zeit, erwartet):
    assert tt.totp_code(RFC_SEED, zeit) == erwartet


def test_totp_fenstergrenzen_und_kleinschreibung():
    assert tt.totp_code(RFC_SEED, 59.9) == tt.totp_code(RFC_SEED, 30.0)
    assert tt.totp_code(RFC_SEED, 60.0) != tt.totp_code(RFC_SEED, 59.9)
    zerlegt = " ".join(RFC_SEED[i:i + 4] for i in range(0, len(RFC_SEED), 4)).lower()
    assert tt.totp_code(zerlegt, 1234567890) == "005924"


@pytest.mark.parametrize("seed,gueltig", [
    (RFC_SEED, True), (RFC_SEED.lower(), True), ("ABCD EFGH IJKL MNOP", True),
    ("", False), ("ABC", False), ("1" * 20, False), ("A" * 15, False), ("A" * 17, False), ("AB!D" * 6, False), (None, False),
])
def test_seed_gueltig(seed, gueltig):
    assert tt.seed_gueltig(seed) is gueltig


def test_ablauf_sekunden():
    assert tt.ablauf_sekunden(59.0) == 1
    assert tt.ablauf_sekunden(60.0) == 30
    assert tt.ablauf_sekunden(75.5) == 15


def _datum(sekunden: int) -> str:
    from email.utils import formatdate
    return formatdate(sekunden, usegmt=True)


def test_zeitversatz_server_vor_der_lokalen_uhr():
    lokal = float(calendar.timegm((2026, 11, 12, 10, 0, 0)))
    assert tt.zeitversatz(_datum(int(lokal) + 40), lokal + 0.5) == pytest.approx(40.0, abs=0.6)


def test_zeitversatz_server_hinter_der_lokalen_uhr():
    lokal = float(calendar.timegm((2026, 11, 12, 10, 0, 0)))
    assert tt.zeitversatz(_datum(int(lokal) - 90), lokal + 0.5) == pytest.approx(-90.0, abs=0.6)


@pytest.mark.parametrize("kopf", [None, "", "kein Datum", "Thu, 99 Foo 2026 10:00:00 GMT", "Thu, 01 Jan 1970 00:00:00 GMT"])
def test_zeitversatz_ohne_brauchbaren_kopf_ist_null(kopf):
    assert tt.zeitversatz(kopf, float(calendar.timegm((2026, 11, 12, 10, 0, 0)))) == 0.0


def test_frisches_fenster_wartet_am_fensterende():
    uhr = {"t": 59.0}
    waren = []

    def warte(s):
        waren.append(s)
        uhr["t"] += s

    t = tt.frisches_fenster(lambda: uhr["t"], warte)
    assert len(waren) == 1 and 1.0 <= waren[0] <= 2.5
    assert t >= 60.0


def test_frisches_fenster_wartet_nicht_mitten_im_fenster():
    waren = []
    assert tt.frisches_fenster(lambda: 70.0, waren.append) == 70.0
    assert waren == []


def _jwt(nutzlast: dict) -> str:
    def teil(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return ".".join((teil({"alg": "ES256"}), teil(nutzlast), "u"))


def test_jwt_claims_liest_aal():
    assert tt.jwt_claims(_jwt({"aal": "aal2", "sub": "x"}))["aal"] == "aal2"


@pytest.mark.parametrize("kaputt", ["", "a", "a.b", "a.%%%.c", ".".join(("a", base64.urlsafe_b64encode(b"[1]").decode(), "c")), None])
def test_jwt_claims_kaputt_ist_leer(kaputt):
    assert tt.jwt_claims(kaputt) == {}


def test_seed_aus_hash_berechnet():
    """Hilfe für spätere Tests: aus einem Namen wird ein 32 Zeichen langer Base32-Seed berechnet, nie eingetragen."""
    seed = base64.b32encode(hashlib.sha1(b"labor-01").digest()).decode()
    assert len(seed) == 32 and tt.seed_gueltig(seed)


# ---------- Umschlag-Datei: lesen, sichern, atomar schreiben, Rechte ----------

import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402

import wamo_labor_faktoren_umschlag as um  # noqa: E402

windows_nur_vorab = pytest.mark.skipif(os.name != "nt", reason="nur unter Windows")

PW = {n: f"Pw{n}-" + "ab" * 3 for n in range(1, 10)}  # kurze, erfundene Testpasswörter (nur Attrappe)


def seed_von(name: str) -> str:
    """Ein 32 Zeichen langer Base32-Seed, aus dem Namen berechnet."""
    return base64.b32encode(hashlib.sha1(name.encode()).digest()).decode()


def umschlag_text(n: int = 3, ende: str = "\n", seeds: dict | None = None) -> str:
    zeilen = []
    for i in range(1, n + 1):
        kennung = f"labor-{i:02d}"
        spalten = [kennung, f"{kennung}@wamo.test", PW[i]] + ([seeds[kennung]] if seeds and kennung in seeds else [])
        zeilen.append("\t".join(spalten))
    return ende.join(zeilen) + ende


def umschlag_datei(tmp_path, text: str | None = None, ende: str = "\n") -> Path:
    ordner = tmp_path / "umschlag"
    ordner.mkdir(exist_ok=True)
    datei = ordner / "umschlag-labor.txt"
    datei.write_bytes((umschlag_text(ende=ende) if text is None else text).encode("utf-8"))
    return datei


class Rechte:
    """Ersatz für das Setzen der Dateirechte: merkt sich Pfade und die Dateigröße zum Zeitpunkt des Aufrufs."""

    def __init__(self):
        self.aufrufe: list[tuple[Path, int]] = []

    def __call__(self, pfad: Path) -> None:
        self.aufrufe.append((Path(pfad), Path(pfad).stat().st_size))


def sicherungen(datei: Path) -> list[Path]:
    return sorted(p for p in datei.parent.iterdir() if ".sicherung-" in p.name)


def test_standard_pfad_liegt_im_heimordner():
    assert um.standard_pfad(Path("/heim/x")) == Path("/heim/x") / ".wamo-labor" / "umschlag-labor.txt"


def test_pfad_pruefen_lehnt_repo_und_fehlende_datei_ab(tmp_path):
    wurzel = Path(__file__).resolve().parent.parent
    with pytest.raises(um.UmschlagFehler, match="Repo"):
        um.pfad_pruefen(wurzel / "scripts" / "x.txt", wurzel)
    with pytest.raises(um.UmschlagFehler, match="fehlt"):
        um.pfad_pruefen(tmp_path / "nicht-da.txt", wurzel)
    datei = umschlag_datei(tmp_path)
    assert um.pfad_pruefen(datei, wurzel) == datei.resolve()


def test_zugang_liest_die_spalten(tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(seeds={"labor-02": seed_von("zwei")}), ende="\r\n")
    u = um.Umschlag(datei, rechte=Rechte())
    z1, z2 = u.zugang("labor-01"), u.zugang("labor-02")
    assert (z1.email, z1.passwort, z1.seed) == ("labor-01@wamo.test", PW[1], "")
    assert z2.seed == seed_von("zwei") and z2.passwort == PW[2]
    assert PW[1] not in repr(z1) and seed_von("zwei") not in repr(z2)


@pytest.mark.parametrize("text,muster", [
    ("", "keine Zeile"),
    ("labor-03\tlabor-03@wamo.test\tPw\nlabor-03\tlabor-03@wamo.test\tPx\n", "mehrere"),
    ("labor-03\tlabor-03@wamo.test\n", "unvollständig"),
    ("labor-03\tlabor-03@wamo.test\t\n", "unvollständig"),
    ("labor-03\t\tPw\n", "unvollständig"),
])
def test_zugang_fehler_ohne_geheimnisse(tmp_path, text, muster):
    u = um.Umschlag(umschlag_datei(tmp_path, text), rechte=Rechte())
    with pytest.raises(um.UmschlagFehler, match=muster) as e:
        u.zugang("labor-03")
    assert "Pw" not in str(e.value)


def test_zugang_ungueltiges_utf8(tmp_path):
    datei = umschlag_datei(tmp_path)
    datei.write_bytes(b"labor-03\t\xff\xfe\tPw\n")
    with pytest.raises(um.UmschlagFehler, match="UTF-8"):
        um.Umschlag(datei, rechte=Rechte()).zugang("labor-03")


@pytest.mark.parametrize("ende", ["\n", "\r\n"])
def test_seed_speichern_aendert_nur_die_eine_zeile(tmp_path, ende):
    rahmen = f"# Umschlag der Betreuer{ende}{ende}"
    original = rahmen + umschlag_text(ende=ende) + f"Notiz mit\tTabulator{ende}"
    datei = umschlag_datei(tmp_path, original)
    um.Umschlag(datei, rechte=Rechte()).seed_speichern("labor-02", seed_von("zwei"))
    erwartet = original.replace(f"labor-02@wamo.test\t{PW[2]}", f"labor-02@wamo.test\t{PW[2]}\t{seed_von('zwei')}")
    assert datei.read_bytes() == erwartet.encode()


def test_seed_speichern_ersetzt_alten_seed_und_behaelt_weitere_spalten(tmp_path):
    original = f"labor-01\tlabor-01@wamo.test\t{PW[1]}\t{seed_von('alt')}\tnotiz\n"
    datei = umschlag_datei(tmp_path, original)
    um.Umschlag(datei, rechte=Rechte()).seed_speichern("labor-01", seed_von("neu"))
    assert datei.read_text(encoding="utf-8") == f"labor-01\tlabor-01@wamo.test\t{PW[1]}\t{seed_von('neu')}\tnotiz\n"


def test_seed_speichern_ohne_schlusszeilenumbruch(tmp_path):
    datei = umschlag_datei(tmp_path, f"labor-01\tlabor-01@wamo.test\t{PW[1]}")
    um.Umschlag(datei, rechte=Rechte()).seed_speichern("labor-01", seed_von("a"))
    assert datei.read_text(encoding="utf-8") == f"labor-01\tlabor-01@wamo.test\t{PW[1]}\t{seed_von('a')}"


def test_seed_entfernen_leert_nur_die_vierte_spalte(tmp_path):
    datei = umschlag_datei(tmp_path, umschlag_text(seeds={"labor-01": seed_von("a"), "labor-02": seed_von("b")}))
    u = um.Umschlag(datei, rechte=Rechte())
    u.seed_entfernen("labor-01")
    assert u.zugang("labor-01").seed == "" and u.zugang("labor-02").seed == seed_von("b")
    assert datei.read_text(encoding="utf-8").splitlines()[0] == f"labor-01\tlabor-01@wamo.test\t{PW[1]}"


def test_sicherung_einmal_je_lauf_mit_zeitstempel_und_rechten(tmp_path):
    original = umschlag_text()
    datei = umschlag_datei(tmp_path, original)
    rechte = Rechte()
    u = um.Umschlag(datei, rechte=rechte, jetzt=lambda: 1_794_000_000.0)
    u.seed_speichern("labor-01", seed_von("a"))
    u.seed_speichern("labor-02", seed_von("b"))
    kopien = sicherungen(datei)
    assert len(kopien) == 1 and re.search(r"\.sicherung-\d{8}-\d{6}$", kopien[0].name)
    assert kopien[0].read_text(encoding="utf-8") == original  # der Stand vor dem ersten Schreiben
    assert kopien[0] in [p for p, _ in rechte.aufrufe]
    u2 = um.Umschlag(datei, rechte=rechte, jetzt=lambda: 1_794_000_000.0)  # derselbe Zeitstempel: kein Überschreiben
    u2.seed_speichern("labor-03", seed_von("c"))
    assert len(sicherungen(datei)) == 2


def test_rechte_werden_vor_dem_inhalt_gesetzt(tmp_path):
    datei = umschlag_datei(tmp_path)
    rechte = Rechte()
    um.Umschlag(datei, rechte=rechte).seed_speichern("labor-01", seed_von("a"))
    temporaer = [(p, g) for p, g in rechte.aufrufe if p != datei and ".sicherung-" not in p.name]
    assert temporaer and temporaer[0][1] == 0, "die Wegwerf-Datei war beim Setzen der Rechte noch leer"
    assert [p for p in datei.parent.iterdir() if p.name not in (datei.name,) and ".sicherung-" not in p.name] == []


def test_fehler_beim_ersetzen_laesst_das_original_und_raeumt_auf(tmp_path, monkeypatch):
    original = umschlag_text()
    datei = umschlag_datei(tmp_path, original)

    def kaputt(*a, **k):
        raise OSError("Platte voll " + PW[1])

    monkeypatch.setattr(os, "replace", kaputt)
    with pytest.raises(um.UmschlagFehler) as e:
        um.Umschlag(datei, rechte=Rechte()).seed_speichern("labor-01", seed_von("a"))
    assert PW[1] not in str(e.value) and seed_von("a") not in str(e.value)
    assert datei.read_text(encoding="utf-8") == original
    assert [p.name for p in datei.parent.iterdir() if ".sicherung-" not in p.name] == [datei.name]


def test_schreibprobe_legt_nichts_zurueck(tmp_path):
    datei = umschlag_datei(tmp_path)
    rechte = Rechte()
    um.Umschlag(datei, rechte=rechte).schreibprobe()
    assert [p.name for p in datei.parent.iterdir()] == [datei.name] and rechte.aufrufe


def test_schreibprobe_meldet_nicht_setzbare_rechte(tmp_path):
    def nein(pfad):
        raise um.UmschlagFehler("Rechte der Datei konnten nicht gesetzt werden")

    datei = umschlag_datei(tmp_path)
    with pytest.raises(um.UmschlagFehler, match="Rechte"):
        um.Umschlag(datei, rechte=nein).schreibprobe()
    assert [p.name for p in datei.parent.iterdir()] == [datei.name]


def test_nicht_setzbare_rechte_hinterlassen_keine_datei_und_aendern_nichts(tmp_path):
    def nein(pfad):
        raise um.UmschlagFehler("Die Rechte der Datei konnten nicht gesetzt werden")

    original = umschlag_text()
    datei = umschlag_datei(tmp_path, original)
    with pytest.raises(um.UmschlagFehler, match="Rechte"):
        um.Umschlag(datei, rechte=nein).seed_speichern("labor-01", seed_von("a"))
    assert [p.name for p in datei.parent.iterdir()] == [datei.name]
    assert datei.read_text(encoding="utf-8") == original


def test_seed_entfernen_leert_die_spalte_bei_weiteren_spalten(tmp_path):
    datei = umschlag_datei(tmp_path, f"labor-01\tlabor-01@wamo.test\t{PW[1]}\t{seed_von('a')}\tnotiz\n")
    um.Umschlag(datei, rechte=Rechte()).seed_entfernen("labor-01")
    assert datei.read_text(encoding="utf-8") == f"labor-01\tlabor-01@wamo.test\t{PW[1]}\t\tnotiz\n"


def test_unvollstaendige_zeile_wird_nicht_beschrieben(tmp_path):
    original = "labor-01\tlabor-01@wamo.test\n"
    datei = umschlag_datei(tmp_path, original)
    with pytest.raises(um.UmschlagFehler, match="unvollständig"):
        um.Umschlag(datei, rechte=Rechte()).seed_speichern("labor-01", seed_von("a"))
    assert datei.read_text(encoding="utf-8") == original and sicherungen(datei) == []


def test_nicht_lesbare_datei_ist_ein_fehler(tmp_path):
    with pytest.raises(um.UmschlagFehler, match="nicht lesbar"):
        um.Umschlag(tmp_path / "gibt-es-nicht.txt", rechte=Rechte()).zugang("labor-01")


def _os_open_scheitert_fuer(monkeypatch, teil: str):
    echt = os.open

    def open_(pfad, *a, **k):
        if teil in str(pfad):
            raise PermissionError("kein Zugriff " + PW[1])
        return echt(pfad, *a, **k)

    monkeypatch.setattr(os, "open", open_)


def test_sicherung_nicht_moeglich_aendert_nichts(tmp_path, monkeypatch):
    original = umschlag_text()
    datei = umschlag_datei(tmp_path, original)
    _os_open_scheitert_fuer(monkeypatch, ".sicherung-")
    with pytest.raises(um.UmschlagFehler, match="Sicherung") as e:
        um.Umschlag(datei, rechte=Rechte()).seed_speichern("labor-01", seed_von("a"))
    assert PW[1] not in str(e.value) and datei.read_text(encoding="utf-8") == original


def test_schreibprobe_ohne_schreibrecht_ist_ein_fehler(tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    _os_open_scheitert_fuer(monkeypatch, ".probe-")
    with pytest.raises(um.UmschlagFehler, match="nicht geschrieben werden"):
        um.Umschlag(datei, rechte=Rechte()).schreibprobe()


@windows_nur_vorab
def test_unter_windows_fehlendes_whoami_ist_ein_fehler(tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    um._benutzer_sid.cache_clear()
    monkeypatch.setattr(um, "_system32", lambda name: str(tmp_path / "gibt-es-nicht" / name))
    try:
        with pytest.raises(um.UmschlagFehler, match="Benutzer"):
            um.rechte_nur_benutzer(datei)
    finally:
        um._benutzer_sid.cache_clear()


@windows_nur_vorab
def test_unter_windows_scheiternder_icacls_ist_ein_fehler(tmp_path, monkeypatch):
    datei = umschlag_datei(tmp_path)
    um._benutzer_sid.cache_clear()
    echt = subprocess.run

    def icacls_scheitert(befehl, *a, **k):
        if str(befehl[0]).lower().endswith("icacls.exe"):
            return subprocess.CompletedProcess(befehl, 5, "", "")
        return echt(befehl, *a, **k)

    monkeypatch.setattr(subprocess, "run", icacls_scheitert)
    try:
        with pytest.raises(um.UmschlagFehler, match="icacls"):
            um.rechte_nur_benutzer(datei)
    finally:
        um._benutzer_sid.cache_clear()


posix_nur = pytest.mark.skipif(os.name != "posix", reason="nur unter Linux")
windows_nur = pytest.mark.skipif(os.name != "nt", reason="nur unter Windows")


@posix_nur
def test_rechte_unter_linux_sind_0600(tmp_path):
    datei = umschlag_datei(tmp_path)
    u = um.Umschlag(datei)
    u.seed_speichern("labor-01", seed_von("a"))
    for p in (datei, *sicherungen(datei)):
        assert (p.stat().st_mode & 0o777) == 0o600


def _icacls(pfad: Path) -> str:
    wurzel = os.environ.get("SystemRoot", r"C:\Windows")
    return subprocess.run([os.path.join(wurzel, "System32", "icacls.exe"), str(pfad)], capture_output=True, text=True).stdout


@windows_nur
def test_rechte_unter_windows_nur_der_benutzer(tmp_path):
    datei = umschlag_datei(tmp_path)
    u = um.Umschlag(datei)  # die echte Rechtefunktion (icacls)
    u.seed_speichern("labor-01", seed_von("a"))
    for p in (datei, *sicherungen(datei)):
        ausgabe = _icacls(p)
        assert ausgabe.count("(F)") == 1, ausgabe
        for fremd in ("Everyone", "BUILTIN", "NT AUTHORITY", "Authenticated Users"):
            assert fremd not in ausgabe, ausgabe
    assert u.zugang("labor-01").seed == seed_von("a")


# ---------- Verbindung zu GoTrue: Adresse, TLS, keine Umleitung, kein Proxy, Zeitversatz ----------

import http.server  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import wamo_labor_faktoren_api as api  # noqa: E402
from wamo_labor_faktoren_attrappe import GoTrueAttrappe, totp_unabhaengig  # noqa: E402

SCHLUESSEL = "anon-" + "a" * 8  # erfundener öffentlicher Schlüssel der Attrappe
EMAIL = "labor-03@wamo.test"


@pytest.fixture
def server():
    a = GoTrueAttrappe(schluessel=SCHLUESSEL)
    a.nutzer_anlegen(EMAIL, PW[3])
    a.starten()
    yield a
    a.beenden()


def verbindung(server, **kw) -> api.Verbindung:
    return api.Verbindung(server.basis, SCHLUESSEL, None, **kw)


@pytest.mark.parametrize("basis,ca,gut", [
    ("https://192.168.178.136:55443", True, True), ("https://192.168.178.136:55443/", True, True),
    ("https://192.168.178.136:55443", False, False), ("http://192.168.178.136:55321", True, False),
    ("http://127.0.0.1:5000", False, True), ("http://localhost:5000", False, True), ("http://[::1]:5000", False, True),
    ("ftp://127.0.0.1", False, False), ("https://", True, False), ("kein-schema", True, False),
    ("https://name:pw@192.168.178.136:55443", True, False), ("https://192.168.178.136:55443/auth", True, False),
    ("https://192.168.178.136:55443?x=1", True, False), ("http://127.0.0.1.example.org:5000", False, False),
    ("https://192.168.178.136:99999", True, False),
])
def test_basis_pruefen(basis, ca, gut):
    if gut:
        api.basis_pruefen(basis, ca)
    else:
        with pytest.raises(api.ApiFehler) as e:
            api.basis_pruefen(basis, ca)
        assert "name:pw" not in str(e.value)


def test_anmelden_liefert_sitzung_und_faktoren(server):
    fid, _ = server.faktor_anlegen(EMAIL, "verified")
    v = verbindung(server)
    s = v.anmelden(EMAIL, PW[3])
    assert s.access_token and s.refresh_token and s.aal == "aal1"
    assert [(f.id, f.status, f.typ) for f in v.faktoren(s.access_token)] == [(fid, "verified", "totp")]
    assert PW[3] not in repr(s) and s.access_token not in repr(s)


def test_anmelden_ohne_faktoren_im_nutzer(server):
    v = verbindung(server)
    assert v.faktoren(v.anmelden(EMAIL, PW[3]).access_token) == ()


def test_falsches_passwort_nennt_status_und_kennung_ohne_wert(server):
    with pytest.raises(api.ApiFehler) as e:
        verbindung(server).anmelden(EMAIL, "falsch-" + PW[3])
    assert (e.value.status, e.value.kennung) == (400, "invalid_credentials")
    assert "400" in str(e.value) and PW[3] not in str(e.value)


def test_ratenbegrenzung_wird_benannt(server):
    server.fehler("POST", "/token", 429, "over_request_rate_limit")
    with pytest.raises(api.ApiFehler, match="Ratenbegrenzung") as e:
        verbindung(server).anmelden(EMAIL, PW[3])
    assert e.value.status == 429


def test_antworttext_des_servers_gelangt_nie_in_den_fehler(server):
    server.fehler("POST", "/token", 400, koerper={"error_code": "x y", "msg": "GEHEIMER-Satz " + PW[3]})
    with pytest.raises(api.ApiFehler) as e:
        verbindung(server).anmelden(EMAIL, PW[3])
    assert "GEHEIMER" not in str(e.value) and PW[3] not in str(e.value)


def test_zeitueberschreitung_ist_ein_fehler(server):
    server.verzoegerung = 1.0
    with pytest.raises(api.ApiFehler, match="Zeitüberschreitung"):
        verbindung(server, zeitlimit=0.2).anmelden(EMAIL, PW[3])


def test_unbrauchbare_antwort_ist_ein_fehler(server):
    server.fehler("POST", "/token", 200, koerper=[1, 2])
    with pytest.raises(api.ApiFehler, match="ungültige Antwort"):
        verbindung(server).anmelden(EMAIL, PW[3])


def test_keine_verbindung_ist_ein_fehler(server):
    server.beenden()
    with pytest.raises(api.ApiFehler, match="keine Verbindung"):
        verbindung(server).anmelden(EMAIL, PW[3])


def test_zeitversatz_kommt_aus_dem_date_kopf(server):
    server.versatz = 70.0
    v = verbindung(server)
    assert v.versatz == 0.0
    v.anmelden(EMAIL, PW[3])
    assert v.versatz == pytest.approx(70.0, abs=1.6)
    assert v.jetzt() == pytest.approx(time.time() + 70.0, abs=1.6)


def test_ohne_date_kopf_bleibt_der_versatz_null(server):
    server.mit_datum = False
    v = verbindung(server)
    v.anmelden(EMAIL, PW[3])
    assert v.versatz == 0.0


def test_umleitung_wird_nicht_befolgt(server):
    server.umleiten = True
    with pytest.raises(api.ApiFehler, match="Umleitung"):
        verbindung(server).anmelden(EMAIL, PW[3])
    assert ("GET", "/ziel") not in server.anfragen and server.anfragen == [("POST", "/auth/v1/token")]


def test_kein_proxy_aus_der_umgebung(server, monkeypatch):
    zugriffe = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            zugriffe.append(self.path)
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.server_address[1]}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        import urllib.request
        assert urllib.request.getproxies().get("http"), "Voraussetzung: die Umgebung nennt einen Proxy"
        assert verbindung(server).anmelden(EMAIL, PW[3]).access_token
    finally:
        proxy.shutdown()
        proxy.server_close()
    assert zugriffe == []


def test_faktor_ablauf_bis_aal2_und_abmeldung(server):
    v = verbindung(server)
    s = v.anmelden(EMAIL, PW[3])
    neu = v.faktor_anlegen(s.access_token, "WAMO-test")
    assert tt.seed_gueltig(neu.seed) and neu.seed not in repr(neu)
    c = v.challenge(s.access_token, neu.id)
    s2 = v.verify(s.access_token, neu.id, c, totp_unabhaengig(neu.seed, server.jetzt()))
    assert s2.aal == "aal2"
    s3 = v.erneuern(s2.refresh_token)
    assert s3.aal == "aal2" and s3.access_token != s2.access_token
    v.abmelden(s3.access_token)
    with pytest.raises(api.ApiFehler) as e:
        v.faktoren(s3.access_token)
    assert e.value.status == 401


def test_verify_mit_falschem_code_wird_abgelehnt(server):
    v = verbindung(server)
    s = v.anmelden(EMAIL, PW[3])
    neu = v.faktor_anlegen(s.access_token, "WAMO-test")
    c = v.challenge(s.access_token, neu.id)
    with pytest.raises(api.ApiFehler) as e:
        v.verify(s.access_token, neu.id, c, "000000")
    assert e.value.kennung == "mfa_verification_failed" and "000000" not in str(e.value)


def test_bestaetigter_faktor_braucht_zum_loeschen_aal2(server):
    fid, seed = server.faktor_anlegen(EMAIL, "verified")
    v = verbindung(server)
    s = v.anmelden(EMAIL, PW[3])
    with pytest.raises(api.ApiFehler) as e:
        v.faktor_loeschen(s.access_token, fid)
    assert e.value.kennung == "insufficient_aal"
    s2 = v.verify(s.access_token, fid, v.challenge(s.access_token, fid), totp_unabhaengig(seed, server.jetzt()))
    v.faktor_loeschen(s2.access_token, fid)
    assert server.faktoren_von(EMAIL) == []


def test_faktorkennung_aus_der_antwort_wird_geprueft(server):
    server.fehler("POST", "/auth/v1/factors", 200, koerper={"id": "../admin", "totp": {"secret": seed_von("x")}})
    v = verbindung(server)
    s = v.anmelden(EMAIL, PW[3])
    with pytest.raises(api.ApiFehler, match="ungültige Antwort"):
        v.faktor_anlegen(s.access_token, "WAMO-test")


def test_challenge_ohne_kennung_und_ungueltige_faktorkennung_sind_fehler(server):
    fid, _ = server.faktor_anlegen(EMAIL, "unverified")
    server.fehler("POST", "/challenge", 200, koerper={"type": "totp"})
    v = verbindung(server)
    s = v.anmelden(EMAIL, PW[3])
    with pytest.raises(api.ApiFehler, match="ungültige Antwort"):
        v.challenge(s.access_token, fid)
    with pytest.raises(api.ApiFehler, match="Ungültige Faktorkennung"):
        v.faktor_loeschen(s.access_token, "../admin")


def test_ungueltiger_seed_aus_der_antwort_wird_abgelehnt(server):
    server.fehler("POST", "/auth/v1/factors", 200, koerper={"id": "faktor-0123456789ab", "totp": {"secret": "kurz"}})
    v = verbindung(server)
    s = v.anmelden(EMAIL, PW[3])
    with pytest.raises(api.ApiFehler, match="ungültige Antwort"):
        v.faktor_anlegen(s.access_token, "WAMO-test")


def _openssl() -> str | None:
    return shutil.which("openssl") or next((p for p in (r"C:\Program Files\Git\usr\bin\openssl.exe",) if os.path.exists(p)), None)


@pytest.fixture(scope="module")
def zertifikate(tmp_path_factory):
    """Eine Test-CA und zwei Serverzertifikate (Adresse 127.0.0.1 passend und unpassend), nur für diesen Testlauf erzeugt."""
    ossl = _openssl()
    if not ossl:
        pytest.skip("openssl fehlt")
    d = tmp_path_factory.mktemp("tls")

    def lauf(*args):
        subprocess.run([ossl, *args], check=True, capture_output=True, cwd=d)

    kurve = ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes")
    lauf("req", "-x509", *kurve, "-keyout", "ca.key", "-out", "ca.crt", "-subj", "/CN=Test-CA", "-days", "2")
    for name, san in (("gut", "IP:127.0.0.1"), ("falsch", "IP:10.9.8.7")):
        (d / f"{name}.cnf").write_text(f"subjectAltName={san}\n")
        lauf("req", *kurve, "-keyout", f"{name}.key", "-out", f"{name}.csr", "-subj", "/CN=server")
        lauf("x509", "-req", "-in", f"{name}.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-out", f"{name}.crt",
             "-days", "2", "-extfile", f"{name}.cnf")
    return d


def _tls_server(zertifikate, name: str) -> GoTrueAttrappe:
    a = GoTrueAttrappe(schluessel=SCHLUESSEL)
    a.nutzer_anlegen(EMAIL, PW[3])
    a.starten(tls=(str(zertifikate / f"{name}.crt"), str(zertifikate / f"{name}.key")))
    return a


def test_tls_mit_der_ca_datei_gelingt(zertifikate):
    a = _tls_server(zertifikate, "gut")
    try:
        v = api.Verbindung(a.basis, SCHLUESSEL, zertifikate / "ca.crt")
        assert v.anmelden(EMAIL, PW[3]).access_token
    finally:
        a.beenden()


def test_tls_pruefung_ist_streng(zertifikate):
    kontext = api.tls_kontext(zertifikate / "ca.crt")
    import ssl
    assert kontext.verify_mode == ssl.CERT_REQUIRED and kontext.check_hostname is True


def test_tls_unbekannte_ca_wird_abgelehnt(zertifikate):
    a = _tls_server(zertifikate, "gut")
    try:
        fremde_ca = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "wamo-ca.crt"
        with pytest.raises(api.ApiFehler, match="TLS") as e:
            api.Verbindung(a.basis, SCHLUESSEL, fremde_ca).anmelden(EMAIL, PW[3])
        assert PW[3] not in str(e.value)
    finally:
        a.beenden()


def test_tls_falscher_hostname_wird_abgelehnt(zertifikate):
    a = _tls_server(zertifikate, "falsch")
    try:
        with pytest.raises(api.ApiFehler, match="TLS"):
            api.Verbindung(a.basis, SCHLUESSEL, zertifikate / "ca.crt").anmelden(EMAIL, PW[3])
    finally:
        a.beenden()


def test_unlesbare_ca_datei_ist_ein_fehler(tmp_path):
    kaputt = tmp_path / "ca.crt"
    kaputt.write_text("kein Zertifikat")
    with pytest.raises(api.ApiFehler, match="CA-Datei"):
        api.Verbindung("https://192.168.178.136:55443", SCHLUESSEL, kaputt)
    with pytest.raises(api.ApiFehler, match="CA-Datei"):
        api.Verbindung("https://192.168.178.136:55443", SCHLUESSEL, tmp_path / "fehlt.crt")
