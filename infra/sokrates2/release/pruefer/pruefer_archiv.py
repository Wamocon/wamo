"""Archivformat zwischen dem Release-Dienst (Client) und dem Annahme-Dienst der Prüfer-Sandbox (Root), Schritt 1.2c.

Der Annahme-Dienst läuft als root, der Release-Dienst nicht. Damit root nie Ordner liest, die ein Dienst ohne Root verändern kann (Links,
Wettläufe beim Austausch von Ordnern), geht der Inhalt der beiden Stände als Datenstrom über den Socket, und root schreibt ihn selbst in einen
frischen Ordner: jede Komponente mit O_NOFOLLOW über Verzeichnis-Deskriptoren, jede Datei mit O_EXCL. Dieses Modul ist der Schreiber (Client) und
der Leser (Dienst) zugleich, damit beide dasselbe Format meinen. Nur Standardbibliothek.

Format (Zeilen enden mit LF, danach folgen bei F genau <Größe> Bytes und ein LF):

    D <pfad>                  Verzeichnis
    L <pfad>                  Verknüpfung (das Ziel wird nicht übertragen; der Leser legt eine ungültige Verknüpfung an, damit der Läufer sie meldet)
    F <größe> <x|-> <pfad>    Datei, x: ausführbar
    END                       Ende; danach darf nichts mehr kommen

Ein Pfad beginnt mit basis/ oder neu/, hat höchstens TIEFE_MAX Teile, kein Teil ist leer, "." oder "..", und kein Zeichen ist ein Steuerzeichen, ein
Rückwärtsstrich oder NUL. Unter einer Datei oder Verknüpfung darf nichts liegen, nichts kommt doppelt vor.
"""

from __future__ import annotations

import os
import stat
import time
from typing import BinaryIO, Callable, Protocol

BAEUME = ("basis", "neu")
AUSLASSEN = frozenset({".git", "node_modules", ".venv", "__pycache__", ".next", "dist", ".pytest_cache", ".ruff_cache"})
DATEIEN_MAX = 40000
DATEI_MAX = 32 * 1024 * 1024
GESAMT_MAX = 600 * 1024 * 1024
TIEFE_MAX = 24
TEIL_MAX = 255
PFAD_MAX = 1024
ZEILE_MAX = PFAD_MAX + 64
CHUNK = 1024 * 1024
LINK_ZIEL = "ungueltig"

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class ArchivFehler(Exception):
    """Der Datenstrom ist kein gültiges Archiv (oder zu groß). Der Text ist ein kurzer Grund ohne Inhalt aus dem Strom."""


def pfad_zerlegen(pfad: str) -> tuple[str, ...]:
    if not pfad or len(pfad) > PFAD_MAX:
        raise ArchivFehler("pfad_laenge")
    teile = tuple(pfad.split("/"))
    if teile[0] not in BAEUME or len(teile) > TIEFE_MAX:
        raise ArchivFehler("pfad_wurzel")
    for t in teile:
        if t in ("", ".", ".."):
            raise ArchivFehler("pfad_teil")
        if len(t.encode("utf-8", "surrogatepass")) > TEIL_MAX or any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in t):
            raise ArchivFehler("pfad_zeichen")
    return teile


class Ziel(Protocol):
    def verzeichnis(self, teile: tuple[str, ...]) -> None: ...
    def link(self, teile: tuple[str, ...]) -> None: ...
    def datei(self, teile: tuple[str, ...], ausfuehrbar: bool, laenge: int, quelle: Callable[[int], bytes]) -> None: ...


