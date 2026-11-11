"""Tests für Durchlauf und Einwilligung (Testlabor 3.1, P1c): strenge Auswertung der Antwort V1, Prüfung beim Start, Wache alle 5 Sekunden.

Ausführen:  python -m pytest -p no:cacheprovider -q test_labor_durchlauf.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import httpx

import labor_client
import labor_durchlauf as ld
import labor_vertrag
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


TOKEN = "t" * 43
URL = "https://labor.test.invalid:9443"


def einstellung(**kw):
    return mock.patch.dict("os.environ", {"WAMO_LABOR_URL": URL, "WAMO_LABOR_TOKEN": TOKEN, **kw})


class ClientQuelleTests(unittest.IsolatedAsyncioTestCase):
    """Die Quelle der Zugangskontrolle ist der Dienstclient des Kerns (labor_client.py): ein Weg zum Dienst für Start, Wache und Werkzeuge."""

    def quelle(self, handler):
        return ld.ClientQuelle(transport=httpx.MockTransport(handler))

    async def test_aufruf_nach_vertrag_v1(self):
        gesehen = {}

        def handler(request):
            gesehen["url"], gesehen["kopf"] = str(request.url), dict(request.headers)
            return httpx.Response(200, json=stand())

        with einstellung():
            antwort = await self.quelle(handler).durchlauf(4)
        self.assertEqual(gesehen["url"], "https://labor.test.invalid:9443/api/kern/durchlauf")
        self.assertEqual(gesehen["kopf"]["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(gesehen["kopf"]["x-wamo-platz"], "4")
        self.assertEqual(ld.durchlauf_lesen(antwort), ld.Durchlauf(D1, True, 2))

    async def test_fehlerantworten_sind_fehler_und_die_pruefung_ist_zu(self):
        antworten = [httpx.Response(s, json={"fehler": "x"}) for s in (401, 404, 409, 429, 500, 503)]
        antworten += [httpx.Response(200, content=b"<html>"), httpx.Response(200, content=b'{"x": "' + b"a" * 70000 + b'"}'),
                      httpx.Response(200, json={**stand(), "zusatz": 1})]  # nur die Felder des Vertrags
        for antwort in antworten:
            with self.subTest(status=antwort.status_code, laenge=len(antwort.content)), einstellung():
                quelle = self.quelle(lambda r, a=antwort: a)
                with self.assertRaises(labor_client.LaborFehler):
                    await quelle.durchlauf(1)
                urteil = await ld.pruefen(platz_identitaet(), quelle=quelle, min_version=2)
                self.assertEqual((urteil.status, urteil.code), (503, ld.CODE_DIENST))

    async def test_unbrauchbare_einstellung_ist_zu_ohne_einen_wert_zu_nennen(self):
        faelle = [("", TOKEN), ("ftp://x", TOKEN), ("http://labor.test.invalid", TOKEN), ("labor.test", TOKEN), ("https://", TOKEN),
                  (f"https://{labor_vertrag.ATTRAPPE_HOST}", TOKEN), (URL, ""), (URL, "kurz"), (URL, "t" * 300)]
        for url, token in faelle:
            with self.subTest(url=url, token=len(token)), mock.patch.dict("os.environ", {"WAMO_LABOR_URL": url, "WAMO_LABOR_TOKEN": token}):
                quelle = self.quelle(lambda r: httpx.Response(200, json=stand()))
                with self.assertRaises(labor_client.LaborKonfigFehler) as ctx:
                    await quelle.durchlauf(1)
                self.assertNotIn(token or "\0", str(ctx.exception))
                self.assertEqual((await ld.pruefen(platz_identitaet(), quelle=quelle, min_version=2)).status, 503)

    async def test_der_client_ist_nach_jeder_frage_zu(self):
        clients = []
        echt = labor_client.client_aus_umgebung

        def merken(platz, **kw):
            clients.append(echt(platz, **kw))
            return clients[-1]

        with einstellung(), mock.patch.object(labor_client, "client_aus_umgebung", merken):
            await self.quelle(lambda r: httpx.Response(200, json=stand())).durchlauf(2)
            with self.assertRaises(labor_client.LaborFehler):
                await self.quelle(lambda r: httpx.Response(500)).durchlauf(2)
        self.assertEqual(len(clients), 2)
        self.assertTrue(all(c._http.is_closed for c in clients))

    def test_quelle_aus_env_ist_der_dienstclient(self):
        self.assertIsInstance(ld.quelle_aus_env(), ld.ClientQuelle)  # die Einstellung wird erst bei der Frage gelesen und geprüft
        with mock.patch.object(ld, "_quelle", None):
            self.assertIs(ld.quelle_holen(), ld.quelle_holen())

    def test_es_gibt_keinen_zweiten_weg_zum_dienst(self):
        """Das Modul baut keinen eigenen HTTP-Client und liest die Einstellung nicht selbst: Adresse, Token, Zertifikat und Antwort prüft nur labor_client."""
        quelltext = Path(ld.__file__).read_text(encoding="utf-8")
        self.assertNotIn("httpx.AsyncClient", quelltext)
        self.assertNotIn("getenv(\"WAMO_LABOR_URL\")", quelltext)
        self.assertNotIn("getenv(\"WAMO_LABOR_TOKEN\")", quelltext)


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

    async def test_ein_langer_ausfall_beendet_die_sitzung_fail_closed(self):
        """Einwilligung und Durchlauf sind nicht mehr prüfbar: nach 12 Fehlversuchen (60 Sekunden bei 5 Sekunden Abstand) endet die Sitzung samt
        Textspeicherung, sie läuft nicht bis zur Höchstdauer weiter."""
        self.assertEqual(ld.MAX_AUSFALL_VERSUCHE * ld.POLL_SECS, 60)
        w = self.wache(httpx.ConnectError("weg"))
        for _ in range(ld.MAX_AUSFALL_VERSUCHE - 1):
            self.assertIsNone(await w.check())
        self.assertEqual(await w.check(), ld.GRUND_AUSFALL)
        self.assertEqual(await w.check(), ld.GRUND_AUSFALL)  # und bleibt dabei
        w._quelle = FesteQuelle(stand())  # kommt der Dienst vorher zurück, zählt von vorn
        self.assertEqual(await self.nach_ausfall(11, stand()), None)

    async def nach_ausfall(self, anzahl: int, antwort):
        w = self.wache(*([httpx.ConnectError("weg")] * anzahl + [antwort]))
        for _ in range(anzahl):
            self.assertIsNone(await w.check())
        return await w.check()

    async def test_run_beendet_die_sitzung_nach_dem_langen_ausfall(self):
        enden = []

        async def schlafen(s):
            pass

        async def ende(grund):
            enden.append(grund)

        await self.wache(httpx.ConnectError("weg"), sleep=schlafen).run(ende)
        self.assertEqual(enden, [ld.GRUND_AUSFALL])

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
