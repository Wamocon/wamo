"""Tests für den Notanker auf Sokrates-2 (infra/sokrates2/anker/wamo_anker.py, Konzept Abschnitt G, Schritt 0.1).

Laufen ohne Root unter Windows und Linux mit Attrappen (Wegwerf-Ordner, falsche Gegenstellen, ein Schritt-Läufer, der nichts
startet). Teile, die Linux brauchen (Unix-Socket, SO_PEERCRED, flock, echte Prozesse), werden unter Windows übersprungen.
Der echte Test auf Sokrates-2 ist infra/sokrates2/anker_rauchtest.py.

Ausführen:  uv run python -m pytest -q test_anker.py   (im Ordner voice-core/)
"""

import ast
import contextlib
import errno
import hashlib
import importlib.util
import io
import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
import unittest
from pathlib import Path
from unittest import mock

INFRA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2"
LINUX = sys.platform.startswith("linux")
POSIX = os.name == "posix"


def _load(name: str, datei: str):
    spec = importlib.util.spec_from_file_location(name, INFRA / datei)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


A = _load("wamo_anker", "anker/wamo_anker.py")
UIDS = {"root": 0, "wamocon": 1000, "wamo-release": 2000}
UUID = "123e4567-e89b-12d3-a456-426614174000"
GUT_UI = {"version": "v0.1.0", "commit": "a" * 40, "ziel": "blau", "seit": "2026-10-22T08:00:00Z"}
NEU_UI = {"version": "v0.1.1", "commit": "b" * 40, "ziel": "gruen", "seit": "2026-10-22T09:00:00Z"}
GUT_KERN = {"version": "v0.1.0", "commit": "c" * 40, "ziel": "a", "seit": "2026-10-22T08:00:00Z"}
NEU_KERN = {"version": "v0.1.1", "commit": "d" * 40, "ziel": "b", "seit": "2026-10-22T09:00:00Z"}


class _Platte:
    """Ersetzt os im Modul: schreiben scheitert mit ENOSPC, wenn wenn(daten) wahr ist (volle Platte)."""

    def __init__(self, wenn):
        self.wenn = wenn

    def __getattr__(self, name):
        return getattr(os, name)

    def write(self, fd, daten):
        if self.wenn(daten):
            raise OSError(errno.ENOSPC, "No space left on device")
        return os.write(fd, daten)


def platte_voll(wenn):
    return mock.patch.object(A, "os", _Platte(wenn))


class AnkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="anker-")
        self.addCleanup(self.tmp.cleanup)
        self.p = A.Pfade.unter(self.tmp.name)
        for ordner in (self.p.lib, self.p.konfig, self.p.zustand, self.p.log, self.p.lauf, self.p.schritte):
            os.makedirs(ordner, exist_ok=True)
            os.chmod(ordner, 0o755)
        os.chmod(self.p.zustand, 0o700)
        self.zeit = [1_800_000_000.0]
        self.aufrufe = []
        self.schritt_ergebnis = (0, b"ok")
        self.zaehler = 0
        self.a = self.neuer_anker()

    def neuer_anker(self, **kw):
        kw.setdefault("ausfuehren", self._ausfuehren)
        kw.setdefault("uhr", lambda: self.zeit[0])
        kw.setdefault("aufloesen", UIDS.get)
        return A.Anker(self.p, **kw)

    def _ausfuehren(self, argv, zeitlimit):
        self.aufrufe.append((list(argv), zeitlimit))
        if isinstance(self.schritt_ergebnis, Exception):
            raise self.schritt_ergebnis
        return self.schritt_ergebnis

    # --- Hilfen
    def zeile(self, befehl, id=None):
        self.zaehler += 1
        return f"{id or format(self.zaehler, '016x')} {befehl}\n".encode("ascii")

    def sende(self, befehl, name="wamocon", id=None):
        return self.a.bearbeite(self.zeile(befehl, id), None, A.Peer(100, UIDS[name], UIDS[name]), name)

    def sende_roh(self, roh, name="wamocon"):
        return self.a.bearbeite(roh, None, A.Peer(100, UIDS[name], UIDS[name]), name)

    def audit(self):
        pfad = self.p.audit
        if not os.path.exists(pfad):
            return []
        with open(pfad, "rb") as f:
            return [json.loads(z) for z in f.read().split(b"\n") if z]

    def stand_setzen(self, **kw):
        stand = A.leerer_stand()
        for k, v in kw.items():
            komp, feld = k.split("_", 1)
            stand["komponenten"][komp][feld] = v
        A.stand_schreiben(self.p.stand, stand)

    def lies(self, pfad):
        with open(pfad, "rb") as f:
            return f.read()

    def schritte_anlegen(self, namen=("rueckweg-ui", "rueckweg-kern"), inhalt=b"#!/bin/sh\nexit 0\n"):
        for n in namen:
            pfad = os.path.join(self.p.schritte, n)
            with open(pfad, "wb") as f:
                f.write(inhalt)
            os.chmod(pfad, 0o755)
        self.manifest_schreiben()

    def manifest_schreiben(self):
        zeilen = []
        for wurzel, _, dateien in os.walk(self.p.lib):
            for d in dateien:
                if d == "SHA256SUMS":
                    continue
                voll = os.path.join(wurzel, d)
                rel = os.path.relpath(voll, self.p.lib).replace(os.sep, "/")
                zeilen.append(f"{hashlib.sha256(self.lies(voll)).hexdigest()}  ./{rel}")
        with open(self.p.manifest, "w", encoding="ascii", newline="\n") as f:
            f.write("\n".join(sorted(zeilen)) + ("\n" if zeilen else ""))
        os.chmod(self.p.manifest, 0o644)

    def stderr(self):
        return contextlib.redirect_stderr(io.StringIO())


# ================================================================ Eingabe
class EingabeTests(AnkerTest):
    ID = "0123456789abcdef"

    def pruefe(self, roh, code):
        with self.assertRaises(A.Abgelehnt, msg=repr(roh)) as ctx:
            A.parse_zeile(roh)
        self.assertEqual(ctx.exception.code, code, repr(roh))

    def test_gueltige_anfragen(self):
        faelle = {
            "status": ("status", None, False),
            "rueckweg ui": ("rueckweg", "ui", False),
            "rueckweg kern --trocken": ("rueckweg", "kern", True),
            "rueckweg alles": ("rueckweg", "alles", False),
            "sperre an": ("sperre", "an", False),
            "sperre aus --trocken": ("sperre", "aus", True),
            "pruefe-sonder 42": ("pruefe-sonder", "42", False),
            f"pruefe-sonder {UUID}": ("pruefe-sonder", UUID, False),
            "umschalten 7": ("umschalten", "7", False),
            f"migrieren {UUID}": ("migrieren", UUID, False),
            "bereitstellen 7 --trocken": ("bereitstellen", "7", True),
        }
        for text, (befehl, arg, trocken) in faelle.items():
            with self.subTest(text=text):
                anf = A.parse_zeile(f"{self.ID} {text}\n".encode())
                self.assertEqual((anf.befehl, anf.arg, anf.trocken), (befehl, arg, trocken))

    def test_zu_lang(self):
        self.pruefe(b"0" * 161, "zeile_zu_lang")
        self.pruefe((self.ID + " status" + " " * 200 + "\n").encode(), "zeile_zu_lang")
        self.pruefe(("0123456789abcdef " + "a" * 200 + "\n").encode(), "zeile_zu_lang")

    def test_unbekannte_befehle(self):
        for befehl in ("reboot", "shutdown", "rm", "bash", "sh", "docker", "sudo", "exec", "klassifiziere", "bewaehrung",
                       "statusx", "stat", "rueckwegs"):
            with self.subTest(befehl=befehl):
                self.pruefe(f"{self.ID} {befehl}\n".encode(), "befehl_unbekannt")

    def test_pfadtricks(self):
        for text in ("rueckweg ../ui", "rueckweg ui/../kern", "rueckweg /etc/passwd", "rueckweg .", "rueckweg ..",
                     "rueckweg ./ui", "pruefe-sonder ../../etc/shadow", "pruefe-sonder ....", "rueckweg ..%2f",
                     "rueckweg ui%2e%2e", "rueckweg ..\\ui", "rueckweg ~", "rueckweg ui/", "sperre /etc/wamo-anker",
                     "../../bin/sh", "status ../x"):
            with self.subTest(text=text):
                with self.assertRaises(A.Abgelehnt):
                    A.parse_zeile(f"{self.ID} {text}\n".encode())

    def test_nullbytes(self):
        for roh in (f"{self.ID} status\x00\n", f"{self.ID}\x00 status\n", f"\x00{self.ID} status\n",
                    f"{self.ID} rueckweg ui\x00reboot\n", "\x00\n"):
            with self.subTest(roh=roh):
                self.pruefe(roh.encode(), "zeichen")

    def test_steuerzeichen(self):
        for zeichen in ("\x01", "\x07", "\x08", "\t", "\r", "\x0b", "\x0c", "\x1b", "\x7f", "\x1b[31m", "\n"):
            with self.subTest(zeichen=repr(zeichen)):
                self.pruefe(f"{self.ID} status{zeichen}\n".encode(), "zeichen")
                self.pruefe(f"{self.ID}{zeichen} status\n".encode(), "zeichen")
        self.pruefe(f"{self.ID} status\r\n".encode(), "zeichen")

    def test_bidi_und_unicode(self):
        punkte = (0x202E, 0x202D, 0x2066, 0x2067, 0x2069, 0x200F, 0x200E, 0x061C, 0x200B, 0xFEFF,
                  0x00A0, 0x2028, 0x2029, 0x0430, 0x00FC, 0xFF53)  # Bidi, unsichtbar, Leerzeichen, Homoglyphe, Umlaut, Vollbreite
        for punkt in punkte:
            z = chr(punkt)
            with self.subTest(zeichen=f"U+{punkt:04X}"):
                self.pruefe(f"{self.ID} st{z}atus\n".encode(), "zeichen")
                self.pruefe(f"{self.ID} {z}status\n".encode(), "zeichen")
        self.pruefe(f"{self.ID} {chr(0xFF53)}tatus\n".encode(), "zeichen")  # Vollbreite statt ASCII
        self.pruefe(b"\xff\xfe" + self.ID.encode() + b" status\n", "zeichen")  # kein gültiges UTF-8

    def test_einschleusen(self):
        for text in ("status; reboot", "status && id", "status | cat", "status > /etc/x", "rueckweg ui $(id)",
                     "rueckweg `id`", "rueckweg ui; reboot", "rueckweg ui & id", "rueckweg 'ui'", 'rueckweg "ui"',
                     "rueckweg ui\\", "rueckweg ${HOME}", "sperre an,aus", "sperre an*", "sperre a?"):
            with self.subTest(text=text):
                with self.assertRaises(A.Abgelehnt):
                    A.parse_zeile(f"{self.ID} {text}\n".encode())

    def test_zwei_zeilen_in_einer_anfrage(self):
        self.pruefe(f"{self.ID} status\n{self.ID} sperre aus\n".encode(), "zeichen")

    def test_form(self):
        for text in ("STATUS", "Status", "sperre AN", "rueckweg UI", " status", "status ", "status  ", "rueckweg  ui",
                     "rueckweg ui ", "", " ", "-status", "sperre -", "rueckweg ui kern"):
            with self.subTest(text=text):
                with self.assertRaises(A.Abgelehnt):
                    A.parse_zeile(f"{self.ID} {text}\n".encode())

    def test_zeilenende(self):
        self.pruefe(f"{self.ID} status".encode(), "kein_zeilenende")
        self.pruefe(b"", "kein_zeilenende")
        self.pruefe(b"\n", "form")

    def test_kennung(self):
        for kennung in ("", "0123456789abcde", "0123456789ABCDEF", "g123456789abcdef", "0" * 33, "01234567-89abcdef",
                        "../../etc/passwd", "0123456789abcdef0"[:15] + "/"):
            with self.subTest(kennung=kennung):
                with self.assertRaises(A.Abgelehnt):
                    A.parse_zeile(f"{kennung} status\n".encode())
        A.parse_zeile(("0" * 32 + " status\n").encode())

    def test_trocken_nur_wo_erlaubt(self):
        self.pruefe(f"{self.ID} status --trocken\n".encode(), "trocken")
        self.pruefe(f"{self.ID} pruefe-sonder 42 --trocken\n".encode(), "trocken")
        for text in ("rueckweg --trocken", "rueckweg ui --trocken --trocken", "rueckweg ui -trocken", "rueckweg ui --TROCKEN",
                     "rueckweg ui--trocken", "sperre --trocken an", "rueckweg --trocken ui", "sperre an --trocken x"):
            with self.subTest(text=text):
                with self.assertRaises(A.Abgelehnt):
                    A.parse_zeile(f"{self.ID} {text}\n".encode())

    def test_argumente_pflicht_oder_verboten(self):
        for text in ("status ui", "sperre", "sperre ein", "rueckweg", "rueckweg ui kern", "rueckweg server", "pruefe-sonder",
                     "pruefe-sonder abc", "pruefe-sonder 0", "pruefe-sonder 1234567890123456789", "umschalten ui", "umschalten", "migrieren", "bereitstellen x", "migrieren 0"):
            with self.subTest(text=text):
                with self.assertRaises(A.Abgelehnt):
                    A.parse_zeile(f"{self.ID} {text}\n".encode())

    def test_abgelehntes_hat_keine_wirkung(self):
        """Nach einer Serie von Verbotsproben sind Zustand, Sperre und Schritt-Aufrufe unberührt."""
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        vorher = self.lies(self.p.stand)
        for roh in (b"x" * 500, b"", b"\x00" * 10, f"{self.ID} reboot\n".encode(), f"{self.ID} rueckweg ../ui\n".encode(),
                    f"{self.ID} status\x07\n".encode(), f"0123456789abcdef st{chr(0x202E)}atus\n".encode()):
            antwort = self.sende_roh(roh)
            self.assertFalse(antwort["ok"])
            self.assertTrue(antwort["abgelehnt"])
        self.assertEqual(self.lies(self.p.stand), vorher)
        self.assertFalse(os.path.exists(self.p.sperre))
        self.assertEqual(self.aufrufe, [])
        self.assertTrue(all(z["ereignis"] == "abgelehnt" for z in self.audit()))


