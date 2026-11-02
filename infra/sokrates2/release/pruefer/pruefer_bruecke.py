"""Brücke in der Prüfer-Sandbox (Konzept docs/selbstueberarbeitung.md D.3, Schritt 1.2c).

Die Sandbox läuft in einem eigenen Netz (bwrap --unshare-net): Sie sieht nichts vom Netz des Servers, auch nicht dessen Loopback (Datenbank,
SSH, Dienste), hat aber ein eigenes Loopback, das Werkzeuge brauchen (zum Beispiel startet Turbopack einen Node-Prozess und verbindet sich
mit ihm über 127.0.0.1). Zum Proxy führt nur eine Datei: der Unix-Socket, den die Sandbox von außen eingebunden bekommt.

Diese Brücke lauscht im eigenen Loopback der Sandbox auf 127.0.0.1:18080 (die Adresse, die HTTPS_PROXY nennt) und reicht jede Verbindung
unverändert an den Unix-Socket weiter. Sie liest und prüft nichts: Was erlaubt ist, entscheidet allein der Proxy außerhalb. Danach startet sie
den eigentlichen Befehl, gibt dessen Rückgabewert weiter und endet mit ihm.

Aufruf (in der Sandbox):  python3 -I -B pruefer_bruecke.py -- befehl [argumente ...]
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
import sys

UNIX = "/proxy/pakete.sock"
LISTEN = ("127.0.0.1", 18080)
CHUNK = 65536
VERBINDEN_S = 5.0


async def _pumpe(von: asyncio.StreamReader, nach: asyncio.StreamWriter) -> None:
    try:
        while daten := await von.read(CHUNK):
            nach.write(daten)
            await nach.drain()
    except (ConnectionError, OSError):
        pass


async def bediene(r: asyncio.StreamReader, w: asyncio.StreamWriter, unix: str = UNIX) -> None:
    uw = None
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_unix_connection(unix), VERBINDEN_S)
        aufgaben = [asyncio.ensure_future(_pumpe(r, uw)), asyncio.ensure_future(_pumpe(ur, w))]
        try:
            await asyncio.wait(aufgaben, return_when=asyncio.FIRST_COMPLETED)   # eine Richtung zu: der Tunnel ist zu
        finally:
            for a in aufgaben:
                a.cancel()
            await asyncio.gather(*aufgaben, return_exceptions=True)
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        for s in (w, uw):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


async def haupt(befehl: list[str], listen: tuple[str, int] = LISTEN, unix: str = UNIX) -> int:
    server = await asyncio.start_server(lambda r, w: bediene(r, w, unix), *listen, backlog=64)   # zuerst lauschen, dann starten
    try:
        kind = subprocess.Popen(befehl)
    except OSError:
        server.close()
        return 127
    schleife = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        schleife.add_signal_handler(sig, kind.send_signal, sig)
    code = await schleife.run_in_executor(None, kind.wait)
    server.close()
    return code if code >= 0 else 128 - code


def main(argv: list[str]) -> int:
    if "--" not in argv or argv.index("--") + 1 >= len(argv):
        print("Aufruf: pruefer_bruecke.py -- befehl [argumente ...]", file=sys.stderr)
        return 2
    return asyncio.run(haupt(argv[argv.index("--") + 1:]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
