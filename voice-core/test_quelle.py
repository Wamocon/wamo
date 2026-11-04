"""Tests der Quelle des Release-Dienstes (infra/sokrates2/release/quelle.py, stufen_quelle.py).

Spiegel und Archiv laufen gegen echte lokale Git-Repositories (git muss vorhanden sein), die GitHub-App gegen eine Attrappe der API mit einem echten, im Test
erzeugten RSA-Schlüssel (openssl muss vorhanden sein). Ein Zugriff auf GitHub findet nicht statt: Die zweite App gibt es noch nicht.

Ausführen:  uv run python -m pytest -q test_quelle.py   (im Ordner voice-core/)
"""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

RELEASE = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "release"
sys.path.insert(0, str(RELEASE))
sys.path.insert(0, str(RELEASE / "pruefer"))

import pruefer_archiv as A  # noqa: E402
import pruefer_client as C  # noqa: E402
import quelle as Q  # noqa: E402
import stufen_quelle as SQ  # noqa: E402
from release_stufen import Kontext  # noqa: E402

GIT = shutil.which("git")
OPENSSL = shutil.which("openssl")
if GIT:
    Q.GIT = GIT   # unter Windows liegt git nicht in /usr/bin
if OPENSSL:
    Q.OPENSSL = OPENSSL
POSIX = os.name == "posix"
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args, eingabe=None) -> str:
    p = subprocess.run([GIT, *args], cwd=cwd, env=GIT_ENV, input=eingabe, capture_output=True, text=True, check=True)
    return p.stdout.strip()


def commit(repo, nachricht="c") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", nachricht)
    return git(repo, "rev-parse", "HEAD")


def commit_index(repo, nachricht="c") -> str:
    """Commit genau des Indexes (ohne git add -A: das würde Einträge ohne Datei im Arbeitsordner, also Links und Submodule, wieder entfernen)."""
    git(repo, "commit", "-q", "--allow-empty", "-m", nachricht)
    return git(repo, "rev-parse", "HEAD")


def neues_repo(wurzel, name="origin") -> str:
    pfad = os.path.join(wurzel, name)
    os.makedirs(pfad)
    git(pfad, "init", "-q", "-b", "main")
    return pfad


