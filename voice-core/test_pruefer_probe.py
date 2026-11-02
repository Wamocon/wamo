"""Tests der Migrationsprobe (infra/sokrates2/release/pruefer/migration_probe.py und migprobe_dienst.py, Schritt 2.5b).

Laufen ohne Docker und ohne Datenbank: Der Container wird durch einen Ersatz für subprocess.run ersetzt, der die Ausgabe erzeugt. Die echte Probe
(Wegwerf-Postgres auf Sokrates-2) prüft pruefer_rauchtest.py Teil H. Geprüft werden die Auswertung (auch gegen gefälschte Ausgabezeilen), die
Eingabeprüfung der Dateien, die Härtung des Docker-Aufrufs, die Annahme im Dienst und die Anbindung des Annahme-Dienstes.

Ausführen:  uv run python -m pytest -q test_pruefer_probe.py   (in voice-core/)
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PRUEFER = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "release" / "pruefer"
sys.path.insert(0, str(PRUEFER))

import migprobe_dienst as D  # noqa: E402
import migration_probe as M  # noqa: E402
import pruefer_annahme as N  # noqa: E402

POSIX = os.name == "posix"
NONCE = "ab12cd34ef56ab12cd34ef56"
ALLES_OK = ("start ok server", "stubs ok attrappen", "hin ok alle", "tests ok alle", "down ok alle", "wieder ok alle", "ende ok fertig")


def ausgabe(*phasen: str, nonce: str = NONCE) -> str:
    return "".join(f"ERG {nonce} {p}\n" for p in phasen)


def baum(wurzel: Path, dateien: dict[str, str]) -> Path:
    for pfad, inhalt in dateien.items():
        p = wurzel / pfad
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(inhalt, encoding="utf-8")
    return wurzel


class Auswertung(unittest.TestCase):
    def test_alles_ok(self):
        r = M.auswerten(ausgabe(*ALLES_OK), NONCE)
        self.assertEqual((r["wert"], r["fehler"]), ("ok", ""))

    def test_erste_fehlgeschlagene_phase_bestimmt_den_code(self):
        r = M.auswerten(ausgabe("start ok server", "stubs ok attrappen", "hin fehler m0017:zweit"), NONCE)
        self.assertEqual((r["wert"], r["fehler"]), ("fehler", "frisch_hin_m0017:zweit"))
        r = M.auswerten(ausgabe(*ALLES_OK[:3], "tests fehler t0002"), NONCE)
        self.assertEqual(r["fehler"], "frisch_tests_t0002")

    def test_unvollstaendig_ist_ein_fehler(self):
        self.assertEqual(M.auswerten("", NONCE)["fehler"], "frisch_unvollstaendig")
        self.assertEqual(M.auswerten(ausgabe(*ALLES_OK[:5]), NONCE)["fehler"], "frisch_unvollstaendig")   # kein wieder, kein ende

    def test_zeilen_ohne_den_zufallswert_zaehlen_nicht(self):
        # eine Migration versucht, gute Zeilen in die Ausgabe zu schreiben
        faelschung = "".join(f"ERG {p}\n" for p in ALLES_OK) + ausgabe(*ALLES_OK, nonce="0" * 24)
        self.assertEqual(M.auswerten(faelschung, NONCE)["fehler"], "frisch_unvollstaendig")
        gemischt = ausgabe("start ok server", "stubs ok attrappen", "hin fehler m0017:erst") + "".join(f"ERG {p}\n" for p in ALLES_OK)
        self.assertEqual(M.auswerten(gemischt, NONCE)["fehler"], "frisch_hin_m0017:erst")

    def test_beliebiger_text_wird_ignoriert_und_nie_durchgereicht(self):
        r = M.auswerten("create table geheim\nERROR: boese\n" + ausgabe(*ALLES_OK), NONCE)
        self.assertEqual(r["wert"], "ok")
        self.assertNotIn("geheim", json.dumps(r))

    def test_zu_lange_codes_passen_nicht_in_die_zeilenform(self):
        r = M.auswerten(ausgabe(*ALLES_OK[:2], "hin fehler " + "x" * 80), NONCE)
        self.assertEqual(r["fehler"], "frisch_unvollstaendig")


class Dateien(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.neu = Path(self.tmp.name) / "neu"
        self.ziel = Path(self.tmp.name) / "ziel"
        self.ziel.mkdir()

    def kopie(self, dateien):
        baum(self.neu, dateien)
        return M._kopiere(str(self.neu), str(self.ziel))

    def test_gueltige_dateien_werden_kopiert(self):
        self.assertIsNone(self.kopie({"supabase/migrations/0001_a.sql": "select 1;", "supabase/migrations/0001_a.down.sql": "select 1;",
                                      "supabase/tests/0001_a_test.sql": "select 1;"}))
        self.assertEqual(sorted(os.listdir(self.ziel / "mig")), ["0001_a.down.sql", "0001_a.sql"])
        self.assertEqual(os.listdir(self.ziel / "tests"), ["0001_a_test.sql"])

    @unittest.skipUnless(POSIX, "UMask nur unter Linux")
    def test_umask_des_dienstes_sperrt_den_container_nicht_aus(self):
        # Unter systemd gilt UMask=0077: ohne ausdrückliches chmod wären mig und tests 0700, der Postgres-Benutzer im Container sähe nichts
        alt = os.umask(0o077)
        self.addCleanup(os.umask, alt)
        self.assertIsNone(self.kopie({"supabase/migrations/0001_a.sql": "select 1;", "supabase/migrations/0001_a.down.sql": "select 1;",
                                      "supabase/tests/0001_a_test.sql": "select 1;"}))
        for unter in ("mig", "tests"):
            self.assertEqual(os.stat(self.ziel / unter).st_mode & 0o555, 0o555, unter)
        self.assertEqual(os.stat(self.ziel / "mig" / "0001_a.sql").st_mode & 0o444, 0o444)
        self.assertEqual((self.ziel / "zahlen").read_text(), "1 1 1\n")   # der Container prüft diese Zahlen gegen das, was er sieht

    def test_harness_prueft_die_dateizahlen_und_wertet_einen_leeren_lauf_nie_als_ok(self):
        for muss in ("/m/zahlen", 'erg "$1" fehler zahl', "erg tests fehler zahl", "erg down fehler zahl", '[ "$anz" -gt 0 ]'):
            self.assertIn(muss, M.HARNESS)

    def test_ungueltiger_name(self):
        self.assertEqual(self.kopie({"supabase/migrations/x.sql": "a"}), "name_ungueltig")

    def test_zu_gross(self):
        self.assertEqual(self.kopie({"supabase/migrations/0001_a.sql": "x" * (M.DATEI_MAX + 1)}), "datei_zu_gross")

    @unittest.skipUnless(POSIX, "Verknüpfungen nur unter Linux")
    def test_verknuepfung_ist_ein_fehler(self):
        baum(self.neu, {"supabase/migrations/0001_a.sql": "a", "geheim.txt": "g"})
        os.symlink(self.neu / "geheim.txt", self.neu / "supabase/migrations/0002_b.sql")
        self.assertEqual(M._kopiere(str(self.neu), str(self.ziel)), "keine_normale_datei")

    def test_aenderung_wird_erkannt(self):
        basis = Path(self.tmp.name) / "basis"
        baum(basis, {"supabase/migrations/0001_a.sql": "a"})
        baum(self.neu, {"supabase/migrations/0001_a.sql": "a"})
        self.assertFalse(M.migrationen_geaendert(str(basis), str(self.neu)))
        baum(self.neu, {"supabase/migrations/0002_b.sql": "b"})
        self.assertTrue(M.migrationen_geaendert(str(basis), str(self.neu)))
        self.assertFalse(M.migrationen_geaendert(str(basis / "x"), str(basis / "y")))


class Haertung(unittest.TestCase):
    def test_docker_aufruf_hat_keine_rechte_und_kein_netz(self):
        a = M.docker_argv("/ordner", "wamo-migprobe-x")
        for muss in ("--network", "none", "--cap-drop", "ALL", "--read-only", "--pull", "never", "--rm", "no-new-privileges", "--user", "100:101"):
            self.assertIn(muss, a)
        for verboten in ("--privileged", "--cap-add", "--network=host", "--pid", "--ipc", "--device", "--env", "-e"):
            self.assertFalse(any(x == verboten or x.startswith(verboten + "=") for x in a), verboten)
        mounts = [a[i + 1] for i, x in enumerate(a) if x == "-v"]
        self.assertEqual(mounts, ["/ordner:/m:ro"])   # einziger Einhängepunkt, schreibgeschützt
        self.assertEqual(a[a.index("--entrypoint") + 1], "bash")

    def test_harness_liest_den_zufallswert_von_stdin_und_gibt_nur_erg_zeilen_aus(self):
        self.assertIn("read -r NONCE", M.HARNESS)
        self.assertNotIn("export NONCE", M.HARNESS)
        self.assertNotIn("cat /work", M.HARNESS)   # keine Server- oder Protokollausgabe nach außen
        self.assertNotIn(chr(0x2014), M.HARNESS)

    @unittest.skipUnless(POSIX and __import__("shutil").which("bash"), "braucht bash")
    def test_harness_ist_gueltiges_bash(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, newline="\n") as f:
            f.write(M.HARNESS)
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(subprocess.run(["bash", "-n", f.name], capture_output=True).returncode, 0)


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.neu = baum(Path(self.tmp.name) / "neu", {"supabase/migrations/0001_a.sql": "select 1;", "supabase/migrations/0001_a.down.sql": "select 1;"})
        self.arbeit = Path(self.tmp.name) / "arbeit"
        self.arbeit.mkdir()

    def ersatz(self, *zeilen, code=0):
        def laufen(argv, **kw):
            nonce = kw["input"].decode().strip()
            self.aufruf = (argv, kw)
            text = "".join(f"ERG {nonce} {z}\n" for z in zeilen)
            return subprocess.CompletedProcess(argv, code, stdout=text.encode(), stderr=b"")
        return laufen

    def test_erfolg_und_aufraeumen(self):
        r = M.probe(str(self.neu), str(self.arbeit), laufen=self.ersatz(*ALLES_OK))
        self.assertEqual(r["wert"], "ok")
        self.assertEqual(os.listdir(self.arbeit), [])   # Arbeitsordner der Probe ist weg
        self.assertEqual(len(self.aufruf[1]["input"].decode().strip()), 24)   # der Zufallswert geht über stdin

    def test_fehlschlag(self):
        r = M.probe(str(self.neu), str(self.arbeit), laufen=self.ersatz(*ALLES_OK[:2], "hin fehler m0001:zweit"))
        self.assertEqual((r["wert"], r["fehler"]), ("fehler", "frisch_hin_m0001:zweit"))

    def test_zeitlimit_docker_fehlt_und_container_ohne_ausgabe(self):
        def zeit(argv, **kw):
            if argv[1] == "kill":
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            raise subprocess.TimeoutExpired(argv, 1)
        self.assertEqual(M.probe(str(self.neu), str(self.arbeit), laufen=zeit)["fehler"], "frisch_zeitlimit")

        def weg(argv, **kw):
            raise FileNotFoundError("docker")
        self.assertEqual(M.probe(str(self.neu), str(self.arbeit), laufen=weg)["fehler"], "frisch_docker_fehlt")
        self.assertEqual(M.probe(str(self.neu), str(self.arbeit), laufen=self.ersatz(code=125))["fehler"], "frisch_container_fehler")

    def test_ungueltige_datei_startet_keinen_container(self):
        baum(self.neu, {"supabase/migrations/boese.sql": "x"})
        aufrufe = []
        r = M.probe(str(self.neu), str(self.arbeit), laufen=lambda *a, **k: aufrufe.append(a))
        self.assertEqual((r["fehler"], aufrufe), ("frisch_name_ungueltig", []))


@unittest.skipUnless(POSIX, "Eigentümer- und Verknüpfungsprüfung nur unter Linux")
class DienstAnnahme(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.annahme = self.tmp.name
        self.ordner = Path(self.annahme) / "a.abc12345"
        (self.ordner / "neu").mkdir(parents=True)
        os.chmod(self.ordner, 0o700)

    def frage(self, zeile: bytes, peer=0, probe=None, root_uid=None):
        raus = io.BytesIO()
        code = D.bedienen(io.BytesIO(zeile), raus, peer, self.annahme, probe or (lambda neu, arbeit: {"wert": "ok", "fehler": ""}),
                          os.getuid() if root_uid is None else root_uid)
        return code, json.loads(raus.getvalue())

    def test_nur_root_darf_fragen(self):
        for peer in (None, 1000, 999):
            self.assertEqual(self.frage(b"MIGPROBE a.abc12345\n", peer=peer), (0, {"wert": "fehler", "fehler": "benutzer"}))

    def test_form_der_anfrage(self):
        for zeile in (b"", b"MIGPROBE\n", b"MIGPROBE ../etc\n", b"MIGPROBE a.abc12345", b"MIGPROBE a.x\n", b"MIGPROBE a.abc/../x\n",
                      b"MIGPROBE /srv/wamo-pruefer/annahme/a.abc12345\n", b"HALLO a.abc12345\n", b"MIGPROBE a.abc12345 -v\n"):
            with self.subTest(zeile=zeile):
                self.assertEqual(self.frage(zeile), (0, {"wert": "fehler", "fehler": "form"}))

    def test_fehlender_ordner_fremder_besitzer_und_offene_rechte(self):
        self.assertEqual(self.frage(b"MIGPROBE a.zzzzzzzz\n")[1]["fehler"], "ordner_fehlt")
        self.assertEqual(self.frage(b"MIGPROBE a.abc12345\n", root_uid=os.getuid() + 1)[1]["fehler"], "ordner_unsicher")
        os.chmod(self.ordner, 0o777)
        self.assertEqual(self.frage(b"MIGPROBE a.abc12345\n")[1]["fehler"], "ordner_unsicher")

    def test_baum_muss_ein_echter_ordner_sein(self):
        os.rmdir(self.ordner / "neu")
        os.symlink(self.annahme, self.ordner / "neu")
        self.assertEqual(self.frage(b"MIGPROBE a.abc12345\n")[1]["fehler"], "baum_fehlt")

    def test_ergebnis_wird_bereinigt_und_eine_panne_nennt_keinen_text(self):
        self.assertEqual(self.frage(b"MIGPROBE a.abc12345\n"), (0, {"wert": "ok", "fehler": ""}))
        code, a = self.frage(b"MIGPROBE a.abc12345\n", probe=lambda n, o: {"wert": "fehler", "fehler": "frisch_hin_m0001:erst; DROP"})
        self.assertEqual((code, a), (0, {"wert": "fehler", "fehler": "frisch_hin_m0001:erst"}))

        def panne(n, o):
            raise RuntimeError("geheimer Text")
        code, a = self.frage(b"MIGPROBE a.abc12345\n", probe=panne)
        self.assertEqual((code, a["wert"]), (0, "fehler"))
        self.assertNotIn("geheim", json.dumps(a))


class AnnahmeAnbindung(unittest.TestCase):
    @unittest.skipUnless(POSIX, "Unix-Socket nur unter Linux")
    def test_nicht_erreichbarer_dienst_ist_ein_fehler(self):
        r = N.migprobe_anfrage("/tmp/neu", "/tmp/a.abc12345", "/nicht/vorhanden.sock", 2.0)
        self.assertEqual(r, {"wert": "fehler", "fehler": "frisch_dienst_nicht_erreichbar"})

    @unittest.skipUnless(POSIX, "Unix-Socket nur unter Linux")
    def test_antwort_des_dienstes_wird_uebernommen_und_bereinigt(self):
        import socket
        import threading
        with tempfile.TemporaryDirectory() as t:
            pfad = os.path.join(t, "p.sock")
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(pfad)
            srv.listen(1)
            empfangen = []

            def antworte():
                c, _ = srv.accept()
                empfangen.append(c.recv(200))
                c.sendall(b'{"wert": "fehler", "fehler": "hin_m0001:erst; rm -rf"}\n')
                c.close()
            th = threading.Thread(target=antworte, daemon=True)
            th.start()
            r = N.migprobe_anfrage("/x/neu", "/srv/a.abc12345/", pfad, 5.0)
            th.join(5)
            srv.close()
        self.assertEqual(empfangen, [b"MIGPROBE a.abc12345\n"])
        self.assertEqual(r, {"wert": "fehler", "fehler": "frisch_hin_m0001:erstrmrf"})

    @unittest.skipUnless(POSIX, "Unix-Socket nur unter Linux")
    def test_code_mit_praefix_bekommt_ihn_nicht_doppelt(self):
        import socket
        import threading
        with tempfile.TemporaryDirectory() as t:
            pfad = os.path.join(t, "p.sock")
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(pfad)
            srv.listen(1)

            def antworte():
                c, _ = srv.accept()
                c.recv(200)
                c.sendall(b'{"wert": "fehler", "fehler": "frisch_hin_m0001:erst"}\n')
                c.close()
            th = threading.Thread(target=antworte, daemon=True)
            th.start()
            r = N.migprobe_anfrage("/x/neu", "/srv/a.abc12345", pfad, 5.0)
            th.join(5)
            srv.close()
        self.assertEqual(r, {"wert": "fehler", "fehler": "frisch_hin_m0001:erst"})

    def test_konfig_hat_die_probe_standardmaessig_an(self):
        c = N.Konfig()
        self.assertTrue(c.migprobe)
        self.assertEqual(c.migprobe_socket, "/run/wamo-migprobe/probe.sock")


if __name__ == "__main__":
    unittest.main()
