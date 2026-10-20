"""Richtet den Datenbankzugang des Voice-Cores ein (Rolle wamo_core im eigenen WAMO-Supabase auf Sokrates-2).

Ablauf:
  1. Zufallspasswort lokal erzeugen.
  2. Nur den SCRAM-SHA-256-Hash an den Server senden (der Klartext erreicht nie Server-Logs).
  3. Pooler-Tenant und Anon-Key aus der Server-.env lesen.
  4. WAMO_DATABASE_URL und SUPABASE_PUBLISHABLE_KEY in die lokale .env eintragen.
Das Skript gibt keine Geheimnisse aus.

TLS (infra/sokrates2/wamo-tls-einrichten.sh): SUPABASE_URL ist https://192.168.178.136:55443, die DSNs tragen
sslmode=verify-full und WAMO_CA_FILE zeigt auf infra/sokrates2/wamo-ca.crt. Jeder Aufruf stellt eine ältere
.env darauf um (auch ohne --neu, ohne neues Passwort) und gleicht ui/.env.local ab.

Aufruf (im Repo-Hauptordner):  python scripts/wamo_db_rolle.py [--host sokrates2] [--neu]
Voraussetzung: SSH-Zugang zu Sokrates-2 (siehe ~/.ssh/config), laufender Stack aus
infra/sokrates2/supabase-wamo-einrichten.sh und angewendete Migration 0001.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
UI_ENV_FILE = ROOT / "ui" / ".env.local"
CA_FILE = ROOT / "infra" / "sokrates2" / "wamo-ca.crt"  # öffentliches Root-Zertifikat der WAMO-CA
SERVER_ENV = "/home/wamocon/supabase-wamo/.env"
DB_CONTAINER = "wamo-supabase-db"
DB_HOST = "192.168.178.136"
SUPABASE_URL = f"https://{DB_HOST}:55443"  # Caddy (wamo-caddy) vor Envoy, Zertifikat der WAMO-CA
OLD_HTTP_URL = f"http://{DB_HOST}:55321"  # unverschlüsselt, nur noch Übergang (Stufe 2 sperrt ihn im LAN)
POOLER_SESSION_PORT = 55322  # Supavisor im Session-Modus (eigener WAMO-Stack), mit TLS
VERIFY_MODES = ("verify-ca", "verify-full")  # geprüftes TLS; alles andere stellt with_tls() auf verify-full
SCRAM_ITERATIONS = 4096


def scram_verifier(password: str) -> str:
    """SCRAM-SHA-256-Verifier im Postgres-Format (RFC 5802 / 7677)."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def ssh(host: str, command: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
        input=stdin, capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        sys.exit(f"SSH-Befehl auf {host} fehlgeschlagen: {result.stderr.strip()[:300]}")
    return result.stdout


def server_env_value(host: str, name: str) -> str:
    value = ssh(host, f"grep -m1 '^{name}=' {SERVER_ENV} | cut -d= -f2-").strip().strip('"').strip("'")
    if not value:
        sys.exit(f"{name} fehlt in {SERVER_ENV} auf {host}.")
    return value


def read_env_lines() -> list[str]:
    if ENV_FILE.exists():
        return ENV_FILE.read_text(encoding="utf-8").splitlines()
    if ENV_EXAMPLE.exists():
        return ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    return []


def current_value(lines: list[str], key: str) -> str:
    for line in lines:
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def set_value(lines: list[str], key: str, value: str) -> list[str]:
    out, done = [], False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    return out


def sync_ui_env(lines: list[str]) -> None:
    """Oeffentliche Werte fuer die Oberflaeche nach ui/.env.local (Publishable Key ist fuer den Browser gedacht)."""
    url, key = current_value(lines, "SUPABASE_URL"), current_value(lines, "SUPABASE_PUBLISHABLE_KEY")
    if not (url and key):
        return
    ui_lines = UI_ENV_FILE.read_text(encoding="utf-8").splitlines() if UI_ENV_FILE.exists() else []
    ui_lines = set_value(ui_lines, "NEXT_PUBLIC_SUPABASE_URL", url)
    ui_lines = set_value(ui_lines, "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", key)
    if not current_value(ui_lines, "NEXT_PUBLIC_VOICE_CORE_URL"):
        ui_lines = set_value(ui_lines, "NEXT_PUBLIC_VOICE_CORE_URL", "http://localhost:7860")
    UI_ENV_FILE.write_text("\n".join(ui_lines) + "\n", encoding="utf-8")
    print(f"Oberflaeche: {UI_ENV_FILE} abgeglichen.")


# Rolle -> (Env-Schluessel, Verbindungslimit). wamo_core: Voice-Core; wamo_admin: voice-core/wamo_admin.py
ROLES = {"wamo_core": ("WAMO_DATABASE_URL", 20), "wamo_admin": ("WAMO_ADMIN_DATABASE_URL", 3)}


