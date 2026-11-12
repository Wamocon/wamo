"""Der Dienstclient des Kerns (voice-core/labor_client.py) gegen den echten Dienst über HTTP auf Loopback: ein Roundtrip V1 bis V7."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from echter_dienst import laufender_dienst
from hilfen import BETREUER_TOKEN, KERN_TOKEN, LABORTAG, Uhr, betreuer_kopf

import labor_client
import labor_vertrag
from wamo_labor_dienst.app import create_app
from wamo_labor_dienst.konfig import Konfig

PLATZ = 5


@pytest.fixture
def adresse(tmp_path):
    konfig = Konfig(daten=tmp_path / "daten", kern_token=KERN_TOKEN, betreuer_token=BETREUER_TOKEN)
    with laufender_dienst(create_app(konfig, uhr=Uhr(LABORTAG))) as url:
        yield url


@pytest.fixture
def clientumgebung(adresse, monkeypatch):
    monkeypatch.setenv("WAMO_LABOR_URL", adresse)
    monkeypatch.setenv("WAMO_LABOR_TOKEN", KERN_TOKEN)
    monkeypatch.delenv("WAMO_CA_FILE", raising=False)
    return adresse


def _betreuer(adresse: str, aktion: str, **kw):
    import httpx

    r = httpx.post(f"{adresse}/api/betreuer/plaetze/{PLATZ}/{aktion}", headers=betreuer_kopf(), trust_env=False, timeout=10, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_der_name_des_dienstes_ist_nicht_der_der_attrappe(clientumgebung):
    assert labor_vertrag.ATTRAPPE_HOST not in clientumgebung
    assert labor_client.adresse_pruefen(clientumgebung) == clientumgebung  # Loopback darf Klartext, keine Zugangsdaten, kein Pfad


def test_roundtrip_v1_bis_v7_ueber_echtes_http(clientumgebung):
    _betreuer(clientumgebung, "neuer-durchlauf", json={"einwilligung_version": 3})

    async def ablauf():
        c = labor_client.client_aus_umgebung(PLATZ)
        try:
            v1 = await c.durchlauf()
            assert v1.offen is True and v1.einwilligung_version == 3 and v1.einwilligung_zeit == "2026-11-28T09:30:00Z"
            schluessel = str(uuid.uuid4())
            neu = await c.anlegen(durchlauf_id=v1.id, schluessel=schluessel, typ="bug", prioritaet="hoch", titel="Anmeldung schlägt fehl",
                                  beschreibung="Nach dem Klick passiert nichts.")  # V4
            assert neu["nummer"] == "LAB-30" and neu["version"] == 1 and neu["im_durchlauf"] is True and neu["status"] == "zu_erledigen"
            wieder = await c.anlegen(durchlauf_id=v1.id, schluessel=schluessel, typ="bug", prioritaet="hoch", titel="Anmeldung schlägt fehl",
                                     beschreibung="Nach dem Klick passiert nichts.")
            assert wieder == neu  # Idempotenz: dieselbe Antwort, kein zweites Ticket
            such = await c.tickets(q="anmeldung")  # V2
            assert [t["nummer"] for t in such["tickets"]] == ["LAB-30"] and such["gesamt"] == 1
            assert (await c.tickets(nur_durchlauf=True, limit=5))["gesamt"] == 1
            assert (await c.tickets(status="erledigt"))["tickets"][0]["nummer"] == "LAB-11"
            lesen = await c.ticket("LAB-30")  # V3
            assert lesen["kommentare"] == [] and lesen["titel"] == "Anmeldung schlägt fehl"
            geaendert = await c.aendern(durchlauf_id=v1.id, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=1,
                                        felder={"titel": "Anmeldung schlägt später fehl", "prioritaet": "hoechste"})  # V5
            assert geaendert["version"] == 2 and geaendert["prioritaet"] == "hoechste"
            erledigt = await c.status_setzen(durchlauf_id=v1.id, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=2, status="in_arbeit")  # V6
            assert erledigt["status"] == "in_arbeit" and erledigt["version"] == 3
            kommentar = await c.kommentieren(durchlauf_id=v1.id, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=3, text="Läuft jetzt\nzweite Zeile")  # V7
            assert kommentar["kommentar_id"] == 1 and kommentar["ticket"]["version"] == 4
            assert (await c.ticket("LAB-30"))["kommentare"] == [{"kommentar_id": 1, "text": "Läuft jetzt\nzweite Zeile"}]
            with pytest.raises(labor_client.LaborFehler) as e:
                await c.status_setzen(durchlauf_id=v1.id, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=1, status="erledigt")
            assert e.value.code == "ticket_veraendert" and e.value.status == 409
            with pytest.raises(labor_client.LaborFehler) as e:
                await c.ticket("LAB-99")
            assert e.value.code == "ticket_unbekannt"
        finally:
            await c.aclose()

    asyncio.run(ablauf())


def test_beendeter_und_neuer_durchlauf_wirken_beim_client(clientumgebung):
    d1 = _betreuer(clientumgebung, "neuer-durchlauf", json={"einwilligung_version": 1})["durchlauf_id"]

    async def ablauf():
        c = labor_client.client_aus_umgebung(PLATZ)
        try:
            _betreuer(clientumgebung, "durchlauf-beenden")
            v1 = await c.durchlauf()
            assert v1.offen is False and v1.id == d1
            with pytest.raises(labor_client.LaborFehler) as e:
                await c.anlegen(durchlauf_id=d1, schluessel=str(uuid.uuid4()), typ="bug", prioritaet="hoch", titel="Zu spät", beschreibung="")
            assert e.value.code == "durchlauf_beendet"
            d2 = _betreuer(clientumgebung, "neuer-durchlauf", json={"einwilligung_version": 2})["durchlauf_id"]
            v2 = await c.durchlauf()
            assert v2.offen is True and v2.id == d2 != d1 and v2.einwilligung_version == 2
            assert (await c.tickets())["gesamt"] == 6  # Seed
        finally:
            await c.aclose()

    asyncio.run(ablauf())


def test_falsches_token_und_unbekannter_platz_beim_client(clientumgebung, monkeypatch):
    async def ablauf():
        schlecht = labor_client.LaborClient(clientumgebung, "x" * 40, PLATZ)
        try:
            with pytest.raises(labor_client.LaborFehler) as e:
                await schlecht.durchlauf()
            assert e.value.code == "token_ungueltig"
        finally:
            await schlecht.aclose()

    asyncio.run(ablauf())


def test_antwortzeit_des_dienstes_liegt_weit_unter_einer_sekunde(clientumgebung):
    async def ablauf():
        c = labor_client.client_aus_umgebung(PLATZ)
        try:
            dauern = []
            for _ in range(40):
                start = time.perf_counter()
                await c.durchlauf()
                dauern.append(time.perf_counter() - start)
            return sorted(dauern)
        finally:
            await c.aclose()

    dauern = asyncio.run(ablauf())
    assert dauern[int(len(dauern) * 0.95)] < 0.5
