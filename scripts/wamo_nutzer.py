"""WAMO-Nutzer verwalten: Anmeldekonto im eigenen WAMO-Supabase auf Sokrates-2 anlegen und Profil freischalten.

Das Passwort wird verdeckt abgefragt und nur ueber die SSH-Eingabe (stdin) uebertragen. Der
Service-Schluessel des WAMO-Supabase bleibt auf Sokrates-2; das Skript liest ihn dort und
gibt ihn nie zurueck. Existiert das Konto schon, bleibt dessen Passwort
unveraendert und es wird nur das WAMO-Profil angelegt bzw. aktualisiert.
Die Registrierung im WAMO-Supabase ist geschlossen; Konten entstehen nur ueber dieses Skript.

Aufrufe (im Repo-Hauptordner):
  python scripts/wamo_nutzer.py anlegen --email niko@wamocon.com --name Niko --rolle admin
  python scripts/wamo_nutzer.py anlegen --email kollege@wamocon.com --name Anna --rolle mitglied --anrede du
  python scripts/wamo_nutzer.py sperren --email kollege@wamocon.com
  python scripts/wamo_nutzer.py liste
Optionen: --schema wamo_dev|wamo_test|wamo_prod (Standard wamo_dev), --host sokrates2

Nach dem Anlegen meldet sich die Person in der WAMO-Oberflaeche an und richtet beim ersten Mal
die Authenticator-App ein (zweiter Faktor ist Pflicht).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import re
import subprocess
import sys

SCHEMA_RE = re.compile(r"^wamo_(dev|test|prod)$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 12

# Laeuft auf Sokrates-2 (python3), Eingabe als JSON ueber stdin.
REMOTE = r'''
import json, re, subprocess, sys, urllib.request
d = json.load(sys.stdin)
if not re.fullmatch(r"wamo_(dev|test|prod)", d["schema"]):
    sys.exit("Ungueltiges Schema")
env = {}
with open("/home/wamocon/supabase-wamo/.env", encoding="utf-8") as fh:
    for line in fh:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

def psql(sql, **vars):
    cmd = ["docker", "exec", "-i", "wamo-supabase-db", "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-tA", "-q"]
    for k, v in vars.items():
        cmd += ["-v", f"{k}={v}"]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Datenbankfehler: " + r.stderr.strip()[:300])
    return r.stdout.strip()

action = d["action"]
if action == "liste":
    print(psql("select p.display_name || ' | ' || p.rolle || ' | ' || case when p.aktiv then 'aktiv' else 'gesperrt' end"
               " || ' | ' || u.email from :\"schema\".profiles p join auth.users u on u.id = p.user_id"
               " order by p.display_name;",
               schema=d["schema"]) or "Keine Profile.")
    sys.exit(0)

uid = psql("select id from auth.users where lower(email) = lower(:'email') limit 1;", email=d["email"])
if action == "sperren":
    if not uid:
        sys.exit("Kein Konto mit dieser E-Mail.")
    n = psql('update :"schema".profiles set aktiv = false where user_id = :\'uid\' returning user_id;', schema=d["schema"], uid=uid)
    print("Profil gesperrt." if n else "Kein WAMO-Profil vorhanden.")
    sys.exit(0)

if uid:
    print("Konto existiert bereits, Passwort bleibt unveraendert.")
else:
    if not d.get("password"):
        sys.exit("PASSWORT_NOETIG")
    req = urllib.request.Request(
        "http://localhost:55321/auth/v1/admin/users",
        data=json.dumps({"email": d["email"], "password": d["password"], "email_confirm": True}).encode(),
        headers={"apikey": env["SERVICE_ROLE_KEY"], "Authorization": "Bearer " + env["SERVICE_ROLE_KEY"],
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            uid = json.load(resp)["id"]
    except urllib.error.HTTPError as e:
        sys.exit(f"Konto konnte nicht angelegt werden (HTTP {e.code}): {e.read().decode(errors='replace')[:200]}")
    print("Anmeldekonto angelegt.")

psql('insert into :"schema".profiles (user_id, display_name, rolle, anrede, aktiv) '
     "values (:'uid', :'name', :'rolle', :'anrede', true) "
     "on conflict (user_id) do update set display_name = excluded.display_name, rolle = excluded.rolle, "
     "anrede = excluded.anrede, aktiv = true;",
     schema=d["schema"], uid=uid, name=d["name"], rolle=d["rolle"], anrede=d["anrede"])
print(f"WAMO-Profil aktiv: {d['name']} ({d['rolle']}) im Schema {d['schema']}, user_id {uid}")
'''


def run_remote(host: str, payload: dict) -> subprocess.CompletedProcess:
    program = base64.b64encode(REMOTE.encode("utf-8")).decode("ascii")
    command = f"python3 -c \"import base64;exec(base64.b64decode('{program}').decode('utf-8'))\""
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
        input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, encoding="utf-8",
    )


def ask_password() -> str:
    first = getpass.getpass(f"Neues Passwort (mindestens {MIN_PASSWORD} Zeichen): ")
    if len(first) < MIN_PASSWORD:
        sys.exit("Passwort zu kurz.")
    if getpass.getpass("Passwort wiederholen: ") != first:
        sys.exit("Passwoerter stimmen nicht ueberein.")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description="WAMO-Nutzer verwalten (WAMO-Supabase auf Sokrates-2)")
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("anlegen", help="Konto anlegen (falls neu) und WAMO-Profil freischalten")
    add.add_argument("--email", required=True)
    add.add_argument("--name", required=True, help="Anzeigename, so spricht WAMO die Person an")
    add.add_argument("--rolle", choices=["admin", "mitglied"], default="mitglied")
    add.add_argument("--anrede", choices=["du", "sie"], default="du")
    lock = sub.add_parser("sperren", help="WAMO-Profil sperren (Konto bleibt bestehen)")
    lock.add_argument("--email", required=True)
    sub.add_parser("liste", help="WAMO-Profile anzeigen")
    for p in (add, lock, sub.choices["liste"]):
        p.add_argument("--schema", default="wamo_dev")
        p.add_argument("--host", default="sokrates2")
    args = parser.parse_args()

    if not SCHEMA_RE.match(args.schema):
        sys.exit("Schema muss wamo_dev, wamo_test oder wamo_prod sein.")
    payload: dict = {"action": args.action, "schema": args.schema}
    if args.action in ("anlegen", "sperren"):
        if not EMAIL_RE.match(args.email):
            sys.exit("E-Mail-Adresse ungueltig.")
        payload["email"] = args.email
    if args.action == "anlegen":
        name = args.name.strip()
        if not 1 <= len(name) <= 60:
            sys.exit("Anzeigename muss 1 bis 60 Zeichen haben.")
        payload.update(name=name, rolle=args.rolle, anrede=args.anrede)

    result = run_remote(args.host, payload)
    if args.action == "anlegen" and "PASSWORT_NOETIG" in (result.stderr + result.stdout):
        payload["password"] = ask_password()
        result = run_remote(args.host, payload)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        sys.exit(result.stderr.strip() or "Fehler auf Sokrates-2.")


if __name__ == "__main__":
    main()
