"""Tests des Annahme-Dienstes der Prüfer-Sandbox (infra/sokrates2/release/pruefer/pruefer_archiv.py, pruefer_annahme.py, pruefer_client.py).

Der Annahme-Dienst läuft als root und nimmt die Stände als Datenstrom entgegen. Die Tests prüfen das Archivformat Zeile für Zeile (jede Abweichung ist
ein Fehler, bevor etwas geschrieben wird, das aus dem Zielordner herausführt), das Schreiben mit Deskriptoren (nur Linux), den Ablauf des Dienstes mit
einem Attrappen-Läufer und den Client über einen echten Unix-Socket. Unix-Sockets und dir_fd gibt es nur unter Linux, dort laufen die Tests mit echten
Dateien; sonst werden sie übersprungen.

Ausführen:  uv run python -m pytest -q test_pruefer_annahme.py   (im Ordner voice-core/)
"""

import io
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PRUEFER = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "release" / "pruefer"
sys.path.insert(0, str(PRUEFER))

import pruefer_annahme as N  # noqa: E402
import pruefer_archiv as A  # noqa: E402
import pruefer_client as C  # noqa: E402

LINUX = sys.platform.startswith("linux") and hasattr(os, "O_NOFOLLOW") and os.geteuid() != 0


def archiv(*zeilen: bytes | str, ende: bool = True) -> io.BytesIO:
    roh = b"".join((z if isinstance(z, bytes) else z.encode()) for z in zeilen)
    return io.BytesIO(roh + (b"END\n" if ende else b""))


def datei(pfad: str, inhalt: bytes = b"x", modus: str = "-") -> bytes:
    return f"F {len(inhalt)} {modus} {pfad}\n".encode() + inhalt + b"\n"


def lies(*zeilen, **kw) -> A.SpeicherZiel:
    ziel = A.SpeicherZiel()
    A.lies_archiv(archiv(*zeilen, **kw), ziel)
    return ziel


