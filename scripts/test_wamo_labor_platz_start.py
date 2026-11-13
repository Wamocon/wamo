"""Tests für `wamo_labor_platz.py starten`: zwei Chrome-Fenster, Platz-Token nur über die einmalige Weiterleitung auf Loopback.

Es wird nie ein echter Prozess gestartet: Der Mitschnitt hält jede Befehlszeile fest, und ein Chrome-Ersatz holt die Weiterleitung
über eine echte Verbindung auf 127.0.0.1 ab (der einzige Netzverkehr, nie nach außen). Das Platz-Token wird aus Zeichen berechnet.
"""
from __future__ import annotations

import dataclasses
import http.client
import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_platz_testwelt as tw  # noqa: E402

BOARD = "https://192.168.178.136:55447/board"


@pytest.fixture(autouse=True)
def _fallen(monkeypatch, tmp_path):
    tw.fallen_stellen(monkeypatch, tmp_path / "echt")


@pytest.fixture
def welt(tmp_path):
    w = tw.Welt(tmp_path / "w")
    assert w.lauf("einrichten", "--platz", "3") == 0
    w.zeilen.clear()
    return w


def _chrome_holt(welt, ergebnisse: list):
    """Spielt Chrome nach: Das Board-Fenster ruft seine Startadresse auf Loopback ab."""
    def bei_start(args):
        app = next((a[len("--app="):] for a in args if a.startswith("--app=http://127.0.0.1:")), None)
        if app is None:
            return
        teile = urlsplit(app)
        verbindung = http.client.HTTPConnection("127.0.0.1", teile.port, timeout=3)
        verbindung.request("GET", teile.path)
        antwort = verbindung.getresponse()
        ergebnisse.append((antwort.status, antwort.getheader("Location"), {k.lower(): v for k, v in antwort.getheaders()}))
        verbindung.close()
    welt.prozesse.bei_start = bei_start


def _alles_gesammelt(welt) -> str:
    teile = [welt.text, repr(welt.reg.momentaufnahme()), repr(welt.prozesse.aufrufe), repr(welt.lnk.aufrufe), repr(welt.zert.aufrufe)]
    teile += [v.decode("utf-8", "replace") for v in welt.dateien().values()]
    return "\n".join(teile)


def _port_zu(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) != 0


# ---------- Ablauf ----------

def test_starten_oeffnet_zwei_fenster_ohne_token_in_der_befehlszeile(welt):
    antworten: list = []
    _chrome_holt(welt, antworten)
    assert welt.lauf("starten") == 0
    erstes, zweites = welt.prozesse.aufrufe
    assert erstes[0] == str(welt.chrome) and "--app=https://192.168.178.136:55445" in erstes
    assert zweites[0] == str(welt.chrome) and any(a.startswith("--app=http://127.0.0.1:") and "/weiter/" in a for a in zweites)
    for args in (erstes, zweites):
        assert f"--user-data-dir={welt.profil}" in args and "--no-first-run" in args
    assert "--window-position=0,0" in erstes and "--window-size=960,1040" in erstes
    assert "--window-position=960,0" in zweites and "--window-size=960,1040" in zweites
    assert not any(tw.TOKEN in a for args in welt.prozesse.aufrufe for a in args)


def test_token_erreicht_das_board_nur_ueber_die_weiterleitung(welt):
    antworten: list = []
    _chrome_holt(welt, antworten)
    assert welt.lauf("starten") == 0
    (status, ort, kopf), = antworten
    assert status == 302 and ort == BOARD + "#t=" + tw.TOKEN
    assert kopf["cache-control"] == "no-store" and kopf["referrer-policy"] == "no-referrer"
    port = int(urlsplit(next(a for a in welt.prozesse.aufrufe[1] if a.startswith("--app="))[6:]).port)
    assert _port_zu(port)  # nach der Abholung ist der Port wieder zu


def test_token_steht_nirgends_ausser_in_der_einen_antwort(welt):
    _chrome_holt(welt, [])
    assert welt.lauf("starten") == 0
    assert tw.TOKEN not in _alles_gesammelt(welt)
    assert "#t=" not in _alles_gesammelt(welt)


