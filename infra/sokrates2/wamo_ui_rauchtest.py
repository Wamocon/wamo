#!/usr/bin/env python3
"""Rauchtest für das Umschalten der WAMO-Oberfläche (infra/sokrates2/wamo-ui-umschalten.sh) mit Attrappen und echtem Caddy.

Was geprüft wird: Erstumstellung vom einzelnen Container wamo-ui auf blau/grün, ein Release, der Rückweg und der Weg
vorwärts, die Schnittstelle des Notankers (anker-rueckweg), --trocken, Fehlerfälle (Prüfung vor dem Umschalten fällt durch,
Kern antwortet falsch, Bewährung fällt durch bei Absturz und bei Fehlerantworten, zwei Läufe zugleich) und der Ausfall der
live gesetzten Farbe. Während aller Schritte läuft eine Last von 20 Anfragen je Sekunde durch Caddy. Gezählt werden Fehler
(kein Netz, falscher Status, falsche Fassung, fehlende Datei) und gemessen wird die Zeit von der Entscheidung bis zur Wirkung.
Ziel: null Fehler beim Umschalten und beim Rückweg.

Alles läuft in einem Wegwerf-Container ("Läufer", Debian mit Docker-CLI), der den Docker-Daemon des Rechners nutzt. Angelegt
werden nur Container, Netze, Volumes und Images mit dem Namen wamo-test-NAME-..., nach dem Lauf werden sie entfernt. Fremde
Container werden nie angefasst (jede Löschung prüft den Präfix). Am PC wie auf dem Server gleich:

    python infra/sokrates2/wamo_ui_rauchtest.py                    # baut den Läufer, führt alles aus, räumt auf
    python infra/sokrates2/wamo_ui_rauchtest.py --bild-node node:22-slim --ausgabe ergebnis.json
    python infra/sokrates2/wamo_ui_rauchtest.py --nur kurz         # ohne die Fehlerfälle mit Absturz und Ausfall

Das Umschalt-Skript selbst läuft im Testmodus (WAMO_UI_PRAEFIX=wamo-test-NAME-): eigene Namen, eigene Ordner, Attrappen statt
der echten Oberfläche (infra/sokrates2/wamo-ui/attrappe), Caddy mit HTTP statt HTTPS. Die Site-Datei kommt aus der echten
Vorlage wamo-ui/wamo-ui.caddy.vorlage, die Include-Datei erzeugt das Skript.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HIER = Path(__file__).resolve().parent
REPO = HIER.parent.parent
CADDY_IMAGE = "caddy:2.11.2-alpine@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d"
LAEUFER_BILD = "wamo-test-ui-lauf:1"
NAME_RE = re.compile(r"^[a-z0-9]{1,12}$")
BILD_RE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]{0,120}$")
VERSION_JSON_RE = re.compile(
    r'^\{"name":"wamo-ui","version":"([0-9.]+)","commit":"([0-9a-f]{40}|unbekannt)","gebaut":"([0-9TZ:-]+|unbekannt)",'
    r'"sauber":(true|false)\}$')


# ======================================================================== Hilfen (außen und innen)
def sh(*cmd: str, env: dict[str, str] | None = None, check: bool = True, timeout: float = 900,
       eingabe: bytes | None = None) -> subprocess.CompletedProcess:
    umgebung = {**os.environ, **(env or {})}
    r = subprocess.run(list(cmd), capture_output=True, env=umgebung, timeout=timeout, input=eingabe)
    r.stdout = r.stdout.decode("utf-8", "replace")
    r.stderr = r.stderr.decode("utf-8", "replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


def aufraeumen_praefix(p: str, behalte: tuple[str, ...] = ()) -> None:
    """Entfernt alles mit dem Präfix p (Container, Images, Netze, Volumes). Verweigert jeden Namen ohne wamo-test-."""
    if not re.fullmatch(r"wamo-test-[a-z0-9]{1,12}-", p):
        raise RuntimeError(f"Ungültiger Präfix {p!r}")

    def sicher(n: str) -> str:
        if not n.startswith(p):
            raise RuntimeError(f"Verweigert: {n} gehört nicht zum Test ({p})")
        return n

    r = sh("docker", "ps", "-a", "--format", "{{.Names}}", "--filter", f"name=^{p}", check=False)
    for n in r.stdout.split():
        if n.startswith(p) and n not in behalte:
            sh("docker", "rm", "-f", "-v", sicher(n), check=False)
    r = sh("docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", check=False)
    for b in r.stdout.split():
        if b.startswith(p):
            sh("docker", "image", "rm", sicher(b), check=False)
    r = sh("docker", "network", "ls", "--format", "{{.Name}}", "--filter", f"name=^{p}", check=False)
    for n in r.stdout.split():
        if n.startswith(p):
            for c in behalte:
                sh("docker", "network", "disconnect", "-f", sicher(n), c, check=False)
            sh("docker", "network", "rm", sicher(n), check=False)
    r = sh("docker", "volume", "ls", "--format", "{{.Name}}", "--filter", f"name=^{p}", check=False)
    for n in r.stdout.split():
        if n.startswith(p) and n not in behalte:
            sh("docker", "volume", "rm", "-f", sicher(n), check=False)


# ======================================================================== außen: Läufer bauen und starten
def aussen(args: argparse.Namespace) -> int:
    praefix = f"wamo-test-{args.name}-"
    laeufer = praefix + "lauf"
    volume = praefix + "etc"
    dockerfile = (
        "FROM docker:cli AS d\n"
        "FROM python:3.12-slim\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends curl util-linux ca-certificates "
        "&& rm -rf /var/lib/apt/lists/*\n"
        "COPY --from=d /usr/local/bin/docker /usr/local/bin/docker\n"
        "COPY --from=d /usr/local/libexec/docker/cli-plugins/docker-compose "
        "/usr/local/libexec/docker/cli-plugins/docker-compose\n"
        "COPY --from=d /usr/local/libexec/docker/cli-plugins/docker-buildx "
        "/usr/local/libexec/docker/cli-plugins/docker-buildx\n"
    )
    print("== Läufer-Image bauen (einmalig, danach aus dem Cache)")
    r = sh("docker", "build", "-q", "-t", LAEUFER_BILD, "-", eingabe=dockerfile.encode(), check=False)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        return 1
    aufraeumen_praefix(praefix)
    sh("docker", "volume", "create", volume)
    mounts = ["-v", "/var/run/docker.sock:/var/run/docker.sock", "-v", f"{REPO}:/repo:ro", "-v", f"{volume}:/arbeit/caddy/etc"]
    befehl = ["docker", "run", "--rm", "--name", laeufer, *mounts]
    ausgabe_ordner = None
    if args.ausgabe:
        ausgabe_ordner = Path(args.ausgabe).resolve().parent
        ausgabe_ordner.mkdir(parents=True, exist_ok=True)
        befehl += ["-v", f"{ausgabe_ordner}:/ausgabe"]
    befehl += [LAEUFER_BILD, "python3", "-u", "/repo/infra/sokrates2/wamo_ui_rauchtest.py", "--innen", "--name", args.name,
               "--bild-node", args.bild_node, "--nur", args.nur]
    if args.ausgabe:
        befehl += ["--ausgabe", f"/ausgabe/{Path(args.ausgabe).name}"]
    try:
        rc = subprocess.run(befehl).returncode
    finally:
        aufraeumen_praefix(praefix)
        if not args.bild_behalten:
            sh("docker", "rmi", LAEUFER_BILD, check=False)
    return rc


# ======================================================================== innen: Last und Kern-Attrappe
def commit_von(k: int) -> str:
    return hashlib.sha1(f"rauchtest-{k}".encode()).hexdigest()


class Probe:
    __slots__ = ("t", "pfad", "status", "dauer", "commit", "fehler", "wdh")

    def __init__(self, t, pfad, status, dauer, commit, fehler, wdh=0):
        self.t, self.pfad, self.status, self.dauer, self.commit, self.fehler = t, pfad, status, dauer, commit, fehler
        self.wdh = wdh


class Last:
    """Gleichmäßige Anfragen (Standard 20 je Sekunde) durch Caddy. Fehler: kein Netz, falscher Status, ungültige Antwort."""

    def __init__(self, host: str, port: int, rate: float = 20.0):
        self.host, self.port, self.rate = host, port, rate
        self.proben: list[Probe] = []
        self.sperre = threading.Lock()
        self.laeuft = False
        self.chunks: set[str] = set()      # diese Dateien müssen immer mit 200 antworten
        self.alt_erlaubt = False           # Fassung vor der Umstellung: /version.json antwortet mit 404
        self.lokal = threading.local()
        self.pool = ThreadPoolExecutor(max_workers=12)
        self.faden: threading.Thread | None = None

    def setze(self, chunks: set[str] | None = None, alt_erlaubt: bool | None = None) -> None:
        with self.sperre:
            if chunks is not None:
                self.chunks = set(chunks)
            if alt_erlaubt is not None:
                self.alt_erlaubt = alt_erlaubt

    def _verbindung(self) -> http.client.HTTPConnection:
        c = getattr(self.lokal, "c", None)
        if c is None:
            c = http.client.HTTPConnection(self.host, self.port, timeout=5)
            self.lokal.c = c
        return c

    def _anfrage(self, pfad: str) -> tuple[int, bytes, int]:
        """Eine Anfrage wie ein Browser: Bricht eine wiederverwendete Verbindung ab (Caddy schließt beim Neuladen der
        Konfiguration Ruhe-Verbindungen), wiederholt er die GET-Anfrage einmal auf einer neuen Verbindung. Das zählt als
        Wiederholung, nicht als Fehler. Eine frische Verbindung wird nie wiederholt."""
        for versuch in (0, 1):
            wiederverwendet = getattr(self.lokal, "benutzt", False)
            try:
                c = self._verbindung()
                c.request("GET", pfad)
                antwort = c.getresponse()
                koerper = antwort.read()
                self.lokal.benutzt = True
                return antwort.status, koerper, versuch
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, http.client.CannotSendRequest,
                    http.client.BadStatusLine):
                self.lokal.c = None
                self.lokal.benutzt = False
                if not (versuch == 0 and wiederverwendet):
                    raise
        raise RuntimeError("unerreichbar")

    def _eine(self, pfad: str) -> None:
        start = time.time()
        status, commit, fehler, wdh = 0, "", "", 0
        try:
            status, koerper, wdh = self._anfrage(pfad)
            with self.sperre:
                alt = self.alt_erlaubt
            if pfad == "/version.json":
                if status == 200:
                    m = VERSION_JSON_RE.match(koerper.decode("utf-8", "replace"))
                    if m:
                        commit = m.group(2)
                    else:
                        fehler = "version.json ungültig"
                elif not (status == 404 and alt):
                    fehler = f"HTTP {status}"
            elif status != 200:
                fehler = f"HTTP {status}"
        except Exception as e:  # noqa: BLE001 - jede Störung zählt als Fehler
            fehler = type(e).__name__
            self.lokal.c = None
            self.lokal.benutzt = False
        with self.sperre:
            self.proben.append(Probe(start, pfad, status, time.time() - start, commit, fehler, wdh))

    def _lauf(self) -> None:
        takt = 1.0 / self.rate
        naechster = time.monotonic()
        i = 0
        while self.laeuft:
            with self.sperre:
                chunks = sorted(self.chunks)
            pfade = ["/version.json", "/", *[f"/_next/static/chunks/{c}" for c in chunks]]
            self.pool.submit(self._eine, pfade[i % len(pfade)])
            i += 1
            naechster += takt
            warten = naechster - time.monotonic()
            if warten > 0:
                time.sleep(warten)

    def start(self) -> None:
        self.laeuft = True
        self.faden = threading.Thread(target=self._lauf, daemon=True)
        self.faden.start()

    def stop(self) -> None:
        self.laeuft = False
        if self.faden:
            self.faden.join(5)
        self.pool.shutdown(wait=True)

    def fenster(self, von: float, bis: float) -> dict:
        with self.sperre:
            ps = [p for p in self.proben if von <= p.t <= bis]
        fehler = [p for p in ps if p.fehler]
        dauern = sorted(p.dauer for p in ps)
        zahl = len(ps)
        return {
            "anfragen": zahl,
            "wiederholungen": sum(p.wdh for p in ps),
            "fehler": len(fehler),
            "fehler_arten": sorted({f"{p.pfad}:{p.fehler}" for p in fehler})[:6],
            "p50_ms": round(statistics.median(dauern) * 1000, 1) if dauern else 0,
            "p95_ms": round(dauern[max(0, int(zahl * 0.95) - 1)] * 1000, 1) if dauern else 0,
            "max_ms": round(dauern[-1] * 1000, 1) if dauern else 0,
        }

    def erste_antwort_mit(self, commit: str, ab: float) -> float | None:
        with self.sperre:
            kandidaten = [p.t for p in self.proben if p.pfad == "/version.json" and p.commit == commit and p.t >= ab]
        return min(kandidaten) if kandidaten else None

    def letzte_antwort_mit(self, commit: str, bis: float) -> float | None:
        with self.sperre:
            kandidaten = [p.t for p in self.proben if p.pfad == "/version.json" and p.commit == commit and p.t <= bis]
        return max(kandidaten) if kandidaten else None


class KernAttrappe:
    """Antwortet auf POST /start wie der Kern ohne Anmeldung (401); der Status ist umstellbar."""

    def __init__(self, port: int):
        aussen_ref = self
        self.status = 401

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.send_response(aussen_ref.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


# ======================================================================== innen: Umgebung
class Umgebung:
    def __init__(self, name: str, bild_node: str):
        self.name = name
        self.p = f"wamo-test-{name}-"
        self.bild_node = bild_node
        self.arbeit = Path("/arbeit")
        self.caddy_dir = self.arbeit / "caddy"
        self.etc = self.caddy_dir / "etc"       # Volume, gemeinsam mit dem Caddy-Container
        self.basis = self.arbeit / "basis"
        self.quelle = self.arbeit / "quelle"
        self.ctx = self.quelle / "ctx"
        self.netz_ui = self.p + "ui-netz"
        self.netz_aussen = self.p + "aussen"
        self.caddy = self.p + "caddy"
        self.port = 18080
        self.kern_port = 18081
        self.mein_name = self.p + "lauf"
        self.c_alt = self.p + "ui"
        self.c_blau = self.p + "ui-blau"
        self.c_gruen = self.p + "ui-gruen"
        self.bauargs = f"NODE_IMAGE={bild_node}"
        self.anker = self._anker_laden()

    @staticmethod
    def _anker_laden():
        spec = importlib.util.spec_from_file_location("wamo_anker", REPO / "infra" / "sokrates2" / "anker" / "wamo_anker.py")
        modul = importlib.util.module_from_spec(spec)
        sys.modules["wamo_anker"] = modul     # dataclasses schlägt den Namen des Moduls nach
        spec.loader.exec_module(modul)
        return modul

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        e = {
            "WAMO_UI_PRAEFIX": self.p,
            "WAMO_UI_BASIS": str(self.basis),
            "WAMO_UI_CADDY_DIR": str(self.caddy_dir),
            "WAMO_UI_QUELLE": str(self.quelle),
            "WAMO_UI_BAUDATEI": str(self.quelle / "attrappe" / "Dockerfile"),
            "WAMO_UI_BAUKONTEXT": str(self.ctx),
            "WAMO_UI_BEREIT_S": "40",
            "ATTRAPPE_BAUARGS": self.bauargs,
        }
        e.update(extra or {})
        return e

    def aufbauen(self) -> None:
        aufraeumen_praefix(self.p, behalte=(self.mein_name, self.p + "etc"))
        sh("docker", "network", "create", self.netz_aussen)
        sh("docker", "network", "create", "--internal", self.netz_ui)
        sh("docker", "network", "connect", self.netz_aussen, self.mein_name)
        self.basis.mkdir(parents=True, exist_ok=True)
        (self.quelle / "wamo-ui").mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO / "infra" / "sokrates2" / "wamo-ui" / "attrappe", self.quelle / "attrappe", dirs_exist_ok=True)
        shutil.copy(REPO / "infra" / "sokrates2" / "wamo-ui" / "docker-compose.yml", self.quelle / "wamo-ui" / "docker-compose.yml")
        shutil.copy(REPO / "infra" / "sokrates2" / "wamo-ui-umschalten.sh", self.quelle / "wamo-ui-umschalten.sh")
        (self.basis / "wamo-ui.conf").write_text(
            f"WAMO_UI_URL=http://{self.caddy}:{self.port}\n"
            "NEXT_PUBLIC_SUPABASE_URL=https://192.168.178.136:55443\n"
            f"NEXT_PUBLIC_WAMO_KERN_BUERO=http://127.0.0.1:{self.kern_port}\n"
            "WAMO_UI_TLS=intern\nWAMO_UI_HSTS=nein\nNEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER=\n", encoding="utf-8")

    def etc_schreiben(self) -> None:
        """Konfiguration von Caddy: Hauptdatei und die Site aus der echten Vorlage (Adresse http://:18080, ohne TLS)."""
        vorlage = (REPO / "infra" / "sokrates2" / "wamo-ui" / "wamo-ui.caddy.vorlage").read_text(encoding="utf-8")
        site = vorlage.replace("__ADRESSE__", f"http://:{self.port}").replace("__TLS__", "").replace("__HSTS__", "")
        (self.etc / "kern").mkdir(parents=True, exist_ok=True)
        (self.etc / "Caddyfile").write_text(
            "{\n\tadmin 127.0.0.1:2019\n\tauto_https off\n}\nimport /etc/caddy/kern/*.caddy\n", encoding="utf-8")
        (self.etc / "kern" / "wamo-ui.caddy").write_text(site, encoding="utf-8")
        os.chmod(self.etc, 0o755)
        os.chmod(self.etc / "kern", 0o755)

    def caddy_starten(self) -> None:
        sh("docker", "run", "-d", "--name", self.caddy, "--network", self.netz_aussen, "--user", "1000:1000", "--read-only",
           "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE", "--security-opt", "no-new-privileges:true",
           "-v", f"{self.p}etc:/etc/caddy:ro", "--tmpfs", "/data:uid=1000", "--tmpfs", "/config:uid=1000", CADDY_IMAGE)
        sh("docker", "network", "connect", self.netz_ui, self.caddy)
        for _ in range(40):
            r = sh("docker", "exec", self.caddy, "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:2019/config/", check=False)
            if r.returncode == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("Caddy startet nicht:\n" + sh("docker", "logs", self.caddy, check=False).stderr)

    def legacy_starten(self) -> None:
        """Der einzelne Container wamo-ui der früheren Fassung: ohne /version.json, Chunk app-0.js."""
        ctx = self.arbeit / "legacy-ctx"
        if ctx.exists():
            shutil.rmtree(ctx)
        (ctx / "static").mkdir(parents=True)
        shutil.copy(self.quelle / "attrappe" / "server.js", ctx / "server.js")
        (ctx / "package.json").write_text(json.dumps({"name": "wamo-ui", "version": "0.0.9"}, indent=2), encoding="utf-8")
        (ctx / "static" / "gemeinsam.js").write_text("// gemeinsam\n", encoding="utf-8")
        (ctx / "static" / "app-0.js").write_text("// fassung 0\n", encoding="utf-8")
        bild = f"{self.p}ui:aktuell"
        sh("docker", "build", "-q", "-f", str(self.quelle / "attrappe" / "Dockerfile"), "--build-arg", f"NODE_IMAGE={self.bild_node}",
           "--build-arg", "ATTRAPPE_MODUS=alt", "--build-arg", "NEXT_PUBLIC_SUPABASE_URL=https://192.168.178.136:55443",
           "--build-arg", f"NEXT_PUBLIC_WAMO_KERN_BUERO=http://127.0.0.1:{self.kern_port}", "-t", bild, str(ctx))
        sh("docker", "run", "-d", "--name", self.c_alt, "--network", self.netz_ui, "--read-only", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges:true", "--restart", "unless-stopped", bild)

    def fassung_bereit(self, k: int, modus: str = "", nach_s: int = 0) -> str:
        """Kontext und bau.info der Fassung k (Version 0.1.<k-1>, Chunk app-<k>.js). Ergebnis: Commit-ID."""
        commit = commit_von(k)
        if self.ctx.exists():
            shutil.rmtree(self.ctx)
        (self.ctx / "static").mkdir(parents=True)
        shutil.copy(self.quelle / "attrappe" / "server.js", self.ctx / "server.js")
        (self.ctx / "package.json").write_text(json.dumps({"name": "wamo-ui", "version": f"0.1.{k - 1}"}, indent=2), encoding="utf-8")
        (self.ctx / "static" / "gemeinsam.js").write_text("// gemeinsam\n", encoding="utf-8")
        (self.ctx / "static" / f"app-{k}.js").write_text(f"// fassung {k}\n", encoding="utf-8")
        (self.quelle / "wamo-ui" / "bau.info").write_text(f"WAMO_COMMIT={commit}\nWAMO_SAUBER=ja\n", encoding="ascii")
        bau = f"NODE_IMAGE={self.bild_node}"
        if modus:
            bau += f" ATTRAPPE_MODUS={modus}"
        if nach_s:
            bau += f" ATTRAPPE_NACH_S={nach_s}"
        self.bauargs = bau
        return commit

    def skript(self, *args: str, timeout: float = 900) -> subprocess.CompletedProcess:
        return sh("bash", str(self.quelle / "wamo-ui-umschalten.sh"), *args, env=self.env(), check=False, timeout=timeout)

    def include(self) -> str:
        p = self.etc / "kern" / "wamo-ui-aktiv.inc"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def zustand(self) -> tuple[str, str]:
        m = re.match(r"# live=(\w+) vorige=(\w+)", self.include())
        return (m.group(1), m.group(2)) if m else ("", "")

    def existiert(self, n: str) -> bool:
        return sh("docker", "inspect", n, check=False).returncode == 0

    def laeuft(self, n: str) -> bool:
        return sh("docker", "inspect", "-f", "{{.State.Running}}", n, check=False).stdout.strip() == "true"

    def abbild(self) -> str:
        """Alles, was ein Trockenlauf nicht ändern darf: Include-Datei und Zustand der Container."""
        r = sh("docker", "ps", "-a", "--filter", f"name=^{self.p}", "--format", "{{.Names}} {{.ID}} {{.State}}", check=False)
        return self.include() + "\n" + "\n".join(sorted(r.stdout.splitlines()))


def zeit_aus(ausgabe: str, name: str) -> float | None:
    m = re.search(rf"^ZEIT {name}=([0-9.]+)$", ausgabe, re.M)
    return float(m.group(1)) if m else None


# ======================================================================== innen: Ablauf
class Bericht:
    def __init__(self):
        self.schritte: list[dict] = []
        self.ausfaelle: list[str] = []
        self.sollnull_fehler = 0
        self.sollnull_anfragen = 0

    def neu(self, name: str) -> dict:
        s = {"name": name, "pruefungen": []}
        self.schritte.append(s)
        print(f"\n== {name}", flush=True)
        return s

    def pruefe(self, s: dict, ok: bool, text: str) -> None:
        s["pruefungen"].append({"ok": bool(ok), "text": text})
        if not ok:
            self.ausfaelle.append(f"{s['name']}: {text}")
        print(f"    {'OK    ' if ok else 'FEHLER'} {text}", flush=True)


def ausgabe_kurz(r: subprocess.CompletedProcess) -> str:
    text = (r.stdout + r.stderr).strip().splitlines()
    return "\n".join("      | " + z for z in text[-14:])


def lauf(u: Umgebung, last: Last, b: Bericht, s: dict, args: list[str], rc_soll: int, sollnull: bool = True,
         nachlauf: float = 1.0) -> tuple[subprocess.CompletedProcess, dict]:
    """Ruft das Skript auf, misst Dauer und zählt Fehler der Last im Zeitfenster. sollnull: gehört zum Ziel null Fehler."""
    t0 = time.time()
    r = u.skript(*args)
    t1 = time.time()
    time.sleep(nachlauf)
    f = last.fenster(t0, time.time())
    s.update({"befehl": " ".join(args), "dauer_s": round(t1 - t0, 1), "rc": r.returncode, **f})
    b.pruefe(s, r.returncode == rc_soll, f"Rückgabewert {r.returncode} (erwartet {rc_soll}), Dauer {t1 - t0:.1f} s")
    if r.returncode != rc_soll:
        print(ausgabe_kurz(r))
    print(f"    Last: {f['anfragen']} Anfragen, {f['fehler']} Fehler, {f['wiederholungen']} Wiederholungen einer abgebrochenen "
          f"Ruhe-Verbindung (wie im Browser), p50 {f['p50_ms']} ms, p95 {f['p95_ms']} ms, "
          f"max {f['max_ms']} ms {f['fehler_arten'] if f['fehler'] else ''}", flush=True)
    if sollnull:
        b.sollnull_fehler += f["fehler"]
        b.sollnull_anfragen += f["anfragen"]
        b.pruefe(s, f["fehler"] == 0, "null Fehler unter Last")
    return r, f


def wirkung(last: Last, ausgabe: str, commit: str, s: dict, b: Bericht, start: str = "schalten_beginn") -> None:
    """Zeit von der Entscheidung (Beginn des Umschaltens) bis zur ersten Antwort der neuen Fassung."""
    t = zeit_aus(ausgabe, start)
    ende = zeit_aus(ausgabe, "schalten_ende")
    if t is None:
        return
    erste = last.erste_antwort_mit(commit, t - 0.05)
    s["schalten_ms"] = round((ende - t) * 1000) if ende else None
    s["wirkung_ms"] = round((erste - t) * 1000) if erste else None
    print(f"    Zeit: Include prüfen, tauschen und Caddy neu laden {s['schalten_ms']} ms; "
          f"bis zur ersten Antwort der neuen Fassung {s['wirkung_ms']} ms", flush=True)
    b.pruefe(s, erste is not None, "die neue Fassung hat über Caddy geantwortet")


def eintrag_pruefen(u: Umgebung, ausgabe: str, s: dict, b: Bericht, commit: str, ziel: str, version: str) -> None:
    """Der Eintrag "letzte gute Version" muss das Format von stand.json erfüllen (geprüft mit dem Code des Ankers)."""
    m = re.search(r"^EINTRAG (\{.*\})$", ausgabe, re.M)
    b.pruefe(s, bool(m), "Ausgabezeile EINTRAG vorhanden")
    if not m:
        return
    e = json.loads(m.group(1))
    stand = {"schema": 1, "komponenten": {"ui": {"aktiv": e, "letzte_gute": e}, "kern": {"aktiv": None, "letzte_gute": None}}}
    try:
        u.anker.stand_pruefen(stand)
        ok = True
    except Exception as ex:  # noqa: BLE001
        ok = False
        print("      Anker lehnt den Eintrag ab:", ex)
    b.pruefe(s, ok and e["commit"] == commit and e["ziel"] == ziel and e["version"] == version,
             f"Eintrag im Format von stand.json (Anker akzeptiert ihn): {e}")


def warte_http(host: str, port: int, pfad: str, status: int, sek: float = 30) -> bool:
    ende = time.time() + sek
    while time.time() < ende:
        try:
            c = http.client.HTTPConnection(host, port, timeout=3)
            c.request("GET", pfad)
            if c.getresponse().status == status:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


def innen(args: argparse.Namespace) -> int:
    u = Umgebung(args.name, args.bild_node)
    b = Bericht()
    kern = KernAttrappe(u.kern_port)
    last = Last(u.caddy, u.port)
    erfolg = False
    try:
        _ablauf(u, b, kern, last, args.nur)
        erfolg = not b.ausfaelle
    except Exception as e:  # noqa: BLE001
        b.ausfaelle.append(f"Abbruch: {type(e).__name__}: {e}")
        print("ABBRUCH:", e, flush=True)
    finally:
        if last.laeuft:
            last.stop()
        kern.stop()
        aufraeumen_praefix(u.p, behalte=(u.mein_name, u.p + "etc"))
    print("\n================ Zusammenfassung")
    print(f"Ziel null Fehler beim Umschalten und Rückweg: {b.sollnull_fehler} Fehler bei {b.sollnull_anfragen} Anfragen")
    for z in b.ausfaelle:
        print("FEHLER", z)
    print("ERGEBNIS:", "BESTANDEN" if erfolg else "NICHT BESTANDEN")
    if args.ausgabe:
        Path(args.ausgabe).write_text(json.dumps({"bestanden": erfolg, "sollnull_fehler": b.sollnull_fehler,
                                                  "sollnull_anfragen": b.sollnull_anfragen, "ausfaelle": b.ausfaelle,
                                                  "schritte": b.schritte}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if erfolg else 1


def _ablauf(u: Umgebung, b: Bericht, kern: KernAttrappe, last: Last, nur: str) -> None:
    c1 = commit_von(1)
    c2 = commit_von(2)

    # ---- Aufbau
    s = b.neu("Aufbau: Netze, Container der früheren Fassung (wamo-ui), Include-Datei, Caddy")
    u.aufbauen()
    u.legacy_starten()
    u.etc_schreiben()
    r = u.skript("include-anlegen")
    b.pruefe(s, r.returncode == 0 and u.zustand() == ("alt", "keine"), f"Include-Datei angelegt, live=alt ({u.zustand()})")
    u.caddy_starten()
    b.pruefe(s, warte_http(u.caddy, u.port, "/", 200), "Startseite der früheren Fassung antwortet über Caddy")
    last.setze(chunks={"gemeinsam.js", "app-0.js"}, alt_erlaubt=True)
    last.start()
    time.sleep(3)
    f = last.fenster(0, time.time() + 1)
    b.pruefe(s, f["fehler"] == 0 and f["anfragen"] >= 30, f"Last läuft ohne Fehler ({f['anfragen']} Anfragen, 20 je Sekunde)")

    # ---- Erstumstellung
    s = b.neu("Erstumstellung: wamo-ui (alt) wird ohne Lücke von wamo-ui-blau abgelöst (Fassung 0.1.0)")
    c1 = u.fassung_bereit(1)
    r, f = lauf(u, last, b, s, ["umschalten", "--bewaehrung", "6"], 0)
    b.pruefe(s, u.zustand() == ("blau", "alt"), f"Zustand live=blau vorige=alt ({u.zustand()})")
    b.pruefe(s, u.existiert(u.c_alt) and u.laeuft(u.c_alt), "der frühere Container läuft weiter (Rückweg, Chunks offener Seiten)")
    wirkung(last, r.stdout, c1, s, b)
    eintrag_pruefen(u, r.stdout, s, b, c1, "blau", "v0.1.0")
    last.setze(chunks={"gemeinsam.js", "app-0.js", "app-1.js"}, alt_erlaubt=False)
    time.sleep(1.5)

    # ---- Release
    s = b.neu("Release: Fassung 0.1.1 in der inaktiven Farbe gruen, Umschalten, Bewährung")
    c2 = u.fassung_bereit(2)
    last.setze(chunks={"gemeinsam.js", "app-1.js"})   # Seiten von vor zwei Fassungen (app-0) verlieren ihre Chunks, siehe README 12.6
    r, f = lauf(u, last, b, s, ["umschalten", "--bewaehrung", "6"], 0)
    b.pruefe(s, u.zustand() == ("gruen", "blau"), f"Zustand live=gruen vorige=blau ({u.zustand()})")
    b.pruefe(s, not u.existiert(u.c_alt), "der Container der früheren Fassung ist abgeräumt")
    b.pruefe(s, u.laeuft(u.c_blau), "die alte Farbe blau läuft weiter")
    wirkung(last, r.stdout, c2, s, b)
    eintrag_pruefen(u, r.stdout, s, b, c2, "gruen", "v0.1.1")
    last.setze(chunks={"gemeinsam.js", "app-1.js", "app-2.js"})
    time.sleep(1.5)

    # ---- Rückweg und wieder vorwärts
    s = b.neu("Rückweg: zurueck von gruen auf blau")
    t0 = time.time()
    r, f = lauf(u, last, b, s, ["zurueck"], 0)
    b.pruefe(s, u.zustand() == ("blau", "gruen"), f"Zustand live=blau vorige=gruen ({u.zustand()})")
    b.pruefe(s, s["dauer_s"] < 30, f"Rückweg in {s['dauer_s']} s (Ziel unter 30 Sekunden)")
    wirkung(last, r.stdout, c1, s, b)
    ent = zeit_aus(r.stdout, "start")
    erste = last.erste_antwort_mit(c1, ent - 0.05) if ent else None
    s["entscheidung_bis_wirkung_ms"] = round((erste - ent) * 1000) if erste and ent else None
    print(f"    Zeit: vom Aufruf bis zur ersten Antwort der alten Fassung {s['entscheidung_bis_wirkung_ms']} ms", flush=True)
    s = b.neu("Weg vorwärts: zurueck von blau auf gruen")
    r, f = lauf(u, last, b, s, ["zurueck"], 0)
    b.pruefe(s, u.zustand() == ("gruen", "blau"), f"Zustand live=gruen vorige=blau ({u.zustand()})")
    wirkung(last, r.stdout, c2, s, b)

    # ---- Trockenläufe
    s = b.neu("--trocken ändert nichts")
    vorher = u.abbild()
    for a in (["umschalten", "--trocken"], ["zurueck", "--trocken"], ["bauen", "--trocken"],
              ["anker-rueckweg", "v0.1.0", c1, "blau", "--trocken"]):
        r = u.skript(*a)
        b.pruefe(s, r.returncode == 0 and "Trockenlauf" in r.stdout, f"{' '.join(a[:1])} --trocken: Plan ausgegeben (Rückgabewert {r.returncode})")
    b.pruefe(s, u.abbild() == vorher, "weder Include-Datei noch Container haben sich geändert")

    # ---- Schnittstelle des Notankers
    s = b.neu("Notanker-Schnittstelle: anker-rueckweg (idempotent, schnell, aus dem Archiv)")
    r, f = lauf(u, last, b, s, ["anker-rueckweg", "v0.1.0", c1, "blau"], 0)
    b.pruefe(s, u.zustand() == ("blau", "gruen"), f"Zustand live=blau vorige=gruen ({u.zustand()})")
    wirkung(last, r.stdout, c1, s, b)
    inc = u.include()
    r = u.skript("anker-rueckweg", "v0.1.0", c1, "blau")
    b.pruefe(s, r.returncode == 0 and "Schon erledigt" in r.stdout and u.include() == inc, "zweiter Aufruf: schon erledigt, nichts geändert")
    r, f = lauf(u, last, b, s, ["anker-rueckweg", "v0.1.1", c2, "gruen"], 0)
    b.pruefe(s, u.zustand() == ("gruen", "blau"), f"vorwärts auf gruen ({u.zustand()})")
    sh("docker", "rm", "-f", u.c_blau)
    last.setze(chunks={"gemeinsam.js", "app-2.js"})
    r, f = lauf(u, last, b, s, ["anker-rueckweg", "v0.1.0", c1, "blau"], 0)
    b.pruefe(s, u.zustand() == ("blau", "gruen"), f"Farbe blau fehlte, wurde aus dem Archiv-Image gestartet ({u.zustand()})")
    wirkung(last, r.stdout, c1, s, b)
    last.setze(chunks={"gemeinsam.js", "app-1.js", "app-2.js"})
    inc = u.include()
    r = u.skript("anker-rueckweg", "v0.1.0", "f" * 40, "gruen")
    b.pruefe(s, r.returncode == 6 and u.include() == inc, f"unbekannte Fassung: Rückgabewert 6, nichts geändert ({r.returncode})")
    r = u.skript("anker-rueckweg", "v0.1.1", c2, "blau")
    b.pruefe(s, r.returncode == 6 and u.include() == inc, f"Ziel ist live, trägt aber eine andere Fassung: 6 ({r.returncode})")
    r = u.skript("eintrag")
    eintrag_pruefen(u, "EINTRAG " + r.stdout.strip(), s, b, c1, "blau", "v0.1.0")

    # ---- Prüfung des neuen Containers fällt durch
    s = b.neu("Prüfung vor dem Umschalten: CSP ohne Anmeldung und Kern, nichts wird umgeschaltet")
    # Ab hier baut jeder Versuch in die inaktive Farbe (gruen, trug 0.1.1): deren Chunks (app-2) gehen mit dem Bau verloren
    last.setze(chunks={"gemeinsam.js", "app-1.js"})
    u.fassung_bereit(3, modus="ohne-csp")
    inc = u.include()
    r, f = lauf(u, last, b, s, ["umschalten", "--bewaehrung", "3"], 3)
    b.pruefe(s, u.include() == inc and u.zustand() == ("blau", "gruen"), "Include-Datei unverändert, blau bleibt live")
    b.pruefe(s, not u.laeuft(u.c_gruen), "der fehlerhafte neue Container wurde angehalten")
    s = b.neu("Prüfung vor dem Umschalten: Kern antwortet ohne Anmeldung mit 200 statt 401")
    u.fassung_bereit(4)
    kern.status = 200
    inc = u.include()
    r, f = lauf(u, last, b, s, ["umschalten", "--bewaehrung", "3"], 3)
    kern.status = 401
    b.pruefe(s, u.include() == inc and "HTTP 200" in r.stderr, "Kernprobe lehnt ab, Include-Datei unverändert")
    s = b.neu("Zwei Läufe zugleich: der zweite wird abgewiesen")
    u.fassung_bereit(5)
    p = subprocess.Popen(["bash", str(u.quelle / "wamo-ui-umschalten.sh"), "umschalten", "--bewaehrung", "8"],
                         env={**os.environ, **u.env()}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(2.5)
    r = u.skript("zurueck")
    b.pruefe(s, r.returncode == 1 and "anderer Schaltvorgang" in r.stderr, f"zweiter Lauf: Rückgabewert {r.returncode}, Sperre gemeldet")
    aus, fehl = p.communicate(timeout=300)
    b.pruefe(s, p.returncode == 0, f"der erste Lauf beendet sich in Ordnung ({p.returncode})")
    b.pruefe(s, u.zustand() == ("gruen", "blau"), f"Zustand live=gruen vorige=blau ({u.zustand()})")
    last.setze(chunks={"gemeinsam.js", "app-1.js", "app-5.js"})
    time.sleep(1)

    if nur == "kurz":
        return

    # ---- Bewährung fällt durch
    s = b.neu("Bewährung fällt durch: die neue Fassung stürzt nach dem Umschalten ab (Hinweis: Fehler unter Last werden berichtet)")
    last.setze(chunks={"gemeinsam.js", "app-5.js"})
    u.fassung_bereit(6, modus="absturz", nach_s=14)
    r, f = lauf(u, last, b, s, ["umschalten", "--bewaehrung", "30"], 4, sollnull=False)
    b.pruefe(s, u.zustand() == ("gruen", "keine"), f"automatischer Rückweg: live=gruen ({u.zustand()})")
    b.pruefe(s, not u.laeuft(u.c_blau), "die fehlerhafte Farbe ist angehalten")
    s["hinweis"] = "Absturz: Verbindungsfehler werden von Caddy auf die vorige Farbe umgeleitet"
    s = b.neu("Bewährung fällt durch: die neue Fassung antwortet nach dem Umschalten mit 500")
    u.fassung_bereit(7, modus="fehler", nach_s=14)
    r, f = lauf(u, last, b, s, ["umschalten", "--bewaehrung", "30"], 4, sollnull=False)
    b.pruefe(s, u.zustand() == ("gruen", "keine"), f"automatischer Rückweg: live=gruen ({u.zustand()})")
    s["hinweis"] = "500er der neuen Fassung erreichen Nutzer, bis die Gesundheitsprobe (2 s) sie aus dem Pool nimmt"

    # ---- Ausfall der live gesetzten Farbe
    s = b.neu("Ausfall der live gesetzten Farbe (docker kill) während der Last")
    u.fassung_bereit(8)
    r, f = lauf(u, last, b, s, ["umschalten", "--bewaehrung", "5"], 0)
    last.setze(chunks={"gemeinsam.js", "app-5.js"})
    time.sleep(1)
    t0 = time.time()
    sh("docker", "kill", u.c_blau)
    time.sleep(12)
    f = last.fenster(t0, time.time())
    s.update({"befehl": "docker kill (live)", **f})
    print(f"    Last: {f['anfragen']} Anfragen, {f['fehler']} Fehler {f['fehler_arten'] if f['fehler'] else ''}", flush=True)
    b.pruefe(s, f["fehler"] <= 2, f"höchstens 2 Fehler beim Ausfall der live Farbe ({f['fehler']}), Caddy nimmt die vorige Farbe")
    b.pruefe(s, u.laeuft(u.c_blau), "Docker hat die live Farbe neu gestartet (restart: unless-stopped)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rauchtest für das Umschalten der WAMO-Oberfläche (Attrappen, echtes Caddy)")
    ap.add_argument("--name", default="rauch", help="Namensteil der Testressourcen (wamo-test-NAME-...)")
    ap.add_argument("--bild-node", default="node:22-bookworm-slim", help="Basisbild der Attrappen")
    ap.add_argument("--nur", choices=["alles", "kurz"], default="alles", help="kurz: ohne Absturz, 500er und Ausfall")
    ap.add_argument("--ausgabe", help="Ergebnis als JSON schreiben")
    ap.add_argument("--bild-behalten", action="store_true", help="das Läufer-Image nach dem Lauf behalten")
    ap.add_argument("--innen", action="store_true", help="intern: im Läufer-Container ausführen")
    args = ap.parse_args()
    if not NAME_RE.match(args.name):
        ap.error("--name: 1 bis 12 Zeichen a-z und 0-9")
    if not BILD_RE.match(args.bild_node):
        ap.error("--bild-node: ungültiger Bildname")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return innen(args) if args.innen else aussen(args)


if __name__ == "__main__":
    sys.exit(main())
