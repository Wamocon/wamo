"""Kern-Kanal des WAMO-Auftragsdispatchers auf Sokrates-2 (Dispatcher Version 7).

Installiert als /opt/wamo/waechter/wamo_job_kernkanal.py (root:wamo 0640) durch wamo-job-einrichten.sh. Der Dispatcher
(wamo_job_dispatch.py) lädt dieses Modul erst, wenn er es braucht, und ruft nur die Funktionen von hier auf. Hier steht
alles, was den Kern-Kanal betrifft, damit der Dispatcher (fast 3000 Zeilen) nur dünne Einhängepunkte bekommt.

Der Kern des Sprachassistenten ruft den Dispatcher über ssh mit einem eigenen Schlüssel auf (eigene Datei
/etc/ssh/authorized_keys.d/wamo-kern). Der Dispatcher erkennt den Kanal am benutzten Schlüssel, nie an der Quelladresse:

    lokal   weder SSH_CONNECTION noch SSH_ORIGINAL_COMMAND (Betreiber, Rauchtests, Notaus)
    pc      Schlüssel aus /etc/ssh/authorized_keys.d/wamo (der Weg wie in Version 6)
    kern    Schlüssel aus /etc/ssh/authorized_keys.d/wamo-kern, gewinnt vor pc
    unklar  im strikten Modus alles andere: herkunft_unklar

Strikt heißt: die Schlüsseldatei des Kerns oder /etc/wamo/kernkanal.conf existiert (auch leer, unlesbar oder fehlerhaft).
Ohne beide Dateien kann niemand als Kern ankommen, dann gilt jeder ssh-Aufruf als pc (Verhalten wie Version 6).

Dieses Modul liest keine Geheimnisse, startet keine Prozesse und spricht kein Netz. Es hat keine Nebenwirkung beim
Import. Pfade, Dateizugriff, Uhr und freier Platz werden eingespeist (Tests). Jede Prüfung liefert None (erlaubt) oder
einen Code aus KANAL_GRUENDE; unbekannt, fehlend oder unklar heißt gesperrt (fail-closed, der Dispatcher antwortet mit
Exit 2 und dem Ereignis kanal_gesperrt). Die Codes bilden die feste Liste der Schnittstelle zum Kern.

Regeln des Kern-Kanals, in dieser Reihenfolge (jeder Verstoß ist ein Code):
    1  Notaus-Datei vorhanden                                   notaus (nur ping und status gehen noch)
    2  Konfiguration fehlerhaft, Kanal zu, Ablauf überschritten  konfig_ungueltig, kanal_zu, abgelaufen
    3  Befehlsliste (ping start status pending approve cancel)   befehl; bei geschlossenem Kanal nur ping status pending cancel
    4  nur eigene Aufträge (auftrag.json mit kanal=kern)         fremd
    5  Art, Repo, Dienste aus der Konfiguration                  art, repo, repo_fehlt, dienst
    6  Projektname kern-<Name>, bei Repo genau daraus abgeleitet projekt
    7  Zugang nur "folgt" mit Profil                             zugang
    8  Fork-Prüfung beim ersten Zugriff je Lauf (fork_pruefen)   fork
    9  Grenzen je Stunde und Tag, Folgen, Abstand, Platz         grenze_start, grenze_laufzeit, grenze_folgen, abstand, platz
       Auch jeder Start, der die erste Prüfung besteht, zählt als Versuch (höchstens MAX_VERSUCH_STUNDE je Stunde, fest 20, nur nach unten
       einstellbar), damit Aufträge, die an einer Sperre scheitern, nicht unbegrenzt Ordner und Logzeilen erzeugen. Der Abstand gilt nicht
       für Folgen desselben Auftrags (Abnahme-Runden), MAX_FOLGEN begrenzt sie. Ein Projektordner, den kein Auftrag mit kanal=kern angelegt
       hat, und ein Ordner, den ein anderes Repo belegt hat, sind gesperrt (projekt).

Die Fenster der Zähler gleiten (Stunde = 3600 s, Tag = 86400 s). Die Zähler stehen in /srv/wamo/logs/kernkanal.jsonl,
je Start und je Ende eine Zeile {"t", "job", "ereignis", "dauer_s" (nur bei ende)}.
"""

from __future__ import annotations

import base64
import binascii
import errno
import json
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping

# Feste Liste der Gründe (Schnittstelle zum Kern: voice-core/entwicklung_betrieb.py hält GRUND_SAETZE mit denselben Schlüsseln)
KANAL_GRUENDE = ("notaus", "konfig_ungueltig", "kanal_zu", "abgelaufen", "befehl", "fremd", "art", "repo", "repo_fehlt",
                 "dienst", "projekt", "zugang", "fork", "herkunft_unklar", "grenze_start", "grenze_laufzeit", "grenze_folgen",
                 "abstand", "platz")

