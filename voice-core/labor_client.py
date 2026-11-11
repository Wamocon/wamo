"""Dienstclient des Kerns zum Test-Jira-Dienst der Kollegen (Vertrag Version 1, docs/labor-dienst-vertrag.md).

Der Client kennt nur V1 bis V7 (GET, POST, PATCH unter /api/kern/), nie etwas Löschendes. Jeder Schreibaufruf trägt durchlauf_id (Kopf
X-WAMO-Durchlauf, Befund 9) und einen Idempotenzschlüssel; nur mit demselben Schlüssel wird einmal wiederholt (Zeitüberschreitung, Verbindungs-
oder Dienstfehler). Der Dienst prüft Durchlauf und Ticketversion atomar (409).

Was der Kern aus dem Dienst übernimmt, ist geprüft: Antworten höchstens 64 KB, genau die Felder des Vertrags mit den Typen und Wertebereichen,
Text normiert (labor_vertrag.normiere). Fehler sind LaborFehler mit einem Code aus dem Vertrag oder einem lokalen (zeitueberschreitung,
nicht_erreichbar, antwort_ungueltig) und einem festen Satz; der Text des Dienstes wird nie weitergegeben. Umleitungen werden nie befolgt, damit der
Bearer nirgends anders hingeht; Proxys aus der Umgebung gelten nicht.

client_aus_umgebung() liest WAMO_LABOR_URL und WAMO_LABOR_TOKEN streng (HTTPS, nur Loopback darf Klartext, keine Zugangsdaten und keine Abfrage in
der Adresse) und lehnt die Adresse der Attrappe ab (labor_vertrag.ATTRAPPE_HOST): Die Attrappe läuft nie vor Besuchern. Das Modul importiert die
Attrappe nicht. Das Geheimnis steht nie in Meldungen, Protokollen oder repr().
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from loguru import logger

import wamo_tls
from labor_vertrag import (
    ATTRAPPE_HOST,
    BESCHREIBUNG_MAX,
    FEHLER_SAETZE,
    KOMMENTAR_MAX,
    PRIORITAETEN,
    STATUS,
    TITEL_MAX,
    TYPEN,
    UUID_RE,
    nummer_gueltig,
    normiere,
)

PFAD = "/api/kern"
LESE_SECS = 5.0
SCHREIB_SECS = 8.0
VERBINDEN_SECS = 3.0
ANFRAGE_MAX = 16 * 1024
ANTWORT_MAX = 64 * 1024
PLATZ_MIN, PLATZ_MAX = 1, 16
TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{32,200}")
ZEIT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")
DIENST_CODES = frozenset({"token_ungueltig", "platz_unbekannt", "ticket_unbekannt", "durchlauf_beendet", "ticket_veraendert",
                          "eingabe_ungueltig", "zu_gross", "quote_erreicht", "nicht_erlaubt", "dienst_fehler"})
WIEDERHOLBAR = frozenset({"zeitueberschreitung", "nicht_erreichbar", "dienst_fehler"})
TICKET_FELDER = {"nummer", "titel", "typ", "prioritaet", "status", "beschreibung", "version", "im_durchlauf"}


class LaborFehler(Exception):
    """Fehler beim Aufruf des Dienstes mit einem Code (Vertrag Abschnitt 4 oder lokal) und einem festen Satz für den Teilnehmer."""

    def __init__(self, code: str, status: int = 0):
        super().__init__(code)
        self.code, self.status = code, status

    @property
    def satz(self) -> str:
        return FEHLER_SAETZE.get(self.code, FEHLER_SAETZE["dienst_fehler"])


class LaborKonfigFehler(ValueError):
    """WAMO_LABOR_URL oder WAMO_LABOR_TOKEN fehlt oder ist unbrauchbar. Die Meldung nennt nie einen Wert."""


@dataclass(frozen=True)
class Durchlauf:
    id: str
    offen: bool
    einwilligung_version: int | None
    einwilligung_zeit: str | None


def _ganz(wert: object) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool)


def _ungueltig() -> LaborFehler:
    return LaborFehler("antwort_ungueltig")


def _text(wert: object, zeilen: bool, hoechstens: int, mindestens: int = 0) -> str:
    """Text aus dem Dienst: zu lang oder kein Text ist ein Fehler, sonst normiert."""
    if not isinstance(wert, str) or len(wert) > hoechstens:
        raise _ungueltig()
    ergebnis = normiere(wert, zeilen=zeilen)
    if ergebnis is None or len(ergebnis) < mindestens:
        raise _ungueltig()
    return ergebnis


def ticket_pruefen(obj: object) -> dict:
    """Ticket nach Vertrag: genau diese Felder, Typen und Wertebereiche, Text normiert."""
    if not isinstance(obj, dict) or set(obj) != TICKET_FELDER:
        raise _ungueltig()
    if (not nummer_gueltig(obj["nummer"]) or obj["typ"] not in TYPEN or obj["prioritaet"] not in PRIORITAETEN or obj["status"] not in STATUS
            or not _ganz(obj["version"]) or obj["version"] < 1 or not isinstance(obj["im_durchlauf"], bool)):
        raise _ungueltig()
    return {**obj, "titel": _text(obj["titel"], False, TITEL_MAX, 1), "beschreibung": _text(obj["beschreibung"], True, BESCHREIBUNG_MAX)}


def _ticket_mit_kommentaren(obj: object) -> dict:
    if not isinstance(obj, dict) or set(obj) != TICKET_FELDER | {"kommentare"} or not isinstance(obj["kommentare"], list) or len(obj["kommentare"]) > 20:
        raise _ungueltig()
    kommentare = []
    for k in obj["kommentare"]:
        if not isinstance(k, dict) or set(k) != {"kommentar_id", "text"} or not _ganz(k["kommentar_id"]):
            raise _ungueltig()
        kommentare.append({"kommentar_id": k["kommentar_id"], "text": _text(k["text"], True, KOMMENTAR_MAX)})
    return {**ticket_pruefen({k: v for k, v in obj.items() if k != "kommentare"}), "kommentare": kommentare}


def _durchlauf_pruefen(obj: object) -> Durchlauf:
    if not isinstance(obj, dict) or set(obj) != {"durchlauf_id", "offen", "einwilligung_version", "einwilligung_zeit"}:
        raise _ungueltig()
    version, zeit = obj["einwilligung_version"], obj["einwilligung_zeit"]
    if (not isinstance(obj["durchlauf_id"], str) or not UUID_RE.fullmatch(obj["durchlauf_id"]) or not isinstance(obj["offen"], bool)
            or not (version is None or (_ganz(version) and version >= 0)) or not (zeit is None or (isinstance(zeit, str) and ZEIT_RE.fullmatch(zeit)))):
        raise _ungueltig()
    return Durchlauf(obj["durchlauf_id"].lower(), obj["offen"], version, zeit)


def _eingabe(bedingung: bool) -> None:
    if not bedingung:
        raise LaborFehler("eingabe_ungueltig")


def _uuid(wert: object) -> bool:
    return isinstance(wert, str) and UUID_RE.fullmatch(wert) is not None


class LaborClient:
    """Client für den Platz eines Laborprofils. transport und verify sind für Tests und die WAMO-CA."""

    def __init__(self, url: str, token: str, platz: int, *, transport: httpx.AsyncBaseTransport | None = None, verify=True,
                 lese_secs: float = LESE_SECS, schreib_secs: float = SCHREIB_SECS):
        if not _ganz(platz) or not PLATZ_MIN <= platz <= PLATZ_MAX:
            raise ValueError("Platz muss eine Zahl von 1 bis 16 sein")
        self.platz = platz
        self._url = url.rstrip("/")
        self._lese, self._schreib = lese_secs, schreib_secs
        self._http = httpx.AsyncClient(
            base_url=self._url, transport=transport, verify=verify, follow_redirects=False, trust_env=False,
            headers={"Authorization": f"Bearer {token}", "X-WAMO-Platz": str(platz), "Accept": "application/json"})

    def __repr__(self) -> str:
        return f"LaborClient(platz={self.platz})"

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- Lesen ----------
    async def durchlauf(self) -> Durchlauf:
        """V1."""
        return _durchlauf_pruefen((await self._anfrage("GET", "/durchlauf"))[1])

    async def tickets(self, *, q: str | None = None, nummer: str | None = None, typ: str | None = None, prioritaet: str | None = None,
                      status: str | None = None, nur_durchlauf: bool = False, limit: int = 10) -> dict:
        """V2: {"tickets": [...], "gesamt": n}."""
        _eingabe(q is None or (isinstance(q, str) and len(q) <= 100))
        _eingabe(nummer is None or nummer_gueltig(nummer))
        _eingabe(typ is None or typ in TYPEN)
        _eingabe(prioritaet is None or prioritaet in PRIORITAETEN)
        _eingabe(status is None or status in STATUS)
        _eingabe(_ganz(limit) and 1 <= limit <= 10)
        params = {k: v for k, v in (("q", q or None), ("nummer", nummer), ("typ", typ), ("prioritaet", prioritaet), ("status", status)) if v}
        if nur_durchlauf:
            params["nur_durchlauf"] = "true"
        params["limit"] = str(limit)
        daten = (await self._anfrage("GET", "/tickets", params=params))[1]
        if (not isinstance(daten, dict) or set(daten) != {"tickets", "gesamt"} or not isinstance(daten["tickets"], list) or len(daten["tickets"]) > 10
                or not _ganz(daten["gesamt"]) or daten["gesamt"] < len(daten["tickets"])):
            raise _ungueltig()
        return {"tickets": [ticket_pruefen(t) for t in daten["tickets"]], "gesamt": daten["gesamt"]}

    async def ticket(self, nummer: str) -> dict:
        """V3: Ticket mit den letzten Kommentaren."""
        _eingabe(nummer_gueltig(nummer))
        return _ticket_mit_kommentaren((await self._anfrage("GET", f"/tickets/{nummer}"))[1])

    # ---------- Schreiben (alle mit durchlauf_id und Idempotenzschlüssel) ----------
    async def anlegen(self, *, durchlauf_id: str, schluessel: str, typ: str, prioritaet: str, titel: str, beschreibung: str) -> dict:
        """V4: Ticket anlegen (201)."""
        _eingabe(typ in TYPEN and prioritaet in PRIORITAETEN and isinstance(titel, str) and isinstance(beschreibung, str))
        t = await self._schreiben("POST", "/tickets", {"typ": typ, "prioritaet": prioritaet, "titel": titel, "beschreibung": beschreibung},
                                  durchlauf_id, schluessel, 201)
        return ticket_pruefen(t)

    async def aendern(self, *, durchlauf_id: str, schluessel: str, nummer: str, erwartete_version: int, felder: dict) -> dict:
        """V5: Felder ändern (titel, beschreibung, typ, prioritaet)."""
        _eingabe(nummer_gueltig(nummer) and _ganz(erwartete_version) and erwartete_version >= 1 and isinstance(felder, dict) and felder)
        _eingabe(set(felder) <= {"titel", "beschreibung", "typ", "prioritaet"} and all(isinstance(v, str) for v in felder.values()))
        t = await self._schreiben("PATCH", f"/tickets/{nummer}", {"erwartete_version": erwartete_version, "felder": felder}, durchlauf_id, schluessel, 200)
        return ticket_pruefen(t)

    async def status_setzen(self, *, durchlauf_id: str, schluessel: str, nummer: str, erwartete_version: int, status: str) -> dict:
        """V6: Status setzen."""
        _eingabe(nummer_gueltig(nummer) and _ganz(erwartete_version) and erwartete_version >= 1 and status in STATUS)
        t = await self._schreiben("POST", f"/tickets/{nummer}/status", {"erwartete_version": erwartete_version, "status": status},
                                  durchlauf_id, schluessel, 200)
        return ticket_pruefen(t)

    async def kommentieren(self, *, durchlauf_id: str, schluessel: str, nummer: str, erwartete_version: int, text: str) -> dict:
        """V7: Kommentar anlegen (201): {"kommentar_id": n, "ticket": Ticket}."""
        _eingabe(nummer_gueltig(nummer) and _ganz(erwartete_version) and erwartete_version >= 1 and isinstance(text, str))
        d = await self._schreiben("POST", f"/tickets/{nummer}/kommentare", {"erwartete_version": erwartete_version, "text": text},
                                  durchlauf_id, schluessel, 201)
        if not isinstance(d, dict) or set(d) != {"kommentar_id", "ticket"} or not _ganz(d["kommentar_id"]):
            raise _ungueltig()
        return {"kommentar_id": d["kommentar_id"], "ticket": ticket_pruefen(d["ticket"])}

    async def _schreiben(self, methode: str, pfad: str, body: dict, durchlauf_id: str, schluessel: str, erwartet: int) -> object:
        _eingabe(_uuid(durchlauf_id) and _uuid(schluessel))
        kopf = {"X-WAMO-Durchlauf": durchlauf_id, "Idempotency-Key": schluessel, "Content-Type": "application/json"}
        return (await self._anfrage(methode, pfad, body=body, kopf=kopf, schreibend=True, erwartet=erwartet))[1]

    # ---------- Übertragung ----------
    async def _anfrage(self, methode: str, pfad: str, *, params: dict | None = None, body: dict | None = None, kopf: dict | None = None,
                       schreibend: bool = False, erwartet: int = 200) -> tuple[int, object]:
        roh = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        if roh is not None and len(roh) > ANFRAGE_MAX:
            raise LaborFehler("zu_gross")
        versuche = 2 if schreibend else 1  # nur ein Schreibaufruf mit demselben Idempotenzschlüssel darf wiederholt werden
        for versuch in range(versuche):
            try:
                return await self._einmal(methode, PFAD + pfad, params, roh, kopf, schreibend, erwartet)
            except LaborFehler as e:
                if versuch + 1 < versuche and e.code in WIEDERHOLBAR:
                    logger.info("Labor-Dienst: {} {} ({}), wiederhole mit demselben Schlüssel", methode, pfad.split("/")[1] if "/" in pfad else pfad, e.code)
                    continue
                raise
        raise LaborFehler("dienst_fehler")  # nicht erreichbar

    async def _einmal(self, methode, pfad, params, roh, kopf, schreibend, erwartet) -> tuple[int, object]:
        frist = httpx.Timeout(self._schreib if schreibend else self._lese, connect=VERBINDEN_SECS)
        try:
            async with self._http.stream(methode, pfad, params=params, content=roh, headers=kopf, timeout=frist) as antwort:
                status, teile, summe = antwort.status_code, [], 0
                async for stueck in antwort.aiter_bytes():
                    summe += len(stueck)
                    if summe > ANTWORT_MAX:
                        raise _ungueltig()
                    teile.append(stueck)
        except httpx.TimeoutException as e:
            raise LaborFehler("zeitueberschreitung") from e
        except httpx.HTTPError as e:
            raise LaborFehler("nicht_erreichbar") from e
        daten = b"".join(teile)
        if status >= 400:
            raise _fehler_aus(status, daten)
        if status != erwartet:
            raise _ungueltig()
        try:
            return status, json.loads(daten.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError) as e:
            raise _ungueltig() from e


def _fehler_aus(status: int, daten: bytes) -> LaborFehler:
    """Fehlercode des Dienstes (nur die des Vertrags), sonst ein lokaler. Der Text des Dienstes wird nie gelesen."""
    try:
        obj = json.loads(daten.decode("utf-8"))
        code = obj.get("fehler") if isinstance(obj, dict) else None
    except (ValueError, UnicodeDecodeError, RecursionError):
        code = None
    if code in DIENST_CODES:
        return LaborFehler(code, status)
    return LaborFehler("dienst_fehler" if status >= 500 else "antwort_ungueltig", status)


# ---------- Einstellung ----------
def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def adresse_pruefen(url: object) -> str:
    """Die geprüfte Basisadresse (Schema, Host, Port) oder LaborKonfigFehler. Eine Adresse der Attrappe gilt nie."""
    if not isinstance(url, str) or not url:
        raise LaborKonfigFehler("WAMO_LABOR_URL fehlt")
    try:
        teile = urlsplit(url)
        host, port = (teile.hostname or "").lower().rstrip("."), teile.port
    except ValueError as e:
        raise LaborKonfigFehler("WAMO_LABOR_URL ist ungültig") from e
    if teile.scheme not in ("https", "http") or not host or teile.username is not None or teile.password is not None \
            or teile.query or teile.fragment or teile.path not in ("", "/"):
        raise LaborKonfigFehler("WAMO_LABOR_URL ist ungültig (nur Schema, Host und Port)")
    if teile.scheme == "http" and not _loopback(host):
        raise LaborKonfigFehler("WAMO_LABOR_URL braucht HTTPS (Klartext nur für Loopback)")
    if host == ATTRAPPE_HOST or host.endswith("." + ATTRAPPE_HOST):
        raise LaborKonfigFehler("WAMO_LABOR_URL zeigt auf die Attrappe, die es nur in Tests gibt")
    return f"{teile.scheme}://{host if ':' not in host else '[' + host + ']'}" + (f":{port}" if port else "")


def client_aus_umgebung(platz: int, *, transport: httpx.AsyncBaseTransport | None = None) -> LaborClient:
    """Client für diesen Platz aus WAMO_LABOR_URL und WAMO_LABOR_TOKEN (Geheimnis nur in core.env). Jeder Fehler ist LaborKonfigFehler."""
    basis = adresse_pruefen(os.getenv("WAMO_LABOR_URL"))
    token = os.getenv("WAMO_LABOR_TOKEN")
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise LaborKonfigFehler("WAMO_LABOR_TOKEN fehlt oder ist unbrauchbar (32 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-)")
    try:
        verify = wamo_tls.https_context() or True  # die WAMO-CA, falls gesetzt
    except wamo_tls.TlsConfigError as e:
        raise LaborKonfigFehler("WAMO_CA_FILE ist unbrauchbar") from e
    return LaborClient(basis, token, platz, transport=transport, verify=verify)
