"""Soniox-TTS, das seinen Stream erst beim ersten Text oeffnet.

Pipecat oeffnet den Soniox-Stream schon, wenn Claude zu antworten beginnt (on_turn_context_created),
damit der erste Ton schneller kommt. Braucht Claude aber lange (am 17.10.2026 einmal 13 s), laeuft der
leere Stream in Soniox' Leerlauf-Timeout: erst "408 request_timeout", dann "400 Stream not found",
und die Antwort bleibt stumm. Hier wird der Stream deshalb erst mit dem ersten Satz geoeffnet.
Kostet ein paar Millisekunden, verliert aber keine Antworten mehr.

Zeit bis zum ersten Ton (Messung: latenz.py, WAMO_LATENZ_LOG=1): Soniox beginnt im Stream erst zu sprechen, wenn rund
65 Zeichen (ohne Leerraum) eingegangen sind oder text_end kommt (Direktverbindung am 21.10.2026: 62 Zeichen warten bis
text_end, 66 starten nach 285 ms; Leerzeichen und Zeilenumbrueche zaehlen nicht). Pipecat gibt den Text satzweise weiter,
bei einem langen ersten Satz ("Eins, zwei, drei, ...") wartet der erste Ton deshalb bis zum Satzende. Der Aggregator
hier gibt den Text schon an einer Wortgrenze frei, sobald die Schwelle erreicht ist (WAMO_TTS_START_ZEICHEN, Standard
68, 0 = Pipecat-Standard). Die Fuge ist fuer Soniox unsichtbar, es sieht einen durchgehenden Text.

Rueckhalt am Textende (Messung 22.10.2026, direkte Verbindung): Soniox spricht nur Text, hinter dem noch etwa 65 Zeichen
Vorschau stehen, oder alles bis text_end. Der Rest eines Satzes erklingt erst, wenn der naechste Text oder text_end eintrifft
(Satz A 95 Zeichen, Satz B 3 s spaeter: Pause von 2 s mitten in A; Satz B allein erklingt erst mit text_end). Solange das
Modell schneller liefert als WAMO spricht, merkt man nichts davon; bei langsamem Modell oder einem Werkzeugaufruf mitten in
der Antwort entsteht daraus Stille. Dagegen hilft der Wartesatz (wartesatz.py), nicht ein anderer Aggregator.

Satzende (WAMO_TTS_SATZLEERZEICHEN, Standard aus bis zur Messung): Pipecat reicht jeden Satz ohne Leerzeichen am Ende
an Soniox. Das Textende ohne Leerraum kann Soniox mit dem Anfang des nächsten Satzes verkleben ("Nutzung.Ich") und das
Satzzeichen vorlesen. fuer_soniox hängt deshalb ein Leerzeichen an; es zählt nicht mit (siehe 65-Zeichen-Schwelle) und
steht nicht im Verlauf, denn der Verlauf kommt aus den Wort-Zeitstempeln.

Stimme, Tempo und Pausen (stimme_einstellung): SONIOX_VOICE, SONIOX_SPEED (0,7 bis 1,3) und SONIOX_REDUCE_SILENCE kommen aus
der Umgebung; leer bleibt die Standardeinstellung von Pipecat und Soniox. Welche Stimme gilt, entscheidet die Hörprobe, nicht
dieser Code.

Gemessen und verworfen: den Stream frueher oeffnen (234 ms bis zum ersten Audio, ob die Konfiguration mit dem Text oder
0,3 s vorher kommt), den Text tokenweise reichen (Pipecat-Modus TOKEN: nach einer Unterbrechung stand nur ein Wort im
Verlauf, Oberflaeche und Kontext waeren falsch) und einen Satzteil am Komma freigeben (unter der Schwelle startet
Soniox nichts).
"""

from __future__ import annotations

import os

from loguru import logger
from websockets.protocol import State

from env_schalter import schalter_an, schalter_oder_none, zahl_im_bereich

from pipecat.services.soniox.tts import SonioxTTSService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.utils.text.base_text_aggregator import Aggregation, AggregationType
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

START_ZEICHEN_STANDARD = 68
TEMPO_MIN, TEMPO_MAX = 0.7, 1.3  # Bereich, den Soniox für speed annimmt


def satz_leerzeichen_an(wert: str | None = None) -> bool:
    """WAMO_TTS_SATZLEERZEICHEN: Leerzeichen am Ende jedes Textstücks für Soniox (Standard aus, bis gemessen)."""
    return schalter_an("WAMO_TTS_SATZLEERZEICHEN", wert, standard=False)


