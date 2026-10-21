"""Socket-Server: fremde uid bekommt nichts, Anfrage und Antwort mit Anhang, kaputte Anfragen, Selbsttest."""

import asyncio
import json
import uuid

from wamo_geraete import server as s
from wamo_geraete.ablage import Ablage
from wamo_geraete.audit import Audit
from wamo_geraete.dienst import Dienst
from wamo_geraete.konfig import aus_daten


class Schreiber:
    def __init__(self):
        self.daten = b""
        self.zu = False

    def get_extra_info(self, name):
        return object() if name == "socket" else None

    def write(self, d):
        self.daten += d

    async def drain(self):
        pass

    def close(self):
        self.zu = True

    async def wait_closed(self):
        pass


def _leser(daten: bytes) -> asyncio.StreamReader:
    r = asyncio.StreamReader()
    r.feed_data(daten)
    r.feed_eof()
    return r


def _dienst(tmp_path):
    return Dienst(aus_daten({"geraet": []}), ablage=Ablage(str(tmp_path / "a")), audit=Audit(None))


async def test_fremde_uid_wird_nicht_gelesen(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "peer_uid", lambda _sock: 1000)
    w = Schreiber()
    gelesen = _leser(b'{"v": 1, "werkzeug": "ping"}\n')
    await s.Server(_dienst(tmp_path), kern_uid=991).verbindung(gelesen, w)
    assert w.daten == b"" and w.zu is True


async def test_anfrage_und_anhang(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "peer_uid", lambda _sock: 991)
    dienst = _dienst(tmp_path)
    uid = str(uuid.uuid4())
    e = dienst.ablage.ablegen(uid, "scan", "a.pdf", "application/pdf", b"%PDF-xy", "scanner")
    anfrage = {"v": 1, "werkzeug": "ablage_holen", "argumente": {"id": e.id},
               "kontext": {"user_id": uid, "rolle": "mitglied"}}
    w = Schreiber()
    await s.Server(dienst, kern_uid=991).verbindung(_leser(json.dumps(anfrage).encode() + b"\n"), w)
    kopf, _, rest = w.daten.partition(b"\n")
    assert json.loads(kopf)["anhang"] == 7 and rest == b"%PDF-xy"


async def test_kaputte_anfragen(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "peer_uid", lambda _sock: 991)
    for roh in (b"kein json\n", b'{"v": 1}', b"\xff\xfe\n"):
        w = Schreiber()
        await s.Server(_dienst(tmp_path), kern_uid=991).verbindung(_leser(roh), w)
        assert json.loads(w.daten.split(b"\n")[0])["status"] == "rejected"


async def test_selbsttest_erkennt_offenen_ausgang():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    async with server:
        offen = await s.selbsttest_ausgang(("127.0.0.1", server.sockets[0].getsockname()[1]))
    assert offen is False  # erreichbar: Selbsttest schlägt an
    assert await s.selbsttest_ausgang(("127.0.0.1", 1)) is True
