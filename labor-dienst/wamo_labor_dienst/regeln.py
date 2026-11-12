"""Festlegungen und Prüfungen des Vertrags Version 1 (docs/labor-dienst-vertrag.md Abschnitt 2.3 und 3).

Konstanten (Wertebereiche, Grenzen, Quoten), der Fehler des Dienstes (DienstFehler) und die strengen Prüfungen der Eingaben. Der Dienst
normalisiert nie, er lehnt ab. Fehlertexte nennen den Feldnamen und nie die Eingabe, keine Pfade, keine Tabellen, keine Versionen.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl

PLAETZE = tuple(range(1, 17))
TYPEN = ("bug", "aufgabe", "story")
PRIORITAETEN = ("niedrig", "mittel", "hoch", "hoechste")
STATUS = ("zu_erledigen", "in_arbeit", "erledigt")
NUMMER_RE = re.compile(r"LAB-[1-9][0-9]{0,3}")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PLATZ_RE = re.compile(r"[1-9][0-9]?")

ANFRAGE_MAX = 16 * 1024  # Anfrage des Kerns (D5)
KOPF_MAX = 16 * 1024  # Summe aus Name, Wert und vier Byte je Kopfzeile (D5)
LISTE_MAX = 60 * 1024  # V2: wüchse die Antwort über 60 KB, liefert der Dienst weniger Tickets
QUOTE_TICKETS, QUOTE_KOMMENTARE, QUOTE_BYTES, QUOTE_RATE = 100, 300, 1024 * 1024, 30  # D1
RATE_FENSTER_SECS = 60.0
IDEM_SECS = 24 * 3600
IDEM_MAX = 200  # Obergrenze je Platz, wirkt nur bei Missbrauch (der Kern schreibt höchstens 25 mal je Durchlauf)
KOMMENTARE_V3 = 20
LIMIT_MAX = 10
Q_MAX = 100
ZAEHLER_START = 30
TICKET_NUMMER_MAX = 9999

_VERBOTEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_ABFRAGE = frozenset({"q", "nummer", "typ", "prioritaet", "status", "nur_durchlauf", "limit"})
_FELDER_AENDERN = frozenset({"titel", "beschreibung", "typ", "prioritaet"})


class DienstFehler(Exception):
    """Ein Fehler des Dienstes mit Status, Code aus Vertrag Abschnitt 4 und einem kurzen deutschen Satz ohne Interna."""

    def __init__(self, status: int, code: str, text: str, *, allow: str | None = None, retry_after: int | None = None):
        super().__init__(code)
        self.status, self.code, self.text, self.allow, self.retry_after = status, code, text, allow, retry_after


def eingabe(text: str) -> DienstFehler:
    return DienstFehler(422, "eingabe_ungueltig", text)


def text_pruefen(wert: object, feld: str, *, zeilen: bool, lo: int, hi: int) -> str:
    """Strenge Prüfung ohne Anpassung: Text, NFC, keine verbotenen Zeichen, Länge in Codepunkten (nicht Bytes)."""
    if not isinstance(wert, str) or not unicodedata.is_normalized("NFC", wert):
        raise eingabe(f"Das Feld {feld} ist ungültig.")
    for zeichen in wert:
        if zeichen == "\n" and zeilen:
            continue
        if zeichen == "\r" or unicodedata.category(zeichen) in _VERBOTEN:
            raise eingabe(f"Das Feld {feld} enthält unzulässige Zeichen.")
    if not lo <= len(wert) <= hi:
        raise eingabe(f"Das Feld {feld} hat eine falsche Länge.")
    return wert


def aus_liste(wert: object, feld: str, erlaubt: tuple[str, ...]) -> str:
    if not isinstance(wert, str) or wert not in erlaubt:
        raise eingabe(f"Das Feld {feld} hat einen unzulässigen Wert.")
    return wert


def version_pruefen(wert: object) -> int:
    if not isinstance(wert, int) or isinstance(wert, bool) or wert < 1:
        raise eingabe("Das Feld erwartete_version ist ungültig.")
    return wert


def json_lesen(roh: bytes) -> object:
    """JSON in UTF-8 ohne BOM, ohne doppelte Schlüssel, ohne NaN und Infinity. Alles andere ist 422."""
    def doppelt(paare: list[tuple[str, object]]) -> dict:
        werte = dict(paare)
        if len(werte) != len(paare):
            raise ValueError("doppeltes Feld")
        return werte

    def keine_zahl(_konstante: str) -> object:
        raise ValueError("keine Zahl")

    try:
        return json.loads(roh.decode("utf-8"), object_pairs_hook=doppelt, parse_constant=keine_zahl)
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise eingabe("Der Inhalt ist kein gültiges JSON.") from e


def nur_felder(obj: object, erlaubt: set[str], muss: set[str] | None = None) -> dict:
    """obj ist ein Objekt mit nur diesen Feldern (alle Pflicht, außer muss nennt weniger)."""
    if not isinstance(obj, dict) or not set(obj) <= erlaubt or not (erlaubt if muss is None else muss) <= set(obj):
        raise eingabe("Die Felder der Anfrage stimmen nicht.")
    return obj


def pruefe_anlegen(daten: object) -> dict:
    d = nur_felder(daten, {"typ", "prioritaet", "titel", "beschreibung"})
    return {"typ": aus_liste(d["typ"], "typ", TYPEN), "prioritaet": aus_liste(d["prioritaet"], "prioritaet", PRIORITAETEN),
            "titel": text_pruefen(d["titel"], "titel", zeilen=False, lo=3, hi=120),
            "beschreibung": text_pruefen(d["beschreibung"], "beschreibung", zeilen=True, lo=0, hi=1500)}


def _feld(name: str, wert: object) -> str:
    if name == "typ":
        return aus_liste(wert, name, TYPEN)
    if name == "prioritaet":
        return aus_liste(wert, name, PRIORITAETEN)
    if name == "beschreibung":
        return text_pruefen(wert, name, zeilen=True, lo=0, hi=1500)
    return text_pruefen(wert, name, zeilen=False, lo=3, hi=120)


def pruefe_aendern(daten: object) -> dict:
    d = nur_felder(daten, {"erwartete_version", "felder"})
    felder = d["felder"]
    if not isinstance(felder, dict) or not felder or not set(felder) <= _FELDER_AENDERN:
        raise eingabe("Das Feld felder ist ungültig.")
    return {"erwartete_version": version_pruefen(d["erwartete_version"]), "felder": {n: _feld(n, w) for n, w in felder.items()}}


def pruefe_status(daten: object) -> dict:
    d = nur_felder(daten, {"erwartete_version", "status"})
    return {"erwartete_version": version_pruefen(d["erwartete_version"]), "status": aus_liste(d["status"], "status", STATUS)}


def pruefe_kommentar(daten: object) -> dict:
    d = nur_felder(daten, {"erwartete_version", "text"})
    return {"erwartete_version": version_pruefen(d["erwartete_version"]), "text": text_pruefen(d["text"], "text", zeilen=True, lo=1, hi=500)}


PRUEFER = {"tickets": pruefe_anlegen, "ticket": pruefe_aendern, "status": pruefe_status, "kommentare": pruefe_kommentar}


@dataclass(frozen=True)
class Suche:
    """Geprüfte Abfrage von V2. Alle Filter gelten zusammen (UND)."""
    q: str = ""
    nummer: int | None = None
    typ: str | None = None
    prioritaet: str | None = None
    status: str | None = None
    nur_durchlauf: bool = False
    limit: int = LIMIT_MAX


def _paare(query: bytes) -> dict[str, str]:
    try:
        paare = parse_qsl(query.decode("utf-8"), keep_blank_values=True, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as e:
        raise eingabe("Die Abfrage ist ungültig.") from e
    abfrage = dict(paare)
    if len(abfrage) != len(paare) or not set(abfrage) <= _ABFRAGE:
        raise eingabe("Die Abfrage hat unbekannte oder doppelte Parameter.")
    return abfrage


def parse_suche(query: bytes) -> Suche:
    """Die Abfrage von V2 streng prüfen: unbekannte oder doppelte Parameter, falsche Werte und ein zu langes q sind 422."""
    abfrage = _paare(query)
    q = abfrage.get("q", "")
    if len(q) > Q_MAX:
        raise eingabe("Der Parameter q ist zu lang.")
    nummer = abfrage.get("nummer")
    if nummer is not None and not NUMMER_RE.fullmatch(nummer):
        raise eingabe("Der Parameter nummer ist ungültig.")
    werte = {f: aus_liste(abfrage[f], f, erlaubt) for f, erlaubt in (("typ", TYPEN), ("prioritaet", PRIORITAETEN), ("status", STATUS)) if f in abfrage}
    if abfrage.get("nur_durchlauf", "false") not in ("true", "false"):
        raise eingabe("Der Parameter nur_durchlauf ist ungültig.")
    limit = abfrage.get("limit", str(LIMIT_MAX))
    if not (limit.isascii() and limit.isdigit() and len(limit) <= 3 and 1 <= int(limit) <= LIMIT_MAX):
        raise eingabe("Der Parameter limit ist ungültig.")
    return Suche(q=q, nummer=int(nummer[4:]) if nummer else None, nur_durchlauf=abfrage.get("nur_durchlauf") == "true", limit=int(limit), **werte)
