"""Geräteverwaltung für Admins: Geräte der Profile freigeben, sperren, entsperren und widerrufen (Migration 0014).

  GET  /wamo/api/geraete/verwaltung          alle persönlichen Geräte aller Profile (nur Rolle admin), ohne Geheimnisse
  POST /wamo/api/geraete/verwaltung/aktion   {"kennung", "aktion", "grund", "fingerabdruck"}

Bisher gab es für ein beantragtes Gerät nur SQL. Die Regeln selbst stehen weiter in der Datenbank (Trigger devices_guard
aus 0002 und die Funktionen aus 0014): feste Übergänge, nur ein aktiver Admin als Akteur, Verlauf in device_events,
Hinweis an den Besitzer. Dieses Modul prüft davor, was die Datenbank nicht prüfen kann (sie sieht kein Token):

  * Anmeldung wie bei allen Routen: Token mit zweitem Faktor, Höchstalter, aktives Profil (wamo_api.identitaet).
  * Rolle admin. Die Rolle kommt aus dem Profil-Cache der Zugangskontrolle (bis 60 Sekunden alt); die Datenbank
    prüft sie bei jeder Aktion noch einmal.
  * Ändernde Aktionen brauchen einen frischen zweiten Faktor: der Code wurde höchstens 120 Sekunden vorher eingegeben
    (Zeitstempel des letzten totp-Eintrags im Claim amr; die Oberfläche fragt dafür den Code erneut ab). Die Zeit geht an die
    Datenbank, die sie ebenfalls prüft. Das Token ist hier schon von der Zugangskontrolle geprüft; es wird nur noch
    gelesen, nicht erneut verifiziert.
  * Freigeben und Entsperren nennen den Fingerabdruck, den der Admin gesehen hat. Er muss zu dem Gerät passen; so wird
    nie ein Gerät freigegeben, dessen Fingerabdruck die Person nicht gesehen hat.
  * Grund (Pflicht bei Sperren und Widerruf, 3 bis 200 Zeichen, ohne Steuerzeichen), Kennung und Aktion sind streng geprüft.
  * Rate-Grenzen je Admin: Liste 60 je Minute, ändernde Aktionen 10 je 10 Minuten (auch abgelehnte zählen).

Antworten enthalten nie Schlüsselmaterial (kein ssh_pubkey), nur den Fingerabdruck. Der Body wird nie geloggt; das Log
enthält Kennung, Aktion, Ergebnis und die ersten Zeichen der Profil-ID des Admins, nie den Grund.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import deque
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from geraete_register import KENNUNG_RE, MAX_KENNUNG
from memory import DB_ERRORS, DatabaseUnavailable, normalize_uuid, shared_database
from wamo_api import bearer, identitaet, lies_json

router = APIRouter()

MAX_BODY = 4 * 1024
AAL2_FRISCH_SECS = 120.0
ZUKUNFT_SECS = 5.0  # so weit darf die Zeit des zweiten Faktors vorgehen (Uhren)
TOTP_METHODEN = ("totp", "mfa/totp")
CODE_AAL2 = "zweiter_faktor_zu_alt"
AKTIONEN = ("freigeben", "sperren", "entsperren", "widerrufen")
BRAUCHT_GRUND = ("sperren", "widerrufen")
BRAUCHT_FINGERABDRUCK = ("freigeben", "entsperren")
GRUND_MIN, GRUND_MAX = 3, 200
FINGERABDRUCK_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
STEUER_RE = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
GRUND_STEUER_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")  # Tab und Zeilenumbruch sind im Grund erlaubt (mehrzeiliges Feld)
STATUS = ("beantragt", "aktiv", "gesperrt", "widerrufen")
ERGEBNISSE = ("ok", "schon", "nicht_moeglich", "unbekannt", "konflikt", "besitzer_inaktiv")

LISTE_GRENZE = (60, 60.0)       # Aufrufe, Sekunden
AKTION_GRENZE = (10, 600.0)
_versuche: dict[tuple[str, str], deque[float]] = {}

STATUS_WORT = {"beantragt": "beantragt", "aktiv": "aktiv", "gesperrt": "gesperrt", "widerrufen": "widerrufen"}
SCHON_TEXT = {"freigeben": "schon freigegeben", "entsperren": "schon wieder freigegeben", "sperren": "schon gesperrt",
              "widerrufen": "schon widerrufen"}


# ---------- Prüfungen ohne Netz (einzeln testbar) ----------
def aal2_zeit(token: str) -> float | None:
    """Zeitpunkt (Unix-Sekunden) des letzten Codes des zweiten Faktors laut amr im Token, sonst None.

    Liest die Claims ohne Signaturprüfung: Aufrufer müssen das Token vorher über die Zugangskontrolle geprüft haben
    (identitaet), hier geht es nur um den Inhalt. Ohne aal2 oder ohne gültigen totp-Eintrag gibt es keinen Zeitpunkt."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False,
                                            "verify_iss": False})
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
    if claims.get("aal") != "aal2":
        return None
    eintraege = claims.get("amr")
    if not isinstance(eintraege, list):
        return None
    zeiten = [e.get("timestamp") for e in eintraege if isinstance(e, dict) and e.get("method") in TOTP_METHODEN]
    gueltig = [float(t) for t in zeiten if isinstance(t, (int, float)) and not isinstance(t, bool)
               and math.isfinite(t) and t > 0]
    return max(gueltig) if gueltig else None


