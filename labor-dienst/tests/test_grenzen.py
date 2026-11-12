"""Grenzen für Lesen, Fehlversuche und Verbindungen (Befund 3 des Sicherheitsreviews)."""
from __future__ import annotations

import socket
import time
from datetime import date

import pytest
from echter_dienst import laufender_dienst
from fastapi.testclient import TestClient
from hilfen import BETREUER_TOKEN, KERN_TOKEN, LABORTAG, Uhr, anlegen_http, betreuer_kopf, kern_kopf, oeffnen

from wamo_labor_dienst import browser_api, server, sicherheit
from wamo_labor_dienst.app import create_app
from wamo_labor_dienst.datenbank import PlatzDB
from wamo_labor_dienst.grenzen import Begrenzer, FehlerZaehler
from wamo_labor_dienst.konfig import Konfig, KonfigFehler, aus_umgebung

PLATZ = 3


# ---------- Bausteine ----------
def test_begrenzer_erlaubt_den_schub_und_fuellt_mit_der_zeit_auf():
    uhr = Uhr()
    b = Begrenzer(rate=2, schub=5, uhr=uhr)
    assert [b.erlaubt("a") for _ in range(7)] == [True] * 5 + [False] * 2
    assert b.erlaubt("b") is True  # jeder Schlüssel hat seinen eigenen Eimer
    uhr.vor(1)
    assert [b.erlaubt("a") for _ in range(3)] == [True, True, False]
    uhr.vor(1000)
    assert [b.erlaubt("a") for _ in range(6)] == [True] * 5 + [False]  # nie mehr als der Schub


def test_begrenzer_uhr_zurueckgestellt_sperrt_nicht_dauerhaft():
    uhr = Uhr()
    b = Begrenzer(rate=2, schub=2, uhr=uhr)
    uhr.vor(5000)
    assert b.erlaubt("a") and b.erlaubt("a") and not b.erlaubt("a")
    uhr.vor(-5000)
    assert b.erlaubt("a") is False
    uhr.vor(1)
    assert b.erlaubt("a") is True


def test_begrenzer_merkt_sich_nur_begrenzt_viele_schluessel():
    b = Begrenzer(rate=1, schub=1, uhr=Uhr(), max_schluessel=50)
    for i in range(500):
        b.erlaubt(f"s{i}")
    assert len(b) <= 50


def test_fehlerzaehler_zaehlt_je_quelle_und_je_fenster():
    uhr = Uhr()
    z = FehlerZaehler(grenze=3, fenster=60, uhr=uhr)
    assert [z.melden("a") for _ in range(5)] == [False, False, False, True, True]
    assert z.melden("b") is False
    uhr.vor(60)
    assert z.melden("a") is False


def test_fehlerzaehler_merkt_sich_nur_begrenzt_viele_quellen():
    z = FehlerZaehler(grenze=3, fenster=60, uhr=Uhr(), max_quellen=40)
    for i in range(400):
        z.melden(f"q{i}")
    assert len(z) <= 40


# ---------- Board: Rate, globale Grenze, Zwischenspeicher ----------
def _token(client, platz: int = PLATZ) -> str:
    return client.post(f"/api/betreuer/plaetze/{platz}/platz-token", headers=betreuer_kopf()).json()["platz_token"]


def _board(client, token: str):
    return client.get("/api/browser/board", headers={"X-Platz-Token": token})


@pytest.fixture
def d(client) -> str:
    return oeffnen(client, PLATZ)


def test_board_je_platz_token_hoechstens_zwei_je_sekunde_mit_schub(client, d, uhr):
    token = _token(client)
    stati = [_board(client, token).status_code for _ in range(7)]
    assert stati == [200] * 5 + [429] * 2
    r = _board(client, token)
    assert r.json() == {"fehler": "quote_erreicht", "text": "Zu viele Anfragen, bitte später."} and r.headers["retry-after"] == "1"
    uhr.vor(1)
    assert [_board(client, token).status_code for _ in range(3)] == [200, 200, 429]


def test_board_rate_gilt_je_platz(client, d):
    oeffnen(client, 4)
    t3, t4 = _token(client, 3), _token(client, 4)
    for _ in range(8):
        _board(client, t3)
    assert _board(client, t3).status_code == 429 and _board(client, t4).status_code == 200


