"""Konfiguration des Gerätedienstes.

  /etc/wamo-geraete/geraete.toml   Geräte mit Kennung, Klasse, Treiber, fester IPv4-Adresse und Treiberoptionen
                                   (root:wamo-geraete 0640, Pflege durch den Admin; Vorlage im Repo:
                                   infra/sokrates2/geraete.toml.example)
  /etc/wamo-geraete/geraete.env            Zugangsdaten (root:wamo-geraete 0640, nur über sudo wamo-geraete-env setzen)

Regeln:
  * Adressen nur als IPv4 im Büro-LAN 192.168.178.0/24, nie Namen (fritz.box löst auf Sokrates-2 ins Internet auf).
  * Ports und Pfade sind je Treiber fest oder aus einer kleinen Liste; was nicht passt, verhindert den Start.
  * Unbekannte Schlüssel verhindern den Start (Tippfehler sollen auffallen, nicht still wirkungslos bleiben).
  * Kennungen wie im Register (devices.kennung): ^[a-z][a-z0-9-]{1,21}[a-z0-9]$ ohne "--".
"""

from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

KONFIG_DATEI = os.getenv("WAMO_GERAETE_KONFIG") or "/etc/wamo-geraete/geraete.toml"
ENV_DATEI = os.getenv("WAMO_GERAETE_ENV") or "/etc/wamo-geraete/geraete.env"
BUERO_NETZ = ipaddress.ip_network("192.168.178.0/24")
SOKRATES2 = "192.168.178.136"
KENNUNG_RE = re.compile(r"^[a-z][a-z0-9-]{1,21}[a-z0-9]$")
ZUGANG_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
DIENST_RE = re.compile(r"^[a-z0-9][a-z0-9@._-]{0,60}\.service$")
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
MDNS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}\.local$")
FREIGABE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._$-]{0,79}$")
ORDNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
SHA256_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){31}$")

KLASSEN = ("server", "drucker", "scanner", "router", "nas", "fernseher", "lautsprecher", "steckdose", "mac")

# Treiber: erlaubte Klassen, Standardport, erlaubte Ports, erlaubte Zusatzschlüssel
TREIBER: dict[str, dict[str, Any]] = {
    "server-lokal": {"klassen": ("server",), "port": None, "ports": (), "optionen": {"dienste"}},
    "server-ssh": {"klassen": ("server",), "port": 22, "ports": (22,), "optionen": {"litellm_port", "hostschluessel"}},
    "ipp": {"klassen": ("drucker",), "port": 631, "ports": (631,), "optionen": {"format", "farbe_erlaubt", "aufloesung"}},
    "escl": {"klassen": ("scanner",), "port": 80, "ports": (80,), "optionen": set()},
    "upnp-igd": {"klassen": ("router",), "port": 49000, "ports": (49000,),
                 "optionen": {"tr064_port", "tls_sha256", "zugang"}},
    "upnp-renderer": {"klassen": ("lautsprecher",), "port": 49152, "ports": (49152, 49153, 49154, 9197),
                      "optionen": {"steuer_pfad", "transport_pfad", "max_lautstaerke"}},
    "shelly": {"klassen": ("steckdose",), "port": 80, "ports": (80,), "optionen": {"zugang", "schutz", "max_laufzeit_s"}},
    "samsung-tv": {"klassen": ("fernseher",), "port": 8001, "ports": (8001,),
                   "optionen": {"mac", "max_lautstaerke", "eingaenge"}},
    "lg-webos": {"klassen": ("fernseher",), "port": 3001, "ports": (3001,),
                 "optionen": {"mac", "max_lautstaerke", "eingaenge"}},
    "smb": {"klassen": ("nas",), "port": 445, "ports": (445,), "optionen": {"zugang", "freigaben", "scanziel"}},
    "anwesenheit": {"klassen": ("mac", "fernseher", "server"), "port": None, "ports": (),
                    "optionen": {"mdns_name", "mac"}},
}
UPNP_PFAD_RE = re.compile(r"^/upnp/control/[A-Za-z0-9_]{1,40}$")
EINGAENGE = ("tv", "hdmi1", "hdmi2", "hdmi3", "hdmi4")
ALLGEMEIN = {"kennung", "name", "klasse", "treiber", "adresse", "port", "beschreibung"}


