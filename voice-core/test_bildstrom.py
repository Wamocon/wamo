"""Tests für das Live-Bild im Kern (bildstrom.py) und seine Tunnelgrenzen (pc_tunnel.py).

Ohne Netz und ohne echten Laptop: Der Host ist eine Attrappe, die Bilder sind Platzhalter. Geprüft werden die Verbotsproben
(nicht erlaubt, Schalter aus, Sperre, zweiter Zuschauer, Rate, Größe, Inaktivität, Zeitlimit) und dass nichts von einem Bild
in Logs, Nachrichten an das Modell oder fremde Wege gerät.
"""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from loguru import logger

import bildstrom
import pc_tunnel
from bildstrom import Bildstrom, pruefe_bild

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"x" * 300).decode()


def bild(**aenderungen):
    antwort = {"status": "success", "monitor": 1, "monitore": 2, "breite": 800, "hoehe": 450, "jpeg_base64": JPEG,
               "bytes": 304}
    antwort.update(aenderungen)
    return antwort


class Uhr:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Aufbau:
    """Bildstrom mit Attrappen. host: Liste von Antworten je Werkzeug (die letzte wiederholt sich)."""

    def __init__(self, antworten=None, darf=True, **optionen):
        self.antworten = {"live_bild_status": [{"status": "success", "bereit": True, "monitore": 2}],
                          "bild_holen": [bild()], "live_bild_beenden": [{"status": "success"}]}
        self.antworten.update(antworten or {})
        self.aufrufe: list[tuple[str, dict]] = []
        self.gesendet: list[dict] = []
        self.angesagt: list[str] = []
        self.darf_wert = darf
        self.uhr = Uhr()
        self.client = object()

        async def aufruf(_client, name, argumente):
            self.aufrufe.append((name, argumente))
            liste = self.antworten[name]
            antwort = liste.pop(0) if len(liste) > 1 else liste[0]
            if isinstance(antwort, Exception):
                raise antwort
            return json.dumps(antwort)

        async def senden(d):
            self.gesendet.append(d)

        async def ansage(t):
            self.angesagt.append(t)

        async def schlaf(s):
            self.uhr.t += s
            await asyncio.sleep(0)

        self.strom = Bildstrom(client=lambda: self.client, darf=lambda: self.darf_wert, senden=senden, ansage=ansage,
                               aufruf=aufruf, jetzt=self.uhr, schlaf=schlaf, **optionen)

    def bilder(self):
        return [d for d in self.gesendet if d["type"] == "bild"]

    def zustaende(self):
        return [d["zustand"] for d in self.gesendet if d["type"] == "bildstrom"]

    async def quittiere_bis(self, anzahl, gesamt=300):
        """Wie die Oberfläche: jedes Bild bestätigen, bis anzahl Bilder angekommen sind."""
        bestaetigt = 0
        for _ in range(gesamt):
            await asyncio.sleep(0)
            b = self.bilder()
            if len(b) > bestaetigt:
                bestaetigt = len(b)
                await self.strom.nachricht({"aktion": "ack", "seq": b[-1]["seq"]})
            if bestaetigt >= anzahl:
                return
        raise AssertionError("zu wenige Bilder")

    async def warte_auf_ende(self, gesamt=500):
        for _ in range(gesamt):
            await asyncio.sleep(0)
            if self.strom.zustand == "aus" and self.strom._task.done():
                return
        raise AssertionError("Lauf endet nicht")


async def starte(a: Aufbau, monitor=1):
    await a.strom.nachricht({"aktion": "an", "monitor": monitor})


async def test_bilder_laufen_und_ende_wird_gemeldet():
    a = Aufbau()
    await starte(a)
    await a.quittiere_bis(3)
    assert a.zustaende()[0] == "laeuft"
    assert [b["seq"] for b in a.bilder()][:3] == [1, 2, 3]
    assert all(b["jpeg"] == JPEG for b in a.bilder())
    assert any("Live-Bild deines Laptops läuft" in t for t in a.angesagt)
    await a.strom.nachricht({"aktion": "aus"})
    assert a.strom.zustand == "aus"
    assert a.gesendet[-1]["zustand"] == "aus"
    assert ("live_bild_beenden", {"zuschauer": a.aufrufe[1][1]["zuschauer"]}) in a.aufrufe  # Platz im Host frei
    assert any("beendet" in t for t in a.angesagt)


async def test_nicht_erlaubt_startet_nie():
    a = Aufbau(darf=False)
    await starte(a)
    assert a.aufrufe == []  # nicht einmal der Host wird gefragt
    assert a.gesendet[-1]["zustand"] == "aus" and a.gesendet[-1]["grund"] == "nicht_erlaubt"
    assert a.strom._task is None


async def test_ohne_client_startet_nie():
    a = Aufbau()
    a.client = None
    await starte(a)
    assert a.aufrufe == [] and a.gesendet[-1]["grund"] == "nicht_erlaubt"


