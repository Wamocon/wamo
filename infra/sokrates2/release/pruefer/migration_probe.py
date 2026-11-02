"""Frische Anwendung der Migrationen auf einem Wegwerf-Postgres (Konzept docs/selbstueberarbeitung.md Abschnitt C, Schritt 2.5b).

Läuft im Annahme-Dienst (Root) NACH dem Läufer der Prüfer-Sandbox und nur, wenn sich supabase/migrations/ gegenüber der Basis geändert hat.
Die Migrationen sind fremder Code: Sie laufen nie auf einer echten Datenbank, sondern in einem Container ohne Netz, ohne Rechte, mit
schreibgeschützter Wurzel, Speicher- und Prozessgrenze und festem Zeitlimit. Das Image ist dasselbe wie das der Datenbank des Kerns
(Postgres 17.6 mit pgvector), damit "geht bei der Probe" und "geht in der Produktion" dasselbe heißen. Es wird nie nachgeladen.

Ablauf im Container (harness.sh): neuer Cluster, Attrappen für anon, authenticated, service_role, auth.users und das Schema extensions, dann
  hin     jede Migration 0001 bis N im Schema wamo_test, jede ZWEIMAL (ein zweiter Lauf darf nicht abbrechen)
  tests   alle supabase/tests/*.sql
  down    alle .down.sql in umgekehrter Reihenfolge
  wieder  alle Migrationen noch einmal frisch (nach dem Rückweg muss das Vorwärts wieder gehen)
Die Ausgabe des Containers wird nie weitergereicht: Nur Zeilen "ERG <Zufallswert> <phase> ok|fehler <kurzer Code>" zählen (der Zufallswert
kommt über stdin, eine Migration kann ihn nicht erraten), der Code besteht aus Migrationsnummer und Phase. So gelangt weder SQL-Text noch eine Servermeldung in Karte oder Protokoll.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import tempfile
from typing import Callable

IMAGE = "supabase/postgres:17.6.1.136"
DOCKER = "/usr/bin/docker"
ZEIT_S = 420.0
DATEIEN_MAX = 120
DATEI_MAX = 512 * 1024
MIG_RE = re.compile(r"^(\d{4})_[a-z0-9_]+(\.down)?\.sql$")
TEST_RE = re.compile(r"^\d{4}_[a-z0-9_]+_test\.sql$")
ERG_RE = r"^ERG {nonce} (start|stubs|hin|tests|down|wieder|ende) (ok|fehler) ([a-z0-9_:]{{1,60}})$"
PHASEN = ("start", "stubs", "hin", "tests", "down", "wieder")
AUSGABE_MAX = 64 * 1024

HARNESS = r"""#!/bin/bash
export PATH=/nix/var/nix/profiles/default/bin:/usr/bin:/bin
D=/work/data
S=wamo_test
read -r NONCE   # Zufallswert von stdin: nur diese Shell kennt ihn, Zeilen ohne ihn (etwa aus copy ... to program) zählen nicht
erg() { echo "ERG $NONCE $1 $2 $3"; }
initdb -D "$D" -U postgres --auth=trust -E UTF8 --locale=C.UTF-8 >/work/i.log 2>&1 || { erg start fehler initdb; exit 0; }
pg_ctl -D "$D" -o "-c listen_addresses= -c unix_socket_directories=/work -c fsync=off -c full_page_writes=off -c max_connections=20 -c shared_buffers=64MB" \
  -w -t 60 -l /work/pg.log start >/dev/null 2>&1 || { erg start fehler pgstart; exit 0; }
erg start ok server
q() { psql -h /work -U postgres -d postgres -X -q -v ON_ERROR_STOP=1 "$@" >/work/out.log 2>&1; }
render() { sed "s/__SCHEMA__/$S/g" "$1"; }
q -c "create role anon nologin; create role authenticated nologin; create role service_role nologin; create schema auth; create table auth.users (id uuid primary key, instance_id uuid, aud text, role text, email text, encrypted_password text, email_confirmed_at timestamptz, raw_app_meta_data jsonb, raw_user_meta_data jsonb, created_at timestamptz default now(), updated_at timestamptz default now()); create schema extensions;" \
  || { erg stubs fehler attrappen; exit 0; }
