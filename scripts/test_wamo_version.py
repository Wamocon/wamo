"""Tests für scripts/wamo_version.py: Stufe aus Conventional Commits, Tags, Changelog, Versionsdateien, Git und Aufruf.

Aufruf (aus voice-core, dort liegt die Python-Umgebung):  uv run python -m pytest ../scripts/test_wamo_version.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_version as wv  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent


def c(titel: str, text: str = "", sha: str = "") -> wv.Commit:
    return wv.Commit(sha or (str(abs(hash((titel, text)))) + "0" * 40)[:40], titel, text)


def stufe(*titel: str, pfade=None, text: str = "") -> str:
    return wv.berechne([c(t, text) for t in titel], pfade).stufe


# ---------- Tabelle der Fälle: Stufe aus den Commits ----------

FAELLE = [
    ("nur fix", ["fix(ui): Absturz beim Start behoben"], "patch", "1.2.4"),
    ("nur feat", ["feat(db): Tabelle für Freigaben"], "minor", "1.3.0"),
    ("feat und fix", ["fix: kleiner Fehler", "feat: neue Karte"], "minor", "1.3.0"),
    ("Breaking mit Ausrufezeichen", ["feat(api)!: Schnittstelle neu"], "major", "2.0.0"),
    ("Breaking ohne Typ-Anhebung", ["fix!: Verhalten ändert sich"], "major", "2.0.0"),
    ("perf ist Patch", ["perf(core): schneller"], "patch", "1.2.4"),
    ("nicht konventionell", ["Mikrofon, Modellzugang, GitHub-App (#5)"], "patch", "1.2.4"),
    ("unbekannter Typ zählt als nicht konventionell", ["wip: halbfertig"], "patch", "1.2.4"),
    ("leere Liste", [], "keine", None),
    ("nur docs ohne Pfade", ["docs: README ergänzt"], "keine", None),
    ("chore und test ohne Pfade", ["chore: aufgeräumt", "test: neuer Test"], "keine", None),
]


@pytest.mark.parametrize(("name", "titel", "soll_stufe", "soll_version"), FAELLE, ids=[f[0] for f in FAELLE])
def test_stufe_und_vorschlag(name, titel, soll_stufe, soll_version):
    ergebnis = wv.berechne([c(t) for t in titel])
    vor = wv.vorschlag((1, 2, 3), ergebnis)
    assert ergebnis.stufe == soll_stufe
    assert (wv.format_version(vor.version) if vor.version else None) == soll_version


def test_breaking_change_als_fusszeile_ist_major():
    assert stufe("fix: Anmeldung", text="Beschreibung\n\nBREAKING CHANGE: Token-Format ändert sich") == "major"
    assert stufe("feat: x", text="BREAKING-CHANGE: anders") == "major"
    assert stufe("feat: x", text="Dies ist kein BREAKING CHANGE: mitten im Text") == "minor"


def test_nicht_konventionell_zaehlt_als_patch_mit_warnung():
    ergebnis = wv.berechne([c("Mikrofon, Modellzugang, GitHub-App, Oberfläche (#5)", sha="a1a5524" + "0" * 33)])
    assert ergebnis.stufe == "patch"
    assert len(ergebnis.warnungen) == 1 and "nicht konventionell" in ergebnis.warnungen[0] and "a1a5524" in ergebnis.warnungen[0]
    # konventionelle Titel erzeugen keine Warnung
    assert wv.berechne([c("fix(ui): x")]).warnungen == ()


def test_docs_allein_hebt_nicht_an_aber_aenderungen_ausserhalb_von_docs_sind_mindestens_patch():
    assert stufe("docs: Konzept", pfade=["docs/selbstueberarbeitung.md"]) == "keine"
    assert stufe("docs: Konzept", pfade=["docs\\a.md", "docs/b.md"]) == "keine"
    assert stufe("chore: Abhängigkeiten", pfade=["ui/package-lock.json"]) == "patch"
    assert stufe("refactor: umgebaut", "test: Test", pfade=["voice-core/memory.py", "docs/x.md"]) == "patch"
    assert stufe("docs: x", pfade=[]) == "keine"
    # ohne Commits gibt es nie ein Release, auch wenn Pfade vorliegen
    assert wv.berechne([], pfade=["ui/x.ts"]).stufe == "keine"


def test_revert_ist_patch_und_hebt_sein_original_im_selben_bereich_auf():
    original = c("feat(ui): neue Karte", sha="abcdef1" + "0" * 33)
    zuruecknahme = c('Revert "feat(ui): neue Karte"', "This reverts commit abcdef1" + "0" * 33 + ".", sha="1234567" + "0" * 33)
    # allein ist ein Revert ein Patch
    allein = wv.berechne([zuruecknahme])
    assert allein.stufe == "patch" and len(allein.eintraege) == 1 and allein.eintraege[0].typ == "revert"
    # mit dem Original im Bereich zählen beide nicht
    beide = wv.berechne([original, zuruecknahme], ["ui/x.tsx"])
    assert beide.stufe == "keine" and beide.eintraege == () and beide.aufgehoben == 2
    # ein anderer Commit bleibt davon unberührt
    drei = wv.berechne([original, zuruecknahme, c("fix: Tippfehler")])
    assert drei.stufe == "patch" and len(drei.eintraege) == 1
    # konventionelle Schreibweise
    assert stufe("revert: Karte zurückgenommen") == "patch"


def test_pr_nummer_und_bereich_werden_gelesen():
    eintrag = wv.parse_commit(c("feat(db): Migration 0013 (#7)"))
    assert (eintrag.typ, eintrag.bereich, eintrag.beschreibung, eintrag.pr) == ("feat", "db", "Migration 0013", 7)
    assert wv.parse_commit(c("Titel ohne Typ (#12)")).pr == 12
    assert wv.parse_commit(c("fix: kein Bezug")).pr is None


# ---------- Versionen, Tags, Wahl ----------

def test_tag_name_ist_genau_vxyz():
    assert wv.tag_name("0.1.0") == "v0.1.0"
    assert wv.tag_name((12, 0, 3)) == "v12.0.3"
    for falsch in ("v0.1.0", "0.1", "1.2.3-rc1", "01.2.3", "1.2.3.4", "", "x.y.z", " "):
        with pytest.raises(wv.VersionsFehler):
            wv.tag_name(falsch)


def test_tags_werden_streng_gelesen_und_numerisch_verglichen():
    assert wv.parse_tag("v1.2.3") == (1, 2, 3)
    for falsch in ("1.2.3", "v1.2", "v1.2.3-rc1", "v01.2.3", "release-1", "V1.2.3"):
        assert wv.parse_tag(falsch) is None
    assert wv.hoechster_tag(["v0.9.0", "v0.10.0", "v0.2.0", "junk", "v1.0.0-rc1"]) == ("v0.10.0", (0, 10, 0))
    assert wv.hoechster_tag(["junk", "v1.0"]) is None
    assert wv.hoechster_tag([]) is None


def test_erhoehen_setzt_kleinere_stellen_zurueck():
    assert wv.erhoehen((1, 2, 3), "patch") == (1, 2, 4)
    assert wv.erhoehen((1, 2, 3), "minor") == (1, 3, 0)
    assert wv.erhoehen((1, 2, 3), "major") == (2, 0, 0)
    with pytest.raises(wv.VersionsFehler):
        wv.erhoehen((1, 2, 3), "keine")


def test_versionssprung_rueckwaerts_wird_abgelehnt():
    vor = wv.vorschlag((0, 5, 0), wv.berechne([c("feat: x")]))          # Vorschlag 0.6.0
    wv.pruefe_wahl((0, 6, 0), (0, 5, 0), vor)                            # gleich dem Vorschlag
    wv.pruefe_wahl((0, 6, 3), (0, 5, 0), vor)                            # Erhöhen ist erlaubt
    wv.pruefe_wahl((0, 9, 0), (0, 5, 0), vor)
    for zu_niedrig in ((0, 5, 0), (0, 4, 9), (0, 5, 9)):                 # gleich dem Tag, darunter, unter dem Vorschlag
        with pytest.raises(wv.VersionsFehler, match="Senken ist nicht erlaubt"):
            wv.pruefe_wahl(zu_niedrig, (0, 5, 0), vor)
    # Patch-Vorschlag darf auf Minor oder Major erhöht werden, nicht darunter
    patch = wv.vorschlag((1, 0, 0), wv.berechne([c("fix: x")]))
    wv.pruefe_wahl((1, 1, 0), (1, 0, 0), patch)
    wv.pruefe_wahl((2, 0, 0), (1, 0, 0), patch)
    with pytest.raises(wv.VersionsFehler):
        wv.pruefe_wahl((1, 0, 0), (1, 0, 0), patch)


def test_ohne_aenderungen_kein_release_und_der_sprung_auf_1_0_0_braucht_bestaetigung():
    keine = wv.vorschlag((0, 1, 0), wv.berechne([]))
    assert keine.version is None
    with pytest.raises(wv.VersionsFehler, match="nichts zu veröffentlichen"):
        wv.pruefe_wahl((0, 1, 1), (0, 1, 0), keine)
    bruch = wv.vorschlag((0, 4, 2), wv.berechne([c("feat!: Bruch")]))
    assert bruch.version == (1, 0, 0) and bruch.braucht_bestaetigung
    with pytest.raises(wv.VersionsFehler, match="Bestätigung"):
        wv.pruefe_wahl((1, 0, 0), (0, 4, 2), bruch)
    wv.pruefe_wahl((1, 0, 0), (0, 4, 2), bruch, major_bestaetigt=True)
    # ab 1.0.0 ist ein Major-Sprung ohne weitere Bestätigung möglich; vor 1.0.0 gilt dieselbe Abbildung wie danach
    assert not wv.vorschlag((1, 4, 2), wv.berechne([c("feat!: Bruch")])).braucht_bestaetigung
    assert wv.vorschlag((0, 4, 2), wv.berechne([c("feat: x")])).version == (0, 5, 0)
    assert wv.vorschlag((0, 4, 2), wv.berechne([c("fix: x")])).version == (0, 4, 3)


# ---------- Changelog ----------

def test_changelog_abschnitt_aus_den_commits():
    ergebnis = wv.berechne([
        c("feat(db): Migration 0013 (#7)"), c("fix(ui): Absturz beim Start behoben."), c("refactor: Aufräumen"),
        c("feat(api)!: Schnittstelle neu"), c("fix(security): Token nicht mehr protokollieren"),
        c("docs: README"), c("Mikrofon, Modellzugang (#5)"),
    ])
    text = wv.changelog_abschnitt("0.2.0", "2026-11-01", ergebnis)
    assert text.startswith("## [0.2.0] - 2026-11-01\n\n### Hinzugefügt\n\n")
    hinzugefuegt = text.split("### Hinzugefügt")[1].split("###")[0]
    assert hinzugefuegt.index("**Bruch:** **api:** Schnittstelle neu") < hinzugefuegt.index("**db:** Migration 0013 (PR #7)")
    assert "### Geändert" in text and "- Aufräumen" in text and "- Mikrofon, Modellzugang (PR #5)" in text
    assert "### Behoben\n\n- **ui:** Absturz beim Start behoben\n" in text
    assert "### Sicherheit\n\n- **security:** Token nicht mehr protokollieren\n" in text
    assert "README" not in text                                  # docs erscheinen nicht
    assert text.index("### Hinzugefügt") < text.index("### Geändert") < text.index("### Behoben") < text.index("### Sicherheit")
    assert "–" not in text and "—" not in text
    # gleiche Eingabe, gleiche Ausgabe (Byte für Byte, Prüfstrecke 3)
    assert text == wv.changelog_abschnitt("0.2.0", "2026-11-01", ergebnis)


def test_changelog_abschnitt_fuer_reine_pflege_ist_nicht_leer():
    text = wv.changelog_abschnitt("0.1.1", "2026-11-02", wv.berechne([c("chore: Abhängigkeiten")], ["ui/package-lock.json"]))
    assert "Keine sichtbaren Änderungen" in text and "###" not in text


VORHER = """# Änderungsprotokoll