def schreibe(repo, rel, inhalt):
    p = Path(repo, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(inhalt if isinstance(inhalt, bytes) else inhalt.encode())


@unittest.skipUnless(GIT, "git nicht vorhanden")
class SpiegelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="quelle-")
        self.addCleanup(self.tmp.cleanup)
        self.origin = neues_repo(self.tmp.name)
        schreibe(self.origin, "a.py", "x = 1\n")
        self.c1 = commit(self.origin)
        self.spiegel = Q.Spiegel(os.path.join(self.tmp.name, "spiegel.git"), self.origin, datei_erlaubt=True)

    def test_holt_main_und_aktualisiert(self):
        self.assertEqual(self.spiegel.aktualisieren(), self.c1)
        schreibe(self.origin, "b.py", "y = 2\n")
        c2 = commit(self.origin)
        self.assertEqual(self.spiegel.aktualisieren(), c2)
        self.assertTrue(self.spiegel.ist_vorfahre(self.c1, c2))
        self.assertFalse(self.spiegel.ist_vorfahre(c2, self.c1))
        self.assertEqual(len(self.spiegel.baum(c2)), 40)

    def test_umgeschriebene_geschichte_wird_uebernommen_und_faellt_bei_der_basis_auf(self):
        self.spiegel.aktualisieren()
        git(self.origin, "reset", "-q", "--hard", "HEAD")
        schreibe(self.origin, "c.py", "z = 3\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-q", "--amend", "-m", "umgeschrieben")
        neu = git(self.origin, "rev-parse", "HEAD")
        self.assertEqual(self.spiegel.aktualisieren(), neu)
        self.assertFalse(self.spiegel.ist_vorfahre(self.c1, neu))   # die alte Basis ist kein Vorfahre mehr: der Aufrufer muss neu beginnen

    def test_nur_main_wird_geholt(self):
        git(self.origin, "checkout", "-q", "-b", "anderer")
        schreibe(self.origin, "x.txt", "x")
        commit(self.origin)
        git(self.origin, "tag", "v1")
        self.spiegel.aktualisieren()
        refs = git(self.spiegel.pfad, "for-each-ref", "--format=%(refname)").splitlines()
        self.assertEqual(refs, ["refs/heads/main"])

    def test_ungueltige_commits_erreichen_git_nie(self):
        for x in ("main", "HEAD", "--help", "-x", "abc", "G" * 40, "a" * 39, "a" * 41, "", None, "a" * 40 + "\n"):
            with self.subTest(x=x):
                with self.assertRaises(Q.QuelleFehler) as e:
                    self.spiegel.baum(x)
                self.assertEqual(e.exception.grund, "commit_ungueltig")
                with self.assertRaises(Q.QuelleFehler):
                    self.spiegel.schreibe_archiv(io.BytesIO(), x, self.c1)
        self.spiegel.aktualisieren()
        with self.assertRaises(Q.QuelleFehler) as e:
            self.spiegel.baum("0" * 40)   # gültige Form, gibt es nicht
        self.assertEqual(e.exception.grund, "git_fehler")

    def test_fehler_nennen_kein_token_und_keinen_gittext(self):
        s = Q.Spiegel(os.path.join(self.tmp.name, "s2.git"), os.path.join(self.tmp.name, "gibt-es-nicht"), datei_erlaubt=True)
        token = "ghs_" + "A" * 36
        with self.assertRaises(Q.QuelleFehler) as e:
            s.aktualisieren(token)
        self.assertNotIn(token, str(e.exception))
        self.assertNotIn("gibt-es-nicht", str(e.exception))
        self.assertEqual(e.exception.grund, "git_fehler")

    def test_dateien_url_ist_ohne_ausdrueckliche_erlaubnis_gesperrt(self):
        s = Q.Spiegel(os.path.join(self.tmp.name, "s3.git"), self.origin)   # datei_erlaubt=False: nur https
        with self.assertRaises(Q.QuelleFehler):
            s.aktualisieren()

    @unittest.skipUnless(POSIX, "Hooks nur unter Linux")
    def test_hooks_im_spiegel_laufen_nie(self):
        self.spiegel.aktualisieren()
        marke = os.path.join(self.tmp.name, "hook-gelaufen")
        for name in ("reference-transaction", "post-checkout", "post-merge", "pre-auto-gc"):
            hook = Path(self.spiegel.pfad, "hooks", name)
            hook.parent.mkdir(exist_ok=True)
            hook.write_text(f"#!/bin/sh\ntouch {marke}\n")
            hook.chmod(0o755)
        schreibe(self.origin, "n.py", "n = 1\n")
        commit(self.origin)
        self.spiegel.aktualisieren()
        self.assertFalse(os.path.exists(marke))

    def test_token_geht_nur_ueber_die_umgebung(self):
        token = "ghs_" + "B" * 36
        aufrufe = []

        def fake(argv, **kw):
            aufrufe.append((list(argv), dict(kw.get("env", {}))))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        os.environ["GEHEIM_AUS_DIENST"] = "x"
        self.addCleanup(os.environ.pop, "GEHEIM_AUS_DIENST", None)
        with mock.patch.object(Q.subprocess, "run", fake), mock.patch.object(Q.Spiegel, "kopf", lambda self_, ref=Q.REF_MAIN: "a" * 40):
            Q.Spiegel(os.path.join(self.tmp.name, "s4.git"), "https://example.org/r.git", proxy="http://127.0.0.1:18082").aktualisieren(token)
        fetch = [a for a in aufrufe if "fetch" in a[0]][0]
        self.assertFalse(any(token in teil for teil in fetch[0]))
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        self.assertEqual(fetch[1]["GIT_CONFIG_VALUE_0"], f"Authorization: Basic {basic}")
        self.assertEqual(fetch[1]["https_proxy"], "http://127.0.0.1:18082")
        self.assertNotIn("GEHEIM_AUS_DIENST", fetch[1])   # nichts aus der Umgebung des Dienstes
        for muss in ("core.hooksPath=/dev/null", "protocol.allow=never", "protocol.https.allow=always", "transfer.fsckObjects=true"):
            self.assertIn(muss, fetch[0])
        self.assertNotIn("protocol.file.allow=always", fetch[0])


