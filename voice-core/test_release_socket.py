"""Socket-Tests des Release-Dienstes (infra/sokrates2/release/wamo_release.py, Schritt 0.3). Nur Linux: Unix-Sockets,
SO_PEERCRED und flock gibt es unter Windows nicht; dort werden alle Tests übersprungen. Der Lauf in einem Wegwerf-Container
(wamo-test-*) ist in der Arbeitsnotiz zu Schritt 0.3 beschrieben.

Dazu gehört ein Zusammenspiel mit dem echten Notanker: Der Anker läuft in einem Wegwerf-Ordner im selben Prozess, der
Release-Dienst spricht ihn über dessen Socket an (AnkerClient). So ist belegt, dass die Zeilen des Clients zu dem passen,
was der Anker liest, und dass der Anker `sperre aus` für wamo-release verweigert.

Ausführen:  uv run python -m pytest -q test_release_socket.py   (im Ordner voice-core/)
"""

import contextlib
import errno
import importlib.util
import io
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import test_release as T

R, RA, AU, K, DB = T.R, T.RA, T.AU, T.K, T.DB
LINUX = sys.platform.startswith("linux")
ADMIN = T.ADMIN
ROOT = LINUX and os.geteuid() == 0
NICHT_ROOT = LINUX and os.geteuid() != 0


def _anker_laden():
    spec = importlib.util.spec_from_file_location("wamo_anker", T.INFRA / "anker" / "wamo_anker.py")
    modul = importlib.util.module_from_spec(spec)
    sys.modules["wamo_anker"] = modul
    spec.loader.exec_module(modul)
    return modul


