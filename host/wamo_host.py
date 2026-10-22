"""WAMO-Host: die "Hände" von WAMO auf dem Windows-PC.

Werkzeuge per MCP. Zwei Betriebsarten:
  --stdio   Der Voice-Core startet den Host als Unterprozess (Betriebsart Unterwegs, nicht im Netz erreichbar).
  --http    Streamable HTTP nur auf Loopback (WAMO_HOST_BIND, Standard 127.0.0.1:8765) mit Bearer-Token aus
            WAMO_HOST_TOKEN_FILE (Betriebsart Büro: der Kern auf Sokrates-2 kommt über den SSH-Rücktunnel,
            siehe host_http.py und scripts/wamo-tunnel.ps1). Ohne gültiges Token startet der Host nicht.

Sicherheitsmodell (wird HIER erzwungen, nicht im Prompt). Alles, was ein Modell schickt, ist
untrusted: auch Text aus Websuche, Mails oder Dateien kann im Modell als Anweisung wirken.
  read      sofort                (system_info, powershell_lesen mit reinen Get-/Test-Befehlen)
  change    sofort, protokolliert (webseite_oeffnen, programm_oeffnen, dark_mode)
  critical  nur Freigabe-Anfrage; ausgeführt erst nach Bestätigung durch einen Admin in der UI
  blocked   nie
Freigaben kann das Modell NICHT selbst erteilen. Solange eine Anfrage offen ist oder eine freigegebene Aktion läuft,
ruhen alle Eingaben (status=wartet), und die eigene Oberfläche (Freigabekarte) bedient WAMO nie: Das Modell kann die
Karte also weder anklicken noch per Tastatur bestätigen. Das Werkzeug freigabe_entscheiden ruft nur der Voice-Core nach
einem Klick in der Oberfläche auf: Es steht nie in LLM_TOOLS (voice-core/pc_host.py, auch über den Tunnel nicht) und
verlangt ein zweites Geheimnis neben dem Bearer-Token (FREIGABE_SCHLUESSEL). Lokal erzeugt es der Kern je Host-Start
und gibt es als Umgebung WAMO_HOST_FREIGABE mit, im Büro liegt es in der Datei WAMO_HOST_FREIGABE_DATEI
(%LOCALAPPDATA%\\WAMO\\host-freigabe). Eine Anfrage gilt FREIGABE_GUELTIG_SECS lang und wird genau einmal
ausgeführt; direkt davor prüft der Host Ziel, Sperre und Adresse erneut.
Jeder Aufruf landet im Audit-Log (JSONL).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP

import windows_control as wc

AUDIT_LOG = Path(os.getenv("WAMO_AUDIT_LOG") or Path(__file__).with_name("wamo_audit.jsonl"))
BIND_HOST = os.getenv("WAMO_HOST_BIND") or "127.0.0.1"  # nie 0.0.0.0
BIND_PORT = int(os.getenv("WAMO_HOST_PORT") or "8765")

MAX_URL_LEN = 2048
MAX_COMMAND_LEN = 1000
MAX_OUTPUT_CHARS = 6000

mcp = FastMCP("wamo-host", host=BIND_HOST, port=BIND_PORT)

# ---------- Programme: nur diese Liste, keine freien Pfade ----------
_LOCAL = os.environ.get("LOCALAPPDATA", "")
APPS: dict[str, list[str]] = {
    # Name -> Kandidaten (exe-Pfad oder Shell-URI); der erste vorhandene gewinnt
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        rf"{_LOCAL}\Google\Chrome\Application\chrome.exe",
    ],
    "edge": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
    "explorer": [r"C:\Windows\explorer.exe"],
    "notepad": [r"C:\Windows\System32\notepad.exe"],
    "rechner": ["calculator:"],
    "einstellungen": ["ms-settings:"],
    "vscode": [rf"{_LOCAL}\Programs\Microsoft VS Code\Code.exe"],
    "spotify": ["spotify:"],
}

# ---------- PowerShell: nur lesen, alles andere braucht Freigabe ----------
READ_CMDLET = re.compile(
    r"^(Get|Test|Measure|Select|Where|Sort|Format|Group)-[A-Za-z]+(\s|$)", re.IGNORECASE
)
# "read" ist eine enge Allowlist: eine Pipeline aus Get-/Test-/...-Cmdlets mit schlichten
# Argumenten. Diese Zeichen kommen darin nie vor, weil sie Code ausführen oder Befehle verketten:
# Zeilenumbrüche und Steuerzeichen, Skriptblöcke {}, Ausdrücke (), Typzugriffe [] und ::,
# Variablen $, Splatting @, Umleitungen <>, ; & `, die Aliase % (ForEach) und ? (Where) und der Platzhalter *
# (sonst umgeht "WAM*\host-token" oder ".ss*\id_*" die Sperrliste der Zugangsdaten unten).
UNSAFE_READ_CHARS = re.compile(r"[\x00-\x1f\x7f{}()\[\]$@<>;&`%?*]")
# Wörter, mit denen man aus einem "harmlosen" Get-Befehl ausbrechen kann
ESCAPE_TOKENS = re.compile(
    r"\binvoke-expression\b|\biex\b|-enc(odedcommand)?\b|\bstart-process\b"
    r"|\binvoke-\w+|\bset-|\bnew-|\bremove-|\bstop-|\bcopy-|\bmove-|\brename-|\bout-file\b|\badd-content\b",
    re.IGNORECASE,
)
# Lesen ja, aber nicht von Zugangsdaten, Umgebungsvariablen, Zwischenablage oder dem WAMO-Repo:
# solche Befehle brauchen eine Freigabe (das Ergebnis ginge sonst ans Sprachmodell).
SENSITIVE_READ = re.compile(
    r"(\.ssh|\.aws|\.gnupg|\.azure|\.kube|\.docker|credential|\.env\b|\.npmrc|\.netrc|\.pgpass|"
    r"id_rsa|id_ed25519|\.git-credentials|\.claude|\.config[\\/]gh|[\\/]wamo([\\/]|\b)|\benv:|"
    r"Get-Clipboard|Cookies|Login Data|\.kdbx|\.pfx|\.pem)",
    re.IGNORECASE,
)
BLOCKED_PATTERNS = [
    r"Set-MpPreference.*Disable", r"\bbcdedit\b", r"\bcipher\s+/w\b", r"ConvertTo-SecureString",
    r"\bnet\s+user\b", r"Get-Credential", r"\bvssadmin\b", r"\bwbadmin\b",
    r"(password|passwort|kennwort)",
]

PENDING: dict[str, dict] = {}
_PENDING_LOCK = threading.RLock()  # freigabe_entscheiden führt im Thread aus, die übrigen Werkzeuge im Eventloop
_laufend = 0  # freigegebene Aktionen, die gerade ausgeführt werden (Eingaben ruhen solange)
FREIGABE_GUELTIG_SECS = 300  # so lange gilt eine Freigabe-Anfrage (Befehle, Webseiten)
FREIGABE_EINGABE_SECS = 120  # Senden, Tasten, Klicks: kürzer, denn solange ruhen alle Eingaben am PC
EINGABE_ARTEN = ("tippen", "tasten", "klick")
FREIGABE_DETAIL_MAX = 1800  # mehr zeigt die Karte nicht vollständig (mit ⏎ je Enter), also keine Anfrage
FREIGABE_TIMEOUT_SECS = 120  # Zeitlimit eines freigegebenen PowerShell-Befehls (der Kern wartet etwas länger)
APPROVAL_ID_RE = re.compile(r"[0-9a-f]{16}")
FREIGABE_SCHLUESSEL_RE = re.compile(r"[A-Za-z0-9_-]{43,512}")  # wie das Host-Token: 256 Bit, base64url


def _lade_freigabe_schluessel() -> str:
    """Zweites Geheimnis für freigabe_entscheiden: Umgebung WAMO_HOST_FREIGABE (lokal, vom Kern je Start) oder Datei
    WAMO_HOST_FREIGABE_DATEI (Büro). Beide Namen werden aus der Umgebung entfernt, damit Kindprozesse (PowerShell,
    gestartete Programme) den Schlüssel nicht erben. Ungültig oder fehlend: leer, dann wird nie etwas freigegeben."""
    wert = (os.environ.pop("WAMO_HOST_FREIGABE", "") or "").strip()
    datei = (os.environ.pop("WAMO_HOST_FREIGABE_DATEI", "") or "").strip()
    if not wert and datei:
        try:
            wert = Path(datei).read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            wert = ""
    return wert if FREIGABE_SCHLUESSEL_RE.fullmatch(wert) else ""


FREIGABE_SCHLUESSEL = _lade_freigabe_schluessel()
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HOSTNAME_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})")
# oeffnen_mit: Allowlist harmloser Dateitypen (alles andere wird nicht geöffnet)
OPEN_SAFE_SUFFIXES = {
    ".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".csv", ".json", ".log",
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
}
EDITOR_SAFE_SUFFIXES = OPEN_SAFE_SUFFIXES | {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".css", ".scss", ".sql", ".toml",
    ".yaml", ".yml", ".ini", ".cfg", ".xml", ".sh", ".ps1", ".java", ".kt", ".go", ".rs", ".c", ".h",
    ".cpp", ".cs", ".php", ".rb", ".swift", ".vue", ".svelte",
}
MAX_PENDING = 5  # offene Freigabe-Anfragen; weitere werden abgelehnt (keine Flut, keine Verdrängung)
MAX_QUERY_CHARS = 200  # längere Query-Strings könnten Daten aus dem Gespräch hinaustragen
# Netzwerk-Abfragen und DNS sind ein Kanal nach draußen: nie "read"
NETWORK_CMDLETS = re.compile(r"\b(Test-Connection|Test-NetConnection|Resolve-\w+|Test-WSMan)\b", re.IGNORECASE)
UNC_PATH = re.compile(r"(^|[\s'\"=])(\\\\|//)[^\\/\s]")
# absolute Pfade: in einfachen oder doppelten Anführungszeichen (mit Leerzeichen) oder ohne
ABS_PATH = re.compile(
    r"'((?:[A-Za-z]:|~)[\\/][^']*)'|\"((?:[A-Za-z]:|~)[\\/][^\"]*)\"|(?<![\w:'\"])((?:[A-Za-z]:|~)[\\/][^\s'\"|,]*)"
)
# Cmdlets mit Dateipfad-Argumenten: ohne absoluten Pfad im Benutzerordner nie "read" (sonst liest
# es im Arbeitsordner des Hosts, also im WAMO-Repo)
PATH_CMDLETS = re.compile(
    r"^(Get-Content|Get-ChildItem|Get-Item|Get-ItemProperty|Get-ItemPropertyValue|Get-Acl|Get-FileHash|"
    r"Select-String|Test-Path|Get-WinEvent)\b", re.IGNORECASE)


KANAL = "stdio"  # stdio (lokal) oder http (Tunnel); steht in jedem Audit-Eintrag


_AUDIT_LOCK = threading.Lock()


def _audit(event: dict) -> None:
    event["ts"] = datetime.now(timezone.utc).isoformat()
    event.setdefault("kanal", KANAL)
    zeile = json.dumps(event, ensure_ascii=False) + "\n"
    with _AUDIT_LOCK, AUDIT_LOG.open("a", encoding="utf-8") as f:  # auch aus dem Freigabe-Thread
        f.write(zeile)


DUPLICATE_WINDOW_SECS = 6.0
_recent_actions: dict[str, float] = {}


def _duplicate(tool: str, **args) -> dict | None:
    """Gleiche Aktion mit gleichen Argumenten innerhalb weniger Sekunden: nicht nochmal ausfuehren.
    (Log 18.10.: Claude drueckte ctrl+2 zweimal direkt hintereinander.)"""
    key = tool + json.dumps(args, sort_keys=True, ensure_ascii=False)
    now = time.monotonic()
    for k, t in list(_recent_actions.items()):
        if now - t > DUPLICATE_WINDOW_SECS:
            del _recent_actions[k]
    if key in _recent_actions:
        _audit({"tool": tool, "risk": "duplicate", **{k: str(v)[:120] for k, v in args.items()}})
        return {"status": "duplicate", "summary": "Genau das wurde gerade schon ausgefuehrt und wird nicht "
                "wiederholt. Soll es nochmal passieren, muss die Person es erneut sagen."}
    _recent_actions[key] = now
    return None


def _clean(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    text = _CONTROL_CHARS.sub("", text)
    if len(text) > limit:
        return text[:limit] + f"\n[gekuerzt, {len(text)} Zeichen insgesamt]"
    return text


# ---------- Validatoren (direkt vor der Aktion, unabhaengig vom Prompt) ----------
def validate_url(url: object) -> str:
    """Nur http(s)-Adressen ins oeffentliche Netz, ohne Zugangsdaten in der URL."""
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL_LEN:
        raise ValueError("ungueltige URL")
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http"):
        raise ValueError("nur http- und https-Adressen")
    if not parts.hostname:
        raise ValueError("URL ohne Host")
    if parts.username or parts.password:
        raise ValueError("Zugangsdaten in der URL sind nicht erlaubt")
    host = parts.hostname.lower().rstrip(".")
    if host in ("localhost",) or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("lokale Adressen sind nicht erlaubt")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and not ip.is_global:
        raise ValueError("private oder lokale IP-Adressen sind nicht erlaubt")
    if ip is None and not HOSTNAME_RE.fullmatch(host):
        # Browser lösen auch 127.1, 2130706433 oder 0x7f.0.0.1 als Loopback auf: nur echte
        # Domainnamen mit Buchstaben-TLD oder kanonische IP-Adressen.
        raise ValueError("ungültiger Hostname")
    if any(c in url for c in ('"', "\n", "\r", "\t", " ")):
        raise ValueError("URL enthaelt unzulaessige Zeichen")
    if ip is None:
        # Namen wie 127.0.0.1.nip.io oder localtest.me zeigen auf lokale Adressen: alle Treffer der
        # DNS-Auflösung müssen öffentlich sein. (Ein späteres Umschalten des DNS, "Rebinding", kann
        # der Host nicht verhindern; der Browser löst selbst noch einmal auf.)
        addresses = _resolve_host(host)
        if not addresses:
            raise ValueError("Adresse lässt sich nicht auflösen")
        for address in addresses:
            try:
                resolved = ipaddress.ip_address(address.split("%", 1)[0])
            except ValueError as e:
                raise ValueError("ungültige Adresse") from e
            if not resolved.is_global:
                raise ValueError("Adresse zeigt auf ein lokales oder privates Netz")
    return url


def _resolve_host(host: str) -> list[str]:
    """IP-Adressen eines Hostnamens (eigene Funktion, damit Tests sie ersetzen können)."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except (OSError, UnicodeError):
        return []


