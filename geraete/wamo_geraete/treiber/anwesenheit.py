"""Anwesenheit eines Geräts ohne Port-Scan (iMac, F10): mDNS-Name über avahi-resolve (Avahi-Daemon des Systems, über
D-Bus) oder MAC-Adresse in der ARP-Tabelle (/proc/net/arp). Es wird nichts gesendet außer der mDNS-Anfrage des
Avahi-Daemons; ohne Name und MAC meldet das Werkzeug "nicht eingerichtet"."""

from __future__ import annotations

import asyncio
import re

from ..konfig import Geraet
from . import NichtEingerichtet

AVAHI = "/usr/bin/avahi-resolve-host-name"


def _arp(mac: str) -> str | None:
    try:
        with open("/proc/net/arp", encoding="ascii") as f:
            next(f, None)
            for zeile in f:
                teile = zeile.split()
                if len(teile) >= 4 and teile[3].lower() == mac and teile[2] == "0x2":
                    return teile[0]
    except OSError:
        return None
    return None


async def status(geraet: Geraet) -> dict:
    mdns, mac = geraet.opt("mdns_name"), geraet.opt("mac")
    if not mdns and not mac:
        raise NichtEingerichtet("Weder mDNS-Name noch MAC-Adresse bekannt (Gerät war bei der Erkundung nicht sichtbar)")
    adresse = None
    if mdns:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(AVAHI, "-4", mdns, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL,
                                                        stdin=asyncio.subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"})
            async with asyncio.timeout(4):
                ausgabe = (await proc.stdout.read(512)).decode("ascii", "replace")
                await proc.wait()
            m = re.search(r"\s(192\.168\.178\.\d{1,3})\s*$", ausgabe)
            adresse = m.group(1) if m else None
        except (OSError, TimeoutError):
            if proc is not None and proc.returncode is None:
                proc.kill()
            adresse = None
    if adresse is None and mac:
        adresse = await asyncio.to_thread(_arp, mac)
    return {"erreichbar": adresse is not None, "sichtbar": adresse is not None, "adresse": adresse or ""}
