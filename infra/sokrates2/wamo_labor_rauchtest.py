#!/usr/bin/env python3
"""Rauchtest für den Labor-Dienst auf Sokrates-2 (Test-Jira des Testlabors, infra/sokrates2/wamo-labor/). Nur Standardbibliothek.

  python3 wamo_labor_rauchtest.py --betrieb --ca CA     prüft den laufenden Betrieb (Container wamo-labor-dienst, wamo-caddy, Firewall,
                                                        Dateien), ändert nichts. Aufgerufen von wamo-labor-einrichten.sh, läuft als root.
  python3 wamo_labor_rauchtest.py --attrappe            läuft ohne Server gegen eine Attrappe der Welt (wamo_labor_rauchtest_attrappe.py, alles grün)
  python3 wamo_labor_rauchtest.py --attrappe --fehler kern_offen,root   mit eingebauten Fehlern: Die Proben dafür müssen rot werden

Es gibt 20 Proben (P01 bis P20). Am Ende steht eine Tabelle mit dem Ergebnis je Probe und, für jede Probe, was sie belegt und was sie NICHT
belegt. Ausgang 0, wenn keine Probe fehlschlägt (eine übersprungene Probe zählt nicht als Fehler, wird aber genannt).

Was der Rauchtest anfasst: Er liest Docker-Zustand, Dateirechte, iptables und die Datei /etc/wamo/core.env, schickt einige Anfragen an 127.0.0.1
(Caddy auf 55447, der Dienst auf 18765) und startet für Proben von einer fremden Adresse einen kurzlebigen Container auf dem Netz von wamo-caddy
(gehärtet, ohne Rechte, nach der Probe weg). Der Dienst bekommt nur lesende Anfragen. Die Token werden aus ihren Dateien gelesen, nur an
Loopback 18765 gesendet (V1 des Kerns, GET) und nie ausgegeben: Weder die Tabelle noch ein Befund noch eine Ausnahme nennt einen Wert.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import socket
import platform
import ssl
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

CONTAINER = "wamo-labor-dienst"
CADDY = "wamo-caddy"
BILD = "wamo-labor-dienst:1"
NETZ_LABOR = "wamo-labor-netz"
NETZ_LOKAL = "wamo-labor-lokal"
NETZ_CADDY = "wamo-caddy_default"
IP_DIENST, IP_LOKAL, IP_CADDY_LABOR = "172.28.0.10", "172.28.1.2", "172.28.0.2"
SITE_IP, PORT_SITE, PORT_LOOPBACK = "192.168.178.136", 55447, 18765
ETC, DATEN, CORE_ENV = "/etc/wamo-labor", "/var/lib/wamo-labor", "/etc/wamo/core.env"
KERN_URL = f"http://127.0.0.1:{PORT_LOOPBACK}"
V1_FELDER = {"durchlauf_id", "offen", "einwilligung_version", "einwilligung_zeit"}
KERN_PFADE = ["/api/kern/durchlauf", "/api/kern/tickets", "//api/kern/durchlauf", "/API/KERN/durchlauf", "/api/%6bern/durchlauf",
              "/api/browser/../kern/durchlauf"]
CODE_UID = "import os; print(os.getuid(), os.getgid())"
CODE_SCHREIBEN = "import os\ntry:\n    open('/app/.rauchtest', 'w').close(); os.unlink('/app/.rauchtest'); print('geschrieben')\nexcept OSError:\n    print('verweigert')"
CODE_STATUS = "print(open('/proc/self/status').read())"
CODE_TCP = ("import socket, sys\ntry:\n    socket.create_connection((sys.argv[1], int(sys.argv[2])), 3).close(); print('offen')\n"
            "except OSError:\n    print('zu')")
CODE_DNS = "import socket, sys\ntry:\n    socket.getaddrinfo(sys.argv[1], 443); print('aufgeloest')\nexcept OSError:\n    print('nicht')"
# Läuft im kurzlebigen Container auf dem Netz von wamo-caddy: eine Anfrage an Caddy von einer fremden Adresse, ohne die Kette zu prüfen
CODE_FREMD = r"""
import json, socket, ssl, sys
ip, port, sni, methode, pfad = sys.argv[1:6]
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
try:
    s = ctx.wrap_socket(socket.create_connection((ip, int(port)), 5), server_hostname=sni)
    s.sendall(f"{methode} {pfad} HTTP/1.1\r\nHost: {sni}:{port}\r\nConnection: close\r\n\r\n".encode())
    roh = b""
    while len(roh) < 65536:
        teil = s.recv(4096)
        if not teil:
            break
        roh += teil
    kopf, _, body = roh.partition(b"\r\n\r\n")
    zeilen = kopf.decode("latin-1").split("\r\n")
    felder = {z.split(":", 1)[0].strip().lower(): z.split(":", 1)[1].strip() for z in zeilen[1:] if ":" in z}
    print(json.dumps({"status": int(zeilen[0].split()[1]), "kopf": felder, "body": body[:300].decode("utf-8", "replace")}))
except Exception as e:
    print(json.dumps({"fehler": type(e).__name__}))