def test_board_globale_grenze(client, d, uhr):
    oeffnen(client, 4)
    t3, t4 = _token(client, 3), _token(client, 4)
    client.app.state.dienst.board_global = Begrenzer(rate=1, schub=3, uhr=uhr)
    assert [_board(client, t3).status_code for _ in range(2)] == [200, 200]
    assert _board(client, t4).status_code == 200
    r = _board(client, t4)
    assert r.status_code == 429 and r.json()["fehler"] == "quote_erreicht"  # der Eimer des Platzes ist noch voll, die globale Grenze greift


def test_board_wird_eine_sekunde_zwischengespeichert_und_bei_aenderung_frisch(client, d, uhr, monkeypatch):
    token = _token(client)
    aufrufe: list[int] = []
    original = PlatzDB.board

    def gezaehlt(self):
        aufrufe.append(1)
        return original(self)

    monkeypatch.setattr(PlatzDB, "board", gezaehlt)
    erst, zweit = _board(client, token), _board(client, token)
    assert erst.content == zweit.content and len(aufrufe) == 1  # die zweite Antwort kommt aus dem Zwischenspeicher
    nummer = anlegen_http(client, d).json()["nummer"]  # jede Änderung macht den Zwischenspeicher ungültig
    assert nummer in _board(client, token).text and len(aufrufe) == 2
    _board(client, token)
    assert len(aufrufe) == 2
    uhr.vor(1.5)
    _board(client, token)
    assert len(aufrufe) == 3  # nach einer Sekunde wird neu gelesen


def test_betreuer_aktionen_machen_den_zwischenspeicher_ungueltig(client, d):
    token = _token(client)
    assert _board(client, token).json()["durchlauf"]["offen"] is True
    client.post(f"/api/betreuer/plaetze/{PLATZ}/durchlauf-beenden", headers=betreuer_kopf())
    assert _board(client, token).json()["durchlauf"]["offen"] is False
    client.post(f"/api/betreuer/plaetze/{PLATZ}/zuruecksetzen", headers=betreuer_kopf())
    assert _board(client, token).json()["tickets"][0]["nummer"] == "LAB-11"


# ---------- unbekannter Token ohne Datenbank und ohne Thread ----------
def test_unbekannter_token_braucht_weder_datenbank_noch_thread_aus_dem_pool(client, d, monkeypatch):
    def verboten(*_a, **_k):
        raise AssertionError("kein Thread und keine Datenbank für einen unbekannten Token")

    monkeypatch.setattr(browser_api, "run_in_threadpool", verboten)
    from wamo_labor_dienst.datenbank import ZentralDB

    monkeypatch.setattr(ZentralDB, "platz_von_hash", verboten)
    for token in ("0" * 32, "f" * 32, "abc", ""):
        assert client.get("/api/browser/board", headers={"X-Platz-Token": token}).status_code == 401


def test_token_index_folgt_vergabe_entzug_und_neustart(konfig, uhr):
    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:
        oeffnen(c, PLATZ)
        token = _token(c)
        assert _board(c, token).status_code == 200
        assert _board(c, "0" * 32).status_code == 401
    with TestClient(create_app(konfig, uhr=uhr), base_url="http://127.0.0.1") as c:  # Neustart: der Index wird aus der Datei geladen
        assert _board(c, token).status_code == 200
        oeffnen(c, PLATZ)
        assert _board(c, token).status_code == 401


# ---------- Fehlversuche je Quelle ----------
def _mit_proxys(konfig, uhr, proxys):
    k = Konfig(daten=konfig.daten, kern_token=KERN_TOKEN, betreuer_token=BETREUER_TOKEN, proxys=frozenset(proxys))
    return TestClient(create_app(k, uhr=uhr), base_url="http://127.0.0.1", raise_server_exceptions=False)


def test_fehlversuche_je_quelle_werden_begrenzt_gueltige_zugaenge_nie(client, uhr):
    stati = [client.get("/api/kern/durchlauf", headers={"Authorization": "Bearer falsch", "X-WAMO-Platz": "3"}).status_code for _ in range(125)]
    assert stati[:120] == [401] * 120 and stati[120:] == [429] * 5
    r = client.get("/api/kern/durchlauf", headers={"Authorization": "Bearer falsch", "X-WAMO-Platz": "3"})
    assert r.json() == {"fehler": "quote_erreicht", "text": "Zu viele Fehlversuche, bitte später."} and r.headers["retry-after"] == "60"
    assert client.get("/api/kern/durchlauf", headers=kern_kopf()).status_code == 200  # wer den richtigen Zugang hat, wird nie ausgesperrt
    assert client.get("/api/betreuer/plaetze", headers=betreuer_kopf()).status_code == 200
    uhr.vor(61)
    assert client.get("/api/kern/durchlauf", headers={"Authorization": "Bearer falsch", "X-WAMO-Platz": "3"}).status_code == 401


