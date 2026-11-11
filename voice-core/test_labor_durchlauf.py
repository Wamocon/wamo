"""Tests für Durchlauf und Einwilligung (Testlabor 3.1, P1c): strenge Auswertung der Antwort V1, Prüfung beim Start, Wache alle 5 Sekunden.

Ausführen:  python -m pytest -p no:cacheprovider -q test_labor_durchlauf.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import unittest
from unittest import mock

import httpx

import labor_durchlauf as ld
from auth_gate import Identity
from labor import laborfelder_stimmig  # noqa: F401  (Schnittstelle, die labor_durchlauf nutzt)

USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
D1 = "5b0c7a0e-1111-4222-8333-444455556666"
D2 = "5b0c7a0e-2222-4222-8333-444455556666"


def platz_identitaet(platz=3):
    return Identity(USER, "Gast", "mitglied", "du", labor_art="platz", labor_platz=platz, labor_aktiv=True)


class FesteQuelle:
    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.plaetze: list[int] = []

    async def durchlauf(self, platz):
        self.plaetze.append(platz)
        a = self.antworten[0] if len(self.antworten) == 1 else self.antworten.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def stand(offen=True, version=2, kennung=D1):
    return {"durchlauf_id": kennung, "offen": offen, "einwilligung_version": version, "einwilligung_zeit": "2026-11-28T09:00:00Z"}


class AuswertungTests(unittest.TestCase):
    def test_gueltige_antwort(self):
        self.assertEqual(ld.durchlauf_lesen(stand()), ld.Durchlauf(D1, True, 2))
        self.assertEqual(ld.durchlauf_lesen(stand(offen=False, version=None)), ld.Durchlauf(D1, False, None))

    def test_alles_ausserhalb_des_vertrags_ist_ein_fehler(self):
        schlecht = [None, [], "x", {}, {"offen": True}, {"durchlauf_id": D1}, stand(kennung="keine-uuid"), stand(kennung=5),
                    stand(kennung=None), {**stand(), "offen": "true"}, {**stand(), "offen": 1}, {**stand(), "offen": None},
                    stand(version="2"), stand(version=True), stand(version=-1), stand(version=2.0)]
        for roh in schlecht:
            with self.subTest(roh=roh), self.assertRaises(ld.DurchlaufFehler):
                ld.durchlauf_lesen(roh)

    def test_kennung_wird_kanonisch(self):
        self.assertEqual(ld.durchlauf_lesen(stand(kennung=D1.upper())).durchlauf_id, D1)


class PruefenTests(unittest.IsolatedAsyncioTestCase):
    async def test_offen_mit_gueltiger_einwilligung(self):
        quelle = FesteQuelle(stand())
        urteil = await ld.pruefen(platz_identitaet(7), quelle=quelle, min_version=2)
        self.assertEqual((urteil.status, urteil.code), (200, None))
        self.assertEqual(urteil.stand, ld.Durchlauf(D1, True, 2))
        self.assertEqual(quelle.plaetze, [7])  # der Platz kommt aus der Identität

    async def test_hoehere_version_gilt(self):
        self.assertEqual((await ld.pruefen(platz_identitaet(), quelle=FesteQuelle(stand(version=5)), min_version=2)).status, 200)

    async def test_beendeter_durchlauf(self):
        urteil = await ld.pruefen(platz_identitaet(), quelle=FesteQuelle(stand(offen=False)), min_version=2)
        self.assertEqual((urteil.status, urteil.code), (403, ld.CODE_BEENDET))

    async def test_keine_oder_zu_alte_einwilligung(self):
        for version in (None, 0, 1):
            with self.subTest(version=version):
                urteil = await ld.pruefen(platz_identitaet(), quelle=FesteQuelle(stand(version=version)), min_version=2)
                self.assertEqual((urteil.status, urteil.code), (403, ld.CODE_EINWILLIGUNG))

    async def test_fail_closed_bei_jedem_fehler_des_dienstes(self):
        fehler = [ld.DurchlaufFehler("x"), httpx.ConnectError("weg"), OSError("x"), RuntimeError("x"), ValueError("x"),
                  {"offen": True}, None, "text"]
        for f in fehler:
            with self.subTest(fehler=f):
                quelle = FesteQuelle(f)
                urteil = await ld.pruefen(platz_identitaet(), quelle=quelle, min_version=2)
                self.assertEqual((urteil.status, urteil.code), (503, ld.CODE_DIENST))

    async def test_zu_langsamer_dienst_ist_503(self):
        import asyncio

        class Langsam:
            async def durchlauf(self, platz):
                await asyncio.sleep(10)

        with mock.patch.object(ld, "LESE_TIMEOUT_SECS", 0.05):
            urteil = await ld.pruefen(platz_identitaet(), quelle=Langsam(), min_version=2)
        self.assertEqual(urteil.status, 503)

    async def test_ohne_quelle_oder_ohne_mindestversion_ist_zu(self):
        with mock.patch.object(ld, "_quelle", None), mock.patch.dict("os.environ", {"WAMO_LABOR_URL": "", "WAMO_LABOR_TOKEN": ""}):
            self.assertEqual((await ld.pruefen(platz_identitaet(), min_version=2)).status, 503)
        for roh in ("", "0", "-1", "abc", "1.5", "1e3", " "):
            with self.subTest(minimum=roh), mock.patch.dict("os.environ", {"WAMO_LABOR_EINWILLIGUNG_MIN": roh}):
                self.assertEqual((await ld.pruefen(platz_identitaet(), quelle=FesteQuelle(stand()))).status, 503)

    async def test_mindestversion_aus_der_umgebung(self):
        with mock.patch.dict("os.environ", {"WAMO_LABOR_EINWILLIGUNG_MIN": "3"}):
            self.assertEqual((await ld.pruefen(platz_identitaet(), quelle=FesteQuelle(stand(version=2)))).status, 403)
            self.assertEqual((await ld.pruefen(platz_identitaet(), quelle=FesteQuelle(stand(version=3)))).status, 200)

    async def test_schaufenster_braucht_keinen_durchlauf(self):
        quelle = FesteQuelle(RuntimeError("darf nicht gefragt werden"))
        schau = Identity(USER, "Gast", "mitglied", "du", labor_art="schaufenster", labor_aktiv=True)
        urteil = await ld.pruefen(schau, quelle=quelle, min_version=2)
        self.assertEqual((urteil.status, quelle.plaetze), (200, []))

    async def test_platz_ohne_nummer_ist_503(self):
        ident = Identity(USER, "Gast", "mitglied", "du", labor_art="platz", labor_platz=None, labor_aktiv=True)
        self.assertEqual((await ld.pruefen(ident, quelle=FesteQuelle(stand()), min_version=2)).status, 503)


class HttpQuelleTests(unittest.IsolatedAsyncioTestCase):
    def quelle(self, handler):
        return ld.HttpQuelle("https://labor.test.invalid:9443", "TESTTOKEN", transport=httpx.MockTransport(handler))

    async def test_aufruf_nach_vertrag_v1(self):
        gesehen = {}

        def handler(request):
            gesehen["url"], gesehen["kopf"] = str(request.url), dict(request.headers)
            return httpx.Response(200, json=stand())

        antwort = await self.quelle(handler).durchlauf(4)
        self.assertEqual(gesehen["url"], "https://labor.test.invalid:9443/api/kern/durchlauf")
        self.assertEqual(gesehen["kopf"]["authorization"], "Bearer TESTTOKEN")
        self.assertEqual(gesehen["kopf"]["x-wamo-platz"], "4")
        self.assertEqual(ld.durchlauf_lesen(antwort).durchlauf_id, D1)

    async def test_fehlerantworten(self):
        for status in (401, 404, 409, 429, 500, 503):
            with self.subTest(status=status), self.assertRaises(ld.DurchlaufFehler):
                await self.quelle(lambda r, s=status: httpx.Response(s, json={"fehler": "x"})).durchlauf(1)
        with self.assertRaises(ld.DurchlaufFehler):
            await self.quelle(lambda r: httpx.Response(200, content=b"<html>")).durchlauf(1)
        with self.assertRaises(ld.DurchlaufFehler):
            await self.quelle(lambda r: httpx.Response(200, content=b'{"x": "' + b"a" * 70000 + b'"}')).durchlauf(1)

    def test_adresse_und_token_werden_geprueft(self):
        for url in ("", "ftp://x", "http://labor.test.invalid", "labor.test", "https://"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ld.HttpQuelle(url, "t")
        with self.assertRaises(ValueError):
            ld.HttpQuelle("https://labor.test.invalid", "")
        ld.HttpQuelle("http://127.0.0.1:9000", "t")  # Loopback ohne TLS ist erlaubt

    def test_der_token_steht_nicht_in_der_darstellung(self):
        self.assertNotIn("TESTTOKEN", repr(self.quelle(lambda r: httpx.Response(200))))

    def test_quelle_aus_env(self):
        with mock.patch.dict("os.environ", {"WAMO_LABOR_URL": "", "WAMO_LABOR_TOKEN": ""}):
            self.assertIsNone(ld.quelle_aus_env())
        with mock.patch.dict("os.environ", {"WAMO_LABOR_URL": "https://labor.test.invalid", "WAMO_LABOR_TOKEN": ""}):
            self.assertIsNone(ld.quelle_aus_env())
        with mock.patch.dict("os.environ", {"WAMO_LABOR_URL": "http://labor.test.invalid", "WAMO_LABOR_TOKEN": "t"}):
            self.assertIsNone(ld.quelle_aus_env())  # kein TLS im Netz
        with mock.patch.dict("os.environ", {"WAMO_LABOR_URL": "https://labor.test.invalid", "WAMO_LABOR_TOKEN": "t"}):
            self.assertIsInstance(ld.quelle_aus_env(), ld.HttpQuelle)


class WacheTests(unittest.IsolatedAsyncioTestCase):
    def wache(self, *antworten, **kw):
        return ld.LaborWache(3, ld.Durchlauf(D1, True, 2), quelle=FesteQuelle(*antworten), min_version=2, **kw)

    async def test_unveraendert_laeuft_weiter(self):
        self.assertIsNone(await self.wache(stand()).check())

    async def test_t14_durchlaufwechsel_beendet_die_sitzung(self):
        self.assertEqual(await self.wache(stand(kennung=D2)).check(), ld.GRUND_ENDE)

    async def test_beendeter_durchlauf_beendet_die_sitzung(self):
        self.assertEqual(await self.wache(stand(offen=False)).check(), ld.GRUND_ENDE)

    async def test_wegfall_der_einwilligung_beendet_die_sitzung(self):
        for version in (None, 1):
            self.assertEqual(await self.wache(stand(version=version)).check(), ld.GRUND_ENDE)

    async def test_ausfall_des_dienstes_beendet_nicht_und_zaehlt(self):
        w = self.wache(httpx.ConnectError("weg"))
        for _ in range(5):
            self.assertIsNone(await w.check())
        self.assertEqual(w.fehler, 5)
        w._quelle = FesteQuelle(stand())
        self.assertIsNone(await w.check())
        self.assertEqual(w.fehler, 0)

    async def test_unlesbare_antwort_beendet_nicht(self):
        self.assertIsNone(await self.wache({"offen": True}).check())

    async def test_run_ruft_das_ende_einmal_und_kehrt_zurueck(self):
        pausen, enden = [], []

        async def schlafen(s):
            pausen.append(s)

        async def ende(grund):
            enden.append(grund)

        w = self.wache(stand(), stand(), stand(offen=False), interval=5.0, sleep=schlafen)
        await w.run(ende)
        self.assertEqual(pausen, [5.0, 5.0, 5.0])
        self.assertEqual(enden, [ld.GRUND_ENDE])

    async def test_ein_fehler_im_ende_stoppt_run_nicht_mit_ausnahme(self):
        async def schlafen(s):
            pass

        async def ende(grund):
            raise RuntimeError("x")

        await self.wache(stand(offen=False), sleep=schlafen).run(ende)

    def test_die_durchlauf_id_der_sitzung(self):
        self.assertEqual(self.wache(stand()).durchlauf_id, D1)


if __name__ == "__main__":
    unittest.main()
