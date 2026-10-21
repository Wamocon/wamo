"""Tests für die Serverseite des Rücktunnels je Gerät (infra/sokrates2/wamo_pc_abgleich.py und wamo_pc_token.py).

Nur Standardbibliothek, damit die Datei auch auf Sokrates-2 läuft:
    unter Windows und Linux:   uv run python -m pytest -q test_pc_server.py       (im Ordner voice-core/)
    auf Sokrates-2 (als root): sudo python3 -m unittest -v test_pc_server         (Datei und infra/sokrates2 liegen nebeneinander
                                                                                   oder WAMO_INFRA=<Ordner> setzen)
Teile, die Linux (dir_fd, select auf Pipes) oder root brauchen, werden sonst übersprungen. Der echte Test mit sshd ist
wamo-pc-rauchtest auf Sokrates-2.
"""

import base64
import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LINUX = sys.platform.startswith("linux")
ROOT = LINUX and os.geteuid() == 0
INFRA = Path(os.environ.get("WAMO_INFRA") or Path(__file__).resolve().parent.parent / "infra" / "sokrates2")


def _load(name: str, datei: str):
    spec = importlib.util.spec_from_file_location(name, INFRA / datei)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul  # dataclasses brauchen das Modul unter seinem Namen
    spec.loader.exec_module(modul)
    return modul


AB = _load("wamo_pc_abgleich", "wamo_pc_abgleich.py")
TK = _load("wamo_pc_token", "wamo_pc_token.py")

# ein echter ed25519-Schlüssel (nur der öffentliche Teil), Fingerabdruck daraus selbst berechnet
BLOB = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(1, 33))
PUB = "ssh-ed25519 " + base64.b64encode(BLOB).decode()
FP = "SHA256:" + base64.b64encode(hashlib.sha256(BLOB).digest()).decode().rstrip("=")
TOKEN, FREIGABE = "A" * 43, "B" * 43


def fp_attrappe(pubkey):
    return FP if pubkey == PUB else ""


class ZeilenPruefung(unittest.TestCase):
    def test_gueltige_zeile_und_platzname(self):
        p = AB.pruefe_zeile("5", "pc-anna", PUB, FP, False, fp_attrappe)
        self.assertEqual((p.name, p.benutzer, p.ordner), ("05", "wamo-t-05", "t05"))
        q = AB.pruefe_zeile("pa", "pc-probe-a", PUB, FP, True, fp_attrappe)
        self.assertEqual((q.benutzer, q.ordner), ("wamo-t-pa", "pa"))

    def test_ungueltiges_wird_abgelehnt(self):
        faelle = [("0", "pc-a", PUB, FP), ("65", "pc-a", PUB, FP), ("x", "pc-a", PUB, FP), (None, "pc-a", PUB, FP),
                  ("1", "laptop", PUB, FP), ("1", "pc-" + "a" * 30, PUB, FP), ("1", "pc-a--b", PUB, FP),
                  ("1", "pc-a", PUB.replace("ssh-ed25519", "ssh-rsa"), FP), ("1", "pc-a", PUB + " x", FP),
                  ("1", "pc-a", PUB, "SHA256:kurz"), ("1", "pc-a", PUB, "SHA256:" + "A" * 43)]
        for slot, kennung, pub, fp in faelle:
            with self.subTest(slot=slot, kennung=kennung):
                with self.assertRaises(ValueError):
                    AB.pruefe_zeile(slot, kennung, pub, fp, False, fp_attrappe)

    def test_probe_namen_nur_im_probemodus(self):
        with self.assertRaises(ValueError):
            AB.pruefe_zeile("pa", "pc-a", PUB, FP, False, fp_attrappe)
        with self.assertRaises(ValueError):
            AB.pruefe_zeile("05", "pc-a", PUB, FP, True, fp_attrappe)

    def test_datenbankausgabe(self):
        self.assertEqual(AB.parse_db_ausgabe("5|pc-a|k|f\n\n7|pc-b|k2|f2\n"), [("5", "pc-a", "k", "f"), ("7", "pc-b", "k2", "f2")])
        with self.assertRaises(ValueError):
            AB.parse_db_ausgabe("5|pc-a|k\n")
        with self.assertRaises(ValueError):
            AB.parse_db_ausgabe("5|pc-a|k|f|x\n")


