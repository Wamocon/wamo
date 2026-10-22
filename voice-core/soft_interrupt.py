"""Weiche Unterbrechung: WAMO spricht den Satz zu Ende, statt mitten im Wort abzubrechen.

Ablauf
  * Die Start-Strategie meldet den Turn der Person OHNE sofortige Unterbrechung
    (MinWordsUserTurnStartStrategy(enable_interruptions=False)).
  * SoftInterruptGate (zwischen user_agg und llm) merkt: die Person redet, während WAMO spricht.
  * PlaybackTracker (nach transport.output) meldet jedes Wort, sobald es laut Zeitstempel beginnt.
  * Unterbrochen wird an der nächsten Satzgrenze, spätestens aber nach dem laufenden Wort plus
    max_words weiteren Wörtern, also immer an einer Wortgrenze.
  * Die Wort-Zeitstempel laufen dem hörbaren Ton voraus (Puffer im WebRTC-Pfad). Deshalb wird
    die Unterbrechung um playback_lag_secs verzögert; Wörter in dieser Spanne gelten als nicht
    gesprochen.
  * Endet der Turn der Person vorher, hält das Gate die neue Anfrage an Claude zurück, bis der
    gesprochene Text von WAMO im Verlauf steht, und sortiert den Einwurf dahinter ein. Sonst sieht
    Claude die eigene Antwort nicht und fängt von vorn an.
  * Spricht WAMO noch nicht (Claude denkt noch), wird wie bisher sofort unterbrochen.
  * Zeitgrenze: Kommen keine Wort-Zeitstempel (Sprachausgabe im Haus liefert nur ganze Sätze), unterbricht das Gate
    spätestens max_wait_secs nach dem Einwurf. Die Grenze macht Unterbrechungen nur früher, nie später.
  * Echo: Hört das Mikrofon die eigene Stimme (Lautsprecher und Mikrofon am selben PC, Echounterdrückung schwach), erkennt
    die Spracherkennung WAMOs eigene Wörter und löste sonst eine Unterbrechung aus. Ein Einwurf aus zwei oder drei Wörtern,
    die WAMO in den letzten Sekunden in genau dieser Folge selbst gesprochen hat, oder ein langer, der zu mindestens 70 Prozent
    aus WAMOs Text der letzten halben Minute in dessen Reihenfolge besteht, gilt als Echo und zählt nicht
    (WAMO_ECHO_FILTER=aus schaltet das ab). Ein einzelnes Wort aus WAMOs letzten Sekunden ("Montag" nach "Montag oder
    Dienstag?") zählt erst, wenn die Erkennung es endgültig meldet; Echo läuft weiter und wird dann zu einer Folge.
    Stoppwörter, Fragewörter, Verneinungen und alles, was WAMO nicht gesagt hat, wirken sofort wie bisher.
  * Verlauf: Das Gate wartet nach der Unterbrechung nur dann auf den gesprochenen Text, wenn ein Sprachabschnitt läuft,
    dessen Text noch nicht im Verlauf steht. Steht er schon da (die Antwort war fertig, nur der Ton lief noch) oder
    wurde nichts gesprochen, geht es sofort weiter statt eine Sekunde zu warten.
"""

from __future__ import annotations

import asyncio
import difflib
import os
import re
import time
from collections import deque
from collections.abc import Callable

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    TTSStartedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)

SENTENCE_END = (".", "!", "?", "…", ":")

# Rueckmeldelaute: zeigen Zuhoeren, sind kein Einwurf. Alles andere zaehlt ("Doch", "Nein", "Stopp").
BACKCHANNEL_WORDS = frozenset(
    {
        "mhm", "mhmm", "mm", "hm", "hmm", "ähm", "äh", "aha", "ah", "oh", "ok", "okay",
        "ja", "jaja", "jo", "genau", "stimmt", "richtig", "klar", "super", "gut", "cool",
        "achso", "ach", "so", "verstehe", "uh", "huh",
    }
)


