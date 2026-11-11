#!/usr/bin/python3 -I
"""Rauchtest für den Kern-Kanal der Entwicklungsaufträge: /opt/wamo-core/bin/wamo-job-kern-rauchtest (als root auf Sokrates-2).

Installiert durch wamo-job-kern-einrichten.sh schluessel (aus ~/wamo-infra, nach der Prüfung der Herkunft). Alle Proben laufen NUR AM SERVER:
Sie brauchen den echten sshd, den Schlüssel von wamo-core und die geladene Unit. Der Test ruft den Dispatcher wie der Kern über ssh auf, als
Benutzer wamo-core mit den Sandbox-Eigenschaften der laufenden Unit (aus systemctl show wamo-core@<slot>, Muster wamo_pc_rauchtest.py),
und zwar mit genau den ssh-Optionen von dev_agent.ssh_argv (ein Test im Repo vergleicht sie). Er startet nie einen echten Auftrag: jede
Start-Probe ist so gebaut, dass sie abgewiesen werden muss, und die Probe bei geschlossenem Kanal läuft nur, wenn ping den Kanal als zu meldet.
Jede Nein-Probe kennt ihren Grund: der Dispatcher meldet ihn im Ereignis kanal_gesperrt (stdout) oder als Abweisung (stderr), der Test
vergleicht ihn. Ein Exit-Code allein genügt nie: ein ssh-Fehler (255), eine Zeitüberschreitung oder ein anderer Grund gelten als nicht bestanden.

    --ping       Ja-Probe: ping über ssh, Antwort mit Version 7 oder höher und kanal=kern
    --produktiv  alle Ja- und Nein-Proben, Isolation (wamo-core und wamo), Hostschlüssel, Dateirechte, PC-Datei unverändert (Prüfsumme)

Exit 0: alle Proben bestanden. 1: eine Probe ist fehlgeschlagen. 2: Umgebung ungeeignet (kein root, kein Linux, kein Schlüssel, keine laufende
Instanz). Die Ausgabe nennt nie Werte aus Geheimnisdateien (nur Namen, Modi und ok oder FEHLER). Die Übergangsproben (Absturz-Neustart,
Rückweg, PC und Kern gleichzeitig, WireGuard, Notaus mit laufendem Auftrag) sind Handarbeit der Stufen 2a und 3 und stehen am Ende als offen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NUR_AM_SERVER = True  # alle Proben dieses Tests sind nur am Server prüfbar (S3), im Repo prüfen Tests nur den Aufbau
EXIT_OK, EXIT_PROBE, EXIT_UMGEBUNG = 0, 1, 2

ZIEL = "wamo@192.168.178.136"
HOST = "192.168.178.136"
KERN_HOME = Path("/var/lib/wamo-core")
SCHLUESSEL = KERN_HOME / ".ssh" / "id_ed25519_wamo_jobs"
KNOWN_HOSTS = KERN_HOME / ".ssh" / "known_hosts_wamo_jobs"
KERN_DATEI = Path("/etc/ssh/authorized_keys.d/wamo-kern")
PC_DATEI = Path("/etc/ssh/authorized_keys.d/wamo")
KONFIG = Path("/etc/wamo/kernkanal.conf")
NOTAUS = Path("/etc/wamo/kernkanal.notaus")
HOSTSCHLUESSEL_PUB = Path("/etc/ssh/ssh_host_ed25519_key.pub")
RAUCHTEST = Path("/opt/wamo-core/bin/wamo-job-kern-rauchtest")
JOB_FREMD = "0123456789abcdef"  # eine Auftrags-ID, die es nicht gibt
ABWEISUNG = "WAMO-Auftrag abgewiesen: "  # Anfang der Meldung des Dispatchers auf stderr bei einer Abweisung ohne Kanal-Ereignis
BEFEHLE_GESCHLOSSEN = ("ping", "status", "pending", "cancel")  # wie wamo_job_kernkanal.py: Befehle bei geschlossenem Kanal
BEFEHLE_NOTAUS = ("ping", "status")  # und bei gesetzter Notaus-Datei (ein Test vergleicht beide Listen mit dem Modul)
ART_RE = re.compile(r"[a-z][a-z_]{1,29}")
KOPF_START = {"auftrag": "Rauchtest, darf nie laufen", "system": "", "zugang": "folgt", "profil": "00000000"}
UEBERGAENGE = ("Absturz-Neustart am freien Slot (systemctl kill, dann warte_auf_slot)", "Rückweg auf ein älteres Release (Entwicklung aus)",
               "PC und Kern gleichzeitig (belegt in beide Richtungen)", "ein PC über WireGuard landet nie im Kern-Kanal",
               "Notaus mit laufendem Kern-Auftrag")

# Sandbox-Eigenschaften der Unit, die der Test für den ssh-Aufruf übernimmt (nur einfache Werte; Exec*, Pfade und Zähler nie)
UNIT_EIGENSCHAFTEN = ("NoNewPrivileges", "ProtectSystem", "ProtectHome", "PrivateTmp", "PrivateDevices", "ProtectKernelTunables",
                      "ProtectKernelModules", "ProtectKernelLogs", "ProtectControlGroups", "ProtectClock", "ProtectHostname", "ProtectProc",
                      "RestrictNamespaces", "RestrictRealtime", "RestrictSUIDSGID", "LockPersonality", "RemoveIPC", "CapabilityBoundingSet",
                      "AmbientCapabilities", "SystemCallArchitectures", "SystemCallErrorNumber", "RestrictAddressFamilies", "UMask")

ergebnisse: list[tuple[bool, str]] = []


def pruefe(ok: bool, text: str) -> bool:
    ergebnisse.append((bool(ok), text))
    print(("  ok     " if ok else "  FEHLER ") + text, flush=True)
    return bool(ok)


# ---------------------------------------------------------------- Aufruf wie im Kern
def ssh_argv(remote: str, schluessel: str | os.PathLike = SCHLUESSEL, known_hosts: str | os.PathLike = KNOWN_HOSTS, ziel: str = ZIEL,
             exe: str = "/usr/bin/ssh", tty: bool = False) -> list[str]:
    """Die ssh-Optionen von dev_agent.ssh_argv (voice-core/dev_agent.py), Wort für Wort. remote ist der Befehl als eine Zeichenkette
    (der Kern übergibt geprüfte Wörter, der Test auch ungeprüfte, damit sshd und Dispatcher sie ablehnen können). tty=True ersetzt -T durch -tt."""
    hosts = Path(known_hosts).as_posix()
    return [exe, "-F", "none", "-i", str(schluessel), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f'UserKnownHostsFile="{hosts}"',
            "-o", "GlobalKnownHostsFile=none", "-o", "UpdateHostKeys=no", "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
            "-o", "PreferredAuthentications=publickey", "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no",
            "-o", "ForwardX11=no", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4", "-o", "LogLevel=ERROR", "-tt" if tty else "-T", "--", ziel, remote]


def unit_eigenschaften(show_text: str) -> list[str]:
    """Aus der Ausgabe von systemctl show die Sandbox-Eigenschaften als Argumente -p NAME=Wert für systemd-run."""
    argumente: list[str] = []
    for zeile in show_text.splitlines():
        name, gleich, wert = zeile.partition("=")
        if gleich and name in UNIT_EIGENSCHAFTEN:
            argumente += ["-p", f"{name}={wert}"]
    return argumente


# ---------------------------------------------------------------- Proben als Daten (der Aufbau ist im Repo prüfbar)
class Probe:
    """erwartet: Alternativen, von denen mindestens eine zutreffen muss. Jede ist ein Tupel:
    ("abgewiesen", Text)           Exit 2 und auf stderr "WAMO-Auftrag abgewiesen: <Text>" (der Dispatcher lehnt den Befehl ab, kein Kanal-Ereignis)
    ("kanal", Grund, ...)          Exit 2 und das Ereignis kanal_gesperrt mit einem dieser Gründe (Liste KANAL_GRUENDE des Moduls)
    ("zustand",)                   wie kanal, der Grund ist der des geschlossenen Kanals aus ping (kanal_zu, abgelaufen, notaus, ...)
    ("sshd", Exit oder None, Meldung, ...)  sshd oder ssh lehnt selbst ab: Exit wie angegeben und eine dieser Meldungen auf stderr
    Bei geschlossenem Kanal oder gesetzter Notaus-Datei greift vor der Regel der Probe schon der Zustand: siehe erlaubte_gruende."""

    def __init__(self, name: str, remote: str, *, ja: bool = False, kopf: dict | None = None, extra: tuple[str, ...] = (), tty: bool = False,
                 nur_geschlossen: bool = False, erwartet: tuple[tuple, ...] = ()):
        self.name, self.remote, self.ja, self.kopf, self.extra, self.tty, self.nur_geschlossen = name, remote, ja, kopf, extra, tty, nur_geschlossen
        self.erwartet = erwartet


def proben() -> list[Probe]:
    """Eine Ja-Probe und alle Nein-Proben aus Abschnitt 7 des Planpapiers, jede mit ihrem erwarteten Grund. {art} in remote setzt art_waehlen ein."""
    start = f"start {JOB_FREMD}"
    unzulaessig = ("abgewiesen", "Befehl hat unzul")  # ASCII-Anfang der Meldung "Befehl hat unzulässige Zeichen oder Form"
    return [
        Probe("ping (Ja)", "ping", ja=True),
        Probe("id", "id", erwartet=(("abgewiesen", "unbekannter Befehl"),)),
        Probe("leerer Befehl", "", erwartet=(("abgewiesen", "kein Befehl"),)),
        Probe("ping; id", "ping; id", erwartet=(unzulaessig,)),
        Probe("Befehl über 300 Zeichen", "ping " + "a" * 300, erwartet=(unzulaessig,)),
        # sshd verweigert das Terminal (PermitTTY no, restrict), der erlaubte ping antwortet trotzdem: die Meldung von ssh ist der Beleg
        Probe("-tt (Terminal)", "ping", tty=True, erwartet=(("sshd", None, "PTY allocation request failed"),)),
        # -W öffnet sofort einen direct-tcpip-Kanal, den der Server verweigern muss (ein -L allein baut nur den lokalen Teil auf, ohne Ablehnung)
        Probe("-L (Weiterleitung)", "", extra=("-W", "127.0.0.1:22"),
              erwartet=(("sshd", 255, "administratively prohibited", "open failed", "stdio forwarding failed"),)),
        # die erste Angabe einer ssh-Option gilt: probe_argv setzt extra vor die Basis-Optionen, sonst bliebe ClearAllForwardings=yes bestehen
        Probe("-R (Rückweiterleitung)", "ping", extra=("-o", "ClearAllForwardings=no", "-o", "ExitOnForwardFailure=yes",
                                                       "-R", "127.0.0.1:19998:127.0.0.1:22"),
              erwartet=(("sshd", 255, "remote port forwarding failed"),)),
        # der erzwungene Befehl ersetzt das Subsystem (der Dispatcher lehnt den Namen des Programms ab) oder sshd kennt kein Subsystem sftp
        Probe("sftp", "sftp", extra=("-s",), erwartet=(unzulaessig, ("abgewiesen", "unbekannter Befehl"),
                                                       ("sshd", 255, "subsystem request failed"))),
        Probe("pruefen", f"pruefen {JOB_FREMD}", erwartet=(("kanal", "befehl"),)),
        Probe("Start mit Projekt wamo", f"{start} wamo {{art}}", kopf=KOPF_START, erwartet=(("kanal", "projekt"),)),
        Probe("Start ohne Präfix kern-", f"{start} rauchtest {{art}}", kopf=KOPF_START, erwartet=(("kanal", "projekt"),)),
        Probe("Start ohne Feld zugang", f"{start} kern-rauchtest {{art}}", kopf={k: v for k, v in KOPF_START.items() if k not in ("zugang", "profil")},
              erwartet=(("kanal", "zugang"),)),
        Probe("Start mit zugang=server", f"{start} kern-rauchtest {{art}}", kopf={**KOPF_START, "zugang": "server"}, erwartet=(("kanal", "zugang"),)),
        Probe("fremde Auftrags-ID (status)", f"status {JOB_FREMD}", erwartet=(("kanal", "fremd"),)),
        Probe("fremde Auftrags-ID (cancel)", f"cancel {JOB_FREMD}", erwartet=(("kanal", "fremd"),)),
        Probe("Start bei geschlossenem Kanal", f"{start} kern-rauchtest {{art}}", kopf=KOPF_START, nur_geschlossen=True, erwartet=(("zustand",),)),
    ]


def art_waehlen(zustand: dict | None) -> str:
    """Die Art für die Start-Proben: recherche, sonst die erste Art, die ping meldet (nur gültige Namen). Mit einer erlaubten Art erreicht die
    Probe die Regel, die sie prüfen soll (projekt, zugang), statt schon an der Regel art zu enden."""
    arten = [a for a in zustand.get("arten", []) if isinstance(a, str) and ART_RE.fullmatch(a)] if isinstance(zustand, dict) and isinstance(zustand.get("arten"), list) else []
    return "recherche" if "recherche" in arten or not arten else arten[0]


def ereignis_gruende(stdout: str) -> list[str]:
    """Die Gründe aller Ereignisse kanal_gesperrt in der Ausgabe (eine JSON-Zeile je Ereignis)."""
    gruende = []
    for zeile in (stdout or "").splitlines():
        try:
            obj = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "wamo" and obj.get("ereignis") == "kanal_gesperrt" and isinstance(obj.get("grund"), str):
            gruende.append(obj["grund"])
    return gruende


def ok_antwort(stdout: str) -> bool:
    """Steht in der Ausgabe eine JSON-Zeile mit ok=true? Das wäre eine Zusage, keine Abweisung."""
    for zeile in (stdout or "").splitlines():
        try:
            obj = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("ok") is True:
            return True
    return False


def grund_des_kanals(zustand: dict | None) -> str | None:
    """Der Grund, aus dem der Kanal geschlossen ist (nach ping), sonst None (offen oder unbekannt)."""
    if isinstance(zustand, dict) and zustand.get("offen") is False and isinstance(zustand.get("grund"), str):
        return zustand["grund"]
    return None


def erlaubte_gruende(probe: Probe, alternative: tuple, zustand: dict | None) -> set[str]:
    """Die Gründe, die zu dieser Probe passen. Ist der Kanal zu (oder die Notaus-Datei gesetzt), prüft der Dispatcher zuerst den Zustand: ein
    Befehl, der dann nicht erlaubt ist (BEFEHLE_GESCHLOSSEN, BEFEHLE_NOTAUS), endet mit dem Grund des Kanals, alle anderen erreichen ihre Regel."""
    kanal = grund_des_kanals(zustand)
    if alternative[0] == "zustand":
        return {kanal} if kanal else set()
    gruende = set(alternative[1:])
    if kanal is None:
        return gruende
    erlaubt = BEFEHLE_NOTAUS if kanal == "notaus" else BEFEHLE_GESCHLOSSEN
    return gruende if probe.remote.split(" ")[0] in erlaubt else {kanal}


def _alternative(probe: Probe, alternative: tuple, returncode: int, stdout: str, stderr: str, zustand: dict | None) -> tuple[bool, str]:
    art = alternative[0]
    if art == "sshd":
        _, exit_code, *meldungen = alternative
        gefunden = any(m.lower() in (stderr or "").lower() for m in meldungen)
        return (exit_code is None or returncode == exit_code) and gefunden, f"sshd oder ssh lehnt ab ({meldungen[0]})"
    if returncode != 2 or ok_antwort(stdout):
        return False, "Exit 2 ohne ok-Antwort"
    if art == "abgewiesen":
        return ABWEISUNG + alternative[1] in (stderr or ""), f"Abweisung ({alternative[1]})"
    erlaubt = erlaubte_gruende(probe, alternative, zustand)
    gefunden = ereignis_gruende(stdout)
    return bool(gefunden) and bool(erlaubt) and all(g in erlaubt for g in gefunden), "Grund " + "/".join(sorted(erlaubt) or ["?"])


def urteil(probe: Probe, returncode: int | None, stdout: str, stderr: str = "", zustand: dict | None = None) -> tuple[bool, str]:
    """(bestanden, Text für die Ausgabe). Der Text nennt Exit-Code und Gründe (feste Wörter), nie den rohen Text von stderr."""
    if returncode is None:
        return False, "Zeitüberschreitung"
    if probe.ja:
        try:
            antwort = json.loads(stdout.strip().splitlines()[-1])
            ok = (returncode == 0 and antwort.get("ok") is True and antwort.get("kanal") == "kern" and int(str(antwort.get("version"))) >= 7)
        except (ValueError, IndexError, AttributeError, TypeError):
            ok = False
        return ok, f"Exit {returncode}"
    einzeln = [_alternative(probe, alt, returncode, stdout, stderr, zustand) for alt in probe.erwartet]
    for ok, beschreibung in einzeln:
        if ok:
            return True, f"Exit {returncode}, {beschreibung}"
    erwartet = " oder ".join(b for _, b in einzeln) or "kein erwarteter Grund hinterlegt"
    gruende = ereignis_gruende(stdout)
    if returncode == 255:
        gefunden = "ssh-Fehler"
    elif ok_antwort(stdout):
        gefunden = "ok-Antwort"
    else:
        gefunden = "Grund " + "/".join(gruende) if gruende else "kein Grund im Ereignis"
    return False, f"Exit {returncode}, {gefunden}, erwartet: {erwartet}"


def bewerte(probe: Probe, returncode: int | None, stdout: str, stderr: str = "", zustand: dict | None = None) -> bool:
    """Ja: Exit 0 und eine JSON-Antwort mit ok, Version 7 oder höher und kanal=kern. Nein: der erwartete Grund der Probe (siehe Probe)."""
    return urteil(probe, returncode, stdout, stderr, zustand)[0]


def datei_ok(pfad: Path, modus: int, besitzer: str, gruppe: str) -> tuple[bool, str]:
    """Modus und Besitzer einer Datei (nie ihr Inhalt)."""
    import grp
    import pwd
    try:
        st = os.lstat(pfad)
    except OSError:
        return False, f"{pfad} fehlt"
    ist = (stat.S_IMODE(st.st_mode), pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name)
    return ist == (modus, besitzer, gruppe), f"{pfad} {oct(ist[0])[2:]} {ist[1]}:{ist[2]} (erwartet {oct(modus)[2:]} {besitzer}:{gruppe})"


# ---------------------------------------------------------------- Lauf am Server
def lauf(argv, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 60)
    return subprocess.run(argv, check=False, **kw)


def aktiver_slot() -> str | None:
    for slot in ("a", "b"):
        if lauf(["systemctl", "is-active", "--quiet", f"wamo-core@{slot}.service"]).returncode == 0:
            return slot
    return None


def als_wamo_core(slot: str, argv: list[str], stdin: str | None = None, dauer: int = 40) -> subprocess.CompletedProcess:
    """argv als Benutzer wamo-core mit den Eigenschaften der Unit (systemd-run, nur ein Aufruf, Ausgabe über Pipes)."""
    eigenschaften = unit_eigenschaften(lauf(["systemctl", "show", f"wamo-core@{slot}.service"]).stdout)
    befehl = ["systemd-run", "--quiet", "--wait", "--collect", "--pipe", "--service-type=exec", "--uid=wamo-core", "--gid=wamo-core",
              "-E", f"HOME={KERN_HOME}", *eigenschaften, *argv]
    try:
        return lauf(befehl, input=stdin if stdin is not None else "", timeout=dauer)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(befehl, None, "", "Zeitüberschreitung")  # type: ignore[arg-type]


def probe_argv(probe: Probe, known_hosts: str | os.PathLike = KNOWN_HOSTS, ziel: str = ZIEL, art: str = "recherche") -> list[str]:
    """Der ssh-Aufruf der Probe (ohne systemd-run). Zusätzliche Optionen stehen vor den Basis-Optionen: bei ssh gilt die erste Angabe."""
    argv = ssh_argv(probe.remote.replace("{art}", art), known_hosts=known_hosts, ziel=ziel, tty=probe.tty)
    if probe.extra == ("-s",):  # Subsystem: ssh -s <ziel> sftp
        argv = argv[:argv.index("--")] + ["-s", "--", ziel, "sftp"]
        argv.remove("-T")
    elif probe.extra[:1] == ("-W",):  # Stdio-Weiterleitung: -W ersetzt den Befehl
        argv = argv[:argv.index("--")] + list(probe.extra) + ["--", ziel]
    elif probe.extra:
        argv = argv[:3] + list(probe.extra) + argv[3:]  # hinter exe, -F none
    return argv


def ssh_probe(slot: str, probe: Probe, known_hosts: str | os.PathLike = KNOWN_HOSTS, ziel: str = ZIEL, art: str = "recherche") -> subprocess.CompletedProcess:
    kopf = (json.dumps(probe.kopf) + "\n") if probe.kopf else ""
    return als_wamo_core(slot, probe_argv(probe, known_hosts, ziel, art), stdin=kopf)


def ping_zustand(stdout: str) -> dict | None:
    """Das Objekt kernkanal aus der Antwort auf ping (offen, grund, arten, ...), sonst None."""
    try:
        zustand = json.loads(stdout.strip().splitlines()[-1])["kernkanal"]
    except (ValueError, IndexError, KeyError, TypeError, AttributeError):
        return None
    return zustand if isinstance(zustand, dict) else None


def ja_probe(slot: str) -> dict | None:
    """Die Ja-Probe: ein ping über ssh. Liefert das Objekt kernkanal der Antwort (für die Nein-Proben) oder None."""
    erste = proben()[0]
    r = ssh_probe(slot, erste)
    ok, text = urteil(erste, r.returncode, r.stdout)
    pruefe(ok, f"ping über ssh als wamo-core: Antwort ok, Version 7 oder höher, kanal=kern ({text})")
    return ping_zustand(r.stdout) if ok else None


def nein_proben(slot: str, zustand: dict | None) -> None:
    """Alle Nein-Proben. zustand ist das Objekt kernkanal aus ping: Start-Proben nehmen eine Art, die der Kanal erlaubt, und der Grund eines
    geschlossenen Kanals zählt dort, wo der Dispatcher den Zustand vor der Regel der Probe prüft."""
    zu = isinstance(zustand, dict) and zustand.get("offen") is False
    art = art_waehlen(zustand)
    if zustand is None:
        print("  Hinweis: der Zustand des Kanals ist unbekannt (die Ja-Probe schlug fehl), die Gründe der Nein-Proben gelten für einen offenen Kanal", flush=True)
    for probe in proben()[1:]:
        if probe.nur_geschlossen and not zu:
            print(f"  übersprungen {probe.name} (der Kanal ist offen: ein gültiger Start darf nie probiert werden)", flush=True)
            continue
        r = ssh_probe(slot, probe, art=art)
        ok, text = urteil(probe, r.returncode, r.stdout, r.stderr, zustand)
        pruefe(ok, f"Nein: {probe.name} ({text})")


def schluessel_und_hosts(slot: str) -> None:
    """Hostschlüssel und Herkunft: eine falsche known_hosts ergibt Exit 255, eine Verbindung über 127.0.0.1 scheitert an from=."""
    tmp = Path(tempfile.mkdtemp(prefix="wamo-kern-rauch-", dir="/run"))
    os.chmod(tmp, 0o755)  # wamo-core muss die Probe-Dateien lesen können; sie enthalten nur öffentliche Hostschlüssel
    try:
        pub = HOSTSCHLUESSEL_PUB.read_text().split()
        falsch = tmp / "known_hosts_falsch"
        fremd = lauf(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp / "fremd")])
        falscher_schluessel = (tmp / "fremd.pub").read_text().split() if fremd.returncode == 0 else pub
        falsch.write_text(f"{HOST} {falscher_schluessel[0]} {falscher_schluessel[1]}\n")
        loopback = tmp / "known_hosts_loopback"
        loopback.write_text(f"127.0.0.1 {pub[0]} {pub[1]}\n")
        for datei in (falsch, loopback):
            os.chmod(datei, 0o644)
        r = ssh_probe(slot, proben()[0], known_hosts=falsch)
        pruefe(r.returncode == 255, f"Nein: falsche known_hosts (Exit {r.returncode}, erwartet 255)")
        r = ssh_probe(slot, proben()[0], known_hosts=loopback, ziel="wamo@127.0.0.1")
        pruefe(r.returncode == 255 and "ping" not in (r.stdout or ""), f"Nein: Verbindung über 127.0.0.1 scheitert an from= (Exit {r.returncode})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def isolation() -> None:
    r = lauf(["runuser", "-u", "wamo-core", "--", "test", "-r", str(KONFIG)])
    pruefe(r.returncode != 0, "wamo-core liest /etc/wamo/kernkanal.conf nicht")
    r = lauf(["runuser", "-u", "wamo", "--", "test", "-r", str(SCHLUESSEL)])
    pruefe(r.returncode != 0, "wamo liest den Schlüssel von wamo-core nicht")
    r = lauf(["id", "-nG", "wamo-core"])
    pruefe(r.stdout.split() == ["wamo-core"], "wamo-core ist in keiner weiteren Gruppe (nie in wamo)")


def dateirechte() -> None:
    for pfad, modus, besitzer, gruppe in ((SCHLUESSEL, 0o600, "wamo-core", "wamo-core"), (SCHLUESSEL.parent, 0o700, "wamo-core", "wamo-core"),
                                          (KNOWN_HOSTS, 0o644, "wamo-core", "wamo-core"), (KERN_DATEI, 0o644, "root", "root"),
                                          (KONFIG, 0o640, "root", "wamo"), (RAUCHTEST, 0o755, "root", "root")):
        ok, text = datei_ok(pfad, modus, besitzer, gruppe)
        pruefe(ok, "Rechte " + text)
    if NOTAUS.exists():
        ok, text = datei_ok(NOTAUS, 0o640, "root", "wamo")
        pruefe(ok, "Rechte " + text)
    zeilen = [z for z in KERN_DATEI.read_text().splitlines() if z.strip()] if KERN_DATEI.exists() else []
    pruefe(len(zeilen) == 1 and "expiry-time" not in zeilen[0] and zeilen[0].startswith('restrict,from="192.168.178.136",command="/opt/wamo/bin/wamo-job-dispatch" ssh-ed25519 ')
           and zeilen[0].endswith(" wamo-kern-jobs"), "authorized_keys.d/wamo-kern: genau eine Zeile in der Form aus I1, ohne expiry-time")


def summe(pfad: Path) -> str | None:
    try:
        return hashlib.sha256(pfad.read_bytes()).hexdigest()
    except OSError:
        return None


def umgebung_pruefen(produktiv: bool) -> str | None:
    """Grund, warum der Test hier nicht laufen kann (Exit 2), sonst None."""
    if not sys.platform.startswith("linux") or not hasattr(os, "geteuid"):
        return "läuft nur auf dem Server (Linux)"
    if os.geteuid() != 0:
        return "bitte als root ausführen (sudo)"
    for programm in ("systemctl", "systemd-run", "ssh-keygen", "runuser"):
        if not shutil.which(programm):
            return f"{programm} fehlt"
    if not Path("/usr/bin/ssh").exists():
        return "/usr/bin/ssh fehlt"
    if not SCHLUESSEL.exists() or not KNOWN_HOSTS.exists():
        return "Schlüssel oder known_hosts des Kerns fehlen (erst wamo-job-kern-einrichten.sh schluessel)"
    return None


def main(argv: list[str] | None = None) -> int:
    argumente = sys.argv[1:] if argv is None else argv
    if argumente not in (["--ping"], ["--produktiv"]):
        print("Aufruf: wamo-job-kern-rauchtest --ping | --produktiv", file=sys.stderr)
        return EXIT_UMGEBUNG
    produktiv = argumente == ["--produktiv"]
    grund = umgebung_pruefen(produktiv)
    if grund:
        print(f"Umgebung ungeeignet: {grund}", file=sys.stderr)
        return EXIT_UMGEBUNG
    slot = aktiver_slot()
    if slot is None:
        print("Umgebung ungeeignet: weder wamo-core@a noch wamo-core@b läuft", file=sys.stderr)
        return EXIT_UMGEBUNG
    print(f"== Kern-Kanal, Instanz wamo-core@{slot} (alle Proben nur am Server prüfbar)")
    vorher = summe(PC_DATEI)
    zustand = ja_probe(slot)  # einmal: das Objekt kernkanal aus der Antwort steuert die Nein-Proben
    if produktiv:
        print("== Nein-Proben")
        nein_proben(slot, zustand)
        print("== Hostschlüssel und Herkunft")
        schluessel_und_hosts(slot)
        print("== Isolation")
        isolation()
        print("== Dateirechte")
        dateirechte()
        pruefe(summe(PC_DATEI) == vorher and vorher is not None, "authorized_keys.d/wamo (PC) unverändert (Prüfsumme)")
        print("== Übergangsproben (Handarbeit, hier nicht automatisiert)")
        for text in UEBERGAENGE:
            print(f"  offen  {text}")
    fehler = [t for ok, t in ergebnisse if not ok]
    print(f"{len(ergebnisse) - len(fehler)} von {len(ergebnisse)} Proben bestanden." + (" FEHLER: " + "; ".join(fehler) if fehler else ""))
    return EXIT_PROBE if fehler else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