erg stubs ok attrappen
read -r NUP NDOWN NTEST </m/zahlen || { erg start fehler zahlen; exit 0; }   # erwartete Dateizahlen: ein unlesbarer Ordner darf nie wie "nichts zu tun" aussehen
hin() {
  anz=$(ls /m/mig/*.sql 2>/dev/null | grep -vc '\.down\.sql$')
  { [ "$anz" = "$NUP" ] && [ "$anz" -gt 0 ]; } || { erg "$1" fehler zahl; return 1; }
  for f in $(ls /m/mig/*.sql 2>/dev/null | grep -v '\.down\.sql$' | sort); do
    n=$(basename "$f" .sql); n=${n%%_*}
    render "$f" >/work/r.sql
    q --single-transaction -f /work/r.sql || { erg "$1" fehler "m${n}:erst"; return 1; }
    q --single-transaction -f /work/r.sql || { erg "$1" fehler "m${n}:zweit"; return 1; }
  done
  erg "$1" ok alle
}
hin hin || exit 0
[ "$(ls /m/tests/*.sql 2>/dev/null | wc -l)" = "$NTEST" ] || { erg tests fehler zahl; exit 0; }
for f in $(ls /m/tests/*.sql 2>/dev/null | sort); do
  n=$(basename "$f" .sql); n=${n%%_*}
  render "$f" >/work/t.sql
  q -f /work/t.sql || { erg tests fehler "t${n}"; exit 0; }
done
erg tests ok alle
[ "$(ls /m/mig/*.down.sql 2>/dev/null | wc -l)" = "$NDOWN" ] || { erg down fehler zahl; exit 0; }
for f in $(ls /m/mig/*.down.sql 2>/dev/null | sort -r); do
  n=$(basename "$f" .sql); n=${n%%_*}
  render "$f" >/work/d.sql
  q --single-transaction -f /work/d.sql || { erg down fehler "m${n}"; exit 0; }
done
erg down ok alle
hin wieder || exit 0
erg ende ok fertig
"""


def migrationen_geaendert(basis: str, neu: str) -> bool:
    """Unterscheidet sich supabase/migrations (und supabase/tests) zwischen den beiden Bäumen?"""
    def inhalt(wurzel: str) -> dict[str, bytes]:
        aus: dict[str, bytes] = {}
        for unter in ("supabase/migrations", "supabase/tests"):
            ordner = os.path.join(wurzel, unter)
            if not os.path.isdir(ordner) or os.path.islink(ordner):
                continue
            for name in sorted(os.listdir(ordner)):
                p = os.path.join(ordner, name)
                if os.path.islink(p) or not os.path.isfile(p):
                    aus[f"{unter}/{name}"] = b"\0nicht-regulaer"
                    continue
                with open(p, "rb") as f:
                    aus[f"{unter}/{name}"] = f.read(DATEI_MAX + 1)
        return aus
    return inhalt(basis) != inhalt(neu)


def _kopiere(neu: str, ziel: str) -> str | None:
    """Kopiert die gültigen Migrations- und Testdateien nach ziel. None, oder ein kurzer Fehlercode."""
    zahl = {"up": 0, "down": 0, "tests": 0}
    for unter, ziel_unter, muster in (("supabase/migrations", "mig", MIG_RE), ("supabase/tests", "tests", TEST_RE)):
        quelle = os.path.join(neu, unter)
        os.makedirs(os.path.join(ziel, ziel_unter), mode=0o755, exist_ok=True)
        os.chmod(os.path.join(ziel, ziel_unter), 0o755)   # ausdrücklich: eine UMask des Dienstes (0077) darf den Container nicht aussperren
        if not os.path.isdir(quelle):
            continue
        if os.path.islink(quelle):
            return "ordner_ist_link"
        namen = sorted(os.listdir(quelle))
        if len(namen) > DATEIEN_MAX:
            return "zu_viele_dateien"
        for name in namen:
            p = os.path.join(quelle, name)
            if os.path.islink(p) or not os.path.isfile(p):
                return "keine_normale_datei"
            if not muster.fullmatch(name):
                return "name_ungueltig"
            if os.path.getsize(p) > DATEI_MAX:
                return "datei_zu_gross"
            shutil.copyfile(p, os.path.join(ziel, ziel_unter, name))
            os.chmod(os.path.join(ziel, ziel_unter, name), 0o644)
            zahl["tests" if ziel_unter == "tests" else ("down" if name.endswith(".down.sql") else "up")] += 1
    with open(os.path.join(ziel, "zahlen"), "w", encoding="ascii", newline="\n") as f:
        f.write(f"{zahl['up']} {zahl['down']} {zahl['tests']}\n")
    os.chmod(os.path.join(ziel, "zahlen"), 0o644)
    return None


def docker_argv(ordner: str, name: str, image: str = IMAGE) -> list[str]:
    return [DOCKER, "run", "--rm", "--name", name, "--pull", "never", "--network", "none", "--user", "100:101",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only", "--pids-limit", "200", "-i",
            "--memory", "1g", "--memory-swap", "1g", "--cpus", "2",
            "--tmpfs", "/work:rw,size=768m,uid=100,gid=101,mode=0700", "--tmpfs", "/tmp:rw,size=64m",
            "-v", f"{ordner}:/m:ro", "--entrypoint", "bash", image, "/m/harness.sh"]


def auswerten(text: str, nonce: str) -> dict:
    """Aus der Ausgabe des Containers: {"wert", "fehler", "phasen"}. Alles außer ERG-Zeilen wird ignoriert."""
    phasen: dict[str, tuple[str, str]] = {}
    muster = re.compile(ERG_RE.format(nonce=re.escape(nonce)))
    for zeile in text.splitlines():
        m = muster.match(zeile.strip())
        if m:
            phasen[m.group(1)] = (m.group(2), m.group(3))
    for phase in PHASEN:
        if phase in phasen and phasen[phase][0] != "ok":
            return {"wert": "fehler", "fehler": f"frisch_{phase}_{phasen[phase][1]}", "phasen": phasen}
    if phasen.get("ende") != ("ok", "fertig") or any(p not in phasen for p in PHASEN):
        return {"wert": "fehler", "fehler": "frisch_unvollstaendig", "phasen": phasen}
    return {"wert": "ok", "fehler": "", "phasen": phasen}


def probe(neu: str, arbeit: str, laufen: Callable[..., subprocess.CompletedProcess] = subprocess.run, image: str = IMAGE,
          zeit_s: float = ZEIT_S) -> dict:
    """Wendet die Migrationen des Baums `neu` frisch auf einen Wegwerf-Postgres an. Wirft nie wegen des Inhalts."""
    ordner = tempfile.mkdtemp(prefix="p.", dir=arbeit)
    name = "wamo-migprobe-" + secrets.token_hex(4)
    nonce = secrets.token_hex(12)
    try:
        os.chmod(ordner, 0o755)
        fehler = _kopiere(neu, ordner)
        if fehler:
            return {"wert": "fehler", "fehler": f"frisch_{fehler}", "phasen": {}}
        pfad = os.path.join(ordner, "harness.sh")
        with open(pfad, "w", encoding="utf-8", newline="\n") as f:
            f.write(HARNESS)
        os.chmod(pfad, 0o755)
        try:
            p = laufen(docker_argv(ordner, name, image), input=f"{nonce}\n".encode("ascii"), capture_output=True, timeout=zeit_s,
                       env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}, cwd="/")
        except subprocess.TimeoutExpired:
            try:
                laufen([DOCKER, "kill", name], stdin=subprocess.DEVNULL, capture_output=True, timeout=30, cwd="/")
            except (OSError, subprocess.SubprocessError):
                pass
            return {"wert": "fehler", "fehler": "frisch_zeitlimit", "phasen": {}}
        except OSError:
            return {"wert": "fehler", "fehler": "frisch_docker_fehlt", "phasen": {}}
        text = (p.stdout or b"")[:AUSGABE_MAX].decode("utf-8", errors="replace")
        if p.returncode != 0 and "ERG " not in text:
            return {"wert": "fehler", "fehler": "frisch_container_fehler", "phasen": {}}
        return auswerten(text, nonce)
    finally:
        shutil.rmtree(ordner, ignore_errors=True)
