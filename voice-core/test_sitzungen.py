"""Tests für sitzungen.py: eine Sitzung je Profil, sichere Übergabe der PC-Sperre, Schalter WAMO_SITZUNG_EINZELN."""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest

import pc_host
from sitzungen import GRUND_UEBERNOMMEN, Sitzung, Sitzungen, einzeln_aktiv

NIKO = "11111111-1111-4111-8111-111111111111"
EVA = "22222222-2222-4222-8222-222222222222"


def verbindung(fehler: Exception | None = None, haengt: bool = False):
    v = mock.Mock()

    async def disconnect():
        if haengt:
            await asyncio.sleep(60)
        if fehler:
            raise fehler

    v.disconnect = mock.AsyncMock(side_effect=disconnect)
    return v


def registry(**kw) -> Sitzungen:
    return Sitzungen(einzeln=True, meldung_vorlauf_secs=0, trennen_timeout_secs=0.2, **kw)


@pytest.fixture
def tunnel(monkeypatch):
    """Büro: die PC-Sperre gilt (über den Tunnel steuert höchstens eine Sitzung)."""
    monkeypatch.setenv("WAMO_PC_HOST_SOCKET", "/run/wamo-tunnel/pc.sock")
    monkeypatch.setattr(pc_host, "_lease", None)


async def test_neue_sitzung_trennt_die_alte_desselben_profils():
    reg = registry()
    alt, neu = Sitzung(NIKO, verbindung()), Sitzung(NIKO, verbindung())
    meldungen: list[str] = []

    async def melden(grund):
        meldungen.append(grund)

    alt.abloesen = melden
    assert await reg.uebernehmen(alt) is None
    assert await reg.uebernehmen(neu) is alt
    alt.verbindung.disconnect.assert_awaited_once()
    neu.verbindung.disconnect.assert_not_awaited()
    assert meldungen == [GRUND_UEBERNOMMEN]
    assert alt.abgeloest and not neu.abgeloest
    assert reg.aktiv(NIKO) is neu


async def test_andere_profile_bleiben_verbunden():
    reg = registry()
    niko, eva = Sitzung(NIKO, verbindung()), Sitzung(EVA, verbindung())
    await reg.uebernehmen(niko)
    await reg.uebernehmen(eva)  # der Büro-Kern bedient mehrere Profile: nicht global
    niko.verbindung.disconnect.assert_not_awaited()
    assert reg.aktiv(NIKO) is niko and reg.aktiv(EVA) is eva


async def test_freigeben_nur_ohne_neuere_sitzung():
    reg = registry()
    alt, neu = Sitzung(NIKO, verbindung()), Sitzung(NIKO, verbindung())
    await reg.uebernehmen(alt)
    await reg.uebernehmen(neu)
    reg.freigeben(alt)  # die alte räumt später auf
    assert reg.aktiv(NIKO) is neu
    reg.freigeben(neu)
    assert reg.aktiv(NIKO) is None


@pytest.mark.parametrize("v", [verbindung(fehler=RuntimeError("schon weg")), verbindung(haengt=True), None])
async def test_kaputte_alte_verbindung_blockiert_nicht(v):
    reg = registry()
    alt, neu = Sitzung(NIKO, v), Sitzung(NIKO, verbindung())

    async def kaputt(_grund):
        raise RuntimeError("Pipeline schon zu")

    alt.abloesen = kaputt
    await reg.uebernehmen(alt)
    await asyncio.wait_for(reg.uebernehmen(neu), 2)  # Fehler und Hänger der alten werden toleriert
    assert reg.aktiv(NIKO) is neu and alt.abgeloest


async def test_pc_sperre_geht_sicher_auf_die_neue_sitzung_ueber(tunnel):
    reg = registry()
    alt, neu = Sitzung(NIKO, verbindung()), Sitzung(NIKO, verbindung())
    await reg.uebernehmen(alt)
    assert alt.pc_lease_nehmen() and alt.pc_lease_haelt()
    assert not neu.pc_lease_nehmen()  # vorher: die alte steuert
    await reg.uebernehmen(neu)
    assert not alt.pc_lease_haelt()  # die alte verliert die Sperre, bevor sie getrennt ist
    assert not alt.pc_lease_nehmen()  # und holt sie sich nicht zurück (später Klick im alten Tab)
    assert neu.pc_lease_nehmen() and neu.pc_lease_haelt()
    alt.pc_lease_freigeben()  # spätes Aufräumen der alten wirkt nicht auf die neue
    assert neu.pc_lease_haelt()


def test_lokal_gilt_die_sperre_nicht_aber_abgeloest_schon(monkeypatch):
    monkeypatch.delenv("WAMO_PC_HOST_SOCKET", raising=False)
    s = Sitzung(NIKO, None)
    assert s.pc_lease_nehmen() and s.pc_lease_haelt()
    s.abgeloest = True
    assert not s.pc_lease_nehmen() and not s.pc_lease_haelt()