def aal2_frisch(zeit: float | None, jetzt: float | None = None) -> bool:
    """Ist der zweite Faktor höchstens 120 Sekunden alt (und nicht aus der Zukunft)?"""
    if zeit is None:
        return False
    jetzt = time.time() if jetzt is None else jetzt
    return -ZUKUNFT_SECS <= jetzt - zeit <= AAL2_FRISCH_SECS


def grund_pruefen(roh: object, aktion: str) -> str | None:
    """Grund bereinigt (Leerraum beschnitten) oder None. Wirft HTTPException 400, wenn er fehlt (Pflicht) oder ungültig ist."""
    if roh is not None and not isinstance(roh, str):
        raise HTTPException(400, "Der Grund muss ein Text sein")
    if isinstance(roh, str) and GRUND_STEUER_RE.search(roh):
        raise HTTPException(400, "Der Grund enthält Steuerzeichen")
    grund = " ".join(roh.split()) if isinstance(roh, str) else ""
    if len(grund) > GRUND_MAX:
        raise HTTPException(400, f"Der Grund ist zu lang (höchstens {GRUND_MAX} Zeichen)")
    if aktion in BRAUCHT_GRUND and len(grund) < GRUND_MIN:
        raise HTTPException(400, f"Bitte einen Grund angeben (mindestens {GRUND_MIN} Zeichen)")
    return grund or None


def _drosseln(user_id: str, art: str, grenze: tuple[int, float], jetzt: float | None = None) -> None:
    anzahl, fenster = grenze
    jetzt = time.monotonic() if jetzt is None else jetzt
    q = _versuche.setdefault((user_id, art), deque())
    while q and jetzt - q[0] > fenster:
        q.popleft()
    if len(q) >= anzahl:
        raise HTTPException(429, "Zu viele Anfragen, bitte in ein paar Minuten noch einmal")
    q.append(jetzt)
    if len(_versuche) > 500:  # abgelaufene Admins aufräumen
        for k in [k for k, d in _versuche.items() if not d or jetzt - d[-1] > max(LISTE_GRENZE[1], AKTION_GRENZE[1])]:
            del _versuche[k]


async def _admin(request: Request):
    ich = await identitaet(request)
    if not ich.is_admin:
        raise HTTPException(403, "Die Geräteverwaltung ist nur für Admins")
    return ich


def _db():
    try:
        db = shared_database()
    except ValueError as e:
        raise HTTPException(503, "Datenbank nicht eingerichtet") from e
    if not db.configured:
        raise HTTPException(503, "Datenbank nicht eingerichtet")
    return db


def _text(wert: object, grenze: int) -> str:
    return STEUER_RE.sub(" ", wert)[:grenze] if isinstance(wert, str) else ""


def _zeit(wert: object) -> str | None:
    return wert.isoformat() if isinstance(wert, datetime) else None


def _ereignisse(roh: object) -> list[dict]:
    if isinstance(roh, str):
        try:
            roh = json.loads(roh)
        except ValueError:
            return []
    if not isinstance(roh, list):
        return []
    aus = []
    for e in roh[:10]:
        if not isinstance(e, dict):
            continue
        von, nach = e.get("von"), e.get("nach")
        aus.append({"von": von if von in STATUS else None, "nach": nach if nach in STATUS else None,
                    "durch": _text(e.get("durch"), 100) or None, "grund": _text(e.get("grund"), GRUND_MAX) or None,
                    "at": _text(e.get("at"), 40) or None})
    return aus