async def test_schalter_aus_wartet_und_startet_erst_nach_dem_einschalten():
    a = Aufbau({"live_bild_status": [
        {"status": "success", "bereit": False, "grund": "schalter_aus", "summary": "Am Laptop ist das Live-Bild aus."},
        {"status": "success", "bereit": False, "grund": "schalter_aus", "summary": "Am Laptop ist das Live-Bild aus."},
        {"status": "success", "bereit": True, "monitore": 1}]})
    await starte(a)
    await a.quittiere_bis(1)
    assert a.zustaende()[:2] == ["wartet", "laeuft"]  # "wartet" nur einmal gemeldet
    assert [n for n, _ in a.aufrufe].index("bild_holen") > 2


async def test_warten_endet_nach_der_frist():
    a = Aufbau({"live_bild_status": [{"status": "success", "bereit": False, "grund": "schalter_aus"}]}, warten_max=10)
    await starte(a)
    await a.warte_auf_ende()
    assert a.bilder() == [] and "bild_holen" not in [n for n, _ in a.aufrufe]
    assert "nicht eingeschaltet" in a.gesendet[-1]["text"]


async def test_mitglied_laptop_stufe_beendet():
    a = Aufbau({"live_bild_status": [{"status": "success", "bereit": False, "grund": "stufe"}]})
    await starte(a)
    await a.warte_auf_ende()
    assert a.bilder() == [] and a.gesendet[-1]["zustand"] == "aus"


async def test_sperre_beendet_sofort():
    a = Aufbau({"bild_holen": [bild(), {"status": "locked", "summary": "gesperrt"}]})
    await starte(a)
    await a.quittiere_bis(1)
    await a.warte_auf_ende()
    assert "gesperrt" in a.gesendet[-1]["text"]
    assert len(a.bilder()) == 1


async def test_sperre_von_aussen_stoppt_den_lauf():
    a = Aufbau()
    await starte(a)
    await a.quittiere_bis(1)
    await a.strom.stoppen("Der Bildschirm am Laptop ist gesperrt.")
    assert a.strom.zustand == "aus" and a.strom._task.done()
    n = len(a.aufrufe)
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(a.aufrufe) == n  # danach ruft nichts mehr den Host


async def test_pc_steuerung_aus_beendet_den_lauf():
    a = Aufbau()
    await starte(a)
    await a.quittiere_bis(1)
    a.darf_wert = False
    await a.quittiere_bis(1)  # bestätigt das letzte Bild; danach prüft die Schleife darf()
    await a.warte_auf_ende()
    assert "PC-Steuerung" in a.gesendet[-1]["text"]


async def test_pausiert_bei_freigabe_und_vollbild_ohne_bild():
    a = Aufbau({"bild_holen": [{"status": "pausiert", "grund": "freigabe"}, {"status": "pausiert", "grund": "vollbild"},
                               bild()]})
    await starte(a)
    await a.quittiere_bis(1)
    z = a.zustaende()
    assert z[:3] == ["pausiert", "pausiert", "laeuft"] or z[:2] == ["pausiert", "laeuft"]
    assert len(a.bilder()) >= 1 and a.bilder()[0]["seq"] == 1


async def test_zweiter_zuschauer_beendet_ohne_bild():
    a = Aufbau({"bild_holen": [{"status": "belegt", "grund": "zuschauer"}]})
    await starte(a)
    await a.warte_auf_ende()
    assert a.bilder() == [] and "anderen Ansicht" in a.gesendet[-1]["text"]


async def test_zu_schnell_wartet_nur():
    a = Aufbau({"bild_holen": [{"status": "langsamer", "warte_ms": 400}, bild()]})
    await starte(a)
    await a.quittiere_bis(2)
    assert a.zustaende()[0] == "laeuft"


async def test_ohne_bestaetigung_endet_die_ansicht():
    a = Aufbau(ack_frist=0.05)
    await starte(a)
    for _ in range(300):
        await asyncio.sleep(0.001)
        if a.strom._task.done():
            break
    assert a.strom._task.done()
    assert len(a.bilder()) == 1  # kein zweites Bild ohne Bestätigung des ersten
    assert "antwortet nicht" in a.gesendet[-1]["text"]


async def test_falsche_bestaetigung_zaehlt_nicht():
    a = Aufbau(ack_frist=0.05)
    await starte(a)
    await asyncio.sleep(0)
    for seq in (0, 99, "1", True, None, -1):
        await a.strom.nachricht({"aktion": "ack", "seq": seq})
    for _ in range(300):
        await asyncio.sleep(0.001)
        if a.strom._task.done():
            break
    assert len(a.bilder()) == 1


async def test_zeitlimit():
    a = Aufbau(zeitlimit=3.0, intervall=1.0)
    await starte(a)
    await a.quittiere_bis(2)
    await a.warte_auf_ende()
    assert "Höchstdauer" in a.gesendet[-1]["text"]
    assert len(a.bilder()) <= 4


