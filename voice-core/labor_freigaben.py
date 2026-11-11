"""Freigaben schreibender Labor-Aktionen (Ticket anlegen, ändern, verschieben, kommentieren, Mail senden) in einer Laborsitzung.

Eigene Klasse mit eigenem Job "labor", getrennt von der PC-Freigabe (pc_freigaben.py bleibt unverändert, Test in
test_labor_pc_unveraendert.py). Entwurf und Bedrohungen: docs/testlabor-wamo.md 3.2.

Ablauf
  1. Ein Werkzeug (labor_werkzeuge.py) prüft seine Parameter im Code und ruft anfrage(aktion, werte, ticket). Die Werte sind schon
     normiert (labor_vertrag.normiere). anfrage() prüft sie noch einmal, baut die Karte, legt den Antrag an und schickt die Karte
     {"type": "freigaben", "job": "labor"} an die Oberfläche. Das Modell bekommt nur "wartet auf den Klick", nie die Kennung.
  2. Der Klick kommt als Client-Nachricht {"type": "freigabe", "data": {"job": "labor", "id", "entscheidung"}} über die angemeldete
     Verbindung. bot.py reicht ihn nur an entscheiden() weiter, wenn es diese Klasse gibt, und sie gibt es nur für Laborprofile.
  3. entscheiden() nimmt den Antrag vor dem ersten await aus dem Speicher (genau einmal) und prüft: Frist, Durchlauf noch offen, Hash der
     gespeicherten Werte, genau "ja". Ausgeführt werden allein die gespeicherten Werte, nie Felder aus der Nachricht. Jeder Schritt steht
     in actions (pending, approved, running, done oder failed, oder rejected), ohne Texte.

Die Karte zeigt alles, was ausgeführt wird, oder es gibt keine Karte: Die Oberfläche kürzt ein Feld bei 2000 UTF-16-Einheiten und würde
sonst weniger zeigen, als geschieht. Deshalb gilt für alle Texte der Karte zusammen KARTE_MAX (1900), gemessen in UTF-16-Einheiten.

Der Hash bindet Sitzung, Profil, Durchlauf, Aktion, Werte, den Stand des Tickets (samt Version, Befund 10) und den Idempotenzschlüssel.
Die Version geht als erwartete_version in den Dienstaufruf: Hat sich das Ticket seit der Karte geändert, antwortet der Dienst mit 409
und es geschieht nichts. Ebenso trägt jeder Dienstaufruf die durchlauf_id (Befund 9).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import secrets
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace

from loguru import logger

from labor_vertrag import (
    BESCHREIBUNG_MAX,
    FEHLER_SAETZE,
    FRIST_SECS,
    KARTE_MAX,
    KOMMENTAR_MAX,
    KOMMENTAR_MIN,
    MAIL_BETREFF_MAX,
    MAIL_BETREFF_MIN,
    MAIL_TEXT_MAX,
    MAIL_TEXT_MIN,
    MAX_OFFEN,
    MAX_SCHREIBEN,
    PRIORITAET_ANZEIGE,
    PRIORITAETEN,
    STATUS,
    STATUS_ANZEIGE,
    TITEL_MAX,
    TITEL_MIN,
    TYP_ANZEIGE,
    TYPEN,
    env_int,
    nummer_gueltig,
    normiere,
    utf16_len,
)
from persona import clean_inline

JOB = "labor"
ID_RE = re.compile(r"[0-9a-f]{32}")  # 128 Bit
AUSFUEHREN_TIMEOUT_SECS = 25.0  # der Dienstclient hat eigene Fristen (8 s, einmal wiederholt), das hier ist nur die Obergrenze
SCHLIESSEN_TIMEOUT_SECS = 3.0
DURCHLAUF_PRUEFEN_TIMEOUT_SECS = 10.0  # Abfrage des Durchlaufs beim Klick: der Dienstclient braucht höchstens etwa 8 Sekunden, hier die Obergrenze
TEXT_PREFIX = "> "  # jede Zeile eines Freitextes: so kann kein Text eine Zeile der Karte vortäuschen


# ---------- Aktionen: was auf der Karte steht ----------
@dataclass(frozen=True)
class _Feld:
    schluessel: str
    bezeichnung: str
    art: str  # "wahl" (feste Liste, Anzeigename), "zeile" (eine Zeile), "text" (mehrere Zeilen mit Präfix), "nummer"
    lo: int = 0
    hi: int = 0
    pflicht: bool = True


@dataclass(frozen=True)
class _Aktion:
    grund: str
    was: str  # für Sätze an das Modell
    ziel: str
    felder: tuple[_Feld, ...]
    mit_ticket: bool
    mind_eines_von: tuple[str, ...] = ()  # ticket_aendern: mindestens eines dieser Felder


_ZIEL_JIRA = "Test-Jira, Platz {platz}"
_ZIEL_MAIL = "Test-Mail, es wird nichts versendet"
_NUMMER = _Feld("nummer", "Ticket", "nummer")

AKTIONEN: dict[str, _Aktion] = {
    "ticket_anlegen": _Aktion("Ticket anlegen", "das Anlegen des Tickets", _ZIEL_JIRA, (
        _Feld("typ", "Typ", "wahl"), _Feld("prioritaet", "Priorität", "wahl"),
        _Feld("titel", "Titel", "zeile", TITEL_MIN, TITEL_MAX), _Feld("beschreibung", "Beschreibung", "text", 0, BESCHREIBUNG_MAX)),
        mit_ticket=False),
    "ticket_aendern": _Aktion("Ticket ändern", "das Ändern des Tickets", _ZIEL_JIRA, (
        _NUMMER, _Feld("titel", "Neuer Titel", "zeile", TITEL_MIN, TITEL_MAX, False),
        _Feld("beschreibung", "Neue Beschreibung", "text", 0, BESCHREIBUNG_MAX, False),
        _Feld("typ", "Neuer Typ", "wahl", pflicht=False), _Feld("prioritaet", "Neue Priorität", "wahl", pflicht=False)),
        mit_ticket=True, mind_eines_von=("titel", "beschreibung", "typ", "prioritaet")),
    "ticket_verschieben": _Aktion("Ticket verschieben", "das Verschieben des Tickets", _ZIEL_JIRA, (
        _NUMMER, _Feld("status", "Neuer Status", "wahl")), mit_ticket=True),
    "ticket_kommentieren": _Aktion("Ticket kommentieren", "den Kommentar", _ZIEL_JIRA, (
        _NUMMER, _Feld("text", "Kommentar", "text", KOMMENTAR_MIN, KOMMENTAR_MAX)), mit_ticket=True),
    "mail_senden": _Aktion("Mail senden", "das Senden der Mail", _ZIEL_MAIL, (
        _Feld("an", "An", "zeile", 3, 100), _Feld("betreff", "Betreff", "zeile", MAIL_BETREFF_MIN, MAIL_BETREFF_MAX),
        _Feld("text", "Text", "text", MAIL_TEXT_MIN, MAIL_TEXT_MAX)), mit_ticket=False),
}
_WAHL = {"typ": (TYPEN, TYP_ANZEIGE), "prioritaet": (PRIORITAETEN, PRIORITAET_ANZEIGE), "status": (STATUS, STATUS_ANZEIGE)}


@dataclass(frozen=True)
class Ticketstand:
    """Stand des Tickets zur Antragszeit (vom Dienst gelesen): Nummer, Titel, Status und Version."""

    nummer: str
    titel: str
    status: str
    version: int


@dataclass(frozen=True)
class Antrag:
    id: str
    aktion: str
    werte: tuple[tuple[str, str], ...]
    ticket: Ticketstand | None
    durchlauf_id: str
    sitzung: str
    profil: str
    idempotenz: str
    hash: str
    erstellt: float
    gueltig: float

    def werte_dict(self) -> dict[str, str]:
        return dict(self.werte)


@dataclass(frozen=True)
class Angebot:
    """Ergebnis von anfrage(): die gesendete Karte oder der Grund (fester Satz) für das Modell, warum es keine gibt."""

    karte: dict | None = None
    grund: str | None = None


@dataclass(frozen=True)
class Ausgang:
    """Ergebnis der Ausführung: ein fester Satz (kein Text des Dienstes, höchstens eine geprüfte Nummer), ok, Ticketnummer, Fehlercode."""

    ok: bool
    satz: str
    nummer: str | None = None
    code: str | None = None


def antrag_hash(*, sitzung: str, profil: str, durchlauf_id: str, aktion: str, werte: tuple[tuple[str, str], ...],
                ticket: Ticketstand | None, idempotenz: str) -> str:
    """SHA-256 über alles, woran ein Antrag gebunden ist. Kanonisches JSON, daher kann kein Trennzeichen etwas verschieben."""
    stand = [ticket.nummer, ticket.version, ticket.titel, ticket.status] if ticket is not None else None
    roh = json.dumps(["wamo-labor-antrag-1", sitzung, profil, durchlauf_id, aktion, [list(p) for p in werte], stand, idempotenz],
                     ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


class SchreibZaehler:
    """Zählt die Anträge je Durchlauf über Sitzungen hinweg (eine neue Verbindung setzt die Zählung nicht zurück). Begrenzt im Speicher."""

    def __init__(self, hoechstens: int = 64):
        self._hoechstens = hoechstens
        self._z: OrderedDict[str, int] = OrderedDict()

    def stand(self, durchlauf_id: str) -> int:
        return self._z.get(durchlauf_id, 0)

    def zaehle(self, durchlauf_id: str) -> int:
        self._z[durchlauf_id] = self._z.get(durchlauf_id, 0) + 1
        self._z.move_to_end(durchlauf_id)
        while len(self._z) > self._hoechstens:
            self._z.popitem(last=False)
        return self._z[durchlauf_id]


_ZAEHLER = SchreibZaehler()


def _ohne_klammern(text: str) -> str:
    """Text für das Modell darf die Klammer der Meldung nicht schließen und keine eigene öffnen."""
    return text.replace("[", "(").replace("]", ")").replace("«", '"').replace("»", '"')


def _wert_ok(feld: _Feld, wert: object) -> bool:
    if not isinstance(wert, str):
        return False
    if feld.art == "wahl":
        return wert in _WAHL[feld.schluessel][0]
    if feld.art == "nummer":
        return nummer_gueltig(wert)
    if normiere(wert, zeilen=feld.art == "text") != wert:
        return False
    return feld.lo <= len(wert) <= feld.hi


def _stand_ok(ticket: Ticketstand) -> Ticketstand | None:
    """Der Stand vom Dienst ist nur Anzeige, trotzdem prüfen und normieren: eine Nummer, ein Titel, ein bekannter Status, eine Version."""
    titel = normiere(ticket.titel, zeilen=False)
    if (not nummer_gueltig(ticket.nummer) or titel is None or not titel or len(titel) > TITEL_MAX or ticket.status not in STATUS
            or not isinstance(ticket.version, int) or isinstance(ticket.version, bool) or ticket.version < 1):
        return None
    return replace(ticket, titel=titel)


def karten_texte(aktion: str, werte: Mapping[str, str], ticket: Ticketstand | None, platz: int) -> tuple[str, str, str]:
    """(grund, ziel, detail) der Karte: jeder Wert in eigenen Zeilen, Freitext mit Präfix, die Auswahlwerte mit ihrem Anzeigenamen."""
    a = AKTIONEN[aktion]
    zeilen: list[str] = []
    if ticket is not None:
        zeilen.append(f"Ticket: {ticket.nummer} ({STATUS_ANZEIGE[ticket.status]}): {ticket.titel}")
    for f in a.felder:
        if f.schluessel not in werte or f.art == "nummer":
            continue
        wert = werte[f.schluessel]
        if f.art == "wahl":
            zeilen.append(f"{f.bezeichnung}: {_WAHL[f.schluessel][1][wert]}")
        elif f.art == "zeile":
            zeilen.append(f"{f.bezeichnung}: {wert}")
        elif wert == "":
            zeilen.append(f"{f.bezeichnung}: (leer)")
        else:
            zeilen.append(f"{f.bezeichnung}:")
            zeilen.extend(TEXT_PREFIX + z for z in wert.split("\n"))
    return a.grund, a.ziel.format(platz=platz), "\n".join(zeilen)


class LaborFreigaben:
    """Offene Labor-Freigaben einer Sitzung.

    memory: log_action und update_action (Tabelle actions); sitzung und profil binden den Antrag; durchlauf_id: der Durchlauf, den die Sitzung
    beim Start gelesen hat; platz: nur für den Zieltext der Karte; von: Anzeigename; ausfuehren(antrag) -> Ausgang führt die gespeicherten
    Werte aus (labor_werkzeuge.py); durchlauf_offen() -> bool fragt den Dienst; melden(text): Nachricht an das Sprachmodell; fremdinhalt():
    turn.taint; senden(nachricht): an die Oberfläche; uhr: Zeit in Sekunden; zaehler: Zähler je Durchlauf.
    """

    def __init__(
        self,
        memory,
        *,
        sitzung: str,
        profil: str,
        durchlauf_id: str,
        platz: int,
        von: str,
        ausfuehren: Callable[[Antrag], Awaitable[Ausgang]],
        durchlauf_offen: Callable[[], Awaitable[bool]],
        melden: Callable[[str], Awaitable[None]],
        fremdinhalt: Callable[[], None],
        senden: Callable[[dict], Awaitable[None]],
        uhr: Callable[[], float] = time.monotonic,
        zaehler: SchreibZaehler | None = None,
        frist_secs: int | None = None,
        max_offen: int | None = None,
        max_schreiben: int | None = None,
    ):
        self._memory = memory
        self._sitzung, self._profil, self._durchlauf_id, self._platz = sitzung, profil, durchlauf_id, platz
        self._von = _ohne_klammern(clean_inline(von, 60)) or "Gast"
        self._ausfuehren, self._durchlauf_offen = ausfuehren, durchlauf_offen
        self._melden, self._fremdinhalt, self._senden = melden, fremdinhalt, senden
        self._uhr = uhr
        self._zaehler = zaehler if zaehler is not None else _ZAEHLER
        self.frist_secs = frist_secs if frist_secs is not None else env_int("WAMO_LABOR_FREIGABE_SECS", FRIST_SECS, 10, 600)
        self.max_offen = max_offen if max_offen is not None else env_int("WAMO_LABOR_MAX_OFFEN", MAX_OFFEN, 1, 10)
        self.max_schreiben = max_schreiben if max_schreiben is not None else env_int("WAMO_LABOR_MAX_SCHREIBEN", MAX_SCHREIBEN, 1, 200)
        self._offen: dict[str, Antrag] = {}
        self._protokoll: dict[str, asyncio.Future] = {}
        self._entschieden: deque[str] = deque(maxlen=100)
        self._hintergrund: set[asyncio.Future] = set()
        self._beendet = False  # der Durchlauf ist zu Ende (oder die Sitzung): keine neuen Anträge, kein Klick wirkt mehr

    @property
    def offen_anzahl(self) -> int:
        return len(self._offen)

    @property
    def offen(self) -> bool:
        """Wartet eine Karte? (für den Ja-Wächter in bot.py)"""
        return bool(self._offen)

    # ---------- 1. Antrag ----------
    async def anfrage(self, aktion: str, werte: Mapping[str, object], ticket: Ticketstand | None = None) -> Angebot:
        """Neuer Antrag. Alles bis zum Eintrag in _offen geschieht ohne await: zwei gleichzeitige Aufrufe überschreiten die Grenze nie."""
        self._aufraeumen()
        if self._beendet:
            return Angebot(grund="Der Durchlauf ist beendet, es gibt keine Aktionen mehr.")
        a = AKTIONEN.get(aktion)
        if a is None:
            return Angebot(grund="Diese Aktion gibt es nicht.")
        fest = self._werte_pruefen(a, werte, ticket)
        if isinstance(fest, str):
            return Angebot(grund=fest)
        werte_fest, stand = fest
        if len(self._offen) >= self.max_offen:
            return Angebot(grund="Es warten schon zu viele Freigaben. Erst darüber entscheiden lassen.")
        if self._zaehler.stand(self._durchlauf_id) >= self.max_schreiben:
            return Angebot(grund="In diesem Durchlauf sind alle schreibenden Aktionen verbraucht.")
        grund, ziel, detail = karten_texte(aktion, dict(werte_fest), stand, self._platz)
        if sum(utf16_len(t) for t in (grund, ziel, detail, self._von)) > KARTE_MAX:
            return Angebot(grund="Die Freigabekarte wäre zu lang. Kürzere Texte nehmen.")
        self._zaehler.zaehle(self._durchlauf_id)
        aid, schluessel = secrets.token_hex(16), str(uuid.uuid4())
        antrag = Antrag(
            id=aid, aktion=aktion, werte=werte_fest, ticket=stand, durchlauf_id=self._durchlauf_id, sitzung=self._sitzung,
            profil=self._profil, idempotenz=schluessel, erstellt=self._uhr(), gueltig=float(self.frist_secs),
            hash=antrag_hash(sitzung=self._sitzung, profil=self._profil, durchlauf_id=self._durchlauf_id, aktion=aktion,
                             werte=werte_fest, ticket=stand, idempotenz=schluessel))
        self._offen[aid] = antrag
        self._protokoll[aid] = self._protokollieren(antrag)
        logger.info("Labor-Freigabe {} angefragt ({})", aid[:8], aktion)
        karte = {"type": "freigaben", "job": JOB, "projekt": "", "items": [{
            "id": aid, "grund": grund, "detail": detail, "von": self._von, "ziel": ziel, "gueltig_s": self.frist_secs}]}
        try:
            await self._senden(karte)
        except Exception as e:  # noqa: BLE001 (ohne sichtbare Karte darf nichts offen bleiben)
            logger.warning("Labor-Freigabe: Karte nicht gesendet ({})", type(e).__name__)
            self._offen.pop(aid, None)
            self._hinten(self._abschliessen(antrag, "rejected"))
            return Angebot(grund="Die Freigabekarte konnte nicht angezeigt werden.")
        return Angebot(karte=karte)

    def _werte_pruefen(self, a: _Aktion, werte: Mapping[str, object], ticket: Ticketstand | None):
        """(feste Werte in Kartenreihenfolge, Stand) oder ein Satz mit dem Grund. Jeder Wert muss schon genau so normiert sein."""
        erlaubt = {f.schluessel for f in a.felder}
        if not isinstance(werte, Mapping) or not set(werte) <= erlaubt or any(f.pflicht and f.schluessel not in werte for f in a.felder):
            return "Die Angaben der Aktion stimmen nicht."
        if a.mind_eines_von and not any(k in werte for k in a.mind_eines_von):
            return "Es ist nichts zu ändern angegeben."
        paare = []
        for f in a.felder:
            if f.schluessel in werte:
                if not _wert_ok(f, werte[f.schluessel]):
                    return f"Der Wert für {f.bezeichnung} ist nicht zulässig."
                paare.append((f.schluessel, werte[f.schluessel]))
        stand = None
        if a.mit_ticket != (ticket is not None):
            return "Der Stand des Tickets fehlt oder passt nicht zur Aktion."
        if ticket is not None:
            stand = _stand_ok(ticket)
            if stand is None or stand.nummer != werte.get("nummer"):
                return "Der Stand des Tickets passt nicht zur Nummer."
        return tuple(paare), stand

    def _protokollieren(self, antrag: Antrag) -> asyncio.Future:
        """Zeile in actions (pending), ohne Texte: Werkzeug, Aktion, Ticketnummer, Durchlauf."""
        params = {"approval_id": antrag.id, "aktion": antrag.aktion, "ticket": antrag.ticket.nummer if antrag.ticket else "",
                  "durchlauf": antrag.durchlauf_id}
        return self._hinten(self._memory.log_action(tool=antrag.aktion, params=params, reason=AKTIONEN[antrag.aktion].grund, risk="change"))

    def _hinten(self, aufgabe: Awaitable) -> asyncio.Future:
        """Hintergrundaufgabe mit starker Referenz (sonst könnte sie vorzeitig eingesammelt werden)."""
        future = asyncio.ensure_future(aufgabe)
        self._hintergrund.add(future)
        future.add_done_callback(self._hintergrund.discard)
        return future

    def _abgelaufen(self, antrag: Antrag) -> bool:
        return self._uhr() - antrag.erstellt > antrag.gueltig

    def _aufraeumen(self) -> None:
        for aid in [k for k, a in self._offen.items() if self._abgelaufen(a)]:
            self._entschieden.append(aid)
            self._hinten(self._abschliessen(self._offen.pop(aid), "rejected"))

    async def _abschliessen(self, antrag: Antrag, status: str) -> None:
        await self._status(await self._action_id(antrag), status)

    # ---------- 2. und 3. Klick ----------
    async def entscheiden(self, data: object) -> Ausgang | None:
        """Entscheidung aus der Oberfläche. Liefert den Ausgang, wenn etwas ausgeführt wurde, sonst None."""
        aid = data.get("id") if isinstance(data, dict) else None
        if not isinstance(aid, str) or not ID_RE.fullmatch(aid):
            logger.warning("Labor-Freigabe mit ungültiger Kennung abgewiesen")
            return None
        if aid in self._entschieden:
            logger.info("Labor-Freigabe {}: schon entschieden (zweiter Klick)", aid[:8])
            return None
        antrag = self._offen.pop(aid, None)  # genau einmal, vor dem ersten await: ein zweiter Klick findet nichts mehr
        if antrag is None:
            logger.warning("Labor-Freigabe {} unbekannt oder abgelaufen", aid[:8])
            await self._melden_kurz("Diese Freigabe ist unbekannt oder abgelaufen. Es wurde nichts ausgeführt.")
            return None
        self._entschieden.append(aid)
        was = AKTIONEN[antrag.aktion].was
        action_id = await self._action_id(antrag)
        pruefung = await self._pruefen(antrag, was)
        if pruefung is not None:
            await self._ablehnen(action_id, pruefung[0])
            await self._melden_kurz(pruefung[1])
            return None
        if (data or {}).get("entscheidung") != "ja":
            await self._ablehnen(action_id, "abgelehnt")
            await self._melden_kurz(f"{self._von} hat {was} abgelehnt. Es wurde nichts ausgeführt.")
            return None
        await self._status(action_id, "approved")
        await self._status(action_id, "running")
        if self._beendet:  # zwischen der Prüfung und dem Aufruf liegen Datenbankzugriffe: der Durchlauf kann dort enden
            await self._status(action_id, "failed")
            await self._melden_kurz("Der Durchlauf ist beendet. Es wurde nichts ausgeführt.")
            return None
        ausgang = await self._ausfuehren_sicher(antrag)
        await self._status(action_id, "done" if ausgang.ok else "failed")
        logger.info("Labor-Freigabe {} ausgeführt: {}", aid[:8], "ok" if ausgang.ok else (ausgang.code or "fehler"))
        self._fremdinhalt()
        await self._melden(f"[Labor-Freigabe: {_ohne_klammern(ausgang.satz)} Sag {self._von} das in einem kurzen Satz.]")
        return ausgang

    async def _pruefen(self, antrag: Antrag, was: str) -> tuple[str, str] | None:
        """None, wenn alles stimmt, sonst (Grund für das Protokoll, Satz für das Modell). Fail-closed bei jedem Zweifel."""
        if self._beendet or not await self._durchlauf_noch_offen():
            return "Durchlauf beendet", "Der Durchlauf ist beendet. Es wurde nichts ausgeführt."
        if self._abgelaufen(antrag):
            return "abgelaufen", f"Die Freigabe für {was} ist abgelaufen. Es wurde nichts ausgeführt, die Aktion muss neu angestoßen werden."
        erwartet = antrag_hash(sitzung=self._sitzung, profil=self._profil, durchlauf_id=self._durchlauf_id, aktion=antrag.aktion,
                               werte=antrag.werte, ticket=antrag.ticket, idempotenz=antrag.idempotenz)
        if (antrag.sitzung, antrag.profil, antrag.durchlauf_id) != (self._sitzung, self._profil, self._durchlauf_id) \
                or not secrets.compare_digest(erwartet, antrag.hash):
            logger.warning("Labor-Freigabe {}: Hash stimmt nicht", antrag.id[:8])
            return "Werte passen nicht", "Die Freigabe passt nicht mehr zu den gespeicherten Werten. Es wurde nichts ausgeführt."
        return None

    async def _durchlauf_noch_offen(self) -> bool:
        try:
            return bool(await asyncio.wait_for(self._durchlauf_offen(), DURCHLAUF_PRUEFEN_TIMEOUT_SECS))
        except Exception as e:  # noqa: BLE001 (nicht lesbar oder keine Antwort in der Frist heißt: nicht ausführen)
            logger.warning("Labor-Freigabe: Durchlauf nicht lesbar ({})", type(e).__name__)
            return False

    async def _ausfuehren_sicher(self, antrag: Antrag) -> Ausgang:
        """Die gespeicherten Werte ausführen. Eine Ausnahme wird zu einem festen Satz, nie zu Einzelheiten."""
        try:
            ausgang = await asyncio.wait_for(self._ausfuehren(antrag), AUSFUEHREN_TIMEOUT_SECS)
            if isinstance(ausgang, Ausgang):
                return ausgang
        except TimeoutError:
            return Ausgang(False, FEHLER_SAETZE["zeitueberschreitung"], code="zeitueberschreitung")
        except Exception as e:  # noqa: BLE001
            logger.warning("Labor-Freigabe: Ausführung fehlgeschlagen ({})", type(e).__name__)
        return Ausgang(False, FEHLER_SAETZE["dienst_fehler"], code="dienst_fehler")

    async def _ablehnen(self, action_id: str | None, grund: str) -> None:
        logger.info("Labor-Freigabe nicht ausgeführt: {}", grund)
        await self._status(action_id, "rejected")

    # ---------- Ende ----------
    def durchlauf_beendet(self) -> None:
        """Der Durchlauf ist zu Ende (der Aufrufer sieht es am Dienst): offene Anträge lehnen sich ab, neue gibt es nicht, Klicks bleiben wirkungslos."""
        self._beendet = True
        for aid in list(self._offen):
            self._entschieden.append(aid)
            self._hinten(self._abschliessen(self._offen.pop(aid), "rejected"))

    async def schliessen(self) -> None:
        """Sitzungsende: alle offenen Anträge gleichzeitig ablehnen (actions), jeden mit kurzer Frist."""
        self._beendet = True
        offene, self._offen = list(self._offen.values()), {}

        async def ablehnen(antrag: Antrag) -> None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._abschliessen(antrag, "rejected"), SCHLIESSEN_TIMEOUT_SECS)

        await asyncio.gather(*(ablehnen(a) for a in offene))

    # ---------- Hilfen ----------
    async def _action_id(self, antrag: Antrag) -> str | None:
        future = self._protokoll.get(antrag.id)
        if future is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(future), 5)
        except Exception as e:  # noqa: BLE001 (Datenbank langsam oder weg: die Entscheidung gilt trotzdem)
            logger.warning("Labor-Freigabe: Protokoll nicht verfügbar ({})", type(e).__name__)
            return None

    async def _status(self, action_id: str | None, status: str) -> None:
        if action_id:
            await self._memory.update_action(action_id, status)

    async def _melden_kurz(self, text: str) -> None:
        await self._melden(f"[Labor-Freigabe: {text} Sag das in einem kurzen Satz.]")