def meaningful_word_count(text: str) -> int:
    """Zaehlt echte Woerter. Einzelne Buchstaben sind Bruchstuecke der Live-Erkennung
    ("J" vor "Ja", "H" vor "Hi") und zaehlen nicht."""
    words = (w.strip(".,!?…:;-\"'").lower() for w in text.split())
    return sum(1 for w in words if len(w) >= 2 and w not in BACKCHANNEL_WORDS)


# Wer eines dieser Wörter sagt, will WAMO anhalten, etwas wissen oder widersprechen: nie Echo, sofort ein Einwurf
STOPP_WORDS = frozenset(
    {
        "stopp", "stop", "halt", "warte", "warten", "moment", "pause", "genug", "nein", "doch", "danke", "sekunde",
        "augenblick", "bitte", "entschuldigung", "aufhören", "hör", "hoer", "ruhig", "leise", "still",
        "was", "wie", "wer", "wo", "wann", "warum", "wieso", "weshalb", "welche", "welcher", "welches", "nicht", "kein",
        "keine", "nie", "nichts", "falsch", "quatsch", "echt", "wirklich", "sorry", "hallo",
    }
)
SATZZEICHEN = ".,!?…:;-()'" + chr(34)
# Die Erkennung schreibt Zahlen als Ziffern ("13"), WAMO spricht "dreizehn": eine Ziffer passt zu einem gesprochenen Zahlwort
ZAHLWORT = re.compile(
    "(?:sechzehn|siebzehn|zwanzig|dreißig|vierzig|fünfzig|sechzig|siebzig|achtzig|neunzig|hundert|tausend|zwölf|sieben"
    "|sechs|fünf|vier|drei|zwei|eins|null|neun|acht|zehn|elf|ein|und)+"
)
ECHO_FENSTER_KURZ_SECS = 6.0  # kurze Einwürfe (bis drei Wörter) gelten nur gegen den Text der letzten Sekunden
ECHO_FENSTER_SECS = 30.0  # ein langes Echo hat als Transkript die ganze bisherige Äußerung, also auch ältere Wörter
ECHO_LANG = 4  # ab so vielen bedeutsamen Wörtern zählt der Anteil in der richtigen Reihenfolge, nicht jedes Wort
ECHO_ANTEIL = 0.7  # so viel vom langen Einwurf muss in WAMOs Text stehen (Verhörer der Erkennung sind normal)
ECHO_AEHNLICH = 0.8  # Ähnlichkeit, ab der die Erkennung ein gesprochenes Wort als dasselbe zählt ("Frankreichs")
ECHO_AEHNLICH_MIN_LAENGE = 6  # kürzere Wörter müssen genau stimmen ("wer" ist nicht "werden")
ECHO_LUECKE = 1  # so viele gespielte Wörter darf die Erkennung zwischen zwei erkannten überhören


def echo_filter_an(wert: str | None = None) -> bool:
    w = os.getenv("WAMO_ECHO_FILTER") if wert is None else wert
    return (w or "an").strip().lower() not in ("aus", "0", "nein", "false")


def _wort(w: str) -> str:
    return w.strip(SATZZEICHEN).lower()


def _ist_ziffer(w: str) -> bool:
    return w.replace(",", "").replace(".", "").isdigit()


def _passt(w: str, g: str, *, letztes: bool) -> bool:
    """Ist das erkannte Wort w das gespielte Wort g? Das letzte Wort darf ein Anfang sein (Live-Erkennung: "Hauptst")."""
    if w == g:
        return True
    if _ist_ziffer(w):
        return g != "und" and ZAHLWORT.fullmatch(g) is not None
    if letztes and len(w) >= 2 and g.startswith(w):
        return True
    return (
        min(len(w), len(g)) >= ECHO_AEHNLICH_MIN_LAENGE
        and abs(len(g) - len(w)) <= 3
        and difflib.SequenceMatcher(None, w, g).ratio() >= ECHO_AEHNLICH
    )