# ================================================================ Gegenstelle und Rechte
class RechteTests(AnkerTest):
    def test_erlaubte_benutzer_sind_fest(self):
        self.assertEqual(set(A.ERLAUBT), {"root", "wamocon", "wamo-release"})
        for fremd in ("wamo", "wamo-core", "wamo-geraete", "wamo-status", "wamo-t-01", "nobody", "www-data", "postgres"):
            self.assertNotIn(fremd, A.ERLAUBT)

    def test_fremde_uid_wird_abgewiesen(self):
        for uid in (1, 33, 999, 1001, 65534, 2 ** 31 + 5, 4294967294):  # auch uids über 2**31 (vorzeichenlos)
            with self.subTest(uid=uid):
                peer = A.Peer(5, uid, uid)
                self.assertIsNone(self.a.benutzer(peer))
                antwort = self.a.abweisen(peer)
                self.assertEqual(antwort["grund"], "benutzer")
                self.assertFalse(antwort["ok"])
        self.assertIsNone(self.a.benutzer(None))
        self.assertEqual(self.a.abweisen(None)["grund"], "benutzer")  # SO_PEERCRED nicht lesbar: nicht vertrauenswürdig

    def test_so_peercred_wird_vorzeichenlos_gelesen(self):
        class Sock:
            def getsockopt(self, *args):
                return struct.pack("iII", 77, 4294967294, 3000000000)

        class Kaputt:
            def getsockopt(self, *args):
                raise OSError(errno.ENOTSOCK, "kein Socket")

        with mock.patch.object(socket, "SO_PEERCRED", 17, create=True):  # unter Windows gibt es die Konstante nicht
            peer = A.peer_von(Sock())
            self.assertEqual((peer.pid, peer.uid, peer.gid), (77, 4294967294, 3000000000))
            self.assertIsNone(A.peer_von(Kaputt()))
            self.assertIsNone(A.peer_von(object()))  # kein getsockopt: nicht vertrauenswürdig

    def test_benutzer_wird_frisch_aufgeloest(self):
        zuordnung = {"wamocon": 1000}
        a = self.neuer_anker(aufloesen=lambda n: zuordnung.get(n))
        self.assertEqual(a.benutzer(A.Peer(1, 1000, 1000)), "wamocon")
        zuordnung["wamocon"] = 1500  # uid-Wechsel (Konto neu angelegt): die alte uid gilt nicht mehr
        self.assertIsNone(a.benutzer(A.Peer(1, 1000, 1000)))
        self.assertEqual(a.benutzer(A.Peer(1, 1500, 1500)), "wamocon")

    def test_fehlender_benutzer_passt_auf_niemanden(self):
        a = self.neuer_anker(aufloesen=lambda n: None)
        for uid in (0, 1000, 2000, -1):
            self.assertIsNone(a.benutzer(A.Peer(1, uid, uid)))

    def test_name_muss_zur_uid_passen(self):
        antwort = self.a.bearbeite(self.zeile("status"), None, A.Peer(1, 1234, 1234), "wamocon")
        self.assertEqual(antwort["grund"], "benutzer")
        antwort = self.a.bearbeite(self.zeile("status"), None, A.Peer(1, 1000, 1000), "root")
        self.assertEqual(antwort["grund"], "benutzer")

    def test_wamo_release_darf_die_sperre_nie_loesen(self):
        self.assertTrue(self.sende("sperre an", "wamo-release")["ok"])
        antwort = self.sende("sperre aus", "wamo-release")
        self.assertEqual(antwort["grund"], "recht")
        self.assertEqual(A.sperre_zustand(self.p), "an")
        self.assertTrue(self.sende("sperre aus", "wamocon")["ok"])
        self.assertEqual(A.sperre_zustand(self.p), "aus")

    def test_root_und_wamocon_duerfen_alles(self):
        alle = {"sperre:an", "sperre:aus"} | {n for n in A.BEFEHLE if n != "sperre"}  # unabhängig aus der Tabelle abgeleitet
        self.assertEqual(set(A.ERLAUBT["root"]), alle)
        self.assertEqual(set(A.ERLAUBT["wamocon"]), alle)
        # lesen, zurückrollen, sperren, die (immer ablehnende) Prüfung der Sonderfreigabe und die Vorwärtsbefehle (die Sperre hält sie an)
        self.assertEqual(set(A.ERLAUBT["wamo-release"]), {"status", "rueckweg", "sperre:an", "pruefe-sonder", "bereitstellen", "migrieren", "umschalten"})

    def test_jeder_fehlercode_hat_einen_text(self):
        """Ein fehlender Text löste früher einen KeyError im Fehlerzweig aus und ließ den Client ohne Antwort."""
        codes = set()
        for knoten in ast.walk(ast.parse((INFRA / "anker" / "wamo_anker.py").read_text(encoding="utf-8"))):
            if isinstance(knoten, ast.Call):
                name = getattr(knoten.func, "id", None) or getattr(knoten.func, "attr", None)
                args = knoten.args[1:2] if name == "_antwort" else knoten.args[:1] if name in {
                    "Abgelehnt", "AnkerFehler", "SchrittFehler", "_ablehnung"} else []
                if name == "_ablehnung":
                    args = knoten.args[2:3]
                codes |= {a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
        self.assertGreater(len(codes), 15)
        self.assertEqual(sorted(c for c in codes if c not in A.GRUENDE), [])
        self.assertEqual(A.Anker._antwort(False, "gibt_es_nicht")["grund"], "gibt_es_nicht")  # und ohne Text kein Absturz

    def test_ablehnung_wird_protokolliert_ohne_zu_lesen(self):
        self.a.abweisen(A.Peer(777, 4242, 4242))
        zeilen = self.audit()
        self.assertEqual(len(zeilen), 1)
        self.assertEqual((zeilen[0]["ereignis"], zeilen[0]["grund"], zeilen[0]["uid"], zeilen[0]["pid"]),
                         ("abgelehnt", "benutzer", 4242, 777))
        self.assertNotIn("benutzer", zeilen[0])

    def test_ablehnungen_sind_begrenzt(self):
        for _ in range(A.ABLEHN_MAX + 30):
            self.a.abweisen(A.Peer(5, 4242, 4242))
        self.assertEqual(len(self.audit()), A.ABLEHN_MAX)  # der Rest wird nur gezählt, das Protokoll füllt sich nicht
        self.zeit[0] += A.ABLEHN_FENSTER_S + 1
        self.a.abweisen(A.Peer(5, 4242, 4242))
        letzte = self.audit()[-1]
        self.assertEqual(letzte["unterdrueckt"], 30)
        self.assertTrue(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])


