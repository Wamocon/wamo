"""Tests der Brücke in der Prüfer-Sandbox (infra/sokrates2/release/pruefer/pruefer_bruecke.py) und des Unix-Sockets des Proxys (pruefer_proxy.serve_unix).

Die Sandbox hat ein eigenes Netz und erreicht den Proxy nur über einen Unix-Socket; die Brücke macht daraus für die Werkzeuge einen TCP-Port auf
127.0.0.1. Unix-Sockets gibt es nur unter Linux, dort laufen die Tests mit echten Sockets; sonst werden sie übersprungen.

Ausführen:  uv run python -m pytest -q test_pruefer_bruecke.py   (im Ordner voice-core/)
"""

import asyncio
import os
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path

PRUEFER = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "release" / "pruefer"
sys.path.insert(0, str(PRUEFER))

import pruefer_bruecke as B  # noqa: E402
import pruefer_proxy as X  # noqa: E402

UNIX = hasattr(socket, "AF_UNIX") and sys.platform.startswith("linux")


def freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(UNIX, "Unix-Sockets nur unter Linux")
class BrueckeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bruecke-")
        self.addCleanup(self.tmp.cleanup)
        self.sock = os.path.join(self.tmp.name, "proxy.sock")

    async def _echo_server(self):
        async def echo(r, w):
            while daten := await r.read(4096):
                w.write(b"echo:" + daten)
                await w.drain()
            w.close()
        return await asyncio.start_unix_server(echo, path=self.sock)

    def test_verbindungen_werden_durchgereicht_und_der_rueckgabewert_kommt_an(self):
        port = freier_port()

        async def lauf():
            server = await self._echo_server()
            async with server:
                async def client():
                    await asyncio.sleep(0.5)   # die Brücke lauscht, bevor sie den Befehl startet
                    r, w = await asyncio.open_connection("127.0.0.1", port)
                    w.write(b"hallo")
                    await w.drain()
                    antwort = await asyncio.wait_for(r.read(100), 5)
                    w.close()
                    return antwort
                t = asyncio.ensure_future(client())
                code = await B.haupt([sys.executable, "-c", "import time, sys; time.sleep(1.5); sys.exit(7)"], ("127.0.0.1", port), self.sock)
                return code, await t
        code, antwort = asyncio.run(lauf())
        self.assertEqual(code, 7)
        self.assertEqual(antwort, b"echo:hallo")

    def test_ohne_proxy_schliesst_die_brueke_die_verbindung_ohne_absturz(self):
        port = freier_port()

        async def lauf():
            async def client():
                await asyncio.sleep(0.5)
                r, w = await asyncio.open_connection("127.0.0.1", port)
                w.write(b"x")
                try:
                    daten = await asyncio.wait_for(r.read(100), 10)
                except ConnectionError:   # die Brücke schließt, während noch Ungelesenes ansteht: ein Reset ist genauso eine Abweisung
                    daten = b""
                w.close()
                return daten
            t = asyncio.ensure_future(client())
            code = await B.haupt([sys.executable, "-c", "import time; time.sleep(1.5)"], ("127.0.0.1", port), os.path.join(self.tmp.name, "gibt-es-nicht.sock"))
            return code, await t
        code, daten = asyncio.run(lauf())
        self.assertEqual(code, 0)
        self.assertEqual(daten, b"")

    def test_befehl_der_nicht_startet_gibt_127(self):
        self.assertEqual(asyncio.run(B.haupt(["/gibt/es/nicht"], ("127.0.0.1", freier_port()), self.sock)), 127)

    def test_ohne_befehl_wird_abgewiesen(self):
        self.assertEqual(B.main([]), 2)
        self.assertEqual(B.main(["--"]), 2)


@unittest.skipUnless(UNIX, "Unix-Sockets nur unter Linux")
class UnixSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="proxy-sock-")
        self.addCleanup(self.tmp.cleanup)
        self.pfad = os.path.join(self.tmp.name, "pakete.sock")
        self.proxy = X.Proxy("pakete", frozenset({"pypi.org"}), protokoll=lambda z: None)

    def test_socket_ist_fuer_alle_verbindbar_und_weist_fremdes_ab(self):
        async def lauf():
            server = await X.serve_unix(self.proxy, self.pfad)
            async with server:
                modus = stat.S_IMODE(os.stat(self.pfad).st_mode)
                r, w = await asyncio.open_unix_connection(self.pfad)
                w.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
                await w.drain()
                antwort = (await asyncio.wait_for(r.readline(), 5)).decode()
                w.close()
                return modus, antwort
        modus, antwort = asyncio.run(lauf())
        self.assertEqual(modus, 0o666)
        self.assertIn("403", antwort)

    def test_rest_eines_alten_sockets_wird_ersetzt(self):
        async def lauf():
            s1 = await X.serve_unix(self.proxy, self.pfad)
            s1.close()
            await s1.wait_closed()
            s2 = await X.serve_unix(self.proxy, self.pfad)   # die Datei des ersten Laufs liegt noch da
            s2.close()
            await s2.wait_closed()
        asyncio.run(lauf())

    def test_belegter_pfad_ohne_socket_wird_nicht_ueberschrieben(self):
        Path(self.pfad).write_text("wichtig")
        with self.assertRaises(ValueError):
            asyncio.run(X.serve_unix(self.proxy, self.pfad))
        self.assertEqual(Path(self.pfad).read_text(), "wichtig")


class QuelltextTests(unittest.TestCase):
    def test_nur_standardbibliothek_und_keine_gedankenstriche(self):
        text = (PRUEFER / "pruefer_bruecke.py").read_text(encoding="utf-8")
        self.assertNotIn(chr(0x2014), text)
        self.assertNotIn(chr(0x2013), text)
        for zeile in text.splitlines():
            if zeile.startswith(("import ", "from ")):
                self.assertIn(zeile.split()[1], {"asyncio", "signal", "subprocess", "sys", "__future__"}, zeile)

    def test_die_brueckenadresse_passt_zum_laeufer(self):
        import pruefer_lauf as L
        self.assertEqual(L.PROXY_PAKETE, f"http://{B.LISTEN[0]}:{B.LISTEN[1]}")


if __name__ == "__main__":
    unittest.main()
