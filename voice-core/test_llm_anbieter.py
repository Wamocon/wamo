"""Tests der Anbieter-Fabrik (llm_anbieter.py): Modellzugang je Profil, Umschaltung, Rückfälle, Gateway-Prüfung,
Zeitgrenzen, Verbrauch je Profil und Werkzeugformat für beide Anbieter. Kein Test spricht mit dem echten
Gateway oder mit Anthropic: das Netz ist über httpx.MockTransport, Attrappen oder Monkeypatch ersetzt
(conftest.py setzt zusätzlich .invalid-Adressen).
"""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from loguru import logger

import llm_anbieter as la
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.anthropic.llm import AnthropicLLMService
from tools import WEBSUCHE_SCHEMA, ZEIT_SCHEMA

SCHLUESSEL = "sk-test-geheim-4711"  # Gateway
PROFIL_SCHLUESSEL = "sk-ant-api03-PROFILeins-0123456789abcdefghijkl"
BASIS = "http://gateway.test.invalid:4000/v1"
USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"


@pytest.fixture(autouse=True)
def _sauber(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL_FAST", "claude-haiku-5-5")
    monkeypatch.delenv("VISION_MODEL", raising=False)


@pytest.fixture
def cz():
    """Zugang eines Profils mit eigenem Claude-Schlüssel."""
    return la.Zugang(USER, "claude", "profil", claude_schluessel=PROFIL_SCHLUESSEL)


@pytest.fixture
def hz():
    """Zugang eines Profils mit nur Hausmodellen."""
    return la.Zugang.haus(USER, "keiner")


@pytest.fixture
def log():
    """Alles, was loguru in diesem Test schreibt, nach dem Log-Schutz (caplog sieht loguru nicht)."""
    zeilen: list[str] = []
    sink = logger.add(lambda m: zeilen.append(str(m)), level="DEBUG")
    yield zeilen
    logger.remove(sink)


def _litellm(monkeypatch, modell="qwen3.6:35b", schluessel=SCHLUESSEL, baustein="gespraech"):
    anbieter_env, modell_env = la._ENV[baustein]
    monkeypatch.setenv(anbieter_env, "litellm")
    monkeypatch.setenv(modell_env, modell)
    monkeypatch.setenv("LITELLM_BASE_URL", BASIS)
    if schluessel:
        monkeypatch.setenv("LITELLM_API_KEY", schluessel)


def _hausmodelle(monkeypatch, modell="gemma4-31b", vision="wamo-vision"):
    """Betriebsvorgabe für haus-Profile: nur Modellnamen, der Anbieter ist immer litellm."""
    monkeypatch.setenv("WAMO_LLM_MODELL", modell)
    monkeypatch.setenv("WAMO_LLM_VISION_MODELL", vision)
    monkeypatch.setenv("LITELLM_BASE_URL", BASIS)
    monkeypatch.setenv("LITELLM_API_KEY", SCHLUESSEL)


def _gateway(monkeypatch, handler):
    """Ersetzt das Netz für litellm_modelle durch einen MockTransport."""
    echt = httpx.AsyncClient
    aufrufe: list[httpx.Request] = []

    def mit_mock(request: httpx.Request) -> httpx.Response:
        aufrufe.append(request)
        return handler(request)

    monkeypatch.setattr(la.httpx, "AsyncClient", lambda **kw: echt(transport=httpx.MockTransport(mit_mock), **kw))
    return aufrufe


def _liste(*namen, alias_status: int = 400):
    """GET /models liefert die Namen; die Probe-Anfrage für nicht gelistete Aliase antwortet mit alias_status."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            return httpx.Response(alias_status, json={"error": {"message": "Invalid model name"}})
        return httpx.Response(200, json={"data": [{"id": n, "object": "model"} for n in namen]})
    return handler


def _weg(request):
    raise httpx.ConnectError("keine Route", request=request)


# --- Zugang ----------------------------------------------------------------------------------------


def test_zugang_zeigt_nie_den_schluessel(cz):
    assert PROFIL_SCHLUESSEL not in repr(cz) and PROFIL_SCHLUESSEL not in str(cz)
    assert cz.kennung == USER[:8]


def test_zugang_regeln():
    with pytest.raises(ValueError):
        la.Zugang(USER, "claude")  # claude ohne Schlüssel
    with pytest.raises(ValueError):
        la.Zugang(USER, "haus", claude_schluessel=PROFIL_SCHLUESSEL)
    with pytest.raises(la.LLMNichtVerfuegbar):
        la.Zugang.haus(USER).anthropic()


def test_client_je_zugang_nie_geteilt():
    a = la.Zugang(USER, "claude", claude_schluessel=PROFIL_SCHLUESSEL)
    b = la.Zugang(USER, "claude", claude_schluessel="sk-ant-api03-PROFILzwei-0123456789abcdefghijkl")
    assert a.anthropic() is a.anthropic()
    assert a.anthropic() is not b.anthropic()
    assert a.anthropic().api_key == PROFIL_SCHLUESSEL and b.anthropic().api_key != PROFIL_SCHLUESSEL
    assert a != la.Zugang(USER, "claude", claude_schluessel=PROFIL_SCHLUESSEL)  # Gleichheit ist Identität


def test_workspace_header_nur_mit_workspace():
    mit = la.Zugang(USER, "claude", claude_schluessel=PROFIL_SCHLUESSEL, claude_workspace="wrkspc_abcdef12")
    assert mit.anthropic().default_headers.get("anthropic-workspace-id") == "wrkspc_abcdef12"
    ohne = la.Zugang(USER, "claude", claude_schluessel=PROFIL_SCHLUESSEL)
    assert "anthropic-workspace-id" not in ohne.anthropic().default_headers


def test_betreiber_zugang(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", PROFIL_SCHLUESSEL)
    assert la.Zugang.betreiber().art == "claude"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert la.Zugang.betreiber().art == "haus"


# --- Umschaltung -------------------------------------------------------------------------------


async def test_standard_ist_haiku(cz):
    wahl = await la.waehlen("gespraech", cz)
    assert (wahl.anbieter, wahl.modell, wahl.rueckfall) == ("claude", "claude-haiku-5-5", False)
    assert wahl.anzeige == "Claude Haiku 5.5"
    assert (await la.waehlen("vision", cz)).modell == "claude-haiku-5-5"  # auch das Bild: Sonnet kostet mehr


async def test_modell_des_profils_und_vision_model(monkeypatch, cz):
    monkeypatch.setenv("VISION_MODEL", "claude-sonnet-5-5")
    assert (await la.waehlen("vision", cz)).modell == "claude-sonnet-5-5"
    eigen = la.Zugang(USER, "claude", claude_schluessel=PROFIL_SCHLUESSEL, claude_modell="claude-sonnet-5-5")
    assert (await la.waehlen("gespraech", eigen)).modell == "claude-sonnet-5-5"


async def test_unbekannter_anbieter_wird_claude(monkeypatch, cz):
    monkeypatch.setenv("WAMO_LLM_LERNEN", "ollama")
    assert (await la.waehlen("lernen", cz)).anbieter == "claude"


async def test_claude_mit_fremdem_modellnamen_nimmt_standard(monkeypatch, cz):
    monkeypatch.setenv("WAMO_LLM_MODELL", "qwen3.6:35b")  # Anbieter steht auf claude (conftest)
    assert (await la.waehlen("gespraech", cz)).modell == "claude-haiku-5-5"
    monkeypatch.setenv("WAMO_LLM_MODELL", "claude-sonnet-5-5")
    assert (await la.waehlen("gespraech", cz)).modell == "claude-sonnet-5-5"


def test_unbekannter_baustein():
    with pytest.raises(ValueError):
        la.konfiguration("telefon")


def test_lernen_und_zusammenfassung_erben_das_gespraechsmodell(monkeypatch):
    _litellm(monkeypatch)
    monkeypatch.setenv("WAMO_LLM_LERNEN", "litellm")
    monkeypatch.setenv("WAMO_LLM_ZUSAMMENFASSUNG", "litellm")
    monkeypatch.setenv("WAMO_LLM_VISION", "litellm")
    assert la.konfiguration("lernen") == ("litellm", "qwen3.6:35b")
    assert la.konfiguration("zusammenfassung") == ("litellm", "qwen3.6:35b")
    assert la.konfiguration("vision") == ("litellm", None)  # Bildmodell nie stillschweigend erben


async def test_litellm_mit_angebotenem_modell(monkeypatch, cz):
    _litellm(monkeypatch)
    aufrufe = _gateway(monkeypatch, _liste("gemma4:31b", "qwen3.6:35b"))
    wahl = await la.waehlen("gespraech", cz)
    assert (wahl.anbieter, wahl.modell, wahl.rueckfall) == ("litellm", "qwen3.6:35b", False)
    assert wahl.anzeige == "qwen3.6:35b (LiteLLM)"
    assert aufrufe[0].url == httpx.URL(f"{BASIS}/models")
    assert aufrufe[0].headers["authorization"] == f"Bearer {SCHLUESSEL}"


async def test_platzhalter_route_zaehlt(monkeypatch, cz):
    _litellm(monkeypatch, modell="ollama/qwen3.6:35b")
    _gateway(monkeypatch, _liste("ollama/*"))
    assert (await la.waehlen("gespraech", cz)).anbieter == "litellm"


async def test_pruefung_wird_zwischengespeichert_und_laeuft_ab(monkeypatch, cz):
    _litellm(monkeypatch)
    aufrufe = _gateway(monkeypatch, _liste("qwen3.6:35b"))
    uhr = [1000.0]
    monkeypatch.setattr(la.time, "monotonic", lambda: uhr[0])
    await la.waehlen("gespraech", cz)
    await la.waehlen("gespraech", cz)
    assert len(aufrufe) == 1
    uhr[0] += la.PRUEF_TTL_SECS + 1
    await la.waehlen("gespraech", cz)
    assert len(aufrufe) == 2


# --- Hausmodelle: nie Claude ---------------------------------------------------------------------------


async def test_haus_profil_nimmt_immer_litellm(monkeypatch, hz):
    _hausmodelle(monkeypatch)
    _gateway(monkeypatch, _liste("gemma4-31b", alias_status=200))
    for baustein, modell in (("gespraech", "gemma4-31b"), ("lernen", "gemma4-31b"),
                             ("zusammenfassung", "gemma4-31b"), ("vision", "wamo-vision")):
        wahl = await la.waehlen(baustein, hz)
        assert (wahl.anbieter, wahl.modell) == ("litellm", modell), baustein


async def test_haus_profil_ohne_gateway_nie_claude(monkeypatch, hz, log):
    _hausmodelle(monkeypatch)
    _gateway(monkeypatch, _weg)
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.waehlen("gespraech", hz)
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.gespraech_llm("WAMO", hz)
    monkeypatch.setenv("WAMO_LLM_ANBIETER", "claude")  # Betriebsvorgabe für claude-Profile gilt hier nicht
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.waehlen("gespraech", hz)


async def test_haus_profil_ohne_schluessel_fuer_das_gateway(monkeypatch, hz):
    _hausmodelle(monkeypatch)
    monkeypatch.delenv("LITELLM_API_KEY")
    with pytest.raises(la.LLMNichtVerfuegbar, match="LITELLM_API_KEY fehlt"):
        await la.waehlen("lernen", hz)


async def test_haus_anfrage_faellt_nie_auf_claude(monkeypatch, hz):
    _hausmodelle(monkeypatch)
    _gateway(monkeypatch, _liste("gemma4-31b"))

    async def weg(wahl, auftrag, zugang):
        raise la.LLMFehler("LiteLLM: keine Verbindung", verbindung=True)

    async def claude(*args, **kwargs):
        raise AssertionError("haus-Profile dürfen Anthropic nie aufrufen")

    monkeypatch.setattr(la, "_litellm_anfrage", weg)
    monkeypatch.setattr(la, "_claude_anfrage", claude)
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.anfrage("lernen", zugang=hz, system="s", inhalt="i", max_tokens=10)


# --- Rückfälle für claude-Profile ------------------------------------------------------------------


async def test_fehlender_gateway_schluessel_faellt_auf_claude_zurueck(monkeypatch, cz, log):
    _litellm(monkeypatch, schluessel="")
    aufrufe = _gateway(monkeypatch, _liste("qwen3.6:35b"))
    wahl = await la.waehlen("gespraech", cz)
    assert (wahl.anbieter, wahl.gewuenscht, wahl.rueckfall) == ("claude", "litellm", True)
    assert wahl.grund == "LITELLM_API_KEY fehlt"
    assert wahl.anzeige == "Claude Haiku 5.5 (Rückfall)"
    assert aufrufe == []
    assert any("Rückfall auf Claude" in z and "LITELLM_API_KEY fehlt" in z for z in log)


@pytest.mark.parametrize("variable,grund", [
    ("LITELLM_BASE_URL", "LITELLM_BASE_URL fehlt"),
    ("WAMO_LLM_MODELL", "kein Modell gesetzt"),
])
async def test_fehlende_einstellung_faellt_zurueck(monkeypatch, cz, variable, grund):
    _litellm(monkeypatch)
    monkeypatch.delenv(variable)
    wahl = await la.waehlen("gespraech", cz)
    assert wahl.anbieter == "claude" and wahl.grund == grund


async def test_adresse_ohne_http_faellt_zurueck(monkeypatch, cz):
    _litellm(monkeypatch)
    monkeypatch.setenv("LITELLM_BASE_URL", "192.168.178.75:4000/v1")
    wahl = await la.waehlen("gespraech", cz)
    assert wahl.anbieter == "claude" and "http" in wahl.grund


async def test_gateway_nicht_erreichbar_gilt_fuer_alle_modelle(monkeypatch, cz):
    _litellm(monkeypatch)
    aufrufe = _gateway(monkeypatch, _weg)
    wahl = await la.waehlen("gespraech", cz)
    assert wahl.anbieter == "claude" and wahl.grund == "Gateway nicht erreichbar (ConnectError)"
    _litellm(monkeypatch, modell="gemma4:31b", baustein="lernen")
    assert (await la.waehlen("lernen", cz)).anbieter == "claude"
    assert len(aufrufe) == 1  # das zweite Modell wartet nicht erneut auf das tote Gateway


@pytest.mark.parametrize("antwort,grund", [
    (httpx.Response(500, text="kaputt"), "Gateway antwortet mit HTTP 500"),
    (httpx.Response(200, text="<html>"), "Modellliste des Gateways nicht lesbar (JSONDecodeError)"),
    (httpx.Response(200, json=["kein", "objekt"]), "Modellliste des Gateways nicht lesbar (AttributeError)"),
])
async def test_gateway_antwort_unbrauchbar(monkeypatch, cz, antwort, grund):
    _litellm(monkeypatch)
    _gateway(monkeypatch, lambda request: antwort)
    assert (await la.waehlen("gespraech", cz)).grund == grund


async def test_modell_nicht_angeboten_faellt_zurueck(monkeypatch, cz):
    _litellm(monkeypatch, modell="llama-unbekannt")
    aufrufe = _gateway(monkeypatch, _liste("qwen3.6:35b"))
    wahl = await la.waehlen("gespraech", cz)
    assert wahl.anbieter == "claude" and "llama-unbekannt" in wahl.grund
    probe = [a for a in aufrufe if a.method == "POST"]
    assert len(probe) == 1 and b'"max_tokens":1' in probe[0].content.replace(b" ", b"")


async def test_gruppen_alias_ohne_eintrag_in_models(monkeypatch, cz):
    """Aliase wie wamo-vision fehlen in /models; die Probe mit einem Token bestätigt sie."""
    _litellm(monkeypatch, modell="wamo-allgemein")
    aufrufe = _gateway(monkeypatch, _liste("qwen3.6-35b", alias_status=200))
    wahl = await la.waehlen("gespraech", cz)
    assert (wahl.anbieter, wahl.modell) == ("litellm", "wamo-allgemein")
    await la.waehlen("gespraech", cz)
    assert len(aufrufe) == 2  # Liste und Probe, beim zweiten Mal aus dem Zwischenspeicher


@pytest.mark.parametrize("status,grund", [
    (401, "Gateway lehnt den Schlüssel ab (HTTP 401)"),
    (503, "Gateway antwortet mit HTTP 503"),
])
async def test_alias_probe_fehler(monkeypatch, cz, status, grund):
    _litellm(monkeypatch, modell="wamo-allgemein")
    _gateway(monkeypatch, _liste("qwen3.6-35b", alias_status=status))
    assert (await la.waehlen("gespraech", cz)).grund == grund


async def test_abgelehnter_schluessel_ohne_geheimnis_in_meldung(monkeypatch, cz, log):
    _litellm(monkeypatch)
    _gateway(monkeypatch, lambda request: httpx.Response(401, json={"error": "invalid key " + SCHLUESSEL}))
    wahl = await la.waehlen("gespraech", cz)
    assert wahl.anbieter == "claude"
    assert wahl.grund == "Gateway lehnt den Schlüssel ab (HTTP 401)"
    assert log and not any(SCHLUESSEL in z for z in log)


async def test_rueckfall_aus_bricht_ab(monkeypatch, cz):
    _litellm(monkeypatch, schluessel="")
    monkeypatch.setenv("WAMO_LLM_RUECKFALL", "aus")
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.waehlen("gespraech", cz)


async def test_rueckfall_je_baustein(monkeypatch, cz):
    monkeypatch.setenv("WAMO_LLM_RUECKFALL", "gespraech, zusammenfassung")
    assert la.rueckfall_erlaubt("gespraech") and la.rueckfall_erlaubt("zusammenfassung")
    assert not la.rueckfall_erlaubt("vision") and not la.rueckfall_erlaubt("lernen")
    _litellm(monkeypatch, schluessel="", baustein="vision", modell="gemma4:31b")
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.waehlen("vision", cz)


async def test_claude_aus_faellt_auf_hausmodell_zurueck(monkeypatch, cz, log):
    """claude-Profil: Claude nicht nutzbar (Schlüssel abgelehnt oder Ausfall) -> Hausmodell mit Hinweis."""
    _hausmodelle(monkeypatch)
    _gateway(monkeypatch, _liste("gemma4-31b"))

    async def problem(zugang):
        return "Claude nicht erreichbar"

    monkeypatch.setattr(la, "claude_problem", problem)
    llm, wahl = await la.gespraech_llm("WAMO", cz)
    assert isinstance(llm, la.LiteLLMService)
    assert (wahl.anbieter, wahl.gewuenscht, wahl.grund) == ("litellm", "claude", "Claude nicht erreichbar")
    assert wahl.anzeige == "gemma4-31b (Rückfall)"
    assert any("Rückfall auf das Hausmodell" in z for z in log)


async def test_claude_pruefung_mit_zwischenspeicher(monkeypatch, cz):
    aufrufe = []

    class Modelle:
        async def list(self, **kw):
            aufrufe.append(kw)
            raise anthropic.APIConnectionError(request=httpx.Request("GET", "https://api.anthropic.test.invalid"))

    class Client:
        def with_options(self, **kw):
            return SimpleNamespace(models=Modelle())

    monkeypatch.setattr(la.Zugang, "anthropic", lambda self: Client())
    assert await la.claude_problem(cz) == "Claude nicht erreichbar"
    assert await la.claude_problem(cz) == "Claude nicht erreichbar"
    assert len(aufrufe) == 1


# --- Pipecat-Dienste ---------------------------------------------------------------------------


async def test_gespraech_llm_claude(monkeypatch, cz):
    async def ok(zugang):
        return None

    monkeypatch.setattr(la, "claude_problem", ok)
    llm, wahl = await la.gespraech_llm("Du bist WAMO.", cz)
    assert isinstance(llm, AnthropicLLMService) and isinstance(llm, la.ClaudeService)
    assert llm._settings.model == "claude-haiku-5-5"
    assert llm._client is cz.anthropic()  # der Client des Profils, kein gemeinsamer
    assert wahl.anbieter == "claude"


async def test_gespraech_llm_litellm(monkeypatch, cz):
    _litellm(monkeypatch)
    monkeypatch.setenv("WAMO_LLM_EXTRA", '{"reasoning_effort": "none"}')
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    llm, wahl = await la.gespraech_llm("Du bist WAMO.", cz)
    assert isinstance(llm, la.LiteLLMService)
    assert llm._settings.model == "qwen3.6:35b"
    assert llm._settings.extra == {"reasoning_effort": "none"}
    assert str(llm._client.base_url).rstrip("/") == BASIS
    assert llm.supports_developer_role is False
    assert llm._client.max_retries == 0
    assert llm._client.timeout.read == la.GESPRAECH_LESE_TIMEOUT_SECS
    assert llm._client.timeout.connect == la.VERBINDUNG_TIMEOUT_SECS


async def test_zusammenfassung(monkeypatch, cz, hz):
    async def ok(zugang):
        return None

    monkeypatch.setattr(la, "claude_problem", ok)
    assert await la.zusammenfassung_llm(cz) is None  # Claude fasst mit dem Gesprächsdienst zusammen
    _hausmodelle(monkeypatch)
    _gateway(monkeypatch, _liste("gemma4-31b"))
    # haus-Profil: immer ein eigener Dienst, auch ohne WAMO_LLM_ZUSAMMENFASSUNG, mit langer Lesezeit
    dienst = await la.zusammenfassung_llm(hz)
    assert isinstance(dienst, la.LiteLLMService) and dienst._settings.model == "gemma4-31b"
    assert dienst._client.timeout.read == la.ANFRAGE_TIMEOUT_SECS["zusammenfassung"]
    gespraech, _ = await la.gespraech_llm("WAMO", hz)
    assert gespraech._client.timeout.read == la.GESPRAECH_LESE_TIMEOUT_SECS  # das Gespräch bleibt kurz
    # claude-Profil mit Gespräch über das Hausmodell: Zusammenfassung nicht über den Gesprächsdienst
    monkeypatch.setenv("WAMO_LLM_ANBIETER", "litellm")
    dienst = await la.zusammenfassung_llm(cz)
    assert dienst is not None and dienst._wamo_baustein == "zusammenfassung"


async def test_verbrauch_je_profil_ohne_inhalt(monkeypatch, cz, log):
    async def ok(zugang):
        return None

    monkeypatch.setattr(la, "claude_problem", ok)
    llm, _ = await la.gespraech_llm("Geheimer Persona-Text", cz)
    await llm.start_llm_usage_metrics(LLMTokenUsage(prompt_tokens=120, completion_tokens=30, total_tokens=150,
                                                    cache_read_input_tokens=1000))
    zeile = next(z for z in log if "Verbrauch" in z)
    assert f"profil={USER[:8]}" in zeile and "ein=120" in zeile and "aus=30" in zeile and "cache_lesen=1000" in zeile
    assert "Geheimer" not in zeile and PROFIL_SCHLUESSEL not in zeile


@pytest.mark.parametrize("roh", ["kein json", "[1, 2]", '"text"'])
def test_extra_ungueltig_wird_ignoriert(monkeypatch, roh):
    monkeypatch.setenv("WAMO_LLM_EXTRA", roh)
    assert la.litellm_extra() == {}


def test_extra_unbekannte_parameter_wandern_in_extra_body(monkeypatch):
    monkeypatch.setenv("WAMO_LLM_EXTRA", '{"temperature": 0.2, "think": false, "extra_body": {"x": 1}}')
    assert la.litellm_extra() == {"temperature": 0.2, "extra_body": {"x": 1, "think": False}}


def _werkzeug_parameter(llm):
    context = LLMContext(
        messages=[{"role": "developer", "content": "Nachfrage"}, {"role": "user", "content": "Wie spät ist es?"}],
        tools=ToolsSchema(standard_tools=[ZEIT_SCHEMA, WEBSUCHE_SCHEMA]),
    )
    adapter = llm.get_llm_adapter()
    if isinstance(llm, la.LiteLLMService):
        return adapter.get_llm_invocation_params(context, system_instruction="WAMO", convert_developer_to_user=True)
    return adapter.get_llm_invocation_params(context, system_instruction="WAMO", enable_prompt_caching=True)


async def test_werkzeuge_im_openai_format_fuer_litellm(monkeypatch, cz):
    _litellm(monkeypatch)
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    llm, _ = await la.gespraech_llm("WAMO", cz)
    params = _werkzeug_parameter(llm)
    assert [t["function"]["name"] for t in params["tools"]] == ["aktuelle_zeit", "websuche"]
    assert params["tools"][1]["function"]["parameters"]["required"] == ["anfrage"]
    assert all(m["role"] != "developer" for m in params["messages"])  # developer wird zu user


async def test_werkzeuge_im_anthropic_format_fuer_claude(monkeypatch, cz):
    async def ok(zugang):
        return None

    monkeypatch.setattr(la, "claude_problem", ok)
    llm, _ = await la.gespraech_llm("WAMO", cz)
    params = _werkzeug_parameter(llm)
    assert [t["name"] for t in params["tools"]] == ["aktuelle_zeit", "websuche"]
    assert params["tools"][1]["input_schema"]["required"] == ["anfrage"]


# --- Schlanker Aufruf für Lernen und Vision ----------------------------------------------------


def _auftrag(**kw):
    werte = {"baustein": "lernen", "system": "s", "inhalt": "i", "max_tokens": 10, "json_schema": None,
             "schema_name": "antwort", "timeout": 5.0} | kw
    return la._Auftrag(**werte)


class _FalscheCompletions:
    def __init__(self, antwort=None, fehler=None):
        self.antwort, self.fehler, self.params = antwort, fehler, None

    async def create(self, **params):
        self.params = params
        if self.fehler:
            raise self.fehler
        return self.antwort


def _openai_antwort(text="ok", grund="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=grund, message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2))


def _mit_litellm_client(monkeypatch, completions):
    monkeypatch.setattr(la, "litellm_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=completions)))


async def test_litellm_anfrage_parameter_und_ende(monkeypatch, hz):
    monkeypatch.setenv("WAMO_LLM_EXTRA", '{"think": false}')
    c = _FalscheCompletions(_openai_antwort("  {\"a\": 1}  ", "length"))
    _mit_litellm_client(monkeypatch, c)
    wahl = la.Wahl("lernen", "litellm", "qwen3.6:35b", "litellm")
    antwort = await la._litellm_anfrage(wahl, _auftrag(json_schema={"type": "object"}, schema_name="eintraege",
                                                         inhalt=[la.Bild("QUJD"), "Frage"]), hz)
    assert antwort.text == '{"a": 1}' and antwort.ende == "zu_lang"
    assert c.params["model"] == "qwen3.6:35b" and c.params["extra_body"] == {"think": False}
    assert c.params["response_format"]["json_schema"]["name"] == "eintraege"
    assert c.params["messages"][0] == {"role": "system", "content": "s"}
    assert c.params["messages"][1]["content"][0]["type"] == "image_url"
    assert c.params["timeout"].read == 5.0


def _openai_status(code):
    request = httpx.Request("POST", f"{BASIS}/chat/completions")
    return openai.APIStatusError("x", response=httpx.Response(code, request=request), body=None)


@pytest.mark.parametrize("fehler,verbindung", [
    (_openai_status(500), True),
    (_openai_status(400), False),
    (openai.APIConnectionError(request=httpx.Request("POST", BASIS)), True),
    (openai.APITimeoutError(request=httpx.Request("POST", BASIS)), True),
    (TypeError("unerwartetes Argument"), False),
])
async def test_litellm_anfrage_fehler(monkeypatch, hz, fehler, verbindung):
    _mit_litellm_client(monkeypatch, _FalscheCompletions(fehler=fehler))
    with pytest.raises(la.LLMFehler) as info:
        await la._litellm_anfrage(la.Wahl("lernen", "litellm", "m", "litellm"), _auftrag(), hz)
    assert info.value.verbindung is verbindung


async def test_litellm_anfrage_leer(monkeypatch, hz):
    _mit_litellm_client(monkeypatch, _FalscheCompletions(SimpleNamespace(choices=[])))
    with pytest.raises(la.LLMFehler, match="leere Antwort"):
        await la._litellm_anfrage(la.Wahl("lernen", "litellm", "m", "litellm"), _auftrag(), hz)


class _FalscheMessages:
    def __init__(self, antwort=None, fehler=None):
        self.antwort, self.fehler, self.params, self.optionen = antwort, fehler, None, None

    async def create(self, **params):
        self.params = params
        if self.fehler:
            raise self.fehler
        return self.antwort


def _mit_claude(monkeypatch, messages):
    class Client:
        def with_options(self, **optionen):
            messages.optionen = optionen
            return SimpleNamespace(messages=messages)

    monkeypatch.setattr(la.Zugang, "anthropic", lambda self: Client())


async def test_claude_anfrage_parameter_und_ende(monkeypatch, cz, log):
    usage = SimpleNamespace(input_tokens=11, output_tokens=3, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    m = _FalscheMessages(SimpleNamespace(stop_reason="refusal", content=[SimpleNamespace(type="text", text=" nein ")],
                                         usage=usage))
    _mit_claude(monkeypatch, m)
    antwort = await la._claude_anfrage(la.Wahl("vision", "claude", "claude-haiku-5-5", "claude"),
                                       _auftrag(baustein="vision", json_schema={"type": "object"},
                                                inhalt=[la.Bild("QUJD"), "x"]), cz)
    assert antwort.ende == "abgelehnt" and antwort.text == "nein"
    schema = {"type": "json_schema", "schema": {"type": "object"}}
    assert m.params["output_config"] == {"effort": "low", "format": schema}
    assert m.params["messages"][0]["content"][0]["type"] == "image"
    assert m.optionen["max_retries"] == 0 and m.optionen["timeout"].read == 5.0
    assert any("Verbrauch" in z and "baustein=vision" in z and "ein=11" in z for z in log)


@pytest.mark.parametrize("fehler,text,verbindung", [
    (anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.test.invalid")),
     "keine Verbindung", True),
    (anthropic.AuthenticationError("x", response=httpx.Response(401, request=httpx.Request("POST", "https://a")),
                                   body=None), "Schlüssel abgelehnt", True),
    (anthropic.BadRequestError("x", response=httpx.Response(400, request=httpx.Request("POST", "https://a")),
                               body=None), "API-Fehler 400", False),
])
async def test_claude_anfrage_fehler(monkeypatch, cz, fehler, text, verbindung):
    _mit_claude(monkeypatch, _FalscheMessages(fehler=fehler))
    with pytest.raises(la.LLMFehler, match=text) as info:
        await la._claude_anfrage(la.Wahl("lernen", "claude", "claude-haiku-5-5", "claude"), _auftrag(), cz)
    assert info.value.verbindung is verbindung


async def test_anfrage_claude_aus_dann_hausmodell(monkeypatch, cz, log):
    _hausmodelle(monkeypatch)
    _gateway(monkeypatch, _liste("gemma4-31b"))
    gerufen = []

    async def claude_weg(wahl, auftrag, zugang):
        gerufen.append("claude")
        raise la.LLMFehler("Claude: keine Verbindung", verbindung=True)

    async def haus(wahl, auftrag, zugang):
        gerufen.append(wahl.modell)
        return la.Antwort('{"eintraege": []}', "fertig", wahl)

    monkeypatch.setattr(la, "_claude_anfrage", claude_weg)
    monkeypatch.setattr(la, "_litellm_anfrage", haus)
    antwort = await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert gerufen == ["claude", "gemma4-31b"]
    assert antwort.wahl.rueckfall and antwort.wahl.gewuenscht == "claude"
    assert any("einmal mit dem Hausmodell" in z for z in log)


def test_systemtext_folgt_der_tatsaechlichen_wahl():
    """Ein Systemtext als Funktion bekommt die Wahl, die wirklich antwortet (wichtig nach einem Rückfall)."""
    auftrag = _auftrag(system=lambda wahl: f"Einheit für {wahl.anbieter}")
    assert la._systemtext(auftrag, la.Wahl("vision", "litellm", "m", "claude")) == "Einheit für litellm"
    assert la._systemtext(_auftrag(), la.Wahl("vision", "claude", "m", "claude")) == "s"


async def test_anfrage_systemtext_nach_rueckfall(monkeypatch, cz):
    _hausmodelle(monkeypatch)
    _gateway(monkeypatch, _liste("gemma4-31b", "wamo-vision"))
    texte = []

    async def claude_weg(wahl, auftrag, zugang):
        texte.append(la._systemtext(auftrag, wahl))
        raise la.LLMFehler("Claude: keine Verbindung", verbindung=True)

    async def haus(wahl, auftrag, zugang):
        texte.append(la._systemtext(auftrag, wahl))
        return la.Antwort("{}", "fertig", wahl)

    monkeypatch.setattr(la, "_claude_anfrage", claude_weg)
    monkeypatch.setattr(la, "_litellm_anfrage", haus)
    await la.anfrage("vision", zugang=cz, system=lambda w: w.anbieter, inhalt="i", max_tokens=10)
    assert texte == ["claude", "litellm"]


async def test_anfrage_litellm_aus_dann_claude(monkeypatch, cz):
    _litellm(monkeypatch, baustein="lernen")
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    gerufen = []

    async def litellm_weg(wahl, auftrag, zugang):
        gerufen.append("litellm")
        raise la.LLMFehler("LiteLLM: keine Verbindung", verbindung=True)

    async def claude(wahl, auftrag, zugang):
        gerufen.append(wahl.anbieter)
        return la.Antwort('{"eintraege": []}', "fertig", wahl)

    monkeypatch.setattr(la, "_litellm_anfrage", litellm_weg)
    monkeypatch.setattr(la, "_claude_anfrage", claude)
    antwort = await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert gerufen == ["litellm", "claude"]
    assert antwort.wahl.rueckfall and antwort.wahl.grund == "LiteLLM: keine Verbindung"
    gerufen.clear()  # Der Ausfall ist vermerkt: der nächste Aufruf geht gleich zu Claude
    await la.anfrage("lernen", zugang=cz, system="s", inhalt="i", max_tokens=10)
    assert gerufen == ["claude"]


async def test_anfrage_ohne_rueckfall_bei_anderem_fehler(monkeypatch, cz):
    _litellm(monkeypatch, baustein="vision", modell="gemma4:31b")
    _gateway(monkeypatch, _liste("gemma4:31b"))

    async def abgelehnt(wahl, auftrag, zugang):
        raise la.LLMFehler("LiteLLM: HTTP 400")

    monkeypatch.setattr(la, "_litellm_anfrage", abgelehnt)
    with pytest.raises(la.LLMFehler, match="HTTP 400"):
        await la.anfrage("vision", zugang=cz, system="s", inhalt="i", max_tokens=10)


def test_vision_passt_in_die_werkzeug_zeitgrenze():
    # Bildschirm-Werkzeuge brechen nach 30 s ab (screen.py); erster Anbieter und Rückfall müssen hineinpassen
    assert 2 * la.ANFRAGE_TIMEOUT_SECS["vision"] < 30


# --- Logs ------------------------------------------------------------------------------------------


def test_log_schwaerzt_schluessel_und_ausgeblendete_namen(monkeypatch, log):
    monkeypatch.setenv("WAMO_LLM_AUSBLENDEN", "fremd")
    logger.info("Fehler mit {} und Modell fremd-chat", PROFIL_SCHLUESSEL)
    logger.warning(f"direkt eingesetzt: {PROFIL_SCHLUESSEL[:20]}")
    text = "\n".join(log)
    assert PROFIL_SCHLUESSEL not in text and PROFIL_SCHLUESSEL[:20] not in text
    assert "fremd-chat" not in text and "sk-ant-…" in text


def test_ausblenden_ohne_einstellung_laesst_alles(monkeypatch):
    monkeypatch.delenv("WAMO_LLM_AUSBLENDEN", raising=False)
    assert la.sichtbare_modelle(["a", "fremd-chat"]) == ["a", "fremd-chat"]
    assert la.ausblenden_text("fremd-chat") == "fremd-chat"


def test_ausblenden_von_namen_und_texten(monkeypatch):
    monkeypatch.setenv("WAMO_LLM_AUSBLENDEN", "fremd, anderes")
    assert la.sichtbare_modelle(["wamo-allgemein", "Fremd-Chat", "qwen3.6-35b", "x-anderes-y"]) == [
        "wamo-allgemein", "qwen3.6-35b"]
    assert la.ausgeblendet("FREMD") and not la.ausgeblendet("wamo-vision")
    text = "Invalid model. Available: ['fremd-chat', 'qwen3.6-35b', ollama/FREMD:latest] und anderes"
    sauber = la.ausblenden_text(text)
    assert "fremd" not in sauber.lower() and "anderes" not in sauber and "qwen3.6-35b" in sauber


def test_bild_inhalt_je_anbieter():
    inhalt = [la.Bild("QUJD"), "Was ist zu sehen?"]
    claude = la._claude_inhalt(inhalt)
    openai_teile = la._openai_inhalt(inhalt)
    assert claude[0]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}
    assert openai_teile[0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}
    assert claude[1] == openai_teile[1] == {"type": "text", "text": "Was ist zu sehen?"}


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', '```\n{"a": 1}\n```'])
def test_json_aus_text(text):
    assert la.json_aus_text(text) == {"a": 1}


def test_lesbarer_modellname():
    assert la.lesbarer_modellname("claude-haiku-5-5") == "Claude Haiku 5.5"
    assert la.lesbarer_modellname("claude-sonnet-4-5-20250929") == "Claude Sonnet 4.5"
    assert la.lesbarer_modellname("qwen3.6:35b") == "qwen3.6:35b"
