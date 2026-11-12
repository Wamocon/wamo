"""Nachbildung des Test-Jira-Dienstes für die Abnahme: eine saubere und mehrere absichtlich verwundbare Varianten. NUR FÜR TESTS DES ABNAHMESKRIPTS.

Das Abnahmeskript (wamo_labor_abnahme.py) gilt erst als Prüfmittel, wenn es gegen die saubere Nachbildung besteht und gegen jede verwundbare
Variante an der richtigen Anforderung durchfällt. Diese Datei liegt bewusst neben dem Skript und nicht in voice-core: Der Kern kennt weder
sie noch die Fehler. Sie läuft nie auf dem Server und nie vor Besuchern.

Die saubere Nachbildung ist die Attrappe des Kerns (voice-core/labor_attrappe.py) mit einer Vorschaltschicht, wie sie der echte Dienst
über seinen Webserver hat: Köpfe über 16 KB werden abgelehnt (D5), und eine Liste (V2), die über 60 KB wüchse, wird vom Ende her gekürzt
(Vertrag Abschnitt 3). Beides kennt die Attrappe selbst nicht.

Jeder Defekt bricht eine Anforderung (Defekt.anforderung) und folgerichtig die, die dasselbe nebenbei prüfen (Defekt.neben). Quoten und Längen bricht die Attrappe selbst, dafür werden ihre
Grenzwerte für die Dauer des Laufs verändert (mock.patch auf die privaten Namen _QUOTE_*, _ANFRAGE_MAX und _text_ok der Attrappe, danach wieder zurück;
benennt jemand sie um, scheitern die Tests dieser Datei sofort). Alles andere macht die Vorschaltschicht an der
Anfrage oder an der Antwort. Kein Defekt ändert Dateien.
"""
from __future__ import annotations

import contextlib
import json
import re
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, urlencode

import httpx

_HIER = Path(__file__).resolve().parent
for _pfad in (_HIER, _HIER.parent / "voice-core"):
    if str(_pfad) not in sys.path:
        sys.path.append(str(_pfad))

import labor_attrappe  # noqa: E402
from labor_attrappe import ATTRAPPE_URL, Attrappe  # noqa: E402
from wamo_labor_abnahme_basis import FalscherTakt  # noqa: E402

KOPF_MAX = 16 * 1024
LISTE_MAX = 60 * 1024
_TICKET_PFAD = re.compile(r"/api/kern/tickets/(LAB-[1-9][0-9]{0,3})(/status|/kommentare)?")
_PLATZ = re.compile(rb"[1-9][0-9]?")


@dataclass(frozen=True)
class Defekt:
    anforderung: str  # die Anforderung der Abnahme (A1 bis A11), die dieser Fehler bricht
    text: str
    neben: frozenset[str] = frozenset()  # weitere Anforderungen, die er folgerichtig mit bricht


