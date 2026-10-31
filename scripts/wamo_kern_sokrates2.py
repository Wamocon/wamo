"""Richtet den WAMO-Kern auf Sokrates-2 vom PC aus ein (Betriebsart Büro). Gibt nie Geheimnisse aus.

Alle Werte gehen nur über stdin von ssh an den Server, nie über eine Befehlszeile:
  * Geheimnisse und Einstellungen an /usr/local/sbin/wamo-core-env setzen (schreibt /etc/wamo/core.env atomar)
  * der SCRAM-Hash der Datenbankrolle an psql (wie scripts/wamo_db_rolle.py); der Klartext erreicht Postgres nie

Befehle (im Repo-Hauptordner):
  python scripts/wamo_kern_sokrates2.py verteilen        infra/sokrates2 nach ~/wamo-infra, Kern-Code nach ~/wamo-infra/kern
  python scripts/wamo_kern_sokrates2.py db               eigene Login-Rolle wamo_core_s2 (Mitglied von wamo_core), DSN in core.env
  python scripts/wamo_kern_sokrates2.py geheimnisse      SONIOX, ANTHROPIC, SUPABASE_PUBLISHABLE_KEY und Einstellungen aus .env
  python scripts/wamo_kern_sokrates2.py token            Host-Token und Freigabe-Schlüssel aus %LOCALAPPDATA%\\WAMO
                                                         (host-token, host-freigabe; scripts\\wamo-tunnel-einrichten.ps1)
  python scripts/wamo_kern_sokrates2.py profil --email E PC an genau dieses Admin-Profil binden (WAMO_PC_HOST_PROFILE)
  python scripts/wamo_kern_sokrates2.py alles --email E  db, geheimnisse, token, profil, dann Neustart des Dienstes
  python scripts/wamo_kern_sokrates2.py status           Schlüsselnamen in core.env und Zustand des Dienstes
Option --neustart startet wamo-core danach neu (bei alles immer).

Datenbank: Die Rolle wamo_core_s2 ist LOGIN, INHERIT, Mitglied von wamo_core (WITH INHERIT TRUE, SET FALSE) und
bekommt deren Rollen-Einstellungen (statement_timeout usw., die werden nicht vererbt). RLS-Policies "to wamo_core"
greifen für Mitglieder (has_privs_of_role). Verbindung über Supavisor 192.168.178.136:55322 mit sslmode=verify-full
(das Pooler-Zertifikat trägt nur die LAN-Adresse). Der PC behält seine eigene Rolle wamo_core für Unterwegs.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("wamo_db_rolle", ROOT / "scripts" / "wamo_db_rolle.py")
db_rolle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_rolle)

HOST = os.environ.get("WAMO_SSH_HOST", "wamo-sokrates2")  # SSH-Alias des Dienstkontos wamocon (~/.ssh/config), per WAMO_SSH_HOST änderbar
ROLLE = "wamo_core_s2"
VERBINDUNGEN = 20
ENV_HELFER = "sudo -n /usr/local/sbin/wamo-core-env"
TOKEN_DATEI = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WAMO" / "host-token"
# Zweites Geheimnis für Freigaben kritischer PC-Aktionen (host/wamo_host.py, freigabe_entscheiden)
FREIGABE_DATEI = TOKEN_DATEI.with_name("host-freigabe")
TUNNEL_PUB = Path.home() / ".ssh" / "id_ed25519_wamo_tunnel.pub"
JOBS_PUB = Path.home() / ".ssh" / "id_ed25519_wamo_jobs.pub"
SCHEMA_RE = re.compile(r"wamo_(dev|test|prod)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}")
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43,512}")
# Aus der .env des PCs: Geheimnisse und Einstellungen, die der Kern auf Sokrates-2 genauso braucht.
# Nicht dabei: SUPABASE_URL, WAMO_CA_FILE, WAMO_DATABASE_URL des PCs, PIPECAT_ALLOWED_ORIGINS, WAMO_ENTWICKLUNG*
# (setzt die Unit bzw. dieses Skript selbst).
AUS_ENV = (
    "SONIOX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID", "SUPABASE_PUBLISHABLE_KEY", "LITELLM_API_KEY",
    "WAMO_DB_SCHEMA", "SONIOX_STT_URL", "SONIOX_TTS_URL", "SONIOX_VOICE", "SONIOX_TERMS", "CLAUDE_MODEL_FAST",
    "VISION_MODEL", "MIN_INTERRUPT_WORDS", "WAMO_CITY", "WAMO_TIMEZONE", "WAMO_GREETING", "WAMO_MAX_SITZUNG_MIN",
    "LITELLM_BASE_URL", "WAMO_SUCHE", "WAMO_SEARXNG_URL", "WAMO_SITZUNG_EINZELN", "WAMO_ANMELDUNG_MAX_STUNDEN",
    # Modellzugang je Profil: Hauptschlüssel der eigenen Claude-Schlüssel und das eine Profil, dem ANTHROPIC_API_KEY gehört
    "WAMO_SCHLUESSEL_KEK", "WAMO_SCHLUESSEL_KEK_ALT", "WAMO_CLAUDE_SCHLUESSEL_PROFIL",
    # Latenz und Unterbrechungen (wie ERLAUBT in infra/sokrates2/wamo_core_env.py)
    "WAMO_LATENZ_LOG", "WAMO_VAD_STOP_SECS", "WAMO_TTS_START_ZEICHEN", "WAMO_ECHO_FILTER", "WAMO_CACHE_VORWAERMEN",
    # Sprachverhalten (voice-core/soft_interrupt.py, wartesatz.py): Stopp an der Wortgrenze, Dämpfen, Rückmeldelaute, Wartesatz
    "WAMO_STOPP_GRENZE", "WAMO_DUCKEN", "WAMO_RUECKMELDUNG", "WAMO_WARTESATZ", "WAMO_WARTESATZ_SECS",
    # Sprachpakete A und B (voice-core/soniox_tts.py, persona.py, bot.py, learning.py, soft_interrupt.py, ja_waechter.py):
    # Tempo und Pausen der Stimme, Leerzeichen am Satzende, Persona, Pausen-Texte, Lernen, Rückmeldelaut bei Werkzeug, Ja-Wächter
    "SONIOX_SPEED", "SONIOX_REDUCE_SILENCE", "WAMO_TTS_SATZLEERZEICHEN", "WAMO_PERSONA_RUECKFRAGEN", "WAMO_PERSONA_BROWSER", "WAMO_PERSONA_TAB", "WAMO_PERSONA_PC_ORDNER", "WAMO_PERSONA_GESPRAECH", "WAMO_WARTESATZ_MODELL", "WAMO_WARTESATZ_MODELL_SECS",
    "WAMO_PAUSEN_PROMPT", "WAMO_LERNEN_OHNE_MOMENT", "WAMO_RUECKMELDUNG_WERKZEUG", "WAMO_JA_WAECHTER", "WAMO_ECHO_MODUS",
    # Fehlerbehebungen aus der Sitzung vom 31.10.2026 (voice-core/werkzeug_wache.py), Standard an
    "WAMO_ABBRUCH_HINWEIS", "WAMO_WERKZEUG_DOPPELT", "WAMO_LERNEN_HAUS_SECS", "WAMO_MARKER_FALLBACK",
)
AUS_ENV_PRAEFIX = ("WAMO_LLM_",)


def ssh(befehl: str, stdin: str | None = None) -> str:
    return db_rolle.ssh(HOST, befehl, stdin)


def env_setzen(werte: dict[str, str]) -> None:
    """Werte nur über stdin an wamo-core-env; die Ausgabe nennt nur Schlüsselnamen."""
    for name, wert in werte.items():
        if "\n" in wert or "\r" in wert or "'" in wert:
            sys.exit(f"Wert für {name} enthält unzulässige Zeichen (Zeilenumbruch oder einfaches Anführungszeichen).")
    text = "".join(f"{k}={v}\n" for k, v in werte.items())
    print(ssh(f"{ENV_HELFER} setzen", stdin=text).strip())


def lokale_env() -> list[str]:
    if not db_rolle.ENV_FILE.exists():
        sys.exit(f"{db_rolle.ENV_FILE} fehlt.")
    return db_rolle.read_env_lines()


def schema() -> str:
    wert = db_rolle.current_value(lokale_env(), "WAMO_DB_SCHEMA") or "wamo_dev"
    if not SCHEMA_RE.fullmatch(wert):
        sys.exit(f"WAMO_DB_SCHEMA {wert!r} ist ungültig.")
    return wert


def psql(sql: str) -> str:
    return ssh(f"docker exec -i {db_rolle.DB_CONTAINER} psql -U postgres -v ON_ERROR_STOP=1 -v VERBOSITY=terse -q -tA", stdin=sql)


# ---------- Befehle ----------
def verteilen(_args) -> None:
    """infra/sokrates2 nach ~/wamo-infra (vorher geleert), Kern-Code nach ~/wamo-infra/kern, öffentliche Schlüssel."""
    kern = sorted(p for p in (ROOT / "voice-core").glob("*.py") if not p.name.startswith("test_") and p.name != "conftest.py")
    kern += [ROOT / "voice-core" / "pyproject.toml", ROOT / "voice-core" / "uv.lock"]
    ssh("rm -rf ~/wamo-infra")
    subprocess.run(["scp", "-q", "-r", str(ROOT / "infra" / "sokrates2"), f"{HOST}:wamo-infra"], check=True)
    ssh("rm -rf ~/wamo-infra/__pycache__ && mkdir -p ~/wamo-infra/kern")
    subprocess.run(["scp", "-q", *map(str, kern), f"{HOST}:wamo-infra/kern/"], check=True)
    # Für wamo-job-einrichten.sh (wie in dessen Kopfkommentar) und den Tunnel
    extra = [(ROOT / "voice-core" / "dev_guard.py", "dev_guard.py"), (ROOT / "voice-core" / "werkzeugkarte.py", "werkzeugkarte.py")]
    if JOBS_PUB.is_file():
        extra.append((JOBS_PUB, "id_ed25519_wamo_jobs.pub"))
    if TUNNEL_PUB.is_file():
        extra.append((TUNNEL_PUB, "wamo-tunnel.pub"))
    for quelle, ziel in extra:
        subprocess.run(["scp", "-q", str(quelle), f"{HOST}:wamo-infra/{ziel}"], check=True)
    ssh("find ~/wamo-infra -name '*.sh' -exec sed -i 's/\\r$//' {} + && chmod -R go-w ~/wamo-infra")
    print(f"Verteilt: infra/sokrates2 und {len(kern)} Kern-Dateien nach {HOST}:~/wamo-infra"
          + (" (mit wamo-tunnel.pub)" if TUNNEL_PUB.is_file() else ""))


def _db(_args) -> None:
    passwort = secrets.token_urlsafe(32)
    sql = DB_SQL.format(rolle=ROLLE, verbindungen=VERBINDUNGEN, hash=db_rolle.scram_verifier(passwort))
    psql(sql)
    sql = None
    tenant = db_rolle.server_env_value(HOST, "POOLER_TENANT_ID")
    dsn = db_rolle.with_tls(f"postgresql://{ROLLE}.{tenant}:{passwort}@{db_rolle.DB_HOST}:{db_rolle.POOLER_SESSION_PORT}/postgres")
    env_setzen({"WAMO_DATABASE_URL": dsn})
    passwort = dsn = None
    sch = schema()
    # postgres ist im Supabase-Image kein Superuser und darf die neue Rolle nicht per SET ROLE annehmen; geprüft wird
    # deshalb über die Rechtefunktionen mit Rollenargument. Den echten Login (RLS) prüft der Kern beim Start.
    # ROLLE ist fest, sch per SCHEMA_RE geprüft: keine Eingabe von außen im SQL
    pruefung = psql(
        f"select pg_has_role('{ROLLE}', 'wamo_core', 'USAGE')::text"  # noqa: S608
        f" || ' ' || has_table_privilege('{ROLLE}', '{sch}.profiles', 'select')::text"
        f" || ' ' || has_table_privilege('{ROLLE}', '{sch}.memories', 'insert')::text"
        f" || ' ' || has_table_privilege('{ROLLE}', '{sch}.memories', 'delete')::text"
        f" || ' ' || pg_has_role('{ROLLE}', 'wamo_core', 'SET')::text;\n"
        f"select string_agg(x, ',') from (select unnest(setconfig) x from pg_db_role_setting"
        f" where setrole = '{ROLLE}'::regrole) s;\n"
    ).split("\n")
    print(f"Rolle {ROLLE} eingerichtet, DSN in core.env (Wert nicht angezeigt).")
    print(f"  erbt wamo_core, select profiles, insert memories, delete memories, set role wamo_core"
          f" (erwartet true true true false false): {pruefung[0] if pruefung else '?'}")
    print(f"  Rollen-Einstellungen: {pruefung[1] if len(pruefung) > 1 else '?'}")


DB_SQL = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = '{rolle}') then
    create role {rolle} nologin;
  end if;
end $$;
-- superuser, bypassrls und replication darf postgres im Supabase-Image nicht setzen; neue Rollen haben sie nie
-- (die Prüfung unten bricht ab, falls doch)
alter role {rolle} with login inherit nocreatedb nocreaterole connection limit {verbindungen} password '{hash}';
grant wamo_core to {rolle} with inherit true, set false;
do $$ begin
  if exists (select 1 from pg_roles where rolname = '{rolle}' and (rolsuper or rolbypassrls or rolreplication)) then
    raise exception 'Rolle {rolle} hat superuser, bypassrls oder replication';
  end if;
end $$;
-- Rollen-Einstellungen werden nicht vererbt: dieselben wie wamo_core (statement_timeout, idle_in_transaction ...)
do $$ declare kv text; begin
  for kv in select unnest(setconfig) from pg_db_role_setting
            where setrole = 'wamo_core'::regrole and setdatabase = 0 loop
    execute format('alter role {rolle} set %s = %L', split_part(kv, '=', 1), substr(kv, strpos(kv, '=') + 1));
  end loop;
end $$;
"""


