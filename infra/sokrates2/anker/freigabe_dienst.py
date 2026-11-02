"""Prozess des Freigabe-Dienstes (Konzept docs/selbstueberarbeitung.md E.2, Schritt 1.4b): die Hülle um die Regeln in freigabe.py.

Ein kleiner Prozess ohne root (Benutzer wamo-anker, Unit wamo-freigabe.service). Er lauscht auf einem Unix-Socket und nimmt genau
eine Anfrage entgegen:

    POST /wamo/release/<anfrage-id>/freigeben
    Authorization: Bearer <Token der Sitzung mit frischem zweiten Faktor>
    Body: {"commit_id": "<40 oder 64 Hex>", "tree_id": "<40 oder 64 Hex>"}

Alles andere beantwortet er mit 404 oder 405 und liest nichts. Die Gegenstelle prüft SO_PEERCRED (nur die erlaubten Benutzer, am
Anfang root; Caddy wird in Schritt 1.4c ergänzt), und das Token prüft freigabe.py (Signatur, Aussteller, Zielgruppe, aal2, Frische).
Die Quell-IP kommt aus dem letzten Eintrag von X-Forwarded-For (setzt der Proxy; wer sonst verbinden könnte, scheitert an
SO_PEERCRED). Der Schlüsselsatz kommt von GoTrue (JWKS über HTTPS mit der eigenen CA, im Speicher, kurz gepuffert).

Nie in einer Ausgabe oder Protokollzeile: das Token, der Schlüsselsatz, Teile davon, die Zugangsdaten, Texte der Datenbank. Das
Protokoll (stderr, Journal) enthält je Anfrage nur Statuscode, Fehlercode und Dauer.

Konfiguration nur aus der Unit (Umgebung): WAMO_FREIGABE_SCHEMA, WAMO_FREIGABE_JWKS_URL, WAMO_FREIGABE_ISS, WAMO_FREIGABE_AUD,
WAMO_FREIGABE_PEERS (Liste von Benutzerkennungen, Standard 0). Zugangsdatei (/etc/wamo-freigabe) und Zertifikat (/etc/wamo-anker/wamo-ca.crt) liegen fest.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import socketserver
import ssl
import stat
import struct
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler
from typing import Callable

PYLIB = "/opt/wamo-freigabe/pylib"  # PyJWT, cryptography, psycopg: hash-gesichert und root-eigen (freigabe-einrichten.sh)
if os.path.isdir(PYLIB) and PYLIB not in sys.path:
    sys.path.append(PYLIB)  # hinten angehängt: Standardbibliothek und eigene Module gehen vor, nichts in PYLIB verdeckt sie

import jwt  # noqa: E402
from freigabe import FEHLER, FreigabeFehler, Freigabedienst, fehler  # noqa: E402

APP = os.path.dirname(os.path.realpath(__file__))
ZUGANG_DATEI = "/etc/wamo-freigabe/db.zugang"  # Kopie der Datei des Ankers, root:wamo-anker 0640 (freigabe-einrichten.sh)
SOCKET_PFAD = "/run/wamo-freigabe/freigabe.sock"

PFAD_RE = re.compile(r"/wamo/release/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/freigeben")
BODY_MAX = 2048
KOPF_MAX = 16384          # Anfragezeile und Kopfzeilen zusammen (http.server begrenzt eine Zeile auf 65536)
LESEN_TIMEOUT_S = 10.0
PARALLEL_MAX = 8
JWKS_MAX_BYTES = 65536
JWKS_TTL_S = 300.0
JWKS_MIN_ABSTAND_S = 30.0
JWKS_ALT_MAX_S = 3600.0   # ein Schlüssel, den GoTrue nicht mehr nennt, gilt höchstens so lange weiter, wenn der Abruf scheitert
SCHEMA_RE = re.compile(r"^wamo_(dev|test|prod)$")


# ------------------------------------------------------------------------------------------------ Schlüsselsatz von GoTrue
def jwks_abrufen(url: str, ca_datei: str, timeout: float = 5.0) -> bytes:
    """Holt den Schlüsselsatz über HTTPS (Zertifikat und Name werden gegen die eigene CA geprüft), ohne Proxy, ohne Weiterleitung."""
    if not url.startswith("https://"):
        raise ValueError("nur https")
    ctx = ssl.create_default_context(cafile=ca_datei)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    class KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):  # noqa: ANN002, ANN003
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx), KeineWeiterleitung())
    with opener.open(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as r:
        if r.status != 200:
            raise OSError("Status")
        daten = r.read(JWKS_MAX_BYTES + 1)
    if len(daten) > JWKS_MAX_BYTES:
        raise ValueError("zu groß")
    return daten


class JwksQuelle:
    """schluessel(kid) für token_pruefen: öffentliche P-256-Schlüssel nach Kennung. Ein unbekannter oder veralteter Schlüssel löst
    höchstens alle 30 Sekunden einen neuen Abruf aus (ein Angreifer mit erfundenen kid kann GoTrue nicht fluten)."""

    def __init__(self, abrufen: Callable[[], bytes], uhr: Callable[[], float] = time.monotonic):
        self._abrufen, self._uhr = abrufen, uhr
        self._schluessel: dict[str, object] = {}
        self._geholt: float | None = None
        self._versuch: float | None = None
        self._sperre = threading.Lock()

    def _laden(self, jetzt: float) -> None:
        self._versuch = jetzt
        try:
            neu: dict[str, object] = {}
            for k in json.loads(self._abrufen()).get("keys", []):
                if (isinstance(k, dict) and k.get("kty") == "EC" and k.get("crv") == "P-256" and isinstance(k.get("kid"), str)
                        and k.get("use", "sig") == "sig"):
                    neu[k["kid"]] = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(k))
            if neu:
                self._schluessel, self._geholt = neu, jetzt
        except Exception:  # noqa: BLE001  jede Panne: der alte Satz bleibt, nie ein Text im Protokoll
            pass

    def __call__(self, kid: str | None) -> object:
        if not isinstance(kid, str):
            raise KeyError("kid")
        with self._sperre:
            jetzt = self._uhr()
            frisch = self._geholt is not None and jetzt - self._geholt < JWKS_TTL_S
            if not (frisch and kid in self._schluessel) and (self._versuch is None or jetzt - self._versuch >= JWKS_MIN_ABSTAND_S):
                self._laden(jetzt)
            if kid in self._schluessel and self._geholt is not None and jetzt - self._geholt < JWKS_ALT_MAX_S:
                return self._schluessel[kid]
        raise KeyError("kid")


# ------------------------------------------------------------------------------------------------ die Anwendung (ohne Netz)
class Anwendung:
    """Entscheidet über eine schon gelesene Anfrage. Kein Socket, keine Datei: so auch ohne Linux prüfbar."""

    def __init__(self, dienst: Freigabedienst):
        self.dienst = dienst

    @staticmethod
    def _antwort(fe: FreigabeFehler) -> tuple[int, dict]:
        return fe.http, {"ok": False, "grund": fe.code, "text": fe.text}

    @staticmethod
    def quell_ip(weitergeleitet: str | None) -> str | None:
        """Letzter Eintrag von X-Forwarded-For, wenn er eine IP ist; sonst keine Angabe (die Freigabe scheitert daran nie)."""
        if not weitergeleitet or len(weitergeleitet) > 512:
            return None
        letzter = weitergeleitet.split(",")[-1].strip()
        if "%" in letzter:  # Scope-Angaben (fe80::1%eth0) kennt weder inet noch freigabe.py
            return None
        try:
            return str(ipaddress.ip_address(letzter))
        except ValueError:
            return None

    def verarbeite(self, methode: str, pfad: str, kopf: dict[str, str], body: bytes) -> tuple[int, dict]:
        """kopf: kleingeschriebene Namen. Gibt (Statuscode, JSON-Objekt) zurück und wirft nie."""
        try:
            m = PFAD_RE.fullmatch(pfad)
            if m is None:
                return 404, {"ok": False, "grund": "unbekannt", "text": "Diesen Pfad gibt es nicht."}
            if methode != "POST":
                return 405, {"ok": False, "grund": "methode", "text": "Nur POST ist erlaubt."}
            auth = kopf.get("authorization", "")
            if not auth.startswith("Bearer ") or auth.count(" ") != 1:
                raise fehler("token_ungueltig")
            if not kopf.get("content-type", "").lower().startswith("application/json"):
                raise fehler("angaben")
            try:
                daten = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise fehler("angaben") from None
            if not isinstance(daten, dict):
                raise fehler("angaben")
            erg = self.dienst.freigeben(token=auth[7:], anfrage=m[1], commit_id=daten.get("commit_id"), tree_id=daten.get("tree_id"),
                                        quell_ip=self.quell_ip(kopf.get("x-forwarded-for")))
            return 200, erg
        except FreigabeFehler as e:
            return self._antwort(e)
        except Exception:  # noqa: BLE001  nie ein Text der Panne nach außen
            return 500, {"ok": False, "grund": "intern", "text": "Interner Fehler."}


# ------------------------------------------------------------------------------------------------ Socket und HTTP
def gegenstelle(sock: socket.socket) -> tuple[int, int, int]:
    """(pid, uid, gid) der Gegenstelle laut Kernel."""
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # eine Anfrage je Verbindung
    server_version = "wamo-freigabe"
    sys_version = ""
    timeout = LESEN_TIMEOUT_S

    def handle(self) -> None:
        try:
            super().handle()
        finally:
            self.server.slots.release()  # type: ignore[attr-defined]

    def log_message(self, *args: object) -> None:  # noqa: ANN002  http.server schreibt sonst Pfad und Adresse: nichts davon ins Journal
        return

    def _senden(self, status: int, objekt: dict, t0: float) -> None:
        daten = json.dumps(objekt, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(daten)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(daten)
        print(f"freigabe {status} {objekt.get('grund', 'ok')} {int((time.monotonic() - t0) * 1000)} ms", file=sys.stderr, flush=True)

    def send_error(self, code, message=None, explain=None) -> None:  # noqa: ANN001  Fehler beim Lesen der Anfrage: JSON statt HTML
        self._senden(code if code in (400, 408, 414, 431, 501) else 400,
                     {"ok": False, "grund": "anfrage", "text": "Die Anfrage ist nicht lesbar."}, time.monotonic())
        self.close_connection = True

    def _alle(self) -> None:
        t0 = time.monotonic()
        kopf = {k.lower(): v for k, v in self.headers.items()}
        if self.headers.get("Transfer-Encoding") is not None or len(self.headers.get_all("Content-Length") or []) > 1:
            return self._senden(400, {"ok": False, "grund": "anfrage", "text": "Die Anfrage ist nicht lesbar."}, t0)
        try:
            laenge = int(kopf.get("content-length", "0"))
        except ValueError:
            laenge = -1
        if not 0 <= laenge <= BODY_MAX:
            return self._senden(413 if laenge > BODY_MAX else 400, {"ok": False, "grund": "angaben", "text": "Die Angaben sind zu groß."}, t0)
        body = self.rfile.read(laenge) if laenge else b""
        if len(body) != laenge:
            return self._senden(400, {"ok": False, "grund": "anfrage", "text": "Die Anfrage ist nicht lesbar."}, t0)
        status, objekt = self.server.anwendung.verarbeite(self.command, self.path, kopf, body)  # type: ignore[attr-defined]
        self._senden(status, objekt, t0)

    do_POST = do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _alle


class Server(socketserver.ThreadingMixIn, getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):  # Unix-Socket nur unter Linux
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, pfad: str, anwendung: Anwendung, erlaubt: frozenset[int]):
        self.anwendung, self.erlaubt = anwendung, erlaubt
        self.slots = threading.BoundedSemaphore(PARALLEL_MAX)
        if os.path.lexists(pfad):  # ein Rest eines früheren Laufs, aber nie etwas anderes (kein Link, keine fremde Datei)
            st = os.lstat(pfad)
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.geteuid():
                raise SystemExit(f"{pfad} ist kein eigener Socket, Abbruch")
            os.unlink(pfad)
        alt = os.umask(0o117)
        try:
            super().__init__(pfad, Handler)
        finally:
            os.umask(alt)  # Socket 0660: nur der Dienst und seine Gruppe

    def verify_request(self, request, client_address) -> bool:  # noqa: ANN001
        try:
            uid = gegenstelle(request)[1]
        except OSError:
            return False
        if uid not in self.erlaubt:
            return False
        return self.slots.acquire(blocking=False)  # zu viele gleichzeitige: die Verbindung wird ohne Antwort geschlossen


# ------------------------------------------------------------------------------------------------ Selbstprüfung der Installation
def installation_pruefen(app: str, pylib: str | None, summen_pylib: str | None, besitzer: int = 0) -> list[str]:
    """Probleme der installierten Dateien: Prüfsummen, fremde Dateien, Schreibrechte. Leere Liste heißt in Ordnung."""
    probleme: list[str] = []

    def summen(datei: str) -> dict[str, str]:
        erg = {}
        with open(datei, encoding="utf-8") as f:
            for zeile in f:
                s, _, name = zeile.rstrip("\n").partition("  ")
                if len(s) == 64 and name:
                    erg[name.removeprefix("./")] = s
        return erg

    def pruefe(ordner: str, soll: dict[str, str]) -> None:
        gefunden = set()
        for wurzel, _dirs, dateien in os.walk(ordner):
            for d in dateien:
                rel = os.path.relpath(os.path.join(wurzel, d), ordner).replace(os.sep, "/")
                if rel == "SHA256SUMS":
                    continue
                gefunden.add(rel)
        for rel in sorted(gefunden - set(soll)):
            probleme.append(f"fremde Datei {rel}")
        for rel, s in soll.items():
            p = os.path.join(ordner, rel)
            try:
                with open(p, "rb") as f:
                    if hashlib.sha256(f.read()).hexdigest() != s:
                        probleme.append(f"Prüfsumme weicht ab: {rel}")
                st = os.lstat(p)
                if st.st_uid != besitzer or st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode):
                    probleme.append(f"Rechte: {rel}")
            except OSError:
                probleme.append(f"fehlt: {rel}")

    try:
        pruefe(app, summen(os.path.join(app, "SHA256SUMS")))
        if pylib and summen_pylib and os.path.isdir(pylib):
            pruefe(pylib, summen(summen_pylib))
    except OSError:
        probleme.append("SHA256SUMS fehlt oder ist nicht lesbar")
    return probleme


# ------------------------------------------------------------------------------------------------ Start
def dienst_bauen(umgebung: dict[str, str]) -> tuple[Anwendung, frozenset[int]]:
    from freigabe_pg import FreigabePg
    from wamo_pg import Pg, zugangsdatei_lesen

    schema = umgebung.get("WAMO_FREIGABE_SCHEMA", "")
    jwks_url, iss = umgebung.get("WAMO_FREIGABE_JWKS_URL", ""), umgebung.get("WAMO_FREIGABE_ISS", "")
    aud = umgebung.get("WAMO_FREIGABE_AUD", "authenticated")
    peers = frozenset(int(x) for x in umgebung.get("WAMO_FREIGABE_PEERS", "0").split(",") if x.strip().isdigit())
    if not (SCHEMA_RE.fullmatch(schema) and jwks_url.startswith("https://") and iss and aud and peers):
        raise SystemExit("Konfiguration unvollständig (Schema, JWKS-Adresse, Aussteller, Zielgruppe, erlaubte Benutzer)")
    ziel = zugangsdatei_lesen(ZUGANG_DATEI)
    ca = ziel.sslrootcert
    db = FreigabePg(Pg(ziel, anwendung="wamo-freigabe"), schema)
    quelle = JwksQuelle(lambda: jwks_abrufen(jwks_url, ca))
    return Anwendung(Freigabedienst(db, schluessel=quelle, aussteller=iss, zielgruppe=aud)), peers


def main(argv: list[str]) -> int:
    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        print("Der Dienst läuft nur unter Linux und nie als root.", file=sys.stderr)
        return 1
    if argv[1:2] == ["selbstpruefung"]:
        probleme = installation_pruefen(APP, PYLIB, os.path.join(os.path.dirname(APP), "pylib.SHA256SUMS"))
        for p in probleme:
            print(f"Installation: {p}", file=sys.stderr)
        print("Installation in Ordnung" if not probleme else "Installation weicht ab, Abbruch", file=sys.stderr)
        return 0 if not probleme else 1
    if argv[1:2] != ["dienst"]:
        print("Aufruf: freigabe_dienst.py dienst | selbstpruefung", file=sys.stderr)
        return 2
    anwendung, peers = dienst_bauen(dict(os.environ))
    server = Server(SOCKET_PFAD, anwendung, peers)
    os.chmod(SOCKET_PFAD, 0o660)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"Freigabe-Dienst lauscht auf {SOCKET_PFAD} (erlaubte Benutzer: {sorted(peers)}, Fehlercodes: {len(FEHLER)})",
          file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if os.path.lexists(SOCKET_PFAD):
            os.unlink(SOCKET_PFAD)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
