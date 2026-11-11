"""Tests des Dienstclients (labor_client.py) gegen die Attrappe und gegen kaputte Dienste (httpx.MockTransport).

Der Client ruft nur V1 bis V7 auf, nie etwas Löschendes, trägt durchlauf_id und Idempotenzschlüssel in jedem Schreibaufruf, zeigt dem Aufrufer
nur feste Fehlercodes und nimmt nie die Adresse der Attrappe aus der Umgebung.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import pathlib
import time
import uuid

import httpx
import pytest

import labor_client
from labor_attrappe import ATTRAPPE_URL, Attrappe
from labor_client import LaborClient, LaborFehler, LaborKonfigFehler, client_aus_umgebung
from labor_vertrag import ATTRAPPE_HOST, FEHLER_SAETZE

HIER = pathlib.Path(__file__).parent
TOKEN = "t" * 43


def mit_attrappe(platz=3, **kw):
    app = Attrappe()
    client = LaborClient(ATTRAPPE_URL, app.token, platz, transport=httpx.ASGITransport(app=app), **kw)
    return app, client


class Aufzeichnung(httpx.AsyncBaseTransport):
    """Reicht an die Attrappe weiter und merkt sich jede Anfrage."""

    def __init__(self, app, vor=None):
        self.inner = httpx.ASGITransport(app=app)
        self.anfragen: list[httpx.Request] = []
        self.vor = vor

    async def handle_async_request(self, request):
        self.anfragen.append(request)
        if self.vor is not None:
            self.vor(request)
        return await self.inner.handle_async_request(request)


def mock(antwort):
    return LaborClient("https://dienst.example", TOKEN, 3, transport=httpx.MockTransport(antwort))


def json_antwort(status, obj, **kopf):
    return httpx.Response(status, json=obj, headers=kopf)


# ---------- V1 bis V7 ----------
async def test_v1_durchlauf():
    app, c = mit_attrappe()
    d = await c.durchlauf()
    assert d.id == app.durchlauf_id(3) and d.offen is True and d.einwilligung_version == 1 and d.einwilligung_zeit.endswith("Z")
    app.neuer_durchlauf(3, einwilligung_version=None)
    d = await c.durchlauf()
    assert d.einwilligung_version is None and d.einwilligung_zeit is None


async def test_v2_v3_lesen():
    app, c = mit_attrappe()
    liste = await c.tickets(limit=3)
    assert liste["gesamt"] == 6 and [t["nummer"] for t in liste["tickets"]] == ["LAB-25", "LAB-20", "LAB-17"]
    t = await c.ticket("LAB-12")
    assert t["nummer"] == "LAB-12" and t["version"] == 1 and t["kommentare"] == []
    for alle in (await c.tickets(q="100%"), await c.tickets(nummer="LAB-12", status="in_arbeit", typ="bug", prioritaet="hoch")):
        assert isinstance(alle["gesamt"], int)
    with pytest.raises(LaborFehler) as e:
        await c.ticket("LAB-99")
    assert e.value.code == "ticket_unbekannt" and e.value.satz == FEHLER_SAETZE["ticket_unbekannt"]


async def test_v4_bis_v7_schreiben():
    app, c = mit_attrappe()
    d = app.durchlauf_id(3)
    t = await c.anlegen(durchlauf_id=d, schluessel=str(uuid.uuid4()), typ="bug", prioritaet="hoch", titel="Neuer Fehler", beschreibung="Text")
    assert t["nummer"] == "LAB-30" and t["version"] == 1 and t["im_durchlauf"] is True
    t = await c.aendern(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=1, felder={"titel": "Anderer Titel"})
    assert t["titel"] == "Anderer Titel" and t["version"] == 2
    t = await c.status_setzen(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=2, status="erledigt")
    assert t["status"] == "erledigt" and t["version"] == 3
    k = await c.kommentieren(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=3, text="Ein Kommentar")
    assert k["kommentar_id"] == 1 and k["ticket"]["version"] == 4


async def test_jeder_schreibaufruf_traegt_durchlauf_platz_token_und_schluessel():
    app = Attrappe()
    rec = Aufzeichnung(app)
    c = LaborClient(ATTRAPPE_URL, app.token, 5, transport=rec)
    d, s = app.durchlauf_id(5), str(uuid.uuid4())
    await c.anlegen(durchlauf_id=d, schluessel=s, typ="bug", prioritaet="hoch", titel="Drei", beschreibung="")
    await c.aendern(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-12", erwartete_version=1, felder={"titel": "Vier"})
    await c.status_setzen(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-12", erwartete_version=2, status="erledigt")
    await c.kommentieren(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-12", erwartete_version=3, text="x")
    assert [r.method for r in rec.anfragen] == ["POST", "PATCH", "POST", "POST"]
    for r in rec.anfragen:
        assert r.headers["x-wamo-durchlauf"] == d and r.headers["x-wamo-platz"] == "5"
        assert r.headers["authorization"] == f"Bearer {app.token}" and uuid.UUID(r.headers["idempotency-key"])
        assert r.headers["content-type"] == "application/json"
    assert rec.anfragen[0].headers["idempotency-key"] == s
    assert len({r.headers["idempotency-key"] for r in rec.anfragen}) == 4


async def test_nur_get_post_patch_und_nur_pfade_des_vertrags():
    app = Attrappe()
    rec = Aufzeichnung(app)
    c = LaborClient(ATTRAPPE_URL, app.token, 3, transport=rec)
    d = app.durchlauf_id(3)
    await c.durchlauf()
    await c.tickets()
    await c.ticket("LAB-12")
    await c.anlegen(durchlauf_id=d, schluessel=str(uuid.uuid4()), typ="bug", prioritaet="hoch", titel="Drei", beschreibung="")
    await c.aendern(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=1, felder={"titel": "Vier"})
    await c.status_setzen(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=2, status="erledigt")
    await c.kommentieren(durchlauf_id=d, schluessel=str(uuid.uuid4()), nummer="LAB-30", erwartete_version=3, text="x")
    assert {r.method for r in rec.anfragen} == {"GET", "POST", "PATCH"}
    for r in rec.anfragen:
        assert r.url.path.startswith("/api/kern/") and r.url.host == ATTRAPPE_HOST


def test_kein_loeschweg_im_client():
    namen = {n.lower() for n in dir(LaborClient) if not n.startswith("__")}
    assert not [n for n in namen if any(w in n for w in ("delete", "loesch", "remove", "entfern", "drop", "truncate"))]
    quelle = inspect.getsource(labor_client).lower()
    assert '"delete"' not in quelle and ".delete(" not in quelle and "x-http-method-override" not in quelle


async def test_zu_grosse_anfrage_wird_nicht_gesendet():
    app = Attrappe()
    rec = Aufzeichnung(app)
    c = LaborClient(ATTRAPPE_URL, app.token, 3, transport=rec)
    with pytest.raises(LaborFehler) as e:
        await c.anlegen(durchlauf_id=app.durchlauf_id(3), schluessel=str(uuid.uuid4()), typ="bug", prioritaet="hoch", titel="Drei",
                        beschreibung="x" * 17000)
    assert e.value.code == "zu_gross" and rec.anfragen == []


async def test_ungueltige_nummer_und_ungueltige_kennungen_gehen_nie_raus():
    app = Attrappe()
    rec = Aufzeichnung(app)
    c = LaborClient(ATTRAPPE_URL, app.token, 3, transport=rec)
    for nummer in ("LAB-0", "lab-1", "LAB-1/../x", "LAB-1?x=1", "../tickets", "LAB-12 ", ""):
        with pytest.raises(LaborFehler) as e:
            await c.ticket(nummer)
        assert e.value.code == "eingabe_ungueltig"
    with pytest.raises(LaborFehler):
        await c.status_setzen(durchlauf_id="kein-uuid", schluessel=str(uuid.uuid4()), nummer="LAB-12", erwartete_version=1, status="erledigt")
    with pytest.raises(LaborFehler):
        await c.status_setzen(durchlauf_id=app.durchlauf_id(3), schluessel="x", nummer="LAB-12", erwartete_version=1, status="erledigt")
    with pytest.raises(LaborFehler):
        await c.status_setzen(durchlauf_id=app.durchlauf_id(3), schluessel=str(uuid.uuid4()), nummer="LAB-12", erwartete_version=0, status="erledigt")
    assert rec.anfragen == []


def test_platz_muss_1_bis_16_sein():
    for platz in (0, 17, -1, "3", None, True, 3.0):
        with pytest.raises(ValueError):
            LaborClient(ATTRAPPE_URL, TOKEN, platz)


# ---------- Fehler ----------
@pytest.mark.parametrize("status,code", [(401, "token_ungueltig"), (404, "platz_unbekannt"), (404, "ticket_unbekannt"), (409, "durchlauf_beendet"),
                                         (409, "ticket_veraendert"), (422, "eingabe_ungueltig"), (413, "zu_gross"), (429, "quote_erreicht"),
                                         (405, "nicht_erlaubt"), (500, "dienst_fehler")])
async def test_fehlercodes_werden_zu_festen_saetzen(status, code):
    c = mock(lambda r: json_antwort(status, {"fehler": code, "text": "Interner Pfad /srv/x token=abc"}))
    with pytest.raises(LaborFehler) as e:
        await c.durchlauf()
    assert e.value.code == code and e.value.satz == FEHLER_SAETZE[code] and "abc" not in e.value.satz and "/srv" not in str(e.value)


async def test_unbekannter_code_und_unlesbare_antworten():
    for antwort in (json_antwort(400, {"fehler": "etwas_neues", "text": "x"}), httpx.Response(400, text="<html>"), httpx.Response(302, headers={"location": "https://boese.example/"}),
                    httpx.Response(204), httpx.Response(200, text="kein json"), json_antwort(200, [1, 2]), json_antwort(200, {"durchlauf_id": 5})):
        c = mock(lambda r, a=antwort: a)
        with pytest.raises(LaborFehler) as e:
            await c.durchlauf()
        assert e.value.code in ("antwort_ungueltig", "dienst_fehler"), antwort


async def test_umleitung_wird_nie_gefolgt_und_der_token_geht_nirgends_anders_hin():
    ziele = []

    def antwort(request):
        ziele.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://boese.example/api/kern/durchlauf"})

    c = mock(antwort)
    with pytest.raises(LaborFehler):
        await c.durchlauf()
    assert ziele == ["dienst.example"]


async def test_antwort_ueber_64_kb_wird_abgelehnt():
    c = mock(lambda r: httpx.Response(200, content=b'{"x": "' + b"a" * 70000 + b'"}', headers={"content-type": "application/json"}))
    with pytest.raises(LaborFehler) as e:
        await c.durchlauf()
    assert e.value.code == "antwort_ungueltig"


async def test_ticket_mit_falscher_form_wird_abgelehnt():
    gut = {"nummer": "LAB-12", "titel": "Titel", "typ": "bug", "prioritaet": "hoch", "status": "in_arbeit", "beschreibung": "", "version": 1, "im_durchlauf": False}
    for kaputt in ({**gut, "typ": "fehler"}, {**gut, "prioritaet": "p1"}, {**gut, "status": "offen"}, {**gut, "nummer": "XYZ-1"}, {**gut, "version": 0},
                   {**gut, "version": "1"}, {**gut, "version": True}, {**gut, "titel": 5}, {**gut, "titel": "x" * 121}, {**gut, "beschreibung": "x" * 1501},
                   {**gut, "im_durchlauf": "ja"}, {**gut, "extra": 1}, {k: v for k, v in gut.items() if k != "titel"}):
        c = mock(lambda r, k=kaputt: json_antwort(200, {**k, "kommentare": []}))
        with pytest.raises(LaborFehler) as e:
            await c.ticket("LAB-12")
        assert e.value.code == "antwort_ungueltig", kaputt
    c = mock(lambda r: json_antwort(200, {**gut, "kommentare": []}))
    assert (await c.ticket("LAB-12"))["nummer"] == "LAB-12"


async def test_text_des_dienstes_wird_normiert():
    t = {"nummer": "LAB-12", "titel": "Ti\u202etel", "typ": "bug", "prioritaet": "hoch", "status": "in_arbeit", "beschreibung": "a\x00b", "version": 1,
         "im_durchlauf": False, "kommentare": [{"kommentar_id": 1, "text": "k\u200bk"}]}
    got = await mock(lambda r: json_antwort(200, t)).ticket("LAB-12")
    assert got["titel"] == "Ti tel" and got["beschreibung"] == "a b" and got["kommentare"] == [{"kommentar_id": 1, "text": "k k"}]


async def test_zeitueberschreitung_und_verbindungsfehler():
    def aus(r):
        raise httpx.ReadTimeout("zu langsam", request=r)

    def weg(r):
        raise httpx.ConnectError("keine Verbindung /srv/geheim", request=r)

    for handler, code in ((aus, "zeitueberschreitung"), (weg, "nicht_erreichbar")):
        with pytest.raises(LaborFehler) as e:
            await mock(handler).durchlauf()
        assert e.value.code == code and "geheim" not in str(e.value)


async def test_fristen_5_und_8_sekunden():
    gesehen = []

    def antwort(request):
        gesehen.append(request.extensions["timeout"])
        return json_antwort(200, {"durchlauf_id": str(uuid.uuid4()), "offen": True, "einwilligung_version": None, "einwilligung_zeit": None}) \
            if request.method == "GET" else json_antwort(500, {"fehler": "dienst_fehler", "text": "x"})

    c = mock(antwort)
    await c.durchlauf()
    with pytest.raises(LaborFehler):
        await c.status_setzen(durchlauf_id=str(uuid.uuid4()), schluessel=str(uuid.uuid4()), nummer="LAB-12", erwartete_version=1, status="erledigt")
    assert gesehen[0]["read"] == 5.0 and gesehen[1]["read"] == 8.0


# ---------- Wiederholen nur mit demselben Schlüssel ----------
async def test_lesen_wird_nicht_wiederholt_schreiben_einmal_mit_demselben_schluessel():
    lese, schreib = [], []

    def antwort(request):
        (lese if request.method == "GET" else schreib).append(request.headers.get("idempotency-key"))
        return json_antwort(500, {"fehler": "dienst_fehler", "text": "x"})

    c = mock(antwort)
    with pytest.raises(LaborFehler) as e:
        await c.durchlauf()
    assert e.value.code == "dienst_fehler" and len(lese) == 1
    s = str(uuid.uuid4())
    with pytest.raises(LaborFehler) as e:
        await c.anlegen(durchlauf_id=str(uuid.uuid4()), schluessel=s, typ="bug", prioritaet="hoch", titel="Drei", beschreibung="")
    assert e.value.code == "dienst_fehler" and schreib == [s, s]


async def test_verlorene_antwort_ergibt_kein_doppeltes_ticket():
    """Der Dienst führt aus, die Antwort geht verloren (Zeitüberschreitung): die Wiederholung mit demselben Schlüssel liefert dieselbe Antwort."""
    app = Attrappe()
    verloren = []

    class Verlust(httpx.AsyncBaseTransport):
        inner = httpx.ASGITransport(app=app)

        async def handle_async_request(self, request):
            antwort = await self.inner.handle_async_request(request)
            if request.method == "POST" and not verloren:
                verloren.append(1)
                raise httpx.ReadTimeout("Antwort verloren", request=request)
            return antwort

    c = LaborClient(ATTRAPPE_URL, app.token, 3, transport=Verlust())
    t = await c.anlegen(durchlauf_id=app.durchlauf_id(3), schluessel=str(uuid.uuid4()), typ="bug", prioritaet="hoch", titel="Nur einmal", beschreibung="")
    assert t["nummer"] == "LAB-30" and len(app.tickets(3)) == 7


async def test_409_wird_nicht_wiederholt():
    n = []

    def antwort(request):
        n.append(1)
        return json_antwort(409, {"fehler": "ticket_veraendert", "text": "x"})

    with pytest.raises(LaborFehler) as e:
        await mock(antwort).status_setzen(durchlauf_id=str(uuid.uuid4()), schluessel=str(uuid.uuid4()), nummer="LAB-12", erwartete_version=1, status="erledigt")
    assert e.value.code == "ticket_veraendert" and len(n) == 1


# ---------- Einstellung ----------
def _env(monkeypatch, url="https://192.168.178.136:55447", token=TOKEN):
    for name, wert in (("WAMO_LABOR_URL", url), ("WAMO_LABOR_TOKEN", token)):
        if wert is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, wert)


def test_client_aus_der_umgebung(monkeypatch):
    _env(monkeypatch)
    c = client_aus_umgebung(4)
    assert isinstance(c, LaborClient) and c.platz == 4
    assert TOKEN not in repr(c) and TOKEN not in str(vars(c).get("url", ""))
    _env(monkeypatch, url="http://127.0.0.1:8080/")
    assert client_aus_umgebung(4) is not None  # Loopback darf Klartext


@pytest.mark.parametrize("url", [
    None, "", "labor", "ftp://dienst.example", "http://dienst.example", "http://192.168.178.136:55447", "https://", "https://u:p@dienst.example",
    "https://dienst.example/?x=1", "https://dienst.example/#a", "https://dienst.example/api/kern", f"https://{ATTRAPPE_HOST}",
    f"https://{ATTRAPPE_HOST.upper()}", f"https://{ATTRAPPE_HOST}.", f"https://x.{ATTRAPPE_HOST}:8443", f"https://{ATTRAPPE_HOST}:443/", ATTRAPPE_URL,
])
def test_falsche_adressen_und_die_attrappe_werden_abgelehnt(monkeypatch, url):
    _env(monkeypatch, url=url)
    with pytest.raises(LaborKonfigFehler):
        client_aus_umgebung(4)


@pytest.mark.parametrize("token", [None, "", "kurz", "x" * 31, "a b" * 20, "t" * 43 + "\n", "ä" * 40, "t" * 300])
def test_falscher_token_wird_abgelehnt_ohne_ihn_zu_nennen(monkeypatch, token):
    _env(monkeypatch, token=token)
    with pytest.raises(LaborKonfigFehler) as e:
        client_aus_umgebung(4)
    assert (token or "zz") not in str(e.value) or token in (None, "")


@pytest.fixture
def frisches_vertrauen():
    """Der Zwischenspeicher des SSL-Kontexts gehört dem Prozess: jeder Test fängt leer an und hinterlässt nichts."""
    labor_client.vertrauen_leeren()
    yield
    labor_client.vertrauen_leeren()


def _verify_je_client(monkeypatch, anzahl: int) -> list:
    """Baut anzahl Clients aus der Umgebung und liefert das verify, das jeder bekommen hat (LaborClient wird nur beobachtet)."""
    gesehen = []
    echt = labor_client.LaborClient

    def beobachten(*a, **kw):
        gesehen.append(kw["verify"])
        return echt(*a, **kw)

    monkeypatch.setattr(labor_client, "LaborClient", beobachten)
    for _ in range(anzahl):
        client_aus_umgebung(4)
    return gesehen


def test_der_ssl_kontext_wird_einmal_gebaut_und_geteilt_ohne_ca(monkeypatch, frisches_vertrauen):
    """Befund 5: jeder Client baute seinen SSL-Kontext neu (etwa 7 ms auf der Ereignisschleife des Sprachpfads, je Wache-Takt, /start und Offer)."""
    import ssl

    _env(monkeypatch)
    monkeypatch.delenv("WAMO_CA_FILE", raising=False)
    echt = ssl.create_default_context
    aufrufe = []
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **kw: aufrufe.append(1) or echt(*a, **kw))
    verify = _verify_je_client(monkeypatch, 10)
    assert len(aufrufe) == 1  # einmal für alle zehn
    assert isinstance(verify[0], ssl.SSLContext) and all(v is verify[0] for v in verify)  # und wirklich derselbe Kontext, nie das ungeprüfte True
    assert verify[0].verify_mode == ssl.CERT_REQUIRED and verify[0].check_hostname  # und er prüft Zertifikat und Rechnername wie der Standard von httpx


def test_der_ssl_kontext_mit_der_wamo_ca_wird_einmal_gebaut_und_geteilt(monkeypatch, frisches_vertrauen):
    import ssl

    import certifi
    import wamo_tls

    _env(monkeypatch)
    monkeypatch.setenv("WAMO_CA_FILE", certifi.where())  # eine gültige PEM-Datei mit Zertifikaten genügt
    echt = wamo_tls.https_context
    aufrufe = []
    monkeypatch.setattr(wamo_tls, "https_context", lambda: aufrufe.append(1) or echt())
    verify = _verify_je_client(monkeypatch, 10)
    assert len(aufrufe) == 1
    assert isinstance(verify[0], ssl.SSLContext) and all(v is verify[0] for v in verify)


def test_anderer_ca_pfad_gibt_einen_neuen_kontext_und_ein_fehler_wird_nicht_gemerkt(monkeypatch, frisches_vertrauen, tmp_path):
    import certifi

    _env(monkeypatch)
    monkeypatch.setenv("WAMO_CA_FILE", str(tmp_path / "fehlt.pem"))
    for _ in range(2):  # ein unbrauchbarer Pfad ist jedes Mal ein Fehler (nichts Halbes im Speicher)
        with pytest.raises(LaborKonfigFehler):
            client_aus_umgebung(4)
    monkeypatch.setenv("WAMO_CA_FILE", certifi.where())
    erster = _verify_je_client(monkeypatch, 1)[0]  # ab jetzt geht es
    monkeypatch.delenv("WAMO_CA_FILE")
    ohne = _verify_je_client(monkeypatch, 1)[0]
    assert ohne is not erster  # der Pfad gehört zum Schlüssel: ohne CA gilt ein anderer Kontext


@pytest.mark.asyncio
async def test_die_quelle_der_wache_baut_den_kontext_nicht_je_frage_neu(monkeypatch, frisches_vertrauen):
    import ssl

    import labor_durchlauf

    app = Attrappe()
    _env(monkeypatch, token=app.token)
    monkeypatch.delenv("WAMO_CA_FILE", raising=False)
    echt = ssl.create_default_context
    aufrufe = []
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **kw: aufrufe.append(1) or echt(*a, **kw))
    quelle = labor_durchlauf.ClientQuelle(transport=httpx.ASGITransport(app=app))
    for _ in range(10):
        assert (await quelle.durchlauf(3))["offen"] is True
    assert len(aufrufe) == 1


def test_nur_die_attrappe_und_der_test_importieren_die_attrappe():
    produktiv = ("labor_client", "labor_vertrag", "labor_freigaben", "labor_ticket", "labor_mail", "labor_werkzeuge", "labor_durchlauf", "labor_sitzung",
                 "labor", "auth_gate", "bot")
    for name in produktiv:
        quelle = (HIER / f"{name}.py").read_text(encoding="utf-8")
        importe = {n.module for n in ast.walk(ast.parse(quelle)) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(ast.parse(quelle)) if isinstance(n, ast.Import) for a in n.names}
        assert "labor_attrappe" not in importe, name


def test_der_name_der_attrappe_steht_nur_in_der_festlegung():
    assert ATTRAPPE_HOST.endswith(".invalid")
    assert json.dumps(sorted(FEHLER_SAETZE))  # die Sätze sind reine Daten


# ---------- Gesamtfrist je Aufruf ----------
class Haengt(httpx.AsyncBaseTransport):
    """Der Dienst antwortet nie (oder tropft): die Fristen von httpx gelten je Schritt, nicht für den ganzen Aufruf."""

    async def handle_async_request(self, request):
        await asyncio.sleep(30)


async def test_jeder_aufruf_hat_eine_gesamtfrist(monkeypatch):
    monkeypatch.setattr("labor_client.VERBINDEN_SECS", 0.05)
    c = LaborClient("https://dienst.example", TOKEN, 3, transport=Haengt(), lese_secs=0.05, schreib_secs=0.05)
    start = time.monotonic()
    with pytest.raises(LaborFehler) as e:
        await c.durchlauf()
    assert e.value.code == "zeitueberschreitung"
    assert time.monotonic() - start < 2


async def test_ein_haengender_schreibaufruf_endet_auch_nach_der_wiederholung(monkeypatch):
    monkeypatch.setattr("labor_client.VERBINDEN_SECS", 0.05)
    c = LaborClient("https://dienst.example", TOKEN, 3, transport=Haengt(), lese_secs=0.05, schreib_secs=0.05)
    start = time.monotonic()
    with pytest.raises(LaborFehler) as e:
        await c.status_setzen(durchlauf_id=str(uuid.uuid4()), schluessel=str(uuid.uuid4()), nummer="LAB-12", erwartete_version=1, status="erledigt")
    assert e.value.code == "zeitueberschreitung"
    assert time.monotonic() - start < 2  # zwei Versuche mit je höchstens 0,1 Sekunden


async def test_ein_tropfender_dienst_haelt_den_aufruf_nicht_ueber_die_frist(monkeypatch):
    """Die Antwort kommt Stück für Stück, jedes Stück innerhalb der Lesefrist von httpx: der Aufruf endet trotzdem nach der Gesamtfrist."""
    monkeypatch.setattr("labor_client.VERBINDEN_SECS", 0.05)

    class Tropft(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(200):
                await asyncio.sleep(0.02)
                yield b" "

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, stream=Tropft())

    c = LaborClient("https://dienst.example", TOKEN, 3, transport=Transport(), lese_secs=0.2)
    start = time.monotonic()
    with pytest.raises(LaborFehler) as e:
        await c.durchlauf()
    assert e.value.code == "zeitueberschreitung" and time.monotonic() - start < 2
