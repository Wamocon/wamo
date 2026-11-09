"""Hostliste der FRITZ!Box über TR-064 (Dienst Hosts:1), nur lesend und passiv: GetHostNumberOfEntries und
GetGenericHostEntry je Index. Kein nmap, kein Portscan, kein ARP-Sweep; der Dienst fragt nur den Router, was er ohnehin weiß.

Übernommen werden nur aktive Einträge. Alles, was die Box liefert (Gerätename, Schnittstelle, Adresse, MAC), ist Daten und
wird geprüft und gekürzt: Adressen nur als IPv4 im Büro-LAN, MAC nur als aa:bb:cc:dd:ee:ff, Namen über text.name().
Namen und MAC-Adressen sind personenbezogen: sie stehen weder in Meldungen noch im Audit noch im Protokoll (Host.__repr__
zeigt sie nicht), die Antworten an den Kern tragen sie nur für Admins und nur gekürzt (Host.oeffentlich()).

Last auf der Box: ein Index je Aufruf, höchstens GLEICHZEITIG zugleich, höchstens MAX_HOSTS Einträge; die Erkennung
(unbekannt.py) holt die Liste höchstens einmal in 60 Sekunden.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field

from ..konfig import BUERO_NETZ, MAC_RE, Geraet, Konfig
from ..text import ganzzahl, name
from . import GeraetFehler, upnp

MAX_HOSTS = 256     # so viele Indizes höchstens je Abruf (die Box merkt sich auch Geräte, die lange weg sind)
GLEICHZEITIG = 4    # gleichzeitige Anfragen an die Box
LESE_ZEIT = 20.0    # Obergrenze für den ganzen Abruf in Sekunden


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


def _ip(wert: str) -> str:
    try:
        ip = ipaddress.ip_address(wert)
    except ValueError:
        return ""
    return str(ip) if ip.version == 4 and ip in BUERO_NETZ else ""


def _host(werte: dict[str, str]) -> Host | None:
    """Host aus einer Antwort von GetGenericHostEntry; None für inaktive Einträge."""
    if werte.get("NewActive") != "1":
        return None
    mac = werte.get("NewMACAddress", "").lower()
    return Host(ip=_ip(werte.get("NewIPAddress", "")), mac=mac if MAC_RE.fullmatch(mac) else "",
                name=name(werte.get("NewHostName"), 63), schnittstelle=name(werte.get("NewInterfaceType"), 16))


_FEHLT = object()  # Marke: dieser Eintrag ließ sich nicht lesen (anders als inaktiv = None)


async def lese(geraet: Geraet, konfig: Konfig) -> list[Host]:
    """Alle aktiven Geräte der Box, ohne doppelte (gleiche MAC oder, ohne MAC, gleiche Adresse oder gleicher Name).
    Wirft NichtEingerichtet (Zugang fehlt oder abgelehnt) oder GeraetFehler (Box antwortet nicht brauchbar)."""
    port, pin, digest = upnp.tr064_zugang(geraet, konfig)

    async def frage(aktion: str, werte: dict[str, int | str] | None = None) -> dict[str, str]:
        return await upnp.aufruf(geraet, aktion, werte, port=port, tls_sha256=pin, digest=digest)

    sperre = asyncio.Semaphore(GLEICHZEITIG)

    async def eintrag(index: int) -> Host | None | object:
        async with sperre:
            try:
                return _host(await frage("tr064_host_eintrag", {"NewIndex": index}))
            except GeraetFehler:
                return _FEHLT  # ein einzelner Eintrag darf fehlen (die Liste kann sich gerade ändern)

    try:
        async with asyncio.timeout(LESE_ZEIT):
            anzahl = ganzzahl((await frage("tr064_host_anzahl")).get("NewHostNumberOfEntries"), 0, 10**6)
            if anzahl is None:
                raise GeraetFehler("Router meldet keine Anzahl der Hosts")
            aufgaben = [asyncio.create_task(eintrag(i)) for i in range(min(anzahl, MAX_HOSTS))]
            try:
                gelesen = await asyncio.gather(*aufgaben)
            except BaseException:
                for a in aufgaben:
                    a.cancel()
                raise
    except TimeoutError as e:
        raise GeraetFehler("Router hat die Hostliste nicht rechtzeitig geliefert") from e
    if gelesen and all(h is _FEHLT for h in gelesen):
        raise GeraetFehler("Router liefert die Einträge der Hostliste nicht")
    gesehen: set[str] = set()
    hosts: list[Host] = []
    for h in gelesen:
        if not isinstance(h, Host):
            continue
        schluessel = h.mac or h.ip or h.name
        if schluessel and schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        hosts.append(h)
    return hosts