def resolve_app(name: object) -> str:
    if not isinstance(name, str) or name.strip().lower() not in APPS:
        raise ValueError(f"unbekanntes Programm, erlaubt: {', '.join(sorted(APPS))}")
    for candidate in APPS[name.strip().lower()]:
        if candidate.endswith(":") or shutil.which(candidate) or Path(candidate).is_file():
            return candidate
    raise ValueError(f"{name} ist auf diesem PC nicht installiert")


def classify(command: object) -> str:
    """read | critical | blocked. Unbekanntes ist nie 'read'."""
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND_LEN:
        return "blocked"
    if any(re.search(p, command, re.IGNORECASE) for p in BLOCKED_PATTERNS):
        return "blocked"
    if UNSAFE_READ_CHARS.search(command) or ESCAPE_TOKENS.search(command) or SENSITIVE_READ.search(command):
        return "critical"
    if NETWORK_CMDLETS.search(command) or UNC_PATH.search(command):
        return "critical"
    segments = [s.strip() for s in command.split("|")]
    if not all(READ_CMDLET.match(s) for s in segments):
        return "critical"
    if not _paths_in_home(command, segments):
        return "critical"
    return "read"


def _paths_in_home(command: str, segments: list[str]) -> bool:
    """Alle absoluten Pfade liegen im Benutzerordner; Datei-Cmdlets haben einen absoluten Pfad."""
    for groups in ABS_PATH.findall(command):
        raw = next(g for g in groups if g)
        try:
            if not Path(raw).expanduser().resolve().is_relative_to(HOME):
                return False
        except (OSError, ValueError, RuntimeError):
            return False
    for segment in segments:
        if PATH_CMDLETS.match(segment) and not ABS_PATH.search(segment):
            return False  # relativer oder fehlender Pfad: würde im Ordner des Hosts lesen
    return True


