"""WAMO Voice-Core.

Pipeline:  Mikrofon (WebRTC, Browser-AEC) -> Silero-VAD -> Soniox STT (Streaming, DE)
           -> Turn-Erkennung -> Claude (Streaming) -> Soniox TTS (Streaming) -> Lautsprecher

Anmeldung: auth_gate.py prüft Token, zweiten Faktor und aktives Profil und schreibt die geprüfte
Identität in runner_args.body["wamo"]. Fehlt sie, endet die Sitzung sofort; einen Weg ohne
Anmeldung gibt es nicht. PC-Werkzeuge und Entwicklungsaufträge bekommt nur die Rolle admin.

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
  {"type": "gemerkt", "bereich", "status", "text", "id"}           nach jedem gespeicherten Gedächtniseintrag
Nachrichten von der Oberfläche:
  {"type": "vergessen", "data": {"id"}}   eigenen Eintrag deaktivieren (memory.forget)
  {"type": "freigabe", "data": {...}}     Freigabe für Entwicklungsaufträge (nur admin)
Live-Untertitel: Pipecat sendet bot-output (mit Wortfortschritt aus den Soniox-Zeitstempeln) und
user-transcription von selbst; RTVIObserverParams bleiben dafür auf dem Standard (eingeschaltet).

Start lokal:  uv run bot.py   -> http://localhost:7860
"""

from __future__ import annotations

import asyncio
import contextlib
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
from pipecat.frames.frames import LLMUpdateSettingsFrame, TTSSpeakFrame
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
from pipecat.services.anthropic.llm import AnthropicLLMService
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

from activity import ActivityReporter
from audio_monitor import AudioInputMonitor, SpeakerTracker
from auth_gate import Identity, identity_from_body, install_access_gate
from dev_agent import DevAgent, entwicklung_enabled, entwicklung_problem
from learning import Gemerkt, learn_from_session
from memory import MemoryStore, Profile, ProfileDirectory, Recall, shared_database
from pc_host import call_host_tool, host_client, host_tools, pc_host_enabled
from persona import build_system_prompt
from screen import PC_OFF, register_screen_tools
from soft_interrupt import (
    MeaningfulWordsUserTurnStartStrategy,
    PlaybackTracker,
    SoftInterruptGate,
)
from soniox_tts import LazySonioxTTSService
from tools import TurnState, anthropic_client, gemerkt_message, register_tools

load_dotenv(override=True)


def _configure_logging() -> None:
    """Standard INFO (WAMO_LOG_LEVEL): INFO enthält keine Gesprächsinhalte, die stehen nur in DEBUG."""
    level = (os.getenv("WAMO_LOG_LEVEL") or "INFO").strip().upper()
    logger.remove()
    try:
        logger.add(sys.stderr, level=level)
    except ValueError:
        logger.add(sys.stderr, level="INFO")
        logger.warning("WAMO_LOG_LEVEL {!r} unbekannt, nutze INFO", level)


_configure_logging()

if sys.platform == "win32":
    # Höhere Priorität: wenn WAMO Programme öffnet, belegen die kurz die CPU; das Audio soll
    # dann nicht stocken.
    import ctypes

    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x8000)

# Standard: EU-Endpunkte (Datenschutz); .env kann umstellen
SONIOX_STT_URL = os.getenv("SONIOX_STT_URL") or "wss://stt-rt.eu.soniox.com/transcribe-websocket"
SONIOX_TTS_URL = os.getenv("SONIOX_TTS_URL") or "wss://tts-rt.soniox.com/tts-websocket"
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
# Hält Claude einen Satz für unvollständig, wartet WAMO so lange auf den Rest (Sekunden).
SMART_TURN_STOP_SECS = float(os.getenv("SMART_TURN_STOP_SECS") or 1.0)
TURN_INCOMPLETE_SHORT_SECS = float(os.getenv("TURN_INCOMPLETE_SHORT_SECS") or 1.5)
TURN_INCOMPLETE_LONG_SECS = float(os.getenv("TURN_INCOMPLETE_LONG_SECS") or 3.0)  # 8 s wirkten wie ein Hänger
# Weiche Unterbrechung: nach einem Einwurf spricht WAMO den Satz zu Ende, höchstens aber das
# laufende Wort plus so viele weitere Wörter (nie mitten im Wort).
SOFT_INTERRUPT_MAX_WORDS = int(os.getenv("SOFT_INTERRUPT_MAX_WORDS") or 3)
# So weit laufen die Wort-Zeitstempel dem hörbaren Ton voraus (gemessen: etwa 0,5 s).
SOFT_INTERRUPT_LAG_SECS = float(os.getenv("SOFT_INTERRUPT_LAG_SECS") or 0.5)
DEFAULT_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
BETRIEB_MODUS = "lokal"  # Betriebsart in der Oberfläche: lokal | buero
MODELL_ORT = "cloud"  # Claude und Soniox laufen in der Cloud; eigene Modelle auf den Sparks wären "haus"
CLEANUP_DB_TIMEOUT_SECS = 5.0
PROFILE_RECHECK_SECS = 60.0  # so oft prüft eine laufende Sitzung, ob das Profil noch gilt
MAX_SITZUNG_MIN = float(os.getenv("WAMO_MAX_SITZUNG_MIN") or 120)