def test_fehlversuche_aller_drei_zugaenge_zaehlen_zusammen(client):
    for i in range(120):
        [lambda: client.get("/api/kern/durchlauf", headers={"Authorization": "Bearer x", "X-WAMO-Platz": "3"}),
         lambda: client.get("/api/browser/board", headers={"X-Platz-Token": "0" * 32}),
         lambda: client.get("/api/betreuer/plaetze", headers={"Authorization": "Bearer y"})][i % 3]()
    assert client.get("/api/browser/board", headers={"X-Platz-Token": "0" * 32}).status_code == 429
    assert client.get("/api/betreuer/plaetze", headers={"Authorization": "Bearer y"}).status_code == 429


def test_quelle_kommt_von_der_verbindung_ein_weitergabekopf_zaehlt_ohne_vertrauten_proxy_nicht(client):
    for i in range(125):
        r = client.get("/api/kern/durchlauf", headers={"Authorization": "Bearer x", "X-WAMO-Platz": "3", "X-Forwarded-For": f"10.0.0.{i}"})
    assert r.status_code == 429  # der Absender kann sich nicht mit einem Kopf eine neue Quelle geben


def test_hinter_vertrautem_proxy_zaehlt_die_adresse_aus_dem_weitergabekopf(konfig, uhr):
    with _mit_proxys(konfig, uhr, {"testclient"}) as c:
        kopf = {"Authorization": "Bearer x", "X-WAMO-Platz": "3"}
        stati = [c.get("/api/kern/durchlauf", headers={**kopf, "X-Forwarded-For": "203.0.113.7"}).status_code for _ in range(125)]
        assert stati[-1] == 429
        assert c.get("/api/kern/durchlauf", headers={**kopf, "X-Forwarded-For": "198.51.100.9"}).status_code == 401  # andere Quelle, eigene Zählung
        assert c.get("/api/kern/durchlauf", headers={**kopf, "X-Forwarded-For": "1.1.1.1, 203.0.113.7"}).status_code == 429  # zählt, was der Proxy sah
        assert c.get("/api/kern/durchlauf", headers={**kopf, "X-Forwarded-For": "203.0.113.7"}, ).status_code == 429


def test_proxys_aus_der_umgebung(tmp_path):
    kern, betreuer = tmp_path / "k", tmp_path / "b"
    kern.write_text(KERN_TOKEN, encoding="utf-8")
    betreuer.write_text(BETREUER_TOKEN, encoding="utf-8")
    env = {"WAMO_LABOR_DIENST_KERN_TOKEN_DATEI": str(kern), "WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI": str(betreuer)}
    assert aus_umgebung(env).proxys == frozenset()
    assert aus_umgebung({**env, "WAMO_LABOR_DIENST_PROXYS": "172.18.0.2, ::1"}).proxys == frozenset({"172.18.0.2", "::1"})
    for falsch in ("keine-adresse", "172.18.0.2,", "300.1.1.1"):
        with pytest.raises(KonfigFehler, match="WAMO_LABOR_DIENST_PROXYS"):
            aus_umgebung({**env, "WAMO_LABOR_DIENST_PROXYS": falsch})


# ---------- Verbindungen und Fristen (echter Server) ----------
def _dienst(tmp_path, **einstellung):
    takt = Uhr(LABORTAG)
    konfig = Konfig(daten=tmp_path / "daten", kern_token=KERN_TOKEN, betreuer_token=BETREUER_TOKEN)
    return laufender_dienst(create_app(konfig, uhr=takt), **einstellung)


def _verbinden(adresse: str) -> socket.socket:
    host, port = adresse.removeprefix("http://").split(":")
    s = socket.create_connection((host, int(port)), timeout=5)
    return s


def _antwort(s: socket.socket) -> bytes:
    """Eine vollständige Antwort (Köpfe und Inhalt nach Content-Length) von einer offenen Verbindung."""
    s.settimeout(5)
    roh = b""
    while b"\r\n\r\n" not in roh:
        stueck = s.recv(4096)
        if not stueck:
            return roh
        roh += stueck
    kopf, _, rest = roh.partition(b"\r\n\r\n")
    laenge = next((int(z.split(b":")[1]) for z in kopf.split(b"\r\n") if z.lower().startswith(b"content-length:")), 0)
    while len(rest) < laenge:
        stueck = s.recv(4096)
        if not stueck:
            break
        rest += stueck
    return kopf + b"\r\n\r\n" + rest


