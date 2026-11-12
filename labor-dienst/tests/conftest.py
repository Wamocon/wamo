"""Gemeinsame Fixtures. Die Integrationstests brauchen scripts/ (Abnahmeskript) und voice-core/ (Dienstclient) auf dem Suchpfad."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2]
for _ordner in (WURZEL / "scripts", WURZEL / "voice-core"):
    if str(_ordner) not in sys.path:
        sys.path.append(str(_ordner))

from hilfen import BETREUER_TOKEN, KERN_TOKEN, Uhr  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from wamo_labor_dienst.app import create_app  # noqa: E402
from wamo_labor_dienst.datenbank import PlatzDB, ZentralDB  # noqa: E402
from wamo_labor_dienst.konfig import Konfig  # noqa: E402


@pytest.fixture
def uhr() -> Uhr:
    return Uhr()


@pytest.fixture
def db(tmp_path, uhr) -> PlatzDB:
    d = PlatzDB(3, tmp_path / "platz-03.sqlite3", 16 * 1024 * 1024, uhr)
    d.anlegen()
    d.neuer_durchlauf(1)
    return d


@pytest.fixture
def zentral(tmp_path) -> ZentralDB:
    z = ZentralDB(tmp_path / "zentral.sqlite3")
    z.anlegen()
    return z


@pytest.fixture
def tokens() -> dict[str, str]:
    return {"kern": KERN_TOKEN, "betreuer": BETREUER_TOKEN}


@pytest.fixture
def konfig(tmp_path) -> Konfig:
    return Konfig(daten=tmp_path / "daten", kern_token=KERN_TOKEN, betreuer_token=BETREUER_TOKEN, host="127.0.0.1", port=0, bis=date(2026, 12, 11), platz_mb=16)


@pytest.fixture
def app(konfig, uhr):
    return create_app(konfig, uhr=uhr)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        yield c
