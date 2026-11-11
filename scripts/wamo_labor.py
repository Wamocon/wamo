"""Laborprofile verwalten (Testlabor): anlegen, aktivieren, sperren, status, loeschen.

Ein Laborprofil ist ein Profil mit profiles.labor_art (Migration 0018): Kennung labor-01 bis labor-16 (Art platz, Platz 1 bis 16)
und labor-schau (Art schaufenster). Es hat immer die Rolle mitglied und bleibt dauerhaft aktiv = false. Freigeschaltet wird
allein über labor_aktiv; nur der Kern mit Laborlogik liest es, ältere Kerne lehnen jedes Laborprofil mit 403 ab.

Wie die anderen Verwaltungsskripte (wamo_nutzer.py) läuft das Skript über ssh auf Sokrates-2 und spricht dort per
docker exec ... psql mit der Datenbank. Neu: Jede Schreibanweisung beginnt mit "set local role wamo_admin", läuft also mit den
Rechten der Verwaltungsrolle und nie mit Superuser-Rechten. Der Superuser liest nur auth.users, um ein vorhandenes Konto zu finden.
Das Löschen geht ausschließlich über die Funktion labor_loeschen (nur wamo_admin darf sie aufrufen, wamo_core nie).

Standard ist der Trockenlauf: Ohne --ausfuehren zeigt das Skript nur, was es täte, und ändert nichts. status liest nur. loeschen zählt im
Trockenlauf nur (labor_zaehlen, lesend) und zeigt, was verschwinden würde.

Geheimnisse: Das Skript gibt nie ein Passwort, einen Schlüssel oder einen Token aus. Beim Anlegen entsteht ein 24 Zeichen langes
Zufallspasswort. Es geht nur über die SSH-Eingabe zum Server und in die Umschlag-Datei (--umschlag-datei, außerhalb des Repos,
nur für den Besitzer lesbar), aus der die Betreuer es abschreiben. Der Service-Schlüssel des WAMO-Supabase bleibt auf Sokrates-2.

Aufrufe (im Repo-Hauptordner):
  python scripts/wamo_labor.py anlegen --kennung labor-01 labor-02 labor-schau --umschlag-datei D:/geheim/umschlag.txt --ausfuehren
  python scripts/wamo_labor.py aktivieren --kennung labor-01 --ausfuehren
  python scripts/wamo_labor.py sperren --alle --ausfuehren
  python scripts/wamo_labor.py status
  python scripts/wamo_labor.py loeschen --alle --ausfuehren               # alle Texte (Frist 05.12.2026), prüft danach auf null
  python scripts/wamo_labor.py loeschen --kennung labor-03 --ab 2026-11-28T09:00:00+01:00 --bis 2026-11-28T09:30:00+01:00 --ausfuehren
Optionen: --schema wamo_dev|wamo_test|wamo_prod (Standard wamo_dev), --host wamo-sokrates2
Reihenfolge vor dem Löschen: erst sperren (labor_aktiv = false), dann die Sitzung beenden lassen, dann löschen.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple

SCHEMA_RE = re.compile(r"^wamo_(dev|test|prod)$")
KENNUNG_RE = re.compile(r"^labor-(0[1-9]|1[0-6]|schau)$")
ANZEIGENAME = "Gast"
MAIL_DOMAIN = "wamo.test"
PASSWORT_LAENGE = 24
EXIT_PASSWORT_NOETIG = 10
WURZEL = Path(__file__).resolve().parent.parent

# Alle Anweisungen stehen hier an einer Stelle (und im Programm, das auf dem Server läuft). Werte gehen nur als psql-Variable
# hinein (:'wert' als Literal, :"schema" als Bezeichner), nie per Zeichenkette. Jede Anweisung außer der Kontosuche läuft als wamo_admin.
ROLLE = "set local role wamo_admin;\n"
_KENNUNG = ("case when p.labor_art = 'platz' then 'labor-' || lpad(p.labor_platz::text, 2, '0') else 'labor-schau' end")
_PLATZ = "p.labor_art = :'art' and p.labor_platz is not distinct from nullif(:'platz', '')::smallint"
_PLATZ_UPDATE = "labor_art = :'art' and labor_platz is not distinct from nullif(:'platz', '')::smallint"
SQL: dict[str, str] = {
    # lesend, als Superuser: nur die Kennung eines vorhandenen Kontos (wamo_admin darf auth.users nicht lesen)
    "konto_suchen": "select id from auth.users where lower(email) = lower(:'email') limit 1;",
    "anlegen": ROLLE + (
        "insert into :\"schema\".profiles (user_id, display_name, rolle, aktiv, labor_art, labor_platz, labor_aktiv)\n"
        "values (:'uid'::uuid, :'name', 'mitglied', false, :'art', nullif(:'platz', '')::smallint, false)\n"
        "on conflict (user_id) do nothing;"),
    "pruefen": ROLLE + (
        "select coalesce(p.labor_art, '') || '|' || coalesce(p.labor_platz::text, '') || '|' || p.aktiv::text || '|' "
        "|| p.labor_aktiv::text\n"
        "from :\"schema\".profiles p where p.user_id = :'uid'::uuid;"),
    "aktivieren": ROLLE + f"update :\"schema\".profiles set labor_aktiv = true where {_PLATZ_UPDATE} returning 1;",
    "sperren": ROLLE + f"update :\"schema\".profiles set labor_aktiv = false where {_PLATZ_UPDATE} returning 1;",
    "sperren_alle": ROLLE + "update :\"schema\".profiles set labor_aktiv = false where labor_art is not null returning 1;",
    "ids": ROLLE + f"select {_KENNUNG} || '|' || p.user_id from :\"schema\".profiles p where {_PLATZ};",
    "ids_alle": ROLLE + (f"select {_KENNUNG} || '|' || p.user_id from :\"schema\".profiles p where p.labor_art is not null\n"
                         "order by p.labor_platz nulls last;"),
    "status": ROLLE + (
        f"select {_KENNUNG} || ' | ' || p.labor_art || ' | ' || case when p.labor_aktiv then 'freigeschaltet' else 'gesperrt' end\n"
        "  || ' | ' || (select coalesce(sum(e.value::bigint), 0) from jsonb_each_text(:\"schema\".labor_zaehlen(p.user_id)) e)"
        " || ' Zeilen'\nfrom :\"schema\".profiles p where p.labor_art is not null order by p.labor_platz nulls last;"),
    "zaehlen": ROLLE + "select :\"schema\".labor_zaehlen(:'uid'::uuid);",
    "loeschen": ROLLE + ("select :\"schema\".labor_loeschen(:'uid'::uuid, nullif(:'ab', '')::timestamptz, "
                         "nullif(:'bis', '')::timestamptz);"),
}

# Das Programm, das auf Sokrates-2 läuft (python3, Eingabe als JSON über stdin). verarbeite() ist von der Außenwelt getrennt
# (psql und GoTrue werden hineingereicht), damit der Test es ohne Server ausführen kann.
REMOTE_KOERPER = r'''
import json, re, subprocess, sys, urllib.error, urllib.request


class Fehler(Exception):
    pass


def sql_ausfuehren(sql, **vars):
    cmd = ["docker", "exec", "-i", "wamo-supabase-db", "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1",
           "--single-transaction", "-tA", "-q"]
    for k, v in vars.items():
        cmd += ["-v", f"{k}={v}"]
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        raise Fehler("Datenbankfehler: " + r.stderr.strip()[:300])
    return r.stdout.strip()


def konto_anlegen(email, passwort):
    env = {}
    with open("/home/wamocon/supabase-wamo/.env", encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    req = urllib.request.Request(
        "http://localhost:55321/auth/v1/admin/users",
        data=json.dumps({"email": email, "password": passwort, "email_confirm": True}).encode(),
        headers={"apikey": env["SERVICE_ROLE_KEY"], "Authorization": "Bearer " + env["SERVICE_ROLE_KEY"],
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)["id"]
    except urllib.error.HTTPError as e:
        raise Fehler(f"Konto konnte nicht angelegt werden (HTTP {e.code})")


def kennung_von(art, platz):
    return "labor-schau" if art == "schaufenster" else f"labor-{int(platz):02d}"


def zusammenfassung(zahlen):
    teile = [f"{k} {v}" for k, v in zahlen.items() if int(v)]
    return ", ".join(teile) if teile else "nichts"


def verarbeite(d, sql, konto):
    try:
        return _verarbeite(d, sql, konto)
    except Fehler as e:
        text = str(e)
        if d.get("password"):
            text = text.replace(d["password"], "***")
        print("Fehler: " + text, file=sys.stderr)
        return 1


def _ziel(d):
    art, platz = d.get("art", ""), str(d.get("platz", "") or "")
    if art not in ("platz", "schaufenster") or (art == "platz") != bool(re.fullmatch(r"([1-9]|1[0-6])", platz)):
        raise Fehler("Ungueltige Angabe zu Art und Platz")
    return art, platz


def _verarbeite(d, sql, konto):
    schema = d.get("schema", "")
    if not re.fullmatch(r"wamo_(dev|test|prod)", schema):
        raise Fehler("Ungueltiges Schema")
    aktion, ausfuehren = d.get("aktion"), bool(d.get("ausfuehren"))
    if aktion == "status":
        print(sql(SQL["status"], schema=schema) or "Keine Laborprofile.")
        return 0
    if aktion not in ("anlegen", "aktivieren", "sperren", "loeschen", "zaehlen"):
        raise Fehler("Unbekannte Aktion")
    if aktion != "zaehlen" and not ausfuehren:
        print("Trockenlauf: nichts geaendert.")
        return 0
    if aktion == "anlegen":
        art, platz = _ziel(d)
        email = d.get("email", "")
        if not re.fullmatch(r"labor-(0[1-9]|1[0-6]|schau)@wamo\.test", email) or kennung_von(art, platz) != email.split("@")[0]:
            raise Fehler("E-Mail passt nicht zur Kennung")
        uid = sql(SQL["konto_suchen"], email=email)
        if not uid:
            if not d.get("password"):
                print("PASSWORT_NOETIG")
                return 10
            uid = konto(email, d["password"])
            print("Anmeldekonto angelegt.")
        sql(SQL["anlegen"], schema=schema, uid=uid, name="Gast", art=art, platz=platz)
        stand = sql(SQL["pruefen"], schema=schema, uid=uid)
        erwartet = f"{art}|{platz}|"
        if not stand.startswith(erwartet):
            raise Fehler(f"Das Konto passt nicht zu {kennung_von(art, platz)}: das Profil ist kein Laborprofil dieser Art und dieses Platzes")
        print(f"Laborprofil {kennung_von(art, platz)} angelegt oder vorhanden (aktiv = false, "
              f"labor_aktiv = {'true' if stand.endswith('true') else 'false'}), Schema {schema}.")
        return 0
    if aktion in ("aktivieren", "sperren"):
        if d.get("alle") and aktion == "sperren":
            n = len([z for z in sql(SQL["sperren_alle"], schema=schema).splitlines() if z.strip()])
            print(f"{n} Laborprofile gesperrt (labor_aktiv = false).")
            return 0
        art, platz = _ziel(d)
        n = len([z for z in sql(SQL[aktion], schema=schema, art=art, platz=platz).splitlines() if z.strip()])
        if n == 0:
            raise Fehler(f"Kein Laborprofil {kennung_von(art, platz)} gefunden")
        print(f"Laborprofil {kennung_von(art, platz)} " + ("freigeschaltet (labor_aktiv = true)." if aktion == "aktivieren"
                                                           else "gesperrt (labor_aktiv = false)."))
        return 0
    # loeschen und zaehlen
    if d.get("alle"):
        ids = sql(SQL["ids_alle"], schema=schema)
    else:
        art, platz = _ziel(d)
        ids = sql(SQL["ids"], schema=schema, art=art, platz=platz)
    profile = [z.split("|") for z in ids.splitlines() if z.strip()]
    if not profile:
        raise Fehler("Kein Laborprofil gefunden")
    ab, bis = d.get("ab", "") or "", d.get("bis", "") or ""
    for kennung, uid in profile:
        if aktion == "zaehlen":
            print(f"{kennung}: wuerde loeschen: " + zusammenfassung(json.loads(sql(SQL["zaehlen"], schema=schema, uid=uid))))
            continue
        geloescht = json.loads(sql(SQL["loeschen"], schema=schema, uid=uid, ab=ab, bis=bis))
        rest = json.loads(sql(SQL["zaehlen"], schema=schema, uid=uid))
        print(f"{kennung}: geloescht: " + zusammenfassung(geloescht))
        if not ab and not bis and any(int(v) for v in rest.values()):
            raise Fehler(f"{kennung}: nach dem Loeschen noch Zeilen vorhanden ({zusammenfassung(rest)}), nicht leer")
    return 0


if __name__ == "__main__":
    sys.exit(verarbeite(json.load(sys.stdin), sql_ausfuehren, konto_anlegen))
'''


def remote_programm() -> str:
    """Das vollständige Programm für den Server: die Anweisungen vorweg, dann der Körper."""
    return "SQL = " + repr(SQL) + "\n" + REMOTE_KOERPER


class Fehler(Exception):
    """Eingabe- oder Ablauffehler mit einem Text für die Person (ohne Geheimnisse)."""


class Ergebnis(NamedTuple):
    code: int
    stdout: str
    stderr: str


def run_remote(host: str, nutzlast: dict) -> Ergebnis:
    programm = base64.b64encode(remote_programm().encode("utf-8")).decode("ascii")
    befehl = f"python3 -c \"import base64;exec(base64.b64decode('{programm}').decode('utf-8'))\""
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, befehl],
                       input=json.dumps(nutzlast, ensure_ascii=False), capture_output=True, text=True, encoding="utf-8")
    return Ergebnis(r.returncode, r.stdout, r.stderr)


def kennung_pruefen(kennung: str) -> tuple[str, int | None]:
    """labor-01 bis labor-16 gehören zur Art platz, labor-schau zu schaufenster. Alles andere ist ein Fehler."""
    if not isinstance(kennung, str) or not KENNUNG_RE.fullmatch(kennung):
        raise Fehler(f"Ungültige Kennung {kennung!r}: erlaubt sind labor-01 bis labor-16 und labor-schau")
    if kennung == "labor-schau":
        return "schaufenster", None
    return "platz", int(kennung.split("-")[1])


def email_fuer(kennung: str) -> str:
    kennung_pruefen(kennung)
    return f"{kennung}@{MAIL_DOMAIN}"


def passwort_erzeugen() -> str:
    return secrets.token_urlsafe(PASSWORT_LAENGE * 3 // 4)


def _ziel(kennung: str) -> dict:
    art, platz = kennung_pruefen(kennung)
    return {"art": art, "platz": "" if platz is None else str(platz)}


def _zeit(text: str, name: str) -> datetime:
    try:
        wert = datetime.fromisoformat(text)
    except ValueError:
        raise Fehler(f"{name}: kein gültiger Zeitpunkt (erwartet ISO 8601 mit Zeitzone, zum Beispiel 2026-11-28T09:00:00+01:00)")
    if wert.tzinfo is None:
        raise Fehler(f"{name}: die Zeitzone fehlt (zum Beispiel +01:00)")
    return wert


def _umschlag_pruefen(pfad: str | None) -> Path:
    if not pfad:
        raise Fehler("Zum Anlegen gehört --umschlag-datei (Ziel für die neuen Passwörter, außerhalb des Repos)")
    datei = Path(pfad).expanduser().resolve()
    if datei == WURZEL or WURZEL in datei.parents:
        raise Fehler("Die Umschlag-Datei darf nicht im Repo liegen (Passwörter gehören nie in ein Repo)")
    if not datei.parent.is_dir():
        raise Fehler("Der Ordner der Umschlag-Datei existiert nicht")
    return datei


def _umschlag_schreiben(datei: Path, kennung: str, passwort: str) -> None:
    neu = not datei.exists()
    fd = os.open(str(datei), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(f"{kennung}\t{email_fuer(kennung)}\t{passwort}\n")
    if neu and os.name == "posix":
        os.chmod(datei, 0o600)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wamo_labor", description="Laborprofile des Testlabors verwalten (Standard: Trockenlauf)")
    sub = p.add_subparsers(dest="aktion", required=True)
    anlegen = sub.add_parser("anlegen", help="Konto und Laborprofil anlegen (aktiv = false, labor_aktiv = false)")
    anlegen.add_argument("--kennung", nargs="+", required=True, help="labor-01 bis labor-16, labor-schau")
    anlegen.add_argument("--umschlag-datei", help="Datei für die neuen Passwörter (außerhalb des Repos)")
    for name, hilfe in (("aktivieren", "Laborprofil freischalten (setzt nur labor_aktiv = true)"),
                        ("sperren", "Laborprofil sperren (setzt nur labor_aktiv = false)"),
                        ("loeschen", "gesamten Text eines Laborprofils löschen (labor_loeschen), danach auf null prüfen")):
        s = sub.add_parser(name, help=hilfe)
        s.add_argument("--kennung", nargs="+")
        if name != "aktivieren":
            s.add_argument("--alle", action="store_true", help="alle Laborprofile")
        if name == "loeschen":
            s.add_argument("--ab", help="nur ab diesem Zeitpunkt (ISO 8601 mit Zeitzone), ein Durchlauf")
            s.add_argument("--bis", help="nur bis zu diesem Zeitpunkt (ISO 8601 mit Zeitzone)")
    sub.add_parser("status", help="Laborprofile mit Freischaltung und Zeilenzahl (nur lesen)")
    for s in sub.choices.values():
        s.add_argument("--schema", default="wamo_dev")
        s.add_argument("--host", default=os.environ.get("WAMO_SSH_HOST", "wamo-sokrates2"))
        if s.prog.split()[-1] != "status":
            s.add_argument("--ausfuehren", action="store_true", help="wirklich ausführen (ohne dies nur Trockenlauf)")
    return p


def _plan(args: argparse.Namespace, kennungen: list[str]) -> str:
    ziel = "alle Laborprofile" if getattr(args, "alle", False) else ", ".join(kennungen)
    text = {
        "anlegen": f"Konto und Laborprofil anlegen für {ziel} (Rolle mitglied, aktiv = false, labor_aktiv = false, "
                   "Zufallspasswort in die Umschlag-Datei, nicht auf den Bildschirm)",
        "aktivieren": f"labor_aktiv = true setzen für {ziel}",
        "sperren": f"labor_aktiv = false setzen für {ziel}",
        "loeschen": f"gesamten Text löschen für {ziel}" + (f" im Fenster {args.ab or '...'} bis {args.bis or '...'}"
                                                           if getattr(args, "ab", None) or getattr(args, "bis", None) else ""),
    }[args.aktion]
    return f"Trockenlauf, Schema {args.schema}: {text}. Zum Ausführen --ausfuehren angeben."


def _aufrufen(remote: Callable, host: str, nutzlast: dict) -> Ergebnis:
    ergebnis = remote(host, nutzlast)
    if ergebnis.stdout.strip() and ergebnis.code != EXIT_PASSWORT_NOETIG:
        print(ergebnis.stdout.strip())
    return ergebnis


def _pruefen(args: argparse.Namespace) -> list[str]:
    """Prüft alle Angaben, bevor der Server angefasst wird. Gibt die Kennungen zurück (leer bei --alle und status)."""
    if not SCHEMA_RE.match(args.schema):
        raise Fehler("Schema muss wamo_dev, wamo_test oder wamo_prod sein.")
    if args.aktion == "status":
        return []
    kennungen = list(args.kennung or [])
    alle = getattr(args, "alle", False)
    if alle and kennungen:
        raise Fehler("Entweder --kennung oder --alle, nicht beides.")
    if not alle and not kennungen:
        raise Fehler("Es fehlt --kennung (oder --alle).")
    for k in kennungen:
        kennung_pruefen(k)
    if args.aktion == "loeschen":
        ab = _zeit(args.ab, "--ab") if args.ab else None
        bis = _zeit(args.bis, "--bis") if args.bis else None
        if ab and bis and ab > bis:
            raise Fehler("--ab liegt nach --bis.")
        if alle and (ab or bis):
            raise Fehler("--alle löscht immer alles; ein Zeitfenster gilt nur für eine Kennung.")
    return kennungen


def _anlegen(args: argparse.Namespace, kennungen: list[str], remote: Callable) -> int:
    datei = _umschlag_pruefen(args.umschlag_datei)
    for kennung in kennungen:
        nutzlast = {"aktion": "anlegen", "schema": args.schema, "email": email_fuer(kennung), **_ziel(kennung), "ausfuehren": True}
        ergebnis = _aufrufen(remote, args.host, nutzlast)
        if ergebnis.code == EXIT_PASSWORT_NOETIG:
            passwort = passwort_erzeugen()
            _umschlag_schreiben(datei, kennung, passwort)  # zuerst sichern, dann senden: ein Abbruch verliert es nie
            ergebnis = _aufrufen(remote, args.host, {**nutzlast, "password": passwort})
            print(f"Passwort für {kennung} steht in der Umschlag-Datei, nicht auf dem Bildschirm.")
        if ergebnis.code != 0:
            print(ergebnis.stderr.strip() or f"Fehler auf dem Server bei {kennung}.", file=sys.stderr)
            return 1
    return 0


def _ergebnis(ergebnis: Ergebnis) -> int:
    if ergebnis.code != 0:
        print(ergebnis.stderr.strip() or "Fehler auf Sokrates-2.", file=sys.stderr)
        return 1
    return 0


def _nutzlast(args: argparse.Namespace, kennungen: list[str], aktion: str, ausfuehren: bool) -> list[dict]:
    """Je Kennung eine Nutzlast, bei --alle genau eine. Das Fenster (ab, bis) geht nur mit, wenn es angegeben ist."""
    basis: dict = {"aktion": aktion, "schema": args.schema}
    if getattr(args, "alle", False):
        return [{**basis, "alle": True, "ausfuehren": ausfuehren}]
    liste = []
    for kennung in kennungen:
        n = {**basis, **_ziel(kennung), "ausfuehren": ausfuehren}
        for feld in ("ab", "bis"):
            if getattr(args, feld, None):
                n[feld] = getattr(args, feld)
        liste.append(n)
    return liste


def _senden(args: argparse.Namespace, remote: Callable, nutzlaesse: list[dict]) -> int:
    for nutzlast in nutzlaesse:
        if _ergebnis(_aufrufen(remote, args.host, nutzlast)):
            return 1
    return 0


def main(argv: list[str] | None = None, *, remote: Callable = run_remote) -> int:
    for strom in (sys.stdout, sys.stderr):
        try:
            strom.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    try:
        kennungen = _pruefen(args)
        if args.aktion == "status":
            return _senden(args, remote, [{"aktion": "status", "schema": args.schema, "ausfuehren": False}])
        if not args.ausfuehren:
            print(_plan(args, kennungen))
            if args.aktion != "loeschen":
                return 0
            # Trockenlauf des Löschens: nur zählen (lesend), damit sichtbar ist, was verschwinden würde
            return _senden(args, remote, _nutzlast(args, kennungen, "zaehlen", False))
        if args.aktion == "anlegen":
            return _anlegen(args, kennungen, remote)
        return _senden(args, remote, _nutzlast(args, kennungen, args.aktion, True))
    except Fehler as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
