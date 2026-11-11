"""Entwicklungsaufträge per Sprache: WAMO gibt Aufträge an Claude Code (ohne Fenster).

Nur für Profile mit der Rolle admin UND nur bei WAMO_ENTWICKLUNG=an (Standard: aus). Sonst bietet
bot.py die Werkzeuge gar nicht an.

Betriebsart (WAMO_ENTWICKLUNG_ORT)
  sokrates2 (Standard): Claude Code läuft auf Sokrates-2 als Benutzer wamo in einer bubblewrap-Sandbox
     (infra/sokrates2/wamo-job.sh). Der Voice-Core spricht per SSH mit eigenem Schlüssel
     (WAMO_ENTWICKLUNG_SSH_SCHLUESSEL, Standard ~/.ssh/id_ed25519_wamo_jobs; Hostschlüssel nur aus
     WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS, Standard ~/.ssh/known_hosts_wamo_jobs) mit dem Dispatcher
     /opt/wamo/bin/wamo-job-dispatch, der als Forced Command nur start, status, pending, approve und
     cancel kennt. Der Auftrag geht als JSON-Zeile über stdin; stdin bleibt danach offen und ist die
     Lebenslinie: Auflegen beendet den SSH-Prozess, damit endet der entfernte Auftrag. Projekte liegen
     unter /srv/wamo/projekte/<projekt> (Klonen erledigt der Auftrag selbst). Wächter, Freigaben und
     Anfragen liegen auf Sokrates-2 im Auftragsordner, approve und pending gehen über denselben Weg.
     Meldet der Dispatcher zugang_erweitert (mehr Token als frühere Läufe im Projekt), wird daraus eine
     Freigabekarte; nach dem Klick startet der Auftrag neu.
     Claude-Zugang je Lauf: Hat das Profil einen eigenen Zugang (Max-Token aus `claude setup-token` oder API-Schlüssel,
     modellzugang.claude_code_zugang_fuer), geht er nach der Kopfzeile als zweite stdin-Zeile an den Dispatcher
     (nie per argv oder Umgebung; bei jedem Lauf neu, auch nach einer Freigabe; der Dispatcher speichert ihn nicht). Ohne
     eigenen Zugang gilt der Zugang aus geheim.env nur für das Profil, das dafür vorgesehen ist
     (WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL, sonst WAMO_CLAUDE_SCHLUESSEL_PROFIL); für alle anderen startet nichts, mit klarer
     Meldung. Das Kontingent des Max-Abos (Ereignis kontingent des Dispatchers, kontingent.py) wird je Profil gespeichert
     und als Servernachricht kontingent an die Oberfläche geschickt.
  lokal: der frühere Weg, Claude Code als Windows-Benutzer auf diesem PC. Nur mit zusätzlich
     WAMO_ENTWICKLUNG_LOKAL_BESTAETIGT=ja, mit deutlicher Warnung im Log; der Wächter ist dort nur eine Bremse.

Ablauf
  1. entwicklung_vorbereiten(auftrag, projekt, art): prüft Projektname, Projekte-Ordner und
     Auftragsart und legt eine Karte in die Oberfläche (RTVI {"type": "auftrag", ...}).
  2. Gestartet wird NUR per Klick in der Oberfläche (Client-Nachricht {"type": "auftrag_start",
     "job", "entscheidung": "ja" | "nein"}). Das Sprachmodell hat kein Start-Werkzeug.
  3. Vor jedem Lauf prüft ein Selbsttest den Wächter (ein bekannter Sperrfall muss abgelehnt
     werden); scheitert er, startet nichts. Claude Code läuft dann im Projektordner unter
     WAMO_PROJECTS_DIR/<projekt> (Standard ~/Projekte). Die Werkzeugkarte (werkzeugkarte.py) sagt ihm,
     welche ECC-Skills, -Agenten und REA-Werkzeuge zur Auftragsart passen. Vor jedem Schritt
     entscheidet dev_guard.py (PreToolUse-Hook): sofort, Freigabe nötig oder gesperrt.
  4. Fortschritt und Ergebnis stehen nur auf dem Canvas. Das Sprachmodell bekommt am Ende eine
     feste, kurze Meldung (Status und Anzahl offener Freigaben), nie den Text von Claude Code.
  5. Freigaben erscheinen als Karten in der UI. Ein Klick schreibt den Fingerabdruck in
     approvals.json und setzt den Auftrag mit --resume fort.

Sicherheit
  * Claude Code wird direkt gestartet (argv, keine Shell); der Auftrag geht über stdin.
  * Der Hook läuft als "... || exit 2": jeder Fehlstart (fehlendes Python, Absturz vor main)
    blockiert. Zweite Schicht: permissions.deny in der Job-Einstellung für die gesperrten Muster.
  * Job-Dateien (approvals.json, pending.jsonl) liegen in voice-core/.jobs. Ihre Pfade bekommt nur
    der Wächter als Argument, nicht die Umgebung. Anfragen aus pending.jsonl werden beim
    Einsammeln neu geprüft (Fingerabdruck und Grund), gefälschte Karten fallen heraus.
  * Liegt WAMO_PROJECTS_DIR im WAMO-Repo oder enthält es das Repo, startet kein Auftrag.
  * Umgebung als Allowlist: Geheimnisse des Voice-Cores erbt Claude Code nicht.
  * Ein Auftrag gleichzeitig (Lock), hartes Zeitlimit JOB_TIMEOUT_SECS; bei Ende, Fehler oder
    Auflegen wird der ganze Prozessbaum beendet.
  * Lokal ist das alles eine Bremse, keine Sandbox: als derselbe Windows-Benutzer bleiben Umwege
    möglich. Echte Trennung bringt die Betriebsart sokrates2 (eigener Benutzer, Sandbox, Landlock).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import LLMMessagesAppendFrame
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.services.llm_service import FunctionCallParams

import abnahme
import abnahme_remote
import entwicklung_betrieb
import kontingent as kt
import pc_host
from dev_agent_regeln import (  # noqa: F401 (alle Namen bleiben über dev_agent erreichbar: bot.py, Tests)
    DENY_RULES, JOB_ENV_NAMES, JOB_ENV_PREFIXES, REMOTE_RULES, RULES_TEMPLATE, job_env, rules_for)
from dev_agent_weg import (  # noqa: F401 (wie oben)
    REMOTE_ARG_RE, REMOTE_ENDE_STATUS, REMOTE_EREIGNIS_STATUS, REMOTE_EXIT_STATUS, SSH_KNOWN_HOSTS_STANDARD, SSH_ZIEL_RE,
    SSH_ZIEL_STANDARD, _lesbar, remote_status, ssh_argv, ssh_known_hosts, ssh_problem, ssh_schluessel, ssh_ziel)
from dev_guard import REPO_ROOT, SELF_REPO, _skript_befehl, decide, fingerprint, write_json_atomic
from entwicklung_betrieb import BueroZulassung
from memory import normalize_uuid, shared_database
from persona import clean_inline
from schluessel import schwaerzen
from teilen import eigene_fassung
from vorgaenge import Vorgaenge
from vorgang_stream import EntwicklungsVorgang, endstatus
from werkzeugkarte import AUFTRAGSARTEN, KARTEN, MODELLE_ERLAUBT, anweisungsblock, art_beschreibung, github_stufe, modell_fuer

try:  # liefert der Modellzugang (Karte "Modellzugang"): {"typ": "oauth" | "api", "wert": str[, "workspace"]} oder None
    from modellzugang import claude_code_zugang_fuer
except ImportError as _fehler:  # noch nicht vorhanden: kein persönlicher Zugang (Tests ersetzen die Funktion)
    logger.warning("modellzugang.claude_code_zugang_fuer fehlt ({}): Aufträge starten nur mit dem Serverzugang des "
                   "vorgesehenen Profils", _fehler.__class__.__name__)

    def claude_code_zugang_fuer(user_id):
        return None

PROJECTS_DIR = Path(os.getenv("WAMO_PROJECTS_DIR") or (Path.home() / "Projekte"))
JOBS_DIR = Path(__file__).resolve().parent / ".jobs"
GUARD = Path(__file__).resolve().parent / "dev_guard.py"
JOB_TIMEOUT_SECS = int(os.getenv("DEV_JOB_TIMEOUT_SECS") or 1800)
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")
MAX_TASK_CHARS = 4000
# Abnahme: ein Auftrag mit prüfbarem Ziel läuft in Runden (arbeiten, prüfen, die offenen Punkte nachbessern)
RUNDEN_STANDARD, RUNDEN_MAX = 3, 5
ZEITLIMIT_STANDARD_MIN, ZEITLIMIT_MAX_MIN = 30, 60  # aktive Arbeitszeit in Minuten, Warten auf Freigaben zählt nicht
MIN_REST_RUNDE_S = 60  # für eine weitere Runde muss mindestens so viel Zeit übrig sein
STATUS_PRUEFT = "prüfe Abnahme"  # Zwischenstatus: der Lauf ist zu Ende, das Urteil über das Ziel steht noch aus (nie "fertig")
STATUS_ABGEBROCHEN_SITZUNG = "abgebrochen (Sitzung beendet)"
ECC_PLUGIN = "ecc@ecc"
SELFTEST_TIMEOUT_SECS = 20
SELFTEST_EVENT = {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}

# ---------- Betriebsart sokrates2 (Dispatcher: infra/sokrates2/wamo_job_dispatch.py) ----------
ORTE = ("sokrates2", "lokal")
REMOTE_PROJECTS_DIR = PurePosixPath("/srv/wamo/projekte")
ZUGANG_TOOL = "WAMO-Zugang"  # Freigabe-Anfrage des Dispatchers: mehr Zugänge als frühere Läufe im Projekt
MAX_OFFENE_FREIGABEN = 10
# Ziel-Repo für das kurzlebige GitHub-Token auf Sokrates-2 (dieselbe Regel wie REPO_RE im Dispatcher)
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
GITHUB_URL_RE = re.compile(r"github\.com[/:]([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})", re.IGNORECASE)
SSH_KURZ_TIMEOUT_SECS = 30
SERVERZUGANG_ENV = "WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL"  # Profil, das den Zugang aus geheim.env nutzen darf
KEIN_ZUGANG_STATUS = "nicht gestartet (kein Claude-Zugang: Max-Token oder API-Schlüssel in der Karte Modellzugang hinterlegen)"
KONTINGENT_SENDEN_SECS = 2.0  # höchstens alle 2 s eine Servernachricht, höchstens alle 60 s ein Schreiben in die Datenbank
KONTINGENT_SPEICHERN_SECS = 60.0
AUTH_FEHLER = ("invalid api key", "invalid x-api-key", "authentication_error", "invalid bearer token",
               "oauth token has expired", "oauth token revoked", "invalid authentication credentials")
REMOTE_PUFFER_SECS = 300  # das harte Zeitlimit gilt auf Sokrates-2; der Voice-Core wartet etwas länger
LOKAL_WARNUNG = (
    "WARNUNG: Entwicklungsaufträge laufen LOKAL (WAMO_ENTWICKLUNG_ORT=lokal, WAMO_ENTWICKLUNG_LOKAL_BESTAETIGT=ja) "
    "als Windows-Benutzer ohne Sandbox. Claude Code darf damit alles, was dieser Benutzer darf; der Wächter ist "
    "nur eine Bremse. Empfohlen: WAMO_ENTWICKLUNG_ORT=sokrates2."
)


def entwicklung_enabled() -> bool:
    """Schalter WAMO_ENTWICKLUNG=an|aus (Standard aus). Im Büro (pc_host.buero) zählt nur WAMO_ENTWICKLUNG_BUERO, der alte
    Schalter wird dort ignoriert (entwicklung_betrieb.py); ein ungültiger Wert heißt aus."""
    if pc_host.buero():
        return entwicklung_betrieb.buero_schalter() == "an"
    return (os.getenv("WAMO_ENTWICKLUNG") or "aus").strip().lower() in ("an", "on", "ja", "1", "true")


def entwicklung_ort() -> str | None:
    """WAMO_ENTWICKLUNG_ORT=sokrates2|lokal, Standard sokrates2. Unbekannte Werte: None (dann keine Aufträge)."""
    ort = (os.getenv("WAMO_ENTWICKLUNG_ORT") or "sokrates2").strip().lower()
    return ort if ort in ORTE else None


def abnahme_befehle_im_betrieb(ort: str | None = None) -> bool:
    """Lässt der Kern in dieser Betriebsart Befehlskriterien zu? Dieselbe Entscheidung wie DevAgent._befehle_moeglich für den
    Standard-Ausführer (nur Betriebsart lokal) plus Zuschaltung (WAMO_ABNAHME_BEFEHLE=ja). Der Prompt (persona.py) und die
    Werkzeugbeschreibung folgen ihr, damit das Modell nie ein Kriterium angeboten bekommt, das der Kern abweist."""
    return (ort or entwicklung_ort() or "sokrates2") == "lokal" and abnahme.befehle_eingeschaltet()


def lokal_bestaetigt() -> bool:
    """Die Betriebsart lokal braucht eine zweite, ausdrückliche Variable."""
    return (os.getenv("WAMO_ENTWICKLUNG_LOKAL_BESTAETIGT") or "").strip().lower() == "ja"


def lokal_problem() -> str | None:
    if not lokal_bestaetigt():
        return "WAMO_ENTWICKLUNG_ORT=lokal braucht zusätzlich WAMO_ENTWICKLUNG_LOKAL_BESTAETIGT=ja"
    return projects_dir_problem()


def modell_wunsch() -> str | None:
    """WAMO_ENTWICKLUNG_MODELL übersteuert das Modell der Auftragsart (nur Namen aus MODELLE_ERLAUBT)."""
    return (os.getenv("WAMO_ENTWICKLUNG_MODELL") or "").strip() or None


def serverzugang_erlaubt(user_id: object) -> bool:
    """Darf dieses Profil ohne eigenen Zugang den Claude-Zugang aus /etc/wamo/geheim.env auf Sokrates-2 nutzen? Nur das
    eine Profil, dem er gehört (WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL, ersatzweise das des Kernschlüssels
    WAMO_CLAUDE_SCHLUESSEL_PROFIL); für alle anderen nie, damit niemand auf fremde Kosten oder fremdes Kontingent arbeitet.
    Im Büro nie: dort zählt nur der eigene Zugang des Profils (kein Rückfall auf die Kernzuordnung)."""
    if pc_host.buero():
        return False
    ziel = normalize_uuid(os.getenv(SERVERZUGANG_ENV) or os.getenv("WAMO_CLAUDE_SCHLUESSEL_PROFIL") or "")
    uid = normalize_uuid(user_id) if user_id else None
    return bool(ziel and uid and ziel == uid)


async def persoenlicher_zugang(user_id: object) -> dict | None:
    """Eigener Claude-Zugang des Profils für Claude Code, frisch für jeden Lauf: {"typ": "oauth" | "api", "wert": str,
    ["workspace": str]} oder None (kein Zugang, ein Fehler beim Lesen oder eine Form, die nicht passt). Der Wert wird nie
    geloggt; bei einem Fehler steht nur die Art der Ausnahme im Log."""
    if not user_id or not normalize_uuid(user_id):
        return None
    try:
        zugang = claude_code_zugang_fuer(user_id)
        if inspect.isawaitable(zugang):
            zugang = await zugang
    except Exception as e:  # noqa: BLE001 (Datenbank, Entschlüsseln: ohne Zugang geht es weiter wie ohne)
        logger.error("Claude-Zugang von Profil {} nicht lesbar ({})", str(user_id)[:8], type(e).__name__)
        return None
    if not isinstance(zugang, dict) or zugang.get("typ") not in ("oauth", "api") \
            or not isinstance(zugang.get("wert"), str) or not zugang["wert"]:
        return None
    out = {"typ": zugang["typ"], "wert": zugang["wert"]}
    if isinstance(zugang.get("workspace"), str) and zugang["workspace"]:
        out["workspace"] = zugang["workspace"]
    return out


def _kontingent_db():
    """Datenbank für das Kontingent (Tabelle model_access); None, wenn sie nicht konfiguriert ist. Tests ersetzen das."""
    try:
        db = shared_database()
    except ValueError:
        return None
    return db if db.configured else None


def entwicklung_problem() -> str | None:
    """Grund, warum Entwicklungsaufträge in der gewählten Betriebsart nicht gehen, oder None."""
    ort = entwicklung_ort()
    if ort is None:
        return "WAMO_ENTWICKLUNG_ORT ist ungültig (erlaubt: sokrates2, lokal)"
    wunsch = modell_wunsch()
    if wunsch and wunsch not in MODELLE_ERLAUBT:
        return f"WAMO_ENTWICKLUNG_MODELL ist ungültig (erlaubt: {', '.join(MODELLE_ERLAUBT)})"
    if pc_host.buero():  # Büro: Schalter, Ort, Profilbindung, Kostengrenzen und ssh_problem (entwicklung_betrieb.py)
        return entwicklung_betrieb.buero_problem(ssh_problem)
    return lokal_problem() if ort == "lokal" else ssh_problem()


def projects_dir_problem(projects_dir: Path | None = None) -> str | None:
    """Grund, warum der Projekte-Ordner unzulässig ist, oder None. Er darf das WAMO-Repo weder
    enthalten (auch nicht als dessen Elternordner) noch selbst im Repo liegen."""
    try:
        p = (projects_dir or PROJECTS_DIR).resolve()
    except (OSError, RuntimeError):
        return "WAMO_PROJECTS_DIR lässt sich nicht auflösen"
    if REPO_ROOT.is_relative_to(p):
        return "WAMO_PROJECTS_DIR enthält das WAMO-Repo"
    if p.is_relative_to(REPO_ROOT):
        return "WAMO_PROJECTS_DIR liegt im WAMO-Repo"
    return None


def auftrag_bereinigen(roh: object) -> str:
    """Der Auftragstext genau so, wie die Karte ihn zeigt und Claude Code ihn bekommt: je Zeile ohne Steuer-, Format-, Tag-,
    Bidi-, Nullbreiten-, private und nicht vergebene Zeichen (sie werden zu Leerzeichen), Leerraum zusammengefasst, die
    Zeilenumbrüche des Auftrags bleiben, höchstens MAX_TASK_CHARS Zeichen. Nur so kann in einem Auftrag keine Anweisung stehen, die die
    Person auf der Karte nicht sieht (Unicode-Tag-Zeichen U+E0000 bis U+E007F, U+200B bis U+200F, U+202A bis U+202E, U+2066 bis
    U+2069, U+FEFF und alles in den Kategorien Cc, Cf, Cs, Co, Cn, Zl, Zp)."""
    text = str(roh if roh is not None else "")[:MAX_TASK_CHARS * 4]  # die Arbeit bleibt begrenzt, auch bei riesigen Eingaben
    return "\n".join(clean_inline(zeile, MAX_TASK_CHARS) for zeile in text.splitlines())[:MAX_TASK_CHARS].strip()


def repo_pruefen(value: object) -> str:
    """Ziel-Repo owner/name (wie check_repo im Dispatcher); ValueError bei allem anderen."""
    if not isinstance(value, str):
        raise ValueError("Repo fehlt")
    repo = value.strip()
    repo = re.sub(r"^(?:https?://)?github\.com/", "", repo, count=1, flags=re.IGNORECASE)
    if repo != value.strip():
        repo = repo.rstrip("/")
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    name = repo.split("/", 1)[-1]
    if len(repo) > 140 or not REPO_RE.fullmatch(repo) or repo.count("/") != 1 or name in (".", ".."):
        raise ValueError("Repo: Form owner/name, nur A-Z, a-z, 0-9, Punkt, Unterstrich und Bindestrich")
    if name.lower() == SELF_REPO:
        raise ValueError("Das WAMO-Repo selbst ist für Aufträge gesperrt")
    return repo


def repo_aus_auftrag(auftrag: str) -> str | None:
    """Ziel-Repo aus einer GitHub-Adresse im Auftragstext (github.com/owner/name). Keine Adresse: None, dann gilt
    das Token für die Installation mit den Rechten der Art. Nennt der Auftrag mehrere verschiedene Repos, ValueError:
    der Auftragstext ist nicht vertrauenswürdig, eine zweite Adresse darf den Zugriff nicht stillschweigend auf die
    ganze Installation ausweiten; das Sprachmodell soll das Repo ausdrücklich angeben."""
    gefunden: dict[str, str] = {}
    for owner, name in GITHUB_URL_RE.findall(auftrag):
        name = name.rstrip(".,;:!?)")
        if name.lower().endswith(".git"):
            name = name[:-4]
        if name.lower() == SELF_REPO:  # nie stillschweigend übergehen: sonst bekäme der Auftrag ein Token ohne Repo
            raise ValueError("Der Auftrag nennt das WAMO-Repo, das für Aufträge gesperrt ist; bitte das Repo, "
                             "an dem gearbeitet wird, ausdrücklich im Feld repo angeben")
        try:
            repo = repo_pruefen(f"{owner}/{name}")
        except ValueError:
            continue
        gefunden.setdefault(repo.lower(), repo)
    if len(gefunden) > 1:
        raise ValueError("Der Auftrag nennt mehrere GitHub-Repos; bitte das Repo, an dem gearbeitet wird, "
                         "ausdrücklich im Feld repo angeben")
    return next(iter(gefunden.values())) if gefunden else None


def claude_exe() -> str:
    """Pfad zu Claude Code, plattformneutral (Windows: nur .exe, nie .cmd über eine Shell)."""
    if sys.platform == "win32":
        native = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        if native.is_file():
            return str(native)
        candidates = ("claude.exe",)
    else:
        candidates = ("claude",)
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError(f"Claude Code nicht gefunden ({', '.join(candidates)}); ist es installiert?")


def project_slug(name: object) -> str:
    if not isinstance(name, str):
        raise ValueError("Projektname fehlt")
    slug = name.strip().lower().replace(" ", "-").replace("_", "-")
    if not PROJECT_NAME.fullmatch(slug) or "--" in slug:
        raise ValueError("Projektname: 3 bis 50 Zeichen, nur a-z, 0-9 und Bindestrich")
    if slug == SELF_REPO:
        raise ValueError("Das WAMO-Repo selbst ist für Aufträge gesperrt")
    return slug


def validate_project(name: object, ort: str | None = None) -> Path | PurePosixPath:
    """Projektordner zum Namen: auf Sokrates-2 /srv/wamo/projekte/<name>, lokal unter WAMO_PROJECTS_DIR."""
    slug = project_slug(name)
    if (ort or entwicklung_ort()) == "sokrates2":
        return REMOTE_PROJECTS_DIR / slug
    problem = projects_dir_problem()
    if problem:
        raise ValueError(problem)
    path = (PROJECTS_DIR / slug).resolve()
    if path.parent != PROJECTS_DIR.resolve():
        raise ValueError("Projekt muss direkt im Projekte-Ordner liegen")
    return path


def hook_command(project: Path, job_dir: Path, nachher: bool = False) -> str:
    """Befehl für den PreToolUse-Hook. "|| exit 2" setzt jeden Fehlstart (Python fehlt, Absturz vor
    main, Exit 1) in eine Blockade um; das gilt in bash wie in cmd. Pfade mit Schrägstrichen.
    nachher=True: derselbe Wächter als PostToolUse-Hook, er verbucht ausgeführte Freigaben."""
    parts = [Path(sys.executable).as_posix(), GUARD.as_posix(), project.as_posix(), job_dir.as_posix()]
    for p in parts:
        if any(c in p for c in '"`$%!\n\r'):
            raise ValueError(f"Pfad mit Sonderzeichen für den Wächter: {p}")
    python, guard, proj, job = parts
    if nachher:
        return f'"{python}" "{guard}" --nachher --project-dir "{proj}" --job-dir "{job}"'
    return f'"{python}" "{guard}" --project-dir "{proj}" --job-dir "{job}" || exit 2'


def job_settings(project: Path, job_dir: Path) -> dict:
    nachher = [{"matcher": "*", "hooks": [{"type": "command", "command": hook_command(project, job_dir, nachher=True)}]}]
    return {
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": hook_command(project, job_dir)}]}],
                  "PostToolUse": nachher, "PostToolUseFailure": nachher},
        "disableAllHooks": False,
        "permissions": {"deny": list(DENY_RULES)},
        "enabledPlugins": {ECC_PLUGIN: True},
    }


async def guard_selftest(project: Path, job_dir: Path) -> bool:
    """Startet den Hook genau wie Claude Code (über die Shell, mit Wrapper) mit einem bekannten
    Sperrfall. Nur wenn er ablehnt (deny oder Exit 2), darf der Auftrag starten."""
    try:
        proc = await asyncio.create_subprocess_shell(
            hook_command(project, job_dir), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=job_env(),
        )
        out, _ = await asyncio.wait_for(proc.communicate(json.dumps(SELFTEST_EVENT).encode()), SELFTEST_TIMEOUT_SECS)
    except (OSError, asyncio.TimeoutError, ValueError) as e:
        logger.error("Wächter-Selbsttest nicht ausführbar ({})", type(e).__name__)
        return False
    if proc.returncode == 2:
        return True
    try:
        decision = json.loads(out.decode("utf-8", "replace"))["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError, TypeError):
        decision = None
    if proc.returncode == 0 and decision == "deny":
        return True
    logger.error("Wächter-Selbsttest fehlgeschlagen (Exit {}, Entscheidung {})", proc.returncode, decision)
    return False


def valid_pending(req: object, project: Path) -> dict | None:
    """Prüft eine Zeile aus pending.jsonl. Die Datei liegt außerhalb des Projekts, könnte aber
    trotzdem manipuliert sein: Fingerabdruck und Grund werden deshalb neu berechnet."""
    if not isinstance(req, dict):
        return None
    tool, tool_input = req.get("tool"), req.get("input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return None
    decision, why = decide(tool, tool_input, project)
    if decision != "approval" or req.get("id") != fingerprint(tool, tool_input):
        return None
    return {"id": req["id"], "grund": why, "tool": tool, "input": tool_input}


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Beendet Claude Code samt Kindprozessen (Dev-Server, Shells)."""
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/T", "/F", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(killer.wait(), 10)
        else:
            os.killpg(proc.pid, 9)
    except (OSError, asyncio.TimeoutError, ProcessLookupError):
        pass
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), 10)


