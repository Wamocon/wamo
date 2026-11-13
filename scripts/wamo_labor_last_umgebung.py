"""Gemeinsame Typen des Lasttests (scripts/wamo_labor_last.py): Einstellungen, Umgebung, Zustand, Uhr und parallele Aufgaben.

Die Umgebung bündelt alles, was der Lauf von außen braucht (Uhr, Browser, Server, Dienst, Kern, Umschlag, Temp-Ordner, Ausgabe). Im echten Lauf sind das die
echten Wege, in den Tests die Attrappen aus scripts/wamo_labor_last_attrappe.py. Der Zustand merkt sich, was der Lauf am Server und auf dem Rechner angefasst
hat, damit das Aufräumen genau das zurücknimmt und nie mehr.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from wamo_labor_last_plan import Annahmen, Kosten, Stufe, profile_fuer
from wamo_labor_platz_adressen import WAMO_URL

FREIGABEN = ("ablehnen", "freigeben", "keine")


@dataclass(frozen=True)
class Einstellungen:
    lauf: str
    seed: int
    stufen: tuple[Stufe, ...]
    clips_ordner: Path
    ausgabe_ordner: Path
    schema: str = "wamo_dev"
    host: str = "wamo-sokrates2"
    slot: str = "b"
    einwilligung: int = 1
    url: str = WAMO_URL
    murmeln_datei: Path | None = None
    freigabe: str = "ablehnen"  # was mit der Freigabekarte bei J1 geschieht: ablehnen (Standard, schreibt nichts), freigeben oder keine (liegen lassen)
    kopflos: bool = False
    mess_intervall_s: float = 15.0
    ratenlimit_anfragen: int | None = None
    ratenlimit_tokens: int | None = None
    annahmen: Annahmen = field(default_factory=Annahmen)
    kosten: Kosten | None = None
    kerndaten_loeschen: bool = True  # am Ende die Gesprächstexte der Profile im Zeitfenster des Laufs im Kern löschen (wamo_labor.py loeschen --ab)

    @property
    def sitzungen_max(self) -> int:
        return max(s.sitzungen for s in self.stufen)

    @property
    def profile(self) -> tuple[str, ...]:
        return profile_fuer(self.sitzungen_max)

    @property
    def plaetze(self) -> tuple[int, ...]:
        return tuple(range(1, self.sitzungen_max + 1))


@dataclass
class Umgebung:
    uhr: object  # jetzt() und schlafen(sekunden)
    treiber_fabrik: Callable[[int], object]
    server: object  # messen(seit)
    dienst: object  # plaetze, durchlauf_oeffnen, durchlauf_beenden, daten_loeschen
    kern: Callable  # (host, nutzlast) -> wamo_labor.Ergebnis, wie wamo_labor.run_remote
    umschlag: object  # zugang(kennung)
    tempordner_fabrik: Callable[[], Path]
    ausgabe: Callable[[str], None]
    speichern: Callable[[dict], None]


@dataclass
class Zustand:
    """Was der Lauf angefasst hat. Einträge entstehen vor dem Aufruf, damit auch ein halber Schritt aufgeräumt wird."""
    tempordner: Path | None = None
    profile: list[str] = field(default_factory=list)
    plaetze: list[int] = field(default_factory=list)
    treiber: dict[int, object] = field(default_factory=dict)
    gespraeche: set[int] = field(default_factory=set)
    ab: float | None = None  # Zeitpunkt auf der Uhr des Servers, ab dem im Kern gelöscht wird (Beginn des Laufs, mit Reserve)

    def angefasst(self) -> bool:
        return bool(self.tempordner or self.profile or self.plaetze or self.treiber)


class Echtzeit:
    """Die echte Uhr."""

    def jetzt(self) -> float:
        return time.time()

    def schlafen(self, sekunden: float) -> None:
        time.sleep(max(0.0, sekunden))


def parallel(aufgaben: Sequence[Callable[[], object]], name: str = "aufgabe") -> list[BaseException | None]:
    """Führt die Aufgaben gleichzeitig aus und gibt je Aufgabe den Fehler (oder None) zurück. Das Warten läuft in Scheiben von einer halben
    Sekunde, damit Strg+C und Signale im Hauptthread ankommen."""
    fehler: list[BaseException | None] = [None] * len(aufgaben)

    def lauf(i: int, aufgabe: Callable[[], object]) -> None:
        try:
            aufgabe()
        except BaseException as e:  # noqa: BLE001 (jede Aufgabe meldet ihren Fehler, nie ein stiller Thread)
            fehler[i] = e

    threads = [threading.Thread(target=lauf, args=(i, a), daemon=True, name=f"{name}-{i}") for i, a in enumerate(aufgaben)]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(0.5)
    return fehler


def kurz(text: object, grenze: int = 200) -> str:
    """Ein Text für die Ausgabe: erste Zeile, ohne Steuerzeichen, gekürzt."""
    erste = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return "".join(c if c.isprintable() else " " for c in erste)[:grenze]
