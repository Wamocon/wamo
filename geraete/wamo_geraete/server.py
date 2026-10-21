"""Unix-Socket-Server des Gerätedienstes (/run/wamo-geraete/geraete.sock).

Protokoll je Verbindung: eine Anfrage als JSON-Zeile (höchstens 512 KB, mit "\\n" beendet), eine Antwort als
JSON-Zeile, danach genau "anhang" Bytes (nur bei Dateien: Scans, NAS-Abrufe). Dann schließt der Server.

Vertrauen: Vor dem Lesen prüft der Server die Gegenstelle per SO_PEERCRED; nur die uid des Kerns (wamo-core) wird
bedient, alle anderen Verbindungen schließt er sofort. Das Verzeichnis ist 2750 wamo-geraete:wamo-core und der
Socket 0660: andere lokale Benutzer kommen schon am Dateisystem nicht weiter, SO_PEERCRED ist die zweite Schicht.

Ausgang: Beim Start prüft der Dienst, dass eine nicht erlaubte Adresse (9.9.9.9:443) NICHT erreichbar ist
(systemd IPAddressDeny=any, nft inet wamo_geraete_egress). Gelingt die Verbindung, beendet er sich (fail closed).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import stat
import struct

from .ablage import Ablage
from .audit import Audit
from .dienst import Dienst
from .grenzen import Grenzen
from .konfig import KonfigFehler, lade

logger = logging.getLogger("wamo-geraete")
SOCKET = os.getenv("WAMO_GERAETE_SOCKET") or "/run/wamo-geraete/geraete.sock"
ZUSTAND = os.getenv("WAMO_GERAETE_ZUSTAND") or "/var/lib/wamo-geraete"
KERN_BENUTZER = os.getenv("WAMO_GERAETE_KERN_BENUTZER") or "wamo-core"
MAX_ANFRAGE = 512 * 1024
GLEICHZEITIG = 16
ANHAENGE_GLEICHZEITIG = 2  # Dateien (ablage_holen) höchstens zwei zugleich: jede liegt bis 30 MB im Speicher
SENDEN_SECS = 60.0  # ein langsamer Leser hält weder Speicher noch Platz im Dienst fest
BLOCK = 256 * 1024
SELBSTTEST_ZIEL = ("9.9.9.9", 443)


def peer_uid(sock: socket.socket) -> int | None:
    try:
        roh = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except (OSError, AttributeError):
        return None
    _pid, uid, _gid = struct.unpack("3i", roh)
    return uid


async def selbsttest_ausgang(ziel: tuple[str, int] = SELBSTTEST_ZIEL) -> bool:
    """True, wenn das nicht erlaubte Ziel unerreichbar ist (so soll es sein)."""
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(*ziel), 3)
    except (OSError, TimeoutError):
        return True
    w.close()
    return False


class Server:
    def __init__(self, dienst: Dienst, kern_uid: int):
        self.dienst = dienst
        self.kern_uid = kern_uid
        self._grenze = asyncio.Semaphore(GLEICHZEITIG)
        self._anhaenge = asyncio.Semaphore(ANHAENGE_GLEICHZEITIG)

    async def verbindung(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        try:
            if sock is None or peer_uid(sock) != self.kern_uid:
                logger.warning("Verbindung von fremder uid abgewiesen")
                return
            async with self._grenze:
                try:
                    zeile = await asyncio.wait_for(reader.readuntil(b"\n"), 10)
                except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, TimeoutError):
                    await self._senden(writer, {"status": "rejected", "summary": "Anfrage zu groß oder unvollständig"})
                    return
                try:
                    anfrage = json.loads(zeile.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, RecursionError):  # RecursionError: tief verschachteltes JSON
                    await self._senden(writer, {"status": "rejected", "summary": "Anfrage ist kein JSON"})
                    return
                liefert_datei = isinstance(anfrage, dict) and anfrage.get("werkzeug") == "ablage_holen"
                async with self._anhaenge if liefert_datei else contextlib.nullcontext():
                    antwort, anhang = await self.dienst.bearbeiten(anfrage)
                    await self._senden(writer, antwort, anhang)
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    @staticmethod
    async def _senden(writer: asyncio.StreamWriter, antwort: dict, anhang: bytes = b"") -> None:
        """Kopfzeile zuerst, dann der Anhang in Blöcken; das Ganze mit Zeitgrenze (ein stehender Leser hält nichts fest)."""
        async with asyncio.timeout(SENDEN_SECS):
            writer.write(json.dumps(antwort, ensure_ascii=False).encode("utf-8") + b"\n")
            await writer.drain()
            for i in range(0, len(anhang), BLOCK):
                writer.write(anhang[i:i + BLOCK])
                await writer.drain()


def _socket_vorbereiten(pfad: str) -> None:
    try:
        info = os.lstat(pfad)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise SystemExit(f"{pfad} ist kein eigener Socket, Abbruch")
    os.unlink(pfad)


async def aufraeumen(dienst: Dienst) -> None:
    while True:
        try:
            anzahl = await asyncio.to_thread(dienst.ablage.aufraeumen)
            if anzahl:
                logger.info("Ablage: %s abgelaufene Einträge gelöscht", anzahl)
        except OSError as e:
            logger.warning("Ablage aufräumen fehlgeschlagen (%s)", type(e).__name__)
        await asyncio.sleep(1800)


async def main_async() -> None:
    try:
        konfig = lade()
    except KonfigFehler as e:
        raise SystemExit(f"Konfiguration: {e}") from e
    import pwd  # nur Linux

    try:
        kern_uid = pwd.getpwnam(KERN_BENUTZER).pw_uid
    except KeyError as e:
        raise SystemExit(f"Benutzer {KERN_BENUTZER} fehlt") from e
    if os.getenv("WAMO_GERAETE_SELBSTTEST", "an") != "aus":
        if not await selbsttest_ausgang():
            raise SystemExit("Selbsttest: nicht erlaubtes Ziel erreichbar, Ausgangsfilter fehlt. Dienst startet nicht.")
        selbsttest = "bestanden"
    else:
        selbsttest = "aus"
    dienst = Dienst(konfig, ablage=Ablage(os.path.join(ZUSTAND, "ablage"), konfig.scan_tage, konfig.abruf_stunden),
                    audit=Audit(os.path.join(ZUSTAND, "audit.jsonl")),
                    grenzen=Grenzen(os.path.join(ZUSTAND, "zaehler.json")))
    dienst.selbsttest = selbsttest
    _socket_vorbereiten(SOCKET)
    server = Server(dienst, kern_uid)
    unix = await asyncio.start_unix_server(server.verbindung, path=SOCKET, limit=MAX_ANFRAGE)
    os.chmod(SOCKET, 0o660)
    logger.info("wamo-geraete bereit: %s Geräte, Selbsttest %s", len(konfig.geraete), selbsttest)
    asyncio.create_task(aufraeumen(dienst))
    async with unix:
        await unix.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main_async())