def with_tls(dsn: str) -> str:
    """DSN mit geprüftem TLS: sslmode verify-ca oder verify-full bleibt, jeder andere Wert (oder keiner) wird
    zu verify-full. Benutzer, Passwort, Host und übrige Parameter bleiben unverändert."""
    parts = urlsplit(dsn)
    params = parse_qsl(parts.query, keep_blank_values=True)
    modes = [v.strip().lower().replace("_", "-") for k, v in params if k == "sslmode"]
    if modes and modes[-1] in VERIFY_MODES:
        return dsn
    params = [(k, v) for k, v in params if k != "sslmode"] + [("sslmode", "verify-full")]
    return urlunsplit(parts._replace(query=urlencode(params, safe=":/\\")))


def require_ca(lines: list[str]) -> None:
    """Vor jedem Zugriff auf den Server: Ohne Root-Zertifikat würde die neue .env nicht funktionieren."""
    configured = current_value(lines, "WAMO_CA_FILE")
    if configured:
        if not Path(configured).is_file():
            print(f"Warnung: WAMO_CA_FILE zeigt auf {configured}, die Datei fehlt. Der Voice-Core lehnt dann alles ab.")
        return
    if not CA_FILE.is_file():
        sys.exit(f"{CA_FILE} fehlt (öffentliches Root-Zertifikat der WAMO-CA, siehe infra/sokrates2/README.md).")


def ensure_tls(lines: list[str]) -> tuple[list[str], list[str]]:
    """Stellt eine ältere .env auf TLS um. Liefert die neuen Zeilen und die Namen der geänderten Schlüssel."""
    changed = []
    url = current_value(lines, "SUPABASE_URL").rstrip("/")
    if not url or url.lower().startswith("http://"):
        if url and url != OLD_HTTP_URL:
            print(f"Hinweis: SUPABASE_URL war {url} (unverschlüsselt) und zeigt jetzt auf {SUPABASE_URL}.")
        lines = set_value(lines, "SUPABASE_URL", SUPABASE_URL)
        changed.append("SUPABASE_URL")
    if not current_value(lines, "WAMO_CA_FILE"):
        require_ca(lines)
        lines = set_value(lines, "WAMO_CA_FILE", str(CA_FILE))
        changed.append("WAMO_CA_FILE")
    for env_key, _ in ROLES.values():
        dsn = current_value(lines, env_key)
        if dsn and with_tls(dsn) != dsn:
            lines = set_value(lines, env_key, with_tls(dsn))
            changed.append(env_key)
    return lines, changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Datenbankzugang fuer wamo_core oder wamo_admin einrichten")
    parser.add_argument("--rolle", choices=sorted(ROLES), default="wamo_core",
                        help="wamo_core fuer den Voice-Core (Standard), wamo_admin fuer die Verwaltung")
    parser.add_argument("--host", default="sokrates2", help="SSH-Alias von Sokrates-2")
    parser.add_argument("--neu", action="store_true", help="Passwort neu setzen, auch wenn schon eingerichtet")
    args = parser.parse_args()
    env_key, conn_limit = ROLES[args.rolle]

    lines = read_env_lines()
    require_ca(lines)  # vor dem ersten ssh(): sonst wäre das Passwort auf dem Server schon geändert
    if current_value(lines, env_key) and not args.neu:
        lines, changed = ensure_tls(lines)
        if changed:
            ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"Auf TLS umgestellt: {', '.join(changed)} (Werte nicht angezeigt).")
        print(f"{env_key} ist bereits gesetzt. Mit --neu wird das Passwort erneuert.")
        sync_ui_env(lines)
        return

    password = secrets.token_urlsafe(32)  # nur A-Z a-z 0-9 - _ , keine URL-Kodierung noetig
    sql = f"alter role {args.rolle} with login password '{scram_verifier(password)}' connection limit {conn_limit};\n"
    ssh(args.host, f"docker exec -i {DB_CONTAINER} psql -U postgres -v ON_ERROR_STOP=1 -q", stdin=sql)

    tenant = server_env_value(args.host, "POOLER_TENANT_ID")
    database_url = with_tls(f"postgresql://{args.rolle}.{tenant}:{password}@{DB_HOST}:{POOLER_SESSION_PORT}/postgres")
    lines = set_value(lines, env_key, database_url)
    if args.rolle == "wamo_core":
        lines = set_value(lines, "SUPABASE_PUBLISHABLE_KEY", server_env_value(args.host, "SUPABASE_PUBLISHABLE_KEY"))
        lines = set_value(lines, "SUPABASE_URL", SUPABASE_URL)
    lines, _ = ensure_tls(lines)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Rolle {args.rolle} freigeschaltet. {env_key} steht in {ENV_FILE} (Werte nicht angezeigt).")
    sync_ui_env(lines)


if __name__ == "__main__":
    main()
