"""Weiche Unterbrechung: WAMO spricht den Satz zu Ende, statt mitten im Wort abzubrechen.

Ablauf
  * Die Start-Strategie meldet den Turn der Person OHNE sofortige Unterbrechung
    (MinWordsUserTurnStartStrategy(enable_interruptions=False)).
  * SoftInterruptGate (zwischen user_agg und llm) merkt: die Person redet, während WAMO spricht.
  * PlaybackTracker (nach transport.output, Modul wiedergabe_tracker.py) meldet jedes Wort, sobald es laut Zeitstempel beginnt.
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
  * Werkzeuge (WAMO_RUECKMELDUNG_WERKZEUG, Standard an seit der Messung vom 31.10.2026): Solange ein Werkzeug läuft, das eine Unterbrechung
    abbrechen würde, und bis WERKZEUG_NACHLAUF_SECS nach seinem Ergebnis (bis die Antwort beginnt), schweigt WAMO. Dann
    startete jedes erkannte Wort, auch ein "mhm", einen Zug, das Gate unterbrach sofort und Pipecat brach das Werkzeug ab
    (Websuche, Bildschirm, Aufträge). Mit dem Schalter gelten in dieser Zeit dieselben Regeln wie beim Sprechen:
    Rückmeldelaute und Echo starten keinen Zug und lassen das Werkzeug laufen; Stoppwörter und echte Einwürfe wirken
    sofort und brechen es wie bisher ab. Das Gate merkt sich die laufenden Werkzeuge je Sitzung (werkzeug_laeuft).
  * Verlauf: Das Gate wartet nach der Unterbrechung nur dann auf den gesprochenen Text, wenn ein Sprachabschnitt läuft,
    dessen Text noch nicht im Verlauf steht. Steht er schon da (die Antwort war fertig, nur der Ton lief noch) oder
    wurde nichts gesprochen, geht es sofort weiter statt eine Sekunde zu warten.
  * Innehalten statt Abbrechen (WAMO_INNEHALTEN, Standard an, wiedergabe.py): Mit einem AusgabeHalt am Tonausgang der Sitzung
    hält WAMO bei einem einzelnen echten Wort der Person ("Danke", "Du") an der nächsten Wortgrenze inne, wie ein Mensch, der
    merkt, dass der andere ansetzt. Kommt nach weiter_secs Stille nichts nach (höchstens max_pause_secs), spricht WAMO genau dort
    weiter. Zwei echte Wörter oder ein Stoppwort ("Stopp", "Nein", "Moment") brechen wie bisher ab, Rückmeldelaute ("mhm") halten
    WAMO gar nicht an. Weil der PlaybackTracker mit dem Halt jedes Wort erst meldet, wenn sein Ton hinausgeht (nicht nach dem
    Zeitstempel), steht der Ton bei einem bestätigten Einwurf schon an der Wortgrenze: Der Halt hält an, der Abbruch folgt sofort.
    Weder die Zeitverschiebung der Zeitstempel (playback_lag_secs) noch das Ausblenden in der Oberfläche (leiser_stopp) sind dann
    nötig, und der Verlauf braucht kein Kürzen. Während eines Halts gibt es kein leiser_stopp und kein Dämpfen; beim Weitersprechen
    nimmt das Gate eine schon gedämpfte Stimme zurück (wie nach jeder Entwarnung, danach eine Weile nicht erneut dämpfen), und alles
    gilt wieder wie vorher. Je Sitzung gehören ein Halt, ein Gate und ein Tracker zusammen, es gibt keinen gemeinsamen Zustand.
    Ohne Halt (WAMO_INNEHALTEN=aus, Sprachausgabe im Haus mit ganzen Sätzen, Ausgang ohne write_audio_frame) gilt alles wie vorher.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import time
from collections import deque
from collections.abc import Callable

from loguru import logger

from env_schalter import schalter_an
from ja_waechter import ist_kurzes_ja

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    UserStartedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)

# Der Tracker und die Antworten des Gates auf ein Wort liegen in wiedergabe_tracker.py; PlaybackTracker bleibt von hier
# importierbar (bot.py und die Tests).
from wiedergabe_tracker import HALTEN, SCHNEIDEN, SPIELEN, PlaybackTracker  # noqa: F401

SENTENCE_END = (".", "!", "?", "…", ":")

# Rueckmeldelaute: zeigen Zuhoeren, sind kein Einwurf. Alles andere zaehlt ("Doch", "Nein", "Stopp").
BACKCHANNEL_WORDS = frozenset(
    {
        "mhm", "mhmm", "mm", "hm", "hmm", "ähm", "äh", "aha", "ah", "oh", "ok", "okay",
        "ja", "jaja", "jo", "genau", "stimmt", "richtig", "klar", "super", "gut", "cool",
        "achso", "ach", "so", "verstehe", "uh", "huh",
        # erweitert (WAMO_RUECKMELDUNG): Schreibweisen der Erkennung für "mhm" und weitere Zustimmung
        "mmh", "mmhm", "mmhmm", "hmhm", "mhmh", "ahja", "achja", "joa", "jup", "yep", "yes", "yeah", "mja",
        "prima", "schön", "toll", "interessant", "natürlich",
    }
)
# Wendungen, die nur als ganze Äußerung eine Rückmeldung sind ("alles klar", aber "alles" allein oder "na" allein nicht)
BACKCHANNEL_PHRASES = frozenset(
    {
        "ich verstehe", "alles klar", "na klar", "ach so", "ach ja", "ja klar", "ja genau", "ganz genau", "sehr gut",
        "ja okay", "ah ok", "oh okay", "na gut", "schon klar", "ja sicher", "ja natürlich", "ja stimmt", "ja richtig",
        "ja ja", "mm hmm", "mm hm", "uh huh", "ach so ja", "sehr schön", "ganz klar", "okay gut", "ja gut", "oh ja",
    }
)
RUECKMELDUNG_ZEICHEN = ".,!?…:;-–—\"'"


def rueckmeldung_an(wert: str | None = None) -> bool:
    """WAMO_RUECKMELDUNG: erweiterte Erkennung kurzer Zustimmung (Standard an), aus = nur die ursprüngliche Wortliste."""
    return schalter_an("WAMO_RUECKMELDUNG", wert)


_URSPRUENGLICH = frozenset(
    {
        "mhm", "mhmm", "mm", "hm", "hmm", "ähm", "äh", "aha", "ah", "oh", "ok", "okay", "ja", "jaja", "jo", "genau",
        "stimmt", "richtig", "klar", "super", "gut", "cool", "achso", "ach", "so", "verstehe", "uh", "huh",
    }
)


def _rueckmeldung_woerter(text: str, erweitert: bool) -> list[str]:
    """Wörter des Textes ohne Satzzeichen; Bindestriche trennen ("Mm-hmm" sind zwei Laute)."""
    if not erweitert:
        return [w.strip(".,!?…:;-\"'").lower() for w in text.split()]
    roh = re.sub(r"[-–—]+", " ", text)
    return [w.strip(RUECKMELDUNG_ZEICHEN).lower() for w in roh.split()]


def ist_rueckmeldung(text: str, erweitert: bool | None = None) -> bool:
    """Besteht der Text nur aus Rückmeldelauten ("mhm", "ja genau", "ach so, ja")? Leerer Text und einzelne Buchstaben
    (Bruchstücke der Live-Erkennung) sind keine Rückmeldung, sie sind noch unentschieden."""
    erweitert = rueckmeldung_an() if erweitert is None else erweitert
    woerter = [w for w in _rueckmeldung_woerter(text, erweitert) if w]
    if not woerter:
        return False
    if erweitert and " ".join(woerter) in BACKCHANNEL_PHRASES:
        return True
    menge = BACKCHANNEL_WORDS if erweitert else _URSPRUENGLICH
    gueltig = [w for w in woerter if len(w) >= 2]  # einzelne Buchstaben sind Bruchstücke
    return bool(gueltig) and all(w in menge for w in gueltig)


def _anfang_eines_lauts(wort: str, menge: frozenset[str]) -> bool:
    """Ist wort der Anfang eines Rückmeldelauts ("Gen" vor "Genau")? Die Live-Erkennung meldet Wörter stückweise."""
    return len(wort) >= 2 and wort not in menge and any(m.startswith(wort) and len(m) > len(wort) for m in menge)


def meaningful_word_count(text: str, erweitert: bool | None = None, interim: bool = False) -> int:
    """Zaehlt echte Woerter. Einzelne Buchstaben sind Bruchstuecke der Live-Erkennung
    ("J" vor "Ja", "H" vor "Hi") und zaehlen nicht. Rückmeldelaute zählen nicht (ist_rueckmeldung).
    interim: Zwischenstand der Erkennung; ein letztes Wort, das erst der Anfang eines Rückmeldelauts ist ("Gen" vor
    "Genau"), zählt noch nicht (mit der erweiterten Erkennung, WAMO_RUECKMELDUNG)."""
    erweitert = rueckmeldung_an() if erweitert is None else erweitert
    woerter = [w for w in _rueckmeldung_woerter(text, erweitert) if w]
    if erweitert and (" ".join(woerter) in BACKCHANNEL_PHRASES):
        return 0
    menge = BACKCHANNEL_WORDS if erweitert else _URSPRUENGLICH
    zaehlen = [w for w in woerter if len(w) >= 2 and w not in menge]
    if erweitert and interim and zaehlen and woerter[-1] == zaehlen[-1] and _anfang_eines_lauts(zaehlen[-1], menge):
        zaehlen = zaehlen[:-1]
    return len(zaehlen)


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
# Echo-Modus (WAMO_ECHO_MODUS): Hört das Mikrofon die eigene Stimme erkennbar (mindestens ECHO_MODUS_MIN als Echo erkannte
# Einwürfe in ECHO_MODUS_FENSTER_SECS), kommen verhörte Bruchstücke des Echos ("Magnetfeldpunkt", "Carl Dre") am Text nicht
# mehr vom Einwurf zu unterscheiden. Solange es anhält, unterbricht nur noch ein Stoppwort (STOPP_WORDS).
ECHO_MODUS_FENSTER_SECS = 20.0
ECHO_MODUS_MIN = 2


def echo_filter_an(wert: str | None = None) -> bool:
    return schalter_an("WAMO_ECHO_FILTER", wert)


def echo_modus_an(wert: str | None = None) -> bool:
    """WAMO_ECHO_MODUS: bei erkennbarem Echo unterbricht nur ein Stoppwort (Standard an; wirkt nur mit dem Echo-Filter)."""
    return schalter_an("WAMO_ECHO_MODUS", wert)


# Rückmeldelaut bei laufendem Werkzeug (WAMO_RUECKMELDUNG_WERKZEUG)
WERKZEUG_NACHLAUF_SECS = 4.0  # so lange nach dem Ergebnis zählt das Werkzeug noch als laufend (bis die Antwort beginnt)
WERKZEUG_MAX_SECS = 120.0  # ein Werkzeug, dessen Ergebnis nie ankam, hält die Regel nicht länger fest


def rueckmeldung_werkzeug_an(wert: str | None = None) -> bool:
    """WAMO_RUECKMELDUNG_WERKZEUG: ein Rückmeldelaut bei laufendem Werkzeug startet keinen Zug und bricht es nicht ab
    (Standard an seit der Messung vom 31.10.2026)."""
    return schalter_an("WAMO_RUECKMELDUNG_WERKZEUG", wert)


# Wörter aus STOPP_WORDS, die für sich allein nur zum Innehalten führen, wenn das Innehalten an ist (WAMO_INNEHALTEN): ein
# "Danke" mitten in der Antwort ist eher ein Ansetzen als ein Abbruch. Ohne Innehalten bleibt es ein Stoppwort wie immer.
WEICHE_STOPPWOERTER = frozenset({"danke"})
# Wörter, die ein einzelnes Wort zum Abbruch machen, aber nicht in STOPP_WORDS stehen ("Abbrechen", "Nee", "Ruhe"). Sie gelten mit
# und ohne Innehalten: Mit Innehalten würden sie sonst nur innehalten lassen, ohne Innehalten (WAMO_INNEHALTEN=aus, Haus-Sprachausgabe,
# Ausgang ohne write_audio_frame) wären sie bei MIN_INTERRUPT_WORDS=2 als einzelnes Wort wirkungslos.
ZUSATZ_STOPPWOERTER = frozenset({"abbrechen", "abbruch", "nee", "ruhe", "wart", "aufhoeren"})


def hat_stoppwort(text: str, ohne: frozenset[str] = frozenset(), zusaetzlich: frozenset[str] = frozenset()) -> bool:
    """Enthält der Text ein Wort, mit dem man WAMO anhält oder etwas wissen will ("Stopp", "Moment", "Was", "Nein")?
    ohne: Wörter, die hier nicht zählen (WEICHE_STOPPWOERTER, nur bei eingeschaltetem Innehalten), zusaetzlich: weitere
    Stoppwörter (ZUSATZ_STOPPWOERTER, immer)."""
    woerter = STOPP_WORDS | zusaetzlich if zusaetzlich else STOPP_WORDS
    return any(w in woerter and w not in ohne for w in (_wort(x) for x in text.split()))


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
    laute = BACKCHANNEL_WORDS if rueckmeldung_an() else _URSPRUENGLICH
    bedeutsam = [w for w in erkannt if (len(w) >= 2 or _ist_ziffer(w)) and w not in laute]
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


def selbst_gesprochen(text: str, gespielt: list[str]) -> bool:
    """Besteht der Text nur aus Wörtern, die WAMO in den letzten Sekunden selbst gespielt hat? Dann ist es sein Echo und kein Wort
    der Person, auch wenn es ein Rückmeldelaut wie "Ja" ist (echo_urteil zählt Rückmeldelaute nicht mit)."""
    erkannt = [w for w in (_wort(x) for x in text.split()) if len(w) >= 2]
    return bool(erkannt) and bool(gespielt) and all(any(_passt(w, g, letztes=False) for g in gespielt) for w in erkannt)


# Fragebeginn mitten im Satz, bevor das Fragezeichen erklingt: "Soll ich ...", "Möchtest du ...", "Darf ich ..."
FRAGE_BEGINN = re.compile(
    r"^\W*(soll|sollen|darf|dürfen|kann|können|will|willst|wollen|möchte|möchtest|möchten|magst|mag)\w*\s+(ich|du|wir|er|sie)\b",
    re.IGNORECASE,
)


SOLL_ICH = re.compile(r"\bsoll\s+ich\b", re.IGNORECASE)  # als ganze Wörter: "Tagessoll ich ..." ist keine Frage

# Gängige Abkürzungen mit Punkt beenden keinen Satz: "Soll ich z. B. den Ordner öffnen?" bleibt bis zum Fragezeichen eine Frage.
# Die Sprachausgabe liefert zweiteilige wie "z. B." auch als zwei Wörter. Der erste Teil beendet nie einen Satz, der zweite nur
# ohne den passenden ersten davor ("Wir nehmen Plan B." endet).
ABKUERZUNGEN = frozenset({
    "z.b.", "d.h.", "u.a.", "dr.", "prof.", "bzw.", "ca.", "usw.", "etc.", "ggf.", "evtl.", "vgl.", "inkl.", "bspw.", "nr.",
})
ABKUERZUNG_TEILE = {"z.": "b.", "d.": "h.", "u.": "a."}  # erster Teil, zweiter Teil
_SATZZEICHEN_DANACH = "\"'”»)"  # Anführungszeichen und Klammer hinter dem Satzende
_ZEICHEN_DAVOR = "\"'„“‚‘»«("  # Anführungszeichen und Klammer vor einem Wort


def satz_endet(wort: str, davor: str = "") -> bool:
    """Endet mit diesem Wort ein Satz? Satzzeichen am Ende (auch vor Anführungszeichen oder Klammer), aber keine gängige Abkürzung
    mit Punkt. davor: das Wort davor, für Abkürzungen aus zwei Wörtern ("z." und "B.")."""
    w = wort.strip().rstrip(_SATZZEICHEN_DANACH)
    if not w.endswith(SENTENCE_END):
        return False
    klein = w.lstrip(_ZEICHEN_DAVOR).lower()
    erster_teil = davor.strip().lstrip(_ZEICHEN_DAVOR).lower()
    return not (klein in ABKUERZUNGEN or klein in ABKUERZUNG_TEILE or ABKUERZUNG_TEILE.get(erster_teil) == klein)


def ist_frage(satz: str) -> bool:
    """Ein zu Ende gesprochener Satz ist eine Frage: Fragezeichen am Ende (auch hinter Anführungszeichen) oder "soll ich"."""
    t = satz.strip().rstrip(" .")
    return bool(re.search(r"\?[!\"'”»)]*$", t)) or bool(SOLL_ICH.search(t))


def fragt_bisher(satz: str) -> bool:
    """Ist der Satz, so weit er erklungen ist, eine Frage? Das Fragezeichen fehlt mitten im Satz noch, also zählt der Beginn."""
    return bool(satz.strip()) and (ist_frage(satz) or bool(FRAGE_BEGINN.match(satz)))


def ist_antwort(text: str) -> bool:
    """Eine Antwort auf die Frage von WAMO ("Ja", "genau", "okay", "mach"), kein Zuhören ("mhm") und keine Nachfrage ("Ja?",
    "Ja, aber ...")."""
    return ist_kurzes_ja(text)


class MeaningfulWordsUserTurnStartStrategy(MinWordsUserTurnStartStrategy):
    """Wie MinWords, aber während WAMO spricht, zählen Rückmeldelaute nicht mit.

    "mhm" oder "ja genau" lassen WAMO weiterreden, ein einzelnes "Doch" oder "Stopp" unterbricht.
    Spricht WAMO nicht, startet wie bisher jedes Wort den Turn.

    gesprochen: liefert zu einer Zahl von Sekunden die Wörter, die WAMO so lange zuletzt gespielt hat, in der Reihenfolge
    (SoftInterruptGate.kuerzlich_gesprochen). Besteht ein Einwurf aus einer Folge davon, ist es das Echo der eigenen
    Stimme und zählt nicht (echo_urteil).
    """

    def __init__(
        self,
        *,
        gesprochen: Callable[[float], list[str]] | None = None,
        beurteilt: Callable[[str], None] | None = None,
        echo_modus: bool = False,
        werkzeug_laeuft: Callable[[], bool] | None = None,
        on_partial: Callable[[str], None] | None = None,
        fragt: Callable[[], bool] | None = None,
        **kwargs,
    ):
        """beurteilt: wird mit "rueckmeldung" oder "echo" gerufen, wenn der erkannte Text kein Einwurf ist (das Gate
        nimmt dann die vorsorglich gedämpfte Stimme wieder zurück). werkzeug_laeuft: sagt, ob gerade ein Werkzeug läuft
        (SoftInterruptGate.werkzeug_laeuft); dann gelten dieselben Regeln wie beim Sprechen, None lässt alles wie bisher.
        on_partial: Innehalten (SoftInterruptGate.ansatz). Ein einzelnes echtes Wort, während WAMO spricht, wird dort gemeldet
        statt ignoriert; ein "Danke" allein unterbricht dann nicht mehr (WEICHE_STOPPWOERTER). None: wie bisher.
        fragt: sagt, ob WAMO gerade eine Frage stellt (SoftInterruptGate.fragt). Dann ist ein "Ja", "genau" oder "okay" die
        Antwort und kein Rückmeldelaut (ist_antwort): es startet den Zug und bleibt im Transkript. None: wie bisher."""
        super().__init__(**kwargs)
        self._fragt = fragt
        self._on_partial = on_partial
        self._werkzeug_laeuft = werkzeug_laeuft
        self.werkzeug_rueckmeldungen = 0  # Rückmeldelaute, die bei laufendem Werkzeug keinen Zug starteten
        self._werkzeug_gemeldet = 0.0
        self._gesprochen = gesprochen
        self._beurteilt = beurteilt
        self._echo_modus = echo_modus
        self._echo_zeiten: deque[float] = deque(maxlen=8)  # Zeiten der als Echo erkannten Einwürfe
        self.echo_vermutet = 0  # Einwürfe ohne Stoppwort, die im Echo-Modus verworfen wurden
        self._echo_vermutet_gemeldet = 0.0
        self.echo_verworfen = 0  # für Tests und das Log
        self._echo_gemeldet = 0.0
        self.rueckmeldungen = 0
        self._rueckmeldung_gemeldet = 0.0

    def echo_modus_aktiv(self) -> bool:
        """Hört WAMO sich gerade erkennbar selbst? (Mindestens ECHO_MODUS_MIN Echo-Urteile in den letzten Sekunden.)"""
        jetzt = time.monotonic()
        return self._echo_modus and sum(1 for t in self._echo_zeiten if jetzt - t < ECHO_MODUS_FENSTER_SECS) >= ECHO_MODUS_MIN

    def _urteil(self, text: str) -> str:
        if self._gesprochen is None:
            return "einwurf"
        return echo_urteil(text, self._gesprochen(ECHO_FENSTER_KURZ_SECS), self._gesprochen(ECHO_FENSTER_SECS))

    def _selbst_gesagt(self, text: str) -> bool:
        """Hat WAMO die Wörter dieses Textes in den letzten Sekunden selbst gespielt? Ohne Echo-Filter weiß der Kern es nicht."""
        return self._gesprochen is not None and selbst_gesprochen(text, self._gesprochen(ECHO_FENSTER_KURZ_SECS))

    def _werkzeug_aktiv(self) -> bool:
        return self._werkzeug_laeuft is not None and not self._bot_speaking and bool(self._werkzeug_laeuft())

    async def _handle_transcription(self, frame) -> ProcessFrameResult:
        werkzeug = self._werkzeug_aktiv()
        if not self._bot_speaking and not werkzeug:
            return await super()._handle_transcription(frame)
        count = meaningful_word_count(frame.text, interim=isinstance(frame, InterimTranscriptionFrame))
        # Schweigt WAMO (Werkzeug läuft), gilt wie bei Pipecat schon ein Wort; MIN_INTERRUPT_WORDS betrifft nur das Sprechen.
        # Ein Stoppwort ("Stopp", "Moment", "Nein", "Was") unterbricht auch unter der Schwelle sofort (12.11.2026).
        # Mit Innehalten (on_partial) führt ein Wort wie "Danke" allein nur zum Innehalten.
        halten = self._on_partial is not None and self._bot_speaking
        weich, zusatz = (WEICHE_STOPPWOERTER if halten else frozenset()), ZUSATZ_STOPPWOERTER
        stoppwort = self._bot_speaking and count >= 1 and hat_stoppwort(frame.text, weich, zusatz)
        if count >= (self._min_words if self._bot_speaking else 1) or stoppwort:
            urteil = self._urteil(frame.text)
            if urteil != "echo" and self.echo_modus_aktiv() and not hat_stoppwort(frame.text, zusaetzlich=zusatz):
                self.echo_vermutet += 1
                jetzt = time.monotonic()
                if jetzt - self._echo_vermutet_gemeldet > 5.0:  # höchstens alle 5 s, ohne Inhalt
                    self._echo_vermutet_gemeldet = jetzt
                    logger.info(f"{self}: Echo vermutet, ohne Stoppwort kein Einwurf ({count} Wörter, insgesamt {self.echo_vermutet})")
                if self._beurteilt is not None:
                    self._beurteilt("echo")
                await self.trigger_reset_aggregation()
                return ProcessFrameResult.CONTINUE
            if urteil == "echo":
                self.echo_verworfen += 1
                jetzt = time.monotonic()
                self._echo_zeiten.append(jetzt)
                if jetzt - self._echo_gemeldet > 5.0:  # höchstens alle 5 s, ohne Inhalt
                    self._echo_gemeldet = jetzt
                    logger.info(f"{self}: Echo der eigenen Stimme verworfen ({count} Wörter, insgesamt {self.echo_verworfen})")
                if self._beurteilt is not None:
                    self._beurteilt("echo")
                await self.trigger_reset_aggregation()
                return ProcessFrameResult.CONTINUE
            if urteil == "unklar" and isinstance(frame, InterimTranscriptionFrame):
                return ProcessFrameResult.CONTINUE  # erst der endgültige Text entscheidet (Echo wächst zu einer Folge)
            logger.info(f"{self}: Einwurf erkannt ({count} Wörter)")
            logger.debug(f"{self}: Einwurf erkannt ({frame.text!r})")
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        if (self._bot_speaking and self._fragt is not None and self._fragt() and ist_antwort(frame.text)
                and self._urteil(frame.text) != "echo" and not self.echo_modus_aktiv() and not self._selbst_gesagt(frame.text)):
            # Ein "Ja" mitten in der Frage von WAMO ist die Antwort: es startet den Zug, statt als Zuhören verworfen zu werden.
            # Nicht, wenn das Mikrofon WAMO gerade hört (Echo-Modus) oder WAMO das Wort eben selbst gesagt hat: dann ist es sein Echo.
            logger.info(f"{self}: Antwort auf die Frage von WAMO")
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        if halten and count >= 1 and self._urteil(frame.text) == "einwurf" and not self.echo_modus_aktiv():
            # Ein einzelnes echtes Wort: kein Einwurf, aber WAMO hält inne und wartet ab, ob mehr kommt. Echo (ein Wort, das
            # WAMO gerade selbst gesprochen hat, oder erkanntes Echo) hält WAMO nie an.
            self._on_partial(frame.text)
        elif ist_rueckmeldung(frame.text):
            self.rueckmeldungen += 1
            jetzt = time.monotonic()
            if werkzeug:
                self.werkzeug_rueckmeldungen += 1
                if jetzt - self._werkzeug_gemeldet > 2.0:  # ohne Inhalt, höchstens alle 2 s
                    self._werkzeug_gemeldet = jetzt
                    logger.info(f"{self}: Rückmeldung bei laufendem Werkzeug, kein Zug, das Werkzeug läuft weiter "
                                f"(insgesamt {self.werkzeug_rueckmeldungen})")
            elif jetzt - self._rueckmeldung_gemeldet > 2.0:  # ohne Inhalt, höchstens alle 2 s
                self._rueckmeldung_gemeldet = jetzt
                logger.info(f"{self}: Rückmeldung erkannt, WAMO redet weiter (insgesamt {self.rueckmeldungen})")
            if self._beurteilt is not None:
                self._beurteilt("rueckmeldung")
        await self.trigger_reset_aggregation()
        return ProcessFrameResult.CONTINUE


ASSISTANT_RECORD_TIMEOUT_SECS = 1.0

# Stopp an der nächsten hörbaren Wortgrenze (WAMO_STOPP_GRENZE) und vorsorgliches Dämpfen (WAMO_DUCKEN)
STOPP_FADE_SECS = 0.14  # so lange blendet die Oberfläche aus (ui/src/lib/useWamo.ts fadeOutAudio)
STOPP_VORLAUF_SECS = 0.05  # Weg der Servernachricht zur Oberfläche
STOPP_SATZ_VORZUG_SECS = 0.30  # ein Satz- oder Kommaende, das höchstens so viel später liegt als die früheste Grenze, geht vor
DUCK_VERZOEGERUNG_SECS = 0.25  # Sprache der Person so lange (nach der Meldung der Spracherkennung), bevor gedämpft wird
DUCK_NACH_VAD_ENDE_SECS = 1.0  # ohne bestätigten Einwurf so lange nach dem Ende der Sprache wieder normal (Pausen, Verzug der Erkennung)
DUCK_SPERRE_SECS = 8.0  # nach einer Entwarnung (Rückmeldung, Echo, Höchstdauer) so lange nicht wieder dämpfen
DUCK_SPERRE_ECHO_SECS = 30.0  # Echo erkannt: die Dämpfung würde mit dem Echo pumpen, also lange nicht
DUCK_SPERRE_KURZ_SECS = 1.5  # nach dem Ende der Sprache ohne Urteil der Erkennung nur kurz
DUCK_BEURTEILT_SECS = 0.3  # Rückmeldung oder Echo erkannt: so lange warten, ob doch ein Einwurf folgt ("Ja, aber ...")
DUCK_MAX_SECS = 3.0  # spätestens nach dieser Zeit ohne bestätigten Einwurf wieder normal (Echo hält die Sprachaktivität an)
# Innehalten (WAMO_INNEHALTEN)
WATCH_SECS = 0.05  # so oft prüft das Gate Innehalten, Weitersprechen und Abbruch ohne Wortgrenze
# So lange steht der Ton mindestens, gerechnet ab dem Anhalten (nicht ab dem Ansatz): Die Stille zählt ab dem Ansatz, das Anhalten
# folgt aber erst an der nächsten Wortgrenze. Im Log lagen Anhalten und Weitersprechen so nur 0,12 s auseinander, ein Stolpern statt
# einer Pause. Der Wert ist geschätzt und per Hörprobe nachzustellen; die Höchstpause (max_pause_secs) geht ihm immer vor.
MIN_HALT_SECS = 0.6


def stopp_grenze_an(wert: str | None = None) -> bool:
    """WAMO_STOPP_GRENZE: Unterbrechung an der nächsten hörbaren Wortgrenze statt nach bis zu drei weiteren Wörtern."""
    return schalter_an("WAMO_STOPP_GRENZE", wert)


def ducken_an(wert: str | None = None) -> bool:
    """WAMO_DUCKEN: Stimme sofort leiser, sobald die Person zu sprechen beginnt; endgültig nur bei einem echten Einwurf."""
    return schalter_an("WAMO_DUCKEN", wert)


class SoftInterruptGate(FrameProcessor):
    """Verzoegert Unterbrechungen bis zur naechsten hoerbaren Satz- bzw. Wortgrenze.

    grenze=True: Die Wort-Zeitstempel (Tracker-Uhr) laufen dem hörbaren Ton um playback_lag_secs voraus. Eine Wortgrenze,
    die der Tracker schon gemeldet hat, ist für die Person also noch nicht zu hören. Das Gate wählt die früheste dieser
    Grenzen, die noch zu erreichen ist (Satz- oder Kommaende bis STOPP_SATZ_VORZUG_SECS später geht vor), lässt die
    Oberfläche so ausblenden, dass die Stimme genau dort verstummt, und bricht erst dann serverseitig ab. Wörter ab der
    Grenze gelten als nicht gehört (ungehoerte_woerter). Ohne Wortzeiten greift weiter die Zeitgrenze.

    ducken=True: Beginnt die Person zu sprechen, während WAMO redet, dämpft die Oberfläche die Stimme vorsorglich
    (Nachricht stimme_leiser). Bestätigt die Spracherkennung einen Einwurf, folgt der Stopp; sind es nur Rückmeldelaute,
    Echo oder Geräusche, nimmt das Gate die Dämpfung zurück und dämpft in den nächsten DUCK_SPERRE_SECS nicht erneut.

    halt: Innehalten (siehe Kopf der Datei). ansatz() meldet ein einzelnes echtes Wort der Person, wort_beginnt() hält an der
    nächsten Wortgrenze an (HALTEN), check_timers() setzt nach weiter_secs Stille (höchstens max_pause_secs) fort oder bricht ohne
    Wortgrenze nach grenzwarte_secs ab. Mit halt stoppt ein bestätigter Einwurf exakt am Ton: playback_lag_secs, grenze (Wahl der
    erreichbaren Grenze) und leiser_stopp entfallen, grenze=False zählt weiter Satzende und Wörter (max_words).
    """

    def __init__(
        self,
        *,
        max_words: int = 3,
        playback_lag_secs: float = 0.5,
        max_wait_secs: float = 1.5,
        uhr: Callable[[], float] = time.monotonic,
        grenze: bool = False,
        ducken: bool = False,
        halt=None,
        weiter_secs: float = 0.7,
        max_pause_secs: float = 2.5,
        grenzwarte_secs: float = 0.4,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._uhr = uhr
        # Innehalten: die Person setzt an (ein Wort), WAMO hält an der nächsten Wortgrenze inne (_ansatz_seit), dann steht der Ton
        # (_haelt_seit) und wartet, ob mehr kommt
        self._halt = halt
        self._weiter_secs = weiter_secs
        self._max_pause_secs = max_pause_secs
        self._grenzwarte_secs = grenzwarte_secs
        self._ansatz_seit: float | None = None
        self._haelt_seit: float | None = None
        self._ansatz_text = ""
        self._letzter_ansatz = 0.0
        self._fortgesetzt_text = ""  # dieser Ansatz hat schon einmal angehalten, nicht noch einmal
        self._user_quiet_since = 0.0  # Ende der letzten Sprache der Person
        self._armed_at = 0.0
        self._watch_task: asyncio.Task | None = None
        self._grenze_modus = grenze
        self._ducken = ducken
        self._grenzen: deque[tuple[float, bool]] = deque(maxlen=200)  # (Tracker-Zeit des Wortbeginns, Satz- oder Kommaende davor)
        self._ungehoert = 0  # so viele bereits gemeldete Wörter liegen hinter der Grenze
        self._duckt = False
        self._duck_task: asyncio.Task | None = None
        self._duck_ende_task: asyncio.Task | None = None  # Sprachende ohne bestätigten Einwurf
        self._duck_max_task: asyncio.Task | None = None  # Höchstdauer der Dämpfung
        self._vad_spricht = False
        self._duck_gesperrt_bis = 0.0
        # zuletzt gespielte Wörter (Zeit, Wort, steht im Verlauf): für den Echo-Filter und das Kürzen des Verlaufs
        self._woerter: deque[tuple[float, str, bool]] = deque(maxlen=400)
        self._kurzlebig: set[asyncio.Task] = set()  # Entdämpfen-Aufgaben, die cleanup beenden muss
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
        self._satz: list[str] = []  # Wörter des Satzes, den WAMO gerade spricht (nur was erklungen ist)
        self._frage_fertig = False  # der zuletzt zu Ende gesprochene Satz war eine Frage
        self._last_word_ended_phrase = False  # Satz- oder Kommaende
        self._words_left = 0  # so viele Wörter darf WAMO nach dem Einwurf noch sprechen
        self._held: list[Frame] = []
        self._held_user_msgs: list[dict] = []
        self._fire_task: asyncio.Task | None = None
        self._turns_recorded = 0
        self._turn_recorded = asyncio.Event()
        self._werkzeuge: dict[str, float] = {}  # laufende Werkzeuge, die ein neuer Zug abbräche: Aufruf -> Beginn
        self._werkzeug_nachlauf_bis = 0.0  # bis dahin zählt das Ergebnis noch als "Werkzeug läuft" (bis die Antwort beginnt)

    def werkzeug_laeuft(self) -> bool:
        """Läuft ein Werkzeug, das ein neuer Zug abbrechen würde, oder ist sein Ergebnis gerade erst gekommen und die Antwort
        beginnt noch nicht? Je Sitzung, kein gemeinsamer Zustand."""
        jetzt = self._uhr()
        for aufruf in [a for a, t in self._werkzeuge.items() if jetzt - t > WERKZEUG_MAX_SECS]:
            del self._werkzeuge[aufruf]  # Ergebnis nie angekommen: nicht ewig festhalten
        return bool(self._werkzeuge) or jetzt < self._werkzeug_nachlauf_bis

    def _werkzeug_verfolgen(self, frame: Frame) -> None:
        if isinstance(frame, FunctionCallInProgressFrame):
            if frame.cancel_on_interruption:  # nur Aufrufe, die ein neuer Zug abbräche; asynchrone stören nicht
                self._werkzeuge.setdefault(frame.tool_call_id, self._uhr())
        elif isinstance(frame, FunctionCallResultFrame):
            if self._werkzeuge.pop(frame.tool_call_id, None) is not None and not self._werkzeuge:
                self._werkzeug_nachlauf_bis = self._uhr() + WERKZEUG_NACHLAUF_SECS
        elif isinstance(frame, FunctionCallCancelFrame):
            # Ein Abbruch folgt meist einem Einwurf, dessen Zug schon läuft: kein Nachlauf, sonst ginge dessen Text verloren
            self._werkzeuge.pop(frame.tool_call_id, None)
        elif isinstance(frame, InterruptionFrame):
            self._werkzeuge.clear()  # Pipecat bricht sie ab
            self._werkzeug_nachlauf_bis = 0.0
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._werkzeug_nachlauf_bis = 0.0  # die Antwort beginnt: ab jetzt gilt die Regel fürs Sprechen

    def kuerzlich_gesprochen(self, sekunden: float = ECHO_FENSTER_KURZ_SECS) -> list[str]:
        """Wörter, die WAMO in den letzten Sekunden gespielt hat, in der Reihenfolge (klein, ohne Satzzeichen)."""
        grenze = self._uhr() - sekunden
        return [w for t, w, _ in self._woerter if t >= grenze]

    def antwort_beginnt(self):
        """Vom PlaybackTracker aufgerufen: ein neuer Sprachabschnitt beginnt, sein Text kommt erst später in den Verlauf."""
        self._tts_offen = True
        if not self._firing:
            self._ungehoert = 0  # ein nicht abgeholter Wert gehört zur vorigen Antwort

    def assistant_turn_recorded(self):
        """Aus on_assistant_turn_stopped aufrufen: der Text von WAMO steht jetzt im Verlauf."""
        self._turns_recorded += 1
        self._tts_offen = False
        self._turn_recorded.set()

    def ungehoerte_woerter(self) -> int:
        """Wie viele der zuletzt gemeldeten Wörter liegen hinter der Stoppgrenze (nie zu hören waren)? Einmal abholen:
        steht danach wieder bei 0. Der Aufrufer kürzt damit den Verlauf auf das tatsächlich Gehörte."""
        n, self._ungehoert = self._ungehoert, 0
        return n

    def beurteilt(self, grund: str):
        """Von der Start-Strategie gerufen: der Einwurf war nur eine Rückmeldung ("rueckmeldung") oder Echo ("echo").
        Die vorsorgliche Dämpfung wird nach DUCK_BEURTEILT_SECS zurückgenommen und dann eine Weile nicht wiederholt.
        Die Frist fängt Zwischenstände ab: ein echter Einwurf beginnt oft mit "Ja, aber", und das Wort nach dem "Ja"
        bestätigt ihn (_arm bricht die Frist ab), ohne dass die Stimme erst lauter und dann leiser wird."""
        if not self._ducken:
            return
        if self._duck_task and not self._duck_task.done():
            self._duck_task.cancel()  # noch nicht gedämpft: in dieser Sprachphase gar nicht erst dämpfen
            sperre = DUCK_SPERRE_ECHO_SECS if grund == "echo" else DUCK_SPERRE_KURZ_SECS
            self._duck_gesperrt_bis = max(self._duck_gesperrt_bis, self._uhr() + sperre)
        if self._duckt and not (self._pending or self._firing):
            self._frist("_duck_ende_task", DUCK_BEURTEILT_SECS, grund, DUCK_SPERRE_ECHO_SECS if grund == "echo" else DUCK_SPERRE_SECS)

    def _kurz(self, coro, name: str):
        """Kurze Hintergrundaufgabe, die cleanup noch beenden kann."""
        task = self.create_task(coro, name=name)
        self._kurzlebig.add(task)
        task.add_done_callback(self._kurzlebig.discard)
        return task

    def _duck_planen(self):
        if (
            not self._ducken
            or self._duckt
            or self._pending
            or self._firing
            or self.haelt_inne
            or self._uhr() < self._duck_gesperrt_bis
            or (self._duck_task and not self._duck_task.done())
        ):
            return
        self._duck_task = self.create_task(self._duck_nach(), name="soft_interrupt_ducken")

    async def _duck_nach(self):
        await asyncio.sleep(DUCK_VERZOEGERUNG_SECS)
        if not (self._vad_spricht and self._bot_speaking) or self._duckt or self._firing or self.haelt_inne:
            return
        self._duckt = True
        logger.info(f"{self}: Stimme gedämpft")
        await self.push_frame(RTVIServerMessageFrame(data={"type": "stimme_leiser", "an": True}))
        self._frist("_duck_max_task", DUCK_MAX_SECS, "Zeit", DUCK_SPERRE_SECS)

    def _frist(self, attribut: str, sekunden: float, grund: str, sperre: float):
        """Dämpfung nach sekunden zurücknehmen, wenn bis dahin kein Einwurf bestätigt ist (ersetzt einen früheren Termin)."""
        alt = getattr(self, attribut)
        if alt and not alt.done():
            alt.cancel()
        setattr(self, attribut, self.create_task(self._entduck_nach(sekunden, grund, sperre), name="soft_interrupt" + attribut))

    def _fristen_abbrechen(self, *attribute: str):
        for attribut in attribute or ("_duck_ende_task", "_duck_max_task"):
            task = getattr(self, attribut)
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()

    async def _entduck_nach(self, sekunden: float, grund: str, sperre: float):
        await asyncio.sleep(sekunden)
        if self._duckt and not (self._pending or self._firing):
            self._duck_gesperrt_bis = self._uhr() + sperre
            await self._entducken(grund)

    async def _entducken(self, grund: str):
        if not self._duckt:
            return
        self._duckt = False
        self._fristen_abbrechen()
        logger.info(f"{self}: Stimme wieder normal ({grund})")
        await self.push_frame(RTVIServerMessageFrame(data={"type": "stimme_leiser", "an": False}))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self._werkzeug_verfolgen(frame)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._last_word_ended_sentence = False
            self._satz, self._frage_fertig = [], False
            self._fortgesetzt_text = ""
            self._clear_ansatz(fortsetzen=True)  # nie in eine neue Antwort hinein stumm bleiben
        elif isinstance(frame, VADUserStartedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            self._vad_spricht = True
            self._fristen_abbrechen("_duck_ende_task")  # die Person redet weiter: Pause in der Rede, kein Ende
            if self._bot_speaking:
                self._duck_planen()
            return
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and direction == FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            self._vad_spricht = False
            self._user_quiet_since = self._uhr()
            if self._duckt and not (self._pending or self._firing):
                self._frist("_duck_ende_task", DUCK_NACH_VAD_ENDE_SECS, "Sprache zu Ende", DUCK_SPERRE_KURZ_SECS)
            return
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._clear_ansatz(fortsetzen=True)
            if self._duckt and not (self._pending or self._firing):
                self._kurz(self._entducken("Antwort zu Ende"), "soft_interrupt_entducken")
            if self._pending:
                await self.push_frame(frame, direction)
                self._start_fire("Antwort zu Ende", interrupt=False)
                return
        elif isinstance(frame, InterruptionFrame):
            # Unterbrechung von anderer Stelle: nichts mehr zurueckhalten (den Ausgang öffnet der Tracker mit dem Abbruch).
            self._clear_ansatz()
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
                if self.werkzeug_laeuft():
                    laufend = len(self._werkzeuge)
                    logger.info(f"{self}: Zug bei laufendem Werkzeug, es wird abgebrochen "
                                f"({laufend} laufend{'' if laufend else ', Nachlauf nach dem Ergebnis'})")
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

    def on_word_played(self, text: str, im_verlauf: bool = True) -> bool:
        """Alte Schnittstelle des Trackers ohne Halt: True = abgeschnitten (siehe wort_beginnt)."""
        return self.wort_beginnt(text, im_verlauf) == SCHNEIDEN

    def wort_beginnt(self, text: str, im_verlauf: bool = True) -> str:
        """Vom PlaybackTracker aufgerufen, wenn laut Zeitstempel (mit Halt: wenn der Ton) ein neues Wort beginnt.

        Beginnt ein neues Wort, ist das vorige fertig: hier liegt eine Wortgrenze.
        SPIELEN: das Wort geht hinaus. SCHNEIDEN: abgeschnitten, gilt als nicht gesprochen. HALTEN (nur mit Halt): WAMO hält vor
        diesem Wort inne, es kommt später wieder.
        """
        if self._firing:
            return SCHNEIDEN
        jetzt = self._uhr()
        if self._halt is not None:
            if self._pending:
                if grund := self._grenze_mit_halt():
                    self._halt.anhalten()  # der Ton steht genau hier; der Abbruch verwirft den Rest
                    self._start_fire(grund)
                    return SCHNEIDEN
            elif self._haelt_seit is not None:
                return HALTEN
            elif self._ansatz_seit is not None:
                self._anhalten("Wortgrenze")
                return HALTEN
        elif self._grenze_modus:
            self._grenzen.append((jetzt, self._last_word_ended_phrase))
            if self._pending and self._plane_grenze(jetzt):
                return SCHNEIDEN
        elif self._pending:
            if self._last_word_ended_sentence:
                self._start_fire("Satzende")
                return SCHNEIDEN
            if self._words_left <= 0:
                self._start_fire("Wortlimit")
                return SCHNEIDEN
            self._words_left -= 1
        self._last_word_ended_sentence = text.rstrip().endswith(SENTENCE_END)
        self._satz_verfolgen(text)
        self._last_word_ended_phrase = text.rstrip().endswith(SENTENCE_END + (",", ";"))
        for w in text.split():  # das Haus-TTS liefert ganze Sätze, Soniox einzelne Wörter
            if wort := _wort(w):
                self._woerter.append((jetzt, wort, im_verlauf))
        return SPIELEN

    def _grenze_mit_halt(self) -> str | None:
        """Mit Halt, bestätigter Einwurf, ein neues Wort beginnt: ist hier Schluss? Der Grund oder None. Die Stoppgrenze
        (WAMO_STOPP_GRENZE) hört an der ersten Wortgrenze auf, sonst gelten wie bisher Satzende und höchstens max_words weitere Wörter."""
        if self._grenze_modus:
            return "Wortgrenze"
        if self._last_word_ended_sentence:
            return "Satzende"
        if self._words_left <= 0:
            return "Wortlimit"
        self._words_left -= 1
        return None

    @property
    def haelt_inne(self) -> bool:
        """Hat die Person angesetzt (ein Wort gemeldet), oder steht der Ton schon? Dann gibt es kein Dämpfen."""
        return self._ansatz_seit is not None or self._haelt_seit is not None

    def _satz_verfolgen(self, text: str) -> None:
        """Merkt sich, ob der Satz, den WAMO gerade spricht oder zuletzt beendet hat, eine Frage ist (nur was erklungen ist)."""
        if not text.strip():
            return
        woerter = text.split()  # Soniox liefert einzelne Wörter, die Sprachausgabe im Haus ganze Sätze
        davor = woerter[-2] if len(woerter) > 1 else (self._satz[-1].split()[-1] if self._satz else "")
        self._satz.append(text.strip())
        if satz_endet(woerter[-1], davor):  # "z. B." und andere Abkürzungen beenden den Satz nicht
            self._frage_fertig = ist_frage(" ".join(self._satz))
            self._satz = []

    def fragt(self) -> bool:
        """Stellt WAMO gerade eine Frage? Mitten im Satz zählt der Fragebeginn ("Soll ich"), nach dem Satz das Fragezeichen.
        Folgt nach der Frage eine Aussage, ist die Frage vorbei."""
        teil = " ".join(self._satz)
        return fragt_bisher(teil) if teil else self._frage_fertig

    def ansatz(self, text: str):
        """Von der Start-Strategie (on_partial): ein einzelnes echtes Wort der Person, während WAMO spricht.

        WAMO hält an der nächsten Wortgrenze inne und wartet ab, ob die Person weiterspricht. Ohne Halt, ohne laufende Antwort
        oder bei schon bestätigtem Einwurf geschieht nichts.
        """
        if self._halt is None or not self._bot_speaking or self._pending or self._firing:
            return
        schluessel = " ".join(w for w in (_wort(x) for x in text.split()) if w)
        if schluessel == self._fortgesetzt_text:
            return  # derselbe Ansatz hat schon einmal angehalten und kam nicht weiter
        self._ansatz_text = schluessel
        self._letzter_ansatz = self._uhr()
        if self._ansatz_seit is None and self._haelt_seit is None:
            self._ansatz_seit = self._letzter_ansatz
            if self._duck_task and not self._duck_task.done():
                self._duck_task.cancel()  # noch nicht gedämpft: während des Halts gar nicht erst dämpfen
            logger.debug(f"{self}: Person setzt an ({text!r}), WAMO hält an der nächsten Wortgrenze inne")
            self._ensure_watch()

    def _anhalten(self, grund: str):
        self._ansatz_seit = None
        self._haelt_seit = self._uhr()
        self._halt.anhalten()
        logger.info(f"{self}: WAMO hält inne ({grund}, Ansatz {self._ansatz_text!r})")

    def _fortsetzen(self, grund: str):
        self._haelt_seit = None
        self._fortgesetzt_text = self._ansatz_text
        self._halt.fortsetzen()
        logger.info(f"{self}: WAMO spricht weiter ({grund}, Ansatz {self._ansatz_text!r})")
        if self._duckt:
            # Eine Dämpfung aus der Zeit vor dem Halt: wie nach jeder Entwarnung zurücknehmen und eine Weile nicht erneut dämpfen
            self._duck_gesperrt_bis = max(self._duck_gesperrt_bis, self._uhr() + DUCK_SPERRE_SECS)
            self._kurz(self._entducken("Weitersprechen"), "soft_interrupt_entducken")

    def _clear_ansatz(self, *, fortsetzen: bool = False):
        """Ansatz und Halt vergessen. fortsetzen: den Ausgang öffnen (bei Abbruch von außen tut das der Tracker mit dem Abbruch)."""
        if fortsetzen and self._haelt_seit is not None and self._halt is not None:
            self._halt.fortsetzen()
        self._ansatz_seit = None
        self._haelt_seit = None

    def check_timers(self) -> bool:
        """Innehalten, Weitersprechen und Abbrechen ohne Wortgrenze. True: weiter beobachten."""
        if self._firing or self._halt is None:
            return False
        jetzt = self._uhr()
        if self._pending:
            if jetzt - self._armed_at >= self._grenzwarte_secs:
                # Keine Wortgrenze in Sicht (lange Pause zwischen zwei Sätzen): jetzt abbrechen.
                self._halt.anhalten()
                self._start_fire("ohne Wortgrenze")
                return False
            return True
        if self._ansatz_seit is not None:
            if jetzt - self._ansatz_seit >= self._grenzwarte_secs:
                self._anhalten("ohne Wortgrenze")
            return True
        if self._haelt_seit is not None:
            if jetzt - self._haelt_seit >= self._max_pause_secs:
                self._fortsetzen("Höchstdauer")
                return False
            still_seit = max(self._letzter_ansatz, self._user_quiet_since)
            weiter_ab = max(still_seit + self._weiter_secs, self._haelt_seit + MIN_HALT_SECS)  # die Höchstpause oben geht vor
            if not self._vad_spricht and jetzt >= weiter_ab:
                self._fortsetzen("kein weiteres Wort")
                return False
            return True
        return False

    def _ensure_watch(self):
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = self.create_task(self._watch(), name="soft_interrupt_watch")

    async def _watch(self):
        try:
            while True:
                await asyncio.sleep(WATCH_SECS)
                if not self.check_timers():
                    return
        finally:
            self._watch_task = None

    def _naechste_grenze(self, jetzt: float) -> tuple[float, bool] | None:
        """Früheste Wortgrenze (Tracker-Zeit), an der die Stimme noch rechtzeitig zu stoppen ist. Das Ausblenden dauert
        STOPP_FADE_SECS plus Weg zur Oberfläche, der Ton läuft dem Tracker um playback_lag_secs hinterher: eine Grenze b
        ist erreichbar, wenn b + lag - fade - vorlauf noch nicht vorbei ist. Ein Satz- oder Kommaende bis
        STOPP_SATZ_VORZUG_SECS nach der frühesten Grenze geht vor."""
        frueh = jetzt - self._playback_lag_secs + STOPP_FADE_SECS + STOPP_VORLAUF_SECS
        kandidaten = [(t, satz) for t, satz in self._grenzen if t >= frueh]
        if not kandidaten:
            return None
        erste = kandidaten[0]
        for t, satz in kandidaten:
            if t > erste[0] + STOPP_SATZ_VORZUG_SECS:
                break
            if satz:
                return t, True
        return erste

    def _plane_grenze(self, jetzt: float) -> bool:
        """Grenze wählen und den Stopp dorthin legen. False, wenn es noch keine Grenze gibt (das nächste Wort bringt eine)."""
        wahl = self._naechste_grenze(jetzt)
        if wahl is None:
            return False
        t, satz = wahl
        self._ungehoert = sum(1 for tw, _, verlauf in self._woerter if tw >= t and verlauf)
        self._pending = False
        self._firing = True
        self._fire_task = self.create_task(
            self._fire_grenze(t, "Satzende" if satz else "Wortgrenze"), name="soft_interrupt_fire"
        )
        return True

    def _arm(self):
        if self._pending or self._firing:
            return
        self._pending = True
        self._words_left = self._max_words
        self._ungehoert = 0
        self._arm_count += 1
        self._armed_at = self._uhr()
        self._fristen_abbrechen()  # der Einwurf ist bestätigt: die Dämpfung geht in den Stopp über
        steht = self._haelt_seit is not None or (self._halt is not None and self._halt.haelt)
        self._ansatz_seit = None
        self._haelt_seit = None
        if self._max_wait_secs > 0:
            self._grenze_task = self.create_task(self._zeitgrenze(self._arm_count), name="soft_interrupt_zeitgrenze")
        if self._halt is not None:
            if steht:
                self._start_fire("Einwurf bestätigt")  # der Ton steht schon, der Abbruch kann sofort folgen
                return
            self._ensure_watch()  # ohne Wortgrenze bricht check_timers nach grenzwarte_secs ab
        elif self._grenze_modus:
            self._plane_grenze(self._uhr())  # schon gemeldete, noch nicht hörbare Grenzen gelten sofort
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
            if interrupt and self._halt is not None:
                # Der Ton steht schon an der Wortgrenze (oder steht ab jetzt): kein Ausblenden in der Oberfläche, keine Wartezeit.
                # Der Abbruch verwirft den Rest, der Tracker öffnet den Ausgang für die nächste Antwort.
                self._halt.anhalten()
                logger.info(f"{self}: weiche Unterbrechung ({reason}, Ton angehalten)")
                await self.push_frame(InterruptionFrame())
            elif interrupt:
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
            await self._fire_ende()

    async def _warte_bis(self, tracker_zeit: float):
        rest = tracker_zeit - self._uhr()
        if rest > 0:
            await asyncio.sleep(rest)

    async def _fire_grenze(self, grenze: float, grund: str):
        """Stopp an der gewählten Grenze: die Oberfläche beginnt so, dass die Stimme an der Grenze verstummt, der Server
        bricht ab, wenn die Person die Grenze hört (vorher würde er Ton wegnehmen, den sie noch hören soll)."""
        try:
            ton = grenze + self._playback_lag_secs  # so spät kommt die Grenze bei der Person an
            await self._warte_bis(ton - STOPP_FADE_SECS - STOPP_VORLAUF_SECS)
            await self.push_frame(RTVIServerMessageFrame(data={"type": "leiser_stopp", "sofort": True}))
            await self._warte_bis(ton)
            logger.info(f"{self}: weiche Unterbrechung ({grund})")
            await self.push_frame(InterruptionFrame())
            await self._wait_for_assistant_turn()
            self._move_held_user_messages_to_end()
        finally:
            await self._fire_ende()

    async def _fire_ende(self):
        self._firing = False
        self._clear_ansatz()
        self._duckt = False  # die Oberfläche setzt die Lautstärke mit der nächsten Antwort zurück
        self._ungehoert = 0  # kam kein Abschlussereignis der Antwort (Text schon im Verlauf), bleibt nichts für später stehen
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
        for task in (self._duck_task, self._duck_ende_task, self._duck_max_task, *self._kurzlebig):
            if task and not task.done():
                await self.cancel_task(task)
        if self._grenze_task and not self._grenze_task.done():
            await self.cancel_task(self._grenze_task)
        self._grenze_task = None
        if self._watch_task and not self._watch_task.done():
            await self.cancel_task(self._watch_task)
        self._watch_task = None
        if self._halt is not None:
            self._halt.fortsetzen()  # nie einen angehaltenen Ausgang zurücklassen


def ohne_letzte_woerter(text: str, n: int) -> str:
    """Text ohne die letzten n Wörter (Leerraum und Satzzeichen am neuen Ende bleiben sauber)."""
    if n <= 0:
        return text
    teile = re.findall(r"\S+\s*", text)
    return "".join(teile[:-n]).rstrip() if n < len(teile) else ""


def kuerze_auf_gehoertes(context, n: int) -> bool:
    """Nimmt die letzten n Wörter aus der zuletzt hinzugefügten Antwort von WAMO im Verlauf. Bleibt nichts übrig, fällt die
    Nachricht weg (eine leere Antwort lehnt das Sprachmodell ab). False, wenn der Verlauf nicht mit einer Antwort aus Text
    endet (dann bleibt alles, wie es ist)."""
    nachrichten = list(context.messages)
    if not nachrichten or not isinstance(nachrichten[-1], dict) or nachrichten[-1].get("role") != "assistant":
        logger.debug("Verlauf nicht gekürzt: die letzte Nachricht ist keine Antwort von WAMO")
        return False
    letzte = nachrichten[-1]
    inhalt = letzte.get("content")
    if not isinstance(inhalt, str):
        logger.debug("Verlauf nicht gekürzt: die Antwort besteht nicht aus einfachem Text")
        return False
    kurz = ohne_letzte_woerter(inhalt, n)
    if kurz.replace("●", "").replace("◐", "").replace("○", "").strip():
        letzte["content"] = kurz
    else:
        context.set_messages(nachrichten[:-1])
    return True