@unittest.skipUnless(LINUX, "braucht Linux (Unix-Sockets, SO_PEERCRED)")
class SocketTests(T.ReleaseTest):
    def starte(self, uid_root=None, uid_kern=None, modus=0o660, **kw):
        eigene = os.getuid()
        tabelle = {"root": eigene if uid_root is None else uid_root, "wamo-core": eigene + 7 if uid_kern is None else uid_kern}
        d = self.neu(aufloesen=tabelle.get, **kw)
        server = R.Server(d, modus)
        server.binden()
        faden = threading.Thread(target=server.lauf, daemon=True)
        faden.start()
        self.server, self.faden, self.dienst = server, faden, d

        def ende():
            server.anhalten()
            faden.join(5)
            server.schliessen(5)

        self.addCleanup(ende)
        return server

    def verbinde(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(self.p.socket)
        return s

    def frage(self, text="status", senden=True, id=None):
        s = self.verbinde()
        if senden:
            s.sendall(self.zeile(text, id))
            s.shutdown(socket.SHUT_WR)
        daten = b""
        while True:
            teil = s.recv(4096)
            if not teil:
                break
            daten += teil
        s.close()
        return json.loads(daten)

    def test_so_peercred_liefert_die_echte_uid(self):
        self.starte()
        s = self.verbinde()
        peer = R.peer_von(s)
        s.close()
        self.assertEqual((peer.uid, peer.gid, peer.pid), (os.getuid(), os.getgid(), os.getpid()))

    def test_erlaubter_benutzer(self):
        self.starte()
        antwort = self.frage("status")
        self.assertTrue(antwort["ok"])
        self.assertEqual(antwort["version"], R.VERSION)
        self.assertTrue(self.frage("zurueck alles --trocken")["ok"])

    def test_der_kern_darf_nur_status_und_anfordern(self):
        self.starte(uid_root=os.getuid() + 1, uid_kern=os.getuid())
        self.assertTrue(self.frage("status")["ok"])
        self.assertEqual(self.frage("zurueck alles")["grund"], "recht")
        self.assertEqual(self.frage(f"abbrechen {ADMIN}")["grund"], "recht")
        self.assertTrue(self.frage(f"anfordern main {ADMIN}")["ok"])
        self.assertEqual(self.frage(f"freigeben {ADMIN}")["grund"], "freigabe_nur_beim_anker")
        self.assertEqual(self.anker.namen().count("rueckweg"), 0)

    def test_fremder_benutzer_bekommt_auch_ohne_zu_senden_eine_abweisung(self):
        self.starte(uid_root=os.getuid() + 1)
        antwort = self.frage(senden=False)  # nichts senden: die Abweisung kommt trotzdem
        self.assertEqual(antwort["grund"], "benutzer")
        z = self.audit()[-1]
        self.assertEqual((z["ereignis"], z["grund"], z["uid"]), ("abgelehnt", "benutzer", os.getuid()))
        self.assertEqual(self.frage("zurueck alles")["grund"], "benutzer")  # und gesendetes wird nicht gelesen
        self.assertEqual(self.anker.aufrufe, [])

    def als_nobody_fragen(self, text="zurueck alles"):
        """Eigener Prozess mit uid 65534: verbindet und sendet eine ändernde Anfrage. Ergebnis wie vom Prozess gemeldet."""
        code = "\n".join([
            "import json, socket, sys",
            "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)",
            "s.settimeout(5)",
            "try:",
            "    s.connect(sys.argv[1])",
            "    s.sendall(sys.argv[2].encode())",
            "    r = json.loads(s.recv(4096))",
            "except OSError as e:",
            "    r = {'fehler': type(e).__name__}",
            "print(json.dumps(r))"])
        r = subprocess.run([sys.executable, "-I", "-c", code, self.p.socket, self.zeile(text).decode()],
                           capture_output=True, text=True, timeout=30, user=65534, group=65534, extra_groups=[])
        if not r.stdout:
            self.skipTest("der Python-Interpreter ist für nobody nicht ausführbar: " + r.stderr[:100])
        return json.loads(r.stdout)

    @unittest.skipUnless(ROOT, "braucht root zum Wechsel der uid")
    def test_fremde_uid_scheitert_an_den_dateirechten_und_an_so_peercred(self):
        os.chmod(self.tmp.name, 0o755)  # nobody darf bis zum Socket gehen
        os.chmod(self.p.lauf, 0o755)
        self.starte(uid_root=0)
        self.assertEqual(self.als_nobody_fragen(), {"fehler": "PermissionError"})  # erste Schicht: Socket 0660
        os.chmod(self.p.socket, 0o666)  # erste Schicht aufgehoben: jetzt muss SO_PEERCRED allein abweisen
        antwort = self.als_nobody_fragen()
        self.assertEqual((antwort.get("ok"), antwort.get("grund")), (False, "benutzer"), antwort)
        self.assertEqual(self.audit()[-1]["uid"], 65534)
        self.assertEqual(self.anker.aufrufe, [])

    def test_ueberlange_und_langsame_anfragen(self):
        self.starte()
        s = self.verbinde()
        s.sendall(b"x" * 300)
        self.assertEqual(json.loads(s.recv(4096))["grund"], "zeile_zu_lang")
        s.close()
        with mock.patch.object(R, "LESE_ZEIT_S", 0.2), mock.patch.object(R, "GESAMT_ZEIT_S", 0.4):
            s = self.verbinde()
            s.sendall(b"0123")
            self.assertEqual(json.loads(s.recv(4096))["grund"], "zeitueberschreitung")
            s.close()
        self.assertTrue(self.frage("status")["ok"])  # der Dienst lebt weiter

    def test_dienst_ueberlebt_muell(self):
        self.starte()
        for muell in (b"\x00" * 50, b"\xff" * 20 + b"\n", os.urandom(100), b"", b"{}\n", b"GET / HTTP/1.1\r\n\r\n",
                      b"0123456789abcdef status\x00\n", b"0123456789abcdef zurueck ../ui\n",
                      "0123456789abcdef status‮\n".encode()):
            s = self.verbinde()
            if muell:
                s.sendall(muell)
            s.shutdown(socket.SHUT_WR)
            antwort = json.loads(s.recv(4096))
            s.close()
            self.assertFalse(antwort["ok"], muell)
        self.assertTrue(self.frage("status")["ok"])
        self.assertEqual(self.anker.namen().count("rueckweg"), 0)

    def test_socket_rechte_und_altlast(self):
        self.starte()
        self.assertEqual(stat.S_IMODE(os.stat(self.p.socket).st_mode), 0o660)
        self.assertTrue(stat.S_ISSOCK(os.stat(self.p.socket).st_mode))

    def test_alter_socket_wird_ersetzt_fremde_datei_nicht(self):
        alt = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        alt.bind(self.p.socket)
        alt.close()
        self.starte()
        self.assertTrue(self.frage("status")["ok"])
        server2 = R.Server(self.neu())
        os.unlink(self.p.socket)
        with open(self.p.socket, "wb") as f:
            f.write(b"keine Socket-Datei")
        with self.assertRaises(SystemExit):
            server2.binden()
        self.assertEqual(Path(self.p.socket).read_bytes(), b"keine Socket-Datei")

    @unittest.skipUnless(NICHT_ROOT, "Rechte nur als normaler Benutzer prüfbar")
    def test_binden_verweigert_offenen_ordner(self):
        os.chmod(self.p.lauf, 0o777)
        with self.assertRaises(SystemExit):
            R.Server(self.neu()).binden()
        self.assertFalse(os.path.exists(self.p.socket))

    def test_start_und_stop_stehen_im_protokoll(self):
        server = self.starte()
        self.frage("status")
        server.anhalten()
        self.faden.join(5)
        self.assertFalse(self.faden.is_alive())
        ereignisse = [z["ereignis"] for z in self.audit()]
        self.assertEqual(ereignisse[0], "dienst_start")
        self.assertEqual(ereignisse[-1], "dienst_stop")
        self.assertEqual(self.audit()[-1]["grund"], "angefordert")
        self.assertTrue(K.kette_pruefen(self.p.audit)[0])

    def test_voruebergehender_accept_fehler_beendet_den_dienst_nicht(self):
        server = R.Server(self.neu())
        folge = [OSError(errno.EMFILE, "zu viele offene Dateien"), OSError(errno.ECONNABORTED, "abgebrochen"),
                 socket.timeout(), OSError(errno.EBADF, "Socket geschlossen")]

        class Attrappe:
            def accept(self):
                raise folge.pop(0)

        server._srv = Attrappe()
        with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(R.time, "sleep"):
            angefordert = server.lauf()
        self.assertEqual(folge, [])
        self.assertFalse(angefordert)  # ohne Anforderung beendet: Exit 1, systemd startet neu
        self.assertEqual(self.audit()[-1]["grund"], "socket_weg")

    def test_haengender_arbeiter_beendet_den_dienst_mit_fehler(self):
        self.starte()
        self.dienst.haenger = True
        self.faden.join(5)
        self.assertFalse(self.faden.is_alive())
        self.assertEqual(self.audit()[-1]["grund"], "haenger")

    def test_ein_langer_rueckweg_haelt_die_anzeige_nicht_auf(self):
        self.starte()
        los, fertig = threading.Event(), threading.Event()
        erster = []

        def blockiere():  # nur der erste Rückweg hängt
            if not erster:
                erster.append(1)
                los.set()
                fertig.wait(10)

        self.anker.waehrend_rueckweg = blockiere
        ergebnis = []
        t = threading.Thread(target=lambda: ergebnis.append(self.frage("zurueck alles")))
        t.start()
        self.assertTrue(los.wait(5))
        antwort = self.frage("status")  # während der Rückweg läuft
        self.assertTrue(antwort["ok"])
        self.assertEqual(self.frage("zurueck ui --trocken")["ok"], True)
        fertig.set()
        t.join(5)
        self.assertTrue(ergebnis[0]["ok"])

    @staticmethod
    def warte_bis(bedingung, sekunden=5.0):
        ende = time.monotonic() + sekunden
        while time.monotonic() < ende:
            if bedingung():
                return True
            time.sleep(0.02)
        return bedingung()

    def test_gleichzeitige_verbindungen_sind_begrenzt_und_fremde_zaehlen_nicht(self):
        server = self.starte()
        with mock.patch.object(R, "GESAMT_ZEIT_S", 3.0), mock.patch.object(R, "LESE_ZEIT_S", 3.0):
            stumm = [self.verbinde() for _ in range(R.VERBINDUNGEN_MAX)]  # erlaubt, aber still: belegen je einen Platz
            self.assertTrue(self.warte_bis(lambda: len(server._laufend) == R.VERBINDUNGEN_MAX))
            antwort = self.frage("status")
            self.assertEqual(antwort["grund"], "ausgelastet")
            for s in stumm:
                s.close()
            self.assertTrue(self.warte_bis(lambda: len(server._laufend) == 0))
        self.assertTrue(self.frage("status")["ok"])

    def test_der_kern_kann_root_nicht_aushungern(self):
        eigene = os.getuid()
        rollen = ["kern"] * (R.VERBINDUNGEN_KERN_MAX + 1) + ["root"]
        d = self.neu(aufloesen={"root": eigene, "wamo-core": eigene + 7}.get)
        server = R.Server(d, 0o660, peer=lambda conn: R.Peer(1, eigene + 7 if rollen.pop(0) == "kern" else eigene, 0))
        server.binden()
        faden = threading.Thread(target=server.lauf, daemon=True)
        faden.start()

        def ende():
            server.anhalten()
            faden.join(5)
            server.schliessen(5)

        self.addCleanup(ende)
        with mock.patch.object(R, "GESAMT_ZEIT_S", 3.0), mock.patch.object(R, "LESE_ZEIT_S", 3.0):
            stumm = [self.verbinde() for _ in range(R.VERBINDUNGEN_KERN_MAX)]  # der Kern belegt seinen ganzen Topf
            self.assertTrue(self.warte_bis(lambda: len(server._laufend) == R.VERBINDUNGEN_KERN_MAX))
            fuenfte = self.verbinde()
            self.assertEqual(json.loads(fuenfte.recv(4096))["grund"], "ausgelastet")  # der Kern bekommt keinen weiteren Platz
            fuenfte.close()
            self.assertTrue(self.frage("zurueck alles --trocken")["ok"])  # root kommt trotzdem durch
            for s in stumm:
                s.close()

    def test_dienst_main_startet_arbeitet_und_endet_auf_sigterm(self):
        import signal
        eigene = os.getuid()
        d = self.neu(wiederaufnehmen=False, aufloesen={"root": eigene}.get)
        antworten = []

        def beenden():
            self.warte_bis(lambda: os.path.exists(self.p.socket), 10)
            antworten.append(self.frage("status"))
            antworten.append(self.frage(f"anfordern main {ADMIN}"))
            self.warte_bis(lambda: self.anfrage(d) is not None and self.anfrage(d)["status"] != "angefragt", 10)
            os.kill(os.getpid(), signal.SIGTERM)

        alt = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
        faden = threading.Thread(target=beenden, daemon=True)
        faden.start()
        try:
            with mock.patch.object(R.os, "geteuid", return_value=4711), mock.patch.object(R, "Dienst", lambda: d):
                rc = R.dienst_main()
        finally:
            signal.signal(signal.SIGTERM, alt[0])
            signal.signal(signal.SIGINT, alt[1])
        faden.join(5)
        self.assertEqual(rc, 0)
        self.assertTrue(antworten[0]["ok"])
        self.assertTrue(antworten[1]["ok"], antworten[1])
        self.assertNotEqual(self.anfrage(d)["status"], "angefragt")  # der Arbeiter im Hintergrund hat sie weitergebracht
        self.assertEqual(self.audit()[-1]["ereignis"], "dienst_stop")
        self.assertTrue(K.kette_pruefen(self.p.audit)[0])

    def test_dienst_main_verweigert_root(self):
        with mock.patch.object(R.os, "geteuid", return_value=0), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(R.dienst_main(), 1)

    def test_gleichzeitige_anfragen_halten_die_kette(self):
        self.starte()
        ergebnisse = []

        def los(i):
            ergebnisse.append(self.frage("status" if i % 2 else f"anfordern main {ADMIN}"))

        faeden = [threading.Thread(target=los, args=(i,)) for i in range(12)]
        for f in faeden:
            f.start()
        for f in faeden:
            f.join(10)
        self.assertEqual(len(ergebnisse), 12)
        self.assertEqual(sum(1 for e in ergebnisse if e.get("anfrage") and e["ok"] and e["text"] == "Anfrage angelegt"), 1)
        self.assertTrue(K.kette_pruefen(self.p.audit)[0])

    def test_client_erfolgspfad_und_exit_codes(self):
        self.starte()
        uid = os.getuid()
        for args, exit_code in ((["status"], 0), (["zurueck", "alles", "--trocken"], 0), (["--json", "status"], 0),
                                (["abbrechen", ADMIN], 1), (["anfordern", "selbst", ADMIN], 1)):
            with self.subTest(args=args):
                ausgabe = io.StringIO()
                with contextlib.redirect_stdout(ausgabe):
                    self.assertEqual(R.client(args, self.p.socket, uid), exit_code)
                self.assertTrue(ausgabe.getvalue().strip())
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(R.client(["status"], self.p.socket, uid + 1), 2)  # Gegenstelle gehört nicht wamo-release
            self.assertEqual(R.client(["status"], os.path.join(self.tmp.name, "nichts.sock"), uid), 2)

    def test_selbstpruefung_ohne_installation_meldet_abweichung(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(R.selbstpruefung(), 1)  # auf diesem Rechner liegt nichts unter /opt/wamo-release


@unittest.skipUnless(LINUX, "braucht Linux (Unix-Sockets, SO_PEERCRED)")
class MitEchtemAnkerTests(T.ReleaseTest):
    """Der Release-Dienst gegen den echten Anker (wamo_anker.py) in einem Wegwerf-Ordner."""

    def setUp(self):
        super().setUp()
        self.A = _anker_laden()
        uid = os.getuid()
        wurzel = os.path.join(self.tmp.name, "anker-wurzel")
        os.makedirs(wurzel)
        os.chmod(wurzel, 0o755)
        self.ap = self.A.Pfade.unter(wurzel)
        for ordner in (self.ap.lib, self.ap.konfig, self.ap.zustand, self.ap.log, self.ap.lauf, self.ap.schritte):
            os.makedirs(ordner, exist_ok=True)
            os.chmod(ordner, 0o755)
        os.chmod(self.ap.zustand, 0o700)
        self.A.stand_schreiben(self.ap.stand, self.A.leerer_stand())
        self.zaehler_anker = [0]
        self.anker_instanz = self.A.Anker(self.ap, aufloesen=lambda n: uid if n == "wamo-release" else None,
                                          uhr=lambda: self.zeit[0])
        self.anker_server = self.A.Server(self.anker_instanz, 0o660)
        self.anker_server.binden()
        self.anker_faden = threading.Thread(target=self.anker_server.lauf, daemon=True)
        self.anker_faden.start()

        def ende():
            self.anker_server.anhalten()
            self.anker_faden.join(5)
            self.anker_server.schliessen()

        self.addCleanup(ende)
        self.echt = RA.AnkerClient(self.ap.socket, erwartete_uid=uid)
        self.d = self.neu(anker=self.echt)

    def test_status_und_sperre_laufen_durch(self):
        a = self.echt.status()
        self.assertTrue(a.ok)
        self.assertEqual(a.daten["sperre"], "aus")
        self.assertEqual(self.echt.sperre_zustand(), "aus")

    def test_sperre_an_haelt_neue_anfragen_an(self):
        self.assertTrue(self.echt.sperre_an().ok)
        self.assertTrue(os.path.exists(self.ap.sperre))
        self.assertEqual(self.echt.sperre_zustand(), "an")
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "gesperrt")
        self.assertEqual(len(self.db.anfragen), 0)
        os.unlink(self.ap.sperre)  # so hebt Niko sie auf (wamo-anker sperre aus)
        self.assertTrue(self.sende(f"anfordern main {ADMIN}")["ok"])

    def test_sperre_an_stoppt_die_laufende_anfrage_der_rueckweg_bleibt(self):
        self.anfordern()
        self.d.arbeite_einmal()
        self.echt.sperre_an()
        self.assertEqual(self.d.arbeite_einmal(), "angehalten")
        self.assertEqual(self.anfrage()["status"], "uebernommen")
        r = self.sende("zurueck alles")  # der Anker hat noch keinen letzten guten Stand: ehrliche Antwort, Anfrage endet trotzdem
        self.assertEqual(self.endgrund(), ("abgebrochen", "zurueck"))
        self.assertEqual(r["grund"], "rueckweg_fehlgeschlagen")

    def test_sperre_dry_run_aendert_nichts(self):
        a = self.echt.sperre_an(trocken=True)
        self.assertTrue(a.ok)
        self.assertFalse(os.path.exists(self.ap.sperre))

    def test_rueckweg_mit_gutem_stand_laeuft_durch_den_anker(self):
        gut = {"version": "v0.1.0", "commit": "a" * 40, "ziel": "blau", "seit": "2026-10-22T08:00:00Z"}
        neu = {**gut, "version": "v0.1.1", "commit": "b" * 40, "ziel": "gruen"}
        stand = self.A.leerer_stand()
        stand["komponenten"]["ui"] = {"aktiv": neu, "letzte_gute": gut}
        self.A.stand_schreiben(self.ap.stand, stand)
        aufrufe = []
        self.anker_instanz.ausfuehren = lambda argv, zeit: (aufrufe.append(argv), (0, b"ok"))[1]
        with mock.patch.object(self.A, "schritt_pruefen", lambda p, name: f"/fest/{name}"):
            a = self.echt.rueckweg("ui")
        self.assertTrue(a.ok, a)
        self.assertEqual(aufrufe[0][0], "/fest/rueckweg-ui")
        self.assertEqual(aufrufe[0][1:], ["v0.1.0", "a" * 40, "blau"])

    def test_der_anker_verweigert_sperre_aus_fuer_wamo_release(self):
        """Doppelte Absicherung: Der Client kann es nicht bauen, und der Anker lehnt es für wamo-release ab."""
        with self.assertRaises(RA.Verboten):
            RA.zeile_bauen("sperre", "aus")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(self.ap.socket)
        s.sendall(b"0123456789abcdef0123456789abcdef sperre aus\n")
        antwort = json.loads(s.recv(4096))
        s.close()
        self.assertEqual(antwort["grund"], "recht")

    def test_gegenstelle_muss_root_sein(self):
        falsch = RA.AnkerClient(self.ap.socket, erwartete_uid=os.getuid() + 1)
        with self.assertRaises(RA.AnkerFehler) as cm:
            falsch.status()
        self.assertEqual(cm.exception.grund, "gegenstelle")
        self.assertEqual(falsch.sperre_zustand(), "unbekannt")

    def test_anker_aus_gilt_als_nicht_erreichbar(self):
        self.anker_server.anhalten()
        self.anker_faden.join(5)
        self.anker_server.schliessen()
        self.assertEqual(self.echt.sperre_zustand(), "unbekannt")
        self.assertEqual(self.sende(f"anfordern main {ADMIN}")["grund"], "anker_nicht_erreichbar")

    def test_ein_langsamer_anker_haelt_nicht_ewig_auf(self):
        stumm = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        pfad = os.path.join(self.tmp.name, "stumm.sock")
        stumm.bind(pfad)
        stumm.listen(1)
        self.addCleanup(stumm.close)
        c = RA.AnkerClient(pfad, erwartete_uid=os.getuid())
        t0 = time.monotonic()
        with mock.patch.object(RA, "ZEIT_KURZ_S", 0.5):
            self.assertEqual(c.sperre_zustand(), "unbekannt")
        self.assertLess(time.monotonic() - t0, 3)

    def test_lange_antwort_wird_abgelehnt(self):
        pfad = os.path.join(self.tmp.name, "gross.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(pfad)
        srv.listen(1)
        self.addCleanup(srv.close)

        def bedienen():
            conn, _ = srv.accept()
            conn.recv(1000)
            with contextlib.suppress(OSError):
                conn.sendall(b'{"ok":true,"x":"' + b"a" * 20000 + b'"}\n')
            conn.close()

        threading.Thread(target=bedienen, daemon=True).start()
        with self.assertRaises(RA.AnkerFehler):
            RA.AnkerClient(pfad, erwartete_uid=os.getuid()).status()


if __name__ == "__main__":
    unittest.main()
