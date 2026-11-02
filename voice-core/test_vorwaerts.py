"""Tests der Vorwärtsbefehle des Ankers (infra/sokrates2/anker/vorwaerts.py und vorwaerts_echt.py, Schritt 1.4d).

Teil 1 mit Attrappen (Datenbank, Quelle, Werk): Prüfreihenfolge, Status, Freigabe, Quelle, Migrationen in beiden Schemas, Abbruch, Wiederholung.
Teil 2 mit einem echten Git-Repository: Auszug nur der erlaubten Pfade, Modi, Verknüpfungen und Submodule.
Teil 3 mit einem Wegwerf-Postgres in Docker (pgvector/pgvector:pg16, nur lokal): die echten Migrationen 0001 und 0013, dann migrieren über docker exec;
der Trigger der Prüfsumme (0013) und die Unveränderlichkeit werden wirklich ausgelöst. Ohne Docker wird Teil 3 übersprungen. Nichts berührt Sokrates-2.
"""

import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parent
ANKER = HIER.parent / "infra" / "sokrates2" / "anker"
MIGRATIONEN = HIER.parent / "supabase" / "migrations"
sys.path.insert(0, str(ANKER))

import vorwaerts_echt as E  # noqa: E402
from vorwaerts import Vorwaerts, VorwaertsFehler  # noqa: E402

ID = "0b9f2c1e-4a6d-4f0e-9a1b-2c3d4e5f6a7b"
COMMIT = "c" * 40
BAUM = "d" * 40
POSIX = os.name == "posix"
DOCKER = shutil.which("docker") is not None


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


MIG_A = b"set local lock_timeout = '5s';\ncreate table if not exists __SCHEMA__.a_tab (id int);\ninsert into __SCHEMA__.schema_migrations (version) values ('0090_a') on conflict do nothing;\n"
MIG_B = b"set local lock_timeout = '5s';\ncreate table if not exists __SCHEMA__.b_tab (id int);\ninsert into __SCHEMA__.schema_migrations (version) values ('0091_b') on conflict do nothing;\n"


class FakeDb:
    def __init__(self, **kw):
        self.zeile = {"status": "veroeffentlicht", "quelle": "main", "art": "normal", "v_commit": COMMIT, "tree_id": BAUM, "version_final": "0.2.0", **kw}
        self.gilt = True
        self.gesetzt: list[tuple] = []

    def anfrage(self, k):
        return dict(self.zeile) if k == ID else None

    def freigabe_gilt(self, k, c, b):
        return self.gilt and (c, b) == (COMMIT, BAUM)

    def status_setzen(self, k, von, nach):
        if self.zeile["status"] != von:
            raise VorwaertsFehler("status_nicht_gesetzt")
        self.zeile["status"] = nach
        self.gesetzt.append((von, nach))


class FakeQuelle:
    def __init__(self, dateien: dict[str, bytes], baum: str = BAUM):
        self.dateien, self.baum, self.aufrufe = dateien, baum, []

    def baum_id(self, commit):
        return self.baum

    def extrahiere(self, commit, ziel, pfade):
        self.aufrufe.append((commit, pfade))
        for pfad, inhalt in self.dateien.items():
            p = Path(ziel) / pfad
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(inhalt)


class FakeWerk:
    def __init__(self, stand=None):
        self.stand = {s: dict(v) for s, v in (stand or {"wamo_test": {}, "wamo_dev": {}}).items()}
        self.angewendet: list[tuple] = []
        self.scheitert: tuple | None = None
        self.nicht_buchen = False
        self.kern: list[tuple] = []

    def migrationsstand(self, schema):
        return dict(self.stand[schema])

    def anwenden(self, schema, version, sql):
        if self.scheitert == (schema, version):
            raise VorwaertsFehler("migration_fehlgeschlagen", version)
        self.angewendet.append((schema, version))
        assert "__SCHEMA__" not in sql and f"{schema}.schema_migrations set sha256" in sql
        if not self.nicht_buchen:
            self.stand[schema][version] = sql.split("sha256 = '")[1].split("'")[0]

    def kern_bereiten(self, name, ordner):
        self.kern.append(("bereiten", name, os.path.isfile(os.path.join(ordner, "bot.py"))))
        return "b"

    def kern_schalten(self, name):
        self.kern.append(("schalten", name))
        return "b"


