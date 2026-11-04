"""Tests der Anzeigelogik des Release-Dienstes (infra/sokrates2/release/release_anzeige.py und die Git-Abfragen diff_uebersicht und titel_stufen in quelle.py).

Die Git-Tests laufen gegen echte Wegwerf-Repositories im Temp-Ordner (git muss vorhanden sein). Kein Netz, kein Server.

Ausführen:  uv run python -m pytest -q test_release_anzeige.py   (im Ordner voice-core/)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "release"
sys.path.insert(0, str(RELEASE))

import quelle as Q  # noqa: E402
import release_anzeige as RA  # noqa: E402

GIT = shutil.which("git")
if GIT:
    Q.GIT = GIT   # unter Windows liegt git nicht in /usr/bin
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args) -> str:
    return subprocess.run([GIT, *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True).stdout.strip()


class Repo:
    """Ein Wegwerf-Repository mit Hilfen zum Schreiben und Committen."""

    def __init__(self, wurzel: str):
        self.pfad = os.path.join(wurzel, "repo")
        os.makedirs(self.pfad)
        git(self.pfad, "init", "-q", "-b", "main")
        git(self.pfad, "config", "core.autocrlf", "false")
        self.spiegel = Q.Spiegel(os.path.join(self.pfad, ".git"), self.pfad, datei_erlaubt=True)

    def schreibe(self, rel: str, inhalt) -> None:
        p = Path(self.pfad, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(inhalt if isinstance(inhalt, bytes) else inhalt.encode())

    def entferne(self, rel: str) -> None:
        Path(self.pfad, rel).unlink()

    def commit(self, nachricht: str = "chore: x") -> str:
        git(self.pfad, "add", "-A")
        git(self.pfad, "commit", "-q", "--allow-empty", "-m", nachricht)
        return git(self.pfad, "rev-parse", "HEAD")


@unittest.skipUnless(GIT, "git nicht vorhanden")
class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="anzeige-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repo(self.tmp.name)

    def zaehlen(self, basis: str, titel: list) -> dict:
        """Legt je Titel einen Commit an und zählt gegen die Basis."""
        for i, t in enumerate(titel):
            self.repo.schreibe(f"src/datei{i}.py", f"x = {i}\n")
            self.repo.commit(t)
        return self.repo.spiegel.titel_stufen(basis, git(self.repo.pfad, "rev-parse", "HEAD"))

    # ------------------------------------------------------------ Diff
    def test_diff_status_und_zeilen(self):
        self.repo.schreibe("a.txt", "1\n2\n3\n")
        self.repo.schreibe("b.txt", "x\ny\n")
        basis = self.repo.commit()
        self.repo.schreibe("a.txt", "1\n3\n4\n5\n")   # +2 -1
        self.repo.schreibe("c.txt", "n1\nn2\nn3\nn4\n")   # neu, +4
        self.repo.entferne("b.txt")   # -2
        neu = self.repo.commit()
        eintraege, abgeschnitten = self.repo.spiegel.diff_uebersicht(basis, neu)
        self.assertFalse(abgeschnitten)
        self.assertEqual(sorted(eintraege), [("A", "c.txt", 4, 0), ("D", "b.txt", 0, 2), ("M", "a.txt", 2, 1)])
        block = RA.diff_block(eintraege, abgeschnitten)
        self.assertEqual((block["anzahl"], block["hinzu"], block["weg"], block["binaer"], block["gekappt"]), (3, 6, 3, 0, False))
        self.assertEqual({d["p"]: (d["s"], d["a"], d["d"]) for d in block["dateien"]}, {"a.txt": ("M", 2, 1), "b.txt": ("D", 0, 2), "c.txt": ("A", 4, 0)})
        self.assertEqual(set(block["dateien"][0]), {"s", "p", "a", "d"})

    def test_diff_loeschen_und_binaer(self):
        self.repo.schreibe("bild.bin", b"\x00\x01\x02" * 50)
        self.repo.schreibe("text.txt", "a\nb\nc\n")
        basis = self.repo.commit()
        self.repo.entferne("text.txt")
        self.repo.schreibe("neu.bin", b"\x00\xff" * 40)
        self.repo.schreibe("bild.bin", b"\x00\x01\x09" * 50)
        neu = self.repo.commit()
        eintraege, _ = self.repo.spiegel.diff_uebersicht(basis, neu)
        stand = {p: (s, a, d) for s, p, a, d in eintraege}
        self.assertEqual(stand["text.txt"], ("D", 0, 3))
        self.assertEqual(stand["neu.bin"], ("A", None, None))
        self.assertEqual(stand["bild.bin"], ("M", None, None))
        block = RA.diff_block(eintraege)
        self.assertEqual((block["anzahl"], block["hinzu"], block["weg"], block["binaer"]), (3, 0, 3, 2))
        binaer = [d for d in block["dateien"] if d["p"].endswith(".bin")]
        self.assertTrue(binaer and all(d["a"] is None and d["d"] is None for d in binaer))

    def test_diff_wird_gekappt(self):
        basis = self.repo.commit("chore: start")
        for i in range(30):
            self.repo.schreibe(f"d/f{i:02d}.txt", "zeile\n" * (i + 1))
        neu = self.repo.commit()
        eintraege, abgeschnitten = self.repo.spiegel.diff_uebersicht(basis, neu)
        self.assertEqual((len(eintraege), abgeschnitten), (30, False))
        block = RA.diff_block(eintraege, abgeschnitten)
        self.assertEqual((block["anzahl"], len(block["dateien"]), block["gekappt"]), (30, RA.DATEIEN_MAX, True))
        self.assertEqual(block["hinzu"], sum(range(1, 31)))   # die Summe zählt alle Dateien, nicht nur die angezeigten
        self.assertEqual(block["dateien"][0]["p"], "d/f29.txt")   # die größte Änderung zuerst
        kurz, abgeschnitten = self.repo.spiegel.diff_uebersicht(basis, neu, 5)
        self.assertEqual((len(kurz), abgeschnitten), (5, True))
        self.assertTrue(RA.diff_block(kurz, abgeschnitten)["gekappt"])

    def test_diff_pfad_mit_sonderzeichen_aus_git(self):
        basis = self.repo.commit("chore: start")
        self.repo.schreibe("größe (1) [x].txt", "a\n")
        neu = self.repo.commit()
        eintraege, _ = self.repo.spiegel.diff_uebersicht(basis, neu)
        self.assertEqual(len(eintraege), 1)
        p = RA.diff_block(eintraege)["dateien"][0]["p"]
        self.assertTrue(p.isascii() and "?" in p)

    def test_commit_wird_geprueft(self):
        for schlecht in ("HEAD", "--output=x", "main", "a" * 39, ""):
            with self.assertRaises(Q.QuelleFehler):
                self.repo.spiegel.diff_uebersicht(schlecht, "b" * 40)
            with self.assertRaises(Q.QuelleFehler):
                self.repo.spiegel.titel_stufen("a" * 40, schlecht)
        with self.assertRaises(Q.QuelleFehler):   # unbekannter Commit: kurzes Schlüsselwort, kein Git-Text
            self.repo.spiegel.diff_uebersicht("a" * 40, "b" * 40)

    # ------------------------------------------------------------ Versionsvorschlag
    def test_vorschlag_feat_minor(self):
        basis = self.repo.commit("chore: start")
        z = self.zaehlen(basis, ["feat(ui): neue Karte", "fix: Tippfehler", "docs: Hinweis"])
        self.assertEqual((z["feat"], z["fix"], z["sonst"], z["unkonventionell"], z["bruch"]), (1, 1, 1, 0, 0))
        e = RA.empfehlung(z, True, "0.1.0")
        self.assertEqual((e["stufe"], e["version"], e["major_null"]), ("minor", "0.2.0", False))
        self.assertEqual((e["feat"], e["fix"], e["sonst"], e["unkonventionell"]), (1, 1, 1, 0))

    def test_vorschlag_fix_patch(self):
        basis = self.repo.commit("chore: start")
        z = self.zaehlen(basis, ["fix: Fehler", "perf(core): schneller", "test: mehr"])
        self.assertEqual((z["fix"], z["sonst"]), (2, 1))
        e = RA.empfehlung(z, True, "0.1.0")
        self.assertEqual((e["stufe"], e["version"]), ("patch", "0.1.1"))

    def test_vorschlag_bruch_major(self):
        basis = self.repo.commit("chore: start")
        z = self.zaehlen(basis, ["feat!: Schnittstelle neu"])
        self.assertEqual(z["bruch"], 1)
        e = RA.empfehlung(z, True, "0.1.0")
        self.assertEqual((e["stufe"], e["version"], e["major_null"]), ("major", "1.0.0", True))
        self.repo.schreibe("src/x.py", "y\n")
        self.repo.commit("fix: kleiner Fehler\n\nBREAKING CHANGE: Feld entfernt")
        z2 = self.repo.spiegel.titel_stufen(basis, git(self.repo.pfad, "rev-parse", "HEAD"))
        self.assertEqual(z2["bruch"], 2)
        e2 = RA.empfehlung(z2, True, "1.4.2")
        self.assertEqual((e2["stufe"], e2["version"], e2["major_null"]), ("major", "2.0.0", False))   # nur der Sprung von 0.y.z auf 1.0.0 braucht die Bestätigung

    def test_nur_docs_keine_erhoehung(self):
        self.repo.schreibe("docs/a.md", "a\n")
        basis = self.repo.commit("chore: start")
        self.repo.schreibe("docs/a.md", "a\nb\n")
        self.repo.commit("docs: ergänzt")
        self.repo.schreibe("docs/neu/b.md", "b\n")
        neu = self.repo.commit("chore: Notiz")
        z = self.repo.spiegel.titel_stufen(basis, neu)
        eintraege, abgeschnitten = self.repo.spiegel.diff_uebersicht(basis, neu)
        self.assertFalse(RA.ausserhalb_von_docs(eintraege, abgeschnitten))
        e = RA.empfehlung(z, RA.ausserhalb_von_docs(eintraege, abgeschnitten), "0.1.0")
        self.assertEqual((e["stufe"], e["version"], e["sonst"]), ("keine", "0.1.0", 2))
        # Dieselben Titel, aber außerhalb von docs/ geändert: mindestens Patch
        self.assertEqual(RA.empfehlung(z, True, "0.1.0")["stufe"], "patch")
        self.assertTrue(RA.ausserhalb_von_docs([("M", "docs/a.md", 1, 0), ("A", "ui/b.ts", 1, 0)]))
        self.assertTrue(RA.ausserhalb_von_docs([("M", "docs/a.md", 1, 0)], abgeschnitten=True))
        self.assertTrue(RA.ausserhalb_von_docs([("M", "docsx/a.md", 1, 0)]))   # nur der Ordner docs/ zählt

    def test_unkonventionelle_titel_warnen(self):
        basis = self.repo.commit("chore: start")
        z = self.zaehlen(basis, ["Update stuff", "WIP", "feature: falscher Typ", "docs: ok"])
        self.assertEqual((z["unkonventionell"], z["sonst"], z["feat"]), (3, 1, 0))
        e = RA.empfehlung(z, False, "0.3.4")
        self.assertEqual((e["stufe"], e["version"], e["unkonventionell"]), ("patch", "0.3.5", 3))

    def test_titel_kommen_nie_nach_aussen(self):
        basis = self.repo.commit("chore: start")
        z = self.zaehlen(basis, ["feat: GEHEIMER-TITEL-4711", "Passwort hunter2 im Titel"])
        self.repo.schreibe("src/z.py", "z\n")
        neu = self.repo.commit("fix: titel\n\nGEHEIMER-KOERPER-0815")
        z = self.repo.spiegel.titel_stufen(basis, neu)
        self.assertTrue(all(isinstance(v, int) and not isinstance(v, bool) for v in z.values()))
        eintraege, ab = self.repo.spiegel.diff_uebersicht(basis, neu)
        alles = json.dumps([z, RA.empfehlung(z, True, "0.1.0"), RA.pruefung_block(RA.diff_block(eintraege, ab), None, RA.empfehlung(z, True, "0.1.0"))])
        for geheim in ("GEHEIMER", "hunter2", "Passwort", "Update"):
            self.assertNotIn(geheim, alles)


class ReineLogikTests(unittest.TestCase):
    def test_pfade_ohne_sonderzeichen(self):
        boese = ["src/ä ö ü ß.py", "a\x1b[31mrot\x00.txt", "x;rm -rf $(id)/`y`.py", "evil‮txt.exe", "tab\tname\nzeile.py", "ok/pfad-1_2.v3/datei.ts", "emoji\U0001F600.md",
                 "a" * 300 + ".py"]
        erlaubt = re.compile(r"[A-Za-z0-9 _.:/=,+@?-]{0,70}")
        for p in boese:
            s = RA.pfad_sauber(p)
            self.assertTrue(erlaubt.fullmatch(s), (p, s))
            self.assertTrue(s.isascii() and len(s) <= 70)
        block = RA.diff_block([("M", p, 1, 1) for p in boese])
        self.assertTrue(all(erlaubt.fullmatch(d["p"]) for d in block["dateien"]))
        self.assertEqual(RA.pfad_sauber("ok/pfad-1_2.v3/datei.ts"), "ok/pfad-1_2.v3/datei.ts")

    def test_zahlen_haben_obergrenzen_und_status_ist_begrenzt(self):
        block = RA.diff_block([("M", "a", 10**12, -5), ("X", "b", True, 3), ("R", "c", "7", 7), ("T", "d", 1, 1)])
        stand = {d["p"]: d for d in block["dateien"]}
        self.assertEqual((stand["a"]["a"], stand["a"]["d"]), (RA.ZAHL_MAX, 0))
        self.assertEqual(stand["b"]["s"], "M")
        self.assertEqual(stand["c"]["s"], "M")
        self.assertEqual(stand["d"]["s"], "T")
        self.assertTrue(all(isinstance(d["a"], int) and d["s"] in "AMDT" for d in block["dateien"]))

    def test_titel_regeln(self):
        z = RA.titel_zaehlen(["feat: a", "FIX(x): b", "fix!: c", "docs: d", "chore(ci): e", "build: f", "style: g", "refactor: h", "ci: i", "test: j",
                              "feat:ohne Leerzeichen", "revert: x", "", "   ", "Merge branch 'x'"])
        self.assertEqual((z["commits"], z["feat"], z["fix"], z["sonst"], z["bruch"], z["unkonventionell"]), (13, 1, 2, 7, 1, 3))
        self.assertEqual(RA.titel_zaehlen(["x: y\n\nBREAKING-CHANGE: z"])["bruch"], 1)
        self.assertEqual(RA.titel_zaehlen(["docs: y\n\nText BREAKING CHANGE: mitten im Satz"])["bruch"], 0)

    def test_empfehlung_ohne_basisversion_und_dienst_zahlen_bleiben_ganz(self):
        e = RA.empfehlung({"feat": 1}, True, None)
        self.assertEqual((e["stufe"], e["version"]), ("minor", ""))
        e = RA.empfehlung({"feat": "viele", "fix": -3, "bruch": True}, False, "0.1.0")
        self.assertEqual((e["stufe"], e["feat"], e["fix"]), ("keine", 0, 0))
        self.assertEqual(set(RA.empfehlung({}, False, "1.0.0")), {"stufe", "version", "feat", "fix", "sonst", "unkonventionell", "major_null"})

    def test_wirkung_block_aus_gemerkten_funden(self):
        w = RA.wirkung_block({"treffer": [{"pfad": "host/a.py", "zeile": 3, "art": "netzziel", "detail": "example.org"},
                                          {"pfad": "host/b.py", "zeile": 9, "art": "geheimnisname", "detail": "core.env"},
                                          {"pfad": "host/c.py", "zeile": 1, "art": "netzziel", "detail": "x"}],
                              "treffer_anzahl": 3, "sonder": True, "neue_pakete": [["rich", "13.0.0"]], "windows_pruefung_noetig": True})
        self.assertEqual((w["anzahl"], w["nach_art"], w["sonder_hinweis"], w["neue_pakete"], w["windows"]), (3, {"netzziel": 2, "geheimnisname": 1}, True, 1, True))
        self.assertEqual(w["treffer"][0], {"p": "host/a.py", "z": 3, "a": "netzziel", "d": "example.org"})
        self.assertIsNone(RA.wirkung_block("kaputt"))
        self.assertEqual(RA.wirkung_block({})["treffer"], [])

    def test_anzeige_bleibt_unter_3000_byte(self):
        dateien = [("M", ("verzeichnis/" * 6) + f"datei{i}.py", 999_999, 999_999) for i in range(60)]
        treffer = [{"pfad": ("host/" * 20) + f"{i}.py", "zeile": 999_999, "art": "geheimnisname", "detail": "x" * 80} for i in range(50)]
        wirkung = RA.wirkung_block({"treffer": treffer, "treffer_anzahl": 50, "sonder": True, "neue_pakete": 200, "windows_pruefung_noetig": True})
        diff = RA.diff_block(dateien)
        empf = RA.empfehlung({"feat": 99_999, "fix": 99_999, "sonst": 99_999, "unkonventionell": 99_999, "bruch": 1}, True, "999999999.999999999.999999999")
        voll = {"diff": diff, "wirkung": wirkung, "empfehlung": empf}
        self.assertGreater(len(json.dumps(voll, separators=(",", ":"))), RA.PRUEFUNG_MAX_BYTES)   # ungekappt wäre er zu groß
        block = RA.pruefung_block(diff, wirkung, empf)
        self.assertLessEqual(len(json.dumps(block, separators=(",", ":")).encode("ascii")), RA.PRUEFUNG_MAX_BYTES)
        self.assertEqual(set(block), {"diff", "wirkung", "empfehlung"})
        self.assertEqual(block["diff"]["anzahl"], 60)   # die Zähler bleiben
        self.assertTrue(block["diff"]["gekappt"])
        self.assertEqual(len(diff["dateien"]), RA.DATEIEN_MAX)   # das Original bleibt unverändert

    def test_kappen_zuerst_treffer_dann_dateien_dann_zaehler(self):
        diff = RA.diff_block([("M", f"ordner/unterordner/datei{i:02d}.py", i, i) for i in range(20)])
        wirkung = RA.wirkung_block({"treffer": [{"pfad": f"host/pfad/zur/datei{i}.py", "zeile": i, "art": "netzziel", "detail": "host.example"} for i in range(15)],
                                    "treffer_anzahl": 15})
        empf = RA.empfehlung({"fix": 1}, True, "0.1.0")
        voll = {"diff": diff, "wirkung": wirkung, "empfehlung": empf}
        gross = len(json.dumps(voll, separators=(",", ":")))
        # knapp unter der vollen Größe: nur Treffer fallen weg
        k = RA.kappen(voll, gross - 10)
        self.assertLess(len(k["wirkung"]["treffer"]), 15)
        self.assertEqual(len(k["diff"]["dateien"]), 20)
        # nur wenig Platz: keine Treffer, weniger Dateien, die Zähler bleiben
        k = RA.kappen(voll, 700)
        self.assertEqual(k["wirkung"]["treffer"], [])
        self.assertLess(len(k["diff"]["dateien"]), 20)
        self.assertTrue(k["diff"]["gekappt"])
        self.assertEqual((k["diff"]["anzahl"], k["wirkung"]["anzahl"]), (20, 15))
        # sehr wenig Platz: nur Zähler
        k = RA.kappen(voll, 320)
        self.assertEqual((k["wirkung"]["treffer"], k["diff"]["dateien"]), ([], []))
        self.assertIn("empfehlung", k)

    def test_leerer_block_gibt_none(self):
        self.assertIsNone(RA.pruefung_block(None, None, None))


if __name__ == "__main__":
    unittest.main()
