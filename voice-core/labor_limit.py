"""Ratenbegrenzung der Laborsitzungen (Sicherheitsreview Befund 4). Alles gilt im Kern, nicht im Prompt und nicht in der Oberfläche.

Ein Laborprofil gehört einem Gast, der Browser-Token ist lesbar: ohne Grenzen könnte ein böswilliges Profil den gemeinsamen Modellschlüssel (jede
getippte Eingabe ist ein Modelllauf, jede Websuche bis zu drei Suchen auf dem Server-Schlüssel), den Dienst der Kollegen (jedes /start und jedes
Offer ist ein Dienstaufruf) und damit den Betrieb belasten. Die Grenzen:

  * getippter Text (send-text)   Abstand zwischen zwei Eingaben und Gesamtmenge je Sitzung (TextBegrenzer, im LaborRTVIProcessor)
  * Websuche                     Anzahl je Sitzung (SuchZaehler, in tools.register_tools)
  * /start und Offer             Anzahl je Profil und Fenster (AnfragenFenster, in der AccessMiddleware), noch bevor der Dienst gefragt wird

Die Standardwerte stehen als benannte Konstanten hier, jeder lässt sich über eine Einstellung WAMO_LABOR_* ändern (ganze Zahlen in festen Grenzen, alles
Unbrauchbare heißt Standard, nie unbegrenzt). Abgelehntes zählt nicht mit: wer wartet, kommt wieder durch.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from env_schalter import zahl_im_bereich
from labor_vertrag import env_int

TEXT_ABSTAND_SECS = 2.0  # WAMO_LABOR_TEXT_ABSTAND_SECS (0,5 bis 60): kleinster Abstand zwischen zwei getippten Eingaben einer Sitzung
TEXT_MAX_ZEICHEN = 40_000  # WAMO_LABOR_TEXT_MAX_ZEICHEN (1000 bis 400000): alle getippten Eingaben einer Sitzung zusammen
SUCHEN_MAX = 10  # WAMO_LABOR_SUCHEN_MAX (1 bis 100): Aufrufe des Werkzeugs websuche je Sitzung
ANFRAGEN_MAX = 20  # WAMO_LABOR_ANFRAGEN_MAX (1 bis 600): /start und Offer je Profil im Fenster
ANFRAGEN_FENSTER_SECS = 60.0
GRUND_SUCHEN = "In diesem Gespräch sind alle Suchen verbraucht. Eine weitere Suche gibt es nicht."


def text_abstand_secs() -> float:
    wert = zahl_im_bereich("WAMO_LABOR_TEXT_ABSTAND_SECS", 0.5, 60)
    return TEXT_ABSTAND_SECS if wert is None else float(wert)


def text_max_zeichen() -> int:
    return env_int("WAMO_LABOR_TEXT_MAX_ZEICHEN", TEXT_MAX_ZEICHEN, 1000, 400_000)


def suchen_max() -> int:
    return env_int("WAMO_LABOR_SUCHEN_MAX", SUCHEN_MAX, 1, 100)


def anfragen_max() -> int:
    return env_int("WAMO_LABOR_ANFRAGEN_MAX", ANFRAGEN_MAX, 1, 600)


class TextBegrenzer:
    """Begrenzung der getippten Eingaben einer Sitzung: Abstand und Gesamtmenge. Eine abgelehnte Eingabe zählt nicht und verlängert die Frist nicht."""

    def __init__(self, *, abstand_secs: float | None = None, max_zeichen: int | None = None, uhr: Callable[[], float] = time.monotonic):
        self.abstand_secs = text_abstand_secs() if abstand_secs is None else float(abstand_secs)
        self.max_zeichen = text_max_zeichen() if max_zeichen is None else int(max_zeichen)
        self._uhr = uhr
        self._letzte: float | None = None
        self._summe = 0

    def zulassen(self, zeichen: int) -> bool:
        jetzt = self._uhr()
        if self._letzte is not None and jetzt - self._letzte < self.abstand_secs:
            return False
        if zeichen < 0 or self._summe + zeichen > self.max_zeichen:
            return False
        self._letzte, self._summe = jetzt, self._summe + zeichen
        return True


class SuchZaehler:
    """Wie viele Suchen eine Sitzung noch hat."""

    def __init__(self, maximum: int | None = None):
        self.maximum = suchen_max() if maximum is None else int(maximum)
        self.verbraucht = 0

    def verbrauchen(self) -> bool:
        if self.verbraucht >= self.maximum:
            return False
        self.verbraucht += 1
        return True


class AnfragenFenster:
    """Gleitendes Fenster je Schlüssel (Profil): höchstens maximum Ereignisse in secs Sekunden. Der Speicher ist begrenzt: über max_schluessel
    Schlüsseln fallen erst die abgelaufenen, dann die ältesten weg."""

    def __init__(self, maximum: int | None = None, secs: float = ANFRAGEN_FENSTER_SECS, *, uhr: Callable[[], float] = time.monotonic,
                 max_schluessel: int = 2048):
        self.maximum = anfragen_max() if maximum is None else int(maximum)
        self.secs = float(secs)
        self._uhr = uhr
        self._max_schluessel = max_schluessel
        self._ereignisse: dict[str, deque[float]] = {}

    def groesse(self) -> int:
        return len(self._ereignisse)

    def _abgelaufene_entfernen(self, ereignisse: deque[float], jetzt: float) -> None:
        while ereignisse and jetzt - ereignisse[0] >= self.secs:
            ereignisse.popleft()

    def zulassen(self, schluessel: str) -> bool:
        jetzt = self._uhr()
        ereignisse = self._ereignisse.setdefault(schluessel, deque())
        self._abgelaufene_entfernen(ereignisse, jetzt)
        erlaubt = len(ereignisse) < self.maximum
        if erlaubt:
            ereignisse.append(jetzt)
        if len(self._ereignisse) > self._max_schluessel:
            self._aufraeumen(jetzt)
        return erlaubt

    def _aufraeumen(self, jetzt: float) -> None:
        for schluessel in list(self._ereignisse):
            self._abgelaufene_entfernen(self._ereignisse[schluessel], jetzt)
            if not self._ereignisse[schluessel]:
                del self._ereignisse[schluessel]
        while len(self._ereignisse) > self._max_schluessel:
            del self._ereignisse[next(iter(self._ereignisse))]  # der älteste Schlüssel (Einfügereihenfolge)
