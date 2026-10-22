"""Tests für das Umschalten der WAMO-Oberfläche (blau/grün): scripts/wamo_ui_sokrates2.py und
infra/sokrates2/wamo-ui-umschalten.sh, wamo-ui-einrichten.sh, wamo-ui-rueckweg.sh, wamo-ui/*.

Nur Tests ohne Docker: Syntax, feste Sicherheitsregeln der Skripte, Verbotsproben der Parameter (die Skripte werden gestartet,
müssen mit Rückgabewert 2 ablehnen und dürfen nie docker aufrufen; ein falsches docker im PATH zeichnet jeden Aufruf auf),
das Verteil-Werkzeug (Tar-Inhalt, bau.info, Optionen). Der Versuch mit echtem Caddy und Wegwerf-Containern unter Last steht in
infra/sokrates2/wamo_ui_rauchtest.py.

Aufruf (aus voice-core, dort liegt die Python-Umgebung):  uv run python -m pytest ../scripts/test_wamo_ui_sokrates2.py
"""
from __future__ import annotations

import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_ui_sokrates2 as w  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
INFRA = WURZEL / "infra" / "sokrates2"
SKRIPTE = ("wamo-ui-umschalten.sh", "wamo-ui-einrichten.sh", "wamo-ui-rueckweg.sh")
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _bash() -> str | None:
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    for k in kandidaten:
        if k and os.path.exists(k):
            return k
    return None


BASH = _bash()
braucht_bash = pytest.mark.skipif(BASH is None, reason="keine Bash vorhanden")


