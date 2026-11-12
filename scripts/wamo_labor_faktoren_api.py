"""Verbindung zu GoTrue (Anmeldedienst des WAMO-Supabase) für scripts/wamo_labor_faktoren.py (nur Standardbibliothek).

Der Vorgang ist derselbe wie in der Oberfläche (ui/src/components/LoginGate.tsx): Anmeldung mit Passwort ergibt aal1, einen
TOTP-Faktor anlegen, mit dem Code bestätigen (challenge und verify), danach trägt das Token aal2.

Sicherheit der Verbindung: Nur https mit der CA-Datei (Zertifikat und Hostname werden geprüft) oder http auf Loopback für Tests.
Keine Zugangsdaten in der Adresse. Keine Umleitung wird befolgt, und die Umgebung nennt keinen Proxy (HTTP_PROXY und Verwandte
werden ignoriert), damit Passwörter und Token nie über einen fremden Weg gehen. Beides gilt von selbst, weil die Anfragen
direkt über http.client laufen. Je Anfrage gilt ein Gesamtlimit (60 Sekunden) für Senden, Antwortkopf und Antworttext zusammen:
Vor jedem Lesen und Senden wird die Wartezeit auf den Rest des Limits gekürzt, so zählt auch eine Antwort, die der Server
Byte für Byte langsam liefert (das Zeitlimit je Vorgang allein würde sie nie abbrechen).

Fehlertexte nennen nur die Handlung, die Ursache in Worten, den HTTP-Status und eine kurze Kennung des Servers (error_code), nie
den Text der Antwort und nie einen Wert der Anfrage. Die Tokens und der Seed stehen nicht in der Darstellung der Objekte (repr).
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import io
import socket
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from wamo_labor_faktoren_totp import jwt_claims, seed_gueltig, seed_normalisieren, zeitversatz

STANDARD_BASIS = "https://192.168.178.136:55443"
ANTWORT_GRENZE = 1_000_000
ZEITLIMIT_S = 30.0  # je einzelner Vorgang (Verbindung, Lesen)
GESAMTLIMIT_S = 60.0  # je Anfrage insgesamt
KENNUNG_FORM = re.compile(r"[A-Za-z0-9_]{1,40}")
FAKTOR_ID_FORM = re.compile(r"[A-Za-z0-9-]{8,64}")


class ApiFehler(Exception):
    """Ein Fehler der Verbindung oder des Servers, als Text ohne Geheimnisse."""

    def __init__(self, grund: str, status: int = 0, kennung: str = ""):
        zusatz = f" (HTTP {status}{' ' + kennung if kennung else ''})" if status else ""
        super().__init__(grund + zusatz)
        self.grund, self.status, self.kennung = grund, status, kennung


@dataclass(frozen=True)
class Faktor:
    id: str
    typ: str
    status: str
    name: str = ""


@dataclass(frozen=True)
class Sitzung:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)

    @property
    def aal(self) -> str:
        wert = jwt_claims(self.access_token).get("aal")
        return wert if isinstance(wert, str) else ""


@dataclass(frozen=True)
class NeuerFaktor:
    id: str
    seed: str = field(repr=False)


@dataclass(frozen=True)
class Antwort:
    status: int
    daten: object = field(repr=False)
    kopf_date: str | None = None


def ist_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def basis_pruefen(basis: str, hat_ca: bool) -> None:
    """https nur mit CA-Datei, http nur auf Loopback (Tests). Keine Zugangsdaten, kein Pfad, keine Abfrage in der Adresse."""
    try:
        teile = urllib.parse.urlsplit(basis)
        host = teile.hostname
        _ = teile.port
    except ValueError:
        raise ApiFehler("Die Adresse des Anmeldeservers ist ungültig") from None
    if teile.scheme not in ("http", "https") or not host:
        raise ApiFehler("Die Adresse des Anmeldeservers muss mit https:// beginnen und einen Rechner nennen")
    if teile.username is not None or teile.password is not None or teile.path not in ("", "/") or teile.query or teile.fragment:
        raise ApiFehler("Die Adresse des Anmeldeservers darf nur Schema, Rechner und Port enthalten (keine Zugangsdaten, kein Pfad)")
    if teile.scheme == "http" and not ist_loopback(host):
        raise ApiFehler("Die Adresse des Anmeldeservers ist nicht lokal: Dort ist nur https mit CA-Datei erlaubt, damit Passwörter "
                        "nie im Klartext übertragen werden")
    if teile.scheme == "https" and not hat_ca:
        raise ApiFehler("Für https fehlt die CA-Datei (--ca oder WAMO_CA_FILE, im Repo infra/sokrates2/wamo-ca.crt)")


def tls_kontext(ca_datei: Path | str) -> ssl.SSLContext:
    """Prüft Zertifikat und Hostname gegen genau diese CA-Datei (keine Systemzertifikate)."""
    try:
        kontext = ssl.create_default_context(cafile=str(ca_datei))
    except (OSError, ssl.SSLError):
        raise ApiFehler("Die CA-Datei ist nicht lesbar oder enthält kein Zertifikat") from None
    kontext.verify_mode = ssl.CERT_REQUIRED
    kontext.check_hostname = True
    return kontext


def _kennung(daten: object) -> str:
    if isinstance(daten, dict):
        for schluessel in ("error_code", "error"):
            wert = daten.get(schluessel)
            if isinstance(wert, str) and KENNUNG_FORM.fullmatch(wert):
                return wert
    return ""


def _fehler_fuer(aktion: str, status: int, daten: object) -> ApiFehler:
    if 300 <= status < 400:
        return ApiFehler(f"{aktion}: Umleitung nicht befolgt (der Server soll nicht auf eine andere Adresse verweisen)", status)
    art = "Ratenbegrenzung des Servers" if status == 429 else "Serverfehler" if status >= 500 else "abgelehnt"
    return ApiFehler(f"{aktion}: {art}", status, _kennung(daten))


def _json(roh: bytes) -> object:
    if not roh:
        return None
    try:
        return json.loads(roh)
    except ValueError:
        return None


class _Gesamtfrist(TimeoutError):
    """Das Gesamtlimit der Anfrage ist erreicht."""


def _rest(ablauf: float, einzel: float) -> float:
    rest = ablauf - time.monotonic()
    if rest <= 0:
        raise _Gesamtfrist("Gesamtlimit je Anfrage")
    return min(einzel, rest)


class _FristLesen(io.RawIOBase):
    def __init__(self, sock: socket.socket, ablauf: float, einzel: float):
        self._sock, self._ablauf, self._einzel = sock, ablauf, einzel

    def readable(self) -> bool:
        return True

    def readinto(self, puffer) -> int:
        self._sock.settimeout(_rest(self._ablauf, self._einzel))
        return self._sock.recv_into(puffer)


class _FristSocket:
    """Ein Anschluss, dessen Senden und Lesen zusammen nie länger als bis zum Ablauf dauert (für http.client)."""

    def __init__(self, sock: socket.socket, ablauf: float, einzel: float):
        self._sock, self._ablauf, self._einzel = sock, ablauf, einzel

    def sendall(self, daten) -> None:
        self._sock.settimeout(_rest(self._ablauf, self._einzel))
        self._sock.sendall(daten)

    def makefile(self, *args, **kwargs) -> io.BufferedReader:
        return io.BufferedReader(_FristLesen(self._sock, self._ablauf, self._einzel))

    def close(self) -> None:
        self._sock.close()

    def __getattr__(self, name: str):
        return getattr(self._sock, name)


class _FristVerbindung:
    """Mischt das Gesamtlimit in HTTPConnection und HTTPSConnection: Nach dem Aufbau (bei https samt TLS) wird der Anschluss umhüllt."""
    ablauf: float
    einzel: float

    def connect(self) -> None:
        super().connect()
        self.sock = _FristSocket(self.sock, self.ablauf, self.einzel)


class _FristHttp(_FristVerbindung, http.client.HTTPConnection):
    pass


class _FristHttps(_FristVerbindung, http.client.HTTPSConnection):
    pass


class Verbindung:
    """Ein Anschluss an GoTrue. versatz ist Server minus lokale Uhr (aus dem Kopf Date der letzten Antwort)."""

    def __init__(self, basis: str, apikey: str, ca_datei: Path | str | None = None, *, zeit: Callable[[], float] = time.time,
                 zeitlimit: float = ZEITLIMIT_S, gesamtlimit: float = GESAMTLIMIT_S):
        basis_pruefen(basis, ca_datei is not None)
        teile = urllib.parse.urlsplit(basis)
        self.basis = basis.rstrip("/")
        self._https = teile.scheme == "https"
        self._host, self._port = teile.hostname or "", teile.port
        self._kontext = tls_kontext(ca_datei) if self._https and ca_datei is not None else None
        self._apikey = apikey
        self._zeit = zeit
        self._zeitlimit = zeitlimit
        self._gesamtlimit = gesamtlimit
        self.versatz = 0.0

    def jetzt(self) -> float:
        """Die Zeit des Servers: lokale Uhr plus gemessener Versatz."""
        return self._zeit() + self.versatz

    def _neue_verbindung(self) -> http.client.HTTPConnection:
        timeout = min(self._zeitlimit, self._gesamtlimit)
        if self._https:
            verbindung = _FristHttps(self._host, self._port, timeout=timeout, context=self._kontext)
        else:
            verbindung = _FristHttp(self._host, self._port, timeout=timeout)
        verbindung.ablauf, verbindung.einzel = time.monotonic() + self._gesamtlimit, self._zeitlimit
        return verbindung

    def anfrage(self, methode: str, pfad: str, daten: dict | None = None, bearer: str | None = None, aktion: str = "Anfrage") -> Antwort:
        kopf = {"apikey": self._apikey, "Authorization": "Bearer " + (bearer or self._apikey), "Accept": "application/json",
                "User-Agent": "wamo-labor-faktoren"}
        if daten is not None:
            kopf["Content-Type"] = "application/json"
        verbindung = self._neue_verbindung()
        beginn = self._zeit()
        try:
            verbindung.request(methode, pfad, body=None if daten is None else json.dumps(daten).encode(), headers=kopf)
            antwort = verbindung.getresponse()
            status, roh, datum = antwort.status, antwort.read(ANTWORT_GRENZE), antwort.getheader("Date")
        except _Gesamtfrist:
            raise ApiFehler(f"{aktion}: Zeitüberschreitung (Gesamtlimit je Anfrage)") from None
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise ApiFehler(f"{aktion}: {self._netzfehler(e)}") from None
        finally:
            verbindung.close()
        self._versatz_merken(datum, beginn)
        if status >= 300:
            raise _fehler_fuer(aktion, status, _json(roh))
        return Antwort(status, _json(roh), datum)

    @staticmethod
    def _netzfehler(e: BaseException) -> str:
        if isinstance(e, ssl.SSLError):  # vor ValueError: SSLCertVerificationError ist auch ein ValueError
            return "TLS-Prüfung fehlgeschlagen (Zertifikat oder Adresse stimmt nicht zur CA-Datei)"
        if isinstance(e, (socket.timeout, TimeoutError)):
            return "Zeitüberschreitung"
        if isinstance(e, ValueError):
            return "ungültige Anfrage"
        return "keine Verbindung zum Anmeldeserver"

    def _versatz_merken(self, datum: str | None, beginn: float) -> None:
        if datum:
            self.versatz = zeitversatz(datum, (beginn + self._zeit()) / 2)

    # ---- die Aufrufe von GoTrue ----

    def anmelden(self, email: str, passwort: str) -> Sitzung:
        a = self.anfrage("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": passwort}, aktion="Anmeldung")
        return _sitzung(a.daten, "Anmeldung")

    def erneuern(self, refresh_token: str) -> Sitzung:
        a = self.anfrage("POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": refresh_token}, aktion="Token erneuern")
        return _sitzung(a.daten, "Token erneuern")

    def faktoren(self, token: str) -> tuple[Faktor, ...]:
        a = self.anfrage("GET", "/auth/v1/user", bearer=token, aktion="Faktoren lesen")
        return _faktoren(a.daten if isinstance(a.daten, dict) else {})

    def faktor_anlegen(self, token: str, name: str) -> NeuerFaktor:
        a = self.anfrage("POST", "/auth/v1/factors", {"factor_type": "totp", "friendly_name": name, "issuer": "WAMO"}, token,
                         aktion="Faktor anlegen")
        d = a.daten if isinstance(a.daten, dict) else {}
        totp = d.get("totp") if isinstance(d.get("totp"), dict) else {}
        fid, seed = d.get("id"), totp.get("secret")
        if not isinstance(fid, str) or not FAKTOR_ID_FORM.fullmatch(fid) or not seed_gueltig(seed):
            raise ApiFehler("Faktor anlegen: ungültige Antwort des Servers")
        return NeuerFaktor(fid, seed_normalisieren(seed))  # ohne Leerraum, in Großbuchstaben: so steht er später in der Datei

    def challenge(self, token: str, faktor_id: str) -> str:
        a = self.anfrage("POST", f"/auth/v1/factors/{_id(faktor_id)}/challenge", {}, token, aktion="challenge")
        cid = a.daten.get("id") if isinstance(a.daten, dict) else None
        if not isinstance(cid, str) or not cid:
            raise ApiFehler("challenge: ungültige Antwort des Servers")
        return cid

    def verify(self, token: str, faktor_id: str, challenge_id: str, code: str) -> Sitzung:
        a = self.anfrage("POST", f"/auth/v1/factors/{_id(faktor_id)}/verify", {"challenge_id": challenge_id, "code": code}, token,
                         aktion="Code bestätigen")
        return _sitzung(a.daten, "Code bestätigen")

    def faktor_loeschen(self, token: str, faktor_id: str) -> None:
        self.anfrage("DELETE", f"/auth/v1/factors/{_id(faktor_id)}", bearer=token, aktion="Faktor löschen")

    def abmelden(self, token: str) -> None:
        self.anfrage("POST", "/auth/v1/logout?scope=local", {}, token, aktion="Abmeldung")


def _id(faktor_id: str) -> str:
    if not isinstance(faktor_id, str) or not FAKTOR_ID_FORM.fullmatch(faktor_id):
        raise ApiFehler("Ungültige Faktorkennung")
    return urllib.parse.quote(faktor_id, safe="")


def _faktoren(user: dict) -> tuple[Faktor, ...]:
    liste = user.get("factors")
    if not isinstance(liste, list):
        return ()
    ergebnis = []
    for f in liste:
        if isinstance(f, dict) and isinstance(f.get("id"), str):
            ergebnis.append(Faktor(f["id"], str(f.get("factor_type", "")), str(f.get("status", "")), str(f.get("friendly_name", ""))))
    return tuple(ergebnis)


def _sitzung(daten: object, aktion: str) -> Sitzung:
    d = daten if isinstance(daten, dict) else {}
    zugriff, erneuern = d.get("access_token"), d.get("refresh_token")
    if not isinstance(zugriff, str) or not zugriff or not isinstance(erneuern, str) or not erneuern:
        raise ApiFehler(f"{aktion}: ungültige Antwort des Servers")
    return Sitzung(zugriff, erneuern)
