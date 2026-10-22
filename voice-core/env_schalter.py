"""Ein Parser für die an/aus-Schalter des Sprachverhaltens (WAMO_ECHO_FILTER, WAMO_ECHO_MODUS, WAMO_STOPP_GRENZE,
WAMO_DUCKEN, WAMO_RUECKMELDUNG, WAMO_WARTESATZ). Leer oder nicht gesetzt gilt der Standard (meist an). Ein unbekannter Wert (Tippfehler im
Notaus) bleibt an, wird aber einmal je Wert gewarnt."""

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
        logger.warning("{} hat den unbekannten Wert {!r}: gilt als an (zum Abschalten aus, 0 oder nein setzen)", name, w)
    return True