# ================================================================ doppelte Anfragen
class DoppeltTests(AnkerTest):
    def test_gleiche_kennung_nur_einmal(self):
        self.assertTrue(self.sende("status", id="a" * 16)["ok"])
        zweite = self.sende("status", id="a" * 16)
        self.assertEqual(zweite["grund"], "doppelt")
        auch_anderer_befehl = self.sende("sperre an", id="a" * 16)
        self.assertEqual(auch_anderer_befehl["grund"], "doppelt")
        self.assertFalse(os.path.exists(self.p.sperre))

    def test_doppelte_schreibende_anfrage_wirkt_nur_einmal(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        self.assertTrue(self.sende("rueckweg ui", id="b" * 16)["ok"])
        self.assertEqual(self.sende("rueckweg ui", id="b" * 16)["grund"], "doppelt")
        self.assertEqual(len(self.aufrufe), 1)

    def test_andere_kennung_ist_keine_doppelte_anfrage(self):
        self.assertTrue(self.sende("status")["ok"])
        self.assertTrue(self.sende("status")["ok"])

    def test_gedaechtnis_ist_begrenzt(self):
        with mock.patch.object(A, "IDS_MAX", 5):
            for i in range(8):
                self.sende("status", id=format(i + 1, "016x"))
            self.assertEqual(len(self.a._ids), 5)
            self.assertEqual(self.sende("status", id=format(8, "016x"))["grund"], "doppelt")


# ================================================================ Audit-Protokoll und Hashkette
class AuditTests(AnkerTest):
    def kette(self, n=6):
        audit = A.Audit(self.p.audit, lambda: self.zeit[0], self.p.vertrauens_uid)
        for i in range(1, n + 1):
            audit.anhaengen(f"e{i}", befehl="status", id=format(i, "016x"))
        return audit

    def zeilen_roh(self):
        return self.lies(self.p.audit).split(b"\n")[:-1]

    def schreiben(self, zeilen):
        with open(self.p.audit, "wb") as f:
            f.write(b"\n".join(zeilen) + (b"\n" if zeilen else b""))
        os.chmod(self.p.audit, 0o600)

    def test_kette_ist_gueltig(self):
        self.kette()
        ok, n, letzter, grund = A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)
        self.assertEqual((ok, n, grund), (True, 6, None))
        self.assertEqual(len(letzter), 64)
        zeilen = self.audit()
        self.assertEqual([z["n"] for z in zeilen], [1, 2, 3, 4, 5, 6])
        self.assertEqual(zeilen[0]["vorher"], A.GENESIS)
        self.assertEqual(zeilen[1]["vorher"], zeilen[0]["hash"])

    def test_fehlende_datei_ist_eine_leere_kette(self):
        self.assertEqual(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid), (True, 0, A.GENESIS, None))

    def test_manipulation_wird_erkannt(self):
        def aendern(zeilen):
            zeilen[2] = zeilen[2].replace(b'"ereignis":"e3"', b'"ereignis":"e9"')

        def loeschen(zeilen):
            del zeilen[2]

        def vertauschen(zeilen):
            zeilen[1], zeilen[2] = zeilen[2], zeilen[1]

        def verdoppeln(zeilen):
            zeilen.append(zeilen[1])

        def fremde_zeile(zeilen):
            zeilen.append(b"{}")

        def leere_zeile(zeilen):
            zeilen.insert(2, b"")

        def nicht_ascii(zeilen):
            zeilen[3] = zeilen[3].replace(b'"status"', '"statüs"'.encode("utf-8"))

        def zu_lang(zeilen):
            zeilen[4] = zeilen[4][:-1] + b',"x":"' + b"a" * 1100 + b'"}'

        def hash_neu_berechnet(zeilen):  # Fälscher rechnet nur die eigene Zeile neu, die nächste verrät es
            obj = json.loads(zeilen[1])
            obj.pop("hash")
            obj["ereignis"] = "gefaelscht"
            zeilen[1] = A._zeile_bauen(obj).rstrip(b"\n")

        faelle = {"aendern": aendern, "loeschen": loeschen, "vertauschen": vertauschen, "verdoppeln": verdoppeln,
                  "fremde_zeile": fremde_zeile, "leere_zeile": leere_zeile, "nicht_ascii": nicht_ascii,
                  "zu_lang": zu_lang, "hash_neu_berechnet": hash_neu_berechnet}
        for name, aender in faelle.items():
            with self.subTest(fall=name):
                if os.path.exists(self.p.audit):
                    os.unlink(self.p.audit)
                self.kette()
                zeilen = self.zeilen_roh()
                aender(zeilen)
                self.schreiben(zeilen)
                ok, _, _, grund = A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)
                self.assertFalse(ok, f"{name} blieb unbemerkt")
                self.assertTrue(grund)

    def test_abgeschnittene_letzte_zeile_wird_erkannt_und_nicht_weitergeschrieben(self):
        audit = self.kette()
        daten = self.lies(self.p.audit)
        with open(self.p.audit, "wb") as f:
            f.write(daten[:-10])
        self.assertFalse(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])
        with self.assertRaises(A.AuditFehler):
            audit.anhaengen("weiter")
        self.assertEqual(self.lies(self.p.audit), daten[:-10])

    def test_veraenderte_letzte_zeile_blockiert_das_anhaengen(self):
        audit = self.kette()
        zeilen = self.zeilen_roh()
        zeilen[-1] = zeilen[-1].replace(b'"e6"', b'"e7"')
        self.schreiben(zeilen)
        with self.assertRaises(A.AuditFehler):
            audit.anhaengen("weiter")

    def test_manipulierte_kette_stoppt_schreibende_befehle_nicht_den_status(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        self.sende("status")
        zeilen = self.zeilen_roh()
        zeilen[-1] = zeilen[-1].replace(b'"ergebnis"', b'"ergebnix"')
        self.schreiben(zeilen)
        with self.stderr():
            antwort = self.sende("rueckweg ui")
            status = self.sende("status")
        self.assertEqual(antwort["grund"], "audit_nicht_schreibbar")
        self.assertEqual(self.aufrufe, [])
        self.assertTrue(status["ok"])
        self.assertEqual(status["audit"]["kette"], "defekt")

    def test_zeilenlaenge_ist_begrenzt(self):
        audit = A.Audit(self.p.audit, lambda: self.zeit[0], self.p.vertrauens_uid)
        audit.anhaengen("ok", befehl="status")
        with self.assertRaises(A.AuditFehler):
            audit.anhaengen("zu_gross", befehl="x" * 3000)
        roh = self.zeilen_roh()
        self.assertEqual(len(roh), 1)  # die zu lange Zeile wurde nicht angehängt
        self.assertLessEqual(len(roh[0]), A.AUDIT_ZEILE_MAX)

    def test_zu_lange_letzte_zeile_blockiert_das_anhaengen(self):
        with open(self.p.audit, "wb") as f:
            f.write(b"a" * 3000 + b"\n")
        os.chmod(self.p.audit, 0o600)
        with self.assertRaisesRegex(A.AuditFehler, "zu lang"):
            A.Audit(self.p.audit, lambda: 1.0, self.p.vertrauens_uid).anhaengen("x")
        self.assertFalse(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])

    def test_zwei_schreiber_halten_die_kette(self):
        a1 = A.Audit(self.p.audit, lambda: 1.0, self.p.vertrauens_uid)
        a2 = A.Audit(self.p.audit, lambda: 2.0, self.p.vertrauens_uid)
        for i in range(10):
            (a1 if i % 2 else a2).anhaengen("x", i=i)
        self.assertEqual(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[:2], (True, 10))

    @unittest.skipUnless(LINUX, "flock nur unter Linux")
    def test_gleichzeitige_schreiber_halten_die_kette(self):
        fehler = []

        def schreiben(nr):
            audit = A.Audit(self.p.audit, lambda: 1.0, self.p.vertrauens_uid)
            try:
                for i in range(40):
                    audit.anhaengen("x", schreiber=nr, i=i)
            except A.AuditFehler as e:
                fehler.append(str(e))

        faeden = [threading.Thread(target=schreiben, args=(n,)) for n in range(4)]
        for f in faeden:
            f.start()
        for f in faeden:
            f.join(60)
        self.assertEqual(fehler, [])
        self.assertEqual(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[:2], (True, 160))

    def test_zu_wenig_freier_platz_haengt_nichts_an(self):
        audit = A.Audit(self.p.audit, lambda: 1.0, self.p.vertrauens_uid)
        audit.anhaengen("vorher")
        groesse = os.path.getsize(self.p.audit)

        class Knapp:
            f_bavail, f_frsize = 100, 4096  # 400 KB frei

        with mock.patch.object(A.os, "statvfs", lambda pfad: Knapp, create=True):
            with self.assertRaisesRegex(A.AuditFehler, "Platz"):
                audit.anhaengen("zu_knapp")
        self.assertEqual(os.path.getsize(self.p.audit), groesse)  # keine halbe Zeile

    def test_nie_ueberschreiben(self):
        self.kette(3)
        vorher = self.lies(self.p.audit)
        self.kette(3)  # zweites Anhängen mit neuer Instanz
        nachher = self.lies(self.p.audit)
        self.assertTrue(nachher.startswith(vorher))
        self.assertEqual(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[:2], (True, 6))

    def test_keine_geheimnisse_im_protokoll(self):
        geheim = "sk-" + "ant-oat01-" + "GEHEIM" * 3  # zusammengesetzt, damit kein Geheimnismuster im Quelltext steht
        self.sende_roh(f"0123456789abcdef rueckweg {geheim}\n".encode())
        self.sende_roh(f"0123456789abcdef status {geheim} --trocken\n".encode())
        self.sende_roh((geheim * 20).encode())
        roh = self.lies(self.p.audit)
        self.assertNotIn(b"GEHEIM", roh)
        self.assertNotIn(b"sk-ant", roh)
        for z in self.audit():
            self.assertIn("roh_sha", z)
            self.assertLessEqual(len(z["roh_sha"]), 16)

    def test_symbolischer_link_wird_nicht_verfolgt(self):
        ziel = os.path.join(self.tmp.name, "woanders.jsonl")
        with open(ziel, "wb"):
            pass
        try:
            os.symlink(ziel, self.p.audit)
        except (OSError, NotImplementedError):
            self.skipTest("Links nicht möglich")
        audit = A.Audit(self.p.audit, lambda: 1.0, self.p.vertrauens_uid)
        with self.assertRaises(A.AuditFehler):
            audit.anhaengen("x")
        self.assertEqual(os.path.getsize(ziel), 0)
        self.assertFalse(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])

    @unittest.skipUnless(POSIX and hasattr(os, "geteuid") and os.geteuid() != 0, "Rechte nur als normaler Benutzer prüfbar")
    def test_zu_offene_rechte_sind_ein_fehler(self):
        self.kette(2)
        os.chmod(self.p.audit, 0o644)
        self.assertFalse(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])
        os.chmod(self.p.audit, 0o600)
        os.chmod(self.p.log, 0o777)
        with self.assertRaises(A.AuditFehler):
            A.Audit(self.p.audit, lambda: 1.0, self.p.vertrauens_uid).anhaengen("x")

    def fuellen(self, anteil, grenze):
        """Füllt das Protokoll mit Zeilen, bis es mindestens anteil der (gesenkten) Höchstgröße hat."""
        while os.path.getsize(self.p.audit) < anteil * grenze:
            self.a.audit.anhaengen("fuellen", 1.0, x="y" * 40)

    def test_klassen_des_fuellgrads_halten_den_rueckweg_offen(self):
        """Lesen bis 75, Sperre bis 90, ein echter Rückweg bis 100 Prozent; die Datei wächst nie über die Höchstgröße."""
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        grenze = 9000
        with mock.patch.object(A, "AUDIT_DATEI_MAX", grenze), self.stderr():
            self.a.audit.anhaengen("start", 1.0)
            self.fuellen(0.76, grenze)
            groesse = os.path.getsize(self.p.audit)
            self.assertTrue(self.sende("status")["ok"])  # ab 75 Prozent beantwortet, aber nicht mehr protokolliert
            self.assertEqual(os.path.getsize(self.p.audit), groesse)
            self.assertTrue(self.sende("sperre an")["ok"])  # ändert etwas: Klasse Sperre, bis 90 Prozent
            self.assertGreater(os.path.getsize(self.p.audit), groesse)
            self.fuellen(0.91, grenze)
            self.assertEqual(self.sende("sperre aus")["grund"], "audit_nicht_schreibbar")  # ab 90 Prozent nicht mehr
            self.assertEqual(A.sperre_zustand(self.p), "an")
            self.assertTrue(self.sende("rueckweg ui")["ok"])  # der echte Rückweg darf bis 100 Prozent
            while True:
                try:
                    self.a.audit.anhaengen("fuellen", 1.0, x="y" * 40)
                except A.AuditFehler:
                    break
            self.assertLessEqual(os.path.getsize(self.p.audit), grenze)  # nie über die Höchstgröße hinaus
            self.assertEqual(self.sende("rueckweg ui")["grund"], "audit_nicht_schreibbar")  # voll: nichts ohne Protokollzeile
            self.assertEqual(self.sende("status")["audit"]["kette"], "ok")  # die Datei ist trotz Grenze lesbar und gültig

    def test_dauerfeuer_ohne_wirkung_fuellt_das_protokoll_nicht(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        self.assertTrue(self.sende("sperre an")["ok"])
        for _ in range(300):
            self.sende("sperre an")  # schon gesetzt: keine Wirkung
            self.sende("status")
        zeilen = self.audit()
        self.assertLessEqual(len(zeilen), 2 + 2 * 2 * A.ABLEHN_MAX)  # begrenzt, der Rest wird nur gezählt
        self.assertTrue(self.sende("rueckweg ui")["ok"])  # der Rückweg ist weiter protokolliert und möglich
        self.assertEqual(self.audit()[-1]["ereignis"], "ergebnis")
        self.assertTrue(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])

    def test_aendernde_anfragen_im_dauerfeuer_werden_gedrosselt(self):
        antworten = [self.sende("sperre an" if i % 2 == 0 else "sperre aus").get("grund") for i in range(A.AENDERN_MAX + 10)]
        self.assertIn("zu_viele", antworten)
        # nach 30 Wechseln steht die Sperre auf "aus": jedes zweite weitere "sperre an" wäre ändernd und wird gedrosselt,
        # jedes zweite "sperre aus" ändert nichts und fällt unter die Anfragen ohne Wirkung
        self.assertEqual(antworten.count("zu_viele"), 5)
        self.zeit[0] += A.ABLEHN_FENSTER_S + 1  # neues Fenster: es geht wieder
        self.assertIsNone(self.sende("sperre an").get("grund"))
        self.assertTrue(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])

    def test_abweisungen_zaehlen_je_grund(self):
        """Rauschen aus ungültigen Zeilen darf einen Rechteverstoß nicht aus dem Protokoll verdrängen."""
        for _ in range(A.ABLEHN_MAX + 40):
            self.sende_roh(b"unsinn\n", "wamo-release")
        self.assertEqual(self.sende("sperre aus", "wamo-release")["grund"], "recht")
        gruende = [z.get("grund") for z in self.audit() if z["ereignis"] == "abgelehnt"]
        self.assertEqual(gruende.count("kein_zeilenende") + gruende.count("form"), A.ABLEHN_MAX)
        self.assertIn("recht", gruende)