"""


# ======================================================================== Hilfen
@dataclass
class Antwort:
    status: int
    kopf: dict[str, str]
    body: bytes


def antwort_lesen(roh: bytes) -> Antwort | None:
    kopf, trenner, body = roh.partition(b"\r\n\r\n")
    zeilen = kopf.decode("latin-1").split("\r\n")
    m = re.match(r"^HTTP/\d(?:\.\d)? (\d{3})", zeilen[0])
    if not trenner or not m:
        return None
    felder = {}
    for z in zeilen[1:]:
        name, doppelpunkt, wert = z.partition(":")
        if doppelpunkt:
            felder[name.strip().lower()] = wert.strip()
    return Antwort(int(m.group(1)), felder, body)


def env_datei_lesen(roh: bytes) -> dict[str, str]:
    """core.env: Zeilen NAME='wert' (die Werte stehen in einfachen Anführungszeichen), Kommentare und Unbrauchbares werden übergangen."""
    werte = {}
    for zeile in roh.decode("utf-8", "replace").splitlines():
        name, gleich, wert = zeile.partition("=")
        if zeile.startswith("#") or not gleich or not name.strip():
            continue
        werte[name.strip()] = wert[1:-1] if len(wert) >= 2 and wert[0] == wert[-1] == "'" else wert
    return werte


def token_gueltig(wert: str) -> bool:
    """Form und Vielfalt wie die Konfiguration des Dienstes: 43 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-, mindestens 16 verschiedene."""
    return re.fullmatch(r"[A-Za-z0-9._~+/=-]{43,200}", wert) is not None and len(set(wert)) >= 16


def gleich(a: str, b: str) -> bool:
    return bool(a) and hmac.compare_digest(a.encode(), b.encode())


def json_lesen(body: bytes) -> object:
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def von_caddy(a: Antwort | None, status: tuple[int, ...] = (404, 405)) -> bool:
    """Eine Antwort, die Caddy selbst erzeugt hat (Klartext mit den Schutzköpfen und X-Frame-Options), nicht der Dienst (der antwortet mit JSON)."""
    return (a is not None and a.status in status and a.kopf.get("x-frame-options") == "DENY"
            and a.kopf.get("content-type", "").startswith("text/plain"))


class Pruefer:
    """Sammelt die Einzelprüfungen einer Probe."""

    def __init__(self) -> None:
        self.pruefungen: list[tuple[bool, str]] = []
        self.skip: str | None = None

    def pruefe(self, bedingung: bool, text: str) -> bool:
        self.pruefungen.append((bool(bedingung), text))
        return bool(bedingung)


@dataclass
class Ergebnis:
    nr: str
    name: str
    belegt: str
    nicht: str
    ergebnis: str = "OK"
    pruefungen: list[tuple[bool, str]] = field(default_factory=list)
    befunde: list[str] = field(default_factory=list)


PROBEN: list[tuple[str, str, Callable, str, str]] = []


def probe(nr: str, name: str, belegt: str, nicht: str):
    def dekorator(fn: Callable) -> Callable:
        PROBEN.append((nr, name, fn, belegt, nicht))
        return fn
    return dekorator


# ======================================================================== die Welt (alles, was den Server berührt)
class Welt:
    def __init__(self, ca: str | None = None) -> None:
        self.ca = ca

    def sh(self, *cmd: str, timeout: float = 40) -> tuple[int, str, str]:
        try:
            r = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", "Zeitüberschreitung"
        except FileNotFoundError:
            return 127, "", "Programm fehlt"
        return r.returncode, r.stdout, r.stderr

    def _inspect(self, *cmd: str) -> dict | None:
        rc, out, _ = self.sh(*cmd)
        try:
            return json.loads(out)[0] if rc == 0 and out.strip() else None
        except (ValueError, IndexError):
            return None

    def inspect(self, name: str) -> dict | None:
        return self._inspect("docker", "container", "inspect", name)

    def netz(self, name: str) -> dict | None:
        return self._inspect("docker", "network", "inspect", name)

    def im_dienst(self, code: str, *args: str) -> tuple[int, str]:
        rc, out, _ = self.sh("docker", "exec", CONTAINER, "python", "-I", "-c", code, *args, timeout=20)
        return rc, out

    def tcp_vom_dienst(self, host: str, port: int) -> str:
        rc, out = self.im_dienst(CODE_TCP, host, str(port))
        return out.strip() if rc == 0 else "fehler"

    def dns_vom_dienst(self, name: str) -> str:
        rc, out = self.im_dienst(CODE_DNS, name)
        return out.strip() if rc == 0 else "fehler"

    def _anfrage(self, s: socket.socket, host: str, methode: str, pfad: str, kopf: dict[str, str] | None) -> Antwort | None:
        zeilen = "".join(f"{k}: {v}\r\n" for k, v in (kopf or {}).items())
        s.sendall(f"{methode} {pfad} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n{zeilen}\r\n".encode("utf-8"))
        roh = b""
        while len(roh) < 262144:
            teil = s.recv(8192)
            if not teil:
                break
            roh += teil
        return antwort_lesen(roh)

    def caddy(self, methode: str, pfad: str, kopf: dict[str, str] | None = None) -> Antwort | None:
        """Anfrage an Caddy auf 55447 über Loopback, mit der Kette der WAMO-CA und dem Namen der LAN-Adresse geprüft."""
        try:
            ctx = ssl.create_default_context(cafile=self.ca)
            with ctx.wrap_socket(socket.create_connection(("127.0.0.1", PORT_SITE), 5), server_hostname=SITE_IP) as s:
                return self._anfrage(s, f"{SITE_IP}:{PORT_SITE}", methode, pfad, kopf)
        except (OSError, ssl.SSLError):
            return None

    def tls_fehler(self) -> str | None:
        try:
            ctx = ssl.create_default_context(cafile=self.ca)
            with ctx.wrap_socket(socket.create_connection((SITE_IP, PORT_SITE), 5), server_hostname=SITE_IP):
                return None
        except ssl.SSLCertVerificationError as e:
            return e.verify_message
        except (OSError, ssl.SSLError) as e:
            return type(e).__name__

    def loopback(self, methode: str, pfad: str, kopf: dict[str, str] | None = None) -> Antwort | None:
        try:
            with socket.create_connection(("127.0.0.1", PORT_LOOPBACK), 5) as s:
                return self._anfrage(s, f"127.0.0.1:{PORT_LOOPBACK}", methode, pfad, kopf)
        except OSError:
            return None

    def caddy_ip(self) -> str | None:
        i = self.inspect(CADDY)
        return ((i or {}).get("NetworkSettings", {}).get("Networks", {}).get(NETZ_CADDY, {}) or {}).get("IPAddress")

    def _container(self, netz: str, *befehl: str) -> tuple[int, str]:
        """Ein kurzlebiger, gehärteter Container (Image des Dienstes, Python) auf dem Netz `netz`."""
        rc, out, _ = self.sh("docker", "run", "--rm", "--network", netz, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                             "--user", "10001:10001", "--entrypoint", "python", BILD, "-I", "-c", *befehl, timeout=60)
        return rc, out

    def tcp_fremd(self, netz: str, host: str, port: int) -> str:
        rc, out = self._container(netz, CODE_TCP, host, str(port))
        return out.strip().splitlines()[-1] if rc == 0 and out.strip() else "fehler"

    def fremd(self, methode: str, pfad: str) -> Antwort | None:
        """Anfrage an Caddy aus einem kurzlebigen Container auf wamo-caddy_default: Caddy sieht eine fremde Adresse, nicht Loopback."""
        ip = self.caddy_ip()
        if not ip:
            return None
        rc, out = self._container(NETZ_CADDY, CODE_FREMD, ip, str(PORT_SITE), SITE_IP, methode, pfad)
        daten = json_lesen(out.strip().splitlines()[-1].encode()) if rc == 0 and out.strip() else None
        if not isinstance(daten, dict) or "status" not in daten:
            return None
        return Antwort(int(daten["status"]), daten.get("kopf", {}), str(daten.get("body", "")).encode())

    def host_arch(self) -> str:
        roh = platform.machine().lower()
        return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(roh, roh)

    def image_arch(self) -> str:
        rc, out, _ = self.sh("docker", "image", "inspect", "-f", "{{.Architecture}}", BILD)
        return out.strip() if rc == 0 else ""

    def datei(self, pfad: str) -> bytes | None:
        try:
            with open(pfad, "rb") as f:
                return f.read()
        except OSError:
            return None

    def stat(self, pfad: str) -> tuple[int, int] | None:
        try:
            s = os.stat(pfad)
        except OSError:
            return None
        return s.st_mode & 0o777, s.st_uid

    def token(self, art: str) -> str:
        roh = self.datei(f"{ETC}/{art}-token")
        return roh.decode("utf-8", "replace").strip() if roh else ""

    def iptables(self, kette: str) -> list[str] | None:
        rc, out, _ = self.sh("iptables", "-w", "5", "-S", kette)
        return out.splitlines() if rc == 0 else None

    def lauscht(self) -> list[str]:
        rc, out, _ = self.sh("ss", "-tlnH")
        return [z.split()[3] for z in out.splitlines() if len(z.split()) >= 4] if rc == 0 else []


# ======================================================================== die Proben
@probe("P01", "Container läuft und ist gesund",
       "Der Container läuft mit dem Image, das auf dem Server gebaut wurde (Architektur wie der Server), sein Healthcheck (GET /gesundheit im Container) "
       "meldet gesund, er startet nicht ständig neu: Das ist der Beleg, dass das Image gebaut und gestartet ist.",
       "Nicht, dass die Daten stimmen oder die Aufbewahrung funktioniert: gesund heißt nur, dass der Dienst auf seinem Port antwortet.")
def p_gesund(w: Welt, p: Pruefer) -> None:
    i = w.inspect(CONTAINER)
    if not p.pruefe(i is not None, f"Container {CONTAINER} gefunden"):
        return
    st = i.get("State", {})
    p.pruefe(st.get("Running") is True, "Container läuft")
    p.pruefe((st.get("Health") or {}).get("Status") == "healthy", "Healthcheck meldet gesund")
    p.pruefe(i.get("RestartCount", 99) <= 2, f"höchstens zwei Neustarts (bisher {i.get('RestartCount')})")
    p.pruefe(w.image_arch() == w.host_arch(), f"Architektur des Images ({w.image_arch() or 'unbekannt'}) ist die des Servers ({w.host_arch()})")


def status_im_dienst(w: Welt) -> str:
    return w.im_dienst(CODE_STATUS)[1]


@probe("P02", "Benutzer 10001",
       "Der Prozess im Container läuft mit der Kennung 10001:10001 (Konfiguration und tatsächlicher Prozess), nicht als root.",
       "Nicht, dass die Kennung 10001 auf dem Host keinem anderen Konto gehört (das prüft das Einrichtungsskript vorab).")
def p_benutzer(w: Welt, p: Pruefer) -> None:
    i = w.inspect(CONTAINER) or {}
    p.pruefe(i.get("Config", {}).get("User") == "10001:10001", "Config.User ist 10001:10001")
    rc, out = w.im_dienst(CODE_UID)
    p.pruefe(rc == 0 and out.split() == ["10001", "10001"], "der Prozess läuft als uid 10001 und gid 10001")


@probe("P03", "Dateisystem schreibgeschützt",
       "Das Wurzeldateisystem ist read_only, und ein Schreibversuch in /app scheitert wirklich.",
       "Nicht, dass /data schreibbar ist (der Rauchtest schreibt dort nie) und nicht die Ordnerrechte außerhalb des Containers (P09).")
def p_readonly(w: Welt, p: Pruefer) -> None:
    i = w.inspect(CONTAINER) or {}
    p.pruefe(i.get("HostConfig", {}).get("ReadonlyRootfs") is True, "ReadonlyRootfs ist gesetzt")
    rc, out = w.im_dienst(CODE_SCHREIBEN)
    p.pruefe(rc == 0 and out.strip() == "verweigert", "Schreiben nach /app wird verweigert")


@probe("P04", "Keine Capabilities",
       "cap_drop ALL steht in der Konfiguration, es gibt kein cap_add, und der laufende Prozess hat wirklich keine wirksamen Fähigkeiten (CapEff 0).",
       "Nicht, dass der Kernel keine Lücke hat, die Capabilities umgeht (das ist Sache der Host-Updates).")
def p_caps(w: Welt, p: Pruefer) -> None:
    h = (w.inspect(CONTAINER) or {}).get("HostConfig", {})
    p.pruefe(h.get("CapDrop") == ["ALL"], "CapDrop ist genau ALL")
    p.pruefe(not h.get("CapAdd"), "keine zusätzlichen Capabilities")
    m = re.search(r"CapEff:\s*([0-9a-fA-F]+)", status_im_dienst(w))
    p.pruefe(m is not None and int(m.group(1), 16) == 0, "wirksame Capabilities im Prozess: keine")


@probe("P05", "Keine neuen Rechte",
       "no-new-privileges ist gesetzt und im laufenden Prozess wirksam (NoNewPrivs 1), der Container ist nicht privilegiert.",
       "Nicht, dass das Image keine setuid-Dateien enthält (sie wären ohne Wirkung, aber vorhanden).")
def p_nnp(w: Welt, p: Pruefer) -> None:
    h = (w.inspect(CONTAINER) or {}).get("HostConfig", {})
    p.pruefe(any(str(o).startswith("no-new-privileges") for o in h.get("SecurityOpt") or []), "SecurityOpt no-new-privileges")
    p.pruefe(h.get("Privileged") is False, "nicht privilegiert")
    m = re.search(r"NoNewPrivs:\s*(\d)", status_im_dienst(w))
    p.pruefe(m is not None and m.group(1) == "1", "NoNewPrivs ist im Prozess wirksam")


@probe("P06", "Grenzen für Speicher, CPU, Prozesse und Protokolle",
       "256 MB Speicher ohne zusätzlichen Swap, 0,5 CPU, höchstens 128 Prozesse, Container-Protokolle auf 3 Dateien zu 10 MB begrenzt.",
       "Nicht, ob die Grenzen für die Last von 8 Plätzen reichen (das zeigt der Lasttest) und nicht den freien Platz auf dem Datenträger.")
def p_grenzen(w: Welt, p: Pruefer) -> None:
    h = (w.inspect(CONTAINER) or {}).get("HostConfig", {})
    p.pruefe(h.get("Memory") == 268435456 and h.get("MemorySwap") == 268435456, "Speicher 256 MB, Swap gleich (kein zusätzlicher Swap)")
    p.pruefe(h.get("NanoCpus") == 500000000, "0,5 CPU")
    p.pruefe(h.get("PidsLimit") == 128, "höchstens 128 Prozesse")
    log = h.get("LogConfig", {})
    p.pruefe(log.get("Type") == "json-file" and log.get("Config", {}).get("max-size") == "10m" and log.get("Config", {}).get("max-file") == "3",
             "Protokoll json-file mit max-size 10m und max-file 3")


@probe("P07", "Netz und Adressen",
       "Der Dienst hängt genau in wamo-labor-netz (intern, 172.28.0.10) und wamo-labor-lokal (172.28.1.2), Caddy hat 172.28.0.2 im internen Netz, "
       "und der Dienst vertraut als Proxy genau dieser Adresse.",
       "Nicht, dass es keine Überschneidung mit späteren Docker-Netzen gibt, und nicht den Ausgang (P14) oder die Firewall (P15).")
def p_netze(w: Welt, p: Pruefer) -> None:
    i = w.inspect(CONTAINER) or {}
    netze = i.get("NetworkSettings", {}).get("Networks", {})
    p.pruefe(sorted(netze) == sorted([NETZ_LABOR, NETZ_LOKAL]), "genau die Netze wamo-labor-netz und wamo-labor-lokal")
    p.pruefe(netze.get(NETZ_LABOR, {}).get("IPAddress") == IP_DIENST and netze.get(NETZ_LOKAL, {}).get("IPAddress") == IP_LOKAL, "feste Adressen")
    intern = w.netz(NETZ_LABOR) or {}
    p.pruefe(intern.get("Internal") is True, "wamo-labor-netz ist intern (kein Weg nach außen)")
    p.pruefe((w.netz(NETZ_LOKAL) or {}).get("Internal") is False, "wamo-labor-lokal ist nicht intern (nur für die Veröffentlichung auf Loopback)")
    c = (w.inspect(CADDY) or {}).get("NetworkSettings", {}).get("Networks", {}).get(NETZ_LABOR, {}).get("IPAddress")
    p.pruefe(c == IP_CADDY_LABOR, "Caddy hat im internen Netz die feste Adresse 172.28.0.2")
    env = dict(e.split("=", 1) for e in i.get("Config", {}).get("Env") or [] if "=" in e)
    p.pruefe(env.get("WAMO_LABOR_DIENST_PROXYS") == c, "der Dienst vertraut als Proxy genau der Adresse von Caddy")


@probe("P08", "Port 18765 nur auf Loopback",
       "Der Container veröffentlicht genau einen Port (8080 nach 127.0.0.1:18765), und auf dem Host lauscht 18765 nur auf 127.0.0.1.",
       "Nicht die Firewall und nicht, ob ein anderer Dienst denselben Port von einer anderen Adresse belegt (dann wäre der Start gescheitert).")
def p_port(w: Welt, p: Pruefer) -> None:
    pb = (w.inspect(CONTAINER) or {}).get("HostConfig", {}).get("PortBindings") or {}
    p.pruefe(pb == {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(PORT_LOOPBACK)}]}, "genau 8080/tcp auf 127.0.0.1:18765 veröffentlicht")
    lauscht = [a for a in w.lauscht() if a.rsplit(":", 1)[-1] == str(PORT_LOOPBACK)]
    p.pruefe(lauscht == [f"127.0.0.1:{PORT_LOOPBACK}"], f"auf dem Host lauscht 18765 nur auf 127.0.0.1 ({', '.join(lauscht) or 'nichts'})")


@probe("P09", "Dateirechte und Token",
       "Beide Token-Dateien gehören 10001 mit Modus 0400, der Datenordner gehört 10001 mit 0700, beide Token haben die Form, die der Dienst verlangt, "
       "und sie sind verschieden.",
       "Nicht, dass die Token geheim geblieben sind (wer sie schon gesehen hat) und nicht den Inhalt der Datenbanken oder Sicherungen außerhalb.")
def p_dateien(w: Welt, p: Pruefer) -> None:
    for pfad in (f"{ETC}/kern-token", f"{ETC}/betreuer-token"):
        p.pruefe(w.stat(pfad) == (0o400, 10001), f"{pfad}: Modus 0400, Besitzer 10001")
    p.pruefe(w.stat(DATEN) == (0o700, 10001), f"{DATEN}: Modus 0700, Besitzer 10001")
    kern, betreuer = w.token("kern"), w.token("betreuer")
    p.pruefe(token_gueltig(kern) and token_gueltig(betreuer), "beide Token haben die verlangte Form (43 bis 200 Zeichen, mindestens 16 verschiedene)")
    p.pruefe(bool(kern) and not gleich(kern, betreuer), "Kern-Token und Betreuer-Token sind verschieden")


KOEPFE = {"x-content-type-options": "nosniff", "referrer-policy": "no-referrer"}


def fehlende_koepfe(a: Antwort) -> list[str]:
    fehlt = [k for k, v in KOEPFE.items() if a.kopf.get(k, "").lower() != v]
    if "no-store" not in a.kopf.get("cache-control", ""):
        fehlt.append("cache-control")
    if "frame-ancestors 'none'" not in a.kopf.get("content-security-policy", ""):
        fehlt.append("content-security-policy")
    return fehlt


@probe("P10", "HTTPS 55447 und Schutzköpfe der Seiten",
       "Caddy antwortet auf 55447 mit einem Zertifikat, das die WAMO-CA beglaubigt und das zur LAN-Adresse passt, und die Seiten / und /board "
       "tragen die Schutzköpfe des Dienstes (nosniff, no-referrer, no-store, CSP mit frame-ancestors).",
       "Nicht, dass die Plätze von anderen Rechnern im Büro-LAN durch die Firewall kommen (nur von Hand: https://192.168.178.136:55447/board "
       "von einem Platz-Laptop öffnen) und nicht, dass die Plätze die CA importiert haben.")
def p_seiten(w: Welt, p: Pruefer) -> None:
    fehler = w.tls_fehler()
    p.pruefe(fehler is None, "TLS-Kette bis zur WAMO-CA gültig, Name 192.168.178.136" + (f" ({fehler})" if fehler else ""))
    for pfad in ("/", "/board"):
        a = w.caddy("GET", pfad)
        if p.pruefe(a is not None and a.status == 200, f"GET {pfad} über Caddy: 200"):
            fehlt = fehlende_koepfe(a)
            p.pruefe(not fehlt, f"{pfad}: Schutzköpfe vollständig" + (f" (es fehlt: {', '.join(fehlt)})" if fehlt else ""))


def beschreibe(a: Antwort | None) -> str:
    return f"Status {a.status}" if a else "keine Antwort"


@probe("P11", "/api/kern/ geht nie über Caddy",
       "Der Pfad des Kerns und seine Schreibweisen (doppelter Schrägstrich, Großschreibung, %-Kodierung, Punkte) und andere Methoden werden von "
       "Caddy selbst abgewiesen (404 oder 405 mit den Schutzköpfen), der Dienst bekommt nie eine Anfrage dazu.",
       "Nicht jede denkbare Schreibweise (die Liste ist endlich) und nicht den Weg über Loopback 18765, der für den Kern gewollt offen ist (P16).")
def p_kern(w: Welt, p: Pruefer) -> None:
    for pfad in KERN_PFADE:
        a = w.caddy("GET", pfad)
        p.pruefe(von_caddy(a), f"GET {pfad}: von Caddy abgewiesen ({beschreibe(a)})")
    a = w.caddy("DELETE", "/api/kern/tickets/LAB-1")
    p.pruefe(von_caddy(a), f"DELETE /api/kern/tickets/LAB-1: von Caddy abgewiesen ({beschreibe(a)})")


@probe("P12", "Betreuer-Pfade nur von den Betreuer-Adressen",
       "Von einer fremden Adresse (kurzlebiger Container auf dem Netz von wamo-caddy) antwortet Caddy auf /betreuer und /api/betreuer/ mit 403; "
       "die Kontrolle (/board von derselben Adresse) geht durch, und von Loopback erreicht man den Dienst (er antwortet ohne Token mit 401).",
       "Nicht die Adressen der echten Betreuer-PCs (die stehen in /etc/wamo-labor/betreuer-adressen; ein Betreuer-PC muss es von Hand ausprobieren).")
def p_betreuer(w: Welt, p: Pruefer) -> None:
    kontrolle = w.fremd("GET", "/board")
    if not p.pruefe(kontrolle is not None and kontrolle.status == 200, f"Kontrolle: /board von der fremden Adresse ({beschreibe(kontrolle)})"):
        return
    for methode, pfad in (("GET", "/betreuer"), ("GET", "/api/betreuer/plaetze"), ("POST", "/api/betreuer/plaetze/3/neuer-durchlauf")):
        a = w.fremd(methode, pfad)
        p.pruefe(von_caddy(a, (403,)), f"{methode} {pfad} von der fremden Adresse: 403 von Caddy ({beschreibe(a)})")
    intern = w.caddy("GET", "/api/betreuer/plaetze")
    daten = json_lesen(intern.body) if intern else None
    p.pruefe(intern is not None and intern.status == 401 and isinstance(daten, dict) and daten.get("fehler") == "token_ungueltig",
             f"Gegenprobe von Loopback: der Dienst antwortet selbst mit 401 ({beschreibe(intern)})")


@probe("P13", "/gesundheit nur intern",
       "Von einer fremden Adresse antwortet Caddy auf /gesundheit mit 403, von Loopback antwortet der Dienst mit seinem Zustand.",
       "Nicht, dass /gesundheit im Container selbst nur lokal erreichbar ist (der Healthcheck braucht es dort).")
def p_gesundheit(w: Welt, p: Pruefer) -> None:
    a = w.fremd("GET", "/gesundheit")
    p.pruefe(von_caddy(a, (403,)), f"von der fremden Adresse: 403 von Caddy ({beschreibe(a)})")
    i = w.caddy("GET", "/gesundheit")
    p.pruefe(i is not None and i.status == 200 and b"ok" in i.body.lower(), f"von Loopback: Zustand ok ({beschreibe(i)})")


@probe("P14", "Kein Weg nach außen",
       "Aus dem Container sind das Internet (1.1.1.1:443) und das Büro-LAN (Router 192.168.178.1:80) nicht erreichbar und eine Namensauflösung "
       "nach außen scheitert; die Kontrolle (Caddy im internen Netz) ist erreichbar, sonst wäre die Probe wertlos.",
       "Nicht den Zugriff auf Dienste des Hosts über die Adresse der Bridge (172.28.1.1: INPUT ist nicht gesperrt) und nicht SMTP im Einzelnen.")
def p_ausgang(w: Welt, p: Pruefer) -> None:
    kontrolle = w.tcp_vom_dienst(IP_CADDY_LABOR, PORT_SITE)
    if not p.pruefe(kontrolle == "offen", f"Kontrolle: Caddy im internen Netz ist vom Dienst aus erreichbar ({kontrolle})"):
        return
    for ziel, text in ((("1.1.1.1", 443), "Internet"), (("192.168.178.1", 80), "Büro-LAN (Router)")):
        antwort = w.tcp_vom_dienst(*ziel)
        p.pruefe(antwort == "zu", f"{text} {ziel[0]}:{ziel[1]} ist vom Dienst aus nicht erreichbar ({antwort})")
    dns = w.dns_vom_dienst("example.org")
    p.pruefe(dns == "nicht", f"Namensauflösung nach außen scheitert ({dns})")


def sicherer_return(zeile: str) -> bool:
    """Eine Quelle der Liste: mit -s, nie die ganze Welt."""
    m = re.search(r"-s (\S+)", zeile)
    return m is not None and not m.group(1).startswith("0.0.0.0")


@probe("P15", "Firewall-Regeln für 55447",
       "Die Kette WAMO-FW leitet 55447 in WAMO-FW-LABOR (Quellen nur mit -s, am Ende DROP) und das Netz wamo-labor-lokal in WAMO-FW-LABAUS "
       "(nur DROP, nie RETURN oder ACCEPT), INPUT schickt es in WAMO-FW-LABIN (nur Antworten), und 18765 wird von außerhalb Loopback verworfen.",
       "Nicht, ob die Quelladressen die richtigen Plätze sind (Frage Q13, bis dahin das Büro-LAN) und nicht, dass die Regeln einen Neustart überstehen.")
def p_firewall(w: Welt, p: Pruefer) -> None:
    haupt = w.iptables("WAMO-FW") or []
    p.pruefe(any(f"ctorigdstport {PORT_SITE}" in z and z.endswith("-j WAMO-FW-LABOR") for z in haupt), "55447 geht in die Kette WAMO-FW-LABOR")
    p.pruefe(any("-s 172.28.1.0/29" in z and z.endswith("-j WAMO-FW-LABAUS") for z in haupt), "das Netz wamo-labor-lokal geht in WAMO-FW-LABAUS")
    labor = [z for z in (w.iptables("WAMO-FW-LABOR") or []) if z.startswith("-A ")]
    p.pruefe(bool(labor) and labor[-1].endswith("-j DROP"), "WAMO-FW-LABOR endet mit DROP")
    rueck = [z for z in labor if z.endswith("-j RETURN")]
    p.pruefe(bool(rueck) and all(sicherer_return(z) for z in rueck), f"{len(rueck)} Quellen, jede mit -s und nie 0.0.0.0/0")
    aus = [z for z in (w.iptables("WAMO-FW-LABAUS") or []) if z.startswith("-A ")]
    p.pruefe(bool(aus) and aus[-1].endswith("-j DROP") and not any(("RETURN" in z or "ACCEPT" in z) for z in aus), "WAMO-FW-LABAUS verwirft alles")
    p.pruefe(any("-s 172.28.1.0/29" in z and z.endswith("-j WAMO-FW-LABIN") for z in (w.iptables("INPUT") or [])), "INPUT: das lokale Netz geht in WAMO-FW-LABIN")
    labin = [z for z in (w.iptables("WAMO-FW-LABIN") or []) if z.startswith("-A ")]
    p.pruefe(bool(labin) and "ESTABLISHED" in labin[0] and labin[-1].endswith("-j DROP"), "WAMO-FW-LABIN lässt nur Antworten durch und verwirft den Rest")
    p.pruefe(any(f"ctorigdstport {PORT_LOOPBACK}" in z and "! -s 127.0.0.0/8" in z and z.endswith("-j DROP") for z in haupt),
             "Port 18765 wird von außerhalb von Loopback verworfen")


@probe("P16", "Roundtrip V1 des Kerns über Loopback",
       "GET /api/kern/durchlauf auf 127.0.0.1:18765 (Platz 15, nur lesend): ohne Bearer 401, mit falschem Bearer 401, mit dem Kern-Token 200 "
       "mit genau den Feldern des Vertrags. Der Dienst läuft also mit diesem Token und antwortet im Format des Vertrags.",
       "Nicht, dass der laufende Kern denselben Wert schon geladen hat (erst nach dem nächsten Kern-Release) und nicht V2 bis V7 (das prüft das Abnahmeskript).")
def p_roundtrip(w: Welt, p: Pruefer) -> None:
    token = w.token("kern")
    if not p.pruefe(bool(token), "Kern-Token gelesen"):
        return
    pfad, platz = "/api/kern/durchlauf", {"X-WAMO-Platz": "15"}
    ohne = w.loopback("GET", pfad, platz)
    p.pruefe(ohne is not None and ohne.status == 401, f"ohne Bearer: 401 ({beschreibe(ohne)})")
    falsch = w.loopback("GET", pfad, {**platz, "Authorization": "Bearer " + token[::-1]})
    p.pruefe(falsch is not None and falsch.status == 401, f"mit falschem Bearer: 401 ({beschreibe(falsch)})")
    gut = w.loopback("GET", pfad, {**platz, "Authorization": "Bearer " + token})
    daten = json_lesen(gut.body) if gut is not None and gut.status == 200 else None
    p.pruefe(isinstance(daten, dict) and set(daten) == V1_FELDER, f"mit dem Kern-Token: 200 mit genau den Feldern von V1 ({beschreibe(gut)})")


@probe("P17", "Einstellungen des Kerns in core.env",
       "/etc/wamo/core.env enthält WAMO_LABOR_URL=http://127.0.0.1:18765 und einen WAMO_LABOR_TOKEN, der mit der Token-Datei übereinstimmt "
       "(Vergleich ohne Ausgabe).",
       "Nicht, dass der laufende Kern die Werte schon kennt: Er liest sie erst mit dem nächsten Kern-Release (wamo-core-slots.sh umschalten-kern).")
def p_core_env(w: Welt, p: Pruefer) -> None:
    roh = w.datei(CORE_ENV)
    if not p.pruefe(roh is not None, f"{CORE_ENV} lesbar"):
        return
    env = env_datei_lesen(roh)
    p.pruefe(env.get("WAMO_LABOR_URL") == KERN_URL, "WAMO_LABOR_URL ist http://127.0.0.1:18765")
    p.pruefe(gleich(env.get("WAMO_LABOR_TOKEN", ""), w.token("kern")), "WAMO_LABOR_TOKEN stimmt mit der Token-Datei überein")


@probe("P18", "Aufbewahrung und keine Token in der Umgebung",
       "WAMO_LABOR_DIENST_BIS ist der 11.12.2026, und in docker inspect (Umgebung, Befehl, Beschriftungen) steht kein Token im Klartext.",
       "Nicht, dass die Aufbewahrung am 11.12.2026 wirklich löscht (der Dienst muss dann laufen, sonst der Löschlauf) und nicht Sicherungen außerhalb.")
def p_aufbewahrung(w: Welt, p: Pruefer) -> None:
    i = w.inspect(CONTAINER) or {}
    env = dict(e.split("=", 1) for e in i.get("Config", {}).get("Env") or [] if "=" in e)
    p.pruefe(env.get("WAMO_LABOR_DIENST_BIS") == "2026-12-11", "WAMO_LABOR_DIENST_BIS ist 2026-12-11")
    blob = json.dumps(i)
    for art in ("kern", "betreuer"):
        token = w.token(art)
        p.pruefe(not (token and token in blob), f"der {art}-Token steht nicht in docker inspect")


@probe("P19", "Dienst aus fremdem Container nicht erreichbar (A10)",
       "Aus einem fremden Container (kurzlebig, auf dem Netz von wamo-caddy) sind 172.28.0.10:8080 und 172.28.1.2:8080, die Adressen des Dienstes, nicht "
       "erreichbar (A10 des Vertrags, Teil Netz); die Kontrolle (Caddy auf 55447 von derselben Stelle) ist erreichbar.",
       "Nicht den Weg von anderen Rechnern im Büro-LAN (sie erreichen nur 55447) und nicht den ganzen A10, der von Hand mit den Kollegen am Server läuft.")
def p_fremd_dienst(w: Welt, p: Pruefer) -> None:
    ip = w.caddy_ip()
    kontrolle = w.tcp_fremd(NETZ_CADDY, ip, PORT_SITE) if ip else "keine Adresse"
    if not p.pruefe(kontrolle == "offen", f"Kontrolle: Caddy {ip}:55447 ist aus dem fremden Container erreichbar ({kontrolle})"):
        return
    for host in (IP_DIENST, IP_LOKAL):
        antwort = w.tcp_fremd(NETZ_CADDY, host, 8080)
        p.pruefe(antwort == "zu", f"{host}:8080 ist aus dem fremden Container nicht erreichbar ({antwort})")


@probe("P20", "Host-Dienste nicht über die Bridge-Adresse",
       "Aus einem Container auf wamo-labor-lokal (wie der Dienst) ist 172.28.1.1:22, die Bridge-Adresse des Hosts, nicht erreichbar (Kette WAMO-FW-LABIN); "
       "die Kontrolle (der Dienst 172.28.1.2:8080 von derselben Stelle) ist erreichbar.",
       "Nicht andere Ports des Hosts, nicht die Wirkung bei gestoppter Firewall (der Ausgang hängt allein an wamo-firewall) und nicht einen Neustart des Hosts.")
def p_bridge_host(w: Welt, p: Pruefer) -> None:
    kontrolle = w.tcp_fremd(NETZ_LOKAL, IP_LOKAL, 8080)
    if not p.pruefe(kontrolle == "offen", f"Kontrolle: der Dienst {IP_LOKAL}:8080 ist aus dem lokalen Netz erreichbar ({kontrolle})"):
        return
    antwort = w.tcp_fremd(NETZ_LOKAL, "172.28.1.1", 22)
    p.pruefe(antwort == "zu", f"172.28.1.1:22 (Bridge-Adresse des Hosts) ist aus dem lokalen Netz nicht erreichbar ({antwort})")


# ======================================================================== Attrappe, Ablauf und Ausgabe
def attrappe_klasse():
    """Die Attrappe liegt in einer eigenen Datei neben diesem Skript (python3 -I nimmt den Ordner des Skripts nicht in den Suchpfad)."""
    ordner = os.path.dirname(os.path.abspath(__file__))
    if ordner not in sys.path:
        sys.path.insert(0, ordner)
    import wamo_labor_rauchtest_attrappe

    return wamo_labor_rauchtest_attrappe.Attrappe


def __getattr__(name: str):
    if name == "Attrappe":
        return attrappe_klasse()
    raise AttributeError(name)


HAND = [
    "Dass Caddy echte LAN-Adressen sieht: Das lässt sich nur von einem Büro-PC prüfen. Von dort https://192.168.178.136:55447/betreuer und /gesundheit "
    "öffnen, beide müssen 403 antworten (ein Betreuer-PC aus betreuer-adressen sieht /betreuer, ohne Token mit 401). Dieser Rauchtest behauptet das nicht: "
    "Er sendet von Loopback und aus Containern.",
    "Dass die Plätze 55447 durch die Firewall erreichen (Adressliste, Frage Q13) und die WAMO-CA importiert haben: von einem Platz-Laptop "
    "https://192.168.178.136:55447/board öffnen.",
    "A10 des Vertrags mit den Kollegen am Server: P14, P19 und P20 sind nur die Vorarbeit.",
]

def alle_proben(welt: Welt) -> list[Ergebnis]:
    ergebnisse = []
    for nr, name, fn, belegt, nicht in PROBEN:
        pruefer = Pruefer()
        e = Ergebnis(nr, name, belegt, nicht)
        try:
            fn(welt, pruefer)
        except Exception as ex:  # eine Probe darf nie die anderen stoppen; von der Ausnahme steht nur der Typ da, nie ihr Text (Token!)
            pruefer.pruefe(False, f"Ausnahme {type(ex).__name__} in der Probe")
        e.pruefungen = pruefer.pruefungen
        e.befunde = [text for ok, text in pruefer.pruefungen if not ok]
        e.ergebnis = "FEHLER" if e.befunde else ("UEBERSPRUNGEN" if pruefer.skip else "OK")
        ergebnisse.append(e)
    return ergebnisse


def ausgeben(ergebnisse: list[Ergebnis]) -> int:
    print(f"\n{'Nr':<4} {'Probe':<50} Ergebnis")
    for e in ergebnisse:
        print(f"{e.nr:<4} {e.name:<50} {e.ergebnis}")
        print(f"     belegt:       {e.belegt}")
        print(f"     belegt nicht: {e.nicht}")
        for ok, text in e.pruefungen:
            print(f"       {'ok    ' if ok else 'FEHLER'} {text}")
    n_ok = sum(1 for e in ergebnisse if e.ergebnis == "OK")
    n_fehler = sum(1 for e in ergebnisse if e.ergebnis == "FEHLER")
    n_skip = sum(1 for e in ergebnisse if e.ergebnis == "UEBERSPRUNGEN")
    print(f"\n{n_ok} OK, {n_fehler} FEHLER, {n_skip} übersprungen")
    print("\nVon Hand zu prüfen (der Rauchtest behauptet es nicht):")
    for z in HAND:
        print(f"  - {z}")
    return 1 if n_fehler else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rauchtest für den Labor-Dienst auf Sokrates-2")
    ap.add_argument("--betrieb", action="store_true", help="den laufenden Betrieb prüfen (als root)")
    ap.add_argument("--ca", help="Datei der WAMO-CA (wamo-ca.crt)")
    ap.add_argument("--attrappe", action="store_true", help="gegen eine Attrappe der Welt laufen (ohne Server)")
    ap.add_argument("--fehler", default="", help="mit --attrappe: eingebaute Fehler, durch Komma getrennt")
    a = ap.parse_args(argv)
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")  # Umlaute auch bei einer Ausgabe in eine Pipe oder Datei
    if a.attrappe:
        fehler = [x for x in a.fehler.split(",") if x]
        print("Rauchtest gegen die Attrappe" + (f" mit eingebauten Fehlern: {', '.join(fehler)}" if fehler else ""))
        return ausgeben(alle_proben(attrappe_klasse()(fehler)))
    if not a.betrieb:
        ap.error("--betrieb oder --attrappe angeben")
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("Hinweis: Ohne root lassen sich Token, Firewall und docker oft nicht lesen, viele Proben schlagen dann fehl.", file=sys.stderr)
    return ausgeben(alle_proben(Welt(a.ca)))


if __name__ == "__main__":
    sys.exit(main())
