"""UPnP-SOAP mit fester Aktionsliste: FRITZ!Box (IGD, Status ohne Anmeldung; TR-064 für Status, Geräteliste und Hostliste
nur mit Zugang), JBL-Lautsprecher und Fernseher (RenderingControl, AVTransport).

Es gibt keinen Weg, eine beliebige SOAP-Aktion aufzurufen: Jede Aktion steht als Schlüssel in AKTIONEN mit festem
Pfad, Diensttyp, Namen und festen Argumentnamen; Werte sind nur Ganzzahlen oder feste Wörter. Insbesondere
AddPortMapping, DeletePortMapping, ForceTermination, SetConnectionType und alle X_AVM-DE-Schreibaktionen fehlen. Auch die
TR-064-Aktionen unten sind nur lesend (Get...); der Router bleibt in diesem Dienst ausschließlich lesbar.
"""

from __future__ import annotations

import asyncio
import re
from xml.sax.saxutils import escape

from ..http_klein import HttpFehler, anfrage, tls_ohne_ca
from ..konfig import Geraet, Konfig
from ..text import ganzzahl, name
from . import GeraetFehler, NichtEingerichtet

WAN_IFC = ("/igdupnp/control/WANCommonIFC1", "urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1")
WAN_IP = ("/igdupnp/control/WANIPConn1", "urn:schemas-upnp-org:service:WANIPConnection:1")
RC = "urn:schemas-upnp-org:service:RenderingControl:1"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
TR064_HOSTS = ("/upnp/control/hosts", "urn:dslforum-org:service:Hosts:1")
TR064_WAN_IFC = ("/upnp/control/wancommonifconfig1", "urn:dslforum-org:service:WANCommonInterfaceConfig:1")
TR064_WAN_IP = ("/upnp/control/wanipconnection1", "urn:dslforum-org:service:WANIPConnection:1")

# Schlüssel -> (Pfad oder None für den Pfad aus der Gerätekonfiguration, Diensttyp, Aktion, feste Argumentnamen)
AKTIONEN: dict[str, tuple[str | None, str, str, tuple[str, ...]]] = {
    "router_link": (WAN_IFC[0], WAN_IFC[1], "GetCommonLinkProperties", ()),
    "router_gesendet": (WAN_IFC[0], WAN_IFC[1], "GetTotalBytesSent", ()),
    "router_empfangen": (WAN_IFC[0], WAN_IFC[1], "GetTotalBytesReceived", ()),
    "router_verbindung": (WAN_IP[0], WAN_IP[1], "GetStatusInfo", ()),
    "rc_lautstaerke_lesen": (None, RC, "GetVolume", ("InstanceID", "Channel")),
    "rc_lautstaerke_setzen": (None, RC, "SetVolume", ("InstanceID", "Channel", "DesiredVolume")),
    "rc_stumm_lesen": (None, RC, "GetMute", ("InstanceID", "Channel")),
    "rc_stumm_setzen": (None, RC, "SetMute", ("InstanceID", "Channel", "DesiredMute")),
    "avt_zustand": (None, AVT, "GetTransportInfo", ("InstanceID",)),
    "tr064_hostliste": (TR064_HOSTS[0], TR064_HOSTS[1], "X_AVM-DE_GetHostListPath", ()),
    # Status über TR-064 (HTTPS 49443, Anmeldung): dieselben Werte wie über UPnP-IGD, nur lesend
    "tr064_link": (TR064_WAN_IFC[0], TR064_WAN_IFC[1], "GetCommonLinkProperties", ()),
    "tr064_gesendet": (TR064_WAN_IFC[0], TR064_WAN_IFC[1], "GetTotalBytesSent", ()),
    "tr064_empfangen": (TR064_WAN_IFC[0], TR064_WAN_IFC[1], "GetTotalBytesReceived", ()),
    "tr064_verbindung": (TR064_WAN_IP[0], TR064_WAN_IP[1], "GetStatusInfo", ()),
    # Hosts:1 für die passive Erkennung unbekannter Geräte (treiber/hostliste.py)
    "tr064_host_anzahl": (TR064_HOSTS[0], TR064_HOSTS[1], "GetHostNumberOfEntries", ()),
    "tr064_host_eintrag": (TR064_HOSTS[0], TR064_HOSTS[1], "GetGenericHostEntry", ("NewIndex",)),
}
_WERT_RE = re.compile(r"^(?:\d{1,6}|Master)$")