# ================================================================ volle Platte
class VollePlatteTests(AnkerTest):
    def test_audit_voll_schreibende_befehle_ändern_nichts(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        stand = self.lies(self.p.stand)
        with platte_voll(lambda d: b'"ereignis"' in d), self.stderr():
            rueckweg = self.sende("rueckweg ui")
            sperre = self.sende("sperre an")
            status = self.sende("status")
        self.assertEqual(rueckweg["grund"], "audit_nicht_schreibbar")
        self.assertEqual(sperre["grund"], "audit_nicht_schreibbar")
        self.assertEqual(self.aufrufe, [])
        self.assertEqual(self.lies(self.p.stand), stand)
        self.assertFalse(os.path.exists(self.p.sperre))
        self.assertTrue(status["ok"])  # Lesen bleibt möglich

    def test_zustand_voll_alte_datei_bleibt_heil(self):
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        vorher = self.lies(self.p.stand)
        stand = A.stand_laden(self.p.stand, self.p.vertrauens_uid)
        stand["komponenten"]["ui"]["aktiv"] = GUT_UI
        with platte_voll(lambda d: b'"schema"' in d):
            with self.assertRaises(OSError):
                A.stand_schreiben(self.p.stand, stand)
        self.assertEqual(self.lies(self.p.stand), vorher)
        self.assertEqual(sorted(os.listdir(self.p.zustand)), ["stand.json"])  # keine Reste
        A.stand_laden(self.p.stand, self.p.vertrauens_uid)

    def test_rueckweg_mit_vollem_zustand_meldet_den_unterschied(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        vorher = self.lies(self.p.stand)
        with platte_voll(lambda d: b'"schema"' in d):
            antwort = self.sende("rueckweg ui")
        teil = antwort["teile"][0]
        self.assertFalse(antwort["ok"])
        self.assertEqual((teil["grund"], teil["geaendert"]), ("zustand_nicht_schreibbar", True))  # der Mensch muss es wissen
        self.assertEqual(self.lies(self.p.stand), vorher)

    def test_sperre_setzt_sich_auch_bei_voller_platte(self):
        with platte_voll(lambda d: b"gesperrt seit" in d):
            self.assertTrue(A.sperre_setzen(self.p, "wamocon", 1.0))
        self.assertEqual(A.sperre_zustand(self.p), "an")  # schon das Vorhandensein sperrt

    def test_erste_audit_zeile_bei_voller_platte(self):
        with platte_voll(lambda d: True), self.stderr():
            self.assertEqual(self.sende("sperre an")["grund"], "audit_nicht_schreibbar")
        self.assertEqual(A.sperre_zustand(self.p), "aus")


# ================================================================ Zustandsdatei
class ZustandTests(AnkerTest):
    def geladen(self):
        return A.stand_laden(self.p.stand, self.p.vertrauens_uid)

    def roh_schreiben(self, daten):
        with open(self.p.stand, "wb") as f:
            f.write(daten)
        os.chmod(self.p.stand, 0o600)

    def test_hin_und_zurueck(self):
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI, kern_aktiv=NEU_KERN, kern_letzte_gute=GUT_KERN)
        stand = self.geladen()
        self.assertEqual(stand["komponenten"]["ui"], {"aktiv": NEU_UI, "letzte_gute": GUT_UI})
        self.assertEqual(stand["komponenten"]["kern"]["letzte_gute"], GUT_KERN)

    def test_zerstoerte_dateien(self):
        gut = json.dumps(A.leerer_stand()).encode()
        faelle = {
            "leer": b"",
            "zufallsbytes": os.urandom(300),
            "abgeschnitten": gut[:-7],
            "kein_json": b"das ist kein json",
            "nur_nullbytes": b"\x00" * 200,
            "liste": b"[]",
            "zahl": b"42",
            "null": b"null",
            "falsches_schema": json.dumps({"schema": 2, "komponenten": A.leerer_stand()["komponenten"]}).encode(),
            "schema_bool": json.dumps({"schema": True, "komponenten": A.leerer_stand()["komponenten"]}).encode(),
            "fremder_schluessel": json.dumps({**A.leerer_stand(), "x": 1}).encode(),
            "komponente_fehlt": json.dumps({"schema": 1, "komponenten": {"ui": {"aktiv": None, "letzte_gute": None}}}).encode(),
            "komponente_zusaetzlich": json.dumps({"schema": 1, "komponenten": {
                **A.leerer_stand()["komponenten"], "db": {"aktiv": None, "letzte_gute": None}}}).encode(),
            "doppelter_schluessel": b'{"schema":1,"schema":1,"komponenten":{"ui":{"aktiv":null,"letzte_gute":null},'
                                    b'"kern":{"aktiv":null,"letzte_gute":null}}}',
            "nan": b'{"schema":NaN,"komponenten":{}}',
            "tief_verschachtelt": b"[" * 100000 + b"]" * 100000,
            "nicht_utf8": b'{"schema":1,"komponenten":"\xff\xfe"}',
            "zu_gross": b'{"schema":1,"x":"' + b"a" * (A.STAND_MAX + 10) + b'"}',
        }
        for name, daten in faelle.items():
            with self.subTest(fall=name):
                self.roh_schreiben(daten)
                with self.assertRaises(A.ZustandDefekt):
                    self.geladen()

    def test_ungueltige_eintraege(self):
        def mit(eintrag):
            stand = A.leerer_stand()
            stand["komponenten"]["ui"]["letzte_gute"] = eintrag
            self.roh_schreiben(json.dumps(stand).encode())
            with self.assertRaises(A.ZustandDefekt):
                self.geladen()

        for aenderung in ({"version": "v1.0.0; reboot"}, {"version": "1.0.0"}, {"version": "v01.0.0"}, {"version": "v1.0"},
                          {"version": "v1.0.0-rc1"}, {"commit": "a" * 39}, {"commit": "A" * 40}, {"commit": "$(id)" + "a" * 36},
                          {"commit": "a" * 40 + "\n"}, {"ziel": "rot"}, {"ziel": "a"}, {"ziel": "../blau"}, {"ziel": "blau\x00"},
                          {"seit": "gestern"}, {"seit": "2026-10-22 08:00:00"}, {"version": 1}):
            with self.subTest(aenderung=aenderung):
                mit({**GUT_UI, **aenderung})
        with self.subTest(fall="zusatzfeld"):
            mit({**GUT_UI, "befehl": "reboot"})
        with self.subTest(fall="feld_fehlt"):
            mit({k: v for k, v in GUT_UI.items() if k != "seit"})
        with self.subTest(fall="kern_ziel_fuer_ui"):
            mit(GUT_KERN)

    def test_rueckweg_mit_kaputter_datei_aendert_nichts(self):
        self.schritte_anlegen()
        for daten in (b"", b"kaputt", os.urandom(50)):
            with self.subTest(daten=daten[:8]):
                self.roh_schreiben(daten)
                antwort = self.sende("rueckweg alles")
                self.assertFalse(antwort["ok"])
                self.assertEqual(antwort["grund"], "zustand_defekt")
                self.assertEqual(self.lies(self.p.stand), daten)  # nie überschrieben
                self.assertEqual(self.aufrufe, [])

    def test_fehlende_datei(self):
        self.assertEqual(self.sende("rueckweg ui")["grund"], "zustand_defekt")
        status = self.sende("status")
        self.assertEqual(status["zustand"], "defekt")
        self.assertTrue(status["ok"])

    def test_status_meldet_defekt_ohne_abzustuerzen(self):
        self.roh_schreiben(os.urandom(80))
        status = self.sende("status")
        self.assertTrue(status["ok"])
        self.assertEqual(status["zustand"], "defekt")
        self.assertIn("zustand_grund", status)

    @unittest.skipUnless(POSIX and hasattr(os, "geteuid") and os.geteuid() != 0, "Rechte nur als normaler Benutzer prüfbar")
    def test_zu_offene_rechte(self):
        self.stand_setzen()
        os.chmod(self.p.stand, 0o644)
        with self.assertRaises(A.ZustandDefekt):
            self.geladen()
        os.chmod(self.p.stand, 0o666)
        with self.assertRaises(A.ZustandDefekt):
            self.geladen()

    def test_link_statt_datei(self):
        self.stand_setzen()
        ziel = os.path.join(self.tmp.name, "anders.json")
        os.replace(self.p.stand, ziel)
        try:
            os.symlink(ziel, self.p.stand)
        except (OSError, NotImplementedError):
            self.skipTest("Links nicht möglich")
        with self.assertRaises(A.ZustandDefekt):
            self.geladen()

    def test_ordner_statt_datei(self):
        os.mkdir(self.p.stand)
        with self.assertRaises(A.ZustandDefekt):
            self.geladen()

    def test_schreiben_ist_atomar_und_sichert_die_vorgaengerin(self):
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        erste = self.lies(self.p.stand)
        self.stand_setzen(ui_aktiv=GUT_UI, ui_letzte_gute=GUT_UI)
        self.assertEqual(self.lies(self.p.stand + ".vorher"), erste)
        self.assertEqual(sorted(os.listdir(self.p.zustand)), ["stand.json", "stand.json.vorher"])
        if POSIX:
            self.assertEqual(stat.S_IMODE(os.stat(self.p.stand).st_mode) & 0o077, 0)

    def test_ungueltiges_wird_nie_geschrieben(self):
        self.stand_setzen()
        vorher = self.lies(self.p.stand)
        stand = A.leerer_stand()
        stand["komponenten"]["ui"]["aktiv"] = {**GUT_UI, "commit": "x"}
        with self.assertRaises(A.ZustandDefekt):
            A.stand_schreiben(self.p.stand, stand)
        self.assertEqual(self.lies(self.p.stand), vorher)


# ================================================================ Sperre
class SperreTests(AnkerTest):
    VORWAERTS = ("bereitstellen", "migrieren", "umschalten")

    def test_an_und_aus(self):
        self.assertEqual(A.sperre_zustand(self.p), "aus")
        antwort = self.sende("sperre an")
        self.assertEqual((antwort["ok"], antwort["geaendert"]), (True, True))
        self.assertEqual(A.sperre_zustand(self.p), "an")
        self.assertEqual(self.sende("sperre an")["geaendert"], False)  # wiederholbar
        self.assertEqual(self.sende("status")["sperre"], "an")
        self.assertTrue(self.sende("sperre aus")["geaendert"])
        self.assertEqual(A.sperre_zustand(self.p), "aus")
        self.assertEqual(self.sende("sperre aus")["geaendert"], False)
        self.assertTrue(self.sende("sperre an")["geaendert"])
        self.assertIn("gesperrt seit", self.lies(self.p.sperre).decode())

    def test_sperre_blockiert_alle_vorwaertsbefehle(self):
        for befehl in self.VORWAERTS:
            with self.subTest(befehl=befehl, sperre="aus"):
                self.assertEqual(self.sende(befehl + " 7")["grund"], "nicht_verfuegbar")  # nicht gesperrt, aber ohne Mitspieler (vorwaerts_fabrik) nicht verfügbar
        self.sende("sperre an")
        for befehl in self.VORWAERTS:
            for name in ("root", "wamocon", "wamo-release"):
                for zusatz in ("", " --trocken"):
                    with self.subTest(befehl=befehl, name=name, zusatz=zusatz):
                        antwort = self.sende(f"{befehl} 7{zusatz}", name)
                        # alle drei dürfen die Vorwärtsbefehle senden (der Release-Dienst ruft sie später); die Sperre hält sie an
                        self.assertEqual(antwort["grund"], "gesperrt")
                        self.assertFalse(antwort["ok"])

    def test_alle_schreibenden_vorwaertsbefehle_sind_in_der_tabelle_markiert(self):
        for name, spez in A.BEFEHLE.items():
            if name in self.VORWAERTS:
                self.assertTrue(spez.vorwaerts and spez.schreibend, name)
            else:
                self.assertFalse(spez.vorwaerts, name)
        self.assertEqual({n for n, s in A.BEFEHLE.items() if s.vorwaerts}, set(self.VORWAERTS))

    def test_rueckweg_und_status_bleiben_unter_der_sperre_moeglich(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        self.sende("sperre an")
        self.assertTrue(self.sende("status")["ok"])
        self.assertTrue(self.sende("rueckweg ui")["ok"])
        self.assertEqual(len(self.aufrufe), 1)

    def test_defekte_sperre_gilt_als_gesetzt(self):
        os.mkdir(self.p.sperre)  # Ordner statt Datei
        self.assertEqual(A.sperre_zustand(self.p), "defekt")
        self.assertEqual(self.sende("umschalten 7")["grund"], "gesperrt")
        self.assertEqual(self.sende("sperre aus")["grund"], "sperre_defekt")  # wird nicht blind gelöscht
        self.assertEqual(self.sende("sperre an")["grund"], "sperre_defekt")
        self.assertTrue(os.path.isdir(self.p.sperre))

    def test_link_als_sperre_ist_defekt(self):
        ziel = os.path.join(self.tmp.name, "x")
        open(ziel, "wb").close()
        try:
            os.symlink(ziel, self.p.sperre)
        except (OSError, NotImplementedError):
            self.skipTest("Links nicht möglich")
        self.assertEqual(A.sperre_zustand(self.p), "defekt")
        self.assertEqual(self.sende("bereitstellen 7")["grund"], "gesperrt")

    def test_nicht_lesbare_sperre_gilt_als_gesetzt(self):
        class Os(_Platte):
            def lstat(self, pfad):
                if str(pfad) == self.p.sperre:
                    raise PermissionError(errno.EACCES, "kein Zugriff")
                return os.lstat(pfad)

        proxy = Os(lambda d: False)
        proxy.p = self.p
        with mock.patch.object(A, "os", proxy):
            self.assertEqual(A.sperre_zustand(self.p), "defekt")

    @unittest.skipUnless(POSIX and hasattr(os, "geteuid") and os.geteuid() != 0, "Rechte nur als normaler Benutzer prüfbar")
    def test_beschreibbarer_ordner_macht_die_sperre_unsicher(self):
        os.chmod(self.p.konfig, 0o777)
        self.assertEqual(A.sperre_zustand(self.p), "defekt")
        self.assertEqual(self.sende("migrieren 7")["grund"], "gesperrt")

    def test_sperrdatei_fehlender_ordner_gilt_als_gesetzt(self):
        os.rmdir(self.p.konfig)
        self.assertEqual(A.sperre_zustand(self.p), "defekt")

    def test_trocken_aendert_die_sperre_nicht(self):
        antwort = self.sende("sperre an --trocken")
        self.assertEqual((antwort["ok"], antwort["geaendert"], antwort["trocken"]), (True, False, True))
        self.assertFalse(os.path.exists(self.p.sperre))
        self.sende("sperre an")
        self.sende("sperre aus --trocken")
        self.assertEqual(A.sperre_zustand(self.p), "an")


# ================================================================ pruefe-sonder
class PruefeSonderTests(AnkerTest):
    IDS = ("1", "42", "999999999999999999", "123e4567-e89b-12d3-a456-426614174000", "00000000-0000-0000-0000-000000000000",
           "ffffffff-ffff-ffff-ffff-ffffffffffff")

    def test_lehnt_immer_ab(self):
        for name in ("root", "wamocon", "wamo-release"):
            for anfrage_id in self.IDS:
                with self.subTest(name=name, id=anfrage_id):
                    antwort = self.sende(f"pruefe-sonder {anfrage_id}", name)
                    self.assertFalse(antwort["ok"])
                    self.assertEqual(antwort["ergebnis"], "NEIN")
                    self.assertEqual(antwort["grund"], "sonder_gesperrt")
                    self.assertNotIn("OK", antwort["text"].split())

    def test_lehnt_auch_ohne_sperre_und_mit_zwei_admins_ab(self):
        self.assertEqual(A.sperre_zustand(self.p), "aus")
        self.assertEqual(self.sende("pruefe-sonder 7")["ergebnis"], "NEIN")
        self.sende("sperre an")
        self.assertEqual(self.sende("pruefe-sonder 7")["ergebnis"], "NEIN")

    def test_direkter_aufruf_kennt_keinen_weg_zu_ok(self):
        for anfrage_id in self.IDS + ("", "x", None):
            antwort = self.a.cmd_pruefe_sonder(anfrage_id)
            self.assertIs(antwort["ok"], False)
            self.assertEqual(antwort["ergebnis"], "NEIN")

    def test_ungueltige_kennung(self):
        for anfrage_id in ("", "abc", "0", "01", "../1", "1;2", "-1", "1" * 19, "123e4567-e89b-12d3-a456-42661417400"):
            with self.subTest(id=anfrage_id):
                self.assertEqual(self.sende_roh(f"0123456789abcdef pruefe-sonder {anfrage_id}\n".encode())["abgelehnt"], True)

    def test_ausgabe_des_clients_beginnt_mit_nein(self):
        zeilen = A.formatiere(self.sende("pruefe-sonder 5"))
        self.assertTrue(zeilen[0].startswith("NEIN "))


# ================================================================ rueckweg
class RueckwegTests(AnkerTest):
    def vorbereiten(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI, kern_aktiv=NEU_KERN, kern_letzte_gute=GUT_KERN)

    def test_ui_zurueck(self):
        self.vorbereiten()
        antwort = self.sende("rueckweg ui")
        self.assertTrue(antwort["ok"])
        self.assertEqual(len(self.aufrufe), 1)
        argv, zeitlimit = self.aufrufe[0]
        self.assertEqual(argv, [os.path.join(self.p.schritte, "rueckweg-ui"), "v0.1.0", "a" * 40, "blau"])
        self.assertEqual(zeitlimit, A.SCHRITT_ZEIT_S["ui"])
        stand = A.stand_laden(self.p.stand, self.p.vertrauens_uid)
        self.assertEqual(stand["komponenten"]["ui"]["aktiv"]["commit"], "a" * 40)
        self.assertEqual(stand["komponenten"]["kern"]["aktiv"], NEU_KERN)  # die andere Komponente bleibt

    def test_kern_zurueck_und_alles(self):
        self.vorbereiten()
        self.assertTrue(self.sende("rueckweg kern")["ok"])
        self.assertEqual([a[0][0] for a in self.aufrufe], [os.path.join(self.p.schritte, "rueckweg-kern")])
        self.aufrufe.clear()
        antwort = self.sende("rueckweg alles")
        self.assertTrue(antwort["ok"])
        self.assertEqual([t["komponente"] for t in antwort["teile"]], ["ui", "kern"])
        self.assertEqual([t.get("hinweis") is not None for t in antwort["teile"]], [False, True])  # kern war schon zurück
        self.assertEqual(len(self.aufrufe), 2)  # das (idempotente) Skript läuft auch für den schon zurückgeführten Kern

    def test_ein_zweiter_rueckweg_geht_nie_weiter_zurueck_als_zum_letzten_guten_stand(self):
        self.vorbereiten()
        self.sende("rueckweg ui")
        zweite = self.sende("rueckweg ui")
        self.assertTrue(zweite["ok"])
        self.assertEqual(zweite["teile"][0]["geaendert"], False)  # Buchführung unverändert
        self.assertEqual([a[0][1:] for a in self.aufrufe], [["v0.1.0", "a" * 40, "blau"]] * 2)  # beide Male dasselbe Ziel
        stand = A.stand_laden(self.p.stand, self.p.vertrauens_uid)
        self.assertEqual(stand["komponenten"]["ui"]["letzte_gute"], GUT_UI)

    def test_schalter_schon_umgelegt_aber_buchfuehrung_hinkt(self):
        """Absturz zwischen Schalter und Buchung: aktiv zeigt schon den guten Stand, das System steht aber noch falsch.
        Der Rückweg verlässt sich nicht auf die Buchführung, sondern ruft das Skript."""
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=GUT_UI, ui_letzte_gute=GUT_UI)
        antwort = self.sende("rueckweg ui")
        self.assertTrue(antwort["ok"])
        self.assertEqual(len(self.aufrufe), 1)

    def test_ausgabe_der_skripte_sehen_nur_menschen(self):
        self.vorbereiten()
        self.schritt_ergebnis = (0, b"docker: container gruen gestartet")
        release = self.sende("rueckweg ui", "wamo-release")
        self.assertTrue(release["ok"])
        self.assertIsNone(release["teile"][0].get("ausgabe"))
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        mensch = self.sende("rueckweg ui", "wamocon")
        self.assertIn("gruen gestartet", mensch["teile"][0]["ausgabe"])
        for z in self.audit():
            self.assertNotIn("gestartet", json.dumps(z))  # und ins Protokoll gehört nie die Ausgabe selbst

    def test_ergebnis_zeile_nennt_die_teilgruende(self):
        self.schritte_anlegen(namen=("rueckweg-ui",))
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI, kern_aktiv=NEU_KERN, kern_letzte_gute=GUT_KERN)
        self.sende("rueckweg alles")
        self.assertEqual(self.audit()[-1]["teile"], "ui:ok,kern:schritt_fehlt")

    def test_kein_guter_stand_bekannt(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI)
        antwort = self.sende("rueckweg ui")
        self.assertFalse(antwort["ok"])
        self.assertEqual(antwort["teile"][0]["grund"], "kein_guter_stand")
        self.assertEqual(self.aufrufe, [])

    def test_aktiv_unbekannt_geht_auf_den_guten_stand(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_letzte_gute=GUT_UI)
        self.assertTrue(self.sende("rueckweg ui")["ok"])
        self.assertEqual(len(self.aufrufe), 1)

    def test_schritt_fehlt(self):
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        antwort = self.sende("rueckweg ui")
        self.assertEqual(antwort["teile"][0]["grund"], "schritt_fehlt")
        self.assertEqual(self.aufrufe, [])
        self.assertEqual(A.stand_laden(self.p.stand, self.p.vertrauens_uid)["komponenten"]["ui"]["aktiv"], NEU_UI)

    def test_schritt_mit_falscher_pruefsumme(self):
        self.vorbereiten()
        with open(os.path.join(self.p.schritte, "rueckweg-ui"), "ab") as f:
            f.write(b"# untergeschoben\n")
        self.assertEqual(self.sende("rueckweg ui")["teile"][0]["grund"], "schritt_unsicher")
        self.assertEqual(self.aufrufe, [])

    def test_schritt_nicht_im_manifest(self):
        self.vorbereiten()
        os.unlink(self.p.manifest)
        self.assertEqual(self.sende("rueckweg ui")["teile"][0]["grund"], "schritt_unsicher")
        self.manifest_schreiben()
        with open(self.p.manifest, "w") as f:
            f.write("")
        self.assertEqual(self.sende("rueckweg ui")["teile"][0]["grund"], "schritt_unsicher")
        self.assertEqual(self.aufrufe, [])

    def test_schritt_als_link(self):
        self.vorbereiten()
        pfad = os.path.join(self.p.schritte, "rueckweg-ui")
        ziel = os.path.join(self.tmp.name, "boese.sh")
        os.replace(pfad, ziel)
        try:
            os.symlink(ziel, pfad)
        except (OSError, NotImplementedError):
            self.skipTest("Links nicht möglich")
        self.manifest_schreiben()
        self.assertEqual(self.sende("rueckweg ui")["teile"][0]["grund"], "schritt_unsicher")

    @unittest.skipUnless(POSIX and hasattr(os, "geteuid") and os.geteuid() != 0, "Rechte nur als normaler Benutzer prüfbar")
    def test_schritt_fuer_alle_beschreibbar_oder_nicht_ausfuehrbar(self):
        self.vorbereiten()
        pfad = os.path.join(self.p.schritte, "rueckweg-ui")
        for modus in (0o775, 0o757, 0o644):
            with self.subTest(modus=oct(modus)):
                os.chmod(pfad, modus)
                self.assertEqual(self.sende("rueckweg ui")["teile"][0]["grund"], "schritt_unsicher")
        self.assertEqual(self.aufrufe, [])

    def test_schritt_fehlgeschlagen_zustand_bleibt(self):
        self.vorbereiten()
        vorher = self.lies(self.p.stand)
        self.schritt_ergebnis = (3, b"docker: Fehler\n\x1b[31mrot")
        antwort = self.sende("rueckweg ui")
        self.assertFalse(antwort["ok"])
        teil = antwort["teile"][0]
        self.assertEqual((teil["grund"], teil["exit"]), ("schritt_fehlgeschlagen", 3))
        self.assertNotIn("\x1b", teil["ausgabe"])
        self.assertEqual(self.lies(self.p.stand), vorher)

    def test_zeitlimit_und_startfehler(self):
        self.vorbereiten()
        vorher = self.lies(self.p.stand)
        for code in ("zeitlimit", "start_fehlgeschlagen"):
            with self.subTest(code=code):
                self.schritt_ergebnis = A.SchrittFehler(code)
                antwort = self.sende("rueckweg ui")
                self.assertEqual(antwort["teile"][0]["grund"], code)
                self.assertEqual(self.lies(self.p.stand), vorher)

    def test_aufruf_ist_eine_liste_aus_geprueften_werten(self):
        self.vorbereiten()
        self.sende("rueckweg alles")
        for argv, _ in self.aufrufe:
            self.assertIsInstance(argv, list)
            self.assertEqual(len(argv), 4)
            self.assertTrue(argv[0].startswith(self.p.schritte + os.sep) or argv[0].startswith(self.p.schritte + "/"))
            self.assertTrue(A.VERSION_RE.fullmatch(argv[1]) and A.COMMIT_RE.fullmatch(argv[2]))

    def test_die_anfrage_beeinflusst_den_aufruf_nicht(self):
        """Der einzige Wert aus der Anfrage ist ui|kern|alles; Version, Commit und Ziel stammen aus der Zustandsdatei."""
        self.vorbereiten()
        for text in ("rueckweg ui; id", "rueckweg ui v9.9.9", "rueckweg ../../tmp/x"):
            self.assertTrue(self.sende(text)["abgelehnt"])
        self.assertEqual(self.aufrufe, [])

    def test_gleichzeitiger_schreibender_befehl_ist_belegt(self):
        if not LINUX:
            self.skipTest("flock nur unter Linux")
        import fcntl
        self.vorbereiten()
        fd = os.open(self.p.schreibsperre, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        antwort = self.sende("rueckweg ui")
        self.assertEqual(antwort["grund"], "belegt")
        self.assertEqual(self.aufrufe, [])
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertTrue(self.sende("rueckweg ui")["ok"])

    def test_alles_meldet_teilfehler(self):
        self.schritte_anlegen(namen=("rueckweg-ui",))
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI, kern_aktiv=NEU_KERN, kern_letzte_gute=GUT_KERN)
        antwort = self.sende("rueckweg alles")
        self.assertFalse(antwort["ok"])
        self.assertEqual([t["ok"] for t in antwort["teile"]], [True, False])
        self.assertEqual(antwort["teile"][1]["grund"], "schritt_fehlt")
        stand = A.stand_laden(self.p.stand, self.p.vertrauens_uid)
        self.assertEqual(stand["komponenten"]["ui"]["aktiv"]["commit"], "a" * 40)  # der Teil, der klappte, ist gebucht
        self.assertEqual(stand["komponenten"]["kern"]["aktiv"], NEU_KERN)

    def test_protokoll_zeigt_anfrage_schritt_und_ergebnis(self):
        self.vorbereiten()
        self.sende("rueckweg ui")
        self.assertEqual([z["ereignis"] for z in self.audit()], ["anfrage", "schritt", "ergebnis"])
        self.assertTrue(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])


