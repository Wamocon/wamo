"""Anbieter-Fabrik für die Sprachmodelle von WAMO (Ausbaustufen P4 und Modellzugang je Profil).

Modellzugang je Profil (modellzugang.py lädt ihn beim Sitzungsstart, Zugang unten):
  claude  Die Person hat einen eigenen Anthropic-Schlüssel hinterlegt (oder der Schlüssel des Kerns ist ihrem
          Profil zugeordnet). Standardmodell Haiku. Fällt Claude aus, übernehmen die Hausmodelle mit Hinweis.
  haus    Kein eigener Schlüssel oder vom Admin festgelegt: Gespräch, Lernen, Zusammenfassung und Bildschirm
          laufen über LiteLLM, die Websuche über SearXNG. Kein Aufruf an Anthropic, auch nicht als Rückfall.
Jede Sitzung hat ihren eigenen Anthropic-Client mit dem Schlüssel ihres Profils; es gibt keinen Client und
keinen Schlüssel, der über Profile hinweg geteilt wird.

Innerhalb von claude lässt sich jeder Baustein zusätzlich auf die Hausmodelle legen (Betrieb, ohne Kosten):

  Baustein          Anbieter (claude | litellm)   Modell
  gespraech         WAMO_LLM_ANBIETER             WAMO_LLM_MODELL (Claude: Modell des Profils, sonst Haiku)
  lernen            WAMO_LLM_LERNEN               WAMO_LLM_LERNEN_MODELL (leer bei litellm: wie das Gespräch)
  vision            WAMO_LLM_VISION               WAMO_LLM_VISION_MODELL (Claude: VISION_MODEL, sonst Haiku)
  zusammenfassung   WAMO_LLM_ZUSAMMENFASSUNG      WAMO_LLM_ZUSAMMENFASSUNG_MODELL (leer: wie das Gespräch)

Für haus-Profile gelten nur die Modellnamen; der Anbieter ist immer litellm. Das Gateway kommt aus
LITELLM_BASE_URL und LITELLM_API_KEY; zusätzliche Anfrage-Parameter aus WAMO_LLM_EXTRA als JSON-Objekt. Für die
Modelle hinter dem Gateway schaltet {"think": false} die Denkphase ab (gemessen 20.10.2026: sonst bis zu 20 s
bis zum ersten Wort).

Gruppen-Aliase des Gateways (z. B. wamo-allgemein) stehen nicht in /models; für sie prüft eine Anfrage mit
einem einzigen Ausgabe-Token, ob das Gateway sie bedient. WAMO_LLM_AUSBLENDEN (kommagetrennte Wortteile,
nur in der lokalen .env) blendet fremde Aliase aus Listen, Ausgaben und Ergebnisdateien aus.

Rückfall bei claude-Profilen: Ist ein auf litellm gelegter Baustein nicht nutzbar, nimmt er Claude mit dem
Schlüssel des Profils (WAMO_LLM_RUECKFALL: leer oder claude für alle, aus für keinen, oder eine Liste wie
"gespraech,zusammenfassung"). Grenze: Geprüft wird beim Start einer Sitzung (Lernen und Bildschirm vor jedem
Aufruf). Fällt ein Anbieter mitten im Gespräch aus, wechselt das laufende Gespräch nicht; kurze Zeitgrenzen
sorgen dafür, dass WAMO dann nicht hängt.

Kosten je Profil: Jeder Modellaufruf schreibt eine Logzeile "Verbrauch" mit Profilkennung, Anbieter, Modell
und Tokens, ohne Inhalt. Ein Log-Filter schwärzt alles, was wie ein Anthropic-Schlüssel aussieht.
"""

from __future__ import annotations

import fnmatch
import functools
import hashlib
import inspect
import json
import os
import re
import time
from urllib.parse import urlparse
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import anthropic
import httpx
import openai
from loguru import logger
from openai.resources.chat.completions import AsyncCompletions

from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.llm_service import LLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.utils.http import connection_limits

from env_schalter import zahl_im_bereich
from marker_wache import MarkerFallback
from schluessel import schwaerzen

