"""Tests für das Einrichtungsskript der Labor-Daten (infra/sokrates2/wamo-labor-daten-einrichten.sh, Testlabor 3.6).

Das Skript läuft hier NICHT gegen ein System. Es läuft im Probelauf (WAMO_LABOR_DATEN_WURZEL) in einem Wegwerf-Ordner, ohne root und ohne Benutzerwechsel, mit
dem echten Lader (labor_daten.py) und dem Interpreter dieser Testumgebung. Das geht unter Linux und mit Git Bash unter Windows. Was echte Besitzer, echte
Rechte oder den Benutzer wamo-core braucht (Herkunftsschutz mit fremdem Besitzer, Lader als wamo-core), ist nur am Server prüfbar.

Ausführen:  python -m pytest -p no:cacheprovider -q test_labor_daten_skript.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import labor_daten as ld

VOICE_CORE = Path(__file__).resolve().parent
INFRA = VOICE_CORE.parent / "infra" / "sokrates2"
SKRIPT = INFRA / "wamo-labor-daten-einrichten.sh"
QUELLE = INFRA / "labor-daten"
STRENG = "persona-zusatz.streng.md"
ALLE = (*ld.DATEIEN_ALLE, STRENG)
LINUX = sys.platform.startswith("linux")
STAGE_ORT = Path(tempfile.gettempdir()) if os.name == "nt" else Path("/tmp")


def _bash() -> str | None:
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    return next((k for k in kandidaten if k and Path(k).exists()), None)


BASH = _bash()
braucht_bash = pytest.mark.skipif(BASH is None, reason="bash fehlt")
braucht_linux = pytest.mark.skipif(not LINUX, reason="Dateirechte und Besitzer nur unter Linux prüfbar")


def lies(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8")


def code(text: str) -> str:
    """Das Skript ohne Kommentarzeilen."""
    return "\n".join(z for z in text.splitlines() if not z.lstrip().startswith("#"))


def sha(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------- Textprüfungen


@pytest.fixture(scope="module")
def text() -> str:
    return lies(SKRIPT)


def test_zeilenenden_lf_keine_gedankenstriche_kein_eval_set_euo_pipefail(text):
    roh = SKRIPT.read_bytes()
    assert b"\r" not in roh
    for strich in (chr(0x2014), chr(0x2013)):
        assert strich not in text
    assert "set -euo pipefail" in text
    assert not re.search(r"\beval\b", code(text))
    assert not re.search(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|\$\w+\s)", code(text))


def test_zeilenenden_der_labor_daten_lf():
    for pfad in QUELLE.iterdir():
        assert b"\r" not in pfad.read_bytes(), pfad.name


@braucht_bash
def test_bash_n():
    r = subprocess.run([BASH, "-n", str(SKRIPT)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr


def test_die_dateiliste_des_skripts_stimmt_mit_dem_lader_und_dem_ordner_ueberein(text):
    daten = re.search(r"^DATEN=\((.*?)\)", text, re.M).group(1).split()
    assert [*daten] == list(ld.DATEIEN_DATEN)
    assert re.search(r"^REGEL=persona-zusatz\.md$", text, re.M) and ld.DATEI_REGELN == "persona-zusatz.md"
    assert re.search(rf"^STRENG={re.escape(STRENG)}$", text, re.M)
    assert {p.name for p in QUELLE.iterdir()} == {*ALLE, "README.md"}  # die README wird nie installiert


def test_nur_root_und_der_herkunftsschutz_kommen_vor_jedem_zugriff(text):
    wurzel_zweig = text.index('if [ -z "$WURZEL" ]; then\n  [ "$(id -u)" -eq 0 ] || fehler')
    root = text.index('[ "$(id -u)" -eq 0 ] || fehler "Bitte als root ausführen (sudo)."')
    herkunft_aufruf = text.index("\n  pruefe_herkunft\n")
    erster_zugriff = text.index('case "$BEFEHL" in\n  einrichten) einrichten ;;')
    assert wurzel_zweig < root < herkunft_aufruf < erster_zugriff
    assert text.index("pruefe_herkunft() {") < herkunft_aufruf
    funktion = re.search(r"^pruefe_herkunft\(\) \{.*?^\}", text, re.M | re.S).group(0)
    for muss in ("-type l", "8#022", "SUDO_UID", "Unsicherer Ablageort"):
        assert muss in funktion, muss


def test_das_skript_fasst_den_kern_nie_an(text):
    ohne = code(text)
    for verboten in ("systemctl", "docker", "daemon-reload", "wamo-core-slots", "umschalten", "restart", "kill ", "service ", "core.env\""):
        assert verboten not in ohne.replace('KERN_ENV="$WURZEL/etc/wamo/core.env"', ""), verboten
    # core.env wird nur gelesen (grep, sed), nie geschrieben
    assert not re.search(r">\s*\"?\$KERN_ENV|install[^\n]*\$KERN_ENV|tee[^\n]*\$KERN_ENV|sed -i[^\n]*\$KERN_ENV", ohne)


def test_das_ziel_ist_ein_unterordner_nie_etc_wamo_selbst(text):
    assert 'ZIEL="$WURZEL/etc/wamo/labor"' in text
    assert 'SICHERUNG_BASIS="$WURZEL/var/backups/wamo-labor-daten"' in text
    assert "-o root -g root" in text and "-m 0644" in text and "-m 0755" in text


def test_der_lader_laeuft_isoliert_und_als_kern_nicht_als_root(text):
    assert "PY_OPT=(-X utf8 -B -I)" in text
    assert 'runuser -u "$KERN_BENUTZER" -- env -i' in text
    assert "KERN_BENUTZER=wamo-core" in text
    assert "nur_root_schreibbar" in text and "labor_daten.py" in text


# ---------------------------------------------------------------- Probelauf


class Welt:
    def __init__(self, ordner: Path, shell: str, infra: Path):
        self.ordner, self.shell, self.infra = ordner, shell, infra
        self.w = ordner / "w"
        self.ziel = self.w / "etc" / "wamo" / "labor"
        self.sicherungen_ordner = self.w / "var" / "backups" / "wamo-labor-daten"
        self.quelle = infra / "labor-daten"

    def lauf(self, *args: str, python: str | None = None, extra: dict | None = None, wurzel: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, "WAMO_LABOR_DATEN_LADER": VOICE_CORE.as_posix(),
               "WAMO_LABOR_DATEN_PYTHON": python or Path(sys.executable).as_posix(), **(extra or {})}
        if wurzel:
            env["WAMO_LABOR_DATEN_WURZEL"] = self.shell
        else:
            env.pop("WAMO_LABOR_DATEN_WURZEL", None)
        return subprocess.run([BASH, str(self.infra / SKRIPT.name), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=180)

    def ausgabe(self, r: subprocess.CompletedProcess) -> str:
        return r.stdout + r.stderr

    def sicherungen(self) -> list[str]:
        return sorted(p.name for p in self.sicherungen_ordner.glob("*")) if self.sicherungen_ordner.is_dir() else []

    def dateien(self, ordner: Path | None = None) -> dict[str, bytes]:
        basis = ordner or self.w
        return {p.relative_to(basis).as_posix(): p.read_bytes() for p in sorted(basis.rglob("*")) if p.is_file()}

    def schreiben(self, name: str, inhalt: str | bytes) -> None:
        (self.quelle / name).write_bytes(inhalt if isinstance(inhalt, bytes) else inhalt.encode("utf-8"))


@pytest.fixture
def welt():
    """Wegwerf-Ordner mit Wurzel (nur /etc/wamo ist da) und einer Kopie von Skript und Labor-Daten, die der Test ändern darf. Unter Windows
    ist /tmp der Temp-Ordner von Git Bash."""
    ordner = Path(tempfile.mkdtemp(prefix="wamo-labor-probe-"))
    shell = f"/tmp/{ordner.name}/w" if os.name == "nt" else str(ordner / "w")
    (ordner / "w" / "etc" / "wamo").mkdir(parents=True)
    infra = ordner / "infra"
    infra.mkdir()
    shutil.copy2(SKRIPT, infra / SKRIPT.name)
    shutil.copytree(QUELLE, infra / "labor-daten")
    stage_vorher = set(STAGE_ORT.glob("wamo-labor-stage.*"))
    try:
        yield Welt(ordner, shell, infra)
    finally:
        shutil.rmtree(ordner, ignore_errors=True)
    assert set(STAGE_ORT.glob("wamo-labor-stage.*")) == stage_vorher, "der Wegwerf-Ordner des Laders bleibt nicht liegen"


def ablehnender_python(welt: Welt) -> str:
    """Ein Interpreter, der die Probe am Wegwerf-Ordner und das Prüfen von loguru an den echten weitergibt, die Probe am Ziel aber ablehnt."""
    att = welt.ordner / "att"
    att.mkdir()
    datei = att / "python-ablehnen"
    datei.write_bytes(b'#!/bin/sh\ncase "$7" in\n  *wamo-labor-stage*|"") exec "$ECHT_PYTHON" "$@" ;;\n'
                      b'  *) echo "    kontakte.md: ABGELEHNT (zeichen)"; exit 1 ;;\nesac\n')
    datei.chmod(0o755)
    return f"/tmp/{welt.ordner.name}/att/python-ablehnen" if os.name == "nt" else str(datei)


@braucht_bash
class TestProbelauf:
    def test_trockenlauf_aendert_nichts(self, welt):
        vorher = welt.dateien()
        r = welt.lauf("einrichten", "--trocken")
        assert r.returncode == 0, welt.ausgabe(r)
        assert "[trocken]" in r.stdout and "Trockenlauf: es wurde nichts geändert." in r.stdout
        assert welt.dateien() == vorher and not welt.ziel.exists() and welt.sicherungen() == []
        assert "ABGELEHNT" not in r.stdout and "gelesen, Prüfsumme" in r.stdout  # der echte Lader hat die Lieferung geprüft

    def test_einrichten_legt_acht_dateien_wie_die_quelle_an_und_sichert_den_leeren_stand(self, welt):
        r = welt.lauf("einrichten")
        assert r.returncode == 0, welt.ausgabe(r)
        assert sorted(p.name for p in welt.ziel.iterdir()) == sorted(ALLE)
        for name in ALLE:
            assert (welt.ziel / name).read_bytes() == (QUELLE / name).read_bytes(), name
        assert "README.md" not in {p.name for p in welt.ziel.iterdir()}
        assert sorted(p.name for p in (welt.w / "etc" / "wamo").iterdir()) == ["labor"]  # nie etwas direkt in /etc/wamo
        (sicherung,) = welt.sicherungen()
        assert re.fullmatch(r"\d{8}-\d{6}", sicherung)
        assert "Dateien vorher: 0" in lies(welt.sicherungen_ordner / sicherung / "VORHER")
        assert "Eingerichtet." in r.stdout

    @braucht_linux
    def test_rechte_ordner_0755_dateien_0644(self, welt):
        assert welt.lauf("einrichten").returncode == 0
        assert stat.S_IMODE(welt.ziel.stat().st_mode) == 0o755
        for name in ALLE:
            assert stat.S_IMODE((welt.ziel / name).stat().st_mode) == 0o644, name
        assert not any(p.is_symlink() for p in welt.ziel.iterdir())

    def test_ein_zweiter_lauf_aendert_nichts(self, welt):
        assert welt.lauf("einrichten").returncode == 0
        vorher, sicherungen = welt.dateien(), welt.sicherungen()
        r = welt.lauf("einrichten")
        assert r.returncode == 0, welt.ausgabe(r)
        assert "Alles unverändert, nichts zu tun." in r.stdout
        assert welt.dateien() == vorher and welt.sicherungen() == sicherungen

    def test_die_fassung_standard_streng_und_die_bisherige_bleibt_erhalten(self, welt):
        assert welt.lauf("einrichten", "--persona", "streng").returncode == 0
        assert (welt.ziel / "persona-zusatz.md").read_bytes() == (QUELLE / STRENG).read_bytes()
        assert (welt.ziel / STRENG).read_bytes() == (QUELLE / STRENG).read_bytes()  # die vorbereitete Fassung liegt immer mit da
        r = welt.lauf("einrichten")  # ohne Angabe bleibt die strenge Fassung
        assert "Fassung der Regeln: streng" in r.stdout and "Alles unverändert" in r.stdout
        assert welt.lauf("einrichten", "--persona", "standard").returncode == 0
        assert (welt.ziel / "persona-zusatz.md").read_bytes() == (QUELLE / "persona-zusatz.md").read_bytes()
        assert len(welt.sicherungen()) == 2  # der zweite Lauf ohne Änderung legt keine an

    def test_zeilenenden_der_quelle_werden_auf_lf_gebracht(self, welt):
        welt.schreiben("kontakte.md", (QUELLE / "kontakte.md").read_bytes().replace(b"\n", b"\r\n"))
        assert welt.lauf("einrichten").returncode == 0
        assert b"\r" not in (welt.ziel / "kontakte.md").read_bytes()
        assert (welt.ziel / "kontakte.md").read_bytes() == (QUELLE / "kontakte.md").read_bytes()
        assert "gleich der Lieferung" in welt.lauf("status").stdout

    def test_eine_abgelehnte_lieferung_aendert_nichts(self, welt):
        welt.schreiben("kontakte.md", "Anna Test, anna.test@wamo.test\x1b[31m")  # Steuerzeichen: der Lader lehnt ab
        r = welt.lauf("einrichten")
        assert r.returncode == 1, welt.ausgabe(r)
        assert "kontakte.md: ABGELEHNT (zeichen)" in r.stdout and "Nichts wurde geändert." in welt.ausgabe(r)
        assert not welt.ziel.exists() and welt.sicherungen() == []

    def test_eine_zu_grosse_lieferung_wird_abgelehnt(self, welt):
        welt.schreiben("kontakte.md", "k" * 3000)  # mit den übrigen Dateien über 6000 Zeichen: die letzte Datei fiele weg
        r = welt.lauf("einrichten")
        assert r.returncode == 1 and "ABGELEHNT" in r.stdout and not welt.ziel.exists()

    def test_lehnt_der_lader_am_ziel_ab_kommt_der_alte_stand_zurueck(self, welt):
        assert welt.lauf("einrichten").returncode == 0
        alt = welt.dateien(welt.ziel)
        welt.schreiben("ausgang.md", "Danke für Ihren Besuch. Mehr zum Seminar erhalten Sie am Stand von WAMOCON.")
        r = welt.lauf("einrichten", python=ablehnender_python(welt), extra={"ECHT_PYTHON": Path(sys.executable).as_posix()})
        assert r.returncode == 1, welt.ausgabe(r)
        assert "alles wird zurückgenommen" in welt.ausgabe(r) and "Zurückgenommen auf den Stand vor dem Lauf" in welt.ausgabe(r)
        assert welt.dateien(welt.ziel) == alt, "Dateien und Inhalt wie vor dem Lauf"
        assert not list(welt.ziel.glob(".*.neu"))
        assert len(welt.sicherungen()) == 2

    def test_lehnt_der_lader_bei_der_ersten_einrichtung_am_ziel_ab_bleibt_nichts_zurueck(self, welt):
        r = welt.lauf("einrichten", python=ablehnender_python(welt), extra={"ECHT_PYTHON": Path(sys.executable).as_posix()})
        assert r.returncode == 1, welt.ausgabe(r)
        assert not welt.ziel.exists(), "weder Dateien noch der leere Ordner"
        assert (welt.w / "etc" / "wamo").is_dir()

    def test_rueckweg_stellt_den_vorigen_stand_wieder_her(self, welt):
        assert welt.lauf("einrichten").returncode == 0
        erster = welt.dateien(welt.ziel)
        welt.schreiben("ausgang.md", "Danke für Ihren Besuch. Mehr zum Seminar erhalten Sie am Stand von WAMOCON, nach dem Vortrag.")
        assert welt.lauf("einrichten").returncode == 0
        assert welt.dateien(welt.ziel) != erster
        r = welt.lauf("rueckweg")
        assert r.returncode == 0, welt.ausgabe(r)
        assert welt.dateien(welt.ziel) == erster
        assert any(name.endswith(".vor-rueckweg") for name in welt.sicherungen()), "der Stand vor dem Rückweg bleibt liegen"

    def test_rueckweg_nach_der_ersten_einrichtung_entfernt_alles(self, welt):
        assert welt.lauf("einrichten").returncode == 0
        assert welt.lauf("rueckweg").returncode == 0
        assert not welt.ziel.exists()
        assert (welt.w / "etc" / "wamo").is_dir()

    def test_rueckweg_trocken_aendert_nichts_und_ohne_sicherung_ist_es_eine_vorbedingung(self, welt):
        r = welt.lauf("rueckweg")
        assert r.returncode == 3 and "keine vollständige Sicherung" in welt.ausgabe(r)
        assert welt.lauf("einrichten").returncode == 0
        vorher = welt.dateien()
        r = welt.lauf("rueckweg", "--trocken")
        assert r.returncode == 0 and "Trockenlauf" in r.stdout and welt.dateien() == vorher

    def test_eine_unvollstaendige_sicherung_ohne_vorher_zaehlt_nicht(self, welt):
        assert welt.lauf("einrichten").returncode == 0
        (welt.sicherungen_ordner / "29990101-000000").mkdir()  # jünger als alles, aber nie abgeschlossen
        assert welt.lauf("rueckweg").returncode == 0
        assert not welt.ziel.exists(), "gewählt wurde die vollständige Sicherung der ersten Einrichtung"

    def test_status_zeigt_pruefsummen_und_vergleich_nie_inhalte(self, welt):
        assert welt.lauf("einrichten").returncode == 0
        r = welt.lauf("status")
        assert r.returncode == 0, welt.ausgabe(r)
        for name in ALLE:
            assert f"{name}: {sha(welt.ziel / name)}, gleich der Lieferung" in r.stdout, name
        assert "Fassung der Regeln: standard" in r.stdout and "Urteil des Laders" in r.stdout
        for name in ALLE:
            for zeile in lies(QUELLE / name).splitlines():
                if len(zeile.strip()) > 25:
                    assert zeile.strip() not in welt.ausgabe(r), f"Inhalt in der Ausgabe: {name}"
        (welt.ziel / "kontakte.md").write_text("geändert", encoding="utf-8")
        assert "kontakte.md" in welt.lauf("status").stdout and "WEICHT von der Lieferung ab" in welt.lauf("status").stdout

    def test_status_ohne_installation_und_ohne_lader(self, welt):
        r = welt.lauf("status")
        assert r.returncode == 0 and "fehlt" in r.stdout and not welt.ziel.exists() and welt.sicherungen() == []

    def test_fehlt_loguru_bricht_es_vor_jeder_aenderung_mit_klarer_meldung_ab(self, welt):
        r = welt.lauf("einrichten", python="false")
        assert r.returncode == 3, welt.ausgabe(r)
        assert "loguru" in r.stderr and "Nichts wurde geändert" in r.stderr
        assert not welt.ziel.exists() and welt.sicherungen() == []

    def test_fehlt_eine_datei_der_lieferung_ist_es_eine_vorbedingung(self, welt):
        (welt.quelle / "ausgang.md").unlink()
        r = welt.lauf("einrichten")
        assert r.returncode == 3 and "ausgang.md" in r.stderr and not welt.ziel.exists()

    def test_ein_fehlendes_etc_wamo_ist_eine_vorbedingung(self, welt):
        (welt.w / "etc" / "wamo").rmdir()
        r = welt.lauf("einrichten")
        assert r.returncode == 3 and not welt.ziel.exists()

    def test_ein_link_im_ziel_wird_nie_angefasst(self, welt):
        assert welt.lauf("einrichten").returncode == 0
        geheim = welt.ordner / "geheim.env"
        geheim.write_text("SCHLUESSEL=GEHEIMER-WERT", encoding="utf-8")
        (welt.ziel / "kontakte.md").unlink()
        try:
            os.symlink(geheim, welt.ziel / "kontakte.md")
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks sind auf dieser Maschine nicht erlaubt")
        r = welt.lauf("einrichten")
        assert r.returncode == 3 and "Link" in r.stderr
        assert geheim.read_text(encoding="utf-8") == "SCHLUESSEL=GEHEIMER-WERT" and "GEHEIMER-WERT" not in welt.ausgabe(r)

    def test_falsche_aufrufe(self, welt):
        for args in (("--persona", "frech"), ("frech",), (), ("einrichten", "--persona"), ("einrichten", "--release", "../x"),
                     ("status", "--trocken"), ("status", "--persona", "streng"), ("einrichten", "--unbekannt"), ("--trocken",)):
            r = welt.lauf(*args)
            assert r.returncode == 2, (args, welt.ausgabe(r))
        assert not welt.ziel.exists() and welt.sicherungen() == []

    def test_der_hinweis_zur_mail_domaene_zeigt_nur_die_domaene(self, welt):
        (welt.w / "etc" / "wamo" / "core.env").write_text("ANTHROPIC_API_KEY=sk-test-GEHEIM\nWAMO_LABOR_MAIL_DOMAIN=labor.test\n", encoding="utf-8")
        r = welt.lauf("status")
        assert "WAMO_LABOR_MAIL_DOMAIN=labor.test" in r.stdout and "WARNUNG" in r.stdout and "wamo.test" in r.stdout
        (welt.w / "etc" / "wamo" / "core.env").write_text("ANTHROPIC_API_KEY=sk-test-GEHEIM\nWAMO_LABOR_MAIL_DOMAIN=wamo.test\n", encoding="utf-8")
        r = welt.lauf("status")
        assert "WAMO_LABOR_MAIL_DOMAIN=wamo.test" in r.stdout and "WARNUNG" not in r.stdout
        assert "GEHEIM" not in welt.ausgabe(r)
        assert (welt.w / "etc" / "wamo" / "core.env").read_text(encoding="utf-8").count("\n") == 2  # core.env bleibt unberührt

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="als root würde der Lauf das echte System anfassen")
    def test_ohne_probelauf_und_ohne_root_wird_nichts_getan(self, welt):
        r = welt.lauf("einrichten", wurzel=False)
        assert r.returncode == 1 and "als root" in r.stderr
        assert not welt.ziel.exists()


# ---------------------------------------------------------------- Herkunftsschutz (die Funktion allein)


@braucht_bash
@braucht_linux
class TestHerkunftsschutz:
    @staticmethod
    def pruefen(here: Path) -> subprocess.CompletedProcess:
        funktion = re.search(r"^pruefe_herkunft\(\) \{.*?^\}", lies(SKRIPT), re.M | re.S).group(0)
        return subprocess.run([BASH, "-c", f'set -euo pipefail\n{funktion}\nHERE="$1"\npruefe_herkunft', "x", str(here)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    @pytest.fixture
    def ablage(self):
        ordner = Path(tempfile.mkdtemp(prefix="wamo-herkunft-"))
        infra = ordner / "wamo-infra"
        shutil.copytree(QUELLE, infra / "labor-daten")
        for p in [infra, *infra.rglob("*")]:
            p.chmod(0o755 if p.is_dir() else 0o644)
        ordner.chmod(0o755)
        yield infra
        shutil.rmtree(ordner, ignore_errors=True)

    def test_eine_geschuetzte_ablage_besteht(self, ablage):
        r = self.pruefen(ablage)
        assert r.returncode == 0, r.stderr

    def test_eine_beschreibbare_datei_in_labor_daten_wird_abgelehnt(self, ablage):
        (ablage / "labor-daten" / "kontakte.md").chmod(0o664)
        r = self.pruefen(ablage)
        assert r.returncode == 1 and "Unsicherer Ablageort" in r.stderr and "kontakte.md" in r.stderr

    def test_ein_fuer_alle_beschreibbarer_ordner_wird_abgelehnt(self, ablage):
        (ablage / "labor-daten").chmod(0o777)
        assert self.pruefen(ablage).returncode == 1

    def test_ein_symlink_in_der_ablage_wird_abgelehnt(self, ablage):
        os.symlink("/etc/passwd", ablage / "labor-daten" / "extra.md")
        r = self.pruefen(ablage)
        assert r.returncode == 1 and "extra.md" in r.stderr
