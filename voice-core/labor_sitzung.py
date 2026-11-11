"""Die Laborsitzung als ein Objekt: Annahme (Durchlauf und Einwilligung), Speicher, Wache, Prompt und die Prüfung der Werkzeuge.

bot.build_session ruft für ein Laborprofil nur LaborSitzung.starten auf und hängt den Rest an wenigen Stellen ein. Alles, was über den
Zweig entscheidet, steht in labor.py und hier, nicht in bot.py.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

import labor
import labor_daten
import labor_durchlauf
from labor_daten import LaborDaten
from labor_durchlauf import Durchlauf, DurchlaufQuelle, LaborWache
from labor_prompt import build_labor_prompt
from labor_speicher import LaborSpeicher


@dataclass
class LaborSitzung:
    identity: Any
    stand: Durchlauf | None
    memory: LaborSpeicher
    wache: LaborWache | None
    # Hier hängt bot.build_session den Entscheider der Labor-Freigabe ein (labor_werkzeuge.LaborSitzung.entscheiden, Strang K2): bekommt die Daten
    # der Client-Nachricht freigabe mit job labor, nachdem labor.client_nachricht_erlaubt sie zugelassen hat. Ohne Eintrag (das Schaufenster hat
    # keinen Platz und keine Freigabe) bleibt der Klick wirkungslos.
    freigabe: Callable[[dict], Awaitable[Any]] | None = None
    daten: LaborDaten = field(default_factory=LaborDaten)  # Regeln, Kontakte, Vorlagen, Wissen: bei jedem Sitzungsstart frisch gelesen

    @classmethod
    async def starten(cls, identity: Any, *, quelle: DurchlaufQuelle | None = None, min_version: int | None = None,
                      db: Any = None, daten: LaborDaten | None = None) -> LaborSitzung:
        """Nimmt eine Laborsitzung an oder wirft LaborAbgelehnt (Durchlauf beendet, keine Einwilligung, Dienst nicht erreichbar).
        daten: nur für Tests, sonst liest labor_daten.laden die Dateien aus WAMO_LABOR_DATEN (ein Fehler dort ergibt den reinen Laborprompt)."""
        if not labor.ist_labor(identity):
            raise labor.LaborPolitikFehler("LaborSitzung.starten ohne Laborprofil")
        if getattr(identity, "labor_aktiv", False) is not True or not labor.laborfelder_stimmig(
                labor.art_normalisieren(identity.labor_art), getattr(identity, "rolle", None), getattr(identity, "labor_platz", None), False):
            # Die Zugangskontrolle lässt nur freigeschaltete, stimmige Laborprofile durch; hier noch einmal, falls jemand eine Identität anders baut
            raise labor.LaborAbgelehnt(labor.GRUND_PROFIL, 403, labor.CODE_PROFIL)
        urteil = await labor_durchlauf.pruefen(identity, quelle=quelle, min_version=min_version)
        if urteil.status != 200:
            raise labor.LaborAbgelehnt(urteil.grund, urteil.status, urteil.code)
        speicher = LaborSpeicher.fuer(identity, urteil.stand, db=db, min_version=min_version)
        wache = (LaborWache(identity.labor_platz, urteil.stand, quelle=quelle, min_version=min_version)
                 if urteil.stand is not None else None)
        geladen = daten if daten is not None else labor_daten.laden()
        logger.info("Labor: Sitzung {} Platz {} Durchlauf {} Text speichern: {} | {}", identity.labor_art, identity.labor_platz,
                    urteil.stand.durchlauf_id[:8] if urteil.stand else "-", "ja" if speicher.enabled else "nein", geladen.logzeile())
        return cls(identity, urteil.stand, speicher, wache, daten=geladen)

    def prompt(self) -> str:
        """Der Prompt dieser Sitzung: immer der Laborprompt, nie der normale mit Gedächtnis und PC. Regeln als eigener Block, alles andere als Daten."""
        return build_labor_prompt(self.identity.name, self.identity.anrede, self.identity.labor_art, self.daten.regeln, self.daten.daten,
                                  text_speichern=bool(self.memory.enabled))  # maßgebend ist, ob der Speicher wirklich schreibt

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