def test_weiterleitung_laeuft_ab_wenn_niemand_sie_abholt(welt):
    welt.umg.weiter_frist_s = 0.3
    assert welt.lauf("starten") == 1 and "nicht abgeholt" in welt.text
    port = int(urlsplit(next(a for a in welt.prozesse.aufrufe[1] if a.startswith("--app="))[6:]).port)
    assert _port_zu(port) and tw.TOKEN not in _alles_gesammelt(welt)


def test_zwischen_den_fenstern_wird_gewartet(welt):
    ereignisse: list = []
    welt.umg.chrome_wartezeit_s = 1.5
    welt.umg.warten = lambda s: ereignisse.append(("warten", s))
    welt.prozesse.bei_start = lambda args: ereignisse.append(("start", len(welt.prozesse.aufrufe)))
    welt.umg.weiter_frist_s = 0.2
    welt.lauf("starten")
    assert ereignisse[:3] == [("start", 1), ("warten", 1.5), ("start", 2)]


# ---------- Eingaben und Schutz ----------

@pytest.mark.parametrize("eingabe", ["", "   ", "abc", tw.TOKEN + "&x=1", tw.TOKEN[:-1], tw.TOKEN.upper(), tw.TOKEN + "\r\nHost: x"])
def test_ungueltiges_token_startet_nichts_und_wird_nicht_wiederholt(welt, eingabe):
    welt.umg.token_eingabe = lambda: eingabe
    assert welt.lauf("starten") == 2
    assert welt.prozesse.aufrufe == []
    assert "Platz-Token" in welt.text
    if eingabe.strip():
        assert eingabe not in welt.text


def test_token_mit_umgebenden_leerzeichen_wird_getrimmt(welt):
    antworten: list = []
    _chrome_holt(welt, antworten)
    welt.umg.token_eingabe = lambda: "  " + tw.TOKEN + "\n"
    assert welt.lauf("starten") == 0 and antworten[0][1].endswith("#t=" + tw.TOKEN)


def test_ohne_board_fragt_nicht_nach_dem_token(welt):
    def verboten():
        raise AssertionError("Token wurde erfragt")
    welt.umg.token_eingabe = verboten
    assert welt.lauf("starten", "--ohne-board") == 0
    assert len(welt.prozesse.aufrufe) == 1 and "--app=https://192.168.178.136:55445" in welt.prozesse.aufrufe[0]


def test_veraenderte_platzdatei_bekommt_nie_ein_token(welt):
    daten = json.loads(welt.platz_datei.read_text(encoding="utf-8"))
    daten["board_url"] = "https://boese.example:55447/board"
    welt.platz_datei.write_text(json.dumps(daten), encoding="utf-8")

    def verboten():
        raise AssertionError("Token wurde erfragt")
    welt.umg.token_eingabe = verboten
    assert welt.lauf("starten") == 1 and "verändert" in welt.text
    assert welt.prozesse.aufrufe == []


def test_ohne_einrichten_und_ohne_chrome(welt, tmp_path):
    welt.lauf("rueckweg")
    assert welt.lauf("starten") == 2 and "einrichten" in welt.text
    welt.lauf("einrichten", "--platz", "3")
    welt.chrome.unlink()
    assert welt.lauf("starten") == 1 and "Chrome" in welt.text and welt.prozesse.aufrufe == []


def test_belegter_port_startet_kein_fenster(welt):
    with socket.socket() as belegt:
        belegt.bind(("127.0.0.1", 0))
        belegt.listen(1)
        welt.umg.weiter_port = belegt.getsockname()[1]
        assert welt.lauf("starten") == 1 and "belegt" in welt.text
    assert welt.prozesse.aufrufe == [] and tw.TOKEN not in _alles_gesammelt(welt)


# ---------- Weiterleitung, Fenster, Chrome ----------

