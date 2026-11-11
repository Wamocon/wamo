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
    pruefen <job>                               ein Befehl der Abnahme in der Sandbox des Auftrags (JSON-Zeile, standardmäßig AUS)

start: Die erste stdin-Zeile ist ein JSON-Objekt {"auftrag": "...", "system": "...", "modell": "..." (optional),
"repo": "owner/name" (optional, Ziel-Repo für das GitHub-Token), "zugang": "folgt" | "server" (optional),
"profil": "<8 Hexzeichen>" (optional, Kennung des Profils für die Buchführung)}. Mit "zugang": "folgt" kommt als
zweite Zeile der persönliche Claude-Zugang des Profils {"typ": "oauth" | "api", "wert": "...", "workspace": "wrkspc_..."
(nur api, optional)}; er gilt für genau diesen Lauf und ersetzt den Claude-Zugang aus geheim.env vollständig (kein
Mischen). "server" erlaubt ausdrücklich den Zugang aus geheim.env (Standard ohne das Feld, für ältere Kerne; mit
WAMO_SERVERZUGANG=aus in geheim.env abschaltbar). Der Wert steht nie in argv, Log, Auftragsordner, Kopfzeile, Ereignissen
oder Fehlertexten und wird nicht gespeichert; in der Ausgabe und in den Dateien unter claude/ wird er geschwärzt.
Danach bleibt stdin
offen und dient als Lebenslinie: endet sie (Auflegen, Verbindungsabbruch, Voice-Core beendet), wird der
Auftrag sofort beendet. stdout trägt die stream-json-Ausgabe von Claude Code und eigene Zeilen
{"type": "wamo", "ereignis": ...}; Zeilen dieses Typs aus der Sandbox werden verworfen. Das Ereignis "kontingent"
gibt die Limits des Max-Abos aus den rate_limit_event von Claude Code weiter (nur Felder einer Allowlist, kontingent.py).
Ablauf je Lauf:

    1. Auftragsordner /srv/wamo/jobs/<job> anlegen bzw. prüfen (Projekt und Art sind an den Auftrag gebunden).
    2. Sperren: ein Lauf je Auftrag, ein Auftrag je Projekt, höchstens PARALLEL_MAX gleichzeitig.
    3. Freigaben aus freigaben.json (außerhalb der Sandbox) als schreibgeschützte approvals.json für
       diesen Lauf bereitstellen; Anfragen des letzten Laufs nach angefragt.jsonl übernehmen.
       Projektordner entschärfen: .claude/settings*.json, .mcp.json, Git-Hooks weg, .git/config nur
       mit einer Allowlist (Autostart-Flächen, die ein früherer Lauf gepflanzt haben könnte).
    4. Selbsttest in der Sandbox: Wächter-Hook mit einem bekannten Sperrfall (genaue Ablehnung), /etc/wamo
       unsichtbar, approvals.json schreibgeschützt, Wächterpfade nicht umbenennbar, Landlock-Sperre auf
       einen eigenen Probe-Port, Ausgangsfirewall zu Docker-Bridge und LAN. Scheitert er, startet nichts.
    5. Token nur der Auftragsart aus /etc/wamo/geheim.env als Umgebung (nie argv) an wamo-job.sh, dazu genau ein
       Claude-Zugang: der persönliche aus der zweiten stdin-Zeile oder, wenn erlaubt, der aus geheim.env. Bekäme der
       Lauf ein Token, das ein früherer Lauf im selben Projekt nicht hatte (ein persönlicher Zugang zählt je Profil),
       startet er nur nach einer Freigabe (Ereignis zugang_erweitert) oder auf einem frischen, leeren Projektordner.
       Das Modell richtet sich nach der Zugangsart (modell_waehlen): Abo (oauth) Sonnet, sonst das der Art.
    6. GitHub im App-Modus (WAMO_GH_APP_ID, WAMO_GH_APP_INSTALLATION_ID, WAMO_GH_APP_SCHLUESSEL in geheim.env):
       der Dispatcher signiert außerhalb der Sandbox ein JWT (RS256) mit dem privaten Schlüssel der App, holt
       damit ein Installations-Token nur mit den Rechten der Auftragsart (Werkzeugkarte, GITHUB_ZUGRIFF) und,
       wenn die Kopfzeile ein Repo nennt, nur für dieses Repo. Es geht als GH_TOKEN in die Sandbox und wird am
       Laufende widerrufen (auch bei Abbruch, cancel, Fehler oder SIGTERM). Scheitert das, startet nichts
       (Ereignis start_abgelehnt, Code 8); ein Rückfall auf GH_TOKEN aus geheim.env gibt es dann nicht.
       Ohne diese drei Variablen gilt weiter GH_TOKEN bzw. GH_TOKEN_LESEN.

pruefen: Führt einen Befehl der Abnahme (befehl:-Kriterium) im Projekt des Auftrags aus, nach einem beendeten Lauf, in einer
engeren Sandbox als der Auftragslauf: kein Netz (--unshare-net), kein Token, kein Claude-Zugang, kein Zustand des Auftrags,
Umgebung nur die Basisumgebung plus Git-Hooks aus (wamo-job.sh --modus pruefen), Zeitlimit 120 s. Standardmäßig AUS: ohne
WAMO_PRUEFEN=an in geheim.env (jeder andere Wert, auch ein Tippfehler im Namen, sperrt) wird jeder Aufruf abgewiesen. Die erste
stdin-Zeile ist {"argv": [...]}: genau dieses Feld, höchstens 8 KiB. Der Dispatcher prüft argv erneut gegen die Allowlist
(abnahme_befehl.py, der Kern ist nicht vertrauenswürdig), dann mit dem Wächter (decide wie ein Bash-Aufruf), und nimmt das Projekt
nur aus auftrag.json. Sperren wie bei start (ein Lauf je Auftrag, ein Auftrag je Projekt, höchstens PARALLEL_MAX gleichzeitig),
höchstens 40 Aufrufe je Auftrag, vorher Projekt entschärfen und den Selbsttest im Modus pruefen (Netz zu). stdin bleibt als
Lebenslinie offen (EOF beendet den Lauf), cancel und das Zeitlimit beenden die Einheit. Antwort: eine JSON-Zeile
{"v":1,"ok","exit","abgebrochen","grund","dauer_s","ausgabe","gekuerzt"} (Ausgabe: das Ende, höchstens 2048 Byte, geschwärzt, nur
als JSON-String); ok nur bei einem echten int 0. Prozess-Exit 0: der Befehl lief (auch wenn er scheiterte), 2 abgewiesen oder
nicht möglich, 3 Selbsttest, 4 belegt, 6 intern, 124 Zeitlimit, 130 abgebrochen: alles außer 0 heißt nicht erfüllt. Die Programme
python3, node 22, npm, make, grep, eslint, git und uv sind in der Sandbox da; pytest, ruff, mypy, tsc, pnpm, yarn, go, cargo und
dotnet fehlen: sie scheitern mit "nicht gefunden", das Kriterium gilt als nicht erfüllt. Die uv-Form läuft nur mit einer .venv.
Fehlt die Prüflogik bei eingeschaltetem Schalter oder ist sie defekt, kommt trotzdem genau eine Zeile (Exit 6). Vor jedem Aufruf: Reste
der Einheiten des Auftrags beenden, Pfade im Aufruf über Links auflösen (müssen im Projekt bleiben), freier Platz (2 GiB) und Projektgröße
(2 GiB, 5 s) messen: eine Eintrittsprüfung, keine Quote (eine echte Plattenquote fehlt). Nach dem Lauf wird das Projekt erneut entschärft.
pruefen führt keine Buchführung der Zugänge (zugang_pruefen): der Lauf hat keine Zugänge. cancel wirkt jobweit (Datei abbruch, alle
Einheiten des Auftrags); der Kern ruft es nach dem Ende von pruefen nicht mehr. Protokollzeilen aus freiem Inhalt sind bereinigt.

Kanäle (Version 7): Jede ssh-Verbindung gehört zu einem Kanal. lokal (ohne SSH_CONNECTION und SSH_ORIGINAL_COMMAND: Betreiber,
Rauchtests, Notaus), pc (Schlüssel aus authorized_keys.d/wamo, Verhalten wie Version 6) oder kern (Schlüssel aus
authorized_keys.d/wamo-kern). Die Zuordnung hängt am benutzten Schlüssel (SSH_USER_AUTH, sshd ExposeAuthInfo), nie an der Adresse.
Existiert ein Kern-Schlüssel oder /etc/wamo/kernkanal.conf, gilt der strikte Modus: was sich nicht eindeutig zuordnen lässt,
endet mit dem Ereignis kanal_gesperrt (herkunft_unklar) und Exit 2, vor jedem Lesen von geheim.env. Im Kern-Kanal gelten Befehlsliste,
nur eigene Aufträge (auftrag.json mit kanal=kern), Namensraum kern-, nur zugang=folgt und die Grenzen aus kernkanal.conf (Wendungen und
Kosten je Lauf als Flags von Claude Code, Starts, Laufzeit, Folgen, Abstand, Platz); eine Notaus-Datei lässt nur ping und status zu.
Die Regeln stehen in wamo_job_kernkanal.py, hier nur die Einhängepunkte. Jeder Verstoß: {"type": "wamo", "ereignis":
"kanal_gesperrt", "grund": <Code>} und Exit 2. ping meldet kanal und kernkanal.

