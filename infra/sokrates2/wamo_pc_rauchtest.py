#!/usr/bin/python3 -I
"""Rauchtest für den Rücktunnel je Gerät: /opt/wamo-pc/bin/wamo-pc-rauchtest (als root auf Sokrates-2).

Zwei Probe-Geräte (wamo-t-pa, wamo-t-pb, Ordner /run/wamo-pc/pa und pb, Wegwerfschlüssel) verbinden sich von Sokrates-2 aus
über 192.168.178.136 (passt zum from= der Schlüssel). Die echten Plätze (wamo-t-01 bis 64) werden nicht berührt; am Ende
prüft der Test, dass sich an ihnen nichts geändert hat. Bei jedem Nein-Fall wird zusätzlich der Grund im sshd-Journal
gesucht, damit ein "Nein" nicht aus einem anderen Grund (Tippfehler, Netz) zustande kommt.

Prüft:
  Eingaben     ungültige Eingaben schreiben nichts (Exit 2)
  Ja           A bindet den eigenen Socket, Token und Freigabe-Schlüssel landen 0640 wamo-t-pa:wamo-core, Fingerabdruck stimmt
  Nein         A bindet in den Ordner von B; A ersetzt den Socket von B; -L auf den Kern-Socket; -R auf einen TCP-Port;
               Befehl/Shell; -W; sftp; TTY
  Verbindung   zweite Verbindung desselben Geräts: neueste gewinnt (alte beendet, Exit 75), danach klappt die dritte;
               endet eine Verbindung, löscht sie nur ihre eigenen Dateien (Inode)
  Sperren      Eintrag aus der Liste nehmen: laufende Verbindung endet binnen eines Abgleichslaufs, neue wird abgelehnt
  Gruppe       wamo-core enthält nur wamo-core
Aufruf:  sudo python3 -I /opt/wamo-pc/bin/wamo-pc-rauchtest
Exit 0 = alle Fälle bestanden. Räumt am Ende alles auf (auch bei Fehlern).
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOST = "192.168.178.136"
WURZEL = Path("/run/wamo-pc")
ABGLEICH = "/opt/wamo-pc/bin/wamo-pc-abgleich"
TOKEN_A = "A" * 43
FREIGABE_A = "B" * 43

ergebnisse: list[tuple[bool, str]] = []


def pruefe(ok: bool, text: str) -> bool:
    ergebnisse.append((bool(ok), text))
    print(("  ok     " if ok else "  FEHLER ") + text, flush=True)
    return bool(ok)


def lauf(argv, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 30)
    return subprocess.run(argv, check=False, **kw)


class Umgebung:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wamo-pc-rauch-", dir="/run"))
        os.chmod(self.tmp, 0o700)
        self.known = self.tmp / "known_hosts"
        hostschluessel = Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().split()
        self.known.write_text(f"{HOST} {hostschluessel[0]} {hostschluessel[1]}\n")
        self.schluessel = {}
        for name in ("pa", "pb"):
            pfad = self.tmp / f"key_{name}"
            lauf(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"rauchtest-{name}", "-f", str(pfad)])
            pub = " ".join(pfad.with_suffix(".pub").read_text().split()[:2])
            fp = lauf(["/usr/bin/ssh-keygen", "-l", "-f", str(pfad) + ".pub"]).stdout.split()[1]
            self.schluessel[name] = (pfad, pub, fp)
        self.verbindungen: list[subprocess.Popen] = []

    def liste(self, namen: list[str]) -> Path:
        eintraege = [{"name": n, "kennung": f"pc-probe-{n[1]}", "ssh_pubkey": self.schluessel[n][1],
                      "ssh_fingerprint": self.schluessel[n][2]} for n in namen]
        pfad = self.tmp / "liste.json"
        pfad.write_text(json.dumps(eintraege))
        return pfad

    def abgleich(self, namen: list[str], sandbox: bool = False) -> subprocess.CompletedProcess:
        """Abgleich im Probe-Modus. Mit sandbox=True unter denselben Sandkasten-Einstellungen wie die echte Unit (aus
        /etc/systemd/system/wamo-pc-abgleich.service gelesen): so fällt auf, wenn eine Einstellung etwa die setgid-Ordner
        (2750) von systemd-tmpfiles verbietet."""
        argv = [ABGLEICH, "--probe", "--liste-datei", str(self.liste(namen))]
        if sandbox:
            argv = (["/usr/bin/systemd-run", "--quiet", "--wait", "--collect", "--pipe", "--service-type=oneshot"]
                    + sandbox_eigenschaften() + argv)
        return lauf(argv, timeout=90)

    def ssh_argv(self, name: str, extra: list[str], benutzer: str | None = None, vorne: list[str] | None = None,
                 nach: list[str] | None = None):
        return (["/usr/bin/ssh", "-F", "none", "-i", str(self.schluessel[name][0]), "-o", "IdentitiesOnly=yes",
                 "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.known}",
                 "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR", "-o", "ServerAliveInterval=5"]
                + (vorne or []) + extra + [f"{benutzer or 'wamo-t-' + name}@{HOST}"] + (nach or []))

    def ssh(self, name: str, extra: list[str], stdin=None, dauer: float = 20, **kw) -> subprocess.CompletedProcess:
        """ssh-Aufruf. Bytes als stdin werden für den Textmodus von lauf() als latin-1 gereicht (byteweise gleich). Eine
        Zeitüberschreitung liefert returncode None (nicht "abgelehnt"): ein unerwartet erfolgreiches -N hängt ewig."""
        eingabe = stdin.decode("latin-1") if isinstance(stdin, bytes) else stdin
        try:
            return lauf(self.ssh_argv(name, extra, **kw), stdin=subprocess.DEVNULL if stdin is None else None,
                        input=eingabe, timeout=dauer)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess([], None, "", "Zeitüberschreitung")

    def halte(self, name: str, token=TOKEN_A, freigabe=FREIGABE_A, extra=None) -> subprocess.Popen:
        """Verbindung mit Rückweiterleitung und Geheimnissen; bleibt offen (stdin als Pipe)."""
        sock = f"/run/wamo-pc/{name}/pc.sock"
        p = subprocess.Popen(self.ssh_argv(name, ["-o", "ExitOnForwardFailure=yes", "-R", f"{sock}:127.0.0.1:9"]
                                           + (extra or [])),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.verbindungen.append(p)
        p.stdin.write(f"{token}\n{freigabe}\n".encode())
        p.stdin.flush()
        return p

    def aufraeumen(self):
        for p in self.verbindungen:
            if p.poll() is None:
                p.kill()
            try:
                p.communicate(timeout=3)
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


def sandbox_eigenschaften() -> list[str]:
    """-p Schlüssel=Wert für systemd-run aus dem [Service]-Block der echten Abgleich-Unit (ohne Exec*, Type, Timeout)."""
    ausgabe: list[str] = []
    im_service = False
    for zeile in Path("/etc/systemd/system/wamo-pc-abgleich.service").read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if zeile.startswith("["):
            im_service = zeile == "[Service]"
            continue
        if not im_service or not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel = zeile.split("=", 1)[0]
        if schluessel in ("Type", "ExecStart", "ExecStartPre", "TimeoutStartSec"):
            continue
        ausgabe += ["-p", zeile]
    return ausgabe


def abgelehnt(r: subprocess.CompletedProcess) -> bool:
    """Der Aufruf endete mit einem Fehlercode (nicht erfolgreich und nicht durch Zeitüberschreitung beendet)."""
    return r.returncode not in (None, 0)


def warte(bedingung, frist=15.0, schritt=0.2) -> bool:
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(schritt)
    return False


def journal(seit: float) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seit - 1))  # eine Sekunde Reserve: --since kennt keine Bruchteile
    return lauf(["/usr/bin/journalctl", "-u", "ssh.service", "-u", "ssh.socket", "-t", "sshd", "--since", stamp,
                 "--no-pager", "-o", "cat"]).stdout


def log_enthaelt(seit: float, muster: str, frist: float = 6.0) -> bool:
    return warte(lambda: re.search(muster, journal(seit)) is not None, frist, 0.5)


def datei(name: str, ordner: str) -> Path:
    return WURZEL / ordner / name


def stat_von(pfad: Path):
    try:
        return os.lstat(pfad)
    except OSError:
        return None


def echte_plaetze() -> str:
    """Fingerabdruck des Zustands der echten Plätze (Benutzer, Schlüsseldateien, Ordner, tmpfiles)."""
    teile = []
    for e in sorted(pwd.getpwall(), key=lambda x: x.pw_name):
        if re.fullmatch(r"wamo-t-(0[1-9]|[1-5][0-9]|6[0-4])", e.pw_name):
            teile.append(f"user {e.pw_name} {e.pw_uid}")
    for p in sorted(Path("/etc/ssh/authorized_keys.d").glob("wamo-t-[0-9]*")):
        teile.append(f"key {p.name} {hashlib.sha256(p.read_bytes()).hexdigest()}")
    for p in sorted(WURZEL.glob("t[0-9]*")):
        teile.append(f"dir {p.name} {oct(p.stat().st_mode)}")
    t = Path("/etc/tmpfiles.d/wamo-pc.conf")
    teile.append("tmpfiles " + (hashlib.sha256(t.read_bytes()).hexdigest() if t.exists() else "-"))
    return "\n".join(teile)


def haupt() -> int:
    if os.geteuid() != 0:
        print("Bitte als root ausführen.", file=sys.stderr)
        return 1
    if not Path(ABGLEICH).exists():
        print("wamo-pc-tunnel-einrichten.sh ist noch nicht gelaufen.", file=sys.stderr)
        return 1
    vorher = echte_plaetze()
    env = Umgebung()
    gid_kern = grp.getgrnam("wamo-core").gr_gid
    try:
        print("== Vorbereitung: Probe-Geräte pa und pb")
        r = env.abgleich(["pa", "pb"], sandbox=True)
        pruefe(r.returncode == 0, f"Abgleich läuft unter dem Sandkasten der Unit (Exit {r.returncode})")
        pruefe(len(sandbox_eigenschaften()) >= 10, "  Sandkasten-Einstellungen der Unit gelesen")
        uid_a, uid_b = pwd.getpwnam("wamo-t-pa").pw_uid, pwd.getpwnam("wamo-t-pb").pw_uid
        for n in ("pa", "pb"):
            e = pwd.getpwnam(f"wamo-t-{n}")
            pruefe(e.pw_shell == "/opt/wamo-pc/bin/wamo-pc-token", f"wamo-t-{n}: Login-Shell ist der Zwangsbefehl")
            pruefe(lauf(["/usr/bin/id", "-nG", e.pw_name]).stdout.split() == [e.pw_name], f"wamo-t-{n}: keine Zusatzgruppen")
            s = stat_von(WURZEL / n)
            pruefe(s is not None and s.st_uid == e.pw_uid and s.st_gid == gid_kern and stat.S_IMODE(s.st_mode) == 0o2750,
                   f"/run/wamo-pc/{n} ist 2750 wamo-t-{n}:wamo-core")
        s = stat_von(WURZEL)
        pruefe(s is not None and s.st_uid == 0 and stat.S_IMODE(s.st_mode) == 0o711, "/run/wamo-pc ist 0711 root:root")
        sshd_t = lauf(["/usr/sbin/sshd", "-T", "-C", "user=wamo-t-pa,host=pc,addr=192.168.178.2"]).stdout
        for muster in ("allowtcpforwarding remote", "permitopen none", "permitlisten none", "maxsessions 1",
                       "permittty no", "exposeauthinfo yes", "forcecommand /opt/wamo-pc/bin/wamo-pc-token",
                       "allowstreamlocalforwarding remote"):
            pruefe(muster in sshd_t, f"sshd -T für wamo-t-pa: {muster}")

        print("== Ungültige Eingaben schreiben nichts")
        faelle = {
            "eine Zeile": b"A" * 43 + b"\n",
            "zu kurz": b"A" * 10 + b"\n" + b"B" * 43 + b"\n",
            "gleich": b"A" * 43 + b"\n" + b"A" * 43 + b"\n",
            "Sonderzeichen": b"A" * 42 + b"!\n" + b"B" * 43 + b"\n",
            "zu lang": b"A" * 700 + b"\n" + b"B" * 43 + b"\n",
        }
        for titel, eingabe in faelle.items():
            r = env.ssh("pa", [], stdin=eingabe)
            leer = not any((WURZEL / "pa" / n).exists() for n in ("host-token", "host-freigabe", "schluessel"))
            pruefe(r.returncode == 2 and leer, f"ungültige Eingabe ({titel}): Exit 2, nichts geschrieben (Exit {r.returncode})")

        print("== Ja: A bindet den eigenen Socket")
        t0 = time.time()
        a1 = env.halte("pa")
        pruefe(warte(lambda: datei("host-token", "pa").exists()), "A: Token-Datei da")
        sock_a = datei("pc.sock", "pa")
        pruefe(warte(sock_a.exists), "A: Socket /run/wamo-pc/pa/pc.sock da")
        s = stat_von(sock_a)
        pruefe(s is not None and stat.S_ISSOCK(s.st_mode) and s.st_uid == uid_a and s.st_gid == gid_kern
               and stat.S_IMODE(s.st_mode) == 0o660, "A: Socket 0660 wamo-t-pa:wamo-core")
        for n, soll in (("host-token", TOKEN_A), ("host-freigabe", FREIGABE_A), ("schluessel", env.schluessel["pa"][2])):
            s = stat_von(datei(n, "pa"))
            ok = s is not None and s.st_uid == uid_a and s.st_gid == gid_kern and stat.S_IMODE(s.st_mode) == 0o640
            pruefe(ok, f"A: {n} ist 0640 wamo-t-pa:wamo-core")
            try:
                inhalt = datei(n, "pa").read_text().strip()
            except OSError:
                inhalt = ""
            pruefe(inhalt == soll, f"A: Inhalt von {n} stimmt" + ("" if n != "schluessel" else " (Fingerabdruck aus SSH_USER_AUTH)"))
        pruefe(not list(WURZEL.glob("pb/*")), "B: Ordner bleibt leer (Übergabe schreibt nur im eigenen Ordner)")

        print("== Nein: Grenzen der Tunnelbenutzer")
        b1 = env.halte("pb", token="C" * 43, freigabe="D" * 43)
        pruefe(warte(datei("pc.sock", "pb").exists), "B: eigener Socket da")
        ino_b = stat_von(datei("pc.sock", "pb")).st_ino
        t = time.time()
        r = env.ssh("pa", ["-o", "ExitOnForwardFailure=yes", "-N", "-R", "/run/wamo-pc/pb/pc.sock:127.0.0.1:9"])
        pruefe(abgelehnt(r), f"A ersetzt den Socket von B: abgelehnt (Exit {r.returncode})")
        pruefe(log_enthaelt(t, r"(Permission denied|cannot listen to path|bind)"), "  Grund im sshd-Journal (Rechte im Ordner von B)")
        s = stat_von(datei("pc.sock", "pb"))
        pruefe(s is not None and s.st_ino == ino_b and s.st_uid == uid_b, "  Socket von B unverändert (gleicher Inode, Besitzer B)")
        t = time.time()
        r = env.ssh("pa", ["-o", "ExitOnForwardFailure=yes", "-N", "-R", "/run/wamo-pc/pb/neu.sock:127.0.0.1:9"])
        pruefe(abgelehnt(r) and not datei("neu.sock", "pb").exists(), "A bindet einen neuen Socket in B's Ordner: abgelehnt")
        pruefe(log_enthaelt(t, r"(Permission denied|cannot listen to path|bind)"), "  Grund im sshd-Journal")

        t = time.time()
        lokal = env.tmp / "l.sock"
        p = subprocess.Popen(env.ssh_argv("pa", ["-N", "-L", f"{lokal}:/run/wamo-core/core.sock"]),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        env.verbindungen.append(p)
        warte(lokal.exists, 10)
        antwort = b"?"
        try:
            c = socket.socket(socket.AF_UNIX)
            c.settimeout(5)
            c.connect(str(lokal))
            c.sendall(b"GET / HTTP/1.0\r\n\r\n")
            antwort = c.recv(100)
            c.close()
        except OSError:
            antwort = b""
        pruefe(antwort == b"", "A: -L auf /run/wamo-core/core.sock liefert nichts")
        p.kill()
        try:
            _, fehlerausgabe = p.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            fehlerausgabe = b""
        pruefe(log_enthaelt(t, r"(administratively prohibited|open failed|not permitted)") or b"prohibited" in fehlerausgabe
               or b"open failed" in fehlerausgabe, "  Grund (Weiterleitung nach lokal verboten) im sshd-Journal oder am Client")

        t = time.time()
        r = env.ssh("pa", ["-o", "ExitOnForwardFailure=yes", "-N", "-R", "127.0.0.1:47001:127.0.0.1:9"])
        pruefe(abgelehnt(r), "A: -R auf einen TCP-Port (127.0.0.1:47001): abgelehnt")
        pruefe(log_enthaelt(t, r"(administratively prohibited|not permitted|Permission denied|listen|refused)"),
               "  Grund im sshd-Journal (PermitListen none)")
        pruefe(lauf(["/usr/bin/ss", "-ltn", "sport = :47001"]).stdout.count("47001") == 0, "  Port 47001 lauscht nicht")

        marke = Path("/tmp/wamo-pc-rauchtest-befehl")
        marke.unlink(missing_ok=True)
        r = env.ssh("pa", [], nach=[f"touch {marke}; id"])  # der Befehl steht HINTER dem Ziel
        pruefe(not marke.exists() and "uid=" not in r.stdout and r.returncode == 2,
               f"A: Befehl wird nicht ausgeführt, es läuft nur der Zwangsbefehl (dessen Exit 2 ohne Eingabe, hier {r.returncode})")
        r = env.ssh("pa", ["-W", "127.0.0.1:22"], dauer=15)
        pruefe(abgelehnt(r) and "SSH-2.0" not in r.stdout, "A: -W 127.0.0.1:22: abgelehnt")
        sb = env.tmp / "sftp.txt"
        sb.write_text("ls /\n")
        r = lauf(["/usr/bin/sftp", "-F", "none", "-i", str(env.schluessel["pa"][0]), "-o", "IdentitiesOnly=yes",
                  "-o", "BatchMode=yes", "-o", f"UserKnownHostsFile={env.known}", "-o", "LogLevel=ERROR",
                  "-b", str(sb), f"wamo-t-pa@{HOST}"], stdin=subprocess.DEVNULL)
        pruefe(abgelehnt(r) and "etc" not in r.stdout, "A: sftp: abgelehnt (Zwangsbefehl statt Subsystem)")
        t = time.time()
        r = env.ssh("pa", ["-tt"], stdin=b"")
        pruefe("PTY allocation request failed" in r.stderr or "PTY" in r.stderr, "A: TTY wird nicht vergeben (PermitTTY no)")

        print("== Streuner: Sockets außerhalb des eigenen Ordners")
        pruefe(a1.poll() is None, "A#1 läuft noch")
        r = lauf([ABGLEICH, "--streuner"])
        pruefe(r.returncode == 0 and "außerhalb" not in r.stdout and a1.poll() is None,
               "Streuner meldet den eigenen Tunnel nicht (auch nicht Sockets von pam_systemd unter /run/user)")
        streu = "/tmp/wamo-pc-rauchtest-streuner.sock"
        Path(streu).unlink(missing_ok=True)
        sp = subprocess.Popen(env.ssh_argv("pa", ["-o", "ExitOnForwardFailure=yes", "-N", "-R", f"{streu}:127.0.0.1:9"]),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        env.verbindungen.append(sp)
        pruefe(warte(lambda: Path(streu).exists(), 10), "A legt einen Socket in /tmp an (so weit kann ein Tunnelbenutzer schreiben)")
        r = lauf([ABGLEICH, "--streuner"])
        pruefe(streu in r.stdout and "WAMO-GERAET" in r.stdout, "Streuner meldet den Socket in /tmp mit Präfix WAMO-GERAET")
        pruefe(warte(lambda: sp.poll() is not None and a1.poll() is not None, 10), "  und beendet die Sitzungen von A")
        Path(streu).unlink(missing_ok=True)
        a1 = env.halte("pa")
        pruefe(warte(datei("pc.sock", "pa").exists), "A verbindet danach wieder (neuer eigener Tunnel steht)")

        print("== Verbindung: neueste gewinnt, nur eigene Inodes löschen")
        pruefe(a1.poll() is None, "A#1 läuft noch")
        a2 = env.halte("pa", token="E" * 43, freigabe="F" * 43)
        try:
            code = a2.wait(timeout=20)
        except subprocess.TimeoutExpired:
            code = None
        pruefe(code == 75, f"A#2 (zweite Verbindung) endet mit Exit 75 und beendet die alte (Exit {code})")
        pruefe(warte(lambda: a1.poll() is not None, 10), "A#1 wurde beendet")
        a3 = env.halte("pa", token="E" * 43, freigabe="F" * 43)
        pruefe(warte(lambda: datei("host-token", "pa").exists() and datei("host-token", "pa").read_text().strip() == "E" * 43),
               "A#3 (neu verbunden): neue Geheimnisse liegen im Ordner")
        pruefe(warte(datei("pc.sock", "pa").exists), "A#3: Socket vorhanden")
        # eine "neuere" Verbindung simulieren: Token-Datei durch eine andere Datei ersetzen, dann A#3 beenden
        fremd = datei("host-token", "pa")
        neu = fremd.with_name("neu-token")
        neu.write_text("G" * 43 + "\n")
        os.chmod(neu, 0o640)
        os.replace(neu, fremd)
        a3.stdin.close()
        pruefe(warte(lambda: a3.poll() is not None, 10), "A#3 endet nach EOF")
        pruefe(fremd.exists() and fremd.read_text().strip() == "G" * 43, "Beim Ende bleibt die fremde (neuere) Token-Datei erhalten")
        pruefe(not datei("host-freigabe", "pa").exists() and not datei("schluessel", "pa").exists(),
               "Beim Ende werden die eigenen Dateien gelöscht")
        fremd.unlink(missing_ok=True)

        print("== Sperren: Eintrag aus der Liste nehmen")
        a4 = env.halte("pa", token="H" * 43, freigabe="I" * 43)
        pruefe(warte(datei("pc.sock", "pa").exists), "A#4 verbunden")
        r = env.abgleich(["pb"])
        pruefe(r.returncode == 0, "Abgleich ohne A läuft")
        pruefe(warte(lambda: a4.poll() is not None, 10), "Laufende Verbindung von A wird getrennt")
        pruefe(not (Path("/etc/ssh/authorized_keys.d") / "wamo-t-pa").exists(), "Schlüsseldatei von A ist weg")
        pruefe(not (WURZEL / "pa").exists(), "Ordner von A ist weg")
        pruefe((WURZEL / "pb").exists() and b1.poll() is None, "B ist unberührt")
        r = env.ssh("pa", ["-o", "NumberOfPasswordPrompts=0"], stdin=b"")
        pruefe(r.returncode == 255 and "Permission denied" in r.stderr, "Neue Verbindung von A wird abgelehnt (publickey)")
        pruefe(log_enthaelt(time.time() - 20, r"(Connection closed by authenticating user wamo-t-pa|Failed publickey for wamo-t-pa|"
                            r"Invalid user|no matching|authentication)"), "  Grund im sshd-Journal")

        print("== Gruppe wamo-core")
        gruppe = grp.getgrnam("wamo-core")
        mitglieder = [m for m in gruppe.gr_mem if m]
        primaer = [e.pw_name for e in pwd.getpwall() if e.pw_gid == gruppe.gr_gid and e.pw_name != "wamo-core"]
        pruefe(mitglieder in ([], ["wamo-core"]) and not primaer,
               f"Gruppe wamo-core enthält nur wamo-core (Zusatzmitglieder {mitglieder or 'keine'}, andere Konten mit dieser Primärgruppe {primaer or 'keine'})")
    finally:
        env.abgleich([])
        env.aufraeumen()
        for n in ("pa", "pb"):
            lauf(["/usr/bin/pkill", "-u", f"wamo-t-{n}"])
            lauf(["/usr/sbin/userdel", f"wamo-t-{n}"])
        for pfad in Path("/etc/ssh/authorized_keys.d/gesperrt").glob("wamo-t-p?.*"):
            pfad.unlink(missing_ok=True)
        for pfad in Path("/etc/ssh/authorized_keys.d").glob("wamo-t-p?"):
            pfad.unlink(missing_ok=True)  # falls der Abgleich beim Aufräumen nicht lief (uid-Wiederverwendung vermeiden)
        Path("/etc/tmpfiles.d/wamo-pc-probe.conf").unlink(missing_ok=True)
        for n in ("pa", "pb"):
            shutil.rmtree(WURZEL / n, ignore_errors=True)
    print("== Aufräumen und echte Plätze")
    pruefe(echte_plaetze() == vorher, "Die echten Plätze (wamo-t-01 bis 64) sind unverändert")
    pruefe(not any(e.pw_name in ("wamo-t-pa", "wamo-t-pb") for e in pwd.getpwall()), "Probe-Benutzer sind entfernt")
    pruefe(not list(Path("/etc/ssh/authorized_keys.d").glob("wamo-t-p?")), "Schlüsseldateien der Probe-Geräte sind entfernt")
    fehler = [t for ok, t in ergebnisse if not ok]
    print(f"\n{len(ergebnisse) - len(fehler)} von {len(ergebnisse)} Prüfungen bestanden.")
    for t in fehler:
        print("  FEHLER:", t)
    return 0 if not fehler else 1


if __name__ == "__main__":
    sys.exit(haupt())