def bauen(tmp, dateien=None, db=None, werk=None, quelle=None):
    dateien = dateien if dateien is not None else {"supabase/migrations/0090_a.sql": MIG_A, "supabase/migrations/0091_b.sql": MIG_B,
                                                   "supabase/migrations/0091_b.down.sql": b"drop table x;", "voice-core/bot.py": b"print(1)\n"}
    db, werk, quelle = db or FakeDb(), werk or FakeWerk(), quelle or FakeQuelle(dateien)
    return Vorwaerts(db, quelle, werk, str(tmp)), db, werk, quelle


class Pruefungen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_ungueltige_kennung_unbekannte_anfrage_falscher_status(self):
        v, db, w, q = bauen(self.tmp.name)
        for k in ("", "x", "../x", ID.upper(), ID + "0"):
            with self.assertRaises(VorwaertsFehler) as e:
                v.migrieren(k)
            self.assertEqual(e.exception.code, "anfrage_ungueltig", k)
        with self.assertRaises(VorwaertsFehler) as e:
            v.migrieren("11111111-1111-4111-8111-111111111111")
        self.assertEqual(e.exception.code, "anfrage_unbekannt")
        db.zeile["status"] = "wartet_freigabe"
        for befehl in ("migrieren", "bereitstellen", "umschalten"):
            with self.assertRaises(VorwaertsFehler) as e:
                getattr(v, befehl)(ID)
            self.assertEqual(e.exception.code, "status_falsch", befehl)
        self.assertEqual((w.angewendet, q.aufrufe), ([], []))   # nichts geholt, nichts angewendet

    def test_unvollstaendige_anfrage(self):
        for feld, wert in (("v_commit", None), ("tree_id", "xyz"), ("version_final", None), ("version_final", "1.2"), ("v_commit", "C" * 40)):
            v, db, w, q = bauen(self.tmp.name, db=FakeDb(**{feld: wert}))
            with self.assertRaises(VorwaertsFehler) as e:
                v.migrieren(ID)
            self.assertEqual(e.exception.code, "anfrage_unvollstaendig", (feld, wert))

    def test_widerrufene_freigabe_und_unstimmige_quelle(self):
        v, db, w, q = bauen(self.tmp.name)
        db.gilt = False
        with self.assertRaises(VorwaertsFehler) as e:
            v.bereitstellen(ID)
        self.assertEqual(e.exception.code, "freigabe_ungueltig")
        db.gilt = True
        q.baum = "e" * 40   # die Quelle liefert unter dieser Commit-ID einen anderen Tree
        with self.assertRaises(VorwaertsFehler) as e:
            v.bereitstellen(ID)
        self.assertEqual(e.exception.code, "quelle_unstimmig")
        self.assertEqual((w.angewendet, w.kern, q.aufrufe), ([], [], []))