Nur Standardbibliothek, im App-Modus zusätzlich das Systempaket python3-cryptography. Wächter, Werkzeugkarte, Kontingent-Parser,
die Prüflogik von pruefen (abnahme_befehl.py, wamo_job_pruefen.py) und die Regeln des Kern-Kanals (wamo_job_kernkanal.py) kommen aus
/opt/wamo/waechter (Kopien aus voice-core und infra/sokrates2).
"""

from __future__ import annotations

import base64
import errno
import hashlib
import http.client
import json
import os
import re
import shlex
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections import namedtuple
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

VERSION = "7"

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
WAMO_REPO_GESPERRT = "Das WAMO-Repo selbst ist für Aufträge gesperrt"
KERN_PRAEFIX = "kern-"  # Namensraum der Projekte des Kern-Kanals; dem PC-Kanal verschlossen
KANAL = "lokal"  # Kanal der laufenden Verbindung (lokal, pc, kern; unklar bei unbekannter Herkunft): main setzt ihn, log schreibt ihn mit
KERN_PFADE = None  # nur für Tests: andere Pfade des Kern-Kanals (None: die echten, siehe wamo_job_kernkanal.PFADE)
KERN_LESER = None  # nur für Tests: anderer Dateizugriff (Eigentümer und Rechte)
KERN_UID = None  # nur für Tests: Benutzerkennung des Dispatchers

# Exit-Codes von "start" (zusätzlich zu den Ereignissen im Datenstrom)
EXIT_OK = 0
EXIT_ABGEWIESEN = 2
EXIT_SELBSTTEST = 3
EXIT_BELEGT = 4
EXIT_KEIN_ZUGANG = 5
EXIT_INTERN = 6
EXIT_ZUGANG_ERWEITERT = 7
EXIT_GITHUB_APP = 8  # App-Modus: Token nicht erzeugbar oder Konfiguration kaputt (fail-closed)
EXIT_ZEITLIMIT = 124
EXIT_ABGEBROCHEN = 130

COMMAND_RE = re.compile(r"[a-z]{2,10}(?: [A-Za-z0-9_-]{1,64}){0,4}")
JOB_RE = re.compile(r"[0-9a-f]{16,32}")
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,48}[a-z0-9]")
SESSION_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FP_RE = re.compile(r"[0-9a-f]{64}")  # voller SHA-256 aus dev_guard.fingerprint
ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
WORKSPACE_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")
MODELL_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,63}")
# Ziel-Repo owner/name: genau ein Schrägstrich, Owner wie bei GitHub (Buchstaben, Ziffern, Bindestrich, bis 39),
# Name bis 100 Zeichen aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich (dieselbe Regel in dev_agent.py)
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
# Persönlicher Claude-Zugang eines Profils (zweite stdin-Zeile von start). Das Format wird streng geprüft: oauth ist
# der Token aus `claude setup-token` (sk-ant-oat01-...), api ein Anthropic-API-Schlüssel (sk-ant-api03-...); Admin-
# Schlüssel (sk-ant-admin01-...) und Sitzungsschlüssel passen auf keins von beiden.
ZUGANG_MODI = ("folgt", "server")
ZUGANG_FORMAT = {"oauth": re.compile(r"sk-ant-oat[0-9]{2}-[A-Za-z0-9_-]{20,400}"),
                 "api": re.compile(r"sk-ant-api[0-9]{2}-[A-Za-z0-9_-]{20,250}")}
ZUGANG_VARIABLE = {"oauth": "CLAUDE_CODE_OAUTH_TOKEN", "api": "ANTHROPIC_API_KEY"}
ZUGANG_FELDER = {"typ", "wert", "workspace"}
PROFIL_RE = re.compile(r"[0-9a-f]{8}")
WORKSPACE_ZUGANG_RE = re.compile(r"wrkspc_[A-Za-z0-9]{6,60}")
MAX_ZUGANG_BYTES = 2048
ZUGANG_ERSATZ = b"[CLAUDE_ZUGANG]"
MAX_SCHWAERZ_DATEI = 64 * 1024 * 1024  # größere Dateien unter claude/ werden gelöscht statt gelesen
MAX_SCHWAERZ_DATEIEN = 5000
SCHWAERZ_ZEIT_S = 20.0  # Zeitbudget für das Schwärzen nach dem Lauf; danach gilt es als unvollständig
KONTINGENT_ABSTAND_S = 2.0  # höchstens alle 2 Sekunden ein Ereignis kontingent (die Zeilen kommen aus der Sandbox)
SK_ANT_RE = re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}")  # jeder Anthropic-Schlüssel in der Ausgabe, auch ein fremder
MAX_COMMAND_CHARS = 300
MAX_HEADER_BYTES = 256 * 1024
MAX_TEXT_CHARS = 20000
MAX_PENDING_BYTES = 2 * 1024 * 1024
MAX_PENDING_LINES = 200
MAX_PENDING_LINE_BYTES = 64 * 1024
MAX_STRING_CHARS = 8192
DECIDE_TIMEOUT_S = 1.0
GESAMT_PRUEF_BUDGET_S = 10.0  # N6: Gesamtzeit je gepruefte_anfragen-Aufruf
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)  # auf Nicht-Linux 0: die fd-Funktionen laufen nur auf dem Server
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
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
SETTINGS_KEYS = ("enabledPlugins",)  # Rest (Hooks, Helfer, statusLine, Marktplätze) nie übernehmen; den
# Marktplatz ecc deklariert allein managed-settings.json (lokale Kopie ohne Hooks, wamo-job-ecc.py)


class Abgewiesen(Exception):
    """Aufruf verletzt die Regeln; wird protokolliert und mit Code 2 beantwortet."""


class ZugangUngueltig(Abgewiesen):
    """Die Zugangszeile ist unbrauchbar. Der Text nennt nie den Inhalt der Zeile."""


# Kopfzeile von start; zugang: None (älterer Kern), "folgt" (zweite Zeile mit dem Zugang) oder "server"
Kopf = namedtuple("Kopf", ("auftrag", "system", "modell", "repo", "zugang", "profil"))


@dataclass(frozen=True, slots=True)
class ClaudeZugang:
    """Persönlicher Claude-Zugang für einen Lauf: typ oauth | api, wert (Geheimnis!), workspace (nur api), profil.
    Der Wert fehlt in der Darstellung (repr=False) und ist weder iterierbar noch in JSON umwandelbar: ein
    versehentliches Protokollieren zeigt ihn nicht."""

    typ: str
    wert: str = field(repr=False)
    workspace: str | None
    profil: str | None


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


def abnahme_modul():
    """Die Allowlist der Befehlskriterien (abnahme_befehl.py, Kopie aus voice-core in /opt/wamo/waechter)."""
    return _modul("abnahme_befehl")


def pruefen_modul():
    """Die Prüflogik von pruefen (wamo_job_pruefen.py): in /opt/wamo/waechter installiert, im Repo neben dieser Datei. Wird erst
    gebraucht, wenn der Schalter an ist; fehlt es oder ist es defekt, wirft das Laden (pruefen ist dann nicht verfügbar)."""
    abnahme_modul()  # das Modul importiert die Allowlist; der Pfad zu /opt/wamo/waechter steht danach in sys.path
    geladen = sys.modules.get("wamo_job_pruefen")
    if geladen is not None:
        return geladen
    if (WAECHTER / "wamo_job_pruefen.py").is_file():
        return _modul("wamo_job_pruefen")
    import importlib.util
    spec = importlib.util.spec_from_file_location("wamo_job_pruefen", Path(__file__).resolve().with_name("wamo_job_pruefen.py"))
    modul = importlib.util.module_from_spec(spec)
    sys.modules["wamo_job_pruefen"] = modul
    spec.loader.exec_module(modul)
    return modul


def modul_sha256(name: str) -> str | None:
    """Prüfsumme der Kopie eines Moduls in /opt/wamo/waechter (None, wenn es sie nicht gibt)."""
    try:
        return hashlib.sha256((WAECHTER / f"{name}.py").read_bytes()).hexdigest()
    except OSError:
        return None


def check_job(value: str) -> str:
    if not JOB_RE.fullmatch(value):
        raise Abgewiesen("Auftrags-ID ungültig")
    return value


def check_project(value: str) -> str:
    if not PROJECT_RE.fullmatch(value) or "--" in value:
        raise Abgewiesen("Projektname ungültig (3 bis 50 Zeichen, a-z, 0-9, Bindestrich)")
    if value == SELF_REPO:
        raise Abgewiesen(WAMO_REPO_GESPERRT)
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
    "pruefen": (1, (check_job,)),  # das Projekt steht nie im Aufruf: es kommt aus auftrag.json
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


def check_repo(value: object) -> str:
    """Ziel-Repo aus der Kopfzeile: owner/name, streng (REPO_RE, genau ein Schrägstrich, kein . oder .., kein
    .git am Ende). Es begrenzt nur das GitHub-Token; ob es zur Installation gehört, prüft die Antwort von GitHub."""
    if not isinstance(value, str) or len(value) > 140 or not REPO_RE.fullmatch(value) or value.count("/") != 1:
        raise Abgewiesen("Kopfzeile: repo ungültig (Form owner/name, nur A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich)")
    name = value.split("/", 1)[1]
    if name in (".", "..") or name.lower().endswith(".git"):
        raise Abgewiesen("Kopfzeile: repo ungültig (Name . oder .. oder mit .git)")
    if name.lower() == SELF_REPO:
        raise Abgewiesen(WAMO_REPO_GESPERRT)
    return value


def parse_header(raw: bytes) -> Kopf:
    """Erste stdin-Zeile von start: {"auftrag": str, "system": str, "modell": str (optional), "repo": "owner/name"
    (optional), "zugang": "folgt" | "server" (optional), "profil": 8 Hexzeichen (optional)}. Steuerzeichen außer
    Zeilenumbruch und Tabulator fliegen heraus; beide Texte sind begrenzt. Das Modell prüft cmd_start gegen die
    Allowlist der Werkzeugkarte, das Repo prüft check_repo. Die Kopfzeile trägt nie einen Zugangswert."""
    if not raw.endswith(b"\n") or len(raw) > MAX_HEADER_BYTES:
        raise Abgewiesen("Kopfzeile fehlt oder ist zu lang")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Abgewiesen("Kopfzeile ist kein JSON") from None
    if not isinstance(data, dict) or set(data) - {"auftrag", "system", "modell", "repo", "zugang", "profil"}:
        raise Abgewiesen("Kopfzeile hat unbekannte Felder")
    modell = data.get("modell")
    if modell is not None and (not isinstance(modell, str) or not MODELL_RE.fullmatch(modell)):
        raise Abgewiesen("Kopfzeile: modell ungültig")
    repo = data.get("repo")
    if repo is not None:
        repo = check_repo(repo)
    zugang, profil = data.get("zugang"), data.get("profil")
    if zugang is not None and zugang not in ZUGANG_MODI:
        raise Abgewiesen("Kopfzeile: zugang ungültig (folgt oder server)")
    if profil is not None and (not isinstance(profil, str) or not PROFIL_RE.fullmatch(profil)):
        raise Abgewiesen("Kopfzeile: profil ungültig")
    if zugang == "folgt" and profil is None:  # sonst teilten sich alle persönlichen Zugänge eine Kennung in der Buchführung
        raise Abgewiesen("Kopfzeile: profil fehlt bei zugang folgt")
    auftrag, system = data.get("auftrag"), data.get("system", "")
    if not isinstance(auftrag, str) or not isinstance(system, str):
        raise Abgewiesen("Kopfzeile: auftrag und system müssen Text sein")
    if KANAL == "kern" and (_versteckt(auftrag) or _versteckt(system)):
        raise Abgewiesen("Kopfzeile: unsichtbare Zeichen (Unicode-Format-, Tag-, Privat- oder Trennzeichen) sind im Kern-Kanal nicht erlaubt")
    auftrag, system = _clean(auftrag), _clean(system)
    if not auftrag.strip():
        raise Abgewiesen("Auftrag ist leer")
    if len(auftrag) > MAX_TEXT_CHARS or len(system) > MAX_TEXT_CHARS:
        raise Abgewiesen("Auftrag oder Systemtext zu lang")
    return Kopf(auftrag, system, modell or None, repo, zugang, profil)


def parse_zugang(raw: bytes, profil: str | None = None) -> ClaudeZugang:
    """Zweite stdin-Zeile von start (nur bei "zugang": "folgt"): {"typ": "oauth" | "api", "wert": str, "workspace":
    str (nur api, optional)}. Genau diese Felder, Format des Werts streng nach Typ (ZUGANG_FORMAT), eine Zeile bis
    MAX_ZUGANG_BYTES. ZugangUngueltig nennt nie den Inhalt der Zeile, auch nicht in Teilen."""
    if not raw.endswith(b"\n") or len(raw) > MAX_ZUGANG_BYTES:
        raise ZugangUngueltig("Zugangszeile fehlt oder ist zu lang")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ZugangUngueltig("Zugangszeile ist kein JSON") from None
    if not isinstance(data, dict) or set(data) - ZUGANG_FELDER:
        raise ZugangUngueltig("Zugangszeile hat unbekannte Felder")
    typ, wert, workspace = data.get("typ"), data.get("wert"), data.get("workspace")
    if not isinstance(typ, str) or typ not in ZUGANG_FORMAT:
        raise ZugangUngueltig("Zugangszeile: typ ungültig (oauth oder api)")
    if not isinstance(wert, str) or not ZUGANG_FORMAT[typ].fullmatch(wert):
        raise ZugangUngueltig(f"Zugangszeile: wert hat nicht das Format für typ {typ}")
    if workspace is not None and (typ != "api" or not isinstance(workspace, str)
                                  or not WORKSPACE_ZUGANG_RE.fullmatch(workspace)):
        raise ZugangUngueltig("Zugangszeile: workspace ungültig (nur bei api, Form wrkspc_...)")
    return ClaudeZugang(typ, wert, workspace, profil)


def zugang_typ(env: dict[str, str]) -> str:
    """Art des Claude-Zugangs in einer Sandbox-Umgebung: oauth (Abo, Kontingent) oder api (Guthaben)."""
    return "oauth" if env.get("CLAUDE_CODE_OAUTH_TOKEN") else "api"


def modell_waehlen(art: str, wunsch: str | None, typ: str) -> str:
    """Modell des Laufs. Ein Wunsch (WAMO_ENTWICKLUNG_MODELL, Kopfzeilenfeld modell) gilt nur aus der Allowlist der
    Werkzeugkarte (ValueError sonst). Ohne Wunsch: Abo-Zugang (oauth, Kontingent des Max-Abos) Sonnet, wenn die Art
    nicht ohnehin ein stärkeres Modell festlegt; sonst (api, Guthaben) das Modell der Art, derzeit Haiku."""
    wk = werkzeugkarte()
    modell = wk.modell_fuer(art, wunsch)
    if not wunsch and typ == "oauth" and modell == wk.MODELL_STANDARD:
        return wk.MODELL_SONNET
    return modell


def serverzugang_erlaubt(geheim: dict[str, str]) -> bool:
    """Darf ein Lauf ohne persönlichen Zugang den Claude-Zugang aus geheim.env nutzen? Standard ja (bisheriges Verhalten,
    auch für ältere Kerne ohne Feld zugang); WAMO_SERVERZUGANG=aus schaltet das ab, sobald jedes Profil seinen eigenen
    Zugang hat. Fail-closed: jeder andere Wert als an/ja/1/true/on und jeder Tippfehler im Namen (WAMO_SERVER...) sperrt."""
    if any(n.startswith("WAMO_SERVER") and n != "WAMO_SERVERZUGANG" for n in geheim):
        return False
    return (geheim.get("WAMO_SERVERZUGANG") or "an").strip().lower() in ("an", "ja", "1", "true", "on")


PRUEFEN_SCHALTER = "WAMO_PRUEFEN"


def pruefen_schalter(geheim: dict[str, str]) -> bool:
    """Ist pruefen eingeschaltet? Nur WAMO_PRUEFEN=an in geheim.env schaltet ein (Standard aus); jeder andere Wert und jeder
    Tippfehler im Namen (WAMO_PRUEF...) sperrt. Fail-closed wie serverzugang_erlaubt."""
    if any(n.startswith("WAMO_PRUEF") and n != PRUEFEN_SCHALTER for n in geheim):
        return False
    return geheim.get(PRUEFEN_SCHALTER, "") == "an"


def pruefen_aktiv(geheim: dict[str, str]) -> bool:
    """Schalter an UND beide Module ladbar: nur dann meldet ping pruefen als aktiv."""
    if not pruefen_schalter(geheim):
        return False
    try:
        pruefen_modul()
    except Exception:  # noqa: BLE001 (fehlt oder defekt: nicht aktiv)
        return False
    return True


# Unsichtbare Zeichen können eine Zusatzanweisung an Claude Code tragen, die niemand liest. Zwei Stufen:
#  1. _clean (jeder Kanal, auch die Statustexte) entfernt nur die gefährlichen: Tag-Zeichen (U+E0000 bis U+E007F), Bidi-Steuerzeichen
#     (U+202A bis U+202E, U+2066 bis U+2069, U+200E, U+200F, U+061C), U+200B, U+2060 bis U+2064, U+206A bis U+206F, U+180E, U+FEFF, Privatbereich (Co),
#     Surrogate (Cs), Zeilen- und Absatztrenner (Zl, Zp) und Steuerzeichen (Cc). ZWJ (U+200D), ZWNJ (U+200C), Variationsselektoren und das weiche
#     Trennzeichen bleiben, sonst zerfallen Emoji-Sequenzen (Familie, Flaggen, Herz) und persische oder indische Schrift.
#  2. _versteckt (nur der Kern-Kanal) lehnt jedes Format-, Privat-, Trenn-, Tag-Zeichen und jeden Variationsselektor ab, auch ZWJ und ZWNJ.
UNSICHTBAR_KATEGORIEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})  # streng (Kern-Kanal)
GEFAEHRLICH_KATEGORIEN = frozenset({"Cc", "Cs", "Co", "Zl", "Zp"})
GEFAEHRLICH_ZEICHEN = frozenset(
    {0x061C, 0x180E, 0x200B, 0x200E, 0x200F, 0x2060, 0xFEFF}
    | set(range(0x202A, 0x202F)) | set(range(0x2061, 0x2065)) | set(range(0x2066, 0x206A)) | set(range(0x206A, 0x2070))
    | set(range(0xE0000, 0xE0080)))


def _unsichtbar(c: str) -> bool:
    """Streng (Kern-Kanal): jedes unsichtbare Zeichen, auch ZWJ, ZWNJ, Variationsselektoren und nicht zugewiesene Zeichen."""
    n = ord(c)
    return unicodedata.category(c) in UNSICHTBAR_KATEGORIEN or 0xFE00 <= n <= 0xFE0F or 0xE0000 <= n <= 0xE01EF


def _gefaehrlich(c: str) -> bool:
    """Mild (alle Kanäle): nur die gefährlichen unsichtbaren Zeichen. Nicht zugewiesene Zeichen bleiben, sie können neue Emojis sein."""
    return unicodedata.category(c) in GEFAEHRLICH_KATEGORIEN or ord(c) in GEFAEHRLICH_ZEICHEN


def _clean(text: str) -> str:
    """Entfernt die gefährlichen unsichtbaren Zeichen (siehe oben), erhalten bleiben Zeilenumbruch, Tabulator, ZWJ, ZWNJ und Variationsselektoren.
    Gilt für jeden Kanal."""
    return "".join(c for c in text if c in "\n\t" or not _gefaehrlich(c))


def _versteckt(text: str) -> bool:
    """Enthält der Text ein unsichtbares Zeichen, das mehr ist als ein gewöhnliches Steuerzeichen unter 32 oder DEL? Der Kern bereinigt selbst:
    kommt so etwas im Kern-Kanal an, ist es keine Panne, sondern ein Versuch, eine unsichtbare Anweisung einzuschleusen."""
    return any(_unsichtbar(c) and not (ord(c) < 32 or ord(c) == 127) for c in text)


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


GIT_OHNE_HOOKS = (("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"))


def basis_env() -> dict[str, str]:
    """Umgebung für wamo-job.sh ohne Token (systemd-run --user braucht den Laufzeitordner)."""
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "XDG_RUNTIME_DIR": f"/run/user/{uid}",
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}


def _github_variablen() -> set[str]:
    """Persönliche GitHub-Token aus geheim.env (GH_TOKEN, GH_TOKEN_LESEN); im App-Modus nie in der Sandbox."""
    wk = werkzeugkarte()
    return {*wk.ZUGANG_VARIABLEN["github"], *wk.ZUGANG_VARIABLEN["github_lesen"]}


def zugangs_quellen(art: str, geheim: dict[str, str], app_modus: bool = False, repo: str | None = None,
                    zugang: ClaudeZugang | None = None) -> set[str]:
    """Namen in geheim.env, deren Werte ein Lauf dieser Art bekommt (für die Buchführung je Projekt). Im
    App-Modus steht statt GH_TOKEN bzw. GH_TOKEN_LESEN der Name GH_APP_SCHREIBEN bzw. GH_APP_LESEN, mit dem
    Umfang des Tokens: "@owner/name" (kleingeschrieben) für ein Repo, "@*" für die ganze Installation. Ein anderes
    oder breiteres Repo als in früheren Läufen des Projekts zählt so als neuer Zugang und braucht eine Freigabe.
    Ein persönlicher Claude-Zugang heißt <Variable>@<Profil> (nie der Wert): der Zugang einer anderen Person, auch
    der aus geheim.env, ist für ein Projekt ein neuer Zugang."""
    wk = werkzeugkarte()
    if zugang is not None:
        claude = f"{ZUGANG_VARIABLE[zugang.typ]}@{zugang.profil or '?'}"
    else:
        claude = next((name for name in wk.CLAUDE_VARIABLEN if geheim.get(name)), None)
    namen = {n for n in wk.zugangs_variablen(art) if geheim.get(n)}
    if app_modus:
        namen -= _github_variablen()
        stufe = wk.github_stufe(art)
        if stufe:
            namen.add(f"{GH_APP_QUELLE[stufe]}@{repo.lower() if repo else '*'}")
    return ({claude} if claude else set()) | namen


def sandbox_env(art: str, geheim: dict[str, str], app_modus: bool = False,
                gh_token: str | None = None, zugang: ClaudeZugang | None = None) -> dict[str, str] | None:
    """Basis plus genau ein Claude-Zugang und die Token der Auftragsart. None ohne Claude-Zugang. Im App-Modus
    kommt GitHub nur als gh_token (kurzlebiges Installations-Token dieses Laufs) an, nie GH_TOKEN oder
    GH_TOKEN_LESEN aus geheim.env; die App-Variablen (WAMO_GH_APP_*) gelangen nie in die Umgebung.
    Mit einem persönlichen Zugang (zugang) ist das der einzige Claude-Zugang des Laufs: nichts davon kommt aus
    geheim.env, auch nicht der Workspace-Header des Organisationsschlüssels (kein Mischen mit einem fremden Schlüssel)."""
    wk = werkzeugkarte()
    env = basis_env()
    if zugang is not None:
        env[ZUGANG_VARIABLE[zugang.typ]] = zugang.wert
        if zugang.typ == "api" and zugang.workspace:
            env["ANTHROPIC_CUSTOM_HEADERS"] = f"anthropic-workspace-id: {zugang.workspace}"
    else:
        claude = next((name for name in wk.CLAUDE_VARIABLEN if geheim.get(name)), None)
        if claude is None:
            return None
        env[claude] = geheim[claude]
        workspace = geheim.get("ANTHROPIC_WORKSPACE_ID", "")
        if claude == "ANTHROPIC_API_KEY" and WORKSPACE_RE.fullmatch(workspace):
            # Organisationsschlüssel ohne festen Workspace: Claude Code schickt den Header bei jeder Anfrage mit
            env["ANTHROPIC_CUSTOM_HEADERS"] = f"anthropic-workspace-id: {workspace}"
    persoenlich = _github_variablen()
    for name in wk.zugangs_variablen(art):
        if app_modus and name in persoenlich:
            continue
        if geheim.get(name):
            env[wk.sandbox_name(name)] = geheim[name]
    if app_modus and gh_token and wk.github_stufe(art):
        env["GH_TOKEN"] = gh_token
    # Git in jedem Repo der Sandbox: keine Hooks und kein fsmonitor (Vorrang vor Repo-Konfigurationen, auch in
    # Submodulen und eingebetteten Repos, die die Entschärfung vor dem Lauf nicht erreicht)
    git = list(GIT_OHNE_HOOKS)
    if "GH_TOKEN" in env:
        # git über HTTPS meldet sich mit dem Token an (gh liest GH_TOKEN selbst); keine Datei nötig
        git += [("credential.https://github.com.helper", ""),
                ("credential.https://github.com.helper", "!/usr/bin/gh auth git-credential")]
    env["GIT_CONFIG_COUNT"] = str(len(git))
    for i, (key, value) in enumerate(git):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    return env


# ---------------------------------------------------------------- GitHub-App: kurzlebige Token je Lauf
GH_APP_ID = "WAMO_GH_APP_ID"
GH_APP_INSTALLATION = "WAMO_GH_APP_INSTALLATION_ID"
GH_APP_SCHLUESSEL = "WAMO_GH_APP_SCHLUESSEL"
GH_APP_VARIABLEN = (GH_APP_ID, GH_APP_INSTALLATION, GH_APP_SCHLUESSEL)
GH_APP_QUELLE = {"lesen": "GH_APP_LESEN", "schreiben": "GH_APP_SCHREIBEN"}  # Namen für die Buchführung je Projekt
# Der private Schlüssel liegt direkt in /etc/wamo (root:wamo 0750, in der Sandbox unsichtbar; der Selbsttest prüft
# das bei jedem Lauf). Erlaubt sind nur Dateien root:<gruppe>, nicht gruppen- oder weltschreibbar, nicht weltlesbar.
SCHLUESSEL_ORDNER = Path("/etc/wamo")
SCHLUESSEL_BESITZER: tuple[int, ...] = (0,)
SCHLUESSEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
MAX_SCHLUESSEL_BYTES = 16 * 1024
MIN_RSA_BITS = 2048
APP_ID_RE = re.compile(r"[1-9][0-9]{0,11}")
INSTALLATION_RE = re.compile(r"[1-9][0-9]{0,15}")
# Form des Installations-Tokens: ghs_ plus Zeichen, die als Header und Umgebungswert unbedenklich sind. Echte Token
# sind rund 390 Zeichen lang und enthalten Punkte (Stand 21.10.2026), deshalb Obergrenze 1000 statt 255.
TOKEN_RE = re.compile(r"ghs_[A-Za-z0-9_.-]{16,1000}")
TOKEN_ZEICHEN_RE = re.compile(r"[\x21-\x7e]{1,2000}")  # darf zum Widerruf als Bearer-Header dienen
GITHUB_API_HOST = "api.github.com"
GITHUB_API_VERSION = "2022-11-28"
GITHUB_ZEITLIMIT_S = 15
MAX_GITHUB_ANTWORT = 256 * 1024
JWT_RUECKDATIERT_S = 60  # iat eine Minute zurück (Uhrabweichung)
JWT_GUELTIG_S = 9 * 60   # exp höchstens 9 Minuten nach jetzt (GitHub erlaubt bis 10)
STUFEN_RANG = {"read": 1, "write": 2}


class GithubAppFehler(Exception):
    """Token der GitHub-App nicht erzeugbar oder Konfiguration kaputt. Die Meldung enthält nie Schlüssel oder Token."""


class _Beendet(BaseException):
    """SIGTERM oder SIGHUP während eines Laufs: Sandbox stoppen, Token widerrufen, dann enden."""


# App-Konfiguration: App-ID, Installation-ID, Dateiname des Schlüssels in SCHLUESSEL_ORDNER
GithubApp = namedtuple("GithubApp", ("app_id", "installation_id", "schluessel_name"))


def github_app_konfig(geheim: dict[str, str]) -> GithubApp | None:
    """App-Modus aus geheim.env. None, wenn keine der drei Variablen gesetzt ist (Rückfall auf GH_TOKEN bzw.
    GH_TOKEN_LESEN). Ist nur ein Teil gesetzt oder ein Wert ungültig, GithubAppFehler: wer den App-Modus
    einrichtet, bekommt bei einem Fehler keinen stillen Rückfall auf den persönlichen Token (fail-closed)."""
    unbekannt = sorted(n for n in geheim if n.startswith("WAMO_GH_") and n not in GH_APP_VARIABLEN)
    if unbekannt:  # Tippfehler im Namen schaltet den App-Modus sonst still aus (Rückfall auf den persönlichen Token)
        raise GithubAppFehler(f"unbekannte Einstellung {', '.join(unbekannt)[:120]} (Tippfehler im Namen?)")
    gesetzt = [n for n in GH_APP_VARIABLEN if geheim.get(n)]
    if not gesetzt:
        return None
    fehlt = [n for n in GH_APP_VARIABLEN if n not in gesetzt]
    if fehlt:
        raise GithubAppFehler(f"Konfiguration unvollständig, es fehlt {', '.join(fehlt)}")
    app_id, installation, pfad = (geheim[n].strip() for n in GH_APP_VARIABLEN)
    if not APP_ID_RE.fullmatch(app_id):
        raise GithubAppFehler(f"{GH_APP_ID} ist keine App-ID (nur Ziffern)")
    if not INSTALLATION_RE.fullmatch(installation):
        raise GithubAppFehler(f"{GH_APP_INSTALLATION} ist keine Installation-ID (nur Ziffern)")
    ordner, _, name = pfad.rpartition("/")
    if ordner != SCHLUESSEL_ORDNER.as_posix().rstrip("/") or not SCHLUESSEL_NAME_RE.fullmatch(name):
        raise GithubAppFehler(f"{GH_APP_SCHLUESSEL} muss eine Datei direkt in {SCHLUESSEL_ORDNER.as_posix()} sein "
                              f"(z. B. {SCHLUESSEL_ORDNER.as_posix()}/gh-app.pem)")
    return GithubApp(app_id, installation, name)


def _rechte_grund(st: os.stat_result, was: str, datei: bool) -> str | None:
    """Grund, warum Besitz oder Rechte von Schlüsseldatei bzw. -ordner nicht passen, oder None."""
    if datei and not stat.S_ISREG(st.st_mode):
        return f"{was} ist keine reguläre Datei"
    if not datei and not stat.S_ISDIR(st.st_mode):
        return f"{was} ist kein Ordner"
    if st.st_uid not in SCHLUESSEL_BESITZER:
        return f"{was} gehört nicht root"
    if st.st_mode & 0o022:
        return f"{was} ist für Gruppe oder alle beschreibbar"
    if st.st_mode & 0o007:
        return f"{was} ist für alle zugänglich (chmod o= nötig)"
    return None


def schluessel_lesen(name: str) -> bytearray:
    """Liest den privaten Schlüssel der App, nur im Dispatcher-Prozess: Ordner und Datei ohne Links (O_NOFOLLOW,
    Datei relativ zum geöffneten Ordner), ohne an einer FIFO zu hängen, mit Besitzer- und Rechteprüfung auf dem
    geöffneten Deskriptor und Größenlimit. Der Inhalt landet nie im Log, in argv oder in der Sandbox; der Aufrufer
    überschreibt den Puffer nach Gebrauch (nur best effort, Python-Kopien bleiben bis zur Speicherbereinigung)."""
    if not SCHLUESSEL_NAME_RE.fullmatch(name):
        raise GithubAppFehler("Name der Schlüsseldatei ungültig")
    flags = os.O_RDONLY | O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    ordner_fd = fd = None
    try:
        try:
            if O_DIRECTORY and os.open in os.supports_dir_fd:
                ordner_fd = os.open(SCHLUESSEL_ORDNER, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
                ordner_st = os.fstat(ordner_fd)
            else:  # nur ohne dir_fd (Windows, Tests)
                ordner_st = os.stat(SCHLUESSEL_ORDNER)
        except OSError as e:
            raise GithubAppFehler(f"Schlüsselordner nicht lesbar ({errno.errorcode.get(e.errno, 'Fehler')})") from None
        grund = _rechte_grund(ordner_st, "Schlüsselordner", datei=False)
        if grund:
            raise GithubAppFehler(grund)
        try:
            fd = os.open(name, flags, dir_fd=ordner_fd) if ordner_fd is not None else os.open(SCHLUESSEL_ORDNER / name, flags)
            st = os.fstat(fd)
        except OSError as e:
            raise GithubAppFehler(f"Schlüsseldatei nicht lesbar ({errno.errorcode.get(e.errno, 'Fehler')}; "
                                  "fehlt sie oder ist sie ein Link?)") from None
        grund = _rechte_grund(st, "Schlüsseldatei", datei=True)
        if grund:
            raise GithubAppFehler(grund)
        if st.st_size > MAX_SCHLUESSEL_BYTES:
            raise GithubAppFehler("Schlüsseldatei ist zu groß für einen privaten Schlüssel")
        daten = bytearray()
        while len(daten) <= MAX_SCHLUESSEL_BYTES:
            teil = os.read(fd, MAX_SCHLUESSEL_BYTES + 1 - len(daten))
            if not teil:
                break
            daten += teil
        if len(daten) > MAX_SCHLUESSEL_BYTES:
            daten[:] = bytes(len(daten))
            raise GithubAppFehler("Schlüsseldatei ist zu groß für einen privaten Schlüssel")
        return daten
    finally:
        for f in (fd, ordner_fd):
            if f is not None:
                os.close(f)


def _rsa_schluessel(pem: bytes):
    """Privater RSA-Schlüssel aus PEM (ohne Passwort, mindestens 2048 Bit) über das Systempaket cryptography."""
    try:
        from cryptography.exceptions import UnsupportedAlgorithm
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        raise GithubAppFehler("Python-Paket cryptography fehlt (python3-cryptography)") from None
    if not pem.lstrip().startswith(b"-----BEGIN "):
        raise GithubAppFehler("Schlüsseldatei ist kein PEM-Schlüssel")
    try:
        schluessel = serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise GithubAppFehler("Schlüsseldatei ist kein lesbarer privater Schlüssel ohne Passwort") from None
    if not isinstance(schluessel, rsa.RSAPrivateKey) or schluessel.key_size < MIN_RSA_BITS:
        raise GithubAppFehler(f"Schlüssel ist kein RSA-Schlüssel mit mindestens {MIN_RSA_BITS} Bit")
    return schluessel


def _b64url(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode("ascii")


def jwt_erzeugen(app_id: str, pem: bytes, jetzt: float | None = None) -> str:
    """JWT für die App (RS256): iat eine Minute zurück, exp höchstens 9 Minuten voraus, iss = App-ID."""
    if not APP_ID_RE.fullmatch(app_id):
        raise GithubAppFehler("App-ID ungültig")
    schluessel = _rsa_schluessel(pem)  # meldet ein fehlendes cryptography als GithubAppFehler
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    t = int(time.time() if jetzt is None else jetzt)
    kopf = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    inhalt = _b64url(json.dumps({"iat": t - JWT_RUECKDATIERT_S, "exp": t + JWT_GUELTIG_S, "iss": app_id},
                                separators=(",", ":")).encode())
    signatur = schluessel.sign(f"{kopf}.{inhalt}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{kopf}.{inhalt}.{_b64url(signatur)}"


def _https(methode: str, pfad: str, kopf: dict[str, str], koerper: bytes | None = None) -> tuple[int, bytes]:
    """Eine HTTPS-Anfrage an api.github.com: Zertifikat und Hostname geprüft (Systemspeicher), mindestens TLS 1.2,
    Zeitlimit, kein Proxy aus der Umgebung, keine Weiterleitung (http.client folgt keiner; ein Token geht so nie
    an einen anderen Host). Antwort höchstens MAX_GITHUB_ANTWORT + 1 Bytes."""
    kontext = ssl.create_default_context()
    kontext.minimum_version = ssl.TLSVersion.TLSv1_2
    verbindung = http.client.HTTPSConnection(GITHUB_API_HOST, 443, timeout=GITHUB_ZEITLIMIT_S, context=kontext)
    try:
        verbindung.request(methode, pfad, body=koerper, headers=kopf)
        antwort = verbindung.getresponse()
        return antwort.status, antwort.read(MAX_GITHUB_ANTWORT + 1)
    finally:
        verbindung.close()


def _github(methode: str, pfad: str, anmeldung: str, koerper: dict | None = None) -> tuple[int, object]:
    """Anfrage an die GitHub-API mit Bearer-Anmeldung (JWT oder Token); Fehler als GithubAppFehler ohne Geheimnisse."""
    kopf = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {anmeldung}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION, "User-Agent": "wamo-job-dispatch"}
    daten = None
    if koerper is not None:
        daten = json.dumps(koerper).encode("utf-8")
        kopf["Content-Type"] = "application/json"
    try:
        status, roh = _https(methode, pfad, kopf, daten)
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise GithubAppFehler(f"GitHub nicht erreichbar ({type(e).__name__})") from None
    if len(roh) > MAX_GITHUB_ANTWORT:
        raise GithubAppFehler("Antwort von GitHub zu groß")
    try:
        obj = json.loads(roh) if roh else None
    except (ValueError, RecursionError):
        obj = None
    return status, obj


def _github_meldung(status: int, obj: object) -> str:
    text = obj.get("message") if isinstance(obj, dict) else None
    text = _clean(str(text)).replace("\n", " ").replace("\t", " ")[:160] if text else ""
    hinweis = {401: "JWT abgelehnt (App-ID, Schlüssel oder Uhrzeit prüfen)",
               403: "keine Berechtigung für diese Installation",
               404: "Installation nicht gefunden (Installation-ID prüfen)",
               422: "Repo oder Rechte nicht in der Installation (Repo freigegeben? Rechte der App gesetzt?)"}.get(status, "")
    return f"GitHub antwortet {status}" + (f": {hinweis}" if hinweis else "") + (f" ({text})" if text else "")


def _antwort_pruefen(obj: dict, rechte: dict[str, str], repo: str | None) -> str | None:
    """Prüft die Token-Antwort: nie mehr Rechte als angefragt, mit Repo genau dieses eine Repo (Owner inklusive)."""
    erhalten = obj.get("permissions")
    if not isinstance(erhalten, dict):
        return "Antwort von GitHub ohne Rechteangabe"
    for bereich, stufe in erhalten.items():
        if bereich not in rechte or not isinstance(stufe, str) \
                or STUFEN_RANG.get(stufe, 99) > STUFEN_RANG[rechte[bereich]]:
            return f"GitHub vergab mehr Rechte als angefragt ({_clean(str(bereich))[:40]})"
    repos = obj.get("repositories")
    namen = [r.get("full_name") for r in repos if isinstance(r, dict)] if isinstance(repos, list) else []
    if repo:
        if len(namen) != 1 or not isinstance(namen[0], str) or namen[0].lower() != repo.lower():
            return f"Token gilt nicht genau für {repo} (gehört das Repo zur Installation?)"
        return None
    # Ohne Ziel-Repo gilt das Token für die ganze Installation: sie muss auf ausgewählte Repos beschränkt sein, und
    # das WAMO-Repo selbst (für Aufträge gesperrt) darf nicht dazugehören
    if obj.get("repository_selection") != "selected":
        return "Installation ist nicht auf ausgewählte Repos beschränkt (Only select repositories einstellen)"
    if any(isinstance(n, str) and n.rsplit("/", 1)[-1].lower() == SELF_REPO for n in namen):
        return "Das WAMO-Repo gehört zur Installation (aus der Auswahl der App entfernen)"
    return None


MAX_REPO_SEITEN = 5  # 500 Repos; mehr gelten als nicht prüfbar


def _repos_der_installation(token: str) -> list[str]:
    """Namen (owner/name) aller Repos, die das Token sieht. GithubAppFehler, wenn die Liste nicht lesbar ist."""
    namen: list[str] = []
    for seite in range(1, MAX_REPO_SEITEN + 1):
        status, obj = _github("GET", f"/installation/repositories?per_page=100&page={seite}", token)
        repos = obj.get("repositories") if isinstance(obj, dict) else None
        if status != 200 or not isinstance(repos, list):
            raise GithubAppFehler(f"Repos der Installation nicht lesbar (GitHub antwortet {status})")
        namen += [r["full_name"] for r in repos if isinstance(r, dict) and isinstance(r.get("full_name"), str)]
        if len(repos) < 100:
            return namen
    raise GithubAppFehler(f"Installation hat mehr als {MAX_REPO_SEITEN * 100} Repos, die Prüfung auf das WAMO-Repo "
                          "ist nicht möglich")


def installations_token(app: GithubApp, art: str, repo: str | None) -> tuple[str, str]:
    """Holt ein Installations-Token mit den Rechten der Art (Werkzeugkarte) und, falls bekannt, nur für das Ziel-Repo.
    Liefert (token, ablauf). Passt die Antwort nicht, wird das Token sofort widerrufen und GithubAppFehler geworfen."""
    try:
        rechte = werkzeugkarte().github_app_rechte(art)
    except (KeyError, ValueError) as e:
        raise GithubAppFehler(f"Werkzeugkarte: {e}") from None
    if rechte is None:
        raise GithubAppFehler("Auftragsart ohne GitHub-Zugang")
    koerper: dict = {"permissions": rechte}
    if repo:
        koerper["repositories"] = [repo.split("/", 1)[1]]
    pem = schluessel_lesen(app.schluessel_name)
    try:
        jwt = jwt_erzeugen(app.app_id, bytes(pem))
    finally:
        pem[:] = bytes(len(pem))
    status, obj = _github("POST", f"/app/installations/{app.installation_id}/access_tokens", jwt, koerper)
    del jwt
    if status != 201 or not isinstance(obj, dict):
        raise GithubAppFehler(_github_meldung(status, obj))
    token = obj.get("token")
    try:  # ab hier existiert ein Token bei GitHub: jeder Fehler, auch ein unerwarteter, widerruft es
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            raise GithubAppFehler("Antwort von GitHub ohne gültiges Token")
        grund = _antwort_pruefen(obj, rechte, repo)
        if grund:
            raise GithubAppFehler(grund)
        if not repo and any(n.rsplit("/", 1)[-1].lower() == SELF_REPO for n in _repos_der_installation(token)):
            # die Antwort zählt die Repos nicht auf: erst die Liste der Installation zeigt, ob das WAMO-Repo dabei ist
            raise GithubAppFehler("Das WAMO-Repo gehört zur Installation (aus der Auswahl der App entfernen)")
        ablauf = obj.get("expires_at")
        ablauf = ablauf if isinstance(ablauf, str) and re.fullmatch(r"[0-9TZ:.+-]{10,40}", ablauf) else "?"
        return token, ablauf
    except BaseException:
        if isinstance(token, str) and TOKEN_ZEICHEN_RE.fullmatch(token) and not token_widerrufen(token):
            log("gh_app_widerruf FEHLGESCHLAGEN (abgelehntes Token, läuft spätestens nach einer Stunde ab)")
            wamo_event("gh_widerruf_fehlgeschlagen", grund="Ein abgelehntes GitHub-Token konnte nicht widerrufen "
                       "werden; es läuft spätestens nach einer Stunde ab")
        raise


WIDERRUF_PAUSEN_S = (1.0, 3.0)  # Pausen vor dem zweiten und dritten Versuch


def token_widerrufen(token: str) -> bool:
    """DELETE /installation/token mit dem Token selbst. True bei 204 (oder 401: schon ungültig). Bis zu drei
    Versuche mit kurzen Pausen bei Netzfehlern, Serverfehlern (5xx) und Ratenbegrenzung (403, 429)."""
    for versuch in range(len(WIDERRUF_PAUSEN_S) + 1):
        if versuch:
            time.sleep(WIDERRUF_PAUSEN_S[versuch - 1])
        try:
            status, _obj = _github("DELETE", "/installation/token", token)
        except GithubAppFehler:
            continue
        if status in (204, 401):
            return True
        if status < 500 and status not in (403, 429):
            return False
    return False


@contextmanager
def _signale(handler):
    """Setzt für SIGTERM und SIGHUP vorübergehend handler (nur im Hauptthread, wo es die Signale gibt)."""
    namen = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if s is not None]
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    alt = {s: signal.signal(s, handler) for s in namen}
    try:
        yield
    finally:
        for s, h in alt.items():
            signal.signal(s, h)


def _als_ausnahme(signum, _frame):
    """Handler für SIGTERM und SIGHUP: ab dem ersten Signal werden weitere ignoriert (das Aufräumen mit Stopp der
    Sandbox und Widerruf des Tokens darf nicht ein zweites Mal unterbrochen werden), dann endet der Lauf."""
    for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)):
        if s is not None:
            signal.signal(s, signal.SIG_IGN)
    raise _Beendet(signum)


def _signale_aufschieben():
    """Blockiert SIGTERM und SIGHUP (sie bleiben anhängig, gehen nicht verloren). Liefert die alte Maske oder None."""
    namen = {s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if s is not None}
    if not hasattr(signal, "pthread_sigmask") or threading.current_thread() is not threading.main_thread():
        return None
    return signal.pthread_sigmask(signal.SIG_BLOCK, namen)


def _signale_zulassen(alt) -> None:
    if alt is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, alt)


@contextmanager
def github_token_fuer_lauf(job: str, art: str, repo: str | None, app: GithubApp | None):
    """Kurzlebiges GitHub-Token für genau einen Lauf: liefert None ohne App-Modus oder für Arten ohne GitHub.
    Sonst Token erzeugen (Fehler: GithubAppFehler, bevor irgendetwas startet) und es nach dem Block widerrufen,
    auch bei Ausnahme, Abbruch oder Signal. Während der Erzeugung sind SIGTERM und SIGHUP aufgeschoben (kein Token
    ohne Widerruf), während des Widerrufs ignoriert. Im Audit stehen nur Auftrag, Repo, Stufe und Ablaufzeit."""
    try:
        stufe = werkzeugkarte().github_stufe(art) if app is not None else None
    except (KeyError, ValueError) as e:
        raise GithubAppFehler(f"Werkzeugkarte: {e}") from None
    if stufe is None:
        yield None
        return
    maske = _signale_aufschieben()
    try:
        token, ablauf = installations_token(app, art, repo)
    except BaseException:
        _signale_zulassen(maske)
        raise
    try:
        _signale_zulassen(maske)  # ein aufgeschobenes Signal kommt jetzt an und endet im finally (Widerruf)
        log(f"gh_app_token {job} repo={repo or '*'} stufe={stufe} ablauf={ablauf}", pflicht=True)
        yield token
    finally:
        ok = False
        for _versuch in range(2):  # ein erstes Signal genau jetzt (_Beendet) überspringt den Widerruf nicht;
            try:                   # _als_ausnahme ignoriert alle weiteren, mehr als ein Wiederholen braucht es nie
                with _signale(signal.SIG_IGN):
                    ok = token_widerrufen(token)
                break
            except _Beendet:
                continue
        if ok:
            log(f"gh_app_widerruf {job} ok")
        else:
            log(f"gh_app_widerruf {job} FEHLGESCHLAGEN (Token läuft spätestens nach einer Stunde ab)")
            wamo_event("gh_widerruf_fehlgeschlagen", grund="Das GitHub-Token konnte nicht widerrufen werden; "
                       "es läuft spätestens nach einer Stunde ab")


def github_status() -> str:
    """Für ping: "token" (kein App-Modus), "app" (Konfiguration und Schlüssel gültig) oder "fehler: <Grund>".
    Ohne Netz; ob GitHub das Token ausstellt, prüft wamo-job-rauchtest --app-echt."""
    try:
        app = github_app_konfig(lese_geheimnisse())
        if app is None:
            return "token"
        pem = schluessel_lesen(app.schluessel_name)
        try:
            _rsa_schluessel(bytes(pem))
        finally:
            pem[:] = bytes(len(pem))
        return "app"
    except GithubAppFehler as e:
        return f"fehler: {e}"


def github_hinweis(repo: str | None) -> str:
    """Zusatz zum Systemtext im App-Modus (nur für Arten mit GitHub)."""
    ziel = f"nur für das Repo {repo}" if repo else "für die Repos der Installation"
    return (f"- GitHub: GH_TOKEN ist ein kurzlebiges Token nur für diesen Auftrag, {ziel}, mit Rechten für Inhalte "
            "und Pull Requests. Repos anlegen, Einstellungen, Workflows, Secrets und Löschen gehen damit nicht; "
            "braucht der Auftrag das, im Ergebnis nennen.")


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
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {client} {text} kanal={KANAL}\n"
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
    # M1: den Auftrags-Konfigordner bis auf projects/ (Sitzungen für --resume) leeren, damit nichts, was ein
    # früherer Lauf dort angelegt hat (agents/, commands/, skills/, settings.local.json), überlebt.
    import shutil
    for eintrag in claude_dir.iterdir() if claude_dir.is_dir() else ():
        if eintrag.name == "projects" and eintrag.is_dir() and not eintrag.is_symlink():
            continue
        try:
            if eintrag.is_dir() and not eintrag.is_symlink():
                shutil.rmtree(eintrag)
            else:
                eintrag.unlink()
        except OSError:
            pass
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


def vorbereiten(jobdir: Path, job: str, projekt: str, art: str, repo: str | None = None, kanal: str | None = None) -> None:
    """Legt den Auftragsordner an oder prüft die Bindung an Projekt, Art und Ziel-Repo (unter der Auftragssperre).
    Das Repo begrenzt das GitHub-Token; eine Fortsetzung mit einem anderen Repo wird abgewiesen. Neue Aufträge aus dem
    Kern-Kanal oder dem PC-Kanal tragen kanal (kern oder pc); ein Auftrag ohne das Feld gehört nie dem Kern-Kanal."""
    meta_path = jobdir / "auftrag.json"
    meta = read_json(meta_path, None)
    if meta is None:
        write_json_atomic(meta_path, {"job": job, "projekt": projekt, "art": art, "repo": repo,
                                      "erstellt": int(time.time()), **({"kanal": kanal} if kanal in ("kern", "pc") else {})})
    elif not isinstance(meta, dict) or meta.get("projekt") != projekt or meta.get("art") != art:
        raise Abgewiesen("Auftrag gehört zu einem anderen Projekt oder einer anderen Art")
    elif meta.get("repo") != repo:
        raise Abgewiesen("Auftrag gehört zu einem anderen Ziel-Repo")
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
    schluss = time.monotonic() + GESAMT_PRUEF_BUDGET_S
    for line in text.splitlines()[:MAX_PENDING_LINES]:
        if time.monotonic() > schluss:  # N6: Gesamtbudget je Aufruf, Rest gilt als ungültig
            break
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
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1", "LANG": "C"}
GIT_ERLAUBT = [re.compile(p) for p in (
    r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks|autocrlf|eol)",
    r"extensions\.objectformat", r"remote\..+\.(url|fetch)", r"branch\..+\.(remote|merge|rebase)",
    r"user\.(name|email)", r"pull\.(rebase|ff)", r"push\.(default|autosetupremote)", r"init\.defaultbranch",
)]
GIT_URL_RE = re.compile(r"https://[A-Za-z0-9.-]+(:\d{1,5})?/[A-Za-z0-9._~/%-]*")
GIT_SICHER = [("core.repositoryformatversion", "0"), ("core.bare", "false")]  # config, wenn die alte unlesbar ist
MAX_CONFIG_BYTES = 1024 * 1024
WALK_MAX_DIRS = 4000      # eingebettete Repos finden, aber node_modules nicht unbegrenzt durchlaufen
WALK_MAX_TIEFE = 8
INVENTUR_FLAECHEN = ("package.json", ".npmrc", ".envrc", "CLAUDE.md", ".claude", "node_modules/.bin")


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
    return os.open(name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=dir_fd)


def _rmtree_at(dir_fd: int, name: str) -> bool:
    """Entfernt name in dir_fd samt Inhalt, ohne Links zu verfolgen. Ein Link wird nur gelöst (unlink), ein
    Verzeichnis rekursiv geleert. False, wenn es den Eintrag nicht gibt."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        os.unlink(name, dir_fd=dir_fd)
        return True
    sub = os.open(name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=dir_fd)
    try:
        for kind in os.listdir(sub):
            if not _rmtree_at(sub, kind):
                os.unlink(kind, dir_fd=sub)
    finally:
        os.close(sub)
    os.rmdir(name, dir_fd=dir_fd)
    return True


