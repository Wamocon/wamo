"""Tests für den Auftragsdispatcher auf Sokrates-2 (infra/sokrates2/wamo_job_dispatch.py), die Abschottung
(abschottung.py: Landlock-Ports, seccomp-Filter) und die Konsistenz zwischen Werkzeugkarte, Läufer
(wamo-job.sh), Ausgangsfilter (wamo-egress.nft) und managed-settings.json. Läuft unter Windows und Linux;
Teile, die Linux brauchen (fcntl, dir_fd, FIFO, setitimer), werden unter Windows übersprungen. Der echte
Sandbox-Test ist wamo-job-rauchtest auf Sokrates-2.

Ausführen:  uv run python -m pytest -q test_sokrates2_dispatch.py   (im Ordner voice-core/)
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dev_agent
import dev_guard
import werkzeugkarte as wk

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
LINUX = sys.platform.startswith("linux")


def _load(name: str, datei: str):
    spec = importlib.util.spec_from_file_location(name, INFRA / datei)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


D = _load("wamo_job_dispatch", "wamo_job_dispatch.py")
A = _load("abschottung", "abschottung.py")
JOB = "abc1234567890def"
SID = "754fb6bb-4880-4fc6-bc55-ed512941f3d3"
FP = "0123456789abcdef" * 4


class CommandValidationTests(unittest.TestCase):
    def test_valid_commands(self):
        cases = {
            "ping": ("ping", []),
            f"start {JOB} rezepte-app umsetzung": ("start", [JOB, "rezepte-app", "umsetzung"]),
            f"start {JOB} rezepte-app reverse_engineering {SID}": ("start", [JOB, "rezepte-app", "reverse_engineering", SID]),
            f"status {JOB}": ("status", [JOB]),
            f"pending {JOB}": ("pending", [JOB]),
            f"approve {JOB} {FP}": ("approve", [JOB, FP]),
            f"cancel {JOB}": ("cancel", [JOB]),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(D.parse_command(raw), expected)

    def test_rejected_commands(self):
        bad = [None, "", "id", "ping; id", "ping && id", "Ping", "ping\nid", "status  " + JOB, " ping",
               "cat /etc/wamo/geheim.env", "start ../x a b", f"start {JOB} wamo umsetzung",
               f"start {JOB} demo-app zaubern", f"start {JOB.upper()} demo-app test", f"start {JOB} demo--app test",
               f"start {JOB} -demo test", f"start {JOB} demo-app test keine-uuid", f"start {JOB} demo-app",
               f"status {JOB} extra", f"approve {JOB} xyz", f"approve {JOB} {FP.upper()}", f"approve {JOB} {FP[:16]}",
               "status abc12345", f"status {JOB}$(id)", "start " + "a" * 400, "bash -c id", "scp -t /tmp",
               "rsync --server . /srv", "git-upload-pack 'x'", "sftp", "internal-sftp"]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(D.Abgewiesen):
                D.parse_command(raw)

    def test_main_rejects_without_shell(self):
        """Über SSH zählt nur SSH_ORIGINAL_COMMAND; ohne Befehl (Shell-Wunsch) wird abgewiesen."""
        with mock.patch.dict(os.environ, {"SSH_CONNECTION": "1.2.3.4 5 6.7.8.9 22"}, clear=False), \
             mock.patch.object(D, "log"), mock.patch("sys.stderr"):
            os.environ.pop("SSH_ORIGINAL_COMMAND", None)
            self.assertEqual(D.main(["ping"]), D.EXIT_ABGEWIESEN)  # argv zählt über SSH nicht
            with mock.patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "id"}):
                self.assertEqual(D.main([]), D.EXIT_ABGEWIESEN)

    def test_header(self):
        auftrag, system, modell = D.parse_header(
            json.dumps({"auftrag": "Baue\u0007 eine App\nmit Login", "system": "x"}).encode() + b"\n")
        self.assertEqual(auftrag, "Baue eine App\nmit Login")
        self.assertEqual(system, "x")
        self.assertIsNone(modell)
        self.assertEqual(D.parse_header(b'{"auftrag": "x", "modell": "claude-opus-5-5"}\n')[2], "claude-opus-5-5")
        for raw in [b'{"auftrag": "x"}', b'kein json\n', b'[]\n', b'{"auftrag": "x", "befehl": "id"}\n',
                    b'{"auftrag": 1}\n', b'{"auftrag": "   "}\n', b"[" * 5000 + b"\n",
                    b'{"auftrag": "x", "modell": 5}\n', b'{"auftrag": "x", "modell": "--dangerously"}\n',
                    b'{"auftrag": "x", "modell": "a b"}\n',
                    json.dumps({"auftrag": "x" * (D.MAX_TEXT_CHARS + 1)}).encode() + b"\n"]:
            with self.subTest(raw=raw[:40]), self.assertRaises(D.Abgewiesen):
                D.parse_header(raw)


class SecretsAndEnvTests(unittest.TestCase):
    GEHEIM = {"ANTHROPIC_API_KEY": "sk-ant-aaaaaaaaaaaa", "CLAUDE_CODE_OAUTH_TOKEN": "oauth-bbbbbbbbbbbb",
              "GH_TOKEN": "ghp_cccccccccccc", "GH_TOKEN_LESEN": "ghp_lesenlesen12", "VERCEL_TOKEN": "vercel-dddddddd",
              "VERCEL_ORG_ID": "team_eeeeeeee", "SUPABASE_ACCESS_TOKEN": "sbp_ffffffffffff",
              "SONIOX_API_KEY": "soniox-gggggggg", "LITELLM_API_KEY": "litellm-hhhhhhhh",
              "JIRA_API_TOKEN": "jira-iiiiiiiiii", "JIRA_EMAIL": "bot@wamocon.com", "JIRA_URL": "https://wamocon.atlassian.net"}

    def test_read_secrets_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, "geheim.env")
            p.write_text("# Kommentar\nANTHROPIC_API_KEY=sk-ant-x\nexport GH_TOKEN='ghp_y'\nVERCEL_TOKEN=\n"
                         "SONIOX_API_KEY=\"s z\"\nkaputt\nlower=x\n", encoding="utf-8")
            self.assertEqual(D.lese_geheimnisse(p), {"ANTHROPIC_API_KEY": "sk-ant-x", "GH_TOKEN": "ghp_y",
                                                     "SONIOX_API_KEY": "s z"})
            self.assertEqual(D.lese_geheimnisse(Path(tmp, "fehlt.env")), {})

    def test_env_is_allowlist_per_art(self):
        recherche = D.sandbox_env("recherche", self.GEHEIM)
        self.assertEqual(recherche["ANTHROPIC_API_KEY"], self.GEHEIM["ANTHROPIC_API_KEY"])
        for name in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "VERCEL_TOKEN", "SUPABASE_ACCESS_TOKEN", "SONIOX_API_KEY",
                     "LITELLM_API_KEY", "JIRA_API_TOKEN"):
            self.assertNotIn(name, recherche)
        datenbank = D.sandbox_env("datenbank", self.GEHEIM)
        self.assertEqual(datenbank["GH_TOKEN"], self.GEHEIM["GH_TOKEN"])
        self.assertIn("SUPABASE_ACCESS_TOKEN", datenbank)
        self.assertNotIn("VERCEL_TOKEN", datenbank)
        deployment = D.sandbox_env("deployment", self.GEHEIM)
        self.assertTrue({"GH_TOKEN", "VERCEL_TOKEN", "VERCEL_ORG_ID", "SUPABASE_ACCESS_TOKEN"} <= set(deployment))
        for art in wk.AUFTRAGSARTEN:
            env = D.sandbox_env(art, self.GEHEIM)
            with self.subTest(art=art):
                self.assertNotIn("SONIOX_API_KEY", env)
                self.assertNotIn("LITELLM_API_KEY", env)
                self.assertNotIn("GH_TOKEN_LESEN", env)
                self.assertEqual(sum(n in env for n in wk.CLAUDE_VARIABLEN), 1)  # genau ein Claude-Zugang
                erlaubt = set(D.basis_env()) | set(wk.CLAUDE_VARIABLEN) | {wk.sandbox_name(n) for n in wk.zugangs_variablen(art)}
                self.assertLessEqual({k for k in env if not k.startswith("GIT_CONFIG_")}, erlaubt)

    def test_reading_arts_get_only_read_token(self):
        for art in ("planung", "review", "analyse", "sicherheit", "reverse_engineering", "dokumentation"):
            with self.subTest(art=art):
                self.assertEqual(D.sandbox_env(art, self.GEHEIM)["GH_TOKEN"], self.GEHEIM["GH_TOKEN_LESEN"])
                ohne_lesen = {k: v for k, v in self.GEHEIM.items() if k != "GH_TOKEN_LESEN"}
                self.assertNotIn("GH_TOKEN", D.sandbox_env(art, ohne_lesen))  # nie das schreibende Token
                self.assertIn("GH_TOKEN_LESEN", D.zugangs_quellen(art, self.GEHEIM))
                self.assertNotIn("GH_TOKEN", D.zugangs_quellen(art, self.GEHEIM))
        self.assertIn("GH_TOKEN", D.zugangs_quellen("umsetzung", self.GEHEIM))

    def test_git_in_sandbox_without_hooks_and_with_token_helper(self):
        env = D.sandbox_env("umsetzung", self.GEHEIM)
        git = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(git["core.hooksPath"], "/dev/null")
        self.assertEqual(git["core.fsmonitor"], "false")
        self.assertEqual(git["credential.https://github.com.helper"], "!/usr/bin/gh auth git-credential")
        recherche = D.sandbox_env("recherche", self.GEHEIM)
        self.assertEqual(recherche["GIT_CONFIG_COUNT"], "2")  # ohne Token kein Helfer, Hooks trotzdem aus

    def test_workspace_header_for_organisation_key(self):
        geheim = {**self.GEHEIM, "ANTHROPIC_WORKSPACE_ID": "wrkspc_01ABCdef"}
        env = D.sandbox_env("review", geheim)
        self.assertEqual(env["ANTHROPIC_CUSTOM_HEADERS"], "anthropic-workspace-id: wrkspc_01ABCdef")
        self.assertNotIn("ANTHROPIC_WORKSPACE_ID", env)
        kaputt = D.sandbox_env("review", {**geheim, "ANTHROPIC_WORKSPACE_ID": "x\nX-Evil: 1"})
        self.assertNotIn("ANTHROPIC_CUSTOM_HEADERS", kaputt)  # keine eingeschleusten Header
        oauth = D.sandbox_env("review", {"CLAUDE_CODE_OAUTH_TOKEN": "o", "ANTHROPIC_WORKSPACE_ID": "wrkspc_1"})
        self.assertNotIn("ANTHROPIC_CUSTOM_HEADERS", oauth)

    def test_jira_key_only_for_jira_arts(self):
        geheim = {**self.GEHEIM, "JIRA_API_KEY": "jira-kkkkkkkk"}
        self.assertEqual(D.sandbox_env("planung", geheim)["JIRA_API_KEY"], "jira-kkkkkkkk")
        self.assertNotIn("JIRA_API_KEY", D.sandbox_env("sicherheit", geheim))

    def test_oauth_only_and_no_claude_access(self):
        env = D.sandbox_env("review", {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-x"})
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "oauth-x")
        self.assertIsNone(D.sandbox_env("review", {"GH_TOKEN": "ghp_x"}))

    def test_literal_secrets_become_references(self):
        servers = {"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/",
                              "headers": {"Authorization": "Bearer ghp_cccccccccccc"}},
                   "supabase": {"command": "npx", "args": ["-y", "x", "--token", "sbp_ffffffffffff"]}}
        out = D.ohne_geheimnisse(servers, self.GEHEIM)
        self.assertEqual(out["github"]["headers"]["Authorization"], "Bearer ${GH_TOKEN}")
        self.assertEqual(out["supabase"]["args"][-1], "${SUPABASE_ACCESS_TOKEN}")
        self.assertNotIn("ghp_cccccccccccc", json.dumps(out))

    def test_mcp_entries_only_placeholders_and_harmless_literals(self):
        gut = {  # die Einträge des Koordinators auf Sokrates-2
            "rea": {"type": "stdio", "command": "npx", "args": ["-y", "rea-agents@6.5.0", "mcp"], "env": {}},
            "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/",
                       "headers": {"Authorization": "Bearer ${GH_TOKEN:-}"}},
            "supabase": {"type": "stdio", "command": "npx", "args": ["-y", "@supabase/mcp-server-supabase@0.13.0", "--read-only"]},
            "jira": {"type": "stdio", "command": "/srv/wamo/.local/bin/mcp-atlassian", "args": [],
                     "env": {"JIRA_URL": "https://wamocon.atlassian.net", "JIRA_USERNAME": "${JIRA_EMAIL:-}",
                             "JIRA_API_TOKEN": "${JIRA_API_KEY:-}", "READ_ONLY_MODE": "true"}},
        }
        for name, server in gut.items():
            with self.subTest(gut=name):
                self.assertIsNone(D.mcp_server_pruefen(server))
        schlecht = [
            {"env": {"TOKEN": "ghp_1234567890abcdefghijklmnop"}},
            {"headers": {"Authorization": "Bearer abcdefghijklmnop1234567890"}},
            {"headers": {"Authorization": "Bearer ${GH_TOKEN:-ghp_1234567890abcdefghij}"}},
            {"url": "https://user:geheim@example.com/mcp"},
            {"url": "https://example.com/mcp?token=abc"},
            {"args": ["--access-token=sbp_1234567890abcdefghijklmn"]},
            {"args": ["sbp1234567890abcdefghijklmnop"]},
            {"env": {"X": 5}}, "kaputt",
        ]
        for server in schlecht:
            with self.subTest(schlecht=server):
                self.assertIsNotNone(D.mcp_server_pruefen(server))

    def test_npx_servers_run_from_global_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            glob = Path(tmp)
            for name, version, bins in (("rea-agents", "6.5.0", {"rea": "a.mjs", "rea-agents": "a.mjs"}),
                                        ("@supabase/mcp-server-supabase", "0.5.0", {"mcp-server-supabase": "b.js"})):
                pkg = glob / "lib" / "node_modules" / name
                pkg.mkdir(parents=True)
                (pkg / "package.json").write_text(json.dumps({"version": version, "bin": bins}), encoding="utf-8")
            (glob / "bin").mkdir()
            for b in ("rea-agents", "mcp-server-supabase"):
                (glob / "bin" / b).write_text("", encoding="utf-8")
            with mock.patch.object(D, "NPM_GLOBAL", glob):
                rea = D.npx_direkt({"type": "stdio", "command": "npx", "args": ["-y", "rea-agents@6.5.0", "mcp"], "env": {}})
                self.assertEqual((rea["command"], rea["args"]), (str(glob / "bin" / "rea-agents"), ["mcp"]))
                sb = D.npx_direkt({"command": "npx", "args": ["-y", "@supabase/mcp-server-supabase@latest", "--read-only"]})
                self.assertEqual((sb["command"], sb["args"]), (str(glob / "bin" / "mcp-server-supabase"), ["--read-only"]))
                for unchanged in ({"command": "npx", "args": ["-y", "rea-agents@6.4.0", "mcp"]},
                                  {"command": "npx", "args": ["-y", "unbekannt", "mcp"]},
                                  {"command": "npx", "args": ["-y", "../../x", "mcp"]},
                                  {"command": "node", "args": ["x.js"]}, {"type": "http", "url": "https://x"}, "kaputt"):
                    with self.subTest(server=unchanged):
                        self.assertEqual(D.npx_direkt(unchanged), unchanged)


class JobStateTests(unittest.TestCase):
    """Auftragsordner: Anfragen prüfen, Freigaben je Lauf bereitstellen, keinen Links folgen, Buchführung."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs = Path(self.tmp.name) / "jobs"
        self.jobdir = self.jobs / JOB
        (self.jobdir / "zustand").mkdir(parents=True)
        (Path(self.tmp.name) / "projekte" / "rezepte-app").mkdir(parents=True)
        self.patches = [mock.patch.object(D, "JOBS", self.jobs),
                        mock.patch.object(D, "PROJEKTE", Path(self.tmp.name) / "projekte"),
                        mock.patch.object(D, "LOGS", Path(self.tmp.name)),
                        mock.patch.object(D, "exklusiv", lambda pfad: contextlib.nullcontext())]
        if not LINUX:
            self.patches.append(mock.patch.object(D, "projekt_entschaerfen", return_value=[]))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def pending_lines(self):
        push = {"command": "git push origin main", "description": "x"}
        good = {"id": dev_guard.fingerprint("Bash", push), "grund": "egal", "tool": "Bash", "input": push}
        forged = {**good, "id": "0" * 64}
        allowed = {"id": dev_guard.fingerprint("Bash", {"command": "ls"}), "grund": "x", "tool": "Bash", "input": {"command": "ls"}}
        own = {"id": dev_guard.fingerprint("WAMO-Zugang", {"neu": ["GH_TOKEN"]}), "grund": "x", "tool": "WAMO-Zugang",
               "input": {"neu": ["GH_TOKEN"]}}
        return good, [json.dumps(good), json.dumps(forged), json.dumps(allowed), json.dumps(own), "kein json", "[]",
                      json.dumps(good)]

    def test_pending_is_revalidated(self):
        good, lines = self.pending_lines()
        (self.jobdir / "zustand" / "pending.jsonl").write_text("\n".join(lines), encoding="utf-8")
        out = D.gepruefte_anfragen(self.jobdir, "rezepte-app")
        self.assertEqual(out, [{"id": good["id"], "grund": "git push", "tool": "Bash", "input": good["input"]}])

    def test_pending_limits(self):
        zeilen = []
        for i in range(300):  # 300 verschiedene, gültige Anfragen
            eingabe = {"command": f"git push origin b{i}"}
            zeilen.append(json.dumps({"id": dev_guard.fingerprint("Bash", eingabe), "tool": "Bash", "input": eingabe}))
        lang = {"command": "git push " + "x" * 9000}
        zeilen.insert(0, json.dumps({"id": dev_guard.fingerprint("Bash", lang), "tool": "Bash", "input": lang}))
        tief = {"command": "git push", "x": json.loads("[" * 30 + "]" * 30)}
        zeilen.insert(0, json.dumps({"id": dev_guard.fingerprint("Bash", tief), "tool": "Bash", "input": tief}))
        (self.jobdir / "zustand" / "pending.jsonl").write_text("\n".join(zeilen), encoding="utf-8")
        out = D.gepruefte_anfragen(self.jobdir, "rezepte-app")
        self.assertEqual(len(out), D.MAX_OFFENE_FREIGABEN)
        self.assertEqual(out[0]["input"]["command"], "git push origin b0")  # zu lang und zu tief übersprungen

    def test_run_snapshot_of_approvals(self):
        good, lines = self.pending_lines()
        zustand = self.jobdir / "zustand"
        (zustand / "pending.jsonl").write_text("\n".join(lines), encoding="utf-8")
        (zustand / "approvals.json.used").write_text("alt\n", encoding="utf-8")
        (self.jobdir / "freigaben.json").write_text(json.dumps([good["id"], "kaputt", 5]), encoding="utf-8")
        D.lauf_vorbereiten(self.jobdir, "rezepte-app")
        self.assertEqual(json.loads((zustand / "approvals.json").read_text(encoding="utf-8")), [good["id"]])
        self.assertEqual((zustand / "approvals.json.used").read_text(encoding="utf-8"), "")
        self.assertEqual((zustand / "pending.jsonl").read_text(encoding="utf-8"), "")
        self.assertFalse((self.jobdir / "freigaben.json").exists())
        angefragt = [json.loads(line)["id"] for line in (self.jobdir / "angefragt.jsonl").read_text().splitlines()]
        self.assertEqual(angefragt, [good["id"]])
        D.lauf_vorbereiten(self.jobdir, "rezepte-app")  # nächster Lauf ohne neue Freigabe: leer
        self.assertEqual(json.loads((zustand / "approvals.json").read_text(encoding="utf-8")), [])

    def test_directory_instead_of_file_is_replaced(self):
        """N2: ein Ordner an Stelle einer Zustandsdatei legt den Auftrag nicht lahm."""
        (self.jobdir / "zustand" / "approvals.json.used").mkdir()
        (self.jobdir / "zustand" / "approvals.json.used" / "x").write_text("x", encoding="utf-8")
        D.lauf_vorbereiten(self.jobdir, "rezepte-app")
        self.assertTrue((self.jobdir / "zustand" / "approvals.json.used").is_file())

    def test_planted_symlinks_are_not_followed(self):
        zustand = self.jobdir / "zustand"
        ziel = Path(self.tmp.name) / "fremde-datei"
        ziel.write_text("unverändert", encoding="utf-8")
        try:
            os.symlink(ziel, zustand / "approvals.json.used")
            os.symlink(ziel, zustand / "pending.jsonl")
        except (OSError, NotImplementedError):
            self.skipTest("symbolische Links hier nicht möglich")
        self.assertEqual(D.gepruefte_anfragen(self.jobdir, "rezepte-app"), [])
        D.lauf_vorbereiten(self.jobdir, "rezepte-app")
        self.assertEqual(ziel.read_text(encoding="utf-8"), "unverändert")
        self.assertFalse((zustand / "approvals.json.used").is_symlink())

    @unittest.skipUnless(LINUX, "FIFO nur unter Linux")
    def test_fifo_does_not_block(self):
        os.mkfifo(self.jobdir / "zustand" / "pending.jsonl")
        self.assertEqual(D.gepruefte_anfragen(self.jobdir, "rezepte-app"), [])
        self.assertIsNone(D.read_no_follow(self.jobdir / "zustand" / "pending.jsonl", 100))

    def test_job_bound_to_project_and_art(self):
        with mock.patch.object(D, "HEIM", Path(self.tmp.name) / "heim"), \
             mock.patch.object(D, "lese_geheimnisse", return_value={}):
            D.vorbereiten(self.jobdir, JOB, "rezepte-app", "umsetzung")
            D.vorbereiten(self.jobdir, JOB, "rezepte-app", "umsetzung")  # derselbe Auftrag: ok
            with self.assertRaises(D.Abgewiesen):
                D.vorbereiten(self.jobdir, JOB, "andere-app", "umsetzung")
            with self.assertRaises(D.Abgewiesen):
                D.vorbereiten(self.jobdir, JOB, "rezepte-app", "deployment")
            conf = json.loads((self.jobdir / "claude" / ".claude.json").read_text(encoding="utf-8"))
            self.assertIs(conf["autoUpdates"], False)
            self.assertIn("wamo:x:", (self.jobdir / "etc" / "passwd").read_text(encoding="utf-8"))

    def test_claude_config_rewritten_every_run(self):
        heim = Path(self.tmp.name) / "heim"
        (heim / ".claude").mkdir(parents=True)
        (heim / ".claude.json").write_text(json.dumps({
            "userID": "u", "oauthAccount": {"emailAddress": "x@y"}, "projects": {"/x": {}},
            "mcpServers": {"github": {"type": "http", "headers": {"Authorization": "Bearer ghp_geheimgeheim"}},
                           "boese": {"type": "stdio", "command": "x", "env": {"T": "abcdefghijklmnopqrstuvwxyz123456"}}}}),
            encoding="utf-8")
        (heim / ".claude" / "settings.json").write_text(json.dumps({
            "enabledPlugins": {"ecc@ecc": True}, "hooks": {"PreToolUse": []}, "statusLine": {"command": "x"}}))
        claude_dir = self.jobdir / "claude"
        claude_dir.mkdir()
        # ein früherer Lauf hat in der Auftragskonfiguration Hooks und projektbezogene MCP-Server eingetragen
        (claude_dir / ".claude.json").write_text(json.dumps({"projects": {"/srv/wamo/projekte/x": {"mcpServers": {"a": {}}}}}))
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"command": "x"}]}]}}))
        (claude_dir / "settings.local.json").write_text("{}")
        with mock.patch.object(D, "HEIM", heim):
            D.claude_konfiguration(claude_dir, {"GH_TOKEN": "ghp_geheimgeheim"})
        text = (claude_dir / ".claude.json").read_text(encoding="utf-8")
        conf = json.loads(text)
        self.assertNotIn("ghp_geheimgeheim", text)
        self.assertNotIn("oauthAccount", conf)
        self.assertNotIn("projects", conf)
        self.assertEqual(list(conf["mcpServers"]), ["github"])  # der Eintrag mit Literal-Token fällt weg
        self.assertEqual(conf["mcpServers"]["github"]["headers"]["Authorization"], "Bearer ${GH_TOKEN}")
        settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings, {"enabledPlugins": {"ecc@ecc": True}})
        self.assertFalse((claude_dir / "settings.local.json").exists())

    def test_access_bookkeeping(self):
        """H3: Ein Lauf mit Token außerhalb der Schnittmenge früherer Läufe wartet auf eine Freigabe."""
        projekt = Path(self.tmp.name) / "projekte" / "rezepte-app"
        klein, gross = {"ANTHROPIC_API_KEY"}, {"ANTHROPIC_API_KEY", "GH_TOKEN"}
        self.assertIsNone(D.zugang_pruefen("rezepte-app", gross, []))  # leeres Projekt: frisch, kein Prüfbedarf
        self.assertIsNone(D.zugang_pruefen("rezepte-app", klein, []))
        (projekt / "datei").write_text("x")
        anfrage = D.zugang_pruefen("rezepte-app", gross, [])
        self.assertEqual(anfrage["tool"], "WAMO-Zugang")
        self.assertEqual(anfrage["input"]["neu"], ["GH_TOKEN"])
        self.assertEqual(anfrage["id"], dev_guard.fingerprint("WAMO-Zugang", anfrage["input"]))
        self.assertIsNone(D.zugang_pruefen("rezepte-app", gross, [anfrage["id"]]))  # freigegeben
        self.assertIsNone(D.zugang_pruefen("rezepte-app", gross, []))  # danach gilt das Projekt als geprüft
        self.assertIsNone(D.zugang_pruefen("rezepte-app", klein, []))  # weniger Token: frei
        self.assertIsNotNone(D.zugang_pruefen("rezepte-app", gross, []))  # wieder mehr: wieder prüfen
        (self.jobs / ".projekte" / "rezepte-app.json").unlink()  # Buchführung fehlt, Projekt hat Inhalt
        self.assertIsNotNone(D.zugang_pruefen("rezepte-app", klein, []))


