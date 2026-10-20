"""Tests für die Betriebsart sokrates2 in dev_agent.py: Aufträge per SSH an den Dispatcher auf Sokrates-2.

SSH ist gemockt (create_subprocess_exec liefert Attrappen-Prozesse); geprüft werden Schalter, ssh-Aufruf,
Kopfzeile und Lebenslinie, Auswertung von stream-json und wamo-Ereignissen, Freigaben über approve und
pending sowie der Abbruch beim Auflegen. Den echten Weg prüft wamo-job-rauchtest auf Sokrates-2.

Ausführen:  uv run python -m pytest -q test_dev_agent_sokrates2.py   (im Ordner voice-core/)
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from loguru import logger

import dev_agent
import dev_guard

SID = "754fb6bb-4880-4fc6-bc55-ed512941f3d3"
PUSH = {"command": "git push origin main", "description": "Änderungen hochladen"}


class SwitchTests(unittest.TestCase):
    def test_ort_default_and_values(self):
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_ORT": ""}):
            self.assertEqual(dev_agent.entwicklung_ort(), "sokrates2")  # Standard, sobald WAMO_ENTWICKLUNG=an
        for value, expected in (("sokrates2", "sokrates2"), ("LOKAL", "lokal"), ("pc", None), ("x", None)):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_ORT": value}):
                self.assertEqual(dev_agent.entwicklung_ort(), expected)
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_ORT": "pc"}):
            self.assertIn("ungültig", dev_agent.entwicklung_problem())

    def test_entwicklung_still_off_by_default(self):
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG": "", "WAMO_ENTWICKLUNG_ORT": "sokrates2"}):
            self.assertFalse(dev_agent.entwicklung_enabled())

    def test_ssh_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, hosts = Path(tmp, "id_ed25519_wamo_jobs"), Path(tmp, "known_hosts_wamo_jobs")
            env = {"WAMO_ENTWICKLUNG_ORT": "sokrates2", "WAMO_ENTWICKLUNG_SSH_SCHLUESSEL": str(key),
                   "WAMO_ENTWICKLUNG_SSH_ZIEL": "wamo@192.168.178.136", "WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS": str(hosts)}
            with mock.patch.dict(os.environ, env), mock.patch.object(dev_agent.shutil, "which", return_value="ssh"):
                self.assertIn("Schlüssel", dev_agent.entwicklung_problem())
                key.write_text("x", encoding="utf-8")
                self.assertIn("Hostschlüssel", dev_agent.entwicklung_problem())
                hosts.write_text("x", encoding="utf-8")
                self.assertIsNone(dev_agent.entwicklung_problem())
                with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_SSH_ZIEL": "wamo@host; id"}):
                    self.assertIn("ZIEL", dev_agent.entwicklung_problem())
            with mock.patch.dict(os.environ, env), mock.patch.object(dev_agent.shutil, "which", return_value=None):
                self.assertIn("ssh", dev_agent.entwicklung_problem())

    def test_ssh_argv(self):
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_SSH_SCHLUESSEL": "C:/Users/x y/.ssh/k",
                                          "WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS": "C:/Users/x y/.ssh/kh",
                                          "WAMO_ENTWICKLUNG_SSH_ZIEL": "wamo@192.168.178.136"}), \
             mock.patch.object(dev_agent.shutil, "which", return_value="C:/Windows/System32/OpenSSH/ssh.exe"):
            argv = dev_agent.ssh_argv(["start", "abc12345", "rezepte-app", "umsetzung"])
            self.assertEqual(argv[0], "C:/Windows/System32/OpenSSH/ssh.exe")
            self.assertEqual(argv[1:3], ["-F", "none"])  # keine ~/.ssh/config (kein fremder Schlüssel, kein Include)
            self.assertIn(str(Path("C:/Users/x y/.ssh/k")), argv)
            for opt in ("IdentitiesOnly=yes", "BatchMode=yes", "StrictHostKeyChecking=yes", "GlobalKnownHostsFile=none",
                        "UpdateHostKeys=no", "HostKeyAlgorithms=ssh-ed25519", "PasswordAuthentication=no",
                        "PreferredAuthentications=publickey", "ClearAllForwardings=yes", "ForwardAgent=no",
                        'UserKnownHostsFile="C:/Users/x y/.ssh/kh"'):
                self.assertIn(opt, argv)
            self.assertIn("-T", argv)
            self.assertEqual(argv[-3:], ["--", "wamo@192.168.178.136", "start abc12345 rezepte-app umsetzung"])
            for bad in (["start", "a b"], ["start", "../x"], ["status", "x;id"], ["status", ""], ["status", "x\ny"]):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    dev_agent.ssh_argv(bad)

    def test_validate_project_remote(self):
        self.assertEqual(dev_agent.validate_project("Rezepte App", "sokrates2"), PurePosixPath("/srv/wamo/projekte/rezepte-app"))
        for bad in ["wamo", "a", "../x", "x" * 60, None, "a--b"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dev_agent.validate_project(bad, "sokrates2")

    def test_rules_mention_sandbox_only_remote(self):
        self.assertIn("Sandbox", dev_agent.rules_for("Erika", "sokrates2"))
        self.assertIn("git clone", dev_agent.rules_for("Erika", "sokrates2"))
        self.assertNotIn("Sandbox", dev_agent.rules_for("Erika"))

    def test_remote_status(self):
        rs = dev_agent.remote_status
        self.assertEqual(rs(0, {"ende": {"ergebnis": "fertig"}}), "fertig")
        self.assertEqual(rs(124, {"ende": {"ergebnis": "zeitlimit"}}), "abgebrochen (Zeitlimit)")
        self.assertEqual(rs(130, {"ende": {"ergebnis": "verlassen"}}), "abgebrochen (Verbindung beendet)")
        self.assertEqual(rs(1, {"ende": {"ergebnis": "fehler", "code": 1}}), "beendet (Code 1)")
        self.assertIn("Selbsttest", rs(3, {"selbsttest_fehlgeschlagen": {}}))
        self.assertIn("Claude-Zugang", rs(5, {"selbsttest_ok": {}, "keine_zugangsdaten": {}}))
        self.assertIn("belegt", rs(4, {"belegt": {}}))
        self.assertIn("abgewiesen", rs(2, {}))
        self.assertIn("SSH", rs(255, {}))
        self.assertEqual(rs(77, {}), "beendet (Code 77)")

    def test_lokal_needs_second_confirmation(self):
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_ORT": "lokal", "WAMO_ENTWICKLUNG_LOKAL_BESTAETIGT": ""}):
            self.assertIn("BESTAETIGT", dev_agent.entwicklung_problem())
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_ORT": "lokal", "WAMO_ENTWICKLUNG_LOKAL_BESTAETIGT": "ja"}), \
             mock.patch.object(dev_agent, "PROJECTS_DIR", Path.home() / "Projekte"):
            self.assertIsNone(dev_agent.entwicklung_problem())

    def test_lokal_needs_explicit_switch_and_warns(self):
        meldungen = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="WARNING")
        try:
            dev_agent.DevAgent(memory=None, auftraggeber="Erika", ort="sokrates2")
            self.assertFalse(any("LOKAL" in m for m in meldungen))
            dev_agent.DevAgent(memory=None, auftraggeber="Erika", ort="lokal")
            self.assertTrue(any("WARNUNG" in m and "LOKAL" in m for m in meldungen))
        finally:
            logger.remove(sink)
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_ORT": ""}):
            self.assertEqual(dev_agent.DevAgent(memory=None, auftraggeber="Erika")._ort, "sokrates2")


# ---------------------------------------------------------------- Attrappen für SSH
class FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        if self.closed:
            raise BrokenPipeError
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed


class FakeProc:
    """Prozess-Attrappe für ssh: stdout aus Zeilen, optional hängend bis zum Beenden."""

    def __init__(self, lines=(), returncode=0, haengt=False):
        self.stdin = FakeStdin()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        for line in lines:
            self.stdout.feed_data((line if isinstance(line, str) else json.dumps(line)).encode() + b"\n")
        if not haengt:
            self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._rc = returncode
        self.returncode = None
        self.pid = 4242
        self.killed = False
        self.stdin_offen_beim_warten = None
        self._ende = asyncio.Event()
        if not haengt:
            self._ende.set()

    def beenden(self):
        self.killed = True
        self._rc = -9
        if not self.stdout.at_eof():
            self.stdout.feed_eof()
        self._ende.set()

    async def wait(self):
        if self.stdin_offen_beim_warten is None:
            self.stdin_offen_beim_warten = not self.stdin.closed
        await self._ende.wait()
        self.returncode = self._rc
        return self.returncode

    async def communicate(self, input=None):
        out = await self.stdout.read()
        self.returncode = self._rc
        return out, b""


class FakeMemory:
    def __init__(self):
        self.actions = []

    async def log_action(self, **kw):
        self.actions.append(kw)
        return f"a{len(self.actions)}"

    async def update_action(self, action_id, status):
        self.actions.append({"update": action_id, "status": status})


class FakeTask:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)

    def messages(self):
        return [getattr(f, "data", None) for f in self.frames if getattr(f, "data", None)]

    def llm_texts(self):
        return [m["content"] for f in self.frames for m in getattr(f, "messages", [])]


def stream(rc_ergebnis="fertig", tool=True):
    lines = [{"type": "wamo", "ereignis": "selbsttest_ok"}, {"type": "wamo", "ereignis": "gestartet"},
             {"type": "system", "subtype": "init", "session_id": SID}]
    if tool:
        lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": PUSH}]}})
    lines += ["kein json", {"type": "result", "result": "Push braucht eine Freigabe.", "total_cost_usd": 0.12,
                            "session_id": SID},
              {"type": "wamo", "ereignis": "ende", "ergebnis": rc_ergebnis, "code": 0}]
    return lines


class RemoteFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        key = Path(self.tmp.name, "id_ed25519_wamo_jobs")
        key.write_text("x", encoding="utf-8")
        hosts = Path(self.tmp.name, "known_hosts_wamo_jobs")
        hosts.write_text("x", encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_ORT": "sokrates2",
                                                "WAMO_ENTWICKLUNG_SSH_KNOWN_HOSTS": str(hosts),
                                                "WAMO_ENTWICKLUNG_SSH_SCHLUESSEL": str(key),
                                                "WAMO_ENTWICKLUNG_SSH_ZIEL": "wamo@192.168.178.136"})
        self.env.start()
        self.which = mock.patch.object(dev_agent.shutil, "which", return_value="ssh")
        self.which.start()
        self.calls: list[list[str]] = []
        self.start_procs: list[FakeProc] = []
        self.approve_rc = 0
        self.pending_rc = 0
        fp = dev_guard.fingerprint("Bash", PUSH)
        self.pending_line = json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": PUSH})

        async def fake_exec(*argv, **kwargs):
            self.calls.append(list(argv))
            befehl = argv[-1].split(" ")[0]
            if befehl == "start":
                return self.start_procs.pop(0)
            if befehl == "pending":
                return FakeProc([self.pending_line] if self.pending_rc == 0 else [], returncode=self.pending_rc)
            if befehl == "approve":
                return FakeProc([{"ok": True}], returncode=self.approve_rc)
            return FakeProc([{"ok": True}])

        async def fake_kill(proc):
            if isinstance(proc, FakeProc) and proc.returncode is None:
                proc.beenden()

        self.exec = mock.patch.object(dev_agent.asyncio, "create_subprocess_exec", side_effect=fake_exec)
        self.exec.start()
        self.kill = mock.patch.object(dev_agent, "_kill_tree", side_effect=fake_kill)
        self.kill.start()
        self.memory = FakeMemory()
        self.agent = dev_agent.DevAgent(memory=self.memory, auftraggeber="Erika")
        self.task = FakeTask()
        self.agent.attach(self.task)

    async def asyncTearDown(self):
        for p in (self.kill, self.exec, self.which, self.env):
            p.stop()
        self.tmp.cleanup()

    def remote(self, befehl):
        return [c[-1] for c in self.calls if c[-1].split(" ")[0] == befehl]

    async def prepare_and_start(self, art="umsetzung"):
        params = type("P", (), {})()
        params.arguments = {"auftrag": "Baue eine Rezepte-App und pushe sie", "projekt": "rezepte-app", "art": art}
        result = {}

        async def cb(r):
            result.update(r)
        params.result_callback = cb
        await self.agent._vorbereiten(params)
        self.assertTrue(result["ok"])
        self.assertNotIn("neu", result)  # entfernter Ordner, lokal nicht prüfbar
        await self.agent.on_client_message("auftrag_start", {"job": result["auftrag_id"], "entscheidung": "ja"})
        await self.agent._running
        return self.agent._jobs[result["auftrag_id"]]

    async def test_job_runs_over_ssh_with_lifeline(self):
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        job = await self.prepare_and_start()
        self.assertRegex(job.id, r"^[0-9a-f]{16}$")
        self.assertEqual(job.projekt, PurePosixPath("/srv/wamo/projekte/rezepte-app"))
        start = self.calls[0]
        self.assertEqual(start[-1], f"start {job.id} rezepte-app umsetzung")
        self.assertEqual(start[1:3], ["-F", "none"])
        kopf = json.loads(proc.stdin.data.decode("utf-8").splitlines()[0])
        self.assertEqual(kopf["auftrag"], "Baue eine Rezepte-App und pushe sie")
        self.assertIn("Sandbox", kopf["system"])
        self.assertIn("Werkzeugkarte für diesen Auftrag (Art: Umsetzung)", kopf["system"])
        self.assertTrue(proc.stdin_offen_beim_warten)  # Lebenslinie bleibt offen, bis der Lauf endet
        self.assertTrue(proc.stdin.closed)
        self.assertEqual(job.status, "fertig")
        self.assertEqual(job.session_id, SID)
        self.assertAlmostEqual(job.cost_usd, 0.12)
        self.assertIn("Wächter-Selbsttest in der Sandbox bestanden", job.steps)
        self.assertTrue(any(s.startswith("Befehl: `git push") for s in job.steps))
        self.assertEqual(self.remote("pending"), [f"pending {job.id}"])
        self.assertEqual(list(job.pending), [dev_guard.fingerprint("Bash", PUSH)])
        karte = next(m for m in self.task.messages() if m.get("type") == "freigaben")
        self.assertEqual(karte["items"][0]["grund"], "git push")
        self.assertEqual(self.remote("cancel"), [])  # sauber beendet: kein zusätzlicher Abbruch
        canvas = [m for m in self.task.messages() if m.get("type") == "canvas"][-1]
        self.assertIn("Sokrates-2, Sandbox", canvas["item"]["text"])
        texte = self.task.llm_texts()
        self.assertNotIn("Push braucht", texte[-1])  # Ergebnistext nur auf dem Canvas

    async def test_approval_goes_over_ssh_and_resumes(self):
        self.start_procs += [FakeProc(stream()), FakeProc(stream(tool=False))]
        job = await self.prepare_and_start()
        fp = dev_guard.fingerprint("Bash", PUSH)
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.agent._running
        self.assertEqual(self.remote("approve"), [f"approve {job.id} {fp}"])
        starts = self.remote("start")
        self.assertEqual(starts[1], f"start {job.id} rezepte-app umsetzung {SID}")  # Fortsetzung der Sitzung
        self.assertIn({"update": "a1", "status": "approved"}, self.memory.actions)

    async def test_failed_approval_keeps_card_and_does_not_resume(self):
        self.start_procs.append(FakeProc(stream()))
        job = await self.prepare_and_start()
        self.approve_rc = 2
        fp = dev_guard.fingerprint("Bash", PUSH)
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        self.assertEqual(len(self.remote("start")), 1)
        self.assertIn(fp, job.pending)
        self.assertTrue(any("nicht übertragen" in s for s in job.steps))

    async def test_forged_pending_line_is_dropped(self):
        self.pending_line = json.dumps({"id": "0" * 16, "grund": "harmlos", "tool": "Bash", "input": PUSH})
        self.start_procs.append(FakeProc(stream()))
        job = await self.prepare_and_start()
        self.assertEqual(job.pending, {})

    async def test_pending_not_retrievable_is_visible(self):
        self.pending_rc = 255
        self.start_procs.append(FakeProc(stream()))
        job = await self.prepare_and_start()
        self.assertEqual(job.status, "fertig, Freigaben nicht abrufbar")

    async def test_access_escalation_becomes_card_and_restarts_after_approval(self):
        """Dispatcher meldet zugang_erweitert (mehr Token als frühere Läufe im Projekt): Karte, nach Klick Neustart."""
        eingabe = {"projekt": "rezepte-app", "neu": ["GH_TOKEN"], "bisher": ["ANTHROPIC_API_KEY"]}
        fp = dev_guard.fingerprint("WAMO-Zugang", eingabe)
        self.pending_line = "{}"  # keine Anfragen aus der Sandbox
        self.start_procs += [
            FakeProc([{"type": "wamo", "ereignis": "selbsttest_ok"},
                      {"type": "wamo", "ereignis": "zugang_erweitert", "id": fp, "tool": "WAMO-Zugang", "input": eingabe,
                       "grund": "Mehr Zugänge als frühere Aufträge in diesem Projekt: GH_TOKEN"}], returncode=7),
            FakeProc(stream(tool=False))]
        job = await self.prepare_and_start()
        self.assertIn("mehr Zugänge", job.status)
        self.assertEqual(list(job.pending), [fp])
        karte = next(m for m in self.task.messages() if m.get("type") == "freigaben")
        self.assertIn("GH_TOKEN", karte["items"][0]["grund"])
        self.assertEqual(self.remote("cancel"), [])  # Code 7: nichts lief, kein Abbruch nötig
        await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
        await self.agent._running
        self.assertEqual(self.remote("approve"), [f"approve {job.id} {fp}"])
        self.assertEqual(self.remote("start")[1], f"start {job.id} rezepte-app umsetzung")  # neu, keine Fortsetzung
        self.assertEqual(job.status, "fertig")

    async def test_forged_escalation_with_wrong_fingerprint_is_dropped(self):
        eingabe = {"projekt": "rezepte-app", "neu": ["GH_TOKEN"], "bisher": []}
        self.pending_line = "{}"
        self.start_procs.append(FakeProc([{"type": "wamo", "ereignis": "zugang_erweitert", "id": "0" * 64,
                                           "tool": "WAMO-Zugang", "input": eingabe, "grund": "x"}], returncode=7))
        job = await self.prepare_and_start()
        self.assertEqual(job.pending, {})

    async def test_selftest_failure_reported(self):
        self.start_procs.append(FakeProc([{"type": "wamo", "ereignis": "selbsttest_fehlgeschlagen", "grund": "x"}],
                                         returncode=3))
        job = await self.prepare_and_start()
        self.assertIn("Selbsttest", job.status)
        self.assertEqual(job.session_id, None)

    async def test_ssh_failure(self):
        self.start_procs.append(FakeProc([], returncode=255))
        job = await self.prepare_and_start()
        self.assertIn("SSH", job.status)
        self.assertEqual(self.remote("cancel"), [f"cancel {job.id}"])  # unklarer Zustand: sicherheitshalber abbrechen

    async def test_hangup_ends_remote_job(self):
        proc = FakeProc([{"type": "wamo", "ereignis": "selbsttest_ok"}], haengt=True)
        self.start_procs.append(proc)
        params = type("P", (), {})()
        params.arguments = {"auftrag": "Lange Arbeit", "projekt": "rezepte-app", "art": "umsetzung"}
        result = {}

        async def cb(r):
            result.update(r)
        params.result_callback = cb
        await self.agent._vorbereiten(params)
        await self.agent.on_client_message("auftrag_start", {"job": result["auftrag_id"], "entscheidung": "ja"})
        for _ in range(50):
            if self.agent._proc is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIs(self.agent._proc, proc)
        await self.agent.close()  # Auflegen
        self.assertTrue(proc.killed)  # ssh beendet: die Lebenslinie auf Sokrates-2 reißt ab
        self.assertIn(f"cancel {result['auftrag_id']}", self.remote("cancel"))  # zweite Absicherung
        try:
            await asyncio.wait_for(self.agent._running, 5)
        except asyncio.CancelledError:
            pass
        self.assertTrue(self.agent._running.done())
        self.assertIsNone(self.agent._proc)


if __name__ == "__main__":
    unittest.main()