class FormatTests(unittest.TestCase):
    def test_gueltiges_archiv(self):
        ziel = lies("D basis\n", "D neu\n", "D neu/src\n", datei("neu/src/a.py", b"x = 1\n"), datei("neu/run.sh", b"#!/bin/sh\n", "x"), "L neu/link\n")
        self.assertEqual(ziel.eintraege["neu/src/a.py"], (b"x = 1\n", False))
        self.assertEqual(ziel.eintraege["neu/run.sh"], (b"#!/bin/sh\n", True))
        self.assertEqual(ziel.eintraege["neu/link"], "L")

    def test_leeres_archiv_und_zaehler(self):
        z = A.SpeicherZiel()
        zaehler = A.lies_archiv(archiv("D basis\n", datei("basis/a", b"abc"), "L basis/l\n"), z)
        self.assertEqual(zaehler, {"dateien": 1, "verzeichnisse": 1, "links": 1, "bytes": 3})
        self.assertEqual(A.lies_archiv(archiv(), A.SpeicherZiel()), {"dateien": 0, "verzeichnisse": 0, "links": 0, "bytes": 0})

    def test_dateien_koennen_leer_sein_und_beliebige_bytes_enthalten(self):
        ziel = lies(datei("neu/leer", b""), datei("neu/bin", bytes(range(256)) + b"\nEND\n"))
        self.assertEqual(ziel.eintraege["neu/leer"][0], b"")
        self.assertEqual(ziel.eintraege["neu/bin"][0], bytes(range(256)) + b"\nEND\n")   # Inhalt wird nie als Zeile gelesen

    def fehler(self, *zeilen, grund=None, **kw):
        with self.assertRaises(A.ArchivFehler) as e:
            A.lies_archiv(archiv(*zeilen, **kw), A.SpeicherZiel())
        if grund:
            self.assertEqual(str(e.exception), grund)

    def test_pfade_werden_streng_geprueft(self):
        for pfad, grund in (("../x", "pfad_wurzel"), ("/etc/passwd", "pfad_wurzel"), ("neu/../../x", "pfad_teil"), ("neu/./a", "pfad_teil"),
                            ("neu//a", "pfad_teil"), ("neu/", "pfad_teil"), ("etc/x", "pfad_wurzel"), ("", "pfad_laenge"), ("neu/a\\b", "pfad_zeichen"),
                            ("neu/a\x01b", "pfad_zeichen"), ("neu/a\x7fb", "pfad_zeichen"), ("neu/" + "a" * 256, "pfad_zeichen"),
                            ("neu/" + "/".join("d" * 3 for _ in range(30)), "pfad_wurzel"),
                            ("basis2/a", "pfad_wurzel"), ("NEU/a", "pfad_wurzel")):
            with self.subTest(pfad=pfad):
                self.fehler(f"D {pfad}\n", grund=grund)
                self.fehler(datei(pfad, b"x"), grund=grund)
                self.fehler(f"L {pfad}\n", grund=grund)

    def test_zu_lange_pfade(self):
        with self.assertRaises(A.ArchivFehler) as e:
            A.pfad_zerlegen("neu/" + "a" * (A.PFAD_MAX + 1))
        self.assertEqual(str(e.exception), "pfad_laenge")
        self.fehler("D neu/" + "a" * (A.PFAD_MAX + 1) + chr(10), grund="pfad_laenge")
        self.fehler("D neu/" + "a" * (A.ZEILE_MAX + 10) + chr(10), grund="zeile")   # noch länger: schon die Zeile ist zu lang, bevor sie gedeutet wird

    def test_wurzel_nur_als_verzeichnis(self):
        self.fehler("L neu\n", grund="pfad_wurzel")
        self.fehler(b"F 1 - neu\nx\n", grund="pfad_wurzel")

    def test_unicode_in_namen_ist_erlaubt(self):
        self.assertIn("neu/Übersicht Größe.md", lies(datei("neu/Übersicht Größe.md", b"ok")).eintraege)

    def test_doppelte_eintraege_und_inhalt_unter_datei_oder_link(self):
        self.fehler("D neu/a\n", "D neu/a\n", grund="doppelt")
        self.fehler(datei("neu/a", b"1"), datei("neu/a", b"2"), grund="doppelt")
        self.fehler("D neu/a\n", datei("neu/a", b"x"), grund="doppelt")
        self.fehler(datei("neu/a", b"x"), "D neu/a/b\n", grund="unter_datei_oder_link")
        self.fehler("L neu/a\n", datei("neu/a/b", b"x"), grund="unter_datei_oder_link")     # der Weg, einen Link zu durchschreiben
        self.fehler("L neu/a\n", "L neu/a/b\n", grund="unter_datei_oder_link")

    def test_kaputte_koepfe(self):
        for zeile in ("F\n", "F x - neu/a\n", "F -1 - neu/a\n", "F 1 y neu/a\n", "F 1 - \n", "F 1  neu/a\n", "F 99999999999 - neu/a\n", "F ١ - neu/a\n",
                      "X neu/a\n", "d neu/a\n", " D neu/a\n", "DD neu/a\n", "\n", "end\n"):
            with self.subTest(zeile=zeile):
                self.fehler(zeile)

    def test_grenzen(self):
        self.fehler(f"F {A.DATEI_MAX + 1} - neu/gross\n", grund="zu_gross")   # abgelehnt, bevor ein Byte gelesen wird
        with self.assertRaises(A.ArchivFehler) as e:
            A.lies_archiv(archiv(datei("neu/a", b"x" * 10), datei("neu/b", b"x" * 10)), A.SpeicherZiel(), gesamt_max=15)
        self.assertEqual(str(e.exception), "zu_gross")
        with self.assertRaises(A.ArchivFehler) as e:
            A.lies_archiv(archiv("D neu/a\n", "D neu/b\n", "D neu/c\n"), A.SpeicherZiel(), dateien_max=2)
        self.assertEqual(str(e.exception), "zu_viele_eintraege")
        self.fehler("D " + "a" * (A.ZEILE_MAX + 5) + "\n", grund="zeile")

    def test_abgebrochene_und_unvollstaendige_stroeme(self):
        self.fehler("D neu/a\n", ende=False, grund="zeile")                                            # kein END
        self.fehler(b"F 10 - neu/a\nabc", ende=False, grund="abgebrochen")                              # Inhalt zu kurz
        self.fehler(b"F 3 - neu/a\nabcd\n", grund="datei_ende")                                         # Inhalt länger als angegeben
        self.fehler(b"F 3 - neu/a\nabc", ende=False, grund="datei_ende")                                # Zeilenende nach dem Inhalt fehlt
        self.fehler(b"D neu/a", ende=False, grund="zeile")                                              # Zeile ohne LF
        self.fehler(b"\xff\xfe\n", grund="zeichensatz")

    def test_nach_end_darf_nichts_kommen(self):
        with self.assertRaises(A.ArchivFehler) as e:
            A.lies_archiv(io.BytesIO(b"D neu\nEND\nD neu/x\n"), A.SpeicherZiel())
        self.assertEqual(str(e.exception), "nach_ende")

    def test_frist(self):
        zeit = [0.0]

        def uhr():
            zeit[0] += 10.0
            return zeit[0]
        with self.assertRaises(A.ArchivFehler) as e:
            A.lies_archiv(archiv("D neu/a\n"), A.SpeicherZiel(), frist=5.0, uhr=uhr)
        self.assertEqual(str(e.exception), "zeit")

    def test_auslassungen_wie_im_laeufer(self):
        import pruefer_lauf as L
        self.assertEqual(A.AUSLASSEN, L.AUSLASSEN)
        self.assertGreaterEqual(A.DATEIEN_MAX, L.DATEIEN_MAX)   # das Archiv darf nie weniger zulassen, als der Läufer prüft


