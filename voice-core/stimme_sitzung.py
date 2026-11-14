"""Stimmabdruck einer Sitzung: Laden, Client-Nachrichten, Einwilligung, Löschen. Der Klebstoff zwischen bot.py und den Teilen der Stimmprüfung.

Eine StimmSitzung gehört genau einer Sitzung, also einem Profil: eigenes Gate (Abdruck, Prüfzustand), eigener Prüfer und Wächter in der Pipeline.
Der Rechner (stimme_modell.MODELL) ist je Prozess einmal da und wird von allen Sitzungen geteilt; einen Abdruck-Zwischenspeicher über Profile hinweg
gibt es nicht. Laborprofile (labor.py) bekommen nie eine StimmSitzung (fuer gibt None), die Client-Nachricht stimmabdruck ist für sie zusätzlich
von der Laborsperre abgewiesen (labor_rtvi.py, labor.client_nachricht_erlaubt).

Ablauf in bot.py: fuer (beim Aufbau der Sitzung) -> werkzeuge_absichern und tippen_beobachten (stimme_gate.py) -> pruefer vor die Spracherkennung,
waechter vor den Aggregator des Nutzers -> vorbereiten (im Hintergrund nach dem Verbinden) -> client_bereit (Status und Einwilligung an die
Oberfläche) -> nachricht (Client-Nachricht stimmabdruck) -> zug_verworfen (in on_user_turn_stopped).

Nachricht der Oberfläche: {"aktion": "start", "einwilligung": "stimme-1"} | {"aktion": "abbrechen"} | {"aktion": "loeschen"}. Start nur mit genau der
aktuellen Version der Einwilligung (stimme_einwilligung.py), nur auf Klick, nie per Sprache. Löschen geht immer (auch im Modus aus) und entfernt
den Abdruck vollständig (DSGVO Art. 17); ein laufendes Einlernen wird vorher beendet, damit danach nichts mehr gespeichert wird.
Ohne Datenbank oder im Modus aus ist die Stimmprüfung aus (Status aus); ohne Abdruck wird nie geprüft oder gesperrt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import numpy as np
from loguru import logger

import labor
from memory import MemoryStore
from stimme_einwilligung import einwilligung_gilt, einwilligung_nachricht
from stimme_gate import StimmGate
from stimme_modell import MODELL
from stimme_pruefer import Stimmpruefer
from stimme_speicher import abdruck_laden, abdruck_loeschen, abdruck_speichern
from stimme_waechter import StimmWaechter

LADE_VERSUCHE = 3  # der Abdruck wird beim Start der Sitzung gelesen; ein kurzer Ausfall der Datenbank soll nicht gleich "kaputt" heißen
LADE_PAUSE_SECS = 0.5
EINWILLIGUNG_FEHLT = "Die Einwilligung fehlt oder gehört zu einem älteren Text. Bitte lies sie noch einmal und bestätige sie."
ABGESCHALTET = "Die Stimmerkennung ist abgeschaltet."
STAND_UNKLAR = "Ich kann gerade nicht lesen, ob schon ein Stimmabdruck gespeichert ist. Bitte versuch es gleich noch einmal."
NICHT_GELOESCHT = "Ich konnte den Stimmabdruck nicht löschen. Bitte versuch es später noch einmal."
SPRECHER_UNSICHER = "stimme-unsicher"  # Markierung (Spalte speaker_label) im Gesprächsprotokoll: nicht sicher die Stimme des Profils


def nachricht_aus() -> dict:
    """Stand für die Oberfläche, wo es keine Stimmprüfung gibt (Laborprofile): die Bedienelemente bleiben weg."""
    return {"type": "stimme", "status": "aus", "modus": "aus"}


def jetzt_utc() -> datetime:
    return datetime.now(timezone.utc)


class StimmSitzung:
    def __init__(
        self,
        identity,
        memory: MemoryStore,
        gate: StimmGate,
        modell,
        *,
        senden: Callable[[dict], Awaitable[None]],
        sagen: Callable[[str], Awaitable[None]],
        uhr: Callable[[], float] | None = None,
    ):
        self._identity = identity
        self._memory = memory
        self._modell = modell
        self._senden = senden
        self.gate = gate
        if not memory.enabled and gate.modus != "aus":
            logger.info("Stimmprüfung aus: keine Datenbank für das Profil")
            gate.modus = "aus"  # ohne Datenbank gibt es keinen Abdruck, nichts zu prüfen und nichts einzulernen
        kwargs = {"uhr": uhr} if uhr is not None else {}
        self.pruefer = Stimmpruefer(gate, modell, speichern=self._speichern, sagen=sagen, **kwargs)
        self.waechter = StimmWaechter(gate, self.pruefer)

    @classmethod
    def fuer(
        cls,
        identity,
        memory: MemoryStore,
        *,
        senden: Callable[[dict], Awaitable[None]],
        sagen: Callable[[str], Awaitable[None]],
        modell=MODELL,
        gate: StimmGate | None = None,
        uhr: Callable[[], float] | None = None,
    ) -> StimmSitzung | None:
        """Die Stimmsitzung dieser Identität, oder None für ein Laborprofil: dort gibt es nie Stimmprüfung und nie Einlernen."""
        if labor.ist_labor(identity):
            return None
        return cls(identity, memory, gate or StimmGate.aus_umgebung(), modell, senden=senden, sagen=sagen, uhr=uhr)

    # ---------- Laden und Stand ----------

    async def vorbereiten(self) -> None:
        """Abdruck des Profils lesen und, wenn es einen gibt, das Modell laden. Läuft im Hintergrund, das Gespräch wartet nicht. Wirft nie."""
        try:
            if self.gate.modus != "aus":
                await self._laden()
        except Exception as e:  # unbekannter Stand heißt kaputt: streng sperrt dann Werkzeuge, getippter Text geht
            logger.warning("Stimmprüfung: Vorbereitung gescheitert ({})", type(e).__name__)
            self.gate.setze_abdruck(None, ok=False)
        logger.info("Stimmprüfung: {} ({})", self.gate.status, self.gate.modus)
        await self.status_melden()

    async def _laden(self) -> None:
        ok, abdruck = False, None
        for versuch in range(LADE_VERSUCHE):
            ok, abdruck = await abdruck_laden(self._memory)
            if ok:
                break
            if versuch + 1 < LADE_VERSUCHE:
                await asyncio.sleep(LADE_PAUSE_SECS * (versuch + 1))
        self.gate.setze_abdruck(abdruck, ok)
        if abdruck is not None:
            self.gate.modell_ok = await self._modell.get() is not None

    async def status_melden(self) -> None:
        await self._senden({"type": "stimme", "status": self.gate.status, "modus": self.gate.modus})

    async def client_bereit(self) -> None:
        """Die Oberfläche ist verbunden: Stand der Stimmprüfung und der Text der Einwilligung."""
        await self.status_melden()
        if self.gate.modus != "aus":
            await self._senden(einwilligung_nachricht())

    def zug_verworfen(self) -> bool:
        return self.waechter.zug_verworfen()

    def nicht_sicher_eigen(self) -> bool:
        """Der Zug stammt nicht sicher von der Person (stimme_gate.StimmGate.nicht_sicher_eigen): bot.py färbt ihn wie Fremdinhalte."""
        return self.gate.nicht_sicher_eigen()

    # ---------- Client-Nachricht stimmabdruck ----------

    async def nachricht(self, daten: object) -> None:
        if not isinstance(daten, dict):
            return
        aktion = daten.get("aktion")
        if aktion == "start":
            await self._start(daten.get("einwilligung"))
        elif aktion == "abbrechen":
            await self.pruefer.einlernen_abbrechen()
        elif aktion == "loeschen":
            await self._loeschen()
        else:
            logger.warning("Stimmabdruck: unbekannte Aktion {!r}", str(aktion)[:40])

    async def _fehler(self, text: str) -> None:
        await self._senden({"type": "stimmabdruck", "status": "fehler", "text": text})

    async def _start(self, einwilligung: object) -> None:
        if self.gate.modus == "aus":
            await self._fehler(ABGESCHALTET)
        elif not einwilligung_gilt(einwilligung):
            await self._fehler(EINWILLIGUNG_FEHLT)
        elif self.gate.zustand in ("laedt", "kaputt"):
            await self._fehler(STAND_UNKLAR)  # nicht blind einen vielleicht vorhandenen Abdruck ersetzen
        else:
            await self.pruefer.einlernen_starten(einwilligung, jetzt_utc())

    async def _speichern(self, abdruck: np.ndarray, version: str, einwilligung_am: datetime) -> bool:
        return await abdruck_speichern(
            self._memory, abdruck, version=version, name=getattr(self._identity, "name", ""), einwilligung_am=einwilligung_am
        )

    async def _loeschen(self) -> None:
        # Erst das Einlernen beenden und ein schon laufendes Speichern abwarten, sonst käme der Abdruck nach dem Löschen wieder
        await self.pruefer.einlernen_abbrechen()
        await self.pruefer.einlernen_warten()
        if not await abdruck_loeschen(self._memory):
            await self._fehler(NICHT_GELOESCHT)
            return
        self.gate.setze_abdruck(None)
        await self._senden({"type": "stimmabdruck", "status": "geloescht"})
        await self.status_melden()
