#!/usr/bin/python3 -I
"""Rauchtest der WAMO-Auftrags-Sandbox auf Sokrates-2 (ohne Claude-Login).

Installiert als /opt/wamo/bin/wamo-job-rauchtest durch wamo-job-einrichten.sh. Ausführen als wamo:

    sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest              Grenzen der Sandbox, Netz, seccomp, Entschärfen
    sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --attrappe   zusätzlich Claude Code Ende zu Ende
    sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp        zusätzlich MCP-Server mit echter Umgebung

--attrappe startet in der Sandbox eine Attrappe der Messages-API (127.0.0.1:20180, nur Standardbibliothek) und
lässt Claude Code mit einem Dummy-Schlüssel dagegen laufen. Die Attrappe verlangt den Befehl
"git push origin main"; geprüft wird, dass der Wächter-Hook aus managed-settings.json in der Sandbox greift,
nur verwaltete Hooks laufen (ECC als verwaltetes Plugin ja, Benutzer- und Projekt-Hooks nein), die Anfrage über
"pending" sichtbar wird, eine Freigabe genau einmal wirkt, die Lebenslinie (stdin) und "cancel" den Lauf beenden.
--mcp startet "claude mcp list" in der Sandbox mit der Umgebung der Auftragsarten datenbank und umsetzung
(echte Token aus /etc/wamo/geheim.env, ausgegeben wird nur der Verbindungsstatus).
Projekt rauchtest-sandbox und die Auftragsordner werden danach entfernt.
"""

from __future__ import annotations

import importlib.machinery
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

D = importlib.machinery.SourceFileLoader("wamo_job_dispatch", "/opt/wamo/bin/wamo-job-dispatch").load_module()
DISPATCH = str(D.BIN / "wamo-job-dispatch")
PROJEKT = "rauchtest-sandbox"
PROJ = D.PROJEKTE / PROJEKT
PORT_ATTRAPPE = 20180  # im Bereich, den der Ausgangsfilter auf Loopback zulässt
ERGEBNISSE: list[tuple[str, bool]] = []

ATTRAPPE = r'''
import json, os, sys, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attrappe.log")
DELAY = float(os.environ.get("ATTRAPPE_PAUSE", "0"))
COMMAND = "git push origin main"
WIEDERHOLT = []

def log(obj):
    with open(LOG, "a") as f:
        f.write(json.dumps(obj) + "\n")

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass
    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        self._json({})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if "count_tokens" in self.path:
            return self._json({"input_tokens": 10})
        model = body.get("model", "claude-attrappe")
        tools = [t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)]
        last = (body.get("messages") or [{}])[-1]
        content = last.get("content") if isinstance(last.get("content"), list) else []
        results = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"]
        if "Bash" in tools:
            if DELAY:
                time.sleep(DELAY)
            text = json.dumps(results[0].get("content"), ensure_ascii=False)[:3000] if results else ""
            if results:
                log({"tool_result": text})
            nochmal = bool(results) and "Fact-Forcing Gate" in text and len(WIEDERHOLT) < 2
            if nochmal:
                WIEDERHOLT.append(1)
            if results and not nochmal:
                blocks, stop = [{"type": "text", "text": "fertig"}], "end_turn"
            else:  # erster Versuch, oder wie Claude nach dem ECC-Gate: Fakten genannt, noch einmal
                blocks = [{"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20], "name": "Bash",
                           "input": {"command": COMMAND, "description": "Rauchtest"}}]
                stop = "tool_use"
                log({"tool_use": COMMAND})
        else:
            blocks, stop = [{"type": "text", "text": "ok"}], "end_turn"
        if not body.get("stream"):
            return self._json({"id": "msg_attrappe", "type": "message", "role": "assistant", "model": model,
                               "content": blocks, "stop_reason": stop, "stop_sequence": None,
                               "usage": {"input_tokens": 10, "output_tokens": 5}})
        events = [("message_start", {"type": "message_start", "message": {
            "id": "msg_" + uuid.uuid4().hex[:20], "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1}}})]
        for i, b in enumerate(blocks):
            if b["type"] == "text":
                events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                       "content_block": {"type": "text", "text": ""}}))
                events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                       "delta": {"type": "text_delta", "text": b["text"]}}))
            else:
                events.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                    "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}))
                events.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                    "type": "input_json_delta", "partial_json": json.dumps(b["input"])}}))
            events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
        events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                         "usage": {"output_tokens": 5}}))
        events.append(("message_stop", {"type": "message_stop"}))
        payload = "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events).encode()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


NETZTEST = r'''
import ctypes, errno, socket, sys, threading
def name(e): return errno.errorcode.get(e.errno, str(e.errno)) if getattr(e, "errno", None) else type(e).__name__
def t(host, port=0, proto=0, fam=socket.AF_INET, timeout=5):
    try:
        s = socket.socket(fam, socket.SOCK_STREAM, proto)
    except OSError as e: return "socket-" + name(e)
    s.settimeout(timeout)
    try:
        s.connect((host, port) if fam != socket.AF_UNIX else host); return "offen"
    except socket.timeout: return "zeit"
    except OSError as e: return name(e)
    finally: s.close()
def tfo(port):
    s = socket.socket(); s.settimeout(3)
    try:
        s.sendto(b"x", socket.MSG_FASTOPEN, ("127.0.0.1", port)); return "gesendet"
    except OSError as e: return name(e)
