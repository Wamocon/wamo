#!/usr/bin/python3 -I
"""Rauchtest des WAMO-Notankers auf Sokrates-2 (Schritt 0.1 der Selbstüberarbeitung). Nur Standardbibliothek.

Aufruf (als root, damit die Verbotsproben als andere Benutzer laufen können):

    sudo python3 -I /usr/local/lib/wamo-anker/anker_rauchtest.py                # Wegwerf-Instanz, nichts Produktives
    sudo python3 -I /usr/local/lib/wamo-anker/anker_rauchtest.py --produktiv    # zusätzlich die echte Installation
    sudo python3 -I anker_rauchtest.py --benutzer wamo,wamo-core,nobody         # andere Benutzer für die Verbotsproben

Teil A (immer): Eine Wegwerf-Instanz des Ankers läuft in einem eigenen Ordner mit eigenem Socket und eigenen Attrappen
der Schritt-Skripte. Geprüft werden Befehle, Verbotsproben (auch als andere Benutzer über runuser), die Sperre,
--trocken, die Hashkette samt Manipulation und die Dauer des Rückwegs (Anteil des Ankers; die echten Skripte kommen mit
den Stufen 1 und 2 und werden dann mit derselben Messung geübt).
Teil B (--produktiv): Die echte Installation wird nur gelesen und angesprochen: Rechte der Ordner und Dateien, Prüfsumme,
Schreibproben als wamo, wamo-core und nobody (müssen scheitern), Socket-Rechte, Aufruf des echten Sockets als diese
Benutzer (müssen abgewiesen werden), `status` als root. Der echte Socket schreibt dabei Zeilen ins Audit-Protokoll.
Exit 0 nur, wenn alles bestanden ist.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

try:  # nur Linux; die Plattformprüfung in main() meldet es freundlich
    import pwd
except ImportError:
    pwd = None

sys.dont_write_bytecode = True  # beim Import aus der Installation darf kein __pycache__ neben die Prüfsumme geraten
HIER = os.path.dirname(os.path.realpath(__file__))
PRODUKTIV_LIB = "/usr/local/lib/wamo-anker"
ID = [0]
ERGEBNIS = {"ok": 0, "fehler": 0, "uebersprungen": 0}

# Wird als anderer Benutzer ausgeführt: verbindet, sendet eine Zeile, gibt die Antwort als JSON aus.
CLIENT = (
    "import json, socket, sys\n"
    "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
    "s.settimeout(5)\n"
    "try:\n"
    "    s.connect(sys.argv[1])\n"
    "except OSError as e:\n"
    "    print(json.dumps({'verbunden': False, 'fehler': type(e).__name__})); sys.exit(0)\n"
    "s.sendall(sys.argv[2].encode() + b'\\n')\n"
    "s.shutdown(socket.SHUT_WR)\n"
    "daten = b''\n"
    "while True:\n"
    "    teil = s.recv(4096)\n"
    "    if not teil:\n"
    "        break\n"
    "    daten += teil\n"
    "print(json.dumps({'verbunden': True, 'antwort': json.loads(daten)}))\n"
)
# Schreibprobe als anderer Benutzer: versucht, eine Datei in einem Ordner anzulegen.
SCHREIBER = (
    "import os, sys\n"
    "try:\n"
    "    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n"
    "    os.close(fd)\n"
    "    os.unlink(sys.argv[1])\n"
    "    print('GESCHRIEBEN')\n"
    "except OSError as e:\n"
    "    print('VERWEIGERT ' + type(e).__name__)\n"
)


def pruefe(name: str, bedingung: bool, detail: str = "") -> bool:
    ERGEBNIS["ok" if bedingung else "fehler"] += 1
    print(f"  {'OK    ' if bedingung else 'FEHLER'} {name}" + (f": {detail}" if detail and not bedingung else ""))
    return bedingung


def uebersprungen(name: str, grund: str) -> None:
    ERGEBNIS["uebersprungen"] += 1
    print(f"  SKIP   {name}: {grund}")


def lade_anker(installiert: bool):
    kandidaten = [os.path.join(PRODUKTIV_LIB, "wamo_anker.py")] if installiert else [
        os.path.join(HIER, "anker", "wamo_anker.py"), os.path.join(HIER, "wamo_anker.py")]
    for pfad in kandidaten:
        if os.path.isfile(pfad):
            spec = importlib.util.spec_from_file_location("wamo_anker", pfad)
            modul = importlib.util.module_from_spec(spec)
            sys.modules["wamo_anker"] = modul
            spec.loader.exec_module(modul)
            return modul, pfad
    raise SystemExit("wamo_anker.py nicht gefunden (neben diesem Skript, in anker/ oder in " + PRODUKTIV_LIB + ")")


def zeile(text: str) -> str:
    ID[0] += 1
    return f"{ID[0]:016x} {text}"


def lies(pfad: str) -> bytes:
    with open(pfad, "rb") as f:
        return f.read()


def frage(sockpfad: str, text: str, roh: bool = False) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(30)
        s.connect(sockpfad)
        s.sendall((text if roh else zeile(text)).encode("utf-8", "surrogateescape") + b"\n")
        s.shutdown(socket.SHUT_WR)
        daten = b""
        while teil := s.recv(4096):
            daten += teil
    return json.loads(daten)


def als_benutzer(benutzer: str, argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["runuser", "-u", benutzer, "--", sys.executable, "-I", "-c", *argv],
                          capture_output=True, text=True, timeout=30, cwd="/")


def kommando(argv: list[str]) -> tuple[int, str]:
    """(Exit-Code, Ausgabe); ein fehlendes Programm ist Code 127 statt eines Absturzes."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, type(e).__name__
    return r.returncode, r.stdout + r.stderr