@unittest.skipUnless(LINUX, "braucht Linux, dir_fd und läuft nie als root")
class DateiZielTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="annahme-")
        self.addCleanup(self.tmp.cleanup)
        self.wurzel = os.path.join(self.tmp.name, "ziel")
        os.mkdir(self.wurzel)
        self.ausserhalb = os.path.join(self.tmp.name, "aussen")
        os.mkdir(self.ausserhalb)

    def schreibe(self, *zeilen, **kw):
        ziel = A.DateiZiel(self.wurzel)
        try:
            return A.lies_archiv(archiv(*zeilen, **kw), ziel)
        finally:
            ziel.schliessen()

    def test_dateien_modi_und_verzeichnisse(self):
        alt = os.umask(0o077)   # der verlangte Modus gilt unabhängig von der umask
        self.addCleanup(os.umask, alt)
        self.schreibe(datei("neu/src/a.py", b"x = 1\n"), datei("neu/run.sh", b"#!/bin/sh\n", "x"), "D basis\n")
        p = Path(self.wurzel)
        self.assertEqual((p / "neu/src/a.py").read_bytes(), b"x = 1\n")
        self.assertEqual(stat.S_IMODE((p / "neu/src/a.py").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((p / "neu/run.sh").stat().st_mode), 0o755)
        self.assertTrue((p / "basis").is_dir())

    def test_links_sind_ungueltig_und_bleiben_im_baum(self):
        self.schreibe("L neu/verweis\n")
        link = Path(self.wurzel, "neu/verweis")
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), A.LINK_ZIEL)
        self.assertFalse(link.exists())   # zeigt ins Leere

    def test_bestehender_link_wird_nie_verfolgt(self):
        os.mkdir(os.path.join(self.wurzel, "neu"))
        os.symlink(self.ausserhalb, os.path.join(self.wurzel, "neu", "tor"))   # so ein Link entstünde nur durch einen früheren Fehler
        with self.assertRaises(OSError):
            self.schreibe(datei("neu/tor/boese", b"x"))
        self.assertEqual(os.listdir(self.ausserhalb), [])
        with self.assertRaises(OSError):
            self.schreibe("D neu/tor/unter\n")
        self.assertEqual(os.listdir(self.ausserhalb), [])

    def test_vorhandene_datei_wird_nie_ueberschrieben(self):
        os.mkdir(os.path.join(self.wurzel, "neu"))
        Path(self.wurzel, "neu", "a").write_text("alt")
        with self.assertRaises(OSError):
            self.schreibe(datei("neu/a", b"neu"))
        self.assertEqual(Path(self.wurzel, "neu", "a").read_text(), "alt")

    def test_namen_wie_option_oder_leerzeichen_sind_nur_namen(self):
        self.schreibe(datei("neu/-rf", b"1"), datei("neu/a b", b"2"), datei("neu/;x", b"3"))
        self.assertEqual(sorted(os.listdir(os.path.join(self.wurzel, "neu"))), ["-rf", ";x", "a b"])


