"""Tests für auth_gate.py: Token-Prüfung, Profil-Gate und Middleware (ohne Netz, eigene Schlüssel).

Ausführen:  uv run python -m pytest -q test_auth_gate.py   (im Ordner voice-core/)
"""

import json
import time
import unittest
from types import SimpleNamespace

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from auth_gate import (
    MAX_BODY_BYTES,
    AccessGate,
    AccessMiddleware,
    Identity,
    TokenCheck,
    TokenVerifier,
    identity_from_body,
    inject_identity,
)
from memory import DatabaseUnavailable, Profile

USER = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"  # neutrale Test-UUID
OTHER = "7d1e9c42-3b6a-4f8e-8c2d-5a9b0e1f2c3d"
URL = "https://wamo-test.example"
ISS = f"{URL}/auth/v1"
KEY = ec.generate_private_key(ec.SECP256R1())
FREMD = ec.generate_private_key(ec.SECP256R1())
HS_SECRET = "nur-ein-testgeheimnis-fuer-hs256-tokens"


class FakeJwks:
    """Steht für PyJWKClient: liefert immer den öffentlichen Testschlüssel."""

    def __init__(self):
        self.calls = 0

    def get_signing_key_from_jwt(self, token):
        self.calls += 1
        return SimpleNamespace(key=KEY.public_key())


