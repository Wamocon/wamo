#!/usr/bin/python3 -I
"""Rauchtest der WAMO-Auftrags-Sandbox auf Sokrates-2 (ohne Claude-Login).

Installiert als /opt/wamo/bin/wamo-job-rauchtest durch wamo-job-einrichten.sh. Ausführen als wamo:

    sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest              Grenzen der Sandbox, Netz, seccomp, Entschärfen
    sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --attrappe   zusätzlich Claude Code Ende zu Ende
    sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp        zusätzlich MCP-Server mit echter Umgebung
    sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --app-echt   zusätzlich die eingerichtete GitHub-App (echt, nur lesend)
    sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --token-attrappe   zusätzlich der persönliche Claude-Zugang (Attrappen)

--attrappe startet in der Sandbox eine Attrappe der Messages-API (127.0.0.1:20180, nur Standardbibliothek) und
lässt Claude Code mit einem Dummy-Schlüssel dagegen laufen. Die Attrappe verlangt den Befehl
"git push origin main"; geprüft wird, dass der Wächter-Hook aus managed-settings.json in der Sandbox greift,
nur der Wächter als Hook läuft (keine ECC-Hooks, keine Benutzer- und Projekt-Hooks), ECC-Skills und
-Agenten da sind, das Modell der Auftragsart gilt, die Anfrage über
"pending" sichtbar wird, eine Freigabe genau einmal wirkt, die Lebenslinie (stdin) und "cancel" den Lauf beenden.
Zusätzlich prüft --attrappe den App-Modus für GitHub über den echten Einstiegspunkt (wamo-job-dispatch start) mit
einer Attrappe der GitHub-API im Dispatcher-Prozess und einem Testschlüssel, ohne echte GitHub-Aufrufe: JWT,
Rechte je Art, Repo-Begrenzung, Token als GH_TOKEN in der Sandbox, Widerruf am Ende, bei cancel, Lebenslinie und
SIGTERM, fail-closed bei Fehlern und kaputter Konfiguration, Rückfall ohne Konfiguration, kein Schlüssel und kein
Token in Audit-Log und argv. Statt Claude Code läuft dabei ein kurzes Prüfskript in der Sandbox.
--token-attrappe prüft die Übergabe des persönlichen Claude-Zugangs über den echten Einstiegspunkt (wamo-job-dispatch start)
mit erfundenen Werten und ohne echte Aufrufe bei Anthropic: zweite stdin-Zeile, strenges Format (fail-closed), einziger
Claude-Zugang der Sandbox (nichts aus geheim.env beigemischt), Modell nach Zugangsart (Abo Sonnet, API-Schlüssel Haiku, Wunsch
aus der Allowlist), Schwärzung in Ausgabe, Protokoll und Dateien unter claude/, kein Wert in Audit, Auftragsordner und
Prozessliste, Wiederholung nach approve, WAMO_SERVERZUGANG=aus sowie echtes Claude Code gegen die API-Attrappe, die
Limit-Kopfzeilen (anthropic-ratelimit-unified-*) schickt: daraus muss das Ereignis kontingent entstehen.
--mcp startet "claude mcp list" in der Sandbox mit der Umgebung der Auftragsarten datenbank und umsetzung
(echte Token aus /etc/wamo/geheim.env, im App-Modus ein echtes, nur lesendes und danach widerrufenes App-Token;
ausgegeben wird nur der Verbindungsstatus).
--app-echt prüft die eingerichtete App bei GitHub, ausschließlich lesend und nur, wenn WAMO_GH_APP_* gesetzt sind:
Rechte der Installation (nur Contents und Pull requests schreibend, Metadata lesend, sonst nichts), Auswahl der Repos
("Only select repositories"), ein lesendes Token für die Installation (listet Repos), ein lesendes Token nur für ein
Repo (sieht genau dieses, liest es und seine Pull Requests, ein anderes privates Repo bleibt unsichtbar), Widerruf
und Ungültigkeit beider Token. Schreibende Token werden nie erzeugt. Der Name der App wird nie ausgegeben.
Projekt rauchtest-sandbox und die Auftragsordner werden danach entfernt.
"""

from __future__ import annotations

import importlib.machinery
import json
import os
import shlex
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
LIMITS = os.environ.get("ATTRAPPE_LIMITS") == "1"   # Kopfzeilen anthropic-ratelimit-unified-* wie bei einem Abo-Zugang
EINMAL = os.environ.get("ATTRAPPE_EINMAL") == "1"   # nur Text antworten, nie ein Werkzeug anfordern
COMMAND = "git push origin main"
WIEDERHOLT = []

