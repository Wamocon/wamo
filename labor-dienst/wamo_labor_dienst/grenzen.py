"""Grenzen im Speicher des Prozesses: Eimer für die Rate (Begrenzer) und Zähler für Fehlversuche je Quelle (FehlerZaehler).

Beide arbeiten mit der Uhr des Dienstes (in Tests verstellbar), merken sich nur begrenzt viele Schlüssel (der älteste fällt heraus, der Speicher
wächst nie mit der Zahl fremder Quellen) und sind für mehrere Fäden sicher. Eine zurückgestellte Uhr sperrt nie dauerhaft.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable

MAX_SCHLUESSEL = 10_000


class Begrenzer:
    """Eimer je Schlüssel: füllt sich mit rate je Sekunde bis höchstens schub, jeder erlaubte Aufruf nimmt einen heraus."""

    def __init__(self, rate: float, schub: int, uhr: Callable[[], float], max_schluessel: int = MAX_SCHLUESSEL):
        self._rate, self._schub, self._uhr, self._max = rate, schub, uhr, max_schluessel
        self._eimer: OrderedDict[object, list[float]] = OrderedDict()  # Schlüssel -> [Inhalt, letzte Zeit]
        self._sperre = threading.Lock()

    def __len__(self) -> int:
        return len(self._eimer)

    def erlaubt(self, schluessel: object) -> bool:
        jetzt = self._uhr()
        with self._sperre:
            inhalt, zuletzt = self._eimer.get(schluessel, [float(self._schub), jetzt])
            inhalt = min(float(self._schub), inhalt + max(0.0, jetzt - zuletzt) * self._rate)  # eine zurückgestellte Uhr füllt nichts auf
            erlaubt = inhalt >= 1.0
            self._eimer[schluessel] = [inhalt - 1.0 if erlaubt else inhalt, jetzt]
            self._eimer.move_to_end(schluessel)
            while len(self._eimer) > self._max:
                self._eimer.popitem(last=False)
        return erlaubt


class FehlerZaehler:
    """Zählt Fehlversuche je Quelle in einem festen Fenster. melden liefert wahr, sobald die Quelle über der Grenze liegt."""

    def __init__(self, grenze: int, fenster: float, uhr: Callable[[], float], max_quellen: int = MAX_SCHLUESSEL):
        self._grenze, self._fenster, self._uhr, self._max = grenze, fenster, uhr, max_quellen
        self._quellen: OrderedDict[str, list[float | int]] = OrderedDict()  # Quelle -> [Beginn, Zahl]
        self._sperre = threading.Lock()

    def __len__(self) -> int:
        return len(self._quellen)

    def melden(self, quelle: str) -> bool:
        jetzt = self._uhr()
        with self._sperre:
            beginn, zahl = self._quellen.get(quelle, [jetzt, 0])
            if jetzt - beginn >= self._fenster or jetzt < beginn:
                beginn, zahl = jetzt, 0
            zahl += 1
            self._quellen[quelle] = [beginn, zahl]
            self._quellen.move_to_end(quelle)
            while len(self._quellen) > self._max:
                self._quellen.popitem(last=False)
        return zahl > self._grenze
