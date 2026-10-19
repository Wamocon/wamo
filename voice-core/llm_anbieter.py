"""Anbieter-Fabrik für die Sprachmodelle von WAMO (Ausbaustufe P4).

Jeder Baustein lässt sich einzeln zwischen Claude und den eigenen Modellen auf den Sparks umschalten.
Die eigenen Modelle laufen ausschließlich über das LiteLLM-Gateway (OpenAI-kompatibel), nie direkt
über die Adressen der Knoten.

  Baustein          Anbieter (claude | litellm)   Modell
  gespraech         WAMO_LLM_ANBIETER             WAMO_LLM_MODELL (Claude: CLAUDE_MODEL_FAST)
  lernen            WAMO_LLM_LERNEN               WAMO_LLM_LERNEN_MODELL (Claude: CLAUDE_MODEL_FAST)
  vision            WAMO_LLM_VISION               WAMO_LLM_VISION_MODELL (Claude: VISION_MODEL)
  zusammenfassung   WAMO_LLM_ZUSAMMENFASSUNG      WAMO_LLM_ZUSAMMENFASSUNG_MODELL

Standard ist überall claude. Ist WAMO_LLM_ZUSAMMENFASSUNG leer, fasst Pipecat mit dem Gesprächsmodell
zusammen. Steht lernen oder zusammenfassung auf litellm ohne eigenes Modell, gilt das LiteLLM-Modell des
Gesprächs. Das Gateway kommt aus LITELLM_BASE_URL und LITELLM_API_KEY; zusätzliche Anfrage-Parameter
(etwa zum Abschalten einer Denkphase) aus WAMO_LLM_EXTRA als JSON-Objekt.

Rückfall: Fehlt Adresse, Schlüssel oder Modell, ist das Gateway nicht erreichbar oder bietet es das
Modell nicht an, schreibt die Fabrik einen Fehler ins Log und nimmt Claude. Die Oberfläche zeigt das
am Modellnamen des Gesprächs ("... (Rückfall)"). WAMO_LLM_RUECKFALL steuert das je Baustein:
leer oder claude (alle), aus (keiner) oder eine Liste wie "gespraech,zusammenfassung". Ohne Rückfall
bricht der Baustein mit LLMNichtVerfuegbar ab; dann geht für ihn nichts an Claude.

Grenze: Geprüft wird beim Start einer Sitzung (und bei Lernen und Vision vor jedem Aufruf). Fällt das
Gateway mitten im Gespräch aus, wechselt das laufende Gespräch nicht zu Claude; Pipecat meldet den
Fehler, die nächste Sitzung fällt zurück. Kurze Zeitgrenzen sorgen dafür, dass WAMO dann nicht hängt.

Für Lernen und Vision gibt es einen schlanken gemeinsamen Aufruf (anfrage). Das Gespräch und die
Zusammenfassung bekommen einen Pipecat-Dienst (gespraech_llm, zusammenfassung_llm).
"""

from __future__ import annotations

import fnmatch
import functools
import inspect
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import anthropic
import httpx
import openai
from loguru import logger
from openai.resources.chat.completions import AsyncCompletions

from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.llm_service import LLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.utils.http import connection_limits

