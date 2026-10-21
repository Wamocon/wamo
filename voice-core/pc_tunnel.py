"""PC-Host über den SSH-Rücktunnel (Betriebsart Büro: Kern auf Sokrates-2, Werkzeuge am PC).

Zwei Betriebsweisen: Der alte Tunnel (ein Socket /run/wamo-tunnel/pc.sock, ein Token) und der Tunnel je Gerät
(WAMO_PC_REGISTER=an, pc_geraet.py): Dort gehört jeder Laptop einem Unix-Benutzer wamo-t-NN mit eigenem Socket. Der Kern
verbindet nur mit dem Socket, den das Register für das Profil nennt, und prüft NACH dem Verbinden per SO_PEERCRED, dass am
anderen Ende genau dieser Benutzer lauscht, bevor ein Byte (und damit das Token) gesendet wird (GegenstellenPruefung).
Jede Anfrage trägt X-WAMO-Geraet und X-WAMO-Profil (der Host prüft sie gegen geraet.json). Antworten der Laptops sind
auf 4 MB begrenzt, Werkzeugaufrufe je Gerät auf 120 je Minute. Nach einem 401 liest der Kern das Token einmal neu (der
Host erzeugt es bei jedem Start neu).

Der PC öffnet zu Sokrates-2 eine Rückweiterleitung auf einen Unix-Socket (scripts/wamo-tunnel.ps1):

    ssh -N -R /run/wamo-tunnel/pc.sock:127.0.0.1:8765 wamo-tunnel@192.168.178.136

Den Socket kann nur der Benutzer wamo-tunnel anlegen (Verzeichnis 2750 wamo-tunnel:wamo-core), verbinden kann nur
der Kern (Gruppe wamo-core). Anders als ein TCP-Port auf Loopback kann ihn bei einem Tunnelausfall kein anderer
lokaler Benutzer belegen und das Token abgreifen. Am anderen Ende lauscht host/wamo_host.py --http auf
127.0.0.1:8765 und verlangt das Bearer-Token (host/host_http.py).

Dieser Client spricht MCP (Streamable HTTP, zustandslos, JSON-Antworten) direkt über httpx mit uds=. Pipecats
MCPClient kann keinen Unix-Socket, und interne Pipecat-Funktionen zu ersetzen wäre bei jedem Update fragil.
Er braucht nur initialize (als Probe, prüft serverInfo "wamo-host"), tools/list und tools/call.

Ausfall: Fehlt der Socket, lehnt die Gegenstelle ab oder läuft eine Zeit ab, wirft jeder Aufruf
PcNichtErreichbar; die Werkzeug-Handler liefern dann PC_NICHT_ERREICHBAR an das Sprachmodell, und die
Überwachung (ueberwache) meldet den Wechsel an die Sitzung (bot.py: Status "getrennt", Prompt-Hinweis). Kehrt
der Tunnel zurück, meldet sie "erreichbar". Mit einem zweiten Rückruf fragt dieselbe Probe auch, ob der Bildschirm
am PC gesperrt ist (Werkzeug sperre_pruefen, nie für das Sprachmodell), und meldet den Wechsel (bot.py: "gesperrt").
"""

from __future__ import annotations

import asyncio
import json
import re
import socket as _socket
import struct
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable

import httpcore
import httpx
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

HOST_URL = "http://127.0.0.1:8765/mcp"  # Host-Header passt zur DNS-Rebinding-Prüfung von FastMCP am PC
PROTOKOLL = "2025-06-18"
SERVER_NAME = "wamo-host"
CONNECT_TIMEOUT_SECS = 3.0
CALL_TIMEOUT_SECS = 30.0
# Werkzeuge, die am PC selbst länger laufen dürfen (powershell_lesen hat dort 60 s Zeitlimit)
LANGE_WERKZEUGE = {"powershell_lesen": 75.0, "freigabe_entscheiden": 135.0}  # Freigabe: 120 s am PC
PROBE_TIMEOUT_SECS = 5.0
ERSTKONTAKT_TIMEOUT_SECS = 10.0  # tools() beim Sitzungsaufbau, wenn der Kern noch Modelle lädt
MAX_ANTWORT_BYTES = 8 * 1024 * 1024  # Bildschirmfotos als JPEG-Base64 liegen bei etwa 1 MB
MAX_ANTWORT_BYTES_GERAET = 4 * 1024 * 1024  # Laptops von Mitgliedern sind nicht vertrauenswürdig: kleinere Grenze
AUFRUFE_JE_MINUTE_GERAET = 120
OHNE_AUFRUFGRENZE = frozenset({"freigabe_entscheiden", "sperre_pruefen"})  # das Nein des Menschen zählt nie mit
UEBERWACHUNG_SECS = 10.0
UEBERWACHUNG_GETRENNT_SECS = 5.0
NACHPRUEFEN_SECS = 1.0
MAX_MODELL_ZEICHEN = 20000  # so viel Werkzeugtext bekommt das Sprachmodell höchstens
MAX_BESCHREIBUNG = 1000
MAX_PARAMETER = 20
MAX_PARAMETER_BESCHREIBUNG = 300
_PARAMETER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,40}")
_STEUER = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")