Anbieter = Literal["claude", "litellm"]
Art = Literal["claude", "haus"]
Ende = Literal["fertig", "abgelehnt", "zu_lang"]
ANBIETER: tuple[Anbieter, ...] = ("claude", "litellm")
_ENV = {
    "gespraech": ("WAMO_LLM_ANBIETER", "WAMO_LLM_MODELL"),
    "lernen": ("WAMO_LLM_LERNEN", "WAMO_LLM_LERNEN_MODELL"),
    "vision": ("WAMO_LLM_VISION", "WAMO_LLM_VISION_MODELL"),
    "zusammenfassung": ("WAMO_LLM_ZUSAMMENFASSUNG", "WAMO_LLM_ZUSAMMENFASSUNG_MODELL"),
}
BAUSTEINE = tuple(_ENV)
# Standard ist überall Haiku (Vorgabe: Sonnet kostet ein Vielfaches); VISION_MODEL kann das Bildmodell anheben.
STANDARD_CLAUDE = "claude-haiku-5-5"
PRUEF_TTL_SECS = 30.0  # so lange gilt das Ergebnis einer Gateway-Prüfung
PRUEF_TIMEOUT_SECS = 2.0  # Prüfung vor dem Sitzungsstart darf das Gespräch kaum verzögern
PRUEF_ALIAS_TIMEOUT_SECS = 5.0  # Probe-Anfrage für Aliase, die nicht in /models stehen
CLAUDE_PRUEF_TIMEOUT_SECS = 3.0
CLAUDE_PRUEF_TTL_SECS = 60.0
# Zeitgrenze je Aufruf. Vision: zwei Versuche (erster Anbieter, dann Rückfall) passen in die 30 s des Werkzeugs.
ANFRAGE_TIMEOUT_SECS = {"lernen": 45.0, "vision": 12.0, "zusammenfassung": 60.0, "gespraech": 20.0}
VERBINDUNG_TIMEOUT_SECS = 3.0
# Hintergrundbausteine eines Profils mit Claude-Zugang: Das Hausmodell bekommt nur diese Zeit, danach übernimmt sofort Claude
# (Befund 31.10.2026: gemma4-31b für "lernen" lief zweimal in die volle Zeitgrenze von 45 s, 12:44 und 12:47; gemessen sind
# etwa 12 s je Fall, siehe eval/ergebnisse/bausteine/bericht.md). WAMO_LERNEN_HAUS_SECS ersetzt den Wert (0 = volle Zeitgrenze).
HAUS_VORVERSUCH_SECS = {"lernen": 15.0}
HAUS_PAUSE_SECS = 300.0  # nach einer Zeitüberschreitung geht der Baustein so lange gleich zu Claude (je Modell, je Prozess)
_haus_pause: dict[str, float] = {}
GESPRAECH_LESE_TIMEOUT_SECS = 20.0  # längste Pause im Stream, bevor das Gespräch die Anfrage aufgibt
ANTHROPIC_WOERTER = ("claude", "anthropic", "haiku", "sonnet", "opus")
_CLAUDE_MODELL = re.compile(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?")
_ALLE = "*"  # Cache-Schlüssel für "Gateway ganz weg", gilt für jedes Modell


class LLMFehler(Exception):
    """Ein Modellaufruf ist fehlgeschlagen. Der Text enthält nie Schlüssel oder Inhalte."""

    def __init__(self, text: str, *, verbindung: bool = False, gateway_weg: bool = False, zeitlimit: bool = False):
        super().__init__(text)
        self.verbindung = verbindung  # Netz, Gateway oder Anbieter weg: Rückfall sinnvoll
        self.gateway_weg = gateway_weg  # keine Verbindung aufgebaut: das ganze Gateway gilt als weg
        self.zeitlimit = zeitlimit  # nur diese eine Anfrage war zu langsam: andere Sitzungen nicht beeinträchtigen


class LLMNichtVerfuegbar(LLMFehler):
    """Kein erlaubter Anbieter ist für den Baustein nutzbar (bei haus-Profilen nie ein Rückfall auf Claude).
    gruende: bei der Wahl des Boss die Gründe je übersprungenem Kandidaten (ohne Vorspann), sonst leer."""

    gruende: str = ""


@dataclass(frozen=True, eq=False)
class Zugang:
    """Modellzugang einer Sitzung. Gleichheit ist Identität: zwei Sitzungen teilen nie einen Zugang oder Client.
    Der Schlüssel erscheint in keiner Darstellung (repr) und in keinem Log."""

    user_id: str
    art: Art
    quelle: str = "keiner"  # profil | zuordnung | labor | betreiber | festgelegt (Admin) | wahl (Person) | keiner | fehler
    claude_modell: str | None = None
    claude_schluessel: str | None = field(default=None, repr=False)
    claude_workspace: str | None = None
    stt: str = "soniox"
    tts: str = "soniox"
    hinweis: str | None = None
    # Hat die Person einen Claude-Code-Token hinterlegt? Nur dieses Merkmal, nie der Token selbst (den liefert
    # modellzugang.claude_code_zugang_fuer kurz vor einem Entwicklungslauf). Er berührt das Gespräch nicht.
    claude_code: bool = False
    _client: list = field(default_factory=list, repr=False, init=False)  # einmal je Zugang gebaut
    _zu: list = field(default_factory=list, repr=False, init=False)  # gefüllt, sobald die Sitzung schloss

    def __post_init__(self):
        if self.art == "claude" and not self.claude_schluessel:
            raise ValueError("Zugang claude braucht einen Schlüssel")
        if self.art == "haus" and self.claude_schluessel:
            raise ValueError("Zugang haus darf keinen Claude-Schlüssel tragen")

    @classmethod
    def haus(cls, user_id: str, quelle: str = "keiner", hinweis: str | None = None, *, stt: str = "soniox",
             tts: str = "soniox", claude_code: bool = False) -> Zugang:
        return cls(user_id, "haus", quelle, stt=stt, tts=tts, hinweis=hinweis, claude_code=claude_code)

    @classmethod
    def betreiber(cls) -> Zugang:
        """Schlüssel aus ANTHROPIC_API_KEY für Werkzeuge des Betreibers (Testsatz, Kommandozeile), nie für eine
        Sitzung: die bekommt ihren Zugang aus dem Profil (modellzugang.zugang_laden)."""
        schluessel = _env("ANTHROPIC_API_KEY")
        if not schluessel:
            return cls.haus("betreiber", "keiner", "ANTHROPIC_API_KEY fehlt")
        return cls("betreiber", "claude", "betreiber", claude_schluessel=schluessel,
                   claude_workspace=_env("ANTHROPIC_WORKSPACE_ID") or None)

    @property
    def kennung(self) -> str:
        """Kurze Profilkennung für Logs (die ersten acht Zeichen der Profil-ID)."""
        return (self.user_id or "-")[:8]

    def anthropic(self) -> anthropic.AsyncAnthropic:
        """Anthropic-Client dieser Sitzung mit dem Schlüssel des Profils (einmal gebaut, nie geteilt)."""
        if self.art != "claude" or not self.claude_schluessel:
            raise LLMNichtVerfuegbar("Dieses Profil nutzt nur Hausmodelle, Anthropic ist gesperrt")
        if not self._client:
            kopf = {"anthropic-workspace-id": self.claude_workspace} if self.claude_workspace else None
            if self._zu:
                raise LLMNichtVerfuegbar("Die Sitzung ist beendet, der Modellclient ist geschlossen")
            # Zeitgrenzen wie bei den Einzelaufrufen: ein hängendes Anthropic soll WAMO nicht Minuten stumm machen
            self._client.append(anthropic.AsyncAnthropic(
                api_key=self.claude_schluessel, default_headers=kopf, max_retries=1,
                timeout=anthropic.Timeout(GESPRAECH_LESE_TIMEOUT_SECS, connect=VERBINDUNG_TIMEOUT_SECS)))
        return self._client[0]

    async def schliessen(self) -> None:
        """Beendet den Client dieser Sitzung (Sitzungsende). Danach baut anthropic() keinen neuen mehr."""
        self._zu.append(True)
        while self._client:
            await self._client.pop().close()


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
        """Name für die Oberfläche, z. B. 'Claude Haiku 5.5', 'gemma4-31b (LiteLLM)' oder '... (Rückfall)'."""
        name = lesbarer_modellname(self.modell)
        if self.rueckfall:
            return f"{name} (Rückfall)"
        return f"{name} (LiteLLM)" if self.anbieter == "litellm" else name


@dataclass(frozen=True)
class Bild:
    """Ein JPEG-Bild als Base64 (Bildschirmfoto vom Host)."""

    jpeg_base64: str


@dataclass(frozen=True)
class Antwort:
    text: str
    ende: Ende
    wahl: Wahl
    eingabe: int = 0  # Tokens laut Anbieter (0, wenn er sie nicht meldet)
    ausgabe: int = 0


def lesbarer_modellname(model: str) -> str:
    """'claude-haiku-5-5' -> 'Claude Haiku 5.5'; andere Namen bleiben, wie sie sind."""
    m = _CLAUDE_MODELL.fullmatch(model or "")
    if not m:
        return model
    family, major, minor = m.groups()
    return f"Claude {family.capitalize()} {major}.{minor}" if minor else f"Claude {family.capitalize()} {major}"


# --- Logs: Schlüssel schwärzen, fremde Aliase ausblenden, Verbrauch je Profil --------------------------


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _ausgeblendete_woerter() -> list[str]:
    return [w.strip().lower() for w in _env("WAMO_LLM_AUSBLENDEN").split(",") if w.strip()]


def ausgeblendet(name: str) -> bool:
    """WAMO_LLM_AUSBLENDEN: kommagetrennte Wortteile. Modellnamen, die einen davon enthalten (etwa Aliase
    anderer Projekte auf demselben Gateway), erscheinen in keiner Liste, keiner Ausgabe und keiner Datei von
    WAMO. Die Wortteile selbst stehen nur in der lokalen .env, nie im Repository."""
    klein = (name or "").lower()
    return any(w in klein for w in _ausgeblendete_woerter())


def sichtbare_modelle(namen: list[str]) -> list[str]:
    return [n for n in namen if not ausgeblendet(n)]


def ausblenden_text(text: str) -> str:
    """Ersetzt in einem Text (Fehlermeldung, Log) jedes Wort, das einen ausgeblendeten Wortteil enthält."""
    woerter = _ausgeblendete_woerter()
    if not woerter or not text:
        return text
    muster = re.compile(r"[^\s\"',;\[\]{}()<>]*(?:" + "|".join(map(re.escape, woerter)) + r")[^\s\"',;\[\]{}()<>]*",
                        re.IGNORECASE)
    return muster.sub("<ausgeblendet>", text)


def _log_schuetzen(record: dict) -> None:
    record["message"] = ausblenden_text(schwaerzen(record["message"]))


def log_schutz_einrichten() -> None:
    """Loguru-Patcher für alle Senken: Schlüssel schwärzen, ausgeblendete Namen ersetzen. Bleibt beim
    Neuaufbau der Senken (logger.remove, logger.add) bestehen."""
    logger.configure(patcher=_log_schuetzen)


log_schutz_einrichten()


def verbrauch_loggen(zugang: Zugang | None, anbieter: str, modell: str, baustein: str, *, eingabe: int = 0,
                     ausgabe: int = 0, cache_lesen: int = 0, cache_schreiben: int = 0) -> None:
    """Eine Zeile je Modellaufruf: wer, womit, wie viele Tokens. Nie Inhalte."""
    logger.info("Verbrauch profil={} anbieter={} modell={} baustein={} ein={} aus={} cache_lesen={} cache_schreiben={}",
                zugang.kennung if zugang else "-", anbieter, modell, baustein, eingabe, ausgabe, cache_lesen,
                cache_schreiben)


# --- Konfiguration -----------------------------------------------------------------------------


def claude_standardmodell(baustein: str, zugang: Zugang | None = None) -> str:
    """Modell des Profils (vom Admin festgelegt), sonst VISION_MODEL bzw. CLAUDE_MODEL_FAST, sonst Haiku."""
    if zugang is not None and zugang.claude_modell:
        return zugang.claude_modell
    if baustein == "vision":
        return _env("VISION_MODEL") or _env("CLAUDE_MODEL_FAST") or STANDARD_CLAUDE
    return _env("CLAUDE_MODEL_FAST") or STANDARD_CLAUDE


def rueckfall_erlaubt(baustein: str) -> bool:
    """WAMO_LLM_RUECKFALL: leer oder claude (alle Bausteine), aus (keiner) oder eine Liste von Bausteinen."""
    wert = _env("WAMO_LLM_RUECKFALL").lower()
    if wert in ("", "claude", "an", "ja"):
        return True
    if wert in ("aus", "off", "0", "false", "nein"):
        return False
    return baustein in {teil.strip() for teil in wert.split(",")}


def vorgabe(baustein: str, zugang: Zugang | None = None) -> tuple[Anbieter | None, str | None]:
    """Was der Betrieb in der Umgebung vorgibt (Übersteuerung der Modellkarte): (Anbieter, Modell). Anbieter None heißt:
    nicht gesetzt, dann wählt der Boss. Für haus-Profile ist der Anbieter immer litellm."""
    if baustein not in _ENV:
        raise ValueError(f"unbekannter Baustein {baustein!r}")
    anbieter_env, modell_env = _ENV[baustein]
    haus = zugang is not None and zugang.art == "haus"
    wert = "litellm" if haus else _env(anbieter_env).lower()
    if wert and wert not in ANBIETER:
        logger.error("{}={!r} ist unbekannt (erlaubt: claude, litellm), nutze claude", anbieter_env, wert)
        wert = "claude"
    anbieter: Anbieter | None = ("litellm" if wert == "litellm" else "claude") if wert else None
    modell = _env(modell_env) or None
    gespraech_im_haus = haus or _env("WAMO_LLM_ANBIETER").lower() == "litellm"
    if anbieter == "litellm" and not modell and baustein in ("lernen", "zusammenfassung") and gespraech_im_haus:
        modell = _env("WAMO_LLM_MODELL") or None  # dasselbe Modell wie das Gespräch
    return anbieter, modell


def konfiguration(baustein: str, zugang: Zugang | None = None) -> tuple[Anbieter, str | None]:
    """(Anbieter, Modell): die Vorgabe des Betriebs aus der Umgebung, sonst der Standard aus der Modellkarte (Boss).
    Für haus-Profile ist der Anbieter immer litellm und das Modell das beste Hausmodell der Karte; für ein Profil mit
    Claude-Schlüssel ist es das Modell mit den besten Punkten, im Gespräch also Claude Haiku."""
    import boss

    anbieter, modell = vorgabe(baustein, zugang)
    klasse = boss.BAUSTEIN_KLASSE[baustein]
    if anbieter is None:  # der Betrieb legt nichts fest: Modellkarte
        liste = boss.rangliste(klasse, claude_erlaubt=zugang is None or zugang.art == "claude")
        if liste and liste[0].anbieter == "litellm":
            return "litellm", liste[0].alias
        return "claude", None
    if anbieter == "litellm" and not modell:
        modell = boss.hausstandard(klasse)
    return anbieter, modell


def litellm_basis() -> str:
    return _env("LITELLM_BASE_URL").rstrip("/")


_ERLAUBTE_PARAMETER = frozenset(inspect.signature(AsyncCompletions.create).parameters) - {
    "self", "messages", "model", "stream", "stream_options", "tools", "tool_choice", "timeout",
}


def litellm_extra() -> dict[str, Any]:
    """Zusätzliche Parameter für jede LiteLLM-Anfrage aus WAMO_LLM_EXTRA (JSON-Objekt), z. B.
    {"think": false} oder {"reasoning_effort": "none"}. Was das OpenAI-SDK nicht als Parameter kennt, wandert
    in extra_body, statt die Anfrage abzubrechen."""
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


async def _alias_pruefen(modell: str, *, client: httpx.AsyncClient | None = None) -> str | None:
    """Gruppen-Aliase des Gateways stehen nicht in /models. Für einen Namen, der dort fehlt, prüft eine
    Anfrage mit einem einzigen Ausgabe-Token, ob das Gateway ihn bedient (Ergebnis wird zwischengespeichert).
    Wirft LLMFehler bei Netz- oder Schlüsselproblemen; sonst None oder der Grund."""
    url = f"{litellm_basis()}/chat/completions"
    kopf = {"Authorization": f"Bearer {_env('LITELLM_API_KEY')}"}
    koerper = {"model": modell, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=PRUEF_ALIAS_TIMEOUT_SECS) as eigener:
                r = await eigener.post(url, headers=kopf, json=koerper)
        else:
            r = await client.post(url, headers=kopf, json=koerper, timeout=PRUEF_ALIAS_TIMEOUT_SECS)
    except httpx.HTTPError as e:
        raise LLMFehler(f"Gateway nicht erreichbar ({type(e).__name__})", verbindung=True) from None
    if r.status_code == 200:
        return None
    if r.status_code in (401, 403):
        raise LLMFehler(f"Gateway lehnt den Schlüssel ab (HTTP {r.status_code})")
    if r.status_code >= 500:
        raise LLMFehler(f"Gateway antwortet mit HTTP {r.status_code}", verbindung=True)
    return f"das Gateway bietet das Modell {modell!r} nicht an"


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
        problem = None if _angeboten(modell, modelle) else await _alias_pruefen(modell)
    except LLMFehler as e:
        problem = str(e)
        if e.verbindung:  # Gateway ganz weg: nicht jedes Modell einzeln je 2 s prüfen
            _pruef_cache[(basis, _ALLE)] = (jetzt, problem)
    _pruef_cache[(basis, modell)] = (jetzt, problem)
    return problem


def _litellm_ausgefallen(problem: str, modell: str | None = None) -> None:
    """Merkt sich einen Ausfall mitten im Betrieb, damit die nächsten Aufrufe gleich zurückfallen. Ohne Modell
    gilt das ganze Gateway als weg (keine Verbindung), sonst nur dieses Modell (Fehler 5xx)."""
    _pruef_cache[(litellm_basis(), modell or _ALLE)] = (time.monotonic(), problem)


# --- Prüfung von Claude (nur für claude-Profile) -------------------------------------------------

_claude_cache: dict[str, tuple[float, str | None]] = {}


def _claude_kennung(zugang: Zugang) -> str:
    # Nur ein Hash des Schlüssels als Cache-Schlüssel, nie der Schlüssel selbst
    return hashlib.sha256((zugang.claude_schluessel or "").encode("utf-8")).hexdigest()


async def claude_problem(zugang: Zugang) -> str | None:
    """Grund, warum Claude mit dem Schlüssel des Profils gerade nicht nutzbar ist, oder None. Kostet nichts
    (Modellliste), Ergebnis gilt 60 s. Wirft LLMNichtVerfuegbar bei haus-Profilen (nie ein Aufruf an Anthropic)."""
    if zugang.art != "claude":
        raise LLMNichtVerfuegbar("Dieses Profil nutzt nur Hausmodelle, Anthropic ist gesperrt")
    kennung, jetzt = _claude_kennung(zugang), time.monotonic()
    if (eintrag := _claude_cache.get(kennung)) and jetzt - eintrag[0] < CLAUDE_PRUEF_TTL_SECS:
        return eintrag[1]
    client = zugang.anthropic().with_options(
        timeout=anthropic.Timeout(CLAUDE_PRUEF_TIMEOUT_SECS, connect=VERBINDUNG_TIMEOUT_SECS), max_retries=0)
    try:
        await client.models.list(limit=1)
        problem = None
    except anthropic.AuthenticationError:
        problem = "Claude lehnt den Schlüssel ab"
    except anthropic.PermissionDeniedError:
        problem = None  # Schlüssel ohne Recht auf die Modellliste: ob Nachrichten gehen, zeigt erst der Aufruf
    except anthropic.APIStatusError as e:
        problem = f"Claude antwortet mit HTTP {e.status_code}"
    except anthropic.APIConnectionError:
        problem = "Claude nicht erreichbar"
    _claude_cache[kennung] = (jetzt, problem)
    return problem


def claude_bekannt_weg(zugang: Zugang) -> str | None:
    """Ein jüngst bekannter Ausfall von Claude für den Schlüssel dieses Zugangs (ohne neue Anfrage), sonst None."""
    eintrag = _claude_cache.get(_claude_kennung(zugang))
    if eintrag and time.monotonic() - eintrag[0] < CLAUDE_PRUEF_TTL_SECS:
        return eintrag[1]
    return None


def _claude_ausgefallen(zugang: Zugang, problem: str) -> None:
    _claude_cache[_claude_kennung(zugang)] = (time.monotonic(), problem)


# --- Auswahl je Baustein ---------------------------------------------------------------------------


def _rueckfall_auf_claude(baustein: str, modell: str | None, problem: str, zugang: Zugang) -> Wahl:
    """Ein auf litellm gelegter Baustein ist nicht nutzbar. Nur claude-Profile dürfen zu Claude wechseln."""
    if zugang.art != "claude":
        logger.error("LiteLLM für {} nicht nutzbar: {}. Profil {} nutzt nur Hausmodelle: kein Rückfall auf Claude.",
                     baustein, problem, zugang.kennung)
        raise LLMNichtVerfuegbar(f"Hausmodell für {baustein} nicht nutzbar: {problem}")
    if not rueckfall_erlaubt(baustein):
        logger.error("LiteLLM für {} nicht nutzbar: {}. Kein Rückfall (WAMO_LLM_RUECKFALL).", baustein, problem)
        raise LLMNichtVerfuegbar(f"LiteLLM für {baustein} nicht nutzbar: {problem}")
    claude = claude_standardmodell(baustein, zugang)
    logger.error("LiteLLM für {} nicht nutzbar ({}): {}. Rückfall auf Claude ({}).", baustein, modell, problem, claude)
    return Wahl(baustein, "claude", claude, "litellm", problem)


def anthropic_ziel(modell: str | None) -> str | None:
    """Grund, wenn ein "Hausmodell" in Wahrheit zu Anthropic führen würde (Claude-Modellname, Anthropic-Alias auf
    dem Gateway oder ein Gateway auf einem Anthropic-Host). Haus-Profile lehnen das ab."""
    klein = (modell or "").lower()
    # Teilstrings statt Anfang: auch "bedrock/anthropic.claude-…", "us.anthropic.…" und die Modellfamilien Haiku, Sonnet, Opus
    if any(wort in klein for wort in ANTHROPIC_WOERTER):
        return f"Das Modell {modell!r} führt zu Anthropic"
    host = (urlparse(litellm_basis()).hostname or "").lower()
    if host == "anthropic.com" or host.endswith(".anthropic.com"):
        return "LITELLM_BASE_URL zeigt auf Anthropic"
    return None


async def _haus_wahl(baustein: str, zugang: Zugang, gewuenscht: Anbieter, grund: str | None = None) -> Wahl:
    """Bestes nutzbares Hausmodell für den Baustein (Boss): zuerst die Vorgabe des Betriebs, dann die Modellkarte der
    Reihe nach; für das Gespräch zusätzlich mit Probe, ob das Modell jetzt schnell genug antwortet. LLMNichtVerfuegbar,
    wenn keines nutzbar ist (nie ein Ersatz durch Claude)."""
    import boss

    _anbieter, vorgabe_modell = vorgabe(baustein, Zugang.haus(zugang.user_id))
    try:
        wahl = await boss.waehlen(boss.BAUSTEIN_KLASSE[baustein], Zugang.haus(zugang.user_id), baustein=baustein,
                                  bevorzugt=("litellm", vorgabe_modell) if vorgabe_modell else None)
    except LLMNichtVerfuegbar as e:
        fehler = LLMNichtVerfuegbar(f"Hausmodell für {baustein} nicht nutzbar: {e.gruende or e}")
        fehler.gruende = e.gruende
        raise fehler from None
    if gewuenscht != wahl.gewuenscht or grund:
        return Wahl(wahl.baustein, wahl.anbieter, wahl.modell, gewuenscht, "; ".join(x for x in (grund, wahl.grund) if x) or None)
    return wahl


async def waehlen(baustein: str, zugang: Zugang) -> Wahl:
    """Anbieter und Modell für einen Baustein nach dem Zugang des Profils. Der Boss (boss.py) wählt aus der
    Modellkarte, es sei denn, der Betrieb legt den Anbieter in der Umgebung fest (WAMO_LLM_*)."""
    import boss

    if zugang.art == "haus":
        return await _haus_wahl(baustein, zugang, "litellm")
    anbieter_vorgabe, _modell_vorgabe = vorgabe(baustein, zugang)
    if anbieter_vorgabe is None:
        return await boss.waehlen(boss.BAUSTEIN_KLASSE[baustein], zugang, baustein=baustein)
    anbieter, modell = konfiguration(baustein, zugang)
    if anbieter == "claude":
        if modell and not modell.startswith("claude-"):
            # z. B. ein LiteLLM-Alias, der nach dem Zurückschalten stehen geblieben ist
            logger.error("{}={!r} ist kein Claude-Modell, nutze {}", _ENV[baustein][1], modell,
                         claude_standardmodell(baustein, zugang))
            modell = None
        # Das vom Admin festgelegte Modell des Profils geht der Betriebsvorgabe (WAMO_LLM_*_MODELL) vor
        return Wahl(baustein, "claude", zugang.claude_modell or modell or claude_standardmodell(baustein, zugang),
                    "claude")
    problem = await litellm_problem(modell)
    if not problem and baustein == "gespraech" and boss.probe_an():
        problem = await boss.live_problem(modell or "")
    if problem or not modell:
        return _rueckfall_auf_claude(baustein, modell, problem or "kein Modell gesetzt", zugang)
    return Wahl(baustein, "litellm", modell, "litellm")


async def _claude_oder_haus(baustein: str, wahl: Wahl, zugang: Zugang) -> Wahl:
    """Bei einer Claude-Wahl: ist Claude für das Profil gerade nicht nutzbar, übernehmen die Hausmodelle."""
    if wahl.anbieter != "claude" or (problem := await claude_problem(zugang)) is None:
        return wahl
    try:
        ersatz = await _haus_wahl(baustein, zugang, "claude", problem)
    except LLMNichtVerfuegbar as e:
        logger.error("Claude für Profil {} nicht nutzbar ({}) und kein Hausmodell: {}", zugang.kennung, problem, e)
        raise LLMNichtVerfuegbar(f"{problem}; {e}") from None
    logger.warning("Claude für Profil {} nicht nutzbar ({}): Rückfall auf das Hausmodell {}", zugang.kennung,
                   problem, ersatz.modell)
    return ersatz


# --- Clients für Hausmodelle -------------------------------------------------------------------------


@functools.cache
def _litellm_client(basis: str, schluessel: str) -> openai.AsyncOpenAI:
    # Ein Client je Gateway (der Schlüssel des Kerns für das Gateway, nicht der eines Profils). Keine stillen
    # Wiederholungen: der Rückfall ist die Wiederholung.
    return openai.AsyncOpenAI(base_url=basis, api_key=schluessel, max_retries=0,
                              timeout=openai.Timeout(ANFRAGE_TIMEOUT_SECS["lernen"], connect=VERBINDUNG_TIMEOUT_SECS))


def litellm_client() -> openai.AsyncOpenAI:
    return _litellm_client(litellm_basis(), _env("LITELLM_API_KEY"))


# --- Pipecat-Dienste für Gespräch und Zusammenfassung ------------------------------------------


class _Verbrauch:
    """Schreibt den Verbrauch jedes Modellaufrufs des Dienstes mit Profilkennung ins Log (ohne Inhalt)."""

    _wamo_zugang: Zugang | None = None
    _wamo_baustein: str = "gespraech"
    _wamo_anbieter: str = ""

    async def start_llm_usage_metrics(self, tokens: LLMTokenUsage):
        verbrauch_loggen(self._wamo_zugang, self._wamo_anbieter, str(self._settings.model), self._wamo_baustein,
                         eingabe=tokens.prompt_tokens or 0, ausgabe=tokens.completion_tokens or 0,
                         cache_lesen=tokens.cache_read_input_tokens or 0,
                         cache_schreiben=tokens.cache_creation_input_tokens or 0)
        await super().start_llm_usage_metrics(tokens)


class LiteLLMService(MarkerFallback, _Verbrauch, OpenAILLMService):
    """Pipecat-Dienst für das LiteLLM-Gateway (OpenAI-kompatibel).

    Nicht jedes Modell hinter dem Gateway kennt die Rolle "developer" (Pipecat schickt damit z. B.
    die Nachfrage nach einer Sprechpause). Sie wird deshalb als "user" gesendet. Der Client hat kurze
    Zeitgrenzen und keine Wiederholungen: ein hängendes Gateway soll WAMO nicht minutenlang stumm machen
    (Standard des SDK: 600 s und zwei Wiederholungen)."""

    supports_developer_role = False
    _wamo_anbieter = "litellm"

    def __init__(self, *, lese_timeout: float = GESPRAECH_LESE_TIMEOUT_SECS, **kwargs):
        # vor super().__init__, denn dort entsteht der Client (create_client)
        self._wamo_lese_timeout = lese_timeout
        super().__init__(**kwargs)

    def create_client(self, api_key=None, base_url=None, organization=None, project=None, default_headers=None,
                      **kwargs):
        return openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            organization=organization,
            project=project,
            default_headers=default_headers,
            max_retries=0,
            timeout=openai.Timeout(self._wamo_lese_timeout, connect=VERBINDUNG_TIMEOUT_SECS),
            http_client=openai.DefaultAsyncHttpxClient(
                limits=connection_limits(max_keepalive_connections=20, max_connections=100, keepalive_expiry=None)
            ),
        )


