"""RTVI-Verarbeiter für Laborsitzungen: verwirft jede Client-Nachricht außerhalb der Erlaubnisliste, bevor Pipecat sie auswertet.

Pipecat wertet die RTVI-Standardnachrichten selbst aus, noch vor dem Handler on_client_message von bot.py (installierte Version 1.12:
client-ready, disconnect-bot, client-message, ui-event, ui-snapshot, ui-cancel-job-group, llm-function-call-result, send-text,
raw-audio, raw-audio-batch, dtmf; ältere Versionen kennen zusätzlich append-to-context, action und update-config). Ein Teilnehmer kann
sein Token aus dem Browser lesen und solche Nachrichten selbst senden, zum Beispiel ein erfundenes Werkzeugergebnis
(llm-function-call-result) oder Text in den Kontext (append-to-context). Deshalb sitzt dieser Filter VOR dem Verarbeiter:
labor.rtvi_nachricht_erlaubt lässt nur client-ready, send-text (begrenzt) und die Freigabe mit job labor durch. Die Liste ist eine
Erlaubnisliste, also gilt sie auch für jeden Typ, den eine künftige Pipecat-Version dazubringt.
"""

from __future__ import annotations

from loguru import logger

from pipecat.frames.frames import Frame, InputTransportMessageFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.frameworks.rtvi import RTVIProcessor

import labor


class LaborRTVIProcessor(RTVIProcessor):
    """RTVIProcessor, der Nachrichten des Browsers gegen die Laborliste prüft. art kommt aus der geprüften Identität."""

    def __init__(self, *, art: str, **kwargs):
        super().__init__(**kwargs)
        self._labor_art = labor.art_normalisieren(art) or labor.STRENGSTE_ART

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, InputTransportMessageFrame) and not labor.rtvi_nachricht_erlaubt(self._labor_art, frame.message):
            message = frame.message if isinstance(frame.message, dict) else {}
            typ = message.get("type")
            # Nur der Typ ins Log (kurz, ohne Inhalt): der Inhalt kann Gesprächstext oder ein Schlüssel sein
            logger.warning("Labor: Client-Nachricht {!r} verworfen", typ[:40] if isinstance(typ, str) else type(typ).__name__)
            return
        await super().process_frame(frame, direction)
