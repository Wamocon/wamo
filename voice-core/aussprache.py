"""Aussprache für die Sprachausgabe: Namen so schreiben, wie die deutsche Stimme sie richtig spricht.

Der Assistent heißt geschrieben W.A.I.S., kurz Wais, gesprochen wie das englische "ways". Die deutsche Stimme liest "Wais"
wie "weiß" und "W.A.I.S." als einzelne Buchstaben; das englische "ways" trifft sie nur mit der Schreibweise NAME_TTS
(persona.py, Hörprobe vom 14.11.2026). In der englischen Langform liest sie "WAMOCON’s" als "Waymo Cons", "Wamokons" klingt
wie die Firma. "WAMOCON GmbH" liest sie schon richtig, das bleibt. Buchstabiert ("W, A, I, S") bleibt ebenfalls.

fuer_sprachausgabe ändert nur den Text, der an die Sprachausgabe geht. In der Pipeline hängt sie als Text-Transform am
TTS-Dienst (anhaengen, Pipecat add_text_transformer): Pipecat wendet Transforms nach den Textfiltern an und schickt nur das
Ergebnis an die Stimme. Was im Verlauf und in der Oberfläche steht, bleibt "Wais": Dienste ohne Wortzeitstempel (Sprachausgabe
im Haus) schreiben den Text vor dem Transform in den Verlauf; bei Soniox ordnet Pipecat (WordCompletionTracker) jedes
gesprochene Wort ("Ways") dem Text des Modells ("Wais") zu, der Verlauf bekommt diesen (raw_text), die Untertitel
(bot-output) den angezeigten Text. Ein Textfilter wäre falsch: dessen Ergebnis landet bei Diensten ohne Wortzeitstempel im
Verlauf. Feste Ansagen (TTSSpeakFrame: Begrüßung, Wartesätze, Stimmprüfung, Live-Bild) laufen durch denselben Dienst und
damit durch den Transform. Nur die vorab aufgenommenen Füllwörter gehen an der Pipeline vorbei direkt an Soniox
(fuellwoerter.soniox_sprechen) und rufen fuer_sprachausgabe selbst auf.
"""

from __future__ import annotations

import re

from persona import NAME_TTS

FIRMA_TTS = "Wamokons"  # "WAMOCON’s" (englische Langform) für die Stimme

# "W.A.I.S." ohne Leerzeichen dazwischen, auch ohne den letzten Punkt ("W.A.I.S," mitten im Satz)
_ANZEIGE = re.compile(r"(?<![\w.])W\.A\.I\.S\b(\.?)")
# Die Kurzform als ganzes Wort, gleich in welcher Schreibung ("Wais", "wais", "WAIS"); "Waise" und "weiß" bleiben
_KURZ = re.compile(r"\bwais\b", re.IGNORECASE)
# Englischer Genitiv der Firma mit geradem oder typografischem Apostroph
_FIRMA_GENITIV = re.compile(r"\bWAMOCON['’]s\b", re.IGNORECASE)


def _anzeige(treffer: re.Match[str]) -> str:
    # Am Ende des Textes ist der Punkt zugleich das Satzende: er bleibt, damit die Stimme den Satz abschließt.
    am_ende = treffer.group(1) and not treffer.string[treffer.end():].strip()
    return NAME_TTS + ("." if am_ende else "")


def fuer_sprachausgabe(text: str) -> str:
    """Der Text, wie ihn die Stimme bekommt: W.A.I.S. und Wais werden NAME_TTS, "WAMOCON’s" wird "Wamokons"."""
    if not text:
        return text
    text = _ANZEIGE.sub(_anzeige, text)
    text = _KURZ.sub(NAME_TTS, text)
    return _FIRMA_GENITIV.sub(FIRMA_TTS, text)


async def _transform(text: str, _art: object) -> str:
    return fuer_sprachausgabe(text)


def anhaengen(tts) -> None:
    """Hängt fuer_sprachausgabe als Transform an einen Pipecat-TTS-Dienst (für jede Art von Text)."""
    tts.add_text_transformer(_transform, "*")
