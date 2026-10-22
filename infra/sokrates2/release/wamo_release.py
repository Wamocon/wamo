#!/usr/bin/python3 -E -s
"""WAMO-Release-Dienst auf Sokrates-2 (Konzept docs/selbstueberarbeitung.md, Abschnitt D, Schritt 0.3: Gerüst).

Der Dienst wamo-release (unprivilegierter Systembenutzer, kein root) führt eine Release-Anfrage durch den Zustandsautomaten
aus release_automat.py. Er lauscht auf dem Unix-Socket /run/wamo-release/ctl.sock und kennt nur diese Befehle:

    status                              Stand der offenen Anfrage, letzte Anfragen, Sperre des Ankers, Audit-Kette
    anfordern main|selbst <profil>      legt eine Anfrage an (nur für einen aktiven Admin laut Datenbank, höchstens eine offene)
    abbrechen <anfrage>                 beendet die offene Anfrage vor der Bereitstellung
    zurueck ui|kern|alles [--trocken]   Rückweg über den Notanker, beendet auch eine offene Anfrage

Alles andere wird abgewiesen. `freigeben` gibt es hier nie: Freigaben schreibt nur der Freigabe-Dienst des Ankers
(Konzept D.1). In diesem Schritt sind die Stufen (Bauen, Prüfen, Review, Refactor, Bereitstellen) Schnittstellen ohne
Inhalt (release_stufen.py), es gibt keine Datenbankverbindung (release_db.py) und keinen Aufruf eines Modells. Der Dienst
im Betrieb lehnt `anfordern` deshalb mit "datenbank_fehlt" ab. Alles Übrige ist funktionsfähig und getestet: Socket mit
SO_PEERCRED, Audit-Kette, Zustandsautomat, Wiederaufnahme nach einem Absturz, Zeitgrenzen, Sperre des Ankers, Rückweg.

Sicherheitsregeln (jede ist mit einer Probe in voice-core/test_release.py belegt):
  * Gegenstelle: SO_PEERCRED vor dem ersten gelesenen Byte; nur die Benutzer aus ERLAUBT (Namen werden bei jeder
    Verbindung frisch zu uid aufgelöst). Der Kern (wamo-core) darf nur status und anfordern.
  * Eingabe: genau eine Zeile, höchstens ZEILE_MAX Bytes, nur ASCII 0x20 bis 0x7e, Allowlist-Regex je Feld. Kein Pfad und
    kein Befehl aus der Anfrage erreicht eine Shell: Dieser Dienst startet in diesem Schritt keinen Prozess.
  * Der Anker ist nur über seinen Socket erreichbar und nur mit status, rueckweg und sperre an (release_anker.py).
    Nie sperre aus. Ist der Anker nicht erreichbar oder gesperrt, beginnt nichts Neues und keine Stufe startet.
  * Audit-Protokoll mit Hashkette, zugleich Write-Ahead-Log: Eine Zustandsänderung gilt erst, wenn ihre Zeile im Protokoll
    steht (fail closed); nach einem Absturz zieht der Dienst die Zeilen hinter dem Stand der Zustandsdatei nach.
    Ausnahme: Der Rückweg (zurueck) läuft auch ohne beschreibbares Protokoll, denn er ist der Notausgang.
  * Eine Anfrage nach der anderen (die Datenbank sichert es zusätzlich durch einen eindeutigen Index).
  * Zustandsdatei atomar (Rename); eine kaputte Datei wird nie überschrieben, der Dienst beginnt dann nichts Neues.
Nur Standardbibliothek, Linux. Unter Windows laufen die Tests mit Attrappen (voice-core/test_release.py).
"""

from __future__ import annotations

import contextlib
import copy
import errno
import hashlib
import json
import os
import re
import signal
import socket
import stat
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from release_anker import AnkerClient, AnkerFehler
from release_automat import (
    ANFRAGEN_JE_TAG, ANKER_FOLGE, BEOBACHTEN, BEREITGESTELLT_AB, DB_SCHREIBBAR, DB_SPALTEN, ENDZUSTAENDE,
    GESAMT_FRIST_S, GRUND_RE, PRUEFSTAND_PFLICHT, REIHENFOLGE, SCHUTZ_SELBST_FREIGESCHALTET, STUFE_RUNDE,
    TAG_S, UUID_RE, VERSUCHE_MAX, WAL_EREIGNISSE, WEG, ZustandDefekt, anwenden, daten_pruefen, frist_ablauf, leerer_zustand,
    naechster, zustand_pruefen,
)
from release_db import Datenbank, DatenbankAbgelehnt, DatenbankFehler, DatenbankFehlt, OhneDatenbank
from release_kette import (
    AUDIT_GRENZE_LESEN, Audit, AuditFehler, KetteDefekt, ZUSTAND_MAX, audit_ab, audit_zeilen,
    datei_atomar_schreiben, eigene_uid, kette_pruefen, lies_datei, sauber, unsicher, zeit_iso,
)
from release_stufen import Ergebnis, Kontext, NichtVerfuegbar, Stufen, aufruf

VERSION = "0.1"
ZEILE_MAX = 160  # Anfrage samt Zeilenende
ANTWORT_MAX = 8192
LESE_ZEIT_S = 3.0
GESAMT_ZEIT_S = 5.0
CLIENT_ZEIT_S = 420.0
IDS_MAX = 4096
ABLEHN_FENSTER_S = 60.0
ABLEHN_MAX = 20  # protokollierte Zeilen je uid, Grund und Fenster für Abweisungen und Anfragen ohne Wirkung
AENDERN_MAX = 30  # ändernde Anfragen je uid und Fenster
VERBINDUNGEN_MAX = 8  # gleichzeitig bediente Verbindungen erlaubter Benutzer
TOR_ZEIT_S = 3.0  # so lange gilt die Antwort des Ankers zur Sperre für den Start einer Stufe
WARTE_S = 5.0  # Abstand, wenn eine Stufe "wartet" meldet
SPIEGEL_WARTE_S = 10.0  # Abstand nach einem Fehler der Datenbank
ABGLEICH_S = 60.0
STUFE_MAX_S = 7200.0  # längste Zeit für einen Aufruf einer Stufe
HAENGER_S = STUFE_MAX_S + 120.0  # danach gilt der Arbeiter als hängend und der Dienst beendet sich (systemd startet neu)
RUECKWEG_WIEDERHOLUNG_S = 60.0
RUECKWEG_VERSUCHE_MAX = 5
QUELLEN_AKTIV = frozenset({"main"})  # selbst kommt mit Stufe 3, der Agent fehlt in diesem Schritt

try:  # nur Linux
    import pwd
except ImportError:  # pragma: no cover
    pwd = None  # type: ignore[assignment]


class Absturz(BaseException):
    """Nur für Tests: simuliert einen Absturz an einem Haken (läuft durch jedes `except Exception`)."""


# ---------------------------------------------------------------- Pfade
@dataclass(frozen=True)
class Pfade:
    """Feste Orte. Nur die Tests und der Rauchtest bauen sie mit unter() in einem Wegwerf-Ordner auf."""

    lib: str = "/opt/wamo-release/app"
    zustand: str = "/var/lib/wamo-release"
    log: str = "/var/log/wamo-release"
    lauf: str = "/run/wamo-release"
    anker_socket: str = "/run/wamo-anker/anker.sock"
    lib_uid: int = 0  # Besitzer des Codes (root)
    besitzer: int = -1  # Besitzer von Zustand, Protokoll und Laufordner (der Dienstbenutzer); -1: der aufrufende Benutzer
    wurzel: str = "/"

    @classmethod
    def unter(cls, wurzel: str, uid: int | None = None) -> "Pfade":
        eigene = eigene_uid() if uid is None else uid
        return cls(lib=os.path.join(wurzel, "lib"), zustand=os.path.join(wurzel, "var-lib"),
                   log=os.path.join(wurzel, "var-log"), lauf=os.path.join(wurzel, "run"),
                   anker_socket=os.path.join(wurzel, "anker", "anker.sock"), lib_uid=eigene, besitzer=eigene, wurzel=wurzel)

    @property
    def uid(self) -> int:
        return eigene_uid() if self.besitzer < 0 else self.besitzer

    @property
    def zustand_datei(self) -> str:
        return os.path.join(self.zustand, "zustand.json")

    @property
    def audit(self) -> str:
        return os.path.join(self.log, "audit.jsonl")

    @property
    def socket(self) -> str:
        return os.path.join(self.lauf, "ctl.sock")

    @property
    def manifest(self) -> str:
        return os.path.join(self.lib, "SHA256SUMS")