@unittest.skipUnless(GIT, "git nicht vorhanden")
class ArchivTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="quelle-archiv-")
        self.addCleanup(self.tmp.cleanup)
        self.origin = neues_repo(self.tmp.name)
        self.spiegel = Q.Spiegel(os.path.join(self.tmp.name, "spiegel.git"), self.origin, datei_erlaubt=True)

    def archiv(self, basis, neu) -> A.SpeicherZiel:
        self.spiegel.aktualisieren()
        puffer = io.BytesIO()
        self.spiegel.schreibe_archiv(puffer, basis, neu)
        ziel = A.SpeicherZiel()
        puffer.seek(0)
        A.lies_archiv(puffer, ziel)
        return ziel

    def test_beide_staende_mit_modi_und_ohne_attribute(self):
        schreibe(self.origin, "a.py", "alt\n")
        c1 = commit(self.origin)
        schreibe(self.origin, "a.py", "neu\n")
        schreibe(self.origin, "bin/run.sh", "#!/bin/sh\n")
        schreibe(self.origin, "docs/Übersicht Größe.md", "text\n")
        schreibe(self.origin, ".gitattributes", "geheim.txt export-ignore\nversion.txt export-subst\n")
        schreibe(self.origin, "geheim.txt", "bleibt im Archiv\n")
        schreibe(self.origin, "version.txt", "$Format:%H$\n")
        schreibe(self.origin, "leer.txt", "")
        schreibe(self.origin, "bin.dat", bytes(range(256)) + b"\nEND\n")
        git(self.origin, "add", "-A")
        git(self.origin, "update-index", "--chmod=+x", "bin/run.sh")
        c2 = commit_index(self.origin)   # nicht noch einmal git add -A: Das setzt den Modus aus dem Arbeitsordner zurück
        e = self.archiv(c1, c2).eintraege
        self.assertEqual(e["basis/a.py"], (b"alt\n", False))
        self.assertEqual(e["neu/a.py"], (b"neu\n", False))
        self.assertEqual(e["neu/bin/run.sh"], (b"#!/bin/sh\n", True))
        self.assertEqual(e["neu/docs/Übersicht Größe.md"][0], b"text\n")
        self.assertEqual(e["neu/geheim.txt"][0], b"bleibt im Archiv\n")      # export-ignore wird nicht beachtet
        self.assertEqual(e["neu/version.txt"][0], b"$Format:%H$\n")           # export-subst wird nicht ersetzt
        self.assertEqual(e["neu/leer.txt"][0], b"")
        self.assertEqual(e["neu/bin.dat"][0], bytes(range(256)) + b"\nEND\n")
        self.assertEqual(e["neu/bin"], "D")
        self.assertNotIn("basis/bin/run.sh", e)

    def test_ausgelassene_ordner_fehlen(self):
        schreibe(self.origin, "node_modules/x/y.js", "x")
        schreibe(self.origin, "ui/dist/a.js", "x")
        schreibe(self.origin, "ui/src/a.ts", "x")
        c = commit(self.origin)
        e = self.archiv(c, c).eintraege
        self.assertEqual(sorted(k for k in e if k.startswith("neu/")), ["neu/ui", "neu/ui/src", "neu/ui/src/a.ts"])

    def test_verknuepfungen_kommen_als_link(self):
        schreibe(self.origin, "a.txt", "x")
        git(self.origin, "add", "a.txt")
        oid = git(self.origin, "hash-object", "-w", "--stdin", eingabe="/etc/passwd")
        git(self.origin, "update-index", "--add", "--cacheinfo", f"120000,{oid},verweis")
        c = commit_index(self.origin)
        self.assertEqual(self.archiv(c, c).eintraege["neu/verweis"], "L")

    def test_submodule_sind_ein_fehler(self):
        schreibe(self.origin, "a.txt", "x")
        git(self.origin, "add", "a.txt")
        git(self.origin, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},sub")
        c = commit_index(self.origin)
        self.spiegel.aktualisieren()
        with self.assertRaises(Q.QuelleFehler) as e:
            self.spiegel.schreibe_archiv(io.BytesIO(), c, c)
        self.assertEqual(e.exception.grund, "submodul")

    @unittest.skipUnless(POSIX, "Git für Windows lässt solche Pfade nicht zu")
    def test_unzulaessige_pfade_sind_ein_fehler(self):
        oid = git(self.origin, "hash-object", "-w", "--stdin", eingabe="x")
        for pfad in ("a\nb", "a\\b", "a\x01b"):
            with self.subTest(pfad=pfad):
                git(self.origin, "read-tree", "--empty")
                git(self.origin, "update-index", "--add", "--cacheinfo", f"100644,{oid},{pfad}")
                tree = git(self.origin, "write-tree")
                c = git(self.origin, "commit-tree", tree, "-m", "x")
                git(self.origin, "update-ref", "refs/heads/main", c)
                self.spiegel.aktualisieren()
                with self.assertRaises(Q.QuelleFehler) as e:
                    self.spiegel.schreibe_archiv(io.BytesIO(), c, c)
                self.assertEqual(e.exception.grund, "pfad_ungueltig")

    def test_archiv_aus_git_gleicht_dem_aus_dem_ordner(self):
        schreibe(self.origin, "src/a.py", "x = 1\n")
        schreibe(self.origin, "README.md", "# t\n")
        c = commit(self.origin)
        arbeit = os.path.join(self.tmp.name, "arbeit")
        git(self.tmp.name, "clone", "-q", self.origin, arbeit)
        aus_ordner = io.BytesIO()
        A.schreibe_archiv(aus_ordner, {"basis": arbeit, "neu": arbeit})
        z_ordner = A.SpeicherZiel()
        aus_ordner.seek(0)
        A.lies_archiv(aus_ordner, z_ordner)
        z_git = self.archiv(c, c)
        inhalt = lambda z: {k: v for k, v in z.eintraege.items() if v != "D"}   # noqa: E731  (Ordner zählen nicht, nur Dateien)
        self.assertEqual(inhalt(z_git), inhalt(z_ordner))

    @unittest.skipUnless(POSIX and hasattr(os, "O_NOFOLLOW") and os.geteuid() != 0, "braucht Linux")
    def test_archiv_laesst_sich_mit_dem_echten_ziel_schreiben(self):
        schreibe(self.origin, "src/a.py", "x = 1\n")
        schreibe(self.origin, "run.sh", "#!/bin/sh\n")
        git(self.origin, "add", "-A")
        git(self.origin, "update-index", "--chmod=+x", "run.sh")
        c = commit_index(self.origin)
        self.spiegel.aktualisieren()
        puffer = io.BytesIO()
        self.spiegel.schreibe_archiv(puffer, c, c)
        puffer.seek(0)
        ziel_ordner = os.path.join(self.tmp.name, "ziel")
        os.mkdir(ziel_ordner)
        z = A.DateiZiel(ziel_ordner)
        try:
            A.lies_archiv(puffer, z)
        finally:
            z.schliessen()
        self.assertEqual(Path(ziel_ordner, "neu/src/a.py").read_text(), "x = 1\n")
        self.assertTrue(os.access(os.path.join(ziel_ordner, "neu/run.sh"), os.X_OK))