class ClaudeService(MarkerFallback, _Verbrauch, AnthropicLLMService):
    """Anthropic-Dienst einer Sitzung mit dem Client des Profils."""

    _wamo_anbieter = "claude"


def gespraech_dienst(wahl: Wahl, system_instruction: str | None, zugang: Zugang,
                     baustein: str = "gespraech") -> LLMService:
    """Pipecat-Dienst für eine feste Wahl (auch für den Testsatz in eval/)."""
    if wahl.anbieter == "litellm":
        dienst: LLMService = LiteLLMService(
            # Die Zusammenfassung läuft ohne Stream (Pipecat run_inference): die Lesezeit gilt dort für die
            # ganze Antwort, nicht für die Pause zwischen zwei Stücken
            lese_timeout=ANFRAGE_TIMEOUT_SECS["zusammenfassung"] if baustein == "zusammenfassung"
            else GESPRAECH_LESE_TIMEOUT_SECS,
            api_key=_env("LITELLM_API_KEY"),
            base_url=litellm_basis(),
            settings=LiteLLMService.Settings(
                model=wahl.modell,
                system_instruction=system_instruction,
                extra=litellm_extra(),
            ),
        )
    else:
        dienst = ClaudeService(
            api_key=zugang.claude_schluessel or "",
            client=zugang.anthropic(),  # Client dieser Sitzung mit dem Schlüssel des Profils
            settings=ClaudeService.Settings(
                model=wahl.modell,
                # Persona als system_instruction, nicht als System-Nachricht im Kontext: die
                # Satzende-Erkennung hängt ihre Regeln hier an. Eine System-Nachricht im Kontext
                # würde Pipecat dann komplett verwerfen.
                system_instruction=system_instruction,
                enable_prompt_caching=True,
                # Haiku denkt sonst vor jeder Antwort nach (bis 1,6 s bis zum ersten Wort).
                thinking=ClaudeService.ThinkingConfig(type="disabled"),
            ),
        )
    dienst._wamo_zugang = zugang
    dienst._wamo_baustein = baustein
    return dienst