class TrockenTests(AnkerTest):
    def test_trocken_aendert_nichts(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI, kern_aktiv=NEU_KERN, kern_letzte_gute=GUT_KERN)
        stand, manifest = self.lies(self.p.stand), self.lies(self.p.manifest)
        vorher = sorted(os.listdir(self.p.zustand))
        for text in ("rueckweg ui --trocken", "rueckweg kern --trocken", "rueckweg alles --trocken", "sperre an --trocken",
                     "sperre aus --trocken"):
            with self.subTest(text=text):
                antwort = self.sende(text)
                self.assertTrue(antwort["ok"], antwort)
                self.assertTrue(antwort["trocken"])
        self.assertEqual(self.aufrufe, [])
        self.assertEqual(self.lies(self.p.stand), stand)
        self.assertEqual(self.lies(self.p.manifest), manifest)
        self.assertFalse(os.path.exists(self.p.sperre))
        self.assertEqual(sorted(os.listdir(self.p.zustand)), vorher)

    def test_trocken_zeigt_den_plan(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        antwort = self.sende("rueckweg ui --trocken")
        self.assertEqual(antwort["teile"][0]["plan"], f"rueckweg-ui v0.1.0 {'a' * 40} blau")
        self.assertFalse(antwort["teile"][0]["geaendert"])
        self.assertIsNone(antwort["teile"][0].get("schon_gut"))
        self.stand_setzen(ui_aktiv=GUT_UI, ui_letzte_gute=GUT_UI)
        self.assertTrue(self.sende("rueckweg ui --trocken")["teile"][0]["schon_gut"])

    def test_trocken_ohne_schritt_meldet_den_mangel(self):
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        antwort = self.sende("rueckweg ui --trocken")
        self.assertFalse(antwort["ok"])
        self.assertEqual(antwort["teile"][0]["grund"], "schritt_fehlt")

    def test_trocken_braucht_kein_protokoll_zum_schreiben_aber_wird_protokolliert(self):
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        self.sende("sperre an --trocken")
        self.assertEqual([z["ereignis"] for z in self.audit()], ["anfrage", "ergebnis"])
        self.assertTrue(all(z.get("trocken") for z in self.audit()))

    def test_trocken_unter_sperre_fuer_vorwaertsbefehle(self):
        self.sende("sperre an")
        self.assertEqual(self.sende("umschalten 7 --trocken")["grund"], "gesperrt")


# ================================================================ Status und Installation
class StatusTests(AnkerTest):
    def test_status_zeigt_alles(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        antwort = self.sende("status")
        self.assertTrue(antwort["ok"])
        self.assertEqual(antwort["sperre"], "aus")
        self.assertEqual(antwort["zustand"], "ok")
        self.assertEqual(antwort["komponenten"]["ui"]["letzte_gute"], GUT_UI)
        self.assertEqual(antwort["installation"], "ok")
        self.assertIn("offene_anfragen_hinweis", antwort)
        self.assertEqual(antwort["audit"]["kette"], "ok")

    def test_status_ist_nur_lesend(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        stand = self.lies(self.p.stand)
        self.sende("status")
        self.assertEqual(self.lies(self.p.stand), stand)
        self.assertFalse(os.path.exists(self.p.sperre))
        self.assertEqual(self.aufrufe, [])

    def test_installation_weicht_ab(self):
        with open(os.path.join(self.p.lib, "wamo_anker.py"), "wb") as f:
            f.write(b"print('ok')\n")
        self.schritte_anlegen()
        self.assertEqual(A.installation_pruefen(self.p), [])
        with open(os.path.join(self.p.lib, "wamo_anker.py"), "ab") as f:
            f.write(b"# veraendert\n")
        self.assertTrue(any("wamo_anker.py" in t for t in A.installation_pruefen(self.p)))
        self.assertEqual(self.sende("status")["installation"], "abweichung")

    def test_installation_unerwartete_datei_und_fehlendes_manifest(self):
        self.schritte_anlegen()
        with open(os.path.join(self.p.lib, "neu.py"), "wb") as f:
            f.write(b"x")
        self.assertTrue(any("neu.py" in t for t in A.installation_pruefen(self.p)))
        os.unlink(self.p.manifest)
        self.assertTrue(A.installation_pruefen(self.p)[0].startswith("Manifest"))

    def test_manifest_mit_pfadtricks_ist_ungueltig(self):
        for zeile in (f"{'a' * 64}  ../etc/passwd", f"{'a' * 64}  /etc/passwd", f"{'a' * 64}  a/../b", f"{'A' * 64}  x",
                      f"{'a' * 63}  x", f"{'a' * 64} x", "x"):
            with self.subTest(zeile=zeile):
                with open(self.p.manifest, "w", newline="\n") as f:
                    f.write(zeile + "\n")
                with self.assertRaises(ValueError):
                    A.manifest_lesen(self.p)

    def test_installation_link_wird_gemeldet(self):
        self.schritte_anlegen()
        try:
            os.symlink(self.tmp.name, os.path.join(self.p.lib, "link"))
        except (OSError, NotImplementedError):
            self.skipTest("Links nicht möglich")
        self.assertTrue(any("link" in t for t in A.installation_pruefen(self.p)))


@unittest.skipUnless(POSIX and hasattr(os, "geteuid") and os.geteuid() != 0, "Rechte nur als normaler Benutzer prüfbar")
class VorfahrenTests(AnkerTest):
    """Wer einen Ordner oberhalb von lib ersetzen darf, kann lib samt eigener Prüfsumme austauschen."""

    def test_in_ordnung(self):
        self.assertEqual(A.vorfahren_unsicher(self.p), [])

    def test_fuer_alle_beschreibbarer_vorfahre(self):
        os.chmod(self.tmp.name, 0o777)
        self.assertTrue(any("für alle beschreibbar" in t for t in A.vorfahren_unsicher(self.p)))
        self.assertTrue(any("beschreibbar" in t for t in A.installation_pruefen(self.p)))
        os.chmod(self.tmp.name, 0o777 | stat.S_ISVTX)  # Sticky-Bit wie bei /tmp: fremde Einträge lassen sich nicht ersetzen
        # Hier geht es um den Zweig "für alle"; die Gruppe prüft test_gruppe_mit_mitgliedern. Ohne die Attrappe hängt das Ergebnis davon ab, ob die
        # Hauptgruppe des Testbenutzers weitere Mitglieder hat (auf Entwicklungsrechnern und in der Prüfer-Sandbox ist es der Benutzer selbst).
        with mock.patch.object(A, "_gruppe_hat_mitglieder", return_value=False):
            self.assertEqual(A.vorfahren_unsicher(self.p), [])

    def test_fremder_besitzer(self):
        p = A.Pfade.unter(self.tmp.name, vertrauens_uid=os.getuid() + 1)
        self.assertTrue(any("Besitzer" in t for t in A.vorfahren_unsicher(p)))

    def test_gruppe_mit_mitgliedern(self):
        os.chmod(self.tmp.name, 0o775)
        with mock.patch.object(A, "_gruppe_hat_mitglieder", return_value=False):
            self.assertEqual(A.vorfahren_unsicher(self.p), [])  # Gruppe ohne Mitglieder (wie staff auf Ubuntu): kein Befund
        with mock.patch.object(A, "_gruppe_hat_mitglieder", return_value=True):
            self.assertTrue(any("Gruppe" in t for t in A.vorfahren_unsicher(self.p)))


# ================================================================ Quelltext
class QuelltextTests(unittest.TestCase):
    QUELLE = (INFRA / "anker" / "wamo_anker.py")

    def baum(self):
        return ast.parse(self.QUELLE.read_text(encoding="utf-8"))

    def test_keine_shell_und_keine_dynamische_ausfuehrung(self):
        for knoten in ast.walk(self.baum()):
            if isinstance(knoten, ast.Call):
                for kw in knoten.keywords:
                    self.assertNotEqual((kw.arg, getattr(kw.value, "value", None)), ("shell", True))
                name = getattr(knoten.func, "id", None) or getattr(knoten.func, "attr", None)
                self.assertNotIn(name, {"eval", "exec", "system", "popen", "input", "__import__", "loads_pickle"})
            if isinstance(knoten, (ast.Import, ast.ImportFrom)):
                module = [a.name for a in knoten.names] + [getattr(knoten, "module", None) or ""]
                for m in module:
                    self.assertNotIn(m.split(".")[0], {"pickle", "marshal", "shelve", "yaml", "requests", "urllib", "http"})

    def test_subprocess_nur_popen_mit_liste(self):
        aufrufe = [k for k in ast.walk(self.baum()) if isinstance(k, ast.Call) and getattr(k.func, "attr", "") in
                   {"Popen", "run", "call", "check_call", "check_output"}]
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(aufrufe[0].args[0].id, "argv")

    def test_keine_gedankenstriche_in_den_texten(self):
        for datei in (self.QUELLE, INFRA / "anker" / "wamo-anker.service", INFRA / "anker" / "wamo-anker.sh"):
            text = datei.read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), text, datei.name)
            self.assertNotIn(chr(0x2013), text, datei.name)
        # Strich mit Leerzeichen als Satzzeichen: nur in Kommentaren und Zeichenketten prüfen (Rechenzeichen im Code sind erlaubt)
        text_arten = {tokenize.COMMENT, tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}
        with open(self.QUELLE, "rb") as f:
            for tok in tokenize.tokenize(f.readline):
                if tok.type in text_arten:
                    self.assertNotIn(" - ", tok.string, f"Zeile {tok.start[0]}")

    def test_groesse_bleibt_klein(self):
        """Konzept Abschnitt G: der Anker bleibt klein und damit prüfbar (Code ohne Leerzeilen, Kommentare und Docstrings)."""
        baum = self.baum()
        doc_zeilen = set()
        for knoten in ast.walk(baum):
            if isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef)) and ast.get_docstring(knoten, clean=False):
                erste = knoten.body[0]
                doc_zeilen.update(range(erste.lineno, erste.end_lineno + 1))
        code = [n for n, z in enumerate(self.QUELLE.read_text(encoding="utf-8").splitlines(), 1)
                if z.strip() and not z.strip().startswith("#") and n not in doc_zeilen]
        # Richtwert des Konzepts: unter 1000. Stand 22.10.2026: etwa 1020 Codezeilen (Server, Client, Prüfungen und Fehlertexte
        # zusammen); die Obergrenze hier verhindert unbemerktes Wachsen, die Abweichung steht im Bericht zu Schritt 0.1
        self.assertLess(len(code), 1100)


