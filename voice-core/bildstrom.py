"""Live-Bild des eigenen Laptops in der Oberfläche (Betriebsart Büro, Tunnel je Gerät).

Was der Host entscheidet (host/live_bild.py, bei jedem Bild): Schalter AM Laptop (Standard aus, nur die Person am Laptop
schaltet ihn ein, nicht fernschaltbar), Einwilligung, Hinweis am Laptop sichtbar, Laptop eines Admins, kein gesperrter
Bildschirm, keine offene Freigabe, kein Vollbild, kein Passwortfenster, ein Zuschauer, Bilder je Sekunde und je Minute.

Was dieser Kern zusätzlich erzwingt:
  * Nur die Sitzung des Admins, der diesen Laptop gebunden hat (Tunnel je Gerät, aal2 hat das Tor schon geprüft),
    mit eingeschalteter PC-Steuerung und gehaltener PC-Sperre. Ein Gerät kommt nie aus einer Nachricht der Oberfläche.
  * Ein Bild nach dem anderen: die Oberfläche bestätigt jedes Bild (ack). Das ist der Rückstau, damit die Bilder die
    Untertitel und das Gespräch im selben Datenkanal nicht ausbremsen, und zugleich die Inaktivitätsprüfung.
  * Zeitlimit je Ansicht (ZEITLIMIT_SECS), Ende bei Sperre, Trennen, Abmelden, abgelöster Sitzung, entzogenem Gerät und
    ausgeschalteter PC-Steuerung. WAMO sagt Anfang und Ende im Gespräch.
  * Keine Aufzeichnung: Ein Bild lebt von der Antwort des Hosts bis zur Servernachricht an die Oberfläche, nur im
    Arbeitsspeicher. Es geht nie an ein Sprachmodell, nie in den Verlauf, das Gedächtnis, die Datenbank, das Log oder das
    Audit. In Logs stehen nur Zustände und Zähler.

Nachrichten von der Oberfläche (RTVI-Client-Nachricht "bildstrom"):
  {"aktion": "an", "monitor": 1}   Ansicht starten (wartet, bis der Schalter am Laptop an ist)
  {"aktion": "aus"}                 beenden
  {"aktion": "monitor", "monitor": n}
  {"aktion": "ack", "seq": n}       Bild n angezeigt
Nachrichten an die Oberfläche (Servernachrichten):
  {"type": "bildstrom", "zustand": "aus"|"wartet"|"laeuft"|"pausiert", "grund"?, "text"?, "monitore"?, "monitor"?, "rest_s"?}
  {"type": "bild", "seq", "monitor", "monitore", "breite", "hoehe", "jpeg" (Base64), "rest_s"}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable

from loguru import logger

from pc_tunnel import PcNichtErreichbar

ZEITLIMIT_SECS = 900.0  # je Ansicht; danach muss die Person sie neu öffnen
INTERVALL_SECS = 0.5  # höchstens 2 Bilder je Sekunde
ACK_FRIST_SECS = 10.0  # so lange wartet der Kern auf die Bestätigung eines Bildes (Inaktivität)
STATUS_SECS = 3.0  # Abstand der Fragen, ob der Schalter am Laptop an ist
WARTEN_MAX_SECS = 120.0  # so lange wartet die Ansicht auf den Schalter am Laptop
PAUSE_SECS = 1.0
FEHLER_MAX = 3  # aufeinanderfolgende Fehler, dann Ende
BILD_B64_MAX = 150_000  # Zeichen; der Host liefert höchstens 110 KB JPEG (Base64: etwa 147 KB)
MONITOR_MAX = 16
_B64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")

NICHT_ERLAUBT = ("Das Live-Bild gibt es nur für Admins am eigenen, gebundenen Laptop und nur bei eingeschalteter "
                 "PC-Steuerung.")
TEXT_PAUSIERT = {
    "freigabe": "Eine Freigabe ist offen: Das Bild ruht, bis sie entschieden ist.",
    "vollbild": "Am Laptop läuft ein Programm im Vollbild: Das Bild ruht.",
    "geschuetzt": "Am Laptop liegt ein geschütztes Fenster vorne (Passwort oder Anmeldung): Das Bild ruht.",
}
TEXT_AUS = {
    "schalter_aus": "Am Laptop wurde das Live-Bild ausgeschaltet.",
    "abgelaufen": "Die Zeit des Live-Bilds am Laptop ist abgelaufen.",
    "hinweis_fehlt": "Der rote Hinweis am Laptop ist nicht sichtbar.",
    "keine_einwilligung": "Die Einwilligung am Laptop fehlt.",
    "stufe": "Dieser Laptop gehört keinem Admin.",
    "nicht_eingerichtet": "Dieser Laptop ist nicht für das Live-Bild eingerichtet.",
}
TEXT_ENDE = {
    "locked": "Der Bildschirm am Laptop ist gesperrt. Das Live-Bild ist beendet.",
    "belegt": "Dieser Laptop wird schon in einer anderen Ansicht gezeigt.",
}


def _ganz(wert: object, kleinste: int, groesste: int) -> int | None:
    if isinstance(wert, int) and not isinstance(wert, bool) and kleinste <= wert <= groesste:
        return wert
    return None


def pruefe_bild(antwort: dict, seq: int, rest_s: int) -> dict | None:
    """Servernachricht für ein Bild des Hosts oder None. Streng: Base64 mit JPEG-Kopf, Größengrenze, ganze Zahlen."""
    jpeg = antwort.get("jpeg_base64")
    monitor = _ganz(antwort.get("monitor"), 1, MONITOR_MAX)
    monitore = _ganz(antwort.get("monitore"), 1, MONITOR_MAX)
    breite = _ganz(antwort.get("breite"), 1, 4096)
    hoehe = _ganz(antwort.get("hoehe"), 1, 4096)
    if not (isinstance(jpeg, str) and 8 < len(jpeg) <= BILD_B64_MAX and jpeg.startswith("/9j/")
            and _B64.fullmatch(jpeg) and None not in (monitor, monitore, breite, hoehe)):
        return None
    return {"type": "bild", "seq": seq, "monitor": monitor, "monitore": monitore, "breite": breite, "hoehe": hoehe,
            "jpeg": jpeg, "rest_s": rest_s}


class Bildstrom:
    """Eine Ansicht des eigenen Laptops. Eine Instanz je Sitzung; höchstens ein Lauf zur Zeit."""

    def __init__(
        self,
        *,
        client: Callable[[], object | None],
        darf: Callable[[], bool],
        senden: Callable[[dict], Awaitable[None]],
        ansage: Callable[[str], Awaitable[None]],
        aufruf: Callable[[object, str, dict], Awaitable[str]],
        jetzt: Callable[[], float] = time.monotonic,
        schlaf: Callable[[float], Awaitable[None]] = asyncio.sleep,
        zeitlimit: float = ZEITLIMIT_SECS,
        intervall: float = INTERVALL_SECS,
        ack_frist: float = ACK_FRIST_SECS,
        status_secs: float = STATUS_SECS,
        warten_max: float = WARTEN_MAX_SECS,
    ):
        self._client = client
        self._darf = darf
        self._senden = senden
        self._ansage = ansage
        self._aufruf = aufruf
        self._jetzt = jetzt
        self._schlaf = schlaf
        self._zeitlimit = zeitlimit
        self._intervall = intervall
        self._ack_frist = ack_frist
        self._status_secs = status_secs
        self._warten_max = warten_max
        self._task: asyncio.Task | None = None
        self._zuschauer: str | None = None
        self._monitor = 1
        self._seq = 0
        self._ack = asyncio.Event()
        self.zustand = "aus"
        self._ausstehend = False
        self._laeuft_gemeldet = False

    # ---------- Nachrichten der Oberfläche ----------
    async def nachricht(self, daten: dict) -> None:
        aktion = daten.get("aktion") if isinstance(daten, dict) else None
        if aktion == "an":
            await self._starten(_ganz(daten.get("monitor"), 1, MONITOR_MAX) or 1)
        elif aktion == "aus":
            await self.stoppen("Du hast das Live-Bild beendet.")
        elif aktion == "monitor":
            if (m := _ganz(daten.get("monitor"), 1, MONITOR_MAX)) is not None:
                self._monitor = m
        elif aktion == "ack":
            if _ganz(daten.get("seq"), 0, 2**31) == self._seq and self._ausstehend:
                self._ausstehend = False
                self._ack.set()

    async def _starten(self, monitor: int) -> None:
        self._monitor = monitor
        if self._task is not None and not self._task.done():
            return  # läuft schon: nur der Monitor wechselt
        if not self._darf() or self._client() is None:
            await self._melden("aus", grund="nicht_erlaubt", text=NICHT_ERLAUBT)
            return
        self._zuschauer = secrets.token_hex(8)
        self._seq = 0
        self._ausstehend = False
        self._ack.clear()
        self._laeuft_gemeldet = False
        self._task = asyncio.create_task(self._lauf(self._zuschauer))

    async def stoppen(self, grund: str, melden: bool = True) -> None:
        """Beendet den Lauf (von außen: Sperre, Trennen, Abmelden, PC-Steuerung aus). Wirft nie."""
        task = self._task
        laeuft = task is not None and not task.done()
        if laeuft and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if laeuft or self.zustand != "aus":
            await self._ende(grund, melden)

    # ---------- Lauf ----------
    async def _lauf(self, zuschauer: str) -> None:
        grund = "Das Live-Bild ist beendet."
        try:
            grund = await self._schleife(zuschauer)
        except asyncio.CancelledError:
            return  # stoppen() meldet das Ende selbst
        except PcNichtErreichbar:
            grund = "Die Verbindung zum Laptop ist unterbrochen. Das Live-Bild ist beendet."
        except Exception as e:  # noqa: BLE001 (nie still sterben, nie Bildinhalte in die Meldung)
            logger.warning("Live-Bild: Lauf endet mit Fehler ({})", type(e).__name__)
            grund = "Das Live-Bild ist wegen eines Fehlers beendet."
        await self._ende(grund, True)

    async def _frage(self, name: str, argumente: dict) -> dict:
        client = self._client()
        if client is None:
            raise PcNichtErreichbar("kein Client")
        text = await self._aufruf(client, name, argumente)
        try:
            daten = json.loads(text)
        except ValueError:
            return {}
        return daten if isinstance(daten, dict) else {}

    async def _schleife(self, zuschauer: str) -> str:
        start = self._jetzt()
        letzter_grund = None
        # Phase 1: warten, bis die Person am Laptop den Schalter eingeschaltet hat
        while True:
            if not self._darf():
                return NICHT_ERLAUBT
            status = await self._frage("live_bild_status", {})
            if status.get("bereit") is True:
                monitore = _ganz(status.get("monitore"), 1, MONITOR_MAX) or 1
                break
            grund = status.get("grund") if isinstance(status.get("grund"), str) else "schalter_aus"
            if grund == "stufe":
                return NICHT_ERLAUBT
            if grund != letzter_grund:
                letzter_grund = grund
                await self._melden("wartet", grund=grund if grund in TEXT_AUS else "schalter_aus")
            if self._jetzt() - start > self._warten_max:
                return "Am Laptop wurde das Live-Bild nicht eingeschaltet. Es ist beendet."
            await self._schlaf(self._status_secs)
        # Phase 2: Bilder, eines nach dem anderen
        lauf_start = self._jetzt()
        fehler = 0
        pausiert = False
        while True:
            if not self._darf():
                return "Die PC-Steuerung ist nicht mehr aktiv. Das Live-Bild ist beendet."
            rest = int(self._zeitlimit - (self._jetzt() - lauf_start))
            if rest <= 0:
                return "Das Live-Bild endet nach der Höchstdauer. Öffne es bei Bedarf neu."
            if self._ausstehend:  # das letzte Bild ist noch nicht bestätigt
                try:
                    await asyncio.wait_for(self._ack.wait(), self._ack_frist)
                except asyncio.TimeoutError:
                    return "Die Ansicht antwortet nicht mehr. Das Live-Bild ist beendet."
                self._ack.clear()
            antwort = await self._frage("bild_holen", {"zuschauer": zuschauer, "monitor": self._monitor})
            art = antwort.get("status")
            if art == "success":
                nachricht = pruefe_bild(antwort, self._seq + 1, max(0, rest))
                if nachricht is None:
                    fehler += 1
                else:
                    fehler = 0
                    if not self._laeuft_gemeldet or pausiert:
                        pausiert = False
                        await self._laeuft_melden(monitore, nachricht["monitore"], rest)
                    self._seq += 1
                    self._ausstehend = True
                    self._ack.clear()
                    await self._senden(nachricht)
                    del nachricht
                await self._schlaf(self._intervall)
            elif art == "langsamer":
                warte = _ganz(antwort.get("warte_ms"), 1, 10_000) or 500
                await self._schlaf(warte / 1000)
            elif art == "pausiert":
                if not pausiert:
                    pausiert = True
                    grund = antwort.get("grund") if antwort.get("grund") in TEXT_PAUSIERT else "geschuetzt"
                    await self._melden("pausiert", grund=grund, text=TEXT_PAUSIERT[grund])
                await self._schlaf(PAUSE_SECS)
            elif art in ("locked", "belegt"):
                return TEXT_ENDE[art]
            elif art == "aus":
                # Feste Texte nach Grundcode: Text vom Laptop gelangt nie in die Meldung (und damit nie ins Sprachmodell)
                return TEXT_AUS.get(antwort.get("grund"), TEXT_AUS["schalter_aus"]) + " Das Live-Bild ist beendet."
            else:
                fehler += 1
            if fehler >= FEHLER_MAX:
                return "Der Laptop liefert gerade kein Bild. Das Live-Bild ist beendet."
            if art not in ("success", "langsamer", "pausiert"):
                await self._schlaf(PAUSE_SECS)

    async def _laeuft_melden(self, monitore_gemeldet: int, monitore: int, rest: int) -> None:
        erster = not self._laeuft_gemeldet
        self._laeuft_gemeldet = True
        await self._melden("laeuft", monitore=max(monitore, monitore_gemeldet), rest_s=rest)
        if erster:
            await self._sicher(self._ansage("Das Live-Bild deines Laptops läuft. Am Laptop ist der rote Rahmen zu sehen."))

    async def _melden(self, zustand: str, **zusatz) -> None:
        self.zustand = zustand
        nachricht = {"type": "bildstrom", "zustand": zustand, "monitor": self._monitor}
        nachricht.update({k: v for k, v in zusatz.items() if v is not None})
        await self._sicher(self._senden(nachricht))

    async def _ende(self, grund: str, melden: bool) -> None:
        """Gemeinsames Ende: Zuschauerplatz im Host freigeben, der Oberfläche und dem Gespräch Bescheid geben."""
        zuschauer, self._zuschauer = self._zuschauer, None
        war_gemeldet = self._laeuft_gemeldet
        self._laeuft_gemeldet = False
        self._ausstehend = False
        self.zustand = "aus"
        client = self._client()
        if zuschauer and client is not None:
            await self._sicher(self._aufruf(client, "live_bild_beenden", {"zuschauer": zuschauer}))
        if melden:
            await self._sicher(self._senden({"type": "bildstrom", "zustand": "aus", "grund": "ende", "text": grund[:300]}))
            if war_gemeldet:
                await self._sicher(self._ansage(grund if len(grund) < 160 else "Das Live-Bild ist beendet."))

    @staticmethod
    async def _sicher(aufgabe: Awaitable) -> None:
        try:
            await aufgabe
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 (eine Meldung darf das Ende nie verhindern)
            logger.debug("Live-Bild: Meldung nicht zugestellt ({})", type(e).__name__)