# ----------------------------------------------------------------------------------------------------------------- GitHub-App
class AttrappeApi:
    def __init__(self, status=201, antwort=None, widerruf_status=204):
        self.aufrufe = []
        self.status, self.widerruf_status = status, widerruf_status
        self.antwort = antwort if antwort is not None else {"token": "ghs_" + "T" * 36, "permissions": {"contents": "read", "metadata": "read"},
                                                           "repositories": [{"name": "wamo"}]}

    def __call__(self, methode, url, kopf, body):
        self.aufrufe.append((methode, url, dict(kopf), body))
        if methode == "DELETE":
            return self.widerruf_status, b""
        return self.status, json.dumps(self.antwort).encode()


@unittest.skipUnless(OPENSSL, "openssl nicht vorhanden")
class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="quelle-app-")
        cls.pem = os.path.join(cls.tmp.name, "app.pem")
        cls.pub = os.path.join(cls.tmp.name, "pub.pem")
        subprocess.run([OPENSSL, "genrsa", "-out", cls.pem, "2048"], check=True, capture_output=True)
        subprocess.run([OPENSSL, "rsa", "-in", cls.pem, "-pubout", "-out", cls.pub], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def app(self, api=None, **kw):
        self.api = api or AttrappeApi()
        return Q.GitHubApp("12345", "67890", self.pem, "wamo", http=self.api, uhr=lambda: 1_000_000.0, **kw)

    def test_token_wird_nur_mit_leserechten_fuer_ein_repository_verlangt(self):
        token = self.app().token_lesen()
        methode, url, kopf, body = self.api.aufrufe[0]
        self.assertEqual((methode, url), ("POST", "https://api.github.com/app/installations/67890/access_tokens"))
        self.assertEqual(json.loads(body), {"repositories": ["wamo"], "permissions": {"contents": "read", "metadata": "read"}})
        self.assertTrue(token.startswith("ghs_"))
        jwt = kopf["Authorization"].removeprefix("Bearer ")
        k, n, s = jwt.split(".")
        dek = lambda t: json.loads(base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)))   # noqa: E731
        self.assertEqual(dek(k), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(dek(n), {"iat": 1_000_000 - 60, "exp": 1_000_000 + 540, "iss": "12345"})

    def test_die_signatur_stimmt(self):
        self.app().token_lesen()
        jwt = self.api.aufrufe[0][2]["Authorization"].removeprefix("Bearer ")
        k, n, s = jwt.split(".")
        sig = os.path.join(self.tmp.name, "sig.bin")
        nutz = os.path.join(self.tmp.name, "nutz.txt")
        Path(sig).write_bytes(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
        Path(nutz).write_bytes(f"{k}.{n}".encode())
        r = subprocess.run([OPENSSL, "dgst", "-sha256", "-verify", self.pub, "-signature", sig, nutz], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Verified OK", r.stdout)

    def test_antworten_mit_mehr_rechten_oder_anderem_repository_werden_abgelehnt(self):
        gut = {"token": "ghs_" + "T" * 36, "permissions": {"contents": "read", "metadata": "read"}, "repositories": [{"name": "wamo"}]}
        faelle = (("token_rechte", {**gut, "permissions": {"contents": "write", "metadata": "read"}}),
                  ("token_rechte", {**gut, "permissions": {"contents": "read", "pull_requests": "read"}}),
                  ("token_repo", {**gut, "repositories": [{"name": "anderes"}]}),
                  ("token_repo", {**gut, "repositories": [{"name": "wamo"}, {"name": "zweites"}]}),
                  ("token_repo", {**gut, "repositories": []}),
                  ("token_antwort", {**gut, "token": "kurz"}),
                  ("token_antwort", {**gut, "token": "ghs_ mit leerzeichen und mehr als zwanzig"}),
                  ("token_antwort", {k: v for k, v in gut.items() if k != "token"}),
                  ("token_antwort", {**gut, "token": 12345678901234567890123}))
        for grund, antwort in faelle:
            with self.subTest(grund=grund, antwort=antwort):
                with self.assertRaises(Q.QuelleFehler) as e:
                    self.app(AttrappeApi(antwort=antwort)).token_lesen()
                self.assertEqual(e.exception.grund, grund)

    def test_ablehnung_der_api(self):
        for status in (401, 403, 404, 422, 500, 200):
            with self.assertRaises(Q.QuelleFehler) as e:
                self.app(AttrappeApi(status=status)).token_lesen()
            self.assertEqual(e.exception.grund, "token_abgelehnt")

    def test_kein_schluessel_kein_jwt(self):
        a = Q.GitHubApp("1", "2", os.path.join(self.tmp.name, "gibt-es-nicht.pem"), "wamo", http=AttrappeApi())
        with self.assertRaises(Q.QuelleFehler) as e:
            a.jwt()
        self.assertEqual(e.exception.grund, "jwt_signatur")
        self.assertNotIn("gibt-es-nicht", str(e.exception))

    def test_ungueltige_kennungen(self):
        for app_id, inst, repo in (("x", "1", "wamo"), ("1", "", "wamo"), ("1", "1; rm", "wamo"), ("1", "1", "a/b"), ("1", "1", ""), ("1", "1", "wamo\n")):
            with self.assertRaises(Q.QuelleFehler):
                Q.GitHubApp(app_id, inst, self.pem, repo, http=AttrappeApi())

    def test_widerruf(self):
        a = self.app()
        self.assertTrue(a.widerrufen("ghs_" + "T" * 36))
        methode, url, kopf, _ = self.api.aufrufe[-1]
        self.assertEqual((methode, url), ("DELETE", "https://api.github.com/installation/token"))
        self.assertEqual(kopf["Authorization"], "Bearer ghs_" + "T" * 36)
        self.assertFalse(self.app(AttrappeApi(widerruf_status=500)).widerrufen("x" * 30))

        def kaputt(*_):
            raise Q.QuelleFehler("netz")
        self.assertFalse(Q.GitHubApp("1", "2", self.pem, "wamo", http=kaputt).widerrufen("x" * 30))

    @unittest.skipUnless(GIT, "git nicht vorhanden")
    def test_quelle_widerruft_das_token_auch_nach_einem_fehler(self):
        app = self.app()

        class Kaputt(Q.Spiegel):
            def aktualisieren(self, token=None):
                raise Q.QuelleFehler("git_fehler", "fetch exit 128")
        with self.assertRaises(Q.QuelleFehler):
            Q.Quelle(Kaputt("/x", "https://example.org/r.git"), app).aktualisieren()
        self.assertEqual([a[0] for a in self.api.aufrufe], ["POST", "DELETE"])

    def test_http_echt_lehnt_klartext_ab(self):
        with self.assertRaises(Q.QuelleFehler) as e:
            Q.http_echt("GET", "http://api.github.com/", {}, None)
        self.assertEqual(e.exception.grund, "nur_https")


class KonfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="quelle-konf-")
        self.addCleanup(self.tmp.cleanup)
        self.pfad = os.path.join(self.tmp.name, "quelle.env")

    def test_fehlende_datei_ist_keine_quelle(self):
        self.assertIsNone(Q.quelle_aus_datei(self.pfad))

    def test_nur_https_urls(self):
        Path(self.pfad).write_text("WAMO_QUELLE_URL=http://github.com/Wamocon/wamo.git\n")
        with self.assertRaises(Q.QuelleFehler):
            Q.quelle_aus_datei(self.pfad)
        Path(self.pfad).write_text("WAMO_QUELLE_URL=file:///x\n")
        with self.assertRaises(Q.QuelleFehler):
            Q.quelle_aus_datei(self.pfad)

    def test_lokale_quelle_nur_unter_dem_festen_ordner(self):
        Path(self.pfad).write_text("WAMO_QUELLE_URL=/var/lib/wamo-quelle/wamo.git\n")
        q = Q.quelle_aus_datei(self.pfad, "/var/lib/x/spiegel.git")
        self.assertIsNone(q.app)
        self.assertTrue(q.spiegel.datei_erlaubt)
        self.assertEqual((q.spiegel.url, q.spiegel.proxy), ("/var/lib/wamo-quelle/wamo.git", None))
        for schlecht in ("/var/lib/wamo-quelle/../etc/x.git", "/var/lib/wamo-quelle/a/b.git", "/etc/wamo.git", "/var/lib/wamo-quelle/.git", "wamo.git",
                         "/var/lib/wamo-quelle/wamo.git/", "/var/lib/wamo-quelle/-x.git", "/var/lib/wamo-quelle/wamo", "file:///var/lib/wamo-quelle/wamo.git"):
            Path(self.pfad).write_text(f"WAMO_QUELLE_URL={schlecht}\n")
            with self.assertRaises(Q.QuelleFehler, msg=schlecht):
                Q.quelle_aus_datei(self.pfad)

    def test_lokaler_pfad_nutzt_die_root_eigene_globale_datei_https_nie(self):
        # git verwirft bei lokalem Transport die Konfiguration von der Befehlszeile für den Kindprozess: freigegeben wird über GIT_CONFIG_GLOBAL
        gesehen = []
        echt = Q.subprocess.run
        for n in ("s.git", "s2.git"):   # vorhandener Spiegel: git init entfällt, der Aufruf des Holens wird sichtbar
            os.makedirs(os.path.join(self.tmp.name, n))
            Path(os.path.join(self.tmp.name, n, "HEAD")).write_text("ref: refs/heads/main" + chr(10))

        def fang(argv, **kw):
            gesehen.append((argv, kw["env"]))
            raise OSError("nur mitgelesen")
        Q.subprocess.run = fang
        try:
            with self.assertRaises(Q.QuelleFehler):
                Q.Spiegel(os.path.join(self.tmp.name, "s.git"), "/var/lib/wamo-quelle/wamo.git", datei_erlaubt=True).aktualisieren()
            lokal = list(gesehen)
            gesehen.clear()
            with self.assertRaises(Q.QuelleFehler):
                Q.Spiegel(os.path.join(self.tmp.name, "s2.git"), "https://github.com/Wamocon/wamo.git").aktualisieren()
        finally:
            Q.subprocess.run = echt
        self.assertEqual(Q.GIT_QUELLE_KONFIG, "/etc/wamo-release/git-quelle.gitconfig")
        self.assertEqual(lokal[0][1]["GIT_CONFIG_GLOBAL"], Q.GIT_QUELLE_KONFIG)
        self.assertEqual(gesehen[0][1]["GIT_CONFIG_GLOBAL"], "/dev/null")
        for argv, _ in lokal + gesehen:
            self.assertFalse(any("safe.directory" in a for a in argv))   # nie per -c: dort wirkt es nicht und wäre nur Täuschung

    def test_ohne_app_nur_spiegel_mit_app_beides(self):
        Path(self.pfad).write_text("# Kommentar\nWAMO_QUELLE_URL=https://github.com/Wamocon/wamo.git\nWAMO_QUELLE_PROXY=http://127.0.0.1:18082\n")
        q = Q.quelle_aus_datei(self.pfad, "/var/lib/x/spiegel.git")
        self.assertIsNone(q.app)
        self.assertEqual((q.spiegel.url, q.spiegel.proxy, q.spiegel.pfad), ("https://github.com/Wamocon/wamo.git", "http://127.0.0.1:18082", "/var/lib/x/spiegel.git"))
        Path(self.pfad).write_text("WAMO_QUELLE_URL=https://github.com/Wamocon/wamo.git\nWAMO_QUELLE_APP_ID=1\nWAMO_QUELLE_INSTALLATION_ID=2\n")
        q = Q.quelle_aus_datei(self.pfad)
        self.assertEqual((q.app.app_id, q.app.installation_id, q.app.pem, q.app.repo), ("1", "2", "/etc/wamo-release/app.pem", "wamo"))


# ----------------------------------------------------------------------------------------------------------------- Stufe
class FakeSpiegel:
    def __init__(self, fehler=None):
        self.archive, self.fehler = [], fehler

    def schreibe_archiv(self, ausgang, basis, neu):
        if self.fehler:
            raise self.fehler
        self.archive.append((basis, neu))
        ausgang.write(b"END\n")


class FakeQuelle:
    def __init__(self, **kw):
        self.spiegel = FakeSpiegel(**kw)


def ctx(daten, abgebrochen=False):
    ereignis = threading.Event()
    if abgebrochen:
        ereignis.set()
    return Kontext("a" * 8, "main", "p", "pruefung1", 1, daten, time.monotonic() + 100, ereignis)


B, C1, R = "1" * 40, "2" * 40, "3" * 40


class StufenTests(unittest.TestCase):
    def stufe(self, antwort=None, fehler=None, quelle_fehler=None):
        self.aufrufe = []

        def fake(schreiber, runde, sockpfad):
            self.aufrufe.append((runde, sockpfad))
            schreiber(io.BytesIO())
            if fehler:
                raise fehler
            return antwort if antwort is not None else {"pruefstand": {"bauen": "ok", "tests": "ok"}}
        self.quelle = FakeQuelle(fehler=quelle_fehler)
        return SQ.QuellenStufen(self.quelle, fake, "/run/test.sock")

    def test_runde_1_prueft_c1_gegen_die_basis(self):
        e = self.stufe().pruefen(ctx({"basis_commit": B, "c1_commit": C1}), 1)
        self.assertEqual((e.art, e.daten), ("ok", {"pruefstand": {"bauen": "ok", "tests": "ok"}}))
        self.assertEqual(self.quelle.spiegel.archive, [(B, C1)])
        self.assertEqual(self.aufrufe, [(1, "/run/test.sock")])

    def test_runde_2_prueft_r_ausser_der_refactor_wurde_verworfen(self):
        s = self.stufe()
        s.pruefen(ctx({"basis_commit": B, "c1_commit": C1, "r_commit": R}), 2)
        s.pruefen(ctx({"basis_commit": B, "c1_commit": C1, "r_commit": R, "refactor_verworfen": True}), 2)
        s.pruefen(ctx({"basis_commit": B, "c1_commit": C1}), 2)
        self.assertEqual(self.quelle.spiegel.archive, [(B, R), (B, C1), (B, C1)])

    def test_runde_3_prueft_v_und_ohne_v_commit_gar_nichts(self):
        e = self.stufe().pruefen(ctx({"basis_commit": B, "c1_commit": C1}), 3)
        self.assertEqual((e.art, e.grund), ("fehler", "commit_fehlt"))   # Runde 3 läuft auf V; ohne ihn wird nichts geprüft
        self.assertEqual(self.aufrufe, [])
        for runde in (0, 4, -1):
            e = self.stufe().pruefen(ctx({"basis_commit": B, "c1_commit": C1}), runde)
            self.assertEqual((e.art, e.grund), ("fehler", "stufe_nicht_verfuegbar"), runde)

    def test_fehlende_oder_ungueltige_commits(self):
        for daten in ({}, {"basis_commit": B}, {"c1_commit": C1}, {"basis_commit": "main", "c1_commit": C1}, {"basis_commit": B, "c1_commit": "x" * 40},
                      {"basis_commit": B, "c1_commit": C1 + "\n"}):
            e = self.stufe().pruefen(ctx(daten), 1)
            self.assertEqual((e.art, e.grund), ("fehler", "commit_fehlt"), daten)

    def test_abbruch_vor_dem_start(self):
        e = self.stufe().pruefen(ctx({"basis_commit": B, "c1_commit": C1}, abgebrochen=True), 1)
        self.assertEqual((e.art, e.grund), ("fehler", "abgebrochen"))
        self.assertEqual(self.aufrufe, [])

    def test_fehler_der_pruefstrecke_und_der_quelle_werden_zu_kurzen_gruenden(self):
        e = self.stufe(fehler=C.PrueferFehler("belegt")).pruefen(ctx({"basis_commit": B, "c1_commit": C1}), 1)
        self.assertEqual((e.art, e.grund), ("fehler", "pruefstrecke_belegt"))
        e = self.stufe(fehler=C.PrueferFehler("Dienst nicht erreichbar!")).pruefen(ctx({"basis_commit": B, "c1_commit": C1}), 1)
        self.assertRegex(e.grund, r"^pruefstrecke_[a-z0-9_]{1,25}$")
        e = self.stufe(quelle_fehler=Q.QuelleFehler("submodul", "Submodule sind nicht erlaubt")).pruefen(ctx({"basis_commit": B, "c1_commit": C1}), 1)
        self.assertEqual((e.art, e.grund), ("fehler", "quelle_submodul"))

    def test_windows_hinweis_wird_zur_sichtbaren_zeile(self):
        antwort = {"pruefstand": {"bauen": "ok"}, "windows_pruefung_noetig": True}
        e = self.stufe(antwort=antwort).pruefen(ctx({"basis_commit": B, "c1_commit": C1}), 1)
        self.assertEqual(e.daten["pruefstand"], {"bauen": "ok", "windows_tests": "uebersprungen"})

    def test_ergebnis_passt_zum_vertrag_des_automaten(self):
        import release_automat as AU
        e = self.stufe(antwort={"pruefstand": dict.fromkeys(AU.PRUEFSTAND_PFLICHT[1], "ok")}).pruefen(ctx({"basis_commit": B, "c1_commit": C1}), 1)
        AU.daten_pruefen(e.daten)   # wirft bei einer Abweichung

    def test_andere_stufen_bleiben_nicht_verfuegbar(self):
        s = self.stufe()
        for name in ("uebernehmen", "reviewen", "refaktorieren", "version_waehlen", "veroeffentlichen", "bereitstellen", "umschalten"):
            e = getattr(s, name)(ctx({}))
            self.assertEqual((e.art, e.grund), ("fehler", "stufe_nicht_verfuegbar"), name)


class QuelltextTests(unittest.TestCase):
    def test_nur_standardbibliothek_nur_lesen_und_keine_gedankenstriche(self):
        text = (RELEASE / "quelle.py").read_text(encoding="utf-8")
        for name in ("quelle.py", "stufen_quelle.py"):
            t = (RELEASE / name).read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), t, name)
            self.assertNotIn(chr(0x2013), t, name)
        erlaubt = {"__future__", "base64", "json", "os", "re", "subprocess", "time", "urllib.error", "urllib.request", "typing", "release_stufen", "pruefer_archiv",
                   "pruefer_client", "quelle", "release_anzeige", "release_kette", "contextlib", "threading", "collections"}
        for name in ("quelle.py", "stufen_quelle.py"):
            for zeile in (RELEASE / name).read_text(encoding="utf-8").splitlines():
                if zeile.startswith(("import ", "from ")) or zeile.startswith("        import ") or zeile.startswith("        from "):
                    self.assertIn(zeile.split()[1], erlaubt, (name, zeile))
        for verboten in ("shell=True", "os.system", "eval(", "push", "/repos", "git/refs", "/releases", "/merges", "/git/tags", '"PUT"', '"PATCH"'):
            self.assertNotIn(verboten, text, verboten)   # nur lesend: kein Schreiben nach GitHub, keine Tags, keine Releases
        self.assertEqual(text.count('"POST"'), 1)   # der einzige POST ist der Token-Abruf

    def test_kein_schreibrecht_in_den_angeforderten_rechten(self):
        self.assertEqual(Q.GitHubApp.LESERECHTE, {"contents": "read", "metadata": "read"})


if __name__ == "__main__":
    unittest.main()
