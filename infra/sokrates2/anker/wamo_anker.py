#!/usr/bin/python3 -I
"""WAMO-Notanker auf Sokrates-2 (Konzept docs/selbstueberarbeitung.md, Abschnitt G, Schritt 0.1).

Der Anker ist der kleine Teil der Selbstüberarbeitung, der Root-Rechte braucht. Er läuft als Dienst wamo-anker (root)
und lauscht auf dem Unix-Socket /run/wamo-anker/anker.sock. Installiert wird er nur von Hand mit
`sudo bash anker-einrichten.sh`, nie durch den Release-Weg. Er liegt außerhalb von /opt/wamo* in
/usr/local/lib/wamo-anker (root:root, nur root schreibbar). WAMO und die Dienstbenutzer können ihn nicht ändern.

Befehle (eine Zeile je Verbindung, siehe BEFEHLE):

    status                          Stand je Komponente, Sperre, Zustandsdatei, Prüfung des Audit-Protokolls
    rueckweg ui|kern|alles          zurück auf den letzten guten Stand aus stand.json (ruft feste Schritt-Skripte)
    sperre an|aus                   Abschalter für jede Selbständerung (Datei selbstaenderung.aus)
    pruefe-sonder <id>              Schnittstelle der Sonderfreigabe; bis Stufe 4 antwortet sie immer NEIN
    bereitstellen, migrieren,       reserviert für Stufe 1 und 2; jetzt nur die Sperre prüfen und ablehnen
    umschalten
    --trocken                       bei rueckweg und sperre: zeigt den Plan, ändert nichts (nur das Audit-Protokoll)

Aufruf als Mensch: `wamo-anker status` (Client). Notfall bei totem Dienst, nur root: `wamo-anker lokal <befehl>`.

Sicherheitsregeln (jede ist mit einer Verbotsprobe in voice-core/test_anker.py belegt):
  * Gegenstelle: SO_PEERCRED vor dem ersten gelesenen Byte; nur die Benutzer aus ERLAUBT (Namen werden bei jeder
    Verbindung frisch zu uid aufgelöst). Alles andere wird abgewiesen, ohne etwas zu lesen.
  * Eingabe: genau eine Zeile, höchstens ZEILE_MAX Bytes, nur ASCII 0x20 bis 0x7e, Allowlist-Regex je Feld.
    Keine Shell, kein Pfad aus der Anfrage: subprocess nur mit Liste und festen Pfaden unter lib/schritte,
    die Werte stammen aus der Zustandsdatei und werden vor dem Aufruf erneut geprüft.
  * Jede Anfrage trägt eine eindeutige Kennung; dieselbe Kennung wird nicht zweimal bedient.
  * Audit-Protokoll: JSON-Zeilen mit Hashkette, nur anhängen (chattr +a), Zeilenlänge begrenzt, nie Geheimnisse.
    Was etwas ändert, läuft nur, wenn die Zeile "anfrage" vorher geschrieben werden konnte (fail closed). Damit
    niemand das Protokoll und damit den Rückweg lahmlegt, gibt es Füllgrad-Klassen (Lesen bis 75 Prozent, Sperre bis
    90 Prozent, echter Rückweg bis 100 Prozent), ein Zeilenbudget je uid und Grund für Abweisungen und Anfragen ohne
    Wirkung und eine Drosselung ändernder Anfragen je uid.
  * Sperre über Datei in root-eigenem Ordner; ist sie nicht eindeutig lesbar, gilt sie als gesetzt.
  * Zustandsdatei atomar (Rename); eine kaputte Datei wird nie überschrieben, rueckweg lehnt dann ab.
  * Der Rückweg verlässt sich nicht auf die Buchführung: Das (idempotente) Schritt-Skript läuft auch, wenn stand.json
    schon den letzten guten Stand zeigt. Seine Ausgabe bekommen nur root und wamocon.
  * Ein Schritt-Skript läuft in eigener Sitzung mit Zeitlimit; bei jeder Unterbrechung wird die Sitzung beendet.
Nur Standardbibliothek, Linux. Unter Windows laufen die Tests mit Attrappen (voice-core/test_anker.py).
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import secrets
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

try:  # nur Linux; unter Windows fehlen diese Module, die Tests ersetzen sie
    import fcntl
    import grp
    import pwd
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]

VERSION = "0.1"
GENESIS = "0" * 64
ZEILE_MAX = 160  # Anfrage samt Zeilenende
ANTWORT_MAX = 8192
AUDIT_ZEILE_MAX = 1024
AUDIT_DATEI_MAX = 32 * 1024 * 1024  # darüber lehnen schreibende Befehle ab (Handarbeit: Datei rotieren, README)
# Füllgrad, bis zu dem eine Klasse noch protokolliert: Lesen und Abweisungen bis 75 Prozent, Sperre bis 90 Prozent, nur ein
# Rückweg, der wirklich etwas ändert, bis 100 Prozent. So kann kein Dauerfeuer ohne Wirkung den Rückweg aussperren.
AUDIT_GRENZE_LESEN = 0.75
AUDIT_GRENZE_SPERRE = 0.90
AUDIT_PLATZ_MIN = 1024 * 1024  # freier Platz, unter dem nichts mehr angehängt wird (verhindert halbe Zeilen)
STAND_MAX = 64 * 1024
DATEI_MAX = 1024 * 1024  # größte Datei, die für die Prüfsumme gelesen wird
SCHRITT_AUSGABE_MAX = 4096
LESE_ZEIT_S = 3.0
GESAMT_ZEIT_S = 5.0
SCHRITT_ZEIT_S = {"ui": 120.0, "kern": 180.0}
CLIENT_ZEIT_S = 420.0
IDS_MAX = 4096
ABLEHN_FENSTER_S = 60.0
ABLEHN_MAX = 20  # protokollierte Zeilen je uid, Grund und Fenster für Abweisungen und Anfragen ohne Wirkung
AENDERN_MAX = 30  # Anfragen, die etwas ändern (Sperre, Rückweg), je uid und Fenster; danach "zu_viele"
SCHRITT_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)


def _eigene_uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0


@dataclass(frozen=True)
class Pfade:
    """Feste Orte. Nur die Tests und der Rauchtest bauen sie mit unter() in einem Wegwerf-Ordner auf."""

    lib: str = "/usr/local/lib/wamo-anker"
    konfig: str = "/etc/wamo-anker"
    zustand: str = "/var/lib/wamo-anker"
    log: str = "/var/log/wamo-anker"
    lauf: str = "/run/wamo-anker"
    vertrauens_uid: int = 0  # Besitzer, dem Dateien und Ordner gehören müssen (root)
    wurzel: str = "/"  # Vorfahren von lib werden bis hierher auf fremde Schreibrechte geprüft

    @classmethod
    def unter(cls, wurzel: str, vertrauens_uid: int | None = None) -> "Pfade":
        uid = _eigene_uid() if vertrauens_uid is None else vertrauens_uid
        return cls(lib=os.path.join(wurzel, "lib"), konfig=os.path.join(wurzel, "etc"),
                   zustand=os.path.join(wurzel, "var-lib"), log=os.path.join(wurzel, "var-log"),
                   lauf=os.path.join(wurzel, "run"), vertrauens_uid=uid, wurzel=wurzel)

    @property
    def sperre(self) -> str:
        return os.path.join(self.konfig, "selbstaenderung.aus")

    @property
    def stand(self) -> str:
        return os.path.join(self.zustand, "stand.json")

    @property
    def schreibsperre(self) -> str:
        return os.path.join(self.zustand, ".schreiben.lock")

    @property
    def audit(self) -> str:
        return os.path.join(self.log, "audit.jsonl")

    @property
    def socket(self) -> str:
        return os.path.join(self.lauf, "anker.sock")

    @property
    def manifest(self) -> str:
        return os.path.join(self.lib, "SHA256SUMS")

    @property
    def schritte(self) -> str:
        return os.path.join(self.lib, "schritte")


PFADE = Pfade()

# ---------------------------------------------------------------- Befehle und Rechte
ANFRAGE_ID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[1-9][0-9]{0,17}")  # UUID oder Zahl
ZEILE_RE = re.compile(
    r"(?P<id>[0-9a-f]{16,32}) (?P<befehl>[a-z][a-z-]{1,19})(?: (?P<arg>[a-z0-9][a-z0-9-]{0,39}))?(?P<trocken> --trocken)?")
KOMPONENTEN = ("ui", "kern")
VORWAERTS_BEFEHLE = ("bereitstellen", "migrieren", "umschalten")


@dataclass(frozen=True)
class Spez:
    arg: "re.Pattern[str] | None" = None  # None: kein Argument erlaubt
    trocken: bool = False  # --trocken erlaubt
    schreibend: bool = False  # kann etwas ändern (dann ist die Audit-Zeile vorher Pflicht)
    vorwaerts: bool = False  # Selbständerung nach vorn: die Sperre hält sie an (der Rückweg bleibt immer möglich)


BEFEHLE = {
    "status": Spez(),
    "rueckweg": Spez(arg=re.compile(r"ui|kern|alles"), trocken=True, schreibend=True),
    "sperre": Spez(arg=re.compile(r"an|aus"), trocken=True, schreibend=True),
    "pruefe-sonder": Spez(arg=ANFRAGE_ID_RE),
    "bereitstellen": Spez(arg=ANFRAGE_ID_RE, trocken=True, schreibend=True, vorwaerts=True),
    "migrieren": Spez(arg=ANFRAGE_ID_RE, trocken=True, schreibend=True, vorwaerts=True),
    "umschalten": Spez(arg=ANFRAGE_ID_RE, trocken=True, schreibend=True, vorwaerts=True),
}
_ALLE = frozenset((set(BEFEHLE) - {"sperre"}) | {"sperre:an", "sperre:aus"})  # Rechte; "sperre" zählt je Argument
# Wer darf was. Namen werden bei jeder Verbindung zu uid aufgelöst; ein fehlender Benutzer passt auf niemanden.
# wamo-release (Schritt 0.3) darf die Sperre nie lösen und bis Stufe 1 nur lesen, zurückrollen und sperren: Die
# Vorwärtsbefehle (bereitstellen, migrieren, umschalten) bekommt er mit der neuen Version des Ankers, die sie füllt.
# Weitere Benutzer ergänzt nur eine neue Version des Ankers.
ERLAUBT = {
    "root": _ALLE,
    "wamocon": _ALLE,
    "wamo-release": frozenset({"status", "rueckweg", "sperre:an", "pruefe-sonder", "bereitstellen", "migrieren", "umschalten"}),
}

GRUENDE = {
    "benutzer": "Benutzer nicht erlaubt",
    "recht": "Dieser Benutzer darf den Befehl nicht ausführen",
    "zeile_zu_lang": "Anfrage zu lang",
    "kein_zeilenende": "Anfrage unvollständig",
    "zeitueberschreitung": "Anfrage zu langsam",
    "zeichen": "Unzulässige Zeichen in der Anfrage",
    "form": "Anfrage hat nicht die erwartete Form",
    "befehl_unbekannt": "Unbekannter Befehl",
    "argument": "Argument fehlt oder ist ungültig",
    "trocken": "--trocken ist für diesen Befehl nicht erlaubt",
    "doppelt": "Diese Anfrage-Kennung wurde schon bedient",
    "gesperrt": "Selbständerung ist gesperrt (wamo-anker sperre aus hebt sie auf)",
    "sperre_defekt": "Sperrdatei ist nicht eindeutig lesbar, es gilt gesperrt (Handarbeit nötig)",
    "audit_nicht_schreibbar": "Audit-Protokoll nicht schreibbar, es wurde nichts geändert",
    "zustand_defekt": "Zustandsdatei fehlt oder ist defekt, es wurde nichts geändert",
    "zustand_nicht_schreibbar": "Zustandsdatei nicht schreibbar",
    "ordner_unsicher": "Ein Ordner gehört nicht root oder ist für andere beschreibbar",
    "belegt": "Ein anderer schreibender Befehl läuft gerade",
    "kein_guter_stand": "Kein letzter guter Stand bekannt",
    "schritt_fehlt": "Das Schritt-Skript fehlt (liefert eine spätere Stufe)",
    "schritt_unsicher": "Das Schritt-Skript ist nicht vertrauenswürdig (Besitzer, Rechte oder Prüfsumme)",
    "schritt_fehlgeschlagen": "Das Schritt-Skript meldete einen Fehler",
    "zeitlimit": "Zeitlimit überschritten, Schritt abgebrochen",
    "start_fehlgeschlagen": "Das Schritt-Skript ließ sich nicht starten",
    "rueckweg_fehlgeschlagen": "Rückweg nicht vollständig",
    "zu_viele": "Zu viele ändernde Anfragen in kurzer Zeit, bitte später erneut",
    "nicht_verfuegbar": "Noch nicht verfügbar (kommt mit Stufe 1 und 2)",
    "sonder_gesperrt": "Sonderfreigabe ist in dieser Ausbaustufe nicht verfügbar (Stufe 4)",
    "antwort_zu_gross": "Antwort zu groß",
    "vorwaerts_fehlgeschlagen": "Vorwärtsbefehl abgelehnt oder fehlgeschlagen (Code in detail)",
    "intern": "Interner Fehler, nichts wurde geändert",
}


sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))   # python -I nimmt den Ordner des Skripts nicht auf; vorwaerts.py liegt daneben (root-eigen, Prüfsumme)
try:
    from vorwaerts import VorwaertsFehler
except ImportError:  # ältere Installation oder Test ohne das Modul: dann gibt es auch keine Mitspieler
    class VorwaertsFehler(Exception):  # type: ignore[no-redef]
        code = "unbekannt"
        detail = ""


class MitCode(Exception):
    """Fehler mit einem Code aus GRUENDE; der Code wird beantwortet und protokolliert, nie der Text der Ausnahme."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class Abgelehnt(MitCode):
    """Die Anfrage selbst ist nicht zulässig."""


