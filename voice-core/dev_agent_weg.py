"""Der Weg von WAMO zu Sokrates-2: Ziel, Schlüssel und Hostschlüssel, der SSH-Aufruf des Dispatchers und die Statustexte seiner
Ergebnisse (Ereignisse und Exit-Codes von "wamo-job-dispatch start").

Ausgelagert aus dev_agent.py als reine Verschiebung, ohne Änderung der Logik. dev_agent importiert alle Namen von hier, damit sie
dort wie bisher erreichbar sind (bot.py, scripts/, Tests). Wer in einem Test die Lesbarkeit einer Datei vortäuschen will, ersetzt
_lesbar in diesem Modul, nicht in dev_agent: ssh_problem ruft es hier auf.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import entwicklung_betrieb

SSH_ZIEL_STANDARD = "wamo@192.168.178.136"

SSH_KNOWN_HOSTS_STANDARD = "~/.ssh/known_hosts_wamo_jobs"  # nur der ed25519-Hostschlüssel von Sokrates-2

SSH_ZIEL_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}@[A-Za-z0-9.-]{1,253}")

REMOTE_ARG_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# Ergebnisse und Exit-Codes von "wamo-job-dispatch start" auf Statustexte
REMOTE_EREIGNIS_STATUS = {
    "selbsttest_fehlgeschlagen": "nicht gestartet (Wächter-Selbsttest auf Sokrates-2 fehlgeschlagen)",
    "keine_zugangsdaten": "nicht gestartet (kein Claude-Zugang auf Sokrates-2)",
    "belegt": "nicht gestartet (Projekt oder Auftrag belegt)",
    "zugang_erweitert": "wartet auf Freigabe (mehr Zugänge als frühere Aufträge im Projekt)",
    "start_abgelehnt": "nicht gestartet (GitHub-Token der App nicht erzeugt)",
    "zugang_ungueltig": "nicht gestartet (Claude-Zugang ungültig, bitte in der Karte Modellzugang neu hinterlegen)",
    # Kern-Kanal (Büro), grund ist einer der Codes aus entwicklung_betrieb.GRUND_SAETZE. Dieser Eintrag wird nie erreicht: remote_status
    # und _read_stream behandeln kanal_gesperrt vorher mit dem Satz zum Code. Er bleibt, weil ihn die Schnittstelle I6 nennt und der
    # Vertragstest (test_kernkanal_vertrag.py) ihn prüft, als Rückfall für jeden, der dieses Wörterbuch ohne die Sonderbehandlung liest.
    "kanal_gesperrt": "nicht gestartet (Kanal gesperrt)",
}

REMOTE_ENDE_STATUS = {
    "fertig": "fertig", "zeitlimit": "abgebrochen (Zeitlimit)", "abgebrochen": "abgebrochen",
    "verlassen": "abgebrochen (Verbindung beendet)",
    "freigabe_mehrfach": "abgebrochen (Freigabe öfter genutzt als erteilt)",
    "egress_offen": "abgebrochen (Ausgangsfilter auf Sokrates-2 ausgefallen)",
}

REMOTE_EXIT_STATUS = {
    2: "nicht gestartet (Auftrag abgewiesen)", 3: REMOTE_EREIGNIS_STATUS["selbsttest_fehlgeschlagen"],
    4: REMOTE_EREIGNIS_STATUS["belegt"], 5: REMOTE_EREIGNIS_STATUS["keine_zugangsdaten"],
    6: "fehlgeschlagen (Fehler im Dispatcher)", 7: REMOTE_EREIGNIS_STATUS["zugang_erweitert"],
    8: REMOTE_EREIGNIS_STATUS["start_abgelehnt"],
    124: "abgebrochen (Zeitlimit)", 130: "abgebrochen",
    255: "fehlgeschlagen (SSH-Verbindung zu Sokrates-2)",
}


def ssh_ziel() -> str:
    return (os.getenv("WAMO_ENTWICKLUNG_SSH_ZIEL") or SSH_ZIEL_STANDARD).strip()


def ssh_schluessel() -> Path:
    raw = os.getenv("WAMO_ENTWICKLUNG_SSH_SCHLUESSEL") or str(Path.home() / ".ssh" / "id_ed25519_wamo_jobs")
    return Path(os.path.expanduser(raw.strip()))


def ssh_known_hosts() -> Path:
    raw = os.getenv("WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS") or SSH_KNOWN_HOSTS_STANDARD
    return Path(os.path.expanduser(raw.strip()))


def _lesbar(pfad: Path) -> bool:
    """Lässt sich die Datei wirklich öffnen und lesen? (is_file allein sagt nichts über die Rechte.) Nur der Ausgang, nie der Pfad."""
    try:
        with open(pfad, "rb") as f:
            f.read(1)
    except OSError:
        return False
    return True


def ssh_problem() -> str | None:
    """Grund, warum die Betriebsart sokrates2 nicht startbar ist, oder None. Schlüssel und Hostschlüssel-Datei müssen
    vorhanden und lesbar sein; der Text nennt nie einen Pfad."""
    if not shutil.which("ssh"):
        return "ssh nicht gefunden (OpenSSH-Client installieren)"
    if not SSH_ZIEL_RE.fullmatch(ssh_ziel()):
        return "WAMO_ENTWICKLUNG_SSH_ZIEL ist ungültig (Form benutzer@host)"
    if not ssh_schluessel().is_file():
        return f"SSH-Schlüssel für Aufträge fehlt ({ssh_schluessel().name})"
    if not _lesbar(ssh_schluessel()):
        return "SSH-Schlüssel für Aufträge ist nicht lesbar"
    if not ssh_known_hosts().is_file():
        return f"Hostschlüssel-Datei für Aufträge fehlt ({ssh_known_hosts().name})"
    if not _lesbar(ssh_known_hosts()):
        return "Hostschlüssel-Datei für Aufträge ist nicht lesbar"
    return None


def ssh_argv(remote_args: list[str]) -> list[str]:
    """argv für einen Aufruf des Dispatchers. Keine Konfigurationsdatei (-F none), nur der Auftragsschlüssel,
    nur der hinterlegte ed25519-Hostschlüssel aus einer eigenen Datei (keine Aktualisierung), nur Public Key,
    keine Weiterleitungen, kein Terminal. Der Befehl besteht nur aus geprüften Wörtern."""
    exe = shutil.which("ssh")
    if not exe:
        raise RuntimeError("ssh nicht gefunden")
    for arg in remote_args:
        if not REMOTE_ARG_RE.fullmatch(arg):
            raise ValueError(f"unzulässiges Argument für Sokrates-2: {arg!r}")
    ziel = ssh_ziel()
    if not SSH_ZIEL_RE.fullmatch(ziel):
        raise ValueError("WAMO_ENTWICKLUNG_SSH_ZIEL ist ungültig")
    # UserKnownHostsFile nimmt mehrere, durch Leerraum getrennte Dateien: Pfad in Anführungszeichen (Leerzeichen
    # im Benutzerordner) und mit Schrägstrichen (Backslash wäre in Anführungszeichen ein Fluchtzeichen)
    hosts = ssh_known_hosts().as_posix()
    if '"' in hosts:
        raise ValueError("Pfad der Hostschlüssel-Datei enthält Anführungszeichen")
    return [exe, "-F", "none", "-i", str(ssh_schluessel()), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f'UserKnownHostsFile="{hosts}"',
            "-o", "GlobalKnownHostsFile=none", "-o", "UpdateHostKeys=no", "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
            "-o", "PreferredAuthentications=publickey", "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no",
            "-o", "ForwardX11=no", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4", "-o", "LogLevel=ERROR", "-T", "--", ziel, " ".join(remote_args)]


def remote_status(returncode: int | None, ereignisse: dict[str, dict]) -> str:
    """Statustext aus den wamo-Ereignissen des Dispatchers, ersatzweise aus dem Exit-Code von ssh."""
    ende = ereignisse.get("ende", {})
    if ende.get("ergebnis") in REMOTE_ENDE_STATUS:
        return REMOTE_ENDE_STATUS[ende["ergebnis"]]
    if ende.get("ergebnis") == "fehler":
        return f"beendet (Code {ende.get('code')})"
    if "kanal_gesperrt" in ereignisse:  # mit dem Satz zum Grund-Code, nie mit Werten des Dispatchers
        return entwicklung_betrieb.kanal_status(ereignisse["kanal_gesperrt"])
    for name, text in REMOTE_EREIGNIS_STATUS.items():
        if name in ereignisse:
            return text
    if returncode == 0:
        return "fertig"
    return REMOTE_EXIT_STATUS.get(returncode, f"beendet (Code {returncode})")