PC_NICHT_ERREICHBAR = {
    "status": "error",
    "summary": "PC nicht erreichbar. Die Verbindung zum PC ist gerade unterbrochen, dort kann WAMO nichts tun.",
}


class PcNichtErreichbar(RuntimeError):
    """Tunnel oder Host antworten nicht (Socket fehlt, abgelehnt, Zeitüberschreitung, falsches Token)."""


class PcZuvieleAufrufe(PcNichtErreichbar):
    """Mehr Werkzeugaufrufe an dieses Gerät als erlaubt (AUFRUFE_JE_MINUTE_GERAET)."""


class GegenstelleFalsch(httpcore.ConnectError):
    """Am Socket lauscht nicht der erwartete Benutzer (oder es ließ sich nicht feststellen)."""


# ---------- Gegenstelle prüfen (Tunnel je Gerät) ----------
def peer_uid(sock: object) -> int:
    """uid des Prozesses am anderen Ende eines verbundenen Unix-Sockets (SO_PEERCRED, nur Linux). Wirft OSError."""
    roh = sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_PEERCRED, struct.calcsize("3i"))  # type: ignore[attr-defined]
    return struct.unpack("3i", roh)[1]


async def pruefe_gegenstelle(stream, erwartete_uid: int, lesen: Callable[[object], int] = peer_uid) -> None:
    """Schließt den Stream und wirft ConnectError, wenn die Gegenstelle nicht genau erwartete_uid gehört oder sich
    nicht feststellen lässt. Läuft direkt nach connect(), bevor irgendein Byte gesendet wird."""
    try:
        sock = stream.get_extra_info("socket")
        uid = lesen(sock)
    except Exception as e:  # noqa: BLE001 (nicht prüfbar heißt nicht vertrauenswürdig)
        await stream.aclose()
        raise GegenstelleFalsch("Gegenstelle nicht prüfbar") from e
    if uid != erwartete_uid:
        await stream.aclose()
        raise GegenstelleFalsch("Gegenstelle gehört nicht dem erwarteten Benutzer")


class GegenstellenPruefung(httpcore.AnyIOBackend):
    """Netzwerk-Backend für httpcore: prüft bei JEDER neuen Verbindung zu einem Unix-Socket die Gegenstelle (auch die
    Verbindungen im Pool, denn der Pool verbindet nur über dieses Backend; eine bestehende Verbindung behält ihre
    gegenstelle, sie kann nicht den Besitzer wechseln)."""

    def __init__(self, erwartete_uid: int, lesen: Callable[[object], int] = peer_uid):
        super().__init__()
        self._uid = erwartete_uid
        self._lesen = lesen

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        stream = await super().connect_unix_socket(path, timeout, socket_options)
        await pruefe_gegenstelle(stream, self._uid, self._lesen)
        return stream


class _Rumpf(httpx.AsyncByteStream):
    def __init__(self, stream) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for teil in self._stream:
                yield teil
        except httpcore.TimeoutException as e:  # wie httpx: Fehler beim Lesen des Rumpfs sind httpx-Fehler
            raise httpx.ReadTimeout(str(e)) from e
        except httpcore.ProtocolError as e:
            raise httpx.RemoteProtocolError(str(e)) from e
        except httpcore.NetworkError as e:
            raise httpx.ReadError(str(e)) from e

    async def aclose(self) -> None:
        if hasattr(self._stream, "aclose"):
            await self._stream.aclose()


