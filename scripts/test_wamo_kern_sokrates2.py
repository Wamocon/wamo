"""Tests für scripts/wamo_kern_sokrates2.py (Kern auf Sokrates-2 vom PC aus einrichten): ohne Netz, ssh und scp sind Attrappen.

Geprüft werden: keine Entwicklungsschalter in der Übertragung, die Dateien von verteilen (alles, was wamo-job-einrichten.sh
verlangt), die Weigerung von neustart und alles im Slot-Betrieb (nie systemctl restart der alten Unit, sondern Hinweis auf
wamo-core-slots.sh umschalten-kern) und die Anzeige von Slots und Schaltern in status (nur Namen).

Aufruf (aus voice-core, dort liegt die Python-Umgebung):  uv run python -m pytest ../scripts/test_wamo_kern_sokrates2.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_kern_sokrates2 as k  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
INFRA = WURZEL / "infra" / "sokrates2"
SLOT_SUCHE = "wamo-core@"  # Merkmal des Aufrufs, der den Slot-Betrieb erkennt (nur lesend)


class Ssh:
    """Attrappe für k.ssh: zeichnet jeden Befehl auf und antwortet auf die Slot-Abfrage mit einer festen Ausgabe."""

    def __init__(self, slot_ausgabe: str = "", slot_fehler: bool = False, ohne_ende: bool = False):
        self.befehle: list[str] = []
        self.slot_ausgabe = slot_ausgabe
        self.slot_fehler = slot_fehler
        self.ohne_ende = ohne_ende  # die Abfrage endet nie mit ihrer Schlussmarke (abgebrochen, unbekannte Shell)

    def __call__(self, befehl: str, stdin: str | None = None) -> str:
        self.befehle.append(befehl)
        if k.SLOT_ENDE in befehl and "ActiveState" in befehl:  # die Slot-Abfrage, nicht status oder neustart
            if self.slot_fehler:
                sys.exit("SSH-Befehl auf wamo-sokrates2 fehlgeschlagen: Zeitüberschreitung")
            return self.slot_ausgabe + ("" if self.ohne_ende else k.SLOT_ENDE + "\n")
        return "active\n"

    def neustarts(self) -> list[str]:
        return [b for b in self.befehle if re.search(r"systemctl\s+(restart|start)\s+wamo-core", b)]


def test_stimmpruefung_und_schwellen_gehen_nie_vom_pc_auf_den_server():
    """Modus und Schwellen der Stimmprüfung sind Sicherheitseinstellungen des Servers (wamo-core-env): eine .env des PCs setzt sie nicht."""
    assert not [n for n in k.AUS_ENV if n.startswith("WAMO_STIMME_")]


def test_aus_env_enthaelt_keinen_entwicklungsschalter():
    assert not [n for n in k.AUS_ENV if n.startswith("WAMO_ENTWICKLUNG")]
    assert "WAMO_ENTWICKLUNG_BUERO" not in k.AUS_ENV and "WAMO_ENTWICKLUNG_PROFILE" not in k.AUS_ENV
    assert not [p for p in k.AUS_ENV_PRAEFIX if p.startswith("WAMO_ENTWICKLUNG")]


def test_kommentar_zu_den_befehlskriterien_ist_korrigiert():
    quelle = (Path(k.__file__)).read_text(encoding="utf-8")
    assert "bleiben auf Sokrates-2 ohne Wirkung" not in quelle  # stimmte seit dem Dispatcher-Befehl pruefen nicht mehr
    assert "WAMO_ENTWICKLUNG_BEFEHLE_BUERO" in quelle


# ---------------------------------------------------------------- verteilen
def test_verteilen_liefert_alle_dateien_die_wamo_job_einrichten_verlangt(monkeypatch, tmp_path):
    ssh = Ssh()
    gerufen: list[list[str]] = []
    monkeypatch.setattr(k, "ssh", ssh)
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: gerufen.append(list(argv)) or subprocess.CompletedProcess(argv, 0))
    monkeypatch.setattr(k, "JOBS_PUB", tmp_path / "gibt-es-nicht.pub")
    monkeypatch.setattr(k, "TUNNEL_PUB", tmp_path / "gibt-es-nicht-auch-nicht.pub")
    k.verteilen(None)

    # ganzer Ordner infra/sokrates2 nach ~/wamo-infra (darin Dispatcher, wamo_job_kernkanal.py, wamo_job_pruefen.py und die Kern-Weg-Dateien)
    assert any(a[:3] == ["scp", "-q", "-r"] and a[3] == str(INFRA) and a[4].endswith(":wamo-infra") for a in gerufen)
    zugestellt = {Path(a[-2]).name: a[-1].split(":", 1)[1] for a in gerufen if a[:2] == ["scp", "-q"] and "-r" not in a[:3] and len(a) == 4}

    kopf = (INFRA / "wamo-job-einrichten.sh").read_text(encoding="utf-8").split("set -euo pipefail", 1)[0]
    aus_voice_core = set(re.findall(r"voice-core/([a-z_]+\.py)", kopf))
    assert {"dev_guard.py", "werkzeugkarte.py", "kontingent.py", "abnahme_befehl.py"} <= aus_voice_core, aus_voice_core
    for name in aus_voice_core:
        assert zugestellt.get(name) == f"wamo-infra/{name}", (name, zugestellt)  # genau dorthin, wo das Skript sie erwartet

    aus_infra = set(re.findall(r"\b([\w-]+\.(?:py|sh|nft|service))\b", kopf.split("scp voice-core/", 1)[0])) - {"wamo-job-einrichten.sh"}
    assert {"wamo_job_kernkanal.py", "wamo_job_pruefen.py"} <= aus_infra, aus_infra
    for name in sorted(aus_infra):
        assert (INFRA / name).is_file(), f"{name} steht im Kopfkommentar von wamo-job-einrichten.sh, liegt aber nicht in infra/sokrates2"


def test_verteilen_liefert_die_kern_weg_dateien_und_nie_einen_schluessel_des_kerns(monkeypatch, tmp_path):
    for name in ("wamo-job-kern-einrichten.sh", "wamo-job-kern-rueckweg.sh", "wamo_job_kern_rauchtest.py", "wamo_job_kernkanal.py"):
        assert (INFRA / name).is_file(), name  # sie liegen im Ordner, den verteilen als Ganzes überträgt
    gerufen: list[list[str]] = []
    monkeypatch.setattr(k, "ssh", Ssh())
    monkeypatch.setattr(k.subprocess, "run", lambda argv, **kw: gerufen.append(list(argv)) or subprocess.CompletedProcess(argv, 0))
    pub = tmp_path / "id_ed25519_wamo_jobs.pub"  # der Schlüssel des PC-Wegs, wie bisher
    pub.write_text("ssh-ed25519 AAAA test", encoding="utf-8")
    monkeypatch.setattr(k, "JOBS_PUB", pub)
    monkeypatch.setattr(k, "TUNNEL_PUB", tmp_path / "nichts.pub")
    k.verteilen(None)
    alles = " ".join(" ".join(a) for a in gerufen)
    assert "wamo-kern" not in alles and "wamo_kern_jobs" not in alles and "kern-jobs" not in alles
    assert "id_ed25519_wamo_jobs.pub" in alles  # der PC-Weg bleibt unverändert
    kern = [a for a in gerufen if a[:2] == ["scp", "-q"] and a[-1].endswith("wamo-infra/kern/")]
    assert kern and any(x.endswith("entwicklung_betrieb.py") for a in kern for x in a), "der Kern-Code enthält entwicklung_betrieb.py"
    assert not any(Path(x).name.startswith("test_") for a in gerufen for x in a if x.endswith(".py"))


# ---------------------------------------------------------------- Slot-Betrieb: nie die alte Unit neu starten
@pytest.mark.parametrize("ausgabe", ["aktiv-a\nslot-a\n", "slot-b\n", "aktiv-b\n", "slot-a\nslot-b\naktiv-a\n",
                                     "fehler-a\n", "fehler-b\nfehler-a\n"])
def test_neustart_verweigert_im_slot_betrieb_mit_hinweis(monkeypatch, capsys, ausgabe):
    ssh = Ssh(ausgabe)
    monkeypatch.setattr(k, "ssh", ssh)
    with pytest.raises(SystemExit) as e:
        k.neustart()
    assert e.value.code == 3
    meldung = capsys.readouterr().err
    assert "umschalten-kern" in meldung and "wamo-core-slots.sh" in meldung
    assert ssh.neustarts() == []  # nie ein Neustart, auch nicht von start
    assert all("systemctl restart" not in b for b in ssh.befehle)


def test_slot_im_fenster_activating_wird_erkannt(monkeypatch, capsys):
    """Nach einem Absturz steht die Instanz kurz auf "activating (auto-restart)": is-active meldet dann nicht active. Die Abfrage
    nimmt deshalb ActiveState und zählt active, activating, reloading und deactivating als laufend."""
    ssh = Ssh("aktiv-b\n")  # nur der Zustand, kein Verweis lesbar
    monkeypatch.setattr(k, "ssh", ssh)
    with pytest.raises(SystemExit) as e:
        k.neustart()
    assert e.value.code == 3 and ssh.neustarts() == []
    abfrage = ssh.befehle[0]
    assert "systemctl show -p ActiveState --value wamo-core@$s" in abfrage
    assert "grep -qE '^(active|activating|reloading|deactivating)$'" in abfrage


def test_marker_nicht_lesbar_gilt_als_slot_betrieb_fail_closed(monkeypatch, capsys):
    """Der Ordner /opt/wamo-core/slots ist root:wamo-core 0750: ohne sudo wäre ein Test darauf immer "nein". Läuft die Prüfung
    nicht durch, behandelt das Skript den Server als Slot-Betrieb (Exit 3), es startet nie die alte Unit."""
    ssh = Ssh("fehler-a\nfehler-b\n")
    monkeypatch.setattr(k, "ssh", ssh)
    with pytest.raises(SystemExit) as e:
        k.neustart()
    assert e.value.code == 3 and ssh.neustarts() == []
    assert "umschalten-kern" in capsys.readouterr().err


def test_ohne_schlussmarke_der_abfrage_gilt_der_server_als_slot_betrieb(monkeypatch):
    ssh = Ssh("", ohne_ende=True)  # leere oder abgeschnittene Antwort: unbekannt, nie "kein Slot"
    monkeypatch.setattr(k, "ssh", ssh)
    assert k.slot_betrieb() == ["fehler-pruefung"]
    with pytest.raises(SystemExit) as e:
        k.neustart()
    assert e.value.code == 3 and ssh.neustarts() == []


def test_slot_abfrage_nur_lesend_mit_zustand_und_marker(monkeypatch):
    ssh = Ssh("")
    monkeypatch.setattr(k, "ssh", ssh)
    assert k.slot_betrieb() == []
    abfrage = ssh.befehle[0]
    assert "/opt/wamo-core/slots/" in abfrage and "sudo -n test -e" in abfrage  # der Marker, mit den Rechten, die ihn lesen
    assert re.search(r"\b0\b|-eq 0", abfrage)
    assert not re.search(r"\b(restart|start|stop|enable|disable|daemon-reload|rm|mv|tee|kill|reset-failed)\b", abfrage)
    assert "sudo -n test" in abfrage and not re.search(r"sudo -n (?!test)", abfrage)  # sudo nur für test -e, sonst nichts


def test_slot_abfrage_erkennt_nur_bekannte_zeilen(monkeypatch):
    monkeypatch.setattr(k, "ssh", Ssh("Warnung: irgendwas\naktiv-a\nslot-c\nslot-a\n  slot-b  \nfehler-b\n"))
    assert k.slot_betrieb() == ["aktiv-a", "fehler-b", "slot-a", "slot-b"]


def _bash() -> str | None:
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    return next((x for x in kandidaten if x and os.path.exists(x)), None)


FAKE_SYSTEMCTL = """#!/bin/bash
# systemctl show -p ActiveState --value wamo-core@X
[ "${FAKE_SYSTEMCTL:-ok}" = fehler ] && exit 1
s="${!#}"; s="${s#wamo-core@}"
v="FAKE_STATE_$s"
echo "${!v:-inactive}"
"""
FAKE_SUDO = """#!/bin/bash
# sudo -n test -e PFAD
[ "$1" = -n ] && shift
if [ "${FAKE_SUDO:-ok}" = nein ]; then echo "sudo: a password is required" >&2; exit 1; fi
s="${3##*/}"
case " ${FAKE_SLOTS:-} " in *" $s "*) exit 0;; esac
exit 1
"""


@pytest.fixture
def abfrage_laufen(tmp_path):
    """Die echte Slot-Abfrage in bash, mit Attrappen für systemctl und sudo (kein Zugriff auf einen Server)."""
    bash = _bash()
    if bash is None:
        pytest.skip("kein bash")
    for name, text in (("systemctl", FAKE_SYSTEMCTL), ("sudo", FAKE_SUDO)):
        (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
        (tmp_path / name).chmod(0o755)

    def laufen(**umgebung) -> list[str]:
        env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""), **umgebung}
        ergebnis = subprocess.run([bash, "-c", k.SLOT_ABFRAGE], env=env, capture_output=True, text=True, timeout=60)
        assert ergebnis.returncode == 0, ergebnis.stderr
        return k.slot_zeilen(ergebnis.stdout)

    return laufen


def test_abfrage_in_bash_kein_slot(abfrage_laufen):
    assert abfrage_laufen() == []  # beide inactive, kein Marker, sudo geht: alte Unit


def test_abfrage_in_bash_activating_wird_erkannt(abfrage_laufen):
    assert abfrage_laufen(FAKE_STATE_a="activating") == ["aktiv-a"]
    assert abfrage_laufen(FAKE_STATE_b="deactivating", FAKE_STATE_a="active") == ["aktiv-a", "aktiv-b"]
    assert abfrage_laufen(FAKE_STATE_a="reloading") == ["aktiv-a"]
    assert abfrage_laufen(FAKE_STATE_a="failed") == []  # failed und inactive laufen nicht


def test_abfrage_in_bash_marker_ohne_aktiven_zustand(abfrage_laufen):
    assert abfrage_laufen(FAKE_SLOTS="a") == ["slot-a"]
    assert abfrage_laufen(FAKE_SLOTS="a b", FAKE_STATE_a="activating") == ["aktiv-a", "slot-a", "slot-b"]


def test_abfrage_in_bash_marker_nicht_lesbar_ist_ein_fehler_kein_nein(abfrage_laufen):
    zeilen = abfrage_laufen(FAKE_SUDO="nein")  # sudo ohne Passwort nicht möglich: nicht lesbar, nicht "kein Slot"
    assert zeilen == ["fehler-a", "fehler-b"]
    assert abfrage_laufen(FAKE_SUDO="nein", FAKE_STATE_a="activating") == ["aktiv-a", "fehler-a", "fehler-b"]


def test_abfrage_in_bash_systemctl_fehler_ist_ein_fehler(abfrage_laufen):
    assert abfrage_laufen(FAKE_SYSTEMCTL="fehler") == ["fehler-a", "fehler-b"]


def test_ohne_slots_gilt_der_alte_weg_unveraendert(monkeypatch):
    ssh = Ssh("")
    monkeypatch.setattr(k, "ssh", ssh)
    k.neustart()
    assert len(ssh.neustarts()) == 1  # die alte Unit, solange es keinen Slot gibt


def test_schlaegt_die_abfrage_fehl_startet_nichts_neu(monkeypatch):
    ssh = Ssh(slot_fehler=True)
    monkeypatch.setattr(k, "ssh", ssh)
    with pytest.raises(SystemExit):
        k.neustart()
    assert ssh.neustarts() == []  # unbekannt heißt: kein Neustart (fail closed)


@pytest.mark.parametrize("argv", [["alles", "--email", "niko@example.com"], ["db", "--neustart"], ["geheimnisse", "--neustart"],
                                  ["token", "--neustart"], ["profil", "--email", "niko@example.com", "--neustart"]])
def test_alles_und_option_neustart_verweigern_bevor_etwas_geaendert_wird(monkeypatch, capsys, argv):
    ssh = Ssh("aktiv-a\nslot-a\n")
    monkeypatch.setattr(k, "ssh", ssh)
    monkeypatch.setattr(k.subprocess, "run", lambda *a, **kw: pytest.fail("kein scp, kein ssh außer der Slot-Abfrage"))
    for name in ("_db", "geheimnisse", "token", "profil"):
        monkeypatch.setattr(k, name, lambda *a, _n=name, **kw: pytest.fail(f"{_n} darf nicht laufen"))
    monkeypatch.setattr(sys, "argv", ["x", *argv])
    with pytest.raises(SystemExit) as e:
        k.main()
    assert e.value.code == 3
    assert "umschalten-kern" in capsys.readouterr().err
    assert len(ssh.befehle) == 1 and SLOT_SUCHE in ssh.befehle[0]  # nur die lesende Abfrage


def test_befehle_ohne_neustart_laufen_auch_im_slot_betrieb(monkeypatch):
    ssh = Ssh("aktiv-a\nslot-a\n")
    monkeypatch.setattr(k, "ssh", ssh)
    gerufen = []
    monkeypatch.setattr(k, "status", lambda args: gerufen.append("status"))
    monkeypatch.setattr(k, "geheimnisse", lambda args: gerufen.append("geheimnisse"))
    for befehl in ("status", "geheimnisse"):
        monkeypatch.setattr(sys, "argv", ["x", befehl])
        k.main()
    assert gerufen == ["status", "geheimnisse"]  # core.env ändern ist erlaubt, nur kein Neustart der alten Unit
    assert ssh.befehle == []


# ---------------------------------------------------------------- status
def test_status_zeigt_slots_und_schalter_nur_als_namen(monkeypatch, capsys):
    ssh = Ssh("")
    monkeypatch.setattr(k, "ssh", ssh)
    k.status(None)
    befehl = ssh.befehle[-1]
    assert "wamo-core-env liste" in befehl  # Namen der Schlüssel, nie Werte
    assert "wamo-core@" in befehl and "/opt/wamo-core/slots" in befehl
    assert "wamo-entwicklung-buero.conf" in befehl or "wamo-core@.service.d" in befehl
    assert "grep -ho" in befehl  # nur der Name des Schalters
    assert not re.search(r"\bcat\b[^|;]*(core\.env|geheim\.env|\.conf)", befehl)  # nie eine Datei mit Werten ausgeben
    assert not re.search(r"systemctl\s+(restart|start|stop)", befehl)
