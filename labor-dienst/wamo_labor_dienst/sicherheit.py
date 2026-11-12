"""Sicherheit: Tokens (Hash, Zufall, Vergleich in konstanter Zeit), Schutzköpfe, Fehlerantworten, Größenlimits, Protokoll je Anfrage.

Token-Regeln (D8, D9): Der Bearer des Kerns und der Betreuer-Token liegen nur als SHA-256 im Speicher des Prozesses und werden über den
Hash in fester Länge verglichen (hmac.compare_digest), damit weder Länge noch Inhalt über die Zeit verraten werden. Platz-Token haben 128 Bit
Zufall und werden nur als Hash gespeichert. Jeder Token öffnet genau eine Gruppe von Pfaden: Bearer des Kerns nur /api/kern/, Platz-Token nur
/api/browser/, Betreuer-Token nur /api/betreuer/.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from starlette.datastructures import MutableHeaders
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response

from .modell import json_bytes
from .regeln import KOPF_MAX, PLAETZE, PLATZ_RE, DienstFehler

PLATZ_TOKEN_RE = re.compile(r"[0-9a-f]{32}")
SICHERHEITSKOEPFE = {
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
}
# Antworten, die ein Angreifer ohne Zugang erzeugen kann: Das Protokoll tastet sie ab (je Minute wenige Zeilen, der Rest wird gezählt)
ABGETASTET = frozenset({400, 401, 403, 404, 405, 413, 429, 431})
_METHODEN = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"})


# ---------- Tokens ----------
def neuer_platz_token() -> str:
    """128 Bit Zufall als 32 Hexzeichen."""
    return secrets.token_hex(16)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


def digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).digest()


def gleich(gegeben: str, erwartet_digest: bytes) -> bool:
    """Vergleich in konstanter Zeit über den Hash (feste Länge)."""
    return hmac.compare_digest(digest(gegeben), erwartet_digest)


def kopf_eins(request: Request, name: str) -> str | None:
    """Der Wert eines Kopfes, der genau einmal vorkommt. Fehlt er oder kommt er mehrfach vor, ist er nicht da."""
    werte = request.headers.getlist(name)
    return werte[0] if len(werte) == 1 else None


def bearer(request: Request) -> str | None:
    """Der Token aus 'Authorization: Bearer <Token>' (genau ein Leerzeichen), sonst None."""
    auth = kopf_eins(request, "authorization")
    if auth is None:
        return None
    schema, _, zeichen = auth.partition(" ")
    return zeichen if schema.lower() == "bearer" else None


def kern_zugang(request: Request, dienst) -> int:
    """Schritte 2 und 3 des Vertrags: Bearer des Kerns (401), dann Platz aus dem Kopf des Kerns (404). Liefert den Platz."""
    token = bearer(request)
    if token is None or not gleich(token, dienst.kern_digest):
        raise DienstFehler(401, "token_ungueltig", "Der Zugang ist nicht gültig.")
    roh = kopf_eins(request, "x-wamo-platz") or ""
    if not PLATZ_RE.fullmatch(roh) or int(roh) not in PLAETZE:
        raise DienstFehler(404, "platz_unbekannt", "Dieser Platz ist nicht bekannt.")
    request.state.platz = int(roh)
    return int(roh)


def betreuer_zugang(request: Request, dienst) -> None:
    """Fehlt der Token: 401. Ist er falsch (auch ein gültiger Platz-Token oder der Bearer des Kerns): 403."""
    token = bearer(request)
    if token is None:
        raise DienstFehler(401, "token_ungueltig", "Der Zugang ist nicht gültig.")
    if not gleich(token, dienst.betreuer_digest):
        raise DienstFehler(403, "zugriff_verweigert", "Dafür fehlt die Berechtigung.")


# ---------- Antworten ----------
def json_antwort(status: int, inhalt: object, *, extra: dict[str, str] | None = None) -> Response:
    koerper = inhalt if isinstance(inhalt, bytes) else json_bytes(inhalt)
    return Response(koerper, status_code=status, headers={"Content-Type": "application/json; charset=utf-8", **SICHERHEITSKOEPFE, **(extra or {})})


def fehler_http(e: DienstFehler) -> Response:
    return json_antwort(e.status, {"fehler": e.code, "text": e.text}, extra={"Allow": e.allow} if e.allow else None)


def zu_gross() -> DienstFehler:
    return DienstFehler(413, "zu_gross", "Die Anfrage ist zu groß.")


async def koerper_lesen(request: Request, maximum: int) -> bytes:
    """Der Inhalt der Anfrage, höchstens maximum Bytes. Ein größerer Inhalt ist 413 und wird nicht gelesen, wenn Content-Length ihn verrät."""
    laenge = kopf_eins(request, "content-length")
    if laenge is not None and laenge.isascii() and laenge.isdigit() and len(laenge) <= 12 and int(laenge) > maximum:
        raise zu_gross()
    teile: list[bytes] = []
    summe = 0
    async for stueck in request.stream():
        summe += len(stueck)
        if summe > maximum:
            raise zu_gross()
        teile.append(stueck)
    return b"".join(teile)


def dienst_von(request: Request):
    return request.app.state.dienst


def fehlerfrei(funktion):
    """Fängt jeden Fehler: DienstFehler wird zur Fehlerantwort des Vertrags, alles Unerwartete zu 500 dienst_fehler ohne Einzelheiten
    (im Protokoll steht nur der Name des Fehlers)."""
    async def umhuellt(request: Request):
        try:
            return await funktion(request)
        except DienstFehler as e:
            return fehler_http(e)
        except ClientDisconnect:
            return fehler_http(DienstFehler(400, "eingabe_ungueltig", "Die Anfrage wurde abgebrochen."))
        except Exception as e:  # noqa: BLE001 (der Dienst zeigt nie Einzelheiten)
            dienst_von(request).protokoll.ereignis("fehler", fehlerart=type(e).__name__)
            return fehler_http(DienstFehler(500, "dienst_fehler", "Interner Fehler."))

    umhuellt.__name__ = funktion.__name__
    return umhuellt


# ---------- Schutzschicht ----------
def _bereich(pfad: str) -> str:
    for praefix, name in (("/api/kern/", "kern"), ("/api/browser/", "browser"), ("/api/betreuer/", "betreuer"), ("/gesundheit", "gesundheit")):
        if pfad.startswith(praefix):
            return name
    return "sonst"


class SchutzMiddleware:
    """Äußerste Schicht des Dienstes: begrenzt die Größe der Köpfe (D5), setzt die Schutzköpfe auf jede Antwort (D4) und protokolliert jede Anfrage
    nur mit Metadaten (Bereich, Methode, Status, Platz, Dauer), nie mit Pfad, Abfrage, Köpfen oder Inhalt (D8, D10)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start, status = time.perf_counter(), 0

        async def senden(nachricht):
            nonlocal status
            if nachricht["type"] == "http.response.start":
                status = nachricht["status"]
                koepfe = MutableHeaders(scope=nachricht)
                for name, wert in SICHERHEITSKOEPFE.items():
                    koepfe.setdefault(name, wert)
            await send(nachricht)

        try:
            if sum(len(n) + len(w) + 4 for n, w in scope.get("headers", [])) > KOPF_MAX:
                await fehler_http(DienstFehler(431, "zu_gross", "Die Köpfe der Anfrage sind zu groß."))(scope, receive, senden)
            else:
                await self.app(scope, receive, senden)
        finally:
            self._protokollieren(scope, status, start)

    @staticmethod
    def _protokollieren(scope, status: int, start: float) -> None:
        app = scope.get("app")
        dienst = getattr(getattr(app, "state", None), "dienst", None)
        if dienst is None:
            return
        methode = scope.get("method", "")
        schreiben = dienst.protokoll.abgetastet if status in ABGETASTET else dienst.protokoll.ereignis
        schreiben("anfrage", bereich=_bereich(scope.get("path", "")), methode=methode if methode in _METHODEN else "SONST", status=status,
                  platz=scope.get("state", {}).get("platz"), ms=round((time.perf_counter() - start) * 1000, 1))