class DatenbankListe(unittest.TestCase):
    def _lauf(self, code, stdout=""):
        def lauf(argv, **kw):
            self.argv = argv
            return subprocess.CompletedProcess(argv, code, stdout, "")
        return lauf

    def test_liste_wird_gelesen_set_zeile_entfernt(self):
        konfig = {"schema": "wamo_test", "container": "wamo-supabase-db", "von": "192.168.178.0/24"}
        zeilen = AB.liste_aus_datenbank(konfig, self._lauf(0, f"SET\n5|pc-anna|{PUB}|{FP}\n"))
        self.assertEqual(zeilen, [("5", "pc-anna", PUB, FP)])
        # schreibgeschützte Sitzung schon beim Verbindungsaufbau (nicht per "set" im selben Text)
        self.assertIn("PGOPTIONS=-c default_transaction_read_only=on", self.argv)
        self.assertIn("wamo_test.pc_abgleich_liste()", self.argv[-1])
        self.assertNotIn("set ", self.argv[-1])
        # keine Shell: die Werte stehen als einzelne Argumente
        self.assertEqual(self.argv[:3], ["/usr/bin/docker", "exec", "-i"])

    def test_fehler_der_datenbank_wirft(self):
        konfig = {"schema": "wamo_test", "container": "x", "von": "192.168.178.0/24"}
        with self.assertRaises(RuntimeError):
            AB.liste_aus_datenbank(konfig, self._lauf(2))

        def kaputt(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)
        with self.assertRaises(RuntimeError):
            AB.liste_aus_datenbank(konfig, kaputt)
        with self.assertRaises(RuntimeError):
            AB.liste_aus_datenbank(konfig, self._lauf(0, "nur|drei|spalten\n"))

    def test_konfig_wird_streng_geprueft(self):
        with tempfile.TemporaryDirectory() as d:
            pfad = Path(d) / "k.conf"
            pfad.write_text("# Quelle der Tunnelgeräte für den Abgleich (Umlaute im Kommentar)\nWAMO_DB_SCHEMA=wamo_test\n"
                            "WAMO_PC_FROM=192.168.178.0/24,10.8.0.0/24\n", encoding="utf-8")
            k = AB.lade_konfig(str(pfad))
            self.assertEqual((k["schema"], k["container"], k["von"]), ("wamo_test", "wamo-supabase-db", "192.168.178.0/24,10.8.0.0/24"))
            for schlecht in ("WAMO_DB_SCHEMA=public\n", "WAMO_DB_SCHEMA=wamo_dev\nWAMO_DB_CONTAINER=a b\n",
                             'WAMO_DB_SCHEMA=wamo_dev\nWAMO_PC_FROM=1.2.3.4" command="x\n'):
                pfad.write_text(schlecht, encoding="ascii")
                with self.assertRaises(ValueError):
                    AB.lade_konfig(str(pfad))


class SystemAttrappe(AB.System):
    """Verbucht, was der Abgleich tun will, statt es zu tun. Dateien liegen in einem Wegwerfordner."""

    def __init__(self, ordner: Path, probe=False, vorhanden=(), anlegen_scheitert=()):
        super().__init__(probe, wurzel=ordner / "run", schluessel_dir=ordner / "keys", tmpfiles_dir=ordner / "tmpfiles")
        (ordner / "tmpfiles").mkdir()
        (ordner / "run").mkdir()
        self.benutzer = set(vorhanden)
        self.anlegen_scheitert = set(anlegen_scheitert)
        self.beendet, self.angewendet = [], 0

    def fingerabdruck(self, pubkey):
        return fp_attrappe(pubkey)

    def vorhandene(self):
        return set(self.benutzer)

    def benutzer_anlegen(self, platz):
        if platz.name in self.anlegen_scheitert:
            raise RuntimeError("useradd fehlgeschlagen")
        neu = platz.name not in self.benutzer
        self.benutzer.add(platz.name)
        return neu

    def prozesse_beenden(self, benutzer):
        self.beendet.append(benutzer)

    def tmpfiles_anwenden(self):
        self.angewendet += 1


