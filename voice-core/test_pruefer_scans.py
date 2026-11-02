"""Tests der deterministischen Prüfstufen (infra/sokrates2/release/pruefer/pruefer_scans.py, Schritt 1.2a, Konzept D.3).

Laufen überall ohne Netz, ohne Datenbank und ohne Werkzeuge. Werte, die wie Geheimnisse aussehen, werden erst zur Laufzeit zusammengesetzt,
damit weder ein Scanner von GitHub noch die eigene Stufe auf diese Testdatei anschlägt.

Ausführen:  uv run python -m pytest -q test_pruefer_scans.py   (in voice-core/)
"""

import base64
import json
import re
import sys
import unittest
from pathlib import Path

PRUEFER = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "release" / "pruefer"
sys.path.insert(0, str(PRUEFER))

import pruefer_scans as P  # noqa: E402
from pruefer_scans import Datei  # noqa: E402


def b(text: str) -> bytes:
    return text.encode("utf-8")


def neu(pfad: str, text: str) -> Datei:
    return Datei(pfad, b(text))


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


class HinzugefuegteZeilenTests(unittest.TestCase):
    def test_neue_datei_alle_zeilen(self):
        self.assertEqual(P.hinzugefuegte_zeilen(None, b("a\nb\n")), [(1, "a"), (2, "b")])

    def test_nur_neue_und_geaenderte_zeilen(self):
        self.assertEqual(P.hinzugefuegte_zeilen(b("a\nb\nc\n"), b("a\nX\nc\nd\n")), [(2, "X"), (4, "d")])

    def test_geloeschte_datei_und_unveraenderte(self):
        self.assertEqual(P.hinzugefuegte_zeilen(b("a\n"), None), [])
        self.assertEqual(P.hinzugefuegte_zeilen(b("a\nb\n"), b("a\nb\n")), [])

    def test_alter_bestand_wird_nicht_gemeldet(self):
        alt = "token = '" + "x" * 20 + "'\n"
        befund = P.secret_scan([Datei("a.py", b(alt + "x = 1\n"), b(alt))])
        self.assertEqual(befund.treffer_anzahl, 0)

    def test_zu_grosse_datei_macht_die_stufe_fehlerhaft(self):
        gross = b("\n".join(["x"] * (P.ZEILEN_MAX + 1)))
        befund = P.secret_scan([Datei("riesig.txt", gross)])
        self.assertEqual((befund.fehler, befund.zeile), ("zu_gross", "fehler"))

    def test_kaputte_bytes_sind_kein_absturz(self):
        self.assertEqual(P.hinzugefuegte_zeilen(None, b"\xff\xfe\x00abc")[0][0], 1)


