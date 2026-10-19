#!/usr/bin/python3 -I
"""WAMO-Auftragsdispatcher auf Sokrates-2: Forced Command des SSH-Schlüssels "wamo-jobs".

Installiert als /opt/wamo/bin/wamo-job-dispatch (root:wamo 0750; /opt/wamo gehört root, wamo kann nichts
umbenennen) durch wamo-job-einrichten.sh. Der Schlüssel des Voice-Cores steht in
/etc/ssh/authorized_keys.d/wamo (root, über den sshd-Drop-in /etc/ssh/sshd_config.d/wamo.conf) als

    restrict,from="192.168.178.0/24",command="/opt/wamo/bin/wamo-job-dispatch" ssh-ed25519 AAAA... wamo-jobs

Damit startet dieser Schlüssel nie eine Shell, sondern immer dieses Programm. Der gewünschte Befehl kommt
aus SSH_ORIGINAL_COMMAND und wird streng geprüft (feste Muster, keine Shell):

    ping                                        Erreichbarkeit und Version (JSON)
    start   <job> <projekt> <art> [<session>]   Auftrag ausführen, mit Session-ID als Fortsetzung
    status  <job>                               Zustand des Auftrags (JSON)
    pending <job>                               offene Freigabe-Anfragen, geprüft (JSON-Zeilen)
    approve <job> <fingerabdruck>               Freigabe für den nächsten Lauf des Auftrags
    cancel  <job>                               laufenden Auftrag beenden

start: Die erste stdin-Zeile ist ein JSON-Objekt {"auftrag": "...", "system": "..."}. Danach bleibt stdin
offen und dient als Lebenslinie: endet sie (Auflegen, Verbindungsabbruch, Voice-Core beendet), wird der
Auftrag sofort beendet. stdout trägt die stream-json-Ausgabe von Claude Code und eigene Zeilen
{"type": "wamo", "ereignis": ...}; Zeilen dieses Typs aus der Sandbox werden verworfen. Ablauf je Lauf:

    1. Auftragsordner /srv/wamo/jobs/<job> anlegen bzw. prüfen (Projekt und Art sind an den Auftrag gebunden).
    2. Sperren: ein Lauf je Auftrag, ein Auftrag je Projekt, höchstens PARALLEL_MAX gleichzeitig.
    3. Freigaben aus freigaben.json (außerhalb der Sandbox) als schreibgeschützte approvals.json für
       diesen Lauf bereitstellen; Anfragen des letzten Laufs nach angefragt.jsonl übernehmen.
       Projektordner entschärfen: .claude/settings*.json, .mcp.json, Git-Hooks weg, .git/config nur
       mit einer Allowlist (Autostart-Flächen, die ein früherer Lauf gepflanzt haben könnte).
    4. Selbsttest in der Sandbox: Wächter-Hook mit einem bekannten Sperrfall (genaue Ablehnung), /etc/wamo
       unsichtbar, approvals.json schreibgeschützt, Wächterpfade nicht umbenennbar, Landlock-Sperre auf
       einen eigenen Probe-Port, Ausgangsfirewall zu Docker-Bridge und LAN. Scheitert er, startet nichts.
    5. Token nur der Auftragsart aus /etc/wamo/geheim.env als Umgebung (nie argv) an wamo-job.sh. Bekäme der
       Lauf ein Token, das ein früherer Lauf im selben Projekt nicht hatte, startet er nur nach einer
       Freigabe (Ereignis zugang_erweitert) oder auf einem frischen, leeren Projektordner.

Nur Standardbibliothek. Wächter und Werkzeugkarte kommen aus /opt/wamo/waechter (Kopien aus voice-core).
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

VERSION = "3"

HEIM = Path("/srv/wamo")
APP = Path("/opt/wamo")  # root-eigen; Programme in bin/, Wächter in waechter/ (in der Sandbox derselbe Pfad)
BIN = APP / "bin"
WAECHTER = APP / "waechter"
JOBS = HEIM / "jobs"
PROJEKTE = HEIM / "projekte"
LOGS = HEIM / "logs"
RUNNER = BIN / "wamo-job.sh"
MANAGED = APP / "claude-code" / "managed-settings.json"
GEHEIM = Path("/etc/wamo/geheim.env")
CLAUDE = HEIM / ".local" / "bin" / "claude"  # derselbe Pfad in der Sandbox
NPM_GLOBAL = HEIM / ".npm-global"  # in der Sandbox nur lesbar
SELF_REPO = "wamo"
PARALLEL_MAX = 1  # Aufträge teilen sich Loopback (kein eigener Netz-Namensraum): nur einer gleichzeitig
LAN_PROBE = "192.168.178.1"  # Ziel der Firewall-Probe im Selbsttest (Router, Port 443)
ENTWICKLUNGS_PORTS = range(20000, 21000)  # 127.0.0.1:20000-20999 lässt der Ausgangsfilter für eigene Server zu
MAX_OFFENE_FREIGABEN = 10  # Karten je Auftrag
ZUGANG_TOOL = "WAMO-Zugang"  # Freigabe-Anfrage für mehr Zugänge als frühere Läufe im Projekt

# Exit-Codes von "start" (zusätzlich zu den Ereignissen im Datenstrom)
EXIT_OK = 0
EXIT_ABGEWIESEN = 2
EXIT_SELBSTTEST = 3
EXIT_BELEGT = 4
EXIT_KEIN_ZUGANG = 5
EXIT_INTERN = 6
EXIT_ZUGANG_ERWEITERT = 7
EXIT_ZEITLIMIT = 124
EXIT_ABGEBROCHEN = 130

COMMAND_RE = re.compile(r"[a-z]{2,10}(?: [A-Za-z0-9_-]{1,64}){0,4}")
JOB_RE = re.compile(r"[0-9a-f]{16,32}")
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,48}[a-z0-9]")
SESSION_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FP_RE = re.compile(r"[0-9a-f]{64}")  # voller SHA-256 aus dev_guard.fingerprint
ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
WORKSPACE_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")
MAX_COMMAND_CHARS = 300
MAX_HEADER_BYTES = 256 * 1024
MAX_TEXT_CHARS = 20000
MAX_PENDING_BYTES = 2 * 1024 * 1024
MAX_PENDING_LINES = 200
MAX_PENDING_LINE_BYTES = 64 * 1024
MAX_STRING_CHARS = 8192
DECIDE_TIMEOUT_S = 1.0
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_ANGEFRAGT_BYTES = 1024 * 1024
MAX_ZEILE_STDOUT = 4 * 1024 * 1024  # dev_agent liest bis 10 MiB je Zeile; längere Zeilen werden verworfen
MAX_ZEILE_STDERR = 1024 * 1024
SPEICHER_DISPATCHER = 1024 * 1024 * 1024  # weiche Adressraumgrenze dieses Prozesses (RLIMIT_AS)

SELFTEST_EVENT = {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}
SELFTEST_GRUND = "GESPERRT: Force-Push"  # Anfang der erwarteten Ablehnung durch dev_guard.py
PLUGIN_SETTINGS = json.dumps({"enabledPlugins": {"ecc@ecc": True}})
# Aus der globalen Claude-Konfiguration von wamo nur diese Schlüssel in den Auftrag übernehmen
CLAUDE_JSON_KEYS = (
    "installMethod", "userID", "machineID", "firstStartTime", "firstStartVersion",
    "migrationVersion", "opusProMigrationComplete", "sonnet1m45MigrationComplete",
    "hasResetAutoModeOptInForDefaultOffer", "autoUpdatesProtectedForNative",
)
SETTINGS_KEYS = ("enabledPlugins", "extraKnownMarketplaces")  # Rest (Hooks, Helfer, statusLine) nie übernehmen


class Abgewiesen(Exception):
    """Aufruf verletzt die Regeln; wird protokolliert und mit Code 2 beantwortet."""


class _Zeitueberschreitung(Exception):
    pass


# ---------------------------------------------------------------- Prüfung der Argumente
def werkzeugkarte():
    return _modul("werkzeugkarte")


def waechter():
    return _modul("dev_guard")


def _modul(name: str):
    if str(WAECHTER) not in sys.path and (WAECHTER / f"{name}.py").is_file():
        sys.path.insert(0, str(WAECHTER))
    return __import__(name)


def check_job(value: str) -> str:
    if not JOB_RE.fullmatch(value):
        raise Abgewiesen("Auftrags-ID ungültig")
    return value


def check_project(value: str) -> str:
    if not PROJECT_RE.fullmatch(value) or "--" in value:
        raise Abgewiesen("Projektname ungültig (3 bis 50 Zeichen, a-z, 0-9, Bindestrich)")
    if value == SELF_REPO:
        raise Abgewiesen("Das WAMO-Repo selbst ist für Aufträge gesperrt")
    return value


def check_art(value: str) -> str:
    if value not in werkzeugkarte().AUFTRAGSARTEN:
        raise Abgewiesen("Auftragsart unbekannt")
    return value


def check_session(value: str) -> str:
    if not SESSION_RE.fullmatch(value):
        raise Abgewiesen("Session-ID ungültig")
    return value


def check_fp(value: str) -> str:
    if not FP_RE.fullmatch(value):
        raise Abgewiesen("Fingerabdruck ungültig")
    return value


BEFEHLE: dict[str, tuple[int, tuple]] = {
    # Befehl: (Pflichtargumente, Prüfer je Position)
    "ping": (0, ()),
    "start": (3, (check_job, check_project, check_art, check_session)),
    "status": (1, (check_job,)),
    "pending": (1, (check_job,)),
    "approve": (2, (check_job, check_fp)),
    "cancel": (1, (check_job,)),
}


def parse_command(raw: str | None) -> tuple[str, list[str]]:
    """Prüft den Befehl aus SSH_ORIGINAL_COMMAND. Ohne Befehl (interaktive Anmeldung) wird abgewiesen."""
    if not raw:
        raise Abgewiesen("kein Befehl (Shell-Zugang ist mit diesem Schlüssel gesperrt)")
    if len(raw) > MAX_COMMAND_CHARS or not COMMAND_RE.fullmatch(raw):
        raise Abgewiesen("Befehl hat unzulässige Zeichen oder Form")
    name, *args = raw.split(" ")
    spec = BEFEHLE.get(name)
    if spec is None:
        raise Abgewiesen("unbekannter Befehl")
    required, checks = spec
    if not required <= len(args) <= len(checks):
        raise Abgewiesen("falsche Anzahl Argumente")
    return name, [check(arg) for check, arg in zip(checks, args)]


def parse_header(raw: bytes) -> tuple[str, str]:
    """Erste stdin-Zeile von start: {"auftrag": str, "system": str}. Steuerzeichen außer Zeilenumbruch
    und Tabulator fliegen heraus; beide Texte sind begrenzt."""
    if not raw.endswith(b"\n") or len(raw) > MAX_HEADER_BYTES:
        raise Abgewiesen("Kopfzeile fehlt oder ist zu lang")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Abgewiesen("Kopfzeile ist kein JSON") from None
    if not isinstance(data, dict) or set(data) - {"auftrag", "system"}:
        raise Abgewiesen("Kopfzeile hat unbekannte Felder")
    auftrag, system = data.get("auftrag"), data.get("system", "")
    if not isinstance(auftrag, str) or not isinstance(system, str):
        raise Abgewiesen("Kopfzeile: auftrag und system müssen Text sein")
    auftrag, system = _clean(auftrag), _clean(system)
    if not auftrag.strip():
        raise Abgewiesen("Auftrag ist leer")
    if len(auftrag) > MAX_TEXT_CHARS or len(system) > MAX_TEXT_CHARS:
        raise Abgewiesen("Auftrag oder Systemtext zu lang")
    return auftrag, system


def _clean(text: str) -> str:
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))


# ---------------------------------------------------------------- Geheimnisse und Umgebung
def lese_geheimnisse(path: Path | None = None) -> dict[str, str]:
    """KEY=VALUE aus /etc/wamo/geheim.env (Kommentare, export und Anführungszeichen erlaubt).
    Leere Werte zählen als nicht gesetzt. Werte werden nie protokolliert."""
    try:
        text = (path or GEHEIM).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if ENV_NAME_RE.fullmatch(name) and value:
            out[name] = value
    return out


def basis_env() -> dict[str, str]:
    """Umgebung für wamo-job.sh ohne Token (systemd-run --user braucht den Laufzeitordner)."""
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "XDG_RUNTIME_DIR": f"/run/user/{uid}",
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}


def zugangs_quellen(art: str, geheim: dict[str, str]) -> set[str]:
    """Namen in geheim.env, deren Werte ein Lauf dieser Art bekommt (für die Buchführung je Projekt)."""
    wk = werkzeugkarte()
    claude = next((name for name in wk.CLAUDE_VARIABLEN if geheim.get(name)), None)
    return ({claude} if claude else set()) | {n for n in wk.zugangs_variablen(art) if geheim.get(n)}


def sandbox_env(art: str, geheim: dict[str, str]) -> dict[str, str] | None:
    """Basis plus genau ein Claude-Zugang und die Token der Auftragsart. None ohne Claude-Zugang."""
    wk = werkzeugkarte()
    claude = next((name for name in wk.CLAUDE_VARIABLEN if geheim.get(name)), None)
    if claude is None:
        return None
    env = basis_env()
    env[claude] = geheim[claude]
    workspace = geheim.get("ANTHROPIC_WORKSPACE_ID", "")
    if claude == "ANTHROPIC_API_KEY" and WORKSPACE_RE.fullmatch(workspace):
        # Organisationsschlüssel ohne festen Workspace: Claude Code schickt den Header bei jeder Anfrage mit
        env["ANTHROPIC_CUSTOM_HEADERS"] = f"anthropic-workspace-id: {workspace}"
    for name in wk.zugangs_variablen(art):
        if geheim.get(name):
            env[wk.sandbox_name(name)] = geheim[name]
    # Git in jedem Repo der Sandbox: keine Hooks und kein fsmonitor (Vorrang vor Repo-Konfigurationen, auch in
    # Submodulen und eingebetteten Repos, die die Entschärfung vor dem Lauf nicht erreicht)
    git = [("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false")]
    if "GH_TOKEN" in env:
        # git über HTTPS meldet sich mit dem Token an (gh liest GH_TOKEN selbst); keine Datei nötig
        git += [("credential.https://github.com.helper", ""),
                ("credential.https://github.com.helper", "!/usr/bin/gh auth git-credential")]
    env["GIT_CONFIG_COUNT"] = str(len(git))
    for i, (key, value) in enumerate(git):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    return env


# ---------------------------------------------------------------- MCP-Server prüfen
PLATZHALTER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-([^}]*))?\}")
HARMLOS_RE = re.compile(
    r"(?:Bearer|Basic|Token|token)\s*|true|false|yes|no|on|off|\d{1,6}"
    r"|https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/-]*)?"
    r"|[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}|[A-Za-z][A-Za-z_-]{0,15}|"
)
GEHEIMNISARTIG_RE = re.compile(r"[A-Za-z0-9_+/=-]{24,}")


def _wert_harmlos(text: str) -> bool:
    """Nur ${NAME}-Platzhalter (mit harmlosem Standardwert) plus unkritische Literale."""
    for m in PLATZHALTER_RE.finditer(text):
        if m.group(2) and not HARMLOS_RE.fullmatch(m.group(2)):
            return False
    return bool(HARMLOS_RE.fullmatch(PLATZHALTER_RE.sub("", text)))


def _arg_geheimnisartig(arg: str) -> bool:
    teil = arg.split("=", 1)[-1] if arg.startswith("-") else arg
    return bool(GEHEIMNISARTIG_RE.fullmatch(teil)) and not teil.startswith(("/", "@")) \
        and any(c.isdigit() for c in teil) and any(c.isalpha() for c in teil)


def mcp_server_pruefen(server: object) -> str | None:
    """Grund, warum ein MCP-Eintrag nicht in den Auftrag darf, oder None. Werte in env und headers sowie die
    URL dürfen nur aus ${NAME}-Platzhaltern und unkritischen Literalen bestehen; Argumente dürfen nicht wie
    Schlüssel aussehen. So gelangt ein Token nie über die Konfiguration, nur über die Allowlist der Umgebung."""
    if not isinstance(server, dict):
        return "kein Objekt"
    for feld in ("env", "headers"):
        werte = server.get(feld) or {}
        if not isinstance(werte, dict):
            return f"{feld} ist kein Objekt"
        for key, value in werte.items():
            if not isinstance(value, str) or not _wert_harmlos(value):
                return f"{feld}.{key} enthält ein Literal, das ein Geheimnis sein könnte"
    if "url" in server and (not isinstance(server["url"], str) or not _wert_harmlos(server["url"])):
        return "url enthält Anmeldedaten oder unbekannte Teile"
    args = server.get("args") or []
    if not isinstance(args, list) or any(not isinstance(a, str) or _arg_geheimnisartig(a) for a in args):
        return "args enthält einen Wert, der wie ein Schlüssel aussieht"
    return None


# ---------------------------------------------------------------- Dateien und Sperren
def log(text: str, pflicht: bool = False) -> None:
    """Audit-Protokoll /srv/wamo/logs/auftraege.log. Mit pflicht=True (Start, Freigabe, Selbsttest, Abbruch)
    bricht ein Schreibfehler den Befehl ab, statt still weiterzulaufen."""
    client = (os.environ.get("SSH_CONNECTION") or "lokal").split(" ")[0]
    text = text.replace("\n", " ").replace("\r", " ")
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {client} {text}\n"
    try:
        with open(LOGS / "auftraege.log", "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        print(f"WAMO-Auftrag: Audit-Protokoll nicht schreibbar ({e.strerror})", file=sys.stderr)
        if pflicht:
            raise


def write_json_atomic(path: Path, data: object) -> None:
    """Zufälliger Name mit O_EXCL (folgt keinem gepflanzten Link), dann rename über das Ziel."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_fresh(path: Path, text: str) -> None:
    """Für Dateien in Ordnern, die die Sandbox beschreiben kann: vorhandenen Eintrag (auch einen Link)
    entfernen und neu anlegen, ohne je einem Link zu folgen."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def read_no_follow(path: Path, limit: int, dir_fd: int | None = None) -> str | None:
    """Liest höchstens limit Bytes einer regulären Datei, ohne einem Link zu folgen und ohne an einer FIFO
    zu hängen (O_NONBLOCK, danach fstat); None, wenn es nicht geht."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags, dir_fd=dir_fd) if dir_fd is not None else os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return None if len(data) > limit else data.decode("utf-8", "replace")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return default


