"""Die Umschlag-Datei der Laborprofile für scripts/wamo_labor_faktoren.py (nur Standardbibliothek).

Aufbau der Datei (so schreibt sie scripts/wamo_labor.py anlegen): eine Zeile je Profil, Spalten durch Tabulator getrennt:
  Kennung, E-Mail, Passwort und, nach der Einrichtung des zweiten Faktors, als vierte Spalte der TOTP-Seed (Base32).
Alle anderen Zeilen (Kommentare, Leerzeilen) und alle weiteren Spalten bleiben unberührt, ebenso die Zeilenenden (LF oder CRLF).

Die Datei wird nur gelesen und geschrieben, nie angezeigt. Fehlertexte nennen nie ein Passwort und nie einen Seed. Vor dem ersten
Schreiben eines Laufs entsteht eine Sicherung mit Zeitstempel im selben Ordner. Geschrieben wird atomar: in eine Wegwerf-Datei im
selben Ordner (Rechte werden gesetzt, solange sie noch leer ist), dann ersetzen. Die Rechte erlauben nur dem Benutzer den Zugriff:
unter Windows mit icacls, sonst 0600.

Zwischendatei: Gleich nach der Bestätigung eines neuen Faktors steht sein Seed in <Datei>.neu (ein Eintrag, Kennung, Faktorkennung und
Seed, gleiche Rechte, atomar). Sie verschwindet nach dem Schreiben in die Umschlag-Datei. Bricht ein Lauf hart ab, übernimmt der nächste
den Seed aus ihr, so geht er nie verloren.
"""
from __future__ import annotations

import contextlib
import csv
import io
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

KENNUNG_RE = re.compile(r"labor-(0[1-9]|1[0-6])")
DATEI_NAME = "umschlag-labor.txt"
ORDNER_NAME = ".wamo-labor"
SEED_SPALTE = 3  # Kennung, E-Mail, Passwort, Seed
ZWISCHEN_ENDUNG = ".neu"
_FAKTOR_FORM = re.compile(r"[A-Za-z0-9-]{8,64}")
_SEED_ZEICHEN = re.compile(r"[A-Z2-7=]{16,}")
_BINAER = getattr(os, "O_BINARY", 0)
_SID_FORM = re.compile(r"S-\d-\d+(-\d+)+")


class UmschlagFehler(Exception):
    """Fehler mit einem Text für die Person, ohne Passwörter und ohne Seeds."""


@dataclass(frozen=True)
class Zugang:
    kennung: str
    email: str
    passwort: str = field(repr=False)
    seed: str = field(default="", repr=False)


@dataclass(frozen=True)
class Zwischen:
    """Der Eintrag der Zwischendatei: ein neu bestätigter Faktor und sein Seed."""
    kennung: str
    faktor: str
    seed: str = field(repr=False)


def standard_pfad(heim: Path | None = None) -> Path:
    return (heim if heim is not None else Path.home()) / ORDNER_NAME / DATEI_NAME


def pfad_pruefen(pfad: str | Path, wurzel: Path) -> Path:
    """Die Datei muss existieren und darf nie im Repo liegen (Passwörter gehören nie in ein Repo)."""
    datei = Path(pfad).expanduser().resolve()
    wurzel = wurzel.resolve()
    if datei == wurzel or wurzel in datei.parents:
        raise UmschlagFehler("Die Umschlag-Datei darf nicht im Repo liegen (Passwörter gehören nie in ein Repo)")
    if not datei.is_file():
        raise UmschlagFehler(f"Die Umschlag-Datei fehlt: {datei}")
    return datei


def _system32(name: str) -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", name)