@unittest.skipUnless(LINUX, "Verzeichnis-Deskriptoren (dir_fd) nur unter Linux")
class EntschaerfenTests(unittest.TestCase):
    def test_project_autostart_surfaces_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            projekt = Path(tmp)
            (projekt / ".git" / "hooks").mkdir(parents=True)
            (projekt / ".git" / "hooks" / "pre-commit").write_text("x")
            (projekt / ".git" / "hooks" / "pre-commit.sample").write_text("x")
            (projekt / ".git" / "config").write_text(
                "[core]\n\trepositoryformatversion = 0\n\tfsmonitor = touch /tmp/x\n"
                "[remote \"origin\"]\n\turl = https://github.com/o/r.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
                "[alias]\n\tst = !sh\n")
            (projekt / ".mcp.json").write_text("{}")
            (projekt / ".claude").mkdir()
            (projekt / ".claude" / "settings.json").write_text("{}")
            (projekt / ".claude" / "agents").mkdir()
            (projekt / ".claude" / "agents" / "evil.md").write_text("x")
            entfernt = D.projekt_entschaerfen(projekt)
            self.assertFalse((projekt / ".mcp.json").exists())
            self.assertFalse((projekt / ".claude").exists())  # ganzes .claude weg (settings, agents, ...)
            self.assertFalse((projekt / ".git" / "hooks" / "pre-commit").exists())
            self.assertTrue((projekt / ".git" / "hooks" / "pre-commit.sample").exists())
            config = (projekt / ".git" / "config").read_text()
            self.assertIn("https://github.com/o/r.git", config)
            self.assertNotIn("fsmonitor", config)
            self.assertNotIn("alias", config)
            self.assertTrue(any("config bereinigt" in e for e in entfernt))

    BOESE = "[core]\n\trepositoryformatversion = 0\n\tfsmonitor = touch /tmp/x\n\tpager = touch /tmp/y\n"

    def _setup(self, tmp):
        p = Path(tmp)
        (p / ".git" / "hooks").mkdir(parents=True)
        (p / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n")
        (p / ".git" / "config").write_text(self.BOESE)
        return p

    def _boese(self, pfad):
        try:
            t = pfad.read_text(errors="replace")
        except OSError:
            return "nicht lesbar"
        return "UNBEREINIGT" if ("fsmonitor" in t or "pager" in t) else "bereinigt"

    def test_git_als_datei_entfernt(self):  # H2: .git als gitdir-Zeiger
        with tempfile.TemporaryDirectory() as tmp:
            p = self._setup(tmp)
            os.rename(p / ".git", p / ".echtgit")
            (p / ".git").write_text("gitdir: .echtgit\n")
            D.projekt_entschaerfen(p)
            self.assertFalse((p / ".git").exists())

    def test_git_config_als_link_ersetzt(self):  # H2: config ist ein Link ins Projekt
        with tempfile.TemporaryDirectory() as tmp:
            p = self._setup(tmp)
            (p / ".evil.cfg").write_text(self.BOESE)
            (p / ".git" / "config").unlink()
            os.symlink("../.evil.cfg", p / ".git" / "config")
            D.projekt_entschaerfen(p)
            self.assertFalse((p / ".git" / "config").is_symlink())
            self.assertEqual(self._boese(p / ".git" / "config"), "bereinigt")
            self.assertEqual(self._boese(p / ".evil.cfg"), "UNBEREINIGT")  # Linkziel unangetastet, Link ersetzt

    def test_git_config_zu_gross_ersetzt(self):  # H2: config über 1 MiB
        with tempfile.TemporaryDirectory() as tmp:
            p = self._setup(tmp)
            (p / ".git" / "config").write_text("# " + "x" * (1024 * 1024 + 10) + "\n" + self.BOESE)
            D.projekt_entschaerfen(p)
            self.assertEqual(self._boese(p / ".git" / "config"), "bereinigt")

    def test_eingebettetes_repo(self):  # H2: .git tief im Baum
        with tempfile.TemporaryDirectory() as tmp:
            p = self._setup(tmp)
            (p / "sub" / "tief" / ".git").mkdir(parents=True)
            (p / "sub" / "tief" / ".git" / "config").write_text(self.BOESE)
            (p / "sub" / "tief" / ".git" / "hooks").mkdir()
            (p / "sub" / "tief" / ".git" / "hooks" / "pre-commit").write_text("x")
            D.projekt_entschaerfen(p)
            self.assertEqual(self._boese(p / "sub" / "tief" / ".git" / "config"), "bereinigt")
            self.assertFalse((p / "sub" / "tief" / ".git" / "hooks" / "pre-commit").exists())

    def test_fail_closed_bei_chmod_100(self):  # H1: unlesbarer Projektordner startet nicht
        with tempfile.TemporaryDirectory() as tmp:
            p = self._setup(tmp)
            os.chmod(p, 0o100)
            try:
                # chmod in projekt_entschaerfen macht wieder lesbar; sonst wäre os.open fehlgeschlagen (fail-closed)
                D.projekt_entschaerfen(p)
                self.assertEqual(self._boese(p / ".git" / "config"), "bereinigt")
            finally:
                os.chmod(p, 0o700)

    def test_inventur_und_vergleich(self):  # H3
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "package.json").write_text('{"scripts": {"build": "tsc", "prepare": "./evil.sh"}}')
            (p / ".npmrc").write_text("x")
            (p / "node_modules" / ".bin").mkdir(parents=True)
            (p / "node_modules" / ".bin" / "tsc").write_text("x")
            inv1 = D.projekt_inventur(p)
            self.assertIn("prepare=./evil.sh", inv1["package.json"])
            self.assertIn(".npmrc", inv1)
            self.assertIn("node_modules/.bin", inv1)
            self.assertEqual(D.inventur_vergleich(inv1, inv1), [])
            (p / "package.json").write_text('{"scripts": {"build": "tsc"}}')
            (p / "CLAUDE.md").write_text("x")
            inv2 = D.projekt_inventur(p)
            geaendert = D.inventur_vergleich(inv1, inv2)
            self.assertTrue(any(z.startswith("geändert: package.json") for z in geaendert))
            self.assertTrue(any(z.startswith("neu: CLAUDE.md") for z in geaendert))

    def test_fail_closed_buchfuehrung_chmod_100(self):  # H1
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp) / "jobs"
            jobs.mkdir()
            projekt = Path(tmp) / "projekte" / "p"
            projekt.mkdir(parents=True)
            (projekt / "datei").write_text("x")
            with mock.patch.object(D, "JOBS", jobs), mock.patch.object(D, "PROJEKTE", Path(tmp) / "projekte"):
                (jobs / ".projekte").mkdir()
                (jobs / ".projekte" / "p.json").write_text(json.dumps({"schnitt": ["ANTHROPIC_API_KEY"]}))
                mehr = {"ANTHROPIC_API_KEY", "GH_TOKEN"}
                os.chmod(projekt, 0o100)
                try:
                    anfrage = D.zugang_pruefen("p", mehr, [])
                finally:
                    os.chmod(projekt, 0o700)
                self.assertIsNotNone(anfrage)  # nicht lesbar -> gilt nicht als leer -> Freigabe verlangt