async def gespraech_llm(system_instruction: str, zugang: Zugang) -> tuple[LLMService, Wahl]:
    """Dienst für das Gespräch nach dem Zugang des Profils. Wirft LLMNichtVerfuegbar, wenn kein erlaubter
    Anbieter nutzbar ist (haus-Profile fallen nie auf Claude zurück)."""
    wahl = await _claude_oder_haus("gespraech", await waehlen("gespraech", zugang), zugang)
    logger.info("Gesprächsmodell für Profil {}: {} über {} (Zugang {})", zugang.kennung, wahl.modell, wahl.anbieter,
                zugang.art)
    return gespraech_dienst(wahl, system_instruction, zugang), wahl


async def zusammenfassung_llm(zugang: Zugang) -> LLMService | None:
    """Eigener Dienst für Zusammenfassungen langer Gespräche. None: Pipecat fasst mit dem Gesprächsmodell der
    Sitzung zusammen; das gilt nur, wenn Gespräch und Zusammenfassung bei Claude liegen. Läuft eines davon über
    ein Hausmodell, gibt es immer einen eigenen Dienst mit längerer Zeitgrenze (Hausmodelle schreiben langsamer,
    und die Zusammenfassung wartet auf die ganze Antwort)."""
    wahl = await _claude_oder_haus("zusammenfassung", await waehlen("zusammenfassung", zugang), zugang)
    nur_claude = wahl.anbieter == "claude" and konfiguration("gespraech", zugang)[0] == "claude"
    if nur_claude and not _env("WAMO_LLM_ZUSAMMENFASSUNG"):
        return None
    logger.info("Zusammenfassung für Profil {}: {} über {}", zugang.kennung, wahl.modell, wahl.anbieter)
    return gespraech_dienst(wahl, None, zugang, "zusammenfassung")


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


