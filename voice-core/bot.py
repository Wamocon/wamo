"""WAMO Voice-Core.

Pipeline:  Mikrofon (WebRTC, Browser-AEC) -> Silero-VAD -> Soniox STT (Streaming, DE)
           -> Turn-Erkennung -> Claude (Streaming) -> Soniox TTS (Streaming) -> Lautsprecher

Anmeldung: auth_gate.py prüft Token, zweiten Faktor und aktives Profil und schreibt die geprüfte
Identität in runner_args.body["wamo"]. Fehlt sie, endet die Sitzung sofort; einen Weg ohne
Anmeldung gibt es nicht. PC-Werkzeuge und Entwicklungsaufträge bekommt nur die Rolle admin. Laborprofile (labor.py, Testlabor) bekommen
einen eigenen, engen Zweig: nur Zeit, Websuche und Canvas, kein Gedächtnis, kein PC, keine Entwicklung.

Unterbrechungen:
  * Während WAMO spricht, laufen VAD und STT weiter.
  * MeaningfulWordsUserTurnStartStrategy: Rückmeldelaute ("mhm", "ja genau") stoppen WAMO
    nicht, jedes andere Wort schon ("Doch", "Stopp").
  * Pipecat schreibt nur die TATSÄCHLICH gesprochenen Wörter in den Kontext (Soniox TTS liefert
    Wort-Zeitstempel). Nach einer Unterbrechung sieht Claude genau, wo WAMO aufgehört hat.
  * on_assistant_turn_stopped(interrupted=True) hängt zusätzlich eine kurze Systemnotiz an,
    damit Claude bewusst anknüpft statt neu anzufangen.

Nachrichten an die Oberfläche (RTVI-Servernachrichten, zusätzlich zu canvas, aktivitaet, ...):
  {"type": "profil", "name", "rolle"}                              beim Verbinden
  {"type": "betrieb", "modus", "ort", "modelle", "pc", "pc_latenz_ms"?}
                                                                   beim Verbinden und wenn der PC-Host steht
  {"type": "bildstrom", ...} und {"type": "bild", ...}             Live-Bild des eigenen Laptops (bildstrom.py)
  {"type": "gemerkt", "bereich", "status", "text", "id"}           nach jedem gespeicherten Gedächtniseintrag
  {"type": "mikrofon", "zustand": "still"|"fehlt"|"ok", "sekunden"}
                                                                   10 s nur digitale Stille vom Mikrofon, "fehlt": 5 s gar
                                                                   keine Audiopakete, "ok": wieder Ton (audio_monitor.py)
Nachrichten von der Oberfläche:
  {"type": "vergessen", "data": {"id"}}   eigenen Eintrag deaktivieren (memory.forget)
  {"type": "freigabe", "data": {"job": "pc", "id", "entscheidung"}}
                                          Freigabe einer kritischen PC-Aktion (pc_freigaben.py; nur admin, PC-Steuerung
                                          an, Sitzung hält die PC-Sperre), geprüft vor den Entwicklungsaufträgen
  {"type": "freigabe", "data": {"job": "labor", "id", "entscheidung"}}
                                          Freigabe einer schreibenden Labor-Aktion (labor_freigaben.py; nur Laborprofile)
  {"type": "bildstrom", "data": {"aktion", ...}}
                                          Live-Bild des eigenen Laptops (bildstrom.py; nur admin mit gebundenem Gerät)
  {"type": "freigabe", "data": {...}}     sonst Freigabe für Entwicklungsaufträge (nur admin)
Live-Untertitel: Pipecat sendet bot-output (mit Wortfortschritt aus den Soniox-Zeitstempeln) und
user-transcription von selbst; RTVIObserverParams bleiben dafür auf dem Standard (eingeschaltet).

Start lokal:  uv run bot.py   -> http://localhost:7860
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dotenv import load_dotenv
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMMessagesAppendFrame, LLMUpdateSettingsFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.settings import LLMSettings
from pipecat.services.soniox.stt import SonioxContextObject, SonioxSTTService
from pipecat.services.soniox.tts import SonioxTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_stop.deferred_user_turn_stop_strategy import deferred
from pipecat.turns.user_stop.llm_turn_completion_user_turn_stop_strategy import (
    LLMTurnCompletionUserTurnStopStrategy,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_completion_mixin import UserTurnCompletionConfig
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.context.llm_context_summarization import (
    LLMAutoContextSummarizationConfig,
    LLMContextSummaryConfig,
)

from abnahme import llm_beurteiler
from activity import ActivityReporter
from ausgaben_kuerzen import Ausgaben, AusgabenKuerzer, register_nachladen
from audio_monitor import AudioInputMonitor, SpeakerTracker
from auth_gate import Identity, anmeldung_max_secs, identity_from_body, install_access_gate, stunden_text
import entwicklung_betrieb
from dev_agent import DevAgent, abnahme_befehle_im_betrieb, entwicklung_enabled, entwicklung_problem
from learning import Gemerkt, learn_from_session
import kontingent
from llm_anbieter import LLMNichtVerfuegbar, ausblenden_text, gespraech_llm, lesbarer_modellname, zusammenfassung_llm
from memory import MemoryStore, Profile, ProfileDirectory, Recall, shared_database
from modellzugang import modellzugang_nachricht, zugang_laden
from boss import Boss, entwicklung_quelle, modus_wirksam
from kern_dienst import runner_vorbereiten
from cache_waerme import vorwaermen
from env_schalter import schalter_an
from latenz import LatenzObserver, api_messen, latenz_log_an
from pc_geraet import geraet_wache, rechnername_passt
from schluessel_filter import SchluesselFilter, absichern
from sprache_anbieter import haus_stt, haus_tts, sprach_wahl
from ja_waechter import JaWaechter, ja_waechter_an
import labor
import labor_durchlauf
import labor_limit
from labor_client import LaborFehler, LaborKonfigFehler
from labor_rtvi import LaborRTVIProcessor
from labor_sitzung import LaborSitzung
from turn_wache import TurnWache
from vorgaenge import Vorgaenge, datenbank_ablage
from werkzeug_wache import AbbruchHinweis, DoppelWache, abbruch_hinweis_an, umhuellen, werkzeug_doppelt_an
from wartesatz import Wartesatz, wartesatz_an, wartesatz_modell_an, wartesatz_modell_secs, wartesatz_secs
from pc_host import (
    buero,
    call_host_tool,
    host_client,
    host_tools,
    ist_tunnel,
    pc_host_fuer,
    pc_sperre_pruefen,
    pc_zugang,
    register_modus,
    sperre_aus_ergebnis,
)
import bild_fern
from bildstrom import NICHT_ERLAUBT as BILD_NICHT_ERLAUBT, Bildstrom
import labor_werkzeuge
from pc_freigaben import PcFreigaben
from persona import build_system_prompt
from screen import PC_OFF, foto as screen_foto, register_screen_tools
from sitzungen import Sitzung, Sitzungen
from soft_interrupt import (
    MeaningfulWordsUserTurnStartStrategy,
    PlaybackTracker,
    SoftInterruptGate,
    echo_filter_an,
    ducken_an,
    echo_modus_an,
    kuerze_auf_gehoertes,
    ohne_letzte_woerter,
    rueckmeldung_werkzeug_an,
    stopp_grenze_an,
)
from soniox_tts import LazySonioxTTSService, stimme_einstellung
from tools import CANVAS_TYPE, TurnState, gemerkt_message, register_tools

load_dotenv(override=True)


def _configure_logging() -> None:
    """Standard INFO (WAMO_LOG_LEVEL): INFO enthält keine Gesprächsinhalte, die stehen nur in DEBUG."""
    level = (os.getenv("WAMO_LOG_LEVEL") or "INFO").strip().upper()
    if level == "TRACE":  # TRACE schriebe ganze Frames (auch Bilder des Live-Bilds) ins Log: höchstens DEBUG
        level = "DEBUG"
    logger.remove()
    # diagnose=False: loguru schriebe sonst bei Ausnahmen die Werte lokaler Variablen (Tokens, Schlüssel) ins Log
    try:
        logger.add(sys.stderr, level=level, backtrace=False, diagnose=False)
    except ValueError:
        logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)
        logger.warning("WAMO_LOG_LEVEL {!r} unbekannt, nutze INFO", level)


_configure_logging()

if sys.platform == "win32":
    # Höhere Priorität: wenn WAMO Programme öffnet, belegen die kurz die CPU; das Audio soll
    # dann nicht stocken.
    import ctypes

    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x8000)

# Standard: EU-Endpunkte (Datenschutz); .env kann umstellen
SONIOX_STT_URL = os.getenv("SONIOX_STT_URL") or "wss://stt-rt.eu.soniox.com/transcribe-websocket"
SONIOX_TTS_URL = os.getenv("SONIOX_TTS_URL") or "wss://tts-rt.eu.soniox.com/tts-websocket"
SONIOX_DEFAULT_VOICE = "Bryce"  # Standardstimme von Pipecat 1.12 (SonioxTTSService)
SONIOX_VOICE = os.getenv("SONIOX_VOICE") or SONIOX_DEFAULT_VOICE
# Fachbegriffe für die Spracherkennung. Bewusst nur "terms", kein "general": ein allgemeiner
# Kontext drückte seine Wörter in fremde Sätze. .env: SONIOX_TERMS (kommagetrennt), leer = aus.
DEFAULT_STT_TERMS = (
    "WAMO, WAMOCON, Eschborn, öffne mal, VS Code, Visual Studio Code, Claude Code, Chrome, Explorer, "
    "Canvas, Dark Mode, SQL-Statement, Supabase, Vercel, GitHub"
)
STT_TERMS = [t.strip() for t in os.getenv("SONIOX_TERMS", DEFAULT_STT_TERMS).split(",") if t.strip()]
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL_FAST") or "claude-haiku-5-5"
# Wörter ohne Rückmeldelaute, ab denen WAMO unterbrochen wird ("Doch" reicht, "mhm" nicht)
MIN_INTERRUPT_WORDS = int(os.getenv("MIN_INTERRUPT_WORDS") or 1)
# Lernen im laufenden Gespräch: nach so vielen neuen Sätzen der Person wertet Haiku den Abschnitt
# aus; Neues landet sofort im System-Prompt.
LEARN_EVERY_USER_TURNS = int(os.getenv("LEARN_EVERY_USER_TURNS") or 4)
LEARN_OVERLAP = 4  # so viele frühere Zeilen als Kontext mitgeben
SUMMARY_TRIGGER_TOKENS = int(os.getenv("SUMMARY_TRIGGER_TOKENS") or 60000)
SUMMARY_TARGET_TOKENS = int(os.getenv("SUMMARY_TARGET_TOKENS") or 25000)
DEFAULT_GREETING = "Hallo {name}, WAMO ist bereit."
# Stille nach dem Sprechende, bis die Spracherkennung (VAD) den Redebeitrag für beendet hält (Standard von Pipecat 0,2 s).
# 0,15 s spart 50 ms bis zum Transkript (gemessen am 21.10.2026: 308 gegen 356 ms) und trennt Sätze mit Pausen nicht
# häufiger; Pipecat meldet einmal je Sitzung, dass der Wert vom Standard abweicht (Warnung im Log, harmlos).
VAD_STOP_SECS = float(os.getenv("WAMO_VAD_STOP_SECS") or 0.15)
# Hält Smart-Turn einen Satz für unvollständig, wartet WAMO so lange (Sekunden Stille) auf den Rest; danach entscheidet
# Claude mit dem Satzende-Marker. Jeder unvollständige Befund kostete bis zu 0,85 s (früher 1,0 s).
SMART_TURN_STOP_SECS = float(os.getenv("SMART_TURN_STOP_SECS") or 0.5)
TURN_INCOMPLETE_SHORT_SECS = float(os.getenv("TURN_INCOMPLETE_SHORT_SECS") or 1.5)
TURN_INCOMPLETE_LONG_SECS = float(os.getenv("TURN_INCOMPLETE_LONG_SECS") or 3.0)  # 8 s wirkten wie ein Hänger
# Weiche Unterbrechung: nach einem Einwurf spricht WAMO den Satz zu Ende, höchstens aber das
# laufende Wort plus so viele weitere Wörter (nie mitten im Wort).
SOFT_INTERRUPT_MAX_WORDS = int(os.getenv("SOFT_INTERRUPT_MAX_WORDS") or 3)
# So weit laufen die Wort-Zeitstempel dem hörbaren Ton voraus (gemessen: etwa 0,5 s).
SOFT_INTERRUPT_LAG_SECS = float(os.getenv("SOFT_INTERRUPT_LAG_SECS") or 0.5)
DEFAULT_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
def betrieb_modus(wert: str | None) -> str:
    """Betriebsart für die Oberfläche: buero (Kern auf Sokrates-2, wamo-core.service), sonst lokal (Unterwegs)."""
    return "buero" if (wert or "").strip().lower() == "buero" else "lokal"


BETRIEB_MODUS = betrieb_modus(os.getenv("WAMO_BETRIEB"))
MODELL_ORT = "cloud"  # Claude und Soniox laufen in der Cloud; eigene Modelle auf den Sparks wären "haus"
CLEANUP_DB_TIMEOUT_SECS = 5.0
PROFILE_RECHECK_SECS = 60.0  # so oft prüft eine laufende Sitzung, ob das Profil noch gilt
MAX_SITZUNG_MIN = float(os.getenv("WAMO_MAX_SITZUNG_MIN") or 120)
SPERR_PROBE_SECS = 10.0  # lokal: so oft fragt eine Sitzung bei gesperrtem Bildschirm nach dem Entsperren

INTERRUPTED_NOTE = (
    "[Hinweis für WAMO: Du wurdest gerade unterbrochen. Deine letzte Antwort endet dort, wo du "
    "aufgehört hast. War der Einwurf nur ein Laut oder eine kurze Zustimmung ohne neue Frage, mach "
    "ohne Entschuldigung genau dort weiter. Sonst reagiere natürlich auf den Einwurf und knüpfe dann, "
    "falls noch relevant, an deinem letzten Gedanken an, statt von vorn zu beginnen.]"
)

# Referenzen halten, sonst räumt der Garbage Collector laufende Hintergrund-Tasks weg.
# (Nur Task-Referenzen, kein Cache: Profil und Gedächtnis gehören zur Sitzung.)
_background_tasks: set[asyncio.Task] = set()
# Eine Sitzung je Profil (sitzungen.py, WAMO_SITZUNG_EINZELN): eine neue löst die laufende desselben Profils ab
SITZUNGEN = Sitzungen()
ENDE_MELDUNG_SECS = 0.5  # so lange darf die Meldung "sitzung_beendet" laufen, bevor die Pipeline endet
SITZUNG_FEHLER_TEXT = "Die Sitzung wurde wegen eines Fehlers beendet. Bitte noch einmal verbinden."  # fester Satz, nie ein Detail der Ausnahme

transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


def _keep(task: asyncio.Task) -> asyncio.Task:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _zugang_nach_lernen(lernen: asyncio.Task | None, zugang) -> None:
    """Schließt den Modellclient der Sitzung erst, wenn die letzte Auswertung (Lernen) fertig ist."""
    if lernen is not None:
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(lernen)
    with contextlib.suppress(Exception):
        await zugang.schliessen()


def greeting_text(name: str) -> str:
    template = os.getenv("WAMO_GREETING", DEFAULT_GREETING)
    return template.replace("{name}", name).strip()


def profil_message(identity: Identity) -> dict:
    return {"type": "profil", "name": identity.name, "rolle": identity.rolle}


def soniox_stt() -> SonioxSTTService:
    return SonioxSTTService(
        api_key=os.environ["SONIOX_API_KEY"],
        url=SONIOX_STT_URL,
        settings=SonioxSTTService.Settings(
            # Nur Deutsch, strikt: mit DE+EN wurde "Wie geht's?" als "We get." erkannt.
            # Englische Fachwörter (SQL, Statement) bleiben trotzdem erkennbar.
            language_hints=[Language.DE],
            language_hints_strict=True,
            enable_speaker_diarization=True,  # mehrere Personen im Raum -> Sprecher-Labels
            context=SonioxContextObject(terms=STT_TERMS) if STT_TERMS else None,
        ),
    )


def soniox_tts() -> LazySonioxTTSService:
    return LazySonioxTTSService(
        api_key=os.environ["SONIOX_API_KEY"],
        url=SONIOX_TTS_URL,
        # Tempo und Pausen nur, wenn SONIOX_SPEED / SONIOX_REDUCE_SILENCE gesetzt sind (stimme_einstellung); sonst Standard
        settings=SonioxTTSService.Settings(voice=SONIOX_VOICE, language=Language.DE, **stimme_einstellung()),
    )


def betrieb_message(
    pc: str, pc_latenz_ms: int | None = None, pc_steuerung: bool = False, entwicklung: bool = False,
    llm: str | None = None, pc_name: str | None = None, pc_belegt: bool = False, sprache: dict[str, str] | None = None,
    modell_modus: str | None = None, modell_quelle: str | None = None, modell_hinweis: str | None = None,
    entwicklung_grund: str | None = None,
) -> dict:
    """pc: verbindet | verbunden | gesperrt | getrennt | aus (gesperrt: Bildschirm am PC gesperrt, pc_anzeige).
    pc_steuerung: ob die PC-Werkzeuge in dieser Sitzung eingeschaltet sind (Standard aus).
    entwicklung: ob Entwicklungsaufträge in dieser Sitzung angeboten werden (WAMO_ENTWICKLUNG und Rolle).
    llm: Anzeigename des Gesprächsmodells dieser Sitzung (llm_anbieter.Wahl.anzeige).
    pc_name: Rechnername des gesteuerten PCs (aus system_info), damit die Oberfläche das Ziel zeigt.
    pc_belegt: Einschalten abgelehnt, weil eine andere Sitzung den PC gerade steuert (Büro).
    sprache: Anzeigenamen für Hören und Stimme (sprache_anbieter.SprachWahl.anzeige), Standard Soniox.
    modell_modus: wirksamer Modus dieser Sitzung (automatisch | nur_haus, boss.modus_wirksam), modell_quelle: warum
    (festgelegt: Admin, wahl: Person, keiner: kein eigener Schlüssel), modell_hinweis: warum der Boss nicht das beste
    Modell nehmen konnte (zu langsam, nicht erreichbar), für die Anzeige in der Statusleiste.
    entwicklung_grund: warum Entwicklungsaufträge im Büro aus sind (höchstens 120 Zeichen); der Aufrufer reicht ihn nur für
    Admins durch (entwicklung_betrieb.anzeige_grund), nie für Mitglieder."""
    sprache = sprache or {}
    message = {
        "type": "betrieb",
        "modus": BETRIEB_MODUS,
        "ort": MODELL_ORT,
        "modelle": {"llm": llm or lesbarer_modellname(CLAUDE_MODEL), "stt": sprache.get("stt", "Soniox"),
                    "tts": sprache.get("tts", f"Soniox {SONIOX_VOICE}")},
        "pc": pc,
        "pc_steuerung": "an" if pc_steuerung else "aus",
        "entwicklung": "an" if entwicklung else "aus",
    }
    if pc_latenz_ms is not None:
        message["pc_latenz_ms"] = pc_latenz_ms
    if pc_name:
        message["pc_name"] = pc_name
    if pc_belegt:
        message["pc_belegt"] = True
    if modell_modus:
        message["modell_modus"] = modell_modus
    if modell_quelle:
        message["modell_quelle"] = modell_quelle
    if modell_hinweis:
        message["modell_hinweis"] = modell_hinweis[:200]
    if entwicklung_grund:
        message["entwicklung_grund"] = " ".join(entwicklung_grund.split())[:entwicklung_betrieb.ANZEIGE_ZEICHEN]
    return message


def kein_modell_text(fehler: Exception, admin: bool = False) -> str:
    """Meldung an die Person, wenn kein Gesprächsmodell nutzbar ist. Die Einzelheiten (Namen von Modellen und
    Einstellungen, Gateway-Meldungen) bekommt nur ein Admin; sie stehen immer im Log. Der Text von LLMNichtVerfuegbar
    enthält nie Schlüssel oder Inhalte; ausgeblendete Namen ersetzt ausblenden_text."""
    text = "Kein Gesprächsmodell erreichbar. Bitte in ein paar Minuten noch einmal versuchen."
    if admin:
        text += f" ({ausblenden_text(' '.join(str(fehler).split()))[:180]})"
    return text


def darf_pc_freigeben(identity: Identity, pc_enabled: bool, steuerung_an: bool, haelt_sperre: bool) -> bool:
    """Darf diese Sitzung eine kritische PC-Aktion freigeben? Nur admin mit PC-Werkzeugen, eingeschalteter
    PC-Steuerung und gehaltener PC-Sperre (eine abgelöste Sitzung hält sie nie)."""
    return identity.is_admin and pc_enabled and steuerung_an and haelt_sperre


def pc_anzeige(status: str, gesperrt: bool) -> str:
    """PC-Zustand für die Oberfläche: ein verbundener PC mit gesperrtem Bildschirm ist "gesperrt"."""
    return "gesperrt" if status == "verbunden" and gesperrt else status


def rechnername(system_info_text: str) -> str | None:
    """Rechnername aus der Antwort von system_info (nur Zeichen, die in Windows-Namen vorkommen)."""
    try:
        name = json.loads(system_info_text).get("machine")
    except (ValueError, AttributeError):
        return None
    return name if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,63}", name) else None


def gate_pc_tools(
    schemas: list[FunctionSchema], allowed: Callable[[], bool], on_foreign_content: Callable[[], None],
    doppel: DoppelWache | None = None,
) -> list[FunctionSchema]:
    """Hüllt die Handler der Host-Werkzeuge: bei ausgeschalteter PC-Steuerung eine klare Absage,
    sonst Aufruf des Originals. Ergebnisse vom PC (Fenstertitel, Ausgaben) gelten als Fremdinhalt.
    doppel (WAMO_WERKZEUG_DOPPELT): derselbe Öffnen-Aufruf im selben Zug läuft nicht noch einmal, eine weitere Seite derselben
    Webseite bekommt einen Hinweis im Ergebnis (werkzeug_wache.py)."""
    gated = []
    for schema in schemas:
        original = schema.handler

        async def handler(params, _original=original):
            if not allowed():
                await params.result_callback(PC_OFF)
                return
            if doppel is not None:
                urteil = doppel.pruefen(params.function_name, params.arguments)
                if urteil.aktion == "ueberspringen":
                    await params.result_callback(urteil.ergebnis)
                    return
                if urteil.eintrag is not None:
                    params = umhuellen(params, doppel, urteil)
            on_foreign_content()
            try:
                await _original(params)
            except BaseException:  # Ausnahme oder Abbruch: kein Ergebnis, die Aktion gilt nicht als getan
                if doppel is not None and params.arguments:
                    doppel.misserfolg(params.function_name, params.arguments)
                raise

        if original is not None:
            handler.__dict__.update(getattr(original, "__dict__", {}))  # @tool_options übernehmen
        gated.append(FunctionSchema(schema.name, schema.description, schema.properties, schema.required,
                                    handler=handler if original is not None else None))
    return gated


class SessionGuard:
    """Prüft eine laufende Sitzung regelmäßig (M5): Anmeldung nicht zu alt (WAMO_ANMELDUNG_MAX_STUNDEN, ab Passwort und
    Code), Profil noch aktiv, Rolle unverändert, maximale Dauer nicht überschritten. Datenbankfehler werden einige Male
    toleriert, dann endet die Sitzung. neu_anmelden: das Ende verlangt eine neue Anmeldung (die Oberfläche meldet ab)."""

    def __init__(
        self,
        identity: Identity,
        lookup,
        *,
        interval: float = PROFILE_RECHECK_SECS,
        max_secs: float = MAX_SITZUNG_MIN * 60,
        max_failures: int = 3,
        max_anmeldung_secs: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        wanduhr: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.identity = identity
        self.lookup = lookup
        self.interval = interval
        self.max_secs = max_secs
        self.max_failures = max_failures
        self.max_anmeldung_secs = max_anmeldung_secs if max_anmeldung_secs is not None else anmeldung_max_secs()
        self.neu_anmelden = False
        self._clock = clock
        self._wanduhr = wanduhr
        self._sleep = sleep
        self._started = clock()
        self._failures = 0

    async def check(self) -> str | None:
        """Grund für das Ende der Sitzung oder None."""
        anmeldung = self.identity.anmeldung
        if anmeldung is not None and self._wanduhr() - anmeldung > self.max_anmeldung_secs:
            self.neu_anmelden = True
            return f"Die Anmeldung ist älter als {stunden_text(self.max_anmeldung_secs)}. Bitte neu anmelden."
        if self._clock() - self._started >= self.max_secs:
            return "Die maximale Sitzungsdauer ist erreicht."
        if self.lookup is None:
            return None
        try:
            profile: Profile | None = await self.lookup.get(self.identity.user_id)
        except Exception as e:  # Datenbank kurz weg: einige Male tolerieren
            self._failures += 1
            logger.warning("Profil-Neuprüfung fehlgeschlagen ({}), Versuch {}", type(e).__name__, self._failures)
            return "Das Profil lässt sich nicht mehr prüfen." if self._failures >= self.max_failures else None
        self._failures = 0
        if profile is None or not labor.profil_aktiv(profile):  # Laborprofil: nur über labor_aktiv (labor.py)
            return "Das WAMO-Profil ist nicht mehr aktiv."
        if grund := labor.aenderung(self.identity, profile):  # Art, Freischaltung oder Platz eines Laborprofils geändert
            return grund
        if profile.rolle != self.identity.rolle:
            return "Die Rolle des Profils hat sich geändert."
        return None

    async def run(self, on_end: Callable[[str], Awaitable[None]]) -> None:
        while True:
            await self._sleep(self.interval)
            reason = await self.check()
            if reason:
                await on_end(reason)
                return


@dataclass
class Session:
    """Eine gebaute Sitzung: der PipelineTask und ein idempotentes Aufräumen."""

    task: PipelineTask
    cleanup: Callable[[], Awaitable[None]]


def pausen_prompt_an(wert: str | None = None) -> bool:
    """WAMO_PAUSEN_PROMPT: neue Texte nach einer Pause, ohne Floskeln wie "Ich bin noch da" (Standard an seit der Messung vom 31.10.2026)."""
    return schalter_an("WAMO_PAUSEN_PROMPT", wert)


def pause_prompts(name: str, neu: bool | None = None) -> tuple[str, str]:
    """Texte, mit denen Pipecat Claude nach einer Pause erneut fragt. Die englischen Standardtexte
    führten dazu, dass Claude nur den Marker "●" ohne Text schickte: WAMO blieb stumm.

    neu (None liest WAMO_PAUSEN_PROMPT): Nach einer kurzen Pause erledigt WAMO den Auftrag, wenn er klar ist, sonst
    schreibt Claude nur ○ und WAMO wartet still weiter (Pipecat fragt nach der langen Frist ein zweites Mal). Erst die lange
    Stille bekommt genau eine konkrete Frage. Der lange Text verbietet ◐ und ○, damit die Kette nach zwei Texten endet; beide
    verbieten Floskeln wie "Ich bin noch da" und "Ich höre zu"."""
    neu = pausen_prompt_an() if neu is None else neu
    if neu:
        short = (
            f"{name} hat mitten im Satz kurz pausiert. Lies die letzten Bruchstücke zusammen. Ist klar, "
            "was gewollt ist, erledige es jetzt (auch mit Werkzeug) und beginne mit ● und einem Leerzeichen. "
            "Ist es nicht klar, schreib nur ○ und sonst nichts: dann wartest du still weiter. Nie ◐. "
            "Sag nie, dass du noch da bist oder zuhörst, und lade nicht mit einer Floskel zum Weiterreden ein."
        )
        long = (
            f"{name} ist seit ein paar Sekunden still. Ist aus dem Zusammenhang klar, was gewollt ist, "
            "erledige es jetzt. Sonst stell genau eine konkrete Frage zum Thema, am besten nach der fehlenden Angabe. "
            "WICHTIG: Beginne mit ● und einem Leerzeichen, dann dein Text. Nie ◐ oder ○, nie ● allein. "
            "Sag nicht, dass du noch da bist, und frag nicht, ob jemand zuhört."
        )
        return short, long
    short = (
        f"{name} hat mitten im Satz kurz pausiert. Lies die letzten Bruchstücke zusammen. Ist klar, "
        "was gewollt ist, erledige es jetzt (auch mit Werkzeug). Sonst sag einen ganz kurzen deutschen "
        "Satz, der zum Weiterreden einlädt. WICHTIG: Beginne mit ● und einem Leerzeichen, dann dein "
        "Text oder Werkzeugaufruf. Nie ◐ oder ○, nie ● allein. Beispiel: ● Ja, das Pop-up, und dann?"
    )
    long = (
        f"{name} ist seit ein paar Sekunden still. Ist aus dem Zusammenhang klar, was gewollt ist, "
        "erledige es jetzt. Sonst frag in einem kurzen deutschen Satz freundlich nach. WICHTIG: Beginne "
        "mit ● und einem Leerzeichen, dann dein Text. Nie ◐ oder ○, nie ● allein. Beispiel: ● Bin noch "
        "da, sag einfach Bescheid."
    )
    return short, long


def summary_prompt(name: str) -> str:
    return (
        f"Fasse das bisherige Gespräch zwischen {name} und WAMO auf Deutsch knapp zusammen: Themen, "
        "Entscheidungen, offene Aufträge, Namen, Zahlen, Links und was WAMO am PC getan hat. "
        "Nichts erfinden, keine Wertungen."
    )


async def build_session(transport, identity: Identity, sitzung: Sitzung | None = None) -> Session:
    """Baut Dienste, Pipeline und Ereignisse einer Sitzung, ohne Netzverbindungen aufzubauen (bis auf
    eine kurze Prüfung des LiteLLM-Gateways, wenn ein Baustein darauf steht, siehe llm_anbieter.py)."""
    # Laborprofil (labor.py): eigener Zweig. Annahme nur bei offenem Durchlauf mit gültiger Einwilligung (sonst LaborAbgelehnt), Speicher ohne
    # Gedächtnis, Laborprompt, Erlaubnisliste der Werkzeuge. PC, Geräte, Entwicklung, Boss, Lernen und Fernzugriff gibt es dort nie.
    labor_sitzung = await LaborSitzung.starten(identity) if labor.ist_labor(identity) else None
    memory = labor_sitzung.memory if labor_sitzung else MemoryStore.from_env(identity.user_id)
    is_admin = identity.is_admin
    # PC-Werkzeuge. Alter Weg: admin, im Büro zusätzlich an diesen PC gebunden (pc_host.py). Tunnel je Gerät
    # (WAMO_PC_REGISTER=an): die Sitzung bekommt nur IHREN Laptop aus dem Geräteregister, sonst keine (pc_geraet.py).
    geraet_zugang = await pc_zugang(identity) if register_modus() and not labor_sitzung else None
    pc_enabled = False if labor_sitzung else (geraet_zugang is not None if register_modus() else pc_host_fuer(identity))
    entwicklung = is_admin and entwicklung_enabled()
    # Fernzugriff auf die Sparks (rd_api): WAMO kennt ihn und kann den Dialog öffnen, nur für Admins und nur bei eingeschaltetem Schalter
    from rd_api import aktiv as rd_aktiv
    fernzugriff = is_admin and rd_aktiv()
    problem = entwicklung_problem() if entwicklung else None
    im_buero = buero()
    if problem and not im_buero:
        logger.error("Entwicklungsaufträge aus: {}", problem)
        entwicklung = False
    # Büro: Admin, Profilbindung und eigener Zugang (hier und nach zugang_laden), Grund für Log und Statusleiste der Admins;
    # Unterwegs bleibt alles wie bisher (entwicklung_betrieb.py)
    ent_grund = entwicklung_betrieb.grund_vorab(identity.user_id, is_admin, problem) if im_buero else None
    if im_buero:
        entwicklung = ent_grund is None
    if labor_sitzung:  # Entwicklungsgate am labor_art (labor.entwicklung_arten): Fernzugriff nie, Entwicklung bis zur Verdrahtung nie
        entwicklung, fernzugriff, ent_grund = False, False, "Entwicklungsaufträge gibt es im Labor nicht"
    # PC-Steuerung je Sitzung standardmäßig aus; nur ein Admin schaltet sie in der UI ein.
    state: dict = {"pc": "verbindet" if pc_enabled else "aus", "pc_latenz_ms": None, "pc_steuerung": False,
                   "client_ready": False, "live": True, "cleaned": False, "guard": None,
                   "pc_weg": False, "pc_tools": False, "pc_waechter": None, "geraet_wache": None, "labor_wache": None,  # pc_weg: Hinweis im Prompt
                   "geraet_ok": geraet_zugang is None, "geraet_entzogen": False,  # Tunnel je Gerät: erst nach dem Namensabgleich, nie nach einem Entzug
                   "pc_name": None, "pc_belegt": False,
                   "pc_gesperrt": False, "sperr_probe": None}  # Bildschirm am PC gesperrt (Host: status=locked)
    # Kennung dieser Sitzung für die PC-Sperre (Sitzung.pc_lease_nehmen) und ihre Ablösung (sitzungen.py)
    sitzung = sitzung or Sitzung(identity.user_id)
    if geraet_zugang is not None:
        sitzung.geraet_id = geraet_zugang.geraet.device_id  # die PC-Sperre gilt je Gerät

    def prompt_for(recall: Recall) -> str:
        if labor_sitzung:  # immer der Laborprompt, nie der normale mit Gedächtnis, PC und Entwicklung
            return labor_sitzung.prompt()
        return build_system_prompt(
            identity.name, identity.anrede, recall.persoenlich, recall.gemeinsam,
            pc_werkzeuge=pc_enabled, entwicklung=entwicklung, pc_erreichbar=not state["pc_weg"], fernzugriff=fernzugriff,
            abnahme_befehle=abnahme_befehle_im_betrieb(), repo_pflicht=im_buero,
        )

    # Gedächtnis je Sitzung laden (kein prozessweiter Cache: jedes Profil sieht nur seins).
    recall = await memory.recall()

    # Modellzugang des Profils (modellzugang.py): eigener Claude-Schlüssel oder nur Hausmodelle. Jede Sitzung hat
    # ihren eigenen Client; der Schlüssel des Kerns gilt nur für das zugeordnete Profil.
    # Persona als system_instruction (Einzelheiten in llm_anbieter.gespraech_dienst).
    # Beide Prüfungen parallel, damit ein langsames Gateway den Start nur einmal verzögert.
    # summary_llm None: Pipecat fasst mit dem Gesprächsmodell zusammen.
    if labor_sitzung:  # labor_art aus der Identität (nie aus dem Gespräch), sobald zugang_laden es kennt (Quelle labor)
        zugang = await zugang_laden(identity.user_id, **labor.zugang_parameter(identity))
    else:
        zugang = await zugang_laden(identity.user_id)
    dev_quelle = entwicklung_quelle(zugang)
    if im_buero and entwicklung and (quelle_grund := entwicklung_betrieb.grund_quelle(dev_quelle)):
        ent_grund, entwicklung = quelle_grund, False  # Büro: nur ein eigener Zugang zählt, uebergang wie keine
    elif entwicklung and dev_quelle == "keine":
        # Claude Code läuft über Anthropic, mit dem Zugang der Person (boss.entwicklung_quelle): Claude-Code-Token oder
        # eigener API-Schlüssel. Ohne beides, oder bei nur Hausmodellen, startet kein Entwicklungsauftrag
        logger.warning("Entwicklungsaufträge aus: Profil {} hat keinen eigenen Zugang für Claude Code oder nutzt nur Hausmodelle",
                       zugang.kennung)
        entwicklung = False
    if im_buero:  # eine Logzeile je Sitzung, auch bei Schalter aus
        entwicklung_betrieb.protokollieren(entwicklung, ent_grund, identity.user_id, fehler=bool(problem))
    try:
        (llm, llm_wahl), summary_llm = await asyncio.gather(
            gespraech_llm(prompt_for(recall), zugang), zusammenfassung_llm(zugang)
        )
    except Exception:
        await zugang.schliessen()  # ein bei der Prüfung gebauter Client darf nicht offen bleiben
        raise

    # Spracherkennung und Sprachausgabe je Profil (Zugang.stt/tts) oder für alle (WAMO_STT_ANBIETER/WAMO_TTS_ANBIETER):
    # Soniox oder im Haus über LiteLLM, mit Rückfall auf Soniox (sprache_anbieter.py)
    sprache = await sprach_wahl(zugang)
    # Ein Schlüssel im Gesagten wird vor allem anderen entfernt (schluessel_filter.py): Verlauf, Modell, Gedächtnis, Untertitel
    stt = absichern(haus_stt(sprache.stt, STT_TERMS) if sprache.stt.anbieter == "haus" else soniox_stt())
    tts = haus_tts(sprache.tts) if sprache.tts.anbieter == "haus" else soniox_tts()

    transcript: list[tuple[str, str]] = []  # fürs Lernen
    # Fremdinhalte im laufenden Zug (Websuche, Bildschirm, PC, Entwicklungs-Ergebnisse) merken: kein
    # sofort wirksames gemeinsames Wissen (M4) und kein Lernen aus diesen Abschnitten.
    turn = TurnState(transcript_len=lambda: len(transcript))
    # Labor: Text, Klicks auf Freigabekarten und Suchen zählen je Profil und Durchlauf, nicht je Verbindung (labor_limit.LimitRegister): eine
    # Neuverbindung setzt nichts zurück. Das Schaufenster ohne Platz hat keinen Durchlauf, dort zählt das Profil allein.
    labor_grenzen = (labor_limit.LIMITS.fuer(identity.user_id, labor_sitzung.stand.durchlauf_id if labor_sitzung.stand else None)
                     if labor_sitzung else None)
    if labor_sitzung:  # Laborprofil: nur die Erlaubnisliste, nie merken oder Fernzugriff (labor.WERKZEUGE_BASIS)
        standard_tools = list(register_tools(llm, memory, identity, turn, zugang=zugang, erlaubt=labor.WERKZEUGE_BASIS,
                                             suchen_zaehler=labor_grenzen.suchen).standard_tools)
    else:
        standard_tools = list(register_tools(llm, memory, identity, turn, zugang=zugang, fernzugriff=fernzugriff).standard_tools)
    # Vorgänge (vorgaenge.py): was WAMO gerade abarbeitet, live für die Tafel "Vorgänge". Alle Änderungen sind synchron, das
    # Senden und Speichern machen Hintergrundtasks mit Frist: der Audiopfad wartet nie darauf. send entsteht weiter unten.
    # Labor: keine Ablage in der Datenbank (vorgaenge bleibt leer, labor_speicher.py)
    vorgaenge = Vorgaenge(lambda d: send(d), speichern=None if labor_sitzung else datenbank_ablage(memory))

    # Lange Werkzeugausgaben verlustarm kürzen (ausgaben_kuerzen.py, WAMO_AUSGABE_SCHWELLE, 0 = aus): das Original bleibt im
    # Speicher dieser Sitzung, ausgabe_nachladen holt Teile gezielt zurück. Labor: kein Kürzer, kein Nachladen (nicht in der Erlaubnisliste)
    ausgaben = Ausgaben(schwelle=0) if labor_sitzung else Ausgaben()
    if ausgaben.schwelle > 0:
        standard_tools.append(register_nachladen(llm, ausgaben, turn.taint))
    dev: DevAgent | None = None
    if entwicklung:
        # Entwicklungsaufträge an Claude Code; Start und Freigaben per Klick in der UI (dev_agent.py). Mit eigenem
        # Zugang läuft der Auftrag auf dem Token oder Schlüssel dieses Profils (je Lauf frisch entschlüsselt, siehe
        # dev_agent.persoenlicher_zugang), ohne ihn nur das Profil des Kernschlüssels.
        # beurteiler: das günstigste Modell des Profils beurteilt die Aussagekriterien der Abnahme (abnahme.py)
        # Büro: die Zulassung entsteht allein hier, aus der geprüften Identität (Admin, Profil aus WAMO_ENTWICKLUNG_PROFILE);
        # ohne sie wirft der Konstruktor. Unterwegs bleibt sie None.
        dev = DevAgent(memory, auftraggeber=identity.name, vorgaenge=vorgaenge, beurteiler=llm_beurteiler(zugang),
                       zulassung=entwicklung_betrieb.zulassung_erteilen(identity.user_id, is_admin) if im_buero else None)
        dev.on_result = turn.taint
        dev.register(llm)
        standard_tools += dev.schemas()
    context = LLMContext(tools=ToolsSchema(standard_tools=standard_tools))

    # PC-Werkzeuge nur für admin; im Hintergrund dazuholen (lokal per stdio, im Büro über den Tunnel).
    pc_client = host_client(geraet_zugang) if pc_enabled else None

    def pc_allowed() -> bool:
        # Abgelöst oder Sperre verloren (Büro): sofort keine PC-Aktionen mehr, auch vor dem Trennen
        return bool(state["pc_steuerung"]) and state["geraet_ok"] and sitzung.pc_lease_haelt()

    # Bildschirm sehen/klicken: Foto vom Host, Bildmodell entscheidet (siehe screen.py)
    # Bildschirm sehen und klicken: Das Bild geht an das Bildmodell, deshalb nur für admin, bis die Einwilligung der
    # Mitglieder geregelt ist (Bauplan S6)
    screen_schemas = (register_screen_tools(llm, lambda: pc_client, pc_allowed, turn.taint, zugang=zugang)
                      if pc_client and is_admin else [])

    # WAMO Boss (boss.py): Aufgaben an Spezialisten verteilen; Karte unter Vorgänge, Ergebnis als Daten (turn.taint).
    # Die Art "bild" gibt es nur mit PC-Steuerung und Rolle admin, wie bildschirm_ansehen.
    async def bildschirm_foto():
        if not pc_allowed():
            raise RuntimeError("PC-Steuerung aus")
        return await screen_foto(pc_client)

    # Labor: kein Boss (nicht in der Erlaubnisliste), er wird nicht einmal gebaut
    boss = None if labor_sitzung else Boss(
        zugang, senden=lambda d: send(d), melden=lambda t: melde_llm(t), fremdinhalt=turn.taint,
        gefaerbt=lambda: turn.tainted, auftraggeber=identity.name, canvas=lambda item: canvas_legen(item),
        bild_quelle=bildschirm_foto if (pc_client and is_admin) else None, vorgaenge=vorgaenge)
    if boss is not None:
        boss.register(llm)
        standard_tools += boss.schemas()
        context.set_tools(ToolsSchema(standard_tools=standard_tools))

    learned_upto = 0  # bis zu diesem Index in transcript ist ausgewertet
    learn_task: asyncio.Task | None = None
    pause_short, pause_long = pause_prompts(identity.name)

    soft_gate = SoftInterruptGate(
        max_words=SOFT_INTERRUPT_MAX_WORDS,
        playback_lag_secs=SOFT_INTERRUPT_LAG_SECS,
        grenze=stopp_grenze_an(),  # Stopp an der nächsten hörbaren Wortgrenze (WAMO_STOPP_GRENZE)
        ducken=ducken_an(),  # Stimme vorsorglich leiser, sobald die Person spricht (WAMO_DUCKEN)
    )

    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        # Lange Gespräche zusammenfassen, bevor der Prompt teuer wird
        assistant_params=LLMAssistantAggregatorParams(
            enable_auto_context_summarization=True,
            auto_context_summarization_config=LLMAutoContextSummarizationConfig(
                max_context_tokens=SUMMARY_TRIGGER_TOKENS,
                max_unsummarized_messages=None,
                summary_config=LLMContextSummaryConfig(
                    target_context_tokens=SUMMARY_TARGET_TOKENS,
                    min_messages_after_summary=8,
                    summarization_prompt=summary_prompt(identity.name),
                    summary_message_template="Zusammenfassung des bisherigen Gesprächs: {summary}",
                    llm=summary_llm,
                ),
            ),
        ),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
            user_turn_strategies=UserTurnStrategies(
                # Unterbrechen entscheidet das SoftInterruptGate, nicht die Strategie.
                start=[
                    MeaningfulWordsUserTurnStartStrategy(
                        min_words=MIN_INTERRUPT_WORDS,
                        enable_interruptions=False,
                        # Echo der eigenen Stimme am Mikrofon löst keine Unterbrechung aus (soft_interrupt.ist_echo)
                        gesprochen=soft_gate.kuerzlich_gesprochen if echo_filter_an() else None,
                        beurteilt=soft_gate.beurteilt,  # Rückmeldung oder Echo: Dämpfung zurücknehmen
                        echo_modus=echo_filter_an() and echo_modus_an(),  # bei erkennbarem Echo unterbricht nur ein Stoppwort
                        # Rückmeldelaut bei laufendem Werkzeug startet keinen Zug (WAMO_RUECKMELDUNG_WERKZEUG)
                        werkzeug_laeuft=soft_gate.werkzeug_laeuft if rueckmeldung_werkzeug_an() else None,
                    )
                ],
                # Satzende: Smart-Turn schlägt vor, Claude entscheidet, ob die Person fertig ist.
                # Bei Bruchstücken ("SQL Statement.") wartet WAMO still, statt sofort zu antworten.
                stop=[
                    # Smart-Turn hält kurze Antworten ("Ja") oft für unfertig und wartet dann
                    # stop_secs Stille ab (Standard 3 s). Claude entscheidet danach ohnehin mit.
                    deferred(
                        TurnAnalyzerUserTurnStopStrategy(
                            turn_analyzer=LocalSmartTurnAnalyzerV3(
                                params=SmartTurnParams(stop_secs=SMART_TURN_STOP_SECS)
                            )
                        )
                    ),
                    LLMTurnCompletionUserTurnStopStrategy(
                        config=UserTurnCompletionConfig(
                            incomplete_short_timeout=TURN_INCOMPLETE_SHORT_SECS,
                            incomplete_long_timeout=TURN_INCOMPLETE_LONG_SECS,
                            incomplete_short_prompt=pause_short,
                            incomplete_long_prompt=pause_long,
                        )
                    ),
                ],
            ),
        ),
    )

    aktivitaet = ActivityReporter()  # Rückruf für Werkzeugergebnisse folgt unten (pc_ergebnis)
    # Gegen doppelte Antworten: dieselbe Lage geht nur einmal an das Modell (turn_wache.py)
    wache = TurnWache()
    # Doppelte Aufrufe im selben Zug und abgebrochene Werkzeuge (werkzeug_wache.py)
    doppel = DoppelWache(turn_nr=lambda: turn.turn_nr) if werkzeug_doppelt_an() else None

    def freigabe_offen() -> bool:
        """Wartet eine Freigabe (PC-Freigabe-Karte oder Entwicklungsauftrag)? Dann greift der Ja-Wächter nie."""
        # Fail closed: fehlt ein erwartetes Attribut (Umbenennung), gilt eine Freigabe als offen
        try:
            if freigaben is not None and freigaben._offen:
                return True
            if labor_werkz is not None and labor_werkz.freigaben.offen:  # Labor-Karte (labor_freigaben.py)
                return True
            return dev is not None and any(j.pending for j in dev._jobs.values())
        except AttributeError:
            logger.warning("Ja-Wächter: Freigabe-Zustand nicht lesbar, es gilt als offen")
            return True

    pipeline = Pipeline(
        [
            transport.input(),
            AudioInputMonitor(),  # Diagnose: Lücken und Pegel des Browser-Mikrofons, Stille-Wächter
            stt,
            SchluesselFilter(),  # zweite Stufe: auch Text, der nicht aus der Spracherkennung kommt
            SpeakerTracker(),  # wer spricht -> Farbe im Stimmen-Rand
            user_agg,
            soft_gate,
            *([JaWaechter(offen=freigabe_offen, gefaerbt=lambda: turn.tainted)] if ja_waechter_an() else []),  # Ja auf Rückfrage: ausführen (WAMO_JA_WAECHTER)
            *([AbbruchHinweis(name="AbbruchHinweisVorne")] if abbruch_hinweis_an() else []),  # CANCELLED im Verlauf erklären (WAMO_ABBRUCH_HINWEIS)
            wache.vorne(),
            llm,
            wache.hinten(),
            *([AbbruchHinweis(name="AbbruchHinweisHinten")] if abbruch_hinweis_an() else []),  # Anfragen von hinten laufen stromaufwärts: erst hier, dann die Wache
            *([Wartesatz(nach_secs=wartesatz_secs(), werkzeug=wartesatz_an(),
                         modell_secs=wartesatz_modell_secs() if wartesatz_modell_an() else None)]
              if (wartesatz_an() or wartesatz_modell_an()) else []),  # langer Werkzeugaufruf oder langsames Modell: kurzer Satz
            aktivitaet,  # Werkzeugaufrufe live an die Oberfläche (Zeitleiste)
            *([AusgabenKuerzer(ausgaben)] if ausgaben.schwelle > 0 else []),  # lange Ergebnisse kürzen, hinter der Zeitleiste (sie sieht das Original)
            tts,
            transport.output(),
            PlaybackTracker(soft_gate),
            assistant_agg,
        ]
    )

    # Latenz je Zug ins Log (WAMO_LATENZ_LOG=1, latenz.py): Sprechende, Turn-Ende, STT, Modell, API, TTS, Ausgabe
    latenz_beobachter = LatenzObserver() if latenz_log_an() else None
    if latenz_beobachter:
        api_messen(llm, latenz_beobachter)
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[latenz_beobachter] if latenz_beobachter else [],
        # Labor: Client-Nachrichten gegen die Erlaubnisliste prüfen, bevor Pipecat sie auswertet (labor_rtvi.py)
        rtvi_processor=(LaborRTVIProcessor(art=identity.labor_art, begrenzer=labor_grenzen.text, klicks=labor_grenzen.klicks)
                        if labor_sitzung else None),
    )
    if dev:
        dev.attach(task)
    try:
        directory = ProfileDirectory(shared_database())
        profiles = directory if directory.configured else None
    except ValueError:
        profiles = None
    guard = SessionGuard(identity, profiles)

    async def send(data: dict) -> None:
        await task.queue_frames([RTVIServerMessageFrame(data=data)])

    async def canvas_legen(item: dict) -> None:
        """Ergebnis einer Boss-Aufgabe aufs Canvas, gespeichert wie bei canvas_zeigen."""
        item["id"] = await memory.save_canvas(CANVAS_TYPE[item["kind"]], item["title"], item)
        await send({"type": "canvas", "item": item})

    async def send_betrieb() -> None:
        await send(betrieb_message(pc_anzeige(state["pc"], state["pc_gesperrt"]), state["pc_latenz_ms"],
                                   state["pc_steuerung"], entwicklung, llm=llm_wahl.anzeige, pc_name=state["pc_name"],
                                   pc_belegt=state["pc_belegt"], sprache=sprache.anzeige(SONIOX_VOICE),
                                   modell_modus=modus_wirksam(zugang), modell_quelle=zugang.quelle,
                                   modell_hinweis=llm_wahl.grund,
                                   entwicklung_grund=entwicklung_betrieb.anzeige_grund(is_admin, ent_grund)))

    async def ansage(text: str) -> None:
        """Kurze Meldung im Gespräch (Live-Bild an oder aus): wird gesprochen, das Modell erfährt es ohne zu antworten."""
        if state["live"]:
            await task.queue_frames([TTSSpeakFrame(text),
                                     LLMMessagesAppendFrame(messages=[{"role": "user", "content": f"Systemhinweis: {text}"}],
                                                            run_llm=False)])

    # Live-Bild des eigenen Laptops: nur Admin mit gebundenem Gerät (Tunnel je Gerät), PC-Steuerung an, Sperre gehalten
    def zugangstor():
        import wamo_api  # erst hier: wamo_api wird nach bot.py geladen
        return wamo_api.gate_holen()

    bild = Bildstrom(
        client=lambda: pc_client, darf=lambda: bool(is_admin and geraet_zugang is not None and pc_allowed()),
        senden=send, ansage=ansage, aufruf=call_host_tool,
        # Fernanfrage vom Handy: Token dieser Sitzung mit frischem Code des zweiten Faktors (bild_fern.py)
        fern_pruefen=bild_fern.pruefer(identity.user_id, zugangstor),
    ) if (pc_client is not None and is_admin and geraet_zugang is not None) else None

    async def set_sperre(gesperrt: bool) -> None:
        """Bildschirm am PC gesperrt oder entsperrt (Werkzeugergebnis, Tunnel-Probe, system_info)."""
        if gesperrt == state["pc_gesperrt"]:
            return
        state["pc_gesperrt"] = gesperrt
        logger.info("PC-Bildschirm {}", "gesperrt" if gesperrt else "entsperrt")
        if gesperrt and bild is not None:
            await bild.stoppen("Der Bildschirm am Laptop ist gesperrt. Das Live-Bild ist beendet.")
        # Lokal (stdio) gibt es keine Tunnel-Probe: solange gesperrt, regelmäßig nachfragen, damit die Anzeige
        # nach dem Entsperren nicht stehen bleibt
        probe = state["sperr_probe"]
        if gesperrt and state["live"] and not ist_tunnel(pc_client) and (probe is None or probe.done()):
            state["sperr_probe"] = _keep(asyncio.create_task(lokale_sperr_probe()))
        if state["live"] and state["client_ready"]:
            await send_betrieb()

    async def lokale_sperr_probe() -> None:
        while state["live"] and state["pc_gesperrt"]:
            await asyncio.sleep(SPERR_PROBE_SECS)
            if await pc_sperre_pruefen(pc_client) is False:
                await set_sperre(False)

    async def pc_ergebnis(name: str, _args: dict, result: object) -> None:
        """Jedes Werkzeugergebnis (activity.ActivityReporter): status=locked oder ein Erfolg am Bildschirm."""
        if pc_client is None:
            return
        gesperrt = sperre_aus_ergebnis(name, result)
        if gesperrt is not None:
            await set_sperre(gesperrt)

    aktivitaet.on_ergebnis = pc_ergebnis

    def markierung_von() -> tuple[str | None, str | None]:
        """Welcher Laptop handelt (Gerätekennung im Tunnel je Gerät, Rechnername): nur dort zeichnet die Desktop-App den
        Lichtrahmen. Ohne PC-Werkzeuge gibt es keinen Rahmen."""
        return (geraet_zugang.geraet.kennung if geraet_zugang is not None else None), state["pc_name"]

    if pc_client is not None:
        aktivitaet.markierung_von = markierung_von

    async def melde_llm(text: str) -> None:
        """Kurze Nachricht an das Sprachmodell, das dann antwortet (Ergebnis einer PC-Freigabe)."""
        if state["live"]:
            turn.turn_nr += 1  # eine Meldung von WAMO selbst (z. B. nach einer Freigabe) ist ein neuer Anlass, kein Doppelaufruf
            await task.queue_frames([LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}], run_llm=True)])

    # Freigaben kritischer PC-Aktionen (pc_freigaben.py): nur mit PC-Werkzeugen; die Karte entsteht in activity.py
    freigaben = PcFreigaben(
        memory, client=lambda: pc_client, melden=melde_llm, fremdinhalt=turn.taint, senden=send, von=identity.name,
        darf=lambda: darf_pc_freigeben(identity, pc_enabled, bool(state["pc_steuerung"]), sitzung.pc_lease_haelt()),
        pc_name=lambda: state["pc_name"], markierung_von=markierung_von,
    ) if pc_client else None
    if freigaben is not None and is_admin:  # Mitglieder können nichts freigeben: keine Karte, die ins Leere läuft
        aktivitaet.on_freigabe = freigaben.anfrage

    # Testlabor (labor_werkzeuge.py): nur Laborprofile, nach der Annahme oben (labor_sitzung.py). Werkzeuge (ticket_*, mail_senden) und Freigabekarte.
    # Fällt der Dienst zwischen Annahme und diesem Schritt aus (oder endet der Durchlauf dort), gibt es keine Sitzung: die Person bekommt dieselbe
    # Meldung wie bei der Annahme, nicht eine Ausnahme aus dem Dienstclient, und der schon gebaute Modellzugang wird geschlossen.
    labor_werkz = None  # nie "labor": so heißt das Modul (labor.py), das build_session weiter oben benutzt
    if labor_sitzung:
        try:
            labor_werkz = await labor_werkzeuge.labor_sitzung(identity, memory, llm, turn_taint=turn.taint, senden=send, melden=melde_llm)
        except (labor_werkzeuge.LaborNichtBereit, LaborFehler, LaborKonfigFehler) as e:
            logger.warning("Laborsitzung: Werkzeuge nicht bereit ({})", type(e).__name__)
            await zugang.schliessen()
            raise labor.LaborAbgelehnt(labor_durchlauf.GRUND_DIENST, 503, labor_durchlauf.CODE_DIENST) from e
        except Exception:  # jede andere Ausnahme: der schon gebaute Modellzugang bleibt nicht offen, bot() beendet die Verbindung
            await zugang.schliessen()
            raise
        if labor_werkz is not None:  # None: das Schau-Profil ohne Platz hat kein Test-Jira
            standard_tools += labor_werkz.schemas
            context.set_tools(ToolsSchema(standard_tools=standard_tools))
            # Der Klick auf die Freigabekarte (Nachricht freigabe mit job labor) geht über die Annahme (Schicht a in on_client_message) hierher
            labor_sitzung.freigabe = labor_werkz.entscheiden
        # Die Grenze: Namen im Kontext und am LLM müssen der Erlaubnisliste entsprechen, sonst entsteht keine Sitzung. Sie gilt für das Endergebnis,
        # also erst nach den Werkzeugen von labor_werkzeuge.
        try:
            labor_sitzung.werkzeuge_pruefen(llm, standard_tools, mit_tickets=labor_werkz is not None)
        except Exception:  # LaborPolitikFehler und alles andere: Dienstclient und Modellzugang schließen, bot() beendet die Verbindung
            if labor_werkz is not None:
                await labor_werkz.schliessen()
            await zugang.schliessen()
            raise

    async def refresh_prompt() -> None:
        await task.queue_frame(
            LLMUpdateSettingsFrame(delta=LLMSettings(system_instruction=prompt_for(await memory.recall())))
        )

    async def set_pc(status: str, latenz_ms: int | None = None) -> None:
        """PC-Zustand setzen; beim Wechsel erreichbar/nicht erreichbar auch den Hinweis im Prompt."""
        weg = status == "getrennt"
        hinweis_neu = weg != state["pc_weg"]
        state["pc"], state["pc_weg"] = status, weg
        if latenz_ms is not None:
            state["pc_latenz_ms"] = latenz_ms
        if not state["live"]:
            return
        if hinweis_neu:
            await refresh_prompt()  # WAMO sagt dann "PC nicht erreichbar" statt Umwege zu versuchen
        if state["client_ready"]:
            await send_betrieb()

    async def attach_pc_tools(melde_fehler: bool = True) -> bool:
        pc = await host_tools(pc_client)
        if pc and geraet_zugang is not None:
            # Plausibilität: der Host meldet denselben Rechnernamen, den das Register für das Gerät führt; sonst keine
            # Werkzeuge (fail closed, auch wenn system_info nicht antwortet)
            gemeldet = None
            with contextlib.suppress(Exception):
                gemeldet = rechnername(await asyncio.wait_for(call_host_tool(pc_client, "system_info", {}), 5))
            if not rechnername_passt(geraet_zugang.geraet, gemeldet):
                logger.warning("WAMO-GERAET Rechnername des Hosts passt nicht zum Gerät {}: keine PC-Werkzeuge",
                               geraet_zugang.geraet.kennung)
                pc = None
                state["geraet_ok"] = False
            elif not state["geraet_entzogen"]:
                state["geraet_ok"] = True
        if not pc and not melde_fehler:
            return False
        latenz = None
        if pc:
            pc_schemas = gate_pc_tools(list(pc.standard_tools), pc_allowed, turn.taint, doppel)
            context.set_tools(ToolsSchema(standard_tools=[*standard_tools, *pc_schemas, *screen_schemas]))
            state["pc_tools"] = True
            started = time.monotonic()
            with contextlib.suppress(Exception):  # Latenz, Rechnername und Sperre sind nur Anzeigen
                info = await asyncio.wait_for(call_host_tool(pc_client, "system_info", {}), 5)
                latenz = round((time.monotonic() - started) * 1000)
                state["pc_name"] = rechnername(info)
                if (gesperrt := sperre_aus_ergebnis("system_info", info)) is not None:
                    await set_sperre(gesperrt)
        await set_pc("verbunden" if pc else "getrennt", latenz)
        if pc and geraet_zugang is not None:
            _keep(asyncio.create_task(geraet_zugang.gesehen()))  # last_seen_at, höchstens einmal je Minute
        return bool(pc)

    async def geraet_entzogen(grund: str) -> None:
        """Das Gerät wurde gesperrt oder widerrufen (oder das Register ist länger nicht erreichbar): PC-Steuerung dieser
        Sitzung endet sofort, die Sperre wird frei, WAMO sagt es."""
        state["geraet_ok"], state["geraet_entzogen"] = False, True  # danach schaltet auch ein Klick in der Oberfläche nichts ein
        state["pc_steuerung"] = False
        sitzung.pc_lease_freigeben()
        state["pc_tools"] = False
        if bild is not None:
            await bild.stoppen("Dein Laptop wurde gesperrt oder die Freigabe wurde widerrufen. Das Live-Bild ist beendet.")
        wachter = state["pc_waechter"]
        if wachter is not None and wachter is not asyncio.current_task():
            wachter.cancel()
        if pc_client is not None:
            with contextlib.suppress(Exception):
                await pc_client.close()
        with contextlib.suppress(Exception):
            await set_pc("getrennt")
            await melde_llm(f"Systemhinweis, bitte kurz und ruhig weitergeben: {grund} WAMO kann den Laptop jetzt nicht mehr bedienen.")

    async def pc_wechsel(erreichbar: bool, latenz_ms: int | None) -> None:
        """Tunnel weg oder zurück (pc_tunnel.TunnelHost.ueberwache); Werkzeuge beim ersten Erfolg anhängen."""
        if erreichbar and not state["pc_tools"]:
            await attach_pc_tools()
        else:
            await set_pc("verbunden" if erreichbar else "getrennt", latenz_ms)

    tools_bereit = asyncio.Event()  # Werkzeuge der Sitzung stehen fest (Wärmen des Prompt-Caches wartet darauf)
    if not pc_client:
        tools_bereit.set()

    async def start_pc() -> None:
        try:
            # Beim Sitzungsaufbau lädt der Kern Modelle; ein erster Fehlschlag zählt noch nicht als "nicht erreichbar"
            if not await attach_pc_tools(melde_fehler=False):
                await asyncio.sleep(2)
                await attach_pc_tools()
        finally:
            tools_bereit.set()
        if ist_tunnel(pc_client) and state["live"]:
            state["pc_waechter"] = _keep(asyncio.create_task(pc_client.ueberwache(pc_wechsel, set_sperre)))
        if geraet_zugang is not None and state["live"]:
            state["geraet_wache"] = _keep(asyncio.create_task(geraet_wache(geraet_zugang, geraet_entzogen)))

    async def cache_vorwaermen() -> None:
        """Prompt-Cache vorwärmen, während die Begrüßung läuft (cache_waerme.py). Die Werkzeuge gehören zum
        Cache-Schlüssel, deshalb erst, wenn der PC-Host seine Werkzeuge geliefert hat (höchstens 6 s warten)."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(tools_bereit.wait(), 6)
        if state["live"]:
            await vorwaermen(llm, context.tools)

    if pc_client:
        _keep(asyncio.create_task(start_pc()))

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_ready(_rtvi):
        state["client_ready"] = True
        await send(profil_message(identity))
        await send_betrieb()
        vorgaenge.erneut_senden()  # die Oberfläche (wieder) verbunden: alle bekannten Vorgänge noch einmal, ohne zu warten

    @task.rtvi.event_handler("on_client_message")
    async def on_client_message(_rtvi, msg):
        # Klicks aus der UI. Kommt nur über die eigene, angemeldete WebRTC-Verbindung.
        data = msg.data if isinstance(msg.data, dict) else {}
        if labor_sitzung:
            # Schicht a der Laborsperre: nur die Freigabe mit job labor wirkt (Schicht b filtert schon vor dem RTVI-Verarbeiter)
            await labor_sitzung.client_nachricht(msg.type, msg.data)
            return
        if msg.type == "vergessen":
            memory_id = str(data.get("id", ""))
            ok = await memory.forget(memory_id)
            logger.info("vergessen {}: {}", memory_id, "ok" if ok else "abgelehnt")
            await send({"type": "vergessen", "id": memory_id, "ok": ok})
            if ok:
                await refresh_prompt()  # WAMO soll den Eintrag sofort nicht mehr nutzen
        elif msg.type == "pc_steuerung":
            # Nur ein Admin mit eingerichtetem PC-Host darf einschalten; ausschalten geht immer.
            # Im Büro steuert höchstens eine Sitzung den PC (pc_host.pc_lease_nehmen); eine abgelöste nie wieder.
            an = bool(data.get("an") is True and pc_enabled and state["geraet_ok"])
            state["pc_belegt"] = an and not sitzung.pc_lease_nehmen()
            if state["pc_belegt"] or not an:
                an = False
                sitzung.pc_lease_freigeben()
            state["pc_steuerung"] = an
            if not an and bild is not None:
                await bild.stoppen("Die PC-Steuerung ist aus. Das Live-Bild ist beendet.")
            logger.info("PC-Steuerung {}{}", "an" if an else "aus", " (andere Sitzung steuert)" if state["pc_belegt"] else "")
            await send_betrieb()
        elif msg.type == "bildstrom":
            # Live-Bild: ohne Admin, gebundenes Gerät und PC-Steuerung gibt es nichts (Bilder sendet nur bildstrom.py)
            if bild is None:
                await send({"type": "bildstrom", "zustand": "aus", "grund": "nicht_erlaubt", "text": BILD_NICHT_ERLAUBT})
            else:
                await bild.nachricht(data)
        elif msg.type == "freigabe" and data.get("job") == "pc":
            # PC-Freigabe vor den Entwicklungsaufträgen. Ohne PC-Werkzeuge (z. B. Mitglied) gibt es keine.
            if freigaben is None:
                logger.warning("PC-Freigabe ohne PC-Werkzeuge in dieser Sitzung abgewiesen")
                return
            await freigaben.entscheiden(data)
        elif msg.type == "freigabe" and data.get("job") == "labor":
            # Labor-Freigaben (labor_freigaben.py) gibt es nur in einer Laborsitzung, dort gehen sie oben über labor_sitzung.client_nachricht an
            # labor_werkz.entscheiden. Hier, in jeder anderen Sitzung, auch bei einem Admin, ist der Klick ohne Wirkung.
            logger.warning("Labor-Freigabe ohne Laborsitzung abgewiesen")
        elif msg.type == "modellzugang":
            # Eigener Claude-Schlüssel: hinterlegen, ersetzen, löschen, Stand (gilt ab der nächsten Sitzung).
            # Der Inhalt dieser Nachricht wird nie geloggt und nie zurückgeschickt.
            await modellzugang_nachricht(identity.user_id, data, send)
        elif msg.type == "kontingent" and not dev:
            # Anzeige "Verbrauch": gespeicherter Stand des Max-Abos dieses Profils. Mit Entwicklungsaufträgen in der
            # Sitzung antwortet der DevAgent (er legt Beobachtungen des laufenden Auftrags darüber), sonst liest ihn der Kern.
            try:
                kontingent_db = shared_database()
            except ValueError:
                kontingent_db = None
            if kontingent_db is not None and not kontingent_db.configured:
                kontingent_db = None
            await send(await kontingent.gespeichert_nachricht(kontingent_db, identity.user_id))
        elif dev:
            await dev.on_client_message(msg.type, data)

    @assistant_agg.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_agg, message):
        # Erst alles, was das Gespräch braucht; die Datenbank zuletzt. Ist sie langsam, darf das
        # weder den Verlauf noch die weiche Unterbrechung aufhalten.
        content = message.content
        if message.interrupted and (ungehoert := soft_gate.ungehoerte_woerter()):
            # Wörter, die der Kern schon gemeldet hat, die die Person aber nicht mehr hörte: aus dem Verlauf nehmen
            if kuerze_auf_gehoertes(context, ungehoert):
                content = ohne_letzte_woerter(content, ungehoert)  # Transkript und Protokoll wie der Verlauf
        transcript.append(("assistant", content))
        if message.interrupted:
            logger.info("WAMO wurde unterbrochen")
            logger.debug("Unterbrochen nach: {!r}", content[-80:])
            # Nur wenn WAMO schon etwas gesagt hat; sonst verwirrt die Notiz Claude.
            if content.strip():
                context.add_message({"role": "user", "content": INTERRUPTED_NOTE})
        soft_gate.assistant_turn_recorded()
        await memory.log_turn(role="assistant", content=content, interrupted=message.interrupted)

    @user_agg.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(_agg, _strategy, message):
        transcript.append(("user", message.content))
        turn.new_user_turn(message.content)
        new_user_turns = sum(1 for role, _ in transcript[learned_upto:] if role == "user")
        if new_user_turns >= LEARN_EVERY_USER_TURNS and not (learn_task and not learn_task.done()):
            start_learning(live=True)
        await memory.log_turn(role="user", content=message.content)

    async def announce(saved: list[Gemerkt]) -> None:
        for entry in saved:
            await send(gemerkt_message(entry.bereich, entry.status, entry.text, entry.id))

    async def learn(live: bool, previous: asyncio.Task | None):
        nonlocal learned_upto
        if previous and not previous.done():
            await previous  # nie zwei Auswertungen gleichzeitig
        end = len(transcript)
        start = max(0, learned_upto - LEARN_OVERLAP)
        if turn.section_tainted(start, end):
            # Abschnitt mit Webseiten-, Bildschirm-, PC- oder Auftragsinhalten: nicht daraus lernen
            logger.info("Lernen übersprungen: Abschnitt enthält Fremdinhalte")
            learned_upto = end
            return
        chunk = transcript[start:end]
        saved = await learn_from_session(memory, chunk, name=identity.name, zugang=zugang)  # Zugang des Profils
        learned_upto = end
        if not (saved and live and state["live"]):
            return
        await announce(saved)
        if any(s.status == "aktiv" for s in saved):
            await refresh_prompt()  # sofort anwenden: neuer System-Prompt mit allem, was WAMO jetzt weiß
        logger.info("Im Gespräch gelernt: {} Einträge", len(saved))

    def start_learning(live: bool):
        nonlocal learn_task
        if labor_sitzung:  # Labor: kein Lernen, kein Gedächtnis
            return
        learn_task = _keep(asyncio.create_task(learn(live, learn_task)))  # läuft nach dem Auflegen weiter

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        # Kurze Begrüßung wärmt die Sprachausgabe auf: der erste Soniox-Satz einer Verbindung
        # brauchte einmal 10 s. WAMO_GREETING leer setzen, um sie abzuschalten.
        greeting = greeting_text(identity.name)
        if greeting:
            await task.queue_frames([TTSSpeakFrame(greeting)])
        _keep(asyncio.create_task(cache_vorwaermen()))  # die erste Frage soll einen warmen Prompt-Cache finden
        # Sitzung erst nach der Begrüßung anlegen (der Insert kostete vor dem ersten Ton Zeit)
        await memory.start_session(channel=os.getenv("WAMO_CHANNEL") or "web")
        # Profil regelmäßig neu prüfen und Höchstdauer einhalten (M5)
        if state["guard"] is None and state["live"]:
            state["guard"] = _keep(asyncio.create_task(guard.run(end_session)))
        # Labor: alle 5 Sekunden den Durchlauf fragen, Ende bei Wechsel oder Schluss (labor_durchlauf.LaborWache)
        if labor_sitzung is not None and labor_sitzung.wache is not None and state["labor_wache"] is None and state["live"]:
            state["labor_wache"] = _keep(asyncio.create_task(labor_sitzung.wache.run(labor_ende)))

    async def labor_ende(grund: str) -> None:
        """Der Durchlauf ist beendet oder gewechselt: die Oberfläche lädt sich neu (Untertitel und Canvas liegen im Speicher der Seite)."""
        # Zuerst die Textspeicherung sperren: die Einwilligung gilt nicht mehr, und der Abbruch der Pipeline (task.cancel in cleanup) kann
        # still scheitern. Ohne diese Sperre schriebe der Speicher bis zum Ende der Pipeline weiter.
        labor_sitzung.memory.stoppen()
        if labor_werkz is not None:
            labor_werkz.durchlauf_beendet()  # offene Karten sind erledigt, keine neuen, kein Klick wirkt mehr (schon vor der Meldung und dem Aufräumen)
        with contextlib.suppress(Exception):
            await send({"type": "labor_durchlauf_ende"})
        await end_session(grund)

    async def end_session(reason: str) -> None:
        logger.warning("Sitzung beendet: {}", reason)
        meldung = {"type": "sitzung_beendet", "grund": reason}
        if guard.neu_anmelden:
            meldung["anmelden"] = True  # Anmeldung zu alt: die Oberfläche meldet ab
        with contextlib.suppress(Exception):
            await send(meldung)
            await asyncio.sleep(ENDE_MELDUNG_SECS)  # die Meldung soll die Oberfläche noch erreichen
        if dev:
            await dev.close()
        await cleanup(cancel_task=True)

    async def abgeloest(grund: str) -> None:
        """Eine neue Sitzung desselben Profils hat übernommen (sitzungen.py). Die PC-Sperre ist schon weg; hier nur
        melden. Getrennt wird von außen; endet die Verbindung nicht von selbst, räumt der Rückfall unten auf."""
        state["pc_steuerung"] = False
        if bild is not None:
            await bild.stoppen(grund, melden=False)
        logger.info("Sitzung abgelöst: {}", grund)
        with contextlib.suppress(Exception):
            await send({"type": "sitzung_beendet", "grund": grund})
        _keep(asyncio.create_task(ende_nach_abloesung()))

    async def ende_nach_abloesung() -> None:
        await asyncio.sleep(ENDE_MELDUNG_SECS * 4)
        if dev:
            await dev.close()
        await cleanup(cancel_task=True)  # idempotent: meist hat das Trennen schon aufgeräumt

    sitzung.abloesen = abgeloest

    async def cleanup(cancel_task: bool = False) -> None:
        """Einmal je Sitzung, egal ob Auflegen, Pipeline-Ende oder Fehler. Beim Auflegen erst die
        Pipeline stoppen (STT/LLM sollen nicht weiterlaufen), dann aufräumen, die Datenbank mit
        Zeitlimit. Ist die Pipeline schon zu Ende, wird sie nicht nochmal abgebrochen."""
        if state["cleaned"]:
            return
        state["cleaned"] = True
        if ausgaben.schwelle > 0:
            logger.info(ausgaben.abschlusszeile())  # eine Zeile, nur Zahlen
            ausgaben.leeren()
        state["live"] = False
        guard_task = state["guard"]
        if guard_task is not None and guard_task is not asyncio.current_task():
            guard_task.cancel()
        for name in ("pc_waechter", "sperr_probe", "geraet_wache", "labor_wache"):
            hintergrund = state[name]
            if hintergrund is not None and hintergrund is not asyncio.current_task():
                hintergrund.cancel()
        state["pc_steuerung"] = False
        sitzung.pc_lease_freigeben()
        if bild is not None:  # Trennen, Abmelden, Pipeline-Ende: das Live-Bild endet mit der Sitzung (Platz im Host frei)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(bild.stoppen("Die Sitzung ist beendet.", melden=False), CLEANUP_DB_TIMEOUT_SECS)
        if cancel_task:
            with contextlib.suppress(Exception):
                await task.cancel()
        if boss is not None:
            with contextlib.suppress(Exception):
                await boss.schliessen()  # laufende Aufgaben der Spezialisten abbrechen
        if dev:
            await dev.close()  # laufenden Entwicklungsauftrag beim Auflegen beenden
        with contextlib.suppress(Exception, asyncio.TimeoutError):
            await asyncio.wait_for(vorgaenge.schliessen(), CLEANUP_DB_TIMEOUT_SECS)  # offene Vorgänge gelten als abgebrochen
        if freigaben is not None:
            # offene PC-Freigaben ablehnen: keine Eingabesperre am PC und keine offene Zeile in actions zurücklassen
            with contextlib.suppress(Exception):
                await asyncio.wait_for(freigaben.schliessen(), CLEANUP_DB_TIMEOUT_SECS)
        if labor_werkz is not None:
            # offene Labor-Karten ablehnen (actions) und den Client des Dienstes schließen
            with contextlib.suppress(Exception):
                await asyncio.wait_for(labor_werkz.schliessen(), CLEANUP_DB_TIMEOUT_SECS)
        if pc_client:
            with contextlib.suppress(Exception):
                await pc_client.close()
        try:
            await asyncio.wait_for(memory.end_session(), CLEANUP_DB_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            logger.warning("Gedächtnis: Sitzungsende nicht rechtzeitig gespeichert")
        # Rest seit der letzten Auswertung im Hintergrund lernen; blockiert nichts.
        start_learning(live=False)
        _keep(asyncio.create_task(_zugang_nach_lernen(learn_task, zugang)))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        await cleanup(cancel_task=True)

    @task.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(_task, _frame):
        await cleanup()

    return Session(task=task, cleanup=cleanup)


async def run_bot(
    transport, runner_args: RunnerArguments, identity: Identity, sitzung: Sitzung | None = None
) -> None:
    session = await build_session(transport, identity, sitzung)
    if sitzung is not None and sitzung.abgeloest:
        # Während des Aufbaus hat eine neuere Sitzung desselben Profils übernommen: nicht erst starten
        logger.info("Sitzung beim Aufbau abgelöst, wird nicht gestartet")
        await session.cleanup()
        return
    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    try:
        await runner.run(session.task)
    finally:
        await session.cleanup()


async def _verbindung_beenden(connection, grund: str) -> None:
    """Der Person sagen, warum die Verbindung endet (Datenkanal, noch ohne Pipeline), dann trennen. Jeder Schritt für sich, damit ein Fehler im
    einen den anderen nicht verhindert: die WebRTC-Verbindung bleibt nie ohne Pipeline offen."""
    if connection is None:
        return
    with contextlib.suppress(Exception):
        connection.send_app_message({"label": "rtvi-ai", "type": "server-message", "data": {"type": "sitzung_beendet", "grund": grund}})
        await asyncio.sleep(ENDE_MELDUNG_SECS * 2)
    with contextlib.suppress(Exception):
        await connection.disconnect()


async def bot(runner_args: RunnerArguments):
    # Die Identität setzt allein die Zugangskontrolle (auth_gate.py). Ohne sie: sofort beenden.
    identity = identity_from_body(getattr(runner_args, "body", None))
    if identity is None:
        logger.error("Sitzung ohne geprüfte Identität: wird sofort beendet")
        connection = getattr(runner_args, "webrtc_connection", None)
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.disconnect()
        return
    logger.info("Sitzung für Profil {} ({})", identity.user_id, identity.rolle)
    connection = getattr(runner_args, "webrtc_connection", None)
    sitzung = Sitzung(identity.user_id, connection)
    try:
        await SITZUNGEN.uebernehmen(sitzung)  # eine laufende Sitzung desselben Profils wird abgelöst
        if sitzung.abgeloest:  # inzwischen selbst abgelöst (drei Verbindungen kurz nacheinander)
            return
        transport = await create_transport(runner_args, transport_params)
        await run_bot(transport, runner_args, identity, sitzung)
    except LLMNichtVerfuegbar as e:
        # LiteLLM nicht nutzbar und kein Rückfall erlaubt (WAMO_LLM_RUECKFALL): Sitzung sauber beenden,
        # statt die Verbindung ohne Pipeline offen zu lassen.
        logger.error("Sitzung beendet, kein Gesprächsmodell: {}", e)
        # Der Person klar sagen, warum die Verbindung endet; die Oberfläche zeigt die Meldung wie jedes "Sitzung beendet"
        await _verbindung_beenden(connection, kein_modell_text(e, identity.is_admin))
    except labor.LaborAbgelehnt as e:
        # Laborprofil: Durchlauf beendet, keine Einwilligung oder Dienst nicht erreichbar (labor_sitzung.py). Der Person sagen, warum.
        logger.warning("Laborsitzung nicht angenommen: {}", e.code)
        await _verbindung_beenden(connection, e.grund)
    except labor.LaborPolitikFehler as e:
        # Eine Laborsitzung verstößt gegen ihre Erlaubnisliste (build_session hat Dienstclient und Modellzugang schon geschlossen): nicht bauen
        logger.error("Laborsitzung nicht gebaut: {}", e)
        await _verbindung_beenden(connection, SITZUNG_FEHLER_TEXT)
    except Exception:
        # Jede andere Ausnahme beim Aufbau oder im Lauf: die Verbindung nicht ohne Pipeline offen lassen. Der Person nur ein fester Satz, das Log
        # bekommt die Ausnahme mit Ablauf (Abbrüche der Aufgabe, CancelledError, fängt das nicht: sie sind keine Exception)
        logger.exception("Sitzung beendet, unerwarteter Fehler")
        await _verbindung_beenden(connection, SITZUNG_FEHLER_TEXT)
    finally:
        SITZUNGEN.freigeben(sitzung)


if __name__ == "__main__":
    from pipecat.runner import run as pipecat_runner
    from pipecat.runner.run import app, main

    # Nur die eigene Oberfläche darf Sitzungen starten. Ohne Liste erlaubt der Runner jede
    # Herkunft (CORS *): eine fremde Webseite im Browser könnte über localhost:7860 eine Sitzung
    # aufbauen. Ein leerer Wert öffnet das nicht.
    if not os.getenv("PIPECAT_ALLOWED_ORIGINS", "").strip():
        os.environ["PIPECAT_ALLOWED_ORIGINS"] = DEFAULT_ALLOWED_ORIGINS
    # Und auch von dort nur angemeldete Profile mit zweitem Faktor (auth_gate.py).
    gate = install_access_gate(app)
    # Eigene Schnittstellen unter /wamo/api (Gerätebindung, Hinweise, geteilte Arbeit), hinter derselben Prüfung
    import wamo_api

    wamo_api.installieren(app, gate)
    # Log-Stufe nach dem Runner wiederherstellen (er setzt DEBUG) und auf Sokrates-2 nur über den
    # Unix-Socket WAMO_CORE_UDS lauschen (kern_dienst.py).
    runner_vorbereiten(pipecat_runner, _configure_logging)
    main()