class AbgleichLauf(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ordner = Path(self._tmp.name)

    def _zeilen(self, *slots):
        return [(str(s), f"pc-g{s}", PUB, FP) for s in slots]

    def test_legt_an_schreibt_schluessel_und_tmpfiles(self):
        sys_ = SystemAttrappe(self.ordner)
        erg = AB.abgleich(self._zeilen(3, 7), sys_)
        self.assertEqual(erg["bereit"], ["03", "07"])
        self.assertEqual(sorted(erg["angelegt"]), ["03", "07"])
        schluessel = (self.ordner / "keys" / "wamo-t-03").read_text()
        self.assertTrue(schluessel.startswith('restrict,port-forwarding,from="192.168.178.0/24" ssh-ed25519 '))
        self.assertEqual(schluessel.count("\n"), 1)
        tmpf = (self.ordner / "tmpfiles" / "wamo-pc.conf").read_text()
        self.assertIn("2750 wamo-t-03 wamo-core -", tmpf)
        self.assertIn("/t07 2750 wamo-t-07 wamo-core -", tmpf)
        # zweiter Lauf: nichts Neues
        erg2 = AB.abgleich(self._zeilen(3, 7), sys_)
        self.assertEqual(erg2["angelegt"], [])
        self.assertFalse(sys_.beendet)

    def test_nicht_mehr_gelistete_werden_gesperrt_nicht_geloescht(self):
        sys_ = SystemAttrappe(self.ordner)
        AB.abgleich(self._zeilen(3, 7), sys_)
        (sys_.wurzel / "t07").mkdir()
        (sys_.wurzel / "t07" / "host-token").write_text("x")
        erg = AB.abgleich(self._zeilen(3), sys_)
        self.assertEqual(erg["gesperrt"], ["07"])
        self.assertEqual(sys_.beendet, ["wamo-t-07"])
        self.assertFalse((self.ordner / "keys" / "wamo-t-07").exists())
        self.assertEqual(len(list((self.ordner / "keys" / "gesperrt").glob("wamo-t-07.*"))), 1)
        self.assertFalse((sys_.wurzel / "t07").exists())
        self.assertTrue((self.ordner / "keys" / "wamo-t-03").exists())
        self.assertNotIn("t07", (self.ordner / "tmpfiles" / "wamo-pc.conf").read_text())
        self.assertIn("07", sys_.benutzer)  # Benutzer bleiben bestehen (keine uid-Wiederverwendung)

    def test_schluesselwechsel_beendet_alte_sitzungen_und_leert_den_ordner(self):
        sys_ = SystemAttrappe(self.ordner)
        AB.abgleich(self._zeilen(4), sys_)
        (sys_.wurzel / "t04").mkdir()
        (sys_.wurzel / "t04" / "host-token").write_text("x")
        (self.ordner / "keys" / "wamo-t-04").write_text("ssh-ed25519 anderer-schluessel\n")  # Platz gehörte einem anderen
        sys_.beendet.clear()
        AB.abgleich(self._zeilen(4), sys_)
        self.assertEqual(sys_.beendet, ["wamo-t-04"])
        self.assertTrue((sys_.wurzel / "t04").is_dir())
        self.assertEqual(list((sys_.wurzel / "t04").iterdir()), [])  # Ordner bleibt, Inhalt weg

    def test_leere_liste_sperrt_alles(self):
        sys_ = SystemAttrappe(self.ordner)
        AB.abgleich(self._zeilen(1, 2), sys_)
        erg = AB.abgleich([], sys_)
        self.assertEqual(sorted(erg["gesperrt"]), ["01", "02"])

    def test_doppelte_und_kaputte_zeilen_werden_uebersprungen(self):
        sys_ = SystemAttrappe(self.ordner)
        zeilen = self._zeilen(4) + [("4", "pc-zwei", PUB, FP), ("99", "pc-x", PUB, FP), ("5", "kein-pc", PUB, FP)]
        erg = AB.abgleich(zeilen, sys_)
        self.assertEqual(erg["bereit"], ["04"])
        self.assertEqual(erg["uebersprungen"], 3)
        self.assertEqual((self.ordner / "keys" / "wamo-t-04").read_text().count("pc-g4"), 1)  # erste Zeile gilt

    def test_benutzer_ohne_anlegen_bekommt_keinen_schluessel(self):
        sys_ = SystemAttrappe(self.ordner, anlegen_scheitert={"06"})
        erg = AB.abgleich(self._zeilen(6, 8), sys_)
        self.assertEqual(erg["bereit"], ["08"])
        self.assertFalse((self.ordner / "keys" / "wamo-t-06").exists())

    def test_tmpfiles_fehler_stoppt_vor_den_schluesseln(self):
        class Defekt(SystemAttrappe):
            def tmpfiles_anwenden(self):
                raise RuntimeError("systemd-tmpfiles --create fehlgeschlagen")
        sys_ = Defekt(self.ordner)
        erg = AB.abgleich(self._zeilen(9), sys_)
        self.assertEqual(erg["bereit"], [])
        self.assertEqual(erg["fehler"], 1)  # haupt() beendet sich damit mit Exit 1
        self.assertFalse((self.ordner / "keys" / "wamo-t-09").exists())  # ohne Ordner nützt der Schlüssel nichts

    def test_widerruf_haengt_nicht_an_tmpfiles(self):
        """Befund des Reviews: ein tmpfiles-Fehler darf keinen Widerruf aufhalten."""
        class Defekt(SystemAttrappe):
            kaputt = False

            def tmpfiles_anwenden(self):
                if self.kaputt:
                    raise RuntimeError("systemd-tmpfiles --create fehlgeschlagen")
        sys_ = Defekt(self.ordner)
        AB.abgleich(self._zeilen(3, 7), sys_)
        sys_.kaputt = True
        erg = AB.abgleich(self._zeilen(3), sys_)
        self.assertEqual(erg["gesperrt"], ["07"])
        self.assertFalse((self.ordner / "keys" / "wamo-t-07").exists())
        self.assertIn("wamo-t-07", sys_.beendet)

    def test_tmpfiles_oserror_wird_gefangen(self):
        class Defekt(SystemAttrappe):
            def tmpfiles_schreiben(self, plaetze):
                raise PermissionError("kein Schreibrecht")
        sys_ = Defekt(self.ordner)
        AB.abgleich(self._zeilen(3), sys_)
        erg = AB.abgleich([], sys_)  # Widerruf klappt trotzdem
        self.assertEqual(erg["gesperrt"], ["03"])

    def test_schluesselwechsel_ueberschreibt_atomar(self):
        sys_ = SystemAttrappe(self.ordner)
        AB.abgleich(self._zeilen(2), sys_)
        pfad = self.ordner / "keys" / "wamo-t-02"
        platz = AB.pruefe_zeile("2", "pc-g2", PUB, FP, False, fp_attrappe)
        self.assertEqual(AB.System.schluessel_schreiben(sys_, platz), "")
        pfad.write_text("alt\n")
        self.assertEqual(AB.System.schluessel_schreiben(sys_, platz), "geaendert")
        self.assertIn(PUB, pfad.read_text())
        self.assertEqual([p.name for p in (self.ordner / "keys").iterdir() if p.name.startswith(".")], [])


class Hilfen(unittest.TestCase):
    def test_passwort_gesperrt_aus_shadow(self):
        with tempfile.TemporaryDirectory() as d:
            shadow = Path(d) / "shadow"
            shadow.write_text("wamo-t-01:!:20000::::::\nwamo-t-02:*:20000::::::\nwamo-t-03:$6$abc$def:20000::::::\n"
                              "wamo-t-04::20000::::::\nroot:x:1:::::\n", encoding="utf-8")
            alt = AB.SHADOW
            AB.SHADOW = str(shadow)
            try:
                s = AB.System(False)
                self.assertTrue(s.passwort_gesperrt("wamo-t-01"))
                self.assertTrue(s.passwort_gesperrt("wamo-t-02"))
                self.assertFalse(s.passwort_gesperrt("wamo-t-03"))
                self.assertFalse(s.passwort_gesperrt("wamo-t-04"))
                self.assertFalse(s.passwort_gesperrt("wamo-t-99"))
            finally:
                AB.SHADOW = alt

    def test_liste_zu_alt(self):
        with tempfile.TemporaryDirectory() as d:
            marke = Path(d) / "ok"
            self.assertFalse(AB.liste_zu_alt(marke, 300))  # ohne Marke beginnt die Zählung jetzt
            self.assertTrue(marke.exists())
            zeit = marke.stat().st_mtime
            self.assertFalse(AB.liste_zu_alt(marke, 300, jetzt=lambda: zeit + 299))
            self.assertTrue(AB.liste_zu_alt(marke, 300, jetzt=lambda: zeit + 301))
            AB.liste_ok(marke)
            self.assertFalse(AB.liste_zu_alt(marke, 300))

    def test_ordner_von(self):
        self.assertEqual(AB.ordner_von("wamo-t-05"), "t05")
        self.assertEqual(AB.ordner_von("wamo-t-pa"), "pa")

    @unittest.skipUnless(LINUX, "braucht /proc-Nachbau mit Symlinks")
    def test_streuner_findet_nur_sockets_ausserhalb_des_eigenen_ordners(self):
        with tempfile.TemporaryDirectory() as d:
            proc = Path(d) / "proc"
            (proc / "net").mkdir(parents=True)
            kopf = "Num RefCount Protocol Flags Type St Inode Path\n"
            (proc / "net" / "unix").write_text(
                kopf
                + "0: 2 0 00010000 0001 01 111 /run/wamo-pc/t05/pc.sock\n"       # eigener Tunnel: in Ordnung
                + "0: 2 0 00010000 0001 01 222 /tmp/streuner.sock\n"                # Streuner
                + "0: 2 0 00010000 0001 01 333 /run/wamo-pc/t06/pc.sock\n"          # Ordner eines anderen Geräts
                + "0: 2 0 00010000 0001 01 444 /run/wamo-pc/t05/unter/x.sock\n"     # tieferer Pfad im eigenen Ordner
                + "0: 2 0 00000000 0001 01 555 /var/crash/verbunden.sock\n"         # verbunden, nicht lauschend
                + "0: 2 0 00010000 0001 01 666 /run/user/1000/systemd/private\n"    # gehört einem anderen Prozess
                + "0: 2 0 00010000 0001 01 777\n", encoding="utf-8")                # namenlos
            for pid, cmd, inodes in ((10, "sshd: wamo-t-05@notty", (111, 222, 333, 444, 555)),
                                     (11, "/lib/systemd/systemd --user", (666,)),
                                     (12, "sshd: wamo-t-05 [priv]", (222,)),
                                     (13, "sshd: wamocon@pts/0", (222,))):
                (proc / str(pid) / "fd").mkdir(parents=True)
                (proc / str(pid) / "cmdline").write_bytes(cmd.encode() + b"\0")
                for i, ino in enumerate(inodes):
                    os.symlink(f"socket:[{ino}]", proc / str(pid) / "fd" / str(i))
            gefunden = AB.finde_streuner(str(proc), Path("/run/wamo-pc"))
            self.assertEqual(list(gefunden), ["wamo-t-05"])
            self.assertEqual(sorted(gefunden["wamo-t-05"]), ["/run/wamo-pc/t05/unter/x.sock", "/run/wamo-pc/t06/pc.sock",
                                                              "/tmp/streuner.sock"])
            system = SystemAttrappe(Path(d))
            self.assertEqual(AB.streuner(system, str(proc)), 1)
            self.assertEqual(system.beendet, ["wamo-t-05"])


class TokenEingaben(unittest.TestCase):
    def test_platz_aus_benutzername(self):
        self.assertEqual(TK.platz_ordner("wamo-t-05"), "t05")
        self.assertEqual(TK.platz_ordner("wamo-t-pa"), "pa")
        for schlecht in ("root", "wamo-t-00", "wamo-t-65", "wamo-t-5", "wamo-t-05x", "wamo-t-../x", "wamo-tunnel"):
            with self.assertRaises(TK.Abbruch):
                TK.platz_ordner(schlecht)

    def test_geheimnisse(self):
        self.assertEqual(TK.pruefe_geheimnisse([TOKEN.encode(), FREIGABE.encode()]), (TOKEN, FREIGABE))
        for zeilen in ([b"A" * 43], [b"A" * 42, b"B" * 43], [b"A" * 43, b"A" * 43], [b"A" * 42 + b"!", b"B" * 43],
                       [b"A" * 513, b"B" * 43], ["Ä".encode() * 30, b"B" * 43], [b"A" * 43, b"B" * 43, b"C" * 43]):
            with self.assertRaises(TK.Abbruch, msg=str(zeilen[:1])):
                TK.pruefe_geheimnisse(zeilen)

    def test_fingerabdruck_aus_auth_datei(self):
        with tempfile.TemporaryDirectory() as d:
            pfad = Path(d) / "auth"
            pfad.write_text(f"publickey {PUB}\n", encoding="ascii")
            self.assertEqual(TK.fingerabdruck_aus_auth(str(pfad)), FP)
            for schlecht in ("publickey ssh-rsa AAAA\n", f"keyboard-interactive/pam\n", "publickey ssh-ed25519 !!!\n",
                             "publickey ssh-ed25519 " + base64.b64encode(b"zu kurz").decode() + "\n"):
                pfad.write_text(schlecht, encoding="ascii")
                with self.assertRaises(TK.Abbruch):
                    TK.fingerabdruck_aus_auth(str(pfad))
        with self.assertRaises(TK.Abbruch):
            TK.fingerabdruck_aus_auth(None)
        with self.assertRaises(TK.Abbruch):
            TK.fingerabdruck_aus_auth("/nicht/vorhanden")

    @unittest.skipUnless(LINUX, "select auf Pipes")
    def test_zwei_zeilen_lesen(self):
        r, w = os.pipe()
        try:
            os.write(w, b"AAA\r\nBBB\nrest")
            self.assertEqual(TK.lies_zeilen(r, 2.0), [b"AAA", b"BBB"])
        finally:
            os.close(r)
            os.close(w)

    @unittest.skipUnless(LINUX, "select auf Pipes")
    def test_zu_kurz_zu_lang_zeitueberschreitung(self):
        for eingabe, frist in ((b"nur eine\n", 2.0), (b"A" * 700 + b"\nB\n", 2.0), (b"A" * 10_000, 2.0)):
            r, w = os.pipe()
            try:
                os.write(w, eingabe)
                os.close(w)
                w = None
                with self.assertRaises(TK.Abbruch):
                    TK.lies_zeilen(r, frist)
            finally:
                os.close(r)
                if w is not None:
                    os.close(w)
        r, w = os.pipe()  # nichts kommt an: Zeitüberschreitung statt Hängen
        try:
            with self.assertRaises(TK.Abbruch):
                TK.lies_zeilen(r, 0.3)
        finally:
            os.close(r)
            os.close(w)


@unittest.skipUnless(ROOT, "braucht Linux und root (Besitzer des Wurzelordners muss root sein)")
class TokenDateien(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.wurzel = Path(self._tmp.name) / "wurzel"
        self.wurzel.mkdir(mode=0o711)
        os.chmod(self.wurzel, 0o711)
        self.uid, self.gid = 65000, 65001
        self.ordner = self.wurzel / "t05"
        self.ordner.mkdir()
        os.chown(self.ordner, self.uid, self.gid)
        os.chmod(self.ordner, 0o2750)

    def test_ordner_oeffnen_und_rechte(self):
        fd = TK.oeffne_geraeteordner("t05", self.uid, self.gid, str(self.wurzel))
        os.close(fd)
        with self.assertRaises(TK.Abbruch):
            TK.oeffne_geraeteordner("t05", self.uid + 1, self.gid, str(self.wurzel))  # falscher Besitzer
        with self.assertRaises(TK.Abbruch):
            TK.oeffne_geraeteordner("t05", self.uid, self.gid + 1, str(self.wurzel))  # falsche Gruppe
        os.chmod(self.ordner, 0o2770)
        with self.assertRaises(TK.Abbruch):
            TK.oeffne_geraeteordner("t05", self.uid, self.gid, str(self.wurzel))  # Gruppe darf schreiben
        os.chmod(self.ordner, 0o2750)
        with self.assertRaises(TK.Abbruch):
            TK.oeffne_geraeteordner("t06", self.uid, self.gid, str(self.wurzel))  # fehlt

    def test_link_statt_ordner_wird_nicht_verfolgt(self):
        (self.wurzel / "t09").symlink_to(self.ordner)
        with self.assertRaises(TK.Abbruch):
            TK.oeffne_geraeteordner("t09", self.uid, self.gid, str(self.wurzel))

    def test_wurzel_beschreibbar_wird_abgelehnt(self):
        os.chmod(self.wurzel, 0o733)
        with self.assertRaises(TK.Abbruch):
            TK.oeffne_geraeteordner("t05", self.uid, self.gid, str(self.wurzel))

    def test_schreiben_und_nur_eigene_inodes_loeschen(self):
        fd = TK.oeffne_geraeteordner("t05", self.uid, self.gid, str(self.wurzel))
        try:
            gid = os.stat(self.ordner).st_gid
            eigene = {}
            for name in ("host-token", "schluessel"):
                eigene[name] = TK.schreibe_atomar(fd, name, "inhalt\n", gid, eigene)
                # der Inode steht schon vor dem rename in eigene (Signal zwischen rename und Rückkehr)
                self.assertEqual(eigene[name], (os.stat(self.ordner / name).st_dev, os.stat(self.ordner / name).st_ino))
            s = os.stat(self.ordner / "host-token")
            self.assertEqual(stat.S_IMODE(s.st_mode), 0o640)
            self.assertEqual(sorted(p.name for p in self.ordner.iterdir()), ["host-token", "schluessel"])
            # eine neuere Verbindung ersetzt das Token: beim Aufräumen bleibt ihre Datei
            neu = self.ordner / "neu"
            neu.write_text("neuer\n")
            os.replace(neu, self.ordner / "host-token")
            TK.loesche_eigene(fd, eigene)
            self.assertEqual((self.ordner / "host-token").read_text(), "neuer\n")
            self.assertFalse((self.ordner / "schluessel").exists())
            # ein Link statt der Datei wird nie gelöscht oder verfolgt
            (self.ordner / "host-token").unlink()
            (self.ordner / "host-token").symlink_to("/etc/hostname")
            TK.loesche_eigene(fd, {"host-token": eigene["host-token"]})
            self.assertTrue((self.ordner / "host-token").is_symlink())
        finally:
            os.close(fd)

    def test_andere_sitzungen_finden(self):
        # /proc einer Attrappe: eigene Kette wird nie gemeldet, fremde sshd-Sitzung des Benutzers schon
        proc = Path(self._tmp.name) / "proc"
        for pid, cmd, stat_pid in ((100, b"sshd: wamo-t-05@notty\0", 1), (101, b"sshd: wamo-t-06@notty\0", 1),
                                   (102, b"sshd: wamo-t-05@notty\0", 100), (103, b"bash\0", 1)):
            (proc / str(pid)).mkdir(parents=True)
            (proc / str(pid) / "cmdline").write_bytes(cmd)
            (proc / str(pid) / "stat").write_bytes(f"{pid} (sshd) S {stat_pid} 0 0".encode())
        # st_uid der Attrappenordner ist root; deshalb Benutzer-ID 0 abfragen
        self.assertEqual(sorted(TK.andere_sitzungen("wamo-t-05", 0, str(proc))), [100, 102])
        self.assertEqual(TK.andere_sitzungen("wamo-t-05", 0, str(proc), ausser={100, 102}), [])
        self.assertEqual(TK.eigene_vorfahren(102, str(proc)), {102, 100})


if __name__ == "__main__":
    unittest.main()