# Systemtext je tatsächlich genutztem Anbieter (z. B. Koordinaten: Claude in Bildpixeln, Hausmodelle auf 0 bis
# 1000); so passt er auch nach einem Rückfall auf den anderen Anbieter
System = str | Callable[[Wahl], str]


@dataclass(frozen=True)
class _Auftrag:
    baustein: str
    system: System
    inhalt: str | list[str | Bild]
    max_tokens: int
    json_schema: dict | None
    schema_name: str
    timeout: float
    denken: bool = True  # False: Claude ohne Denkphase (sonst zählt sie zu max_tokens und die Antwort kann abbrechen)


def _systemtext(a: _Auftrag, wahl: Wahl) -> str:
    return a.system(wahl) if callable(a.system) else a.system


async def _claude_anfrage(wahl: Wahl, a: _Auftrag, zugang: Zugang) -> Antwort:
    output_config: dict[str, Any] = {"effort": "low"}
    if a.json_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": a.json_schema}
    client = zugang.anthropic().with_options(
        timeout=anthropic.Timeout(a.timeout, connect=VERBINDUNG_TIMEOUT_SECS), max_retries=0
    )
    zusatz: dict[str, Any] = {} if a.denken else {"thinking": {"type": "disabled"}}
    try:
        r = await client.messages.create(
            model=wahl.modell,
            max_tokens=a.max_tokens,
            system=_systemtext(a, wahl),
            output_config=output_config,
            messages=[{"role": "user", "content": _claude_inhalt(a.inhalt)}],
            **zusatz,
        )
    except anthropic.AuthenticationError:
        raise LLMFehler("Claude: Schlüssel abgelehnt", verbindung=True) from None
    except anthropic.APIStatusError as e:
        raise LLMFehler(f"Claude: API-Fehler {e.status_code}", verbindung=e.status_code >= 500) from None
    except anthropic.APITimeoutError:
        raise LLMFehler("Claude: Zeitüberschreitung", verbindung=True, zeitlimit=True) from None
    except anthropic.APIConnectionError:
        raise LLMFehler("Claude: keine Verbindung", verbindung=True) from None
    ein = aus = 0
    if (u := getattr(r, "usage", None)) is not None:
        ein, aus = u.input_tokens or 0, u.output_tokens or 0
        verbrauch_loggen(zugang, "claude", wahl.modell, a.baustein, eingabe=ein, ausgabe=aus,
                         cache_lesen=getattr(u, "cache_read_input_tokens", 0) or 0,
                         cache_schreiben=getattr(u, "cache_creation_input_tokens", 0) or 0)
    ende: Ende = {"refusal": "abgelehnt", "max_tokens": "zu_lang"}.get(r.stop_reason or "", "fertig")
    text = " ".join(b.text.strip() for b in r.content if b.type == "text").strip()
    return Antwort(text, ende, wahl, ein, aus)


