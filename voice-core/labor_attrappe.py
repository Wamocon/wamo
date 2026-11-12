"""Attrappe des Test-Jira-Dienstes: bildet den Vertrag Version 1 (docs/labor-dienst-vertrag.md) im Prozess nach. NUR FÜR TESTS.

Eine ASGI-App ohne Netz (httpx.ASGITransport), mit Seed je Platz, allen Fehlercodes, Idempotenz, Ticketversionen, Durchlauf und den
Quoten aus D1. Sie dient den Tests des Kerns (Werkzeuge, Freigabe, Dienstclient), den Eval-Fällen und der Messrunde 1.

Sie läuft nie auf dem Server und nie vor Besuchern: Der Produktionscode (labor_client.py und alle labor_*-Module außer diesem) importiert
sie nicht, und der Kern lehnt eine WAMO_LABOR_URL ab, die auf ihren Namen zeigt (labor_vertrag.ATTRAPPE_HOST, Test in test_labor_client.py).
Sie prüft den Vertrag mit eigenem Code und teilt nur die Namen der Wertebereiche mit dem Kern nicht, damit ein Fehler im Kern keinen
Fehler hier verdeckt.

Die Variante verwundbar=True lässt Maskierung der Suche, Quoten und die atomare Prüfung von Durchlauf und Version weg. Sie dient nur dazu,
dass das Abnahmeskript nachweislich rot wird.

Betreuer-Funktionen des echten Dienstes (Durchlauf beenden und neu starten) gibt es hier als Python-Methoden: durchlauf_beenden,
neuer_durchlauf. Das Abnahmeskript kennt sie nicht.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import time
import unicodedata
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from labor_vertrag import ATTRAPPE_HOST

ATTRAPPE_URL = f"https://{ATTRAPPE_HOST}"

_PLAETZE = range(1, 17)
_TYPEN = ("bug", "aufgabe", "story")
_PRIOS = ("niedrig", "mittel", "hoch", "hoechste")
_STATUS = ("zu_erledigen", "in_arbeit", "erledigt")
_NUMMER = re.compile(r"LAB-[1-9][0-9]{0,3}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_PLATZ = re.compile(r"[1-9][0-9]?")
_VERBOTEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_ANFRAGE_MAX = 16 * 1024
_KOPF_MAX = 16 * 1024  # D5: Kopfgröße begrenzt (Summe aus Name, Wert und vier Byte je Kopfzeile), darüber 431
_LISTE_MAX = 60 * 1024  # V2: wüchse die Antwort für 10 Tickets über 60 KB, liefert der Dienst weniger Tickets (den Anfang der Liste)
_QUOTE_TICKETS, _QUOTE_KOMMENTARE, _QUOTE_BYTES, _QUOTE_RATE = 100, 300, 1024 * 1024, 30
_IDEM_SECS = 24 * 3600
_FENSTER_SECS = 60.0
_ABFRAGE = frozenset({"q", "nummer", "typ", "prioritaet", "status", "nur_durchlauf", "limit"})

_SEED = (
    (11, "bug", "niedrig", "erledigt", "Tippfehler auf der Startseite", "Im Willkommenstext fehlt ein Buchstabe."),
    (12, "bug", "hoch", "in_arbeit", "Login schlägt bei falschem Passwort fehl", "Statt einer Meldung bleibt die Seite leer."),
    (14, "aufgabe", "mittel", "zu_erledigen", "Testdaten für den Regressionslauf anlegen", ""),
    (17, "aufgabe", "niedrig", "zu_erledigen", "Dokumentation der Rollen prüfen", "Seite Rollen und Rechte lesen."),
    (20, "aufgabe", "niedrig", "zu_erledigen", "Hilfetext auf der Startseite ergänzen", ""),
    (25, "story", "mittel", "zu_erledigen", "Export der Ergebnisse als Tabelle", "Als Anwender will ich Ergebnisse exportieren."),
)


def mit_client(platz: int = 3, **kwargs):
    """Testhilfe: eine Attrappe und ein Dienstclient für diesen Platz, im Prozess verbunden (ohne Netz). kwargs gehen an Attrappe()."""
    import httpx

    from labor_client import LaborClient

    app = Attrappe(**kwargs)
    return app, LaborClient(ATTRAPPE_URL, app.token, platz, transport=httpx.ASGITransport(app=app))


class _Fehler(Exception):
    def __init__(self, status: int, code: str, text: str, *, allow: str | None = None):
        super().__init__(code)
        self.status, self.code, self.text, self.allow = status, code, text, allow


def _text_ok(wert: object, feld: str, *, zeilen: bool, lo: int, hi: int) -> str:
    """Strenge Prüfung ohne Anpassung: Text, NFC, keine verbotenen Zeichen, Länge in Codepunkten."""
    if not isinstance(wert, str) or not unicodedata.is_normalized("NFC", wert):
        raise _Fehler(422, "eingabe_ungueltig", f"Das Feld {feld} ist ungültig.")
    for ch in wert:
        if ch == "\n" and zeilen:
            continue
        if unicodedata.category(ch) in _VERBOTEN or ch == "\r":
            raise _Fehler(422, "eingabe_ungueltig", f"Das Feld {feld} enthält unzulässige Zeichen.")
    if not lo <= len(wert) <= hi:
        raise _Fehler(422, "eingabe_ungueltig", f"Das Feld {feld} hat eine falsche Länge.")
    return wert


def _aus_liste(wert: object, feld: str, erlaubt: tuple[str, ...]) -> str:
    if not isinstance(wert, str) or wert not in erlaubt:
        raise _Fehler(422, "eingabe_ungueltig", f"Das Feld {feld} hat einen unzulässigen Wert.")
    return wert


def _version(wert: object) -> int:
    if not isinstance(wert, int) or isinstance(wert, bool) or wert < 1:
        raise _Fehler(422, "eingabe_ungueltig", "Das Feld erwartete_version ist ungültig.")
    return wert


def _json(body: bytes) -> object:
    def doppelt(paare):
        werte = dict(paare)
        if len(werte) != len(paare):
            raise ValueError("doppeltes Feld")
        return werte

    def zahl(_konstante):
        raise ValueError("keine Zahl")

    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=doppelt, parse_constant=zahl)
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise _Fehler(422, "eingabe_ungueltig", "Der Inhalt ist kein gültiges JSON.") from e


def _nur_felder(obj: object, erlaubt: set[str], muss: set[str] | None = None) -> dict:
    if not isinstance(obj, dict) or not set(obj) <= erlaubt or not (muss if muss is not None else erlaubt) <= set(obj):
        raise _Fehler(422, "eingabe_ungueltig", "Die Felder der Anfrage stimmen nicht.")
    return obj


class _Platz:
    def __init__(self, uhr: Callable[[], float]):
        self.uhr = uhr
        self.durchlauf = str(uuid.uuid4())
        self.offen = True
        self.einwilligung: tuple[int, str] | None = None
        self.idem: dict[str, tuple[float, str, int, bytes]] = {}
        self.schreibzeiten: deque[float] = deque()
        self.zuruecksetzen()

    def zuruecksetzen(self) -> None:
        self.tickets: dict[int, dict] = {}
        self.zaehler = 30
        self.kommentar_zaehler = 0
        self.kommentare_gesamt = 0
        self.nutzbytes = 0
        for nr, typ, prio, status, titel, text in _SEED:
            self.tickets[nr] = {"titel": titel, "typ": typ, "prioritaet": prio, "status": status, "beschreibung": text,
                                "version": 1, "im_durchlauf": False, "kommentare": []}
            self.nutzbytes += len(titel.encode()) + len(text.encode())


def _ausgabe(nr: int, t: dict) -> dict:
    return {"nummer": f"LAB-{nr}", "titel": t["titel"], "typ": t["typ"], "prioritaet": t["prioritaet"], "status": t["status"],
            "beschreibung": t["beschreibung"], "version": t["version"], "im_durchlauf": t["im_durchlauf"]}


class Attrappe:
    """ASGI-App des Dienstes. uhr: Zeit in Sekunden (für Schreibrate und Gültigkeit der Idempotenzschlüssel, in Tests verstellbar)."""

    def __init__(self, *, token: str | None = None, uhr: Callable[[], float] = time.time, verwundbar: bool = False, grenzen: bool = True):
        """grenzen: Kürzung von V2 auf 60 KB und Grenze der Kopfgröße (431). Mit False (und bei verwundbar) lässt die Attrappe beide weg."""
        self.token = token or secrets.token_urlsafe(32)
        self._uhr = uhr
        self._verwundbar = verwundbar
        self._grenzen = grenzen and not verwundbar
        self._plaetze = {n: _Platz(uhr) for n in _PLAETZE}
        for platz in self._plaetze.values():
            platz.einwilligung = (1, self._jetzt())

    # ---------- Betreuer-Funktionen und Einsicht (nur für Tests) ----------
    def _jetzt(self) -> str:
        return datetime.fromtimestamp(self._uhr(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def durchlauf_id(self, platz: int) -> str:
        return self._plaetze[platz].durchlauf

    def durchlauf_beenden(self, platz: int) -> None:
        self._plaetze[platz].offen = False

    def neuer_durchlauf(self, platz: int, *, einwilligung_version: int | None = 1) -> str:
        """Der Nächste: neuer Durchlauf mit Einwilligung (oder ohne), Seed, frische Schlüssel."""
        p = self._plaetze[platz]
        p.durchlauf, p.offen = str(uuid.uuid4()), True
        p.einwilligung = (einwilligung_version, self._jetzt()) if einwilligung_version is not None else None
        p.idem.clear()
        p.schreibzeiten.clear()
        p.zuruecksetzen()
        return p.durchlauf

    def tickets(self, platz: int) -> list[dict]:
        return [_ausgabe(n, t) for n, t in sorted(self._plaetze[platz].tickets.items())]

    def ticket(self, platz: int, nummer: str) -> dict:
        return _ausgabe(int(nummer[4:]), self._plaetze[platz].tickets[int(nummer[4:])])

    def setze_nutzbytes(self, platz: int, wert: int) -> None:
        self._plaetze[platz].nutzbytes = wert

    # ---------- ASGI ----------
    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        extra: list[tuple[bytes, bytes]] = []
        try:
            status, obj = await self._bearbeiten(scope, receive, extra)
        except _Fehler as e:
            status, obj = e.status, {"fehler": e.code, "text": e.text}
            if e.allow:
                extra.append((b"allow", e.allow.encode()))
        except Exception:  # noqa: BLE001 (der Dienst zeigt nie Einzelheiten)
            status, obj = 500, {"fehler": "dienst_fehler", "text": "Interner Fehler."}
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"), (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"), (b"content-length", str(len(body)).encode()), *extra]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _kopfzeilen(scope) -> dict[str, list[str]]:
        kopf: dict[str, list[str]] = {}
        for name, wert in scope.get("headers", []):
            kopf.setdefault(name.decode("latin-1").lower(), []).append(wert.decode("latin-1"))
        return kopf

    @staticmethod
    def _eins(kopf: dict[str, list[str]], name: str) -> str | None:
        werte = kopf.get(name)
        return werte[0] if werte and len(werte) == 1 else None

    def _route(self, methode: str, pfad: str) -> tuple[str, str | None]:
        feste = {"/api/kern/durchlauf": ("durchlauf", ("GET",)), "/api/kern/tickets": ("tickets", ("GET", "POST"))}
        if pfad in feste:
            name, erlaubt = feste[pfad]
            nummer = None
        elif (m := re.fullmatch(r"/api/kern/tickets/([^/]+)", pfad)):
            name, erlaubt, nummer = "ticket", ("GET", "PATCH"), m.group(1)
        elif (m := re.fullmatch(r"/api/kern/tickets/([^/]+)/(status|kommentare)", pfad)):
            name, erlaubt, nummer = m.group(2), ("POST",), m.group(1)
        else:
            raise _Fehler(404, "nicht_erlaubt", "Diesen Pfad gibt es nicht.")
        if methode not in erlaubt:
            raise _Fehler(405, "nicht_erlaubt", "Diese Methode gibt es hier nicht.", allow=", ".join(erlaubt))
        return name, nummer

    async def _lesen(self, scope, receive) -> bytes:
        teile, summe = [], 0
        while True:
            nachricht = await receive()
            teile.append(nachricht.get("body", b""))
            summe += len(teile[-1])
            if summe > _ANFRAGE_MAX:
                raise _Fehler(413, "zu_gross", "Die Anfrage ist zu groß.")
            if not nachricht.get("more_body"):
                return b"".join(teile)

    async def _bearbeiten(self, scope, receive, extra) -> tuple[int, object]:
        methode, pfad = scope["method"], scope["path"]
        if self._grenzen and sum(len(n) + len(w) + 4 for n, w in scope.get("headers", [])) > _KOPF_MAX:  # 0. Kopfgröße (D5)
            raise _Fehler(431, "zu_gross", "Die Köpfe der Anfrage sind zu groß.")
        name, nummer = self._route(methode, pfad)  # 1. Pfad und Methode
        kopf = self._kopfzeilen(scope)
        auth = self._eins(kopf, "authorization") or ""
        schema, _, zeichen = auth.partition(" ")
        if schema.lower() != "bearer" or not hmac.compare_digest(zeichen.encode(), self.token.encode()):  # 2. Bearer
            raise _Fehler(401, "token_ungueltig", "Der Zugang ist nicht gültig.")
        roh_platz = self._eins(kopf, "x-wamo-platz") or ""  # 3. Platz
        if not _PLATZ.fullmatch(roh_platz) or int(roh_platz) not in self._plaetze:
            raise _Fehler(404, "platz_unbekannt", "Dieser Platz ist nicht bekannt.")
        platz = self._plaetze[int(roh_platz)]
        schreibend = methode in ("POST", "PATCH")
        body = b""
        if schreibend:  # 4. Größe
            laenge = self._eins(kopf, "content-length")
            if laenge and laenge.isascii() and laenge.isdigit() and int(laenge) > _ANFRAGE_MAX:
                raise _Fehler(413, "zu_gross", "Die Anfrage ist zu groß.")
            body = await self._lesen(scope, receive)
        if name == "durchlauf":
            if scope.get("query_string"):
                raise _Fehler(422, "eingabe_ungueltig", "Dieser Aufruf nimmt keine Abfrage.")
            return 200, self._v1(platz)
        if nummer is not None and not _NUMMER.fullmatch(nummer):  # 5. Form
            raise _Fehler(422, "eingabe_ungueltig", "Die Ticketnummer hat eine falsche Form.")
        if name == "tickets" and methode == "GET":
            return 200, self._v2(platz, scope.get("query_string", b""))
        if name == "ticket" and methode == "GET":
            return 200, self._v3(platz, int(nummer[4:]))
        return self._schreiben(platz, name, nummer, kopf, body, extra)

    def _v1(self, p: _Platz) -> dict:
        ein = p.einwilligung
        return {"durchlauf_id": p.durchlauf, "offen": p.offen, "einwilligung_version": ein[0] if ein else None,
                "einwilligung_zeit": ein[1] if ein else None}

    def _v2(self, p: _Platz, query: bytes) -> dict:
        try:
            paare = parse_qsl(query.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError as e:
            raise _Fehler(422, "eingabe_ungueltig", "Die Abfrage ist ungültig.") from e
        abfrage = dict(paare)
        if len(abfrage) != len(paare) or not set(abfrage) <= _ABFRAGE:
            raise _Fehler(422, "eingabe_ungueltig", "Die Abfrage hat unbekannte oder doppelte Parameter.")
        q = abfrage.get("q", "")
        if len(q) > 100:
            raise _Fehler(422, "eingabe_ungueltig", "Der Parameter q ist zu lang.")
        if "nummer" in abfrage and not _NUMMER.fullmatch(abfrage["nummer"]):
            raise _Fehler(422, "eingabe_ungueltig", "Der Parameter nummer ist ungültig.")
        for feld, erlaubt in (("typ", _TYPEN), ("prioritaet", _PRIOS), ("status", _STATUS)):
            if feld in abfrage:
                _aus_liste(abfrage[feld], feld, erlaubt)
        if abfrage.get("nur_durchlauf", "false") not in ("true", "false"):
            raise _Fehler(422, "eingabe_ungueltig", "Der Parameter nur_durchlauf ist ungültig.")
        limit = abfrage.get("limit", "10")
        if not (limit.isascii() and limit.isdigit() and 1 <= int(limit) <= 10):
            raise _Fehler(422, "eingabe_ungueltig", "Der Parameter limit ist ungültig.")
        if self._verwundbar and q:  # LIKE ohne Maskierung: % und _ wirken als Platzhalter
            muster = re.compile("".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in q), re.I | re.S)
            passt = lambda t: muster.search(t["titel"] + "\n" + t["beschreibung"])  # noqa: E731
        elif q:
            passt = lambda t: q.casefold() in (t["titel"] + "\n" + t["beschreibung"]).casefold()  # noqa: E731
        else:
            passt = lambda t: True  # noqa: E731
        treffer = [(n, t) for n, t in sorted(p.tickets.items(), reverse=True)
                   if passt(t) and (not abfrage.get("nummer") or abfrage["nummer"] == f"LAB-{n}")
                   and all(abfrage.get(f) in (None, t[f]) for f in ("typ", "prioritaet", "status"))
                   and (abfrage.get("nur_durchlauf") != "true" or t["im_durchlauf"])]
        tickets = [_ausgabe(n, t) for n, t in treffer[:int(limit)]]
        while self._grenzen and tickets and len(json.dumps({"tickets": tickets, "gesamt": len(treffer)}, ensure_ascii=False).encode("utf-8")) > _LISTE_MAX:
            tickets.pop()  # weniger Tickets, den Anfang der Liste; gesamt bleibt die echte Zahl
        return {"tickets": tickets, "gesamt": len(treffer)}

    def _v3(self, p: _Platz, nr: int) -> dict:
        t = p.tickets.get(nr)
        if t is None:
            raise _Fehler(404, "ticket_unbekannt", "Dieses Ticket gibt es nicht.")
        return {**_ausgabe(nr, t), "kommentare": [dict(k) for k in t["kommentare"][-20:]]}

    # ---------- schreibende Aufrufe ----------
    def _schreiben(self, p: _Platz, name: str, nummer: str | None, kopf, body: bytes, extra) -> tuple[int, object]:
        durchlauf, schluessel = self._eins(kopf, "x-wamo-durchlauf"), self._eins(kopf, "idempotency-key")
        if "application/json" != (self._eins(kopf, "content-type") or "").split(";")[0].strip().lower():
            raise _Fehler(422, "eingabe_ungueltig", "Der Inhalt muss JSON sein.")
        daten = _json(body)
        pruefung = {"tickets": self._pruefe_anlegen, "ticket": self._pruefe_aendern, "status": self._pruefe_status,
                    "kommentare": self._pruefe_kommentar}[name]
        werte = pruefung(daten)
        if not durchlauf or not _UUID.fullmatch(durchlauf) or not schluessel or not _UUID.fullmatch(schluessel):
            raise _Fehler(422, "eingabe_ungueltig", "Die Köpfe X-WAMO-Durchlauf und Idempotency-Key fehlen oder sind ungültig.")
        jetzt = self._uhr()
        for k in [k for k, (t, *_r) in p.idem.items() if jetzt - t > _IDEM_SECS]:
            del p.idem[k]
        fingerabdruck = f"{name}|{nummer}|{json.dumps(daten, sort_keys=True, ensure_ascii=False)}"
        if (alt := p.idem.get(schluessel.lower())) is not None:  # 6. Wiederholung
            if alt[1] != fingerabdruck:
                raise _Fehler(422, "eingabe_ungueltig", "Der Idempotency-Key gehört zu einer anderen Anfrage.")
            extra.append((b"idempotent-replayed", b"true"))
            return alt[2], alt[3]
        if not self._verwundbar:  # 7. Schreibrate
            while p.schreibzeiten and jetzt - p.schreibzeiten[0] >= _FENSTER_SECS:
                p.schreibzeiten.popleft()
            if len(p.schreibzeiten) >= _QUOTE_RATE:
                raise _Fehler(429, "quote_erreicht", "Zu viele Schreibaufrufe, bitte später.")
        p.schreibzeiten.append(jetzt)
        ergebnis = self._ausfuehren(p, name, nummer, durchlauf, werte)
        if ergebnis[0] < 500 and ergebnis[0] != 429:
            p.idem[schluessel.lower()] = (jetzt, fingerabdruck, ergebnis[0], json.dumps(ergebnis[1], ensure_ascii=False).encode())
        return ergebnis

    def _ausfuehren(self, p: _Platz, name: str, nummer: str | None, durchlauf: str, werte: dict) -> tuple[int, object]:
        # Von hier bis zum Schreiben gibt es kein await: Prüfung und Schreiben sind unteilbar (D13).
        try:
            if not self._verwundbar and (durchlauf.lower() != p.durchlauf.lower() or not p.offen):  # 8. Durchlauf
                raise _Fehler(409, "durchlauf_beendet", "Der Durchlauf ist beendet.")
            if name == "tickets":
                if not self._verwundbar and len(p.tickets) >= _QUOTE_TICKETS:
                    raise _Fehler(429, "quote_erreicht", "Das Limit für Tickets ist erreicht.")
                kosten = len(werte["titel"].encode()) + len(werte["beschreibung"].encode())
                self._bytes(p, kosten)
                nr = p.zaehler
                p.zaehler += 1
                p.nutzbytes += kosten
                p.tickets[nr] = {**werte, "status": "zu_erledigen", "version": 1, "im_durchlauf": True, "kommentare": []}
                return 201, _ausgabe(nr, p.tickets[nr])
            nr = int(nummer[4:])
            t = p.tickets.get(nr)
            if t is None:  # 9. Ticket
                raise _Fehler(404, "ticket_unbekannt", "Dieses Ticket gibt es nicht.")
            if not self._verwundbar and t["version"] != werte["erwartete_version"]:  # 10. Version
                raise _Fehler(409, "ticket_veraendert", "Das Ticket hat sich geändert.")
            if name == "ticket":
                kosten = sum(len(v.encode()) - len(t[f].encode()) for f, v in werte["felder"].items() if f in ("titel", "beschreibung"))
                self._bytes(p, kosten)
                t.update(werte["felder"])
                p.nutzbytes += kosten
                t["version"] += 1
                return 200, _ausgabe(nr, t)
            if name == "status":
                t["status"] = werte["status"]
                t["version"] += 1
                return 200, _ausgabe(nr, t)
            if not self._verwundbar and p.kommentare_gesamt >= _QUOTE_KOMMENTARE:  # 11. Mengen
                raise _Fehler(429, "quote_erreicht", "Das Limit für Kommentare ist erreicht.")
            kosten = len(werte["text"].encode())
            self._bytes(p, kosten)
            p.nutzbytes += kosten
            p.kommentar_zaehler += 1
            p.kommentare_gesamt += 1
            t["kommentare"].append({"kommentar_id": p.kommentar_zaehler, "text": werte["text"]})
            t["version"] += 1
            return 201, {"kommentar_id": p.kommentar_zaehler, "ticket": _ausgabe(nr, t)}
        except _Fehler as e:
            return e.status, {"fehler": e.code, "text": e.text}

    def _bytes(self, p: _Platz, kosten: int) -> None:
        if not self._verwundbar and kosten > 0 and p.nutzbytes + kosten > _QUOTE_BYTES:
            raise _Fehler(429, "quote_erreicht", "Das Limit für Text ist erreicht.")

    @staticmethod
    def _pruefe_anlegen(daten: object) -> dict:
        d = _nur_felder(daten, {"typ", "prioritaet", "titel", "beschreibung"})
        return {"typ": _aus_liste(d["typ"], "typ", _TYPEN), "prioritaet": _aus_liste(d["prioritaet"], "prioritaet", _PRIOS),
                "titel": _text_ok(d["titel"], "titel", zeilen=False, lo=3, hi=120),
                "beschreibung": _text_ok(d["beschreibung"], "beschreibung", zeilen=True, lo=0, hi=1500)}

    @staticmethod
    def _pruefe_aendern(daten: object) -> dict:
        d = _nur_felder(daten, {"erwartete_version", "felder"})
        felder = d["felder"]
        if not isinstance(felder, dict) or not felder or not set(felder) <= {"titel", "beschreibung", "typ", "prioritaet"}:
            raise _Fehler(422, "eingabe_ungueltig", "Das Feld felder ist ungültig.")
        neu: dict[str, str] = {}
        for f, wert in felder.items():
            neu[f] = (_aus_liste(wert, f, _TYPEN) if f == "typ" else _aus_liste(wert, f, _PRIOS) if f == "prioritaet"
                      else _text_ok(wert, f, zeilen=f == "beschreibung", lo=0 if f == "beschreibung" else 3,
                                    hi=1500 if f == "beschreibung" else 120))
        return {"erwartete_version": _version(d["erwartete_version"]), "felder": neu}

    @staticmethod
    def _pruefe_status(daten: object) -> dict:
        d = _nur_felder(daten, {"erwartete_version", "status"})
        return {"erwartete_version": _version(d["erwartete_version"]), "status": _aus_liste(d["status"], "status", _STATUS)}

    @staticmethod
    def _pruefe_kommentar(daten: object) -> dict:
        d = _nur_felder(daten, {"erwartete_version", "text"})
        return {"erwartete_version": _version(d["erwartete_version"]), "text": _text_ok(d["text"], "text", zeilen=True, lo=1, hi=500)}