class SecretScanTests(unittest.TestCase):
    def pruefe(self, text, pfad="a.py"):
        return P.secret_scan([neu(pfad, text)])

    def test_bekannte_formen_werden_erkannt(self):
        jwt_ = ".".join((b64({"alg": "ES256"}), b64({"sub": "x" * 20}), "s" * 20))
        faelle = {
            "anthropic_schluessel": "k = '" + "sk-ant-api03-" + "aB3_" * 25 + "'",
            "jwt": "t = " + jwt_,
            "privater_schluessel": "-----BEGIN " + "EC PRIVATE KEY-----\n" + "MHcCAQEEI" * 20 + "\n-----END " + "EC PRIVATE KEY-----",
            "github_token": "x = " + "ghp_" + "A" * 36,
            "aws_schluessel": "id = " + "AKIA" + "B" * 16,
            "slack_token": "s = " + "xoxb-" + "1" * 12,
            "zuweisung": "password = '" + "Zq8vL2mN5xR7tB3kW9pD" + "'",
        }
        for art, text in faelle.items():
            with self.subTest(art=art):
                befund = self.pruefe(text)
                self.assertEqual([t.art for t in befund.treffer], [art])
                self.assertEqual(befund.zeile, "fehler")

    def test_platzhalter_und_kopfzeilen_ohne_schluessel_sind_kein_treffer(self):
        kurz = "sk-ant-api03-" + "TEST-0123456789abcdefghij"          # wie die Testwerte im Repo, deutlich unter 80 Zeichen
        nur_kopf = "x = '-----BEGIN " + "RSA PRIVATE KEY-----'"         # Muster in einer Prüfung, kein Schlüsselkörper
        for text in (kurz, nur_kopf, "-----BEGIN " + "PRIVATE KEY-----\n-----END " + "PRIVATE KEY-----"):
            befund = self.pruefe(text)
            self.assertEqual((befund.treffer_anzahl, befund.zeile), (0, "ok"), text)

    def test_kopfzeile_vor_beliebigem_code_ist_kein_schluesselkoerper(self):
        code = "kopf = '-----BEGIN " + "OPENSSH PRIVATE KEY-----'\n" + "\n".join(f"wert_{i} = berechne_etwas_langes_{i}()" for i in range(20))
        self.assertEqual(self.pruefe(code).treffer_anzahl, 0)
        echt = "-----BEGIN " + "PRIVATE KEY-----\n" + "\n".join(["MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"] * 4)
        self.assertEqual(self.pruefe(echt).treffer_anzahl, 1)

    def test_passwortzuweisung_in_testdateien_wird_nur_angezeigt(self):
        text = "PASSWORT = '" + "Zq8vL2mN5xR7tB3kW9pD" + "'"
        for pfad in ("voice-core/test_x.py", "ui/src/lib/a.test.ts", "desktop/b.spec.js", "ui/src/__tests__/c.tsx"):
            befund = self.pruefe(text, pfad)
            self.assertEqual((befund.treffer_anzahl, befund.zeile, [h.art for h in befund.hinweise]), (0, "ok", ["testwert"]), pfad)
        for pfad in ("voice-core/x.py", "ui/src/lib/a.ts", "contest.py", "scripts/latest_run.sh"):
            self.assertEqual(self.pruefe(text, pfad).treffer_anzahl, 1, pfad)

    def test_echte_formen_blockieren_auch_in_testdateien(self):
        for text in ("t = " + "ghp_" + "A" * 36, "id = " + "AKIA" + "B" * 16, "k = " + "sk-ant-api03-" + "aB3_" * 25):
            self.assertEqual(self.pruefe(text, "voice-core/test_x.py").treffer_anzahl, 1, text)

    def test_der_gefundene_text_steht_nie_im_ergebnis(self):
        geheim = "sk-ant-api03-" + "Q" * 95
        befund = self.pruefe("k = '" + geheim + "'")
        self.assertNotIn("Q" * 10, repr(befund))

    def test_harmloses_besteht(self):
        for text in ("x = 1", "token = get_token()", "password = ''", "# sk-ant- steht im Kommentar", "api_key = os.environ['K']"):
            self.assertEqual(self.pruefe(text).treffer_anzahl, 0, text)
        self.assertEqual(self.pruefe("x = 1").zeile, "ok")

    def test_dateinamen(self):
        for pfad in (".env", "a/.env.local", "x/id_rsa", "k.pem", "d/server.key", ".npmrc", "gh-app.pem", "core.env", "geheim.env", "a.p12"):
            self.assertEqual(self.pruefe("x", pfad).treffer_anzahl, 1, pfad)
        for pfad in (".env.example", ".env.sample", "a/.env.template", "keyboard.py", "monkey.txt", "environment.py", "pem.md"):
            self.assertEqual(self.pruefe("x", pfad).treffer_anzahl, 0, pfad)

    def test_arbeitsbaum_wird_mitgeprueft(self):
        befund = P.secret_scan([neu("a.py", "x = 1")], arbeitsbaum=("infra/.env", "README.md"))
        self.assertEqual([(t.pfad, t.zeile) for t in befund.treffer], [("infra/.env", 0)])

    def test_geloeschte_datei_zaehlt_nicht(self):
        self.assertEqual(P.secret_scan([Datei(".env", None, b("x"))]).treffer_anzahl, 0)


class FremdverweisTests(unittest.TestCase):
    MUSTER = P.muster_laden(["# Kommentar", "", "fremdname", r"\bsonderwort\d+\b"])

    def test_inhalt_und_name(self):
        befund = P.fremdverweis([neu("a.md", "Hallo\nein FREMDNAME hier\nsonderwort42"), neu("fremdname/x.txt", "ok")], self.MUSTER)
        self.assertEqual(sorted((t.pfad, t.zeile, t.art) for t in befund.treffer),
                         [("a.md", 2, "inhalt"), ("a.md", 3, "inhalt"), ("fremdname/x.txt", 0, "name")])
        self.assertEqual(befund.zeile, "fehler")

    def test_nur_hinzugefuegtes_zaehlt(self):
        alt = b("fremdname im Bestand\n")
        self.assertEqual(P.fremdverweis([Datei("a.md", alt + b("neu\n"), alt)], self.MUSTER).treffer_anzahl, 0)

    def test_der_treffertext_steht_nicht_im_ergebnis(self):
        befund = P.fremdverweis([neu("a.md", "sonderwort42")], self.MUSTER)
        self.assertNotIn("sonderwort42", repr(befund))

    def test_mustervorlage_wird_geprueft(self):
        with self.assertRaises(ValueError):
            P.muster_laden(["# nur Kommentar"])
        with self.assertRaises(ValueError):
            P.muster_laden(["x" * (P.MUSTER_LAENGE_MAX + 1)])
        with self.assertRaises(ValueError):
            P.muster_laden([f"m{i}" for i in range(P.MUSTER_MAX + 1)])
        with self.assertRaises(re.error):
            P.muster_laden(["(unvollstaendig"])

    def test_sauberer_text_besteht(self):
        self.assertEqual(P.fremdverweis([neu("a.md", "WAMO ist der Assistent.")], self.MUSTER).zeile, "ok")


