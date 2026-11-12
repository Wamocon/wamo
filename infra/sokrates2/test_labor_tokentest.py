"""Tests für infra/sokrates2/labor_tokentest.py (Token-Test des Testlabors, Befund 11 in docs/testlabor-wamo.md).

Aufruf (im Ordner infra/sokrates2, die Umgebung von voice-core genügt):
  D:/IDEA/Projekt/wamo/voice-core/.venv/Scripts/python.exe -m pytest -p no:cacheprovider -q test_labor_tokentest.py
Kein Netz und kein Server: Eine Attrappe im Prozess (lokaler HTTP-Server auf 127.0.0.1) spielt GoTrue, PostgREST, Storage und Realtime nach.
Jede Attrappe merkt sich alle Geheimnisse, die sie sieht oder ausgibt (Schlüssel, Passwörter, Token, TOTP-Geheimnis, Zeilen- und Bucket-Inhalt).
Jeder Lauf prüft danach, dass keines davon in der Ausgabe steht, und dass kein Wegwerf-Konto übrig bleibt.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labor_tokentest as lt  # noqa: E402

QUELLE = Path(lt.__file__)
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ZEILEN_MARKE = "GEHEIME-ZEILE-4711"
BUCKET_MARKE = "geheimer-eimer-4712"
FREMDE_MAIL = "fremder-nutzer-4713@example.org"

STANDARD = {
    "passwort_ohne_reauth": False,   # PUT /auth/v1/user ändert das Passwort ohne erneute Anmeldung
    "admin_offen": False,            # Admin-Pfade antworten dem Besucher-Token mit 200
    "signup_offen": False,           # POST /auth/v1/signup legt ein Konto an
    "invite_offen": False,           # POST /auth/v1/invite legt ein Konto an
    "rest": "dicht",                 # dicht | leer | offen
    "rest_wurzel_wamo": False,       # das Verzeichnis unter /rest/v1/ nennt WAMO-Schema und WAMO-Tabelle
    "storage": "leer",               # leer | offen | fehler500 | fehler_mit_geheimnis | umleitung
    "realtime": "dicht",             # dicht | privat_offen | cdc_offen | kein | stumm | abgewiesen
    "passwort_wirkungslos": False,   # PUT /auth/v1/user antwortet 200, ändert aber nichts
    "signup_429": False,             # POST /auth/v1/signup antwortet mit der Ratenbegrenzung
    "user_lesen": "ok",              # ok | abgelehnt | fremd
    "verify_geht": True,             # der TOTP-Code wird angenommen
    "aal2_claim": True,              # das Token nach dem Code trägt aal2
    "loeschen_geht": True,           # DELETE /auth/v1/admin/users/<id> klappt
    "anlegen_geht": True,            # POST /auth/v1/admin/users mit dem Dienstschlüssel klappt
    "faktor_loeschen_geht": True,    # DELETE /auth/v1/factors/<id> klappt
}


def _b64(objekt: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(objekt).encode()).decode().rstrip("=")


class Zustand:
    """Gedächtnis der Attrappe: Konten, Token, Faktoren, Aufrufe und alle Geheimnisse."""

    def __init__(self, **opt: object):
        unbekannt = set(opt) - set(STANDARD)
        assert not unbekannt, unbekannt
        self.opt = {**STANDARD, **opt}
        self.service = "SRK-" + secrets.token_hex(12)
        self.anon = "ANON-" + secrets.token_hex(12)
        self.nutzer: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.faktoren: dict[str, dict] = {}
        self.geloescht: list[str] = []
        self.anfragen: list[tuple[str, str]] = []
        self.beitritte: list[dict] = []
        self.anmeldungen: list[tuple[str, int]] = []
        self.geheim: list[str] = [self.service, self.anon, ZEILEN_MARKE, BUCKET_MARKE, FREMDE_MAIL]

    def neuer_nutzer(self, email: str, passwort: str | None) -> str:
        uid = str(uuid.uuid4())
        self.nutzer[uid] = {"email": email, "passwort": passwort}
        if passwort:
            self.geheim.append(passwort)
        return uid

    def neues_token(self, uid: str, aal: str) -> dict:
        jetzt = int(time.time())
        amr = [{"method": "password", "timestamp": jetzt}]
        if aal == "aal2":
            amr.insert(0, {"method": "totp", "timestamp": jetzt})
        token = ".".join([_b64({"alg": "ES256", "typ": "JWT"}), _b64({"sub": uid, "aal": aal, "amr": amr, "role": "authenticated"}),
                          secrets.token_urlsafe(40)])
        refresh = secrets.token_urlsafe(14)
        self.tokens[token] = {"uid": uid, "aal": aal}
        self.geheim += [token, refresh]
        return {"access_token": token, "refresh_token": refresh, "token_type": "bearer", "expires_in": 3600}


def _handler(z: Zustand):
    class Attrappe(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D401
            return

        def _koerper(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            roh = self.rfile.read(n) if n else b""
            try:
                return json.loads(roh) if roh else {}
            except ValueError:
                return {}

        def _senden(self, status: int, objekt: object = None) -> None:
            roh = json.dumps({} if objekt is None else objekt).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(roh)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(roh)

        def _rolle(self) -> tuple[str, str]:
            auth = self.headers.get("Authorization", "")
            t = auth[7:] if auth.startswith("Bearer ") else ""
            if t == z.service:
                return "service", t
            if t in z.tokens:
                return "besucher", t
            if t == z.anon:
                return "anon", t
            return "keine", t

        def _nicht_admin(self, rolle: str) -> None:
            if rolle == "besucher":
                self._senden(403, {"code": 403, "error_code": "not_admin", "msg": "User not allowed"})
            else:
                self._senden(401, {"code": 401, "error_code": "no_authorization", "msg": "This endpoint requires a Bearer token"})

        def _verarbeiten(self, methode: str) -> None:
            pfad, _, abfrage = self.path.partition("?")
            z.anfragen.append((methode, pfad))
            o = z.opt
            if pfad.startswith("/realtime/v1/"):
                return self._realtime()
            body = self._koerper() if methode in ("POST", "PUT", "DELETE") else {}
            rolle, token = self._rolle()
            if pfad == "/auth/v1/admin/users":
                if rolle == "service" or (rolle == "besucher" and o["admin_offen"]):
                    if methode == "GET":
                        return self._senden(200, {"users": [{"id": "x", "email": FREMDE_MAIL}]})
                    if rolle == "service" and not o["anlegen_geht"]:
                        return self._senden(500, {"msg": "kaputt " + token})
                    if body.get("password"):
                        z.geheim.append(body["password"])
                    uid = z.neuer_nutzer(body.get("email", ""), body.get("password"))
                    return self._senden(200, {"id": uid, "email": body.get("email")})
                return self._nicht_admin(rolle)
            if pfad.startswith("/auth/v1/admin/users/") and methode == "DELETE":
                if rolle != "service":
                    return self._nicht_admin(rolle)
                if not o["loeschen_geht"]:
                    return self._senden(500, {"msg": "kaputt " + token})
                uid = pfad.rsplit("/", 1)[1]
                if z.nutzer.pop(uid, None) is None:
                    return self._senden(404, {"error_code": "user_not_found"})
                z.geloescht.append(uid)
                return self._senden(200, {})
            if pfad == "/auth/v1/token" and methode == "POST":
                for uid, n in z.nutzer.items():
                    if n["email"] == body.get("email") and n["passwort"] == body.get("password"):
                        z.anmeldungen.append((n["email"], 200))
                        return self._senden(200, z.neues_token(uid, "aal1"))
                z.anmeldungen.append((str(body.get("email")), 400))
                return self._senden(400, {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"})
            if pfad == "/auth/v1/user":
                if rolle != "besucher":
                    return self._senden(401, {"error_code": "bad_jwt"})
                uid = z.tokens[token]["uid"]
                if methode == "GET":
                    if o["user_lesen"] == "abgelehnt":
                        return self._senden(401, {"error_code": "bad_jwt", "msg": "invalid JWT " + token})
                    if o["user_lesen"] == "fremd":
                        return self._senden(200, {"id": str(uuid.uuid4())})
                    return self._senden(200, {"id": uid, "email": z.nutzer[uid]["email"]})
                if methode == "PUT":
                    neu = body.get("password")
                    if neu:
                        z.geheim.append(neu)
                    if neu and o["passwort_wirkungslos"]:
                        return self._senden(200, {"id": uid})
                    if neu and o["passwort_ohne_reauth"]:
                        z.nutzer[uid]["passwort"] = neu
                        return self._senden(200, {"id": uid})
                    return self._senden(400, {"code": 400, "error_code": "reauthentication_needed", "msg": "Password update requires reauthentication"})
            if pfad == "/auth/v1/factors" and methode == "POST":
                if rolle != "besucher":
                    return self._senden(401, {"error_code": "bad_jwt"})
                fid = str(uuid.uuid4())
                geheimnis = base64.b32encode(os.urandom(10)).decode().rstrip("=")
                z.faktoren[fid] = {"uid": z.tokens[token]["uid"], "secret": geheimnis, "verified": False}
                z.geheim.append(geheimnis)
                return self._senden(200, {"id": fid, "type": "totp", "totp": {"secret": geheimnis, "uri": "otpauth://totp/x?secret=" + geheimnis}})
            if pfad.startswith("/auth/v1/factors/"):
                teile = pfad.split("/")
                fid = teile[4]
                f = z.faktoren.get(fid)
                if rolle != "besucher" or f is None:
                    return self._senden(404, {"error_code": "mfa_factor_not_found"})
                if methode == "DELETE" and len(teile) == 5:
                    if not o["faktor_loeschen_geht"]:
                        return self._senden(403, {"error_code": "insufficient_aal"})
                    z.faktoren.pop(fid)
                    return self._senden(200, {"id": fid})
                if methode == "POST" and teile[5:] == ["challenge"]:
                    return self._senden(200, {"id": "chal-" + secrets.token_hex(4)})
                if methode == "POST" and teile[5:] == ["verify"]:
                    gueltig = {lt.totp_code(f["secret"], time.time() + d) for d in (-30, 0, 30)}
                    if not o["verify_geht"] or body.get("code") not in gueltig:
                        return self._senden(400, {"error_code": "mfa_verification_failed"})
                    f["verified"] = True
                    return self._senden(200, z.neues_token(f["uid"], "aal2" if o["aal2_claim"] else "aal1"))
            if pfad == "/auth/v1/signup" and methode == "POST":
                if o["signup_429"]:
                    return self._senden(429, {"error_code": "over_request_rate_limit"})
                if o["signup_offen"]:
                    return self._senden(200, {"id": z.neuer_nutzer(body.get("email", ""), body.get("password"))})
                return self._senden(422, {"code": 422, "error_code": "signup_disabled", "msg": "Signups not allowed for this instance"})
            if pfad == "/auth/v1/invite" and methode == "POST":
                if rolle == "besucher" and o["invite_offen"]:
                    return self._senden(200, {"id": z.neuer_nutzer(body.get("email", ""), None)})
                return self._nicht_admin(rolle)
            if pfad == "/auth/v1/recover" and methode == "POST":
                return self._senden(200, {})
            if pfad == "/rest/v1/" and methode == "GET":
                paths = {"/": {}}
                if o["rest_wurzel_wamo"]:
                    paths["/profiles"] = {}
                return self._senden(200, {"swagger": "2.0", "info": {"title": "standard public schema" if not o["rest_wurzel_wamo"] else "wamo_dev"}, "paths": paths})
            if pfad.startswith("/rest/v1/") and methode == "GET":
                profil = self.headers.get("Accept-Profile")
                if o["rest"] == "offen":
                    return self._senden(200, [{"id": ZEILEN_MARKE}])
                if o["rest"] == "leer":
                    return self._senden(200, [])
                if profil:
                    return self._senden(406, {"code": "PGRST106", "message": "The schema must be one of the following: public"})
                return self._senden(404, {"code": "PGRST205", "message": "Could not find the table 'public." + pfad.rsplit("/", 1)[1] + "'"})
            if pfad == "/storage/v1/ziel":
                return self._senden(200, [{"id": BUCKET_MARKE}])
            if pfad == "/storage/v1/bucket" and methode == "GET":
                if o["storage"] == "umleitung":
                    self.send_response(302)
                    self.send_header("Location", "/storage/v1/ziel")
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return
                if o["storage"] == "fehler_mit_geheimnis":
                    # ein Server, der als Fehlerkennung ausgerechnet ein Geheimnis zurückgibt (hier das TOTP-Geheimnis des Wegwerf-Kontos)
                    return self._senden(500, {"error_code": next(iter(z.faktoren.values()))["secret"]})
                if o["storage"] == "offen":
                    return self._senden(200, [{"id": BUCKET_MARKE, "name": BUCKET_MARKE}])
                if o["storage"] == "fehler500":
                    return self._senden(500, {"message": "interner Fehler bei " + token})
                return self._senden(200, [])
            return self._senden(404, {"message": "no route"})

        def _realtime(self) -> None:
            modus = z.opt["realtime"]
            if modus == "kein":
                return self._senden(404, {"message": "no route"})
            if modus == "abgewiesen":
                return self._senden(401, {"message": "unauthorized"})
            schluessel = self.headers.get("Sec-WebSocket-Key")
            if not schluessel or self.headers.get("Upgrade", "").lower() != "websocket":
                return self._senden(400, {})
            akzept = base64.b64encode(hashlib.sha1((schluessel + WS_GUID).encode()).digest()).decode()
            self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                              f"Sec-WebSocket-Accept: {akzept}\r\n\r\n").encode())
            self.wfile.flush()
            kopf = self.rfile.read(2)
            laenge = kopf[1] & 0x7F
            if laenge == 126:
                laenge = int.from_bytes(self.rfile.read(2), "big")
            maske = self.rfile.read(4)
            daten = bytes(b ^ maske[i % 4] for i, b in enumerate(self.rfile.read(laenge)))
            nachricht = json.loads(daten)
            z.beitritte.append(nachricht)
            if modus == "stumm":
                time.sleep(1.0)
                return
            konfig = nachricht["payload"].get("config", {})

            def rahmen(objekt: dict) -> bytes:
                roh = json.dumps(objekt).encode()
                return bytes([0x81, len(roh)]) + roh if len(roh) < 126 else bytes([0x81, 126]) + len(roh).to_bytes(2, "big") + roh

            def antwort(status: str, antwort_inhalt: dict) -> dict:
                return {"ref": nachricht.get("ref"), "topic": nachricht["topic"], "event": "phx_reply",
                        "payload": {"status": status, "response": antwort_inhalt}}
            if konfig.get("private"):
                if modus == "privat_offen":
                    self.wfile.write(rahmen(antwort("ok", {})))
                else:
                    self.wfile.write(rahmen(antwort("error", {"reason": "You do not have permissions to read from this Channel topic"})))
            else:
                if modus == "cdc_offen":
                    self.wfile.write(rahmen(antwort("ok", {"postgres_changes": [{"id": 1, "event": "*", "schema": "wamo_dev", "table": "*"}]})))
                else:
                    self.wfile.write(rahmen(antwort("ok", {"postgres_changes": []})))
                    self.wfile.write(rahmen({"ref": None, "topic": nachricht["topic"], "event": "system",
                                             "payload": {"status": "error", "message": "Unable to subscribe to changes with given parameters"}}))
            self.wfile.write(b"\x88\x00")
            self.wfile.flush()

        def do_GET(self):  # noqa: N802
            self._verarbeiten("GET")

        def do_POST(self):  # noqa: N802
            self._verarbeiten("POST")

        def do_PUT(self):  # noqa: N802
            self._verarbeiten("PUT")

        def do_DELETE(self):  # noqa: N802
            self._verarbeiten("DELETE")

    return Attrappe


@contextlib.contextmanager
def attrappe(**opt):
    z = Zustand(**opt)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(z))
    server.daemon_threads = True
    faden = threading.Thread(target=server.serve_forever, daemon=True)
    faden.start()
    try:
        yield z, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        faden.join(5)


@pytest.fixture(autouse=True)
def kurze_fristen(monkeypatch):
    monkeypatch.setattr(lt, "WS_WARTEZEIT_S", 3.0)
    monkeypatch.setattr(lt, "WS_NACHLAUF_S", 0.3)


def laufen(z: Zustand, url: str, tmp_path: Path, capsys, *extra: str, aufraeumen: bool = True):
    env = tmp_path / "wamo.env"
    env.write_text(f"# Attrappe\nANON_KEY={z.anon}\nSERVICE_ROLE_KEY={z.service}\n", encoding="utf-8")
    code, zeilen = lt.lauf(["--basis", url, "--env-datei", str(env), *extra])
    gesehen = capsys.readouterr()
    for g in z.geheim:
        assert g not in gesehen.out, "Geheimnis in der Ausgabe"
        assert g not in gesehen.err, "Geheimnis in der Fehlerausgabe"
    if aufraeumen:
        assert z.nutzer == {}, "Wegwerf-Konto oder fremd angelegtes Konto blieb übrig"
    return code, zeilen, gesehen.out


def zeile(zeilen, teil: str):
    treffer = [r for r in zeilen if teil in r.endpunkt]
    assert len(treffer) == 1, (teil, [r.endpunkt for r in treffer])
    return treffer[0]


def urteil(zeilen, teil: str) -> str:
    return zeile(zeilen, teil).urteil


# ---------------------------------------------------------------------------------------------------------------------------------
# Alles dicht
# ---------------------------------------------------------------------------------------------------------------------------------

ERWARTUNG_DICHT = {
    "GET /auth/v1/user": lt.DOKUMENTIERT,
    "Verzeichnis": lt.ERWARTET,
    "ohne Schema-Profil": lt.ERWARTET,
    "Schema-Profil wamo_dev": lt.ERWARTET,
    "Schema-Profil wamo_test": lt.ERWARTET,
    "Schema-Profil wamo_prod": lt.ERWARTET,
    "Schreiben": lt.NICHT_GEMESSEN,
    "GET /storage/v1/bucket": lt.ERWARTET,
    "Handshake": lt.DOKUMENTIERT,
    "privaten Kanal": lt.ERWARTET,
    "Änderungen": lt.ERWARTET,
    "GET /auth/v1/admin/users": lt.ERWARTET,
    "POST /auth/v1/admin/users": lt.ERWARTET,
    "POST /auth/v1/signup": lt.ERWARTET,
    "POST /auth/v1/invite": lt.ERWARTET,
    "POST /auth/v1/recover": lt.DOKUMENTIERT,
    "POST /auth/v1/factors": lt.DOKUMENTIERT,
    "neuer Faktor": lt.DOKUMENTIERT,
    "PUT /auth/v1/user": lt.ERWARTET,
    "bestätigter Faktor": lt.DOKUMENTIERT,
}


def test_alles_dicht_gibt_exit_0(tmp_path, capsys):
    with attrappe() as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 0
    assert {k: urteil(zeilen, k) for k in ERWARTUNG_DICHT} == ERWARTUNG_DICHT
    assert len(zeilen) == len(ERWARTUNG_DICHT)
    assert lt.OFFEN not in [r.urteil for r in zeilen]
    assert len(z.geloescht) == 1


def test_ausgabe_ist_eine_tabelle_mit_zusammenfassung(tmp_path, capsys):
    with attrappe() as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    for spalte in ("Endpunkt", "Erwartung", "Messung", "Urteil"):
        assert spalte in out
    for r in zeilen:
        assert r.messung.strip() and r.erwartung.strip()
    assert "Wegwerf-Konto gelöscht" in out
    assert "0 OFFEN" in out
    assert "nicht gemessen" in out


def test_messungen_zeigen_nur_status_und_kennung(tmp_path, capsys):
    with attrappe() as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert "HTTP 403" in zeile(zeilen, "GET /auth/v1/admin/users").messung
    assert "not_admin" in zeile(zeilen, "GET /auth/v1/admin/users").messung
    assert "HTTP 422" in zeile(zeilen, "POST /auth/v1/signup").messung
    assert "HTTP 406" in zeile(zeilen, "Schema-Profil wamo_dev").messung
    assert "reauthentication_needed" in zeile(zeilen, "PUT /auth/v1/user").messung


def test_besucher_token_geht_an_alle_schnittstellen_und_nie_der_dienstschluessel(tmp_path, capsys):
    """Gemessen wird mit dem Besucher-Token. Der Dienstschlüssel darf nur für Anlegen und Löschen des Wegwerf-Kontos dienen."""
    with attrappe() as (z, url):
        laufen(z, url, tmp_path, capsys)
    pfade = {p for _, p in z.anfragen}
    assert {"/rest/v1/", "/storage/v1/bucket", "/auth/v1/user", "/auth/v1/signup", "/auth/v1/invite", "/auth/v1/recover",
            "/auth/v1/factors", "/auth/v1/admin/users", "/realtime/v1/websocket"} <= pfade
    assert z.beitritte and all(b["payload"]["access_token"] in z.tokens for b in z.beitritte)
    assert z.beitritte[0]["payload"]["config"]["private"] is True


def test_recover_trifft_nur_eine_nicht_vorhandene_adresse(tmp_path, capsys):
    """recover geht nie an die Adresse eines vorhandenen Kontos, auch nicht an die des Wegwerf-Kontos (es soll keine Post entstehen)."""
    mails: list[str] = []
    vorhandene: list[set[str]] = []

    with attrappe() as (z, url):
        original = lt.Messlauf.anfrage

        def mitschnitt(self, methode, pfad, **kw):
            if pfad == "/auth/v1/recover":
                mails.append(kw["daten"]["email"])
                vorhandene.append({n["email"] for n in z.nutzer.values()})
            return original(self, methode, pfad, **kw)
        lt.Messlauf.anfrage = mitschnitt
        try:
            laufen(z, url, tmp_path, capsys)
        finally:
            lt.Messlauf.anfrage = original
    assert len(mails) == 1 and mails[0].endswith("@example.org")
    assert vorhandene[0] and mails[0] not in vorhandene[0]


# ---------------------------------------------------------------------------------------------------------------------------------
# Fälle mit OFFEN
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("opt, schluessel", [
    ({"passwort_ohne_reauth": True}, ["PUT /auth/v1/user"]),
    ({"admin_offen": True}, ["GET /auth/v1/admin/users", "POST /auth/v1/admin/users"]),
    ({"signup_offen": True}, ["POST /auth/v1/signup"]),
    ({"invite_offen": True}, ["POST /auth/v1/invite"]),
    ({"rest": "offen"}, ["ohne Schema-Profil", "Schema-Profil wamo_dev", "Schema-Profil wamo_test", "Schema-Profil wamo_prod"]),
    ({"rest_wurzel_wamo": True}, ["Verzeichnis"]),
    ({"storage": "offen"}, ["GET /storage/v1/bucket"]),
    ({"realtime": "privat_offen"}, ["privaten Kanal"]),
], ids=["passwort", "admin", "signup", "invite", "rest_zeilen", "rest_wurzel", "storage", "realtime_privat"])
def test_offen_gibt_exit_1_und_raeumt_auf(opt, schluessel, tmp_path, capsys):
    with attrappe(**opt) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 1
    assert sorted(r.endpunkt for r in zeilen if r.urteil == lt.OFFEN) == sorted(zeile(zeilen, k).endpunkt for k in schluessel)
    assert "OFFEN" in out


def test_passwort_ohne_reauth_nennt_klaren_satz_und_gegenmassnahme(tmp_path, capsys):
    with attrappe(passwort_ohne_reauth=True) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    r = zeile(zeilen, "PUT /auth/v1/user")
    assert r.urteil == lt.OFFEN
    assert "HTTP 200" in r.messung
    assert "altes Passwort danach ungültig" in r.messung
    assert "Platz" in r.hinweis and "ohne erneute Anmeldung" in r.hinweis
    assert "GOTRUE_SECURITY_UPDATE_PASSWORD_REQUIRE_REAUTHENTICATION=true" in r.hinweis
    assert "am Server" in r.hinweis
    assert "GOTRUE_SECURITY_UPDATE_PASSWORD_REQUIRE_REAUTHENTICATION=true" in out
    # Anmeldung vor der Änderung (200), danach das alte Passwort (abgewiesen) und das neue (angenommen)
    assert [status for _, status in z.anmeldungen] == [200, 400, 200]


def test_admin_offen_loescht_auch_das_von_ihm_angelegte_konto(tmp_path, capsys):
    with attrappe(admin_offen=True) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 1
    assert len(z.geloescht) == 2


def test_signup_und_invite_offen_loeschen_ihre_konten(tmp_path, capsys):
    with attrappe(signup_offen=True, invite_offen=True) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 1
    assert len(z.geloescht) == 3


def test_rest_offen_gibt_keine_zeilen_aus(tmp_path, capsys):
    with attrappe(rest="offen") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert ZEILEN_MARKE not in out
    assert "Zeile" in zeile(zeilen, "ohne Schema-Profil").messung


def test_rest_leer_ist_erwartet_aber_wird_benannt(tmp_path, capsys):
    with attrappe(rest="leer") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 0
    r = zeile(zeilen, "Schema-Profil wamo_dev")
    assert r.urteil == lt.ERWARTET and "leer" in r.messung


def test_storage_offen_nennt_keine_bucket_namen(tmp_path, capsys):
    with attrappe(storage="offen") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert BUCKET_MARKE not in out


# ---------------------------------------------------------------------------------------------------------------------------------
# Nicht gemessen statt raten
# ---------------------------------------------------------------------------------------------------------------------------------

def test_realtime_nicht_erreichbar_ist_nicht_gemessen(tmp_path, capsys):
    with attrappe(realtime="kein") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 0
    for k in ("Handshake", "privaten Kanal", "Änderungen"):
        assert urteil(zeilen, k) == lt.NICHT_GEMESSEN
    assert "HTTP 404" in zeile(zeilen, "Handshake").messung


def test_realtime_stumm_ist_nicht_gemessen(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(lt, "WS_WARTEZEIT_S", 0.3)
    with attrappe(realtime="stumm") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 0
    assert urteil(zeilen, "Handshake") == lt.DOKUMENTIERT
    assert urteil(zeilen, "privaten Kanal") == lt.NICHT_GEMESSEN
    assert urteil(zeilen, "Änderungen") == lt.NICHT_GEMESSEN


def test_realtime_beitritt_zu_aenderungen_ohne_gegenprobe_bleibt_nicht_gemessen(tmp_path, capsys):
    with attrappe(realtime="cdc_offen") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 0
    r = zeile(zeilen, "Änderungen")
    assert r.urteil == lt.NICHT_GEMESSEN
    assert "Änderung" in r.hinweis


def test_server_fehler_ist_nicht_gemessen_und_das_echo_des_tokens_bleibt_unsichtbar(tmp_path, capsys):
    with attrappe(storage="fehler500") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 0
    r = zeile(zeilen, "GET /storage/v1/bucket")
    assert r.urteil == lt.NICHT_GEMESSEN and "HTTP 500" in r.messung
    assert "interner Fehler" not in out  # Antworttexte des Servers werden nie ausgegeben, nur Status und Kennung


def test_geheimnis_als_fehlerkennung_wird_geschwaerzt(tmp_path, capsys):
    """Zweite Sicherung: Selbst eine Kennung, die ein Geheimnis ist, kommt nicht durch (maskiert in Zeile und Ausgabe)."""
    with attrappe(storage="fehler_mit_geheimnis") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    r = zeile(zeilen, "GET /storage/v1/bucket")
    assert r.urteil == lt.NICHT_GEMESSEN and r.messung == "HTTP 500 ***"
    assert "HTTP 500 ***" in out


def test_umleitung_wird_nicht_verfolgt(tmp_path, capsys):
    with attrappe(storage="umleitung") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    r = zeile(zeilen, "GET /storage/v1/bucket")
    assert r.urteil == lt.NICHT_GEMESSEN and "HTTP 302" in r.messung
    assert ("GET", "/storage/v1/ziel") not in z.anfragen
    assert code == 0


def test_ausgabe_maskiert_bekannte_geheimnisse(capsys):
    lt.GEHEIMNISSE.clear()
    lt.merke("ein-Geheimnis-4242")
    lt.ausgabe("vorher ein-Geheimnis-4242 nachher")
    assert capsys.readouterr().out == "vorher *** nachher\n"
    lt.GEHEIMNISSE.clear()


def test_anlegen_fehler_zeigt_den_antworttext_nicht(tmp_path, capsys):
    with attrappe(anlegen_geht=False) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert "kaputt" not in out and "HTTP 500" in out


def test_schreibproben_sind_als_nicht_gemessen_mit_grund_ausgewiesen(tmp_path, capsys):
    with attrappe() as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    r = zeile(zeilen, "Schreiben")
    assert r.urteil == lt.NICHT_GEMESSEN and r.hinweis
    assert not [m for m, p in z.anfragen if m != "GET" and p.startswith(("/rest/", "/storage/"))]


def test_passwort_200_aber_wirkungslos_ist_nicht_gemessen(tmp_path, capsys):
    """Antwortet PUT mit 200, das alte Passwort gilt aber weiter, ist nichts gesperrt worden: kein OFFEN ohne Beleg."""
    with attrappe(passwort_wirkungslos=True) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    r = zeile(zeilen, "PUT /auth/v1/user")
    assert r.urteil == lt.NICHT_GEMESSEN and "alte Passwort gilt weiter" in r.messung
    assert code == 0


def test_realtime_handshake_abgewiesen_ist_erwartet(tmp_path, capsys):
    with attrappe(realtime="abgewiesen") as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 0
    for k in ("Handshake", "privaten Kanal", "Änderungen"):
        assert urteil(zeilen, k) == lt.ERWARTET
    assert "HTTP 401" in zeile(zeilen, "Handshake").messung


def test_ratenbegrenzung_ist_keine_ablehnung_durch_berechtigung(tmp_path, capsys):
    with attrappe(signup_429=True) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    r = zeile(zeilen, "POST /auth/v1/signup")
    assert r.urteil == lt.NICHT_GEMESSEN and "HTTP 429" in r.messung
    assert code == 0


def test_faktor_loeschen_abgelehnt_ist_erwartet(tmp_path, capsys):
    with attrappe(faktor_loeschen_geht=False) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 0
    assert urteil(zeilen, "bestätigter Faktor") == lt.ERWARTET
    assert urteil(zeilen, "neuer Faktor") == lt.ERWARTET


# ---------------------------------------------------------------------------------------------------------------------------------
# Voraussetzungen, Fehler und Aufräumen
# ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("opt, satz", [
    ({"user_lesen": "abgelehnt"}, "Token wird nicht akzeptiert"),
    ({"user_lesen": "fremd"}, "anderes Konto"),
], ids=["token_abgelehnt", "fremdes_konto"])
def test_token_ohne_beweis_bricht_ab_statt_zu_taeuschen(opt, satz, tmp_path, capsys):
    """Gilt das Token nicht, wären alle Ablehnungen wertlos. Dann gibt es keine Tabelle, sondern Exit 1."""
    with attrappe(**opt) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 1 and zeilen == []
    assert satz in out
    assert len(z.geloescht) == 1
    assert [p for _, p in z.anfragen if p.startswith(("/rest", "/storage", "/realtime"))] == []


def test_aal2_fehlt_nach_dem_code_bricht_ab_und_raeumt_auf(tmp_path, capsys):
    with attrappe(aal2_claim=False) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 1 and zeilen == []
    assert "aal2" in out
    assert len(z.geloescht) == 1


def test_code_wird_abgelehnt_bricht_ab_und_raeumt_auf(tmp_path, capsys):
    with attrappe(verify_geht=False) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 1 and zeilen == []
    assert "FEHLER" in out
    assert len(z.geloescht) == 1


def test_konto_anlegen_scheitert_ohne_aufraeumen_und_ohne_dienstschluessel_in_der_ausgabe(tmp_path, capsys):
    with attrappe(anlegen_geht=False) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 1 and zeilen == []
    assert "Wegwerf-Konto" in out
    assert z.geloescht == []


def test_ausnahme_in_einer_pruefung_wird_nicht_gemessen_und_das_konto_trotzdem_geloescht(tmp_path, capsys, monkeypatch):
    def boese(ml):
        raise RuntimeError("Token " + ml.token + " Dienstschlüssel " + ml.dienst)
    monkeypatch.setattr(lt, "pr_storage_buckets", boese)
    with attrappe() as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    r = zeile(zeilen, "GET /storage/v1/bucket")
    assert r.urteil == lt.NICHT_GEMESSEN and "RuntimeError" in r.messung
    assert code == 0
    assert len(z.geloescht) == 1
    assert urteil(zeilen, "bestätigter Faktor") == lt.DOKUMENTIERT  # die übrigen Prüfungen liefen weiter


def test_loeschen_scheitert_dann_exit_1_und_die_kennung_zum_handloeschen(tmp_path, capsys):
    with attrappe(loeschen_geht=False) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys, aufraeumen=False)
    assert code == 1
    assert "nicht gelöscht" in out
    (konto,) = z.nutzer.values()
    assert konto["email"] in out
    assert lt.OFFEN not in [r.urteil for r in zeilen]


def test_unerwarteter_fehler_ausserhalb_der_pruefungen_raeumt_trotzdem_auf(tmp_path, capsys, monkeypatch):
    def kaputt():
        raise ValueError("Dienstschluessel-im-Fehlertext")
    monkeypatch.setattr(lt, "plan", kaputt)
    with attrappe() as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    assert code == 1 and zeilen == []
    assert "unerwarteter Fehler (ValueError)" in out and "Dienstschluessel-im-Fehlertext" not in out
    assert len(z.geloescht) == 1


def test_fremdes_konto_nicht_loeschbar_wird_mit_kennung_gemeldet(tmp_path, capsys):
    with attrappe(admin_offen=True, loeschen_geht=False) as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys, aufraeumen=False)
    assert code == 1
    assert "von der Prüfung angelegtes Konto nicht gelöscht" in out
    angelegt_von_pruefung = [uid for uid, n in z.nutzer.items() if n["email"].startswith("labor-fremd-")]
    assert angelegt_von_pruefung and all(uid in out for uid in angelegt_von_pruefung)


def test_ablauf_der_ausgabe_vorbereitung_tabelle_hinweise_aufraeumen_ergebnis(tmp_path, capsys):
    with attrappe() as (z, url):
        code, zeilen, out = laufen(z, url, tmp_path, capsys)
    stellen = [out.index(s) for s in ("Wegwerf-Konto angelegt", "| Endpunkt", "Hinweise", "Aufräumen", "Wegwerf-Konto gelöscht", "Ergebnis:")]
    assert stellen == sorted(stellen)


def test_ca_datei_fehlt_gibt_exit_1_ohne_aufruf(tmp_path, capsys):
    with attrappe() as (z, url):
        env = tmp_path / "wamo.env"
        env.write_text(f"ANON_KEY={z.anon}\nSERVICE_ROLE_KEY={z.service}\n", encoding="utf-8")
        code, zeilen = lt.lauf(["--basis", url, "--env-datei", str(env), "--ca", str(tmp_path / "gibt-es-nicht.crt")])
        assert z.anfragen == []
    assert code == 1 and zeilen == []
    assert "CA-Datei" in capsys.readouterr().out


def test_keine_verbindung_gibt_exit_1_ohne_traceback(tmp_path, capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = tmp_path / "wamo.env"
    env.write_text("ANON_KEY=a-anon-1234\nSERVICE_ROLE_KEY=a-service-1234\n", encoding="utf-8")
    code, zeilen = lt.lauf(["--basis", f"http://127.0.0.1:{port}", "--env-datei", str(env)])
    gesehen = capsys.readouterr()
    assert code == 1 and zeilen == []
    assert "Wegwerf-Konto" in gesehen.out
    assert "Traceback" not in gesehen.out + gesehen.err
    assert "a-service-1234" not in gesehen.out + gesehen.err


def test_umgebungsdatei_fehlt_oder_ist_unvollstaendig(tmp_path, capsys):
    code, zeilen = lt.lauf(["--basis", "http://127.0.0.1:9", "--env-datei", str(tmp_path / "gibt-es-nicht.env")])
    assert code == 1 and zeilen == []
    assert "Umgebungsdatei" in capsys.readouterr().out
    unvollstaendig = tmp_path / "u.env"
    unvollstaendig.write_text("ANON_KEY=nur-der-eine-5678\n", encoding="utf-8")
    code, zeilen = lt.lauf(["--basis", "http://127.0.0.1:9", "--env-datei", str(unvollstaendig)])
    gesehen = capsys.readouterr().out
    assert code == 1
    assert "SERVICE_ROLE_KEY" in gesehen and "nur-der-eine-5678" not in gesehen


# ---------------------------------------------------------------------------------------------------------------------------------
# Trockenlauf
# ---------------------------------------------------------------------------------------------------------------------------------

def test_trocken_zeigt_nur_den_plan_und_ruft_nichts_auf(tmp_path, capsys):
    with attrappe() as (z, url):
        code, zeilen = lt.lauf(["--trocken", "--basis", url, "--env-datei", str(tmp_path / "gibt-es-nicht.env")])
        out = capsys.readouterr().out
        assert z.anfragen == [] and z.nutzer == {}
    assert code == 0
    assert "Trockenlauf" in out and "geplant" in out
    for schluessel in ERWARTUNG_DICHT:
        zeile(zeilen, schluessel)  # jede Prüfung steht genau einmal im Plan
    assert "GET /rest/v1/" in out and "POST /auth/v1/signup" in out
    assert len(zeilen) == len(ERWARTUNG_DICHT)
    assert {r.urteil for r in zeilen} == {"-"} and {r.messung for r in zeilen} == {"geplant"}  # kein Urteil ohne Messung


def test_main_gibt_den_exitcode_zurueck(tmp_path, capsys):
    assert lt.main(["--trocken"]) == 0


# ---------------------------------------------------------------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------------------------------------------------------------

def test_totp_nach_rfc6238():
    geheimnis = base64.b32encode(b"12345678901234567890").decode()
    for zeit, code in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037")):
        assert lt.totp_code(geheimnis, zeit) == code
    assert lt.totp_code(geheimnis[:16].lower() + " " + geheimnis[16:], 59) == "287082"  # Kleinschreibung und Leerzeichen stören nicht


def test_claims_liest_die_nutzlast():
    token = ".".join([_b64({"alg": "x"}), _b64({"aal": "aal2", "sub": "ä"}), "sig"])
    assert lt.claims(token) == {"aal": "aal2", "sub": "ä"}


def test_ws_rahmen_wie_in_rfc6455():
    assert lt.ws_rahmen("Hello", maske=bytes.fromhex("37fa213d")) == bytes.fromhex("818537fa213d7f9f4d5158")


def test_ws_rahmen_mit_langer_nutzlast_und_zufallsmaske():
    text = "x" * 300
    rahmen = lt.ws_rahmen(text)
    assert rahmen[0] == 0x81 and rahmen[1] == 0x80 | 126
    laenge = int.from_bytes(rahmen[2:4], "big")
    maske, daten = rahmen[4:8], rahmen[8:]
    assert laenge == 300 == len(daten)
    assert bytes(b ^ maske[i % 4] for i, b in enumerate(daten)) == text.encode()
    assert lt.ws_rahmen(text) != rahmen  # die Maske ist zufällig


def test_bereinigen_ersetzt_bekannte_geheimnisse_und_jwt_formen():
    lt.GEHEIMNISSE.clear()
    lt.merke("SchluesselWert-998877")
    lt.merke("ab")  # zu kurz, würde Text zerstören
    jwt = ".".join([_b64({"alg": "ES256"}), _b64({"sub": "1"}), "abcDEF_-123"])
    text = f"a SchluesselWert-998877 b {jwt} c ab"
    sauber = lt.bereinigen(text)
    assert "SchluesselWert-998877" not in sauber and jwt not in sauber
    assert sauber.endswith("c ab") and sauber.count("***") == 2
    lt.GEHEIMNISSE.clear()


def test_kennung_nur_kurze_bezeichner():
    assert lt.kennung(lt.Antwort(403, json.dumps({"error_code": "not_admin", "msg": "mit Leerzeichen und Geheimnis"}).encode())) == "not_admin"
    assert lt.kennung(lt.Antwort(406, json.dumps({"code": "PGRST106"}).encode())) == "PGRST106"
    assert lt.kennung(lt.Antwort(400, json.dumps({"error_code": "ein Satz mit Leerzeichen"}).encode())) == ""
    assert lt.kennung(lt.Antwort(400, json.dumps({"code": 400}).encode())) == ""
    assert lt.kennung(lt.Antwort(500, b"<html>kein json</html>")) == ""
    assert lt.kennung(lt.Antwort(400, json.dumps({"error_code": "x" * 80}).encode())) == ""


@pytest.mark.parametrize("status, kennung_, streng, erwartet", [
    (200, "", False, lt.OFFEN), (204, "", True, lt.OFFEN), (201, "", False, lt.OFFEN),
    (401, "", True, lt.ERWARTET), (403, "not_admin", True, lt.ERWARTET), (401, "no_authorization", True, lt.NICHT_GEMESSEN),
    (422, "signup_disabled", False, lt.ERWARTET), (400, "reauthentication_needed", False, lt.ERWARTET), (404, "", False, lt.ERWARTET),
    (404, "", True, lt.NICHT_GEMESSEN), (400, "", True, lt.NICHT_GEMESSEN),
    (400, "bad_jwt", False, lt.NICHT_GEMESSEN), (403, "bad_jwt", True, lt.NICHT_GEMESSEN),
    (400, "weak_password", False, lt.NICHT_GEMESSEN), (422, "same_password", False, lt.NICHT_GEMESSEN),
    (500, "", False, lt.NICHT_GEMESSEN), (502, "", True, lt.NICHT_GEMESSEN), (0, "", False, lt.NICHT_GEMESSEN), (302, "", False, lt.NICHT_GEMESSEN),
    (429, "over_request_rate_limit", False, lt.NICHT_GEMESSEN), (429, "", True, lt.NICHT_GEMESSEN),
])
def test_bewerte_ablehnung(status, kennung_, streng, erwartet):
    assert lt.bewerte_ablehnung(status, kennung_, streng=streng) == erwartet


def test_tabelle_bricht_lange_zellen_um_und_behaelt_alle_spalten():
    text = lt.tabelle(("Endpunkt", "Erwartung", "Messung", "Urteil"),
                      [("GET /rest/v1/<tabelle> mit Schema-Profil wamo_dev und einem sehr langen Zusatz", "abgewiesen oder leer", "HTTP 406 x7", lt.ERWARTET)],
                      (20, 18, 14, 12))
    zeilen = text.splitlines()
    assert len(zeilen) >= 4
    assert max(len(z) for z in zeilen) <= 20 + 18 + 14 + 12 + 3 * 3 + 4
    assert "Schema-Profil" in text and "HTTP 406 x7" in text and lt.ERWARTET in text


def test_quelltext_ohne_gedankenstriche():
    lang, kurz = chr(0x2014), chr(0x2013)
    for datei in (QUELLE, Path(__file__)):
        text = datei.read_text(encoding="utf-8")
        assert lang not in text and kurz not in text, datei.name
