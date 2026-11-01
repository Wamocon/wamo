#!/usr/bin/python3 -E -s
"""Rauchtest des WAMO-Release-Dienstes auf Sokrates-2 (Schritt 0.3 der Selbstüberarbeitung). Nur Standardbibliothek.

Aufruf (als root, damit die Verbotsproben als andere Benutzer laufen können):

    sudo python3 -E -s /opt/wamo-release/app/release_rauchtest.py                # Wegwerf-Instanz, nichts Produktives
    sudo python3 -E -s /opt/wamo-release/app/release_rauchtest.py --produktiv    # zusätzlich die echte Installation
    sudo python3 -E -s release_rauchtest.py --benutzer wamo,wamo-core,nobody      # andere Benutzer für die Verbotsproben

Teil A (immer): Eine Wegwerf-Instanz des Dienstes läuft in einem eigenen Ordner mit eigenem Socket, mit Attrappen für den
Anker, die Datenbank und die Stufen. Geprüft werden der ganze Weg einer Anfrage bis "bewaehrt", die Verbotsproben am Socket
(auch als andere Benutzer über runuser), Sperre des Ankers, Rückweg, Wiederaufnahme nach einem Neustart und die Hashkette
samt Manipulation. Es wird nichts am echten System geändert und kein Anker gefragt.
Teil B (--produktiv): Die echte Installation wird nur gelesen und angesprochen: Rechte der Ordner und Dateien, Prüfsumme,
Dienst aktiv ohne Capabilities, Schreibproben als wamo, wamo-core und nobody (müssen scheitern), Socket-Rechte, Aufruf des
echten Sockets als diese Benutzer (müssen abgewiesen werden), `status` als root (Anker erreichbar?) und eine Anfrage, die in
diesem Schritt abgelehnt werden muss (Sperre oder kein Admin), dazu die Netzproben als wamo-release (nur die Datenbank auf
192.168.178.136:55322 ist erreichbar, SSH und Internet nicht). Der echte Socket schreibt dabei Zeilen ins Audit-Protokoll.
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
import threading
import time

try:  # nur Linux; die Plattformprüfung in main() meldet es freundlich
    import grp
    import pwd
except ImportError:
    grp = None
    pwd = None

sys.dont_write_bytecode = True  # beim Import aus der Installation darf kein __pycache__ neben die Prüfsumme geraten
HIER = os.path.dirname(os.path.realpath(__file__))
PRODUKTIV_APP = "/opt/wamo-release/app"
ID = [time.time_ns() >> 4 & 0x0FFFFFFFFFFFFFFF]   # je Lauf neu: der laufende Dienst merkt sich Kennungen und lehnt Wiederholungen ab
ERGEBNIS = {"ok": 0, "fehler": 0, "uebersprungen": 0}
ADMIN = "123e4567-e89b-12d3-a456-426614174000"
NICHT_ADMIN = "223e4567-e89b-12d3-a456-426614174000"

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


def lade_dienst(installiert: bool):
    """Importiert die Module des Dienstes aus der Installation oder aus dem Quellbaum (release/ neben diesem Skript)."""
    kandidaten = [PRODUKTIV_APP] if installiert else [os.path.join(HIER, "release"), HIER]
    for ordner in kandidaten:
        if os.path.isfile(os.path.join(ordner, "wamo_release.py")):
            if installiert and (os.stat(ordner).st_uid != 0 or os.stat(ordner).st_mode & 0o022):
                raise SystemExit(f"{ordner} gehört nicht root oder ist beschreibbar: Der Rauchtest lädt dort keinen Code")
            sys.path.insert(0, ordner)
            import release_anker as ra
            import release_automat as au
            import release_db as db
            import release_kette as k
            import release_stufen as st
            import wamo_release as r
            return {"R": r, "RA": ra, "AU": au, "DB": db, "K": k, "ST": st}, ordner
    raise SystemExit("wamo_release.py nicht gefunden (neben diesem Skript, in release/ oder in " + PRODUKTIV_APP + ")")


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


NETZPROBE = "\n".join([
    "import socket, sys",
    "s = socket.socket(); s.settimeout(3)",
    "try:",
    "    s.connect((sys.argv[1], int(sys.argv[2]))); print('VERBUNDEN')",
    "except OSError as e:",
    "    print('ABGELEHNT', type(e).__name__)",
])
DB_ADRESSE = ("192.168.178.136", 55322)


def netzproben(vorhanden: bool, db_soll_da: bool) -> None:
    """Der Benutzer wamo-release erreicht nur die Datenbank (nft-Tabelle wamo_release_ausgang und IPAddressAllow)."""
    if not vorhanden:
        return uebersprungen("Netzproben", "Benutzer wamo-release existiert nicht")
    for ziel, port, name in (("192.168.178.136", 22, "SSH auf Sokrates-2"), ("192.168.178.1", 80, "Router im LAN"),
                             ("1.1.1.1", 443, "Internet")):
        r = als_benutzer("wamo-release", [NETZPROBE, ziel, str(port)])
        pruefe(f"wamo-release erreicht {name} nicht", "ABGELEHNT" in r.stdout, (r.stdout + r.stderr)[:200])
    r = als_benutzer("wamo-release", [NETZPROBE, DB_ADRESSE[0], str(DB_ADRESSE[1])])
    if db_soll_da:
        pruefe("wamo-release erreicht die Datenbank (Supavisor)", "VERBUNDEN" in r.stdout, (r.stdout + r.stderr)[:200])
    rc, aus = kommando(["nft", "list", "table", "inet", "wamo_release_ausgang"])
    pruefe("nft-Tabelle wamo_release_ausgang geladen", rc == 0 and "drop" in aus, aus[:200])


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
        # wamo-core darf verbinden (status, anfordern), aber nie zurueck oder abbrechen: dann zählt "recht" wie "benutzer"
        ok = (not a["verbunden"]) or (a["antwort"].get("ok") is False and a["antwort"].get("grund") in ("benutzer", "recht"))
        schicht = f"Dateirechte: {a['fehler']}" if not a["verbunden"] else "SO_PEERCRED"
        pruefe(f"Socket als {b} abgewiesen ({schicht})", ok, str(a)[:200])


def attrappen(M):
    """Anker, Datenbank und Stufen für die Wegwerf-Instanz."""
    RA = M["RA"]

    class FakeAnker:
        def __init__(self):
            self.sperre, self.aufrufe = "aus", []

        def sperre_zustand(self):
            return self.sperre

        def rueckweg(self, komponente, trocken=False):
            self.aufrufe.append(("rueckweg", komponente, trocken))
            return RA.Antwort(True, None, {"ok": True, "teile": [{"komponente": komponente, "ok": True}]})

        def sperre_an(self, trocken=False):
            self.aufrufe.append(("sperre_an", trocken))
            self.sperre = "an" if not trocken else self.sperre
            return RA.Antwort(True, None, {"ok": True})

    return FakeAnker(), M["DB"].AttrappeDatenbank({ADMIN}), M["ST"].Attrappe()


def anker_spielt(db, dienst) -> None:
    """Tut, was Freigabe-Dienst und Anker in der Datenbank täten, sobald der Dienst so weit ist."""
    a = dienst._z["anfrage"]
    if a is None:
        return
    s = db.anfragen[a["id"]]["status"]
    if a["status"] == "wartet_freigabe" and s == "wartet_freigabe":
        db.anker_setzt(a["id"], "freigegeben", v_commit=a["daten"].get("v_commit"), tree_id=a["daten"].get("tree_id"))
    elif a["status"] == "veroeffentlicht" and a["fertig"] and s == "veroeffentlicht":
        db.anker_setzt(a["id"], "bereitgestellt")
    elif a["status"] == "bereitgestellt" and a["fertig"] and s == "bereitgestellt":
        db.anker_setzt(a["id"], "umgeschaltet")
    elif a["status"] == "umgeschaltet" and s == "umgeschaltet":
        db.anker_setzt(a["id"], "bewaehrt")


def lauf_ende(dienst, db, maximal: int = 60) -> None:
    for _ in range(maximal):
        if dienst._z["anfrage"] is None:
            return
        dienst.arbeite_einmal()
        anker_spielt(db, dienst)


def teil_a(M, benutzer: list[str], root: bool) -> None:
    R, K = M["R"], M["K"]
    print("== A. Wegwerf-Instanz")
    tmp = tempfile.mkdtemp(prefix="release-rauchtest-")
    os.chmod(tmp, 0o755)
    try:
        p = R.Pfade.unter(tmp)
        for ordner in (p.lib, p.zustand, p.log, p.lauf):
            os.makedirs(ordner, exist_ok=True)
            os.chmod(ordner, 0o755 if ordner == p.lauf else 0o700)  # offen bis zum Socket: dann zählt nur noch SO_PEERCRED
        anker, db, stufen = attrappen(M)
        eigene_uid = os.getuid()
        zeit = [time.time()]

        def neuer_dienst():
            d = R.Dienst(p, anker=anker, db=db, stufen=stufen, uhr=lambda: zeit[0],
                         aufloesen=lambda n: eigene_uid if n == "root" else None)
            d.tor_zeit = 0
            d.wiederaufnehmen()
            return d

        dienst = neuer_dienst()
        server = R.Server(dienst, modus=0o666)  # offen, damit andere Benutzer nur noch an SO_PEERCRED scheitern
        server.binden()
        faden = threading.Thread(target=server.lauf, daemon=True)
        faden.start()
        sock = p.socket
        try:
            a = frage(sock, "status")
            pruefe("status", a["ok"] and a["anker"]["sperre"] == "aus" and a["datenbank"] == "angeschlossen", str(a)[:200])

            # Der ganze Weg einer Anfrage (Quelle main) bis zur Bewährung
            a = frage(sock, f"anfordern main {ADMIN}")
            pruefe("anfordern main", a["ok"], str(a)[:200])
            a2 = frage(sock, f"anfordern main {ADMIN}")
            pruefe("zweite offene Anfrage abgelehnt", a2.get("grund") == "belegt", str(a2)[:200])
            t0 = time.monotonic()
            lauf_ende(dienst, db)
            dienst._spiegeln()
            ende = dienst._z["abgeschlossen"][-1] if dienst._z["abgeschlossen"] else {}
            pruefe(f"Weg bis bewaehrt ({time.monotonic() - t0:.2f} s)", ende.get("status") == "bewaehrt", str(ende))
            with open(p.audit, "rb") as f:
                schritte = [json.loads(z) for z in f.read().split(b"\n") if z and b'"ereignis":"uebergang"' in z]
            pruefe("zehn Übergänge im Protokoll", len(schritte) == 10, str(len(schritte)))
            pruefe("Datenbank bekam nichts Verbotenes", db.verstoesse == [], str(db.verstoesse)[:200])
            pruefe("Ereignisse in die Datenbank gespiegelt", len(db.ereignisse) >= 10, str(len(db.ereignisse)))

            # Sperre des Ankers hält neue Anfragen an, die Wegnahme gibt sie frei
            anker.sperre = "an"
            a = frage(sock, f"anfordern main {ADMIN}")
            pruefe("sperre an hält neue Anfragen an", a.get("grund") == "gesperrt", str(a)[:200])
            anker.sperre = "aus"
            a = frage(sock, f"anfordern main {NICHT_ADMIN}")
            pruefe("Nicht-Admin abgelehnt", a.get("grund") == "kein_admin", str(a)[:200])

            # abbrechen und zurueck
            a = frage(sock, f"anfordern main {ADMIN}")
            kennung = a["anfrage"]["id"] if a.get("ok") else ""
            a = frage(sock, f"abbrechen {kennung}")
            pruefe("abbrechen", a["ok"] and dienst._z["anfrage"] is None, str(a)[:200])
            a = frage(sock, f"anfordern main {ADMIN}")
            pruefe("dritte Anfrage des Tages", a["ok"], str(a)[:200])
            t0 = time.monotonic()
            a = frage(sock, "zurueck alles")
            dauer = time.monotonic() - t0
            pruefe(f"zurueck alles beendet die offene Anfrage ({dauer:.1f} s, Anteil des Dienstes)",
                   a["ok"] and dienst._z["anfrage"] is None and ("rueckweg", "alles", False) in anker.aufrufe, str(a)[:200])
            a = frage(sock, f"anfordern main {ADMIN}")
            pruefe("vierte Anfrage des Tages abgelehnt", a.get("grund") == "zu_viele_anfragen", str(a)[:200])
            pruefe("zurueck --trocken ändert nichts", frage(sock, "zurueck ui --trocken")["ok"])

            # Wiederaufnahme: ein neuer Dienst auf denselben Dateien kennt denselben Stand
            vorher = json.dumps(dienst._z["abgeschlossen"], sort_keys=True)
            d2 = neuer_dienst()
            pruefe("Neustart: Stand aus Zustandsdatei und Protokoll", json.dumps(d2._z["abgeschlossen"], sort_keys=True) == vorher)

            # Verbotsproben am Eingang
            bose = ["reboot", "zurueck ../ui", "zurueck ui; id", "status\x00", "st\u202eatus", "status\x1b[31m", "x" * 400,
                    "zurueck /etc/passwd", "sperre aus", "umschalten", "abbrechen ../../etc", "status --trocken",
                    f"freigeben {ADMIN}", "diff " + ADMIN, "anfordern main /etc/passwd"]
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
            pruefe("freigeben gibt es hier nie", frage(sock, f"freigeben {ADMIN}").get("grund") == "freigabe_nur_beim_anker")

            # ändernde Anfragen im Dauerfeuer werden gedrosselt
            antworten = [frage(sock, f"abbrechen {ADMIN}").get("grund") for _ in range(R.AENDERN_MAX + 5)]
            pruefe("ändernde Anfragen im Dauerfeuer werden gedrosselt", "zu_viele" in antworten)

            # Verbotsproben als andere Benutzer
            if root:
                verbotsprobe_socket(sock, benutzer, "zurueck alles")
            else:
                uebersprungen("Verbotsproben als andere Benutzer", "nur als root möglich (runuser)")

            # Hashkette
            ok, n, _, grund = K.kette_pruefen(p.audit, p.uid)
            pruefe(f"Hashkette gültig ({n} Zeilen)", ok and n > 40, str(grund))
            kopie = os.path.join(tmp, "kette-kopie.jsonl")
            roh = lies(p.audit).split(b"\n")
            roh[5] = roh[5].replace(b'"t":"20', b'"t":"21')  # Zeitstempel einer mittleren Zeile ändern
            with open(kopie, "wb") as f:
                f.write(b"\n".join(roh))
            os.chmod(kopie, 0o600)
            pruefe("Manipulation der Kette wird erkannt", not K.kette_pruefen(kopie, p.uid)[0])
            pruefe("Dienst meldet die Kette als gültig im status", frage(sock, "status")["audit"]["kette"] == "ok")
        finally:
            server.anhalten()
            faden.join(5)
            server.schliessen(5)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def teil_b(M, benutzer: list[str], root: bool) -> None:
    R = M["R"]
    print("== B. Echte Installation (nur lesend und anfragend)")
    P = R.PFADE
    probleme = R.installation_pruefen(P)
    pruefe("Prüfsumme der installierten Dateien", probleme == [], "; ".join(probleme))
    for pfad, ordner, erwartet, uid in ((P.lib, True, 0o755, 0), (P.manifest, False, 0o644, 0),
                                        (P.zustand, True, 0o700, None), (P.log, True, 0o750, None),
                                        (P.audit, False, 0o600, None)):
        try:
            st = os.lstat(pfad)
            art_ok = stat.S_ISDIR(st.st_mode) if ordner else stat.S_ISREG(st.st_mode)
            besitzer = pwd.getpwnam("wamo-release").pw_uid if uid is None else uid
            ok = st.st_uid == besitzer and stat.S_IMODE(st.st_mode) == erwartet and art_ok
            pruefe(f"{pfad} {oct(erwartet)}", ok, f"uid {st.st_uid}, Modus {oct(stat.S_IMODE(st.st_mode))}")
        except (OSError, KeyError) as e:
            pruefe(f"{pfad} vorhanden", False, str(e))
    rc, aus = kommando(["lsattr", "-d", P.audit])
    pruefe("Audit-Protokoll nur anhängbar (chattr +a)", rc == 0 and "a" in (aus.split() or [""])[0], aus)
    rc, aus = kommando(["systemctl", "is-active", "wamo-release.service"])
    pruefe("Dienst wamo-release aktiv", aus.strip() == "active", aus)
    rc, pid = kommando(["systemctl", "show", "-p", "MainPID", "--value", "wamo-release.service"])
    pid = pid.strip()
    if rc == 0 and pid.isdigit() and int(pid) > 1:
        try:
            with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as f:
                status = f.read()
            felder = dict(z.split(":\t", 1) for z in status.splitlines() if ":\t" in z)
            wr = pwd.getpwnam("wamo-release").pw_uid
            pruefe("Dienstprozess ohne Capabilities", felder.get("CapEff", "").strip() == "0000000000000000", felder.get("CapEff", ""))
            pruefe("Dienstprozess ohne neue Rechte", felder.get("NoNewPrivs", "").strip() == "1", felder.get("NoNewPrivs", ""))
            pruefe("Dienstprozess läuft als wamo-release", felder.get("Uid", "").split()[:1] == [str(wr)], felder.get("Uid", ""))
        except (OSError, KeyError) as e:
            pruefe("Dienstprozess lesbar", False, str(e))
    else:
        pruefe("Hauptprozess bekannt", False, pid)
    try:
        st = os.lstat(P.socket)
        pruefe("Socket 0660 wamo-release:wamo-core",
               stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o660 and st.st_uid == pwd.getpwnam("wamo-release").pw_uid
               and st.st_gid == grp.getgrnam("wamo-core").gr_gid, f"{oct(stat.S_IMODE(st.st_mode))} {st.st_uid}:{st.st_gid}")
    except (OSError, KeyError) as e:
        pruefe("Socket vorhanden", False, str(e))
    if not root:
        uebersprungen("Schreibproben, Socket als andere Benutzer und status", "nur als root möglich (runuser, Socket)")
        return
    for b in benutzer:
        if not benutzer_vorhanden(b):
            uebersprungen(f"Schreibproben als {b}", "Benutzer existiert nicht")
            continue
        schlecht = []
        for ordner in (P.lib, P.zustand, P.log, P.lauf, os.path.dirname(P.lib)):
            r = als_benutzer(b, [SCHREIBER, os.path.join(ordner, ".rauchtest-probe")])
            if "GESCHRIEBEN" in r.stdout:
                schlecht.append(ordner)
        pruefe(f"{b} kann in keinem Ordner des Release-Dienstes schreiben", not schlecht, ", ".join(schlecht))
    verbotsprobe_socket(P.socket, benutzer, "zurueck alles --trocken")  # nie status: der Kern darf status
    a = frage(P.socket, "status")
    pruefe("status am echten Socket als root", a["ok"] and a["audit"]["kette"] == "ok", json.dumps(a)[:300])
    pruefe("Anker erreichbar (Zusatzgruppe und Socket des Ankers)", a.get("anker", {}).get("erreichbar") is True,
           json.dumps(a.get("anker"))[:100])
    pruefe("Installation ohne Abweichung", a.get("installation") == "ok", json.dumps(a.get("installation_probleme"))[:200])
    a = frage(P.socket, f"anfordern main {ADMIN}")
    # Sperre nach der Einrichtung gesetzt; ohne Sperre lehnt die echte Datenbank die Test-UUID als Nicht-Admin ab
    erwartet = a.get("grund") in ("datenbank_fehlt", "gesperrt", "kein_admin")
    pruefe("anfordern wird in diesem Schritt abgelehnt", erwartet and not a.get("ok"), json.dumps(a)[:200])
    s = frage(P.socket, "status")
    print(f"  Hinweis: Datenbank {s.get('datenbank')}")
    netzproben(benutzer_vorhanden("wamo-release"), s.get("datenbank") == "angeschlossen")
    pruefe("freigeben wird nie angenommen", frage(P.socket, f"freigeben {ADMIN}").get("grund") == "freigabe_nur_beim_anker")
    print(f"  Hinweis: Die Kette hat {frage(P.socket, 'status')['audit']['zeilen']} Zeilen")
    rc, aus = kommando(["systemd-analyze", "security", "wamo-release.service", "--no-pager"])
    letzte = [z for z in aus.splitlines() if "Overall exposure level" in z]
    print(f"  Hinweis: {letzte[-1].strip() if letzte else 'systemd-analyze security nicht verfügbar'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rauchtest des WAMO-Release-Dienstes")
    ap.add_argument("--produktiv", action="store_true", help="auch die echte Installation prüfen")
    ap.add_argument("--benutzer", default="wamo,wamo-core,nobody", help="Benutzer für die Verbotsproben (Komma getrennt)")
    ap.add_argument("--installiert", action="store_true", help=f"Code aus {PRODUKTIV_APP} statt aus dem Quellbaum")
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        print("Der Rauchtest läuft nur unter Linux (Sokrates-2).", file=sys.stderr)
        return 2
    benutzer = [b for b in args.benutzer.split(",") if b]
    root = os.geteuid() == 0
    M, ordner = lade_dienst(args.installiert or args.produktiv)
    print(f"Release-Rauchtest, Code aus {ordner}, {'root' if root else 'ohne root'}")
    teil_a(M, benutzer, root)
    if args.produktiv:
        teil_b(M, benutzer, root)
    print(f"Ergebnis: {ERGEBNIS['ok']} bestanden, {ERGEBNIS['fehler']} fehlgeschlagen, {ERGEBNIS['uebersprungen']} übersprungen")
    return 0 if ERGEBNIS["fehler"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
