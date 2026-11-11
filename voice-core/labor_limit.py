"""Ratenbegrenzung der Laborsitzungen (Sicherheitsreview Befund 4). Alles gilt im Kern, nicht im Prompt und nicht in der Oberfläche.

Ein Laborprofil gehört einem Gast, der Browser-Token ist lesbar: ohne Grenzen könnte ein böswilliges Profil den gemeinsamen Modellschlüssel (jede
getippte Eingabe ist ein Modelllauf, jede Websuche bis zu drei Suchen auf dem Server-Schlüssel), den Dienst der Kollegen (jedes /start und jedes
Offer ist ein Dienstaufruf) und damit den Betrieb belasten. Die Grenzen:

  * getippter Text (send-text)   Abstand zwischen zwei Eingaben und Gesamtmenge je Profil und Durchlauf (TextBegrenzer, im LaborRTVIProcessor)
  * Klick auf eine Freigabekarte Abstand und Höchstzahl je Profil und Durchlauf (KlickBegrenzer, im LaborRTVIProcessor)
  * Websuche                     Anzahl je Profil und Durchlauf (SuchZaehler, in tools.register_tools)
  * /start und Offer             Anzahl je Profil und Fenster (AnfragenFenster, in der AccessMiddleware), noch bevor der Dienst gefragt wird

Text, Klicks und Suchen zählen je Profil und Durchlauf, nicht je Verbindung: LimitRegister gibt derselben Person im selben Durchlauf bei jeder
Neuverbindung dieselben Zähler zurück (eine Neuverbindung setzt nichts zurück). Ein neuer Durchlauf ist ein neuer Test und fängt neu an. Der
Speicher des Registers ist begrenzt (die ältesten Schlüssel fallen weg); ein Neustart des Kerns setzt alle Zähler zurück.

Die Standardwerte stehen als benannte Konstanten hier, jeder lässt sich über eine Einstellung WAMO_LABOR_* ändern (ganze Zahlen in festen Grenzen, alles
Unbrauchbare heißt Standard, nie unbegrenzt). Abgelehntes zählt nicht mit: wer wartet, kommt wieder durch.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass

from env_schalter import zahl_im_bereich
from labor_vertrag import env_int

TEXT_ABSTAND_SECS = 2.0  # WAMO_LABOR_TEXT_ABSTAND_SECS (0,5 bis 60): kleinster Abstand zwischen zwei getippten Eingaben einer Sitzung
TEXT_MAX_ZEICHEN = 40_000  # WAMO_LABOR_TEXT_MAX_ZEICHEN (1000 bis 400000): alle getippten Eingaben einer Sitzung zusammen
SUCHEN_MAX = 10  # WAMO_LABOR_SUCHEN_MAX (1 bis 100): Aufrufe des Werkzeugs websuche je Sitzung
ANFRAGEN_MAX = 20  # WAMO_LABOR_ANFRAGEN_MAX (1 bis 600): /start und Offer je Profil im Fenster
ANFRAGEN_FENSTER_SECS = 60.0
KLICK_ABSTAND_SECS = 0.5  # WAMO_LABOR_KLICK_ABSTAND_SECS (0,1 bis 60): kleinster Abstand zwischen zwei Klicks auf Freigabekarten
KLICK_MAX = 100  # WAMO_LABOR_KLICK_MAX (1 bis 1000): alle Klicks auf Freigabekarten zusammen, mindestens so viele wie WAMO_LABOR_MAX_SCHREIBEN (jede Karte braucht einen)
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


def klick_abstand_secs() -> float:
    wert = zahl_im_bereich("WAMO_LABOR_KLICK_ABSTAND_SECS", 0.1, 60)
    return KLICK_ABSTAND_SECS if wert is None else float(wert)


def klick_max() -> int:
    return env_int("WAMO_LABOR_KLICK_MAX", KLICK_MAX, 1, 1000)


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


class KlickBegrenzer:
    """Begrenzung der Klicks auf Freigabekarten (client-message freigabe): Abstand und Höchstzahl. Ein abgelehnter Klick zählt nicht und verlängert die
    Frist nicht. Ohne sie könnte ein Teilnehmer den Klickweg (Prüfung, Log, Meldung an das Modell) beliebig schnell und beliebig oft anstoßen."""

    def __init__(self, *, abstand_secs: float | None = None, maximum: int | None = None, uhr: Callable[[], float] = time.monotonic):
        self.abstand_secs = klick_abstand_secs() if abstand_secs is None else float(abstand_secs)
        self.maximum = klick_max() if maximum is None else int(maximum)
        self._uhr = uhr
        self._letzte: float | None = None
        self._anzahl = 0

    def zulassen(self) -> bool:
        jetzt = self._uhr()
        if self._letzte is not None and jetzt - self._letzte < self.abstand_secs:
            return False
        if self._anzahl >= self.maximum:
            return False
        self._letzte, self._anzahl = jetzt, self._anzahl + 1
        return True


class SuchZaehler:
    """Wie viele Suchen ein Profil in einem Durchlauf noch hat."""

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


@dataclass(frozen=True)
class SitzungsLimits:
    """Die Zähler einer Laborsitzung: getippter Text, Klicks auf Freigabekarten und Websuchen. Gemeinsam für alle Verbindungen desselben Profils im selben Durchlauf."""

    text: TextBegrenzer
    klicks: KlickBegrenzer
    suchen: SuchZaehler


class LimitRegister:
    """Gibt je Profil und Durchlauf immer dieselben Zähler zurück (eine Neuverbindung setzt Text, Klicks und Suchen nicht zurück). Der Speicher ist
    begrenzt: über max_schluessel Schlüsseln fallen die am längsten nicht benutzten weg."""

    def __init__(self, max_schluessel: int = 1024):
        self._max_schluessel = max_schluessel
        self._eintraege: OrderedDict[str, SitzungsLimits] = OrderedDict()

    def groesse(self) -> int:
        return len(self._eintraege)

    def fuer(self, profil: str, durchlauf: str | None) -> SitzungsLimits:
        """Zähler für dieses Profil im Durchlauf (durchlauf None: das Schaufenster ohne Platz, dann ein Schlüssel je Profil)."""
        schluessel = json.dumps([str(profil), None if durchlauf is None else str(durchlauf)], separators=(",", ":"))  # kein Trennzeichen verschiebt etwas
        eintrag = self._eintraege.get(schluessel)
        if eintrag is None:
            eintrag = self._eintraege[schluessel] = SitzungsLimits(TextBegrenzer(), KlickBegrenzer(), SuchZaehler())
        self._eintraege.move_to_end(schluessel)
        while len(self._eintraege) > self._max_schluessel:
            self._eintraege.popitem(last=False)
        return eintrag


LIMITS = LimitRegister()  # der Kern: bot.build_session holt hier die Zähler einer Laborsitzung
