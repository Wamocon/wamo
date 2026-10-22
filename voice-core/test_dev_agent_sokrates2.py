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
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from unittest import mock

from loguru import logger

import dev_agent
import dev_guard

SID = "754fb6bb-4880-4fc6-bc55-ed512941f3d3"
OAUTH_TEST = "sk-ant-oat01-" + "GEHEIMoauth0" * 5
API_TEST = "sk-ant-api03-" + "GEHEIMapi00" * 5
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

    def test_repo_pruefen_und_aus_auftrag(self):
        for roh, repo in (("wamocon/rezepte-app", "wamocon/rezepte-app"), (" a/b ", "a/b"),
                          ("https://github.com/a/b.git", "a/b"), ("github.com/a/b/", "a/b"), ("a/b.c_d-e", "a/b.c_d-e")):
            with self.subTest(roh=roh):
                self.assertEqual(dev_agent.repo_pruefen(roh), repo)
        for schlecht in ("", "a", "a/b/c", "../x", "a/..", "a b/c", "a/b;id", "https://evil.example/a/b", None, 5):
            with self.subTest(schlecht=schlecht), self.assertRaises(ValueError):
                dev_agent.repo_pruefen(schlecht)
        aus = dev_agent.repo_aus_auftrag
        self.assertEqual(aus("Fix in github.com/wamocon/app, bitte."), "wamocon/app")
        self.assertEqual(aus("https://github.com/a/b und https://github.com/A/B.git"), "a/b")  # dasselbe Repo
        with self.assertRaises(ValueError) as ctx:  # mehrere Repos: nie stillschweigend die ganze Installation
            aus("https://github.com/a/b und https://github.com/c/d")
        self.assertIn("ausdrücklich", str(ctx.exception))
        self.assertIsNone(aus("Lesen/Schreiben im Repo rezepte"))  # ohne GitHub-Adresse kein Raten
        for eigen in ("wamocon/wamo", "Wamocon/WAMO", "https://github.com/wamocon/wamo.git"):  # Selbstschutz
            with self.subTest(eigen=eigen), self.assertRaises(ValueError):
                dev_agent.repo_pruefen(eigen)
        with self.assertRaises(ValueError) as ctx:  # nie stillschweigend übergehen: sonst Token für die ganze Installation
            aus("Siehe https://github.com/wamocon/wamo für den Hintergrund.")
        self.assertIn("Feld repo", str(ctx.exception))
        for gross in ("https://GitHub.com/a/b", "HTTP://GITHUB.COM/a/b/", "GitHub.com/a/b.git"):  # Groß-/Kleinschreibung
            with self.subTest(gross=gross):
                self.assertEqual(dev_agent.repo_pruefen(gross), "a/b")

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


NIKO = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"  # das Profil, dem der Zugang aus geheim.env auf Sokrates-2 gehört
KOLLEGIN = "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"


class FakeMemory:
    def __init__(self, user_id=NIKO):
        self.actions = []
        self.user_id = user_id

    async def log_action(self, **kw):
        self.actions.append(kw)
        return f"a{len(self.actions)}"

    async def update_action(self, action_id, status):
        self.actions.append({"update": action_id, "status": status})


