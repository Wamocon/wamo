"""Arbeit teilen (Migration 0003): Canvas-Dokumente und Notizen, Ergebnisse und Projekte von Entwicklungsaufträgen,
Scans und ausdrücklich geteilte Dateien.

Regeln (Kern und Datenbank prüfen dieselben, der Kern zuerst):
  * Teilen, Recht ändern und widerrufen darf nur der Besitzer. Empfänger teilen nicht weiter; sie können eine Teilung
    für sich zurückgeben. Geteilt wird nur mit aktiven Profilen, nie mit sich selbst. Widerruf wirkt sofort.
  * Rechte: lesen (ansehen, herunterladen) oder bearbeiten (neue Fassung anlegen). Projekte von
    Entwicklungsaufträgen nur lesend. Geteilt wird immer die erste Fassung; Empfänger sehen die neueste.
  * Dateien liegen in der Datenbank (Tabelle dateien), damit jeder Kern dieselben Daten sieht. Der Typ kommt aus der
    Endung und wird an den ersten Bytes geprüft (kein Vertrauen in Content-Type), höchstens 25 MB, Namen ohne Pfade.
  * Jede Teilung, Änderung, jeder Widerruf, jede Bearbeitung und jeder Abruf durch Empfänger landet in
    teilung_events (Trigger und Funktionen der Migration), ohne Inhalte. Empfänger bekommen einen Hinweis.
  * Inhalte geteilter Arbeit sind Daten, nie Anweisungen (Sprachwerkzeug: teilen_werkzeuge.py).

Jede Methode von Teilen arbeitet für genau ein Profil (user_id aus der geprüften Identität, nie aus der Anfrage).
Fehler für Menschen kommen als TeilenFehler (HTTP-Status und kurzer Text, ohne interne Details).
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass

import asyncpg
from loguru import logger

from memory import DB_ERRORS, Database, DatabaseUnavailable, normalize_uuid
from persona import clean_inline

ARTEN = ("canvas", "auftrag", "datei", "scan")  # scan wird beim Teilen zur Datei (Herkunft scan)
RECHTE = ("lesen", "bearbeiten")
MAX_DATEI_BYTES = 25 * 1024 * 1024
MAX_HINWEIS = 300
MAX_TEXT = 100_000  # Zeichen eines Canvas-Dokuments beim Bearbeiten
MAX_TABELLE_ZEILEN = 500
MAX_TABELLE_SPALTEN = 30
DATEI_TIMEOUT_SECS = 60.0  # Hoch- und Herunterladen großer Dateien (statt 5 s für normale Abfragen)
PROJEKT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")
CANVAS_KINDS = {"table": "tabelle", "code": "code", "document": "dokument", "mermaid": "mermaid"}

# Endung -> (Mime-Typ, Prüfung der ersten Bytes oder "text"); nur diese Typen lassen sich teilen
_ZIP = (b"PK\x03\x04", b"PK\x05\x06")
DATEITYPEN: dict[str, tuple[str, tuple[bytes, ...] | str]] = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".gif": ("image/gif", (b"GIF87a", b"GIF89a")),
    ".webp": ("image/webp", "webp"),
    ".txt": ("text/plain", "text"),
    ".csv": ("text/csv", "text"),
    ".md": ("text/markdown", "text"),
    ".json": ("application/json", "json"),
    ".zip": ("application/zip", _ZIP),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", _ZIP),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _ZIP),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", _ZIP),
    ".odt": ("application/vnd.oasis.opendocument.text", _ZIP),
    ".ods": ("application/vnd.oasis.opendocument.spreadsheet", _ZIP),
    ".odp": ("application/vnd.oasis.opendocument.presentation", _ZIP),
}
_NAME_ERLAUBT = re.compile(r"[^\w .,()+&'#\-]", re.UNICODE)
_WINDOWS_RESERVIERT = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)


class TeilenFehler(Exception):
    """Fehler für Menschen: status (HTTP) und kurzer Text ohne interne Details."""

    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status = status
        self.text = text


# ---------- Dateien prüfen (reine Funktionen) ----------
def dateiname_pruefen(roh: object) -> str:
    """Sicherer Anzeigename: ohne Pfad, ohne Steuerzeichen, nur übliche Zeichen, höchstens 200 Zeichen mit Endung."""
    if not isinstance(roh, str):
        raise TeilenFehler(400, "Dateiname fehlt")
    name = unicodedata.normalize("NFC", roh)
    name = re.split(r"[\\/]", name)[-1]  # nur der letzte Teil eines Pfads
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")  # Steuer-, Format-, unsichtbare Zeichen
    name = _NAME_ERLAUBT.sub("_", " ".join(name.split())).strip(" .")
    if not name or "." not in name:
        raise TeilenFehler(400, "Dateiname ohne Endung")
    stamm, endung = name.rsplit(".", 1)
    endung = "." + endung.lower()
    if endung not in DATEITYPEN:
        raise TeilenFehler(415, "Dieser Dateityp lässt sich nicht teilen")
    stamm = stamm.strip(" .")[: 200 - len(endung)] or "Datei"
    if _WINDOWS_RESERVIERT.match(stamm):
        stamm = f"_{stamm}"
    return stamm + endung


def dateityp(name: str, inhalt: bytes) -> str:
    """Mime-Typ aus der Endung, geprüft an den ersten Bytes. Wirft TeilenFehler bei leerem, zu großem oder
    unpassendem Inhalt."""
    if not inhalt:
        raise TeilenFehler(400, "Die Datei ist leer")
    if len(inhalt) > MAX_DATEI_BYTES:
        raise TeilenFehler(413, "Die Datei ist größer als 25 MB")
    mime, pruefung = DATEITYPEN["." + name.rsplit(".", 1)[-1].lower()]
    if pruefung in ("text", "json"):
        if b"\x00" in inhalt:
            raise TeilenFehler(415, "Der Inhalt passt nicht zur Endung")
        try:
            text = inhalt.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise TeilenFehler(415, "Textdateien nur in UTF-8") from e
        if pruefung == "json":
            try:
                json.loads(text)
            except ValueError as e:
                raise TeilenFehler(415, "Der Inhalt ist kein gültiges JSON") from e
        return mime
    if pruefung == "webp":
        ok = inhalt[:4] == b"RIFF" and inhalt[8:12] == b"WEBP"
    else:
        ok = any(inhalt.startswith(m) for m in pruefung)
    if not ok:
        raise TeilenFehler(415, "Der Inhalt passt nicht zur Endung")
    return mime


def recht_pruefen(recht: object) -> str:
    if recht not in RECHTE:
        raise TeilenFehler(400, "Recht muss lesen oder bearbeiten sein")
    return recht  # type: ignore[return-value]


def hinweis_pruefen(hinweis: object) -> str | None:
    if hinweis is None or hinweis == "":
        return None
    if not isinstance(hinweis, str):
        raise TeilenFehler(400, "Nachricht muss Text sein")
    text = clean_inline(hinweis, MAX_HINWEIS)
    return text or None


def canvas_inhalt(kind: str, titel: object, daten: object) -> tuple[str, dict]:
    """Neue Fassung eines Canvas-Elements prüfen (Art bleibt die der ersten Fassung). Liefert (Titel, Daten)."""
    titel_s = clean_inline(titel, 300) if isinstance(titel, str) else ""
    if not titel_s:
        raise TeilenFehler(400, "Titel fehlt")
    if not isinstance(daten, dict):
        raise TeilenFehler(400, "Inhalt fehlt")
    if kind == "tabelle":
        spalten = daten.get("columns")
        zeilen = daten.get("rows")
        if not isinstance(spalten, list) or not isinstance(zeilen, list) or not spalten:
            raise TeilenFehler(400, "Tabelle braucht Spalten und Zeilen")
        if len(spalten) > MAX_TABELLE_SPALTEN or len(zeilen) > MAX_TABELLE_ZEILEN:
            raise TeilenFehler(413, "Tabelle zu groß")
        spalten_s = [str(c)[:200] for c in spalten]
        zeilen_s = [[str(v)[:2000] for v in z[: len(spalten_s)]] for z in zeilen if isinstance(z, list)]
        return titel_s, {"kind": kind, "title": titel_s, "columns": spalten_s, "rows": zeilen_s}
    text = daten.get("text")
    if not isinstance(text, str) or not text.strip():
        raise TeilenFehler(400, "Text fehlt")
    if len(text) > MAX_TEXT:
        raise TeilenFehler(413, "Text zu lang")
    item = {"kind": kind, "title": titel_s, "text": text.replace("\x00", "")}
    if kind == "code":
        sprache = daten.get("language")
        item["language"] = re.sub(r"[^a-z0-9+#-]", "", str(sprache or "").lower())[:20]
    return titel_s, item


class Grenze:
    """Gleitendes Fenster je Profil (im Speicher des Kerns): höchstens anzahl Ereignisse in fenster Sekunden."""

    def __init__(self, anzahl: int, fenster: float, uhr=time.monotonic):
        self.anzahl = anzahl
        self.fenster = fenster
        self._uhr = uhr
        self._ereignisse: dict[str, deque[float]] = {}

    def erlaubt(self, schluessel: str) -> bool:
        jetzt = self._uhr()
        q = self._ereignisse.setdefault(schluessel, deque())
        while q and jetzt - q[0] > self.fenster:
            q.popleft()
        if len(q) >= self.anzahl:
            return False
        q.append(jetzt)
        if len(self._ereignisse) > 1000:  # alte Profile vergessen
            for k in [k for k, v in self._ereignisse.items() if not v]:
                del self._ereignisse[k]
        return True


UPLOADS = Grenze(30, 3600.0)  # Uploads und Fassungen je Profil und Stunde
TEILUNGEN = Grenze(60, 3600.0)  # neue Teilungen und Rechteänderungen je Profil und Stunde


@dataclass(frozen=True)
class Datei:
    id: str
    name: str
    mime: str
    inhalt: bytes


def _iso(wert) -> str | None:
    return wert.isoformat() if wert is not None else None


def _db_fehler(e: BaseException, was: str) -> TeilenFehler:
    """Datenbankfehler in einen Fehler für Menschen übersetzen (Details nur als Typ ins Log)."""
    if isinstance(e, asyncpg.InsufficientPrivilegeError):
        return TeilenFehler(403, "Das ist nicht erlaubt.")
    if isinstance(e, asyncpg.UniqueViolationError):
        return TeilenFehler(409, "Das ist schon geteilt.")
    if isinstance(e, asyncpg.ProgramLimitExceededError):
        return TeilenFehler(413, "Das Kontingent für geteilte Dateien ist erreicht (500 MB).")
    if isinstance(e, (asyncpg.CheckViolationError, asyncpg.InvalidParameterValueError,
                      asyncpg.NotNullViolationError, asyncpg.ForeignKeyViolationError)):
        return TeilenFehler(400, "Die Angaben passen nicht.")
    logger.warning("Teilen: {} fehlgeschlagen ({})", was, type(e).__name__)
    return TeilenFehler(503, "Gerade nicht möglich, bitte später noch einmal.")


class Teilen:
    """Geteilte Arbeit aus Sicht genau eines Profils."""

    def __init__(self, db: Database, user_id: str):
        uid = normalize_uuid(user_id)
        if uid is None:
            raise TeilenFehler(401, "Profil unbekannt")
        if not db.configured:
            raise TeilenFehler(503, "Datenbank nicht eingerichtet")
        self.db = db
        self.user_id = uid

    def t(self, name: str) -> str:
        return self.db.t(name)

    # ---------- Datenbank-Hilfen ----------
    async def _fetch(self, was: str, sql: str, *args, timeout: float | None = None):
        try:
            return await (await self.db.pool()).fetch(sql, *args, timeout=timeout)
        except (*DB_ERRORS, DatabaseUnavailable) as e:
            raise _db_fehler(e, was) from e

    async def _fetchrow(self, was: str, sql: str, *args, timeout: float | None = None):
        try:
            return await (await self.db.pool()).fetchrow(sql, *args, timeout=timeout)
        except (*DB_ERRORS, DatabaseUnavailable) as e:
            raise _db_fehler(e, was) from e

    async def _fetchval(self, was: str, sql: str, *args, timeout: float | None = None):
        try:
            return await (await self.db.pool()).fetchval(sql, *args, timeout=timeout)
        except (*DB_ERRORS, DatabaseUnavailable) as e:
            raise _db_fehler(e, was) from e

    # ---------- Profile ----------
    async def profile(self) -> list[dict]:
        """Aktive Profile außer dem eigenen (für die Empfängerwahl): nur user_id und Name."""
        zeilen = await self._fetch(
            "Profile", f"select user_id::text as user_id, display_name from {self.t('profiles')} "
            "where aktiv and user_id <> $1::uuid order by display_name limit 500", self.user_id)
        return [{"user_id": r["user_id"], "name": clean_inline(r["display_name"], 60)} for r in zeilen]

    # ---------- Übersicht ----------
    async def liste(self) -> dict:
        """Laufende Teilungen: von mir geteilt und mit mir geteilt (ohne Inhalte)."""
        t, p = self.t("teilungen"), self.t("profiles")
        titel = f"{self.db.schema}.teilung_objekt_titel(x.art, x.canvas_id, x.datei_id, x.projekt)"
        spalten = ("x.id::text as id, x.art, x.recht, x.hinweis, x.projekt, x.canvas_id::text as canvas_id, "
                   f"x.datei_id::text as datei_id, x.erstellt_at, x.geaendert_at, {titel} as titel")
        von_mir = await self._fetch(
            "Liste", f"select {spalten}, x.empfaenger::text as person, e.display_name as name from {t} x "
            f"join {p} e on e.user_id = x.empfaenger where x.besitzer = $1::uuid and x.widerrufen_at is null "
            "order by x.erstellt_at desc limit 200", self.user_id)
        mit_mir = await self._fetch(
            "Liste", f"select {spalten}, x.besitzer::text as person, b.display_name as name from {t} x "
            f"join {p} b on b.user_id = x.besitzer where x.empfaenger = $1::uuid and x.widerrufen_at is null "
            "order by x.erstellt_at desc limit 200", self.user_id)

        def zeile(r) -> dict:
            objekt = r["canvas_id"] or r["datei_id"] or r["projekt"]
            return {"id": r["id"], "art": r["art"], "recht": r["recht"], "titel": clean_inline(r["titel"], 120),
                    "objekt": objekt, "person": r["person"], "name": clean_inline(r["name"], 60),
                    "hinweis": r["hinweis"], "erstellt_at": _iso(r["erstellt_at"]),
                    "geaendert_at": _iso(r["geaendert_at"])}

        return {"von_mir": [zeile(r) for r in von_mir], "mit_mir": [zeile(r) for r in mit_mir]}

    # ---------- Teilen, Recht ändern, widerrufen ----------
    async def teilen(self, art: object, objekt: object, empfaenger: object, recht: object = "lesen",
                     hinweis: object = None) -> dict:
        """Teilt ein eigenes Objekt mit einem aktiven Profil. Ist es schon geteilt, wird nur das Recht angepasst."""
        if art not in ARTEN:
            raise TeilenFehler(400, "Unbekannte Art")
        recht_s = recht_pruefen(recht)
        hinweis_s = hinweis_pruefen(hinweis)
        empfaenger_id = normalize_uuid(empfaenger)
        if empfaenger_id is None:
            raise TeilenFehler(400, "Empfänger fehlt")
        if empfaenger_id == self.user_id:
            raise TeilenFehler(400, "Mit sich selbst teilen geht nicht")
        if not TEILUNGEN.erlaubt(self.user_id):
            raise TeilenFehler(429, "Zu viele Teilungen in kurzer Zeit, bitte später noch einmal.")
        aktiv = await self._fetchval(
            "Empfänger", f"select aktiv from {self.t('profiles')} where user_id = $1::uuid", empfaenger_id)
        if not aktiv:
            raise TeilenFehler(404, "Dieses Profil gibt es nicht oder es ist nicht aktiv")
        canvas_id = datei_id = projekt = None
        if art == "scan":
            datei_id = await self._scan_als_datei(objekt)
            art = "datei"
        elif art == "canvas":
            canvas_id = await self._eigene_canvas_wurzel(objekt)
        elif art == "datei":
            datei_id = await self._eigene_datei_wurzel(objekt)
        else:  # auftrag: Ergebnisse eines Projekts, nur lesen
            projekt = objekt if isinstance(objekt, str) and PROJEKT_RE.fullmatch(objekt) and "--" not in objekt else None
            if projekt is None:
                raise TeilenFehler(400, "Projektname ungültig")
            if recht_s != "lesen":
                raise TeilenFehler(400, "Projekte werden nur zum Lesen geteilt")
            hat = await self._fetchval(
                "Projekt", f"select exists (select 1 from {self.t('canvas_items')} where user_id = $1::uuid "
                "and data ? 'auftrag' and (data -> 'auftrag') ->> 'projekt' = $2)", self.user_id, projekt)
            if not hat:
                raise TeilenFehler(404, "Zu diesem Projekt gibt es keine eigenen Ergebnisse")
        t = self.t("teilungen")
        vorhanden = await self._fetchrow(
            "Teilen", f"select id::text as id, recht from {t} where besitzer = $1::uuid and empfaenger = $2::uuid "
            "and widerrufen_at is null and art = $3 and canvas_id is not distinct from $4::uuid "
            "and datei_id is not distinct from $5::uuid and projekt is not distinct from $6",
            self.user_id, empfaenger_id, art, canvas_id, datei_id, projekt)
        if vorhanden:
            if vorhanden["recht"] != recht_s:
                await self._recht_setzen(vorhanden["id"], recht_s)
            return {"id": vorhanden["id"], "art": art, "recht": recht_s, "neu": False}
        neu = await self._fetchval(
            "Teilen", f"insert into {t} (besitzer, empfaenger, art, canvas_id, datei_id, projekt, recht, hinweis) "
            "values ($1::uuid, $2::uuid, $3, $4::uuid, $5::uuid, $6, $7, $8) returning id::text",
            self.user_id, empfaenger_id, art, canvas_id, datei_id, projekt, recht_s, hinweis_s)
        logger.info("Teilen: {} geteilt (Recht {})", art, recht_s)
        return {"id": neu, "art": art, "recht": recht_s, "neu": True}

    async def _eigene_canvas_wurzel(self, objekt: object) -> str:
        cid = normalize_uuid(objekt)
        if cid is None:
            raise TeilenFehler(400, "Canvas-Element fehlt")
        zeile = await self._fetchrow(
            "Canvas", f"select w.id::text as id, w.user_id::text as besitzer from {self.t('canvas_items')} c "
            f"join {self.t('canvas_items')} w on w.id = coalesce(c.parent_id, c.id) where c.id = $1::uuid", cid)
        if zeile is None or zeile["besitzer"] != self.user_id:
            raise TeilenFehler(404, "Dieses Canvas-Element gibt es nicht oder es gehört dir nicht")
        return zeile["id"]

    async def _eigene_datei_wurzel(self, objekt: object) -> str:
        did = normalize_uuid(objekt)
        if did is None:
            raise TeilenFehler(400, "Datei fehlt")
        zeile = await self._fetchrow(
            "Datei", f"select w.id::text as id, w.user_id::text as besitzer from {self.t('dateien')} d "
            f"join {self.t('dateien')} w on w.id = coalesce(d.wurzel_id, d.id) where d.id = $1::uuid", did)
        if zeile is None or zeile["besitzer"] != self.user_id:
            raise TeilenFehler(404, "Diese Datei gibt es nicht oder sie gehört dir nicht")
        return zeile["id"]

    async def _scan_als_datei(self, objekt: object) -> str:
        """Eigenen Scan aus dem Gerätedienst holen und als Datei ablegen (Herkunft scan)."""
        if not isinstance(objekt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", objekt):
            raise TeilenFehler(400, "Scan fehlt")
        try:
            from geraete_client import scan_holen  # Gerätedienst (nur im Büro); fehlt er, gibt es keine Scans
        except ImportError as e:
            raise TeilenFehler(503, "Scans sind hier nicht verfügbar") from e
        try:
            name, inhalt = await scan_holen(self.user_id, objekt)
        except Exception as e:  # noqa: BLE001 (Dienst weg, Scan unbekannt: klare Meldung statt Ausnahme)
            logger.warning("Teilen: Scan nicht abrufbar ({})", type(e).__name__)
            raise TeilenFehler(404, "Diesen Scan gibt es nicht oder er ist nicht mehr da") from e
        return await self.datei_ablegen(name if isinstance(name, str) else f"Scan-{objekt}.pdf", inhalt, "scan")

    async def _eigene_teilung(self, teilung_id: object, *, als: str) -> dict:
        tid = normalize_uuid(teilung_id)
        if tid is None:
            raise TeilenFehler(404, "Unbekannte Teilung")
        spalte = "besitzer" if als == "besitzer" else "empfaenger"
        zeile = await self._fetchrow(
            "Teilung", f"select id::text as id, art, recht, besitzer::text as besitzer, empfaenger::text as empfaenger, "
            f"canvas_id::text as canvas_id, datei_id::text as datei_id, projekt from {self.t('teilungen')} "
            f"where id = $1::uuid and {spalte} = $2::uuid and widerrufen_at is null", tid, self.user_id)
        if zeile is None:
            raise TeilenFehler(404, "Unbekannte oder schon widerrufene Teilung")
        return dict(zeile)

    async def _recht_setzen(self, teilung_id: str, recht: str) -> None:
        status = await self._fetchval(
            "Recht", f"with u as (update {self.t('teilungen')} set recht = $3 where id = $1::uuid and besitzer = $2::uuid "
            "and widerrufen_at is null returning 1) select count(*) from u", teilung_id, self.user_id, recht)
        if not status:
            raise TeilenFehler(404, "Unbekannte oder schon widerrufene Teilung")

    async def recht_aendern(self, teilung_id: object, recht: object) -> dict:
        recht_s = recht_pruefen(recht)
        teilung = await self._eigene_teilung(teilung_id, als="besitzer")
        if teilung["art"] == "auftrag" and recht_s != "lesen":
            raise TeilenFehler(400, "Projekte werden nur zum Lesen geteilt")
        if not TEILUNGEN.erlaubt(self.user_id):
            raise TeilenFehler(429, "Zu viele Änderungen in kurzer Zeit, bitte später noch einmal.")
        if teilung["recht"] != recht_s:
            await self._recht_setzen(teilung["id"], recht_s)
        return {"id": teilung["id"], "recht": recht_s}

    async def widerrufen(self, teilung_id: object) -> dict:
        """Besitzer beendet die Teilung (endgültig, sofort)."""
        teilung = await self._eigene_teilung(teilung_id, als="besitzer")
        await self._beenden(teilung["id"], "besitzer")
        logger.info("Teilen: Teilung widerrufen")
        return {"id": teilung["id"], "widerrufen": True}

    async def zurueckgeben(self, teilung_id: object) -> dict:
        """Empfänger gibt eine Teilung für sich zurück (endgültig, der Besitzer erfährt es)."""
        teilung = await self._eigene_teilung(teilung_id, als="empfaenger")
        await self._beenden(teilung["id"], "empfaenger")
        return {"id": teilung["id"], "zurueckgegeben": True}

    async def _beenden(self, teilung_id: str, rolle: str) -> None:
        spalte = "besitzer" if rolle == "besitzer" else "empfaenger"
        anzahl = await self._fetchval(
            "Widerruf", f"with u as (update {self.t('teilungen')} set widerrufen_at = now(), widerrufen_von = $2::uuid "
            f"where id = $1::uuid and {spalte} = $2::uuid and widerrufen_at is null returning 1) select count(*) from u",
            teilung_id, self.user_id)
        if not anzahl:
            raise TeilenFehler(404, "Unbekannte oder schon widerrufene Teilung")

    async def protokoll(self, teilung_id: object) -> list[dict]:
        """Verlauf einer eigenen Teilung (nur für den Besitzer): wer hat wann was getan, ohne Inhalte."""
        tid = normalize_uuid(teilung_id)
        if tid is None:
            raise TeilenFehler(404, "Unbekannte Teilung")
        gehoert = await self._fetchval(
            "Protokoll", f"select exists (select 1 from {self.t('teilungen')} where id = $1::uuid and besitzer = $2::uuid)",
            tid, self.user_id)
        if not gehoert:
            raise TeilenFehler(404, "Unbekannte Teilung")
        zeilen = await self._fetch(
            "Protokoll", f"select e.ereignis, e.at, e.details, p.display_name as durch from {self.t('teilung_events')} e "
            f"left join {self.t('profiles')} p on p.user_id = e.durch where e.teilung_id = $1::uuid "
            "order by e.at desc limit 200", tid)
        ergebnis = []
        for r in zeilen:
            details = r["details"]
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except ValueError:
                    details = {}
            ergebnis.append({"ereignis": r["ereignis"], "at": _iso(r["at"]),
                             "durch": clean_inline(r["durch"], 60) if r["durch"] else None,
                             "details": {k: v for k, v in (details or {}).items() if k in ("recht", "von", "nach")}})
        return ergebnis

    # ---------- Canvas ----------
    async def _canvas_recht(self, canvas_id: str) -> str | None:
        return await self._fetchval("Recht", f"select {self.db.schema}.canvas_recht($1::uuid, $2::uuid)",
                                    self.user_id, canvas_id)

    async def _abruf(self, *, canvas_wurzel: str | None = None, datei_wurzel: str | None = None,
                     projekt_von: tuple[str, str] | None = None) -> None:
        """Abruf durch einen Empfänger protokollieren (die Funktion prüft die Teilung selbst)."""
        t = self.t("teilungen")
        if canvas_wurzel:
            sql, args = (f"select id::text from {t} where empfaenger = $1::uuid and widerrufen_at is null "
                         "and canvas_id = $2::uuid", (self.user_id, canvas_wurzel))
        elif datei_wurzel:
            sql, args = (f"select id::text from {t} where empfaenger = $1::uuid and widerrufen_at is null "
                         "and datei_id = $2::uuid", (self.user_id, datei_wurzel))
        elif projekt_von:
            sql, args = (f"select id::text from {t} where empfaenger = $1::uuid and widerrufen_at is null "
                         "and besitzer = $2::uuid and projekt = $3", (self.user_id, *projekt_von))
        else:
            return
        try:
            teilung = await (await self.db.pool()).fetchval(sql, *args)
            if teilung:
                await (await self.db.pool()).fetchval(
                    f"select {self.db.schema}.teilung_abgerufen($1::uuid, $2::uuid)", self.user_id, teilung)
        except (*DB_ERRORS, DatabaseUnavailable) as e:  # das Protokoll darf das Lesen nicht verhindern
            logger.warning("Teilen: Abruf nicht protokolliert ({})", type(e).__name__)

    async def canvas_lesen(self, canvas_id: object) -> dict:
        """Neueste Fassung eines eigenen oder geteilten Canvas-Elements. Liefert Element, Recht und Besitzer."""
        cid = normalize_uuid(canvas_id)
        if cid is None:
            raise TeilenFehler(404, "Unbekanntes Canvas-Element")
        recht = await self._canvas_recht(cid)
        if recht is None:
            raise TeilenFehler(404, "Dieses Canvas-Element gibt es nicht oder es ist nicht mit dir geteilt")
        c = self.t("canvas_items")
        zeile = await self._fetchrow(
            "Canvas", f"with w as (select coalesce(parent_id, id) as id from {c} where id = $1::uuid) "
            f"select n.id::text as fassung, w.id::text as wurzel, n.type::text as typ, n.title, n.data, n.version, "
            f"n.created_at, r.user_id::text as besitzer, b.display_name as besitzer_name, r.data -> 'auftrag' as auftrag, "
            f"bp.display_name as bearbeiter "
            f"from w join {c} r on r.id = w.id "
            f"join lateral (select * from {c} x where x.id = w.id or x.parent_id = w.id "
            f"  order by x.version desc, x.created_at desc limit 1) n on true "
            f"left join {self.t('profiles')} b on b.user_id = r.user_id "
            f"left join {self.t('profiles')} bp on bp.user_id = n.bearbeitet_von", cid)
        if zeile is None:
            raise TeilenFehler(404, "Unbekanntes Canvas-Element")
        daten = zeile["data"]
        if isinstance(daten, str):
            daten = json.loads(daten)
        kind = CANVAS_KINDS.get(zeile["typ"], "dokument")
        item = _canvas_item_aus(daten, kind, zeile["title"])
        item["id"] = zeile["wurzel"]
        auftrag = zeile["auftrag"]
        if isinstance(auftrag, str):
            auftrag = json.loads(auftrag)
        if recht != "besitzer":
            projekt = auftrag.get("projekt") if isinstance(auftrag, dict) else None
            await self._abruf(canvas_wurzel=zeile["wurzel"])
            if isinstance(projekt, str):
                await self._abruf(projekt_von=(zeile["besitzer"], projekt))
        return {"item": item, "recht": recht, "fassung": zeile["fassung"], "version": zeile["version"],
                "besitzer": clean_inline(zeile["besitzer_name"], 60) if zeile["besitzer_name"] else None,
                "bearbeitet_von": clean_inline(zeile["bearbeiter"], 60) if zeile["bearbeiter"] else None,
                "geaendert_at": _iso(zeile["created_at"])}

    async def canvas_bearbeiten(self, canvas_id: object, titel: object, daten: object) -> dict:
        """Neue Fassung (Besitzer oder Empfänger mit Recht bearbeiten)."""
        cid = normalize_uuid(canvas_id)
        if cid is None:
            raise TeilenFehler(404, "Unbekanntes Canvas-Element")
        recht = await self._canvas_recht(cid)
        if recht not in ("besitzer", "bearbeiten"):
            raise TeilenFehler(403, "Du darfst dieses Element nur lesen" if recht else "Unbekanntes Canvas-Element")
        typ = await self._fetchval(
            "Canvas", f"select w.type::text from {self.t('canvas_items')} c join {self.t('canvas_items')} w "
            "on w.id = coalesce(c.parent_id, c.id) where c.id = $1::uuid", cid)
        titel_s, item = canvas_inhalt(CANVAS_KINDS.get(typ, "dokument"), titel, daten)
        fassung = await self._fetchval(
            "Bearbeiten", f"select {self.db.schema}.canvas_fassung_anlegen($1::uuid, $2::uuid, $3, $4::jsonb)::text",
            self.user_id, cid, titel_s, json.dumps(item, ensure_ascii=False))
        return {"fassung": fassung}

    async def projekt_dokumente(self, teilung_id: object) -> list[dict]:
        """Ergebnisdokumente eines geteilten Projekts (für Empfänger einer Projekt-Teilung, oder den Besitzer)."""
        tid = normalize_uuid(teilung_id)
        if tid is None:
            raise TeilenFehler(404, "Unbekannte Teilung")
        teilung = await self._fetchrow(
            "Projekt", f"select besitzer::text as besitzer, projekt from {self.t('teilungen')} where id = $1::uuid "
            "and art = 'auftrag' and widerrufen_at is null and (empfaenger = $2::uuid or besitzer = $2::uuid)",
            tid, self.user_id)
        if teilung is None:
            raise TeilenFehler(404, "Unbekannte oder schon widerrufene Teilung")
        zeilen = await self._fetch(
            "Projekt", f"select id::text as id, title, created_at from {self.t('canvas_items')} "
            "where user_id = $1::uuid and parent_id is null and data ? 'auftrag' "
            "and (data -> 'auftrag') ->> 'projekt' = $2 order by created_at desc limit 100",
            teilung["besitzer"], teilung["projekt"])
        return [{"id": r["id"], "titel": clean_inline(r["title"], 120), "erstellt_at": _iso(r["created_at"])}
                for r in zeilen]

    # ---------- Dateien ----------
    async def datei_ablegen(self, name: object, inhalt: bytes, herkunft: str = "upload") -> str:
        """Eigene Datei in der Datenbank ablegen (nach Sokrates-2 kopiert). Liefert die id."""
        if herkunft not in ("upload", "scan", "nas"):
            raise TeilenFehler(400, "Unbekannte Herkunft")
        name_s = dateiname_pruefen(name)
        mime = dateityp(name_s, inhalt)
        if not UPLOADS.erlaubt(self.user_id):
            raise TeilenFehler(429, "Zu viele Dateien in kurzer Zeit, bitte später noch einmal.")
        datei_id = await self._fetchval(
            "Ablegen", f"insert into {self.t('dateien')} (user_id, name, mime, groesse, sha256, herkunft, inhalt) "
            "values ($1::uuid, $2, $3, 1, repeat('0', 64), $4, $5) returning id::text",
            self.user_id, name_s, mime, herkunft, inhalt, timeout=DATEI_TIMEOUT_SECS)
        logger.info("Teilen: Datei abgelegt ({}, {} Bytes)", mime, len(inhalt))
        return datei_id

    async def datei_fassung(self, datei_id: object, name: object, inhalt: bytes) -> str:
        """Neue Fassung einer eigenen Datei oder einer mit Recht bearbeiten geteilten Datei."""
        did = normalize_uuid(datei_id)
        if did is None:
            raise TeilenFehler(404, "Unbekannte Datei")
        recht = await self._fetchval("Recht", f"select {self.db.schema}.datei_recht($1::uuid, $2::uuid)",
                                     self.user_id, did)
        if recht not in ("besitzer", "bearbeiten"):
            raise TeilenFehler(403, "Du darfst diese Datei nur lesen" if recht else "Unbekannte Datei")
        name_s = dateiname_pruefen(name)
        mime = dateityp(name_s, inhalt)
        if not UPLOADS.erlaubt(self.user_id):
            raise TeilenFehler(429, "Zu viele Dateien in kurzer Zeit, bitte später noch einmal.")
        return await self._fetchval(
            "Fassung", f"select {self.db.schema}.datei_fassung_anlegen($1::uuid, $2::uuid, $3, $4, $5)::text",
            self.user_id, did, name_s, mime, inhalt, timeout=DATEI_TIMEOUT_SECS)

    async def dateien(self) -> list[dict]:
        """Eigene Dateien (erste Fassungen) mit Angaben zur neuesten Fassung, ohne Inhalt."""
        d = self.t("dateien")
        zeilen = await self._fetch(
            "Dateien", f"select w.id::text as id, n.name, n.mime, n.groesse, w.herkunft, w.erstellt_at, "
            f"n.erstellt_at as geaendert_at, (select count(*) from {d} v where v.wurzel_id = w.id) as fassungen "
            f"from {d} w join lateral (select name, mime, groesse, erstellt_at from {d} x "
            f"  where x.id = w.id or x.wurzel_id = w.id order by x.erstellt_at desc limit 1) n on true "
            "where w.user_id = $1::uuid and w.wurzel_id is null order by w.erstellt_at desc limit 500", self.user_id)
        return [{"id": r["id"], "name": r["name"], "mime": r["mime"], "groesse": r["groesse"],
                 "herkunft": r["herkunft"], "erstellt_at": _iso(r["erstellt_at"]),
                 "geaendert_at": _iso(r["geaendert_at"]), "fassungen": int(r["fassungen"]) + 1} for r in zeilen]

    async def datei_lesen(self, datei_id: object) -> Datei:
        """Neueste Fassung einer eigenen oder geteilten Datei (protokolliert den Abruf durch Empfänger)."""
        did = normalize_uuid(datei_id)
        if did is None:
            raise TeilenFehler(404, "Unbekannte Datei")
        recht = await self._fetchval("Recht", f"select {self.db.schema}.datei_recht($1::uuid, $2::uuid)",
                                     self.user_id, did)
        if recht is None:
            raise TeilenFehler(404, "Diese Datei gibt es nicht oder sie ist nicht mit dir geteilt")
        d = self.t("dateien")
        zeile = await self._fetchrow(
            "Lesen", f"with w as (select coalesce(wurzel_id, id) as id from {d} where id = $1::uuid) "
            f"select w.id::text as wurzel, n.name, n.mime, n.inhalt from w join lateral "
            f"(select name, mime, inhalt from {d} x where x.id = w.id or x.wurzel_id = w.id "
            " order by x.erstellt_at desc limit 1) n on true", did, timeout=DATEI_TIMEOUT_SECS)
        if zeile is None:
            raise TeilenFehler(404, "Unbekannte Datei")
        if recht != "besitzer":
            await self._abruf(datei_wurzel=zeile["wurzel"])
        return Datei(id=zeile["wurzel"], name=zeile["name"], mime=zeile["mime"], inhalt=bytes(zeile["inhalt"]))

    async def datei_loeschen(self, datei_id: object) -> bool:
        did = normalize_uuid(datei_id)
        if did is None:
            raise TeilenFehler(404, "Unbekannte Datei")
        ok = await self._fetchval("Löschen", f"select {self.db.schema}.datei_loeschen($1::uuid, $2::uuid)",
                                  self.user_id, did)
        if not ok:
            raise TeilenFehler(404, "Diese Datei gibt es nicht oder sie gehört dir nicht")
        return True


def _canvas_item_aus(daten: object, kind: str, titel: str) -> dict:
    """Canvas-Element für die Oberfläche aus den gespeicherten Daten, nur bekannte Felder."""
    d = daten if isinstance(daten, dict) else {}
    title = clean_inline(d.get("title") or titel, 300) or "Ohne Titel"
    if kind == "tabelle":
        spalten = [str(c) for c in d.get("columns") or []][:MAX_TABELLE_SPALTEN]
        zeilen = [[str(v) for v in z] for z in (d.get("rows") or [])[:MAX_TABELLE_ZEILEN] if isinstance(z, list)]
        return {"kind": "tabelle", "title": title, "columns": spalten, "rows": zeilen}
    item = {"kind": kind, "title": title, "text": str(d.get("text") or "")[:MAX_TEXT]}
    if kind == "code":
        item["language"] = str(d.get("language") or "")[:20]
    return item


async def eigene_fassung(memory, canvas_id: str, titel: str, daten: dict) -> str | None:
    """Neue Fassung eines eigenen Canvas-Elements (z. B. das Ergebnis eines Entwicklungsauftrags nach einem weiteren
    Lauf). Ohne Datenbank oder ohne Migration 0003: None (der Aufrufer legt dann ein neues Element an)."""
    if not getattr(memory, "enabled", False):
        return None
    db = memory.db
    try:
        return await db.fetchval(
            f"select {db.schema}.canvas_fassung_anlegen($1::uuid, $2::uuid, $3, $4::jsonb)::text",
            memory.user_id, canvas_id, clean_inline(titel, 300) or "Ergebnis", json.dumps(daten, ensure_ascii=False))
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        logger.warning("Teilen: Fassung nicht gespeichert ({})", type(e).__name__)
        return None