@unittest.skipUnless(LINUX, "braucht Linux und läuft nie als root")
class SchreiberTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="annahme-schreiber-")
        self.addCleanup(self.tmp.cleanup)
        self.basis = os.path.join(self.tmp.name, "b")
        self.neu = os.path.join(self.tmp.name, "n")
        os.makedirs(self.basis)
        os.makedirs(self.neu)

    def schreiben(self) -> bytes:
        puffer = io.BytesIO()
        A.schreibe_archiv(puffer, {"basis": self.basis, "neu": self.neu})
        return puffer.getvalue()

    def test_rundreise_mit_leser(self):
        Path(self.neu, "src").mkdir()
        Path(self.neu, "src", "a.py").write_text("x = 1\n")
        Path(self.neu, "run.sh").write_text("#!/bin/sh\n")
        os.chmod(os.path.join(self.neu, "run.sh"), 0o755)
        Path(self.basis, "alt.txt").write_text("alt")
        ziel = A.SpeicherZiel()
        A.lies_archiv(io.BytesIO(self.schreiben()), ziel)
        self.assertEqual(ziel.eintraege["neu/src/a.py"], (b"x = 1\n", False))
        self.assertEqual(ziel.eintraege["neu/run.sh"], (b"#!/bin/sh\n", True))
        self.assertEqual(ziel.eintraege["basis/alt.txt"], (b"alt", False))

    def test_ausgelassene_ordner_fehlen_und_links_werden_gemeldet(self):
        for name in (".git", "node_modules", ".venv", "__pycache__", ".next"):
            Path(self.neu, name).mkdir()
            Path(self.neu, name, "x").write_text("x")
        os.symlink("/etc", os.path.join(self.neu, "ordnerlink"))
        os.symlink("/etc/passwd", os.path.join(self.neu, "dateilink"))
        ziel = A.SpeicherZiel()
        A.lies_archiv(io.BytesIO(self.schreiben()), ziel)
        self.assertEqual({k for k in ziel.eintraege if k.startswith("neu/")}, {"neu/ordnerlink", "neu/dateilink"})
        self.assertEqual(ziel.eintraege["neu/ordnerlink"], "L")
        self.assertEqual(ziel.eintraege["neu/dateilink"], "L")   # es wird nie ein Inhalt hinter einem Link gelesen

    def test_sonderdatei_ist_ein_fehler(self):
        os.mkfifo(os.path.join(self.neu, "pipe"))
        with self.assertRaises(A.ArchivFehler) as e:
            self.schreiben()
        self.assertEqual(str(e.exception), "sonderdatei")


# ----------------------------------------------------------------------------------------------------------------- Dienst
LAUF_OK = """#!/bin/sh
# Attrappe des Läufers: prüft die Aufrufform und schreibt ein Ergebnis
[ "$1" = lauf ] || exit 2
shift
while [ $# -gt 0 ]; do
  case "$1" in
    --basis) basis="$2"; shift 2 ;;
    --neu) neu="$2"; shift 2 ;;
    --runde) runde="$2"; shift 2 ;;
    --ausgabe) aus="$2"; shift 2 ;;
    *) exit 3 ;;
  esac
done
[ -d "$basis" ] && [ -d "$neu" ] || exit 4
zahl=$(find "$neu" -type f | wc -l)
printf '{"runde": %s, "pruefstand": {"bauen": "ok", "tests": "ok"}, "dateien_neu": %s}' "$runde" "$zahl" > "$aus"
exit 0
"""

