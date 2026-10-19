"""Verbindung zum WAMO-Host (Werkzeuge am Windows-PC) per MCP über stdio.

Der Voice-Core startet host/wamo_host.py als Unterprozess. Dadurch ist der Host nicht im Netz
erreichbar, und der Unterprozess erbt nur die Standard-Umgebung von MCP (PATH, USERPROFILE, ...),
keine API-Schlüssel aus .env. Risikoklassen und Freigaben prüft der Host selbst.

Nur Profile mit der Rolle admin bekommen die PC-Werkzeuge (bot.py). WAMO_PC_HOST=off schaltet
sie ganz ab.
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

HOST_DIR = Path(__file__).resolve().parent.parent / "host"
HOST_SCRIPT = HOST_DIR / "wamo_host.py"
CONNECT_TIMEOUT_SECS = 20
LLM_TOOLS = [
    "webseite_oeffnen", "programm_oeffnen", "powershell_lesen", "system_info", "dark_mode",
    "fenster_auflisten", "fenster_fokussieren", "tastatur_tippen", "tasten_druecken", "maus_scrollen", "oeffnen_mit",
]


def pc_host_enabled() -> bool:
    return os.getenv("WAMO_PC_HOST", "on").strip().lower() not in ("off", "0", "false", "aus")


def _venv_python() -> Path:
    if sys.platform == "win32":
        return HOST_DIR / ".venv" / "Scripts" / "python.exe"
    return HOST_DIR / ".venv" / "bin" / "python"


def host_client() -> MCPClient | None:
    if not pc_host_enabled():
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


async def host_tools(client: MCPClient | None) -> ToolsSchema | None:
    """Werkzeuge des Hosts; None, wenn er aus ist oder nicht startet (Gespräch läuft trotzdem)."""
    if client is None:
        return None
    try:
        tools = await asyncio.wait_for(client.tools(), CONNECT_TIMEOUT_SECS)
        logger.info("PC-Host verbunden: {}", [t.name for t in tools.standard_tools])
        return tools
    except Exception as e:  # PC-Werkzeuge sind optional
        logger.warning("PC-Host nicht verfügbar: {}", e)
        return None


async def call_host_tool(client: MCPClient, name: str, arguments: dict) -> str:
    """Ruft ein Host-Werkzeug direkt auf, auch wenn es nicht im tools_filter steht
    (bildschirmfoto, maus_klicken), und liefert den Text der Antwort.

    Pipecat 1.12 hat dafür keine öffentliche Methode: MCPClient bietet nur tools() für das
    Sprachmodell. Die private Methode _call_tool_text wird deshalb nur hier benutzt. Bei einem
    Pipecat-Update genügt es, diese eine Stelle anzupassen.
    """
    return await client._call_tool_text(name, arguments)
