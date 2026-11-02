"""Tests des Erlaubnisliste-Proxys der Prüfer-Sandbox (infra/sokrates2/release/pruefer/pruefer_proxy.py, Schritt 1.2b, Konzept D.3).

Laufen überall ohne Netz: Auflösung und Verbindung sind ersetzt, der "Server draußen" ist ein Echo-Server auf Loopback. Geprüft wird vor
allem, dass nichts nach draußen geht, was nicht ausdrücklich erlaubt ist (kein Aufruf von Auflösung oder Verbindung bei Abweisungen).

Ausführen:  uv run python -m pytest -q test_pruefer_proxy.py   (in voice-core/)
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

PRUEFER = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "release" / "pruefer"
sys.path.insert(0, str(PRUEFER))

import pruefer_proxy as X  # noqa: E402

OEFFENTLICH = "93.184.216.34"
ERLAUBT = frozenset({"pypi.org", "registry.npmjs.org"})


class Welt:
    """Proxy, Echo-Server als Gegenstelle und Zähler für alles, was nach draußen ginge."""

    def __init__(self, erlaubt=ERLAUBT, adressen=(OEFFENTLICH,), aufloesen_fehler=False, verbinden_fehler=()):
        self.log: list[str] = []
        self.aufgeloest: list[str] = []
        self.verbunden: list[str] = []
        self.adressen, self.aufloesen_fehler, self.verbinden_fehler = list(adressen), aufloesen_fehler, set(verbinden_fehler)
        self.erlaubt = erlaubt

    async def __aenter__(self):
        self.echo = await asyncio.start_server(self._echo, "127.0.0.1", 0)
        self.echo_port = self.echo.sockets[0].getsockname()[1]
        self.proxy = X.Proxy("pakete", self.erlaubt, self._aufloesen, self._verbinden, protokoll=self.log.append)
        self.server = await X.serve(self.proxy, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        for s in (self.server, self.echo):
            s.close()
            await s.wait_closed()

    @staticmethod
    async def _echo(r, w):
        try:
            while d := await r.read(65536):
                w.write(d)
                await w.drain()
        except ConnectionError:
            pass
        finally:
            w.close()

    async def _aufloesen(self, name, port):
        self.aufgeloest.append(name)
        if self.aufloesen_fehler:
            raise OSError("keine Auflösung")
        return list(self.adressen)

    async def _verbinden(self, adresse, port):
        self.verbunden.append(adresse)
        if adresse in self.verbinden_fehler:
            raise OSError("keine Verbindung")
        return await asyncio.open_connection("127.0.0.1", self.echo_port)  # statt der echten Adresse: der Echo-Server

    async def anfrage(self, roh: bytes, lesen=True):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(roh)
        await w.drain()
        zeile = await asyncio.wait_for(r.readline(), 5) if lesen else b""
        return r, w, zeile.decode("ascii", "replace").strip()

    async def erneut_connect(self):
        _r, wr, zeile = await self.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
        wr.close()
        return zeile

    def nichts_nach_draussen(self, test):
        test.assertEqual((self.aufgeloest, self.verbunden), ([], []))


def lauf(coro):
    return asyncio.run(coro)


class TunnelTests(unittest.TestCase):
    def test_erlaubter_name_wird_getunnelt(self):
        async def t():
            async with Welt() as w:
                r, wr, zeile = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\nHost: pypi.org:443\r\n\r\n")
                self.assertEqual(zeile, "HTTP/1.1 200 Connection Established")
                await r.readline()  # die leere Zeile hinter dem Kopf
                wr.write(b"hallo tunnel")
                await wr.drain()
                self.assertEqual(await asyncio.wait_for(r.readexactly(12), 5), b"hallo tunnel")
                wr.close()
                await asyncio.sleep(0.2)
                self.assertEqual((w.aufgeloest, w.verbunden), (["pypi.org"], [OEFFENTLICH]))
                self.assertTrue(any(z.startswith("proxy pakete ok pypi.org:443 ") for z in w.log), w.log)
        lauf(t())

    def test_http_1_0_wird_auch_angenommen(self):
        async def t():
            async with Welt() as w:
                _r, wr, zeile = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.0\r\n\r\n")
                self.assertTrue(zeile.endswith("200 Connection Established"))
                wr.close()
        lauf(t())

    def test_zweite_adresse_wenn_die_erste_nicht_antwortet(self):
        async def t():
            async with Welt(adressen=(OEFFENTLICH, "151.101.0.223"), verbinden_fehler=(OEFFENTLICH,)) as w:
                _r, wr, zeile = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                self.assertTrue(zeile.endswith("200 Connection Established"))
                self.assertEqual(w.verbunden, [OEFFENTLICH, "151.101.0.223"])
                wr.close()
        lauf(t())


class AbweisungsTests(unittest.TestCase):
    def abweisen(self, roh: bytes, erwartet: str, **kw):
        async def t():
            async with Welt(**kw) as w:
                _r, wr, zeile = await w.anfrage(roh)
                self.assertIn(erwartet, zeile)
                wr.close()
                w.nichts_nach_draussen(self)
        lauf(t())

    def test_nicht_erlaubte_namen(self):
        for ziel in ("evil.example:443", "github.com:443", "evil.pypi.org:443", "pypi.org.evil.example:443", "PyPI.org:443", "pypi.org.:443",
                     "*.pypi.org:443", "pypi.org@evil.example:443", "pypi.org%2eevil:443", "api.anthropic.com:443", "localhost:443"):
            with self.subTest(ziel=ziel):
                self.abweisen(f"CONNECT {ziel} HTTP/1.1\r\n\r\n".encode(), "403")

    def test_ip_adressen_als_ziel(self):
        for ziel in (f"{OEFFENTLICH}:443", "127.0.0.1:443", "[::1]:443", "10.0.0.1:443", "0x7f000001:443", "2130706433:443", "[::ffff:127.0.0.1]:443"):
            with self.subTest(ziel=ziel):
                self.abweisen(f"CONNECT {ziel} HTTP/1.1\r\n\r\n".encode(), "403")

    def test_andere_ports(self):
        for port in ("80", "22", "8443", "0", "65535", "4430", "443443", "-443", "abc"):
            with self.subTest(port=port):
                self.abweisen(f"CONNECT pypi.org:{port} HTTP/1.1\r\n\r\n".encode(), "403")
        self.abweisen(b"CONNECT pypi.org HTTP/1.1\r\n\r\n", "403")  # ohne Port

    def test_andere_methoden_und_formen(self):
        for roh in (b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\n\r\n", b"POST / HTTP/1.1\r\n\r\n", b"connect pypi.org:443 HTTP/1.1\r\n\r\n",
                    b"CONNECT pypi.org:443 HTTP/2\r\n\r\n", b"CONNECT  pypi.org:443 HTTP/1.1\r\n\r\n", b"CONNECT pypi.org:443\r\n\r\n",
                    b"CONNECT pypi.org:443 HTTP/1.1 extra\r\n\r\n", b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"):
            with self.subTest(roh=roh[:30]):
                self.abweisen(roh, "405")

    def test_kein_ascii_in_der_anfragezeile(self):
        self.abweisen("CONNECT pypiä.org:443 HTTP/1.1\r\n\r\n".encode("utf-8"), "400")

    def test_innere_adressen_werden_nach_der_aufloesung_abgewiesen(self):
        for adresse in ("127.0.0.1", "10.1.2.3", "192.168.178.136", "169.254.1.1", "172.17.0.2", "172.23.0.5", "0.0.0.0", "100.64.0.1",
                        "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:192.168.1.1", "224.0.0.1", "255.255.255.255", "192.0.2.1"):
            with self.subTest(adresse=adresse):
                async def t(adresse=adresse):
                    async with Welt(adressen=(adresse,)) as w:
                        _r, wr, zeile = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                        self.assertIn("403", zeile)
                        self.assertEqual(w.verbunden, [])  # aufgelöst ja, verbunden nie
                        self.assertTrue(any("adresse_nicht_oeffentlich" in z for z in w.log), w.log)
                        wr.close()
                lauf(t())

    def test_eine_innere_adresse_unter_mehreren_genuegt_zur_abweisung(self):
        async def t():
            async with Welt(adressen=(OEFFENTLICH, "10.0.0.5")) as w:
                _r, wr, zeile = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                self.assertIn("403", zeile)
                self.assertEqual(w.verbunden, [])
                wr.close()
        lauf(t())

    def test_leere_aufloesung_und_fehler(self):
        async def leer():
            async with Welt(adressen=()) as w:
                _r, wr, zeile = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                self.assertIn("403", zeile)
                wr.close()

        async def fehler():
            async with Welt(aufloesen_fehler=True) as w:
                _r, wr, zeile = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                self.assertIn("502", zeile)
                wr.close()

        async def nicht_erreichbar():
            async with Welt(verbinden_fehler=(OEFFENTLICH,)) as w:
                _r, wr, zeile = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                self.assertIn("502", zeile)
                wr.close()
        for f in (leer, fehler, nicht_erreichbar):
            lauf(f())


class GrenzenTests(unittest.TestCase):
    def test_zu_grosser_kopf(self):
        for groesse, erlaubt in ((X.KOPF_MAX + 200, ("431", "400")), (X.KOPF_MAX + 5000, ("400",))):
            with self.subTest(groesse=groesse):
                async def t(groesse=groesse, erlaubt=erlaubt):
                    async with Welt() as w:
                        roh = b"CONNECT pypi.org:443 HTTP/1.1\r\nX: " + b"a" * groesse + b"\r\n\r\n"
                        _r, wr, zeile = await w.anfrage(roh)
                        self.assertTrue(any(c in zeile for c in erlaubt), zeile)
                        w.nichts_nach_draussen(self)
                        wr.close()
                lauf(t())

    def test_kopf_der_nie_fertig_wird(self):
        async def t():
            with mock.patch.object(X, "KOPF_ZEIT_S", 0.3):
                async with Welt() as w:
                    r, wr, _ = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\nHost: x\r\n", lesen=False)  # ohne die Leerzeile am Ende
                    zeile = (await asyncio.wait_for(r.readline(), 5)).decode().strip()
                    self.assertIn("400", zeile)
                    w.nichts_nach_draussen(self)
                    wr.close()
                    self.assertIn("200", await w.erneut_connect())  # der Proxy lebt weiter
        lauf(t())

    def test_leerlauf_beendet_den_tunnel(self):
        async def t():
            with mock.patch.object(X, "LEER_ZEIT_S", 0.3):
                async with Welt() as w:
                    r, wr, _ = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                    await r.readline()
                    self.assertEqual(await asyncio.wait_for(r.read(10), 5), b"")  # der Proxy schließt von sich aus
                    wr.close()
        lauf(t())

    def test_mengengrenze_beendet_den_tunnel(self):
        async def t():
            with mock.patch.object(X, "BYTES_MAX", 1000):
                async with Welt() as w:
                    r, wr, _ = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                    await r.readline()
                    wr.write(b"x" * 20000)
                    await wr.drain()
                    gelesen = 0
                    try:
                        while d := await asyncio.wait_for(r.read(65536), 5):
                            gelesen += len(d)
                    except ConnectionError:
                        pass
                    self.assertLess(gelesen, 20000)
                    wr.close()
        lauf(t())

    def test_parallelgrenze_gibt_503_und_der_platz_wird_wieder_frei(self):
        async def t():
            async with Welt() as w:
                w.proxy._frei = asyncio.Semaphore(1)
                _r1, wr1, zeile1 = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                self.assertIn("200", zeile1)
                _r2, wr2, zeile2 = await w.anfrage(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
                self.assertIn("503", zeile2)
                wr1.close()
                wr2.close()
                await asyncio.sleep(0.3)
                self.assertIn("200", await w.erneut_connect())
        lauf(t())

    def test_abgebrochene_verbindungen_bringen_den_proxy_nicht_zu_fall(self):
        async def t():
            async with Welt() as w:
                for roh in (b"", b"CONNECT pypi", b"\x00\x01\x02\r\n\r\n", b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n"):
                    _r, wr, _z = await w.anfrage(roh, lesen=False)
                    wr.close()
                await asyncio.sleep(0.3)
                self.assertIn("200", await w.erneut_connect())
        lauf(t())


class HilfenTests(unittest.TestCase):
    def test_adresse_oeffentlich(self):
        for ok in (OEFFENTLICH, "151.101.0.223", "2606:4700:4700::1111", "::ffff:93.184.216.34"):
            self.assertTrue(X.adresse_oeffentlich(ok), ok)
        for nein in ("127.0.0.1", "::1", "10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.0.1", "fe80::1", "fc00::1", "0.0.0.0", "::", "224.0.0.1",
                     "ff02::1", "100.64.0.1", "::ffff:10.0.0.1", "kein.ip", "", "1.2.3", "999.1.1.1"):
            self.assertFalse(X.adresse_oeffentlich(nein), nein)

    def test_erlaubnisliste_wird_geprueft(self):
        for schlecht in ("*.pypi.org", "pypi", "PyPI.org", "pypi.org.", "93.184.216.34", "::1", "a b.de", "pypi.org/x", "", "-x.de", "x-.de",
                         "x" * 64 + ".de"):
            with self.subTest(name=schlecht):
                with self.assertRaises(ValueError):
                    X.Proxy("pakete", frozenset({schlecht}))
        X.Proxy("pakete", frozenset({"registry.npmjs.org"}))

    def test_nur_loopback(self):
        async def t():
            for host in ("0.0.0.0", "192.168.178.136", "::", OEFFENTLICH):
                with self.assertRaises(ValueError):
                    await X.serve(X.Proxy("pakete", ERLAUBT), host, 0)
        lauf(t())

    def test_festgelegte_listen_des_konzepts(self):
        X.Proxy("pakete", frozenset({"registry.npmjs.org", "pypi.org", "files.pythonhosted.org", "github.com", "api.github.com"}))
        X.Proxy("claude", frozenset({"api.anthropic.com"}))

    def test_protokoll_enthaelt_nie_inhalt(self):
        async def t():
            async with Welt() as w:
                geheim = "sk-ant-" + "Q" * 40
                _r, wr, _z = await w.anfrage(f"CONNECT {geheim}.example:443 HTTP/1.1\r\nAuthorization: Bearer {geheim}\r\n\r\n".encode())
                wr.close()
                await asyncio.sleep(0.2)
                self.assertNotIn("Authorization", " ".join(w.log))
                self.assertNotIn("Q" * 10, " ".join(w.log))
                self.assertTrue(all(len(z) < 200 for z in w.log), w.log)
        lauf(t())

    def test_quelltext_ohne_gedankenstriche_und_nur_standardbibliothek(self):
        import ast
        text = (PRUEFER / "pruefer_proxy.py").read_text(encoding="utf-8")
        for zeichen in (chr(0x2014), chr(0x2013), "\r"):
            self.assertNotIn(zeichen, text)
        for k in ast.walk(ast.parse(text)):
            if isinstance(k, ast.Import):
                module = [a.name.split(".")[0] for a in k.names]
            elif isinstance(k, ast.ImportFrom) and k.level == 0:
                module = [(k.module or "").split(".")[0]]
            else:
                continue
            for m in module:
                self.assertIn(m, sys.stdlib_module_names, m)
        for verboten in ("subprocess", "os.system", "eval(", "exec(", "verify=False", "CERT_NONE"):
            self.assertNotIn(verboten, text)


if __name__ == "__main__":
    unittest.main()
