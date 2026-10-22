"""Tests für die Bindung des eigenen Laptops (geraet_api.py) und das Geräteregister im Kern (geraete_register.py), ohne
Datenbank. Die Regeln der Erstbindung selbst prüft supabase/tests/0002_wamo_geraete_test.sql gegen wamo_test.

Ausführen:  uv run python -m pytest -q test_geraet_api.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import geraet_api
import geraete_register as gr
from auth_gate import Identity
from memory import DatabaseUnavailable

ICH = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
ANDERE = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e"
HOSTKEY = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))).decode()


def neuer_schluessel() -> tuple[Ed25519PrivateKey, str]:
    privat = Ed25519PrivateKey.generate()
    roh = privat.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + roh
    return privat, "ssh-ed25519 " + base64.b64encode(blob).decode()


def signiere(privat: Ed25519PrivateKey, nonce: str, user_id: str, hostname: str) -> str:
    # genau so baut die Desktop-App die Nachricht (desktop/geraet.js: nachrichtBinden)
    nachricht = f"wamo-geraet-binden\n{nonce}\n{user_id}\n{hostname}".encode()
    return base64.urlsafe_b64encode(privat.sign(nachricht)).decode().rstrip("=")


class NonceTests(unittest.TestCase):
    def setUp(self):
        geraet_api._benutzt.clear()

    def test_gueltig_einmal_und_nur_fuer_das_profil(self):
        n = geraet_api.nonce_erzeugen(ICH, jetzt=1000)
        self.assertFalse(geraet_api.nonce_verbrauchen(n, ANDERE, jetzt=1001))  # anderes Profil
        self.assertTrue(geraet_api.nonce_verbrauchen(n, ICH, jetzt=1001))
        self.assertFalse(geraet_api.nonce_verbrauchen(n, ICH, jetzt=1002))  # zweites Mal

    def test_abgelaufen_und_manipuliert(self):
        n = geraet_api.nonce_erzeugen(ICH, jetzt=1000)
        self.assertFalse(geraet_api.nonce_verbrauchen(n, ICH, jetzt=1000 + geraet_api.GUELTIG_SECS + 1))
        roh = bytearray(base64.urlsafe_b64decode(n + "=="))
        roh[20] ^= 1  # Ablauf verändert
        falsch = base64.urlsafe_b64encode(bytes(roh)).decode().rstrip("=")
        self.assertFalse(geraet_api.nonce_verbrauchen(falsch, ICH, jetzt=1001))
        for schlecht in ("", "abc", "!" * 54, "A" * 100):
            self.assertFalse(geraet_api.nonce_verbrauchen(schlecht, ICH, jetzt=1001))

    def test_signatur(self):
        privat, pub = neuer_schluessel()
        nachricht = geraet_api.nachricht_bauen("n1", ICH, "WMC-H-04-NS")
        sig = privat.sign(nachricht)
        self.assertTrue(geraet_api.signatur_gueltig(pub, nachricht, sig))
        self.assertFalse(geraet_api.signatur_gueltig(pub, nachricht + b"x", sig))
        self.assertFalse(geraet_api.signatur_gueltig(pub, nachricht, sig[:-1] + bytes([sig[-1] ^ 1])))
        self.assertFalse(geraet_api.signatur_gueltig(neuer_schluessel()[1], nachricht, sig))  # anderer Schlüssel
        self.assertFalse(geraet_api.signatur_gueltig(pub, nachricht, sig[:10]))
        self.assertFalse(geraet_api.signatur_gueltig("ssh-rsa AAAA", nachricht, sig))
        self.assertFalse(geraet_api.signatur_gueltig(pub + " kommentar", nachricht, sig))

    def test_nachricht_wie_in_der_desktop_app(self):
        self.assertEqual(geraet_api.nachricht_bauen("N", ICH, "H"), f"wamo-geraet-binden\nN\n{ICH}\nH".encode())


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(geraet_api.router, prefix="/wamo/api")
    return app


class ApiTests(unittest.TestCase):
    def setUp(self):
        geraet_api._benutzt.clear()
        geraet_api._versuche.clear()
        self.rolle = "mitglied"
        self.zeile = {"device_id": str(uuid.uuid4()), "kennung": "pc-wmc-h-04-ns", "tunnel_slot": 5, "status": "aktiv",
                      "name": "Anna Laptop", "ergebnis": "neu_aktiv"}
        self.register = mock.MagicMock()
        self.register.binden = mock.AsyncMock(side_effect=lambda *a: dict(self.zeile))

        async def identitaet(_request):
            return Identity(user_id=ICH, name="Anna", rolle=self.rolle, anrede="du", anmeldung=1.0)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        datei = Path(self._tmp.name) / "hostkey.pub"
        datei.write_text(HOSTKEY + " root@spark\n", encoding="ascii")
        self.patches = [mock.patch.object(geraet_api, "identitaet", identitaet),
                        mock.patch.object(geraet_api, "_register", lambda: self.register),
                        mock.patch.dict("os.environ", {"WAMO_BETRIEB": "buero", "WAMO_PC_SSH_HOSTKEY_DATEI": str(datei),
                                                       "WAMO_PC_SSH_HOST": "192.168.178.136"})]
        for p in self.patches:
            p.start()
        geraet_api._hostschluessel_cache = None
        self.client = TestClient(_app())
        self.privat, self.pub = neuer_schluessel()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _binden(self, **ueberschreiben):
        nonce = self.client.get("/wamo/api/geraet/herausforderung").json()["nonce"]
        body = {"pubkey": self.pub, "hostname": "WMC-H-04-NS", "nonce": nonce,
                "signatur": signiere(self.privat, nonce, ICH, "WMC-H-04-NS")}
        body.update(ueberschreiben)
        return self.client.post("/wamo/api/geraet/binden", json=body)

    def test_nur_im_buero(self):
        with mock.patch.dict("os.environ", {"WAMO_BETRIEB": ""}):
            self.assertEqual(self.client.get("/wamo/api/geraet/herausforderung").status_code, 404)
            self.assertEqual(self.client.post("/wamo/api/geraet/binden", json={}).status_code, 404)

    def test_ohne_identitaet(self):
        async def abgelehnt(_request):
            raise HTTPException(401, "nicht angemeldet")

        with mock.patch.object(geraet_api, "identitaet", abgelehnt):
            self.assertEqual(self.client.get("/wamo/api/geraet/herausforderung").status_code, 401)
            self.assertEqual(self.client.post("/wamo/api/geraet/binden", json={}).status_code, 401)
        self.register.binden.assert_not_called()

    def test_aktiv_liefert_das_ziel(self):
        r = self._binden()
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual((d["ergebnis"], d["status"], d["kennung"], d["slot"], d["stufe"], d["user_id"]),
                         ("neu_aktiv", "aktiv", "pc-wmc-h-04-ns", 5, "mitglied", ICH))
        self.assertEqual(d["ziel"], {"benutzer": "wamo-t-05", "host": "192.168.178.136",
                                     "socket": "/run/wamo-pc/t05/pc.sock", "hostschluessel": HOSTKEY})
        # die Datenbank bekommt Profil aus dem Token, Schlüssel, Rechnername, Namen
        self.register.binden.assert_awaited_once_with(ICH, self.pub, "WMC-H-04-NS", None)

    def test_admin_bekommt_stufe_admin(self):
        self.rolle = "admin"
        self.assertEqual(self._binden().json()["stufe"], "admin")

    def test_beantragt_ohne_ziel(self):
        self.zeile.update(status="beantragt", ergebnis="neu_beantragt", kennung=None, tunnel_slot=None)
        d = self._binden().json()
        self.assertEqual((d["status"], d["kennung"], d["slot"], d["ziel"]), ("beantragt", None, None, None))

    def test_gesperrt_ohne_ziel(self):
        self.zeile.update(status="gesperrt", ergebnis="bekannt")
        d = self._binden().json()
        self.assertEqual((d["status"], d["ziel"]), ("gesperrt", None))

    def test_fremd_und_zu_viele(self):
        self.zeile.update(ergebnis="fremd", status=None)
        r = self._binden()
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("kennung", r.text.lower().replace("keine", ""))  # verrät nichts über das fremde Gerät
        self.zeile.update(ergebnis="zu_viele")
        self.assertEqual(self._binden().status_code, 429)

    def test_signatur_pflicht_datenbank_bleibt_unberuehrt(self):
        fremd, _ = neuer_schluessel()
        nonce = self.client.get("/wamo/api/geraet/herausforderung").json()["nonce"]
        r = self.client.post("/wamo/api/geraet/binden", json={
            "pubkey": self.pub, "hostname": "WMC-H-04-NS", "nonce": nonce,
            "signatur": signiere(fremd, nonce, ICH, "WMC-H-04-NS")})  # signiert mit einem anderen Schlüssel
        self.assertEqual(r.status_code, 400)
        self.register.binden.assert_not_called()

    def test_signatur_ist_an_hostname_und_profil_gebunden(self):
        nonce = self.client.get("/wamo/api/geraet/herausforderung").json()["nonce"]
        r = self.client.post("/wamo/api/geraet/binden", json={
            "pubkey": self.pub, "hostname": "ANDERER-PC", "nonce": nonce,
            "signatur": signiere(self.privat, nonce, ICH, "WMC-H-04-NS")})
        self.assertEqual(r.status_code, 400)
        nonce = self.client.get("/wamo/api/geraet/herausforderung").json()["nonce"]
        r = self.client.post("/wamo/api/geraet/binden", json={
            "pubkey": self.pub, "hostname": "WMC-H-04-NS", "nonce": nonce,
            "signatur": signiere(self.privat, nonce, ANDERE, "WMC-H-04-NS")})  # Signatur für ein anderes Profil
        self.assertEqual(r.status_code, 400)
        self.register.binden.assert_not_called()

    def test_nonce_nur_kanonisch(self):
        n = geraet_api.nonce_erzeugen(ICH, jetzt=1000)
        # Die letzte Base64-Stelle hat 4 freie Bits: Bit 0 umzukehren ergibt eine andere Schreibweise derselben Bytes.
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        andere = n[:-1] + alphabet[alphabet.index(n[-1]) ^ 1]
        self.assertNotEqual(andere, n)
        self.assertEqual(base64.urlsafe_b64decode(andere + "=" * (-len(andere) % 4)), base64.urlsafe_b64decode(n + "=" * (-len(n) % 4)))
        self.assertFalse(geraet_api.nonce_verbrauchen(andere, ICH, jetzt=1001))
        self.assertTrue(geraet_api.nonce_verbrauchen(n, ICH, jetzt=1001))

    def test_herausforderung_nur_einmal(self):
        nonce = self.client.get("/wamo/api/geraet/herausforderung").json()["nonce"]
        body = {"pubkey": self.pub, "hostname": "WMC-H-04-NS", "nonce": nonce,
                "signatur": signiere(self.privat, nonce, ICH, "WMC-H-04-NS")}
        self.assertEqual(self.client.post("/wamo/api/geraet/binden", json=body).status_code, 200)
        self.assertEqual(self.client.post("/wamo/api/geraet/binden", json=body).status_code, 400)  # Wiederholung

    def test_ungueltige_eingaben(self):
        for ueberschreiben in ({"pubkey": "ssh-rsa AAAA"}, {"hostname": "a b"}, {"hostname": "x" * 64},
                               {"name": "x" * 101}, {"name": "a\nb"}, {"name": 5}, {"signatur": ""}, {"nonce": 7},
                               {"hostname": None}):
            with self.subTest(**{k: str(v)[:20] for k, v in ueberschreiben.items()}):
                geraet_api._versuche.clear()  # die Drosselung zählt jeden Aufruf
                self.assertEqual(self._binden(**ueberschreiben).status_code, 400)
        self.register.binden.assert_not_called()

    def test_datenbank_fehler_wird_503(self):
        self.register.binden = mock.AsyncMock(side_effect=gr.RegisterFehler("Register nicht erreichbar (OSError)"))
        r = self._binden()
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("OSError", r.text)

    def test_unerwartete_antwort_der_datenbank(self):
        self.zeile.update(ergebnis="etwas_neues")
        self.assertEqual(self._binden().status_code, 503)
        self.zeile.update(ergebnis="neu_aktiv", tunnel_slot=99)
        self.assertEqual(self._binden().status_code, 503)

    def test_hostschluessel_nicht_lesbar_oder_kaputt(self):
        with mock.patch.dict("os.environ", {"WAMO_PC_SSH_HOSTKEY_DATEI": "/nicht/da"}):
            geraet_api._hostschluessel_cache = None
            self.assertEqual(self._binden().status_code, 503)
        datei = Path(self._tmp.name) / "kaputt.pub"
        datei.write_text("ssh-ed25519 zu-kurz\n", encoding="ascii")
        with mock.patch.dict("os.environ", {"WAMO_PC_SSH_HOSTKEY_DATEI": str(datei)}):
            geraet_api._hostschluessel_cache = None
            self.assertEqual(self._binden().status_code, 503)

    def test_drosselung(self):
        for _ in range(geraet_api.VERSUCHE):
            self.assertEqual(self.client.get("/wamo/api/geraet/herausforderung").status_code, 200)
        self.assertEqual(self.client.get("/wamo/api/geraet/herausforderung").status_code, 429)
        # der andere Schritt hat seinen eigenen Zähler (die Oberfläche fragt Herausforderung und Bindung je Runde einmal)
        self.assertEqual(self.client.post("/wamo/api/geraet/binden", json={}).status_code, 400)

    def test_ssh_host_muss_eine_ipv4_sein(self):
        with mock.patch.dict("os.environ", {"WAMO_PC_SSH_HOST": "evil.example.com"}):
            with self.assertRaises(HTTPException):
                geraet_api.ssh_host()
        for schlecht in ("300.1.1.1", "192.168.178.0136", "192.168.178.01"):
            with mock.patch.dict("os.environ", {"WAMO_PC_SSH_HOST": schlecht}):
                with self.assertRaises(HTTPException, msg=schlecht):
                    geraet_api.ssh_host()


class FakeDb:
    schema = "wamo_test"

    def __init__(self, zeilen=None, fehler=None, wert=None):
        self.zeilen, self.fehler, self.wert, self.aufrufe = zeilen or [], fehler, wert, []

    def t(self, name):
        return f"wamo_test.{name}"

    async def fetch(self, sql, *args):
        self.aufrufe.append((sql, args))
        if self.fehler:
            raise self.fehler
        return self.zeilen

    async def fetchrow(self, sql, *args):
        self.aufrufe.append((sql, args))
        if self.fehler:
            raise self.fehler
        return self.zeilen[0] if self.zeilen else None

    async def fetchval(self, sql, *args):
        self.aufrufe.append((sql, args))
        if self.fehler:
            raise self.fehler
        return self.wert


def zeile(**k):
    z = {"id": str(uuid.uuid4()), "kennung": "pc-anna", "tunnel_slot": 7, "name": "Anna", "hostname": "ANNA-PC",
         "ssh_fingerprint": "SHA256:" + "A" * 43}
    z.update(k)
    return z


class RegisterTests(unittest.IsolatedAsyncioTestCase):
    async def test_aktives_geraet_nur_ueber_das_profil(self):
        db = FakeDb([zeile()])
        g = await gr.GeraeteRegister(db).aktives_geraet(ICH)
        self.assertEqual((g.kennung, g.slot, g.benutzer, g.ordner), ("pc-anna", 7, "wamo-t-07", "t07"))
        sql, args = db.aufrufe[0]
        self.assertEqual(args, (ICH,))
        self.assertIn("status = 'aktiv'", sql)
        self.assertIn("p.aktiv", sql)

    async def test_kein_geraet_und_mehrere(self):
        self.assertIsNone(await gr.GeraeteRegister(FakeDb([])).aktives_geraet(ICH))
        with self.assertRaises(gr.RegisterFehler):
            await gr.GeraeteRegister(FakeDb([zeile(), zeile()])).aktives_geraet(ICH)

    async def test_ungueltige_zeilen_werden_abgelehnt(self):
        for schlecht in (zeile(kennung="anna"), zeile(kennung="pc-../x"), zeile(kennung="pc-" + "a" * 30),
                         zeile(tunnel_slot=0), zeile(tunnel_slot=65), zeile(tunnel_slot="7"), zeile(tunnel_slot=True),
                         zeile(ssh_fingerprint="SHA256:kurz"), zeile(id="kein-uuid")):
            with self.subTest(**{k: v for k, v in schlecht.items() if k in ("kennung", "tunnel_slot")}):
                with self.assertRaises(gr.RegisterFehler):
                    await gr.GeraeteRegister(FakeDb([schlecht])).aktives_geraet(ICH)

    async def test_datenbankausfall_und_ungueltige_user_id(self):
        with self.assertRaises(gr.RegisterFehler):
            await gr.GeraeteRegister(FakeDb(fehler=DatabaseUnavailable("weg"))).aktives_geraet(ICH)
        with self.assertRaises(gr.RegisterFehler):
            await gr.GeraeteRegister(FakeDb()).aktives_geraet("x'; drop table devices;--")

    async def test_ist_aktiv(self):
        self.assertTrue(await gr.GeraeteRegister(FakeDb(wert=True)).ist_aktiv(str(uuid.uuid4()), ICH))
        self.assertFalse(await gr.GeraeteRegister(FakeDb(wert=False)).ist_aktiv(str(uuid.uuid4()), ICH))
        self.assertIsNone(await gr.GeraeteRegister(FakeDb(fehler=DatabaseUnavailable("weg"))).ist_aktiv(str(uuid.uuid4()), ICH))
        self.assertFalse(await gr.GeraeteRegister(FakeDb(wert=True)).ist_aktiv("kein-uuid", ICH))

    async def test_gesehen_gedrosselt(self):
        t = [0.0]
        db = FakeDb()
        reg = gr.GeraeteRegister(db, uhr=lambda: t[0])
        await reg.gesehen("pc-anna")
        await reg.gesehen("pc-anna")
        t[0] = 61.0
        await reg.gesehen("pc-anna")
        await reg.gesehen("kein-pc")  # ungültige Kennung: nicht an die Datenbank
        self.assertEqual(len(db.aufrufe), 2)
        await gr.GeraeteRegister(FakeDb(fehler=DatabaseUnavailable("weg"))).gesehen("pc-anna")  # wirft nie

    async def test_binden_reicht_nur_die_geprueften_werte_durch(self):
        db = FakeDb([{"device_id": "x", "kennung": None, "tunnel_slot": None, "status": "beantragt", "name": "A",
                      "ergebnis": "neu_beantragt"}])
        antwort = await gr.GeraeteRegister(db).binden(ICH, "ssh-ed25519 AAAA", "PC", None)
        self.assertEqual(antwort["ergebnis"], "neu_beantragt")
        self.assertEqual(db.aufrufe[0][1], (ICH, "ssh-ed25519 AAAA", "PC", None))
        self.assertIn("geraet_binden", db.aufrufe[0][0])
        with self.assertRaises(gr.RegisterFehler):
            await gr.GeraeteRegister(FakeDb([])).binden(ICH, "k", "h", None)


if __name__ == "__main__":
    unittest.main()
