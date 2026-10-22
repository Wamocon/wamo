#!/usr/bin/python3 -I
"""Zwangsbefehl für die Rücktunnel-Geräte: /opt/wamo-pc/bin/wamo-pc-token (Login-Shell UND ForceCommand von wamo-t-NN).

Der Laptop erzeugt bei jedem Start seines Hosts ein neues Host-Token und einen neuen Freigabe-Schlüssel und sendet beide
beim Tunnelaufbau über stdin der SSH-Verbindung (zwei Zeilen). Dieses Programm legt sie im Ordner des Geräts ab:

    /run/wamo-pc/tNN/host-token       Bearer-Token des Hosts
    /run/wamo-pc/tNN/host-freigabe    Freigabe-Schlüssel (zweites Geheimnis für Freigaben)
    /run/wamo-pc/tNN/schluessel       Fingerabdruck des benutzten SSH-Schlüssels (aus SSH_USER_AUTH berechnet)

Regeln (alle fail closed, Exit 2 bei ungültiger Eingabe, dann ist nichts geschrieben):
  * Alle Argumente und SSH_ORIGINAL_COMMAND werden ignoriert: Es gibt keinen Befehl, den der Laptop wählen kann.
  * Der Platz ergibt sich nur aus dem Benutzernamen des Prozesses (wamo-t-NN, NN 01 bis 64), nie aus der Eingabe.
  * Genau zwei Zeilen, je höchstens 600 Byte, beide [A-Za-z0-9_-]{43,512} und verschieden. Zeitgrenze 10 s.
  * Geschrieben wird nur in den eigenen Ordner: geöffnet mit O_NOFOLLOW|O_DIRECTORY, geprüft (Besitzer ist dieser
    Benutzer, Gruppe wamo-core, Modus 2750, Elternordner gehört root und ist nicht beschreibbar), danach nur noch
    relativ zu diesem Dateideskriptor (dir_fd). Dateien atomar (temp, fsync, rename), Modus 0640.
  * Der Fingerabdruck stammt aus SSH_USER_AUTH (sshd ExposeAuthInfo), nie aus der Eingabe.
  * Neueste Verbindung gewinnt: andere sshd-Sitzungen desselben Benutzers werden beendet; dann endet diese mit Exit
    75, damit der Laptop sofort sauber neu verbindet (sshd räumt beim Schließen den Socket der alten Verbindung weg).
  * Am Ende (EOF auf stdin, SIGHUP, SIGTERM) wird jede Datei nur gelöscht, wenn der Pfad noch genau die eigene Datei
    ist (st_dev, st_ino): eine neuere Verbindung behält ihre Werte.
Nie in Ausgaben: Token, Schlüssel, Pfade fremder Geräte.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import select
import signal
import stat
import sys
import time

try:
    import grp
    import pwd
except ImportError:  # Windows: nur damit die Tests die reinen Funktionen laden können
    grp = pwd = None

WURZEL = "/run/wamo-pc"
KERN_GRUPPE = "wamo-core"
MAX_ZEILE = 600
GEHEIMNIS_RE = re.compile(r"[A-Za-z0-9_-]{43,512}")
BENUTZER_RE = re.compile(r"wamo-t-(?:(0[1-9]|[1-5][0-9]|6[0-4])|(p[a-z]))")
LESE_FRIST_SECS = 10.0
ALTE_SITZUNG_FRIST_SECS = 5.0
EXIT_EINGABE = 2
EXIT_UMGEBUNG = 3
EXIT_NEU_VERBINDEN = 75
# Reihenfolge: das Token zuletzt, denn damit meldet sich der Kern an; dann sind die anderen Dateien schon da
DATEIEN = ("schluessel", "host-freigabe", "host-token")


class Abbruch(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(text)
        self.code = code
        self.text = text


def platz_ordner(benutzer: str) -> str:
    """Ordnername des Geräts zum Benutzernamen (wamo-t-05 -> t05, Probe wamo-t-pa -> pa)."""
    m = BENUTZER_RE.fullmatch(benutzer)
    if not m:
        raise Abbruch(EXIT_UMGEBUNG, "Kein Gerätebenutzer")
    return f"t{m.group(1)}" if m.group(1) else m.group(2)


def lies_zeilen(fd: int, frist_secs: float = LESE_FRIST_SECS, uhr=time.monotonic) -> list[bytes]:
    """Liest genau zwei Zeilen von fd (höchstens MAX_ZEILE Byte je Zeile, \\r vor \\n wird toleriert).
    Restliche Bytes, die schon im selben Lesevorgang ankamen, gehen verloren (der Laptop schickt nichts weiter)."""
    ende = uhr() + frist_secs
    puffer = b""
    while puffer.count(b"\n") < 2:
        rest = ende - uhr()
        if rest <= 0:
            raise Abbruch(EXIT_EINGABE, "Zeitüberschreitung")
        bereit, _, _ = select.select([fd], [], [], rest)
        if not bereit:
            raise Abbruch(EXIT_EINGABE, "Zeitüberschreitung")
        stueck = os.read(fd, 4096)
        if not stueck:
            raise Abbruch(EXIT_EINGABE, "Eingabe zu kurz")
        puffer += stueck
        if len(puffer) > 2 * (MAX_ZEILE + 2) + 4096:
            raise Abbruch(EXIT_EINGABE, "Eingabe zu lang")
    zeilen = puffer.split(b"\n")[:2]
    ergebnis = []
    for z in zeilen:
        if z.endswith(b"\r"):
            z = z[:-1]
        if len(z) > MAX_ZEILE:
            raise Abbruch(EXIT_EINGABE, "Zeile zu lang")
        ergebnis.append(z)
    return ergebnis


def pruefe_geheimnisse(zeilen: list[bytes]) -> tuple[str, str]:
    """Token und Freigabe-Schlüssel: nur ASCII, Regex, verschieden."""
    if len(zeilen) != 2:
        raise Abbruch(EXIT_EINGABE, "Genau zwei Zeilen erwartet")
    try:
        token, freigabe = (z.decode("ascii") for z in zeilen)
    except UnicodeDecodeError:
        raise Abbruch(EXIT_EINGABE, "Nur ASCII erlaubt") from None
    if not GEHEIMNIS_RE.fullmatch(token) or not GEHEIMNIS_RE.fullmatch(freigabe):
        raise Abbruch(EXIT_EINGABE, "Format ungültig")
    if token == freigabe:
        raise Abbruch(EXIT_EINGABE, "Token und Freigabe-Schlüssel müssen verschieden sein")
    return token, freigabe


def fingerabdruck_aus_auth(pfad: str | None) -> str:
    """SHA256-Fingerabdruck des benutzten Schlüssels aus der Datei in SSH_USER_AUTH ("publickey ssh-ed25519 AAAA...")."""
    if not pfad:
        raise Abbruch(EXIT_UMGEBUNG, "SSH_USER_AUTH fehlt (sshd ExposeAuthInfo)")
    try:
        with open(pfad, "rb") as f:
            inhalt = f.read(8192).decode("ascii", "replace")
    except OSError:
        raise Abbruch(EXIT_UMGEBUNG, "SSH_USER_AUTH nicht lesbar") from None
    for zeile in inhalt.splitlines():
        teile = zeile.split()
        if len(teile) >= 3 and teile[0] == "publickey" and teile[1] == "ssh-ed25519":
            try:
                blob = base64.b64decode(teile[2], validate=True)
            except ValueError:
                break
            if len(blob) == 51:
                return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    raise Abbruch(EXIT_UMGEBUNG, "Kein ed25519-Schlüssel in SSH_USER_AUTH")


def oeffne_geraeteordner(ordner: str, uid: int, kern_gid: int, wurzel: str = WURZEL) -> int:
    """Öffnet den Geräteordner ohne Links zu folgen und prüft Besitzer, Gruppe und Modus. Liefert den Deskriptor."""
    try:
        # O_PATH: /run/wamo-pc ist 0711 (betreten, nicht lesen); ein normales Öffnen zum Lesen scheiterte mit EACCES.
        # Mit O_PATH genügt das Durchsuchen-Recht; fstat und openat(dir_fd) funktionieren damit.
        wurzel_fd = os.open(wurzel, getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise Abbruch(EXIT_UMGEBUNG, "Wurzelordner fehlt") from None
    try:
        w = os.fstat(wurzel_fd)
        if w.st_uid != 0 or (w.st_mode & 0o022):
            raise Abbruch(EXIT_UMGEBUNG, "Wurzelordner gehört nicht root oder ist beschreibbar")
        try:
            fd = os.open(ordner, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wurzel_fd)
        except OSError:
            raise Abbruch(EXIT_UMGEBUNG, "Geräteordner fehlt") from None
    finally:
        os.close(wurzel_fd)
    s = os.fstat(fd)
    if s.st_uid != uid or s.st_gid != kern_gid or stat.S_IMODE(s.st_mode) != 0o2750:
        os.close(fd)
        raise Abbruch(EXIT_UMGEBUNG, "Geräteordner hat falsche Rechte")
    return fd


def schreibe_atomar(dir_fd: int, name: str, inhalt: str, kern_gid: int,
                    eigene: dict[str, tuple[int, int]] | None = None) -> tuple[int, int]:
    """Schreibt name im Ordner (temp, fsync, rename), Modus 0640, Gruppe wamo-core. Liefert (st_dev, st_ino).
    Trägt den Inode in eigene ein, BEVOR umbenannt wird: trifft ein Signal zwischen rename und Rückkehr, findet das
    Aufräumen die Datei trotzdem."""
    tmp = f".{name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(tmp, flags, 0o640, dir_fd=dir_fd)
    try:
        try:
            os.fchmod(fd, 0o640)
            daten = inhalt.encode("ascii")
            if os.write(fd, daten) != len(daten):
                raise OSError("Schreiben unvollständig")
            os.fsync(fd)
            s = os.fstat(fd)
            if s.st_gid != kern_gid:
                raise OSError("Datei hat nicht die Gruppe wamo-core")
        finally:
            os.close(fd)
        if eigene is not None:
            eigene[name] = (s.st_dev, s.st_ino)
        os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    return (s.st_dev, s.st_ino)


def loesche_eigene(dir_fd: int, eigene: dict[str, tuple[int, int]]) -> None:
    """Löscht jede Datei nur, wenn der Pfad noch genau die eigene Datei ist."""
    for name, kennung in eigene.items():
        try:
            s = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError:
            continue
        if (s.st_dev, s.st_ino) == kennung and stat.S_ISREG(s.st_mode):
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                pass


def eigene_vorfahren(pid: int, proc: str = "/proc") -> set[int]:
    """Prozess selbst und alle Vorfahren (damit die eigene Sitzung nie als "andere" gilt)."""
    menge = set()
    while pid > 1 and pid not in menge:
        menge.add(pid)
        try:
            with open(f"{proc}/{pid}/stat", "rb") as f:
                rest = f.read().rsplit(b")", 1)[1].split()
            pid = int(rest[1])
        except (OSError, ValueError, IndexError):
            break
    return menge


def andere_sitzungen(benutzer: str, uid: int, proc: str = "/proc", ausser: set[int] | None = None) -> list[int]:
    """PIDs anderer sshd-Sitzungen desselben Benutzers ("sshd: wamo-t-NN@notty"), ohne die eigene Prozesskette."""
    ausser = ausser or set()
    pids = []
    try:
        eintraege = os.listdir(proc)
    except OSError:
        return pids
    for name in eintraege:
        if not name.isdigit() or int(name) in ausser:
            continue
        try:
            if os.stat(f"{proc}/{name}").st_uid != uid:
                continue
            with open(f"{proc}/{name}/cmdline", "rb") as f:
                kommando = f.read(256).split(b"\0", 1)[0].decode("ascii", "replace")
        except OSError:
            continue
        if kommando.startswith(f"sshd: {benutzer}@") or kommando == f"sshd: {benutzer}":
            pids.append(int(name))
    return pids


def beende_alte_sitzungen(pids: list[int], frist_secs: float = ALTE_SITZUNG_FRIST_SECS, schlaf=time.sleep) -> bool:
    """SIGTERM an die alten Sitzungen, kurz warten, den Rest mit SIGKILL. True, wenn es welche gab."""
    if not pids:
        return False
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    ende = time.monotonic() + frist_secs
    while time.monotonic() < ende:
        if not any(os.path.exists(f"/proc/{p}") for p in pids):
            return True
        schlaf(0.1)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return True


def warte_auf_ende(fd: int) -> None:
    """Blockiert bis EOF auf stdin; weitere Eingaben werden verworfen."""
    while True:
        try:
            if not os.read(fd, 4096):
                return
        except InterruptedError:
            continue
        except OSError:
            return


def haupt(stdin_fd: int = 0, umgebung: dict | None = None, wurzel: str = WURZEL) -> int:
    umgebung = os.environ if umgebung is None else umgebung
    uid = os.getuid()
    try:
        benutzer = pwd.getpwuid(uid).pw_name
        ordner = platz_ordner(benutzer)
        kern_gid = grp.getgrnam(KERN_GRUPPE).gr_gid
    except (KeyError, Abbruch) as e:
        print(e.text if isinstance(e, Abbruch) else "Umgebung ungültig", file=sys.stderr)
        return EXIT_UMGEBUNG
    try:
        zeilen = lies_zeilen(stdin_fd)
        token, freigabe = pruefe_geheimnisse(zeilen)
        fingerabdruck = fingerabdruck_aus_auth(umgebung.get("SSH_USER_AUTH"))
        dir_fd = oeffne_geraeteordner(ordner, uid, kern_gid, wurzel)
    except Abbruch as e:
        print(e.text, file=sys.stderr)
        return e.code

    eigene: dict[str, tuple[int, int]] = {}

    def aufraeumen(*_):
        loesche_eigene(dir_fd, eigene)

    def beende(signum, _frame):
        # nach dem ersten Signal weitere ignorieren: das Aufräumen im finally darf nicht unterbrochen werden
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, beende)
    signal.signal(signal.SIGHUP, beende)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    try:
        alte = andere_sitzungen(benutzer, uid, ausser=eigene_vorfahren(os.getpid()))
        if beende_alte_sitzungen(alte):
            print("Alte Verbindung beendet, bitte neu verbinden", file=sys.stderr)
            return EXIT_NEU_VERBINDEN
        werte = {"schluessel": fingerabdruck + "\n", "host-freigabe": freigabe + "\n", "host-token": token + "\n"}
        for name in DATEIEN:
            eigene[name] = schreibe_atomar(dir_fd, name, werte[name], kern_gid, eigene)
        warte_auf_ende(stdin_fd)
        return 0
    except SystemExit:
        return 0
    except OSError:
        print("Schreiben nicht möglich", file=sys.stderr)
        return EXIT_UMGEBUNG
    finally:
        aufraeumen()
        os.close(dir_fd)


if __name__ == "__main__":
    sys.exit(haupt())
