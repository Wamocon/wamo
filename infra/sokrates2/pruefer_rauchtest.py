#!/usr/bin/python3 -E -s
"""Rauchtest der WAMO-Prüfer-Sandbox auf Sokrates-2 (Schritt 1.2c der Selbstüberarbeitung). Nur Standardbibliothek.

Aufruf (als root):

    sudo python3 -E -s /opt/wamo-pruefer/app/pruefer_rauchtest.py              # alles
    sudo python3 -E -s /opt/wamo-pruefer/app/pruefer_rauchtest.py --schnell    # ohne die Läufe mit Paketen aus dem Netz (Teil E)

Teil A: Installation (nur lesen): Besitzer und Rechte, Prüfsummen, Dienste, Ausgangsregeln, bwrap-Kopie, Werkzeuge.
Teil B: Der Proxy "pakete" lässt nur zu, was in der Liste steht (CONNECT auf Port 443), und weist alles andere ab.
Teil C: Die Sandbox selbst: Wer sie ist, was sie sieht, wohin sie schreiben darf, welche Adressen sie erreicht (nur den Proxy).
Teil D: Zeitlimit (nach dem Limit bleibt kein Prozess übrig) und Aufräumen der Arbeitsordner.
Teil E: Der ganze Weg mit einem kleinen Projekt: grüner Stand, absichtlich kaputter Test, untergeschobener Schlüssel, Lockfile-Änderung mit
        Audit, dazu ein Test, der aus dem Code heraus Adressen im Inneren und im Internet anzusprechen versucht (muss scheitern).
Es wird nichts am System geändert. Der Rauchtest schreibt nur in Wegwerf-Ordner unter /var/tmp und in die Arbeitsordner der Läufe.
Exit 0 nur, wenn alles bestanden ist.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

try:  # nur Linux; main() meldet es freundlich
    import pwd
except ImportError:
    pwd = None

sys.dont_write_bytecode = True
APP = "/opt/wamo-pruefer/app"
WERK = "/opt/wamo-pruefer/werkzeuge"
BASIS = "/opt/wamo-pruefer"
LAUF_SH = f"{APP}/wamo-pruefer-lauf.sh"
PROXY = ("127.0.0.1", 18080)
SANDBOX_BENUTZER = "wamo-pruefer"
PROXY_BENUTZER = "wamo-pruefer-proxy"
ERGEBNIS = {"ok": 0, "fehler": 0, "uebersprungen": 0}

# Läuft in der Sandbox: sammelt, was die Sandbox sieht und erreicht. Gibt eine JSON-Zeile aus.
SONDE = r"""
import json, os, socket, sys
erg = {}
erg["uid"] = os.getuid()
erg["name"] = os.environ.get("USER", "") or ""
erg["env_namen"] = sorted(os.environ)
def existiert(p):
    try:
        os.stat(p)
        return True
    except OSError:
        return False
erg["sichtbar"] = {p: existiert(p) for p in ("/etc/wamo", "/etc/wamo-anker", "/etc/wamo-release", "/etc/wamo-pruefer", "/etc/shadow", "/home", "/root", "/srv", "/var",
                                              "/run", "/opt/wamo", "/opt/wamo-release", "/opt/wamo-anker", "/mnt", "/media", "/boot", "/sys/fs/cgroup/system.slice")}
try:
    erg["wamo_lib"] = sorted(os.listdir("/usr/local/lib/wamo"))
except OSError:
    erg["wamo_lib"] = "fehlt"
try:
    erg["pruefer_lib"] = sorted(os.listdir("/usr/local/lib/wamo-pruefer"))
except OSError:
    erg["pruefer_lib"] = "fehlt"
def schreibbar(p):
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.unlink(p)
        return True
    except OSError:
        return False
erg["schreibbar"] = {p: schreibbar(p + "/probe.tmp") for p in ("/usr", "/usr/bin", "/etc", "/basis", "/opt/wamo-pruefer/app", "/opt/wamo-pruefer/werkzeuge", "/lauf/wamo",
                                                                  "/lauf/aus", "/lauf/home", "/tmp", "/lauf")}
