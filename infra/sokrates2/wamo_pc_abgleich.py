#!/usr/bin/python3 -I
"""Abgleich der Rücktunnel-Plätze auf Sokrates-2: /opt/wamo-pc/bin/wamo-pc-abgleich (läuft als root, alle 10 s).

Quelle der Wahrheit ist die Datenbank (Tabelle devices, Funktion <schema>.pc_abgleich_liste(): nur aktive Geräte
aktiver Profile). Dieses Programm macht daraus das System:

  je Platz NN (01 bis 64)  Benutzer wamo-t-NN (System, Shell /opt/wamo-pc/bin/wamo-pc-token, keine Zusatzgruppen,
                           Passwort gesperrt), /etc/ssh/authorized_keys.d/wamo-t-NN, Zeile in /etc/tmpfiles.d/wamo-pc.conf
                           (Ordner /run/wamo-pc/tNN, 2750 wamo-t-NN:wamo-core)
  nicht mehr gelistet      Schlüsseldatei nach /etc/ssh/authorized_keys.d/gesperrt/, laufende Prozesse des Benutzers
                           beenden, Ordnerinhalt löschen, Ordner und tmpfiles-Zeile entfernen. Benutzer werden nie
                           gelöscht (keine uid-Wiederverwendung), sie bleiben gesperrt.

Regeln
  * Datenbank nicht erreichbar oder Antwort kaputt: nichts ändern (fail static), Meldung ins Journal, Exit 0, damit der
    Zeitgeber weiterläuft. Eine leere Liste ist eine gültige Antwort und sperrt alles.
  * Jede Zeile wird neu geprüft (Platz, Kennung, Schlüssel-Regex, Fingerabdruck gegen ssh-keygen).
  * Befehle nur mit Argumentlisten, nie über eine Shell. Werte aus der Datenbank gehen nie in Befehle außer als
    geprüfte Argumente. Schlüssel und Geheimnisse stehen nie im Journal (nur Plätze, Kennungen, Fingerabdrücke).
  * Sperre über flock gegen parallele Läufe.
  * --probe nutzt eigene Namen (wamo-t-pa, wamo-t-pb) und eine eigene tmpfiles-Datei: der Rauchtest berührt die echten
    Plätze nie.
  * Sperren (Widerruf) kommt vor dem Anlegen und hängt weder an tmpfiles noch an Schlüsseldateien: ein Fehler dort
    verhindert nie einen Widerruf. Fehler beim Anlegen enden mit Exit 1 (Zeitgeber läuft weiter).
  * Schweigt die Datenbank länger als LISTE_ALT_SECS, kommt "WAMO-GERAET ALARM" und Exit 1 (ein dauerhaft falsches Schema
    oder eine tote Datenbank fällt so auf).
  * --streuner: meldet lauschende Unix-Sockets der Tunnelbenutzer außerhalb ihres eigenen Ordners unter /run/wamo-pc
    (aus /proc/net/unix und den Deskriptoren der Prozesse "sshd: wamo-t-NN@..."; unabhängig vom Ort) und beendet
    deren Sitzungen. Eigene Sperrdatei, damit er den Abgleich nie aufhält.
Journal-Präfix: WAMO-GERAET. Namen und Pfade aus fremder Hand gehen nur als repr() ins Journal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
    import pwd
except ImportError:  # Windows: nur damit die Tests die reinen Funktionen laden können
    fcntl = pwd = None

KONFIG = "/etc/wamo/pc-abgleich.conf"
SCHEMA_RE = re.compile(r"wamo_(dev|test|prod)")
CONTAINER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
KENNUNG_RE = re.compile(r"pc-[a-z0-9]+(?:-[a-z0-9]+)*")
PUBKEY_RE = re.compile(r"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-P][A-Za-z0-9+/]{42}")
FINGER_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
PROBE_RE = re.compile(r"p[a-z]")
FROM_RE = re.compile(r"[0-9./,:a-fA-F]+")
BENUTZER_PRAEFIX = "wamo-t-"
SCHLUESSEL_DIR = Path("/etc/ssh/authorized_keys.d")
GESPERRT_DIR = SCHLUESSEL_DIR / "gesperrt"
TMPFILES_DIR = Path("/etc/tmpfiles.d")
WURZEL = Path("/run/wamo-pc")
SHELL = "/opt/wamo-pc/bin/wamo-pc-token"
KERN_GRUPPE = "wamo-core"
FROM_STANDARD = "192.168.178.0/24"
SPERRDATEI = "/run/wamo-pc-abgleich.lock"
STREUNER_SPERRDATEI = "/run/wamo-pc-streuner.lock"
MARKE_LISTE_OK = "/run/wamo-pc-liste-ok"
LISTE_ALT_SECS = 300  # so lange darf die Datenbank schweigen, bevor ein Alarm (Exit 1) kommt
PROBE_WARTE_SECS = 30.0
DB_FRIST_SECS = 20
SHADOW = "/etc/shadow"


def melde(text: str) -> None:
    print(f"WAMO-GERAET {text}", flush=True)


@dataclass(frozen=True)
class Platz:
    """Ein Tunnelplatz. name: "05" (echt) oder "pa" (Probe); Ordner t05 bzw. pa."""

    name: str
    kennung: str
    pubkey: str
    fingerabdruck: str
    probe: bool = False

    @property
    def benutzer(self) -> str:
        return BENUTZER_PRAEFIX + self.name

    @property
    def ordner(self) -> str:
        return self.name if self.probe else "t" + self.name


def platz_namen_re(probe: bool) -> re.Pattern:
    return PROBE_RE if probe else re.compile(r"0[1-9]|[1-5][0-9]|6[0-4]")


def pruefe_zeile(slot: object, kennung: str, pubkey: str, fingerabdruck: str, probe: bool, fp_berechnen) -> Platz:
    """Eine Zeile der Liste; wirft ValueError mit kurzer Begründung (ohne Werte)."""
    if probe:
        name = str(slot)
    else:
        try:
            nummer = int(slot)
        except (TypeError, ValueError):
            raise ValueError("Platz ist keine Zahl") from None
        if not 1 <= nummer <= 64:
            raise ValueError("Platz außerhalb 1 bis 64")
        name = f"{nummer:02d}"
    if not platz_namen_re(probe).fullmatch(name):
        raise ValueError("Platzname ungültig")
    if not KENNUNG_RE.fullmatch(kennung) or len(kennung) > 23:
        raise ValueError("Kennung ungültig")
    if not PUBKEY_RE.fullmatch(pubkey):
        raise ValueError("Schlüssel ungültig")
    if not FINGER_RE.fullmatch(fingerabdruck):
        raise ValueError("Fingerabdruck ungültig")
    if fp_berechnen(pubkey) != fingerabdruck:
        raise ValueError("Fingerabdruck passt nicht zum Schlüssel")
    return Platz(name, kennung, pubkey, fingerabdruck, probe)


def parse_db_ausgabe(text: str) -> list[tuple[str, str, str, str]]:
    zeilen = []
    for zeile in text.splitlines():
        if not zeile.strip():
            continue
        teile = zeile.split("|")
        if len(teile) != 4:
            raise ValueError("Zeile der Datenbank hat nicht vier Spalten")
        zeilen.append((teile[0], teile[1], teile[2], teile[3]))
    return zeilen


class System:
    """Alles, was das Betriebssystem ändert. Tests ersetzen diese Klasse."""

    def __init__(self, probe: bool, wurzel: Path = WURZEL, schluessel_dir: Path = SCHLUESSEL_DIR,
                 tmpfiles_dir: Path = TMPFILES_DIR, von: str = FROM_STANDARD):
        self.probe = probe
        self.wurzel = wurzel
        self.schluessel_dir = schluessel_dir
        self.gesperrt_dir = schluessel_dir / "gesperrt"
        self.tmpfiles_datei = tmpfiles_dir / ("wamo-pc-probe.conf" if probe else "wamo-pc.conf")
        self.von = von

    # ---- Befehle ----
    def lauf(self, argv: list[str], eingabe: str | None = None, frist: int = 15) -> subprocess.CompletedProcess:
        """Befehl ohne Shell. Eine Zeitüberschreitung (hängendes ssh-keygen, useradd, ...) wird ein Fehlschlag mit Code 124,
        kein Absturz: ein Hänger bei einem Platz darf weder die anderen Plätze noch einen Widerruf aufhalten."""
        try:
            return subprocess.run(argv, input=eingabe, capture_output=True, text=True, timeout=frist, check=False)
        except subprocess.TimeoutExpired:
            melde(f"Befehl {os.path.basename(argv[0])} nach {frist} s abgebrochen")
            return subprocess.CompletedProcess(argv, 124, "", "")

    def fingerabdruck(self, pubkey: str) -> str:
        r = self.lauf(["/usr/bin/ssh-keygen", "-l", "-f", "/dev/stdin"], eingabe=pubkey + "\n")
        teile = r.stdout.split()
        if r.returncode != 0 or len(teile) < 2:
            return ""
        return teile[1]

    # ---- Benutzer ----
    def vorhandene(self) -> set[str]:
        """Namen (05, pa) aller bereits angelegten Tunnelbenutzer dieser Art."""
        muster = platz_namen_re(self.probe)
        namen = set()
        for eintrag in pwd.getpwall():
            if eintrag.pw_name.startswith(BENUTZER_PRAEFIX):
                name = eintrag.pw_name[len(BENUTZER_PRAEFIX):]
                if muster.fullmatch(name):
                    namen.add(name)
        return namen

    def benutzer_anlegen(self, platz: Platz) -> bool:
        """Legt den Benutzer an, falls er fehlt. True, wenn neu angelegt. Shell, Sperre und Gruppen werden nur
        geschrieben, wenn sie abweichen (der Abgleich läuft alle 10 s; usermod und passwd schreiben /etc neu)."""
        try:
            eintrag = pwd.getpwnam(platz.benutzer)
        except KeyError:
            eintrag = None
        neu = eintrag is None
        if neu:
            r = self.lauf(["/usr/sbin/useradd", "--system", "--user-group", "--home-dir", "/nonexistent",
                           "--no-create-home", "--shell", SHELL, "--comment", f"WAMO Tunnel {platz.kennung}",
                           platz.benutzer])
            if r.returncode != 0:
                raise RuntimeError(f"useradd {platz.benutzer} fehlgeschlagen")
            eintrag = pwd.getpwnam(platz.benutzer)
        if eintrag.pw_shell != SHELL:
            self.lauf(["/usr/sbin/usermod", "-s", SHELL, platz.benutzer])
        if not self.passwort_gesperrt(platz.benutzer):
            self.lauf(["/usr/bin/passwd", "-l", platz.benutzer])
        if set(self.gruppen(platz.benutzer, eintrag.pw_gid)) != {eintrag.pw_gid}:
            raise RuntimeError(f"{platz.benutzer} hat weitere Gruppen")
        return neu

    def passwort_gesperrt(self, benutzer: str) -> bool:
        """Passwortfeld in /etc/shadow beginnt mit ! oder * (kein Passwort-Login möglich)."""
        try:
            with open(SHADOW, encoding="utf-8", errors="replace") as f:
                for zeile in f:
                    teile = zeile.split(":")
                    if teile[0] == benutzer:
                        return len(teile) > 1 and teile[1][:1] in ("!", "*")
        except OSError:
            pass
        return False

    def gruppen(self, benutzer: str, gid: int) -> list[int]:
        return os.getgrouplist(benutzer, gid)

    def prozesse_beenden(self, benutzer: str) -> None:
        """Alle Prozesse genau dieses Benutzers beenden (SIGTERM, nach 2 s SIGKILL)."""
        if not benutzer.startswith(BENUTZER_PRAEFIX):
            raise ValueError("kein Tunnelbenutzer")
        self.lauf(["/usr/bin/pkill", "-u", benutzer])
        for _ in range(20):
            if self.lauf(["/usr/bin/pgrep", "-u", benutzer]).returncode != 0:
                return
            time.sleep(0.1)
        self.lauf(["/usr/bin/pkill", "-KILL", "-u", benutzer])

    # ---- Dateien ----
    def schluesselzeile(self, platz: Platz) -> str:
        return f'restrict,port-forwarding,from="{self.von}" {platz.pubkey} wamo-geraet-{platz.kennung}\n'

    def schluessel_schreiben(self, platz: Platz) -> str:
        """Schlüsseldatei atomar schreiben (root 0644). Liefert "" (unverändert), "neu" oder "geaendert" (der Platz gehörte
        vorher einem anderen Schlüssel, zum Beispiel nach einem Widerruf und einer Neuvergabe des Platzes)."""
        ziel = self.schluessel_dir / platz.benutzer
        soll = self.schluesselzeile(platz)
        vorher = None
        try:
            vorher = ziel.read_text(encoding="utf-8")
            if vorher == soll:
                return ""
        except OSError:
            pass
        self.schluessel_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        atomar_schreiben(ziel, soll, 0o644)
        return "geaendert" if vorher is not None else "neu"

    def schluessel_sperren(self, benutzer: str) -> bool:
        """Schlüsseldatei nach gesperrt/ verschieben. True, wenn es eine gab."""
        quelle = self.schluessel_dir / benutzer
        if not quelle.exists():
            return False
        self.gesperrt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.gesperrt_dir, 0o700)
        os.replace(quelle, self.gesperrt_dir / f"{benutzer}.{int(time.time())}")
        return True

    def tmpfiles_schreiben(self, plaetze: list[Platz]) -> bool:
        zeilen = ["# WAMO-Tunnel: Ordner je Platz (geschrieben von wamo-pc-abgleich, nicht von Hand ändern)"]
        for p in sorted(plaetze, key=lambda x: x.name):
            zeilen.append(f"d {self.wurzel}/{p.ordner} 2750 {p.benutzer} {KERN_GRUPPE} -")
        soll = "\n".join(zeilen) + "\n"
        try:
            if self.tmpfiles_datei.read_text(encoding="utf-8") == soll:
                return False
        except OSError:
            pass
        atomar_schreiben(self.tmpfiles_datei, soll, 0o644)
        return True

    def tmpfiles_anwenden(self) -> None:
        # Wurzelordner (wamo-pc-wurzel.conf) und Plätze zusammen: so stehen Eltern vor den Kindern
        argv = ["/usr/bin/systemd-tmpfiles", "--create"]
        wurzel_conf = self.tmpfiles_datei.parent / "wamo-pc-wurzel.conf"
        if wurzel_conf.exists():
            argv.append(str(wurzel_conf))
        r = self.lauf(argv + [str(self.tmpfiles_datei)])
        if r.returncode != 0:
            raise RuntimeError("systemd-tmpfiles --create fehlgeschlagen")

    def ordner_leeren(self, ordner: str, behalten: bool = False) -> None:
        """Inhalt und Ordner entfernen (keine Verzeichnisse darin, keine Links verfolgen); mit behalten bleibt der Ordner."""
        pfad = self.wurzel / ordner
        try:
            eintraege = list(os.scandir(pfad))
        except OSError:
            return
        for e in eintraege:
            if e.is_dir(follow_symlinks=False):
                melde(f"Ordner {ordner!r}: Unterordner {e.name!r} bleibt (unerwartet)")
                continue
            try:
                os.unlink(e.path)
            except OSError:
                pass
        if behalten:
            return
        try:
            os.rmdir(pfad)
        except OSError:
            pass


def atomar_schreiben(ziel: Path, inhalt: str, modus: int) -> None:
    """Temp-Datei im selben Ordner, fsync, rename. Besitzer root."""
    fd, tmp = tempfile.mkstemp(dir=str(ziel.parent), prefix=f".{ziel.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(inhalt)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, modus)
        os.replace(tmp, ziel)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def abgleich(zeilen: list[tuple[str, str, str, str]], system: System) -> dict:
    """Bringt das System auf den Stand der Liste. Liefert eine Zusammenfassung (für Journal und Tests).
    Reihenfolge: prüfen, zuerst sperren (Widerruf hängt an nichts anderem), dann anlegen und Schlüssel schreiben."""
    ergebnis = {"bereit": [], "angelegt": [], "gesperrt": [], "uebersprungen": 0, "fehler": 0}
    plaetze: list[Platz] = []
    gesehen: set[str] = set()
    for slot, kennung, pubkey, fp in zeilen:
        try:
            platz = pruefe_zeile(slot, kennung, pubkey, fp, system.probe, system.fingerabdruck)
        except ValueError as e:
            melde(f"Zeile übersprungen ({e})")
            ergebnis["uebersprungen"] += 1
            continue
        if platz.name in gesehen:
            melde(f"Platz {platz.name} doppelt in der Liste, zweite Zeile übersprungen")
            ergebnis["uebersprungen"] += 1
            continue
        gesehen.add(platz.name)
        plaetze.append(platz)

    # 1. nicht mehr gelistete Plätze sperren (Schlüssel weg, Sitzungen beenden, Ordnerinhalt löschen)
    soll_namen = {p.name for p in plaetze}
    for name in sorted(system.vorhandene() - soll_namen):
        benutzer = BENUTZER_PRAEFIX + name
        ordner = name if system.probe else "t" + name
        try:
            gesperrt = system.schluessel_sperren(benutzer)
            system.prozesse_beenden(benutzer)
            system.ordner_leeren(ordner)
        except OSError as e:
            melde(f"Platz {name} nicht vollständig gesperrt ({type(e).__name__})")
            ergebnis["fehler"] += 1
            continue
        if gesperrt or os.path.exists(system.wurzel / ordner):
            melde(f"Platz {name} gesperrt")
        ergebnis["gesperrt"].append(name)

    # 2. neue Plätze: Benutzer, Ordner, dann erst der Schlüssel (ohne Ordner nützt er nichts)
    bereit: list[Platz] = []
    for platz in plaetze:
        try:
            if system.benutzer_anlegen(platz):
                ergebnis["angelegt"].append(platz.name)
                melde(f"Platz {platz.name} angelegt ({platz.kennung})")
            bereit.append(platz)
        except (RuntimeError, ValueError, OSError) as e:
            melde(f"Platz {platz.name} nicht bereit ({e})")
            ergebnis["fehler"] += 1
    try:
        system.tmpfiles_schreiben(bereit)
        system.tmpfiles_anwenden()
    except (RuntimeError, OSError) as e:
        melde(f"Ordner der Plätze nicht angelegt ({e})")
        ergebnis["fehler"] += 1
        return ergebnis
    for platz in bereit:
        try:
            geschrieben = system.schluessel_schreiben(platz)
            if geschrieben == "geaendert":
                # Der Platz hat einen anderen Schlüssel als zuvor: Sitzungen des alten Schlüssels und die Dateien seines
                # Geräts (Token, Freigabe, Fingerabdruck) enden jetzt; der Kern lehnt sie ohnehin über den Fingerabdruck ab
                system.prozesse_beenden(platz.benutzer)
                system.ordner_leeren(platz.ordner, behalten=True)
            if geschrieben:
                melde(f"Schlüssel für Platz {platz.name} geschrieben ({platz.kennung}, {platz.fingerabdruck})")
            ergebnis["bereit"].append(platz.name)
        except OSError as e:
            melde(f"Schlüssel für Platz {platz.name} nicht geschrieben ({type(e).__name__})")
            ergebnis["fehler"] += 1
    return ergebnis


# ---------- Datenbank ----------
def lade_konfig(pfad: str) -> dict[str, str]:
    werte: dict[str, str] = {}
    with open(pfad, encoding="utf-8") as f:
        for zeile in f:
            zeile = zeile.strip()
            if not zeile or zeile.startswith("#") or "=" not in zeile:
                continue
            k, _, v = zeile.partition("=")
            werte[k.strip()] = v.strip()
    schema = werte.get("WAMO_DB_SCHEMA", "")
    container = werte.get("WAMO_DB_CONTAINER", "wamo-supabase-db")
    if not SCHEMA_RE.fullmatch(schema):
        raise ValueError("WAMO_DB_SCHEMA ungültig")
    if not CONTAINER_RE.fullmatch(container):
        raise ValueError("WAMO_DB_CONTAINER ungültig")
    von = werte.get("WAMO_PC_FROM", FROM_STANDARD)
    if not FROM_RE.fullmatch(von):
        raise ValueError("WAMO_PC_FROM ungültig")
    return {"schema": schema, "container": container, "von": von}


def liste_aus_datenbank(konfig: dict[str, str], lauf=subprocess.run) -> list[tuple[str, str, str, str]]:
    """Liste der aktiven Geräte. Wirft RuntimeError, wenn die Datenbank nicht antwortet."""
    sql = f"select tunnel_slot, kennung, ssh_pubkey, ssh_fingerprint from {konfig['schema']}.pc_abgleich_liste()"
    try:
        # PGOPTIONS gilt schon beim Verbindungsaufbau: die Sitzung ist schreibgeschützt (ein "set" im selben Text
        # würde die laufende Transaktion nicht mehr erfassen)
        r = lauf(["/usr/bin/docker", "exec", "-i", "-e", "PGOPTIONS=-c default_transaction_read_only=on",
                  konfig["container"], "psql", "-U", "postgres", "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", sql],
                 capture_output=True, text=True, timeout=DB_FRIST_SECS, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"Datenbank nicht erreichbar ({type(e).__name__})") from None
    if r.returncode != 0:
        raise RuntimeError(f"Datenbank meldet Fehler (Code {r.returncode})")
    # "set ..." gibt "SET" auf stdout aus; das erste Statement liefert eine Zeile "SET"
    text = "\n".join(z for z in r.stdout.splitlines() if z.strip() != "SET")
    try:
        return parse_db_ausgabe(text)
    except ValueError as e:
        raise RuntimeError(str(e)) from None


def liste_aus_datei(pfad: str) -> list[tuple[str, str, str, str]]:
    with open(pfad, encoding="utf-8") as f:
        daten = json.load(f)
    if not isinstance(daten, list):
        raise RuntimeError("Listendatei ist keine Liste")
    zeilen = []
    for e in daten:
        if not isinstance(e, dict):
            raise RuntimeError("Listendatei: Eintrag ist kein Objekt")
        zeilen.append((str(e.get("slot", e.get("name", ""))), str(e.get("kennung", "")), str(e.get("ssh_pubkey", "")),
                       str(e.get("ssh_fingerprint", ""))))
    return zeilen


# ---------- Streuner ----------
SOCKET_LAUSCHT = 0x10000  # Flags-Spalte in /proc/net/unix (SO_ACCEPTCON)
SSHD_SITZUNG_RE = re.compile(rf"sshd: ({re.escape(BENUTZER_PRAEFIX)}(?:0[1-9]|[1-5][0-9]|6[0-4]|p[a-z]))(?:@\S+)?")


def ordner_von(benutzer: str) -> str:
    """Geräteordner zum Benutzernamen: wamo-t-05 -> t05, Probe wamo-t-pa -> pa."""
    name = benutzer[len(BENUTZER_PRAEFIX):]
    return name if PROBE_RE.fullmatch(name) else "t" + name


def lauschende_sockets(proc: str = "/proc") -> dict[str, str]:
    """Inode -> Pfad aller lauschenden Unix-Sockets mit Dateipfad (abstrakte und namenlose bleiben außen vor)."""
    sockets: dict[str, str] = {}
    with open(f"{proc}/net/unix", encoding="utf-8", errors="replace") as f:
        next(f, None)
        for zeile in f:
            teile = zeile.split(None, 7)
            if len(teile) < 8:
                continue
            try:
                flags = int(teile[3], 16)
            except ValueError:
                continue
            if flags & SOCKET_LAUSCHT and teile[7].strip().startswith("/"):
                sockets[teile[6]] = teile[7].strip()
    return sockets


def finde_streuner(proc: str = "/proc", wurzel: Path = WURZEL) -> dict[str, list[str]]:
    """Benutzer -> Pfade lauschender Sockets, die ihre sshd-Sitzung außerhalb des eigenen Geräteordners hält.
    Gesucht wird nur in den Prozessen "sshd: wamo-t-NN@..." (nicht in systemd --user o. ä.): Das sind die Prozesse, die
    Rückweiterleitungen halten. Der Ort spielt keine Rolle (/tmp, /run/lock, /var/crash ...)."""
    sockets = lauschende_sockets(proc)
    if not sockets:
        return {}
    gefunden: dict[str, list[str]] = {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(f"{proc}/{name}/cmdline", "rb") as f:
                kopf = f.read(200).split(b"\0", 1)[0].decode("ascii", "replace")
            m = SSHD_SITZUNG_RE.fullmatch(kopf)
            if not m:
                continue
            benutzer = m.group(1)
            erlaubt = f"{wurzel}/{ordner_von(benutzer)}/"
            for fd in os.listdir(f"{proc}/{name}/fd"):
                try:
                    ziel = os.readlink(f"{proc}/{name}/fd/{fd}")
                except OSError:
                    continue
                if ziel.startswith("socket:[") and ziel.endswith("]"):
                    pfad = sockets.get(ziel[8:-1])
                    if pfad and not (pfad.startswith(erlaubt) and "/" not in pfad[len(erlaubt):]):
                        liste = gefunden.setdefault(benutzer, [])
                        if pfad not in liste:
                            liste.append(pfad)
        except OSError:
            continue
    return gefunden


def streuner(system: System, proc: str = "/proc") -> int:
    """Streuner melden und die Sitzungen des Benutzers beenden. Liefert die Zahl der betroffenen Benutzer."""
    gefunden = finde_streuner(proc, system.wurzel)
    for benutzer, pfade in sorted(gefunden.items()):
        for pfad in pfade:
            melde(f"Socket von {benutzer} außerhalb seines Ordners: {pfad!r}")
        system.prozesse_beenden(benutzer)
    return len(gefunden)


# ---------- Liste zu alt ----------
def liste_ok(marke: Path = Path(MARKE_LISTE_OK)) -> None:
    try:
        marke.touch()
    except OSError:
        pass


def liste_zu_alt(marke: Path = Path(MARKE_LISTE_OK), frist: int = LISTE_ALT_SECS, jetzt=time.time) -> bool:
    """True, wenn die Datenbank seit mehr als frist Sekunden keine gültige Liste geliefert hat. Ohne Marke beginnt
    die Zählung jetzt."""
    try:
        return jetzt() - marke.stat().st_mtime > frist
    except OSError:
        liste_ok(marke)
        return False


# ---------- Hauptprogramm ----------
def sperre_holen(pfad: str, warten_secs: float = 0.0):
    """flock auf pfad. Liefert den Deskriptor oder None, wenn die Sperre nach warten_secs nicht frei wurde."""
    fd = os.open(pfad, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    ende = time.monotonic() + warten_secs
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= ende:
                os.close(fd)
                return None
            time.sleep(0.2)


def haupt(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Abgleich der Rücktunnel-Plätze (siehe Modulkopf)")
    ap.add_argument("--konfig", default=KONFIG)
    ap.add_argument("--liste-datei", help="JSON-Liste statt Datenbank (Rauchtest)")
    ap.add_argument("--probe", action="store_true", help="Probe-Plätze wamo-t-pa/pb statt echter Plätze")
    ap.add_argument("--streuner", action="store_true", help="Sockets außerhalb des eigenen Ordners melden")
    ap.add_argument("--trocken", action="store_true", help="nur prüfen und anzeigen, nichts ändern")
    args = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("Bitte als root ausführen.", file=sys.stderr)
        return 1
    if args.liste_datei and not args.probe:
        print("--liste-datei nur zusammen mit --probe (die echten Plätze kommen aus der Datenbank).", file=sys.stderr)
        return 1
    try:
        konfig = lade_konfig(args.konfig)
    except (OSError, ValueError) as e:
        melde(f"Konfiguration unbrauchbar ({e})")
        return 1
    if args.streuner:  # eigene Sperre: ein langsamer Streuner hält nie einen Widerruf auf
        sperre = sperre_holen(STREUNER_SPERRDATEI)
        if sperre is None:
            return 0
        try:
            streuner(System(args.probe, von=konfig["von"]))
        except OSError as e:
            melde(f"Streuner-Prüfung fehlgeschlagen ({type(e).__name__})")
            return 1
        finally:
            os.close(sperre)
        return 0
    # Probe-Läufe (Rauchtest) warten auf die Sperre und scheitern laut; der Zeitgeber überspringt einen Lauf leise
    sperre = sperre_holen(SPERRDATEI, PROBE_WARTE_SECS if args.probe else 0.0)
    if sperre is None:
        if args.probe:
            melde("Sperre nicht frei geworden, Probe-Lauf abgebrochen")
            return 3
        melde("Ein anderer Lauf ist noch aktiv, übersprungen")
        return 0
    try:
        system = System(args.probe, von=konfig["von"])
        try:
            zeilen = liste_aus_datei(args.liste_datei) if args.liste_datei else liste_aus_datenbank(konfig)
        except (RuntimeError, OSError, ValueError) as e:
            melde(f"Liste nicht verfügbar, Zustand bleibt unverändert ({e})")
            if not args.probe and liste_zu_alt():
                melde(f"ALARM: seit mehr als {LISTE_ALT_SECS} s keine gültige Liste (Schema und Datenbank prüfen)")
                return 1
            return 0
        if not args.probe:
            liste_ok()
        if args.trocken:
            for slot, kennung, _pub, fp in zeilen:
                print(f"Platz {slot}: {kennung} {fp}")
            return 0
        ergebnis = abgleich(zeilen, system)
        return 1 if ergebnis["fehler"] else 0
    finally:
        os.close(sperre)


if __name__ == "__main__":
    sys.exit(haupt())
