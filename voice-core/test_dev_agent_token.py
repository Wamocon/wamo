"""Ausgabe von Claude Code mit eigenem Zugang des Profils (dev_agent.py, Betriebsart sokrates2): Ein Token oder
Schlüssel in der Ausgabe (Prompt Injection im Repo) darf weder auf das Canvas noch in die Karten gelangen. Den Weg des
Zugangs zum Dispatcher (zweite stdin-Zeile) prüft test_dev_agent_sokrates2.py. SSH ist gemockt."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import dev_agent
from test_dev_agent_sokrates2 import KOLLEGIN, FakeProc, RemoteFlowTests, stream

TOKEN = "sk-ant-oat01-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99"
API = "sk-ant-api03-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99"


class AusgabeSchwaerzenTests(unittest.IsolatedAsyncioTestCase):
    # Aufbau (SSH-Attrappen, Zugangsdaten-Dateien) und Hilfen von RemoteFlowTests ohne deren Testfälle übernehmen
    asyncSetUp = RemoteFlowTests.asyncSetUp
    asyncTearDown = RemoteFlowTests.asyncTearDown
    remote = RemoteFlowTests.remote
    prepare_and_start = RemoteFlowTests.prepare_and_start

    async def test_ausgabe_von_claude_code_wird_geschwaerzt(self):
        self.memory.user_id = KOLLEGIN
        zeilen = stream()
        zeilen[-2] = {"type": "result", "result": f"Der Token ist {TOKEN} und der Schluessel {API}", "total_cost_usd": 0.1,
                      "session_id": "754fb6bb-4880-4fc6-bc55-ed512941f3d3"}
        self.start_procs.append(FakeProc(zeilen))
        with mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value={"typ": "oauth", "wert": TOKEN}):
            await self.prepare_and_start()
        canvas = [m for m in self.task.messages() if m.get("type") == "canvas"][-1]["item"]["text"]
        self.assertNotIn(TOKEN, canvas)
        self.assertNotIn(API, canvas)
        self.assertNotIn("sk-ant-oat01-GEHEIM", canvas)
        self.assertNotIn("sk-ant-api03-GEHEIM", json.dumps(self.task.messages(), default=str))


if __name__ == "__main__":
    unittest.main()