def log(obj):
    with open(LOG, "a") as f:
        f.write(json.dumps(obj) + "\n")

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass
    def _limits(self):
        if not LIMITS:
            return
        jetzt = int(time.time())
        for name, wert in (("status", "allowed"), ("5h-utilization", "0.42"), ("5h-reset", jetzt + 3 * 3600),
                           ("7d-utilization", "0.13"), ("7d-reset", jetzt + 4 * 86400),
                           ("7d_oi-utilization", "0.07"), ("7d_oi-reset", jetzt + 5 * 86400),
                           ("representative-claim", "five_hour"), ("reset", jetzt + 3 * 3600)):
            self.send_header("anthropic-ratelimit-unified-" + name, str(wert))
    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self._limits()
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
        if EINMAL:
            blocks, stop = [{"type": "text", "text": "fertig"}], "end_turn"
        elif "Bash" in tools:
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
        self._limits()
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
    try:
        app = D.github_app_konfig(geheim)
    except D.GithubAppFehler as e:
        pruefe("claude mcp list (GitHub-App-Konfiguration kaputt)", False, str(e))
        return
    for art in ("datenbank", "umsetzung"):
        job, jobdir = neuer_auftrag(art)
        if D.sandbox_env(art, geheim, app_modus=app is not None) is None:
            pruefe(f"claude mcp list für {art} (übersprungen: kein Claude-Zugang)", True)
            continue
        gh_token = None
        try:
            if app is not None:  # im App-Modus ein echtes Token, aber nur lesend (der Test schreibt nichts), danach widerrufen
                gh_token, _ablauf = D.installations_token(app, "review", None)
            env = D.sandbox_env(art, geheim, app_modus=app is not None, gh_token=gh_token)
            out, err = text(sandbox(job, "/srv/wamo/.local/bin/claude mcp list 2>&1 | grep -E ':' | tail -n 8",
                                    env=env, timeout=300))
        except D.GithubAppFehler as e:
            pruefe(f"claude mcp list für {art}: GitHub-App-Token", False, str(e))
            entfernen(jobdir)
            continue
        finally:
            if gh_token is not None:
                D.token_widerrufen(gh_token)
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
    art = json.loads((jobdir / "auftrag.json").read_text())["art"]
    claude = shlex.join(D.claude_argv("Rauchtest", D.werkzeugkarte().modell_fuer(art), session))  # wie der Dispatcher
    innen = (f"ATTRAPPE_PAUSE={pause} python3 -I {PROJ}/.rauchtest/attrappe.py {PORT_ATTRAPPE} </dev/null >/dev/null 2>&1 & "
             f"for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/{PORT_ATTRAPPE}) 2>/dev/null && break; sleep 0.1; done; "
             f"exec {claude}")
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
    init = next((z for z in zeilen_ if z.get("type") == "system" and z.get("subtype") == "init"), {})
    skills, agenten = set(init.get("skills") or []), set(init.get("agents") or [])
    ecc = next((p for p in init.get("plugins") or [] if isinstance(p, dict) and p.get("name") == "ecc"), {})
    pruefe("ECC-Skills und -Agenten verfügbar (ecc:verification-loop, ecc:code-reviewer), aus der Kopie ohne Hooks",
           "ecc:verification-loop" in skills and "ecc:code-reviewer" in agenten
           and str(ecc.get("path", "")).endswith("/cache/ecc/ecc/ohne-hooks"),
           f"{len(skills)} Skills, {len(agenten)} Agenten, Plugin-Pfad {ecc.get('path', '?')}")
    hooks = [z for z in zeilen_ if z.get("type") == "system" and str(z.get("subtype", "")).startswith("hook_")]
    gate = [e for e in ergebnis1 if "Fact-Forcing Gate" in e]
    pruefe("kein ECC-Hook läuft (kein SessionStart-Hook, kein Fact-Forcing Gate)", not hooks and not gate,
           ", ".join(sorted({str(h.get("hook_name")) for h in hooks})) or ("Gate" if gate else "keiner"))
    pruefe("Modell der Auftragsart (Standard claude-haiku-5-5)", init.get("model") == "claude-haiku-5-5",
           str(init.get("model")))
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


# ---------------------------------------------------------------- GitHub-App mit Attrappe (ohne echte Aufrufe)
GH_PRUEFSKRIPT = r'''
h=$(printf %s "${GH_TOKEN:-}" | sha256sum | cut -c1-64)
s=nein; [ -e "$1" ] && s=ja
w=nein; [ -e /etc/wamo ] && w=ja
v=$(env | grep -c '^WAMO_GH_APP' || true)
printf '{"type":"rauchtest","gh":"%s","schluessel_sichtbar":"%s","etc_wamo":"%s","app_variablen":"%s"}\n' "$h" "$s" "$w" "$v"
sleep "$2"
'''


def _jwt_pruefen(jwt: str, oeffentlich, app_id: str) -> bool:
    """Prüft ein JWT der Attrappe: RS256-Signatur, iss, iat eine Minute zurück, exp höchstens 9 Minuten voraus."""
    import base64
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64(teil: str) -> bytes:
        return base64.urlsafe_b64decode(teil + "=" * (-len(teil) % 4))
    try:
        kopf, inhalt, signatur = jwt.split(".")
        oeffentlich.verify(b64(signatur), f"{kopf}.{inhalt}".encode(), padding.PKCS1v15(), hashes.SHA256())
        k, c = json.loads(b64(kopf)), json.loads(b64(inhalt))
    except (ValueError, InvalidSignature):
        return False
    jetzt = time.time()
    return (k == {"alg": "RS256", "typ": "JWT"} and c.get("iss") == app_id and c["iat"] <= jetzt - 55
            and jetzt < c["exp"] <= jetzt + 9 * 60 + 5 and c["exp"] - c["iat"] <= 600)