PFADE = Pfade()

# ---------------------------------------------------------------- Befehle und Rechte
ZEILE_RE = re.compile(r"(?P<id>[0-9a-f]{16,32}) (?P<befehl>[a-z][a-z-]{1,19})(?P<args>(?: [a-z0-9][a-z0-9-]{0,39}){0,3})"
                      r"(?P<trocken> --trocken)?")
QUELLE_RE = re.compile(r"selbst|main")
KOMP_RE = re.compile(r"ui|kern|alles")


@dataclass(frozen=True)
class Spez:
    args: tuple = ()  # je Argument ein Muster; Anzahl und Form sind fest
    trocken: bool = False
    schreibend: bool = False  # ändert etwas (dann gehört eine Protokollzeile davor)


BEFEHLE = {
    "status": Spez(),
    "anfordern": Spez(args=(QUELLE_RE, UUID_RE), schreibend=True),
    "abbrechen": Spez(args=(UUID_RE,), schreibend=True),
    "zurueck": Spez(args=(KOMP_RE,), trocken=True, schreibend=True),
}
# Bekannte Befehle, die dieser Dienst nie ausführt; sie bekommen eine deutliche Antwort statt "unbekannt".
BEKANNT_ABGEWIESEN = {"freigeben": "freigabe_nur_beim_anker", "diff": "nicht_verfuegbar"}
# Wer darf was. Namen werden bei jeder Verbindung zu uid aufgelöst; ein fehlender Benutzer passt auf niemanden.
# Der Kern darf nur anfragen und lesen. Menschen kommen über sudo als root; die Personen mit Token (api.sock) kommen mit 1.4.
ERLAUBT = {
    "root": frozenset({"status", "anfordern", "abbrechen", "zurueck"}),
    "wamo-core": frozenset({"status", "anfordern"}),
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
    "freigabe_nur_beim_anker": "Freigaben nimmt nur der Freigabe-Dienst des Ankers entgegen, nie dieser Dienst",
    "nicht_verfuegbar": "Noch nicht verfügbar (kommt mit Stufe 1)",
    "gesperrt": "Selbständerung ist gesperrt (der Anker hält die Sperre, wamo-anker sperre aus hebt sie auf)",
    "anker_nicht_erreichbar": "Der Notanker ist nicht erreichbar oder antwortet unklar, es beginnt nichts Neues",
    "datenbank_fehlt": "Keine Datenbank angeschlossen (kommt mit Stufe 1), es kann keine Anfrage angelegt werden",
    "datenbank_nicht_erreichbar": "Die Datenbank ist nicht erreichbar",
    "datenbank_abgelehnt": "Die Datenbank lehnt die Anfrage ab",
    "kein_admin": "Nur ein aktiver Admin darf einen Release beauftragen",
    "quelle_nicht_freigeschaltet": "Diese Quelle ist in dieser Ausbaustufe nicht freigeschaltet",
    "belegt": "Es läuft schon eine Anfrage (höchstens eine zugleich)",
    "zu_viele_anfragen": "Zu viele Anfragen dieses Admins in 24 Stunden",
    "keine_anfrage": "Es gibt keine offene Anfrage",
    "falsche_anfrage": "Die Kennung gehört nicht zur offenen Anfrage",
    "nicht_abbrechbar": "Die Anfrage ist schon ausgerollt, bitte zurueck benutzen",
    "audit_nicht_schreibbar": "Audit-Protokoll nicht schreibbar, es wurde nichts geändert",
    "zustand_defekt": "Zustandsdatei oder Protokoll sind defekt, es beginnt nichts Neues (Handarbeit nötig)",
    "rueckweg_fehlgeschlagen": "Der Rückweg des Ankers ist nicht gelungen",
    "zu_viele": "Zu viele ändernde Anfragen in kurzer Zeit, bitte später erneut",
    "ausgelastet": "Zu viele gleichzeitige Verbindungen",
    "antwort_zu_gross": "Antwort zu groß",
    "intern": "Interner Fehler, nichts wurde geändert",
}


