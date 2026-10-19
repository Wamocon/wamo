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
Freigaben kann das Modell NICHT selbst erteilen: es gibt dafür kein MCP-Werkzeug.
Jeder Aufruf landet im Audit-Log (JSONL).
"""

from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
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
MAX_PENDING = 20  # offene PowerShell-Freigaben; ältere fallen heraus
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


def _audit(event: dict) -> None:
    event["ts"] = datetime.now(timezone.utc).isoformat()
    event.setdefault("kanal", KANAL)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


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
    try:
        safe_url = validate_url(url)
        exe = resolve_app(browser if browser in ("chrome", "edge") else "chrome")
    except ValueError as e:
        _audit({"tool": "webseite_oeffnen", "url": str(url)[:200], "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    if len(urlsplit(safe_url).query) > MAX_QUERY_CHARS:
        _audit({"tool": "webseite_oeffnen", "url": safe_url[:200], "risk": "critical", "grund": "lange Query"})
        return {"status": "needs_approval", "summary": "Sehr lange Such-Parameter könnten Gesprächsinhalte "
                "nach außen tragen; das braucht eine Freigabe, die es für PC-Aktionen noch nicht gibt."}
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
    windows = [{k: w[k] for k in ("titel", "programm")} for w in wc.list_windows()]
    _audit({"tool": "fenster_auflisten", "risk": "read", "count": len(windows)})
    return {"status": "success", "fenster": windows[:40], "vorne": wc.foreground()["titel"]}


@mcp.tool()
def fenster_fokussieren(fenster: str) -> dict:
    """Holt ein offenes Fenster nach vorne (change). `fenster` = Teil des Titels oder Programmname,
    z. B. 'Outlook', 'Code', 'Chrome'."""
    try:
        w = wc.find_window(str(fenster))
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


SEND_NEEDS_APPROVAL = {
    "status": "needs_approval",
    "summary": "Senden in Mail- und Messenger-Fenstern braucht eine Freigabe. Für PC-Aktionen gibt es "
    "den Freigabeweg noch nicht; bitte selbst senden.",
}


@mcp.tool()
def tastatur_tippen(text: str, fenster: str = "") -> dict:
    """Tippt Text ins Fenster vorne oder vorher ins genannte Fenster (change), z. B. eine Nachricht in
    Teams oder einen Prompt in den Claude-Chat in VS Code. Zeilenumbruch = Enter. Nur tippen, was
    die Person so gewollt hat. In Konsolen, Admin- und Passwortfenster tippt WAMO nie."""
    if not isinstance(text, str) or not text or len(text) > wc.MAX_TYPE_CHARS:
        return {"status": "rejected", "summary": f"Text fehlt oder ist laenger als {wc.MAX_TYPE_CHARS} Zeichen"}
    try:
        target = _prepare_input(fenster or None)
    except ValueError as e:
        _audit({"tool": "tastatur_tippen", "fenster": str(fenster)[:80], "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    if "\n" in text and wc.is_messaging(target["programm"], target["titel"]):
        # Zeilenumbruch = Enter = Senden
        _audit({"tool": "tastatur_tippen", "ziel": target["titel"][:120], "risk": "critical", "grund": "senden"})
        return SEND_NEEDS_APPROVAL
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
    try:
        codes = wc.parse_combo(kombination)
        target = _prepare_input(fenster or None)
    except ValueError as e:
        _audit({"tool": "tasten_druecken", "kombination": str(kombination)[:40], "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    if frozenset(codes) in wc.SEND_COMBOS and wc.is_messaging(target["programm"], target["titel"]):
        _audit({"tool": "tasten_druecken", "kombination": kombination, "ziel": target["titel"][:120],
                "risk": "critical", "grund": "senden"})
        return SEND_NEEDS_APPROVAL
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
    _audit({"tool": "powershell_lesen", "command": str(command)[:MAX_COMMAND_LEN], "reason": reason, "risk": risk})
    if risk == "blocked":
        return {"status": "blocked", "summary": "Dieser Befehl ist für WAMO gesperrt."}
    if risk == "critical":
        approval_id = uuid.uuid4().hex[:8]
        PENDING[approval_id] = {"command": command, "reason": reason}
        while len(PENDING) > MAX_PENDING:  # begrenzen: älteste Anfrage fällt heraus
            PENDING.pop(next(iter(PENDING)))
        return {
            "status": "needs_approval",
            "approval_id": approval_id,
            "summary": "Das verändert etwas am PC. Ein Admin muss es in der Oberfläche freigeben; "
            "bis dahin passiert nichts.",
        }
    result = _powershell(command)
    _audit({"tool": "powershell_lesen", "result": result["status"]})
    return result


@mcp.tool()
def system_info() -> dict:
    """Basisinfos zum PC (read)."""
    _audit({"tool": "system_info", "risk": "read"})
    return {
        "status": "success",
        "os": platform.platform(),
        "machine": platform.node(),
        "python": platform.python_version(),
    }


@mcp.tool()
def dark_mode(enabled: bool) -> dict:
    """Windows-Dark-Mode fuer Apps und System an/aus (change, nur HKCU)."""
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
    wc.check_target(target["programm"], target["titel"])
    return target


@mcp.tool()
def maus_klicken(x: int, y: int, taste: str = "links", doppelt: bool = False) -> dict:
    """Klickt an einer Bildschirmposition (change). Nur fuer den Voice-Core (Koordinaten kommen aus
    dem Bildmodell). Nie auf Konsolen, Admin-, Anmelde- oder Passwortfenster."""
    if taste not in ("links", "rechts"):
        return {"status": "rejected", "summary": "taste: links oder rechts"}
    try:
        x, y = int(x), int(y)
        target = _check_point(x, y)
    except (ValueError, TypeError) as e:
        _audit({"tool": "maus_klicken", "x": x, "y": y, "risk": "rejected", "error": str(e)})
        return {"status": "rejected", "summary": str(e)}
    _audit({"tool": "maus_klicken", "x": x, "y": y, "taste": taste, "doppelt": bool(doppelt),
            "ziel": target["titel"][:120], "programm": target["programm"], "risk": "change"})
    wc.click(x, y, taste, bool(doppelt))
    return {"status": "success", "summary": f"Klick bei {x},{y} in {target['titel'][:60]}"}


@mcp.tool()
def maus_scrollen(richtung: str = "runter", schritte: int = 5) -> dict:
    """Scrollt im Fenster vorne (change). richtung: hoch oder runter, schritte 1-20."""
    if richtung not in ("hoch", "runter"):
        return {"status": "rejected", "summary": "richtung: hoch oder runter"}
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


def execute_approved(approval_id: str) -> dict:
    """Fuehrt eine freigegebene Aktion aus. Bewusst KEIN MCP-Werkzeug: Freigaben kommen spaeter
    aus der UI (Tabelle actions, Klick durch einen Admin), nie vom Modell."""
    action = PENDING.pop(approval_id, None)
    if not action:
        return {"status": "error", "summary": "Unbekannte oder abgelaufene Freigabe."}
    _audit({"tool": "execute_approved", "approval_id": approval_id, **action})
    return _powershell(action["command"], timeout=600)


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
            host_http.starte(mcp, BIND_HOST, BIND_PORT, os.getenv("WAMO_HOST_TOKEN_FILE"), audit=_audit)
        except (host_http.TokenFehler, ValueError) as e:
            sys.exit(f"WAMO-Host startet nicht: {e}")
    else:
        sys.exit("Aufruf: wamo_host.py --stdio | --http")
