"""Tests für die Skripte des Kern-Kanals auf Sokrates-2 (Pakete R9 und R10).

R9: sshd-Block, Totmannschalter, Ruhe-Prüfung und Rückweg-Sperre von wamo-job-einrichten.sh und wamo-job-rueckweg.sh.
R10: wamo-job-kern-einrichten.sh, wamo-job-kern-rueckweg.sh und wamo_job_kern_rauchtest.py (Klassen weiter unten).

Die Skripte laufen im Probelauf (WAMO_JOB_WURZEL, WAMO_JOB_KERN_WURZEL) in einem Wegwerf-Ordner unter dem Temp-Ordner, ohne root und
ohne systemd. Das geht unter Linux und mit Git Bash unter Windows; was echte Rechte oder echte Dienste braucht, ist "nur am Server
prüfbar" (der Rauchtest wamo-job-kern-rauchtest). Ausführen (im Ordner voice-core/):  python -m pytest -q test_job_kern_skripte.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
LINUX = sys.platform.startswith("linux")


def _bash() -> str | None:
    kandidaten = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    kandidaten.append(shutil.which("bash") if os.name != "nt" else None)
    return next((k for k in kandidaten if k and Path(k).exists()), None)


BASH = _bash()
braucht_bash = pytest.mark.skipif(BASH is None, reason="bash fehlt")
braucht_linux = pytest.mark.skipif(not LINUX, reason="Dateirechte, Eigentümer und Prozesse nur unter Linux prüfbar")

EINRICHTEN = INFRA / "wamo-job-einrichten.sh"
RUECKWEG = INFRA / "wamo-job-rueckweg.sh"


def lies(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8")


@pytest.fixture
def wurzel():
    """Ein Wegwerf-Ordner im Temp-Ordner. Liefert (Pfad für Python, Pfad für die Shell). Die Skripte verlangen einen Pfad aus
    A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich: unter Windows ist /tmp der Temp-Ordner von Git Bash."""
    ordner = Path(tempfile.mkdtemp(prefix="wamo-probe-"))
    shell = f"/tmp/{ordner.name}" if os.name == "nt" else str(ordner)
    try:
        yield ordner, shell
    finally:
        shutil.rmtree(ordner, ignore_errors=True)


def lauf(skript: Path, *args: str, shell_wurzel: str, variable: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, variable: shell_wurzel, **(extra_env or {})}
    return subprocess.run([BASH, str(skript), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                          timeout=120)


# ---------------------------------------------------------------- R9: wamo-job-einrichten.sh (Text und Funktionen)
@pytest.fixture(scope="module")
def einrichten() -> str:
    return lies(EINRICHTEN)


@pytest.fixture(scope="module")
def rueckweg() -> str:
    return lies(RUECKWEG)


def _match_block(einrichten: str) -> list[str]:
    m = re.search(r"cat > \"\$neu\" <<'SSHD'\n(.*?)\nSSHD\n", einrichten, re.S)
    assert m, "Heredoc SSHD fehlt"
    block = m.group(1).splitlines()
    return block[block.index("Match User wamo"):block.index("Match all")]


def test_der_match_block_hat_beide_authorizedkeysfile_und_exposeauthinfo(einrichten):
    zeilen = [z.strip() for z in _match_block(einrichten)]
    assert "AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u /etc/ssh/authorized_keys.d/%u-kern" in zeilen
    assert "ExposeAuthInfo yes" in zeilen
    assert "AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u" not in zeilen  # keine alte Einzelzeile daneben
    for bleibt in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "AuthenticationMethods publickey",
                   "ForceCommand /opt/wamo/bin/wamo-job-dispatch", "PermitUserRC no", "MaxSessions 2", "AllowTcpForwarding no",
                   "AllowAgentForwarding no", "AllowStreamLocalForwarding no", "PermitTunnel no", "X11Forwarding no", "PermitTTY no"):
        assert bleibt in zeilen, bleibt
    assert sum(z.startswith("AuthorizedKeysFile") for z in zeilen) == 1 and sum(z.startswith("ExposeAuthInfo") for z in zeilen) == 1


def test_sshd_t_und_die_globalprobe_bleiben_unveraendert(einrichten):
    assert "/usr/sbin/sshd -t" in einrichten
    assert "global_t=$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2)" in einrichten
    assert "grep -qx 'permittty yes' <<<\"$global_t\" && grep -qx 'allowtcpforwarding yes' <<<\"$global_t\"" in einrichten
    assert "Drop-in wirkt über wamo hinaus, zurückgenommen." in einrichten
    assert "sshd -T -C user=wamo,host=pc,addr=192.168.178.2 | grep -E '^(authorizedkeysfile|permittty|allowtcpforwarding|clientaliveinterval|passwordauthentication) '" in einrichten


def test_die_einrichtung_schreibt_nie_in_die_schluesseldatei_des_kerns(einrichten):
    for nr, zeile in enumerate(einrichten.splitlines(), 1):
        if "wamo-kern" not in zeile and "%u-kern" not in zeile:
            continue
        sauber = zeile.strip()
        assert sauber.startswith("#") or sauber.startswith("AuthorizedKeysFile ") or sauber.startswith("echo "), f"Zeile {nr}: {sauber}"
        assert not re.search(r"(>|install|tee|cp|mv|rm|sed -i|truncate|ln)\b.*wamo-kern", sauber) or sauber.startswith("#"), f"Zeile {nr}"
    assert 'KEYS=/etc/ssh/authorized_keys.d/wamo\n' in einrichten  # die PC-Datei bleibt die einzige, die dieses Skript schreibt


def test_die_vorige_wamo_conf_wird_dauerhaft_gesichert_und_der_totmann_stellt_sie_wieder_her(einrichten):
    assert "/var/backups/wamo-job-kern" in einrichten and "wamo.conf.vorher" in einrichten
    assert re.search(r"date (-u )?\+%Y%m%d-%H%M%S", einrichten)
    sicherung = einrichten.index('cp -p "$DROPIN" "$KERN_SICHERUNG/wamo.conf.vorher"')
    neu_schreiben = einrichten.index('install -o root -g root -m 0644 "$neu" "$DROPIN"')
    assert sicherung < neu_schreiben, "erst sichern, dann ersetzen"
    assert 'rm -f "$vorher"' not in einrichten  # die Sicherung bleibt liegen (früher wurde sie nach dem Einrichten gelöscht)
    totmann = einrichten[einrichten.index("systemd-run --quiet --unit=\"wamo-sshd-rueckfall-"):]
    totmann = totmann[:totmann.index("rm -f /run/wamo-sshd-bestaetigt") if "rm -f /run/wamo-sshd-bestaetigt" in totmann else 1500]
    assert "wamo.conf.vorher" in totmann or "$vorher" in totmann
    assert "install -o root -g root -m 0644" in totmann  # stellt die gesicherte Fassung wieder her
    assert "rm -f $DROPIN" in totmann  # gab es keine vorige Fassung, wird die Datei gelöscht
    assert "systemctl reload ssh.service" in totmann
    assert "test -e /run/wamo-sshd-bestaetigt" in totmann


def test_das_modul_des_kern_kanals_wird_installiert_gesichert_und_vorausgesetzt(einrichten):
    pflicht = einrichten[einrichten.index("for datei in wamo-job.sh"):]
    assert "wamo_job_kernkanal.py" in pflicht[:pflicht.index("; do")]
    assert '"waechter/wamo_job_kernkanal.py:wamo_job_kernkanal.py"' in einrichten
    sicherung = einrichten.index("== 2c. Sicherung")
    assert einrichten.index('"waechter/wamo_job_kernkanal.py:wamo_job_kernkanal.py"') < einrichten.index('install -o root -g "$WAMO_USER" -m 0750 "$HERE/wamo_job_dispatch.py"')
    assert sicherung < einrichten.index('"$HERE/wamo_job_kernkanal.py"')
    assert re.search(r'install -o root -g "\$WAMO_USER" -m 0640 .*"\$HERE/wamo_job_kernkanal\.py".* "\$APP/waechter/"', einrichten)
    assert '"$APP/waechter/wamo_job_kernkanal.py"' in einrichten  # Zeilenenden wie bei den anderen Kopien
    kopf = einrichten[:einrichten.index("set -euo pipefail")]
    assert "wamo_job_kernkanal.py" in kopf  # steht in der scp-Liste und in der Liste der Änderungen


def test_der_sshd_block_und_das_modul_stehen_im_kopfkommentar(einrichten):
    kopf = einrichten[:einrichten.index("set -euo pipefail")]
    assert "ExposeAuthInfo" in kopf and "authorized_keys.d/%u-kern" in kopf and "/var/backups/wamo-job-kern" in kopf


def test_die_ruhe_wird_vor_der_ersten_aenderung_geprueft(einrichten):
    assert "ruhe_pruefen() {" in einrichten
    aufruf = einrichten.index("\nruhe_pruefen\n")
    assert aufruf < einrichten.index("== 1. bubblewrap")
    assert aufruf < einrichten.index("systemctl restart wamo-egress.service")  # die Einrichtung startet den Ausgangsfilter neu
    assert "exit 3" in einrichten[einrichten.index("ruhe_pruefen() {"):aufruf]
    assert einrichten.index("pruefe_herkunft\n") < aufruf  # erst die Herkunft, dann die Ruhe


def _funktion(text: str, name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.M | re.S)
    assert m, f"Funktion {name} fehlt"
    return m.group(0)


def _ruhe(einrichten: str, ps_ausgabe: str, timer: str = "", wurzel: str = "", path: str | None = None) -> subprocess.CompletedProcess:
    """Die echte Funktion ruhe_pruefen mit Attrappen für ps und systemctl."""
    snippet = ("set -euo pipefail\nWAMO_USER=wamo\nWURZEL=" + repr(wurzel).replace("'", '"') + "\n"
               + f"ps() {{ printf '%s' {_q(ps_ausgabe)}; [ -n {_q(ps_ausgabe)} ]; }}\n"
               + f"systemctl() {{ printf '%s' {_q(timer)}; }}\n"
               + _funktion(einrichten, "ruhe_pruefen") + "\nruhe_pruefen\necho RUHE\n")
    return subprocess.run([BASH, "-c", snippet], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                          env={**os.environ, **({"PATH": path} if path is not None else {})})


def _q(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


BASIS = "systemd\n(sd-pam)\ndbus-daemon\n"


@braucht_bash
def test_ruhe_ist_gegeben_wenn_nur_der_benutzerdienst_laeuft_und_kein_totmann_offen_ist(einrichten):
    p = _ruhe(einrichten, BASIS)
    assert p.returncode == 0 and "RUHE" in p.stdout, p.stdout + p.stderr
    assert _ruhe(einrichten, "").returncode == 0  # gar kein Prozess von wamo


@braucht_bash
def test_die_portale_des_benutzerdienstes_zaehlen_nicht_als_auftrag(einrichten):
    """Am Server beobachtet (11.11.2026): xdg-document-portal und xdg-permission-store laufen als Benutzerdienste von wamo
    (D-Bus-aktiviert, seit Wochen) und brachten die Ruhe-Prüfung mit Exit 3 zum Abbruch. ps kürzt den Namen auf 15 Zeichen."""
    portale = "xdg-document-po\nxdg-permission-\n"
    p = _ruhe(einrichten, BASIS + portale)
    assert p.returncode == 0 and "RUHE" in p.stdout, p.stdout + p.stderr
    assert _ruhe(einrichten, BASIS + "xdg-document-po\n").returncode == 0
    assert _ruhe(einrichten, BASIS + "xdg-permission-\n").returncode == 0
    for fremd in ("xdg-open\n", "xdg-document-portal-x\n", "xdg-desktop-por\n", "xdg-permission-store-x\n"):  # nur genau diese zwei Namen
        assert _ruhe(einrichten, BASIS + portale + fremd).returncode == 3, fremd
    assert _ruhe(einrichten, BASIS + portale + "claude\n").returncode == 3  # ein Auftrag neben den Portalen bleibt ein Auftrag


@braucht_bash
@pytest.mark.parametrize("ps_ausgabe", [BASIS + "claude\n", BASIS + "bwrap\n", BASIS + "wamo-job-dispatch\n", "node\n",
                                        BASIS + "wamo-job.sh\n", BASIS + "sleep\n"])
def test_ein_laufender_auftrag_oder_irgendein_anderer_prozess_von_wamo_bricht_mit_exit_3_ab(einrichten, ps_ausgabe):
    p = _ruhe(einrichten, ps_ausgabe)
    assert p.returncode == 3 and "RUHE" not in p.stdout, p.stdout + p.stderr
    assert "Ruhe" in p.stderr


@braucht_bash
def test_ein_offener_totmannschalter_bricht_mit_exit_3_ab(einrichten):
    timer = "Wed 2026-11-11 12:05:00 CET 4min left n/a n/a wamo-sshd-rueckfall-1762859000.timer wamo-sshd-rueckfall-1762859000.service\n"
    p = _ruhe(einrichten, BASIS, timer=timer)
    assert p.returncode == 3 and "Totmannschalter" in p.stderr, p.stdout + p.stderr
    assert _ruhe(einrichten, BASIS, timer="0 timers listed.\n").returncode == 0


@braucht_bash
def test_im_probelauf_mit_wurzel_gibt_es_keine_ruhe_pruefung(einrichten):
    p = _ruhe(einrichten, BASIS + "claude\n", timer="wamo-sshd-rueckfall-1.timer\n", wurzel="/tmp/probe")
    assert p.returncode == 0 and "RUHE" in p.stdout


@braucht_bash
def test_ist_ps_nicht_vorhanden_ist_die_ruhe_nicht_prueffbar_und_das_skript_bricht_ab(einrichten):
    snippet = ("set -euo pipefail\nWAMO_USER=wamo\nWURZEL=''\n" + _funktion(einrichten, "ruhe_pruefen") + "\nruhe_pruefen\necho RUHE\n")
    p = subprocess.run([BASH, "-c", "PATH=/nichts; " + snippet], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=30)
    assert p.returncode == 3 and "RUHE" not in p.stdout, p.stdout + p.stderr


def test_die_einrichtung_kennt_den_probelauf_nur_fuer_die_ruhe_und_nie_als_root(einrichten):
    assert 'WURZEL="${WAMO_JOB_WURZEL:-}"' in einrichten
    ab = einrichten[einrichten.index('[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen."'):]
    assert '[ -z "$WURZEL" ] ||' in ab[:600]  # die Einrichtung selbst verweigert die Wurzel (sie schreibt nach /opt, /etc, /srv)


@braucht_bash
def test_die_hinweise_nennen_den_alias_wamo_sokrates2(einrichten, rueckweg):
    for name, text in (("einrichten", einrichten), ("rueckweg", rueckweg)):
        assert not re.search(r"ssh sokrates2\b", text), name
        assert not re.search(r"(?<![-\w])sokrates2:wamo-infra", text), name
    assert "ssh wamo-sokrates2" in einrichten and "ssh wamo-sokrates2" in rueckweg
    assert "wamo-sokrates2:wamo-infra/" in einrichten


@braucht_bash
@pytest.mark.parametrize("skript", ["wamo-job-einrichten.sh", "wamo-job-rueckweg.sh"])
def test_bash_syntax(skript):
    p = subprocess.run([BASH, "-n", str(INFRA / skript)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_die_skripte_haben_unix_zeilenenden_und_keine_gedankenstriche_und_nie_systemctl_start_stop(einrichten, rueckweg):
    for name, text in (("einrichten", einrichten), ("rueckweg", rueckweg)):
        assert "\r" not in text and "\u2014" not in text and "\u2013" not in text, name
    assert not re.search(r"systemctl (restart|start|stop) wamo-core", einrichten + rueckweg)


# ---------------------------------------------------------------- R9: wamo-job-rueckweg.sh im Probelauf
def _sicherung(w: Path, inhalt: str = "alt\n") -> Path:
    app = w / "opt" / "wamo"
    (app / "bin").mkdir(parents=True)
    (app / "waechter").mkdir(parents=True)
    alt = app / "vorher" / "20261110T100000Z"
    (alt / "bin").mkdir(parents=True)
    (alt / "bin" / "wamo-job-dispatch").write_bytes(inhalt.encode())
    (alt / "NEU.txt").write_bytes(b"waechter/wamo_job_kernkanal.py\n")
    (app / "bin" / "wamo-job-dispatch").write_bytes(b"neu\n")
    (app / "waechter" / "wamo_job_kernkanal.py").write_bytes(b"neu\n")
    return app


def _kern_datei(w: Path) -> Path:
    ordner = w / "etc" / "ssh" / "authorized_keys.d"
    ordner.mkdir(parents=True, exist_ok=True)
    datei = ordner / "wamo-kern"
    datei.write_text("restrict ssh-ed25519 AAAA wamo-kern-jobs\n")
    return datei


@braucht_bash
def test_der_rueckweg_verweigert_solange_die_schluesseldatei_des_kerns_existiert(wurzel):
    w, shell = wurzel
    app = _sicherung(w)
    kern = _kern_datei(w)
    p = lauf(RUECKWEG, shell_wurzel=shell, variable="WAMO_JOB_WURZEL")
    assert p.returncode == 3, p.stdout + p.stderr
    meldung = p.stdout + p.stderr
    assert "wamo-kern" in meldung and "wamo-job-kern-rueckweg.sh notaus" in meldung and "--entfernen" in meldung
    assert (app / "bin" / "wamo-job-dispatch").read_text() == "neu\n"  # nichts zurückgespielt
    assert not (app / "vorher" / "20261110T100000Z" / "ZURUECK.txt").exists()
    assert kern.exists()
    p = lauf(RUECKWEG, "--trocken", shell_wurzel=shell, variable="WAMO_JOB_WURZEL")
    assert p.returncode == 3  # auch im Trockenlauf gilt die Sperre


@braucht_bash
def test_ohne_die_schluesseldatei_des_kerns_laeuft_der_rueckweg_wie_bisher(wurzel):
    w, shell = wurzel
    app = _sicherung(w)
    (w / "etc" / "ssh" / "authorized_keys.d").mkdir(parents=True)
    (w / "etc" / "ssh" / "authorized_keys.d" / "wamo").write_text("restrict ssh-ed25519 AAAA wamo-jobs\n")  # die PC-Datei stört nicht
    p = lauf(RUECKWEG, shell_wurzel=shell, variable="WAMO_JOB_WURZEL")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (app / "bin" / "wamo-job-dispatch").read_text() == "alt\n"
    assert not (app / "waechter" / "wamo_job_kernkanal.py").exists()  # das Modul gab es vorher nicht: der Rückweg entfernt es
    assert (app / "vorher" / "20261110T100000Z" / "ZURUECK.txt").is_file()


@braucht_bash
def test_ein_dangling_link_an_der_stelle_der_datei_zaehlt_als_vorhanden(wurzel):
    if os.name == "nt":
        pytest.skip("symbolische Links brauchen unter Windows besondere Rechte")
    w, shell = wurzel
    _sicherung(w)
    ordner = w / "etc" / "ssh" / "authorized_keys.d"
    ordner.mkdir(parents=True)
    (ordner / "wamo-kern").symlink_to(w / "gibt-es-nicht")
    assert lauf(RUECKWEG, shell_wurzel=shell, variable="WAMO_JOB_WURZEL").returncode == 3


def test_der_rueckweg_prueft_die_sperre_vor_dem_zurueckspielen(rueckweg):
    sperre = rueckweg.index("authorized_keys.d/wamo-kern")
    assert sperre < rueckweg.index("== 1. Neueste Sicherung suchen")
    assert "exit 3" in rueckweg[sperre:rueckweg.index("== 1. Neueste Sicherung suchen")]
    kopf = rueckweg[:rueckweg.index("set -euo pipefail")]
    assert "wamo-kern" in kopf and "notaus" in kopf  # der Kopfkommentar erklärt die Sperre


# ================================================================ R10: Rauchtest, wamo-job-kern-einrichten.sh, wamo-job-kern-rueckweg.sh
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import stat as _stat  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

KERN_EINRICHTEN = INFRA / "wamo-job-kern-einrichten.sh"
KERN_RUECKWEG = INFRA / "wamo-job-kern-rueckweg.sh"
RAUCHTEST = INFRA / "wamo_job_kern_rauchtest.py"
VOICE_CORE = Path(__file__).resolve().parent
PROFIL = "6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
RELEASE = "v0.1.0-abc1234"


def _lade_rauchtest():
    spec = importlib.util.spec_from_file_location("wamo_job_kern_rauchtest", RAUCHTEST)
    modul = importlib.util.module_from_spec(spec)
    sys.modules["wamo_job_kern_rauchtest"] = modul
    spec.loader.exec_module(modul)
    return modul


R = _lade_rauchtest()


# ---------------------------------------------------------------- Rauchtest (Aufbau; alle Proben selbst nur am Server prüfbar)
def test_die_ssh_optionen_des_rauchtests_gleichen_dev_agent_ssh_argv(monkeypatch, tmp_path):
    import dev_agent
    schluessel, hosts = tmp_path / "id_ed25519_wamo_jobs", tmp_path / "known_hosts_wamo_jobs"
    monkeypatch.setenv("WAMO_ENTWICKLUNG_SSH_SCHLUESSEL", str(schluessel))
    monkeypatch.setenv("WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS", str(hosts))
    monkeypatch.setenv("WAMO_ENTWICKLUNG_SSH_ZIEL", R.ZIEL)
    monkeypatch.setattr(dev_agent.shutil, "which", lambda name: "/usr/bin/ssh")
    for woerter in (["ping"], ["status", "0123456789abcdef"], ["start", "0123456789abcdef", "kern-x", "recherche"]):
        assert R.ssh_argv(" ".join(woerter), schluessel, hosts, R.ZIEL, exe="/usr/bin/ssh") == dev_agent.ssh_argv(woerter), woerter
    tty = R.ssh_argv("ping", tty=True)
    ohne = R.ssh_argv("ping")
    assert "-tt" in tty and "-T" not in tty and "-T" in ohne and [a for a in tty if a != "-tt"] == [a for a in ohne if a != "-T"]


def test_der_rauchtest_kennt_die_standardpfade_des_kerns():
    import dev_agent
    assert R.ZIEL == dev_agent.SSH_ZIEL_STANDARD
    assert R.SCHLUESSEL.name == "id_ed25519_wamo_jobs" and R.KNOWN_HOSTS.name == dev_agent.SSH_KNOWN_HOSTS_STANDARD.split("/")[-1]


def test_unit_eigenschaften_werden_aus_systemctl_show_uebernommen_aber_keine_pfade_und_keine_befehle():
    show = ("Id=wamo-core@a.service\nExecStart={ path=/opt/x ; argv[]=x }\nNoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=yes\n"
            "PrivateTmp=yes\nCapabilityBoundingSet=\nSystemCallErrorNumber=EPERM\nReadWritePaths=/run/wamo-core\nEnvironment=WAMO_X=1\n"
            "EnvironmentFile=/etc/wamo/core.env\nMemoryMax=4294967296\n")
    erg = R.unit_eigenschaften(show)
    assert erg == ["-p", "NoNewPrivileges=yes", "-p", "ProtectSystem=strict", "-p", "ProtectHome=yes", "-p", "PrivateTmp=yes",
                   "-p", "CapabilityBoundingSet=", "-p", "SystemCallErrorNumber=EPERM"]
    assert not any("Environment" in a or "Exec" in a or "/etc/wamo" in a for a in erg)


def test_die_proben_decken_den_katalog_aus_abschnitt_7_ab_und_starten_nie_einen_gueltigen_auftrag():
    proben = R.proben()
    namen = [p.name for p in proben]
    assert proben[0].ja and proben[0].remote == "ping"
    for erwartet in ("id", "leerer Befehl", "ping; id", "Befehl über 300 Zeichen", "-tt (Terminal)", "-L (Weiterleitung)", "-R (Rückweiterleitung)",
                     "sftp", "pruefen", "Start mit Projekt wamo", "Start ohne Präfix kern-", "Start ohne Feld zugang", "Start mit zugang=server",
                     "fremde Auftrags-ID (status)", "Start bei geschlossenem Kanal"):
        assert erwartet in namen, erwartet
    assert len(next(p for p in proben if p.name == "Befehl über 300 Zeichen").remote) > 300
    for p in proben:
        if p.remote.startswith("start "):
            assert p.kopf is not None  # nur der Kopf, nie die zweite Zeile mit einem Zugang: ein versehentlich gestarteter Lauf endete an der Zugangszeile
            assert "wert" not in json.dumps(p.kopf) and "typ" not in p.kopf
    assert [p.name for p in proben if p.nur_geschlossen] == ["Start bei geschlossenem Kanal"]
    assert R.NUR_AM_SERVER is True


def test_die_bewertung_unterscheidet_ja_und_nein():
    ja, nein = R.proben()[0], R.proben()[1]
    gut = json.dumps({"ok": True, "version": "7", "kanal": "kern", "kernkanal": {"offen": False}})
    assert R.bewerte(ja, 0, gut + "\n")
    for schlecht in (json.dumps({"ok": True, "version": "6", "kanal": "kern"}), json.dumps({"ok": True, "version": "7", "kanal": "pc"}),
                     json.dumps({"ok": False, "version": "7", "kanal": "kern"}), "kein json", ""):
        assert not R.bewerte(ja, 0, schlecht)
    assert not R.bewerte(ja, 2, gut) and not R.bewerte(ja, None, gut)
    assert not R.bewerte(nein, 255, "") and not R.bewerte(nein, 1, "Fehler") and not R.bewerte(nein, 2, "")  # ohne den erwarteten Grund nie
    assert not R.bewerte(nein, 0, "uid=1000") and not R.bewerte(nein, 2, '{"ok": true}') and not R.bewerte(nein, None, "")


# ---------------------------------------------------------------- Rauchtest: jede Nein-Probe kennt ihren erwarteten Grund
ZUSTAND_OFFEN = {"strikt": True, "offen": True, "grund": None, "arten": ["recherche", "entwicklung"], "repos": [], "ablauf": "20301231", "tage_rest": 900}
ZUSTAND_ZU = {**ZUSTAND_OFFEN, "offen": False, "grund": "kanal_zu"}
ZUSTAND_NOTAUS = {**ZUSTAND_OFFEN, "offen": False, "grund": "notaus"}


def _lade_kernkanal():
    import importlib.util
    spec = importlib.util.spec_from_file_location("wamo_job_kernkanal_fuer_rauchtest", INFRA / "wamo_job_kernkanal.py")
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul  # die Datenklassen des Moduls brauchen den Eintrag
    spec.loader.exec_module(modul)
    return modul


def _probe(name: str):
    return next(p for p in R.proben() if p.name == name)


def _ereignis(grund: str) -> str:
    return json.dumps({"type": "wamo", "ereignis": "kanal_gesperrt", "grund": grund}) + "\n"


def _abweisung(text: str) -> str:
    return f"WAMO-Auftrag abgewiesen: {text}\n"


def test_jede_nein_probe_hat_einen_erwarteten_grund_aus_einer_festen_liste():
    kern = _lade_kernkanal()
    for p in R.proben()[1:]:
        assert p.erwartet, p.name
        for alternative in p.erwartet:
            art = alternative[0]
            assert art in ("abgewiesen", "kanal", "zustand", "sshd", "stumm"), (p.name, alternative)
            if art == "stumm":  # nur das Subsystem sftp: dort kommt stderr nie beim Client an, sonst wäre "kein Text" kein Beleg
                assert alternative == ("stumm",) and p.name == "sftp", (p.name, alternative)
            if art == "kanal":
                assert alternative[1:] and all(g in kern.KANAL_GRUENDE for g in alternative[1:]), (p.name, alternative)
            if art == "abgewiesen":
                assert len(alternative) == 2 and alternative[1].isascii(), (p.name, alternative)  # ASCII: unabhängig von der Zeichenkodierung am Server


def test_die_befehlslisten_des_rauchtests_gleichen_denen_des_moduls():
    kern = _lade_kernkanal()
    assert R.BEFEHLE_GESCHLOSSEN == kern.BEFEHLE_GESCHLOSSEN and R.BEFEHLE_NOTAUS == kern.BEFEHLE_NOTAUS


def test_eine_kanal_probe_besteht_nur_mit_dem_erwarteten_grund_aus_dem_ereignis():
    probe = _probe("Start mit Projekt wamo")
    assert R.bewerte(probe, 2, _ereignis("projekt"), _abweisung("Kanal gesperrt (projekt)"), ZUSTAND_OFFEN)
    for falsch in ("art", "zugang", "befehl", "kanal_zu", "dienst"):  # ein anderer Grund heißt: die Regel dieser Probe wurde nie erreicht
        assert not R.bewerte(probe, 2, _ereignis(falsch), _abweisung(f"Kanal gesperrt ({falsch})"), ZUSTAND_OFFEN), falsch
    assert not R.bewerte(probe, 255, "", "ssh: connect to host 192.168.178.136 port 22: Connection refused", ZUSTAND_OFFEN)  # ssh-Fehler
    assert not R.bewerte(probe, 255, _ereignis("projekt"), "", ZUSTAND_OFFEN)  # Exit 255 gilt nie, auch nicht mit Ereignis
    assert not R.bewerte(probe, None, _ereignis("projekt"), "", ZUSTAND_OFFEN)  # Zeitüberschreitung
    assert not R.bewerte(probe, 2, "", "", ZUSTAND_OFFEN)  # Exit 2 ohne Ereignis
    assert not R.bewerte(probe, 2, "kein json\n", _abweisung("Auftrag ist leer"), ZUSTAND_OFFEN)
    assert not R.bewerte(probe, 1, _ereignis("projekt"), "", ZUSTAND_OFFEN)
    assert not R.bewerte(probe, 0, _ereignis("projekt") + '{"ok": true}\n', "", ZUSTAND_OFFEN)
    assert not R.bewerte(probe, 2, '{"type": "wamo", "ereignis": "belegt", "grund": "projekt"}\n', "", ZUSTAND_OFFEN)  # anderes Ereignis


def test_bei_geschlossenem_kanal_gilt_der_grund_des_kanals_und_sonst_nicht():
    for probe_name in ("Start mit Projekt wamo", "Start ohne Feld zugang", "pruefen", "Start bei geschlossenem Kanal"):
        probe = _probe(probe_name)
        assert R.bewerte(probe, 2, _ereignis("kanal_zu"), "", ZUSTAND_ZU), probe_name
        assert R.bewerte(probe, 2, _ereignis("notaus"), "", ZUSTAND_NOTAUS), probe_name
        assert not R.bewerte(probe, 2, _ereignis("kanal_zu"), "", ZUSTAND_OFFEN), probe_name  # offener Kanal: kanal_zu kann nicht kommen
        assert not R.bewerte(probe, 2, _ereignis("notaus"), "", ZUSTAND_ZU), probe_name  # der Grund muss der des Kanals sein
        assert not R.bewerte(probe, 2, _ereignis("kanal_zu"), "", None), probe_name  # Zustand unbekannt


def test_die_befehlsproben_nennen_befehl_und_die_proben_fremder_auftraege_nennen_fremd():
    pruefen = _probe("pruefen")
    assert R.bewerte(pruefen, 2, _ereignis("befehl"), "", ZUSTAND_OFFEN)
    assert not R.bewerte(pruefen, 2, _ereignis("fremd"), "", ZUSTAND_OFFEN)
    for name in ("fremde Auftrags-ID (status)", "fremde Auftrags-ID (cancel)"):
        probe = _probe(name)
        assert R.bewerte(probe, 2, _ereignis("fremd"), "", ZUSTAND_OFFEN), name
        assert R.bewerte(probe, 2, _ereignis("fremd"), "", ZUSTAND_ZU), name  # status und cancel gehen auch bei geschlossenem Kanal bis zur Prüfung
        assert not R.bewerte(probe, 2, _ereignis("befehl"), "", ZUSTAND_OFFEN), name
    assert not R.bewerte(_probe("fremde Auftrags-ID (cancel)"), 2, _ereignis("fremd"), "", ZUSTAND_NOTAUS)  # cancel ist beim Notaus gesperrt
    assert R.bewerte(_probe("fremde Auftrags-ID (cancel)"), 2, _ereignis("notaus"), "", ZUSTAND_NOTAUS)


def test_die_start_proben_nennen_ihre_regel_projekt_oder_zugang():
    for name in ("Start mit Projekt wamo", "Start ohne Präfix kern-"):
        assert R.bewerte(_probe(name), 2, _ereignis("projekt"), "", ZUSTAND_OFFEN), name
    for name in ("Start ohne Feld zugang", "Start mit zugang=server"):
        assert R.bewerte(_probe(name), 2, _ereignis("zugang"), "", ZUSTAND_OFFEN), name
        assert not R.bewerte(_probe(name), 2, _ereignis("projekt"), "", ZUSTAND_OFFEN), name


def test_eine_abweisung_des_dispatchers_besteht_nur_mit_exit_2_und_dem_erwarteten_text():
    meldungen = {"id": "unbekannter Befehl", "leerer Befehl": "kein Befehl (Shell-Zugang ist mit diesem Schlüssel gesperrt)",
                 "ping; id": "Befehl hat unzulässige Zeichen oder Form", "Befehl über 300 Zeichen": "Befehl hat unzulässige Zeichen oder Form"}
    for name, text in meldungen.items():
        probe = _probe(name)
        assert R.bewerte(probe, 2, "", _abweisung(text), ZUSTAND_OFFEN), name
        assert R.bewerte(probe, 2, "", _abweisung(text), ZUSTAND_ZU), name  # der Befehl wird vor dem Kanal geprüft
        assert not R.bewerte(probe, 255, "", _abweisung(text), ZUSTAND_OFFEN), name  # ssh-Fehler
        assert not R.bewerte(probe, 255, "", "Permission denied (publickey).", ZUSTAND_OFFEN), name
        assert not R.bewerte(probe, None, "", "", ZUSTAND_OFFEN), name
        assert not R.bewerte(probe, 2, "", _abweisung("Auftrag ist leer"), ZUSTAND_OFFEN), name  # falscher Grund
        assert not R.bewerte(probe, 2, _ereignis("befehl"), "", ZUSTAND_OFFEN), name  # ein Kanal-Ereignis ist hier nicht der Grund
        assert not R.bewerte(probe, 0, '{"ok": true, "version": "7"}', "", ZUSTAND_OFFEN), name
        assert not R.bewerte(probe, 2, "", "", ZUSTAND_OFFEN), name  # Exit 2 ohne Text
    assert not R.bewerte(_probe("id"), 2, "", _abweisung("kein Befehl (Shell-Zugang ist mit diesem Schlüssel gesperrt)"), ZUSTAND_OFFEN)


def test_die_proben_der_ssh_ebene_brauchen_die_meldung_des_servers_nicht_nur_einen_fehlercode():
    r_probe, tty, weiterleitung = _probe("-R (Rückweiterleitung)"), _probe("-tt (Terminal)"), _probe("-L (Weiterleitung)")
    assert R.bewerte(r_probe, 255, "", "Error: remote port forwarding failed for listen port 19998", ZUSTAND_OFFEN)
    assert not R.bewerte(r_probe, 255, "", "ssh: connect to host 192.168.178.136 port 22: Connection refused", ZUSTAND_OFFEN)
    assert not R.bewerte(r_probe, 255, "", "", ZUSTAND_OFFEN)
    assert not R.bewerte(r_probe, 0, '{"ok": true}', "", ZUSTAND_OFFEN)  # die Weiterleitung wurde angenommen
    assert not R.bewerte(r_probe, None, "", "Error: remote port forwarding failed for listen port 19998", ZUSTAND_OFFEN)
    # ein Terminal wird verweigert, der erlaubte ping antwortet trotzdem: gilt nur mit der Meldung
    assert R.bewerte(tty, 0, '{"ok": true}\n', "PTY allocation request failed on channel 0", ZUSTAND_OFFEN)
    assert not R.bewerte(tty, 0, '{"ok": true}\n', "", ZUSTAND_OFFEN)
    assert not R.bewerte(tty, 255, "", "Permission denied (publickey).", ZUSTAND_OFFEN)
    assert R.bewerte(weiterleitung, 255, "", "channel 0: open failed: administratively prohibited: open failed", ZUSTAND_OFFEN)
    assert not R.bewerte(weiterleitung, 255, "", "Host key verification failed.", ZUSTAND_OFFEN)
    assert not R.bewerte(weiterleitung, 0, "", "", ZUSTAND_OFFEN)


def test_sftp_gilt_bei_einer_abweisung_des_dispatchers_oder_einer_verweigerung_des_servers():
    sftp = _probe("sftp")
    assert R.bewerte(sftp, 2, "", _abweisung("Befehl hat unzulässige Zeichen oder Form"), ZUSTAND_OFFEN)
    assert R.bewerte(sftp, 2, "", _abweisung("unbekannter Befehl"), ZUSTAND_OFFEN)
    assert R.bewerte(sftp, 255, "", "subsystem request failed on channel 0", ZUSTAND_OFFEN)
    assert not R.bewerte(sftp, 255, "", "Connection closed by 192.168.178.136 port 22", ZUSTAND_OFFEN)
    assert not R.bewerte(sftp, 0, "", "", ZUSTAND_OFFEN)


def test_sftp_gilt_auch_als_stumme_abweisung_denn_sshd_leitet_stderr_eines_subsystems_nicht_weiter():
    """Am Server beobachtet (11.11.2026): ssh -s liefert Exit 2 ohne jeden Text, das Audit-Log des Dispatchers zeigt die Abweisung."""
    sftp = _probe("sftp")
    assert R.bewerte(sftp, 2, "", "", ZUSTAND_OFFEN)
    assert R.bewerte(sftp, 2, "", "", ZUSTAND_ZU)  # der Befehl wird vor dem Kanal geprüft
    assert not R.bewerte(sftp, 2, '{"ok": true, "version": "7", "kanal": "kern"}', "", ZUSTAND_OFFEN)  # eine ok-Antwort ist nie eine Abweisung
    assert not R.bewerte(sftp, 2, _ereignis("befehl"), "", ZUSTAND_OFFEN)  # Ausgabe auf stdout: nicht der stumme Fall
    for code in (0, 1, 255, None):  # nur der Exit-Code 2 des Dispatchers gilt
        assert not R.bewerte(sftp, code, "", "", ZUSTAND_OFFEN), code
    assert not R.bewerte(_probe("id"), 2, "", "", ZUSTAND_OFFEN)  # die Ausnahme gilt nur für das Subsystem


def test_start_mit_projekt_wamo_gilt_auch_mit_der_abweisung_durch_den_selbstschutz():
    """Am Server beobachtet (11.11.2026): Der Selbstschutz (check_project) lehnt das Projekt wamo vor der Regel des Kern-Kanals ab, ohne Ereignis."""
    probe = _probe("Start mit Projekt wamo")
    text = "Das WAMO-Repo selbst ist für Aufträge gesperrt"
    assert R.bewerte(probe, 2, "", _abweisung(text), ZUSTAND_OFFEN)
    assert R.bewerte(probe, 2, "", _abweisung(text), ZUSTAND_ZU)  # der Selbstschutz greift unabhängig vom Zustand des Kanals
    assert not R.bewerte(probe, 2, "", _abweisung("Auftrag ist leer"), ZUSTAND_OFFEN)  # ein anderer Text zählt nicht
    assert not R.bewerte(probe, 255, "", _abweisung(text), ZUSTAND_OFFEN)  # ssh-Fehler
    assert not R.bewerte(probe, 0, "", _abweisung(text), ZUSTAND_OFFEN)
    assert not R.bewerte(_probe("Start ohne Präfix kern-"), 2, "", _abweisung(text), ZUSTAND_OFFEN)  # nur die Probe mit dem Namen wamo


def test_der_grund_steht_in_der_ausgabe_der_probe_nie_der_rohe_fehlertext():
    probe = _probe("Start mit Projekt wamo")
    ok, text = R.urteil(probe, 2, _ereignis("art"), _abweisung("Kanal gesperrt (art)"), ZUSTAND_OFFEN)
    assert not ok and "art" in text and "projekt" in text  # Grund und Erwartung
    ok, text = R.urteil(probe, 255, "", "ssh: connect to host 192.168.178.136 port 22: Connection refused", ZUSTAND_OFFEN)
    assert not ok and "255" in text and "Connection refused" not in text
    ok, text = R.urteil(probe, None, "", "", ZUSTAND_OFFEN)
    assert not ok and "Zeitüberschreitung" in text
    ok, text = R.urteil(probe, 2, _ereignis("projekt"), "", ZUSTAND_OFFEN)
    assert ok and "projekt" in text


def test_start_proben_nehmen_die_art_aus_dem_zustand_des_kanals():
    probe = _probe("Start mit Projekt wamo")
    assert R.probe_argv(probe, art="entwicklung")[-1] == f"start {R.JOB_FREMD} wamo entwicklung"
    assert R.art_waehlen(ZUSTAND_OFFEN) == "recherche"
    assert R.art_waehlen({**ZUSTAND_OFFEN, "arten": ["entwicklung"]}) == "entwicklung"
    assert R.art_waehlen({**ZUSTAND_OFFEN, "arten": []}) == "recherche" and R.art_waehlen(None) == "recherche"
    for p in R.proben():
        assert not any("{art}" in wort for wort in R.probe_argv(p)), p.name


def test_zusaetzliche_ssh_optionen_stehen_vor_den_basis_optionen_denn_die_erste_angabe_gilt():
    for name in ("-R (Rückweiterleitung)",):
        argv = R.probe_argv(_probe(name))
        assert argv.index("ClearAllForwardings=no") < argv.index("ClearAllForwardings=yes"), name
        assert argv.index("ExitOnForwardFailure=yes") > 0 and "-R" in argv
        assert argv[argv.index("--") + 1:] == [R.ZIEL, "ping"]
    weiterleitung = R.probe_argv(_probe("-L (Weiterleitung)"))
    assert "-W" in weiterleitung and weiterleitung[weiterleitung.index("-W") + 1] == "127.0.0.1:22"
    assert weiterleitung[weiterleitung.index("--") + 1:] == [R.ZIEL]  # -W ersetzt den Befehl
    sftp = R.probe_argv(_probe("sftp"))
    assert "-s" in sftp and sftp[-1] == "sftp" and "-T" not in sftp
    tty = R.probe_argv(_probe("-tt (Terminal)"))
    assert "-tt" in tty and "-T" not in tty


def test_der_rauchtest_ruft_die_ja_probe_nur_einmal_je_lauf(monkeypatch):
    aufrufe = {"ja": 0, "nein": 0}

    def ja(slot):
        aufrufe["ja"] += 1
        return ZUSTAND_OFFEN

    monkeypatch.setattr(R, "umgebung_pruefen", lambda produktiv: None)
    monkeypatch.setattr(R, "aktiver_slot", lambda: "a")
    monkeypatch.setattr(R, "ja_probe", ja)
    monkeypatch.setattr(R, "nein_proben", lambda slot, zustand: aufrufe.__setitem__("nein", aufrufe["nein"] + 1))
    for name in ("schluessel_und_hosts", "isolation", "dateirechte"):
        monkeypatch.setattr(R, name, lambda *a: None)
    monkeypatch.setattr(R, "summe", lambda pfad: "x")
    monkeypatch.setattr(R, "ergebnisse", [])
    assert R.main(["--ping"]) == 0 and aufrufe == {"ja": 1, "nein": 0}
    monkeypatch.setattr(R, "ergebnisse", [])
    aufrufe.update(ja=0, nein=0)
    assert R.main(["--produktiv"]) == 0 and aufrufe == {"ja": 1, "nein": 1}


def test_der_rauchtest_endet_bei_falscher_aufrufform_und_ausserhalb_des_servers_mit_exit_2():
    assert R.main([]) == 2 and R.main(["--unfug"]) == 2 and R.main(["--ping", "--produktiv"]) == 2
    if not LINUX:
        assert R.main(["--ping"]) == 2 and R.main(["--produktiv"]) == 2
    assert (R.EXIT_OK, R.EXIT_PROBE, R.EXIT_UMGEBUNG) == (0, 1, 2)


def test_der_rauchtest_hat_keine_geheimnisse_keine_gedankenstriche_und_unix_zeilenenden():
    roh = RAUCHTEST.read_bytes()
    text = roh.decode("utf-8")
    assert b"\r" not in roh and chr(0x2014) not in text and chr(0x2013) not in text
    for muster in (r"sk-ant-[A-Za-z0-9_-]{20,}", r"ghp_[A-Za-z0-9]{20,}", r"-----BEGIN", r"geheim\.env", r"core\.env"):
        assert not re.search(muster, text), muster
    assert not re.search(r"systemctl\s+(restart|start|stop)", text)


@braucht_linux
def test_dateirechte_pruefung_meldet_modus_und_besitzer_ohne_inhalt(tmp_path):
    datei = tmp_path / "x"
    datei.write_text("geheim")
    datei.chmod(0o640)
    import grp
    import pwd
    ok, text = R.datei_ok(datei, 0o640, pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name)
    assert ok and "geheim" not in text
    assert R.datei_ok(datei, 0o600, "root", "root")[0] is False


# ---------------------------------------------------------------- Probelauf der Skripte
@pytest.fixture(scope="module")
def hostschluessel():
    """Der Hostschlüssel des Wegwerf-Systems und sein Fingerabdruck: einmal je Modul erzeugt (ssh-keygen startet zweimal), je Test nur kopiert.
    Liefert (privat, öffentlich als Bytes, Fingerabdruck)."""
    if BASH is None:
        pytest.skip("bash fehlt")
    ordner = Path(tempfile.mkdtemp(prefix="wamo-hostkey-"))
    shell = f"/tmp/{ordner.name}" if os.name == "nt" else str(ordner)
    try:
        subprocess.run([BASH, "-c", f'ssh-keygen -q -t ed25519 -N "" -f "{shell}/ssh_host_ed25519_key"'], check=True, capture_output=True)
        fp = subprocess.run([BASH, "-c", f'ssh-keygen -l -E sha256 -f "{shell}/ssh_host_ed25519_key.pub"'], check=True, capture_output=True,
                            text=True).stdout.split()[1]
        yield (ordner / "ssh_host_ed25519_key").read_bytes(), (ordner / "ssh_host_ed25519_key.pub").read_bytes(), fp
    finally:
        shutil.rmtree(ordner, ignore_errors=True)


@pytest.fixture
def welt(wurzel, hostschluessel):
    """Ein Wegwerf-System: Hostschlüssel, sshd-Block, PC-Datei, Dispatcher 7, Module, Release, Attrappen für systemctl, die Slots usw."""
    w, shell = wurzel

    def schreibe(rel: str, text: str | bytes) -> Path:
        pfad = w / rel
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        if rel.startswith("attrappen/"):
            pfad.chmod(0o755)  # unter Linux findet die Shell Attrappen nur mit Ausführungsrecht (die Skripte prüfen auch -x auf runuser)
        return pfad

    (w / "etc" / "ssh").mkdir(parents=True)
    privat, oeffentlich, fp = hostschluessel
    (w / "etc/ssh/ssh_host_ed25519_key").write_bytes(privat)
    (w / "etc/ssh/ssh_host_ed25519_key").chmod(0o600)
    (w / "etc/ssh/ssh_host_ed25519_key.pub").write_bytes(oeffentlich)
    block = _match_block(lies(EINRICHTEN))
    schreibe("etc/ssh/sshd_config.d/wamo.conf", "\n".join(block) + "\nMatch all\n")
    schreibe("etc/ssh/authorized_keys.d/wamo", 'restrict,from="192.168.178.0/24",command="/opt/wamo/bin/wamo-job-dispatch" ssh-ed25519 AAAAC3PC wamo-jobs\n')
    schreibe("opt/wamo/bin/wamo-job-dispatch", '#!/usr/bin/python3 -I\nVERSION = "7"\n')
    schreibe("opt/wamo/waechter/wamo_job_kernkanal.py", (INFRA / "wamo_job_kernkanal.py").read_bytes())
    schreibe("opt/wamo/waechter/werkzeugkarte.py", (VOICE_CORE / "werkzeugkarte.py").read_bytes())
    schreibe(f"opt/wamo-core/releases/{RELEASE}/voice-core/entwicklung_betrieb.py", "SCHRANKEN_VERSION = 1\n")
    (w / "srv" / "wamo" / "jobs").mkdir(parents=True)
    (w / "srv" / "wamo" / "projekte").mkdir(parents=True)
    a = w / "attrappen"
    a.mkdir()
    schreibe("attrappen/systemctl", '''#!/usr/bin/env bash
A="$(dirname "$0")"
echo "systemctl $*" >> "$A/aufrufe.log"
case "$1" in
  daemon-reload) exit 0 ;;
  list-timers) cat "$A/timers.txt" 2>/dev/null || true ;;
  is-active) z="$(grep "^${2%.service} " "$A/aktiv.txt" 2>/dev/null | cut -d' ' -f2)"; echo "${z:-inactive}"; [ "$z" = active ] ;;
  show) s="${@: -1}"; cat "$A/pid_${s:10:1}" 2>/dev/null || echo 0 ;;
  *) exit 0 ;;
esac
''')
    schreibe("attrappen/wamo-core-slots.sh", '''#!/usr/bin/env bash
A="$(dirname "$0")"
echo "slots $* dropin=$([ -e "$A/../etc/systemd/system/wamo-core@.service.d/wamo-entwicklung-buero.conf" ] && echo ja || echo nein)" >> "$A/aufrufe.log"
case "$1" in
  status) cat "$A/slots_status.txt" 2>/dev/null || true ;;
  umschalten-kern)
    modus="$(cat "$A/slots_modus.txt" 2>/dev/null || echo ok)"
    neu() { printf 'Slot a: /opt/wamo-core/releases/v0.0.9  inactive\\nSlot b: /opt/wamo-core/releases/%s  active\\n' "$2" > "$A/slots_status.txt"; }
    case "$modus" in ok) neu "$@" ;; fehl_nachher) neu "$@"; exit 1 ;; fehl_vorher) exit 1 ;; esac ;;
esac
''')
    schreibe("attrappen/wamo-core-env", '#!/usr/bin/env bash\ncat "$(dirname "$0")/core_env.txt" 2>/dev/null || printf "SONIOX_API_KEY: gesetzt\\n"\n')
    schreibe("attrappen/claude", '#!/usr/bin/env bash\ncat "$(dirname "$0")/claude_help.txt" 2>/dev/null || echo "--max-turns --max-budget-usd"\n')
    schreibe("attrappen/logger", '#!/usr/bin/env bash\necho "logger $*" >> "$(dirname "$0")/aufrufe.log"\n')
    # runuser: hält fest, was als wamo-core läuft (ein Wechsel des Benutzers findet im Probelauf nicht statt)
    schreibe("attrappen/runuser", '#!/usr/bin/env bash\necho "runuser $*" >> "$(dirname "$0")/aufrufe.log"\nshift 3\nexec "$@"\n')
    schreibe("attrappen/fail2ban-client", '''#!/usr/bin/env bash
A="$(dirname "$0")"
[ -f "$A/fail2ban_aktiv" ] || exit 1
case "$*" in
  status) echo "Jail list: sshd" ;;
  "get sshd ignoreself") cat "$A/ignoreself.txt" 2>/dev/null || echo false ;;
  "get sshd ignoreip") cat "$A/ignoreip.txt" 2>/dev/null ;;
esac
''')
    ergebnis = {"w": w, "shell": shell, "fp": fp, "schreibe": schreibe, "a": a}
    ergebnis["stand0"] = stand_fingerabdruck(w)  # die unberührte Welt: nur auf ihr gilt der gemerkte Lauf von schluessel_einrichten
    return ergebnis


def kern(welt, skript: Path, *args: str, extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {"WAMO_JOB_KERN_PYTHON": sys.executable, **(extra or {})}
    return lauf(skript, *args, shell_wurzel=welt["shell"], variable="WAMO_JOB_KERN_WURZEL", extra_env=env)


def einr(welt, *args: str, **kw) -> subprocess.CompletedProcess:
    return kern(welt, KERN_EINRICHTEN, *args, **kw)


def rueck(welt, *args: str, **kw) -> subprocess.CompletedProcess:
    return kern(welt, KERN_RUECKWEG, *args, **kw)


def ablauf_in(tage: int) -> str:
    return (datetime.now() + timedelta(days=tage)).strftime("%Y%m%d")


def dateien_liste(w: Path) -> dict[str, str]:
    """Pfad -> Prüfsumme aller Dateien (ohne die Attrappen), um 'ändert nichts' zu prüfen."""
    erg = {}
    for p in sorted(w.rglob("*")):
        rel = p.relative_to(w).as_posix()
        if p.is_file() and not rel.startswith("attrappen/"):
            erg[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return erg


def ohne_spur(liste: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in liste.items() if "aktionen.jsonl" not in k}


def stand_fingerabdruck(w: Path) -> str:
    """Prüfsumme über alle Ordner und Dateien der Welt (ohne das Protokoll der Attrappen): gleich heißt unberührt."""
    h = hashlib.sha256()
    for p in sorted(w.rglob("*")):
        rel = p.relative_to(w).as_posix()
        if rel == "attrappen/aufrufe.log":
            continue
        h.update(rel.encode("utf-8") + (b"\0" + p.read_bytes() if p.is_file() else b"\0dir") + b"\n")
    return h.hexdigest()


_SCHLUESSEL_STAND: dict[str, tuple[Path, subprocess.CompletedProcess]] = {}


@pytest.fixture(scope="module", autouse=True)
def _gemerkte_staende_aufraeumen():
    yield
    for ordner, _ in _SCHLUESSEL_STAND.values():
        shutil.rmtree(ordner, ignore_errors=True)
    _SCHLUESSEL_STAND.clear()


def schluessel_einrichten(welt, ablauf: str | None = None, echt: bool = False):
    """Führt wamo-job-kern-einrichten.sh schluessel aus (Standard: Ablauf in 30 Tagen). Das Skript braucht dafür unter Git Bash gut zwei
    Sekunden, und 30 Tests fangen mit genau diesem Zustand an: Der Standardlauf auf der unberührten Welt läuft deshalb nur einmal je Modul,
    danach wird sein Ergebnis in die Welt des Tests kopiert (wie bei einem echten Lauf, nur ohne ihn zu wiederholen). Ein anderer Ablauf, eine
    veränderte Welt oder echt=True (Tests, die den Lauf selbst prüfen) führen das Skript immer aus."""
    ablauf = ablauf or ablauf_in(30)
    w = welt["w"]
    gemerkt = not echt and ablauf == ablauf_in(30) and stand_fingerabdruck(w) == welt["stand0"]
    if gemerkt and welt["stand0"] in _SCHLUESSEL_STAND:
        ordner, p = _SCHLUESSEL_STAND[welt["stand0"]]
        for kind in list(w.iterdir()):
            shutil.rmtree(kind) if kind.is_dir() else kind.unlink()
        shutil.copytree(ordner, w, dirs_exist_ok=True, symlinks=True)
        return p
    p = einr(welt, "schluessel", "--ablauf", ablauf, "--hostfingerabdruck", welt["fp"])
    assert p.returncode == 0, p.stdout + p.stderr
    if gemerkt:
        ordner = Path(tempfile.mkdtemp(prefix="wamo-stand-"))
        shutil.copytree(w, ordner, dirs_exist_ok=True, symlinks=True)
        _SCHLUESSEL_STAND[welt["stand0"]] = (ordner, p)
    return p


def kern_auftrag(welt, job: str, kanal: str = "kern", laeuft: bool = False) -> Path:
    ordner = welt["w"] / "srv" / "wamo" / "jobs" / job
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "auftrag.json").write_text(json.dumps({"job": job, "projekt": "kern-x", "art": "recherche", "repo": None, "kanal": kanal}), encoding="utf-8")
    (ordner / "status.json").write_text(json.dumps({"ergebnis": "läuft" if laeuft else "fertig"}, ensure_ascii=False), encoding="utf-8")
    return ordner


JOB_A, JOB_B = "a" * 16, "b" * 16


# ---------------------------------------------------------------- Aufrufformen (Exit 2)
FP_FORM = "SHA256:" + "A" * 43


@braucht_bash
@pytest.mark.parametrize("args", [
    [], ["unfug"], ["schluessel"], ["schluessel", "--trocken", "--unfug"], ["schluessel", "--ablauf", "morgen", "--hostfingerabdruck", FP_FORM],
    ["schluessel", "--ablauf", "20200101", "--hostfingerabdruck", FP_FORM], ["schluessel", "--ablauf", ablauf_in(401), "--hostfingerabdruck", FP_FORM],
    ["schluessel", "--hostfingerabdruck", "SHA256:kurz"], ["schluessel", "--hostfingerabdruck"],
    ["kanal"], ["kanal", "--repos", "acme/x"], ["kanal", "--arten", "recherche", "--max-turns", "201"], ["kanal", "--arten", "recherche", "--max-turns", "0"],
    ["kanal", "--arten", "recherche", "--max-budget-usd", "20.01"], ["kanal", "--arten", "recherche", "--max-budget-usd", "2.001"],
    ["kanal", "--arten", "recherche", "--max-budget-usd", "0"], ["kanal", "--arten", "recherche", "--max-budget-usd", "2,00"],
    ["kanal", "--arten", "recherche", "--repos", "acme/wamo"], ["kanal", "--arten", "recherche", "--repos", "acme/WAMO"],
    ["kanal", "--arten", "recherche", "--repos", ",".join(f"acme/r{i}" for i in range(11))], ["kanal", "--arten", "recherche", "--repos", "acme"],
    ["kanal", "--arten", "recherche", "--repos", "acme/a,acme/a"], ["kanal", "--arten", "recherche", "--dienste", "supabase"],
    ["kanal", "zu", "--arten", "recherche"], ["kanal", "--unfug"],
    ["schalter"], ["schalter", "an"], ["schalter", "ein", RELEASE], ["schalter", "an", "release-ohne-v", "--profil", PROFIL], ["schalter", "an", RELEASE],
    ["schalter", "an", RELEASE, "--profil", "kein-uuid"], ["schalter", "an", RELEASE, "--profil", PROFIL.upper()],
    ["schalter", "an", RELEASE, "--profil", PROFIL, "--kosten-auftrag", "0"], ["schalter", "an", RELEASE, "--profil", PROFIL, "--kosten-tag", "1,5"],
    ["schalter", "aus", RELEASE, "--profil", PROFIL], ["notaus-aufheben", "extra"], ["status", "extra"],
])
def test_falsche_aufrufformen_enden_mit_exit_2_und_schreiben_nichts(args, welt):
    vorher = dateien_liste(welt["w"])
    p = einr(welt, *args)
    assert p.returncode == 2, (args, p.stdout, p.stderr)
    assert dateien_liste(welt["w"]) == vorher
    assert not (welt["w"] / "var" / "log").exists()  # auch keine Bedienspur für eine falsche Aufrufform


@braucht_bash
@pytest.mark.parametrize("args", [[], ["--unfug"], ["notaus", "--entfernen"], ["--trocken"], ["notaus", "extra"]])
def test_der_rueckweg_kennt_nur_notaus_und_entfernen_mit_trocken(args, welt):
    assert rueck(welt, *args).returncode == 2


# ---------------------------------------------------------------- schluessel
@braucht_bash
def test_schluessel_trocken_prueft_nur_und_schreibt_nichts(welt):
    vorher = dateien_liste(welt["w"])
    p = einr(welt, "schluessel", "--trocken")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Trockenlauf" in p.stdout and "OpenSSH" in p.stdout
    assert dateien_liste(welt["w"]) == vorher
    assert not (welt["w"] / "var").exists()


@braucht_bash
def test_schluessel_legt_alles_an_mit_der_zeilenform_aus_i1_ohne_expiry_time(welt):
    w = welt["w"]
    pc_vorher = hashlib.sha256((w / "etc/ssh/authorized_keys.d/wamo").read_bytes()).hexdigest()
    ablauf = ablauf_in(30)
    p = schluessel_einrichten(welt, ablauf, echt=True)
    assert "Probelauf" in p.stdout and "keine Ja-Probe" in p.stdout
    assert (w / "var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs").is_file()
    pub = (w / "var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs.pub").read_text().split()
    zeile = (w / "etc/ssh/authorized_keys.d/wamo-kern").read_text()
    assert zeile == f'restrict,from="192.168.178.136",command="/opt/wamo/bin/wamo-job-dispatch" {pub[0]} {pub[1]} wamo-kern-jobs\n'
    assert "expiry-time" not in zeile and zeile.count("\n") == 1
    host = (w / "etc/ssh/ssh_host_ed25519_key.pub").read_text().split()
    assert (w / "var/lib/wamo-core/.ssh/known_hosts_wamo_jobs").read_text() == f"192.168.178.136 {host[0]} {host[1]}\n"
    konfig = (w / "etc/wamo/kernkanal.conf").read_text(encoding="utf-8").splitlines()
    assert "KANAL=zu" in konfig and f"ABLAUF={ablauf}" in konfig and "ARTEN=" in konfig and "REPOS=" in konfig and "DIENSTE=" in konfig
    for std in ("MAX_TURNS=40", "MAX_BUDGET_USD=2.00", "MAX_START_STUNDE=4", "MAX_START_TAG=8", "MAX_LAUFZEIT_TAG_S=14400", "MAX_FOLGEN=5",
                "MIN_ABSTAND_S=60", "MIN_PLATZ_MB=2048", "MAX_VERSUCH_STUNDE=20"):
        assert std in konfig, std
    assert (w / "opt/wamo-core/bin/wamo-job-kern-rauchtest").read_bytes() == RAUCHTEST.read_bytes()
    assert hashlib.sha256((w / "etc/ssh/authorized_keys.d/wamo").read_bytes()).hexdigest() == pc_vorher  # die PC-Datei bleibt unverändert
    assert not list(INFRA.glob("*.pub")) and not list(INFRA.rglob("*wamo_jobs*"))  # nie ein öffentlicher Schlüssel unter ~/wamo-infra
    spur = [json.loads(z) for z in (w / "var/log/wamo-job-kern/aktionen.jsonl").read_text().splitlines()]
    assert len(spur) == 1 and spur[0]["aktion"] == "schluessel" and spur[0]["ergebnis"] == "ok" and set(spur[0]) == {"t", "aktion", "ergebnis"}
    assert "logger -t wamo-job-kern aktion=schluessel ergebnis=ok" in (welt["a"] / "aufrufe.log").read_text()


@braucht_bash
@braucht_linux
def test_schluessel_setzt_die_rechte_nach_e3(welt):
    schluessel_einrichten(welt)
    w = welt["w"]

    def modus(rel):
        return oct(_stat.S_IMODE((w / rel).stat().st_mode))[2:]
    assert modus("var/lib/wamo-core/.ssh") == "700" and modus("var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs") == "600"
    assert modus("var/lib/wamo-core/.ssh/known_hosts_wamo_jobs") == "644" and modus("etc/ssh/authorized_keys.d/wamo-kern") == "644"
    assert modus("etc/wamo/kernkanal.conf") == "640" and modus("opt/wamo-core/bin/wamo-job-kern-rauchtest") == "755"
    assert modus("var/log/wamo-job-kern") == "700" and modus("var/log/wamo-job-kern/aktionen.jsonl") == "640"


@braucht_bash
def test_ein_zweiter_lauf_aendert_nichts_und_legt_keine_neue_sicherung_an(welt):
    schluessel_einrichten(welt, ablauf_in(30))
    vorher = dateien_liste(welt["w"])
    p = einr(welt, "schluessel", "--hostfingerabdruck", welt["fp"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert ohne_spur(dateien_liste(welt["w"])) == ohne_spur(vorher)
    assert not (welt["w"] / "var" / "backups").exists(), "ohne Änderung keine Sicherung"
    assert p.stdout.count("unverändert") >= 3


@braucht_bash
def test_ein_neuer_ablauf_verlaengert_nur_die_zeile_ablauf_und_sichert_die_alte_konfiguration(welt):
    schluessel_einrichten(welt, ablauf_in(30))
    neu = ablauf_in(180)
    p = einr(welt, "schluessel", "--ablauf", neu, "--hostfingerabdruck", welt["fp"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"ABLAUF={neu}" in (welt["w"] / "etc/wamo/kernkanal.conf").read_text()
    sicherungen = list((welt["w"] / "var/backups/wamo-job-kern").glob("*/kernkanal.conf"))
    assert len(sicherungen) == 1 and f"ABLAUF={ablauf_in(30)}" in sicherungen[0].read_text()


@braucht_bash
def test_ein_falscher_hostfingerabdruck_stoppt_mit_exit_3_vor_jeder_aenderung(welt):
    vorher = dateien_liste(welt["w"])
    p = einr(welt, "schluessel", "--ablauf", ablauf_in(30), "--hostfingerabdruck", FP_FORM)
    assert p.returncode == 3 and "Hostschlüssel" in p.stderr, p.stdout + p.stderr
    assert dateien_liste(welt["w"]) == vorher


@braucht_bash
@pytest.mark.parametrize("fall, erwartet", [
    ("dispatcher_alt", "älter als Version 7"), ("modul_fehlt", "wamo_job_kernkanal.py fehlt"), ("sshd_block_fehlt", "sshd-Block"),
    ("auftrag_laeuft", "Ruhe fehlt"), ("totmann_offen", "Totmannschalter"), ("openssh_alt", "zu alt"),
    ("fail2ban_ohne_ausnahme", "fail2ban"), ("hostschluessel_fehlt", "Hostschlüssel"),
])
def test_jede_vorbedingung_von_schluessel_endet_mit_exit_3_und_nichts_ist_geschrieben(welt, fall, erwartet):
    w, a = welt["w"], welt["a"]
    if fall == "dispatcher_alt":
        (w / "opt/wamo/bin/wamo-job-dispatch").write_text('VERSION = "6"\n')
    elif fall == "modul_fehlt":
        (w / "opt/wamo/waechter/wamo_job_kernkanal.py").unlink()
    elif fall == "sshd_block_fehlt":
        (w / "etc/ssh/sshd_config.d/wamo.conf").write_text("Match User wamo\n    ForceCommand x\nMatch all\n")
    elif fall == "auftrag_laeuft":
        kern_auftrag(welt, JOB_A, laeuft=True)
    elif fall == "totmann_offen":
        (a / "timers.txt").write_text("Wed 12:05 4min left wamo-sshd-rueckfall-1762859000.timer wamo-sshd-rueckfall-1762859000.service\n")
    elif fall == "openssh_alt":
        (a / "ssh").write_text('#!/usr/bin/env bash\n[ "$1" = -V ] && echo "OpenSSH_7.4p1, OpenSSL 1.0.2" >&2\nexit 0\n')
    elif fall == "fail2ban_ohne_ausnahme":
        (a / "fail2ban_aktiv").write_text("")
        (a / "ignoreip.txt").write_text("These IP addresses/networks are ignored:\n`- 10.0.0.0/8\n")
    elif fall == "hostschluessel_fehlt":
        (w / "etc/ssh/ssh_host_ed25519_key.pub").unlink()
    vorher = dateien_liste(w)
    p = einr(welt, "schluessel", "--ablauf", ablauf_in(30), "--hostfingerabdruck", welt["fp"])
    assert p.returncode == 3 and erwartet in p.stderr, (p.returncode, p.stdout, p.stderr)
    assert dateien_liste(w) == vorher and not (w / "var/lib").exists()


@braucht_bash
def test_fail2ban_mit_ausnahme_fuer_die_eigene_adresse_und_loopback_ist_in_ordnung(welt):
    a = welt["a"]
    (a / "fail2ban_aktiv").write_text("")
    (a / "ignoreip.txt").write_text("These IP addresses/networks are ignored:\n|- 127.0.0.0/8\n`- 192.168.178.0/24\n")
    p = einr(welt, "schluessel", "--trocken")
    assert p.returncode == 0 and "fail2ban nimmt" in p.stdout, p.stdout + p.stderr
    (a / "ignoreip.txt").write_text("")
    (a / "ignoreself.txt").write_text("true\n")
    assert einr(welt, "schluessel", "--trocken").returncode == 0


# ---------------------------------------------------------------- kanal
def konfig_von(welt) -> dict[str, str]:
    zeilen = (welt["w"] / "etc/wamo/kernkanal.conf").read_text(encoding="utf-8").splitlines()
    return dict(z.split("=", 1) for z in zeilen if "=" in z and not z.startswith("#"))


@braucht_bash
def test_kanal_oeffnet_mit_den_angegebenen_regeln_und_laesst_die_uebrigen_werte(welt):
    schluessel_einrichten(welt)
    p = einr(welt, "kanal", "--arten", "recherche", "--max-turns", "20", "--max-budget-usd", "1.5")
    assert p.returncode == 0, p.stdout + p.stderr
    k = konfig_von(welt)
    assert (k["KANAL"], k["ARTEN"], k["REPOS"], k["MAX_TURNS"], k["MAX_BUDGET_USD"], k["MAX_START_STUNDE"]) == ("offen", "recherche", "", "20", "1.5", "4")
    assert (welt["w"] / "var/log/wamo-job-kern/aktionen.jsonl").read_text().count('"aktion":"kanal","ergebnis":"ok"') == 1
    p = einr(welt, "kanal", "--arten", "recherche", "--max-turns", "20", "--max-budget-usd", "1.5")
    assert p.returncode == 0 and "unverändert" in p.stdout  # gleicher Stand: nichts schreiben


@braucht_bash
def test_kanal_fuer_github_arten_braucht_repos_und_die_flags_von_claude_code(welt):
    schluessel_einrichten(welt)
    assert einr(welt, "kanal", "--arten", "recherche,review").returncode == 2  # GitHub-Art ohne --repos
    (welt["a"] / "claude_help.txt").write_text("Optionen: --max-turns\n")  # --max-budget-usd fehlt
    p = einr(welt, "kanal", "--arten", "review", "--repos", "Acme/Test-Repo")
    assert p.returncode == 3 and "--max-budget-usd" in p.stderr and konfig_von(welt)["KANAL"] == "zu"
    assert einr(welt, "kanal", "--arten", "recherche").returncode == 0  # ohne GitHub-Art ist das Flag keine Voraussetzung
    (welt["a"] / "claude_help.txt").write_text("--max-turns --max-budget-usd\n")
    p = einr(welt, "kanal", "--arten", "review,recherche", "--repos", "Acme/Test-Repo")
    assert p.returncode == 0, p.stdout + p.stderr
    k = konfig_von(welt)
    assert (k["KANAL"], k["ARTEN"], k["REPOS"]) == ("offen", "review,recherche", "acme/test-repo")


@braucht_bash
def test_kanal_lehnt_unbekannte_arten_ab_und_aendert_nur_ohne_laufenden_kern_auftrag_zu_aber_immer(welt):
    schluessel_einrichten(welt)
    assert einr(welt, "kanal", "--arten", "gibtsnicht").returncode == 2
    kern_auftrag(welt, JOB_A, laeuft=True)
    p = einr(welt, "kanal", "--arten", "recherche")
    assert p.returncode == 3 and "Ruhe fehlt" in p.stderr and konfig_von(welt)["KANAL"] == "zu"
    kern_auftrag(welt, JOB_A, laeuft=False)
    assert einr(welt, "kanal", "--arten", "recherche").returncode == 0
    kern_auftrag(welt, JOB_A, laeuft=True)
    p = einr(welt, "kanal", "zu")  # kanal zu geht immer, auch mit laufendem Auftrag
    assert p.returncode == 0 and konfig_von(welt)["KANAL"] == "zu", p.stdout + p.stderr


@braucht_bash
def test_kanal_braucht_schluessel_gueltigen_ablauf_und_keine_sperrdatei(welt):
    assert einr(welt, "kanal", "--arten", "recherche").returncode == 3  # noch nichts eingerichtet
    assert einr(welt, "kanal", "zu").returncode == 3  # keine kernkanal.conf
    schluessel_einrichten(welt)
    (welt["w"] / "etc/wamo/kernkanal.notaus").write_text("x")
    p = einr(welt, "kanal", "--arten", "recherche")
    assert p.returncode == 3 and "notaus-aufheben" in p.stderr
    (welt["w"] / "etc/wamo/kernkanal.notaus").unlink()
    k = konfig_von(welt)
    text = (welt["w"] / "etc/wamo/kernkanal.conf").read_text().replace(f"ABLAUF={k['ABLAUF']}", "ABLAUF=20200101")
    (welt["w"] / "etc/wamo/kernkanal.conf").write_text(text)
    p = einr(welt, "kanal", "--arten", "recherche")
    assert p.returncode == 3 and "abgelaufen" in p.stderr
    (welt["w"] / "etc/wamo/kernkanal.conf").write_text(text + "UNBEKANNT=1\n")
    assert einr(welt, "kanal", "zu").returncode == 3  # unbekannte Schlüssel: nichts darf verloren gehen


# ---------------------------------------------------------------- schalter
DROPIN_REL = "etc/systemd/system/wamo-core@.service.d/wamo-entwicklung-buero.conf"


def aufrufe(welt) -> list[str]:
    pfad = welt["a"] / "aufrufe.log"
    return pfad.read_text().splitlines() if pfad.exists() else []


@braucht_bash
def test_schalter_an_schreibt_das_dropin_laedt_neu_und_schaltet_dann_um(welt):
    schluessel_einrichten(welt)
    p = einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL, "--kosten-auftrag", "3", "--kosten-tag", "10")
    assert p.returncode == 0, p.stdout + p.stderr
    text = (welt["w"] / DROPIN_REL).read_text(encoding="utf-8")
    zeilen = text.splitlines()
    assert zeilen[1:] == ["[Service]", "Environment=WAMO_ENTWICKLUNG_BUERO=an", "Environment=WAMO_ENTWICKLUNG_ORT=sokrates2",
                          f"Environment=WAMO_ENTWICKLUNG_PROFILE={PROFIL}", "Environment=WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD=3",
                          "Environment=WAMO_ENTWICKLUNG_KOSTEN_TAG_USD=10"]
    assert not text.startswith("# gespiegelt")  # keine Spiegel-Marke
    log = [z for z in aufrufe(welt) if z.startswith(("systemctl daemon-reload", "slots umschalten-kern"))]
    assert log == ["systemctl daemon-reload", f"slots umschalten-kern {RELEASE} dropin=ja"]  # erst neu laden, dann umschalten, Drop-in lag schon
    assert not re.search(r"systemctl (restart|start|stop)", "\n".join(aufrufe(welt)))
    assert (welt["w"] / "var/log/wamo-job-kern/aktionen.jsonl").read_text().count('"aktion":"schalter","ergebnis":"ok"') == 1


@braucht_bash
def test_schalter_mit_mehreren_profilen_und_den_standardkosten(welt):
    schluessel_einrichten(welt)
    zweites = str(uuid.UUID(int=12345))
    assert einr(welt, "schalter", "an", RELEASE, "--profil", f"{PROFIL},{zweites}").returncode == 0
    text = (welt["w"] / DROPIN_REL).read_text()
    assert f"WAMO_ENTWICKLUNG_PROFILE={PROFIL},{zweites}" in text
    assert "KOSTEN_AUFTRAG_USD=3" in text and "KOSTEN_TAG_USD=10" in text  # Standard nach E2


@braucht_bash
def test_ein_zweiter_aufruf_mit_gleichem_stand_ist_ein_noop(welt):
    schluessel_einrichten(welt)
    assert einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL).returncode == 0
    anzahl = len(aufrufe(welt))
    vorher = dateien_liste(welt["w"])
    p = einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL)
    assert p.returncode == 0 and "nichts zu tun" in p.stdout, p.stdout + p.stderr
    assert [z for z in aufrufe(welt)[anzahl:] if "umschalten-kern" in z or "daemon-reload" in z] == []
    assert ohne_spur(dateien_liste(welt["w"])) == ohne_spur(vorher)


@braucht_bash
def test_scheitert_umschalten_kern_vor_dem_umschalten_kommt_das_vorige_dropin_zurueck(welt):
    schluessel_einrichten(welt)
    (welt["a"] / "slots_modus.txt").write_text("fehl_vorher")
    p = einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL)
    assert p.returncode == 1, p.stdout + p.stderr
    assert not (welt["w"] / DROPIN_REL).exists()  # gab es keins, bleibt keins
    (welt["a"] / "slots_modus.txt").write_text("ok")
    assert einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL).returncode == 0
    vorher = (welt["w"] / DROPIN_REL).read_text()
    (welt["a"] / "slots_status.txt").write_text("Slot a: /opt/wamo-core/releases/v0.0.9  active\nSlot b: /opt/wamo-core/releases/v0.0.9  inactive\n")
    (welt["a"] / "slots_modus.txt").write_text("fehl_vorher")
    p = einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL, "--kosten-tag", "20")
    assert p.returncode == 1 and (welt["w"] / DROPIN_REL).read_text() == vorher  # das vorige Drop-in ist wieder da
    assert aufrufe(welt).count("systemctl daemon-reload") >= 3  # auch nach der Rücknahme neu geladen


@braucht_bash
def test_scheitert_umschalten_kern_nach_dem_umschalten_bleibt_das_dropin_und_exit_4(welt):
    schluessel_einrichten(welt)
    (welt["a"] / "slots_modus.txt").write_text("fehl_nachher")
    p = einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL)
    assert p.returncode == 4 and "teilweise" in p.stderr, p.stdout + p.stderr
    assert (welt["w"] / DROPIN_REL).is_file()  # bleibt: der neue Slot läuft schon damit
    assert '"ergebnis":"fehler"' in (welt["w"] / "var/log/wamo-job-kern/aktionen.jsonl").read_text()


@braucht_bash
def test_schalter_aus_entfernt_das_dropin_und_schaltet_um(welt):
    schluessel_einrichten(welt)
    assert einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL).returncode == 0
    (welt["a"] / "slots_status.txt").write_text("Slot a: /opt/wamo-core/releases/v0.0.9  active\nSlot b: /opt/wamo-core/releases/v0.0.9  inactive\n")
    p = einr(welt, "schalter", "aus", "v0.1.1-def5678")
    assert p.returncode == 0, p.stdout + p.stderr
    assert not (welt["w"] / DROPIN_REL).exists()
    assert [z for z in aufrufe(welt) if z.startswith("slots umschalten-kern")][-1] == "slots umschalten-kern v0.1.1-def5678 dropin=nein"
    assert list((welt["w"] / "var/backups/wamo-job-kern").glob("*/wamo-entwicklung-buero.conf"))  # das alte Drop-in ist gesichert


@braucht_bash
@pytest.mark.parametrize("fall, erwartet", [
    ("schluessel_fehlt", "schluessel"), ("release_ohne_schranken", "Büro-Schranken"), ("release_fehlt", "Büro-Schranken"),
    ("core_env_hat_entwicklung", "core.env"), ("alte_unit_hat_dropin", "Spiegeln"), ("auftrag_laeuft", "Ruhe fehlt"), ("rauchtest_fehlt", "Rauchtest"),
])
def test_jede_vorabpruefung_von_schalter_an_endet_mit_exit_3_ohne_etwas_zu_aendern(welt, fall, erwartet):
    schluessel_einrichten(welt)
    w = welt["w"]
    if fall == "schluessel_fehlt":
        (w / "etc/ssh/authorized_keys.d/wamo-kern").unlink()
    elif fall == "release_ohne_schranken":
        (w / f"opt/wamo-core/releases/{RELEASE}/voice-core/entwicklung_betrieb.py").write_text("SCHRANKEN_VERSION = 0\n")
    elif fall == "release_fehlt":
        (w / f"opt/wamo-core/releases/{RELEASE}/voice-core/entwicklung_betrieb.py").unlink()
    elif fall == "core_env_hat_entwicklung":
        (welt["a"] / "core_env.txt").write_text("SONIOX_API_KEY: gesetzt\nWAMO_ENTWICKLUNG: gesetzt\n")
    elif fall == "alte_unit_hat_dropin":
        d = w / "etc/systemd/system/wamo-core.service.d"
        d.mkdir(parents=True)
        (d / "wamo-entwicklung-buero.conf").write_text("[Service]\n")
    elif fall == "auftrag_laeuft":
        kern_auftrag(welt, JOB_A, laeuft=True)
    elif fall == "rauchtest_fehlt":
        (w / "opt/wamo-core/bin/wamo-job-kern-rauchtest").unlink()
    vorher = dateien_liste(w)
    p = einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL)
    assert p.returncode == 3 and erwartet in p.stderr, (p.returncode, p.stdout, p.stderr)
    assert not (w / DROPIN_REL).exists() and not [z for z in aufrufe(welt) if "umschalten-kern" in z]
    assert ohne_spur(dateien_liste(w)) == ohne_spur(vorher)


# ---------------------------------------------------------------- status und notaus-aufheben
@braucht_bash
def test_status_zeigt_resttage_kanal_schalter_je_slot_und_jede_abweichung_ohne_werte(welt):
    schluessel_einrichten(welt, ablauf_in(30))
    assert einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL).returncode == 0
    a, w = welt["a"], welt["w"]
    (a / "aktiv.txt").write_text("wamo-core@a active\nwamo-core@b active\n")
    (a / "pid_a").write_text("4711\n")
    (a / "pid_b").write_text("4712\n")
    for pid, wert in (("4711", "an"), ("4712", "aus")):  # Slot b läuft noch ohne Schalter
        env = w / "proc" / pid / "environ"
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_bytes(b"PATH=/usr/bin\0WAMO_ENTWICKLUNG=aus\0WAMO_ENTWICKLUNG_BUERO=" + wert.encode() + b"\0WAMO_ENTWICKLUNG_PROFILE=" + PROFIL.encode() + b"\0")
    kern_auftrag(welt, JOB_A, laeuft=True)
    kern_auftrag(welt, JOB_B, kanal="pc", laeuft=True)  # ein PC-Auftrag zählt nicht
    (w / "srv/wamo/projekte/kern-test").mkdir()
    p = einr(welt, "status")
    assert p.returncode == 0, p.stdout + p.stderr
    o = p.stdout
    assert "noch 30 Tage" in o and "Kanal: zu" in o and "Notaus: nein" in o
    assert "Slot a: aktiv" in o and "WAMO_ENTWICKLUNG_BUERO=an" in o and "WAMO_ENTWICKLUNG_BUERO=aus" in o
    assert "WAMO_ENTWICKLUNG_PROFILE" in o  # nur der Name
    assert PROFIL not in o and PROFIL not in p.stderr  # nie ein Wert (user_id)
    assert f"Laufende Kern-Aufträge: {JOB_A}" in o and JOB_B not in o and "Kern-Ordner: 1" in o
    assert "Abweichung: Slot b hat WAMO_ENTWICKLUNG_BUERO=aus, das Drop-in sagt an" in o
    assert "Abweichungen: keine" not in o


@braucht_bash
def test_status_ohne_einrichtung_und_mit_notaus_nennt_die_lage(welt):
    p = einr(welt, "status")
    assert p.returncode == 0 and "Schlüsselzeile: keine" in p.stdout and "keine kernkanal.conf" in p.stdout and "Abweichungen: keine" in p.stdout
    schluessel_einrichten(welt)
    (welt["w"] / "etc/wamo/kernkanal.notaus").write_text("x")
    o = einr(welt, "status").stdout
    assert "Notaus: Sperrdatei besteht" in o and "Abweichung: die Sperrdatei" in o


@braucht_bash
def test_notaus_aufheben_entfernt_die_sperrdatei_und_oeffnet_nichts(welt):
    schluessel_einrichten(welt)
    sperre = welt["w"] / "etc/wamo/kernkanal.notaus"
    sperre.write_text("x")
    p = einr(welt, "notaus-aufheben")
    assert p.returncode == 0 and not sperre.exists() and "geschlossen" in p.stdout
    assert konfig_von(welt)["KANAL"] == "zu"
    assert einr(welt, "notaus-aufheben").returncode == 0  # ohne Datei: nichts zu tun
    assert (welt["w"] / "var/log/wamo-job-kern/aktionen.jsonl").read_text().count('"aktion":"notaus"') == 2


# ---------------------------------------------------------------- Rückweg: notaus und --entfernen
def dispatcher_7(welt) -> Path:
    """Stellt den Dispatcher-Platzhalter mit VERSION = "7" wieder her (fake_dispatcher überschreibt ihn)."""
    return welt["schreibe"]("opt/wamo/bin/wamo-job-dispatch", '#!/usr/bin/python3 -I\nVERSION = "7"\n')


def fake_dispatcher(welt, beendet: bool = True, scheitert: tuple[str, ...] = ()) -> Path:
    """Dispatcher-Ersatz: hält fest, was beim cancel schon gilt, und beendet den Auftrag (status.json) wie der echte nach cancel.
    Für die Aufträge in scheitert endet cancel mit Exit 1 (ohne den Auftrag zu beenden)."""
    ende = 'echo \'{"ergebnis": "abgebrochen"}\' > "$W/srv/wamo/jobs/$2/status.json"' if beendet else ":"
    fehl = "".join(f'[ "$2" = "{j}" ] && exit 1\n' for j in scheitert)
    return welt["schreibe"]("opt/wamo/bin/wamo-job-dispatch", f'''#!/usr/bin/env bash
W="{welt["shell"]}"
echo "cancel $2 notaus=$([ -e "$W/etc/wamo/kernkanal.notaus" ] && echo ja || echo nein) schluessel=$([ -e "$W/etc/ssh/authorized_keys.d/wamo-kern" ] && echo ja || echo nein) kanal=$(grep '^KANAL=' "$W/etc/wamo/kernkanal.conf" 2>/dev/null | cut -d= -f2) ssh=${{SSH_CONNECTION:-keine}}" >> "$W/attrappen/cancel.log"
{fehl}{ende}
''')


@braucht_bash
def test_notaus_haelt_die_reihenfolge_ein_sperrdatei_dann_schluessel_dann_cancel_dann_suche(welt):
    schluessel_einrichten(welt)
    assert einr(welt, "kanal", "--arten", "recherche").returncode == 0 and konfig_von(welt)["KANAL"] == "offen"
    kern_auftrag(welt, JOB_A, laeuft=True)
    kern_auftrag(welt, JOB_B, kanal="pc", laeuft=True)
    kern_auftrag(welt, "c" * 16, laeuft=False)  # ein beendeter Kern-Auftrag bekommt ebenfalls ein cancel (Schritt 3 nennt alle mit kanal=kern)
    fake_dispatcher(welt)
    p = rueck(welt, "notaus")
    assert p.returncode == 0, p.stdout + p.stderr
    o = p.stdout
    assert o.index("== 1. Sperrdatei") < o.index("== 2. Schlüsselzeile") < o.index("== 3. Kern-Aufträge") < o.index("== 4. Erneut suchen")
    cancels = (welt["a"] / "cancel.log").read_text().splitlines()
    assert sorted(z.split()[1] for z in cancels) == sorted([JOB_A, "c" * 16])  # nur Aufträge mit kanal=kern
    for z in cancels:  # beim cancel gilt die Sperrdatei schon, die Schlüsselzeile ist weg, der Kanal steht auf zu, und es gibt keine SSH-Variablen
        assert "notaus=ja" in z and "schluessel=nein" in z and "kanal=zu" in z and "ssh=keine" in z, z
    assert konfig_von(welt)["KANAL"] == "zu"  # der Notaus schließt den Kanal selbst: kein Aufheben öffnet ihn von allein
    assert (welt["w"] / "etc/wamo/kernkanal.notaus").is_file() and not (welt["w"] / "etc/ssh/authorized_keys.d/wamo-kern").exists()
    gesichert = list((welt["w"] / "var/backups/wamo-job-kern").glob("*/wamo-kern"))
    assert len(gesichert) == 1 and "wamo-kern-jobs" in gesichert[0].read_text()
    assert (welt["w"] / "etc/ssh/authorized_keys.d/wamo").is_file()  # die PC-Datei bleibt
    assert '"aktion":"notaus","ergebnis":"ok"' in (welt["w"] / "var/log/wamo-job-kern/aktionen.jsonl").read_text()
    assert einr(welt, "status").stdout.count("Notaus: Sperrdatei besteht") == 1
    assert rueck(welt, "notaus").returncode == 0  # wiederholbar


@braucht_bash
def test_notaus_trocken_aendert_nichts(welt):
    schluessel_einrichten(welt)
    kern_auftrag(welt, JOB_A, laeuft=True)
    fake_dispatcher(welt)
    vorher = dateien_liste(welt["w"])
    p = rueck(welt, "notaus", "--trocken")
    assert p.returncode == 0 and "Trockenlauf" in p.stdout and "[trocken]" in p.stdout, p.stdout + p.stderr
    assert dateien_liste(welt["w"]) == vorher and not (welt["a"] / "cancel.log").exists()


@braucht_bash
def test_notaus_meldet_einen_fehler_wenn_ein_kern_auftrag_nicht_endet(welt):
    schluessel_einrichten(welt)
    kern_auftrag(welt, JOB_A, laeuft=True)
    fake_dispatcher(welt, beendet=False)
    p = rueck(welt, "notaus", extra={"WAMO_JOB_KERN_SUCHFRIST_S": "2"})
    assert p.returncode == 1 and JOB_A in p.stderr, p.stdout + p.stderr
    assert (welt["w"] / "etc/wamo/kernkanal.notaus").is_file()  # die Sperre bleibt trotzdem
    assert '"aktion":"notaus","ergebnis":"fehler"' in (welt["w"] / "var/log/wamo-job-kern/aktionen.jsonl").read_text()


@braucht_bash
def test_entfernen_verweigert_bei_vorhandenem_dropin_und_bei_laufendem_auftrag(welt):
    schluessel_einrichten(welt)
    assert einr(welt, "schalter", "an", RELEASE, "--profil", PROFIL).returncode == 0
    vorher = dateien_liste(welt["w"])
    p = rueck(welt, "--entfernen")
    assert p.returncode == 3 and "schalter aus" in p.stderr, p.stdout + p.stderr
    assert ohne_spur(dateien_liste(welt["w"])) == ohne_spur(vorher)
    (welt["w"] / DROPIN_REL).unlink()
    kern_auftrag(welt, JOB_A, laeuft=True)
    p = rueck(welt, "--entfernen")
    assert p.returncode == 3 and "notaus" in p.stderr


@braucht_bash
def test_entfernen_raeumt_schluessel_known_hosts_konfiguration_und_sperrdatei_ab(welt):
    schluessel_einrichten(welt)
    w = welt["w"]
    (w / "etc/wamo/kernkanal.notaus").write_text("x")
    pc = hashlib.sha256((w / "etc/ssh/authorized_keys.d/wamo").read_bytes()).hexdigest()
    p = rueck(welt, "--entfernen", "--trocken")
    assert p.returncode == 0 and "[trocken]" in p.stdout and (w / "etc/wamo/kernkanal.conf").exists()
    p = rueck(welt, "--entfernen")
    assert p.returncode == 0, p.stdout + p.stderr
    for rel in ("etc/ssh/authorized_keys.d/wamo-kern", "var/lib/wamo-core/.ssh/known_hosts_wamo_jobs", "etc/wamo/kernkanal.conf",
                "etc/wamo/kernkanal.notaus", "var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs", "var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs.pub"):
        assert not (w / rel).exists(), rel
    assert hashlib.sha256((w / "etc/ssh/authorized_keys.d/wamo").read_bytes()).hexdigest() == pc
    gesichert = {p.name for p in (w / "var/backups/wamo-job-kern").glob("*/*")}
    assert {"wamo-kern", "known_hosts_wamo_jobs", "kernkanal.conf", "kernkanal.notaus"} <= gesichert
    assert "id_ed25519_wamo_jobs" not in gesichert and "id_ed25519_wamo_jobs.pub" not in gesichert  # der private Schlüssel wird nie gesichert
    assert einr(welt, "schluessel", "--ablauf", ablauf_in(30), "--hostfingerabdruck", welt["fp"]).returncode == 0  # neu einrichten geht


# ---------------------------------------------------------------- Eigenschaften der neuen Dateien
@pytest.mark.parametrize("skript", [KERN_EINRICHTEN, KERN_RUECKWEG])
def test_die_kern_skripte_haben_die_form_der_vorhandenen(skript):
    text = lies(skript)
    assert text.startswith("#!/usr/bin/env bash\n") and "set -euo pipefail" in text
    assert "\r" not in text and chr(0x2014) not in text and chr(0x2013) not in text
    assert not re.search(r"systemctl\s+(restart|start|stop)", text), "die Skripte starten, stoppen und starten nie einen Dienst neu"
    assert "logger -t wamo-job-kern" in text and "aktionen.jsonl" in text
    assert "WAMO_JOB_KERN_WURZEL" in text and "läuft nicht als root" in text
    assert not re.search(r"sk-ant-|ghp_|-----BEGIN", text)


@braucht_bash
@pytest.mark.parametrize("skript", [KERN_EINRICHTEN, KERN_RUECKWEG])
def test_bash_syntax_der_kern_skripte(skript):
    p = subprocess.run([BASH, "-n", str(skript)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_das_einrichtungsskript_schreibt_nie_in_die_pc_datei_und_hat_die_standardwerte_aus_i2():
    text = lies(KERN_EINRICHTEN)
    for zeile in text.splitlines():
        if "$PC_DATEI" in zeile:
            assert re.search(r'(\[ -f "\$PC_DATEI" \]|\$\{PC_DATEI#)', zeile) or zeile.startswith("PC_DATEI="), zeile
    std = dict(re.findall(r"^STD_([A-Z_]+)=(\S+)$", text, re.M))
    assert std == {"KANAL": "zu", "ABLAUF_TAGE": "30", "MAX_TURNS": "40", "MAX_BUDGET_USD": "2.00", "MAX_START_STUNDE": "4", "MAX_START_TAG": "8",
                   "MAX_LAUFZEIT_TAG_S": "14400", "MAX_FOLGEN": "5", "MIN_ABSTAND_S": "60", "MIN_PLATZ_MB": "2048",
                   "MAX_VERSUCH_STUNDE": "20", "KOSTEN_AUFTRAG_USD": "3", "KOSTEN_TAG_USD": "10"}
    schluessel = re.search(r"^KONFIG_SCHLUESSEL=\((.*?)\)$", text, re.M | re.S).group(1).split()
    assert schluessel == ["KANAL", "ABLAUF", "ARTEN", "REPOS", "DIENSTE", "MAX_TURNS", "MAX_BUDGET_USD", "MAX_START_STUNDE", "MAX_START_TAG",
                          "MAX_LAUFZEIT_TAG_S", "MAX_FOLGEN", "MIN_ABSTAND_S", "MIN_PLATZ_MB", "MAX_VERSUCH_STUNDE"]  # genau die Schlüssel, die der Dispatcher kennt


def test_die_exit_codes_stehen_im_kopf_der_skripte():
    for skript in (KERN_EINRICHTEN, KERN_RUECKWEG):
        text = lies(skript)
        kopf = text[:text.index("set -euo pipefail")]
        assert "Exit-Codes" in kopf and "3 Vorbedingung nicht erfüllt" in kopf
    assert "4 teilweise umgesetzt" in lies(KERN_EINRICHTEN)[:3000]


# ---------------------------------------------------------------- R12: README
def test_die_readme_beschreibt_den_kern_kanal_vollstaendig_und_ohne_gedankenstriche_und_geheimnisse():
    text = (INFRA / "README.md").read_text(encoding="utf-8")
    assert "## 18. Kern-Kanal der Entwicklungsaufträge" in text
    abschnitt = text[text.index("## 18. Kern-Kanal der Entwicklungsaufträge"):]
    for datei in ("wamo-job-kern-einrichten.sh", "wamo-job-kern-rueckweg.sh", "wamo_job_kern_rauchtest.py", "wamo_job_kernkanal.py", "kernkanal.conf",
                  "kernkanal.notaus", "kernkanal.jsonl", "wamo-entwicklung-buero.conf", "authorized_keys.d/wamo-kern", "wamo-kern-jobs", "aktionen.jsonl",
                  "/var/backups/wamo-job-kern", "wamo-job-rueckweg.sh", "umschalten-kern", "Notaus", "Ablauf", "Widerruf", "Widerrufsstelle", "ExposeAuthInfo",
                  "WAMO_ENTWICKLUNG_BUERO", "herkunft_unklar", "nur am Server prüfbar", "Exit 4" if "Exit 4" in abschnitt else "4 teilweise umgesetzt"):
        assert datei in abschnitt, datei
    for code in ("notaus", "konfig_ungueltig", "kanal_zu", "abgelaufen", "befehl", "fremd", "art", "repo", "repo_fehlt", "dienst", "projekt", "zugang", "fork",
                 "herkunft_unklar", "grenze_start", "grenze_laufzeit", "grenze_folgen", "abstand", "platz"):
        assert code in abschnitt, code  # die feste Liste der Gründe steht komplett da
    for rel in ("wamo-job-kern-einrichten.sh", "wamo-job-kern-rueckweg.sh", "wamo_job_kern_rauchtest.py", "wamo_job_kernkanal.py"):
        assert (INFRA / rel).is_file(), rel  # jede genannte Datei gibt es
    for stelle in (text[text.index("| SSH | `/etc/ssh/authorized_keys.d/wamo`"):][:900], abschnitt):
        assert chr(0x2014) not in stelle and chr(0x2013) not in stelle
    assert not re.search(r"sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN", abschnitt)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", abschnitt)  # keine user_id
    assert "ssh wamo-sokrates2" in abschnitt
    for zeile in text.splitlines():  # die Vorprüfung bleibt unverändert: die README sagt es
        if zeile.startswith("| Vorprüfung |"):
            assert "bleibt unverändert" in zeile


# ---------------------------------------------------------------- Nachbesserung nach der Sicherheitsprüfung
WURZEL_REF = re.compile(r'\$\{(?:SSH_ORDNER|KEY|KNOWN)#"\$WURZEL"\}')
BAUM_REF = re.compile(r'\$\{?(?:SSH_ORDNER|KEY|KNOWN)\b')
ROOT_BEFEHLE = re.compile(r"\b(chmod|chown|mktemp|rm|cp|mv|tee|cat|install|mkdir|ssh-keygen|touch|ln|sed|read)\b")


@pytest.mark.parametrize("skript", [KERN_EINRICHTEN, KERN_RUECKWEG])
def test_root_fasst_den_baum_von_wamo_core_nie_an_alles_laeuft_ueber_als_kern(skript):
    """Der Baum /var/lib/wamo-core/.ssh gehört dem Kern: ein gekaperter Kern könnte ihn durch einen Link ersetzen oder die Temp-Datei
    vorwegnehmen. Jede Dateiarbeit dort läuft als wamo-core (als_kern), root schreibt nur nach /etc, /var/backups und /var/log."""
    for nr, zeile in enumerate(lies(skript).splitlines(), 1):
        sauber = zeile.strip()
        if sauber.startswith("#") or re.match(r"^(SSH_ORDNER|KEY|KNOWN)=", sauber):
            continue
        ohne_meldung = WURZEL_REF.sub("", zeile)
        if BAUM_REF.search(ohne_meldung) and ROOT_BEFEHLE.search(ohne_meldung):
            assert "als_kern" in zeile, f"{skript.name} Zeile {nr}: {sauber}"


@braucht_bash
def test_schluessel_arbeitet_im_baum_von_wamo_core_als_wamo_core(welt):
    schluessel_einrichten(welt, echt=True)
    log = [z for z in aufrufe(welt) if z.startswith("runuser ")]
    assert all(z.startswith("runuser -u wamo-core -- ") for z in log)
    gesamt = "\n".join(log)
    assert "ssh-keygen -q -t ed25519" in gesamt and "id_ed25519_wamo_jobs" in gesamt  # das Schlüsselpaar entsteht als wamo-core
    assert "known_hosts_wamo_jobs" in gesamt  # die Datei known_hosts wird als wamo-core geschrieben
    assert "id_ed25519_wamo_jobs.pub" in gesamt  # der öffentliche Teil wird als wamo-core gelesen (und danach von root geprüft)
    assert not any(re.search(r"\b(chmod|chown)\b", z) for z in log if "ssh" not in z.split("--", 1)[1])  # kein root-Eingriff in den Baum


@braucht_bash
def test_ein_verfaelschter_oeffentlicher_schluessel_wird_von_root_abgelehnt(welt):
    schluessel_einrichten(welt)
    pub = welt["w"] / "var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs.pub"
    zeile_vorher = (welt["w"] / "etc/ssh/authorized_keys.d/wamo-kern").read_text()
    for bose in ('ssh-ed25519 AAAA";touch /tmp/x;" kommentar\n', 'ssh-rsa AAAAB3NzaC1yc2E x\n', 'ssh-ed25519 AAAA ssh-ed25519 BBBB\nrestrict\n',
                 'command="x" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA x\n'):
        pub.write_text(bose)
        (welt["w"] / "etc/ssh/authorized_keys.d/wamo-kern").unlink(missing_ok=True)
        p = einr(welt, "schluessel", "--hostfingerabdruck", welt["fp"])
        assert p.returncode == 1 and not (welt["w"] / "etc/ssh/authorized_keys.d/wamo-kern").exists(), (bose, p.stdout, p.stderr)
    assert zeile_vorher.startswith('restrict,from="192.168.178.136"')


@braucht_bash
def test_ein_link_an_stelle_des_ssh_ordners_wird_von_beiden_skripten_verweigert_ohne_ihm_zu_folgen(welt):
    if os.name == "nt":
        pytest.skip("symbolische Links brauchen unter Windows besondere Rechte")
    w = welt["w"]
    ziel = w / "etc" / "fremd"
    ziel.mkdir(parents=True)
    (ziel / "wichtig").write_text("nicht anfassen")
    (w / "var/lib/wamo-core").mkdir(parents=True)
    (w / "var/lib/wamo-core/.ssh").symlink_to(ziel)
    vorher = dateien_liste(w)
    modus_vorher = oct(_stat.S_IMODE(ziel.stat().st_mode))
    p = einr(welt, "schluessel", "--ablauf", ablauf_in(30), "--hostfingerabdruck", welt["fp"])
    assert p.returncode == 3 and "Link" in p.stderr, p.stdout + p.stderr
    p = rueck(welt, "--entfernen")
    assert p.returncode == 3 and "Link" in p.stderr, p.stdout + p.stderr
    assert dateien_liste(w) == vorher and oct(_stat.S_IMODE(ziel.stat().st_mode)) == modus_vorher  # nichts im Ziel des Links geschrieben, gelöscht oder umgestellt
    assert (ziel / "wichtig").read_text() == "nicht anfassen"
    assert not [z for z in aufrufe(welt) if z.startswith("runuser ")]  # nicht einmal ein Aufruf als wamo-core


@braucht_bash
def test_der_notaus_wirkt_auch_bei_einem_link_und_rennt_nicht_in_den_ersetzten_ordner(welt):
    """Der Notaus darf nie an einem Link scheitern: Sperrdatei, Schlüsselzeile und Kanal liegen in /etc. Nur das Beiseitelegen des Paares unterbleibt."""
    if os.name == "nt":
        pytest.skip("symbolische Links brauchen unter Windows besondere Rechte")
    w = welt["w"]
    schluessel_einrichten(welt)
    assert einr(welt, "kanal", "--arten", "recherche").returncode == 0
    ssh = w / "var/lib/wamo-core/.ssh"
    ziel = w / "etc" / "fremd"
    ziel.mkdir(parents=True)
    (ziel / "wichtig").write_text("nicht anfassen")
    for datei in ssh.iterdir():
        datei.rename(ziel / datei.name)
    ssh.rmdir()
    ssh.symlink_to(ziel)
    inhalt_vorher = sorted(p.name for p in ziel.iterdir())
    fake_dispatcher(welt)
    aufrufe_vorher = len([z for z in aufrufe(welt) if z.startswith("runuser ")])
    p = rueck(welt, "notaus")
    assert p.returncode == 0 and "Link" in p.stderr, p.stdout + p.stderr
    assert (w / "etc/wamo/kernkanal.notaus").is_file() and not (w / "etc/ssh/authorized_keys.d/wamo-kern").exists() and konfig_von(welt)["KANAL"] == "zu"
    assert sorted(p.name for p in ziel.iterdir()) == inhalt_vorher and (ziel / "wichtig").read_text() == "nicht anfassen"  # im Ziel des Links ist nichts geschehen
    assert len([z for z in aufrufe(welt) if z.startswith("runuser ")]) == aufrufe_vorher  # nicht ein Aufruf als wamo-core in den Link hinein
    dispatcher_7(welt)
    assert einr(welt, "notaus-aufheben").returncode == 0
    p = einr(welt, "schluessel", "--hostfingerabdruck", welt["fp"])
    assert p.returncode == 3 and "Link" in p.stderr, p.stdout + p.stderr  # schluessel verweigert, solange der Baum ein Link ist


@braucht_bash
def test_ein_link_an_stelle_des_schluessels_oder_der_known_hosts_wird_nicht_beschrieben(welt):
    if os.name == "nt":
        pytest.skip("symbolische Links brauchen unter Windows besondere Rechte")
    schluessel_einrichten(welt)
    w = welt["w"]
    ziel = w / "etc" / "fremd-datei"
    ziel.write_text("unverändert")
    known = w / "var/lib/wamo-core/.ssh/known_hosts_wamo_jobs"
    known.unlink()
    known.symlink_to(ziel)
    p = einr(welt, "schluessel", "--hostfingerabdruck", welt["fp"])
    assert p.returncode == 3 and "Link" in p.stderr, p.stdout + p.stderr
    assert ziel.read_text() == "unverändert"


@braucht_bash
def test_entfernen_loescht_im_baum_von_wamo_core_als_wamo_core(welt):
    schluessel_einrichten(welt)
    assert rueck(welt, "--entfernen").returncode == 0
    log = [z for z in aufrufe(welt) if z.startswith("runuser ")]
    assert any(" rm " in z and "id_ed25519_wamo_jobs" in z for z in log)
    assert any(" rm " in z and "known_hosts_wamo_jobs" in z for z in log)


# ---- MEDIUM 2: der Notaus beendet laufende Aufträge zuerst und bricht nie vor den späteren Schritten ab
@braucht_bash
def test_notaus_beendet_laufende_auftraege_zuerst_und_ein_fehlgeschlagenes_cancel_bricht_nichts_ab(welt):
    schluessel_einrichten(welt)
    kern_auftrag(welt, "0" * 16, laeuft=False)  # sortiert vor dem laufenden, ist aber beendet
    kern_auftrag(welt, JOB_A, laeuft=True)
    kern_auftrag(welt, "1" * 16, laeuft=False)
    fake_dispatcher(welt, scheitert=("0" * 16,))
    p = rueck(welt, "notaus")
    assert p.returncode == 0, p.stdout + p.stderr  # nichts läuft mehr: der Notaus ist erreicht
    cancels = [z.split()[1] for z in (welt["a"] / "cancel.log").read_text().splitlines()]
    assert cancels[0] == JOB_A and set(cancels) == {JOB_A, "0" * 16, "1" * 16}  # der laufende zuerst, dann die übrigen
    o = p.stdout + p.stderr
    assert "== 4. Erneut suchen" in p.stdout and "kein Kern-Auftrag läuft mehr" in p.stdout
    assert "1 cancel" in o or "fehlgeschlagen" in o  # der Fehler wird gemeldet, nicht verschluckt
    assert '"aktion":"notaus","ergebnis":"ok"' in (welt["w"] / "var/log/wamo-job-kern/aktionen.jsonl").read_text()


@braucht_bash
def test_scheitert_das_cancel_eines_laufenden_auftrags_laeuft_die_suche_weiter_und_endet_mit_exit_1(welt):
    schluessel_einrichten(welt)
    kern_auftrag(welt, JOB_A, laeuft=True)
    kern_auftrag(welt, JOB_B, laeuft=False)
    fake_dispatcher(welt, beendet=False, scheitert=(JOB_A,))
    p = rueck(welt, "notaus", extra={"WAMO_JOB_KERN_SUCHFRIST_S": "2"})
    assert p.returncode == 1 and JOB_A in p.stderr, p.stdout + p.stderr
    assert "== 4. Erneut suchen" in p.stdout  # Schritt 4 wurde erreicht, obwohl cancel scheiterte
    assert (welt["w"] / "etc/wamo/kernkanal.notaus").is_file()


# ---- MEDIUM 4: der Notaus schließt den Kanal und legt das Schlüsselpaar beiseite
@braucht_bash
def test_notaus_setzt_den_kanal_auf_zu_und_legt_das_schluesselpaar_beiseite(welt):
    schluessel_einrichten(welt)
    assert einr(welt, "kanal", "--arten", "recherche").returncode == 0
    ssh = welt["w"] / "var/lib/wamo-core/.ssh"
    alt_pub = (ssh / "id_ed25519_wamo_jobs.pub").read_text()
    fake_dispatcher(welt)
    p = rueck(welt, "notaus")
    assert p.returncode == 0, p.stdout + p.stderr
    assert konfig_von(welt)["KANAL"] == "zu"
    assert list((welt["w"] / "var/backups/wamo-job-kern").glob("*/kernkanal.conf"))  # die offene Fassung ist gesichert
    assert not (ssh / "id_ed25519_wamo_jobs").exists() and not (ssh / "id_ed25519_wamo_jobs.pub").exists()
    beiseite = sorted(p.name for p in ssh.glob("id_ed25519_wamo_jobs*.notaus-*"))
    assert len(beiseite) == 2 and any(".pub.notaus-" in n for n in beiseite), beiseite
    assert re.fullmatch(r"id_ed25519_wamo_jobs(\.pub)?\.notaus-\d{8}-\d{6}", beiseite[0]), beiseite  # mit Zeitstempel
    assert [z for z in aufrufe(welt) if z.startswith("runuser ") and " mv " in z]  # das Beiseitelegen läuft als wamo-core
    assert rueck(welt, "notaus").returncode == 0  # wiederholbar, auch ohne Schlüsselpaar
    assert alt_pub in (ssh / beiseite[1] if beiseite[1].endswith(tuple("0123456789")) and ".pub." in beiseite[1] else ssh / beiseite[0]).read_text()


@braucht_bash
def test_nach_notaus_aufheben_geht_der_kanal_nur_mit_einem_neuen_schluessel_und_einem_ausdruecklichen_kanal(welt):
    schluessel_einrichten(welt)
    assert einr(welt, "kanal", "--arten", "recherche").returncode == 0
    ssh = welt["w"] / "var/lib/wamo-core/.ssh"
    alt_pub = (ssh / "id_ed25519_wamo_jobs.pub").read_text().split()[1]
    fake_dispatcher(welt)
    assert rueck(welt, "notaus").returncode == 0
    dispatcher_7(welt)  # der Ersatz-Dispatcher stand nur für das cancel da
    p = einr(welt, "schluessel", "--hostfingerabdruck", welt["fp"])
    assert p.returncode == 3 and "notaus-aufheben" in p.stderr, p.stdout + p.stderr  # solange die Sperrdatei besteht, setzt schluessel nichts
    assert not (welt["w"] / "etc/ssh/authorized_keys.d/wamo-kern").exists()
    assert einr(welt, "notaus-aufheben").returncode == 0
    assert konfig_von(welt)["KANAL"] == "zu" and not (welt["w"] / "etc/ssh/authorized_keys.d/wamo-kern").exists()  # das Aufheben öffnet nichts
    schluessel_einrichten(welt)
    neu_pub = (ssh / "id_ed25519_wamo_jobs.pub").read_text().split()[1]
    assert neu_pub != alt_pub  # ein neues Paar, nie der womöglich kompromittierte Schlüssel
    assert neu_pub in (welt["w"] / "etc/ssh/authorized_keys.d/wamo-kern").read_text() and alt_pub not in (welt["w"] / "etc/ssh/authorized_keys.d/wamo-kern").read_text()
    assert konfig_von(welt)["KANAL"] == "zu"  # auch schluessel öffnet nicht
    assert einr(welt, "kanal", "--arten", "recherche").returncode == 0 and konfig_von(welt)["KANAL"] == "offen"  # erst kanal öffnet


@braucht_bash
def test_entfernen_raeumt_auch_die_beiseitegelegten_paare_ab(welt):
    schluessel_einrichten(welt)
    fake_dispatcher(welt)
    assert rueck(welt, "notaus").returncode == 0
    assert einr(welt, "notaus-aufheben").returncode == 0
    assert rueck(welt, "--entfernen").returncode == 0
    assert not list((welt["w"] / "var/lib/wamo-core/.ssh").glob("id_ed25519_wamo_jobs*"))


@braucht_bash
def test_notaus_ohne_konfiguration_und_ohne_schluessel_laeuft_trotzdem_durch(welt):
    fake_dispatcher(welt)
    p = rueck(welt, "notaus")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (welt["w"] / "etc/wamo/kernkanal.notaus").is_file()

# ---- LOW d: ganze Zeichenkette prüfen, nicht nur die erste Zeile. Ein Argument mit Zeilenumbruch lässt sich unter Windows nicht über
# einen Prozessaufruf übergeben (es wird am Umbruch abgeschnitten), deshalb laufen die echten Prüffunktionen aus dem Skript in einer Shell
# mit $'...'-Literalen.
def _pruef_snippet(aufruf_zeile: str) -> subprocess.CompletedProcess:
    text = lies(KERN_EINRICHTEN)
    funktionen = "\n".join(_funktion(text, n) for n in ("aufruf", "pruefe_repos", "pruefe_dienste", "pruefe_profil"))
    uuid_re = re.search(r"^UUID_RE='.*'$", text, re.M).group(0)
    snippet = f"set -euo pipefail\n{uuid_re}\n{funktionen}\n{aufruf_zeile}\necho GEPRUEFT\n"
    return subprocess.run([BASH, "-c", snippet], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


@braucht_bash
@pytest.mark.parametrize("aufruf_zeile", [
    "pruefe_repos $'acme/a\\nacme/wamo'", "pruefe_repos $'acme/a\\n'", "pruefe_repos $'acme/a,acme/b\\nx y'", "pruefe_repos $'\\nacme/a'",
    "pruefe_repos $'acme/a\\x0bacme/b'", "pruefe_repos 'acme/a,,acme/b'", "pruefe_repos 'acme/a acme/b'",
    "pruefe_dienste $'jira\\nsupabase'", "pruefe_dienste $'jira\\n'", "pruefe_dienste 'jira;id'",
    "pruefe_profil $'6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d\\nkein-uuid'", "pruefe_profil $'6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d\\n'",
    "pruefe_profil $'6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d,\\n6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'", "pruefe_profil '6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d,'",
    "pruefe_profil '6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d '", "pruefe_profil 'KEIN-UUID'",
])
def test_werte_mit_zeilenumbruechen_und_steuerzeichen_werden_ganz_geprueft_und_abgelehnt(aufruf_zeile):
    p = _pruef_snippet(aufruf_zeile)
    assert p.returncode == 2 and "GEPRUEFT" not in p.stdout, (aufruf_zeile, p.stdout, p.stderr)


@braucht_bash
@pytest.mark.parametrize("aufruf_zeile", [
    "pruefe_repos 'acme/a,Acme/b'", "pruefe_repos 'acme/a'", "pruefe_dienste 'jira,vercel'", "pruefe_dienste ''",
    "pruefe_profil '6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d,6f1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e'",
])
def test_gueltige_werte_bestehen_die_ganze_pruefung(aufruf_zeile):
    p = _pruef_snippet(aufruf_zeile)
    assert p.returncode == 0 and "GEPRUEFT" in p.stdout, (aufruf_zeile, p.stdout, p.stderr)
