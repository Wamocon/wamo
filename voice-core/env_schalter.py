"""Ein Parser für die an/aus-Schalter des Sprachverhaltens (WAMO_ECHO_FILTER, WAMO_STOPP_GRENZE, WAMO_DUCKEN,
WAMO_RUECKMELDUNG, WAMO_WARTESATZ). Leer oder nicht gesetzt heißt an; aus, 0, nein, false, off und no schalten ab."""

from __future__ import annotations

import os

AUS = ("aus", "0", "nein", "false", "off", "no")


def schalter_an(name: str, wert: str | None = None) -> bool:
    """Ist der Schalter name an? wert ersetzt die Umgebungsvariable (für Tests)."""
    w = os.getenv(name) if wert is None else wert
    return (w or "an").strip().lower() not in AUS
