"""Tests für den Auftragsdispatcher auf Sokrates-2 (infra/sokrates2/wamo_job_dispatch.py), die Abschottung
(abschottung.py: Landlock-Ports, seccomp-Filter) und die Konsistenz zwischen Werkzeugkarte, Läufer
(wamo-job.sh), Ausgangsfilter (wamo-egress.nft) und managed-settings.json. Läuft unter Windows und Linux;
Teile, die Linux brauchen (fcntl, dir_fd, FIFO, setitimer), werden unter Windows übersprungen. Der echte
Sandbox-Test ist wamo-job-rauchtest auf Sokrates-2.

Ausführen:  uv run python -m pytest -q test_sokrates2_dispatch.py   (im Ordner voice-core/)
"""

import contextlib
import dataclasses
import importlib.util
import io
import json
import os
import re
import struct
import sys
import tempfile
import time
import unittest
import uuid
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
    sys.modules[name] = modul  # nötig für @dataclass (löst Typen über sys.modules auf)
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
        auftrag, system, modell, repo, zugang, profil = D.parse_header(
            json.dumps({"auftrag": "Baue\u0007 eine App\nmit Login", "system": "x"}).encode() + b"\n")
        self.assertEqual(auftrag, "Baue eine App\nmit Login")
        self.assertEqual(system, "x")
        self.assertIsNone(modell)
        self.assertIsNone(repo)
        self.assertIsNone(zugang)  # älterer Kern: Zugang aus geheim.env wie bisher
        self.assertIsNone(profil)
        self.assertEqual(D.parse_header(b'{"auftrag": "x", "modell": "claude-opus-5-5"}\n')[2], "claude-opus-5-5")
        self.assertEqual(D.parse_header(b'{"auftrag": "x", "repo": "wamocon/rezepte-app"}\n')[3], "wamocon/rezepte-app")
        for raw in [b'{"auftrag": "x"}', b'kein json\n', b'[]\n', b'{"auftrag": "x", "befehl": "id"}\n',
                    b'{"auftrag": 1}\n', b'{"auftrag": "   "}\n', b"[" * 5000 + b"\n",
                    b'{"auftrag": "x", "modell": 5}\n', b'{"auftrag": "x", "modell": "--dangerously"}\n',
                    b'{"auftrag": "x", "modell": "a b"}\n', b'{"auftrag": "x", "repo": 5}\n',
                    json.dumps({"auftrag": "x" * (D.MAX_TEXT_CHARS + 1)}).encode() + b"\n"]:
            with self.subTest(raw=raw[:40]), self.assertRaises(D.Abgewiesen):
                D.parse_header(raw)

    def test_repo_strictly_checked(self):
        for gut in ("wamocon/rezepte-app", "a/b", "Owner-1/repo.name_2", "x" * 39 + "/" + "y" * 100):
            with self.subTest(gut=gut):
                self.assertEqual(D.check_repo(gut), gut)
        for schlecht in ("", "wamocon", "a/b/c", "/b", "a/", "-a/b", "a_b/c", "a.b/c", "a/b c", "a/b\n", "a/../b",
                         "a/..", "a/.", "a/b.git", "a/B.GIT", "../b", "a\\b", "a/b;id", "a/b$", "ä/b", "a/bä",
                         "x" * 40 + "/b", "a/" + "y" * 101, "a//b", " a/b", "a/b ", None, 5, ["a/b"],
                         "wamocon/wamo", "Wamocon/WAMO", "x/Wamo"):  # das WAMO-Repo selbst bleibt gesperrt
            with self.subTest(schlecht=schlecht), self.assertRaises(D.Abgewiesen):
                D.check_repo(schlecht)
        self.assertEqual(D.check_repo("wamocon/wamo-extra"), "wamocon/wamo-extra")  # nur der genaue Name ist gesperrt


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


def _rsa_pem(bits: int = 2048):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    schluessel = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    pem = schluessel.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption())
    return schluessel, pem


class FakeGithub:
    """Attrappe der GitHub-API anstelle von D._https: prüft das JWT gegen den öffentlichen Schlüssel, stellt ein
    Token mit den angefragten Rechten aus (Owner wamocon) und nimmt den Widerruf an. Keine echten Aufrufe."""

    def __init__(self, public_key, token="ghs_" + "T0k3n" * 7 + "x", status=201, antwort=None, widerruf=204,
                 netzfehler=False, waehrend_post=None):
        self.public_key, self.token, self.status, self.antwort = public_key, token, status, antwort
        self.widerruf, self.netzfehler, self.waehrend_post = widerruf, netzfehler, waehrend_post
        self.repos = ["wamocon/app", "wamocon/rezepte-app"]  # was /installation/repositories ohne Ziel-Repo zeigt
        self.repo_status = 200
        self.anfragen: list[tuple] = []
        self.claims: list[dict] = []

    def __call__(self, methode, pfad, kopf, koerper=None):
        import jwt as pyjwt
        koerper_obj = json.loads(koerper) if koerper else None
        self.anfragen.append((methode, pfad, dict(kopf), koerper_obj))
        if self.netzfehler:
            raise TimeoutError("timed out")
        anmeldung = kopf["Authorization"].split(" ", 1)[1]
        if methode == "POST":
            self.claims.append(pyjwt.decode(anmeldung, self.public_key, algorithms=["RS256"],
                                            options={"verify_exp": False, "verify_iat": False}))
            if self.waehrend_post:
                self.waehrend_post()
            body = self.antwort if self.antwort is not None else {
                "token": self.token, "expires_at": "2026-10-21T09:00:00Z",
                "permissions": koerper_obj["permissions"], "repository_selection": "selected",
                "repositories": [{"full_name": f"wamocon/{n}"} for n in koerper_obj.get("repositories", [])]}
            return self.status, json.dumps(body).encode()
        if methode == "DELETE":
            return self.widerruf, b""
        if methode == "GET" and pfad.startswith("/installation/repositories"):
            seite = int(pfad.rsplit("page=", 1)[1]) if "page=" in pfad else 1
            teil = self.repos[(seite - 1) * 100:seite * 100]
            return self.repo_status, json.dumps({"total_count": len(self.repos),
                                                 "repositories": [{"full_name": n} for n in teil]}).encode()
        return 404, b"{}"

    def widerrufe(self):
        return [a for a in self.anfragen if a[0] == "DELETE" and a[1] == "/installation/token"]