class WirkungsflaechenTests(unittest.TestCase):
    def scan(self, text, pfad="a.py", alt=None):
        return P.wirkungsflaechen([Datei(pfad, b(text), None if alt is None else b(alt))])

    def test_arten(self):
        faelle = {
            "prozess": ["import subprocess", "os." + "system('ls')", "x = require('child_process')", "run(c, shell=True)"],
            "auswerten": ["eval" + "(x)", "ex" + "ec(code)", "new " + "Function('a')"],
            "serialisierung": ["import pickle"],
            "netzcode": ["urllib.request.urlopen(u)", "requests.get(u)", "await fetch(u)", "new WebSocket(u)"],
            "sudo": ["sudo systemctl restart x"],
        }
        for art, texte in faelle.items():
            for t in texte:
                with self.subTest(art=art, text=t):
                    self.assertIn(art, [x.art for x in self.scan(t).treffer])

    def test_treffer_werden_angezeigt_und_nie_abgelehnt(self):
        befund = self.scan("import subprocess")
        self.assertEqual((befund.treffer_anzahl, befund.zeile, befund.sonder), (1, "ok", False))

    def test_netzziele_mit_rechnername(self):
        befund = self.scan("u = 'https://api.beispiel-dienst.de/v1'\nv = 'http://localhost:3000'\nw = 'https://www.w3.org/2000/svg'")
        self.assertEqual([(t.art, t.detail) for t in befund.treffer], [("netzziel", "api.beispiel-dienst.de")])

    def test_kaputte_adressen_bringen_die_stufe_nicht_zum_absturz(self):
        befund = self.scan("a = 'http://[::1'\nb = 'https://[x'\nc = 'http://ok.beispiel.de/'")
        self.assertEqual(sorted(t.detail for t in befund.treffer), ["ok.beispiel.de", "ungueltig", "ungueltig"])

    def test_geheimnisname_in_neuer_datei_ist_sonder(self):
        befund = self.scan("kek = os.environ['WAMO_SCHLUESSEL_KEK']")
        self.assertTrue(befund.sonder)
        self.assertEqual([(t.art, t.detail) for t in befund.treffer], [("geheimnisname", "WAMO_SCHLUESSEL_KEK")])

    def test_geheimnisname_im_eigenen_modul_ist_nur_ein_treffer(self):
        alt = "kek = os.environ['WAMO_SCHLUESSEL_KEK']\n"
        befund = self.scan(alt + "k2 = os.environ['WAMO_SCHLUESSEL_KEK']\n", alt=alt)
        self.assertEqual((befund.treffer_anzahl, befund.sonder), (1, False))

    def test_nur_code_und_skripte(self):
        self.assertEqual(self.scan("import subprocess", pfad="README.md").treffer_anzahl, 0)
        self.assertEqual(self.scan("sudo reboot", pfad="x.sh").treffer_anzahl, 1)

    def test_bestand_wird_nicht_gemeldet(self):
        alt = "import subprocess\n"
        self.assertEqual(self.scan(alt + "x = 1\n", alt=alt).treffer_anzahl, 0)

    def test_treffer_sind_begrenzt_die_zahl_bleibt_richtig(self):
        befund = self.scan("\n".join(["import pickle"] * (P.TREFFER_MAX + 50)))
        self.assertEqual((len(befund.treffer), befund.treffer_anzahl), (P.TREFFER_MAX, P.TREFFER_MAX + 50))


