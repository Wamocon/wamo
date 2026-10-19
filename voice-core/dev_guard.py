"""Wächter für Entwicklungsaufträge: PreToolUse-Hook von Claude Code.

Claude Code ruft dieses Skript vor JEDEM Werkzeugaufruf auf (JSON auf stdin). Hier, nicht im
Prompt, gilt die Freigabe-Tabelle:

  sofort     Code schreiben im Projektordner, Tests, lokale Commits, Preview-Deploys per CLI,
             lesende Werkzeuge (Read/Grep/Glob, lesende MCP-Werkzeuge von Supabase, Vercel,
             GitHub, Jira und REA)
  Freigabe   git push, Veröffentlichen (npm, Docker, PyPI, GitHub-Release, gh repo create --push),
             Produktions-Deploys, schreibende gh-api-Aufrufe, Uploads an fremde Server,
             WebFetch/WebSearch, jedes schreibende oder unbekannte MCP-Werkzeug, unbekannte
             Werkzeuge, REA-Werkzeuge mit Wirkung auf Geräte, Prozesse oder Dateien
  gesperrt   Löschen von Repos/Projekten/Datenbanken, Secrets und Env-Variablen, Force-Push,
             Zugangsdaten und Schlüsseldateien (.env, *.pem, *.key, id_*, ...), das WAMO-Repo
             selbst (über aufgelöste Pfade gegen REPO_ROOT), Freigabedaten und Umgebungsvariablen

Freigabe-Aktionen werden abgelehnt und als Anfrage in <job-dir>/pending.jsonl notiert. Ein Admin
gibt sie per Klick in der UI frei; dann steht der exakte Fingerabdruck in <job-dir>/approvals.json
und identische Aufrufe dürfen durch, bis einer davon wirklich ausgeführt wurde: derselbe Wächter läuft
mit --nachher als PostToolUse-Hook und verbucht die Ausführung in approvals.json.used. Sperrt ein anderer
Hook den Aufruf, bleibt die Freigabe also bestehen. Die Freigabedatei selbst wird nie umgeschrieben und
darf schreibgeschützt sein. Grenze: Laufen zwei identische Aufrufe parallel (Claude Code startet Hooks
gleichzeitig), sehen beide die Freigabe noch offen und dürfen durch; auf Sokrates-2 zählt der Dispatcher die
tatsächlichen Ausführungen im Datenstrom mit und beendet den Lauf, wenn eine Freigabe öfter genutzt wurde. Projekt- und Job-Ordner
kommen als Argumente (--project-dir, --job-dir) aus der Hook-Konfiguration, nicht aus der Umgebung.

Auf Sokrates-2 läuft derselbe Wächter in der bubblewrap-Sandbox (infra/sokrates2/wamo-job.sh), als
Kopie unter /opt/wamo/waechter/dev_guard.py (auf der schreibgeschützten Wurzel der Sandbox, nicht
umbenennbar). REPO_ROOT ist dort /opt/wamo (die Installation selbst), der Hook steht in der root-eigenen
managed-settings.json, und approvals.json ist schreibgeschützt eingehängt.

Was der Wächter leistet und was nicht: Er verhindert, dass ein Auftrag Freigaben fälscht oder freigabepflichtige
Werkzeugaufrufe ohne Klick ausführt, und er bremst offensichtliche Umwege. Gegen einen feindlichen Agenten
(etwa nach einer Prompt-Injection) schützt er nicht vollständig: Die Token der Auftragsart liegen in der
Sandbox in der Umgebung und sind über Umwege lesbar (/proc/self/environ, env -0, process.env, getenv, eine
Skriptdatei, die das tut) und mit curl oder einem eigenen Programm nutzbar. Die Sperrmuster für diese Wege
sind eine Bremse gegen versehentliches Ausgeben, kein Schutz. Die harte Grenze ist die Sandbox selbst (Dateisystem,
Namensräume, Landlock, seccomp, Ausgangsfirewall); den Rest begrenzen eng zugeschnittene Token.

Fail-closed: Jede unerwartete Ausnahme und ein Hänger über WATCHDOG_SECS enden mit Exit-Code 2.
dev_agent.py startet den Hook zusätzlich über "... || exit 2", damit auch ein fehlendes Python
oder ein Absturz vor main() blockiert, und prüft den Wächter vor jedem Auftrag mit einem Selbsttest.

Grenzen: Diese Regeln sind eine Bremse, keine Sandbox. Wer als derselbe Windows-Benutzer Befehle
ausführt (Betriebsart lokal), findet Umwege (Skriptdateien, cd-Ketten, Aliase). Die eigentliche Grenze
ist die Isolation auf Betriebssystemebene (Betriebsart sokrates2).

Nur Standardbibliothek: läuft mit dem Python des Voice-Cores als eigener Prozess.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

SELF_REPO = "wamo"
REPO_ROOT = Path(__file__).resolve().parent.parent
EXIT_BLOCK = 2  # Claude Code: Exit 2 blockiert den Werkzeugaufruf
WATCHDOG_SECS = 5.0
MAX_COMMAND_CHARS = 8192  # längere Befehle prüft der Wächter nicht im Detail: Freigabe nötig
SELF_BLOCK_REASON = "Das WAMO-Repo selbst ist für Entwicklungsaufträge gesperrt"
SECRET_REASON = "Zugriff auf Zugangsdaten und Schlüsseldateien ist gesperrt"

# Zugangsdaten und Schlüssel: gilt für Read/Grep/Glob (Pfad, Muster, glob) und für Shell-Befehle.
# .env.example/.sample/.template sind Vorlagen ohne Werte und bleiben lesbar.
SECRET_PATH_RE = re.compile(
    r"(\.ssh[\\/]|\.aws[\\/]|\.azure[\\/]|\.kube[\\/]|\.gnupg[\\/]|\.git-credentials|\.npmrc\b|\.netrc\b|"
    r"\.pgpass\b|\.claude[\\/]\.credentials|\.claude\.json\b|\.config[\\/]gh[\\/]|\.docker[\\/]config\.json|"
    r"credentials\.json\b|[\\/]Login Data\b|[\\/]Cookies\b|\bid_(rsa|dsa|ecdsa|ed25519)(_sk)?\b|"
    r"[\\/]proc[\\/][^\\/\s]+[\\/]environ\b|"
    r"(^|[\\/\s\"'=*])\.env(\.(?!example\b|sample\b|template\b|dist\b)[\w.-]+)?(?=$|[\\/\s\"'`;&|),*])|"
    r"(?<![^\s\"'\\/])[^\s\"'\\/]*\.(pem|key|pfx|p12|kdbx)(?=$|[\s\"'`;&|),])|\*\.(pem|key|pfx|p12|kdbx)\b)",
    re.IGNORECASE,
)
# Schreiben in diese Dateien ändert Git- oder Claude-Code-Verhalten (Aliase, Hooks, Einstellungen)
PROTECTED_WRITE_RE = re.compile(
    r"([\\/]\.git[\\/](config|hooks)|[\\/]\.gitconfig$|[\\/]\.claude[\\/]|[\\/]\.mcp\.json$)", re.IGNORECASE
)
WILDCARD = re.compile(r"[*?\[]")

# ---------- git ----------
GIT_VALUE_OPTIONS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env")
GIT_FLAG_OPTIONS = (
    "--no-pager", "-p", "--paginate", "-P", "--bare", "--no-replace-objects", "--literal-pathspecs",
    "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs", "--no-optional-locks", "--no-advice",
    "--no-lazy-fetch", "--exec-path", "--html-path", "--man-path", "--info-path",
)
_VALUE_ALT = "|".join(re.escape(o) for o in GIT_VALUE_OPTIONS)
_FLAG_ALT = "|".join(re.escape(o) for o in sorted(GIT_FLAG_OPTIONS, key=len, reverse=True))
# git, dann beliebig viele globale Optionen, dann der Unterbefehl. Bewusst OHNE re.IGNORECASE:
# sonst passen -C und -c beide auf dieselbe Option, und die Rückverfolgung wächst mit 2^n
# (gemessen: 30 Optionen = Minuten). Nur der Programmname ist unabhängig von Groß/Klein.
GIT_PREFIX = (
    rf"\b(?i:git)(?:\.(?i:exe))?(?:\s+(?:(?:{_VALUE_ALT})(?:=|\s+)(?:\"[^\"]*\"|'[^']*'|\S+)"
    rf"|(?:{_FLAG_ALT})(?:=\S+)?(?=\s)))*\s+"
)
GIT_PUSH_RE = re.compile(GIT_PREFIX + r"(?:push|send-pack)\b")
GIT_FORCE_RE = re.compile(
    GIT_PREFIX + r"push\b.*(\s--force\b|\s-[A-Za-z]*f[A-Za-z]*\b|\s--force-with-lease\b|\s--mirror\b|\s\+\S)"
)
_SEGMENTS = re.compile(r"&&|\|\||[;|&\n]|\$\(|`|\)")


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _program(token: str) -> str:
    return re.split(r"[\\/]", token)[-1].lower().removesuffix(".exe")


def git_invocations(cmd: str) -> Iterator[tuple[str, list[str], list[str]]]:
    """(Unterbefehl, Argumente, Werte von -c) für jeden git-Aufruf in einer Befehlszeile."""
    for segment in _SEGMENTS.split(cmd):
        tokens = _tokens(segment)
        for i, tok in enumerate(tokens):
            if _program(tok) != "git":
                continue
            configs: list[str] = []
            j = i + 1
            while j < len(tokens):
                t = tokens[j]
                if t in GIT_VALUE_OPTIONS:
                    if t == "-c" and j + 1 < len(tokens):
                        configs.append(tokens[j + 1])
                    j += 2
                elif any(t.startswith(o + "=") for o in GIT_VALUE_OPTIONS if o.startswith("--")):
                    j += 1
                elif t.startswith("-"):
                    j += 1
                else:
                    break
            if j < len(tokens):
                yield tokens[j].lower(), tokens[j + 1:], configs


def _push_is_destructive(args: list[str]) -> bool:
    for a in args:
        if a in ("--force", "--mirror", "--delete", "--prune", "--force-if-includes") or a.startswith("--force-with-lease"):
            return True
        if a.startswith("-") and not a.startswith("--") and ("f" in a[1:] or "d" in a[1:]):
            return True
        if a.startswith("+") or (a.startswith(":") and len(a) > 1):
            return True
    return False


def decide_git(cmd: str) -> tuple[str, str] | None:
    for sub, args, configs in git_invocations(cmd):
        if any(c.lower().startswith("alias.") for c in configs):
            return "block", "Git-Aliase in Befehlen sind gesperrt"
        if sub == "config" and any(a.lower().startswith("alias.") for a in args):
            return "block", "Git-Aliase anlegen ist gesperrt"
        if sub == "push":
            if _push_is_destructive(args):
                return "block", "Force-Push und Löschen auf dem Server sind gesperrt"
            return "approval", "git push"
        if sub == "send-pack":
            return "approval", "git push"
    if GIT_FORCE_RE.search(cmd):
        return "block", "Force-Push ist gesperrt"
    if GIT_PUSH_RE.search(cmd):
        return "approval", "git push"
    return None


# ---------- Uploads an fremde Server ----------
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
URL_HOST = re.compile(r"https?://(\[[^\]]+\]|[^/\s\"':?#]+)", re.IGNORECASE)
CURL_UPLOAD = re.compile(r"^(-d|-F|-T|--data(-\w+)?|--form(-string)?|--upload-file|--json)(=|$)|^-[dFT]\S")
CURL_METHOD = re.compile(r"^(-X|--request)(=|$)|^-X[A-Za-z]")
WGET_UPLOAD = re.compile(r"^--(post-data|post-file|body-data|body-file|method)(=|$)")
PS_UPLOAD = re.compile(r"^-(Body|InFile|Form)$|^-Method$", re.IGNORECASE)


def _foreign_hosts(text: str) -> bool:
    hosts = [h.lower() for h in URL_HOST.findall(text)]
    return not hosts or any(h not in LOCAL_HOSTS for h in hosts)  # ohne erkennbare URL: vorsichtig


def decide_upload(cmd: str) -> tuple[str, str] | None:
    for segment in _SEGMENTS.split(cmd):
        tokens = _tokens(segment)
        if not tokens:
            continue
        programs = {_program(t) for t in tokens[:3]}
        upload = False
        if "curl" in programs:
            for i, t in enumerate(tokens):
                if CURL_UPLOAD.match(t):
                    upload = True
                elif CURL_METHOD.match(t):
                    if "=" in t:
                        method = t.split("=", 1)[1]
                    elif t.startswith("-X") and len(t) > 2:
                        method = t[2:]
                    else:
                        method = tokens[i + 1] if i + 1 < len(tokens) else ""
                    upload = upload or method.upper() not in ("GET", "HEAD", "")
        if "wget" in programs:
            upload = upload or any(WGET_UPLOAD.match(t) for t in tokens)
        if programs & {"invoke-webrequest", "invoke-restmethod", "iwr", "irm"}:
            for i, t in enumerate(tokens):
                if PS_UPLOAD.match(t):
                    if t.lower() == "-method":
                        upload = upload or (i + 1 < len(tokens) and tokens[i + 1].upper() not in ("GET", "HEAD"))
                    else:
                        upload = True
        if upload and _foreign_hosts(segment):
            return "approval", "Daten an einen fremden Server senden"
    return None


# ---------- Shell ----------
SHELL_BLOCKED = [
    (r"\bgit(?:\.exe)?\b.*\bfilter-(branch|repo)\b", "Git-Historie umschreiben ist gesperrt"),
    (r"\bgit(?:\.exe)?\b.*\bcredential\b|\bgit-credential", "Zugriff auf Zugangsdaten ist gesperrt"),
    (r"\bgh\s+repo\s+(delete|archive)\b", "Repos löschen/archivieren ist gesperrt"),
    (r"\bgh\s+release\s+delete\b", "Releases löschen ist gesperrt"),
    (r"\bgh\s+(secret|variable|auth|ssh-key|gpg-key)\b", "Secrets und Zugänge sind gesperrt"),
    (r"\b(vercel|vc)\s+(rm|remove)\b|\b(vercel|vc)\s+project\s+(rm|remove)\b", "Vercel-Projekte löschen ist gesperrt"),
    (r"\b(vercel|vc)\s+(env|secrets?|domains?\s+buy|certs?)\b", "Vercel-Env/Domains/Zertifikate sind gesperrt"),
    (r"\bsupabase\s+(projects\s+delete|secrets|orgs)\b", "Supabase-Projekte/Secrets sind gesperrt"),
    (r"\b(npm|pnpm|yarn|bun)\s+unpublish\b", "Pakete zurückziehen ist gesperrt"),
    (r"\b(dropdb|drop\s+database)\b", "Datenbank löschen ist gesperrt"),
    (r"\brm\s+(?:-[a-z-]+\s+)*(/\*?|~[^\s]*|\$HOME\S*|\$\{HOME\}\S*|[A-Za-z]:[\\/]?\*?)(\s|$)",
     "Löschen außerhalb des Projekts ist gesperrt"),
    (r"Remove-Item\b.*\s(C:\\|~|\$env:USERPROFILE)(\s|\\?$)", "Löschen außerhalb des Projekts ist gesperrt"),
    (r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^|]*\|\s*(sh|bash|iex|powershell|pwsh)\b",
     "Skripte aus dem Netz direkt ausführen ist gesperrt"),
    (r"approvals\.json|pending\.jsonl|[\\/]\.jobs\b|[\\/]run[\\/]wamo\b|\bWAMO_|os\.environ|\bprintenv\b|"
     r"\benviron\b|\$env:|\benv:|Win32_Process|\bwmic\b",
     "Zugriff auf Freigabedaten und Umgebungsvariablen ist gesperrt"),
    (r"\bGIT_CONFIG_", "Git-Konfiguration über Umgebungsvariablen ist gesperrt"),
    # Zugangstoken in der Sandbox: gh, vercel und Claude Code lesen sie selbst, Befehle brauchen sie nie.
    (r"\b(GH_TOKEN|GH_TOKEN_LESEN|GITHUB_TOKEN|VERCEL_TOKEN|SUPABASE_ACCESS_TOKEN|JIRA_API_KEY|JIRA_API_TOKEN|ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_CUSTOM_HEADERS)\b|"
     r"\bprocess\.env\b|\bgetenv\b|"
     r"(^|[;&|(]\s*)(env|export\s+-p|declare\s+-[a-z]*[px][a-z]*|set|compgen\s+-[ev])\s*($|[;&|)>])",
     "Zugriff auf Zugangstoken ist gesperrt"),
]
SHELL_APPROVAL = [
    (r"\bgh\s+pr\s+merge\b", "Pull Request mergen"),
    (r"\bgh\s+pr\s+create\b", "Pull Request anlegen (pusht den Branch)"),
    (r"\bgh\s+repo\s+create\b.*\s--push\b", "Repo anlegen und pushen"),
    (r"\bgh\s+release\s+(create|upload|edit)\b", "GitHub-Release veröffentlichen"),
    (r"\bgh\s+api\b.*(\s(-f|-F)(\s|$)|\s(--field|--raw-field|--input)(\s|=|$)|\s-[fF]\w+=)", "GitHub-API schreibend"),
    (r"\bgh\s+api\b.*(\s-X\s*|\s--method[\s=]+)(?!GET\b)[A-Za-z]", "GitHub-API schreibend"),
    (r"\b(vercel|vc)\b.*(\s--prod\b|\s--target(=|\s+)['\"]?prod)", "Vercel-Produktion"),
    (r"\b(vercel|vc)\s+(promote|rollback|alias)\b", "Vercel-Produktion"),
    (r"\bsupabase\s+(db\s+push|db\s+reset|migration\s+up|link)\b", "Supabase-Datenbank (CLI) ändern"),
    (r"\b(npm|pnpm|yarn|bun)\s+publish\b", "Paket veröffentlichen"),
    (r"\b(poetry|uv|cargo|gem)\s+publish\b|\bgem\s+push\b|\btwine\s+upload\b", "Paket veröffentlichen"),
    (r"\bdocker\s+(image\s+)?push\b|\bdocker\s+buildx\b.*\s--push\b", "Container-Image veröffentlichen"),
]

# ---------- MCP: Supabase, Vercel, GitHub (Allowlist lesender Werkzeuge) ----------
MCP_READ_PREFIXES = ("get_", "list_", "search_", "count_", "aggregate_")
MCP_BLOCKED = {
    "supabase": re.compile(r"^(delete_branch|pause_project|create_edge_function_secret)$|secret", re.IGNORECASE),
    "vercel": re.compile(
        r"(env|secret|domain_availability|buy_domain|create_or_transfer_domain|deletion|firewall|"
        r"protection_bypass|revoke|token|kms|pause_project|delete|attack_challenge|bypass_ip|drain)",
        re.IGNORECASE),
    "github": re.compile(r"(delete|secret|archive)", re.IGNORECASE),
    "jira": re.compile(r"(delete|remove|token)", re.IGNORECASE),
}
# Lesend klingende Namen, die trotzdem etwas freigeben oder abrufen lassen
MCP_READ_EXCEPTIONS = re.compile(r"(access_to|deletion|share|token|key_?s?$|env)", re.IGNORECASE)
MCP_READ_EXTRA = {
    "supabase": {"generate_typescript_types"}, "vercel": set(), "github": set(),
    "jira": {"jira_search", "confluence_search", "jira_batch_get_changelogs"},
}
# Der Jira-Server (mcp-atlassian) stellt seinen Werkzeugen das Produkt voran: jira_get_issue, confluence_get_page
MCP_NAME_PREFIXES = {"jira": ("jira_", "confluence_")}

# ---------- MCP: REA (rea-agents 6.5.0) ----------
# Freigabepflichtig: verändert Geräte, Prozesse oder Dateien oder führt Code im Ziel aus.
REA_APPROVAL: dict[str, str] = {
    # Android-Gerät verändern
    "install_adb_package": "REA installiert eine App auf dem Gerät",
    "uninstall_adb_package": "REA entfernt eine App vom Gerät",
    "push_adb_file": "REA schreibt eine Datei auf das Gerät",
    "start_adb_app": "REA startet eine App auf dem Gerät",
    "stop_adb_app": "REA beendet eine App auf dem Gerät",
    "start_adb_activity": "REA startet eine Activity auf dem Gerät",
    # Code in fremde Prozesse einschleusen (Frida)
    "instrument_with_frida": "REA instrumentiert einen Prozess mit Frida",
    "start_frida_session": "REA hängt sich mit Frida an einen Prozess",
    "load_frida_script": "REA lädt ein Frida-Skript in einen Prozess",
    "unload_frida_script": "REA ändert die Frida-Instrumentierung eines Prozesses",
    "resume_frida_session": "REA setzt einen angehaltenen Prozess fort",
    # Debugger mit Ausführung im Ziel
    "start_gdb_session": "REA startet einen GDB-Debugger",
    "gdb_console": "REA führt GDB-Befehle aus",
    "start_rizin_debug_session": "REA startet einen Rizin-Debugger",
    "rizin_debug_command": "REA führt Rizin-Debug-Befehle aus",
    "execute_rizin_command": "REA führt freie Rizin-Befehle aus (können Dateien schreiben)",
    "cutter_command": "REA führt freie Cutter-Befehle aus",
    # Zielprogramm starten oder fernsteuern
    "capture_process_scenario": "REA startet das Zielprogramm",
    "capture_native_ui_scenario": "REA startet und bedient eine native Oberfläche",
    "trace_native_ui_action": "REA löst Aktionen in einer nativen Oberfläche aus",
    "observe_native_calls": "REA startet das Zielprogramm unter einem Debugger",
    "capture_electron_scenario": "REA startet eine Electron-App und klickt darin",
    "capture_browser_scenario": "REA steuert einen Browser (Klicks, Eingaben, Formulare)",
    "observe_web_execution": "REA instrumentiert eine laufende Seite (Coverage)",
}
# Erlaubt: liest nur, ändert nur REAs eigene Analysedaten oder schreibt neue Dateien in neue Ordner.
REA_ALLOWED: frozenset[str] = frozenset({
    # Analyse lesen
    "address_name", "address_to_file_offset", "analyze_function", "analyze_javascript_application",
    "analyze_swift_types", "analyze_web_bundle", "batch_decompile", "binary_overview", "binary_session",
    "build_call_path", "build_reconstruction_obligation_ledger", "compare_application_versions",
    "compare_artifacts", "compare_bundles", "compare_functions", "compare_javascript_export_shapes",
    "compare_managed_members", "compare_process_captures", "compare_source_to_bundle",
    "compare_wasm_interfaces", "compare_web_captures", "compare_web_screenshots",
    "correlate_static_and_runtime", "current_address", "current_document", "current_procedure",
    "decode_interface_builder", "decode_signal_recording", "decompile_jeb_item", "demangle_swift",
    "evaluate_reconstruction_coverage", "find_changed_behavior", "find_xrefs_to_name",
    "get_call_graph", "get_evidence_bundle", "get_navigation_context", "get_objc_classes",
    "get_objc_protocols", "identify_flutter_build", "inspect_address_context", "inspect_analysis_view",
    "inspect_android_class", "inspect_android_method", "inspect_android_package", "inspect_apktool_client",
    "inspect_artifact", "inspect_asset_catalog", "inspect_binary_layout", "inspect_dart_aot",
    "inspect_electron_fuses", "inspect_evm_interface", "inspect_firmware_regions", "inspect_go_binary",
    "inspect_jeb_client", "inspect_keyed_archive", "inspect_macho", "inspect_managed_artifact",
    "inspect_managed_members", "inspect_managed_native_boundaries", "inspect_native_api",
    "inspect_native_data_type", "inspect_native_dispatch_metadata", "inspect_native_instruction",
    "inspect_native_load_image", "inspect_packet_capture", "inspect_packet_fields", "inspect_pcap_capture",
    "inspect_pcap_client", "inspect_pe_resources", "inspect_plist", "inspect_recorded_crash",
    "inspect_rust_binary", "inspect_signal_decoders", "inspect_signature", "inspect_sqlite_database",
    "inspect_wasm_artifact", "inspect_with_objdump", "list_architectures", "list_bookmarks",
    "list_documents", "list_jeb_units", "list_names", "list_procedures", "list_segments", "list_strings",
    "list_unknowns", "procedure_address", "procedure_assembly", "procedure_callees", "procedure_callers",
    "procedure_info", "procedure_pseudo_code", "procedure_references", "project_android_application_graph",
    "project_apple_application_graph", "project_harmony_application_graph",
    "project_managed_application_graph", "read_bytes", "read_function_instructions",
    "reconcile_javascript_runtime", "recover_javascript_sources", "resolve_containing_procedure",
    "resolve_native_call_targets", "search_android_classes", "search_bytes", "search_procedures",
    "search_strings", "trace_android_references", "trace_application_feature", "trace_call_path",
    "trace_dylib_resolution", "trace_feature", "trace_javascript_semantics", "trace_native_values",
    "trace_web_module_imports", "trace_web_source_location", "verify_managed_native_boundaries",
    "verify_reconstruction", "verify_unknown_resolution", "xrefs",
    # Laufende Ziele passiv beobachten (kein Ausführen, kein Navigieren, keine Eingaben)
    "capture_adb_screen", "capture_web_screenshot", "collect_adb_bugreport", "discover_webmcp_tools",
    "inspect_adb_client", "inspect_adb_device", "inspect_adb_display", "inspect_adb_package",
    "inspect_adb_window", "inspect_electron_page", "inspect_web_event_listeners",
    "inspect_web_network_capture", "inspect_web_page", "list_adb_devices", "list_adb_directory",
    "list_adb_features", "list_adb_packages", "list_adb_processes", "list_adb_services",
    "list_browser_targets", "list_cutter_sessions", "list_electron_targets", "list_frida_devices",
    "list_frida_processes", "list_javascript_runtime_targets", "observe_javascript_runtime",
    "observe_native_ui", "observe_web_session", "read_adb_logcat", "read_adb_setting",
    "resolve_adb_packages", "frida_session_status", "gdb_session_status", "rizin_debug_session_status",
    # Nur REAs eigene Analysedaten (Kommentare, Namen, Lesezeichen, Sitzungen)
    "annotate_native_function", "close_binary", "close_frida_session", "close_gdb_session",
    "close_rizin_debug_session", "comment", "goto_address", "import_evidence_bundle",
    "import_managed_reconstruction", "inline_comment", "next_address", "open_binary", "open_jeb_project",
    "prev_address", "record_unknown", "set_address_name", "set_addresses_names", "set_bookmark",
    "set_comment", "set_inline_comment", "unset_bookmark", "update_unknown",
    # Neue Dateien in neuen Ordnern, überschreibt nichts (Gerät bleibt unverändert)
    "decode_android_resources", "export_web_scripts", "extract_artifact", "extract_firmware",
    "pull_adb_file", "pull_adb_package",
})
REA_CONDITIONAL = {"export_evidence_bundle"}  # erlaubt, außer mit overwrite=true

FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "NotebookRead"}
SEARCH_TOOLS = {"Glob", "Grep", "LS"}
SHELL_TOOLS = {"Bash", "PowerShell", "Monitor"}  # führen Befehle aus: gleiche Regeln wie Bash
# Eingebaute Werkzeuge, die weder Dateien schreiben, noch Befehle ausführen, noch ins Netz gehen.
# Alles andere, was nicht ausdrücklich geprüft wird (auch WebFetch und WebSearch), braucht eine Freigabe.
SAFE_BUILTINS = {
    "TodoWrite", "TodoRead", "Task", "Agent", "Skill", "SlashCommand",
    "ToolSearch", "ExitPlanMode", "EnterPlanMode", "BashOutput", "TaskOutput", "KillShell", "KillBash",
    "TaskStop", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "ListMcpResourcesTool",
    "ReadMcpResourceTool", "AskUserQuestion", "SendMessage", "SubagentHandback",
}


def fingerprint(tool_name: str, tool_input: dict) -> str:
    raw = tool_name + "\n" + json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()  # volle 256 Bit, kein Geburtstagsangriff


def _mcp_parts(tool_name: str) -> tuple[str, str] | None:
    """('server', 'werkzeug') für mcp__<server>__<werkzeug>, sonst None."""
    parts = tool_name.split("__")
    if len(parts) >= 3 and parts[0] == "mcp":
        return parts[1].lower(), parts[-1]
    return None


def _is_rea(server: str) -> bool:
    return server == "rea" or server.endswith("_rea")


# ---------- Repo-Selbstschutz über aufgelöste Pfade ----------
def _resolve(path: str, base: Path | None) -> Path | None:
    try:
        p = Path(os.path.expandvars(os.path.expanduser(path.strip().strip("'\""))))
        if not p.is_absolute():
            p = (base or Path.cwd()) / p
        return p.resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def repo_check(path: str, base: Path | None) -> str | None:
    """'block', wenn der Pfad im WAMO-Repo liegt; 'approval', wenn ein Platzhalter (*, ?, [) im
    Pfad auf das Repo zeigen könnte; sonst None."""
    if not path or not path.strip():
        return None
    norm = path.replace("\\", "/")
    if WILDCARD.search(norm):
        prefix = norm[: WILDCARD.search(norm).start()]
        prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        root = _resolve(prefix or ".", base)
        if root is None:
            return "approval"
        if root.is_relative_to(REPO_ROOT):
            return "block"
        if REPO_ROOT.is_relative_to(root):
            return "approval"
        return None
    resolved = _resolve(norm, base)
    if resolved is None:
        return "approval"
    return "block" if resolved.is_relative_to(REPO_ROOT) else None


def _looks_like_path(token: str) -> bool:
    return ("/" in token or token.startswith(("~", ".")) or re.match(r"^[A-Za-z]:", token) is not None
            or "$HOME" in token or "%USERPROFILE%" in token.upper())


def shell_repo_check(cmd: str, base: Path | None) -> str | None:
    """Prüft jeden pfadartigen Teil eines Befehls gegen REPO_ROOT (Quoting wird wie in der Shell
    aufgelöst, w''amo ergibt wamo). Ein cd innerhalb der Befehlszeile wird nicht nachverfolgt."""
    if str(REPO_ROOT).lower().replace("\\", "/") in cmd.lower().replace("\\", "/"):
        return "block"
    result = None
    for segment in _SEGMENTS.split(cmd.replace("\\", "/")):
        for token in _tokens(segment):
            for part in (token, token.split("=", 1)[-1]):
                if _looks_like_path(part):
                    hit = repo_check(part, base)
                    if hit == "block":
                        return "block"
                    result = result or hit
    return result


def decide_rea(name: str, tool_input: dict) -> tuple[str, str]:
    if name in REA_APPROVAL:
        return "approval", REA_APPROVAL[name]
    if name in REA_CONDITIONAL:
        if tool_input.get("overwrite") is True:
            return "approval", "REA überschreibt eine vorhandene Datei"
        return "allow", ""
    if name in REA_ALLOWED:
        return "allow", ""
    return "approval", f"Unbekanntes REA-Werkzeug {name}"  # fail-safe


def decide_mcp_service(service: str, name: str) -> tuple[str, str]:
    """Supabase, Vercel, GitHub, Jira: Gesperrtes sperren, Lesendes erlauben, alles andere freigeben lassen."""
    if MCP_BLOCKED[service].search(name):
        return "block", f"{service} {name} ist gesperrt"
    base = name.lower()
    for prefix in MCP_NAME_PREFIXES.get(service, ()):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    readable = (base.startswith(MCP_READ_PREFIXES) and not MCP_READ_EXCEPTIONS.search(name)) \
        or name in MCP_READ_EXTRA[service]
    if readable:
        return "allow", ""
    return "approval", f"{service.capitalize()} {name} (schreibend oder unbekannt)"


def decide_shell(cmd: str, project_dir: Path | None = None) -> tuple[str, str]:
    if len(cmd) > MAX_COMMAND_CHARS:
        return "approval", "Befehl ist zu lang für die automatische Prüfung"
    for pattern, why in SHELL_BLOCKED:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "block", why
    if SECRET_PATH_RE.search(cmd) or SECRET_PATH_RE.search(cmd.replace("'", "").replace('"', "")):
        return "block", SECRET_REASON
    repo = shell_repo_check(cmd, project_dir)
    if repo == "block":
        return "block", SELF_BLOCK_REASON
    git = decide_git(cmd)
    if git is not None:
        return git
    for pattern, why in SHELL_APPROVAL:
        if re.search(pattern, cmd, re.IGNORECASE):
            return "approval", why
    upload = decide_upload(cmd)
    if upload is not None:
        return upload
    if repo == "approval":
        return "approval", "Pfad mit Platzhalter könnte auf das WAMO-Repo zeigen"
    return "allow", ""


def decide(tool_name: str, tool_input: dict, project_dir: Path | None) -> tuple[str, str]:
    """('allow'|'approval'|'block', Begründung)."""
    if tool_input is None:
        tool_input = {}
    if not isinstance(tool_input, dict):
        raise TypeError("tool_input muss ein Objekt sein")

    if tool_name in FILE_TOOLS or tool_name in READ_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if SECRET_PATH_RE.search(path):
            return "block", SECRET_REASON
        repo = repo_check(path, project_dir)
        if repo == "block":
            return "block", SELF_BLOCK_REASON
        if tool_name in FILE_TOOLS:
            if project_dir is None:
                return "block", "Projektordner unbekannt, Schreiben gesperrt"
            resolved = _resolve(path, project_dir)
            if resolved is None or not resolved.is_relative_to(project_dir.resolve()):
                return "block", f"Schreiben nur im Projektordner {project_dir}"
            if PROTECTED_WRITE_RE.search(str(resolved).replace("\\", "/")):
                return "block", "Git- und Claude-Einstellungen im Projekt ändern ist gesperrt"
        if repo == "approval":
            return "approval", "Pfad mit Platzhalter könnte auf das WAMO-Repo zeigen"
        return "allow", ""

    if tool_name in SEARCH_TOOLS:
        path = str(tool_input.get("path") or "")
        targets = [path, str(tool_input.get("pattern") or ""), str(tool_input.get("glob") or "")]
        if any(SECRET_PATH_RE.search(t) for t in targets):
            return "block", SECRET_REASON
        base = _resolve(path, project_dir) if path else project_dir
        checks = [repo_check(path, project_dir)]
        if tool_name == "Glob":  # das Muster ist bei Glob selbst ein Pfad
            checks.append(repo_check(str(tool_input.get("pattern") or ""), base))
        if "block" in checks:
            return "block", SELF_BLOCK_REASON
        if "approval" in checks:
            return "approval", "Pfad mit Platzhalter könnte auf das WAMO-Repo zeigen"
        return "allow", ""

    if tool_name in SHELL_TOOLS:
        return decide_shell(str(tool_input.get("command", "")), project_dir)

    mcp = _mcp_parts(tool_name)
    if mcp is None:
        if tool_name in SAFE_BUILTINS:
            return "allow", ""
        return "approval", f"Werkzeug {tool_name} braucht eine Freigabe"  # fail-safe (auch WebFetch)
    server, name = mcp

    if _is_rea(server):
        return decide_rea(name, tool_input)
    for service in ("supabase", "vercel", "github", "jira"):
        if service in server:
            return decide_mcp_service(service, name)
    return "approval", f"Unbekannter MCP-Dienst {server}"  # fail-safe


def write_json_atomic(path: Path, data: object) -> None:
    """Schreibt erst in eine temporäre Datei daneben und ersetzt dann (kein halber Zustand)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _granted(fp: str, approvals_path: Path | None) -> int:
    """Wie oft der Fingerabdruck in approvals.json steht (zweimal freigegeben heißt zweimal darin)."""
    if not approvals_path or not approvals_path.is_file():
        return 0
    try:
        approved = json.loads(approvals_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(1 for a in approved if a == fp) if isinstance(approved, list) else 0


def _used_path(approvals_path: Path) -> Path:
    return approvals_path.with_name(approvals_path.name + ".used")


def _approval_open(fp: str, approvals_path: Path | None) -> bool:
    """Vorher (PreToolUse): ist eine Freigabe für genau diesen Aufruf noch offen? Liest nur."""
    granted = _granted(fp, approvals_path)
    if not granted:
        return False
    try:
        used = _used_path(approvals_path).read_text(encoding="utf-8").split()
    except FileNotFoundError:
        used = []
    return used.count(fp) < granted


def _record_use(fp: str, approvals_path: Path | None) -> bool:
    """Nachher (PostToolUse und PostToolUseFailure): der freigegebene Schritt lief, die Freigabe ist
    verbraucht. Erst hier, nicht schon beim Durchlassen: sperrt ein anderer Hook (etwa ein ECC-Hook) den
    Aufruf, bleibt die Freigabe für den nächsten identischen Versuch bestehen. approvals.json wird nie
    umgeschrieben (in der Sandbox auf Sokrates-2 ist sie schreibgeschützt), verbraucht wird zeilenweise
    in approvals.json.used."""
    granted = _granted(fp, approvals_path)
    if not granted:
        return False
    with open(_used_path(approvals_path), "a+", encoding="utf-8") as f:
        _lock(f)
        f.seek(0)
        if f.read().split().count(fp) >= granted:
            return False
        f.seek(0, os.SEEK_END)
        f.write(fp + "\n")
    return True


def _lock(f) -> None:
    """Exklusive Sperre bis zum Schließen (parallele Hooks); unter Windows ohne Sperre wie bisher."""
    try:
        import fcntl
    except ImportError:
        return
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def parse_args(argv: list[str]) -> tuple[Path | None, Path | None, bool]:
    """[--nachher] --project-dir <pfad> --job-dir <pfad> (Ordner optional; ohne Projektordner kein
    Schreiben). --nachher: Aufruf als PostToolUse-Hook, verbucht nur ausgeführte Freigaben."""
    values: dict[str, str] = {}
    nachher = False
    it = iter(argv)
    for arg in it:
        if arg in ("--project-dir", "--job-dir"):
            values[arg] = next(it, "")
        elif arg == "--nachher":
            nachher = True
        else:
            raise ValueError(f"unbekanntes Argument {arg!r}")
    project, job = values.get("--project-dir"), values.get("--job-dir")
    return (Path(project) if project else None), (Path(job) if job else None), nachher


def _run(raw: str, project_dir: Path | None, job_dir: Path | None, nachher: bool = False) -> int:
    if nachher:
        return _run_nachher(raw, project_dir, job_dir)
    try:
        event = json.loads(raw or "{}")
    except json.JSONDecodeError:
        _deny("Wächter: unlesbare Anfrage")
        return 0
    if not isinstance(event, dict):
        raise TypeError("Hook-Ereignis muss ein JSON-Objekt sein")
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input")
    decision, why = decide(tool_name, tool_input, project_dir)

    if decision == "allow":
        return 0
    if decision == "block":
        _deny(f"GESPERRT: {why}. Das macht der Auftraggeber selbst. Nicht umgehen, nicht anders versuchen.")
        return 0

    fp = fingerprint(tool_name, tool_input or {})
    if job_dir is not None:
        if _approval_open(fp, job_dir / "approvals.json"):
            return 0  # verbraucht wird erst nachher (_run_nachher), wenn der Schritt wirklich lief
        with open(job_dir / "pending.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": fp, "grund": why, "tool": tool_name, "input": tool_input or {}},
                               ensure_ascii=False) + "\n")
    _deny(
        f"FREIGABE NÖTIG ({why}). Ein Admin muss das in der Oberfläche freigeben. Mach alles andere "
        "fertig, führe diesen Schritt NICHT anders aus und nenne ihn am Ende deiner Antwort."
    )
    return 0


