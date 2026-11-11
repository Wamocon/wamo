"""Ratenbegrenzung der Laborsitzungen (Sicherheitsreview Befund 4): getippter Text, Websuche und /start und Offer sind begrenzt.

Ein böswilliges Laborprofil darf den gemeinsamen Modellschlüssel, den Dienst der Kollegen und den Betrieb nicht belasten. Die Grenzen gelten im Kern
(nicht im Prompt, nicht in der Oberfläche), je Sitzung oder je Profil, mit benannten Standardwerten und Überschreibung über WAMO_LABOR_*.

Ausführen:  python -m pytest -p no:cacheprovider -q test_labor_limits.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import labor
import labor_limit
from auth_gate import AccessMiddleware
from test_auth_gate import token
from test_labor_sperre import Urteil, gate_mit, laborprofil


class Uhr:
    def __init__(self):
        self.jetzt = 1000.0

    def __call__(self) -> float:
        return self.jetzt


class TextBegrenzerTests(unittest.TestCase):
    def test_standardwerte_sind_benannt(self):
        self.assertEqual((labor_limit.TEXT_ABSTAND_SECS, labor_limit.TEXT_MAX_ZEICHEN), (2.0, 40_000))

    def test_abstand_zwischen_zwei_eingaben(self):
        uhr = Uhr()
        b = labor_limit.TextBegrenzer(uhr=uhr)
        self.assertTrue(b.zulassen(10))
        uhr.jetzt += 1.9
        self.assertFalse(b.zulassen(10))  # zu schnell
        uhr.jetzt += 0.2  # 2,1 Sekunden nach der angenommenen Eingabe: die abgelehnte hat die Frist nicht verlängert
        self.assertTrue(b.zulassen(10))

    def test_gesamtmenge_je_sitzung(self):
        uhr = Uhr()
        b = labor_limit.TextBegrenzer(abstand_secs=1, max_zeichen=100, uhr=uhr)
        for _ in range(10):
            self.assertTrue(b.zulassen(10))
            uhr.jetzt += 5
        self.assertFalse(b.zulassen(1))  # 100 von 100 verbraucht, auch nach beliebiger Wartezeit
        uhr.jetzt += 3600
        self.assertFalse(b.zulassen(1))

    def test_eine_zu_grosse_eingabe_verbraucht_nichts(self):
        b = labor_limit.TextBegrenzer(abstand_secs=0, max_zeichen=100, uhr=Uhr())
        self.assertFalse(b.zulassen(101))
        self.assertTrue(b.zulassen(100))

    def test_jede_sitzung_hat_ihre_eigene_menge(self):
        a, b = labor_limit.TextBegrenzer(max_zeichen=10, uhr=Uhr()), labor_limit.TextBegrenzer(max_zeichen=10, uhr=Uhr())
        self.assertTrue(a.zulassen(10))
        self.assertTrue(b.zulassen(10))

    def test_ueberschreibung_ueber_die_umgebung_und_ungueltiges_heisst_standard(self):
        with mock.patch.dict("os.environ", {"WAMO_LABOR_TEXT_ABSTAND_SECS": "5", "WAMO_LABOR_TEXT_MAX_ZEICHEN": "2000"}):
            b = labor_limit.TextBegrenzer(uhr=Uhr())
            self.assertEqual((b.abstand_secs, b.max_zeichen), (5.0, 2000))
        for roh in ("", "abc", "-1", "0", "1e9", "99999999"):
            with self.subTest(roh=roh), mock.patch.dict("os.environ", {"WAMO_LABOR_TEXT_ABSTAND_SECS": roh, "WAMO_LABOR_TEXT_MAX_ZEICHEN": roh}):
                b = labor_limit.TextBegrenzer(uhr=Uhr())
                self.assertEqual((b.abstand_secs, b.max_zeichen), (labor_limit.TEXT_ABSTAND_SECS, labor_limit.TEXT_MAX_ZEICHEN))


class KlickBegrenzerTests(unittest.TestCase):
    """Der Klick auf eine Freigabekarte (client-message freigabe) ist begrenzt: Abstand und Höchstzahl, damit kein Teilnehmer den Klickweg flutet."""

    def test_standardwerte_sind_benannt(self):
        self.assertEqual((labor_limit.KLICK_ABSTAND_SECS, labor_limit.KLICK_MAX), (0.5, 100))

    def test_abstand_zwischen_zwei_klicks(self):
        uhr = Uhr()
        b = labor_limit.KlickBegrenzer(uhr=uhr)
        self.assertTrue(b.zulassen())
        uhr.jetzt += 0.4
        self.assertFalse(b.zulassen())  # zu schnell
        uhr.jetzt += 0.2  # 0,6 Sekunden nach dem angenommenen Klick: der abgelehnte hat die Frist nicht verlängert
        self.assertTrue(b.zulassen())

    def test_hoechstzahl_je_sitzung_auch_nach_beliebiger_wartezeit(self):
        uhr = Uhr()
        b = labor_limit.KlickBegrenzer(abstand_secs=1, maximum=5, uhr=uhr)
        for _ in range(5):
            self.assertTrue(b.zulassen())
            uhr.jetzt += 2
        self.assertFalse(b.zulassen())
        uhr.jetzt += 3600
        self.assertFalse(b.zulassen())

    def test_ueberschreibung_ueber_die_umgebung_und_ungueltiges_heisst_standard(self):
        with mock.patch.dict("os.environ", {"WAMO_LABOR_KLICK_ABSTAND_SECS": "3", "WAMO_LABOR_KLICK_MAX": "7"}):
            b = labor_limit.KlickBegrenzer(uhr=Uhr())
            self.assertEqual((b.abstand_secs, b.maximum), (3.0, 7))
        for roh in ("", "abc", "-1", "0", "0.05", "1e9", "99999999", "inf", "nan", "1001"):
            with self.subTest(roh=roh), mock.patch.dict("os.environ", {"WAMO_LABOR_KLICK_ABSTAND_SECS": roh, "WAMO_LABOR_KLICK_MAX": roh}):
                b = labor_limit.KlickBegrenzer(uhr=Uhr())
                self.assertEqual((b.abstand_secs, b.maximum), (labor_limit.KLICK_ABSTAND_SECS, labor_limit.KLICK_MAX))


class LimitRegisterTests(unittest.TestCase):
    """Befund 2: Text, Suchen und Klicks zählen je Profil und Durchlauf, nicht je Verbindung: eine Neuverbindung setzt nichts zurück."""

    def test_dasselbe_profil_im_selben_durchlauf_teilt_die_zaehler(self):
        register = labor_limit.LimitRegister()
        erste = register.fuer("profil-1", "durchlauf-1")
        zweite = register.fuer("profil-1", "durchlauf-1")  # Neuverbindung
        self.assertIs(erste.text, zweite.text)
        self.assertIs(erste.suchen, zweite.suchen)
        self.assertIs(erste.klicks, zweite.klicks)

    def test_verbrauch_bleibt_nach_der_neuverbindung(self):
        register = labor_limit.LimitRegister()
        erste = register.fuer("profil-1", "durchlauf-1")
        erste.suchen.maximum = 2
        self.assertTrue(erste.suchen.verbrauchen())
        self.assertTrue(erste.suchen.verbrauchen())
        self.assertFalse(register.fuer("profil-1", "durchlauf-1").suchen.verbrauchen())  # auch die neue Verbindung hat keine Suche mehr
        erste.text.max_zeichen = 10
        self.assertTrue(erste.text.zulassen(10))
        self.assertFalse(register.fuer("profil-1", "durchlauf-1").text.zulassen(1))

    def test_anderes_profil_und_anderer_durchlauf_haben_eigene_zaehler(self):
        register = labor_limit.LimitRegister()
        a = register.fuer("profil-1", "durchlauf-1")
        for anderer in (register.fuer("profil-2", "durchlauf-1"), register.fuer("profil-1", "durchlauf-2"), register.fuer("profil-1", None)):
            self.assertIsNot(a.text, anderer.text)
            self.assertIsNot(a.suchen, anderer.suchen)
            self.assertIsNot(a.klicks, anderer.klicks)

    def test_kein_trennzeichen_verwischt_die_schluessel(self):
        register = labor_limit.LimitRegister()
        self.assertIsNot(register.fuer("a|b", "c").text, register.fuer("a", "b|c").text)

    def test_der_speicher_ist_begrenzt_die_aeltesten_fallen_weg(self):
        register = labor_limit.LimitRegister(max_schluessel=3)
        erste = register.fuer("p0", "d")
        for i in range(1, 50):
            register.fuer(f"p{i}", "d")
        self.assertEqual(register.groesse(), 3)
        self.assertIsNot(register.fuer("p0", "d").text, erste.text)  # der älteste ist längst weg
        letzter = register.fuer("p49", "d")
        register.fuer("p50", "d")
        self.assertIs(register.fuer("p49", "d").text, letzter.text)  # ein neuer Schlüssel bleibt


class SuchZaehlerTests(unittest.TestCase):
    def test_hoechstens_so_viele_suchen_je_sitzung(self):
        z = labor_limit.SuchZaehler(3)
        self.assertEqual([z.verbrauchen() for _ in range(5)], [True, True, True, False, False])

    def test_standard_zehn_und_ueberschreibung(self):
        self.assertEqual(labor_limit.SUCHEN_MAX, 10)
        self.assertEqual(labor_limit.suchen_max(), 10)
        with mock.patch.dict("os.environ", {"WAMO_LABOR_SUCHEN_MAX": "4"}):
            self.assertEqual(labor_limit.suchen_max(), 4)
        with mock.patch.dict("os.environ", {"WAMO_LABOR_SUCHEN_MAX": "0"}):
            self.assertEqual(labor_limit.suchen_max(), 10)  # unbrauchbar: Standard, nie unbegrenzt


class AnfragenFensterTests(unittest.TestCase):
    def test_je_profil_hoechstens_n_je_fenster(self):
        uhr = Uhr()
        f = labor_limit.AnfragenFenster(maximum=3, secs=60, uhr=uhr)
        self.assertEqual([f.zulassen("a") for _ in range(5)], [True, True, True, False, False])
        self.assertTrue(f.zulassen("b"))  # ein anderes Profil ist nicht betroffen
        uhr.jetzt += 61
        self.assertTrue(f.zulassen("a"))  # das Fenster ist vorbei

    def test_das_fenster_gleitet(self):
        uhr = Uhr()
        f = labor_limit.AnfragenFenster(maximum=2, secs=60, uhr=uhr)
        self.assertTrue(f.zulassen("a"))
        uhr.jetzt += 40
        self.assertTrue(f.zulassen("a"))
        uhr.jetzt += 30  # der erste Aufruf ist 70 Sekunden alt, der zweite 30
        self.assertTrue(f.zulassen("a"))
        self.assertFalse(f.zulassen("a"))

    def test_der_speicher_ist_begrenzt(self):
        uhr = Uhr()
        f = labor_limit.AnfragenFenster(maximum=1, secs=60, uhr=uhr, max_schluessel=50)
        for i in range(500):
            uhr.jetzt += 0.001
            f.zulassen(f"profil-{i}")
        self.assertLessEqual(f.groesse(), 100)

    def test_standard_und_ueberschreibung(self):
        self.assertEqual((labor_limit.ANFRAGEN_MAX, labor_limit.ANFRAGEN_FENSTER_SECS), (20, 60.0))
        with mock.patch.dict("os.environ", {"WAMO_LABOR_ANFRAGEN_MAX": "7"}):
            self.assertEqual(labor_limit.AnfragenFenster().maximum, 7)
        with mock.patch.dict("os.environ", {"WAMO_LABOR_ANFRAGEN_MAX": "nix"}):
            self.assertEqual(labor_limit.AnfragenFenster().maximum, labor_limit.ANFRAGEN_MAX)


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def lauf(self, mw, methode, pfad):
        gerufen, gesendet = [], []

        async def app(scope, receive, send):
            gerufen.append(scope["path"])
            await receive()

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(message):
            gesendet.append(message)

        scope = {"type": "http", "method": methode, "path": pfad, "headers": [(b"authorization", f"Bearer {token()}".encode())]}
        await AccessMiddleware(app, mw.gate, labor_pruefer=mw.pruefer, labor_fenster=mw.fenster)(scope, receive, send)
        return gerufen, gesendet

    def middleware(self, maximum=3, pruefungen=None, profil=None):
        async def pruefer(identity):
            if pruefungen is not None:
                pruefungen.append(identity)
            return Urteil()

        return SimpleNamespace(gate=gate_mit(profil or laborprofil()), pruefer=pruefer,
                               fenster=labor_limit.AnfragenFenster(maximum=maximum, secs=60, uhr=Uhr()))

    async def test_start_und_offer_sind_je_profil_begrenzt_und_fragen_den_dienst_dann_nicht_mehr(self):
        pruefungen: list = []
        mw = self.middleware(3, pruefungen)
        for methode, pfad in (("POST", "/start"), ("POST", "/api/offer"), ("POST", "/start")):
            gerufen, gesendet = await self.lauf(mw, methode, pfad)
            self.assertEqual(gerufen, [pfad])
        gerufen, gesendet = await self.lauf(mw, "POST", "/api/offer")
        self.assertEqual(gerufen, [])
        self.assertEqual(gesendet[0]["status"], 429)
        antwort = json.loads(gesendet[1]["body"])
        self.assertEqual(antwort["code"], labor.CODE_ZU_OFT)
        self.assertIn("Bitte kurz warten", antwort["detail"])
        self.assertEqual(len(pruefungen), 3)  # der vierte Aufruf hat den Dienst nicht belastet

    async def test_ice_und_die_labor_api_zaehlen_nicht(self):
        mw = self.middleware(1)
        for _ in range(5):
            gerufen, _ = await self.lauf(mw, "PATCH", "/api/offer")
            self.assertEqual(gerufen, ["/api/offer"])
        gerufen, _ = await self.lauf(mw, "POST", "/start")
        self.assertEqual(gerufen, ["/start"])

    async def test_ein_gesperrter_pfad_verbraucht_nichts(self):
        mw = self.middleware(1)
        for _ in range(5):
            gerufen, gesendet = await self.lauf(mw, "GET", "/wamo/api/profile")
            self.assertEqual((gerufen, gesendet[0]["status"]), ([], 403))
        gerufen, _ = await self.lauf(mw, "POST", "/start")
        self.assertEqual(gerufen, ["/start"])

    async def test_gewoehnliche_profile_sind_nie_begrenzt(self):
        from memory import Profile
        from test_auth_gate import USER

        mw = self.middleware(1, profil=Profile(user_id=USER, name="Erika", rolle="mitglied", aktiv=True, anrede="du"))
        for _ in range(5):
            gerufen, _ = await self.lauf(mw, "POST", "/start")
            self.assertEqual(gerufen, ["/start"])


class RtviProzessorTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_text_wird_je_sitzung_begrenzt_alles_andere_nicht(self):
        from pipecat.frames.frames import InputTransportMessageFrame
        from pipecat.processors.frame_processor import FrameDirection
        from pipecat.processors.frameworks.rtvi import RTVIProcessor

        from labor_rtvi import LaborRTVIProcessor
        from test_labor_sperre import client_msg, rtvi

        uhr = Uhr()
        verarbeiter = LaborRTVIProcessor(art="platz", begrenzer=labor_limit.TextBegrenzer(abstand_secs=2, max_zeichen=30, uhr=uhr))
        gesehen = []

        async def fake(self, frame, direction):
            gesehen.append(frame.message["data"].get("content") if frame.message["type"] == "send-text" else frame.message["type"])

        freigabe = client_msg("freigabe", {"job": "labor", "id": "a", "entscheidung": "ja"})
        with mock.patch.object(RTVIProcessor, "process_frame", fake):
            async def sende(nachricht):
                await verarbeiter.process_frame(InputTransportMessageFrame(message=nachricht), FrameDirection.DOWNSTREAM)

            await sende(rtvi("send-text", {"content": "a" * 10}))  # angenommen
            await sende(rtvi("send-text", {"content": "b" * 10}))  # zu schnell
            await sende(rtvi("client-ready", {"version": "1.0.0"}))  # nie begrenzt
            await sende(freigabe)  # nie begrenzt: der Klick auf eine Karte darf nicht am Textlimit hängen
            uhr.jetzt += 3
            await sende(rtvi("send-text", {"content": "c" * 10}))  # angenommen, 20 von 30
            uhr.jetzt += 3
            await sende(rtvi("send-text", {"content": "d" * 11}))  # 31 von 30: zu viel
            uhr.jetzt += 3
            await sende(rtvi("send-text", {"content": "e" * 10}))  # genau 30: angenommen
            uhr.jetzt += 3
            await sende(rtvi("send-text", {"content": "f"}))  # alles verbraucht
        self.assertEqual(gesehen, ["a" * 10, "client-ready", "client-message", "c" * 10, "e" * 10])

    async def test_freigabe_klicks_sind_nach_abstand_und_hoechstzahl_begrenzt(self):
        from pipecat.frames.frames import InputTransportMessageFrame
        from pipecat.processors.frame_processor import FrameDirection
        from pipecat.processors.frameworks.rtvi import RTVIProcessor

        from labor_rtvi import LaborRTVIProcessor
        from test_labor_sperre import client_msg, rtvi

        uhr = Uhr()
        verarbeiter = LaborRTVIProcessor(art="platz", klicks=labor_limit.KlickBegrenzer(abstand_secs=1, maximum=3, uhr=uhr))
        gesehen = []

        async def fake(self, frame, direction):
            gesehen.append(frame.message["data"]["d"]["id"] if frame.message["type"] == "client-message" else frame.message["type"])

        with mock.patch.object(RTVIProcessor, "process_frame", fake):
            async def sende(nachricht):
                await verarbeiter.process_frame(InputTransportMessageFrame(message=nachricht), FrameDirection.DOWNSTREAM)

            def klick(n):
                return client_msg("freigabe", {"job": "labor", "id": f"{n:032x}", "entscheidung": "ja"})

            await sende(klick(1))  # angenommen
            await sende(klick(2))  # zu schnell
            await sende(rtvi("send-text", {"content": "hallo"}))  # Text und Klick teilen keine Zähler
            uhr.jetzt += 1.5
            await sende(klick(3))  # angenommen
            uhr.jetzt += 1.5
            await sende(klick(4))  # angenommen, 3 von 3
            uhr.jetzt += 1.5
            await sende(klick(5))  # Höchstzahl erreicht
            uhr.jetzt += 3600
            await sende(klick(6))  # auch nach beliebiger Wartezeit
            await sende(rtvi("client-ready", {"version": "1.0.0"}))  # alles andere bleibt unbegrenzt
        self.assertEqual(gesehen, [f"{1:032x}", "send-text", f"{3:032x}", f"{4:032x}", "client-ready"])

    async def test_ohne_angabe_hat_jeder_verarbeiter_seine_eigene_begrenzung(self):
        from labor_rtvi import LaborRTVIProcessor

        a, b = LaborRTVIProcessor(art="platz"), LaborRTVIProcessor(art="schaufenster")
        self.assertIsNot(a.begrenzer, b.begrenzer)
        self.assertIsInstance(a.begrenzer, labor_limit.TextBegrenzer)
        self.assertIsNot(a.klicks, b.klicks)
        self.assertIsInstance(a.klicks, labor_limit.KlickBegrenzer)


class WebsucheTests(unittest.IsolatedAsyncioTestCase):
    async def test_hoechstens_n_suchen_je_sitzung_und_die_grenze_ist_nur_im_labor(self):
        import tools

        class LLM:
            def __init__(self):
                self.handler = {}

            def register_function(self, name, handler, **kw):
                self.handler[name] = handler

        async def suchen(llm, anzahl):
            ergebnisse = []
            for _ in range(anzahl):
                async def antwort(wert, **_kw):
                    ergebnisse.append(wert)

                await llm.handler["websuche"](SimpleNamespace(arguments={"anfrage": "Wetter"}, result_callback=antwort))
            return ergebnisse

        identity = SimpleNamespace(name="Gast", rolle="mitglied", is_admin=False)
        with mock.patch.object(tools, "websuche_ergebnis", mock.AsyncMock(return_value={"ergebnis": "sonnig"})) as suche:
            labor_llm = LLM()
            tools.register_tools(labor_llm, mock.Mock(), identity, zugang=mock.Mock(), erlaubt=labor.WERKZEUGE_BASIS, suchen_max=2)
            ergebnisse = await suchen(labor_llm, 4)
            self.assertEqual([e["ergebnis"] for e in ergebnisse[:2]], ["sonnig", "sonnig"])
            self.assertNotIn("sonnig", str(ergebnisse[2:]))
            self.assertIn("Suchen", ergebnisse[2]["ergebnis"])
            self.assertEqual(suche.await_count, 2)  # die dritte und vierte haben den Schlüssel nicht berührt
            suche.reset_mock()
            normal = LLM()
            tools.register_tools(normal, mock.Mock(), identity, zugang=mock.Mock())
            self.assertEqual(len(await suchen(normal, 15)), 15)  # ohne Angabe wie bisher unbegrenzt
            self.assertEqual(suche.await_count, 15)


if __name__ == "__main__":
    unittest.main()