def fuer_soniox(text: str, an: bool | None = None) -> str:
    """Der Text, wie er an Soniox geht: mit einem Leerzeichen am Ende, damit das Satzzeichen nicht mit dem nächsten Satz
    verklebt und vorgelesen wird. an=None liest den Schalter. Leerer Text und Text mit Leerraum am Ende bleiben unverändert."""
    if not (satz_leerzeichen_an() if an is None else an):
        return text
    return text if not text or text[-1].isspace() else text + " "


def stimme_einstellung(speed: str | None = None, reduce_silence: str | None = None) -> dict:
    """Zusätzliche Einstellungen der Stimme aus der Umgebung: speed (SONIOX_SPEED, 0,7 bis 1,3) und reduce_silence
    (SONIOX_REDUCE_SILENCE, an oder aus). Nur gesetzte und gültige Werte stehen im Ergebnis; ein ungültiger Wert wird
    mit Warnung ignoriert, die Stimme bleibt dann beim Standard. Die Parameter ersetzen die Umgebung (für Tests)."""
    out: dict = {}
    tempo = zahl_im_bereich("SONIOX_SPEED", TEMPO_MIN, TEMPO_MAX, speed)
    if tempo is not None:
        out["speed"] = tempo
    pausen = schalter_oder_none("SONIOX_REDUCE_SILENCE", reduce_silence)
    if pausen is not None:
        out["reduce_silence"] = pausen
    return out


def start_zeichen(wert: str | None = None) -> int:
    """Zeichen ohne Leerraum, ab denen der erste Text an Soniox geht (WAMO_TTS_START_ZEICHEN), 0 = aus."""
    w = os.getenv("WAMO_TTS_START_ZEICHEN") if wert is None else wert
    if w is None or not w.strip():
        return START_ZEICHEN_STANDARD
    try:
        return max(0, int(w.strip()))
    except ValueError:
        return START_ZEICHEN_STANDARD


def _zeichen(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


class StartAggregator(SimpleTextAggregator):
    """Satz-Aggregator, der den Anfang einer Antwort an einer Wortgrenze freigibt, sobald Soniox genug Text hat.

    Zaehlt je Antwort die Zeichen (ohne Leerraum), die schon an die Sprachausgabe gingen. Erreichen sie mit dem Puffer
    die Schwelle, geht der Puffer ohne Satzende raus; danach gilt wieder die Satzgrenze. Kurze Antworten bleiben
    unveraendert (sie erreichen die Schwelle nie, Soniox wartet dann auf text_end)."""

    def __init__(self, *, schwelle: int = START_ZEICHEN_STANDARD, **kwargs):
        super().__init__(**kwargs)
        self._schwelle = schwelle
        self._gesendet = 0

    async def _check_sentence_with_lookahead(self, char: str) -> Aggregation | None:
        satz = await super()._check_sentence_with_lookahead(char)
        if satz is not None:
            self._gesendet += _zeichen(satz.text)
            return satz
        if self._gesendet >= self._schwelle or not char.isspace() or self._text[-2:-1].isspace():
            return None
        if self._gesendet + _zeichen(self._text) < self._schwelle:
            return None
        teil = self._text.strip()
        await SimpleTextAggregator.reset(self)  # Puffer und Vorschau leeren, Zaehler bleibt
        self._gesendet += _zeichen(teil)
        # Mit Leerzeichen am Ende: Pipecat reicht den Text unverändert an Soniox, der Rest des Satzes beginnt ohne Leerzeichen.
        # An einer Satzgrenze trennt das Satzzeichen die Teile, hier mitten im Satz müsste sonst "des" + "Landes" zu "desLandes"
        # werden.
        return Aggregation(text=teil + " ", type=AggregationType.SENTENCE)

    async def flush(self) -> Aggregation | None:
        try:
            return await super().flush()
        finally:
            self._gesendet = 0

    async def handle_interruption(self):
        await super().handle_interruption()
        self._gesendet = 0

    async def reset(self):
        await super().reset()
        self._gesendet = 0


class LazySonioxTTSService(SonioxTTSService):
    def __init__(self, *args, start: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        schwelle = start_zeichen() if start is None else start
        if schwelle > 0 and self._text_aggregation_mode == TextAggregationMode.SENTENCE:
            self._text_aggregator = StartAggregator(
                schwelle=schwelle,
                aggregation_type=self._text_aggregation_mode,
                language=self._text_aggregator.language,
            )

    async def on_turn_context_created(self, context_id: str):
        # Bewusst nichts tun: kein Stream ohne Text (siehe Modul-Docstring).
        return

    async def run_tts(self, text: str, context_id: str):
        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()
            await self._send_config(context_id)  # idempotent, nur beim ersten Satz wirksam
        except Exception as e:
            logger.warning(f"{self}: Soniox-Stream {context_id} konnte nicht geoeffnet werden: {e}")
        async for frame in super().run_tts(fuer_soniox(text), context_id):
            yield frame
