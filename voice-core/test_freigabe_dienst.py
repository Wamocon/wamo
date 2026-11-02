"""Tests des Prozesses des Freigabe-Dienstes (infra/sokrates2/anker/freigabe_dienst.py, Schritt 1.4b).

Die Anwendung (HTTP-Eingabe zu Statuscode und JSON), die Schlüsselquelle und die Selbstprüfung laufen überall ohne Netz und ohne
Datenbank: eigene ES256-Wegwerfschlüssel, Attrappe der Datenbank. Der echte Unix-Socket (Gegenstelle per SO_PEERCRED, HTTP-Hülle,
Grenzen) läuft nur unter Linux. Die Regeln selbst prüft test_freigabe.py.

Ausführen:  uv run python -m pytest -q test_freigabe_dienst.py   (in voice-core/)
"""

import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import jwt

import test_freigabe as T  # nur die Hilfen (Token, Schlüssel, Kennungen), nicht die Testklassen

ANKER = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "anker"
sys.path.insert(0, str(ANKER))

import freigabe as F  # noqa: E402
import freigabe_dienst as D  # noqa: E402

POSIX_SOCKET = hasattr(socket, "AF_UNIX") and hasattr(socket, "SO_PEERCRED") and sys.platform.startswith("linux")
PFAD = f"/wamo/release/{T.ANFRAGE}/freigeben"
KOPF = {"content-type": "application/json"}


def koerper(**kw) -> bytes:
    return json.dumps({"commit_id": T.COMMIT, "tree_id": T.TREE, **kw}).encode()


class Basis(unittest.TestCase):
    def setUp(self):
        self.db = F.AttrappeFreigabeDatenbank(uhr=lambda: T.JETZT)
        self.db.admins = {T.ADMIN, T.ADMIN2}
        self.db.anfragen[T.ANFRAGE] = {"status": "wartet_freigabe", "art": "normal", "v_commit": T.COMMIT, "tree_id": T.TREE,
                                       "angefordert_von": T.ADMIN, "wartet_bis": None}
        self.dienst = F.Freigabedienst(self.db, schluessel=T.schluessel, aussteller=T.ISS, zielgruppe=T.AUD, uhr=lambda: T.JETZT)
        self.app = D.Anwendung(self.dienst)

    def post(self, tok=None, pfad=PFAD, body=None, kopf=None, methode="POST"):
        k = {**KOPF, "authorization": f"Bearer {tok if tok is not None else T.token()}", **(kopf or {})}
        return self.app.verarbeite(methode, pfad, k, koerper() if body is None else body)