@unittest.skipUnless(LINUX, "AF_UNIX-Server nur unter Linux")
class FreigabeServerTests(unittest.TestCase):
    """M5: der Dispatcher-Server vergibt Freigaben atomar; der Wächter-Client reserviert und bestätigt."""

    def _anfrage(self, sock_path, op, fp):
        import socket as s
        with s.socket(s.AF_UNIX, s.SOCK_STREAM) as c:
            c.settimeout(4)
            c.connect(sock_path)
            c.sendall((json.dumps({"op": op, "fp": fp}) + "\n").encode())
            return json.loads(c.makefile("rb").readline(4096)).get("ok")

    def test_reserve_commit_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            fp = "a" * 64
            srv = D.FreigabeServer(Path(tmp) / "f.sock", [fp])  # genau einmal freigegeben
            srv.start()
            try:
                sp = srv.sock_path
                self.assertTrue(self._anfrage(sp, "reserve", fp))     # erste Reservierung
                self.assertFalse(self._anfrage(sp, "reserve", fp))    # keine zweite (nur einmal frei)
                self.assertTrue(self._anfrage(sp, "release", fp))     # Hook-Sperre: zurückgeben
                self.assertTrue(self._anfrage(sp, "reserve", fp))     # danach wieder frei
                self.assertTrue(self._anfrage(sp, "commit", fp))      # ausgeführt
                self.assertFalse(self._anfrage(sp, "reserve", fp))    # verbraucht
                self.assertFalse(self._anfrage(sp, "reserve", "b" * 64))  # nie freigegeben
            finally:
                srv.stop()

    def test_guard_client_uses_socket(self):
        import dev_guard as g
        with tempfile.TemporaryDirectory() as tmp:
            fp = g.fingerprint("Bash", {"command": "git push"})
            srv = D.FreigabeServer(Path(tmp) / "f.sock", [fp, fp])  # zweimal freigegeben
            srv.start()
            try:
                self.assertTrue(g._socket_anfrage(srv.sock_path, "reserve", fp))
                self.assertTrue(g._socket_anfrage(srv.sock_path, "commit", fp))
                self.assertTrue(g._socket_anfrage(srv.sock_path, "reserve", fp))  # zweite Freigabe offen
                self.assertTrue(g._socket_anfrage(srv.sock_path, "commit", fp))
                self.assertFalse(g._socket_anfrage(srv.sock_path, "reserve", fp))
            finally:
                srv.stop()
        # nicht erreichbarer Socket: fail-closed
        self.assertFalse(g._socket_anfrage(str(Path(tmp) / "weg.sock"), "reserve", fp))


