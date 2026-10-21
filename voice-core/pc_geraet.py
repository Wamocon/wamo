"""Zuordnung einer Sitzung zum eigenen Laptop (Büro-Kern, Tunnel je Gerät; Schalter WAMO_PC_REGISTER=an).

Regeln (Bauplan 5.2):
  * Das Gerät ergibt sich NUR aus dem Profil der Sitzung: das eine aktive persönliche Gerät mit user_id = Profil
    (geraete_register.py). Kein Werkzeugparameter, keine Liste, keine user_id aus Umgebung oder Gespräch. Das Sprachmodell
    kann kein fremdes Gerät benennen, weil es dafür keinen Weg gibt. Auch kein Admin steuert einen fremden Laptop.
  * Pfade entstehen nur aus Zahlen der Datenbank: /run/wamo-pc/t<NN>/pc.sock (NN = tunnel_slot, 1 bis 64).
  * Geheimnisse (host-token, host-freigabe, schluessel) liegen im Ordner des Geräts, den der Zwangsbefehl auf Sokrates-2
    schreibt (wamo_pc_token.py). Gelesen wird wettlauffrei: Wurzel und Ordner mit O_NOFOLLOW (Wurzel mit O_PATH, weil sie
    0711 hat), Dateien per openat mit O_NOFOLLOW, dann fstat: reguläre Datei, Besitzer = Benutzer wamo-t-NN, höchstens
    600 Byte, keine Rechte für andere. Der Fingerabdruck in "schluessel" muss dem Register entsprechen.
  * Die Gegenstelle des Sockets prüft pc_tunnel.GegenstellenPruefung per SO_PEERCRED vor dem ersten Byte.
  * Jede Ablehnung steht mit dem Präfix WAMO-GERAET im Journal (ohne Geheimnisse); für die Sitzung heißt sie: keine
    PC-Werkzeuge.
  * Mitglieder bekommen PC-Werkzeuge nur mit WAMO_PC_MITGLIEDER=an (Standard aus, bis die Rechte auf dem eigenen
    Laptop abgenommen sind).
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import re
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loguru import logger

from geraete_register import Geraet, GeraeteRegister, RegisterFehler
from memory import shared_database

try:
    import pwd
except ImportError:  # Windows: nur für die Tests der reinen Teile
    pwd = None

WURZEL_STANDARD = "/run/wamo-pc"
SECRET_RE = re.compile(r"[A-Za-z0-9_-]{43,512}")
FINGERABDRUCK_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
MAX_DATEI = 600
WACHE_SECS = 60.0
WACHE_ERSTE_SECS = 15.0  # die erste Prüfung kommt früh: ein Entzug zwischen Bindung und Sitzungsbeginn wirkt schnell
WACHE_PRUEFUNG_SECS = 10.0  # Zeitgrenze einer einzelnen Prüfung (auch das Warten auf eine freie Verbindung)
LESEN_SECS = 10.0
WACHE_AUSFAELLE = 3
AN = ("an", "on", "1", "true", "ja")


class PcGeraetFehler(RuntimeError):
    """Gerät, Ordner oder Geheimnisse sind nicht in dem Zustand, den der Kern verlangt (fail closed)."""


def register_modus() -> bool:
    """Ordnet dieser Büro-Kern das Gerät über das Register zu? (WAMO_PC_REGISTER, fest in der Unit; Standard aus.)"""
    return (os.getenv("WAMO_BETRIEB") or "").strip().lower() == "buero" and \
        (os.getenv("WAMO_PC_REGISTER") or "aus").strip().lower() in AN


def mitglieder_erlaubt() -> bool:
    return (os.getenv("WAMO_PC_MITGLIEDER") or "aus").strip().lower() in AN


def socket_wurzel() -> str:
    wurzel = (os.getenv("WAMO_PC_SOCKET_WURZEL") or WURZEL_STANDARD).strip()
    if not wurzel.startswith("/") or ".." in wurzel.split("/") or wurzel != posixpath.normpath(wurzel):
        raise PcGeraetFehler("WAMO_PC_SOCKET_WURZEL ist kein sauberer absoluter Pfad")
    return wurzel


class Geheimnisse:
    """Host-Token und Freigabe-Schlüssel eines Geräts. Wird nach einem 401 neu gelesen; nie in Logs oder Darstellungen."""

    __slots__ = ("token", "freigabe")

    def __init__(self, token: str, freigabe: str):
        self.token = token
        self.freigabe = freigabe

    def __repr__(self) -> str:
        return "Geheimnisse(...)"


def _uid_von(benutzer: str) -> int:
    if pwd is None:
        raise PcGeraetFehler("Benutzerabfrage nicht möglich")
    try:
        return pwd.getpwnam(benutzer).pw_uid
    except KeyError as e:
        raise PcGeraetFehler("Tunnelbenutzer des Geräts fehlt") from e


def _gruppen_des_kerns() -> set[int]:
    return {os.getgid(), *os.getgroups()}


def _lies_datei(ordner_fd: int, name: str, uid: int, regel: re.Pattern, was: str) -> str:
    try:
        # O_NONBLOCK: ein FIFO unter dem Dateinamen würde sonst den Lesethread dauerhaft blockieren
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0), dir_fd=ordner_fd)
    except OSError as e:
        raise PcGeraetFehler(f"{was} fehlt oder ist ein Link") from e
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != uid or s.st_size > MAX_DATEI or s.st_mode & 0o137:
            raise PcGeraetFehler(f"{was} hat falschen Typ, Besitzer, Größe oder Rechte")
        roh = os.read(fd, MAX_DATEI + 1)
    finally:
        os.close(fd)
    try:
        text = roh.decode("ascii").strip()
    except UnicodeDecodeError as e:
        raise PcGeraetFehler(f"{was} ist kein ASCII") from e
    if len(roh) > MAX_DATEI or not regel.fullmatch(text):
        raise PcGeraetFehler(f"{was} hat ein ungültiges Format")
    return text


def lies_geheimnisse(geraet: Geraet, wurzel: str = WURZEL_STANDARD, *, uid: int | None = None, wurzel_uid: int = 0,
                     gruppen: set[int] | None = None) -> Geheimnisse:
    """Token, Freigabe-Schlüssel und Fingerabdruck aus dem Ordner des Geräts, wettlauffrei und streng geprüft.
    Wirft PcGeraetFehler (ohne Werte), auch bei Fehlern des Dateisystems. Blockierend, aber nur wenige kleine Dateien."""
    try:
        return _lies_geheimnisse(geraet, wurzel, uid=uid, wurzel_uid=wurzel_uid, gruppen=gruppen)
    except OSError as e:  # EIO, ESTALE o. ä. aus fstat und read
        raise PcGeraetFehler(f"Dateisystemfehler beim Lesen der Gerätedaten ({type(e).__name__})") from e


def _lies_geheimnisse(geraet: Geraet, wurzel: str, *, uid: int | None, wurzel_uid: int, gruppen: set[int] | None) -> Geheimnisse:
    uid = _uid_von(geraet.benutzer) if uid is None else uid
    gruppen = _gruppen_des_kerns() if gruppen is None else gruppen
    # Die Wurzel hat 0711 (betreten, nicht lesen): O_PATH genügt für fstat und openat, ein Öffnen zum Lesen scheiterte.
    try:
        wurzel_fd = os.open(wurzel, getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        raise PcGeraetFehler("Wurzelordner der Geräte fehlt") from e
    try:
        w = os.fstat(wurzel_fd)
        if w.st_uid != wurzel_uid or w.st_mode & 0o022:
            raise PcGeraetFehler("Wurzelordner gehört nicht root oder ist beschreibbar")
        try:
            ordner_fd = os.open(geraet.ordner, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wurzel_fd)
        except OSError as e:
            raise PcGeraetFehler("Geräteordner fehlt (Tunnel steht nicht)") from e
    finally:
        os.close(wurzel_fd)
    try:
        o = os.fstat(ordner_fd)
        if o.st_uid != uid or o.st_gid not in gruppen or stat.S_IMODE(o.st_mode) != 0o2750:
            raise PcGeraetFehler("Geräteordner hat falschen Besitzer, falsche Gruppe oder falsche Rechte")
        token = _lies_datei(ordner_fd, "host-token", uid, SECRET_RE, "Host-Token")
        freigabe = _lies_datei(ordner_fd, "host-freigabe", uid, SECRET_RE, "Freigabe-Schlüssel")
        fingerabdruck = _lies_datei(ordner_fd, "schluessel", uid, FINGERABDRUCK_RE, "Schlüssel-Fingerabdruck")
    finally:
        os.close(ordner_fd)
    if fingerabdruck != geraet.fingerabdruck:
        raise PcGeraetFehler("Der Tunnel nutzt nicht den Schlüssel, den das Register für dieses Gerät führt")
    if token == freigabe:
        raise PcGeraetFehler("Token und Freigabe-Schlüssel sind gleich")
    return Geheimnisse(token, freigabe)


@dataclass(eq=False)
class PcZugang:
    """Alles, was eine Sitzung für den Zugriff auf IHREN Laptop braucht. Entsteht nur über pc_zugang_fuer."""

    geraet: Geraet
    user_id: str
    socket: str
    uid: int
    wurzel: str
    geheimnisse: Geheimnisse
    register: GeraeteRegister

    async def nachladen(self) -> str:
        """Token und Freigabe-Schlüssel neu lesen (der Host erzeugt sie bei jedem Start neu). Liefert das Token."""
        neu = await asyncio.wait_for(asyncio.to_thread(lies_geheimnisse, self.geraet, self.wurzel, uid=self.uid), LESEN_SECS)
        self.geheimnisse.token, self.geheimnisse.freigabe = neu.token, neu.freigabe
        return neu.token

    async def gesehen(self) -> None:
        await self.register.gesehen(self.geraet.kennung)


def rechnername_passt(geraet: Geraet, gemeldet: str | None) -> bool:
    """Plausibilität: meldet der Host den Rechnernamen, den das Register für das Gerät führt? Fehlt eine der beiden
    Angaben, gilt das als Abweichung (fail closed). Groß und klein sind gleich, wie bei Windows-Namen."""
    return bool(geraet.hostname and gemeldet and geraet.hostname.lower() == gemeldet.lower())


async def pc_zugang_fuer(identity, register: GeraeteRegister | None = None, wurzel: str | None = None) -> PcZugang | None:
    """Zugang dieser Sitzung zu IHREM Laptop oder None (dann keine PC-Werkzeuge). Wirft nie."""
    if not (identity.is_admin or mitglieder_erlaubt()):
        logger.info("WAMO-GERAET PC-Werkzeuge aus: Profil ist Mitglied und WAMO_PC_MITGLIEDER ist aus")
        return None
    try:
        wurzel = wurzel or socket_wurzel()
        register = register or GeraeteRegister(shared_database())
        geraet = await register.aktives_geraet(identity.user_id)
    except (RegisterFehler, PcGeraetFehler, ValueError) as e:
        logger.warning("WAMO-GERAET Register nicht verfügbar: {}", e)
        return None
    if geraet is None:
        logger.info("WAMO-GERAET PC-Werkzeuge aus: Profil {} hat kein aktives Gerät", identity.user_id[:8])
        return None
    try:
        uid = _uid_von(geraet.benutzer)
        geheimnisse = await asyncio.wait_for(asyncio.to_thread(lies_geheimnisse, geraet, wurzel, uid=uid), LESEN_SECS)
    except (PcGeraetFehler, TimeoutError) as e:
        logger.warning("WAMO-GERAET PC-Werkzeuge aus für Gerät {}: {}", geraet.kennung, e)
        return None
    return PcZugang(geraet=geraet, user_id=identity.user_id, socket=f"{wurzel}/{geraet.ordner}/pc.sock", uid=uid,
                    wurzel=wurzel, geheimnisse=geheimnisse, register=register)


async def geraet_wache(zugang: PcZugang, bei_entzug: Callable[[str], Awaitable[None]], *, intervall: float = WACHE_SECS,
                       erste: float = WACHE_ERSTE_SECS, ausfaelle: int = WACHE_AUSFAELLE,
                       schlaf: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """Prüft alle intervall Sekunden, ob das Gerät noch aktiv und noch Eigentum dieses Profils ist (gesperrt, widerrufen,
    Profil deaktiviert). Dann endet die PC-Steuerung dieser Sitzung (bei_entzug). Fällt die Datenbank aus, geschieht das
    nach ausfaelle Prüfungen in Folge (fail closed). Läuft, bis die Sitzung den Task abbricht oder das Gerät entzogen ist."""
    fehler = 0
    pause = erste
    while True:
        await schlaf(pause)
        pause = intervall
        try:
            aktiv = await asyncio.wait_for(zugang.register.ist_aktiv(zugang.geraet.device_id, zugang.user_id), WACHE_PRUEFUNG_SECS)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 (jeder Fehler der Prüfung zählt wie ein Ausfall: nie still enden, fail closed)
            logger.warning("WAMO-GERAET Prüfung des Geräts fehlgeschlagen ({})", type(e).__name__)
            aktiv = None
        if aktiv is None:
            fehler += 1
            if fehler >= ausfaelle:
                logger.warning("WAMO-GERAET Register {} Prüfungen in Folge nicht erreichbar: PC-Steuerung endet", fehler)
                await bei_entzug("Das Geräteregister ist nicht erreichbar.")
                return
            continue
        fehler = 0
        if not aktiv:
            logger.warning("WAMO-GERAET Gerät {} ist nicht mehr aktiv: PC-Steuerung endet", zugang.geraet.kennung)
            await bei_entzug("Dein Laptop wurde gesperrt oder die Freigabe wurde widerrufen.")
            return
