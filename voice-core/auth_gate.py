"""Zugangskontrolle für den Voice-Core: Sitzungen starten nur angemeldete Konten mit zweitem
Faktor (aal2) und aktivem WAMO-Profil.

Prüfung je HTTP-Anfrage an den Pipecat-Runner (/start, /api/offer, /sessions/..., ICE-Updates):
  1. Bearer-Token: Signatur, Ablauf, Aussteller (iss), Zielgruppe (aud), aal2 und Höchstalter der Anmeldung.
     Hauptweg ES256/RS256 lokal über die öffentlichen Schlüssel des Stacks (JWKS, gecacht).
     Rückfall HS256: GoTrue prüft selbst (GET /auth/v1/user mit SUPABASE_PUBLISHABLE_KEY).
     Beide Abrufe laufen über HTTPS und vertrauen zusätzlich der WAMO-CA aus WAMO_CA_FILE (wamo_tls.py).
  2. Profil in <WAMO_DB_SCHEMA>.profiles nachschlagen (Cache: aktives Profil 60 s, kein oder
     inaktives Profil 10 s). Datenbank nicht konfiguriert oder nicht erreichbar: 503, also
     ebenfalls abgelehnt (fail-closed). Der eigene WAMO-Stack hat eine geschlossene Registrierung
     (Konten legt nur der Admin an); trotzdem gibt allein ein aktives WAMO-Profil Zugang. Eine
     laufende Sitzung prüft das Profil zusätzlich jede Minute neu (bot.py, SessionGuard).
  Statuscodes (so wertet sie die Oberfläche aus): 401 = Token fehlt, ist ungültig oder abgelaufen
  ("Anmeldung abgelehnt"); 403 = Token gültig, aber zweiter Faktor fehlt (aal1) oder kein aktives
  Profil ("kein aktives WAMO-Profil oder zweiter Faktor fehlt"); 503 = Anmeldedienst (JWKS, GoTrue)
  oder Datenbank nicht erreichbar, auch bei gescheiterter TLS-Prüfung.
  SUPABASE_URL muss https:// sein; http:// nur über Loopback (Voice-Core auf demselben Rechner wie der
  Stack). Sonst ist die Prüfung nicht eingerichtet und jede Anfrage wird abgelehnt.
  3. Geprüfte Identität an den Bot weitergeben (siehe unten).

Höchstalter der Anmeldung: Gemessen ab Passwort und Code, also am frühesten Zeitstempel im Claim amr (GoTrue behält ihn
beim Erneuern des Tokens bei), nicht am letzten Erneuern. Tokens ohne amr werden abgelehnt. Grenze
WAMO_ANMELDUNG_MAX_STUNDEN (Standard 168, also 7 Tage; erlaubt mehr als 0 und höchstens 720, sonst gilt der Standard). Sie gilt auch für
gecachte Tokens (der Zeitpunkt liegt mit im Cache). Abgelehnt wird mit 401 und code "anmeldung_zu_alt"; die Oberfläche
meldet dann ab. Eine laufende Sitzung beendet bot.SessionGuard beim Überschreiten (Identity.anmeldung). Die Oberfläche
meldet im Browser früher ab (NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER, ui/src/lib/anmeldung.ts).
CORS-Preflight (OPTIONS) bleibt offen, er trägt keine Daten. WebSockets lehnt die Middleware ab:
WAMO nutzt nur WebRTC.

Wie die Identität zum Bot kommt (geprüft gegen Pipecat 1.12, pipecat/runner/run.py):
  * POST /start legt den JSON-Schlüssel "body" als Sitzungsdaten ab und startet noch keinen Bot.
  * Der Bot startet mit dem WebRTC-Offer. runner_args.body ist dann "request_data" aus diesem
    Offer: direkt bei POST /api/offer, beim Proxy POST /sessions/<id>/api/offer zuerst
    "request_data", dann "requestData", erst ohne beides der gespeicherte /start-Body.
  * Die Middleware schreibt die geprüfte Identität deshalb an beide Stellen: bei /start unter
    body.wamo, bei jedem Offer (jeder POST-Pfad, der auf api/offer endet, wie Pipecat prüft)
    unter request_data.wamo. Ein vom Client gesendeter Schlüssel "wamo" wird immer ersetzt,
    "requestData" wird in "request_data" überführt. Weil jedes Offer danach ein nicht leeres
    request_data trägt, greift der /start-Fallback nie.
  * Ergebnis: runner_args.body["wamo"] stammt immer aus dem Token der Anfrage, die den Bot
    gestartet hat. Zusätzliche Body-Felder aus /start erreichen den Bot nur, wenn der Client sie
    im Offer erneut mitschickt. bot.py beendet jede Sitzung ohne gültige Identität sofort.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import jwt
from loguru import logger

import labor
import labor_durchlauf
from memory import ANREDEN, ROLLEN, DatabaseUnavailable, Profile, ProfileDirectory, normalize_uuid, shared_database
from wamo_tls import https_context

TOKEN_CACHE_SECS = 60  # geprüfte Tokens so lange merken: ICE-Updates kommen im Sekundentakt
PROFILE_HIT_SECS = 60.0  # aktives Profil
PROFILE_MISS_SECS = 10.0  # kein oder inaktives Profil
LEEWAY_SECS = 30  # Uhren von Laptop und Server dürfen etwas auseinanderlaufen
MAX_BODY_BYTES = 256 * 1024  # SDP-Offers sind wenige Kilobyte groß
IDENTITY_KEY = "wamo"
MAX_NAME_CHARS = 60
ANMELDUNG_MAX_STUNDEN = 168.0  # Standard: 7 Tage ab Passwort und Code
ANMELDUNG_MAX_STUNDEN_GRENZE = 720.0  # mehr als 30 Tage lässt sich nicht einstellen
CODE_ZU_ALT = "anmeldung_zu_alt"


def anmeldung_max_secs() -> float:
    """WAMO_ANMELDUNG_MAX_STUNDEN in Sekunden. Leer, unlesbar, nicht positiv oder über 720: Standard 168 Stunden."""
    roh = (os.getenv("WAMO_ANMELDUNG_MAX_STUNDEN") or "").strip()
    if not roh:
        return ANMELDUNG_MAX_STUNDEN * 3600
    try:
        stunden = float(roh)
    except ValueError:
        stunden = math.nan
    if not (math.isfinite(stunden) and 0 < stunden <= ANMELDUNG_MAX_STUNDEN_GRENZE):
        logger.error("WAMO_ANMELDUNG_MAX_STUNDEN {!r} ungültig (0 bis {} Stunden), es gilt {}", roh[:20],
                     ANMELDUNG_MAX_STUNDEN_GRENZE, ANMELDUNG_MAX_STUNDEN)
        return ANMELDUNG_MAX_STUNDEN * 3600
    return stunden * 3600


def anmeldezeit(claims: dict) -> float | None:
    """Beginn der Anmeldung: frühester Zeitstempel in amr (Passwort, danach Code). None, wenn keiner gültig ist."""
    eintraege = claims.get("amr")
    if not isinstance(eintraege, list):
        return None
    stempel = [e.get("timestamp") for e in eintraege if isinstance(e, dict)]
    gueltig = [float(t) for t in stempel
               if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) and t > 0]
    return min(gueltig) if gueltig else None


def stunden_text(secs: float) -> str:
    """Dauer in Stunden für Meldungen: "1 Stunde", "1,5 Stunden", "168 Stunden"."""
    stunden = secs / 3600
    return f"{stunden:g} Stunden".replace(".", ",") if stunden != 1 else "1 Stunde"


def _zu_alt(anmeldung: float, jetzt: float, max_secs: float) -> str | None:
    if jetzt - anmeldung <= max_secs:
        return None
    return f"Anmeldung älter als {stunden_text(max_secs)}, bitte neu anmelden"


@dataclass(frozen=True)
class Identity:
    """Geprüfte Identität, wie sie der Bot in runner_args.body["wamo"] sieht. anmeldung: Beginn der Anmeldung
    (Unix-Zeit aus amr), für das Höchstalter in bot.SessionGuard; zählt nicht beim Vergleich.
    Laborprofil (Testlabor, labor.py): labor_art (platz oder schaufenster, None = kein Laborprofil; ein unbekannter Wert ergibt platz),
    labor_platz (1 bis 16, nur platz) und labor_aktiv (die Freischaltung, die das Gate gelesen hat). Ein Laborprofil ist nie Admin."""

    user_id: str
    name: str
    rolle: str
    anrede: str
    anmeldung: float | None = field(default=None, compare=False)
    labor_art: str | None = None
    labor_platz: int | None = None
    labor_aktiv: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "labor_art", labor.art_normalisieren(self.labor_art))

    @property
    def is_admin(self) -> bool:
        return self.rolle == "admin" and self.labor_art is None

    def to_body(self) -> dict[str, str | float | int | bool | None]:
        body: dict[str, str | float | int | bool | None] = {"user_id": self.user_id, "name": self.name, "rolle": self.rolle,
                                                            "anrede": self.anrede}
        if self.anmeldung is not None:
            body["anmeldung"] = self.anmeldung
        if self.labor_art is not None:  # nur bei Laborprofilen: andere Identitäten behalten ihren Body wie bisher
            body.update({"labor_art": self.labor_art, "labor_platz": self.labor_platz, "labor_aktiv": self.labor_aktiv})
        return body


def identity_from_body(body: object) -> Identity | None:
    """Liest die von der Middleware gesetzte Identität. None, wenn sie fehlt oder unvollständig ist."""
    if not isinstance(body, dict):
        return None
    raw = body.get(IDENTITY_KEY)
    if not isinstance(raw, dict):
        return None
    user_id = normalize_uuid(raw.get("user_id"))
    name = " ".join(str(raw.get("name") or "").split())[:MAX_NAME_CHARS]
    rolle, anrede, anmeldung = raw.get("rolle"), raw.get("anrede"), raw.get("anmeldung")
    if user_id is None or not name or rolle not in ROLLEN or anrede not in ANREDEN:
        return None
    # Den Anmeldezeitpunkt setzt die Zugangskontrolle immer; ohne ihn ist die Identität nicht geprüft
    if isinstance(anmeldung, bool) or not isinstance(anmeldung, (int, float)) or not math.isfinite(anmeldung) \
            or anmeldung <= 0:
        return None
    art = labor.art_normalisieren(raw.get("labor_art"))
    if art is None:
        return Identity(user_id=user_id, name=name, rolle=rolle, anrede=anrede, anmeldung=float(anmeldung))
    # Laborprofil: die Zugangskontrolle schreibt Art, Platz und Freischaltung immer; eine Identität ohne stimmige Laborfelder ist nicht geprüft
    platz, aktiv = raw.get("labor_platz"), raw.get("labor_aktiv")
    if aktiv is not True or not labor.laborfelder_stimmig(art, rolle, platz, False):
        return None
    return Identity(user_id=user_id, name=name, rolle=rolle, anrede=anrede, anmeldung=float(anmeldung), labor_art=art,
                    labor_platz=platz, labor_aktiv=True)


@dataclass(frozen=True)
class Decision:
    identity: Identity | None
    status: int = 200
    reason: str = ""
    code: str | None = None  # maschinenlesbar für die Oberfläche, z. B. anmeldung_zu_alt


class ProfileLookup(Protocol):
    async def get(self, user_id: str) -> Profile | None: ...


@dataclass(frozen=True)
class TokenCheck:
    """Ergebnis der Token-Prüfung: user_id oder Grund plus HTTP-Status (401 oder 403). anmeldung und code zählen nicht
    beim Vergleich (anmeldung: Beginn der Anmeldung aus amr; code: z. B. anmeldung_zu_alt)."""

    user_id: str | None
    reason: str | None = None
    status: int = 200
    anmeldung: float | None = field(default=None, compare=False)
    code: str | None = field(default=None, compare=False)


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def supabase_url_problem(url: str | None) -> str | None:
    """Grund, warum SUPABASE_URL nicht genutzt wird, oder None. Außerhalb von Loopback nur https."""
    if not url:
        return "Voice-Core ohne SUPABASE_URL"
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return None
    if parts.scheme == "http" and _loopback(host):
        return None
    return "SUPABASE_URL ohne https (http nur über Loopback erlaubt)"


class TokenVerifier:
    """Prüft Supabase-Access-Tokens. check() liefert TokenCheck (user_id oder Grund und Status).

    SUPABASE_URL muss https sein (http nur über Loopback), sonst ist der Prüfer nicht eingerichtet.
    ssl_context: Vertrauensbasis für JWKS und GoTrue. Ohne Angabe gilt wamo_tls.https_context()
    (öffentliche Zertifikate plus WAMO-CA aus WAMO_CA_FILE); ein unlesbarer WAMO_CA_FILE wirft CaFileError.
    max_anmeldung_secs: Höchstalter der Anmeldung (Standard aus WAMO_ANMELDUNG_MAX_STUNDEN); uhr: nur für Tests.
    """

    def __init__(
        self,
        supabase_url: str | None,
        api_key: str | None = None,
        *,
        issuer: str | None = None,
        jwks_client: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
        ssl_context: ssl.SSLContext | None = None,
        max_anmeldung_secs: float | None = None,
        uhr: Callable[[], float] = time.time,
    ):
        self.problem = supabase_url_problem(supabase_url)
        self.max_anmeldung_secs = max_anmeldung_secs if max_anmeldung_secs is not None else anmeldung_max_secs()
        self._uhr = uhr
        self.configured = self.problem is None
        if supabase_url and not self.configured:
            logger.error("Zugangskontrolle: {}; alle Anfragen werden abgelehnt", self.problem)
        base = (supabase_url or "").strip().rstrip("/")
        self.auth_url = f"{base}/auth/v1"
        self.issuer = (issuer or "").rstrip("/") or self.auth_url
        self.api_key = api_key
        self.ssl_context = ssl_context if ssl_context is not None else (https_context() if self.configured else None)
        self._jwks = jwks_client
        if self._jwks is None and self.configured:
            self._jwks = jwt.PyJWKClient(
                f"{self.auth_url}/.well-known/jwks.json", cache_keys=True, lifespan=600, ssl_context=self.ssl_context
            )
        self._transport = transport  # nur für Tests
        self._ok_until: dict[str, tuple[float, str, float]] = {}  # Token-Hash -> (gültig bis, user_id, Anmeldung)

    async def check(self, token: str) -> TokenCheck:
        if not self.configured:
            return TokenCheck(None, self.problem or "Voice-Core ohne SUPABASE_URL", 401)
        if not token:
            return TokenCheck(None, "nicht angemeldet", 401)
        key = hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()
        now = self._uhr()
        cached = self._ok_until.get(key)
        if cached and cached[0] > now:
            # Auch aus dem Cache: das Höchstalter der Anmeldung gilt mit der aktuellen Zeit
            if grund := _zu_alt(cached[2], now, self.max_anmeldung_secs):
                return TokenCheck(None, grund, 401, code=CODE_ZU_ALT)
            return TokenCheck(cached[1], anmeldung=cached[2])
        try:
            alg = jwt.get_unverified_header(token).get("alg")
            if alg in ("ES256", "RS256"):
                signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=[alg],
                    audience="authenticated",
                    issuer=self.issuer,
                    leeway=LEEWAY_SECS,
                    options={"require": ["exp", "sub", "iss", "aud"]},
                )
            elif alg == "HS256":
                claims = await self._check_remote(token)
            else:
                return TokenCheck(None, f"Signaturverfahren {alg} nicht erlaubt", 401)
        except jwt.ExpiredSignatureError:
            return TokenCheck(None, "Anmeldung abgelaufen", 401)
        except (jwt.PyJWKClientConnectionError, httpx.TransportError, ssl.SSLError) as e:
            # Netz oder TLS-Prüfung zum Stack gescheitert: nicht die Schuld des Nutzers. Nur der Fehlertyp ins Log.
            logger.warning("Zugangskontrolle: Anmeldedienst nicht erreichbar ({})", type(e).__name__)
            return TokenCheck(None, "Anmeldedienst nicht erreichbar", 503)
        except (jwt.PyJWTError, httpx.HTTPError, ValueError) as e:
            return TokenCheck(None, f"Token ungültig ({type(e).__name__})", 401)

        user_id = normalize_uuid(claims.get("sub"))
        if user_id is None:
            return TokenCheck(None, "Token ohne gültiges Konto", 401)
        if claims.get("aal") != "aal2":
            return TokenCheck(None, "zweiter Faktor fehlt", 403)
        anmeldung = anmeldezeit(claims)
        if anmeldung is None:
            return TokenCheck(None, "Anmeldezeitpunkt fehlt im Token (amr)", 401)
        if grund := _zu_alt(anmeldung, now, self.max_anmeldung_secs):
            return TokenCheck(None, grund, 401, code=CODE_ZU_ALT)
        self._ok_until = {k: v for k, v in self._ok_until.items() if v[0] > now}
        self._ok_until[key] = (min(float(claims["exp"]), now + TOKEN_CACHE_SECS), user_id, anmeldung)
        return TokenCheck(user_id, anmeldung=anmeldung)

    async def _check_remote(self, token: str) -> dict:
        verify: ssl.SSLContext | bool = self.ssl_context if self.ssl_context is not None else True
        async with httpx.AsyncClient(timeout=5, transport=self._transport, verify=verify) as client:
            response = await client.get(
                f"{self.auth_url}/user",
                headers={"Authorization": f"Bearer {token}", "apikey": self.api_key or ""},
            )
        if response.status_code != 200:
            raise jwt.InvalidTokenError(f"GoTrue lehnt ab ({response.status_code})")
        # Die Signatur hat GoTrue geprüft; Ablauf, Aussteller und Zielgruppe prüfen wir selbst.
        claims = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
                "require": ["exp", "sub", "iss", "aud"],
            },
            audience="authenticated",
            issuer=self.issuer,
            leeway=LEEWAY_SECS,
        )
        try:
            user = response.json()
        except ValueError as e:
            raise jwt.InvalidTokenError("GoTrue-Antwort ist kein JSON") from e
        if not isinstance(user, dict) or user.get("id") != claims.get("sub"):
            raise jwt.InvalidTokenError("Token und GoTrue-Nutzer passen nicht zusammen")
        return claims


class AccessGate:
    """Token plus aktives Profil. Jede Unsicherheit führt zur Ablehnung."""

    def __init__(
        self,
        verifier: TokenVerifier,
        profiles: ProfileLookup | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.verifier = verifier
        self.profiles = profiles
        self._clock = clock
        self._cache: dict[str, tuple[float, Profile | None]] = {}

    async def authorize(self, token: str) -> Decision:
        result = await self.verifier.check(token)
        user_id = result.user_id
        if user_id is None:
            return Decision(None, result.status if result.status in (401, 403, 503) else 401,
                            result.reason or "nicht angemeldet", code=result.code)
        if self.profiles is None:
            return Decision(None, 503, "Profilprüfung nicht möglich: Datenbank ist nicht eingerichtet")
        try:
            profile = await self._profile(user_id)
        except DatabaseUnavailable:
            return Decision(None, 503, "Profilprüfung nicht möglich: Datenbank nicht erreichbar")
        except Exception:  # fail-closed: unbekannte Fehler dürfen nie Zugang geben
            logger.exception("Zugangskontrolle: Profilprüfung fehlgeschlagen")
            return Decision(None, 503, "Profilprüfung nicht möglich")
        if profile is None:
            return Decision(None, 403, "Für dieses Konto gibt es kein WAMO-Profil.")
        # Laborprofil: wirksam aktiv nur über labor_aktiv und mit stimmigen Feldern (Rolle mitglied, Platz); alle anderen über aktiv
        if labor.art_von(profile) is not None and (problem := labor.profil_problem(profile)) is not None:
            logger.warning("Zugangskontrolle: Laborprofil unstimmig ({})", problem)
            return Decision(None, 403, labor.GRUND_PROFIL, code=labor.CODE_PROFIL)
        if not labor.profil_aktiv(profile):
            return Decision(None, 403, labor.GRUND_DEAKTIVIERT)
        art = labor.art_von(profile)
        identity = Identity(
            user_id=profile.user_id,
            name=" ".join(profile.name.split())[:MAX_NAME_CHARS] or "Gast",
            rolle=profile.rolle if profile.rolle in ROLLEN else "mitglied",
            anrede=profile.anrede if profile.anrede in ANREDEN else "du",
            anmeldung=result.anmeldung,
            labor_art=art,
            labor_platz=profile.labor_platz if art is not None else None,
            labor_aktiv=art is not None,
        )
        return Decision(identity)

    async def _profile(self, user_id: str) -> Profile | None:
        now = self._clock()
        cached = self._cache.get(user_id)
        if cached and cached[0] > now:
            return cached[1]
        profile = await self.profiles.get(user_id)  # Fehler werden nicht gecacht
        ttl = PROFILE_HIT_SECS if profile is not None and labor.profil_aktiv(profile) else PROFILE_MISS_SECS
        self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[user_id] = (now + ttl, profile)
        return profile


# ---------- Body-Injektion ----------
class BodyError(ValueError):
    pass


def _starts_bot(path: str) -> str | None:
    """'start' oder 'offer', wenn eine POST-Anfrage auf diesem Pfad Daten an einen Bot gibt.
    Absichtlich mindestens so weit gefasst wie Pipecat (path.endswith("api/offer"))."""
    p = path.rstrip("/")
    if p == "/start":
        return "start"
    if p.endswith("api/offer"):
        return "offer"
    return None


def inject_identity(kind: str, raw: bytes, identity: Identity) -> bytes:
    """Setzt die Identität in den JSON-Body und ersetzt jeden vom Client gesendeten 'wamo'-Schlüssel."""
    try:
        data = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
        raise BodyError("Body ist kein gültiges JSON") from e
    if not isinstance(data, dict):
        raise BodyError("Body muss ein JSON-Objekt sein")
    if kind == "start":
        body = data.get("body")
        body = {k: v for k, v in body.items() if k != IDENTITY_KEY} if isinstance(body, dict) else {}
        body[IDENTITY_KEY] = identity.to_body()
        data["body"] = body
    else:
        rd = data.pop("request_data", None)
        alt = data.pop("requestData", None)
        source = rd if isinstance(rd, dict) and rd else alt if isinstance(alt, dict) else {}
        request_data = {k: v for k, v in source.items() if k != IDENTITY_KEY}
        request_data[IDENTITY_KEY] = identity.to_body()
        data["request_data"] = request_data
    try:
        # ensure_ascii: einzelne Surrogate aus dem Client-JSON bleiben als \\u-Escape gültig
        return json.dumps(data).encode("ascii")
    except (RecursionError, ValueError) as e:
        raise BodyError("Body ist kein gültiges JSON") from e


Receive = Callable[[], Awaitable[dict]]


async def _read_body(receive: Receive, limit: int) -> bytes | None:
    """Liest den ganzen Body. None, wenn er zu groß ist oder der Client abbricht."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


