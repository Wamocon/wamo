"""Beweistests der Laborsperre (Plan Testlabor 3.1, P1b und P1d): T2, T3 (Schicht b), T8, T9 und die Konsistenzregeln.

Jede Einschränkung gilt fail-closed und wird im Kern durchgesetzt: Gate (labor_aktiv statt aktiv), Pfadliste der Middleware
(exakter Regex, nichts anderes), Filter der Client-Nachrichten vor dem RTVI-Verarbeiter. Ohne Netz, ohne Datenbank.

Ausführen:  python -m pytest -p no:cacheprovider -q test_labor_sperre.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import inspect
import json
import re
import unittest
import uuid
from pathlib import Path
from unittest import mock

import asyncpg
from fastapi import FastAPI
from fastapi.routing import APIRoute

import labor
import wamo_api
from auth_gate import AccessGate, AccessMiddleware, Identity, identity_from_body
from memory import DatabaseUnavailable, Profile, ProfileDirectory
from test_auth_gate import USER, FakeProfiles, token, verifier

SITZUNG = str(uuid.uuid4())


def laborprofil(**kw) -> Profile:
    """Ein gültiges Laborprofil, wie es die Migration 0018 zulässt: Mitglied, aktiv = false, labor_aktiv = true."""
    werte = dict(user_id=USER, name="Gast", rolle="mitglied", aktiv=False, anrede="du",
                 labor_art="platz", labor_platz=3, labor_aktiv=True)
    werte.update(kw)
    return Profile(**werte)


def gate_mit(profil: Profile | None) -> AccessGate:
    return AccessGate(verifier(), FakeProfiles({USER: profil} if profil else {}))


class ArtTests(unittest.TestCase):
    def test_bekannte_arten(self):
        self.assertEqual(labor.art_normalisieren("platz"), "platz")
        self.assertEqual(labor.art_normalisieren("schaufenster"), "schaufenster")

    def test_kein_labor_nur_bei_none(self):
        self.assertIsNone(labor.art_normalisieren(None))

    def test_t9_unbekannter_wert_ergibt_platz_nie_kein_labor(self):
        for roh in ("xyz", "", " ", "PLATZ", "Platz", "platz ", "schaufenster\n", 5, True, False, 0, b"platz", ["platz"]):
            with self.subTest(roh=roh):
                self.assertEqual(labor.art_normalisieren(roh), "platz")

    def test_ist_labor_an_der_identitaet(self):
        self.assertFalse(labor.ist_labor(Identity(USER, "Erika", "admin", "du")))
        self.assertTrue(labor.ist_labor(Identity(USER, "Gast", "mitglied", "du", labor_art="platz", labor_platz=1)))
        self.assertTrue(labor.ist_labor(Identity(USER, "Gast", "mitglied", "du", labor_art="irgendwas", labor_platz=1)))

    def test_identity_normalisiert_die_art(self):
        self.assertEqual(Identity(USER, "G", "mitglied", "du", labor_art="irgendwas", labor_platz=1).labor_art, "platz")
        self.assertIsNone(Identity(USER, "G", "mitglied", "du").labor_art)
        self.assertFalse(Identity(USER, "G", "mitglied", "du").labor_aktiv)

    def test_vergleich_der_identitaet_beruecksichtigt_labor(self):
        a = Identity(USER, "G", "mitglied", "du", labor_art="platz", labor_platz=1, labor_aktiv=True)
        self.assertNotEqual(a, Identity(USER, "G", "mitglied", "du"))
        self.assertNotEqual(a, Identity(USER, "G", "mitglied", "du", labor_art="schaufenster", labor_aktiv=True))
        self.assertNotEqual(a, Identity(USER, "G", "mitglied", "du", labor_art="platz", labor_platz=2, labor_aktiv=True))


class IdentitaetBodyTests(unittest.TestCase):
    def test_body_ohne_labor_bleibt_wie_bisher(self):
        body = Identity(USER, "Erika", "mitglied", "du", anmeldung=1760000000.0).to_body()
        self.assertEqual(set(body), {"user_id", "name", "rolle", "anrede", "anmeldung"})

    def test_rundreise_des_laborprofils(self):
        ident = Identity(USER, "Gast", "mitglied", "du", anmeldung=1760000000.0, labor_art="platz", labor_platz=3,
                         labor_aktiv=True)
        zurueck = identity_from_body({"wamo": ident.to_body()})
        self.assertEqual(zurueck, ident)
        self.assertEqual((zurueck.labor_art, zurueck.labor_platz, zurueck.labor_aktiv), ("platz", 3, True))

    def test_body_mit_unbekannter_art_wird_platz(self):
        body = Identity(USER, "Gast", "mitglied", "du", anmeldung=1760000000.0, labor_art="platz", labor_platz=3,
                        labor_aktiv=True).to_body()
        body["labor_art"] = "gast-vip"
        self.assertEqual(identity_from_body({"wamo": body}).labor_art, "platz")

    def test_body_mit_art_aber_ohne_platz_ist_keine_identitaet(self):
        body = Identity(USER, "Gast", "mitglied", "du", anmeldung=1760000000.0, labor_art="platz", labor_platz=3,
                        labor_aktiv=True).to_body()
        body.pop("labor_platz")
        self.assertIsNone(identity_from_body({"wamo": body}))

    def test_body_mit_labor_und_admin_ist_keine_identitaet(self):
        body = Identity(USER, "Gast", "mitglied", "du", anmeldung=1760000000.0, labor_art="platz", labor_platz=3,
                        labor_aktiv=True).to_body()
        body["rolle"] = "admin"
        self.assertIsNone(identity_from_body({"wamo": body}))


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def test_laborprofil_mit_labor_aktiv_kommt_durch(self):
        d = await gate_mit(laborprofil()).authorize(token())
        self.assertEqual(d.status, 200)
        i = d.identity
        self.assertEqual((i.rolle, i.labor_art, i.labor_platz, i.labor_aktiv), ("mitglied", "platz", 3, True))

    async def test_t8_labor_aktiv_falsch_ist_403(self):
        for aktiv in (False, None):
            with self.subTest(labor_aktiv=aktiv):
                d = await gate_mit(laborprofil(labor_aktiv=aktiv)).authorize(token())
                self.assertEqual((d.identity, d.status), (None, 403))
                self.assertIn("deaktiviert", d.reason)

    async def test_t8_der_vorige_stand_sieht_aktiv_false_und_lehnt_ab(self):
        # Ältere Kerne kennen labor_aktiv nicht: sie prüfen nur "aktiv", und das ist bei jedem Laborprofil dauerhaft falsch
        self.assertIs(laborprofil().aktiv, False)

    async def test_aktiv_allein_macht_kein_laborprofil_wirksam(self):
        for profil in (laborprofil(labor_aktiv=False, aktiv=True), laborprofil(aktiv=True)):
            with self.subTest(profil=profil):
                d = await gate_mit(profil).authorize(token())
                self.assertEqual((d.identity, d.status), (None, 403))

    async def test_gewoehnliches_profil_bleibt_unveraendert(self):
        d = await gate_mit(Profile(user_id=USER, name="Erika", rolle="admin", aktiv=True, anrede="du")).authorize(token())
        self.assertEqual(d.status, 200)
        self.assertIsNone(d.identity.labor_art)
        self.assertFalse(d.identity.labor_aktiv)
        d = await gate_mit(Profile(user_id=USER, name="Erika", rolle="admin", aktiv=False, anrede="du")).authorize(token())
        self.assertEqual((d.identity, d.status), (None, 403))

    async def test_labor_aktiv_ohne_labor_art_ersetzt_aktiv_nie(self):
        d = await gate_mit(Profile(user_id=USER, name="E", rolle="mitglied", aktiv=False, anrede="du",
                                   labor_aktiv=True)).authorize(token())
        self.assertEqual((d.identity, d.status), (None, 403))

    async def test_t9_unbekannte_art_ergibt_platz(self):
        d = await gate_mit(laborprofil(labor_art="sonstwas")).authorize(token())
        self.assertEqual((d.status, d.identity.labor_art), (200, "platz"))

    async def test_schaufenster_hat_keinen_platz(self):
        d = await gate_mit(laborprofil(labor_art="schaufenster", labor_platz=None)).authorize(token())
        self.assertEqual((d.status, d.identity.labor_art, d.identity.labor_platz), (200, "schaufenster", None))

    async def test_unstimmige_laborprofile_sind_403(self):
        faelle = {
            "admin": laborprofil(rolle="admin"),
            "platz ohne nummer": laborprofil(labor_platz=None),
            "platz null": laborprofil(labor_platz=0),
            "platz 17": laborprofil(labor_platz=17),
            "platz kein int": laborprofil(labor_platz="3"),
            "platz bool": laborprofil(labor_platz=True),
            "schaufenster mit platz": laborprofil(labor_art="schaufenster", labor_platz=3),
        }
        for name, profil in faelle.items():
            with self.subTest(name):
                d = await gate_mit(profil).authorize(token())
                self.assertEqual((d.identity, d.status), (None, 403))
                self.assertIn("Laborprofil", d.reason)

    async def test_laborprofil_wird_als_treffer_gecacht(self):
        zeit = [0.0]
        profile = FakeProfiles({USER: laborprofil()})
        gate = AccessGate(verifier(), profile, clock=lambda: zeit[0])
        await gate.authorize(token())
        zeit[0] = 30.0
        await gate.authorize(token())
        self.assertEqual(profile.calls, 1)  # Treffer gelten 60 s, auch bei labor_aktiv

    async def test_fehlende_spalte_heisst_ausfall_nicht_oeffnung(self):
        class Db:
            configured = True
            schema = "wamo_test"

            def t(self, name):
                return f"wamo_test.{name}"

            async def fetchrow(self, sql, *args):
                raise asyncpg.UndefinedColumnError('Spalte "labor_art" existiert nicht')

        verzeichnis = ProfileDirectory(Db())
        with self.assertRaises(DatabaseUnavailable):
            await verzeichnis.get(USER)
        d = await AccessGate(verifier(), verzeichnis).authorize(token())
        self.assertEqual((d.identity, d.status), (None, 503))


class ProfilVerzeichnisTests(unittest.IsolatedAsyncioTestCase):
    async def test_select_liest_die_laborspalten_und_bildet_das_profil(self):
        gesehen = {}

        class Db:
            configured = True
            schema = "wamo_test"

            def t(self, name):
                return f"wamo_test.{name}"

            async def fetchrow(self, sql, *args):
                gesehen["sql"] = sql
                return {"user_id": USER, "display_name": "Gast", "rolle": "mitglied", "aktiv": False, "anrede": "du",
                        "voice_id": None, "labor_art": "platz", "labor_platz": 4, "labor_aktiv": True}

        profil = await ProfileDirectory(Db()).get(USER)
        self.assertIn("labor_art", gesehen["sql"])
        self.assertIn("labor_platz", gesehen["sql"])
        self.assertIn("labor_aktiv", gesehen["sql"])
        self.assertEqual((profil.labor_art, profil.labor_platz, profil.labor_aktiv, profil.aktiv), ("platz", 4, True, False))

    async def test_profil_ohne_laborwerte(self):
        class Db:
            configured = True
            schema = "wamo_test"

            def t(self, name):
                return f"wamo_test.{name}"

            async def fetchrow(self, sql, *args):
                return {"user_id": USER, "display_name": "Erika", "rolle": "admin", "aktiv": True, "anrede": "du",
                        "voice_id": None, "labor_art": None, "labor_platz": None, "labor_aktiv": False}

        profil = await ProfileDirectory(Db()).get(USER)
        self.assertEqual((profil.labor_art, profil.labor_platz, profil.labor_aktiv), (None, None, False))


# ---------- HTTP-Pfade (T2) ----------
MIT_ID = f"/sessions/{SITZUNG}/api/offer"
ERLAUBT = [("POST", "/start"), ("POST", "/api/offer"), ("PATCH", "/api/offer"),
           ("POST", MIT_ID), ("PATCH", MIT_ID),
           ("GET", "/wamo/api/labor/durchlauf"), ("POST", "/wamo/api/labor/ende")]


def pipecat_routen() -> list[tuple[str, str]]:
    """Alle Routen des installierten Pipecat-Runners, gelesen aus dem Quelltext (keine feste Liste)."""
    import pipecat.runner.run as runner

    quelle = Path(inspect.getsourcefile(runner)).read_text(encoding="utf-8")
    pfade = re.findall(r'@app\.(?:get|post|patch|put|delete|api_route|mount)\(\s*"([^"]+)"', quelle)
    routen = []
    for pfad in pfade:
        for beispiel in ("api/offer", "x", "api/offer/x"):
            p = pfad.replace("{session_id}", SITZUNG).replace("{path:path}", beispiel)
            p = re.sub(r"\{[^}]+\}", "abc", p)
            for methode in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                routen.append((methode, p))
    return routen


def kern_routen() -> list[tuple[str, str]]:
    """Alle Routen unter /wamo/api, dynamisch aus den Modulen gelesen."""
    app = FastAPI()
    wamo_api.installieren(app, None)
    routen = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            pfad = re.sub(r"\{[^}]+\}", "abc", route.path)
            for methode in route.methods:
                routen.append((methode, pfad))
            for methode in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                routen.append((methode, pfad))
    return routen


ZUSATZ = [(m, p) for m in ("GET", "POST", "PUT", "PATCH", "DELETE") for p in (
    "/", "/status", "/docs", "/redoc", "/openapi.json", "/client/", "/client/index.html", "/files/x", "/ws", "/ws-client",
    "/daily", "/wamo/slot", "/wamo/release/abc/freigeben", "/wamo/api", "/wamo/api/", "/wamo/api/profile",
    "/wamo/api/teilen", "/wamo/api/buero/status", "/wamo/api/geraete", "/wamo/api/modellzugang", "/wamo/api/release",
    "/wamo/api/hinweise", "/wamo/api/rd", "/wamo/api/vorgaenge", "/wamo/api/dateien", "/wamo/api/fernzugriff",
)]


class PfadTests(unittest.TestCase):
    def test_erlaubt_ist_genau_die_liste(self):
        for methode, pfad in ERLAUBT:
            with self.subTest(methode=methode, pfad=pfad):
                self.assertIsNotNone(labor.pfad_pruefen("platz", methode, pfad))
                self.assertIsNotNone(labor.pfad_pruefen("schaufenster", methode, pfad))

    def test_arten_der_erlaubten_pfade(self):
        self.assertEqual(labor.pfad_pruefen("platz", "POST", "/start"), "start")
        self.assertEqual(labor.pfad_pruefen("platz", "POST", "/api/offer"), "offer")
        self.assertEqual(labor.pfad_pruefen("platz", "POST", MIT_ID), "offer")
        self.assertEqual(labor.pfad_pruefen("platz", "PATCH", MIT_ID), "ice")
        self.assertEqual(labor.pfad_pruefen("platz", "GET", "/wamo/api/labor/durchlauf"), "api")

    def test_t2_jede_route_der_apps_ist_gesperrt_ausser_der_liste(self):
        routen = set(pipecat_routen()) | set(kern_routen()) | set(ZUSATZ)
        self.assertGreater(len(routen), 100)
        durchgelassen = {(m, p) for m, p in routen if labor.pfad_pruefen("platz", m, p) is not None}
        erwartet = set(ERLAUBT)
        for m, p in durchgelassen:
            self.assertTrue((m, p) in erwartet or p.startswith("/wamo/api/labor/"), f"{m} {p} ist nicht gesperrt")
        # und die internen Module sind wirklich gelesen worden
        self.assertTrue(any(p.startswith("/wamo/api/profile") or p.startswith("/wamo/api/teilen") for _, p in routen))

    def test_t2_pfadvarianten_sind_gesperrt(self):
        varianten = [
            "//start", "/start/", "/START", "/Start", "/start%2f", "/st%61rt", "/start;x", "/start?x=1", "/start\n", "/start\x00",
            "/start\\", "/stärt", "/ start", "/start ", "start", "", "/", "//", "/start//", "//api/offer", "/api//offer",
            "/api/./offer", "/api/offer/", "/API/offer", "/api/Offer", "/sessions/../start", "/sessions/x/../../start",
            f"/sessions/{SITZUNG.upper()}/api/offer", "/sessions/abc/api/offer", f"/sessions/{SITZUNG}/api/offer/",
            f"/sessions/{SITZUNG}/xapi/offer", f"/sessions/{SITZUNG}//api/offer", f"/sessions/{SITZUNG}/api/offer%2f",
            f"/sessions/{SITZUNG}/api/offer/../x", f"//sessions/{SITZUNG}/api/offer",
            "//wamo/api/profile", "/wamo/api//profile", "/wamo%2fapi/profile", "/wamo/api%2fprofile", "/wamo/api%2Fprofile",
            "/wamo/api%252fprofile", "/WAMO/api/profile", "/wamo/API/profile", "/wamo/api/PROFILE", "/wamo/api/profile/",
            "/wamo/api/./profile", "/wamo/api/labor/../profile", "/wamo/api/labor/x/../../profile",
            "/wamo/api/labor/%2e%2e/profile", "/wamo/api/labor/..%2fprofile", "/wamo/api/labor/", "/wamo/api/labor",
            "/wamo/api/labor/UPPER", "/wamo/api/labor/a%2fb", "/wamo/api/labor/a/b", "/wamo/api/labor//x",
            "/wamo/api/labor/ä", "/wamo/api/labor/x/", "/wamo/api/labor/" + "a" * 33,
            "/wamo/api/labor%2fx", "/wamo/api/labor\\x", "/wamo/api/labor/x\x00", "/wamo/api/labor/1x",
        ]
        for pfad in varianten:
            for methode in ("GET", "POST", "PATCH", "PUT", "DELETE"):
                with self.subTest(pfad=pfad, methode=methode):
                    self.assertIsNone(labor.pfad_pruefen("platz", methode, pfad))

    def test_t2_methoden_sind_exakt(self):
        for methode in ("GET", "PUT", "DELETE", "HEAD", "OPTIONS", "get", "Post", "post", "PATCH ", "", "TRACE"):
            self.assertIsNone(labor.pfad_pruefen("platz", methode, "/start"), methode)
        for methode in ("GET", "PUT", "DELETE", "post", "patch"):
            self.assertIsNone(labor.pfad_pruefen("platz", methode, "/api/offer"), methode)
        for methode in ("PUT", "DELETE", "PATCH", "get"):
            self.assertIsNone(labor.pfad_pruefen("platz", methode, "/wamo/api/labor/x"), methode)

    def test_t2_roher_pfad_mit_prozentcodierung_ist_gesperrt(self):
        self.assertIsNone(labor.pfad_pruefen("platz", "POST", "/start", raw_path=b"/st%61rt"))
        self.assertIsNone(labor.pfad_pruefen("platz", "POST", "/start", raw_path=b"/start%2f"))
        self.assertIsNone(labor.pfad_pruefen("platz", "POST", "/start", raw_path=b"//start"))
        self.assertIsNone(labor.pfad_pruefen("platz", "POST", "/start", raw_path=b"/START"))
        self.assertEqual(labor.pfad_pruefen("platz", "POST", "/start", raw_path=b"/start"), "start")
        self.assertEqual(labor.pfad_pruefen("platz", "POST", "/start", raw_path=b"/start?a=b"), "start")

    def test_schaufenster_darf_zusaetzlich_die_vorgaenge_lesen(self):
        self.assertIsNone(labor.pfad_pruefen("platz", "GET", "/wamo/api/vorgaenge"))
        self.assertEqual(labor.pfad_pruefen("schaufenster", "GET", "/wamo/api/vorgaenge"), "api")
        self.assertIsNone(labor.pfad_pruefen("schaufenster", "POST", "/wamo/api/vorgaenge"))
        self.assertIsNone(labor.pfad_pruefen("schaufenster", "GET", "/wamo/api/vorgaenge/x"))

    def test_unbekannte_art_wird_wie_platz_geprueft(self):
        self.assertIsNone(labor.pfad_pruefen("gast", "GET", "/wamo/api/vorgaenge"))
        self.assertIsNone(labor.pfad_pruefen(None, "GET", "/wamo/api/profile"))


class Urteil:
    """Ersatz für labor_durchlauf.Urteil: nur Status, Code und Grund zählen für die Middleware."""

    def __init__(self, status=200, code=None, grund=""):
        self.status, self.code, self.grund = status, code, grund


class MiddlewareLaborTests(unittest.IsolatedAsyncioTestCase):
    async def lauf(self, methode, pfad, profil, *, pruefer=None, body=b"{}", raw_path=None):
        gerufen, gesendet, gesehen = [], [], {}
        pruefungen = []

        async def app(scope, receive, send):
            gerufen.append((scope["method"], scope["path"]))
            msg = await receive()
            gesehen["body"] = msg.get("body", b"")

        queue = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive():
            return queue.pop(0) if queue else {"type": "http.disconnect"}

        async def send(message):
            gesendet.append(message)

        async def standard_pruefer(identity):
            pruefungen.append(identity)
            return Urteil()

        scope = {"type": "http", "method": methode, "path": pfad,
                 "headers": [(b"authorization", f"Bearer {token()}".encode()), (b"content-length", b"2")]}
        if raw_path is not None:
            scope["raw_path"] = raw_path
        mw = AccessMiddleware(app, gate_mit(profil), labor_pruefer=pruefer or standard_pruefer)
        await mw(scope, receive, send)
        return gerufen, gesendet, gesehen, pruefungen

    async def test_gesperrter_pfad_403_mit_code_und_app_unberuehrt(self):
        for pfad in ("/wamo/api/profile", "//wamo/api/profile", "/wamo/api/teilen", "/status", "/docs", "/wamo/api%2fprofile",
                     "/wamo/api/labor/../profile", "/start/", "/WAMO/api/profile"):
            with self.subTest(pfad=pfad):
                gerufen, gesendet, _, pruefungen = await self.lauf("GET", pfad, laborprofil())
                self.assertEqual(gerufen, [])
                self.assertEqual(gesendet[0]["status"], 403)
                antwort = json.loads(gesendet[1]["body"])
                self.assertEqual(antwort["code"], "labor_pfad_gesperrt")
                self.assertNotIn("profile", antwort["detail"].lower())  # keine Auskunft über interne Pfade
                self.assertEqual(pruefungen, [])

    async def test_gesperrter_roher_pfad(self):
        gerufen, gesendet, _, _ = await self.lauf("POST", "/start", laborprofil(), raw_path=b"/st%61rt")
        self.assertEqual((gerufen, gesendet[0]["status"]), ([], 403))

    async def test_start_und_offer_gehen_durch_die_durchlaufpruefung(self):
        for methode, pfad in (("POST", "/start"), ("POST", "/api/offer"), ("POST", MIT_ID)):
            with self.subTest(pfad=pfad):
                gerufen, _, gesehen, pruefungen = await self.lauf(methode, pfad, laborprofil())
                self.assertEqual(gerufen, [(methode, pfad)])
                self.assertEqual(len(pruefungen), 1)
                self.assertEqual(pruefungen[0].labor_art, "platz")
                self.assertEqual(json.loads(gesehen["body"])["body" if pfad == "/start" else "request_data"]["wamo"]["labor_art"],
                                 "platz")

    async def test_ice_und_labor_api_ohne_durchlaufpruefung_aber_nur_auf_der_liste(self):
        for methode, pfad in (("PATCH", "/api/offer"), ("PATCH", MIT_ID), ("GET", "/wamo/api/labor/durchlauf")):
            with self.subTest(pfad=pfad):
                gerufen, _, _, pruefungen = await self.lauf(methode, pfad, laborprofil())
                self.assertEqual(gerufen, [(methode, pfad)])
                self.assertEqual(pruefungen, [])

    async def test_durchlauf_beendet_oder_ohne_einwilligung_oder_dienst_weg(self):
        for status, code in ((403, "labor_durchlauf_beendet"), (403, "labor_keine_einwilligung"),
                             (503, "labor_dienst_nicht_erreichbar")):
            with self.subTest(code=code):
                async def pruefer(identity, _s=status, _c=code):
                    return Urteil(_s, _c, "Grund")

                gerufen, gesendet, _, _ = await self.lauf("POST", "/start", laborprofil(), pruefer=pruefer)
                self.assertEqual(gerufen, [])
                self.assertEqual(gesendet[0]["status"], status)
                self.assertEqual(json.loads(gesendet[1]["body"])["code"], code)

    async def test_ein_fehler_in_der_pruefung_ist_503_nie_durchlass(self):
        async def kaputt(identity):
            raise RuntimeError("unerwartet")

        gerufen, gesendet, _, _ = await self.lauf("POST", "/api/offer", laborprofil(), pruefer=kaputt)
        self.assertEqual((gerufen, gesendet[0]["status"]), ([], 503))

    async def test_gewoehnliches_profil_hat_keine_pfadliste_und_keine_durchlaufpruefung(self):
        profil = Profile(user_id=USER, name="Erika", rolle="mitglied", aktiv=True, anrede="du")
        for methode, pfad in (("GET", "/wamo/api/profile"), ("POST", "/start/"), ("GET", "/status")):
            with self.subTest(pfad=pfad):
                gerufen, _, _, pruefungen = await self.lauf(methode, pfad, profil)
                self.assertEqual(gerufen, [(methode, pfad)])
                self.assertEqual(pruefungen, [])

    async def test_ohne_dienst_und_ohne_einstellung_ist_zu(self):
        # Standardpruefer ohne registrierte Quelle: fail-closed
        import labor_durchlauf

        with mock.patch.object(labor_durchlauf, "_quelle", None), mock.patch.dict("os.environ", {}, clear=False):
            gerufen, gesendet = await self.lauf_standard("POST", "/start")
        self.assertEqual((gerufen, gesendet[0]["status"]), ([], 503))

    async def lauf_standard(self, methode, pfad):
        gerufen, gesendet = [], []

        async def app(scope, receive, send):
            gerufen.append(scope["path"])

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(message):
            gesendet.append(message)

        scope = {"type": "http", "method": methode, "path": pfad,
                 "headers": [(b"authorization", f"Bearer {token()}".encode())]}
        await AccessMiddleware(app, gate_mit(laborprofil()))(scope, receive, send)
        return gerufen, gesendet


# ---------- Client-Nachrichten, Schicht b (vor dem RTVI-Verarbeiter) ----------
def rtvi_typen() -> list[str]:
    """Alle Nachrichtentypen, die der installierte RTVI-Verarbeiter kennt, gelesen aus seinem Quelltext."""
    from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor

    quelle = inspect.getsource(RTVIProcessor._handle_message)
    typen: list[str] = []
    for gruppe in re.findall(r'case ((?:"[a-z-]+"(?: \| )?)+):', quelle):
        typen += re.findall(r'"([a-z-]+)"', gruppe)
    return typen


def rtvi(typ, data=None):
    return {"label": "rtvi-ai", "type": typ, "id": "1", "data": data}


def client_msg(t, d=None):
    return rtvi("client-message", {"t": t, "d": d})


class RtviFilterTests(unittest.TestCase):
    def test_die_typen_des_installierten_pipecat_sind_gelesen(self):
        typen = rtvi_typen()
        for erwartet in ("client-ready", "send-text", "client-message", "llm-function-call-result", "disconnect-bot", "dtmf"):
            self.assertIn(erwartet, typen)

    def test_t3_jeder_typ_ausser_der_erlaubnisliste_wird_verworfen(self):
        erlaubt = {"client-ready", "send-text", "client-message"}
        for typ in rtvi_typen() + ["append-to-context", "action", "update-config", "describe-config", "get-available-actions",
                                   "llm-function-call-result", "raw-audio", "dtmf", "unbekannt", "", "CLIENT-READY", "send-text "]:
            for art in ("platz", "schaufenster"):
                with self.subTest(typ=typ, art=art):
                    if typ in erlaubt and typ != "client-message":
                        self.assertTrue(labor.rtvi_nachricht_erlaubt(art, rtvi(typ, {"content": "hallo"})))
                    elif typ != "client-message":
                        self.assertFalse(labor.rtvi_nachricht_erlaubt(art, rtvi(typ, {"x": 1})))

    def test_t3_client_message_nur_die_labor_freigabe(self):
        for art in ("platz", "schaufenster"):
            self.assertTrue(labor.rtvi_nachricht_erlaubt(art, client_msg("freigabe", {"job": "labor", "id": "a", "entscheidung": "ja"})))
            for t, d in (("vergessen", {"id": "x"}), ("pc_steuerung", {"an": True}), ("bildstrom", {"aktion": "an"}),
                         ("modellzugang", {"aktion": "status"}), ("kontingent", {}), ("auftrag_start", {"job": "j", "entscheidung": "ja"}),
                         ("freigabe", {"job": "pc", "id": "a", "entscheidung": "ja"}),
                         ("freigabe", {"job": "abc123", "id": "a", "entscheidung": "ja"}),
                         ("freigabe", {"job": "Labor"}), ("freigabe", {"job": ["labor"]}), ("freigabe", {}), ("freigabe", None),
                         ("freigabe", "labor"), ("Freigabe", {"job": "labor"}), ("freigabe ", {"job": "labor"}), (None, {"job": "labor"}),
                         ("", {}), (5, {})):
                with self.subTest(art=art, t=t, d=d):
                    self.assertFalse(labor.rtvi_nachricht_erlaubt(art, client_msg(t, d)))

    def test_t3_kaputte_nachrichten(self):
        for roh in (None, "client-ready", 5, [], {}, {"type": "send-text"}, {"label": "andere", "type": "send-text"},
                    {"label": "rtvi-ai"}, {"label": "rtvi-ai", "type": None}, {"label": "rtvi-ai", "type": ["send-text"]},
                    {"label": "rtvi-ai", "type": "client-message", "data": "x"}, {"label": "rtvi-ai", "type": "client-message"},
                    {"label": "rtvi-ai", "type": "client-message", "data": {"t": "freigabe"}}):
            with self.subTest(roh=roh):
                self.assertFalse(labor.rtvi_nachricht_erlaubt("platz", roh))

    def test_client_nachricht_erlaubt_schicht_a(self):
        self.assertTrue(labor.client_nachricht_erlaubt("platz", "freigabe", {"job": "labor"}))
        for typ in ("vergessen", "pc_steuerung", "bildstrom", "modellzugang", "kontingent", "auftrag_start", "freigabe", None, ""):
            self.assertFalse(labor.client_nachricht_erlaubt("platz", typ, {"job": "pc"}))
        self.assertFalse(labor.client_nachricht_erlaubt("platz", "freigabe", None))
        self.assertFalse(labor.client_nachricht_erlaubt("platz", "freigabe", {"job": "labor "}))


class RtviProzessorTests(unittest.IsolatedAsyncioTestCase):
    async def test_der_laborverarbeiter_verwirft_vor_dem_rtvi_verarbeiter(self):
        from pipecat.frames.frames import InputTransportMessageFrame, TextFrame
        from pipecat.processors.frame_processor import FrameDirection
        from pipecat.processors.frameworks.rtvi import RTVIProcessor

        from labor_rtvi import LaborRTVIProcessor

        verarbeiter = LaborRTVIProcessor(art="platz")
        self.assertIsInstance(verarbeiter, RTVIProcessor)
        gesehen = []

        async def fake(self, frame, direction):
            gesehen.append(frame)

        with mock.patch.object(RTVIProcessor, "process_frame", fake):
            erlaubt = [rtvi("client-ready", {"version": "1.0.0"}), rtvi("send-text", {"content": "hallo"}),
                       client_msg("freigabe", {"job": "labor", "id": "a", "entscheidung": "ja"})]
            verboten = [rtvi(t, {"x": 1}) for t in rtvi_typen() if t not in ("client-ready", "send-text", "client-message")]
            verboten += [rtvi("append-to-context", {}), rtvi("action", {}), rtvi("update-config", {}),
                         client_msg("pc_steuerung", {"an": True}), client_msg("modellzugang", {"aktion": "status"}),
                         client_msg("freigabe", {"job": "pc"})]
            for nachricht in erlaubt + verboten:
                await verarbeiter.process_frame(InputTransportMessageFrame(message=nachricht), FrameDirection.UPSTREAM)
            self.assertEqual([f.message for f in gesehen], erlaubt)
            # andere Frames gehen unverändert durch
            await verarbeiter.process_frame(TextFrame("x"), FrameDirection.DOWNSTREAM)
            self.assertEqual(len(gesehen), len(erlaubt) + 1)


class EntwicklungGateTests(unittest.TestCase):
    def test_t12_entwicklungsarten_je_art(self):
        platz = Identity(USER, "G", "mitglied", "du", labor_art="platz", labor_platz=1, labor_aktiv=True)
        schau = Identity(USER, "G", "mitglied", "du", labor_art="schaufenster", labor_aktiv=True)
        self.assertEqual(labor.entwicklung_arten(platz), frozenset())
        self.assertEqual(labor.entwicklung_arten(schau), frozenset({"recherche"}))
        self.assertEqual(labor.entwicklung_arten(Identity(USER, "G", "mitglied", "du", labor_art="x", labor_platz=1)), frozenset())

    def test_kein_labor_kein_urteil(self):
        with self.assertRaises(ValueError):
            labor.entwicklung_arten(Identity(USER, "E", "admin", "du"))


if __name__ == "__main__":
    unittest.main()
