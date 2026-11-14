"""Stimmabdruck einlernen: 20 Sekunden reine Sprache aufnehmen, in Stücke von 3 s teilen, den Mittelwert als Abdruck speichern.

Das Einlernen beginnt nur auf Klick in der angemeldeten Oberfläche (Client-Nachricht stimmabdruck mit aktion start und gültiger Einwilligung,
siehe stimme_sitzung.py), nie per Sprache: sonst könnte eine fremde Stimme den Abdruck ersetzen. Gibt es schon einen Abdruck (oder gab es in
dieser Sitzung einen), braucht es außer im Modus aus einen Nachweis: die Person ist gerade an der Stimme erkannt oder frisch angemeldet. Während der Aufnahme hört die Spracherkennung nur Stille (der Aufrufer ersetzt den Ton,
wenn sammeln True meldet), WAMO antwortet also nicht auf den Vorlesetext.

Ablauf: starten (Modell laden, Anleitung sprechen) -> sammeln je Tonpaket bis ZIEL_SECS reine Sprache -> rechnen (Stücke, Mittelwert,
Streuung) -> speichern -> Abdruck und Vertrauen im Gate setzen. Abbrechen geht jederzeit, auch während des Rechnens (dann wird nichts
gespeichert). Der Rohton liegt nur im Arbeitsspeicher und wird nach dem Rechnen verworfen.

Meldungen an die Oberfläche (über senden): {"type": "stimmabdruck", "status": start | aufnahme | rechnet | fertig | fehler | abgebrochen,
"sekunden", "ziel", "text"} und {"type": "stimme", "status", "modus"} beim Laden des Modells und nach dem Einlernen.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from loguru import logger

from stimme_gate import EIGEN, StimmGate
from stimme_modell import MIN_STUECKE, SPRACHE_DBFS, dbfs, einlern_stuecke, mittelpunkt, sprachbloecke

ZIEL_SECS = 20.0  # so viel reine Sprache für den Abdruck
MAX_SECS = 90.0
MIN_RATE, MAX_RATE = 8000, 48000  # Abtastraten, die das Einlernen annimmt (alles andere ist kein Mikrofon)
MAX_BYTES = int(MAX_SECS * MAX_RATE) * 2  # Obergrenze des Aufnahmepuffers im Arbeitsspeicher
WARTE_SECS = 8.0  # spricht WAMO die Anleitung nicht, beginnt die Aufnahme trotzdem
MIN_STREUUNG = 0.55  # die Stücke liegen im Schnitt so nah am Mittel, sonst war die Aufnahme zu unruhig
MELDE_SECS = 0.5
ABBRUCH_WARTE_SECS = 10.0  # so lange wartet das Abbrechen auf ein schon laufendes Speichern
ANLEITUNG = (
    "Okay, ich lerne jetzt deine Stimme. Lies den Text auf dem Bildschirm in Ruhe vor oder erzähl einfach etwas, "
    "bis der Balken voll ist."
)
FERTIG_TEXT = "Danke, jetzt kenne ich deine Stimme."
ZU_WENIG = "Ich habe zu wenig von dir gehört. Versuch es bitte noch einmal."
ZU_UNRUHIG = "Die Aufnahme war zu unruhig. Bitte noch einmal, am besten ohne andere Stimmen im Raum."
KEIN_MODELL = "Das Modell für die Stimmerkennung ist gerade nicht verfügbar. Bitte versuch es später noch einmal."
KEINE_ABTASTRATE = "Das Mikrofon liefert eine unbekannte Abtastrate. Ich kann deine Stimme so nicht einlernen."
NICHT_GESPEICHERT = "Ich konnte den Stimmabdruck nicht speichern. Bitte versuch es später noch einmal."
NICHT_GEKLAPPT = "Das Einlernen hat nicht geklappt. Bitte versuch es später noch einmal."
ERST_ERKENNEN = (
    "Zum Ersetzen deines Stimmabdrucks muss ich dich erst an der Stimme erkennen: Sag kurz etwas in einem ganzen Satz. "
    "Oder bitte neu anmelden, mit zweitem Faktor, dann geht es auch."
)

Speichern = Callable[[np.ndarray, str, datetime], Awaitable[bool]]


@dataclass
class _Lauf:
    gestartet: float
    version: str
    am: datetime
    wartet: bool = True  # bis WAMO die Anleitung fertig gesprochen hat
    bot_begann: bool = False
    audio: bytearray = field(default_factory=bytearray)
    sprache_secs: float = 0.0
    gemeldet: float = 0.0


class Einlernen:
    """Das Einlernen einer Sitzung. Alles läuft auf der Ereignisschleife; nur das Rechnen der Abdrücke geht in einen Thread."""

    def __init__(
        self,
        gate: StimmGate,
        modell,
        *,
        senden: Callable[[dict], Awaitable[None]],
        sagen: Callable[[str], Awaitable[None]],
        speichern: Speichern,
        uhr: Callable[[], float] = time.time,
        nachweis: Callable[[], bool] | None = None,
    ):
        self._gate = gate
        self._nachweis = nachweis or gate.stimme_vertraut  # wer einen Abdruck ersetzt, muss das nachweisen (stimme_sitzung.py)
        self._modell = modell
        self._senden = senden
        self._sagen = sagen
        self._speichern = speichern
        self._uhr = uhr  # Wanduhr wie VADUser...SpeakingFrame.timestamp
        self._lauf: _Lauf | None = None
        self._rechnet = False
        self._laedt = False
        self._bot_spricht = False
        self._gen = 0  # zählt Abbrüche: ein Start, der noch auf das Modell wartet, merkt so, dass er abgebrochen wurde
        self._task: asyncio.Task | None = None

    @property
    def aktiv(self) -> bool:
        """Die Aufnahme läuft (oder wartet auf das Ende der Anleitung)."""
        return self._lauf is not None

    @property
    def beschaeftigt(self) -> bool:
        return self._lauf is not None or self._rechnet or self._laedt

    async def warten(self) -> None:
        """Wartet auf ein laufendes Rechnen und Speichern (für Tests und das Löschen)."""
        task = self._task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({task}, timeout=ABBRUCH_WARTE_SECS)

    # ---------- Start und Abbruch ----------

    async def starten(self, version: str, einwilligung_am: datetime) -> None:
        if self.beschaeftigt:
            return
        if self._gate.abdruck_geschuetzt and self._gate.modus != "aus" and not self._nachweis():
            # Ersetzen (auch nach dem Löschen in dieser Sitzung) nur mit Nachweis: die Person ist gerade an der Stimme erkannt oder
            # frisch mit zweitem Faktor angemeldet. Wer nur an der angemeldeten Oberfläche sitzt, soll den Abdruck nicht umschreiben können.
            await self._fehler(ERST_ERKENNEN)
            return
        gen = self._gen
        self._laedt = True
        try:
            if not self._modell.bereit:
                await self._senden({"type": "stimme", "status": "laedt", "modus": self._gate.modus})
            rechner = await self._modell.get()
        finally:
            self._laedt = False
        if gen != self._gen:
            return  # abgebrochen, während das Modell lud
        self._gate.modell_ok = rechner is not None
        if rechner is None:
            await self._fehler(KEIN_MODELL)
            await self._status_melden()
            return
        if self._gate.hat_abdruck:
            logger.warning("Stimmabdruck: vorhandener Abdruck wird neu eingelernt")
        self._lauf = _Lauf(gestartet=self._uhr(), version=version, am=einwilligung_am)
        logger.info("Stimmabdruck: Einlernen beginnt")
        await self._senden({"type": "stimmabdruck", "status": "start", "sekunden": 0, "ziel": ZIEL_SECS})
        await self._sagen(ANLEITUNG)

    async def abbrechen(self) -> None:
        if not self.beschaeftigt:
            return
        self._gen += 1  # ein Lauf, der schon rechnet oder speichert, merkt es am Zähler
        beim_rechnen = self._rechnet
        self._lauf = None
        logger.info("Stimmabdruck: Einlernen abgebrochen")
        await self._senden({"type": "stimmabdruck", "status": "abgebrochen"})
        if beim_rechnen:
            await self.warten()

    async def aufraeumen(self) -> None:
        """Die Sitzung endet: Aufnahme verwerfen, ein laufendes Rechnen abbrechen und abwarten. Danach läuft nichts mehr weiter und es wird
        nichts mehr gespeichert (ein Rechnen im Thread läuft noch zu Ende, sein Ergebnis kommt aber nirgends mehr an)."""
        self._gen += 1
        self._lauf = None
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._rechnet = False

    def bot_spricht(self, spricht: bool) -> None:
        self._bot_spricht = spricht
        lauf = self._lauf
        if lauf is None:
            return
        if spricht:
            lauf.bot_begann = True
        elif lauf.bot_begann:
            lauf.wartet = False  # die Anleitung ist vorbei, jetzt aufnehmen

    # ---------- Aufnahme ----------

    async def sammeln(self, pcm: bytes, rate: int) -> bool:
        """Nimmt ein Tonpaket auf. True: Einlernen war aktiv, der Aufrufer ersetzt den Ton durch Stille."""
        lauf = self._lauf
        if lauf is None:
            return False
        if not MIN_RATE <= rate <= MAX_RATE:
            self._lauf = None
            await self._fehler(KEINE_ABTASTRATE)
            return True
        jetzt = self._uhr()
        if jetzt - lauf.gestartet > MAX_SECS:  # zuerst: auch wenn das Ende der Anleitung nie gemeldet wurde
            self._lauf = None
            await self._fehler(ZU_WENIG)
            return True
        if lauf.wartet:
            # Die Anleitung soll nicht im Abdruck landen; kommt sie nicht, trotzdem anfangen
            if jetzt - lauf.gestartet < WARTE_SECS or self._bot_spricht:
                return True
            lauf.wartet = False
        if len(lauf.audio) + len(pcm) > MAX_BYTES:  # der Puffer wächst nie über die Obergrenze, auch nicht bei vielen kleinen Paketen
            self._lauf = None
            await self._fehler(ZU_WENIG)
            return True
        lauf.audio.extend(pcm)
        if dbfs(np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16)) > SPRACHE_DBFS:
            lauf.sprache_secs += len(pcm) / 2 / rate if rate else 0.0
        if lauf.sprache_secs >= ZIEL_SECS:
            await self._abschliessen(lauf, rate)
        elif jetzt - lauf.gemeldet >= MELDE_SECS:
            lauf.gemeldet = jetzt
            await self._senden({"type": "stimmabdruck", "status": "aufnahme", "sekunden": round(lauf.sprache_secs, 1), "ziel": ZIEL_SECS})
        return True

    async def _abschliessen(self, lauf: _Lauf, rate: int) -> None:
        self._lauf = None
        self._rechnet = True
        await self._senden({"type": "stimmabdruck", "status": "rechnet", "sekunden": ZIEL_SECS, "ziel": ZIEL_SECS})
        self._task = asyncio.create_task(self._fertig(bytes(lauf.audio), rate, lauf.version, lauf.am, self._gen))

    # ---------- Rechnen und Speichern ----------

    async def _fertig(self, pcm: bytes, rate: int, version: str, am: datetime, gen: int) -> None:
        try:
            await self._rechnen_und_speichern(pcm, rate, version, am, gen)
        except Exception as e:
            logger.exception("Stimmabdruck: Einlernen fehlgeschlagen: {}", e)
            await self._fehler(NICHT_GEKLAPPT)
        finally:
            self._rechnet = False

    async def _rechnen_und_speichern(self, pcm: bytes, rate: int, version: str, am: datetime, gen: int) -> None:
        rechner = await self._modell.get()
        if rechner is None:
            await self._fehler(KEIN_MODELL)
            return
        stuecke = einlern_stuecke(sprachbloecke(pcm, rate), rate)
        if len(stuecke) < MIN_STUECKE:
            await self._fehler(ZU_WENIG)
            return
        abdruecke = await asyncio.to_thread(lambda: [rechner.embed(s, rate) for s in stuecke])
        abdruck, streuung = mittelpunkt(abdruecke)
        logger.info("Stimmabdruck: {} Stücke, Streuung {:.2f}", len(stuecke), streuung)
        if streuung < MIN_STREUUNG:
            await self._fehler(ZU_UNRUHIG)
            return
        if gen != self._gen:  # direkt vor dem Speichern: abgebrochen oder gelöscht, dann wird nichts gespeichert
            logger.info("Stimmabdruck: abgebrochen, nichts gespeichert")
            return
        if not await self._speichern(abdruck, version, am):
            await self._fehler(NICHT_GESPEICHERT)
            return
        self._gate.setze_abdruck(abdruck)
        self._gate.modell_ok = True
        if gen != self._gen:
            # Abbruch oder Löschen, während gespeichert wurde: der Abdruck steht schon in der Datenbank. Das Gate gibt den Stand wieder
            # (ein Löschen entfernt ihn danach von außen, siehe stimme_sitzung.py), aber nichts wird als fertig gemeldet.
            logger.info("Stimmabdruck: Abbruch während des Speicherns, der Abdruck ist gespeichert, aber nicht gemeldet")
            await self._status_melden()
            return
        self._gate.eintragen(EIGEN)  # die Person hat gerade per Klick eingelernt und gesprochen
        await self._senden({"type": "stimmabdruck", "status": "fertig", "sekunden": ZIEL_SECS, "ziel": ZIEL_SECS})
        await self._status_melden()
        await self._sagen(FERTIG_TEXT)

    async def _status_melden(self) -> None:
        await self._senden({"type": "stimme", "status": self._gate.status, "modus": self._gate.modus})

    async def _fehler(self, text: str) -> None:
        logger.warning("Stimmabdruck: {}", text)
        await self._senden({"type": "stimmabdruck", "status": "fehler", "text": text})
        await self._sagen(text)