def soap_rumpf(aktion: str, werte: dict[str, int | str]) -> tuple[str, bytes]:
    """(SOAPAction, Rumpf) für eine Aktion aus AKTIONEN. Wirft KeyError oder ValueError für alles andere."""
    _, dienst, name_, argnamen = AKTIONEN[aktion]
    if set(werte) != set(argnamen):
        raise ValueError("Argumente passen nicht zur Aktion")
    teile = []
    for a in argnamen:
        w = str(werte[a])
        if not _WERT_RE.fullmatch(w):
            raise ValueError("Argumentwert nicht erlaubt")
        teile.append(f"<{a}>{escape(w)}</{a}>")
    rumpf = ('<?xml version="1.0" encoding="utf-8"?>'
             '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
             's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
             f'<u:{name_} xmlns:u="{dienst}">{"".join(teile)}</u:{name_}></s:Body></s:Envelope>')
    return f'"{dienst}#{name_}"', rumpf.encode("utf-8")


def soap_werte(xml: bytes) -> dict[str, str]:
    """Einfache Rückgabewerte (<NewX>wert</NewX>, <CurrentVolume>...) als Text; Fehlerantworten werfen."""
    text = xml.decode("utf-8", "replace")
    if "<s:Fault>" in text or ":Fault>" in text:
        code = re.search(r"<errorCode>(\d{1,5})</errorCode>", text)
        raise GeraetFehler(f"Gerät lehnt die Aktion ab (UPnP-Fehler {code.group(1) if code else '?'})")
    return {m.group(1): m.group(2) for m in re.finditer(r"<([A-Za-z_][A-Za-z0-9_-]{0,60})>([^<]{0,300})</\1>", text)}


async def aufruf(geraet: Geraet, aktion: str, werte: dict[str, int | str] | None = None, *, pfad: str | None = None,
                 port: int | None = None, tls_sha256: str | None = None, digest: tuple[str, str] | None = None,
                 timeout: float = 4.0) -> dict[str, str]:
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse fehlt")
    fest, _, _, _ = AKTIONEN[aktion]
    ziel_pfad = fest or pfad
    if not ziel_pfad:
        raise NichtEingerichtet("Steuerpfad fehlt in der Gerätekonfiguration")
    soapaction, rumpf = soap_rumpf(aktion, werte or {})
    try:
        antwort = await anfrage(geraet.adresse, port or geraet.port or 49000, "POST", ziel_pfad,
                                kopf={"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": soapaction},
                                rumpf=rumpf, timeout=timeout, max_bytes=64 * 1024,
                                tls=tls_ohne_ca() if tls_sha256 else None, tls_sha256=tls_sha256, digest=digest)
    except HttpFehler as e:
        raise GeraetFehler(f"Gerät nicht erreichbar ({e})") from e
    if antwort.status == 401:
        raise NichtEingerichtet("Gerät verlangt eine Anmeldung, die Zugangsdaten fehlen oder stimmen nicht")
    if antwort.status not in (200, 500):
        raise GeraetFehler(f"Gerät antwortet mit HTTP {antwort.status}")
    return soap_werte(antwort.rumpf)