DEFEKTE: dict[str, Defekt] = {
    "xss": Defekt("A1", "Antworten als text/html und ohne nosniff", neben=frozenset({"A11"})),
    "sql": Defekt("A2", "Suche und Platzkopf werden in SQL zusammengesetzt (Treffer fremder Zeilen, 5xx)"),
    "prozent": Defekt("A3", "LIKE ohne Maskierung: Prozent und Unterstrich sind Platzhalter"),
    "quote_rate": Defekt("A4", "keine Grenze der Schreibrate"),
    "quote_tickets": Defekt("A4", "keine Obergrenze für Tickets"),
    "quote_kommentare": Defekt("A4", "keine Obergrenze für Kommentare"),
    "quote_bytes": Defekt("A4", "keine Obergrenze für den Nutztext"),
    "anfragegroesse": Defekt("A5", "Anfragen über 16 KB werden gelesen", neben=frozenset({"A11"})),
    "feldlaenge": Defekt("A5", "Feldlängen nach oben nicht begrenzt"),
    "kopfgroesse": Defekt("A5", "keine Grenze für die Größe der Köpfe"),
    "platz": Defekt("A6", "der Platzkopf wird ignoriert, alle Plätze teilen ein Board"),
    "loeschweg": Defekt("A7", "DELETE (auch über X-HTTP-Method-Override) löscht ein Ticket", neben=frozenset({"A4"})),
    "durchlauf": Defekt("A8", "die Durchlaufkennung wird nicht geprüft", neben=frozenset({"A2", "A11"})),
    "version": Defekt("A8", "die erwartete Version wird nicht geprüft (kein Schutz vor gleichzeitigen Änderungen)", neben=frozenset({"A11"})),
    "idempotenz": Defekt("A8", "derselbe Idempotenzschlüssel wird nicht erkannt, es entsteht ein zweites Ticket", neben=frozenset({"A2", "A11"})),
    "bearer": Defekt("A9", "ohne oder mit falschem Bearer geht jede Anfrage durch", neben=frozenset({"A2", "A6", "A11"})),
    "status422": Defekt("A11", "Formfehler ergeben 400 statt 422", neben=frozenset({"A1", "A2", "A5", "A7", "A8"})),
    "felder": Defekt("A11", "Tickets tragen ein zusätzliches Feld"),
    "reihenfolge": Defekt("A11", "der Platz wird vor dem Bearer geprüft (Reihenfolge Abschnitt 2.2)", neben=frozenset({"A6"})),
}


class _Frueh(Exception):
    def __init__(self, status: int, obj: object):
        super().__init__(status)
        self.status, self.obj = status, obj


