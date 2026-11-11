"""Lader der Labor-Daten (Testlabor 3.6, P5): Regeln, Kontakte, Vorlagen und Wissen liegen als Dateien auf dem Server und werden bei jedem
Sitzungsstart gelesen. So bleibt der Inhalt nach dem Freeze änderbar, ohne neues Release.

Lesesicherheit (Befund 13): Der Kern liest nur Namen aus einer FESTEN Liste (kein Verzeichnislisting, kein Muster). Jede Datei wird mit
O_NOFOLLOW geöffnet (kein Symlink), muss nach fstat eine reguläre Datei sein, root gehören, darf für Gruppe und andere nicht schreibbar sein
und nicht mehrfach verlinkt sein; zwischen Prüfung und Öffnen darf sie nicht getauscht worden sein. Das Verzeichnis selbst muss ein echter Ordner
sein (kein Symlink), root gehören und für Dritte unbeschreibbar sein; es darf nicht /etc/wamo (dort liegt die Geheimnisdatei) oder ein
übergeordneter Ordner sein. Verstößt etwas dagegen, wird die Datei nicht gelesen und nur der Typ des Verstoßes ins Log geschrieben.

Grenzen: zusammen höchstens 6000 Zeichen, der Regelteil (persona-zusatz.md) höchstens 2000, nur Buchstaben, Ziffern, Satz- und
Sonderzeichen, Leerzeichen, Zeilenumbruch und Tabulator (keine Steuer-, Format-, Bidi- und privaten Zeichen). Eine Datei, die eine Grenze
reißt, wird nicht gekürzt, sondern ganz abgelehnt. Fehlt etwas oder ist es unzulässig, läuft die Sitzung ohne diesen Teil weiter, im
Zweifel mit dem reinen Laborprompt (labor_prompt.py), nie mit dem normalen Prompt.

Regelteil getrennt vom Datenteil: persona-zusatz.md kommt als eigener Regelblock in den Prompt, alles andere als Daten ohne Anweisungston.
Die Prüfsummen der gelesenen Dateien stehen in der Logzeile des Sitzungsstarts (Namen und die ersten 16 Zeichen von SHA-256, nie Inhalt).
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field

from loguru import logger

STANDARD_VERZEICHNIS = "/etc/wamo/labor"
DATEI_REGELN = "persona-zusatz.md"
DATEIEN_DATEN = ("kontakte.md", "anforderung-vorlage.md", "wissen-istqb.md", "bericht-vorlage.md", "ausgang.md", "ctfl-zuordnung.json")
DATEIEN_ALLE = (DATEI_REGELN, *DATEIEN_DATEN)
MAX_GESAMT = 6000
MAX_REGELN = 2000
MAX_BYTES = MAX_GESAMT * 4 + 8  # UTF-8 braucht je Zeichen bis zu 4 Byte; mehr wird nicht einmal gelesen
_VERBOTENE_VERZEICHNISSE = frozenset({"/", "/etc", "/etc/wamo", "/root", "/home"})
_ERLAUBTE_KATEGORIEN = "LMNPS"


@dataclass(frozen=True)
class Richtlinie:
    """besitzer: erwartete uid (root = 0), None prüft sie nicht (nur Tests). verboten_modus: Rechtebits, die nicht gesetzt sein dürfen
    (0o022: Gruppe und andere dürfen nicht schreiben), 0 prüft sie nicht (nur Tests)."""

    besitzer: int | None = 0
    verboten_modus: int = 0o022


@dataclass(frozen=True)
class LaborDaten:
    regeln: str = ""
    daten: tuple[tuple[str, str], ...] = ()
    pruefsummen: Mapping[str, str] = field(default_factory=dict)
    abgelehnt: Mapping[str, str] = field(default_factory=dict)  # Dateiname (oder * für das Verzeichnis) -> Typ des Verstoßes

    def logzeile(self) -> str:
        """Eine Zeile für das Log: je Datei Name und Prüfsumme oder Vermerk, nie Inhalt."""
        teile = []
        if "*" in self.abgelehnt:
            teile.append(f"Verzeichnis({self.abgelehnt['*']})")
        for name in DATEIEN_ALLE:
            if name in self.pruefsummen:
                teile.append(f"{name}={self.pruefsummen[name]}")
            elif name in self.abgelehnt:
                teile.append(f"{name}({self.abgelehnt[name]})")
        return "Labor-Daten: " + " ".join(teile)


def verzeichnis_aus_env() -> str:
    return (os.getenv("WAMO_LABOR_DATEN") or "").strip() or STANDARD_VERZEICHNIS


def _verboten(pfad: str) -> bool:
    """Ist das Verzeichnis eines der verbotenen (/etc/wamo und darüber)? Schrägstriche werden zusammengefasst, Punktsegmente aufgelöst, ein
    Laufwerksbuchstabe (nur auf Entwicklerrechnern) abgeschnitten, Groß- und Kleinschreibung zählt nicht."""
    glatt = re.sub(r"^[A-Za-z]:", "", pfad.replace("\\", "/"))
    teile: list[str] = []
    for teil in glatt.split("/"):
        if teil in ("", "."):
            continue
        if teil == "..":
            if teile:
                teile.pop()
            continue
        teile.append(teil)
    return ("/" + "/".join(teile)).lower() in _VERBOTENE_VERZEICHNISSE


def stat_problem(st: os.stat_result, richtlinie: Richtlinie = Richtlinie()) -> str | None:
    """Typ des Verstoßes einer Datei oder None."""
    if not stat.S_ISREG(st.st_mode):
        return "keine_datei"  # auch ein Symlink (lstat), ein Ordner, ein Gerät, eine Pipe
    if richtlinie.besitzer is not None and st.st_uid != richtlinie.besitzer:
        return "besitzer"
    if st.st_mode & richtlinie.verboten_modus:
        return "schreibrecht_dritte"
    if st.st_nlink != 1:
        return "mehrfach_verlinkt"
    return None


def verzeichnis_problem(pfad: str, st: os.stat_result, richtlinie: Richtlinie = Richtlinie()) -> str | None:
    if _verboten(pfad):
        return "verzeichnis_verboten"
    if not stat.S_ISDIR(st.st_mode):
        return "keine_datei"  # auch ein Symlink auf einen Ordner
    if richtlinie.besitzer is not None and st.st_uid != richtlinie.besitzer:
        return "besitzer"
    if st.st_mode & richtlinie.verboten_modus:
        return "schreibrecht_dritte"
    return None


def _zeichen_ok(text: str) -> bool:
    return all(ch in "\n\t " or unicodedata.category(ch)[0] in _ERLAUBTE_KATEGORIEN for ch in text)


def _lesen(pfad: str, richtlinie: Richtlinie) -> tuple[bytes | None, str | None]:
    """Roher Inhalt oder der Typ des Verstoßes. Die Prüfung gilt vor dem Öffnen (lstat) und nach dem Öffnen (fstat) derselben Datei."""
    try:
        vorher = os.lstat(pfad)
    except FileNotFoundError:
        return None, "fehlt"
    except OSError:
        return None, "nicht_lesbar"
    if problem := stat_problem(vorher, richtlinie):
        return None, problem
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(pfad, flags)
    except OSError:
        return None, "nicht_lesbar"
    try:
        nachher = os.fstat(fd)
        if (nachher.st_ino, nachher.st_dev) != (vorher.st_ino, vorher.st_dev):
            return None, "getauscht"
        if problem := stat_problem(nachher, richtlinie):
            return None, problem
        if nachher.st_size > MAX_BYTES:
            return None, "zu_gross"
        stuecke: list[bytes] = []
        gelesen = 0
        while True:
            stueck = os.read(fd, 65536)
            if not stueck:
                break
            gelesen += len(stueck)
            if gelesen > MAX_BYTES:
                return None, "zu_gross"
            stuecke.append(stueck)
        return b"".join(stuecke), None
    except OSError:
        return None, "nicht_lesbar"
    finally:
        os.close(fd)


def laden(verzeichnis: str | None = None, *, richtlinie: Richtlinie = Richtlinie()) -> LaborDaten:
    """Liest die Labor-Daten. Wirft nie: Verstöße stehen in abgelehnt, nichts davon kommt in den Prompt."""
    pfad = verzeichnis if verzeichnis is not None else verzeichnis_aus_env()
    try:
        echt = os.path.realpath(pfad)
        if _verboten(pfad) or _verboten(echt):
            return _abgelehnt_gesamt("verzeichnis_verboten")
        st = os.lstat(pfad)
    except FileNotFoundError:
        return _abgelehnt_gesamt("fehlt")
    except OSError:
        return _abgelehnt_gesamt("nicht_lesbar")
    if problem := verzeichnis_problem(pfad, st, richtlinie):
        return _abgelehnt_gesamt(problem)

    regeln, daten, summen, abgelehnt = "", [], {}, {}
    gesamt = 0
    for name in DATEIEN_ALLE:
        roh, problem = _lesen(os.path.join(pfad, name), richtlinie)
        text = None
        if problem is None and roh is not None:
            try:
                text = roh[3:].decode("utf-8") if roh.startswith(b"\xef\xbb\xbf") else roh.decode("utf-8")
            except UnicodeDecodeError:
                problem = "kodierung"
        if text is not None:
            text = text.replace("\r\n", "\n")
            if not _zeichen_ok(text):
                problem = "zeichen"
            elif not text.strip():
                problem = "leer"
            elif len(text) > MAX_GESAMT:
                problem = "zu_gross"
            elif name == DATEI_REGELN and len(text.strip()) > MAX_REGELN:
                problem = "regeln_zu_lang"
            elif gesamt + len(text.strip()) > MAX_GESAMT:
                problem = "gesamt_zu_gross"
        if problem is not None or text is None:
            abgelehnt[name] = problem or "nicht_lesbar"
            continue
        text = text.strip()
        gesamt += len(text)
        summen[name] = hashlib.sha256(roh).hexdigest()[:16]
        if name == DATEI_REGELN:
            regeln = text
        else:
            daten.append((name, text))
    ergebnis = LaborDaten(regeln, tuple(daten), summen, abgelehnt)
    if abgelehnt:
        logger.warning("Labor-Daten: nicht gelesen: {}", ", ".join(f"{n}({t})" for n, t in abgelehnt.items()))
    return ergebnis


def _abgelehnt_gesamt(typ: str) -> LaborDaten:
    logger.warning("Labor-Daten: Verzeichnis nicht gelesen ({})", typ)
    return LaborDaten(abgelehnt={"*": typ})
