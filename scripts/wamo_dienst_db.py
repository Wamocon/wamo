"""Richtet den Datenbankzugang der Dienste Release und Freigabe (Anker) auf Sokrates-2 ein. Gibt nie ein Passwort aus.

Je Dienst entsteht eine eigene Login-Rolle als Mitglied der Gruppenrolle aus Migration 0013 (wie bei wamo_core_s2):
    release  wamo_release_s2  (Mitglied von wamo_release)   Zugangsdatei /etc/wamo-release/db.zugang
    anker    wamo_anker_s2    (Mitglied von wamo_anker)     Zugangsdatei /etc/wamo-anker/db.zugang
Ablauf wie bei scripts/wamo_db_rolle.py und wamo_kern_sokrates2.py db: Das Passwort entsteht lokal, nur der SCRAM-Hash geht an
die Datenbank, die Zugangsdatei geht über stdin an root (0640 root:wamo-release bzw. 0600 root:root, bis es den Benutzer
wamo-anker gibt), dazu eine Kopie des öffentlichen Zertifikats der WAMO-CA im selben Ordner. Danach prüft das Skript von hier aus mit
der echten Datenbankschicht (infra/sokrates2/pg/wamo_pg.py, TLS verify-full, dieselben Startoptionen wie im Dienst): Anmeldung über
Supavisor, die Rechte der Rolle und dass fremde Rechte fehlen.

Aufrufe (im Repo-Hauptordner, mit einem Python, das psycopg hat, zum Beispiel D:\\tmp\\wamo-pg\\venv\\Scripts\\python.exe):
  python scripts/wamo_dienst_db.py einrichten release|anker [--trocken]   Rolle, Zugangsdatei, CA-Kopie, Prüfung
  python scripts/wamo_dienst_db.py pruefen release|anker                  nur den Stand der Rolle in der Datenbank anzeigen
  python scripts/wamo_dienst_db.py sperren release|anker                  Rolle auf nologin (Zugangsdatei bleibt, ist dann wertlos)
Die Prüfung mit der echten Datenbankschicht läuft nur im einrichten-Lauf (nur dort ist das Passwort bekannt; die Zugangsdatei
gehört root und wird hier nie gelesen).
Schema: WAMO_DB_SCHEMA aus der lokalen .env, sonst wamo_dev.
"""
from __future__ import annotations

import argparse
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_db_rolle as db_rolle  # noqa: E402

HOST = "wamo-sokrates2"
CA = ROOT / "infra" / "sokrates2" / "wamo-ca.crt"
SCHEMA_RE = re.compile(r"wamo_(dev|test|prod)")
VERBINDUNGEN = 5
DIENSTE = {
    "release": {"rolle": "wamo_release_s2", "gruppe_db": "wamo_release", "ordner": "/etc/wamo-release",
                "gruppe_os": "wamo-release", "modus": "0640"},
    "anker": {"rolle": "wamo_anker_s2", "gruppe_db": "wamo_anker", "ordner": "/etc/wamo-anker",
              "gruppe_os": "root", "modus": "0600"},  # der Benutzer wamo-anker entsteht erst mit dem Prozess des Freigabe-Dienstes
}

DB_SQL = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = '{rolle}') then
    create role {rolle} nologin;
  end if;
end $$;
alter role {rolle} with login inherit nocreatedb nocreaterole connection limit {verbindungen} password '{hash}';
grant {gruppe} to {rolle} with inherit true, set false;
do $$ begin
  if exists (select 1 from pg_roles where rolname = '{rolle}' and (rolsuper or rolbypassrls or rolreplication)) then
    raise exception 'Rolle {rolle} hat superuser, bypassrls oder replication';
  end if;
end $$;
-- Rollen-Einstellungen werden nicht vererbt: dieselben wie die Gruppenrolle (statement_timeout, idle_in_transaction ...)
do $$ declare kv text; begin
  for kv in select unnest(setconfig) from pg_db_role_setting
            where setrole = '{gruppe}'::regrole and setdatabase = 0 loop
    execute format('alter role {rolle} set %s = %L', split_part(kv, '=', 1), substr(kv, strpos(kv, '=') + 1));
  end loop;