class KonfigFehler(ValueError):
    """geraete.toml oder geraete.env ist unbrauchbar (der Dienst startet dann nicht)."""


@dataclass(frozen=True)
class Geraet:
    kennung: str
    name: str
    klasse: str
    treiber: str
    adresse: str | None
    port: int | None
    optionen: dict[str, Any] = field(default_factory=dict)

    def opt(self, name: str, standard: Any = None) -> Any:
        return self.optionen.get(name, standard)


@dataclass
class Konfig:
    geraete: dict[str, Geraet]
    geheimnisse: dict[str, str] = field(default_factory=dict)
    scan_tage: int = 30
    abruf_stunden: int = 24
    druck_seiten_tag: int = 50
    druck_seiten_gesamt_tag: int = 200

    def geheimnis(self, zugang: str | None, teil: str) -> str | None:
        """Wert WAMO_GERAETE_<ZUGANG>_<TEIL> aus geraete.env (TEIL: BENUTZER, PASSWORT) oder None."""
        if not zugang:
            return None
        wert = self.geheimnisse.get(f"WAMO_GERAETE_{zugang}_{teil}")
        return wert or None

    def adressen(self) -> list[str]:
        """Alle Zieladressen (für IPAddressAllow und die nft-Tabelle)."""
        return sorted({g.adresse for g in self.geraete.values() if g.adresse}, key=ipaddress.ip_address)

    def ziele(self) -> list[tuple[str, str, int]]:
        """(Protokoll, Adresse, Port) für die nft-Tabelle. Nur was die Treiber wirklich brauchen."""
        ziele: set[tuple[str, str, int]] = set()
        for g in self.geraete.values():
            if not g.adresse:
                continue
            if g.port:
                ziele.add(("tcp", g.adresse, g.port))
            if g.treiber == "server-ssh" and g.opt("litellm_port"):
                ziele.add(("tcp", g.adresse, int(g.opt("litellm_port"))))
            if g.treiber == "upnp-igd" and g.opt("tr064_port"):
                ziele.add(("tcp", g.adresse, int(g.opt("tr064_port"))))
            if g.treiber == "samsung-tv":
                ziele.update({("tcp", g.adresse, 8002), ("tcp", g.adresse, 9197)})
            if g.treiber in ("samsung-tv", "lg-webos") and g.opt("mac"):
                ziele.add(("udp", "192.168.178.255", 9))  # Wake-on-LAN
        return sorted(ziele, key=lambda z: (z[0], ipaddress.ip_address(z[1]), z[2]))


def _adresse(wert: object, kennung: str) -> str | None:
    if wert in (None, ""):
        return None
    if not isinstance(wert, str):
        raise KonfigFehler(f"{kennung}: adresse muss Text sein")
    try:
        ip = ipaddress.ip_address(wert)
    except ValueError as e:
        raise KonfigFehler(f"{kennung}: adresse {wert!r} ist keine IPv4-Adresse (keine Namen)") from e
    if ip.version != 4 or ip not in BUERO_NETZ or ip in (BUERO_NETZ.network_address, BUERO_NETZ.broadcast_address):
        raise KonfigFehler(f"{kennung}: adresse {wert} liegt nicht im Büro-LAN 192.168.178.0/24")
    return str(ip)


def _ganz(wert: object, was: str, lo: int, hi: int) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or not lo <= wert <= hi:
        raise KonfigFehler(f"{was} muss eine Ganzzahl von {lo} bis {hi} sein")
    return wert


