"""Erlaubnisliste-Proxy für die Prüfer-Sandbox (Konzept docs/selbstueberarbeitung.md D.3, Schritt 1.2b).

Die Prüfer-Sandbox hat kein eigenes Netz. Sie erreicht nur zwei Proxys auf Loopback, und jeder erlaubt genau eine feste Liste von Domains:
  * Proxy "pakete": registry.npmjs.org, pypi.org, files.pythonhosted.org, github.com, api.github.com
  * Proxy "claude": api.anthropic.com
Der Proxy kennt nur CONNECT auf Port 443 (TLS-Tunnel, er sieht den Inhalt nie). Alles andere wird abgewiesen, ohne eine Verbindung nach
draußen aufzubauen. Die Namensauflösung macht der Proxy selbst, die Sandbox braucht kein DNS:
  * Der Name muss wörtlich in der Liste stehen (kleingeschrieben, ohne Platzhalter, keine IP-Adresse, keine Endung mit Punkt).
  * Jede aufgelöste Adresse muss öffentlich sein. Zeigt ein Name auf Loopback, das LAN, Link-Local, Docker oder Ähnliches, wird abgewiesen
    (Schutz gegen DNS-Rebinding und Umleitung ins Innere). Verbunden wird mit der geprüften Adresse, nie noch einmal mit dem Namen.
Grenzen: höchstens PARALLEL_MAX Verbindungen, Kopf höchstens KOPF_MAX Bytes in KOPF_ZEIT_S Sekunden, je Verbindung höchstens
LEER_ZEIT_S Sekunden ohne Daten, GESAMT_ZEIT_S Sekunden insgesamt und BYTES_MAX Bytes je Richtung.
Im Protokoll (stderr): Zeit, Name des Proxys, Ergebnis und Zielname, Port, Bytes. Nie ein Inhalt.

Aufruf:  python3 pruefer_proxy.py --name pakete --listen 127.0.0.1:18080 --erlaubt registry.npmjs.org,pypi.org
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import re
import socket
import stat
import sys
import time
from typing import Awaitable, Callable

PARALLEL_MAX = 32
KOPF_MAX = 4096
KOPF_ZEIT_S = 10.0
LEER_ZEIT_S = 60.0
GESAMT_ZEIT_S = 1800.0
BYTES_MAX = 1024 * 1024 * 1024
CHUNK = 65536
CONNECT_ZEIT_S = 10.0
NAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
ZEILE_RE = re.compile(r"CONNECT ([^ ]{1,260}) HTTP/1\.[01]")

Aufloeser = Callable[[str, int], Awaitable[list[str]]]
Verbinder = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


def _ist_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def adresse_oeffentlich(text: str) -> bool:
    """True nur für globale Adressen: nicht privat, nicht Loopback, nicht Link-Local, nicht Multicast, nicht reserviert, nicht unspezifiziert.
    IPv4-in-IPv6 wird nach der eingebetteten IPv4-Adresse beurteilt."""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def aufloesen_echt(name: str, port: int) -> list[str]:
    schleife = asyncio.get_running_loop()
    infos = await schleife.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


async def verbinden_echt(adresse: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(adresse, port)


class Proxy:
    def __init__(self, name: str, erlaubt: frozenset[str], aufloesen: Aufloeser = aufloesen_echt, verbinden: Verbinder = verbinden_echt,
                 protokoll: Callable[[str], None] | None = None, uhr: Callable[[], float] = time.monotonic):
        for n in erlaubt:
            if not NAME_RE.fullmatch(n) or _ist_ip(n):
                raise ValueError(f"ungültiger Name in der Erlaubnisliste: {n!r}")  # nur Domainnamen, nie eine IP-Adresse, nie ein Platzhalter
        self.name, self.erlaubt, self._aufloesen, self._verbinden = name, erlaubt, aufloesen, verbinden
        self._protokoll = protokoll or (lambda z: print(z, file=sys.stderr, flush=True))
        self._uhr = uhr
        self._frei = asyncio.Semaphore(PARALLEL_MAX)

    def _log(self, ergebnis: str, ziel: str = "-", port: int | str = "-", ein: int = 0, aus: int = 0) -> None:
        # Der Name steht nur im Protokoll, wenn er in der Erlaubnisliste steht: Was die Sandbox frei wählt (Name, Port), gelangt nie ins Journal
        ziel = ziel if ziel in self.erlaubt else "-"
        port = port if isinstance(port, int) or (isinstance(port, str) and port.isdigit() and len(port) <= 5) else "?"
        self._protokoll(f"proxy {self.name} {ergebnis} {ziel}:{port} {ein}/{aus}")

    @staticmethod
    async def _antwort(w: asyncio.StreamWriter, status: str) -> None:
        try:
            w.write(f"HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
            await asyncio.wait_for(w.drain(), 5)
        except (OSError, asyncio.TimeoutError):
            pass

    async def bediene(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        if self._frei.locked():
            self._log("zu_viele")
            await self._antwort(w, "503 Service Unavailable")
            return await self._schliesse(w)
        async with self._frei:
            try:
                await self._lauf(r, w)
            except Exception:  # noqa: BLE001  nie ein Absturz des Proxys durch eine Verbindung, nie ein Text im Protokoll
                self._log("fehler")
            finally:
                await self._schliesse(w)

    @staticmethod
    async def _schliesse(w: asyncio.StreamWriter) -> None:
        try:
            w.close()
            await asyncio.wait_for(w.wait_closed(), 5)
        except Exception:  # noqa: BLE001
            pass

    async def _lauf(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            kopf = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), KOPF_ZEIT_S)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            self._log("kopf_ungueltig")
            return await self._antwort(w, "400 Bad Request")
        if len(kopf) > KOPF_MAX:
            self._log("kopf_zu_gross")
            return await self._antwort(w, "431 Request Header Fields Too Large")
        try:
            erste = kopf.split(b"\r\n", 1)[0].decode("ascii")
        except UnicodeDecodeError:
            self._log("kopf_ungueltig")
            return await self._antwort(w, "400 Bad Request")
        m = ZEILE_RE.fullmatch(erste)
        if m is None:
            self._log("methode")
            return await self._antwort(w, "405 Method Not Allowed")
        ziel, _, port_text = m[1].rpartition(":")
        if not port_text.isdigit() or int(port_text) != 443:
            self._log("port", ziel[:80], port_text[:6])
            return await self._antwort(w, "403 Forbidden")
        if ziel not in self.erlaubt:  # wörtlich: kleingeschrieben, ohne Punkt am Ende, keine IP-Adresse (die steht nie in der Liste)
            self._log("nicht_erlaubt", ziel, 443)
            return await self._antwort(w, "403 Forbidden")
        try:
            adressen = await asyncio.wait_for(self._aufloesen(ziel, 443), CONNECT_ZEIT_S)
        except Exception:  # noqa: BLE001
            self._log("aufloesung", ziel, 443)
            return await self._antwort(w, "502 Bad Gateway")
        if not adressen or not all(adresse_oeffentlich(a) for a in adressen):
            self._log("adresse_nicht_oeffentlich", ziel, 443)  # eine einzige innere Adresse genügt: der ganze Name wird abgewiesen
            return await self._antwort(w, "403 Forbidden")
        ur = uw = None
        for a in adressen:
            try:
                ur, uw = await asyncio.wait_for(self._verbinden(a, 443), CONNECT_ZEIT_S)
                break
            except Exception:  # noqa: BLE001
                continue
        if ur is None or uw is None:
            self._log("verbindung", ziel, 443)
            return await self._antwort(w, "502 Bad Gateway")
        try:
            w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await w.drain()
            ein, aus = await self._tunnel(r, w, ur, uw)
        finally:
            await self._schliesse(uw)
        self._log("ok", ziel, 443, ein, aus)

    async def _tunnel(self, r: asyncio.StreamReader, w: asyncio.StreamWriter, ur: asyncio.StreamReader, uw: asyncio.StreamWriter) -> tuple[int, int]:
        zaehler = {"hin": 0, "her": 0}
        ende = self._uhr() + GESAMT_ZEIT_S

        async def pumpe(von: asyncio.StreamReader, nach: asyncio.StreamWriter, richtung: str) -> None:
            while True:
                rest = ende - self._uhr()
                if rest <= 0 or zaehler[richtung] > BYTES_MAX:
                    return
                try:
                    daten = await asyncio.wait_for(von.read(CHUNK), min(LEER_ZEIT_S, rest))
                except (asyncio.TimeoutError, ConnectionError):
                    return
                if not daten:
                    return
                zaehler[richtung] += len(daten)
                nach.write(daten)
                await nach.drain()

        aufgaben = [asyncio.ensure_future(pumpe(r, uw, "hin")), asyncio.ensure_future(pumpe(ur, w, "her"))]
        try:
            await asyncio.wait(aufgaben, return_when=asyncio.FIRST_COMPLETED)  # eine Richtung zu: der Tunnel ist zu
        finally:
            for a in aufgaben:
                a.cancel()
            await asyncio.gather(*aufgaben, return_exceptions=True)
        return zaehler["hin"], zaehler["her"]


async def serve(proxy: Proxy, host: str, port: int) -> asyncio.AbstractServer:
    if not ipaddress.ip_address(host).is_loopback:
        raise ValueError("der Proxy lauscht nur auf Loopback")
    return await asyncio.start_server(proxy.bediene, host, port, limit=KOPF_MAX + 1024, backlog=64)


async def serve_unix(proxy: Proxy, pfad: str) -> asyncio.AbstractServer:
    """Zusätzlich zu Loopback: ein Unix-Socket für die Sandbox, die ein eigenes Netz hat (bwrap --unshare-net) und nur diese Datei sieht.
    Der Socket ist für alle Benutzer verbindbar (0666): Er gibt nur die feste Liste frei, genau wie der Loopback-Port."""
    if os.path.lexists(pfad):
        if not stat.S_ISSOCK(os.lstat(pfad).st_mode):
            raise ValueError("der Pfad des Sockets ist belegt und kein Socket")
        os.unlink(pfad)   # Rest eines früheren Laufs
    server = await asyncio.start_unix_server(proxy.bediene, path=pfad, limit=KOPF_MAX + 1024, backlog=64)
    os.chmod(pfad, 0o666)
    return server


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Erlaubnisliste-Proxy der Prüfer-Sandbox")
    ap.add_argument("--name", required=True, choices=("pakete", "claude"))
    ap.add_argument("--listen", required=True, help="host:port auf Loopback")
    ap.add_argument("--unix", help="zusätzlich: Pfad eines Unix-Sockets (für die Sandbox mit eigenem Netz)")
    ap.add_argument("--erlaubt", required=True, help="Namen, durch Komma getrennt")
    a = ap.parse_args(argv)
    host, _, port = a.listen.rpartition(":")
    proxy = Proxy(a.name, frozenset(n.strip() for n in a.erlaubt.split(",") if n.strip()))

    async def start() -> None:
        server = await serve(proxy, host, int(port))
        unix_server = await serve_unix(proxy, a.unix) if a.unix else None
        print(f"proxy {a.name} lauscht auf {a.listen}{' und ' + a.unix if a.unix else ''}, erlaubt: {sorted(proxy.erlaubt)}", file=sys.stderr, flush=True)
        async with server:
            if unix_server is not None:
                await asyncio.gather(server.serve_forever(), unix_server.serve_forever())
            else:
                await server.serve_forever()

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
