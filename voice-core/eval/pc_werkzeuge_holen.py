r"""Schreibt die Schemas der PC-Werkzeuge (Name, Beschreibung, Parameter) nach eval/pc_werkzeuge.json.

Der Testsatz startet den PC-Host nicht und ruft keine echten PC-Werkzeuge auf. Damit das Sprachmodell
trotzdem dieselben Werkzeuge sieht wie im Gespräch, liest dieses Skript die Schemas einmal aus
host/wamo_host.py (FastMCP list_tools, ohne Server und ohne Aufruf) und filtert sie wie der Voice-Core
(LLM_TOOLS in voice-core/pc_host.py).

Ausführen mit dem Python des Hosts (dort ist mcp installiert), im Hauptordner des Repos:
    host\.venv\Scripts\python.exe voice-core\eval\pc_werkzeuge_holen.py
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOST = REPO / "host"
ZIEL = Path(__file__).with_name("pc_werkzeuge.json")


def llm_tools() -> list[str]:
    baum = ast.parse((REPO / "voice-core" / "pc_host.py").read_text(encoding="utf-8"))
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and any(getattr(t, "id", "") == "LLM_TOOLS" for t in knoten.targets):
            return list(ast.literal_eval(knoten.value))
    raise SystemExit("LLM_TOOLS nicht in voice-core/pc_host.py gefunden")


async def main() -> None:
    sys.path.insert(0, str(HOST))
    import wamo_host  # noqa: PLC0415  (erst nach dem Pfad)

    erlaubt = llm_tools()
    werkzeuge = []
    for tool in await wamo_host.mcp.list_tools():
        if tool.name not in erlaubt:
            continue
        schema = tool.inputSchema or {}
        werkzeuge.append({
            "name": tool.name,
            "description": tool.description or "",
            "properties": schema.get("properties", {}),
            "required": schema.get("required", []),
        })
    werkzeuge.sort(key=lambda w: erlaubt.index(w["name"]))
    fehlend = sorted(set(erlaubt) - {w["name"] for w in werkzeuge})
    if fehlend:
        raise SystemExit(f"Im Host fehlen Werkzeuge aus LLM_TOOLS: {fehlend}")
    ZIEL.write_text(json.dumps(werkzeuge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(werkzeuge)} Werkzeuge nach {ZIEL.relative_to(REPO)} geschrieben")


if __name__ == "__main__":
    asyncio.run(main())
