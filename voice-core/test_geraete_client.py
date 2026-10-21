"""Client für den Gerätedienst: Protokoll, Prüfung der Gegenstelle vor dem ersten Byte, Grenzen (ohne Unix-Socket:
die Verbindung kommt über einen lokalen TCP-Testserver, die uid-Prüfung ist eingesetzt)."""

import asyncio
import json

import pytest

import geraete_client as gc

KONTEXT = gc.Kontext("00000000-0000-4000-8000-000000000001", "mitglied", None, "Anna")


async def _server(antwort: bytes, gesehen: list):
    async def behandeln(reader, writer):
        try:
            zeile = await asyncio.wait_for(reader.readuntil(b"\n"), 2)
            gesehen.append(json.loads(zeile))
        except Exception:  # noqa: BLE001
            gesehen.append(None)
        writer.write(antwort)
        await writer.drain()
        writer.close()
    return await asyncio.start_server(behandeln, "127.0.0.1", 0)


def _verbinden(port):
    async def verbinden(_pfad, _timeout):
        return await asyncio.open_connection("127.0.0.1", port)
    return verbinden


async def test_aufruf_mit_anhang():
    gesehen: list = []
    server = await _server(b'{"status": "success", "eintrag": {"name": "Scan.pdf"}, "anhang": 4}\n%PDF', gesehen)
    async with server:
        antwort, anhang = await gc.aufruf("ablage_holen", KONTEXT, argumente={"id": "a" * 32},
                                          verbinden=_verbinden(server.sockets[0].getsockname()[1]),
                                          uid_pruefen=lambda _s: 990, erwartete_uid=990)
    assert antwort["status"] == "success" and anhang == b"%PDF"
    assert gesehen[0]["kontext"] == KONTEXT.als_dict() and gesehen[0]["v"] == 1 and "geraet" not in gesehen[0]


async def test_fremde_gegenstelle_bekommt_nichts():
    gesehen: list = []
    server = await _server(b'{"status": "success"}\n', gesehen)
    async with server:
        with pytest.raises(gc.GeraeteNichtVerfuegbar):
            await gc.aufruf("status", KONTEXT, geraet="drucker",
                            verbinden=_verbinden(server.sockets[0].getsockname()[1]),
                            uid_pruefen=lambda _s: 1000, erwartete_uid=990)
        await asyncio.sleep(0.1)
    assert gesehen == [None]  # der Server hat keine Anfrage bekommen


@pytest.mark.parametrize("antwort", [b"kaputt\n", b'{"status": "success", "anhang": 99999999999}\n',
                                     b'{"status": 1}\n', b'{"status": "success", "anhang": 10}\nkurz'])
async def test_kaputte_antworten(antwort):
    server = await _server(antwort, [])
    async with server:
        with pytest.raises(gc.GeraeteNichtVerfuegbar):
            await gc.aufruf("ping", KONTEXT, verbinden=_verbinden(server.sockets[0].getsockname()[1]),
                            uid_pruefen=lambda _s: 990, erwartete_uid=990, timeout=2)


async def test_nicht_erreichbar_und_unterwegs(monkeypatch):
    async def weg(_p, _t):
        raise ConnectionRefusedError
    with pytest.raises(gc.GeraeteNichtVerfuegbar):
        await gc.aufruf("ping", KONTEXT, verbinden=weg, uid_pruefen=lambda _s: 990, erwartete_uid=990)
    monkeypatch.setenv("WAMO_GERAETE", "aus")
    assert gc.verfuegbar() is False