class GitConfigTests(unittest.TestCase):
    def test_filter_allowlist(self):
        eintraege = [("core.repositoryformatversion", "0"), ("core.fsmonitor", "x"), ("core.hooksPath", "x"),
                     ("core.sshCommand", "x"), ("core.pager", "x"), ("remote.origin.url", "https://github.com/o/r.git"),
                     ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"), ("remote.evil.url", "ext::sh -c x"),
                     ("remote.tok.url", "https://u:p@github.com/o/r.git"), ("remote.origin.uploadpack", "x"),
                     ("branch.main.remote", "origin"), ("alias.st", "!sh"), ("filter.x.smudge", "x"),
                     ("url.https://evil/.insteadof", "https://github.com/"), ("include.path", "x"),
                     ("extensions.worktreeconfig", "true"), ("user.name", "WAMO")]
        out = dict(D.git_config_filtern(eintraege))
        self.assertEqual(set(out), {"core.repositoryformatversion", "remote.origin.url", "remote.origin.fetch",
                                    "branch.main.remote", "user.name"})


E = _load("wamo_job_ecc", "wamo-job-ecc.py")


class EccKopieTests(unittest.TestCase):
    """Punkt 2: ECC für die Sandbox ohne Hooks, Namen bleiben gleich."""

    def _plugin(self, wurzel: Path) -> Path:
        cache = wurzel / "cache" / "ecc" / "ecc" / "2.2.3"
        for rel, inhalt in {
            ".claude-plugin/plugin.json": json.dumps({"name": "ecc", "version": "2.2.3", "skills": ["./skills/"],
                                                      "hooks": "./hooks/hooks.json", "mcpServers": {"x": {}},
                                                      "userConfig": {"hooks_enabled": {}, "anders": {}}}),
            ".claude-plugin/marketplace.json": json.dumps({"name": "ecc", "plugins": [
                {"name": "ecc", "source": "./", "hooks": "x", "version": "2.2.3"}, {"name": "fremd", "source": "./f"}]}),
            "hooks/hooks.json": '{"hooks": {"SessionStart": []}}',
            "scripts/hooks/session-start.js": "x", "scripts/lib/x.js": "y", ".mcp.json": "{}", ".in_use/1": "",
            "skills/verification-loop/SKILL.md": "# v", "agents/code-reviewer.md": "# a", "commands/plan.md": "# p",
        }.items():
            (cache / rel).parent.mkdir(parents=True, exist_ok=True)
            (cache / rel).write_text(inhalt, encoding="utf-8")
        (wurzel / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"ecc@ecc": [
            {"scope": "user", "installPath": str(cache), "version": "2.2.3"}]}}), encoding="utf-8")
        return cache

    def test_kopie_ohne_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            quelle, ziel = Path(tmp) / "quelle", Path(tmp) / "opt" / "claude-plugins"
            ziel.parent.mkdir()
            self._plugin(quelle)
            info = E.erzeugen(quelle, ziel, PurePosixPathLike("/srv/wamo"))
            self.assertEqual((info["skills"], info["agenten"], info["version"]), (1, 1, "2.2.3"))
            plugin = ziel / "cache" / "ecc" / "ecc" / "ohne-hooks"
            for weg in ("hooks", "scripts/hooks", ".mcp.json", ".in_use"):
                self.assertFalse((plugin / weg).exists(), weg)
            for bleibt in ("skills/verification-loop/SKILL.md", "agents/code-reviewer.md", "commands/plan.md",
                           "scripts/lib/x.js"):
                self.assertTrue((plugin / bleibt).is_file(), bleibt)
            meta = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["name"], "ecc")
            self.assertNotIn("hooks", meta)
            self.assertEqual(meta["mcpServers"], {})
            self.assertNotIn("hooks_enabled", meta["userConfig"])
            markt = json.loads((plugin / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
            self.assertEqual([p["name"] for p in markt["plugins"]], ["ecc"])
            self.assertNotIn("hooks", markt["plugins"][0])
            inst = json.loads((ziel / "installed_plugins.json").read_text(encoding="utf-8"))
            self.assertEqual(inst["plugins"]["ecc@ecc"][0]["installPath"],
                             "/srv/wamo/.claude/plugins/cache/ecc/ecc/ohne-hooks")  # Pfad, wie die Sandbox ihn sieht
            markets = json.loads((ziel / "known_marketplaces.json").read_text(encoding="utf-8"))
            self.assertEqual(markets["ecc"]["source"], {"source": "directory",
                                                        "path": "/srv/wamo/.claude/plugins/cache/ecc/ecc/ohne-hooks"})
            self.assertTrue((ziel / "data").is_dir())
            E.erzeugen(quelle, ziel, PurePosixPathLike("/srv/wamo"))  # wiederholbar (nach ECC-Updates)
            self.assertFalse(ziel.with_name("claude-plugins.alt").exists())

    def test_quelle_ausserhalb_des_caches_abgewiesen(self):
        with tempfile.TemporaryDirectory() as tmp:
            quelle = Path(tmp) / "quelle"
            self._plugin(quelle)
            (quelle / "installed_plugins.json").write_text(json.dumps({"plugins": {"ecc@ecc": [
                {"installPath": tmp}]}}), encoding="utf-8")
            with self.assertRaises(E.Fehler):
                E.erzeugen(quelle, Path(tmp) / "ziel", PurePosixPathLike("/srv/wamo"))

    @unittest.skipUnless(LINUX, "symbolische Links")
    def test_links_werden_nicht_kopiert(self):
        with tempfile.TemporaryDirectory() as tmp:
            quelle = Path(tmp) / "quelle"
            cache = self._plugin(quelle)
            os.symlink("/etc/passwd", cache / "skills" / "verification-loop" / "geheim")
            E.erzeugen(quelle, Path(tmp) / "ziel", PurePosixPathLike("/srv/wamo"))
            self.assertFalse((Path(tmp) / "ziel" / "cache/ecc/ecc/ohne-hooks/skills/verification-loop/geheim").exists())


def PurePosixPathLike(text: str):  # noqa: N802  (Heim-Pfad der Sandbox, auch unter Windows als POSIX)
    from pathlib import PurePosixPath
    return PurePosixPath(text)


class ModellTests(unittest.TestCase):
    """Kosten: Modell je Auftragsart, übersteuerbar nur mit einem erlaubten Namen."""

    def test_modell_je_art(self):
        for art in wk.AUFTRAGSARTEN:  # Standard für alle Arten: Haiku (Kosten)
            with self.subTest(art=art):
                self.assertEqual(wk.modell_fuer(art), "claude-haiku-5-5")
        self.assertEqual(wk.MODELLE_ERLAUBT, ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"))
        self.assertEqual(wk.modell_fuer("umsetzung", "claude-opus-5-5"), "claude-opus-5-5")  # nur ausdrücklich
        self.assertEqual(wk.modell_fuer("planung", "claude-sonnet-5-5"), "claude-sonnet-5-5")
        with self.assertRaises(ValueError):
            wk.modell_fuer("umsetzung", "claude-irgendwas")

    def test_claude_argv(self):
        argv = D.claude_argv("Regeln", "claude-haiku-5-5")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-haiku-5-5")
        self.assertNotIn("--resume", argv)
        self.assertEqual(D.claude_argv("R", "claude-opus-5-5", SID)[-2:], ["--resume", SID])


class AuditTests(unittest.TestCase):
    def test_cancel_und_approve_nur_einmal_im_audit(self):
        zeilen = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(D, "JOBS", Path(tmp)), \
             mock.patch.object(D, "log", side_effect=lambda t, pflicht=False: zeilen.append(t)), \
             mock.patch.object(D, "stop_units"), mock.patch.object(D, "emit"), mock.patch.dict(os.environ):
            os.environ.pop("SSH_CONNECTION", None)
            os.environ.pop("SSH_ORIGINAL_COMMAND", None)
            self.assertEqual(D.main(["cancel", JOB]), D.EXIT_OK)
        self.assertEqual([z for z in zeilen if z.startswith("cancel")], [f"cancel {JOB}"])


class StreamTests(unittest.TestCase):
    def test_sandbox_lines_claiming_to_be_dispatcher_events_are_dropped(self):
        self.assertTrue(D.ist_wamo_zeile(b'{"type": "wamo", "ereignis": "ende", "ergebnis": "fertig"}\n'))
        self.assertTrue(D.ist_wamo_zeile(b'{"type":" WAMO "}'))
        self.assertTrue(D.ist_wamo_zeile(b'{"type": "\\u0077amo", "ereignis": "ende"}'))
        self.assertFalse(D.ist_wamo_zeile(b'{"type": "assistant", "text": "wamo"}'))
        self.assertFalse(D.ist_wamo_zeile(b"wamo kein json"))

    def test_overlong_lines_are_dropped(self):
        strom = io.BytesIO(b"kurz\n" + b"x" * 100 + b"\nnoch\n")
        self.assertEqual(list(D.zeilen(strom, 50)), [b"kurz\n", None, b"noch\n"])

    def test_approval_used_more_often_than_granted_is_detected(self):
        push = {"command": "git push"}
        fp = dev_guard.fingerprint("Bash", push)
        z = D.Freigabezaehler([fp])

        def aufruf(i):
            return json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": push}]}}).encode()

        def ergebnis(i, text):
            return json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": text}]}}).encode()

        self.assertFalse(z.sehen(aufruf(1)))
        self.assertFalse(z.sehen(ergebnis(1, "PreToolUse:Bash hook error: [Fact-Forcing Gate] ...")))  # nicht gelaufen
        self.assertFalse(z.sehen(aufruf(2)))
        self.assertFalse(z.sehen(ergebnis(2, [{"type": "text", "text": "Exit code 1 fatal"}])))  # lief: 1 von 1
        self.assertFalse(z.sehen(aufruf(3)))
        self.assertTrue(z.sehen(ergebnis(3, "Everything up-to-date")))  # zweites Mal: mehr als freigegeben
        self.assertFalse(D.Freigabezaehler([]).sehen(aufruf(4)))

    @unittest.skipUnless(hasattr(__import__("signal"), "setitimer"), "setitimer nur unter Linux")
    def test_decide_time_limit(self):
        import time as zeit
        with self.assertRaises(D._Zeitueberschreitung):
            D.mit_zeitlimit(lambda: zeit.sleep(3), 0.2)


class SeccompTests(unittest.TestCase):
    """Rechnet den erzeugten cBPF-Filter mit einem kleinen Emulator durch (wie der Kernel)."""

    @staticmethod
    def ausfuehren(programm: bytes, nr: int, arch: int, args=(0, 0, 0, 0, 0, 0)) -> int:
        daten = struct.pack("<iIQ6Q", nr, arch, 0, *args)
        befehle = [struct.unpack("<HBBI", programm[i:i + 8]) for i in range(0, len(programm), 8)]
        pc, a = 0, 0
        for _ in range(1000):
            code, jt, jf, k = befehle[pc]
            if code == A.LD_W_ABS:
                a = struct.unpack_from("<I", daten, k)[0]
                pc += 1
            elif code == A.RET_K:
                return k
            elif code in (A.JEQ_K, A.JGE_K, A.JSET_K):
                treffer = a == k if code == A.JEQ_K else a >= k if code == A.JGE_K else bool(a & k)
                pc += 1 + (jt if treffer else jf)
            else:
                raise AssertionError(f"unbekannter Befehl {code:#x}")
        raise AssertionError("Endlosschleife")

    def test_filter_per_architecture(self):
        for arch, idx in (("aarch64", 0), ("x86_64", 1)):
            prog = A.seccomp_bpf(arch)
            audit = A.AUDIT_ARCH[arch]
            lauf = lambda nr, *args: self.ausfuehren(prog, nr, audit, args + (0,) * (6 - len(args)))  # noqa: E731
            with self.subTest(arch=arch):
                for name, nummern in A.SYSCALLS.items():
                    self.assertEqual(lauf(nummern[idx]), A.RET_ERRNO | A.EPERM, name)
                self.assertEqual(lauf(A.ENOSYS_SYSCALLS["clone3"][idx]), A.RET_ERRNO | A.ENOSYS)
                sock = A.SOCKET_NR[idx]
                # M2: Allowlist der Familien. AF_UNIX, AF_INET, AF_INET6, AF_NETLINK(Protokoll 0) erlaubt
                self.assertEqual(lauf(sock, 1, 1, 0), A.RET_ALLOW)  # AF_UNIX
                self.assertEqual(lauf(sock, 2, 1, 0), A.RET_ALLOW)  # AF_INET, SOCK_STREAM, TCP
                self.assertEqual(lauf(sock, 10, 1, 6), A.RET_ALLOW)  # AF_INET6, TCP
                self.assertEqual(lauf(sock, 2, 2, 1), A.RET_ALLOW)  # AF_INET, SOCK_DGRAM, UDP (DNS)
                self.assertEqual(lauf(sock, 16, 3, 0), A.RET_ALLOW)  # AF_NETLINK NETLINK_ROUTE (getaddrinfo)
                self.assertEqual(lauf(sock, 2, 1, 262), A.RET_ERRNO | A.EPERM)  # MPTCP
                self.assertEqual(lauf(sock, 10, 1, 262), A.RET_ERRNO | A.EPERM)
                self.assertEqual(lauf(sock, 2, 1, 132), A.RET_ERRNO | A.EPERM)  # SCTP
                self.assertEqual(lauf(sock, 16, 3, 4), A.RET_ERRNO | A.EPERM)  # NETLINK_SOCK_DIAG
                self.assertEqual(lauf(sock, 16, 3, 9), A.RET_ERRNO | A.EPERM)  # NETLINK_AUDIT
                for familie in (17, 40, 38, 31, 42, 45, 44):  # PACKET, VSOCK, ALG, BLUETOOTH, QIPCRTR, MCTP, XDP
                    self.assertEqual(lauf(sock, familie, 3, 0), A.RET_ERRNO | A.EPERM, familie)
                for (nummern, flag_idx) in A.SEND_SYSCALLS.values():
                    args = [0] * 6
                    args[flag_idx] = A.MSG_FASTOPEN | 0x40
                    self.assertEqual(lauf(nummern[idx], *args), A.RET_ERRNO | A.EPERM)
                    args[flag_idx] = 0x40
                    self.assertEqual(lauf(nummern[idx], *args), A.RET_ALLOW)
                self.assertEqual(lauf(63 if arch == "aarch64" else 0), A.RET_ALLOW)  # read
                self.assertEqual(lauf(444), A.RET_ALLOW)  # landlock_create_ruleset bleibt erlaubt
                self.assertEqual(self.ausfuehren(prog, 63, 0x1234), A.RET_KILL_PROCESS)  # fremde Architektur
        x86 = A.seccomp_bpf("x86_64")
        self.assertEqual(self.ausfuehren(x86, 0x40000001, A.AUDIT_ARCH["x86_64"]), A.RET_KILL_PROCESS)  # x32

    def test_unknown_architecture(self):
        with self.assertRaises(OSError):
            A.seccomp_bpf("riscv64")

    def test_landlock_bind_nur_entwicklungsports(self):
        z = A.port_zugriff({22, 55322, 20500})
        self.assertEqual(z[443], A.LANDLOCK_ACCESS_NET_CONNECT_TCP)  # connect offen
        self.assertNotIn(22, z)  # Host-Port: kein Zugriff (connect gesperrt, bind nicht erlaubt)
        self.assertNotIn(55322, z)
        self.assertEqual(z[20000], A.LANDLOCK_ACCESS_NET_BIND_TCP | A.LANDLOCK_ACCESS_NET_CONNECT_TCP)
        self.assertEqual(z[20999], A.LANDLOCK_ACCESS_NET_BIND_TCP | A.LANDLOCK_ACCESS_NET_CONNECT_TCP)
        self.assertEqual(z[20500], A.LANDLOCK_ACCESS_NET_BIND_TCP)  # für connect gesperrt, bind erlaubt
        self.assertNotIn(80, [p for p, a in z.items() if a & A.LANDLOCK_ACCESS_NET_BIND_TCP])  # bind 80 gesperrt
        self.assertNotIn(21000, [p for p, a in z.items() if a & A.LANDLOCK_ACCESS_NET_BIND_TCP])


class ConsistencyTests(unittest.TestCase):
    """Dispatcher, Läufer, Ausgangsfilter, Managed-Settings und Voice-Core müssen zueinander passen."""

    def test_managed_settings_match_guard_and_runner(self):
        settings = json.loads((INFRA / "claude-code" / "managed-settings.json").read_text(encoding="utf-8"))
        self.assertIs(settings["disableAllHooks"], False)
        self.assertIs(settings["allowManagedHooksOnly"], True)  # nur verwaltete Hooks ...
        self.assertEqual(settings["enabledPlugins"], {"ecc@ecc": True})  # ... ECC als verwaltetes Plugin bleibt
        self.assertIs(settings["enableAllProjectMcpServers"], False)
        # M1: gepflanzte Agenten und MCP-Server aus dem Projekt greifen nicht; ECC-Skills/-Befehle bleiben nutzbar
        self.assertEqual(settings["strictPluginOnlyCustomization"], ["agents", "mcp"])
        self.assertNotIn("disableSkillShellExecution", settings)  # würde ECC-Skills die Shell nehmen
        self.assertIn("ecc", settings["extraKnownMarketplaces"])
        self.assertEqual(settings["permissions"]["deny"], dev_agent.DENY_RULES)
        vorher = settings["hooks"]["PreToolUse"][0]
        self.assertEqual(vorher["matcher"], "*")
        cmd = vorher["hooks"][0]["command"]
        self.assertTrue(cmd.endswith("|| exit 2"))
        self.assertIn("/opt/wamo/waechter/dev_guard.py", cmd)
        self.assertIn("--freigabe-socket /run/wamo/zustand/freigabe.sock", cmd)  # M5
        for event in ("PostToolUse", "PostToolUseFailure"):
            nach = settings["hooks"][event][0]["hooks"][0]["command"]
            self.assertIn("--nachher", nach)
            self.assertIn("--freigabe-socket /run/wamo/zustand/freigabe.sock", nach)
        # Punkt 2: der Marktplatz ecc ist die root-eigene Kopie ohne Hooks (wamo-job-ecc.py)
        self.assertEqual(settings["extraKnownMarketplaces"]["ecc"]["source"],
                         {"source": "directory", "path": "/srv/wamo/.claude/plugins/cache/ecc/ecc/ohne-hooks"})
        self.assertEqual(D.SETTINGS_KEYS, ("enabledPlugins",))
        mcp = json.loads((INFRA / "claude-code" / "managed-mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(set(mcp), {"rea", "github", "supabase", "jira"})  # enterprise MCP, ersetzt Benutzer-Einträge
        self.assertEqual(mcp["github"]["headers"]["Authorization"], "Bearer ${GH_TOKEN:-}")
        self.assertEqual(mcp["jira"]["env"]["JIRA_API_TOKEN"], "${JIRA_API_KEY:-}")
        self.assertNotIn("npx", [mcp[s].get("command") for s in mcp])  # feste Pfade, kein Download je Auftrag
        runner = (INFRA / "wamo-job.sh").read_text(encoding="utf-8")
        for mount in ("/run/wamo/zustand", "/run/wamo/projekt", '"$APP/claude-code" /etc/claude-code',
                      '"$APP/waechter" "$WAECHTER_INNEN"', "WAECHTER_INNEN=/opt/wamo/waechter", "--remount-ro /",
                      '--remount-ro "$PROJEKTE"', "--seccomp 9", '9<"$SECCOMP"', "--tmpfs /usr/local/lib/wamo",
                      "APP=/opt/wamo", 'PLUGINS=$APP/claude-plugins', '--ro-bind "$PLUGINS" "$HEIM/.claude/plugins"',
                      '--bind "$JOBDIR/plugin-daten" "$HEIM/.claude/plugins/data"'):
            self.assertIn(mount, runner)
        # Wächter in /opt/wamo/waechter: REPO_ROOT ist dort /opt/wamo (die Installation), nicht der Projekte-Ordner
        self.assertEqual(Path("/opt/wamo/waechter/dev_guard.py").parent.parent.as_posix(), "/opt/wamo")

    def test_runner_env_allowlist_covers_all_tokens(self):
        runner = (INFRA / "wamo-job.sh").read_text(encoding="utf-8")
        erlaubt = re.search(r"ERLAUBT_RE='\^\(([^)]*)\)\$'", runner).group(1).split("|")
        for name in [*wk.CLAUDE_VARIABLEN, *(wk.sandbox_name(n) for v in wk.ZUGANG_VARIABLEN.values() for n in v),
                     "ANTHROPIC_CUSTOM_HEADERS"]:
            self.assertIn(name, erlaubt)
        for name in ("SONIOX_API_KEY", "LITELLM_API_KEY", "GH_TOKEN_LESEN"):
            self.assertNotIn(name, erlaubt)

    def test_egress_rules(self):
        nft = (INFRA / "wamo-egress.nft").read_text(encoding="utf-8")
        for regel in ("table inet wamo_egress", "type filter hook output priority 0; policy accept;",
                      "meta skuid @WAMO_UID@ counter jump wamo", "ct state established,related accept",
                      "ip daddr 127.0.0.53 meta l4proto { tcp, udp } th dport 53 accept",
                      "ip daddr 127.0.0.1 tcp dport 20000-20999 accept", "meta nfproto ipv6 counter jump abweisen",
                      "ip daddr @intern4 counter jump abweisen",
                      "fib daddr type { local, broadcast, multicast, anycast } counter jump abweisen",
                      "tcp dport { 80, 443 } accept", "reject with icmpx type admin-prohibited"):
            self.assertIn(regel, nft)
        elemente = re.search(r"elements = \{([^}]*)\}", nft).group(1)
        for netz in ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
                     "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/3"):
            self.assertIn(netz, elemente)
        self.assertEqual((D.ENTWICKLUNGS_PORTS.start, D.ENTWICKLUNGS_PORTS.stop), (20000, 21000))
        self.assertIn("zwischen 20000 und 20999", dev_agent.REMOTE_RULES)
        unit = (INFRA / "wamo-egress.service").read_text(encoding="utf-8")
        for zeile in ("DefaultDependencies=no", "Before=network-pre.target", "Conflicts=nftables.service",
                      "ExecStartPre=/usr/sbin/nft -c -f /etc/wamo/egress.nft", "ExecStop=/usr/sbin/nft delete table inet wamo_egress"):
            self.assertIn(zeile, unit)

    def test_guard_blocks_every_token_name(self):
        for name in [*wk.CLAUDE_VARIABLEN, "GH_TOKEN", "GH_TOKEN_LESEN", "VERCEL_TOKEN", "SUPABASE_ACCESS_TOKEN",
                     "JIRA_API_KEY", "JIRA_API_TOKEN", "ANTHROPIC_CUSTOM_HEADERS"]:
            with self.subTest(name=name):
                self.assertEqual(dev_guard.decide("Bash", {"command": f"echo ${name}"}, None)[0], "block")

    def test_exit_codes_match_voice_core(self):
        self.assertIn("abgewiesen", dev_agent.REMOTE_EXIT_STATUS[D.EXIT_ABGEWIESEN])
        self.assertIn("Selbsttest", dev_agent.REMOTE_EXIT_STATUS[D.EXIT_SELBSTTEST])
        self.assertIn("belegt", dev_agent.REMOTE_EXIT_STATUS[D.EXIT_BELEGT])
        self.assertIn("Claude-Zugang", dev_agent.REMOTE_EXIT_STATUS[D.EXIT_KEIN_ZUGANG])
        self.assertIn("Zugänge", dev_agent.REMOTE_EXIT_STATUS[D.EXIT_ZUGANG_ERWEITERT])
        self.assertIn("Zeitlimit", dev_agent.REMOTE_EXIT_STATUS[D.EXIT_ZEITLIMIT])
        self.assertEqual(dev_agent.REMOTE_EXIT_STATUS[D.EXIT_ABGEBROCHEN], "abgebrochen")
        self.assertEqual(dev_agent.PROJECT_NAME.pattern.strip("^$"), D.PROJECT_RE.pattern)  # gleiche Projektnamen
        self.assertEqual(dev_agent.SELFTEST_EVENT, D.SELFTEST_EVENT)
        self.assertEqual(dev_agent.ZUGANG_TOOL, D.ZUGANG_TOOL)
        self.assertEqual(dev_agent.MAX_OFFENE_FREIGABEN, D.MAX_OFFENE_FREIGABEN)
        self.assertIn("freigabe_mehrfach", dev_agent.REMOTE_ENDE_STATUS)

    def test_selftest_never_uses_mv(self):
        """mv kopiert bei EXDEV und löscht die Quelle: im Projektordner wäre danach der Inhalt weg."""
        import inspect
        code = "\n".join(z for z in inspect.getsource(D.selbsttest).splitlines() if not z.strip().startswith("#"))
        self.assertNotRegex(code, r"(?<![\w-])mv\s")
        self.assertIn("os.rename", inspect.getsource(D.selbsttest))

    def test_selftest_expects_exact_guard_reason(self):
        grund = dev_guard.decide("Bash", D.SELFTEST_EVENT["tool_input"], None)[1]
        self.assertTrue(f"GESPERRT: {grund}".startswith(D.SELFTEST_GRUND))

    def test_every_art_has_known_services(self):
        for art, karte in wk.KARTEN.items():
            with self.subTest(art=art):
                self.assertLessEqual(set(karte.zugaenge), set(wk.ZUGANG_VARIABLEN))
        self.assertEqual(wk.zugangs_variablen("recherche"), ())
        self.assertIn("GH_TOKEN", wk.zugangs_variablen("umsetzung"))
        self.assertIn("GH_TOKEN_LESEN", wk.zugangs_variablen("review"))


class AbschottungTests(unittest.TestCase):
    def test_ports(self):
        self.assertEqual(A.parse_ports("22, 5432,,11434"), {22, 5432, 11434})
        self.assertEqual(A.parse_ports(""), set())
        for bad in ("0", "70000", "x", "-1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                A.parse_ports(bad)
        erlaubt = A.erlaubte_ports({22, 55322})
        self.assertNotIn(22, erlaubt)
        self.assertNotIn(55322, erlaubt)
        self.assertIn(443, erlaubt)
        self.assertEqual(len(erlaubt), 65535 - 2)

    def test_args(self):
        self.assertEqual(A.parse_args(["--sperrports", "1,2", "--", "claude", "-p"]), ({1, 2}, ["claude", "-p"]))
        for bad in (["--", "x"], ["--sperrports", "1"], ["--sperrports", "1", "--"], ["--egal", "1", "--", "x"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                A.parse_args(bad)

    def test_fail_closed_without_landlock(self):
        with mock.patch.object(A, "abschotten", side_effect=OSError("kein Landlock")), \
             mock.patch.object(A.os, "execvp") as execvp, mock.patch("sys.stderr"):
            self.assertEqual(A.main(["--sperrports", "22", "--", "claude"]), A.EXIT_FEHLER)
        execvp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
