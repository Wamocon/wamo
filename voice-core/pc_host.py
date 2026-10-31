"""Verbindung zum WAMO-Host (Werkzeuge am Windows-PC) per MCP.

Zwei Wege, gewählt über die Umgebung:
  * Unterwegs (Standard): Der Voice-Core läuft auf dem PC und startet host/wamo_host.py als Unterprozess über
    stdio. Der Host ist nicht im Netz erreichbar, und der Unterprozess erbt nur die Standard-Umgebung von MCP
    (PATH, USERPROFILE, ...), keine API-Schlüssel aus .env.
  * Büro (WAMO_PC_HOST_SOCKET gesetzt): Der Kern läuft auf Sokrates-2 und erreicht den Host über den
    SSH-Rücktunnel (Unix-Socket, pc_tunnel.py) mit dem Bearer-Token aus WAMO_PC_HOST_TOKEN. Weil dort mehrere
    Profile denselben Kern nutzen, bekommt die Werkzeuge dieses PCs nur das eine Profil aus WAMO_PC_HOST_PROFILE
    (user_id der Person, der der PC gehört), und steuern darf immer nur eine Sitzung (pc_lease_nehmen). Fehlt
    die Bindung oder das Token, gibt es keine PC-Werkzeuge (fail closed).

Tunnel je Gerät (WAMO_PC_REGISTER=an, nur Büro, pc_geraet.py): Jeder Laptop hat einen eigenen Benutzer und Socket auf
Sokrates-2. Der Kern ordnet die Sitzung IHREM Laptop zu, nur über das Profil und das Geräteregister (pc_zugang): kein
Werkzeugparameter, keine Liste, keine Bindung in der Umgebung, kein Ausweichen auf den alten Tunnel oder stdio. Die
Sperre gilt dann je Gerät. Ohne den Schalter (Standard) bleibt der alte Weg unverändert.

Risikoklassen und Freigaben prüft der Host selbst. Im alten Weg bekommen nur Profile mit der Rolle admin die
PC-Werkzeuge, im Tunnel je Gerät auch Mitglieder, sobald WAMO_PC_MITGLIEDER=an gesetzt ist. WAMO_PC_HOST=off schaltet
sie ganz ab.

Freigaben kritischer PC-Aktionen (Host: status=needs_approval): Nur pc_freigabe_entscheiden ruft nach einem Klick in
der Oberfläche das Host-Werkzeug freigabe_entscheiden auf. Es steht nicht in LLM_TOOLS (das Sprachmodell sieht es
weder lokal noch über den Tunnel) und braucht ein zweites Geheimnis neben dem Bearer-Token: lokal erzeugt dieser Kern
es je Host-Start und gibt es nur als Umgebung WAMO_HOST_FREIGABE an den Unterprozess, im Büro steht es in
WAMO_PC_HOST_FREIGABE (/etc/wamo/core.env, am PC in %LOCALAPPDATA%\\WAMO\\host-freigabe). Der Schlüssel landet
nie im Log, im Prompt oder in einer Antwort.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import sys
import weakref
from pathlib import Path

from loguru import logger
from mcp import StdioServerParameters

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.mcp_service import MCPClient

from memory import normalize_uuid
from pc_geraet import PcZugang, pc_zugang_fuer, register_modus
from pc_tunnel import (
    AUFRUFE_JE_MINUTE_GERAET,
    MAX_ANTWORT_BYTES_GERAET,
    PC_NICHT_ERREICHBAR,
    PcNichtErreichbar,
    TunnelHost,
)

HOST_DIR = Path(__file__).resolve().parent.parent / "host"
HOST_SCRIPT = HOST_DIR / "wamo_host.py"
CONNECT_TIMEOUT_SECS = 20
LLM_TOOLS = [
    "webseite_oeffnen", "programm_oeffnen", "powershell_lesen", "system_info", "dark_mode",
    "fenster_auflisten", "fenster_fokussieren", "tastatur_tippen", "tasten_druecken", "maus_scrollen", "oeffnen_mit",
    "ordner_anlegen", "elemente_auflisten", "element_klicken", "element_text_setzen", "element_lesen",
    "git_klonen", "github_repo_anlegen",
]


APPROVAL_ID_RE = re.compile(r"[0-9a-f]{16}")
FREIGABE_SCHLUESSEL_RE = re.compile(r"[A-Za-z0-9_-]{43,512}")
# Freigabe-Schlüssel je Host-Verbindung (nicht am Objekt, damit er in keiner Darstellung des Clients auftaucht)
_FREIGABE_SCHLUESSEL: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def freigabe_schluessel(client: object) -> str | None:
    """Zweites Geheimnis für die Freigaben dieses Hosts oder None (dann gibt es keinen Freigabeweg). Im Tunnel je Gerät
    steht ein Geheimnisse-Objekt, das nach einem Host-Neustart neu gelesen wird."""
    try:
        wert = _FREIGABE_SCHLUESSEL.get(client)
    except TypeError:
        return None
    return getattr(wert, "freigabe", wert)


def _buero_freigabe_schluessel() -> str | None:
    wert = (os.getenv("WAMO_PC_HOST_FREIGABE") or "").strip()
    if not wert:
        return None
    if not FREIGABE_SCHLUESSEL_RE.fullmatch(wert):
        logger.error("WAMO_PC_HOST_FREIGABE ist ungültig: Freigaben für PC-Aktionen bleiben aus")
        return None
    return wert


def pc_host_enabled() -> bool:
    return os.getenv("WAMO_PC_HOST", "on").strip().lower() not in ("off", "0", "false", "aus")


def tunnel_socket() -> str | None:
    """Pfad des Tunnel-Sockets (Betriebsart Büro) oder None (lokal über stdio)."""
    value = (os.getenv("WAMO_PC_HOST_SOCKET") or "").strip()
    return value or None


def tunnel_profil() -> str | None:
    """Die eine user_id, die den PC über den Tunnel steuern darf (WAMO_PC_HOST_PROFILE). Mehrere Werte, ein
    ungültiger Wert oder keiner: niemand (ein PC gehört genau einer Person)."""
    raw = (os.getenv("WAMO_PC_HOST_PROFILE") or "").strip()
    if not raw:
        return None
    uid = normalize_uuid(raw)
    if uid is None:
        logger.error("WAMO_PC_HOST_PROFILE ist keine einzelne gültige user_id: PC-Werkzeuge bleiben aus")
    return uid


def buero() -> bool:
    """Läuft dieser Kern als Büro-Kern (wamo-core.service auf Sokrates-2)?"""
    return (os.getenv("WAMO_BETRIEB") or "").strip().lower() == "buero"


async def pc_zugang(identity) -> PcZugang | None:
    """Tunnel je Gerät: der Zugang dieser Sitzung zu IHREM Laptop oder None (keine PC-Werkzeuge). Wirft nie."""
    if not pc_host_enabled():
        return None
    return await pc_zugang_fuer(identity)


def pc_host_fuer(identity) -> bool:
    """Bekommt diese Sitzung PC-Werkzeuge (alter Weg)? Nur admin, Host an; im Büro zusätzlich Token und Profilbindung.
    Im Tunnel je Gerät gilt stattdessen pc_zugang; dieser Weg liefert dort nie True."""
    if register_modus():
        return False
    if not (identity.is_admin and pc_host_enabled()):
        return False
    if tunnel_socket() is None:
        if buero():  # Büro ohne Tunnel: nie auf den lokalen Weg ohne Profilbindung ausweichen
            logger.error("PC-Werkzeuge aus: WAMO_BETRIEB=buero ohne WAMO_PC_HOST_SOCKET")
            return False
        return True
    if not (os.getenv("WAMO_PC_HOST_TOKEN") or "").strip():
        logger.error("PC-Werkzeuge aus: WAMO_PC_HOST_TOKEN fehlt")
        return False
    if identity.user_id != tunnel_profil():
        logger.info("PC-Werkzeuge aus: Profil ist nicht an diesen PC gebunden (WAMO_PC_HOST_PROFILE)")
        return False
    return True


# Höchstens eine Sitzung steuert den PC über den Tunnel (zweites Gerät, zweiter Tab). Lokal hat jeder PC seinen
# eigenen Kern, dort gilt die Sperre nicht. Im alten Tunnel gibt es eine Sperre für den einen PC, im Tunnel je Gerät
# eine je Gerät (Schlüssel = device_id): zwei Profile mit zwei Laptops steuern gleichzeitig, nie zwei Sitzungen
# denselben Laptop.
_lease: object | None = None
_lease_je_geraet: dict[str, object] = {}


def tunnel_modus() -> bool:
    return tunnel_socket() is not None or register_modus()


def pc_lease_nehmen(sitzung: object, geraet_id: str | None = None) -> bool:
    """True, wenn diese Sitzung den PC steuern darf (frei oder schon ihre)."""
    global _lease
    if not tunnel_modus():
        return True
    if geraet_id is not None:
        if _lease_je_geraet.get(geraet_id) in (None, sitzung):
            _lease_je_geraet[geraet_id] = sitzung
            return True
        return False
    if _lease is None or _lease is sitzung:
        _lease = sitzung
        return True
    return False


def pc_lease_haelt(sitzung: object, geraet_id: str | None = None) -> bool:
    """Hält diese Sitzung die Sperre? Lokal immer (jede Sitzung hat ihren eigenen Host über stdio)."""
    if not tunnel_modus():
        return True
    if geraet_id is not None:
        return _lease_je_geraet.get(geraet_id) is sitzung
    return _lease is sitzung


def pc_lease_freigeben(sitzung: object, geraet_id: str | None = None) -> None:
    global _lease
    if geraet_id is not None:
        if _lease_je_geraet.get(geraet_id) is sitzung:
            del _lease_je_geraet[geraet_id]
    elif _lease is sitzung:
        _lease = None


def ist_tunnel(client) -> bool:
    return isinstance(client, TunnelHost)


def _venv_python() -> Path:
    if sys.platform == "win32":
        return HOST_DIR / ".venv" / "Scripts" / "python.exe"
    return HOST_DIR / ".venv" / "bin" / "python"


def host_client(zugang: PcZugang | None = None) -> MCPClient | TunnelHost | None:
    if not pc_host_enabled():
        return None
    if zugang is not None:
        # Tunnel je Gerät: nur der Socket dieses Geräts, Gegenstelle per SO_PEERCRED geprüft, Kennung und Profil in jeder
        # Anfrage, Token wird nach einem 401 neu gelesen. bildschirmfoto, maus_klicken usw. ruft nur der Kern auf.
        tunnel = TunnelHost(zugang.socket, zugang.geheimnisse.token, tools_filter=LLM_TOOLS, erwartete_uid=zugang.uid,
                            geraet=zugang.geraet.kennung, profil=zugang.user_id, nachladen=zugang.nachladen,
                            max_antwort_bytes=MAX_ANTWORT_BYTES_GERAET, aufrufe_je_minute=AUFRUFE_JE_MINUTE_GERAET)
        _FREIGABE_SCHLUESSEL[tunnel] = zugang.geheimnisse
        return tunnel
    if register_modus():
        return None  # Büro mit Register: ohne Zugang kein PC, nie zurück auf den alten Weg
    socket_path = tunnel_socket()
    if socket_path is not None:
        token = (os.getenv("WAMO_PC_HOST_TOKEN") or "").strip()
        if not token:
            return None
        # bildschirmfoto, maus_klicken, sperre_pruefen und freigabe_entscheiden ruft nur der Kern auf, nie das Modell
        tunnel = TunnelHost(socket_path, token, tools_filter=LLM_TOOLS)
        if schluessel := _buero_freigabe_schluessel():
            _FREIGABE_SCHLUESSEL[tunnel] = schluessel
        return tunnel
    if buero():
        return None
    script = str(HOST_SCRIPT)
    venv_python = _venv_python()
    if venv_python.is_file():
        # Direkt das Python des Hosts: startet in ~1 s statt ~6 s über "uv run"
        command, args = str(venv_python), [script, "--stdio"]
    else:
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        command, args = uv, ["run", "--project", str(HOST_DIR), "python", script, "--stdio"]
    # Freigabe-Schlüssel je Host-Start; MCP legt env über seine Standard-Umgebung, der Host bekommt nur ihn dazu
    schluessel = secrets.token_urlsafe(32)
    params = StdioServerParameters(command=command, args=args, cwd=str(HOST_DIR), env={"WAMO_HOST_FREIGABE": schluessel})
    # bildschirmfoto, maus_klicken, sperre_pruefen und freigabe_entscheiden ruft nur der Kern auf, nie das Modell
    client = MCPClient(server_params=params, tools_filter=LLM_TOOLS)
    _FREIGABE_SCHLUESSEL[client] = schluessel
    return client


async def host_tools(client: MCPClient | TunnelHost | None) -> ToolsSchema | None:
    """Werkzeuge des Hosts; None, wenn er aus ist oder nicht antwortet (Gespräch läuft trotzdem)."""
    if client is None:
        return None
    try:
        tools = await asyncio.wait_for(client.tools(), CONNECT_TIMEOUT_SECS)
        logger.info("PC-Host verbunden: {}", [t.name for t in tools.standard_tools])
        return tools
    except PcNichtErreichbar as e:
        logger.warning("PC-Host nicht erreichbar: {}", e)
        return None
    except Exception as e:  # PC-Werkzeuge sind optional
        logger.warning("PC-Host nicht verfügbar: {}", e)
        return None


# ---------- Gesperrter Bildschirm ----------
# Werkzeuge, die der Host bei Sperre mit status=locked ablehnt (wamo_host._gesperrt); ihr Erfolg beweist "entsperrt".
SPERR_WERKZEUGE = frozenset({
    "fenster_fokussieren", "tastatur_tippen", "tasten_druecken", "maus_scrollen", "bildschirm_ansehen",
    "auf_bildschirm_klicken", "element_klicken", "element_text_setzen", "ordner_anlegen", "git_klonen",
})
SPERR_PROBE = "sperre_pruefen"  # nur für den Kern, nie in LLM_TOOLS


def _als_dict(result: object) -> dict | None:
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sperre_aus_ergebnis(name: str, result: object) -> bool | None:
    """Was ein Werkzeugergebnis über die Sperre sagt: True gesperrt, False entsperrt, None nichts Belastbares."""
    data = _als_dict(result)
    if data is None:
        return None
    if data.get("status") == "locked":
        return True
    if name == "system_info" and isinstance(data.get("gesperrt"), bool):
        return data["gesperrt"]
    if name in SPERR_WERKZEUGE and data.get("status") == "success":
        return False
    return None


async def pc_sperre_pruefen(client: MCPClient | TunnelHost | None) -> bool | None:
    """Fragt den Host, ob der Bildschirm gesperrt ist. None, wenn es sich nicht feststellen lässt (Host weg, älterer
    Host ohne Probe, kaputte Antwort); wirft nie."""
    if client is None:
        return None
    try:
        data = _als_dict(await call_host_tool(client, SPERR_PROBE, {}))
    except Exception as e:  # noqa: BLE001 (Probe ist nur eine Anzeige)
        logger.debug("Sperr-Probe: {}", type(e).__name__)
        return None
    gesperrt = data.get("gesperrt") if data else None
    return gesperrt if isinstance(gesperrt, bool) else None


async def pc_freigabe_entscheiden(client: MCPClient | TunnelHost | None, approval_id: object, ja: bool) -> dict:
    """Entscheidung aus der Oberfläche an den Host (der einzige Weg, eine PC-Aktion freizugeben). Ohne bekannten
    Schlüssel oder mit unsinniger ID wird der Host gar nicht gefragt. Liefert das Ergebnis des Hosts als dict."""
    aid = approval_id if isinstance(approval_id, str) else ""
    schluessel = freigabe_schluessel(client) if client is not None else None
    if not APPROVAL_ID_RE.fullmatch(aid):
        return {"status": "error", "summary": "Unbekannte Freigabe."}
    if not schluessel:
        return {"status": "error", "summary": "Freigaben sind für diesen PC nicht eingerichtet."}
    try:
        text = await call_host_tool(client, "freigabe_entscheiden",
                                    {"approval_id": aid, "ja": ja is True, "schluessel": schluessel})
    except PcNichtErreichbar:
        return dict(PC_NICHT_ERREICHBAR)
    except Exception as e:  # noqa: BLE001 (Antwort kaputt oder Host weg: klares Ergebnis statt Ausnahme)
        logger.warning("PC-Freigabe: Host antwortet nicht verwertbar ({})", type(e).__name__)
        return {"status": "error", "summary": "Der PC hat keine verwertbare Antwort geliefert."}
    return _als_dict(text) or {"status": "error", "summary": "Der PC hat keine verwertbare Antwort geliefert."}


async def call_host_tool(client: MCPClient | TunnelHost, name: str, arguments: dict) -> str:
    """Ruft ein Host-Werkzeug direkt auf, auch wenn es nicht im tools_filter steht
    (bildschirmfoto, maus_klicken), und liefert den Text der Antwort.

    Über den Tunnel: TunnelHost.call_text (wirft PcNichtErreichbar). Lokal: Pipecat 1.12 hat dafür keine
    öffentliche Methode, MCPClient bietet nur tools() für das Sprachmodell. Die private Methode _call_tool_text
    wird deshalb nur hier benutzt. Bei einem Pipecat-Update genügt es, diese eine Stelle anzupassen.
    """
    if isinstance(client, TunnelHost):
        return await client.call_text(name, arguments)
    return await client._call_tool_text(name, arguments)