class AccessMiddleware:
    """ASGI-Middleware vor dem Pipecat-Runner (eingehängt über install_access_gate)."""

    def __init__(self, app, gate: AccessGate, labor_pruefer: Callable[[Identity], Awaitable[Any]] | None = None):
        self.app = app
        self.gate = gate
        # Laborprofile: Durchlauf und Einwilligung bei /start und jedem Offer (labor_durchlauf.pruefen); nur Tests setzen etwas anderes
        self._labor_pruefer = labor_pruefer or labor_durchlauf.pruefen

    async def _labor_urteil(self, identity: Identity, scope) -> tuple[int, str, str | None] | None:
        """None, wenn die Anfrage eines Laborprofils durchgelassen wird, sonst (Status, Grund, Code). Zuerst die Pfadliste (exakt, Default deny),
        dann bei /start und Offer der Durchlauf. Jeder Fehler in der Prüfung heißt 503, nie Durchlass."""
        art = labor.pfad_pruefen(identity.labor_art, scope["method"], scope["path"], scope.get("raw_path"))
        if art is None:
            return 403, "Dieser Weg ist für dieses Profil gesperrt.", labor.CODE_PFAD_GESPERRT
        if art not in ("start", "offer"):
            return None
        try:
            urteil = await self._labor_pruefer(identity)
        except Exception as e:  # fail-closed
            logger.warning("Zugangskontrolle: Laborprüfung fehlgeschlagen ({})", type(e).__name__)
            return 503, labor_durchlauf.GRUND_DIENST, labor_durchlauf.CODE_DIENST
        if urteil.status != 200:
            return urteil.status, urteil.grund, urteil.code
        return None

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if kind != "http" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return

        auth = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"authorization"), "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        decision = await self.gate.authorize(token)
        if decision.identity is None:
            logger.warning("Zugang abgelehnt: {} {} ({})", scope["method"], scope["path"], decision.reason)
            await _reject(send, decision.status, decision.reason, decision.code)
            return

        if labor.ist_labor(decision.identity) and (urteil := await self._labor_urteil(decision.identity, scope)) is not None:
            logger.warning("Zugang für Laborprofil abgelehnt: {} ({})", scope["method"], urteil[2])
            await _reject(send, urteil[0], urteil[1], urteil[2])
            return

        target = _starts_bot(scope["path"]) if scope["method"] == "POST" else None
        if target:
            raw = await _read_body(receive, MAX_BODY_BYTES)
            if raw is None:
                await _reject(send, 413, "Anfrage zu groß oder abgebrochen")
                return
            try:
                body = inject_identity(target, raw, decision.identity)
            except BodyError as e:
                await _reject(send, 400, str(e))
                return
            headers = [(k, v) for k, v in scope["headers"] if k not in (b"content-length", b"transfer-encoding")]
            headers.append((b"content-length", str(len(body)).encode("ascii")))
            scope = {**scope, "headers": headers}
            receive = _replay(body, receive)
        await self.app(scope, receive, send)