def geheimnisse(_args) -> None:
    zeilen = lokale_env()
    werte = {}
    for zeile in zeilen:
        if "=" not in zeile or zeile.lstrip().startswith("#"):
            continue
        name = zeile.split("=", 1)[0].strip()
        if name in AUS_ENV or name.startswith(AUS_ENV_PRAEFIX):
            wert = db_rolle.current_value(zeilen, name)
            if wert:
                werte[name] = wert
    for pflicht in ("SONIOX_API_KEY", "SUPABASE_PUBLISHABLE_KEY"):
        if pflicht not in werte:
            sys.exit(f"{pflicht} fehlt in {db_rolle.ENV_FILE}.")
    env_setzen(werte)


def _geheimnis(datei: Path, was: str) -> str:
    try:
        wert = datei.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        sys.exit(f"{datei} fehlt (zuerst scripts\\wamo-tunnel-einrichten.ps1).")
    if not TOKEN_RE.fullmatch(wert):
        sys.exit(f"{was} in {datei} ist ungültig.")
    return wert


def token(_args) -> None:
    """Host-Token und Freigabe-Schlüssel des PCs an den Kern (beide nur über stdin, nie angezeigt)."""
    werte = {"WAMO_PC_HOST_TOKEN": _geheimnis(TOKEN_DATEI, "Token"),
             "WAMO_PC_HOST_FREIGABE": _geheimnis(FREIGABE_DATEI, "Freigabe-Schlüssel")}
    if werte["WAMO_PC_HOST_TOKEN"] == werte["WAMO_PC_HOST_FREIGABE"]:
        sys.exit("Token und Freigabe-Schlüssel sind gleich; scripts\\wamo-tunnel-einrichten.ps1 -FreigabeNeu ausführen.")
    env_setzen(werte)
    werte = None