class MitCode(Exception):
    """Fehler mit einem Code aus GRUENDE; der Code wird beantwortet und protokolliert, nie der Text der Ausnahme."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class Abgelehnt(MitCode):
    """Die Anfrage selbst ist nicht zulässig."""


@dataclass(frozen=True)
class Anfrage:
    id: str
    befehl: str
    args: tuple
    trocken: bool


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
    args = tuple(m["args"].split())
    if m["befehl"] in BEKANNT_ABGEWIESEN:
        return Anfrage(m["id"], m["befehl"], args, bool(m["trocken"]))
    spez = BEFEHLE.get(m["befehl"])
    if spez is None:
        raise Abgelehnt("befehl_unbekannt")
    if len(args) != len(spez.args) or not all(p.fullmatch(a) for p, a in zip(spez.args, args)):
        raise Abgelehnt("argument")
    if m["trocken"] and not spez.trocken:
        raise Abgelehnt("trocken")
    return Anfrage(m["id"], m["befehl"], args, bool(m["trocken"]))


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


# ---------------------------------------------------------------- Installation (Prüfsummen des Codes)
MANIFEST_RE = re.compile(r"([0-9a-f]{64})  (?:\./)?([A-Za-z0-9_][A-Za-z0-9_.-]{0,63})")


def manifest_lesen(p: Pfade) -> dict[str, str]:
    """{Dateiname: sha256} aus lib/SHA256SUMS. Wirft ValueError, OSError."""
    text = lies_datei(p.manifest, 1024 * 1024, p.lib_uid).decode("ascii")
    eintraege: dict[str, str] = {}
    for zeile in text.splitlines():
        m = MANIFEST_RE.fullmatch(zeile)
        if m is None or m[2] in eintraege:
            raise ValueError("Manifest ungültig")
        eintraege[m[2]] = m[1]
    return eintraege


def installation_pruefen(p: Pfade) -> list[str]:
    """Abweichungen der installierten Dateien vom Manifest und unsichere Ordner (leer: in Ordnung). Höchstens 10 Meldungen.
    Der Code liegt flach in lib; Unterordner, Links und Dateien außerhalb des Manifests sind Abweichungen."""
    probleme: list[str] = []
    grund = unsicher(p.lib, True, p.lib_uid)
    if grund:
        return [f"lib: {grund}"]
    if os.name == "posix":  # Elternordner: fremder Besitzer oder für alle beschreibbar erlaubte den Austausch von lib
        ordner, wurzel = os.path.dirname(os.path.normpath(p.lib)), os.path.normpath(p.wurzel)
        while True:
            try:
                st = os.lstat(ordner)
            except OSError:
                probleme.append(f"{ordner}: nicht lesbar")
                break
            if st.st_uid != p.lib_uid:
                probleme.append(f"{ordner}: gehört nicht dem erwarteten Besitzer")
            elif st.st_mode & 0o002 and not st.st_mode & stat.S_ISVTX:
                probleme.append(f"{ordner}: für alle beschreibbar")
            if ordner in (wurzel, os.path.dirname(ordner)):
                break
            ordner = os.path.dirname(ordner)
    try:
        soll = manifest_lesen(p)
    except (ValueError, OSError) as e:
        return [*probleme, f"Manifest: {e}"][:10]
    for name, h in sorted(soll.items()):
        try:
            if hashlib.sha256(lies_datei(os.path.join(p.lib, name), 4 * 1024 * 1024, p.lib_uid)).hexdigest() != h:
                probleme.append(f"{name}: Prüfsumme weicht ab")
        except (ValueError, OSError) as e:
            probleme.append(f"{name}: {e}")
    for eintrag in sorted(os.listdir(p.lib)):
        if eintrag not in soll and eintrag != "SHA256SUMS":
            probleme.append(f"{eintrag}: nicht im Manifest")
    return probleme[:10]


# ---------------------------------------------------------------- Der Dienst
@dataclass
class _Fenster:
    start: float
    anzahl: int = 0
    unterdrueckt: int = 0
    nachlass: int = 0


class Dienst:
    """Zustandsautomat, Befehle und Arbeiter. Alle Fremdsysteme (Anker, Datenbank, Stufen, Uhr) sind austauschbar."""

    def __init__(self, pfade: Pfade = PFADE, anker: AnkerClient | None = None, db: Datenbank | None = None,
                 stufen: Stufen | None = None, erlaubt: dict[str, frozenset] | None = None,
                 uhr: Callable[[], float] = time.time, aufloesen: Callable[[str], int | None] = name_zu_uid,
                 quellen: frozenset = QUELLEN_AKTIV, haken: dict[str, Callable[[], None]] | None = None):
        self.p = pfade
        self.anker = anker or AnkerClient(pfade.anker_socket)
        self.db: Datenbank = db or OhneDatenbank()
        self.stufen: Stufen = stufen or NichtVerfuegbar()
        self.erlaubt = ERLAUBT if erlaubt is None else erlaubt
        self.uhr = uhr
        self.aufloesen = aufloesen
        self.quellen = quellen
        self.haken = haken or {}
        self.audit = Audit(pfade.audit, uhr, pfade.uid)
        self._lock = threading.RLock()  # Zustand und Protokoll
        self._meta = threading.Lock()  # Gedächtnis der Kennungen und der Fenster
        self._anfordern_laeuft = threading.Lock()
        self._spiegel_laeuft = threading.Lock()
        self._spiegel_dran = False  # es gibt Zeilen, die noch nicht in der Datenbank stehen
        self._z = leerer_zustand()
        self._bereit = False  # wiederaufnehmen() ist durchgelaufen und der Zustand ist brauchbar
        self._defekt: str | None = None
        self._ids: dict[str, None] = {}
        self._fenster: dict[tuple[int, str], _Fenster] = {}
        self._abbruch = threading.Event()
        self._tor_stand: tuple[float, str] = (-1e9, "unbekannt")
        self._angehalten: str | None = None
        self._arbeit_seit: float | None = None
        self._naechster_versuch = 0.0
        self._spiegel_naechster = 0.0
        self._spiegel_defekt = False
        self._abgleich_naechster = 0.0
        self._abweichung: str | None = None
        self._gemeldet: set[tuple] = set()
        self._rueckweg_offen: dict | None = None
        self._speicher_fehler: str | None = None
        self._kette_stand: tuple = (None, (True, 0, "", None))
        self._halt = threading.Event()
        self._faeden: list[threading.Thread] = []
        self.haenger = False
        self.tor_zeit = TOR_ZEIT_S  # so lange gilt die Antwort des Ankers zur Sperre (Tests setzen 0)

    # --- Hilfen
    def _haken(self, name: str) -> None:
        f = self.haken.get(name)
        if f is not None:
            f()

    def benutzer(self, peer: Peer | None) -> str | None:
        if peer is None:
            return None
        for name in self.erlaubt:
            if self.aufloesen(name) == peer.uid:
                return name
        return None

    def _jetzt(self) -> int:
        return int(self.uhr())

    # --- Protokoll
    def protokolliere(self, ereignis: str, bis: float = AUDIT_GRENZE_LESEN, **felder: object) -> bool:
        """Eine Zeile ohne Wirkung auf den Zustand. False, wenn sie nicht geschrieben wurde."""
        try:
            with self._lock:
                self.audit.anhaengen(ereignis, bis, **felder)
                if "anfrage" in felder:
                    self._spiegel_dran = True
            return True
        except AuditFehler as e:
            print(f"wamo-release: Audit nicht schreibbar ({sauber(e, 60)})", file=sys.stderr)
            return False

    def _wal(self, ereignis: str, **felder: object) -> int:
        """Eine Zeile des Write-Ahead-Logs: erst auf einer Kopie ausprobieren, dann anhängen, dann anwenden, dann die
        Zustandsdatei schreiben. Wirft AuditFehler (nichts geändert) oder ZustandDefekt (die Zeile passt nicht zum Zustand;
        dann wurde nichts angehängt). Der Aufrufer hält self._lock."""
        zeile = {"ereignis": ereignis, "zeit": self._jetzt(), **{k: v for k, v in felder.items() if v is not None}}
        probe = copy.deepcopy(self._z)
        anwenden(probe, zeile)
        self._haken("vor_audit")
        n = self.audit.anhaengen(ereignis, 1.0, **{k: v for k, v in zeile.items() if k != "ereignis"})
        self._haken("nach_audit")
        anwenden(self._z, zeile)
        self._z["audit_n"] = n
        self._spiegel_dran = True
        self._speichern()
        self._haken("nach_speichern")
        return n

    def _speichern(self) -> bool:
        try:
            zustand_pruefen(self._z)
            daten = (json.dumps(self._z, sort_keys=True, indent=1) + "\n").encode("ascii")
            datei_atomar_schreiben(self.p.zustand_datei, daten)
            self._speicher_fehler = None
            return True
        except OSError as e:  # der Arbeitsstand lebt im Speicher und im Protokoll weiter; die Datei zieht beim nächsten Mal nach
            self._speicher_fehler = errno.errorcode.get(e.errno or 0, "?")
            return False

    def _defekt_setzen(self, grund: str) -> None:
        self._defekt = grund
        self.protokolliere("dienst_defekt", grund=grund)

    # --- Start und Wiederaufnahme
    def _zustand_laden(self) -> dict:
        grund = unsicher(self.p.zustand_datei, False, self.p.uid, privat=True)
        if grund == "fehlt":
            return leerer_zustand()  # frisch, oder verloren: dann baut das Protokoll den Zustand wieder auf
        try:
            daten = lies_datei(self.p.zustand_datei, ZUSTAND_MAX, self.p.uid, privat=True)
            return zustand_pruefen(json.loads(daten.decode("utf-8"), object_pairs_hook=_keine_doppelten))
        except (ValueError, OSError, RecursionError) as e:
            raise ZustandDefekt(f"Zustandsdatei: {sauber(e, 60)}") from e

    def wiederaufnehmen(self) -> None:
        """Beim Start: Zustandsdatei laden, die Zeilen des Protokolls hinter ihrem Stand nachziehen (Write-Ahead-Log),
        mit der Datenbank abgleichen. Ist etwas defekt, beginnt der Dienst nichts Neues (status und zurueck gehen weiter)."""
        angewandt = 0
        with self._lock:
            self._defekt = None
            try:
                z = self._zustand_laden()
                for n, obj, _h in audit_zeilen(self.p.audit, self.p.uid):
                    if n <= z["audit_n"]:
                        continue
                    if obj.get("ereignis") in WAL_EREIGNISSE:
                        anwenden(z, obj)
                        angewandt += 1
                    z["audit_n"] = n
            except ZustandDefekt as e:
                self._defekt = "zustand_defekt"
                print(f"wamo-release: {sauber(e, 80)}", file=sys.stderr)
            except KetteDefekt as e:
                self._defekt = "zustand_defekt"
                print(f"wamo-release: Protokoll defekt ({sauber(e, 80)})", file=sys.stderr)
            else:
                self._z = z
                if angewandt:
                    self._speichern()
            self._bereit = self._defekt is None
        if self._defekt is None:
            self.protokolliere("dienst_start", version=VERSION, nachgezogen=angewandt or None)
            self._abgleichen()
            self._spiegeln()

    # --- Gedächtnis gegen Dauerfeuer (wie im Anker)
    def _budget(self, uid: int, schluessel: str, maximum: int) -> tuple[bool, int]:
        with self._meta:
            jetzt = self.uhr()
            f = self._fenster.get((uid, schluessel))
            if f is None or jetzt - f.start >= ABLEHN_FENSTER_S:
                if len(self._fenster) >= 512:
                    self._fenster.clear()
                f = self._fenster[(uid, schluessel)] = _Fenster(jetzt, nachlass=f.unterdrueckt if f else 0)
            if f.anzahl >= maximum:
                f.unterdrueckt += 1
                return False, 0
            f.anzahl += 1
            nachlass, f.nachlass = f.nachlass, 0
            return True, nachlass

    def _ablehnung(self, peer: Peer | None, name: str | None, code: str, anf: Anfrage | None = None,
                   roh: bytes | None = None) -> dict:
        uid = peer.uid if peer else -1
        erlaubt, nachlass = self._budget(uid, code, ABLEHN_MAX)
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

    @staticmethod
    def _antwort(ok: bool, grund: str | None = None, text: str | None = None, **felder: object) -> dict:
        a: dict = {"ok": ok}
        if grund:
            a["grund"] = grund
        a["text"] = text or (GRUENDE.get(grund, GRUENDE["intern"]) if grund else "in Ordnung")
        a.update({k: v for k, v in felder.items() if v is not None})
        return a

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
        with self._meta:
            if anf.id in self._ids:
                doppelt = True
            else:
                doppelt = False
                self._ids[anf.id] = None
                if len(self._ids) > IDS_MAX:
                    del self._ids[next(iter(self._ids))]
        if doppelt:
            return self._ablehnung(peer, name, "doppelt", anf, roh)
        if anf.befehl in BEKANNT_ABGEWIESEN:
            return self._ablehnung(peer, name, BEKANNT_ABGEWIESEN[anf.befehl], anf, roh)
        if anf.befehl not in self.erlaubt[name]:
            return self._ablehnung(peer, name, "recht", anf, roh)
        aendernd = BEFEHLE[anf.befehl].schreibend and not anf.trocken
        daten = {"uid": peer.uid, "pid": peer.pid, "benutzer": name, "id": anf.id, "befehl": anf.befehl,
                 "arg": " ".join(anf.args) or None, "trocken": anf.trocken or None}
        protokoll = False
        if aendernd:
            if not self._budget(peer.uid, "aendern", AENDERN_MAX)[0]:
                return self._ablehnung(peer, name, "zu_viele", anf, roh)
            protokoll = self.protokolliere("anfrage", 1.0, **daten)
            if not protokoll and anf.befehl != "zurueck":  # der Rückweg ist der Notausgang und läuft auch ohne Protokoll
                return self._antwort(False, "audit_nicht_schreibbar")
        else:
            erlaubt, nachlass = self._budget(peer.uid, "ohne_wirkung", ABLEHN_MAX)
            if erlaubt:
                self.protokolliere("anfrage", AUDIT_GRENZE_LESEN, unterdrueckt=nachlass or None, **daten)
        try:
            antwort = self._ausfuehren(anf, name)
        except MitCode as e:
            antwort = self._antwort(False, e.code, detail=sauber(e.detail, 80) if e.detail else None)
        except Exception as e:  # noqa: BLE001 (nie abstürzen, nie Einzelheiten nach außen)
            print(f"wamo-release: interner Fehler {type(e).__name__}", file=sys.stderr)
            antwort = self._antwort(False, "intern")
        if aendernd:
            self.protokolliere("ergebnis", 1.0, id=anf.id, befehl=anf.befehl, ok=bool(antwort.get("ok")),
                               grund=antwort.get("grund"))
        return antwort

    def _ausfuehren(self, anf: Anfrage, name: str) -> dict:
        if anf.befehl == "status":
            return self.cmd_status()
        if anf.befehl == "anfordern":
            return self.cmd_anfordern(anf.args[0], anf.args[1])
        if anf.befehl == "abbrechen":
            return self.cmd_abbrechen(anf.args[0])
        return self.cmd_zurueck(anf.args[0], anf.trocken)

    # --- Hilfen für Befehle
    def _ansicht(self, a: dict | None) -> dict | None:
        if a is None:
            return None
        d = a["daten"]
        weg = WEG[a["quelle"]]
        return {"id": a["id"], "status": a["status"], "quelle": a["quelle"], "profil": a["profil"], "seq": a["seq"],
                "schritt": weg.index(a["status"]), "schritte": len(weg), "seit": zeit_iso(a["seit"]),
                "frist_bis": zeit_iso(frist_ablauf(a)), "versuch": a["versuch"], "fertig": a["fertig"],
                "version": d.get("version_final"), "klasse": d.get("klasse")}

    def _vorwaerts_pruefen(self) -> None:
        if self._defekt or not self._bereit:
            raise MitCode("zustand_defekt", self._defekt)

    def _kette(self) -> tuple[bool, int, str, str | None]:
        """Prüfung der ganzen Kette, nur neu gerechnet, wenn sich die Datei geändert hat."""
        try:
            st = os.stat(self.p.audit)
            schluessel: object = (st.st_size, st.st_mtime_ns, st.st_ino)
        except OSError:
            schluessel = None
        if self._kette_stand[0] != schluessel or schluessel is None:
            self._kette_stand = (schluessel, kette_pruefen(self.p.audit, self.p.uid))
        return self._kette_stand[1]

    # --- status
    def cmd_status(self) -> dict:
        with self._lock:
            z = copy.deepcopy(self._z)
            defekt, bereit = self._defekt, self._bereit
        ok, zeilen, letzter, grund = self._kette()
        try:
            self.db.offene_anfragen()
            datenbank = "angeschlossen"
        except DatenbankFehlt:
            datenbank = "fehlt"
        except DatenbankFehler:
            datenbank = "nicht_erreichbar"
        sperre = self.anker.sperre_zustand()
        probleme = installation_pruefen(self.p)
        arbeit = "frei" if self._arbeit_seit is None else ("haengt" if self.haenger else "beschaeftigt")
        return self._antwort(
            True, text="Release-Dienst läuft", version=VERSION, zeit=zeit_iso(self.uhr()), modus="geruest",
            bereit=bereit and defekt is None, defekt=defekt, anfrage=self._ansicht(z["anfrage"]),
            abgeschlossen=[{k: e[k] for k in ("id", "status", "grund")} for e in z["abgeschlossen"][-5:]],
            angehalten=self._angehalten,
            anker={"erreichbar": sperre != "unbekannt", "sperre": sperre}, datenbank=datenbank, arbeiter=arbeit,
            audit={"kette": "ok" if ok else "defekt", "zeilen": zeilen, "letzter_hash": letzter, "grund": grund,
                   "fuellgrad_prozent": round(self.audit.fuellgrad() * 100, 1)},
            spiegel={"rueckstand": self._rueckstand(z), "defekt": self._spiegel_defekt or None},
            abweichung=self._abweichung, rueckweg_offen=self._rueckweg_offen is not None or None,
            speicherfehler=self._speicher_fehler,
            installation="ok" if not probleme else "abweichung", installation_probleme=probleme or None)

    def _rueckstand(self, z: dict) -> int:
        return max(0, z["audit_n"] - z["spiegel_n"])

    # --- anfordern
    def cmd_anfordern(self, quelle: str, profil: str) -> dict:
        if not self._anfordern_laeuft.acquire(blocking=False):
            raise MitCode("belegt")
        try:
            self._vorwaerts_pruefen()
            if quelle not in self.quellen:
                raise MitCode("quelle_nicht_freigeschaltet")
            sperre = self.anker.sperre_zustand()  # frisch, nie aus dem Zwischenspeicher
            if sperre == "unbekannt":
                raise MitCode("anker_nicht_erreichbar")
            if sperre != "aus":
                raise MitCode("gesperrt")
            with self._lock:
                if self._z["anfrage"] is not None or self._abweichung:
                    raise MitCode("belegt")
                gestern = self._jetzt() - TAG_S
                zahl = sum(1 for e in self._z["abgeschlossen"] if e["profil"] == profil and e["start"] >= gestern)
                if zahl >= ANFRAGEN_JE_TAG:
                    raise MitCode("zu_viele_anfragen")
            try:
                if not self.db.ist_admin(profil):
                    raise MitCode("kein_admin")
                kennung = self.db.anfrage_anlegen(profil, quelle)
            except DatenbankFehlt as e:
                raise MitCode("datenbank_fehlt") from e
            except DatenbankFehler as e:
                raise MitCode("datenbank_nicht_erreichbar") from e
            except DatenbankAbgelehnt as e:
                raise MitCode({"23505": "belegt", "42501": "kein_admin"}.get(e.code, "datenbank_abgelehnt"),
                              e.code) from e
            self._haken("nach_db_anlegen")
            with self._lock:
                try:
                    self._wal("angefordert", anfrage=kennung, profil=profil, quelle=quelle)
                except (AuditFehler, ZustandDefekt) as e:
                    self._verwaisung_beenden(kennung)
                    raise MitCode("audit_nicht_schreibbar" if isinstance(e, AuditFehler) else "zustand_defekt") from e
                ansicht = self._ansicht(self._z["anfrage"])
            self._spiegeln()
            return self._antwort(True, text="Anfrage angelegt", anfrage=ansicht)
        finally:
            self._anfordern_laeuft.release()

    def _verwaisung_beenden(self, kennung: str) -> None:
        """Die Datenbank kennt die Anfrage schon, das Protokoll nicht: aufräumen, soweit die Datenbank es zulässt."""
        with contextlib.suppress(DatenbankFehler, DatenbankAbgelehnt):
            self.db.status_setzen(kennung, "abgebrochen", {})

    # --- Übergänge
    def _uebergang(self, erwartet: dict, nach: str, beleg: str, grund: str | None = None, daten: dict | None = None,
                   detail: str | None = None) -> bool:
        """Ein Übergang mit Vergleich: Kennung, Zustand und Zähler der Anfrage müssen noch so sein wie erwartet (eine
        verspätete Stufe ändert nichts). True, wenn er stattfand. Wirft nichts; ein Fehler macht den Dienst defekt."""
        with self._lock:
            a = self._z["anfrage"]
            if a is None or a["id"] != erwartet["id"] or a["status"] != erwartet["status"] or a["seq"] != erwartet["seq"]:
                return False
            try:
                self._wal("uebergang", anfrage=a["id"], von=a["status"], nach=nach, seq=a["seq"] + 1, beleg=beleg,
                          grund=grund, daten=daten or None, detail=sauber(detail, 40) if detail else None)
            except AuditFehler:
                return False
            except ZustandDefekt:
                self._defekt_setzen("zustand_defekt")
                return False
        return True

    def _beenden(self, erwartet: dict, grund: str, beleg: str, detail: str | None = None) -> bool:
        self._abbruch.set()
        return self._uebergang(erwartet, "abgebrochen", beleg, grund, None, detail)

    # --- abbrechen
    def cmd_abbrechen(self, kennung: str) -> dict:
        with self._lock:
            a = copy.deepcopy(self._z["anfrage"])
        if a is None:
            raise MitCode("keine_anfrage")
        if a["id"] != kennung:
            raise MitCode("falsche_anfrage")
        if REIHENFOLGE.index(a["status"]) >= BEREITGESTELLT_AB:
            raise MitCode("nicht_abbrechbar")
        self._abbruch.set()  # eine laufende Stufe soll zügig zurückkehren
        if not self._uebergang(a, "abgebrochen", "abbruch", "abbruch_person"):
            raise MitCode("audit_nicht_schreibbar" if not self._defekt else "zustand_defekt")
        self._spiegeln()
        return self._antwort(True, text="Anfrage abgebrochen", anfrage=kennung)

    # --- zurueck
    def cmd_zurueck(self, komponente: str, trocken: bool) -> dict:
        """Der Rückweg ist von jedem Zustand aus erreichbar: auch ohne offene Anfrage, auch mit defektem Zustand, auch mit
        nicht beschreibbarem Protokoll. Die Arbeit macht der Anker; dieser Dienst beendet danach die offene Anfrage."""
        with self._lock:
            a = copy.deepcopy(self._z["anfrage"])
        ausgerollt = a is not None and REIHENFOLGE.index(a["status"]) >= BEREITGESTELLT_AB
        beendet = None
        if a is not None and not ausgerollt and not trocken:
            beendet = self._beenden(a, "zurueck", "zurueck")  # vor der Bereitstellung gibt es nichts zurückzurollen
        try:
            ant = self.anker.rueckweg(komponente, trocken)
        except AnkerFehler as e:
            if a is not None and ausgerollt and not trocken:
                self._rueckweg_gescheitert(a, e.grund)
            self._spiegeln()
            return self._antwort(False, "anker_nicht_erreichbar", detail=e.grund, anfrage_beendet=beendet or None)
        if not trocken and a is not None and ausgerollt:
            if ant.ok:
                beendet = self._uebergang(a, "zurueckgenommen", "zurueck", "zurueck")
                self._rueckweg_offen = None
            else:
                self._rueckweg_gescheitert(a, ant.grund or "unbekannt")
        teile = ant.daten.get("teile")
        self._spiegeln()
        return self._antwort(ant.ok, None if ant.ok else "rueckweg_fehlgeschlagen",
                             text=("Trockenlauf: " if trocken else "") + ("Rückweg in Ordnung" if ant.ok else GRUENDE[
                                 "rueckweg_fehlgeschlagen"]),
                             anker_grund=ant.grund, anker_teile=teile if isinstance(teile, list) else None,
                             anfrage_beendet=beendet or None, trocken=trocken or None)

    def _rueckweg_gescheitert(self, a: dict, grund: str) -> None:
        """Nach der Bereitstellung gelang der Rückweg nicht: festhalten, weitere Versuche planen, nichts Neues zulassen."""
        erster = self._rueckweg_offen is None
        self._rueckweg_offen = {"anfrage": a["id"], "versuche": (self._rueckweg_offen or {}).get("versuche", 0) + 1,
                                "naechster": self.uhr() + RUECKWEG_WIEDERHOLUNG_S}
        self.protokolliere("rueckweg_fehlgeschlagen", 1.0, anfrage=a["id"], grund=sauber(grund, 40))
        if erster:
            self._auto_sperre("rueckweg_fehlgeschlagen", a["id"])

    def _auto_sperre(self, grund: str, anfrage: str | None = None) -> None:
        """Setzt die Sperre des Ankers, damit nichts mehr nach vorn geht (nie sperre aus). Beste Mühe, protokolliert."""
        try:
            ant = self.anker.sperre_an()
            ok = ant.ok
        except AnkerFehler:
            ok = False
        self._tor_stand = (-1e9, "unbekannt")
        self.protokolliere("sperre_an", 1.0, grund=grund, anfrage=anfrage, ok=ok)

    # --- Arbeiter
    def _sperre_gecached(self) -> str:
        jetzt = time.monotonic()
        if jetzt - self._tor_stand[0] >= self.tor_zeit:
            self._tor_stand = (jetzt, self.anker.sperre_zustand())
        return self._tor_stand[1]

    def _tor(self) -> str | None:
        """None, wenn eine Stufe starten darf, sonst der Grund für das Anhalten."""
        s = self._sperre_gecached()
        if s == "unbekannt":
            return "anker"
        return None if s == "aus" else "sperre"

    def arbeite_einmal(self) -> str:
        """Ein Schritt für die offene Anfrage. Kehrt nach einer Stufe zurück. Ergebnis: leer | angehalten | wartet |
        gearbeitet | veraendert | audit | defekt (für Tests und die Statusanzeige)."""
        try:
            return self._arbeiten()
        finally:
            self._spiegeln()

    def _arbeiten(self) -> str:
        with self._lock:
            if self._defekt or not self._bereit:
                return "defekt"
            a = copy.deepcopy(self._z["anfrage"])
        if a is None:
            return "leer"
        if self.uhr() < self._naechster_versuch:
            return "wartet"
        grund = self._tor()
        self._angehalten = grund
        if grund:
            return "angehalten"
        status = a["status"]
        if status in BEOBACHTEN or (a["fertig"] and status in ANKER_FOLGE):
            return self._beobachten(a)
        if status == "angefragt":
            return "gearbeitet" if self._uebergang(a, naechster(status, a["quelle"]), "stufe") else "audit"
        fn = aufruf(self.stufen, status)
        if fn is None:
            self._beenden(a, "stufe_nicht_verfuegbar", "fehler")
            return "gearbeitet"
        return self._stufe(a, fn)

    def _stufe(self, a: dict, fn: Callable[[Kontext], Ergebnis]) -> str:
        with self._lock:
            cur = self._z["anfrage"]
            if cur is None or cur["id"] != a["id"] or cur["status"] != a["status"] or cur["seq"] != a["seq"]:
                return "veraendert"
            if cur["versuch"] >= VERSUCHE_MAX:
                self._beenden(a, "wiederaufnahme_erschoepft", "fehler")
                return "gearbeitet"
            versuch = cur["versuch"] + 1
            try:
                self._wal("stufe_start", anfrage=a["id"], status=a["status"], versuch=versuch)
            except AuditFehler:
                return "audit"
            except ZustandDefekt:
                self._defekt_setzen("zustand_defekt")
                return "defekt"
            self._abbruch = ereignis = threading.Event()
            rest = max(1.0, min(frist_ablauf(a) - self.uhr(), STUFE_MAX_S))
            self._arbeit_seit = time.monotonic()
            ctx = Kontext(anfrage=a["id"], quelle=a["quelle"], profil=a["profil"], status=a["status"], versuch=versuch,
                          daten=copy.deepcopy(a["daten"]), frist=time.monotonic() + rest, abbruch=ereignis)
        self._haken("in_stufe")
        ergebnis: object = None
        ausnahme: str | None = None
        try:
            ergebnis = fn(ctx)
        except Exception as e:  # noqa: BLE001 (eine Stufe darf den Dienst nie beenden)
            ausnahme = type(e).__name__
        finally:
            self._arbeit_seit = None
        return self._ergebnis(a, ergebnis, ausnahme)

    def _ergebnis(self, a: dict, ergebnis: object, ausnahme: str | None) -> str:
        """Wertet das Ergebnis einer Stufe aus. Alles, was nicht eindeutig in Ordnung ist, beendet die Anfrage (fail closed)."""
        grund: str | None = None
        detail: str | None = None
        daten: dict = {}
        if ausnahme:
            grund, detail = "stufe_ausnahme", ausnahme
        elif not isinstance(ergebnis, Ergebnis) or ergebnis.art not in ("ok", "fehler", "wartet"):
            grund = "stufe_ungueltig"
        elif ergebnis.art == "fehler":
            grund = "stufe_fehler" if ergebnis.grund != "stufe_nicht_verfuegbar" else "stufe_nicht_verfuegbar"
            detail = ergebnis.grund if isinstance(ergebnis.grund, str) and GRUND_RE.fullmatch(ergebnis.grund) else None
        elif ergebnis.art == "wartet":
            self._naechster_versuch = self.uhr() + WARTE_S
            return "wartet"
        else:
            try:
                daten = daten_pruefen(ergebnis.daten)
            except ValueError:
                grund = "stufe_ungueltig"
        if grund is None:
            grund = self._tor_ergebnis(a, daten)
            if grund == "wiederholen":
                self._naechster_versuch = self.uhr() + WARTE_S
                return "wartet"
        with self._lock:
            cur = self._z["anfrage"]
            if cur is None or cur["id"] != a["id"] or cur["status"] != a["status"] or cur["seq"] != a["seq"]:
                self.protokolliere("ergebnis_verworfen", 1.0, anfrage=a["id"], status=a["status"])
                return "veraendert"
            try:
                self._wal("stufe_ende", anfrage=a["id"], status=a["status"], ok=(grund is None) or None,
                          grund=grund, detail=sauber(detail, 40) if detail else None)
            except AuditFehler:
                return "audit"
            except ZustandDefekt:
                self._defekt_setzen("zustand_defekt")
                return "defekt"
            erwartet = copy.deepcopy(self._z["anfrage"])
        if grund is not None:
            self._beenden(erwartet, grund, "fehler", detail)
            return "gearbeitet"
        if a["status"] in ANKER_FOLGE:  # der Anker setzt den nächsten Zustand, der Dienst beobachtet ihn nur
            return "gearbeitet"
        self._uebergang(erwartet, naechster(a["status"], a["quelle"]), "stufe", None, daten or None)
        return "gearbeitet"

    def _tor_ergebnis(self, a: dict, daten: dict) -> str | None:
        """Tore nach einer Stufe: Der Dienst verlangt bestimmte Werte, bevor die Anfrage weitergeht. Ergebnis: Endgrund,
        'wiederholen' (die Datenbank war nicht erreichbar) oder None."""
        status = a["status"]
        alle = {**a["daten"], **daten}
        if status in STUFE_RUNDE:
            stand = daten.get("pruefstand")
            pflicht = PRUEFSTAND_PFLICHT[STUFE_RUNDE[status]]
            if stand is None or any(name not in stand for name in pflicht):
                return "pruefstand_unvollstaendig"
            if any(stand[name] != "ok" for name in pflicht):
                return "pruefung_fehlgeschlagen"
        if status == "uebernommen" and not all(k in alle for k in ("basis_commit", "c1_commit")):
            return "daten_unvollstaendig"
        if status == "refactor" and not ("r_commit" in alle or alle.get("refactor_verworfen")):
            return "daten_unvollstaendig"
        if status == "versionswahl" and not all(k in alle for k in ("bump", "version_vorschlag", "version_final",
                                                                      "v_commit", "tree_id")):
            return "daten_unvollstaendig"
        if status == "pruefung3":
            klasse = daten.get("klasse")
            if klasse is None:
                return "klassifizierung_fehlt"
            if (daten.get("treffer_anzahl", 0) > 0) != (klasse != "normal"):
                return "stufe_ungueltig"  # Klasse und Treffer müssen zusammenpassen
            if klasse == "gesperrt" or (klasse == "sonder" and a["quelle"] == "selbst" and not SCHUTZ_SELBST_FREIGESCHALTET):
                return "schutzschicht_gesperrt"
            if klasse == "sonder":  # ein anderer Admin muss freigeben können: unter zwei aktiven Admins bleibt es gesperrt
                try:
                    if self.db.anzahl_admins() < 2:
                        return "zweiter_admin_noetig"
                except DatenbankFehlt:
                    return "zweiter_admin_noetig"
                except DatenbankFehler:
                    return "wiederholen"
            if not all(k in alle for k in ("v_commit", "tree_id", "version_final")):
                return "daten_unvollstaendig"
        return None

    # --- Beobachtung der Datenbank (Zustände des Ankers)
    def _beobachten(self, a: dict) -> str:
        try:
            st = self.db.status_lesen(a["id"])
        except DatenbankFehler:
            return "wartet"
        if st is None:
            self._beenden(a, "verwaist", "fehler")
            return "gearbeitet"
        s = st.get("status")
        if s == a["status"]:
            return "wartet"
        ausgerollt = REIHENFOLGE.index(a["status"]) >= BEREITGESTELLT_AB
        folge = naechster(a["status"], a["quelle"])
        if s == folge and folge in ("freigegeben", "bereitgestellt", "umgeschaltet", "bewaehrt"):
            if folge == "freigegeben" and (st.get("v_commit") != a["daten"].get("v_commit")
                                          or st.get("tree_id") != a["daten"].get("tree_id")):
                if self._beenden(a, "freigabe_abweichung", "fehler"):
                    self._auto_sperre("freigabe_abweichung", a["id"])
                return "gearbeitet"
            self._uebergang(a, folge, "datenbank")
            return "gearbeitet"
        if s == "zurueckgenommen" and ausgerollt:
            self._uebergang(a, "zurueckgenommen", "datenbank", "zurueck")
            return "gearbeitet"
        if s == "abgebrochen" and not ausgerollt:
            self._beenden(a, "abgebrochen_extern", "abbruch")
            return "gearbeitet"
        if s in ENDZUSTAENDE or (a["status"], s) not in self._gemeldet:
            self._gemeldet.add((a["status"], s))
            self.protokolliere("db_abweichung", 1.0, anfrage=a["id"], lokal=a["status"], db=sauber(s, 20))
        return "wartet"

    # --- Wache: Zeitgrenzen, Spiegel, Abgleich, Hänger
    def wache(self) -> None:
        """Zeitgrenzen, Wiederholung des Rückwegs, Abgleich und Spiegel. Blockiert nie lange."""
        jetzt = self.uhr()
        with self._lock:
            a = copy.deepcopy(self._z["anfrage"]) if self._bereit and not self._defekt else None
        if a is not None:
            if jetzt - a["start"] > GESAMT_FRIST_S:
                self._frist_abgelaufen(a, "gesamtzeitlimit")
            elif jetzt > frist_ablauf(a):
                self._frist_abgelaufen(a, "zeitlimit")
        if self._arbeit_seit is not None and time.monotonic() - self._arbeit_seit > HAENGER_S and not self.haenger:
            self.haenger = True
            self.protokolliere("haenger", 1.0)
        if self._rueckweg_offen and jetzt >= self._rueckweg_offen["naechster"]:
            self._rueckweg_wiederholen()
        if jetzt >= self._abgleich_naechster and self._bereit and not self._defekt:
            self._abgleich_naechster = jetzt + ABGLEICH_S
            self._abgleichen()
        if jetzt >= self._spiegel_naechster:
            self._spiegeln()

    def _frist_abgelaufen(self, a: dict, grund: str) -> None:
        if REIHENFOLGE.index(a["status"]) < BEREITGESTELLT_AB:
            self._beenden(a, grund, "zeit")
            return
        # Ausgerollt, aber nicht bewährt: Der sichere Zustand ist der letzte gute Stand.
        if self._rueckweg_offen is not None:
            return
        try:
            ant = self.anker.rueckweg("alles")
            ok = ant.ok
            antwort_grund = ant.grund or "unbekannt"
        except AnkerFehler as e:
            ok, antwort_grund = False, e.grund
        if ok:
            self._uebergang(a, "zurueckgenommen", "zurueck", "rueckweg_nach_zeitlimit")
        else:
            self._rueckweg_gescheitert(a, antwort_grund)

    def _rueckweg_wiederholen(self) -> None:
        offen = self._rueckweg_offen
        if offen is None:
            return
        with self._lock:
            a = copy.deepcopy(self._z["anfrage"])
        if a is None or a["id"] != offen["anfrage"]:
            self._rueckweg_offen = None
            return
        if offen["versuche"] >= RUECKWEG_VERSUCHE_MAX:
            return  # Handarbeit: wamo-anker rueckweg alles; der Status zeigt rueckweg_offen
        try:
            ant = self.anker.rueckweg("alles")
            ok, grund = ant.ok, ant.grund or "unbekannt"
        except AnkerFehler as e:
            ok, grund = False, e.grund
        if ok:
            self._rueckweg_offen = None
            self._uebergang(a, "zurueckgenommen", "zurueck", "rueckweg_nach_zeitlimit")
        else:
            offen["versuche"] += 1
            offen["naechster"] = self.uhr() + RUECKWEG_WIEDERHOLUNG_S
            self.protokolliere("rueckweg_fehlgeschlagen", 1.0, anfrage=a["id"], grund=sauber(grund, 40))

    # --- Abgleich mit der Datenbank
    def _abgleichen(self) -> None:
        """Stimmt die Datenbank mit dem Zustand überein? Eine Anfrage, die nur die Datenbank kennt (Absturz zwischen
        Anlegen und Protokoll), wird beendet, solange sie noch am Anfang steht; alles andere ist eine Abweichung, die
        neue Anfragen sperrt, bis ein Mensch nachgesehen hat."""
        if self._anfordern_laeuft.locked():
            return  # eine Anfrage entsteht gerade: die Datenbank kennt sie schon, das Protokoll noch nicht
        try:
            offene = self.db.offene_anfragen()
        except DatenbankFehler:
            return
        with self._lock:
            lokal = copy.deepcopy(self._z["anfrage"])
        bekannt = {o["id"] for o in offene}
        abweichung = None
        for o in offene:
            if lokal is not None and o["id"] == lokal["id"]:
                continue
            if o["status"] == "angefragt":
                try:
                    self.db.status_setzen(o["id"], "abgebrochen", {})
                    self.protokolliere("verwaist_abgebrochen", 1.0, anfrage=o["id"])
                    continue
                except (DatenbankFehler, DatenbankAbgelehnt):
                    pass
            abweichung = f"db_kennt_{sauber(o['status'], 20)}"
        if lokal is not None and lokal["id"] not in bekannt:
            try:
                st = self.db.status_lesen(lokal["id"])
            except DatenbankFehler:
                st = {"status": "?"}
            if st is None or st.get("status") in ENDZUSTAENDE:
                ausgerollt = REIHENFOLGE.index(lokal["status"]) >= BEREITGESTELLT_AB
                if not ausgerollt:
                    self._beenden(lokal, "abgebrochen_extern" if st else "verwaist", "abbruch")
        if abweichung and abweichung != self._abweichung:
            self.protokolliere("db_abweichung", 1.0, art=abweichung)
        self._abweichung = abweichung

    # --- Spiegel in die Datenbank (release_ereignisse)
    def _spiegeln(self) -> None:
        """Gibt die Zeilen des Protokolls hinter spiegel_n an die Datenbank weiter, in Reihenfolge und mindestens einmal.
        Fällt die Datenbank aus, bleibt der Rest liegen und wird später nachgeholt; eine dauerhafte Ablehnung
        überspringt die Zeile (und steht im Protokoll)."""
        if not self._spiegel_dran or not self._spiegel_laeuft.acquire(blocking=False):
            return
        try:
            if not self._bereit or self._spiegel_defekt or self.uhr() < self._spiegel_naechster:
                return
            while True:
                self._spiegel_dran = False
                with self._lock:
                    n0, offset = self._z["spiegel_n"], self._z["spiegel_offset"]
                try:
                    zeilen = audit_ab(self.p.audit, offset, n0, self.p.uid, 20)
                except KetteDefekt:
                    self._spiegel_defekt = True
                    return
                if not zeilen:
                    return
                for n, obj, ende in zeilen:
                    self._haken("vor_spiegel")
                    try:
                        self._zeile_spiegeln(obj)
                    except DatenbankFehler:
                        self._spiegel_naechster = self.uhr() + SPIEGEL_WARTE_S
                        self._spiegel_dran = True
                        return
                    except DatenbankAbgelehnt as e:
                        self.protokolliere("spiegel_abgelehnt", 1.0, zeile=n, code=sauber(e.code, 8))
                    with self._lock:  # jede Zeile einzeln festhalten: nach einem Absturz geht nichts doppelt hinaus
                        self._z["spiegel_n"], self._z["spiegel_offset"] = n, ende
                        self._speichern()
        finally:
            self._spiegel_laeuft.release()

    def _zeile_spiegeln(self, obj: dict) -> None:
        anfrage = obj.get("anfrage")
        if anfrage is None:
            return  # nicht anfragebezogen (Abweisungen, Start, Stopp) bleibt im lokalen Protokoll
        ereignis = obj["ereignis"]
        if ereignis == "uebergang" and obj.get("nach") in DB_SCHREIBBAR and obj.get("beleg") != "datenbank":
            felder = {k: v for k, v in obj.get("daten", {}).items() if k in DB_SPALTEN}
            self.db.status_setzen(anfrage, obj["nach"], felder)
        daten = {k: v for k, v in obj.items() if k not in ("anfrage", "ereignis", "zeit", "vorher", "hash", "t")}
        daten["quelle_n"] = daten.pop("n")
        if len(json.dumps(daten)) > 3000:
            daten = {"quelle_n": daten["quelle_n"], "gekuerzt": True}
        self.db.ereignis_anhaengen(anfrage, ereignis, daten, obj.get("profil") if ereignis == "angefordert" else None)

    # --- Hintergrund
    def starte_hintergrund(self) -> None:
        def schleife(fn: Callable[[], object], takt: float) -> None:
            while not self._halt.is_set():
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 (der Arbeiter darf nie sterben)
                    print(f"wamo-release: {fn.__name__} fehlgeschlagen ({type(e).__name__})", file=sys.stderr)
                self._halt.wait(takt)
        self._faeden = [threading.Thread(target=schleife, args=(self.arbeite_einmal, 0.5), daemon=True),
                        threading.Thread(target=schleife, args=(self.wache, 1.0), daemon=True)]
        for f in self._faeden:
            f.start()

    def stoppe_hintergrund(self) -> None:
        self._halt.set()
        self._abbruch.set()
        for f in self._faeden:
            f.join(5.0)


def _keine_doppelten(paare: list) -> dict:
    d = dict(paare)
    if len(d) != len(paare):
        raise ValueError("doppelter Schlüssel")
    return d


# ---------------------------------------------------------------- Server
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
    def __init__(self, dienst: Dienst, modus: int = 0o660, peer: Callable[[socket.socket], Peer | None] = peer_von):
        self.dienst = dienst
        self.modus = modus
        self.peer = peer
        self._halt = False
        self._srv: socket.socket | None = None
        self._platz = threading.BoundedSemaphore(VERBINDUNGEN_MAX)
        self._laufend: set[threading.Thread] = set()

    def binden(self) -> None:
        p = self.dienst.p
        if unsicher(p.lauf, True, p.uid):
            raise SystemExit(f"{p.lauf} ist nicht vertrauenswürdig (Besitzer oder Rechte), Abbruch")
        try:
            st = os.lstat(p.socket)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != p.uid:
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

    @staticmethod
    def _antworten(conn: socket.socket, antwort: dict) -> None:
        conn.settimeout(LESE_ZEIT_S)
        with contextlib.suppress(OSError):
            conn.sendall(antwort_bytes(antwort))
            conn.shutdown(socket.SHUT_WR)
            conn.setblocking(False)
            conn.recv(4096)  # ungelesene Bytes verwerfen (nicht auswerten), sonst löscht ein Reset die Antwort

    def bedienen(self, conn: socket.socket) -> None:
        """Eine Verbindung vollständig: Gegenstelle prüfen, lesen, bearbeiten, antworten."""
        d = self.dienst
        peer = self.peer(conn)
        name = d.benutzer(peer)
        if peer is None or name is None:
            antwort = d.abweisen(peer)  # nichts lesen
        else:
            roh, fehler = lese_zeile(conn)
            antwort = d.bearbeite(roh, fehler, peer, name)
        self._antworten(conn, antwort)

    def _bedienen_thread(self, conn: socket.socket) -> None:
        try:
            with contextlib.closing(conn):
                try:
                    self.bedienen(conn)
                except Exception as e:  # noqa: BLE001 (eine Verbindung darf den Dienst nie beenden)
                    print(f"wamo-release: Verbindung fehlgeschlagen ({type(e).__name__})", file=sys.stderr)
        finally:
            self._platz.release()
            self._laufend.discard(threading.current_thread())

    def lauf(self) -> bool:
        """Nimmt Verbindungen an, bis anhalten() gerufen wird. True: auf Anforderung beendet. False: der Socket ist weg
        oder der Arbeiter hängt (der Dienst darf dann nicht still mit Exit 0 enden, systemd startet neu). Fremde
        Gegenstellen werden sofort abgewiesen und belegen keinen Platz; erlaubte bekommen je einen Faden, höchstens
        VERBINDUNGEN_MAX zugleich, damit ein Rückweg die Anzeige nicht aufhält."""
        if self._srv is None:
            raise RuntimeError("binden() zuerst aufrufen")
        d = self.dienst
        while not self._halt and not d.haenger:
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError as e:
                if e.errno in (errno.EBADF, errno.EINVAL, errno.ENOTSOCK):  # Socket geschlossen oder ungültig
                    break
                print(f"wamo-release: accept fehlgeschlagen ({errno.errorcode.get(e.errno or 0, '?')})", file=sys.stderr)
                time.sleep(0.5)
                continue
            peer = self.peer(conn)
            if peer is None or d.benutzer(peer) is None:
                with contextlib.closing(conn):
                    self._antworten(conn, d.abweisen(peer))
                continue
            if not self._platz.acquire(blocking=False):
                with contextlib.closing(conn):
                    self._antworten(conn, {"ok": False, "abgelehnt": True, "grund": "ausgelastet",
                                           "text": GRUENDE["ausgelastet"]})
                continue
            t = threading.Thread(target=self._bedienen_thread, args=(conn,), daemon=True)
            self._laufend.add(t)
            t.start()
        d.protokolliere("dienst_stop", AUDIT_GRENZE_LESEN,
                        grund="haenger" if d.haenger else ("angefordert" if self._halt else "socket_weg"))
        return self._halt and not d.haenger

    def anhalten(self, *_: object) -> None:
        self._halt = True

    def schliessen(self, warten: float = 330.0) -> None:
        """Wartet auf laufende Verbindungen (ein Rückweg darf zu Ende laufen), schließt dann den Socket."""
        ende = time.monotonic() + warten
        for t in list(self._laufend):
            t.join(max(0.0, ende - time.monotonic()))
        if self._srv is not None:
            self._srv.close()
            with contextlib.suppress(OSError):
                os.unlink(self.dienst.p.socket)


def dienst_main() -> int:
    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        print("Der Dienst läuft nur unter Linux und nie als root.", file=sys.stderr)
        return 1
    dienst = Dienst()
    dienst.wiederaufnehmen()
    server = Server(dienst)
    server.binden()
    signal.signal(signal.SIGTERM, server.anhalten)
    signal.signal(signal.SIGINT, server.anhalten)
    dienst.starte_hintergrund()
    try:
        angefordert = server.lauf()
    finally:
        dienst.stoppe_hintergrund()
        server.schliessen()
    return 0 if angefordert else 1  # ohne Anforderung beendet: Fehler, damit systemd neu startet


# ---------------------------------------------------------------- Client und Aufruf
USAGE = ("Aufruf: wamo-release [--json] status | anfordern main|selbst <profil> | abbrechen <anfrage>"
         " | zurueck ui|kern|alles [--trocken]\n       wamo-release selbstpruefung")


def formatiere(a: dict) -> list[str]:
    """Antwort als lesbare Zeilen für den Menschen (die Maschine liest das JSON)."""
    marke = "OK" if a.get("ok") else "ABGELEHNT" if a.get("abgelehnt") else "FEHLER"
    zeilen = [f"{marke} {a.get('text', '')}"]
    if a.get("detail"):
        zeilen.append(f"  Einzelheit: {a['detail']}")
    if "modus" in a:
        zeilen.append(f"  Modus: {a['modus']}, bereit: {a['bereit']}, angehalten: {a.get('angehalten')}")
        an = a.get("anfrage")
        if an:
            zeilen.append(f"  Anfrage {an['id']}: {an['status']} (Schritt {an['schritt'] + 1} von {an['schritte']}), "
                          f"Quelle {an['quelle']}, Frist bis {an['frist_bis']}")
        else:
            zeilen.append("  Keine offene Anfrage")
        zeilen.append(f"  Anker: {a['anker']['sperre']}, Datenbank: {a['datenbank']}, Arbeiter: {a['arbeiter']}")
        au = a["audit"]
        zeilen.append(f"  Audit: Kette {au['kette']}, {au['zeilen']} Zeilen, {au['fuellgrad_prozent']} Prozent voll"
                      + (f" ({au['grund']})" if au.get("grund") else ""))
        zeilen.append(f"  Installation: {a['installation']}" + (f": {'; '.join(a['installation_probleme'])}"
                                                                if a.get("installation_probleme") else ""))
    return zeilen


def client(args: list[str], sockpfad: str | None = None, erwartete_uid: int | None = None) -> int:
    """Eine Anfrage an den Dienst. Exit 0 bei ok, 1 bei Ablehnung oder Fehler, 2 bei falschem Aufruf oder keiner Antwort.
    Die Gegenstelle muss dem Benutzer wamo-release gehören (sonst wird nichts gesendet)."""
    ausgabe_json = bool(args) and args[0] == "--json"
    teile = args[1:] if ausgabe_json else args
    kennung = os.urandom(16).hex()
    zeile = (" ".join([kennung, *teile]) + "\n").encode("ascii", "replace")
    try:
        parse_zeile(zeile)
    except Abgelehnt:
        print(USAGE, file=sys.stderr)
        return 2
    uid = name_zu_uid("wamo-release") if erwartete_uid is None else erwartete_uid
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(CLIENT_ZEIT_S)
    try:
        s.connect(sockpfad or PFADE.socket)
        gegenstelle = peer_von(s)
        if gegenstelle is None or uid is None or gegenstelle.uid != uid:
            print("Die Gegenstelle am Socket gehört nicht wamo-release, Abbruch.", file=sys.stderr)
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
        print(f"Keine Antwort vom Release-Dienst ({type(e).__name__}). Läuft der Dienst? journalctl -u wamo-release",
              file=sys.stderr)
        return 2
    finally:
        s.close()
    if ausgabe_json:
        print(json.dumps(antwort, ensure_ascii=False))
    else:
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
    return client(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
