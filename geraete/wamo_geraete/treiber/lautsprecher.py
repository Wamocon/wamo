"""Lautsprecher über UPnP-RenderingControl (derzeit ist kein Lautsprecher in der Konfiguration eingetragen).

Status: Lautstärke, stumm, Wiedergabe. Steuern (change): Lautstärke setzen (höchstens max_lautstaerke, Standard 70,
damit eine eingeschleuste Anweisung den Raum nicht beschallt), lauter, leiser, stumm, laut. Kein Abspielen fremder
Adressen (SetAVTransportURI fehlt bewusst)."""

from __future__ import annotations

from ..konfig import Geraet
from . import Abgelehnt
from . import upnp

STANDARD_MAX = 70
SCHRITT = 10


def _pfade(geraet: Geraet) -> tuple[str, str]:
    return (geraet.opt("steuer_pfad", "/upnp/control/rendercontrol1"),
            geraet.opt("transport_pfad", "/upnp/control/rendertransport1"))


async def status(geraet: Geraet) -> dict:
    rc, avt = _pfade(geraet)
    return {"erreichbar": True, "lautstaerke": await upnp.lautstaerke_lesen(geraet, rc),
            "stumm": await upnp.stumm_lesen(geraet, rc), "wiedergabe": await upnp.wiedergabe(geraet, avt),
            "max_lautstaerke": geraet.opt("max_lautstaerke", STANDARD_MAX)}


async def steuern(geraet: Geraet, aktion: str, wert: int | None) -> dict:
    rc, _ = _pfade(geraet)
    grenze = int(geraet.opt("max_lautstaerke", STANDARD_MAX))
    if aktion in ("stumm", "laut"):
        await upnp.stumm_setzen(geraet, rc, aktion == "stumm")
        return {"stumm": aktion == "stumm"}
    if aktion == "lautstaerke":
        if wert is None or not 0 <= wert <= 100:
            raise Abgelehnt("Lautstärke 0 bis 100")
        ziel = wert
    elif aktion in ("lauter", "leiser"):
        jetzt = await upnp.lautstaerke_lesen(geraet, rc) or 0
        ziel = jetzt + SCHRITT if aktion == "lauter" else jetzt - SCHRITT
    else:
        raise Abgelehnt("Diese Aktion gibt es für Lautsprecher nicht")
    ziel = max(0, min(grenze, ziel))
    await upnp.lautstaerke_setzen(geraet, rc, ziel)
    return {"lautstaerke": ziel, "begrenzt": (wert or 0) > grenze if aktion == "lautstaerke" else False}
