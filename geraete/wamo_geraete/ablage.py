"""Ablage je Person auf Sokrates-2: Scans (F8, bis WMC_NAS1 als Ziel eingerichtet ist) und vom NAS abgerufene Dateien.

  /var/lib/wamo-geraete/ablage/<user_id>/<id>.bin    Inhalt (0600, Besitzer wamo-geraete)
  /var/lib/wamo-geraete/ablage/<user_id>/<id>.json   Metadaten: art (scan, nas), name, mime, groesse, erstellt, quelle

Zugriff nur mit der user_id aus dem Kontext des Kerns: Jede Person sieht und holt nur ihre eigenen Einträge. Die id ist
zufällig (128 Bit), Pfade entstehen nur aus geprüfter user_id und id. Aufbewahrung: Scans scan_tage (Standard 30),
NAS-Abrufe abruf_stunden (Standard 24); danach löscht aufraeumen() die Einträge. Grenzen je Person: 200 Einträge,
500 MB.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass

from .text import dateiname

ID_RE = re.compile(r"^[0-9a-f]{32}$")
ARTEN = {"scan", "nas"}
MIME = {"application/pdf", "image/jpeg", "image/png", "text/plain", "application/octet-stream"}
MAX_EINTRAEGE = 200
MAX_BYTES_PERSON = 500 * 1024 * 1024
MAX_DATEI = 30 * 1024 * 1024


class AblageFehler(ValueError):
    pass


@dataclass(frozen=True)
class Eintrag:
    id: str
    art: str
    name: str
    mime: str
    groesse: int
    erstellt: float
    quelle: str

    def oeffentlich(self) -> dict:
        return {"id": self.id, "art": self.art, "name": self.name, "mime": self.mime, "groesse": self.groesse,
                "erstellt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.erstellt)), "quelle": self.quelle}


def _uid(user_id: object) -> str:
    try:
        return str(uuid.UUID(str(user_id)))
    except (ValueError, TypeError, AttributeError) as e:
        raise AblageFehler("Profil ungültig") from e


class Ablage:
    def __init__(self, wurzel: str, scan_tage: int = 30, abruf_stunden: int = 24, uhr=time.time):
        self.wurzel = wurzel
        self.scan_secs = scan_tage * 86400
        self.abruf_secs = abruf_stunden * 3600
        self._uhr = uhr

    def _ordner(self, user_id: str, anlegen: bool = False) -> str:
        pfad = os.path.join(self.wurzel, _uid(user_id))
        if anlegen:
            os.makedirs(self.wurzel, mode=0o700, exist_ok=True)
            os.makedirs(pfad, mode=0o700, exist_ok=True)
        return pfad

    def liste(self, user_id: str) -> list[Eintrag]:
        ordner = self._ordner(user_id)
        eintraege: list[Eintrag] = []
        try:
            namen = os.listdir(ordner)
        except FileNotFoundError:
            return []
        for n in namen:
            if not n.endswith(".json") or not ID_RE.fullmatch(n[:-5]):
                continue
            try:
                with open(os.path.join(ordner, n), encoding="utf-8") as f:
                    m = json.load(f)
                e = Eintrag(id=n[:-5], art=m["art"], name=dateiname(m["name"]), mime=m["mime"],
                            groesse=int(m["groesse"]), erstellt=float(m["erstellt"]), quelle=str(m.get("quelle", ""))[:23])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if e.art in ARTEN and e.mime in MIME:
                eintraege.append(e)
        return sorted(eintraege, key=lambda e: -e.erstellt)

    def ablegen(self, user_id: str, art: str, name: str, mime: str, daten: bytes, quelle: str) -> Eintrag:
        if art not in ARTEN or mime not in MIME:
            raise AblageFehler("Art oder Typ unbekannt")
        if not daten or len(daten) > MAX_DATEI:
            raise AblageFehler("Datei leer oder zu groß")
        vorhanden = self.liste(user_id)
        if len(vorhanden) >= MAX_EINTRAEGE or sum(e.groesse for e in vorhanden) + len(daten) > MAX_BYTES_PERSON:
            raise AblageFehler("Ablage voll: bitte ältere Einträge löschen")
        ordner = self._ordner(user_id, anlegen=True)
        eid = secrets.token_hex(16)
        eintrag = Eintrag(eid, art, dateiname(name), mime, len(daten), self._uhr(), dateiname(quelle, 23))
        self._schreiben(os.path.join(ordner, f"{eid}.bin"), daten)
        meta = {"art": art, "name": eintrag.name, "mime": mime, "groesse": len(daten), "erstellt": eintrag.erstellt,
                "quelle": eintrag.quelle, "sha256": hashlib.sha256(daten).hexdigest()}
        self._schreiben(os.path.join(ordner, f"{eid}.json"), json.dumps(meta).encode("utf-8"))
        return eintrag

    @staticmethod
    def _schreiben(pfad: str, daten: bytes) -> None:
        tmp = f"{pfad}.{secrets.token_hex(4)}.neu"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(daten)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, pfad)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def holen(self, user_id: str, eid: str) -> tuple[Eintrag, bytes]:
        if not isinstance(eid, str) or not ID_RE.fullmatch(eid):
            raise AblageFehler("Unbekannter Eintrag")
        for e in self.liste(user_id):
            if e.id == eid:
                pfad = os.path.join(self._ordner(user_id), f"{eid}.bin")
                fd = os.open(pfad, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as f:
                    daten = f.read(MAX_DATEI + 1)
                if len(daten) != e.groesse:
                    raise AblageFehler("Eintrag beschädigt")
                return e, daten
        raise AblageFehler("Unbekannter Eintrag")

    def loeschen(self, user_id: str, eid: str) -> bool:
        if not isinstance(eid, str) or not ID_RE.fullmatch(eid):
            return False
        ordner = self._ordner(user_id)
        weg = False
        for endung in (".bin", ".json"):
            try:
                os.unlink(os.path.join(ordner, f"{eid}{endung}"))
                weg = True
            except FileNotFoundError:
                pass
        return weg

    def aufraeumen(self) -> int:
        """Abgelaufene Einträge aller Personen löschen. Liefert die Anzahl."""
        jetzt, anzahl = self._uhr(), 0
        try:
            personen = os.listdir(self.wurzel)
        except FileNotFoundError:
            return 0
        for p in personen:
            try:
                uid = _uid(p)
            except AblageFehler:
                continue
            for e in self.liste(uid):
                grenze = self.scan_secs if e.art == "scan" else self.abruf_secs
                if jetzt - e.erstellt > grenze and self.loeschen(uid, e.id):
                    anzahl += 1
        return anzahl