BEFEHLE_KERN = ("ping", "start", "status", "pending", "approve", "cancel")
BEFEHLE_GESCHLOSSEN = ("ping", "status", "pending", "cancel")  # bei geschlossenem oder fehlerhaftem Kanal
BEFEHLE_NOTAUS = ("ping", "status")

PRAEFIX = "kern-"
SELF_REPO = "wamo"
MAX_PROJEKT_ZEICHEN = 50
MAX_REPOS = 10
MAX_ABLAUF_TAGE = 400
MAX_VERSUCH_STUNDE_FEST = 20  # Versuche je Stunde (jeder Start, der die erste Prüfung besteht, auch wenn er später an Sperren scheitert)
MAX_SCHLUESSELDATEI_BYTES = 256 * 1024
MAX_AUTH_BYTES = 16 * 1024
MAX_KONFIG_BYTES = 64 * 1024
MAX_ZAEHLER_BYTES = 2 * 1024 * 1024  # größere Dateien: nur das Ende wird gelesen
EREIGNISSE = ("start", "ende", "versuch")  # versuch: ein Start, der die erste Prüfung bestand (auch wenn er später an einer Sperre scheitert)
STUNDE_S = 3600
TAG_S = 86400
MIB = 1024 * 1024

JOB_RE = re.compile(r"[0-9a-f]{16,32}")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")  # wie REPO_RE im Dispatcher
SCHLUESSEL_RE = re.compile(r"[A-Z][A-Z_]*")
ART_RE = re.compile(r"[a-z][a-z_]{1,29}")
DIENST_RE = re.compile(r"[a-z][a-z0-9_]{1,29}")
WERT_RE = re.compile(r"[ -~]*")  # druckbares ASCII
ABLAUF_RE = re.compile(r"[0-9]{8}")
GANZ_RE = re.compile(r"0|[1-9][0-9]{0,8}")
BUDGET_RE = re.compile(r"(0|[1-9][0-9]?)(\.[0-9]{1,2})?")
KEY_TYP_RE = re.compile(r"ssh-[a-z0-9-]+|ecdsa-sha2-[a-z0-9]+|sk-[a-z0-9@.-]+")
BASE64_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")
NORMAL_RE = re.compile(r"[^a-z0-9-]")

# Schlüssel der Konfiguration: (kleinster Wert, größter Wert) für Zahlen; Standard wie beim Anlegen durch das Skript
ZAHLEN = {"MAX_TURNS": (1, 200), "MAX_START_STUNDE": (1, 60), "MAX_START_TAG": (1, 200), "MAX_LAUFZEIT_TAG_S": (60, 86400),
          "MAX_FOLGEN": (0, 20), "MIN_ABSTAND_S": (0, 3600), "MIN_PLATZ_MB": (100, 1000000),
          "MAX_VERSUCH_STUNDE": (1, MAX_VERSUCH_STUNDE_FEST)}  # Versuche je Stunde: der Wert darf den festen nur senken
LISTEN = ("ARTEN", "REPOS", "DIENSTE")
BEKANNTE_SCHLUESSEL = frozenset({"KANAL", "ABLAUF", "MAX_BUDGET_USD", *ZAHLEN, *LISTEN})


@dataclass(frozen=True)
class Pfade:
    """Alle Dateien des Kern-Kanals (einspeisbar, damit Tests nie ein echtes System berühren)."""

    kern_schluessel: Path = Path("/etc/ssh/authorized_keys.d/wamo-kern")
    pc_schluessel: Path = Path("/etc/ssh/authorized_keys.d/wamo")
    konfig: Path = Path("/etc/wamo/kernkanal.conf")
    notaus: Path = Path("/etc/wamo/kernkanal.notaus")
    zaehler: Path = Path("/srv/wamo/logs/kernkanal.jsonl")
    platz: Path = Path("/srv/wamo")  # hier wird der freie Platz gemessen


PFADE = Pfade()


@dataclass(frozen=True)
class KanalErgebnis:
    kanal: str | None  # lokal, pc, kern oder None (unklar)
    strikt: bool
    grund: str | None = None  # herkunft_unklar, wenn kanal None ist


@dataclass(frozen=True)
class Konfig:
    """Regeln aus /etc/wamo/kernkanal.conf. Fehlende Zahlen gelten mit dem Standard, fehlende Listen sind leer (nichts erlaubt)."""

    kanal: str | None = None  # zu, offen oder None (fehlt: gilt als zu)
    ablauf: date | None = None
    arten: tuple[str, ...] = ()
    repos: tuple[str, ...] = ()  # klein geschrieben
    dienste: tuple[str, ...] = ()
    max_turns: int = 40
    max_budget_usd: str = "2.00"
    max_start_stunde: int = 4
    max_start_tag: int = 8
    max_laufzeit_tag_s: int = 14400
    max_folgen: int = 5
    max_versuch_stunde: int = MAX_VERSUCH_STUNDE_FEST
    min_abstand_s: int = 60
    min_platz_mb: int = 2048


