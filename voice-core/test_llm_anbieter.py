"""Tests der Anbieter-Fabrik (llm_anbieter.py): Umschaltung, Rückfall auf Claude, fehlender Schlüssel,
Gateway-Prüfung, Zeitgrenzen und Werkzeugformat für beide Anbieter. Kein Test spricht mit dem echten
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
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.anthropic.llm import AnthropicLLMService
from tools import WEBSUCHE_SCHEMA, ZEIT_SCHEMA

SCHLUESSEL = "sk-test-geheim-4711"
BASIS = "http://gateway.test.invalid:4000/v1"


@pytest.fixture(autouse=True)
def _sauber(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL_FAST", "claude-haiku-5-5")
    monkeypatch.delenv("VISION_MODEL", raising=False)


@pytest.fixture
def log():
    """Alles, was loguru in diesem Test schreibt (caplog sieht loguru nicht)."""
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


def _gateway(monkeypatch, handler):
    """Ersetzt das Netz für litellm_modelle durch einen MockTransport."""
    echt = httpx.AsyncClient
    aufrufe: list[httpx.Request] = []

    def mit_mock(request: httpx.Request) -> httpx.Response:
        aufrufe.append(request)
        return handler(request)

    monkeypatch.setattr(la.httpx, "AsyncClient", lambda **kw: echt(transport=httpx.MockTransport(mit_mock), **kw))
    return aufrufe


def _liste(*namen):
    return lambda request: httpx.Response(200, json={"data": [{"id": n, "object": "model"} for n in namen]})


# --- Umschaltung -------------------------------------------------------------------------------


async def test_standard_ist_claude():
    wahl = await la.waehlen("gespraech")
    assert (wahl.anbieter, wahl.modell, wahl.rueckfall) == ("claude", "claude-haiku-5-5", False)
    assert wahl.anzeige == "Claude Haiku 5.5"


async def test_vision_nimmt_vision_model(monkeypatch):
    assert (await la.waehlen("vision")).modell == "claude-sonnet-5-5"
    monkeypatch.setenv("VISION_MODEL", "claude-opus-5-5")
    assert (await la.waehlen("vision")).modell == "claude-opus-5-5"


async def test_unbekannter_anbieter_wird_claude(monkeypatch):
    monkeypatch.setenv("WAMO_LLM_LERNEN", "ollama")
    assert (await la.waehlen("lernen")).anbieter == "claude"


async def test_claude_mit_fremdem_modellnamen_nimmt_standard(monkeypatch):
    monkeypatch.setenv("WAMO_LLM_MODELL", "qwen3.6:35b")  # Anbieter steht auf claude (conftest)
    assert (await la.waehlen("gespraech")).modell == "claude-haiku-5-5"
    monkeypatch.setenv("WAMO_LLM_MODELL", "claude-sonnet-5-5")
    assert (await la.waehlen("gespraech")).modell == "claude-sonnet-5-5"


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


async def test_litellm_mit_angebotenem_modell(monkeypatch):
    _litellm(monkeypatch)
    aufrufe = _gateway(monkeypatch, _liste("gemma4:31b", "qwen3.6:35b"))
    wahl = await la.waehlen("gespraech")
    assert (wahl.anbieter, wahl.modell, wahl.rueckfall) == ("litellm", "qwen3.6:35b", False)
    assert wahl.anzeige == "qwen3.6:35b (LiteLLM)"
    assert aufrufe[0].url == httpx.URL(f"{BASIS}/models")
    assert aufrufe[0].headers["authorization"] == f"Bearer {SCHLUESSEL}"


async def test_platzhalter_route_zaehlt(monkeypatch):
    _litellm(monkeypatch, modell="ollama/qwen3.6:35b")
    _gateway(monkeypatch, _liste("ollama/*"))
    assert (await la.waehlen("gespraech")).anbieter == "litellm"


async def test_pruefung_wird_zwischengespeichert_und_laeuft_ab(monkeypatch):
    _litellm(monkeypatch)
    aufrufe = _gateway(monkeypatch, _liste("qwen3.6:35b"))
    uhr = [1000.0]
    monkeypatch.setattr(la.time, "monotonic", lambda: uhr[0])
    await la.waehlen("gespraech")
    await la.waehlen("gespraech")
    assert len(aufrufe) == 1
    uhr[0] += la.PRUEF_TTL_SECS + 1
    await la.waehlen("gespraech")
    assert len(aufrufe) == 2


# --- Rückfall ----------------------------------------------------------------------------------


async def test_fehlender_schluessel_faellt_auf_claude_zurueck(monkeypatch, log):
    _litellm(monkeypatch, schluessel="")
    aufrufe = _gateway(monkeypatch, _liste("qwen3.6:35b"))
    wahl = await la.waehlen("gespraech")
    assert (wahl.anbieter, wahl.gewuenscht, wahl.rueckfall) == ("claude", "litellm", True)
    assert wahl.grund == "LITELLM_API_KEY fehlt"
    assert wahl.anzeige == "Claude Haiku 5.5 (Rückfall)"
    assert aufrufe == []  # ohne Schlüssel keine Anfrage ans Gateway
    assert any("Rückfall auf Claude" in z and "LITELLM_API_KEY fehlt" in z for z in log)  # klare Meldung


@pytest.mark.parametrize("variable,grund", [
    ("LITELLM_BASE_URL", "LITELLM_BASE_URL fehlt"),
    ("WAMO_LLM_MODELL", "kein Modell gesetzt"),
])
async def test_fehlende_einstellung_faellt_zurueck(monkeypatch, variable, grund):
    _litellm(monkeypatch)
    monkeypatch.delenv(variable)
    wahl = await la.waehlen("gespraech")
    assert wahl.anbieter == "claude" and wahl.grund == grund


async def test_adresse_ohne_http_faellt_zurueck(monkeypatch):
    _litellm(monkeypatch)
    monkeypatch.setenv("LITELLM_BASE_URL", "192.168.178.75:4000/v1")
    wahl = await la.waehlen("gespraech")
    assert wahl.anbieter == "claude" and "http" in wahl.grund


async def test_gateway_nicht_erreichbar_gilt_fuer_alle_modelle(monkeypatch):
    _litellm(monkeypatch)

    def weg(request):
        raise httpx.ConnectError("keine Route", request=request)

    aufrufe = _gateway(monkeypatch, weg)
    wahl = await la.waehlen("gespraech")
    assert wahl.anbieter == "claude"
    assert wahl.grund == "Gateway nicht erreichbar (ConnectError)"
    _litellm(monkeypatch, modell="gemma4:31b", baustein="lernen")
    assert (await la.waehlen("lernen")).anbieter == "claude"
    assert len(aufrufe) == 1  # das zweite Modell wartet nicht erneut auf das tote Gateway


@pytest.mark.parametrize("antwort,grund", [
    (httpx.Response(500, text="kaputt"), "Gateway antwortet mit HTTP 500"),
    (httpx.Response(200, text="<html>"), "Modellliste des Gateways nicht lesbar (JSONDecodeError)"),
    (httpx.Response(200, json=["kein", "objekt"]), "Modellliste des Gateways nicht lesbar (AttributeError)"),
])
async def test_gateway_antwort_unbrauchbar(monkeypatch, antwort, grund):
    _litellm(monkeypatch)
    _gateway(monkeypatch, lambda request: antwort)
    assert (await la.waehlen("gespraech")).grund == grund


async def test_modell_nicht_angeboten_faellt_zurueck(monkeypatch):
    _litellm(monkeypatch, modell="llama-unbekannt")
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    wahl = await la.waehlen("gespraech")
    assert wahl.anbieter == "claude" and "llama-unbekannt" in wahl.grund


async def test_abgelehnter_schluessel_ohne_geheimnis_in_meldung(monkeypatch, log):
    _litellm(monkeypatch)
    _gateway(monkeypatch, lambda request: httpx.Response(401, json={"error": "invalid key " + SCHLUESSEL}))
    wahl = await la.waehlen("gespraech")
    assert wahl.anbieter == "claude"
    assert wahl.grund == "Gateway lehnt den Schlüssel ab (HTTP 401)"
    assert log and not any(SCHLUESSEL in z for z in log)


async def test_rueckfall_aus_bricht_ab(monkeypatch):
    _litellm(monkeypatch, schluessel="")
    monkeypatch.setenv("WAMO_LLM_RUECKFALL", "aus")
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.waehlen("gespraech")


async def test_rueckfall_je_baustein(monkeypatch):
    monkeypatch.setenv("WAMO_LLM_RUECKFALL", "gespraech, zusammenfassung")
    assert la.rueckfall_erlaubt("gespraech") and la.rueckfall_erlaubt("zusammenfassung")
    assert not la.rueckfall_erlaubt("vision") and not la.rueckfall_erlaubt("lernen")
    _litellm(monkeypatch, schluessel="", baustein="vision", modell="gemma4:31b")
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.waehlen("vision")


# --- Pipecat-Dienste ---------------------------------------------------------------------------


async def test_gespraech_llm_claude():
    llm, wahl = await la.gespraech_llm("Du bist WAMO.")
    assert isinstance(llm, AnthropicLLMService)
    assert llm._settings.model == "claude-haiku-5-5"
    assert wahl.anbieter == "claude"


async def test_gespraech_llm_litellm(monkeypatch):
    _litellm(monkeypatch)
    monkeypatch.setenv("WAMO_LLM_EXTRA", '{"reasoning_effort": "none"}')
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    llm, wahl = await la.gespraech_llm("Du bist WAMO.")
    assert isinstance(llm, la.LiteLLMService)
    assert llm._settings.model == "qwen3.6:35b"
    assert llm._settings.extra == {"reasoning_effort": "none"}
    assert str(llm._client.base_url).rstrip("/") == BASIS
    assert llm.supports_developer_role is False
    # Kurze Zeitgrenzen, keine stillen Wiederholungen (SDK-Standard wären 600 s und 2 Versuche)
    assert llm._client.max_retries == 0
    assert llm._client.timeout.read == la.GESPRAECH_LESE_TIMEOUT_SECS
    assert llm._client.timeout.connect == la.VERBINDUNG_TIMEOUT_SECS


async def test_gespraech_llm_rueckfall_liefert_claude_dienst(monkeypatch):
    _litellm(monkeypatch, schluessel="")
    llm, wahl = await la.gespraech_llm("Du bist WAMO.")
    assert isinstance(llm, AnthropicLLMService) and wahl.rueckfall


async def test_zusammenfassung(monkeypatch):
    assert await la.zusammenfassung_llm() is None
    monkeypatch.setenv("WAMO_LLM_ZUSAMMENFASSUNG", "claude")
    assert isinstance(await la.zusammenfassung_llm(), AnthropicLLMService)
    _litellm(monkeypatch)
    monkeypatch.setenv("WAMO_LLM_ZUSAMMENFASSUNG", "litellm")
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    dienst = await la.zusammenfassung_llm()
    assert isinstance(dienst, la.LiteLLMService) and dienst._settings.model == "qwen3.6:35b"


@pytest.mark.parametrize("roh", ["kein json", "[1, 2]", '"text"'])
def test_extra_ungueltig_wird_ignoriert(monkeypatch, roh):
    monkeypatch.setenv("WAMO_LLM_EXTRA", roh)
    assert la.litellm_extra() == {}


def test_extra_unbekannte_parameter_wandern_in_extra_body(monkeypatch):
    monkeypatch.setenv("WAMO_LLM_EXTRA", '{"temperature": 0.2, "chat_template_kwargs": {"enable_thinking": false},'
                                         ' "extra_body": {"think": false}}')
    assert la.litellm_extra() == {
        "temperature": 0.2,
        "extra_body": {"think": False, "chat_template_kwargs": {"enable_thinking": False}},
    }


def _werkzeug_parameter(llm):
    context = LLMContext(
        messages=[{"role": "developer", "content": "Nachfrage"}, {"role": "user", "content": "Wie spät ist es?"}],
        tools=ToolsSchema(standard_tools=[ZEIT_SCHEMA, WEBSUCHE_SCHEMA]),
    )
    adapter = llm.get_llm_adapter()
    if isinstance(llm, la.LiteLLMService):
        return adapter.get_llm_invocation_params(context, system_instruction="WAMO", convert_developer_to_user=True)
    return adapter.get_llm_invocation_params(context, system_instruction="WAMO", enable_prompt_caching=True)


async def test_werkzeuge_im_openai_format_fuer_litellm(monkeypatch):
    _litellm(monkeypatch)
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    llm, _ = await la.gespraech_llm("WAMO")
    params = _werkzeug_parameter(llm)
    assert [t["function"]["name"] for t in params["tools"]] == ["aktuelle_zeit", "websuche"]
    assert params["tools"][1]["function"]["parameters"]["required"] == ["anfrage"]
    assert all(m["role"] != "developer" for m in params["messages"])  # developer wird zu user


async def test_werkzeuge_im_anthropic_format_fuer_claude():
    llm, _ = await la.gespraech_llm("WAMO")
    params = _werkzeug_parameter(llm)
    assert [t["name"] for t in params["tools"]] == ["aktuelle_zeit", "websuche"]
    assert params["tools"][1]["input_schema"]["required"] == ["anfrage"]


# --- Schlanker Aufruf für Lernen und Vision ----------------------------------------------------


def _auftrag(**kw):
    werte = {"system": "s", "inhalt": "i", "max_tokens": 10, "json_schema": None, "schema_name": "antwort",
             "timeout": 5.0} | kw
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
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=grund, message=SimpleNamespace(content=text))])


def _mit_litellm_client(monkeypatch, completions):
    monkeypatch.setattr(la, "litellm_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=completions)))


async def test_litellm_anfrage_parameter_und_ende(monkeypatch):
    monkeypatch.setenv("WAMO_LLM_EXTRA", '{"reasoning_effort": "none"}')
    c = _FalscheCompletions(_openai_antwort("  {\"a\": 1}  ", "length"))
    _mit_litellm_client(monkeypatch, c)
    wahl = la.Wahl("lernen", "litellm", "qwen3.6:35b", "litellm")
    antwort = await la._litellm_anfrage(wahl, _auftrag(json_schema={"type": "object"}, schema_name="eintraege",
                                                         inhalt=[la.Bild("QUJD"), "Frage"]))
    assert antwort.text == '{"a": 1}' and antwort.ende == "zu_lang"
    assert c.params["model"] == "qwen3.6:35b" and c.params["reasoning_effort"] == "none"
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
async def test_litellm_anfrage_fehler(monkeypatch, fehler, verbindung):
    _mit_litellm_client(monkeypatch, _FalscheCompletions(fehler=fehler))
    with pytest.raises(la.LLMFehler) as info:
        await la._litellm_anfrage(la.Wahl("lernen", "litellm", "m", "litellm"), _auftrag())
    assert info.value.verbindung is verbindung


async def test_litellm_anfrage_leer(monkeypatch):
    _mit_litellm_client(monkeypatch, _FalscheCompletions(SimpleNamespace(choices=[])))
    with pytest.raises(la.LLMFehler, match="leere Antwort"):
        await la._litellm_anfrage(la.Wahl("lernen", "litellm", "m", "litellm"), _auftrag())


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

    monkeypatch.setattr(la, "anthropic_client", lambda: Client())


async def test_claude_anfrage_parameter_und_ende(monkeypatch):
    m = _FalscheMessages(SimpleNamespace(stop_reason="refusal", content=[SimpleNamespace(type="text", text=" nein ")]))
    _mit_claude(monkeypatch, m)
    antwort = await la._claude_anfrage(la.Wahl("vision", "claude", "claude-sonnet-5-5", "claude"),
                                       _auftrag(json_schema={"type": "object"}, inhalt=[la.Bild("QUJD"), "x"]))
    assert antwort.ende == "abgelehnt" and antwort.text == "nein"
    schema = {"type": "json_schema", "schema": {"type": "object"}}
    assert m.params["output_config"] == {"effort": "low", "format": schema}
    assert m.params["messages"][0]["content"][0]["type"] == "image"
    assert m.optionen["max_retries"] == 0 and m.optionen["timeout"].read == 5.0


async def test_claude_anfrage_fehler(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.test.invalid")
    _mit_claude(monkeypatch, _FalscheMessages(fehler=anthropic.APIConnectionError(request=request)))
    with pytest.raises(la.LLMFehler, match="keine Verbindung"):
        await la._claude_anfrage(la.Wahl("lernen", "claude", "claude-haiku-5-5", "claude"), _auftrag())


async def test_anfrage_faellt_bei_verbindungsfehler_auf_claude_zurueck(monkeypatch):
    _litellm(monkeypatch, baustein="lernen")
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    gerufen = []

    async def litellm_weg(wahl, auftrag):
        gerufen.append("litellm")
        raise la.LLMFehler("LiteLLM: keine Verbindung", verbindung=True)

    async def claude(wahl, auftrag):
        gerufen.append(wahl.anbieter)
        return la.Antwort('{"eintraege": []}', "fertig", wahl)

    monkeypatch.setattr(la, "_litellm_anfrage", litellm_weg)
    monkeypatch.setattr(la, "_claude_anfrage", claude)
    antwort = await la.anfrage("lernen", system="s", inhalt="i", max_tokens=10)
    assert gerufen == ["litellm", "claude"]
    assert antwort.wahl.rueckfall and antwort.wahl.grund == "LiteLLM: keine Verbindung"
    gerufen.clear()  # Der Ausfall ist vermerkt: der nächste Aufruf geht gleich zu Claude
    await la.anfrage("lernen", system="s", inhalt="i", max_tokens=10)
    assert gerufen == ["claude"]


async def test_anfrage_ohne_rueckfall_bei_anderem_fehler(monkeypatch):
    _litellm(monkeypatch, baustein="vision", modell="gemma4:31b")
    _gateway(monkeypatch, _liste("gemma4:31b"))

    async def abgelehnt(wahl, auftrag):
        raise la.LLMFehler("LiteLLM: HTTP 400")

    monkeypatch.setattr(la, "_litellm_anfrage", abgelehnt)
    with pytest.raises(la.LLMFehler, match="HTTP 400"):
        await la.anfrage("vision", system="s", inhalt="i", max_tokens=10)


async def test_anfrage_rueckfall_aus_nach_laufzeitfehler(monkeypatch):
    _litellm(monkeypatch, baustein="lernen")
    _gateway(monkeypatch, _liste("qwen3.6:35b"))
    monkeypatch.setenv("WAMO_LLM_RUECKFALL", "aus")

    async def weg(wahl, auftrag):
        raise la.LLMFehler("LiteLLM: keine Verbindung", verbindung=True)

    async def claude(wahl, auftrag):
        raise AssertionError("ohne Rückfall darf nichts an Claude gehen")

    monkeypatch.setattr(la, "_litellm_anfrage", weg)
    monkeypatch.setattr(la, "_claude_anfrage", claude)
    with pytest.raises(la.LLMNichtVerfuegbar):
        await la.anfrage("lernen", system="s", inhalt="i", max_tokens=10)


def test_vision_passt_in_die_werkzeug_zeitgrenze():
    # Bildschirm-Werkzeuge brechen nach 30 s ab (screen.py); LiteLLM und Claude-Rückfall müssen hineinpassen
    assert 2 * la.ANFRAGE_TIMEOUT_SECS["vision"] < 30


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