_DISPATCHER_PING: abnahme_remote.DispatcherPing | None = None
BEFEHLE_AUS_HINWEIS = "Befehlskriterien (befehl:) sind in dieser Betriebsart nicht zugeschaltet (Schalter aus)"


REPO_PFLICHT_BESCHREIBUNG = (
    "Pflicht für jede Auftragsart außer recherche: das GitHub-Repo, an dem gearbeitet wird, als owner/name, z. B. "
    "acme/rezepte-app. Begrenzt den GitHub-Zugang des Auftrags auf dieses Repo, das Projekt leitet der Kern daraus ab. "
    "Fehlt es, frag einmal kurz danach."
)


def _befehle_an() -> bool:
    """Ist der Schalter für Befehlskriterien an? WAMO_ABNAHME_BEFEHLE, im Büro zusätzlich WAMO_ENTWICKLUNG_BEFEHLE_BUERO=an
    (Standard aus): so überlebt der Zustand jede Übertragung der core.env, und ein Büro-Kern führt keine Befehle ohne eigenes Ja aus."""
    return abnahme.befehle_eingeschaltet() and (not pc_host.buero() or entwicklung_betrieb.befehle_buero_an())


def _dispatcher_ping() -> abnahme_remote.DispatcherPing:
    """Der Ping an den Dispatcher auf Sokrates-2 (pruefen möglich?), einer je Prozess: Der Cache gilt über Gespräche hinweg. ssh_argv und
    _kill_tree werden erst beim Aufruf aufgelöst (Tests ersetzen sie)."""
    global _DISPATCHER_PING
    if _DISPATCHER_PING is None:
        _DISPATCHER_PING = abnahme_remote.DispatcherPing(lambda args: ssh_argv(args), beenden=lambda proc: _kill_tree(proc))
    return _DISPATCHER_PING


