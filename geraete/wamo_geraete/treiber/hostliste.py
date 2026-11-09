"""Hostliste der FRITZ!Box über TR-064, nur lesend und passiv: der Pfad der Hostliste (SOAP X_AVM-DE_GetHostListPath) und die
Datei devicehostlist.lua mit allen Einträgen, zusammen zwei Anfragen. Der Weg und der Parser sind derselbe wie bei der
Geräteliste im Büro (upnp.hostliste_holen, seit dem 21.10. im Betrieb); die Datei trägt je Eintrag auch die MAC-Adresse. Kein
nmap, kein Portscan, kein ARP-Sweep; der Dienst fragt nur den Router, was er ohnehin weiß.

Übernommen werden nur aktive Einträge. Alles, was die Box liefert (Gerätename, Schnittstelle, Adresse, MAC), ist Daten und
wird geprüft und gekürzt: Adressen nur als IPv4 im Büro-LAN, MAC nur als aa:bb:cc:dd:ee:ff, Namen über text.name().
Namen und MAC-Adressen sind personenbezogen: sie stehen weder in Meldungen noch im Audit noch im Protokoll (Host.__repr__
zeigt sie nicht), die Antworten an den Kern tragen sie nur für Admins und nur gekürzt (Host.oeffentlich()).

Last auf der Box: zwei Anfragen je Messung, unabhängig von der Zahl der Geräte (früher eine je Eintrag, bis 257 mit TLS und
Digest); höchstens MAX_HOSTS Einträge, darüber meldet das Ergebnis abgeschnitten: true. Die Erkennung (unbekannt.py) holt die
Liste höchstens einmal in 60 Sekunden. Fehlt einem Eintrag die MAC, vergleicht die Erkennung weiter nach Adresse und Name.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field

from ..konfig import BUERO_NETZ, MAC_RE, Geraet, Konfig
from ..text import name
from . import GeraetFehler, upnp

MAX_HOSTS = 256     # so viele Einträge höchstens je Abruf (die Box merkt sich auch Geräte, die lange weg sind)
LESE_ZEIT = 20.0    # Obergrenze für den ganzen Abruf (zwei Anfragen) in Sekunden


@dataclass(frozen=True)
class Host:
    """Ein aktives Gerät aus der Hostliste. ip und mac sind leer, wenn die Box nichts Brauchbares liefert."""
    ip: str
    mac: str = field(repr=False)
    name: str = field(repr=False)
    schnittstelle: str

    def oeffentlich(self) -> dict:
        """Form für die Antwort an Admins: IP, MAC-Vorsatz (erste drei Byte), Name auf 24 Zeichen, Schnittstelle."""
        return {"ip": self.ip, "mac_vorsatz": self.mac[:8], "name": self.name[:24], "schnittstelle": self.schnittstelle}


@dataclass(frozen=True)
class Hostliste:
    """Die aktiven Geräte der Box ohne doppelte. abgeschnitten: Die Box meldet mehr als MAX_HOSTS Einträge, der Rest fehlt."""
    hosts: tuple[Host, ...]
    abgeschnitten: bool = False


def _ip(wert: str) -> str:
    try:
        ip = ipaddress.ip_address(wert)
    except ValueError:
        return ""
    return str(ip) if ip.version == 4 and ip in BUERO_NETZ else ""


def _host(felder: dict[str, str]) -> Host | None:
    """Host aus einem Eintrag der Hostliste (Felder wie upnp.HOST_FELDER); None für inaktive Einträge."""
    if felder.get("Active") != "1":
        return None
    mac = felder.get("MACAddress", "").lower()
    return Host(ip=_ip(felder.get("IPAddress", "")), mac=mac if MAC_RE.fullmatch(mac) else "",
                name=name(felder.get("HostName"), 63), schnittstelle=name(felder.get("InterfaceType"), 16))


async def lese(geraet: Geraet, konfig: Konfig) -> Hostliste:
    """Alle aktiven Geräte der Box, ohne doppelte (gleiche MAC oder, ohne MAC, gleiche Adresse oder gleicher Name).
    Wirft NichtEingerichtet (Zugang fehlt oder abgelehnt) oder GeraetFehler (Box antwortet nicht brauchbar)."""
    try:
        async with asyncio.timeout(LESE_ZEIT):
            eintraege, abgeschnitten = await upnp.hostliste_holen(geraet, konfig, MAX_HOSTS)
    except TimeoutError as e:
        raise GeraetFehler("Router hat die Hostliste nicht rechtzeitig geliefert") from e
    gesehen: set[str] = set()
    hosts: list[Host] = []
    for felder in eintraege:
        h = _host(felder)
        if h is None:
            continue
        schluessel = h.mac or h.ip or h.name
        if schluessel and schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        hosts.append(h)
    return Hostliste(tuple(hosts), abgeschnitten)
