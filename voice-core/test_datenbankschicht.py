"""Tests der Datenbankschicht für Release-Dienst und Freigabe-Dienst (infra/sokrates2/pg/wamo_pg.py, release/release_pg.py,
anker/freigabe_pg.py, Schritt 1.4b und Stufe 1 des Konzepts docs/selbstueberarbeitung.md).

Teil 1 läuft überall ohne Datenbank: Zugangsdatei, Fehlerabbildung, Verbindungsparameter (verify-full, Zeitgrenzen).
Teil 2 braucht Docker und psycopg und startet ein Wegwerf-Postgres (Container wamo-test-pg-*, pgvector/pgvector:pg16, nur auf
127.0.0.1) mit den ECHTEN Migrationen 0001 und 0013, den echten Rollen (Mitgliedschaft mit INHERIT, ohne SET wie in der
Produktion) und prüft die Rechte, die Trigger und dass die Attrappe der Freigabe-Datenbank dieselben Entscheidungen trifft wie
die echte Datenbank. Ohne Docker oder psycopg wird Teil 2 übersprungen. Nichts davon berührt Sokrates-2.

Ausführen (Teil 2):  D:\\tmp\\wamo-pg\\venv\\Scripts\\python.exe -m pytest -q test_datenbankschicht.py   (psycopg[binary], pyjwt,
cryptography, pytest im Venv; Docker Desktop läuft)
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

HIER = Path(__file__).resolve().parent
INFRA = HIER.parent / "infra" / "sokrates2"
MIGRATIONEN = HIER.parent / "supabase" / "migrations"
for ordner in ("pg", "release", "anker"):
    sys.path.insert(0, str(INFRA / ordner))

import freigabe as F  # noqa: E402
import release_db as RDB  # noqa: E402
import release_pg as RPG  # noqa: E402
import wamo_pg as W  # noqa: E402
from freigabe_pg import FreigabePg  # noqa: E402

try:
    import psycopg  # noqa: F401

    PSYCOPG = True
except ImportError:
    PSYCOPG = False

POSIX = os.name == "posix"
DOCKER = shutil.which("docker") is not None
PASSWORT = "Zq8vL2mN5xR7tB3kW9pD"  # nur für Tests, 20 Zeichen


def datei(verzeichnis: Path, inhalt: str, name="db.zugang", modus=0o640) -> Path:
    p = verzeichnis / name
    p.write_text(inhalt, encoding="utf-8")
    if POSIX:
        p.chmod(modus)
    return p


GUT = (f"host=192.168.178.136\nport=55322\ndbname=postgres\nuser=wamo_release_s2.wamo\npassword={PASSWORT}\n"
       "sslrootcert=/opt/wamo-release/wamo-ca.crt\n")


@unittest.skipUnless(POSIX, "Dateirechte und Besitzer gibt es nur unter Linux")
class ZugangsdateiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.uid = os.getuid()
        self.ca = datei(self.dir, "ca", name="ca.crt", modus=0o644)
        self.gut = GUT.replace("/opt/wamo-release/wamo-ca.crt", str(self.ca))
        self.dir.chmod(0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def lies(self, p):
        return W.zugangsdatei_lesen(p, erwartete_uids=(self.uid,))

    def test_gute_datei(self):
        z = self.lies(datei(self.dir, self.gut))
        self.assertEqual((z.host, z.port, z.dbname, z.user), ("192.168.178.136", 55322, "postgres", "wamo_release_s2.wamo"))
        self.assertEqual(z.sslrootcert, str(self.ca))

    def test_passwort_steht_nie_in_repr_oder_str(self):
        z = self.lies(datei(self.dir, self.gut))
        for text in (repr(z), str(z), f"{z}"):
            self.assertNotIn(PASSWORT, text)

    def test_kommentare_und_leerzeilen(self):
        self.assertEqual(self.lies(datei(self.dir, "# Kommentar\n\n" + self.gut + "\n# Ende\n")).port, 55322)

    def test_rechte(self):
        for modus in (0o644, 0o666, 0o660, 0o604, 0o650, 0o642):
            with self.assertRaises(W.ZugangsdateiFehler, msg=oct(modus)):
                self.lies(datei(self.dir, self.gut, modus=modus))
        for modus in (0o600, 0o640, 0o400, 0o440):
            self.assertEqual(self.lies(datei(self.dir, self.gut, modus=modus)).port, 55322, oct(modus))

    def test_besitzer(self):
        with self.assertRaises(W.ZugangsdateiFehler):
            W.zugangsdatei_lesen(datei(self.dir, self.gut), erwartete_uids=(self.uid + 1,))

    def test_link_wird_abgewiesen(self):
        echt = datei(self.dir, self.gut, name="echt")
        link = self.dir / "link"
        link.symlink_to(echt)
        with self.assertRaises(W.ZugangsdateiFehler):
            self.lies(link)

    def test_verzeichnis_und_fehlende_datei(self):
        with self.assertRaises(W.ZugangsdateiFehler):
            self.lies(self.dir)
        with self.assertRaises(W.ZugangsdateiFehler):
            self.lies(self.dir / "gibt-es-nicht")

    def test_inhalt_wird_streng_geprueft(self):
        schlecht = [
            self.gut.replace("host=192.168.178.136\n", ""),                                  # fehlt
            self.gut + "host=zweiter\n",                                                      # doppelt
            self.gut + "extra=1\n",                                                           # unbekannt
            self.gut + "ohne gleichheitszeichen\n",
            self.gut.replace(PASSWORT, "kurz"),                                               # zu kurz
            self.gut.replace(PASSWORT, "ein passwort mit leer zeichen"),
            self.gut.replace("port=55322", "port=70000"),
            self.gut.replace("port=55322", "port=abc"),
            self.gut.replace("user=wamo_release_s2.wamo", "user=a b"),
            self.gut.replace("user=wamo_release_s2.wamo", "user=x;drop"),
            self.gut.replace("dbname=postgres", "dbname="),
            self.gut.replace("host=192.168.178.136", "host=a b"),
        ]
        for i, inhalt in enumerate(schlecht):
            with self.assertRaises(W.ZugangsdateiFehler, msg=str(i)):
                self.lies(datei(self.dir, inhalt))

    def test_ca_datei_wird_streng_geprueft(self):
        gut = self.lies(datei(self.dir, self.gut))
        self.assertEqual(gut.sslrootcert, str(self.ca))
        fremd = datei(self.dir, "x", name="fremd.crt", modus=0o666)                           # für alle beschreibbar
        for wert in ("system", "ca.crt", "./ca.crt", str(self.dir / "gibt-es-nicht.crt"), str(self.dir), str(fremd)):
            with self.assertRaises(W.ZugangsdateiFehler, msg=wert):
                self.lies(datei(self.dir, self.gut.replace(str(self.ca), wert)))
        link = self.dir / "link.crt"
        link.symlink_to(self.ca)
        with self.assertRaises(W.ZugangsdateiFehler):
            self.lies(datei(self.dir, self.gut.replace(str(self.ca), str(link))))
        with self.assertRaises(W.ZugangsdateiFehler):                                          # andere Besitzer erwartet
            W.zugangsdatei_lesen(datei(self.dir, self.gut, name="b.zugang"), erwartete_uids=(self.uid + 1,))

    def test_beschreibbarer_ordner_wird_abgewiesen(self):
        p = datei(self.dir, self.gut)
        self.dir.chmod(0o770)
        with self.assertRaises(W.ZugangsdateiFehler):
            self.lies(p)
        self.dir.chmod(0o702)
        with self.assertRaises(W.ZugangsdateiFehler):
            self.lies(p)
        self.dir.chmod(0o700)
        self.assertEqual(self.lies(p).port, 55322)

    def test_zu_grosse_datei_wird_abgewiesen(self):
        with self.assertRaises(W.ZugangsdateiFehler):
            self.lies(datei(self.dir, self.gut + "# " + "x" * 20000 + "\n"))

    def test_zeile_ohne_schluessel_zeigt_nie_ihren_inhalt(self):
        geheim = "Qw3rtzPassw0rtOhneSchluessel=="
        for inhalt in (self.gut + geheim + "\n", self.gut + geheim.replace("==", "") + "\n"):
            with self.assertRaises(W.ZugangsdateiFehler) as m:
                self.lies(datei(self.dir, inhalt))
            self.assertNotIn("Qw3rtz", str(m.exception))
            self.assertRegex(str(m.exception), r"Zeile \d+")

    def test_fehlertexte_nennen_das_passwort_nie(self):
        for inhalt in (self.gut + "extra=" + PASSWORT + "\n", self.gut.replace("port=55322", "port=" + PASSWORT)):
            with self.assertRaises(W.ZugangsdateiFehler) as m:
                self.lies(datei(self.dir, inhalt))
            self.assertNotIn(PASSWORT, str(m.exception))


class PgOhneDatenbankTests(unittest.TestCase):
    ZIEL = W.Ziel("h.example", 5432, "postgres", "u", PASSWORT, "/ca.crt")

    def test_verbindungsparameter_sind_fest(self):
        gesehen = {}

        class FakePsycopg:
            @staticmethod
            def connect(**kw):
                gesehen.update(kw)
                raise OSError("kein Server")

        with mock.patch.dict(sys.modules, {"psycopg": FakePsycopg}):
            with self.assertRaises(W.PgFehler) as m:
                W.Pg(self.ZIEL, anwendung="test").zeilen("select 1")
        self.assertEqual(gesehen["sslmode"], "verify-full")
        self.assertEqual(gesehen["sslrootcert"], "/ca.crt")
        self.assertEqual(gesehen["ssl_min_protocol_version"], "TLSv1.2")
        self.assertEqual(gesehen["gssencmode"], "disable")                    # keine Kerberos-Verschlüsselung statt TLS
        self.assertEqual(gesehen["channel_binding"], "prefer")
        self.assertEqual((gesehen["keepalives"], gesehen["keepalives_idle"], gesehen["keepalives_count"]), (1, 10, 3))
        self.assertEqual(gesehen["tcp_user_timeout"], 15000)                  # tote Leitung: nicht bis zum TCP-Timeout hängen
        self.assertEqual(gesehen["connect_timeout"], W.VERBINDEN_SECS)
        self.assertIn(f"statement_timeout={W.ANWEISUNG_MS}", gesehen["options"])
        self.assertIn("lock_timeout", gesehen["options"])
        self.assertIn("idle_in_transaction_session_timeout", gesehen["options"])
        self.assertEqual(gesehen["application_name"], "test")
        self.assertFalse(gesehen["autocommit"])
        self.assertTrue(m.exception.vorlaeufig)
        self.assertEqual(m.exception.sqlstate, "")
        self.assertNotIn(PASSWORT, str(m.exception) + repr(m.exception.__dict__))

    def test_anwendungsname_wird_geprueft(self):
        for name in ("", "a b", "x;y", "a" * 64):
            with self.assertRaises(ValueError):
                W.Pg(self.ZIEL, anwendung=name)

    def test_fehlerabbildung(self):
        class E(Exception):
            def __init__(self, sqlstate, text="geheimer servertext"):
                self.sqlstate = sqlstate
                self.diag = type("D", (), {"sqlstate": sqlstate, "message_primary": text})()

        for sqlstate, vorlaeufig in (("23505", False), ("23514", False), ("42501", False), ("22P02", False), ("P0001", False),
                                     ("3D000", False), ("XX000", False), ("42P01", False),
                                     ("08006", True), ("53300", True), ("57P01", True), ("40001", True), ("40P01", True), ("58000", True),
                                     ("55P03", True), ("25006", True), ("28P01", True), ("28000", True)):
            f = W._abbilden(E(sqlstate))
            self.assertEqual((f.sqlstate, f.vorlaeufig), (sqlstate, vorlaeufig), sqlstate)
            self.assertNotIn("geheimer", str(f))
        for unfug in ("", "abc", "123456", "23-05", None):                      # ohne gültigen SQLSTATE: ein Programmfehler, nicht wiederholen
            f = W._abbilden(E(unfug))
            self.assertEqual((f.sqlstate, f.vorlaeufig), ("", False))

    def test_ohne_sqlstate_gilt_nur_ein_verbindungsfehler_als_vorlaeufig(self):
        class OperationalError(Exception):                                      # so heißt der Fehler des Treibers (psycopg)
            pass

        class ConnectionTimeout(OperationalError):
            pass

        class ProgrammingError(Exception):
            pass

        for fehler, vorlaeufig in ((OSError("x"), True), (TimeoutError("x"), True), (ConnectionRefusedError("x"), True),
                                   (OperationalError("x"), True), (ConnectionTimeout("x"), True),
                                   (ImportError("x"), False), (ProgrammingError("x"), False), (ValueError("x"), False),
                                   (TypeError("x"), False), (RuntimeError("x"), False)):
            f = W._abbilden(fehler)
            self.assertEqual((f.sqlstate, f.vorlaeufig), ("", vorlaeufig), type(fehler).__name__)

    def test_pg_umgebung_wird_entfernt(self):
        gesetzt = {"PGSSLMODE": "disable", "PGSSLROOTCERT": "system", "PGSERVICE": "x", "PGOPTIONS": "-c x=1", "PGHOST": "evil",
                   "PGSSLCERT": "/tmp/c", "PGGSSENCMODE": "prefer", "PGCHANNELBINDING": "disable", "NICHT_PG": "bleibt"}
        with mock.patch.dict(os.environ, gesetzt):
            W.Pg(self.ZIEL, anwendung="t")
            self.assertEqual([k for k in os.environ if k.startswith("PG")], [])
            self.assertEqual(os.environ["NICHT_PG"], "bleibt")

    def test_transaktion_rollback_und_schliessen(self):
        ereignisse = []

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                raise RuntimeError("boom")

        class Conn:
            def cursor(self):
                return Cur()

            def commit(self):
                ereignisse.append("commit")

            def rollback(self):
                ereignisse.append("rollback")

            def close(self):
                ereignisse.append("close")

        pg = W.Pg(self.ZIEL, anwendung="t", verbinden=lambda ziel: Conn())
        with self.assertRaises(W.PgFehler):
            pg.transaktion(lambda cur: cur.execute("x"))
        self.assertEqual(ereignisse, ["rollback", "close"])

    def test_adapter_nehmen_nur_feste_schemanamen(self):
        pg = W.Pg(self.ZIEL, anwendung="t", verbinden=lambda z: None)
        for schema in ("public", "wamo_dev; drop table x", "WAMO_DEV", "", "wamo_x"):
            with self.assertRaises(ValueError):
                RPG.ReleaseDatenbank(pg, schema)
            with self.assertRaises(ValueError):
                FreigabePg(pg, schema)
        RPG.ReleaseDatenbank(pg, "wamo_test")
        FreigabePg(pg, "wamo_prod")

    def test_vorlaeufige_fehler_werden_zu_datenbankfehler_regeln_zu_abgelehnt(self):
        class Wirft:
            def __init__(self, fehler):
                self.fehler = fehler

            def eine(self, *a, **k):
                raise self.fehler

        db = RPG.ReleaseDatenbank(Wirft(W.PgFehler("08006", "x", True)), "wamo_test")
        with self.assertRaises(RDB.DatenbankFehler):
            db.anzahl_admins()
        db = RPG.ReleaseDatenbank(Wirft(W.PgFehler("42501", "permission denied for table release_freigaben", False)), "wamo_test")
        with self.assertRaises(RDB.DatenbankAbgelehnt) as m:
            db.anzahl_admins()
        self.assertEqual(m.exception.code, "42501")
        fdb = FreigabePg(Wirft(W.PgFehler("23514", "release_freigaben: ...", False)), "wamo_test")
        with self.assertRaises(F.DatenbankAbgelehnt):
            fdb.anfrage_lesen("423e4567-e89b-12d3-a456-426614174000")

    def test_ungueltige_kennungen_erreichen_die_datenbank_nicht(self):
        class Verboten:
            def eine(self, *a, **k):
                raise AssertionError("darf nicht fragen")

            zeilen = eine

        rdb = RPG.ReleaseDatenbank(Verboten(), "wamo_test")
        fdb = FreigabePg(Verboten(), "wamo_test")
        self.assertFalse(rdb.ist_admin("kein-uuid; drop"))
        self.assertFalse(fdb.ist_admin("x"))
        self.assertIsNone(fdb.anfrage_lesen("x' or '1'='1"))
        with self.assertRaises(F.DatenbankAbgelehnt):
            fdb.freigabe_schreiben("x", "y", "z", "w", 1.0, None)
        with self.assertRaises(RDB.DatenbankAbgelehnt):
            rdb.ereignis_anhaengen("kein-uuid", "ereignis", {})
        with self.assertRaises(RDB.DatenbankAbgelehnt):
            rdb.ereignis_anhaengen(None, "GROSS", {})
        with self.assertRaises(RDB.DatenbankAbgelehnt):
            rdb.ereignis_anhaengen(None, "ok", {"x": "y" * 5000})


# ================================================================================================ Teil 2: echtes Postgres
ADMIN = "123e4567-e89b-12d3-a456-426614174000"
ADMIN2 = "223e4567-e89b-12d3-a456-426614174000"
MITGLIED = "323e4567-e89b-12d3-a456-426614174000"
KEIN_PROFIL = "923e4567-e89b-12d3-a456-426614174000"
UNBEKANNT = "523e4567-e89b-12d3-a456-426614174000"
COMMIT = "a" * 40
TREE = "b" * 40
SCHEMA = "wamo_test"
ISS = "https://test.example/auth/v1"
AUD = "authenticated"
SCHLUESSEL = ec.generate_private_key(ec.SECP256R1())

CONTAINER = None
PORT = None


def docker(*args, eingabe=None, pruefen=True, timeout=180):
    r = subprocess.run(["docker", *args], input=eingabe, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    if pruefen and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])} fehlgeschlagen: {r.stderr.strip()[:300]}")
    return r


def psql(sql: str):
    return docker("exec", "-i", CONTAINER, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "--single-transaction", "-q", "-At",
                  eingabe=sql)


def container_starten():
    """Startet das Wegwerf-Postgres und legt Migrationen, Rollen und Profile an (einmal je Lauf, erst für Teil 2)."""
    global CONTAINER, PORT
    if CONTAINER is not None or not (PSYCOPG and DOCKER):
        return
    if docker("info", pruefen=False, timeout=30).returncode != 0:
        return
    name = "wamo-test-pg-" + secrets.token_hex(4)
    docker("run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=" + secrets.token_hex(12), "-p", "127.0.0.1::5432",
           "pgvector/pgvector:pg16", timeout=600)
    CONTAINER = name
    for _ in range(60):
        if docker("exec", name, "pg_isready", "-U", "postgres", pruefen=False).returncode == 0:
            time.sleep(1)  # der Server startet beim ersten Mal einmal neu
            if docker("exec", name, "pg_isready", "-U", "postgres", pruefen=False).returncode == 0:
                break
        time.sleep(1)
    PORT = int(docker("port", name, "5432/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1])
    psql("""
      create role anon nologin; create role authenticated nologin; create role service_role nologin;
      create schema auth; create table auth.users (id uuid primary key);
      create schema extensions;
    """)
    for nr in ("0001_wamo_init", "0013_release_freigaben", "0016_release_freigaben_lesen"):
        psql((MIGRATIONEN / f"{nr}.sql").read_text(encoding="utf-8").replace("__SCHEMA__", SCHEMA))
    # Anmeldekonten wie in der Produktion: Mitglied mit INHERIT, ohne SET (nie SET ROLE auf die Gruppenrolle)
    psql(f"""
      create role t_release login password '{PASSWORT}'; grant wamo_release to t_release with inherit true, set false;
      create role t_anker login password '{PASSWORT}'; grant wamo_anker to t_anker with inherit true, set false;
      create role t_core login password '{PASSWORT}'; grant wamo_core to t_core with inherit true, set false;
      insert into auth.users (id) values ('{ADMIN}'), ('{ADMIN2}'), ('{MITGLIED}');
      insert into {SCHEMA}.profiles (user_id, display_name, rolle, anrede, aktiv) values
        ('{ADMIN}', 'Admin Eins', 'admin', 'du', true), ('{ADMIN2}', 'Admin Zwei', 'admin', 'du', true),
        ('{MITGLIED}', 'Mitglied', 'mitglied', 'du', true);
    """)


def tearDownModule():
    if CONTAINER:
        docker("rm", "-f", CONTAINER, pruefen=False, timeout=60)  # läuft nur, wenn pytest Modul-Fixtures aufruft, auch bei Abbruch


def plain_verbinden(z):
    import psycopg as p

    return p.connect(host=z.host, port=z.port, dbname=z.dbname, user=z.user, password=z.password, sslmode="disable",
                     connect_timeout=3, options="-c statement_timeout=5000", autocommit=False)


def pg_als(rolle: str, port: int | None = None) -> W.Pg:
    return W.Pg(W.Ziel("127.0.0.1", port or PORT, "postgres", rolle, PASSWORT, ""), anwendung="test", verbinden=plain_verbinden)


def token(sub=ADMIN, alter=10):
    echt = int(time.time())
    claims = {"sub": sub, "iss": ISS, "aud": AUD, "aal": "aal2", "iat": echt, "exp": echt + 600,
              "amr": [{"method": "totp", "timestamp": time.time() - alter}]}
    return jwt.encode(claims, SCHLUESSEL, algorithm="ES256", headers={"kid": "k1"})


def schluessel(kid):
    if kid == "k1":
        return SCHLUESSEL.public_key()
    raise KeyError(kid)


@unittest.skipUnless(PSYCOPG and DOCKER, "psycopg oder Docker fehlt")
class EchteDatenbankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        container_starten()
        if CONTAINER is None:
            raise unittest.SkipTest("Docker läuft nicht")

    @staticmethod
    def leeren():
        """Die Tabellen sind nur anfügbar (Trigger, auch gegen TRUNCATE). Zwischen den Tests räumt der Eigentümer auf, mit
        abgeschalteten Triggern; nur im Test, die Rollen des Dienstes können das nie."""
        tabellen = ("release_freigaben", "release_ereignisse", "release_anfragen")
        psql("".join(f"alter table {SCHEMA}.{t} disable trigger user;" for t in tabellen)
             + "".join(f"delete from {SCHEMA}.{t};" for t in tabellen)
             + "".join(f"alter table {SCHEMA}.{t} enable trigger user;" for t in tabellen))

    def setUp(self):
        self.leeren()
        self.rel = RPG.ReleaseDatenbank(pg_als("t_release"), SCHEMA)
        self.anker = pg_als("t_anker")
        self.fpg = FreigabePg(self.anker, SCHEMA)

    def bis_wartet_freigabe(self, art="normal"):
        """Führt eine Anfrage durch den Release-Dienst bis wartet_freigabe; der Anker klassifiziert dazwischen."""
        a = self.rel.anfrage_anlegen(ADMIN, "main")
        self.rel.status_setzen(a, "uebernommen", {"basis_commit": "c" * 40, "c1_commit": "d" * 40})
        self.rel.status_setzen(a, "pruefung1", {})
        self.rel.status_setzen(a, "versionswahl", {"bump": "patch", "version_vorschlag": "0.1.1"})
        self.rel.status_setzen(a, "pruefung3", {"version_final": "0.1.1", "v_commit": COMMIT, "tree_id": TREE,
                                               "pruefstand": {"bauen": "ok", "tests": "ok"}})
        self.anker.zeilen(f"update {SCHEMA}.release_anfragen set art = %s, schutz_treffer = %s::jsonb where id = %s::uuid",
                          (art, json.dumps([] if art == "normal" else ["voice-core/dev_guard.py"]), a))
        self.rel.status_setzen(a, "wartet_freigabe", {})
        return a

    # ---------------------------------------------------------------- Release-Dienst
    def test_admin_und_anzahl(self):
        self.assertTrue(self.rel.ist_admin(ADMIN))
        self.assertFalse(self.rel.ist_admin(MITGLIED))
        self.assertFalse(self.rel.ist_admin(KEIN_PROFIL))
        self.assertEqual(self.rel.anzahl_admins(), 2)

    def test_anlegen_und_nur_eine_offene_anfrage(self):
        a = self.rel.anfrage_anlegen(ADMIN, "main")
        self.assertRegex(a, r"^[0-9a-f-]{36}$")
        self.assertEqual(self.rel.offene_anfragen(), [{"id": a, "status": "angefragt"}])
        with self.assertRaises(RDB.DatenbankAbgelehnt) as m:
            self.rel.anfrage_anlegen(ADMIN2, "main")
        self.assertEqual(m.exception.code, "23505")

    def test_mitglied_darf_nicht_beauftragen(self):
        with self.assertRaises(RDB.DatenbankAbgelehnt) as m:
            self.rel.anfrage_anlegen(MITGLIED, "main")
        self.assertIn(m.exception.code, ("42501", "23514"))

    def test_status_weg_bis_wartet_freigabe_und_lesen(self):
        a = self.bis_wartet_freigabe()
        self.assertEqual(self.rel.status_lesen(a), {"status": "wartet_freigabe", "art": "normal", "v_commit": COMMIT, "tree_id": TREE})
        self.assertIsNone(self.rel.status_lesen(UNBEKANNT))

    def test_rueckwaerts_und_unbekannte_anfrage_werden_abgelehnt(self):
        a = self.rel.anfrage_anlegen(ADMIN, "main")
        self.rel.status_setzen(a, "pruefung1", {})
        with self.assertRaises(RDB.DatenbankAbgelehnt) as m:
            self.rel.status_setzen(a, "uebernommen", {})
        self.assertEqual(m.exception.code, "23514")
        with self.assertRaises(RDB.DatenbankAbgelehnt):
            self.rel.status_setzen(UNBEKANNT, "pruefung1", {})

    def test_der_release_dienst_kann_nicht_freigeben_klassifizieren_oder_ueber_freigegeben_setzen(self):
        a = self.bis_wartet_freigabe()
        with self.assertRaises(RDB.DatenbankAbgelehnt) as m:
            self.rel.status_setzen(a, "freigegeben", {})                        # Zustand gehört dem Anker
        self.assertEqual(m.exception.code, "42501")
        nur_release = pg_als("t_release")
        for sql, parameter in (
            (f"insert into {SCHEMA}.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) "
             "values (%s::uuid, %s::uuid, %s, %s, now())", (a, ADMIN, COMMIT, TREE)),
            (f"update {SCHEMA}.release_anfragen set art = 'sonder' where id = %s::uuid", (a,)),
            (f"update {SCHEMA}.release_anfragen set schutz_treffer = '[]'::jsonb where id = %s::uuid", (a,)),
            (f"delete from {SCHEMA}.release_anfragen where id = %s::uuid", (a,)),
            (f"insert into {SCHEMA}.release_stand (komponente, aktive_version) values ('ui', '0.1.0')", ()),
        ):
            with self.assertRaises(W.PgFehler, msg=sql[:50]) as m2:
                nur_release.zeilen(sql, parameter)
            self.assertEqual(m2.exception.sqlstate, "42501", sql[:50])

    def test_ereignisse_haengen_an_und_die_kette_stimmt(self):
        a = self.rel.anfrage_anlegen(ADMIN, "main")
        for i in range(3):
            self.rel.ereignis_anhaengen(a, "test.ereignis", {"nr": i}, akteur=ADMIN)
        self.rel.ereignis_anhaengen(None, "system.start", {})
        self.assertIsNone(self.anker.eine(f"select {SCHEMA}.release_kette_pruefen()")[0])   # null = die Kette ist in Ordnung
        self.assertEqual(self.anker.eine(f"select count(*) from {SCHEMA}.release_ereignisse")[0], 4)
        with self.assertRaises(W.PgFehler) as m:                                     # Ereignisse sind nur anfügbar
            pg_als("t_release").zeilen(f"update {SCHEMA}.release_ereignisse set ereignis = 'x'")
        self.assertEqual(m.exception.sqlstate, "42501")

    def test_der_kern_darf_nur_lesen(self):
        a = self.bis_wartet_freigabe()
        core = pg_als("t_core")
        with self.assertRaises(W.PgFehler) as m:
            core.zeilen(f"insert into {SCHEMA}.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit) "
                        "values (%s::uuid, %s::uuid, %s, %s, now())", (a, ADMIN, COMMIT, TREE))
        self.assertEqual(m.exception.sqlstate, "42501")
        self.assertEqual(len(core.zeilen(f"select 1 from {SCHEMA}.release_anfragen")), 1)

    def test_verbindung_zu_totem_port_ist_vorlaeufig(self):
        with self.assertRaises(RDB.DatenbankFehler):
            RPG.ReleaseDatenbank(pg_als("t_release", port=1), SCHEMA).anzahl_admins()

    # ---------------------------------------------------------------- Freigabe-Dienst
    def dienst(self, db=None):
        return F.Freigabedienst(db or self.fpg, schluessel=schluessel, aussteller=ISS, zielgruppe=AUD)

    def test_freigabe_ende_zu_ende_mit_echtem_token(self):
        a = self.bis_wartet_freigabe()
        r = self.dienst().freigeben(token=token(), anfrage=a, commit_id=COMMIT, tree_id=TREE, quell_ip="192.168.178.20")
        self.assertTrue(r["ok"])
        z = self.anker.eine(f"select freigeber::text, commit_id, tree_id, host(quell_ip), (now() - aal2_zeit) < interval '30 seconds' "
                            f"from {SCHEMA}.release_freigaben where id = %s::uuid", (r["freigabe"],))
        self.assertEqual(z, (ADMIN, COMMIT, TREE, "192.168.178.20", True))

    def test_ganzer_weg_bis_veroeffentlicht_mit_den_echten_rollen(self):
        """Nach der Freigabe setzt der Anker freigegeben, danach der Release-Dienst veroeffentlicht. Der Trigger prüft dabei, dass die
        Freigabe noch gilt (er liest release_freigaben); das muss mit den Rechten der Rolle wamo_release gehen."""
        a = self.bis_wartet_freigabe()
        self.dienst().freigeben(token=token(), anfrage=a, commit_id=COMMIT, tree_id=TREE)
        self.anker.zeilen(f"update {SCHEMA}.release_anfragen set status = 'freigegeben' where id = %s::uuid", (a,))
        self.rel.status_setzen(a, "veroeffentlicht", {})
        self.assertEqual(self.rel.status_lesen(a)["status"], "veroeffentlicht")

    def test_migration_0016_ist_wiederholbar_und_hat_einen_rueckweg(self):
        hoch = (MIGRATIONEN / "0016_release_freigaben_lesen.sql").read_text(encoding="utf-8").replace("__SCHEMA__", SCHEMA)
        runter = (MIGRATIONEN / "0016_release_freigaben_lesen.down.sql").read_text(encoding="utf-8").replace("__SCHEMA__", SCHEMA)
        psql(hoch)                                                                   # zweiter Lauf ändert nichts und bricht nicht ab
        try:
            psql(runter)
            namen = "('release_freigabe_gilt', 'release_freigabe_nicht_widerrufen')"
            self.assertEqual(psql(f"select count(*) from pg_proc where proname in {namen}").stdout.strip(), "0")
            a = self.bis_wartet_freigabe()
            self.dienst().freigeben(token=token(), anfrage=a, commit_id=COMMIT, tree_id=TREE)
            self.anker.zeilen(f"update {SCHEMA}.release_anfragen set status = 'freigegeben' where id = %s::uuid", (a,))
            with self.assertRaises(RDB.DatenbankAbgelehnt) as m:                     # der Fehler von 0013 ist ohne 0016 wieder da
                self.rel.status_setzen(a, "veroeffentlicht", {})
            self.assertEqual(m.exception.code, "42501")
        finally:
            psql(hoch)                                                               # die anderen Tests brauchen 0016 wieder
        self.rel.status_setzen(a, "veroeffentlicht", {})
        merk = psql(f"select count(*) from {SCHEMA}.schema_migrations where version = '0016_release_freigaben_lesen'")
        self.assertEqual(merk.stdout.strip(), "1")

    def test_widerruf_nach_freigegeben_verhindert_das_veroeffentlichen(self):
        a = self.bis_wartet_freigabe()
        self.dienst().freigeben(token=token(), anfrage=a, commit_id=COMMIT, tree_id=TREE)
        self.anker.zeilen(f"update {SCHEMA}.release_anfragen set status = 'freigegeben' where id = %s::uuid", (a,))
        self.anker.zeilen(f"update {SCHEMA}.release_freigaben set widerrufen_at = now() where anfrage_id = %s::uuid", (a,))
        with self.assertRaises(RDB.DatenbankAbgelehnt) as m:
            self.rel.status_setzen(a, "veroeffentlicht", {})
        self.assertEqual(m.exception.code, "23514")
        self.assertEqual(self.rel.status_lesen(a)["status"], "freigegeben")

    def test_freigegeben_verlangt_eine_gueltige_freigabe(self):
        a = self.bis_wartet_freigabe()
        with self.assertRaises(W.PgFehler) as m:                                  # noch keine Freigabe: der Anker darf nicht
            self.anker.zeilen(f"update {SCHEMA}.release_anfragen set status = 'freigegeben' where id = %s::uuid", (a,))
        self.assertEqual(m.exception.sqlstate, "23514")

    def test_die_hilfsfunktionen_darf_nur_ausfuehren_wer_soll(self):
        for rolle, soll in (("t_release", True), ("t_anker", True), ("t_core", False)):
            try:
                pg_als(rolle).zeilen(f"select {SCHEMA}.release_freigabe_nicht_widerrufen(%s::uuid, %s, %s)", (UNBEKANNT, COMMIT, TREE))
                darf = True
            except W.PgFehler as e:
                self.assertEqual(e.sqlstate, "42501")
                darf = False
            self.assertEqual(darf, soll, rolle)

    def test_zweite_freigabe_derselben_person_und_veraltete_fassung(self):
        a = self.bis_wartet_freigabe()
        d = self.dienst()
        d.freigeben(token=token(), anfrage=a, commit_id=COMMIT, tree_id=TREE)
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(), anfrage=a, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "schon_freigegeben")
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(sub=ADMIN2), anfrage=a, commit_id="e" * 40, tree_id=TREE)
        self.assertEqual(m.exception.code, "fassung_passt_nicht")

    def test_sonder_mit_echten_triggern(self):
        a = self.bis_wartet_freigabe(art="sonder")
        d = self.dienst()
        with self.assertRaises(F.FreigabeFehler) as m:                               # der Beauftragte selbst
            d.freigeben(token=token(sub=ADMIN), anfrage=a, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "keine_berechtigung")
        with self.assertRaises(F.FreigabeFehler) as m:                               # die Wartezeit (15 Minuten) läuft noch
            d.freigeben(token=token(sub=ADMIN2), anfrage=a, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "wartezeit")
        # Wartezeit verkürzen: Der Trigger friert die Zeile ein, deshalb als Eigentümer mit abgeschalteten Triggern (nur im Test)
        psql(f"alter table {SCHEMA}.release_anfragen disable trigger user; "
             f"update {SCHEMA}.release_anfragen set wartet_bis = now() - interval '1 minute'; "
             f"alter table {SCHEMA}.release_anfragen enable trigger user;")
        self.assertEqual(d.freigeben(token=token(sub=ADMIN2), anfrage=a, commit_id=COMMIT, tree_id=TREE)["gilt_s"], 86400)

    def test_mitglied_und_unbekannte_anfrage(self):
        a = self.bis_wartet_freigabe()
        d = self.dienst()
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(sub=MITGLIED), anfrage=a, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "keine_berechtigung")
        with self.assertRaises(F.FreigabeFehler) as m:
            d.freigeben(token=token(), anfrage=UNBEKANNT, commit_id=COMMIT, tree_id=TREE)
        self.assertEqual(m.exception.code, "anfrage_unbekannt")

    # ---------------------------------------------------------------- Attrappe gegen Wirklichkeit
    FAELLE = (
        ("normal ok", "normal", ADMIN, -10, COMMIT, TREE),
        ("kein admin", "normal", MITGLIED, -10, COMMIT, TREE),
        ("aal2 zu alt", "normal", ADMIN, -61, COMMIT, TREE),
        ("aal2 zukunft", "normal", ADMIN, 30, COMMIT, TREE),
        ("falscher commit", "normal", ADMIN, -10, "e" * 40, TREE),
        ("falscher tree", "normal", ADMIN, -10, COMMIT, "e" * 40),
        ("sonder selbst", "sonder", ADMIN, -10, COMMIT, TREE),
        ("sonder wartezeit", "sonder", ADMIN2, -10, COMMIT, TREE),
    )

    @staticmethod
    def _code(db, anfrage, freigeber, aal2, commit, tree):
        """Der Code, den freigabe.py aus der Antwort der Datenbank macht (oder "ok")."""
        try:
            db.freigabe_schreiben(anfrage, freigeber, commit, tree, aal2, None)
            return "ok"
        except F.DatenbankAbgelehnt as e:
            return F.Freigabedienst._abbildung(e).code

    def test_attrappe_entscheidet_wie_die_echte_datenbank(self):
        """Dieselben Fälle gegen die echte Datenbank und die Attrappe: Die Codes müssen übereinstimmen. Geschrieben wird direkt über
        freigabe_schreiben, damit die Antworten der Trigger (nicht nur die Vorprüfungen des Dienstes) verglichen werden."""
        for name, art, freigeber, versatz, commit, tree in self.FAELLE:
            self.leeren()
            a = self.bis_wartet_freigabe(art=art)
            aal2 = time.time() + versatz
            echt = self._code(self.fpg, a, freigeber, aal2, commit, tree)
            att = F.AttrappeFreigabeDatenbank()
            att.admins = {ADMIN, ADMIN2}
            att.anfragen[a] = {"status": "wartet_freigabe", "art": art, "v_commit": COMMIT, "tree_id": TREE, "angefordert_von": ADMIN,
                               "wartet_bis": time.time() + 900 if art == "sonder" else None}
            self.assertEqual(self._code(att, a, freigeber, aal2, commit, tree), echt, name)


if __name__ == "__main__":
    unittest.main()