def profil(args) -> None:
    if not args.email or not EMAIL_RE.fullmatch(args.email):
        sys.exit("--email fehlt oder ist ungültig.")
    sch = schema()
    # sch per SCHEMA_RE, E-Mail per EMAIL_RE geprüft (keine Anführungszeichen, kein Semikolon möglich)
    zeile = psql(
        f"select p.user_id::text || ' ' || p.rolle::text || ' ' || p.aktiv::text from {sch}.profiles p"  # noqa: S608
        f" join auth.users u on u.id = p.user_id where lower(u.email) = lower('{args.email}');\n"
    ).strip()
    teile = zeile.split()
    if len(teile) != 3:
        sys.exit(f"Kein Profil für {args.email} in {sch}.")
    user_id, rolle, aktiv = teile
    if rolle != "admin" or aktiv != "true":
        sys.exit(f"{args.email} ist nicht aktives Profil mit Rolle admin (rolle {rolle}, aktiv {aktiv}).")
    env_setzen({"WAMO_PC_HOST_PROFILE": user_id})
    print(f"PC gebunden an {args.email} ({user_id}).")


def neustart() -> None:
    print(ssh("sudo -n systemctl restart wamo-core && sleep 4 && systemctl is-active wamo-core || "
              "sudo -n journalctl -u wamo-core -n 20 --no-pager").strip())


def status(_args) -> None:
    print(ssh(f"{ENV_HELFER} liste; echo; systemctl is-active wamo-core; "
              "ls -la /run/wamo-core/core.sock /run/wamo-tunnel/pc.sock 2>&1 | sed 's/^/  /'").rstrip())


def alles(args) -> None:
    _db(args)
    geheimnisse(args)
    token(args)
    profil(args)


def main() -> None:
    parser = argparse.ArgumentParser(description="WAMO-Kern auf Sokrates-2 einrichten (Werte nur über stdin)")
    parser.add_argument("befehl", choices=["verteilen", "db", "geheimnisse", "token", "profil", "alles", "status"])
    parser.add_argument("--email", help="Admin-Profil, an das der PC gebunden wird (profil, alles)")
    parser.add_argument("--neustart", action="store_true", help="wamo-core danach neu starten")
    args = parser.parse_args()
    {"verteilen": verteilen, "db": _db, "geheimnisse": geheimnisse, "token": token, "profil": profil,
     "alles": alles, "status": status}[args.befehl](args)
    if args.neustart or args.befehl == "alles":
        neustart()


if __name__ == "__main__":
    main()