# ================================================================ Client
class ClientTests(AnkerTest):
    def test_ungueltige_aufrufe_enden_mit_2_ohne_verbindung(self):
        for args in ([], ["reboot"], ["rueckweg"], ["rueckweg", "../ui"], ["rueckweg", "ui", "--trocken", "x"], ["sperre", "ein"],
                     ["status", "--trocken"], ["pruefe-sonder"], ["status\x00"], ["stat" + chr(0x202E) + "us"], ["--json"]):
            with self.subTest(args=args):
                with self.stderr(), mock.patch.object(socket, "socket", side_effect=AssertionError("keine Verbindung")):
                    self.assertEqual(A.client(list(args)), 2)

    def test_main(self):
        with self.stderr():
            self.assertEqual(A.main(["wamo_anker.py", "reboot"]), 2)  # falscher Aufruf, keine Verbindung
        with mock.patch.object(A, "PFADE", self.p), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(A.main(["wamo_anker.py", "selbstpruefung"]), 1)  # Wegwerf-Ordner ohne Manifest: weicht ab
            self.schritte_anlegen()
            self.assertEqual(A.main(["wamo_anker.py", "selbstpruefung"]), 0)
        if not (LINUX and os.geteuid() == 0):  # als root unter Linux würde der echte Dienst starten
            with self.stderr():
                self.assertEqual(A.main(["wamo_anker.py", "dienst"]), 1)  # nur Linux als root

    def test_lokal_ist_nur_fuer_root(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("läuft als root")
        with self.stderr():
            self.assertEqual(A.lokal(["status"]), 2)

    def test_formatiere(self):
        self.schritte_anlegen()
        self.stand_setzen(ui_aktiv=NEU_UI, ui_letzte_gute=GUT_UI)
        for befehl in ("status", "rueckweg ui --trocken", "sperre an", "pruefe-sonder 3", "umschalten 7"):
            with self.subTest(befehl=befehl):
                text = "\n".join(A.formatiere(self.sende(befehl)))
                self.assertTrue(text)
        self.assertIn("ABGELEHNT", A.formatiere(self.sende_roh(b"unsinn"))[0])

    def test_antwort_ist_begrenzt_und_ascii(self):
        roh = A.antwort_bytes({"ok": True, "text": "Rückweg " + chr(0x202E) + "x" * 10000})
        self.assertLessEqual(len(roh), A.ANTWORT_MAX)
        roh.decode("ascii")
        self.assertEqual(json.loads(roh)["grund"], "antwort_zu_gross")


# ================================================================ Lesen und Bedienen (Socketpaar, auch unter Windows)
class LeseTests(AnkerTest):
    def paar(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        return a, b

    def test_gueltige_zeile(self):
        a, b = self.paar()
        b.sendall(b"0123456789abcdef status\n")
        self.assertEqual(A.lese_zeile(a), (b"0123456789abcdef status\n", None))

    def test_zeile_in_stuecken(self):
        a, b = self.paar()

        def schicken():
            for teil in (b"0123456789", b"abcdef sta", b"tus\n"):
                b.sendall(teil)
                time.sleep(0.05)

        t = threading.Thread(target=schicken)
        t.start()
        self.assertEqual(A.lese_zeile(a)[1], None)
        t.join()

    def test_zu_lang(self):
        a, b = self.paar()
        b.sendall(b"x" * 400)
        self.assertEqual(A.lese_zeile(a)[1], "zeile_zu_lang")

    def test_grenze_genau(self):
        a, b = self.paar()
        b.sendall(b"x" * (A.ZEILE_MAX - 1) + b"\n")
        self.assertIsNone(A.lese_zeile(a)[1])
        a2, b2 = self.paar()
        b2.sendall(b"x" * A.ZEILE_MAX + b"\n")
        self.assertEqual(A.lese_zeile(a2)[1], "zeile_zu_lang")

    def test_langsam_wird_abgebrochen(self):
        a, b = self.paar()
        b.sendall(b"0123")
        with mock.patch.object(A, "LESE_ZEIT_S", 0.2), mock.patch.object(A, "GESAMT_ZEIT_S", 0.4):
            start = time.monotonic()
            self.assertEqual(A.lese_zeile(a)[1], "zeitueberschreitung")
        self.assertLess(time.monotonic() - start, 2)

    def test_tropfen_fuer_tropfen_hat_eine_gesamtgrenze(self):
        a, b = self.paar()
        stop = threading.Event()

        def tropfen():
            while not stop.is_set():
                try:
                    b.sendall(b"a")
                except OSError:
                    return
                time.sleep(0.1)

        t = threading.Thread(target=tropfen, daemon=True)
        t.start()
        with mock.patch.object(A, "LESE_ZEIT_S", 0.5), mock.patch.object(A, "GESAMT_ZEIT_S", 0.6):
            self.assertEqual(A.lese_zeile(a)[1], "zeitueberschreitung")
        stop.set()
        t.join()

    def test_abgebrochene_verbindung_ist_keine_zeitueberschreitung(self):
        class Abgebrochen:
            def settimeout(self, sekunden):
                pass

            def recv(self, n):
                raise ConnectionResetError(errno.ECONNRESET, "Verbindung zurückgesetzt")

        self.assertEqual(A.lese_zeile(Abgebrochen())[1], "kein_zeilenende")

    def test_geschlossen_ohne_zeilenende(self):
        a, b = self.paar()
        b.sendall(b"abc")
        b.close()
        self.assertEqual(A.lese_zeile(a)[1], "kein_zeilenende")

    def test_mehr_als_eine_zeile_wird_abgelehnt(self):
        a, b = self.paar()
        b.sendall(b"0123456789abcdef status\n0123456789abcdee sperre aus\n")
        roh, fehler = A.lese_zeile(a)
        self.assertIsNone(fehler)
        antwort = self.sende_roh(roh)
        self.assertEqual(antwort["grund"], "zeichen")
        self.assertFalse(os.path.exists(self.p.sperre))

    def server(self, uid):
        return A.Server(self.a, peer=lambda conn: A.Peer(1, uid, uid))

    def test_bedienen_erlaubter(self):
        a, b = self.paar()
        b.sendall(b"0123456789abcdef status\n")
        self.server(1000).bedienen(a)
        antwort = json.loads(b.recv(65536))
        self.assertTrue(antwort["ok"])

    def test_bedienen_fremder_wird_ohne_lesen_abgewiesen(self):
        a, b = self.paar()
        b.sendall(b"0123456789abcdef sperre an\n")
        with mock.patch.object(A, "lese_zeile", side_effect=AssertionError("darf nichts lesen")):
            self.server(4711).bedienen(a)
        antwort = json.loads(b.recv(65536))
        self.assertEqual(antwort["grund"], "benutzer")
        self.assertFalse(os.path.exists(self.p.sperre))

    def test_bedienen_ohne_peer_ist_fremd(self):
        a, b = self.paar()
        A.Server(self.a, peer=lambda conn: None).bedienen(a)
        self.assertEqual(json.loads(b.recv(65536))["grund"], "benutzer")

    def test_bedienen_ueberlange_anfrage(self):
        a, b = self.paar()
        b.sendall(b"x" * 1000)
        self.server(1000).bedienen(a)
        self.assertEqual(json.loads(b.recv(65536))["grund"], "zeile_zu_lang")


# ================================================================ echter Unix-Socket (nur Linux)
@unittest.skipUnless(LINUX, "Unix-Socket und SO_PEERCRED nur unter Linux")
class SocketTests(AnkerTest):
    def starte(self, uid_fuer_wamocon=None, modus=0o660):
        uid = os.getuid() if uid_fuer_wamocon is None else uid_fuer_wamocon
        anker = self.neuer_anker(aufloesen=lambda n: uid if n == "wamocon" else None)
        server = A.Server(anker, modus)
        server.binden()
        faden = threading.Thread(target=server.lauf, daemon=True)
        faden.start()
        self.faden = faden

        def ende():
            server.anhalten()
            faden.join(5)
            server.schliessen()

        self.addCleanup(ende)
        return server

    def frage(self, text="status", senden=True, id=None):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(self.p.socket)
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
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.p.socket)
        peer = A.peer_von(s)
        s.close()
        self.assertEqual((peer.uid, peer.gid, peer.pid), (os.getuid(), os.getgid(), os.getpid()))

    def test_erlaubter_benutzer(self):
        self.starte()
        antwort = self.frage("status")
        self.assertTrue(antwort["ok"])
        self.assertEqual(antwort["version"], A.VERSION)

    def test_fremder_benutzer_bekommt_auch_nach_dem_senden_eine_antwort(self):
        self.starte(uid_fuer_wamocon=os.getuid() + 1)
        self.assertEqual(self.frage("sperre aus")["grund"], "benutzer")  # die Anfrage wurde nicht gelesen
        self.assertFalse(os.path.exists(self.p.sperre))

    def test_fremder_benutzer_ohne_dass_der_server_liest(self):
        self.starte(uid_fuer_wamocon=os.getuid() + 1)
        antwort = self.frage(senden=False)  # nichts senden: die Abweisung kommt trotzdem
        self.assertEqual(antwort["grund"], "benutzer")
        zeilen = self.audit()
        self.assertEqual((zeilen[-1]["ereignis"], zeilen[-1]["grund"], zeilen[-1]["uid"]), ("abgelehnt", "benutzer", os.getuid()))

    def als_nobody_fragen(self):
        """Eigener Prozess mit uid 65534 (kein fork: ein vererbter flock des Dienstes würde ihn blockieren): verbindet
        und sendet eine schreibende Anfrage. Ergebnis wie vom Prozess gemeldet."""
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
        r = subprocess.run([sys.executable, "-I", "-c", code, self.p.socket, self.zeile("sperre aus").decode()],
                           capture_output=True, text=True, timeout=30, user=65534, group=65534, extra_groups=[])
        if not r.stdout:
            self.skipTest("der Python-Interpreter ist für nobody nicht ausführbar: " + r.stderr[:100])
        return json.loads(r.stdout)

    @unittest.skipUnless(LINUX and os.geteuid() == 0, "braucht root zum Wechsel der uid")
    def test_fremde_uid_scheitert_an_den_dateirechten_und_an_so_peercred(self):
        os.chmod(self.tmp.name, 0o755)  # nobody darf bis zum Socket gehen
        self.starte(uid_fuer_wamocon=0)  # erlaubt ist nur root
        self.assertEqual(self.als_nobody_fragen(), {"fehler": "PermissionError"})  # erste Schicht: Socket 0660
        os.chmod(self.p.socket, 0o666)  # erste Schicht aufgehoben: jetzt muss SO_PEERCRED allein abweisen
        antwort = self.als_nobody_fragen()
        self.assertEqual((antwort.get("ok"), antwort.get("grund")), (False, "benutzer"), antwort)
        self.assertEqual(self.audit()[-1]["uid"], 65534)
        self.assertFalse(os.path.exists(self.p.sperre))

    def test_ueberlange_und_langsame_anfragen(self):
        self.starte()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(self.p.socket)
        s.sendall(b"x" * 300)
        self.assertEqual(json.loads(s.recv(4096))["grund"], "zeile_zu_lang")
        s.close()
        with mock.patch.object(A, "LESE_ZEIT_S", 0.2), mock.patch.object(A, "GESAMT_ZEIT_S", 0.4):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(self.p.socket)
            s.sendall(b"0123")
            self.assertEqual(json.loads(s.recv(4096))["grund"], "zeitueberschreitung")
            s.close()
        self.assertTrue(self.frage("status")["ok"])  # der Dienst lebt weiter

    def test_dienst_ueberlebt_muell(self):
        self.starte()
        for muell in (b"\x00" * 50, b"\xff" * 20 + b"\n", os.urandom(100), b"", b"{}\n", b"GET / HTTP/1.1\r\n\r\n"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(self.p.socket)
            if muell:
                s.sendall(muell)
            s.shutdown(socket.SHUT_WR)
            antwort = json.loads(s.recv(4096))
            s.close()
            self.assertFalse(antwort["ok"])
        self.assertTrue(self.frage("status")["ok"])

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
        server2 = A.Server(self.neuer_anker())
        os.unlink(self.p.socket)
        with open(self.p.socket, "wb") as f:
            f.write(b"keine Socket-Datei")
        with self.assertRaises(SystemExit):
            server2.binden()
        self.assertEqual(self.lies(self.p.socket), b"keine Socket-Datei")

    @unittest.skipUnless(LINUX and os.geteuid() != 0, "Rechte nur als normaler Benutzer prüfbar")
    def test_binden_verweigert_offenen_ordner(self):
        os.chmod(self.p.lauf, 0o777)
        with self.assertRaises(SystemExit):
            A.Server(self.neuer_anker()).binden()
        self.assertFalse(os.path.exists(self.p.socket))

    def test_dienst_start_und_stop_stehen_im_protokoll(self):
        server = self.starte()
        self.frage("status")
        server.anhalten()
        self.faden.join(5)
        self.assertFalse(self.faden.is_alive())
        ereignisse = [z["ereignis"] for z in self.audit()]
        self.assertEqual(ereignisse[0], "dienst_start")
        self.assertEqual(ereignisse[-1], "dienst_stop")
        self.assertEqual(self.audit()[-1]["grund"], "angefordert")
        self.assertTrue(A.kette_pruefen(self.p.audit, self.p.vertrauens_uid)[0])

    def test_voruebergehender_accept_fehler_beendet_den_dienst_nicht(self):
        """Zu viele offene Dateien (EMFILE) und Verbindungsabbrüche dürfen den Notanker nicht still beenden."""
        server = A.Server(self.neuer_anker(aufloesen=lambda n: os.getuid() if n == "wamocon" else None))
        folge = [OSError(errno.EMFILE, "zu viele offene Dateien"), OSError(errno.ECONNABORTED, "abgebrochen"),
                 socket.timeout(), OSError(errno.EBADF, "Socket geschlossen")]

        class Attrappe:
            def accept(self):
                raise folge.pop(0)

        server._srv = Attrappe()
        with self.stderr(), mock.patch.object(A.time, "sleep"):
            angefordert = server.lauf()
        self.assertEqual(folge, [])  # alle vier Fehler wurden abgearbeitet, erst EBADF beendete die Schleife
        self.assertFalse(angefordert)  # ohne Anforderung beendet: dienst_main meldet Exit 1, systemd startet neu
        self.assertEqual(self.audit()[-1]["grund"], "socket_weg")

    def test_client_erfolgspfad_und_exit_codes(self):
        self.starte()
        uid = os.getuid()
        for args, exit_code in ((["status"], 0), (["sperre", "an"], 0), (["sperre", "an", "--trocken"], 0),
                                (["pruefe-sonder", "7"], 1), (["umschalten", "7"], 1), (["--json", "status"], 0)):
            with self.subTest(args=args):
                ausgabe = io.StringIO()
                with contextlib.redirect_stdout(ausgabe):
                    self.assertEqual(A.client(args, self.p.socket, uid), exit_code)
                self.assertTrue(ausgabe.getvalue().strip())
        with self.stderr(), contextlib.redirect_stdout(io.StringIO()):  # Gegenstelle gehört nicht der erwarteten uid
            self.assertEqual(A.client(["status"], self.p.socket, uid + 1), 2)
        with self.stderr():  # kein Dienst unter dem Pfad
            self.assertEqual(A.client(["status"], os.path.join(self.tmp.name, "nichts.sock"), uid), 2)



# ================================================================ echter Schritt-Läufer (nur Linux)
@unittest.skipUnless(LINUX, "Prozesse, select auf Pipes und Sitzungen nur unter Linux")
class SchrittLaeuferTests(unittest.TestCase):
    def skript(self, text):
        d = tempfile.TemporaryDirectory(prefix="anker-schritt-")
        self.addCleanup(d.cleanup)
        pfad = os.path.join(d.name, "s.sh")
        with open(pfad, "w", newline="\n") as f:
            f.write("#!/bin/sh\n" + text)
        os.chmod(pfad, 0o755)
        return pfad

    def test_exit_code_und_ausgabe(self):
        rc, aus = A.schritt_ausfuehren([self.skript("echo hallo; exit 7\n")], 10)
        self.assertEqual((rc, aus), (7, b"hallo\n"))

    def test_argumente_werden_nicht_von_der_shell_ausgewertet(self):
        rc, aus = A.schritt_ausfuehren([self.skript('printf "%s|" "$@"\n'), "$(id)", "a b", ";reboot"], 10)
        self.assertEqual(aus, b"$(id)|a b|;reboot|")

    def test_leere_umgebung(self):
        _, aus = A.schritt_ausfuehren([self.skript("env | sort\n")], 10)
        namen = {z.split(b"=")[0] for z in aus.splitlines()}
        self.assertTrue(namen <= {b"PATH", b"LC_ALL", b"HOME", b"PWD", b"SHLVL", b"_", b"OLDPWD"}, namen)

    def test_ausgabe_ist_begrenzt(self):
        rc, aus = A.schritt_ausfuehren([self.skript("head -c 3000000 /dev/zero | tr '\\0' 'a'\n")], 20)
        self.assertEqual(rc, 0)
        self.assertEqual(len(aus), A.SCHRITT_AUSGABE_MAX)

    def test_zeitlimit_beendet_auch_kindprozesse(self):
        marke = tempfile.NamedTemporaryFile(delete=False)
        marke.close()
        self.addCleanup(os.unlink, marke.name)
        start = time.monotonic()
        with self.assertRaises(A.SchrittFehler) as ctx:
            A.schritt_ausfuehren([self.skript(f"(sleep 1; echo lebt > {marke.name}) &\nsleep 30\n")], 0.5)
        self.assertEqual(ctx.exception.code, "zeitlimit")
        self.assertLess(time.monotonic() - start, 5)
        time.sleep(1.5)
        self.assertEqual(os.path.getsize(marke.name), 0)  # das Kind wurde mit der Sitzung beendet

    def test_nicht_startbar(self):
        with self.assertRaises(A.SchrittFehler) as ctx:
            A.schritt_ausfuehren(["/nicht/vorhanden"], 5)
        self.assertEqual(ctx.exception.code, "start_fehlgeschlagen")

    def test_stdin_ist_leer(self):
        rc, aus = A.schritt_ausfuehren([self.skript("cat; echo ende\n")], 10)
        self.assertEqual(aus, b"ende\n")

    def test_skript_schliesst_die_ausgabe_und_laeuft_weiter(self):
        """Ausgabe zu Ende (EOF), Prozess aber nicht: das Zeitlimit greift im wait, die Sitzung wird beendet."""
        marke = tempfile.NamedTemporaryFile(delete=False)
        marke.close()
        self.addCleanup(os.unlink, marke.name)
        start = time.monotonic()
        with self.assertRaises(A.SchrittFehler) as ctx:
            A.schritt_ausfuehren([self.skript(f"exec 1>&- 2>&-\n(sleep 1; echo lebt > {marke.name}) &\nsleep 30\n")], 0.6)
        self.assertEqual(ctx.exception.code, "zeitlimit")
        self.assertLess(time.monotonic() - start, 5)
        time.sleep(1.5)
        self.assertEqual(os.path.getsize(marke.name), 0)

    def test_jede_unterbrechung_beendet_den_prozess(self):
        """Auch ein unerwarteter Fehler im Anker (hier simuliert) lässt keinen Root-Prozess zurück."""
        marke = tempfile.NamedTemporaryFile(delete=False)
        marke.close()
        self.addCleanup(os.unlink, marke.name)
        with mock.patch.object(A.select, "select", side_effect=RuntimeError("Störung")):
            with self.assertRaises(RuntimeError):
                A.schritt_ausfuehren([self.skript(f"sleep 1; echo lebt > {marke.name}\n")], 30)
        time.sleep(1.5)
        self.assertEqual(os.path.getsize(marke.name), 0)


if __name__ == "__main__":
    unittest.main()