class UnixSocketTransport(httpx.AsyncBaseTransport):
    """httpx-Transport über einen httpcore-Pool zu genau einem Unix-Socket mit prüfendem Backend."""

    def __init__(self, socket_path: str, backend: httpcore.AsyncNetworkBackend):
        self._pool = httpcore.AsyncConnectionPool(uds=socket_path, network_backend=backend, retries=0,
                                                  max_connections=4, keepalive_expiry=30.0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        angefragt = httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host, port=request.url.port,
                             target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        )
        try:
            antwort = await self._pool.handle_async_request(angefragt)
        except httpcore.TimeoutException as e:
            raise httpx.TimeoutException(str(e), request=request) from e
        except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.UnsupportedProtocol) as e:
            raise httpx.TransportError(str(e), request=request) from e
        return httpx.Response(status_code=antwort.status, headers=antwort.headers, stream=_Rumpf(antwort.stream),
                              extensions=antwort.extensions)

    async def aclose(self) -> None:
        await self._pool.aclose()


class TunnelHost:
    """MCP-Client für den PC-Host über den Unix-Socket des Tunnels. Gleiche Schnittstelle wie Pipecats MCPClient,
    soweit bot.py und screen.py sie nutzen: tools(), close() und call_text() (über pc_host.call_host_tool)."""

    def __init__(
        self,
        socket_path: str,
        token: str,
        *,
        tools_filter: list[str],
        transport: httpx.AsyncBaseTransport | None = None,
        url: str = HOST_URL,
        erwartete_uid: int | None = None,
        geraet: str | None = None,
        profil: str | None = None,
        nachladen: Callable[[], Awaitable[str]] | None = None,
        max_antwort_bytes: int | None = None,
        aufrufe_je_minute: int | None = None,
        peer_lesen: Callable[[object], int] = peer_uid,
    ):
        if not token or not token.strip():
            raise ValueError("Host-Token fehlt")
        self._url = url
        self._filter = set(tools_filter)
        self._token = token.strip()
        self._geraet = geraet
        self._nachladen = nachladen
        self._max_antwort = max_antwort_bytes or MAX_ANTWORT_BYTES
        self._aufrufe_je_minute = aufrufe_je_minute
        self._aufrufe: deque[float] = deque()
        # Accept-Encoding identity: ein Laptop darf keine komprimierte Antwort schicken, die sich beim Entpacken über die
        # Grenze aufbläht (die Grenze gilt für die Rohbytes, siehe _lies_begrenzt)
        kopf = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                "Accept-Encoding": "identity"}
        if geraet:
            kopf["X-WAMO-Geraet"] = geraet
        if profil:
            kopf["X-WAMO-Profil"] = profil
        if transport is None and erwartete_uid is not None:
            # Tunnel je Gerät: nach dem Verbinden prüfen, wer lauscht, bevor etwas gesendet wird
            transport = UnixSocketTransport(socket_path, GegenstellenPruefung(erwartete_uid, peer_lesen))
        self._client = httpx.AsyncClient(
            transport=transport or httpx.AsyncHTTPTransport(uds=socket_path, retries=0),
            headers=kopf,
            timeout=httpx.Timeout(CALL_TIMEOUT_SECS, connect=CONNECT_TIMEOUT_SECS),
            follow_redirects=False,
            trust_env=False,  # keine Proxys aus der Umgebung
        )
        self._next_id = 0
        self._protokoll = PROTOKOLL
        self.erreichbar = False
        self._wechsel = asyncio.Event()

    # ---------- Verbindung ----------
    def _melde(self, ok: bool) -> None:
        if ok != self.erreichbar:
            self.erreichbar = ok
            self._wechsel.set()

    async def _rpc(self, method: str, params: dict, timeout: float | None = None) -> dict:
        """Ein JSON-RPC-Aufruf. Gesamtzeit höchstens timeout (auch bei tröpfelnder Antwort), Antwort höchstens
        max_antwort_bytes, beim Lesen gezählt (nie erst komplett puffern). Nach einem 401 wird das Token einmal neu
        gelesen (nachladen) und die Anfrage wiederholt."""
        self._next_id += 1
        rpc_id = self._next_id
        body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        gesamt = timeout if timeout is not None else CALL_TIMEOUT_SECS
        for versuch in (0, 1):
            try:
                async with asyncio.timeout(gesamt):
                    async with self._client.stream(
                        "POST", self._url, content=json.dumps(body),
                        headers={"MCP-Protocol-Version": self._protokoll, "Authorization": f"Bearer {self._token}"},
                        timeout=httpx.Timeout(gesamt, connect=CONNECT_TIMEOUT_SECS),
                    ) as response:
                        status = response.status_code
                        typ = response.headers.get("content-type", "")
                        if status == 200:
                            daten = await _lies_begrenzt(response, self._max_antwort)
            except (httpx.TransportError, OSError, TimeoutError) as e:  # Socket fehlt, abgelehnt, abgerissen, zu langsam
                self._melde(False)
                if isinstance(e.__cause__, GegenstelleFalsch):
                    self._warne_gegenstelle(str(e.__cause__))
                raise PcNichtErreichbar(f"Tunnel: {type(e).__name__}") from e
            if status == 401 and versuch == 0 and self._nachladen is not None:
                try:
                    self._token = (await self._nachladen()).strip()
                except Exception as e:  # noqa: BLE001 (Dateien weg oder ungültig: bleibt bei 401)
                    logger.warning("WAMO-GERAET PC-Host: Token ließ sich nicht neu lesen ({})", e)
                    break
                continue
            break
        if status in (401, 403):
            self._melde(False)
            logger.error("PC-Host lehnt die Anmeldung ab (HTTP {}): Host-Token prüfen", status)
            raise PcNichtErreichbar("Anmeldung am PC abgelehnt")
        if status != 200:
            self._melde(False)
            raise PcNichtErreichbar(f"PC-Host antwortet mit HTTP {status}")
        self._melde(True)
        return _ergebnis(typ, daten, rpc_id)

    async def pruefen(self, timeout: float = PROBE_TIMEOUT_SECS) -> int:
        """initialize als Probe. Liefert die Antwortzeit in Millisekunden; wirft PcNichtErreichbar."""
        started = time.monotonic()
        result = await self._rpc(
            "initialize",
            {"protocolVersion": PROTOKOLL, "capabilities": {}, "clientInfo": {"name": "wamo-voice-core", "version": "p3"}},
            timeout=timeout,
        )
        name = (result.get("serverInfo") or {}).get("name")
        if name != SERVER_NAME:
            self._melde(False)
            raise PcNichtErreichbar("Gegenstelle ist nicht der WAMO-Host")
        version = result.get("protocolVersion")
        if isinstance(version, str) and 0 < len(version) <= 20:
            self._protokoll = version
        return round((time.monotonic() - started) * 1000)

    # ---------- Werkzeuge ----------
    async def tools(self) -> ToolsSchema:
        """Werkzeuge des Hosts (nur die aus tools_filter), mit Handlern. Wirft PcNichtErreichbar."""
        await self.pruefen(ERSTKONTAKT_TIMEOUT_SECS)
        result = await self._rpc("tools/list", {}, timeout=ERSTKONTAKT_TIMEOUT_SECS)
        schemas = []
        for tool in result.get("tools") or []:
            name = tool.get("name") if isinstance(tool, dict) else None
            if name not in self._filter:
                continue
            schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
            properties = _eigenschaften(schema.get("properties"))
            required = [r for r in schema.get("required") or [] if isinstance(r, str) and r in properties]
            schemas.append(FunctionSchema(
                name=name, description=_text(tool.get("description"), MAX_BESCHREIBUNG),
                properties=properties, required=required, handler=self._handler,
            ))
        return ToolsSchema(standard_tools=schemas)

    async def _handler(self, params) -> None:
        try:
            text = await self.call_text(params.function_name, params.arguments or {})
        except PcZuvieleAufrufe:
            logger.warning("PC-Werkzeug {}: zu viele Aufrufe", params.function_name)
            text = json.dumps({"status": "error", "summary": "Zu viele Aufrufe am PC in kurzer Zeit. Bitte kurz warten."},
                              ensure_ascii=False)
        except PcNichtErreichbar as e:
            logger.warning("PC-Werkzeug {}: PC nicht erreichbar ({})", params.function_name, e)
            text = json.dumps(PC_NICHT_ERREICHBAR, ensure_ascii=False)
        except Exception as e:  # Antwort kaputt: das Modell bekommt trotzdem ein klares Ergebnis
            logger.warning("PC-Werkzeug {}: {}", params.function_name, type(e).__name__)
            text = json.dumps({"status": "error", "summary": "Der PC hat keine verwertbare Antwort geliefert."},
                              ensure_ascii=False)
        if len(text) > MAX_MODELL_ZEICHEN:  # Kontext und Kosten des Sprachmodells begrenzen
            text = text[:MAX_MODELL_ZEICHEN] + f" [gekürzt, {len(text)} Zeichen insgesamt]"
        await params.result_callback(text)

    async def call_text(self, name: str, arguments: dict) -> str:
        """Ruft ein Werkzeug auf (auch bildschirmfoto und maus_klicken für screen.py) und liefert den Text."""
        if name not in OHNE_AUFRUFGRENZE:
            self._aufrufe_pruefen()
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments},
                                 timeout=LANGE_WERKZEUGE.get(name, CALL_TIMEOUT_SECS))
        texts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
        text = "".join(t for t in texts if isinstance(t, str))
        return text or json.dumps({"status": "error", "summary": "Leere Antwort vom PC."}, ensure_ascii=False)

    def _warne_gegenstelle(self, grund: str) -> None:
        """Falsche Gegenstelle ins Journal (Präfix WAMO-GERAET), höchstens einmal je Minute."""
        jetzt = time.monotonic()
        if jetzt - getattr(self, "_letzte_warnung", -1e9) >= 60.0:
            self._letzte_warnung = jetzt
            logger.warning("WAMO-GERAET {} (Gerät {}): keine Anfrage gesendet", grund, self._geraet or "-")

    def _aufrufe_pruefen(self) -> None:
        """Höchstens aufrufe_je_minute Werkzeugaufrufe je Minute (nur wenn gesetzt, Tunnel je Gerät)."""
        if self._aufrufe_je_minute is None:
            return
        jetzt = time.monotonic()
        while self._aufrufe and jetzt - self._aufrufe[0] > 60.0:
            self._aufrufe.popleft()
        if len(self._aufrufe) >= self._aufrufe_je_minute:
            raise PcZuvieleAufrufe("Zu viele Aufrufe")
        self._aufrufe.append(jetzt)

    # ---------- Überwachung ----------
    async def ueberwache(
        self,
        on_change: Callable[[bool, int | None], Awaitable[None]],
        on_sperre: Callable[[bool], Awaitable[None]] | None = None,
    ) -> None:
        """Prüft regelmäßig (verbunden alle 10 s, getrennt alle 5 s, sofort nach einem gescheiterten Aufruf) und
        meldet jeden Wechsel. "Nicht erreichbar" erst nach zwei Fehlschlägen im Abstand von 1 s, damit ein
        einzelner Aussetzer den System-Prompt nicht umschaltet. Mit on_sperre fragt jede erfolgreiche Probe auch,
        ob der Bildschirm gesperrt ist, und meldet den ersten Stand und jeden Wechsel. Läuft, bis die Sitzung den
        Task abbricht."""
        gemeldet = self.erreichbar
        while True:
            warten = UEBERWACHUNG_SECS if self.erreichbar else UEBERWACHUNG_GETRENNT_SECS
            try:
                await asyncio.wait_for(self._wechsel.wait(), warten)
            except asyncio.TimeoutError:
                pass
            self._wechsel.clear()
            latenz = await self._probe()
            if latenz is None and gemeldet:
                # Hysterese: einen Aussetzer noch einmal prüfen, bevor die Sitzung umschaltet (Prompt-Cache)
                await asyncio.sleep(NACHPRUEFEN_SECS)
                latenz = await self._probe()
            if self.erreichbar != gemeldet:
                gemeldet = self.erreichbar
                logger.info("PC-Host {}", "wieder erreichbar" if gemeldet else "nicht erreichbar")
                try:
                    await on_change(gemeldet, latenz)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # ein Fehler der Sitzung darf die Überwachung nicht still beenden
                    logger.warning("PC-Überwachung: Meldung an die Sitzung fehlgeschlagen ({})", type(e).__name__)
            if on_sperre is not None and latenz is not None:
                # Jeder gültige Stand geht an die Sitzung (sie entprellt selbst): auch ein Werkzeugergebnis kann den
                # Zustand dort geändert haben, ein eigener Merker hier liefe dann auseinander
                neu = await self._sperre()
                if neu is not None:
                    try:
                        await on_sperre(neu)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # wie oben: die Überwachung läuft weiter
                        logger.warning("PC-Überwachung: Sperre nicht gemeldet ({})", type(e).__name__)

    async def _sperre(self) -> bool | None:
        """Bildschirm gesperrt? (Werkzeug sperre_pruefen des Hosts) None, wenn unbekannt; wirft nie."""
        try:
            result = await self._rpc("tools/call", {"name": "sperre_pruefen", "arguments": {}},
                                     timeout=PROBE_TIMEOUT_SECS)
            texte = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict)]
            daten = json.loads("".join(t for t in texte if isinstance(t, str)))
        except Exception:  # noqa: BLE001 (älterer Host, Aussetzer, kaputte Antwort: Stand bleibt)
            return None
        wert = daten.get("gesperrt") if isinstance(daten, dict) else None
        return wert if isinstance(wert, bool) else None

    async def _probe(self) -> int | None:
        """Antwortzeit in ms oder None (nicht erreichbar); wirft nie."""
        try:
            return await self.pruefen()
        except PcNichtErreichbar:
            return None
        except Exception as e:  # unerwartete Antwort: wie nicht erreichbar behandeln, Schleife läuft weiter
            logger.warning("PC-Überwachung: {}", type(e).__name__)
            self._melde(False)
            return None

    async def close(self) -> None:
        await self._client.aclose()


