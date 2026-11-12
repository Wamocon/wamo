"""Bausteine des Abnahmeskripts für den Test-Jira-Dienst (scripts/wamo_labor_abnahme.py): Antwort, Zugang zum Dienst, Takt, Sammler, Ergebnis.

Der Zugang spricht den Dienst mit rohem HTTP an (httpx), nie über den Dienstclient des Kerns, damit ein Fehler im Client keinen Fehler des
Dienstes verdeckt. Er kennt zwei Plätze, die für die Abnahme reserviert sind (PLATZ_A = 15, PLATZ_B = 16), und hält die Schreibrate des
Vertrags ein (höchstens 30 Schreibaufrufe je Minute und Platz, hier höchstens 25 in 62 Sekunden), damit keine Prüfung an der Rate scheitert,
die nicht die Rate prüft. Eine Zeitüberschreitung oder ein Verbindungsfehler ist eine Antwort mit Status 0 (nie eine Ausnahme); wiederholt
sich das dreimal hintereinander, bricht der Lauf ab (LaufAbbruch). Der Token steht nur im Zugang, nie in einer Meldung.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass

import httpx

BESTANDEN, DURCHGEFALLEN, VON_HAND, NICHT_GELAUFEN = "bestanden", "durchgefallen", "von Hand", "nicht gelaufen"
PLATZ_A, PLATZ_B = 15, 16
PFAD = "/api/kern"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
NUMMER_RE = re.compile(r"LAB-[1-9][0-9]{0,3}")
ZEIT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")
STANDARD = object()  # "der Bearer des Kerns" (None heißt: kein Kopf Authorization)
FEHLER_HINTEREINANDER = 3


class LaufAbbruch(Exception):
    """Der Lauf kann nicht weitergehen (Dienst weg, Ausgangsstand falsch). Die Meldung nennt nie ein Geheimnis."""


@dataclass
class Ergebnis:
    nr: str
    titel: str
    status: str
    meldung: str
    von_hand: tuple[str, ...] = ()  # was die Prüfung nicht abdeckt und von Hand bleibt


@dataclass(frozen=True)
class Optionen:
    """Angaben, ohne die ein Teil von A1, A6 und A9 nicht läuft (der Vertrag legt Board-Seite, Platz-Token und Betreuer-Pfade nicht fest)."""
    board_url: str | None = None
    platz_token: str | None = None  # Platz-Token des Abnahmeplatzes 15
    betreuer_pfade: tuple[str, ...] = ()  # "PFAD" oder "METHODE PFAD"
    platz_pfade: tuple[str, ...] = ()  # Pfad mit {platz}


class Antwort:
    def __init__(self, status: int, kopf: dict[str, str], inhalt: bytes, fehler: str = ""):
        self.status, self.kopf, self.inhalt, self.fehler = status, kopf, inhalt, fehler
        try:
            self.json = json.loads(inhalt.decode("utf-8")) if inhalt else None
        except (ValueError, UnicodeDecodeError, RecursionError):
            self.json = None

    @property
    def code(self) -> str | None:
        fehler = self.json.get("fehler") if isinstance(self.json, dict) else None
        return fehler if isinstance(fehler, str) else None

    def ist(self, status: int | tuple[int, ...], code: str | None = None) -> bool:
        erlaubt = (status,) if isinstance(status, int) else status
        return self.status in erlaubt and (code is None or self.code == code)

    def beschreibung(self) -> str:
        if self.status == 0:
            return f"keine Antwort ({self.fehler})"
        return f"{self.status}" + (f" {self.code}" if self.code else "")

    def kopf_fehler(self) -> list[str]:
        """Verstöße gegen die gemeinsamen Regeln des Vertrags für Antworten (JSON, nosniff, no-store)."""
        fehler = []
        if self.kopf.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            fehler.append("Content-Type ist nicht application/json")
        if self.kopf.get("x-content-type-options", "").lower() != "nosniff":
            fehler.append("X-Content-Type-Options: nosniff fehlt")
        if "no-store" not in self.kopf.get("cache-control", "").lower():
            fehler.append("Cache-Control: no-store fehlt")
        return fehler


# ---------- Zeit ----------
class EchterTakt:
    def jetzt(self) -> float:
        return time.monotonic()

    async def warte_bis(self, ziel: float) -> None:
        rest = ziel - self.jetzt()
        if rest > 5:
            print(f"  Warte {rest:.0f} s auf das Ende des Schreibfensters (der Dienst nimmt höchstens 30 Schreibaufrufe je Minute und Platz an).",
                  file=sys.stderr, flush=True)
        if rest > 0:
            await asyncio.sleep(rest)


class FalscherTakt:
    """Uhr für die Attrappe: Warten kostet nichts, die Zeit springt nur vorwärts (auch wenn zwei Plätze zugleich warten)."""

    def __init__(self, start: float | None = None):
        self.t = time.time() if start is None else start

    def jetzt(self) -> float:
        return self.t

    async def warte_bis(self, ziel: float) -> None:
        self.t = max(self.t, ziel)


class Schreibtakt:
    """Die Schreibaufrufe eines Platzes im laufenden Fenster. Wartet, bis ein weiterer Aufruf die Rate des Vertrags nicht berührt."""

    def __init__(self, takt, grenze: int = 25, fenster: float = 62.0):
        self.takt, self.grenze, self.fenster = takt, grenze, fenster
        self.zeiten: deque[float] = deque()

    def _altes_entfernen(self) -> None:
        jetzt = self.takt.jetzt()
        while self.zeiten and jetzt - self.zeiten[0] >= self.fenster:
            self.zeiten.popleft()

    def vermerken(self, n: int = 1) -> None:
        self.zeiten.extend([self.takt.jetzt()] * n)

    async def vor_schreiben(self, n: int = 1) -> None:
        n = min(n, self.grenze)
        while True:
            self._altes_entfernen()
            if len(self.zeiten) + n <= self.grenze:
                break
            await self.takt.warte_bis(self.zeiten[0] + self.fenster)
        self.vermerken(n)

    async def leeren(self) -> None:
        while self.zeiten:
            await self.takt.warte_bis(self.zeiten[-1] + self.fenster)
            self._altes_entfernen()


# ---------- Sammler ----------
class Sammler:
    """Sammelt die Einzelprüfungen einer Anforderung. Der erste Fehlschlag ändert nichts am Ablauf, alle Prüfungen laufen weiter."""

    def __init__(self) -> None:
        self.anzahl = 0
        self.fehler: dict[str, None] = {}  # ohne Doppelte, in der Reihenfolge des Auftretens
        self.von_hand: list[str] = []
        self.notizen: list[str] = []  # was beobachtet wurde (zum Beispiel "429 bei 100 Tickets"), steht in der Meldung einer bestandenen Prüfung
        self.proben: list[Antwort] = []  # Fehlerantworten, deren Format am Ende geprüft wird (A11)

    def notiz(self, text: str) -> None:
        self.notizen.append(text)

    def erwarte(self, bedingung: bool, text: str) -> bool:
        self.anzahl += 1
        if not bedingung:
            self.fehler.setdefault(text, None)
        return bool(bedingung)

    def status(self, a: Antwort, erwartet: int | tuple[int, ...], text: str, code: str | None = None) -> bool:
        ok = a.ist(erwartet, code)
        soll = "/".join(map(str, (erwartet,) if isinstance(erwartet, int) else erwartet)) + (f" {code}" if code else "")
        return self.erwarte(ok, f"{text}: erwartet {soll}, erhalten {a.beschreibung()}")

    def koepfe(self, a: Antwort, text: str) -> bool:
        """Jede Antwort des Dienstes trägt JSON, nosniff und no-store (Vertrag Abschnitt 2)."""
        probleme = a.kopf_fehler() if a.status else []
        return self.erwarte(not probleme, f"{text}: {', '.join(probleme)}")

    def ergebnis(self, nr: str, titel: str, gut: str = "") -> Ergebnis:
        hand = tuple(self.von_hand)
        if not self.fehler:
            rest = "; ".join(t for t in (gut, "; ".join(self.notizen)) if t)
            return Ergebnis(nr, titel, BESTANDEN, f"{self.anzahl} Einzelprüfungen" + (f", {rest}" if rest else ""), hand)
        namen = list(self.fehler)
        meldung = "; ".join(namen[:3]) + (f" (und {len(namen) - 3} weitere)" if len(namen) > 3 else "")
        return Ergebnis(nr, titel, DURCHGEFALLEN, meldung, hand)


# ---------- Zugang ----------
def verbindungsfehler(e: BaseException) -> str:
    kette, ursache = [], e
    while ursache is not None and len(kette) < 6:
        kette.append(str(ursache))
        ursache = ursache.__cause__ or ursache.__context__
    if any("CERTIFICATE_VERIFY_FAILED" in t or "certificate" in t.lower() for t in kette):
        return "Zertifikat nicht vertrauenswürdig (WAMO_CA_FILE oder --ca-datei setzen)"
    if isinstance(e, httpx.ConnectError):
        return "Verbindung nicht möglich (Adresse, Port oder Netz prüfen)"
    return f"Verbindungsfehler ({type(e).__name__})"


class Zugang:
    def __init__(self, client: httpx.AsyncClient, token: str, takt, *, frist: float | None = None, basis: str = ""):
        self.client, self._token, self.takt, self.basis = client, token, takt, basis
        self.frist_lesen, self.frist_schreiben = (frist, frist) if frist else (5.0, 8.0)  # so lange wartet der Kern
        self.durchlauf: dict[int, str] = {}
        self.kommentare: Counter[int] = Counter()  # angenommene Kommentare je Platz (für die Mengenprüfung)
        self._schreibtakt: dict[int, Schreibtakt] = {}
        self._fehlstarts = 0

    def schreibtakt(self, platz: int) -> Schreibtakt:
        return self._schreibtakt.setdefault(platz, Schreibtakt(self.takt))

    def token_mit(self, anhang: str | None) -> str:
        """Der Token des Kerns, um ein Zeichen verlängert oder (anhang None) gekürzt: für A9, nie ausgeben."""
        return self._token[:-1] if anhang is None else self._token + anhang

    # ---------- eine Anfrage ----------
    async def senden(self, methode: str, pfad: str, *, platz: int | str | None = PLATZ_A, bearer=STANDARD, kopf: dict | None = None,
                     params: dict | None = None, inhalt: bytes | None = None, daten: object = None, schreibend: bool = False,
                     abbruch_ok: bool = False) -> Antwort:
        """Eine Anfrage, so wie der Kern sie schickt (Bearer, Platz, Accept). kopf überschreibt Köpfe, None entfernt sie."""
        k: dict[str, str] = {"Accept": "application/json"}
        if bearer is STANDARD:
            k["Authorization"] = f"Bearer {self._token}"
        elif bearer is not None:
            k["Authorization"] = f"Bearer {bearer}"
        if platz is not None:
            k["X-WAMO-Platz"] = str(platz)
        if daten is not None:
            inhalt = json.dumps(daten, ensure_ascii=False).encode("utf-8")
            k["Content-Type"] = "application/json"
        for name, wert in (kopf or {}).items():
            if wert is None:
                k.pop(name, None)
            else:
                k[name] = wert
        antwort = await self._schicken(methode, pfad, k, params, inhalt, self.frist_schreiben if schreibend else self.frist_lesen)
        if antwort.status == 0 and not abbruch_ok:
            self._fehlstarts += 1
            if self._fehlstarts >= FEHLER_HINTEREINANDER:
                raise LaufAbbruch(f"Der Dienst antwortet nicht mehr ({FEHLER_HINTEREINANDER} Anfragen hintereinander ohne Antwort: {antwort.fehler}). "
                                  "Lauf abgebrochen.")
        elif antwort.status:
            self._fehlstarts = 0
        return antwort

    async def _schicken(self, methode, pfad, kopf, params, inhalt, frist) -> Antwort:
        try:
            async with asyncio.timeout(frist):
                r = await self.client.request(methode, pfad, headers=kopf, params=params, content=inhalt)
        except (TimeoutError, httpx.TimeoutException):
            return Antwort(0, {}, b"", f"Zeitüberschreitung nach {frist:g} s")
        except httpx.TransportError as e:
            return Antwort(0, {}, b"", verbindungsfehler(e))
        return Antwort(r.status_code, {n.lower(): v for n, v in r.headers.items()}, r.content)

    async def seite(self, url: str) -> Antwort:
        """Eine Seite ohne Zugangsdaten (Board-Oberfläche): kein Bearer, kein Platzkopf, denn sie liegt auf einer anderen Herkunft."""
        return await self._schicken("GET", url, {"Accept": "text/html"}, None, None, self.frist_lesen)

    # ---------- lesende Aufrufe V1 bis V3 ----------
    async def v1(self, platz: int = PLATZ_A, **kw) -> Antwort:
        return await self.senden("GET", f"{PFAD}/durchlauf", platz=platz, **kw)

    async def tickets(self, platz: int = PLATZ_A, **params) -> Antwort:
        return await self.senden("GET", f"{PFAD}/tickets", platz=platz, params={k: str(v) for k, v in params.items()} or None)

    async def ticket(self, platz: int, nummer: str, **kw) -> Antwort:
        return await self.senden("GET", f"{PFAD}/tickets/{nummer}", platz=platz, **kw)

    # ---------- schreibende Aufrufe V4 bis V7 ----------
    async def schreiben(self, methode: str, pfad: str, daten: object, *, platz: int = PLATZ_A, durchlauf: str | None = None,
                        schluessel: str | None = None, modus: str = "normal", kopf: dict | None = None, **kw) -> Antwort:
        """modus: normal (Rate einhalten, nach 429 einmal mit demselben Schlüssel wiederholen), gebremst (Rate einhalten, jedes 429 ist
        ein Ergebnis), frei (nicht warten, aber vermerken), sonde (zählt nicht: eine Form-Prüfung, die vor der Schreibrate scheitert)."""
        schluessel = schluessel or str(uuid.uuid4())
        k = {"X-WAMO-Durchlauf": durchlauf or self.durchlauf.get(platz, str(uuid.uuid4())), "Idempotency-Key": schluessel, **(kopf or {})}
        takt = self.schreibtakt(platz)
        if modus in ("normal", "gebremst"):
            await takt.vor_schreiben()
        elif modus == "frei":
            takt.vermerken()
        antwort = await self.senden(methode, pfad, platz=platz, daten=daten, kopf=k, schreibend=True, **kw)
        if antwort.status == 429 and modus == "normal":
            await takt.leeren()
            takt.vermerken()
            antwort = await self.senden(methode, pfad, platz=platz, daten=daten, kopf=k, schreibend=True, **kw)
        return antwort

    async def anlegen(self, platz: int, titel: str, beschreibung: str = "", *, typ: str = "bug", prioritaet: str = "mittel", **kw) -> Antwort:
        daten = {"typ": typ, "prioritaet": prioritaet, "titel": titel, "beschreibung": beschreibung}
        return await self.schreiben("POST", f"{PFAD}/tickets", daten, platz=platz, **kw)

    async def aendern(self, platz: int, nummer: str, version: int, felder: dict, **kw) -> Antwort:
        return await self.schreiben("PATCH", f"{PFAD}/tickets/{nummer}", {"erwartete_version": version, "felder": felder}, platz=platz, **kw)

    async def status_setzen(self, platz: int, nummer: str, version: int, status: str, **kw) -> Antwort:
        return await self.schreiben("POST", f"{PFAD}/tickets/{nummer}/status", {"erwartete_version": version, "status": status}, platz=platz, **kw)

    async def kommentieren(self, platz: int, nummer: str, version: int, text: str, **kw) -> Antwort:
        a = await self.schreiben("POST", f"{PFAD}/tickets/{nummer}/kommentare", {"erwartete_version": version, "text": text}, platz=platz, **kw)
        if a.status == 201:
            self.kommentare[platz] += 1
        return a