@lru_cache(maxsize=1)
def _benutzer_sid() -> str:
    """Die SID des angemeldeten Benutzers (whoami), damit die Rechte nicht von Namen und Sprache abhängen."""
    try:
        r = subprocess.run([_system32("whoami.exe"), "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=30)
        zeile = next(csv.reader(io.StringIO(r.stdout)), [])
    except (OSError, subprocess.SubprocessError, csv.Error):
        zeile = []
    sid = zeile[1] if len(zeile) > 1 else ""
    if not _SID_FORM.fullmatch(sid):
        raise UmschlagFehler("Der Benutzer konnte nicht bestimmt werden (whoami), die Rechte der Datei wurden nicht gesetzt")
    return sid


def rechte_nur_benutzer(pfad: Path) -> None:
    """Nur der Benutzer darf die Datei lesen und schreiben: Windows icacls ohne Vererbung, sonst 0600."""
    try:
        if os.name == "nt":
            r = subprocess.run([_system32("icacls.exe"), str(pfad), "/inheritance:r", "/grant:r", f"*{_benutzer_sid()}:F"],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise UmschlagFehler("Die Rechte der Datei konnten nicht gesetzt werden (icacls)")
        else:
            os.chmod(pfad, 0o600)
    except (OSError, subprocess.SubprocessError):
        raise UmschlagFehler("Die Rechte der Datei konnten nicht gesetzt werden") from None


def _felder(zeile: str, nummer: int) -> list[str]:
    felder = zeile.rstrip("\r").split("\t")
    if nummer == 0:
        felder[0] = felder[0].lstrip("\ufeff")  # eine Byte-Order-Marke gehört nicht zur Kennung
    return felder


def _mit_seed(zeile: str, seed: str | None) -> str:
    """Die Zeile mit neuem Seed in der vierten Spalte (None entfernt ihn). Zeilenende und weitere Spalten bleiben."""
    cr = "\r" if zeile.endswith("\r") else ""
    felder = zeile[:len(zeile) - len(cr)].split("\t")
    if seed is None:
        if len(felder) == SEED_SPALTE + 1:
            del felder[SEED_SPALTE]
        elif len(felder) > SEED_SPALTE + 1:
            felder[SEED_SPALTE] = ""
    else:
        felder[SEED_SPALTE:SEED_SPALTE + 1] = [seed]
    return "\t".join(felder) + cr


class Umschlag:
    """Liest Zugänge aus der Umschlag-Datei und trägt Seeds ein. Jeder Schreibvorgang liest die Datei neu und ändert nur eine Zeile."""

    def __init__(self, pfad: Path, rechte: Callable[[Path], None] = rechte_nur_benutzer, jetzt: Callable[[], float] = time.time):
        self.pfad = Path(pfad)
        self._rechte = rechte
        self._jetzt = jetzt
        self._gesichert = False

    # ---- lesen ----

    def _text(self) -> str:
        try:
            roh = self.pfad.read_bytes()
        except OSError:
            raise UmschlagFehler("Die Umschlag-Datei ist nicht lesbar") from None
        try:
            return roh.decode("utf-8")
        except UnicodeDecodeError:
            raise UmschlagFehler("Die Umschlag-Datei ist kein gültiges UTF-8") from None

    def _zeile_finden(self, zeilen: list[str], kennung: str) -> int:
        treffer = [i for i, z in enumerate(zeilen) if _felder(z, i)[0] == kennung]
        if not treffer:
            raise UmschlagFehler(f"Für {kennung} gibt es keine Zeile in der Umschlag-Datei")
        if len(treffer) > 1:
            raise UmschlagFehler(f"Für {kennung} gibt es mehrere Zeilen in der Umschlag-Datei, eine davon muss entfernt werden")
        return treffer[0]

    def zugang(self, kennung: str) -> Zugang:
        zeilen = self._text().split("\n")
        nummer = self._zeile_finden(zeilen, kennung)
        felder = _felder(zeilen[nummer], nummer)
        if len(felder) < 3 or not felder[1].strip() or not felder[2]:
            raise UmschlagFehler(f"Die Zeile für {kennung} ist unvollständig (E-Mail oder Passwort fehlt)")
        return Zugang(kennung, felder[1].strip(), felder[2], felder[3].strip() if len(felder) > SEED_SPALTE else "")

    # ---- schreiben ----

    def seed_speichern(self, kennung: str, seed: str) -> None:
        self._seed_setzen(kennung, seed)

    def seed_entfernen(self, kennung: str) -> None:
        self._seed_setzen(kennung, None)

    def _seed_setzen(self, kennung: str, seed: str | None) -> None:
        zeilen = self._text().split("\n")
        nummer = self._zeile_finden(zeilen, kennung)
        if len(_felder(zeilen[nummer], nummer)) < 3:
            raise UmschlagFehler(f"Die Zeile für {kennung} ist unvollständig (E-Mail oder Passwort fehlt)")
        zeilen[nummer] = _mit_seed(zeilen[nummer], seed)
        self._sichern()
        self._atomar("\n".join(zeilen).encode("utf-8"))

    def _neu_anlegen(self, ziel: Path, daten: bytes) -> None:
        """Legt ziel exklusiv an, setzt die Rechte, solange es leer ist, und schreibt erst dann den Inhalt."""
        os.close(os.open(ziel, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINAER, 0o600))  # FileExistsError: die Datei gehört nicht uns
        try:
            self._rechte(ziel)
            with open(ziel, "wb") as f:
                f.write(daten)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                ziel.unlink(missing_ok=True)
            raise

    def _sichern(self) -> None:
        """Eine Sicherung je Lauf, vor dem ersten Schreiben: <Datei>.sicherung-JJJJMMTT-HHMMSS im selben Ordner."""
        if self._gesichert:
            return
        try:
            original = self.pfad.read_bytes()
            stempel = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._jetzt()))
            for n in range(1, 1000):
                ziel = self.pfad.with_name(f"{self.pfad.name}.sicherung-{stempel}" + ("" if n == 1 else f"-{n}"))
                try:
                    self._neu_anlegen(ziel, original)
                except FileExistsError:
                    continue
                self._gesichert = True
                return
        except OSError:
            raise UmschlagFehler("Die Sicherung der Umschlag-Datei konnte nicht angelegt werden") from None
        raise UmschlagFehler("Die Sicherung der Umschlag-Datei konnte nicht angelegt werden")

    def _wegwerf(self, art: str) -> Path:
        return self.pfad.with_name(f".{self.pfad.name}.{art}-{secrets.token_hex(4)}")

    def _atomar(self, daten: bytes, ziel: Path | None = None) -> None:
        temporaer = self._wegwerf("tmp")
        try:
            self._neu_anlegen(temporaer, daten)
            os.replace(temporaer, ziel or self.pfad)
        except OSError as e:
            raise UmschlagFehler(f"Die Umschlag-Datei konnte nicht geschrieben werden ({type(e).__name__})") from None
        finally:
            with contextlib.suppress(OSError):
                temporaer.unlink(missing_ok=True)

    def schreibprobe(self) -> None:
        """Prüft vor dem ersten Eingriff am Server: Im Ordner lässt sich schreiben, und die Rechte lassen sich setzen."""
        probe = self._wegwerf("probe")
        try:
            self._neu_anlegen(probe, b"")
        except OSError:
            raise UmschlagFehler("Im Ordner der Umschlag-Datei kann nicht geschrieben werden") from None
        finally:
            with contextlib.suppress(OSError):
                probe.unlink(missing_ok=True)

    # ---- Zwischendatei ----

    @property
    def zwischen_pfad(self) -> Path:
        return self.pfad.with_name(self.pfad.name + ZWISCHEN_ENDUNG)

    def zwischen_schreiben(self, kennung: str, faktor_id: str, seed: str) -> None:
        """Hält den Seed eines gerade bestätigten Faktors fest, bis er in der Umschlag-Datei steht."""
        if not KENNUNG_RE.fullmatch(kennung) or not _FAKTOR_FORM.fullmatch(faktor_id) or not _SEED_ZEICHEN.fullmatch(seed):
            raise UmschlagFehler("Die Zwischendatei konnte nicht geschrieben werden (ungültige Angaben)")
        self._atomar(f"{kennung}\t{faktor_id}\t{seed}\n".encode("utf-8"), self.zwischen_pfad)

    def zwischen_lesen(self) -> Zwischen | None:
        try:
            roh = self.zwischen_pfad.read_bytes().decode("utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError):
            raise UmschlagFehler(f"Die Zwischendatei {self.zwischen_pfad.name} ist nicht lesbar, bitte prüfen") from None
        felder = roh.rstrip("\r\n").split("\t")
        if (len(felder) != 3 or not KENNUNG_RE.fullmatch(felder[0]) or not _FAKTOR_FORM.fullmatch(felder[1])
                or not _SEED_ZEICHEN.fullmatch(felder[2])):
            raise UmschlagFehler(f"Die Zwischendatei {self.zwischen_pfad.name} ist ungültig (ein früherer Lauf wurde abgebrochen), "
                                 "bitte prüfen")
        return Zwischen(*felder)

    def zwischen_loeschen(self) -> None:
        try:
            self.zwischen_pfad.unlink(missing_ok=True)
        except OSError:
            raise UmschlagFehler(f"Die Zwischendatei {self.zwischen_pfad.name} konnte nicht gelöscht werden") from None