Anbieter = Literal["claude", "litellm"]
Ende = Literal["fertig", "abgelehnt", "zu_lang"]
ANBIETER: tuple[Anbieter, ...] = ("claude", "litellm")
_ENV = {
    "gespraech": ("WAMO_LLM_ANBIETER", "WAMO_LLM_MODELL"),
    "lernen": ("WAMO_LLM_LERNEN", "WAMO_LLM_LERNEN_MODELL"),
    "vision": ("WAMO_LLM_VISION", "WAMO_LLM_VISION_MODELL"),
    "zusammenfassung": ("WAMO_LLM_ZUSAMMENFASSUNG", "WAMO_LLM_ZUSAMMENFASSUNG_MODELL"),
}
BAUSTEINE = tuple(_ENV)
STANDARD_CLAUDE_SCHNELL = "claude-haiku-5-5"
STANDARD_CLAUDE_VISION = "claude-sonnet-5-5"
PRUEF_TTL_SECS = 30.0  # so lange gilt das Ergebnis einer Gateway-Prüfung
PRUEF_TIMEOUT_SECS = 2.0  # Prüfung vor dem Sitzungsstart darf das Gespräch kaum verzögern
# Zeitgrenze je Aufruf. Vision: zwei Versuche (LiteLLM, dann Claude) müssen in die 30 s des Werkzeugs passen.
ANFRAGE_TIMEOUT_SECS = {"lernen": 45.0, "vision": 12.0, "zusammenfassung": 60.0, "gespraech": 20.0}
VERBINDUNG_TIMEOUT_SECS = 3.0
GESPRAECH_LESE_TIMEOUT_SECS = 20.0  # längste Pause im Stream, bevor das Gespräch die Anfrage aufgibt
_CLAUDE_MODELL = re.compile(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?")
_ALLE = "*"  # Cache-Schlüssel für "Gateway ganz weg", gilt für jedes Modell


class LLMFehler(Exception):
    """Ein Modellaufruf ist fehlgeschlagen. Der Text enthält nie Schlüssel oder Inhalte."""

    def __init__(self, text: str, *, verbindung: bool = False):
        super().__init__(text)
        self.verbindung = verbindung  # Netz oder Gateway weg: Rückfall sinnvoll


class LLMNichtVerfuegbar(LLMFehler):
    """LiteLLM ist für einen Baustein nicht nutzbar und der Rückfall auf Claude ist aus."""


@dataclass(frozen=True)
class Wahl:
    """Welcher Anbieter und welches Modell einen Baustein tatsächlich bedient."""

    baustein: str
    anbieter: Anbieter
    modell: str
    gewuenscht: Anbieter  # konfigurierter Anbieter
    grund: str | None = None  # warum es nicht der gewünschte Anbieter ist

    @property
    def rueckfall(self) -> bool:
        return self.anbieter != self.gewuenscht

    @property
    def anzeige(self) -> str:
        """Name für die Oberfläche, z. B. 'Claude Haiku 5.5' oder 'qwen3.6:35b (LiteLLM)'."""
        name = lesbarer_modellname(self.modell)
        if self.anbieter == "litellm":
            return f"{name} (LiteLLM)"
        return f"{name} (Rückfall)" if self.rueckfall else name


@dataclass(frozen=True)
class Bild:
    """Ein JPEG-Bild als Base64 (Bildschirmfoto vom Host)."""

    jpeg_base64: str


@dataclass(frozen=True)
class Antwort:
    text: str
    ende: Ende
    wahl: Wahl


def lesbarer_modellname(model: str) -> str:
    """'claude-haiku-5-5' -> 'Claude Haiku 5.5'; andere Namen bleiben, wie sie sind."""
    m = _CLAUDE_MODELL.fullmatch(model or "")
    if not m:
        return model
    family, major, minor = m.groups()
    return f"Claude {family.capitalize()} {major}.{minor}" if minor else f"Claude {family.capitalize()} {major}"


# --- Konfiguration -----------------------------------------------------------------------------


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def claude_standardmodell(baustein: str) -> str:
    if baustein == "vision":
        return _env("VISION_MODEL") or STANDARD_CLAUDE_VISION
    return _env("CLAUDE_MODEL_FAST") or STANDARD_CLAUDE_SCHNELL


def rueckfall_erlaubt(baustein: str) -> bool:
    """WAMO_LLM_RUECKFALL: leer oder claude (alle Bausteine), aus (keiner) oder eine Liste von Bausteinen."""
    wert = _env("WAMO_LLM_RUECKFALL").lower()
    if wert in ("", "claude", "an", "ja"):
        return True
    if wert in ("aus", "off", "0", "false", "nein"):
        return False
    return baustein in {teil.strip() for teil in wert.split(",")}


def konfiguration(baustein: str) -> tuple[Anbieter, str | None]:
    """(Anbieter, Modell) aus der Umgebung. Ein unbekannter Anbieter gilt als claude (mit Fehlermeldung)."""
    if baustein not in _ENV:
        raise ValueError(f"unbekannter Baustein {baustein!r}")
    anbieter_env, modell_env = _ENV[baustein]
    wert = _env(anbieter_env).lower() or "claude"
    if wert not in ANBIETER:
        logger.error("{}={!r} ist unbekannt (erlaubt: claude, litellm), nutze claude", anbieter_env, wert)
        wert = "claude"
    anbieter: Anbieter = "litellm" if wert == "litellm" else "claude"
    modell = _env(modell_env) or None
    if (anbieter == "litellm" and not modell and baustein in ("lernen", "zusammenfassung")
            and _env("WAMO_LLM_ANBIETER").lower() == "litellm"):
        modell = _env("WAMO_LLM_MODELL") or None  # dasselbe Modell wie das Gespräch
    return anbieter, modell


def litellm_basis() -> str:
    return _env("LITELLM_BASE_URL").rstrip("/")


_ERLAUBTE_PARAMETER = frozenset(inspect.signature(AsyncCompletions.create).parameters) - {
    "self", "messages", "model", "stream", "stream_options", "tools", "tool_choice", "timeout",
}


def litellm_extra() -> dict[str, Any]:
    """Zusätzliche Parameter für jede LiteLLM-Anfrage aus WAMO_LLM_EXTRA (JSON-Objekt), z. B.
    {"reasoning_effort": "none"} oder {"extra_body": {"chat_template_kwargs": {"enable_thinking": false}}}.
    Was das OpenAI-SDK nicht als Parameter kennt, wandert in extra_body, statt die Anfrage abzubrechen."""
    raw = _env("WAMO_LLM_EXTRA")
    if not raw:
        return {}
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("WAMO_LLM_EXTRA ist kein gültiges JSON, wird ignoriert")
        return {}
    if not isinstance(extra, dict):
        logger.error("WAMO_LLM_EXTRA muss ein JSON-Objekt sein, wird ignoriert")
        return {}
    ergebnis = {k: v for k, v in extra.items() if k in _ERLAUBTE_PARAMETER and k != "extra_body"}
    body = dict(extra["extra_body"]) if isinstance(extra.get("extra_body"), dict) else {}
    body.update({k: v for k, v in extra.items() if k not in _ERLAUBTE_PARAMETER})
    if body:
        ergebnis["extra_body"] = body
    return ergebnis


def litellm_problem_statisch(modell: str | None) -> str | None:
    """Was ohne Netz schon feststeht: fehlende Adresse, fehlender Schlüssel, fehlendes Modell."""
    basis = litellm_basis()
    if not basis:
        return "LITELLM_BASE_URL fehlt"
    if not basis.startswith(("http://", "https://")):
        return "LITELLM_BASE_URL muss mit http:// oder https:// beginnen"
    if not _env("LITELLM_API_KEY"):
        return "LITELLM_API_KEY fehlt"
    if not modell:
        return "kein Modell gesetzt"
    return None


# --- Gateway-Prüfung ---------------------------------------------------------------------------

_pruef_cache: dict[tuple[str, str], tuple[float, str | None]] = {}


def _cache_leeren() -> None:
    _pruef_cache.clear()


async def litellm_modelle(timeout: float = PRUEF_TIMEOUT_SECS, *, client: httpx.AsyncClient | None = None) -> list[str]:
    """Modellnamen, die das Gateway anbietet (GET /models). Wirft LLMFehler."""
    url = f"{litellm_basis()}/models"
    kopf = {"Authorization": f"Bearer {_env('LITELLM_API_KEY')}"}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as eigener:
                r = await eigener.get(url, headers=kopf)
        else:
            r = await client.get(url, headers=kopf, timeout=timeout)
    except httpx.HTTPError as e:
        raise LLMFehler(f"Gateway nicht erreichbar ({type(e).__name__})", verbindung=True) from None
    if r.status_code in (401, 403):
        raise LLMFehler(f"Gateway lehnt den Schlüssel ab (HTTP {r.status_code})")
    if r.status_code != 200:
        raise LLMFehler(f"Gateway antwortet mit HTTP {r.status_code}", verbindung=r.status_code >= 500)
    try:
        daten = r.json().get("data", [])
        return sorted(str(m["id"]) for m in daten if isinstance(m, dict) and m.get("id"))
    except (ValueError, AttributeError, TypeError) as e:
        raise LLMFehler(f"Modellliste des Gateways nicht lesbar ({type(e).__name__})") from None


def _angeboten(modell: str, modelle: list[str]) -> bool:
    """Auch Platzhalter-Routen des Gateways (z. B. "ollama/*") zählen."""
    return any(modell == m or ("*" in m and fnmatch.fnmatchcase(modell, m)) for m in modelle)


def _gecacht(basis: str, modell: str, jetzt: float) -> tuple[bool, str | None]:
    for schluessel in ((basis, _ALLE), (basis, modell)):
        if (eintrag := _pruef_cache.get(schluessel)) and jetzt - eintrag[0] < PRUEF_TTL_SECS:
            return True, eintrag[1]
    return False, None


async def litellm_problem(modell: str | None) -> str | None:
    """Grund, warum LiteLLM mit diesem Modell gerade nicht nutzbar ist, oder None."""
    if problem := litellm_problem_statisch(modell):
        return problem
    if modell is None:  # durch litellm_problem_statisch ausgeschlossen, hier nur für die Typprüfung
        return "kein Modell gesetzt"
    basis, jetzt = litellm_basis(), time.monotonic()
    treffer, problem = _gecacht(basis, modell, jetzt)
    if treffer:
        return problem
    try:
        modelle = await litellm_modelle()
        problem = None if _angeboten(modell, modelle) else f"das Gateway bietet das Modell {modell!r} nicht an"
    except LLMFehler as e:
        problem = str(e)
        if e.verbindung:  # Gateway ganz weg: nicht jedes Modell einzeln je 2 s prüfen
            _pruef_cache[(basis, _ALLE)] = (jetzt, problem)
    _pruef_cache[(basis, modell)] = (jetzt, problem)
    return problem


def _litellm_ausgefallen(problem: str) -> None:
    """Merkt sich einen Ausfall mitten im Betrieb, damit die nächsten Aufrufe gleich zurückfallen."""
    _pruef_cache[(litellm_basis(), _ALLE)] = (time.monotonic(), problem)


def _rueckfall(baustein: str, modell: str | None, problem: str) -> Wahl:
    claude = claude_standardmodell(baustein)
    if not rueckfall_erlaubt(baustein):
        logger.error("LiteLLM für {} nicht nutzbar: {}. Kein Rückfall (WAMO_LLM_RUECKFALL).", baustein, problem)
        raise LLMNichtVerfuegbar(f"LiteLLM für {baustein} nicht nutzbar: {problem}")
    logger.error("LiteLLM für {} nicht nutzbar ({}): {}. Rückfall auf Claude ({}).", baustein, modell, problem, claude)
    return Wahl(baustein, "claude", claude, "litellm", problem)


async def waehlen(baustein: str) -> Wahl:
    """Anbieter und Modell für einen Baustein, mit Prüfung des Gateways und Rückfall auf Claude."""
    anbieter, modell = konfiguration(baustein)
    if anbieter == "claude":
        if modell and not modell.startswith("claude-"):
            # z. B. ein LiteLLM-Alias, der nach dem Zurückschalten stehen geblieben ist
            logger.error("{}={!r} ist kein Claude-Modell, nutze {}", _ENV[baustein][1], modell,
                         claude_standardmodell(baustein))
            modell = None
        return Wahl(baustein, "claude", modell or claude_standardmodell(baustein), "claude")
    problem = await litellm_problem(modell)
    if problem or not modell:
        return _rueckfall(baustein, modell, problem or "kein Modell gesetzt")
    return Wahl(baustein, "litellm", modell, "litellm")


# --- Clients -----------------------------------------------------------------------------------


@functools.cache
def anthropic_client() -> anthropic.AsyncAnthropic:
    """Ein Client für den ganzen Prozess (Gespräch, Websuche, Lernen, Bildschirm): der Aufbau
    (SSL-Kontext) kostet sonst bei jedem Aufruf Zeit im laufenden Sprachstrom.
    Ist der API-Schlüssel nicht an einen Workspace gebunden (Organisationsschlüssel), verlangt
    Anthropic die Workspace-ID als Header; sie kommt aus ANTHROPIC_WORKSPACE_ID."""
    workspace = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    return anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"], default_headers=headers)


@functools.cache
def _litellm_client(basis: str, schluessel: str) -> openai.AsyncOpenAI:
    # Je Adresse und Schlüssel ein Client (Verbindungen bleiben offen). Keine stillen Wiederholungen:
    # der Rückfall auf Claude ist die Wiederholung.
    return openai.AsyncOpenAI(base_url=basis, api_key=schluessel, max_retries=0,
                              timeout=openai.Timeout(ANFRAGE_TIMEOUT_SECS["lernen"], connect=VERBINDUNG_TIMEOUT_SECS))


def litellm_client() -> openai.AsyncOpenAI:
    return _litellm_client(litellm_basis(), _env("LITELLM_API_KEY"))


# --- Pipecat-Dienste für Gespräch und Zusammenfassung ------------------------------------------


class LiteLLMService(OpenAILLMService):
    """Pipecat-Dienst für das LiteLLM-Gateway (OpenAI-kompatibel).

    Nicht jedes Modell hinter dem Gateway kennt die Rolle "developer" (Pipecat schickt damit z. B.
    die Nachfrage nach einer Sprechpause). Sie wird deshalb als "user" gesendet. Der Client hat kurze
    Zeitgrenzen und keine Wiederholungen: ein hängendes Gateway soll WAMO nicht minutenlang stumm machen
    (Standard des SDK: 600 s und zwei Wiederholungen)."""

    supports_developer_role = False

    def create_client(self, api_key=None, base_url=None, organization=None, project=None, default_headers=None,
                      **kwargs):
        return openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            organization=organization,
            project=project,
            default_headers=default_headers,
            max_retries=0,
            timeout=openai.Timeout(GESPRAECH_LESE_TIMEOUT_SECS, connect=VERBINDUNG_TIMEOUT_SECS),
            http_client=openai.DefaultAsyncHttpxClient(
                limits=connection_limits(max_keepalive_connections=20, max_connections=100, keepalive_expiry=None)
            ),
        )


