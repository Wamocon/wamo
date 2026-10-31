"""Tests für die GitHub-Werkzeuge des Hosts: github_repo_anlegen (critical, nur per Freigabe-Karte) und git_klonen.

gh und git laufen nie wirklich: _gh_lauf und die Programmsuche sind Attrappen, der Benutzerordner ist ein Wegwerfordner.
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wamo_host as host

SCHLUESSEL = "k" * 43
GH = r"C:\Fake\gh.exe"
GEHEIM = "ghp_" + "A1b2C3d4" * 4


def _umgebung(**werte):
    """Umgebung des Hosts für einen Test: WAMO_GITHUB_* und Token setzen, sonst nichts."""
    basis = {k: v for k, v in os.environ.items() if not k.startswith("WAMO_GITHUB_")}
    basis.update(werte)
    return mock.patch.dict(os.environ, basis, clear=True)


class _Basis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve()
        self.projekte = self.home / "Projekte"
        wurzeln = tuple(self.home / n for n in ("Projekte", "Documents", "Dokumente", "Desktop", "Downloads"))
        self.audit: list[dict] = []
        self.laeufe: list[tuple[list[str], int]] = []
        self.antwort = (0, "", "")
        for p in (
            mock.patch.object(host, "HOME", self.home), mock.patch.object(host, "PROJEKTE_DIR", self.projekte),
            mock.patch.object(host, "ORDNER_WURZELN", wurzeln), mock.patch.object(host, "_audit", side_effect=self.audit.append),
            mock.patch.object(host, "_gh_pfad", return_value=GH), mock.patch.object(host, "_git_pfad", return_value="git"),
            mock.patch.object(host, "_gh_lauf", side_effect=self._lauf), mock.patch.object(host, "FREIGABE_SCHLUESSEL", SCHLUESSEL),
            mock.patch.object(host, "_ist_gesperrt", return_value=False),  # nie vom echten Bildschirm abhängig
        ):
            p.start()
            self.addCleanup(p.stop)
        host.PENDING.clear()
        self.addCleanup(host.PENDING.clear)
        host._rate_zeiten.clear()
        self.addCleanup(host._rate_zeiten.clear)

    def _lauf(self, argv, timeout):
        self.laeufe.append((list(argv), timeout))
        antwort = self.antwort
        if isinstance(antwort, Exception):
            raise antwort
        if callable(antwort):
            return antwort(argv)
        return antwort

    def audit_text(self) -> str:
        return json.dumps(self.audit, ensure_ascii=False)


class NamenTests(_Basis):
    def test_organisation_standard_ist_wamocon_und_kommt_aus_der_umgebung(self):
        with _umgebung():
            self.assertEqual(host.github_org(), "Wamocon")
        with _umgebung(WAMO_GITHUB_ORG="Andere-Org"):
            self.assertEqual(host.github_org(), "Andere-Org")

    def test_ungueltige_organisation_wird_abgelehnt(self):
        for schlecht in ("a b", "x;calc", "-start", "a" * 40, "org/evil", "o\u202ee", "ü"):
            with self.subTest(org=schlecht), _umgebung(WAMO_GITHUB_ORG=schlecht), self.assertRaises(ValueError):
                host.github_org()

    def test_repo_name(self):
        for gut in ("radar", "Mein-Projekt_2.0", "a", "x" * 100):
            self.assertEqual(host.validate_repo_name(gut), gut)
        for schlecht in ("", " ", "../x", "a/b", "a b", "-x", ".x", "x.git", "x.", "con", "NUL", "x" * 101, "ä", None, 5,
                         "a\nb", "a;b", "a$b"):
            with self.subTest(name=schlecht), self.assertRaises(ValueError):
                host.validate_repo_name(schlecht)

    def test_repo_formen(self):
        with _umgebung():
            for text, soll in [
                ("radar", ("Wamocon", "radar")), ("Wamocon/radar", ("Wamocon", "radar")),
                ("https://github.com/Wamocon/radar", ("Wamocon", "radar")),
                ("https://github.com/Wamocon/radar.git/", ("Wamocon", "radar")),
                ("git@github.com:Wamocon/radar.git", ("Wamocon", "radar")), ("wamocon/radar", ("wamocon", "radar")),
            ]:
                with self.subTest(text=text):
                    self.assertEqual(host.parse_repo(text), soll)

    def test_fremde_konten_nur_per_erlaubnisliste(self):
        with _umgebung():
            with self.assertRaises(ValueError):
                host.parse_repo("fremder/projekt")
            with self.assertRaises(ValueError):
                host.parse_repo("https://github.com/fremder/projekt")
        with _umgebung(WAMO_GITHUB_KLONEN_VON="partner, ungültig!, x"):
            self.assertEqual(host.parse_repo("partner/projekt"), ("partner", "projekt"))
            self.assertEqual(host.github_klonen_von(), {"wamocon", "partner", "x"})

    def test_unsinnige_eingaben(self):
        with _umgebung():
            for schlecht in ("", None, 5, "a/b/c", "https://evil.example/Wamocon/radar", "Wamocon/../x",
                             "x" * 400, "Wamocon/ra\x00dar", "https://github.com/Wamocon/radar/tree/main"):
                with self.subTest(repo=schlecht), self.assertRaises(ValueError):
                    host.parse_repo(schlecht)


class AnlegenTests(_Basis):
    def anfrage(self, *args, offen_lassen=False, **kw):
        if not offen_lassen:
            host.PENDING.clear()  # eine offene Karte lässt Eingaben ruhen: jeder Aufruf beginnt ohne
        with _umgebung():
            return host.github_repo_anlegen(*args, **kw)

    def test_anlegen_ist_critical_und_nur_eine_karte_nichts_wird_ausgefuehrt(self):
        r = self.anfrage("radar")
        self.assertEqual(r["status"], "needs_approval")
        self.assertEqual(r["aktion"], "github")
        self.assertIn("Repository: Wamocon/radar", r["detail"])
        self.assertIn("privat", r["detail"])
        self.assertIn("Wamocon", r["ziel"])
        self.assertEqual(self.laeufe, [])
        self.assertEqual(self.audit[-1]["risk"], "critical")
        self.assertEqual(self.audit[-1]["repo"], "Wamocon/radar")
        self.assertIs(self.audit[-1]["privat"], True)

    def test_privat_ist_der_standard_oeffentlich_steht_deutlich_auf_der_karte(self):
        self.assertIn("privat", self.anfrage("a1")["detail"])
        r = self.anfrage("a2", privat=False)
        self.assertIn("ÖFFENTLICH", r["detail"])
        self.assertIn("ÖFFENTLICH", r["grund"])

    def test_beschreibung_wird_gesaeubert_und_begrenzt(self):
        r = self.anfrage("radar", beschreibung="Zeile\neins\u202e  zwei")
        self.assertIn("Beschreibung: Zeile eins zwei", r["detail"])
        self.assertEqual(self.anfrage("x1", beschreibung="a" * 201)["status"], "rejected")

    def test_ungueltige_namen_und_organisation_werden_abgelehnt(self):
        for schlecht in ("", "../x", "a b", "x;calc", "con"):
            self.assertEqual(self.anfrage(schlecht)["status"], "rejected", schlecht)
        with _umgebung(WAMO_GITHUB_ORG="böse org"):
            self.assertEqual(host.github_repo_anlegen("radar")["status"], "rejected")
        self.assertEqual(self.anfrage("radar", privat="ja")["status"], "rejected")
        self.assertEqual(self.laeufe, [])

    def test_ohne_gh_klare_meldung(self):
        with mock.patch.object(host, "_gh_pfad", side_effect=host.GhFehler("gh fehlt")):
            r = self.anfrage("radar")
        self.assertEqual(r, {"status": "error", "summary": "gh fehlt"})

    def test_gesperrt_und_offene_freigabe(self):
        with mock.patch.object(host, "_ist_gesperrt", return_value=True):
            self.assertEqual(self.anfrage("radar")["status"], "locked")
        self.anfrage("radar")  # offene Karte
        self.assertEqual(self.anfrage("anderes", offen_lassen=True)["status"], "wartet")
        self.assertEqual(self.laeufe, [])

    def test_rate_grenze(self):
        for _ in range(host.GH_ANLEGEN_PRO_MINUTE):
            host._rate_ok("github_repo_anlegen", host.GH_ANLEGEN_PRO_MINUTE)
        self.assertEqual(self.anfrage("radar")["status"], "rejected")

    # ---------- nach der Freigabe ----------
    def freigeben(self, antrag, ja=True):
        with _umgebung(GH_TOKEN="soll-nicht-weitergegeben-werden"):
            return host.entscheiden(antrag["approval_id"], ja, SCHLUESSEL)

    def test_freigabe_fuehrt_genau_einmal_und_mit_festen_argumenten_aus(self):
        antrag = self.anfrage("radar", beschreibung="Radar-Auswertung")
        r = self.freigeben(antrag)
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["url"], "https://github.com/Wamocon/radar")
        self.assertEqual(self.laeufe, [([GH, "repo", "create", "Wamocon/radar", "--private",
                                         "--description=Radar-Auswertung"], host.GH_ANLEGEN_TIMEOUT_SECS)])
        self.assertEqual(host.entscheiden(antrag["approval_id"], True, SCHLUESSEL)["status"], "error")  # nicht noch einmal
        self.assertEqual(len(self.laeufe), 1)

    def test_oeffentlich_nur_mit_ausdruecklichem_public(self):
        self.freigeben(self.anfrage("a1", privat=False))
        self.assertIn("--public", self.laeufe[0][0])
        self.assertNotIn("--private", self.laeufe[0][0])

    def test_nein_fuehrt_nichts_aus(self):
        r = self.freigeben(self.anfrage("radar"), ja=False)
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(self.laeufe, [])

    def test_ohne_schluessel_nichts(self):
        antrag = self.anfrage("radar")
        self.assertEqual(host.entscheiden(antrag["approval_id"], True, "falsch")["status"], "blocked")
        self.assertEqual(self.laeufe, [])

    def test_gesperrt_nach_der_freigabe_haelt_die_anfrage_offen(self):
        antrag = self.anfrage("radar")
        with mock.patch.object(host, "_ist_gesperrt", return_value=True):
            self.assertEqual(host.entscheiden(antrag["approval_id"], True, SCHLUESSEL)["status"], "locked")
        self.assertEqual(self.laeufe, [])
        self.assertEqual(host.entscheiden(antrag["approval_id"], True, SCHLUESSEL)["status"], "success")

    def test_beschreibung_mit_bindestrich_bleibt_ein_argument(self):
        self.freigeben(self.anfrage("radar", beschreibung="--web --delete"))
        argv = self.laeufe[0][0]
        self.assertEqual(argv[-1], "--description=--web --delete")
        self.assertNotIn("--web", argv)

    def test_fehler_werden_uebersetzt_und_nie_roh_weitergegeben(self):
        faelle = [
            ((1, "", "GraphQL: Name already exists on this account"), "gibt es schon"),
            ((4, "", "To get started with GitHub CLI, please run:  gh auth login"), "Anmeldung"),
            ((1, "", "HTTP 403: Resource not accessible"), "Berechtigung"),
            ((1, "", f"merkwürdig token {GEHEIM} kaputt"), "Exit-Code 1"),
        ]
        for antwort, erwartet in faelle:
            with self.subTest(erwartet=erwartet):
                self.antwort = antwort
                r = self.freigeben(self.anfrage(f"r{len(self.laeufe)}"))
                self.assertEqual(r["status"], "error")
                self.assertIn(erwartet, r["summary"])
                self.assertNotIn(GEHEIM, json.dumps(r))
        self.assertNotIn(GEHEIM, self.audit_text())

    def test_gh_nach_der_freigabe_verschwunden_gibt_die_klare_meldung(self):
        antrag = self.anfrage("radar")
        with mock.patch.object(host, "_gh_pfad", side_effect=host.GhFehler("gh fehlt jetzt")):
            r = host.entscheiden(antrag["approval_id"], True, SCHLUESSEL)
        self.assertEqual(r, {"status": "error", "summary": "gh fehlt jetzt"})

    def test_zeitlimit(self):
        self.antwort = host.GhFehler("Zeitlimit von 60 s überschritten.")
        r = self.freigeben(self.anfrage("radar"))
        self.assertEqual(r["status"], "error")
        self.assertIn("Zeitlimit", r["summary"])

    def test_organisation_aenderung_zwischen_anfrage_und_freigabe(self):
        antrag = self.anfrage("radar")
        with _umgebung(WAMO_GITHUB_ORG="Andere"):
            r = host.entscheiden(antrag["approval_id"], True, SCHLUESSEL)
        self.assertEqual(r["status"], "error")
        self.assertEqual(self.laeufe, [])

    def test_audit_ohne_beschreibung_im_klartext(self):
        self.freigeben(self.anfrage("radar", beschreibung="Geheime Kundenliste Müller"))
        self.assertNotIn("Müller", self.audit_text())
        self.assertNotIn("Kundenliste", self.audit_text())
        self.assertIn("Wamocon/radar", self.audit_text())

    def test_token_der_host_umgebung_geht_nicht_an_gh(self):
        with _umgebung(GH_TOKEN="a", GITHUB_TOKEN="b", GH_HOST="evil.example", PATH="x"):
            env = host._gh_umgebung()
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"):
            self.assertNotIn(name, env)
        self.assertEqual(env["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")


class KlonenTests(_Basis):
    def klonen(self, repo):
        with _umgebung():
            return asyncio.run(host.git_klonen(repo))

    def _legt_klon_an(self, url="https://github.com/Wamocon/radar.git"):
        def lauf(argv):
            ziel = Path(argv[-1])
            (ziel / ".git").mkdir(parents=True)
            (ziel / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (ziel / ".git" / "config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n', encoding="utf-8")
            return 0, "", ""
        return lauf

    def test_klont_nach_projekte_mit_gh(self):
        self.antwort = self._legt_klon_an()
        r = self.klonen("radar")
        self.assertEqual(r["status"], "success")
        self.assertEqual(r["pfad"], str(self.projekte / "radar"))
        self.assertIs(r["vorhanden"], False)
        self.assertEqual(self.laeufe, [([GH, "repo", "clone", "Wamocon/radar", str(self.projekte / "radar")],
                                        host.GIT_KLONEN_TIMEOUT_SECS)])
        self.assertEqual(self.audit[-1]["result"], "success")

    def test_schon_geklont_mit_gleichem_remote_ist_erfolg(self):
        for url in ("https://github.com/Wamocon/radar.git", "https://github.com/wamocon/RADAR", "git@github.com:Wamocon/radar.git",
                    "ssh://git@github.com/Wamocon/radar.git", "https://user@github.com/Wamocon/radar.git/"):
            with self.subTest(url=url):
                ziel = self.projekte / "radar"
                if ziel.exists():
                    import shutil
                    shutil.rmtree(ziel)
                self.antwort = self._legt_klon_an(url)
                host._rate_zeiten.clear()
                self.klonen("radar")
                self.laeufe.clear()
                host._rate_zeiten.clear()
                r = self.klonen("Wamocon/radar")
                self.assertEqual(r["status"], "success")
                self.assertIs(r["vorhanden"], True)
                self.assertEqual(self.laeufe, [])  # nichts wird erneut geklont, und git läuft im Ordner nie

    def test_anderes_remote_im_selben_ordner_ist_ein_fehler(self):
        for url in ("https://github.com/fremd/radar.git", "https://github.com/Wamocon/anderes.git",
                    "https://evil.example/Wamocon/radar.git", "https://github.com.evil.example/Wamocon/radar"):
            with self.subTest(url=url):
                ziel = self.projekte / "radar"
                if ziel.exists():
                    import shutil
                    shutil.rmtree(ziel)
                self.antwort = self._legt_klon_an(url)
                host._rate_zeiten.clear()
                self.klonen("radar")
                self.laeufe.clear()
                host._rate_zeiten.clear()
                r = self.klonen("radar")
                self.assertEqual(r["status"], "error")
                self.assertIn("anderes oder unvollständiges Repository", r["summary"])
                self.assertEqual(self.laeufe, [])

    def test_git_ordner_ohne_remote_ist_ein_fehler(self):
        (self.projekte / "radar" / ".git").mkdir(parents=True)
        self.assertEqual(self.klonen("radar")["status"], "error")
        self.assertEqual(self.laeufe, [])

    def test_belegter_ordner_ohne_git_ist_ein_fehler_leerer_wird_genutzt(self):
        (self.projekte / "radar").mkdir(parents=True)
        (self.projekte / "radar" / "notiz.txt").write_text("x", encoding="utf-8")
        self.assertEqual(self.klonen("radar")["status"], "error")
        self.assertEqual(self.laeufe, [])
        (self.projekte / "radar" / "notiz.txt").unlink()
        self.antwort = self._legt_klon_an()
        self.assertEqual(self.klonen("radar")["status"], "success")

    def test_nur_projekte_nie_andere_ziele(self):
        for schlecht in ("../evil", "Wamocon/..", "C:/Windows", "wamo", "a/b/c"):
            with self.subTest(repo=schlecht):
                self.assertEqual(self.klonen(schlecht)["status"], "rejected")
        self.assertEqual(self.laeufe, [])
        # ein Name, der ein verbotener Ordner wäre (Zugangsdaten- und WAMO-Ordner), wird wie bei ordner_anlegen abgelehnt
        self.assertEqual(self.klonen("Wamocon/.ssh")["status"], "rejected")

    def test_fremde_konten_werden_abgelehnt(self):
        self.assertEqual(self.klonen("fremder/projekt")["status"], "rejected")
        self.assertEqual(self.laeufe, [])

    def test_fehler_werden_uebersetzt_und_gefiltert(self):
        self.antwort = (1, "", f"fatal: Authentication failed for 'https://x:{GEHEIM}@github.com/Wamocon/radar/'")
        r = self.klonen("radar")
        self.assertEqual(r["status"], "error")
        self.assertIn("Anmeldung", r["summary"])
        self.assertNotIn(GEHEIM, json.dumps(r) + self.audit_text())
        self.antwort = (1, "", "remote: Repository not found.")
        self.assertIn("gibt es nicht", self.klonen("radar2")["summary"])
        self.antwort = (1, "", f"komisch {GEHEIM} " + "x" * 600)
        r = self.klonen("radar3")
        self.assertNotIn(GEHEIM, r["summary"])
        self.assertLess(len(r["summary"]), 450)

    def test_misslungener_klon_ohne_git_ordner_ist_ein_fehler(self):
        self.antwort = (0, "", "")  # gh meldet Erfolg, aber es liegt nichts da
        self.assertEqual(self.klonen("radar")["status"], "error")

    def test_ein_klon_zur_zeit(self):
        self.assertTrue(host._KLON_LOCK.acquire(blocking=False))
        try:
            r = self.klonen("radar")
        finally:
            host._KLON_LOCK.release()
        self.assertEqual(r["status"], "rejected")
        self.assertIn("läuft", r["summary"])
        self.assertEqual(self.laeufe, [])

    def test_zielordner_belegt_meldet_nicht_das_repository_gibt_es_schon(self):
        self.antwort = (128, "", "fatal: destination path 'x' already exists and is not an empty directory.")
        self.assertIn("Zielordner", self.klonen("radar")["summary"])

    def test_abgebrochener_klon_wird_aufgeraeumt_und_gilt_nie_als_vorhanden(self):
        def halb(argv):
            ziel = Path(argv[-1])
            (ziel / ".git").mkdir(parents=True)
            (ziel / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/Wamocon/radar\n', encoding="utf-8")
            raise host.GhFehler("Zeitlimit von 90 s überschritten.")
        self.antwort = halb
        self.assertEqual(self.klonen("radar")["status"], "error")
        self.assertFalse((self.projekte / "radar").exists())
        # vorhanden, aber unvollständig (kein HEAD): kein Erfolg, nichts gelöscht, nichts geklont
        (self.projekte / "halb" / ".git").mkdir(parents=True)
        (self.projekte / "halb" / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/Wamocon/halb\n', encoding="utf-8")
        self.laeufe.clear()
        self.assertEqual(self.klonen("halb")["status"], "error")
        self.assertTrue((self.projekte / "halb").exists())
        self.assertEqual(self.laeufe, [])

    def test_zeitlimit_und_fehlendes_gh(self):
        self.antwort = host.GhFehler("Zeitlimit von 90 s überschritten.")
        self.assertIn("Zeitlimit", self.klonen("radar")["summary"])
        with mock.patch.object(host, "_gh_pfad", side_effect=host.GhFehler("gh fehlt")):
            self.assertEqual(self.klonen("radar2"), {"status": "error", "summary": "gh fehlt"})

    def test_gesperrt_offene_freigabe_und_rate(self):
        with mock.patch.object(host, "_ist_gesperrt", return_value=True):
            self.assertEqual(self.klonen("radar")["status"], "locked")
        host.PENDING["0123456789abcdef"] = {"art": "powershell", "erstellt": host._jetzt(), "frist": 300, "command": "x"}
        self.assertEqual(self.klonen("radar")["status"], "wartet")
        host.PENDING.clear()
        self.assertEqual(self.laeufe, [])
        for _ in range(host.GIT_KLONEN_PRO_MINUTE):
            host._rate_ok("git_klonen", host.GIT_KLONEN_PRO_MINUTE)
        self.assertEqual(self.klonen("radar")["status"], "rejected")

    def test_origin_wird_als_text_gelesen_nie_mit_git(self):
        (self.projekte / "x" / ".git").mkdir(parents=True)
        (self.projekte / "x" / ".git" / "config").write_text(
            '[core]\n\tfsmonitor = calc.exe\n[remote "upstream"]\n\turl = https://github.com/a/b\n[remote "origin"]\n\turl = https://github.com/Wamocon/x\n',
            encoding="utf-8")
        self.assertEqual(host._origin_url(self.projekte / "x"), "https://github.com/Wamocon/x")
        self.assertIsNone(host._origin_url(self.projekte / "gibt-es-nicht"))
        self.assertEqual(self.laeufe, [])


class LaufTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Prozess-Test für Windows")
    def test_zeitlimit_beendet_den_prozess_und_meldet_klar(self):
        import sys
        import time
        start = time.monotonic()
        with self.assertRaises(host.GhFehler) as ctx:
            host._gh_lauf([sys.executable, "-c", "import time; time.sleep(30)"], 1)
        self.assertLess(time.monotonic() - start, 20)
        self.assertIn("Zeitlimit", ctx.exception.text)

    @unittest.skipUnless(os.name == "nt", "Prozess-Test für Windows")
    def test_ausgabe_und_exit_code_ohne_shell(self):
        import sys
        rc, out, err = host._gh_lauf([sys.executable, "-c", "import sys; print('hallo & calc'); sys.stderr.write('e'); sys.exit(3)"], 20)
        self.assertEqual((rc, out.strip(), err), (3, "hallo & calc", "e"))

    def test_nicht_startbar(self):
        with mock.patch.object(host.platform, "system", return_value="Windows"), self.assertRaises(host.GhFehler):
            host._gh_lauf([r"C:\gibt\es\nicht.exe"], 5)


class AusgabeTests(unittest.TestCase):
    def test_geheimnisse_werden_entfernt_und_gekuerzt(self):
        for geheim in (GEHEIM, "github_pat_" + "A1" * 20, "Authorization: Bearer abc.def.ghi.jkl.mno", "Authorization: token abcdefghijklmnop1234"):
            self.assertNotIn(geheim.split()[-1], host._gh_ausgabe(f"Fehler {geheim} Ende"))
        self.assertNotIn("passwort123", host._gh_ausgabe("clone https://user:passwort123@github.com/x/y"))
        self.assertNotIn("\x1b", host._gh_ausgabe("\x1b[31mrot\x1b[0m"))
        self.assertLessEqual(len(host._gh_ausgabe("x" * 5000)), host.GH_AUSGABE_MAX + 20)

    def test_gleiches_repo(self):
        self.assertTrue(host._gleiches_repo("https://github.com/Wamocon/radar.git", "Wamocon", "radar"))
        self.assertFalse(host._gleiches_repo("https://github.com/Wamocon/radar2", "Wamocon", "radar"))
        self.assertFalse(host._gleiches_repo("file:///C:/x", "Wamocon", "radar"))


class RegistrierungTests(unittest.TestCase):
    def test_werkzeuge_sind_registriert(self):
        namen = {t.name for t in asyncio.run(host.mcp.list_tools())}
        self.assertTrue({"git_klonen", "github_repo_anlegen"} <= namen)


if __name__ == "__main__":
    unittest.main()