@dataclass(frozen=True)
class KernZustand:
    """Zustand des Kanals: grund ist None, wenn er offen ist, sonst der erste Code der Regeln 1 und 2."""

    notaus: bool
    konfig: Konfig | None
    grund: str | None
    tage_rest: int | None = None


@dataclass(frozen=True)
class StartAnfrage:
    """Was ein Start im Kern-Kanal verlangt (aus Befehl und Kopfzeile; github und dienste stammen aus der Werkzeugkarte)."""

    job: str
    projekt: str
    art: str
    repo: str | None
    zugang: str | None
    profil: str | None
    github: bool  # die Art hat eine GitHub-Stufe
    dienste: tuple[str, ...]  # Dienste der Art außer GitHub
    app_modus: bool  # die GitHub-App ist eingerichtet (kein Rückfall auf einen persönlichen Token)
    fremd: bool = False  # der Auftrag besteht schon und gehört nicht dem Kern-Kanal (Regel 4)
    projekt_fremd: bool = False  # der Projektordner besteht schon und stammt aus keinem Auftrag mit kanal=kern
    projekt_kollision: bool = False  # ein Kern-Auftrag hat denselben Projektordner mit einem anderen Repo belegt


class KonfigFehler(Exception):
    """kernkanal.conf ist unbrauchbar. Der Text nennt Schlüsselnamen, nie Werte."""

    grund = "konfig_ungueltig"


class ProjektnameFehler(ValueError):
    """Projektname nicht ableitbar: grund ist repo oder projekt."""

    def __init__(self, grund: str, text: str = ""):
        super().__init__(text or grund)
        self.grund = grund