def gespraech_dienst(wahl: Wahl, system_instruction: str | None) -> LLMService:
    """Pipecat-Dienst für eine feste Wahl (auch für den Testsatz in eval/)."""
    if wahl.anbieter == "litellm":
        return LiteLLMService(
            api_key=_env("LITELLM_API_KEY"),
            base_url=litellm_basis(),
            settings=LiteLLMService.Settings(
                model=wahl.modell,
                system_instruction=system_instruction,
                extra=litellm_extra(),
            ),
        )
    return AnthropicLLMService(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        client=anthropic_client(),  # gemeinsamer Client, inklusive Workspace-Header (ANTHROPIC_WORKSPACE_ID)
        settings=AnthropicLLMService.Settings(
            model=wahl.modell,
            # Persona als system_instruction, nicht als System-Nachricht im Kontext: die
            # Satzende-Erkennung hängt ihre Regeln hier an. Eine System-Nachricht im Kontext
            # würde Pipecat dann komplett verwerfen.
            system_instruction=system_instruction,
            enable_prompt_caching=True,
            # Haiku denkt sonst vor jeder Antwort nach (bis 1,6 s bis zum ersten Wort).
            thinking=AnthropicLLMService.ThinkingConfig(type="disabled"),
        ),
    )


async def gespraech_llm(system_instruction: str) -> tuple[LLMService, Wahl]:
    """Dienst für das Gespräch nach WAMO_LLM_ANBIETER und WAMO_LLM_MODELL, mit Rückfall auf Claude.
    Wirft LLMNichtVerfuegbar, wenn LiteLLM nicht nutzbar und der Rückfall für das Gespräch aus ist."""
    wahl = await waehlen("gespraech")
    logger.info("Gesprächsmodell: {} über {}", wahl.modell, wahl.anbieter)
    return gespraech_dienst(wahl, system_instruction), wahl


