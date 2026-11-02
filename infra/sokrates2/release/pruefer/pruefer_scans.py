"""Deterministische Prüfstufen der Prüfstrecke (Konzept docs/selbstueberarbeitung.md D.3, Schritt 1.2a).

Vier Stufen, die nur Text und Dateien lesen und kein Werkzeug des Projekts starten: Secret-Scan, Fremdverweis-Grep, Wirkungsflächen und
Abhängigkeiten (Lockfile-Diff). Sie laufen später im Prozess der Prüfer-Sandbox (Benutzer wamo-pruefer), nie im Release-Dienst selbst; der
Release-Dienst bekommt nur das Ergebnis (Zeile "ok" oder "fehler" je Stufe) und die Trefferzahl.

Eingabe ist eine Liste von Dateien mit altem und neuem Inhalt (Bytes oder None). Geprüft werden nur die HINZUGEFÜGTEN Zeilen und die
Dateinamen; was schon im Bestand stand, gehört nicht zu dieser Änderung.

Nie in einem Ergebnis: der gefundene Text. Ein Treffer ist Pfad, Zeilennummer und Art (bei Netzzielen und Paketen zusätzlich der Rechner-
oder Paketname, der kein Geheimnis ist). So landet ein gefundenes Geheimnis nicht in der Karte, im Protokoll oder im Journal.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

try:
    import tomllib
except ImportError:  # Python vor 3.11: uv.lock wird dann nicht gelesen und die Stufe meldet das als Fehler
    tomllib = None  # type: ignore[assignment]

ZEILEN_MAX = 200_000        # je Datei: darüber hinaus wird nicht mehr verglichen (die Stufe meldet "zu_gross")
TREFFER_MAX = 200           # mehr zeigt keine Karte; die Zahl bleibt korrekt in treffer_anzahl
MUSTER_MAX = 200
MUSTER_LAENGE_MAX = 200
REGISTRY_ERLAUBT = frozenset({"registry.npmjs.org", "pypi.org", "files.pythonhosted.org"})
NETZ_UNBEDENKLICH = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "example.com", "example.org", "www.w3.org", "json-schema.org",
                               "schemas.microsoft.com", "www.apple.com"})


@dataclass(frozen=True)
class Datei:
    pfad: str                  # relativ zur Wurzel des Repos, mit Schrägstrich
    neu: bytes | None          # None: Datei wird gelöscht
    alt: bytes | None = None   # None: Datei ist neu


@dataclass(frozen=True)
class Treffer:
    pfad: str
    zeile: int                 # 0: der Dateiname selbst
    art: str
    detail: str = ""           # nur unbedenkliche Angaben (Rechnername, Paketname), nie gefundener Text


@dataclass
class Befund:
    name: str
    treffer: list[Treffer] = field(default_factory=list)
    treffer_anzahl: int = 0
    fehler: str = ""           # die Stufe selbst konnte nicht prüfen (zu groß, kein Parser): zählt als "fehler"
    sonder: bool = False       # nur Wirkungsflächen: die Änderung braucht eine Sonderfreigabe
    neue_pakete: list[tuple[str, str]] = field(default_factory=list)
    hinweise: list[Treffer] = field(default_factory=list)   # werden in der Karte angezeigt, blockieren nicht (Testwerte)

    @property
    def zeile(self) -> str:
        """Wert der Prüfstandszeile (release_automat.PRUEFSTAND_WERTE). Wirkungsflächen werden angezeigt, nie abgelehnt."""
        blockiert = self.name != "wirkungsflaechen" and self.treffer_anzahl > 0
        return "ok" if not self.fehler and not blockiert else "fehler"

    def melde(self, t: Treffer) -> None:
        self.treffer_anzahl += 1
        if len(self.treffer) < TREFFER_MAX:
            self.treffer.append(t)


# ------------------------------------------------------------------------------------------------ Hilfen
def _text(b: bytes | None) -> str:
    return "" if b is None else b.decode("utf-8", errors="replace")


def hinzugefuegte_zeilen(alt: bytes | None, neu: bytes | None) -> list[tuple[int, str]] | None:
    """(Zeilennummer in der neuen Fassung ab 1, Text) aller Zeilen, die neu sind. None, wenn die Datei zu groß ist."""
    if neu is None:
        return []
    zn = _text(neu).splitlines()
    if len(zn) > ZEILEN_MAX:
        return None
    if alt is None:
        return list(enumerate(zn, 1))
    za = _text(alt).splitlines()
    if len(za) > ZEILEN_MAX:
        return None
    erg: list[tuple[int, str]] = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, za, zn, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            erg.extend((j + 1, zn[j]) for j in range(j1, j2))
    return erg


def _durchlaufe(dateien: list[Datei], befund: Befund):
    """Liefert (Datei, hinzugefügte Zeilen) für jede Datei mit neuem Inhalt; zu große Dateien machen die Stufe fehlerhaft."""
    for d in dateien:
        zeilen = hinzugefuegte_zeilen(d.alt, d.neu)
        if zeilen is None:
            befund.fehler = "zu_gross"
            continue
        yield d, zeilen


# ------------------------------------------------------------------------------------------------ Secret-Scan
SECRET_NAMEN = re.compile(
    r"(^|/)(\.env(\.(?!example$|sample$|template$|dist$)[\w.-]+)?|id_(rsa|dsa|ecdsa|ed25519)(_sk)?|\.npmrc|\.netrc|\.pgpass|\.git-credentials|"
    r"credentials\.json|gh-app\.pem|geheim\.env|core\.env)$|\.(pem|key|pfx|p12|kdbx)$", re.IGNORECASE)
ANTHROPIC_LAENGE_MIN = 80   # echte Schlüssel sind über 100 Zeichen lang; kürzere Werte (Testwerte wie sk-ant-api03-TEST-0123) sind Platzhalter
PEM_KOERPER_MIN = 100       # Zeichen Base64 hinter der Kopfzeile; echte Schlüssel haben mehrere hundert, Testkopfzeilen keine
BASE64_ZEILE = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
TEST_PFAD = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(test|spec)\.[cm]?[jt]sx?|tests?/[^/]*|__tests__/[^/]*)$")
SECRET_INHALT = (
    ("anthropic_schluessel", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("privater_schluessel", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("aws_schluessel", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("zuweisung", re.compile(r"(?i)\b(password|passwort|secret|api[_-]?key|token)\b\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']")),
)


def secret_scan(dateien: list[Datei], arbeitsbaum: tuple[str, ...] = ()) -> Befund:
    """Dateinamen (geänderte und alle des Arbeitsbaums) und hinzugefügte Zeilen."""
    b = Befund("secret_scan")
    for pfad in sorted({d.pfad for d in dateien if d.neu is not None} | set(arbeitsbaum)):
        if SECRET_NAMEN.search(pfad):
            b.melde(Treffer(pfad, 0, "geheimnisdatei"))
    for d, zeilen in _durchlaufe(dateien, b):
        neu_zeilen = _text(d.neu).splitlines()
        for nr, text in zeilen:
            for art, muster in SECRET_INHALT:
                m = muster.search(text)
                if m is None:
                    continue
                if art == "anthropic_schluessel" and len(m.group(0)) < ANTHROPIC_LAENGE_MIN:
                    continue  # Platzhalter, kein echter Schlüssel
                if art == "privater_schluessel" and not _pem_koerper_lang(neu_zeilen, nr):
                    continue  # nur eine Kopfzeile (Testtext, Muster in einer Prüfung), kein Schlüssel
                if art == "zuweisung" and TEST_PFAD.search(d.pfad):
                    b.hinweise.append(Treffer(d.pfad, nr, "testwert"))  # in Testdateien nur anzeigen
                    break
                b.melde(Treffer(d.pfad, nr, art))
                break
    return b


def _pem_koerper_lang(zeilen: list[str], kopf_nr: int) -> bool:
    """Hat der Schlüssel hinter der Kopfzeile (Zeile kopf_nr, ab 1) einen Körper von mindestens PEM_KOERPER_MIN Base64-Zeichen?"""
    summe = 0
    for z in zeilen[kopf_nr:kopf_nr + 80]:
        z = z.strip()
        if not BASE64_ZEILE.fullmatch(z):
            break  # der Körper besteht aus zusammenhängenden Base64-Zeilen; Code, Kommentar oder die Endzeile beenden ihn
        summe += len(z)
    return summe >= PEM_KOERPER_MIN


# ------------------------------------------------------------------------------------------------ Fremdverweis-Grep
def muster_laden(zeilen: list[str]) -> list[re.Pattern[str]]:
    """Die Mustervorlage liegt im Anker (/etc/wamo-anker/fremdverweise.txt), nicht im Repo: eine Regex je Zeile, # beginnt einen Kommentar."""
    erg = []
    for z in zeilen:
        z = z.strip()
        if not z or z.startswith("#"):
            continue
        if len(z) > MUSTER_LAENGE_MAX or len(erg) >= MUSTER_MAX:
            raise ValueError("Mustervorlage zu lang oder zu groß")
        erg.append(re.compile(z, re.IGNORECASE))
    if not erg:
        raise ValueError("Mustervorlage ist leer")
    return erg