async def _litellm_anfrage(wahl: Wahl, a: _Auftrag, zugang: Zugang) -> Antwort:
    params: dict[str, Any] = {
        "model": wahl.modell,
        "max_tokens": a.max_tokens,
        "messages": [{"role": "system", "content": _systemtext(a, wahl)}, {"role": "user", "content": _openai_inhalt(a.inhalt)}],
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
    except openai.APITimeoutError:
        raise LLMFehler("LiteLLM: Zeitüberschreitung", verbindung=True, zeitlimit=True) from None
    except openai.APIConnectionError:
        raise LLMFehler("LiteLLM: keine Verbindung", verbindung=True, gateway_weg=True) from None
    except TypeError:  # unbekannter Parameter trotz Prüfung in litellm_extra
        raise LLMFehler("LiteLLM: ungültige Anfrage-Parameter (WAMO_LLM_EXTRA prüfen)") from None
    if not r.choices:
        raise LLMFehler("LiteLLM: leere Antwort")
    ein = aus = 0
    if (u := getattr(r, "usage", None)) is not None:
        ein, aus = getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0
        verbrauch_loggen(zugang, "litellm", wahl.modell, a.baustein, eingabe=ein, ausgabe=aus)
    grund = r.choices[0].finish_reason or ""
    ende: Ende = {"content_filter": "abgelehnt", "length": "zu_lang"}.get(grund, "fertig")
    return Antwort((r.choices[0].message.content or "").strip(), ende, wahl, ein, aus)


def haus_vorversuch_secs(baustein: str, zugang: Zugang) -> float | None:
    """Zeitgrenze für den Hausmodell-Versuch eines Hintergrundbausteins, wenn ein Rückfall auf Claude möglich ist
    (Profil mit Claude-Zugang, WAMO_LLM_RUECKFALL erlaubt es), sonst None (dann gilt die volle Zeitgrenze des Bausteins)."""
    standard = HAUS_VORVERSUCH_SECS.get(baustein)
    if standard is None or zugang.art != "claude" or not rueckfall_erlaubt(baustein):
        return None
    if baustein == "lernen":
        wert = zahl_im_bereich("WAMO_LERNEN_HAUS_SECS", 0, 600)
        if wert is not None:
            return wert or None  # 0 schaltet die Verkürzung ab
    return standard


def haus_pausiert(modell: str | None) -> bool:
    return bool(modell) and time.monotonic() < _haus_pause.get(modell or "", 0.0)


def _haus_pausieren(modell: str | None) -> None:
    if modell:
        _haus_pause[modell] = time.monotonic() + HAUS_PAUSE_SECS


async def anfrage(
    baustein: str,
    *,
    zugang: Zugang,
    system: System,
    inhalt: str | list[str | Bild],
    max_tokens: int,
    json_schema: dict | None = None,
    schema_name: str = "antwort",
) -> Antwort:
    """Ein einzelner Modellaufruf für Lernen oder Vision, nach dem Zugang des Profils.

    Rückfälle bei Ausfall (Netz, Anbieter, Zeitgrenze, abgelehnter Schlüssel), jeweils mit Meldung im Log:
      claude-Profil, Baustein auf Claude    einmal mit dem Hausmodell
      claude-Profil, Baustein auf LiteLLM   einmal mit Claude (WAMO_LLM_RUECKFALL)
      haus-Profil                           kein Rückfall, LLMNichtVerfuegbar oder LLMFehler
    Wirft LLMFehler."""
    auftrag = _Auftrag(baustein, system, inhalt, max_tokens, json_schema, schema_name,
                       ANFRAGE_TIMEOUT_SECS.get(baustein, 30.0))
    wahl = await waehlen(baustein, zugang)
    if wahl.anbieter == "litellm":
        grenze = haus_vorversuch_secs(baustein, zugang)
        if grenze is not None and haus_pausiert(wahl.modell):
            # Das Hausmodell war vor kurzem zu langsam: nicht noch einmal warten
            claude = claude_standardmodell(baustein, zugang)
            logger.info("Hausmodell {} für {} war vor kurzem zu langsam: gleich Claude ({})", wahl.modell, baustein, claude)
            wahl = Wahl(baustein, "claude", claude, "litellm", "LiteLLM: vor kurzem zu langsam")
            return await _claude_anfrage(wahl, auftrag, zugang)
        versuch = replace(auftrag, timeout=min(auftrag.timeout, grenze)) if grenze is not None else auftrag
        try:
            return await _litellm_anfrage(wahl, versuch, zugang)
        except LLMFehler as e:
            if not e.verbindung:
                raise
            if not e.zeitlimit:  # eine langsame Anfrage sperrt weder das Gateway noch andere Sitzungen
                _litellm_ausgefallen(str(e), None if e.gateway_weg else wahl.modell)
            elif grenze is not None:
                _haus_pausieren(wahl.modell)
                logger.warning("Hausmodell {} für {} nach {:.0f} s ohne Antwort: Pause von {:.0f} s, Claude übernimmt",
                               wahl.modell, baustein, versuch.timeout, HAUS_PAUSE_SECS)
            wahl = _rueckfall_auf_claude(baustein, wahl.modell, str(e), zugang)
        return await _claude_anfrage(wahl, auftrag, zugang)
    try:
        return await _claude_anfrage(wahl, auftrag, zugang)
    except LLMFehler as e:
        if not e.verbindung:
            raise
        if not e.zeitlimit:
            _claude_ausgefallen(zugang, str(e))
        ersatz = await _haus_wahl(baustein, zugang, "claude", str(e))
        logger.warning("Claude für {} nicht nutzbar ({}): einmal mit dem Hausmodell {}", baustein, e, ersatz.modell)
        return await _litellm_anfrage(ersatz, auftrag, zugang)


async def modell_anfrage(
    wahl: Wahl,
    *,
    zugang: Zugang,
    system: System,
    inhalt: str | list[str | Bild],
    max_tokens: int,
    timeout: float,
    baustein: str,
    json_schema: dict | None = None,
    schema_name: str = "antwort",
    denken: bool = False,
) -> Antwort:
    """Ein Aufruf genau dieser Wahl, ohne Rückfall (den Rückfall entscheidet der Boss, boss.py). Zwei Sperren, die
    unabhängig von der Auswahl gelten: ein haus-Profil ruft nie Claude auf, und ein "Hausmodell", das zu Anthropic
    führt, wird abgelehnt. Claude läuft standardmäßig ohne Denkphase (denken=False), damit die Tokengrenze für die
    Antwort gilt. Wirft LLMFehler."""
    if wahl.anbieter == "claude" and zugang.art != "claude":
        raise LLMNichtVerfuegbar("Dieses Profil nutzt nur Hausmodelle, Anthropic ist gesperrt")
    if wahl.anbieter == "litellm" and (ziel := anthropic_ziel(wahl.modell)):
        raise LLMNichtVerfuegbar(f"Hausmodell {wahl.modell!r} nicht nutzbar: {ziel}")
    auftrag = _Auftrag(baustein, system, inhalt, max_tokens, json_schema, schema_name, timeout, denken)
    try:
        if wahl.anbieter == "litellm":
            return await _litellm_anfrage(wahl, auftrag, zugang)
        return await _claude_anfrage(wahl, auftrag, zugang)
    except LLMFehler as e:
        if e.verbindung and not e.zeitlimit:  # eine langsame Anfrage sperrt weder das Gateway noch andere Sitzungen
            if wahl.anbieter == "litellm":
                _litellm_ausgefallen(str(e), None if e.gateway_weg else wahl.modell)
            else:
                _claude_ausgefallen(zugang, str(e))
        raise


_ZAUN = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def json_aus_text(text: str) -> Any:
    """JSON aus einer Modellantwort. Toleriert einen Markdown-Zaun (```json ... ```), den manche
    Modelle trotz Schema setzen. Wirft json.JSONDecodeError."""
    roh = (text or "").strip()
    if m := _ZAUN.match(roh):
        roh = m.group(1)
    return json.loads(roh)