def zeile_pruefen(r, ich_id: str) -> dict | None:
    """Eine Zeile der Datenbank als Antwortzeile. None, wenn sie nicht passt (die Zeile fällt weg, die Liste bleibt)."""
    try:
        kennung, status = r["kennung"], r["status"]
        if (not isinstance(kennung, str) or len(kennung) > MAX_KENNUNG or not KENNUNG_RE.fullmatch(kennung)
                or status not in STATUS):
            return None
        fp = r["fingerabdruck"]
        profil_id = normalize_uuid(r["profil_id"]) if r["profil_id"] is not None else None
        return {
            "kennung": kennung,
            "name": _text(r["name"], 100),
            "rechnername": _text(r["hostname"], 63) or None,
            "profil": {"name": _text(r["profil_name"], 100) or None, "aktiv": bool(r["profil_aktiv"])},
            "status": status,
            "bindung": r["bindung"] if r["bindung"] in ("automatisch", "admin") else "admin",
            "fingerabdruck": fp if isinstance(fp, str) and FINGERABDRUCK_RE.fullmatch(fp) else None,
            "erstellt": _zeit(r["erstellt_at"]),
            "freigegeben": _zeit(r["freigegeben_at"]),
            "gesperrt": _zeit(r["gesperrt_at"]),
            "grund": _text(r["gesperrt_grund"], GRUND_MAX) or None,
            "zuletzt_gesehen": _zeit(r["zuletzt_gesehen"]),
            "ereignisse": _ereignisse(r["ereignisse"]),
            "eigen": profil_id is not None and profil_id == ich_id,
        }
    except (KeyError, TypeError, IndexError):
        return None


def antwort_text(aktion: str, vorher: str | None, kennung: str, ergebnis: str) -> str:
    """Konkrete Meldung für die Oberfläche."""
    if ergebnis == "ok":
        if aktion == "freigeben":
            return f"Das Gerät {kennung} ist freigegeben."
        if aktion == "entsperren":
            return f"Das Gerät {kennung} ist wieder freigegeben."
        if aktion == "sperren":
            return f"Das Gerät {kennung} ist gesperrt."
        return (f"Der Antrag für {kennung} ist abgelehnt." if vorher == "beantragt"
                else f"Die Freigabe von {kennung} ist widerrufen. Das gilt endgültig.")
    if ergebnis == "schon":
        return f"Das Gerät {kennung} ist {SCHON_TEXT[aktion]}. Es wurde nichts geändert."
    if ergebnis == "nicht_moeglich":
        zustand = STATUS_WORT.get(vorher or "", "in einem anderen Zustand")
        return (f"Das geht nicht: {kennung} ist {zustand}. Bitte die Liste neu laden, vielleicht hat ein anderer "
                f"Admin schon entschieden.")
    if ergebnis == "unbekannt":
        return "Ein Gerät mit dieser Kennung gibt es nicht. Bitte die Liste neu laden."
    if ergebnis == "konflikt":
        return ("Das Profil hat schon ein anderes aktives Gerät. Sperre oder widerrufe dieses zuerst, "
                "dann kann das neue freigegeben werden.")
    return "Das Profil des Besitzers ist deaktiviert. Aktiviere das Profil, bevor du das Gerät freigibst."


# ---------- Routen ----------
@router.get("/geraete/verwaltung")
async def liste(request: Request, response: Response) -> dict:
    ich = await _admin(request)
    _drosseln(ich.user_id, "liste", LISTE_GRENZE)
    response.headers["Cache-Control"] = "no-store"
    db = _db()
    try:
        zeilen = await db.fetch(f"select * from {db.t('geraete_verwaltung_liste')}($1::uuid)", ich.user_id)
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        if getattr(e, "sqlstate", None) == "42501":
            raise HTTPException(403, "Dir fehlt die Berechtigung für die Geräteverwaltung") from e
        logger.warning("Geräteverwaltung: Liste fehlgeschlagen ({})", type(e).__name__)
        raise HTTPException(503, "Die Geräteliste ist gerade nicht verfügbar") from e
    geraete = [g for g in (zeile_pruefen(r, ich.user_id) for r in zeilen) if g is not None]
    return {"geraete": geraete}