def fremdverweis(dateien: list[Datei], muster: list[re.Pattern[str]]) -> Befund:
    """Dateinamen und hinzugefügte Zeilen gegen die Muster. Der Treffertext wird nie ausgegeben (das Muster selbst verriete sonst, was gesucht wird)."""
    b = Befund("fremdverweis")
    for d, zeilen in _durchlaufe(dateien, b):
        if d.neu is not None and any(m.search(d.pfad) for m in muster):
            b.melde(Treffer(d.pfad, 0, "name"))
        for nr, text in zeilen:
            if any(m.search(text) for m in muster):
                b.melde(Treffer(d.pfad, nr, "inhalt"))
    return b


# ------------------------------------------------------------------------------------------------ Wirkungsflächen
CODE_ENDUNGEN = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".cmd", ".ps1", ".sql")
WIRKUNG = (
    ("prozess", re.compile(r"\bsubprocess\b|\bos\.(system|popen|exec\w*|spawn\w*)\b|\bchild_process\b|\bpty\.spawn\b|\bshell\s*=\s*True\b")),
    ("auswerten", re.compile(r"\beval\s*\(|\bexec\s*\(|\bnew\s+Function\s*\(|\b__import__\s*\(")),
    ("serialisierung", re.compile(r"\bpickle\b|\bmarshal\.loads\b|\bshelve\b")),
    ("netzcode", re.compile(r"\bsocket\.(socket|create_connection)\b|\burllib\b|\bhttp\.client\b|\brequests\.(get|post|put|delete|request)\b|"
                            r"\baiohttp\b|\bhttpx\b|\bfetch\s*\(|\baxios\b|\bnew\s+WebSocket\b")),
    ("sudo", re.compile(r"\bsudo\b")),
)
URL_RE = re.compile(r"\bhttps?://[^\s\"'<>)\]]+", re.IGNORECASE)
GEHEIMNISNAMEN = ("WAMO_SCHLUESSEL_KEK", "core.env", "geheim.env", "gh-app.pem")


