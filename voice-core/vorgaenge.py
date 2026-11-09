"""Vorgänge: was WAMO gerade abarbeitet, live für die Oberfläche (Tafel "Vorgänge").

Ein Vorgang hat einen Titel, eine Art, einen Status, nummerierte Schritte mit je einem Fortschritt, Prüfkriterien, optional
eine Runde n von m, eine Freigabe und ein Ergebnis auf dem Canvas. Boss (boss.py) und Entwicklungsaufträge (dev_agent.py)
melden ihre Arbeit hier an. Die Oberfläche bekommt je Änderung die Servernachricht
{"type": "vorgang", "vorgang": {...}} (Felder in Vorgang.als_dict, Prüfung in ui/src/lib/nachrichten.ts).

Rechnung
  * gesamt = Mittel der Schrittfortschritte (fertig und übersprungen zählen voll, ein fertiger Vorgang hat 1).
    Steht die Zahl der Schritte nicht fest (offenes_ende, zum Beispiel ein Lauf ohne Aufgabenliste), steigt gesamt nie über
    0,9 und es gibt keine Restzeit: eine Schätzung ohne Grundlage wird nicht vorgetäuscht.
  * restzeit_s = verstrichen / gesamt * (1 - gesamt), erst ab gesamt 0,1 und nur, solange gearbeitet wird.

Senden (nie den Audiopfad blockieren, siehe Skill latency-critical-systems)
  * Alle Änderungen sind synchron und kosten nur Rechenzeit im Speicher. Das Senden macht ein eigener Hintergrundtask mit
    einer Schlange, in der je Vorgang nur der neueste Stand wartet (jede Nachricht ist ein ganzer Stand, nichts geht
    verloren, der Speicher wächst nicht). Jedes Senden hat eine Frist; ein Fehler wird nur als Typ protokolliert.
  * Fortschritt wird auf höchstens vier Nachrichten je Sekunde und Vorgang gedrosselt (der letzte Stand kommt immer an).
    Ein Statuswechsel geht sofort.
  * Beim Verbinden der Oberfläche sendet erneut_senden() alle bekannten Vorgänge.

Speichern: nur bei einem Statuswechsel, nie bei jedem Fortschritt, in die Tabelle vorgaenge (Migration 0017) mit
Zusammenfassung wie beim Senden (der neueste Stand je Vorgang gewinnt). Fehler stören nichts.

Texte (Titel, Schritte, Hinweise) laufen durch clean_inline und schluessel.schwaerzen: keine Steuerzeichen, keine Schlüssel.
Sie sind Daten für die Anzeige, nie Anweisungen.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace

from loguru import logger

from memory import DB_ERRORS
from persona import clean_inline
from schluessel import schwaerzen

ARTEN = ("aufgabe", "entwicklung", "geraet", "sonstiges")
STATUS = ("geplant", "laeuft", "wartet_freigabe", "pruefen", "fertig", "fehler", "abgebrochen")
ENDSTATUS = frozenset({"fertig", "fehler", "abgebrochen"})
SCHRITT_STATUS = ("offen", "laeuft", "fertig", "fehler", "uebersprungen", "wartet_freigabe")
SCHRITT_VOLL = frozenset({"fertig", "uebersprungen"})  # zählt beim Gesamtfortschritt voll

MAX_SCHRITTE = 40
MAX_KRITERIEN = 12
MAX_VORGAENGE = 40  # je Sitzung im Speicher; zuerst fallen die ältesten beendeten heraus
TITEL_ZEICHEN = 200
SCHRITT_ZEICHEN = 160
DETAIL_ZEICHEN = 200
KENNUNG_ZEICHEN = 100
DATEN_MAX_ZEICHEN = 90_000  # Stand in der Datenbank (Prüfung in Migration 0017 bei 100000)

TAKT_S = 0.25  # höchstens vier Nachrichten je Sekunde und Vorgang
SENDE_FRIST_S = 2.0
ABLAGE_FRIST_S = 10.0
REST_AB = 0.1  # Restzeit erst ab diesem Gesamtfortschritt
OFFEN_DECKEL = 0.9  # Gesamtfortschritt ohne feste Schrittzahl

_UNGESETZT: object = ...


def bereinigt(wert: object, grenze: int) -> str:
    """Text für die Anzeige: eine Zeile ohne Steuerzeichen, ohne Schlüssel, gekürzt."""
    return clean_inline(schwaerzen(str(wert if wert is not None else "")), grenze)


def _anteil(wert: object) -> float:
    """Zahl von 0 bis 1 (auf Tausendstel); alles andere ist 0."""
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        return 0.0
    return round(max(0.0, min(1.0, float(wert))), 3)


@dataclass
class Schritt:
    nr: int
    titel: str
    status: str = "offen"
    fortschritt: float = 0.0
    detail: str | None = None


@dataclass
class Kriterium:
    text: str
    erfuellt: bool | None = None
    hinweis: str | None = None


@dataclass
class Vorgang:
    id: str
    titel: str
    art: str
    status: str = "geplant"
    gestartet_ms: int = 0
    beendet_ms: int | None = None
    arbeit: list[Schritt] = field(default_factory=list)  # Schritte der Arbeit
    freigaben: list[Schritt] = field(default_factory=list)  # Freigabeschritte, stehen hinter der Arbeit
    kriterien: list[Kriterium] = field(default_factory=list)
    runde: int | None = None
    max_runden: int | None = None
    freigabe_id: str | None = None
    canvas_id: str | None = None
    offenes_ende: bool = False
    start_monoton: float = 0.0

    def schritte(self) -> list[Schritt]:
        """Alle Schritte mit fortlaufender Nummer ab 1 (Kopien, die Nummer ist abgeleitet)."""
        return [replace(s, nr=i) for i, s in enumerate((*self.arbeit, *self.freigaben), start=1)]

    def gesamt(self) -> float:
        if self.status == "fertig":
            return 1.0
        schritte = (*self.arbeit, *self.freigaben)
        if not schritte:
            return 0.0
        summe = sum(1.0 if s.status in SCHRITT_VOLL else s.fortschritt for s in schritte)
        wert = max(0.0, min(1.0, summe / len(schritte)))
        return min(wert, OFFEN_DECKEL) if self.offenes_ende else wert

    def restzeit_s(self, jetzt: float) -> int | None:
        if self.status not in ("laeuft", "pruefen") or self.offenes_ende:
            return None
        gesamt = self.gesamt()
        if gesamt < REST_AB or gesamt >= 1.0:
            return None
        return max(0, round(max(0.0, jetzt - self.start_monoton) / gesamt * (1.0 - gesamt)))

    def als_dict(self, jetzt: float) -> dict:
        return {
            "id": self.id, "titel": self.titel, "art": self.art, "status": self.status,
            "gestartet_ms": self.gestartet_ms, "beendet_ms": self.beendet_ms,
            "gesamt": round(self.gesamt(), 3), "restzeit_s": self.restzeit_s(jetzt),
            "schritte": [{"nr": s.nr, "titel": s.titel, "status": s.status, "fortschritt": s.fortschritt,
                          "detail": s.detail} for s in self.schritte()],
            "kriterien": [{"text": k.text, "erfuellt": k.erfuellt, "hinweis": k.hinweis} for k in self.kriterien],
            "runde": self.runde, "max_runden": self.max_runden, "freigabe_id": self.freigabe_id,
            "ergebnis": {"canvas_id": self.canvas_id} if self.canvas_id else None,
        }

    def nachricht(self, jetzt: float) -> dict:
        return {"type": "vorgang", "vorgang": self.als_dict(jetzt)}


class _Ausgang:
    """Schlange mit Zusammenfassung: je Schlüssel wartet nur der neueste Wert. Ein Hintergrundtask arbeitet sie ab, jeder
    Aufruf des Ziels hat eine Frist, ein Fehler wird nur als Typ protokolliert. Ohne laufende Ereignisschleife bleibt der
    Wert liegen (kein Senden), es wird nie etwas geworfen."""

    def __init__(self, ziel: Callable[[dict], Awaitable[None]], name: str, frist_s: float):
        self._ziel = ziel
        self._name = name
        self._frist = frist_s
        self._offen: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._zu = False

    def __len__(self) -> int:
        return len(self._offen)

    def beschaeftigt(self) -> bool:
        return bool(self._offen) or (self._task is not None and not self._task.done())

    def einreihen(self, schluessel: str, wert: dict) -> None:
        if self._zu:
            return
        self._offen.pop(schluessel, None)  # der neueste Stand kommt ans Ende
        self._offen[schluessel] = wert
        self._anstossen()

    def _anstossen(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._lauf())
        except RuntimeError:
            self._task = None

    async def _lauf(self) -> None:
        while self._offen:
            schluessel = next(iter(self._offen))
            wert = self._offen.pop(schluessel)
            try:
                await asyncio.wait_for(self._ziel(wert), self._frist)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 (die Oberfläche und die Datenbank dürfen nie etwas aufhalten)
                logger.warning("Vorgänge: {} fehlgeschlagen ({})", self._name, type(e).__name__)

    async def leeren(self, frist_s: float) -> None:
        """Wartet höchstens frist_s, bis alles abgearbeitet ist; danach wird der Rest verworfen."""
        if self._offen:
            self._anstossen()
        task = self._task
        if task is not None and not task.done():
            _, offen = await asyncio.wait({task}, timeout=frist_s)
            if offen:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._offen.clear()

    def abbrechen(self) -> None:
        self._zu = True
        self._offen.clear()
        if self._task is not None and not self._task.done():
            self._task.cancel()


Senden = Callable[[dict], Awaitable[None]]


class Vorgaenge:
    """Alle Vorgänge einer Sitzung. senden: Nachricht an die Oberfläche (bot.py: send). speichern: Stand in die Datenbank
    (datenbank_ablage). Alle Änderungen sind synchron und werfen nie; ungültige Angaben (Status, Nummer) bleiben ohne Wirkung."""

    def __init__(
        self,
        senden: Senden,
        *,
        speichern: Callable[[dict], Awaitable[None]] | None = None,
        uhr: Callable[[], float] = time.monotonic,
        wanduhr: Callable[[], float] = time.time,
        takt_s: float = TAKT_S,
        frist_s: float | None = None,
        max_vorgaenge: int = MAX_VORGAENGE,
    ):
        self._uhr = uhr
        self._wanduhr = wanduhr
        self._takt = takt_s
        self._max = max_vorgaenge
        self._vorgaenge: OrderedDict[str, Vorgang] = OrderedDict()
        self._ausgang = _Ausgang(senden, "Senden", frist_s or SENDE_FRIST_S)
        self._ablage = _Ausgang(speichern, "Speichern", frist_s or ABLAGE_FRIST_S) if speichern else None
        self._letzte: dict[str, float] = {}  # wann zuletzt gesendet (Drosselung)
        self._gesendeter_status: dict[str, str] = {}  # Status der letzten Nachricht (Statuswechsel gehen sofort raus)
        self._timer: dict[str, asyncio.TimerHandle] = {}  # nachholendes Senden des letzten Stands
        self._gespeichert: dict[str, str] = {}  # zuletzt gespeicherter Status
        self._zu = False

    # ---------- Lesen ----------
    def hole(self, vorgang_id: str) -> Vorgang | None:
        return self._vorgaenge.get(vorgang_id)

    def alle(self) -> list[Vorgang]:
        return list(self._vorgaenge.values())

    def aktive(self) -> list[Vorgang]:
        return [v for v in self._vorgaenge.values() if v.status not in ENDSTATUS]

    # ---------- Anlegen und Ändern (synchron, werfen nie) ----------
    def neu(
        self,
        titel: str,
        art: str = "sonstiges",
        *,
        id: str | None = None,  # noqa: A002 (Feld der Nachricht)
        schritte: Iterable[str] = (),
        kriterien: Iterable[str] = (),
        max_runden: int | None = None,
        canvas_id: str | None = None,
        offenes_ende: bool = False,
    ) -> Vorgang:
        v = Vorgang(
            id=id or str(uuid.uuid4()), titel=bereinigt(titel, TITEL_ZEICHEN) or "Vorgang",
            art=art if art in ARTEN else "sonstiges", gestartet_ms=self._ms(), start_monoton=self._uhr(),
            canvas_id=bereinigt(canvas_id, KENNUNG_ZEICHEN) or None, offenes_ende=bool(offenes_ende),
        )
        v.arbeit = [Schritt(0, bereinigt(t, SCHRITT_ZEICHEN)) for t in list(schritte)[:MAX_SCHRITTE]]
        v.kriterien = [Kriterium(bereinigt(k, SCHRITT_ZEICHEN)) for k in list(kriterien)[:MAX_KRITERIEN]]
        if isinstance(max_runden, int) and not isinstance(max_runden, bool) and max_runden >= 1:
            v.max_runden = max_runden
        self._vorgaenge[v.id] = v
        self._aufraeumen()
        self._geaendert(v)
        return v

    def status_setzen(self, v: Vorgang, status: str) -> None:
        if status not in STATUS or v.status in ENDSTATUS:
            return
        if status in ENDSTATUS:
            self.beenden(v, status)
            return
        if status == "laeuft" and v.status == "geplant":  # Beginn der Arbeit: ab hier zählt die Zeit
            v.start_monoton, v.gestartet_ms = self._uhr(), self._ms()
        v.status = status
        self._geaendert(v)

    def starten(self, v: Vorgang) -> None:
        if v.status == "geplant":
            self.status_setzen(v, "laeuft")

    def schritte_setzen(self, v: Vorgang, schritte: Iterable[Schritt], *, offenes_ende: bool | None = None) -> None:
        """Die Schritte der Arbeit ersetzen (zum Beispiel aus der Aufgabenliste von Claude Code)."""
        if v.status in ENDSTATUS:
            return
        neu: list[Schritt] = []
        for s in list(schritte)[:MAX_SCHRITTE]:
            status = s.status if s.status in SCHRITT_STATUS else "offen"
            fortschritt = 1.0 if status == "fertig" else _anteil(s.fortschritt)
            detail = bereinigt(s.detail, DETAIL_ZEICHEN) if s.detail else None
            neu.append(Schritt(0, bereinigt(s.titel, SCHRITT_ZEICHEN), status, fortschritt, detail or None))
        v.arbeit = neu
        if offenes_ende is not None:
            v.offenes_ende = bool(offenes_ende)
        self._geaendert(v)

    def schritt(self, v: Vorgang, nr: int, *, status: str | None = None, fortschritt: float | None = None,
                detail: object = _UNGESETZT) -> None:
        if v.status in ENDSTATUS or isinstance(nr, bool) or not isinstance(nr, int) or not 1 <= nr <= len(v.arbeit):
            return
        s = v.arbeit[nr - 1]
        if status is not None and status in SCHRITT_STATUS:
            s.status = status
            if fortschritt is None:
                s.fortschritt = 1.0 if status == "fertig" else (0.0 if status == "offen" else s.fortschritt)
        if fortschritt is not None:
            s.fortschritt = 1.0 if s.status == "fertig" else _anteil(fortschritt)
        if detail is not _UNGESETZT:
            s.detail = bereinigt(detail, DETAIL_ZEICHEN) or None
        self._geaendert(v)

    def kriterium(self, v: Vorgang, text: str, erfuellt: bool | None = None, hinweis: str | None = None) -> None:
        """Ein Prüfkriterium setzen oder (gleicher Text) aktualisieren. erfuellt: True, False oder None (offen)."""
        if v.status in ENDSTATUS:
            return
        text = bereinigt(text, SCHRITT_ZEICHEN)
        wert = erfuellt if isinstance(erfuellt, bool) else None
        hinweis = bereinigt(hinweis, DETAIL_ZEICHEN) or None if hinweis else None
        for k in v.kriterien:
            if k.text == text:
                k.erfuellt, k.hinweis = wert, hinweis
                break
        else:
            if not text or len(v.kriterien) >= MAX_KRITERIEN:
                return
            v.kriterien.append(Kriterium(text, wert, hinweis))
        self._geaendert(v)

    def runde(self, v: Vorgang, n: int, maximal: int | None = None) -> None:
        if v.status in ENDSTATUS or isinstance(n, bool) or not isinstance(n, int) or n < 1:
            return
        if maximal is not None and (isinstance(maximal, bool) or not isinstance(maximal, int) or maximal < n):
            return
        v.runde, v.max_runden = n, (maximal if maximal is not None else v.max_runden)
        self._geaendert(v)

    def freigabe(self, v: Vorgang, freigabe_id: str | None, grund: str | None = None) -> None:
        """Eine Freigabe wartet (freigabe_id der Karte in der Oberfläche) oder es wartet keine mehr (None)."""
        if freigabe_id:
            self.freigaben_setzen(v, [Schritt(0, f"Freigabe: {bereinigt(grund, SCHRITT_ZEICHEN - 10) or 'nötig'}",
                                              "wartet_freigabe")], freigabe_id)
            return
        self.freigaben_setzen(v, [replace(s, status="fertig", fortschritt=1.0) if s.status == "wartet_freigabe" else s
                                  for s in v.freigaben], None)

    def freigaben_setzen(self, v: Vorgang, schritte: Iterable[Schritt], freigabe_id: str | None) -> None:
        """Die Freigabeschritte setzen (hinter der Arbeit). Wartet einer, steht der Vorgang auf wartet_freigabe und trägt die
        Kennung der Karte; sonst geht ein wartender Vorgang zurück auf laeuft."""
        if v.status in ENDSTATUS:
            return
        v.freigaben = [Schritt(0, bereinigt(s.titel, SCHRITT_ZEICHEN), s.status if s.status in SCHRITT_STATUS else "offen",
                               1.0 if s.status == "fertig" else 0.0, None) for s in list(schritte)[:MAX_SCHRITTE]]
        wartet = any(s.status == "wartet_freigabe" for s in v.freigaben)
        v.freigabe_id = (bereinigt(freigabe_id, KENNUNG_ZEICHEN) or None) if wartet else None
        if wartet and v.status != "wartet_freigabe":
            v.status = "wartet_freigabe"
        elif not wartet and v.status == "wartet_freigabe":
            v.status = "laeuft"
        self._geaendert(v)

    def ergebnis(self, v: Vorgang, canvas_id: str | None) -> None:
        if v.status in ENDSTATUS:
            return
        v.canvas_id = bereinigt(canvas_id, KENNUNG_ZEICHEN) or None
        self._geaendert(v)

    def beenden(self, v: Vorgang, status: str = "fertig") -> None:
        """Endstatus (fertig, fehler, abgebrochen). Ein beendeter Vorgang bleibt beendet. Offene Schritte folgen dem Ende."""
        if status not in ENDSTATUS or v.status in ENDSTATUS:
            return
        wandel = {
            "fertig": {"laeuft": "fertig", "wartet_freigabe": "uebersprungen", "offen": "uebersprungen"},
            "fehler": {"laeuft": "fehler", "wartet_freigabe": "uebersprungen"},
            "abgebrochen": {"laeuft": "uebersprungen", "wartet_freigabe": "uebersprungen"},
        }[status]
        for s in (*v.arbeit, *v.freigaben):
            neu = wandel.get(s.status)
            if neu:
                s.status = neu
                if neu == "fertig":
                    s.fortschritt = 1.0
        v.status = status
        v.freigabe_id = None
        v.beendet_ms = self._ms()
        self._geaendert(v)

    # ---------- Oberfläche ----------
    def erneut_senden(self) -> None:
        """Beim Verbinden der Oberfläche: alle bekannten Vorgänge noch einmal, sofort."""
        if self._zu:
            return
        for v in self._vorgaenge.values():
            self._senden_jetzt(v)

    async def warten(self, frist_s: float = 2.0) -> None:
        """Wartet, bis alles gesendet und gespeichert ist (Tests, Sitzungsende), höchstens frist_s."""
        ende = time.monotonic() + frist_s
        while time.monotonic() < ende and (self._timer or self._ausgang.beschaeftigt()
                                           or (self._ablage is not None and self._ablage.beschaeftigt())):
            await asyncio.sleep(0.005)

    async def schliessen(self, frist_s: float = 3.0) -> None:
        """Sitzungsende: laufende Vorgänge gelten als abgebrochen und werden noch gespeichert, gesendet wird nichts mehr."""
        self._zu = True
        for handle in self._timer.values():
            handle.cancel()
        self._timer.clear()
        for v in self.aktive():
            self.beenden(v, "abgebrochen")
        self._ausgang.abbrechen()
        if self._ablage is not None:
            await self._ablage.leeren(frist_s)
            self._ablage.abbrechen()

    # ---------- Innenleben ----------
    def _ms(self) -> int:
        return int(self._wanduhr() * 1000)

    def _aufraeumen(self) -> None:
        while len(self._vorgaenge) > self._max:
            alt = next((k for k, v in self._vorgaenge.items() if v.status in ENDSTATUS), None)
            if alt is None:
                return
            del self._vorgaenge[alt]
            self._letzte.pop(alt, None)
            self._gesendeter_status.pop(alt, None)
            self._gespeichert.pop(alt, None)
            handle = self._timer.pop(alt, None)
            if handle is not None:
                handle.cancel()

    def _geaendert(self, v: Vorgang) -> None:
        jetzt = self._uhr()
        if self._ablage is not None and self._gespeichert.get(v.id) != v.status:
            self._gespeichert[v.id] = v.status  # nur bei Statuswechsel, nie bei jedem Fortschritt
            self._ablage.einreihen(v.id, self._stand(v, jetzt))
        if self._zu:
            return
        letzt = self._letzte.get(v.id)
        statuswechsel = self._gesendeter_status.get(v.id) != v.status
        if statuswechsel or letzt is None or jetzt - letzt >= self._takt:
            self._senden_jetzt(v)
        elif v.id not in self._timer:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # keine Schleife: nichts zu senden
            self._timer[v.id] = loop.call_later(max(0.0, self._takt - (jetzt - letzt)), self._nachholen, v.id)

    def _stand(self, v: Vorgang, jetzt: float) -> dict:
        stand = v.als_dict(jetzt)
        if len(json.dumps(stand, ensure_ascii=False)) > DATEN_MAX_ZEICHEN:  # nie größer als die Grenze der Datenbank
            stand = {**stand, "schritte": stand["schritte"][:5], "kriterien": []}
        return stand

    def _senden_jetzt(self, v: Vorgang) -> None:
        jetzt = self._uhr()
        self._letzte[v.id] = jetzt
        self._gesendeter_status[v.id] = v.status
        handle = self._timer.pop(v.id, None)
        if handle is not None:
            handle.cancel()
        self._ausgang.einreihen(v.id, v.nachricht(jetzt))

    def _nachholen(self, vorgang_id: str) -> None:
        self._timer.pop(vorgang_id, None)
        v = self._vorgaenge.get(vorgang_id)
        if v is not None and not self._zu:
            self._senden_jetzt(v)


# ---------- Datenbank ----------
_UPSERT = (
    "insert into {t} (id, user_id, session_id, art, titel, status, gesamt, daten, gestartet_at, beendet_at) "
    "values ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::real, $8::text::jsonb, "
    "to_timestamp($9::double precision), to_timestamp($10::double precision)) "
    "on conflict (id) do update set session_id = coalesce({t}.session_id, excluded.session_id), titel = excluded.titel, "
    "status = excluded.status, gesamt = excluded.gesamt, daten = excluded.daten, beendet_at = excluded.beendet_at "
    "where {t}.user_id = excluded.user_id"
)


def datenbank_ablage(memory: object) -> Callable[[dict], Awaitable[None]]:
    """Schreibfunktion für Vorgaenge(speichern=...): legt den Stand in vorgaenge ab (neu oder Statuswechsel, nie die Zeile
    eines anderen Profils). Ohne Datenbank passiert nichts, ein Fehler wird nur als Typ protokolliert."""

    async def speichern(stand: dict) -> None:
        if not getattr(memory, "enabled", False):
            return
        try:
            db = memory.db  # type: ignore[attr-defined]
            beendet = stand.get("beendet_ms")
            await db.execute(
                _UPSERT.format(t=db.t("vorgaenge")),
                stand["id"], memory.user_id, memory.session_id,  # type: ignore[attr-defined]
                stand["art"], stand["titel"], stand["status"], stand["gesamt"],
                json.dumps(stand, ensure_ascii=False), stand["gestartet_ms"] / 1000,
                beendet / 1000 if isinstance(beendet, int) else None,
            )
        except DB_ERRORS as e:
            logger.warning("Vorgänge: nicht gespeichert ({})", type(e).__name__)

    return speichern


def _ms_aus(zeit: object) -> int | None:
    try:
        return int(zeit.timestamp() * 1000)  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return None


def aus_zeile(zeile: object) -> dict:
    """Ein Vorgang für die Schnittstelle (GET /wamo/api/vorgaenge) aus einer Zeile von vorgaenge. Die Spalten gelten (Kennung,
    Art, Titel, Status, Gesamt, Ende); Schritte, Kriterien und Ergebnis kommen aus dem gespeicherten Stand und werden dabei
    geprüft. Die Form ist die der Servernachricht, restzeit_s ist immer leer."""
    roh = zeile["daten"]
    if isinstance(roh, str):
        try:
            roh = json.loads(roh)
        except ValueError:
            roh = {}
    stand = roh if isinstance(roh, dict) else {}
    art = zeile["art"] if zeile["art"] in ARTEN else "sonstiges"
    status = zeile["status"] if zeile["status"] in STATUS else "fehler"
    schritte = []
    for i, s in enumerate(stand.get("schritte") if isinstance(stand.get("schritte"), list) else [], start=1):
        if isinstance(s, dict) and i <= MAX_SCHRITTE:
            detail = bereinigt(s.get("detail"), DETAIL_ZEICHEN) if s.get("detail") else None
            schritte.append({"nr": i, "titel": bereinigt(s.get("titel"), SCHRITT_ZEICHEN),
                             "status": s.get("status") if s.get("status") in SCHRITT_STATUS else "offen",
                             "fortschritt": _anteil(s.get("fortschritt")), "detail": detail or None})
    kriterien = []
    for k in stand.get("kriterien") if isinstance(stand.get("kriterien"), list) else []:
        if isinstance(k, dict) and len(kriterien) < MAX_KRITERIEN:
            hinweis = bereinigt(k.get("hinweis"), DETAIL_ZEICHEN) if k.get("hinweis") else None
            kriterien.append({"text": bereinigt(k.get("text"), SCHRITT_ZEICHEN),
                              "erfuellt": k["erfuellt"] if isinstance(k.get("erfuellt"), bool) else None,
                              "hinweis": hinweis or None})
    ergebnis = stand.get("ergebnis")
    canvas = bereinigt(ergebnis.get("canvas_id"), KENNUNG_ZEICHEN) if isinstance(ergebnis, dict) else ""
    gestartet = stand.get("gestartet_ms")
    gestartet = gestartet if isinstance(gestartet, int) and not isinstance(gestartet, bool) else _ms_aus(
        zeile["gestartet_at"])
    return {
        "id": str(zeile["id"]), "titel": bereinigt(zeile["titel"], TITEL_ZEICHEN), "art": art, "status": status,
        "gestartet_ms": gestartet or 0, "beendet_ms": _ms_aus(zeile["beendet_at"]) if zeile["beendet_at"] else None,
        "gesamt": _anteil(zeile["gesamt"]), "restzeit_s": None, "schritte": schritte, "kriterien": kriterien,
        "runde": stand["runde"] if isinstance(stand.get("runde"), int) and not isinstance(stand.get("runde"), bool) else None,
        "max_runden": stand["max_runden"] if isinstance(stand.get("max_runden"), int)
        and not isinstance(stand.get("max_runden"), bool) else None,
        "freigabe_id": bereinigt(stand.get("freigabe_id"), KENNUNG_ZEICHEN) or None,
        "ergebnis": {"canvas_id": canvas} if canvas else None,
    }