INTERRUPTED_NOTE = (
    "[Hinweis für WAMO: Du wurdest gerade unterbrochen. Deine letzte Antwort endet dort, wo du "
    "aufgehört hast. Reagiere natürlich auf den Einwurf und knüpfe dann, falls noch relevant, an "
    "deinem letzten Gedanken an, statt von vorn zu beginnen.]"
)

# Referenzen halten, sonst räumt der Garbage Collector laufende Hintergrund-Tasks weg.
# (Nur Task-Referenzen, kein Cache: Profil und Gedächtnis gehören zur Sitzung.)
_background_tasks: set[asyncio.Task] = set()

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


def lesbarer_modellname(model: str) -> str:
    """'claude-haiku-5-5' -> 'Claude Haiku 5.5'; Unbekanntes bleibt, wie es ist."""
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?", model)
    if not m:
        return model
    family, major, minor = m.groups()
    return f"Claude {family.capitalize()} {major}.{minor}" if minor else f"Claude {family.capitalize()} {major}"


def greeting_text(name: str) -> str:
    template = os.getenv("WAMO_GREETING", DEFAULT_GREETING)
    return template.replace("{name}", name).strip()


def profil_message(identity: Identity) -> dict:
    return {"type": "profil", "name": identity.name, "rolle": identity.rolle}


def betrieb_message(
    pc: str, pc_latenz_ms: int | None = None, pc_steuerung: bool = False, entwicklung: bool = False
) -> dict:
    """pc: verbunden | getrennt | aus (gesperrt erkennt der Host noch nicht).
    pc_steuerung: ob die PC-Werkzeuge in dieser Sitzung eingeschaltet sind (Standard aus).
    entwicklung: ob Entwicklungsaufträge in dieser Sitzung angeboten werden (WAMO_ENTWICKLUNG und Rolle)."""
    message = {
        "type": "betrieb",
        "modus": BETRIEB_MODUS,
        "ort": MODELL_ORT,
        "modelle": {"llm": lesbarer_modellname(CLAUDE_MODEL), "stt": "Soniox", "tts": f"Soniox {SONIOX_VOICE}"},
        "pc": pc,
        "pc_steuerung": "an" if pc_steuerung else "aus",
        "entwicklung": "an" if entwicklung else "aus",
    }
    if pc_latenz_ms is not None:
        message["pc_latenz_ms"] = pc_latenz_ms
    return message


def gate_pc_tools(
    schemas: list[FunctionSchema], allowed: Callable[[], bool], on_foreign_content: Callable[[], None]
) -> list[FunctionSchema]:
    """Hüllt die Handler der Host-Werkzeuge: bei ausgeschalteter PC-Steuerung eine klare Absage,
    sonst Aufruf des Originals. Ergebnisse vom PC (Fenstertitel, Ausgaben) gelten als Fremdinhalt."""
    gated = []
    for schema in schemas:
        original = schema.handler

        async def handler(params, _original=original):
            if not allowed():
                await params.result_callback(PC_OFF)
                return
            on_foreign_content()
            await _original(params)

        if original is not None:
            handler.__dict__.update(getattr(original, "__dict__", {}))  # @tool_options übernehmen
        gated.append(FunctionSchema(schema.name, schema.description, schema.properties, schema.required,
                                    handler=handler if original is not None else None))
    return gated