def wirkungsflaechen(dateien: list[Datei]) -> Befund:
    """Deterministischer Scan der hinzugefügten Zeilen von Code und Skripten. Die Treffer werden angezeigt und nie abgelehnt. Ein
    Verweis auf einen Geheimnisnamen in einer Datei, die ihn vorher nicht enthielt (also außerhalb ihres Moduls), macht die Änderung
    zur Sonderfreigabe (sonder=True)."""
    b = Befund("wirkungsflaechen")
    for d, zeilen in _durchlaufe(dateien, b):
        if not d.pfad.lower().endswith(CODE_ENDUNGEN):
            continue
        alt_text = _text(d.alt)
        for nr, text in zeilen:
            for art, muster in WIRKUNG:
                if muster.search(text):
                    b.melde(Treffer(d.pfad, nr, art))
            for u in URL_RE.findall(text):
                try:
                    host = (urlsplit(u).hostname or "").lower()
                except ValueError:  # kaputte Adresse wie http://[::1: darf die Stufe nie zum Absturz bringen
                    b.melde(Treffer(d.pfad, nr, "netzziel", "ungueltig"))
                    continue
                if host and host not in NETZ_UNBEDENKLICH:
                    b.melde(Treffer(d.pfad, nr, "netzziel", host[:80]))
            for name in GEHEIMNISNAMEN:
                if name in text:
                    b.melde(Treffer(d.pfad, nr, "geheimnisname", name))
                    if name not in alt_text:
                        b.sonder = True
    return b


# ------------------------------------------------------------------------------------------------ Abhängigkeiten
def _uv_lock(roh: bytes | None) -> dict[tuple[str, str], str]:
    """(Name, Version) -> Quelle aus uv.lock: Registry-Adresse, Git- oder URL-Quelle, sonst leer (lokal oder eingebaut)."""
    if roh is None:
        return {}
    if tomllib is None:
        raise ValueError("kein TOML-Parser")
    erg = {}
    for p in tomllib.loads(_text(roh)).get("package", []):
        quelle = p.get("source", {})
        erg[(str(p.get("name", "")), str(p.get("version", "")))] = str(quelle.get("registry") or quelle.get("git") or quelle.get("url") or "")
    return erg


def _npm_lock(roh: bytes | None) -> dict[tuple[str, str], str]:
    if roh is None:
        return {}
    erg = {}
    for pfad, p in json.loads(_text(roh)).get("packages", {}).items():
        if not pfad or not isinstance(p, dict):
            continue  # der Eintrag "" ist das Projekt selbst
        erg[(pfad.rsplit("node_modules/", 1)[-1], str(p.get("version", "")))] = str(p.get("resolved", ""))
    return erg


def abhaengigkeiten(dateien: list[Datei]) -> Befund:
    """Lockfile-Diff: neue Pakete werden aufgelistet (für die Karte), Quellen außerhalb von npmjs und PyPI sind ein Fehler. Die
    Werkzeuge npm audit und pip-audit laufen in der Sandbox und liefern ihre eigene Zeile."""
    b = Befund("abhaengigkeiten")
    for d in dateien:
        name = d.pfad.rsplit("/", 1)[-1]
        if name not in ("uv.lock", "package-lock.json") or d.neu is None:
            continue
        leser = _uv_lock if name == "uv.lock" else _npm_lock
        try:
            alt, neu = leser(d.alt), leser(d.neu)
        except (ValueError, KeyError, TypeError, AttributeError):
            b.fehler = "lockfile_unlesbar"
            continue
        for (paket, version), quelle in sorted(neu.items()):
            if (paket, version) in alt:
                continue
            b.neue_pakete.append((paket, version))
            if "://" not in quelle:
                continue  # ohne Adresse: lokales Paket des Projekts oder ein Eintrag ohne Quelle
            host = (urlsplit(quelle).hostname or "").lower()
            if host not in REGISTRY_ERLAUBT:
                b.melde(Treffer(d.pfad, 0, "fremde_quelle", f"{paket[:60]} ({host[:60] or 'unbekannt'})"))
    return b