# ---------------------------------------------------------------- Dateizugriff
def datei_lesen(pfad, limit: int) -> tuple[bytes, os.stat_result]:
    """Liest höchstens limit Bytes einer regulären Datei ohne einem Link zu folgen und ohne an einer FIFO zu hängen.
    Liefert den Inhalt und os.fstat der geöffneten Datei (Eigentümer und Rechte gehören zu genau dieser Datei)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(pfad, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "keine reguläre Datei")
        daten = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(daten) > limit:
        raise OSError(errno.EFBIG, "Datei zu groß")
    return daten, st


def existiert(pfad) -> bool:
    """Gibt es den Eintrag? Ein Fehler, der etwas anderes als Fehlen meldet (Rechte, Ein-/Ausgabe), gilt als vorhanden."""
    try:
        os.lstat(pfad)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def _nur_eigene_datei(st: os.stat_result, uid: int | None) -> bool:
    """Gehört die Datei dem eigenen Benutzer und ist sie für Gruppe und andere nicht beschreibbar?"""
    return uid is not None and st.st_uid == uid and not (st.st_mode & 0o022)


# ---------------------------------------------------------------- Schlüssel lesen und vergleichen
def _woerter(zeile: str) -> list[str]:
    """Teilt eine Zeile an Leerraum außerhalb von Anführungszeichen (Optionen wie command="..." enthalten Leerzeichen)."""
    woerter: list[str] = []
    aktuell: list[str] = []
    in_quote = False
    for zeichen in zeile:
        if zeichen == '"':
            in_quote = not in_quote
            aktuell.append(zeichen)
        elif zeichen in " \t" and not in_quote:
            if aktuell:
                woerter.append("".join(aktuell))
                aktuell = []
        else:
            aktuell.append(zeichen)
    if aktuell:
        woerter.append("".join(aktuell))
    return woerter


def _schluessel_aus_woertern(woerter: list[str]) -> tuple[str, str] | None:
    """Das erste Paar (Typ, Base64-Teil) in einer Zeile; Optionen davor und Kommentar danach bleiben unbeachtet."""
    for i, wort in enumerate(woerter[:-1]):
        if KEY_TYP_RE.fullmatch(wort) and BASE64_RE.fullmatch(woerter[i + 1]):
            try:
                base64.b64decode(woerter[i + 1], validate=True)
            except (binascii.Error, ValueError):
                return None
            return wort, woerter[i + 1]
    return None


def schluessel_aus_text(text: str) -> set[tuple[str, str]]:
    """Alle Schlüssel einer authorized_keys-Datei als (Typ, Base64-Teil), tolerant gegenüber Optionen, Kommentaren, Leer- und
    Kommentarzeilen und kaputten Zeilen (diese zählen nicht)."""
    menge: set[tuple[str, str]] = set()
    for zeile in text.split("\n"):
        zeile = zeile.strip(" \t\r")
        if not zeile or zeile.startswith("#"):
            continue
        paar = _schluessel_aus_woertern(_woerter(zeile))
        if paar is not None:
            menge.add(paar)
    return menge


def schluessel_aus_auth(text: str) -> tuple[str, str] | None:
    """Der Schlüsselteil der Zeile "publickey <Typ> <Base64>" aus der Datei in SSH_USER_AUTH, oder None."""
    for zeile in text.split("\n"):
        woerter = zeile.strip("\r").split(" ")
        if len(woerter) >= 3 and woerter[0] == "publickey":
            paar = _schluessel_aus_woertern(woerter[1:3])
            if paar is not None:
                return paar
    return None


def _schluesselmenge(pfad, lesen: Callable) -> set[tuple[str, str]]:
    """Schlüssel einer Datei; eine fehlende Datei hat keine, jeder andere Lesefehler wird weitergegeben."""
    try:
        daten, _st = lesen(pfad, MAX_SCHLUESSELDATEI_BYTES)
    except FileNotFoundError:
        return set()
    return schluessel_aus_text(daten.decode("utf-8", "replace"))


def _benutzter_schluessel(umgebung: Mapping[str, str], lesen: Callable, uid: int | None) -> tuple[str, str] | None:
    pfad = umgebung.get("SSH_USER_AUTH")
    if not pfad:
        return None
    daten, st = lesen(pfad, MAX_AUTH_BYTES)
    if not _nur_eigene_datei(st, uid):
        return None
    return schluessel_aus_auth(daten.decode("ascii", "replace"))


def kanal_bestimmen(umgebung: Mapping[str, str], pfade: Pfade = PFADE, *, lesen: Callable = datei_lesen,
                    vorhanden: Callable = existiert, eigene_uid: int | None = None) -> KanalErgebnis:
    """Kanal der Verbindung nach der Falltabelle (siehe Kopf). umgebung ist die Umgebung des Dispatchers (nur lesend,
    dieses Modul greift nie selbst auf die Prozessumgebung zu). eigene_uid ist die Benutzerkennung des Dispatchers;
    ohne sie gibt es keine vertrauenswürdige SSH_USER_AUTH."""
    strikt = vorhanden(pfade.kern_schluessel) or vorhanden(pfade.konfig)
    if "SSH_CONNECTION" not in umgebung and "SSH_ORIGINAL_COMMAND" not in umgebung:
        return KanalErgebnis("lokal", strikt)
    if not strikt:
        return KanalErgebnis("pc", False)
    unklar = KanalErgebnis(None, True, "herkunft_unklar")
    try:
        benutzt = _benutzter_schluessel(umgebung, lesen, eigene_uid)
        kern = _schluesselmenge(pfade.kern_schluessel, lesen)
        pc = _schluesselmenge(pfade.pc_schluessel, lesen)
    except (OSError, ValueError):
        return unklar
    if benutzt is None:
        return unklar
    if benutzt in kern:
        return KanalErgebnis("kern", True)
    if benutzt in pc:
        return KanalErgebnis("pc", True)
    return unklar


# ---------------------------------------------------------------- Konfiguration
def _zahl(name: str, wert: str) -> int:
    kleinst, groesst = ZAHLEN[name]
    if not GANZ_RE.fullmatch(wert) or not kleinst <= int(wert) <= groesst:
        raise KonfigFehler(f"{name} ausserhalb des Bereichs {kleinst} bis {groesst}")
    return int(wert)


def _budget(wert: str) -> str:
    if not BUDGET_RE.fullmatch(wert):
        raise KonfigFehler("MAX_BUDGET_USD ist keine Dezimalzahl mit Punkt und höchstens zwei Stellen")
    zahl = float(wert)
    if not 0.01 <= zahl <= 20:
        raise KonfigFehler("MAX_BUDGET_USD ausserhalb des Bereichs 0.01 bis 20")
    return wert


def _liste(name: str, wert: str) -> list[str]:
    if not wert:
        return []
    teile = wert.split(",")
    if len(set(t.lower() for t in teile)) != len(teile):
        raise KonfigFehler(f"{name} enthält einen Eintrag doppelt")
    return teile


def _repos(wert: str) -> tuple[str, ...]:
    teile = _liste("REPOS", wert)
    if len(teile) > MAX_REPOS:
        raise KonfigFehler(f"REPOS hat mehr als {MAX_REPOS} Einträge")
    for repo in teile:
        name = repo.split("/", 1)[-1]
        if (not REPO_RE.fullmatch(repo) or repo.count("/") != 1 or name in (".", "..") or name.lower().endswith(".git")
                or name.lower() == SELF_REPO):
            raise KonfigFehler("REPOS enthält ein ungültiges Repo (owner/name, kein Platzhalter, nie das WAMO-Repo)")
    return tuple(r.lower() for r in teile)


def _arten(wert: str, bekannte: Iterable[str] | None) -> tuple[str, ...]:
    teile = _liste("ARTEN", wert)
    for art in teile:
        if not ART_RE.fullmatch(art) or (bekannte is not None and art not in tuple(bekannte)):
            raise KonfigFehler("ARTEN enthält eine unbekannte Art")
    return tuple(teile)


def _dienste(wert: str) -> tuple[str, ...]:
    teile = _liste("DIENSTE", wert)
    for dienst in teile:
        if not DIENST_RE.fullmatch(dienst) or dienst == "supabase":
            raise KonfigFehler("DIENSTE enthält einen unzulässigen Dienst (supabase nie)")
    return tuple(teile)


def _ablauf(wert: str, heute: date) -> date:
    if not ABLAUF_RE.fullmatch(wert):
        raise KonfigFehler("ABLAUF ist kein Datum der Form JJJJMMTT")
    try:
        tag = datetime.strptime(wert, "%Y%m%d").date()
    except ValueError:
        raise KonfigFehler("ABLAUF ist kein gültiges Datum") from None
    if tag > heute + timedelta(days=MAX_ABLAUF_TAGE):
        raise KonfigFehler(f"ABLAUF liegt mehr als {MAX_ABLAUF_TAGE} Tage voraus")
    return tag


def _heute(jetzt: float | None) -> date:
    return datetime.fromtimestamp(time.time() if jetzt is None else jetzt).date()


def konfig_lesen(pfad, *, jetzt: float | None = None, lesen: Callable = datei_lesen,
                 bekannte_arten: Iterable[str] | None = None) -> Konfig:
    """Liest und prüft die Konfiguration: Eigentümer root, für Gruppe und andere nicht beschreibbar, nur bekannte und je einmal
    vorkommende Schlüssel, alle Werte im Bereich nach I2. Jeder Mangel wirft KonfigFehler (konfig_ungueltig)."""
    try:
        daten, st = lesen(pfad, MAX_KONFIG_BYTES)
    except OSError:
        raise KonfigFehler("kernkanal.conf fehlt oder ist nicht lesbar") from None
    if len(daten) > MAX_KONFIG_BYTES:
        raise KonfigFehler("kernkanal.conf ist zu groß")
    if st.st_uid != 0:
        raise KonfigFehler("kernkanal.conf gehört nicht root")
    if st.st_mode & 0o022:
        raise KonfigFehler("kernkanal.conf ist für Gruppe oder andere beschreibbar")
    try:
        text = daten.decode("utf-8")
    except UnicodeDecodeError:
        raise KonfigFehler("kernkanal.conf ist kein UTF-8") from None
    werte: dict[str, str] = {}
    for zeile in text.split("\n"):
        zeile = zeile.strip(" \t\r")
        if not zeile or zeile.startswith("#"):
            continue
        name, gleich, wert = zeile.partition("=")
        if not gleich or not SCHLUESSEL_RE.fullmatch(name) or not WERT_RE.fullmatch(wert):
            raise KonfigFehler("kernkanal.conf hat eine Zeile ohne die Form SCHLUESSEL=wert")
        if name not in BEKANNTE_SCHLUESSEL:
            raise KonfigFehler(f"unbekannter Schlüssel {name[:40]}")
        if name in werte:
            raise KonfigFehler(f"Schlüssel {name} kommt doppelt vor")
        werte[name] = wert.strip()
    kanal = werte.get("KANAL")
    if "KANAL" in werte and kanal not in ("zu", "offen"):
        raise KonfigFehler("KANAL ist weder zu noch offen")
    standard = Konfig()
    zahlen = {n.lower(): _zahl(n, werte[n]) if n in werte else getattr(standard, n.lower()) for n in ZAHLEN}
    return Konfig(
        kanal=kanal,
        ablauf=_ablauf(werte["ABLAUF"], _heute(jetzt)) if "ABLAUF" in werte else None,
        arten=_arten(werte.get("ARTEN", ""), bekannte_arten),
        repos=_repos(werte.get("REPOS", "")),
        dienste=_dienste(werte.get("DIENSTE", "")),
        max_budget_usd=_budget(werte["MAX_BUDGET_USD"]) if "MAX_BUDGET_USD" in werte else standard.max_budget_usd,
        **zahlen)


def kanal_zustand(pfade: Pfade = PFADE, *, jetzt: float | None = None, lesen: Callable = datei_lesen,
                  vorhanden: Callable = existiert, bekannte_arten: Iterable[str] | None = None) -> KernZustand:
    """Zustand des Kern-Kanals aus Notaus-Datei und Konfiguration: der erste zutreffende Grund der Regeln 1 und 2."""
    notaus = bool(vorhanden(pfade.notaus))
    try:
        konfig = konfig_lesen(pfade.konfig, jetzt=jetzt, lesen=lesen, bekannte_arten=bekannte_arten)
    except KonfigFehler:
        return KernZustand(notaus, None, "notaus" if notaus else "konfig_ungueltig")
    tage = (konfig.ablauf - _heute(jetzt)).days if konfig.ablauf is not None else None
    if notaus:
        grund = "notaus"
    elif konfig.kanal != "offen":
        grund = "kanal_zu"
    elif tage is None or tage < 0:
        grund = "abgelaufen"
    else:
        grund = None
    return KernZustand(notaus, konfig, grund, tage)


def kernkanal_bericht(zustand: KernZustand, strikt: bool) -> dict:
    """Das Objekt kernkanal der ping-Antwort. Ohne eingerichteten Kern-Kanal (nicht strikt) gibt es keinen Grund."""
    konfig = zustand.konfig
    return {"strikt": bool(strikt), "offen": bool(strikt) and zustand.grund is None,
            "grund": zustand.grund if strikt else None,
            "arten": list(konfig.arten) if konfig else [], "repos": list(konfig.repos) if konfig else [],
            "ablauf": konfig.ablauf.strftime("%Y%m%d") if konfig and konfig.ablauf else None,
            "tage_rest": zustand.tage_rest}


# ---------------------------------------------------------------- Befehle und Aufträge (Regeln 1 bis 4)
def pruefe_befehl(name: str, zustand: KernZustand) -> str | None:
    """Regeln 1 bis 3 für einen Befehl aus dem Kern-Kanal."""
    if zustand.notaus:
        return None if name in BEFEHLE_NOTAUS else "notaus"
    if zustand.grund is not None and name not in BEFEHLE_GESCHLOSSEN:
        return zustand.grund
    if name not in BEFEHLE_KERN:
        return "befehl"
    return None


def pruefe_eigener_auftrag(meta: object) -> str | None:
    """Regel 4: nur Aufträge mit kanal=kern gehören dem Kern-Kanal. Ein Auftrag ohne das Feld gehört nie dazu."""
    if isinstance(meta, dict) and meta.get("kanal") == "kern":
        return None
    return "fremd"


# ---------------------------------------------------------------- Projektname (I5, Regel 6)
def _normal(name: str) -> str:
    """klein, Punkt und Unterstrich zu Bindestrich, alles außerhalb a-z0-9- verwerfen, Bindestriche zusammenfassen und am Rand streichen."""
    text = NORMAL_RE.sub("", name.lower().replace(".", "-").replace("_", "-"))
    return re.sub(r"-{2,}", "-", text).strip("-")


def kern_projektname(repo: str | None, wort: str | None = None) -> str:
    """Projektname im Kern-Kanal: kern- plus Name. Der Name kommt aus dem Repo (Teil nach dem Schrägstrich), sonst aus dem
    Projektwort. ProjektnameFehler mit grund repo (ungültiges Repo, nie das WAMO-Repo) oder projekt (kein Name ableitbar,
    länger als 50 Zeichen mit Präfix). Dieselbe Regel und dieselbe Wertetabelle wie voice-core/entwicklung_betrieb.py."""
    if repo:
        if not REPO_RE.fullmatch(repo) or repo.count("/") != 1:
            raise ProjektnameFehler("repo", "Repo ungültig")
        roh = repo.split("/", 1)[1]
        name = _normal(roh)
        if roh.lower() == SELF_REPO or name == SELF_REPO:
            raise ProjektnameFehler("repo", "das WAMO-Repo selbst ist gesperrt")
    else:
        name = _normal(wort or "")
        if name == SELF_REPO:  # auch ohne Repo kein Projekt kern-wamo (der Kern lehnt es ebenfalls ab)
            raise ProjektnameFehler("projekt", "der Name wamo ist gesperrt")
    if not name or len(PRAEFIX) + len(name) > MAX_PROJEKT_ZEICHEN:
        raise ProjektnameFehler("projekt", "Projektname nicht ableitbar")
    return PRAEFIX + name


def projekt_pruefen(projekt: str, repo: str | None) -> str | None:
    """Regel 6: das Projekt beginnt mit kern-, mit gesetztem Repo ist es genau kern-<abgeleiteter Name>."""
    if not projekt.startswith(PRAEFIX):
        return "projekt"
    if repo:
        try:
            erwartet = kern_projektname(repo)
        except ProjektnameFehler as e:
            return e.grund
        return None if projekt == erwartet else "projekt"
    rest = projekt[len(PRAEFIX):]
    if rest and rest != SELF_REPO and _normal(rest) == rest and len(projekt) <= MAX_PROJEKT_ZEICHEN:
        return None
    return "projekt"


def repo_kollision(repo: str | None, vorhandene: Iterable[str | None]) -> bool:
    """Teilen sich zwei verschiedene Repos einen Projektordner (my_repo und my-repo ergeben beide kern-my-repo)? True, wenn ein
    Kern-Auftrag denselben Ordner mit einem anderen, gesetzten Repo belegt hat (Vergleich ohne Groß- und Kleinschreibung)."""
    if not repo:
        return False
    return any(v and v.lower() != repo.lower() for v in vorhandene)


def pc_projekt_erlaubt(projekt: str) -> bool:
    """Der PC-Kanal darf den Namensraum kern- nicht öffnen: beide Kanäle teilen sich keinen Projektordner."""
    return not projekt.startswith(PRAEFIX)


# ---------------------------------------------------------------- Zähler (Regel 9)
def zaehler_lesen(pfad) -> list[dict]:
    """Gültige Zeilen des Zählers. Fehlt die Datei, ist er leer. Sehr große Dateien: nur das Ende (die angeschnittene erste Zeile
    fällt weg). Kaputte Zeilen zählen nicht; ein anderer Lesefehler wird weitergegeben (die Grenzen sind dann nicht prüfbar)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(pfad, flags)
    except FileNotFoundError:
        return []
    try:
        groesse = os.fstat(fd).st_size
        angeschnitten = groesse > MAX_ZAEHLER_BYTES
        if angeschnitten:
            os.lseek(fd, groesse - MAX_ZAEHLER_BYTES, os.SEEK_SET)
        daten = os.read(fd, MAX_ZAEHLER_BYTES)
    finally:
        os.close(fd)
    zeilen = daten.decode("utf-8", "replace").split("\n")
    if angeschnitten and zeilen:
        zeilen = zeilen[1:]
    ereignisse: list[dict] = []
    for zeile in zeilen:
        try:
            e = json.loads(zeile)
        except (ValueError, RecursionError):
            continue
        if not isinstance(e, dict) or not isinstance(e.get("t"), int) or isinstance(e.get("t"), bool):
            continue
        job, art, dauer = e.get("job"), e.get("ereignis"), e.get("dauer_s")
        if not isinstance(job, str) or not JOB_RE.fullmatch(job) or art not in EREIGNISSE:
            continue
        if art == "ende" and (not isinstance(dauer, int) or isinstance(dauer, bool) or dauer < 0):
            continue
        ereignisse.append({"t": e["t"], "job": job, "ereignis": art, **({"dauer_s": dauer} if art == "ende" else {})})
    return ereignisse