class AnwendungTests(Basis):
    def test_gueltige_freigabe(self):
        status, a = self.post(kopf={"x-forwarded-for": "203.0.113.7"})
        self.assertEqual((status, a["ok"], a["gilt_s"]), (200, True, 3600))
        f = self.db.freigaben[0]
        self.assertEqual((f["anfrage"], f["freigeber"], f["commit_id"], f["tree_id"], f["quell_ip"]),
                         (T.ANFRAGE, T.ADMIN, T.COMMIT, T.TREE, "203.0.113.7"))

    def test_andere_pfade_und_methoden(self):
        for pfad in ("/", "/wamo/release/x/freigeben", f"/wamo/release/{T.ANFRAGE}/freigeben/", f"/wamo/release/{T.ANFRAGE}/freigeben?a=1",
                     f"/wamo/release/{T.ANFRAGE.upper()}/freigeben", f"/x/wamo/release/{T.ANFRAGE}/freigeben", "/wamo/release/../freigeben"):
            self.assertEqual(self.post(pfad=pfad)[0], 404, pfad)
        for m in ("GET", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            self.assertEqual(self.post(methode=m)[0], 405, m)
        self.assertEqual(self.db.freigaben, [])

    def test_token_form(self):
        faelle = [({}, 401), ({"authorization": "Basic abc"}, 401), ({"authorization": "Bearer"}, 401), ({"authorization": "bearer x"}, 401),
                  ({"authorization": "Bearer a b"}, 401), ({"authorization": "Bearer " + "x" * 9000}, 401)]
        for kopf, erwartet in faelle:
            k = {**KOPF, **kopf}
            self.assertEqual(self.app.verarbeite("POST", PFAD, k, koerper())[0], erwartet, kopf)
        self.assertEqual(self.post(tok=T.token(key=T.FREMD))[0], 401)
        self.assertEqual(self.db.freigaben, [])

    def test_body_form(self):
        for body in (b"", b"nicht json", b"[]", b'"x"', b"null", b"\xff\xfe", b'{"commit_id": 1, "tree_id": 2}', b"{}"):
            status, a = self.post(body=body)
            self.assertEqual((status, a["grund"]), (400, "angaben"), body)
        self.assertEqual(self.post(kopf={"content-type": "text/plain"})[1]["grund"], "angaben")
        self.assertEqual(self.db.freigaben, [])

    def test_fehlercodes_haben_ihre_statuscodes(self):
        self.assertEqual(self.post(tok=T.token(alter=120))[0:2], (403, {"ok": False, "grund": "aal2_alt", "text": F.FEHLER["aal2_alt"][0]}))
        self.assertEqual(self.post(body=koerper(tree_id="c" * 40))[1]["grund"], "fassung_passt_nicht")
        self.assertEqual(self.post(tok=T.token(sub=T.MITGLIED))[1]["grund"], "keine_berechtigung")
        self.assertEqual(self.post()[0], 200)
        self.assertEqual(self.post()[1]["grund"], "schon_freigegeben")
        self.db.ausfall = True
        self.assertEqual(self.post(tok=T.token(sub=T.ADMIN2))[0], 503)

    def test_drosselung_antwortet_429(self):
        codes = [self.post(tok=T.token(sub=T.MITGLIED))[0] for _ in range(F.VERSUCHE_MAX + 1)]
        self.assertEqual(codes[-1], 429)

    def test_unerwartete_panne_gibt_500_ohne_text(self):
        with mock.patch.object(self.dienst, "freigeben", side_effect=RuntimeError("geheimer Text mit Passwort")):
            status, a = self.post()
        self.assertEqual((status, a["grund"]), (500, "intern"))
        self.assertNotIn("geheim", json.dumps(a))

    def test_antworten_enthalten_nie_das_token(self):
        tok = T.token(alter=500)
        _status, a = self.post(tok=tok)
        self.assertNotIn(tok, json.dumps(a))
        self.assertNotIn(tok.split(".")[1], json.dumps(a))

    def test_quell_ip(self):
        q = D.Anwendung.quell_ip
        self.assertEqual(q("198.51.100.1, 10.0.0.2"), "10.0.0.2")
        self.assertEqual(q("2001:db8::1"), "2001:db8::1")
        self.assertEqual(q(" 192.0.2.5 "), "192.0.2.5")
        for schlecht in (None, "", "abc", "1.2.3", "fe80::1%eth0", "1.2.3.4; drop table", "x" * 600, "1.2.3.4,"):
            self.assertIsNone(q(schlecht), schlecht)
        # eine kaputte Angabe verhindert die Freigabe nie
        self.assertEqual(self.post(kopf={"x-forwarded-for": "müll"})[0], 200)
        self.assertIsNone(self.db.freigaben[0]["quell_ip"])


# ------------------------------------------------------------------------------------------------ Schlüsselquelle
def jwk(key, kid, **extra) -> dict:
    return {**jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": kid, "use": "sig", **extra}


class JwksTests(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.abrufe = 0
        self.satz = {"keys": [jwk(T.SCHLUESSEL, "k1")]}
        self.ausfall = False

    def quelle(self):
        def abrufen():
            self.abrufe += 1
            if self.ausfall:
                raise OSError("Netz weg")
            return json.dumps(self.satz).encode()

        return D.JwksQuelle(abrufen, uhr=lambda: self.t[0])

    def test_bekannter_schluessel_verifiziert_ein_token(self):
        q = self.quelle()
        claims = jwt.decode(T.token(), q("k1"), algorithms=["ES256"], audience=T.AUD, issuer=T.ISS)
        self.assertEqual(claims["sub"], T.ADMIN)
        self.assertEqual(self.abrufe, 1)
        q("k1")
        self.assertEqual(self.abrufe, 1)  # gepuffert

    def test_kid_fehlt_oder_ist_kein_text(self):
        q = self.quelle()
        for kid in (None, 5, b"k1", ["k1"]):
            with self.assertRaises(KeyError):
                q(kid)
        self.assertEqual(self.abrufe, 0)

    def test_unbekannte_kennung_loest_hoechstens_alle_30_sekunden_einen_abruf_aus(self):
        q = self.quelle()
        for _ in range(20):
            with self.assertRaises(KeyError):
                q("erfunden")
        self.assertEqual(self.abrufe, 1)
        self.t[0] += 31
        with self.assertRaises(KeyError):
            q("erfunden")
        self.assertEqual(self.abrufe, 2)

    def test_rotation_neuer_schluessel_nach_dem_mindestabstand(self):
        q = self.quelle()
        q("k1")
        self.satz = {"keys": [jwk(T.SCHLUESSEL, "k1"), jwk(T.FREMD, "k2")]}
        self.t[0] += 31
        self.assertIsNotNone(q("k2"))

    def test_fehlgeschlagener_abruf_behaelt_den_alten_satz_nur_begrenzt(self):
        q = self.quelle()
        q("k1")
        self.ausfall = True
        self.t[0] += D.JWKS_TTL_S + 1
        self.assertIsNotNone(q("k1"))  # abgelaufen, Abruf scheitert: der alte Satz gilt weiter
        self.t[0] += D.JWKS_ALT_MAX_S
        with self.assertRaises(KeyError):
            q("k1")

    def test_nur_ec_p256_zum_signieren(self):
        rsa_aehnlich = {"kty": "RSA", "kid": "r1", "n": "AQAB", "e": "AQAB", "use": "sig"}
        self.satz = {"keys": [rsa_aehnlich, jwk(T.SCHLUESSEL, "p384", crv="P-384"), jwk(T.SCHLUESSEL, "enc", use="enc"),
                              {"kty": "EC", "crv": "P-256", "x": "a", "y": "b"}, "müll", jwk(T.SCHLUESSEL, "ok")]}
        q = self.quelle()
        self.assertIsNotNone(q("ok"))
        for kid in ("r1", "p384", "enc"):
            self.t[0] += 31
            with self.assertRaises(KeyError):
                q(kid)

    def test_kaputte_antworten_sind_kein_absturz(self):
        for roh in (b"", b"nicht json", b"[]", b'{"keys": 5}', b'{"keys": [null]}'):
            q = D.JwksQuelle(lambda roh=roh: roh, uhr=lambda: self.t[0])
            with self.assertRaises(KeyError):
                q("k1")

    def test_abruf_nur_ueber_https(self):
        with self.assertRaises(ValueError):
            D.jwks_abrufen("http://192.168.178.136:55443/auth/v1/.well-known/jwks.json", "/nope")


# ------------------------------------------------------------------------------------------------ Selbstprüfung
class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="freigabe-inst-")
        self.addCleanup(self.tmp.cleanup)
        self.app = Path(self.tmp.name, "app")
        self.lib = Path(self.tmp.name, "pylib")
        for ordner in (self.app, self.lib):
            ordner.mkdir()
        (self.app / "a.py").write_text("print(1)\n")
        (self.lib / "x").mkdir()
        (self.lib / "x" / "m.py").write_text("y = 2\n")
        self.schreibe_summen()

    def schreibe_summen(self):
        for ordner, ziel in ((self.app, self.app / "SHA256SUMS"), (self.lib, Path(self.tmp.name, "pylib.SHA256SUMS"))):
            zeilen = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(ordner).as_posix()}"
                      for p in sorted(ordner.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
            ziel.write_text("\n".join(zeilen) + "\n")
        self.summen_lib = str(Path(self.tmp.name, "pylib.SHA256SUMS"))

    def pruefe(self):
        besitzer = os.getuid() if hasattr(os, "getuid") else 0
        return D.installation_pruefen(str(self.app), str(self.lib), self.summen_lib, besitzer=besitzer)

    @unittest.skipIf(os.name == "nt", "Dateirechte und Besitzer")
    def test_unveraendert_ist_in_ordnung(self):
        self.assertEqual(self.pruefe(), [])

    def test_geaenderte_datei(self):
        (self.app / "a.py").write_text("print(2)\n")
        self.assertIn("Prüfsumme weicht ab: a.py", self.pruefe())
        (self.app / "a.py").write_text("print(1)\n")
        (self.lib / "x" / "m.py").write_text("y = 3\n")
        self.assertIn("Prüfsumme weicht ab: x/m.py", self.pruefe())

    def test_fremde_und_fehlende_dateien(self):
        (self.app / "fremd.py").write_text("")
        (self.lib / "x" / "dazu.so").write_text("")
        self.assertTrue({"fremde Datei fremd.py", "fremde Datei x/dazu.so"} <= set(self.pruefe()))
        (self.app / "fremd.py").unlink()
        (self.lib / "x" / "dazu.so").unlink()
        (self.app / "a.py").unlink()
        self.assertIn("fehlt: a.py", self.pruefe())

    def test_fehlende_summendatei(self):
        (self.app / "SHA256SUMS").unlink()
        self.assertEqual(self.pruefe(), ["SHA256SUMS fehlt oder ist nicht lesbar"])

    @unittest.skipIf(os.name == "nt", "Dateirechte")
    def test_beschreibbare_datei_wird_gemeldet(self):
        os.chmod(self.app / "a.py", 0o666)
        self.assertEqual(self.pruefe(), ["Rechte: a.py"])


# ------------------------------------------------------------------------------------------------ der echte Socket (nur Linux)
@unittest.skipUnless(POSIX_SOCKET, "braucht Linux (AF_UNIX, SO_PEERCRED)")
class SocketTests(Basis):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix="fd-")
        self.addCleanup(self.tmp.cleanup)
        self.pfad = os.path.join(self.tmp.name, "f.sock")
        self.server = None
        self.starte({os.geteuid()})
        self.addCleanup(self.stoppe)

    def starte(self, erlaubt):
        self.server = D.Server(self.pfad, self.app, frozenset(erlaubt))
        self.faden = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.faden.start()

    def stoppe(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.faden.join(5)
            self.server = None

    def roh(self, daten: bytes) -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(self.pfad)
            s.sendall(daten)
            antwort = b""
            try:
                while teil := s.recv(4096):
                    antwort += teil
            except (ConnectionResetError, socket.timeout):
                pass
            return antwort

    def anfrage(self, tok=None, body=None, pfad=PFAD, extra="", methode="POST") -> tuple[int, dict, bytes]:
        b = koerper() if body is None else body
        kopf = (f"{methode} {pfad} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {tok or T.token()}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(b)}\r\n{extra}\r\n").encode()
        roh = self.roh(kopf + b)
        status = int(roh.split(b" ", 2)[1]) if roh else 0
        text = roh.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in roh else b""
        return status, (json.loads(text) if text else {}), roh

    def test_freigabe_ueber_den_socket(self):
        status, a, roh = self.anfrage(extra="X-Forwarded-For: 203.0.113.9\r\n")
        self.assertEqual((status, a["ok"]), (200, True))
        self.assertIn(b"Cache-Control: no-store", roh)
        self.assertNotIn(b"Python", roh)
        self.assertEqual(self.db.freigaben[0]["quell_ip"], "203.0.113.9")

    def test_socket_ist_nur_fuer_den_besitzer_und_die_gruppe(self):
        self.assertEqual(oct(os.stat(self.pfad).st_mode & 0o777), "0o660")

    def test_fehler_ueber_den_socket(self):
        self.assertEqual(self.anfrage(pfad="/anderes")[0], 404)
        self.assertEqual(self.anfrage(methode="GET")[0], 405)
        self.assertEqual(self.anfrage(tok=T.token(alter=500))[1]["grund"], "aal2_alt")

    def test_grenzen_der_huelle(self):
        self.assertEqual(self.anfrage(body=b"x" * (D.BODY_MAX + 1))[0], 413)
        chunked = f"POST {PFAD} HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n0\r\n\r\n".encode()
        self.assertIn(b" 400 ", self.roh(chunked).split(b"\r\n")[0])
        doppelt = f"POST {PFAD} HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{{}}".encode()
        self.assertIn(b" 400 ", self.roh(doppelt).split(b"\r\n")[0])
        self.assertIn(b'"grund": "anfrage"', self.roh(b"muell\r\n\r\n"))  # ohne HTTP-Version (0.9): nur der Körper, nie eine Freigabe
        zu_kurz = f"POST {PFAD} HTTP/1.1\r\nContent-Length: 50\r\n\r\n{{}}".encode()  # Body kürzer als angekündigt, Gegenstelle schließt
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(self.pfad)
            s.sendall(zu_kurz)
            s.shutdown(socket.SHUT_WR)
            self.assertIn(b" 400 ", s.recv(4096).split(b"\r\n")[0])
        self.assertEqual(self.db.freigaben, [])

    def test_fremde_gegenstelle_wird_ohne_antwort_geschlossen(self):
        self.stoppe()
        self.starte({os.geteuid() + 1})
        self.assertEqual(self.anfrage()[0], 0)
        self.assertEqual(self.db.freigaben, [])

    def test_rest_eines_sockets_wird_ersetzt_andere_dateien_nie(self):
        self.stoppe()
        self.starte({os.geteuid()})  # der Socket des vorigen Laufs lag noch da (server_close entfernt ihn nicht)
        self.assertEqual(self.anfrage()[0], 200)
        self.stoppe()
        Path(self.pfad).unlink(missing_ok=True)
        Path(self.pfad).write_text("wichtig")
        with self.assertRaises(SystemExit):
            D.Server(self.pfad, self.app, frozenset({os.geteuid()}))
        self.assertEqual(Path(self.pfad).read_text(), "wichtig")

    def test_viele_leere_verbindungen_blockieren_den_dienst_nicht_dauerhaft(self):
        offen = []
        for _ in range(D.PARALLEL_MAX + 4):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(self.pfad)
            offen.append(s)
        for s in offen:
            s.close()  # die Gegenseite sieht das Ende und gibt ihre Plätze frei
        self.assertEqual(self.anfrage()[0], 200)


if __name__ == "__main__":
    unittest.main()
