"""Kleiner HTTP/1.1-Client für Geräte im Büro-LAN (IPP, eSCL, UPnP-SOAP, Shelly, TR-064, Samsung, LiteLLM).

Bewusst eigener Code statt einer allgemeinen Bibliothek: Er spricht nur die Adresse und den Port, die der Treiber
aus geraete.toml übergibt, folgt keinen Weiterleitungen, kennt keine Proxys aus der Umgebung, keine Cookies und
keine Namensauflösung. Antworten sind begrenzt (Kopf 16 KB, Rumpf max_bytes, gezählt beim Lesen), jede Anfrage hat
eine Gesamtzeitgrenze. Digest-Anmeldung (MD5, SHA-256; Shelly, FRITZ!Box) und TLS mit festem
Zertifikats-Fingerabdruck (FRITZ!Box 49443) sind eingebaut.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import re
import secrets
import ssl
from dataclasses import dataclass, field

KOPF_GRENZE = 16 * 1024
_PFAD_RE = re.compile(r"^/[\x21-\x7e]{0,2000}$")
_WERT_RE = re.compile(r"^[\x20-\x7e]{0,4000}$")
_TOKEN_RE = re.compile(r'(\w+)=("([^"]*)"|[^,\s]+)')


class HttpFehler(RuntimeError):
    """Gerät nicht erreichbar, Antwort kaputt, zu groß oder zu langsam (Text ohne Gerätedaten)."""


@dataclass
class Antwort:
    status: int
    kopf: dict[str, str] = field(default_factory=dict)
    rumpf: bytes = b""
    authentifizieren: list[str] = field(default_factory=list)


def _pruefe_ziel(adresse: str, port: int) -> None:
    ip = ipaddress.ip_address(adresse)  # wirft bei Namen
    if ip.version != 4:
        raise HttpFehler("nur IPv4")
    if not (isinstance(port, int) and 0 < port < 65536):
        raise HttpFehler("Port ungültig")


def _fingerabdruck(der: bytes) -> str:
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


async def _lies_kopf(reader: asyncio.StreamReader) -> tuple[int, dict[str, str], list[str]]:
    try:
        roh = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError as e:
        raise HttpFehler("Antwortkopf zu groß") from e
    except asyncio.IncompleteReadError as e:
        raise HttpFehler("Verbindung ohne vollständigen Antwortkopf beendet") from e
    if len(roh) > KOPF_GRENZE:
        raise HttpFehler("Antwortkopf zu groß")
    zeilen = roh.decode("latin-1").split("\r\n")
    m = re.match(r"^HTTP/1\.[01] (\d{3})", zeilen[0])
    if not m:
        raise HttpFehler("keine HTTP-Antwort")
    kopf: dict[str, str] = {}
    auth: list[str] = []
    for z in zeilen[1:]:
        if not z or ":" not in z:
            continue
        k, v = z.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "www-authenticate":
            auth.append(v)
        kopf[k] = v
    return int(m.group(1)), kopf, auth


async def _lies_rumpf(reader: asyncio.StreamReader, kopf: dict[str, str], max_bytes: int, methode: str,
                      status: int) -> bytes:
    if methode == "HEAD" or status in (204, 304) or 100 <= status < 200:
        return b""
    if "chunked" in kopf.get("transfer-encoding", "").lower():
        teile: list[bytes] = []
        summe = 0
        while True:
            zeile = await reader.readline()
            if not zeile:
                raise HttpFehler("chunked-Antwort abgebrochen")
            try:
                groesse = int(zeile.split(b";", 1)[0].strip(), 16)
            except ValueError as e:
                raise HttpFehler("chunked-Antwort kaputt") from e
            if groesse == 0:
                while (z := await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                return b"".join(teile)
            summe += groesse
            if summe > max_bytes:
                raise HttpFehler("Antwort zu groß")
            teile.append(await reader.readexactly(groesse))
            await reader.readexactly(2)
    laenge = kopf.get("content-length")
    if laenge is not None:
        if not laenge.isdigit():
            raise HttpFehler("Content-Length ungültig")
        n = int(laenge)
        if n > max_bytes:
            raise HttpFehler("Antwort zu groß")
        return await reader.readexactly(n)
    teile = []
    summe = 0
    while stueck := await reader.read(65536):
        summe += len(stueck)
        if summe > max_bytes:
            raise HttpFehler("Antwort zu groß")
        teile.append(stueck)
    return b"".join(teile)


def _digest_kopf(challenge: str, methode: str, uri: str, benutzer: str, passwort: str) -> str | None:
    if not challenge.lower().startswith("digest "):
        return None
    werte = {m.group(1).lower(): (m.group(3) if m.group(3) is not None else m.group(2))
             for m in _TOKEN_RE.finditer(challenge[7:])}
    realm, nonce = werte.get("realm"), werte.get("nonce")
    if realm is None or nonce is None:
        return None
    algo = (werte.get("algorithm") or "MD5").upper()
    if algo not in ("MD5", "SHA-256"):
        return None
    h = (lambda s: hashlib.md5(s.encode(), usedforsecurity=False).hexdigest()) if algo == "MD5" else \
        (lambda s: hashlib.sha256(s.encode()).hexdigest())
    ha1 = h(f"{benutzer}:{realm}:{passwort}")
    ha2 = h(f"{methode}:{uri}")
    qops = [q.strip() for q in (werte.get("qop") or "").split(",") if q.strip()]
    teile = [f'username="{benutzer}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"', f"algorithm={algo}"]
    if "auth" in qops:
        cnonce, nc = secrets.token_hex(8), "00000001"
        antwort = h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        teile += [f'response="{antwort}"', "qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    else:
        teile.append(f'response="{h(f"{ha1}:{nonce}:{ha2}")}"')
    if "opaque" in werte:
        teile.append(f'opaque="{werte["opaque"]}"')
    return "Digest " + ", ".join(teile)


async def _einmal(adresse: str, port: int, methode: str, pfad: str, kopf: dict[str, str], rumpf: bytes,
                  max_bytes: int, tls: ssl.SSLContext | None, tls_sha256: str | None) -> Antwort:
    reader, writer = await asyncio.open_connection(adresse, port, ssl=tls, server_hostname="" if tls else None,
                                                   limit=KOPF_GRENZE + 1024)
    try:
        if tls is not None:
            der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True) or b""
            if not tls_sha256 or not hmac.compare_digest(_fingerabdruck(der), tls_sha256.upper()):
                raise HttpFehler("Zertifikat des Geräts passt nicht zum festgehaltenen Fingerabdruck")
        zeilen = [f"{methode} {pfad} HTTP/1.1", f"Host: {adresse}:{port}", "Connection: close",
                  "User-Agent: wamo-geraete", "Accept-Encoding: identity"]
        for k, v in kopf.items():
            if not re.fullmatch(r"[A-Za-z0-9-]{1,40}", k) or not _WERT_RE.fullmatch(v):
                raise HttpFehler("Kopfzeile ungültig")
            zeilen.append(f"{k}: {v}")
        if rumpf or methode in ("POST", "PUT"):
            zeilen.append(f"Content-Length: {len(rumpf)}")
        writer.write(("\r\n".join(zeilen) + "\r\n\r\n").encode("latin-1") + rumpf)
        await writer.drain()
        status, antwortkopf, auth = await _lies_kopf(reader)
        daten = await _lies_rumpf(reader, antwortkopf, max_bytes, methode, status)
        return Antwort(status, antwortkopf, daten, auth)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass


async def anfrage(adresse: str, port: int, methode: str, pfad: str, *, kopf: dict[str, str] | None = None,
                  rumpf: bytes = b"", timeout: float = 5.0, max_bytes: int = 1024 * 1024,
                  tls: ssl.SSLContext | None = None, tls_sha256: str | None = None,
                  digest: tuple[str, str] | None = None) -> Antwort:
    """Eine Anfrage an adresse:port (IPv4 aus der Konfiguration). Wirft HttpFehler, nie mit Gerätedaten im Text."""
    _pruefe_ziel(adresse, port)
    if methode not in ("GET", "POST", "PUT", "DELETE", "HEAD") or not _PFAD_RE.fullmatch(pfad):
        raise HttpFehler("Anfrage ungültig")
    kopf = dict(kopf or {})
    try:
        async with asyncio.timeout(timeout):
            antwort = await _einmal(adresse, port, methode, pfad, kopf, rumpf, max_bytes, tls, tls_sha256)
            if antwort.status == 401 and digest:
                for challenge in antwort.authentifizieren:
                    if (wert := _digest_kopf(challenge, methode, pfad, *digest)) is not None:
                        kopf["Authorization"] = wert
                        return await _einmal(adresse, port, methode, pfad, kopf, rumpf, max_bytes, tls, tls_sha256)
            return antwort
    except HttpFehler:
        raise
    except TimeoutError as e:
        raise HttpFehler("Gerät antwortet nicht rechtzeitig") from e
    except (OSError, ssl.SSLError, asyncio.IncompleteReadError, UnicodeError) as e:
        raise HttpFehler(f"Gerät nicht erreichbar ({type(e).__name__})") from e


def tls_ohne_ca() -> ssl.SSLContext:
    """TLS-Kontext für Geräte mit eigenem Zertifikat: Kette und Name werden nicht geprüft, stattdessen der
    festgehaltene SHA-256-Fingerabdruck (tls_sha256) in anfrage()."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
