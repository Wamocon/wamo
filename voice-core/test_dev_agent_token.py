"""Entwicklungsaufträge mit dem Claude-Code-Token der Person (dev_agent.py, Betriebsart sokrates2):
zweite stdin-Zeile für den Dispatcher, Modell je Zugang, kein Lauf ohne nutzbaren Token, kein Token in Ausgaben,
Aufrufen, Log oder Canvas. SSH ist gemockt (Attrappen aus test_dev_agent_sokrates2.py)."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from loguru import logger

import dev_agent
from test_dev_agent_sokrates2 import FakeProc, RemoteFlowTests, stream

TOKEN = "sk-ant-oat01-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99"


class TokenFlowTests(unittest.IsolatedAsyncioTestCase):
    # Aufbau (SSH-Attrappen, Zugangsdaten-Dateien) und Hilfen von RemoteFlowTests ohne deren Testfälle übernehmen
    asyncSetUp = RemoteFlowTests.asyncSetUp
    asyncTearDown = RemoteFlowTests.asyncTearDown
    remote = RemoteFlowTests.remote
    prepare_and_start = RemoteFlowTests.prepare_and_start

    def agent_mit_token(self, token=TOKEN):
        self.token_aufrufe = 0

        async def quelle():
            self.token_aufrufe += 1
            return token

        self.agent = dev_agent.DevAgent(memory=self.memory, auftraggeber="Erika", token_quelle=quelle)
        self.agent.attach(self.task)

    async def test_token_geht_als_zweite_stdin_zeile_und_nie_anders_wohin(self):
        self.agent_mit_token()
        meldungen: list[str] = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="DEBUG")
        try:
            proc = FakeProc(stream())
            self.start_procs.append(proc)
            job = await self.prepare_and_start()
        finally:
            logger.remove(sink)
        zeilen = proc.stdin.data.decode("utf-8").splitlines()
        self.assertEqual(len(zeilen), 2)
        kopf, token = json.loads(zeilen[0]), json.loads(zeilen[1])
        self.assertIs(kopf["token_folgt"], True)
        self.assertEqual(token, {"claude_code_token": TOKEN})
        self.assertNotIn("claude_code_token", kopf)
        self.assertNotIn(TOKEN, zeilen[0])
        self.assertEqual(kopf["modell"], "claude-sonnet-5-5")  # Abo: Standard Sonnet
        self.assertEqual(self.token_aufrufe, 1)  # je Lauf einmal geholt
        self.assertEqual(job.status, "fertig")
        # nirgends sonst: nicht in der ssh-Befehlszeile, in Umgebung-Argumenten, Log, Karten, Canvas, Meldungen, Schritten
        gesamt = json.dumps(self.calls) + "\n".join(meldungen) + json.dumps(self.task.messages(), default=str)
        gesamt += "\n".join(self.task.llm_texts()) + "\n".join(job.steps) + (job.result or "")
        self.assertNotIn(TOKEN, gesamt)
        self.assertNotIn(TOKEN[:20], gesamt)

    async def test_wunsch_des_betriebs_geht_dem_standard_vor(self):
        self.agent_mit_token()
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_MODELL": "claude-opus-5-5"}):
            await self.prepare_and_start()
        self.assertEqual(json.loads(proc.stdin.data.decode("utf-8").splitlines()[0])["modell"], "claude-opus-5-5")

    async def test_ohne_nutzbaren_token_startet_nichts(self):
        self.agent_mit_token(token=None)
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        job = await self.prepare_and_start()
        self.assertIn("nicht gestartet", job.status)
        self.assertEqual(self.remote("start"), [])  # kein ssh-Aufruf, kein Rückfall auf den Zugang der Sandbox
        self.assertEqual(proc.stdin.data, b"")
        self.assertEqual(len(self.start_procs), 1)  # die Attrappe blieb unbenutzt
        texte = "\n".join(self.task.llm_texts())
        self.assertIn("Entwicklungsauftrag", texte)
        canvas = [m for m in self.task.messages() if m.get("type") == "canvas"][-1]
        self.assertIn("Claude-Code-Token", canvas["item"]["text"])

    async def test_ohne_token_quelle_wie_bisher_ohne_zweite_zeile(self):
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        await self.prepare_and_start()  # self.agent aus asyncSetUp: kein token_quelle (Übergang)
        zeilen = proc.stdin.data.decode("utf-8").splitlines()
        self.assertEqual(len(zeilen), 1)
        self.assertNotIn("token_folgt", json.loads(zeilen[0]))
        self.assertNotIn("modell", json.loads(zeilen[0]))  # der Dispatcher wählt das Modell der Art (Haiku)

    async def test_lokal_gilt_der_token_nicht(self):
        agent = dev_agent.DevAgent(memory=self.memory, auftraggeber="Erika", ort="lokal",
                                   token_quelle=mock.AsyncMock(return_value=TOKEN))
        agent.attach(self.task)
        job = dev_agent.Job(id="a" * 16, auftrag="x", projekt=Path("/tmp/x"), art="umsetzung")
        await agent._run_locked(job, "x", resume=False)
        self.assertIn("nur auf Sokrates-2", job.status)
        self.assertEqual(self.calls, [])  # kein Prozess gestartet


if __name__ == "__main__":
    unittest.main()
