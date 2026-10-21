"""Client des Kerns für den Gerätedienst wamo-geraete auf Sokrates-2 (Bürogeräte).

Verbindung: Unix-Socket /run/wamo-geraete/geraete.sock (WAMO_GERAETE_SOCKET). Nach dem Verbinden und vor dem ersten
Byte prüft der Client per SO_PEERCRED, dass am anderen Ende der Benutzer wamo-geraete lauscht (WAMO_GERAETE_BENUTZER);
sonst bricht er ab, ohne etwas zu senden. Protokoll: eine JSON-Zeile hin, eine JSON-Zeile zurück, danach genau
"anhang" Bytes (Scans, NAS-Dateien), dann schließt der Dienst.

Den Kontext (user_id, rolle, session_id, name) setzt immer der Kern aus der geprüften Identität der Sitzung, nie aus
dem Gespräch. Der Dienst nimmt ihn als Behauptung des Kerns (er vertraut nur der uid von wamo-core).

Unterwegs (Windows) oder ohne Socket gibt es keine Bürogeräte: verfuegbar() ist dann False, aufruf() wirft
GeraeteNichtVerfuegbar mit einer klaren Meldung.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import sys
from dataclasses import dataclass

SOCKET = "/run/wamo-geraete/geraete.sock"
DIENST_BENUTZER = "wamo-geraete"
MAX_KOPF = 4 * 1024 * 1024
MAX_ANHANG = 31 * 1024 * 1024
NUR_IM_BUERO = "Bürogeräte gibt es nur in der Betriebsart Büro (Kern auf Sokrates-2)."


class GeraeteNichtVerfuegbar(RuntimeError):
    """Gerätedienst nicht erreichbar oder nicht vertrauenswürdig (Text für Menschen, ohne Interna)."""


@dataclass(frozen=True)
class Kontext:
    user_id: str
    rolle: str
    session_id: str | None = None
    name: str = ""

    def als_dict(self) -> dict:
        return {"user_id": self.user_id, "rolle": self.rolle, "session_id": self.session_id, "name": self.name[:40]}


def socket_pfad() -> str:
    return (os.getenv("WAMO_GERAETE_SOCKET") or SOCKET).strip()


def verfuegbar() -> bool:
    """Gibt es den Gerätedienst hier? (nur Linux mit vorhandenem Socket; Unterwegs nie)"""
    if sys.platform == "win32" or (os.getenv("WAMO_GERAETE") or "an").strip().lower() in ("aus", "off", "0"):
        return False
    try:
        return os.path.exists(socket_pfad())
    except OSError:
        return False


def _erwartete_uid() -> int:
    import pwd  # nur Linux

    try:
        return pwd.getpwnam((os.getenv("WAMO_GERAETE_BENUTZER") or DIENST_BENUTZER).strip()).pw_uid
    except KeyError as e:
        raise GeraeteNichtVerfuegbar("Gerätedienst ist nicht eingerichtet") from e


def gegenstelle_uid(sock: socket.socket) -> int | None:
    try:
        roh = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except (OSError, AttributeError):
        return None
    return struct.unpack("3i", roh)[1]


async def _verbinden(pfad: str, timeout: float):
    return await asyncio.wait_for(asyncio.open_unix_connection(pfad, limit=MAX_KOPF + 1024), timeout)


async def aufruf(werkzeug: str, kontext: Kontext, *, geraet: str | None = None, argumente: dict | None = None,
                 timeout: float = 20.0, max_anhang: int = MAX_ANHANG, verbinden=_verbinden,
                 uid_pruefen=gegenstelle_uid, erwartete_uid=None) -> tuple[dict, bytes]:
    """Ein Aufruf. Liefert (Antwort, Anhang). Wirft GeraeteNichtVerfuegbar, nie mit Gerätedaten im Text."""
    if sys.platform == "win32" and verbinden is _verbinden:
        raise GeraeteNichtVerfuegbar(NUR_IM_BUERO)
    anfrage: dict = {"v": 1, "werkzeug": werkzeug, "argumente": argumente or {}, "kontext": kontext.als_dict()}
    if geraet is not None:
        anfrage["geraet"] = geraet
    try:
        reader, writer = await verbinden(socket_pfad(), 3.0)
    except (OSError, TimeoutError) as e:
        raise GeraeteNichtVerfuegbar("Der Gerätedienst ist gerade nicht erreichbar.") from e
    try:
        soll = erwartete_uid if erwartete_uid is not None else _erwartete_uid()
        if uid_pruefen(writer.get_extra_info("socket")) != soll:
            raise GeraeteNichtVerfuegbar("Gegenstelle ist nicht der WAMO-Gerätedienst.")
        writer.write(json.dumps(anfrage, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
        async with asyncio.timeout(timeout):
            kopf = await reader.readuntil(b"\n")
            try:
                antwort = json.loads(kopf.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as e:
                raise GeraeteNichtVerfuegbar("Antwort des Gerätedienstes ist kaputt.") from e
            if not isinstance(antwort, dict) or not isinstance(antwort.get("status"), str):
                raise GeraeteNichtVerfuegbar("Antwort des Gerätedienstes ist kaputt.")
            n = antwort.get("anhang", 0)
            if isinstance(n, bool) or not isinstance(n, int) or n < 0 or n > max_anhang:
                raise GeraeteNichtVerfuegbar("Anhang des Gerätedienstes ist zu groß.")
            anhang = await reader.readexactly(n) if n else b""
        return antwort, anhang
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError) as e:
        raise GeraeteNichtVerfuegbar("Der Gerätedienst hat nicht rechtzeitig geantwortet.") from e
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def scan_holen(user_id: str, scan_id: str, rolle: str = "mitglied") -> tuple[str, bytes]:
    """Für geteilte Scans: (Dateiname, Inhalt) eines Eintrags aus der Ablage dieser
    user_id. Wirft GeraeteNichtVerfuegbar oder LookupError (nicht vorhanden oder nicht dieser Person)."""
    antwort, daten = await aufruf("ablage_holen", Kontext(user_id, rolle), argumente={"id": scan_id}, timeout=20)
    if antwort.get("status") != "success" or not isinstance(antwort.get("eintrag"), dict):
        raise LookupError("Eintrag nicht gefunden")
    eintrag = antwort["eintrag"]
    return str(eintrag.get("name") or "datei"), daten