def _github_attrappe(spec: dict):
    """Ersetzt D._https im Dispatcher-Kindprozess. Protokolliert nur Prüfergebnisse, nie Token oder JWT."""
    from cryptography.hazmat.primitives import serialization
    oeffentlich = serialization.load_pem_public_key(Path(spec["oeffentlich"]).read_bytes())

    def notieren(eintrag: dict) -> None:
        with open(spec["protokoll"], "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag) + "\n")

    def https(methode, pfad, kopf, koerper=None):
        anmeldung = kopf.get("Authorization", "")
        if methode == "POST":
            body = json.loads(koerper)
            notieren({"methode": methode, "pfad": pfad, "koerper": body, "version": kopf.get("X-GitHub-Api-Version"),
                      "jwt_ok": _jwt_pruefen(anmeldung.removeprefix("Bearer "), oeffentlich, spec["geheim"].get("WAMO_GH_APP_ID", ""))})
            if spec.get("github_status", 201) != 201:
                return spec["github_status"], b'{"message": "Attrappe: abgelehnt"}'
            return 201, json.dumps({"token": spec["token"], "expires_at": "2026-10-21T23:59:00Z",
                                    "permissions": body["permissions"], "repository_selection": "selected",
                                    "repositories": [{"full_name": f"wamocon/{n}"} for n in body.get("repositories", [])]
                                    }).encode()
        if methode == "DELETE":
            notieren({"methode": methode, "pfad": pfad, "token_ok": anmeldung == f"Bearer {spec['token']}"})
            return 204, b""
        if methode == "GET" and pfad.startswith("/installation/repositories"):  # Prüfung auf das WAMO-Repo ohne Ziel-Repo
            return 200, json.dumps({"total_count": 1, "repositories": [{"full_name": "wamocon/rauchtest"}]}).encode()
        return 404, b"{}"
    return https


def start_attrappe(spec_pfad: str) -> int:
    """Kindprozess: der echte Dispatcher (main/cmd_start) mit Attrappen für geheim.env, GitHub-API und Claude Code."""
    spec = json.loads(Path(spec_pfad).read_text())
    D.SCHLUESSEL_ORDNER = Path(spec["ordner"])
    D.SCHLUESSEL_BESITZER = (0, os.getuid())  # Testschlüssel gehört wamo statt root
    D.lese_geheimnisse = lambda path=None: dict(spec["geheim"])
    D._https = _github_attrappe(spec)
    D.claude_argv = lambda system, modell, session=None: ["/bin/bash", "-c", GH_PRUEFSKRIPT, "pruefung",
                                                          spec["schluessel"], str(spec["schlaf"])]
    return D.main(["start", spec["job"], PROJEKT, spec["art"]])


def github_app_mit_attrappe() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import hashlib
    import signal
    ordner = Path(tempfile.mkdtemp(prefix=".rauchtest-gh-", dir=D.JOBS))  # wamo-eigen, in der Sandbox unsichtbar
    os.chmod(ordner, 0o750)
    schluessel = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = schluessel.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption())
    (ordner / "gh-app.pem").write_bytes(pem)
    os.chmod(ordner / "gh-app.pem", 0o640)
    (ordner / "gh-app.pub").write_bytes(schluessel.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    token = "ghs_rauchtest" + uuid.uuid4().hex
    persoenlich = "ghp_rauchtestpersoenlich" + uuid.uuid4().hex[:12]
    pem_zeile = pem.decode().splitlines()[3]
    app = {"WAMO_GH_APP_ID": "424242", "WAMO_GH_APP_INSTALLATION_ID": "4711",
           "WAMO_GH_APP_SCHLUESSEL": f"{ordner}/gh-app.pem"}
    basis = {"ANTHROPIC_API_KEY": "rauchtest-attrappe", "GH_TOKEN": persoenlich, "GH_TOKEN_LESEN": persoenlich + "l"}
    jobs: list[str] = []

    def lauf(art: str, geheim: dict, repo: str | None = None, schlaf: int = 0, cancel_s: float | None = None,
             lebenslinie_s: float | None = None, sigterm_s: float | None = None, waehrend=None, status: int = 201):
        job = uuid.uuid4().hex[:16]
        jobs.append(job)
        for name in os.listdir(PROJ):  # leerer Projektordner: die Buchführung verlangt keine Freigabe
            entfernen(PROJ / name) if (PROJ / name).is_dir() else (PROJ / name).unlink()
        protokoll = ordner / f"github-{job}.jsonl"
        spec = {"job": job, "art": art, "ordner": str(ordner), "schluessel": str(ordner / "gh-app.pem"),
                "oeffentlich": str(ordner / "gh-app.pub"), "protokoll": str(protokoll), "token": token,
                "geheim": geheim, "schlaf": schlaf, "github_status": status}
        spec_pfad = ordner / f"spec-{job}.json"
        spec_pfad.write_text(json.dumps(spec))
        kopf = {"auftrag": "Rauchtest GitHub-App", "system": "Rauchtest"} | ({"repo": repo} if repo else {})
        with open(ordner / "kind-stderr.log", "ab") as fehlerlog:
            kind = subprocess.Popen([sys.executable, "-I", __file__, "--start-attrappe", str(spec_pfad)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fehlerlog, env=D.basis_env())
        kind.stdin.write((json.dumps(kopf) + "\n").encode())
        kind.stdin.flush()
        puffer: list[bytes] = []
        laeuft = threading.Event()  # das Prüfskript in der Sandbox hat sich gemeldet: das Token lebt

        def lesen():
            for zeile in kind.stdout:
                puffer.append(zeile)
                if b'"rauchtest"' in zeile:
                    laeuft.set()
        leser = threading.Thread(target=lesen, daemon=True)
        leser.start()
        for wartezeit, aktion in ((cancel_s, lambda: subprocess.run([DISPATCH, "cancel", job], capture_output=True,
                                                                    env=D.basis_env())),
                                  (lebenslinie_s, kind.stdin.close), (sigterm_s, lambda: kind.send_signal(signal.SIGTERM))):
            if wartezeit is not None:
                laeuft.wait(timeout=180)  # erst nach Selbsttest und Start der Sandbox eingreifen
                time.sleep(wartezeit)
                if waehrend is not None:
                    waehrend()
                aktion()
        try:
            kind.wait(timeout=300)
        except subprocess.TimeoutExpired:
            kind.kill()
        leser.join(timeout=10)
        if not kind.stdin.closed:
            kind.stdin.close()
        zeilen_ = []
        for line in b"".join(puffer).decode("utf-8", "replace").splitlines():
            try:
                zeilen_.append(json.loads(line))
            except ValueError:
                pass
        eintraege = [json.loads(z) for z in protokoll.read_text().splitlines()] if protokoll.exists() else []
        return kind.returncode, zeilen_, eintraege, job

    def ereignisse(zeilen_):
        return [z.get("ereignis") for z in zeilen_ if z.get("type") == "wamo"]

    def pruefung(zeilen_):
        return next((z for z in zeilen_ if z.get("type") == "rauchtest"), {})

    gh_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        # 1. schreibende Art mit Ziel-Repo: Token nur für das Repo, in der Sandbox als GH_TOKEN, am Ende widerrufen
        rc, z, e, job = lauf("umsetzung", basis | app, repo="wamocon/rauchtest")
        post = next((x for x in e if x["methode"] == "POST"), {})
        weg = [x for x in e if x["methode"] == "DELETE"]
        innen = pruefung(z)
        pruefe("GitHub-App (Attrappe): JWT RS256 gültig, Rechte der Art (schreiben), nur das Ziel-Repo",
               post.get("jwt_ok") is True and post.get("pfad") == "/app/installations/4711/access_tokens"
               and post.get("koerper") == {"permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"},
                                           "repositories": ["rauchtest"]} and post.get("version") == "2022-11-28",
               json.dumps({k: post.get(k) for k in ("jwt_ok", "pfad", "koerper")}))
        pruefe("GitHub-App (Attrappe): Token als GH_TOKEN in der Sandbox; Schlüssel, /etc/wamo und WAMO_GH_APP_* unsichtbar",
               innen.get("gh") == gh_hash and innen.get("schluessel_sichtbar") == "nein" and innen.get("etc_wamo") == "nein"
               and innen.get("app_variablen") == "0", json.dumps({**innen, "gh": "passt" if innen.get("gh") == gh_hash else "falsch"}))
        pruefe("GitHub-App (Attrappe): Lauf fertig, Token danach genau einmal widerrufen",
               rc == 0 and ereignisse(z)[-1:] == ["ende"] and len(weg) == 1 and weg[0].get("token_ok") is True,
               f"Exit {rc}, Ereignisse {ereignisse(z)}, Widerrufe {len(weg)}")

        # 2. lesende Art ohne Repo, Abbruch per cancel; argv aller Prozesse von wamo ohne Token und Schlüssel
        argv_funde: list[str] = []

        def argv_pruefen():
            for pid in os.listdir("/proc"):
                if pid.isdigit():
                    try:
                        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
                    except OSError:
                        continue
                    if token in cmd or pem_zeile in cmd or persoenlich in cmd:
                        argv_funde.append(pid)
        rc, z, e, job = lauf("review", basis | app, schlaf=120, cancel_s=2, waehrend=argv_pruefen)
        post = next((x for x in e if x["methode"] == "POST"), {})
        ende = next((x for x in z if x.get("ereignis") == "ende"), {})
        pruefe("GitHub-App (Attrappe): lesende Art ohne Repo: nur Leserechte, Token für die Installation",
               post.get("koerper") == {"permissions": {"contents": "read", "metadata": "read", "pull_requests": "read"}},
               json.dumps(post.get("koerper")))
        pruefe("GitHub-App (Attrappe): cancel beendet den Lauf, Token widerrufen; kein Token und Schlüssel in argv",
               ende.get("ergebnis") == "abgebrochen" and [x["methode"] for x in e].count("DELETE") == 1
               and not argv_funde and not einheiten(job), f"{ende}, Widerrufe {[x['methode'] for x in e].count('DELETE')}, "
               f"argv-Funde {argv_funde}")

        # 3. Lebenslinie reißt ab
        rc, z, e, job = lauf("umsetzung", basis | app, schlaf=120, lebenslinie_s=2)
        ende = next((x for x in z if x.get("ereignis") == "ende"), {})
        pruefe("GitHub-App (Attrappe): Lebenslinie weg, Lauf endet, Token widerrufen",
               ende.get("ergebnis") == "verlassen" and [x["methode"] for x in e].count("DELETE") == 1 and not einheiten(job),
               f"{ende}, Widerrufe {[x['methode'] for x in e].count('DELETE')}")

        # 4. SIGTERM an den Dispatcher: Sandbox gestoppt, Token widerrufen
        rc, z, e, job = lauf("umsetzung", basis | app, schlaf=120, sigterm_s=2)
        pruefe("GitHub-App (Attrappe): SIGTERM an den Dispatcher stoppt die Sandbox und widerruft das Token",
               rc == D.EXIT_ABGEBROCHEN and [x["methode"] for x in e].count("DELETE") == 1 and not einheiten(job),
               f"Exit {rc}, Widerrufe {[x['methode'] for x in e].count('DELETE')}, Einheiten {einheiten(job) or 'keine'}")

        # 5. GitHub lehnt ab: fail-closed, nichts startet, kein Rückfall auf den persönlichen Token
        rc, z, e, job = lauf("umsetzung", basis | app, status=401)
        pruefe("GitHub-App (Attrappe): GitHub lehnt ab -> Start abgelehnt (Code 8), nichts gestartet",
               rc == D.EXIT_GITHUB_APP and ereignisse(z) == ["selbsttest_ok", "start_abgelehnt"] and not pruefung(z),
               f"Exit {rc}, Ereignisse {ereignisse(z)}")

        # 6. kaputte Konfiguration (Installation-ID fehlt): fail-closed ohne Aufruf bei GitHub
        rc, z, e, job = lauf("umsetzung", basis | {k: v for k, v in app.items() if k != "WAMO_GH_APP_INSTALLATION_ID"})
        pruefe("GitHub-App (Attrappe): unvollständige Konfiguration -> Start abgelehnt, kein Aufruf bei GitHub",
               rc == D.EXIT_GITHUB_APP and ereignisse(z) == ["selbsttest_ok", "start_abgelehnt"] and not e,
               f"Exit {rc}, Ereignisse {ereignisse(z)}, Aufrufe {len(e)}")

        # 7. ohne Konfiguration: Rückfall auf GH_TOKEN wie bisher
        rc, z, e, job = lauf("umsetzung", basis)
        innen = pruefung(z)
        pruefe("GitHub-App (Attrappe): ohne WAMO_GH_APP_* Rückfall auf GH_TOKEN, kein Aufruf bei GitHub",
               rc == 0 and innen.get("gh") == hashlib.sha256(persoenlich.encode()).hexdigest() and not e,
               f"Exit {rc}, Aufrufe {len(e)}")

        audit = (D.LOGS / "auftraege.log").read_text(encoding="utf-8", errors="replace")[-200_000:]
        pruefe("GitHub-App (Attrappe): Audit nennt Repo, Stufe und Widerruf, nie Token oder Schlüssel",
               "repo=wamocon/rauchtest stufe=schreiben" in audit and "gh_app_widerruf" in audit
               and token not in audit and pem_zeile not in audit and "PRIVATE KEY" not in audit and persoenlich not in audit,
               "geprüft in /srv/wamo/logs/auftraege.log")
    finally:
        for job in jobs:
            entfernen(D.JOBS / job)
        entfernen(ordner)


# ---------------------------------------------------------------- Persönlicher Claude-Zugang mit Attrappe
# Prüfskript statt Claude Code: meldet nur Hashes des Zugangs, gibt den Wert absichtlich in Ausgabe, Datei und stderr aus
# (der Dispatcher muss ihn schwärzen) und wartet, damit der Test die Prozessliste lesen kann.
TOKEN_PRUEFSKRIPT = r'''
t="${CLAUDE_CODE_OAUTH_TOKEN:-}"; a="${ANTHROPIC_API_KEY:-}"
ho=$(printf %s "$t" | sha256sum | cut -c1-64); ha=$(printf %s "$a" | sha256sum | cut -c1-64)
hh=$(printf %s "${ANTHROPIC_CUSTOM_HEADERS:-}" | sha256sum | cut -c1-64)
w=nein; [ -e /etc/wamo ] && w=ja
printf '{"type":"rauchtest","oauth":"%s","api":"%s","header":"%s","modell":"%s","etc_wamo":"%s"}\n' "$ho" "$ha" "$hh" "$1" "$w"
printf '{"type":"assistant","text":"Wert: %s und %s"}\n' "$t" "$a"
mkdir -p /srv/wamo/.claude/projects/x && printf '%s %s' "$t" "$a" > /srv/wamo/.claude/projects/x/s.jsonl
printf 'Fehlerausgabe %s %s\n' "$t" "$a" >&2
sleep "$2"
'''
SERVER_GEHEIM = {"ANTHROPIC_API_KEY": "sk-ant-api03-RauchtestServerSchluessel0123456789",
                 "ANTHROPIC_WORKSPACE_ID": "wrkspc_ServerWork01", "GH_TOKEN": "ghp_rauchtestserver0123456789"}


def start_token(spec_pfad: str) -> int:
    """Kindprozess: der echte Dispatcher (main/cmd_start) mit Attrappen für geheim.env und das Programm in der Sandbox.
    Modus pruef: das Prüfskript. Modus claude: echtes Claude Code gegen die API-Attrappe (Modell wie vom Dispatcher
    gewählt). Der persönliche Zugang kommt wie im Betrieb über stdin, nie über die Spezifikation."""
    spec = json.loads(Path(spec_pfad).read_text())
    D.lese_geheimnisse = lambda path=None: dict(spec["geheim"])
    if spec["modus"] == "pruef":
        D.claude_argv = lambda system, modell, session=None: ["/bin/bash", "-c", TOKEN_PRUEFSKRIPT, "pruefung", modell,
                                                              str(spec["schlaf"])]
    else:
        echt = D.claude_argv

        def mit_attrappe(system, modell, session=None):
            claude = shlex.join(echt(system, modell, session))
            innen = (f"ATTRAPPE_LIMITS={int(spec['limits'])} ATTRAPPE_EINMAL=1 python3 -I /run/wamo/zustand/attrappe.py "
                     f"{PORT_ATTRAPPE} </dev/null >/dev/null 2>&1 & "
                     f"for i in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/{PORT_ATTRAPPE}) 2>/dev/null && break; sleep 0.1; done; "
                     f"exec {claude}")
            return ["/bin/bash", "-c", innen]
        D.claude_argv = mit_attrappe
        ohne_basis = D.sandbox_env

        def sandbox_env(*args, **kwargs):
            env = ohne_basis(*args, **kwargs)
            return None if env is None else {**env, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{PORT_ATTRAPPE}"}
        D.sandbox_env = sandbox_env
    return D.main(["start", spec["job"], PROJEKT, spec["art"]])


def token_attrappe() -> None:
    import hashlib
    ordner = Path(tempfile.mkdtemp(prefix=".rauchtest-tok-", dir=D.JOBS))  # wamo-eigen, in der Sandbox unsichtbar
    os.chmod(ordner, 0o750)
    jobs: list[str] = []
    stempel = uuid.uuid4().hex
    oauth = "sk-ant-oat01-Rauchtest" + stempel          # Format wie von `claude setup-token`, aber erfunden
    api = "sk-ant-api03-Rauchtest" + stempel[::-1]
    workspace = "wrkspc_Rauch01"

    def sha(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def lauf(art="umsetzung", geheim=None, kopf_extra=None, zeile=None, modus="pruef", schlaf=0, limits=False,
             job=None, waehrend=None):
        job = job or uuid.uuid4().hex[:16]
        if job not in jobs:
            jobs.append(job)
        jobdir = D.job_ordner(job)
        (jobdir / "zustand").mkdir(mode=0o700, exist_ok=True)
        if modus == "claude":
            (jobdir / "zustand" / "attrappe.py").write_text(ATTRAPPE, encoding="utf-8")
            (jobdir / "zustand" / "attrappe.log").unlink(missing_ok=True)
        for name in os.listdir(PROJ):  # leerer Projektordner: die Buchführung verlangt keine Freigabe
            entfernen(PROJ / name) if (PROJ / name).is_dir() else (PROJ / name).unlink()
        spec_pfad = ordner / f"spec-{uuid.uuid4().hex[:8]}.json"
        spec_pfad.write_text(json.dumps({"job": job, "art": art, "geheim": SERVER_GEHEIM if geheim is None else geheim,
                                         "modus": modus, "schlaf": schlaf, "limits": limits}))
        with open(ordner / "kind-stderr.log", "ab") as fehlerlog:
            kind = subprocess.Popen([sys.executable, "-I", __file__, "--start-token", str(spec_pfad)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=fehlerlog, env=D.basis_env())
        kopf = {"auftrag": "Rauchtest persönlicher Zugang", "system": "Rauchtest", **(kopf_extra or {})}
        kind.stdin.write((json.dumps(kopf) + "\n").encode())
        if zeile is not None:
            kind.stdin.write(zeile)
        kind.stdin.flush()
        if zeile is None and (kopf_extra or {}).get("zugang") == "folgt":
            kind.stdin.close()  # die angekündigte Zugangszeile fehlt: sofort Ende statt 30 Sekunden Wartezeit
        puffer: list[bytes] = []
        laeuft = threading.Event()  # das Prüfskript in der Sandbox hat sich gemeldet

        def lesen():
            for z in kind.stdout:
                puffer.append(z)
                if b'"rauchtest"' in z:
                    laeuft.set()
        leser = threading.Thread(target=lesen, daemon=True)
        leser.start()
        if waehrend is not None:
            laeuft.wait(timeout=180)
            waehrend()
        try:
            kind.wait(timeout=400)
        except subprocess.TimeoutExpired:
            kind.kill()
        leser.join(timeout=10)
        if not kind.stdin.closed:
            try:
                kind.stdin.close()
            except OSError:
                pass
        text_ = b"".join(puffer).decode("utf-8", "replace")
        zeilen_ = []
        for z in text_.splitlines():
            try:
                zeilen_.append(json.loads(z))
            except ValueError:
                pass
        return kind.returncode, zeilen_, text_, jobdir, job

    def ereignisse(zeilen_):
        return [z.get("ereignis") for z in zeilen_ if z.get("type") == "wamo"]

    def pruefung(zeilen_):
        return next((z for z in zeilen_ if z.get("type") == "rauchtest"), {})

    def init(zeilen_):
        return next((z for z in zeilen_ if z.get("type") == "system" and z.get("subtype") == "init"), {})

    def fundstellen(wert: str, jobdir: Path | None, *texte: str) -> list[str]:
        """Wo der Wert noch steht: in den Texten, im Audit, in der stderr-Datei des Kindes und in jeder Datei unter dem
        Auftragsordner (ohne Links zu folgen)."""
        funde = [f"text{i}" for i, t in enumerate(texte) if wert in t]
        for pfad in (D.LOGS / "auftraege.log", ordner / "kind-stderr.log"):
            if pfad.is_file() and wert in pfad.read_bytes().decode("utf-8", "replace"):
                funde.append(pfad.name)
        for wurzel, _ordner, dateien in os.walk(jobdir if jobdir else ordner, followlinks=False):
            for name in dateien:
                p = Path(wurzel) / name
                if p.is_symlink() or not p.is_file():
                    continue
                try:
                    if wert.encode() in p.read_bytes():
                        funde.append(str(p.relative_to(jobdir if jobdir else ordner)))
                except OSError:
                    pass
        return funde

    try:
        # 1. Abo-Zugang (oauth) mit Prüfskript: Umgebung, einziger Claude-Zugang, Schwärzung, Prozessliste
        argv_funde: list[str] = []

        def argv_pruefen():
            for pid in os.listdir("/proc"):
                if pid.isdigit():
                    try:
                        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
                    except OSError:
                        continue
                    if oauth in cmd:
                        argv_funde.append(pid)
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d"},
                                        zeile=(json.dumps({"typ": "oauth", "wert": oauth}) + "\n").encode(),
                                        schlaf=4, waehrend=argv_pruefen)
        innen = pruefung(z)
        pruefe("Persönlicher Zugang (oauth): einziger Claude-Zugang in der Sandbox, kein Schlüssel und kein Workspace aus "
               "geheim.env, Modell Sonnet",
               rc == 0 and innen.get("oauth") == sha(oauth) and innen.get("api") == sha("") and innen.get("header") == sha("")
               and innen.get("etc_wamo") == "nein" and innen.get("modell") == "claude-sonnet-5-5",
               json.dumps({k: ("passt" if innen.get(k) == sha(w) else "falsch") for k, w in (("oauth", oauth), ("api", ""), ("header", ""))}
                          | {"modell": innen.get("modell"), "rc": rc, "ereignisse": ereignisse(z)}))
        audit = (D.LOGS / "auftraege.log").read_text(encoding="utf-8", errors="replace")[-200_000:]
        sitzung = (jobdir / "claude" / "projects" / "x" / "s.jsonl")
        lauflog = (jobdir / "lauf.log").read_text(encoding="utf-8", errors="replace") if (jobdir / "lauf.log").is_file() else ""
        pruefe("Persönlicher Zugang: der Wert steht nirgends (Ausgabe, Audit, Auftragsordner, Protokoll, Prozessliste), "
               "[CLAUDE_ZUGANG] an seiner Stelle",
               not fundstellen(oauth, jobdir, text, audit, lauflog) and not argv_funde and "[CLAUDE_ZUGANG]" in text
               and "[CLAUDE_ZUGANG]" in lauflog and sitzung.is_file() and "[CLAUDE_ZUGANG]" in sitzung.read_text()
               and "claude=persoenlich-oauth" in audit,
               f"Funde {fundstellen(oauth, jobdir, text, audit, lauflog)}, argv {argv_funde}, Sitzungsdatei "
               f"{'geschwärzt' if sitzung.is_file() and oauth not in sitzung.read_text() else 'fehlt oder offen'}")

        # 2. API-Schlüssel (Guthaben) mit eigenem Workspace: Haiku, kein oauth
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d"},
                                        zeile=(json.dumps({"typ": "api", "wert": api, "workspace": workspace}) + "\n").encode())
        innen = pruefung(z)
        pruefe("Persönlicher Zugang (api): ANTHROPIC_API_KEY mit eigenem Workspace-Header, nichts aus geheim.env, Modell Haiku",
               rc == 0 and innen.get("api") == sha(api) and innen.get("oauth") == sha("")
               and innen.get("header") == sha(f"anthropic-workspace-id: {workspace}") and innen.get("modell") == "claude-haiku-5-5"
               and not fundstellen(api, jobdir, text), f"rc {rc}, Modell {innen.get('modell')}, Funde {fundstellen(api, jobdir, text)}")

        # 3. Claude Code selbst gegen die Attrappe: Modell nach Zugangsart, Kontingent aus rate_limit_event
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d"}, modus="claude", limits=True,
                                        zeile=(json.dumps({"typ": "oauth", "wert": oauth}) + "\n").encode())
        kont = [x for x in z if x.get("ereignis") == "kontingent"]
        limits = kont[-1].get("limits", []) if kont else []
        pruefe("Claude Code mit Abo-Zugang: Modell Sonnet, rate_limit_event wird zum Ereignis kontingent (Sitzung 42, Woche 13, "
               "Modell-Woche 7 Prozent, nur Allowlist-Felder)",
               rc == 0 and init(z).get("model") == "claude-sonnet-5-5" and bool(kont)
               and [(e.get("art"), e.get("prozent")) for e in limits] == [("sitzung", 42.0), ("woche", 13.0), ("fable", 7.0)]
               and all(set(e) <= {"art", "prozent", "endet", "status"} and len(e.get("endet", "")) == 20 for e in limits)
               and set(kont[-1]) == {"type", "ereignis", "quelle", "limits"} and any(x.get("type") == "rate_limit_event" for x in z)
               and not fundstellen(oauth, jobdir, text),
               f"rc {rc}, Modell {init(z).get('model')}, Limits {[(e.get('art'), e.get('prozent')) for e in limits]}, "
               f"Ereignisse {ereignisse(z)}")
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d"}, modus="claude", limits=True,
                                        zeile=(json.dumps({"typ": "api", "wert": api}) + "\n").encode())
        pruefe("Claude Code mit API-Schlüssel: Modell Haiku, kein Kontingent (API-Schlüssel tragen keine Limit-Kopfzeilen)",
               rc == 0 and init(z).get("model") == "claude-haiku-5-5" and init(z).get("apiKeySource") == "ANTHROPIC_API_KEY"
               and "kontingent" not in ereignisse(z) and not fundstellen(api, jobdir, text),
               f"rc {rc}, Modell {init(z).get('model')}, Quelle {init(z).get('apiKeySource')}, Ereignisse {ereignisse(z)}")
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d", "modell": "claude-opus-5-5"},
                                        modus="claude", zeile=(json.dumps({"typ": "oauth", "wert": oauth}) + "\n").encode())
        pruefe("Übersteuerung über das Kopfzeilenfeld modell (Allowlist) gilt auch mit Abo-Zugang",
               rc == 0 and init(z).get("model") == "claude-opus-5-5", f"rc {rc}, Modell {init(z).get('model')}")

        # 4. fail-closed bei kaputtem Format: nichts startet, der Wert steht nirgends
        kaputt = "sk-ant-api03-Rauchtest" + stempel  # api-Format, aber als oauth übergeben
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "folgt"}, zeile=(json.dumps({"typ": "oauth", "wert": kaputt}) + "\n").encode())
        pruefe("Kaputtes Format: Start abgewiesen (Code 2, Ereignis zugang_ungueltig), Sandbox nicht gestartet, Wert nirgends",
               rc == D.EXIT_ABGEWIESEN and ereignisse(z) == ["zugang_ungueltig"] and not pruefung(z)
               and not fundstellen(kaputt, jobdir, text), f"rc {rc}, Ereignisse {ereignisse(z)}")
        for beispiel, zeile_ in (("kein JSON", b"das ist kein json\n"), ("fehlende Zeile", None),
                                 ("zusätzliches Feld", (json.dumps({"typ": "oauth", "wert": oauth, "x": 1}) + "\n").encode())):
            rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "folgt"}, zeile=zeile_)
            pruefe(f"Zugangszeile ungültig ({beispiel}): Code 2, nichts gestartet", rc == D.EXIT_ABGEWIESEN and not pruefung(z)
                   and "zugang_ungueltig" in ereignisse(z) and not fundstellen(oauth, jobdir, text), f"rc {rc}, {ereignisse(z)}")

        # 5. Wiederholung (nach approve oder Fortsetzung): der Dispatcher hat nichts aufbewahrt
        wiederholt = uuid.uuid4().hex[:16]
        a = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d"}, zeile=(json.dumps({"typ": "oauth", "wert": oauth}) + "\n").encode(),
                 job=wiederholt)
        b = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d"}, zeile=None, geheim={}, job=wiederholt)
        c = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d"}, zeile=(json.dumps({"typ": "oauth", "wert": oauth}) + "\n").encode(),
                 job=wiederholt, geheim={})
        pruefe("Wiederholung desselben Auftrags: ohne erneute Übergabe kein Zugang (nichts gespeichert), mit erneuter Übergabe läuft er",
               a[0] == 0 and b[0] == D.EXIT_ABGEWIESEN and c[0] == 0 and pruefung(c[1]).get("oauth") == sha(oauth)
               and not fundstellen(oauth, c[3], a[2], b[2], c[2]), f"Exits {a[0]} {b[0]} {c[0]}")

        # 6. Zugang aus geheim.env: nur wenn erlaubt, nie als Beimischung
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "server"})
        innen = pruefung(z)
        pruefe("Serverzugang (zugang=server): Schlüssel aus geheim.env, Haiku wie bisher",
               rc == 0 and innen.get("api") == sha(SERVER_GEHEIM["ANTHROPIC_API_KEY"]) and innen.get("modell") == "claude-haiku-5-5",
               f"rc {rc}, Modell {innen.get('modell')}")
        rc, z, text, jobdir, job = lauf()
        pruefe("Älterer Kern (ohne Feld zugang): Zugang aus geheim.env wie bisher", rc == 0
               and pruefung(z).get("api") == sha(SERVER_GEHEIM["ANTHROPIC_API_KEY"]), f"rc {rc}")
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "server"}, geheim=SERVER_GEHEIM | {"WAMO_SERVERZUGANG": "aus"})
        pruefe("WAMO_SERVERZUGANG=aus: ohne persönlichen Zugang startet nichts (Code 5, keine_zugangsdaten)",
               rc == D.EXIT_KEIN_ZUGANG and "keine_zugangsdaten" in ereignisse(z) and not pruefung(z), f"rc {rc}, {ereignisse(z)}")
        rc, z, text, jobdir, job = lauf(kopf_extra={"zugang": "folgt", "profil": "1a2b3c4d"},
                                        zeile=(json.dumps({"typ": "oauth", "wert": oauth}) + "\n").encode(),
                                        geheim=SERVER_GEHEIM | {"WAMO_SERVERZUGANG": "aus"})
        pruefe("WAMO_SERVERZUGANG=aus: der persönliche Zugang geht trotzdem", rc == 0 and pruefung(z).get("oauth") == sha(oauth), f"rc {rc}")

        audit = (D.LOGS / "auftraege.log").read_text(encoding="utf-8", errors="replace")[-300_000:]
        pruefe("Audit nennt Art und Kennung des Zugangs, nie den Wert",
               "claude=persoenlich-oauth" in audit and "CLAUDE_CODE_OAUTH_TOKEN@1a2b3c4d" in audit and oauth not in audit
               and api not in audit, "geprüft in /srv/wamo/logs/auftraege.log")
    finally:
        for job in jobs:
            entfernen(D.JOBS / job)
        entfernen(ordner)