class DateiZiel:
    """Schreibt unter einem vorhandenen Ordner. Jede Komponente wird relativ zum Deskriptor des Elternordners mit O_NOFOLLOW geöffnet: Ein Link, der
    vorher dort läge, wird nie verfolgt. Dateien entstehen mit O_EXCL und genau dem verlangten Modus (unabhängig von der umask)."""

    def __init__(self, wurzel: str):
        self._wurzel = os.open(wurzel, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)

    def schliessen(self) -> None:
        os.close(self._wurzel)

    def _ordner(self, teile: tuple[str, ...]) -> int:
        fd = os.dup(self._wurzel)
        try:
            for t in teile:
                try:
                    os.mkdir(t, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                neu = os.open(t, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = neu
        except BaseException:
            os.close(fd)
            raise
        return fd

    def verzeichnis(self, teile: tuple[str, ...]) -> None:
        os.close(self._ordner(teile))

    def link(self, teile: tuple[str, ...]) -> None:
        fd = self._ordner(teile[:-1])
        try:
            os.symlink(LINK_ZIEL, teile[-1], dir_fd=fd)
        finally:
            os.close(fd)

    def datei(self, teile: tuple[str, ...], ausfuehrbar: bool, laenge: int, quelle: Callable[[int], bytes]) -> None:
        fd = self._ordner(teile[:-1])
        try:
            f = os.open(teile[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC | _BINARY, 0o600, dir_fd=fd)
            try:
                rest = laenge
                while rest:
                    block = quelle(min(CHUNK, rest))
                    os.write(f, block)   # block ist höchstens so lang wie angefordert; os.write schreibt bei Dateien vollständig
                    rest -= len(block)
                os.fchmod(f, 0o755 if ausfuehrbar else 0o644)
            finally:
                os.close(f)
        finally:
            os.close(fd)


class SpeicherZiel:
    """Für Tests und Plattformen ohne Deskriptor-Aufrufe: hält alles im Speicher (Pfad -> Inhalt, 'D' oder 'L')."""

    def __init__(self) -> None:
        self.eintraege: dict[str, object] = {}

    def verzeichnis(self, teile: tuple[str, ...]) -> None:
        self.eintraege["/".join(teile)] = "D"

    def link(self, teile: tuple[str, ...]) -> None:
        self.eintraege["/".join(teile)] = "L"

    def datei(self, teile: tuple[str, ...], ausfuehrbar: bool, laenge: int, quelle: Callable[[int], bytes]) -> None:
        daten, rest = b"", laenge
        while rest:
            block = quelle(min(CHUNK, rest))
            daten += block
            rest -= len(block)
        self.eintraege["/".join(teile)] = (daten, ausfuehrbar)


def lies_archiv(eingang: BinaryIO, ziel: Ziel, frist: float | None = None, dateien_max: int = DATEIEN_MAX,
                gesamt_max: int = GESAMT_MAX, uhr: Callable[[], float] = time.monotonic) -> dict:
    """Liest das Archiv bis END und danach bis zum Ende des Stroms. Liefert {"dateien", "verzeichnisse", "links", "bytes"}.
    Wirft ArchivFehler bei jeder Abweichung; was bis dahin geschrieben wurde, räumt der Aufrufer weg."""
    arten: dict[tuple[str, ...], str] = {}
    zaehler = {"dateien": 0, "verzeichnisse": 0, "links": 0, "bytes": 0}

    def zeit() -> None:
        if frist is not None and uhr() > frist:
            raise ArchivFehler("zeit")

    def zeile() -> str:
        roh = eingang.readline(ZEILE_MAX + 1)
        if not roh.endswith(b"\n") or len(roh) > ZEILE_MAX:
            raise ArchivFehler("zeile")
        try:
            return roh[:-1].decode("utf-8")
        except UnicodeDecodeError:
            raise ArchivFehler("zeichensatz") from None

    def lesen(n: int) -> bytes:
        zeit()
        block = eingang.read(n)
        if not block:
            raise ArchivFehler("abgebrochen")
        return block

    def vorbereiten(teile: tuple[str, ...]) -> None:
        if teile in arten:
            raise ArchivFehler("doppelt")
        if any(arten.get(teile[:i]) in ("F", "L") for i in range(1, len(teile))):
            raise ArchivFehler("unter_datei_oder_link")
        if len(arten) >= dateien_max:
            raise ArchivFehler("zu_viele_eintraege")

    while True:
        zeit()
        text = zeile()
        if text == "END":
            break
        art, _, rest = text.partition(" ")
        if art == "D":
            teile = pfad_zerlegen(rest)
            vorbereiten(teile)
            ziel.verzeichnis(teile)
            arten[teile] = "D"
            zaehler["verzeichnisse"] += 1
        elif art == "L":
            teile = pfad_zerlegen(rest)
            if len(teile) < 2:
                raise ArchivFehler("pfad_wurzel")
            vorbereiten(teile)
            ziel.link(teile)
            arten[teile] = "L"
            zaehler["links"] += 1
        elif art == "F":
            groesse_text, _, rest2 = rest.partition(" ")
            modus, _, pfad = rest2.partition(" ")
            if not groesse_text.isascii() or not groesse_text.isdigit() or len(groesse_text) > 10 or modus not in ("x", "-"):
                raise ArchivFehler("kopf_datei")
            groesse = int(groesse_text)
            teile = pfad_zerlegen(pfad)
            if len(teile) < 2:
                raise ArchivFehler("pfad_wurzel")
            if groesse > DATEI_MAX or zaehler["bytes"] + groesse > gesamt_max:
                raise ArchivFehler("zu_gross")   # abgelehnt, bevor ein Byte gelesen wird
            vorbereiten(teile)
            ziel.datei(teile, modus == "x", groesse, lesen)
            if eingang.read(1) != b"\n":
                raise ArchivFehler("datei_ende")
            arten[teile] = "F"
            zaehler["dateien"] += 1
            zaehler["bytes"] += groesse
        else:
            raise ArchivFehler("eintragsart")
    if eingang.read(1) != b"":
        raise ArchivFehler("nach_ende")
    return zaehler


def schreibe_archiv(ausgang: BinaryIO, baeume: dict[str, str]) -> dict:
    """Schreibt die Bäume {"basis": ordner, "neu": ordner} als Archiv. Verknüpfungen werden nie verfolgt (nur als L gemeldet), Ordner aus AUSLASSEN fehlen,
    Sonderdateien (Pipe, Socket, Gerät) sind ein Fehler. Jede Datei wird einmal mit O_NOFOLLOW geöffnet und in genau der Größe gelesen, die fstat nannte."""
    zaehler = {"dateien": 0, "bytes": 0}
    for name in BAEUME:
        wurzel = baeume[name]
        ausgang.write(f"D {name}\n".encode())
        for ordner, unter, dateien in os.walk(wurzel, followlinks=False):
            rel = os.path.relpath(ordner, wurzel).replace(os.sep, "/")
            rel = name if rel == "." else f"{name}/{rel}"
            echt = []
            for d in sorted(unter):
                voll = os.path.join(ordner, d)
                if os.path.islink(voll):
                    ausgang.write(f"L {rel}/{d}\n".encode())
                elif d not in AUSLASSEN:
                    echt.append(d)
                    ausgang.write(f"D {rel}/{d}\n".encode())
            unter[:] = echt
            for datei in sorted(dateien):
                voll = os.path.join(ordner, datei)
                pfad = f"{rel}/{datei}"
                if os.path.islink(voll):
                    ausgang.write(f"L {pfad}\n".encode())
                    continue
                if not stat.S_ISREG(os.lstat(voll).st_mode):
                    raise ArchivFehler("sonderdatei")   # vor dem Öffnen: open() auf eine Pipe blockierte sonst, bis jemand schreibt
                fd = os.open(voll, os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC | _BINARY)
                try:
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode):
                        raise ArchivFehler("sonderdatei")
                    ausgang.write(f"F {st.st_size} {'x' if st.st_mode & 0o111 else '-'} {pfad}\n".encode())
                    rest = st.st_size
                    while rest:
                        block = os.read(fd, min(CHUNK, rest))
                        if not block:
                            raise ArchivFehler("datei_geaendert")
                        ausgang.write(block)
                        rest -= len(block)
                finally:
                    os.close(fd)
                ausgang.write(b"\n")
                zaehler["dateien"] += 1
                zaehler["bytes"] += st.st_size
    ausgang.write(b"END\n")
    return zaehler