LAUF_ERGEBNIS = '#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = --ausgabe ] && aus="$2"; shift; done\nprintf \'%s\' \'{inhalt}\' > "$aus"\nexit {code}\n'


class Dienst:
    """Hilfe für die Dienst-Tests: ein Wegwerf-Ordner mit Attrappen-Läufer."""

    def __init__(self, test: unittest.TestCase, lauf: str = LAUF_OK, lauf_max_s: float = 20.0):
        self.tmp = tempfile.TemporaryDirectory(prefix="annahme-dienst-")
        test.addCleanup(self.tmp.cleanup)
        self.lauf = os.path.join(self.tmp.name, "lauf.sh")
        self.schreibe_lauf(lauf)
        self.annahme = os.path.join(self.tmp.name, "annahme")
        self.cfg = N.Konfig(lauf_sh=self.lauf, annahme=self.annahme, hochladen_max_s=30.0, lauf_max_s=lauf_max_s)

    def schreibe_lauf(self, text: str) -> None:
        Path(self.lauf).write_text(text)
        os.chmod(self.lauf, 0o755)

    def anfrage(self, daten: bytes, peer=1000, erlaubt=frozenset({0, 1000})) -> tuple[int, dict]:
        raus = io.BytesIO()
        code = N.bedienen(io.BytesIO(daten), raus, peer, erlaubt, self.cfg)
        return code, json.loads(raus.getvalue())

    def reste(self) -> list[str]:
        return os.listdir(self.annahme) if os.path.isdir(self.annahme) else []


GUT = b"WAMO-PRUEFEN 1 1\n" + b"D basis\nD neu\n" + datei("neu/a.py", b"x = 1\n") + datei("basis/a.py", b"x = 0\n") + b"END\n"