def _folge_gespielt(erkannt: list[str], gespielt: list[str]) -> bool:
    """Steht die erkannte Wortfolge zusammenhängend (mit höchstens ECHO_LUECKE überhörten Wörtern) in dem Gespielten?"""
    n = len(erkannt)
    for start in range(len(gespielt)):
        if not _passt(erkannt[0], gespielt[start], letztes=n == 1):
            continue
        j = start
        for i in range(1, n):
            for k in range(j + 1, j + 2 + ECHO_LUECKE):
                if k < len(gespielt) and _passt(erkannt[i], gespielt[k], letztes=i == n - 1):
                    j = k
                    break
            else:
                break
        else:
            return True
    return False


def _anteil_gespielt(erkannt: list[str], gespielt: list[str]) -> float:
    """Anteil der erkannten Wörter, die in der Reihenfolge des Gespielten wiederkommen (Verhörer zählen nicht mit).

    Ziffern ("13") zählen, wenn WAMO irgendein Zahlwort gespielt hat; welches, lässt sich nicht zuordnen."""
    bekannt = list(dict.fromkeys(gespielt))
    ziffern = sum(1 for w in erkannt if _ist_ziffer(w))
    rest = [w for w in erkannt if not _ist_ziffer(w)]
    zahl_gespielt = any(g != "und" and ZAHLWORT.fullmatch(g) for g in bekannt)
    treffer = ziffern if zahl_gespielt else 0
    if rest:
        kanon: list[str] = []
        for i, w in enumerate(rest):
            gleich = next((g for g in bekannt if _passt(w, g, letztes=i == len(rest) - 1)), None)
            kanon.append(gleich if gleich is not None else "∅" + w)  # nie gleich einem gespielten Wort
        blocks = difflib.SequenceMatcher(None, kanon, gespielt, autojunk=False).get_matching_blocks()
        treffer += sum(b.size for b in blocks)
    return treffer / len(erkannt)


def echo_urteil(text: str, kurz: list[str], lang: list[str] | None = None) -> str:
    """Ist der Einwurf das Echo der eigenen Stimme?

    kurz: WAMOs letzte Sekunden, lang: die letzten dreißig Sekunden (Standard: wie kurz).
    "echo": zwei oder drei Wörter in genau der Folge, die WAMO gerade gespielt hat, oder ein langes Transkript, das zu
    mindestens ECHO_ANTEIL aus WAMOs Text in dessen Reihenfolge besteht, oder eine einzelne Zahl, während WAMO Zahlen
    spricht. "unklar": ein Wort, das WAMO gerade gespielt hat, oder ein Wortanfang (Echo oder knappe Antwort, erst der
    endgültige Text entscheidet). Sonst "einwurf"."""
    lang = kurz if lang is None else lang
    erkannt = [w for w in (_wort(x) for x in text.split()) if w]
    if len(erkannt) > 1 and len(erkannt[-1]) <= 2 and not _ist_ziffer(erkannt[-1]):
        erkannt = erkannt[:-1]  # Bruchstück am Ende ("Gern, ich z"): das Wort ist noch nicht zu Ende gesprochen
    bedeutsam = [w for w in erkannt if (len(w) >= 2 or _ist_ziffer(w)) and w not in BACKCHANNEL_WORDS]
    if not bedeutsam or not lang:
        return "einwurf"
    if len(bedeutsam) >= ECHO_LANG:
        return "echo" if _anteil_gespielt(erkannt, lang) >= ECHO_ANTEIL else "einwurf"
    if not kurz or any(w in STOPP_WORDS for w in bedeutsam):
        return "einwurf"
    if len(bedeutsam) >= 2:
        return "echo" if _folge_gespielt(erkannt, kurz) else "einwurf"
    w = bedeutsam[0]
    if _ist_ziffer(w):  # eine einzelne Zahl, während WAMO Zahlen spricht (Zählen, Vorlesen): Echo, keine Antwort
        return "echo" if any(_passt(w, g, letztes=True) for g in kurz) else "einwurf"
    if len(w) <= 2:
        return "unklar"  # Wortanfang der Live-Erkennung ("En", "Ke"): erst der endgültige Text entscheidet
    return "unklar" if any(_passt(w, g, letztes=True) for g in kurz) else "einwurf"


