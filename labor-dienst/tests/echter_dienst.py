"""Hilfe der Integrationstests (kein Test): startet den Dienst mit denselben Einstellungen wie der Einstieg auf Loopback in einem Thread."""
from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import Iterator

import httpx
import uvicorn
from hilfen import BETREUER_TOKEN, betreuer_kopf

from wamo_labor_dienst.__main__ import uvicorn_einstellungen


@contextlib.contextmanager
def laufender_dienst(app) -> Iterator[str]:
    """Der Dienst auf 127.0.0.1 mit freiem Port. Liefert die Adresse (http://127.0.0.1:PORT)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    adresse = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, **uvicorn_einstellungen()))
    faden = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])), daemon=True)
    faden.start()
    ende = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > ende or not faden.is_alive():
            raise RuntimeError("Der Dienst ist nicht gestartet")
        time.sleep(0.02)
    try:
        yield adresse
    finally:
        server.should_exit = True
        faden.join(15)
        sock.close()


def abnahmeplaetze_vorbereiten(adresse: str, plaetze: tuple[int, ...] = (15, 16)) -> str:
    """Der Betreuer legt für die Abnahmeplätze einen neuen Durchlauf mit Einwilligung an. Liefert den Platz-Token des ersten Platzes."""
    with httpx.Client(base_url=adresse, trust_env=False, timeout=10) as c:
        for platz in plaetze:
            r = c.post(f"/api/betreuer/plaetze/{platz}/neuer-durchlauf", json={"einwilligung_version": 1}, headers=betreuer_kopf())
            assert r.status_code == 200, r.text
        r = c.post(f"/api/betreuer/plaetze/{plaetze[0]}/platz-token", headers=betreuer_kopf())
        assert r.status_code == 200 and BETREUER_TOKEN not in r.text
        return r.json()["platz_token"]
