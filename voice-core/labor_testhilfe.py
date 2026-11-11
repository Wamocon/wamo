"""Testhilfe für Laborsitzungen im ganzen Kern. NUR FÜR TESTS: kein Produktionsmodul importiert sie (wie labor_attrappe.py).

Im Betrieb sprechen zwei Teile des Kerns mit demselben Dienst der Kollegen: die Zugangskontrolle und die Wache (labor_durchlauf.py, Durchlauf und
Einwilligung) und die Werkzeuge (labor_werkzeuge.py, Tickets und Freigabe). Beide holen ihren Client bei labor_client.client_aus_umgebung. Hier steht
die Attrappe (labor_attrappe.py) an genau dieser Stelle, damit beide Teile denselben Dienst mit demselben Stand sehen und eine Sitzung durch
bot.build_session läuft, ohne dass ein Test die Umgebung oder einen Client von Hand verdrahten muss.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from unittest import mock

import httpx

import labor_client
import labor_durchlauf
import labor_werkzeuge
from labor_attrappe import ATTRAPPE_URL, Attrappe
from labor_client import LaborClient

EINWILLIGUNG_MIN = "1"  # die Attrappe erfasst für jeden Platz die Version 1 der Einwilligung


@contextlib.contextmanager
def dienst(app: Attrappe | None = None, *, einwilligung_min: str | None = EINWILLIGUNG_MIN,
           transport: httpx.AsyncBaseTransport | None = None) -> Iterator[Attrappe]:
    """Die Attrappe als der Dienst der Kollegen für den Kern: Durchlauf-Quelle, Wache und Werkzeuge bekommen Clients auf dieselbe Attrappe.

    transport: ein anderer Transport zur Attrappe (zum Beispiel einer, der Aufrufe stört); Standard ist die Attrappe im Prozess.
    einwilligung_min: WAMO_LABOR_EINWILLIGUNG_MIN für die Dauer (None: nicht setzen).
    """
    app = app or Attrappe()
    ziel = transport or httpx.ASGITransport(app=app)

    def fabrik(platz: int, **_kw) -> LaborClient:
        return LaborClient(ATTRAPPE_URL, app.token, platz, transport=ziel)

    umgebung = {"WAMO_LABOR_EINWILLIGUNG_MIN": einwilligung_min} if einwilligung_min is not None else {}
    with mock.patch.object(labor_client, "client_aus_umgebung", fabrik), mock.patch.object(labor_werkzeuge, "client_aus_umgebung", fabrik), \
         mock.patch.object(labor_durchlauf, "_quelle", None), mock.patch.dict(os.environ, umgebung):
        yield app