def ist_echo(text: str, kurz: list[str], lang: list[str] | None = None) -> bool:
    return echo_urteil(text, kurz, lang) == "echo"


class MeaningfulWordsUserTurnStartStrategy(MinWordsUserTurnStartStrategy):
    """Wie MinWords, aber während WAMO spricht, zählen Rückmeldelaute nicht mit.

    "mhm" oder "ja genau" lassen WAMO weiterreden, ein einzelnes "Doch" oder "Stopp" unterbricht.
    Spricht WAMO nicht, startet wie bisher jedes Wort den Turn.

    gesprochen: liefert zu einer Zahl von Sekunden die Wörter, die WAMO so lange zuletzt gespielt hat, in der Reihenfolge
    (SoftInterruptGate.kuerzlich_gesprochen). Besteht ein Einwurf aus einer Folge davon, ist es das Echo der eigenen
    Stimme und zählt nicht (echo_urteil).
    """

    def __init__(self, *, gesprochen: Callable[[float], list[str]] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._gesprochen = gesprochen
        self.echo_verworfen = 0  # für Tests und das Log
        self._echo_gemeldet = 0.0

    def _urteil(self, text: str) -> str:
        if self._gesprochen is None:
            return "einwurf"
        return echo_urteil(text, self._gesprochen(ECHO_FENSTER_KURZ_SECS), self._gesprochen(ECHO_FENSTER_SECS))

    async def _handle_transcription(self, frame) -> ProcessFrameResult:
        if not self._bot_speaking:
            return await super()._handle_transcription(frame)
        count = meaningful_word_count(frame.text)
        if count >= self._min_words:
            urteil = self._urteil(frame.text)
            if urteil == "echo":
                self.echo_verworfen += 1
                jetzt = time.monotonic()
                if jetzt - self._echo_gemeldet > 5.0:  # höchstens alle 5 s, ohne Inhalt
                    self._echo_gemeldet = jetzt
                    logger.info(f"{self}: Echo der eigenen Stimme verworfen ({count} Wörter, insgesamt {self.echo_verworfen})")
                await self.trigger_reset_aggregation()
                return ProcessFrameResult.CONTINUE
            if urteil == "unklar" and isinstance(frame, InterimTranscriptionFrame):
                return ProcessFrameResult.CONTINUE  # erst der endgültige Text entscheidet (Echo wächst zu einer Folge)
            logger.info(f"{self}: Einwurf erkannt ({count} Wörter)")
            logger.debug(f"{self}: Einwurf erkannt ({frame.text!r})")
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        await self.trigger_reset_aggregation()
        return ProcessFrameResult.CONTINUE


ASSISTANT_RECORD_TIMEOUT_SECS = 1.0


class SoftInterruptGate(FrameProcessor):
    """Verzoegert Unterbrechungen bis zur naechsten hoerbaren Satz- bzw. Wortgrenze."""

    def __init__(
        self,
        *,
        max_words: int = 3,
        playback_lag_secs: float = 0.5,
        max_wait_secs: float = 1.5,
        uhr: Callable[[], float] = time.monotonic,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._uhr = uhr
        self._woerter: deque[tuple[float, str]] = deque(maxlen=400)  # zuletzt gespielte Wörter (für den Echo-Filter)
        self._tts_offen = False  # ein Sprachabschnitt läuft, dessen Text noch nicht im Verlauf steht
        self._max_words = max_words
        self._playback_lag_secs = playback_lag_secs
        self._max_wait_secs = max_wait_secs
        self._arm_count = 0  # jede Scharfschaltung zählt; die Zeitgrenze gilt nur für ihre eigene
        self._grenze_task: asyncio.Task | None = None
        self._bot_speaking = False
        self._pending = False  # die Person spricht, Unterbrechung steht aus
        self._firing = False  # Grenze gefunden, Unterbrechung laeuft
        self._last_word_ended_sentence = False
        self._words_left = 0  # so viele Wörter darf WAMO nach dem Einwurf noch sprechen
        self._held: list[Frame] = []
        self._held_user_msgs: list[dict] = []
        self._fire_task: asyncio.Task | None = None
        self._turns_recorded = 0
        self._turn_recorded = asyncio.Event()

    def kuerzlich_gesprochen(self, sekunden: float = ECHO_FENSTER_KURZ_SECS) -> list[str]:
        """Wörter, die WAMO in den letzten Sekunden gespielt hat, in der Reihenfolge (klein, ohne Satzzeichen)."""
        grenze = self._uhr() - sekunden
        return [w for t, w in self._woerter if t >= grenze]

    def antwort_beginnt(self):
        """Vom PlaybackTracker aufgerufen: ein neuer Sprachabschnitt beginnt, sein Text kommt erst später in den Verlauf."""
        self._tts_offen = True

    def assistant_turn_recorded(self):
        """Aus on_assistant_turn_stopped aufrufen: der Text von WAMO steht jetzt im Verlauf."""
        self._turns_recorded += 1
        self._tts_offen = False
        self._turn_recorded.set()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._last_word_ended_sentence = False
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._pending:
                await self.push_frame(frame, direction)
                self._start_fire("Antwort zu Ende", interrupt=False)
                return
        elif isinstance(frame, InterruptionFrame):
            # Unterbrechung von anderer Stelle: nichts mehr zurueckhalten.
            await self.push_frame(frame, direction)
            if self._pending:
                self._start_fire("externe Unterbrechung", interrupt=False)
            return
        elif isinstance(frame, UserStartedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            if self._bot_speaking:
                self._arm()
            elif not self._firing:
                # WAMO spricht noch nicht (Claude denkt) -> sofort abbrechen wie bisher.
                await self.push_frame(InterruptionFrame())
            return
        elif (
            isinstance(frame, LLMContextFrame)
            and direction == FrameDirection.DOWNSTREAM
            and (self._pending or self._firing)
        ):
            messages = frame.context.messages
            if messages and messages[-1].get("role") == "user":
                self._held_user_msgs.append(messages[-1])
            self._held.append(frame)
            return

        await self.push_frame(frame, direction)

    def on_word_played(self, text: str) -> bool:
        """Vom PlaybackTracker aufgerufen, wenn laut Zeitstempel ein neues Wort beginnt.

        Beginnt ein neues Wort, ist das vorige fertig: hier liegt eine Wortgrenze.
        Gibt True zurueck, wenn das Wort abgeschnitten wird (gilt dann als nicht gesprochen).
        """
        if self._firing:
            return True
        if self._pending:
            if self._last_word_ended_sentence:
                self._start_fire("Satzende")
                return True
            if self._words_left <= 0:
                self._start_fire("Wortlimit")
                return True
            self._words_left -= 1
        self._last_word_ended_sentence = text.rstrip().endswith(SENTENCE_END)
        jetzt = self._uhr()
        for w in text.split():  # das Haus-TTS liefert ganze Sätze, Soniox einzelne Wörter
            if wort := _wort(w):
                self._woerter.append((jetzt, wort))
        return False

    def _arm(self):
        if self._pending or self._firing:
            return
        self._pending = True
        self._words_left = self._max_words
        self._arm_count += 1
        if self._max_wait_secs > 0:
            self._grenze_task = self.create_task(self._zeitgrenze(self._arm_count), name="soft_interrupt_zeitgrenze")
        logger.debug(f"{self}: Person spricht, WAMO beendet erst den Satz")

    async def _zeitgrenze(self, arm: int):
        """Ohne Wortgrenze (keine Zeitstempel) spätestens nach max_wait_secs unterbrechen."""
        await asyncio.sleep(self._max_wait_secs)
        if self._pending and arm == self._arm_count:
            self._start_fire("Zeitgrenze")

    def _start_fire(self, reason: str, *, interrupt: bool = True):
        if not self._pending:
            return
        self._pending = False
        self._firing = True
        # Eigener Task: der Aufrufer (Tracker) darf nicht blockieren, sonst haengt die Pipeline.
        self._fire_task = self.create_task(
            self._fire(reason, interrupt), name="soft_interrupt_fire"
        )

    async def _fire(self, reason: str, interrupt: bool):
        try:
            if interrupt:
                # Die Oberflaeche hoert, was gerade erklingt: sie blendet in der naechsten Wortpause
                # aus. Der Server bricht danach ab (Zeitstempel laufen dem Ton voraus).
                await self.push_frame(RTVIServerMessageFrame(data={"type": "leiser_stopp"}))
                await asyncio.sleep(self._playback_lag_secs)
                logger.info(f"{self}: weiche Unterbrechung ({reason})")
                await self.push_frame(InterruptionFrame())
            else:
                logger.info(f"{self}: keine Unterbrechung noetig ({reason})")
            await self._wait_for_assistant_turn()
            self._move_held_user_messages_to_end()
        finally:
            self._firing = False
            self._fire_task = None
            held, self._held = self._held, []
            self._held_user_msgs = []
            for frame in held:
                await self.push_frame(frame)

    async def _wait_for_assistant_turn(self):
        """Wartet, bis der Text des laufenden Sprachabschnitts im Verlauf steht, höchstens ASSISTANT_RECORD_TIMEOUT_SECS."""
        deadline = asyncio.get_running_loop().time() + ASSISTANT_RECORD_TIMEOUT_SECS
        while self._tts_offen:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                logger.warning(f"{self}: Antwort von WAMO nicht rechtzeitig im Verlauf")
                self._tts_offen = False  # nicht bei jedem weiteren Einwurf erneut eine Sekunde warten
                return
            self._turn_recorded.clear()
            try:
                await asyncio.wait_for(self._turn_recorded.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    def _move_held_user_messages_to_end(self):
        """Den Einwurf der Person hinter den gesprochenen Text von WAMO einsortieren."""
        if not self._held or not self._held_user_msgs:
            return
        context = self._held[-1].context
        held_ids = {id(m) for m in self._held_user_msgs}
        messages = list(context.messages)
        if not messages or id(messages[-1]) in held_ids:
            return  # steht schon am Ende, nichts zu tun
        rest = [m for m in messages if id(m) not in held_ids]
        moved = [m for m in messages if id(m) in held_ids]
        context.set_messages(rest + moved)

    async def cleanup(self):
        await super().cleanup()
        if self._fire_task:
            await self.cancel_task(self._fire_task)
            self._fire_task = None
        if self._grenze_task and not self._grenze_task.done():
            await self.cancel_task(self._grenze_task)
        self._grenze_task = None


class PlaybackTracker(FrameProcessor):
    """Sitzt hinter transport.output() und meldet dem Gate jedes abgespielte Wort."""

    def __init__(self, gate: SoftInterruptGate, **kwargs):
        super().__init__(**kwargs)
        self._gate = gate

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStartedFrame) and direction == FrameDirection.DOWNSTREAM:
            if getattr(frame, "append_to_context", True):
                self._gate.antwort_beginnt()
        elif isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._gate.on_word_played(frame.text):
                return  # abgeschnitten -> nicht in den Gespraechsverlauf
        await self.push_frame(frame, direction)