def _run_nachher(raw: str, project_dir: Path | None, job_dir: Path | None) -> int:
    """PostToolUse/PostToolUseFailure: der Aufruf lief. War er freigabepflichtig, ist die Freigabe jetzt
    verbraucht. Kann nichts mehr sperren; ein Fehler endet mit Exit 1 (Claude Code zeigt ihn nur an)."""
    event = json.loads(raw or "{}")
    if not isinstance(event, dict) or job_dir is None or not tool_ran(event):
        return 0
    tool_name, tool_input = str(event.get("tool_name", "")), event.get("tool_input")
    if decide(tool_name, tool_input, project_dir)[0] == "approval":
        _record_use(fingerprint(tool_name, tool_input or {}), job_dir / "approvals.json")
    return 0


HOOK_BLOCKED_RE = re.compile(r"^\s*(Error:\s*)?(<tool_use_error>\s*)?PreToolUse:\S* hook (error|blocked)")


def tool_ran(event: dict) -> bool:
    """Lief der Aufruf wirklich? Sperrt ein PreToolUse-Hook per Exit-Code (etwa ein ECC-Hook), meldet
    Claude Code trotzdem PostToolUse, mit "Error: PreToolUse:<Werkzeug> hook error: ..." als Antwort.
    Im Zweifel gilt der Aufruf als gelaufen (Freigabe verbraucht, nie doppelt nutzbar)."""
    for key in ("tool_response", "error"):
        value = event.get(key)
        if isinstance(value, str) and HOOK_BLOCKED_RE.match(value):
            return False
    return True


def _watchdog() -> None:
    sys.stderr.write("GESPERRT: Der Wächter hat zu lange gebraucht. Diesen Schritt nicht ausführen.\n")
    sys.stderr.flush()
    os._exit(EXIT_BLOCK)


def main(argv: list[str] | None = None) -> int:
    timer = threading.Timer(WATCHDOG_SECS, _watchdog)
    timer.daemon = True
    timer.start()
    try:
        project_dir, job_dir, nachher = parse_args(sys.argv[1:] if argv is None else argv)
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        return _run(raw, project_dir, job_dir, nachher)
    except BaseException as e:  # fail-closed: lieber blockieren als durchlassen
        print(f"GESPERRT: Der Wächter ist auf einen Fehler gelaufen ({type(e).__name__}). "
              "Diesen Schritt nicht ausführen und nicht umgehen.", file=sys.stderr)
        return EXIT_BLOCK
    finally:
        timer.cancel()


if __name__ == "__main__":
    # stdout/stderr als UTF-8, damit Umlaute in den Meldungen unter Windows nicht scheitern
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