async def test_schalter_aus_laesst_mehrere_sitzungen_zu():
    reg = Sitzungen(einzeln=False, meldung_vorlauf_secs=0)
    alt, neu = Sitzung(NIKO, verbindung()), Sitzung(NIKO, verbindung())
    await reg.uebernehmen(alt)
    assert await reg.uebernehmen(neu) is None
    alt.verbindung.disconnect.assert_not_awaited()
    assert not alt.abgeloest


@pytest.mark.parametrize("wert,an", [(None, True), ("", True), ("an", True), ("ja", True), ("aus", False),
                                     ("off", False), ("0", False), (" NEIN ", False), ("false", False)])
def test_schalter_standard_an(monkeypatch, wert, an):
    if wert is None:
        monkeypatch.delenv("WAMO_SITZUNG_EINZELN", raising=False)
    else:
        monkeypatch.setenv("WAMO_SITZUNG_EINZELN", wert)
    assert einzeln_aktiv() is an


async def test_dieselbe_sitzung_zweimal_trennt_nicht():
    reg = registry()
    s = Sitzung(NIKO, verbindung())
    await reg.uebernehmen(s)
    assert await reg.uebernehmen(s) is None
    s.verbindung.disconnect.assert_not_awaited()


async def test_bot_loest_die_alte_sitzung_desselben_profils_ab(monkeypatch):
    import time

    import bot

    reg = Sitzungen(einzeln=True, meldung_vorlauf_secs=0)
    monkeypatch.setattr(bot, "SITZUNGEN", reg)
    monkeypatch.setattr(bot, "create_transport", mock.AsyncMock(return_value=object()))
    gestartet: list[Sitzung] = []
    ende = asyncio.Event()

    async def run_bot(_transport, _args, _identity, sitzung):
        gestartet.append(sitzung)
        await ende.wait()

    monkeypatch.setattr(bot, "run_bot", run_bot)
    body = {"wamo": {"user_id": NIKO, "name": "Niko", "rolle": "admin", "anrede": "du", "anmeldung": time.time()}}
    erste = mock.Mock(body=body, webrtc_connection=verbindung())
    zweite = mock.Mock(body=body, webrtc_connection=verbindung())
    async def bis(bedingung):
        async def warten():
            while not bedingung():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(warten(), 5)

    t1 = asyncio.create_task(bot.bot(erste))
    await bis(lambda: gestartet)
    t2 = asyncio.create_task(bot.bot(zweite))
    await bis(lambda: len(gestartet) >= 2)
    erste.webrtc_connection.disconnect.assert_awaited_once()
    zweite.webrtc_connection.disconnect.assert_not_awaited()
    assert gestartet[0].abgeloest and reg.aktiv(NIKO) is gestartet[1]
    ende.set()
    await asyncio.gather(t1, t2)
    assert reg.aktiv(NIKO) is None  # die neue hat beim Ende freigegeben, die alte nichts überschrieben


async def test_abloesung_waehrend_des_aufbaus_startet_die_alte_nicht(monkeypatch):
    """Review: Übernimmt eine neue Sitzung, während die alte noch aufgebaut wird, darf die alte nicht anlaufen."""
    import time

    import bot

    reg = Sitzungen(einzeln=True, meldung_vorlauf_secs=0)
    monkeypatch.setattr(bot, "SITZUNGEN", reg)
    monkeypatch.setattr(bot, "create_transport", mock.AsyncMock(return_value=object()))
    aufbau = asyncio.Event()
    weiter = asyncio.Event()
    aufgeraeumt: list[int] = []
    gestartet: list[int] = []

    async def build_session(_transport, _identity, sitzung):
        if not aufbau.is_set():
            aufbau.set()
            await weiter.wait()  # die erste Sitzung hängt im Aufbau (Modelle, Gedächtnis)

        async def cleanup():
            aufgeraeumt.append(1)
        return bot.Session(task=mock.Mock(), cleanup=cleanup)

    class Runner:
        def __init__(self, **_kw):
            pass

        async def run(self, _task):
            gestartet.append(1)

    monkeypatch.setattr(bot, "build_session", build_session)
    monkeypatch.setattr(bot, "PipelineRunner", Runner)
    body = {"wamo": {"user_id": NIKO, "name": "Niko", "rolle": "admin", "anrede": "du", "anmeldung": time.time()}}
    erste = mock.Mock(body=body, webrtc_connection=verbindung(), handle_sigint=False)
    t1 = asyncio.create_task(bot.bot(erste))
    await asyncio.wait_for(aufbau.wait(), 5)
    await bot.bot(mock.Mock(body=body, webrtc_connection=verbindung(), handle_sigint=False))
    weiter.set()
    await asyncio.wait_for(t1, 5)
    erste.webrtc_connection.disconnect.assert_awaited()
    assert gestartet == [1]  # nur die neue lief
    assert len(aufgeraeumt) >= 2  # die alte wurde nach dem Aufbau sofort aufgeräumt