class FakeKontingentDb:
    """Tabelle model_access im Speicher, nur die Spalten des Kontingents (kontingent.py)."""

    configured = True

    def __init__(self, fehler: Exception | None = None):
        self.zeilen: dict[str, dict] = {}
        self.anweisungen: list[tuple[str, tuple]] = []
        self.fehler = fehler

    def t(self, name: str) -> str:
        return f"wamo_test.{name}"

    async def fetchrow(self, sql: str, *args):
        if self.fehler:
            raise self.fehler
        return self.zeilen.get(args[0])

    async def execute(self, sql: str, *args):
        if self.fehler:
            raise self.fehler
        self.anweisungen.append((sql, args))
        self.zeilen[args[0]] = {"kontingent": args[1], "kontingent_stand": datetime.now(timezone.utc)}


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
                                                "WAMO_ENTWICKLUNG_SSH_ZIEL": "wamo@192.168.178.136",
                                                "WAMO_CLAUDE_SCHLUESSEL_PROFIL": NIKO,
                                                "WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL": ""})
        self.env.start()
        self.which = mock.patch.object(dev_agent.shutil, "which", return_value="ssh")
        self.which.start()
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.start_procs: list[FakeProc] = []
        self.approve_rc = 0
        self.pending_rc = 0
        fp = dev_guard.fingerprint("Bash", PUSH)
        self.pending_line = json.dumps({"id": fp, "grund": "git push", "tool": "Bash", "input": PUSH})

        async def fake_exec(*argv, **kwargs):
            self.calls.append(list(argv))
            self.kwargs.append(kwargs)
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

    async def prepare_and_start(self, art="umsetzung", **extra):
        params = type("P", (), {})()
        params.arguments = {"auftrag": "Baue eine Rezepte-App und pushe sie", "projekt": "rezepte-app", "art": art,
                            **extra}
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
        self.assertNotIn("modell", kopf)  # ohne WAMO_ENTWICKLUNG_MODELL wählt der Dispatcher das Modell der Art
        self.assertNotIn("repo", kopf)  # kein Repo genannt: Token für die Installation mit den Rechten der Art
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

    async def test_modell_override_goes_in_header(self):
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_MODELL": "claude-opus-5-5"}):
            await self.prepare_and_start()
        kopf = json.loads(proc.stdin.data.decode("utf-8").splitlines()[0])
        self.assertEqual(kopf["modell"], "claude-opus-5-5")

    async def test_repo_goes_in_header_and_card(self):
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        job = await self.prepare_and_start(repo="https://github.com/wamocon/rezepte-app.git")
        kopf = json.loads(proc.stdin.data.decode("utf-8").splitlines()[0])
        self.assertEqual(kopf["repo"], "wamocon/rezepte-app")
        self.assertEqual(job.repo, "wamocon/rezepte-app")
        karte = next(m for m in self.task.messages() if m.get("type") == "auftrag")
        self.assertEqual(karte["repo"], "wamocon/rezepte-app")
        canvas = [m for m in self.task.messages() if m.get("type") == "canvas"][-1]
        self.assertIn("wamocon/rezepte-app", canvas["item"]["text"])

    async def test_repo_from_github_address_in_task(self):
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        await self.prepare_and_start(auftrag="Behebe den Fehler aus https://github.com/Wamocon/rezepte-app/issues/3.")
        kopf = json.loads(proc.stdin.data.decode("utf-8").splitlines()[0])
        self.assertEqual(kopf["repo"], "Wamocon/rezepte-app")

    async def test_invalid_repo_is_rejected_before_card(self):
        params = type("P", (), {})()
        params.arguments = {"auftrag": "x", "projekt": "rezepte-app", "art": "umsetzung", "repo": "../etc/passwd"}
        result = {}

        async def cb(r):
            result.update(r)
        params.result_callback = cb
        await self.agent._vorbereiten(params)
        self.assertFalse(result["ok"])
        self.assertIn("owner/name", result["fehler"])
        self.assertEqual(self.agent._jobs, {})

    async def test_multiple_repos_in_task_must_be_named_explicitly(self):
        params = type("P", (), {})()
        params.arguments = {"auftrag": "Vergleiche https://github.com/a/b mit https://github.com/c/d und ändere beides",
                            "projekt": "rezepte-app", "art": "umsetzung"}
        result = {}

        async def cb(r):
            result.update(r)
        params.result_callback = cb
        await self.agent._vorbereiten(params)
        self.assertFalse(result["ok"])
        self.assertIn("mehrere GitHub-Repos", result["fehler"])
        self.assertEqual(self.agent._jobs, {})  # keine Karte, kein stilles Token für die ganze Installation
        params.arguments["repo"] = "c/d"  # ausdrücklich genannt: ok
        result.clear()
        await self.agent._vorbereiten(params)
        self.assertTrue(result["ok"])
        self.assertEqual(result["repo"], "c/d")

    async def test_revoke_failure_is_shown_on_canvas(self):
        self.start_procs.append(FakeProc(stream() + [{"type": "wamo", "ereignis": "gh_widerruf_fehlgeschlagen"}]))
        job = await self.prepare_and_start()
        self.assertTrue(any("nicht widerrufen" in s for s in job.steps))

    async def test_github_token_refused_is_clean_and_reported(self):
        self.start_procs.append(FakeProc([{"type": "wamo", "ereignis": "selbsttest_ok"},
                                          {"type": "wamo", "ereignis": "start_abgelehnt",
                                           "grund": "GitHub-App: GitHub antwortet 401: JWT abgelehnt"}], returncode=8))
        job = await self.prepare_and_start()
        self.assertEqual(job.status, "nicht gestartet (GitHub-Token der App nicht erzeugt)")
        self.assertTrue(any(s.startswith("Start abgelehnt: GitHub-App: GitHub antwortet 401") for s in job.steps))
        self.assertEqual(self.remote("cancel"), [])  # Code 8: nichts lief, kein Abbruch nötig
        self.assertNotIn("401", self.task.llm_texts()[-1])  # Grund nur auf dem Canvas
        self.assertIn("GitHub", dev_agent.remote_status(8, {}))

    async def test_invalid_modell_override_blocks_start(self):
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_MODELL": "claude-irgendwas"}):
            self.assertIn("WAMO_ENTWICKLUNG_MODELL", dev_agent.entwicklung_problem() or "")

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

    # ---------------------------------------------------------------- Claude-Zugang je Profil
    def zugang_texte(self) -> str:
        """Alles, was der Kern nach außen gibt: ssh-Aufrufe, Umgebungen, Nachrichten an Oberfläche und Sprachmodell."""
        return json.dumps([self.calls, [k.get("env") for k in self.kwargs], self.task.messages(), self.task.llm_texts(),
                           [(j.steps, j.result, j.status) for j in self.agent._jobs.values()]], default=str)

    async def test_eigener_zugang_geht_als_zweite_zeile_und_nirgends_sonst(self):
        meldungen: list[str] = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="DEBUG")
        self.memory.user_id = KOLLEGIN  # nicht das Profil, dem der Zugang aus geheim.env gehört
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        try:
            with mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value={"typ": "oauth", "wert": OAUTH_TEST}):
                job = await self.prepare_and_start()
        finally:
            logger.remove(sink)
        zeilen_ = proc.stdin.data.decode("utf-8").splitlines()
        self.assertEqual(len(zeilen_), 2)
        kopf = json.loads(zeilen_[0])
        self.assertEqual((kopf["zugang"], kopf["profil"]), ("folgt", "7d3e9c1a"))
        self.assertNotIn(OAUTH_TEST, zeilen_[0])  # nie in der Kopfzeile
        self.assertEqual(json.loads(zeilen_[1]), {"typ": "oauth", "wert": OAUTH_TEST})
        self.assertEqual(job.status, "fertig")
        self.assertTrue(all(k.get("env") is None for k in self.kwargs))  # ssh erbt die Umgebung, es gibt nichts Besonderes
        for geheim in (OAUTH_TEST, "GEHEIMoauth"):
            self.assertNotIn(geheim, self.zugang_texte())  # weder argv noch Karten noch Sprachmodell
            self.assertNotIn(geheim, "\n".join(meldungen))  # und nicht im Log

    async def test_eigener_zugang_async_mit_api_und_workspace(self):
        self.memory.user_id = KOLLEGIN
        proc = FakeProc(stream())
        self.start_procs.append(proc)

        async def zugang(user_id):
            self.assertEqual(user_id, KOLLEGIN)
            return {"typ": "api", "wert": API_TEST, "workspace": "wrkspc_Mine01", "unbekannt": "x"}
        with mock.patch.object(dev_agent, "claude_code_zugang_fuer", zugang):
            await self.prepare_and_start()
        zeile = json.loads(proc.stdin.data.decode("utf-8").splitlines()[1])
        self.assertEqual(zeile, {"typ": "api", "wert": API_TEST, "workspace": "wrkspc_Mine01"})  # nur bekannte Felder

    async def test_zugang_wird_nach_freigabe_erneut_gesendet(self):
        self.memory.user_id = KOLLEGIN
        erste, zweite = FakeProc(stream()), FakeProc(stream(tool=False))
        self.start_procs += [erste, zweite]
        with mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value={"typ": "oauth", "wert": OAUTH_TEST}):
            job = await self.prepare_and_start()
            fp = dev_guard.fingerprint("Bash", PUSH)
            await self.agent.on_client_message("freigabe", {"job": job.id, "id": fp, "entscheidung": "ja"})
            await self.agent._running
        for proc in (erste, zweite):
            zeilen_ = proc.stdin.data.decode("utf-8").splitlines()
            self.assertEqual(json.loads(zeilen_[1])["wert"], OAUTH_TEST)  # bei jedem Lauf frisch, nichts aufbewahrt
        self.assertEqual(self.remote("start")[1], f"start {job.id} rezepte-app umsetzung {SID}")
        self.assertNotIn(OAUTH_TEST, self.zugang_texte())

    async def test_ohne_zugang_und_ohne_berechtigung_startet_nichts(self):
        meldungen: list[str] = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="DEBUG")
        self.memory.user_id = KOLLEGIN
        try:
            with mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value=None):
                job = await self.prepare_and_start()
        finally:
            logger.remove(sink)
        self.assertEqual(self.remote("start"), [])  # kein ssh-Aufruf, also auch nicht mit dem Schlüssel aus geheim.env
        self.assertEqual(job.status, dev_agent.KEIN_ZUGANG_STATUS)
        self.assertTrue(any("Modellzugang" in s for s in job.steps))
        canvas = [m for m in self.task.messages() if m.get("type") == "canvas"][-1]
        self.assertIn("Modellzugang", canvas["item"]["text"])
        self.assertIn("kein Claude-Zugang", self.task.llm_texts()[-1])  # klare Meldung auch für die Stimme
        self.assertEqual(self.remote("cancel"), [])
        self.assertTrue(any("nicht gestartet" in m for m in meldungen))

    async def test_fehler_beim_lesen_des_zugangs_zeigt_nie_den_inhalt(self):
        meldungen: list[str] = []
        sink = logger.add(lambda m: meldungen.append(str(m)), level="DEBUG")
        self.memory.user_id = KOLLEGIN

        def kaputt(user_id):
            raise RuntimeError(f"Entschlüsseln fehlgeschlagen: {OAUTH_TEST}")
        try:
            with mock.patch.object(dev_agent, "claude_code_zugang_fuer", kaputt):
                job = await self.prepare_and_start()
        finally:
            logger.remove(sink)
        self.assertEqual(self.remote("start"), [])
        self.assertEqual(job.status, dev_agent.KEIN_ZUGANG_STATUS)
        self.assertNotIn(OAUTH_TEST, "\n".join(meldungen) + self.zugang_texte())
        self.assertTrue(any("RuntimeError" in m for m in meldungen))  # nur die Art der Ausnahme

    async def test_unbrauchbare_zugaenge_zaehlen_als_keiner(self):
        self.memory.user_id = KOLLEGIN
        for roh in ({"typ": "x", "wert": OAUTH_TEST}, {"typ": "oauth", "wert": ""}, {"typ": "oauth"}, {"wert": OAUTH_TEST},
                    "sk-ant-oat01-text", [OAUTH_TEST], 5, {"typ": "api", "wert": 5}):
            with self.subTest(roh=str(roh)[:30]), mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value=roh):
                self.assertIsNone(await dev_agent.persoenlicher_zugang(KOLLEGIN))
        self.assertIsNone(await dev_agent.persoenlicher_zugang(None))

    async def test_serverzugang_nur_fuer_das_vorgesehene_profil(self):
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        job = await self.prepare_and_start()  # NIKO, ohne eigenen Zugang (Standard der Attrappe: keiner)
        zeilen_ = proc.stdin.data.decode("utf-8").splitlines()
        self.assertEqual(len(zeilen_), 1)  # keine zweite Zeile
        self.assertEqual(json.loads(zeilen_[0])["zugang"], "server")
        self.assertNotIn("profil", json.loads(zeilen_[0]))
        self.assertEqual(job.status, "fertig")
        for user, soll in ((NIKO, True), (NIKO.upper(), True), (KOLLEGIN, False), (None, False), ("", False), ("kein uuid", False)):
            self.assertIs(dev_agent.serverzugang_erlaubt(user), soll, user)
        with mock.patch.dict(os.environ, {"WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL": KOLLEGIN}):  # ausdrücklich ein anderes
            self.assertTrue(dev_agent.serverzugang_erlaubt(KOLLEGIN))
            self.assertFalse(dev_agent.serverzugang_erlaubt(NIKO))
        with mock.patch.dict(os.environ, {"WAMO_CLAUDE_SCHLUESSEL_PROFIL": "", "WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL": ""}):
            self.assertFalse(dev_agent.serverzugang_erlaubt(NIKO))  # nichts zugeordnet: für niemanden

    async def test_eigener_zugang_hat_vorrang_vor_dem_serverzugang(self):
        proc = FakeProc(stream())
        self.start_procs.append(proc)
        with mock.patch.object(dev_agent, "claude_code_zugang_fuer", return_value={"typ": "oauth", "wert": OAUTH_TEST}):
            await self.prepare_and_start()  # NIKO hat beides
        zeilen_ = proc.stdin.data.decode("utf-8").splitlines()
        self.assertEqual(json.loads(zeilen_[0])["zugang"], "folgt")
        self.assertEqual(json.loads(zeilen_[1])["typ"], "oauth")

    async def test_profil_ohne_kennung_startet_nichts(self):
        self.memory.user_id = None
        job = await self.prepare_and_start()
        self.assertEqual(self.remote("start"), [])
        self.assertEqual(job.status, dev_agent.KEIN_ZUGANG_STATUS)

    async def test_ungueltiger_zugang_und_abgelehnter_token_werden_gemeldet(self):
        self.start_procs.append(FakeProc([{"type": "wamo", "ereignis": "zugang_ungueltig",
                                           "grund": "Zugangszeile: wert hat nicht das Format für typ oauth"}], returncode=2))
        job = await self.prepare_and_start()
        self.assertIn("Claude-Zugang ungültig", job.status)
        self.assertTrue(any(s.startswith("Claude-Zugang abgelehnt: Zugangszeile: wert hat nicht das Format") for s in job.steps))
        self.assertEqual(self.remote("cancel"), [])
        self.start_procs.append(FakeProc([{"type": "wamo", "ereignis": "selbsttest_ok"},
                                          {"type": "result", "is_error": True, "session_id": SID,
                                           "result": "Invalid bearer token"}]))
        job2 = await self.prepare_and_start()
        self.assertTrue(any("Claude-Zugang abgelehnt" in s and "Modellzugang" in s for s in job2.steps))
        self.assertIn("zugang_ungueltig", dev_agent.REMOTE_EREIGNIS_STATUS)

    # ---------------------------------------------------------------- Kontingent
    def kontingent_zeilen(self, prozent=42.0):
        limits = [{"art": "sitzung", "prozent": prozent, "endet": "2096-10-22T15:00:00Z", "status": "ok"},
                  {"art": "woche", "prozent": 13.0, "endet": "2096-10-26T09:00:00Z"},
                  {"art": "fable", "prozent": 7.0, "endet": "2096-10-27T09:00:00Z"}]
        return [{"type": "wamo", "ereignis": "selbsttest_ok"},
                {"type": "wamo", "ereignis": "kontingent", "quelle": "rate_limit_event", "limits": limits},
                {"type": "wamo", "ereignis": "kontingent", "quelle": "rate_limit_event", "limits": limits[:1]},
                {"type": "wamo", "ereignis": "kontingent", "limits": [{"art": "geheim", "prozent": 1, "x": OAUTH_TEST}]},
                {"type": "wamo", "ereignis": "ende", "ergebnis": "fertig", "code": 0}]

    async def test_kontingent_wird_geprueft_gespeichert_und_gesendet(self):
        db = FakeKontingentDb()
        self.start_procs.append(FakeProc(self.kontingent_zeilen()))
        with mock.patch.object(dev_agent, "_kontingent_db", return_value=db):
            job = await self.prepare_and_start()
        nachrichten = [m for m in self.task.messages() if m.get("type") == "kontingent"]
        self.assertTrue(nachrichten)
        letzte = nachrichten[-1]
        self.assertEqual(letzte["zustand"], "bekannt")
        self.assertEqual([e["art"] for e in letzte["limits"]], ["sitzung", "woche", "fable"])  # die gefälschte Art fiel weg
        self.assertEqual(set(letzte), {"type", "zustand", "stand", "limits"})
        self.assertNotIn(OAUTH_TEST, json.dumps(nachrichten))
        gespeichert = json.loads(db.zeilen[NIKO]["kontingent"])
        self.assertEqual([e["art"] for e in gespeichert], ["sitzung", "woche", "fable"])
        self.assertTrue(db.anweisungen)
        self.assertFalse(any("kontingent" in s.lower() for s in job.steps))  # kein Schritt im Auftrag
        self.assertNotIn("kontingent", job.ereignisse)
        self.assertNotIn("42", " ".join(self.task.llm_texts()))  # das Sprachmodell bekommt nur die feste Meldung

    async def test_kontingent_abfrage_ohne_lauf_und_ohne_daten(self):
        db = FakeKontingentDb()
        with mock.patch.object(dev_agent, "_kontingent_db", return_value=db):
            await self.agent.on_client_message("kontingent", {})
        leer = [m for m in self.task.messages() if m.get("type") == "kontingent"][-1]
        self.assertEqual(leer, {"type": "kontingent", "zustand": "unbekannt", "stand": None, "limits": []})
        db.zeilen[NIKO] = {"kontingent": json.dumps([{"art": "woche", "prozent": 55.5, "endet": "2096-10-26T09:00:00Z"}]),
                           "kontingent_stand": datetime(2026, 10, 22, 11, 59, tzinfo=timezone.utc)}
        agent = dev_agent.DevAgent(memory=self.memory, auftraggeber="Erika")
        agent.attach(self.task)
        with mock.patch.object(dev_agent, "_kontingent_db", return_value=db):
            await agent.on_client_message("kontingent", {})
            await agent.on_client_message("kontingent", {})  # zweiter Aufruf liest nicht noch einmal
        bekannt = [m for m in self.task.messages() if m.get("type") == "kontingent"][-1]
        self.assertEqual(bekannt["zustand"], "bekannt")
        self.assertEqual(bekannt["limits"], [{"art": "woche", "prozent": 55.5, "endet": "2096-10-26T09:00:00Z"}])
        self.assertEqual(bekannt["stand"], "2026-10-22T11:59:00Z")
        # ohne Datenbank oder bei einem Fehler: unbekannt, nie eine Ausnahme
        kaputt = FakeKontingentDb(fehler=RuntimeError("Tabelle fehlt"))
        agent2 = dev_agent.DevAgent(memory=self.memory, auftraggeber="Erika")
        agent2.attach(self.task)
        with mock.patch.object(dev_agent, "_kontingent_db", return_value=kaputt):
            await agent2.on_client_message("kontingent", {})
        self.assertEqual([m for m in self.task.messages() if m.get("type") == "kontingent"][-1]["zustand"], "unbekannt")
        agent3 = dev_agent.DevAgent(memory=self.memory, auftraggeber="Erika")
        agent3.attach(self.task)
        with mock.patch.object(dev_agent, "_kontingent_db", return_value=None):
            await agent3.on_client_message("kontingent", {})
        self.assertEqual([m for m in self.task.messages() if m.get("type") == "kontingent"][-1]["zustand"], "unbekannt")

    async def test_kontingent_speicherfehler_stoert_den_auftrag_nicht(self):
        self.start_procs.append(FakeProc(self.kontingent_zeilen()))
        with mock.patch.object(dev_agent, "_kontingent_db", return_value=FakeKontingentDb(fehler=RuntimeError("weg"))):
            job = await self.prepare_and_start()
        self.assertEqual(job.status, "fertig")
        self.assertTrue([m for m in self.task.messages() if m.get("type") == "kontingent"])  # Anzeige trotzdem

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