def _abruf(port: int, pfad: str, host: str | None = None, methode: str = "GET"):
    verbindung = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    kopf = {"Host": host} if host else {}
    verbindung.request(methode, pfad, headers=kopf)
    antwort = verbindung.getresponse()
    status, ort = antwort.status, antwort.getheader("Location")
    verbindung.close()
    return status, ort


def test_weiterleitung_gilt_nur_fuer_den_einen_pfad_den_richtigen_host_und_get():
    import wamo_labor_platz_start as st
    with st.EinmalWeiterleitung(0, BOARD + "#t=" + tw.TOKEN, 3.0) as w:
        pfad = urlsplit(w.adresse).path
        assert pfad.startswith("/weiter/") and len(pfad) > len("/weiter/") + 16
        for schlecht in ("/", "/weiter/", pfad + "x", "/weiter/falsch", "/favicon.ico"):
            assert _abruf(w.port, schlecht)[0] == 404, schlecht
        assert _abruf(w.port, pfad, host="evil.example")[0] == 404
        assert _abruf(w.port, pfad, methode="POST")[0] != 302
        assert not w.abgeholt
        assert _abruf(w.port, pfad) == (302, BOARD + "#t=" + tw.TOKEN)
        assert w.abwarten() is True and w.abgeholt


def test_weiterleitung_wird_nur_einmal_bedient_und_hat_pro_start_einen_neuen_pfad():
    import wamo_labor_platz_start as st
    with st.EinmalWeiterleitung(0, BOARD + "#t=" + tw.TOKEN, 3.0) as a:
        erster = a.adresse
        assert _abruf(a.port, urlsplit(erster).path)[0] == 302
        assert _abruf(a.port, urlsplit(erster).path)[0] == 404
    with st.EinmalWeiterleitung(0, BOARD, 3.0) as b:
        assert urlsplit(b.adresse).path != urlsplit(erster).path


def test_weiterleitung_wartet_nicht_ewig():
    import wamo_labor_platz_start as st
    with st.EinmalWeiterleitung(0, BOARD, 0.2) as w:
        assert w.abwarten() is False


def test_festes_tor_ist_in_der_richtlinie_erlaubt():
    import wamo_labor_platz_adressen as adr
    import wamo_labor_platz_umgebung as um
    standard = {f.name: f.default for f in dataclasses.fields(um.Umgebung)}["weiter_port"]
    assert standard == adr.WEITER_PORT and adr.WEITER_ADRESSE.startswith(f"http://127.0.0.1:{standard}/")


@pytest.mark.parametrize("bereich,links,rechts", [
    ((0, 0, 1920, 1040), (0, 0, 960, 1040), (960, 0, 960, 1040)),
    ((0, 0, 1367, 728), (0, 0, 683, 728), (683, 0, 684, 728)),
    ((100, 50, 1000, 600), (100, 50, 500, 600), (600, 50, 500, 600)),
])
def test_fenster_teilen_den_arbeitsbereich_in_zwei_haelften(bereich, links, rechts):
    import wamo_labor_platz_start as st
    assert st.fenster_geometrie(bereich) == (links, rechts)


def test_chrome_wird_in_der_reihenfolge_der_kandidaten_gesucht(tmp_path):
    import wamo_labor_platz_start as st
    a, b = tmp_path / "a" / "chrome.exe", tmp_path / "b" / "chrome.exe"
    b.parent.mkdir()
    b.write_bytes(b"")
    assert st.chrome_finden((a, b)) == b and st.chrome_finden((a,)) is None
    a.parent.mkdir()
    a.write_bytes(b"")
    assert st.chrome_finden((a, b)) == a


def test_chrome_argumente_gegen_drosselung_und_ohne_automatisches_mikrofon(tmp_path):
    import wamo_labor_platz_start as st
    args = st.chrome_argumente(tmp_path / "chrome.exe", tmp_path / "profil", "https://192.168.178.136:55445", (0, 0, 800, 600))
    assert "--disable-renderer-backgrounding" in args and "--disable-background-timer-throttling" in args
    assert any(a.startswith("--disable-features=") and "CalculateNativeWinOcclusion" in a for a in args)
    assert not any("fake-ui" in a or "remote-debugging" in a for a in args)