def _optionen(treiber: str, kennung: str, roh: dict[str, Any]) -> dict[str, Any]:
    erlaubt = TREIBER[treiber]["optionen"]
    fremd = set(roh) - erlaubt
    if fremd:
        raise KonfigFehler(f"{kennung}: unbekannte Schlüssel {sorted(fremd)} für Treiber {treiber}")
    o: dict[str, Any] = {}
    for k, v in roh.items():
        was = f"{kennung}.{k}"
        if k == "dienste":
            if not isinstance(v, list) or len(v) > 20 or not all(isinstance(d, str) and DIENST_RE.fullmatch(d) for d in v):
                raise KonfigFehler(f"{was}: Liste von höchstens 20 Units name.service")
            o[k] = tuple(v)
        elif k in ("litellm_port", "tr064_port"):
            o[k] = _ganz(v, was, 1, 65535)
            if k == "tr064_port" and o[k] != 49443:
                raise KonfigFehler(f"{was}: TR-064 nur über HTTPS 49443")
        elif k == "hostschluessel":
            if not isinstance(v, str) or not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]{68}", v):
                raise KonfigFehler(f"{was}: ssh-ed25519 <base64>")
            o[k] = v
        elif k == "format":
            if v not in ("pwg", "jpeg"):
                raise KonfigFehler(f"{was}: pwg oder jpeg")
            o[k] = v
        elif k in ("farbe_erlaubt", "schutz"):
            if not isinstance(v, bool):
                raise KonfigFehler(f"{was}: true oder false")
            o[k] = v
        elif k == "max_laufzeit_s":
            o[k] = _ganz(v, was, 10, 86400)
        elif k == "aufloesung":
            if v not in (300, 600):
                raise KonfigFehler(f"{was}: 300 oder 600")
            o[k] = v
        elif k == "tls_sha256":
            if not isinstance(v, str) or not SHA256_RE.fullmatch(v.upper()):
                raise KonfigFehler(f"{was}: SHA-256-Fingerabdruck AA:BB:...")
            o[k] = v.upper()
        elif k == "zugang":
            if not isinstance(v, str) or not ZUGANG_RE.fullmatch(v):
                raise KonfigFehler(f"{was}: Name A-Z0-9 (z. B. FRITZBOX)")
            o[k] = v
        elif k in ("steuer_pfad", "transport_pfad"):
            if not isinstance(v, str) or not UPNP_PFAD_RE.fullmatch(v):
                raise KonfigFehler(f"{was}: /upnp/control/<name>")
            o[k] = v
        elif k == "max_lautstaerke":
            o[k] = _ganz(v, was, 1, 100)
        elif k == "mac":
            if not isinstance(v, str) or not MAC_RE.fullmatch(v.lower()):
                raise KonfigFehler(f"{was}: aa:bb:cc:dd:ee:ff")
            o[k] = v.lower()
        elif k == "eingaenge":
            if not isinstance(v, list) or not v or not all(e in EINGAENGE for e in v):
                raise KonfigFehler(f"{was}: Liste aus {EINGAENGE}")
            o[k] = tuple(dict.fromkeys(v))
        elif k == "freigaben":
            if not isinstance(v, list) or not 1 <= len(v) <= 10 or not all(isinstance(f, str) and FREIGABE_RE.fullmatch(f) for f in v):
                raise KonfigFehler(f"{was}: Liste von 1 bis 10 Freigabenamen")
            o[k] = tuple(v)
        elif k == "scanziel":
            if not isinstance(v, dict) or set(v) != {"freigabe", "ordner"} \
                    or not FREIGABE_RE.fullmatch(str(v["freigabe"])) or not ORDNER_RE.fullmatch(str(v["ordner"])):
                raise KonfigFehler(f"{was}: {{ freigabe = \"...\", ordner = \"...\" }}")
            o[k] = {"freigabe": v["freigabe"], "ordner": v["ordner"]}
        elif k == "mdns_name":
            if not isinstance(v, str) or not MDNS_RE.fullmatch(v):
                raise KonfigFehler(f"{was}: name.local")
            o[k] = v
    return o


