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

Rückgabe 0, wenn nichts fehlschlug (übersprungene Proben zählen nicht als Fehler, werden aber genannt).
"""
from __future__ import annotations

import argparse
import base64
import http.client as httpclient
import ipaddress
import json
import re
import secrets
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
import base64, hashlib, hmac, json, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
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
    import os
    r = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       input=eingabe, env={**os.environ, **(env or {})})
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
    r = sh("docker", "inspect", name)
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
    """'offen', wenn der Container die Verbindung aufbauen kann, sonst 'zu' (keine Route, verworfen, abgelehnt, Zeitüberschreitung)."""
    if container.endswith("guacd"):
        cmd = ["docker", "exec", container, "nc", "-w", "3", "-z", host, str(port)]
    else:  # bash ist im Image der Webanwendung vorhanden, nc nicht
        cmd = ["docker", "exec", container, "timeout", "4", "bash", "-c", f"exec 3<>/dev/tcp/{host}/{port}"]
    return "offen" if sh(*cmd, timeout=20).returncode == 0 else "zu"


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
    e.pruefe(("x-frame-options", ["DENY"]) in k.items(), "X-Frame-Options DENY")
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
        e.pruefe("httponly" in c.lower() and "samesite" in c.lower(), "Cookie mit HttpOnly und SameSite", c.split("=")[0])
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
    # Es gibt weder Konten noch Anmeldeerweiterungen
    s, _, body = http(basis + "/api/extensions", ca=ca)
    e.pruefe(s != 200 or b"jdbc" not in body.lower(), "keine Datenbank-Erweiterung geladen")


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


def warte_gesund(namen: list[str], sekunden: int = 150) -> bool:
    ende = time.time() + sekunden
    while time.time() < ende:
        if all((inspect(n) or {}).get("State", {}).get("Health", {}).get("Status") == "healthy" for n in namen):
            return True
        time.sleep(3)
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
    tmp = tempfile.TemporaryDirectory(prefix="wamo-test-rd-")
    arbeit = Path(tmp.name)
    schluessel = secrets.token_hex(16)
    (arbeit / "json.key").write_text(schluessel)  # ohne Zeilenumbruch: Guacamole liest die Datei unverändert
    (arbeit / "json.key").chmod(0o644)  # nur Testschlüssel; im Betrieb gilt 0640 root:wamo-rd-gateway
    uid = "65100"
    env = {"WAMO_RD_PRAEFIX": p, "WAMO_RD_UID": uid, "WAMO_RD_GID": uid, "WAMO_RD_NETZ": netz_rd, "WAMO_RD_GUACD_NETZ": netz_guacd,
           "WAMO_RD_ZIEL_NETZ": netz_ziel, "WAMO_RD_GUACD_SUBNETZ": str(ipaddress.ip_network(sub_guacd)),
           "WAMO_RD_ZIEL_SUBNETZ": sub_ziel, "WAMO_RD_KEY": str(arbeit / "json.key"), "WAMO_RD_VERTRAUT": f"{vertraut_ip}/32",
           "WAMO_RD_PROXY_REGEX": caddy_ip.replace(".", "[.]")}
    try:
        print(f"== Testaufbau {p} (Netze {sub_rd}, {sub_guacd}, {sub_ziel}; vertraute Adresse {vertraut_ip})")
        sh("docker", "network", "create", "--internal", "--subnet", sub_rd, "-o", "com.docker.network.bridge.inhibit_ipv4=true", netz_rd, check=True)
        r = sh("docker", "compose", "-p", projekt, "-f", str(COMPOSE), "up", "-d", env=env, timeout=400)
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
        if not e.pruefe(warte_gesund([web, guacd]), "Container werden gesund"):
            print(sh("docker", "logs", "--tail", "40", web).stdout[-2000:])
            return e.summe()
        port = sh("docker", "port", caddy, "8080/tcp", check=True).stdout.split(":")[-1].strip()
        basis = f"http://127.0.0.1:{port}"
        time.sleep(2)
        netze = {"rd": netz_rd, "guacd": netz_guacd, "ziel": netz_ziel}
        pruefe_container(e, web, guacd, netze)
        # Das Netz wamo-rd-netz gehört im Betrieb zu wamo-caddy; hier legt es der Test an. Vergleichbare Proben:
        pruefe_kopfzeilen(e, basis, None, nur_ein_fehlversuch=False)

        print("== Isolation (aus den Containern)")
        e.pruefe(tcp_von(web, guacd, 4822) == "offen", "Kontrolle: web erreicht guacd:4822 (Probe funktioniert)")
        for host, port_ in (("1.1.1.1", 443), ("8.8.8.8", 53), ("host.docker.internal", 22), ("169.254.169.254", 80)):
            e.pruefe(tcp_von(web, host, port_) == "zu", f"web erreicht {host}:{port_} nicht")
        e.pruefe(tcp_von(guacd, container_ip(caddy, netz_rd), 8080) == "zu", "guacd erreicht Caddy im Netz wamo-rd-netz nicht")
        e.pruefe(tcp_von(guacd, container_ip(zugang, netz_rd), 22) == "zu", "guacd erreicht das Netz wamo-rd-netz nicht")
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
            s, _, _ = http(f"{basis}/api/session/data/json/users/rauchtest?token={token}", methode="PUT", daten={})
            e.pruefe(s in (405, 401, 403), "Schreiben auf Benutzer abgewiesen", f"HTTP {s}")
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
            aufraeumen_praefix(p)
            tmp.cleanup()


# ======================================================================== Betrieb
def lies_ziele(pfad: str = "/etc/wamo-rd/ziele.toml") -> list[str]:
    try:
        text = Path(pfad).read_text(encoding="utf-8")
    except OSError:
        return []
    return re.findall(r'^\s*adresse\s*=\s*"(\d{1,3}(?:\.\d{1,3}){3})"', text, flags=re.M)


def betrieb(args: argparse.Namespace) -> int:
    e = Ergebnis()
    ca = args.ca
    netze = {"rd": "wamo-rd-netz", "guacd": "wamo-rd-guacd-netz", "ziel": "wamo-rd-ziel"}
    pruefe_container(e, "wamo-rd-web", "wamo-rd-guacd", netze)
    pruefe_kopfzeilen(e, BETRIEB_URL, ca)
    print("== TLS")
    try:
        ctx = ssl.create_default_context(cafile=ca)
        import socket
        with socket.create_connection(("192.168.178.136", 55446), timeout=10) as s, ctx.wrap_socket(s, server_hostname="192.168.178.136") as t:
            e.ok("Zertifikat der WAMO-CA gilt für 192.168.178.136:55446", t.version() or "")
    except Exception as ex:  # noqa: BLE001
        e.fehler("TLS-Prüfung", repr(ex))

    print("== Anmeldung aus dem Netz (ohne Schlüssel, ohne vertraute Adresse)")
    r = sh("docker", "exec", "wamo-caddy", "wget", "-q", "-S", "-O", "/dev/null", "--post-data=data=AAAA", "http://wamo-rd-web:8080/api/tokens")
    e.pruefe(" 403 " in r.stderr or " 401 " in r.stderr, "direkt an die Webanwendung (nicht vertraute Adresse, ohne Signatur) abgewiesen",
             (r.stderr.strip().splitlines() or ["keine Antwort"])[0].strip())

    print("== Isolation (aus den Containern)")
    e.pruefe(tcp_von("wamo-rd-web", "wamo-rd-guacd", 4822) == "offen", "Kontrolle: web erreicht guacd:4822 (Probe funktioniert)")
    ziele = lies_ziele()
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
    if ziele:
        for z in ziele:
            e.ok(f"Ziel {z}:3389 (erlaubt): {tcp_von('wamo-rd-guacd', z, 3389)}", "Hinweis, ob das Ziel gerade antwortet")
    else:
        e.ok("Zielliste ist leer: guacd hat keinen erlaubten Ausgang")
    r = sh("sudo", "-n", "iptables", "-w", "5", "-S", "WAMO-FW-RD")
    if r.returncode == 0:
        regeln = r.stdout
        e.pruefe("-A WAMO-FW-RD -j DROP" in regeln and "-A WAMO-FW-RD ! -i br-+ -j DROP" in regeln, "Kette WAMO-FW-RD verwirft den Rest")
        freigaben = re.findall(r"-A WAMO-FW-RD -d (\S+)/32 -p tcp -m tcp --dport 3389 -j RETURN", regeln)
        e.pruefe(sorted(freigaben) == sorted(ziele), "Freigaben der Kette entsprechen genau der Zielliste", f"{freigaben} / {ziele}")
    else:
        e.skip("Regeln der Kette WAMO-FW-RD", "sudo -n iptables nicht möglich")
    r = sh("sudo", "-n", "iptables", "-w", "5", "-S", "INPUT")
    if r.returncode == 0:
        e.pruefe(re.search(r"^-A INPUT -s 172\.27\.0\.0/24 -j WAMO-FW-RDIN$", r.stdout, flags=re.M) is not None, "INPUT: Pakete des Gateways an den Host gehen durch WAMO-FW-RDIN")
    r = sh("sudo", "-n", "iptables", "-w", "5", "-S", "WAMO-FW")
    if r.returncode == 0:
        e.pruefe(re.search(r"--ctorigdstport 55446 (--ctdir ORIGINAL )?-j WAMO-FW-LAN", r.stdout) is not None, "Port 55446 nur für Loopback und Büro-LAN (WAMO-FW-LAN)")
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
    if args.aufraeumen:
        aufraeumen_praefix(f"wamo-test-rd-{args.name}-")
        return 0
    return betrieb(args) if args.betrieb else testmodus(args)


if __name__ == "__main__":
    sys.exit(main())