@unittest.skipUnless(LINUX, "braucht Linux und läuft nie als root")
class DienstTests(unittest.TestCase):
    def test_ganzer_weg_liefert_das_ergebnis_des_laeufers_und_raeumt_auf(self):
        d = Dienst(self)
        code, a = d.anfrage(GUT)
        self.assertEqual(code, 0)
        self.assertTrue(a["ok"])
        self.assertEqual(a["ergebnis"], {"runde": 1, "pruefstand": {"bauen": "ok", "tests": "ok"}, "dateien_neu": 1})
        self.assertEqual((a["exit"], a["annahme"]["dateien"]), (0, 2))
        self.assertEqual(d.reste(), [])

    def test_rundenzahl_kommt_aus_dem_kopf(self):
        self.assertEqual(Dienst(self).anfrage(GUT.replace(b"1 1\n", b"1 3\n", 1))[1]["ergebnis"]["runde"], 3)

    def test_durchgefallene_stufe_ist_ein_ergebnis_kein_fehler(self):
        d = Dienst(self, LAUF_ERGEBNIS.format(inhalt='{"pruefstand": {"tests": "fehler"}}', code=1))
        code, a = d.anfrage(GUT)
        self.assertEqual(code, 0)
        self.assertTrue(a["ok"])
        self.assertEqual((a["exit"], a["ergebnis"]["pruefstand"]["tests"]), (1, "fehler"))

    def test_geaenderte_migrationen_gehen_durch_die_probe_und_ein_fehler_macht_die_stufe_rot(self):
        import dataclasses
        mig = (b"WAMO-PRUEFEN 1 1\n" + b"D basis\nD neu\nD neu/supabase\nD neu/supabase/migrations\n"
               + datei("neu/supabase/migrations/0001_a.sql", b"select 1;\n") + b"END\n")
        for wert, fehler, erwartet_exit in (("fehler", "frisch_hin_m0001:zweit", 1), ("ok", "", 0)):
            d = Dienst(self, LAUF_ERGEBNIS.format(inhalt='{"pruefstand": {"migrations_lint": "ok"}, "details": {"migrations_lint": {"treffer_anzahl": 0, "fehler": ""}}}', code=0))
            aufrufe = []
            d.cfg = dataclasses.replace(d.cfg, probe=lambda neu, wurzel, w=wert, f=fehler: aufrufe.append((neu, wurzel)) or {"wert": w, "fehler": f})
            code, a = d.anfrage(mig)
            self.assertEqual(code, 0)
            self.assertEqual(len(aufrufe), 1)
            self.assertTrue(aufrufe[0][0].endswith("neu") and os.path.basename(aufrufe[0][1]).startswith("a."))
            self.assertEqual((a["exit"], a["ergebnis"]["pruefstand"]["migrations_lint"]), (erwartet_exit, "ok" if wert == "ok" else "fehler"))
            self.assertEqual(a["ergebnis"]["details"]["migrations_lint"]["frisch"], wert)
            if fehler:
                self.assertEqual(a["ergebnis"]["details"]["migrations_lint"]["fehler"], fehler)

    def test_ohne_geaenderte_migrationen_gibt_es_keine_probe(self):
        import dataclasses
        d = Dienst(self)
        aufrufe = []
        d.cfg = dataclasses.replace(d.cfg, probe=lambda *a: aufrufe.append(a) or {"wert": "fehler", "fehler": "x"})
        self.assertEqual(d.anfrage(GUT)[1]["exit"], 0)
        self.assertEqual(aufrufe, [])

    def test_fremde_benutzer_werden_vor_dem_ersten_byte_abgewiesen(self):
        d = Dienst(self)
        for peer in (None, 1001, 999, 65534):
            code, a = d.anfrage(GUT, peer=peer)
            self.assertEqual((code, a), (1, {"ok": False, "grund": "benutzer"}))
        self.assertEqual(d.reste(), [])   # nichts angelegt

    def test_kopf(self):
        d = Dienst(self)
        for kopf in (b"", b"\n", b"WAMO-PRUEFEN 1\n", b"WAMO-PRUEFEN 1 0\n", b"WAMO-PRUEFEN 1 4\n", b"WAMO-PRUEFEN 2 1\n", b"WAMO-PRUEFEN 1 1 --x\n",
                     b"wamo-pruefen 1 1\n", b"WAMO-PRUEFEN 1 1", b"x" * 100 + b"\n"):
            with self.subTest(kopf=kopf):
                code, a = d.anfrage(kopf + b"END\n")
                self.assertEqual((code, a["grund"]), (1, "kopf"))
        self.assertEqual(d.reste(), [])

    def test_ungueltiges_archiv_raeumt_auf_und_startet_nichts(self):
        d = Dienst(self)
        marke = os.path.join(d.tmp.name, "gelaufen")
        d.schreibe_lauf(f"#!/bin/sh\ntouch {marke}\n")
        for roh in (b"D neu\nD neu/../x\nEND\n", b"F 5 - neu/a\nab", b"D neu\nEND\nD x\n", b"D neu/a\nD neu/a\nEND\n"):
            code, a = d.anfrage(b"WAMO-PRUEFEN 1 1\n" + roh)
            self.assertEqual((code, a["ok"], a["grund"]), (1, False, "archiv_ungueltig"))
        self.assertFalse(os.path.exists(marke))
        self.assertEqual(d.reste(), [])

    def test_belegt_wird_gemeldet(self):
        d = Dienst(self, "#!/bin/sh\nexit 75\n")
        code, a = d.anfrage(GUT)
        self.assertEqual((code, a), (1, {"ok": False, "grund": "belegt"}))
        self.assertEqual(d.reste(), [])

    def test_lauf_ohne_ergebnis_und_unbrauchbares_ergebnis(self):
        d = Dienst(self, '#!/bin/sh\necho "Fehler im Lauf" >&2\nexit 7\n')
        code, a = d.anfrage(GUT)
        self.assertEqual((code, a["grund"], a["exit"]), (1, "lauf_ohne_ergebnis", 7))
        self.assertIn("Fehler im Lauf", a["detail"])
        for inhalt in ("kein json", "[]", '{"anderes": 1}'):
            d.schreibe_lauf(LAUF_ERGEBNIS.format(inhalt=inhalt, code=0))
            self.assertEqual(d.anfrage(GUT)[1]["grund"], "lauf_ohne_ergebnis", inhalt)
        self.assertEqual(d.reste(), [])

    def test_zeitlimit_des_laufs(self):
        d = Dienst(self, "#!/bin/sh\nsleep 30\n", lauf_max_s=1.0)
        code, a = d.anfrage(GUT)
        self.assertEqual((code, a), (1, {"ok": False, "grund": "zeitlimit"}))
        self.assertEqual(d.reste(), [])

    def test_der_laeufer_bekommt_nur_feste_argumente_und_eine_saubere_umgebung(self):
        d = Dienst(self)
        spur = os.path.join(d.tmp.name, "spur")
        d.schreibe_lauf(f'#!/bin/sh\nprintf "%s\\n" "$@" > {spur}\nenv >> {spur}\nwhile [ $# -gt 0 ]; do [ "$1" = --ausgabe ] && aus="$2"; shift; done\n'
                        f'printf \'{{"pruefstand": {{}}}}\' > "$aus"\n')
        os.environ["GEHEIM_TEST"] = "x"
        self.addCleanup(os.environ.pop, "GEHEIM_TEST", None)
        d.anfrage(GUT)
        zeilen = Path(spur).read_text().splitlines()
        self.assertEqual([zeilen[i] for i in (0, 1, 3, 5, 6, 7)], ["lauf", "--basis", "--neu", "--runde", "1", "--ausgabe"])
        self.assertTrue(all(z.startswith(d.annahme) for z in (zeilen[2], zeilen[4], zeilen[8])))
        self.assertNotIn("GEHEIM_TEST", "\n".join(zeilen))


