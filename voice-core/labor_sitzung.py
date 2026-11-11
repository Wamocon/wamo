"""Die Laborsitzung als ein Objekt: Annahme (Durchlauf und Einwilligung), Speicher, Wache, Prompt und die Prüfung der Werkzeuge.

bot.build_session ruft für ein Laborprofil nur LaborSitzung.starten auf und hängt den Rest an wenigen Stellen ein. Alles, was über den
Zweig entscheidet, steht in labor.py und hier, nicht in bot.py.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

import labor
import labor_durchlauf
from labor_durchlauf import Durchlauf, DurchlaufQuelle, LaborWache
from labor_prompt import build_labor_prompt
from labor_speicher import LaborSpeicher


@dataclass
class LaborSitzung:
    identity: Any
    stand: Durchlauf | None
    memory: LaborSpeicher
    wache: LaborWache | None
    # Hier hängt die Labor-Freigabe (LaborFreigaben, Strang K2) ihren Entscheider ein: bekommt die Daten der Client-Nachricht freigabe
    # mit job labor, nachdem labor.client_nachricht_erlaubt sie zugelassen hat. Ohne Eintrag bleibt der Klick wirkungslos.
    freigabe: Callable[[dict], Awaitable[None]] | None = None

    @classmethod
    async def starten(cls, identity: Any, *, quelle: DurchlaufQuelle | None = None, min_version: int | None = None,
                      db: Any = None) -> LaborSitzung:
        """Nimmt eine Laborsitzung an oder wirft LaborAbgelehnt (Durchlauf beendet, keine Einwilligung, Dienst nicht erreichbar)."""
        if not labor.ist_labor(identity):
            raise labor.LaborPolitikFehler("LaborSitzung.starten ohne Laborprofil")
        urteil = await labor_durchlauf.pruefen(identity, quelle=quelle, min_version=min_version)
        if urteil.status != 200:
            raise labor.LaborAbgelehnt(urteil.grund, urteil.status, urteil.code)
        speicher = LaborSpeicher.fuer(identity, urteil.stand, db=db, min_version=min_version)
        wache = (LaborWache(identity.labor_platz, urteil.stand, quelle=quelle, min_version=min_version)
                 if urteil.stand is not None else None)
        logger.info("Labor: Sitzung {} Platz {} Durchlauf {} Text speichern: {}", identity.labor_art, identity.labor_platz,
                    urteil.stand.durchlauf_id[:8] if urteil.stand else "-", "ja" if speicher.enabled else "nein")
        return cls(identity, urteil.stand, speicher, wache)

    def prompt(self) -> str:
        """Der Prompt dieser Sitzung: immer der Laborprompt, nie der normale mit Gedächtnis und PC."""
        return build_labor_prompt(self.identity.name, self.identity.anrede, self.identity.labor_art)

    def werkzeuge_pruefen(self, llm: Any, standard_tools: list) -> None:
        """Die Namen im Kontext und die am LLM registrierten Namen müssen der Erlaubnisliste entsprechen, sonst bricht die Sitzung ab."""
        labor.werkzeuge_pruefen(self.identity, [s.name for s in standard_tools])
        labor.werkzeuge_pruefen(self.identity, labor.registrierte_namen(llm))

    async def client_nachricht(self, typ: str, data: Any) -> bool:
        """Schicht a der Client-Nachrichten. True, wenn sie an die Labor-Freigabe ging."""
        if not labor.client_nachricht_erlaubt(self.identity.labor_art, typ, data):
            logger.warning("Labor: Client-Nachricht {!r} ohne Wirkung", typ if isinstance(typ, str) else type(typ).__name__)
            return False
        if self.freigabe is None:
            logger.warning("Labor: Freigabe ohne Entscheider in dieser Sitzung ohne Wirkung")
            return False
        await self.freigabe(data)
        return True
