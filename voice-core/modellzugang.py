"""Modellzugang je Profil: eigener Claude-Schlüssel oder nur Hausmodelle (Tabelle model_access, Migration 0010).

  claude  Die Person hat ihren eigenen Anthropic-Schlüssel hinterlegt. Standardmodell Haiku (ein Admin kann ein
          anderes festlegen). Fällt Claude aus, übernehmen die Hausmodelle mit Hinweis (llm_anbieter.py).
  haus    Ohne eigenen Schlüssel oder vom Admin festgelegt (nur_haus): alles über LiteLLM und SearXNG, kein
          Aufruf an Anthropic, auch nicht als Rückfall.

Der Schlüssel des Kerns (ANTHROPIC_API_KEY) gehört einer Person. Er gilt nur für das Profil in
WAMO_CLAUDE_SCHLUESSEL_PROFIL (Übergang, bis sie ihn selbst hinterlegt hat), nie für andere.

Schlüssel liegen nur verschlüsselt in der Datenbank (schluessel.py, Hauptschlüssel WAMO_SCHLUESSEL_KEK nur beim
Kern). Sie werden nie geloggt, nie an die Oberfläche zurückgegeben (nur "hinterlegt, endet auf …abcd") und nie
in actions oder turns geschrieben. Eine Änderung gilt ab der nächsten Sitzung.

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
from schluessel import SchluesselFehler, Tresor, ende, format_pruefen

ZUORDNUNG_ENV = "WAMO_CLAUDE_SCHLUESSEL_PROFIL"
TEST_TIMEOUT_SECS = 10.0
VERSUCHE_JE_FENSTER = 5  # Testanfragen je Profil und Zeitfenster (Schutz vor Durchprobieren)
VERSUCHE_FENSTER_SECS = 600.0
_WORKSPACE = re.compile(r"wrkspc_[A-Za-z0-9]{6,60}")
_SPALTEN = ("nur_haus, claude_schluessel, claude_schluessel_ende, claude_workspace, claude_modell, "
            "claude_geprueft_at, stt, tts")
_versuche: dict[str, list[float]] = {}


class ZugangFehler(Exception):
    """Hinterlegen oder Löschen ist nicht möglich. Der Text ist für die Person gedacht, ohne Schlüssel."""


@dataclass(frozen=True)
class Stand:
    """Was die Oberfläche über den Zugang erfährt. Enthält nie den Schlüssel."""

    art: str  # claude | haus
    quelle: str
    hinterlegt: bool
    ende: str | None
    geprueft: str | None
    festgelegt: bool
    modell: str
    stt: str
    tts: str

    def nachricht(self, *, fehler: str | None = None, aktion: str = "status") -> dict:
        n = {"type": "modellzugang", "aktion": aktion, "art": self.art, "quelle": self.quelle,
             "hinterlegt": self.hinterlegt, "ende": self.ende, "geprueft": self.geprueft,
             "festgelegt": self.festgelegt, "modell": self.modell, "stt": self.stt, "tts": self.tts,
             "wirksam": "ab der nächsten Sitzung"}
        if fehler:
            n["fehler"] = fehler
        return n


def kern_zuordnung(user_id: str) -> bool:
    """Gilt der Schlüssel des Kerns (ANTHROPIC_API_KEY) für dieses Profil? Nur für genau die eine UUID in
    WAMO_CLAUDE_SCHLUESSEL_PROFIL, nie für andere."""
    ziel = normalize_uuid(os.getenv(ZUORDNUNG_ENV) or "")
    return bool(ziel) and ziel == normalize_uuid(user_id) and bool((os.getenv("ANTHROPIC_API_KEY") or "").strip())


async def _zeile(db: Database, user_id: str):
    return await db.fetchrow(f"select {_SPALTEN} from {db.t('model_access')} where user_id = $1::uuid", user_id)


async def zugang_laden(user_id: str, *, db: Database | None = None, tresor: Tresor | None = None) -> Zugang:
    """Zugang der Sitzung aus dem Profil. Jede Unsicherheit führt zu nur Hausmodellen (nie zu Kosten auf einem
    fremden Schlüssel): fehlende Tabelle, Datenbankfehler, fehlender Hauptschlüssel, unlesbares Chiffrat."""
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
                      claude_workspace=zeile["claude_workspace"], stt=stt, tts=tts)
    if kern_zuordnung(uid):
        return Zugang(uid, "claude", "zuordnung", claude_modell=modell,
                      claude_schluessel=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
                      claude_workspace=(os.getenv("ANTHROPIC_WORKSPACE_ID") or "").strip() or None, stt=stt, tts=tts)
    return Zugang.haus(uid, "keiner", stt=stt, tts=tts)


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
    hinterlegt = bool(zeile and zeile["claude_schluessel"] is not None)
    zuordnung = not hinterlegt and not festgelegt and kern_zuordnung(uid)
    art = "claude" if (hinterlegt or zuordnung) and not festgelegt else "haus"
    modell = (zeile["claude_modell"] if zeile else None) or os.getenv("CLAUDE_MODEL_FAST") or STANDARD_CLAUDE
    zeitpunkt = zeile["claude_geprueft_at"] if zeile else None
    geprueft = zeitpunkt.isoformat(timespec="minutes") if zeitpunkt else None
    return Stand(
        art=art,
        quelle="festgelegt" if festgelegt else "profil" if hinterlegt else "zuordnung" if zuordnung else "keiner",
        hinterlegt=hinterlegt, ende=zeile["claude_schluessel_ende"] if hinterlegt else None, geprueft=geprueft,
        festgelegt=festgelegt, modell=lesbarer_modellname(modell) if art == "claude" else "Hausmodelle",
        stt=zeile["stt"] if zeile else "soniox", tts=zeile["tts"] if zeile else "soniox",
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
        return "Anthropic lehnt den Schlüssel ab"
    except anthropic.PermissionDeniedError:
        return "Der Schlüssel darf dieses Modell nicht nutzen (Workspace oder Berechtigung prüfen)"
    except anthropic.APIStatusError as e:
        return f"Anthropic antwortet mit HTTP {e.status_code}"
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
    except asyncpg.CheckViolationError:
        # Ein Admin hat das Profil zwischen Prüfung und Speichern auf nur Hausmodelle festgelegt
        raise ZugangFehler("Ein Admin hat dieses Profil auf nur Hausmodelle festgelegt") from None
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


Pruefer = Callable[[str, str | None], Awaitable[str | None]]


async def modellzugang_nachricht(user_id: str, data: dict, senden: Callable[[dict], Awaitable[None]], *,
                                 db: Database | None = None, tresor: Tresor | None = None,
                                 testen: Pruefer = schluessel_testen) -> None:
    """Klick aus der Oberfläche (RTVI-Nachricht "modellzugang"). Die Antwort enthält nie den Schlüssel; der
    Inhalt der Nachricht wird nie geloggt."""
    aktion = data.get("aktion") if data.get("aktion") in ("hinterlegen", "loeschen", "status") else "status"
    try:
        if aktion == "hinterlegen":
            stand = await hinterlegen(user_id, data.get("schluessel"), data.get("workspace"), db=db, tresor=tresor,
                                      testen=testen)
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
        print(f"Zugang: {stand.art} ({stand.quelle}), Modell {stand.modell}, "
              + (f"Schlüssel hinterlegt, endet auf …{stand.ende}" if stand.hinterlegt else "kein eigener Schlüssel"))
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