class QuelltextTests(unittest.TestCase):
    def test_nur_standardbibliothek_und_keine_gedankenstriche(self):
        erlaubt = {"__future__", "argparse", "json", "os", "re", "shutil", "socket", "stat", "struct", "subprocess", "sys", "tempfile", "time", "dataclasses",
                   "typing", "pruefer_archiv", "migration_probe", "pwd"}
        for name in ("pruefer_archiv.py", "pruefer_annahme.py", "pruefer_client.py"):
            text = (PRUEFER / name).read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), text, name)
            self.assertNotIn(chr(0x2013), text, name)
            for zeile in text.splitlines():
                if zeile.startswith(("import ", "from ")):
                    self.assertIn(zeile.split()[1], erlaubt, (name, zeile))

    def test_dienst_ruft_nur_den_festen_laeufer_ohne_shell(self):
        text = (PRUEFER / "pruefer_annahme.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", text)
        self.assertNotIn("os.system", text)
        self.assertNotIn("eval(", text)
        self.assertIn('"/opt/wamo-pruefer/app/wamo-pruefer-lauf.sh"', text)

    def test_festlegungen_stimmen_zwischen_client_dienst_und_unit(self):
        sockunit = (PRUEFER.parent.parent / "pruefer" / "wamo-pruefer-annahme.socket").read_text(encoding="utf-8")
        self.assertIn(f"ListenStream={C.SOCKET}", sockunit)
        for soll in ("SocketUser=wamo-release", "SocketMode=0600", "Accept=yes"):
            self.assertIn(soll, sockunit)
        self.assertEqual(N.ERLAUBTE_NAMEN, ("wamo-release",))
        unit = (PRUEFER.parent.parent / "pruefer" / "wamo-pruefer-annahme@.service").read_text(encoding="utf-8")
        for soll in ("StandardInput=socket", "NoNewPrivileges=yes", "ProtectSystem=strict", "RestrictAddressFamilies=AF_UNIX",
                     "ExecStart=/usr/bin/python3 -I -B /opt/wamo-pruefer/app/pruefer_annahme.py"):
            self.assertIn(soll, unit)


