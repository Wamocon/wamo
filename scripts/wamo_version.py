#!/usr/bin/env python3
"""WAMO Versionsskript: Version aus Conventional Commits, Versionsdateien und CHANGELOG.md (Konzept docs/selbstueberarbeitung.md,
Abschnitt F). Nur Standardbibliothek, Python 3.11 oder neuer. Es gibt eine Version für das ganze Monorepo.

Unterbefehle (alle ohne Netz; das Skript pusht nichts und setzt kein Tag, das tut später der Release-Dienst):
  vorschlag [--json]        zeigt das letzte Tag, die Commits seitdem und den Vorschlag
  changelog [VERSION]       gibt den Changelog-Abschnitt aus den Commits aus (ohne Schreiben)
  anwenden VERSION          setzt alle Versionsdateien und fügt den Abschnitt in CHANGELOG.md ein (--trocken schreibt nichts)
  pruefen [--erwartet X.Y.Z]   prüft Versionsdateien, letztes Tag und CHANGELOG.md auf Abweichungen (Exit-Code 1)
Der Schalter --pruefen gilt wie der Unterbefehl pruefen.

Regeln (Konzept F):
  * Basis ist das höchste Tag vX.Y.Z (keine Vorabversionen). Ohne Tag gilt die Version der Dateien und die ganze Historie.
  * Merge-Commits zählen nicht, bei Squash zählt der Titel. fix, perf und revert ergeben Patch, feat Minor, "!" oder
    "BREAKING CHANGE" Major. docs, chore, ci, test, style, refactor und build heben nicht an; liegen Änderungen außerhalb
    von docs/ vor, gilt mindestens Patch. Nicht konventionelle Titel zählen als Patch und erscheinen als Warnung.
  * Ein Revert hebt den ursprünglichen Commit auf, wenn dieser im selben Bereich liegt (beide zählen dann nicht).
  * Erhöhen ist erlaubt, Senken nie: die Wahl liegt über dem letzten Tag und nicht unter dem Vorschlag. Der Sprung von
    0.y.z auf 1.0.0 oder höher braucht die ausdrückliche Bestätigung (--major-bestaetigt).
  * Tags heißen genau vX.Y.Z, die Version in den Dateien X.Y.Z.
Versionsdateien: je ui und desktop package.json und package-lock.json (Wurzel und packages[""]), je voice-core, host und
geraete pyproject.toml und der Eintrag des Pakets in uv.lock. Was fehlt, wird übersprungen.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/Wamocon/wamo"
NPM_PAKETE = ("ui", "desktop")
PY_PAKETE = ("voice-core", "host", "geraete")

Version = tuple[int, int, int]

VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
KOPF_RE = re.compile(r"^(?P<typ>[a-z]+)(?:\((?P<bereich>[^()\r\n]+)\))?(?P<bruch>!)?: (?P<text>\S.*)$")
REVERT_RE = re.compile(r'^Revert "(?P<titel>.+)"$')
REVERT_SHA_RE = re.compile(r"This reverts commit (?P<sha>[0-9a-f]{7,64})", re.I)
BRUCH_RE = re.compile(r"^BREAKING[ -]CHANGE:\s*\S", re.M)
PR_RE = re.compile(r"\(#(?P<nr>\d+)\)\s*$")

STUFEN = ("keine", "patch", "minor", "major")
TYP_STUFE = {"feat": "minor", "fix": "patch", "perf": "patch", "revert": "patch",
             "docs": "keine", "chore": "keine", "ci": "keine", "test": "keine", "style": "keine",
             "refactor": "keine", "build": "keine"}
# Gruppen nach Keep a Changelog; Reihenfolge wie im Abschnitt
GRUPPEN = ("Hinzugefügt", "Geändert", "Veraltet", "Entfernt", "Behoben", "Sicherheit")
TYP_GRUPPE = {"feat": "Hinzugefügt", "refactor": "Geändert", "perf": "Geändert", "revert": "Geändert", "fix": "Behoben"}
UNVEROEFFENTLICHT = ("Unveröffentlicht", "Unreleased")


class VersionsFehler(Exception):
    """Fehler, die dem Aufrufer als Meldung gezeigt werden (Exit-Code 1)."""


# ---------- Versionen und Tags ----------

def parse_version(text: str) -> Version:
    treffer = VERSION_RE.match(text.strip())
    if not treffer:
        raise VersionsFehler(f"Ungültige Version '{text}': erwartet X.Y.Z ohne führende Nullen und ohne Zusätze")
    return int(treffer.group(1)), int(treffer.group(2)), int(treffer.group(3))


def format_version(version: Version) -> str:
    return ".".join(str(teil) for teil in version)


def tag_name(version: str | Version) -> str:
    """Tag zu einer Version: genau vX.Y.Z (die Version in den Dateien hat kein v)."""
    wert = parse_version(version) if isinstance(version, str) else version
    return "v" + format_version(wert)


def parse_tag(tag: str) -> Version | None:
    treffer = TAG_RE.match(tag.strip())
    return (int(treffer.group(1)), int(treffer.group(2)), int(treffer.group(3))) if treffer else None


def hoechster_tag(tags: Iterable[str]) -> tuple[str, Version] | None:
    """Höchster Tag vX.Y.Z (numerisch, v0.10.0 liegt über v0.9.0); alles andere wird ignoriert."""
    gueltig = [(parse_tag(tag), tag.strip()) for tag in tags if parse_tag(tag)]
    if not gueltig:
        return None
    version, tag = max(gueltig, key=lambda paar: paar[0])
    return tag, version


def erhoehen(version: Version, stufe: str) -> Version:
    major, minor, patch = version
    if stufe == "major":
        return major + 1, 0, 0
    if stufe == "minor":
        return major, minor + 1, 0
    if stufe == "patch":
        return major, minor, patch + 1
    raise VersionsFehler(f"Unbekannte Stufe '{stufe}'")


# ---------- Commits ----------

@dataclasses.dataclass(frozen=True)
class Commit:
    sha: str
    titel: str
    text: str = ""


@dataclasses.dataclass(frozen=True)
class Eintrag:
    commit: Commit
    typ: str | None            # None = nicht konventionell
    bereich: str | None
    beschreibung: str
    bruch: bool
    stufe: str
    pr: int | None = None
    revert_von: str | None = None


@dataclasses.dataclass(frozen=True)
class Ergebnis:
    stufe: str                 # keine | patch | minor | major
    eintraege: tuple[Eintrag, ...]
    warnungen: tuple[str, ...]
    aufgehoben: int = 0        # Zahl der Commits, die ein Revert mit seinem Original aufgehoben hat


@dataclasses.dataclass(frozen=True)
class Vorschlag:
    version: Version | None    # None = nichts zu veröffentlichen
    stufe: str
    braucht_bestaetigung: bool
    warnungen: tuple[str, ...]


def parse_commit(commit: Commit) -> Eintrag:
    """Liest einen Titel nach Conventional Commits: Typ(Bereich)!: Text, dazu die Fußzeile BREAKING CHANGE."""
    titel = commit.titel.strip()
    pr = PR_RE.search(titel)
    pr_nummer = int(pr.group("nr")) if pr else None
    bruch_fuss = bool(BRUCH_RE.search(commit.text))
    revert = REVERT_RE.match(titel)
    sha_treffer = REVERT_SHA_RE.search(commit.text)
    revert_von = sha_treffer.group("sha").lower() if sha_treffer else None
    if revert:
        return Eintrag(commit, "revert", None, revert.group("titel"), bruch_fuss,
                       "major" if bruch_fuss else "patch", pr_nummer, revert_von)
    kopf = KOPF_RE.match(titel)
    if kopf and kopf.group("typ") in TYP_STUFE:
        typ = kopf.group("typ")
        bruch = bool(kopf.group("bruch")) or bruch_fuss
        beschreibung = PR_RE.sub("", kopf.group("text")).strip()
        return Eintrag(commit, typ, kopf.group("bereich"), beschreibung, bruch,
                       "major" if bruch else TYP_STUFE[typ], pr_nummer, revert_von if typ == "revert" else None)
    return Eintrag(commit, None, None, PR_RE.sub("", titel).strip() or titel, bruch_fuss,
                   "major" if bruch_fuss else "patch", pr_nummer)


def berechne(commits: Sequence[Commit], pfade: Sequence[str] | None = None) -> Ergebnis:
    """Stufe aus den Commits. pfade sind die geänderten Dateien des Bereichs (None = unbekannt, dann zählt nur der Titel)."""
    eintraege = [parse_commit(c) for c in commits]
    # Ein Revert hebt sein Original auf, wenn es im selben Bereich liegt
    aufgehoben: set[str] = set()
    for eintrag in eintraege:
        if eintrag.typ != "revert" or not eintrag.revert_von:
            continue
        for anderer in eintraege:
            if anderer is not eintrag and anderer.commit.sha and anderer.commit.sha.lower().startswith(eintrag.revert_von):
                aufgehoben.update({eintrag.commit.sha, anderer.commit.sha})
    uebrig = tuple(e for e in eintraege if e.commit.sha not in aufgehoben)
    warnungen = tuple(
        f'Titel nicht konventionell (zählt als Patch): "{e.commit.titel}" ({e.commit.sha[:7]}); '
        f'bitte als "typ(bereich): Text" schreiben'
        for e in uebrig if e.typ is None
    )
    stufe = max((e.stufe for e in uebrig), key=STUFEN.index, default="keine")
    if uebrig and stufe == "keine" and pfade is not None and any(not p.replace("\\", "/").startswith("docs/") for p in pfade):
        stufe = "patch"     # Änderungen außerhalb von docs/ gelten mindestens als Patch (Konzept F, Punkt 3)
    return Ergebnis(stufe, uebrig, warnungen, len(aufgehoben))


def vorschlag(basis: Version, ergebnis: Ergebnis) -> Vorschlag:
    if ergebnis.stufe == "keine":
        return Vorschlag(None, "keine", False, ergebnis.warnungen)
    neu = erhoehen(basis, ergebnis.stufe)
    return Vorschlag(neu, ergebnis.stufe, basis[0] == 0 and neu[0] >= 1, ergebnis.warnungen)


def pruefe_wahl(gewaehlt: Version, basis: Version, vorschlag_: Vorschlag, major_bestaetigt: bool = False) -> None:
    """Erhöhen ist erlaubt, Senken nie: über dem letzten Tag, nicht unter dem Vorschlag, 1.0.0 nur bestätigt."""
    if gewaehlt <= basis:
        raise VersionsFehler(
            f"Version {format_version(gewaehlt)} liegt nicht über dem letzten Stand {format_version(basis)}: Senken ist nicht erlaubt")
    if vorschlag_.version is None:
        raise VersionsFehler("Keine Änderungen seit dem letzten Tag: nichts zu veröffentlichen")
    if gewaehlt < vorschlag_.version:
        raise VersionsFehler(
            f"Version {format_version(gewaehlt)} liegt unter dem Vorschlag {format_version(vorschlag_.version)}: Senken ist nicht erlaubt")
    if basis[0] == 0 and gewaehlt[0] >= 1 and not major_bestaetigt:
        raise VersionsFehler(
            f"Der Sprung von {format_version(basis)} auf {format_version(gewaehlt)} braucht die ausdrückliche Bestätigung (--major-bestaetigt)")


# ---------- Changelog ----------

def _zeile(eintrag: Eintrag) -> str:
    kopf = f"**{eintrag.bereich}:** " if eintrag.bereich else ""
    text = eintrag.beschreibung.strip()
    if eintrag.typ == "revert":
        text = f"Zurückgenommen: {text}"
    if eintrag.bruch:
        kopf = "**Bruch:** " + kopf
    text = text[:1].upper() + text[1:] if text else text
    pr = f" (PR #{eintrag.pr})" if eintrag.pr else ""
    return f"- {kopf}{text.rstrip('.')}{pr}"


def _gruppe(eintrag: Eintrag) -> str | None:
    if eintrag.typ is None:
        return "Geändert"
    if eintrag.bereich and eintrag.bereich.lower() in ("security", "sicherheit"):
        return "Sicherheit"
    return TYP_GRUPPE.get(eintrag.typ)


def changelog_abschnitt(version: str | Version, datum: str, ergebnis: Ergebnis) -> str:
    """Abschnitt '## [X.Y.Z] - Datum' aus den Commits: Gruppen nach Keep a Changelog, Brüche zuerst, je Commit eine Zeile."""
    wert = parse_version(version) if isinstance(version, str) else version
    zeilen = [f"## [{format_version(wert)}] - {datum}", ""]
    nach_gruppe: dict[str, list[Eintrag]] = {g: [] for g in GRUPPEN}
    for eintrag in ergebnis.eintraege:
        gruppe = _gruppe(eintrag)
        if gruppe:
            nach_gruppe[gruppe].append(eintrag)
    leer = True
    for gruppe in GRUPPEN:
        eintraege = sorted(nach_gruppe[gruppe], key=lambda e: not e.bruch)    # Brüche vorangestellt, sonst Reihenfolge der Commits
        if not eintraege:
            continue
        leer = False
        zeilen += [f"### {gruppe}", ""] + [_zeile(e) for e in eintraege] + [""]
    if leer:
        zeilen += ["Keine sichtbaren Änderungen (Pflege und interne Arbeiten).", ""]
    return "\n".join(zeilen).rstrip("\n") + "\n"


_KOPF_RE = re.compile(r"^## \[(?P<name>[^\]]+)\](?: - (?P<datum>\S+))?\s*$", re.M)
_LINK_RE = re.compile(r"^\[[^\]]+\]: \S+\s*$")


def _zerlege_changelog(text: str) -> tuple[str, list[tuple[str, str]], list[str]]:
    """(Kopf vor dem ersten Abschnitt, [(Name, Rumpf)], Linkzeilen am Ende)."""
    zeilen = text.replace("\r\n", "\n").split("\n")
    while zeilen and not zeilen[-1].strip():
        zeilen.pop()
    links: list[str] = []
    while zeilen and _LINK_RE.match(zeilen[-1]):
        links.insert(0, zeilen.pop())
    text_ohne = "\n".join(zeilen) + "\n"
    treffer = list(_KOPF_RE.finditer(text_ohne))
    if not treffer:
        raise VersionsFehler("CHANGELOG.md hat keinen Abschnitt '## [...]'")
    kopf = text_ohne[: treffer[0].start()]
    abschnitte = []
    for i, t in enumerate(treffer):
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text_ohne)
        abschnitte.append((t.group("name"), text_ohne[t.start():ende].rstrip("\n") + "\n"))
    return kopf, abschnitte, links


def changelog_versionen(text: str) -> list[Version]:
    """Alle Versionsabschnitte (neueste zuerst, wie in der Datei)."""
    _, abschnitte, _ = _zerlege_changelog(text)
    return [parse_version(name) for name, _ in abschnitte if name not in UNVEROEFFENTLICHT]


def _links(versionen: Sequence[Version], repo_url: str | None) -> list[str]:
    if not repo_url:
        return []
    neu_zuerst = list(versionen)
    zeilen = []
    if neu_zuerst:
        zeilen.append(f"[Unveröffentlicht]: {repo_url}/compare/{tag_name(neu_zuerst[0])}...HEAD")
    for i, version in enumerate(neu_zuerst):
        if i + 1 < len(neu_zuerst):
            zeilen.append(f"[{format_version(version)}]: {repo_url}/compare/{tag_name(neu_zuerst[i + 1])}...{tag_name(version)}")
        else:
            zeilen.append(f"[{format_version(version)}]: {repo_url}/releases/tag/{tag_name(version)}")
    return zeilen


def changelog_einfuegen(text: str, version: str | Version, abschnitt: str, repo_url: str | None = REPO_URL) -> str:
    """Fügt den Abschnitt unter '## [Unveröffentlicht]' ein und erzeugt die Vergleichslinks neu (deterministisch)."""
    wert = parse_version(version) if isinstance(version, str) else version
    kopf, abschnitte, _ = _zerlege_changelog(text)
    namen = [name for name, _ in abschnitte]
    unveroeffentlicht = next((i for i, n in enumerate(namen) if n in UNVEROEFFENTLICHT), None)
    if unveroeffentlicht is None:
        raise VersionsFehler("CHANGELOG.md hat keinen Abschnitt '## [Unveröffentlicht]'")
    rumpf = abschnitte[unveroeffentlicht][1].split("\n", 1)[1].strip()
    if rumpf:
        raise VersionsFehler(
            "Der Abschnitt [Unveröffentlicht] ist nicht leer: Einträge entstehen aus den Commits, bitte von Hand Eingetragenes "
            "vorher in die Commit-Titel oder in einen Abschnitt verschieben")
    bestehende = [parse_version(n) for n in namen if n not in UNVEROEFFENTLICHT]
    if wert in bestehende:
        raise VersionsFehler(f"CHANGELOG.md hat den Abschnitt [{format_version(wert)}] schon")
    if bestehende and wert <= max(bestehende):
        raise VersionsFehler(f"Version {format_version(wert)} liegt nicht über dem neuesten Abschnitt {format_version(max(bestehende))}")
    bloecke = [f"## [{namen[unveroeffentlicht]}]\n", abschnitt] + [r for n, r in abschnitte if n not in UNVEROEFFENTLICHT]
    text_neu = kopf + "\n".join(b.rstrip("\n") + "\n" for b in bloecke)         # eine Leerzeile zwischen den Abschnitten
    links = _links(sorted(bestehende + [wert], reverse=True), repo_url)
    if links:
        text_neu = text_neu.rstrip("\n") + "\n\n" + "\n".join(links) + "\n"
    return text_neu


# ---------- Versionsdateien ----------

@dataclasses.dataclass(frozen=True)
class Fund:
    pfad: Path
    ort: str
    version: str | None


def _lies(pfad: Path) -> str:
    with open(pfad, encoding="utf-8", newline="") as datei:
        return datei.read()


def _schreibe(pfad: Path, text: str) -> None:
    with open(pfad, "w", encoding="utf-8", newline="") as datei:
        datei.write(text)


def _npm_manifest(wurzel: Path, ordner: str) -> list[Fund]:
    funde = []
    manifest = wurzel / ordner / "package.json"
    if manifest.is_file():
        funde.append(Fund(manifest, "version", json.loads(_lies(manifest)).get("version")))
    lock = wurzel / ordner / "package-lock.json"
    if lock.is_file():
        daten = json.loads(_lies(lock))
        funde.append(Fund(lock, "version", daten.get("version")))
        funde.append(Fund(lock, 'packages[""].version', (daten.get("packages", {}).get("") or {}).get("version")))
    return funde


def _py_projekt(wurzel: Path, ordner: str) -> list[Fund]:
    funde = []
    projekt = wurzel / ordner / "pyproject.toml"
    if not projekt.is_file():
        return funde
    daten = tomllib.loads(_lies(projekt)).get("project", {})
    funde.append(Fund(projekt, "project.version", daten.get("version")))
    lock = wurzel / ordner / "uv.lock"
    if lock.is_file() and daten.get("name"):
        treffer = _uv_lock_re(daten["name"]).search(_lies(lock))
        funde.append(Fund(lock, f'package "{daten["name"]}"', treffer.group("version") if treffer else None))
    return funde


def _uv_lock_re(name: str) -> re.Pattern[str]:
    return re.compile(rf'(?P<kopf>\[\[package\]\]\r?\nname = "{re.escape(name)}"\r?\nversion = ")(?P<version>[^"]*)(?P<ende>")')


def lies_versionen(wurzel: Path = WURZEL) -> list[Fund]:
    funde: list[Fund] = []
    for ordner in NPM_PAKETE:
        funde += _npm_manifest(wurzel, ordner)
    for ordner in PY_PAKETE:
        funde += _py_projekt(wurzel, ordner)
    return funde


def konsistenz(funde: Sequence[Fund], wurzel: Path = WURZEL) -> list[str]:
    """Liste der Abweichungen (leer = alle Versionsdateien stimmen überein)."""
    fehler = []
    if not funde:
        return ["Keine Versionsdateien gefunden"]
    for fund in funde:
        ort = f"{fund.pfad.relative_to(wurzel).as_posix()} ({fund.ort})" if fund.pfad.is_relative_to(wurzel) else f"{fund.pfad} ({fund.ort})"
        if fund.version is None:
            fehler.append(f"{ort}: Version fehlt")
        elif not VERSION_RE.match(fund.version):
            fehler.append(f"{ort}: ungültige Version '{fund.version}'")
    gesetzt = {f.version for f in funde if f.version}
    if len(gesetzt) > 1:
        zaehler = {v: sum(1 for f in funde if f.version == v) for v in gesetzt}
        fehler.append("Versionen weichen ab: " + ", ".join(f"{v} ({n}x)" for v, n in sorted(zaehler.items())))
        for fund in funde:
            if fund.version and fund.version != max(zaehler, key=zaehler.get):
                ort = fund.pfad.relative_to(wurzel).as_posix() if fund.pfad.is_relative_to(wurzel) else str(fund.pfad)
                fehler.append(f"  {ort} ({fund.ort}): {fund.version}")
    return fehler


def gemeinsame_version(funde: Sequence[Fund], wurzel: Path = WURZEL) -> Version:
    abweichungen = konsistenz(funde, wurzel)
    if abweichungen:
        raise VersionsFehler("Versionsdateien sind nicht konsistent:\n  " + "\n  ".join(abweichungen))
    return parse_version(next(f.version for f in funde if f.version))


def _ersetze(text: str, muster: re.Pattern[str], neu: str, ort: str) -> str:
    ergebnis, anzahl = muster.subn(lambda m: m.group("kopf") + neu + m.group("ende"), text, count=1)
    if anzahl != 1:
        raise VersionsFehler(f"{ort}: Zeile mit der Version nicht gefunden")
    return ergebnis


def setze_versionen(version: str | Version, wurzel: Path = WURZEL, trocken: bool = False) -> list[Path]:
    """Setzt alle Versionsdateien auf X.Y.Z, ohne ihr Format zu ändern. Liefert die Dateien, die sich ändern würden."""
    neu = format_version(parse_version(version) if isinstance(version, str) else version)
    geaendert: list[Path] = []
    plan: dict[Path, str] = {}
    for ordner in NPM_PAKETE:
        manifest = wurzel / ordner / "package.json"
        if manifest.is_file():
            muster = re.compile(r'(?P<kopf>^  "version": ")[^"]*(?P<ende>")', re.M)
            plan[manifest] = _ersetze(_lies(manifest), muster, neu, f"{ordner}/package.json")
        lock = wurzel / ordner / "package-lock.json"
        if lock.is_file():
            text = _lies(lock)
            text = _ersetze(text, re.compile(r'(?P<kopf>^  "version": ")[^"]*(?P<ende>")', re.M), neu,
                            f"{ordner}/package-lock.json (Wurzel)")
            wurzel_eintrag = re.compile(r'(?P<kopf>"packages": \{\s*"": \{.*?\n\s*"version": ")[^"]*(?P<ende>")', re.S)
            plan[lock] = _ersetze(text, wurzel_eintrag, neu, f'{ordner}/package-lock.json (packages[""])')
    for ordner in PY_PAKETE:
        projekt = wurzel / ordner / "pyproject.toml"
        if not projekt.is_file():
            continue
        text = _lies(projekt)
        name = tomllib.loads(text).get("project", {}).get("name")
        abschnitt = re.search(r"^\[project\]\s*$.*?(?=^\[|\Z)", text, re.M | re.S)
        if not abschnitt:
            raise VersionsFehler(f"{ordner}/pyproject.toml: Abschnitt [project] nicht gefunden")
        kopf = re.compile(r'(?P<kopf>^version = ")[^"]*(?P<ende>")', re.M)
        neuer_abschnitt = _ersetze(abschnitt.group(0), kopf, neu, f"{ordner}/pyproject.toml")
        plan[projekt] = text[: abschnitt.start()] + neuer_abschnitt + text[abschnitt.end():]
        lock = wurzel / ordner / "uv.lock"
        if lock.is_file() and name:
            plan[lock] = _ersetze(_lies(lock), _uv_lock_re(name), neu, f'{ordner}/uv.lock (Paket "{name}")')
    for pfad, text in plan.items():
        if _lies(pfad) != text:
            geaendert.append(pfad)
            if not trocken:
                _schreibe(pfad, text)
    if not trocken:
        rest = konsistenz(lies_versionen(wurzel), wurzel)
        if rest or any(f.version != neu for f in lies_versionen(wurzel)):
            raise VersionsFehler("Nach dem Schreiben stimmen die Versionsdateien nicht:\n  " + "\n  ".join(rest))
    return geaendert


# ---------- Git ----------

def _git(wurzel: Path, *args: str) -> str:
    lauf = subprocess.run(["git", "-C", str(wurzel), *args], capture_output=True, text=True, encoding="utf-8")
    if lauf.returncode != 0:
        raise VersionsFehler(f"git {' '.join(args)} schlug fehl: {lauf.stderr.strip()[:300]}")
    return lauf.stdout


def lies_tags(wurzel: Path = WURZEL) -> list[str]:
    return [t for t in _git(wurzel, "tag", "--list", "v*").split("\n") if t.strip()]


def lies_commits(wurzel: Path = WURZEL, basis: str | None = None) -> list[Commit]:
    """Commits ohne Merges seit basis (ältester zuerst); ohne basis die ganze Historie."""
    bereich = [f"{basis}..HEAD"] if basis else ["HEAD"]
    roh = _git(wurzel, "log", "--no-merges", "--reverse", "--format=%H%x1f%s%x1f%b%x1e", *bereich)
    commits = []
    for block in roh.split("\x1e"):
        block = block.strip("\n")
        if not block.strip():
            continue
        sha, titel, text = (block.split("\x1f") + ["", ""])[:3]
        commits.append(Commit(sha.strip(), titel.strip(), text.strip()))
    return commits


def lies_pfade(wurzel: Path = WURZEL, basis: str | None = None) -> list[str]:
    if basis:
        return [p for p in _git(wurzel, "diff", "--name-only", f"{basis}..HEAD").split("\n") if p.strip()]
    return [p for p in _git(wurzel, "ls-tree", "-r", "--name-only", "HEAD").split("\n") if p.strip()]


def repo_url(wurzel: Path = WURZEL) -> str | None:
    try:
        url = _git(wurzel, "remote", "get-url", "origin").strip()
    except VersionsFehler:
        return REPO_URL
    treffer = re.match(r"^(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?$", url)
    return f"https://github.com/{treffer.group(1)}" if treffer else REPO_URL


@dataclasses.dataclass(frozen=True)
class Stand:
    tag: str | None
    basis: Version
    commits: tuple[Commit, ...]
    pfade: tuple[str, ...]
    hinweise: tuple[str, ...]


def lade_stand(wurzel: Path = WURZEL) -> Stand:
    gefunden = hoechster_tag(lies_tags(wurzel))
    if gefunden:
        tag, basis = gefunden
        return Stand(tag, basis, tuple(lies_commits(wurzel, tag)), tuple(lies_pfade(wurzel, tag)), ())
    basis = gemeinsame_version(lies_versionen(wurzel), wurzel)
    hinweis = (f"Kein Tag vX.Y.Z vorhanden: Basis ist die Version der Dateien ({format_version(basis)}), "
               "es zählt die ganze Historie",)
    return Stand(None, basis, tuple(lies_commits(wurzel)), tuple(lies_pfade(wurzel)), hinweis)


# ---------- Befehle ----------

def _stand_und_vorschlag(wurzel: Path) -> tuple[Stand, Ergebnis, Vorschlag]:
    stand = lade_stand(wurzel)
    ergebnis = berechne(stand.commits, stand.pfade)
    return stand, ergebnis, vorschlag(stand.basis, ergebnis)


def befehl_vorschlag(args: argparse.Namespace) -> int:
    stand, ergebnis, vor = _stand_und_vorschlag(args.wurzel)
    if args.json:
        print(json.dumps({
            "basis_tag": stand.tag, "basis": format_version(stand.basis), "commits": len(ergebnis.eintraege),
            "stufe": vor.stufe, "version": format_version(vor.version) if vor.version else None,
            "tag": tag_name(vor.version) if vor.version else None, "braucht_bestaetigung": vor.braucht_bestaetigung,
            "warnungen": list(stand.hinweise) + list(vor.warnungen),
        }, ensure_ascii=False, indent=2))
        return 0
    print(f"Letztes Tag: {stand.tag or 'keines'} (Basisversion {format_version(stand.basis)})")
    print(f"Commits seitdem: {len(stand.commits)}"
          + (f", {ergebnis.aufgehoben} durch Reverts aufgehoben" if ergebnis.aufgehoben else ""))
    for text in stand.hinweise:
        print(f"Hinweis: {text}")
    if vor.version is None:
        print("Vorschlag: keiner, es gibt keine Änderung, die ein Release auslöst")
    else:
        print(f"Vorschlag: {format_version(vor.version)} ({vor.stufe}), Tag {tag_name(vor.version)}")
        if vor.braucht_bestaetigung:
            print("Der Sprung auf 1.0.0 oder höher braucht die ausdrückliche Bestätigung des Admins (--major-bestaetigt).")
    for text in vor.warnungen:
        print(f"Warnung: {text}")
    return 0


def befehl_changelog(args: argparse.Namespace) -> int:
    stand, ergebnis, vor = _stand_und_vorschlag(args.wurzel)
    version = parse_version(args.version) if args.version else vor.version
    if version is None:
        raise VersionsFehler("Keine Änderungen seit dem letzten Tag: kein Abschnitt")
    print(changelog_abschnitt(version, args.datum, ergebnis), end="")
    return 0


def befehl_anwenden(args: argparse.Namespace) -> int:
    wurzel = args.wurzel
    stand, ergebnis, vor = _stand_und_vorschlag(wurzel)
    gewaehlt = parse_version(args.version)
    gemeinsam = gemeinsame_version(lies_versionen(wurzel), wurzel)
    if gemeinsam > stand.basis and gemeinsam != gewaehlt:
        raise VersionsFehler(f"Die Dateien stehen schon auf {format_version(gemeinsam)}, über dem letzten Tag {format_version(stand.basis)}")
    pruefe_wahl(gewaehlt, stand.basis, vor, args.major_bestaetigt)
    changelog = wurzel / "CHANGELOG.md"
    if not changelog.is_file():
        raise VersionsFehler("CHANGELOG.md fehlt")
    abschnitt = changelog_abschnitt(gewaehlt, args.datum, ergebnis)
    neu = changelog_einfuegen(_lies(changelog), gewaehlt, abschnitt, repo_url(wurzel))
    geaendert = setze_versionen(gewaehlt, wurzel, trocken=True)
    modus = "Trockenlauf, es wird nichts geschrieben" if args.trocken else "geschrieben"
    print(f"Version {format_version(gewaehlt)} (Tag {tag_name(gewaehlt)}), {modus}:")
    for pfad in geaendert + [changelog]:
        print(f"  {pfad.relative_to(wurzel).as_posix()}")
    for text in vor.warnungen:
        print(f"Warnung: {text}")
    print("\n" + abschnitt, end="")
    if not args.trocken:
        setze_versionen(gewaehlt, wurzel)
        _schreibe(changelog, neu)
    return 0


def pruefen(wurzel: Path, erwartet: str | None = None) -> list[str]:
    """Alle Abweichungen: Versionsdateien, letztes Tag, CHANGELOG.md (leer = in Ordnung)."""
    funde = lies_versionen(wurzel)
    fehler = konsistenz(funde, wurzel)
    if fehler:
        return fehler
    version = parse_version(funde[0].version)
    if erwartet is not None and version != parse_version(erwartet):
        fehler.append(f"Die Dateien stehen auf {format_version(version)}, erwartet war {erwartet}")
    try:
        gefunden = hoechster_tag(lies_tags(wurzel))
    except VersionsFehler:
        gefunden = None       # kein Git-Repository: nur die Dateien prüfen
    if gefunden and version < gefunden[1]:
        fehler.append(f"Die Dateien stehen auf {format_version(version)}, unter dem letzten Tag {gefunden[0]}")
    changelog = wurzel / "CHANGELOG.md"
    if not changelog.is_file():
        fehler.append("CHANGELOG.md fehlt")
    else:
        try:
            versionen = changelog_versionen(_lies(changelog))
        except VersionsFehler as fehler_:
            fehler.append(f"CHANGELOG.md: {fehler_}")
        else:
            if not versionen or max(versionen) != version:
                neueste = format_version(max(versionen)) if versionen else "keiner"
                fehler.append(f"CHANGELOG.md: der neueste Abschnitt ({neueste}) passt nicht zur Version {format_version(version)}")
    return fehler


def befehl_pruefen(args: argparse.Namespace) -> int:
    fehler = pruefen(args.wurzel, args.erwartet)
    if fehler:
        print("Abweichungen:")
        for text in fehler:
            print(f"  {text}")
        return 1
    print(f"In Ordnung: alle Versionsdateien stehen auf {format_version(parse_version(lies_versionen(args.wurzel)[0].version))}")
    return 0


def baue_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WAMO Versionsskript (Conventional Commits, Versionsdateien, CHANGELOG.md)")
    parser.add_argument("--wurzel", type=Path, default=WURZEL, help="Wurzel des Repos (Standard: dieses Repo)")
    unter = parser.add_subparsers(dest="befehl", required=True)
    heute = datetime.date.today().isoformat()

    sub = unter.add_parser("vorschlag", help="Vorschlag aus den Commits seit dem letzten Tag")
    sub.add_argument("--json", action="store_true", help="maschinenlesbare Ausgabe")
    sub.set_defaults(funktion=befehl_vorschlag)

    sub = unter.add_parser("changelog", help="Changelog-Abschnitt ausgeben")
    sub.add_argument("version", nargs="?", help="Version des Abschnitts (Standard: Vorschlag)")
    sub.add_argument("--datum", default=heute, help="Datum JJJJ-MM-TT (Standard: heute)")
    sub.set_defaults(funktion=befehl_changelog)

    sub = unter.add_parser("anwenden", help="Versionsdateien setzen und CHANGELOG.md ergänzen")
    sub.add_argument("version", help="Version X.Y.Z (nicht unter dem Vorschlag und über dem letzten Tag)")
    sub.add_argument("--trocken", action="store_true", help="nur anzeigen, nichts schreiben")
    sub.add_argument("--datum", default=heute, help="Datum JJJJ-MM-TT (Standard: heute)")
    sub.add_argument("--major-bestaetigt", action="store_true", help="Sprung von 0.y.z auf 1.0.0 oder höher bestätigt")
    sub.set_defaults(funktion=befehl_anwenden)

    sub = unter.add_parser("pruefen", help="Versionsdateien, Tag und CHANGELOG.md auf Abweichungen prüfen")
    sub.add_argument("--erwartet", help="erwartete Version X.Y.Z")
    sub.set_defaults(funktion=befehl_pruefen)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    roh = list(sys.argv[1:] if argv is None else argv)
    roh = ["pruefen" if a == "--pruefen" else a for a in roh]
    args = baue_parser().parse_args(roh)
    try:
        if getattr(args, "datum", None):
            datetime.date.fromisoformat(args.datum)
        return args.funktion(args)
    except (VersionsFehler, ValueError) as fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