end $$;
"""


def ssh(befehl: str, stdin: str | None = None) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST, befehl], input=stdin, capture_output=True,
                       text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"ssh-Befehl scheiterte ({r.returncode}): {r.stderr.strip()[:300]}")
    return r.stdout


def psql(sql: str) -> str:
    return ssh(f"docker exec -i {db_rolle.DB_CONTAINER} psql -U postgres -v ON_ERROR_STOP=1 -v VERBOSITY=terse -q -tA", stdin=sql)


def schema() -> str:
    env = ROOT / ".env"
    wert = "wamo_dev"
    if env.is_file():
        for zeile in env.read_text(encoding="utf-8").splitlines():
            if zeile.startswith("WAMO_DB_SCHEMA="):
                wert = zeile.split("=", 1)[1].strip().strip('"').strip("'") or wert
    if not SCHEMA_RE.fullmatch(wert):
        sys.exit(f"WAMO_DB_SCHEMA {wert!r} ist ungültig.")
    return wert


def datei_installieren(ziel: str, inhalt: str, gruppe: str, modus: str, ordner_modus: str) -> None:
    """Legt die Datei root-eigen ab (atomar über eine Zwischendatei, Inhalt nur über stdin, nie in einem Argument)."""
    ordner = ziel.rsplit("/", 1)[0]
    ssh(f"sudo -n install -d -m {ordner_modus} -o root -g {gruppe} {ordner} && "
        f"sudo -n tee {ziel}.neu >/dev/null && sudo -n chown root:{gruppe} {ziel}.neu && sudo -n chmod {modus} {ziel}.neu && "
        f"sudo -n mv -f {ziel}.neu {ziel}", stdin=inhalt)


def einrichten(args) -> None:
    d = DIENSTE[args.dienst]
    sch = schema()
    passwort = secrets.token_urlsafe(32)
    sql = DB_SQL.format(rolle=d["rolle"], verbindungen=VERBINDUNGEN, hash=db_rolle.scram_verifier(passwort), gruppe=d["gruppe_db"])
    tenant = db_rolle.server_env_value(HOST, "POOLER_TENANT_ID")
    datei = (f"# WAMO Zugang des Dienstes {args.dienst} zur Datenbank (scripts/wamo_dienst_db.py). Nie in ein Repo, nie ausgeben.\n"
             f"host={db_rolle.DB_HOST}\nport={db_rolle.POOLER_SESSION_PORT}\ndbname=postgres\nuser={d['rolle']}.{tenant}\n"
             f"password={passwort}\nsslrootcert={d['ordner']}/wamo-ca.crt\n")
    if args.trocken:
        print(f"[trocken] Rolle {d['rolle']} (Mitglied von {d['gruppe_db']}, höchstens {VERBINDUNGEN} Verbindungen) anlegen")
        print(f"[trocken] {d['ordner']}/db.zugang (root:{d['gruppe_os']} {d['modus']}) und {d['ordner']}/wamo-ca.crt (root:root 0644)")
        return
    if d["gruppe_os"] != "root":
        ssh(f"getent group {d['gruppe_os']} >/dev/null || {{ echo 'Gruppe {d['gruppe_os']} fehlt' >&2; exit 1; }}")
    psql(sql)
    sql = None
    ordner_modus = "0750" if d["gruppe_os"] != "root" else "0755"
    datei_installieren(f"{d['ordner']}/wamo-ca.crt", CA.read_text(encoding="utf-8"), "root", "0644", ordner_modus)
    datei_installieren(f"{d['ordner']}/db.zugang", datei, d["gruppe_os"], d["modus"], ordner_modus)
    datei = None
    print(f"Rolle {d['rolle']} und {d['ordner']}/db.zugang eingerichtet (Werte nicht angezeigt).")
    pruefen_mit(args.dienst, sch, tenant, passwort)


def pruefen_mit(dienst: str, sch: str, tenant: str, passwort: str) -> None:
    """Prüft von hier aus mit der echten Datenbankschicht (TLS verify-full, Startoptionen, Rechte der Rolle)."""
    d = DIENSTE[dienst]
    for ordner in ("pg", "release", "anker"):
        sys.path.insert(0, str(ROOT / "infra" / "sokrates2" / ordner))
    import wamo_pg as W
    from freigabe_pg import FreigabePg
    from release_pg import ReleaseDatenbank

    ziel = W.Ziel(db_rolle.DB_HOST, db_rolle.POOLER_SESSION_PORT, "postgres", f"{d['rolle']}.{tenant}", passwort, str(CA))
    pg = W.Pg(ziel, anwendung=f"wamo-{dienst}-probe")
    ok = []

    def schritt(name, aufruf):
        try:
            erg = aufruf()
        except W.PgFehler as e:
            ok.append(False)
            print(f"  FEHLER   {name}: SQLSTATE {e.sqlstate or '(keine Verbindung)'}"
                  f" [{type(e.__context__).__name__ if e.__context__ is not None else '-'}]"
                  f" {str(e.__context__).replace(passwort, '***')[:140]!r}")  # Meldungen von libpq enthalten kein Passwort, trotzdem ersetzt
            return None
        ok.append(True)
        print(f"  ok       {name}" + (f": {erg}" if erg is not None else ""))
        return erg

    print(f"== Prüfung der Rolle {d['rolle']} gegen {db_rolle.DB_HOST}:{db_rolle.POOLER_SESSION_PORT} (TLS verify-full)")
    # Supavisor merkt sich die Anmeldedaten und lehnt die erste Anmeldung direkt nach einer Passwortrotation noch mit dem alten
    # Geheimnis ab (gemessen). Im Betrieb ohne Rotation tritt das nicht auf; die Dienste wiederholen vorläufige Fehler ohnehin.
    try:
        pg.eine("select 1")
    except W.PgFehler:
        print("  Hinweis  erste Anmeldung nach der Rotation abgelehnt (Pooler kennt das neue Passwort noch nicht), zweiter Versuch")
    wirksam = schritt("Anmeldung über Supavisor, wirksame Zeitgrenzen in der Transaktion",
                      lambda: "statement_timeout " + pg.eine("select current_setting('statement_timeout')")[0])
    if wirksam is not None and wirksam != "statement_timeout 5s":
        ok.append(False)
        print(f"  FEHLER   die Zeitgrenze des Dienstes (5 s) gilt nicht: {wirksam}")
    adapter = ReleaseDatenbank(pg, sch) if dienst == "release" else FreigabePg(pg, sch)
    schritt("Funktion release_ist_admin", lambda: adapter.ist_admin("00000000-0000-4000-8000-000000000000"))
    schritt("Anfragen lesen", lambda: len(pg.zeilen(f"select 1 from {sch}.release_anfragen limit 1")) >= 0)
    # Verbotsproben: jede muss mit 42501 scheitern
    verboten = [("delete in release_anfragen", f"delete from {sch}.release_anfragen"),
                ("Zugriff auf profiles", f"select 1 from {sch}.profiles limit 1")]
    if dienst == "release":   # der Release-Dienst schreibt weder Freigaben noch den Stand
        verboten += [("insert in release_stand", f"insert into {sch}.release_stand (komponente, aktive_version) values ('ui', '0.0.0')"),
                     ("insert in release_freigaben",
                      f"insert into {sch}.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) "
                      "values (gen_random_uuid(), gen_random_uuid(), repeat('a', 40), repeat('b', 40), now())")]
    else:                     # der Anker legt keine Anfragen an (Stand und Freigaben dagegen gehören ihm)
        verboten += [("insert in release_anfragen", f"insert into {sch}.release_anfragen (angefordert_von, quelle) "
                                                    "values (gen_random_uuid(), 'main')")]
    for name, sql in verboten:
        try:
            pg.zeilen(sql)
            ok.append(False)
            print(f"  FEHLER   {name}: wurde nicht abgelehnt")
        except W.PgFehler as e:
            gut = e.sqlstate == "42501"
            ok.append(gut)
            print(f"  {'ok      ' if gut else 'FEHLER  '} {name}: abgelehnt ({e.sqlstate or 'keine Verbindung'})"
                  + ("" if gut else ", erwartet 42501"))
    if not all(ok):
        sys.exit(1)
    print("Alle Prüfungen bestanden.")


def sperren(args) -> None:
    d = DIENSTE[args.dienst]
    psql(f"alter role {d['rolle']} nologin;")
    print(f"Rolle {d['rolle']} ist gesperrt (nologin).")


def pruefen(args) -> None:
    d = DIENSTE[args.dienst]
    sch = schema()
    z = psql(f"select rolcanlogin::text || ' ' || rolconnlimit::text || ' ' || pg_has_role('{d['rolle']}', '{d['gruppe_db']}', 'USAGE')::text"
             f" || ' ' || pg_has_role('{d['rolle']}', '{d['gruppe_db']}', 'SET')::text from pg_roles where rolname = '{d['rolle']}'").strip()
    print(f"Rolle {d['rolle']} (login, Verbindungsgrenze, erbt, SET): {z or 'fehlt'}  Schema {sch}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="aktion", required=True)
    for name, fn in (("einrichten", einrichten), ("pruefen", pruefen), ("sperren", sperren)):
        s = sub.add_parser(name)
        s.add_argument("dienst", choices=sorted(DIENSTE))
        if name == "einrichten":
            s.add_argument("--trocken", action="store_true")
        s.set_defaults(fn=fn)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
