"""Ein Parser für die an/aus-Schalter des Sprachverhaltens (WAMO_ECHO_FILTER, WAMO_ECHO_MODUS, WAMO_STOPP_GRENZE,
WAMO_DUCKEN, WAMO_RUECKMELDUNG, WAMO_WARTESATZ). Leer oder nicht gesetzt gilt der Standard (meist an). Ein unbekannter Wert (Tippfehler im
Notaus) bleibt an, wird aber einmal je Wert gewarnt.

Neu (Sprachpakete A und B, jeder Schalter einzeln; Standard je nach Messung vom 31.10.2026, siehe STANDARDWERTE unten: an sind
WAMO_PAUSEN_PROMPT, WAMO_RUECKMELDUNG_WERKZEUG und das Lernen ohne Momentaufnahmen, die übrigen bleiben aus):
  WAMO_TTS_SATZLEERZEICHEN     Leerzeichen am Ende jedes Satzes, damit Soniox das Satzzeichen nicht vorliest (soniox_tts.py)
  WAMO_PERSONA_RUECKFRAGEN     Persona: Rückfragen begrenzen, Aufträge am Stück (persona.py)
  WAMO_PERSONA_PC_ORDNER       Persona: Ordner des PCs nie raten, per system_info erfragen (persona.py)
  WAMO_PERSONA_BROWSER         Persona: Browserinhalte zuerst mit browser_lesen lesen, Foto nur wenn Text nicht reicht (Standard an)
  WAMO_PAUSEN_PROMPT           Texte nach einer Pause ohne "Ich bin noch da" (bot.py, pause_prompts)
  WAMO_LERNEN_OHNE_MOMENT      Lernen ohne Momentaufnahmen und Aufgabenstände (learning.py, Standard an)
  WAMO_RUECKMELDUNG_WERKZEUG   Rückmeldelaut bei laufendem Werkzeug startet keinen Zug (soft_interrupt.py)
  WAMO_JA_WAECHTER             Ja auf eine Rückfrage führt den Auftrag aus, statt erneut zu fragen (ja_waechter.py)
Fehlerbehebungen aus der Sitzung vom 31.10.2026 (Standard an, sie ändern kein Sprachverhalten, nur Fehlerfälle):
  WAMO_ABBRUCH_HINWEIS         abgebrochenes Werkzeug im Verlauf als "ohne Ergebnis, Auftrag offen" erklären (werkzeug_wache.py)
  WAMO_WERKZEUG_DOPPELT        dieselbe Öffnen-Aktion im selben Zug nicht wiederholen, weitere Seite mit Hinweis (werkzeug_wache.py)
  WAMO_MARKER_FALLBACK         fehlt der Satzende-Marker, den Text sofort freigeben statt am Antwortende (marker_wache.py)
  WAMO_LERNEN_HAUS_SECS        Zahl: so lange darf ein Hausmodell für "lernen" brauchen, bevor Claude übernimmt (Standard 15, 0 = aus)
Zahlen und Werte ohne Schalterform (Stimme, Tempo, Pausen): SONIOX_VOICE, SONIOX_SPEED (0,7 bis 1,3) und
SONIOX_REDUCE_SILENCE (an/aus); leer lässt die Standardeinstellung von Soniox (soniox_tts.stimme_einstellung)."""

from __future__ import annotations

import os

from loguru import logger

AUS = ("aus", "0", "nein", "false", "off", "no", "n", "disabled", "deaktiviert", "inaktiv")
AN = ("an", "ein", "1", "ja", "true", "on", "yes", "y", "aktiv", "enabled")
_GEWARNT: set[tuple[str, str]] = set()


#: Standardwerte der Schalter aus den Sprachpaketen A und B (alle anderen Schalter sind ohne Eintrag hier standardmäßig an).
#: Gemessen am 31.10.2026 mit scripts/wamo-stimme-messen.cjs (Unterwegs, Claude Haiku, Basis ohne die sechs Schalter, je
#: Schalter ein Lauf mit nur diesem Schalter an; Zahlen sind Median / 95. Perzentil bei 3 bis 8 Wiederholungen, Tabelle und
#: Streuung in der README, Abschnitt "Stimme messen"). Ein Schalter steht auf True nur, wenn alle Kennzahlen innerhalb der
#: Streuung der Basis blieben UND der Lauf vollständig war; sonst bleibt er aus, mit Grund:
STANDARDWERTE: dict[str, bool] = {
    # AUS: Lauf brach beim Echo-Szenario ab (Browser geschlossen), Echo und Pause nicht gemessen; die übrigen Kennzahlen blieben
    # in der Streuung (größte Lücke lang p95 750 ms bei 660 bis 780 ms in allen Läufen, Verstummen 1400 / 1780 ms). Der Nutzen
    # (Satzzeichen nicht vorlesen) ist nur in der Hörprobe prüfbar.
    "WAMO_TTS_SATZLEERZEICHEN": False,
    # AUS: Lauf unvollständig (Einwurf, Backchannel, Echo, Pause fehlen, Datenbankausfall), erster Ton nach Faktenfrage
    # 2840 / 3740 ms gegen 2316 / 3242 ms in der Basis (Läufe ohne diesen Schalter: p50 2316 bis 2617 ms, früher 2058 ms).
    "WAMO_PERSONA_RUECKFRAGEN": False,
    # AUS: die Sitzung mit den Faktenfragen fiel auf ein Hausmodell (Datenbank nicht erreichbar), erster Ton nach Faktenfrage
    # nicht gemessen; erster Ton bei Werkzeug p95 5852 ms gegen 2791 ms in der Basis (ein Ausreißer bei 4 Antworten).
    "WAMO_PERSONA_PC_ORDNER": False,
    # AN: vollständiger Lauf, alle Kennzahlen innerhalb der Streuung (Verstummen 1315 / 2260 ms gegen 1340 / 1910 ms, verpasst
    # 0 von 6, erster Ton Fakten 2336 / 2574 ms gegen 2316 / 3242 ms, Pause: WAMO redet dazwischen 0 von 6 wie in der Basis).
    # Der Nutzen (keine Floskel "Ich bin noch da") zeigt sich in den Kennzahlen nicht, nur in der Hörprobe.
    "WAMO_PAUSEN_PROMPT": True,
    # AN: Zug bei Rückmeldelaut während des Werkzeugs 0 von 10 gegen 5 von 10 in der Basis (21 von 38 über alle Läufe ohne
    # den Schalter), Ergebnis kam 5 von 5 wie in der Basis, Verstummen 1345 / 2480 ms gegen 1340 / 1910 ms (Median gleich),
    # verpasste Einwürfe 0 von 8, falsche Unterbrechungen im Log 0 von 15. Stoppwörter und echte Einwürfe wirken weiter,
    # eine Freigabe entscheidet nie die Sprache (test_werkzeug_rueckmeldung.py).
    "WAMO_RUECKMELDUNG_WERKZEUG": True,
    # AUS: nicht gemessen (der Lauf kam nicht mehr zustande); der Wächter braucht Messung UND die Freigabe-Prüfung.
    "WAMO_JA_WAECHTER": False,
}


def schalter_an(name: str, wert: str | None = None, standard: bool | None = None) -> bool:
    """Ist der Schalter name an? wert ersetzt die Umgebungsvariable (für Tests), standard gilt bei leerem Wert
    (ohne Angabe: der Eintrag in STANDARDWERTE, sonst an)."""
    if standard is None:
        standard = STANDARDWERTE.get(name, True)
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