def bind_an(host, port):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port)); s.listen(1); return "ok"
    except OSError as e: return name(e)
    finally: s.close()
def eigener_server():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 20123)); srv.listen(1)
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    return t("127.0.0.1", 20123)
w = {"postgres": t("127.0.0.1", 55322), "ollama": t("127.0.0.1", 11434), "lo9": t("127.0.0.1", 9),
     "lo9mptcp": t("127.0.0.1", 9, 262), "netz10": t("10.255.255.254", 9), "netz10mptcp": t("10.255.255.254", 9, 262),
     "bruecke": t(sys.argv[1], 9), "lan443": t("192.168.178.1", 443), "github": t("github.com", 443, timeout=10),
     "ipv6": t("2606:4700:4700::1111", 443, fam=socket.AF_INET6), "x11": t("\0/tmp/.X11-unix/X1", fam=socket.AF_UNIX),
     "tfo55322": tfo(55322), "eigener20123": eigener_server(),
     "bind_dev": bind_an("127.0.0.1", 20511), "bind_0.0.0.0:55999": bind_an("0.0.0.0", 55999),
     "bind_80": bind_an("127.0.0.1", 80), "bind_ephemer": bind_an("127.0.0.1", 0)}
for k, v in w.items(): print(k, v)
'''

SECCOMPTEST = r'''
import ctypes, errno, os, socket
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
def sc(nr, *args):
    r = libc.syscall(ctypes.c_long(nr), *[ctypes.c_long(a) for a in args])
    return "ok" if r >= 0 else errno.errorcode.get(ctypes.get_errno(), str(ctypes.get_errno()))
def sock(fam, typ, proto=0):
    try:
        socket.socket(fam, typ, proto).close(); return "ok"
    except OSError as e: return errno.errorcode.get(e.errno, str(e.errno))
try:
    os.unshare(os.CLONE_NEWNS); u = "ok"
except OSError as e: u = errno.errorcode.get(e.errno, str(e.errno))
w = {"unshare": u, "keyctl": sc(219, 0, 0), "io_uring_setup": sc(425, 1, 0), "bpf": sc(280, 0, 0, 0),
     "mount": sc(40, 0, 0, 0, 0, 0), "clone3": sc(435, 0, 0), "af_packet": sock(17, socket.SOCK_RAW),
     "af_alg": sock(38, socket.SOCK_SEQPACKET), "mptcp": sock(socket.AF_INET, socket.SOCK_STREAM, 262),
     "sctp": sock(socket.AF_INET, socket.SOCK_STREAM, 132), "tcp": sock(socket.AF_INET, socket.SOCK_STREAM)}