def geraet_aus(roh: dict[str, Any]) -> Geraet:
    if not isinstance(roh, dict):
        raise KonfigFehler("[[geraet]] muss eine Tabelle sein")
    kennung = roh.get("kennung")
    if not isinstance(kennung, str) or not KENNUNG_RE.fullmatch(kennung) or "--" in kennung:
        raise KonfigFehler(f"Kennung {kennung!r} ungültig (wie devices.kennung)")
    treiber, klasse = roh.get("treiber"), roh.get("klasse")
    if treiber not in TREIBER:
        raise KonfigFehler(f"{kennung}: unbekannter Treiber {treiber!r}")
    if klasse not in TREIBER[treiber]["klassen"]:
        raise KonfigFehler(f"{kennung}: Klasse {klasse!r} passt nicht zum Treiber {treiber}")
    name = roh.get("name", kennung)
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9ÄÖÜäöüß !._()-]{1,60}", name):
        raise KonfigFehler(f"{kennung}: name höchstens 60 Zeichen, Buchstaben, Ziffern, Leerzeichen ! . _ - ( )")
    adresse = _adresse(roh.get("adresse"), kennung)
    if adresse == SOKRATES2 and treiber != "server-lokal":
        raise KonfigFehler(f"{kennung}: Sokrates-2 selbst nur mit server-lokal")
    if treiber == "server-lokal" and adresse not in (None, SOKRATES2):
        raise KonfigFehler(f"{kennung}: server-lokal hat keine fremde Adresse")
    # ohne Adresse startet der Dienst trotzdem; die Werkzeuge dieses Geräts melden dann "nicht eingerichtet"
    port = roh.get("port", TREIBER[treiber]["port"])
    if port is not None:
        port = _ganz(port, f"{kennung}.port", 1, 65535)
        if port not in TREIBER[treiber]["ports"]:
            raise KonfigFehler(f"{kennung}: Port {port} ist für {treiber} nicht vorgesehen")
    extra = {k: v for k, v in roh.items() if k not in ALLGEMEIN}
    return Geraet(kennung=kennung, name=name, klasse=klasse, treiber=treiber, adresse=adresse, port=port,
                  optionen=_optionen(treiber, kennung, extra))


def lade_env(pfad: str = ENV_DATEI) -> dict[str, str]:
    werte: dict[str, str] = {}
    try:
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.rstrip("\n")
                if not zeile or zeile.startswith("#") or "=" not in zeile:
                    continue
                k, v = zeile.split("=", 1)
                if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
                    v = v[1:-1]
                if re.fullmatch(r"WAMO_GERAETE_[A-Z0-9_]{1,60}", k):
                    werte[k] = v
    except FileNotFoundError:
        pass
    return werte


def lade(pfad: str = KONFIG_DATEI, env_pfad: str = ENV_DATEI) -> Konfig:
    try:
        with open(pfad, "rb") as f:
            roh = tomllib.load(f)
    except FileNotFoundError as e:
        raise KonfigFehler(f"{pfad} fehlt (Vorlage: infra/sokrates2/geraete.toml.example)") from e
    except tomllib.TOMLDecodeError as e:
        raise KonfigFehler(f"{pfad} ist kein gültiges TOML ({e})") from e
    return aus_daten(roh, lade_env(env_pfad))


def aus_daten(roh: dict[str, Any], geheimnisse: dict[str, str] | None = None) -> Konfig:
    fremd = set(roh) - {"dienst", "geraet"}
    if fremd:
        raise KonfigFehler(f"unbekannte Abschnitte {sorted(fremd)}")
    geraete: dict[str, Geraet] = {}
    for eintrag in roh.get("geraet", []) or []:
        g = geraet_aus(eintrag)
        if g.kennung in geraete:
            raise KonfigFehler(f"Kennung {g.kennung} doppelt")
        geraete[g.kennung] = g
    if len(geraete) > 64:
        raise KonfigFehler("höchstens 64 Geräte")
    d = roh.get("dienst", {}) or {}
    fremd = set(d) - {"scan_tage", "abruf_stunden", "druck_seiten_tag", "druck_seiten_gesamt_tag"}
    if fremd:
        raise KonfigFehler(f"[dienst]: unbekannte Schlüssel {sorted(fremd)}")
    return Konfig(
        geraete=geraete, geheimnisse=dict(geheimnisse or {}),
        scan_tage=_ganz(d.get("scan_tage", 30), "scan_tage", 1, 365),
        abruf_stunden=_ganz(d.get("abruf_stunden", 24), "abruf_stunden", 1, 168),
        druck_seiten_tag=_ganz(d.get("druck_seiten_tag", 50), "druck_seiten_tag", 1, 500),
        druck_seiten_gesamt_tag=_ganz(d.get("druck_seiten_gesamt_tag", 200), "druck_seiten_gesamt_tag", 1, 2000),
    )