erg["schreibbar_datei"] = {p: schreibbar(p) for p in ("/fremdverweise.txt.neu",)}
def verbindet(host, port, familie=socket.AF_INET):
    s = socket.socket(familie, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()
erg["netz"] = {
    "proxy": verbindet("127.0.0.1", 18080),
    "proxy_claude": verbindet("127.0.0.1", 18081),
    "db_supavisor_lan": verbindet("192.168.178.136", 55322),
    "db_supavisor_loopback": verbindet("127.0.0.1", 55322),
    "ssh_lan": verbindet("192.168.178.136", 22),
    "ssh_loopback": verbindet("127.0.0.1", 22),
    "caddy_lan": verbindet("192.168.178.136", 55443),
    "kern_loopback": verbindet("127.0.0.1", 8000),
    "internet": verbindet("1.1.1.1", 443),
    "ipv6_loopback_proxy": verbindet("::1", 18080, socket.AF_INET6),
}
# Das eigene Loopback der Sandbox muss funktionieren (Turbopack verbindet sich mit einem eigenen Node-Prozess)
try:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cl = socket.create_connection(srv.getsockname(), timeout=3)
    cl.close()
    srv.close()
    erg["eigener_loopback"] = True
except OSError:
    erg["eigener_loopback"] = False
try:
    socket.getaddrinfo("pypi.org", 443)
    erg["dns"] = True
except OSError:
    erg["dns"] = False
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
u.settimeout(3)
try:
    u.sendto(b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x04pypi\x03org\x00\x00\x01\x00\x01", ("127.0.0.53", 53))
    u.recv(512)
    erg["dns_udp"] = True
except OSError:
    erg["dns_udp"] = False
erg["prozesse"] = sum(1 for n in os.listdir("/proc") if n.isdigit())
print("SONDE " + json.dumps(erg))
"""

# Test im kleinen Projekt: Der Code versucht, aus der Sandbox heraus Adressen im Inneren und im Internet zu erreichen. Er besteht nur,
# wenn alles scheitert.
TEST_NETZ = '''\
import socket


def verbindet(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def test_inneres_und_internet_sind_nicht_erreichbar():
    for host, port in (("192.168.178.136", 55322), ("192.168.178.136", 22), ("127.0.0.1", 55322), ("127.0.0.1", 22), ("1.1.1.1", 443)):
        assert not verbindet(host, port), (host, port)
'''
SCHLUESSEL = "sk-ant-api03-" + "Zq9" * 30   # 13 + 90 Zeichen: Form eines echten Schlüssels, aber nur eine Attrappe


def pruefe(name: str, bedingung: bool, detail: str = "") -> bool:
    ERGEBNIS["ok" if bedingung else "fehler"] += 1
    print(f"  {'OK    ' if bedingung else 'FEHLER'} {name}" + (f": {detail}" if detail and not bedingung else ""))
    return bedingung


def uebersprungen(name: str, grund: str) -> None:
    ERGEBNIS["uebersprungen"] += 1
    print(f"  SKIP   {name}: {grund}")


def lauf(argv: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("errors", "replace")
    return subprocess.run(argv, **kw)


def info(pfad: str):
    try:
        return os.lstat(pfad)
    except OSError:
        return None


def root_eigen(pfad: str) -> bool:
    st = info(pfad)
    return st is not None and st.st_uid == 0 and not st.st_mode & 0o022


# ------------------------------------------------------------------------------------------------ Teil A
def teil_a() -> None:
    print("Teil A: Installation")
    for pfad in (BASIS, APP, WERK, "/srv/wamo-pruefer", "/srv/wamo-pruefer/lauf", "/etc/wamo-pruefer", "/usr/local/lib/wamo-pruefer"):
        pruefe(f"{pfad} gehört root und ist nicht beschreibbar", root_eigen(pfad) and stat.S_ISDIR(info(pfad).st_mode), str(info(pfad)))
    for pfad in (LAUF_SH, f"{APP}/pruefer_lauf.py", f"{APP}/pruefer_proxy.py", f"{APP}/pruefer_scans.py", f"{APP}/SHA256SUMS", "/etc/wamo-pruefer/regeln.nft",
                 "/etc/wamo-pruefer/proxy-pakete.env", "/etc/apparmor.d/wamo-pruefer-bwrap"):
        pruefe(f"{pfad} gehört root und ist nicht beschreibbar", root_eigen(pfad), str(info(pfad)))
    st = info("/usr/local/lib/wamo-pruefer/bwrap")
    pruefe("bwrap-Kopie root:wamo-pruefer 0750", st is not None and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o750, str(st))
    if st:
        pruefe("bwrap-Kopie gleicht /usr/bin/bwrap", open("/usr/bin/bwrap", "rb").read() == open("/usr/local/lib/wamo-pruefer/bwrap", "rb").read())
    r = lauf(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=APP)
    pruefe("Prüfsumme der Programme stimmt", r.returncode == 0, r.stdout + r.stderr)
    r = lauf(["sha256sum", "-c", "--quiet", f"{BASIS}/werkzeuge.SHA256SUMS"], cwd=WERK)
    pruefe("Prüfsumme der Werkzeuge stimmt", r.returncode == 0, r.stdout + r.stderr)
    pruefe("kein __pycache__ in der Installation", not any("__pycache__" in d for d, _, _ in os.walk(APP)))
    for b in (SANDBOX_BENUTZER, PROXY_BENUTZER):
        try:
            u = pwd.getpwnam(b)
            gruppen = lauf(["id", "-nG", b]).stdout.split()
            pruefe(f"Benutzer {b}: nologin, keine weiteren Gruppen", u.pw_shell.endswith("nologin") and gruppen == [b], f"{u.pw_shell} {gruppen}")
        except KeyError:
            pruefe(f"Benutzer {b} vorhanden", False)
    for unit in ("wamo-pruefer-ausgang.service", "wamo-pruefer-proxy@pakete.service"):
        r = lauf(["systemctl", "is-active", unit])
        pruefe(f"{unit} aktiv", r.stdout.strip() == "active", r.stdout)
    r = lauf(["nft", "list", "table", "inet", "wamo_pruefer_ausgang"])
    pruefe("Tabelle wamo_pruefer_ausgang geladen", r.returncode == 0 and "skuid" in r.stdout and "reject" in r.stdout and "100.64.0.0/10" in r.stdout, r.stderr)
    r = lauf(["systemctl", "show", "wamo-pruefer-proxy@pakete.service", "-p", "User", "-p", "NRestarts", "-p", "MainPID"])
    pruefe("Proxy läuft als wamo-pruefer-proxy", f"User={PROXY_BENUTZER}" in r.stdout, r.stdout)
    pid = [z.split("=", 1)[1] for z in r.stdout.splitlines() if z.startswith("MainPID=")]
    if pid and pid[0] != "0":
        caps = [z for z in open(f"/proc/{pid[0]}/status").read().splitlines() if z.startswith(("CapEff", "NoNewPrivs"))]
        pruefe("Proxy ohne Fähigkeiten und mit NoNewPrivs", caps == ["CapEff:\t0000000000000000", "NoNewPrivs:\t1"], str(caps))
    for werk in ("node/bin/node", "bin/uv", "bin/ruff", "bin/pip-audit"):
        pruefe(f"Werkzeug {werk} vorhanden", os.access(f"{WERK}/{werk}", os.X_OK))
    r = lauf([f"{WERK}/node/bin/node", "--version"])
    pruefe("Node ist Fassung 22", r.stdout.startswith("v22."), r.stdout)
    pruefe("Werkzeuge gehören root", all(root_eigen(os.path.join(d, n)) for d, _, ns in os.walk(WERK) for n in ns[:50] if not os.path.islink(os.path.join(d, n))))
    r = lauf(["sysctl", "-n", "kernel.apparmor_restrict_unprivileged_userns"])
    r2 = lauf(["runuser", "-u", SANDBOX_BENUTZER, "--", "/usr/bin/bwrap", "--unshare-user", "--ro-bind", "/usr", "/usr", "/usr/bin/true"])
    pruefe("das System-bwrap bleibt für wamo-pruefer gesperrt (nur die Kopie mit Profil darf)", r.stdout.strip() != "1" or r2.returncode != 0, r2.stderr)


# ------------------------------------------------------------------------------------------------ Teil B
def connect_antwort(ziel: str, pfad_zeile: str | None = None) -> str:
    """Schickt eine Zeile an den Proxy und liefert die erste Antwortzeile (oder 'keine Antwort')."""
    s = socket.create_connection(PROXY, timeout=15)
    try:
        s.sendall((pfad_zeile if pfad_zeile is not None else f"CONNECT {ziel} HTTP/1.1\r\nHost: {ziel}\r\n\r\n").encode())
        s.settimeout(15)
        daten = b""
        try:
            while b"\r\n" not in daten:
                teil = s.recv(512)
                if not teil:
                    break
                daten += teil
        except OSError:
            pass
        return daten.split(b"\r\n", 1)[0].decode("latin-1") or "keine Antwort"
    finally:
        s.close()


def teil_b() -> None:
    print("Teil B: Proxy pakete")
    a = connect_antwort("pypi.org:443")
    pruefe("CONNECT pypi.org:443 wird aufgebaut", " 200" in a, a)
    a = connect_antwort("registry.npmjs.org:443")
    pruefe("CONNECT registry.npmjs.org:443 wird aufgebaut", " 200" in a, a)
    for ziel, text in (("example.com:443", "fremde Domain"), ("pypi.org:22", "Port 22"), ("pypi.org:80", "Port 80"), ("1.1.1.1:443", "IP-Adresse"),
                       ("192.168.178.136:55322", "Datenbank im LAN"), ("localhost:443", "localhost"), ("127.0.0.1:18080", "der Proxy selbst"),
                       ("[::1]:443", "IPv6-Loopback"), ("api.anthropic.com:443", "Claude gehört nicht in diese Liste"), ("PYPI.ORG:443", "Großbuchstaben"),
                       ("pypi.org.:443", "Punkt am Ende"), ("evil.pypi.org:443", "Unterdomain")):
        a = connect_antwort(ziel)
        pruefe(f"CONNECT {ziel} abgewiesen ({text})", " 200" not in a and a.startswith(("HTTP/1.1 4", "HTTP/1.1 5", "keine Antwort")), a)
    a = connect_antwort("", "GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\n\r\n")
    pruefe("GET über den Proxy wird abgewiesen", " 200" not in a, a)
    a = connect_antwort("", "CONNECT pypi.org:443 HTTP/1.1\r\n" + "X: " + "a" * 5000 + "\r\n\r\n")
    pruefe("zu langer Kopf wird abgewiesen", " 200" not in a, a)


# ------------------------------------------------------------------------------------------------ Teil C
def sandbox(argv: list[str], *, neu: str | None = None, zeit: int = 120, extra: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = [LAUF_SH, "befehl", "--zeit", str(zeit), *(["--neu", neu] if neu else []), *(extra or []), "--", *argv]
    return lauf(cmd, timeout=zeit + 120)


def teil_c() -> None:
    print("Teil C: die Sandbox")
    r = sandbox(["/usr/bin/python3", "-I", "-c", SONDE])
    zeilen = [z for z in r.stdout.splitlines() if z.startswith("SONDE ")]
    if not pruefe("die Sonde läuft in der Sandbox", r.returncode == 0 and len(zeilen) == 1, (r.stdout + r.stderr)[-600:]):
        return
    d = json.loads(zeilen[0][6:])
    uid = pwd.getpwnam(SANDBOX_BENUTZER).pw_uid
    pruefe("Prozess läuft als wamo-pruefer (nicht root)", d["uid"] == uid and d["uid"] != 0, str(d["uid"]))
    schlecht = [n for n in d["env_namen"] if n.startswith("WAMO") or any(w in n.upper() for w in ("TOKEN", "SECRET", "KEY", "PASS", "ANTHROPIC", "SUPABASE", "DATABASE"))]
    pruefe("keine Geheimnisse oder WAMO-Namen in der Umgebung", not schlecht, str(schlecht))
    pruefe("Umgebung enthält den Proxy und HOME in /lauf", "HTTPS_PROXY" in d["env_namen"] and "HOME" in d["env_namen"])
    sicht = [p for p, ist in d["sichtbar"].items() if ist]
    pruefe("Geheimnis- und Systemordner sind unsichtbar", not sicht, str(sicht))
    pruefe("die Ordner der anderen WAMO-Sandboxen sind leer", d["wamo_lib"] in ([], "fehlt") and d["pruefer_lib"] in ([], "fehlt"), f"{d['wamo_lib']} {d['pruefer_lib']}")
    sch = sorted(p for p, ok in d["schreibbar"].items() if ok)
    pruefe("schreibbar sind nur /lauf (wamo, aus, home) und /tmp", sch == ["/lauf", "/lauf/aus", "/lauf/home", "/lauf/wamo", "/tmp"], str(sch))
    pruefe("die Sandbox sieht nur wenige Prozesse", d["prozesse"] < 15, str(d["prozesse"]))
    n = d["netz"]
    pruefe("das eigene Loopback der Sandbox funktioniert (Brücke zum Proxy, eigene Prozesse)", d["eigener_loopback"] is True)
    pruefe("Proxy pakete ist über die Brücke erreichbar", n["proxy"] is True)
    nicht = [k for k, v in n.items() if k != "proxy" and v]
    pruefe("alles andere ist unerreichbar (Datenbank, SSH, Caddy, Kern, Internet, IPv6, zweiter Proxy)", not nicht, str(nicht))
    pruefe("keine Namensauflösung und kein DNS über UDP", d["dns"] is False and d["dns_udp"] is False, f"dns={d['dns']} udp={d['dns_udp']}")
    r = sandbox(["/usr/bin/python3", "-I", "-c", "import ssl,urllib.request as u,os;"
                 "p=u.ProxyHandler({'https':os.environ['HTTPS_PROXY']});o=u.build_opener(p);"
                 "print('PYPI',o.open('https://pypi.org/simple/',timeout=30).status)"])
    pruefe("HTTPS zu pypi.org aus der Sandbox über den Proxy", "PYPI 200" in r.stdout, (r.stdout + r.stderr)[-400:])
    r = sandbox(["/usr/bin/python3", "-I", "-c", "import urllib.request as u,os;"
                 "o=u.build_opener(u.ProxyHandler({'https':os.environ['HTTPS_PROXY']}));"
                 "\ntry:\n o.open('https://example.com/',timeout=15); print('OFFEN')\nexcept Exception as e: print('ZU', type(e).__name__)"])
    pruefe("HTTPS zu example.com aus der Sandbox scheitert", "ZU " in r.stdout and "OFFEN" not in r.stdout, (r.stdout + r.stderr)[-300:])
    r = sandbox(["/bin/sh", "-c", "cat /etc/passwd; echo ---; cat /etc/group"])
    pruefe("passwd und group sind die eigenen (nur root und wamo-pruefer, je Datei zwei Zeilen)", r.stdout.count(":x:") == 4 and "wamocon" not in r.stdout, r.stdout[-300:])


# ------------------------------------------------------------------------------------------------ Teil D
def teil_d() -> None:
    print("Teil D: Zeitlimit und Aufräumen")
    t0 = time.monotonic()
    r = sandbox(["/bin/sh", "-c", "sleep 300 & sleep 300"], zeit=4)
    dauer = time.monotonic() - t0
    pruefe("das Zeitlimit bricht den Lauf ab (Rückgabe ungleich 0, schnell)", r.returncode != 0 and dauer < 40, f"code={r.returncode} dauer={dauer:.0f}s")
    time.sleep(1)
    reste = lauf(["pgrep", "-u", SANDBOX_BENUTZER]).stdout.split()
    pruefe("nach dem Limit läuft kein Prozess von wamo-pruefer mehr", not reste, str(reste))
    r = lauf(["systemctl", "list-units", "--all", "--no-legend", "wamo-pruefer-lauf-*"])
    pruefe("keine Einheit eines Laufs bleibt zurück", not r.stdout.strip(), r.stdout)
    pruefe("Arbeitsordner sind aufgeräumt", os.listdir("/srv/wamo-pruefer/lauf") == [], str(os.listdir("/srv/wamo-pruefer/lauf")))
    r = sandbox(["/bin/true"], extra=["--bogus"])
    pruefe("unbekannte Option wird abgewiesen", r.returncode != 0)
    r = lauf([LAUF_SH, "lauf", "--basis", "/nonexistent", "--neu", "/nonexistent", "--runde", "1", "--ausgabe", "/tmp/x.json"])
    pruefe("fehlende Quelle wird abgewiesen, ohne etwas zu starten", r.returncode != 0 and not os.path.exists("/tmp/x.json"))
    with tempfile.TemporaryDirectory(dir="/var/tmp") as t:
        os.symlink("/etc", os.path.join(t, "verweis"))
        r = lauf([LAUF_SH, "lauf", "--basis", os.path.join(t, "verweis"), "--neu", t, "--runde", "1", "--ausgabe", os.path.join(t, "aus.json")])
        pruefe("ein Link als Quelle wird abgewiesen", r.returncode != 0 and "Link" in r.stderr, r.stderr)


# ------------------------------------------------------------------------------------------------ Teil E
def schreibe(wurzel: str, pfad: str, inhalt: str) -> None:
    voll = os.path.join(wurzel, pfad)
    os.makedirs(os.path.dirname(voll), exist_ok=True)
    with open(voll, "w", encoding="utf-8") as f:
        f.write(inhalt)


def werkzeug_env(tmp: str) -> dict[str, str]:
    return {"PATH": f"{WERK}/bin:{WERK}/node/bin:/usr/bin:/bin", "HOME": tmp, "LANG": "C.UTF-8", "UV_CACHE_DIR": os.path.join(tmp, "uvcache"),
            "npm_config_cache": os.path.join(tmp, "npmcache"), "UV_PYTHON_DOWNLOADS": "never", "NEXT_TELEMETRY_DISABLED": "1"}


def baue_projekt(wurzel: str, tmp: str) -> bool:
    """Kleines Projekt in der Form, die der Läufer erwartet (voice-core mit uv.lock, ui mit package-lock.json). Die Sperrdateien entstehen hier
    als root mit direktem Netz, nicht in der Sandbox."""
    schreibe(wurzel, "voice-core/pyproject.toml",
             '[project]\nname = "mini"\nversion = "0.0.0"\nrequires-python = ">=3.11"\ndependencies = ["idna==3.15"]\n\n[dependency-groups]\ndev = ["pytest>=8"]\n\n'
             '[tool.uv]\npackage = false\n')
    schreibe(wurzel, "voice-core/test_mini.py", "def test_eins():\n    assert 1 + 1 == 2\n")
    schreibe(wurzel, "ui/package.json", json.dumps({"name": "mini-ui", "version": "0.0.0", "private": True,
             "scripts": {"build": "node -e \"process.stdout.write('bau ok')\"", "test": "node -e \"process.exit(0)\"", "lint": "node -e \"process.exit(0)\""},
             "dependencies": {"is-number": "7.0.0"}, "devDependencies": {"typescript": "5.6.3"}}, indent=1) + "\n")
    schreibe(wurzel, "ui/tsconfig.json", '{"compilerOptions": {"noEmit": true, "strict": true, "target": "es2022"}, "files": ["a.ts"]}\n')
    schreibe(wurzel, "ui/a.ts", "export const x: number = 1;\n")
    schreibe(wurzel, "README.md", "Kleines Projekt für den Rauchtest der Prüfer-Sandbox.\n")
    env = werkzeug_env(tmp)
    r1 = lauf([f"{WERK}/bin/uv", "lock"], cwd=os.path.join(wurzel, "voice-core"), env=env, timeout=300)
    r2 = lauf([f"{WERK}/node/bin/npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=os.path.join(wurzel, "ui"), env=env, timeout=300)
    ok = r1.returncode == 0 and r2.returncode == 0 and os.path.exists(os.path.join(wurzel, "voice-core/uv.lock")) and os.path.exists(os.path.join(wurzel, "ui/package-lock.json"))
    if not ok:
        print((r1.stdout + r1.stderr + r2.stdout + r2.stderr)[-800:])
    return ok


def kopiere(quelle: str, ziel: str) -> None:
    shutil.copytree(quelle, ziel, symlinks=True)


def fuehre_lauf(basis: str, neu: str, ausgabe: str, extra: list[str] | None = None) -> tuple[int, dict | None, str]:
    muster = os.path.join(os.path.dirname(ausgabe), "muster.txt")
    with open(muster, "w", encoding="utf-8") as f:
        f.write("# Attrappe der Mustervorlage\nzzz-ein-wort-das-es-nicht-gibt-zzz\n")
    r = lauf([LAUF_SH, "lauf", "--basis", basis, "--neu", neu, "--runde", "1", "--ausgabe", ausgabe, "--fremdverweise", muster, *(extra or [])], timeout=3000)
    daten = None
    if os.path.exists(ausgabe):
        with open(ausgabe, encoding="utf-8") as f:
            daten = json.load(f)
    return r.returncode, daten, r.stdout + r.stderr


def teil_e() -> None:
    print("Teil E: der ganze Weg mit einem kleinen Projekt (holt Pakete über den Proxy, dauert einige Minuten)")
    with tempfile.TemporaryDirectory(dir="/var/tmp", prefix="pruefer-rauch.") as t:
        os.chmod(t, 0o755)
        projekt = os.path.join(t, "projekt")
        os.makedirs(projekt)
        if not pruefe("kleines Projekt mit Sperrdateien angelegt (uv lock, npm lock)", baue_projekt(projekt, t)):
            return
        # E1: grüner Stand. Zusätzlich der Netztest, der nur besteht, wenn alles Innere und das Internet unerreichbar sind.
        basis, neu = os.path.join(t, "basis1"), os.path.join(t, "neu1")
        kopiere(projekt, basis)
        kopiere(projekt, neu)
        schreibe(neu, "voice-core/test_netz.py", TEST_NETZ)
        schreibe(neu, "README.md", "Kleines Projekt für den Rauchtest der Prüfer-Sandbox. Geändert.\n")
        code, d, text = fuehre_lauf(basis, neu, os.path.join(t, "e1.json"))
        ps = d["pruefstand"] if d else {}
        pruefe("E1 grüner Stand: Exit 0 und jede Stufe ok", code == 0 and d is not None and all(v == "ok" for v in ps.values()) and len(ps) == 8, f"{code} {ps} {text[-500:]}")
        if d:
            pruefe("E1 Stufen bauen, tests, typen_lint haben wirklich gelaufen (Dauer messbar)", d.get("dauer_s", 0) >= 1 and d["details"]["bauen"]["fehler"] == "", str(d.get("dauer_s")))
            pruefe("E1 die Netzprobe aus dem Code ist in den Tests grün (Inneres und Internet unerreichbar)", d["pruefstand"]["tests"] == "ok")
            pruefe("E1 Prüfstand passt zur Form, die release_automat verlangt", d["pruefstand"].keys() == {"bauen", "tests", "typen_lint", "secret_scan", "fremdverweis",
                   "abhaengigkeiten", "migrations_lint", "wirkungsflaechen"} and d["runde"] == 1)
        # E2: absichtlich kaputter Test
        neu2 = os.path.join(t, "neu2")
        kopiere(projekt, neu2)
        schreibe(neu2, "voice-core/test_kaputt.py", "def test_kaputt():\n    assert 1 == 2\n")
        code, d, text = fuehre_lauf(basis, neu2, os.path.join(t, "e2.json"))
        ps = d["pruefstand"] if d else {}
        pruefe("E2 kaputter Test: Exit 1, tests=fehler, bauen=ok", code == 1 and ps.get("tests") == "fehler" and ps.get("bauen") == "ok", f"{code} {ps}")
        if d:
            pruefe("E2 der Grund nennt das Paket, nicht den Testinhalt", d["details"]["tests"]["fehler"].startswith("exit_1:voice-core"), d["details"]["tests"]["fehler"])
        # E3: untergeschobener Schlüssel
        neu3 = os.path.join(t, "neu3")
        kopiere(projekt, neu3)
        schreibe(neu3, "voice-core/konfig.py", f'TOKEN = "{SCHLUESSEL}"\n')
        code, d, text = fuehre_lauf(basis, neu3, os.path.join(t, "e3.json"))
        ps = d["pruefstand"] if d else {}
        roh = open(os.path.join(t, "e3.json"), encoding="utf-8").read() if d else ""
        pruefe("E3 untergeschobener Schlüssel: secret_scan=fehler, Exit 1", code == 1 and ps.get("secret_scan") == "fehler", f"{code} {ps}")
        pruefe("E3 das Ergebnis enthält den Schlüssel nicht", d is not None and SCHLUESSEL not in roh and SCHLUESSEL[:20] not in roh)
        # E4: Lockfile-Änderung: der Audit läuft über den Proxy (npm audit, uv export und pip-audit)
        basis4 = os.path.join(t, "basis4")
        kopiere(projekt, basis4)
        os.remove(os.path.join(basis4, "ui/package-lock.json"))
        os.remove(os.path.join(basis4, "voice-core/uv.lock"))
        code, d, text = fuehre_lauf(basis4, neu, os.path.join(t, "e4.json"))
        ps = d["pruefstand"] if d else {}
        pruefe("E4 neue Sperrdateien: neue Pakete werden gemeldet und der Audit läuft über den Proxy", d is not None and len(d.get("neue_pakete", [])) > 0 and ps.get("abhaengigkeiten") == "ok",
               f"{code} {ps} {d and d['details'].get('abhaengigkeiten')} {text[-400:]}")
        # E4b: bekannte Lücke (idna 3.10, PYSEC-2026-215, behoben in 3.15): der Audit muss die Stufe rot machen
        neu4b = os.path.join(t, "neu4b")
        kopiere(projekt, neu4b)
        pp = os.path.join(neu4b, "voice-core/pyproject.toml")
        with open(pp, encoding="utf-8") as f:
            text_pp = f.read()
        with open(pp, "w", encoding="utf-8") as f:
            f.write(text_pp.replace("idna==3.15", "idna==3.10"))
        lauf([f"{WERK}/bin/uv", "lock"], cwd=os.path.join(neu4b, "voice-core"), env=werkzeug_env(t), timeout=300)
        code, d, text = fuehre_lauf(projekt, neu4b, os.path.join(t, "e4b.json"))
        ps = d["pruefstand"] if d else {}
        pruefe("E4b bekannte Lücke in einem Paket: abhaengigkeiten=fehler (pip-audit über den Proxy)", code == 1 and ps.get("abhaengigkeiten") == "fehler"
               and "pip-audit" in d["details"]["abhaengigkeiten"]["fehler"], f"{code} {ps}")
        # E5: Link im Stand
        neu5 = os.path.join(t, "neu5")
        kopiere(projekt, neu5)
        os.symlink("/etc", os.path.join(neu5, "verweis"))
        code, d, text = fuehre_lauf(basis, neu5, os.path.join(t, "e5.json"))
        ps = d["pruefstand"] if d else {}
        pruefe("E5 ein Link im Stand: bauen=fehler (link_im_baum)", d is not None and ps.get("bauen") == "fehler" and d["details"]["bauen"]["fehler"] == "link_im_baum",
               f"{code} {ps}")
    pruefe("nach Teil E sind die Arbeitsordner aufgeräumt", os.listdir("/srv/wamo-pruefer/lauf") == [])
    pruefe("nach Teil E läuft kein Prozess von wamo-pruefer", not lauf(["pgrep", "-u", SANDBOX_BENUTZER]).stdout.split())


def main() -> int:
    ap = argparse.ArgumentParser(description="Rauchtest der WAMO-Prüfer-Sandbox")
    ap.add_argument("--schnell", action="store_true", help="ohne Teil E (Läufe mit Paketen aus dem Netz)")
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        print("Der Rauchtest läuft nur unter Linux (Sokrates-2).", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("Bitte als root ausführen.", file=sys.stderr)
        return 2
    print("Prüfer-Sandbox-Rauchtest")
    teil_a()
    teil_b()
    teil_c()
    teil_d()
    if args.schnell:
        uebersprungen("Teil E", "--schnell")
    else:
        teil_e()
    print(f"Ergebnis: {ERGEBNIS['ok']} bestanden, {ERGEBNIS['fehler']} fehlgeschlagen, {ERGEBNIS['uebersprungen']} übersprungen")
    return 0 if ERGEBNIS["fehler"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