async def zusammenfassung_llm() -> LLMService | None:
    """Eigener Dienst für Zusammenfassungen langer Gespräche, nur wenn WAMO_LLM_ZUSAMMENFASSUNG
    gesetzt ist. None: Pipecat fasst mit dem Gesprächsmodell zusammen."""
    if not _env("WAMO_LLM_ZUSAMMENFASSUNG"):
        return None
    wahl = await waehlen("zusammenfassung")
    logger.info("Zusammenfassung: {} über {}", wahl.modell, wahl.anbieter)
    return gespraech_dienst(wahl, None)


# --- Schlanker Aufruf für Lernen und Vision ----------------------------------------------------


def _claude_inhalt(inhalt: str | list[str | Bild]) -> str | list[dict]:
    if isinstance(inhalt, str):
        return inhalt
    teile: list[dict] = []
    for teil in inhalt:
        if isinstance(teil, Bild):
            quelle = {"type": "base64", "media_type": "image/jpeg", "data": teil.jpeg_base64}
            teile.append({"type": "image", "source": quelle})
        else:
            teile.append({"type": "text", "text": teil})
    return teile


def _openai_inhalt(inhalt: str | list[str | Bild]) -> str | list[dict]:
    if isinstance(inhalt, str):
        return inhalt
    teile: list[dict] = []
    for teil in inhalt:
        if isinstance(teil, Bild):
            teile.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{teil.jpeg_base64}"}})
        else:
            teile.append({"type": "text", "text": teil})
    return teile


