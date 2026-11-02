#!/usr/bin/env python3
"""Rauchtest des WAMO-Freigabe-Dienstes (Schritt 1.4b). Läuft auf Sokrates-2:

    sudo python3 -E -s /opt/wamo-freigabe/app/freigabe_rauchtest.py --produktiv

Teil A (immer): Eine Wegwerf-Instanz (eigener Socket, eigene ES256-Schlüssel, Attrappe der Datenbank) nimmt eine Freigabe an und
lehnt Falsches ab: der ganze Weg vom HTTP-Aufruf bis zur Freigabe, ohne das System zu ändern.
Teil B (--produktiv, als root): Die echte Installation wird nur gelesen und angesprochen: Prüfsummen, Dienst aktiv ohne Capabilities,
Socket-Rechte, Aufruf mit einem ungültigen Token (muss 401 liefern), andere Benutzer kommen nicht an den Socket, und als wamo-anker
(runuser): Datenbank antwortet auf select 1, der Schlüsselsatz von GoTrue lässt sich laden, SSH, Router und Internet sind gesperrt.
Exit 0 nur, wenn alles bestanden ist. Nie in der Ausgabe: Token, Schlüssel, Zugangsdaten.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # als root nie .pyc in den geschützten Programmordner schreiben (sonst schlägt die Prüfsumme an)

import argparse
import json
import os
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

HIER = os.path.dirname(os.path.realpath(__file__))
for ordner in (HIER, os.path.join(HIER, "anker"), os.path.join(HIER, "pg")):  # installiert liegt alles in HIER, im Repo in anker/ und pg/
    if os.path.isdir(ordner) and ordner not in sys.path:
        sys.path.append(ordner)

ERGEBNIS = {"ok": 0, "fehler": 0}
APP = "/opt/wamo-freigabe/app"
SOCKET = "/run/wamo-freigabe/freigabe.sock"
BENUTZER = "wamo-anker"
ANFRAGE = "423e4567-e89b-12d3-a456-426614174000"
ADMIN = "123e4567-e89b-12d3-a456-426614174000"
COMMIT, TREE = "a" * 40, "b" * 40


def pruefe(name: str, ok: bool, detail: str = "") -> None:
    ERGEBNIS["ok" if ok else "fehler"] += 1
    print(f"  {'OK    ' if ok else 'FEHLER'} {name}" + ("" if ok else f": {detail}"))


def http(pfad: str, token: str, body: bytes, sock: str, methode: str = "POST") -> tuple[int, dict]:
    kopf = (f"{methode} {pfad} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n").encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(10)
        s.connect(sock)
        s.sendall(kopf + body)
        roh = b""
        while teil := s.recv(4096):
            roh += teil
    status = int(roh.split(b" ", 2)[1])
    return status, json.loads(roh.split(b"\r\n\r\n", 1)[1])


def als_benutzer(name: str, code: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["runuser", "-u", name, "--", sys.executable, "-E", "-s", "-B", "-c", code, *argv],
                          capture_output=True, text=True, timeout=60, cwd="/")


def teil_a() -> None:
    print("== A. Wegwerf-Instanz")
    import freigabe as F
    import freigabe_dienst as D
    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec

    schluessel = ec.generate_private_key(ec.SECP256R1())
    iss, aud = "https://wegwerf.invalid/auth/v1", "authenticated"
    db = F.AttrappeFreigabeDatenbank()
    db.admins = {ADMIN, "223e4567-e89b-12d3-a456-426614174000"}
    db.anfragen[ANFRAGE] = {"status": "wartet_freigabe", "art": "normal", "v_commit": COMMIT, "tree_id": TREE,
                            "angefordert_von": ADMIN, "wartet_bis": None}
    dienst = F.Freigabedienst(db, schluessel=lambda kid: schluessel.public_key() if kid == "k1" else (_ for _ in ()).throw(KeyError(kid)),
                              aussteller=iss, zielgruppe=aud)
    pfad = os.path.join(tempfile.mkdtemp(prefix="freigabe-rauch-"), "f.sock")
    server = D.Server(pfad, D.Anwendung(dienst), frozenset({os.geteuid()}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def token(alter: int = 5, key=schluessel) -> str:
            jetzt = int(time.time())
            return jwt.encode({"sub": ADMIN, "iss": iss, "aud": aud, "aal": "aal2", "iat": jetzt, "exp": jetzt + 600,
                               "amr": [{"method": "totp", "timestamp": jetzt - alter}]}, key, algorithm="ES256", headers={"kid": "k1"})

        url = f"/wamo/release/{ANFRAGE}/freigeben"
        body = json.dumps({"commit_id": COMMIT, "tree_id": TREE}).encode()
        s, a = http(url, token(alter=500), body, pfad)
        pruefe("zu alter zweiter Faktor abgelehnt", (s, a.get("grund")) == (403, "aal2_alt"), str((s, a))[:150])
        s, a = http(url, token(key=ec.generate_private_key(ec.SECP256R1())), body, pfad)
        pruefe("fremde Signatur abgelehnt", (s, a.get("grund")) == (401, "token_ungueltig"), str((s, a))[:150])
        s, a = http(url, token(), json.dumps({"commit_id": COMMIT, "tree_id": "c" * 40}).encode(), pfad)
        pruefe("falsche Fassung abgelehnt", (s, a.get("grund")) == (409, "fassung_passt_nicht"), str((s, a))[:150])
        pruefe("bis hierher nichts geschrieben", db.freigaben == [], str(db.freigaben)[:100])
        s, a = http(url, token(), body, pfad)
        pruefe("gültige Freigabe", s == 200 and a.get("ok") is True and len(db.freigaben) == 1, str((s, a))[:150])
        s, a = http(url, token(), body, pfad)
        pruefe("zweite Freigabe derselben Person abgelehnt", (s, a.get("grund")) == (409, "schon_freigegeben"), str((s, a))[:150])
        s, a = http("/anderes", token(), body, pfad)
        pruefe("anderer Pfad", s == 404, str(s))
        s, a = http(url, token(), body, pfad, methode="GET")
        pruefe("andere Methode", s == 405, str(s))
    finally:
        server.shutdown()
        server.server_close()


def teil_b() -> None:
    print("== B. Echte Installation (nur lesend und anfragend)")
    import freigabe_dienst as D

    root = os.geteuid() == 0
    probleme = D.installation_pruefen(APP, "/opt/wamo-freigabe/pylib", "/opt/wamo-freigabe/pylib.SHA256SUMS")
    pruefe("Prüfsummen und Rechte der installierten Dateien", probleme == [], "; ".join(probleme)[:200])
    r = subprocess.run(["systemctl", "is-active", "wamo-freigabe.service"], capture_output=True, text=True)
    pruefe("Dienst wamo-freigabe aktiv", r.stdout.strip() == "active", r.stdout.strip())
    pid = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", "wamo-freigabe.service"], capture_output=True, text=True).stdout.strip()
    if pid.isdigit() and int(pid) > 0:
        status = open(f"/proc/{pid}/status", encoding="utf-8").read()
        feld = {z.split(":")[0]: z.split(":", 1)[1].strip() for z in status.splitlines() if ":" in z}
        pruefe("Dienstprozess ohne Capabilities", feld.get("CapEff", "x").strip("0") == "", feld.get("CapEff", ""))
        pruefe("Dienstprozess ohne neue Rechte", feld.get("NoNewPrivs") == "1", feld.get("NoNewPrivs", ""))
        pruefe(f"Dienstprozess läuft als {BENUTZER}", int(feld["Uid"].split()[0]) == pwd.getpwnam(BENUTZER).pw_uid, feld["Uid"])
    else:
        pruefe("Hauptprozess bekannt", False, pid)
    try:
        st = os.lstat(SOCKET)
        pruefe(f"Socket 0660 {BENUTZER}", stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o660
               and st.st_uid == pwd.getpwnam(BENUTZER).pw_uid, f"{oct(stat.S_IMODE(st.st_mode))} {st.st_uid}")
    except OSError as e:
        pruefe("Socket vorhanden", False, str(e))
    if not root:
        print("  übersprungen: Aufrufe am Socket und Proben als wamo-anker brauchen root")
        return
    url = f"/wamo/release/{ANFRAGE}/freigeben"
    kein_token = "x" * 30 + ".yy.zz"
    s, a = http(url, kein_token, json.dumps({"commit_id": COMMIT, "tree_id": TREE}).encode(), SOCKET)
    pruefe("ungültiges Token am echten Socket: 401", (s, a.get("grund")) == (401, "token_ungueltig"), str((s, a))[:150])
    for b in ("wamo", "nobody"):
        try:
            pwd.getpwnam(b)
        except KeyError:
            continue
        r = als_benutzer(b, "import socket,sys\ns=socket.socket(socket.AF_UNIX)\ntry:\n s.connect(sys.argv[1]);print('VERBUNDEN')\nexcept OSError as e:\n print('ABGELEHNT')\n", SOCKET)
        pruefe(f"Socket als {b} abgewiesen", "ABGELEHNT" in r.stdout, (r.stdout + r.stderr)[:150])
    # als wamo-anker: Datenbank und Schlüsselsatz mit genau dem Code und den Einstellungen des Dienstes
    code = ("import sys, os\nsys.path.insert(0, sys.argv[1])\nimport freigabe_dienst as D\nfrom wamo_pg import Pg, zugangsdatei_lesen\n"
            "import jwt, psycopg, cryptography\nprint('LIBS', all(m.__file__.startswith(D.PYLIB + '/') for m in (jwt, psycopg, cryptography)), jwt.__version__)\n"
            "z = zugangsdatei_lesen(D.ZUGANG_DATEI)\nprint('DB', Pg(z, anwendung='wamo-freigabe').zeilen('select 1'))\n"
            "q = D.JwksQuelle(lambda: D.jwks_abrufen(sys.argv[2], z.sslrootcert))\ntry:\n q('x')\nexcept KeyError:\n pass\nprint('SCHLUESSEL', len(q._schluessel))\n")
    jwks = subprocess.run(["systemctl", "show", "-p", "Environment", "--value", "wamo-freigabe.service"], capture_output=True, text=True).stdout
    url_jwks = next((t.split("=", 1)[1] for t in jwks.split() if t.startswith("WAMO_FREIGABE_JWKS_URL=")), "")
    r = als_benutzer(BENUTZER, code, APP, url_jwks)
    pruefe("Datenbank antwortet auf select 1 (Rolle wamo_anker_s2)", r.returncode == 0 and "DB " in r.stdout, (r.stdout + r.stderr)[-200:])
    pruefe("PyJWT, cryptography und psycopg kommen aus dem gesicherten Ordner, nicht aus dem System", "LIBS True" in r.stdout,
           (r.stdout + r.stderr)[-200:])
    n = [z for z in r.stdout.splitlines() if z.startswith("SCHLUESSEL")]
    pruefe("Schlüsselsatz von GoTrue ladbar (mindestens ein ES256-Schlüssel)", bool(n) and int(n[0].split()[1]) >= 1, (r.stdout + r.stderr)[-200:])
    netz = ("import socket, sys\ns = socket.socket(); s.settimeout(3)\ntry:\n s.connect((sys.argv[1], int(sys.argv[2]))); print('VERBUNDEN')\n"
            "except OSError:\n print('ABGELEHNT')\n")
    for ziel, port, name in (("192.168.178.136", 22, "SSH auf Sokrates-2"), ("192.168.178.1", 80, "Router im LAN"), ("1.1.1.1", 443, "Internet")):
        r = als_benutzer(BENUTZER, netz, ziel, str(port))
        pruefe(f"{BENUTZER} erreicht {name} nicht", "ABGELEHNT" in r.stdout, (r.stdout + r.stderr)[:150])
    r = subprocess.run(["nft", "list", "table", "inet", "wamo_freigabe_ausgang"], capture_output=True, text=True)
    pruefe("nft-Tabelle wamo_freigabe_ausgang geladen", r.returncode == 0 and "drop" in r.stdout, r.stderr[:150])


def main() -> int:
    ap = argparse.ArgumentParser(description="Rauchtest des WAMO-Freigabe-Dienstes")
    ap.add_argument("--produktiv", action="store_true", help="auch die echte Installation prüfen")
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        print("Der Rauchtest läuft nur unter Linux.", file=sys.stderr)
        return 1
    print(f"Freigabe-Rauchtest, Code aus {HIER}, {'root' if os.geteuid() == 0 else 'Benutzer-ID ' + str(os.geteuid())}")
    teil_a()
    if args.produktiv:
        teil_b()
    print(f"Ergebnis: {ERGEBNIS['ok']} bestanden, {ERGEBNIS['fehler']} fehlgeschlagen")
    return 0 if ERGEBNIS["fehler"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