class AnkerFehler(MitCode):
    """Ein Befehl scheitert (kein Absturz)."""


class AuditFehler(Exception):
    pass


@dataclass(frozen=True)
class Anfrage:
    id: str
    befehl: str
    arg: str | None
    trocken: bool

    @property
    def recht(self) -> str:
        return f"{self.befehl}:{self.arg}" if self.befehl == "sperre" else self.befehl


def parse_zeile(roh: bytes) -> Anfrage:
    """Prüft eine Anfrage streng. Wirft Abgelehnt mit einem Code aus GRUENDE."""
    if len(roh) > ZEILE_MAX:
        raise Abgelehnt("zeile_zu_lang")
    if not roh.endswith(b"\n"):
        raise Abgelehnt("kein_zeilenende")
    koerper = roh[:-1]
    if any(b < 0x20 or b > 0x7E for b in koerper):  # Steuerzeichen, Nullbyte, Zeilenwechsel, Nicht-ASCII (auch Bidi)
        raise Abgelehnt("zeichen")
    m = ZEILE_RE.fullmatch(koerper.decode("ascii"))
    if m is None:
        raise Abgelehnt("form")
    spez = BEFEHLE.get(m["befehl"])
    if spez is None:
        raise Abgelehnt("befehl_unbekannt")
    arg = m["arg"]
    if (spez.arg is None) != (arg is None) or (spez.arg is not None and not spez.arg.fullmatch(arg or "")):
        raise Abgelehnt("argument")
    if m["trocken"] and not spez.trocken:
        raise Abgelehnt("trocken")
    return Anfrage(m["id"], m["befehl"], arg, bool(m["trocken"]))


# ---------------------------------------------------------------- Hilfen
def zeit_iso(sekunden: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sekunden))


def _sauber(text: object, laenge: int) -> str:
    """Nur harmlose Zeichen in Protokoll und Antworten (Steuer-, Bidi- und Nicht-ASCII-Zeichen werden zu ?)."""
    return re.sub(r"[^A-Za-z0-9 _.:/=,+@-]", "?", str(text))[:laenge]


def _kanonisch(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _flock(fd: int, nicht_blockierend: bool = False) -> None:
    if fcntl is None:
        return
    fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nicht_blockierend else 0))


