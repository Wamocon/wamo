#!/usr/bin/env python3
"""Attrappe der Welt für wamo_labor_rauchtest.py (Lauf ohne Server und Tests): Im guten Zustand antwortet sie wie der fertig eingerichtete Server,
mit eingebauten Fehlern (--fehler NAME,...) wie ein kaputter. Die Datei liegt neben dem Rauchtest und wird nur bei --attrappe und in den Tests geladen,
nie am Server im Betrieb. Nur Standardbibliothek."""
from __future__ import annotations

import json
import re
import string

from wamo_labor_rauchtest import (CADDY, CODE_SCHREIBEN, CODE_UID, CONTAINER, CORE_ENV, DATEN, IP_CADDY_LABOR, IP_DIENST, IP_LOKAL, KERN_URL, NETZ_CADDY,
                                  NETZ_LABOR, NETZ_LOKAL, PORT_LOOPBACK, PORT_SITE, Antwort, Welt)

ALPHABET = string.ascii_letters + string.digits  # daraus entstehen die Wegwerf-Token der Attrappe, kein Literal


def _text(status: int) -> Antwort:
    return Antwort(status, {"content-type": "text/plain; charset=utf-8", "x-frame-options": "DENY", "cache-control": "no-store",
                            "x-content-type-options": "nosniff", "referrer-policy": "no-referrer"}, b"Nicht gefunden.")


def _json(status: int, daten: dict) -> Antwort:
    return Antwort(status, {"content-type": "application/json", "cache-control": "no-store"}, json.dumps(daten).encode())


def _token(a: int, b: int) -> str:
    return "".join(ALPHABET[(i * a + b) % 62] for i in range(43))