# ---------------------------------------------------------------- Dateien und feste Regeln
@braucht_bash
@pytest.mark.parametrize("name", SKRIPTE)
def test_bash_n(name):
    r = subprocess.run([BASH, "-n", str(INFRA / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("name", SKRIPTE)
def test_feste_regeln_der_skripte(name):
    text = (INFRA / name).read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert not re.search(r"(^|[^A-Za-z_])eval ", text)
    assert "\r" not in text
    assert "\u2014" not in text and "\u2013" not in text      # keine Gedankenstriche
    assert text.startswith("#!/usr/bin/env bash\n")


def test_umschalter_ruft_weder_sudo_noch_shell_aus_eingaben_auf():
    text = (INFRA / "wamo-ui-umschalten.sh").read_text(encoding="utf-8")
    assert not re.search(r"\bsudo\s+-", text), "der Umschalter braucht kein sudo (er läuft als wamocon oder über den Notanker als root)"
    assert "bash -c" not in text and "sh -c" not in text
    assert "source " not in text and not re.search(r"(^|\n)\s*\. [^\s]", text)


def test_umschalter_prueft_nur_feste_namen_im_betrieb():
    text = (INFRA / "wamo-ui-umschalten.sh").read_text(encoding="utf-8")
    assert r'^wamo-test-[a-z0-9]{1,16}-$' in text           # Testmodus nur mit diesem Präfix
    assert 'PRAEFIX="${WAMO_UI_PRAEFIX:-wamo-}"' in text   # ohne Variable gelten die Namen des Betriebs
    for name in ('C_BLAU="${PRAEFIX}ui-blau"', 'C_GRUEN="${PRAEFIX}ui-gruen"', 'C_CADDY="${PRAEFIX}caddy"',
                 'NETZ="${PRAEFIX}ui-netz"', 'BILD="${PRAEFIX}ui"'):
        assert name in text
    for fest in ("BASIS=/home/wamocon/wamo-ui", "CADDY_DIR=/home/wamocon/wamo-tls/caddy",
                 "CA=/home/wamocon/wamo-tls/ca/wamo-ca.crt"):
        assert fest in text


def test_caddy_regeln_im_umschalter():
    text = (INFRA / "wamo-ui-umschalten.sh").read_text(encoding="utf-8")
    assert "handle /_next/static/*" in text and "handle_response @fehlt" in text and "@fehlt status 404" in text
    assert "lb_policy first" in text and "health_uri" in text and "fail_duration 10s" in text
    assert "caddy reload" in text and "caddy adapt" in text
    # der Neustart von Caddy ist verboten: nur reload
    assert not re.search(r"docker (restart|stop|rm)[^\n]*\$C_CADDY", text)
    assert "up -d --no-deps --force-recreate" in text


def test_site_vorlage_bindet_die_include_datei_ein():
    text = (INFRA / "wamo-ui" / "wamo-ui.caddy.vorlage").read_text(encoding="utf-8")
    assert "import /etc/caddy/kern/wamo-ui-aktiv.inc" in text
    assert "reverse_proxy wamo-ui:3000" not in text
    assert "@schreibend not method GET HEAD" in text         # nur GET und HEAD, sonst wäre der Static-Fallback nicht sicher


def test_compose_zwei_gehaertete_farben():
    text = (INFRA / "wamo-ui" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "container_name: ${WAMO_UI_PRAEFIX:-wamo-}ui-blau" in text and "container_name: ${WAMO_UI_PRAEFIX:-wamo-}ui-gruen" in text
    assert "image: ${WAMO_UI_BILD:-wamo-ui}:blau" in text and "image: ${WAMO_UI_BILD:-wamo-ui}:gruen" in text
    for zeile in ("read_only: true", "no-new-privileges:true", "mem_limit: 512m", "pids_limit: 128", "cpus: 1.0",
                  "pull_policy: never", "restart: unless-stopped", "internal" if False else "external: true"):
        assert zeile in text, zeile
    assert re.search(r"cap_drop:\n\s+- ALL", text)
    assert "\n    ports:" not in text and "\nports:" not in text     # kein veröffentlichter Port
    assert "/version.json" in text                                   # Gesundheitsprobe auf die neue Route
    services = re.findall(r"^  (blau|gruen):$", text, re.M)
    assert services == ["blau", "gruen"]


def test_dockerfile_commit_und_probe():
    text = (INFRA / "wamo-ui" / "Dockerfile").read_text(encoding="utf-8")
    for arg in ("ARG WAMO_COMMIT=unbekannt", "ARG WAMO_BAUZEIT=unbekannt", "ARG WAMO_SAUBER=nein"):
        assert arg in text
    assert "version.json" in text.split("HEALTHCHECK", 1)[1]
    assert "service_role" not in text.lower() or "nie der service_role" in text
    # die Argumente stehen erst in der Laufzeit-Stufe: ein anderer Commit baut die Oberfläche nicht neu
    assert text.index("ARG WAMO_COMMIT") > text.index("AS laufzeit")


def test_attrappe_und_werkzeuge_sind_nicht_in_der_verteilung():
    assert not any("attrappe" in d for d in w.INFRA_DATEIEN)
    assert "wamo-ui-umschalten.sh" in w.INFRA_DATEIEN
    for rel in w.INFRA_DATEIEN:
        assert (INFRA / rel).is_file(), rel


# ---------------------------------------------------------------- Verbotsproben der Parameter
@pytest.fixture
def falsches_docker(tmp_path):
    """Ein docker im PATH, das jeden Aufruf aufzeichnet und scheitert: Die Verbotsproben dürfen es nie erreichen."""
    log = tmp_path / "docker-aufrufe.log"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    d = bin_ / "docker"
    d.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log.as_posix()}"\nexit 99\n', encoding="utf-8", newline="\n")
    d.chmod(d.stat().st_mode | stat.S_IEXEC)
    return bin_, log


def _lauf(falsches_docker, *args, env=None):
    bin_, log = falsches_docker
    umgebung = {**os.environ, "PATH": f"{bin_.as_posix()}{os.pathsep}{os.environ['PATH']}"}
    umgebung.pop("WAMO_UI_PRAEFIX", None)
    umgebung.update(env or {})
    r = subprocess.run([BASH, (INFRA / "wamo-ui-umschalten.sh").as_posix(), *args], capture_output=True, text=True,
                       env=umgebung, timeout=60)
    aufrufe = log.read_text(encoding="utf-8") if log.exists() else ""
    return r, aufrufe


VERBOTEN_ALLGEMEIN = [
    [],
    ["unbekannt"],
    ["status", "--unbekannt"],
    ["status", "--trocken"],                       # nur lesend: --trocken gibt es hier nicht
    ["umschalten", "--json"],
    ["zurueck", "--commit", COMMIT],
    ["zurueck", "--bewaehrung", "5"],
    ["eintrag", "--trocken"],
    ["include-anlegen", "--trocken"],
    ["umschalten", "--commit"],
    ["umschalten", "--commit", ""],
    ["umschalten", "--commit", COMMIT.upper()],
    ["umschalten", "--commit", COMMIT[:39]],
    ["umschalten", "--commit", COMMIT + "0"],
    ["umschalten", "--commit", "g" * 40],
    ["umschalten", "--commit", f"{COMMIT[:30]};id;{COMMIT[:4]}"[:40]],
    ["umschalten", "--commit", "$(id)" + "0" * 36],
    ["umschalten", "--sauber", "vielleicht"],
    ["umschalten", "--sauber"],
    ["umschalten", "--sauber", "JA"],
    ["umschalten", "--bewaehrung", "-1"],
    ["umschalten", "--bewaehrung", "1801"],
    ["umschalten", "--bewaehrung", "99999"],
    ["umschalten", "--bewaehrung", "abc"],
    ["umschalten", "--bewaehrung", "1e3"],
    ["umschalten", "--bewaehrung", "10;ls"],
    ["umschalten", "--bewaehrung", ""],
    ["umschalten", "--bewaehrung"],
    ["umschalten", "--fehlerquote", "51"],
    ["umschalten", "--fehlerquote", "-5"],
    ["umschalten", "--fehlerquote", "5%"],
    ["umschalten", "--fehlerquote", "5 6"],
    ["umschalten", "--ziel", "blau"],
    ["umschalten", "blau"],
    ["umschalten", "--trocken", "extra"],
    ["bauen", "--ohne-bau"],
    ["bauen", "--bewaehrung", "5"],
]

VERBOTEN_ANKER = [
    ["anker-rueckweg"],
    ["anker-rueckweg", "v0.1.0"],
    ["anker-rueckweg", "v0.1.0", COMMIT],
    ["anker-rueckweg", "0.1.0", COMMIT, "blau"],                  # ohne v
    ["anker-rueckweg", "v1.2", COMMIT, "blau"],
    ["anker-rueckweg", "v01.2.3", COMMIT, "blau"],                # führende Null
    ["anker-rueckweg", "v1.2.3-rc1", COMMIT, "blau"],
    ["anker-rueckweg", "v1.2.3.4", COMMIT, "blau"],
    ["anker-rueckweg", "v12345.0.0", COMMIT, "blau"],             # mehr als vier Stellen
    ["anker-rueckweg", "V1.2.3", COMMIT, "blau"],
    ["anker-rueckweg", "v1.2.3;id", COMMIT, "blau"],
    ["anker-rueckweg", "v1.2.3", COMMIT[:39], "blau"],
    ["anker-rueckweg", "v1.2.3", COMMIT.upper(), "blau"],
    ["anker-rueckweg", "v1.2.3", "$(id)" + "0" * 36, "blau"],
    ["anker-rueckweg", "v1.2.3", COMMIT, "rot"],
    ["anker-rueckweg", "v1.2.3", COMMIT, "alt"],                  # der Notanker kennt nur blau und gruen
    ["anker-rueckweg", "v1.2.3", COMMIT, "keine"],
    ["anker-rueckweg", "v1.2.3", COMMIT, "blau;id"],
    ["anker-rueckweg", "v1.2.3", COMMIT, "../blau"],
    ["anker-rueckweg", "v1.2.3", COMMIT, "/etc/passwd"],
    ["anker-rueckweg", "v1.2.3", COMMIT, ""],
    ["anker-rueckweg", "v1.2.3", COMMIT, "Blau"],
    ["anker-rueckweg", "v1.2.3", COMMIT, "blau", "extra"],
    ["anker-rueckweg", "v1.2.3", COMMIT, "blau", "--commit", COMMIT],   # Optionen des Umschaltens gelten hier nicht
    ["anker-rueckweg", "v1.2.3", COMMIT, "blau", "--bewaehrung", "5"],
]


# Ein Zeilenumbruch am Ende eines Arguments übersteht die Befehlszeile unter Windows nicht; unter Linux (wo die Skripte laufen)
# lehnt das Muster ihn ab. Die Probe gilt deshalb nur dort.
if os.name != "nt":
    VERBOTEN_ALLGEMEIN.append(["umschalten", "--commit", COMMIT + "\n"])
    VERBOTEN_ANKER.append(["anker-rueckweg", "v1.2.3", COMMIT, "blau\n"])


@braucht_bash
@pytest.mark.parametrize("args", VERBOTEN_ALLGEMEIN + VERBOTEN_ANKER, ids=lambda a: " ".join(a)[:70] or "ohne Argumente")
def test_verbotsprobe_parameter(falsches_docker, args):
    r, docker_aufrufe = _lauf(falsches_docker, *args)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "Aufruf:" in r.stderr
    assert docker_aufrufe == "", "bei abgelehnten Parametern darf docker nie aufgerufen werden"


@braucht_bash
@pytest.mark.parametrize("praefix", [
    "foo-", "wamo-test-", "wamo-test--", "wamo-test-UPPER-", "wamo-test-x", "wamo-test-a/../b-", "wamo-test-a b-",
    "wamo-test-aaaaaaaaaaaaaaaaa-", "wamo-", "wamo-test-a;id-", "wamo-test-$(id)-", "wamo--",
])
def test_verbotsprobe_testmodus_praefix(falsches_docker, praefix):
    r, docker_aufrufe = _lauf(falsches_docker, "status", env={"WAMO_UI_PRAEFIX": praefix})
    if praefix == "wamo-":
        # der Betriebswert ist erlaubt; ohne wamocon scheitert der Lauf später mit 1, aber nie mit der Ablehnung 2
        assert r.returncode != 2
    else:
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "WAMO_UI_PRAEFIX" in r.stderr
    assert docker_aufrufe == ""


@braucht_bash
@pytest.mark.parametrize("variable,wert", [
    ("WAMO_UI_BASIS", "relativ/pfad"), ("WAMO_UI_BASIS", "/mit leerzeichen"), ("WAMO_UI_BASIS", "/a/../b"),
    ("WAMO_UI_CADDY_DIR", "/a;b"), ("WAMO_UI_QUELLE", "/a$(id)"), ("WAMO_UI_BAUDATEI", "/a\nb"),
    ("WAMO_UI_BAUKONTEXT", "~/x"), ("WAMO_UI_BEREIT_S", "abc"), ("WAMO_UI_BEREIT_S", "9999"), ("WAMO_UI_BEREIT_S", "-1"),
])
def test_verbotsprobe_testmodus_pfade(falsches_docker, variable, wert):
    env = {"WAMO_UI_PRAEFIX": "wamo-test-t1-", "WAMO_UI_BASIS": "/tmp/x", "WAMO_UI_CADDY_DIR": "/tmp/y", variable: wert}
    r, docker_aufrufe = _lauf(falsches_docker, "status", env=env)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert docker_aufrufe == ""


@braucht_bash
def test_testmodus_ohne_pflichtpfade_wird_abgelehnt(falsches_docker):
    r, docker_aufrufe = _lauf(falsches_docker, "status", env={"WAMO_UI_PRAEFIX": "wamo-test-t1-"})
    assert r.returncode != 0 and docker_aufrufe == ""


@braucht_bash
def test_erlaubte_parameter_werden_angenommen(falsches_docker):
    """Gültige Aufrufe laufen bis zur ersten Voraussetzung (Benutzer, Einstellungen) und scheitern dort mit 1, nie mit 2."""
    faelle = [
        ["status"], ["status", "--json"], ["eintrag"], ["include-anlegen"], ["bauen"], ["bauen", "--trocken"],
        ["umschalten"], ["umschalten", "--trocken", "--ohne-bau", "--commit", COMMIT, "--sauber", "ja",
                         "--bewaehrung", "0", "--fehlerquote", "50"],
        ["umschalten", "--bewaehrung", "1800", "--fehlerquote", "0"], ["zurueck"], ["zurueck", "--trocken"],
        ["anker-rueckweg", "v0.1.0", COMMIT, "blau"], ["anker-rueckweg", "v1234.0.9", COMMIT, "gruen", "--trocken"],
    ]
    for a in faelle:
        r, docker_aufrufe = _lauf(falsches_docker, *a)
        assert r.returncode != 2, (a, r.stderr)
        assert docker_aufrufe == "", a


@braucht_bash
@pytest.mark.parametrize("args,rc_ist_zwei", [
    ([ "v0.1.0", COMMIT, "blau"], False),
    (["v0.1.0", COMMIT, "gruen"], False),
    (["v0.1.0", COMMIT, "alt"], True),
    (["0.1.0", COMMIT, "blau"], True),
    (["v0.1.0", COMMIT[:39], "blau"], True),
    (["v0.1.0", COMMIT, "blau", "--trocken"], False),
    (["v0.1.0", COMMIT, "blau", "extra"], True),
    (["v0.1.0", COMMIT], True),
    ([], True),
    (["umschalten"], True),            # als Schritt-Skript gibt es nur den Rückweg
    (["status"], True),
])
def test_als_schritt_skript_rueckweg_ui(falsches_docker, tmp_path, args, rc_ist_zwei):
    """Der Anker kopiert das Skript unverändert als schritte/rueckweg-ui und ruft es mit Version, Commit und Ziel auf."""
    kopie = tmp_path / "rueckweg-ui"
    shutil.copy(INFRA / "wamo-ui-umschalten.sh", kopie)
    bin_, log = falsches_docker
    umgebung = {**os.environ, "PATH": f"{bin_.as_posix()}{os.pathsep}{os.environ['PATH']}"}
    umgebung.pop("WAMO_UI_PRAEFIX", None)
    r = subprocess.run([BASH, kopie.as_posix(), *args], capture_output=True, text=True, env=umgebung, timeout=60)
    assert (r.returncode == 2) == rc_ist_zwei, (args, r.returncode, r.stderr)
    assert not log.exists() or log.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------- Verteil-Werkzeug
def test_bau_info_nur_gueltige_commit_ids():
    assert w.bau_info(COMMIT, True) == f"WAMO_COMMIT={COMMIT}\nWAMO_SAUBER=ja\n".encode()
    assert w.bau_info(COMMIT, False).endswith(b"WAMO_SAUBER=nein\n")
    for schlecht in ("", "abc", COMMIT.upper(), COMMIT + "0", "$(id)" * 8, COMMIT[:-1] + "\n"):
        with pytest.raises(ValueError):
            w.bau_info(schlecht, True)


def test_tar_enthaelt_skripte_bau_info_und_keine_umgebungsdateien():
    daten, n_ui, n_infra = w.tar_bauen(False, COMMIT, True)
    tar = tarfile.open(fileobj=io.BytesIO(daten))
    namen = tar.getnames()
    assert "wamo-ui-umschalten.sh" in namen and "wamo-ui/bau.info" in namen
    assert tar.getmember("wamo-ui-umschalten.sh").mode == 0o755            # ausführbar
    assert tar.getmember("wamo-ui/Dockerfile").mode == 0o644
    assert tar.extractfile("wamo-ui/bau.info").read() == w.bau_info(COMMIT, True)
    assert not any(n.startswith("wamo-ui/attrappe") for n in namen)       # Testhilfen gehören nicht auf den Server
    assert "ui/src/app/version.json/route.ts" in namen and "ui/src/lib/buildInfo.ts" in namen
    assert not any(re.search(r"(^|/)\.env", n) for n in namen)
    assert not any("node_modules" in n for n in namen)
    assert n_ui > 20 and n_infra == len(w.INFRA_DATEIEN)
    for n in namen:
        m = tar.getmember(n)
        if m.isfile() and m.name.endswith((".sh", ".yml", "Dockerfile", ".vorlage")):
            assert b"\r" not in tar.extractfile(n).read(), n                # Unix-Zeilenenden


def test_tar_mit_firewall_und_caddy_hat_die_gemeinsamen_dateien():
    daten, _, n_infra = w.tar_bauen(True, COMMIT, False)
    namen = tarfile.open(fileobj=io.BytesIO(daten)).getnames()
    assert "wamo-caddy/docker-compose.yml" in namen and n_infra == len(w.INFRA_DATEIEN) + len(w.GEMEINSAME_DATEIEN)


def test_git_stand_liefert_commit_und_flag():
    commit, sauber = w.git_stand()
    assert re.fullmatch(r"[0-9a-f]{40}", commit) and isinstance(sauber, bool)


def test_optionen_nur_geprueft_und_nur_fuer_umschalten(monkeypatch, capsys):
    p = w.parser_bauen()
    a = p.parse_args(["umschalten", "--bewaehrung", "45", "--fehlerquote", "5", "--ohne-bau", "--trocken"])
    assert w.umschalt_argumente(a) == ["--bewaehrung", "45", "--fehlerquote", "5", "--ohne-bau", "--trocken"]
    assert w.umschalt_argumente(p.parse_args(["umschalten"])) == []
    for schlecht in (["umschalten", "--bewaehrung", "1801"], ["umschalten", "--bewaehrung", "-1"],
                     ["umschalten", "--bewaehrung", "5;ls"], ["umschalten", "--fehlerquote", "51"],
                     ["umschalten", "--fehlerquote", "x"]):
        with pytest.raises(SystemExit):
            p.parse_args(schlecht)
    aufrufe = []
    monkeypatch.setattr(w, "ssh", lambda befehl, **kw: aufrufe.append(befehl) or subprocess.CompletedProcess([], 0, b"", b""))
    monkeypatch.setattr(sys, "argv", ["x", "zurueck", "--bewaehrung", "5"])
    with pytest.raises(SystemExit) as e:
        w.main()
    assert e.value.code == 2 and aufrufe == []                              # --bewaehrung gilt nur für umschalten
    monkeypatch.setattr(sys, "argv", ["x", "status", "--trocken"])
    with pytest.raises(SystemExit) as e:
        w.main()
    assert e.value.code == 2 and aufrufe == []


def test_befehle_gehen_an_die_richtigen_skripte(monkeypatch):
    aufrufe = []
    monkeypatch.setattr(w, "ssh", lambda befehl, **kw: aufrufe.append(befehl) or subprocess.CompletedProcess([], 0, b"", b""))
    erwartet = {
        ("umschalten",): "bash ~/wamo-infra/wamo-ui-umschalten.sh umschalten",
        ("umschalten", "--bewaehrung", "60", "--trocken"): "bash ~/wamo-infra/wamo-ui-umschalten.sh umschalten --bewaehrung 60 --trocken",
        ("zurueck",): "bash ~/wamo-infra/wamo-ui-umschalten.sh zurueck",
        ("bauen",): "bash ~/wamo-infra/wamo-ui-umschalten.sh bauen",
        ("eintrag",): "bash ~/wamo-infra/wamo-ui-umschalten.sh eintrag",
        ("einrichten", "--neustart"): "bash ~/wamo-infra/wamo-ui-einrichten.sh alles --neustart",
        ("pruefen",): "bash ~/wamo-infra/wamo-ui-einrichten.sh pruefen",
        ("status",): "bash ~/wamo-infra/wamo-ui-einrichten.sh status",
    }
    for argv, soll in erwartet.items():
        aufrufe.clear()
        monkeypatch.setattr(sys, "argv", ["x", *argv])
        with pytest.raises(SystemExit) as e:
            w.main()
        assert e.value.code == 0 and aufrufe == [soll], (argv, aufrufe)