def _fsync_ordner(pfad: str) -> None:
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):
        fd = os.open(pfad, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def unsicher(pfad: str, ordner: bool, vertrauens_uid: int, privat: bool = False) -> str | None:
    """Grund, warum Datei oder Ordner nicht vertrauenswürdig ist (None: in Ordnung). Kein Link, Besitzer
    vertrauens_uid, für Gruppe und alle nicht beschreibbar (privat: auch nicht lesbar)."""
    try:
        st = os.lstat(pfad)
    except FileNotFoundError:
        return "fehlt"
    except OSError:
        return "nicht lesbar"
    if stat.S_ISLNK(st.st_mode):
        return "ist ein Link"
    if not (stat.S_ISDIR(st.st_mode) if ordner else stat.S_ISREG(st.st_mode)):
        return "hat die falsche Art"
    if os.name == "posix":
        if st.st_uid != vertrauens_uid:
            return "gehört nicht dem erwarteten Besitzer"
        if st.st_mode & (0o077 if privat else 0o022):
            return "hat zu offene Rechte"
    return None


def _lies_datei(pfad: str, grenze: int, vertrauens_uid: int, privat: bool = False) -> bytes:
    """Liest eine vertrauenswürdige reguläre Datei bis grenze Bytes. Wirft OSError, ValueError."""
    grund = unsicher(pfad, False, vertrauens_uid, privat)
    if grund:
        raise ValueError(grund)
    fd = os.open(pfad, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _BINARY)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("hat die falsche Art")
        daten = b""
        while len(daten) <= grenze:
            teil = os.read(fd, 65536)
            if not teil:
                return daten
            daten += teil
        raise ValueError("zu groß")
    finally:
        os.close(fd)


# ---------------------------------------------------------------- Audit-Protokoll mit Hashkette
def _zeile_bauen(rest: dict) -> bytes:
    h = hashlib.sha256(_kanonisch(rest).encode("ascii")).hexdigest()
    return (_kanonisch({**rest, "hash": h}) + "\n").encode("ascii")


def _zeile_lesen(zeile: bytes) -> tuple[dict, str]:
    """(Inhalt ohne hash, hash) einer Protokollzeile; wirft ValueError bei Formfehlern und falschem Hash."""
    if len(zeile) > AUDIT_ZEILE_MAX:
        raise ValueError("Zeile zu lang")
    try:
        obj = json.loads(zeile.decode("ascii"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise ValueError("Zeile unlesbar") from e
    h = obj.pop("hash", None) if isinstance(obj, dict) else None
    if not isinstance(h, str) or not isinstance(obj.get("n"), int) or isinstance(obj.get("n"), bool):
        raise ValueError("Zeile ohne n oder hash")
    if hashlib.sha256(_kanonisch(obj).encode("ascii")).hexdigest() != h:
        raise ValueError("Hash stimmt nicht")
    return obj, h


def kette_pruefen(pfad: str, vertrauens_uid: int | None = None) -> tuple[bool, int, str, str | None]:
    """Prüft die ganze Hashkette Zeile für Zeile (konstanter Speicher). Ergebnis (ok, Zeilen, letzter Hash, Grund).
    Fehlt die Datei, ist die Kette leer."""
    grund = unsicher(pfad, False, _eigene_uid() if vertrauens_uid is None else vertrauens_uid, privat=True)
    if grund == "fehlt":
        return True, 0, GENESIS, None
    if grund:
        return False, 0, GENESIS, f"Datei {grund}"
    vorher, n, gelesen = GENESIS, 0, 0
    try:
        fd = os.open(pfad, os.O_RDONLY | _NOFOLLOW | _CLOEXEC | _BINARY)
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return False, 0, GENESIS, "Datei hat die falsche Art"
            while zeile := f.readline(AUDIT_ZEILE_MAX + 2):
                n += 1
                gelesen += len(zeile)
                if gelesen > AUDIT_DATEI_MAX + AUDIT_ZEILE_MAX:
                    return False, n, vorher, "Datei zu groß"
                if not zeile.endswith(b"\n"):
                    return False, n, vorher, f"Zeile {n}: unvollständig oder zu lang"
                try:
                    obj, h = _zeile_lesen(zeile[:-1])
                except ValueError as e:
                    return False, n, vorher, f"Zeile {n}: {e}"
                if obj["n"] != n or obj.get("vorher") != vorher:
                    return False, n, vorher, f"Zeile {n}: Kette unterbrochen"
                vorher = h
    except OSError:
        return False, n, vorher, "Datei nicht lesbar"
    return True, n, vorher, None


class Audit:
    """Anhängen mit Hashkette. Jede Zeile enthält n, vorher (Hash der Vorzeile) und hash. Es wird nie überschrieben.
    Mehrere Prozesse (Dienst, `lokal`) hängen unter flock an; die letzte Zeile wird vor jedem Anhängen geprüft."""

    def __init__(self, pfad: str, uhr: Callable[[], float] = time.time, vertrauens_uid: int | None = None):
        self.pfad = pfad
        self.uhr = uhr
        self.vertrauens_uid = _eigene_uid() if vertrauens_uid is None else vertrauens_uid

    def fuellgrad(self) -> float:
        try:
            return os.stat(self.pfad).st_size / AUDIT_DATEI_MAX
        except OSError:
            return 0.0

    def anhaengen(self, ereignis: str, bis: float = 1.0, **felder: object) -> None:
        """Hängt eine Zeile an, solange die Datei danach höchstens bis (Anteil von AUDIT_DATEI_MAX) groß ist und noch
        AUDIT_PLATZ_MIN frei ist. Wirft AuditFehler; angehängt wird dann nichts."""
        rest = {"ereignis": ereignis, "t": zeit_iso(self.uhr())}
        rest.update({k: v for k, v in felder.items() if v is not None})
        fd = None
        grund = unsicher(os.path.dirname(self.pfad), True, self.vertrauens_uid)
        grund_datei = unsicher(self.pfad, False, self.vertrauens_uid, privat=True)
        if grund or grund_datei not in (None, "fehlt"):  # Link, fremder Besitzer, offene Rechte: nicht anfassen
            raise AuditFehler(f"Ordner oder Datei nicht vertrauenswürdig ({grund or grund_datei})")
        try:
            fd = os.open(self.pfad, os.O_RDWR | os.O_APPEND | os.O_CREAT | _NOFOLLOW | _CLOEXEC | _BINARY, 0o600)
            _flock(fd)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise AuditFehler("keine reguläre Datei")
            vorher, n = self._letzte(fd, st.st_size)
            rest.update(n=n + 1, vorher=vorher)
            zeile = _zeile_bauen(rest)
            if len(zeile) > AUDIT_ZEILE_MAX:
                raise AuditFehler("Zeile zu lang")
            if st.st_size + len(zeile) > AUDIT_DATEI_MAX * bis:
                raise AuditFehler("Audit-Datei voll")
            frei = getattr(os, "statvfs", None)
            if frei is not None:  # keine halbe Zeile bei voller Platte: erst prüfen, dann schreiben
                v = frei(os.path.dirname(self.pfad))
                if v.f_bavail * v.f_frsize < AUDIT_PLATZ_MIN:
                    raise AuditFehler("zu wenig freier Platz")
            if os.write(fd, zeile) != len(zeile):
                raise AuditFehler("Teilschreibung")
            os.fsync(fd)
        except OSError as e:
            raise AuditFehler(f"Schreibfehler {errno.errorcode.get(e.errno or 0, '?')}") from e
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _letzte(fd: int, groesse: int) -> tuple[str, int]:
        if groesse == 0:
            return GENESIS, 0
        laenge = min(groesse, 2 * AUDIT_ZEILE_MAX)
        os.lseek(fd, groesse - laenge, os.SEEK_SET)
        block = b""
        while len(block) < laenge:
            teil = os.read(fd, laenge - len(block))
            if not teil:
                break
            block += teil
        if not block.endswith(b"\n"):
            raise AuditFehler("letzte Zeile unvollständig")
        teile = block[:-1].rsplit(b"\n", 1)
        if len(teile) == 1 and groesse > laenge:
            raise AuditFehler("letzte Zeile zu lang")
        try:
            obj, h = _zeile_lesen(teile[-1])
        except ValueError as e:
            raise AuditFehler(f"letzte Zeile: {e}") from e
        return h, obj["n"]


# ---------------------------------------------------------------- Zustandsdatei (letzte gute Stände)
VERSION_RE = re.compile(r"v(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
ZEIT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
ZIEL_RE = {"ui": re.compile(r"blau|gruen"), "kern": re.compile(r"a|b")}  # Farbe der Oberfläche, Slot des Kerns
EINTRAG_FELDER = ("version", "commit", "ziel", "seit")


class ZustandDefekt(Exception):
    pass


def leerer_stand() -> dict:
    return {"schema": 1, "komponenten": {k: {"aktiv": None, "letzte_gute": None} for k in KOMPONENTEN}}


def _eintrag_pruefen(komp: str, e: object) -> dict | None:
    if e is None:
        return None
    if not isinstance(e, dict) or set(e) != set(EINTRAG_FELDER) or not all(isinstance(v, str) for v in e.values()):
        raise ZustandDefekt(f"{komp}: Eintrag hat die falschen Felder")
    if not (VERSION_RE.fullmatch(e["version"]) and COMMIT_RE.fullmatch(e["commit"])
            and ZIEL_RE[komp].fullmatch(e["ziel"]) and ZEIT_RE.fullmatch(e["seit"])):
        raise ZustandDefekt(f"{komp}: Eintrag hat ungültige Werte")
    return dict(e)


def stand_pruefen(obj: object) -> dict:
    if not isinstance(obj, dict) or set(obj) != {"schema", "komponenten"} or obj["schema"] != 1 \
            or isinstance(obj["schema"], bool):
        raise ZustandDefekt("Kopf ungültig")
    komp = obj["komponenten"]
    if not isinstance(komp, dict) or set(komp) != set(KOMPONENTEN):
        raise ZustandDefekt("Komponenten ungültig")
    ergebnis = leerer_stand()
    for k in KOMPONENTEN:
        if not isinstance(komp[k], dict) or set(komp[k]) != {"aktiv", "letzte_gute"}:
            raise ZustandDefekt(f"{k}: Felder ungültig")
        ergebnis["komponenten"][k] = {f: _eintrag_pruefen(k, komp[k][f]) for f in ("aktiv", "letzte_gute")}
    return ergebnis


def _keine_doppelten(paare: list) -> dict:
    d = dict(paare)
    if len(d) != len(paare):
        raise ZustandDefekt("doppelter Schlüssel")
    return d


def stand_laden(pfad: str, vertrauens_uid: int) -> dict:
    try:
        daten = _lies_datei(pfad, STAND_MAX, vertrauens_uid, privat=True)
    except ValueError as e:
        raise ZustandDefekt(f"Datei {e}") from e
    except OSError as e:
        raise ZustandDefekt("Datei nicht lesbar") from e
    try:
        return stand_pruefen(json.loads(daten.decode("utf-8"), object_pairs_hook=_keine_doppelten))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise ZustandDefekt("kein gültiges JSON") from e


def stand_schreiben(pfad: str, stand: dict) -> None:
    """Atomar: neue Datei im selben Ordner (O_EXCL, 0600), fsync, Rename. Die bisherige Datei bleibt als .vorher.
    Bei jedem Fehler (volle Platte) bleibt die alte Datei unberührt. Wirft ZustandDefekt, OSError."""
    stand_pruefen(stand)
    daten = (json.dumps(stand, sort_keys=True, indent=1) + "\n").encode("ascii")
    ordner = os.path.dirname(pfad)
    tmp = os.path.join(ordner, f".stand.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC | _BINARY, 0o600)
    try:
        try:
            if os.write(fd, daten) != len(daten):
                raise OSError(errno.ENOSPC, "Teilschreibung")
            os.fsync(fd)
        finally:
            os.close(fd)
        vorher_tmp = tmp + ".v"
        try:  # Sicherung der letzten guten Datei, best effort
            os.link(pfad, vorher_tmp)
            os.replace(vorher_tmp, pfad + ".vorher")
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(vorher_tmp)
        os.replace(tmp, pfad)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_ordner(ordner)


# ---------------------------------------------------------------- Sperre (Datei in root-eigenem Ordner)
def sperre_zustand(p: Pfade) -> str:
    """"an", "aus" oder "defekt". Nur "aus" lässt Selbständerung zu; "defekt" gilt wie "an"."""
    if unsicher(p.konfig, True, p.vertrauens_uid):
        return "defekt"
    try:
        st = os.lstat(p.sperre)
    except FileNotFoundError:
        return "aus"
    except OSError:
        return "defekt"
    if not stat.S_ISREG(st.st_mode) or (os.name == "posix" and (st.st_uid != p.vertrauens_uid or st.st_mode & 0o022)):
        return "defekt"
    return "an"


def sperre_setzen(p: Pfade, benutzer: str, jetzt: float) -> bool:
    """Legt die Sperrdatei an. Schon das Vorhandensein zählt, der Inhalt ist nur Hinweis (eine volle Platte
    verhindert die Sperre deshalb nicht). True, wenn sie neu gesetzt wurde."""
    try:
        fd = os.open(p.sperre, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC | _BINARY, 0o644)
    except FileExistsError:
        return False
    try:
        with contextlib.suppress(OSError):
            os.write(fd, f"gesperrt seit {zeit_iso(jetzt)} von {_sauber(benutzer, 32)}\n".encode("ascii"))
    finally:
        os.close(fd)
    _fsync_ordner(p.konfig)
    return True


def sperre_loesen(p: Pfade) -> bool:
    os.unlink(p.sperre)
    _fsync_ordner(p.konfig)
    return True


# ---------------------------------------------------------------- Installation und Schritt-Skripte
MANIFEST_RE = re.compile(r"([0-9a-f]{64})  (?:\./)?([A-Za-z0-9_][A-Za-z0-9_./-]{0,127})")


def manifest_lesen(p: Pfade) -> dict[str, str]:
    """{relativer Pfad: sha256} aus lib/SHA256SUMS. Wirft ValueError, OSError."""
    text = _lies_datei(p.manifest, DATEI_MAX, p.vertrauens_uid).decode("ascii")
    eintraege: dict[str, str] = {}
    for zeile in text.splitlines():
        m = MANIFEST_RE.fullmatch(zeile)
        if m is None or ".." in m[2].split("/") or m[2] in eintraege:
            raise ValueError("Manifest ungültig")
        eintraege[m[2]] = m[1]
    return eintraege


def _sha256(pfad: str, vertrauens_uid: int) -> str:
    return hashlib.sha256(_lies_datei(pfad, DATEI_MAX, vertrauens_uid)).hexdigest()


def _gruppe_hat_mitglieder(gid: int) -> bool:
    if grp is None or pwd is None:
        return True  # nicht prüfbar: vorsichtig
    try:
        if grp.getgrgid(gid).gr_mem:
            return True
    except KeyError:
        return False
    return any(u.pw_gid == gid for u in pwd.getpwall() if u.pw_uid != 0)


def vorfahren_unsicher(p: Pfade) -> list[str]:
    """Ordner oberhalb von lib bis p.wurzel, in denen jemand lib samt eigener Prüfsumme austauschen könnte: fremder
    Besitzer, für alle beschreibbar (ohne Sticky-Bit) oder für eine Gruppe mit Mitgliedern beschreibbar."""
    if os.name != "posix":
        return []
    probleme = []
    ordner, wurzel = os.path.dirname(os.path.normpath(p.lib)), os.path.normpath(p.wurzel)
    while True:
        try:
            st = os.lstat(ordner)
        except OSError:
            probleme.append(f"{ordner}: nicht lesbar")
            break
        if st.st_uid != p.vertrauens_uid:
            probleme.append(f"{ordner}: gehört nicht dem erwarteten Besitzer")
        elif st.st_mode & 0o002 and not st.st_mode & stat.S_ISVTX:
            probleme.append(f"{ordner}: für alle beschreibbar")
        elif st.st_mode & 0o020 and _gruppe_hat_mitglieder(st.st_gid):
            probleme.append(f"{ordner}: für eine Gruppe mit Mitgliedern beschreibbar")
        if ordner in (wurzel, os.path.dirname(ordner)):
            break
        ordner = os.path.dirname(ordner)
    return probleme


def installation_pruefen(p: Pfade) -> list[str]:
    """Abweichungen der installierten Dateien vom Manifest und unsichere Elternordner (leer: in Ordnung). Höchstens
    10 Meldungen."""
    probleme = vorfahren_unsicher(p)
    try:
        soll = manifest_lesen(p)
    except (ValueError, OSError) as e:
        return [*probleme, f"Manifest: {e}"][:10]
    for rel, h in sorted(soll.items()):
        try:
            if _sha256(os.path.join(p.lib, *rel.split("/")), p.vertrauens_uid) != h:
                probleme.append(f"{rel}: Prüfsumme weicht ab")
        except (ValueError, OSError) as e:
            probleme.append(f"{rel}: {e}")
    for wurzel, ordner, dateien in os.walk(p.lib):
        rel_wurzel = os.path.relpath(wurzel, p.lib).replace(os.sep, "/")
        if unsicher(wurzel, True, p.vertrauens_uid):
            probleme.append(f"{rel_wurzel}: Ordner nicht vertrauenswürdig")
        for name in ordner + dateien:
            pfad = os.path.join(wurzel, name)
            rel = name if rel_wurzel == "." else f"{rel_wurzel}/{name}"
            if os.path.islink(pfad):
                probleme.append(f"{rel}: ist ein Link")
            elif name in dateien and rel not in soll and rel != "SHA256SUMS":
                probleme.append(f"{rel}: nicht im Manifest")
    return probleme[:10]


def schritt_pruefen(p: Pfade, name: str) -> str:
    """Pfad des Schritt-Skripts, wenn es vertrauenswürdig ist: reguläre Datei, kein Link, Besitzer root, für andere
    nicht beschreibbar, ausführbar, Prüfsumme wie im Manifest. Wirft AnkerFehler(schritt_fehlt|schritt_unsicher)."""
    pfad = os.path.join(p.schritte, name)
    grund = unsicher(pfad, False, p.vertrauens_uid)
    if grund == "fehlt":
        raise AnkerFehler("schritt_fehlt", name)
    if grund or unsicher(p.schritte, True, p.vertrauens_uid) or (os.name == "posix" and not os.stat(pfad).st_mode & 0o100):
        raise AnkerFehler("schritt_unsicher", f"{name}: {grund or 'Ordner oder Rechte'}")
    try:
        if manifest_lesen(p).get(f"schritte/{name}") != _sha256(pfad, p.vertrauens_uid):
            raise AnkerFehler("schritt_unsicher", f"{name}: Prüfsumme")
    except (ValueError, OSError) as e:
        raise AnkerFehler("schritt_unsicher", f"{name}: {e}") from e
    return pfad


class SchrittFehler(MitCode):
    """Ein Schritt-Skript ließ sich nicht starten oder überschritt das Zeitlimit."""


def schritt_ausfuehren(argv: list[str], zeitlimit: float) -> tuple[int, bytes]:
    """Startet ein Schritt-Skript ohne Shell, mit leerer Umgebung, eigener Sitzung und begrenzter Ausgabe.
    Ergebnis (Exit-Code, die ersten SCHRITT_AUSGABE_MAX Ausgabe-Bytes). Wirft SchrittFehler."""
    env = {"PATH": SCHRITT_PATH, "LC_ALL": "C.UTF-8", "HOME": "/root"}
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=env, cwd="/", close_fds=True, start_new_session=True, shell=False)
    except OSError as e:
        raise SchrittFehler("start_fehlgeschlagen") from e
    ende = time.monotonic() + zeitlimit
    behalten = b""
    try:
        while True:
            rest = ende - time.monotonic()
            if rest <= 0:
                raise SchrittFehler("zeitlimit")
            if select.select([proc.stdout], [], [], min(rest, 1.0))[0]:
                block = os.read(proc.stdout.fileno(), 4096)
                if not block:
                    break
                behalten += block[:max(0, SCHRITT_AUSGABE_MAX - len(behalten))]
        return proc.wait(timeout=max(0.1, ende - time.monotonic())), behalten
    except BaseException as e:  # Zeitlimit, aber auch jede andere Unterbrechung: nie einen Root-Prozess zurücklassen
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)  # die ganze Sitzung, auch Kindprozesse
        proc.wait()
        if isinstance(e, (SchrittFehler, subprocess.TimeoutExpired)):
            raise SchrittFehler("zeitlimit") from None
        raise
    finally:
        proc.stdout.close()


# ---------------------------------------------------------------- Gegenstelle
@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    gid: int


def peer_von(sock: socket.socket) -> Peer | None:
    try:  # struct ucred: pid_t (int), uid_t und gid_t (vorzeichenlos)
        roh = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))  # type: ignore[attr-defined]
        pid, uid, gid = struct.unpack("iII", roh)
    except (OSError, AttributeError, struct.error):
        return None
    return Peer(pid, uid, gid)


def name_zu_uid(name: str) -> int | None:
    if pwd is None:
        return None
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


# ---------------------------------------------------------------- Der Anker
MENSCHEN = frozenset({"root", "wamocon"})  # nur sie bekommen die Ausgabe der Schritt-Skripte zu sehen


@dataclass
class _Fenster:
    """Zählt Ereignisse einer uid in einem Zeitfenster: wie viele durften ins Protokoll, wie viele wurden nur gezählt."""

    start: float
    anzahl: int = 0
    unterdrueckt: int = 0
    nachlass: int = 0  # im letzten Fenster nur gezählte, steht in der ersten Zeile des nächsten


class Anker:
    def __init__(self, pfade: Pfade = PFADE, erlaubt: dict[str, frozenset] | None = None,
                 ausfuehren: Callable[[list[str], float], tuple[int, bytes]] = schritt_ausfuehren,
                 uhr: Callable[[], float] = time.time,
                 aufloesen: Callable[[str], int | None] = name_zu_uid,
                 vorwaerts_fabrik: Callable[[], object] | None = None):
        self.p = pfade
        self._vorwaerts_fabrik = vorwaerts_fabrik   # baut beim ersten Vorwärtsbefehl die Mitspieler (Datenbank, Quelle, Werk); ohne sie: nicht_verfuegbar
        self._vorwaerts = None
        self.erlaubt = ERLAUBT if erlaubt is None else erlaubt
        self.ausfuehren = ausfuehren
        self.uhr = uhr
        self.aufloesen = aufloesen
        self.audit = Audit(pfade.audit, uhr, pfade.vertrauens_uid)
        self._ids: dict[str, None] = {}
        self._fenster: dict[tuple[int, str], _Fenster] = {}

    # --- Gegenstelle
    def benutzer(self, peer: Peer | None) -> str | None:
        if peer is None:
            return None
        for name in self.erlaubt:
            if self.aufloesen(name) == peer.uid:
                return name
        return None

    # --- Protokoll
    def protokolliere(self, ereignis: str, bis: float, **felder: object) -> bool:
        """Eine Zeile ins Audit-Protokoll (bis: höchster Füllgrad der Klasse). False, wenn sie nicht geschrieben wurde."""
        try:
            self.audit.anhaengen(ereignis, bis, **felder)
            return True
        except AuditFehler as e:
            print(f"wamo-anker: Audit nicht schreibbar ({_sauber(e, 60)})", file=sys.stderr)
            return False

    def _budget(self, uid: int, schluessel: str, maximum: int) -> tuple[bool, int]:
        """Darf diese uid in diesem Fenster noch eine Zeile dieser Art protokollieren? Dazu die Zahl der im Fenster davor
        nur gezählten Ereignisse (sie steht in der ersten Zeile des neuen Fensters)."""
        jetzt = self.uhr()
        f = self._fenster.get((uid, schluessel))
        if f is None or jetzt - f.start >= ABLEHN_FENSTER_S:
            if len(self._fenster) >= 512:
                self._fenster.clear()
            f = self._fenster[(uid, schluessel)] = _Fenster(jetzt, nachlass=f.unterdrueckt if f else 0)
        if f.anzahl >= maximum:
            f.unterdrueckt += 1  # nur zählen: auch ein erlaubter Benutzer füllt das Protokoll so nicht
            return False, 0
        f.anzahl += 1
        nachlass, f.nachlass = f.nachlass, 0
        return True, nachlass

    def _ablehnung(self, peer: Peer | None, name: str | None, code: str, anf: Anfrage | None = None,
                   roh: bytes | None = None) -> dict:
        uid = peer.uid if peer else -1
        erlaubt, nachlass = self._budget(uid, code, ABLEHN_MAX)  # je Grund, damit Rauschen echte Verstöße nicht verdeckt
        if erlaubt:
            self.protokolliere("abgelehnt", AUDIT_GRENZE_LESEN, grund=code, uid=uid, pid=peer.pid if peer else None,
                               benutzer=name, id=anf.id if anf else None, befehl=anf.befehl if anf else None,
                               roh_laenge=len(roh) if roh is not None else None,
                               roh_sha=hashlib.sha256(roh).hexdigest()[:16] if roh else None,
                               unterdrueckt=nachlass or None)
        return {"ok": False, "abgelehnt": True, "grund": code, "text": GRUENDE.get(code, GRUENDE["intern"])}

    def abweisen(self, peer: Peer | None) -> dict:
        """Gegenstelle nicht erlaubt: nichts wird gelesen."""
        return self._ablehnung(peer, None, "benutzer")

    def _wirkung(self, anf: Anfrage) -> str | None:
        """"rueckweg" oder "sperre", wenn die Anfrage etwas ändern wird, sonst None. Davon hängt ab, wie viel Platz im
        Protokoll ihr zusteht: Anfragen ohne Wirkung dürfen den Platz für einen echten Rückweg nie aufbrauchen."""
        if anf.befehl in VORWAERTS_BEFEHLE and not anf.trocken and self._vorwaerts_fabrik is not None:
            return "vorwaerts"   # ändert Datenbank und Kern: die Protokollzeile vorher ist Pflicht
        if anf.trocken or anf.befehl not in ("sperre", "rueckweg"):
            return None
        if anf.befehl == "sperre":
            zustand = sperre_zustand(self.p)
            return "sperre" if zustand in ("an", "aus") and zustand != str(anf.arg) else None
        try:
            stand = stand_laden(self.p.stand, self.p.vertrauens_uid)
        except ZustandDefekt:
            return None  # scheitert ohnehin, ohne etwas zu ändern
        komp = KOMPONENTEN if anf.arg == "alles" else (str(anf.arg),)
        return "rueckweg" if any(stand["komponenten"][k]["letzte_gute"] for k in komp) else None

    # --- Anfrage bearbeiten
    def bearbeite(self, roh: bytes, lesefehler: str | None, peer: Peer, name: str) -> dict:
        """Eine Anfrage einer erlaubten Gegenstelle. lesefehler: Code, wenn schon das Lesen scheiterte."""
        if lesefehler:
            return self._ablehnung(peer, name, lesefehler, roh=roh)
        if self.benutzer(peer) != name:  # Gegenprobe: der Name muss zur uid passen
            return self._ablehnung(peer, None, "benutzer", roh=roh)
        try:
            anf = parse_zeile(roh)
        except Abgelehnt as e:
            return self._ablehnung(peer, name, e.code, roh=roh)
        if anf.id in self._ids:
            return self._ablehnung(peer, name, "doppelt", anf, roh)
        self._ids[anf.id] = None
        if len(self._ids) > IDS_MAX:
            del self._ids[next(iter(self._ids))]
        if anf.recht not in self.erlaubt[name]:
            return self._ablehnung(peer, name, "recht", anf, roh)
        if BEFEHLE[anf.befehl].vorwaerts and sperre_zustand(self.p) != "aus":
            return self._ablehnung(peer, name, "gesperrt", anf, roh)
        wirkung = self._wirkung(anf)
        bis = {"rueckweg": 1.0, "sperre": AUDIT_GRENZE_SPERRE}.get(wirkung or "", AUDIT_GRENZE_LESEN)
        daten = {"uid": peer.uid, "pid": peer.pid, "benutzer": name, "id": anf.id, "befehl": anf.befehl, "arg": anf.arg,
                 "trocken": anf.trocken or None}
        if wirkung:  # ändert etwas: begrenzt, und ohne Protokollzeile vorher geschieht nichts
            if not self._budget(peer.uid, "aendern", AENDERN_MAX)[0]:
                return self._ablehnung(peer, name, "zu_viele", anf, roh)
            if not self.protokolliere("anfrage", bis, **daten):
                return self._antwort(False, "audit_nicht_schreibbar")
            protokoll = True
        else:  # Lesen und Anfragen ohne Wirkung: begrenzt protokolliert, nie vor dem Rückweg
            protokoll, nachlass = self._budget(peer.uid, "ohne_wirkung", ABLEHN_MAX)
            if protokoll:
                self.protokolliere("anfrage", bis, unterdrueckt=nachlass or None, **daten)
        try:
            antwort = self._ausfuehren(anf, name)
        except MitCode as e:
            antwort = self._antwort(False, e.code, detail=_sauber(e.detail, 80) if e.detail else None)
        except Exception as e:  # noqa: BLE001 (nie abstürzen, nie Einzelheiten nach außen)
            print(f"wamo-anker: interner Fehler {type(e).__name__}", file=sys.stderr)
            antwort = self._antwort(False, "intern")
        if protokoll:
            teile = ",".join(f"{t['komponente']}:{'ok' if t['ok'] else t.get('grund')}" for t in antwort.get("teile", []))
            self.protokolliere("ergebnis", bis, id=anf.id, befehl=anf.befehl, ok=bool(antwort.get("ok")),
                               grund=antwort.get("grund"), trocken=anf.trocken or None, teile=_sauber(teile, 120) or None)
        return antwort

    @staticmethod
    def _antwort(ok: bool, grund: str | None = None, text: str | None = None, **felder: object) -> dict:
        a: dict = {"ok": ok}
        if grund:
            a["grund"] = grund
        a["text"] = text or (GRUENDE.get(grund, GRUENDE["intern"]) if grund else "in Ordnung")
        a.update({k: v for k, v in felder.items() if v is not None})
        return a

    def _ausfuehren(self, anf: Anfrage, name: str) -> dict:
        if anf.befehl == "status":
            return self.cmd_status()
        if anf.befehl == "rueckweg":
            return self.cmd_rueckweg(str(anf.arg), anf.trocken, name in MENSCHEN)
        if anf.befehl == "sperre":
            return self.cmd_sperre(str(anf.arg), anf.trocken, name)
        if anf.befehl == "pruefe-sonder":
            return self.cmd_pruefe_sonder(str(anf.arg))
        if anf.befehl in VORWAERTS_BEFEHLE:
            return self.cmd_vorwaerts(anf)
        return self._antwort(False, "nicht_verfuegbar")

    @contextlib.contextmanager
    def _schreibsperre(self):
        """Ein schreibender Befehl zur Zeit (auch zwischen Dienst und `lokal`)."""
        if unsicher(self.p.zustand, True, self.p.vertrauens_uid):
            raise AnkerFehler("ordner_unsicher", "zustand")
        try:
            fd = os.open(self.p.schreibsperre, os.O_RDWR | os.O_CREAT | _NOFOLLOW | _CLOEXEC | _BINARY, 0o600)
        except OSError as e:
            raise AnkerFehler("zustand_nicht_schreibbar") from e
        try:
            try:
                _flock(fd, nicht_blockierend=True)
            except BlockingIOError:
                raise AnkerFehler("belegt") from None
            yield
        finally:
            os.close(fd)

    # --- status
    def cmd_status(self) -> dict:
        try:
            stand = stand_laden(self.p.stand, self.p.vertrauens_uid)
            zustand, zustand_grund = "ok", None
        except ZustandDefekt as e:
            stand, zustand, zustand_grund = leerer_stand(), "defekt", str(e)
        ok, zeilen, letzter, grund = kette_pruefen(self.p.audit, self.p.vertrauens_uid)
        probleme = installation_pruefen(self.p)
        return self._antwort(
            True, text="Anker läuft", version=VERSION, zeit=zeit_iso(self.uhr()), sperre=sperre_zustand(self.p),
            zustand=zustand, zustand_grund=zustand_grund, komponenten=stand["komponenten"],
            offene_anfragen="nicht verfügbar", offene_anfragen_hinweis="folgt mit Schritt 0.2 (Datenbank)",
            audit={"kette": "ok" if ok else "defekt", "zeilen": zeilen, "letzter_hash": letzter, "grund": grund,
                   "fuellgrad_prozent": round(self.audit.fuellgrad() * 100, 1)},
            installation="ok" if not probleme else "abweichung", installation_probleme=probleme or None)

    # --- bereitstellen, migrieren, umschalten (vorwaerts.py)
    def cmd_vorwaerts(self, anf: Anfrage) -> dict:
        """Die Vorwärtsbefehle. Der Anker liest Anfrage und Freigabe selbst aus der Datenbank (vorwaerts.py); hier nur Aufruf, Zeitgrenze, Sperre gegen
        Gleichzeitiges, Prüfung des Schritt-Skripts des Kerns und das Nachführen der Zustandsdatei. Fehler kommen als Code, nie als Text aus der Quelle."""
        if self._vorwaerts_fabrik is None:
            return self._antwort(False, "nicht_verfuegbar")
        if self._vorwaerts is None:
            try:
                self._vorwaerts = self._vorwaerts_fabrik()
            except Exception as e:  # noqa: BLE001 (Datenbank oder Zugangsdatei fehlen: ehrlich nicht verfügbar, nichts geändert)
                print(f"wamo-anker: Vorwärtsbefehle nicht verfügbar ({type(e).__name__})", file=sys.stderr)
                return self._antwort(False, "nicht_verfuegbar", detail="mitspieler")
        schritt_pruefen(self.p, "kern-slots")   # das Skript, über das der Kern bereitgestellt und umgeschaltet wird: Besitzer, Rechte und Prüfsumme
        if anf.trocken:
            return self._vorwaerts_aufruf(anf, True)
        with self._schreibsperre():
            return self._vorwaerts_aufruf(anf, False)

    def _vorwaerts_aufruf(self, anf: Anfrage, trocken: bool) -> dict:
        try:
            erg = self._vorwaerts.ausfuehren(anf.befehl, str(anf.arg), trocken)
        except VorwaertsFehler as e:
            self.protokolliere("vorwaerts", 1.0, befehl=anf.befehl, ok=False, code=e.code)
            return self._antwort(False, "vorwaerts_fehlgeschlagen", detail=_sauber(f"{e.code} {e.detail}".strip(), 80))
        self.protokolliere("vorwaerts", 1.0, befehl=anf.befehl, ok=True, release=_sauber(erg.get("release") or "", 60) or None, trocken=trocken or None)
        if anf.befehl == "umschalten" and not trocken:
            self._kern_aktiv_eintragen(erg)
        felder = {k: erg[k] for k in ("release", "slot", "plan", "angewendet") if k in erg}
        return self._antwort(True, text="Trockenlauf" if trocken else "in Ordnung", trocken=trocken or None, ergebnis=felder)

    def _kern_aktiv_eintragen(self, erg: dict) -> None:
        """Nach dem Umschalten steht der neue Slot als aktiv in der Zustandsdatei. 'letzte gute' folgt erst nach bestandener Bewährung (nicht hier)."""
        try:
            stand = stand_laden(self.p.stand, self.p.vertrauens_uid)
            eintrag = {"version": f"v{erg['version']}", "commit": erg["commit"], "ziel": erg["slot"], "seit": zeit_iso(self.uhr())}
            if VERSION_RE.fullmatch(eintrag["version"]) and COMMIT_RE.fullmatch(eintrag["commit"]) and ZIEL_RE["kern"].fullmatch(eintrag["ziel"]):
                stand["komponenten"]["kern"]["aktiv"] = eintrag
                stand_schreiben(self.p.stand, stand)
        except (OSError, ZustandDefekt, KeyError):
            self.protokolliere("vorwaerts", 1.0, befehl="umschalten", zustand="nicht_nachgefuehrt")

    # --- sperre
    def cmd_sperre(self, arg: str, trocken: bool, name: str) -> dict:
        zustand = sperre_zustand(self.p)
        if zustand == "defekt":
            return self._antwort(False, "sperre_defekt")
        soll = "an" if arg == "an" else "aus"
        if zustand == soll:
            text = "Sperre war schon gesetzt" if soll == "an" else "Sperre war schon aufgehoben"
            return self._antwort(True, text=text, geaendert=False, trocken=trocken or None)
        if trocken:
            text = "Würde die Sperre setzen" if soll == "an" else "Würde die Sperre aufheben"
            return self._antwort(True, text=text, geaendert=False, trocken=True)
        try:
            if soll == "an":
                sperre_setzen(self.p, name, self.uhr())
            else:
                sperre_loesen(self.p)
        except OSError as e:
            raise AnkerFehler("zustand_nicht_schreibbar", errno.errorcode.get(e.errno or 0)) from e
        return self._antwort(True, text="Sperre gesetzt" if soll == "an" else "Sperre aufgehoben", geaendert=True)

    # --- pruefe-sonder
    def cmd_pruefe_sonder(self, anfrage_id: str) -> dict:
        """Stufe 4 füllt diese Funktion (zwei Admins, anderer Freigeber, Wartezeit, Commit und Tree gleich). Bis dahin
        gibt es keinen Weg zu OK: die Antwort ist immer NEIN."""
        return self._antwort(False, "sonder_gesperrt", ergebnis="NEIN", anfrage=anfrage_id)

    # --- rueckweg
    def cmd_rueckweg(self, arg: str, trocken: bool, mit_ausgabe: bool) -> dict:
        """Zurück auf den letzten guten Stand. Das Schritt-Skript läuft auch dann, wenn die Buchführung schon den
        letzten guten Stand zeigt (sie könnte nach einem Absturz hinter dem Schalter zurückliegen); es ist idempotent.
        Die Ausgabe des Skripts sieht nur, wer in MENSCHEN steht."""
        komp = KOMPONENTEN if arg == "alles" else (arg,)
        if trocken:
            return self._rueckweg(komp, True, mit_ausgabe)
        with self._schreibsperre():
            return self._rueckweg(komp, False, mit_ausgabe)

    def _rueckweg(self, komp: tuple[str, ...], trocken: bool, mit_ausgabe: bool) -> dict:
        try:
            stand = stand_laden(self.p.stand, self.p.vertrauens_uid)
        except ZustandDefekt as e:
            return self._antwort(False, "zustand_defekt", detail=_sauber(e, 80))
        teile = [self._rueckweg_eine(k, stand, trocken, mit_ausgabe) for k in komp]
        ok = all(t["ok"] for t in teile)
        text = ("Trockenlauf: " if trocken else "") + ("Rückweg in Ordnung" if ok else GRUENDE["rueckweg_fehlgeschlagen"])
        return self._antwort(ok, None if ok else "rueckweg_fehlgeschlagen", text=text, teile=teile,
                             trocken=trocken or None)

    def _rueckweg_eine(self, komp: str, stand: dict, trocken: bool, mit_ausgabe: bool) -> dict:
        eintrag = stand["komponenten"][komp]
        ziel, aktiv = eintrag["letzte_gute"], eintrag["aktiv"]
        teil: dict = {"komponente": komp, "ok": False}
        if ziel is None:
            return {**teil, "grund": "kein_guter_stand"}
        schon_gut = aktiv is not None and all(aktiv[f] == ziel[f] for f in ("version", "commit", "ziel"))
        try:
            argv = [schritt_pruefen(self.p, f"rueckweg-{komp}"), ziel["version"], ziel["commit"], ziel["ziel"]]
            # Gegenprobe unmittelbar vor dem Aufruf (die Werte stammen aus der Datei, nicht aus der Anfrage)
            if not (VERSION_RE.fullmatch(argv[1]) and COMMIT_RE.fullmatch(argv[2]) and ZIEL_RE[komp].fullmatch(argv[3])):
                raise AnkerFehler("zustand_defekt")
        except AnkerFehler as e:
            return {**teil, "grund": e.code, "detail": _sauber(e.detail, 80) if e.detail else None}
        plan = os.path.basename(argv[0]) + " " + " ".join(argv[1:])
        if trocken:
            return {**teil, "ok": True, "geaendert": False, "plan": plan, "schon_gut": schon_gut or None}
        try:
            rc, ausgabe = self.ausfuehren(argv, SCHRITT_ZEIT_S[komp])
        except SchrittFehler as e:
            self.protokolliere("schritt", 1.0, komponente=komp, grund=e.code)
            return {**teil, "grund": e.code, "geaendert": None}
        text = _sauber(ausgabe.decode("utf-8", "replace").replace("\n", " | "), 300) if mit_ausgabe else None
        self.protokolliere("schritt", 1.0, komponente=komp, exit=rc, ausgabe_bytes=len(ausgabe),
                           ausgabe_sha=hashlib.sha256(ausgabe).hexdigest()[:16])
        if rc != 0:
            return {**teil, "grund": "schritt_fehlgeschlagen", "exit": rc, "ausgabe": text}
        if schon_gut:  # Buchführung stimmte schon, nichts neu zu buchen
            return {**teil, "ok": True, "geaendert": False, "hinweis": "Buchführung zeigte schon den letzten guten Stand",
                    "ausgabe": text}
        eintrag["aktiv"] = {**ziel, "seit": zeit_iso(self.uhr())}
        try:
            stand_schreiben(self.p.stand, stand)
        except (OSError, ZustandDefekt):
            # Der Schritt lief, die Datei ließ sich nicht schreiben: der Mensch muss es wissen
            return {**teil, "grund": "zustand_nicht_schreibbar", "geaendert": True}
        return {**teil, "ok": True, "geaendert": True, "ausgabe": text}


# ---------------------------------------------------------------- Dienst
def lese_zeile(conn: socket.socket) -> tuple[bytes, str | None]:
    """Liest genau eine Zeile mit Zeitgrenzen. Ergebnis (Bytes, Fehlercode oder None)."""
    ende = time.monotonic() + GESAMT_ZEIT_S
    puffer = b""
    while b"\n" not in puffer:
        if len(puffer) >= ZEILE_MAX:
            return puffer, "zeile_zu_lang"
        rest = ende - time.monotonic()
        if rest <= 0:
            return puffer, "zeitueberschreitung"
        conn.settimeout(min(rest, LESE_ZEIT_S))
        try:
            teil = conn.recv(ZEILE_MAX - len(puffer) + 1)
        except socket.timeout:
            return puffer, "zeitueberschreitung"
        except OSError:  # Verbindung abgebrochen
            return puffer, "kein_zeilenende"
        if not teil:
            return puffer, "kein_zeilenende"
        puffer += teil
    if puffer.find(b"\n") >= ZEILE_MAX:
        return puffer, "zeile_zu_lang"
    return puffer, None


def antwort_bytes(antwort: dict) -> bytes:
    daten = (json.dumps(antwort, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    if len(daten) > ANTWORT_MAX:
        daten = (json.dumps({"ok": False, "grund": "antwort_zu_gross", "text": GRUENDE["antwort_zu_gross"]})
                 + "\n").encode("ascii")
    return daten


class Server:
    def __init__(self, anker: Anker, modus: int = 0o660, peer: Callable[[socket.socket], Peer | None] = peer_von):
        self.anker = anker
        self.modus = modus
        self.peer = peer
        self._halt = False
        self._srv: socket.socket | None = None

    def binden(self) -> None:
        p = self.anker.p
        if unsicher(p.lauf, True, p.vertrauens_uid):
            raise SystemExit(f"{p.lauf} ist nicht vertrauenswürdig (Besitzer oder Rechte), Abbruch")
        try:
            st = os.lstat(p.socket)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != p.vertrauens_uid:
                raise SystemExit(f"{p.socket} ist kein eigener Socket, Abbruch")
            os.unlink(p.socket)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        alt = os.umask(0o117)
        try:
            srv.bind(p.socket)
            os.chmod(p.socket, self.modus)
            srv.listen(8)
            srv.settimeout(1.0)
        except BaseException:
            srv.close()
            raise
        finally:
            os.umask(alt)
        self._srv = srv

    def bedienen(self, conn: socket.socket) -> None:
        a = self.anker
        peer = self.peer(conn)
        name = a.benutzer(peer)
        if peer is None or name is None:
            antwort = a.abweisen(peer)  # nichts lesen
        else:
            roh, fehler = lese_zeile(conn)
            antwort = a.bearbeite(roh, fehler, peer, name)
        conn.settimeout(LESE_ZEIT_S)
        with contextlib.suppress(OSError):
            conn.sendall(antwort_bytes(antwort))
            conn.shutdown(socket.SHUT_WR)
            conn.setblocking(False)
            conn.recv(4096)  # ungelesene Bytes verwerfen (nicht auswerten), sonst löscht ein Reset beim Schließen die Antwort

    def lauf(self) -> bool:
        """Bedient Verbindungen, bis anhalten() gerufen wird. True: auf Anforderung beendet. False: der Socket ist
        weg (der Dienst darf dann nicht still mit Exit 0 enden). Vorübergehende accept-Fehler (zu viele offene Dateien,
        abgebrochene Verbindung) beenden den Dienst nie, er wartet kurz und macht weiter."""
        if self._srv is None:
            raise RuntimeError("binden() zuerst aufrufen")
        a = self.anker
        a.protokolliere("dienst_start", AUDIT_GRENZE_LESEN, version=VERSION,
                        kette=kette_pruefen(a.p.audit, a.p.vertrauens_uid)[0])
        while not self._halt:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError as e:
                if e.errno in (errno.EBADF, errno.EINVAL, errno.ENOTSOCK):  # Socket geschlossen oder ungültig
                    break
                print(f"wamo-anker: accept fehlgeschlagen ({errno.errorcode.get(e.errno or 0, '?')})", file=sys.stderr)
                time.sleep(0.5)
                continue
            with contextlib.closing(conn):
                try:
                    self.bedienen(conn)
                except Exception as e:  # noqa: BLE001 (eine Verbindung darf den Dienst nie beenden)
                    print(f"wamo-anker: Verbindung fehlgeschlagen ({type(e).__name__})", file=sys.stderr)
        a.protokolliere("dienst_stop", AUDIT_GRENZE_LESEN, grund="angefordert" if self._halt else "socket_weg")
        return self._halt

    def anhalten(self, *_: object) -> None:
        self._halt = True

    def schliessen(self) -> None:
        if self._srv is not None:
            self._srv.close()
            with contextlib.suppress(OSError):
                os.unlink(self.anker.p.socket)


def dienst_main() -> int:
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        print("Der Dienst läuft nur unter Linux als root.", file=sys.stderr)
        return 1
    def fabrik():
        import vorwaerts_echt  # erst beim ersten Vorwärtsbefehl: Datenbank-Treiber und Zugangsdatei sind für Status und Rückweg nicht nötig
        return vorwaerts_echt.vorwaerts_fabrik()

    server = Server(Anker(vorwaerts_fabrik=fabrik))
    server.binden()
    signal.signal(signal.SIGTERM, server.anhalten)
    signal.signal(signal.SIGINT, server.anhalten)
    try:
        angefordert = server.lauf()
    finally:
        server.schliessen()
    return 0 if angefordert else 1  # ohne Anforderung beendet: Fehler, damit systemd neu startet


# ---------------------------------------------------------------- Client und Aufruf
USAGE = ("Aufruf: wamo-anker [--json] status | rueckweg ui|kern|alles [--trocken] | sperre an|aus [--trocken]"
         " | pruefe-sonder <id>\n       wamo-anker selbstpruefung | lokal <befehl> (nur root, wenn der Dienst steht)")


def _kurz(eintrag: dict | None) -> str:
    return f"{eintrag['version']} {eintrag['ziel']} {eintrag['commit'][:8]}" if eintrag else "unbekannt"


def formatiere(a: dict) -> list[str]:
    """Antwort als lesbare Zeilen für den Menschen (die Maschine liest das JSON)."""
    marke = a.get("ergebnis") or ("OK" if a.get("ok") else "ABGELEHNT" if a.get("abgelehnt") else "FEHLER")
    zeilen = [f"{marke} {a.get('text', '')}"]
    if a.get("detail"):
        zeilen.append(f"  Einzelheit: {a['detail']}")
    if "komponenten" in a:
        zg = f" ({a['zustand_grund']})" if a.get("zustand_grund") else ""
        zeilen += [f"  Sperre: {a['sperre']}", f"  Zustandsdatei: {a['zustand']}{zg}"]
        zeilen += [f"  {k}: aktiv {_kurz(e['aktiv'])}, letzte gute {_kurz(e['letzte_gute'])}"
                   for k, e in a["komponenten"].items()]
        au = a["audit"]
        ag = f" ({au['grund']})" if au.get("grund") else ""
        zeilen.append(f"  Audit: Kette {au['kette']}, {au['zeilen']} Zeilen, {au['fuellgrad_prozent']} Prozent voll{ag}")
        pr = "; ".join(a.get("installation_probleme") or [])
        zeilen.append(f"  Installation: {a['installation']}" + (f": {pr}" if pr else ""))
        zeilen.append(f"  Offene Anfragen: {a['offene_anfragen_hinweis']}")
    for t in a.get("teile", []):
        stand = "in Ordnung" if t["ok"] else f"nicht in Ordnung ({t.get('grund')})"
        zeilen.append(f"  {t['komponente']}: {stand}" + (f", {t['plan']}" if t.get("plan") else "")
                      + (f", {t['hinweis']}" if t.get("hinweis") else "")
                      + (", Buchführung zeigt schon den letzten guten Stand, das Skript liefe trotzdem"
                         if t.get("schon_gut") else ""))
    return zeilen


def client(args: list[str], sockpfad: str | None = None, erwartete_uid: int = 0) -> int:
    """Eine Anfrage an den Dienst. Exit 0 bei ok, 1 bei Ablehnung oder Fehler, 2 bei falschem Aufruf oder keiner Antwort.
    sockpfad und erwartete_uid ändern nur die Tests und der Rauchtest (die Gegenstelle muss root gehören)."""
    ausgabe_json = bool(args) and args[0] == "--json"
    teile = args[1:] if ausgabe_json else args
    zeile = (" ".join([secrets.token_hex(16), *teile]) + "\n").encode("ascii", "replace")
    try:
        parse_zeile(zeile)
    except Abgelehnt:
        print(USAGE, file=sys.stderr)
        return 2
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(CLIENT_ZEIT_S)
    try:
        s.connect(sockpfad or PFADE.socket)
        gegenstelle = peer_von(s)
        if gegenstelle is None or gegenstelle.uid != erwartete_uid:  # nur ein Anker von root ist ein Anker
            print("Die Gegenstelle am Socket gehört nicht root, Abbruch.", file=sys.stderr)
            return 2
        s.sendall(zeile)
        s.shutdown(socket.SHUT_WR)
        daten = b""
        while len(daten) <= ANTWORT_MAX:
            teil = s.recv(4096)
            if not teil:
                break
            daten += teil
        antwort = json.loads(daten.decode("ascii"))
    except (OSError, ValueError) as e:
        print(f"Keine Antwort vom Anker ({type(e).__name__}). Läuft der Dienst? Notfall als root: wamo-anker lokal ...",
              file=sys.stderr)
        return 2
    finally:
        s.close()
    if ausgabe_json:
        print(json.dumps(antwort, ensure_ascii=False))
    else:
        print("\n".join(formatiere(antwort)))
    return 0 if antwort.get("ok") else 1


def lokal(args: list[str]) -> int:
    """Notfallweg ohne Dienst, nur root: dieselbe Prüfung und dasselbe Protokoll, nur ohne Socket."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("lokal ist nur für root.", file=sys.stderr)
        return 2
    zeile = (" ".join([secrets.token_hex(16), *args]) + "\n").encode("ascii", "replace")
    antwort = Anker().bearbeite(zeile, None, Peer(os.getpid(), 0, 0), "root")
    print("\n".join(formatiere(antwort)))
    return 0 if antwort.get("ok") else 1


def selbstpruefung() -> int:
    probleme = installation_pruefen(PFADE)
    for t in probleme:
        print(f"Abweichung: {t}")
    print("Installation in Ordnung" if not probleme else "Installation weicht ab")
    return 0 if not probleme else 1


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = argv[1:]
    if args == ["dienst"]:
        return dienst_main()
    if args == ["selbstpruefung"]:
        return selbstpruefung()
    if args[:1] == ["lokal"]:
        return lokal(args[1:])
    return client(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
