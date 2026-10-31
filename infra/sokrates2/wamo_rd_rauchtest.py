#!/usr/bin/env python3
"""Rauchtest für das Remote-Desktop-Gateway (infra/sokrates2/wamo-rd/, Stufe 1 des Konzepts docs/remote-desktop.md).

Zwei Betriebsarten:

  python wamo_rd_rauchtest.py                      Testmodus: baut aus der echten Compose-Datei und der echten Caddy-Vorlage einen
                                                   eigenen Stack mit dem Namen wamo-test-NAME-... (Netze mit anderen Adressen),
                                                   prüft ihn und räumt ihn wieder ab. Läuft am PC mit Docker Desktop wie auf dem
                                                   Server. Angelegt werden nur Container, Netze und Volumes mit diesem Präfix,
                                                   jede Löschung prüft den Präfix. Fremde Container werden nie angefasst.
  python wamo_rd_rauchtest.py --betrieb --ca CA    Prüft die laufenden Container auf Sokrates-2 (wamo-rd-web, wamo-rd-guacd,
                                                   wamo-caddy), ändert nichts. Aufgerufen von wamo-rd-einrichten.sh.

Geprüft wird: Container gesund und gehärtet (read-only, ohne Capabilities, ohne neue Rechte, ohne Root, Grenzen, kein
veröffentlichter Port, richtige Netze, Schlüssel nicht in der Umgebung), Antwortköpfe der Gateway-Seite (CSP, kein iframe,
kein Zwischenspeicher, Zwischenablage gesperrt), Isolation (kein Ausgang der Webanwendung ins Internet, ins LAN oder zum Host;
guacd erreicht nur die Ziele), Verbotsproben (Anmeldung ohne gültiges JSON, falsche Signatur, abgelaufenes JSON, JSON von einer
fremden Adresse, Methoden). Nur im Testmodus zusätzlich der positive Weg mit einem Zugangsdienst-Attrappe-Container in der
festen vertrauten Adresse (gültiges JSON gibt ein Token, Abmelden macht es ungültig).

Stufe 3 (Zugangsdienst wamo-rd-zugang) und die Ausgangsregel für das Konto wamo-rd (infra/sparks/wamo-rd-ausgang-einrichten.sh) gehören
dazu: Im Testmodus startet der echte Zugangsdienst aus der Compose-Datei (ohne Anmeldung, die Probe nutzt eine Attrappe); mit --betrieb
prüft der Rauchtest zusätzlich Härtung und Netze des Zugangsdienstes und, falls die Tabelle inet wamo_rd_ausgang geladen ist, als
Benutzer wamo-rd (sudo -n -u wamo-rd) die Verbotsproben: kein Kern-Port, keine Supabase-Ports, kein Router, keine anderen Sparks,
kein Loopback-Dienst, kein IPv6, dagegen Internet und DNS. Jede Verbotsprobe hat eine Kontrolle (derselbe Weg als wamocon ist offen),
sonst zählt sie nicht als bestanden.

Rückgabe 0, wenn nichts fehlschlug (übersprungene Proben zählen nicht als Fehler, werden aber genannt).
"""
from __future__ import annotations

import argparse
import base64
import http.client as httpclient
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

HIER = Path(__file__).resolve().parent
COMPOSE = HIER / "wamo-rd" / "docker-compose.yml"
VORLAGE = HIER / "wamo-rd" / "wamo-rd.caddy.vorlage"
CADDY_IMAGE = "caddy:2.11.2-alpine@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d"
ATTRAPPE_IMAGE = "python:3.12-slim"
BETRIEB_URL = "https://192.168.178.136:55446"
NAME_RE = re.compile(r"^[a-z0-9]{1,12}$")

# Im Zugangsdienst-Attrappe-Container: baut das signierte und verschlüsselte JSON ("Encrypted JSON authentication") und
# reicht es bei der Webanwendung ein. Kommandozeile: Ziel-URL, Modus (gut, falsch, abgelaufen), Schlüsseldatei.
ATTRAPPE_CODE = r"""
import base64, json, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
url, modus, keydatei = sys.argv[1:4]
xff = sys.argv[4] if len(sys.argv) > 4 else ""
key = open(keydatei).read().strip()
jetzt = int(time.time() * 1000)
daten = {"username": "rauchtest", "expires": jetzt + (-60000 if modus == "abgelaufen" else 60000),
         "connections": {"test": {"protocol": "rdp", "parameters": {"hostname": "192.0.2.1", "port": "3389",
                                                                  "ignore-cert": "true"}}}}
roh = json.dumps(daten).encode()
sig = subprocess.run(["openssl", "dgst", "-sha256", "-mac", "HMAC", "-macopt", "hexkey:" + (key if modus != "falsch" else "0" * 32),
                      "-binary"], input=roh, capture_output=True, check=True).stdout
enc = subprocess.run(["openssl", "enc", "-aes-128-cbc", "-K", key, "-iv", "0" * 32], input=sig + roh,
                     capture_output=True, check=True).stdout
form = urllib.parse.urlencode({"data": base64.b64encode(enc).decode()}).encode()
try:
    kopf = {"X-Forwarded-For": xff} if xff else {}
    r = urllib.request.urlopen(urllib.request.Request(url + "/api/tokens", data=form, headers=kopf), timeout=10)
    print(r.status, r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:200])
"""


# ======================================================================== Hilfen
def sh(*cmd: str, check: bool = False, timeout: float = 180, eingabe: str | None = None,
       env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           input=eingabe, env={**os.environ, **(env or {})})
    except subprocess.TimeoutExpired:
        r = subprocess.CompletedProcess(list(cmd), 124, "", "Zeitüberschreitung")
    except FileNotFoundError as ex:
        r = subprocess.CompletedProcess(list(cmd), 127, "", f"Programm fehlt: {ex}")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


class Ergebnis:
    def __init__(self) -> None:
        self.zeilen: list[tuple[str, str, str]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.zeilen.append(("OK", name, detail))
        print(f"  ok       {name}" + (f"  ({detail})" if detail else ""))

    def fehler(self, name: str, detail: str = "") -> None:
        self.zeilen.append(("FEHLER", name, detail))
        print(f"  FEHLER   {name}" + (f"  ({detail})" if detail else ""), file=sys.stderr)

    def skip(self, name: str, grund: str) -> None:
        self.zeilen.append(("UEBERSPRUNGEN", name, grund))
        print(f"  skip     {name}  ({grund})")

    def pruefe(self, bedingung: bool, name: str, detail: str = "") -> bool:
        (self.ok if bedingung else self.fehler)(name, detail)
        return bedingung

    def summe(self) -> int:
        n_fehler = sum(1 for z in self.zeilen if z[0] == "FEHLER")
        n_ok = sum(1 for z in self.zeilen if z[0] == "OK")
        n_skip = sum(1 for z in self.zeilen if z[0] == "UEBERSPRUNGEN")
        print(f"\n{n_ok} bestanden, {n_fehler} fehlgeschlagen, {n_skip} übersprungen")
        return 1 if n_fehler else 0


def inspect(name: str) -> dict | None:
    r = sh("docker", "container", "inspect", name)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)[0]