class GithubAppTests(unittest.TestCase):
    """App-Modus: JWT, Rechte je Art, Repo-Begrenzung, Widerruf, keine Geheimnisse in Log und argv, Rückfall
    ohne Konfiguration und fail-closed bei kaputter Konfiguration. GitHub ist eine Attrappe (FakeGithub)."""

    @classmethod
    def setUpClass(cls):
        cls.schluessel, cls.pem = _rsa_pem()
        cls.public = cls.schluessel.public_key()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        wurzel = Path(self.tmp.name)
        self.ordner = wurzel / "etc-wamo"
        self.ordner.mkdir()
        (self.ordner / "gh-app.pem").write_bytes(self.pem)
        self.jobs, self.projekte, self.heim = wurzel / "jobs", wurzel / "projekte", wurzel / "heim"
        for p in (self.jobs, self.projekte / "rezepte-app", self.heim):
            p.mkdir(parents=True)
        self.geheim = {"ANTHROPIC_API_KEY": "sk-ant-aaaaaaaaaaaa", "GH_TOKEN": "ghp_persoenlich1234567890",
                       "GH_TOKEN_LESEN": "ghp_lesenpersoenlich12345", "VERCEL_TOKEN": "vercel-dddddddd",
                       "WAMO_GH_APP_ID": "123456", "WAMO_GH_APP_INSTALLATION_ID": "987654",
                       "WAMO_GH_APP_SCHLUESSEL": f"{self.ordner.as_posix()}/gh-app.pem"}
        self.fake = FakeGithub(self.public)
        self.logzeilen: list[str] = []
        self.ereignisse: list[dict] = []
        self.patches = [mock.patch.object(D, "SCHLUESSEL_ORDNER", self.ordner),
                        mock.patch.object(D, "_https", side_effect=lambda *a, **k: self.fake(*a, **k)),
                        mock.patch.object(D, "log", side_effect=lambda t, pflicht=False: self.logzeilen.append(t)),
                        mock.patch.object(D, "JOBS", self.jobs), mock.patch.object(D, "PROJEKTE", self.projekte),
                        mock.patch.object(D, "HEIM", self.heim), mock.patch.object(D, "WIDERRUF_PAUSEN_S", (0.0, 0.0)),
                        mock.patch.object(D, "emit", side_effect=lambda ev: self.ereignisse.append(ev) or True)]
        if LINUX:  # echte Rechteprüfung: Besitzer ist hier der Testbenutzer statt root
            os.chmod(self.ordner, 0o750)
            os.chmod(self.ordner / "gh-app.pem", 0o640)
            self.patches.append(mock.patch.object(D, "SCHLUESSEL_BESITZER", (os.getuid(),)))
        else:  # Windows kennt keine POSIX-Rechte; _rechte_grund ist unten eigens getestet
            self.patches.append(mock.patch.object(D, "_rechte_grund", return_value=None))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def app(self):
        return D.github_app_konfig(self.geheim)

    # ------------------------------------------------------------ JWT
    def test_jwt_aufbau_und_pruefung(self):
        import jwt as pyjwt
        jetzt = 1_800_000_000
        token = D.jwt_erzeugen("123456", self.pem, jetzt=jetzt)
        self.assertEqual(pyjwt.get_unverified_header(token), {"alg": "RS256", "typ": "JWT"})
        claims = pyjwt.decode(token, self.public, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
        self.assertEqual(claims, {"iat": jetzt - 60, "exp": jetzt + 540, "iss": "123456"})
        self.assertLessEqual(claims["exp"] - jetzt, 9 * 60)
        self.assertLessEqual(claims["exp"] - claims["iat"], 10 * 60)
        frisch = pyjwt.decode(D.jwt_erzeugen("123456", self.pem), self.public, algorithms=["RS256"])  # jetzt gültig
        self.assertEqual(frisch["iss"], "123456")
        kopf, inhalt, signatur = token.split(".")
        falsch = f"{kopf}.{inhalt[:-2]}AA.{signatur}"
        with self.assertRaises(pyjwt.InvalidSignatureError):
            pyjwt.decode(falsch, self.public, algorithms=["RS256"], options={"verify_exp": False})
        anderer, _ = _rsa_pem()
        with self.assertRaises(pyjwt.InvalidSignatureError):
            pyjwt.decode(token, anderer.public_key(), algorithms=["RS256"], options={"verify_exp": False})
        with self.assertRaises(D.GithubAppFehler):
            D.jwt_erzeugen("12a", self.pem)

    def test_ungeeignete_schluessel_abgewiesen(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        ec_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        klein, _ = _rsa_pem(1024)
        klein_pem = klein.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
        mit_passwort = self.schluessel.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.BestAvailableEncryption(b"geheim"))
        for name, pem in (("ec", ec_pem), ("rsa1024", klein_pem), ("passwort", mit_passwort), ("muell", b"kein pem"),
                          ("leer", b""), ("kaputt", b"-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n")):
            with self.subTest(name=name), self.assertRaises(D.GithubAppFehler) as ctx:
                D.jwt_erzeugen("123456", pem)
            self.assertNotIn("BEGIN", str(ctx.exception))

    # ------------------------------------------------------------ Rechte je Art
    def test_rechte_je_art_aus_der_werkzeugkarte(self):
        self.assertEqual(set(wk.GITHUB_ZUGRIFF), set(wk.AUFTRAGSARTEN))
        lesen = {"contents": "read", "metadata": "read", "pull_requests": "read"}
        schreiben = {"contents": "write", "pull_requests": "write", "metadata": "read"}
        for art, karte in wk.KARTEN.items():
            erwartet = schreiben if "github" in karte.zugaenge else lesen if "github_lesen" in karte.zugaenge else None
            with self.subTest(art=art):
                self.assertEqual(wk.github_app_rechte(art), erwartet)
                if erwartet is None:
                    continue
                self.assertFalse(set(erwartet) & wk.GITHUB_APP_RECHTE_VERBOTEN)
                self.fake.anfragen.clear()
                token, ablauf = D.installations_token(self.app(), art, None)
                methode, pfad, kopf, koerper = self.fake.anfragen[0]
                self.assertEqual((methode, pfad), ("POST", "/app/installations/987654/access_tokens"))
                self.assertEqual(koerper, {"permissions": erwartet})  # ohne Repo: ganze Installation, Rechte der Art
                self.assertEqual(kopf["X-GitHub-Api-Version"], "2022-11-28")
                self.assertEqual(token, self.fake.token)
                self.assertEqual(ablauf, "2026-10-21T09:00:00Z")
        for verboten in ("administration", "workflows", "secrets", "actions", "members"):
            self.assertIn(verboten, wk.GITHUB_APP_RECHTE_VERBOTEN)
        self.assertEqual(wk.GITHUB_ZUGRIFF["review"], "lesen")
        self.assertEqual(wk.GITHUB_ZUGRIFF["umsetzung"], "schreiben")
        self.assertIsNone(wk.github_app_rechte("recherche"))

    def test_tabelle_gegen_karte_fail_closed(self):
        with mock.patch.dict(wk.GITHUB_ZUGRIFF, {"review": "schreiben"}):
            with self.assertRaises(ValueError):
                wk.github_app_rechte("review")
            with self.assertRaises(D.GithubAppFehler):
                D.installations_token(self.app(), "review", None)
        with mock.patch.dict(wk.GITHUB_APP_RECHTE, {"lesen": {**wk.GITHUB_APP_RECHTE["lesen"], "administration": "read"}}):
            with self.assertRaises(ValueError):
                wk.github_app_rechte("review")
        # die Stufe lesen bleibt lesend, auch wenn der Bereich erlaubt ist; schreiben nie über die Obergrenze
        for stufe, art, aenderung in (("lesen", "review", {"contents": "write"}),
                                      ("lesen", "review", {"pull_requests": "write"}),
                                      ("schreiben", "umsetzung", {"metadata": "write"})):
            with self.subTest(stufe=stufe, aenderung=aenderung), \
                 mock.patch.dict(wk.GITHUB_APP_RECHTE, {stufe: {**wk.GITHUB_APP_RECHTE[stufe], **aenderung}}):
                with self.assertRaises(ValueError):
                    wk.github_app_rechte(art)
                with self.assertRaises(D.GithubAppFehler):
                    D.installations_token(self.app(), art, None)
        self.assertEqual(self.fake.anfragen, [])

    # ------------------------------------------------------------ Repo-Begrenzung
    def test_repo_begrenzung(self):
        D.installations_token(self.app(), "umsetzung", "wamocon/rezepte-app")
        self.assertEqual(self.fake.anfragen[0][3]["repositories"], ["rezepte-app"])
        self.assertEqual(self.fake.widerrufe(), [])
        # Repo eines anderen Owners: GitHub stellt ein Token für wamocon/<name> aus, das passt nicht -> widerrufen
        self.fake.anfragen.clear()
        with self.assertRaises(D.GithubAppFehler) as ctx:
            D.installations_token(self.app(), "umsetzung", "fremd/rezepte-app")
        self.assertIn("fremd/rezepte-app", str(ctx.exception))
        self.assertEqual(len(self.fake.widerrufe()), 1)
        self.assertEqual(self.fake.widerrufe()[0][2]["Authorization"], f"Bearer {self.fake.token}")

    def test_mehr_rechte_als_angefragt_wird_widerrufen(self):
        self.fake.antwort = {"token": self.fake.token, "expires_at": "x",
                             "permissions": {"contents": "write", "administration": "write", "metadata": "read"}}
        with self.assertRaises(D.GithubAppFehler) as ctx:
            D.installations_token(self.app(), "umsetzung", None)
        self.assertIn("mehr Rechte", str(ctx.exception))
        self.assertEqual(len(self.fake.widerrufe()), 1)
        self.fake.anfragen.clear()
        self.fake.antwort = {"token": self.fake.token, "permissions": {"contents": "write", "metadata": "read"}}
        with self.assertRaises(D.GithubAppFehler):  # lesende Art bekommt Schreibrecht
            D.installations_token(self.app(), "review", None)
        self.assertEqual(len(self.fake.widerrufe()), 1)

    # ------------------------------------------------------------ Widerruf
    def test_widerruf_bei_ende_abbruch_und_fehler(self):
        with D.github_token_fuer_lauf(JOB, "umsetzung", "wamocon/rezepte-app", self.app()) as token:
            self.assertEqual(token, self.fake.token)
            self.assertEqual(self.fake.widerrufe(), [])
        self.assertEqual(len(self.fake.widerrufe()), 1)
        for ausnahme in (RuntimeError("kaputt"), D._Beendet(15), KeyboardInterrupt()):
            self.fake.anfragen.clear()
            with self.subTest(ausnahme=type(ausnahme).__name__), self.assertRaises(type(ausnahme)):
                with D.github_token_fuer_lauf(JOB, "review", None, self.app()):
                    raise ausnahme
            self.assertEqual(len(self.fake.widerrufe()), 1)
        self.assertTrue(any(z.startswith(f"gh_app_widerruf {JOB} ok") for z in self.logzeilen))

    def test_widerruf_fehlgeschlagen_wird_protokolliert(self):
        self.fake.widerruf = 500
        with D.github_token_fuer_lauf(JOB, "umsetzung", None, self.app()):
            pass
        self.assertEqual(len(self.fake.widerrufe()), 3)  # drei Versuche bei Serverfehlern, hier je eine Antwort 500
        self.assertTrue(any("FEHLGESCHLAGEN" in z for z in self.logzeilen))
        # der Voice-Core erfährt es (Ereignis ohne Token), damit es nicht nur im Log steht
        self.assertEqual([e["ereignis"] for e in self.ereignisse], ["gh_widerruf_fehlgeschlagen"])
        self.geheimnisfrei(json.dumps(self.ereignisse))

    def test_widerruf_wiederholt_bei_ratenbegrenzung_mit_pausen(self):
        pausen = []
        with mock.patch.object(D, "WIDERRUF_PAUSEN_S", (1.0, 3.0)), \
             mock.patch.object(D.time, "sleep", side_effect=pausen.append):
            for status in (403, 429):  # Ratenbegrenzung: drei Versuche mit Pausen
                self.fake.anfragen.clear()
                pausen.clear()
                self.fake.widerruf = status
                self.assertFalse(D.token_widerrufen(self.fake.token))
                self.assertEqual((len(self.fake.widerrufe()), pausen), (3, [1.0, 3.0]))
            self.fake.anfragen.clear()
            self.fake.widerruf = 404  # alles andere unter 500: aufgeben, keine Wiederholung
            self.assertFalse(D.token_widerrufen(self.fake.token))
            self.assertEqual(len(self.fake.widerrufe()), 1)
            self.fake.anfragen.clear()
            self.fake.widerruf = 401  # schon ungültig: erledigt
            self.assertTrue(D.token_widerrufen(self.fake.token))
            self.assertEqual(len(self.fake.widerrufe()), 1)

    def test_ohne_repo_nur_ausgewaehlte_repos_und_nie_das_wamo_repo(self):
        rechte = wk.GITHUB_APP_RECHTE["schreiben"]
        faelle = {
            "Auswahl alle": {"token": self.fake.token, "permissions": rechte, "repository_selection": "all"},
            "Auswahl fehlt": {"token": self.fake.token, "permissions": rechte},
            "WAMO-Repo dabei": {"token": self.fake.token, "permissions": rechte, "repository_selection": "selected",
                                "repositories": [{"full_name": "wamocon/app"}, {"full_name": "Wamocon/WAMO"}]},
        }
        for name, antwort in faelle.items():
            self.fake.anfragen.clear()
            self.fake.antwort = antwort
            with self.subTest(fall=name), self.assertRaises(D.GithubAppFehler):
                D.installations_token(self.app(), "umsetzung", None)
            self.assertEqual(len(self.fake.widerrufe()), 1, name)  # sofort widerrufen
        self.fake.anfragen.clear()
        self.fake.antwort = {"token": self.fake.token, "permissions": rechte, "repository_selection": "selected",
                             "repositories": [{"full_name": "wamocon/app"}, {"full_name": "wamocon/wamo-docs"}]}
        self.assertEqual(D.installations_token(self.app(), "umsetzung", None)[0], self.fake.token)  # wamo-docs ist ok
        self.assertEqual(self.fake.widerrufe(), [])

    def test_ohne_repo_prueft_die_liste_der_installation(self):
        """Die echte Antwort zählt die Repos nicht auf: erst /installation/repositories zeigt das WAMO-Repo."""
        faelle = {"WAMO-Repo in der Liste": dict(repos=["wamocon/app", "wamocon/WAMO"]),
                  "Liste nicht lesbar": dict(repo_status=500),
                  "mehr als 500 Repos": dict(repos=[f"wamocon/r{i}" for i in range(501)])}
        for name, einstellung in faelle.items():
            self.fake.anfragen.clear()
            for k, v in einstellung.items():
                setattr(self.fake, k, v)
            with self.subTest(fall=name), self.assertRaises(D.GithubAppFehler):
                D.installations_token(self.app(), "umsetzung", None)
            self.assertEqual(len(self.fake.widerrufe()), 1, name)  # Token sofort widerrufen
            self.fake.repos, self.fake.repo_status = ["wamocon/app"], 200
        self.fake.anfragen.clear()
        self.fake.repos = [f"wamocon/r{i}" for i in range(250)]  # mehrere Seiten, kein WAMO-Repo: in Ordnung
        D.installations_token(self.app(), "review", None)
        self.assertEqual([a[1] for a in self.fake.anfragen if a[0] == "GET"],
                         [f"/installation/repositories?per_page=100&page={i}" for i in (1, 2, 3)])
        self.assertEqual(self.fake.widerrufe(), [])
        self.fake.anfragen.clear()
        D.installations_token(self.app(), "umsetzung", "wamocon/rezepte-app")  # mit Ziel-Repo keine Liste nötig
        self.assertEqual([a for a in self.fake.anfragen if a[0] == "GET"], [])

    def test_echte_token_sind_lang_und_haben_punkte(self):
        lang = "ghs_" + "Ab1_" * 40 + "." + "Cd-2" * 50  # Form der echten Token: rund 390 Zeichen mit Punkten
        self.fake.token = lang
        self.assertEqual(D.installations_token(self.app(), "umsetzung", None)[0], lang)
        code, aufrufe = self.start(art="review", repo=None)
        self.assertEqual((code, aufrufe[0][1]["GH_TOKEN"]), (D.EXIT_OK, lang))
        self.assertEqual(self.fake.widerrufe()[-1][2]["Authorization"], f"Bearer {lang}")
        for schlecht in ("ghp_" + "a" * 30, "ghs_" + "a" * 5, "ghs_a b" + "c" * 20, "ghs_" + "a" * 1001, "ghs_x\n" + "a" * 20):
            self.assertIsNone(D.TOKEN_RE.fullmatch(schlecht), schlecht)

    def test_abgelehntes_token_ohne_widerruf_wird_gemeldet(self):
        self.fake.antwort = {"token": self.fake.token, "permissions": {"contents": "write", "administration": "write"}}
        self.fake.widerruf = 500
        with self.assertRaises(D.GithubAppFehler):
            D.installations_token(self.app(), "umsetzung", None)
        self.assertTrue(any("FEHLGESCHLAGEN" in z for z in self.logzeilen))
        self.assertEqual([e["ereignis"] for e in self.ereignisse], ["gh_widerruf_fehlgeschlagen"])
        self.geheimnisfrei(json.dumps(self.ereignisse) + "\n".join(self.logzeilen))

    def test_unerwartete_antwort_widerruft_das_token(self):
        faelle = {
            "Rechte als Liste": {"token": self.fake.token, "permissions": {"contents": ["write"], "metadata": "read"}},
            "Token mit Bindestrich": {"token": "ghs-" + "a" * 30, "permissions": {"contents": "write"}},
            "Rechte fehlen": {"token": self.fake.token},
        }
        for name, antwort in faelle.items():
            self.fake.anfragen.clear()
            self.fake.antwort = antwort
            with self.subTest(fall=name), self.assertRaises(D.GithubAppFehler):
                D.installations_token(self.app(), "umsetzung", None)
            widerrufe = self.fake.widerrufe()
            self.assertEqual(len(widerrufe), 1, name)
            self.assertEqual(widerrufe[0][2]["Authorization"], f"Bearer {antwort['token']}")
        self.fake.anfragen.clear()
        self.fake.antwort = {"token": "ghs_\nX-Evil: 1" + "a" * 20, "permissions": {}}  # nicht header-sicher: kein Widerruf
        with self.assertRaises(D.GithubAppFehler):
            D.installations_token(self.app(), "umsetzung", None)
        self.assertEqual(self.fake.widerrufe(), [])

    def test_signal_handler_ignoriert_weitere_signale(self):
        import signal as sig
        alt = sig.getsignal(sig.SIGTERM)
        try:
            with self.assertRaises(D._Beendet):
                D._als_ausnahme(sig.SIGTERM, None)
            self.assertEqual(sig.getsignal(sig.SIGTERM), sig.SIG_IGN)  # ein zweites SIGTERM stört das Aufräumen nicht
        finally:
            sig.signal(sig.SIGTERM, alt)

    def test_signal_im_widerruf_ueberspringt_ihn_nicht(self):
        echt = D.token_widerrufen
        aufrufe = []

        def mit_signal(token):
            aufrufe.append(token)
            if len(aufrufe) == 1:
                raise D._Beendet(15)  # das erste Signal trifft genau den ersten Widerruf
            return echt(token)
        with mock.patch.object(D, "token_widerrufen", side_effect=mit_signal):
            with D.github_token_fuer_lauf(JOB, "umsetzung", None, self.app()):
                pass
        self.assertEqual(len(aufrufe), 2)
        self.assertEqual(len(self.fake.widerrufe()), 1)

    def test_ohne_app_oder_ohne_github_kein_token(self):
        with D.github_token_fuer_lauf(JOB, "recherche", None, self.app()) as token:
            self.assertIsNone(token)
        with D.github_token_fuer_lauf(JOB, "umsetzung", None, None) as token:
            self.assertIsNone(token)
        self.assertEqual(self.fake.anfragen, [])

    @unittest.skipUnless(hasattr(__import__("signal"), "pthread_sigmask"), "Signalmaske nur unter Linux")
    def test_sigterm_waehrend_der_erzeugung_widerruft(self):
        import signal as sig
        self.fake.waehrend_post = lambda: os.kill(os.getpid(), sig.SIGTERM)  # kommt mitten in der Token-Erzeugung
        with D._signale(D._als_ausnahme), self.assertRaises(D._Beendet):
            with D.github_token_fuer_lauf(JOB, "umsetzung", None, self.app()):
                for _ in range(100):  # das aufgeschobene Signal kommt spätestens hier an (echte Zeit statt leerer Durchläufe: auf einem kalten Rechner reichten 100 nicht)
                    time.sleep(0.01)
        self.assertEqual(len(self.fake.widerrufe()), 1)

    # ------------------------------------------------------------ Echter Einstiegspunkt cmd_start
    class _Sperre:
        def __init__(self, pfad):
            self.datei = None

        def versuchen(self):
            return True

    def start(self, art="umsetzung", repo="wamocon/rezepte-app", ausfuehren=None, geheim=None):
        """cmd_start mit Attrappen für Sperren, Selbsttest und Sandbox-Lauf; alles andere echt."""
        aufrufe: list[tuple] = []

        def fake_ausfuehren(argv, env, auftrag, jobdir, job, projekt, art_):
            aufrufe.append((list(argv), dict(env)))
            self.assertEqual(self.fake.widerrufe(), [])  # während des Laufs ist das Token gültig
            return ausfuehren() if ausfuehren else D.EXIT_OK

        kopf = {"auftrag": "Baue etwas", "system": "Regeln"}
        if repo:
            kopf["repo"] = repo
        with mock.patch.object(D, "_kopfzeile_lesen", return_value=json.dumps(kopf).encode() + b"\n"), \
             mock.patch.object(D, "Sperre", self._Sperre), mock.patch.object(D, "aufraeumen"), \
             mock.patch.object(D, "exklusiv", lambda pfad: contextlib.nullcontext()), \
             mock.patch.object(D, "selbsttest", return_value=(True, "ok")), \
             mock.patch.object(D, "lese_geheimnisse", return_value=self.geheim if geheim is None else geheim), \
             mock.patch.object(D, "projekt_entschaerfen", return_value=[]), \
             mock.patch.object(D, "_ausfuehren", side_effect=fake_ausfuehren):
            try:
                code = D.cmd_start(uuid.uuid4().hex[:16], "rezepte-app", art)  # je Start ein neuer Auftrag
            finally:
                (self.jobs / ".projekte" / "rezepte-app.json").unlink(missing_ok=True)
        return code, aufrufe

    def geheimnisfrei(self, text: str) -> None:
        pem_zeilen = [z for z in self.pem.decode().splitlines() if z and not z.startswith("-----")]
        for geheim in (self.fake.token, "ghp_persoenlich1234567890", "ghp_lesenpersoenlich12345", *pem_zeilen[:3],
                       "PRIVATE KEY"):
            self.assertNotIn(geheim, text)

    def test_cmd_start_app_modus_token_in_sandbox_und_widerruf(self):
        code, aufrufe = self.start()
        self.assertEqual(code, D.EXIT_OK)
        argv, env = aufrufe[0]
        self.assertEqual(env["GH_TOKEN"], self.fake.token)  # das App-Token, nicht der persönliche Token
        self.assertNotIn("ghp_persoenlich1234567890", env.values())
        self.assertFalse([k for k in env if k.startswith("WAMO_GH_APP")])
        self.assertEqual(env["VERCEL_TOKEN"], "vercel-dddddddd")  # andere Zugänge der Art unverändert
        self.assertEqual(self.fake.anfragen[0][3], {"permissions": wk.GITHUB_APP_RECHTE["schreiben"],
                                                   "repositories": ["rezepte-app"]})
        self.assertEqual(len(self.fake.widerrufe()), 1)
        self.assertIn("nur für das Repo wamocon/rezepte-app", argv[argv.index("--append-system-prompt") + 1])
        # weder Schlüssel noch Token in argv, Audit oder Ereignissen; im Audit steht der Repo-Name
        self.geheimnisfrei(" ".join(argv))
        self.geheimnisfrei("\n".join(self.logzeilen))
        self.geheimnisfrei(json.dumps(self.ereignisse))
        self.assertTrue(any(re.fullmatch(r"gh_app_token [0-9a-f]{16} repo=wamocon/rezepte-app stufe=schreiben "
                                         r"ablauf=2026-10-21T09:00:00Z", z) for z in self.logzeilen), self.logzeilen)
        self.assertTrue(any("github=app repo=wamocon/rezepte-app" in z for z in self.logzeilen))
        self.assertTrue(any(re.fullmatch(r"gh_app_widerruf [0-9a-f]{16} ok", z) for z in self.logzeilen))

    def test_cmd_start_widerruf_bei_cancel_und_fehler(self):
        code, _ = self.start(ausfuehren=lambda: D.EXIT_ABGEBROCHEN)  # cancel oder Lebenslinie: Lauf endet so
        self.assertEqual(code, D.EXIT_ABGEBROCHEN)
        self.assertEqual(len(self.fake.widerrufe()), 1)
        self.fake.anfragen.clear()

        def kaputt():
            raise RuntimeError("Fehler im Lauf")
        with self.assertRaises(RuntimeError):
            self.start(ausfuehren=kaputt)
        self.assertEqual(len(self.fake.widerrufe()), 1)
        self.fake.anfragen.clear()

        def signal_():
            raise D._Beendet(15)
        with self.assertRaises(D._Beendet):
            self.start(ausfuehren=signal_)
        self.assertEqual(len(self.fake.widerrufe()), 1)

    def test_main_meldet_signal_als_abbruch(self):
        def beendet(*_):
            raise D._Beendet(15)
        with mock.patch.dict(D.HANDLER, {"start": beendet}), mock.patch.dict(os.environ):
            os.environ.pop("SSH_CONNECTION", None)
            os.environ.pop("SSH_ORIGINAL_COMMAND", None)
            self.assertEqual(D.main(["start", JOB, "rezepte-app", "umsetzung"]), D.EXIT_ABGEBROCHEN)

    def test_lesende_art_bekommt_lesetoken(self):
        code, aufrufe = self.start(art="review", repo=None)
        self.assertEqual(code, D.EXIT_OK)
        self.assertEqual(aufrufe[0][1]["GH_TOKEN"], self.fake.token)
        self.assertEqual(self.fake.anfragen[0][3], {"permissions": wk.GITHUB_APP_RECHTE["lesen"]})
        self.assertIn("für die Repos der Installation", aufrufe[0][0][aufrufe[0][0].index("--append-system-prompt") + 1])

    def test_art_ohne_github_ruft_github_nicht(self):
        code, aufrufe = self.start(art="recherche", repo=None)
        self.assertEqual(code, D.EXIT_OK)
        self.assertNotIn("GH_TOKEN", aufrufe[0][1])
        self.assertEqual(self.fake.anfragen, [])

    def test_rueckfall_ohne_konfiguration(self):
        ohne = {k: v for k, v in self.geheim.items() if not k.startswith("WAMO_GH_APP")}
        self.assertIsNone(D.github_app_konfig(ohne))
        code, aufrufe = self.start(geheim=ohne)
        self.assertEqual(code, D.EXIT_OK)
        self.assertEqual(aufrufe[0][1]["GH_TOKEN"], "ghp_persoenlich1234567890")  # wie bisher
        code, aufrufe = self.start(art="review", geheim=ohne)
        self.assertEqual(aufrufe[0][1]["GH_TOKEN"], "ghp_lesenpersoenlich12345")
        self.assertEqual(self.fake.anfragen, [])  # kein Aufruf bei GitHub
        leer = {**ohne, "WAMO_GH_APP_ID": "", "WAMO_GH_APP_INSTALLATION_ID": "", "WAMO_GH_APP_SCHLUESSEL": ""}
        self.assertIsNone(D.github_app_konfig(leer))
        with mock.patch.object(D, "lese_geheimnisse", return_value=ohne):
            self.assertEqual(D.github_status(), "token")

    def test_fail_closed_bei_kaputter_konfiguration(self):
        pfad = self.geheim["WAMO_GH_APP_SCHLUESSEL"]
        faelle = {
            "nur App-ID": {"WAMO_GH_APP_ID": "123456"},
            "ohne Schlüssel": {"WAMO_GH_APP_ID": "123456", "WAMO_GH_APP_INSTALLATION_ID": "987654"},
            "App-ID keine Zahl": {"WAMO_GH_APP_ID": "Iv1.abc"},
            "Installation keine Zahl": {"WAMO_GH_APP_INSTALLATION_ID": "98 7654"},
            "Pfad relativ": {"WAMO_GH_APP_SCHLUESSEL": "gh-app.pem"},
            "Pfad außerhalb": {"WAMO_GH_APP_SCHLUESSEL": "/srv/wamo/gh-app.pem"},
            "Pfad mit ..": {"WAMO_GH_APP_SCHLUESSEL": f"{self.ordner.as_posix()}/../gh-app.pem"},
            "Pfad Unterordner": {"WAMO_GH_APP_SCHLUESSEL": f"{self.ordner.as_posix()}/sub/gh-app.pem"},
            "Tippfehler im Namen": {"WAMO_GH_APP_SCHLUESEL": f"{self.ordner.as_posix()}/gh-app.pem"},
            "Datei fehlt": {"WAMO_GH_APP_SCHLUESSEL": f"{self.ordner.as_posix()}/fehlt.pem"},
            "kein Schlüssel": {"WAMO_GH_APP_SCHLUESSEL": f"{self.ordner.as_posix()}/kaputt.pem"},
        }
        (self.ordner / "kaputt.pem").write_text("kein Schlüssel", encoding="utf-8")
        if LINUX:
            os.chmod(self.ordner / "kaputt.pem", 0o640)
        for name, aenderung in faelle.items():
            if name in ("nur App-ID", "ohne Schlüssel"):
                geheim = {k: v for k, v in self.geheim.items() if not k.startswith("WAMO_GH_APP")} | aenderung
            else:
                geheim = {**self.geheim, **aenderung}
            self.ereignisse.clear()
            with self.subTest(fall=name):
                code, aufrufe = self.start(geheim=geheim)
                self.assertEqual(code, D.EXIT_GITHUB_APP)
                self.assertEqual(aufrufe, [])  # nichts gestartet, kein Rückfall auf ghp_persoenlich...
                self.assertEqual(self.ereignisse[-1]["ereignis"], "start_abgelehnt")
                self.assertIn("GitHub-App", self.ereignisse[-1]["grund"])
                self.geheimnisfrei(json.dumps(self.ereignisse) + "\n".join(self.logzeilen))
                with mock.patch.object(D, "lese_geheimnisse", return_value=geheim):
                    self.assertTrue(D.github_status().startswith("fehler: "))
        self.assertEqual(self.fake.anfragen, [])
        self.assertEqual(pfad, self.geheim["WAMO_GH_APP_SCHLUESSEL"])

    def test_fail_closed_bei_fehlern_von_github(self):
        faelle = {"401": dict(status=401, antwort={"message": "A JSON web token could not be decoded"}),
                  "404": dict(status=404, antwort={"message": "Not Found"}),
                  "422 Repo": dict(status=422, antwort={"message": "There is at least one repository that does not exist"}),
                  "500": dict(status=500, antwort={}),
                  "Netz": dict(netzfehler=True),
                  "ohne Token": dict(antwort={"expires_at": "x", "permissions": {}}),
                  "Token mit Zeilenumbruch": dict(antwort={"token": "ghs_abc\nX-Evil: 1aaaaaaaaaaaaaaa", "permissions": {}})}
        for name, kw in faelle.items():
            self.fake = FakeGithub(self.public, **kw)
            self.ereignisse.clear()
            with self.subTest(fall=name):
                code, aufrufe = self.start()
                self.assertEqual(code, D.EXIT_GITHUB_APP)
                self.assertEqual(aufrufe, [])
                self.assertEqual(self.ereignisse[-1]["ereignis"], "start_abgelehnt")
                self.geheimnisfrei(json.dumps(self.ereignisse) + "\n".join(self.logzeilen))
        self.assertIn("Installation-ID", D._github_meldung(404, {"message": "Not Found"}))
        self.assertIn("Uhrzeit", D._github_meldung(401, {"message": "x\ny"}))
        self.assertNotIn("\n", D._github_meldung(401, {"message": "x\ny"}))

    def test_tippfehler_in_allen_drei_namen_schaltet_den_app_modus_nicht_still_aus(self):
        falsch = {k: v for k, v in self.geheim.items() if not k.startswith("WAMO_GH_APP")} | {
            "WAMO_GH_APP_IDD": "123456", "WAMO_GH_APP_INSTALATION_ID": "987654",
            "WAMO_GH_APP_SCHLUESEL": f"{self.ordner.as_posix()}/gh-app.pem"}
        with self.assertRaises(D.GithubAppFehler) as ctx:
            D.github_app_konfig(falsch)
        self.assertIn("Tippfehler", str(ctx.exception))
        code, aufrufe = self.start(geheim=falsch)  # kein Rückfall auf ghp_persoenlich...
        self.assertEqual((code, aufrufe), (D.EXIT_GITHUB_APP, []))
        with mock.patch.object(D, "lese_geheimnisse", return_value=falsch):
            self.assertTrue(D.github_status().startswith("fehler: "))

    @unittest.skipUnless(LINUX, "Prozess und AF_UNIX-Socket nur unter Linux")
    def test_signal_beim_start_des_prozesses_stoppt_und_wirft_weiter(self):
        jobdir = self.jobs / JOB
        (jobdir / "zustand").mkdir(parents=True)
        with mock.patch.object(D, "stop_units") as stopp, mock.patch.object(D, "_status"), \
             mock.patch.object(D.subprocess, "Popen", side_effect=D._Beendet(15)):
            with self.assertRaises(D._Beendet):  # kein AttributeError durch ein noch nicht gesetztes proc
                D._ausfuehren(["/bin/true"], {"PATH": "/usr/bin"}, "Auftrag", jobdir, JOB, "rezepte-app", "umsetzung")
        stopp.assert_called_once_with(JOB)

    def test_status_app_bereit(self):
        with mock.patch.object(D, "lese_geheimnisse", return_value=self.geheim):
            self.assertEqual(D.github_status(), "app")
        self.assertEqual(self.fake.anfragen, [])  # ping prüft ohne Netz

    def test_buchfuehrung_im_app_modus(self):
        self.assertEqual(D.zugangs_quellen("umsetzung", self.geheim, app_modus=True),
                         {"ANTHROPIC_API_KEY", "GH_APP_SCHREIBEN@*", "VERCEL_TOKEN"})
        self.assertEqual(D.zugangs_quellen("review", self.geheim, app_modus=True),
                         {"ANTHROPIC_API_KEY", "GH_APP_LESEN@*"})
        self.assertEqual(D.zugangs_quellen("recherche", self.geheim, app_modus=True), {"ANTHROPIC_API_KEY"})
        # der Umfang des Tokens gehört in die Buchführung: ein anderes oder breiteres Repo ist ein neuer Zugang
        self.assertIn("GH_APP_SCHREIBEN@wamocon/rezepte-app",
                      D.zugangs_quellen("umsetzung", self.geheim, app_modus=True, repo="Wamocon/Rezepte-App"))
        projekt = self.projekte / "rezepte-app"
        eng = D.zugangs_quellen("umsetzung", self.geheim, app_modus=True, repo="wamocon/rezepte-app")
        breit = D.zugangs_quellen("umsetzung", self.geheim, app_modus=True)
        (projekt / "datei").write_text("x")
        (self.jobs / ".projekte").mkdir(exist_ok=True)
        (self.jobs / ".projekte" / "rezepte-app.json").write_text(json.dumps({"schnitt": sorted(eng)}))
        anfrage = D.zugang_pruefen("rezepte-app", breit, [])  # Lauf A mit Repo, Lauf B ohne Repo: Freigabe nötig
        self.assertEqual(anfrage["input"]["neu"], ["GH_APP_SCHREIBEN@*"])
        anderes = D.zugangs_quellen("umsetzung", self.geheim, app_modus=True, repo="wamocon/anderes")
        self.assertIsNotNone(D.zugang_pruefen("rezepte-app", anderes, []))  # anderes Repo: Freigabe nötig
        self.assertIsNone(D.zugang_pruefen("rezepte-app", eng, []))  # dasselbe Repo: frei
        self.assertIn("GH_TOKEN", D.zugangs_quellen("umsetzung", self.geheim))  # ohne App-Modus wie bisher
        ohne_token = D.sandbox_env("umsetzung", self.geheim, app_modus=True)
        self.assertNotIn("GH_TOKEN", ohne_token)  # App-Modus ohne erzeugtes Token: auch kein persönlicher
        git = {ohne_token[f"GIT_CONFIG_KEY_{i}"] for i in range(int(ohne_token["GIT_CONFIG_COUNT"]))}
        self.assertNotIn("credential.https://github.com.helper", git)

    def test_repo_ist_an_den_auftrag_gebunden(self):
        jobdir = self.jobs / JOB
        jobdir.mkdir()
        with mock.patch.object(D, "lese_geheimnisse", return_value={}):
            D.vorbereiten(jobdir, JOB, "rezepte-app", "umsetzung", "wamocon/rezepte-app")
            D.vorbereiten(jobdir, JOB, "rezepte-app", "umsetzung", "wamocon/rezepte-app")  # Fortsetzung: ok
            for anders in (None, "wamocon/anderes"):
                with self.subTest(repo=anders), self.assertRaises(D.Abgewiesen):
                    D.vorbereiten(jobdir, JOB, "rezepte-app", "umsetzung", anders)
            meta = json.loads((jobdir / "auftrag.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["repo"], "wamocon/rezepte-app")
            ohne = self.jobs / "0123456789abcdef"
            ohne.mkdir()
            D.vorbereiten(ohne, "0123456789abcdef", "rezepte-app", "review")  # ohne Repo wie bisher
            self.assertIsNone(json.loads((ohne / "auftrag.json").read_text(encoding="utf-8"))["repo"])

    @unittest.skipUnless(LINUX, "Prozess mit /bin/sh und AF_UNIX-Socket nur unter Linux")
    def test_token_in_der_ausgabe_wird_geschwaerzt(self):
        import threading

        class Leitung:  # stdin der SSH-Verbindung: bleibt offen, bis der Test endet
            def __init__(self):
                self.ende, self.buffer = threading.Event(), self

            def read1(self, _n):
                self.ende.wait(60)
                return b""
        jobdir = self.jobs / JOB
        (jobdir / "zustand").mkdir(parents=True)
        zeile = json.dumps({"type": "assistant", "text": f"Das Token ist {self.fake.token} und weiter"})
        gesehen: list[bytes] = []
        leitung = Leitung()
        try:
            with mock.patch.object(D.sys, "stdin", leitung), mock.patch.object(D, "stop_units"), \
                 mock.patch.object(D, "_status"), mock.patch.object(D, "egress_offen", return_value=False), \
                 mock.patch.object(D, "emit_raw", side_effect=lambda b: gesehen.append(b) or True):
                code = D._ausfuehren(["/bin/sh", "-c", f"cat >/dev/null; printf '%s\\n' '{zeile}'"],
                                     {"GH_TOKEN": self.fake.token, "PATH": "/usr/bin:/bin"}, "Auftrag", jobdir, JOB,
                                     "rezepte-app", "umsetzung")
        finally:
            leitung.ende.set()
        text = b"".join(gesehen).decode("utf-8")
        self.assertEqual(code, D.EXIT_OK)
        self.assertIn("Das Token ist [GH_TOKEN] und weiter", text)
        self.assertNotIn(self.fake.token, text)

    # ------------------------------------------------------------ Schlüsseldatei
    def test_rechtepruefung(self):
        def st(mode, uid=0):
            return os.stat_result((mode, 1, 1, 1, uid, 0, 1700, 0, 0, 0))
        pruefen = self._echte_rechtepruefung  # unter Windows ist D._rechte_grund in setUp ersetzt
        with mock.patch.object(D, "SCHLUESSEL_BESITZER", (0,)):
            self.assertIsNone(pruefen(st(0o100640), "Datei", True))
            self.assertIsNone(pruefen(st(0o100600), "Datei", True))
            self.assertIsNone(pruefen(st(0o40750), "Ordner", False))
            self.assertIn("beschreibbar", pruefen(st(0o100660), "Datei", True))
            self.assertIn("beschreibbar", pruefen(st(0o100602), "Datei", True))
            self.assertIn("für alle", pruefen(st(0o100644), "Datei", True))
            self.assertIn("root", pruefen(st(0o100640, uid=995), "Datei", True))
            self.assertIn("reguläre", pruefen(st(0o120777), "Datei", True))
            self.assertIn("reguläre", pruefen(st(0o10640), "Datei", True))  # FIFO
            self.assertIn("Ordner", pruefen(st(0o100750), "Ordner", False))
            self.assertIn("beschreibbar", pruefen(st(0o40770), "Ordner", False))

    _echte_rechtepruefung = staticmethod(D._rechte_grund)

    def test_schluessel_zu_gross(self):
        (self.ordner / "gross.pem").write_bytes(b"-----BEGIN RSA PRIVATE KEY-----\n" + b"A" * (D.MAX_SCHLUESSEL_BYTES + 10))
        if LINUX:
            os.chmod(self.ordner / "gross.pem", 0o640)
        with self.assertRaises(D.GithubAppFehler) as ctx:
            D.schluessel_lesen("gross.pem")
        self.assertIn("zu groß", str(ctx.exception))
        for name in ("../gh-app.pem", "a/b", "", ".pem"):
            with self.subTest(name=name), self.assertRaises(D.GithubAppFehler):
                D.schluessel_lesen(name)

    @unittest.skipUnless(LINUX, "symbolische Links und POSIX-Rechte")
    def test_schluessel_als_link_oder_mit_falschen_rechten(self):
        os.symlink(self.ordner / "gh-app.pem", self.ordner / "link.pem")
        with self.assertRaises(D.GithubAppFehler):
            D.schluessel_lesen("link.pem")
        os.chmod(self.ordner / "gh-app.pem", 0o644)
        with self.assertRaises(D.GithubAppFehler):
            D.schluessel_lesen("gh-app.pem")
        os.chmod(self.ordner / "gh-app.pem", 0o660)
        with self.assertRaises(D.GithubAppFehler):
            D.schluessel_lesen("gh-app.pem")
        os.chmod(self.ordner / "gh-app.pem", 0o640)
        os.chmod(self.ordner, 0o770)
        with self.assertRaises(D.GithubAppFehler):
            D.schluessel_lesen("gh-app.pem")
        os.chmod(self.ordner, 0o750)
        os.mkfifo(self.ordner / "fifo.pem")
        with self.assertRaises(D.GithubAppFehler):  # hängt nicht (O_NONBLOCK), abgewiesen als keine reguläre Datei
            D.schluessel_lesen("fifo.pem")
        self.assertEqual(bytes(D.schluessel_lesen("gh-app.pem")), self.pem)

    # ------------------------------------------------------------ Transport
    def test_https_prueft_zertifikat_ohne_proxy_und_ohne_weiterleitung(self):
        gesehen = {}

        class Verbindung:
            def __init__(self, host, port, timeout=None, context=None):
                gesehen.update(host=host, port=port, timeout=timeout, context=context)

            def request(self, methode, pfad, body=None, headers=None):
                gesehen.update(methode=methode, pfad=pfad)

            def getresponse(self):
                return type("A", (), {"status": 302, "read": lambda self, n: b"{}"})()

            def close(self):
                gesehen["zu"] = True

        self.patches[1].stop()  # echtes _https mit Attrappe der Verbindung
        try:
            with mock.patch.object(D.http.client, "HTTPSConnection", Verbindung), \
                 mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://boese.example:3128"}):
                status, _ = D._https("DELETE", "/installation/token", {"Authorization": "Bearer x"})
        finally:
            self.patches[1].start()
        self.assertEqual(status, 302)  # eine Weiterleitung wird nicht verfolgt
        self.assertEqual((gesehen["host"], gesehen["port"], gesehen["timeout"]), ("api.github.com", 443, D.GITHUB_ZEITLIMIT_S))
        kontext = gesehen["context"]
        self.assertEqual(kontext.verify_mode, D.ssl.CERT_REQUIRED)
        self.assertTrue(kontext.check_hostname)
        self.assertGreaterEqual(kontext.minimum_version, D.ssl.TLSVersion.TLSv1_2)
        self.assertTrue(gesehen["zu"])


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
        for name in ("SONIOX_API_KEY", "LITELLM_API_KEY", "GH_TOKEN_LESEN", *D.GH_APP_VARIABLEN):
            self.assertNotIn(name, erlaubt)
        self.assertFalse(any(n.startswith("WAMO_") for n in erlaubt))  # App-Konfiguration nie in der Sandbox

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
        self.assertIn("GitHub", dev_agent.REMOTE_EXIT_STATUS[D.EXIT_GITHUB_APP])
        self.assertIn("start_abgelehnt", dev_agent.REMOTE_EREIGNIS_STATUS)
        self.assertEqual(dev_agent.REPO_RE.pattern, D.REPO_RE.pattern)  # gleiche Repo-Regel auf PC und Server

    def test_zugang_vertrag_zwischen_kern_und_dispatcher(self):
        """Die zweite stdin-Zeile: Felder, Arten und Meldungen müssen auf PC/Kern und Server gleich lauten."""
        self.assertEqual(D.ZUGANG_FELDER, {"typ", "wert", "workspace"})
        self.assertEqual(set(D.ZUGANG_FORMAT), {"oauth", "api"})
        self.assertEqual(D.ZUGANG_MODI, ("folgt", "server"))
        self.assertEqual(D.ZUGANG_VARIABLE, {"oauth": "CLAUDE_CODE_OAUTH_TOKEN", "api": "ANTHROPIC_API_KEY"})
        self.assertEqual(set(D.ZUGANG_VARIABLE.values()), set(wk.CLAUDE_VARIABLEN))
        self.assertIn("zugang_ungueltig", dev_agent.REMOTE_EREIGNIS_STATUS)
        self.assertIn("Claude-Zugang", dev_agent.REMOTE_EREIGNIS_STATUS["zugang_ungueltig"])
        ablauf = (INFRA / "wamo-job-einrichten.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(ablauf.count("kontingent.py"), 3)  # Pflichtdatei, Installation und Anleitung
        self.assertIn('"$HERE/kontingent.py"', ablauf)
        # der Parser für den Dispatcher kennt nur die Standardbibliothek (er läuft in /opt/wamo/waechter ohne Pakete)
        import ast
        import kontingent
        baum = ast.parse(Path(kontingent.__file__).read_text(encoding="utf-8"))
        module = {n.module for n in ast.walk(baum) if isinstance(n, ast.ImportFrom)} | \
                 {a.name for n in ast.walk(baum) if isinstance(n, ast.Import) for a in n.names}
        self.assertLessEqual({m.split(".")[0] for m in module if m}, {"__future__", "json", "math", "re", "datetime"})

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


OAUTH_WERT = "sk-ant-oat01-" + "GEHEIMoauth0" * 5
API_WERT = "sk-ant-api03-" + "GEHEIMapi00" * 5
NIKO_GEHEIM = {"ANTHROPIC_API_KEY": "sk-ant-api03-NikoServerSchluessel0123456789", "ANTHROPIC_WORKSPACE_ID": "wrkspc_01ABCdef",
               "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-NikoServerAbo0123456789abc", "GH_TOKEN": "ghp_cccccccccccc"}


def zeile(typ="oauth", wert=OAUTH_WERT, **extra) -> bytes:
    return json.dumps({"typ": typ, "wert": wert, **extra}).encode() + b"\n"


class PersoenlicherZugangTests(unittest.TestCase):
    """Übergabe des persönlichen Claude-Zugangs: zweite stdin-Zeile, strenge Prüfung, einziger Claude-Zugang des Laufs,
    Modellwahl nach Art, Buchführung je Profil, Schwärzung. Nichts davon zeigt den Wert."""

    def test_zugangszeile_streng(self):
        for typ, wert in (("oauth", OAUTH_WERT), ("api", API_WERT)):
            z = D.parse_zugang(zeile(typ, wert), "1a2b3c4d")
            self.assertEqual((z.typ, z.wert, z.workspace, z.profil), (typ, wert, None, "1a2b3c4d"))
        self.assertEqual(D.parse_zugang(zeile("api", API_WERT, workspace="wrkspc_01ABCdef")).workspace, "wrkspc_01ABCdef")
        schlecht = [
            b"", b"\n", b"kein json\n", b"[]\n", zeile()[:-1],  # ohne Zeilenende
            zeile("oauth", API_WERT), zeile("api", OAUTH_WERT),  # Wert passt nicht zum Typ
            zeile("api", "sk-ant-admin01-" + "A" * 40), zeile("api", "sk-ant-sid01-" + "A" * 40),
            zeile("oauth", "sk-ant-oat01-kurz"), zeile("oauth", OAUTH_WERT + " "), zeile("oauth", OAUTH_WERT + "\n"),
            zeile("oauth", "x" + OAUTH_WERT), zeile("oauth", "sk-ant-oat01-" + "ä" * 30),
            zeile("oauth", OAUTH_WERT, workspace="wrkspc_01ABCdef"),  # Workspace nur bei api
            zeile("api", API_WERT, workspace="evil\nX: 1"), zeile("api", API_WERT, workspace=5),
            zeile("kaputt", OAUTH_WERT), zeile(None, OAUTH_WERT), zeile(["oauth"], OAUTH_WERT),
            zeile("oauth", None), zeile("oauth", 5), zeile("oauth", [OAUTH_WERT]),
            zeile("oauth", OAUTH_WERT, extra="x"),  # unbekanntes Feld
            b'{"typ": "oauth"}\n', b"\xff\xfe\n", b"[" * 5000 + b"\n",
            zeile("oauth", "sk-ant-oat01-" + "A" * (D.MAX_ZUGANG_BYTES)),  # zu lang
        ]
        for raw in schlecht:
            with self.subTest(raw=raw[:50]):
                with self.assertRaises(D.ZugangUngueltig) as ctx:
                    D.parse_zugang(raw)
                for geheim in (OAUTH_WERT, API_WERT, "GEHEIM", "sk-ant-"):  # die Meldung nennt nie etwas aus der Zeile
                    self.assertNotIn(geheim, str(ctx.exception))
        z = D.parse_zugang(zeile(), "1a2b3c4d")
        self.assertNotIn(OAUTH_WERT, repr(z) + str(z))  # auch ein versehentliches Protokollieren zeigt den Wert nicht
        self.assertTrue(issubclass(D.ZugangUngueltig, D.Abgewiesen))

    def test_kopfzeile_zugang_und_profil(self):
        def kopf(**extra):
            return json.dumps({"auftrag": "x", **extra}).encode() + b"\n"
        self.assertEqual(D.parse_header(kopf(zugang="folgt", profil="1a2b3c4d"))[4:], ("folgt", "1a2b3c4d"))
        self.assertEqual(D.parse_header(kopf(zugang="server"))[4:], ("server", None))
        with self.assertRaises(D.Abgewiesen):  # ohne Kennung teilten sich alle persönlichen Zugänge eine Buchführung
            D.parse_header(kopf(zugang="folgt"))
        for schlecht in ({"zugang": "ja"}, {"zugang": 1}, {"zugang": ["folgt"]}, {"profil": "xyz"}, {"profil": "1A2B3C4D"},
                         {"profil": "1a2b3c4d5"}, {"profil": 5}, {"zugangswert": OAUTH_WERT}, {"wert": OAUTH_WERT}):
            with self.subTest(schlecht=schlecht), self.assertRaises(D.Abgewiesen):
                D.parse_header(kopf(**schlecht))

    def test_umgebung_enthaelt_nur_den_persoenlichen_zugang(self):
        for art in wk.AUFTRAGSARTEN:
            with self.subTest(art=art, typ="oauth"):
                z = D.ClaudeZugang("oauth", OAUTH_WERT, None, "1a2b3c4d")
                env = D.sandbox_env(art, NIKO_GEHEIM, zugang=z)
                self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], OAUTH_WERT)
                self.assertNotIn("ANTHROPIC_API_KEY", env)  # kein Mischen mit dem Schlüssel aus geheim.env
                self.assertNotIn("ANTHROPIC_CUSTOM_HEADERS", env)  # auch nicht mit dessen Workspace
                self.assertNotIn("ANTHROPIC_WORKSPACE_ID", env)
                self.assertNotIn(NIKO_GEHEIM["CLAUDE_CODE_OAUTH_TOKEN"], env.values())
                self.assertNotIn(NIKO_GEHEIM["ANTHROPIC_API_KEY"], env.values())
            with self.subTest(art=art, typ="api"):
                z = D.ClaudeZugang("api", API_WERT, None, "1a2b3c4d")
                env = D.sandbox_env(art, NIKO_GEHEIM, zugang=z)
                self.assertEqual(env["ANTHROPIC_API_KEY"], API_WERT)
                self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
                self.assertNotIn("ANTHROPIC_CUSTOM_HEADERS", env)
                self.assertNotIn(NIKO_GEHEIM["ANTHROPIC_API_KEY"], env.values())
        mit_workspace = D.sandbox_env("review", NIKO_GEHEIM, zugang=D.ClaudeZugang("api", API_WERT, "wrkspc_Mine01", "1a2b3c4d"))
        self.assertEqual(mit_workspace["ANTHROPIC_CUSTOM_HEADERS"], "anthropic-workspace-id: wrkspc_Mine01")
        # ohne jeden Zugang in geheim.env geht es trotzdem; die Dienst-Token der Art bleiben wie bisher
        ohne = D.sandbox_env("umsetzung", {"GH_TOKEN": "ghp_cccccccccccc"}, zugang=D.ClaudeZugang("oauth", OAUTH_WERT, None, None))
        self.assertEqual(ohne["CLAUDE_CODE_OAUTH_TOKEN"], OAUTH_WERT)
        self.assertEqual(ohne["GH_TOKEN"], "ghp_cccccccccccc")
        self.assertIsNone(D.sandbox_env("umsetzung", {"GH_TOKEN": "ghp_cccccccccccc"}))  # wie bisher: ohne Zugang kein Start
        erlaubt = set(D.basis_env()) | set(wk.CLAUDE_VARIABLEN) | {"ANTHROPIC_CUSTOM_HEADERS", "GH_TOKEN"}
        self.assertLessEqual({k for k in mit_workspace if not k.startswith("GIT_CONFIG_")}, erlaubt)

    def test_buchfuehrung_je_profil(self):
        z = D.ClaudeZugang("oauth", OAUTH_WERT, None, "1a2b3c4d")
        namen = D.zugangs_quellen("review", NIKO_GEHEIM, zugang=z)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN@1a2b3c4d", namen)
        self.assertNotIn("ANTHROPIC_API_KEY", namen)  # der Schlüssel aus geheim.env zählt nicht mit
        self.assertNotIn(OAUTH_WERT, " ".join(namen))
        andere = D.zugangs_quellen("review", NIKO_GEHEIM, zugang=D.ClaudeZugang("oauth", OAUTH_WERT, None, "9f8e7d6c"))
        self.assertNotEqual(namen, andere)
        self.assertIn("ANTHROPIC_API_KEY@?", D.zugangs_quellen("review", {}, zugang=D.ClaudeZugang("api", API_WERT, None, None)))
        self.assertIn("ANTHROPIC_API_KEY", D.zugangs_quellen("review", NIKO_GEHEIM))  # Serverzugang wie bisher

    def test_modell_nach_zugangsart(self):
        for art in wk.AUFTRAGSARTEN:
            self.assertEqual(D.modell_waehlen(art, None, "oauth"), "claude-sonnet-5-5")  # Max-Kontingent
            self.assertEqual(D.modell_waehlen(art, None, "api"), "claude-haiku-5-5")  # Guthaben
        for typ in ("oauth", "api"):  # Wunsch (WAMO_ENTWICKLUNG_MODELL, Kopfzeilenfeld modell) übersteuert, nur aus der Allowlist
            self.assertEqual(D.modell_waehlen("umsetzung", "claude-opus-5-5", typ), "claude-opus-5-5")
            self.assertEqual(D.modell_waehlen("umsetzung", "claude-haiku-5-5", typ), "claude-haiku-5-5")
            with self.assertRaises(ValueError):
                D.modell_waehlen("umsetzung", "claude-irgendwas", typ)
        opus = dataclasses.replace(wk.KARTEN["planung"], modell=wk.MODELL_OPUS)
        with mock.patch.dict(wk.KARTEN, {"planung": opus}):
            self.assertEqual(D.modell_waehlen("planung", None, "oauth"), "claude-opus-5-5")  # nie unter das der Art
        self.assertEqual(D.zugang_typ({"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_WERT}), "oauth")
        self.assertEqual(D.zugang_typ({"ANTHROPIC_API_KEY": API_WERT}), "api")

    def test_serverzugang_schalter(self):
        self.assertTrue(D.serverzugang_erlaubt({}))
        for an in ("an", "ja", "AN", "1"):
            self.assertTrue(D.serverzugang_erlaubt({"WAMO_SERVERZUGANG": an}))
        for aus in ("aus", "AUS", " nein ", "0", "false", "off", "no", "disabled", "vielleicht", "an!"):
            self.assertFalse(D.serverzugang_erlaubt({"WAMO_SERVERZUGANG": aus}), aus)  # alles Unbekannte sperrt
        for tippfehler in ("WAMO_SERVERZUGAGN", "WAMO_SERVERZUGANG_", "WAMO_SERVER_ZUGANG"):
            self.assertFalse(D.serverzugang_erlaubt({tippfehler: "aus"}), tippfehler)  # Tippfehler im Namen sperrt
            self.assertFalse(D.serverzugang_erlaubt({tippfehler: "an", "WAMO_SERVERZUGANG": "an"}), tippfehler)

    def test_schwaerzen_und_dateien(self):
        ersetzungen = D.schwaerzungen({"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_WERT, "ANTHROPIC_API_KEY": API_WERT,
                                       "GH_TOKEN": "ghs_" + "T0k3n" * 6, "VERCEL_TOKEN": "kurz"})
        text = f"a {OAUTH_WERT} b {API_WERT} c ghs_{'T0k3n' * 6} d kurz".encode()
        aus = D.schwaerzen(text, ersetzungen)
        self.assertEqual(aus, b"a [CLAUDE_ZUGANG] b [CLAUDE_ZUGANG] c [GH_TOKEN] d kurz")
        self.assertEqual(D.schwaerzungen({"CLAUDE_CODE_OAUTH_TOKEN": "kurz", "ANTHROPIC_API_KEY": ""}), [])
        with tempfile.TemporaryDirectory() as tmp:
            wurzel = Path(tmp) / "claude"
            (wurzel / "projects" / "p").mkdir(parents=True)
            sitzung = wurzel / "projects" / "p" / "s.jsonl"
            sitzung.write_bytes(json.dumps({"tool_result": f"TOKEN={OAUTH_WERT}"}).encode() + b"\nzweite Zeile\n")
            (wurzel / "unberuehrt.txt").write_bytes(b"nichts Geheimes")
            gross = wurzel / "gross.bin"
            gross.write_bytes(b"x" * 400)
            ausserhalb = Path(tmp) / "ausserhalb.txt"
            ausserhalb.write_bytes(OAUTH_WERT.encode())
            try:
                os.symlink(ausserhalb, wurzel / "link.txt")
                os.symlink(tmp, wurzel / "ordnerlink", target_is_directory=True)
            except (OSError, NotImplementedError):
                pass  # Windows ohne Rechte für Links
            with mock.patch.object(D, "MAX_SCHWAERZ_DATEI", 200):
                geaendert, offen = D.zugang_aus_dateien_entfernen(wurzel, ersetzungen)
            self.assertGreaterEqual(geaendert, 2)  # Sitzung geschwärzt, große gelöscht
            self.assertEqual(offen, 0)
            self.assertEqual([n for n in os.listdir(wurzel / "projects" / "p") if n.startswith(".schwaerzen")], [])
            self.assertEqual(json.loads(sitzung.read_bytes().splitlines()[0]), {"tool_result": "TOKEN=[CLAUDE_ZUGANG]"})
            self.assertIn(b"zweite Zeile", sitzung.read_bytes())
            self.assertEqual((wurzel / "unberuehrt.txt").read_bytes(), b"nichts Geheimes")
            self.assertFalse(gross.exists())
            self.assertEqual(ausserhalb.read_bytes(), OAUTH_WERT.encode())  # Ziel eines Links bleibt unangetastet
            self.assertEqual(D.zugang_aus_dateien_entfernen(wurzel, []), (0, 0))
            self.assertEqual(D.zugang_aus_dateien_entfernen(Path(tmp) / "gibt-es-nicht", ersetzungen), (0, 0))

    def test_schwaerzen_erkennt_jeden_anthropic_schluessel_und_bleibt_sonst_still(self):
        fremd = "sk-ant-api03-" + "FREMDER0schluessel" * 2  # nicht der Wert dieses Laufs, aber ein Schlüssel
        ersetzungen = D.schwaerzungen({"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_WERT})
        text = f"a {fremd} b sk-ant-kurz c".encode()
        self.assertEqual(D.schwaerzen(text, ersetzungen), b"a [CLAUDE_ZUGANG] b sk-ant-kurz c")
        self.assertEqual(D.schwaerzen(b"nichts Besonderes", ersetzungen), b"nichts Besonderes")
        self.assertEqual(D.schwaerzen(f"{fremd}".encode(), []), b"[CLAUDE_ZUGANG]")  # auch ganz ohne Wert des Laufs

    def test_unvollstaendiges_schwaerzen_entfernt_die_sitzungsdaten(self):
        ersetzungen = D.schwaerzungen({"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_WERT})
        with tempfile.TemporaryDirectory() as tmp:
            jobdir = Path(tmp)
            sitzung = jobdir / "claude" / "projects" / "p" / "s.jsonl"
            sitzung.parent.mkdir(parents=True)
            sitzung.write_bytes(f"TOKEN={OAUTH_WERT}".encode())
            (jobdir / "claude" / "settings.json").write_text("{}")
            zeilen_: list[str] = []
            with mock.patch.object(D, "log", side_effect=lambda t, pflicht=False: zeilen_.append(t)):
                D.zugang_aufraeumen(jobdir, JOB, ersetzungen)  # alles gut: geschwärzt, Sitzung bleibt
                self.assertEqual(sitzung.read_text(), "TOKEN=[CLAUDE_ZUGANG]")
                self.assertTrue(any("schwaerzen" in z and "unvollstaendig" not in z for z in zeilen_))
                sitzung.write_bytes(f"TOKEN={OAUTH_WERT}".encode())
                with mock.patch.object(D, "MAX_SCHWAERZ_DATEIEN", 0):  # Grenze überschritten: fail-closed
                    D.zugang_aufraeumen(jobdir, JOB, ersetzungen)
                self.assertFalse((jobdir / "claude" / "projects").exists())  # Sitzungsdaten sind weg, der Wert mit
                self.assertTrue(any("schwaerzen_unvollstaendig" in z for z in zeilen_))
                with mock.patch.object(D, "zugang_aus_dateien_entfernen", side_effect=RuntimeError("kaputt")):
                    D.zugang_aufraeumen(jobdir, JOB, ersetzungen)  # wirft nie
            self.assertNotIn(OAUTH_WERT, "\n".join(zeilen_))

    def test_zugang_ist_kein_json_und_nicht_iterierbar(self):
        z = D.ClaudeZugang("oauth", OAUTH_WERT, None, "1a2b3c4d")
        with self.assertRaises(TypeError):
            json.dumps(z)
        with self.assertRaises(TypeError):
            list(z)
        self.assertNotIn(OAUTH_WERT, repr(z))

    def test_kontingent_ereignis_nie_eine_ausnahme(self):
        import kontingent
        for roh in (b"kein json", b"[" * 5000, b'{"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": '
                    b'{"five_hour": {"utilization": 1' + b"0" * 400 + b', "resetsAt": 1}}}}'):
            self.assertIsNone(D.kontingent_ereignis(kontingent, roh))
        self.assertIsNotNone(D.kontingent_ereignis(kontingent, json.dumps(
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"}}).encode()))


class PersoenlicherZugangStartTests(unittest.TestCase):
    """cmd_start mit persönlichem Zugang: Sperren, Selbsttest und Sandbox-Lauf sind Attrappen, alles andere ist echt."""

    class _Sperre:
        def __init__(self, pfad):
            self.datei = None

        def versuchen(self):
            return True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        wurzel = Path(self.tmp.name)
        self.jobs, self.projekte, self.heim = wurzel / "jobs", wurzel / "projekte", wurzel / "heim"
        for p in (self.jobs, self.projekte / "rezepte-app", self.heim):
            p.mkdir(parents=True)
        self.logzeilen: list[str] = []
        self.ereignisse: list[dict] = []
        self.selbsttests = 0
        self.patches = [mock.patch.object(D, "log", side_effect=lambda t, pflicht=False: self.logzeilen.append(t)),
                        mock.patch.object(D, "JOBS", self.jobs), mock.patch.object(D, "PROJEKTE", self.projekte),
                        mock.patch.object(D, "HEIM", self.heim),
                        mock.patch.object(D, "emit", side_effect=lambda ev: self.ereignisse.append(ev) or True),
                        mock.patch.object(D, "Sperre", self._Sperre), mock.patch.object(D, "aufraeumen"),
                        mock.patch.object(D, "exklusiv", lambda pfad: contextlib.nullcontext()),
                        mock.patch.object(D, "selbsttest", side_effect=self._selbsttest),
                        mock.patch.object(D, "projekt_entschaerfen", return_value=[])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _selbsttest(self, *_):
        self.selbsttests += 1
        return True, "ok"

    def start(self, kopf_extra=None, zugangszeile=None, geheim=None, art="umsetzung", job=JOB, session=None):
        """Liefert (Code, Aufrufe von _ausfuehren als (argv, env)). zugangszeile=None: es wird keine gelesen."""
        aufrufe: list[tuple] = []

        def fake_ausfuehren(argv, env, auftrag, jobdir, job_, projekt, art_):
            aufrufe.append((list(argv), dict(env)))
            return D.EXIT_OK

        kopf = json.dumps({"auftrag": "Baue etwas", "system": "Regeln", **(kopf_extra or {})}).encode() + b"\n"
        with mock.patch.object(D, "_kopfzeile_lesen", return_value=kopf), \
             mock.patch.object(D, "_zugangszeile_lesen", return_value=zugangszeile) as lesen, \
             mock.patch.object(D, "lese_geheimnisse", return_value=NIKO_GEHEIM if geheim is None else geheim), \
             mock.patch.object(D, "_ausfuehren", side_effect=fake_ausfuehren):
            try:
                code = D.cmd_start(job, "rezepte-app", art, session)
            finally:
                self.zeilen_gelesen = lesen.call_count
        return code, aufrufe

    def ereignis_namen(self) -> list[str]:
        return [e["ereignis"] for e in self.ereignisse if e.get("ereignis") != "selbsttest_ok"]

    def nirgends(self, *werte: str) -> None:
        """Keiner der Werte steht in Log, Ereignissen oder einer Datei unter dem Auftragsordner (inklusive Projekt)."""
        alles = "\n".join(self.logzeilen) + json.dumps(self.ereignisse)
        for ordner, _, dateien in os.walk(self.tmp.name):
            for name in dateien:
                alles += (Path(ordner) / name).read_bytes().decode("utf-8", "replace")
        for wert in werte:
            self.assertNotIn(wert, alles)

    def test_oauth_zugang_ersetzt_den_serverzugang_und_waehlt_sonnet(self):
        code, aufrufe = self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, zeile("oauth", OAUTH_WERT))
        self.assertEqual(code, D.EXIT_OK)
        self.assertEqual(self.zeilen_gelesen, 1)
        argv, env = aufrufe[0]
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], OAUTH_WERT)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_CUSTOM_HEADERS", env)
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-5-5")
        self.assertNotIn(OAUTH_WERT, " ".join(argv))  # nie in argv
        self.assertEqual(env["GH_TOKEN"], "ghp_cccccccccccc")  # Dienst-Token der Art wie bisher
        self.assertTrue(any("claude=persoenlich-oauth" in z and "modell=claude-sonnet-5-5" in z for z in self.logzeilen))
        self.assertTrue(any("CLAUDE_CODE_OAUTH_TOKEN@1a2b3c4d" in z for z in self.logzeilen))  # Kennung, nicht der Wert
        self.nirgends(OAUTH_WERT, NIKO_GEHEIM["ANTHROPIC_API_KEY"])

    def test_api_zugang_waehlt_haiku_und_kein_oauth_aus_geheim_env(self):
        code, aufrufe = self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, zeile("api", API_WERT, workspace="wrkspc_Mine01"))
        argv, env = aufrufe[0]
        self.assertEqual(env["ANTHROPIC_API_KEY"], API_WERT)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)  # geheim.env hat beide, keiner davon gilt
        self.assertEqual(env["ANTHROPIC_CUSTOM_HEADERS"], "anthropic-workspace-id: wrkspc_Mine01")  # nicht der von Niko
        self.assertEqual(argv[argv.index("--model") + 1], "claude-haiku-5-5")
        self.nirgends(API_WERT, NIKO_GEHEIM["CLAUDE_CODE_OAUTH_TOKEN"])

    def test_modellwunsch_uebersteuert_nach_allowlist(self):
        for zugang in (zeile("oauth", OAUTH_WERT), zeile("api", API_WERT)):
            argv = self.start({"zugang": "folgt", "profil": "1a2b3c4d", "modell": "claude-opus-5-5"}, zugang,
                              job=uuid.uuid4().hex[:16])[1][0][0]
            self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
        with self.assertRaises(D.Abgewiesen):
            self.start({"zugang": "folgt", "profil": "1a2b3c4d", "modell": "claude-irgendwas"}, zeile())
        self.assertEqual(self.zeilen_gelesen, 0)  # ein Wunsch außerhalb der Allowlist scheitert, bevor etwas gelesen wird

    def test_ungueltiger_zugang_startet_nichts(self):
        for schlecht in (zeile("oauth", "sk-ant-api03-" + "A" * 40), b"kein json\n", zeile("oauth", OAUTH_WERT, extra="x")):
            self.ereignisse.clear()
            code, aufrufe = self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, schlecht)
            self.assertEqual(code, D.EXIT_ABGEWIESEN)
            self.assertEqual(aufrufe, [])
            self.assertEqual(self.ereignis_namen(), ["zugang_ungueltig"])
        self.assertEqual(self.selbsttests, 0)  # scheitert vor Sperren und Selbsttest
        self.nirgends(OAUTH_WERT, "sk-ant-api03-" + "A" * 40)

    def test_zeitlimit_der_zugangszeile_wird_abgewiesen(self):
        with mock.patch.object(D, "_zugangszeile_lesen", side_effect=D.ZugangUngueltig("Zugangszeile kam nicht rechtzeitig")), \
             mock.patch.object(D, "_kopfzeile_lesen",
                               return_value=b'{"auftrag": "x", "zugang": "folgt", "profil": "1a2b3c4d"}\n'):
            self.assertEqual(D.cmd_start(JOB, "rezepte-app", "review"), D.EXIT_ABGEWIESEN)
        self.assertEqual(self.ereignis_namen(), ["zugang_ungueltig"])

    def test_server_zugang_nur_wenn_erlaubt(self):
        for extra in ({}, {"zugang": "server"}):  # älterer Kern ohne Feld, oder ausdrücklich erlaubt
            code, aufrufe = self.start(extra, None, job=uuid.uuid4().hex[:16])
            self.assertEqual(code, D.EXIT_OK)
            self.assertEqual(self.zeilen_gelesen, 0)  # keine zweite Zeile
            argv, env = aufrufe[0]
            self.assertEqual(env["ANTHROPIC_API_KEY"], NIKO_GEHEIM["ANTHROPIC_API_KEY"])  # wie bisher
            self.assertEqual(argv[argv.index("--model") + 1], "claude-haiku-5-5")  # API-Schlüssel: Haiku wie bisher
            self.assertTrue(any("claude=server" in z for z in self.logzeilen))
        # abgeschaltet: ohne persönlichen Zugang kein Start mit dem Schlüssel aus geheim.env, mit klarer Meldung
        self.ereignisse.clear()
        aus = {**NIKO_GEHEIM, "WAMO_SERVERZUGANG": "aus"}
        for extra in ({}, {"zugang": "server"}):
            code, aufrufe = self.start(extra, None, geheim=aus, job=uuid.uuid4().hex[:16])
            self.assertEqual(code, D.EXIT_KEIN_ZUGANG)
            self.assertEqual(aufrufe, [])
        self.assertEqual(self.ereignis_namen(), ["keine_zugangsdaten"] * 2)
        self.assertIn("WAMO_SERVERZUGANG", [e for e in self.ereignisse if e["ereignis"] == "keine_zugangsdaten"][0]["grund"])
        code, aufrufe = self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, zeile(), geheim=aus, job=uuid.uuid4().hex[:16])
        self.assertEqual(code, D.EXIT_OK)  # der persönliche Zugang geht trotzdem
        self.assertEqual(aufrufe[0][1]["CLAUDE_CODE_OAUTH_TOKEN"], OAUTH_WERT)

    def test_wiederholung_nach_approve_sendet_den_zugang_erneut_und_speichert_nichts(self):
        fp = "0123456789abcdef" * 4
        job = uuid.uuid4().hex[:16]
        code1, a1 = self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, zeile(), job=job)
        (self.jobs / job / "freigaben.json").write_text(json.dumps([fp]))  # wie nach approve
        code2, a2 = self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, zeile(), job=job)
        self.assertEqual((code1, code2), (D.EXIT_OK, D.EXIT_OK))
        self.assertEqual(a1[0][1]["CLAUDE_CODE_OAUTH_TOKEN"], OAUTH_WERT)
        self.assertEqual(a2[0][1]["CLAUDE_CODE_OAUTH_TOKEN"], OAUTH_WERT)  # nur weil er neu übergeben wurde
        self.nirgends(OAUTH_WERT)
        # ohne erneute Übergabe gibt es keinen Zugang: der Dispatcher hat nichts aufbewahrt
        code3, a3 = self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, b"", job=job)
        self.assertEqual((code3, a3), (D.EXIT_ABGEWIESEN, []))

    def test_zugang_einer_anderen_person_im_selben_projekt_braucht_freigabe(self):
        self.assertEqual(self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, zeile())[0], D.EXIT_OK)  # leeres Projekt
        (self.projekte / "rezepte-app" / "datei.txt").write_text("Inhalt")  # jetzt hat das Projekt Inhalt
        gleiche = self.start({"zugang": "folgt", "profil": "1a2b3c4d"}, zeile(), job=uuid.uuid4().hex[:16])
        self.assertEqual(gleiche[0], D.EXIT_OK)  # dieselbe Person: frei
        self.ereignisse.clear()
        andere = self.start({"zugang": "folgt", "profil": "9f8e7d6c"}, zeile(), job=uuid.uuid4().hex[:16])
        self.assertEqual(andere[0], D.EXIT_ZUGANG_ERWEITERT)
        self.assertEqual(andere[1], [])
        ereignis = self.ereignisse[-1]
        self.assertEqual(ereignis["ereignis"], "zugang_erweitert")
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN@9f8e7d6c", ereignis["input"]["neu"])
        self.assertNotIn(OAUTH_WERT, json.dumps(ereignis))
        # der Serverzugang ist für dieses Projekt ebenfalls ein anderer Zugang als der persönliche
        self.assertEqual(self.start({}, None, job=uuid.uuid4().hex[:16])[0], D.EXIT_ZUGANG_ERWEITERT)

    def test_ping_meldet_serverzugang_und_kontingent(self):
        with mock.patch.object(D, "lese_geheimnisse", return_value={"WAMO_SERVERZUGANG": "aus"}), \
             mock.patch.object(D, "github_status", return_value="token"):
            D.cmd_ping()
        ping = self.ereignisse[-1]
        self.assertIs(ping["serverzugang"], False)
        self.assertIs(ping["kontingent"], True)
        self.assertEqual(ping["version"], "5")

    @unittest.skipUnless(LINUX, "Prozess mit /bin/sh nur unter Linux")
    def test_ausgabe_log_und_dateien_ohne_zugangswert_und_kontingent_nur_aus_der_allowlist(self):
        import threading
        from datetime import datetime, timezone

        class Leitung:  # stdin der SSH-Verbindung: bleibt offen, bis der Test endet
            def __init__(self):
                self.ende, self.buffer = threading.Event(), self

            def read1(self, _n):
                self.ende.wait(60)
                return b""
        jobdir = self.jobs / JOB
        (jobdir / "zustand").mkdir(parents=True)
        (jobdir / "claude" / "projects").mkdir(parents=True)
        sitzung = jobdir / "claude" / "projects" / "s.jsonl"
        ereignis = {"type": "rate_limit_event", "uuid": "u", "session_id": "s", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "five_hour", "resetsAt": 4_000_000_000,
            "unifiedWindows": {"five_hour": {"utilization": 0.42, "resetsAt": 4_000_000_000}}, "geheim": OAUTH_WERT}}
        zeilen_ = [json.dumps({"type": "assistant", "text": f"Das ist {OAUTH_WERT} und {API_WERT}"}),
                   json.dumps(ereignis), json.dumps(ereignis),  # zweimal derselbe Stand: nur einmal weitergegeben
                   json.dumps({"type": "wamo", "ereignis": "kontingent", "limits": [{"art": "woche", "prozent": 1}]})]
        gesehen: list[bytes] = []
        leitung = Leitung()
        skript = (f"cat >/dev/null; printf 'Fehlerausgabe %s\\n' \"$0\" >&2; printf '%s' 'TOKEN={OAUTH_WERT}' > '{sitzung}'; "
                  + "; ".join(f"printf '%s\\n' '{z}'" for z in zeilen_))
        ereignisse: list[dict] = []
        try:
            with mock.patch.object(D.sys, "stdin", leitung), mock.patch.object(D, "stop_units"), \
                 mock.patch.object(D, "_status"), mock.patch.object(D, "egress_offen", return_value=False), \
                 mock.patch.object(D, "emit_raw", side_effect=lambda b: gesehen.append(b) or True), \
                 mock.patch.object(D, "emit", side_effect=lambda ev: ereignisse.append(ev) or True):
                code = D._ausfuehren(["/bin/sh", "-c", skript, OAUTH_WERT],
                                     {"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_WERT, "ANTHROPIC_API_KEY": API_WERT,
                                      "PATH": "/usr/bin:/bin"}, "Auftrag", jobdir, JOB, "rezepte-app", "umsetzung")
        finally:
            leitung.ende.set()
        text = b"".join(gesehen).decode("utf-8")
        endet = datetime.fromtimestamp(4_000_000_000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(code, D.EXIT_OK)
        self.assertIn("Das ist [CLAUDE_ZUGANG] und [CLAUDE_ZUGANG]", text)
        self.assertNotIn("GEHEIM", text)
        self.assertNotIn('"ereignis": "kontingent"', text)  # die gefälschte wamo-Zeile aus der Sandbox ist verworfen
        kontingent = [e for e in ereignisse if e.get("ereignis") == "kontingent"]
        self.assertEqual(len(kontingent), 1)  # echtes Ereignis, einmal
        self.assertEqual(kontingent[0]["limits"], [{"art": "sitzung", "prozent": 42.0, "endet": endet, "status": "ok"}])
        self.assertNotIn("GEHEIM", json.dumps(ereignisse))
        self.assertEqual(sitzung.read_text(), "TOKEN=[CLAUDE_ZUGANG]")  # Datei unter claude/ geschwärzt
        lauflog = (jobdir / "lauf.log").read_text(encoding="utf-8")
        self.assertIn("Fehlerausgabe", lauflog)  # das Protokoll ist vollständig (der Lesethread wird abgewartet) ...
        self.assertIn("[CLAUDE_ZUGANG]", lauflog)
        self.assertNotIn("GEHEIM", lauflog)  # ... und ohne den Wert
        self.assertNotIn("GEHEIM", " ".join(self.logzeilen))


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