async def test_bild_mit_falschen_werten_wird_nie_weitergegeben():
    schlecht = [bild(jpeg_base64="A" * 20), bild(jpeg_base64="/9j/" + "A" * bildstrom.BILD_B64_MAX),
                bild(jpeg_base64="/9j/ !!"), bild(monitor=0), bild(breite=True), bild(hoehe="5")]
    for s in schlecht:
        assert pruefe_bild(s, 1, 10) is None, s
    assert pruefe_bild(bild(), 1, 10)["jpeg"] == JPEG
    a = Aufbau({"bild_holen": [bild(jpeg_base64="kein jpeg")]})
    await starte(a)
    await a.warte_auf_ende()
    assert a.bilder() == []  # nach drei Fehlern Ende


async def test_tunnel_ausfall_beendet():
    a = Aufbau({"bild_holen": [bild(), pc_tunnel.PcNichtErreichbar("weg")]})
    await starte(a)
    await a.quittiere_bis(1)
    await a.warte_auf_ende()
    assert "unterbrochen" in a.gesendet[-1]["text"]


async def test_monitorwahl_wirkt_auf_den_naechsten_abruf():
    a = Aufbau()
    await starte(a, monitor=1)
    await a.quittiere_bis(1)
    await a.strom.nachricht({"aktion": "monitor", "monitor": 2})
    await a.strom.nachricht({"aktion": "monitor", "monitor": 99})  # ungültig: bleibt
    await a.quittiere_bis(3)
    monitore = [arg["monitor"] for n, arg in a.aufrufe if n == "bild_holen"]
    assert monitore[0] == 1 and 2 in monitore and 99 not in monitore
    await a.strom.stoppen("Ende")


async def test_unbekannte_und_kaputte_nachrichten_sind_harmlos():
    a = Aufbau()
    for d in ({}, {"aktion": 5}, {"aktion": "x"}, {"aktion": "an", "monitor": "a"}, None, "text"):
        await a.strom.nachricht(d)
    if a.strom._task:
        await a.strom.stoppen("Ende")


async def test_nachrichten_an_das_modell_enthalten_nie_ein_bild():
    a = Aufbau()
    await starte(a)
    await a.quittiere_bis(2)
    await a.strom.stoppen("Ende")
    for text in a.angesagt:
        assert JPEG not in text and "jpeg" not in text.lower()


async def test_logs_enthalten_nie_ein_bild():
    meldungen: list[str] = []
    senke = logger.add(lambda m: meldungen.append(str(m)), level="DEBUG")
    try:
        a = Aufbau({"bild_holen": [bild(), RuntimeError("boom"), bild()]})
        await starte(a)
        await a.quittiere_bis(1)
        await a.warte_auf_ende()
    finally:
        logger.remove(senke)
    assert JPEG not in "".join(meldungen)


# ---------- Tunnelgrenzen ----------
def tunnel(handler, **optionen):
    return pc_tunnel.TunnelHost("/x/pc.sock", "t" * 43, tools_filter=[], transport=httpx.MockTransport(handler),
                                geraet="pc-test", profil="p", aufrufe_je_minute=pc_tunnel.AUFRUFE_JE_MINUTE_GERAET,
                                max_antwort_bytes=pc_tunnel.MAX_ANTWORT_BYTES_GERAET, **optionen)


def antwort_mit(inhalt: str):
    def handler(request):
        rpc = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"],
                                         "result": {"content": [{"type": "text", "text": inhalt}]}})
    return handler


async def test_bildabrufe_zaehlen_nicht_zu_den_allgemeinen_aufrufen():
    t = tunnel(antwort_mit("{}"))
    for _ in range(pc_tunnel.AUFRUFE_JE_MINUTE_GERAET + 30):
        await t.call_text("bild_holen", {"zuschauer": "a" * 16, "monitor": 1})
    await t.call_text("system_info", {})  # die allgemeine Grenze ist unberührt
    await t.close()


async def test_bildabrufe_haben_eine_eigene_grenze():
    t = tunnel(antwort_mit("{}"))
    with pytest.raises(pc_tunnel.PcZuvieleAufrufe):
        for _ in range(pc_tunnel.BILD_AUFRUFE_JE_MINUTE + 1):
            await t.call_text("bild_holen", {})
    await t.call_text("system_info", {})  # andere Werkzeuge laufen weiter
    await t.close()


async def test_bildantwort_ist_auf_256_kb_begrenzt():
    gross = "x" * (pc_tunnel.MAX_ANTWORT_BYTES_BILD + 1000)
    t = tunnel(antwort_mit(gross))
    with pytest.raises(RuntimeError):
        await t.call_text("bild_holen", {})
    # ein anderes Werkzeug darf bis 4 MB antworten
    assert len(await t.call_text("system_info", {})) > 256 * 1024
    await t.close()


def test_bildwerkzeuge_sind_nie_fuer_das_modell():
    import pc_host
    for name in pc_tunnel.BILD_WERKZEUGE:
        assert name not in pc_host.LLM_TOOLS