def http(url: str, methode: str = "GET", daten: dict | None = None, ca: str | None = None, kopf: dict | None = None,
         timeout: float = 15) -> tuple[int, dict[str, list[str]], bytes]:
    u = urlsplit(url)
    if u.scheme == "https":
        ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
        verb = httpclient.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=ctx)
    else:
        verb = httpclient.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    koerper = urlencode(daten).encode() if daten is not None else None
    k = dict(kopf or {})
    if koerper is not None:
        k.setdefault("Content-Type", "application/x-www-form-urlencoded")
    try:
        verb.request(methode, (u.path or "/") + (f"?{u.query}" if u.query else ""), body=koerper, headers=k)
        a = verb.getresponse()
        koepfe: dict[str, list[str]] = {}
        for n, w in a.getheaders():
            koepfe.setdefault(n.lower(), []).append(w)
        return a.status, koepfe, a.read()
    finally:
        verb.close()


def tcp_von(container: str, host: str, port: int) -> str:
    """'offen': Verbindung steht. 'zu': keine Route, verworfen, abgelehnt oder Zeitüberschreitung. 'defekt': die Probe selbst
    ging schief (kein Host angegeben, Container fehlt, Werkzeug fehlt); ein defekte Probe zählt nie als bestanden."""
    if not host:
        return "defekt"
    if container.endswith("guacd"):
        cmd = ["docker", "exec", container, "nc", "-w", "3", "-z", host, str(port)]
    else:  # bash ist im Image der Webanwendung vorhanden, nc nicht
        cmd = ["docker", "exec", container, "timeout", "4", "bash", "-c", f"exec 3<>/dev/tcp/{host}/{port}"]
    r = sh(*cmd, timeout=20)
    if r.returncode == 0:
        return "offen"
    text = (r.stdout + r.stderr).lower()
    if r.returncode in (126, 127) or any(t in text for t in ("no such container", "is not running", "executable file not found",
                                                             "oci runtime", "error response from daemon", "programm fehlt")):
        return "defekt"
    return "zu"


def container_ip(container: str, netz: str) -> str:
    d = inspect(container)
    if not d:
        return ""
    return d["NetworkSettings"]["Networks"].get(netz, {}).get("IPAddress", "")