@dataclass
class Job:
    id: str
    auftrag: str
    projekt: Path
    art: str
    dir: Path = field(init=False)
    session_id: str | None = None
    steps: list[str] = field(default_factory=list)
    result: str = ""
    cost_usd: float = 0.0
    status: str = "vorbereitet"
    started: bool = False
    quittiert: bool = False  # die Quittung (auftrag_quittung) für den letzten Klick auf Starten ist gesendet
    aktion_id: str | None = None  # die offene Audit-Zeile in actions (nur im Büro, entwicklung_betrieb.AuftragsBuch)
    kosten_gemeldet: bool = False  # der letzte Lauf hat verwertbare Kosten gemeldet (result mit gültigem total_cost_usd)
    kosten_geschaetzt: bool = False  # cost_usd enthält die Untergrenze, weil ein gestarteter Lauf keine Kosten gemeldet hat (Büro)
    pending: dict[str, dict] = field(default_factory=dict)
    ereignisse: dict[str, dict] = field(default_factory=dict)  # wamo-Ereignisse des Dispatchers (sokrates2)
    repo: str | None = None  # Ziel-Repo owner/name: begrenzt das GitHub-Token auf Sokrates-2 auf dieses Repo
    gespeichert_id: str | None = None  # Ergebnis als Canvas-Dokument (teilbar, Migration 0003)
    fuehrung: EntwicklungsVorgang | None = None  # die Anzeige unter Vorgänge (vorgang_stream.py); None ohne Registry
    # Abnahme (abnahme.py): ohne kriterien bleibt es bei einer Runde ohne Prüfung
    kriterien: list[abnahme.Kriterium] = field(default_factory=list)
    max_runden: int = 1
    zeitlimit_s: int = JOB_TIMEOUT_SECS  # aktive Arbeitszeit aller Runden; Warten auf eine Freigabe zählt nicht
    runde: int = 1
    aktiv_s: float = 0.0  # verbrauchte Arbeitszeit der beendeten Läufe
    lauf_beginn: float = 0.0
    lauf_ok: bool = False  # der Claude-Lauf dieser Runde endete ohne Fehler (unabhängig vom Statustext, der später das Urteil trägt)
    lauf_gemeldet: bool = False  # der Vorgang hat das Ende dieses Laufs schon bekommen (sonst meldet _run einen Fehler)
    weiter: str | None = None  # Prompt der nächsten Runde, solange die Schleife weiterläuft
    neue_runde: bool = False  # der nächste Lauf ist eine Nachbesserungsrunde (nur für den Schritt im Verlauf)
    beendet: bool = False  # die Person hat abgelehnt: keine weiteren Runden
    ergebnisse: list[abnahme.Ergebnis] = field(default_factory=list)  # Stand der letzten Abnahme
    dateien: dict[str, None] = field(default_factory=dict)  # mit Write und Edit geänderte Dateien, relativ zum Projekt

    def __post_init__(self):
        self.dir = JOBS_DIR / self.id