def _lesen_bis_ende(s: socket.socket, frist: float = 5.0) -> bytes:
    s.settimeout(frist)
    teile = b""
    try:
        while True:
            stueck = s.recv(4096)
            if not stueck:
                return teile
            teile += stueck
    except ConnectionError:
        return teile  # vom Server geschlossen (auf manchen Systemen als Zurücksetzen gemeldet)
    except TimeoutError:
        return teile + b"<Zeit>"


def test_einstellungen_des_servers_haben_obergrenze_und_frist():
    e = server.uvicorn_einstellungen()
    assert e["limit_concurrency"] == 64 and e["http"] is server.FristH11Protocol and e["timeout_keep_alive"] == 5 and e["access_log"] is False


def test_kopfzeilen_die_zu_langsam_ankommen_schliessen_die_verbindung(tmp_path, monkeypatch):
    monkeypatch.setattr(server.FristH11Protocol, "lese_frist", 0.6)
    with _dienst(tmp_path) as adresse:
        langsam = _verbinden(adresse)
        langsam.sendall(b"GET /gesundheit HTTP/1.1\r\nHost: x\r\nX-Langsam: a")  # unvollständig, und es kommt nichts mehr
        stumm = _verbinden(adresse)  # verbunden, aber nie eine Anfrage
        start = time.monotonic()
        assert _lesen_bis_ende(langsam) == b"" and _lesen_bis_ende(stumm) == b""  # beide wurden vom Server geschlossen
        assert time.monotonic() - start < 3
        ok = _verbinden(adresse)  # eine rechtzeitige Anfrage geht durch, auch mehrere auf derselben Verbindung
        for _ in range(2):
            ok.sendall(b"GET /gesundheit HTTP/1.1\r\nHost: x\r\n\r\n")
            assert b'"ok"' in _antwort(ok)
            time.sleep(0.2)
        ok.close()


def test_tropfenweise_koepfe_ueberziehen_die_frist_auch_wenn_immer_wieder_etwas_kommt(tmp_path, monkeypatch):
    monkeypatch.setattr(server.FristH11Protocol, "lese_frist", 0.8)
    with _dienst(tmp_path) as adresse:
        s = _verbinden(adresse)
        s.sendall(b"GET /gesundheit HTTP/1.1\r\n")
        geschlossen = False
        for _ in range(12):
            time.sleep(0.2)
            try:
                s.sendall(b"X-A: b\r\n")
            except OSError:
                geschlossen = True
                break
        assert geschlossen or _lesen_bis_ende(s, 1.0) == b""


def test_frist_gilt_nach_einer_antwort_erneut_fuer_die_naechste_anfrage(tmp_path, monkeypatch):
    monkeypatch.setattr(server.FristH11Protocol, "lese_frist", 0.6)
    with _dienst(tmp_path) as adresse:
        s = _verbinden(adresse)
        s.sendall(b"GET /gesundheit HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b'"ok"' in _antwort(s)
        s.sendall(b"GET /gesundheit HTTP/1.1\r\nHost: x\r\nX-Halb: a")  # die zweite Anfrage bleibt stecken
        assert _lesen_bis_ende(s) == b""


def test_mehr_als_die_erlaubte_zahl_von_verbindungen_ergibt_503(tmp_path):
    with _dienst(tmp_path, limit_concurrency=3) as adresse:
        halten = [_verbinden(adresse) for _ in range(3)]
        time.sleep(0.2)
        s = _verbinden(adresse)
        s.sendall(b"GET /gesundheit HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b" 503 " in _antwort(s).split(b"\r\n")[0] + b" "
        for h in halten:
            h.close()
        time.sleep(0.3)
        ok = _verbinden(adresse)
        ok.sendall(b"GET /gesundheit HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b'"ok"' in _antwort(ok)


def test_inhalt_der_zu_langsam_ankommt_ist_408(tmp_path, monkeypatch):
    monkeypatch.setattr(sicherheit, "KOERPER_FRIST_SECS", 0.5)
    with _dienst(tmp_path) as adresse:
        s = _verbinden(adresse)
        kopf = (f"POST /api/kern/tickets HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {KERN_TOKEN}\r\nX-WAMO-Platz: 3\r\n"
                "Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{").encode()
        s.sendall(kopf)  # der Rest kommt nie
        antwort = _lesen_bis_ende(s, 4.0)
        assert b" 408 " in antwort.split(b"\r\n")[0] + b" " and b'"fehler": "eingabe_ungueltig"' in antwort
