"""Gemeinsame Test-Einstellungen für voice-core.

Sicherheitsnetz: Kein Test darf einen echten Entwicklungsauftrag auf Sokrates-2 starten. Standard ist die
Betriebsart sokrates2; ein Test, der versehentlich ohne gemocktes SSH bis zum Aufruf kommt, landet so bei
einem Ziel, das es nicht gibt (.invalid), statt beim Dispatcher. Tests, die SSH mocken, setzen ihr Ziel selbst.

Ebenso spricht kein Test mit dem LiteLLM-Gateway, mit SearXNG oder mit Anthropic: alle Bausteine stehen
auf claude, die Suche auf anthropic, und Gateway, SearXNG und Anthropic-API zeigen auf Adressen, die es
nicht gibt. Kein Profil hat einen Schlüssel oder eine Zuordnung, es sei denn, ein Test setzt sie. Tests der Fabrik
(test_llm_anbieter.py) setzen ihre Werte selbst und mocken das Netz.
"""

import pytest


@pytest.fixture(autouse=True)
def _kein_echtes_sokrates2(monkeypatch):
    monkeypatch.setenv("WAMO_ENTWICKLUNG_SSH_ZIEL", "wamo@sokrates2.test.invalid")


@pytest.fixture(autouse=True)
def _kein_echtes_gateway(monkeypatch):
    for name in ("WAMO_LLM_ANBIETER", "WAMO_LLM_LERNEN", "WAMO_LLM_VISION"):
        monkeypatch.setenv(name, "claude")
    for name in ("WAMO_LLM_ZUSAMMENFASSUNG", "WAMO_LLM_MODELL", "WAMO_LLM_LERNEN_MODELL", "WAMO_LLM_VISION_MODELL",
                 "WAMO_LLM_ZUSAMMENFASSUNG_MODELL", "WAMO_LLM_EXTRA", "WAMO_LLM_RUECKFALL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WAMO_SUCHE", "anthropic")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.test.invalid:4000/v1")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.setenv("WAMO_SEARXNG_URL", "http://searxng.test.invalid:8080")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://anthropic.test.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-anthropic")
    monkeypatch.delenv("WAMO_CLAUDE_SCHLUESSEL_PROFIL", raising=False)
    monkeypatch.delenv("WAMO_SCHLUESSEL_KEK", raising=False)
    monkeypatch.delenv("WAMO_SCHLUESSEL_KEK_ALT", raising=False)
    # Der Boss prüft vor dem Gespräch mit einer kleinen Anfrage, ob ein Hausmodell jetzt schnell genug antwortet:
    # in Tests aus (test_boss.py schaltet sie gezielt ein und ersetzt das Netz)
    monkeypatch.setenv("WAMO_BOSS_PROBE", "aus")
    import boss
    import llm_anbieter
    import modellzugang

    llm_anbieter._cache_leeren()
    llm_anbieter._claude_cache.clear()
    llm_anbieter._haus_pause.clear()
    modellzugang._versuche.clear()
    boss._probe_cache_leeren()
    yield
    llm_anbieter._cache_leeren()
    llm_anbieter._claude_cache.clear()
    llm_anbieter._haus_pause.clear()
    boss._probe_cache_leeren()