class FakeProfiles:
    def __init__(self, profiles=None, error=None):
        self.profiles = profiles or {}
        self.error = error
        self.calls = 0

    async def get(self, user_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.profiles.get(user_id)


def profile(user_id=USER, aktiv=True, rolle="admin", anrede="du", name="Erika Muster"):
    return Profile(user_id=user_id, name=name, rolle=rolle, aktiv=aktiv, anrede=anrede)


def token(key=KEY, alg="ES256", **override):
    now = int(time.time())
    claims = {
        "sub": USER,
        "aal": "aal2",
        "aud": "authenticated",
        "iss": ISS,
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(override)
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test"})


def verifier(transport=None):
    return TokenVerifier(URL, "sb_publishable_test", jwks_client=FakeJwks(), transport=transport)


def gate(profiles=None, clock=None):
    profiles = FakeProfiles({USER: profile()}) if profiles is None else profiles
    kwargs = {"clock": clock} if clock else {}
    return AccessGate(verifier(), profiles, **kwargs)


class TokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_gueltiges_token(self):
        self.assertEqual(await verifier().check(token()), TokenCheck(USER))

    async def test_ohne_token(self):
        self.assertEqual(await verifier().check(""), TokenCheck(None, "nicht angemeldet", 401))

    async def test_abgelaufen(self):
        self.assertEqual((await verifier().check(token(exp=int(time.time()) - 120))).reason, "Anmeldung abgelaufen")

    async def test_ohne_zweiten_faktor(self):
        self.assertEqual((await verifier().check(token(aal="aal1"))).reason, "zweiter Faktor fehlt")

    async def test_sub_keine_uuid(self):
        self.assertEqual((await verifier().check(token(sub="jemand"))).reason, "Token ohne gültiges Konto")

    async def test_falscher_aussteller(self):
        reason = (await verifier().check(token(iss="https://anders.example/auth/v1"))).reason
        self.assertTrue(reason.startswith("Token ungültig"), reason)

    async def test_eigener_aussteller_konfigurierbar(self):
        v = TokenVerifier(URL, None, issuer="http://kong:8000/auth/v1", jwks_client=FakeJwks())
        self.assertEqual(await v.check(token(iss="http://kong:8000/auth/v1")), TokenCheck(USER))

    async def test_falsche_zielgruppe(self):
        reason = (await verifier().check(token(aud="anon"))).reason
        self.assertTrue(reason.startswith("Token ungültig"), reason)

    async def test_fremder_schluessel(self):
        reason = (await verifier().check(token(key=FREMD))).reason
        self.assertTrue(reason.startswith("Token ungültig"), reason)

    async def test_unsigniert_abgelehnt(self):
        unsigned = jwt.encode({"sub": USER, "aal": "aal2"}, None, algorithm="none")
        self.assertEqual((await verifier().check(unsigned)).reason, "Signaturverfahren none nicht erlaubt")

    async def test_kaputtes_token(self):
        self.assertTrue((await verifier().check("kein.jwt")).reason.startswith("Token ungültig"))

    async def test_ohne_konfiguration_alles_zu(self):
        v = TokenVerifier(None, None, jwks_client=FakeJwks())
        self.assertIn("ohne", (await v.check(token())).reason)

    async def test_cache_spart_schluesselabfrage(self):
        v = verifier()
        t = token()
        await v.check(t)
        self.assertEqual(await v.check(t), TokenCheck(USER))
        self.assertEqual(v._jwks.calls, 1)

    async def test_hs256_prueft_gotrue(self):
        def antwort(request):
            self.assertEqual(request.url.path, "/auth/v1/user")
            self.assertEqual(request.headers["apikey"], "sb_publishable_test")
            return httpx.Response(200, json={"id": USER})

        v = verifier(httpx.MockTransport(antwort))
        self.assertEqual(await v.check(token(key=HS_SECRET, alg="HS256")), TokenCheck(USER))

    async def test_hs256_von_gotrue_abgelehnt(self):
        v = verifier(httpx.MockTransport(lambda r: httpx.Response(401, json={"msg": "invalid"})))
        reason = (await v.check(token(key=HS_SECRET, alg="HS256"))).reason
        self.assertTrue(reason.startswith("Token ungültig"), reason)

    async def test_hs256_anderer_nutzer(self):
        v = verifier(httpx.MockTransport(lambda r: httpx.Response(200, json={"id": OTHER})))
        reason = (await v.check(token(key=HS_SECRET, alg="HS256"))).reason
        self.assertTrue(reason.startswith("Token ungültig"), reason)

    async def test_hs256_abgelaufen_trotz_gotrue_ok(self):
        v = verifier(httpx.MockTransport(lambda r: httpx.Response(200, json={"id": USER})))
        reason = (await v.check(token(key=HS_SECRET, alg="HS256", exp=int(time.time()) - 120))).reason
        self.assertEqual(reason, "Anmeldung abgelaufen")


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def test_gueltig_liefert_identitaet(self):
        d = await gate().authorize(token())
        self.assertEqual(d.status, 200)
        self.assertEqual(d.identity, Identity(USER, "Erika Muster", "admin", "du"))

    async def test_aal1_abgelehnt_403(self):
        # Token gültig, aber zweiter Faktor fehlt: 403 (UI: "kein aktives Profil oder zweiter Faktor fehlt")
        d = await gate().authorize(token(aal="aal1"))
        self.assertIsNone(d.identity)
        self.assertEqual((d.status, d.reason), (403, "zweiter Faktor fehlt"))

    async def test_ungueltiges_token_401(self):
        for t in ["", "kein.jwt", token(key=FREMD), token(exp=int(time.time()) - 120)]:
            with self.subTest(t=t[:12]):
                self.assertEqual((await gate().authorize(t)).status, 401)

    async def test_kein_profil_403(self):
        d = await gate(FakeProfiles({})).authorize(token())
        self.assertIsNone(d.identity)
        self.assertEqual(d.status, 403)
        self.assertIn("kein WAMO-Profil", d.reason)

    async def test_inaktives_profil_403(self):
        d = await gate(FakeProfiles({USER: profile(aktiv=False)})).authorize(token())
        self.assertIsNone(d.identity)
        self.assertEqual(d.status, 403)
        self.assertIn("deaktiviert", d.reason)

    async def test_datenbank_nicht_erreichbar_503(self):
        d = await gate(FakeProfiles(error=DatabaseUnavailable("weg"))).authorize(token())
        self.assertIsNone(d.identity)
        self.assertEqual(d.status, 503)

    async def test_unbekannter_fehler_fail_closed(self):
        d = await gate(FakeProfiles(error=RuntimeError("kaputt"))).authorize(token())
        self.assertIsNone(d.identity)
        self.assertEqual(d.status, 503)

    async def test_ohne_datenbank_alles_zu(self):
        d = await AccessGate(verifier(), None).authorize(token())
        self.assertIsNone(d.identity)
        self.assertEqual(d.status, 503)

    async def test_unbekannte_rolle_wird_mitglied(self):
        d = await gate(FakeProfiles({USER: profile(rolle="chef")})).authorize(token())
        self.assertEqual(d.identity.rolle, "mitglied")

    async def test_cache_positiv_60_negativ_10(self):
        now = [1000.0]
        hit = FakeProfiles({USER: profile()})
        g = gate(hit, clock=lambda: now[0])
        await g.authorize(token())
        now[0] += 59
        await g.authorize(token())
        self.assertEqual(hit.calls, 1)
        now[0] += 2
        await g.authorize(token())
        self.assertEqual(hit.calls, 2)

        miss = FakeProfiles({})
        g = gate(miss, clock=lambda: now[0])
        await g.authorize(token())
        now[0] += 9
        await g.authorize(token())
        self.assertEqual(miss.calls, 1)
        now[0] += 2
        await g.authorize(token())
        self.assertEqual(miss.calls, 2)

    async def test_fehler_werden_nicht_gecacht(self):
        p = FakeProfiles(error=DatabaseUnavailable("weg"))
        g = gate(p)
        await g.authorize(token())
        p.error = None
        p.profiles = {USER: profile()}
        self.assertIsNotNone((await g.authorize(token())).identity)


class IdentityTests(unittest.TestCase):
    def test_from_body(self):
        body = {"wamo": {"user_id": USER, "name": " Erika  Muster ", "rolle": "mitglied", "anrede": "sie"}}
        self.assertEqual(identity_from_body(body), Identity(USER, "Erika Muster", "mitglied", "sie"))

    def test_from_body_unvollstaendig(self):
        for body in [None, {}, {"wamo": "x"}, {"wamo": {"user_id": "keine-uuid", "name": "A", "rolle": "admin", "anrede": "du"}},
                     {"wamo": {"user_id": USER, "name": "", "rolle": "admin", "anrede": "du"}},
                     {"wamo": {"user_id": USER, "name": "A", "rolle": "gott", "anrede": "du"}},
                     {"wamo": {"user_id": USER, "name": "A", "rolle": "admin", "anrede": "ihr"}}]:
            with self.subTest(body=body):
                self.assertIsNone(identity_from_body(body))

    def test_inject_start_ueberschreibt_faelschung(self):
        ident = Identity(USER, "Erika", "mitglied", "du")
        raw = json.dumps({"body": {"wamo": {"user_id": OTHER, "rolle": "admin"}, "x": 1}}).encode()
        data = json.loads(inject_identity("start", raw, ident))
        self.assertEqual(data["body"], {"x": 1, "wamo": ident.to_body()})

    def test_inject_offer_request_data_und_camelcase(self):
        ident = Identity(USER, "Erika", "mitglied", "du")
        fake = {"user_id": OTHER, "name": "Boss", "rolle": "admin", "anrede": "du"}
        for raw in [{"sdp": "v=0", "type": "offer", "requestData": {"wamo": fake, "k": 2}},
                    {"sdp": "v=0", "type": "offer", "request_data": {"wamo": fake, "k": 2}, "requestData": {"wamo": fake}},
                    {"sdp": "v=0", "type": "offer", "request_data": "kein-objekt", "requestData": {"k": 2, "wamo": fake}}]:
            with self.subTest(raw=raw):
                data = json.loads(inject_identity("offer", json.dumps(raw).encode(), ident))
                self.assertNotIn("requestData", data)
                self.assertEqual(data["request_data"], {"k": 2, "wamo": ident.to_body()})

    def test_inject_offer_ohne_daten(self):
        ident = Identity(USER, "Erika", "mitglied", "du")
        data = json.loads(inject_identity("offer", b'{"sdp": "v=0", "type": "offer"}', ident))
        self.assertEqual(data["request_data"], {"wamo": ident.to_body()})


class MiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def run_mw(self, scope, body=b"", profiles=None, chunks=None):
        called, sent, seen = [], [], {}

        async def app(scope, receive, send):
            called.append(scope["type"])
            if scope["type"] == "http":
                msg = await receive()
                seen["body"] = msg.get("body", b"")
                seen["headers"] = dict(scope["headers"])

        parts = chunks if chunks is not None else [body]
        queue = [{"type": "http.request", "body": p, "more_body": i < len(parts) - 1} for i, p in enumerate(parts)]

        async def receive():
            return queue.pop(0) if queue else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await AccessMiddleware(app, gate(profiles))(scope, receive, send)
        return called, sent, seen

    def http(self, method="POST", auth=None, path="/start"):
        headers = [(b"content-length", b"0")]
        if auth:
            headers.append((b"authorization", auth.encode()))
        return {"type": "http", "method": method, "path": path, "headers": headers}

    async def test_websocket_abgelehnt(self):
        called, sent, _ = await self.run_mw({"type": "websocket", "path": "/ws-client", "headers": []})
        self.assertEqual(called, [])
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])

    async def test_preflight_offen(self):
        called, _, _ = await self.run_mw(self.http("OPTIONS"))
        self.assertEqual(called, ["http"])

    async def test_ohne_token_401(self):
        called, sent, _ = await self.run_mw(self.http())
        self.assertEqual(called, [])
        self.assertEqual(sent[0]["status"], 401)

    async def test_kein_profil_403_nichts_durchgereicht(self):
        called, sent, _ = await self.run_mw(self.http(auth=f"Bearer {token()}"), b"{}", profiles=FakeProfiles({}))
        self.assertEqual(called, [])
        self.assertEqual(sent[0]["status"], 403)
        self.assertIn("kein WAMO-Profil", json.loads(sent[1]["body"])["detail"])

    async def test_inaktiv_403(self):
        p = FakeProfiles({USER: profile(aktiv=False)})
        called, sent, _ = await self.run_mw(self.http(auth=f"Bearer {token()}"), b"{}", profiles=p)
        self.assertEqual((called, sent[0]["status"]), ([], 403))

    async def test_aal1_403(self):
        called, sent, _ = await self.run_mw(self.http(auth=f"Bearer {token(aal='aal1')}"), b"{}")
        self.assertEqual((called, sent[0]["status"]), ([], 403))

    async def test_kaputte_bodies_ohne_ausnahme(self):
        for raw in [b'{"body": {"x": "\\ud800"}}', b"[" * 100000 + b"]" * 100000]:
            with self.subTest(raw=raw[:20]):
                called, sent, seen = await self.run_mw(self.http(auth=f"Bearer {token()}"), raw)
                if called:  # Surrogat bleibt als gültiges JSON-Escape erhalten
                    self.assertEqual(json.loads(seen["body"])["body"]["wamo"]["user_id"], USER)
                else:
                    self.assertEqual(sent[0]["status"], 400)

    async def test_gotrue_antwort_kein_json(self):
        v = verifier(httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>")))
        reason = (await v.check(token(key=HS_SECRET, alg="HS256"))).reason
        self.assertTrue(reason.startswith("Token ungültig"), reason)

    async def test_start_bekommt_identitaet_faelschung_ersetzt(self):
        forged = {"body": {"wamo": {"user_id": OTHER, "name": "Boss", "rolle": "admin", "anrede": "du"}}}
        p = FakeProfiles({USER: profile(rolle="mitglied", anrede="sie")})
        called, _, seen = await self.run_mw(self.http(auth=f"Bearer {token()}"), json.dumps(forged).encode(), profiles=p)
        self.assertEqual(called, ["http"])
        body = json.loads(seen["body"])
        self.assertEqual(body["body"]["wamo"], {"user_id": USER, "name": "Erika Muster", "rolle": "mitglied", "anrede": "sie"})
        self.assertEqual(seen["headers"][b"content-length"], str(len(seen["body"])).encode())

    async def test_offer_pfade_bekommen_identitaet(self):
        forged = {"sdp": "v=0", "type": "offer", "requestData": {"wamo": {"user_id": OTHER, "rolle": "admin"}}}
        for path in ["/api/offer", "/sessions/abc/api/offer", "/sessions/abc/xapi/offer", "/start/"]:
            with self.subTest(path=path):
                called, _, seen = await self.run_mw(
                    self.http(auth=f"Bearer {token()}", path=path), json.dumps(forged).encode())
                self.assertEqual(called, ["http"])
                data = json.loads(seen["body"])
                ident = (data.get("request_data") or data.get("body"))["wamo"]
                self.assertEqual(ident["user_id"], USER)
                self.assertEqual(identity_from_body(data.get("request_data") or data.get("body")).user_id, USER)

    async def test_body_in_teilen(self):
        raw = json.dumps({"sdp": "v=0", "type": "offer"}).encode()
        called, _, seen = await self.run_mw(
            self.http(auth=f"Bearer {token()}", path="/api/offer"), chunks=[raw[:5], raw[5:]])
        self.assertEqual(called, ["http"])
        self.assertEqual(json.loads(seen["body"])["request_data"]["wamo"]["user_id"], USER)

    async def test_ungueltiges_json_400(self):
        called, sent, _ = await self.run_mw(self.http(auth=f"Bearer {token()}"), b"{kaputt")
        self.assertEqual((called, sent[0]["status"]), ([], 400))

    async def test_zu_gross_413(self):
        called, sent, _ = await self.run_mw(self.http(auth=f"Bearer {token()}"), b"x" * (MAX_BODY_BYTES + 1))
        self.assertEqual((called, sent[0]["status"]), ([], 413))

    async def test_andere_pfade_unveraendert(self):
        called, _, seen = await self.run_mw(self.http("PATCH", auth=f"Bearer {token()}", path="/api/offer"), b'{"pc_id": "x"}')
        self.assertEqual(called, ["http"])
        self.assertEqual(seen["body"], b'{"pc_id": "x"}')


if __name__ == "__main__":
    unittest.main()