def benutzer_vorhanden(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def verbotsprobe_socket(sockpfad: str, benutzer: list[str], text: str) -> None:
    """Aufruf des Sockets als fremder Benutzer: muss scheitern (Dateirechte) oder abgewiesen werden (SO_PEERCRED)."""
    for b in benutzer:
        if not benutzer_vorhanden(b):
            uebersprungen(f"Socket als {b}", "Benutzer existiert nicht")
            continue
        r = als_benutzer(b, [CLIENT, sockpfad, zeile(text)])
        try:
            a = json.loads(r.stdout)
        except ValueError:
            pruefe(f"Socket als {b} abgewiesen", False, (r.stdout + r.stderr)[:200])
            continue
        ok = (not a["verbunden"]) or (a["antwort"].get("ok") is False and a["antwort"].get("grund") == "benutzer")
        schicht = f"Dateirechte: {a['fehler']}" if not a["verbunden"] else "SO_PEERCRED"
        pruefe(f"Socket als {b} abgewiesen ({schicht})", ok, str(a)[:200])


def teil_a(A, benutzer: list[str], root: bool) -> None:
    print("== A. Wegwerf-Instanz")
    tmp = tempfile.mkdtemp(prefix="anker-rauchtest-")
    os.chmod(tmp, 0o755)
    try:
        p = A.Pfade.unter(tmp)
        for ordner in (p.lib, p.konfig, p.zustand, p.log, p.lauf, p.schritte):
            os.makedirs(ordner, exist_ok=True)
            os.chmod(ordner, 0o755)
        os.chmod(p.zustand, 0o700)
        for komp in ("ui", "kern"):  # Attrappen der Schritt-Skripte späterer Stufen
            pfad = os.path.join(p.schritte, f"rueckweg-{komp}")
            with open(pfad, "w", newline="\n") as f:
                f.write("#!/bin/sh\necho \"rueckweg $1 $2 $3\"\nsleep 0.2\nexit 0\n")
            os.chmod(pfad, 0o755)
        zeilen = [f"{hashlib.sha256(lies(os.path.join(p.schritte, n))).hexdigest()}  ./schritte/{n}"
                  for n in sorted(os.listdir(p.schritte))]
        with open(p.manifest, "w") as f:
            f.write("\n".join(zeilen) + "\n")
        os.chmod(p.manifest, 0o644)
        gut = {"version": "v0.1.0", "commit": "a" * 40, "ziel": "blau", "seit": "2026-10-22T08:00:00Z"}
        neu = {"version": "v0.1.1", "commit": "b" * 40, "ziel": "gruen", "seit": "2026-10-22T09:00:00Z"}
        gut_k = {**gut, "commit": "c" * 40, "ziel": "a"}
        neu_k = {**neu, "commit": "d" * 40, "ziel": "b"}
        stand = A.leerer_stand()
        stand["komponenten"]["ui"] = {"aktiv": neu, "letzte_gute": gut}
        stand["komponenten"]["kern"] = {"aktiv": neu_k, "letzte_gute": gut_k}
        A.stand_schreiben(p.stand, stand)

        eigene_uid = os.getuid()
        anker = A.Anker(p, aufloesen=lambda n: eigene_uid if n == ("root" if root else "wamocon") else None)
        server = A.Server(anker, modus=0o666)  # offen, damit andere Benutzer nur noch an SO_PEERCRED scheitern
        server.binden()
        faden = threading.Thread(target=server.lauf, daemon=True)
        faden.start()
        sock = p.socket
        try:
            a = frage(sock, "status")
            pruefe("status", a["ok"] and a["sperre"] == "aus" and a["zustand"] == "ok", str(a)[:200])
            # Rückweg mit Zeitmessung (Ziel: UI unter 30 s, Kern unter 60 s; hier der Anteil des Ankers mit Attrappe)
            t0 = time.monotonic()
            a = frage(sock, "rueckweg ui")
            dauer_ui = time.monotonic() - t0
            pruefe(f"rueckweg ui ({dauer_ui:.1f} s, Ziel unter 30 s)", a["ok"] and dauer_ui < 30, str(a)[:200])
            t0 = time.monotonic()
            a = frage(sock, "rueckweg kern")
            dauer_k = time.monotonic() - t0
            pruefe(f"rueckweg kern ({dauer_k:.1f} s, Ziel unter 60 s)", a["ok"] and dauer_k < 60, str(a)[:200])
            a = frage(sock, "rueckweg alles")
            pruefe("rueckweg alles danach: Buchführung unverändert",
                   a["ok"] and all(t.get("geaendert") is False for t in a["teile"]), str(a)[:200])
            st = A.stand_laden(p.stand, p.vertrauens_uid)["komponenten"]
            pruefe("Zustandsdatei zeigt die guten Stände",
                   st["ui"]["aktiv"]["commit"] == "a" * 40 and st["kern"]["aktiv"]["commit"] == "c" * 40)

            # --trocken ändert nichts
            A.stand_schreiben(p.stand, {**stand})
            vorher = lies(p.stand)
            a1, a2 = frage(sock, "rueckweg alles --trocken"), frage(sock, "sperre an --trocken")
            pruefe("--trocken ändert Zustand und Sperre nicht",
                   a1["ok"] and a2["ok"] and lies(p.stand) == vorher and not os.path.exists(p.sperre))

            # Sperre
            pruefe("sperre an", frage(sock, "sperre an")["ok"] and os.path.exists(p.sperre))
            # Vorwärtsbefehle verlangen eine Anfrage-ID (seit 1.4e); die Sperre greift vor jedem Datenbankzugriff
            antworten = [frage(sock, f"{b} 00000000-0000-4000-8000-000000000000") for b in ("migrieren", "bereitstellen", "umschalten")]
            pruefe("sperre an blockiert Vorwärtsbefehle", all(a.get("grund") == "gesperrt" for a in antworten), str(antworten)[:200])
            pruefe("rueckweg bleibt unter der Sperre möglich", frage(sock, "rueckweg ui")["ok"])
            pruefe("sperre aus", frage(sock, "sperre aus")["ok"] and not os.path.exists(p.sperre))

            # pruefe-sonder lehnt immer ab
            a = frage(sock, "pruefe-sonder 42")
            pruefe("pruefe-sonder antwortet NEIN", a.get("ergebnis") == "NEIN" and a["ok"] is False, str(a)[:200])

            # Verbotsproben am Eingang
            bose = ["reboot", "rueckweg ../ui", "rueckweg ui; id", "status\x00", "st\u202eatus", "status\x1b[31m", "x" * 400,
                    "rueckweg /etc/passwd", "sperre aus --trocken --trocken", "pruefe-sonder ../../etc"]
            fehlgeschlagen = []
            for text in bose:
                try:
                    r = frage(sock, zeile(text), roh=True)
                except (BrokenPipeError, ConnectionResetError):
                    r = {"ok": False}  # der Dienst schließt, bevor die ganze überlange Anfrage gesendet ist: abgewiesen
                except (OSError, ValueError) as e:
                    r = {"ok": None, "fehler": type(e).__name__}
                if r.get("ok") is not False:
                    fehlgeschlagen.append(repr(text)[:40])
            pruefe(f"{len(bose)} Verbotsproben am Eingang abgelehnt", not fehlgeschlagen, ", ".join(fehlgeschlagen))
            kennung = zeile("status")
            frage(sock, kennung, roh=True)
            pruefe("doppelte Anfrage abgelehnt", frage(sock, kennung, roh=True).get("grund") == "doppelt")

            # ändernde Anfragen im Dauerfeuer werden gedrosselt (das Protokoll bleibt für den Rückweg frei)
            antworten = [frage(sock, "sperre an" if i % 2 == 0 else "sperre aus").get("grund") for i in range(A.AENDERN_MAX + 5)]
            pruefe("ändernde Anfragen im Dauerfeuer werden gedrosselt", "zu_viele" in antworten)

            # Verbotsproben als andere Benutzer
            if root:
                verbotsprobe_socket(sock, benutzer, "sperre aus")
            else:
                uebersprungen("Verbotsproben als andere Benutzer", "nur als root möglich (runuser)")

            # Hashkette
            ok, n, _, grund = A.kette_pruefen(p.audit, p.vertrauens_uid)
            pruefe(f"Hashkette gültig ({n} Zeilen)", ok and n > 20, str(grund))
            kopie = os.path.join(tmp, "kette-kopie.jsonl")
            roh = lies(p.audit).split(b"\n")
            roh[5] = roh[5].replace(b'"t":"20', b'"t":"21')  # Zeitstempel einer mittleren Zeile ändern
            with open(kopie, "wb") as f:
                f.write(b"\n".join(roh))
            os.chmod(kopie, 0o600)
            pruefe("Manipulation der Kette wird erkannt", not A.kette_pruefen(kopie, p.vertrauens_uid)[0])
            pruefe("Anker meldet die Kette als gültig im status", frage(sock, "status")["audit"]["kette"] == "ok")
        finally:
            server.anhalten()
            faden.join(5)
            server.schliessen()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def teil_b(A, benutzer: list[str], root: bool) -> None:
    print("== B. Echte Installation (nur lesend und anfragend)")
    P = A.PFADE
    pruefe("Prüfsumme der installierten Dateien", A.installation_pruefen(P) == [], "; ".join(A.installation_pruefen(P)))
    for pfad, ordner, erwartet in ((P.lib, True, 0o755), (P.konfig, True, 0o755), (P.zustand, True, 0o700), (P.log, True, 0o750),
                                   (P.stand, False, 0o600), (P.audit, False, 0o600), (P.manifest, False, 0o644)):
        try:
            st = os.lstat(pfad)
            art_ok = stat.S_ISDIR(st.st_mode) if ordner else stat.S_ISREG(st.st_mode)
            ok = st.st_uid == 0 and stat.S_IMODE(st.st_mode) == erwartet and art_ok
            pruefe(f"{pfad} root {oct(erwartet)}", ok, f"uid {st.st_uid}, Modus {oct(stat.S_IMODE(st.st_mode))}")
        except OSError as e:
            pruefe(f"{pfad} vorhanden", False, str(e))
    rc, aus = kommando(["lsattr", "-d", P.audit])
    pruefe("Audit-Protokoll nur anhängbar (chattr +a)", rc == 0 and "a" in (aus.split() or [""])[0], aus)
    rc, aus = kommando(["systemctl", "is-active", "wamo-anker.service"])
    pruefe("Dienst wamo-anker aktiv", aus.strip() == "active", aus)
    try:
        st = os.lstat(P.socket)
        pruefe("Socket 0660 root:wamocon", stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o660 and st.st_uid == 0
               and st.st_gid == pwd.getpwnam("wamocon").pw_gid, f"{oct(stat.S_IMODE(st.st_mode))} {st.st_uid}:{st.st_gid}")
    except (OSError, KeyError) as e:
        pruefe("Socket vorhanden", False, str(e))
    if not root:
        uebersprungen("Schreibproben und Socket als andere Benutzer", "nur als root möglich (runuser)")
        return
    for b in benutzer:
        if not benutzer_vorhanden(b):
            uebersprungen(f"Schreibproben als {b}", "Benutzer existiert nicht")
            continue
        schlecht = []
        for ordner in (P.lib, P.schritte, P.konfig, P.zustand, P.log, P.lauf):
            r = als_benutzer(b, [SCHREIBER, os.path.join(ordner, ".rauchtest-probe")])
            if "GESCHRIEBEN" in r.stdout:
                schlecht.append(ordner)
        pruefe(f"{b} kann in keinem Ankerordner schreiben", not schlecht, ", ".join(schlecht))
    verbotsprobe_socket(P.socket, benutzer, "status")
    a = frage(P.socket, "status")
    pruefe("status am echten Socket als root", a["ok"] and a["audit"]["kette"] == "ok", json.dumps(a)[:300])
    print(f"  Hinweis: Sperre ist {a['sperre']}, Zustandsdatei {a['zustand']}, Installation {a['installation']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rauchtest des WAMO-Notankers")
    ap.add_argument("--produktiv", action="store_true", help="auch die echte Installation prüfen")
    ap.add_argument("--benutzer", default="wamo,wamo-core,nobody", help="Benutzer für die Verbotsproben (Komma getrennt)")
    ap.add_argument("--installiert", action="store_true", help=f"Code aus {PRODUKTIV_LIB} statt aus dem Ordner dieses Skripts")
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        print("Der Rauchtest läuft nur unter Linux (Sokrates-2).", file=sys.stderr)
        return 2
    benutzer = [b for b in args.benutzer.split(",") if b]
    root = os.geteuid() == 0
    A, pfad = lade_anker(args.installiert or args.produktiv)
    print(f"Notanker-Rauchtest, Code aus {pfad}, {'root' if root else 'ohne root'}")
    teil_a(A, benutzer, root)
    if args.produktiv:
        teil_b(A, benutzer, root)
    print(f"Ergebnis: {ERGEBNIS['ok']} bestanden, {ERGEBNIS['fehler']} fehlgeschlagen, {ERGEBNIS['uebersprungen']} übersprungen")
    return 0 if ERGEBNIS["fehler"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
