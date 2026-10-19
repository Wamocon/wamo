"""Verwaltung von WAMO-Profilen und gemeinsamen Gedächtnisvorschlägen (Kommandozeile).

Nutzt eine eigene Datenbankverbindung als Rolle wamo_admin (WAMO_ADMIN_DATABASE_URL) und das
Schema aus WAMO_DB_SCHEMA. Der Voice-Core (wamo_core) darf weder Profile anlegen noch Vorschläge
freigeben. wamo_admin darf auth.users nicht lesen: das Anmeldekonto legt ein eigenes Skript an;
hier entsteht nur das WAMO-Profil zu einer vorhandenen user_id.

Beispiele (im Ordner voice-core/):
  uv run python wamo_admin.py profile
  uv run python wamo_admin.py profil-anlegen --user-id <uuid> --name "Max Muster" --rolle mitglied --anrede sie
  uv run python wamo_admin.py vorschlaege
  uv run python wamo_admin.py freigeben <memory-id> --admin <uuid>
  uv run python wamo_admin.py ablehnen <memory-id> --admin <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from memory import ADMIN_DSN_ENV, ANREDEN, ROLLEN, AdminDirectory, DatabaseUnavailable, ProfileError, admin_database
from persona import clean_inline

HERE = Path(__file__).resolve().parent


def _load_env() -> None:
    # Gleiche Reihenfolge wie der Voice-Core: zuerst voice-core/.env, dann die im Hauptordner.
    for path in (HERE / ".env", HERE.parent / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wamo_admin", description="WAMO-Profile und gemeinsame Vorschläge verwalten")
    sub = p.add_subparsers(dest="befehl", required=True)
    sub.add_parser("profile", help="alle Profile auflisten")
    a = sub.add_parser("profil-anlegen", help="Profil zu einem vorhandenen Anmeldekonto anlegen")
    a.add_argument("--user-id", required=True, help="UUID des Kontos in auth.users")
    a.add_argument("--name", required=True, help="Anzeigename (1 bis 60 Zeichen)")
    a.add_argument("--rolle", required=True, choices=ROLLEN)
    a.add_argument("--anrede", default="du", choices=ANREDEN)
    sub.add_parser("vorschlaege", help="offene gemeinsame Vorschläge auflisten")
    for name, hilfe in (("freigeben", "Vorschlag freigeben (wird aktiv)"), ("ablehnen", "Vorschlag ablehnen")):
        r = sub.add_parser(name, help=hilfe)
        r.add_argument("memory_id", help="id des Gedächtniseintrags")
        r.add_argument("--admin", required=True, help="user_id des entscheidenden Admins")
    return p


async def _run(args: argparse.Namespace) -> int:
    directory = AdminDirectory(admin_database())
    print(f"Schema: {directory.schema}")
    if args.befehl == "profile":
        profile = await directory.list_profiles()
        if not profile:
            print("Keine Profile vorhanden.")
        for pr in profile:
            zustand = "aktiv" if pr.aktiv else "deaktiviert"
            print(f"{pr.user_id}  {clean_inline(pr.name, 60)}  Rolle: {pr.rolle}  Anrede: {pr.anrede}  ({zustand})")
        return 0
    if args.befehl == "profil-anlegen":
        pr = await directory.create_profile(args.user_id, args.name, args.rolle, args.anrede)
        print(f"Profil angelegt: {pr.name} ({pr.rolle}, Anrede {pr.anrede}), user_id {pr.user_id}")
        return 0
    if args.befehl == "vorschlaege":
        vorschlaege = await directory.list_proposed_shared()
        if not vorschlaege:
            print("Keine offenen Vorschläge.")
        for v in vorschlaege:
            # Inhalte stammen aus Gesprächen: Steuer- und Bidi-Zeichen entfernen, eine Zeile je Eintrag
            autor = clean_inline(v.autor_name or v.autor_id, 60)
            print(f"{v.id}  [{v.kind}, Wichtigkeit {v.importance}]  von {autor}, {v.created_at[:16]}\n"
                  f"    {clean_inline(v.content, 2000)}")
        return 0
    entscheidung = args.befehl  # freigeben | ablehnen
    if await directory.review_shared(args.memory_id, args.admin, entscheidung):
        print("Vorschlag freigegeben, er gilt ab sofort für alle." if entscheidung == "freigeben"
              else "Vorschlag abgelehnt.")
        return 0
    print("Kein offener gemeinsamer Vorschlag mit dieser id gefunden.", file=sys.stderr)
    return 1


async def _main_async(args: argparse.Namespace) -> int:
    try:
        return await _run(args)
    finally:
        await admin_database().close()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    _load_env()
    args = _parser().parse_args(argv)
    if not os.getenv(ADMIN_DSN_ENV):
        print(f"{ADMIN_DSN_ENV} ist nicht gesetzt. Die Verwaltung braucht die Verbindung als Rolle "
              "wamo_admin; der Voice-Core-Zugang (WAMO_DATABASE_URL) darf keine Profile anlegen und "
              "nichts freigeben.", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_main_async(args))
    except (ProfileError, PermissionError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    except DatabaseUnavailable as e:
        print(f"Datenbank: {e}", file=sys.stderr)
        return 2
    except asyncpg.PostgresError as e:
        print(f"Datenbankfehler ({type(e).__name__}): {e}", file=sys.stderr)
        return 2
    except ValueError as e:  # z. B. ungültiges WAMO_DB_SCHEMA
        print(f"Konfiguration: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