async def _lies_begrenzt(response: httpx.Response, grenze: int = MAX_ANTWORT_BYTES) -> bytes:
    """Liest den Rumpf und bricht ab, sobald er grenze überschreitet (angekündigt oder gezählt)."""
    if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
        raise RuntimeError("Antwort vom PC ist komprimiert")
    laenge = response.headers.get("content-length", "")
    if laenge.isdigit() and int(laenge) > grenze:
        raise RuntimeError("Antwort vom PC ist zu groß")
    teile: list[bytes] = []
    summe = 0
    async for stueck in response.aiter_bytes():  # Content-Encoding ist oben ausgeschlossen: kein Entpacken
        summe += len(stueck)
        if summe > grenze:
            raise RuntimeError("Antwort vom PC ist zu groß")
        teile.append(stueck)
    return b"".join(teile)


def _text(wert: object, grenze: int) -> str:
    """Text vom Host für das Sprachmodell: nur Zeichenketten, ohne Steuerzeichen, gekürzt."""
    if not isinstance(wert, str):
        return ""
    return _STEUER.sub(" ", wert)[:grenze]


def _eigenschaften(roh: object) -> dict:
    """Parameter eines Werkzeugs vom Host, bereinigt: nur einfache Typen, gekürzte Beschreibung, kleine enum-Listen.
    Beschreibungen kommen vom PC; mehr als nötig soll davon nicht ins Sprachmodell gelangen."""
    if not isinstance(roh, dict):
        return {}
    sauber: dict = {}
    for name, spec in list(roh.items())[:MAX_PARAMETER]:
        if not isinstance(name, str) or not _PARAMETER_RE.fullmatch(name) or not isinstance(spec, dict):
            continue
        typ = spec.get("type")
        if typ not in ("string", "integer", "number", "boolean"):
            continue
        eintrag: dict = {"type": typ}
        if beschreibung := _text(spec.get("description"), MAX_PARAMETER_BESCHREIBUNG):
            eintrag["description"] = beschreibung
        if isinstance(spec.get("enum"), list):
            eintrag["enum"] = [e[:60] if isinstance(e, str) else e for e in spec["enum"][:20]
                               if isinstance(e, (str, int, float, bool))]
        if "default" in spec and isinstance(spec["default"], (str, int, float, bool)):
            standard = spec["default"]
            eintrag["default"] = standard[:100] if isinstance(standard, str) else standard
        sauber[name] = eintrag
    return sauber


def _ergebnis(typ: str, daten: bytes, rpc_id: int) -> dict:
    """result einer JSON-RPC-Antwort (JSON oder notfalls SSE). Fehler des Servers werden RuntimeError."""
    if typ.startswith("text/event-stream"):
        nachricht = None
        for zeile in daten.decode("utf-8", "replace").splitlines():
            if zeile.startswith("data:"):
                try:
                    kandidat = json.loads(zeile[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(kandidat, dict) and kandidat.get("id") == rpc_id:
                    nachricht = kandidat
    else:
        try:
            nachricht = json.loads(daten)
        except ValueError as e:
            raise RuntimeError("Antwort vom PC ist kein JSON") from e
    if not isinstance(nachricht, dict) or nachricht.get("id") != rpc_id:
        raise RuntimeError("Antwort vom PC passt nicht zur Anfrage")
    if "error" in nachricht:
        fehler = nachricht.get("error") or {}
        code = fehler.get("code") if isinstance(fehler, dict) else None
        raise RuntimeError(f"PC-Host meldet Fehler {code if isinstance(code, int) and not isinstance(code, bool) else 'unbekannt'}")
    result = nachricht.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Antwort vom PC ohne Ergebnis")
    return result