Einleitung.

## [Unveröffentlicht]

## [0.1.0] - 2026-10-22

### Hinzugefügt

- Grundlage

[Unveröffentlicht]: https://example.org/r/compare/v0.1.0...HEAD
[0.1.0]: https://example.org/r/releases/tag/v0.1.0
"""


def test_changelog_einfuegen_setzt_abschnitt_und_vergleichslinks():
    abschnitt = wv.changelog_abschnitt("0.1.1", "2026-11-01", wv.berechne([c("fix: x")]))
    neu = wv.changelog_einfuegen(VORHER, "0.1.1", abschnitt, "https://example.org/r")
    assert neu.index("## [Unveröffentlicht]") < neu.index("## [0.1.1] - 2026-11-01") < neu.index("## [0.1.0] - 2026-10-22")
    assert neu.startswith("# Änderungsprotokoll\n\nEinleitung.\n\n## [Unveröffentlicht]\n\n## [0.1.1]")
    assert neu.endswith(
        "[Unveröffentlicht]: https://example.org/r/compare/v0.1.1...HEAD\n"
        "[0.1.1]: https://example.org/r/compare/v0.1.0...v0.1.1\n"
        "[0.1.0]: https://example.org/r/releases/tag/v0.1.0\n")
    assert "\n\n\n" not in neu
    assert wv.changelog_versionen(neu) == [(0, 1, 1), (0, 1, 0)]
    # deterministisch und ein zweites Anwenden derselben Version wird abgelehnt
    assert neu == wv.changelog_einfuegen(VORHER, "0.1.1", abschnitt, "https://example.org/r")
    with pytest.raises(wv.VersionsFehler, match="schon"):
        wv.changelog_einfuegen(neu, "0.1.1", abschnitt, "https://example.org/r")


def test_changelog_einfuegen_lehnt_ruecksprung_und_handeintraege_ab():
    abschnitt = wv.changelog_abschnitt("0.0.9", "2026-11-01", wv.berechne([c("fix: x")]))
    with pytest.raises(wv.VersionsFehler, match="nicht über"):
        wv.changelog_einfuegen(VORHER, "0.0.9", abschnitt)
    mit_hand = VORHER.replace("## [Unveröffentlicht]\n", "## [Unveröffentlicht]\n\n- von Hand\n")
    with pytest.raises(wv.VersionsFehler, match="nicht leer"):
        wv.changelog_einfuegen(mit_hand, "0.1.1", wv.changelog_abschnitt("0.1.1", "2026-11-01", wv.berechne([c("fix: x")])))
    with pytest.raises(wv.VersionsFehler, match="Unveröffentlicht"):
        wv.changelog_einfuegen("# T\n\n## [0.1.0] - 2026-10-22\n\n- a\n", "0.1.1", abschnitt)


def test_changelog_ohne_repo_url_hat_keine_links():
    abschnitt = wv.changelog_abschnitt("0.1.1", "2026-11-01", wv.berechne([c("fix: x")]))
    neu = wv.changelog_einfuegen(VORHER, "0.1.1", abschnitt, None)
    assert "compare" not in neu and "https://" not in neu


# ---------- Versionsdateien ----------

PACKAGE_JSON = '{\n  "name": "wamo-%s",\n  "version": "%s",\n  "private": true,\n  "dependencies": {\n    "x": "1.0.0"\n  }\n}\n'
PACKAGE_LOCK = ('{\n  "name": "wamo-%s",\n  "version": "%s",\n  "lockfileVersion": 3,\n  "packages": {\n    "": {\n'
                '      "name": "wamo-%s",\n      "version": "%s",\n      "dependencies": {\n        "x": "1.0.0"\n      }\n    },\n'
                '    "node_modules/x": {\n      "version": "1.0.0"\n    }\n  }\n}\n')
PYPROJECT = '[project]\nname = "wamo-%s"\nversion = "%s"\ndependencies = ["x>=1"]\n\n[tool.pytest.ini_options]\nversion = "9.9.9"\n'
UV_LOCK = ('version = 1\n\n[[package]]\nname = "x"\nversion = "1.0.0"\n\n[[package]]\nname = "wamo-%s"\nversion = "%s"\n'
           'source = { virtual = "." }\n')


def baue_repo(ziel: Path, version: str = "0.1.0") -> Path:
    for ordner in wv.NPM_PAKETE:
        (ziel / ordner).mkdir(parents=True)
        (ziel / ordner / "package.json").write_text(PACKAGE_JSON % (ordner, version), encoding="utf-8", newline="")
        (ziel / ordner / "package-lock.json").write_text(PACKAGE_LOCK % (ordner, version, ordner, version), encoding="utf-8", newline="")
    for ordner in wv.PY_PAKETE:
        (ziel / ordner).mkdir(parents=True)
        name = ordner
        (ziel / ordner / "pyproject.toml").write_text(PYPROJECT % (name, version), encoding="utf-8", newline="")
        (ziel / ordner / "uv.lock").write_text(UV_LOCK % (name, version), encoding="utf-8", newline="")
    (ziel / "CHANGELOG.md").write_text(VORHER.replace("0.1.0", version), encoding="utf-8", newline="")
    return ziel


def test_versionsdateien_lesen_und_setzen_ohne_das_format_zu_aendern(tmp_path):
    repo = baue_repo(tmp_path / "r")
    funde = wv.lies_versionen(repo)
    assert len(funde) == 2 * 3 + 3 * 2 and {f.version for f in funde} == {"0.1.0"}
    assert wv.konsistenz(funde, repo) == [] and wv.gemeinsame_version(funde, repo) == (0, 1, 0)
    vorher = {p: p.read_text(encoding="utf-8") for p in repo.rglob("*") if p.is_file()}
    geaendert = wv.setze_versionen("0.2.0", repo)
    assert len(geaendert) == 10
    for pfad in repo.rglob("*"):
        if pfad.is_file() and pfad.name != "CHANGELOG.md":
            erwartet = vorher[pfad].replace("0.1.0", "0.2.0")
            if pfad.name.startswith("pyproject"):
                erwartet = vorher[pfad]            # nur die Zeile im Abschnitt [project] ändert sich
                erwartet = erwartet.replace('version = "0.1.0"', 'version = "0.2.0"', 1)
            assert pfad.read_text(encoding="utf-8") == erwartet, pfad
    assert "9.9.9" in (repo / "voice-core" / "pyproject.toml").read_text(encoding="utf-8")   # fremde Tabellen bleiben
    assert '"version": "1.0.0"' in (repo / "ui" / "package-lock.json").read_text(encoding="utf-8")  # Abhängigkeiten bleiben
    assert 'name = "x"\nversion = "1.0.0"' in (repo / "host" / "uv.lock").read_text(encoding="utf-8")
    assert {f.version for f in wv.lies_versionen(repo)} == {"0.2.0"}
    assert wv.setze_versionen("0.2.0", repo) == []        # zweites Mal ändert nichts


def test_trockenlauf_schreibt_nichts(tmp_path):
    repo = baue_repo(tmp_path / "r")
    vorher = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    geaendert = wv.setze_versionen("0.3.0", repo, trocken=True)
    assert len(geaendert) == 10
    assert {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()} == vorher


def test_zeilenenden_bleiben_erhalten(tmp_path):
    repo = baue_repo(tmp_path / "r")
    projekt = repo / "host" / "pyproject.toml"
    projekt.write_bytes(projekt.read_bytes().replace(b"\n", b"\r\n"))
    lock = repo / "host" / "uv.lock"
    lock.write_bytes(lock.read_bytes().replace(b"\n", b"\r\n"))
    wv.setze_versionen("0.1.1", repo)
    assert projekt.read_bytes().count(b"\r\n") == projekt.read_bytes().count(b"\n")
    assert b'version = "0.1.1"' in projekt.read_bytes() and b'version = "0.1.1"' in lock.read_bytes()


def test_dateien_inkonsistent_wird_gemeldet(tmp_path):
    repo = baue_repo(tmp_path / "r")
    (repo / "desktop" / "package.json").write_text(PACKAGE_JSON % ("desktop", "0.1.1"), encoding="utf-8", newline="")
    fehler = wv.konsistenz(wv.lies_versionen(repo), repo)
    assert fehler and "weichen ab" in fehler[0] and "desktop/package.json" in "\n".join(fehler)
    with pytest.raises(wv.VersionsFehler, match="nicht konsistent"):
        wv.gemeinsame_version(wv.lies_versionen(repo), repo)
    # auch ein abweichender Lock-Eintrag und eine kaputte Version fallen auf
    repo2 = baue_repo(tmp_path / "r2")
    lock = repo2 / "voice-core" / "uv.lock"
    lock.write_text(lock.read_text(encoding="utf-8").replace('name = "wamo-voice-core"\nversion = "0.1.0"',
                                                              'name = "wamo-voice-core"\nversion = "0.1.5"'), encoding="utf-8")
    assert any("uv.lock" in z for z in wv.konsistenz(wv.lies_versionen(repo2), repo2))
    repo3 = baue_repo(tmp_path / "r3")
    (repo3 / "ui" / "package.json").write_text(PACKAGE_JSON % ("ui", "v0.1.0"), encoding="utf-8", newline="")
    assert any("ungültige Version" in z for z in wv.konsistenz(wv.lies_versionen(repo3), repo3))


def test_fehlende_dateien_werden_uebersprungen(tmp_path):
    repo = baue_repo(tmp_path / "r")
    shutil.rmtree(repo / "geraete")
    (repo / "host" / "uv.lock").unlink()
    funde = wv.lies_versionen(repo)
    assert len(funde) == 2 * 3 + 2 + 1 and wv.konsistenz(funde, repo) == []     # npm 6, voice-core 2, host nur pyproject 1
    wv.setze_versionen("0.4.0", repo)
    assert {f.version for f in wv.lies_versionen(repo)} == {"0.4.0"}


# ---------- Git und Befehle in einem Wegwerf-Repo ----------

def git(repo: Path, *args: str) -> str:
    lauf = subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@wamo.invalid",
                           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                          capture_output=True, text=True, encoding="utf-8")
    assert lauf.returncode == 0, lauf.stderr
    return lauf.stdout


def commit(repo: Path, titel: str, datei: str = "x.txt", inhalt: str | None = None, text: str = "") -> None:
    pfad = repo / datei
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt if inhalt is not None else titel, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", titel + (f"\n\n{text}" if text else ""))


@pytest.fixture
def git_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git nicht verfügbar")
    repo = baue_repo(tmp_path / "g")
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "feat: Grundlage")
    return repo


def test_ohne_tag_gilt_die_version_der_dateien(git_repo):
    stand = wv.lade_stand(git_repo)
    assert stand.tag is None and stand.basis == (0, 1, 0) and len(stand.commits) == 1 and stand.hinweise
    assert wv.vorschlag(stand.basis, wv.berechne(stand.commits, stand.pfade)).version == (0, 2, 0)


def test_mit_tag_zaehlen_nur_die_commits_seitdem_und_keine_merges(git_repo):
    git(git_repo, "tag", "-a", "v0.1.0", "-m", "Release 0.1.0")
    git(git_repo, "tag", "release-1")                       # kein gültiges Tag, wird ignoriert
    stand = wv.lade_stand(git_repo)
    assert stand.tag == "v0.1.0" and stand.commits == () and wv.berechne(stand.commits, stand.pfade).stufe == "keine"
    git(git_repo, "checkout", "-q", "-b", "feature")
    commit(git_repo, "fix(ui): Fehler", "ui/a.txt")
    git(git_repo, "checkout", "-q", "main")
    commit(git_repo, "docs: Hinweis", "docs/a.md")
    git(git_repo, "merge", "--no-ff", "-q", "-m", "Merge pull request #1 from x/feature", "feature")
    stand = wv.lade_stand(git_repo)
    titel = [c_.titel for c_ in stand.commits]
    assert titel == ["docs: Hinweis", "fix(ui): Fehler"] or titel == ["fix(ui): Fehler", "docs: Hinweis"]
    assert not any(t.startswith("Merge") for t in titel)
    assert set(stand.pfade) == {"docs/a.md", "ui/a.txt"}
    ergebnis = wv.berechne(stand.commits, stand.pfade)
    assert ergebnis.stufe == "patch" and wv.vorschlag(stand.basis, ergebnis).version == (0, 1, 1)


def test_nur_docs_seit_dem_tag_ergibt_keinen_vorschlag(git_repo, capsys):
    git(git_repo, "tag", "-a", "v0.1.0", "-m", "x")
    commit(git_repo, "docs: Konzept", "docs/k.md")
    assert wv.main(["--wurzel", str(git_repo), "vorschlag"]) == 0
    assert "Vorschlag: keiner" in capsys.readouterr().out


def test_reverts_im_git_verlauf(git_repo):
    git(git_repo, "tag", "-a", "v0.1.0", "-m", "x")
    commit(git_repo, "feat(ui): Karte", "ui/karte.txt")
    git(git_repo, "revert", "--no-edit", "HEAD")
    stand = wv.lade_stand(git_repo)
    ergebnis = wv.berechne(stand.commits, stand.pfade)
    assert len(stand.commits) == 2 and ergebnis.stufe == "keine" and ergebnis.aufgehoben == 2


def test_vorschlag_json_und_anwenden_im_trockenlauf(git_repo, capsys):
    git(git_repo, "tag", "-a", "v0.1.0", "-m", "x")
    commit(git_repo, "feat(db): Tabelle (#7)", "supabase/a.sql")
    commit(git_repo, "Titel ohne Typ", "scripts/a.py")
    assert wv.main(["--wurzel", str(git_repo), "vorschlag", "--json"]) == 0
    daten = json.loads(capsys.readouterr().out)
    assert daten["version"] == "0.2.0" and daten["tag"] == "v0.2.0" and daten["stufe"] == "minor"
    assert daten["basis_tag"] == "v0.1.0" and len(daten["warnungen"]) == 1
    vorher = {p: p.read_bytes() for p in git_repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert wv.main(["--wurzel", str(git_repo), "anwenden", "0.2.0", "--trocken", "--datum", "2026-11-01"]) == 0
    ausgabe = capsys.readouterr().out
    assert "Trockenlauf" in ausgabe and "## [0.2.0] - 2026-11-01" in ausgabe and "CHANGELOG.md" in ausgabe
    assert {p: p.read_bytes() for p in git_repo.rglob("*") if p.is_file() and ".git" not in p.parts} == vorher


def test_anwenden_schreibt_versionsdateien_und_changelog_aber_setzt_kein_tag(git_repo, capsys):
    git(git_repo, "tag", "-a", "v0.1.0", "-m", "x")
    commit(git_repo, "feat(db): Tabelle (#7)", "supabase/a.sql")
    tags_vorher = git(git_repo, "tag", "--list")
    assert wv.main(["--wurzel", str(git_repo), "anwenden", "0.2.0", "--datum", "2026-11-01"]) == 0
    capsys.readouterr()
    assert {f.version for f in wv.lies_versionen(git_repo)} == {"0.2.0"}
    changelog = (git_repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [0.2.0] - 2026-11-01" in changelog and "**db:** Tabelle (PR #7)" in changelog
    assert git(git_repo, "tag", "--list") == tags_vorher            # kein Tag, kein Push
    assert wv.pruefen(git_repo) == []


def test_anwenden_lehnt_senken_ab_und_aendert_dann_nichts(git_repo, capsys):
    git(git_repo, "tag", "-a", "v0.1.0", "-m", "x")
    commit(git_repo, "feat: neu", "a.txt")
    vorher = {p: p.read_bytes() for p in git_repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    for version in ("0.1.0", "0.0.9", "0.1.5"):                    # Tag, darunter, unter dem Vorschlag 0.2.0
        assert wv.main(["--wurzel", str(git_repo), "anwenden", version]) == 1
        assert "Senken ist nicht erlaubt" in capsys.readouterr().err
    assert wv.main(["--wurzel", str(git_repo), "anwenden", "v0.2.0"]) == 1          # falsches Format
    assert wv.main(["--wurzel", str(git_repo), "anwenden", "0.2.0", "--datum", "morgen"]) == 1
    capsys.readouterr()
    assert {p: p.read_bytes() for p in git_repo.rglob("*") if p.is_file() and ".git" not in p.parts} == vorher
    # Erhöhen auf Minor-Sprung über dem Vorschlag ist erlaubt
    assert wv.main(["--wurzel", str(git_repo), "anwenden", "0.3.0", "--datum", "2026-11-01"]) == 0


def test_pruefen_schlaegt_bei_abweichung_fehl(git_repo, capsys):
    assert wv.main(["--wurzel", str(git_repo), "--pruefen"]) == 0                    # Schalterform wie Unterbefehl
    assert "In Ordnung" in capsys.readouterr().out
    assert wv.main(["--wurzel", str(git_repo), "pruefen", "--erwartet", "0.1.0"]) == 0
    assert wv.main(["--wurzel", str(git_repo), "pruefen", "--erwartet", "0.2.0"]) == 1
    capsys.readouterr()
    (git_repo / "host" / "pyproject.toml").write_text(PYPROJECT % ("host", "0.1.1"), encoding="utf-8", newline="")
    assert wv.main(["--wurzel", str(git_repo), "pruefen"]) == 1
    ausgabe = capsys.readouterr().out
    assert "Abweichungen" in ausgabe and "host/pyproject.toml" in ausgabe


def test_pruefen_meldet_dateien_unter_dem_tag_und_changelog_ohne_abschnitt(git_repo):
    git(git_repo, "tag", "-a", "v0.3.0", "-m", "x")
    fehler = wv.pruefen(git_repo)
    assert any("unter dem letzten Tag v0.3.0" in z for z in fehler)
    (git_repo / "CHANGELOG.md").unlink()
    assert any("CHANGELOG.md fehlt" in z for z in wv.pruefen(git_repo))
    (git_repo / "CHANGELOG.md").write_text("# T\n\n## [Unveröffentlicht]\n\n## [0.0.1] - 2026-01-01\n", encoding="utf-8")
    assert any("passt nicht zur Version" in z for z in wv.pruefen(git_repo))


def test_changelog_befehl_gibt_den_abschnitt_aus(git_repo, capsys):
    commit(git_repo, "fix(host): Absturz", "host/a.txt")
    assert wv.main(["--wurzel", str(git_repo), "changelog", "--datum", "2026-11-01"]) == 0
    ausgabe = capsys.readouterr().out
    assert ausgabe.startswith("## [0.2.0] - 2026-11-01") and "### Behoben" in ausgabe and "**host:** Absturz" in ausgabe


# ---------- Das echte Repo ----------

def test_echtes_repo_ist_konsistent():
    """Fällt auf, sobald eine Versionsdatei allein angehoben wird oder CHANGELOG.md hinterherhinkt."""
    assert wv.pruefen(WURZEL) == []
    funde = wv.lies_versionen(WURZEL)
    assert {f.pfad.parent.name for f in funde} == {"ui", "desktop", "voice-core", "host", "geraete"}
    assert wv.tag_name(wv.gemeinsame_version(funde, WURZEL)).startswith("v")
