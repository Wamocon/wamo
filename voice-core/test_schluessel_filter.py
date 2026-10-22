"""Schutzstufe gegen gesprochene oder getippte Schlüssel (schluessel_filter.py) und die Hilfe in der Persona.
Der Schlüssel darf nirgends ankommen: nicht in den Frames hinter der Spracherkennung (Verlauf, Modell, Lernen,
Gedächtnis, turns, actions), nicht bei den Beobachtern (Untertitel) und nicht im Log."""

from __future__ import annotations

import pytest
from loguru import logger

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMMessagesAppendFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.observers.base_observer import BaseObserver
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import run_test

import schluessel as sl
import schluessel_filter as sf
from persona import build_system_prompt

API = "sk-ant-api03-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99"
TOKEN = "sk-ant-oat01-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99"
GEHEIM = ("GEHEIMgeheim", "0123456789abcdefghij", "Zz99")


def _ohne_geheimnis(text: str) -> bool:
    return not any(teil in text for teil in GEHEIM)


@pytest.mark.parametrize("satz", [
    f"Mein Schlüssel ist {API} und jetzt?",
    f"Der Token lautet {TOKEN}.",
    f"{TOKEN}",
    "mein schlüssel ist sk ant api null drei A B C D E F G H I J K",
    "S K Strich A N T Strich O A T null eins A B C D E F G H",
    "sk-ant-oat01-abc def ghi jkl mno",
    "SK-ANT-API03-Abcd1234 Efgh5678",
])
def test_schluessel_wird_entfernt(satz):
    neu, geaendert = sf.entfernen(satz)
    assert geaendert and sf.MARKE in neu
    assert "GEHEIM" not in neu and "0123456789" not in neu and "Zz99" not in neu
    assert "sk-ant" not in neu.lower()


@pytest.mark.parametrize("satz", [
    "Wo trage ich meinen Schlüssel ein?",
    "Die Antwort auf die Frage ist schnell da.",
    "Ich skizziere kurz den Ablauf, dann fangen wir an.",
    "sk ist die Abkürzung, ant steht für Ameise",
    "Mein Anthropic-Schlüssel beginnt mit sk, den sage ich nicht.",
    "",
])
def test_normale_saetze_bleiben_unveraendert(satz):
    assert sf.entfernen(satz) == (satz, False)


def test_satz_bleibt_sonst_erhalten():
    neu, _ = sf.entfernen(f"Hier: {TOKEN}")
    assert neu == f"Hier: {sf.MARKE}"


class _Dienst:
    """Stand-in für einen Spracherkennungsdienst: push_frame leitet an Beobachter weiter."""

    def __init__(self):
        self.gesehen: list = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.gesehen.append(frame)


async def test_absichern_bereinigt_vor_beobachtern_und_nachfolgern(caplog):
    zeilen: list[str] = []
    senke = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    try:
        dienst = sf.absichern(_Dienst())
        fertig = TranscriptionFrame(f"Mein Schlüssel: {API}", "u1", "2026-10-22T08:00:00Z", result={"worte": [API]})
        zwischen = InterimTranscriptionFrame(f"Mein Schlüssel: {TOKEN}", "u1", "2026-10-22T08:00:00Z")
        normal = TranscriptionFrame("Wie spät ist es?", "u1", "2026-10-22T08:00:01Z")
        for f in (fertig, zwischen, normal):
            await dienst.push_frame(f)
    finally:
        logger.remove(senke)
    texte = [f.text for f in dienst.gesehen]
    assert texte == [f"Mein Schlüssel: {sf.MARKE}", f"Mein Schlüssel: {sf.MARKE}", "Wie spät ist es?"]
    assert dienst.gesehen[0].result is None  # das Rohergebnis des Dienstes enthielte den Schlüssel
    protokoll = "\n".join(zeilen)
    assert _ohne_geheimnis(protokoll) and "wurde entfernt" in protokoll


class _Sammler(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.texte: list[str] = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            self.texte.append(frame.text)
        await self.push_frame(frame, direction)


async def test_zweite_stufe_in_der_pipeline():
    nach, _ = await run_test(
        sf.SchluesselFilter(),
        frames_to_send=[TranscriptionFrame(f"Schlüssel {TOKEN}", "u1", "2026-10-22T08:00:00Z"),
                        LLMMessagesAppendFrame(messages=[{"role": "user", "content": f"Hier {API}"},
                                                         {"role": "assistant", "content": f"Eigener Text {API}"}],
                                               run_llm=False)],
        expected_down_frames=[TranscriptionFrame, LLMMessagesAppendFrame],
    )
    assert nach[0].text == f"Schlüssel {sf.MARKE}"
    benutzer, assistent = nach[1].messages
    assert benutzer["content"] == f"Hier {sf.MARKE}"
    assert assistent["content"] == f"Eigener Text {API}"  # nur Nutzersätze werden angefasst


async def test_beobachter_sehen_nur_den_bereinigten_satz():
    """Wie im Betrieb: Der Dienst ERZEUGT den Satz (Eingang von außen), die Beobachter sehen jeden Hop."""

    class Erzeuger(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TextFrame) and frame.text == "los":
                await self.push_frame(TranscriptionFrame(f"Mein Token: {TOKEN}", "u1", "2026-10-22T08:00:00Z"))
            else:
                await self.push_frame(frame, direction)

    class Beobachter(BaseObserver):
        def __init__(self):
            super().__init__()
            self.texte: list[str] = []

        async def on_push_frame(self, data):
            if isinstance(data.frame, TranscriptionFrame):
                self.texte.append(data.frame.text)

    beobachter = Beobachter()
    nach, _ = await run_test(
        sf.absichern(Erzeuger()),
        frames_to_send=[TextFrame("los")],
        expected_down_frames=[TranscriptionFrame],
        observers=[beobachter],
    )
    assert beobachter.texte and all(_ohne_geheimnis(t) and sf.MARKE in t for t in beobachter.texte)
    assert nach[0].text == f"Mein Token: {sf.MARKE}"

def test_log_schwaerzt_schluessel_und_claude_code_token():
    for wert in (API, TOKEN):
        assert _ohne_geheimnis(sl.schwaerzen(f"Fehler bei {wert} aufgetreten"))
    assert sl.schwaerzen("kein Schlüssel hier") == "kein Schlüssel hier"


def test_persona_kennt_die_karte_und_verbietet_diktieren():
    prompt = build_system_prompt("Niko", "du")
    for stueck in ("Karte „Modellzugang“", "linken Spalte", "API-Schlüssel", "Claude-Code-Token", "claude setup-token",
                   "console.anthropic.com", "[Schlüssel entfernt]", "Bitte nicht aussprechen oder schreiben",
                   "ab der nächsten Sitzung"):
        assert stueck in prompt, stueck
    assert "Fordere nie dazu auf, einen Schlüssel oder Token zu diktieren" in prompt
    zugang = prompt[prompt.index("Schlüssel und Modellzugang"):]
    assert not any(c in zugang[:900] for c in "–—")  # keine Gedankenstriche
    assert len(zugang.split("\nGedächtnis")[0]) < 1400  # kurz: wenige Zeilen im Prompt
