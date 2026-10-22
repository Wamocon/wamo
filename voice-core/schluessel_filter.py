"""Schutzstufe gegen gesprochene oder getippte Schlüssel: Erkennt der Kern in einem Nutzersatz (Transkript der
Spracherkennung, Text aus der Oberfläche) ein Muster wie sk-ant-… (API-Schlüssel oder Claude-Code-Token), ersetzt er
es durch "[Schlüssel entfernt]", bevor irgendetwas anderes den Satz sieht: Verlauf, Modell, Lernen, Gedächtnis,
Protokoll (turns, actions) und die Untertitel der Oberfläche. Die Persona sagt dann freundlich, dass der Schlüssel in
die Karte Modellzugang gehört.

Warum: Gesprochener Text geht an den Spracherkennungsdienst, in den Verlauf und ins Modell. Ein Schlüssel gehört dort
nie hin. Der Filter sitzt deshalb an der Quelle: absichern() ersetzt push_frame des Spracherkennungsdienstes, sodass
schon die Beobachter der Pipeline (Untertitel) nur noch den bereinigten Satz sehen; SchluesselFilter fängt zusätzlich
Text ab, der nicht aus der Spracherkennung kommt (RTVI-Texteingabe). Im Log steht nur, DASS etwas entfernt wurde.

Erkannt werden die genaue Form (sk-ant-api03-…, sk-ant-oat01-…) und die gesprochene Form ("S K Strich Ant Strich O A T
null eins …": Buchstaben und Zahlwörter mit Leerzeichen oder Bindestrichen), jeweils samt dem folgenden Schlüsseltext.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

MARKE = "[Schlüssel entfernt]"
# Genaue Form (wie schluessel.schwaerzen, nur kürzer vorausgesetzt)
_GENAU = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}", re.IGNORECASE)
# Gesprochen oder mit Leerzeichen getrennt: "sk ant api 03 …", "s k - a n t - o a t 0 1 …", "sk-ant-oat01-abc def …"
_SEP = r"(?:[\s\-_–—.]|strich|minus|bindestrich|dash){0,3}"  # auch gesprochene Trennwörter
_SKANT = rf"\bs{_SEP}k{_SEP}a{_SEP}n{_SEP}t"
_ART = rf"(?:api|oat|a{_SEP}p{_SEP}i|o{_SEP}a{_SEP}t)"
_LOCKER = re.compile(rf"{_SKANT}{_SEP}{_ART}{_SEP}\d{{0,2}}(?:{_SEP}[A-Za-z0-9]{{1,6}}){{3,60}}", re.IGNORECASE)
# Der Anfang allein ("sk ant oat") reicht auch: dahinter folgt ein Schlüssel, der nur nicht erkannt wurde
_ANFANG = re.compile(rf"{_SKANT}{_SEP}{_ART}\w*", re.IGNORECASE)


def entfernen(text: str) -> tuple[str, bool]:
    """(bereinigter Text, wurde etwas entfernt). Ohne Treffer bleibt der Text unverändert."""
    if not text:
        return text, False
    neu = _GENAU.sub(MARKE, text)
    neu = _LOCKER.sub(MARKE, neu)
    neu = _ANFANG.sub(MARKE, neu)
    return neu, neu != text


def _bereinigen(frame: Frame) -> bool:
    """Ersetzt Schlüssel im Text des Frames. True, wenn etwas entfernt wurde."""
    if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
        neu, geaendert = entfernen(frame.text)
        if geaendert:
            frame.text = neu
            frame.result = None  # das Rohergebnis des Dienstes enthielte den Schlüssel in Wortform
        return geaendert
    if isinstance(frame, LLMMessagesAppendFrame):
        geaendert = False
        for nachricht in frame.messages or []:
            inhalt = nachricht.get("content") if isinstance(nachricht, dict) else None
            if nachricht.get("role") == "user" and isinstance(inhalt, str):
                neu, g = entfernen(inhalt)
                if g:
                    nachricht["content"] = neu
                    geaendert = True
        return geaendert
    return False


def absichern(dienst: Any) -> Any:
    """Der Spracherkennungsdienst gibt nur noch bereinigte Sätze weiter (auch an die Beobachter der Pipeline)."""
    original: Callable[..., Awaitable[None]] = dienst.push_frame

    async def push_frame(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        if _bereinigen(frame) and not isinstance(frame, InterimTranscriptionFrame):
            logger.warning("Ein Schlüssel im Gesagten wurde entfernt (nicht gespeichert, nicht ans Modell gegeben)")
        await original(frame, direction)

    dienst.push_frame = push_frame
    return dienst


class SchluesselFilter(FrameProcessor):
    """Zweite Stufe hinter der Spracherkennung: bereinigt auch Text, der nicht aus der Spracherkennung kommt."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if _bereinigen(frame) and not isinstance(frame, InterimTranscriptionFrame):
            logger.warning("Ein Schlüssel in einer Eingabe wurde entfernt (nicht gespeichert, nicht ans Modell gegeben)")
        await self.push_frame(frame, direction)