class Migrieren(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_beide_schemas_test_zuerst_in_reihenfolge_mit_pruefsumme(self):
        v, db, w, q = bauen(self.tmp.name)
        erg = v.migrieren(ID)
        self.assertEqual(w.angewendet, [("wamo_test", "0090_a"), ("wamo_test", "0091_b"), ("wamo_dev", "0090_a"), ("wamo_dev", "0091_b")])
        self.assertEqual(erg["angewendet"], {"wamo_test": ["0090_a", "0091_b"], "wamo_dev": ["0090_a", "0091_b"]})
        self.assertEqual(w.stand["wamo_dev"]["0090_a"], sha(MIG_A))
        self.assertEqual(erg["release"], "v0.2.0-" + COMMIT[:7])
        self.assertEqual(db.gesetzt, [])   # migrieren allein ändert den Status nicht
        self.assertEqual(os.listdir(self.tmp.name), [])   # der Auszug ist weg

    def test_rueckwege_laufen_nie_vorwaerts(self):
        v, db, w, q = bauen(self.tmp.name)
        v.migrieren(ID)
        self.assertFalse(any("down" in version for _, version in w.angewendet))

    def test_wiederholung_wendet_nichts_doppelt_an(self):
        v, db, w, q = bauen(self.tmp.name)
        v.migrieren(ID)
        n = len(w.angewendet)
        erg = v.migrieren(ID)
        self.assertEqual((len(w.angewendet), erg["angewendet"]), (n, {"wamo_test": [], "wamo_dev": []}))

    def test_nur_fehlende_migrationen(self):
        v, db, w, q = bauen(self.tmp.name, werk=FakeWerk({"wamo_test": {"0090_a": sha(MIG_A)}, "wamo_dev": {"0090_a": ""}}))   # "": älter als die Prüfsumme
        erg = v.migrieren(ID)
        self.assertEqual(erg["angewendet"], {"wamo_test": ["0091_b"], "wamo_dev": ["0091_b"]})

    def test_geaenderte_angewendete_datei_bricht_ab_bevor_etwas_geschieht(self):
        v, db, w, q = bauen(self.tmp.name, werk=FakeWerk({"wamo_test": {"0090_a": "f" * 64}, "wamo_dev": {}}))
        with self.assertRaises(VorwaertsFehler) as e:
            v.migrieren(ID)
        self.assertEqual((e.exception.code, e.exception.detail), ("migration_veraendert", "0090_a"))
        self.assertEqual(w.angewendet, [])

    def test_scheitert_test_bleibt_dev_unberuehrt(self):
        v, db, w, q = bauen(self.tmp.name)
        w.scheitert = ("wamo_test", "0091_b")
        with self.assertRaises(VorwaertsFehler) as e:
            v.migrieren(ID)
        self.assertEqual(e.exception.code, "migration_fehlgeschlagen")
        self.assertEqual(w.angewendet, [("wamo_test", "0090_a")])   # in wamo_dev ist nichts passiert
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_migration_die_ihre_version_nicht_bucht_ist_ein_fehler(self):
        v, db, w, q = bauen(self.tmp.name)
        w.nicht_buchen = True
        with self.assertRaises(VorwaertsFehler) as e:
            v.migrieren(ID)
        self.assertEqual(e.exception.code, "migration_nicht_gebucht")

    def test_trockenlauf_aendert_nichts(self):
        v, db, w, q = bauen(self.tmp.name)
        erg = v.migrieren(ID, trocken=True)
        self.assertTrue(erg["trocken"])
        self.assertEqual(erg["plan"], {"wamo_test": ["0090_a", "0091_b"], "wamo_dev": ["0090_a", "0091_b"]})
        self.assertEqual((w.angewendet, db.gesetzt), ([], []))


class BereitstellenUmschalten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_bereitstellen_migriert_zuerst_baut_das_release_setzt_den_status(self):
        v, db, w, q = bauen(self.tmp.name)
        erg = v.bereitstellen(ID)
        self.assertEqual(len(w.angewendet), 4)
        self.assertEqual(w.kern, [("bereiten", "v0.2.0-" + COMMIT[:7], True)])
        self.assertEqual((erg["slot"], db.gesetzt), ("b", [("veroeffentlicht", "bereitgestellt")]))
        self.assertEqual(q.aufrufe, [(COMMIT, ("voice-core", "supabase/migrations"))])   # nur diese Pfade werden geholt

    def test_scheitert_die_migration_wird_kein_release_gebaut_und_kein_status_gesetzt(self):
        v, db, w, q = bauen(self.tmp.name)
        w.scheitert = ("wamo_test", "0090_a")
        with self.assertRaises(VorwaertsFehler):
            v.bereitstellen(ID)
        self.assertEqual((w.kern, db.gesetzt), ([], []))

    def test_ohne_kern_im_stand_wird_nicht_bereitgestellt(self):
        v, db, w, q = bauen(self.tmp.name, dateien={"supabase/migrations/0090_a.sql": MIG_A})
        with self.assertRaises(VorwaertsFehler) as e:
            v.bereitstellen(ID)
        self.assertEqual(e.exception.code, "kern_fehlt")
        self.assertEqual(db.gesetzt, [])

    def test_trockenlauf_bereitstellen(self):
        v, db, w, q = bauen(self.tmp.name)
        erg = v.bereitstellen(ID, trocken=True)
        self.assertTrue(erg["trocken"])
        self.assertEqual((w.angewendet, w.kern, db.gesetzt), ([], [], []))

    def test_umschalten_braucht_bereitgestellt_und_setzt_umgeschaltet(self):
        v, db, w, q = bauen(self.tmp.name, db=FakeDb(status="bereitgestellt"))
        erg = v.umschalten(ID)
        self.assertEqual((erg["slot"], erg["version"], erg["commit"]), ("b", "0.2.0", COMMIT))
        self.assertEqual((w.kern, db.gesetzt), ([("schalten", "v0.2.0-" + COMMIT[:7])], [("bereitgestellt", "umgeschaltet")]))
        v2, db2, w2, q2 = bauen(self.tmp.name, db=FakeDb(status="bereitgestellt"))
        self.assertTrue(v2.umschalten(ID, trocken=True)["trocken"])
        self.assertEqual((w2.kern, db2.gesetzt), ([], []))

    def test_unbekannter_befehl(self):
        v, *_ = bauen(self.tmp.name)
        with self.assertRaises(VorwaertsFehler):
            v.ausfuehren("rueckweg", ID)


def git(ordner, *argv):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false", *argv], cwd=ordner, capture_output=True,
                          check=True).stdout