class DevAgent:
    """Ein Objekt pro Gespräch. Braucht den PipelineTask, um Karten, Canvas und Meldungen zu senden.
    vorgaenge: Registry der Sitzung (vorgaenge.py); jeder Auftrag erscheint dort als Vorgang.
    beurteiler: beurteilt Aussagekriterien der Abnahme (abnahme.llm_beurteiler); ohne ihn gibt es nur Befehlskriterien.
    befehl_ausfuehrer: führt Befehlskriterien aus und ersetzt dann jeden Standard (Tests). Standard in der Betriebsart lokal:
    abnahme.befehl_ausfuehren. Auf Sokrates-2 darf der Kern die Sandbox nie betreten: Dort läuft ein Befehl über den Dispatcher-
    Befehl pruefen (abnahme_remote.RemoteAusfuehrer, je Auftrag). Beides gilt nur mit WAMO_ABNAHME_BEFEHLE=ja (Standard aus) und auf
    Sokrates-2 nur, wenn ein gecachter ping zeigt, dass der Dispatcher pruefen bietet und dieselbe Allowlist hat; sonst wird
    befehl: schon beim Vorbereiten abgewiesen, mit dem Grund in einem Satz.
    dispatcher_ping: der Ping samt Cache (abnahme_remote.DispatcherPing); Standard ist einer je Prozess.
    zulassung: Nachweis für das Büro (entwicklung_betrieb.BueroZulassung, nur bot.build_session erzeugt ihn aus der geprüften
    Identität). Im Büro (pc_host.buero) wirft der Konstruktor ohne gültige Zulassung, _vorbereiten, _auftrag_start und _freigabe
    prüfen sie erneut. Außerhalb des Büros hat der Wert keine Wirkung."""

    def __init__(self, memory, auftraggeber: str, ort: str | None = None, vorgaenge: Vorgaenge | None = None,
                 beurteiler: abnahme.Beurteiler | None = None, befehl_ausfuehrer: abnahme.Ausfuehrer | None = None,
                 dispatcher_ping: abnahme_remote.DispatcherPing | None = None, zulassung: BueroZulassung | None = None):
        if pc_host.buero():
            nutzer = getattr(memory, "user_id", None)
            if not isinstance(zulassung, BueroZulassung) or (nutzer and not entwicklung_betrieb.zulassung_gueltig(zulassung, nutzer)):
                raise entwicklung_betrieb.ZulassungFehlt("Entwicklungsaufträge im Büro brauchen eine gültige Zulassung")
        self._zulassung = zulassung
        # Büro: Audit und Kostengrenzen über actions (E9); sonst keine Buchführung, wie bisher
        self._buch = entwicklung_betrieb.AuftragsBuch(memory, zulassung) if pc_host.buero() else None
        self._memory = memory
        self._vorgaenge = vorgaenge
        self._auftraggeber = clean_inline(auftraggeber, 60) or "Admin"
        self._ort = ort or entwicklung_ort() or "sokrates2"
        if pc_host.buero() and self._ort != "sokrates2":
            raise ValueError("Im Büro gibt es nur die Betriebsart sokrates2")
        if self._ort == "lokal":
            logger.warning(LOKAL_WARNUNG)
        self._beurteiler = beurteiler
        self._ausfuehrer = befehl_ausfuehrer or (abnahme.befehl_ausfuehren if self._ort == "lokal" else None)
        self._ping = dispatcher_ping or _dispatcher_ping()  # nur auf Sokrates-2 ohne eingesetzten Ausführer gefragt
        self._ping_task: asyncio.Task | None = None  # das Aufwärmen des Pings beim attach (Hintergrund, nie im Audiopfad)
        self._zu = False  # Sitzungsende: keine weitere Runde
        self._jetzt = time.monotonic  # die Uhr der Schleife (Tests ersetzen sie)
        self._running_job: Job | None = None
        self._task = None
        self._jobs: dict[str, Job] = {}
        self._running: asyncio.Task | None = None
        self._run_lock = asyncio.Lock()  # nie zwei Claude-Code-Läufe gleichzeitig
        self._proc: asyncio.subprocess.Process | None = None
        self.on_result = None  # optional: Rückruf, wenn ein Ergebnis eintrifft (z. B. für M4 im Bot)
        # Kontingent des Max-Abos dieses Profils (kontingent.py); None = noch nicht aus der Datenbank geladen
        self._kontingent: list[dict] = []
        self._kontingent_stand: str | None = None
        self._kontingent_geladen = False  # der gespeicherte Stand ist eingelesen (sonst wird nichts gespeichert)
        self._kontingent_lock = asyncio.Lock()
        self._kontingent_offen = False  # neue Beobachtung, noch nicht gespeichert
        self._kontingent_ungesendet = False  # neue Beobachtung, noch nicht an die Oberfläche geschickt
        self._kontingent_gesendet = float("-inf")
        self._kontingent_gespeichert = float("-inf")
        self._eigener_zugang = False  # der laufende Lauf nutzt den Zugang des Profils (nur dann gilt sein Kontingent)
        self._startet = False  # ein Start (Prüfungen, Buchung in actions) läuft gerade: kein zweiter Start dazwischen

    def attach(self, pipeline_task) -> None:
        self._task = pipeline_task
        self._ping_aufwaermen()

    # ---------- Befehlskriterien auf Sokrates-2: ping und Ausführer ----------
    def _remote_befehle(self) -> bool:
        """Läuft ein befehl: hier über den Dispatcher (Sokrates-2, kein eingesetzter Ausführer) und ist der Schalter an? Nur dann
        wird der Dispatcher gefragt."""
        return self._ort == "sokrates2" and self._ausfuehrer is None and _befehle_an()

    def _ping_aufwaermen(self) -> None:
        """Holt den Ping im Hintergrund, damit die Beschreibung des Werkzeugs im nächsten Gespräch weiß, ob der Dispatcher pruefen
        kann (sie wird beim Anlegen des Gesprächs gebaut, nicht erst beim Aufruf). Wartet nie darauf; ohne laufende Ereignisschleife
        passiert nichts."""
        if self._ping_task is not None or not self._remote_befehle():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ping_task = loop.create_task(self._ping.auffrischen())

    async def _ping_vor_vorbereiten(self, abnahme_roh: object) -> None:
        """Vor dem Parsen: Steht ein befehl: im Ziel und läuft er über den Dispatcher, wird der Ping aufgefrischt (aus dem Cache, wenn
        er jünger als 5 Minuten ist, sonst mit Frist von 5 Sekunden). Wirft nie: Ein Fehler heißt, dass befehl: nicht möglich ist."""
        if not self._remote_befehle() or not abnahme.enthaelt_befehl(abnahme_roh):
            return
        try:
            await self._ping.auffrischen()
        except Exception as e:  # noqa: BLE001 (auffrischen wirft selbst nie; ein Abbruch ist keine Exception)
            logger.warning("Abnahme: ping an den Dispatcher nicht möglich ({})", type(e).__name__)

    # ---------- Vorgänge (vorgaenge.py): reine Anzeige, ein Fehler darin hält den Auftrag nie auf ----------
    def _vorgang_anlegen(self, job: Job) -> None:
        if self._vorgaenge is None:
            return
        try:
            job.fuehrung = EntwicklungsVorgang(self._vorgaenge, f"{KARTEN[job.art].titel}: {job.projekt.name}", job.id,
                                               abnahme=[k.text for k in job.kriterien], max_runden=job.max_runden)
        except Exception as e:  # noqa: BLE001
            logger.warning("Entwicklungsauftrag {}: Vorgang nicht angelegt ({})", job.id, type(e).__name__)

    def _fuehrung(self, job: Job, aktion: str, *args, **kwargs) -> None:
        if job.fuehrung is None:
            return
        try:
            getattr(job.fuehrung, aktion)(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            logger.warning("Entwicklungsauftrag {}: Vorgang nicht gemeldet ({})", job.id, type(e).__name__)

    # ---------- Werkzeuge für das Sprachmodell ----------
    def schemas(self) -> list[FunctionSchema]:
        name = self._auftraggeber
        return [
            FunctionSchema(
                name="entwicklung_vorbereiten",
                description=(
                    "Bereitet einen Software-Auftrag für Claude Code vor (planen, bauen, Fehler beheben, "
                    "prüfen, testen, analysieren ...) und legt ihn als Karte in die Oberfläche. "
                    f"{name} startet ihn dort per Klick; du kannst Aufträge nicht selbst starten. "
                    "Mit abnahme bekommt der Auftrag ein prüfbares Ziel und läuft in Runden, bis es erreicht ist."
                ),
                properties={
                    "auftrag": {"type": "string", "description": f"Vollständiger Auftrag in den Worten von {name}, präzisiert"},
                    "projekt": {"type": "string",
                                "description": ("Ordner-/Repo-Name, z. B. rezepte-app (a-z, 0-9, -). Nennt der Auftrag keinen "
                                                "Namen, leite ihn aus dem Auftrag ab und frag nie danach.")},
                    "repo": {
                        "type": "string",
                        "description": (REPO_PFLICHT_BESCHREIBUNG if pc_host.buero() else
                                        "Nur wenn der Auftrag ein bestehendes GitHub-Repo nennt: owner/name, z. B. "
                                        "wamocon/rezepte-app. Begrenzt den GitHub-Zugang des Auftrags auf dieses Repo. "
                                        "Sonst weglassen."),
                    },
                    "art": {
                        "type": "string",
                        "enum": list(AUFTRAGSARTEN),
                        "description": "Auftragsart, bestimmt die Werkzeuge von Claude Code. " + art_beschreibung(),
                    },
                    "abnahme": {"type": "array", "items": {"type": "string"},
                                "description": self._abnahme_beschreibung()},
                    "max_runden": {
                        "type": "integer",
                        "description": (f"Nur mit abnahme: höchstens so viele Runden (arbeiten, prüfen, nachbessern). Standard "
                                        f"{RUNDEN_STANDARD}, höchstens {RUNDEN_MAX}. Meist weglassen."),
                    },
                    "zeitlimit_min": {
                        "type": "integer",
                        "description": (f"Nur mit abnahme: höchstens so viele Minuten Arbeitszeit über alle Runden (Warten auf "
                                        f"Freigaben zählt nicht). Standard {ZEITLIMIT_STANDARD_MIN}, höchstens "
                                        f"{ZEITLIMIT_MAX_MIN}. Meist weglassen."),
                    },
                },
                required=["auftrag", "projekt", "art"],
            ),
            FunctionSchema(
                name="entwicklung_status",
                description="Kurzer Stand des laufenden oder letzten Entwicklungsauftrags.",
                properties={},
                required=[],
            ),
        ]

    def _befehle_moeglich(self) -> bool:
        """Befehlskriterien gibt es nur mit bewusster Zuschaltung (WAMO_ABNAHME_BEFEHLE=ja, Standard aus) UND einem Weg, sie
        auszuführen: einem Ausführer (Betriebsart lokal, oder eingesetzt) oder, auf Sokrates-2, einem frischen ping (höchstens
        5 Minuten alt), der pruefen mit derselben Allowlist meldet. Liest nur den Cache und blockiert nie; ohne frischen ping
        (nie gefragt, abgelaufen, Fehler) ist es nicht möglich. Projektcode läuft dabei ohne Hook: lokal mit den Rechten des
        Kerns, auf Sokrates-2 in der engeren Sandbox des Dispatchers."""
        if not _befehle_an():
            return False
        if self._ausfuehrer is not None:
            return True
        return self._ort == "sokrates2" and self._ping.grund() is None

    def _befehle_hinweis(self) -> str:
        """Warum befehl: abgelehnt wird, in einem Satz: Schalter aus, oder (Sokrates-2) der Grund aus dem ping: Dispatcher ohne
        pruefen, Prüfsumme der Allowlist weicht ab, Dispatcher nicht erreichbar."""
        if not _befehle_an():
            return BEFEHLE_AUS_HINWEIS
        if self._ausfuehrer is None and self._ort == "sokrates2" and (grund := self._ping.grund()):
            return f"Befehlskriterien (befehl:) gehen hier nicht: {grund}"
        return ""

    def _befehle_angeboten(self) -> bool:
        """Soll die Beschreibung des Werkzeugs befehl: nennen? Wie _befehle_moeglich, auf Sokrates-2 aber auch mit einem abgelaufenen
        ping, wenn der letzte eindeutige Stand "möglich" war: Die Beschreibung entsteht beim Anlegen des Gesprächs, wo der Cache
        nach einer Pause leer ist. Das ist nur ein Hinweis an das Modell; beim Vorbereiten entscheidet _befehle_moeglich."""
        if self._befehle_moeglich():
            return True
        return _befehle_an() and self._ausfuehrer is None and self._ort == "sokrates2" and self._ping.angeboten()

    def _ausfuehrer_fuer(self, job: Job) -> abnahme.Ausfuehrer | None:
        """Der Ausführer für die Befehlskriterien dieses Auftrags: ein eingesetzter oder der lokale; auf Sokrates-2 ein
        RemoteAusfuehrer mit der Kennung des Auftrags (der Dispatcher nimmt das Projekt aus auftrag.json, nie vom Kern). Ohne den
        Schalter keiner. Der ping entscheidet hier nicht mehr: Er hat beim Vorbereiten entschieden, und ein Dispatcher, der
        inzwischen zurückgebaut ist, lehnt den Aufruf mit Exit 2 ab (nicht erfüllt)."""
        if self._ausfuehrer is not None:
            return self._ausfuehrer
        if self._ort == "sokrates2" and _befehle_an():
            return abnahme_remote.RemoteAusfuehrer(
                job.id, lambda args: ssh_argv(args), beenden=lambda proc: _kill_tree(proc),
                abbrechen=lambda: self._ssh_kurz(["cancel", job.id], timeout=10))
        return None

    def _abnahme_beschreibung(self) -> str:
        """Beschreibung des Arguments abnahme. Sie sagt dem Modell, was in dieser Betriebsart geht: Befehle nur, wo sie laufen
        können (sonst würde es Kriterien ableiten, die nie prüfbar sind), auf Sokrates-2 mit dem, was die Sandbox dort hat."""
        text = ("Prüfbares Ziel: 1 bis 5 kurze, messbare Kriterien als Liste von Texten (je höchstens 300 Zeichen), die WAMO "
                f"nach jeder Runde selbst prüft. Nennt {self._auftraggeber} kein Ziel, leite 1 bis 3 Kriterien selbst ab und "
                "nenne sie in einem Satz, ohne zusätzliche Rückfrage. Nie vage wie „mach es gut“. Eine Aussage beurteilt ein "
                "kleines Modell nach dem Abschlussbericht, zum Beispiel „Die README erklärt die Installation“.")
        if self._befehle_angeboten():
            if self._ausfuehrer is None and self._ort == "sokrates2":
                return f"{text} {abnahme_remote.SANDBOX_HINWEIS}"
            return (text + " Mit dem Präfix „befehl:“ läuft ein einzelner Befehl im Projektordner, Exit 0 heißt erfüllt, "
                    "zum Beispiel „befehl: pytest -q“ oder „befehl: npm test“. Nur Tests, Build, Lint und grep, keine "
                    "Verkettung mit ; & | oder Backticks.")
        return text + " Befehle als Kriterium sind hier nicht zugeschaltet, formuliere Aussagen."

    def register(self, llm) -> None:
        llm.register_function("entwicklung_vorbereiten", self._vorbereiten)
        llm.register_function("entwicklung_status", self._status)

    async def _send(self, data: dict) -> None:
        if self._task:
            await self._task.queue_frames([RTVIServerMessageFrame(data=data)])

    def _buero_darf(self, aktion: str) -> bool:
        """Im Büro: gilt die Zulassung noch für das Profil dieser Sitzung? Ein Versuch ohne wird mit Kurzkennung ins Log geschrieben
        (nie ein Wert). Außerhalb des Büros immer True."""
        if not pc_host.buero():
            return True
        nutzer = getattr(self._memory, "user_id", None)
        if entwicklung_betrieb.zulassung_gueltig(self._zulassung, nutzer):
            return True
        logger.warning("Entwicklungsauftrag ({}) im Büro ohne Zulassung abgewiesen, Profil {}", aktion,
                       entwicklung_betrieb.kennung_von(nutzer))
        return False

    async def _vorbereiten(self, params: FunctionCallParams):
        auftrag = auftrag_bereinigen(params.arguments.get("auftrag", ""))  # einmal, Karte und Start nutzen genau diesen Text
        art = str(params.arguments.get("art", "")).strip()
        if not self._buero_darf("vorbereiten"):
            await params.result_callback({"ok": False, "fehler": entwicklung_betrieb.SATZ_KEINE_ZULASSUNG})
            return
        im_buero = pc_host.buero()
        try:  # im Büro entsteht der Projektname erst nach dem Repo (Namensraum kern-, entwicklung_betrieb.buero_projekt)
            projekt = None if im_buero else validate_project(params.arguments.get("projekt"), self._ort)
        except ValueError as e:
            await params.result_callback({"ok": False, "fehler": str(e)})
            return
        if not auftrag:
            await params.result_callback({"ok": False, "fehler": "Auftrag ist leer"})
            return
        if art not in KARTEN:
            await params.result_callback({"ok": False, "fehler": f"Unbekannte Auftragsart, erlaubt: {', '.join(AUFTRAGSARTEN)}"})
            return
        repo_arg = params.arguments.get("repo")
        try:  # ausdrücklich genannt (Werkzeugaufruf) oder eindeutig als GitHub-Adresse im Auftrag
            repo = repo_pruefen(repo_arg) if isinstance(repo_arg, str) and repo_arg.strip() else repo_aus_auftrag(auftrag)
            if im_buero:  # Repo-Pflicht für Arten mit GitHub-Stufe, Projekt kern-<Name aus dem Repo>
                projekt = validate_project(entwicklung_betrieb.buero_projekt(art, repo, params.arguments.get("projekt")), self._ort)
        except ValueError as e:
            await params.result_callback({"ok": False, "fehler": str(e)})
            return
        await self._ping_vor_vorbereiten(params.arguments.get("abnahme"))  # nur mit befehl: auf Sokrates-2, mit Frist
        try:
            kriterien = abnahme.parse_abnahme(params.arguments.get("abnahme"), befehle_erlaubt=self._befehle_moeglich(),
                                              befehle_hinweis=self._befehle_hinweis())
            if self._beurteiler is None and any(k.art == "aussage" for k in kriterien):
                raise ValueError("Aussagen lassen sich in dieser Sitzung nicht beurteilen, formuliere nur Befehle oder lass "
                                 "das Ziel weg")
        except ValueError as e:
            await params.result_callback({"ok": False, "fehler": str(e)})
            return
        job = Job(id=uuid.uuid4().hex[:16], auftrag=auftrag, projekt=projekt, art=art, repo=repo)
        if kriterien:  # ohne abnahme bleibt es bei einer Runde ohne Prüfung (max_runden und zeitlimit_min gelten dann nicht)
            job.kriterien = kriterien
            job.max_runden = _ganzzahl(params.arguments.get("max_runden"), RUNDEN_STANDARD, 1, RUNDEN_MAX)
            job.zeitlimit_s = 60 * _ganzzahl(params.arguments.get("zeitlimit_min"), ZEITLIMIT_STANDARD_MIN, 1, ZEITLIMIT_MAX_MIN)
        self._jobs[job.id] = job
        self._vorgang_anlegen(job)
        karte = {"type": "auftrag", "job": job.id, "projekt": projekt.name, "art": art,
                 "auftrag": auftrag}  # derselbe Text, den Claude Code bekommt: nichts Unsichtbares kommt dazu
        if repo:
            karte["repo"] = repo
        if kriterien:  # die Person bestätigt mit dem Klick auch die Kriterien (und damit Befehle), also muss sie sie sehen
            karte["abnahme"] = [clean_inline(schwaerzen(k.text), abnahme.KRITERIUM_ZEICHEN) for k in kriterien[:abnahme.MAX_KRITERIEN]]
        await self._send(karte)
        ergebnis = {
            "ok": True, "auftrag_id": job.id, "art": KARTEN[art].titel,
            "hinweis": (f"Der Auftrag liegt als Karte in der Oberfläche. Sag kurz Auftrag und Art und dass "
                        f"{self._auftraggeber} ihn dort per Klick startet oder verwirft."),
        }
        if repo:
            ergebnis["repo"] = repo
        if kriterien:
            ergebnis["abnahme"] = [k.text for k in kriterien]
            ergebnis["max_runden"] = job.max_runden
            ergebnis["hinweis"] += (f" Nenne in einem Satz das Ziel ({len(kriterien)} Kriterien, höchstens "
                                    f"{job.max_runden} Runden), ohne zusätzliche Rückfrage.")
        if self._ort == "lokal":
            ergebnis["neu"] = not Path(projekt).exists()
        await params.result_callback(ergebnis)

    async def _status(self, params: FunctionCallParams):
        job = next(reversed(self._jobs.values()), None) if self._jobs else None
        if not job:
            await params.result_callback({"status": "kein Auftrag"})
            return
        # Nur eigene, feste Angaben; Schritte und Ergebnistext stehen auf dem Canvas.
        stand = {
            "status": job.status, "projekt": job.projekt.name, "art": KARTEN[job.art].titel,
            "schritte": len(job.steps), "offene_freigaben": len(job.pending),
            "hinweis": "Details stehen auf dem Canvas.",
        }
        if job.kriterien:
            stand["runde"] = f"{job.runde} von {job.max_runden}"
        await params.result_callback(stand)

    # ---------- Klicks aus der UI ----------
    async def on_client_message(self, msg_type: str, data: dict) -> None:
        if not isinstance(data, dict):
            return
        if msg_type == "auftrag_start":
            await self._auftrag_start(data)
        elif msg_type == "freigabe":
            await self._freigabe(data)
        elif msg_type == "kontingent":  # Anzeige fragt den gespeicherten Stand ab (ohne Lauf, ohne Anfrage an Anthropic)
            await self._kontingent_laden()
            await self._kontingent_senden()

    async def _quittung(self, job: Job | str, ok: bool, grund: str | None = None) -> None:
        """auftrag_quittung an die Oberfläche: ok, oder nicht ok mit einem Satz als Grund (nie Pfade oder Werte). Je Klick auf Starten
        genau eine (Job.quittiert); ein unbekannter Auftrag (nur die Kennung des Klicks) bekommt immer eine."""
        if isinstance(job, Job):
            if job.quittiert:
                return
            job.quittiert = True
        daten = {"type": "auftrag_quittung", "job": job.id if isinstance(job, Job) else clean_inline(job, 100), "ok": ok}
        if not ok:
            daten["grund"] = clean_inline(grund or entwicklung_betrieb.GRUND_NICHT_GESTARTET, 300)
        await self._send(daten)

    async def _auftrag_start(self, data: dict) -> None:
        job_id, ja = str(data.get("job", "")), data.get("entscheidung") == "ja"
        job = self._jobs.get(job_id)
        if not job:
            if ja and job_id:  # die Karte stammt aus einer früheren Sitzung: nicht still bleiben
                await self._quittung(job_id, False, entwicklung_betrieb.SATZ_UNBEKANNT)
            return
        if job.started:
            return
        if not self._buero_darf("start"):
            if ja:
                await self._quittung(job, False, entwicklung_betrieb.SATZ_KEINE_ZULASSUNG)
            return
        if not ja:
            self._jobs.pop(job.id, None)
            self._fuehrung(job, "verwerfen")
            logger.info("Entwicklungsauftrag {} in der Oberfläche verworfen", job.id)
            return
        job.quittiert = False
        if (self._running and not self._running.done()) or self._startet:
            await self._notify(f"Entwicklungsauftrag {job.projekt.name} wartet: es läuft schon ein Auftrag.")
            await self._quittung(job, False, entwicklung_betrieb.SATZ_BELEGT)
            return
        # Ab hier bis zum Anlegen des Laufs gehört der Start diesem Klick: _startet gilt schon vor dem ersten await, sonst kämen zwei
        # schnelle Klicks auf verschiedene Karten beide durch die Prüfung (self._running entsteht erst nach der Buchung in actions)
        self._startet = True
        angelegt = False
        try:
            wunsch = modell_wunsch()
            problem = (f"WAMO_ENTWICKLUNG_MODELL {wunsch!r} ist nicht erlaubt" if wunsch and wunsch not in MODELLE_ERLAUBT
                       else entwicklung_betrieb.buero_problem(ssh_problem) if pc_host.buero()
                       else lokal_problem() if self._ort == "lokal" else ssh_problem())
            if problem:
                logger.error("Entwicklungsauftrag {} nicht gestartet: {}", job.id, problem)
                await self._quittung(job, False, entwicklung_betrieb.SATZ_PROBLEM)
                return
            job.started = True  # sofort, ohne await davor: ein zweiter Klick auf dieselbe Karte startet nichts doppelt
            if self._buch is not None:  # Büro: Tagesgrenze, Datenbank und Audit-Zeile (pending, approved, running) vor dem Start
                job.aktion_id, grund = await self._buch.beginnen(job.id, job.art, job.projekt.name, job.repo,
                                                                 github_stufe(job.art) == "schreiben", abbruch=lambda: self._zu)
                if grund:
                    await self._quittung(job, False, grund)
                    return
            if self._zu:  # close() lief während der Buchung: kein bezahlter Lauf ohne Sitzung, die Zeile wird geschlossen
                await self._aktion_abschliessen(job, STATUS_ABGEBROCHEN_SITZUNG)
                with contextlib.suppress(Exception):  # die Oberfläche ist womöglich schon weg
                    await self._quittung(job, False, entwicklung_betrieb.SATZ_SITZUNG_ENDE)
                return
            self._running = asyncio.create_task(self._run(job, self._start_prompt(job), resume=False))
            angelegt = True
        except Exception as e:  # noqa: BLE001 (ein Fehler beim Start: Quittung mit festem Satz, nie der Fehlertext)
            logger.error("Entwicklungsauftrag {} nicht gestartet: Ausnahme {}", job.id, type(e).__name__)
            await self._aktion_abschliessen(job, "fehlgeschlagen")  # eine Zeile, die beginnen schon geliefert hat, nicht offen lassen
            with contextlib.suppress(Exception):
                await self._quittung(job, False, entwicklung_betrieb.SATZ_INTERN)
        finally:
            self._startet = False
            if not angelegt:  # abgelehnt oder Fehler: die Karte bleibt und ein zweiter Klick geht
                job.started = False

    async def _freigabe(self, data: dict) -> None:
        if not self._buero_darf("freigabe"):
            return
        job = self._jobs.get(str(data.get("job", "")))
        req = job.pending.pop(str(data.get("id", "")), None) if job else None
        if not job or not req:
            return
        approved = data.get("entscheidung") == "ja"
        if approved:
            if self._ort == "sokrates2":
                code, ausgabe = await self._ssh_kurz(["approve", job.id, req["id"]])
                if code != 0:  # nichts verbuchen: die Freigabe bleibt offen (pending), der zweite Klick wird verbucht
                    logger.error("Freigabe für Auftrag {} nicht übertragen (Code {})", job.id, code)
                    satz = entwicklung_betrieb.grund_aus_ausgabe(ausgabe)
                    job.steps.append(f"Freigabe nicht übertragen, bitte erneut versuchen: {req['grund']}"
                                     + (f" ({satz.rstrip('.')})" if satz else ""))
                    job.pending[req["id"]] = req
                    await self._canvas(job)
                    return
            else:
                path = job.dir / "approvals.json"
                try:
                    current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
                except (OSError, json.JSONDecodeError):
                    current = []
                if not isinstance(current, list):
                    current = []
                write_json_atomic(path, [*current, req["id"]])
            await self._memory.update_action(req.get("action_id"), "approved")  # erst nach Code 0 von approve
            job.steps.append(f"Freigegeben: {req['grund']}")
            self._fuehrung(job, "freigabe_entschieden", req, True, job.pending)
            if self._zu:  # close() lief, während die Freigabe übertragen wurde: kein neuer Lauf ohne Sitzung (die Zeile ist schon zu)
                return
            if req.get("tool") == ZUGANG_TOOL:  # Projekt mit diesen Zugängen freigegeben: Auftrag neu starten
                self._running = asyncio.create_task(self._run(job, self._start_prompt(job), resume=False))
                return
            follow_up = (
                f"{self._auftraggeber} hat freigegeben: {req['grund']} "
                f"({json.dumps(req['input'], ensure_ascii=False)[:500]}). "
                "Führe genau diesen Schritt jetzt unverändert aus und berichte kurz."
            )
            # Nicht im Handler auf den laufenden Auftrag warten: _run wartet selbst auf das Lock.
            self._running = asyncio.create_task(self._run(job, follow_up, resume=True))
        else:
            await self._memory.update_action(req.get("action_id"), "rejected")
            job.steps.append(f"Abgelehnt: {req['grund']}")
            job.beendet = True  # eine Absage beendet den Auftrag, die Abnahme läuft nicht weiter
            self._fuehrung(job, "freigabe_entschieden", req, False, job.pending, lauf_folgt=False)
            await self._canvas(job)
            await self._aktion_abschliessen(job, "abgebrochen (Freigabe abgelehnt)")

    # ---------- Ausführung ----------
    def _start_prompt(self, job: Job) -> str:
        """Der erste Prompt eines Auftrags: der Auftrag, mit Abnahme zusätzlich das prüfbare Ziel."""
        if not job.kriterien:
            return job.auftrag
        return f"{job.auftrag}\n\n{abnahme.ziel_text(job.kriterien, job.max_runden)}"

    def _verbraucht(self, job: Job) -> float:
        """Aktive Arbeitszeit des Auftrags in Sekunden: beendete Läufe plus der laufende. Warten auf Freigaben fehlt darin,
        weil zwischen zwei Läufen kein Lauf besteht."""
        return job.aktiv_s + max(0.0, self._jetzt() - job.lauf_beginn)

    def _frist(self, job: Job, basis: float) -> float:
        """Zeitgrenze eines Laufs: die bisherige (harte) Grenze, mit Abnahme zusätzlich die noch übrige Arbeitszeit des Ziels."""
        if not job.kriterien:
            return basis
        return max(30.0, min(basis, job.zeitlimit_s - self._verbraucht(job)))

    def _start_schritt(self, job: Job, resume: bool, start_text: str) -> str:
        if job.neue_runde:
            job.neue_runde = False
            return f"Runde {job.runde} von {job.max_runden}: offene Kriterien nachbessern"
        return "Weiter nach Freigabe" if resume else start_text

    async def _run(self, job: Job, prompt: str, resume: bool) -> None:
        """Ein Lauf von Claude Code und, mit Abnahme, die Schleife darum: Lauf, prüfen, bei Nichterfüllung der nächste Lauf
        (Wiederaufnahme der Sitzung) mit den offenen Kriterien. Ein Abbruch (close) bricht diese Aufgabe ab und damit die
        Schleife sofort; _run_locked beendet dabei Claude Code und jeden Prüfbefehl."""
        async with self._run_lock:
            while True:
                if self._buch is not None and entwicklung_betrieb.auftragsgrenze_erreicht(job.cost_usd):
                    await self._kostengrenze_erreicht(job)  # Büro: vor jedem weiteren Lauf die Grenze je Auftrag
                    return
                job.weiter, job.lauf_beginn = None, self._jetzt()
                job.lauf_ok = job.lauf_gemeldet = job.kosten_gemeldet = False
                try:
                    await self._run_locked(job, prompt, resume)
                except asyncio.CancelledError:  # Abbruch (close): nie verschlucken, aber der Vorgang bleibt nicht offen
                    self._lauf_scheitert(job, STATUS_ABGEBROCHEN_SITZUNG)
                    raise
                except Exception as e:  # noqa: BLE001 (Fehler außerhalb des try von _run_locked beendeten den Task still)
                    logger.error("Entwicklungsauftrag {}: Lauf mit {} beendet", job.id, type(e).__name__)
                    await self._lauf_fehlgeschlagen(job, e)
                    return
                finally:
                    job.aktiv_s += max(0.0, self._jetzt() - job.lauf_beginn)
                    self._kosten_festschreiben(job)  # Büro: ein gestarteter Lauf ohne gemeldete Kosten zählt mindestens die Grenze
                if job.weiter is None or self._zu:
                    return
                prompt, resume = job.weiter, True

    async def _kostengrenze_erreicht(self, job: Job) -> None:
        """Die Kosten dieses Auftrags (alle Läufe) haben die Grenze erreicht: kein weiterer Lauf, der Auftrag endet und die Zeile
        in actions wird geschlossen."""
        job.status, job.weiter, job.beendet = entwicklung_betrieb.STATUS_AUFTRAGSGRENZE, None, True
        job.steps.append(entwicklung_betrieb.SATZ_AUFTRAGSGRENZE)
        self._ende_melden(job, "lauf_endet", job.status, job.pending)
        await self._canvas(job)
        await self._aktion_abschliessen(job)
        await self._announce(job)

    def _kosten_festschreiben(self, job: Job) -> None:
        """Büro: Hat der letzte Lauf gestartet (Ereignis gestartet), aber keine verwertbaren Kosten gemeldet (Abbruch, Auflegen,
        Zeitlimit, ungültiger Wert statt total_cost_usd), zählt der Auftrag mindestens die Auftragsgrenze. Ein abgewiesener Start
        (kein gestartet) kostet nichts. Idempotent: ein zweiter Aufruf ändert nichts."""
        if self._buch is None or "gestartet" not in job.ereignisse or job.kosten_gemeldet:
            return
        if entwicklung_betrieb.kosten_auftrag() is not None:
            job.cost_usd = entwicklung_betrieb.kosten_mit_untergrenze(job.cost_usd)
            job.kosten_geschaetzt = True

    async def _aktion_abschliessen(self, job: Job, status: str | None = None) -> None:
        """Büro: die offene Audit-Zeile dieses Auftrags schließen (done bei fertig, sonst failed), genau einmal. status ersetzt den
        Statustext des Auftrags (Auflegen, abgelehnte Freigabe). Ein Fehler der Datenbank stört den Auftrag nie."""
        if self._buch is None or not job.aktion_id:
            return
        aktion, job.aktion_id = job.aktion_id, None
        self._kosten_festschreiben(job)
        text = status or job.status
        gesperrt = job.ereignisse.get("kanal_gesperrt")
        grund = None  # nur ein bekannter Code des Kanals kommt ins result, nie ein freier Text des Dispatchers
        if isinstance(gesperrt, dict):
            code = gesperrt.get("grund")
            grund = code if isinstance(code, str) and code in entwicklung_betrieb.GRUND_SAETZE else "unbekannt"
        try:
            await self._buch.abschliessen(
                aktion, endstatus(text) == "fertig",
                entwicklung_betrieb.auftrag_ergebnis(job.id, job.art, job.projekt.name, job.repo, text, job.aktiv_s,
                                                     job.cost_usd, job.runde, grund, geschaetzt=job.kosten_geschaetzt))
        except Exception as e:  # noqa: BLE001 (die Buchführung hält den Auftrag nie auf)
            logger.warning("Entwicklungsauftrag {}: Ende nicht gebucht ({})", job.id, type(e).__name__)

    def _lauf_scheitert(self, job: Job, status: str) -> None:
        """Beendet Auftrag und Vorgang, wenn der Lauf keinen eigenen Abschluss mehr melden konnte (Abbruch, Ausnahme). Ein
        schon gemeldetes Ende (zum Beispiel wartet der Vorgang auf eine Freigabe) bleibt unangetastet."""
        if job.lauf_gemeldet:
            return
        job.status = status
        self._ende_melden(job, "lauf_endet", status, {})

    def _ende_melden(self, job: Job, aktion: str, *args) -> None:
        """Gibt dem Vorgang das Ende des Laufs (lauf_endet, ziel_verfehlt) und merkt es, damit _run nichts doppelt meldet."""
        self._fuehrung(job, aktion, *args)
        job.lauf_gemeldet = True

    async def _lauf_fehlgeschlagen(self, job: Job, e: Exception) -> None:
        """Eine Ausnahme außerhalb des Laufs selbst (Ordner, Selbsttest, Freigaben, Canvas): Auftrag und Vorgang enden als
        fehlgeschlagen, die Person erfährt es. Nur der Ausnahmetyp geht ins Log und ins Ergebnis, nie Inhalte."""
        if not job.quittiert:  # der Klick auf Starten bekommt auch bei einem Fehler im Kern eine Antwort
            await self._quittung_nicht_gestartet(job, entwicklung_betrieb.SATZ_INTERN)
        if job.lauf_gemeldet:  # der Lauf war schon abgeschlossen, nur die Zustellung scheiterte: das Ergebnis bleibt gültig
            return
        job.result = job.result or f"Fehler beim Ausführen von Claude Code: {type(e).__name__}"
        self._lauf_scheitert(job, "fehlgeschlagen")
        try:
            await self._canvas(job)
            await self._announce(job)
        except Exception as e2:  # noqa: BLE001 (die Meldung ist eine Zugabe)
            logger.warning("Entwicklungsauftrag {}: Fehler nicht gemeldet ({})", job.id, type(e2).__name__)

    async def _run_locked(self, job: Job, prompt: str, resume: bool) -> None:
        if self._ort == "sokrates2":
            await self._run_remote(job, prompt, resume)
            return
        logger.warning(LOKAL_WARNUNG)
        job.dir.mkdir(parents=True, exist_ok=True)
        job.projekt.mkdir(parents=True, exist_ok=True)
        pending_file = job.dir / "pending.jsonl"
        pending_file.write_text("", encoding="utf-8")
        settings_file = job.dir / "settings.json"
        system_append = f"{rules_for(self._auftraggeber)}\n\n{anweisungsblock(job.art)}"

        if not await guard_selftest(job.projekt, job.dir):
            job.status = "nicht gestartet (Wächter-Selbsttest fehlgeschlagen)"
            self._ende_melden(job, "lauf_endet", job.status, {})
            await self._quittung_nicht_gestartet(job, "Der Wächter-Selbsttest ist fehlgeschlagen.")
            await self._canvas(job)
            await self._announce(job)
            return

        await self._quittung(job, True)
        job.status = "läuft"
        self._fuehrung(job, "starten", resume)
        if job.kriterien:
            self._fuehrung(job, "runde_beginnt", job.runde, job.max_runden)
        job.steps.append(self._start_schritt(job, resume, f"Start in {job.projekt} ({KARTEN[job.art].titel})"))
        await self._canvas(job)
        logger.info("Entwicklungsauftrag {} startet ({}, {})", job.id, job.projekt.name, job.art)
        stderr_tail: deque[str] = deque(maxlen=40)
        stderr_task: asyncio.Task | None = None
        try:
            settings_file.write_text(json.dumps(job_settings(job.projekt, job.dir)), encoding="utf-8")
            args = [claude_exe(), "-p", "--output-format", "stream-json", "--verbose",
                    "--permission-mode", "bypassPermissions", "--model", modell_fuer(job.art, modell_wunsch()),
                    "--settings", str(settings_file), "--append-system-prompt", system_append]
            if resume and job.session_id:
                args += ["--resume", job.session_id]
            self._proc = await asyncio.create_subprocess_exec(
                *args, cwd=str(job.projekt), env=job_env(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=10 * 1024 * 1024,
                start_new_session=sys.platform != "win32",  # eigene Prozessgruppe zum Beenden
            )
            stderr_task = asyncio.create_task(_drain(self._proc.stderr, stderr_tail))
            self._proc.stdin.write(prompt.encode("utf-8"))
            await self._proc.stdin.drain()
            self._proc.stdin.close()
            await asyncio.wait_for(self._read_stream(job), self._frist(job, JOB_TIMEOUT_SECS))
            await asyncio.wait_for(self._proc.wait(), 30)
            job.lauf_ok = self._proc.returncode == 0
            job.status = "fertig" if job.lauf_ok else f"beendet (Code {self._proc.returncode})"
        except asyncio.TimeoutError:
            job.status = "abgebrochen (Zeitlimit)"
            job.result = job.result or "Der Auftrag hat das Zeitlimit überschritten und wurde abgebrochen."
        except Exception as e:  # Start- und Laufzeitfehler landen im Ergebnis, nicht im Gespräch
            logger.exception("Entwicklungsauftrag {} fehlgeschlagen", job.id)
            job.status = "fehlgeschlagen"
            job.result = f"Fehler beim Ausführen von Claude Code: {type(e).__name__}"
        finally:
            # Auch bei Ausnahme oder Abbruch: kein unbeobachteter Lauf mit bypassPermissions.
            proc, self._proc = self._proc, None
            if proc is not None:
                await _kill_tree(proc)
            if stderr_task is not None:
                stderr_task.cancel()
            if proc is not None and proc.returncode not in (0, None) and stderr_tail:
                logger.warning("Claude Code meldete: {}", " | ".join(stderr_tail)[-1500:])

        await self._lauf_ende(job, pending_file)

    async def _lauf_ende(self, job: Job, pending_file: Path | None) -> None:
        """Nach einem Lauf: Freigaben einsammeln, dann (nur mit Abnahme, ohne offene Freigabe, nach einem Lauf ohne Fehler)
        das Ziel prüfen. Erreicht oder verfehlt: der Auftrag endet. Nicht erreicht, aber Runden und Zeit übrig: job.weiter
        trägt den Prompt der nächsten Runde, _run startet sie. Eine offene Freigabe hält die Schleife an (Status
        wartet_freigabe); nach dem Klick läuft derselbe Auftrag weiter und prüft danach. Ohne Abnahme ändert sich nichts."""
        self._fuehrung(job, "lauf_endet_vorlaeufig")
        await self._collect_pending(job, pending_file)
        urteil = None
        if job.kriterien and not job.pending and not job.beendet and not self._zu and job.lauf_ok:
            # Die Prüfung dauert (Befehle, Modellaufrufe): bis zum Urteil gilt der Auftrag nicht als fertig, weder im
            # Status-Werkzeug noch auf der Karte. Der Lauferfolg steht in job.lauf_ok, nicht mehr im Statustext.
            job.status = STATUS_PRUEFT
            await self._canvas(job)
            try:
                urteil = await self._abnahme_pruefen(job)
            except Exception:  # noqa: BLE001 (nie fertig melden, wenn die Prüfung selbst scheitert)
                logger.exception("Entwicklungsauftrag {}: Abnahme nicht auswertbar", job.id)
                job.status, urteil = "Ziel nicht geprüft (Abnahme nicht auswertbar)", "verfehlt"
        if urteil == "weiter":
            await self._canvas(job)
            return
        if urteil == "verfehlt":
            self._ende_melden(job, "ziel_verfehlt", job.status)
        else:
            self._ende_melden(job, "lauf_endet", job.status, job.pending)
        await self._canvas(job)
        await self._announce(job)

    async def _abnahme_pruefen(self, job: Job) -> str:
        """Prüft alle Kriterien (Befehle, Aussagen) und entscheidet: "erreicht", "weiter" (nächste Runde vorbereitet) oder
        "verfehlt" (Runden oder Zeit erschöpft). Alle Kriterien jedes Mal, damit ein Rückschritt auffällt."""
        ergebnisse = await abnahme.auswerten(
            job.kriterien, arbeitsordner=Path(job.projekt), bericht=job.result, dateien=list(job.dateien),
            beurteiler=self._beurteiler, ausfuehrer=self._ausfuehrer_fuer(job))
        job.ergebnisse = ergebnisse
        self._fuehrung(job, "abnahme_ergebnis", [(e.text, e.erfuellt, e.hinweis) for e in ergebnisse])
        offen = [e for e in ergebnisse if not e.erfuellt]
        if not offen:
            job.steps.append(f"Abnahme bestanden nach {_runden_text(job.runde)}")
            job.status = "fertig"
            return "erreicht"
        job.steps.append(f"Abnahme nach Runde {job.runde}: {len(offen)} von {len(ergebnisse)} Kriterien offen")
        zeit_uebrig = job.zeitlimit_s - self._verbraucht(job) >= MIN_REST_RUNDE_S
        if job.runde < job.max_runden and zeit_uebrig and not self._zu:
            job.runde += 1
            prompt = abnahme.runden_prompt(offen, job.runde, job.max_runden)
            if not job.session_id:  # ohne Sitzung zum Fortsetzen kennt Claude Code den Auftrag nicht
                prompt = f"Ursprünglicher Auftrag: {job.auftrag}\n\n{prompt}"
            job.weiter, job.neue_runde = prompt, True
            job.status = f"Runde {job.runde} von {job.max_runden} beginnt"  # bis der Lauf "läuft" meldet: kein alter Status
            job.result = ""  # der Bericht dieser Runde gilt nicht für die nächste, falls sie keinen liefert
            self._fuehrung(job, "runde_beginnt", job.runde, job.max_runden)
            return "weiter"
        job.status = f"Ziel nach {_runden_text(job.runde)} nicht erreicht" + ("" if job.runde >= job.max_runden else " (Zeitlimit)")
        return "verfehlt"

    # ---------- Betriebsart sokrates2 ----------
    async def _ssh_kurz(self, args: list[str], timeout: float = SSH_KURZ_TIMEOUT_SECS) -> tuple[int, str]:
        """Kurzer Dispatcher-Aufruf (pending, approve, cancel, status). (-1, "") bei Zeitüberschreitung."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_argv(args), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("Sokrates-2 nicht erreichbar ({})", type(e).__name__)
            return -1, ""
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            await _kill_tree(proc)
            return -1, ""
        if proc.returncode != 0:
            logger.warning("Dispatcher {}: Code {} {}", args[0], proc.returncode,
                           err.decode("utf-8", "replace").strip()[-300:])
        return proc.returncode, out.decode("utf-8", "replace")

    async def _run_remote(self, job: Job, prompt: str, resume: bool) -> None:
        """Auftrag auf Sokrates-2: ssh ... "start <job> <projekt> <art> [<session>]", Auftrag als JSON-Zeile
        über stdin, danach (mit eigenem Zugang des Profils) der Claude-Zugang als zweite Zeile, stdin bleibt als
        Lebenslinie offen, stdout ist stream-json wie lokal. Der Zugang wird bei jedem Lauf frisch gelesen und gesendet,
        auch nach einer Freigabe und beim Fortsetzen; der Dispatcher speichert ihn nicht."""
        system_append = f"{rules_for(self._auftraggeber, 'sokrates2')}\n\n{anweisungsblock(job.art)}"
        args = ["start", job.id, job.projekt.name, job.art]
        if resume and job.session_id:
            args.append(job.session_id)
        zugang = await self._remote_zugang()
        if zugang is None:  # nie mit dem Zugang eines anderen starten: lieber nicht starten, mit klarer Meldung
            job.ereignisse = {}
            job.status = KEIN_ZUGANG_STATUS
            job.steps.append("Kein Claude-Zugang für dieses Profil. In der Karte Modellzugang den Max-Token "
                             "(claude setup-token) oder einen API-Schlüssel hinterlegen und den Auftrag danach neu stellen.")
            logger.warning("Entwicklungsauftrag {} nicht gestartet: kein Claude-Zugang für Profil {}", job.id,
                           str(getattr(self._memory, "user_id", None) or "?")[:8])
            self._ende_melden(job, "lauf_endet", job.status, {})
            await self._quittung_nicht_gestartet(job, entwicklung_betrieb.SATZ_KEIN_ZUGANG)
            await self._canvas(job)
            await self._announce(job)
            return
        zugang_kopf, zugang_zeile = zugang
        self._eigener_zugang = zugang_kopf.get("zugang") == "folgt"
        job.ereignisse = {}
        job.status = "läuft"
        self._fuehrung(job, "starten", resume)
        if job.kriterien:
            self._fuehrung(job, "runde_beginnt", job.runde, job.max_runden)
        job.steps.append(self._start_schritt(job, resume, f"Start auf Sokrates-2 in {job.projekt} ({KARTEN[job.art].titel})"))
        await self._canvas(job)
        logger.info("Entwicklungsauftrag {} startet auf Sokrates-2 ({}, {})", job.id, job.projekt.name, job.art)
        stderr_tail: deque[str] = deque(maxlen=40)
        stderr_task: asyncio.Task | None = None
        sauber = False
        ende_code: int | None = None
        self._running_job = job
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *ssh_argv(args), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=10 * 1024 * 1024,
                start_new_session=sys.platform != "win32",
            )
            stderr_task = asyncio.create_task(_drain(self._proc.stderr, stderr_tail))
            kopf_daten = {"auftrag": prompt, "system": system_append}
            if modell_wunsch():  # sonst wählt der Dispatcher das Modell der Art (Werkzeugkarte auf Sokrates-2, Haiku)
                kopf_daten["modell"] = modell_wunsch()
            if job.repo:  # im App-Modus gilt das GitHub-Token dann nur für dieses Repo
                kopf_daten["repo"] = job.repo
            kopf_daten.update(zugang_kopf)  # nur "zugang" und "profil"; der Wert steht nie in der Kopfzeile
            daten = (json.dumps(kopf_daten, ensure_ascii=False) + "\n").encode("utf-8")
            if zugang_zeile is not None:
                daten += zugang_zeile  # zweite Zeile, nur über stdin (nicht in argv, nicht in der Umgebung von ssh)
            zugang_zeile = None
            self._proc.stdin.write(daten)
            del daten
            await self._proc.stdin.drain()  # stdin NICHT schließen: Lebenslinie des entfernten Auftrags
            await asyncio.wait_for(self._read_stream(job), self._frist(job, JOB_TIMEOUT_SECS + REMOTE_PUFFER_SECS))
            await asyncio.wait_for(self._proc.wait(), 30)
            ende_code = self._proc.returncode
            job.status = remote_status(self._proc.returncode, job.ereignisse)
            job.lauf_ok = endstatus(job.status) == "fertig"
            # sauber: Ende gemeldet, oder der Dispatcher hat gar nicht erst gestartet (Codes 2 bis 5, 7, 8)
            sauber = "ende" in job.ereignisse or self._proc.returncode in (2, 3, 4, 5, 7, 8)
        except asyncio.TimeoutError:
            job.status = "abgebrochen (Zeitlimit)"
            job.result = job.result or "Der Auftrag hat das Zeitlimit überschritten und wurde abgebrochen."
        except Exception as e:  # Start- und Laufzeitfehler landen im Ergebnis, nicht im Gespräch
            logger.exception("Entwicklungsauftrag {} fehlgeschlagen", job.id)
            job.status = "fehlgeschlagen"
            job.result = f"Fehler beim Auftrag auf Sokrates-2: {type(e).__name__}"
        finally:
            proc, self._proc = self._proc, None
            if proc is not None:
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.close()
                await _kill_tree(proc)  # beendet ssh; der Dispatcher sieht das Ende der Lebenslinie
            if not sauber:
                await self._ssh_kurz(["cancel", job.id], timeout=15)  # zweite Absicherung
            self._running_job = None
            if stderr_task is not None:
                await asyncio.wait({stderr_task}, timeout=1.0)  # die letzten Zeilen noch einsammeln (Grund eines ssh-Fehlers)
                stderr_task.cancel()
            if proc is not None and proc.returncode not in (0, None) and stderr_tail:
                logger.warning("Sokrates-2 meldete: {}", " | ".join(stderr_tail)[-1500:])

        if not job.quittiert:  # nie ein Lauf ohne Quittung: nicht angenommen, mit dem Grund aus Ereignis, Exit-Code oder stderr
            await self._quittung_nicht_gestartet(job, entwicklung_betrieb.ablehn_grund(ende_code, job.ereignisse,
                                                                                       " ".join(stderr_tail)))
        await self._kontingent_abschliessen()
        await self._lauf_ende(job, None)

    async def _quittung_nicht_gestartet(self, job: Job, grund: str) -> None:
        """Quittung ok=false und der Auftrag gilt wieder als nicht gestartet: die Karte bleibt, ein zweiter Klick ist möglich."""
        await self._quittung(job, False, grund)
        job.started = False

    async def _remote_zugang(self) -> tuple[dict, bytes | None] | None:
        """Felder der Kopfzeile und, mit eigenem Zugang, die zweite Zeile für den Dispatcher; None ohne erlaubten Zugang.
        Eigener Zugang des Profils (Max-Token oder API-Schlüssel) hat Vorrang. Ohne ihn gilt der Zugang aus geheim.env nur,
        wenn das Profil dafür vorgesehen ist (serverzugang_erlaubt)."""
        user_id = getattr(self._memory, "user_id", None)
        kennung = normalize_uuid(user_id) if user_id else None
        if not kennung:
            return None
        profil = kennung.replace("-", "")[:8]  # nur zur Zuordnung im Audit und in der Buchführung des Dispatchers
        eigener = await persoenlicher_zugang(user_id)
        if eigener is not None:
            return ({"zugang": "folgt", "profil": profil},
                    (json.dumps(eigener, ensure_ascii=False) + "\n").encode("utf-8"))
        if serverzugang_erlaubt(user_id):
            return {"zugang": "server", "profil": profil}, None
        return None

    # ---------- Kontingent des Max-Abos ----------
    async def _kontingent_laden(self) -> None:
        """Gespeicherten Stand des Profils einmal je Sitzung aus der Datenbank holen und unter die Beobachtungen legen,
        die inzwischen eingetroffen sind. Schlägt es fehl, bleibt der Stand ungeladen (nächster Versuch beim nächsten
        Aufruf) und es wird nichts gespeichert, damit nichts Bekanntes überschrieben wird."""
        if self._kontingent_geladen:
            return
        async with self._kontingent_lock:
            if self._kontingent_geladen:
                return
            user_id, db = getattr(self._memory, "user_id", None), _kontingent_db()
            if not user_id or db is None:
                self._kontingent_geladen = True  # nichts zu laden, und es gibt auch nichts zu speichern
                return
            try:
                alt, stand = await kt.laden(db, user_id)
            except Exception as e:  # noqa: BLE001 (Anzeige ist eine Zugabe: Tabelle fehlt, Datenbank weg)
                logger.warning("Kontingent von Profil {} nicht lesbar ({})", str(user_id)[:8], type(e).__name__)
                return
            self._kontingent = kt.zusammenfuehren(alt, self._kontingent)
            self._kontingent_stand = self._kontingent_stand or stand
            self._kontingent_geladen = True

    async def _kontingent_senden(self) -> None:
        """Servernachricht kontingent: nur geprüfte Felder (kt.nachricht); ohne Daten ist der Zustand unbekannt."""
        self._kontingent_gesendet = time.monotonic()
        self._kontingent_ungesendet = False
        await self._send(kt.nachricht(self._kontingent, self._kontingent_stand))

    async def _kontingent_aufnehmen(self, ereignis: dict) -> None:
        """Ereignis kontingent des Dispatchers: erneut geprüft, über den bekannten Stand gelegt, an die Oberfläche
        geschickt (höchstens alle 2 s) und gespeichert (höchstens alle 60 s, am Laufende immer). Nur für Läufe mit dem
        eigenen Zugang des Profils: das Kontingent des Serverzugangs gehört nicht in dessen Zeile."""
        limits = kt.pruefen_ereignis(ereignis)
        if not limits or not self._eigener_zugang:
            return
        await self._kontingent_laden()
        self._kontingent = kt.zusammenfuehren(self._kontingent, limits)
        self._kontingent_stand = kt.jetzt_iso()
        self._kontingent_offen = self._kontingent_ungesendet = True
        if time.monotonic() - self._kontingent_gesendet >= KONTINGENT_SENDEN_SECS:
            await self._kontingent_senden()
        await self._kontingent_speichern()

    async def _kontingent_speichern(self, erzwingen: bool = False) -> None:
        if not self._kontingent_offen:
            return
        if not erzwingen and time.monotonic() - self._kontingent_gespeichert < KONTINGENT_SPEICHERN_SECS:
            return
        await self._kontingent_laden()
        if not self._kontingent_geladen:
            return  # Stand unlesbar: nicht speichern (sonst gingen die bekannten Limits verloren); später erneut
        user_id, db = getattr(self._memory, "user_id", None), _kontingent_db()
        if not user_id or db is None:
            self._kontingent_offen = False
            return
        self._kontingent_gespeichert = time.monotonic()
        try:
            await kt.speichern(db, user_id, self._kontingent)
            self._kontingent_offen = False
        except Exception as e:  # noqa: BLE001
            logger.warning("Kontingent von Profil {} nicht gespeichert ({})", str(user_id)[:8], type(e).__name__)

    async def _kontingent_abschliessen(self) -> None:
        """Am Ende eines Laufs (oder beim Auflegen): der letzte Stand geht immer an die Oberfläche und in die Datenbank."""
        if self._kontingent_ungesendet:
            await self._kontingent_senden()
        await self._kontingent_speichern(erzwingen=True)

    async def _read_stream(self, job: Job) -> None:
        last_canvas = 0.0
        async for raw in self._proc.stdout:
            try:
                ev = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                job.session_id = ev.get("session_id") or job.session_id
            elif ev.get("type") == "assistant":
                for block in (ev.get("message") or {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        job.steps.append(_describe(block.get("name", ""), block.get("input") or {}))
                        if job.kriterien:
                            _datei_merken(job, block)
            elif ev.get("type") == "wamo":  # Ereignisse des Dispatchers auf Sokrates-2
                name = str(ev.get("ereignis") or "")
                if name == "kontingent":  # Limits des Max-Abos: kein Schritt, nur Anzeige und Speicher
                    await self._kontingent_aufnehmen(ev)
                    continue
                if name:
                    job.ereignisse[name] = ev
                if name in ("gestartet", "zugang_erweitert"):  # der Dispatcher hat den Auftrag angenommen (oder wartet auf die Freigabe)
                    await self._quittung(job, True)
                if name == "selbsttest_ok":
                    job.steps.append("Wächter-Selbsttest in der Sandbox bestanden")
                elif name == "kanal_gesperrt":  # nur der Satz zum Grund-Code, nie ein Wert des Dispatchers
                    job.steps.append(f"Kanal gesperrt: {entwicklung_betrieb.grund_satz(ev.get('grund'))}")
                elif name == "start_abgelehnt":  # Grund nur auf dem Canvas, nie ans Sprachmodell
                    job.steps.append(f"Start abgelehnt: {clean_inline(str(ev.get('grund') or ''), 300)}")
                elif name == "zugang_ungueltig":  # der Dispatcher nennt nie den Zugang selbst, nur was am Format nicht passt
                    job.steps.append(f"Claude-Zugang abgelehnt: {clean_inline(str(ev.get('grund') or ''), 200)}")
                elif name == "gh_widerruf_fehlgeschlagen":
                    job.steps.append("Achtung: Das GitHub-Token dieses Auftrags konnte nicht widerrufen werden; "
                                     "es läuft spätestens nach einer Stunde ab")
                elif name in REMOTE_EREIGNIS_STATUS:
                    job.steps.append(REMOTE_EREIGNIS_STATUS[name])
            elif ev.get("type") == "result":
                job.result = str(ev.get("result") or "")
                kosten = entwicklung_betrieb.kosten_aus_ereignis(ev.get("total_cost_usd"))  # aus der Sandbox: nie ungeprüft addieren
                if kosten is not None:
                    job.cost_usd += kosten
                    job.kosten_gemeldet = True
                job.session_id = ev.get("session_id") or job.session_id
                if ev.get("is_error") and any(k in job.result.lower() for k in AUTH_FEHLER):
                    job.steps.append("Anthropic hat den Claude-Zugang abgelehnt (abgelaufen oder widerrufen?). "
                                     "In der Karte Modellzugang neu hinterlegen.")
            self._fuehrung(job, "ereignis", ev, job.steps)  # Aufgabenliste, Selbsttest: der Vorgang unter Vorgänge
            if time.monotonic() - last_canvas > 1.5:
                last_canvas = time.monotonic()
                await self._canvas(job)

    async def _collect_pending(self, job: Job, pending_file: Path | None) -> None:
        if self._ort == "sokrates2":
            # Anfragen liegen auf Sokrates-2; der Dispatcher prüft sie, hier wird erneut geprüft. Pfade
            # werden gegen einen neutralen Ordner außerhalb des Repos aufgelöst (der echte liegt dort).
            code, out = await self._ssh_kurz(["pending", job.id])
            lines = out.splitlines() if code == 0 else []
            if code != 0:
                job.status = f"{job.status}, Freigaben nicht abrufbar"
                logger.error("Entwicklungsauftrag {}: Freigaben auf Sokrates-2 nicht abrufbar (Code {})", job.id, code)
            zugang = job.ereignisse.get("zugang_erweitert")
            if zugang:  # Anfrage des Dispatchers selbst, nicht aus der Sandbox
                lines.append(json.dumps({k: zugang.get(k) for k in ("id", "grund", "tool", "input")}))
            pruefprojekt = Path(tempfile.gettempdir()) / "wamo-sokrates2" / job.projekt.name
        else:
            if pending_file is None or not pending_file.is_file():
                return
            lines = pending_file.read_text(encoding="utf-8", errors="replace").splitlines()
            pruefprojekt = job.projekt
        for line in lines:
            try:
                req = valid_pending(json.loads(line), pruefprojekt)
            except (json.JSONDecodeError, RecursionError, TypeError):
                req = None
            if req is None:
                logger.warning("Entwicklungsauftrag {}: ungültige Freigabe-Anfrage verworfen", job.id)
                continue
            if req["id"] in job.pending:
                continue
            if len(job.pending) >= MAX_OFFENE_FREIGABEN:
                logger.warning("Entwicklungsauftrag {}: mehr als {} offene Freigaben, Rest verworfen", job.id,
                               MAX_OFFENE_FREIGABEN)
                break
            if req["tool"] == ZUGANG_TOOL:
                zug = job.ereignisse.get("zugang_erweitert", {})
                req["grund"] = clean_inline(str(zug.get("grund", "")), 200) \
                    or "Mehr Zugänge als frühere Aufträge in diesem Projekt"
                # H3: dem Admin die seit dem letzten Lauf geänderten Autostart-Flächen zeigen, dazu die Klon-Option
                teile = [str(z) for z in zug.get("inventur", []) if isinstance(z, str)][:30]
                if zug.get("hinweis_klon"):
                    teile.append(str(zug["hinweis_klon"]))
                req["detail"] = clean_inline(" | ".join(teile), 2000)
            elif _skript_befehl(req["tool"], req["input"]):  # M5: Warnung bei Befehlen, die Projektcode ausführen
                req["warnung"] = "Dieser Befehl führt Projektcode aus (Skript, npm, make); Inhalt genau prüfen."
            req["action_id"] = await self._memory.log_action(
                tool=req["tool"], params=req["input"], reason=req["grund"], risk="critical")
            job.pending[req["id"]] = req
        if job.pending:
            await self._send({
                "type": "freigaben",
                "job": job.id,
                "projekt": job.projekt.name,
                "items": [{"id": r["id"], "grund": r["grund"], "detail": r.get("detail") or _detail(r["tool"], r["input"]),
                           "warnung": r.get("warnung"), "von": self._auftraggeber}
                          for r in job.pending.values()],
            })

    async def _canvas(self, job: Job) -> None:
        if not self._task:
            return
        await self._send({"type": "canvas", "item": self._canvas_item(job)})

    def _canvas_item(self, job: Job) -> dict:
        ordner = f"`{job.projekt}` (Sokrates-2, Sandbox)" if self._ort == "sokrates2" else f"`{job.projekt}`"
        lines = ["## Auftrag", job.auftrag, "", f"**Art:** {KARTEN[job.art].titel}",
                 f"**Status:** {job.status}", f"**Ordner:** {ordner}"]
        if job.repo and self._ort == "sokrates2":
            lines.append(f"**GitHub-Repo:** `{job.repo}` (Token nur für dieses Repo, wenn die App eingerichtet ist)")
        if job.cost_usd:
            lines.append(f"**Kosten bisher:** {job.cost_usd:.2f} USD")
        if job.kriterien:
            stand = {e.text: e for e in job.ergebnisse}
            lines += ["", "## Abnahme", f"**Runde:** {job.runde} von {job.max_runden}"]
            for k in job.kriterien:
                e = stand.get(k.text)
                wort = "offen" if e is None else ("erfüllt" if e.erfuellt else "nicht erfüllt")
                lines.append(f"- {wort}: {k.text}" + (f" (Hinweis: {e.hinweis})" if e is not None and e.hinweis else ""))
        lines += ["", "## Schritte", *[f"- {s}" for s in job.steps[-25:]]]
        if job.result:
            lines += ["", "## Ergebnis", job.result]
        if job.pending:
            lines += ["", "## Wartet auf Freigabe", *[f"- {p['grund']}" for p in job.pending.values()]]
        # Ausgabe von Claude Code kann einen Schlüssel oder Token enthalten (Prompt Injection im Repo): schwärzen, bevor
        # sie auf das Canvas, in die Datenbank oder ins Teilen geht
        item = {"id": f"dev-{job.id}", "kind": "dokument", "title": f"Entwicklung: {job.projekt.name}",
                "text": schwaerzen("\n".join(lines))}
        if job.gespeichert_id:  # gespeichert: die Oberfläche kann es teilen (Dokument oder ganzes Projekt)
            item["teilbar_id"] = job.gespeichert_id
            item["teilbar_projekt"] = job.projekt.name
        return item

    async def _ergebnis_speichern(self, job: Job) -> None:
        """Ergebnis eines Laufs als Canvas-Dokument speichern, damit es sich teilen lässt (Migration 0003): beim ersten
        Mal neu, nach weiteren Läufen als neue Fassung. Fehler stören den Auftrag nicht."""
        item = self._canvas_item(job)
        daten = {k: v for k, v in item.items() if k in ("kind", "title", "text")}
        daten["auftrag"] = {"job": job.id, "projekt": job.projekt.name, "art": job.art, "status": job.status[:80]}
        try:
            if job.gespeichert_id and await eigene_fassung(self._memory, job.gespeichert_id, daten):
                return
            job.gespeichert_id = await self._memory.save_canvas("document", item["title"], daten) or job.gespeichert_id
        except Exception as e:  # noqa: BLE001 (Speichern ist eine Zugabe)
            logger.warning("Entwicklungsauftrag {}: Ergebnis nicht gespeichert ({})", job.id, type(e).__name__)

    async def _notify(self, text: str, run_llm: bool = True) -> None:
        if self._task:
            await self._task.queue_frames([
                LLMMessagesAppendFrame(messages=[{"role": "user", "content": f"[{text}]"}], run_llm=run_llm)
            ])

    async def _announce(self, job: Job) -> None:
        """Feste, kurze Meldung ans Sprachmodell. Der Text von Claude Code geht nur auf den Canvas,
        damit nichts aus dem Auftrag (Webseiten, Dateien, Ausgaben) als Anweisung wirken kann."""
        if callable(self.on_result):
            self.on_result()
        if not job.pending:  # mit offener Freigabe geht der Auftrag nach dem Klick weiter, die Zeile bleibt auf running
            await self._aktion_abschliessen(job)
        await self._ergebnis_speichern(job)
        if job.gespeichert_id:
            await self._canvas(job)  # jetzt mit teilbar_id: die Oberfläche bietet "Teilen" an
        offen = len(job.pending)
        # Nur feste Zahlen zur Abnahme, nie die Hinweise: sie stammen aus Prüfausgaben und stehen auf dem Canvas
        abnahme_stand = (f" Abnahme: {sum(1 for e in job.ergebnisse if e.erfuellt)} von {len(job.ergebnisse)} Kriterien "
                         f"erfüllt, Runde {job.runde} von {job.max_runden}.") if job.ergebnisse else ""
        await self._notify(
            f"Entwicklungsauftrag {job.projekt.name}: {job.status}.{abnahme_stand} Die Details stehen auf dem Canvas. "
            f"Offene Freigaben: {offen}. Sag {self._auftraggeber} das in einem kurzen Satz"
            + ("; die Freigaben gehen in der Oberfläche." if offen else ".")
        )

    async def close(self) -> None:
        """Auflegen: laufenden Auftrag beenden. Auf Sokrates-2 genügt das Ende des ssh-Prozesses (die
        Lebenslinie reißt ab); cancel ist die zweite Absicherung. Die Abnahmeschleife endet sofort: keine weitere Runde, und
        ein laufender Prüfbefehl oder Modellaufruf wird mit der Aufgabe abgebrochen."""
        self._zu = True
        if self._ping_task is not None and not self._ping_task.done():
            self._ping_task.cancel()  # das Aufwärmen des Pings: der Prozess dahinter wird dabei beendet
        running_job = self._running_job
        if self._proc is not None:
            await _kill_tree(self._proc)
        if self._ort == "sokrates2" and running_job is not None:
            await self._ssh_kurz(["cancel", running_job.id], timeout=10)
        with contextlib.suppress(Exception, asyncio.TimeoutError):  # der Abbruch unten lässt das Endspeichern des Laufs aus
            await asyncio.wait_for(self._kontingent_abschliessen(), 5)
        for job in list(self._jobs.values()):  # Büro: offene Audit-Zeilen schließen (der Abbruch bucht sonst nichts mehr)
            with contextlib.suppress(Exception, asyncio.TimeoutError):
                await asyncio.wait_for(self._aktion_abschliessen(job, STATUS_ABGEBROCHEN_SITZUNG), 5)
        if self._running and not self._running.done():
            self._running.cancel()


def _ganzzahl(wert: object, standard: int, minimum: int, maximum: int) -> int:
    """Ganze Zahl aus einem Werkzeugargument, auf minimum bis maximum begrenzt. Alles, was keine Zahl ist (Text, Wahrheitswert,
    Bruch, nichts), gilt als nicht angegeben: der Standard."""
    if isinstance(wert, str) and re.fullmatch(r"\s*-?\d{1,6}\s*", wert):
        wert = int(wert)
    if isinstance(wert, float) and wert.is_integer():
        wert = int(wert)
    if isinstance(wert, bool) or not isinstance(wert, int):
        return standard
    return max(minimum, min(maximum, wert))


def _runden_text(n: int) -> str:
    return f"{n} Runde" if n == 1 else f"{n} Runden"


def _datei_merken(job: Job, block: dict) -> None:
    """Merkt eine mit Write, Edit, MultiEdit oder NotebookEdit geänderte Datei (relativ zum Projekt) für die Abnahme. Nur
    diese vier Werkzeuge: was ein Befehl ändert, sehen wir hier nicht."""
    eingabe = block.get("input")
    if block.get("name") not in ("Write", "Edit", "MultiEdit", "NotebookEdit") or not isinstance(eingabe, dict):
        return
    pfad = str(eingabe.get("file_path") or eingabe.get("notebook_path") or "").replace("\\", "/")
    wurzel = str(job.projekt).replace("\\", "/").rstrip("/") + "/"
    if pfad.startswith(wurzel):
        pfad = pfad[len(wurzel):]
    pfad = clean_inline(pfad, abnahme.DATEI_ZEICHEN)
    if pfad and len(job.dateien) < 200:
        job.dateien[pfad] = None


async def _drain(stream: asyncio.StreamReader | None, tail: deque[str]) -> None:
    """Liest stderr mit, damit der Puffer nie volläuft; die letzten Zeilen bleiben für das Log."""
    if stream is None:
        return
    async for raw in stream:
        tail.append(raw.decode("utf-8", "replace").rstrip()[:300])


def _describe(name: str, data: dict) -> str:
    if name in ("Bash", "PowerShell"):
        return f"Befehl: `{clean_inline(data.get('command', ''), 120)}`"
    if name in ("Write", "Edit", "MultiEdit"):
        return f"Datei: `{Path(str(data.get('file_path', ''))).name}`"
    if name.startswith("mcp__"):
        return f"Dienst: {name.split('__')[1].replace('claude_ai_', '')} {name.rsplit('__', 1)[-1]}"
    if name == "Skill":
        return f"Skill: {clean_inline(data.get('skill') or data.get('name') or '', 80)}"
    if name in ("Agent", "Task"):
        return f"Agent: {clean_inline(data.get('subagent_type') or data.get('description') or '', 80)}"
    return name


DETAIL_CHARS = 20000  # Freigabekarte: der vollständige Aufruf, nur von Steuer- und Bidi-Zeichen bereinigt


def _gekuerzt(text: str) -> str:
    """Kürzt auf DETAIL_CHARS und macht die Kürzung sichtbar (N8); der Fingerabdruck deckt den ganzen Aufruf."""
    sauber = clean_inline(text, DETAIL_CHARS + 1)
    if len(sauber) > DETAIL_CHARS:
        return sauber[:DETAIL_CHARS] + " […gekürzt; der Fingerabdruck deckt den vollständigen Aufruf]"
    return sauber


def _detail(tool: str, data: dict) -> str:
    if tool in ("Bash", "PowerShell", "Monitor"):
        return _gekuerzt(str(data.get("command", "")))
    return _gekuerzt(f"{tool.rsplit('__', 1)[-1]} {json.dumps(data, ensure_ascii=False)}")
