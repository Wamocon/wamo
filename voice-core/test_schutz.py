"""Tests der Schutzschicht des Ankers (infra/sokrates2/anker/schutz.py, Befehl klassifiziere, Schritt 3).

Teil 1: das reine Muster- und Pfadmodul. Teil 2: Vorwaerts.klassifizieren mit Attrappen (Status, Commits, Art nur anheben, Trockenlauf). Teil 3: QuelleGit.aenderungen
mit einem echten Git-Repository (auch Löschen, Moduswechsel und Pfade mit Sonderzeichen). Teil 4: die Verdrahtung im Anker (Rechte, Sperre, Protokoll, Antwortfelder).
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent
ANKER = HIER.parent / "infra" / "sokrates2" / "anker"
sys.path.insert(0, str(ANKER))
sys.path.insert(0, str(HIER))

import schutz as S  # noqa: E402
import test_vorwaerts as V  # noqa: E402
import vorwaerts_echt as E  # noqa: E402
from vorwaerts import Vorwaerts, VorwaertsFehler  # noqa: E402

ID = V.ID
A = "a" * 40
B = "b" * 40
C = "c" * 40


def kl(*pfade, status="M"):
    return S.klassifizieren([(status, p.encode("utf-8") if isinstance(p, str) else p) for p in pfade])


class Muster(unittest.TestCase):
    def test_normale_aenderungen(self):
        for p in ("voice-core/bot.py", "voice-core/persona.py", "ui/src/components/Wamo.tsx", "docs/README.md", "README.md", "ui/src/lib/useWamo.ts"):
            self.assertEqual(kl(p)["klasse"], "normal", p)

    def test_sonderpfade_aus_dem_konzept(self):
        for p in ("voice-core/dev_guard.py", "voice-core/auth_gate.py", "voice-core/sitzungen.py", "voice-core/memory.py", "voice-core/schluessel_x.py",
                  "voice-core/modellzugang.py", "voice-core/pc_steuerung.py", "host/wamo_host.py", "desktop/main.js", "infra/sokrates2/release/wamo_release.py",
                  "infra/sokrates2/wamo-core-slots.sh", ".github/workflows/x.yml", "scripts/wamo_nutzer.py", "supabase/migrations/0017_x.sql", "CLAUDE.md",
                  "docs/AGENTS.md", ".claude/settings.json", ".mcp.json", "ui/package.json", "ui/Dockerfile", "ui/next.config.ts", "ui/src/components/Freigaben.tsx",
                  "ui/src/components/kommando/ReleaseKarte.tsx", "infra/sokrates2/claude-code/x.sh", "geraete/x.py", "ui/src/lib/modellzugang_x.ts"):
            self.assertEqual(kl(p)["klasse"], "sonder", p)

    def test_gesperrt_ist_der_anker_selbst(self):
        for p in ("infra/sokrates2/anker/wamo_anker.py", "infra/sokrates2/anker/schritte/x", "infra/sokrates2/anker-einrichten.sh", "infra/sokrates2/anker_rauchtest.py"):
            self.assertEqual(kl(p)["klasse"], "gesperrt", p)

    def test_gesperrt_geht_vor_sonder_und_normal(self):
        e = kl("voice-core/bot.py", "voice-core/auth_gate.py", "infra/sokrates2/anker/wamo_anker.py")
        self.assertEqual(e["klasse"], "gesperrt")
        self.assertEqual(e["treffer"], ["infra/sokrates2/anker/wamo_anker.py", "voice-core/auth_gate.py"])   # der normale Pfad ist kein Treffer

    def test_gross_klein_und_unicode_aendern_nichts(self):
        self.assertEqual(kl("VOICE-CORE/DEV_GUARD.PY")["klasse"], "sonder")
        self.assertEqual(kl("Infra/Sokrates2/ANKER/x")["klasse"], "gesperrt")
        # a + kombinierender Umlautpunkt (NFD) ist derselbe Pfad wie der zusammengesetzte Buchstabe (NFC)
        self.assertEqual(kl("voice-core/schluessel_ä.py")["klasse"], "sonder")

    def test_loeschen_und_typwechsel_zaehlen(self):
        self.assertEqual(S.klassifizieren([("D", b"voice-core/dev_guard.py")])["klasse"], "sonder")
        self.assertEqual(S.klassifizieren([("T", b"voice-core/dev_guard.py")])["klasse"], "sonder")

    def test_unmoegliche_pfade_und_unbekannte_status_gelten_als_gesperrt(self):
        for roh in (b"voice-core/../x", b"/abs", b"a//b", b"a/./b", b"a\\b", b"a\x00b", b"a\x01b", b"\xff\xfe", b"", b"x" * 500):
            e = S.klassifizieren([("M", roh)])
            self.assertEqual(e["klasse"], "gesperrt", roh[:20])
            self.assertTrue(all(32 <= ord(c) < 127 for t in e["treffer"] for c in t) or roh == b"", e["treffer"])
        for status in ("R", "C", "U", "X", "B", "MM", ""):
            self.assertEqual(S.klassifizieren([(status, b"voice-core/bot.py")])["klasse"], "gesperrt", status)

    def test_muster_sind_keine_teilstuecke(self):
        self.assertEqual(kl("voice-core/dev_guard.py.bak")["klasse"], "normal")
        self.assertEqual(kl("voice-core/xdev_guard.py")["klasse"], "normal")
        self.assertEqual(kl("voice-core/wamo-job")["klasse"], "sonder")
        self.assertEqual(kl("docs/hostile.md")["klasse"], "normal")   # host/** gilt ab der Wurzel, nicht überall
        self.assertEqual(kl("docs/host/x.md")["klasse"], "normal")

    def test_treffer_sind_sortiert_eindeutig_und_gekappt(self):
        viele = [f"scripts/x{i:03d}.py" for i in range(250)]
        e = kl(*viele, *viele)
        self.assertEqual(e["treffer_anzahl"], S.TREFFER_MAX)
        self.assertEqual(len(e["treffer"]), S.TREFFER_MAX)
        self.assertEqual(e["treffer"], sorted(e["treffer"]))
        andere = kl(*viele[:-1], "scripts/zzz.py")
        self.assertNotEqual(e["treffer_sha"], andere["treffer_sha"])   # die Prüfsumme deckt auch, was gekappt wurde
        self.assertEqual(kl("voice-core/bot.py")["treffer"], [])

    def test_zusatzmuster_erweitern_nur(self):
        self.assertEqual(S.klassifizieren([("M", b"voice-core/bot.py")], zusatz_sonder=("voice-core/bot.py",))["klasse"], "sonder")
        self.assertEqual(S.klassifizieren([("M", b"voice-core/dev_guard.py")], zusatz_sonder=("x/**",))["klasse"], "sonder")   # der Boden bleibt

    def test_ausgabe_von_git_wird_streng_gelesen(self):
        self.assertEqual(S.aenderungen_lesen(b"M\0a.py\0D\0b.py\0"), [("M", b"a.py"), ("D", b"b.py")])
        self.assertEqual(S.aenderungen_lesen(b""), [])
        with self.assertRaises(ValueError):
            S.aenderungen_lesen(b"M\0a.py\0D\0")


class FakeDbKl(V.FakeDb):
    def __init__(self, **kw):
        super().__init__()
        self.zeile.update(status="pruefung3", art="normal", basis_commit=A, c1_commit=B, r_commit=None, v_commit=None)
        self.zeile.update(kw)
        self.klass: list[tuple] = []

    def anfrage_stand(self, k):
        z = self.zeile
        return {n: z.get(n) for n in ("status", "art", "basis_commit", "c1_commit", "r_commit", "v_commit")} if k == ID else None

    def klassifizierung_setzen(self, k, art, treffer):
        self.klass.append((art, treffer))
        self.zeile["art"] = art


class FakeQuelleKl(V.FakeQuelle):
    def __init__(self, aenderungen):
        super().__init__({})
        self._a = aenderungen
        self.diffs: list[tuple] = []

    def aenderungen(self, basis, neu):
        self.diffs.append((basis, neu))
        return self._a


class Klassifizieren(unittest.TestCase):
    def bauen(self, aenderungen, **kw):
        db, q = FakeDbKl(**kw), FakeQuelleKl(aenderungen)
        return Vorwaerts(db, q, V.FakeWerk(), tempfile.gettempdir()), db, q

    def test_normal_schreibt_art_normal_und_keine_treffer(self):
        v, db, q = self.bauen([("M", b"voice-core/bot.py")])
        erg = v.ausfuehren("klassifiziere", ID)
        self.assertEqual((erg["klasse"], erg["art"], erg["treffer_anzahl"]), ("normal", "normal", 0))
        self.assertEqual(db.klass, [("normal", [])])

    def test_sonder_und_gesperrt_schreiben_art_sonder_mit_treffern(self):
        v, db, _ = self.bauen([("M", b"voice-core/dev_guard.py")])
        self.assertEqual(v.ausfuehren("klassifiziere", ID)["klasse"], "sonder")
        self.assertEqual(db.klass, [("sonder", ["voice-core/dev_guard.py"])])
        v, db, _ = self.bauen([("M", b"infra/sokrates2/anker/wamo_anker.py")])
        erg = v.ausfuehren("klassifiziere", ID)
        self.assertEqual((erg["klasse"], erg["art"]), ("gesperrt", "sonder"))   # die Datenbank kennt nur normal und sonder

    def test_die_art_wird_nie_gesenkt(self):
        v, db, _ = self.bauen([("M", b"voice-core/bot.py")], art="sonder")
        self.assertEqual(v.ausfuehren("klassifiziere", ID)["art"], "sonder")
        self.assertEqual(db.klass, [("sonder", [])])

    def test_der_juengste_stand_wird_verglichen(self):
        for felder, erwartet in (({}, B), ({"r_commit": C}, C), ({"r_commit": C, "v_commit": "d" * 40}, "d" * 40)):
            v, _, q = self.bauen([], **felder)
            v.ausfuehren("klassifiziere", ID)
            self.assertEqual(q.diffs, [(A, erwartet)], felder)

    def test_nur_in_den_pruefstufen(self):
        for status in ("angefragt", "uebernommen", "versionswahl", "wartet_freigabe", "freigegeben", "veroeffentlicht", "bereitgestellt"):
            v, db, _ = self.bauen([])
            db.zeile["status"] = status
            with self.assertRaises(VorwaertsFehler) as e:
                v.ausfuehren("klassifiziere", ID)
            self.assertEqual(e.exception.code, "status_falsch", status)
            self.assertEqual(db.klass, [])

    def test_fehlende_commits_und_unbekannte_anfrage(self):
        v, db, _ = self.bauen([], basis_commit=None)
        with self.assertRaises(VorwaertsFehler) as e:
            v.ausfuehren("klassifiziere", ID)
        self.assertEqual(e.exception.code, "anfrage_unvollstaendig")
        v, db, _ = self.bauen([])
        for kennung, code in (("nix", "anfrage_ungueltig"), ("1" * 8 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 4 + "-" + "1" * 12, "anfrage_unbekannt")):
            with self.assertRaises(VorwaertsFehler) as e:
                v.ausfuehren("klassifiziere", kennung)
            self.assertEqual(e.exception.code, code)

    def test_trockenlauf_schreibt_nichts(self):
        v, db, _ = self.bauen([("M", b"voice-core/dev_guard.py")])
        erg = v.ausfuehren("klassifiziere", ID, True)
        self.assertEqual(erg["klasse"], "sonder")
        self.assertEqual(db.klass, [])


@unittest.skipUnless(shutil.which("git"), "braucht git")
class DiffAusGit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = os.path.join(self.tmp.name, "w")
        os.makedirs(os.path.join(self.w, "voice-core"))
        os.makedirs(os.path.join(self.w, "docs"))
        Path(self.w, "voice-core", "bot.py").write_text("x = 1\n")
        Path(self.w, "voice-core", "dev_guard.py").write_text("g = 1\n")
        Path(self.w, "voice-core", "start.sh").write_text("#!/bin/sh\n")
        Path(self.w, "docs", "a.md").write_text("a\n")
        V.git(self.w, "init", "-q", "-b", "main")
        V.git(self.w, "add", "-A")
        V.git(self.w, "commit", "-q", "-m", "eins")
        self.alt = V.git(self.w, "rev-parse", "HEAD").decode().strip()

    def neu(self):
        V.git(self.w, "add", "-A")
        V.git(self.w, "commit", "-q", "-m", "zwei")
        n = V.git(self.w, "rev-parse", "HEAD").decode().strip()
        repo = os.path.join(self.tmp.name, "r.git")
        shutil.rmtree(repo, ignore_errors=True)
        import subprocess
        subprocess.run(["git", "clone", "-q", "--bare", self.w, repo], check=True, capture_output=True)
        return E.QuelleGit(repo, git_umgebung_fn=lambda: V.git_env(self.tmp.name), git="git"), n

    def test_aendern_loeschen_neu_und_umbenennen(self):
        Path(self.w, "voice-core", "bot.py").write_text("x = 2\n")
        os.remove(os.path.join(self.w, "voice-core", "dev_guard.py"))
        Path(self.w, "docs", "neu.md").write_text("n\n")
        os.rename(os.path.join(self.w, "docs", "a.md"), os.path.join(self.w, "docs", "b.md"))
        q, n = self.neu()
        a = dict((p, s) for s, p in q.aenderungen(self.alt, n))
        self.assertEqual(a, {b"voice-core/bot.py": "M", b"voice-core/dev_guard.py": "D", b"docs/neu.md": "A", b"docs/a.md": "D", b"docs/b.md": "A"})
        self.assertEqual(S.klassifizieren(q.aenderungen(self.alt, n))["klasse"], "sonder")   # das Löschen des Wächters zählt

    def test_moduswechsel_und_sonderzeichen_im_namen(self):
        # Windows kennt kein Ausführungsrecht (Git für Windows setzt core.filemode=false): dort prüft der Test nur den Namen,
        # den Moduswechsel prüft er unter Linux, wo der Anker läuft
        moduswechsel = os.name != "nt"
        if moduswechsel:
            os.chmod(os.path.join(self.w, "voice-core", "start.sh"), 0o755)
        Path(self.w, "docs", "ä öü.md").write_text("u\n")
        q, n = self.neu()
        a = dict((p, s) for s, p in q.aenderungen(self.alt, n))
        if moduswechsel:
            self.assertEqual(a[b"voice-core/start.sh"], "M")
        self.assertIn("docs/ä öü.md".encode("utf-8"), a)

    def test_ungueltige_commits_werden_abgewiesen(self):
        Path(self.w, "docs", "c.md").write_text("c\n")
        q, n = self.neu()
        for schlecht in ("HEAD", "main", "-x", n[:39], "0" * 40):
            with self.assertRaises(VorwaertsFehler):
                q.aenderungen(schlecht, n)
            with self.assertRaises(VorwaertsFehler):
                q.aenderungen(n, schlecht)


class KlassifiziereImAnker(unittest.TestCase):
    def setUp(self):
        T = V.VorwaertsImAnker
        self.T = T
        import test_anker as TA

        class Fall(TA.AnkerTest):
            def runTest(self):
                pass
        self.f = Fall()
        self.f.setUp()
        self.addCleanup(self.f.tmp.cleanup)
        self.f.schritte_anlegen(namen=("rueckweg-ui", "rueckweg-kern", "kern-slots"))
        self.aufrufe: list[tuple] = []
        self.fehler = None

        class Echt:
            def ausfuehren(inner, befehl, kennung, trocken):
                self.aufrufe.append((befehl, kennung, trocken))
                if self.fehler:
                    raise self.fehler
                return {"klasse": "sonder", "art": "sonder", "treffer_anzahl": 1, "treffer_sha": "e" * 64, "treffer": ["geheim/pfad"], "trocken": trocken or None}
        self.A = TA.A
        self.f.a = self.f.neuer_anker(vorwaerts_fabrik=lambda: Echt())

    def test_release_dienst_darf_fragen_antwort_enthaelt_nur_die_vier_felder(self):
        r = self.f.sende(f"klassifiziere {ID}", "wamo-release")
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(r["ergebnis"]), ["art", "klasse", "treffer_anzahl", "treffer_sha"])
        self.assertEqual(self.aufrufe, [("klassifiziere", ID, False)])
        self.assertIn("klassifiziere", [z["ereignis"] for z in self.f.audit()])

    def test_trockenlauf_und_sperre(self):
        self.assertTrue(self.f.sende(f"klassifiziere {ID} --trocken", "wamo-release")["ok"])
        self.assertEqual(self.aufrufe[-1], ("klassifiziere", ID, True))
        self.f.sende("sperre an")
        self.assertTrue(self.f.sende(f"klassifiziere {ID}", "wamo-release")["ok"])   # ändert nichts am System: die Sperre hält nur die Vorwärtsbefehle an

    def test_fehler_kommen_als_code(self):
        self.fehler = self.A.VorwaertsFehler("status_falsch", "angefragt")
        r = self.f.sende(f"klassifiziere {ID}", "wamo-release")
        self.assertEqual((r["ok"], r["grund"], r["detail"]), (False, "vorwaerts_fehlgeschlagen", "status_falsch angefragt"))

    def test_fremde_benutzer_und_schlechte_argumente(self):
        for arg in ("", "x y", "../x"):
            self.assertFalse(self.f.sende(f"klassifiziere {arg}".strip(), "wamo-release")["ok"], arg)
        self.assertEqual(self.aufrufe, [])


class Quelltext(unittest.TestCase):
    def test_schutz_hat_keine_ein_und_ausgabe(self):
        t = (ANKER / "schutz.py").read_text(encoding="utf-8")
        for verboten in ("import os", "import subprocess", "import socket", "open(", "eval(", "exec("):
            self.assertNotIn(verboten, t, verboten)

    def test_installer_und_rauchtest_kennen_schutz(self):
        t = (ANKER.parent / "anker-einrichten.sh").read_text(encoding="utf-8")
        self.assertIn("anker/schutz.py", t)


if __name__ == "__main__":
    unittest.main()
