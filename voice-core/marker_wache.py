"""Satzende-Marker fehlt: den Text nicht bis zum Ende der Antwort zurückhalten.

Befund (31.10.2026, 12:44:36, Zug 6): Nach der Werkzeugrunde schickte das Modell Text ohne den Marker ● (die Persona
verlangt ihn, auch nach einem Werkzeugergebnis). Pipecat hält Text so lange zurück, bis ein Marker auftaucht, und gibt ihn
ohne Marker erst am Ende der Antwort frei ("filter_incomplete_user_turns is enabled but LLM response did not contain turn
completion markers", "Pushing text anyway"). Es geht also kein Text verloren, aber die Sprachausgabe beginnt erst, wenn
das Modell die ganze Antwort geschrieben hat, und der Marker steht nicht im Verlauf (das Modell lernt dann im nächsten Zug
aus seinen eigenen Antworten, ihn wegzulassen).

Wächter: Beginnt eine Antwort mit Text und ohne Marker, ist sie vollständig (ein unvollständiger Redebeitrag wird mit ◐ oder ○
gemeldet, nie mit Text). Sobald MARKER_MIN_ZEICHEN Zeichen Text da sind, setzt der Wächter den Marker ● vor den Text, als
hätte ihn das Modell geschrieben; Pipecat gibt den Text dann sofort frei, der Verlauf zeigt "● Text" und die Turn-Erkennung
bekommt ihr Signal. Dieser Weg greift nur in Antworten, die Pipecat noch ohne Marker puffert und die nicht schon durch den
Zwischenmerker "ein Marker je Redebeitrag" verworfen werden (dessen Entscheidung bleibt unberührt).

Schalter: WAMO_MARKER_FALLBACK (Standard an; verkürzt nur die Wartezeit auf eine ohnehin ausgesprochene Antwort).
"""

from __future__ import annotations

from loguru import logger

from env_schalter import schalter_an

MARKER_MIN_ZEICHEN = 4  # so viel Text ohne Marker, dann gilt die Antwort als vollständig


def marker_fallback_an(wert: str | None = None) -> bool:
    """WAMO_MARKER_FALLBACK: fehlt der Satzende-Marker, den Text trotzdem sofort freigeben (Standard an)."""
    return schalter_an("WAMO_MARKER_FALLBACK", wert)


class MarkerFallback:
    """Mixin für Pipecat-LLM-Dienste (vor den Dienst in die Basisklassen): ergänzt in _push_turn_text den fehlenden Marker.
    Greift auf drei Attribute des Pipecat-Mixins zu (_turn_marker, _turn_text_buffer, _user_turn_completion_voiced und die
    Konfiguration); fehlt eines (Pipecat-Umbau), bleibt alles wie in Pipecat und es gibt eine Warnung."""

    _wamo_marker_ergaenzt = 0  # für Tests und das Log
    _wamo_marker_warnung = False

    async def _push_turn_text(self, text: str):
        try:
            if (
                marker_fallback_an()
                and self._turn_marker is None  # noch kein Urteil in dieser Antwort
                and not self._user_turn_completion_voiced  # dann verwirft Pipecat die Antwort mit Absicht
            ):
                config = self._user_turn_completion_config
                gesamt = self._turn_text_buffer + text
                markers = (config.complete_marker, config.incomplete_short_marker, config.incomplete_long_marker)
                if not any(m in gesamt for m in markers) and len(gesamt.strip()) >= MARKER_MIN_ZEICHEN:
                    self._turn_text_buffer = f"{config.complete_marker} {self._turn_text_buffer}"
                    self._wamo_marker_ergaenzt += 1
                    logger.info("Satzende-Marker fehlte, ● ergänzt (insgesamt {}): Text wird sofort freigegeben",
                                self._wamo_marker_ergaenzt)
        except AttributeError:
            if not self._wamo_marker_warnung:
                self._wamo_marker_warnung = True
                logger.warning("Marker-Fallback nicht möglich: Pipecat-Mixin hat andere Attribute als erwartet")
        await super()._push_turn_text(text)