@dataclass(frozen=True)
class _Auftrag:
    system: str
    inhalt: str | list[str | Bild]
    max_tokens: int
    json_schema: dict | None
    schema_name: str
    timeout: float


async def _claude_anfrage(wahl: Wahl, a: _Auftrag) -> Antwort:
    output_config: dict[str, Any] = {"effort": "low"}
    if a.json_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": a.json_schema}
    client = anthropic_client().with_options(
        timeout=anthropic.Timeout(a.timeout, connect=VERBINDUNG_TIMEOUT_SECS), max_retries=0
    )
    try:
        r = await client.messages.create(
            model=wahl.modell,
            max_tokens=a.max_tokens,
            system=a.system,
            output_config=output_config,
            messages=[{"role": "user", "content": _claude_inhalt(a.inhalt)}],
        )
    except anthropic.APIStatusError as e:
        raise LLMFehler(f"Claude: API-Fehler {e.status_code}") from None
    except anthropic.APIConnectionError:  # auch Zeitüberschreitung
        raise LLMFehler("Claude: keine Verbindung", verbindung=True) from None
    ende: Ende = {"refusal": "abgelehnt", "max_tokens": "zu_lang"}.get(r.stop_reason or "", "fertig")
    text = " ".join(b.text.strip() for b in r.content if b.type == "text").strip()
    return Antwort(text, ende, wahl)