async def _reject(send, status: int, reason: str, code: str | None = None) -> None:
    daten = {"error": "nicht berechtigt", "detail": reason}
    if code:
        daten["code"] = code
    body = json.dumps(daten, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8")]
    if status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def install_access_gate(app) -> AccessGate:
    """Hängt die Zugangskontrolle vor alle Routen des Runners. Ohne Konfiguration: alles zu."""
    verifier = TokenVerifier(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_PUBLISHABLE_KEY"),
        issuer=os.getenv("WAMO_AUTH_ISSUER"),
    )
    if not os.getenv("SUPABASE_URL"):  # eine unbrauchbare Adresse meldet TokenVerifier selbst
        logger.error("Zugangskontrolle: SUPABASE_URL fehlt, alle Anfragen werden abgelehnt")
    profiles: ProfileDirectory | None = None
    try:
        directory = ProfileDirectory(shared_database())
        if directory.configured:
            profiles = directory
            logger.info("Zugangskontrolle: Profile aus Schema {}", directory.schema)
        else:
            logger.error("Zugangskontrolle: WAMO_DATABASE_URL fehlt, alle Anfragen werden abgelehnt")
    except ValueError as e:
        logger.error("Zugangskontrolle: {}; alle Anfragen werden abgelehnt", e)
    gate = AccessGate(verifier, profiles)
    app.add_middleware(AccessMiddleware, gate=gate)
    return gate
