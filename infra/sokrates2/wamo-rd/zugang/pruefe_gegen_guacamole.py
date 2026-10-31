#!/usr/bin/env python3
"""Prüft die Anweisung des Kerns (voice-core/rd_api.py) und den Zugangsdienst gegen das ECHTE Apache Guacamole 1.6.0.

Läuft am PC mit Docker Desktop (oder auf einem Rechner mit Docker). Baut aus der echten Compose-Datei (infra/sokrates2/wamo-rd/) einen
eigenen Stack mit dem Namen wamo-test-rd-z1-... (eigene Netze, eigenes Volume), bildet den Kern durch einen Container mit der
Benutzerkennung des Kerns nach, der nur über den Unix-Socket spricht, und als Ziel dient eine Attrappe (ein Dienst, der Verbindungen
annimmt und hält). Es gibt keinen echten Spark als Ziel. Angelegt wird nur, was mit wamo-test-rd-z1- beginnt; jede Löschung prüft den
Präfix, fremde Container werden nie angefasst.

Aufruf:  voice-core/.venv/Scripts/python.exe infra/sokrates2/wamo-rd/zugang/pruefe_gegen_guacamole.py [--behalten] [--lang]
  --lang      zusätzlich das Höchstende von 60 Sekunden abwarten (rund 75 Sekunden mehr)
  --behalten  Stack stehen lassen

Geprüft wird (Rückgabe 0, wenn nichts fehlschlug):
  * Die Anweisung aus rd_api.anweisung_bauen wird von Guacamole angenommen und ergibt ein Sitzungstoken; die Verbindung ist die
    eine fest benannte, das Token hat nur Leserecht darauf; Passwort und Schlüssel erscheinen in keiner Antwort der REST-Schnittstelle.
  * Abgelaufene, falsch signierte und fremd abgesendete Anweisungen werden abgelehnt, jeweils mit genau einem Versuch.
  * Wirkung von expires (Befund): Mit Ablauf von expires wird das Sitzungstoken UNGÜLTIG (neue Anfragen scheitern), ein schon offener
    Tunnel läuft aber weiter. expires kann also keine kurze Frist nur für das Einreichen sein und ersetzt kein Höchstende für laufende Tunnel.
  * Beenden durch den Zugangsdienst löscht das Token und schließt den Tunnel; Trennung, nicht geöffnet und Höchstende werden erkannt.
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import shutil
import sys
import time
from pathlib import Path

HIER = Path(__file__).resolve().parent
SOKRATES2 = HIER.parent.parent
sys.path.insert(0, str(SOKRATES2))
sys.path.insert(0, str(SOKRATES2.parent.parent / "voice-core"))

import ipaddress  # noqa: E402
from urllib.parse import quote  # noqa: E402

import wamo_rd_rauchtest as rt  # noqa: E402

COMPOSE = SOKRATES2 / "wamo-rd" / "docker-compose.yml"
IMAGE_PY = "python:3.12-slim"
SCHLUESSEL = secrets.token_hex(16)
PASSWORT = "Pr0be" + secrets.token_hex(12)         # nur für diese Probe; erscheint nirgends
KERN_UID = "65101"
SITZUNGEN = {n: f"{n:x}0000000-1111-4222-8333-444444444444"[:36] for n in (1, 2, 3, 4, 5, 6)}

# Läuft im Kern-Container (Benutzerkennung des Kerns, nur Standardbibliothek): sendet eine Zeile an den Zugangsdienst oder ruft
# die Webanwendung auf. Ausgabe: eine Zeile "STATUS TEXT".
KERN_CODE = r'''
import http.client, json, socket, sys, urllib.parse
art = sys.argv[1]
if art == "relay":
    s = socket.socket(socket.AF_UNIX); s.settimeout(30); s.connect("/run/wamo-rd/ctl.sock")
    s.sendall(sys.argv[2].encode() + b"\n")
    daten = b""
    while not daten.endswith(b"\n"):
        stueck = s.recv(65536)
        if not stueck: break
        daten += stueck
    print("0", daten.decode().strip())
else:
    methode, host, pfad = sys.argv[2], sys.argv[3], sys.argv[4]
    body = sys.argv[5].encode() if len(sys.argv) > 5 else None
    c = http.client.HTTPConnection(host, 8080, timeout=15)
    c.request(methode, pfad, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"} if body else {})
    r = c.getresponse()
    print(r.status, r.read().decode(errors="replace").replace("\n", " "))
'''

# Läuft im Kern-Container: öffnet den WebSocket-Tunnel von Guacamole wie der Browser (Unterprotokoll guacamole), beantwortet sync,
# hält die Verbindung für `halte` Sekunden und schreibt Ereignisse in eine Datei (OFFEN, ENDE-Grund, GESCHLOSSEN).
WS_CODE = r'''
import base64, os, socket, struct, sys, time
host, pfad, halte, logdatei = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
def log(t):
    with open(logdatei, "a") as f: f.write(f"{time.time():.1f} {t}\n")
def maskiert(opcode, daten):
    m = os.urandom(4); n = len(daten)
    kopf = bytes([0x80 | opcode, 0x80 | n]) if n < 126 else bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
    return kopf + m + bytes(b ^ m[i % 4] for i, b in enumerate(daten))
def lesen(s, n):
    d = b""
    while len(d) < n:
        x = s.recv(n - len(d))
        if not x: raise EOFError
        d += x
    return d
try:
    s = socket.create_connection((host, 8080), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {pfad} HTTP/1.1\r\nHost: {host}:8080\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: guacamole\r\nOrigin: http://{host}:8080\r\n\r\n").encode())
    kopf = b""
    while b"\r\n\r\n" not in kopf: kopf += s.recv(1)
    status = kopf.split(b"\r\n")[0].decode()
    log("KOPF " + status)
    if " 101 " not in status: sys.exit(0)
    s.settimeout(1.0)
    ende = time.time() + halte
    while time.time() < ende:
        try:
            h = lesen(s, 2)
        except socket.timeout:
            continue
        op, n = h[0] & 0x0f, h[1] & 0x7f
        if n == 126: n = struct.unpack(">H", lesen(s, 2))[0]
        elif n == 127: n = struct.unpack(">Q", lesen(s, 8))[0]
        daten = lesen(s, n) if n else b""
        if op == 8:
            log("GESCHLOSSEN " + daten[2:].decode(errors="replace")); sys.exit(0)
        if op == 9: s.sendall(maskiert(10, daten))
        if op == 1:
            t = daten.decode(errors="replace")
            if "5.error," in t or "10.disconnect" in t: log("ENDE " + t[:160])
            if "4.sync," in t:
                ts = t.split("4.sync,", 1)[1].split(";")[0]
                s.sendall(maskiert(1, f"4.sync,{ts};".encode()))
            if "OFFEN" not in open(logdatei).read() and "4.sync" in t: log("OFFEN")
    log("GEHALTEN")
except EOFError:
    log("GESCHLOSSEN (Verbindung weg)")
except Exception as e:
    log("FEHLER " + type(e).__name__)
'''

ZIEL_CODE = r'''
import socket, sys, threading
def halten(c):
    try:
        c.sendall(b"login: ")
        while c.recv(1024): pass
    except OSError: pass
    print("ZU", flush=True)
    c.close()
for port in (23, 3389):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", port)); s.listen(5)
    def lauf(s=s):
        while True:
            c, _ = s.accept(); print("AUF", flush=True); threading.Thread(target=halten, args=(c,), daemon=True).start()
    threading.Thread(target=lauf, daemon=True).start()
threading.Event().wait()
'''


def verschluesseln(schluessel_hex: str, daten: dict) -> str:
    """Gegenprobe: dieselbe Anweisung wie rd_api, aber für beliebige Daten (zum Beispiel ein Telnet-Ziel für die Tunnelprobe)."""
    import hashlib
    import hmac

    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    k = bytes.fromhex(schluessel_hex)
    roh = json.dumps(daten, separators=(",", ":")).encode()
    polster = padding.PKCS7(128).padder()
    klar = polster.update(hmac.new(k, roh, hashlib.sha256).digest() + roh) + polster.finalize()
    enc = Cipher(algorithms.AES(k), modes.CBC(bytes(16))).encryptor()
    return base64.b64encode(enc.update(klar) + enc.finalize()).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--behalten", action="store_true")
    ap.add_argument("--lang", action="store_true")
    ap.add_argument("--schnell", action="store_true", help="nur bis zur Anweisung (Fehlersuche)")
    args = ap.parse_args()
    import rd_api  # aus voice-core

    p = "wamo-test-rd-z1-"
    projekt = p.rstrip("-")
    e = rt.Ergebnis()
    rt.aufraeumen_praefix(p)
    arbeit = rt.arbeitsordner(p)
    sub_rd, sub_guacd, sub_ziel = rt.freie_netze(3)
    net_rd = ipaddress.ip_network(sub_rd)
    zugang_ip, kern_ip, caddy_ip = str(net_rd[10]), str(net_rd[11]), str(net_rd[2])
    guacd_ip = str(ipaddress.ip_network(sub_guacd)[3])
    ziel_ip = str(ipaddress.ip_network(sub_ziel)[50])
    netz_rd, netz_guacd, netz_ziel, volume = p + "netz", p + "guacd-netz", p + "ziel", p + "run"
    web, zugang, kern, ziel = p + "rd-web", p + "rd-zugang", p + "kern", p + "ziel-attrappe"
    uid = "65100"
    env = {"WAMO_RD_PRAEFIX": p, "WAMO_RD_UID": uid, "WAMO_RD_GID": uid, "WAMO_RD_NETZ": netz_rd, "WAMO_RD_GUACD_NETZ": netz_guacd,
           "WAMO_RD_ZIEL_NETZ": netz_ziel, "WAMO_RD_GUACD_SUBNETZ": str(ipaddress.ip_network(sub_guacd)),
           "WAMO_RD_ZIEL_SUBNETZ": sub_ziel, "WAMO_RD_KEY": str(arbeit / "json.key"), "WAMO_RD_VERTRAUT": f"{zugang_ip}/32",
           "WAMO_RD_PROXY_REGEX": caddy_ip.replace(".", "[.]"), "WAMO_RD_GUACD_IP": guacd_ip, "WAMO_RD_ZUGANG_IP": zugang_ip,
           "WAMO_RD_KERN_UID": KERN_UID, "WAMO_RD_RUN": volume, "WAMO_RD_ZIELE_DATEI": str(arbeit / "ziele.toml")}
    try:
        arbeit.mkdir(parents=True, exist_ok=True)
        (arbeit / "json.key").write_text(SCHLUESSEL)
        (arbeit / "json.key").chmod(0o644)
        (arbeit / "ziele.toml").write_text(
            f'[[ziel]]\nname = "attrappe"\nanzeige = "Attrappe"\nadresse = "{ziel_ip}"\nport = 3389\n'
            '[[ziel]]\nname = "nirgends"\nanzeige = "Nirgends"\nadresse = "192.0.2.77"\n', encoding="utf-8")
        # Test-Zusatz: kurze Zeiten im Aufseher, Volume als benannte Ablage
        (arbeit / "override.yml").write_text(
            "services:\n  zugang:\n    environment:\n      WAMO_RD_AUFSEHER_TAKT: \"1\"\n      WAMO_RD_NICHT_GEOEFFNET_SECS: \"8\"\n"
            "      WAMO_RD_GETRENNT_SECS: \"4\"\nvolumes:\n  " + volume + ":\n    name: " + volume + "\n", encoding="utf-8")
        print(f"== Testaufbau {p} (Netze {sub_rd}, {sub_guacd}, {sub_ziel})")
        rt.sh("docker", "network", "create", "--internal", "--subnet", sub_rd, "-o", "com.docker.network.bridge.inhibit_ipv4=true",
              netz_rd, check=True)
        rt.sh("docker", "volume", "create", volume, check=True)
        rt.sh("docker", "run", "--rm", "-v", f"{volume}:/r", IMAGE_PY, "sh", "-c", f"chown {uid}:{KERN_UID} /r && chmod 2770 /r", check=True)  # wie im Betrieb: 2770 Dienst:Kern
        r = rt.sh("docker", "build", "--tag", f"{p}rd-zugang:1", str(HIER), timeout=600)
        if not e.pruefe(r.returncode == 0, "Image des Zugangsdienstes baut", (r.stderr or "")[-300:]):
            return e.summe()
        r = rt.sh("docker", "compose", "-p", projekt, "-f", str(COMPOSE), "-f", str(arbeit / "override.yml"), "up", "-d",
                  env=env, timeout=400)
        if not e.pruefe(r.returncode == 0, "docker compose up (Gateway mit Zugangsdienst)", (r.stderr or r.stdout)[-500:]):
            return e.summe()
        rt.sh("docker", "create", "--name", ziel, "--network", netz_ziel, "--ip", ziel_ip, IMAGE_PY, "python", "-u", "-c", ZIEL_CODE,
              check=True)
        rt.sh("docker", "start", ziel, check=True)
        rt.sh("docker", "create", "--name", kern, "--network", netz_rd, "--ip", kern_ip, "--user", f"{KERN_UID}:{KERN_UID}", "-v", f"{volume}:/run/wamo-rd",
              IMAGE_PY, "sleep", "900", check=True)
        rt.sh("docker", "start", kern, check=True)
        if not e.pruefe(rt.warte_gesund([web, p + "rd-guacd", zugang]), "Gateway und Zugangsdienst werden gesund"):
            print(rt.sh("docker", "logs", "--tail", "40", zugang).stdout[-1500:])
            return e.summe()

        def relay(**anfrage) -> dict:
            r_ = rt.sh("docker", "exec", kern, "python", "-c", KERN_CODE, "relay", json.dumps(anfrage), timeout=60)
            zeile = (r_.stdout or r_.stderr).strip()
            try:
                return json.loads(zeile.split(" ", 1)[1])
            except (IndexError, json.JSONDecodeError):
                return {"ok": False, "code": "PROBE_DEFEKT:" + zeile[:120]}

        def web_(methode: str, pfad: str, body: str = "", von: str = kern) -> tuple[int, str]:
            r_ = rt.sh("docker", "exec", von, "python", "-c", KERN_CODE, "web", methode, web, pfad, *([body] if body else []), timeout=60)
            zeile = (r_.stdout or r_.stderr).strip()
            code, _, text = zeile.partition(" ")
            return (int(code) if code.isdigit() else -1), text

        # ---------- Zugangsdienst allein
        print("== Zugangsdienst")
        probe = relay(cmd="proben")
        e.pruefe(probe.get("ok") is True and probe.get("ziele") == {"attrappe": True, "nirgends": False},
                 "Erreichbarkeit: die Attrappe ist erreichbar, 192.0.2.77 nicht", str(probe)[:200])
        e.pruefe(relay(cmd="status", sitzung="kein-uuid").get("code") == "sitzung_ungueltig", "Sitzungskennung muss eine UUID sein")
        # Der Benutzer eines anderen Containers kommt nicht an den Socket (SO_PEERCRED): Probe mit anderer Kennung
        fremd = rt.sh("docker", "run", "--rm", "--user", f"65199:{KERN_UID}", "-v", f"{volume}:/run/wamo-rd", IMAGE_PY, "python", "-c",
                      KERN_CODE, "relay", '{"cmd":"proben"}', timeout=60)
        e.pruefe('"ok":true' not in (fremd.stdout or ""), "fremde Benutzerkennung (richtige Gruppe, falsche Kennung) bekommt keine Antwort vom Zugangsdienst",
                 (fremd.stdout or fremd.stderr)[-100:])

        ziel_obj = rd_api.Ziel("attrappe", "Attrappe", ziel_ip, 3389)
        schluessel = bytes.fromhex(SCHLUESSEL)

        # ---------- Anweisung des Kerns gegen das echte Guacamole
        print("== Anweisung gegen Guacamole 1.6.0")
        gut = rd_api.anweisung_bauen(schluessel, SITZUNGEN[1], ziel_obj, PASSWORT)
        a = relay(cmd="anmelden", sitzung=SITZUNGEN[1], data=gut, max_secs=3600)
        token = a.get("token")
        e.pruefe(a.get("ok") is True and isinstance(token, str), "gültige Anweisung ergibt ein Sitzungstoken", str(a)[:200])
        e.pruefe(set(a) == {"ok", "token"}, "Antwort des Zugangsdienstes enthält nur das Token")
        alle_antworten = ""
        if token:
            s, t = web_("GET", f"/api/session/data/json/connectionGroups/ROOT/tree?token={token}")
            alle_antworten += t
            baum = json.loads(t) if s == 200 else {}
            namen = [c.get("name") for c in baum.get("childConnections", [])]
            e.pruefe(s == 200 and namen == [rd_api.VERBINDUNG], "mit dem Token ist genau die eine fest benannte Verbindung sichtbar", f"{s} {namen}")
            print("    Baum:", json.dumps(baum)[:400])
            e.pruefe(s == 200 and baum["childConnections"][0].get("protocol") == "rdp", "Protokoll ist rdp")
            s, t = web_("GET", f"/api/session/data/json/self/effectivePermissions?token={token}")
            alle_antworten += t
            rechte = json.loads(t) if s == 200 else {}
            e.pruefe(s == 200 and rechte.get("systemPermissions") == [] and rechte.get("connectionPermissions") == {rd_api.VERBINDUNG: ["READ"]},
                     "Token hat nur Leserecht auf die eine Verbindung, keine Systemrechte")
            s, t = web_("GET", f"/api/session/data/json/connections/{rd_api.VERBINDUNG}/parameters?token={token}")
            alle_antworten += t
            e.pruefe(s in (401, 403, 404), "Verbindungsparameter (Zielhost, Konto, Passwort) sind für das Token nicht lesbar", f"HTTP {s}")
            s, t = web_("GET", f"/api/session/data/json/connections/{rd_api.VERBINDUNG}?token={token}")
            alle_antworten += t
            s2, t2 = web_("GET", f"/api/session/data/json/activeConnections?token={token}")
            alle_antworten += t2
            e.pruefe(PASSWORT not in alle_antworten and SCHLUESSEL not in alle_antworten and ziel_ip not in alle_antworten,
                     "Passwort, Schlüssel und Zielhost erscheinen in keiner REST-Antwort")
            logs = rt.sh("docker", "logs", web).stdout + rt.sh("docker", "logs", web).stderr + rt.sh("docker", "logs", zugang).stdout
            logs += rt.sh("docker", "logs", p + "rd-guacd").stdout + rt.sh("docker", "logs", p + "rd-guacd").stderr
            e.pruefe(PASSWORT not in logs and SCHLUESSEL not in logs and token not in logs and gut not in logs,
                     "Passwort, Schlüssel, Token und Anweisung stehen in keinem Container-Protokoll")
            # die Seite lässt sich öffnen: Adresse des Clients (Verbindungsname + Quelle) wie in rd_api.seiten_url
            kennung = base64.b64encode(f"{rd_api.VERBINDUNG}\0c\0json".encode()).decode()
            e.pruefe(rd_api.seiten_url("http://127.0.0.1", token) == f"http://127.0.0.1/?token={token}#/client/{kennung}", "Adresse der Seite")

        # Telnet-Verbindung zur Attrappe, damit ein Tunnel entsteht (der Kern baut nur RDP; hier geht es um das Verhalten des Gateways)
        def tunnel_anweisung(sitzung: str, gueltig: float = 3600) -> str:
            return verschluesseln(SCHLUESSEL, {"username": "rd-t" + sitzung[:6], "expires": int((time.time() + gueltig) * 1000),
                                               "connections": {"t": {"protocol": "telnet", "parameters": {"hostname": ziel_ip, "port": "23"}}}})

        def tunnel_oeffnen(tok: str, name: str, halte: int = 300) -> None:
            """Öffnet den WebSocket-Tunnel wie der Browser und hält ihn im Hintergrund offen (Ereignisse in /tmp/ws-NAME.log)."""
            pfad = (f"/websocket-tunnel?token={tok}&GUAC_DATA_SOURCE=json&GUAC_ID=t&GUAC_TYPE=c"
                    "&GUAC_WIDTH=800&GUAC_HEIGHT=600&GUAC_DPI=96")
            rt.sh("docker", "exec", "-d", kern, "python", "-c", WS_CODE, web, pfad, str(halte), f"/tmp/ws-{name}.log")
            time.sleep(3)

        def tunnel_log(name: str) -> str:
            return rt.sh("docker", "exec", kern, "sh", "-c", f"cat /tmp/ws-{name}.log 2>/dev/null || true").stdout

        def aktive(tok: str) -> int:
            s_, t_ = web_("GET", f"/api/session/tunnels?token={tok}")
            return len(json.loads(t_)) if s_ == 200 else -1

        def ziel_zaehler() -> tuple[int, int]:
            zl_ = rt.sh("docker", "logs", ziel).stdout
            return zl_.count("AUF"), zl_.count("ZU")

        if args.schnell:
            return e.summe()
        print("== Wirkung von expires (gemessen am echten Guacamole)")
        kurz = rd_api.anweisung_bauen(schluessel, SITZUNGEN[3], ziel_obj, PASSWORT, gueltig_secs=6)
        a = relay(cmd="anmelden", sitzung=SITZUNGEN[3], data=kurz, max_secs=3600)
        t3 = a.get("token")
        e.pruefe(a.get("ok") is True, "Anweisung mit 6 Sekunden Gültigkeit wird angenommen")
        if t3:
            s, _ = web_("GET", f"/api/session/data/json/connectionGroups/ROOT/tree?token={t3}")
            e.pruefe(s == 200, "das Token gilt vor dem Ablauf", f"HTTP {s}")
            time.sleep(10)
            s, _ = web_("GET", f"/api/session/data/json/connectionGroups/ROOT/tree?token={t3}")
            e.pruefe(s in (401, 403), "BEFUND: expires gilt für die ganze Sitzung, das Token ist nach dem Ablauf ungültig", f"HTTP {s}")
        relay(cmd="beenden", sitzung=SITZUNGEN[3], grund="beendet")
        a = relay(cmd="anmelden", sitzung=SITZUNGEN[4], data=tunnel_anweisung(SITZUNGEN[4], gueltig=8), max_secs=3600)
        t4 = a.get("token")
        if t4:
            _, zu_vorher = ziel_zaehler()
            tunnel_oeffnen(t4, "expires")
            e.pruefe(aktive(t4) == 1, "Tunnel mit 8 Sekunden Gültigkeit steht (eine aktive Verbindung)", tunnel_log("expires")[-200:])
            time.sleep(12)
            lg = tunnel_log("expires")
            offen_nach_ablauf = "GESCHLOSSEN" not in lg and "ENDE" not in lg
            print(f"    BEFUND: ein schon offener Tunnel ist nach Ablauf von expires {'NOCH OFFEN' if offen_nach_ablauf else 'zu'} "
                  "(expires sperrt nur neue Anfragen mit dem Token; das Höchstende für laufende Tunnel setzt der Zugangsdienst durch)")
            e.pruefe(offen_nach_ablauf is not None, "Befund zu expires bei laufendem Tunnel festgehalten")
        if t4:
            relay(cmd="beenden", sitzung=SITZUNGEN[4], grund="beendet")
            time.sleep(4)
            e.pruefe("GESCHLOSSEN" in tunnel_log("expires"), "Beenden durch den Zugangsdienst schließt auch diesen Tunnel", tunnel_log("expires")[-120:])

        print("== Beenden, Trennung, nicht geöffnet")
        auf0, zu0 = ziel_zaehler()
        a = relay(cmd="anmelden", sitzung=SITZUNGEN[5], data=tunnel_anweisung(SITZUNGEN[5]), max_secs=3600)
        t5 = a.get("token")
        e.pruefe(bool(t5), "Telnet-Anweisung für die Tunnelprobe wird angenommen", str(a)[:150])
        if t5:
            tunnel_oeffnen(t5, "beenden")
            e.pruefe("OFFEN" in tunnel_log("beenden"), "Tunnel zur Attrappe öffnet sich", tunnel_log("beenden")[-200:])
            e.pruefe(aktive(t5) == 1, "Guacamole zählt eine aktive Verbindung")
            st = relay(cmd="status", sitzung=SITZUNGEN[5])
            e.pruefe(st.get("verbunden") is True, "der Zugangsdienst sieht die Verbindung", str(st))
            relay(cmd="beenden", sitzung=SITZUNGEN[5], grund="beendet")
            time.sleep(6)
            e.pruefe(aktive(t5) == -1, "nach dem Beenden gilt das Token nicht mehr")
            e.pruefe("GESCHLOSSEN" in tunnel_log("beenden"), "der Tunnel im Browser wird geschlossen",
                     tunnel_log("beenden")[-200:])
            for _ in range(20):  # guacd braucht einen Moment, bis es die Verbindung zum Ziel schließt
                auf, zu = ziel_zaehler()
                if zu > zu0:
                    break
                time.sleep(1)
            e.pruefe(zu > zu0, "die Verbindung von guacd zum Ziel ist geschlossen (Beenden durch den Zugangsdienst)", f"AUF {auf} ZU {zu}")
            if zu <= zu0:
                print("    guacd:", rt.sh("docker", "logs", "--tail", "12", p + "rd-guacd").stderr[-900:])

        a = relay(cmd="anmelden", sitzung=SITZUNGEN[6], data=tunnel_anweisung(SITZUNGEN[6]), max_secs=3600)
        t6 = a.get("token")
        if t6:
            tunnel_oeffnen(t6, "getrennt", halte=6)       # der Browser schließt nach 6 Sekunden
            e.pruefe(relay(cmd="status", sitzung=SITZUNGEN[6]).get("verbunden") is True, "zweite Sitzung ist verbunden")
            time.sleep(12)
            st = relay(cmd="status", sitzung=SITZUNGEN[6])
            e.pruefe(st.get("offen") is False and st.get("grund") == "getrennt", "nach dem Schließen des Tunnels endet die Sitzung als getrennt", str(st))

        nie = rd_api.anweisung_bauen(schluessel, "dddddddd-1111-4222-8333-444444444444", ziel_obj, PASSWORT)
        a = relay(cmd="anmelden", sitzung="dddddddd-1111-4222-8333-444444444444", data=nie, max_secs=3600)
        e.pruefe(a.get("ok") is True, "Sitzung ohne Nutzung wird angelegt")
        time.sleep(14)
        st = relay(cmd="status", sitzung="dddddddd-1111-4222-8333-444444444444")
        e.pruefe(st.get("offen") is False and st.get("grund") == "nicht_geoeffnet", "eine nie geöffnete Sitzung endet", str(st))

        if args.lang:
            print("== Höchstende durch den Zugangsdienst (60 Sekunden, das Minimum; die Anweisung gilt länger)")
            lang = "eeeeeeee-1111-4222-8333-444444444444"
            a = relay(cmd="anmelden", sitzung=lang, data=tunnel_anweisung(lang), max_secs=60)
            tl = a.get("token")
            if tl:
                tunnel_oeffnen(tl, "lang")
            e.pruefe(bool(tl) and aktive(tl) == 1, "Tunnel offen vor dem Höchstende")
            time.sleep(66)
            st = relay(cmd="status", sitzung=lang)
            e.pruefe(st.get("offen") is False and st.get("grund") == "zeitlimit", "nach 60 Sekunden beendet der Zugangsdienst die Sitzung", str(st))
            e.pruefe(bool(tl) and aktive(tl) == -1, "und das Token samt Tunnel ist bei Guacamole gelöscht")
            e.pruefe("GESCHLOSSEN" in tunnel_log("lang"), "der Tunnel im Browser ist zu", tunnel_log("lang")[-200:])
        else:
            e.skip("Höchstende durch den Zugangsdienst", "nur mit --lang")

        # Zuletzt, weil Fehlversuche die Absenderadresse sperren (3 in 15 Minuten): genau zwei Fehlversuche des Zugangsdienstes
        print("== Ablehnung")
        alt = rd_api.anweisung_bauen(schluessel, SITZUNGEN[2], ziel_obj, PASSWORT, jetzt=time.time() - 7200)
        a = relay(cmd="anmelden", sitzung=SITZUNGEN[2], data=alt, max_secs=3600)
        e.pruefe(a.get("ok") is False and a.get("code") == "abgelehnt", "abgelaufene Anweisung wird abgelehnt", str(a)[:150])
        falsch = rd_api.anweisung_bauen(bytes.fromhex("ff" * 16), SITZUNGEN[2], ziel_obj, PASSWORT)
        a = relay(cmd="anmelden", sitzung=SITZUNGEN[2], data=falsch, max_secs=3600)
        e.pruefe(a.get("ok") is False and a.get("code") == "abgelehnt", "falsch signierte Anweisung wird abgelehnt", str(a)[:150])
        s, t = web_("POST", "/api/tokens", "data=" + quote(gut, safe=""))
        e.pruefe(s in (401, 403), "gültige Anweisung von einer anderen Adresse als dem Zugangsdienst wird abgelehnt", f"HTTP {s}")
        s, t = web_("POST", "/api/tokens", "data=" + "A" * 200)
        e.pruefe(s in (401, 403), "Müll als Anweisung wird abgelehnt", f"HTTP {s}")
        return e.summe()
    finally:
        if not args.behalten:
            try:
                rt.sh("docker", "compose", "-p", projekt, "-f", str(COMPOSE), "-f", str(arbeit / "override.yml"), "down", "-v",
                      env={**env}, timeout=120)
            finally:
                rt.aufraeumen_praefix(p)
                shutil.rmtree(arbeit, ignore_errors=True)
                rt.sh("docker", "image", "rm", f"{p}rd-zugang:1")


if __name__ == "__main__":
    sys.exit(main())