# ---------- Router ----------
def tr064_zugang(geraet: Geraet, konfig: Konfig | None) -> tuple[int, str, tuple[str, str]]:
    """(Port, Zertifikats-Fingerabdruck, (Benutzer, Passwort)) für TR-064 oder NichtEingerichtet."""
    zugang = geraet.opt("zugang")
    benutzer = konfig.geheimnis(zugang, "BENUTZER") if konfig else None
    passwort = konfig.geheimnis(zugang, "PASSWORT") if konfig else None
    pin, port = geraet.opt("tls_sha256"), geraet.opt("tr064_port")
    if not (zugang and benutzer and passwort and pin and port):
        raise NichtEingerichtet("TR-064 ist nicht eingerichtet (Benutzer, Passwort, Zertifikat und Port fehlen)")
    return port, pin, (benutzer, passwort)


def _router_felder(link: dict[str, str], verbindung: dict[str, str], gesendet: dict[str, str],
                   empfangen: dict[str, str]) -> dict:
    """Gemeinsame Auswertung für UPnP-IGD und TR-064: gleiche Felder, gleiche Wörter."""
    hoch = ganzzahl(link.get("NewLayer1UpstreamMaxBitRate"), 0, 10**11)
    runter = ganzzahl(link.get("NewLayer1DownstreamMaxBitRate"), 0, 10**11)
    return {
        "erreichbar": True,
        "internet": {"Connected": "verbunden", "Disconnected": "getrennt", "Connecting": "verbindet",
                     "Unconfigured": "nicht eingerichtet"}.get(verbindung.get("NewConnectionStatus", ""), "unbekannt"),
        "leitung": {"Up": "steht", "Down": "getrennt", "Initializing": "startet"}.get(
            link.get("NewPhysicalLinkStatus", ""), "unbekannt"),
        "zugang": name(link.get("NewWANAccessType"), 20),
        "laufzeit_stunden": round((ganzzahl(verbindung.get("NewUptime"), 0, 10**9) or 0) / 3600, 1),
        "hoch_mbit": round(hoch / 1e6, 1) if hoch is not None else None,
        "runter_mbit": round(runter / 1e6, 1) if runter is not None else None,
        "gesendet_gb": round((ganzzahl(gesendet.get("NewTotalBytesSent"), 0, 2**64) or 0) / 1e9, 2),
        "empfangen_gb": round((ganzzahl(empfangen.get("NewTotalBytesReceived"), 0, 2**64) or 0) / 1e9, 2),
    }


async def router_status(geraet: Geraet, konfig: Konfig | None = None) -> dict:
    """Status der FRITZ!Box. Quelle nach status_quelle: "upnp" (Standard, IGD ohne Anmeldung) oder "tr064" (HTTPS 49443 mit
    Anmeldung und festgehaltenem Zertifikat; dafür braucht der Treiber die Konfiguration mit den Zugangsdaten)."""
    if geraet.opt("status_quelle", "upnp") == "tr064":
        port, pin, digest = tr064_zugang(geraet, konfig)
        schluessel = ("tr064_link", "tr064_verbindung", "tr064_gesendet", "tr064_empfangen")
        link, verbindung, gesendet, empfangen = await asyncio.gather(
            *(aufruf(geraet, a, port=port, tls_sha256=pin, digest=digest) for a in schluessel))
        return _router_felder(link, verbindung, gesendet, empfangen)
    link = await aufruf(geraet, "router_link")
    verbindung = await aufruf(geraet, "router_verbindung")
    gesendet = await aufruf(geraet, "router_gesendet")
    empfangen = await aufruf(geraet, "router_empfangen")
    return _router_felder(link, verbindung, gesendet, empfangen)


def _items(text: str, maximum: int = 300) -> list[str]:
    """Inhalte der <Item>-Blöcke einer Geräteliste, linear (split statt Regex mit Rücksprung auf ungeschlossenen Blöcken)."""
    erg: list[str] = []
    for stueck in text.split("<Item>")[1:maximum + 1]:
        ende = stueck.find("</Item>")
        if ende >= 0:
            erg.append(stueck[:ende])
    return erg


