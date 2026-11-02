"""Eine Sitzung je Profil: eine neue Sitzung desselben Profils löst die laufende ab.

Warum: Lädt die Oberfläche neu, wechselt die Person das Gerät oder bleibt ein zweiter Tab offen, lief die alte
WebRTC-Verbindung unsichtbar weiter. Zwei Pipelines desselben Profils sprächen dann gleichzeitig, und im Büro hielte
die alte Sitzung die PC-Sperre (pc_host.pc_lease_nehmen), sodass die neue den PC nicht steuern könnte.

Regeln:
  * Je user_id höchstens eine Sitzung, nicht global: der Büro-Kern bedient mehrere Profile gleichzeitig.
  * Die neue gewinnt. Die alte wird zuerst als abgelöst markiert und verliert die PC-Sperre (danach lehnt sie
    PC-Werkzeuge, Freigaben und erneutes Einschalten selbst ab), bekommt dann eine Meldung an ihre Oberfläche
    (abloesen, Typ sitzung_beendet) und wird getrennt (webrtc_connection.disconnect()). Fehler und Hänger der alten
    Verbindung werden toleriert und blockieren die neue nie.
  * Freigeben (Ende einer Sitzung) wirkt nur, wenn keine neuere Sitzung übernommen hat.
  * Schalter WAMO_SITZUNG_EINZELN (Standard an; aus, off, 0, false, nein schalten ab).
  * Zwei Kern-Instanzen (Slots, Schritt 2.3): Mit WAMO_SLOT legt jede Instanz für ihre Sitzungen Dateien an (sitzungen_bruecke.py). Eine neue
    Sitzung bittet die andere Instanz, die Sitzung desselben Profils abzulösen, und wartet höchstens kurz darauf; die andere gibt vorher die
    PC-Sperre frei. Ohne WAMO_SLOT ändert sich nichts.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger

from pc_host import pc_lease_freigeben, pc_lease_haelt, pc_lease_nehmen
from sitzungen_bruecke import Bruecke

GRUND_UEBERNOMMEN = "Eine neue Sitzung dieses Profils hat übernommen."
MELDUNG_VORLAUF_SECS = 0.5  # so lange darf die Meldung an die alte Oberfläche vor dem Trennen laufen
TRENNEN_TIMEOUT_SECS = 5.0
BRUECKE_WARTE_SECS = 5.0  # so lange wartet eine neue Sitzung auf die Ablösung durch die andere Instanz (Konzept A.3), danach läuft sie trotzdem an
BRUECKE_TAKT_SECS = 1.0  # so oft schaut eine Instanz nach Marken der anderen
AUS = ("aus", "off", "0", "false", "nein")


def einzeln_aktiv() -> bool:
    """WAMO_SITZUNG_EINZELN: Standard an."""
    return (os.getenv("WAMO_SITZUNG_EINZELN") or "an").strip().lower() not in AUS


@dataclass(eq=False)
class Sitzung:
    """Eine laufende Sitzung. lease ist ihre Kennung für die PC-Sperre; abloesen meldet der Oberfläche das Ende."""

    user_id: str
    verbindung: object | None = None  # SmallWebRTCConnection (disconnect()) oder None (Tests)
    lease: object = field(default_factory=object)
    abloesen: Callable[[str], Awaitable[None]] | None = None
    abgeloest: bool = False
    geraet_id: str | None = None  # Tunnel je Gerät: die Sperre gilt je Gerät (bot.build_session setzt es früh)

    def pc_lease_nehmen(self) -> bool:
        """PC-Sperre nehmen; eine abgelöste Sitzung bekommt sie nie wieder."""
        return not self.abgeloest and pc_lease_nehmen(self.lease, self.geraet_id)

    def pc_lease_haelt(self) -> bool:
        """Darf diese Sitzung den PC gerade steuern? Abgelöst nie, sonst laut pc_host (lokal immer)."""
        return not self.abgeloest and pc_lease_haelt(self.lease, self.geraet_id)

    def pc_lease_freigeben(self) -> None:
        pc_lease_freigeben(self.lease, self.geraet_id)


class Sitzungen:
    """Registry je user_id (eine Instanz je Prozess, siehe bot.py)."""

    def __init__(
        self,
        *,
        einzeln: bool | None = None,
        meldung_vorlauf_secs: float = MELDUNG_VORLAUF_SECS,
        trennen_timeout_secs: float = TRENNEN_TIMEOUT_SECS,
        bruecke: Bruecke | None = None,
        bruecke_warte_secs: float = BRUECKE_WARTE_SECS,
        bruecke_takt_secs: float = BRUECKE_TAKT_SECS,
    ):
        self._einzeln = einzeln
        self._vorlauf = meldung_vorlauf_secs
        self._timeout = trennen_timeout_secs
        self._aktiv: dict[str, Sitzung] = {}
        # Brücke zur anderen Kern-Instanz (nur mit WAMO_SLOT, sonst aus): beim Start liegen gebliebene Dateien einer früheren Instanz löschen
        self._bruecke = bruecke if bruecke is not None else Bruecke.aus_umgebung()
        self._bruecke.aufraeumen()
        self._bruecke_warte = bruecke_warte_secs
        self._takt = bruecke_takt_secs
        self._beobachter: asyncio.Task | None = None

    @property
    def einzeln(self) -> bool:
        return einzeln_aktiv() if self._einzeln is None else self._einzeln

    def aktiv(self, user_id: str) -> Sitzung | None:
        return self._aktiv.get(user_id)

    async def uebernehmen(self, neu: Sitzung) -> Sitzung | None:
        """Meldet die neue Sitzung an und löst eine laufende desselben Profils ab. Liefert die abgelöste oder None."""
        if not self.einzeln:
            return None
        alt = self._aktiv.get(neu.user_id)
        self._aktiv[neu.user_id] = neu  # sofort, vor jedem await: zwei neue Sitzungen überholen sich nicht
        if self._bruecke.aktiv:
            self._bruecke.eintragen(neu.user_id)
            self._beobachter_starten()
        abgeloest = None
        if alt is not None and alt is not neu:
            alt.abgeloest = True
            alt.pc_lease_freigeben()  # die Sperre ist frei, bevor die alte getrennt ist
            logger.info("Neue Sitzung für Profil {}: die laufende wird beendet", neu.user_id)
            # Abgeschirmt: auch wenn die neue Sitzung abbricht, wird die alte zu Ende abgelöst und getrennt
            await asyncio.shield(self._abloesen(alt))
            abgeloest = alt
        if self._bruecke.aktiv:
            await self._andere_abloesen(neu.user_id)
        return abgeloest

    async def _andere_abloesen(self, user_id: str) -> None:
        """Hat die andere Kern-Instanz eine Sitzung dieses Profils, bittet die Brücke sie um die Ablösung und wartet höchstens kurz darauf.
        Die Marke bleibt liegen, wenn die Ablösung länger dauert: die andere Instanz führt sie dann trotzdem noch aus (Marken verfallen nach 60 s)."""
        if not self._bruecke.andere_hat(user_id):
            return
        logger.info("Neue Sitzung für Profil {}: die Sitzung der anderen Instanz wird abgelöst", user_id)
        self._bruecke.marke_setzen(user_id)
        loop = asyncio.get_running_loop()
        ende = loop.time() + self._bruecke_warte
        while self._bruecke.andere_hat(user_id) and loop.time() < ende:
            await asyncio.sleep(0.1)
        if self._bruecke.andere_hat(user_id):
            logger.warning("Die andere Instanz hat die Sitzung nicht innerhalb von {} s abgelöst: die neue läuft trotzdem an", self._bruecke_warte)

    def _beobachter_starten(self) -> None:
        if self._beobachter is None or self._beobachter.done():
            self._beobachter = asyncio.get_running_loop().create_task(self._beobachten())

    async def _beobachten(self) -> None:
        """Solange Sitzungen laufen: jede Sekunde nach Marken der anderen Instanz schauen und die genannte Sitzung ablösen."""
        while self._aktiv:
            await asyncio.sleep(self._takt)
            for user_id in self._bruecke.marken_fuer_mich():
                try:
                    await self._abgeben(user_id)
                except Exception as e:  # noqa: BLE001 (eine Panne bei einem Profil darf die Beobachtung der anderen nicht beenden)
                    logger.warning("Ablösung für die andere Instanz fehlgeschlagen ({})", type(e).__name__)

    async def _abgeben(self, user_id: str) -> None:
        """Die andere Instanz hat für dieses Profil übernommen: die Sitzung hier ablösen, dann Datei und Marke löschen."""
        sitzung = self._aktiv.get(user_id)
        if sitzung is not None and not sitzung.abgeloest:
            del self._aktiv[user_id]
            sitzung.abgeloest = True
            sitzung.pc_lease_freigeben()  # die Sperre ist frei, bevor die Sitzung getrennt ist
            logger.info("Die andere Instanz hat für Profil {} übernommen: die Sitzung hier wird beendet", user_id)
            await asyncio.shield(self._abloesen(sitzung))
        self._bruecke.austragen(user_id)
        self._bruecke.marke_loeschen(user_id, fuer=self._bruecke.slot)

    def anzahl(self) -> int:
        """Zahl der laufenden Sitzungen dieser Instanz."""
        return len(self._aktiv)

    async def _abloesen(self, alt: Sitzung) -> None:
        """Meldung an die alte Oberfläche, dann trennen. Baut die alte noch auf (abloesen fehlt), genügt das Trennen:
        bot.run_bot startet eine abgelöste Sitzung nicht mehr."""
        if alt.abloesen is not None:
            try:
                await asyncio.wait_for(alt.abloesen(GRUND_UEBERNOMMEN), self._timeout)
                if self._vorlauf:
                    await asyncio.sleep(self._vorlauf)
            except Exception as e:  # noqa: BLE001 (die alte Pipeline kann schon halb weg sein)
                logger.warning("Alte Sitzung ließ sich nicht benachrichtigen ({})", type(e).__name__)
        if alt.verbindung is not None:
            try:
                await asyncio.wait_for(alt.verbindung.disconnect(), self._timeout)
            except Exception as e:  # noqa: BLE001 (Verbindung kann schon weg sein oder hängen)
                logger.warning("Alte Sitzung ließ sich nicht sauber trennen ({})", type(e).__name__)

    def freigeben(self, sitzung: Sitzung) -> None:
        """Sitzung beendet: austragen, falls nicht schon eine neuere übernommen hat."""
        if self._aktiv.get(sitzung.user_id) is sitzung:
            del self._aktiv[sitzung.user_id]
            self._bruecke.austragen(sitzung.user_id)