class AbhaengigkeitenTests(unittest.TestCase):
    UV_ALT = ('version = 1\n[[package]]\nname = "pyjwt"\nversion = "2.7.0"\nsource = { registry = "https://pypi.org/simple" }\n'
              '[[package]]\nname = "wamo-voice-core"\nversion = "0.1.0"\nsource = { editable = "." }\n')

    def uv(self, extra=""):
        return Datei("voice-core/uv.lock", b(self.UV_ALT + extra), b(self.UV_ALT))

    def test_neues_paket_von_pypi(self):
        extra = '[[package]]\nname = "rich"\nversion = "13.0.0"\nsource = { registry = "https://pypi.org/simple" }\n'
        befund = P.abhaengigkeiten([self.uv(extra)])
        self.assertEqual((befund.neue_pakete, befund.zeile), ([("rich", "13.0.0")], "ok"))

    def test_fremde_quelle_ist_ein_fehler(self):
        extra = ('[[package]]\nname = "boese"\nversion = "1.0"\nsource = { registry = "https://pakete.beispiel.de/simple" }\n'
                 '[[package]]\nname = "vomgit"\nversion = "1.0"\nsource = { git = "https://github.com/x/y?rev=abc#abc" }\n')
        befund = P.abhaengigkeiten([self.uv(extra)])
        self.assertEqual(befund.zeile, "fehler")
        self.assertEqual(sorted(t.detail for t in befund.treffer), ["boese (pakete.beispiel.de)", "vomgit (github.com)"])

    def test_lokales_paket_ohne_adresse_ist_in_ordnung(self):
        extra = '[[package]]\nname = "eigen"\nversion = "0.2.0"\nsource = { editable = "eigen" }\n'
        self.assertEqual(P.abhaengigkeiten([self.uv(extra)]).zeile, "ok")

    def test_unveraenderte_lockfile_hat_keine_neuen_pakete(self):
        befund = P.abhaengigkeiten([Datei("uv.lock", b(self.UV_ALT), b(self.UV_ALT))])
        self.assertEqual((befund.neue_pakete, befund.treffer_anzahl), ([], 0))

    def test_npm_lockfile(self):
        def lock(pakete):
            return b(json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "ui"}, **pakete}}))

        alt = lock({"node_modules/react": {"version": "19.0.0", "resolved": "https://registry.npmjs.org/react/-/react-19.0.0.tgz"}})
        neu_ = lock({"node_modules/react": {"version": "19.0.0", "resolved": "https://registry.npmjs.org/react/-/react-19.0.0.tgz"},
                     "node_modules/gut": {"version": "1.0.0", "resolved": "https://registry.npmjs.org/gut/-/gut-1.0.0.tgz"},
                     "node_modules/a/node_modules/boese": {"version": "2.0.0", "resolved": "https://evil.example.net/boese.tgz"}})
        befund = P.abhaengigkeiten([Datei("ui/package-lock.json", neu_, alt)])
        self.assertEqual(befund.neue_pakete, [("boese", "2.0.0"), ("gut", "1.0.0")])
        self.assertEqual([t.detail for t in befund.treffer], ["boese (evil.example.net)"])

    def test_kaputte_lockfile_ist_ein_fehler_kein_absturz(self):
        for roh in (b"kein toml [[", b"\xff\xfe"):
            self.assertEqual(P.abhaengigkeiten([Datei("uv.lock", roh)]).zeile, "fehler")
        self.assertEqual(P.abhaengigkeiten([Datei("package-lock.json", b("{kaputt"))]).zeile, "fehler")
        self.assertEqual(P.abhaengigkeiten([Datei("package-lock.json", b('{"packages": []}'))]).zeile, "fehler")

    def test_andere_dateien_werden_ignoriert(self):
        self.assertEqual(P.abhaengigkeiten([neu("requirements.txt", "evil==1.0")]).zeile, "ok")


class QuelltextTests(unittest.TestCase):
    def test_nur_standardbibliothek_und_keine_gedankenstriche(self):
        import ast
        text = (PRUEFER / "pruefer_scans.py").read_text(encoding="utf-8")
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)
        self.assertNotIn("\r", text)
        for knoten in ast.walk(ast.parse(text)):
            module = []
            if isinstance(knoten, ast.Import):
                module = [a.name.split(".")[0] for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom) and knoten.level == 0:
                module = [(knoten.module or "").split(".")[0]]
            for m in module:
                self.assertIn(m, sys.stdlib_module_names, m)

    def test_kein_aufruf_von_programmen_oder_netz(self):
        # die Muster stehen als Text in der Datei; ausgeführt wird nichts davon: diese Module werden nie importiert
        import ast
        gesamt = ast.parse((PRUEFER / "pruefer_scans.py").read_text(encoding="utf-8"))
        importe = {a.name.split(".")[0] for k in ast.walk(gesamt) if isinstance(k, ast.Import) for a in k.names}
        importe |= {(k.module or "").split(".")[0] for k in ast.walk(gesamt) if isinstance(k, ast.ImportFrom)}
        self.assertFalse(importe & {"subprocess", "os", "socket", "http", "pickle", "importlib", "ctypes", "requests"})


if __name__ == "__main__":
    unittest.main()
