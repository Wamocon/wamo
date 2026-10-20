"""Gemeinsame Test-Einstellungen für voice-core.

Sicherheitsnetz: Kein Test darf einen echten Entwicklungsauftrag auf Sokrates-2 starten. Standard ist die
Betriebsart sokrates2; ein Test, der versehentlich ohne gemocktes SSH bis zum Aufruf kommt, landet so bei
einem Ziel, das es nicht gibt (.invalid), statt beim Dispatcher. Tests, die SSH mocken, setzen ihr Ziel selbst.
"""

import pytest


@pytest.fixture(autouse=True)
def _kein_echtes_sokrates2(monkeypatch):
    monkeypatch.setenv("WAMO_ENTWICKLUNG_SSH_ZIEL", "wamo@sokrates2.test.invalid")
