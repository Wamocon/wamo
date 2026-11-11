"""Modellzugang je Profil: eigener Claude-Schlüssel oder nur Hausmodelle (Tabelle model_access, Migrationen 0010, 0011).

  claude  Die Person hat ihren eigenen Anthropic-API-Schlüssel hinterlegt. Standardmodell Haiku (ein Admin kann ein
          anderes festlegen). Fällt Claude aus, übernehmen die Hausmodelle mit Hinweis (llm_anbieter.py).
  haus    Ohne eigenen Schlüssel, vom Admin festgelegt (nur_haus) oder von der Person gewählt (modell_modus
          nur_haus): alles über LiteLLM und SearXNG, kein Aufruf an Anthropic, auch nicht als Rückfall. Der Zwang
          des Admins ist stärker als die Wahl der Person.

Zwei Schlüsseltypen, getrennt gespeichert und verwendet:
  API-Schlüssel      sk-ant-api…  Gespräch, Lernen, Bildschirm (Anthropic Messages API, bezahlt nach Verbrauch).
  Claude-Code-Token  sk-ant-oat…  aus `claude setup-token` (Max-Tarif); gilt nur für Entwicklungsaufträge mit Claude
                     Code, die Messages-API nimmt ihn nicht. Er wird nur auf sein Format geprüft (eine Testanfrage
                     würde Verbrauch des Max-Kontingents erzeugen) und über claude_code_zugang_fuer() nur dem
                     Entwicklungs-Agenten dieser Sitzung für genau einen Lauf übergeben.

Der Schlüssel des Kerns (ANTHROPIC_API_KEY) gehört einer Person. Er gilt nur für das Profil in
WAMO_CLAUDE_SCHLUESSEL_PROFIL (Übergang, bis sie ihn selbst hinterlegt hat) und für Laborprofile (Testlabor, Quelle
"labor": nur das Gespräch, Standardmodell Haiku, nie für Entwicklungsaufträge), nie für andere.

Schlüssel und Token liegen nur verschlüsselt in der Datenbank (schluessel.py, Hauptschlüssel WAMO_SCHLUESSEL_KEK nur
beim Kern). Sie werden nie geloggt, nie an die Oberfläche zurückgegeben (nur "hinterlegt, endet auf …abcd") und nie
in actions, turns oder im Gedächtnis gespeichert. Eine Änderung gilt ab der nächsten Sitzung.

Für Phase P5 trägt jedes Profil stt und tts (soniox oder haus). Der Strang "Stimme" liest sie aus dem Zugang der
Sitzung (Zugang.stt, Zugang.tts) und baut danach Spracherkennung und Sprachausgabe.

Kommandozeile für den Betrieb (im Ordner voice-core/, mit der Umgebung des Kerns):
  uv run python modellzugang.py kek-erzeugen          # neuer Hauptschlüssel für WAMO_SCHLUESSEL_KEK
  uv run python modellzugang.py hinterlegen --user-id <uuid> [--workspace wrkspc_...]   # liest den Schlüssel verdeckt
  uv run python modellzugang.py status --user-id <uuid>
  uv run python modellzugang.py loeschen --user-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anthropic
import asyncpg
from loguru import logger

from llm_anbieter import STANDARD_CLAUDE, Zugang, lesbarer_modellname
from memory import DB_ERRORS, Database, DatabaseUnavailable, admin_database, normalize_uuid, shared_database
from schluessel import CODE_TOKEN, SCHLUESSEL, SchluesselFehler, Tresor, ende, format_pruefen, token_format_pruefen

ZUORDNUNG_ENV = "WAMO_CLAUDE_SCHLUESSEL_PROFIL"
LABOR_ARTEN = ("platz", "schaufenster")  # profiles.labor_art (Migration 0018); nur diese Werte geben den Server-Schlüssel frei
TEST_TIMEOUT_SECS = 10.0
VERSUCHE_JE_FENSTER = 5  # Testanfragen je Profil und Zeitfenster (Schutz vor Durchprobieren)
VERSUCHE_FENSTER_SECS = 600.0
MODI = ("automatisch", "nur_haus")
SCHLUESSELARTEN = ("api", "code")
_WORKSPACE = re.compile(r"wrkspc_[A-Za-z0-9]{6,60}")
_SPALTEN_ALT = ("nur_haus, claude_schluessel, claude_schluessel_ende, claude_workspace, claude_modell, "
                "claude_geprueft_at, stt, tts")
# Ab Migration 0011: Wahl der Person und Angaben zum Claude-Code-Token (das Chiffrat selbst liest nur
# claude_code_zugang_fuer, nie diese Abfrage)
_SPALTEN = _SPALTEN_ALT + ", modell_modus, claude_code_token_ende, claude_code_token_at"
_versuche: dict[str, list[float]] = {}


class ZugangFehler(Exception):
    """Hinterlegen oder Löschen ist nicht möglich. Der Text ist für die Person gedacht, ohne Schlüssel."""


@dataclass(frozen=True)
class Stand:
    """Was die Oberfläche über den Zugang erfährt. Enthält nie den Schlüssel."""

    art: str  # claude | haus (wirksam: nach Zwang des Admins und Wahl der Person)
    quelle: str  # profil | zuordnung | festgelegt | wahl | keiner
    hinterlegt: bool
    ende: str | None
    geprueft: str | None
    festgelegt: bool  # der Admin hat nur Hausmodelle festgelegt: die Wahl der Person ist gesperrt
    modell: str
    stt: str
    tts: str
    modus: str = "automatisch"  # Wahl der Person (automatisch | nur_haus), unabhängig vom Zwang des Admins
    code_hinterlegt: bool = False  # Claude-Code-Token für Entwicklungsaufträge
    code_ende: str | None = None
    code_gesetzt: str | None = None

    def nachricht(self, *, fehler: str | None = None, aktion: str = "status") -> dict:
        n = {"type": "modellzugang", "aktion": aktion, "art": self.art, "quelle": self.quelle,
             "hinterlegt": self.hinterlegt, "ende": self.ende, "geprueft": self.geprueft,
             "festgelegt": self.festgelegt, "modell": self.modell, "stt": self.stt, "tts": self.tts,
             "modus": self.modus, "code_hinterlegt": self.code_hinterlegt, "code_ende": self.code_ende,
             "code_gesetzt": self.code_gesetzt, "wirksam": "ab der nächsten Sitzung"}
        if fehler:
            n["fehler"] = fehler
        return n


def kern_zuordnung(user_id: str) -> bool:
    """Gilt der Schlüssel des Kerns (ANTHROPIC_API_KEY) für dieses Profil? Nur für genau die eine UUID in
    WAMO_CLAUDE_SCHLUESSEL_PROFIL, nie für andere."""
    ziel = normalize_uuid(os.getenv(ZUORDNUNG_ENV) or "")
    return bool(ziel) and ziel == normalize_uuid(user_id) and bool((os.getenv("ANTHROPIC_API_KEY") or "").strip())


async def _zeile(db: Database, user_id: str):
    try:
        return await db.fetchrow(f"select {_SPALTEN} from {db.t('model_access')} where user_id = $1::uuid", user_id)
    except asyncpg.UndefinedColumnError:
        # Migration 0011 fehlt noch in diesem Schema: der Kern darf deshalb nicht alle Profile auf Hausmodelle
        # werfen. Ohne die Spalten gilt der Stand von 0010 (keine Wahl der Person, kein Claude-Code-Token).
        logger.warning("model_access ohne Migration 0011: Wahl der Person und Claude-Code-Token stehen nicht zur Verfügung")
        return await db.fetchrow(f"select {_SPALTEN_ALT} from {db.t('model_access')} where user_id = $1::uuid", user_id)


def _verletzt(fehler: Exception, constraint: str) -> bool:
    """Hat die Datenbank genau diese Prüfung (check constraint) abgelehnt?"""
    return constraint == (getattr(fehler, "constraint_name", None) or "") or constraint in str(fehler)


def _modus(zeile) -> str:
    wert = zeile.get("modell_modus") if zeile else None
    return wert if wert in MODI else "automatisch"


def _hat_code_token(zeile) -> bool:
    return bool(zeile and zeile.get("claude_code_token_ende"))


async def zugang_laden(user_id: str, *, db: Database | None = None, tresor: Tresor | None = None,
                       labor_art: str | None = None) -> Zugang:
    """Zugang der Sitzung aus dem Profil. Jede Unsicherheit führt zu nur Hausmodellen (nie zu Kosten auf einem
    fremden Schlüssel): fehlende Tabelle, Datenbankfehler, fehlender Hauptschlüssel, unlesbares Chiffrat.
    Reihenfolge: Zwang des Admins (nur_haus), dann die Wahl der Person (modell_modus), dann der eigene Schlüssel, dann
    der Server-Schlüssel für Laborprofile (labor_art), dann der Schlüssel des Kerns für das zugeordnete Profil.
    labor_art kommt allein aus der Identität des Kerns (profiles.labor_art, Middleware), nie aus dem Gespräch, und ist
    deshalb nur als Schlüsselwort möglich. Nur platz und schaufenster zählen; jeder andere Wert ist kein Laborprofil."""
    uid = normalize_uuid(user_id)
    if uid is None:
        return Zugang.haus(str(user_id), "fehler", "Profil-ID ungültig")
    db = db or shared_database()
    zeile = None
    if db.configured:
        try:
            zeile = await _zeile(db, uid)
        except (DatabaseUnavailable, *DB_ERRORS) as e:
            logger.error("Modellzugang für Profil {} nicht lesbar ({}): nur Hausmodelle", uid[:8], type(e).__name__)
            return Zugang.haus(uid, "fehler", "Modellzugang nicht lesbar")
    stt = zeile["stt"] if zeile else "soniox"
    tts = zeile["tts"] if zeile else "soniox"
    if zeile and zeile["nur_haus"]:
        return Zugang.haus(uid, "festgelegt", stt=stt, tts=tts)
    if _modus(zeile) == "nur_haus":
        # Die Person hat nur Hausmodelle gewählt: ohne Schlüssel im Zugang, ohne Claude Code (nichts geht in die Cloud)
        return Zugang.haus(uid, "wahl", stt=stt, tts=tts)
    code = _hat_code_token(zeile)
    modell = (zeile["claude_modell"] if zeile else None) or None
    if zeile and zeile["claude_schluessel"] is not None:
        tresor = tresor if tresor is not None else _tresor_oder_none()
        if tresor is None:
            logger.error("WAMO_SCHLUESSEL_KEK fehlt: Schlüssel von Profil {} nicht nutzbar, nur Hausmodelle", uid[:8])
            return Zugang.haus(uid, "fehler", "Hauptschlüssel fehlt", stt=stt, tts=tts)
        try:
            schluessel = tresor.entschluesseln(uid, zeile["claude_schluessel"])
        except SchluesselFehler as e:
            logger.error("Schlüssel von Profil {} nicht lesbar ({}): nur Hausmodelle", uid[:8], e)
            return Zugang.haus(uid, "fehler", "Schlüssel nicht lesbar", stt=stt, tts=tts)
        return Zugang(uid, "claude", "profil", claude_modell=modell, claude_schluessel=schluessel,
                      claude_workspace=zeile["claude_workspace"], stt=stt, tts=tts, claude_code=code)
    server_schluessel = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if labor_art in LABOR_ARTEN and server_schluessel:
        # Testlabor: Laborprofile führen das Gespräch über den Server-Schlüssel (ANTHROPIC_API_KEY), Standardmodell Haiku
        # (oder das vom Admin gesetzte Modell des Profils). Nur das Gespräch: kein Claude-Code-Token (claude_code bleibt
        # falsch) und boss.entwicklung_quelle liefert für labor immer "keine". Ohne Schlüssel bleibt es bei Hausmodellen.
        return Zugang(uid, "claude", "labor", claude_modell=modell, claude_schluessel=server_schluessel,
                      claude_workspace=(os.getenv("ANTHROPIC_WORKSPACE_ID") or "").strip() or None, stt=stt, tts=tts)
    if kern_zuordnung(uid):
        return Zugang(uid, "claude", "zuordnung", claude_modell=modell,
                      claude_schluessel=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
                      claude_workspace=(os.getenv("ANTHROPIC_WORKSPACE_ID") or "").strip() or None, stt=stt, tts=tts,
                      claude_code=code)
    return Zugang.haus(uid, "keiner", stt=stt, tts=tts, claude_code=code)


def _tresor_oder_none() -> Tresor | None:
    try:
        return Tresor.from_env()
    except SchluesselFehler as e:
        logger.error("Hauptschlüssel unbrauchbar: {}", e)
        return None


async def stand_laden(user_id: str, *, db: Database | None = None) -> Stand:
    """Stand für die Oberfläche (ohne Entschlüsseln: es geht nur um "hinterlegt" und die letzten Zeichen)."""
    uid = normalize_uuid(user_id) or ""
    db = db or shared_database()
    zeile = await _zeile(db, uid) if db.configured and uid else None
    festgelegt = bool(zeile and zeile["nur_haus"])
    modus = _modus(zeile)
    wahl_haus = modus == "nur_haus" and not festgelegt
    hinterlegt = bool(zeile and zeile["claude_schluessel"] is not None)
    zuordnung = not hinterlegt and not festgelegt and kern_zuordnung(uid)
    art = "claude" if (hinterlegt or zuordnung) and not festgelegt and not wahl_haus else "haus"
    modell = (zeile["claude_modell"] if zeile else None) or os.getenv("CLAUDE_MODEL_FAST") or STANDARD_CLAUDE
    zeitpunkt = zeile["claude_geprueft_at"] if zeile else None
    geprueft = zeitpunkt.isoformat(timespec="minutes") if zeitpunkt else None
    code = _hat_code_token(zeile)
    code_zeit = zeile.get("claude_code_token_at") if zeile else None
    return Stand(
        art=art,
        quelle=("festgelegt" if festgelegt else "wahl" if wahl_haus else "profil" if hinterlegt
                else "zuordnung" if zuordnung else "keiner"),
        hinterlegt=hinterlegt, ende=zeile["claude_schluessel_ende"] if hinterlegt else None, geprueft=geprueft,
        festgelegt=festgelegt, modell=lesbarer_modellname(modell) if art == "claude" else "Hausmodelle",
        stt=zeile["stt"] if zeile else "soniox", tts=zeile["tts"] if zeile else "soniox",
        modus=modus, code_hinterlegt=code, code_ende=zeile["claude_code_token_ende"] if code else None,
        code_gesetzt=code_zeit.isoformat(timespec="minutes") if code and code_zeit else None,
    )


async def schluessel_testen(schluessel: str, workspace: str | None = None) -> str | None:
    """Kurze Testanfrage (ein Ausgabe-Token mit Haiku). None, wenn der Schlüssel funktioniert; sonst ein Grund
    für die Person, ohne Schlüssel."""
    kopf = {"anthropic-workspace-id": workspace} if workspace else None
    client = anthropic.AsyncAnthropic(api_key=schluessel, default_headers=kopf, max_retries=0,
                                      timeout=anthropic.Timeout(TEST_TIMEOUT_SECS, connect=3.0))
    try:
        await client.messages.create(model=os.getenv("CLAUDE_MODEL_FAST") or STANDARD_CLAUDE, max_tokens=1,
                                     messages=[{"role": "user", "content": "ping"}])
        return None
    except anthropic.AuthenticationError:
        return ("Anthropic lehnt den Schlüssel ab (401): Er ist ungültig, widerrufen oder gehört zu einem anderen "
                "Konto. Bitte in der Anthropic-Konsole prüfen oder einen neuen erzeugen")
    except anthropic.PermissionDeniedError:
        return "Der Schlüssel darf dieses Modell nicht nutzen (403): Workspace oder Berechtigung prüfen"
    except anthropic.APIStatusError as e:
        return f"Anthropic antwortet mit HTTP {e.status_code}"
    except anthropic.APITimeoutError:
        return f"Anthropic hat nicht rechtzeitig geantwortet (Zeitüberschreitung nach {TEST_TIMEOUT_SECS:.0f} s)"
    except anthropic.APIConnectionError:
        return "Anthropic ist gerade nicht erreichbar"
    except anthropic.APIError as e:
        return f"Anthropic-Fehler ({type(e).__name__})"
    finally:
        await client.close()


def _versuch_erlaubt(uid: str, jetzt: float | None = None) -> bool:
    jetzt = time.monotonic() if jetzt is None else jetzt
    liste = [t for t in _versuche.get(uid, []) if jetzt - t < VERSUCHE_FENSTER_SECS]
    if len(liste) >= VERSUCHE_JE_FENSTER:
        _versuche[uid] = liste
        return False
    _versuche[uid] = [*liste, jetzt]
    return True


async def hinterlegen(user_id: str, schluessel_roh: object, workspace_roh: object = None, *,
                      db: Database | None = None, tresor: Tresor | None = None,
                      testen: Callable[[str, str | None], Awaitable[str | None]] = schluessel_testen) -> Stand:
    # Ein Claude-Code-Token gehört ins andere Feld: format_pruefen sagt es der Person in einem Satz
    """Prüft, verschlüsselt und speichert den eigenen Schlüssel. Wirft ZugangFehler mit einem Grund für die
    Person. Der Schlüssel verlässt diese Funktion nur als Chiffrat."""
    uid = normalize_uuid(user_id)
    if uid is None:
        raise ZugangFehler("Profil ungültig")
    db = db or shared_database()
    if not db.configured:
        raise ZugangFehler("Keine Datenbank: der Schlüssel kann nicht gespeichert werden")
    tresor = tresor if tresor is not None else _tresor_oder_none()
    if tresor is None:
        raise ZugangFehler("Der Kern hat keinen Hauptschlüssel (WAMO_SCHLUESSEL_KEK); bitte an den Admin wenden")
    try:
        schluessel = format_pruefen(schluessel_roh)
    except SchluesselFehler as e:
        raise ZugangFehler(str(e)) from None
    workspace = str(workspace_roh or "").strip() or None
    if workspace and not _WORKSPACE.fullmatch(workspace):
        raise ZugangFehler("Die Workspace-ID beginnt mit wrkspc_")
    zeile = await _zeile(db, uid)
    if zeile and zeile["nur_haus"]:
        raise ZugangFehler("Ein Admin hat dieses Profil auf nur Hausmodelle festgelegt")
    if not _versuch_erlaubt(uid):
        raise ZugangFehler("Zu viele Versuche, bitte in zehn Minuten noch einmal")
    if grund := await testen(schluessel, workspace):
        raise ZugangFehler(grund)
    chiffre = tresor.verschluesseln(uid, schluessel)
    try:
        await db.execute(
            f"insert into {db.t('model_access')} (user_id, claude_schluessel, claude_schluessel_ende, "
            "claude_workspace, claude_geprueft_at) values ($1::uuid, $2, $3, $4, now()) on conflict (user_id) "
            "do update set claude_schluessel = excluded.claude_schluessel, "
            "claude_schluessel_ende = excluded.claude_schluessel_ende, claude_workspace = excluded.claude_workspace, "
            "claude_geprueft_at = excluded.claude_geprueft_at",
            uid, chiffre, ende(schluessel), workspace,
        )
    except asyncpg.CheckViolationError as e:
        if _verletzt(e, "model_access_nur_haus_ohne_schluessel"):
            # Ein Admin hat das Profil zwischen Prüfung und Speichern auf nur Hausmodelle festgelegt
            raise ZugangFehler("Ein Admin hat dieses Profil auf nur Hausmodelle festgelegt") from None
        raise ZugangFehler("Die Datenbank hat den Schlüssel abgelehnt (Länge, Format oder Workspace)") from None
    logger.info("Claude-Schlüssel für Profil {} hinterlegt", uid[:8])  # nie auch nur Teile des Schlüssels loggen
    return await stand_laden(uid, db=db)


async def loeschen(user_id: str, *, db: Database | None = None) -> Stand:
    uid = normalize_uuid(user_id)
    if uid is None:
        raise ZugangFehler("Profil ungültig")
    db = db or shared_database()
    if not db.configured:
        raise ZugangFehler("Keine Datenbank")
    await db.execute(
        f"update {db.t('model_access')} set claude_schluessel = null, claude_schluessel_ende = null, "
        "claude_workspace = null, claude_geprueft_at = null where user_id = $1::uuid", uid,
    )
    logger.info("Claude-Schlüssel für Profil {} gelöscht", uid[:8])
    return await stand_laden(uid, db=db)


async def code_token_hinterlegen(user_id: str, token_roh: object, *, db: Database | None = None,
                                 tresor: Tresor | None = None) -> Stand:
    """Prüft das Format, verschlüsselt und speichert den Claude-Code-Token (nur für Entwicklungsaufträge). Es gibt
    keine Testanfrage: sie würde Verbrauch des Max-Kontingents erzeugen. Wirft ZugangFehler mit einem Grund für die
    Person; der Token verlässt diese Funktion nur als Chiffrat."""
    uid = normalize_uuid(user_id)
    if uid is None:
        raise ZugangFehler("Profil ungültig")
    db = db or shared_database()
    if not db.configured:
        raise ZugangFehler("Keine Datenbank: der Token kann nicht gespeichert werden")
    tresor = tresor if tresor is not None else _tresor_oder_none()
    if tresor is None:
        raise ZugangFehler("Der Kern hat keinen Hauptschlüssel (WAMO_SCHLUESSEL_KEK); bitte an den Admin wenden")
    try:
        token = token_format_pruefen(token_roh)
    except SchluesselFehler as e:
        raise ZugangFehler(str(e)) from None
    zeile = await _zeile(db, uid)
    if zeile and zeile["nur_haus"]:
        raise ZugangFehler("Ein Admin hat dieses Profil auf nur Hausmodelle festgelegt")
    chiffre = tresor.verschluesseln(uid, token, CODE_TOKEN)
    try:
        await db.execute(
            f"insert into {db.t('model_access')} (user_id, claude_code_token, claude_code_token_ende, "
            "claude_code_token_at) values ($1::uuid, $2, $3, now()) on conflict (user_id) "
            "do update set claude_code_token = excluded.claude_code_token, "
            "claude_code_token_ende = excluded.claude_code_token_ende, "
            "claude_code_token_at = excluded.claude_code_token_at",
            uid, chiffre, ende(token),
        )
    except asyncpg.CheckViolationError as e:
        if _verletzt(e, "model_access_nur_haus_ohne_codetoken"):
            raise ZugangFehler("Ein Admin hat dieses Profil auf nur Hausmodelle festgelegt") from None
        raise ZugangFehler("Die Datenbank hat den Token abgelehnt (Länge oder Format)") from None
    except asyncpg.UndefinedColumnError:
        raise ZugangFehler("Der Claude-Code-Token ist in dieser Datenbank noch nicht vorgesehen (Migration 0011)") from None
    logger.info("Claude-Code-Token für Profil {} hinterlegt", uid[:8])  # nie auch nur Teile des Tokens loggen
    return await stand_laden(uid, db=db)


async def code_token_loeschen(user_id: str, *, db: Database | None = None) -> Stand:
    uid = normalize_uuid(user_id)
    if uid is None:
        raise ZugangFehler("Profil ungültig")
    db = db or shared_database()
    if not db.configured:
        raise ZugangFehler("Keine Datenbank")
    await db.execute(
        f"update {db.t('model_access')} set claude_code_token = null, claude_code_token_ende = null, "
        "claude_code_token_at = null where user_id = $1::uuid", uid,
    )
    logger.info("Claude-Code-Token für Profil {} gelöscht", uid[:8])
    return await stand_laden(uid, db=db)


async def modus_setzen(user_id: str, modus_roh: object, *, db: Database | None = None) -> Stand:
    """Die Wahl der Person: automatisch (der Boss wählt unter allen erlaubten Modellen) oder nur_haus. Sie gilt ab
    der nächsten Sitzung und lässt hinterlegte Schlüssel unberührt. Hat ein Admin das Profil auf nur Hausmodelle
    festgelegt, ist die Wahl gesperrt (der Zwang ist stärker)."""
    uid = normalize_uuid(user_id)
    if uid is None:
        raise ZugangFehler("Profil ungültig")
    if modus_roh not in MODI:
        raise ZugangFehler("Unbekannte Wahl (automatisch oder nur_haus)")
    db = db or shared_database()
    if not db.configured:
        raise ZugangFehler("Keine Datenbank: die Wahl kann nicht gespeichert werden")
    zeile = await _zeile(db, uid)
    if zeile and zeile["nur_haus"]:
        raise ZugangFehler("Ein Admin hat dieses Profil auf nur Hausmodelle festgelegt; die Wahl ist gesperrt")
    try:
        await db.execute(
            f"insert into {db.t('model_access')} (user_id, modell_modus) values ($1::uuid, $2) "
            "on conflict (user_id) do update set modell_modus = excluded.modell_modus",
            uid, modus_roh,
        )
    except asyncpg.UndefinedColumnError:
        raise ZugangFehler("Die Wahl ist in dieser Datenbank noch nicht vorgesehen (Migration 0011)") from None
    logger.info("Modellwahl für Profil {}: {}", uid[:8], modus_roh)
    return await stand_laden(uid, db=db)


async def claude_code_zugang_fuer(user_id: str, *, db: Database | None = None,
                                  tresor: Tresor | None = None) -> dict[str, str] | None:
    """Der Zugang des Profils für GENAU EINEN Lauf eines Entwicklungsauftrags mit Claude Code, entschlüsselt:
    {"typ": "oauth" | "api", "wert": "<Token oder Schlüssel>"}, sonst None.
      oauth  Der Claude-Code-Token (Max-Tarif, Kontingent des Abos, Standardmodell Sonnet). Hat Vorrang.
      api    Der eigene API-Schlüssel (geht vom Guthaben ab, Standardmodell Haiku), wenn kein Token hinterlegt ist.
    Aufruf nur im Kern und nur durch den Entwicklungs-Agenten der Sitzung dieses Profils (bot.py bindet die Profil-ID
    fest), kurz vor dem Start eines Laufs; der Wert wird nicht im Zugang, in keinem Objekt und nie im Log gehalten.
    None, wenn nichts hinterlegt ist, der Admin oder die Person nur Hausmodelle verlangt (nichts geht in die Cloud),
    der Hauptschlüssel fehlt oder ein Chiffrat nicht lesbar ist: dann startet kein Lauf. Der Schlüssel des Kerns
    (ANTHROPIC_API_KEY) gehört nicht dazu: Das Profil mit der Zuordnung nutzt weiter den Zugang der Sandbox."""
    uid = normalize_uuid(user_id)
    if uid is None:
        return None
    db = db or shared_database()
    if not db.configured:
        return None
    try:
        zeile = await db.fetchrow(
            f"select nur_haus, modell_modus, claude_code_token, claude_schluessel from {db.t('model_access')} "
            "where user_id = $1::uuid", uid)
    except asyncpg.UndefinedColumnError:
        return None
    except (DatabaseUnavailable, *DB_ERRORS) as e:
        logger.error("Zugang für Entwicklungsauftrag von Profil {} nicht lesbar ({})", uid[:8], type(e).__name__)
        return None
    if not zeile or zeile["nur_haus"] or _modus(zeile) == "nur_haus":
        return None
    tresor = tresor if tresor is not None else _tresor_oder_none()
    if tresor is None:
        logger.error("WAMO_SCHLUESSEL_KEK fehlt: Zugang von Profil {} für Entwicklungsaufträge nicht nutzbar", uid[:8])
        return None
    for typ, spalte, zweck in (("oauth", "claude_code_token", CODE_TOKEN), ("api", "claude_schluessel", SCHLUESSEL)):
        if zeile[spalte] is None:
            continue
        try:
            return {"typ": typ, "wert": tresor.entschluesseln(uid, zeile[spalte], zweck)}
        except SchluesselFehler as e:
            # nicht stillschweigend auf den anderen Typ ausweichen: ein kaputtes Chiffrat soll auffallen
            logger.error("Zugang ({}) von Profil {} nicht lesbar ({})", typ, uid[:8], e)
            return None
    return None


Pruefer = Callable[[str, str | None], Awaitable[str | None]]


async def modellzugang_nachricht(user_id: str, data: dict, senden: Callable[[dict], Awaitable[None]], *,
                                 db: Database | None = None, tresor: Tresor | None = None,
                                 testen: Pruefer = schluessel_testen) -> None:
    """Klick aus der Oberfläche (RTVI-Nachricht "modellzugang"). Aktionen: status, modus (Wahl der Person),
    hinterlegen und loeschen (mit schluesselart api oder code). Die Antwort enthält nie einen Schlüssel oder Token;
    der Inhalt der Nachricht wird nie geloggt."""
    aktion = data.get("aktion") if data.get("aktion") in ("hinterlegen", "loeschen", "status", "modus") else "status"
    art = data.get("schluesselart") if data.get("schluesselart") in SCHLUESSELARTEN else "api"
    try:
        if aktion == "modus":
            stand = await modus_setzen(user_id, data.get("modus"), db=db)
        elif aktion == "hinterlegen" and art == "code":
            stand = await code_token_hinterlegen(user_id, data.get("schluessel"), db=db, tresor=tresor)
        elif aktion == "hinterlegen":
            stand = await hinterlegen(user_id, data.get("schluessel"), data.get("workspace"), db=db, tresor=tresor,
                                      testen=testen)
        elif aktion == "loeschen" and art == "code":
            stand = await code_token_loeschen(user_id, db=db)
        elif aktion == "loeschen":
            stand = await loeschen(user_id, db=db)
        else:
            aktion, stand = "status", await stand_laden(user_id, db=db)
        await senden(stand.nachricht(aktion=aktion))
    except ZugangFehler as e:
        logger.info("Modellzugang {} für Profil {} abgelehnt: {}", aktion, str(user_id)[:8], e)
        try:
            stand = await stand_laden(user_id, db=db)
            await senden(stand.nachricht(aktion=aktion, fehler=str(e)))
        except (DatabaseUnavailable, *DB_ERRORS):
            await senden({"type": "modellzugang", "aktion": aktion, "fehler": str(e)})
    except (DatabaseUnavailable, *DB_ERRORS) as e:
        logger.error("Modellzugang {} für Profil {}: Datenbank ({})", aktion, str(user_id)[:8], type(e).__name__)
        await senden({"type": "modellzugang", "aktion": aktion, "fehler": "Datenbank gerade nicht erreichbar"})
    except Exception as e:  # nie ohne Antwort an die Oberfläche (sie wartet sonst bis zur Zeitgrenze)
        logger.error("Modellzugang {} für Profil {}: unerwarteter Fehler ({})", aktion, str(user_id)[:8],
                     type(e).__name__)
        await senden({"type": "modellzugang", "aktion": aktion, "fehler": "Unerwarteter Fehler, bitte später noch einmal"})


# ---------- Verwaltung (wamo_admin, nur wamo_admin.py) ----------


async def admin_festlegen(user_id: str, *, nur_haus: bool | None = None, modell: str | None = None,
                          stt: str | None = None, tts: str | None = None, db: Database | None = None) -> None:
    """Admin: nur Hausmodelle an oder aus, Claude-Modell, Stimme. Der Wechsel auf nur_haus räumt den Schlüssel
    in der Datenbank (Trigger model_access_guard); die Verwaltung liest das Chiffrat nie."""
    uid = normalize_uuid(user_id)
    if uid is None:
        raise ZugangFehler("user_id ist keine UUID")
    if modell is not None and not re.fullmatch(r"claude-[a-z0-9-]{3,60}", modell):
        raise ZugangFehler("Modell muss eine Claude-ID sein, z. B. claude-haiku-5-5")
    for wert in (stt, tts):
        if wert is not None and wert not in ("soniox", "haus"):
            raise ZugangFehler("stt und tts: soniox oder haus")
    felder = {k: v for k, v in (("nur_haus", nur_haus), ("claude_modell", modell), ("stt", stt), ("tts", tts))
              if v is not None}
    if not felder:
        raise ZugangFehler("Nichts zu ändern")
    db = db or admin_database()
    spalten = ["user_id", *felder]
    platz = ", ".join(f"${i + 1}" for i in range(len(spalten)))
    setzen = ", ".join(f"{k} = excluded.{k}" for k in felder)
    await db.execute(
        f"insert into {db.t('model_access')} ({', '.join(spalten)}) values ({platz}) "
        f"on conflict (user_id) do update set {setzen}",
        uid, *felder.values(),
    )


async def admin_liste(db: Database | None = None) -> list[dict]:
    db = db or admin_database()
    zeilen = await db.fetch(
        f"select p.user_id::text as user_id, p.display_name, coalesce(m.nur_haus, false) as nur_haus, "
        "m.claude_schluessel_ende, m.claude_modell, coalesce(m.stt, 'soniox') as stt, "
        "coalesce(m.tts, 'soniox') as tts "
        f"from {db.t('profiles')} p left join {db.t('model_access')} m on m.user_id = p.user_id order by p.display_name"
    )
    return [dict(z) for z in zeilen]


# ---------- Kommandozeile für den Betrieb (Umgebung des Kerns) ----------


async def _cli(args: argparse.Namespace) -> int:
    db = shared_database()
    try:
        if args.befehl == "hinterlegen":
            schluessel = getpass.getpass("Anthropic-Schlüssel (wird nicht angezeigt): ")
            stand = await hinterlegen(args.user_id, schluessel, args.workspace, db=db)
            del schluessel
        elif args.befehl == "loeschen":
            stand = await loeschen(args.user_id, db=db)
        else:
            stand = await stand_laden(args.user_id, db=db)
        print(f"Zugang: {stand.art} ({stand.quelle}), Modell {stand.modell}, Wahl {stand.modus}, "
              + (f"Schlüssel hinterlegt, endet auf …{stand.ende}" if stand.hinterlegt else "kein eigener Schlüssel")
              + (f", Claude-Code-Token hinterlegt, endet auf …{stand.code_ende}" if stand.code_hinterlegt
                 else ", kein Claude-Code-Token"))
        return 0
    except ZugangFehler as e:
        print(f"Abgelehnt: {e}", file=sys.stderr)
        return 1
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="modellzugang", description="Modellzugang je Profil (Betrieb, Kern)")
    sub = p.add_subparsers(dest="befehl", required=True)
    sub.add_parser("kek-erzeugen", help="neuen Hauptschlüssel für WAMO_SCHLUESSEL_KEK ausgeben")
    for name in ("hinterlegen", "status", "loeschen"):
        b = sub.add_parser(name)
        b.add_argument("--user-id", required=True)
        if name == "hinterlegen":
            b.add_argument("--workspace", help="nur bei Organisationsschlüsseln: wrkspc_...")
    args = p.parse_args(argv)
    # diagnose=False: loguru schriebe sonst bei Ausnahmen die Werte lokaler Variablen (auch Schlüssel) aus
    logger.remove()
    logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)
    if args.befehl == "kek-erzeugen":
        from schluessel import kek_erzeugen

        print(kek_erzeugen())
        return 0
    hier = os.path.dirname(os.path.abspath(__file__))
    for pfad in (os.path.join(hier, ".env"), os.path.join(hier, "..", ".env")):
        if os.path.isfile(pfad):
            load_dotenv(pfad, override=False)
    return asyncio.run(_cli(args))


if __name__ == "__main__":
    sys.exit(main())