def _kopf(koepfe: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    return next((v for n, v in koepfe if n == name), None)


def _setzen(koepfe: list[tuple[bytes, bytes]], name: bytes, wert: bytes | None) -> list[tuple[bytes, bytes]]:
    rest = [(n, v) for n, v in koepfe if n != name]
    return rest + ([(name, wert)] if wert is not None else [])


def _platz(koepfe: list[tuple[bytes, bytes]]) -> int | None:
    roh = _kopf(koepfe, b"x-wamo-platz")
    return int(roh) if roh is not None and _PLATZ.fullmatch(roh) and 1 <= int(roh) <= 16 else None


class Nachbildung:
    """ASGI-Schicht vor der Attrappe (siehe Kopf der Datei)."""

    def __init__(self, attrappe: Attrappe, defekte: frozenset[str] = frozenset()):
        self.attrappe, self.defekte = attrappe, frozenset(defekte)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self.attrappe(scope, receive, send)
        try:
            scope, receive = await self._anfrage(scope, receive)
        except _Frueh as f:
            body = json.dumps(f.obj, ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": f.status, "headers": [
                (b"content-type", b"application/json; charset=utf-8"), (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"), (b"content-length", str(len(body)).encode())]})
            return await send({"type": "http.response.body", "body": body})
        start: dict = {}
        teile: list[bytes] = []

        async def abfangen(nachricht) -> None:
            if nachricht["type"] == "http.response.start":
                start.update(nachricht)
                return
            teile.append(nachricht.get("body", b""))
            if nachricht.get("more_body"):
                return
            status, koepfe, body = self._antwort(scope, start["status"], list(start["headers"]), b"".join(teile))
            await send({"type": "http.response.start", "status": status, "headers": koepfe})
            await send({"type": "http.response.body", "body": body})

        await self.attrappe(scope, receive, abfangen)

    # ---------- Anfrage ----------
    async def _anfrage(self, scope, receive):
        d, koepfe, query = self.defekte, list(scope["headers"]), scope.get("query_string", b"")
        methode, pfad = scope["method"], scope["path"]
        if "kopfgroesse" not in d and sum(len(n) + len(v) + 4 for n, v in koepfe) > KOPF_MAX:
            raise _Frueh(431, {"fehler": "zu_gross", "text": "Die Köpfe der Anfrage sind zu groß."})
        gueltig = _kopf(koepfe, b"authorization") == b"Bearer " + self.attrappe.token.encode()
        if "loeschweg" in d and gueltig:
            self._loeschen(koepfe, methode, pfad)
        if "bearer" in d and not gueltig:
            koepfe = _setzen(koepfe, b"authorization", b"Bearer " + self.attrappe.token.encode())
            gueltig = True
        if "reihenfolge" in d and not gueltig and _platz(koepfe) is None and pfad.startswith("/api/kern/"):
            raise _Frueh(404, {"fehler": "platz_unbekannt", "text": "Dieser Platz ist nicht bekannt."})
        if "platz" in d and _platz(koepfe) is not None:
            koepfe = _setzen(koepfe, b"x-wamo-platz", b"15")
        if "sql" in d and b"'" in (_kopf(koepfe, b"x-wamo-platz") or b""):
            raise _Frueh(500, {"fehler": "dienst_fehler", "text": "Interner Fehler."})
        query = self._abfrage(d, pfad, query)
        koepfe = self._schreibkoepfe(d, methode, koepfe)
        body = None
        if methode in ("POST", "PATCH") and ({"version", "sql"} & d):
            body = await self._lesen(receive)
            body = self._body(d, pfad, koepfe, body)
        scope = {**scope, "headers": koepfe, "query_string": query}
        if body is not None:
            scope["headers"] = _setzen(koepfe, b"content-length", str(len(body)).encode())

            async def receive() -> dict:  # noqa: F811 (der Inhalt ist gelesen und geändert)
                return {"type": "http.request", "body": body, "more_body": False}
        return scope, receive

    def _loeschen(self, koepfe, methode: str, pfad: str) -> None:
        ueberschrieben = (_kopf(koepfe, b"x-http-method-override") or b"").upper() == b"DELETE"
        treffer, platz = _TICKET_PFAD.fullmatch(pfad), _platz(koepfe)
        if (methode == "DELETE" or ueberschrieben) and treffer and not treffer.group(2) and platz is not None:
            nummer = int(treffer.group(1)[4:])
            gefunden = self.attrappe._plaetze[platz].tickets.pop(nummer, None) is not None
            raise _Frueh(200 if gefunden else 404, {"geloescht": True} if gefunden else {"fehler": "ticket_unbekannt", "text": "Nicht gefunden."})

    @staticmethod
    def _abfrage(d: frozenset[str], pfad: str, query: bytes) -> bytes:
        if pfad != "/api/kern/tickets" or not query:
            return query
        paare = parse_qsl(query.decode("utf-8", "replace"), keep_blank_values=True)
        weg: set[str] = set()
        for name, wert in paare:
            if "sql" in d and name in ("q", "nummer") and "'" in wert:
                weg.add(name)  # die Bedingung ist durch die Einspritzung immer wahr: es gilt kein Filter
            if "prozent" in d and name == "q" and ("%" in wert or "_" in wert):
                weg.add(name)  # der Platzhalter passt auf alles
        return urlencode([(n, w) for n, w in paare if n not in weg]).encode() if weg else query

    def _schreibkoepfe(self, d: frozenset[str], methode: str, koepfe):
        if methode not in ("POST", "PATCH"):
            return koepfe
        if "durchlauf" in d and (platz := _platz(koepfe)) is not None:
            koepfe = _setzen(koepfe, b"x-wamo-durchlauf", self.attrappe.durchlauf_id(platz).encode())
        if "idempotenz" in d:
            koepfe = _setzen(koepfe, b"idempotency-key", str(uuid.uuid4()).encode())
        return koepfe

    def _body(self, d: frozenset[str], pfad: str, koepfe, body: bytes) -> bytes:
        if "sql" in d and b"DROP TABLE" in body:
            raise _Frueh(500, {"fehler": "dienst_fehler", "text": "Interner Fehler."})
        treffer, platz = _TICKET_PFAD.fullmatch(pfad), _platz(koepfe)
        if "version" in d and treffer and platz is not None:
            try:
                daten = json.loads(body)
                if isinstance(daten, dict) and "erwartete_version" in daten:
                    daten["erwartete_version"] = self.attrappe.ticket(platz, treffer.group(1))["version"]
                    return json.dumps(daten, ensure_ascii=False).encode()
            except (ValueError, KeyError):
                pass
        return body

    @staticmethod
    async def _lesen(receive) -> bytes:
        teile = []
        while True:
            nachricht = await receive()
            teile.append(nachricht.get("body", b""))
            if not nachricht.get("more_body"):
                return b"".join(teile)

    # ---------- Antwort ----------
    def _antwort(self, scope, status: int, koepfe, body: bytes):
        d = self.defekte
        if scope["method"] == "GET" and scope["path"] == "/api/kern/tickets" and status == 200 and len(body) > LISTE_MAX:
            daten = json.loads(body)
            while daten["tickets"] and len(json.dumps(daten, ensure_ascii=False).encode()) > LISTE_MAX:
                daten["tickets"].pop()  # den Anfang der Liste behalten, gesamt bleibt die echte Zahl
            body = json.dumps(daten, ensure_ascii=False).encode()
        if "xss" in d:
            koepfe = _setzen(_setzen(koepfe, b"content-type", b"text/html; charset=utf-8"), b"x-content-type-options", None)
        if "status422" in d and status == 422:
            status = 400
        if "felder" in d and status in (200, 201):
            try:
                body = json.dumps(self._ergaenzt(json.loads(body)), ensure_ascii=False).encode()
            except ValueError:
                pass
        return status, _setzen(koepfe, b"content-length", str(len(body)).encode()), body

    @classmethod
    def _ergaenzt(cls, obj):
        if isinstance(obj, dict):
            neu = {k: cls._ergaenzt(v) for k, v in obj.items()}
            return {**neu, "intern": "x"} if "nummer" in obj else neu
        return [cls._ergaenzt(v) for v in obj] if isinstance(obj, list) else obj


@dataclass
class Ziel:
    """Eine laufende Nachbildung: ASGI-App, Attrappe dahinter (für Betreuer-Handgriffe in Tests), Uhr und Token (nie ausgeben)."""
    app: Nachbildung
    attrappe: Attrappe
    takt: FalscherTakt
    token: str
    url: str = ATTRAPPE_URL

    @property
    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.app)


def _patches(defekte: frozenset[str]) -> list:
    werte = {"quote_rate": ("_QUOTE_RATE", 10**9), "quote_tickets": ("_QUOTE_TICKETS", 10**9), "quote_kommentare": ("_QUOTE_KOMMENTARE", 10**9),
             "quote_bytes": ("_QUOTE_BYTES", 10**12), "anfragegroesse": ("_ANFRAGE_MAX", 10**9)}
    patches = [mock.patch.object(labor_attrappe, name, wert) for defekt, (name, wert) in werte.items() if defekt in defekte]
    if "feldlaenge" in defekte:
        echt = labor_attrappe._text_ok

        def locker(wert, feld, *, zeilen, lo, hi):
            return echt(wert, feld, zeilen=zeilen, lo=lo, hi=10**9)

        patches.append(mock.patch.object(labor_attrappe, "_text_ok", locker))
    return patches


@contextlib.contextmanager
def starten(*, defekte=()) -> Iterator[Ziel]:
    """Startet die Nachbildung im Prozess (kein Netz, keine Anschlüsse). defekte: Namen aus DEFEKTE, leer ist die saubere Variante."""
    gewaehlt = frozenset(defekte)
    if gewaehlt - set(DEFEKTE):
        raise ValueError("unbekannter Defekt: " + ", ".join(sorted(gewaehlt - set(DEFEKTE))))
    takt = FalscherTakt()
    attrappe = Attrappe(uhr=takt.jetzt)
    with contextlib.ExitStack() as stack:
        for patch in _patches(gewaehlt):
            stack.enter_context(patch)
        yield Ziel(Nachbildung(attrappe, gewaehlt), attrappe, takt, attrappe.token)
