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
    ("", False), ("ABC", False), ("1" * 20, False), ("A" * 15, False), ("AB!D" * 6, False), (None, False),
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
