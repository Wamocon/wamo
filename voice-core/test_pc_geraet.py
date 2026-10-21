"""Tests für die Zuordnung der Sitzung zum eigenen Laptop (pc_geraet.py, pc_tunnel.py im Tunnel je Gerät, pc_host.py).

Kern der Sache: Profil A erreicht nie das Gerät von B (anderer Socket, andere Gegenstelle, anderes Token), ein Profil ohne
aktives Gerät bekommt nichts, jede Unstimmigkeit endet ohne PC-Werkzeuge. Die Tests mit echten Unix-Sockets und
Dateirechten laufen nur unter Linux (Sokrates-2 oder WSL); unter Windows werden sie übersprungen.

Ausführen:  uv run python -m pytest -q test_pc_geraet.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import httpcore
import httpx

import pc_geraet
import pc_host
import pc_tunnel
from auth_gate import Identity
from geraete_register import Geraet, RegisterFehler

LINUX = sys.platform.startswith("linux")
ICH = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
ANDERE = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"
FP_A = "SHA256:" + "A" * 43
FP_B = "SHA256:" + "B" * 43
TOKEN_A, FREIGABE_A = "a" * 43, "b" * 43
TOKEN_B, FREIGABE_B = "c" * 43, "d" * 43


def geraet(slot: int, fp: str, kennung: str, hostname: str | None = "ANNA-PC") -> Geraet:
    return Geraet(device_id=str(uuid.uuid4()), kennung=kennung, slot=slot, name="Laptop", hostname=hostname, fingerabdruck=fp)


def identitaet(user_id=ICH, rolle="admin") -> Identity:
    return Identity(user_id=user_id, name="Anna", rolle=rolle, anrede="du", anmeldung=1.0)


class FakeRegister:
    def __init__(self, geraete: dict[str, Geraet | None] | None = None, fehler: Exception | None = None, aktiv=True):
        self.geraete = geraete or {}
        self.fehler = fehler
        self.aktiv = aktiv
        self.abfragen: list[str] = []
        self.gesehen_kennungen: list[str] = []

    async def aktives_geraet(self, user_id: str):
        self.abfragen.append(user_id)
        if self.fehler:
            raise self.fehler
        return self.geraete.get(user_id)

    async def ist_aktiv(self, device_id: str, user_id: str):
        return self.aktiv

    async def gesehen(self, kennung: str) -> None:
        self.gesehen_kennungen.append(kennung)


class ZugangTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"WAMO_BETRIEB": "buero", "WAMO_PC_REGISTER": "an", "WAMO_PC_MITGLIEDER": "aus"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.gelesen: list[str] = []

        def lies(g, wurzel, **kw):
            self.gelesen.append(f"{wurzel}/{g.ordner}")
            return pc_geraet.Geheimnisse(TOKEN_A, FREIGABE_A)

        for p in (mock.patch.object(pc_geraet, "lies_geheimnisse", lies), mock.patch.object(pc_geraet, "_uid_von", lambda b: 1234)):
            p.start()
            self.addCleanup(p.stop)

    async def test_register_modus_nur_im_buero_mit_schalter(self):
        self.assertTrue(pc_geraet.register_modus())
        with mock.patch.dict(os.environ, {"WAMO_PC_REGISTER": "aus"}):
            self.assertFalse(pc_geraet.register_modus())
        with mock.patch.dict(os.environ, {"WAMO_BETRIEB": ""}):
            self.assertFalse(pc_geraet.register_modus())
        with mock.patch.dict(os.environ, {"WAMO_PC_REGISTER": ""}):
            self.assertFalse(pc_geraet.register_modus())  # Standard: aus

    async def test_gerat_nur_ueber_das_profil_und_pfad_nur_aus_der_zahl(self):
        reg = FakeRegister({ICH: geraet(5, FP_A, "pc-anna"), ANDERE: geraet(9, FP_B, "pc-bernd")})
        a = await pc_geraet.pc_zugang_fuer(identitaet(ICH), reg)
        b = await pc_geraet.pc_zugang_fuer(identitaet(ANDERE), reg)
        self.assertEqual((a.socket, a.uid, a.geraet.kennung), ("/run/wamo-pc/t05/pc.sock", 1234, "pc-anna"))
        self.assertEqual(b.socket, "/run/wamo-pc/t09/pc.sock")
        self.assertEqual(reg.abfragen, [ICH, ANDERE])  # nur die user_id der jeweiligen Sitzung
        self.assertEqual(self.gelesen, ["/run/wamo-pc/t05", "/run/wamo-pc/t09"])
        self.assertNotEqual(a.geraet.device_id, b.geraet.device_id)

    async def test_ohne_aktives_geraet_keine_werkzeuge(self):
        self.assertIsNone(await pc_geraet.pc_zugang_fuer(identitaet(), FakeRegister({ICH: None})))
        self.assertEqual(self.gelesen, [])

    async def test_register_ausfall_und_kaputte_zeile(self):
        for fehler in (RegisterFehler("weg"), RegisterFehler("Kennung ungültig")):
            self.assertIsNone(await pc_geraet.pc_zugang_fuer(identitaet(), FakeRegister(fehler=fehler)))

    async def test_mitglied_nur_mit_schalter(self):
        reg = FakeRegister({ICH: geraet(5, FP_A, "pc-anna")})
        self.assertIsNone(await pc_geraet.pc_zugang_fuer(identitaet(rolle="mitglied"), reg))
        self.assertEqual(reg.abfragen, [])  # gar nicht erst gefragt
        with mock.patch.dict(os.environ, {"WAMO_PC_MITGLIEDER": "an"}):
            self.assertIsNotNone(await pc_geraet.pc_zugang_fuer(identitaet(rolle="mitglied"), reg))

    async def test_geheimnisse_unstimmig_keine_werkzeuge(self):
        def kaputt(g, wurzel, **kw):
            raise pc_geraet.PcGeraetFehler("Host-Token hat falschen Typ, Besitzer, Größe oder Rechte")

        with mock.patch.object(pc_geraet, "lies_geheimnisse", kaputt):
            self.assertIsNone(await pc_geraet.pc_zugang_fuer(identitaet(), FakeRegister({ICH: geraet(5, FP_A, "pc-anna")})))

    async def test_ungueltige_wurzel_aus_der_umgebung(self):
        for schlecht in ("run/wamo-pc", "/run/../etc", "/run//wamo-pc", "/run/wamo-pc/"):
            with mock.patch.dict(os.environ, {"WAMO_PC_SOCKET_WURZEL": schlecht}):
                self.assertIsNone(await pc_geraet.pc_zugang_fuer(identitaet(), FakeRegister({ICH: geraet(5, FP_A, "pc-anna")})), schlecht)

    async def test_host_client_ist_an_das_geraet_gebunden(self):
        zugang = await pc_geraet.pc_zugang_fuer(identitaet(), FakeRegister({ICH: geraet(5, FP_A, "pc-anna")}))
        client = pc_host.host_client(zugang)
        try:
            self.assertIsInstance(client, pc_tunnel.TunnelHost)
            self.assertEqual(pc_host.freigabe_schluessel(client), FREIGABE_A)
            zugang.geheimnisse.freigabe = "e" * 43  # nach einem Host-Neustart neu gelesen
            self.assertEqual(pc_host.freigabe_schluessel(client), "e" * 43)
        finally:
            await client.close()
        # ohne Zugang im Register-Modus nie zurück auf den alten Weg oder stdio
        self.assertIsNone(pc_host.host_client(None))
        self.assertFalse(pc_host.pc_host_fuer(identitaet()))

    async def test_pc_zugang_respektiert_host_aus(self):
        with mock.patch.dict(os.environ, {"WAMO_PC_HOST": "off"}):
            self.assertIsNone(await pc_host.pc_zugang(identitaet()))


class NameTests(unittest.TestCase):
    def test_rechnername_passt(self):
        g = geraet(5, FP_A, "pc-anna", hostname="Anna-PC")
        self.assertTrue(pc_geraet.rechnername_passt(g, "ANNA-PC"))
        self.assertFalse(pc_geraet.rechnername_passt(g, "BERND-PC"))
        self.assertFalse(pc_geraet.rechnername_passt(g, None))  # system_info antwortet nicht: fail closed
        self.assertFalse(pc_geraet.rechnername_passt(geraet(5, FP_A, "pc-anna", hostname=None), "ANNA-PC"))


class WacheTests(unittest.IsolatedAsyncioTestCase):
    async def _lauf(self, antworten):
        gesendet: list[str] = []

        class Reg(FakeRegister):
            async def ist_aktiv(self, device_id, user_id):
                return antworten.pop(0) if antworten else True

        zugang = pc_geraet.PcZugang(geraet=geraet(5, FP_A, "pc-anna"), user_id=ICH, socket="/x", uid=1, wurzel="/run/wamo-pc",
                                    geheimnisse=pc_geraet.Geheimnisse(TOKEN_A, FREIGABE_A), register=Reg())

        async def entzug(grund):
            gesendet.append(grund)

        async def schlaf(_s):
            await asyncio.sleep(0)

        task = asyncio.create_task(pc_geraet.geraet_wache(zugang, entzug, schlaf=schlaf))
        await asyncio.sleep(0.05)
        return task, gesendet

    async def test_gesperrt_beendet_die_steuerung_einmal(self):
        task, gesendet = await self._lauf([True, True, False])
        await task
        self.assertEqual(len(gesendet), 1)
        self.assertIn("gesperrt", gesendet[0])

    async def test_aktiv_laeuft_weiter(self):
        task, gesendet = await self._lauf([True] * 5)
        self.assertFalse(task.done())
        task.cancel()
        self.assertEqual(gesendet, [])

    async def test_datenbankausfall_erst_nach_drei_pruefungen(self):
        task, gesendet = await self._lauf([None, None, True, None, None, None])
        await task
        self.assertEqual(len(gesendet), 1)  # nach der Unterbrechung durch True zählt es von vorn
        self.assertIn("nicht erreichbar", gesendet[0])


class WacheFehlerTests(unittest.IsolatedAsyncioTestCase):
    def _zugang(self, ist_aktiv):
        class Reg(FakeRegister):
            pass

        reg = Reg()
        reg.ist_aktiv = ist_aktiv
        return pc_geraet.PcZugang(geraet=geraet(5, FP_A, "pc-anna"), user_id=ICH, socket="/x", uid=1, wurzel="/run/wamo-pc",
                                  geheimnisse=pc_geraet.Geheimnisse(TOKEN_A, FREIGABE_A), register=reg)

    async def test_unerwartete_ausnahme_zaehlt_als_ausfall_und_beendet_nicht_still(self):
        async def kaputt(_d, _u):
            raise ValueError("unerwartet")

        gesendet: list[str] = []

        async def entzug(grund):
            gesendet.append(grund)

        await asyncio.wait_for(pc_geraet.geraet_wache(self._zugang(kaputt), entzug, schlaf=lambda _s: asyncio.sleep(0)), 5)
        self.assertEqual(len(gesendet), 1)

    async def test_haengende_pruefung_ist_ein_ausfall(self):
        async def haengt(_d, _u):
            await asyncio.sleep(3600)

        gesendet: list[str] = []

        async def entzug(grund):
            gesendet.append(grund)

        with mock.patch.object(pc_geraet, "WACHE_PRUEFUNG_SECS", 0.01):
            await asyncio.wait_for(pc_geraet.geraet_wache(self._zugang(haengt), entzug, schlaf=lambda _s: asyncio.sleep(0)), 5)
        self.assertEqual(len(gesendet), 1)

    async def test_erste_pruefung_kommt_frueh(self):
        pausen: list[float] = []

        async def schlaf(s):
            pausen.append(s)
            if len(pausen) >= 3:
                raise asyncio.CancelledError

        async def aktiv(_d, _u):
            return True

        with self.assertRaises(asyncio.CancelledError):
            await pc_geraet.geraet_wache(self._zugang(aktiv), lambda g: asyncio.sleep(0), intervall=60.0, erste=15.0, schlaf=schlaf)
        self.assertEqual(pausen, [15.0, 60.0, 60.0])

    async def test_nachladen_liest_neu_und_aktualisiert_das_objekt(self):
        zugang = self._zugang(lambda *_: None)
        with mock.patch.object(pc_geraet, "lies_geheimnisse", lambda g, w, **kw: pc_geraet.Geheimnisse("n" * 43, "m" * 43)):
            self.assertEqual(await zugang.nachladen(), "n" * 43)
        self.assertEqual((zugang.geheimnisse.token, zugang.geheimnisse.freigabe), ("n" * 43, "m" * 43))
        with mock.patch.object(pc_geraet, "lies_geheimnisse", mock.Mock(side_effect=pc_geraet.PcGeraetFehler("weg"))):
            with self.assertRaises(pc_geraet.PcGeraetFehler):
                await zugang.nachladen()
        self.assertEqual(zugang.geheimnisse.token, "n" * 43)  # bei Fehlern bleibt der letzte Stand


class LeaseTests(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.dict(os.environ, {"WAMO_BETRIEB": "buero", "WAMO_PC_REGISTER": "an"}),
                  mock.patch.object(pc_host, "_lease_je_geraet", {}), mock.patch.object(pc_host, "_lease", None)):
            p.start()
            self.addCleanup(p.stop)

    def test_sperre_je_geraet(self):
        a, b, c = object(), object(), object()
        self.assertTrue(pc_host.pc_lease_nehmen(a, "geraet-1"))
        self.assertTrue(pc_host.pc_lease_nehmen(b, "geraet-2"))  # anderes Gerät: unabhängig
        self.assertFalse(pc_host.pc_lease_nehmen(c, "geraet-1"))  # dasselbe Gerät: nur eine Sitzung
        self.assertTrue(pc_host.pc_lease_haelt(a, "geraet-1") and not pc_host.pc_lease_haelt(c, "geraet-1"))
        pc_host.pc_lease_freigeben(c, "geraet-1")  # fremde Freigabe wirkt nicht
        self.assertTrue(pc_host.pc_lease_haelt(a, "geraet-1"))
        pc_host.pc_lease_freigeben(a, "geraet-1")
        self.assertTrue(pc_host.pc_lease_nehmen(c, "geraet-1"))

    def test_sitzung_und_ablosung_nehmen_die_sperre_des_geraets(self):
        from sitzungen import Sitzung

        alt, neu = Sitzung(ICH), Sitzung(ICH)
        alt.geraet_id = neu.geraet_id = "geraet-1"
        fremd = Sitzung(ANDERE)
        fremd.geraet_id = "geraet-2"
        self.assertTrue(alt.pc_lease_nehmen() and fremd.pc_lease_nehmen())
        self.assertFalse(neu.pc_lease_nehmen())
        alt.abgeloest = True
        alt.pc_lease_freigeben()  # die Ablösung übernimmt die Sperre des Geräts, nicht die des anderen
        self.assertTrue(neu.pc_lease_nehmen() and fremd.pc_lease_haelt())


# ---------- TunnelHost im Tunnel je Gerät (ohne echte Sockets) ----------
def rpc_antwort(request: httpx.Request, result: dict | None = None) -> httpx.Response:
    nachricht = json.loads(request.content)
    if nachricht["method"] == "initialize":
        result = {"serverInfo": {"name": "wamo-host"}, "protocolVersion": "2025-06-18"}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": nachricht["id"],
                                     "result": result or {"content": [{"type": "text", "text": "{}"}]}})


class TunnelGeraetTests(unittest.IsolatedAsyncioTestCase):
    async def test_kopfzeilen_und_token(self):
        gesehen: list[httpx.Headers] = []

        def antwort(request):
            gesehen.append(request.headers)
            return rpc_antwort(request)

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(antwort),
                                    geraet="pc-anna", profil=ICH)
        await host.call_text("system_info", {})
        h = gesehen[0]
        self.assertEqual((h["authorization"], h["x-wamo-geraet"], h["x-wamo-profil"]), (f"Bearer {TOKEN_A}", "pc-anna", ICH))
        await host.close()

    async def test_token_wird_nach_401_einmal_neu_gelesen(self):
        token_im_host = {"wert": TOKEN_B}  # der Host hat neu gestartet
        aufrufe: list[str] = []

        def antwort(request):
            aufrufe.append(request.headers["authorization"])
            if request.headers["authorization"] != f"Bearer {token_im_host['wert']}":
                return httpx.Response(401, json={"error": "nicht angemeldet"})
            return rpc_antwort(request)

        nachgeladen: list[int] = []

        async def nachladen():
            nachgeladen.append(1)
            return TOKEN_B

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(antwort), nachladen=nachladen)
        await host.call_text("system_info", {})
        self.assertEqual(aufrufe, [f"Bearer {TOKEN_A}", f"Bearer {TOKEN_B}"])
        await host.call_text("system_info", {})  # das neue Token bleibt
        self.assertEqual(aufrufe[-1], f"Bearer {TOKEN_B}")
        self.assertEqual(len(nachgeladen), 1)
        await host.close()

    async def test_nachladen_scheitert_oder_token_bleibt_falsch(self):
        def immer_401(request):
            return httpx.Response(401, json={})

        async def nachladen_scheitert():
            raise pc_geraet.PcGeraetFehler("weg")

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(immer_401),
                                    nachladen=nachladen_scheitert)
        with self.assertRaises(pc_tunnel.PcNichtErreichbar):
            await host.call_text("system_info", {})
        await host.close()
        n = []

        async def nachladen_gleich():
            n.append(1)
            return TOKEN_A

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(immer_401), nachladen=nachladen_gleich)
        with self.assertRaises(pc_tunnel.PcNichtErreichbar):
            await host.call_text("system_info", {})
        self.assertEqual(len(n), 1)  # genau einmal, keine Schleife
        await host.close()

    async def test_antwortgrenze_und_aufrufgrenze(self):
        def gross(request):
            return rpc_antwort(request, {"content": [{"type": "text", "text": "x" * 5000}]})

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(gross), max_antwort_bytes=1000)
        with self.assertRaisesRegex(RuntimeError, "zu groß"):
            await host.call_text("system_info", {})
        await host.close()
        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(rpc_antwort),
                                    aufrufe_je_minute=3)
        for _ in range(3):
            await host.call_text("system_info", {})
        with self.assertRaises(pc_tunnel.PcZuvieleAufrufe):
            await host.call_text("system_info", {})
        await host.pruefen()  # die Probe der Überwachung zählt nicht mit
        await host.close()


class TunnelRandfaelleTests(unittest.IsolatedAsyncioTestCase):
    async def test_komprimierte_antwort_wird_abgelehnt(self):
        def gzip_antwort(request):
            return httpx.Response(200, headers={"content-encoding": "gzip", "content-type": "application/json"}, content=b"x")

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(gzip_antwort))
        with self.assertRaisesRegex(RuntimeError, "komprimiert"):
            await host.call_text("system_info", {})
        await host.close()

    async def test_anfrage_verlangt_identity_kodierung(self):
        gesehen = []

        def antwort(request):
            gesehen.append(request.headers.get("accept-encoding"))
            return rpc_antwort(request)

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(antwort))
        await host.call_text("system_info", {})
        self.assertEqual(gesehen, ["identity"])
        await host.close()

    async def test_aufrufgrenze_zaehlt_nie_das_nein_des_menschen(self):
        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(rpc_antwort), aufrufe_je_minute=1)
        await host.call_text("system_info", {})
        with self.assertRaises(pc_tunnel.PcZuvieleAufrufe):
            await host.call_text("system_info", {})
        await host.call_text("freigabe_entscheiden", {"approval_id": "0" * 16, "ja": False, "schluessel": "x"})
        await host.close()

    async def test_fehlercode_des_hosts_nur_als_zahl(self):
        def fehler(request):
            rpc_id = json.loads(request.content)["id"]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc_id, "error": {"code": "x\nWAMO-GERAET gefälscht"}})

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=httpx.MockTransport(fehler))
        with self.assertRaises(RuntimeError) as ctx:
            await host.call_text("system_info", {})
        self.assertNotIn("\n", str(ctx.exception))
        self.assertIn("unbekannt", str(ctx.exception))
        await host.close()

    async def test_lesefehler_im_rumpf_ist_pc_nicht_erreichbar(self):
        """Der Rumpf einer Antwort bricht ab: aus dem httpcore-Fehler wird ein httpx-Fehler, der Host gilt als weg."""

        class Strom(httpcore.AsyncNetworkStream):
            def __init__(self):
                self.teile = [b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 500\r\n\r\n{"]

            async def read(self, max_bytes, timeout=None):
                if self.teile:
                    return self.teile.pop(0)
                raise httpcore.ReadError("abgerissen")

            async def write(self, buffer, timeout=None):
                return None

            async def aclose(self):
                return None

            def get_extra_info(self, info):
                return None

        class Backend(httpcore.AsyncNetworkBackend):
            async def connect_unix_socket(self, path, timeout=None, socket_options=None):
                return Strom()

            async def connect_tcp(self, *a, **kw):
                raise NotImplementedError

            async def sleep(self, seconds):
                await asyncio.sleep(seconds)

        host = pc_tunnel.TunnelHost("/x", TOKEN_A, tools_filter=[], transport=pc_tunnel.UnixSocketTransport("/x", Backend()))
        host._melde(True)
        with self.assertRaises(pc_tunnel.PcNichtErreichbar):
            await host.call_text("system_info", {})
        self.assertFalse(host.erreichbar)
        await host.close()

    async def test_falsche_gegenstelle_ist_eine_eigene_ausnahme(self):
        stream = FakeStream()
        with self.assertRaises(pc_tunnel.GegenstelleFalsch):
            await pc_tunnel.pruefe_gegenstelle(stream, 1, lambda _s: 2)


# ---------- Gegenstelle prüfen ----------
class FakeStream:
    def __init__(self):
        self.geschlossen = False

    def get_extra_info(self, name):
        return object() if name == "socket" else None

    async def aclose(self):
        self.geschlossen = True


class GegenstelleTests(unittest.IsolatedAsyncioTestCase):
    async def test_richtige_und_falsche_gegenstelle(self):
        ok = FakeStream()
        await pc_tunnel.pruefe_gegenstelle(ok, 1234, lambda _s: 1234)
        self.assertFalse(ok.geschlossen)
        falsch = FakeStream()
        with self.assertRaises(httpcore.ConnectError):
            await pc_tunnel.pruefe_gegenstelle(falsch, 1234, lambda _s: 4321)
        self.assertTrue(falsch.geschlossen)

    async def test_nicht_pruefbar_heisst_nicht_vertrauenswuerdig(self):
        def kaputt(_s):
            raise OSError("keine SO_PEERCRED")

        stream = FakeStream()
        with self.assertRaises(httpcore.ConnectError):
            await pc_tunnel.pruefe_gegenstelle(stream, 1234, kaputt)
        self.assertTrue(stream.geschlossen)


@unittest.skipUnless(LINUX, "echte Unix-Sockets und SO_PEERCRED nur unter Linux")
class EchteSocketsTests(unittest.IsolatedAsyncioTestCase):
    """Zwei Geräte A und B mit eigenen Sockets: jede Sitzung erreicht nur den eigenen, und die Gegenstelle wird geprüft,
    bevor ein Byte (das Token) gesendet wird."""

    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wamo-pc-test-", dir="/tmp")
        self.empfangen: dict[str, list[bytes]] = {"a": [], "b": []}
        self.server = {}
        for name in ("a", "b"):
            pfad = os.path.join(self.tmp, f"{name}.sock")
            self.server[name] = await asyncio.start_unix_server(self._bediene(name), path=pfad)
        self.addAsyncCleanup(self._ende)

    async def _ende(self):
        for s in self.server.values():
            s.close()
            await s.wait_closed()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bediene(self, name):
        async def bearbeite(reader, writer):
            try:
                kopf = await reader.readuntil(b"\r\n\r\n")
                laenge = int([z.split(b":")[1] for z in kopf.split(b"\r\n") if z.lower().startswith(b"content-length")][0])
                koerper = await reader.readexactly(laenge)
                self.empfangen[name].append(kopf + koerper)
                rpc_id = json.loads(koerper)["id"]
                antwort = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": {"content": [{"type": "text", "text": name}]}}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(antwort)).encode()
                             + b"\r\nConnection: close\r\n\r\n" + antwort)
                await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()

        return bearbeite

    def _host(self, name: str, uid: int, token: str, kennung: str) -> pc_tunnel.TunnelHost:
        return pc_tunnel.TunnelHost(os.path.join(self.tmp, f"{name}.sock"), token, tools_filter=[], erwartete_uid=uid,
                                    geraet=kennung, profil=ICH)

    async def test_jede_sitzung_erreicht_nur_den_eigenen_socket(self):
        a = self._host("a", os.getuid(), TOKEN_A, "pc-anna")
        b = self._host("b", os.getuid(), TOKEN_B, "pc-bernd")
        self.assertEqual(await a.call_text("system_info", {}), "a")
        self.assertEqual(await b.call_text("system_info", {}), "b")
        self.assertEqual(len(self.empfangen["a"]), 1)
        self.assertEqual(len(self.empfangen["b"]), 1)
        self.assertIn(TOKEN_A.encode(), self.empfangen["a"][0])
        self.assertNotIn(TOKEN_A.encode(), self.empfangen["b"][0])  # das Token von A erreicht B nie
        self.assertNotIn(TOKEN_B.encode(), self.empfangen["a"][0])
        await a.close()
        await b.close()

    async def test_falsche_gegenstelle_bekommt_kein_byte(self):
        # erwartet wird ein anderer Benutzer als der, der den Socket hält: kein Token, keine Anfrage
        host = self._host("a", os.getuid() + 4242, TOKEN_A, "pc-anna")
        with self.assertRaises(pc_tunnel.PcNichtErreichbar):
            await host.call_text("system_info", {})
        await asyncio.sleep(0.1)
        self.assertEqual(self.empfangen["a"], [])
        self.assertFalse(host.erreichbar)
        await host.close()

    async def test_fehlender_socket_ist_nicht_erreichbar(self):
        host = pc_tunnel.TunnelHost(os.path.join(self.tmp, "gibt-es-nicht.sock"), TOKEN_A, tools_filter=[],
                                    erwartete_uid=os.getuid())
        with self.assertRaises(pc_tunnel.PcNichtErreichbar):
            await host.call_text("system_info", {})
        await host.close()


# ---------- Geheimnisse lesen (Dateirechte, Links, Besitzer) ----------
@unittest.skipUnless(LINUX, "O_NOFOLLOW, O_PATH und dir_fd nur unter Linux")
class GeheimnisseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wamo-pc-test-", dir="/tmp")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.uid, self.gid = os.getuid(), os.getgid()
        os.chmod(self.tmp, 0o711)
        self.g = geraet(5, FP_A, "pc-anna")
        self.ordner = Path(self.tmp) / "t05"
        self.ordner.mkdir()
        os.chmod(self.ordner, 0o2750)
        self.schreibe("host-token", TOKEN_A + "\n")
        self.schreibe("host-freigabe", FREIGABE_A + "\n")
        self.schreibe("schluessel", FP_A + "\n")

    def schreibe(self, name, inhalt, modus=0o640):
        pfad = self.ordner / name
        pfad.unlink(missing_ok=True)
        pfad.write_bytes(inhalt.encode() if isinstance(inhalt, str) else inhalt)
        os.chmod(pfad, modus)

    def lies(self, **kw):
        kw.setdefault("uid", self.uid)
        kw.setdefault("wurzel_uid", self.uid)
        kw.setdefault("gruppen", {self.gid})
        return pc_geraet.lies_geheimnisse(self.g, self.tmp, **kw)

    def test_gut(self):
        s = self.lies()
        self.assertEqual((s.token, s.freigabe), (TOKEN_A, FREIGABE_A))
        self.assertNotIn(TOKEN_A, repr(s))

    def test_fremder_besitzer_der_dateien_oder_des_ordners(self):
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies(uid=self.uid + 1)

    def test_falsche_gruppe_des_ordners(self):
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies(gruppen={self.gid + 1})

    def test_ordnerrechte(self):
        os.chmod(self.ordner, 0o2770)
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies()

    def test_wurzel_beschreibbar_oder_fremd(self):
        os.chmod(self.tmp, 0o733)
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies()
        os.chmod(self.tmp, 0o711)
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies(wurzel_uid=self.uid + 1)

    def test_link_statt_datei_oder_ordner(self):
        ziel = Path(self.tmp) / "fremd-token"
        ziel.write_text(TOKEN_B + "\n")
        os.chmod(ziel, 0o640)
        (self.ordner / "host-token").unlink()
        (self.ordner / "host-token").symlink_to(ziel)
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies()
        # Ordner selbst als Link auf den Ordner eines anderen Geräts
        andere = Path(self.tmp) / "t06"
        andere.mkdir()
        os.chmod(andere, 0o2750)
        g2 = geraet(6, FP_A, "pc-bernd")
        os.rmdir(andere)
        andere.symlink_to(self.ordner)
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            pc_geraet.lies_geheimnisse(g2, self.tmp, uid=self.uid, wurzel_uid=self.uid, gruppen={self.gid})

    def test_dateirechte_groesse_und_format(self):
        for name, inhalt, modus in (("host-token", TOKEN_A, 0o666), ("host-token", TOKEN_A, 0o660),
                                    ("host-token", "A" * 700, 0o640), ("host-token", "kurz", 0o640),
                                    ("host-token", TOKEN_A[:-1] + "!", 0o640), ("host-token", "Ä" * 50, 0o640),
                                    ("schluessel", "SHA256:kurz", 0o640)):
            with self.subTest(name=name, inhalt=inhalt[:10], modus=oct(modus)):
                self.schreibe(name, inhalt, modus)
                with self.assertRaises(pc_geraet.PcGeraetFehler):
                    self.lies()
                self.schreibe("host-token", TOKEN_A + "\n")
                self.schreibe("schluessel", FP_A + "\n")

    def test_fingerabdruck_muss_zum_register_passen(self):
        self.schreibe("schluessel", FP_B + "\n")
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies()

    def test_token_und_freigabe_verschieden(self):
        self.schreibe("host-freigabe", TOKEN_A + "\n")
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies()

    def test_datei_fehlt_oder_ordner_fehlt(self):
        (self.ordner / "host-freigabe").unlink()
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies()
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            pc_geraet.lies_geheimnisse(geraet(7, FP_A, "pc-carla"), self.tmp, uid=self.uid, wurzel_uid=self.uid, gruppen={self.gid})
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            pc_geraet.lies_geheimnisse(self.g, os.path.join(self.tmp, "gibt-es-nicht"), uid=self.uid, wurzel_uid=self.uid)

    def test_wurzel_mit_nur_durchsuchen_recht_laesst_sich_oeffnen(self):
        # Die echte Wurzel hat 0711: Fremde dürfen betreten, nicht lesen. Hier hat sogar der Besitzer kein Leserecht (0311):
        # ein Öffnen zum Lesen scheitert mit EACCES, O_PATH nicht (als root nicht prüfbar, dort gilt jedes Recht).
        os.chmod(self.tmp, 0o311)
        try:
            if os.geteuid() != 0:
                with self.assertRaises(PermissionError):
                    os.open(self.tmp, os.O_RDONLY | os.O_DIRECTORY)
            self.assertEqual(self.lies().token, TOKEN_A)
        finally:
            os.chmod(self.tmp, 0o711)
        self.assertEqual(stat.S_IMODE(os.stat(self.tmp).st_mode), 0o711)

    def test_fifo_statt_datei_blockiert_nicht(self):
        (self.ordner / "host-token").unlink()
        os.mkfifo(self.ordner / "host-token", 0o640)
        with self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies()

    def test_dateisystemfehler_wird_pcgeraetfehler(self):
        with mock.patch("os.fstat", side_effect=OSError(5, "EIO")), self.assertRaises(pc_geraet.PcGeraetFehler):
            self.lies()


if __name__ == "__main__":
    unittest.main()