async def _litellm_anfrage(wahl: Wahl, a: _Auftrag) -> Antwort:
    params: dict[str, Any] = {
        "model": wahl.modell,
        "max_tokens": a.max_tokens,
        "messages": [{"role": "system", "content": a.system}, {"role": "user", "content": _openai_inhalt(a.inhalt)}],
        "timeout": openai.Timeout(a.timeout, connect=VERBINDUNG_TIMEOUT_SECS),
    }
    if a.json_schema is not None:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": a.schema_name, "schema": a.json_schema, "strict": True},
        }
    params.update(litellm_extra())
    try:
        r = await litellm_client().chat.completions.create(**params)
    except openai.APIStatusError as e:
        raise LLMFehler(f"LiteLLM: HTTP {e.status_code}", verbindung=e.status_code >= 500) from None
    except openai.APIConnectionError:  # auch Zeitüberschreitung
        raise LLMFehler("LiteLLM: keine Verbindung", verbindung=True) from None
    except TypeError:  # unbekannter Parameter trotz Prüfung in litellm_extra
        raise LLMFehler("LiteLLM: ungültige Anfrage-Parameter (WAMO_LLM_EXTRA prüfen)") from None
    if not r.choices:
        raise LLMFehler("LiteLLM: leere Antwort")
    grund = r.choices[0].finish_reason or ""
    ende: Ende = {"content_filter": "abgelehnt", "length": "zu_lang"}.get(grund, "fertig")
    return Antwort((r.choices[0].message.content or "").strip(), ende, wahl)


async def anfrage(
    baustein: str,
    *,
    system: str,
    inhalt: str | list[str | Bild],
    max_tokens: int,
    json_schema: dict | None = None,
    schema_name: str = "antwort",
) -> Antwort:
    """Ein einzelner Modellaufruf für Lernen oder Vision, beim konfigurierten Anbieter.

    Fällt LiteLLM mitten im Betrieb aus (Netz, Gateway, Zeitgrenze), wiederholt der Aufruf einmal bei
    Claude, sofern der Rückfall für den Baustein erlaubt ist, und meldet das im Log. Wirft LLMFehler."""
    auftrag = _Auftrag(system, inhalt, max_tokens, json_schema, schema_name, ANFRAGE_TIMEOUT_SECS.get(baustein, 30.0))
    wahl = await waehlen(baustein)
    if wahl.anbieter == "litellm":
        try:
            return await _litellm_anfrage(wahl, auftrag)
        except LLMFehler as e:
            if not e.verbindung:
                raise
            _litellm_ausgefallen(str(e))
            wahl = _rueckfall(baustein, wahl.modell, str(e))
    return await _claude_anfrage(wahl, auftrag)


_ZAUN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def json_aus_text(text: str) -> Any:
    """JSON aus einer Modellantwort. Toleriert einen Markdown-Zaun (```json ... ```), den manche
    Modelle trotz Schema setzen. Wirft json.JSONDecodeError."""
    roh = (text or "").strip()
    if m := _ZAUN.match(roh):
        roh = m.group(1)
    return json.loads(roh)