def git_env(heim):
    return {"PATH": os.environ["PATH"], "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "HOME": heim, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}


@unittest.skipUnless(shutil.which("git"), "braucht git")
class QuelleGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "r.git")
        arbeit = os.path.join(self.tmp.name, "w")
        os.makedirs(os.path.join(arbeit, "voice-core"))
        os.makedirs(os.path.join(arbeit, "supabase", "migrations"))
        os.makedirs(os.path.join(arbeit, "ui"))
        Path(arbeit, "voice-core", "bot.py").write_text("x = 1\n")
        Path(arbeit, "voice-core", "start.sh").write_text("#!/bin/sh\n")
        Path(arbeit, "supabase", "migrations", "0001_a.sql").write_text("select 1;\n")
        Path(arbeit, "ui", "geheim.txt").write_text("nicht holen\n")
        git(arbeit, "init", "-q", "-b", "main")
        git(arbeit, "add", "-A")
        git(arbeit, "update-index", "--chmod=+x", "voice-core/start.sh")
        git(arbeit, "commit", "-q", "-m", "x")
        self.commit = git(arbeit, "rev-parse", "HEAD").decode().strip()
        self.baum = git(arbeit, "rev-parse", "HEAD^{tree}").decode().strip()
        subprocess.run(["git", "clone", "-q", "--bare", arbeit, self.repo], check=True, capture_output=True)
        self.q = E.QuelleGit(self.repo, git_umgebung_fn=lambda: git_env(self.tmp.name), git="git")

    def test_baum_id_stimmt_und_ungueltige_commits_werden_abgewiesen(self):
        self.assertEqual(self.q.baum_id(self.commit), self.baum)
        for schlecht in ("HEAD", "main", "-x", self.commit[:39], self.commit.upper(), "0" * 40):
            with self.assertRaises(VorwaertsFehler):
                self.q.baum_id(schlecht)

    def test_auszug_nur_der_genannten_pfade(self):
        ziel = os.path.join(self.tmp.name, "auszug")
        os.makedirs(ziel)
        self.q.extrahiere(self.commit, ziel, ("voice-core", "supabase/migrations"))
        self.assertEqual(sorted(os.listdir(ziel)), ["supabase", "voice-core"])   # ui/ wurde nie geholt
        self.assertEqual(Path(ziel, "voice-core", "bot.py").read_text(), "x = 1\n")
        self.assertEqual(Path(ziel, "supabase", "migrations", "0001_a.sql").read_text(), "select 1;\n")
        if POSIX:
            self.assertTrue(os.access(os.path.join(ziel, "voice-core", "start.sh"), os.X_OK))
            self.assertFalse(os.access(os.path.join(ziel, "voice-core", "bot.py"), os.X_OK))

    def test_vorhandene_datei_wird_nie_ueberschrieben(self):
        ziel = os.path.join(self.tmp.name, "auszug2")
        os.makedirs(os.path.join(ziel, "voice-core"))
        Path(ziel, "voice-core", "bot.py").write_text("alt")
        with self.assertRaises(FileExistsError):
            self.q.extrahiere(self.commit, ziel, ("voice-core",))
        self.assertEqual(Path(ziel, "voice-core", "bot.py").read_text(), "alt")

    @unittest.skipUnless(POSIX, "Verknüpfungen nur unter Linux")
    def test_verknuepfung_im_stand_ist_ein_fehler(self):
        arbeit = os.path.join(self.tmp.name, "w2")
        os.makedirs(os.path.join(arbeit, "voice-core"))
        os.symlink("/etc/passwd", os.path.join(arbeit, "voice-core", "link"))
        git(arbeit, "init", "-q", "-b", "main")
        git(arbeit, "add", "-A")
        git(arbeit, "commit", "-q", "-m", "l")
        commit = git(arbeit, "rev-parse", "HEAD").decode().strip()
        repo = os.path.join(self.tmp.name, "r2.git")
        subprocess.run(["git", "clone", "-q", "--bare", arbeit, repo], check=True, capture_output=True)
        ziel = os.path.join(self.tmp.name, "a3")
        os.makedirs(ziel)
        q = E.QuelleGit(repo, git_umgebung_fn=lambda: git_env(self.tmp.name), git="git")
        with self.assertRaises(VorwaertsFehler) as e:
            q.extrahiere(commit, ziel, ("voice-core",))
        self.assertEqual(e.exception.code, "modus_unbekannt")
        self.assertFalse(os.path.lexists(os.path.join(ziel, "voice-core", "link")))


