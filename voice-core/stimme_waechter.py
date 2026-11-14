"""Nur diese Stimme hören: eine sicher fremde Äußerung erreicht das Sprachmodell nicht (Modus streng, nur mit eingelerntem Abdruck).

Der Wächter sitzt hinter der Spracherkennung und vor dem Aggregator des Nutzers. Er hält ausschließlich das endgültige Transkript einer
Äußerung (TranscriptionFrame) an, bis das Urteil des Prüfers (stimme_pruefer.py) da ist, höchstens WARTEN_SECS. Die Prüfung beginnt beim
Sprechende und läuft parallel zur Spracherkennung, die Antwortzeit wächst für die eigene Stimme also höchstens um den Rest der Prüfzeit, der
über die Wartezeit der Spracherkennung hinausgeht. Zwischenstände (InterimTranscriptionFrame) laufen ungebremst durch: Unterbrechen durch die
Person bleibt so schnell wie ohne Prüfung.

Verworfen wird nur, wenn alles zutrifft: Modus streng, Abdruck und Modell bereit, das Urteil ist fremd (Wert unter der Fremd-Schwelle), die
Äußerung hatte genug Sprache (min_secs) und WAMO sprach nicht gleichzeitig (sonst ist das Urteil unklar, siehe StimmGate.einstufen). Alles
andere geht durch: unklar, kurz, kein Urteil in der Frist (Zeitüberschreitung gilt als unklar), beobachten, aus, kein Abdruck. Werkzeuge bleiben
dann trotzdem gesperrt, wenn die Stimme nicht sicher erkannt ist (StimmGate.pruefen).

Verwerfen heißt: Das Transkript wird durch ein Leerzeichen ersetzt. Der Zug des Nutzers endet dann leer: kein Satz im Verlauf, nichts für
das Gedächtnis, kein Aufruf des Modells. bot.py kehrt in on_user_turn_stopped für einen solchen Zug zurück (zug_verworfen). Dazu geht
eine Meldung an die Oberfläche: {"type": "stimme", "ergebnis": "fremd", "wert", "modus", "ignoriert": true}, einmal je Urteil.

Grenzen: Die Untertitel der Oberfläche können den Satz noch zeigen (sie entstehen schon bei der Spracherkennung). Spricht jemand Fremdes
dazwischen, während WAMO redet, ist das Urteil unklar und der Satz geht durch (nur Werkzeuge bleiben gesperrt).
"""

from __future__ import annotations

from dataclasses import replace

from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

from stimme_gate import FREMD, StimmGate

WARTEN_SECS = 1.5  # so lange hält das endgültige Transkript höchstens auf das Urteil


class StimmWaechter(FrameProcessor):
    def __init__(self, gate: StimmGate, pruefer, *, warten: float = WARTEN_SECS, **kwargs):
        super().__init__(**kwargs)
        self._gate = gate
        self._pruefer = pruefer
        self._warten = warten
        self._verworfen = False
        self._gemeldet_nr = 0

    def zug_verworfen(self) -> bool:
        """Wurde seit dem letzten Aufruf ein Transkript verworfen? Setzt zurück. Für on_user_turn_stopped in bot.py."""
        verworfen, self._verworfen = self._verworfen, False
        return verworfen

    def _aktiv(self) -> bool:
        return self._gate.modus == "streng" and self._gate.status == "bereit"

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame) and self._aktiv():
            frame = await self._pruefen(frame)
        await self.push_frame(frame, direction)

    async def _pruefen(self, frame: TranscriptionFrame) -> TranscriptionFrame:
        if not frame.text.strip():
            return frame
        urteil = await self._pruefer.urteil_abwarten(self._warten)  # None: Frist abgelaufen oder nichts geprüft, gilt als unklar
        if urteil is None or urteil.urteil != FREMD or urteil.wert is None or urteil.secs < self._gate.schwellen.min_secs:
            return frame
        if urteil.nr != self._gemeldet_nr:
            self._gemeldet_nr = urteil.nr
            logger.info("Stimme: fremde Äußerung ignoriert ({:.2f} bei {:.1f} s)", urteil.wert, urteil.secs)
            await self.push_frame(RTVIServerMessageFrame(data={
                "type": "stimme", "ergebnis": FREMD, "wert": round(float(urteil.wert), 2), "modus": self._gate.modus,
                "ignoriert": True,
            }))
        self._verworfen = True
        return replace(frame, text=" ", result=None)  # ein neues Frame: Beobachter der Pipeline halten das Original
