"""Gemeinsame Test-Vorbereitung für den Host: jeder Test beginnt ohne offene Freigaben und ohne Doppel-Sperre.

Offene Freigaben lassen Eingaben ruhen (status=wartet, wamo_host._eingabe_vorab); ein Rest aus einem früheren Test
würde sonst spätere Tests beeinflussen.
"""

from unittest import mock

import pytest

import wamo_host


@pytest.fixture(autouse=True)
def _sauberer_host():
    """Ohne offene Freigaben und entsperrt (die Tests hängen nie vom echten Bildschirm ab; Tests mit Sperre setzen
    sie selbst, der Echttest in test_security.py nutzt die echte Prüfung)."""
    wamo_host.PENDING.clear()
    wamo_host._recent_actions.clear()
    with mock.patch.object(wamo_host.wc, "is_locked", return_value=False):
        yield
    wamo_host.PENDING.clear()
    wamo_host._recent_actions.clear()