@unittest.skipUnless(DOCKER, "braucht Docker")
class EchtePostgresTests(unittest.TestCase):
    """Wegwerf-Postgres mit den echten Migrationen 0001 und 0013 in wamo_test und wamo_dev."""

    container = None

    @classmethod
    def setUpClass(cls):
        if subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode != 0:
            raise unittest.SkipTest("Docker läuft nicht")
        cls.container = "wamo-test-vw-" + secrets.token_hex(4)
        subprocess.run(["docker", "run", "-d", "--rm", "--name", cls.container, "-e", "POSTGRES_PASSWORD=" + secrets.token_hex(12), "pgvector/pgvector:pg16"],
                       check=True, capture_output=True, timeout=600)
        for _ in range(90):
            ok = subprocess.run(["docker", "exec", cls.container, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0
            if ok:
                time.sleep(1)
                if subprocess.run(["docker", "exec", cls.container, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0:
                    break
            time.sleep(1)
        def lokal(argv, eingabe, zeit):   # der Anker nutzt /usr/bin/docker und eine eingeschränkte Umgebung; hier der docker des Entwicklungsrechners
            p = subprocess.run(argv, input=eingabe, capture_output=True, timeout=zeit)
            return p.returncode, p.stdout + (b"" if p.returncode == 0 else bytes([10]) + p.stderr)
        cls.werk = E.WerkDocker("/nicht/benutzt", container=cls.container, lauf=lokal, docker="docker")
        cls.sql("create role anon nologin; create role authenticated nologin; create role service_role nologin; create schema auth; "
                "create table auth.users (id uuid primary key); create schema extensions;")
        for schema in ("wamo_test", "wamo_dev"):
            for nr in ("0001_wamo_init", "0013_release_freigaben"):
                cls.sql((MIGRATIONEN / f"{nr}.sql").read_text(encoding="utf-8").replace("__SCHEMA__", schema))

    @classmethod
    def tearDownClass(cls):
        if cls.container:
            subprocess.run(["docker", "rm", "-f", cls.container], capture_output=True, timeout=60)

    @classmethod
    def sql(cls, text):
        rc, aus = cls.werk._psql(["-v", "ON_ERROR_STOP=1", "--single-transaction"], text.encode("utf-8"), 300)
        if rc != 0:
            raise AssertionError("SQL fehlgeschlagen: " + aus.decode("utf-8", "replace")[-400:])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_stand_und_anwenden_mit_pruefsumme_und_sperre_gegen_aenderung(self):
        stand = self.werk.migrationsstand("wamo_test")
        self.assertIn("0001_wamo_init", stand)
        text = (MIG_A.decode().replace("__SCHEMA__", "wamo_test")
                + f"\nupdate wamo_test.schema_migrations set sha256 = '{sha(MIG_A)}' where version = '0090_a' and sha256 is null;\n")
        self.werk.anwenden("wamo_test", "0090_a", text)
        self.assertEqual(self.werk.migrationsstand("wamo_test")["0090_a"], sha(MIG_A))
        # die eingetragene Prüfsumme ist unveränderlich (Trigger aus 0013)
        rc, _ = self.werk._psql(["-v", "ON_ERROR_STOP=1", "-c", "update wamo_test.schema_migrations set sha256 = repeat('0', 64) where version = '0090_a'"], None, 60)
        self.assertNotEqual(rc, 0)

    def test_fehlerhafte_migration_laesst_nichts_zurueck(self):
        kaputt = "create table wamo_test.halb (id int);\nselect 1/0;\n"
        with self.assertRaises(VorwaertsFehler):
            self.werk.anwenden("wamo_test", "0092_kaputt", kaputt)
        rc, aus = self.werk._psql(["-At", "-c", "select to_regclass('wamo_test.halb') is null"], None, 60)
        self.assertEqual(aus.strip(), b"t")   # eine Transaktion: auch die erste Anweisung ist zurückgenommen

    def test_schema_wird_geprueft(self):
        for schlecht in ("public", "wamo_test; drop table x", "WAMO_TEST", "wamo_x"):
            with self.assertRaises(VorwaertsFehler):
                self.werk.migrationsstand(schlecht)

    def test_ganzer_weg_migrieren_in_beiden_schemas_und_wiederholbar(self):
        inhalt = (b"set local lock_timeout = '5s';\ncreate table if not exists __SCHEMA__.neu_tab (id int);\n"
                  b"insert into __SCHEMA__.schema_migrations (version) values ('0093_neu') on conflict do nothing;\n")
        v = Vorwaerts(FakeDb(), FakeQuelle({"supabase/migrations/0093_neu.sql": inhalt}), self.werk, self.tmp.name)
        erg = v.migrieren(ID)
        self.assertEqual(erg["angewendet"], {"wamo_test": ["0093_neu"], "wamo_dev": ["0093_neu"]})
        for schema in ("wamo_test", "wamo_dev"):
            self.assertEqual(self.werk.migrationsstand(schema)["0093_neu"], sha(inhalt))
        self.assertEqual(v.migrieren(ID)["angewendet"], {"wamo_test": [], "wamo_dev": []})
        # dieselbe Version mit anderem Inhalt: abgelehnt, bevor etwas geschieht
        v2 = Vorwaerts(FakeDb(), FakeQuelle({"supabase/migrations/0093_neu.sql": inhalt + b"-- anders\n"}), self.werk, self.tmp.name)
        with self.assertRaises(VorwaertsFehler) as e:
            v2.migrieren(ID)
        self.assertEqual(e.exception.code, "migration_veraendert")

    def test_migration_ohne_eigene_buchung_wird_erkannt(self):
        v = Vorwaerts(FakeDb(), FakeQuelle({"supabase/migrations/0094_still.sql": b"select 1;\n"}), self.werk, self.tmp.name)
        with self.assertRaises(VorwaertsFehler) as e:
            v.migrieren(ID)
        self.assertEqual(e.exception.code, "migration_nicht_gebucht")


@unittest.skipUnless(POSIX and hasattr(os, "geteuid") and os.geteuid() != 0, "der Anker prüft Besitzer und Rechte: nur als normaler Benutzer unter Linux")
class VorwaertsImAnker(unittest.TestCase):
    """Die Verdrahtung in wamo_anker.py: Rechte, Sperre, Pflichtprotokoll, Fehlercodes, Zustandsdatei."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HIER))
        import test_anker as T
        cls.T = T

    def setUp(self):
        T = self.T

        class Fall(T.AnkerTest):
            def runTest(self):
                pass
        self.f = Fall()
        self.f.setUp()
        self.addCleanup(self.f.tmp.cleanup)
        self.f.schritte_anlegen(namen=("rueckweg-ui", "rueckweg-kern", "kern-slots"))
        self.aufrufe: list[tuple] = []
        self.verhalten = {"fehler": None, "fabrik_fehler": False}

        class Echt:
            def ausfuehren(inner, befehl, kennung, trocken):
                self.aufrufe.append((befehl, kennung, trocken))
                if self.verhalten["fehler"]:
                    raise self.verhalten["fehler"]
                return {"release": "v0.2.0-" + COMMIT[:7], "slot": "b", "version": "0.2.0", "commit": COMMIT, "plan": {"wamo_test": []}, "geheim": "nie"}

        def fabrik():
            if self.verhalten["fabrik_fehler"]:
                raise RuntimeError("keine Datenbank")
            return Echt()
        self.A = T.A
        self.f.a = self.f.neuer_anker(vorwaerts_fabrik=fabrik)

    def test_release_dienst_darf_senden_ergebnis_ist_gefiltert_und_protokolliert(self):
        for befehl in ("bereitstellen", "migrieren", "umschalten"):
            r = self.f.sende(f"{befehl} {ID}", "wamo-release")
            self.assertTrue(r["ok"], (befehl, r))
            self.assertEqual(r["ergebnis"]["release"], "v0.2.0-" + COMMIT[:7])
            self.assertNotIn("geheim", r["ergebnis"])   # nur bekannte Felder gehen zurück
        self.assertEqual([a[:2] for a in self.aufrufe], [(b, ID) for b in ("bereitstellen", "migrieren", "umschalten")])
        zeilen = self.f.audit()
        namen = [z["ereignis"] for z in zeilen]
        self.assertLess(namen.index("anfrage"), namen.index("vorwaerts"))   # die Protokollzeile vor der Wirkung ist Pflicht

    def test_fehler_kommen_als_code_und_aendern_nichts(self):
        self.verhalten["fehler"] = self.A.VorwaertsFehler("freigabe_ungueltig", "x")
        r = self.f.sende(f"bereitstellen {ID}", "wamo-release")
        self.assertFalse(r["ok"])
        self.assertEqual((r["grund"], r["detail"]), ("vorwaerts_fehlgeschlagen", "freigabe_ungueltig x"))

    def test_trockenlauf_wird_durchgereicht(self):
        r = self.f.sende(f"migrieren {ID} --trocken", "wamo-release")
        self.assertTrue(r["ok"])
        self.assertEqual(self.aufrufe, [("migrieren", ID, True)])

    def test_sperre_haelt_alle_an_bevor_die_mitspieler_auch_nur_gebaut_werden(self):
        self.f.sende("sperre an")
        for name in ("root", "wamocon", "wamo-release"):
            r = self.f.sende(f"umschalten {ID}", name)
            self.assertEqual(r["grund"], "gesperrt", name)
        self.assertEqual(self.aufrufe, [])

    def test_fehlt_die_datenbank_ist_es_nicht_verfuegbar_ohne_absturz(self):
        self.verhalten["fabrik_fehler"] = True
        with self.f.stderr():
            r = self.f.sende(f"bereitstellen {ID}", "wamocon")
        self.assertEqual((r["ok"], r["grund"]), (False, "nicht_verfuegbar"))

    def test_schritt_skript_des_kerns_muss_vertrauenswuerdig_sein(self):
        os.unlink(os.path.join(self.f.p.schritte, "kern-slots"))
        self.f.manifest_schreiben()
        r = self.f.sende(f"umschalten {ID}", "wamocon")
        self.assertEqual(r["grund"], "schritt_fehlt")
        self.assertEqual(self.aufrufe, [])

    def test_nach_dem_umschalten_steht_der_neue_slot_als_aktiv_in_der_zustandsdatei(self):
        self.f.stand_setzen()
        self.f.sende(f"umschalten {ID}", "wamocon")
        kern = self.A.stand_laden(self.f.p.stand, self.f.p.vertrauens_uid)["komponenten"]["kern"]
        self.assertEqual((kern["aktiv"]["version"], kern["aktiv"]["commit"], kern["aktiv"]["ziel"]), ("v0.2.0", COMMIT, "b"))
        self.assertIsNone(kern["letzte_gute"])   # die letzte gute Fassung gilt erst nach bestandener Bewährung

    def test_ohne_fabrik_bleibt_es_nicht_verfuegbar(self):
        self.f.a = self.f.neuer_anker()
        self.assertEqual(self.f.sende(f"umschalten {ID}", "wamocon")["grund"], "nicht_verfuegbar")


class Quelltext(unittest.TestCase):
    def test_keine_gedankenstriche_und_keine_shell(self):
        for n in ("vorwaerts.py", "vorwaerts_echt.py"):
            t = (ANKER / n).read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), t, n)
            self.assertNotIn(chr(0x2013), t, n)
            self.assertNotIn("shell=True", t, n)
            self.assertNotIn("os.system", t, n)


if __name__ == "__main__":
    unittest.main()