class SessionGuard:
    """Prüft eine laufende Sitzung regelmäßig (M5): Profil noch aktiv, Rolle unverändert, maximale
    Dauer nicht überschritten. Datenbankfehler werden einige Male toleriert, dann endet die Sitzung."""

    def __init__(
        self,
        identity: Identity,
        lookup,
        *,
        interval: float = PROFILE_RECHECK_SECS,
        max_secs: float = MAX_SITZUNG_MIN * 60,
        max_failures: int = 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.identity = identity
        self.lookup = lookup
        self.interval = interval
        self.max_secs = max_secs
        self.max_failures = max_failures
        self._clock = clock
        self._sleep = sleep
        self._started = clock()
        self._failures = 0

    async def check(self) -> str | None:
        """Grund für das Ende der Sitzung oder None."""
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
        if profile is None or not profile.aktiv:
            return "Das WAMO-Profil ist nicht mehr aktiv."
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


def pause_prompts(name: str) -> tuple[str, str]:
    """Texte, mit denen Pipecat Claude nach einer Pause erneut fragt. Die englischen Standardtexte
    führten dazu, dass Claude nur den Marker "●" ohne Text schickte: WAMO blieb stumm."""
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


async def build_session(transport, identity: Identity) -> Session:
    """Baut Dienste, Pipeline und Ereignisse einer Sitzung, ohne Netzverbindungen aufzubauen."""
    memory = MemoryStore.from_env(identity.user_id)
    is_admin = identity.is_admin
    pc_enabled = is_admin and pc_host_enabled()
    entwicklung = is_admin and entwicklung_enabled()
    if entwicklung and (problem := entwicklung_problem()):
        logger.error("Entwicklungsaufträge aus: {}", problem)
        entwicklung = False
    # PC-Steuerung je Sitzung standardmäßig aus; nur ein Admin schaltet sie in der UI ein.
    state: dict = {"pc": "getrennt" if pc_enabled else "aus", "pc_latenz_ms": None, "pc_steuerung": False,
                   "client_ready": False, "live": True, "cleaned": False, "guard": None}

    def prompt_for(recall: Recall) -> str:
        return build_system_prompt(
            identity.name, identity.anrede, recall.persoenlich, recall.gemeinsam,
            pc_werkzeuge=pc_enabled, entwicklung=entwicklung,
        )

    # Gedächtnis je Sitzung laden (kein prozessweiter Cache: jedes Profil sieht nur seins).
    recall = await memory.recall()

    stt = SonioxSTTService(
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

    tts = LazySonioxTTSService(
        api_key=os.environ["SONIOX_API_KEY"],
        url=SONIOX_TTS_URL,
        settings=SonioxTTSService.Settings(voice=SONIOX_VOICE, language=Language.DE),
    )

    llm = AnthropicLLMService(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        client=anthropic_client(),  # gemeinsamer Client, inklusive Workspace-Header (ANTHROPIC_WORKSPACE_ID)
        settings=AnthropicLLMService.Settings(
            model=CLAUDE_MODEL,
            # Persona als system_instruction, nicht als System-Nachricht im Kontext: die
            # Satzende-Erkennung hängt ihre Regeln hier an. Eine System-Nachricht im Kontext
            # würde Pipecat dann komplett verwerfen.
            system_instruction=prompt_for(recall),
            enable_prompt_caching=True,
            # Haiku denkt sonst vor jeder Antwort nach (bis 1,6 s bis zum ersten Wort).
            thinking=AnthropicLLMService.ThinkingConfig(type="disabled"),
        ),
    )

    transcript: list[tuple[str, str]] = []  # fürs Lernen
    # Fremdinhalte im laufenden Zug (Websuche, Bildschirm, PC, Entwicklungs-Ergebnisse) merken: kein
    # sofort wirksames gemeinsames Wissen (M4) und kein Lernen aus diesen Abschnitten.
    turn = TurnState(transcript_len=lambda: len(transcript))
    standard_tools = list(register_tools(llm, memory, identity, turn).standard_tools)
    dev: DevAgent | None = None
    if entwicklung:
        # Entwicklungsaufträge an Claude Code; Start und Freigaben per Klick in der UI (dev_agent.py)
        dev = DevAgent(memory, auftraggeber=identity.name)
        dev.on_result = turn.taint
        dev.register(llm)
        standard_tools += dev.schemas()
    context = LLMContext(tools=ToolsSchema(standard_tools=standard_tools))

    # PC-Werkzeuge (Host per MCP-stdio) nur für admin; im Hintergrund dazuholen.
    pc_client = host_client() if pc_enabled else None

    def pc_allowed() -> bool:
        return bool(state["pc_steuerung"])

    # Bildschirm sehen/klicken: Foto vom Host, Bildmodell entscheidet (siehe screen.py)
    screen_schemas = register_screen_tools(llm, lambda: pc_client, pc_allowed, turn.taint) if pc_client else []

    learned_upto = 0  # bis zu diesem Index in transcript ist ausgewertet
    learn_task: asyncio.Task | None = None
    pause_short, pause_long = pause_prompts(identity.name)

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
                ),
            ),
        ),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=UserTurnStrategies(
                # Unterbrechen entscheidet das SoftInterruptGate, nicht die Strategie.
                start=[
                    MeaningfulWordsUserTurnStartStrategy(
                        min_words=MIN_INTERRUPT_WORDS, enable_interruptions=False
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

    soft_gate = SoftInterruptGate(
        max_words=SOFT_INTERRUPT_MAX_WORDS,
        playback_lag_secs=SOFT_INTERRUPT_LAG_SECS,
    )

    pipeline = Pipeline(
        [
            transport.input(),
            AudioInputMonitor(),  # Diagnose: Lücken und Pegel des Browser-Mikrofons
            stt,
            SpeakerTracker(),  # wer spricht -> Farbe im Stimmen-Rand
            user_agg,
            soft_gate,
            llm,
            ActivityReporter(),  # Werkzeugaufrufe live an die Oberfläche (Zeitleiste)
            tts,
            transport.output(),
            PlaybackTracker(soft_gate),
            assistant_agg,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
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

    async def send_betrieb() -> None:
        await send(betrieb_message(state["pc"], state["pc_latenz_ms"], state["pc_steuerung"], entwicklung))

    async def refresh_prompt() -> None:
        await task.queue_frame(
            LLMUpdateSettingsFrame(delta=LLMSettings(system_instruction=prompt_for(await memory.recall())))
        )

    async def attach_pc_tools() -> None:
        pc = await host_tools(pc_client)
        if pc:
            pc_schemas = gate_pc_tools(list(pc.standard_tools), pc_allowed, turn.taint)
            context.set_tools(ToolsSchema(standard_tools=[*standard_tools, *pc_schemas, *screen_schemas]))
            started = time.monotonic()
            with contextlib.suppress(Exception):  # Latenz ist nur eine Anzeige
                await asyncio.wait_for(call_host_tool(pc_client, "system_info", {}), 5)
                state["pc_latenz_ms"] = round((time.monotonic() - started) * 1000)
        state["pc"] = "verbunden" if pc else "getrennt"
        if state["client_ready"] and state["live"]:
            await send_betrieb()

    if pc_client:
        _keep(asyncio.create_task(attach_pc_tools()))

    @task.rtvi.event_handler("on_client_ready")
    async def on_client_ready(_rtvi):
        state["client_ready"] = True
        await send(profil_message(identity))
        await send_betrieb()

    @task.rtvi.event_handler("on_client_message")
    async def on_client_message(_rtvi, msg):
        # Klicks aus der UI. Kommt nur über die eigene, angemeldete WebRTC-Verbindung.
        data = msg.data if isinstance(msg.data, dict) else {}
        if msg.type == "vergessen":
            memory_id = str(data.get("id", ""))
            ok = await memory.forget(memory_id)
            logger.info("vergessen {}: {}", memory_id, "ok" if ok else "abgelehnt")
            await send({"type": "vergessen", "id": memory_id, "ok": ok})
            if ok:
                await refresh_prompt()  # WAMO soll den Eintrag sofort nicht mehr nutzen
        elif msg.type == "pc_steuerung":
            # Nur ein Admin mit eingerichtetem PC-Host darf einschalten; ausschalten geht immer.
            state["pc_steuerung"] = bool(data.get("an") is True and pc_enabled)
            logger.info("PC-Steuerung {}", "an" if state["pc_steuerung"] else "aus")
            await send_betrieb()
        elif dev:
            await dev.on_client_message(msg.type, data)

    @assistant_agg.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_agg, message):
        # Erst alles, was das Gespräch braucht; die Datenbank zuletzt. Ist sie langsam, darf das
        # weder den Verlauf noch die weiche Unterbrechung aufhalten.
        transcript.append(("assistant", message.content))
        if message.interrupted:
            logger.info("WAMO wurde unterbrochen")
            logger.debug("Unterbrochen nach: {!r}", message.content[-80:])
            # Nur wenn WAMO schon etwas gesagt hat; sonst verwirrt die Notiz Claude.
            if message.content.strip():
                context.add_message({"role": "user", "content": INTERRUPTED_NOTE})
        soft_gate.assistant_turn_recorded()
        await memory.log_turn(role="assistant", content=message.content, interrupted=message.interrupted)

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
        saved = await learn_from_session(memory, chunk, CLAUDE_MODEL, name=identity.name)
        learned_upto = end
        if not (saved and live and state["live"]):
            return
        await announce(saved)
        if any(s.status == "aktiv" for s in saved):
            await refresh_prompt()  # sofort anwenden: neuer System-Prompt mit allem, was WAMO jetzt weiß
        logger.info("Im Gespräch gelernt: {} Einträge", len(saved))

    def start_learning(live: bool):
        nonlocal learn_task
        learn_task = _keep(asyncio.create_task(learn(live, learn_task)))  # läuft nach dem Auflegen weiter

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        # Kurze Begrüßung wärmt die Sprachausgabe auf: der erste Soniox-Satz einer Verbindung
        # brauchte einmal 10 s. WAMO_GREETING leer setzen, um sie abzuschalten.
        greeting = greeting_text(identity.name)
        if greeting:
            await task.queue_frames([TTSSpeakFrame(greeting)])
        # Sitzung erst nach der Begrüßung anlegen (der Insert kostete vor dem ersten Ton Zeit)
        await memory.start_session(channel=os.getenv("WAMO_CHANNEL") or "web")
        # Profil regelmäßig neu prüfen und Höchstdauer einhalten (M5)
        if state["guard"] is None and state["live"]:
            state["guard"] = _keep(asyncio.create_task(guard.run(end_session)))

    async def end_session(reason: str) -> None:
        logger.warning("Sitzung beendet: {}", reason)
        with contextlib.suppress(Exception):
            await send({"type": "sitzung_beendet", "grund": reason})
        if dev:
            await dev.close()
        await cleanup(cancel_task=True)

    async def cleanup(cancel_task: bool = False) -> None:
        """Einmal je Sitzung, egal ob Auflegen, Pipeline-Ende oder Fehler. Beim Auflegen erst die
        Pipeline stoppen (STT/LLM sollen nicht weiterlaufen), dann aufräumen, die Datenbank mit
        Zeitlimit. Ist die Pipeline schon zu Ende, wird sie nicht nochmal abgebrochen."""
        if state["cleaned"]:
            return
        state["cleaned"] = True
        state["live"] = False
        guard_task = state["guard"]
        if guard_task is not None and guard_task is not asyncio.current_task():
            guard_task.cancel()
        if cancel_task:
            with contextlib.suppress(Exception):
                await task.cancel()
        if dev:
            await dev.close()  # laufenden Entwicklungsauftrag beim Auflegen beenden
        if pc_client:
            with contextlib.suppress(Exception):
                await pc_client.close()
        try:
            await asyncio.wait_for(memory.end_session(), CLEANUP_DB_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            logger.warning("Gedächtnis: Sitzungsende nicht rechtzeitig gespeichert")
        # Rest seit der letzten Auswertung im Hintergrund lernen; blockiert nichts.
        start_learning(live=False)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        await cleanup(cancel_task=True)

    @task.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(_task, _frame):
        await cleanup()

    return Session(task=task, cleanup=cleanup)


async def run_bot(transport, runner_args: RunnerArguments, identity: Identity) -> None:
    session = await build_session(transport, identity)
    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    try:
        await runner.run(session.task)
    finally:
        await session.cleanup()


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
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args, identity)


if __name__ == "__main__":
    from pipecat.runner.run import app, main

    # Nur die eigene Oberfläche darf Sitzungen starten. Ohne Liste erlaubt der Runner jede
    # Herkunft (CORS *): eine fremde Webseite im Browser könnte über localhost:7860 eine Sitzung
    # aufbauen. Ein leerer Wert öffnet das nicht.
    if not os.getenv("PIPECAT_ALLOWED_ORIGINS", "").strip():
        os.environ["PIPECAT_ALLOWED_ORIGINS"] = DEFAULT_ALLOWED_ORIGINS
    # Und auch von dort nur angemeldete Profile mit zweitem Faktor (auth_gate.py).
    install_access_gate(app)
    main()