def _launch(target: str, args: list[str] | None = None) -> None:
    if platform.system() != "Windows":
        raise RuntimeError("Host laeuft nicht unter Windows")
    if target.endswith(":") and not args:
        os.startfile(target)  # Shell-URI wie ms-settings:
        return
    # argv-Liste, keine Shell: nichts aus dem Modell wird als Befehl interpretiert
    subprocess.Popen([target, *(args or [])], close_fds=True)


def _powershell(command: str, timeout: int = 60) -> dict:
    if platform.system() != "Windows":
        return {"status": "error", "summary": "Host laeuft nicht unter Windows"}
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "summary": f"PowerShell hat das Zeitlimit von {timeout} s überschritten"}
    except OSError as e:
        return {"status": "error", "summary": f"PowerShell nicht startbar ({type(e).__name__})"}
    return {
        "status": "success" if p.returncode == 0 else "error",
        "exit_code": p.returncode,
        "stdout": _clean(p.stdout),
        "stderr": _clean(p.stderr, 2000),
    }


# ---------- MCP-Werkzeuge ----------
@mcp.tool()
def webseite_oeffnen(url: str, browser: str = "chrome") -> dict:
    """Oeffnet eine Webseite im Browser (change). Fuer Suchen auf Shops direkt die Such-URL bauen,
    z. B. https://www.amazon.de/s?k=wohnzimmer+lampe. browser: chrome oder edge."""
    if locked := _gesperrt("webseite_oeffnen"):
        return locked
    try:
        safe_url = validate_url(url)
        exe = resolve_app(browser if browser in ("chrome", "edge") else "chrome")
    except ValueError as e:
        _audit({"tool": "webseite_oeffnen", "url": str(url)[:200], "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    if len(urlsplit(safe_url).query) > MAX_QUERY_CHARS:
        browser_name = browser if browser in ("chrome", "edge") else "chrome"
        return _anfrage(
            "webseite_oeffnen", "webseite", {"url": safe_url, "browser": browser_name}, "webseite",
            "Webseite mit sehr langer Suchanfrage öffnen", safe_url, browser_name,
            "Sehr lange Such-Parameter könnten Gesprächsinhalte nach außen tragen. Ein Admin muss das in der "
            "Oberfläche freigeben; bis dahin passiert nichts.", {"url": safe_url[:200]})
    if dup := _duplicate("webseite_oeffnen", url=safe_url):
        return dup
    _audit({"tool": "webseite_oeffnen", "url": safe_url, "browser": browser, "risk": "change"})
    _launch(exe, [safe_url])
    return {"status": "success", "summary": f"{safe_url} in {browser} geoeffnet"}


@mcp.tool()
def programm_oeffnen(name: str) -> dict:
    """Startet ein installiertes Programm (change), z. B. Outlook, Teams, VS Code, Claude, Chrome.
    Gesucht wird im Startmenue. Ist es schon offen, besser fenster_fokussieren nutzen.
    Shells, Registry und Admin-Werkzeuge startet WAMO nicht."""
    if locked := _gesperrt("programm_oeffnen"):
        return locked
    try:
        try:
            target = resolve_app(name)
            label = name
        except ValueError:
            app = wc.pick_start_app(str(name), wc.start_menu_apps())
            target, label = None, app["Name"]
    except ValueError as e:
        _audit({"tool": "programm_oeffnen", "name": str(name)[:50], "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    if dup := _duplicate("programm_oeffnen", name=label):
        return dup
    _audit({"tool": "programm_oeffnen", "name": label, "risk": "change"})
    if target:
        _launch(target)
    else:
        wc.launch_start_app(app["AppID"])
    return {"status": "success", "summary": f"{label} gestartet"}


HOME = Path(os.environ.get("USERPROFILE", str(Path.home()))).resolve()
# "wamo": %LOCALAPPDATA%\WAMO enthält das Host-Token des Tunnels (scripts/wamo-tunnel-einrichten.ps1)
SENSITIVE_PARTS = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker", "credentials", "wamo"}
OPEN_WITH = {"vscode": "vscode", "code": "vscode", "explorer": "explorer", "notepad": "notepad", "editor": "notepad"}


def check_open_type(path: Path, app_name: str) -> None:
    """Allowlist statt Denylist: Ordner und harmlose Dateitypen. Der Explorer öffnet Dateien mit dem
    Standardprogramm (könnte Code ausführen), daher dort nur Dokumente und Bilder; Editoren
    (VS Code, Notepad) zeigen Text nur an und dürfen zusätzlich Quelltext öffnen."""
    if path.is_dir():
        if app_name in ("vscode", "explorer"):
            return
        raise ValueError("Ordner öffnen nur VS Code und der Explorer")
    suffix = path.suffix.lower()
    allowed = OPEN_SAFE_SUFFIXES if app_name == "explorer" else EDITOR_SAFE_SUFFIXES
    if suffix not in allowed and not (app_name != "explorer" and suffix == ""):
        raise ValueError(f"Dateityp {suffix or '(ohne Endung)'} öffnet WAMO mit {app_name} nicht")


def validate_local_path(pfad: object) -> Path:
    """Nur vorhandene Pfade im eigenen Benutzerordner, keine Schluessel-/Zugangsdaten-Ordner."""
    if not isinstance(pfad, str) or not pfad.strip() or len(pfad) > 400:
        raise ValueError("Pfad fehlt")
    path = Path(pfad.strip().strip('"')).expanduser()
    if not path.is_absolute():
        raise ValueError(f"Bitte den vollen Pfad, z. B. {HOME / 'Projekte' / 'radar'}")
    path = path.resolve()
    if not path.is_relative_to(HOME):
        raise ValueError(f"Nur Pfade unter {HOME}")
    if any(part.lower() in SENSITIVE_PARTS for part in path.parts):
        raise ValueError("Ordner mit Zugangsdaten öffnet WAMO nicht")
    if not path.exists():
        raise ValueError(f"{path} gibt es nicht")
    return path


@mcp.tool()
def oeffnen_mit(pfad: str, programm: str = "vscode") -> dict:
    """Oeffnet einen Ordner oder eine Datei direkt in einem Programm (change): vscode (Standard, z. B.
    ein Projektordner), explorer oder notepad. Der richtige Weg, um Projekte/Dateien zu oeffnen;
    niemals Befehle wie 'code ...' in ein Fenster tippen. Nur Pfade im eigenen Benutzerordner."""
    if locked := _gesperrt("oeffnen_mit"):
        return locked
    try:
        path = validate_local_path(pfad)
        app_name = OPEN_WITH.get(str(programm).strip().lower())
        if not app_name:
            raise ValueError("programm: vscode, explorer oder notepad")
        if app_name == "notepad" and path.is_dir():
            raise ValueError("Notepad oeffnet nur Dateien")
        check_open_type(path, app_name)
        exe = resolve_app(app_name)
    except ValueError as e:
        _audit({"tool": "oeffnen_mit", "pfad": str(pfad)[:200], "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    if dup := _duplicate("oeffnen_mit", pfad=str(path), programm=app_name):
        return dup
    _audit({"tool": "oeffnen_mit", "pfad": str(path), "programm": app_name, "risk": "change"})
    _launch(exe, [str(path)])  # argv, keine Shell
    return {"status": "success", "summary": f"{path.name} in {app_name} geoeffnet"}


@mcp.tool()
def fenster_auflisten() -> dict:
    """Listet die offenen Fenster mit Titel und Programm (read). Nutzen, um zu sehen, was laeuft,
    z. B. ob VS Code oder Outlook schon offen ist."""
    if locked := _gesperrt("fenster_auflisten"):
        return locked
    windows = [{k: w[k] for k in ("titel", "programm")} for w in wc.list_windows()]
    _audit({"tool": "fenster_auflisten", "risk": "read", "count": len(windows)})
    return {"status": "success", "fenster": windows[:40], "vorne": wc.foreground()["titel"]}


GESPERRT = {
    "status": "locked",
    "summary": "Der Bildschirm ist gesperrt (oder der Bildschirmschoner läuft). Tippen, Klicken, Ansehen und Öffnen "
    "gehen erst wieder, wenn die Person ihn selbst entsperrt; WAMO kann das nicht.",
}
WARTET = {
    "status": "wartet",
    "summary": "Eine Freigabe in der Oberfläche ist noch offen. Bis sie entschieden ist (höchstens 5 Minuten), tippt, "
    "klickt und scrollt WAMO nicht.",
}


def _ist_gesperrt() -> bool:
    """Bildschirm gesperrt? Lässt es sich nicht prüfen, gilt er als gesperrt (keine Eingabe ins Ungewisse)."""
    try:
        return bool(wc.is_locked())
    except Exception:  # noqa: BLE001 (jede Störung der Prüfung heißt: nichts tun)
        return True


def _gesperrt(tool: str) -> dict | None:
    """Gemeinsamer Vorab-Check vor Eingaben, Klicks, Fensterwechseln, Fotos und Öffnen: bei Sperre status=locked."""
    if not _ist_gesperrt():
        return None
    _audit({"tool": tool, "risk": "locked", "grund": "Bildschirm gesperrt"})
    return dict(GESPERRT)


def _eingabe_vorab(tool: str) -> dict | None:
    """Vor jeder Eingabe (Tippen, Tasten, Klicken, Scrollen, Fensterwechsel): Sperre, dann offene Freigaben. Ist eine
    Anfrage offen oder läuft eine freigegebene Aktion, ruhen Eingaben: so kann das Modell die Freigabekarte nicht
    selbst bedienen und nichts zwischen Anfrage und Ausführung verändern."""
    if locked := _gesperrt(tool):
        return locked
    with _PENDING_LOCK:
        _aufraeumen()
        offen = bool(PENDING) or _laufend > 0
    if not offen:
        return None
    _audit({"tool": tool, "risk": "rejected", "grund": "Freigabe offen"})
    return dict(WARTET)

@mcp.tool()
def fenster_fokussieren(fenster: str) -> dict:
    """Holt ein offenes Fenster nach vorne (change). `fenster` = Teil des Titels oder Programmname,
    z. B. 'Outlook', 'Code', 'Chrome'. Bei gesperrtem Bildschirm kommt status=locked."""
    if vorab := _eingabe_vorab("fenster_fokussieren"):
        return vorab
    try:
        w = wc.find_window(str(fenster))
        wc.check_not_wamo_ui(w["programm"], w["titel"])
    except ValueError as e:
        return {"status": "rejected", "summary": str(e)}
    _audit({"tool": "fenster_fokussieren", "titel": w["titel"], "programm": w["programm"], "risk": "change"})
    wc.focus(w["hwnd"])
    return {"status": "success", "summary": f"{w['titel']} ist vorne"}


def _prepare_input(fenster: str | None) -> dict:
    """Optional Fenster fokussieren, dann pruefen, ob dort getippt werden darf."""
    if fenster:
        wc.focus(wc.find_window(fenster)["hwnd"])
    target = wc.foreground()
    wc.check_typing_target(target["programm"], target["titel"])
    return target


def _same_target(target: dict) -> None:
    """Für type_text: wirft ValueError, wenn inzwischen ein anderes Fenster vorne ist."""
    now = wc.foreground()
    if now["hwnd"] != target["hwnd"]:
        raise ValueError("Das Zielfenster hat gewechselt")
    wc.check_typing_target(now["programm"], now["titel"])


SENDEN_HINWEIS = ("Senden in Mail- und Messenger-Fenstern braucht eine Freigabe. Die Anfrage liegt als Karte in "
                  "der Oberfläche; erst nach dem Klick eines Admins wird gesendet.")


def _jetzt() -> float:
    return time.monotonic()


def _aufraeumen() -> None:
    """Abgelaufene Anfragen entfernen (mit _PENDING_LOCK aufrufen); jede Anfrage hat ihre eigene Frist."""
    jetzt = _jetzt()
    for aid in [a for a, e in list(PENDING.items()) if jetzt - e["erstellt"] > e["frist"]]:
        PENDING.pop(aid, None)


class FreigabeNichtMoeglich(ValueError):
    """Keine Anfrage angelegt (zu viele offen oder zu lang für die Karte)."""


def _neue_freigabe(art: str, **daten) -> str:
    """Legt eine Freigabe-Anfrage an (art: powershell, tippen, tasten, klick, webseite) und liefert ihre Kennung. Eine
    gleiche offene Anfrage wird wiederverwendet (keine doppelte Karte, kein doppeltes Senden); ist die Warteschlange
    voll, gibt es keine neue (alte werden nie verdrängt)."""
    with _PENDING_LOCK:
        _aufraeumen()
        for aid, eintrag in PENDING.items():
            if eintrag["art"] == art and all(eintrag.get(k) == v for k, v in daten.items()):
                return aid
        if len(PENDING) >= MAX_PENDING:
            raise FreigabeNichtMoeglich(f"Es warten schon {MAX_PENDING} Freigaben in der Oberfläche. Erst entscheiden, "
                                        "dann neu anfragen.")
        approval_id = secrets.token_hex(8)
        frist = FREIGABE_EINGABE_SECS if art in EINGABE_ARTEN else FREIGABE_GUELTIG_SECS
        PENDING[approval_id] = {"art": art, "erstellt": _jetzt(), "frist": frist, **daten}
        return approval_id


def _freigabe_antwort(approval_id: str, aktion: str, grund: str, detail: str, ziel: str, summary: str,
                      frist: int = FREIGABE_GUELTIG_SECS, anlass: str = "") -> dict:
    """needs_approval mit allem, was die Freigabekarte zeigt (der Kern macht daraus die Karte, activity.py).
    detail ist genau das, was ausgeführt wird; anlass (vom Modell) steht getrennt davon."""
    antwort = {"status": "needs_approval", "approval_id": approval_id, "aktion": aktion, "grund": grund,
               "detail": detail, "ziel": ziel, "gueltig_s": frist, "summary": summary}
    if anlass:
        antwort["anlass"] = anlass
    return antwort


def _text_audit(text: str) -> dict:
    """Getippte Texte nie im Klartext ins Audit: Länge und Prüfsumme genügen zum Nachweis."""
    return {"zeichen": len(text), "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]}


def _anfrage(tool: str, art: str, daten: dict, aktion: str, grund: str, detail: str, ziel: str, summary: str,
             audit: dict, anlass: str = "") -> dict:
    """Freigabe-Anfrage anlegen und beantworten. Zu lange Inhalte (die Karte könnte sie nicht ganz zeigen) und eine
    volle Warteschlange ergeben status=rejected."""
    if len(detail) + detail.count("\n") > FREIGABE_DETAIL_MAX:
        _audit({"tool": tool, "risk": "rejected", "grund": "zu lang für eine Freigabe", **audit})
        return {"status": "rejected", "summary": "Das ist zu lang, um es sicher zur Freigabe zu zeigen. Bitte kürzer."}
    try:
        approval_id = _neue_freigabe(art, **daten)
    except FreigabeNichtMoeglich as e:
        _audit({"tool": tool, "risk": "rejected", "grund": "Warteschlange voll", **audit})
        return {"status": "rejected", "summary": str(e)}
    _audit({"tool": tool, "risk": "critical", "approval_id": approval_id, "art": art, **audit})
    frist = FREIGABE_EINGABE_SECS if art in EINGABE_ARTEN else FREIGABE_GUELTIG_SECS
    return _freigabe_antwort(approval_id, aktion, grund, detail, ziel, summary, frist, anlass)


def _ziel_daten(target: dict) -> dict:
    return {"hwnd": target["hwnd"], "titel": target["titel"], "programm": target["programm"]}


def _ziel_text(target: dict) -> str:
    return f"{target['titel'][:120]} ({target['programm']})"


def _senden_anfrage(tool: str, art: str, target: dict, **daten) -> dict:
    ziel_audit = {"ziel": target["titel"][:120], "programm": target["programm"]}
    if art == "tippen":
        return _anfrage(tool, art, {**_ziel_daten(target), **daten}, "senden", "Nachricht tippen und senden",
                        daten["text"], _ziel_text(target), SENDEN_HINWEIS, {**ziel_audit, **_text_audit(daten["text"])})
    return _anfrage(tool, art, {**_ziel_daten(target), **daten}, "senden", f"Senden mit {daten['kombination']}",
                    f"Taste {daten['kombination']} drücken (sendet)", _ziel_text(target), SENDEN_HINWEIS,
                    {**ziel_audit, "kombination": daten["kombination"]})


# Zeilentrenner, die Eingabefelder wie Enter behandeln können: vor der Prüfung auf Senden zu "\n" vereinheitlichen
_ZEILENTRENNER = re.compile(r"\r\n?|[\x0b\x0c\x85\u2028\u2029]")


@mcp.tool()
def tastatur_tippen(text: str, fenster: str = "") -> dict:
    """Tippt Text ins Fenster vorne oder vorher ins genannte Fenster (change), z. B. eine Nachricht in
    Teams oder einen Prompt in den Claude-Chat in VS Code. Zeilenumbruch = Enter. Nur tippen, was
    die Person so gewollt hat. In Konsolen, Admin- und Passwortfenster tippt WAMO nie."""
    if not isinstance(text, str) or not text or len(text) > wc.MAX_TYPE_CHARS:
        return {"status": "rejected", "summary": f"Text fehlt oder ist laenger als {wc.MAX_TYPE_CHARS} Zeichen"}
    text = _ZEILENTRENNER.sub("\n", text)
    if vorab := _eingabe_vorab("tastatur_tippen"):
        return vorab
    try:
        target = _prepare_input(fenster or None)
    except ValueError as e:
        _audit({"tool": "tastatur_tippen", "fenster": str(fenster)[:80], "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    messenger = wc.is_messaging(target["programm"], target["titel"])
    if messenger and "\t" in text:
        # Tab kann in Mail- und Messenger-Fenstern den Fokus auf "Senden" legen
        _audit({"tool": "tastatur_tippen", "ziel": target["titel"][:120], "risk": "rejected", "grund": "Tab"})
        return {"status": "rejected", "summary": "Tabulatoren tippt WAMO in Mail- und Messenger-Fenstern nicht."}
    if "\n" in text and messenger:
        # Zeilenumbruch = Enter = Senden: nur nach Freigabe, dann genau in dieses Fenster
        return _senden_anfrage("tastatur_tippen", "tippen", target, text=text)
    if dup := _duplicate("tastatur_tippen", text=text, ziel=target["titel"]):
        return dup
    _audit({"tool": "tastatur_tippen", "ziel": target["titel"][:120], "programm": target["programm"],
            "zeichen": len(text), "risk": "change"})
    try:
        wc.type_text(text, recheck=lambda: _same_target(target))
    except ValueError as e:
        _audit({"tool": "tastatur_tippen", "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": f"Tippen abgebrochen: {e}"}
    return {"status": "success", "summary": f"{len(text)} Zeichen in {target['titel'][:60]} getippt"}


@mcp.tool()
def tasten_druecken(kombination: str, fenster: str = "") -> dict:
    """Drueckt eine Taste oder Tastenkombination (change), z. B. 'enter', 'ctrl+t', 'esc', 'alt+tab'.
    Gesperrt: Windows-Taste, Adressleiste (ctrl+l, alt+d, f6), Konsolen, Explorer, Admin- und
    Passwortfenster. Senden in Mail- und Messenger-Fenstern (enter, ctrl+enter, alt+s) braucht eine
    Freigabe."""
    if vorab := _eingabe_vorab("tasten_druecken"):
        return vorab
    try:
        codes = wc.parse_combo(kombination)
        target = _prepare_input(fenster or None)
    except ValueError as e:
        _audit({"tool": "tasten_druecken", "kombination": str(kombination)[:40], "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    if frozenset(codes) in wc.SEND_COMBOS and wc.is_messaging(target["programm"], target["titel"]):
        return _senden_anfrage("tasten_druecken", "tasten", target, codes=codes, kombination=kombination.lower())
    if dup := _duplicate("tasten_druecken", kombination=kombination.lower(), ziel=target["titel"]):
        return dup
    _audit({"tool": "tasten_druecken", "kombination": kombination, "ziel": target["titel"][:120], "risk": "change"})
    wc.press(codes)
    return {"status": "success", "summary": f"{kombination} in {target['titel'][:60]} gedrueckt"}


@mcp.tool()
def powershell_lesen(command: str, reason: str) -> dict:
    """Liest Informationen per PowerShell (nur Get-/Test-Befehle, optional mit | Select/Where/Sort/
    Format/Measure). Alles, was etwas veraendert, wird NICHT ausgefuehrt, sondern als Freigabe-
    Anfrage gestellt (status=needs_approval), die ein Admin freigeben muss. `reason` = wofür,
    wird protokolliert."""
    risk = classify(command)
    if locked := _gesperrt("powershell_lesen"):  # auch Lesen nicht: niemand sieht, wer am PC spricht
        return locked
    _audit({"tool": "powershell_lesen", "command": str(command)[:MAX_COMMAND_LEN], "reason": reason, "risk": risk})
    if risk == "blocked":
        return {"status": "blocked", "summary": "Dieser Befehl ist für WAMO gesperrt."}
    if risk == "critical":
        # Überschrift fest; der Anlass kommt vom Modell und steht nur klein unter dem genauen Befehl
        anlass = " ".join(str(reason).split())[:200]
        return _anfrage(
            "powershell_lesen", "powershell", {"command": command}, "powershell", "PowerShell-Befehl ausführen",
            command, f"PowerShell auf {platform.node()}",
            "Das verändert etwas am PC. Ein Admin muss es in der Oberfläche freigeben; bis dahin passiert nichts.",
            {"befehl": str(command)[:300]}, anlass=anlass)
    result = _powershell(command)
    _audit({"tool": "powershell_lesen", "result": result["status"]})
    return result


@mcp.tool()
def system_info() -> dict:
    """Basisinfos zum PC (read), dazu ob der Bildschirm gerade gesperrt ist (gesperrt)."""
    gesperrt = _ist_gesperrt()
    _audit({"tool": "system_info", "risk": "read", "gesperrt": gesperrt})
    return {
        "status": "success",
        "os": platform.platform(),
        "machine": platform.node(),
        "python": platform.python_version(),
        "gesperrt": gesperrt,
    }


_letzte_sperre: bool | None = None  # nur Wechsel landen im Audit (die Probe kommt alle paar Sekunden)


@mcp.tool()
def sperre_pruefen() -> dict:
    """Nur für den Voice-Core (Probe, nicht für das Gesprächsmodell): ist der Bildschirm gesperrt? (read)"""
    global _letzte_sperre
    gesperrt = _ist_gesperrt()
    if gesperrt != _letzte_sperre:
        _letzte_sperre = gesperrt
        _audit({"tool": "sperre_pruefen", "risk": "read", "gesperrt": gesperrt})
    return {"status": "success", "gesperrt": gesperrt}


@mcp.tool()
def dark_mode(enabled: bool) -> dict:
    """Windows-Dark-Mode fuer Apps und System an/aus (change, nur HKCU)."""
    if locked := _gesperrt("dark_mode"):
        return locked
    value = 0 if bool(enabled) else 1
    key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    cmd = (
        f"Set-ItemProperty -Path '{key}' -Name AppsUseLightTheme -Value {value}; "
        f"Set-ItemProperty -Path '{key}' -Name SystemUsesLightTheme -Value {value}"
    )
    _audit({"tool": "dark_mode", "enabled": bool(enabled), "risk": "change"})
    return _powershell(cmd)


@mcp.tool()
def bildschirmfoto(monitor: int = 0) -> dict:
    """Bildschirmfoto als JPEG (read). Nur fuer den Voice-Core, der es an ein Bildmodell gibt;
    wird dem Gespraechsmodell nicht angeboten. monitor 0 = der mit dem Fenster vorne."""
    if locked := _gesperrt("bildschirmfoto"):
        return locked
    try:
        target = wc.foreground()  # kein Foto, wenn vorne ein Passwort- oder Anmeldefenster ist
        wc.check_target(target["programm"], target["titel"])
        shot = wc.screenshot(int(monitor))
    except ValueError as e:
        _audit({"tool": "bildschirmfoto", "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    _audit({"tool": "bildschirmfoto", "monitor": shot["monitor"], "risk": "read"})
    return {"status": "success", **shot}


def _check_point(x: int, y: int) -> dict:
    left, top, right, bottom = wc.screen_bounds()
    if not (left <= x < right and top <= y < bottom):
        raise ValueError("Punkt liegt ausserhalb der Bildschirme")
    target = wc.window_at(x, y)
    wc.check_input_target(target["programm"], target["titel"])
    return target


@mcp.tool()
def maus_klicken(x: int, y: int, taste: str = "links", doppelt: bool = False, beschreibung: str = "") -> dict:
    """Klickt an einer Bildschirmposition (change). Nur fuer den Voice-Core (Koordinaten kommen aus
    dem Bildmodell). Nie auf Konsolen, Admin-, Anmelde- oder Passwortfenster und nie in die eigene Oberfläche.
    In Mail- und Messenger-Fenstern (ein Klick kann senden) nur nach Freigabe; beschreibung zeigt die Karte."""
    if taste not in ("links", "rechts"):
        return {"status": "rejected", "summary": "taste: links oder rechts"}
    if vorab := _eingabe_vorab("maus_klicken"):
        return vorab
    try:
        x, y = int(x), int(y)
        target = _check_point(x, y)
    except (ValueError, TypeError) as e:
        _audit({"tool": "maus_klicken", "x": x, "y": y, "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    if wc.is_messaging(target["programm"], target["titel"]):
        was = " ".join(str(beschreibung or "").split())[:200]
        detail = f"{'Doppelklick' if doppelt else 'Klick'} ({taste}) bei {x},{y}" + (f"\nLaut Bildmodell: {was}" if was else "")
        return _anfrage("maus_klicken", "klick", {**_ziel_daten(target), "x": x, "y": y, "taste": taste,
                                                   "doppelt": bool(doppelt)},
                        "klicken", f"Klick in {target['programm']}", detail, _ziel_text(target), SENDEN_HINWEIS,
                        {"x": x, "y": y, "ziel": target["titel"][:120], "programm": target["programm"]})
    _audit({"tool": "maus_klicken", "x": x, "y": y, "taste": taste, "doppelt": bool(doppelt),
            "ziel": target["titel"][:120], "programm": target["programm"], "risk": "change"})
    wc.click(x, y, taste, bool(doppelt))
    return {"status": "success", "summary": f"Klick bei {x},{y} in {target['titel'][:60]}"}


@mcp.tool()
def maus_scrollen(richtung: str = "runter", schritte: int = 5) -> dict:
    """Scrollt im Fenster vorne (change). richtung: hoch oder runter, schritte 1-20."""
    if richtung not in ("hoch", "runter"):
        return {"status": "rejected", "summary": "richtung: hoch oder runter"}
    if vorab := _eingabe_vorab("maus_scrollen"):
        return vorab
    steps = max(1, min(20, int(schritte)))
    win32gui = wc._win32()[0]
    left, top, right, bottom = win32gui.GetWindowRect(win32gui.GetForegroundWindow())
    x, y = (left + right) // 2, (top + bottom) // 2
    try:
        target = _check_point(x, y)
    except ValueError as e:
        return {"status": "rejected", "summary": str(e)}
    _audit({"tool": "maus_scrollen", "richtung": richtung, "schritte": steps, "ziel": target["titel"][:120], "risk": "change"})
    wc.scroll(x, y, steps if richtung == "hoch" else -steps)
    return {"status": "success", "summary": f"{steps} Schritte {richtung} in {target['titel'][:60]}"}


UNBEKANNT = {"status": "error", "summary": "Unbekannte oder abgelaufene Freigabe."}


def _schluessel_ok(schluessel: object) -> bool:
    """Konstanter Vergleich mit dem zweiten Geheimnis; ohne eigenen Schlüssel nie."""
    if not FREIGABE_SCHLUESSEL or not isinstance(schluessel, str):
        return False
    return hmac.compare_digest(schluessel.encode("utf-8", "replace"), FREIGABE_SCHLUESSEL.encode("ascii"))


def _inhalt_audit(eintrag: dict) -> dict:
    """Was freigegeben wurde, fürs Audit am PC: Befehl, Adresse, Taste, Klick; getippter Text nur als Prüfsumme."""
    art = eintrag["art"]
    if art == "powershell":
        return {"befehl": str(eintrag["command"])[:300]}
    if art == "webseite":
        return {"url": str(eintrag["url"])[:200]}
    if art == "tippen":
        return {"ziel": eintrag["titel"][:120], **_text_audit(eintrag["text"])}
    if art == "tasten":
        return {"ziel": eintrag["titel"][:120], "kombination": eintrag["kombination"]}
    if art == "klick":
        return {"ziel": eintrag["titel"][:120], "x": eintrag["x"], "y": eintrag["y"]}
    return {}


def entscheiden(approval_id: object, ja: object, schluessel: object) -> dict:
    """Entscheidung zu einer Freigabe-Anfrage (Logik von freigabe_entscheiden, synchron und testbar).
    Reihenfolge: Schlüssel (konstanter Vergleich), Kennung, Ablauf, nein oder ja, Sperre, genau einmal ausführen."""
    global _laufend
    aid = approval_id if isinstance(approval_id, str) else ""
    if not _schluessel_ok(schluessel):
        _audit({"tool": "freigabe_entscheiden", "approval_id": aid[:20], "risk": "blocked",
                "grund": "ohne gültigen Schlüssel"})
        return {"status": "blocked", "summary": "Freigaben kommen nur aus der Oberfläche."}
    with _PENDING_LOCK:
        _aufraeumen()
        eintrag = PENDING.get(aid) if APPROVAL_ID_RE.fullmatch(aid) else None
        if eintrag is None:
            _audit({"tool": "freigabe_entscheiden", "approval_id": aid[:20], "risk": "rejected",
                    "grund": "unbekannt oder abgelaufen"})
            return dict(UNBEKANNT)
        if ja is False:
            PENDING.pop(aid, None)
            _audit({"tool": "freigabe_entscheiden", "approval_id": aid, "art": eintrag["art"], "risk": "rejected",
                    "entscheidung": "nein", **_inhalt_audit(eintrag)})
            return {"status": "rejected", "summary": "Abgelehnt, am PC ist nichts passiert."}
        if ja is not True:
            return {"status": "error", "summary": "Entscheidung fehlt (ja oder nein)."}
        if locked := _gesperrt("freigabe_entscheiden"):
            return locked  # Anfrage bleibt offen: nach dem Entsperren noch einmal freigeben
        PENDING.pop(aid, None)  # genau einmal, auch wenn die Ausführung scheitert
        _laufend += 1  # Eingaben ruhen, bis die Aktion fertig ist
    _audit({"tool": "freigabe_entscheiden", "approval_id": aid, "art": eintrag["art"], "risk": "critical",
            "entscheidung": "ja", **_inhalt_audit(eintrag)})
    try:
        ergebnis = _ausfuehren(eintrag)
    except ValueError as e:  # Prüfung direkt vor der Aktion gescheitert (Fenster weg, Adresse lokal, ...)
        ergebnis = {"status": "error", "summary": f"Nicht ausgeführt: {e}"}
    except Exception as e:  # noqa: BLE001 (Windows-API-Fehler: nichts weiter versuchen, nur melden)
        ergebnis = {"status": "error", "summary": f"Nicht ausgeführt ({type(e).__name__})"}
    finally:
        with _PENDING_LOCK:
            _laufend -= 1
    _audit({"tool": "freigabe_entscheiden", "approval_id": aid, "result": ergebnis.get("status")})
    return ergebnis


@mcp.tool()
async def freigabe_entscheiden(approval_id: str, ja: bool, schluessel: str = "") -> dict:
    """Nur für den Voice-Core nach einem Klick in der Oberfläche, nie für das Gesprächsmodell. Ohne den
    Freigabe-Schlüssel dieses Hosts passiert nichts (critical)."""
    # Im Thread: ein freigegebener Befehl (bis 120 s) oder langes Tippen blockiert die Probe des Kerns nicht
    return await asyncio.to_thread(entscheiden, approval_id, ja, schluessel)


# Ungelesen-Marker vorne im Titel: "(3) Chat | Microsoft Teams", "[2] ...", "* Slack", "• Slack"
_TITEL_ZAEHLER = re.compile(r"^\s*(?:[(\[]\d{1,4}[)\]]|[*•●])\s*")


def _titel_kern(titel: str) -> str:
    return _TITEL_ZAEHLER.sub("", titel or "").strip()


def _passt_zur_anfrage(info: dict, eintrag: dict) -> None:
    """Dasselbe Fenster wie bei der Anfrage: gleiches Handle, gleiches Programm, gleicher Titel (Chat, Betreff)."""
    if info.get("hwnd") != eintrag["hwnd"] or (info.get("programm") or "").lower() != eintrag["programm"].lower():
        raise ValueError("Das Zielfenster der Freigabe gibt es nicht mehr")
    if _titel_kern(info.get("titel", "")) != _titel_kern(eintrag["titel"]):
        raise ValueError("Das Fenster zeigt jetzt etwas anderes als bei der Anfrage")


def _zielfenster(eintrag: dict) -> dict:
    """Fenster der Anfrage prüfen (noch da, gleich, Tippen erlaubt), nach vorne holen und erneut prüfen."""
    info = wc.window_info(eintrag["hwnd"])
    _passt_zur_anfrage(info, eintrag)
    wc.check_typing_target(info["programm"], info["titel"])
    wc.focus(eintrag["hwnd"])
    vorne = wc.foreground()
    _passt_zur_anfrage(vorne, eintrag)
    wc.check_typing_target(vorne["programm"], vorne["titel"])
    return vorne


def _gleiches_ziel(eintrag: dict) -> None:
    """recheck für type_text: vorne ist noch das Fenster der Anfrage (auch vor jedem Enter)."""
    vorne = wc.foreground()
    _passt_zur_anfrage(vorne, eintrag)
    wc.check_typing_target(vorne["programm"], vorne["titel"])


def _ausfuehren(eintrag: dict) -> dict:
    """Führt eine freigegebene Anfrage aus. Alle Prüfungen laufen direkt davor noch einmal."""
    art = eintrag["art"]
    if art == "powershell":
        return _powershell(eintrag["command"], timeout=FREIGABE_TIMEOUT_SECS)
    if art == "tippen":
        ziel = _zielfenster(eintrag)
        try:
            wc.type_text(eintrag["text"], recheck=lambda: _gleiches_ziel(eintrag))
        except ValueError as e:
            return {"status": "error", "summary": f"Abgebrochen ({e}). Ein Teil kann schon getippt sein, bitte prüfen."}
        return {"status": "success", "summary": f"Nachricht in {ziel['titel'][:60]} getippt und gesendet"}
    if art == "tasten":
        ziel = _zielfenster(eintrag)
        wc.press(eintrag["codes"])
        return {"status": "success", "summary": f"{eintrag['kombination']} in {ziel['titel'][:60]} gedrückt"}
    if art == "klick":
        _check_point(eintrag["x"], eintrag["y"])
        _passt_zur_anfrage(wc.window_at(eintrag["x"], eintrag["y"]), eintrag)
        wc.click(eintrag["x"], eintrag["y"], eintrag["taste"], eintrag["doppelt"])
        return {"status": "success", "summary": f"Klick bei {eintrag['x']},{eintrag['y']} in {eintrag['titel'][:60]}"}
    if art == "webseite":
        url = validate_url(eintrag["url"])  # die DNS-Auflösung kann sich seit der Anfrage geändert haben
        _launch(resolve_app(eintrag["browser"]), [url])
        return {"status": "success", "summary": f"{url[:80]} in {eintrag['browser']} geöffnet"}
    raise ValueError("unbekannte Art der Freigabe")


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    elif "--http" in sys.argv:
        import host_http

        KANAL = "http"
        if log_datei := (os.getenv("WAMO_HOST_LOG") or "").strip():
            # Ohne Konsole (geplante Aufgabe): Meldungen von uvicorn in eine Datei; nie Token oder Header
            sys.stdout = sys.stderr = open(log_datei, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        try:
            host_http.starte(mcp, BIND_HOST, BIND_PORT, os.getenv("WAMO_HOST_TOKEN_FILE"), audit=_audit,
                             geraet_datei=(os.getenv("WAMO_HOST_GERAET_DATEI") or "").strip() or None)
        except (host_http.TokenFehler, ValueError) as e:
            sys.exit(f"WAMO-Host startet nicht: {e}")
    else:
        sys.exit("Aufruf: wamo_host.py --stdio | --http")
