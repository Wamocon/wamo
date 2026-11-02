#!/usr/bin/python3 -E -s
"""Test des Freigabe-Dienstes mit einem ECHTEN Token von GoTrue (Schritt 1.4c der Selbstüberarbeitung). Nur Standardbibliothek.

Aufruf auf Sokrates-2 als Benutzer wamocon (ohne sudo; das ist auch der Benutzer, als der Caddy den Socket erreicht):

    python3 -E -s ~/wamo-infra/freigabe_tokentest.py                    # direkt am Socket des Dienstes
    python3 -E -s ~/wamo-infra/freigabe_tokentest.py --ueber-caddy      # zusätzlich durch die Route auf https://192.168.178.136:55444
    python3 -E -s ~/wamo-infra/freigabe_tokentest.py --schnell          # ohne das Warten auf "aal2_alt" (65 Sekunden)

Was geschieht: Über die Admin-Schnittstelle von GoTrue (Loopback, Schlüssel bleibt auf dem Server und wird nie ausgegeben) entsteht ein Wegwerf-Konto ohne
WAMO-Profil. Es meldet sich an (aal1), richtet einen zweiten Faktor (TOTP) ein und bestätigt ihn mit einem selbst berechneten Code (aal2). Mit diesen
echten Token wird der Freigabe-Dienst angefragt. Weil das Konto kein Profil hat, endet jede gültige Anfrage mit "keine_berechtigung": Das ist der Beleg, dass
Signatur, Aussteller, Zielgruppe, Ablauf und die Frische des zweiten Faktors akzeptiert wurden, ohne dass etwas in WAMO-Tabellen geschrieben wird. Am Ende wird
das Konto gelöscht (auch nach einem Fehler).

Geprüft wird insbesondere die offene Frage aus 1.4b: Erneuert ein Token-Refresh den Zeitstempel des totp-Eintrags in amr? Der Dienst verlangt höchstens
60 Sekunden; erneuerte der Refresh den Zeitstempel, könnte ein alter zweiter Faktor mit einem frischen Refresh-Token "frisch" wirken.
Exit 0 nur, wenn alles bestanden ist.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import secrets
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid

GOTRUE = "http://localhost:55321"
ENV_DATEI = "/home/wamocon/supabase-wamo/.env"
SOCKET = "/run/wamo-freigabe/freigabe.sock"
CADDY = "https://192.168.178.136:55444"
CA = "/home/wamocon/wamo-tls/ca/wamo-ca.crt"
UI_URSPRUNG = "https://192.168.178.136:55445"
ERGEBNIS = {"ok": 0, "fehler": 0}
TOKEN_FEHLER = {"token_ungueltig", "aal2_fehlt", "aal2_alt", "aal2_zukunft"}
ANGABEN = json.dumps({"commit_id": "a" * 40, "tree_id": "b" * 40}).encode()


def pruefe(name: str, bedingung: bool, detail: str = "") -> bool:
    ERGEBNIS["ok" if bedingung else "fehler"] += 1
    print(f"  {'OK    ' if bedingung else 'FEHLER'} {name}" + (f": {detail}" if detail and not bedingung else ""))
    return bedingung


def env_lesen() -> dict[str, str]:
    w: dict[str, str] = {}
    with open(ENV_DATEI, encoding="utf-8") as f:
        for z in f:
            if "=" in z and not z.lstrip().startswith("#"):
                k, v = z.rstrip("\n").split("=", 1)
                w[k.strip()] = v.strip().strip('"').strip("'")
    return w


def gotrue(methode: str, pfad: str, koerper: dict | None, apikey: str, bearer: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(GOTRUE + pfad, data=None if koerper is None else json.dumps(koerper).encode(), method=methode,
                                 headers={"apikey": apikey, "Authorization": "Bearer " + (bearer or apikey), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            roh = r.read()
            return r.status, (json.loads(roh) if roh else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def totp_code(geheimnis: str, jetzt: float | None = None) -> str:
    """RFC 6238: SHA-1, 30 Sekunden, 6 Stellen."""
    sauber = geheimnis.replace(" ", "").upper()
    schluessel = base64.b32decode(sauber + "=" * (-len(sauber) % 8))
    zaehler = int((time.time() if jetzt is None else jetzt) // 30)
    h = hmac.new(schluessel, struct.pack(">Q", zaehler), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def claims(token: str) -> dict:
    teil = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(teil + "=" * (-len(teil) % 4)))


class UnixVerbindung(http.client.HTTPConnection):
    def __init__(self, pfad: str):
        super().__init__("localhost", timeout=20)
        self._pfad = pfad

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(20)
        self.sock.connect(self._pfad)


def freigeben_socket(token: str, anfrage: str, pfad: str | None = None, methode: str = "POST", koerper: bytes | None = None) -> tuple[int, dict]:
    c = UnixVerbindung(SOCKET)
    try:
        c.request(methode, pfad or f"/wamo/release/{anfrage}/freigeben", body=ANGABEN if koerper is None else koerper,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        r = c.getresponse()
        roh = r.read()
        return r.status, (json.loads(roh) if roh else {})
    finally:
        c.close()


def caddy_anfrage(methode: str, pfad: str, token: str | None = None, koerper: bytes | None = None, ursprung: str | None = None) -> tuple[int, dict, dict]:
    kopf = {"Content-Type": "application/json"}
    if token:
        kopf["Authorization"] = f"Bearer {token}"
    if ursprung:
        kopf["Origin"] = ursprung
    req = urllib.request.Request(CADDY + pfad, data=koerper, method=methode, headers=kopf)
    ctx = ssl.create_default_context(cafile=CA)

    def lesen(roh: bytes) -> dict:
        try:
            return json.loads(roh) if roh.startswith(b"{") else {}
        except ValueError:
            return {}
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return r.status, lesen(r.read()), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, lesen(e.read()), {k.lower(): v for k, v in e.headers.items()}


class Konto:
    """Wegwerf-Konto mit zweitem Faktor. neues_aal2() liefert ein frisches aal2-Token (wartet auf das nächste 30-Sekunden-Fenster, weil GoTrue einen
    Code nicht zweimal annimmt)."""

    def __init__(self, anon: str, dienst: str):
        self.anon, self.dienst = anon, dienst
        self.email = f"token-test-{secrets.token_hex(6)}@example.org"
        self.passwort = secrets.token_urlsafe(24)
        self.uid = self.faktor = self.geheimnis = None
        self.letztes_fenster = -1

    def anlegen(self) -> bool:
        s, d = gotrue("POST", "/auth/v1/admin/users", {"email": self.email, "password": self.passwort, "email_confirm": True}, self.dienst)
        if s in (200, 201) and "id" in d:
            self.uid = d["id"]
        return self.uid is not None

    def anmelden(self) -> tuple[int, dict]:
        return gotrue("POST", "/auth/v1/token?grant_type=password", {"email": self.email, "password": self.passwort}, self.anon)

    def faktor_einrichten(self, aal1: str) -> bool:
        s, d = gotrue("POST", "/auth/v1/factors", {"factor_type": "totp", "friendly_name": "token-test", "issuer": "WAMO"}, self.anon, aal1)
        if s in (200, 201) and "totp" in d:
            self.faktor, self.geheimnis = d["id"], d["totp"]["secret"]
        return self.faktor is not None

    def neues_aal2(self, bearer: str) -> tuple[int, dict]:
        fenster = int(time.time() // 30)
        if fenster <= self.letztes_fenster:
            time.sleep(30 - (time.time() % 30) + 1)
        s, d = gotrue("POST", f"/auth/v1/factors/{self.faktor}/challenge", {}, self.anon, bearer)
        if s not in (200, 201) or "id" not in d:
            return s, d
        self.letztes_fenster = int(time.time() // 30)
        return gotrue("POST", f"/auth/v1/factors/{self.faktor}/verify", {"challenge_id": d["id"], "code": totp_code(self.geheimnis)}, self.anon, bearer)

    def loeschen(self) -> bool:
        s, _ = gotrue("DELETE", f"/auth/v1/admin/users/{self.uid}", None, self.dienst)
        return s in (200, 204)


def main() -> int:
    ap = argparse.ArgumentParser(description="Freigabe-Dienst mit echtem GoTrue-Token testen")
    ap.add_argument("--ueber-caddy", action="store_true", help="zusätzlich durch die Route auf 55444")
    ap.add_argument("--schnell", action="store_true", help="ohne das Warten auf aal2_alt")
    a = ap.parse_args()
    env = env_lesen()
    konto = Konto(env["ANON_KEY"], env["SERVICE_ROLE_KEY"])
    anfrage = str(uuid.uuid4())
    print("Freigabe-Dienst mit echtem Token (Wegwerf-Konto ohne WAMO-Profil)")
    try:
        if not pruefe("Wegwerf-Konto angelegt", konto.anlegen()):
            return 1
        s, d = konto.anmelden()
        if not pruefe("Anmeldung mit Passwort liefert ein Token (aal1)", s == 200 and "access_token" in d, f"HTTP {s}"):
            return 1
        aal1 = d["access_token"]
        c1 = claims(aal1)
        pruefe("das Token ist aal1 ohne totp", c1.get("aal") == "aal1" and not any(e.get("method") == "totp" for e in c1.get("amr", [])), str(c1.get("amr")))
        if not pruefe("zweiter Faktor (TOTP) eingerichtet", konto.faktor_einrichten(aal1)):
            return 1
        s, d = konto.neues_aal2(aal1)
        if not pruefe("Code bestätigt: Token mit aal2", s == 200 and "access_token" in d, f"HTTP {s} {d.get('msg', '')}"):
            return 1
        aal2, refresh = d["access_token"], d.get("refresh_token", "")
        t0 = time.time()
        c2 = claims(aal2)
        totp = [e for e in c2.get("amr", []) if e.get("method") == "totp"]
        if not pruefe("das Token trägt aal2 und genau einen totp-Eintrag mit Zeitstempel", c2.get("aal") == "aal2" and len(totp) == 1
                      and isinstance(totp[0].get("timestamp"), int), str(c2.get("amr"))):
            return 1
        print(f"  Hinweis: amr = {[(e.get('method'), e.get('timestamp')) for e in c2.get('amr', [])]}, Alter des Codes {int(t0 - totp[0]['timestamp'])} s")

        print("Am Socket des Dienstes (als wamocon, uid 1000)")
        s, d = freigeben_socket(aal2, anfrage)
        pruefe("echtes aal2-Token wird akzeptiert (erst die Berechtigung lehnt ab)", (s, d.get("grund")) == (403, "keine_berechtigung"), str((s, d.get("grund"))))
        s, d = freigeben_socket(aal1, anfrage)
        pruefe("echtes aal1-Token wird abgewiesen: aal2_fehlt", (s, d.get("grund")) == (403, "aal2_fehlt"), str((s, d.get("grund"))))
        s, d = freigeben_socket("x" * 20 + "." + "y" * 20 + "." + "z" * 20, anfrage)
        pruefe("ein erfundenes Token wird abgewiesen: token_ungueltig", (s, d.get("grund")) == (401, "token_ungueltig"), str((s, d.get("grund"))))
        manipuliert = aal2.rsplit(".", 1)[0] + "." + ("A" if aal2[-1] != "A" else "B") * 86
        s, d = freigeben_socket(manipuliert, anfrage)
        pruefe("ein echtes Token mit verfälschter Signatur wird abgewiesen", (s, d.get("grund")) == (401, "token_ungueltig"), str((s, d.get("grund"))))
        s, d = freigeben_socket(aal2, anfrage, koerper=b"x" * 3000)
        pruefe("zu große Angaben werden abgewiesen", s in (400, 413), str((s, d.get("grund"))))
        for fremd in ("/wamo/release/x/freigeben", "/", "/wamo/release/" + anfrage):
            s, d = freigeben_socket(aal2, anfrage, pfad=fremd)
            pruefe(f"anderer Pfad {fremd[:30]}: 404", s == 404, str(s))
        s, d = freigeben_socket(aal2, anfrage, methode="GET", koerper=b"")
        pruefe("GET wird abgewiesen: 405", s == 405, str(s))

        print("Token-Refresh (offene Frage aus 1.4b)")
        s, d = gotrue("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": refresh}, konto.anon)
        erneuert_token = None
        if pruefe("Refresh liefert ein neues Token", s == 200 and "access_token" in d, f"HTTP {s}"):
            erneuert_token = d["access_token"]
            cn = claims(erneuert_token)
            tn = [e for e in cn.get("amr", []) if e.get("method") == "totp"]
            erneuert = bool(tn) and tn[0].get("timestamp", 0) > totp[0]["timestamp"]
            print(f"  Befund: aal nach dem Refresh = {cn.get('aal')}, totp-Zeitstempel {'ERNEUERT' if erneuert else 'unverändert'}")
            pruefe("der Refresh erneuert den Zeitstempel des zweiten Faktors NICHT (sonst wäre 'frisch' nach 60 Sekunden aushebelbar)", not erneuert,
                   f"alt {totp[0]['timestamp']}, neu {tn[0]['timestamp'] if tn else None}")
        if not a.schnell:
            rest = 65 - (time.time() - t0)
            if rest > 0:
                print(f"  warte {int(rest) + 1} s, bis der zweite Faktor älter als 60 Sekunden ist ...")
                time.sleep(rest + 1)
            s, d = freigeben_socket(aal2, anfrage)
            pruefe("nach über 60 Sekunden: aal2_alt (auch wenn das Token selbst noch gültig ist)", (s, d.get("grund")) == (403, "aal2_alt"), str((s, d.get("grund"))))
            if erneuert_token:
                s, d = freigeben_socket(erneuert_token, anfrage)
                pruefe("auch das per Refresh erneuerte Token ist dann aal2_alt", (s, d.get("grund")) == (403, "aal2_alt"), str((s, d.get("grund"))))
        s, d = konto.neues_aal2(aal1)
        if pruefe("mit einem neuen Code entsteht ein neues aal2-Token", s == 200 and "access_token" in d, f"HTTP {s}"):
            frisch = d["access_token"]
            s, d = freigeben_socket(frisch, anfrage)
            pruefe("das neue Token ist wieder frisch (erst die Berechtigung lehnt ab)", (s, d.get("grund")) == (403, "keine_berechtigung"), str((s, d.get("grund"))))

            if a.ueber_caddy:
                print("Durch die Route von Caddy (55444)")
                pfad = f"/wamo/release/{anfrage}/freigeben"
                s, j, k = caddy_anfrage("POST", pfad, frisch, ANGABEN, UI_URSPRUNG)
                pruefe("POST durch Caddy erreicht den Freigabe-Dienst (JSON des Dienstes, nicht des Kerns)", s == 403 and j.get("grund") == "keine_berechtigung", str((s, j)))
                pruefe("die Antwort trägt das CORS-Recht für genau die Oberfläche", k.get("access-control-allow-origin") == UI_URSPRUNG
                       and "origin" in k.get("vary", "").lower(), str(k)[:200])
                s, j, k = caddy_anfrage("POST", pfad, aal1, b"{}", "https://evil.example")
                pruefe("ein fremder Ursprung bekommt kein CORS-Recht", "access-control-allow-origin" not in k, str(k)[:200])
                s, j, k = caddy_anfrage("OPTIONS", pfad, None, None, UI_URSPRUNG)
                pruefe("Preflight (OPTIONS) wird von Caddy beantwortet: 204 mit erlaubten Methoden und Köpfen", s == 204
                       and k.get("access-control-allow-methods") == "POST, OPTIONS" and "authorization" in k.get("access-control-allow-headers", "").lower(),
                       str((s, k))[:200])
                s, j, k = caddy_anfrage("POST", pfad, frisch, b"x" * 6000, UI_URSPRUNG)
                pruefe("über 4 KB werden von Caddy abgewiesen (413)", s == 413, str(s))
                s, j, k = caddy_anfrage("POST", "/wamo/release/abcd/freigeben", frisch, b"{}")
                pruefe("ein Hex-Pfad ohne UUID-Form erreicht den Dienst und bekommt dort 404", s == 404 and j.get("grund") == "unbekannt", str((s, j)))
                s, j, k = caddy_anfrage("POST", "/wamo/release/x/freigeben", frisch, b"{}")
                pruefe("ein Pfad mit anderen Zeichen geht nicht an den Dienst, sondern wie bisher an den Kern", j.get("grund") not in TOKEN_FEHLER | {"keine_berechtigung", "unbekannt"}, str((s, j)))
                s, j, k = caddy_anfrage("GET", pfad, frisch)
                pruefe("GET auf die Route geht an den Kern, nicht an den Freigabe-Dienst", j.get("grund") not in TOKEN_FEHLER | {"keine_berechtigung", "unbekannt"}, str((s, j)))
                s, j, k = caddy_anfrage("POST", "/wamo/release/anderes", frisch, b"{}")
                pruefe("andere Pfade unter /wamo/release gehen weiter an den Kern", j.get("grund") not in TOKEN_FEHLER | {"keine_berechtigung"}, str((s, j)))
                s, j, k = caddy_anfrage("GET", "/start")
                pruefe("der Kern ist wie bisher erreichbar: /start ohne Anmeldung gibt 401", s == 401, str(s))
    finally:
        if konto.uid:
            pruefe("Wegwerf-Konto gelöscht", konto.loeschen())
    print(f"Ergebnis: {ERGEBNIS['ok']} bestanden, {ERGEBNIS['fehler']} fehlgeschlagen")
    return 0 if ERGEBNIS["fehler"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
