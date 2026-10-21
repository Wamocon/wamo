"""Geräteregister im Kern (Tabelle devices, Migration 0002): welches Gerät gehört zu welchem Profil.

Der Kern ordnet ein Gerät immer nur über das Profil der Sitzung zu: das eine aktive persönliche Gerät mit
user_id = Profil. Es gibt keinen Weg, ein fremdes Gerät zu benennen (kein Werkzeugparameter, keine Liste, keine
user_id aus Umgebung oder Gespräch). Schreiben darf der Kern nur über die Funktionen der Migration: geraet_binden
(Erstbindung, Regeln in der Datenbank) und geraet_gesehen (last_seen_at).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from memory import DB_ERRORS, Database, DatabaseUnavailable, normalize_uuid

KENNUNG_RE = re.compile(r"pc-[a-z0-9]+(?:-[a-z0-9]+)*")  # wie devices_kennung_chk und devices_praefix_chk
MAX_KENNUNG = 23
SLOT_MIN, SLOT_MAX = 1, 64
GESEHEN_ABSTAND_SECS = 60.0


class RegisterFehler(RuntimeError):
    """Datenbank oder Register nicht verfügbar, Antwort unbrauchbar."""


@dataclass(frozen=True)
class Geraet:
    """Ein aktives persönliches Gerät. Alle Felder sind geprüft (Kennung, Platz), bevor daraus ein Pfad wird."""

    device_id: str
    kennung: str
    slot: int
    name: str
    hostname: str | None
    fingerabdruck: str

    @property
    def benutzer(self) -> str:
        return f"wamo-t-{self.slot:02d}"

    @property
    def ordner(self) -> str:
        return f"t{self.slot:02d}"


def geraet_aus_zeile(zeile: object) -> Geraet:
    """Prüft eine Zeile des Registers. Wirft RegisterFehler bei allem, was nicht genau passt (fail closed)."""
    try:
        device_id = normalize_uuid(zeile["id"])
        kennung = zeile["kennung"]
        slot = zeile["tunnel_slot"]
        name = zeile["name"] or ""
        hostname = zeile["hostname"]
        fingerabdruck = zeile["ssh_fingerprint"]
    except (KeyError, TypeError, IndexError) as e:
        raise RegisterFehler("Zeile des Registers unvollständig") from e
    if (device_id is None or not isinstance(kennung, str) or len(kennung) > MAX_KENNUNG
            or not KENNUNG_RE.fullmatch(kennung)):
        raise RegisterFehler("Kennung ungültig")
    if isinstance(slot, bool) or not isinstance(slot, int) or not SLOT_MIN <= slot <= SLOT_MAX:
        raise RegisterFehler("Tunnelplatz ungültig")
    if not isinstance(fingerabdruck, str) or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerabdruck):
        raise RegisterFehler("Fingerabdruck ungültig")
    return Geraet(device_id, kennung, slot, str(name)[:100],
                  hostname if isinstance(hostname, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", hostname) else None,
                  fingerabdruck)


class GeraeteRegister:
    def __init__(self, db: Database, uhr=time.monotonic):
        self._db = db
        self._uhr = uhr
        self._gesehen: dict[str, float] = {}

    async def aktives_geraet(self, user_id: str) -> Geraet | None:
        """Das eine aktive persönliche Gerät des Profils (aktives Profil vorausgesetzt) oder None. Wirft
        RegisterFehler, wenn die Datenbank nicht antwortet oder die Zeile nicht passt (dann gibt es keine PC-Werkzeuge)."""
        uid = normalize_uuid(user_id)
        if uid is None:
            raise RegisterFehler("user_id ungültig")
        try:
            zeilen = await self._db.fetch(
                f"select d.id::text as id, d.kennung, d.tunnel_slot, d.name, d.hostname, d.ssh_fingerprint "
                f"from {self._db.t('devices')} d join {self._db.t('profiles')} p on p.user_id = d.user_id "
                f"where d.user_id = $1::uuid and d.art = 'persoenlich' and d.status = 'aktiv' and p.aktiv "
                f"order by d.freigegeben_at nulls last limit 2", uid)
        except (*DB_ERRORS, DatabaseUnavailable) as e:
            raise RegisterFehler(f"Register nicht erreichbar ({type(e).__name__})") from e
        if not zeilen:
            return None
        if len(zeilen) > 1:  # der eindeutige Teilindex schließt das aus; trotzdem nie raten
            raise RegisterFehler("Mehr als ein aktives Gerät für das Profil")
        return geraet_aus_zeile(zeilen[0])

    async def ist_aktiv(self, device_id: str, user_id: str) -> bool | None:
        """Ist dieses Gerät noch aktiv und gehört es noch diesem Profil? None, wenn die Datenbank nicht antwortet."""
        did, uid = normalize_uuid(device_id), normalize_uuid(user_id)
        if did is None or uid is None:
            return False
        try:
            wert = await self._db.fetchval(
                f"select exists (select 1 from {self._db.t('devices')} d join {self._db.t('profiles')} p "
                f"on p.user_id = d.user_id where d.id = $1::uuid and d.user_id = $2::uuid and d.art = 'persoenlich' "
                f"and d.status = 'aktiv' and p.aktiv)", did, uid)
        except (*DB_ERRORS, DatabaseUnavailable):
            return None
        return bool(wert)

    async def gesehen(self, kennung: str) -> None:
        """last_seen_at nach einer erfolgreichen Probe, höchstens einmal je Minute und Gerät; wirft nie."""
        jetzt = self._uhr()
        if not KENNUNG_RE.fullmatch(kennung or "") or jetzt - self._gesehen.get(kennung, -1e9) < GESEHEN_ABSTAND_SECS:
            return
        self._gesehen[kennung] = jetzt
        try:
            await self._db.fetchval(f"select {self._db.t('geraet_gesehen')}($1)", kennung)
        except (*DB_ERRORS, DatabaseUnavailable):
            pass  # reine Anzeige

    async def binden(self, user_id: str, pubkey: str, hostname: str, name: str | None) -> dict:
        """Erstbindung (geraet_binden). Liefert die Zeile als dict mit ergebnis, status, kennung, tunnel_slot, name."""
        uid = normalize_uuid(user_id)
        if uid is None:
            raise RegisterFehler("user_id ungültig")
        try:
            zeile = await self._db.fetchrow(
                f"select device_id::text as device_id, kennung, tunnel_slot, status::text as status, name, ergebnis "
                f"from {self._db.t('geraet_binden')}($1::uuid, $2, $3, $4)", uid, pubkey, hostname, name)
        except (*DB_ERRORS, DatabaseUnavailable) as e:
            raise RegisterFehler(f"Register nicht erreichbar ({type(e).__name__})") from e
        if zeile is None:
            raise RegisterFehler("Keine Antwort der Bindung")
        return dict(zeile)
