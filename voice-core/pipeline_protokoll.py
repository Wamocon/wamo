"""Kurzes Protokoll der wichtigsten Ereignisse einer Sitzung (Pipecat-Observer), damit ein Hänger im Journal sichtbar wird.

Anlass am 12.11.2026: Im Chat drehte sich der Puls (WAMO "denkt"), eine neue Frage löste nichts aus, und im Journal stand von 09:42:02 bis 09:46:02 keine
einzige Zeile. Ohne diese Zeilen ließ sich nicht sagen, ob die Nachricht den Kern erreichte, ob das Modell gefragt wurde oder ob ein Fehler fiel.

Je Ereignis eine Zeile, nie ein Inhalt des Gesprächs, nur Frame-Art, Name des Prozessors und Zähler:

  Eingabe       LLMMessagesAppendFrame (getippte Nachricht ist in der Pipeline, mit Zeichenzahl und ob das Modell sofort läuft)
  Modell        LLMFullResponseStartFrame und LLMFullResponseEndFrame (Anfrage an das Modell beginnt und endet)
  Werkzeug      FunctionCallInProgressFrame (Name) und FunctionCallResultFrame (Name)
  Unterbrechung InterruptionFrame
  Fehler        ErrorFrame (Text des Fehlers, ohne Gesprächsinhalt)

Jede Zeile beginnt mit "Pipeline:" und lässt sich im Journal mit grep finden. Der Beobachter ändert nichts am Fluss.
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection


def _name(quelle: object) -> str:
    return str(getattr(quelle, "name", None) or type(quelle).__name__)[:40]


def _zeichen(frame: LLMMessagesAppendFrame) -> int:
    summe = 0
    for m in frame.messages or []:
        inhalt = m.get("content") if isinstance(m, dict) else None
        summe += len(inhalt) if isinstance(inhalt, str) else 0
    return summe


def beschreibe(frame: object, quelle: object, richtung: FrameDirection) -> str | None:
    """Die Logzeile zu einem Frame oder None, wenn er nicht zu den Ereignissen gehört. Nie ein Inhalt des Gesprächs."""
    von = _name(quelle)
    if isinstance(frame, LLMMessagesAppendFrame) and richtung == FrameDirection.DOWNSTREAM:
        return f"Pipeline: Eingabe angekommen ({_zeichen(frame)} Zeichen, Modell sofort: {bool(frame.run_llm)}) von {von}"
    if isinstance(frame, LLMFullResponseStartFrame) and richtung == FrameDirection.DOWNSTREAM:
        return f"Pipeline: Anfrage an das Modell beginnt (von {von})"
    if isinstance(frame, LLMFullResponseEndFrame) and richtung == FrameDirection.DOWNSTREAM:
        return f"Pipeline: Antwort des Modells beendet (von {von})"
    if isinstance(frame, FunctionCallInProgressFrame):
        return f"Pipeline: Werkzeug {str(frame.function_name)[:60]} läuft"
    if isinstance(frame, FunctionCallResultFrame):
        return f"Pipeline: Werkzeug {str(frame.function_name)[:60]} fertig"
    if isinstance(frame, InterruptionFrame) and richtung == FrameDirection.DOWNSTREAM:
        return f"Pipeline: Unterbrechung (von {von})"
    if isinstance(frame, ErrorFrame):
        return f"Pipeline: Fehler von {von}: {str(frame.error)[:200]}"
    return None


class PipelineProtokoll(BaseObserver):
    """Schreibt die Ereignisse einer Sitzung als kurze Zeilen ins Journal."""

    def __init__(self, **kwargs):
        super().__init__(observe_every_push=False, **kwargs)

    async def on_push_frame(self, data: FramePushed):
        zeile = beschreibe(data.frame, data.source, data.direction)
        if zeile:
            logger.info(zeile)
