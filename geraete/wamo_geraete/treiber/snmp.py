"""SNMPv3-Lesetreiber für Switches und Access Points (nur lesen).

Was es gibt: GET auf drei Systemwerte (sysDescr, sysUpTime, sysName) und das Durchlaufen (Walk) von vier festen Spalten der
Schnittstellentabelle (ifType, ifOperStatus und auf Wunsch ifInErrors und ifOutErrors). Daraus entstehen Name, Laufzeit, die
Zahl der Ports oben und unten und optional die Summen der Fehlerzähler.

Was es nicht gibt: SET, Traps, SNMPv1, SNMPv2c (keine Community), SHA-1, MD5, DES, authNoPriv, noAuthNoPriv. Die Anmeldung ist
immer SNMPv3 authPriv mit SHA-2 (224 bis 512) und AES (128, 192, 256). Die Verfahren sind feste Wörter (konfig.SNMP_AUTH,
konfig.SNMP_PRIV), die OIDs stehen in ERLAUBTE_OIDS; _lesen() weist jede andere OID ab, bevor etwas auf das Netz geht.

Zugangsdaten nur aus /etc/wamo-geraete/geraete.env, je Zugang drei Werte (mit zugang = "SWITCH1" in geraete.toml):
  WAMO_GERAETE_SWITCH1_BENUTZER      Benutzername des eigenen Lese-Benutzers (USM-Benutzer am Gerät, Gruppe nur lesen)
  WAMO_GERAETE_SWITCH1_PASSWORT      Passphrase für die Anmeldung (Authentifizierung)
  WAMO_GERAETE_SWITCH1_PRIVPASSWORT  Passphrase für die Verschlüsselung (Privacy)
Passphrasen haben mindestens 8 Zeichen (RFC 3414). Sie stehen nie in Meldungen, Protokollen oder im Audit.

Alles, was das Gerät liefert (Name, Beschreibung), ist unvertrauenswürdiger Text: gekürzt und von Steuer- und Klammerzeichen
befreit (text.py). Die Bibliothek pysnmp wird erst beim ersten Abruf geladen, damit der Dienst ohne SNMP-Geräte keinen Speicher
dafür braucht.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field

from ..konfig import SNMP_AUTH, SNMP_PRIV, Geraet, Konfig
from ..text import name, text
from . import GeraetFehler, NichtEingerichtet

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"
IF_IN_FEHLER = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_FEHLER = "1.3.6.1.2.1.2.2.1.20"
SKALARE = (SYS_DESCR, SYS_UPTIME, SYS_NAME)
TABELLEN = (IF_TYPE, IF_OPER, IF_IN_FEHLER, IF_OUT_FEHLER)
ERLAUBTE_OIDS = frozenset(SKALARE + TABELLEN)

# Namen in geraete.toml -> Bezeichner in pysnmp (hlapi.v3arch.asyncio). Nur SHA-2 und AES.
AUTH_VERFAHREN = {"sha224": "usmHMAC128SHA224AuthProtocol", "sha256": "usmHMAC192SHA256AuthProtocol",
                  "sha384": "usmHMAC256SHA384AuthProtocol", "sha512": "usmHMAC384SHA512AuthProtocol"}
PRIV_VERFAHREN = {"aes128": "usmAesCfb128Protocol", "aes192": "usmAesCfb192Protocol", "aes256": "usmAesCfb256Protocol"}
assert set(AUTH_VERFAHREN) == set(SNMP_AUTH) and set(PRIV_VERFAHREN) == set(SNMP_PRIV)

# ifType: ethernetCsmacd, fastEther, fastEtherFX, gigabitEthernet. Loopback, VLAN, Funk und Trunks zählen nicht als Port.
PORT_TYPEN = frozenset({6, 62, 69, 117})
OPER_OBEN = 1       # ifOperStatus up
OPER_UNTEN = (2, 7)  # down, lowerLayerDown
MAX_ZEILEN = 512    # höchstens so viele Schnittstellen je Tabelle
MIN_PASSPHRASE = 8
ANTWORT_ZEIT = 2.0  # Sekunden je Anfrage, ein Wiederholversuch
# Obergrenze für das Netz eines Abrufs. Sie liegt unter der Grenze des Dienstes (dienst.STATUS_ZEIT, 6 s bei Status aller Geräte),
# denn sonst entschiede immer der Dienst und diese Grenze griffe nie. Vor ihr liegen die Engine und das Ziel (rund 0,05 bis 0,3 s).
GESAMT_ZEIT = 5.0


@dataclass(frozen=True)
class Zugang:
    """Anmeldedaten für SNMPv3 authPriv. Nichts davon erscheint in repr() oder in Fehlermeldungen."""
    benutzer: str = field(repr=False)
    auth_passwort: str = field(repr=False)
    priv_passwort: str = field(repr=False)
    auth: str = "sha256"
    priv: str = "aes128"


@dataclass
class Messung:
    """Rohwerte eines Abrufs: OID -> Ganzzahl oder Text (oder None), und je Tabellenspalte Index -> Wert."""
    skalare: dict[str, object] = field(default_factory=dict)
    tabellen: dict[str, dict[int, object]] = field(default_factory=dict)


# ---------- Zugangsdaten ----------
def _zugang(g: Geraet, konfig: Konfig) -> Zugang:
    if not g.adresse:
        raise NichtEingerichtet("Adresse fehlt")
    zugang = g.opt("zugang")
    benutzer = konfig.geheimnis(zugang, "BENUTZER")
    auth_pw = konfig.geheimnis(zugang, "PASSWORT")
    priv_pw = konfig.geheimnis(zugang, "PRIVPASSWORT")
    if not (benutzer and auth_pw and priv_pw):
        raise NichtEingerichtet("SNMP ist nicht eingerichtet (Benutzer und zwei Passphrasen fehlen in geraete.env)")
    if len(auth_pw) < MIN_PASSPHRASE or len(priv_pw) < MIN_PASSPHRASE:
        raise NichtEingerichtet(f"SNMP: Eine Passphrase ist zu kurz, mindestens {MIN_PASSPHRASE} Zeichen nötig")
    return Zugang(benutzer, auth_pw, priv_pw, g.opt("auth", "sha256"), g.opt("verschluesselung", "aes128"))


# ---------- Netz (pysnmp) ----------
def _ganz(wert: object) -> int | None:
    """Nur echte Ganzzahlen (keine Wahrheitswerte, keine Texte)."""
    return wert if isinstance(wert, int) and not isinstance(wert, bool) else None


def _wert(roh: object) -> int | str | None:
    """Wert aus pysnmp (pyasn1) in eine Ganzzahl oder einen Text; alles andere (noSuchObject, endOfMibView, ...) ist None."""
    from pyasn1.type import univ

    if isinstance(roh, univ.Integer):
        return int(roh)
    if isinstance(roh, univ.OctetString):
        return roh.asOctets().decode("utf-8", "replace")
    return None


def _pruefen(fehler: object, status: object) -> None:
    """Fehlerangaben einer Antwort in einen kurzen Text ohne Gerätedaten übersetzen."""
    if fehler:
        raise GeraetFehler(f"SNMP-Fehler ({type(fehler).__name__})")
    if status:
        raise GeraetFehler("SNMP: Das Gerät lehnt die Abfrage ab (keine Berechtigung oder unbekannter Wert)")


def _index(oid: str, spalte: str) -> int | None:
    """Zeilennummer aus <spalte>.<index>; None, wenn die OID nicht unter der Spalte liegt."""
    if not oid.startswith(spalte + "."):
        return None
    rest = oid[len(spalte) + 1:]
    return int(rest) if rest.isdigit() and len(rest) <= 9 else None


async def _lesen(geraet: Geraet, zugang: Zugang, tabellen: tuple[str, ...]) -> Messung:
    """Ein Abruf: die drei Systemwerte (GET) und je gewünschter Spalte ein Walk. Wirft ValueError bei einer OID, die nicht in
    ERLAUBTE_OIDS steht (vor jedem Netzzugriff), und GeraetFehler bei Netz- oder SNMP-Fehlern."""
    if any(t not in TABELLEN for t in tabellen):
        raise ValueError("OID nicht erlaubt")
    ip = ipaddress.ip_address(geraet.adresse or "")  # wirft ValueError bei Namen und leerer Adresse
    if ip.version != 4:
        raise ValueError("nur IPv4")
    from pysnmp.error import PySnmpError
    from pysnmp.hlapi.v3arch import asyncio as hl
    from pysnmp.proto.secmod.rfc3826.priv import aes

    if getattr(aes, "PysnmpCryptoError", False):  # pysnmp meldet sonst später nur "EncryptionError"
        raise GeraetFehler("SNMP: AES nicht verfügbar (Bibliothek cryptography fehlt oder ist älter als 47)")
    # SnmpEngine() ist rund 50 ms reine Python-Arbeit (beim ersten Mal 235 ms, pysnmp 7.1): im Arbeitsthread, nicht in der
    # Ereignisschleife des Dienstes. Je Abruf eine eigene Engine und danach geschlossen: Eine Engine für den ganzen Prozess würde
    # den Zustand (Benutzertabelle, Transport an die Schleife gebunden) zwischen Zugängen und Läufen teilen; das ist ohne Gerät
    # nicht zu belegen und wurde darum nicht gebaut.
    engine = await asyncio.to_thread(hl.SnmpEngine)
    try:
        benutzer = hl.UsmUserData(zugang.benutzer, authKey=zugang.auth_passwort, privKey=zugang.priv_passwort,
                                  authProtocol=getattr(hl, AUTH_VERFAHREN[zugang.auth]),
                                  privProtocol=getattr(hl, PRIV_VERFAHREN[zugang.priv]))
        ziel = await hl.UdpTransportTarget.create((str(ip), geraet.port or 161), timeout=ANTWORT_ZEIT, retries=1)
        kontext = hl.ContextData()
        messung = Messung()
        async with asyncio.timeout(GESAMT_ZEIT):
            fehler, status, _, binds = await hl.get_cmd(
                engine, benutzer, ziel, kontext, *(hl.ObjectType(hl.ObjectIdentity(o)) for o in SKALARE), lookupMib=False)
            _pruefen(fehler, status)
            for bind in binds:
                oid = str(bind[0].get_oid() if hasattr(bind[0], "get_oid") else bind[0])
                if oid in SKALARE:
                    messung.skalare[oid] = _wert(bind[1])
            for spalte in tabellen:
                zeilen: dict[int, object] = {}
                async for fehler, status, _, binds in hl.walk_cmd(
                        engine, benutzer, ziel, kontext, hl.ObjectType(hl.ObjectIdentity(spalte)),
                        lexicographicMode=False, lookupMib=False, maxCalls=MAX_ZEILEN):
                    _pruefen(fehler, status)
                    for bind in binds:
                        zeile = _index(str(bind[0].get_oid() if hasattr(bind[0], "get_oid") else bind[0]), spalte)
                        if zeile is not None and len(zeilen) < MAX_ZEILEN:
                            zeilen[zeile] = _wert(bind[1])
                messung.tabellen[spalte] = zeilen
        return messung
    except (PySnmpError, OSError, TimeoutError) as e:
        raise GeraetFehler(f"SNMP: keine brauchbare Antwort ({type(e).__name__})") from e
    finally:
        engine.close_dispatcher()


# ---------- Status ----------
def _summe(tabelle: dict[int, object], ports: set[int]) -> int:
    werte = (_ganz(w) for i, w in tabelle.items() if i in ports)
    return sum(w for w in werte if w is not None and w >= 0)


async def status(geraet: Geraet, konfig: Konfig) -> dict:
    """Zustand eines Switches oder Access Points: erreichbar, Name, Beschreibung, Laufzeit in Stunden, Ports gesamt, oben,
    unten und (auf Wunsch) die Summen der Fehlerzähler aller Ports."""
    zugang = _zugang(geraet, konfig)
    fehler_zaehlen = geraet.opt("fehlerzaehler") is True
    tabellen = (IF_TYPE, IF_OPER, *((IF_IN_FEHLER, IF_OUT_FEHLER) if fehler_zaehlen else ()))
    m = await _lesen(geraet, zugang, tabellen)
    typen, oper = m.tabellen.get(IF_TYPE, {}), m.tabellen.get(IF_OPER, {})
    ports = {i for i, t in typen.items() if _ganz(t) in PORT_TYPEN}
    ticks = _ganz(m.skalare.get(SYS_UPTIME))
    erg: dict = {
        "erreichbar": True,
        "name": name(m.skalare.get(SYS_NAME), 40),
        "beschreibung": text(m.skalare.get(SYS_DESCR), 80),
        "laufzeit_stunden": round(ticks / 360000, 1) if ticks is not None and 0 <= ticks < 2**32 else None,
        "ports_gesamt": len(ports),
        "ports_oben": sum(1 for i in ports if _ganz(oper.get(i)) == OPER_OBEN),
        "ports_unten": sum(1 for i in ports if _ganz(oper.get(i)) in OPER_UNTEN),
    }
    if fehler_zaehlen:
        erg["fehler_ein"] = _summe(m.tabellen.get(IF_IN_FEHLER, {}), ports)
        erg["fehler_aus"] = _summe(m.tabellen.get(IF_OUT_FEHLER, {}), ports)
    return erg
