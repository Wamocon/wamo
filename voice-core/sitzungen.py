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
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger

from pc_host import pc_lease_freigeben, pc_lease_haelt, pc_lease_nehmen

GRUND_UEBERNOMMEN = "Eine neue Sitzung dieses Profils hat übernommen."
MELDUNG_VORLAUF_SECS = 0.5  # so lange darf die Meldung an die alte Oberfläche vor dem Trennen laufen
TRENNEN_TIMEOUT_SECS = 5.0
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

    def pc_lease_nehmen(self) -> bool:
        """PC-Sperre nehmen; eine abgelöste Sitzung bekommt sie nie wieder."""
        return not self.abgeloest and pc_lease_nehmen(self.lease)

    def pc_lease_haelt(self) -> bool:
        """Darf diese Sitzung den PC gerade steuern? Abgelöst nie, sonst laut pc_host (lokal immer)."""
        return not self.abgeloest and pc_lease_haelt(self.lease)

    def pc_lease_freigeben(self) -> None:
        pc_lease_freigeben(self.lease)


class Sitzungen:
    """Registry je user_id (eine Instanz je Prozess, siehe bot.py)."""

    def __init__(
        self,
        *,
        einzeln: bool | None = None,
        meldung_vorlauf_secs: float = MELDUNG_VORLAUF_SECS,
        trennen_timeout_secs: float = TRENNEN_TIMEOUT_SECS,
    ):
        self._einzeln = einzeln
        self._vorlauf = meldung_vorlauf_secs
        self._timeout = trennen_timeout_secs
        self._aktiv: dict[str, Sitzung] = {}

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
        if alt is None or alt is neu:
            return None
        alt.abgeloest = True
        alt.pc_lease_freigeben()  # die Sperre ist frei, bevor die alte getrennt ist
        logger.info("Neue Sitzung für Profil {}: die laufende wird beendet", neu.user_id)
        # Abgeschirmt: auch wenn die neue Sitzung abbricht, wird die alte zu Ende abgelöst und getrennt
        await asyncio.shield(self._abloesen(alt))
        return alt

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