@router.post("/geraete/verwaltung/aktion", response_model=None)
async def aktion(request: Request, response: Response) -> dict | JSONResponse:
    ich = await _admin(request)
    _drosseln(ich.user_id, "aktion", AKTION_GRENZE)
    response.headers["Cache-Control"] = "no-store"
    body = await lies_json(request, MAX_BODY)
    kennung, was = body.get("kennung"), body.get("aktion")
    if (not isinstance(kennung, str) or len(kennung) > MAX_KENNUNG or not KENNUNG_RE.fullmatch(kennung)):
        raise HTTPException(400, "Kennung ungültig")
    if not isinstance(was, str) or was not in AKTIONEN:
        raise HTTPException(400, "Aktion ungültig")
    grund = grund_pruefen(body.get("grund"), was)
    fingerabdruck = body.get("fingerabdruck")
    if was in BRAUCHT_FINGERABDRUCK and (not isinstance(fingerabdruck, str) or not FINGERABDRUCK_RE.fullmatch(fingerabdruck)):
        raise HTTPException(400, "Der Fingerabdruck fehlt: Er muss vor der Freigabe angezeigt und bestätigt werden")

    zeit = aal2_zeit(bearer(request))
    if not aal2_frisch(zeit):
        logger.info("Geräteverwaltung: {} für {} abgelehnt, zweiter Faktor zu alt (Admin {})", was, kennung, ich.user_id[:8])
        return JSONResponse({"detail": "Bitte gib den Code deiner Authenticator-App noch einmal ein. "
                                       "Er darf höchstens zwei Minuten alt sein.", "code": CODE_AAL2},
                            status_code=403, headers={"Cache-Control": "no-store"})

    db = _db()
    try:
        if was in BRAUCHT_FINGERABDRUCK:
            echt = await db.fetchval(
                f"select ssh_fingerprint from {db.t('devices')} where kennung = $1 and art = 'persoenlich'", kennung)
            if echt is None:
                raise HTTPException(404, antwort_text(was, None, kennung, "unbekannt"))
            if echt != fingerabdruck:
                raise HTTPException(409, "Der Fingerabdruck passt nicht zu diesem Gerät. Bitte die Liste neu laden "
                                         "und den Fingerabdruck mit dem Laptop vergleichen.")
        zeile = await db.fetchrow(
            f"select kennung, vorher::text as vorher, nachher::text as nachher, ergebnis "
            f"from {db.t('geraet_entscheiden')}($1::uuid, $2, $3, $4, $5::timestamptz)",
            ich.user_id, kennung, was, grund, datetime.fromtimestamp(zeit, tz=timezone.utc))
    except HTTPException:
        raise
    except (*DB_ERRORS, DatabaseUnavailable) as e:
        code = getattr(e, "sqlstate", None)
        logger.warning("Geräteverwaltung: {} für {} fehlgeschlagen ({}, {})", was, kennung, type(e).__name__, code)
        if code == "42501":
            raise HTTPException(403, "Dir fehlt die Berechtigung: Nur aktive Admins verwalten Geräte") from e
        meldung = str(e).lower()
        if code == "23514" and ("zweite faktor" in meldung or "aal2" in meldung):
            return JSONResponse({"detail": "Der zweite Faktor ist zu alt. Bitte den Code noch einmal eingeben.",
                                 "code": CODE_AAL2}, status_code=403, headers={"Cache-Control": "no-store"})
        if code in ("22023", "22004"):
            raise HTTPException(400, "Die Angaben wurden von der Datenbank abgelehnt") from e
        raise HTTPException(503, "Die Datenbank hat nicht sauber geantwortet. Ob die Aktion gelaufen ist, zeigt die Liste. "
                                 "Bitte neu laden und den Stand prüfen.") from e
    ergebnis = zeile["ergebnis"] if zeile is not None else None
    if ergebnis not in ERGEBNISSE:
        logger.error("Geräteverwaltung: unerwartete Antwort der Datenbank")
        raise HTTPException(503, "Unerwartete Antwort der Datenbank. Bitte die Liste neu laden und prüfen.")
    vorher, nachher = zeile["vorher"], zeile["nachher"]
    logger.info("Geräteverwaltung: {} {} -> {} (Admin {})", was, kennung, ergebnis, ich.user_id[:8])
    text = antwort_text(was, vorher, kennung, ergebnis)
    if ergebnis in ("ok", "schon"):
        return {"ok": True, "ergebnis": ergebnis, "kennung": kennung, "aktion": was, "vorher": vorher,
                "status": nachher, "text": text}
    status_http = {"unbekannt": 404}.get(ergebnis, 409)
    raise HTTPException(status_http, text)
