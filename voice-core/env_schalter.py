"""Ein Parser für die an/aus-Schalter des Sprachverhaltens (WAMO_ECHO_FILTER, WAMO_ECHO_MODUS, WAMO_STOPP_GRENZE,
WAMO_DUCKEN, WAMO_RUECKMELDUNG, WAMO_WARTESATZ). Leer oder nicht gesetzt gilt der Standard (meist an). Ein unbekannter Wert (Tippfehler im
Notaus) bleibt an, wird aber einmal je Wert gewarnt.

Neu (Sprachpakete A und B, jeder Schalter einzeln; Standard aus, bis der Messweg die Kennzahlen bestätigt hat, nur das
Lernen ohne Momentaufnahmen ist an, weil es nicht im Sprachpfad liegt):
  WAMO_TTS_SATZLEERZEICHEN     Leerzeichen am Ende jedes Satzes, damit Soniox das Satzzeichen nicht vorliest (soniox_tts.py)
  WAMO_PERSONA_RUECKFRAGEN     Persona: Rückfragen begrenzen, Aufträge am Stück (persona.py)
  WAMO_PERSONA_PC_ORDNER       Persona: Ordner des PCs nie raten, per system_info erfragen (persona.py)
  WAMO_PAUSEN_PROMPT           Texte nach einer Pause ohne "Ich bin noch da" (bot.py, pause_prompts)
  WAMO_LERNEN_OHNE_MOMENT      Lernen ohne Momentaufnahmen und Aufgabenstände (learning.py, Standard an)
  WAMO_RUECKMELDUNG_WERKZEUG   Rückmeldelaut bei laufendem Werkzeug startet keinen Zug (soft_interrupt.py)
  WAMO_JA_WAECHTER             Ja auf eine Rückfrage führt den Auftrag aus, statt erneut zu fragen (ja_waechter.py)
Fehlerbehebungen aus der Sitzung vom 31.10.2026 (Standard an, sie ändern kein Sprachverhalten, nur Fehlerfälle):
  WAMO_ABBRUCH_HINWEIS         abgebrochenes Werkzeug im Verlauf als "ohne Ergebnis, Auftrag offen" erklären (werkzeug_wache.py)
  WAMO_WERKZEUG_DOPPELT        dieselbe Öffnen-Aktion im selben Zug nicht wiederholen, weitere Seite mit Hinweis (werkzeug_wache.py)
Zahlen und Werte ohne Schalterform (Stimme, Tempo, Pausen): SONIOX_VOICE, SONIOX_SPEED (0,7 bis 1,3) und
SONIOX_REDUCE_SILENCE (an/aus); leer lässt die Standardeinstellung von Soniox (soniox_tts.stimme_einstellung)."""

from __future__ import annotations

import os

from loguru import logger

AUS = ("aus", "0", "nein", "false", "off", "no", "n", "disabled", "deaktiviert", "inaktiv")
AN = ("an", "ein", "1", "ja", "true", "on", "yes", "y", "aktiv", "enabled")
_GEWARNT: set[tuple[str, str]] = set()


def schalter_an(name: str, wert: str | None = None, standard: bool = True) -> bool:
    """Ist der Schalter name an? wert ersetzt die Umgebungsvariable (für Tests), standard gilt bei leerem Wert."""
    w = (os.getenv(name) if wert is None else wert) or ""
    w = w.strip().lower()
    if not w:
        return standard
    if w in AN:
        return True
    if w in AUS:
        return False
    if (name, w) not in _GEWARNT:
        _GEWARNT.add((name, w))
        if standard:
            logger.warning("{} hat den unbekannten Wert {!r}: gilt als an (zum Abschalten aus, 0 oder nein setzen)", name, w)
        else:
            logger.warning("{} hat den unbekannten Wert {!r}: bleibt aus (zum Einschalten an, 1 oder ja setzen)", name, w)
    return standard


def schalter_oder_none(name: str, wert: str | None = None) -> bool | None:
    """Wie schalter_an, aber leer oder unbekannt heißt None (nichts einstellen): für Einstellungen, die ohne Wert die
    Standardeinstellung des Dienstes behalten. Ein unbekannter Wert wird einmal je Wert gewarnt."""
    w = ((os.getenv(name) if wert is None else wert) or "").strip().lower()
    if not w:
        return None
    if w in AN:
        return True
    if w in AUS:
        return False
    if (name, w) not in _GEWARNT:
        _GEWARNT.add((name, w))
        logger.warning("{} hat den unbekannten Wert {!r}: wird ignoriert (erlaubt: an oder aus)", name, w)
    return None


def zahl_im_bereich(name: str, minimum: float, maximum: float, wert: str | None = None) -> float | None:
    """Zahl aus der Umgebung (Komma oder Punkt). Leer gibt None; eine ungültige oder außerhalb von minimum bis maximum
    liegende Zahl wird mit einer Warnung (einmal je Wert) ignoriert, also ebenfalls None."""
    w = ((os.getenv(name) if wert is None else wert) or "").strip()
    if not w:
        return None
    try:
        zahl = float(w.replace(",", "."))
    except ValueError:
        zahl = float("nan")
    if not minimum <= zahl <= maximum:  # auch nan fällt hier heraus
        if (name, w) not in _GEWARNT:
            _GEWARNT.add((name, w))
            logger.warning("{} hat den ungültigen Wert {!r}: wird ignoriert (erlaubt: {} bis {})", name, w, minimum, maximum)
        return None
    return zahl