@contextmanager
def exklusiv(pfad: Path):
    """Wartende Sperre (kurze Abschnitte wie das Eintragen einer Freigabe)."""
    import fcntl
    with open(pfad, "a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield


class Sperre:
    """flock auf einer Datei ohne Warten; gilt, solange das Objekt lebt (offene Datei)."""

    def __init__(self, pfad: Path):
        self.pfad = pfad
        self.datei = None

    def versuchen(self) -> bool:
        import fcntl
        self.datei = open(self.pfad, "a+")
        try:
            fcntl.flock(self.datei.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            self.datei.close()
            self.datei = None
            return False


def sperren_ordner() -> Path:
    path = JOBS / ".sperren"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def job_laeuft(job: str) -> bool:
    lock = JOBS / job / ".lauf"
    if not lock.exists():
        return False
    probe = Sperre(lock)
    if probe.versuchen():
        probe.datei.close()
        return False
    return True


def _no_symlink(path: Path) -> Path:
    if path.is_symlink():
        raise Abgewiesen(f"{path.name} ist ein symbolischer Link")
    return path


def job_ordner(job: str) -> Path:
    jobdir = _no_symlink(JOBS / job)
    jobdir.mkdir(mode=0o700, exist_ok=True)
    return jobdir


def ohne_geheimnisse(obj, geheim: dict[str, str]):
    """Ersetzt wörtlich eingetragene Geheimniswerte (aus geheim.env) durch ${NAME}."""
    if isinstance(obj, dict):
        return {k: ohne_geheimnisse(v, geheim) for k, v in obj.items()}
    if isinstance(obj, list):
        return [ohne_geheimnisse(v, geheim) for v in obj]
    if isinstance(obj, str):
        for name, wert in sorted(geheim.items(), key=lambda kv: -len(kv[1])):
            if len(wert) >= 8 and wert in obj:
                obj = obj.replace(wert, "${" + name + "}")
    return obj


def npx_direkt(server: object) -> object:
    """MCP-Server der Form npx [-y] <paket>[@version] args... laufen direkt aus der schreibgeschützten
    globalen Installation von wamo (.npm-global), wenn das Paket dort in passender Version liegt:
    kein Download je Auftrag, kein beschreibbarer gemeinsamer Cache. Sonst bleibt der Eintrag, wie er ist."""
    if not isinstance(server, dict) or server.get("command") != "npx" or not isinstance(server.get("args"), list):
        return server
    args = [a for a in server["args"] if isinstance(a, str)]
    if len(args) != len(server["args"]):
        return server
    rest = args[1:] if args[:1] in (["-y"], ["--yes"]) else args
    if not rest or rest[0].startswith("-"):
        return server
    spec, rest = rest[0], rest[1:]
    name, version = spec, ""
    if "@" in spec[1:]:
        name, version = spec.rsplit("@", 1)
    if not re.fullmatch(r"(@[a-z0-9._-]+/)?[a-z0-9._-]+", name):
        return server
    meta = read_json(NPM_GLOBAL / "lib" / "node_modules" / name / "package.json", None)
    if not isinstance(meta, dict) or (version not in ("", "latest") and meta.get("version") != version):
        return server
    kurz = name.rsplit("/", 1)[-1]
    bins = meta.get("bin")
    programm = kurz if isinstance(bins, str) or (isinstance(bins, dict) and kurz in bins) else (
        next(iter(bins), None) if isinstance(bins, dict) and bins else None)
    if not programm or not (NPM_GLOBAL / "bin" / programm).exists():
        return server
    return {**server, "command": str(NPM_GLOBAL / "bin" / programm), "args": rest}


def claude_konfiguration(claude_dir: Path, geheim: dict[str, str]) -> None:
    """Globale Claude-Konfiguration des Auftrags (CLAUDE_CONFIG_DIR in der Sandbox), bei JEDEM Lauf neu aus
    der Konfiguration von wamo: nichts, was ein früherer Lauf in .claude.json oder settings.json eingetragen
    hat (Hooks, projektbezogene MCP-Server, Helferbefehle), überlebt. MCP-Server ohne Geheimniswerte und nur,
    wenn mcp_server_pruefen nichts findet. Der Ordner ist in der Sandbox beschreibbar: ohne Links schreiben."""
    host = read_json(HEIM / ".claude.json", {})
    host = host if isinstance(host, dict) else {}
    conf = {k: host[k] for k in CLAUDE_JSON_KEYS if k in host}
    servers = {}
    roh = host.get("mcpServers")
    for name, server in (roh.items() if isinstance(roh, dict) else ()):
        server = ohne_geheimnisse(npx_direkt(server), geheim)
        grund = mcp_server_pruefen(server)
        if grund:
            log(f"MCP-Server {str(name)[:40]} nicht übernommen: {grund}")
            continue
        servers[name] = server
    conf.update({"mcpServers": servers, "autoUpdates": False, "hasCompletedOnboarding": True})
    write_fresh(claude_dir / ".claude.json", json.dumps(conf, ensure_ascii=False))
    host_settings = read_json(HEIM / ".claude" / "settings.json", {})
    host_settings = host_settings if isinstance(host_settings, dict) else {}
    settings = {k: host_settings[k] for k in SETTINGS_KEYS if k in host_settings}
    write_fresh(claude_dir / "settings.json", json.dumps(ohne_geheimnisse(settings, geheim), ensure_ascii=False))
    (claude_dir / "settings.local.json").unlink(missing_ok=True)


def vorbereiten(jobdir: Path, job: str, projekt: str, art: str) -> None:
    """Legt den Auftragsordner an oder prüft die Bindung an Projekt und Art (unter der Auftragssperre)."""
    meta_path = jobdir / "auftrag.json"
    meta = read_json(meta_path, None)
    if meta is None:
        write_json_atomic(meta_path, {"job": job, "projekt": projekt, "art": art, "erstellt": int(time.time())})
    elif not isinstance(meta, dict) or meta.get("projekt") != projekt or meta.get("art") != art:
        raise Abgewiesen("Auftrag gehört zu einem anderen Projekt oder einer anderen Art")
    for sub in ("claude", "zustand", "etc"):
        _no_symlink(jobdir / sub).mkdir(mode=0o700, exist_ok=True)
    claude_konfiguration(jobdir / "claude", lese_geheimnisse())
    uid = os.getuid() if hasattr(os, "getuid") else 995
    gid = os.getgid() if hasattr(os, "getgid") else 982
    (jobdir / "etc" / "passwd").write_text(
        "root:x:0:0:root:/root:/usr/sbin/nologin\n"
        f"wamo:x:{uid}:{gid}:WAMO:{HEIM}:/bin/bash\n"
        "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n", encoding="utf-8")
    (jobdir / "etc" / "group").write_text(
        f"root:x:0:\nwamo:x:{gid}:\nnogroup:x:65534:\n", encoding="utf-8")
    projektdir = _no_symlink(PROJEKTE / projekt)
    projektdir.mkdir(mode=0o750, exist_ok=True)


def lauf_vorbereiten(jobdir: Path, projekt: str) -> None:
    """Je Lauf: Anfragen des letzten Laufs sichern, Freigaben als Schnappschuss bereitstellen, Projekt
    entschärfen."""
    zustand = jobdir / "zustand"
    alte = gepruefte_anfragen(jobdir, projekt)
    if alte:
        angefragt_anhaengen(jobdir, alte)
    with exklusiv(jobdir / ".freigaben"):
        neu = read_json(jobdir / "freigaben.json", [])
        (jobdir / "freigaben.json").unlink(missing_ok=True)
    neu = [fp for fp in neu if isinstance(fp, str) and FP_RE.fullmatch(fp)] if isinstance(neu, list) else []
    # zustand/ ist in der Sandbox beschreibbar: nichts darin wird gelesen oder geschrieben, ohne Links
    # auszuschließen. approvals.json hängt im Lauf schreibgeschützt über sich selbst.
    for name in ("pending.jsonl", "approvals.json.used", "approvals.json"):
        if (zustand / name).is_dir() and not (zustand / name).is_symlink():
            import shutil
            shutil.rmtree(zustand / name)  # rmtree folgt keinen Links; die Sandbox läuft gerade nicht
            log(f"zustand/{name} war ein Ordner und wurde ersetzt ({jobdir.name})")
    write_fresh(zustand / "pending.jsonl", "")
    write_fresh(zustand / "approvals.json.used", "")
    write_fresh(zustand / "approvals.json", json.dumps(neu))
    (jobdir / "abbruch").unlink(missing_ok=True)
    for entfernt in projekt_entschaerfen(PROJEKTE / projekt):
        log(f"entschärft {jobdir.name} {projekt}: {entfernt}")


def angefragt_anhaengen(jobdir: Path, eintraege: list[dict]) -> None:
    """angefragt.jsonl (außerhalb der Sandbox) wächst nicht unbegrenzt: über 1 MiB bleiben die jüngsten 500."""
    pfad = jobdir / "angefragt.jsonl"
    with open(pfad, "a", encoding="utf-8") as f:
        for req in eintraege:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")
    try:
        if pfad.stat().st_size > MAX_ANGEFRAGT_BYTES:
            zeilen = pfad.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
            write_fresh(pfad, "\n".join(zeilen) + "\n")
    except OSError:
        pass


def _kurz_genug(obj, tiefe: int = 0, zaehler: list | None = None) -> bool:
    """Alle Texte höchstens MAX_STRING_CHARS, höchstens 20 Ebenen und 2000 Knoten."""
    zaehler = zaehler if zaehler is not None else [0]
    zaehler[0] += 1
    if tiefe > 20 or zaehler[0] > 2000:
        return False
    if isinstance(obj, str):
        return len(obj) <= MAX_STRING_CHARS
    if isinstance(obj, dict):
        return all(_kurz_genug(k, tiefe + 1, zaehler) and _kurz_genug(v, tiefe + 1, zaehler) for k, v in obj.items())
    if isinstance(obj, list):
        return all(_kurz_genug(v, tiefe + 1, zaehler) for v in obj)
    return True


def mit_zeitlimit(funktion, sekunden: float):
    """Führt funktion() mit hartem Zeitlimit aus (SIGALRM, nur im Hauptthread unter Linux). Wirft
    _Zeitueberschreitung. Schutz gegen langsame Muster im Wächter bei gepflanzten Anfragen."""
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        return funktion()

    def abbrechen(*_):
        raise _Zeitueberschreitung()
    alt = signal.signal(signal.SIGALRM, abbrechen)
    signal.setitimer(signal.ITIMER_REAL, sekunden)
    try:
        return funktion()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, alt)


def gepruefte_anfragen(jobdir: Path, projekt: str) -> list[dict]:
    """Zeilen aus zustand/pending.jsonl (in der Sandbox beschreibbar), neu geprüft: Fingerabdruck und Grund
    kommen vom Wächter. Begrenzt in Zeilen, Länge, Verschachtelung und Rechenzeit je Zeile; Werkzeugnamen
    WAMO-* (eigene Anfragen des Dispatchers) kann die Sandbox nicht anfragen."""
    text = read_no_follow(jobdir / "zustand" / "pending.jsonl", MAX_PENDING_BYTES)
    if text is None:
        return []
    g = waechter()
    out, seen = [], set()
    for line in text.splitlines()[:MAX_PENDING_LINES]:
        if len(line.encode("utf-8", "replace")) > MAX_PENDING_LINE_BYTES:
            continue
        try:
            req = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(req, dict) or not isinstance(req.get("tool"), str) or not isinstance(req.get("input"), dict):
            continue
        tool, tool_input = req["tool"], req["input"]
        if len(tool) > 200 or tool.startswith("WAMO-") or not _kurz_genug(tool_input):
            continue
        try:
            decision, why = mit_zeitlimit(lambda: g.decide(tool, tool_input, PROJEKTE / projekt), DECIDE_TIMEOUT_S)
        except (Exception, _Zeitueberschreitung):  # unprüfbar oder zu langsam heißt ungültig
            continue
        fp = g.fingerprint(tool, tool_input)
        if decision != "approval" or req.get("id") != fp or fp in seen:
            continue
        seen.add(fp)
        out.append({"id": fp, "grund": why, "tool": tool, "input": tool_input})
        if len(out) >= MAX_OFFENE_FREIGABEN:
            break
    return out


# ---------------------------------------------------------------- Projekt entschärfen (H3)
GIT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "LANG": "C"}
GIT_ERLAUBT = [re.compile(p) for p in (
    r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks|autocrlf|eol)",
    r"extensions\.objectformat", r"remote\..+\.(url|fetch)", r"branch\..+\.(remote|merge|rebase)",
    r"user\.(name|email)", r"pull\.(rebase|ff)", r"push\.(default|autosetupremote)", r"init\.defaultbranch",
)]
GIT_URL_RE = re.compile(r"https://[A-Za-z0-9.-]+(:\d{1,5})?/[A-Za-z0-9._~/%-]*")