# ---------------------------------------------------------------- GitHub-App echt (nach dem Einrichten)
def github_app_echt() -> None:
    """Prüft die eingerichtete App bei GitHub, ausschließlich lesend: Installation lesen (JWT), dann zwei Token der
    Stufe lesen (Art review: contents, metadata und pull_requests nur lesend), eines für die ganze Installation und
    eines nur für ein Repo, damit Repos auflisten und ein privates Repo lesen, beide widerrufen und prüfen, dass sie
    danach ungültig sind. Schreibende Token werden nie erzeugt, nichts wird geschrieben. Gibt weder Token noch
    Schlüssel noch den Namen der App aus. Nur wenn die drei Variablen WAMO_GH_APP_* gesetzt sind."""
    try:
        app = D.github_app_konfig(D.lese_geheimnisse())
    except D.GithubAppFehler as e:
        pruefe("GitHub-App: Konfiguration in /etc/wamo/geheim.env", False, str(e))
        return
    if app is None:
        pruefe("GitHub-App: nicht eingerichtet (WAMO_GH_APP_* fehlen), übersprungen", True)
        return
    status = D.github_status()
    if not pruefe("GitHub-App: Konfiguration und Schlüsseldatei (root:wamo 0640, RSA)", status == "app", status):
        return
    try:
        pem = D.schluessel_lesen(app.schluessel_name)
        try:
            jwt = D.jwt_erzeugen(app.app_id, bytes(pem))
        finally:
            pem[:] = bytes(len(pem))
        code, inst = D._github("GET", f"/app/installations/{app.installation_id}", jwt)
    except D.GithubAppFehler as e:
        pruefe("GitHub-App: Installation abrufbar", False, str(e))
        return
    rechte = inst.get("permissions") if code == 200 and isinstance(inst, dict) else None
    if not pruefe("GitHub-App: Installation abrufbar (App-ID, Schlüssel, Installation-ID passen)", isinstance(rechte, dict),
                  D._github_meldung(code, inst)):
        return
    zusaetzlich = sorted(k for k in rechte if k not in ("contents", "pull_requests", "metadata"))
    pruefe("GitHub-App: Rechte nur Contents (schreiben), Pull requests (schreiben), Metadata (lesen)",
           rechte.get("contents") == "write" and rechte.get("pull_requests") == "write"
           and rechte.get("metadata") == "read" and not zusaetzlich,
           ", ".join(f"{k}={v}" for k, v in sorted(rechte.items())) + (f"; bitte entfernen: {', '.join(zusaetzlich)}"
                                                                        if zusaetzlich else ""))
    pruefe("GitHub-App: nur auf ausgewählte Repos installiert (Only select repositories)",
           inst.get("repository_selection") == "selected", str(inst.get("repository_selection")))

    def lesetoken(was: str, repo: str | None, pruefung) -> None:
        """Token der Stufe lesen holen, damit lesen, widerrufen und die Ungültigkeit prüfen."""
        try:
            token, ablauf = D.installations_token(app, "review", repo)
        except D.GithubAppFehler as e:
            pruefe(f"GitHub-App: Lesetoken {was}", False, str(e))
            return
        try:
            pruefung(token, ablauf)
        except D.GithubAppFehler as e:
            pruefe(f"GitHub-App: Lesetoken {was}, Zugriffe", False, str(e))
        finally:
            widerrufen = D.token_widerrufen(token)
        code, _ = D._github("GET", "/installation/repositories?per_page=1", token)
        pruefe(f"GitHub-App: Lesetoken {was} widerrufen und danach ungültig", widerrufen and code == 401, f"GitHub {code}")

    gesehen: list[dict] = []

    def alle_repos(token: str, ablauf: str) -> None:
        code, obj = D._github("GET", "/installation/repositories?per_page=100", token)
        repos = [r for r in obj.get("repositories", []) if isinstance(r, dict)] \
            if code == 200 and isinstance(obj, dict) else []
        gesehen.extend(repos)
        pruefe(f"GitHub-App: Lesetoken (ganze Installation, läuft ab {ablauf}) listet {len(repos)} Repos",
               code == 200 and bool(repos), ", ".join(str(r.get("full_name")) for r in repos[:15]))
        pruefe("GitHub-App: das WAMO-Repo selbst gehört nicht zur Installation (für Aufträge gesperrt)",
               not any(str(r.get("full_name")).rsplit("/", 1)[-1].lower() == D.SELF_REPO for r in repos),
               "bitte in den Einstellungen der Installation aus der Auswahl entfernen")

    lesetoken("für die ganze Installation", None, alle_repos)
    if not gesehen:
        return
    ziel = next((r for r in gesehen if r.get("private")), gesehen[0])  # bevorzugt ein privates Repo
    name = str(ziel.get("full_name"))

    def ein_repo(token: str, ablauf: str) -> None:
        code, obj = D._github("GET", "/installation/repositories?per_page=100", token)
        sichtbar = [r.get("full_name") for r in obj.get("repositories", [])] if code == 200 and isinstance(obj, dict) else []
        pruefe("GitHub-App: Lesetoken nur für ein Repo sieht genau dieses eine Repo", sichtbar == [name],
               f"{len(sichtbar)} sichtbar, Ziel {name}")
        code, obj = D._github("GET", f"/repos/{name}", token)
        pruefe(f"GitHub-App: Lesezugriff auf {'privates ' if ziel.get('private') else ''}Repo {name}",
               code == 200 and isinstance(obj, dict) and obj.get("full_name") == name, f"GitHub {code}")
        code, _ = D._github("GET", f"/repos/{name}/pulls?per_page=1&state=all", token)
        pruefe("GitHub-App: Pull Requests lesbar (pull_requests: read)", code == 200, f"GitHub {code}")
        # ein anderes privates Repo (ein öffentliches wäre für jeden lesbar und sagt nichts über das Token)
        andere = next((r.get("full_name") for r in gesehen if r.get("private") and r.get("full_name") != name), None)
        if andere:
            code, _ = D._github("GET", f"/repos/{andere}", token)
            pruefe("GitHub-App: ein anderes Repo der Installation bleibt mit diesem Token unsichtbar", code == 404,
                   f"GitHub {code}")

    lesetoken(f"nur für {name}", name, ein_repo)


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--ausfuehren":
        D.beenden(ausfuehren(sys.argv[2]))  # hartes Ende wie der Dispatcher (Lebenslinien-Thread)
    if len(sys.argv) == 3 and sys.argv[1] == "--start-attrappe":
        D.beenden(start_attrappe(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--start-token":
        D.beenden(start_token(sys.argv[2]))
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
            github_app_mit_attrappe()
        if "--token-attrappe" in sys.argv:
            token_attrappe()
        if "--app-echt" in sys.argv:
            github_app_echt()
    finally:
        entfernen(PROJ)
        (D.JOBS / ".projekte" / f"{PROJEKT}.json").unlink(missing_ok=True)
    fehler = [n for n, ok in ERGEBNISSE if not ok]
    print(f"\n{len(ERGEBNISSE) - len(fehler)} von {len(ERGEBNISSE)} Prüfungen bestanden.")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
