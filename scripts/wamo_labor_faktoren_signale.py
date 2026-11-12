"""Signale als Ausnahmen für scripts/wamo_labor_faktoren.py (Muster wie infra/sokrates2/labor_tokentest.py).

Strg+C (SIGINT), SIGTERM, unter Linux SIGHUP und unter Windows SIGBREAK lösen die Ausnahme Signal aus. So laufen die
finally-Zweige und das Aufräumen eines Profils auch bei einem Abbruch von außen. Während des Aufräumens zählt kein weiteres Signal
mehr: Ein zweites Strg+C darf das Entfernen des Faktors nie abschneiden. Signale lassen sich nur im Hauptthread setzen, sonst
bleibt alles beim Alten.
"""
from __future__ import annotations

import contextlib
import signal

SIGNALE = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK") if hasattr(signal, name))


class Signal(BaseException):
    """Ein Signal als Ausnahme. Bewusst keine Exception, damit kein allgemeiner except-Zweig sie verschluckt."""

    def __init__(self, nummer: int):
        super().__init__(nummer)
        self.nummer = nummer


def _setzen(handler) -> dict:
    alt: dict = {}
    for s in SIGNALE:
        with contextlib.suppress(ValueError, OSError):
            alt[s] = signal.signal(s, handler)
    return alt


def umsetzen() -> dict:
    """Signale lösen ab jetzt die Ausnahme Signal aus. Liefert die bisherigen Handler für zuruecksetzen."""
    def handler(nummer, frame):
        raise Signal(int(nummer))

    return _setzen(handler)


def ignorieren() -> dict:
    """Signale werden ignoriert (während des Aufräumens). Liefert die bisherigen Handler für zuruecksetzen."""
    return _setzen(signal.SIG_IGN)


def zuruecksetzen(alt: dict) -> None:
    for s, handler in alt.items():
        with contextlib.suppress(ValueError, OSError):
            signal.signal(s, signal.SIG_DFL if handler is None else handler)
