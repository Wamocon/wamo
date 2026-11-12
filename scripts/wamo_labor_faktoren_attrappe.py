"""Attrappe von GoTrue für scripts/test_wamo_labor_faktoren.py. Nur für Tests, nie im Produktionspfad.

Ein kleiner HTTP-Server auf Loopback mit den Aufrufen, die scripts/wamo_labor_faktoren.py braucht: Anmeldung mit Passwort,
Token erneuern, Nutzer lesen, Faktor anlegen, challenge, verify, Faktor löschen und Abmeldung. Verhalten wie GoTrue, soweit der
Test es braucht: ein bestätigter Faktor lässt sich nur mit aal2 löschen, ein Code gilt im Fenster der Uhr der Attrappe und je
einem Fenster davor und danach, Namen von Faktoren sind je Nutzer eindeutig. Die Uhr der Attrappe geht um `versatz` Sekunden vor
und bestimmt auch den Kopf Date der Antworten. Die TOTP-Prüfung ist hier unabhängig von scripts/wamo_labor_faktoren_totp.py
geschrieben, damit die Tests nicht sich selbst prüfen.

Fehler lassen sich gezielt einspielen (fehler(...)): je Methode, Pfadteil und auf Wunsch je E-Mail, einmal oder dauerhaft.
Seeds und Tokens entstehen zur Laufzeit aus Hashwerten, nie als Zeichenfolge im Quelltext.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def totp_unabhaengig(seed: str, zeit: float) -> str:
    schluessel = base64.b32decode(seed + "=" * (-len(seed) % 8))
    mac = hmac.new(schluessel, int(zeit // 30).to_bytes(8, "big"), "sha1").digest()
    ab = mac[19] & 15
    return str((int.from_bytes(mac[ab:ab + 4], "big") & 0x7FFFFFFF) % 1_000_000).zfill(6)


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


class GoTrueAttrappe:
    def __init__(self, schluessel: str = "anon-attrappe", versatz: float = 0.0, mit_datum: bool = True):
        self.schluessel = schluessel
        self.versatz = versatz
        self.mit_datum = mit_datum
        self.umleiten = False
        self.nutzer: dict[str, dict] = {}      # E-Mail -> Nutzer
        self.sitzungen: dict[str, dict] = {}   # Sitzungskennung -> Sitzung
        self.tokens: dict[str, str] = {}       # Access- oder Refresh-Token -> Sitzungskennung
        self.faktoren: dict[str, dict] = {}    # Faktorkennung -> Faktor
        self.challenges: dict[str, str] = {}   # Challenge -> Faktorkennung
        self.anfragen: list[tuple[str, str]] = []
        self.regeln: list[dict] = []
        self._zaehler = 0
        self._sperre = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self.basis = ""

    # ---- Einrichtung der Attrappe (aus dem Test) ----

    def jetzt(self) -> float:
        return time.time() + self.versatz

    def _neue_id(self, art: str) -> str:
        with self._sperre:
            self._zaehler += 1
            return f"{art}-" + hashlib.sha1(f"{art}{self._zaehler}".encode()).hexdigest()[:12]

    def nutzer_anlegen(self, email: str, passwort: str) -> None:
        self.nutzer[email] = {"id": self._neue_id("nutzer"), "email": email, "passwort": passwort}

    def faktor_anlegen(self, email: str, status: str = "verified", name: str = "WAMO-alt") -> tuple[str, str]:
        """Legt einen Faktor für den Nutzer an (wie ein früherer Lauf oder die Oberfläche) und liefert Kennung und Seed."""
        fid = self._neue_id("faktor")
        seed = base64.b32encode(hashlib.sha1(f"{email}/{fid}".encode()).digest()).decode()
        self.faktoren[fid] = {"id": fid, "email": email, "status": status, "name": name, "seed": seed}
        return fid, seed

    def faktoren_von(self, email: str) -> list[dict]:
        return [f for f in self.faktoren.values() if f["email"] == email]

    def sitzungen_von(self, email: str) -> int:
        return sum(1 for s in self.sitzungen.values() if s["email"] == email)

    def fehler(self, methode: str, pfadteil: str, status: int, kennung: str = "", email: str | None = None, einmal: bool = False,
               koerper: dict | None = None) -> None:
        self.regeln.append({"methode": methode, "pfad": pfadteil, "status": status, "email": email, "einmal": einmal,
                            "koerper": koerper if koerper is not None else ({"code": status, "error_code": kennung, "msg": "Fehler der Attrappe"})})

    def starten(self, tls: tuple[str, str] | None = None) -> str:
        """Startet den Server auf Loopback. tls ist (Zertifikatsdatei, Schlüsseldatei) für https."""
        attrappe = self

        class Handler(_Handler):
            owner = attrappe

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        schema = "http"
        if tls is not None:
            kontext = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            kontext.load_cert_chain(*tls)
            self._server.socket = kontext.wrap_socket(self._server.socket, server_side=True)
            schema = "https"
        self.basis = f"{schema}://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return self.basis

    def beenden(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # ---- Bausteine für die Antworten ----

    def _user_json(self, email: str) -> dict:
        n = self.nutzer[email]
        d = {"id": n["id"], "email": email, "aud": "authenticated", "role": "authenticated"}
        liste = [{"id": f["id"], "friendly_name": f["name"], "factor_type": "totp", "status": f["status"]}
                 for f in self.faktoren_von(email)]
        if liste:
            d["factors"] = liste
        return d

    def _token(self, email: str, sid: str, aal: str) -> str:
        self._zaehler += 1
        nutzlast = {"sub": self.nutzer[email]["id"], "email": email, "aal": aal, "session_id": sid, "exp": int(self.jetzt()) + 3600,
                    "jti": self._zaehler}
        kopf = _b64({"alg": "ES256", "typ": "JWT"})
        t = f"{kopf}.{_b64(nutzlast)}."
        t += hashlib.sha256(t.encode()).hexdigest()[:16]
        self.tokens[t] = sid
        return t

    def _sitzung_ausgeben(self, sid: str) -> dict:
        s = self.sitzungen[sid]
        self._zaehler += 1
        refresh = hashlib.sha1(f"refresh{sid}{self._zaehler}".encode()).hexdigest()[:12]
        self.tokens[refresh] = sid
        return {"access_token": self._token(s["email"], sid, s["aal"]), "token_type": "bearer", "expires_in": 3600,
                "refresh_token": refresh, "user": self._user_json(s["email"])}


class _Handler(BaseHTTPRequestHandler):
    owner: GoTrueAttrappe
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # still
        pass

    def send_response(self, code, message=None):
        self.send_response_only(code, message)
        self.send_header("Server", "attrappe")
        if self.owner.mit_datum:
            self.send_header("Date", formatdate(self.owner.jetzt(), usegmt=True))

    # ---- Hilfen ----

    def _koerper(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        roh = self.rfile.read(n) if n else b""
        try:
            d = json.loads(roh) if roh else {}
        except ValueError:
            return {}
        return d if isinstance(d, dict) else {}

    def _senden(self, status: int, daten: object = None, kopf: dict | None = None) -> None:
        roh = b"" if daten is None else json.dumps(daten).encode()
        self.send_response(status)
        for k, v in (kopf or {}).items():
            self.send_header(k, v)
        if roh:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        if roh:
            self.wfile.write(roh)

    def _sitzung(self) -> tuple[str, dict] | None:
        bearer = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        sid = self.owner.tokens.get(bearer)
        if sid is None or sid not in self.owner.sitzungen or bearer == self.owner.schluessel:
            return None
        return sid, self.owner.sitzungen[sid]

    def _regel(self, methode: str, pfad: str, email: str | None) -> dict | None:
        for r in self.owner.regeln:
            if r["methode"] == methode and r["pfad"] in pfad and (r["email"] is None or r["email"] == email):
                if r["einmal"]:
                    self.owner.regeln.remove(r)
                return r
        return None

    def _bearbeiten(self, methode: str) -> None:
        teile = urlsplit(self.path)
        pfad, abfrage = teile.path, parse_qs(teile.query)
        koerper = self._koerper() if methode == "POST" else {}
        with self.owner._sperre:
            self.owner.anfragen.append((methode, pfad))
            if self.headers.get("apikey") != self.owner.schluessel:
                return self._senden(401, {"message": "No API key found in request"})
            if self.owner.umleiten and pfad.endswith("/token"):
                return self._senden(302, None, {"Location": f"{self.owner.basis}/ziel"})
            sitzung = self._sitzung()
            email = koerper.get("email") or (sitzung[1]["email"] if sitzung else None)
            regel = self._regel(methode, pfad, email)
            if regel:
                return self._senden(regel["status"], regel["koerper"])
            self._verteilen(methode, pfad, abfrage, koerper, sitzung)

    def _verteilen(self, methode, pfad, abfrage, koerper, sitzung) -> None:
        o = self.owner
        if methode == "POST" and pfad == "/auth/v1/token":
            art = (abfrage.get("grant_type") or [""])[0]
            return self._anmelden(koerper) if art == "password" else self._erneuern(koerper)
        if sitzung is None:
            return self._senden(401, {"code": 401, "error_code": "bad_jwt", "msg": "Token ungültig"})
        sid, s = sitzung
        if methode == "GET" and pfad == "/auth/v1/user":
            return self._senden(200, o._user_json(s["email"]))
        if methode == "POST" and pfad == "/auth/v1/logout":
            o.sitzungen.pop(sid, None)
            return self._senden(204)
        if methode == "POST" and pfad == "/auth/v1/factors":
            return self._faktor_anlegen(s, koerper)
        teile = pfad.split("/")  # ['', 'auth', 'v1', 'factors', '<id>', 'challenge']
        if len(teile) >= 5 and teile[3] == "factors":
            f = o.faktoren.get(teile[4])
            if f is None or f["email"] != s["email"]:
                return self._senden(404, {"code": 404, "error_code": "mfa_factor_not_found", "msg": "unbekannt"})
            if methode == "POST" and len(teile) == 6 and teile[5] == "challenge":
                return self._challenge(f)
            if methode == "POST" and len(teile) == 6 and teile[5] == "verify":
                return self._verify(sid, f, koerper)
            if methode == "DELETE" and len(teile) == 5:
                return self._loeschen(s, f)
        self._senden(404, {"code": 404, "error_code": "not_found", "msg": "unbekannte Route"})

    # ---- Aufrufe ----

    def _anmelden(self, k: dict) -> None:
        o = self.owner
        n = o.nutzer.get(k.get("email", ""))
        if n is None or n["passwort"] != k.get("password"):
            return self._senden(400, {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"})
        sid = o._neue_id("sitzung")
        o.sitzungen[sid] = {"email": n["email"], "aal": "aal1"}
        self._senden(200, o._sitzung_ausgeben(sid))

    def _erneuern(self, k: dict) -> None:
        o = self.owner
        sid = o.tokens.get(k.get("refresh_token", ""))
        if sid is None or sid not in o.sitzungen:
            return self._senden(400, {"code": 400, "error_code": "refresh_token_not_found", "msg": "unbekannt"})
        self._senden(200, o._sitzung_ausgeben(sid))

    def _faktor_anlegen(self, s: dict, k: dict) -> None:
        o = self.owner
        name = k.get("friendly_name", "")
        if any(f["name"] == name for f in o.faktoren_von(s["email"])):
            return self._senden(422, {"code": 422, "error_code": "mfa_factor_name_conflict", "msg": "Name vergeben"})
        fid, seed = o.faktor_anlegen(s["email"], "unverified", name)
        uri = f"otpauth://totp/WAMO:{s['email']}?secret={seed}&issuer=WAMO"
        self._senden(200, {"id": fid, "type": "totp", "friendly_name": name, "totp": {"qr_code": "<svg/>", "secret": seed, "uri": uri}})

    def _challenge(self, f: dict) -> None:
        o = self.owner
        cid = o._neue_id("challenge")
        o.challenges[cid] = f["id"]
        self._senden(200, {"id": cid, "type": "totp", "expires_at": int(o.jetzt()) + 300})

    def _verify(self, sid: str, f: dict, k: dict) -> None:
        o = self.owner
        if o.challenges.get(k.get("challenge_id", "")) != f["id"]:
            return self._senden(400, {"code": 400, "error_code": "mfa_challenge_expired", "msg": "Challenge unbekannt"})
        jetzt = o.jetzt()
        gueltig = {totp_unabhaengig(f["seed"], jetzt + d * 30) for d in (-1, 0, 1)}
        if k.get("code") not in gueltig:
            return self._senden(400, {"code": 400, "error_code": "mfa_verification_failed", "msg": "Invalid TOTP code entered"})
        o.challenges.pop(k["challenge_id"], None)
        f["status"] = "verified"
        o.sitzungen[sid]["aal"] = "aal2"
        self._senden(200, o._sitzung_ausgeben(sid))

    def _loeschen(self, s: dict, f: dict) -> None:
        if f["status"] == "verified" and s["aal"] != "aal2":
            return self._senden(401, {"code": 401, "error_code": "insufficient_aal", "msg": "AAL2 required to unenroll verified factor"})
        self.owner.faktoren.pop(f["id"], None)
        self._senden(200, {"id": f["id"]})

    def do_GET(self):
        if urlsplit(self.path).path == "/ziel":
            with self.owner._sperre:
                self.owner.anfragen.append(("GET", "/ziel"))
            return self._senden(200, {})
        self._bearbeiten("GET")

    def do_POST(self):
        self._bearbeiten("POST")

    def do_DELETE(self):
        self._bearbeiten("DELETE")