# ======================================================================== gemeinsame Prüfungen
def pruefe_container(e: Ergebnis, web: str, guacd: str, netze: dict[str, str], key_datei_ziel: str = "/run/wamo-rd/json.key") -> None:
    """Gesundheit und Härtung beider Container. netze: Rollen -> Netzname (rd, guacd, ziel)."""
    print("== Container: gesund und gehärtet")
    for name, grenze_mb, rollen in ((web, 768, {netze["rd"], netze["guacd"]}), (guacd, 256, {netze["guacd"], netze["ziel"]})):
        d = inspect(name)
        if not e.pruefe(d is not None, f"{name} vorhanden"):
            continue
        assert d is not None
        st, hc, cfg = d["State"], d["HostConfig"], d["Config"]
        e.pruefe(st.get("Running") is True and st.get("Health", {}).get("Status") == "healthy", f"{name} läuft und ist gesund",
                 st.get("Health", {}).get("Status", "ohne Probe"))
        e.pruefe(hc["ReadonlyRootfs"] is True, f"{name}: Dateisystem schreibgeschützt")
        e.pruefe((hc.get("CapDrop") or []) == ["ALL"] and not hc.get("CapAdd"), f"{name}: alle Capabilities entfernt, keine hinzugefügt")
        e.pruefe(any("no-new-privileges" in s for s in (hc.get("SecurityOpt") or [])), f"{name}: no-new-privileges")
        e.pruefe(hc.get("Privileged") is False, f"{name}: nicht privilegiert")
        benutzer = cfg.get("User", "")
        e.pruefe(bool(re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", benutzer)), f"{name}: läuft nicht als root", benutzer or "leer")
        e.pruefe(0 < hc["Memory"] <= grenze_mb * 1024 * 1024 and hc["MemorySwap"] == hc["Memory"], f"{name}: Speichergrenze {grenze_mb} MB, kein Swap",
                 f"{hc['Memory'] // 1048576} MB")
        e.pruefe(hc["NanoCpus"] > 0, f"{name}: CPU-Grenze")
        e.pruefe(0 < (hc.get("PidsLimit") or 0) <= 256, f"{name}: Prozessgrenze", str(hc.get("PidsLimit")))
        e.pruefe(not (d["NetworkSettings"].get("Ports") or {}) or all(v is None for v in d["NetworkSettings"]["Ports"].values()),
                 f"{name}: veröffentlicht keinen Port")
        e.pruefe(hc["RestartPolicy"]["Name"] == "unless-stopped", f"{name}: Neustart unless-stopped")
        e.pruefe(d["HostConfig"].get("NetworkMode") != "host" and not hc.get("PidMode") and not hc.get("IpcMode") in ("host",),
                 f"{name}: eigene Namensräume")
        quellen = [m["Source"] for m in d.get("Mounts", []) if m.get("Type") == "bind"]
        e.pruefe(all("docker.sock" not in q for q in quellen), f"{name}: kein Docker-Socket")
        erlaubt_binds = 1 if name == web else 0
        e.pruefe(len(quellen) == erlaubt_binds and all(m.get("RW") is False for m in d.get("Mounts", []) if m.get("Type") == "bind"),
                 f"{name}: nur {'der Schlüssel (lesend)' if erlaubt_binds else 'keine Einbindungen vom Host'}", ", ".join(quellen) or "keine")
        e.pruefe(set(d["NetworkSettings"]["Networks"]) == rollen, f"{name}: nur in den Netzen {sorted(rollen)}",
                 str(sorted(d["NetworkSettings"]["Networks"])))
        umgebung = {z.split("=", 1)[0]: z.split("=", 1)[1] for z in cfg.get("Env", []) if "=" in z}
        verboten = [k for k in umgebung if re.match(r"(JSON_SECRET_KEY$|POSTGRES|MYSQL_|SQLSERVER_|LDAP_|OPENID_|SAML_|CAS_|DUO_|HTTP_AUTH_)", k)]
        e.pruefe(not verboten, f"{name}: Schlüssel und Datenbank-/SSO-Einstellungen nicht in der Umgebung", ",".join(verboten))
        if name == web:
            e.pruefe(umgebung.get("JSON_SECRET_KEY_FILE") == key_datei_ziel and bool(umgebung.get("JSON_TRUSTED_NETWORKS")),
                     "web: Schlüssel aus Datei, vertraute Adresse gesetzt", umgebung.get("JSON_TRUSTED_NETWORKS", ""))
            vertraut = umgebung.get("JSON_TRUSTED_NETWORKS", "")
            e.pruefe(bool(re.fullmatch(r"\d+\.\d+\.\d+\.\d+/32", vertraut)), "web: JSON nur aus genau einer Adresse (/32)", vertraut)
    # Netze: intern oder nicht
    for rolle, intern in (("rd", True), ("guacd", True), ("ziel", False)):
        r = sh("docker", "network", "inspect", "-f", "{{.Internal}}", netze[rolle])
        e.pruefe(r.returncode == 0 and r.stdout.strip() == str(intern).lower(), f"Netz {netze[rolle]}: intern={str(intern).lower()}",
                 r.stdout.strip() or r.stderr.strip())


def pruefe_kopfzeilen(e: Ergebnis, basis: str, ca: str | None, nur_ein_fehlversuch: bool = True) -> None:
    print("== Gateway-Seite: Kopfzeilen und Methoden")
    try:
        status, k, _ = http(basis + "/", ca=ca)
    except Exception as ex:  # noqa: BLE001
        e.fehler("GET / erreichbar", repr(ex))
        return
    e.pruefe(status == 200, "GET / ergibt 200", str(status))
    csp = (k.get("content-security-policy") or [""])[0]
    e.pruefe("frame-ancestors 'none'" in csp and "default-src 'none'" in csp, "CSP: kein iframe, Standard verboten")
    skripte = re.search(r"script-src ([^;]*);", csp)
    e.pruefe(skripte is not None and skripte[1] == "'self' 'unsafe-eval'",
             "CSP: Skripte nur von der eigenen Herkunft (eval nur für AngularJS), keine Inline-Skripte", skripte[1] if skripte else "")
    e.pruefe(k.get("x-frame-options") == ["DENY"], "X-Frame-Options DENY")
    e.pruefe(k.get("referrer-policy") == ["no-referrer"], "Referrer-Policy no-referrer")
    e.pruefe(k.get("x-content-type-options") == ["nosniff"], "X-Content-Type-Options nosniff")
    e.pruefe(k.get("cross-origin-opener-policy") == ["same-origin"] and k.get("cross-origin-resource-policy") == ["same-origin"],
             "COOP und CORP same-origin")
    e.pruefe(k.get("cache-control") == ["no-store"], "Cache-Control no-store", str(k.get("cache-control")))
    pp = (k.get("permissions-policy") or [""])[0]
    e.pruefe("clipboard-read=()" in pp and "clipboard-write=()" in pp and "camera=()" in pp, "Permissions-Policy sperrt Zwischenablage und Kamera")
    e.pruefe("server" not in k and "x-powered-by" not in k, "weder Server noch X-Powered-By sichtbar", str(k.get("server")))
    e.pruefe("access-control-allow-origin" not in k, "kein CORS")
    for c in k.get("set-cookie", []):
        e.pruefe("httponly" in c.lower() and "samesite" in c.lower() and (basis.startswith("http://") or "secure" in c.lower()),
                 "Cookie mit HttpOnly, SameSite und (über HTTPS) Secure", c.split("=")[0])
    # Methoden und Anmeldung
    for methode in ("PUT", "PATCH", "OPTIONS", "TRACE"):
        try:
            s, _, _ = http(basis + "/", methode=methode, ca=ca)
        except Exception as ex:  # noqa: BLE001
            e.fehler(f"{methode} abgewiesen", repr(ex))
            continue
        e.pruefe(s == 405, f"{methode} wird abgewiesen (405)", str(s))
    # Wenige Fehlversuche: Die Sperre von Guacamole zählt sie je Absenderadresse (3 sperren die Adresse für 15 Minuten)
    versuche = [{"data": base64.b64encode(secrets.token_bytes(64)).decode()}]
    if nur_ein_fehlversuch is False:
        versuche.append({"username": "guacadmin", "password": "guacadmin"})
    for daten in versuche:
        s, _, _ = http(basis + "/api/tokens", methode="POST", daten=daten, ca=ca)
        e.pruefe(s in (401, 403), f"POST /api/tokens ({', '.join(daten)}) ohne gültiges JSON abgewiesen", str(s))
    for s_ziel in ("/api/session/data/json/connectionGroups/ROOT/tree", "/api/session/tunnels", "/api/session/data/json/users"):
        s, _, _ = http(basis + s_ziel, ca=ca)
        e.pruefe(s in (401, 403), f"GET {s_ziel} ohne Token abgewiesen", str(s))
    s, _, _ = http(basis + "/api/session/data/json/connectionGroups/ROOT/tree?token=" + "0" * 64, ca=ca)
    e.pruefe(s in (401, 403), "erfundenes Token abgewiesen", str(s))
    s, _, _ = http(basis + "/api/session/tunnels?token=" + secrets.token_hex(32), ca=ca)
    e.pruefe(s in (401, 403), "erfundenes Token am Tunnel abgewiesen", str(s))
    e.pruefe(http(basis + "/api/languages", ca=ca)[0] == 200, "Seite lädt ohne Konto (nur statische Teile)")


# ======================================================================== Testmodus
def freie_netze(anzahl: int) -> list[str]:
    belegt = []
    ids = sh("docker", "network", "ls", "-q").stdout.split()
    if ids:
        r = sh("docker", "network", "inspect", "-f", "{{range .IPAM.Config}}{{.Subnet}} {{end}}", *ids)
        belegt = [ipaddress.ip_network(t, strict=False) for t in r.stdout.split() if ":" not in t]
    gefunden: list[str] = []
    for k in range(200, 255):
        kandidat = ipaddress.ip_network(f"10.{k}.{secrets.randbelow(200) + 20}.0/24")
        if not any(kandidat.overlaps(b) for b in belegt) and str(kandidat) not in gefunden:
            gefunden.append(str(kandidat))
        if len(gefunden) == anzahl:
            return gefunden
    raise RuntimeError("keine freien Testnetze gefunden")


def aufraeumen_praefix(p: str) -> None:
    """Entfernt alles mit dem Präfix p (Container, Netze, Volumes). Verweigert jeden Namen ohne wamo-test-rd-."""
    if not re.fullmatch(r"wamo-test-rd-[a-z0-9]{1,12}-", p):
        raise RuntimeError(f"Ungültiger Präfix {p!r}")

    def sicher(n: str) -> str:
        if not n.startswith(p):
            raise RuntimeError(f"Verweigert: {n} gehört nicht zum Test ({p})")
        return n

    for n in sh("docker", "ps", "-a", "--format", "{{.Names}}", "--filter", f"name=^{p}").stdout.split():
        if n.startswith(p):
            sh("docker", "rm", "-f", "-v", sicher(n))
    for n in sh("docker", "network", "ls", "--format", "{{.Name}}", "--filter", f"name=^{p}").stdout.split():
        if n.startswith(p):
            sh("docker", "network", "rm", sicher(n))
    for n in sh("docker", "volume", "ls", "--format", "{{.Name}}", "--filter", f"name=^{p}").stdout.split():
        if n.startswith(p):
            sh("docker", "volume", "rm", "-f", sicher(n))
    # Nachprüfung: Reste mit dem Präfix melden, nie andere Namen anfassen
    rest = [n for art in (("ps", "-a", "--format", "{{.Names}}"), ("network", "ls", "--format", "{{.Name}}"))
            for n in sh("docker", *art).stdout.split() if n.startswith(p)]
    if rest:
        print(f"WARNUNG: nach dem Aufräumen noch vorhanden: {', '.join(rest)}", file=sys.stderr)


def arbeitsordner(p: str) -> Path:
    """Fester Ordner je Testaufbau (die Bind-Mounts der Container zeigen darauf, auch bei --behalten)."""
    return Path(tempfile.gettempdir()).resolve() / (p + "arbeit")


def warte_gesund(namen: list[str], sekunden: int = 150) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        zustaende = [(inspect(n) or {}).get("State", {}) for n in namen]
        if all(z.get("Health", {}).get("Status") == "healthy" for z in zustaende):
            return True
        if any(z.get("Status") in ("exited", "dead") for z in zustaende):
            return False
        time.sleep(3)
    return False


def warte_antwort(url: str, sekunden: int = 30) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        try:
            if http(url, timeout=5)[0] == 200:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def testmodus(args: argparse.Namespace) -> int:
    name = args.name
    p = f"wamo-test-rd-{name}-"
    projekt = p.rstrip("-")
    e = Ergebnis()
    aufraeumen_praefix(p)
    netz_rd, netz_guacd, netz_ziel = p + "netz", p + "guacd-netz", p + "ziel"
    sub_rd, sub_guacd, sub_ziel = freie_netze(3)
    vertraut_ip = str(ipaddress.ip_network(sub_rd)[10])
    fremd_ip = str(ipaddress.ip_network(sub_rd)[11])
    caddy_ip = str(ipaddress.ip_network(sub_rd)[2])
    web, guacd, caddy, zugang, fremd = p + "rd-web", p + "rd-guacd", p + "caddy", p + "zugang", p + "fremd"
    arbeit = arbeitsordner(p)
    schluessel = secrets.token_hex(16)
    guacd_ip = str(ipaddress.ip_network(sub_guacd)[3])
    uid = "65100"
    env = {"WAMO_RD_PRAEFIX": p, "WAMO_RD_UID": uid, "WAMO_RD_GID": uid, "WAMO_RD_NETZ": netz_rd, "WAMO_RD_GUACD_NETZ": netz_guacd,
           "WAMO_RD_ZIEL_NETZ": netz_ziel, "WAMO_RD_GUACD_SUBNETZ": str(ipaddress.ip_network(sub_guacd)),
           "WAMO_RD_ZIEL_SUBNETZ": sub_ziel, "WAMO_RD_KEY": str(arbeit / "json.key"), "WAMO_RD_VERTRAUT": f"{vertraut_ip}/32",
           "WAMO_RD_PROXY_REGEX": caddy_ip.replace(".", "[.]"), "WAMO_RD_GUACD_IP": guacd_ip,
           "WAMO_RD_ZUGANG_IP": str(ipaddress.ip_network(sub_rd)[12]), "WAMO_RD_KERN_UID": "65101", "WAMO_RD_RUN": p + "run",
           "WAMO_RD_ZIELE_DATEI": str(arbeit / "ziele.toml")}
    try:
        arbeit.mkdir(parents=True, exist_ok=True)
        (arbeit / "json.key").write_text(schluessel)  # ohne Zeilenumbruch: Guacamole liest die Datei unverändert
        (arbeit / "json.key").chmod(0o644)  # nur Testschlüssel; im Betrieb gilt 0640 root:wamo-rd-gateway
        (arbeit / "ziele.toml").write_text("# leer: Stufe 1 hat keine Ziele\n", encoding="utf-8")
        (arbeit / "override.yml").write_text(f"volumes:\n  {p}run:\n    name: {p}run\n", encoding="utf-8")
        sh("docker", "volume", "create", p + "run", check=True)
        sh("docker", "run", "--rm", "-v", f"{p}run:/r", ATTRAPPE_IMAGE, "sh", "-c", f"chown {uid}:65101 /r && chmod 2770 /r", check=True)
        r = sh("docker", "build", "--tag", f"{p}rd-zugang:1", str(HIER / "wamo-rd" / "zugang"), timeout=600)
        if not e.pruefe(r.returncode == 0, "Image des Zugangsdienstes baut", (r.stderr or "")[-200:]):
            return e.summe()
        print(f"== Testaufbau {p} (Netze {sub_rd}, {sub_guacd}, {sub_ziel}; vertraute Adresse {vertraut_ip})")
        sh("docker", "network", "create", "--internal", "--subnet", sub_rd, "-o", "com.docker.network.bridge.inhibit_ipv4=true", netz_rd, check=True)
        r = sh("docker", "compose", "-p", projekt, "-f", str(COMPOSE), "-f", str(arbeit / "override.yml"), "up", "-d", env=env, timeout=400)
        if r.returncode != 0:
            e.fehler("docker compose up", (r.stderr or r.stdout)[-600:])
            return e.summe()
        # Caddy mit der echten Vorlage (HTTP statt HTTPS, sonst unverändert)
        site = VORLAGE.read_text(encoding="utf-8").replace("__ADRESSE__", "http://:8080").replace("__TLS__", "").replace("__WSS__", "ws://127.0.0.1")
        (arbeit / "Caddyfile").write_text("{\n\tadmin off\n\tauto_https off\n}\n" + site, encoding="utf-8")
        sh("docker", "run", "--rm", "--network", "none", "-v", f"{arbeit}:/etc/caddy:ro", CADDY_IMAGE, "caddy", "validate",
           "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile", check=True)
        sh("docker", "create", "--name", caddy, "-p", "127.0.0.1::8080", "-e", f"WAMO_RD_UPSTREAM={web}:8080", "-v",
           f"{arbeit}/Caddyfile:/etc/caddy/Caddyfile:ro", CADDY_IMAGE, check=True)
        sh("docker", "network", "connect", "--ip", caddy_ip, netz_rd, caddy, check=True)
        sh("docker", "start", caddy, check=True)
        # Zugangsdienst-Attrappe in der vertrauten Adresse, "fremder" Absender in einer anderen
        for cname, ip in ((zugang, vertraut_ip), (fremd, fremd_ip)):
            sh("docker", "create", "--name", cname, "--network", netz_rd, "--ip", ip, "--cap-drop", "ALL", "-v",
               f"{arbeit}/json.key:/key:ro", ATTRAPPE_IMAGE, "sleep", "600", check=True)
            sh("docker", "start", cname, check=True)
        if not e.pruefe(warte_gesund([web, guacd, p + "rd-zugang"]), "Container werden gesund"):
            print(sh("docker", "logs", "--tail", "40", web).stdout[-2000:])
            return e.summe()
        port = sh("docker", "port", caddy, "8080/tcp", check=True).stdout.split(":")[-1].strip()
        basis = f"http://127.0.0.1:{port}"
        e.pruefe(warte_antwort(basis + "/"), "Caddy antwortet")
        netze = {"rd": netz_rd, "guacd": netz_guacd, "ziel": netz_ziel}
        pruefe_container(e, web, guacd, netze)
        pruefe_zugang(e, p + "rd-zugang", netze)
        # Das Netz wamo-rd-netz gehört im Betrieb zu wamo-caddy; hier legt es der Test an. Vergleichbare Proben:
        pruefe_kopfzeilen(e, basis, None, nur_ein_fehlversuch=False)

        print("== Isolation (aus den Containern)")
        e.pruefe(tcp_von(web, guacd, 4822) == "offen", "Kontrolle: web erreicht guacd:4822 (Probe funktioniert)")
        e.pruefe(tcp_von(guacd, web, 8080) == "offen", "Kontrolle: guacd erreicht die Webanwendung im Netz wamo-rd-guacd-netz (nc funktioniert)")
        for host, port_ in (("1.1.1.1", 443), ("8.8.8.8", 53), ("169.254.169.254", 80), (sub_ziel.split("/")[0][:-1] + "1", 22)):
            e.pruefe(tcp_von(web, host, port_) == "zu", f"web erreicht {host}:{port_} nicht")
        e.pruefe(tcp_von(guacd, container_ip(caddy, netz_rd), 8080) == "zu", "guacd erreicht Caddy im Netz wamo-rd-netz nicht")
        e.pruefe(tcp_von(guacd, container_ip(zugang, netz_rd), 22) == "zu", "guacd erreicht das Netz wamo-rd-netz nicht")
        # guacd lauscht nur an seiner Adresse im internen Netz, nicht im Netz mit Ausgang (Host und Dritte erreichen ihn dort nicht)
        sh("docker", "network", "connect", netz_ziel, fremd, check=True)
        ziel_ip = container_ip(guacd, netz_ziel)
        e.pruefe(tcp_von(fremd, web, 8080) == "offen", "Kontrolle: der Fremd-Container erreicht die Webanwendung (bash-Probe funktioniert)")
        e.pruefe(bool(ziel_ip) and tcp_von(fremd, ziel_ip, 4822) == "zu", "guacd:4822 ist im Netz wamo-rd-ziel nicht erreichbar", ziel_ip)
        e.skip("guacd erreicht nur die Ziele", "Ausgangskette WAMO-FW-RD gibt es nur auf dem Server; Regeltext wird in test_rd_skripte.py geprüft")

        print("== Anmeldung: nur mit gültigem JSON aus der vertrauten Adresse")
        url_intern = f"http://{web}:8080"
        keyp = "/key"

        def einreichen(container: str, modus: str, url: str = url_intern, xff: str = "") -> tuple[int, dict | str]:
            r_ = sh("docker", "exec", container, "python3", "-c", ATTRAPPE_CODE, url, modus, keyp, xff)
            zeile = (r_.stdout or r_.stderr).strip()
            code, _, rest = zeile.partition(" ")
            try:
                return int(code), json.loads(rest)
            except (ValueError, json.JSONDecodeError):
                return int(code) if code.isdigit() else 0, rest

        code, antwort = einreichen(zugang, "gut")
        token = antwort.get("authToken") if isinstance(antwort, dict) else None
        e.pruefe(code == 200 and bool(token), "gültiges JSON aus der vertrauten Adresse ergibt ein Token", f"HTTP {code}")
        e.pruefe(isinstance(antwort, dict) and antwort.get("username") == "rauchtest" and "password" not in json.dumps(antwort).lower(),
                 "Antwort enthält Token und Namen, kein Passwort")
        fremd_code = einreichen(fremd, "gut")[0]
        e.pruefe(fremd_code in (401, 403), "gültiges JSON von einer fremden Adresse wird abgewiesen", f"HTTP {fremd_code}")
        # Fälschung der Absenderadresse: direkt (Peer ist kein Proxy, X-Forwarded-For zählt nicht) und über Caddy (setzt die Adresse neu)
        direkt = einreichen(fremd, "gut", xff=vertraut_ip)
        e.pruefe(direkt[0] in (401, 403, 429), "gefälschtes X-Forwarded-For direkt an die Webanwendung wirkt nicht", str(direkt)[:150])
        ueber = einreichen(fremd, "gut", url=f"http://{caddy}:8080", xff=vertraut_ip)
        e.pruefe(ueber[0] in (401, 403, 429), "gefälschtes X-Forwarded-For über Caddy wirkt nicht", str(ueber)[:150])
        e.pruefe(einreichen(zugang, "falsch")[0] in (401, 403), "JSON mit falscher Signatur wird abgewiesen")
        e.pruefe(einreichen(zugang, "abgelaufen")[0] in (401, 403), "abgelaufenes JSON wird abgewiesen")
        if token:
            s, _, body = http(f"{basis}/api/session/data/json/connectionGroups/ROOT/tree?token={token}")
            baum = json.loads(body) if s == 200 else {}
            e.pruefe(s == 200 and "test" in json.dumps(baum), "mit dem Token ist genau die eingereichte Verbindung sichtbar", f"HTTP {s}")
            s, _, body = http(f"{basis}/api/session/data/json/self/effectivePermissions?token={token}")
            rechte = json.loads(body) if s == 200 else {}
            e.pruefe(s == 200 and rechte.get("systemPermissions") == [] and list(rechte.get("connectionPermissions", {})) == ["test"]
                     and rechte["connectionPermissions"]["test"] == ["READ"], "Token hat nur Leserecht auf die eine Verbindung, keine Systemrechte")
            s, _, body = http(f"{basis}/api/session/data/json/users?token={token}")
            e.pruefe(s == 200 and list(json.loads(body)) == ["rauchtest"], "keine fremden Benutzer sichtbar", f"HTTP {s}")
            s, _, _ = http(f"{basis}/api/session/data/json/connections/test/parameters?token={token}")
            e.pruefe(s in (401, 403), "Verbindungsparameter (Adresse, Zugangsdaten) für das Token nicht lesbar", f"HTTP {s}")
            s, _, _ = http(f"{basis}/api/session/data/json/users/rauchtest?token={token}", methode="DELETE")
            e.pruefe(s in (401, 403, 404, 405), "Benutzer löschen abgewiesen", f"HTTP {s}")
            s, _, body = http(f"{basis}/api/session/data/json/users?token={token}")
            e.pruefe(s == 200 and list(json.loads(body)) == ["rauchtest"], "Benutzer ist danach noch da", f"HTTP {s}")
            e.pruefe(isinstance(antwort, dict) and antwort.get("availableDataSources") == ["json"],
                     "nur die Datenquelle json ist aktiv (kein JDBC, LDAP, SSO)", str(antwort.get("availableDataSources")) if isinstance(antwort, dict) else "")
            s, _, _ = http(f"{basis}/api/tokens/{token}", methode="DELETE")
            e.pruefe(s in (200, 204), "Abmelden (DELETE) wirkt", f"HTTP {s}")
            s, _, _ = http(f"{basis}/api/session/data/json/connectionGroups/ROOT/tree?token={token}")
            e.pruefe(s in (401, 403), "Token nach dem Abmelden ungültig", f"HTTP {s}")
        if args.behalten:
            code, antwort = einreichen(zugang, "gut")
            if isinstance(antwort, dict) and antwort.get("authToken"):
                print(f"\nStack bleibt stehen. Seite: {basis}/#/?token={antwort['authToken']}\nAufräumen: python {Path(__file__).name} --aufraeumen --name {name}")
                return e.summe()
        return e.summe()
    finally:
        if not args.behalten:
            try:
                aufraeumen_praefix(p)
            finally:
                shutil.rmtree(arbeit, ignore_errors=True)


# ======================================================================== Betrieb
def pruefe_zugang(e: Ergebnis, name: str, netze: dict[str, str]) -> None:
    """Härtung und Netze des Zugangsdienstes (Stufe 3): nur Standardbibliothek, nur Socket, kein Port, keine Geheimnisse."""
    print("== Zugangsdienst")
    d = inspect(name)
    if not e.pruefe(d is not None, f"{name} vorhanden"):
        return
    assert d is not None
    st, hc, cfg = d["State"], d["HostConfig"], d["Config"]
    e.pruefe(st.get("Running") is True and st.get("Health", {}).get("Status") == "healthy", f"{name} läuft und ist gesund", st.get("Health", {}).get("Status", "ohne Probe"))
    e.pruefe(hc["ReadonlyRootfs"] is True, f"{name}: Dateisystem schreibgeschützt")
    e.pruefe((hc.get("CapDrop") or []) == ["ALL"] and not hc.get("CapAdd"), f"{name}: alle Capabilities entfernt")
    e.pruefe(any("no-new-privileges" in x for x in (hc.get("SecurityOpt") or [])) and hc.get("Privileged") is False, f"{name}: no-new-privileges, nicht privilegiert")
    e.pruefe(bool(re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", cfg.get("User", ""))), f"{name}: läuft nicht als root", cfg.get("User", "") or "leer")
    e.pruefe(0 < hc["Memory"] <= 128 * 1024 * 1024 and 0 < (hc.get("PidsLimit") or 0) <= 64, f"{name}: Speicher- und Prozessgrenze")
    e.pruefe(all(v is None for v in (d["NetworkSettings"].get("Ports") or {}).values()), f"{name}: veröffentlicht keinen Port")
    quellen = [(m["Source"], m.get("RW")) for m in d.get("Mounts", []) if m.get("Type") in ("bind", "volume")]  # im Testmodus ist das Socket-Verzeichnis ein benanntes Volume
    e.pruefe(len(quellen) == 2 and all("docker.sock" not in q for q, _ in quellen) and sum(1 for _, rw in quellen if rw is False) == 1,
             f"{name}: genau zwei Einbindungen (Socket-Verzeichnis, Zielliste lesend)", ", ".join(q for q, _ in quellen))
    e.pruefe(set(d["NetworkSettings"]["Networks"]) == {netze["rd"], netze["ziel"]}, f"{name}: nur in den Netzen rd und ziel", str(sorted(d["NetworkSettings"]["Networks"])))
    umgebung = {z.split("=", 1)[0]: z.split("=", 1)[1] for z in cfg.get("Env", []) if "=" in z}
    # GPG_KEY ist die öffentliche Schlüsselkennung des Python-Images, kein Geheimnis
    e.pruefe(not [k for k in umgebung if k != "GPG_KEY" and re.search(r"KEY|SECRET|PASS|TOKEN", k, flags=re.I)], f"{name}: kein Schlüssel, kein Passwort, kein Token in der Umgebung")
    e.pruefe(bool(re.fullmatch(r"[1-9][0-9]{2,}", umgebung.get("WAMO_RD_KERN_UID", ""))), f"{name}: Benutzerkennung des Kerns gesetzt", umgebung.get("WAMO_RD_KERN_UID", ""))


# Läuft als der geprüfte Benutzer: Verbindungsversuch, Ausgabe OFFEN oder ZU (jedes Werkzeug, das fehlt, gibt nichts aus: "defekt")
AUSGANG_PROBE = (
    "import socket,sys\n"
    "h,p,fam=sys.argv[1],int(sys.argv[2]),sys.argv[3]\n"
    "s=socket.socket(socket.AF_INET6 if fam=='6' else socket.AF_INET)\n"
    "s.settimeout(3)\n"
    "try:\n"
    "    s.connect((h,p)); print('OFFEN')\n"
    "except OSError:\n"
    "    print('ZU')\n"
)
AUSGANG_DNS = "import socket;socket.getaddrinfo('example.org',443,socket.AF_INET);print('OFFEN')"


def probe_als(benutzer: str | None, host: str, port: int, fam: str = "4") -> str:
    """'offen', 'zu' oder 'defekt' (die Probe selbst ging schief). benutzer None: als der aktuelle Benutzer."""
    cmd = ["sudo", "-n", "-u", benutzer] if benutzer else []
    r = sh(*cmd, "python3", "-I", "-c", AUSGANG_PROBE.replace("\\n", "\n"), host, str(port), fam, timeout=20)
    aus = r.stdout.strip()
    return {"OFFEN": "offen", "ZU": "zu"}.get(aus, "defekt")


def ausgang_proben(e: Ergebnis, ziele: list[str]) -> None:
    """Verbotsproben der Ausgangsregel für wamo-rd (infra/sparks/wamo-rd-ausgang-einrichten.sh), nur wenn die Tabelle geladen ist."""
    print("== Ausgangsregel für wamo-rd (Proben als wamo-rd)")
    r = sh("sudo", "-n", "nft", "list", "table", "inet", "wamo_rd_ausgang")
    if r.returncode != 0:
        e.skip("Ausgangsregel für wamo-rd", "Tabelle inet wamo_rd_ausgang nicht geladen oder sudo -n nft nicht möglich (Vorbedingung vor dem ersten Ziel Sokrates-2)")
        return
    e.pruefe("meta skuid wamo-rd jump wamo_rd" in r.stdout and "ip daddr @gesperrt4 counter" in r.stdout and "meta nfproto ipv6 counter" in r.stdout,
             "Tabelle enthält die Regel für das Konto, die Sperre für LAN und Loopback und die Sperre für IPv6")
    if sh("sudo", "-n", "-u", "wamo-rd", "true").returncode != 0:
        e.skip("Proben als wamo-rd", "sudo -n -u wamo-rd nicht möglich")
        return
    eigene = "192.168.178.136"
    verbot: list[tuple[str, str, int, str]] = [
        ("Kern über Caddy (55444)", "127.0.0.1", 55444, "4"),
        ("Kern über Caddy im LAN (55444)", eigene, 55444, "4"),
        ("Supabase-API (55443) auf Loopback", "127.0.0.1", 55443, "4"),
        ("Supabase-API (55443) im LAN", eigene, 55443, "4"),
        ("Supavisor (55322)", "127.0.0.1", 55322, "4"),
        ("Postgres (5432)", "127.0.0.1", 5432, "4"),
        ("Oberfläche (55445)", "127.0.0.1", 55445, "4"),
        ("Gateway-Seite (55446)", "127.0.0.1", 55446, "4"),
        ("SSH auf Loopback", "127.0.0.1", 22, "4"),
        ("SSH auf der LAN-Adresse", eigene, 22, "4"),
        ("Router (80)", "192.168.178.1", 80, "4"),
        ("Router (443)", "192.168.178.1", 443, "4"),
        ("LiteLLM auf den Sparks (4000)", "192.168.178.75", 4000, "4"),
        ("Docker-Brücke (22)", "172.17.0.1", 22, "4"),
        ("IPv6 Loopback (22)", "::1", 22, "6"),
    ]
    for z in ziele:
        verbot += [(f"Spark {z} (SSH)", z, 22, "4"), (f"Spark {z} (RDP)", z, 3389, "4")]
    for name, host, port, fam in verbot:
        kontrolle = probe_als(None, host, port, fam)
        if kontrolle != "offen":
            e.skip(f"wamo-rd erreicht {name} nicht", f"Kontrolle: auch ohne die Regel nicht erreichbar ({kontrolle})")
            continue
        e.pruefe(probe_als("wamo-rd", host, port, fam) == "zu", f"wamo-rd erreicht {name} nicht", f"{host}:{port}")
    if probe_als(None, "1.1.1.1", 443) == "offen":
        e.pruefe(probe_als("wamo-rd", "1.1.1.1", 443) == "offen", "wamo-rd erreicht das Internet (1.1.1.1:443)")
        dns = sh("sudo", "-n", "-u", "wamo-rd", "python3", "-I", "-c", AUSGANG_DNS)
        e.pruefe(dns.stdout.strip() == "OFFEN", "wamo-rd löst Namen auf (über den Stub 127.0.0.53)", (dns.stderr or dns.stdout).strip()[-100:])
    else:
        e.skip("wamo-rd erreicht das Internet", "dieser Rechner erreicht 1.1.1.1:443 selbst nicht")


def lies_ziele(pfad: str = "/etc/wamo-rd/ziele.toml") -> list[str] | None:
    """Gültige Zieladressen wie in wamo-firewall.sh (IPv4, kein Loopback, Multicast, Link-Local, Docker-Bereich, höchstens 8).
    None, wenn die Datei nicht lesbar ist (dann lässt sich die Kette nicht mit ihr vergleichen)."""
    try:
        text = Path(pfad).read_text(encoding="utf-8")
    except OSError:
        return None
    ergebnis: list[str] = []
    docker = ipaddress.ip_network("172.16.0.0/12")
    for a in re.findall(r'^\s*adresse\s*=\s*"([^"]*)"', text, flags=re.M):
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.version != 4 or ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved or ip in docker:
            continue
        if re.search(r"(^|\.)0\d", a):
            continue
        ergebnis.append(a)
        if len(ergebnis) == 8:
            break
    return ergebnis


def betrieb(args: argparse.Namespace) -> int:
    e = Ergebnis()
    ca = args.ca
    netze = {"rd": "wamo-rd-netz", "guacd": "wamo-rd-guacd-netz", "ziel": "wamo-rd-ziel"}
    pruefe_container(e, "wamo-rd-web", "wamo-rd-guacd", netze)
    pruefe_zugang(e, "wamo-rd-zugang", netze)
    pruefe_kopfzeilen(e, BETRIEB_URL, ca)
    print("== TLS")
    try:
        ctx = ssl.create_default_context(cafile=ca)
        host_, port_ = urlsplit(BETRIEB_URL).hostname or "", urlsplit(BETRIEB_URL).port or 443
        with socket.create_connection((host_, port_), timeout=10) as s, ctx.wrap_socket(s, server_hostname=host_) as t:
            e.ok("Zertifikat der WAMO-CA gilt für 192.168.178.136:55446", t.version() or "")
    except Exception as ex:  # noqa: BLE001
        e.fehler("TLS-Prüfung", repr(ex))

    print("== Isolation (aus den Containern)")
    guacd_ip = container_ip("wamo-rd-guacd", "wamo-rd-guacd-netz")
    e.pruefe(tcp_von("wamo-rd-web", guacd_ip, 4822) == "offen", "Kontrolle: web erreicht guacd:4822 (bash-Probe funktioniert)", guacd_ip)
    e.pruefe(tcp_von("wamo-rd-guacd", "wamo-rd-web", 8080) == "offen", "Kontrolle: guacd erreicht die Webanwendung (nc-Probe funktioniert)")
    ziel_ip = container_ip("wamo-rd-guacd", "wamo-rd-ziel")
    try:  # der Host selbst (dieses Skript) darf guacd über die Brücke 172.27.0.1 nicht erreichen
        socket.create_connection((ziel_ip, 4822), timeout=3).close()
        host_erreicht = True
    except OSError:
        host_erreicht = False
    e.pruefe(bool(ziel_ip) and not host_erreicht, "Host erreicht guacd:4822 im Netz wamo-rd-ziel nicht", ziel_ip)
    gelesen = lies_ziele()
    ziele = gelesen or []
    lan_proben = [("1.1.1.1", 443), ("8.8.8.8", 53), ("192.168.178.1", 80), ("192.168.178.136", 22), ("192.168.178.136", 55443),
                  ("192.168.178.136", 55321), ("172.17.0.1", 22)]
    for host, port in lan_proben:
        e.pruefe(tcp_von("wamo-rd-web", host, port) == "zu", f"web erreicht {host}:{port} nicht")
    caddy_ip = container_ip("wamo-caddy", "wamo-caddy_default")
    gateway_ip = "172.27.0.1"
    guacd_proben = lan_proben + [("1.1.1.1", 3389), ("192.168.178.1", 3389), (gateway_ip, 22), (gateway_ip, 55443)]
    if caddy_ip:
        guacd_proben += [(caddy_ip, 2019), (caddy_ip, 55443)]
    for host, port in guacd_proben:
        if host in ziele and port == 3389:
            continue
        # Ein Ziel selbst darf auf anderen Ports als 3389 ebenfalls nicht erreichbar sein; nur (Ziel, 3389) ist erlaubt
        e.pruefe(tcp_von("wamo-rd-guacd", host, port) == "zu", f"guacd erreicht {host}:{port} nicht")
    if gelesen is None:
        e.skip("Zielliste", "/etc/wamo-rd/ziele.toml nicht lesbar")
    for z in ziele:
        print(f"  info     Ziel {z}:3389 (erlaubt) antwortet: {tcp_von('wamo-rd-guacd', z, 3389)}")
    r = sh("sudo", "-n", "iptables", "-w", "5", "-S", "WAMO-FW-RD")
    if r.returncode == 0:
        regeln = r.stdout
        e.pruefe("-A WAMO-FW-RD -j DROP" in regeln and "-A WAMO-FW-RD ! -i br-+ -j DROP" in regeln, "Kette WAMO-FW-RD verwirft den Rest")
        freigaben = re.findall(r"-A WAMO-FW-RD -d (\S+)/32 -p tcp -m tcp --dport 3389 -j RETURN", regeln)
        if gelesen is not None:
            e.pruefe(sorted(freigaben) == sorted(ziele), "Freigaben der Kette entsprechen genau der Zielliste (leer: kein Ausgang)", f"{freigaben} / {ziele}")
    else:
        e.skip("Regeln der Kette WAMO-FW-RD", "sudo -n iptables nicht möglich")
    r = sh("sudo", "-n", "iptables", "-w", "5", "-S", "INPUT")
    if r.returncode == 0:
        zeilen = r.stdout.splitlines()
        e.pruefe(len(zeilen) > 1 and zeilen[1] == "-A INPUT -s 172.27.0.0/24 -j WAMO-FW-RDIN",
                 "INPUT: Pakete des Gateways an den Host gehen als erste Regel durch WAMO-FW-RDIN (vor ufw)", (zeilen[1] if len(zeilen) > 1 else "leer"))
    else:
        e.skip("INPUT-Sprung nach WAMO-FW-RDIN", "sudo -n iptables nicht möglich")
    r = sh("sudo", "-n", "iptables", "-w", "5", "-S", "WAMO-FW")
    if r.returncode == 0:
        e.pruefe(re.search(r"--ctorigdstport 55446 (--ctdir ORIGINAL )?-j WAMO-FW-LAN", r.stdout) is not None, "Port 55446 nur für Loopback und Büro-LAN (WAMO-FW-LAN)")
    else:
        e.skip("Port 55446 in WAMO-FW", "sudo -n iptables nicht möglich")
    ausgang_proben(e, ziele)
    return e.summe()


def main() -> int:
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--betrieb", action="store_true", help="laufende Container auf Sokrates-2 prüfen")
    ap.add_argument("--ca", help="WAMO-CA (Betrieb)")
    ap.add_argument("--name", default="t1", help="Namensteil des Testaufbaus (wamo-test-rd-NAME-)")
    ap.add_argument("--behalten", action="store_true", help="Testaufbau nicht abräumen (für Browser-Proben)")
    ap.add_argument("--aufraeumen", action="store_true", help="nur den Testaufbau NAME abräumen")
    args = ap.parse_args()
    if not NAME_RE.fullmatch(args.name):
        print("Ungültiger --name (a-z, 0-9, höchstens 12 Zeichen)", file=sys.stderr)
        return 2
    if args.betrieb and not args.ca:
        ap.error("--betrieb braucht --ca (WAMO-CA)")
    if args.aufraeumen:
        praefix = f"wamo-test-rd-{args.name}-"
        aufraeumen_praefix(praefix)
        shutil.rmtree(arbeitsordner(praefix), ignore_errors=True)
        return 0
    try:
        return betrieb(args) if args.betrieb else testmodus(args)
    except Exception as ex:  # noqa: BLE001  (Zusammenfassung statt Traceback, damit der Aufrufer ein Ergebnis hat)
        print(f"  FEHLER   Rauchtest abgebrochen: {ex!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
