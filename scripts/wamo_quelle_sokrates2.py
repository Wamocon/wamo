"""Legt den in main gemergten Stand in die lokale Quelle auf Sokrates-2 ab (Schritt 1.3, Entscheidung 02.11.2026, bis die zweite GitHub-App da ist).

Der Mensch bleibt das Tor: abgelegt wird nur ein Commit, der auf GitHub schon in origin/main steht (also gemergt ist). Ein lokaler Stand, ein Zweig oder
ein Commit, der nicht in origin/main liegt, wird abgelehnt. Das Skript schreibt nur in das eine Bare-Repository, das wamo-quelle-einrichten.sh angelegt hat;
der Hook dort nimmt ausschließlich refs/heads/main an und keine verändernde Geschichte.

Befehle (im Repo-Hauptordner):
  python scripts/wamo_quelle_sokrates2.py ablegen      holt origin/main und legt dessen Spitze ab (Standard)
  python scripts/wamo_quelle_sokrates2.py ablegen --commit <sha>   legt genau diesen Commit ab, falls er in origin/main liegt
  python scripts/wamo_quelle_sokrates2.py status       zeigt Spitze der Quelle und von origin/main
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("WAMO_SSH_HOST", "wamo-sokrates2")
REPO_FERN = "/var/lib/wamo-quelle/wamo.git"
SHA_RE = re.compile(r"[0-9a-f]{40}")


def git(*argv: str, pruefen: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=ROOT, capture_output=True, text=True, check=pruefen)


def fern_kopf() -> str | None:
    p = subprocess.run(["ssh", HOST, f"git --git-dir {REPO_FERN} rev-parse --verify --quiet refs/heads/main"], capture_output=True, text=True)
    s = p.stdout.strip()
    return s if SHA_RE.fullmatch(s) else None


def lokal_main() -> str:
    git("fetch", "--quiet", "origin", "main")
    return git("rev-parse", "origin/main").stdout.strip()


def ablegen(args: argparse.Namespace) -> int:
    spitze = lokal_main()
    commit = args.commit or spitze
    if not SHA_RE.fullmatch(commit):
        print("--commit muss ein voller SHA-1 (40 Hex-Zeichen) sein.", file=sys.stderr)
        return 2
    if git("merge-base", "--is-ancestor", commit, "origin/main", pruefen=False).returncode != 0:
        print(f"{commit[:12]} liegt nicht in origin/main: nur gemergter Stand wird abgelegt.", file=sys.stderr)
        return 1
    # Nur vorwärts: der Hook und receive.denyNonFastForwards sichern das serverseitig, hier die freundliche Meldung
    fern = fern_kopf()
    if fern and git("merge-base", "--is-ancestor", fern, commit, pruefen=False).returncode != 0:
        print(f"Die Quelle steht auf {fern[:12]}, das ist kein Vorfahre von {commit[:12]}: nur Fortschreiben ist erlaubt.", file=sys.stderr)
        return 1
    p = subprocess.run(["git", "push", f"{HOST}:{REPO_FERN}", f"{commit}:refs/heads/main"], cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        print("Ablegen fehlgeschlagen:\n" + (p.stderr or p.stdout)[-600:], file=sys.stderr)
        return 1
    print(f"Abgelegt: {commit} (Spitze von origin/main: {spitze[:12]}).")
    return 0


def status(_args: argparse.Namespace) -> int:
    print(f"origin/main:  {lokal_main()}")
    print(f"Quelle:       {fern_kopf() or '(leer oder nicht eingerichtet)'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="befehl")
    a = sub.add_parser("ablegen")
    a.add_argument("--commit")
    a.set_defaults(fn=ablegen)
    sub.add_parser("status").set_defaults(fn=status)
    args = ap.parse_args()
    if not getattr(args, "fn", None):
        args = ap.parse_args(["ablegen"])
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