def _entfernen_at(dir_fd: int, name: str) -> bool:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISDIR(st.st_mode):
        return False
    os.unlink(name, dir_fd=dir_fd)
    return True


def _config_schreiben_at(git_fd: int, praefix: str, gefiltert: list[tuple[str, str]], grund: str,
                         entfernt: list[str]) -> None:
    """Ersetzt git_fd/config durch eine gefilterte Fassung (fail-closed: auch wenn die alte ein Link, zu groß
    oder unlesbar war). os.replace ersetzt die Datei oder den Link selbst, nicht dessen Ziel."""
    neu = git_config_schreiben(gefiltert)
    tmp = ".config.wamo.tmp"
    _entfernen_at(git_fd, tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o644, dir_fd=git_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(neu)
    os.replace(tmp, "config", src_dir_fd=git_fd, dst_dir_fd=git_fd)
    entfernt.append(f"{praefix}/config bereinigt ({grund})")


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
    # config fail-closed: ein Link, eine Datei über MAX_CONFIG_BYTES oder unlesbares ergibt None und wird durch
    # eine sichere Fassung ersetzt (nicht übersprungen). read_no_follow öffnet mit O_NOFOLLOW, folgt also keinem Link.
    text = read_no_follow(Path("config"), MAX_CONFIG_BYTES, dir_fd=git_fd)
    if text is None:
        _config_schreiben_at(git_fd, praefix, GIT_SICHER, "war Link, zu groß oder unlesbar", entfernt)
        return
    eintraege = git_config_lesen(text)
    if eintraege is None:
        _config_schreiben_at(git_fd, praefix, GIT_SICHER, "nicht parsbar", entfernt)
        return
    gefiltert = git_config_filtern(eintraege)
    if gefiltert == eintraege:
        return
    weg = sorted({k for k, _ in eintraege} - {k for k, _ in gefiltert})
    _config_schreiben_at(git_fd, praefix, gefiltert, ", ".join(weg)[:200] or "nichts", entfernt)


def _git_behandeln(parent_fd: int, praefix: str, st: os.stat_result, entfernt: list[str]) -> None:
    """Ein .git-Eintrag: Link und gitdir-Zeiger (Datei) werden entfernt, ein echtes Verzeichnis entschärft."""
    if stat.S_ISLNK(st.st_mode):
        os.unlink(".git", dir_fd=parent_fd)
        entfernt.append(f"{praefix}.git (Link entfernt)")
        return
    if not stat.S_ISDIR(st.st_mode):  # .git als Datei (gitdir:-Zeiger) oder Sonstiges: entfernen
        os.unlink(".git", dir_fd=parent_fd)
        entfernt.append(f"{praefix}.git (gitdir-Zeiger entfernt)")
        return
    git_fd = os.open(".git", os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=parent_fd)
    try:
        _git_ordner_entschaerfen(git_fd, f"{praefix}.git", entfernt)
        module_fd = _verzeichnis_at(git_fd, "modules")
        if module_fd is not None:
            try:
                for name in sorted(os.listdir(module_fd))[:200]:
                    sub_fd = _verzeichnis_at(module_fd, name)
                    if sub_fd is not None:
                        try:
                            _git_ordner_entschaerfen(sub_fd, f"{praefix}.git/modules/{name}", entfernt)
                        finally:
                            os.close(sub_fd)
            finally:
                os.close(module_fd)
    finally:
        os.close(git_fd)


def _walk_entschaerfen(parent_fd: int, praefix: str, tiefe: int, zaehler: list[int], entfernt: list[str]) -> None:
    """Begrenzte fd-basierte Suche nach .git-Einträgen (auch eingebettete Repos tief im Baum, H2). Kein Link
    wird verfolgt; node_modules und ähnliche große Bäume begrenzt WALK_MAX_DIRS."""
    if tiefe > WALK_MAX_TIEFE or zaehler[0] > WALK_MAX_DIRS:
        return
    try:
        namen = sorted(os.listdir(parent_fd))
    except OSError:
        return
    unterordner = []
    for name in namen:
        zaehler[0] += 1
        if zaehler[0] > WALK_MAX_DIRS:
            break
        try:
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            continue
        if name == ".git":
            _git_behandeln(parent_fd, praefix, st, entfernt)
        elif stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
            unterordner.append(name)
    for name in unterordner:
        if zaehler[0] > WALK_MAX_DIRS:
            break
        try:
            sub_fd = os.open(name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=parent_fd)
        except OSError:
            continue
        try:
            _walk_entschaerfen(sub_fd, f"{praefix}{name}/", tiefe + 1, zaehler, entfernt)
        finally:
            os.close(sub_fd)


def projekt_entschaerfen(projektdir: Path) -> list[str]:
    """Entfernt Autostart-Flächen, die ein früherer Lauf (mit anderen Token) gepflanzt haben könnte: .mcp.json,
    .claude/settings*.json im Projektwurzel sowie Git-Hooks, config.worktree und eine bereinigte config in jedem
    .git (auch eingebettet oder als gitdir-Zeiger) im Baum. Läuft außerhalb der Sandbox über Verzeichnis-
    Deskriptoren, kein Link wird verfolgt. Fail-closed (H1): lässt sich der Ordner nicht öffnen, wirft os.open,
    der Lauf startet dann nicht. Vorher chmod 0750, damit ein früherer Lauf den Ordner nicht unlesbar machen konnte."""
    entfernt: list[str] = []
    try:
        os.chmod(projektdir, 0o750)
    except OSError:
        pass
    projekt_fd = os.open(projektdir, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW)  # Fehler: fail-closed
    try:
        if _entfernen_at(projekt_fd, ".mcp.json"):
            entfernt.append(".mcp.json")
        # Das projekteigene .claude (settings, agents, skills, commands, hooks) ist ein Ausführungsweg und wird
        # für einen isolierten Lauf nicht gebraucht: ganz entfernen. Ein Link an der Stelle wird nur gelöst.
        if _rmtree_at(projekt_fd, ".claude"):
            entfernt.append(".claude/ entfernt")
        _walk_entschaerfen(projekt_fd, "", 0, [0], entfernt)
    finally:
        os.close(projekt_fd)
    return entfernt


def _signatur_at(projekt_fd: int, rel: str) -> str | None:
    """Kurzsignatur einer Autostart-Fläche: bei package.json die scripts-Schlüssel, bei Ordnern die sortierte
    Namensliste, sonst mtime und Größe. None, wenn es den Eintrag nicht gibt. Folgt keinem Link (dann als Link markiert)."""
    pfade = rel.split("/")
    fd = projekt_fd
    zu_schliessen = []
    try:
        for teil in pfade[:-1]:
            fd = _verzeichnis_at_ro(fd, teil)
            if fd is None:
                return None
            zu_schliessen.append(fd)
        name = pfade[-1]
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            return None
        if stat.S_ISLNK(st.st_mode):
            return "link"
        if stat.S_ISDIR(st.st_mode):
            try:
                eintraege = sorted(os.listdir(os.open(name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=fd)))
            except OSError:
                return "ordner?"
            return f"ordner[{len(eintraege)}]: " + ", ".join(eintraege[:20])
        if rel == "package.json" and stat.S_ISREG(st.st_mode):
            text = read_no_follow(Path(name), 512 * 1024, dir_fd=fd)
            try:
                skripte = json.loads(text).get("scripts") if text else None
            except (ValueError, AttributeError):
                skripte = None
            if isinstance(skripte, dict):
                return "scripts: " + "; ".join(f"{k}={str(v)[:60]}" for k, v in list(skripte.items())[:20])
        return f"{int(st.st_mtime)}:{st.st_size}"
    finally:
        for f in zu_schliessen:
            os.close(f)


def _verzeichnis_at_ro(dir_fd: int, name: str) -> int | None:
    """Wie _verzeichnis_at, aber ohne zu verändern (für die Inventur, nur lesen)."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        return None
    try:
        return os.open(name, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        return None


def projekt_inventur(projektdir: Path) -> dict[str, str]:
    """Signaturen der Autostart-Flächen, die beim Weiterverwenden eines Projekts implizit Code ausführen können
    (package.json-Skripte, .npmrc, .envrc, CLAUDE.md, .claude/, node_modules/.bin). Nur lesen."""
    out: dict[str, str] = {}
    try:
        projekt_fd = os.open(projektdir, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
    except OSError:
        return out
    try:
        for rel in INVENTUR_FLAECHEN:
            sig = _signatur_at(projekt_fd, rel)
            if sig is not None:
                out[rel] = sig
    finally:
        os.close(projekt_fd)
    return out


def inventur_vergleich(vorher: dict, nachher: dict) -> list[str]:
    """Menschlich lesbare Liste der Flächen, die seit dem letzten geprüften Lauf neu oder geändert sind (H3)."""
    vorher = vorher if isinstance(vorher, dict) else {}
    zeilen = []
    for rel, sig in nachher.items():
        if rel not in vorher:
            zeilen.append(f"neu: {rel} ({sig})")
        elif vorher[rel] != sig:
            zeilen.append(f"geändert: {rel} ({sig})")
    for rel in vorher:
        if rel not in nachher:
            zeilen.append(f"entfernt: {rel}")
    return zeilen


def projekt_leeren_sicher(projektdir: Path) -> bool:
    """Löscht den Inhalt des Projektordners (für die Option 'frischer Klon' aus der Freigabekarte). Der Ordner
    selbst bleibt. Fehler beim Öffnen gilt als nicht leer. Kein Link wird verfolgt."""
    import shutil
    try:
        os.chmod(projektdir, 0o750)
    except OSError:
        pass
    try:
        with os.scandir(projektdir) as it:
            eintraege = list(it)
    except OSError:
        return False
    for e in eintraege:
        try:
            if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                os.unlink(e.path)
            else:
                shutil.rmtree(e.path)
        except OSError:
            return False
    return True


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
    projektdir = PROJEKTE / projekt
    # H1: ein früherer Lauf konnte den Ordner unlesbar machen (chmod 100). Erst wieder lesbar machen; lässt sich
    # das nicht oder ist der Scan nicht möglich, gilt der Ordner NICHT als leer (sonst käme mehr Zugang ohne Freigabe).
    try:
        os.chmod(projektdir, 0o750)
    except OSError:
        pass
    try:
        leer = not any(os.scandir(projektdir))
    except OSError:
        leer = False
    inventur = projekt_inventur(projektdir)
    bisher = set(eintrag["schnitt"]) if isinstance(eintrag, dict) and isinstance(eintrag.get("schnitt"), list) else None
    if leer:
        write_json_atomic(pfad, {"schnitt": sorted(namen), "flaechen": inventur})
        return None
    if bisher is None:  # Projekt mit Inhalt, aber ohne Buchführung (älter, von Hand angelegt): erst prüfen lassen
        bisher = set()
    neu = namen - bisher
    if neu:
        anfrage_input = {"projekt": projekt, "neu": sorted(neu), "bisher": sorted(bisher)}
        fp = waechter().fingerprint(ZUGANG_TOOL, anfrage_input)  # Inventur NICHT im Fingerabdruck (ändert sich je Lauf)
        if fp not in freigaben:
            alte_flaechen = eintrag.get("flaechen") if isinstance(eintrag, dict) else {}
            geaendert = inventur_vergleich(alte_flaechen, inventur)
            return {"id": fp, "grund": "Mehr Zugänge als frühere Aufträge in diesem Projekt: " + ", ".join(sorted(neu)),
                    "tool": ZUGANG_TOOL, "input": anfrage_input,
                    "inventur": geaendert or ["keine geänderten Autostart-Flächen seit dem letzten Lauf"],
                    "hinweis_klon": "Alternative: Projektordner vor dem Start leeren (frischer Klon)."}
        write_json_atomic(pfad, {"schnitt": sorted(namen), "flaechen": inventur})  # geprüft und freigegeben
        return None
    write_json_atomic(pfad, {"schnitt": sorted(bisher & namen), "flaechen": inventur})
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


class FreigabeServer:
    """Vergibt Freigaben atomar außerhalb der Sandbox (M5). Der Wächter in der Sandbox verbindet sich über
    /run/wamo/zustand/freigabe.sock und reserviert im PreToolUse, bestätigt im PostToolUse oder gibt bei einer
    Hook-Sperre zurück. Ein einziger Thread mit Sperre bedient die Anfragen: ein freigegebener Aufruf kann so
    nie öfter laufen als freigegeben, auch nicht bei parallelen Hooks oder manipuliertem Zustand in der Sandbox.
    Die Freigabeliste kommt aus approvals.json (schreibgeschützt in der Sandbox); verbraucht wird nur hier."""

    def __init__(self, sock_path: Path, freigaben: object):
        self.sock_path = str(sock_path)
        self.granted: dict[str, int] = {}
        for fp in freigaben if isinstance(freigaben, list) else []:
            if isinstance(fp, str) and FP_RE.fullmatch(fp):
                self.granted[fp] = self.granted.get(fp, 0) + 1
        self.reserviert: dict[str, int] = {}
        self.benutzt: dict[str, int] = {}
        self._lock = threading.Lock()
        self._srv: socket.socket | None = None

    def start(self) -> None:
        try:
            os.unlink(self.sock_path)
        except FileNotFoundError:
            pass
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.sock_path)
        os.chmod(self.sock_path, 0o660)
        self._srv.listen(16)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            try:
                conn.settimeout(5)
                self._bedienen(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def _bedienen(self, conn: socket.socket) -> None:
        line = conn.makefile("rb").readline(4096)
        ok = False
        try:
            req = json.loads(line)
            op, fp = req.get("op"), req.get("fp")
        except (ValueError, AttributeError):
            op, fp = None, None
        if isinstance(fp, str) and FP_RE.fullmatch(fp):
            with self._lock:
                frei = self.granted.get(fp, 0) - self.benutzt.get(fp, 0) - self.reserviert.get(fp, 0)
                if op == "reserve" and frei > 0:
                    self.reserviert[fp] = self.reserviert.get(fp, 0) + 1
                    ok = True
                elif op == "commit" and self.reserviert.get(fp, 0) > 0:
                    self.reserviert[fp] -= 1
                    self.benutzt[fp] = self.benutzt.get(fp, 0) + 1
                    ok = True
                elif op == "release" and self.reserviert.get(fp, 0) > 0:
                    self.reserviert[fp] -= 1
                    ok = True
        conn.sendall((json.dumps({"ok": ok}) + "\n").encode("utf-8"))

    def stop(self) -> None:
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass


def egress_offen() -> bool:
    """Prüft, ob der Ausgangsfilter steht: ein Verbindungsversuch zu einem privaten Ziel muss scheitern.
    True heißt: der Filter ist weg (z. B. nft neu geladen), der Lauf muss sofort enden (M6)."""
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("10.255.255.254", 9))
        return True  # eine Verbindung dorthin dürfte nie zustande kommen
    except OSError as e:
        return e.errno not in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EACCES, errno.EPERM, errno.ETIMEDOUT)
    finally:
        s.close()


# ---------------------------------------------------------------- Befehle
def cmd_ping() -> int:
    # abnahme_befehl_sha256 ist nur ein Versionsabgleich für den Kern (stimmt seine Kopie mit dieser überein?). Maßgeblich ist die
    # Kopie in /opt/wamo/waechter, die dieser Dispatcher selbst lädt: gegen sie prüft er jeden Aufruf von pruefen erneut.
    geheim = lese_geheimnisse()
    emit({"ok": True, "version": VERSION, "runner": RUNNER.is_file(), "waechter": (WAECHTER / "dev_guard.py").is_file(),
          "github": github_status(), "serverzugang": serverzugang_erlaubt(geheim),
          "kontingent": kontingent_modul() is not None, "pruefen": pruefen_aktiv(geheim),
          "abnahme_befehl_sha256": modul_sha256("abnahme_befehl"), "kanal": KANAL, "kernkanal": kernkanal_ping()})
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
         ("bruecke", ziel(bruecke, 9)), ("lan", ziel(lan, 443)),
         ("github", ziel("github.com", 443, timeout=10))]
print("wamo-netz " + " ".join("%s=%s" % w for w in werte), file=sys.stderr)
"""
NETZ_ERWARTET = {"landlock": "zu", "lo9": "zu", "lo9mptcp": "zu", "netz10": "zu", "netz10mptcp": "zu",
                 "bruecke": "zu", "lan": "zu", "github": "offen"}
# Modus pruefen: kein Netz (eigener Netz-Namensraum), also ist alles zu. Das Internet wird per IP geprüft (ein Name ergäbe
# einen DNS-Fehler statt "zu"), auch GitHub, das im Lauf-Modus offen sein muss. Im leeren Netz-Namensraum ist Loopback an: Port 9
# dort ist "zu" oder "fehler-ECONNREFUSED" (es lauscht nichts); ein Rest-Zugang zum Host zeigte sich an internet und an
# /proc/net/dev (PRUEFEN_PRUEFUNGEN).
NETZPROBE_PRUEFEN = NETZPROBE.replace('("github", ziel("github.com", 443, timeout=10))', '("internet", ziel("1.1.1.1", 443, timeout=10))')
NETZ_ERWARTET_PRUEFEN = {**{k: ("zu",) for k in NETZ_ERWARTET if k != "github"}, "internet": ("zu",),
                         "lo9": ("zu", "fehler-ECONNREFUSED")}
# Zusätzliche Grenzen, die nur im Modus pruefen gelten: nichts von Claude, kein Zustand des Auftrags, keine Token in der Umgebung,
# keine Netzschnittstelle außer lo, Git-Hooks aus
PRUEFEN_PRUEFUNGEN = [
    ("[ -e /srv/wamo/.claude ]", "Claude-Konfiguration sichtbar"),
    ("[ -e /srv/wamo/.local/share/claude ]", "Claude Code sichtbar"),
    ("[ -e /run/wamo/zustand ]", "Zustand des Auftrags sichtbar"),
    ("[ -n \"$(env | grep -E '^(ANTHROPIC_|CLAUDE_|GH_TOKEN|GITHUB_|VERCEL_|SUPABASE_|JIRA_|WAMO_)')\" ]", "Token oder Zugang in der Umgebung"),
    ("[ -n \"$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ' | grep -vx lo)\" ]", "Netzschnittstelle außer lo sichtbar"),
    ("[ \"$(git config --get core.hooksPath)\" != /dev/null ]", "Git-Hooks nicht abgeschaltet"),
]


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


def selbsttest(job: str, projekt: str, modus: str = "lauf") -> tuple[bool, str]:
    """Führt den Hook-Befehl aus managed-settings.json in der Sandbox mit einem Sperrfall aus und prüft
    nebenbei die Grenzen. Bestanden nur, wenn der Wächter wirklich lief und genau mit der erwarteten
    Begründung ablehnt (ein Absturz mit Exit 2 sperrt im Betrieb auch, zählt hier aber nicht).
    modus "pruefen" läuft in der Sandbox des Befehls pruefen (wamo-job.sh --modus pruefen): kein Netz, nichts von Claude, kein
    Zustand, keine Token; erwartet wird dort überall "zu" und es gelten die zusätzlichen Prüfungen in PRUEFEN_PRUEFUNGEN."""
    pruefen = modus == "pruefen"
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
        ("! grep -q '^Seccomp:[[:space:]]*2' /proc/self/status", "seccomp nicht aktiv"),
        ("! grep -q '^NoNewPrivs:[[:space:]]*1' /proc/self/status", "no_new_privs nicht gesetzt"),
        ("! grep -qE '^CapEff:[[:space:]]*0+$' /proc/self/status", "Capabilities nicht leer"),
        ("unshare -U true 2>/dev/null", "Benutzer-Namensraum anlegbar (seccomp/disable-userns wirkt nicht)"),
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
    if pruefen:
        pruefungen += PRUEFEN_PRUEFUNGEN
    # Nur rename(2), nie mv: mv kopiert bei EXDEV und löscht danach die Quelle, im Projektordner also dessen Inhalt
    umbenennen = "umbenennen() { /usr/bin/python3 -I -c 'import os,sys; os.rename(sys.argv[1], sys.argv[2])' \"$1\" \"$2\" 2>/dev/null; }"
    teile = ["f=0", umbenennen] + [f"if {bed}; then echo 'wamo-pruefung: {text}' >&2; f=1; fi" for bed, text in pruefungen]
    # Probe-Lauscher im Bereich, den der Ausgangsfilter auf Loopback zulässt: nur Landlock darf ihn sperren
    probe = probe_lauscher()
    port = probe.getsockname()[1]
    bruecke = docker_bruecke()  # N1: None heißt ip fehlt oder keine Bridge -> Fehler, kein stummes Überspringen
    if bruecke is None:
        probe.close()
        return False, "Docker-Bridge nicht ermittelbar (ip fehlt oder keine Bridge); Netzprobe nicht möglich"
    netzprobe = NETZPROBE_PRUEFEN if pruefen else NETZPROBE
    teile += [f"/usr/bin/python3 -I -c '{netzprobe}' {port} {bruecke} {LAN_PROBE}",
              f"( {hook} ); c=$?; echo \"wamo-selbsttest code=$c fehler=$f\" >&2"]
    argv = [str(RUNNER), "--auftrag", job, "--projekt", projekt, "--zeitlimit", "60", "--einheit-zusatz", "pruefung",
            *(["--modus", "pruefen"] if pruefen else []), "--", "/bin/sh", "-c", "; ".join(teile)]
    try:
        p = subprocess.run(argv, input=json.dumps(SELFTEST_EVENT).encode(), capture_output=True,
                           env=pruefen_env() if pruefen else basis_env(), timeout=120, cwd="/")  # wie der echte Aufruf: Git-Hooks aus
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
    erwartet = NETZ_ERWARTET_PRUEFEN if pruefen else NETZ_ERWARTET
    abweichend = {k: werte.get(k, "?") for k, v in erwartet.items() if werte.get(k) not in ((v,) if isinstance(v, str) else v)}
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


def aufraeumen() -> None:
    """Räumt als wamo alte Auftrags-Caches auf (N5: nicht als root über Sandbox-beschreibbare Ordner).
    npm/cache/plugin-daten nach 7 Tagen, claude/ nach 30 Tagen; Projektordner, Buchführung und Sperren bleiben.
    Best effort; Fehler stören den Lauf nicht. Folgt keinem Link (ein Link als Cache-Ordner wird entfernt)."""
    import shutil
    jetzt = time.time()
    alter = {"npm": 7 * 86400, "cache": 7 * 86400, "plugin-daten": 7 * 86400, "claude": 30 * 86400}
    try:
        jobs = [e.name for e in os.scandir(JOBS) if e.is_dir() and not e.is_symlink() and JOB_RE.fullmatch(e.name)]
    except OSError:
        return
    for job in jobs[:500]:
        if job_laeuft(job):
            continue
        for teil, grenze in alter.items():
            p = JOBS / job / teil
            try:
                st = p.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                p.unlink(missing_ok=True)
                continue
            if stat.S_ISDIR(st.st_mode) and jetzt - st.st_mtime > grenze:
                shutil.rmtree(p, ignore_errors=True)


def _zeile_lesen(limit: int, was: str, fehler=Abgewiesen) -> bytes:
    """Eine Zeile von stdin, höchstens limit Bytes, innerhalb von 30 Sekunden."""
    def zu_spaet(*_):
        raise fehler(f"{was} kam nicht rechtzeitig")
    signal.signal(signal.SIGALRM, zu_spaet)
    signal.alarm(30)
    try:
        return sys.stdin.buffer.readline(limit)
    finally:
        signal.alarm(0)


def _kopfzeile_lesen() -> bytes:
    return _zeile_lesen(MAX_HEADER_BYTES + 1, "Kopfzeile")


def _zugangszeile_lesen() -> bytes:
    return _zeile_lesen(MAX_ZUGANG_BYTES + 1, "Zugangszeile", ZugangUngueltig)


def kontingent_modul():
    """Parser für rate_limit_event (kontingent.py aus /opt/wamo/waechter); None, wenn die Kopie fehlt."""
    try:
        return _modul("kontingent")
    except Exception:  # fehlt oder defekt: ohne Kontingent weiterlaufen, der Auftrag ist wichtiger
        return None


def kontingent_ereignis(modul, raw: bytes) -> dict | None:
    """Ereignis kontingent aus einer Zeile von Claude Code (nur Felder der Allowlist) oder None. Die Zeile kommt aus der
    Sandbox: was nicht passt, wirft nie, sondern ergibt None."""
    try:
        return modul.ereignis_aus_zeile(json.loads(raw))
    except Exception:  # ungültiges JSON, zu tief verschachtelt, absurde Zahlen: ohne Ereignis weiter
        return None


def schwaerzungen(env: dict[str, str]) -> list[tuple[bytes, bytes]]:
    """Geheimnisse dieses Laufs, die in der Ausgabe des Agenten nicht erscheinen sollen: (Wert, Ersatz). Mindestens 8
    Zeichen (kürzere Werte sind keine Schlüssel und würden Text zerstören)."""
    out = []
    for name, ersatz in (("GH_TOKEN", b"[GH_TOKEN]"), ("CLAUDE_CODE_OAUTH_TOKEN", ZUGANG_ERSATZ),
                         ("ANTHROPIC_API_KEY", ZUGANG_ERSATZ)):
        wert = env.get(name, "")
        if len(wert) >= 8:
            out.append((wert.encode("utf-8"), ersatz))
    return out


def schwaerzen(daten: bytes, ersetzungen: list[tuple[bytes, bytes]]) -> bytes:
    """Ersetzt die Werte dieses Laufs wörtlich und danach alles, was wie ein Anthropic-Schlüssel aussieht."""
    for wert, ersatz in ersetzungen:
        daten = daten.replace(wert, ersatz)
    return SK_ANT_RE.sub(ZUGANG_ERSATZ, daten) if b"sk-ant-" in daten else daten


def _datei_schwaerzen(pfad: str, ordner: str, ersetzungen: list[tuple[bytes, bytes]]) -> str:
    """Eine reguläre Datei (ohne Link) schwärzen. Ergebnis: "unveraendert", "geschwaerzt", "entfernt" (nicht prüfbar, zu
    groß oder nicht ersetzbar: fail-closed gelöscht) oder "offen" (weder geschwärzt noch gelöscht)."""
    flags = os.O_RDONLY | O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    tmp = None
    try:
        fd = os.open(pfad, flags)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return "unveraendert"
            if st.st_size > MAX_SCHWAERZ_DATEI:
                raise OSError("zu groß")
            with os.fdopen(fd, "rb", closefd=False) as f:
                daten = f.read()
        finally:
            os.close(fd)
        neu = schwaerzen(daten, ersetzungen)
        if neu == daten:
            return "unveraendert"
        tmp_fd, tmp = tempfile.mkstemp(dir=ordner, prefix=".schwaerzen.")
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(neu)
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.replace(tmp, pfad)
        return "geschwaerzt"
    except OSError:
        # nicht lesbar, zu groß oder nicht ersetzbar: fail-closed, die Datei wird entfernt (Sitzungsdaten sind nur
        # eine Zugabe für --resume, ein Zugangswert darf nicht überleben)
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        try:
            os.unlink(pfad)
        except OSError:
            return "offen"
        return "entfernt"


def zugang_aus_dateien_entfernen(wurzel: Path, ersetzungen: list[tuple[bytes, bytes]]) -> tuple[int, int]:
    """Schwärzt Zugangswerte in allen Dateien unter wurzel (der Konfigordner claude/ des Auftrags mit den Sitzungen
    für --resume): dort landet ein Wert, den der Agent trotz Sperre ausgibt, als Werkzeugergebnis. Läuft nach dem Ende
    der Sandbox, ohne Links zu folgen (nur reguläre Dateien, O_NOFOLLOW), mit Grenzen für Zahl und Zeit. Liefert
    (geändert, offen): offen zählt alles, was nicht geprüft oder nicht bereinigt werden konnte (nicht lesbare Ordner,
    Dateien, die sich weder schwärzen noch löschen ließen, Überschreiten der Grenzen)."""
    if not ersetzungen or wurzel.is_symlink() or not wurzel.is_dir():
        return 0, 0
    geaendert = offen = geprueft = 0
    ende = time.monotonic() + SCHWAERZ_ZEIT_S

    def fehler(_e: OSError) -> None:
        nonlocal offen
        offen += 1
    for ordner, unterordner, dateien in os.walk(wurzel, followlinks=False, onerror=fehler):
        unterordner[:] = [d for d in unterordner if not os.path.islink(os.path.join(ordner, d))]
        for name in dateien:
            geprueft += 1
            if geprueft > MAX_SCHWAERZ_DATEIEN or time.monotonic() > ende:
                return geaendert, offen + 1
            ergebnis = _datei_schwaerzen(os.path.join(ordner, name), ordner, ersetzungen)
            if ergebnis in ("geschwaerzt", "entfernt"):
                geaendert += 1
            elif ergebnis == "offen":
                offen += 1
    return geaendert, offen


def _baum_entfernen(pfad: Path) -> None:
    """Entfernt einen Ordner samt Inhalt, auch wenn die Sandbox Rechte entzogen hat (ohne Links zu folgen)."""
    import shutil

    def frei(funktion, p, _exc):
        try:
            os.chmod(os.path.dirname(p), 0o700)
            os.chmod(p, 0o700)
            funktion(p)
        except OSError:
            pass
    if pfad.is_dir() and not pfad.is_symlink():
        shutil.rmtree(pfad, onerror=frei)


def zugang_aufraeumen(jobdir: Path, job: str, ersetzungen: list[tuple[bytes, bytes]]) -> None:
    """Nach dem Lauf: Zugangswerte aus claude/ schwärzen. Bleibt etwas offen, gehen die Sitzungsdaten (projects/)
    verloren statt den Wert zu behalten; das Audit nennt nur Zahlen. Wirft nie."""
    try:
        geaendert, offen = zugang_aus_dateien_entfernen(jobdir / "claude", ersetzungen)
        if offen:
            _baum_entfernen(jobdir / "claude" / "projects")
            log(f"schwaerzen_unvollstaendig {job}: {offen} offen, Sitzungsdaten entfernt")
        elif geaendert:
            log(f"schwaerzen {job}: {geaendert} Dateien unter claude/")
    except Exception as e:  # noqa: BLE001 (Aufräumen darf den Abschluss des Auftrags nie verhindern)
        log(f"schwaerzen {job} FEHLER {type(e).__name__}")


# ---------------------------------------------------------------- Kern-Kanal (Version 7): Einhängepunkte, die Regeln stehen in wamo_job_kernkanal.py
def kern_modul():
    """Das Kern-Kanal-Modul: in /opt/wamo/waechter installiert, im Repo neben dieser Datei. Es wird erst beim ersten Gebrauch geladen
    (jeder ssh-Aufruf, ping); fehlt es oder ist es defekt, wirft das Laden."""
    geladen = sys.modules.get("wamo_job_kernkanal")
    if geladen is not None:
        return geladen
    if (WAECHTER / "wamo_job_kernkanal.py").is_file():
        return _modul("wamo_job_kernkanal")
    import importlib.util
    spec = importlib.util.spec_from_file_location("wamo_job_kernkanal", Path(__file__).resolve().with_name("wamo_job_kernkanal.py"))
    modul = importlib.util.module_from_spec(spec)
    sys.modules["wamo_job_kernkanal"] = modul
    try:
        spec.loader.exec_module(modul)
    except BaseException:
        sys.modules.pop("wamo_job_kernkanal", None)
        raise
    return modul


def kern_zustand():
    """Notaus-Datei und Konfiguration des Kern-Kanals, jedes Mal frisch gelesen (Regeln 1 und 2)."""
    k = kern_modul()
    return k.kanal_zustand(KERN_PFADE or k.PFADE, lesen=KERN_LESER or k.datei_lesen, bekannte_arten=werkzeugkarte().AUFTRAGSARTEN)


def _kern_vorhanden(pfad) -> bool:
    try:
        os.lstat(pfad)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True  # unlesbar heißt vorhanden
    return True


def kanal_ermitteln() -> str | None:
    """Kanal dieser Verbindung: lokal, pc oder kern; None, wenn die Herkunft im strikten Modus unklar ist. Fehlt das Modul oder ist
    es defekt, gilt ssh ohne Kern-Schlüssel und ohne kernkanal.conf (Stufe 0) weiter als pc, sonst als unklar (fail-closed)."""
    ssh = "SSH_CONNECTION" in os.environ or "SSH_ORIGINAL_COMMAND" in os.environ
    try:
        k = kern_modul()
        uid = KERN_UID if KERN_UID is not None else (os.getuid() if hasattr(os, "getuid") else None)
        return k.kanal_bestimmen(os.environ, KERN_PFADE or k.PFADE, lesen=KERN_LESER or k.datei_lesen, eigene_uid=uid).kanal
    except Exception:  # noqa: BLE001 (Modul fehlt oder ist defekt)
        if not ssh:
            return "lokal"
        pfade = KERN_PFADE
        strikt = _kern_vorhanden(pfade.kern_schluessel if pfade else "/etc/ssh/authorized_keys.d/wamo-kern") \
            or _kern_vorhanden(pfade.konfig if pfade else "/etc/wamo/kernkanal.conf")
        return None if strikt else "pc"


def kernkanal_ping() -> dict:
    """Das Objekt kernkanal der ping-Antwort (I7). Wirft nie; ein Fehler sieht aus wie ein geschlossener, fehlerhafter Kanal."""
    try:
        k = kern_modul()
        pfade = KERN_PFADE or k.PFADE
        return k.kernkanal_bericht(kern_zustand(), k.existiert(pfade.kern_schluessel) or k.existiert(pfade.konfig))
    except Exception:  # noqa: BLE001 (ping muss immer antworten)
        return {"strikt": True, "offen": False, "grund": "konfig_ungueltig", "arten": [], "repos": [], "ablauf": None, "tage_rest": None}


def kanal_abweisen(grund: str) -> int:
    """Ereignis kanal_gesperrt (I6, Grund aus KANAL_GRUENDE) und Exit 2. Nie mit Token, nie mit einem Wert aus geheim.env."""
    log(f"kanal_gesperrt grund={grund}")
    wamo_event("kanal_gesperrt", grund=grund)
    print(f"WAMO-Auftrag abgewiesen: Kanal gesperrt ({grund})", file=sys.stderr)
    return EXIT_ABGEWIESEN


def kern_befehl_pruefen(name: str, args: list[str]) -> str | None:
    """Regeln 1 bis 4 für einen Befehl aus dem Kern-Kanal: None (erlaubt) oder ein Code. In den anderen Kanälen immer None.
    start prüft cmd_start selbst, nach dem Lesen der Kopfzeile."""
    if KANAL != "kern":
        return None
    k = kern_modul()
    grund = k.pruefe_befehl(name, kern_zustand())
    if grund is None and name in ("status", "pending", "approve", "cancel"):
        grund = k.pruefe_eigener_auftrag(read_json(JOBS / args[0] / "auftrag.json", None))
    return grund


def kern_projekt_lage(projekt: str, repo: str | None) -> tuple[bool, bool]:
    """(fremd, kollision) für den Projektordner eines Starts im Kern-Kanal. fremd: der Ordner besteht (oder ein Link steht dort), aber kein Auftrag
    mit kanal=kern hat ihn angelegt (die Auftragsordner sind für die Sandbox unsichtbar, ein Marker im Projektordner wäre fälschbar): der
    Kern-Auftrag liefe sonst in fremdem Inhalt. kollision: ein Kern-Auftrag hat denselben Ordner mit einem anderen Repo belegt (my_repo und
    my-repo ergeben beide kern-my-repo)."""
    belegt: list[str | None] = []
    gefunden = False
    if JOBS.is_dir():
        for meta_pfad in JOBS.glob("*/auftrag.json"):
            meta = read_json(meta_pfad, None)
            if isinstance(meta, dict) and meta.get("kanal") == "kern" and meta.get("projekt") == projekt:
                gefunden = True
                belegt.append(meta.get("repo") if isinstance(meta.get("repo"), str) else None)
    ordner = PROJEKTE / projekt
    fremd = (ordner.is_symlink() or os.path.lexists(ordner)) and (ordner.is_symlink() or not gefunden)
    return fremd, kern_modul().repo_kollision(repo, belegt)


def kern_start_pruefen(job: str, projekt: str, art: str, kopf: Kopf, app_modus: bool, eigener_versuch: bool = False):
    """Regeln 1 bis 7 und 9 für einen Start aus dem Kern-Kanal. Liefert (Code oder None, Konfiguration). Liest keine Geheimnisse:
    app_modus ist vor dem Lesen von geheim.env nicht bekannt (die erste Prüfung nimmt True an, die zweite den echten Wert).
    eigener_versuch: die zweite Prüfung zählt den Versuch dieses Starts, den die erste schon gebucht hat, nicht mit."""
    k = kern_modul()
    pfade = KERN_PFADE or k.PFADE
    zustand = kern_zustand()
    wk = werkzeugkarte()
    meta_pfad = JOBS / job / "auftrag.json"
    projekt_fremd, projekt_kollision = kern_projekt_lage(projekt, kopf.repo)
    anfrage = k.StartAnfrage(
        job=job, projekt=projekt, art=art, repo=kopf.repo, zugang=kopf.zugang, profil=kopf.profil,
        github=wk.github_stufe(art) is not None, dienste=tuple(sorted(set(wk.KARTEN[art].zugaenge) - {"github", "github_lesen"})),
        app_modus=app_modus, fremd=meta_pfad.exists() and k.pruefe_eigener_auftrag(read_json(meta_pfad, None)) is not None,
        projekt_fremd=projekt_fremd, projekt_kollision=projekt_kollision)
    try:
        ereignisse = k.zaehler_lesen(pfade.zaehler)
    except OSError:
        ereignisse = None  # nicht lesbar: die Grenzen sind nicht prüfbar, Regel 9 sperrt
    if eigener_versuch and ereignisse is not None:
        eigene = [i for i, e in enumerate(ereignisse) if e["ereignis"] == "versuch" and e["job"] == job]
        if eigene:
            del ereignisse[eigene[-1]]
    grund = k.pruefe_start(anfrage, zustand, ereignisse=ereignisse, jetzt=time.time(), freier_platz_mb=k.freier_platz_mb(pfade.platz))
    return grund, zustand.konfig


def kern_versuch_buchen(job: str) -> bool:
    """Bucht den Versuch (Ereignis versuch im Zähler), sobald ein Start die erste Prüfung besteht: auch Starts, die später an einer Sperre
    scheitern (belegt, zugang_erweitert, App-Fehler), zählen so gegen die Grenze je Stunde. False, wenn der Zähler nicht schreibbar ist."""
    k = kern_modul()
    try:
        k.zaehler_schreiben((KERN_PFADE or k.PFADE).zaehler, job, "versuch", time.time())
    except OSError:
        return False
    return True


def kern_fork_grund(repo: str | None, token: str) -> str | None:
    """Regel 8: die Metadaten des Repos bei GitHub abfragen (mit dem Token dieses Laufs) und prüfen. Unklar heißt gesperrt."""
    if not repo:
        return "repo_fehlt"
    try:
        status, obj = _github("GET", f"/repos/{repo}", token)
    except GithubAppFehler:
        return "fork"
    return "fork" if status != 200 else kern_modul().fork_pruefen(obj, repo)


@contextmanager
def kern_buchung(job: str):
    """Im Kern-Kanal Start und Ende des Laufs in den Zähler schreiben (kernkanal.jsonl). Lässt sich der Start nicht buchen, startet
    nichts (die Grenzen wären nicht mehr prüfbar). Das Ende wird auch bei einer Ausnahme oder einem Signal gebucht."""
    if KANAL != "kern":
        yield
        return
    k = kern_modul()
    zaehler = (KERN_PFADE or k.PFADE).zaehler
    beginn = time.time()
    k.zaehler_schreiben(zaehler, job, "start", beginn)
    try:
        yield
    finally:
        try:
            k.zaehler_schreiben(zaehler, job, "ende", time.time(), dauer_s=max(0, int(time.time() - beginn)))
        except OSError:
            log(f"kernkanal: Ende von {job} nicht gebucht")


def cmd_start(job: str, projekt: str, art: str, session: str | None = None) -> int:
    kern = KANAL == "kern"
    try:
        kopf = parse_header(_kopfzeile_lesen())
    except Abgewiesen as e:
        if kern and str(e) == WAMO_REPO_GESPERRT:
            return kanal_abweisen("repo")
        raise
    if kern:  # erste Prüfung: vor Geheimnissen, Sperren und Selbsttest
        grund, _konfig = kern_start_pruefen(job, projekt, art, kopf, app_modus=True)
        if grund is not None:
            return kanal_abweisen(grund)
        if not kern_versuch_buchen(job):  # ohne Zähler lassen sich die Grenzen nicht erzwingen
            return kanal_abweisen("grenze_start")
    elif KANAL == "pc" and projekt.startswith(KERN_PRAEFIX):
        raise Abgewiesen("Projektnamen mit kern- sind dem Kern-Kanal vorbehalten")
    auftrag, system, wunsch, repo = kopf.auftrag, kopf.system, kopf.modell, kopf.repo
    try:
        werkzeugkarte().modell_fuer(art, wunsch)  # ein Wunsch außerhalb der Allowlist scheitert vor allem anderen
    except ValueError as e:
        raise Abgewiesen(str(e)) from None
    zugang = None
    if kopf.zugang == "folgt":  # persönlicher Zugang als zweite Zeile, jetzt lesen (vor Sperren und Selbsttest)
        try:
            zugang = parse_zugang(_zugangszeile_lesen(), kopf.profil)
        except ZugangUngueltig as e:
            log(f"zugang_ungueltig {job}: {e}", pflicht=True)
            wamo_event("zugang_ungueltig", grund=str(e)[:200])
            return EXIT_ABGEWIESEN
    aufraeumen()  # N5: alte Caches als wamo entfernen
    sperren = sperren_ordner()
    jobdir = job_ordner(job)
    # Eine Sperre gilt, solange ihr Objekt lebt (offene Datei): deshalb alle bis zum Ende festhalten.
    gehalten = [Sperre(jobdir / ".lauf")]
    if not gehalten[0].versuchen():
        wamo_event("belegt", grund="Dieser Auftrag läuft bereits")
        return EXIT_BELEGT
    vorbereiten(jobdir, job, projekt, art, repo, KANAL)
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
    try:
        app = github_app_konfig(geheim)
    except GithubAppFehler as e:
        return _start_abgelehnt(jobdir, job, projekt, art, str(e))
    if zugang is None and not serverzugang_erlaubt(geheim):
        wamo_event("keine_zugangsdaten", grund="Kein persönlicher Claude-Zugang übergeben, und der Zugang aus geheim.env "
                   "ist abgeschaltet (WAMO_SERVERZUGANG=aus)")
        _status(jobdir, job, projekt, art, ergebnis="keine_zugangsdaten", code=EXIT_KEIN_ZUGANG)
        return EXIT_KEIN_ZUGANG
    env = sandbox_env(art, geheim, app_modus=app is not None, zugang=zugang)
    if env is None:
        wamo_event("keine_zugangsdaten", grund="ANTHROPIC_API_KEY oder CLAUDE_CODE_OAUTH_TOKEN in /etc/wamo/geheim.env fehlt")
        _status(jobdir, job, projekt, art, ergebnis="keine_zugangsdaten", code=EXIT_KEIN_ZUGANG)
        return EXIT_KEIN_ZUGANG
    modell = modell_waehlen(art, wunsch, zugang_typ(env))
    quelle = f"persoenlich-{zugang.typ}" if zugang is not None else "server"
    zugaenge = sorted(zugangs_quellen(art, geheim, app_modus=app is not None, repo=repo, zugang=zugang))
    snapshot = read_json(jobdir / "zustand" / "approvals.json", [])
    anfrage = zugang_pruefen(projekt, set(zugaenge), snapshot if isinstance(snapshot, list) else [])
    if anfrage is not None:
        angefragt_anhaengen(jobdir, [anfrage])
        log(f"zugang_erweitert {job} {projekt}: {', '.join(anfrage['input']['neu'])}", pflicht=True)
        wamo_event("zugang_erweitert", **anfrage)
        _status(jobdir, job, projekt, art, ergebnis="zugang_erweitert", code=EXIT_ZUGANG_ERWEITERT)
        return EXIT_ZUGANG_ERWEITERT
    github = "-" if werkzeugkarte().github_stufe(art) is None else "app" if app is not None else "token"
    if github == "app":
        system = f"{system}\n{github_hinweis(repo)}"
    grenzen = None
    if kern:  # zweite Prüfung unmittelbar vor der Token-Erzeugung: eine Notaus-Datei oder eine neue Konfiguration seit der ersten greift noch
        grund, konfig = kern_start_pruefen(job, projekt, art, kopf, app_modus=app is not None, eigener_versuch=True)
        if grund is not None:
            return kanal_abweisen(grund)
        grenzen = (str(konfig.max_turns), konfig.max_budget_usd)  # nur aus kernkanal.conf, die Kopfzeile kann nichts steuern
    claude = claude_argv(system, modell, session, grenzen) if grenzen else claude_argv(system, modell, session)
    argv = [str(RUNNER), "--auftrag", job, "--projekt", projekt, "--", *claude]
    # Ab hier lebt ggf. ein GitHub-Token: SIGTERM und SIGHUP werden zur Ausnahme, damit Sandbox-Stopp und Widerruf
    # (finally in github_token_fuer_lauf) sicher laufen. SIGKILL lässt sich nicht abfangen: dann läuft das Token
    # nach spätestens einer Stunde ab.
    with _signale(_als_ausnahme), kern_buchung(job):
        try:
            with github_token_fuer_lauf(job, art, repo, app) as gh_token:
                if gh_token is not None:
                    env = sandbox_env(art, geheim, app_modus=True, gh_token=gh_token, zugang=zugang)
                    if kern:  # Regel 8: kein Fork, keine Vorlage, kein Ursprung wamo; das Token wird danach widerrufen
                        grund = kern_fork_grund(repo, gh_token)
                        if grund is not None:
                            return kanal_abweisen(grund)
                log(f"start {job} {projekt} {art} {'fortsetzung' if session else 'neu'} modell={modell} "
                    f"zugaenge={','.join(zugaenge)} github={github} repo={repo or '-'} claude={quelle} "
                    f"profil={kopf.profil or '-'}", pflicht=True)
                code = _ausfuehren(argv, env, auftrag, jobdir, job, projekt, art)
        except GithubAppFehler as e:
            return _start_abgelehnt(jobdir, job, projekt, art, str(e))
    del gehalten  # erst jetzt frei (Prozessende gibt sie ohnehin frei)
    return code


def _start_abgelehnt(jobdir: Path, job: str, projekt: str, art: str, grund: str) -> int:
    """App-Modus kaputt oder Token nicht erzeugbar: nichts startet, kein Rückfall auf den persönlichen Token."""
    grund = f"GitHub-App: {grund}"[:300]
    log(f"start_abgelehnt {job} {projekt} {art}: {grund}", pflicht=True)
    wamo_event("start_abgelehnt", grund=grund)
    _status(jobdir, job, projekt, art, ergebnis="start_abgelehnt", code=EXIT_GITHUB_APP)
    return EXIT_GITHUB_APP


def claude_argv(system: str, modell: str, session: str | None = None, grenzen: tuple[str, str] | None = None) -> list[str]:
    """Aufruf von Claude Code in der Sandbox (auch vom Rauchtest genutzt). grenzen (Wendungen, Kosten in USD) gibt es nur im
    Kern-Kanal und nur aus kernkanal.conf: --max-turns und --max-budget-usd."""
    argv = [str(CLAUDE), "-p", "--output-format", "stream-json", "--verbose", "--permission-mode",
            "bypassPermissions", "--model", modell, "--settings", PLUGIN_SETTINGS, "--append-system-prompt", system]
    if session:
        argv += ["--resume", session]
    if grenzen:
        argv += ["--max-turns", grenzen[0], "--max-budget-usd", grenzen[1]]
    return argv


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
    # Freigaben atomar außerhalb der Sandbox (M5): Socket im Zustand-Ordner, in der Sandbox /run/wamo/zustand/freigabe.sock
    freigabe = FreigabeServer(jobdir / "zustand" / "freigabe.sock",
                              read_json(jobdir / "zustand" / "approvals.json", []))
    freigabe.start()
    gruende: list[str] = []
    gestoppt = threading.Event()
    proc = None  # gesetzt direkt nach dem Start unten; die folgenden Funktionen lesen es erst danach

    def stoppen(grund: str) -> None:
        if gestoppt.is_set():
            return
        gestoppt.set()
        gruende.append(grund)
        stop_units(job)
        if proc is None:  # Signal noch vor dem Start des Prozesses: es gibt nichts zu beenden
            return
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

    def egress_wache() -> None:
        # M6: fällt der Ausgangsfilter aus (nft neu geladen o. Ä.), sofort stoppen
        while not gestoppt.wait(15):
            if proc.poll() is not None:
                return
            if egress_offen():
                log(f"egress_offen {job}: Ausgangsfilter weg, Auftrag gestoppt", pflicht=True)
                stoppen("egress_offen")
                return

    def stderr_sichern() -> None:
        written = 0
        with open(log_pfad, "ab") as f:
            for raw in zeilen(proc.stderr, MAX_ZEILE_STDERR):
                if raw is not None and written < MAX_LOG_BYTES:
                    raw = schwaerzen(raw, ersetzungen)  # ein Zugangswert gehört auch nicht ins Protokoll des Auftrags
                    f.write(raw)
                    f.flush()
                    written += len(raw)

    # GitHub-Token und Claude-Zugang sind Geheimnisse dieses Laufs: erscheinen sie in der Ausgabe des Agenten, werden sie
    # vor dem Weiterreichen an den PC (Canvas, Sprachmodell) geschwärzt, ebenso im Protokoll und nach dem Lauf in den
    # Dateien unter claude/ (Defense in Depth; kodierte Formen fängt das nicht)
    ersetzungen = schwaerzungen(env)
    kontingent = kontingent_modul()
    kontingent_zuletzt: list | None = None
    kontingent_ungesendet: dict | None = None
    kontingent_gesendet = float("-inf")
    verworfen = 0
    # Der try-Block beginnt vor dem Start: ein Signal ab hier stoppt immer auch die Sandbox, selbst wenn es
    # zwischen Popen und dem ersten Befehl danach eintrifft
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, cwd="/", start_new_session=True)
        stderr_thread = threading.Thread(target=stderr_sichern, daemon=True)
        stderr_thread.start()
        threading.Thread(target=lebenslinie, daemon=True).start()
        threading.Thread(target=egress_wache, daemon=True).start()
        try:
            proc.stdin.write(auftrag.encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        wamo_event("gestartet")
        sessions = read_json(jobdir / "sessions.json", [])
        sessions = sessions if isinstance(sessions, list) else []
        zaehler = Freigabezaehler(read_json(jobdir / "zustand" / "approvals.json", []))
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
            ausgabe = raw if raw.endswith(b"\n") else raw + b"\n"
            ausgabe = schwaerzen(ausgabe, ersetzungen)
            if not emit_raw(ausgabe):
                stoppen("verlassen")
                break
            if kontingent is not None and b'"rate_limit_event"' in raw:
                # Limits des Abos: die Zeile kommt aus der Sandbox, also nur Felder der Allowlist und mit Wertebereich;
                # eigene Zeile vom Typ wamo (die der Sandbox werden verworfen). Unveränderte Werte nicht wiederholen,
                # höchstens ein Ereignis alle KONTINGENT_ABSTAND_S; der letzte Stand kommt nach der Schleife nach.
                ereignis = kontingent_ereignis(kontingent, raw)
                if ereignis is not None and ereignis["limits"] != kontingent_zuletzt:
                    kontingent_zuletzt = ereignis["limits"]
                    kontingent_ungesendet = ereignis
                if kontingent_ungesendet is not None and time.monotonic() - kontingent_gesendet >= KONTINGENT_ABSTAND_S:
                    emit(kontingent_ungesendet)
                    kontingent_ungesendet, kontingent_gesendet = None, time.monotonic()
        rc = proc.wait()
        stderr_thread.join(timeout=5)  # das Protokoll des Auftrags ist vollständig, bevor es geprüft wird
        if kontingent_ungesendet is not None:
            emit(kontingent_ungesendet)  # der letzte Stand gehört dazu, auch wenn der Abstand noch nicht um war
        zugang_aufraeumen(jobdir, job, ersetzungen)
    except BaseException:
        # Signal (_Beendet) oder Fehler im Dispatcher: die Sandbox nie unbeobachtet weiterlaufen lassen; der
        # Aufrufer widerruft danach ein GitHub-Token (finally in github_token_fuer_lauf)
        with _signale(signal.SIG_IGN):  # ein zweites SIGTERM darf das Stoppen der Sandbox nicht unterbrechen
            stoppen("intern")
            freigabe.stop()
            zugang_aufraeumen(jobdir, job, ersetzungen)
            try:
                _status(jobdir, job, projekt, art, ende=int(time.time()), ergebnis="abgebrochen", code=None)
            except OSError:
                pass
            log(f"ende {job} intern (Dispatcher unterbrochen, Sandbox gestoppt)")
        raise
    freigabe.stop()
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


# ---------------------------------------------------------------- pruefen: ein Befehl der Abnahme in der Sandbox
PRUEFEN_ZAEHLER = "pruefen.json"  # im Auftragsordner (die Sandbox sieht ihn nicht): {"aufrufe": n}


def pruefen_env() -> dict[str, str]:
    """Umgebung des Läufers im Modus pruefen: die Basisumgebung plus Git-Hooks aus, sonst nichts. Kein sandbox_env: keine
    Token, kein Claude-Zugang, keine Einstellung der Auftragsart. Der Läufer lässt im Modus pruefen ohnehin nur diese Namen durch."""
    env = basis_env()
    env["GIT_CONFIG_COUNT"] = str(len(GIT_OHNE_HOOKS))
    for i, (schluessel, wert) in enumerate(GIT_OHNE_HOOKS):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = schluessel, wert
    return env


def _pruefen_zeile_lesen() -> bytes:
    return _zeile_lesen(pruefen_modul().ZEILE_MAX_BYTES + 1, "argv-Zeile")


def lebenslinie_beobachten(bei_ende) -> threading.Thread:
    """stdin der SSH-Verbindung ist die Lebenslinie: EOF oder ein Lesefehler heißt, der Voice-Core ist weg (Auflegen, Abbruch,
    Netz). Dann ruft der Thread bei_ende genau einmal auf. Daten auf stdin werden gelesen und verworfen."""
    def lesen() -> None:
        try:
            while sys.stdin.buffer.read1(65536):
                pass
        except (OSError, ValueError):
            pass
        bei_ende()
    faden = threading.Thread(target=lesen, daemon=True)
    faden.start()
    return faden


def _pruefen_waechter(argv: list[str], projektdir: Path) -> None:
    """Der Wächter entscheidet wie bei einem Bash-Aufruf des Auftrags. Nur allow läuft: block, Freigabepflicht und jeder Fehler
    heißen nicht ausführen (eine Freigabekarte umgeht die Abnahme nie)."""
    try:
        entscheidung, grund = mit_zeitlimit(lambda: waechter().decide("Bash", {"command": shlex.join(argv)}, projektdir),
                                            DECIDE_TIMEOUT_S)
    except (Exception, _Zeitueberschreitung):
        raise Abgewiesen("Der Wächter konnte den Befehl nicht prüfen, er wurde nicht ausgeführt") from None
    if entscheidung == "block":
        raise Abgewiesen(f"Vom Wächter gesperrt: {str(grund)[:200]}")
    if entscheidung != "allow":
        raise Abgewiesen(f"Der Wächter verlangt eine Freigabe, das läuft nie automatisch: {str(grund)[:200]}")


def _pruefen_sperren(jobdir: Path, projekt: str) -> tuple[list, str | None]:
    """Sperren wie bei start: ein Lauf je Auftrag, ein Auftrag je Projekt, höchstens PARALLEL_MAX gleichzeitig. Liefert
    (gehaltene Sperren, Grund für belegt oder None). Die Sperren gelten, solange die Liste lebt."""
    sperren = sperren_ordner()
    gehalten = [Sperre(jobdir / ".lauf")]
    if not gehalten[0].versuchen():
        return gehalten, "belegt: Dieser Auftrag läuft gerade"
    gehalten.append(Sperre(sperren / f"projekt-{projekt}"))
    if not gehalten[-1].versuchen():
        return gehalten, "belegt: Im Projekt läuft schon ein anderer Auftrag"
    platz = next((s for s in (Sperre(sperren / f"platz-{i}") for i in range(PARALLEL_MAX)) if s.versuchen()), None)
    if platz is None:
        return gehalten, "belegt: Es läuft schon ein Auftrag" if PARALLEL_MAX == 1 else f"belegt: Es laufen schon {PARALLEL_MAX} Aufträge"
    gehalten.append(platz)
    return gehalten, None


def _pruefen_zaehlen(jobdir: Path, aufrufe_max: int) -> None:
    """Höchstens aufrufe_max Prüfungen je Auftrag (die Sandbox ist ein einziger Platz). Ein unlesbarer Zähler sperrt
    (fail-closed); nur der Dispatcher schreibt ihn, die Sandbox sieht den Auftragsordner nicht."""
    pfad = jobdir / PRUEFEN_ZAEHLER
    n: object = 0  # ohne Datei: noch keine Prüfung
    if os.path.lexists(pfad):
        text = read_no_follow(pfad, 4096)
        try:
            daten = json.loads(text) if text is not None else None
        except (ValueError, RecursionError):
            daten = None
        n = daten.get("aufrufe") if isinstance(daten, dict) else None
    if type(n) is not int or n < 0:
        raise Abgewiesen("Der Prüfzähler des Auftrags ist unlesbar")
    if n >= aufrufe_max:
        raise Abgewiesen(f"Höchstens {aufrufe_max} Prüfungen je Auftrag, dieser Auftrag hat sie verbraucht")
    write_json_atomic(jobdir / PRUEFEN_ZAEHLER, {"aufrufe": n + 1})


class PruefenNichtVerfuegbar(RuntimeError):
    """Der Schalter ist an, aber die Prüflogik (wamo_job_pruefen.py, abnahme_befehl.py) fehlt oder ist defekt: ein Fehler der
    Einrichtung, kein Verstoß des Aufrufers (Prozess-Exit 6 statt 2)."""


PRUEFEN_NICHT_VERFUEGBAR = "pruefen ist nicht verfügbar (die Prüflogik fehlt oder ist defekt)"
_LOG_UNSICHTBAR = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def antwort_leer_sicher(grund: object) -> dict:
    """Die Antwort für einen Aufruf, der nichts ausgeführt hat, auch wenn die Prüflogik fehlt oder defekt ist: dann genau dieselben
    Felder aus festen Werten (v, ok false, exit null, abgebrochen false, grund, dauer_s 0.0, ausgabe leer, gekuerzt false). Es kommt
    immer genau eine Zeile; der Kern wertet jede Antwort ohne ok true als nicht erfüllt."""
    try:
        return pruefen_modul().antwort_leer(str(grund))
    except Exception:  # noqa: BLE001 (das Modul fehlt oder ist defekt: die feste Antwort ohne das Modul)
        pass
    text = " ".join("".join(" " if c.isspace() else ("" if unicodedata.category(c) in _LOG_UNSICHTBAR else c)
                            for c in str(grund)).split())
    return {"v": 1, "ok": False, "exit": None, "abgebrochen": False, "grund": text if len(text) <= 300 else text[:299].rstrip() + "…",
            "dauer_s": 0.0, "ausgabe": "", "gekuerzt": False}


def _pruefen_logger(pm, ersetzungen: list[tuple[bytes, bytes]]):
    """Liefert (sauber, plog). sauber(text, grenze) macht freien Inhalt (argv, Fehlertext der Sandbox) zu einer Zeile ohne Steuer-
    und Format-Zeichen, geschwärzt und gekürzt; plog schreibt sie mit Pflicht (ein Schreibfehler bricht den Befehl ab) oder ohne."""
    def sauber(text: object, grenze: int | None = None) -> str:
        return pm.log_text(text, pm.LOG_ZEICHEN if grenze is None else grenze, schwaerzen=lambda daten: schwaerzen(daten, ersetzungen))

    def plog(text: str, pflicht: bool = True) -> None:
        log(sauber(text), pflicht=pflicht)
    return sauber, plog


def _pruefen_aufruf(pm, plog, job: str) -> tuple[list[str], str, Path, Path]:
    """Die stdin-Zeile, der Auftrag, das Projekt und der Wächter. Liefert (argv, Projektname, Auftragsordner, Projektordner);
    Abgewiesen für alles, was nicht laufen darf. Das Projekt kommt nie vom Aufrufer."""
    roh = _pruefen_zeile_lesen()
    try:
        argv = pm.argv_aus_zeile(roh)
    except pm.Abgelehnt as e:
        plog(f"pruefen_abgewiesen {job}: {e} zeile={json.dumps(roh.decode('utf-8', 'replace')[:200])}")
        raise Abgewiesen(str(e)) from None
    jobdir = _no_symlink(JOBS / job)
    meta = read_json(jobdir / "auftrag.json", None)
    if not isinstance(meta, dict):
        raise Abgewiesen("Auftrag unbekannt")
    projekt = check_project(str(meta.get("projekt", "")))
    projektdir = _no_symlink(PROJEKTE / projekt)
    if not projektdir.is_dir():
        raise Abgewiesen("Projektordner fehlt")
    try:
        _pruefen_waechter(argv, projektdir)
    except Abgewiesen as e:
        plog(f"pruefen_abgewiesen {job}: {e} argv={shlex.join(argv)}")
        raise
    return argv, projekt, jobdir, projektdir


def _pruefen_vorweg(pm, plog, job: str, projekt: str, jobdir: Path, projektdir: Path, argv: list[str]) -> tuple[int, dict] | None:
    """Unter den Sperren und vor dem ersten Eingriff: Reste einer früheren Sandbox des Auftrags beenden, nur nach einem Lauf, kein
    cancel, kein Pfad aus dem Projekt hinaus, der Zähler, die Platte, zuletzt das Entschärfen. Liefert None (weiter) oder
    (Exit, Antwort) für einen Aufruf, der nichts ausführt."""
    # Die Sperre hängt am Prozess: Wurde ein früherer Dispatcher dieses Auftrags mit SIGKILL beendet, lief seine Sandbox bis
    # RuntimeMaxSec weiter. Wer .lauf hält, darf alles Alte des Auftrags beenden, bevor er prüft oder entschärft.
    stop_units(job)
    status = read_json(jobdir / "status.json", None)  # unter der Auftragssperre: ein Lauf ist nicht mehr dabei zu schreiben
    if not isinstance(status, dict) or status.get("start") is None:
        raise Abgewiesen("Der Auftrag hatte noch keinen Lauf, pruefen gilt nur nach einem beendeten Lauf")
    if (jobdir / "abbruch").exists():
        return EXIT_ABGEBROCHEN, pm.antwort_leer("abgebrochen", abgebrochen=True)
    if grund := pm.pfad_grund(argv, projektdir):  # ein Link im Projekt darf nie aus dem Projektordner hinausführen
        plog(f"pruefen_abgewiesen {job}: {grund} argv={shlex.join(argv)}")
        raise Abgewiesen(grund)
    _pruefen_zaehlen(jobdir, pm.AUFRUFE_MAX)
    if grund := pm.platten_grund(projektdir, HEIM):  # freier Platz und Projektgröße, vor jedem Aufruf (keine echte Quote)
        plog(f"pruefen_nicht_moeglich {job}: {grund}")
        return EXIT_ABGEWIESEN, pm.antwort_leer(grund)
    for entfernt in projekt_entschaerfen(projektdir):
        plog(f"entschärft {job} {projekt}: {entfernt}", pflicht=False)
    return None


def _pruefen_nachher_entschaerfen(plog, job: str, projekt: str, projektdir: Path) -> None:
    """Nach dem Lauf erneut entschärfen: gepflanzte Hooks und .git/config bleiben so nicht bis zum nächsten start oder pruefen
    liegen. Die Sandbox ist hier beendet. Ein Fehler steht im Audit und verdeckt weder das Ergebnis noch eine laufende Ausnahme
    (jeder nächste Lauf entschärft vor dem Start und scheitert dort fail-closed)."""
    try:
        for entfernt in projekt_entschaerfen(projektdir):
            plog(f"entschärft {job} {projekt} (nach dem Lauf): {entfernt}", pflicht=False)
    except Exception as e:  # noqa: BLE001
        plog(f"entschaerfen_nach_dem_lauf_fehlgeschlagen {job} {projekt}: {type(e).__name__}", pflicht=False)


def _pruefen_ausfuehren(pm, sauber, plog, ersetzungen, job: str, projekt: str, jobdir: Path, projektdir: Path,
                        argv: list[str]) -> tuple[int, dict]:
    """Selbsttest, Lauf und Antwort. cancel kann den Lauf nur beenden, solange eine Einheit läuft: Eine abbruch-Datei, die zwischen
    der letzten Prüfung und dem Start der Einheit entsteht, macht das Ergebnis nach dem Lauf nicht erfüllt (Rest: höchstens die
    120 Sekunden dieses Laufs). cancel wirkt jobweit (abbruch-Datei, alle Einheiten des Auftrags)."""
    lauf = pm.Lauf([str(RUNNER), "--auftrag", job, "--projekt", projekt, "--modus", "pruefen", "--zeitlimit", str(pm.ZEITLIMIT_S),
                    "--einheit-zusatz", "pruefen", "--", *argv], pruefen_env(), lambda: stop_units(job),
                   zeitlimit_s=pm.ZEITLIMIT_S, cwd="/")
    lebenslinie_beobachten(lambda: lauf.stoppen("verlassen"))  # schon während des Selbsttests: Auflegen verhindert den Start
    try:
        with _signale(_als_ausnahme):
            vorher = time.monotonic()
            ok, grund = selbsttest(job, projekt, "pruefen")
            plog(f"selbsttest {job} pruefen {'ok' if ok else 'FEHLER'} dauer={time.monotonic() - vorher:.1f}s: {grund}")
            if lauf.gestoppt:  # Auflegen während des Selbsttests (die Einheit des Selbsttests wurde dabei beendet)
                return EXIT_ABGEBROCHEN, pm.antwort_leer(lauf.grund or "verlassen", abgebrochen=True)
            if (jobdir / "abbruch").exists():  # cancel kam, während der Selbsttest lief (auch dann ist er womöglich gescheitert)
                return EXIT_ABGEBROCHEN, pm.antwort_leer("abgebrochen", abgebrochen=True)
            if not ok:
                return EXIT_SELBSTTEST, pm.antwort_leer(f"selbsttest_fehlgeschlagen: {sauber(grund, pm.GRUND_ZEICHEN)}")
            plog(f"pruefen {job} {projekt} argv={shlex.join(argv)}")
            if (jobdir / "abbruch").exists():  # die letzte Prüfung unmittelbar vor dem Start (cancel, während das Audit geschrieben wurde)
                return EXIT_ABGEBROCHEN, pm.antwort_leer("abgebrochen", abgebrochen=True)
            ergebnis = lauf.ausfuehren()
    except BaseException:
        lauf.stoppen("intern")  # auch ein Signal: die Sandbox nie unbeobachtet weiterlaufen lassen
        raise
    finally:
        stop_units(job)
        _pruefen_nachher_entschaerfen(plog, job, projekt, projektdir)
    if (jobdir / "abbruch").exists() and not ergebnis.abgebrochen:  # cancel beendet die Einheit, der Läufer endet dann "normal"
        ergebnis = replace(ergebnis, rc=None, abgebrochen=True, grund="abgebrochen")
    antwort = pm.antwort(ergebnis, lambda daten: schwaerzen(daten, ersetzungen))  # die Sandbox hat keine Geheimnisse; trotzdem schwärzen
    plog(f"pruefen_ende {job} exit={antwort['exit']} ok={antwort['ok']} abgebrochen={antwort['abgebrochen']} "
         f"grund={antwort['grund'] or '-'} dauer={antwort['dauer_s']}s")
    if antwort["grund"] == "zeitlimit":
        return EXIT_ZEITLIMIT, antwort
    return (EXIT_ABGEBROCHEN if antwort["abgebrochen"] else EXIT_OK), antwort


def _pruefen(job: str) -> tuple[int, dict]:
    """Der ganze Ablauf von pruefen. Liefert (Prozess-Exit, Antwort); Abgewiesen für alles, was nicht lief.

    pruefen führt keine Buchführung der Zugänge (zugang_pruefen): Der Lauf hat keine Zugänge, die Schnittmenge der Token früherer
    Läufe bleibt unberührt. Ein Lauf ohne Zugänge schriebe sie auf leer und löste vor jedem späteren start eine Freigabekarte aus.
    Gepflanzte Autostart-Flächen entfernt das Entschärfen vor und nach jedem Lauf (Hooks, .git/config, .claude, .mcp.json)."""
    pm = pruefen_modul()
    ersetzungen = schwaerzungen(lese_geheimnisse())  # was der Dispatcher kennt, steht nie in Antwort oder Protokoll
    sauber, plog = _pruefen_logger(pm, ersetzungen)
    argv, projekt, jobdir, projektdir = _pruefen_aufruf(pm, plog, job)
    if abnahme_modul().braucht_venv(argv) and not pm.venv_vorhanden(projektdir):
        return EXIT_ABGEWIESEN, pm.antwort_leer(pm.KEIN_VENV)
    gehalten, belegt = _pruefen_sperren(jobdir, projekt)  # die Sperren gelten, bis diese Funktion endet
    if belegt:
        return EXIT_BELEGT, pm.antwort_leer(belegt)
    vorweg = _pruefen_vorweg(pm, plog, job, projekt, jobdir, projektdir, argv)
    if vorweg is not None:
        return vorweg
    ergebnis = _pruefen_ausfuehren(pm, sauber, plog, ersetzungen, job, projekt, jobdir, projektdir, argv)
    del gehalten  # erst jetzt frei (Prozessende gibt sie ohnehin frei)
    return ergebnis


def cmd_pruefen(job: str) -> int:
    """pruefen <job>: siehe Kopf dieser Datei. Genau eine JSON-Zeile auf stdout, auch bei einer Abweisung und auch dann, wenn die
    Prüflogik fehlt oder defekt ist (antwort_leer_sicher)."""
    geheim = lese_geheimnisse()
    if not pruefen_aktiv(geheim):  # als Erstes, vor jedem Lesen und Laden: Schalter UND beide Module
        if not pruefen_schalter(geheim):
            emit(antwort_leer_sicher("pruefen ist abgeschaltet"))
            raise Abgewiesen("pruefen ist abgeschaltet (WAMO_PRUEFEN nicht an)")
        emit(antwort_leer_sicher(PRUEFEN_NICHT_VERFUEGBAR))  # an, aber ohne Modul: ein Fehler der Einrichtung (Exit 6), immer noch fail-closed
        raise PruefenNichtVerfuegbar(PRUEFEN_NICHT_VERFUEGBAR)
    try:
        code, antwort = _pruefen(job)
    except Abgewiesen as e:
        zeile = antwort_leer_sicher(str(e))
        emit(zeile)
        raise Abgewiesen(zeile["grund"]) from None  # bereinigt und gekürzt: auch das Protokoll von main und stderr bekommen nie freien Text
    except Exception as e:  # noqa: BLE001 (ein Fehler im Dispatcher: auch dann genau eine Zeile, main meldet ihn mit Exit 6)
        emit(antwort_leer_sicher(f"interner Fehler ({type(e).__name__})"))
        raise
    emit(antwort)
    return code


HANDLER = {"ping": cmd_ping, "start": cmd_start, "status": cmd_status, "pending": cmd_pending,
           "approve": cmd_approve, "cancel": cmd_cancel, "pruefen": cmd_pruefen}


def speicher_begrenzen() -> None:
    """Weiche Adressraumgrenze für diesen Prozess (Kinder setzen sie in wamo-job.sh wieder hoch) und keine
    Core-Dumps, die Token enthalten könnten (N2)."""
    try:
        import resource
        _, hart = resource.getrlimit(resource.RLIMIT_AS)
        weich = SPEICHER_DISPATCHER if hart == resource.RLIM_INFINITY else min(SPEICHER_DISPATCHER, hart)
        resource.setrlimit(resource.RLIMIT_AS, (weich, hart))
    except (ImportError, ValueError, OSError):
        pass
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE=0
    except (OSError, AttributeError, ValueError, TypeError):
        pass


def main(argv: list[str] | None = None) -> int:
    # Über SSH zählt NUR SSH_ORIGINAL_COMMAND (argv ist durch command="..." fest leer). Ohne SSH (Rauchtest
    # als wamo) gelten die Argumente, mit derselben Prüfung.
    global KANAL
    if "SSH_CONNECTION" in os.environ or "SSH_ORIGINAL_COMMAND" in os.environ:
        raw = os.environ.get("SSH_ORIGINAL_COMMAND")
    else:
        raw = " ".join(sys.argv[1:] if argv is None else argv)
    try:
        KANAL = kanal_ermitteln() or "unklar"  # vor allem anderen: unklare Herkunft im strikten Modus endet hier
        if KANAL == "unklar":
            return kanal_abweisen("herkunft_unklar")
        name, args = parse_command(raw)
        gesperrt = kern_befehl_pruefen(name, args)
        if gesperrt is not None:
            return kanal_abweisen(gesperrt)
        if name == "start":  # Eingang; approve und cancel protokollieren selbst (Pflichtzeile), sonst doppelt
            log(f"{name} {' '.join(args)}")
        return HANDLER[name](*args)
    except Abgewiesen as e:
        log(f"ABGEWIESEN ({e}): {(raw or '')[:80]!r}")
        print(f"WAMO-Auftrag abgewiesen: {e}", file=sys.stderr)
        return EXIT_ABGEWIESEN
    except BrokenPipeError:  # der Voice-Core ist weg; still enden (N2)
        return EXIT_ABGEBROCHEN
    except _Beendet as e:  # SIGTERM/SIGHUP während eines Laufs: Sandbox gestoppt, Token widerrufen
        log(f"beendet durch Signal {e.args[0] if e.args else '?'}")
        return EXIT_ABGEBROCHEN
    except Exception as e:  # nichts durchreichen, aber protokollieren
        log(f"FEHLER {type(e).__name__}: {str(e)[:200]}")
        print(f"WAMO-Auftrag: interner Fehler ({type(e).__name__})", file=sys.stderr)
        return EXIT_INTERN
    finally:
        KANAL = "lokal"  # der Kanal gilt nur für diese Verbindung


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
