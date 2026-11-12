#!/usr/bin/python3 -E -s
"""Token-Test des Testlabors (Befund 11 in docs/testlabor-wamo.md, Abschnitt "Konten und Anmeldedienst"). Nur Standardbibliothek.

Aufruf auf Sokrates-2 als Benutzer wamocon (ohne sudo):

    python3 -E -s ~/wamo-infra/labor_tokentest.py             # Messung mit einem Wegwerf-Konto
    python3 -E -s ~/wamo-infra/labor_tokentest.py --trocken   # nur der Plan, es wird nichts angelegt und nichts aufgerufen

Was der Test misst: Ein Besucher am Labortag hat ein Konto mit aal2 (Passwort und TOTP) und kann sein Token aus dem Browser lesen. Der Test legt
über die Admin-Schnittstelle von GoTrue (Loopback, der Schlüssel bleibt auf dem Server und wird nie ausgegeben) ein Wegwerf-Konto ohne WAMO-Profil an,
meldet es an (aal1), richtet TOTP ein und bestätigt mit einem selbst berechneten Code (aal2). Mit diesem Token ruft er die Schnittstellen auf, wie es
ein Besucher könnte: /rest (Verzeichnis, WAMO-Tabellen mit und ohne Schema-Profil), /storage (Bucket-Liste), /realtime (Handshake, Beitritt zu einem
fremden privaten Kanal und zu WAMO-Änderungen) und die Auth-Endpunkte (eigenes Konto, Passwort ändern, Faktoren anlegen und entfernen, Admin-Pfade,
Registrierung, Einladung, Passwort vergessen). Je Prüfung steht in der Tabelle die Erwartung, die Messung und das Urteil: erwartet, dokumentiert (Aufruf
auf das EIGENE Konto, erlaubt), OFFEN (Lücke) oder nicht gemessen. Bei OFFEN nennt der Test den Satz und die Gegenmaßnahme. Exit 0 nur, wenn nichts
OFFEN ist und das Wegwerf-Konto samt allem, was der Test angelegt hat, gelöscht wurde (auch nach einem Fehler). Nie ausgegeben werden Schlüssel,
Passwörter, Token und Zeileninhalte, bei Fehlern nur der Status und eine kurze Kennung.

Was er nicht misst: Schreibzugriffe auf WAMO-Tabellen und Buckets (bei einer Lücke wären es echte Eingriffe), die Auslieferung von Realtime-Änderungen
(braucht eine echte Änderung an einer WAMO-Tabelle), Postversand, RPC-Funktionen und Edge Functions sowie die Wirkung auf vorhandene fremde Konten (es
dienen nur Wegwerf-Konten und nicht vorhandene Adressen). Gemessen wird am Eingang des Stacks (Envoy auf 55321), derselbe Weg wie über Caddy auf 55443,
der alle Pfade unverändert durchreicht. Ein zweiter Lauf von außen geht mit --basis https://192.168.178.136:55443 --ca <wamo-ca.crt>.
"""

from __future__ import annotations

import argparse
import base64
import collections
import contextlib
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import struct
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import partial
from typing import Callable

BASIS = "http://localhost:55321"
ENV_DATEI = "/home/wamocon/supabase-wamo/.env"
WAMO_SCHEMAS = ("wamo_dev", "wamo_test", "wamo_prod")
WAMO_TABELLEN = ("profiles", "memories", "model_access", "sessions", "turns", "actions", "dateien")
ERWARTET, DOKUMENTIERT, OFFEN, NICHT_GEMESSEN = "erwartet", "dokumentiert", "OFFEN", "nicht gemessen"
GEGENMASSNAHME_PASSWORT = "GOTRUE_SECURITY_UPDATE_PASSWORD_REQUIRE_REAUTHENTICATION=true"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_WARTEZEIT_S = 8.0
WS_NACHLAUF_S = 1.5
ANTWORT_GRENZE = 2_000_000
# Kennungen, die sagen: Nicht die Berechtigung hat abgelehnt, sondern das Token wurde nicht angenommen. Dann ist die Messung wertlos.
TOKEN_KENNUNGEN = frozenset({"bad_jwt", "no_authorization", "session_not_found", "session_expired", "refresh_token_not_found", "invalid_token",
                             "InvalidJWT", "PGRST300", "PGRST301"})
# Kennungen einer Ablehnung aus einem anderen Grund als dem geprüften (etwa ein zu schwaches neues Passwort)
ANDERE_GRUENDE = frozenset({"weak_password", "same_password", "validation_failed", "bad_json"})
KENNUNG_FORM = re.compile(r"[A-Za-z0-9_]{1,40}")
JWT_FORM = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
SPALTEN = ("Endpunkt", "Erwartung", "Messung", "Urteil")
BREITEN = (40, 26, 34, 14)
GEHEIMNISSE: list[str] = []


# ---------------------------------------------------------------------------------------------------------------------------------
# Ausgabe ohne Geheimnisse
# ---------------------------------------------------------------------------------------------------------------------------------

def merke(wert: object) -> None:
    """Merkt einen Wert, der nie in der Ausgabe stehen darf. Zu kurze Werte würden nur Text zerstören."""
    if isinstance(wert, str) and len(wert) >= 6 and wert not in GEHEIMNISSE:
        GEHEIMNISSE.append(wert)


def bereinigen(text: str) -> str:
    for g in sorted(GEHEIMNISSE, key=len, reverse=True):
        text = text.replace(g, "***")
    return JWT_FORM.sub("***", text)


def ausgabe(text: str = "") -> None:
    print(bereinigen(text), flush=True)


def pruefe(name: str, bedingung: bool, detail: str = "") -> bool:
    ausgabe(f"  {'OK    ' if bedingung else 'FEHLER'} {name}" + (f": {detail}" if detail and not bedingung else ""))
    return bedingung


class Abbruch(Exception):
    """Die Messung kann nicht sinnvoll weitergehen. Der Text steht in der Ausgabe und darf keine Geheimnisse enthalten."""


# ---------------------------------------------------------------------------------------------------------------------------------
# Bausteine: Antworten, TOTP, Token, WebSocket
# ---------------------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Antwort:
    status: int
    roh: bytes = b""

    def als_json(self) -> object:
        try:
            return json.loads(self.roh) if self.roh else None
        except ValueError:
            return None


