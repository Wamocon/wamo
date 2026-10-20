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

Risikoklassen und Freigaben prüft der Host selbst. Nur Profile mit der Rolle admin bekommen die PC-Werkzeuge.
WAMO_PC_HOST=off schaltet sie ganz ab.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

from loguru import logger
from mcp import StdioServerParameters

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.mcp_service import MCPClient

from memory import normalize_uuid
from pc_tunnel import PcNichtErreichbar, TunnelHost

HOST_DIR = Path(__file__).resolve().parent.parent / "host"
HOST_SCRIPT = HOST_DIR / "wamo_host.py"
CONNECT_TIMEOUT_SECS = 20
LLM_TOOLS = [
    "webseite_oeffnen", "programm_oeffnen", "powershell_lesen", "system_info", "dark_mode",
    "fenster_auflisten", "fenster_fokussieren", "tastatur_tippen", "tasten_druecken", "maus_scrollen", "oeffnen_mit",
]


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


def pc_host_fuer(identity) -> bool:
    """Bekommt diese Sitzung PC-Werkzeuge? Nur admin, Host an; im Büro zusätzlich Token und Profilbindung."""
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
# eigenen Kern, dort gilt die Sperre nicht.
_lease: object | None = None


def pc_lease_nehmen(sitzung: object) -> bool:
    """True, wenn diese Sitzung den PC steuern darf (frei oder schon ihre)."""
    global _lease
    if tunnel_socket() is None:
        return True
    if _lease is None or _lease is sitzung:
        _lease = sitzung
        return True
    return False


def pc_lease_freigeben(sitzung: object) -> None:
    global _lease
    if _lease is sitzung:
        _lease = None


def ist_tunnel(client) -> bool:
    return isinstance(client, TunnelHost)


def _venv_python() -> Path:
    if sys.platform == "win32":
        return HOST_DIR / ".venv" / "Scripts" / "python.exe"
    return HOST_DIR / ".venv" / "bin" / "python"


def host_client() -> MCPClient | TunnelHost | None:
    if not pc_host_enabled():
        return None
    socket_path = tunnel_socket()
    if socket_path is not None:
        token = (os.getenv("WAMO_PC_HOST_TOKEN") or "").strip()
        if not token:
            return None
        # bildschirmfoto und maus_klicken ruft nur der Voice-Core (screen.py) auf, nie das Sprachmodell
        return TunnelHost(socket_path, token, tools_filter=LLM_TOOLS)
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
    params = StdioServerParameters(command=command, args=args, cwd=str(HOST_DIR))
    # bildschirmfoto und maus_klicken ruft nur der Voice-Core (screen.py) auf, nie das Sprachmodell
    return MCPClient(server_params=params, tools_filter=LLM_TOOLS)


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
