"""HTTP-Betrieb des WAMO-Hosts (Betriebsart Büro): MCP über Streamable HTTP hinter einem Bearer-Token.

Der Kern auf Sokrates-2 erreicht den Host nur über den SSH-Rücktunnel (scripts/wamo-tunnel.ps1). Auf dem PC
lauscht der Host ausschließlich auf Loopback. Was hier erzwungen wird:

  * Bindung nur an 127.0.0.1 oder ::1, sonst startet der Host nicht.
  * Jede Anfrage braucht genau einen Header "Authorization: Bearer <Token>". Ohne gültiges Token gibt es 401,
    auch für OPTIONS, GET, unbekannte Pfade und Discovery (/.well-known/...). Der Vergleich läuft in konstanter
    Zeit (hmac.compare_digest).
  * Anfragen mit Origin-Header (also aus einem Browser) werden abgelehnt (403); der Kern schickt keinen.
  * Erst nach der Anmeldung: nur der Pfad /mcp, alles andere 404. WebSockets werden geschlossen.
  * Gerätebindung (wenn WAMO_HOST_GERAET_DATEI gesetzt ist, siehe GeraetBindung): Jede Anfrage trägt X-WAMO-Geraet
    (Kennung) und X-WAMO-Profil (Besitzer), beide müssen zu geraet.json passen, die die Desktop-App bei der Bindung
    schreibt. Sonst 403. Das ist eine zweite Schicht neben dem Token (Kern und Laptop müssen sich über Kennung und
    Besitzer einig sein), keine Grenze gegen die Person am Laptop selbst.
  * Das Token kommt aus einer Datei (WAMO_HOST_TOKEN_FILE), nie aus Befehlszeile oder Umgebung. Es hat mindestens
    43 Zeichen aus dem base64url-Alphabet (secrets.token_urlsafe(32), 256 Bit).
  * Fehlversuche landen gedrosselt im Audit-Log, nie mit dem gesendeten Wert.
  * MCP läuft zustandslos mit JSON-Antworten: jede Anfrage steht für sich, ein abgerissener Tunnel hinterlässt
    keine offenen Sitzungen.

Rollen, Risikoklassen, Freigaben und Audit der Werkzeuge bleiben unverändert in wamo_host.py.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43,512}")
MCP_PATH = "/mcp"
KENNUNG_RE = re.compile(r"pc-[a-z0-9]+(?:-[a-z0-9]+)*")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
STUFEN = ("admin", "mitglied")
GERAET_MAX_BYTES = 8192
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
FEHLVERSUCHE_JE_MINUTE = 10  # so viele Fehlversuche je Minute landen im Audit-Log, der Rest nur als Zähler

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


class TokenFehler(ValueError):
    """Token-Datei fehlt, ist unlesbar oder das Token ist zu schwach."""


def lade_token(pfad: str | None) -> str:
    """Liest das Token aus der Datei. Wirft TokenFehler, ohne den Inhalt zu nennen."""
    if not pfad or not pfad.strip():
        raise TokenFehler("WAMO_HOST_TOKEN_FILE fehlt (Datei mit dem Host-Token)")
    datei = Path(pfad.strip())
    try:
        inhalt = datei.read_text(encoding="ascii").strip()
    except FileNotFoundError as e:
        raise TokenFehler(f"Token-Datei {datei} fehlt") from e
    except (OSError, UnicodeError) as e:
        raise TokenFehler(f"Token-Datei {datei} ist nicht lesbar ({type(e).__name__})") from e
    if not TOKEN_RE.fullmatch(inhalt):
        raise TokenFehler(f"Token in {datei} ist ungültig (mindestens 43 Zeichen A-Z, a-z, 0-9, - und _)")
    return inhalt


def pruefe_bindung(host: str) -> str:
    """Nur Loopback. Wirft ValueError für alles andere (auch 0.0.0.0, :: und Namen)."""
    wert = (host or "").strip().strip("[]")
    try:
        ip = ipaddress.ip_address(wert)
    except ValueError as e:
        raise ValueError(f"WAMO_HOST_BIND muss 127.0.0.1 oder ::1 sein, nicht {host!r}") from e
    if str(ip) not in LOOPBACK_HOSTS:
        raise ValueError(f"WAMO_HOST_BIND muss 127.0.0.1 oder ::1 sein, nicht {host!r}")
    return str(ip)


class GeraetBindung:
    """Kennung, Besitzer und Stufe dieses Laptops aus geraet.json (%LOCALAPPDATA%\\WAMO, geschrieben von der Desktop-App
    nach der Bindung). Die Datei wird bei jeder Änderung neu gelesen (Bindung oder Entzug ohne Neustart des Hosts).
    Fehlt sie oder ist sie ungültig, lehnt der Host alles ab (fail closed). Die Stufe ist "mitglied", wenn sie fehlt oder
    unbekannt ist; "admin" steht nur ausdrücklich in der Datei."""

    def __init__(self, pfad: str):
        self._pfad = Path(pfad)
        self._stempel: tuple[int, int] | None = None
        self._werte: tuple[str, str, str] | None = None

    def _lesen(self) -> tuple[str, str, str] | None:
        try:
            st = self._pfad.stat()
            stempel = (st.st_mtime_ns, st.st_size)
            if stempel == self._stempel:
                return self._werte
            if st.st_size > GERAET_MAX_BYTES:
                raise ValueError("zu groß")
            daten = json.loads(self._pfad.read_text(encoding="utf-8"))
            kennung, besitzer = daten.get("kennung"), daten.get("besitzer")
            if not (isinstance(kennung, str) and KENNUNG_RE.fullmatch(kennung)
                    and isinstance(besitzer, str) and UUID_RE.fullmatch(besitzer)):
                raise ValueError("Kennung oder Besitzer ungültig")
            stufe = daten.get("stufe") if daten.get("stufe") in STUFEN else "mitglied"
            werte = (kennung, besitzer, stufe)
        except (OSError, ValueError, AttributeError, UnicodeError):
            stempel, werte = None, None
        self._stempel, self._werte = stempel, werte
        return werte

    @property
    def stufe(self) -> str:
        werte = self._lesen()
        return werte[2] if werte else "mitglied"

    def pruefen(self, headers: list[tuple[bytes, bytes]]) -> str | None:
        """None, wenn die Anfrage zu diesem Gerät passt, sonst ein kurzer Grund (ohne Werte)."""
        werte = self._lesen()
        if werte is None:
            return "geraet.json fehlt oder ist ungültig"
        kennung, besitzer, _stufe = werte
        gefunden = {}
        for name, schluessel in ((b"x-wamo-geraet", "Geraet"), (b"x-wamo-profil", "Profil")):
            liste = [v.strip() for k, v in headers if k.lower() == name]
            if len(liste) != 1:
                return f"Header X-WAMO-{schluessel} fehlt oder ist mehrdeutig"
            gefunden[schluessel] = liste[0]
        if not hmac.compare_digest(gefunden["Geraet"], kennung.encode("ascii")):
            return "Kennung passt nicht zu diesem Gerät"
        if not hmac.compare_digest(gefunden["Profil"].lower(), besitzer.encode("ascii")):
            return "Profil ist nicht der Besitzer dieses Geräts"
        return None


class TokenWaechter:
    """ASGI-Middleware vor der FastMCP-App. Lässt nur Lifespan und angemeldete Anfragen an /mcp durch."""

    def __init__(self, app, token: str, *, audit: Callable[[dict], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, geraet: GeraetBindung | None = None):
        if not TOKEN_RE.fullmatch(token or ""):
            raise TokenFehler("Token ist ungültig")
        self.app = app
        self._geraet = geraet
        self._token = token.encode("ascii")
        self._audit = audit
        self._clock = clock
        self._fenster_start = clock()
        self._fehlversuche = 0

    def _angemeldet(self, headers: list[tuple[bytes, bytes]]) -> bool:
        werte = [v for k, v in headers if k.lower() == b"authorization"]
        if len(werte) != 1:  # fehlend oder mehrdeutig
            return False
        wert = werte[0].strip()
        if wert[:7].lower() != b"bearer ":
            return False
        return hmac.compare_digest(wert[7:].strip(), self._token)

    def _vermerke_fehlversuch(self, scope: Scope, grund: str) -> None:
        jetzt = self._clock()
        if jetzt - self._fenster_start >= 60:
            if self._fehlversuche > FEHLVERSUCHE_JE_MINUTE and self._audit:
                self._audit({"tool": "http", "risk": "rejected", "grund": "weitere Fehlversuche",
                             "anzahl": self._fehlversuche - FEHLVERSUCHE_JE_MINUTE})
            self._fenster_start, self._fehlversuche = jetzt, 0
        self._fehlversuche += 1
        if self._audit and self._fehlversuche <= FEHLVERSUCHE_JE_MINUTE:
            self._audit({"tool": "http", "risk": "rejected", "grund": grund,
                         "methode": str(scope.get("method", ""))[:10], "pfad": str(scope.get("path", ""))[:80]})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        art = scope.get("type")
        if art == "lifespan":
            await self.app(scope, receive, send)
            return
        if art != "http":
            if art == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        headers = scope.get("headers") or []
        if not self._angemeldet(headers):
            self._vermerke_fehlversuch(scope, "ohne gültiges Token")
            await _antwort(send, 401, b'{"error":"nicht angemeldet"}', [(b"www-authenticate", b"Bearer")])
            return
        if any(k.lower() == b"origin" for k, _ in headers):
            self._vermerke_fehlversuch(scope, "Origin-Header")
            await _antwort(send, 403, b'{"error":"keine Browser-Anfragen"}')
            return
        if scope.get("path") != MCP_PATH:
            await _antwort(send, 404, b'{"error":"unbekannt"}')
            return
        if self._geraet is not None and (grund := self._geraet.pruefen(headers)) is not None:
            self._vermerke_fehlversuch(scope, f"Gerätebindung: {grund}")
            await _antwort(send, 403, b'{"error":"gehoert nicht zu diesem Geraet"}')
            return
        await self.app(scope, receive, send)


async def _antwort(send: Send, status: int, body: bytes, extra: list[tuple[bytes, bytes]] | None = None) -> None:
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")),
               *(extra or [])]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def baue_app(mcp, token: str, audit: Callable[[dict], None] | None = None, geraet: GeraetBindung | None = None):
    """FastMCP-App für den Tunnel: zustandslos, JSON-Antworten, hinter dem TokenWaechter. Nur einmal je Prozess
    aufrufen (der Sitzungsmanager von FastMCP lässt sich nur einmal starten)."""
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.streamable_http_path = MCP_PATH
    return TokenWaechter(mcp.streamable_http_app(), token, audit=audit, geraet=geraet)


def starte(mcp, host: str, port: int, token_datei: str | None, audit: Callable[[dict], None] | None = None,
           geraet_datei: str | None = None) -> None:
    """Startet den HTTP-Host (blockiert). Ohne gültiges Token oder mit Nicht-Loopback-Adresse: Abbruch. Mit
    geraet_datei (geraet.json) prüft der Host bei jeder Anfrage Kennung und Besitzer des Geräts."""
    import logging

    import uvicorn

    # Die Kern-Probe kommt alle 10 s: ohne Grenze schriebe das MCP-SDK je Anfrage eine INFO-Zeile ins Host-Log
    for name in ("mcp", "mcp.server", "mcp.server.lowlevel.server", "mcp.server.streamable_http"):
        logging.getLogger(name).setLevel(logging.WARNING)
    bindung = pruefe_bindung(host)
    app = baue_app(mcp, lade_token(token_datei), audit, GeraetBindung(geraet_datei) if geraet_datei else None)
    uvicorn.run(
        app,
        host=bindung,
        port=int(port),
        log_level="warning",
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_keep_alive=30,
        limit_concurrency=16,
    )