# ----------------------------------------------------------------------------------------------------------------- Client
@unittest.skipUnless(LINUX, "braucht Linux und Unix-Sockets, läuft nie als root")
class ClientTests(unittest.TestCase):
    def setUp(self):
        self.d = Dienst(self)
        self.sockpfad = os.path.join(self.d.tmp.name, "a.sock")
        self.basis = os.path.join(self.d.tmp.name, "b")
        self.neu = os.path.join(self.d.tmp.name, "n")
        for p in (self.basis, self.neu):
            os.makedirs(p)
        Path(self.neu, "a.py").write_text("x = 1\n")
        Path(self.basis, "a.py").write_text("x = 0\n")

    def dienen(self, antwort_fn):
        """Ein Dienst mit Socket im Hintergrund: antwort_fn(conn) bedient genau eine Verbindung."""
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.sockpfad)
        srv.listen(1)
        self.addCleanup(srv.close)

        def lauf():
            conn, _ = srv.accept()
            with conn:
                antwort_fn(conn)
        t = threading.Thread(target=lauf, daemon=True)
        t.start()
        self.addCleanup(t.join, 5)

    def echt(self, conn):
        rein, raus = conn.makefile("rb"), conn.makefile("wb")
        N.bedienen(rein, raus, os.getuid(), frozenset({os.getuid()}), self.d.cfg)
        raus.flush()

    def test_ganzer_weg_ueber_den_socket(self):
        self.dienen(self.echt)
        erg = C.pruefen(self.basis, self.neu, 2, self.sockpfad)
        self.assertEqual(erg["runde"], 2)
        self.assertEqual(erg["pruefstand"], {"bauen": "ok", "tests": "ok"})
        self.assertEqual(self.d.reste(), [])

    def test_ablehnung_wird_zum_fehler_mit_grund(self):
        self.dienen(lambda conn: conn.sendall(b'{"ok": false, "grund": "belegt"}\n'))
        with self.assertRaises(C.PrueferFehler) as e:
            C.pruefen(self.basis, self.neu, 1, self.sockpfad)
        self.assertEqual(e.exception.grund, "belegt")

    def test_dienst_nicht_erreichbar(self):
        with self.assertRaises(C.PrueferFehler) as e:
            C.pruefen(self.basis, self.neu, 1, os.path.join(self.d.tmp.name, "gibt-es-nicht.sock"))
        self.assertEqual(e.exception.grund, "dienst_nicht_erreichbar")

    def test_unbrauchbare_antworten(self):
        for roh in (b"", b"kein json\n", b"[]\n", b'{"ok": "ja"}\n', b'{"ok": true}\n', b'{"ok": true, "ergebnis": {"pruefstand": 3}}\n'):
            with self.subTest(antwort=roh):
                if os.path.exists(self.sockpfad):
                    os.unlink(self.sockpfad)

                def antwort(conn, roh=roh):
                    while conn.recv(65536):   # erst alles lesen (bis shutdown), dann antworten
                        pass
                    conn.sendall(roh)
                self.dienen(antwort)
                with self.assertRaises(C.PrueferFehler) as e:
                    C.pruefen(self.basis, self.neu, 1, self.sockpfad)
                self.assertEqual(e.exception.grund, "antwort_ungueltig")

    def test_rundenzahl_wird_geprueft(self):
        for runde in (0, 4, "1", None):
            with self.assertRaises(ValueError):
                C.pruefen(self.basis, self.neu, runde, self.sockpfad)

    def test_kommandozeile_gibt_exit_je_nach_ergebnis(self):
        self.dienen(self.echt)
        aus = io.StringIO()
        alt = sys.stdout
        sys.stdout = aus
        try:
            code = C.main(["--basis", self.basis, "--neu", self.neu, "--runde", "1", "--socket", self.sockpfad])
        finally:
            sys.stdout = alt
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(aus.getvalue())["pruefstand"]["bauen"], "ok")


if __name__ == "__main__":
    unittest.main()