async def router_geraete(geraet: Geraet, konfig: Konfig) -> list[dict]:
    """Verbundene Geräte über TR-064 (HTTPS 49443, Digest, festgehaltenes Zertifikat). Nur für Admins; das Ergebnis
    geht im Kern direkt an die Oberfläche (personenbezogen: zeigt, wer im Büro ist)."""
    port, pin, digest = tr064_zugang(geraet, konfig)
    werte = await aufruf(geraet, "tr064_hostliste", port=port, tls_sha256=pin, digest=digest)
    pfad = werte.get("NewX_AVM-DE_HostListPath", "")
    if not re.fullmatch(r"/devicehostlist\.lua\?sid=[0-9a-f]{16}", pfad):
        raise GeraetFehler("Router liefert keinen gültigen Pfad zur Geräteliste")
    try:
        antwort = await anfrage(geraet.adresse, port, "GET", pfad, timeout=6, max_bytes=512 * 1024,
                                tls=tls_ohne_ca(), tls_sha256=pin)
    except HttpFehler as e:
        raise GeraetFehler(f"Geräteliste nicht abrufbar ({e})") from e
    if antwort.status != 200:
        raise GeraetFehler(f"Geräteliste: HTTP {antwort.status}")
    text = antwort.rumpf.decode("utf-8", "replace")
    liste = []
    for item in _items(text):
        def feld(tag: str) -> str:
            m = re.search(rf"<{tag}>([^<]{{0,100}})</{tag}>", item)
            return m.group(1) if m else ""
        ip = feld("IPAddress")
        liste.append({"name": name(feld("HostName"), 32) or "?",
                      "adresse": ip if re.fullmatch(r"192\.168\.178\.\d{1,3}", ip) else "",
                      "aktiv": feld("Active") == "1", "verbindung": name(feld("InterfaceType"), 16)})
    return liste


# ---------- RenderingControl (Lautsprecher, Fernseher) ----------
async def lautstaerke_lesen(geraet: Geraet, pfad: str | None, port: int | None = None) -> int | None:
    werte = await aufruf(geraet, "rc_lautstaerke_lesen", {"InstanceID": 0, "Channel": "Master"}, pfad=pfad, port=port)
    return ganzzahl(werte.get("CurrentVolume"), 0, 100)


async def lautstaerke_setzen(geraet: Geraet, pfad: str | None, wert: int, port: int | None = None) -> None:
    await aufruf(geraet, "rc_lautstaerke_setzen", {"InstanceID": 0, "Channel": "Master", "DesiredVolume": int(wert)},
                 pfad=pfad, port=port)


async def stumm_lesen(geraet: Geraet, pfad: str | None, port: int | None = None) -> bool | None:
    werte = await aufruf(geraet, "rc_stumm_lesen", {"InstanceID": 0, "Channel": "Master"}, pfad=pfad, port=port)
    wert = werte.get("CurrentMute")
    return {"1": True, "0": False, "true": True, "false": False}.get((wert or "").lower())


async def stumm_setzen(geraet: Geraet, pfad: str | None, an: bool, port: int | None = None) -> None:
    await aufruf(geraet, "rc_stumm_setzen", {"InstanceID": 0, "Channel": "Master", "DesiredMute": 1 if an else 0},
                 pfad=pfad, port=port)


async def wiedergabe(geraet: Geraet, pfad: str | None, port: int | None = None) -> str:
    werte = await aufruf(geraet, "avt_zustand", {"InstanceID": 0}, pfad=pfad, port=port)
    return {"PLAYING": "spielt", "PAUSED_PLAYBACK": "pausiert", "STOPPED": "gestoppt",
            "TRANSITIONING": "wechselt", "NO_MEDIA_PRESENT": "nichts geladen"}.get(
        (werte.get("CurrentTransportState") or "").upper(), "unbekannt")