def git_config_filtern(eintraege: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Nur unkritische Schlüssel; Remote-URLs nur https ohne Anmeldedaten. Alles, was Befehle startet oder
    umlenkt (fsmonitor, hooksPath, sshCommand, pager, filter, textconv, alias, insteadOf, include, http,
    credential, uploadpack, receivepack, worktreeconfig), fällt weg."""
    out = []
    for key, value in eintraege:
        teile = key.split(".")
        norm = ".".join([teile[0].lower(), *teile[1:-1], teile[-1].lower()]) if len(teile) >= 2 else key.lower()
        if not any(p.fullmatch(norm) for p in GIT_ERLAUBT) or "\n" in value:
            continue
        if norm.startswith("remote.") and norm.endswith(".url") and not GIT_URL_RE.fullmatch(value):
            continue
        out.append((key, value))
    return out


def _git(*args: str, eingabe: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], input=eingabe, capture_output=True, env=GIT_ENV, timeout=20, cwd="/")


def git_config_lesen(text: str) -> list[tuple[str, str]] | None:
    """Liest eine Git-Konfiguration ohne Includes (git config --file ... --list -z); None, wenn unlesbar."""
    with tempfile.TemporaryDirectory() as tmp:
        pfad = Path(tmp) / "config"
        pfad.write_text(text, encoding="utf-8")
        p = _git("config", "--file", str(pfad), "--no-includes", "--list", "-z")
    if p.returncode != 0:
        return None
    out = []
    for eintrag in p.stdout.decode("utf-8", "replace").split("\0"):
        if eintrag:
            key, _, value = eintrag.partition("\n")
            out.append((key, value if "\n" in eintrag else "true"))
    return out


def git_config_schreiben(eintraege: list[tuple[str, str]]) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        pfad = Path(tmp) / "config"
        pfad.write_text("", encoding="utf-8")
        for key, value in eintraege:
            _git("config", "--file", str(pfad), "--add", key, value).check_returncode()
        return pfad.read_text(encoding="utf-8")


def _verzeichnis_at(dir_fd: int, name: str) -> int | None:
    """Öffnet name in dir_fd als Verzeichnis, ohne einem Link zu folgen. Ein Link an dieser Stelle wird
    entfernt (nie verfolgt); fehlt der Eintrag oder ist er keine Verzeichnis, None."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        os.unlink(name, dir_fd=dir_fd)
        return None
    if not stat.S_ISDIR(st.st_mode):
        return None
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def _entfernen_at(dir_fd: int, name: str) -> bool:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISDIR(st.st_mode):
        return False
    os.unlink(name, dir_fd=dir_fd)
    return True


def _git_ordner_entschaerfen(git_fd: int, praefix: str, entfernt: list[str]) -> None:
    for name in ("config.worktree",):
        if _entfernen_at(git_fd, name):
            entfernt.append(f"{praefix}/{name}")
    hooks_fd = _verzeichnis_at(git_fd, "hooks")
    if hooks_fd is not None:
        try:
            for name in os.listdir(hooks_fd):
                if not name.endswith(".sample") and _entfernen_at(hooks_fd, name):
                    entfernt.append(f"{praefix}/hooks/{name}")
        finally:
            os.close(hooks_fd)
    text = read_no_follow(Path("config"), 1024 * 1024, dir_fd=git_fd)
    if text is None:
        return
    eintraege = git_config_lesen(text)
    gefiltert = git_config_filtern(eintraege) if eintraege is not None else [
        ("core.repositoryformatversion", "0"), ("core.bare", "false")]
    if eintraege is not None and gefiltert == eintraege:
        return
    neu = git_config_schreiben(gefiltert)
    tmp = ".config.wamo.tmp"
    _entfernen_at(git_fd, tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=git_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(neu)
    os.replace(tmp, "config", src_dir_fd=git_fd, dst_dir_fd=git_fd)
    weg = sorted({k for k, _ in (eintraege or [])} - {k for k, _ in gefiltert})
    entfernt.append(f"{praefix}/config bereinigt ({', '.join(weg)[:200] or 'unlesbar'})")


def projekt_entschaerfen(projektdir: Path) -> list[str]:
    """Entfernt Autostart-Flächen, die ein früherer Lauf (mit anderen Token) im Projekt gepflanzt haben
    könnte: .mcp.json, .claude/settings*.json, Git-Hooks und config.worktree in .git und .git/modules/*,
    und setzt deren config auf eine Allowlist zurück. Läuft außerhalb der Sandbox auf einem Ordner, den die
    Sandbox beschreiben kann: alles über Verzeichnis-Deskriptoren, kein Link wird verfolgt."""
    entfernt: list[str] = []
    try:
        projekt_fd = os.open(projektdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return entfernt
    try:
        if _entfernen_at(projekt_fd, ".mcp.json"):
            entfernt.append(".mcp.json")
        claude_fd = _verzeichnis_at(projekt_fd, ".claude")
        if claude_fd is not None:
            try:
                for name in os.listdir(claude_fd):
                    if re.fullmatch(r"settings.*\.json", name) and _entfernen_at(claude_fd, name):
                        entfernt.append(f".claude/{name}")
            finally:
                os.close(claude_fd)
        git_fd = _verzeichnis_at(projekt_fd, ".git")
        if git_fd is not None:
            try:
                _git_ordner_entschaerfen(git_fd, ".git", entfernt)
                module_fd = _verzeichnis_at(git_fd, "modules")
                if module_fd is not None:
                    try:
                        for name in os.listdir(module_fd)[:200]:
                            sub_fd = _verzeichnis_at(module_fd, name)
                            if sub_fd is not None:
                                try:
                                    _git_ordner_entschaerfen(sub_fd, f".git/modules/{name}", entfernt)
                                finally:
                                    os.close(sub_fd)
                    finally:
                        os.close(module_fd)
            finally:
                os.close(git_fd)
    finally:
        os.close(projekt_fd)
    return entfernt


def zugang_pruefen(projekt: str, namen: set[str], freigaben: list[str]) -> dict | None:
    """Buchführung je Projekt (außerhalb der Sandbox): Schnittmenge der Token-Namen aller bisherigen Läufe.
    Ein Lauf, der ein Token außerhalb dieser Schnittmenge bekäme, könnte Autostart-Flächen ausführen, die ein
    früherer Lauf ohne dieses Token gepflanzt hat. Er startet deshalb nur, wenn die passende Freigabe in
    diesem Lauf vorliegt (dann gilt das Projekt mit diesen Zugängen als geprüft) oder der Projektordner leer
    ist. Liefert die Freigabe-Anfrage, wenn der Lauf warten muss, sonst None."""
    ordner = JOBS / ".projekte"
    ordner.mkdir(mode=0o700, exist_ok=True)
    pfad = ordner / f"{projekt}.json"
    eintrag = read_json(pfad, None)
    try:
        leer = not any(os.scandir(PROJEKTE / projekt))
    except OSError:
        leer = True
    bisher = set(eintrag["schnitt"]) if isinstance(eintrag, dict) and isinstance(eintrag.get("schnitt"), list) else None
    if leer:
        write_json_atomic(pfad, {"schnitt": sorted(namen)})
        return None
    if bisher is None:  # Projekt mit Inhalt, aber ohne Buchführung (älter, von Hand angelegt): erst prüfen lassen
        bisher = set()
    neu = namen - bisher
    if neu:
        anfrage_input = {"projekt": projekt, "neu": sorted(neu), "bisher": sorted(bisher)}
        fp = waechter().fingerprint(ZUGANG_TOOL, anfrage_input)
        if fp not in freigaben:
            return {"id": fp, "grund": "Mehr Zugänge als frühere Aufträge in diesem Projekt: " + ", ".join(sorted(neu)),
                    "tool": ZUGANG_TOOL, "input": anfrage_input}
        write_json_atomic(pfad, {"schnitt": sorted(namen)})  # geprüft und freigegeben
        return None
    write_json_atomic(pfad, {"schnitt": sorted(bisher & namen)})
    return None


# ---------------------------------------------------------------- Ausgabe
_ausgabe_lock = threading.Lock()


def emit(event: dict) -> bool:
    """Eine JSON-Zeile auf stdout; False, wenn der Empfänger weg ist."""
    return emit_raw((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))


def emit_raw(line: bytes) -> bool:
    with _ausgabe_lock:
        try:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False


def wamo_event(ereignis: str, **extra) -> bool:
    return emit({"type": "wamo", "ereignis": ereignis, **extra})


def ist_wamo_zeile(raw: bytes) -> bool:
    """Gibt sich eine Zeile aus der Sandbox als eigenes Ereignis aus? (Der Agent kann auf fd 1 von Claude
    Code schreiben.) Solche Zeilen werden verworfen."""
    # Ohne Vorfilter auf Bytes: JSON erlaubt Escapes (wamo) und beliebige Schreibweisen
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        return False
    return isinstance(obj, dict) and str(obj.get("type", "")).strip().lower() == "wamo"


def zeilen(stream, limit: int):
    """Zeilen höchstens limit Bytes; längere werden bis zum Zeilenende verworfen (None als Marker)."""
    while True:
        line = stream.readline(limit)
        if not line:
            return
        if len(line) >= limit and not line.endswith(b"\n"):
            while True:
                rest = stream.readline(limit)
                if not rest or rest.endswith(b"\n"):
                    break
            yield None
            continue
        yield line


class Freigabezaehler:
    """Zählt im stream-json, wie oft freigegebene Aufrufe wirklich ausgeführt wurden (tool_use mit einem
    tool_result, das keine Hook-Sperre meldet). Öfter als freigegeben heißt: approvals.json.used wurde in der
    Sandbox zurückgesetzt oder parallele Hooks haben dieselbe Freigabe doppelt durchgelassen; der Lauf endet."""

    def __init__(self, freigaben: object):
        self.erlaubt: dict[str, int] = {}
        for fp in freigaben if isinstance(freigaben, list) else []:
            if isinstance(fp, str) and FP_RE.fullmatch(fp):
                self.erlaubt[fp] = self.erlaubt.get(fp, 0) + 1
        self.offen: dict[str, str] = {}
        self.genutzt: dict[str, int] = {}

    def sehen(self, raw: bytes) -> bool:
        """True, sobald eine Freigabe öfter genutzt wurde als erteilt."""
        if not self.erlaubt or (b'"tool_use"' not in raw and b'"tool_result"' not in raw):
            return False
        try:
            ev = json.loads(raw)
        except (ValueError, RecursionError):
            return False
        bloecke = ((ev.get("message") or {}).get("content") if isinstance(ev, dict) else None) or []
        if not isinstance(bloecke, list):
            return False
        g = waechter()
        for b in bloecke:
            if not isinstance(b, dict):
                continue
            if ev.get("type") == "assistant" and b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
                fp = g.fingerprint(str(b.get("name", "")), b["input"])
                if fp in self.erlaubt:
                    self.offen[str(b.get("id"))] = fp
            elif ev.get("type") == "user" and b.get("type") == "tool_result":
                fp = self.offen.pop(str(b.get("tool_use_id")), None)
                if fp is None:
                    continue
                inhalt = b.get("content")
                text = inhalt if isinstance(inhalt, str) else " ".join(
                    str(t.get("text", "")) for t in inhalt if isinstance(t, dict)) if isinstance(inhalt, list) else ""
                if g.tool_ran({"tool_response": text}):
                    self.genutzt[fp] = self.genutzt.get(fp, 0) + 1
                    if self.genutzt[fp] > self.erlaubt[fp]:
                        return True
        return False


# ---------------------------------------------------------------- Befehle
def cmd_ping() -> int:
    emit({"ok": True, "version": VERSION, "runner": RUNNER.is_file(), "waechter": (WAECHTER / "dev_guard.py").is_file()})
    return EXIT_OK


def cmd_status(job: str) -> int:
    status = read_json(JOBS / job / "status.json", None)
    if not isinstance(status, dict):
        emit({"job": job, "bekannt": False})
        return EXIT_OK
    status["laeuft"] = job_laeuft(job)
    emit(status)
    return EXIT_OK


def cmd_pending(job: str) -> int:
    meta = read_json(JOBS / job / "auftrag.json", None)
    if not isinstance(meta, dict) or not isinstance(meta.get("projekt"), str):
        return EXIT_OK
    for req in gepruefte_anfragen(JOBS / job, check_project(meta["projekt"])):
        emit(req)
    return EXIT_OK


def cmd_approve(job: str, fp: str) -> int:
    jobdir = JOBS / job
    meta = read_json(jobdir / "auftrag.json", None)
    if not isinstance(meta, dict):
        raise Abgewiesen("Auftrag unbekannt")
    projekt = check_project(str(meta.get("projekt", "")))
    bekannt = {r["id"] for r in gepruefte_anfragen(jobdir, projekt)}
    text = read_no_follow(jobdir / "angefragt.jsonl", MAX_ANGEFRAGT_BYTES * 2) or ""
    for line in text.splitlines():
        try:
            eintrag = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(eintrag, dict):
            bekannt.add(eintrag.get("id"))
    if fp not in bekannt:
        raise Abgewiesen("Fingerabdruck wurde nie angefragt")
    with exklusiv(jobdir / ".freigaben"):
        neu = read_json(jobdir / "freigaben.json", [])
        write_json_atomic(jobdir / "freigaben.json", [*(neu if isinstance(neu, list) else []), fp])
    log(f"approve {job} {fp}", pflicht=True)
    emit({"ok": True})
    return EXIT_OK


def stop_units(job: str) -> None:
    """Beendet alle systemd-Einheiten des Auftrags (Sandbox samt Kindprozessen)."""
    try:
        subprocess.run(["/usr/bin/systemctl", "--user", "stop", f"wamo-job-{job}-*.scope"],
                       env=basis_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def cmd_cancel(job: str) -> int:
    jobdir = JOBS / job
    if jobdir.is_dir() and not jobdir.is_symlink():
        (jobdir / "abbruch").write_text(str(int(time.time())), encoding="utf-8")
    stop_units(job)
    log(f"cancel {job}", pflicht=True)
    emit({"ok": True})
    return EXIT_OK


NETZPROBE = r"""
import errno, socket, sys
def ziel(host, port, proto=0, timeout=4):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, proto)
    except PermissionError: return "zu"
    except OSError as e: return "fehler-" + errno.errorcode.get(e.errno, str(e.errno))
    s.settimeout(timeout)
    try:
        s.connect((host, port)); return "offen"
    except PermissionError: return "zu"
    except OSError as e:
        if e.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH): return "zu"
        if isinstance(e, socket.timeout): return "zeit"
        return "fehler-" + errno.errorcode.get(e.errno, str(e.errno))
    except Exception as e: return "fehler-" + type(e).__name__
    finally: s.close()
probe, bruecke, lan = int(sys.argv[1]), sys.argv[2], sys.argv[3]
werte = [("landlock", ziel("127.0.0.1", probe)), ("lo9", ziel("127.0.0.1", 9)), ("lo9mptcp", ziel("127.0.0.1", 9, 262)),
         ("netz10", ziel("10.255.255.254", 9)), ("netz10mptcp", ziel("10.255.255.254", 9, 262)),
         ("bruecke", ziel(bruecke, 9) if bruecke != "-" else "keine"), ("lan", ziel(lan, 443)),
         ("github", ziel("github.com", 443, timeout=10))]
print("wamo-netz " + " ".join("%s=%s" % w for w in werte), file=sys.stderr)
"""
NETZ_ERWARTET = {"landlock": "zu", "lo9": "zu", "lo9mptcp": "zu", "netz10": "zu", "netz10mptcp": "zu",
                 "bruecke": "zu", "lan": "zu", "github": "offen"}


def probe_lauscher() -> socket.socket:
    """Lauschender Socket auf 127.0.0.1 im Bereich ENTWICKLUNGS_PORTS (zufällig, erster freier)."""
    import random
    ports = list(ENTWICKLUNGS_PORTS)
    random.shuffle(ports)
    for port in ports[:50]:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)
            return s
        except OSError:
            s.close()
    raise OSError("kein freier Port für die Landlock-Probe")


def docker_bruecke() -> str | None:
    """Adresse einer Docker-Bridge des Hosts (docker0 oder br-*), Ziel der Firewall-Probe."""
    try:
        p = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=10,
                           env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in p.stdout.splitlines():
        teile = line.split()
        if len(teile) >= 4 and (teile[1] == "docker0" or teile[1].startswith("br-")):
            return teile[3].split("/")[0]
    return None


def selbsttest(job: str, projekt: str) -> tuple[bool, str]:
    """Führt den Hook-Befehl aus managed-settings.json in der Sandbox mit einem Sperrfall aus und prüft
    nebenbei die Grenzen. Bestanden nur, wenn der Wächter wirklich lief und genau mit der erwarteten
    Begründung ablehnt (ein Absturz mit Exit 2 sperrt im Betrieb auch, zählt hier aber nicht)."""
    try:
        hook = json.loads(MANAGED.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return False, "managed-settings.json unlesbar"
    pp = f"{PROJEKTE}/{projekt}"
    pruefungen = [
        ("[ -e /etc/wamo ]", "/etc/wamo sichtbar"),
        ("[ -e /srv/wamo/.ssh ]", ".ssh sichtbar"),
        ("[ -e /usr/local/lib/wamo/bwrap ]", "bwrap-Kopie sichtbar"),
        ("[ -e /etc/docker ]", "/etc nicht auf die Allowlist begrenzt"),
        ("! [ -r /etc/claude-code/managed-settings.json ]", "managed-settings fehlt"),
        ("[ -w /run/wamo/zustand/approvals.json ]", "approvals beschreibbar"),
        ("umbenennen /opt/wamo /opt/wamo-x", "Wächterordner umbenennbar"),
        ("umbenennen /opt/wamo/waechter /opt/wamo/x", "Wächter umbenennbar"),
        ("mkdir /opt/wamo/neu 2>/dev/null", "Wächterordner beschreibbar"),
        ("touch /opt/wamo/waechter/x 2>/dev/null", "Wächter beschreibbar"),
        ("umbenennen /srv/wamo/projekte /srv/wamo/projekte-x", "Projekte-Ordner umbenennbar"),
        (f"umbenennen {pp} {pp}-x", "Projektordner umbenennbar"),
        ("mkdir /srv/wamo/projekte/neu 2>/dev/null", "Projekte-Ordner beschreibbar"),
        ("ln -sfn / /run/wamo/projekt 2>/dev/null", "Projektverweis ersetzbar"),
        (f"[ \"$(readlink -f /run/wamo/projekt)\" != {pp} ]", "Projektverweis zeigt woanders hin"),
    ]
    # Nur rename(2), nie mv: mv kopiert bei EXDEV und löscht danach die Quelle, im Projektordner also dessen Inhalt
    umbenennen = "umbenennen() { /usr/bin/python3 -I -c 'import os,sys; os.rename(sys.argv[1], sys.argv[2])' \"$1\" \"$2\" 2>/dev/null; }"
    teile = ["f=0", umbenennen] + [f"if {bed}; then echo 'wamo-pruefung: {text}' >&2; f=1; fi" for bed, text in pruefungen]
    # Probe-Lauscher im Bereich, den der Ausgangsfilter auf Loopback zulässt: nur Landlock darf ihn sperren
    probe = probe_lauscher()
    port = probe.getsockname()[1]
    bruecke = docker_bruecke() or "-"
    teile += [f"/usr/bin/python3 -I -c '{NETZPROBE}' {port} {bruecke} {LAN_PROBE}",
              f"( {hook} ); c=$?; echo \"wamo-selbsttest code=$c fehler=$f\" >&2"]
    argv = [str(RUNNER), "--auftrag", job, "--projekt", projekt, "--zeitlimit", "60",
            "--einheit-zusatz", "pruefung", "--", "/bin/sh", "-c", "; ".join(teile)]
    try:
        p = subprocess.run(argv, input=json.dumps(SELFTEST_EVENT).encode(), capture_output=True,
                           env=basis_env(), timeout=120, cwd="/")
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"Sandbox nicht startbar ({type(e).__name__})"
    finally:
        probe.close()
    err = p.stderr.decode("utf-8", "replace")[-20000:]
    m = re.search(r"wamo-selbsttest code=(\d+) fehler=(\d)", err)
    if not m:
        return False, "Sandbox oder Hook lief nicht: " + err.strip()[-300:]
    code, fehler = int(m.group(1)), m.group(2) == "1"
    if fehler:
        return False, "Grenzen verletzt: " + " ".join(re.findall(r"wamo-pruefung: [^\n]+", err))
    netz = re.search(r"wamo-netz (.+)", err)
    if not netz:
        return False, "Netzprobe lief nicht"
    werte = dict(w.split("=", 1) for w in netz.group(1).split() if "=" in w)
    erwartet = {**NETZ_ERWARTET, **({"bruecke": "keine"} if bruecke == "-" else {})}
    abweichend = {k: werte.get(k, "?") for k, v in erwartet.items() if werte.get(k) != v}
    if abweichend:
        return False, "Netzgrenzen nicht wie erwartet: " + ", ".join(f"{k}={v}" for k, v in abweichend.items())
    try:
        antwort = json.loads(p.stdout.decode("utf-8", "replace"))["hookSpecificOutput"]
        decision, grund = antwort["permissionDecision"], str(antwort.get("permissionDecisionReason", ""))
    except (ValueError, KeyError, TypeError, RecursionError):
        decision, grund = None, ""
    if code == 0 and decision == "deny" and grund.startswith(SELFTEST_GRUND):
        return True, "Hook lehnt ab, Grenzen und Netz geprüft"
    return False, f"Hook lieferte nicht die erwartete Ablehnung (Exit {code}, Entscheidung {decision})"


def _kopfzeile_lesen() -> bytes:
    def zu_spaet(*_):
        raise Abgewiesen("Kopfzeile kam nicht rechtzeitig")
    signal.signal(signal.SIGALRM, zu_spaet)
    signal.alarm(30)
    try:
        return sys.stdin.buffer.readline(MAX_HEADER_BYTES + 1)
    finally:
        signal.alarm(0)


def cmd_start(job: str, projekt: str, art: str, session: str | None = None) -> int:
    auftrag, system = parse_header(_kopfzeile_lesen())
    sperren = sperren_ordner()
    jobdir = job_ordner(job)
    # Eine Sperre gilt, solange ihr Objekt lebt (offene Datei): deshalb alle bis zum Ende festhalten.
    gehalten = [Sperre(jobdir / ".lauf")]
    if not gehalten[0].versuchen():
        wamo_event("belegt", grund="Dieser Auftrag läuft bereits")
        return EXIT_BELEGT
    vorbereiten(jobdir, job, projekt, art)
    gehalten.append(Sperre(sperren / f"projekt-{projekt}"))
    if not gehalten[-1].versuchen():
        wamo_event("belegt", grund="Im Projekt läuft schon ein anderer Auftrag")
        return EXIT_BELEGT
    platz = next((s for s in (Sperre(sperren / f"platz-{i}") for i in range(PARALLEL_MAX)) if s.versuchen()), None)
    if platz is None:
        wamo_event("belegt", grund="Es läuft schon ein Auftrag" if PARALLEL_MAX == 1 else f"Es laufen schon {PARALLEL_MAX} Aufträge")
        return EXIT_BELEGT
    gehalten.append(platz)
    sessions = read_json(jobdir / "sessions.json", [])
    if session is not None and (not isinstance(sessions, list) or session not in sessions):
        raise Abgewiesen("Session gehört nicht zu diesem Auftrag")
    lauf_vorbereiten(jobdir, projekt)

    ok, grund = selbsttest(job, projekt)
    log(f"selbsttest {job} {'ok' if ok else 'FEHLER'}: {grund[:300]}", pflicht=True)
    if not ok:
        wamo_event("selbsttest_fehlgeschlagen", grund=grund[:300])
        _status(jobdir, job, projekt, art, ergebnis="selbsttest_fehlgeschlagen", code=EXIT_SELBSTTEST)
        return EXIT_SELBSTTEST
    wamo_event("selbsttest_ok")

    geheim = lese_geheimnisse()
    env = sandbox_env(art, geheim)
    if env is None:
        wamo_event("keine_zugangsdaten", grund="ANTHROPIC_API_KEY oder CLAUDE_CODE_OAUTH_TOKEN in /etc/wamo/geheim.env fehlt")
        _status(jobdir, job, projekt, art, ergebnis="keine_zugangsdaten", code=EXIT_KEIN_ZUGANG)
        return EXIT_KEIN_ZUGANG
    zugaenge = sorted(zugangs_quellen(art, geheim))
    snapshot = read_json(jobdir / "zustand" / "approvals.json", [])
    anfrage = zugang_pruefen(projekt, set(zugaenge), snapshot if isinstance(snapshot, list) else [])
    if anfrage is not None:
        angefragt_anhaengen(jobdir, [anfrage])
        log(f"zugang_erweitert {job} {projekt}: {', '.join(anfrage['input']['neu'])}", pflicht=True)
        wamo_event("zugang_erweitert", **anfrage)
        _status(jobdir, job, projekt, art, ergebnis="zugang_erweitert", code=EXIT_ZUGANG_ERWEITERT)
        return EXIT_ZUGANG_ERWEITERT
    argv = [str(RUNNER), "--auftrag", job, "--projekt", projekt, "--", str(CLAUDE), "-p",
            "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
            "--settings", PLUGIN_SETTINGS, "--append-system-prompt", system]
    if session:
        argv += ["--resume", session]
    log(f"start {job} {projekt} {art} {'fortsetzung' if session else 'neu'} zugaenge={','.join(zugaenge)}", pflicht=True)
    code = _ausfuehren(argv, env, auftrag, jobdir, job, projekt, art)
    del gehalten  # erst jetzt frei (Prozessende gibt sie ohnehin frei)
    return code


def _status(jobdir: Path, job: str, projekt: str, art: str, **fields) -> None:
    status = read_json(jobdir / "status.json", {})
    status = status if isinstance(status, dict) else {}
    status.update({"job": job, "projekt": projekt, "art": art, **fields})
    write_json_atomic(jobdir / "status.json", status)


def _ausfuehren(argv: list[str], env: dict, auftrag: str, jobdir: Path, job: str, projekt: str, art: str) -> int:
    start = time.time()
    _status(jobdir, job, projekt, art, start=int(start), ende=None, ergebnis="läuft", code=None)
    log_pfad = jobdir / "lauf.log"
    if log_pfad.is_file() and not log_pfad.is_symlink():
        os.replace(log_pfad, jobdir / "lauf.log.1")  # je Lauf ein Protokoll, das vorige bleibt einmal erhalten
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd="/", start_new_session=True)
    gruende: list[str] = []
    gestoppt = threading.Event()

    def stoppen(grund: str) -> None:
        if gestoppt.is_set():
            return
        gestoppt.set()
        gruende.append(grund)
        stop_units(job)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue

    def lebenslinie() -> None:
        # stdin der SSH-Verbindung: EOF heißt, der Voice-Core ist weg (Auflegen, Abbruch, Netz)
        try:
            while sys.stdin.buffer.read1(65536):
                pass
        except (OSError, ValueError):
            pass
        if proc.poll() is None:
            stoppen("verlassen")

    def stderr_sichern() -> None:
        written = 0
        with open(log_pfad, "ab") as f:
            for raw in zeilen(proc.stderr, MAX_ZEILE_STDERR):
                if raw is not None and written < MAX_LOG_BYTES:
                    f.write(raw)
                    f.flush()
                    written += len(raw)

    threading.Thread(target=stderr_sichern, daemon=True).start()
    threading.Thread(target=lebenslinie, daemon=True).start()
    try:
        proc.stdin.write(auftrag.encode("utf-8"))
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    wamo_event("gestartet")
    sessions = read_json(jobdir / "sessions.json", [])
    sessions = sessions if isinstance(sessions, list) else []
    zaehler = Freigabezaehler(read_json(jobdir / "zustand" / "approvals.json", []))
    verworfen = 0
    for raw in zeilen(proc.stdout, MAX_ZEILE_STDOUT):
        if raw is None or ist_wamo_zeile(raw):
            verworfen += 1
            continue
        if zaehler.sehen(raw):
            log(f"freigabe_mehrfach {job}: freigegebener Aufruf öfter ausgeführt als freigegeben", pflicht=True)
            stoppen("freigabe_mehrfach")
        if b'"session_id"' in raw:
            try:
                sid = json.loads(raw).get("session_id")
            except (ValueError, AttributeError, RecursionError):
                sid = None
            if isinstance(sid, str) and SESSION_RE.fullmatch(sid) and sid not in sessions:
                sessions.append(sid)
                write_json_atomic(jobdir / "sessions.json", sessions[-50:])
        if not emit_raw(raw if raw.endswith(b"\n") else raw + b"\n"):
            stoppen("verlassen")
            break
    rc = proc.wait()
    if (jobdir / "abbruch").exists():
        ergebnis, code = "abgebrochen", EXIT_ABGEBROCHEN
    elif gruende:
        ergebnis, code = gruende[0], EXIT_ABGEBROCHEN
    elif rc in (124, 137):
        ergebnis, code = "zeitlimit", EXIT_ZEITLIMIT
    elif rc == 0:
        ergebnis, code = "fertig", EXIT_OK
    else:
        ergebnis, code = "fehler", rc if 0 < rc < 256 else EXIT_INTERN
    _status(jobdir, job, projekt, art, ende=int(time.time()), ergebnis=ergebnis, code=rc)
    log(f"ende {job} {ergebnis} rc={rc} dauer={int(time.time() - start)}s"
        + (f" verworfene_zeilen={verworfen}" if verworfen else ""))
    wamo_event("ende", ergebnis=ergebnis, code=rc)
    return code


HANDLER = {"ping": cmd_ping, "start": cmd_start, "status": cmd_status, "pending": cmd_pending,
           "approve": cmd_approve, "cancel": cmd_cancel}


def speicher_begrenzen() -> None:
    """Weiche Adressraumgrenze für diesen Prozess (Kinder setzen sie in wamo-job.sh wieder hoch)."""
    try:
        import resource
        _, hart = resource.getrlimit(resource.RLIMIT_AS)
        weich = SPEICHER_DISPATCHER if hart == resource.RLIM_INFINITY else min(SPEICHER_DISPATCHER, hart)
        resource.setrlimit(resource.RLIMIT_AS, (weich, hart))
    except (ImportError, ValueError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    # Über SSH zählt NUR SSH_ORIGINAL_COMMAND (argv ist durch command="..." fest leer). Ohne SSH (Rauchtest
    # als wamo) gelten die Argumente, mit derselben Prüfung.
    if "SSH_CONNECTION" in os.environ or "SSH_ORIGINAL_COMMAND" in os.environ:
        raw = os.environ.get("SSH_ORIGINAL_COMMAND")
    else:
        raw = " ".join(sys.argv[1:] if argv is None else argv)
    try:
        name, args = parse_command(raw)
        if name not in ("ping", "status", "pending"):
            log(f"{name} {' '.join(args)}")
        return HANDLER[name](*args)
    except Abgewiesen as e:
        log(f"ABGEWIESEN ({e}): {(raw or '')[:80]!r}")
        print(f"WAMO-Auftrag abgewiesen: {e}", file=sys.stderr)
        return EXIT_ABGEWIESEN
    except Exception as e:  # nichts durchreichen, aber protokollieren
        log(f"FEHLER {type(e).__name__}: {str(e)[:200]}")
        print(f"WAMO-Auftrag: interner Fehler ({type(e).__name__})", file=sys.stderr)
        return EXIT_INTERN


def beenden(code: int) -> None:
    """Hartes Ende nach dem Leeren der Puffer: der Lebenslinien-Thread hängt womöglich noch in einem
    blockierenden stdin-Read; ein normales Herunterfahren bräche dann mit SIGABRT ab (falscher Exit-Code)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(code)


if __name__ == "__main__":
    # SIGPIPE bleibt ignoriert (Python-Standard): ein Schreibfehler auf stdout soll den Auftrag erst
    # beenden (BrokenPipeError, dann stoppen), nicht den Dispatcher vorher stumm abbrechen.
    speicher_begrenzen()
    beenden(main())