class Attrappe(Welt):
    """Eine Welt im Speicher: im guten Zustand antwortet sie wie der fertig eingerichtete Server, mit eingebauten Fehlern wie ein kaputter."""

    BEKANNTE_FEHLER = frozenset(
        "ungesund root schreibbar caps privilegiert ohne_nnp ohne_grenzen ohne_loggrenze netz_extern falscher_proxy port_offen port_zusatz "
        "token_modus token_besitzer daten_modus token_gleich token_kurz ca_falsch koepfe_fehlen kern_offen kern_variante_offen betreuer_offen "
        "betreuer_gegenprobe gesundheit_offen ausgang_internet ausgang_dns ausgang_kontrolle firewall_fehlt firewall_offen roundtrip_kaputt "
        "roundtrip_ohne_token env_url env_token token_in_env ende_falsch arch_falsch labin_fehlt loopback_regel_fehlt fremd_dienst_offen "
        "fremd_ip_offen fremd_kontrolle bridge_host_offen bridge_kontrolle".split())

    def __init__(self, fehler=()) -> None:
        super().__init__(None)
        self.fehler = set(fehler)
        unbekannt = self.fehler - self.BEKANNTE_FEHLER
        if unbekannt:
            raise ValueError("unbekannter Fehler: " + ", ".join(sorted(unbekannt)))
        self.kern, self.betreuer = _token(7, 3), _token(11, 5)
        self.anfragen: list[dict] = []

    def _dienst(self) -> dict:
        f = self.fehler
        env = ["WAMO_LABOR_DIENST_BIS=" + ("2027-01-01" if "ende_falsch" in f else "2026-12-11"),
               "WAMO_LABOR_DIENST_PROXYS=" + ("172.28.0.99" if "falscher_proxy" in f else IP_CADDY_LABOR),
               "WAMO_LABOR_DIENST_KERN_TOKEN_DATEI=/run/wamo-labor/kern-token"] + (["GEHEIM=" + self.kern] if "token_in_env" in f else [])
        host = {"ReadonlyRootfs": "schreibbar" not in f, "CapDrop": None if "caps" in f else ["ALL"], "CapAdd": None, "Privileged": "privilegiert" in f,
                "SecurityOpt": [] if "ohne_nnp" in f else ["no-new-privileges:true"], "Memory": 0 if "ohne_grenzen" in f else 268435456,
                "MemorySwap": 0 if "ohne_grenzen" in f else 268435456, "NanoCpus": 500000000, "PidsLimit": 128,
                "LogConfig": {"Type": "json-file", "Config": {} if "ohne_loggrenze" in f else {"max-size": "10m", "max-file": "3"}},
                "PortBindings": {"8080/tcp": [{"HostIp": "0.0.0.0" if "port_offen" in f else "127.0.0.1", "HostPort": str(PORT_LOOPBACK)}]}}
        if "port_zusatz" in f:
            host["PortBindings"]["9999/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": "9999"}]
        netze = {NETZ_LOKAL: {"IPAddress": IP_LOKAL}} | ({} if "netz_extern" in f else {NETZ_LABOR: {"IPAddress": IP_DIENST}})
        return {"State": {"Running": True, "Health": {"Status": "unhealthy" if "ungesund" in f else "healthy"}}, "RestartCount": 0,
                "Config": {"User": "0:0" if "root" in f else "10001:10001", "Env": env}, "HostConfig": host, "NetworkSettings": {"Networks": netze}}

    def inspect(self, name: str) -> dict | None:
        if name == CADDY:
            return {"NetworkSettings": {"Networks": {NETZ_CADDY: {"IPAddress": "172.24.0.2"}, NETZ_LABOR: {"IPAddress": IP_CADDY_LABOR}}}}
        return self._dienst() if name == CONTAINER else None

    def netz(self, name: str) -> dict | None:
        return {"Internal": name == NETZ_LABOR and "netz_extern" not in self.fehler} if name in (NETZ_LABOR, NETZ_LOKAL) else None

    def im_dienst(self, code: str, *args: str) -> tuple[int, str]:
        f = self.fehler
        if code == CODE_UID:
            return 0, "0 0\n" if "root" in f else "10001 10001\n"
        if code == CODE_SCHREIBEN:
            return 0, "geschrieben\n" if "schreibbar" in f else "verweigert\n"
        return 0, f"CapEff:\t{'00000000a80425fb' if 'caps' in f else '0000000000000000'}\nNoNewPrivs:\t{0 if 'ohne_nnp' in f else 1}\n"

    def tcp_vom_dienst(self, host: str, port: int) -> str:
        f = self.fehler
        if host == IP_CADDY_LABOR:
            return "zu" if "ausgang_kontrolle" in f else "offen"
        return "offen" if "ausgang_internet" in f and host == "1.1.1.1" else "zu"

    def dns_vom_dienst(self, name: str) -> str:
        return "aufgeloest" if "ausgang_dns" in self.fehler else "nicht"

    def lauscht(self) -> list[str]:
        return [f"{'0.0.0.0' if 'port_offen' in self.fehler else '127.0.0.1'}:{PORT_LOOPBACK}", "0.0.0.0:55447", "0.0.0.0:22"]

    def tls_fehler(self) -> str | None:
        return "self-signed certificate in certificate chain" if "ca_falsch" in self.fehler else None

    def _seiten_koepfe(self) -> dict[str, str]:
        if "koepfe_fehlen" in self.fehler:
            return {"content-type": "text/html"}
        return {"content-type": "text/html", "x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "cache-control": "no-store",
                "content-security-policy": "default-src 'none'; frame-ancestors 'none'"}

    def _caddy(self, methode: str, pfad: str, herkunft: str) -> Antwort:
        f = self.fehler
        if methode not in ("GET", "HEAD", "POST"):
            return _text(405)
        if re.search(r"(?i)api/+(kern|%6bern)", pfad) or "/../kern" in pfad:
            if "kern_offen" in f or ("kern_variante_offen" in f and not pfad.startswith("/api/kern/")):
                return _json(401, {"fehler": "token_ungueltig"})
            return _text(404)
        if pfad == "/betreuer" or pfad.startswith("/api/betreuer/"):
            if (herkunft == "fremd" and "betreuer_offen" not in f) or (herkunft == "intern" and "betreuer_gegenprobe" in f):
                return _text(403)
            return _json(401, {"fehler": "token_ungueltig"})
        if pfad == "/gesundheit":
            return _text(403) if herkunft == "fremd" and "gesundheit_offen" not in f else _json(200, {"zustand": "ok"})
        return Antwort(200, self._seiten_koepfe(), b"<html></html>") if pfad in ("/", "/board") else _text(404)

    def _merke(self, ziel: str, methode: str, pfad: str, kopf: dict | None) -> None:
        self.anfragen.append({"ziel": ziel, "methode": methode, "pfad": pfad, "bearer": "Authorization" in (kopf or {})})

    def caddy(self, methode: str, pfad: str, kopf: dict[str, str] | None = None) -> Antwort | None:
        self._merke("caddy", methode, pfad, kopf)
        return self._caddy(methode, pfad, "intern")

    def fremd(self, methode: str, pfad: str) -> Antwort | None:
        self._merke("fremd", methode, pfad, None)
        return self._caddy(methode, pfad, "fremd")

    def loopback(self, methode: str, pfad: str, kopf: dict[str, str] | None = None) -> Antwort | None:
        self._merke("loopback", methode, pfad, kopf)
        f = self.fehler
        if pfad != "/api/kern/durchlauf":
            return _json(404, {"fehler": "nicht_erlaubt"})
        daten = {"durchlauf_id": "5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41aa", "offen": False, "einwilligung_version": None, "einwilligung_zeit": None}
        if "roundtrip_ohne_token" in f:
            return _json(200, daten)
        if (kopf or {}).get("Authorization") != "Bearer " + self.token("kern"):
            return _json(401, {"fehler": "token_ungueltig"})
        return _json(500, {"fehler": "dienst_fehler"}) if "roundtrip_kaputt" in f else _json(200, daten)

    def stat(self, pfad: str) -> tuple[int, int] | None:
        f = self.fehler
        if pfad.endswith("/kern-token"):
            return (0o644 if "token_modus" in f else 0o400), (0 if "token_besitzer" in f else 10001)
        if pfad.endswith("/betreuer-token"):
            return 0o400, 10001
        return ((0o755 if "daten_modus" in f else 0o700), 10001) if pfad == DATEN else None

    def token(self, art: str) -> str:
        if art == "kern":
            return "zukurz" if "token_kurz" in self.fehler else self.kern
        return self.kern if "token_gleich" in self.fehler else self.betreuer

    def datei(self, pfad: str) -> bytes | None:
        if pfad != CORE_ENV:
            return None
        url = "http://127.0.0.1:9" if "env_url" in self.fehler else KERN_URL
        token = _token(13, 1) if "env_token" in self.fehler else self.token("kern")
        return f"# Attrappe\nWAMO_LABOR_URL='{url}'\nWAMO_LABOR_TOKEN='{token}'\n".encode()

    def iptables(self, kette: str) -> list[str] | None:
        f = self.fehler
        if "firewall_fehlt" in f:
            return None
        if kette == "WAMO-FW":
            return (["-N WAMO-FW", f"-A WAMO-FW -p tcp -m conntrack --ctorigdstport {PORT_SITE} --ctdir ORIGINAL -j WAMO-FW-LABOR",
                     "-A WAMO-FW -s 172.28.1.0/29 -j WAMO-FW-LABAUS"]
                    + ([] if "loopback_regel_fehlt" in f else
                       [f"-A WAMO-FW -p tcp -m conntrack --ctorigdstport {PORT_LOOPBACK} --ctdir ORIGINAL ! -s 127.0.0.0/8 -j DROP"]))
        if kette == "INPUT":
            return ["-P INPUT DROP"] + ([] if "labin_fehlt" in f else ["-A INPUT -s 172.28.1.0/29 -j WAMO-FW-LABIN"])
        if kette == "WAMO-FW-LABIN":
            return None if "labin_fehlt" in f else ["-N WAMO-FW-LABIN", "-A WAMO-FW-LABIN -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN",
                                                    "-A WAMO-FW-LABIN -j DROP"]
        if kette == "WAMO-FW-LABOR":
            return ["-N WAMO-FW-LABOR", "-A WAMO-FW-LABOR -s 127.0.0.0/8 -j RETURN"] + (
                ["-A WAMO-FW-LABOR -j RETURN"] if "firewall_offen" in f else ["-A WAMO-FW-LABOR -s 192.168.178.0/24 -j RETURN"]) + [
                "-A WAMO-FW-LABOR -j DROP"]
        return ["-N WAMO-FW-LABAUS", "-A WAMO-FW-LABAUS -j DROP"]

    def caddy_ip(self) -> str | None:
        return "172.24.0.2"

    def tcp_fremd(self, netz: str, host: str, port: int) -> str:
        f = self.fehler
        if netz == NETZ_CADDY:
            if host == "172.24.0.2":
                return "zu" if "fremd_kontrolle" in f else "offen"
            return "offen" if (host == IP_DIENST and "fremd_dienst_offen" in f) or (host == IP_LOKAL and "fremd_ip_offen" in f) else "zu"
        if host == IP_LOKAL:
            return "zu" if "bridge_kontrolle" in f else "offen"
        return "offen" if host == "172.28.1.1" and "bridge_host_offen" in f else "zu"

    def host_arch(self) -> str:
        return "arm64"

    def image_arch(self) -> str:
        return "amd64" if "arch_falsch" in self.fehler else "arm64"