def kennung(a: Antwort) -> str:
    """Kurze Fehlerkennung aus der Antwort (nur kurze Bezeichner, nie Sätze, nie Inhalte)."""
    d = a.als_json()
    if not isinstance(d, dict):
        return ""
    for schluessel in ("error_code", "code", "error"):
        w = d.get(schluessel)
        if isinstance(w, str) and KENNUNG_FORM.fullmatch(w):
            return w
    return ""


def totp_code(geheimnis: str, jetzt: float | None = None) -> str:
    """RFC 6238: SHA-1, 30 Sekunden, 6 Stellen."""
    sauber = geheimnis.replace(" ", "").upper()
    schluessel = base64.b32decode(sauber + "=" * (-len(sauber) % 8))
    zaehler = int((time.time() if jetzt is None else jetzt) // 30)
    h = hmac.new(schluessel, struct.pack(">Q", zaehler), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def claims(token: str) -> dict:
    try:
        teil = token.split(".")[1]
        d = json.loads(base64.urlsafe_b64decode(teil + "=" * (-len(teil) % 4)))
    except (IndexError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def ws_rahmen(text: str, maske: bytes | None = None) -> bytes:
    """Ein maskierter Textrahmen eines WebSocket-Clients (RFC 6455, Abschnitt 5)."""
    nutzlast = text.encode()
    maske = os.urandom(4) if maske is None else maske
    n = len(nutzlast)
    if n < 126:
        kopf = bytes([0x81, 0x80 | n])
    elif n < 65536:
        kopf = bytes([0x81, 0x80 | 126]) + n.to_bytes(2, "big")
    else:
        kopf = bytes([0x81, 0x80 | 127]) + n.to_bytes(8, "big")
    return kopf + maske + bytes(b ^ maske[i % 4] for i, b in enumerate(nutzlast))


def _lesen_genau(sock: socket.socket, n: int) -> bytes:
    puffer = b""
    while len(puffer) < n:
        stueck = sock.recv(n - len(puffer))
        if not stueck:
            raise ConnectionError("Verbindung beendet")
        puffer += stueck
    return puffer


def ws_lesen(sock: socket.socket, frist: float) -> str | None:
    """Nächster Textrahmen des Servers. None bei Schließen, Fehler oder Ablauf der Frist. Ping und Pong werden übergangen."""
    ende = time.monotonic() + frist
    try:
        while True:
            rest = ende - time.monotonic()
            if rest <= 0:
                return None
            sock.settimeout(rest)
            kopf = _lesen_genau(sock, 2)
            opcode, laenge = kopf[0] & 0x0F, kopf[1] & 0x7F
            if laenge == 126:
                laenge = int.from_bytes(_lesen_genau(sock, 2), "big")
            elif laenge == 127:
                laenge = int.from_bytes(_lesen_genau(sock, 8), "big")
            maske = _lesen_genau(sock, 4) if kopf[1] & 0x80 else None
            if laenge > ANTWORT_GRENZE:
                return None
            daten = _lesen_genau(sock, laenge)
            if maske:
                daten = bytes(b ^ maske[i % 4] for i, b in enumerate(daten))
            if opcode == 0x8:
                return None
            if opcode == 0x1:
                return daten.decode("utf-8", "replace")
    except (OSError, ValueError):
        return None


def tabelle(kopf: tuple[str, ...], zeilen: list[tuple[str, ...]], breiten: tuple[int, ...]) -> str:
    def zelle(zellen: tuple[str, ...]) -> list[str]:
        teile = [textwrap.wrap(z, b, break_long_words=True, break_on_hyphens=False) or [""] for z, b in zip(zellen, breiten)]
        return ["| " + " | ".join((t[i] if i < len(t) else "").ljust(b) for t, b in zip(teile, breiten)) + " |"
                for i in range(max(len(t) for t in teile))]
    trenner = "+-" + "-+-".join("-" * b for b in breiten) + "-+"
    aus = [trenner, *zelle(kopf), trenner]
    for z in zeilen:
        aus += [*zelle(z), trenner]
    return "\n".join(aus)


# ---------------------------------------------------------------------------------------------------------------------------------
# Urteile
# ---------------------------------------------------------------------------------------------------------------------------------

def bewerte_ablehnung(status: int, kennung_: str = "", *, streng: bool = False) -> str:
    """Urteil für einen Aufruf, den der Stack ablehnen muss. streng: nur 401 und 403 gelten als Ablehnung (Admin-Pfade)."""
    if 200 <= status < 300:
        return OFFEN
    if kennung_ in TOKEN_KENNUNGEN or kennung_ in ANDERE_GRUENDE or status == 429:
        return NICHT_GEMESSEN  # Token abgelehnt, anderer Grund oder Ratenbegrenzung: keine Aussage über die Berechtigung
    if status in (401, 403):
        return ERWARTET
    if 400 <= status < 500 and not streng:
        return ERWARTET
    return NICHT_GEMESSEN


def kurz(a: Antwort) -> str:
    if a.status == 0:
        return "keine Verbindung"
    k = kennung(a)
    return f"HTTP {a.status}" + (f" {k}" if k else "")


@dataclass(frozen=True)
class Ergebnis:
    messung: str
    urteil: str
    hinweis: str = ""


@dataclass(frozen=True)
class Zeile:
    endpunkt: str
    erwartung: str
    messung: str
    urteil: str
    hinweis: str = ""


@dataclass(frozen=True)
class Pruefung:
    endpunkt: str
    erwartung: str
    ausfuehren: Callable[["Messlauf"], Ergebnis]
    voraussetzung: bool = False


# ---------------------------------------------------------------------------------------------------------------------------------
# Messlauf, Wegwerf-Konto
# ---------------------------------------------------------------------------------------------------------------------------------

class _OhneUmleitung(urllib.request.HTTPRedirectHandler):
    """Folgt keiner Umleitung: Ein Aufruf mit dem Dienstschlüssel darf den Stack nie verlassen. Die 3xx-Antwort selbst wird gemessen."""

    def redirect_request(self, *args, **kwargs):
        return None


class Messlauf:
    """Verbindung zum Stack, die Schlüssel und der Zustand der Messung (Token des Besuchers, angelegte Fremdkonten, Zwischenergebnisse)."""

    def __init__(self, basis: str, anon: str, dienst: str, ctx: ssl.SSLContext | None = None):
        self.basis = basis.rstrip("/")
        self.anon, self.dienst, self.ctx = anon, dienst, ctx
        handler: list = [urllib.request.ProxyHandler({}), _OhneUmleitung()]  # kein Proxy aus der Umgebung, keine Umleitung
        if ctx is not None:
            handler.append(urllib.request.HTTPSHandler(context=ctx))
        self.opener = urllib.request.build_opener(*handler)
        self.token = ""
        self.konto: Konto | None = None
        self.fremde: list[str] = []
        self.zwischen: dict[str, object] = {}
        merke(anon)
        merke(dienst)

    def anfrage(self, methode: str, pfad: str, *, daten: dict | None = None, apikey: str | None = None, bearer: str | None = None,
                kopf: dict | None = None) -> Antwort:
        schluessel = apikey or self.anon
        h = {"apikey": schluessel, "Authorization": "Bearer " + (bearer or schluessel)}
        if daten is not None:
            h["Content-Type"] = "application/json"
        h.update(kopf or {})
        req = urllib.request.Request(self.basis + pfad, data=None if daten is None else json.dumps(daten).encode(), method=methode, headers=h)
        try:
            with self.opener.open(req, timeout=20) as r:
                return Antwort(r.status, r.read(ANTWORT_GRENZE))
        except urllib.error.HTTPError as e:
            try:
                return Antwort(e.code, e.read(ANTWORT_GRENZE))
            except (OSError, http.client.HTTPException):
                return Antwort(e.code)
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
            return Antwort(0)

    def besucher(self, methode: str, pfad: str, **kw) -> Antwort:
        """Aufruf so, wie ihn ein Besucher stellt: öffentlicher Schlüssel als apikey, sein aal2-Token als Bearer."""
        return self.anfrage(methode, pfad, apikey=self.anon, bearer=self.token, **kw)

    def dienst_anfrage(self, methode: str, pfad: str, **kw) -> Antwort:
        return self.anfrage(methode, pfad, apikey=self.dienst, **kw)

    def fremdes_konto_merken(self, a: Antwort) -> None:
        """Hat ein Aufruf, der abgelehnt werden müsste, ein Konto angelegt, wird es am Ende gelöscht."""
        d = a.als_json()
        if 200 <= a.status < 300 and isinstance(d, dict):
            nutzer = d.get("user")
            uid = d.get("id") or (nutzer.get("id") if isinstance(nutzer, dict) else None)
            if isinstance(uid, str) and uid not in self.fremde:
                self.fremde.append(uid)

    def konto_loeschen(self, uid: str) -> bool:
        for _ in range(2):
            if self.dienst_anfrage("DELETE", f"/auth/v1/admin/users/{uid}").status in (200, 204, 404):
                return True
        return False


class Konto:
    """Wegwerf-Konto mit zweitem Faktor, angelegt über die Admin-Schnittstelle (ohne WAMO-Profil)."""

    def __init__(self, ml: Messlauf):
        self.ml = ml
        self.email = f"labor-tokentest-{secrets.token_hex(6)}@example.org"
        self.passwort = secrets.token_urlsafe(24)
        merke(self.passwort)
        self.uid: str | None = None
        self.faktor: str | None = None
        self.geheimnis: str | None = None
        self.letztes_fenster = -1

    def _tokens_merken(self, a: Antwort) -> None:
        d = a.als_json()
        if isinstance(d, dict):
            merke(d.get("access_token"))
            merke(d.get("refresh_token"))

    def anlegen(self) -> Antwort:
        a = self.ml.dienst_anfrage("POST", "/auth/v1/admin/users", daten={"email": self.email, "password": self.passwort, "email_confirm": True})
        d = a.als_json()
        if a.status in (200, 201) and isinstance(d, dict) and isinstance(d.get("id"), str):
            self.uid = d["id"]
        return a

    def anmelden(self, passwort: str | None = None) -> Antwort:
        a = self.ml.anfrage("POST", "/auth/v1/token?grant_type=password", daten={"email": self.email, "password": passwort or self.passwort})
        self._tokens_merken(a)
        return a

    def faktor_einrichten(self, aal1: str) -> bool:
        a = self.ml.anfrage("POST", "/auth/v1/factors", daten={"factor_type": "totp", "friendly_name": "labor-tokentest", "issuer": "WAMO"}, bearer=aal1)
        d = a.als_json()
        if a.status in (200, 201) and isinstance(d, dict) and isinstance(d.get("totp"), dict) and "id" in d:
            self.faktor, self.geheimnis = d["id"], d["totp"]["secret"]
            merke(self.geheimnis)
        return self.faktor is not None

    def neues_aal2(self, bearer: str) -> Antwort:
        """Wartet bei Bedarf auf das nächste 30-Sekunden-Fenster, weil GoTrue einen Code nicht zweimal annimmt."""
        if int(time.time() // 30) <= self.letztes_fenster:
            time.sleep(30 - (time.time() % 30) + 1)
        c = self.ml.anfrage("POST", f"/auth/v1/factors/{self.faktor}/challenge", daten={}, bearer=bearer)
        d = c.als_json()
        if c.status not in (200, 201) or not isinstance(d, dict) or "id" not in d:
            return c
        self.letztes_fenster = int(time.time() // 30)
        a = self.ml.anfrage("POST", f"/auth/v1/factors/{self.faktor}/verify", daten={"challenge_id": d["id"], "code": totp_code(self.geheimnis or "")},
                            bearer=bearer)
        self._tokens_merken(a)
        return a


# ---------------------------------------------------------------------------------------------------------------------------------
# Prüfungen: lesen
# ---------------------------------------------------------------------------------------------------------------------------------

def pr_user_lesen(ml: Messlauf) -> Ergebnis:
    a = ml.besucher("GET", "/auth/v1/user")
    d = a.als_json()
    if a.status == 200 and isinstance(d, dict):
        if ml.konto is not None and d.get("id") == ml.konto.uid:
            return Ergebnis("HTTP 200, Daten des eigenen Kontos", DOKUMENTIERT,
                            "Das Token liest das eigene Konto, das ist vorgesehen. Dieser Aufruf liefert keine fremden Konten.")
        return Ergebnis("HTTP 200, aber ein anderes Konto als das Wegwerf-Konto", NICHT_GEMESSEN)
    return Ergebnis(kurz(a), NICHT_GEMESSEN)


def pr_rest_verzeichnis(ml: Messlauf) -> Ergebnis:
    a = ml.besucher("GET", "/rest/v1/", kopf={"Accept": "application/openapi+json"})
    if 200 <= a.status < 300:
        d = a.als_json()
        if not isinstance(d, dict):
            return Ergebnis(kurz(a) + ", keine auswertbare Antwort", NICHT_GEMESSEN)
        pfade = d.get("paths") if isinstance(d.get("paths"), dict) else {}
        text = a.roh.decode("utf-8", "replace")
        genannt = [s for s in WAMO_SCHEMAS if s in text] + ["/" + t for t in WAMO_TABELLEN if "/" + t in pfade]
        if genannt:
            return Ergebnis(f"HTTP {a.status}, nennt WAMO: {', '.join(genannt)}", OFFEN,
                            "Das Verzeichnis der Schnittstelle verrät WAMO-Schema oder WAMO-Tabellen. Prüfen, welche Schemas PostgREST bereitstellt "
                            "(am Server zu bestätigen).")
        return Ergebnis(f"HTTP {a.status}, Pfade: {len(pfade)}, kein WAMO-Schema, keine WAMO-Tabelle", ERWARTET)
    return Ergebnis(kurz(a), bewerte_ablehnung(a.status, kennung(a)))


def bewerte_lesen(a: Antwort) -> tuple[str, str]:
    """Ein Lesezugriff auf eine WAMO-Tabelle: Beschriftung für die Messung und Urteil. Zeileninhalte werden nie weitergegeben."""
    if 200 <= a.status < 300:
        d = a.als_json()
        if isinstance(d, list):
            return ("HTTP 200 mit Zeilen", OFFEN) if d else ("HTTP 200 leer", ERWARTET)
        return f"HTTP {a.status} unerwartete Antwort", NICHT_GEMESSEN
    return kurz(a), bewerte_ablehnung(a.status, kennung(a))


def rest_tabellen(ml: Messlauf, profil: str | None) -> Ergebnis:
    zaehler: collections.Counter[str] = collections.Counter()
    urteile = []
    for t in WAMO_TABELLEN:
        a = ml.besucher("GET", f"/rest/v1/{t}?select=*&limit=1", kopf={"Accept-Profile": profil} if profil else None)
        beschriftung, u = bewerte_lesen(a)
        zaehler[beschriftung] += 1
        urteile.append(u)
    messung = f"{len(WAMO_TABELLEN)} Tabellen: " + "; ".join(f"{b} ({n}x)" for b, n in zaehler.items())
    if OFFEN in urteile:
        return Ergebnis(messung, OFFEN, "Zeilen aus WAMO-Tabellen sind mit einem Besucher-Token lesbar. Rechte der Rolle authenticated, RLS und die von "
                                         "PostgREST bereitgestellten Schemas prüfen (am Server zu bestätigen).")
    if NICHT_GEMESSEN in urteile:
        return Ergebnis(messung, NICHT_GEMESSEN)
    return Ergebnis(messung, ERWARTET)


def pr_rest_ohne_profil(ml: Messlauf) -> Ergebnis:
    return rest_tabellen(ml, None)


def pr_rest_schema(ml: Messlauf, schema: str) -> Ergebnis:
    return rest_tabellen(ml, schema)


def pr_rest_schreiben(ml: Messlauf) -> Ergebnis:
    return Ergebnis("nicht gemessen", NICHT_GEMESSEN,
                    "Eine Schreibprobe würde bei einer Lücke echte Zeilen oder Buckets anlegen. Gemessen werden darum nur lesende Aufrufe. Die "
                    "Schreibrechte ergeben sich aus den Grants der Rollen (Migrationen: keine Rechte für anon, authenticated, service_role) und sind "
                    "am Server mit psql zu bestätigen.")


def pr_storage_buckets(ml: Messlauf) -> Ergebnis:
    a = ml.besucher("GET", "/storage/v1/bucket")
    if a.status == 200:
        d = a.als_json()
        if not isinstance(d, list):
            return Ergebnis("HTTP 200, keine auswertbare Antwort", NICHT_GEMESSEN)
        if not d:
            return Ergebnis("HTTP 200, leer (0 Buckets)", ERWARTET)
        return Ergebnis(f"HTTP 200, {len(d)} Bucket(s) sichtbar", OFFEN,
                        "Ein Besucher-Token sieht Buckets. Richtlinien auf storage.buckets und storage.objects prüfen (am Server zu bestätigen).")
    if 200 <= a.status < 300:
        return Ergebnis(kurz(a), NICHT_GEMESSEN)
    return Ergebnis(kurz(a), bewerte_ablehnung(a.status, kennung(a)))


# ---------------------------------------------------------------------------------------------------------------------------------
# Prüfungen: Realtime
# ---------------------------------------------------------------------------------------------------------------------------------

def ws_beitreten(ml: Messlauf, topic: str, konfig: dict) -> tuple[int, list[dict]]:
    """Öffnet den WebSocket, tritt einem Kanal bei (Phoenix-Protokoll wie supabase-js) und sammelt die Antworten. Status 101 heißt: Verbindung steht."""
    pfad = "/realtime/v1/websocket?apikey=" + urllib.parse.quote(ml.anon) + "&vsn=1.0.0"
    status, sock = ws_oeffnen(ml, pfad)
    if sock is None:
        return status, []
    nachrichten: list[dict] = []
    try:
        sock.sendall(ws_rahmen(json.dumps({"topic": topic, "event": "phx_join", "ref": "1", "join_ref": "1",
                                           "payload": {"config": konfig, "access_token": ml.token}})))
        ende = time.monotonic() + WS_WARTEZEIT_S
        while True:
            rest = ende - time.monotonic()
            if rest <= 0:
                break
            text = ws_lesen(sock, rest)
            if text is None:
                break
            try:
                m = json.loads(text)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            nachrichten.append(m)
            if m.get("event") == "phx_reply" and m.get("ref") == "1":
                ende = min(ende, time.monotonic() + WS_NACHLAUF_S)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            sock.close()
    return status, nachrichten


def ws_oeffnen(ml: Messlauf, pfad: str) -> tuple[int, socket.socket | None]:
    u = urllib.parse.urlsplit(ml.basis)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=WS_WARTEZEIT_S)
        if u.scheme == "https":
            sock = (ml.ctx or ssl.create_default_context()).wrap_socket(sock, server_hostname=host)
        schluessel = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {pfad} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {schluessel}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        kopf = b""
        while not kopf.endswith(b"\r\n\r\n"):
            byte = sock.recv(1)
            if not byte or len(kopf) > 16384:
                raise ConnectionError("kein vollständiger Kopf")
            kopf += byte
        zeilen = kopf.decode("latin-1").split("\r\n")
        status = int(zeilen[0].split()[1])
        felder = {z.split(":", 1)[0].strip().lower(): z.split(":", 1)[1].strip() for z in zeilen[1:] if ":" in z}
        if status != 101:
            sock.close()
            return status, None
        erwartet = base64.b64encode(hashlib.sha1((schluessel + WS_GUID).encode()).digest()).decode()
        if felder.get("sec-websocket-accept") != erwartet:
            raise ValueError("ungültiger Handshake")
        return 101, sock
    except (OSError, ValueError, IndexError):
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.close()
        return 0, None


def beitritt_auswerten(nachrichten: list[dict]) -> tuple[str | None, bool]:
    """Liefert die Antwort auf den Beitritt (ok, error oder None) und ob der Server danach einen Fehler gemeldet hat."""
    antwort: str | None = None
    fehler = False
    for m in nachrichten:
        ereignis = m.get("event")
        payload = m["payload"] if isinstance(m.get("payload"), dict) else {}
        if ereignis == "phx_reply" and m.get("ref") == "1":
            antwort = payload.get("status") if payload.get("status") in ("ok", "error") else None
        elif ereignis in ("phx_error", "phx_close") or (ereignis == "system" and payload.get("status") == "error"):
            fehler = True
    return antwort, fehler


def realtime_messen(ml: Messlauf) -> tuple[int, list[dict], int, list[dict]]:
    """Zwei Beitritte in eigenen Verbindungen, einmal je Lauf: privater Kanal mit zufälligem Namen, dann Änderungen im WAMO-Schema."""
    if "realtime" not in ml.zwischen:
        zufall = secrets.token_hex(4)
        s1, n1 = ws_beitreten(ml, f"realtime:labor-fremd-{zufall}",
                              {"private": True, "broadcast": {"self": False, "ack": False}, "presence": {"key": ""}})
        s2, n2 = (s1, []) if s1 != 101 else ws_beitreten(
            ml, f"realtime:labor-wamo-{zufall}",
            {"private": False, "postgres_changes": [{"event": "*", "schema": WAMO_SCHEMAS[0], "table": "*"}]})
        ml.zwischen["realtime"] = (s1, n1, s2, n2)
    return ml.zwischen["realtime"]  # type: ignore[return-value]


def _realtime_ohne_verbindung(status: int) -> Ergebnis:
    if status in (401, 403):
        return Ergebnis(f"HTTP {status}, Handshake abgewiesen", ERWARTET)
    return Ergebnis(f"HTTP {status}, Handshake nicht möglich" if status else "keine Verbindung oder ungültiger Handshake", NICHT_GEMESSEN,
                    "Realtime war nicht erreichbar oder hat den Handshake nicht wie erwartet beantwortet. Ob ein Besucher beitreten kann, ist "
                    "damit nicht gemessen.")


def pr_realtime_handshake(ml: Messlauf) -> Ergebnis:
    s1 = realtime_messen(ml)[0]
    if s1 == 101:
        return Ergebnis("HTTP 101, Verbindung steht", DOKUMENTIERT,
                        "Der Handshake verlangt nur den öffentlichen Schlüssel, den jeder Besucher im Browser sieht. Rechte zeigen erst die "
                        "Beitritte darunter.")
    return _realtime_ohne_verbindung(s1)


def pr_realtime_privat(ml: Messlauf) -> Ergebnis:
    s1, n1, _, _ = realtime_messen(ml)
    if s1 != 101:
        return _realtime_ohne_verbindung(s1)
    antwort, fehler = beitritt_auswerten(n1)
    if antwort == "error":
        return Ergebnis("Beitritt abgewiesen (phx_reply error)", ERWARTET)
    if antwort == "ok" and not fehler:
        return Ergebnis("Beitritt gelungen (phx_reply ok)", OFFEN,
                        "Ein Besucher kann einem privaten Kanal mit frei gewähltem Namen beitreten. Richtlinien auf realtime.messages und die "
                        "Einstellung für öffentlichen Zugang im Realtime-Dienst prüfen (am Server zu bestätigen), oder den Pfad /realtime/ am "
                        "Vorbau sperren, falls WAMO ihn nicht nutzt.")
    return Ergebnis("keine eindeutige Antwort auf den Beitritt", NICHT_GEMESSEN)


def pr_realtime_aenderungen(ml: Messlauf) -> Ergebnis:
    s1, _, s2, n2 = realtime_messen(ml)
    if s1 != 101 or s2 != 101:
        return _realtime_ohne_verbindung(s2)
    antwort, fehler = beitritt_auswerten(n2)
    if antwort == "error" or fehler:
        return Ergebnis("Abo auf WAMO-Änderungen abgewiesen", ERWARTET)
    if antwort == "ok":
        return Ergebnis("Beitritt gelungen, Auslieferung nicht gemessen", NICHT_GEMESSEN,
                        "Der Beitritt ist gelungen. Ob Änderungen aus WAMO-Tabellen ankommen, belegt nur eine echte Änderung an einer WAMO-Tabelle, "
                        "und die löst der Test bewusst nicht aus. Der Schutz der Zeilen hängt an Rechten und RLS (siehe die Leseproben über /rest).")
    return Ergebnis("keine eindeutige Antwort auf den Beitritt", NICHT_GEMESSEN)


# ---------------------------------------------------------------------------------------------------------------------------------
# Prüfungen: Auth, Admin, Registrierung
# ---------------------------------------------------------------------------------------------------------------------------------

def _admin_pfad(ml: Messlauf, methode: str, pfad: str, daten: dict | None, hinweis_offen: str) -> Ergebnis:
    a = ml.besucher(methode, pfad, daten=daten)
    u = bewerte_ablehnung(a.status, kennung(a), streng=True)
    if u == OFFEN:
        ml.fremdes_konto_merken(a)
        return Ergebnis(f"HTTP {a.status}, Aufruf angenommen", OFFEN, hinweis_offen)
    return Ergebnis(kurz(a), u)


def _fremde_adresse() -> str:
    return f"labor-fremd-{secrets.token_hex(6)}@example.org"


def pr_admin_liste(ml: Messlauf) -> Ergebnis:
    return _admin_pfad(ml, "GET", "/auth/v1/admin/users?per_page=1", None,
                       "Die Admin-Schnittstelle von GoTrue antwortet einem Besucher-Token und liefert die Kontenliste. Vor dem Labortag klären.")


def pr_admin_anlegen(ml: Messlauf) -> Ergebnis:
    passwort = secrets.token_urlsafe(24)
    merke(passwort)
    return _admin_pfad(ml, "POST", "/auth/v1/admin/users", {"email": _fremde_adresse(), "password": passwort, "email_confirm": True},
                       "Ein Besucher-Token kann über die Admin-Schnittstelle Konten anlegen. Vor dem Labortag klären.")


def pr_signup(ml: Messlauf) -> Ergebnis:
    passwort = secrets.token_urlsafe(24)
    merke(passwort)
    a = ml.besucher("POST", "/auth/v1/signup", daten={"email": _fremde_adresse(), "password": passwort})
    u = bewerte_ablehnung(a.status, kennung(a))
    if u == OFFEN:
        ml.fremdes_konto_merken(a)
        return Ergebnis(f"HTTP {a.status}, Konto angelegt", OFFEN,
                        "Die Registrierung ist offen, jeder mit dem öffentlichen Schlüssel kann Konten anlegen. GOTRUE_DISABLE_SIGNUP=true setzen "
                        "(laut Stand 12.11.2026 vorgesehen) und den Auth-Dienst neu starten.")
    return Ergebnis(kurz(a), u)


def pr_invite(ml: Messlauf) -> Ergebnis:
    return _admin_pfad(ml, "POST", "/auth/v1/invite", {"email": _fremde_adresse()},
                       "Ein Besucher-Token kann Einladungen auslösen und damit Konten anlegen. Vor dem Labortag klären.")


def pr_recover(ml: Messlauf) -> Ergebnis:
    a = ml.besucher("POST", "/auth/v1/recover", daten={"email": _fremde_adresse()})
    if 200 <= a.status < 300:
        return Ergebnis(f"HTTP {a.status}, Anfrage angenommen", DOKUMENTIERT,
                        "Gestellt wurde die Anfrage mit einer nicht vorhandenen Adresse, damit keine Post entsteht und kein echtes Konto betroffen "
                        "ist. Nicht gemessen: ob die Antwort für vorhandene Adressen gleich ausfällt.")
    return Ergebnis(kurz(a), bewerte_ablehnung(a.status, kennung(a)))


def pr_faktor_anlegen(ml: Messlauf) -> Ergebnis:
    a = ml.besucher("POST", "/auth/v1/factors", daten={"factor_type": "totp", "friendly_name": f"labor-zweitfaktor-{secrets.token_hex(3)}",
                                                      "issuer": "WAMO"})
    d = a.als_json()
    if a.status in (200, 201) and isinstance(d, dict) and isinstance(d.get("id"), str):
        ml.zwischen["neuer_faktor"] = d["id"]
        if isinstance(d.get("totp"), dict):
            merke(d["totp"].get("secret"))
        return Ergebnis(f"HTTP {a.status}, weiterer Faktor (unbestätigt) angelegt", DOKUMENTIERT,
                        "Ein Besucher kann weitere Faktoren für sein eigenes Konto anlegen. Der Aufruf gilt nur für das Konto des Tokens.")
    if 200 <= a.status < 300:
        return Ergebnis(kurz(a), NICHT_GEMESSEN)
    return Ergebnis(kurz(a), bewerte_ablehnung(a.status, kennung(a)))


def _faktor_loeschen(ml: Messlauf, faktor: object, hinweis: str) -> Ergebnis:
    if not isinstance(faktor, str):
        return Ergebnis("kein Faktor zum Löschen vorhanden", NICHT_GEMESSEN)
    a = ml.besucher("DELETE", f"/auth/v1/factors/{faktor}")
    if 200 <= a.status < 300:
        return Ergebnis(f"HTTP {a.status}, Faktor entfernt", DOKUMENTIERT, hinweis)
    return Ergebnis(kurz(a), bewerte_ablehnung(a.status, kennung(a)))


def pr_faktor_neu_loeschen(ml: Messlauf) -> Ergebnis:
    return _faktor_loeschen(ml, ml.zwischen.get("neuer_faktor"), "Ein Besucher kann seine eigenen, unbestätigten Faktoren wieder entfernen.")


def pr_faktor_bestaetigt_loeschen(ml: Messlauf) -> Ergebnis:
    return _faktor_loeschen(
        ml, ml.konto.faktor if ml.konto else None,
        "Ein Besucher kann seinen bestätigten zweiten Faktor mit aal2 entfernen und einen eigenen einrichten. Der Faktor des Betreuers wäre dann "
        "ungültig und der Platz für den nächsten Besucher blockiert. Das bleibt ein Restrisiko. Ob GoTrue dafür einen Schalter bietet, ist am Server "
        "zu prüfen. Abhilfe im Betrieb: Faktor als Admin neu einrichten oder Reserveprofil nehmen, Laborkonten nach dem Labortag löschen.")


def pr_passwort_aendern(ml: Messlauf) -> Ergebnis:
    neu = secrets.token_urlsafe(24)
    merke(neu)
    a = ml.besucher("PUT", "/auth/v1/user", daten={"password": neu})
    if not 200 <= a.status < 300:
        return Ergebnis(kurz(a), bewerte_ablehnung(a.status, kennung(a)))
    konto = ml.konto
    alt = konto.anmelden() if konto else Antwort(0)
    if alt.status == 200:
        return Ergebnis(f"HTTP {a.status}, aber das alte Passwort gilt weiter", NICHT_GEMESSEN)
    if konto is not None and 400 <= alt.status < 500:
        konto.passwort = neu
        konto.anmelden()
    folge = "altes Passwort danach ungültig" if 400 <= alt.status < 500 else "Folgeprüfung ohne Ergebnis"
    return Ergebnis(f"HTTP {a.status}, {folge}", OFFEN,
                    "Ein Besucher mit aal2-Token kann das Passwort seines Platzes ohne erneute Anmeldung ändern und den Platz für den nächsten "
                    f"Besucher sperren. Gegenmaßnahme: {GEGENMASSNAHME_PASSWORT} am Auth-Dienst setzen (Name aus der GoTrue-Dokumentation, am "
                    "Server zu bestätigen), den Dienst neu starten und diesen Test wiederholen.")


def plan() -> list[Pruefung]:
    """Reihenfolge: erst lesen und ablehnen lassen, solange das Token sicher gilt, am Ende die Änderungen am eigenen Konto."""
    p = [
        Pruefung("GET /auth/v1/user (eigenes Konto)", "eigenes Konto lesbar (erlaubt)", pr_user_lesen, voraussetzung=True),
        Pruefung("GET /rest/v1/ (Verzeichnis der Schnittstelle)", "kein WAMO-Schema, keine WAMO-Tabelle genannt", pr_rest_verzeichnis),
        Pruefung("GET /rest/v1/<tabelle> ohne Schema-Profil", "abgewiesen oder leer", pr_rest_ohne_profil),
    ]
    for s in WAMO_SCHEMAS:
        p.append(Pruefung(f"GET /rest/v1/<tabelle> mit Schema-Profil {s}", "abgewiesen oder leer", partial(pr_rest_schema, schema=s)))
    p += [
        Pruefung("POST /rest/v1/<tabelle> und /storage/v1 (Schreiben)", "abgewiesen", pr_rest_schreiben),
        Pruefung("GET /storage/v1/bucket", "abgewiesen oder leere Liste", pr_storage_buckets),
        Pruefung("WS /realtime/v1/websocket (Handshake)", "gelingt mit öffentlichem Schlüssel (dokumentiert)", pr_realtime_handshake),
        Pruefung("Realtime: Beitritt zu einem fremden privaten Kanal", "abgewiesen", pr_realtime_privat),
        Pruefung(f"Realtime: Mitlesen von Änderungen in {WAMO_SCHEMAS[0]}", "abgewiesen oder keine Lieferung", pr_realtime_aenderungen),
        Pruefung("GET /auth/v1/admin/users", "401 oder 403", pr_admin_liste),
        Pruefung("POST /auth/v1/admin/users", "401 oder 403", pr_admin_anlegen),
        Pruefung("POST /auth/v1/signup", "abgelehnt", pr_signup),
        Pruefung("POST /auth/v1/invite", "401 oder 403", pr_invite),
        Pruefung("POST /auth/v1/recover", "angenommen, kein fremdes Konto betroffen", pr_recover),
        Pruefung("POST /auth/v1/factors", "eigener Faktor anlegbar (dokumentiert)", pr_faktor_anlegen),
        Pruefung("DELETE /auth/v1/factors/<neuer Faktor>", "eigener Faktor löschbar (dokumentiert)", pr_faktor_neu_loeschen),
        Pruefung("PUT /auth/v1/user (Passwort)", "ohne erneute Anmeldung abgelehnt", pr_passwort_aendern),
        Pruefung("DELETE /auth/v1/factors/<bestätigter Faktor>", "eigener Faktor löschbar (dokumentiert)", pr_faktor_bestaetigt_loeschen),
    ]
    return p


# ---------------------------------------------------------------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------------------------------------------------------------

def env_lesen(datei: str) -> dict[str, str]:
    try:
        with open(datei, encoding="utf-8") as f:
            zeilen = f.read().splitlines()
    except OSError:
        raise Abbruch(f"Umgebungsdatei {datei} nicht lesbar") from None
    w: dict[str, str] = {}
    for z in zeilen:
        if "=" in z and not z.lstrip().startswith("#"):
            k, v = z.split("=", 1)
            w[k.strip()] = v.strip().strip('"').strip("'")
    fehlend = [k for k in ("ANON_KEY", "SERVICE_ROLE_KEY") if not w.get(k)]
    if fehlend:
        raise Abbruch(f"Umgebungsdatei {datei}: {', '.join(fehlend)} fehlt")
    return w


def vorbereiten(ml: Messlauf, konto: Konto) -> bool:
    """Wegwerf-Konto anlegen, anmelden (aal1), TOTP einrichten und bestätigen (aal2). False bei jedem Fehlschlag."""
    a = konto.anlegen()
    if not pruefe("Wegwerf-Konto angelegt (Admin-Schnittstelle, ohne WAMO-Profil)", konto.uid is not None,
                  f"{kurz(a)}; bei Bedarf Konto {konto.email} von Hand prüfen"):
        return False
    a = konto.anmelden()
    d = a.als_json()
    if not pruefe("Anmeldung mit Passwort liefert ein Token (aal1)", a.status == 200 and isinstance(d, dict) and "access_token" in d, kurz(a)):
        return False
    aal1 = d["access_token"]  # type: ignore[index]
    if not pruefe("zweiter Faktor (TOTP) eingerichtet", konto.faktor_einrichten(aal1)):
        return False
    a = konto.neues_aal2(aal1)
    d = a.als_json()
    if not pruefe("Code bestätigt: Token mit aal2", a.status == 200 and isinstance(d, dict) and "access_token" in d, kurz(a)):
        return False
    c = claims(d["access_token"])  # type: ignore[index]
    if not pruefe("das Token trägt aal2 und einen totp-Eintrag", c.get("aal") == "aal2" and any(e.get("method") == "totp" for e in c.get("amr", []))):
        return False
    ml.token = d["access_token"]  # type: ignore[index]
    return True


def messen(ml: Messlauf, pruefungen: list[Pruefung]) -> list[Zeile]:
    zeilen: list[Zeile] = []
    for p in pruefungen:
        try:
            e = p.ausfuehren(ml)
        except Exception as ex:  # noqa: BLE001 (eine kaputte Prüfung darf die übrigen und das Aufräumen nicht verhindern)
            e = Ergebnis(f"Fehler bei der Messung: {type(ex).__name__}", NICHT_GEMESSEN,
                         "Die Prüfung ist abgebrochen. Einzelheiten werden absichtlich nicht ausgegeben, sie könnten Geheimnisse enthalten.")
        if p.voraussetzung and e.urteil != DOKUMENTIERT:
            raise Abbruch(f"Token wird nicht akzeptiert oder passt nicht zum Konto ({e.messung}), alle weiteren Messungen wären wertlos")
        zeilen.append(Zeile(p.endpunkt, p.erwartung, bereinigen(e.messung), e.urteil, bereinigen(e.hinweis)))
    return zeilen


def aufraeumen(ml: Messlauf, konto: Konto) -> bool:
    sauber = True
    if not konto.uid and not ml.fremde:
        ausgabe("  nichts angelegt, nichts zu löschen")
    for uid in ml.fremde:
        if not ml.konto_loeschen(uid):
            sauber = False
            ausgabe(f"  FEHLER von der Prüfung angelegtes Konto nicht gelöscht, Kennung {uid}, bitte von Hand entfernen")
    if ml.fremde and sauber:
        ausgabe(f"  OK     {len(ml.fremde)} von der Prüfung angelegte fremde Konten gelöscht")
    if konto.uid:
        if ml.konto_loeschen(konto.uid):
            ausgabe("  OK     Wegwerf-Konto gelöscht")
        else:
            sauber = False
            ausgabe(f"  FEHLER Wegwerf-Konto nicht gelöscht, bitte von Hand entfernen: {konto.email}")
    return sauber


def ausgeben(zeilen: list[Zeile], plan_nur: bool = False) -> None:
    ausgabe(tabelle(SPALTEN, [(z.endpunkt, z.erwartung, z.messung, z.urteil) for z in zeilen], BREITEN))
    if plan_nur:
        return
    mit_hinweis = [z for z in zeilen if z.hinweis and z.urteil in (OFFEN, DOKUMENTIERT, NICHT_GEMESSEN)]
    if mit_hinweis:
        ausgabe()
        ausgabe("Hinweise")
        for z in mit_hinweis:
            ausgabe(textwrap.fill(f"{z.urteil}, {z.endpunkt}: {z.hinweis}", 124, initial_indent="  ", subsequent_indent="    "))


def zusammenfassung(zeilen: list[Zeile]) -> None:
    zaehler = collections.Counter(z.urteil for z in zeilen)
    ausgabe()
    ausgabe(f"Ergebnis: {zaehler[ERWARTET]} erwartet, {zaehler[DOKUMENTIERT]} dokumentiert, {zaehler[OFFEN]} OFFEN, "
            f"{zaehler[NICHT_GEMESSEN]} nicht gemessen")


def trockenlauf() -> tuple[int, list[Zeile]]:
    ausgabe("Labor-Tokentest, Trockenlauf: Es wird nichts angelegt und nichts aufgerufen.")
    ausgabe("Vorbereitung: Wegwerf-Konto ohne WAMO-Profil anlegen (Admin-Schnittstelle, Loopback), anmelden (aal1), TOTP einrichten und bestätigen "
            "(aal2), prüfen, dass GoTrue das Token für das eigene Konto annimmt.")
    ausgabe()
    zeilen = [Zeile(p.endpunkt, p.erwartung, "geplant", "-") for p in plan()]
    ausgeben(zeilen, plan_nur=True)
    ausgabe()
    ausgabe("Aufräumen: Wegwerf-Konto und alle von der Prüfung angelegten Konten löschen, auch nach einem Fehler.")
    return 0, zeilen


def lauf(argv: list[str]) -> tuple[int, list[Zeile]]:
    ap = argparse.ArgumentParser(description="Was darf ein Laborkonto (aal2) mit seinem Token? Messung mit einem Wegwerf-Konto.")
    ap.add_argument("--trocken", action="store_true", help="nur den Plan zeigen, nichts anlegen und nichts aufrufen")
    ap.add_argument("--basis", default=BASIS, help=f"Eingang des Stacks (Standard {BASIS})")
    ap.add_argument("--env-datei", default=ENV_DATEI, help="Datei mit ANON_KEY und SERVICE_ROLE_KEY")
    ap.add_argument("--ca", default=None, help="CA-Datei, wenn --basis eine https-Adresse mit der WAMO-CA ist")
    a = ap.parse_args(argv)
    GEHEIMNISSE.clear()
    if a.trocken:
        return trockenlauf()
    ausgabe(f"Labor-Tokentest: Was darf ein Laborkonto (aal2) mit seinem Token? Basis {a.basis}")
    try:
        env = env_lesen(a.env_datei)
        ctx = ssl.create_default_context(cafile=a.ca) if a.ca else None
    except Abbruch as e:
        ausgabe(f"  FEHLER {e}")
        return 1, []
    except (OSError, ssl.SSLError):
        ausgabe("  FEHLER CA-Datei nicht lesbar")
        return 1, []
    ml = Messlauf(a.basis, env["ANON_KEY"], env["SERVICE_ROLE_KEY"], ctx)
    konto = Konto(ml)
    ml.konto = konto
    zeilen: list[Zeile] = []
    fehler = False
    try:
        if vorbereiten(ml, konto):
            zeilen = messen(ml, plan())
            ausgabe()
            ausgeben(zeilen)
        else:
            fehler = True
    except Abbruch as e:
        ausgabe(f"  FEHLER {e}")
        fehler = True
    except Exception as e:  # noqa: BLE001
        ausgabe(f"  FEHLER unerwarteter Fehler ({type(e).__name__}), die Messung wurde abgebrochen")
        fehler = True
    finally:
        ausgabe()
        ausgabe("Aufräumen")
        sauber = aufraeumen(ml, konto)
    if zeilen:
        zusammenfassung(zeilen)
    offen = any(z.urteil == OFFEN for z in zeilen)
    if not sauber:
        ausgabe("Das Aufräumen ist nicht vollständig, der Lauf gilt als gescheitert.")
    return (1 if (offen or fehler or not sauber) else 0), zeilen


def main(argv: list[str] | None = None) -> int:
    return lauf(sys.argv[1:] if argv is None else argv)[0]


if __name__ == "__main__":
    sys.exit(main())