def zaehler_schreiben(pfad, job: str, ereignis: str, jetzt: float, dauer_s: int | None = None) -> None:
    """Hängt eine Zeile an den Zähler (eine Zeile je Schreibaufruf, atomar genug für zwei Prozesse). OSError, wenn es nicht geht."""
    if not JOB_RE.fullmatch(job) or ereignis not in EREIGNISSE:
        raise ValueError("Auftrag oder Ereignis ungültig")
    zeile: dict = {"t": int(jetzt), "job": job, "ereignis": ereignis}
    if dauer_s is not None:
        if not isinstance(dauer_s, int) or dauer_s < 0:
            raise ValueError("dauer_s ungültig")
        zeile["dauer_s"] = dauer_s
    daten = (json.dumps(zeile) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(pfad, flags, 0o640)
    try:
        os.write(fd, daten)
    finally:
        os.close(fd)


def freier_platz_mb(pfad) -> int | None:
    """Freier Platz in MiB, None wenn er sich nicht messen lässt."""
    try:
        return shutil.disk_usage(pfad).free // MIB
    except (OSError, ValueError):
        return None


def pruefe_grenzen(konfig: Konfig, ereignisse: list[dict], job: str, jetzt: float, platz_mb: int | None) -> str | None:
    """Regel 9 in der Reihenfolge grenze_start, grenze_laufzeit, grenze_folgen, abstand, platz. Der n-te Start im Fenster geht,
    der (n+1)-te nicht (n ist die Grenze). Fenster gleiten: Stunde 3600 s, Tag 86400 s."""
    starts = [e for e in ereignisse if e["ereignis"] == "start"]
    in_stunde = sum(1 for e in starts if e["t"] > jetzt - STUNDE_S)
    am_tag = sum(1 for e in starts if e["t"] > jetzt - TAG_S)
    versuche = sum(1 for e in ereignisse if e["ereignis"] == "versuch" and e["t"] > jetzt - STUNDE_S)
    if in_stunde >= konfig.max_start_stunde or am_tag >= konfig.max_start_tag or versuche >= konfig.max_versuch_stunde:
        return "grenze_start"
    laufzeit = sum(e.get("dauer_s", 0) for e in ereignisse if e["ereignis"] == "ende" and e["t"] > jetzt - TAG_S)
    if laufzeit >= konfig.max_laufzeit_tag_s:
        return "grenze_laufzeit"
    folge = any(e["job"] == job for e in starts)  # eine Folge desselben Auftrags (Abnahme-Runde): MAX_FOLGEN begrenzt sie, der Abstand nicht
    if sum(1 for e in starts if e["job"] == job) >= konfig.max_folgen + 1:
        return "grenze_folgen"
    laeufe = [e["t"] for e in ereignisse if e["ereignis"] in ("start", "ende")]
    if not folge and laeufe and konfig.min_abstand_s > 0 and jetzt - max(laeufe) < konfig.min_abstand_s:
        return "abstand"
    if platz_mb is None or platz_mb < konfig.min_platz_mb:
        return "platz"
    return None


# ---------------------------------------------------------------- Start: Regeln 1 bis 9
def pruefe_start(anfrage: StartAnfrage, zustand: KernZustand, *, ereignisse: list[dict] | None, jetzt: float,
                 freier_platz_mb: int | None) -> str | None:
    """Alle Regeln, die ein Start im Kern-Kanal vor dem ersten GitHub-Zugriff erfüllen muss (Regel 8 folgt mit fork_pruefen,
    sobald es Repo-Daten gibt). ereignisse None heißt: der Zähler ist nicht lesbar. Der Dispatcher ruft das vor dem Lesen der Geheimnisse und ein zweites Mal unmittelbar vor der
    Erzeugung des GitHub-Tokens, damit eine Notaus-Datei oder eine Konfigurationsänderung dazwischen noch greift."""
    grund = pruefe_befehl("start", zustand)
    if grund is not None:
        return grund
    konfig = zustand.konfig
    if konfig is None:  # kann nicht sein, wenn der Zustand offen ist; unklar heißt gesperrt
        return "konfig_ungueltig"
    if anfrage.fremd:
        return "fremd"
    if anfrage.art not in konfig.arten:
        return "art"
    repo = anfrage.repo
    if anfrage.github:
        if not repo:
            return "repo_fehlt"
        if repo.lower() not in konfig.repos or not anfrage.app_modus:
            return "repo"
    elif repo and repo.lower() not in konfig.repos:
        return "repo"
    for dienst in anfrage.dienste:
        if dienst == "supabase" or dienst not in konfig.dienste:
            return "dienst"
    grund = projekt_pruefen(anfrage.projekt, repo)
    if grund is not None:
        return grund
    if anfrage.projekt_fremd or anfrage.projekt_kollision:
        return "projekt"
    if anfrage.zugang != "folgt" or not anfrage.profil:
        return "zugang"
    if ereignisse is None:  # der Zähler ist nicht lesbar: die Grenzen lassen sich nicht prüfen
        return "grenze_start"
    return pruefe_grenzen(konfig, ereignisse, anfrage.job, jetzt, freier_platz_mb)


# ---------------------------------------------------------------- Regel 8: Fork-Prüfung
def _herkunft_wamo(eintrag: object) -> bool | None:
    """True, wenn ein Eintrag (parent, source, template_repository) das WAMO-Repo benennt; None, wenn er nicht lesbar ist."""
    if not isinstance(eintrag, dict) or not isinstance(eintrag.get("full_name"), str):
        return None
    return eintrag["full_name"].rsplit("/", 1)[-1].lower() == SELF_REPO


def fork_pruefen(antwort: object, repo: str | None = None) -> str | None:
    """Regel 8 über die Antwort von GitHub zu einem Repo (GET /repos/<owner>/<name>): ein Fork, ein Repo aus einer Vorlage oder
    ein Ursprung (parent, source, Vorlage) mit dem Namen wamo ist fork, ebenso jede unklare Antwort. Mit repo muss die Antwort
    zu genau diesem Repo gehören (klein verglichen)."""
    if not isinstance(antwort, dict):
        return "fork"
    voll = antwort.get("full_name")
    if not isinstance(voll, str) or (repo is not None and voll.lower() != repo.lower()):
        return "fork"
    if antwort.get("fork") is not False:  # True ist ein Fork, alles andere unklar
        return "fork"
    if antwort.get("template_repository") is not None:
        return "fork"
    for feld in ("parent", "source"):
        if antwort.get(feld) is not None and _herkunft_wamo(antwort[feld]) is not False:
            return "fork"
    return None
