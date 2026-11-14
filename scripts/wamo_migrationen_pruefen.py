#!/usr/bin/env python3
"""Migrations-Lint für die Prüfungen auf GitHub (Workflow .github/workflows/ci.yml, Job "Migrationen").

Ruft das Lint der Prüfstrecke (infra/sokrates2/release/pruefer/migration_lint.py) für die Dateien unter supabase/migrations/ auf, die der
aktuelle Stand gegenüber einer Basis hinzufügt, ändert oder löscht. Verglichen wird mit der Merge-Basis, so zählt nur, was der Zweig selbst
ändert. Nur Standardbibliothek und git, kein Netz, keine Datenbank.

  python scripts/wamo_migrationen_pruefen.py --basis <Commit oder Ref>

Ausgabe je Treffer: Pfad, Zeile und Art, nie der gefundene Text. Auf GitHub (GITHUB_ACTIONS=true) kommt je Treffer zusätzlich eine
Anmerkung am Pull Request. Exit 0 ohne Treffer, 1 mit Treffer, 2 bei einem Bedienfehler (zum Beispiel einer unbekannten Basis).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
PRUEFER = WURZEL / "infra" / "sokrates2" / "release" / "pruefer"
sys.path.insert(0, str(PRUEFER))

import migration_lint as ML  # noqa: E402
from pruefer_scans import Datei  # noqa: E402


class Bedienfehler(Exception):
    """Der Vergleich ließ sich nicht bilden (Exit-Code 2)."""


def _git(repo: Path, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode != 0:
        meldung = r.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise Bedienfehler(meldung[0][:300] if meldung else f"git {args[0]} mit Exit-Code {r.returncode}")
    return r.stdout


def _blob(repo: Path, rev: str, pfad: str) -> bytes | None:
    r = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"{rev}:{pfad}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def _liste(roh: bytes) -> list[str]:
    return [p for p in roh.decode("utf-8").split("\0") if p]


def aenderungen(repo: Path, basis: str, ziel: str = "HEAD") -> tuple[list[Datei], tuple[str, ...]]:
    """Geänderte Migrationen (alter und neuer Inhalt) und alle Pfade des Zielstands (für Nummer und Rückweg)."""
    try:
        mb = _git(repo, "merge-base", basis, ziel).decode("ascii").strip()
    except Bedienfehler as e:
        raise Bedienfehler(f"keine Merge-Basis zwischen {basis} und {ziel}: {e}") from None
    pfade = _liste(_git(repo, "diff", "--name-only", "--no-renames", "-z", mb, ziel, "--", ML.ORDNER))
    dateien = [Datei(p, _blob(repo, ziel, p), _blob(repo, mb, p)) for p in pfade]
    baum = tuple(_liste(_git(repo, "ls-tree", "-r", "--name-only", "-z", ziel)))
    return dateien, baum


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Migrations-Lint auf die Änderungen gegenüber einer Basis")
    p.add_argument("--basis", required=True, help="Commit oder Ref, mit dessen Merge-Basis verglichen wird (zum Beispiel origin/main)")
    p.add_argument("--repo", default=str(WURZEL), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    try:
        dateien, baum = aenderungen(Path(a.repo), a.basis)
    except Bedienfehler as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    if not dateien:
        print("Keine Migration geändert, nichts zu prüfen.")
        return 0
    befund = ML.migration_lint(dateien, baum)
    auf_github = os.environ.get("GITHUB_ACTIONS") == "true"
    for t in befund.treffer:
        print(f"{t.pfad}:{t.zeile}: {t.art}" + (f" ({t.detail})" if t.detail else ""))
        if auf_github:
            print(f"::error file={t.pfad},line={max(t.zeile, 1)}::Migrations-Lint: {t.art}")
    if befund.treffer_anzahl > len(befund.treffer):
        print(f"... und {befund.treffer_anzahl - len(befund.treffer)} weitere Treffer")
    if befund.fehler:
        print(f"Fehler: das Lint konnte nicht vollständig prüfen ({befund.fehler})", file=sys.stderr)
        return 1
    anzahl = "1 Datei" if len(dateien) == 1 else f"{len(dateien)} Dateien"
    if befund.treffer_anzahl:
        print(f"{anzahl} geprüft, {befund.treffer_anzahl} Treffer. Regeln: infra/sokrates2/release/pruefer/migration_lint.py")
        return 1
    print(f"{anzahl} geprüft, keine Treffer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