for k, v in w.items(): print(k, v)
'''


def pruefe(name: str, ok: bool, detail: str = "") -> bool:
    ERGEBNISSE.append((name, ok))
    print(f"[{'OK    ' if ok else 'FEHLER'}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


def sandbox(job: str, befehl: str, zeit: int | None = None, timeout: int = 180, env: dict | None = None,
            stdin: bytes | None = None) -> subprocess.CompletedProcess:
    argv = [str(D.RUNNER), "--auftrag", job, "--projekt", PROJEKT]
    if zeit:
        argv += ["--zeitlimit", str(zeit)]
    argv += ["--", "/bin/bash", "-c", befehl]
    return subprocess.run(argv, capture_output=True, env=env or D.basis_env(), timeout=timeout,
                          input=stdin if stdin is not None else b"")


def text(p: subprocess.CompletedProcess) -> tuple[str, str]:
    return p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def python_in_sandbox(job: str, code: str, *args: str) -> dict[str, str]:
    p = sandbox(job, "python3 -I - " + " ".join(args) + " > /tmp/aus.txt; cat /tmp/aus.txt", stdin=code.encode())
    out, _ = text(p)
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


def neuer_auftrag(art: str = "test") -> tuple[str, Path]:
    job = uuid.uuid4().hex[:16]
    jobdir = D.job_ordner(job)
    D.vorbereiten(jobdir, job, PROJEKT, art)
    D.lauf_vorbereiten(jobdir, PROJEKT)
    return job, jobdir


def entfernen(pfad: Path) -> None:
    def frei(func, p, _exc):
        os.chmod(p, stat.S_IRWXU)
        os.chmod(os.path.dirname(p), stat.S_IRWXU)
        func(p)
    if pfad.exists():
        for wurzel, ordner, _ in os.walk(pfad):
            for o in ordner:
                try:
                    os.chmod(os.path.join(wurzel, o), stat.S_IRWXU)
                except OSError:
                    pass
        shutil.rmtree(pfad, onerror=frei)


def einheiten(job: str) -> str:
    p = subprocess.run(["systemctl", "--user", "list-units", "--no-legend", "--plain", f"wamo-job-{job}-*"],
                       capture_output=True, text=True, env=D.basis_env())
    return p.stdout.strip()


# ---------------------------------------------------------------- Grenzen der Sandbox
def grenzen() -> None:
    job, jobdir = neuer_auftrag()
    vorher_heim = set(os.listdir(D.HEIM))
    vorher_projekte = set(os.listdir(D.PROJEKTE))

    out, err = text(sandbox(job, "/srv/wamo/.local/bin/claude --version"))
    pruefe("claude --version in der Sandbox", "Claude Code" in out, out.strip() or err[-200:])

    out, err = text(sandbox(job, "ls -la /etc/wamo; cat /etc/wamo/geheim.env; ls /srv/wamo/.ssh; ls /home /root; "
                                 "for p in /srv/wamo/.claude.json /srv/wamo/jobs /srv/wamo/.config /etc/docker /etc/ssh "
                                 "/opt/wamo/bin /usr/local/lib/wamo/bwrap /srv/wamo/.local/share/chrome-devtools-mcp "
                                 "/srv/wamo/.local/state; do test -e $p && echo SICHTBAR:$p; done; "
                                 "ls /srv/wamo/projekte; echo ENDE"))
    pruefe("unsichtbar: /etc/wamo, .ssh, /home, /root, echte Konfiguration, andere Aufträge, /etc außerhalb der "
           "Allowlist, Programme in /opt/wamo/bin, bwrap-Kopie",
           "ENDE" in out and "SICHTBAR" not in out and out.split("ENDE")[0].strip() == PROJEKT,
           " ".join(line for line in out.splitlines() if "SICHTBAR" in line) or "nichts sichtbar")

    out, _ = text(sandbox(job, "id; grep -E '^(CapEff|NoNewPrivs|Seccomp)' /proc/self/status; hostname"))
    pruefe("Benutzer wamo ohne Capabilities, no_new_privs, seccomp aktiv", "(wamo)" in out
           and "CapEff:\t0000000000000000" in out and "NoNewPrivs:\t1" in out and "Seccomp:\t2" in out,
           out.replace("\n", " | ")[:220])

    p = sandbox(job, "ps -e -o pid=,comm= | wc -l; ls /dev")
    out, _ = text(p)
    zeilen_ = out.splitlines()
    pruefe("eigener PID-Namensraum (wenige Prozesse)", bool(zeilen_) and zeilen_[0].strip().isdigit() and int(zeilen_[0]) < 10,
           f"{zeilen_[0] if zeilen_ else '?'} Prozesse")
    geraete = set(" ".join(zeilen_[1:]).split())
    pruefe("keine Gerätezugriffe (nur Standardgeräte, keine GPU)", p.returncode == 0 and "null" in geraete
           and not any(g.startswith(("nvidia", "nvme", "sd", "dri", "kvm")) for g in geraete), " ".join(sorted(geraete))[:200])

    befehl = ("set +e; touch " + str(PROJ) + "/schreibtest-ok && echo PROJEKT-OK; "
              "for z in /usr/x /etc/x /run/x /opt/wamo/waechter/x /opt/wamo/x /etc/claude-code/x /srv/wamo/.local/bin/x "
              "/srv/wamo/projekte/x; do touch $z 2>/dev/null && echo SCHREIBBAR:$z; done; "
              "echo '[]' > /run/wamo/zustand/approvals.json 2>/dev/null && echo SCHREIBBAR:approvals; "
              "umbenennen() { python3 -I -c 'import os,sys; os.rename(sys.argv[1], sys.argv[2])' \"$1\" \"$2\" 2>/dev/null; }; "
              "umbenennen /opt/wamo /opt/x && echo UMBENANNT:/opt/wamo; "
              "umbenennen /srv/wamo/projekte /srv/wamo/p && echo UMBENANNT:projekte; "
              f"umbenennen {PROJ} {PROJ}-x && echo UMBENANNT:projekt; "
              "ln -sfn / /run/wamo/projekt 2>/dev/null && echo UMBENANNT:verweis; "
              "touch /srv/wamo/nur-im-tmpfs; echo ENDE")
    out, _ = text(sandbox(job, befehl))
    pruefe("schreiben nur im Projektordner; Wächter, Projektpfade und Verweis nicht umbenennbar",
           "PROJEKT-OK" in out and "SCHREIBBAR" not in out and "UMBENANNT" not in out and "ENDE" in out,
           " ".join(line for line in out.splitlines() if "SCHREIBBAR" in line or "UMBENANNT" in line) or "nichts")
    neu_heim = set(os.listdir(D.HEIM)) ^ vorher_heim
    neu_projekte = set(os.listdir(D.PROJEKTE)) - vorher_projekte - {PROJEKT}
    approvals = json.loads((jobdir / "zustand" / "approvals.json").read_text())
    pruefe("nichts außerhalb des Projektordners auf der Platte gelandet",
           not neu_heim and not neu_projekte and (PROJ / "schreibtest-ok").is_file() and approvals == [],
           f"Home {sorted(neu_heim)}, Projekte {sorted(neu_projekte)}, approvals {approvals}")

    out, err = text(sandbox(job, "unshare -U true && echo NAMENSRAUM-OK; echo ENDE"))
    pruefe("keine weiteren Namensräume in der Sandbox", "ENDE" in out and "NAMENSRAUM-OK" not in out, err.strip()[-120:])

    out, _ = text(sandbox(job, "env | cut -d= -f1 | sort | tr '\\n' ' '"))
    namen = set(out.split())
    pruefe("Umgebung ohne Token und ohne Laufzeitordner",
           "HOME" in namen and not namen & {"XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "ANTHROPIC_API_KEY", "GH_TOKEN",
                                            "SSH_CONNECTION"}, " ".join(sorted(namen))[:250])

    s = python_in_sandbox(job, SECCOMPTEST)
    erwartet = {"unshare": "EPERM", "keyctl": "EPERM", "io_uring_setup": "EPERM", "bpf": "EPERM", "mount": "EPERM",
                "clone3": "ENOSYS", "af_packet": "EPERM", "af_alg": "EPERM", "mptcp": "EPERM", "sctp": "EPERM", "tcp": "ok"}
    pruefe("seccomp: MPTCP, SCTP, AF_PACKET, AF_ALG, io_uring, keyctl, bpf, mount, unshare gesperrt, clone3 ENOSYS",
           all(s.get(k) == v for k, v in erwartet.items()), str(s))

    bruecke = D.docker_bruecke() or "172.17.0.1"
    n = python_in_sandbox(job, NETZTEST, bruecke)
    zu = {"EHOSTUNREACH", "EACCES", "EPERM", "ENETUNREACH"}
    pruefe("Landlock: Dienste des Hosts (Postgres 55322, Ollama 11434) gesperrt",
           n.get("postgres") in ("EACCES", "EPERM") and n.get("ollama") in ("EACCES", "EPERM"), f"postgres={n.get('postgres')} ollama={n.get('ollama')}")
    pruefe("Ausgangsfilter und seccomp: Loopback, 10/8, Docker-Bridge, LAN, IPv6 sofort abgewiesen, auch über MPTCP",
           all(n.get(k) in zu or (n.get(k) or "").startswith("socket-E") for k in
               ("lo9", "lo9mptcp", "netz10", "netz10mptcp", "bruecke", "lan443", "ipv6")),
           " ".join(f"{k}={n.get(k)}" for k in ("lo9", "lo9mptcp", "netz10", "netz10mptcp", "bruecke", "lan443", "ipv6")))
    pruefe("TCP Fast Open umgeht die Portsperre nicht", n.get("tfo55322") in ("EPERM", "EACCES"), n.get("tfo55322", "?"))
    pruefe("abstrakter X11-Socket gesperrt", n.get("x11") in ("EPERM", "EACCES", "ECONNREFUSED"), n.get("x11", "?"))
    pruefe("Internet über 443 offen (github.com)", n.get("github") == "offen", n.get("github", "?"))
    pruefe("eigener Entwicklungsserver auf 127.0.0.1:20123 erreichbar", n.get("eigener20123") == "offen",
           n.get("eigener20123", "?"))
    pruefe("Landlock bind: 20000-20999 erlaubt, Port 80 und 0.0.0.0:55999 gesperrt, Port 0 frei (M3)",
           n.get("bind_dev") == "ok" and n.get("bind_ephemer") == "ok"
           and n.get("bind_80") in zu and n.get("bind_0.0.0.0:55999") in zu,
           " ".join(f"{k}={n.get(k)}" for k in ("bind_dev", "bind_0.0.0.0:55999", "bind_80", "bind_ephemer")))

    out, err = text(sandbox(job, f"cd {PROJ} && git clone -q --depth 1 https://github.com/octocat/Hello-World.git hallo && ls hallo"))
    pruefe("git clone über HTTPS im Projektordner", "README" in out, (err or out).strip()[-150:])

    start = time.monotonic()
    p = sandbox(job, "sleep 60", zeit=3)
    dauer = time.monotonic() - start
    pruefe("Zeitlimit greift (3 s)", p.returncode == 124 and dauer < 25, f"Exit {p.returncode} nach {dauer:.1f} s")

    hinter = subprocess.Popen([str(D.RUNNER), "--auftrag", job, "--projekt", PROJEKT, "--", "/bin/sleep", "120"],
                              env=D.basis_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(3)
    unit = einheiten(job).split()[0] if einheiten(job) else ""
    props = subprocess.run(["systemctl", "--user", "show", unit, "-p", "MemoryMax", "-p", "MemorySwapMax", "-p", "TasksMax",
                            "-p", "CPUQuotaPerSecUSec", "-p", "RuntimeMaxUSec"], capture_output=True, text=True,
                           env=D.basis_env()).stdout if unit else ""
    pruefe("Ressourcengrenzen der systemd-Einheit", "MemoryMax=8589934592" in props and "TasksMax=1024" in props
           and "CPUQuotaPerSecUSec=4s" in props and "MemorySwapMax=0" in props, props.replace("\n", " ")[:250])
    start = time.monotonic()
    subprocess.run([DISPATCH, "cancel", job], capture_output=True, env=D.basis_env())
    try:
        rc = hinter.wait(timeout=30)
    except subprocess.TimeoutExpired:
        hinter.kill()
        rc = None
    pruefe("cancel beendet den laufenden Auftrag", bool(unit) and rc is not None and rc != 0 and not einheiten(job),
           f"Exit {rc} nach {time.monotonic() - start:.1f} s")

    (PROJ / "bleibt.txt").write_text("Inhalt des Projekts")
    vorher = sorted(os.listdir(PROJ))
    ok, grund = D.selbsttest(job, PROJEKT)
    pruefe("Selbsttest des Dispatchers (Hook, Grenzen, Kanarienvögel)", ok, grund)
    pruefe("Selbsttest lässt den Projektinhalt unangetastet", sorted(os.listdir(PROJ)) == vorher
           and (PROJ / "bleibt.txt").read_text() == "Inhalt des Projekts", f"vorher {vorher}, nachher {sorted(os.listdir(PROJ))}")

    out, _ = text(sandbox(job, "/srv/wamo/.local/bin/claude mcp list 2>&1 | tail -n 6", timeout=240))
    pruefe("REA-MCP startet in der Sandbox (aus .npm-global, ohne Download)", "rea" in out and "Connected" in out,
           out.strip().replace("\n", " | ")[-300:])
    entfernen(jobdir)


def entschaerfen_und_buchfuehrung() -> None:
    """H3: Autostart-Flächen eines früheren Laufs verschwinden vor dem nächsten; mehr Zugänge brauchen eine Freigabe."""
    git_env = {**D.GIT_ENV, "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "init", "-q", str(PROJ)], env=git_env, check=True)
    subprocess.run(["git", "-C", str(PROJ), "remote", "add", "origin", "https://github.com/wamocon/beispiel.git"],
                   env=git_env, check=True)
    for key, value in (("core.fsmonitor", "touch /tmp/x"), ("alias.st", "!sh"), ("core.hooksPath", ".haken"),
                       ("url.https://boese.example/.insteadOf", "https://github.com/"), ("user.name", "Rauchtest")):
        subprocess.run(["git", "-C", str(PROJ), "config", key, value], env=git_env, check=True)
    (PROJ / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\ntouch /tmp/x\n")
    (PROJ / ".git" / "modules" / "sub" / "hooks").mkdir(parents=True)
    (PROJ / ".git" / "modules" / "sub" / "hooks" / "post-checkout").write_text("#!/bin/sh\n")
    (PROJ / ".git" / "modules" / "sub" / "config").write_text("[core]\n\tfsmonitor = touch /tmp/y\n")
    (PROJ / ".mcp.json").write_text("{}")
    (PROJ / ".claude" / "agents").mkdir(parents=True)
    (PROJ / ".claude" / "settings.json").write_text('{"hooks": {}}')
    (PROJ / ".claude" / "agents" / "evil.md").write_text("x")
    entfernt = D.projekt_entschaerfen(PROJ)
    config = subprocess.run(["git", "config", "--file", str(PROJ / ".git" / "config"), "--list"], env=git_env,
                            capture_output=True, text=True).stdout
    sub = subprocess.run(["git", "config", "--file", str(PROJ / ".git" / "modules" / "sub" / "config"), "--list"],
                         env=git_env, capture_output=True, text=True).stdout
    weg = [PROJ / ".mcp.json", PROJ / ".claude",
           PROJ / ".git" / "hooks" / "pre-commit", PROJ / ".git" / "modules" / "sub" / "hooks" / "post-checkout"]
    pruefe("Entschärfen: .mcp.json, ganzes .claude/ (settings, agents), Git-Hooks (auch Submodule) entfernt",
           not any(p.exists() for p in weg), "; ".join(entfernt)[:250])
    pruefe("Entschärfen: .git/config nur Allowlist (Remote bleibt, fsmonitor, alias, hooksPath, insteadOf weg)",
           "remote.origin.url=https://github.com/wamocon/beispiel.git" in config and "user.name=Rauchtest" in config
           and not any(k in config for k in ("fsmonitor", "alias.", "hookspath", "insteadof")) and "fsmonitor" not in sub,
           config.replace("\n", " ")[:250])

    # H2: vier Umgehungen des Git-Entschärfens, alle fail-closed
    for name in os.listdir(PROJ):
        entfernen(PROJ / name) if (PROJ / name).is_dir() else (PROJ / name).unlink()
    boese = "[core]\n\tfsmonitor = touch /tmp/x\n\tpager = touch /tmp/y\n"
    (PROJ / ".git" / "hooks").mkdir(parents=True)
    (PROJ / ".git" / "config").write_text(boese)
    os.rename(PROJ / ".git", PROJ / ".echtgit")
    (PROJ / ".git").write_text("gitdir: .echtgit\n")  # .git als Zeiger-Datei
    (PROJ / "sub" / ".git").mkdir(parents=True)        # eingebettetes Repo
    (PROJ / "sub" / ".git" / "config").write_text(boese)
    D.projekt_entschaerfen(PROJ)
    offen = (PROJ / ".git").exists() and "fsmonitor" in (PROJ / ".echtgit" / "config").read_text()
    sub_offen = "fsmonitor" in (PROJ / "sub" / ".git" / "config").read_text()
    pruefe("H2: .git-Zeiger entfernt, eingebettetes Repo entschärft",
           not (PROJ / ".git").exists() and not sub_offen, f"git-Zeiger da={offen}, sub offen={sub_offen}")
    for name in os.listdir(PROJ):
        entfernen(PROJ / name) if (PROJ / name).is_dir() else (PROJ / name).unlink()
    (PROJ / ".git").mkdir()
    (PROJ / ".git" / "config").symlink_to("../.evil.cfg")  # config als Link ins Projekt
    (PROJ / ".evil.cfg").write_text(boese)
    D.projekt_entschaerfen(PROJ)
    pruefe("H2: .git/config-Link ersetzt, Linkziel unangetastet",
           not (PROJ / ".git" / "config").is_symlink() and "fsmonitor" in (PROJ / ".evil.cfg").read_text(),
           "Link noch da" if (PROJ / ".git" / "config").is_symlink() else "ersetzt")
    for name in os.listdir(PROJ):
        entfernen(PROJ / name) if (PROJ / name).is_dir() else (PROJ / name).unlink()

    projekt_eintrag = D.JOBS / ".projekte" / f"{PROJEKT}.json"
    projekt_eintrag.unlink(missing_ok=True)
    (PROJ / "inhalt.txt").write_text("x")
    klein, gross = {"ANTHROPIC_API_KEY"}, {"ANTHROPIC_API_KEY", "GH_TOKEN"}
    a1 = D.zugang_pruefen(PROJEKT, klein, [])  # Projekt mit Inhalt, keine Buchführung: erst prüfen lassen
    a2 = D.zugang_pruefen(PROJEKT, klein, [a1["id"]] if a1 else [])
    a3 = D.zugang_pruefen(PROJEKT, gross, [])
    a4 = D.zugang_pruefen(PROJEKT, gross, [a3["id"]] if a3 else [])
    a5 = D.zugang_pruefen(PROJEKT, klein, [])
    a6 = D.zugang_pruefen(PROJEKT, gross, [])
    pruefe("Buchführung: mehr Zugänge als frühere Läufe brauchen eine Freigabe, weniger nicht",
           a1 is not None and a2 is None and a3 is not None and a3["input"]["neu"] == ["GH_TOKEN"] and a4 is None
           and a5 is None and a6 is not None, f"{bool(a1)} {a2} {a3 and a3['input']['neu']} {a4} {a5} {bool(a6)}")
    # H1: unlesbarer Projektordner gilt nicht als leer (sonst käme mehr Zugang ohne Freigabe)
    projekt_eintrag.write_text(json.dumps({"schnitt": ["ANTHROPIC_API_KEY"]}))
    os.chmod(PROJ, 0o100)
    try:
        a7 = D.zugang_pruefen(PROJEKT, gross, [])
    finally:
        os.chmod(PROJ, 0o750)
    pruefe("H1: chmod 100 macht das Projekt nicht 'leer', Freigabe bleibt nötig", a7 is not None, str(a7))
    projekt_eintrag.unlink(missing_ok=True)
    for name in os.listdir(PROJ):
        entfernen(PROJ / name) if (PROJ / name).is_dir() else (PROJ / name).unlink()


def mcp_mit_echter_umgebung() -> None:
    geheim = D.lese_geheimnisse()
    for art in ("datenbank", "umsetzung"):
        job, jobdir = neuer_auftrag(art)
        env = D.sandbox_env(art, geheim)
        if env is None:
            pruefe(f"claude mcp list für {art} (übersprungen: kein Claude-Zugang)", True)
            continue
        out, err = text(sandbox(job, "/srv/wamo/.local/bin/claude mcp list 2>&1 | grep -E ':' | tail -n 8", env=env,
                                timeout=300))
        zeilen_ = [line.strip() for line in out.splitlines() if " - " in line]
        status = {line.split(":", 1)[0]: ("Connected" in line) for line in zeilen_}
        # verbunden sein müssen REA und die Dienste, deren Token die Art bekommt; die anderen bleiben ohne Zugang
        erwartet = {"rea", "github"} | ({"supabase"} if "SUPABASE_ACCESS_TOKEN" in env else set())             | ({"jira"} if "JIRA_API_KEY" in env else set())
        ohne = {"supabase"} - erwartet
        pruefe(f"claude mcp list mit Umgebung der Art {art}: {', '.join(sorted(erwartet))} verbunden"
               + (f", {', '.join(sorted(ohne))} ohne Zugang" if ohne else ""),
               all(status.get(s) for s in erwartet) and not any(status.get(s) for s in ohne),
               " | ".join(f"{k}={'ok' if v else 'aus'}" for k, v in status.items()) or err[-200:])
        entfernen(jobdir)


def start_ohne_zugang() -> None:
    if any(D.lese_geheimnisse().get(n) for n in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")):
        pruefe("start ohne Claude-Zugang (übersprungen: Zugang ist eingetragen)", True)
        return
    job = uuid.uuid4().hex[:16]
    kopf = json.dumps({"auftrag": "Rauchtest", "system": "Rauchtest"}) + "\n"
    p = subprocess.Popen([DISPATCH, "start", job, PROJEKT, "test"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=D.basis_env())
    p.stdin.write(kopf.encode())
    p.stdin.flush()
    out, err = p.communicate(timeout=180)
    ereignisse = [json.loads(line).get("ereignis") for line in out.decode().splitlines() if line.strip()]
    pruefe("start: Selbsttest ok, dann sauber abgewiesen ohne Claude-Zugang (Code 5)",
           p.returncode == D.EXIT_KEIN_ZUGANG and ereignisse == ["selbsttest_ok", "keine_zugangsdaten"],
           f"Exit {p.returncode}, Ereignisse {ereignisse}")
    entfernen(D.JOBS / job)


# ---------------------------------------------------------------- Ende zu Ende mit Attrappe
def zweiter_start(job: str) -> tuple[int, list[str]]:
    """Startet denselben Auftrag über den Dispatcher ein zweites Mal (während der erste läuft)."""
    kopf = json.dumps({"auftrag": "zweiter Start", "system": "Rauchtest"}) + "\n"
    p = subprocess.run([DISPATCH, "start", job, PROJEKT, "umsetzung"], input=kopf.encode(),
                       capture_output=True, env=D.basis_env(), timeout=120)
    return p.returncode, [json.loads(line).get("ereignis") for line in p.stdout.decode().splitlines() if line.strip()]


def lauf_mit_attrappe(job: str, jobdir: Path, session: str | None = None, pause: int = 0,
                      lebenslinie_s: float | None = None, abbruch_s: float | None = None,
                      waehrend=None, fremde_hooks: bool = False) -> tuple[int, list[dict]]:
    """Startet _ausfuehren des Dispatchers in einem Kindprozess (stdin als Lebenslinie)."""
    D.lauf_vorbereiten(jobdir, PROJEKT)
    if fremde_hooks:  # nach dem Entschärfen gepflanzt: allowManagedHooksOnly muss sie wirkungslos machen
        haken = lambda marke: {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [  # noqa: E731
            {"type": "command", "command": f"touch {PROJ}/{marke}"}]}]}}
        (jobdir / "claude" / "settings.json").write_text(json.dumps(haken("hook-benutzer")))
        (PROJ / ".claude").mkdir(exist_ok=True)
        (PROJ / ".claude" / "settings.json").write_text(json.dumps(haken("hook-projekt")))
    resume = f" --resume {session}" if session else ""
    innen = (f"ATTRAPPE_PAUSE={pause} python3 -I {PROJ}/.rauchtest/attrappe.py {PORT_ATTRAPPE} </dev/null >/dev/null 2>&1 & "
             f"for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/{PORT_ATTRAPPE}) 2>/dev/null && break; sleep 0.1; done; "
             f"exec {D.CLAUDE} -p --output-format stream-json --verbose --permission-mode bypassPermissions "
             f"--settings '{D.PLUGIN_SETTINGS}' --append-system-prompt Rauchtest{resume}")
    spec = {"argv": [str(D.RUNNER), "--auftrag", job, "--projekt", PROJEKT, "--zeitlimit", "300", "--",
                     "/bin/bash", "-c", innen],
            "env": {**D.basis_env(), "ANTHROPIC_API_KEY": "rauchtest-attrappe",
                    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{PORT_ATTRAPPE}"},
            "job": job}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=jobdir) as f:
        json.dump(spec, f)
    with open(jobdir / "rauchtest-stderr.log", "ab") as fehlerlog:
        # stdin bleibt offen (Lebenslinie), bis der Lauf endet oder der Test sie absichtlich schließt
        kind = subprocess.Popen([sys.executable, "-I", __file__, "--ausfuehren", f.name], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=fehlerlog)
    puffer: list[bytes] = []
    leser = threading.Thread(target=lambda: puffer.append(kind.stdout.read()), daemon=True)
    leser.start()
    if waehrend is not None:
        time.sleep(5)
        waehrend()
    if lebenslinie_s is not None:
        time.sleep(lebenslinie_s)
        kind.stdin.close()
    if abbruch_s is not None:
        time.sleep(abbruch_s)
        subprocess.run([DISPATCH, "cancel", job], capture_output=True, env=D.basis_env())
    kind.wait(timeout=400)
    leser.join(timeout=10)
    if not kind.stdin.closed:
        kind.stdin.close()
    out = b"".join(puffer)
    os.unlink(f.name)
    zeilen_ = []
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            zeilen_.append(json.loads(line))
        except ValueError:
            pass
    return kind.returncode, zeilen_


def ende_zu_ende() -> None:
    job, jobdir = neuer_auftrag("umsetzung")
    (PROJ / ".rauchtest").mkdir(exist_ok=True)
    (PROJ / ".rauchtest" / "attrappe.py").write_text(ATTRAPPE, encoding="utf-8")
    log = PROJ / ".rauchtest" / "attrappe.log"
    log.unlink(missing_ok=True)

    rc, zeilen_ = lauf_mit_attrappe(job, jobdir, fremde_hooks=True)
    session = next((z.get("session_id") for z in zeilen_ if z.get("type") == "system"), None)
    eintraege = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    ergebnis1 = [e["tool_result"] for e in eintraege if "tool_result" in e]
    pruefe("Lauf 1: Claude Code fragt git push an, der Hook in der Sandbox verweigert (FREIGABE NÖTIG)",
           rc == 0 and bool(ergebnis1) and "FREIGABE N" in ergebnis1[-1], f"Exit {rc}, {ergebnis1[-1][:120] if ergebnis1 else 'kein tool_result'}")
    pruefe("nur verwaltete Hooks: Benutzer- und Projekt-Hook liefen nicht",
           not (PROJ / "hook-benutzer").exists() and not (PROJ / "hook-projekt").exists(),
           ", ".join(m for m in ("hook-benutzer", "hook-projekt") if (PROJ / m).exists()) or "keiner lief")
    p = subprocess.run([DISPATCH, "pending", job], capture_output=True, text=True, env=D.basis_env())
    anfragen = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
    pruefe("pending liefert die geprüfte Anfrage (Fingerabdruck 64 Zeichen)", len(anfragen) == 1
           and anfragen[0]["input"].get("command") == "git push origin main" and anfragen[0]["grund"] == "git push"
           and len(anfragen[0]["id"]) == 64, str(anfragen)[:200])
    if not anfragen or not session:
        return
    fp = anfragen[0]["id"]
    p = subprocess.run([DISPATCH, "approve", job, "0" * 64], capture_output=True, text=True, env=D.basis_env())
    pruefe("approve mit nie angefragtem Fingerabdruck wird abgewiesen", p.returncode == D.EXIT_ABGEWIESEN, p.stderr.strip())
    p = subprocess.run([DISPATCH, "approve", job, fp], capture_output=True, text=True, env=D.basis_env())
    pruefe("approve des angefragten Fingerabdrucks", p.returncode == 0, p.stdout.strip())

    log.unlink(missing_ok=True)
    rc, zeilen_ = lauf_mit_attrappe(job, jobdir, session=session)
    eintraege = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    ergebnis2 = [e["tool_result"] for e in eintraege if "tool_result" in e]
    # Freigabe über den Socket (M5): der Befehl lief (kein FREIGABE mehr); verbraucht wird im Dispatcher-Server,
    # nicht in approvals.json.used. Dass es GENAU einmal war, zeigt Lauf 3 (danach wieder verweigert).
    pruefe("Lauf 2 (--resume): freigegebener Befehl läuft durch (Socket-Reservierung)",
           rc == 0 and bool(ergebnis2) and "FREIGABE N" not in ergebnis2[-1],
           f"{' >> '.join(e[:110] for e in ergebnis2) if ergebnis2 else 'kein tool_result'}")
    out_ecc, _ = text(sandbox(job, "/srv/wamo/.local/bin/claude plugin list 2>&1 | tail -n 20", timeout=180))
    pruefe("ECC-Plugin geladen (Skills und Agenten der Werkzeugkarte, M1)", "ecc" in out_ecc.lower(),
           out_ecc.strip().replace("\n", " | ")[-200:])

    log.unlink(missing_ok=True)
    rc, zeilen_ = lauf_mit_attrappe(job, jobdir, session=session)
    eintraege = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    ergebnis3 = [e["tool_result"] for e in eintraege if "tool_result" in e]
    pruefe("Lauf 3: Freigabe verbraucht, wieder verweigert", bool(ergebnis3) and "FREIGABE N" in ergebnis3[-1],
           ergebnis3[-1][:100] if ergebnis3 else "kein tool_result")

    start = time.monotonic()
    rc, zeilen_ = lauf_mit_attrappe(job, jobdir, pause=200, lebenslinie_s=10)
    ende = next((z for z in zeilen_ if z.get("ereignis") == "ende"), {})
    pruefe("Lebenslinie: Verbindung weg, Auftrag endet sofort", ende.get("ergebnis") == "verlassen"
           and time.monotonic() - start < 40 and not einheiten(job), f"{ende}, {time.monotonic() - start:.1f} s")

    start = time.monotonic()
    zweiter: list = []
    rc, zeilen_ = lauf_mit_attrappe(job, jobdir, pause=200, abbruch_s=5,
                                    waehrend=lambda: zweiter.append(zweiter_start(job)))
    pruefe("zweiter Start desselben Auftrags während des Laufs: belegt (Code 4)",
           bool(zweiter) and zweiter[0] == (D.EXIT_BELEGT, ["belegt"]), str(zweiter))
    ende = next((z for z in zeilen_ if z.get("ereignis") == "ende"), {})
    pruefe("cancel während des Laufs beendet den Auftrag", ende.get("ergebnis") == "abgebrochen"
           and time.monotonic() - start < 40 and not einheiten(job), f"{ende}, {time.monotonic() - start:.1f} s")
    entfernen(jobdir)


def ausfuehren(spec_pfad: str) -> int:
    spec = json.loads(Path(spec_pfad).read_text())
    job = spec["job"]
    meta = json.loads((D.JOBS / job / "auftrag.json").read_text())
    sperre = D.Sperre(D.JOBS / job / ".lauf")  # wie cmd_start: ein Lauf je Auftrag
    if not sperre.versuchen():
        return D.EXIT_BELEGT
    return D._ausfuehren(spec["argv"], spec["env"], "Rauchtest: bitte git push", D.JOBS / job, job,
                         meta["projekt"], meta["art"])


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--ausfuehren":
        D.beenden(ausfuehren(sys.argv[2]))  # hartes Ende wie der Dispatcher (Lebenslinien-Thread)
    if os.getuid() == 0 or Path.home() != D.HEIM:
        print("Bitte als wamo ausführen: sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest", file=sys.stderr)
        return 1
    entfernen(PROJ)
    PROJ.mkdir(mode=0o750)
    (D.JOBS / ".projekte" / f"{PROJEKT}.json").unlink(missing_ok=True)
    try:
        grenzen()
        entschaerfen_und_buchfuehrung()
        start_ohne_zugang()
        if "--mcp" in sys.argv:
            mcp_mit_echter_umgebung()
        if "--attrappe" in sys.argv:
            ende_zu_ende()
    finally:
        entfernen(PROJ)
        (D.JOBS / ".projekte" / f"{PROJEKT}.json").unlink(missing_ok=True)
    fehler = [n for n, ok in ERGEBNISSE if not ok]
    print(f"\n{len(ERGEBNISSE) - len(fehler)} von {len(ERGEBNISSE)} Prüfungen bestanden.")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
