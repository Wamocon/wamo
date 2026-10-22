"""Kontingent eines Max-Abos aus den Ereignissen von Claude Code (Sitzungslimit, Wochenlimit, Modell-Wochenlimit).

Quelle ist das Ereignis `rate_limit_event` im stream-json von Claude Code (geprüft gegen Version 2.1.303 mit einer
API-Attrappe). Es erscheint nur bei einem Abo-Zugang (CLAUDE_CODE_OAUTH_TOKEN aus `claude setup-token`), nie bei einem
API-Schlüssel, und nur, wenn eine Antwort der API die Kopfzeilen anthropic-ratelimit-unified-* trägt:

    {"type": "rate_limit_event", "uuid": "...", "session_id": "...", "rate_limit_info": {
       "status": "allowed" | "allowed_warning" | "rejected", "rateLimitType": "five_hour" | "seven_day" | ...,
       "resetsAt": <Unix-Sekunden>, "utilization": <Bruchteil>, "isUsingOverage": false,
       "unifiedWindows": {"five_hour": {"utilization": 0.42, "resetsAt": ...}, "seven_day": {...},
                          "seven_day_overage_included": {...}}}}

Das Modell-Wochenlimit heißt in Claude Code "Fable limit" (seven_day_overage_included). Den Namen des Modells liefert
nur /usage (Abrufrecht user:profile), das Tokens aus `setup-token` nicht haben; deshalb bleibt es hier bei der Art.

Dieses Modul kennt nur die Standardbibliothek: der Dispatcher auf Sokrates-2 lädt eine Kopie aus /opt/wamo/waechter
(Zeilen aus der Sandbox sind nicht vertrauenswürdig, also nur Felder aus einer festen Allowlist und mit Wertebereich),
der Kern prüft das Ereignis des Dispatchers noch einmal mit denselben Regeln, speichert es je Profil und schickt es
als Servernachricht {"type": "kontingent", ...} an die Oberfläche.

Ein Limit: {"art": sitzung | woche | opus | sonnet | fable, "prozent": 0..1000, "endet": "YYYY-MM-DDTHH:MM:SSZ",
"status": ok | warnung | erreicht}. Jedes Feld außer art kann fehlen. Ohne Daten ist der Zustand "unbekannt".
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone

ARTEN = ("sitzung", "woche", "opus", "sonnet", "fable")
STATI = ("ok", "warnung", "erreicht")
# Namen von Claude Code (rateLimitType und Schlüssel in unifiedWindows) -> Art
FENSTER = {"five_hour": "sitzung", "seven_day": "woche", "seven_day_opus": "opus", "seven_day_sonnet": "sonnet",
           "seven_day_overage_included": "fable"}
STATUS = {"allowed": "ok", "allowed_warning": "warnung", "rejected": "erreicht"}
MAX_PROZENT = 1000.0  # über 100 kommt vor (Nutzung läuft über die Grenze); alles darüber ist Unsinn
MAX_LIMITS = len(ARTEN)
MIN_EPOCHE, MAX_EPOCHE = 1_577_836_800, 4_102_444_800  # 2020-01-01 bis 2100-01-01
ZEIT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")  # nur ASCII-Ziffern
SCHLUESSEL = ("art", "prozent", "endet", "status")


def _zahl(wert: object) -> float | None:
    """Endliche Zahl als float; Wahrheitswerte, Text und Ganzzahlen mit absurd vielen Stellen (OverflowError) sind keine."""
    if isinstance(wert, bool):
        return None
    if isinstance(wert, int):
        return float(wert) if abs(wert) < 10**15 else None
    if isinstance(wert, float) and math.isfinite(wert):
        return wert
    return None


def _prozent(bruchteil: object) -> float | None:
    """Bruchteil des Fensters (0.42) als Prozent mit einer Nachkommastelle; None außerhalb von 0 bis MAX_PROZENT."""
    z = _zahl(bruchteil)
    if z is None or z < 0 or z * 100 > MAX_PROZENT:
        return None
    return round(z * 100, 1)


def _zeit(epoche: object) -> str | None:
    z = _zahl(epoche)
    if z is None or not MIN_EPOCHE <= z <= MAX_EPOCHE:
        return None
    return datetime.fromtimestamp(int(z), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def zeit_gueltig(text: object) -> bool:
    """ISO-Zeit in UTC (YYYY-MM-DDTHH:MM:SSZ), wirklich ein Datum."""
    if not isinstance(text, str) or not ZEIT_RE.fullmatch(text):
        return False
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _limit(art: str, prozent: float | None, endet: str | None, status: str | None) -> dict:
    out: dict = {"art": art}
    if prozent is not None:
        out["prozent"] = prozent
    if endet is not None:
        out["endet"] = endet
    if status is None and prozent is not None and prozent >= 100:
        status = "erreicht"
    if status is not None:
        out["status"] = status
    return out


def aus_rate_limit_info(info: object) -> list[dict]:
    """Limits aus dem Feld rate_limit_info eines rate_limit_event. Nur bekannte Namen, nur Zahlen im Wertebereich;
    alles andere (auch Zusatzfelder wie overageStatus) wird ignoriert."""
    if not isinstance(info, dict):
        return []
    eintraege: dict[str, dict] = {}
    fenster = info.get("unifiedWindows")
    if isinstance(fenster, dict):
        for name, daten in fenster.items():
            art = FENSTER.get(name) if isinstance(name, str) else None
            if art and isinstance(daten, dict):
                eintraege[art] = _limit(art, _prozent(daten.get("utilization")), _zeit(daten.get("resetsAt")), None)
    typ = info.get("rateLimitType")
    art = FENSTER.get(typ) if isinstance(typ, str) else None
    if art:  # das gerade begrenzende Fenster: Status der Antwort, Werte nur ergänzen
        status = info.get("status")
        alt = eintraege.get(art, {})
        eintraege[art] = _limit(art, alt.get("prozent", _prozent(info.get("utilization"))),
                                alt.get("endet", _zeit(info.get("resetsAt"))),
                                STATUS.get(status) if isinstance(status, str) else None)
    return [eintraege[a] for a in ARTEN if len(eintraege.get(a, {})) > 1]


def ereignis_aus_zeile(zeile: object) -> dict | None:
    """Zeile aus dem stream-json von Claude Code -> Ereignis des Dispatchers oder None. Das Ergebnis ist ein
    Ereignis vom Typ wamo: der Dispatcher verwirft solche Zeilen aus der Sandbox, die gesendeten sind seine eigenen."""
    if not isinstance(zeile, dict) or zeile.get("type") != "rate_limit_event":
        return None
    limits = aus_rate_limit_info(zeile.get("rate_limit_info"))
    return {"type": "wamo", "ereignis": "kontingent", "quelle": "rate_limit_event", "limits": limits} if limits else None


def limits_pruefen(limits: object) -> list[dict]:
    """Prüft eine Liste fertiger Limits streng (für Ereignisse des Dispatchers und Werte aus der Datenbank). Was nicht
    passt, fällt weg; doppelte Arten zählen einmal; Reihenfolge wie ARTEN."""
    if not isinstance(limits, list):
        return []
    gefunden: dict[str, dict] = {}
    for eintrag in limits[:MAX_LIMITS * 4]:
        if not isinstance(eintrag, dict) or set(eintrag) - set(SCHLUESSEL) or eintrag.get("art") not in ARTEN:
            continue
        prozent, endet, status = eintrag.get("prozent"), eintrag.get("endet"), eintrag.get("status")
        z = _zahl(prozent)
        if prozent is not None and (z is None or not 0 <= z <= MAX_PROZENT):
            continue
        if endet is not None and not zeit_gueltig(endet):
            continue
        if status is not None and status not in STATI:
            continue
        gefunden.setdefault(eintrag["art"], _limit(eintrag["art"], None if z is None else round(z, 1), endet, status))
    return [gefunden[a] for a in ARTEN if len(gefunden.get(a, {})) > 1]


def pruefen_ereignis(ereignis: object) -> list[dict]:
    """Limits aus einem wamo-Ereignis "kontingent" des Dispatchers (nochmals geprüft); leer, wenn es nicht passt."""
    if not isinstance(ereignis, dict) or ereignis.get("ereignis") != "kontingent":
        return []
    return limits_pruefen(ereignis.get("limits"))


def jetzt_iso(jetzt: datetime | None = None) -> str:
    return (jetzt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def zusammenfuehren(alt: list[dict], neu: list[dict], jetzt: datetime | None = None) -> list[dict]:
    """Neue Beobachtungen über die bisherigen legen. Abgelaufene Fenster fallen vorher weg (Claude Code verwirft sie
    ebenso): ihr Stand ist unbekannt, bis der nächste Lauf sie wieder meldet. Je Art zählt die neue Angabe; endet das
    Fenster anders als das bekannte, ist es ein neues Fenster und nichts vom alten (Prozent, Status) gilt weiter."""
    grenze = jetzt_iso(jetzt)

    def gueltig(e: dict) -> bool:
        return e.get("endet", "9") > grenze  # ohne Ende läuft ein Eintrag nie ab ("9" sortiert hinter jedem Datum)
    stand = {e["art"]: dict(e) for e in limits_pruefen(alt) if gueltig(e)}
    for e in limits_pruefen(neu):
        if not gueltig(e):
            continue
        vorher = stand.get(e["art"], {})
        if "endet" in e and e["endet"] != vorher.get("endet"):
            vorher = {}
        stand[e["art"]] = {**vorher, **e}
    return [stand[a] for a in ARTEN if a in stand and len(stand[a]) > 1]


def nachricht(limits: object, stand: str | None = None, jetzt: datetime | None = None) -> dict:
    """Servernachricht an die Oberfläche. Ohne (noch gültige) Daten ist der Zustand "unbekannt"."""
    gueltig = zusammenfuehren([], limits if isinstance(limits, list) else [], jetzt)
    return {"type": "kontingent", "zustand": "bekannt" if gueltig else "unbekannt",
            "stand": stand if zeit_gueltig(stand) and gueltig else None,
            "limits": gueltig}


# ---------- Speicher je Profil (Spalten kontingent und kontingent_stand der Tabelle model_access, Migration 0012) ----------
# db ist ein Database aus memory.py (configured, t(), fetchrow(), execute()); user_id eine UUID als Text.
async def laden(db, user_id: str) -> tuple[list[dict], str | None]:
    """Gespeicherter Stand: (Limits, Zeitpunkt der letzten Beobachtung). Ohne Zeile oder Daten ([], None)."""
    zeile = await db.fetchrow(
        f"select kontingent, kontingent_stand from {db.t('model_access')} where user_id = $1::uuid", user_id)
    if not zeile or zeile["kontingent"] is None or zeile["kontingent_stand"] is None:
        return [], None
    roh = zeile["kontingent"]
    try:
        limits = limits_pruefen(json.loads(roh) if isinstance(roh, (str, bytes)) else roh)
    except (ValueError, RecursionError):
        limits = []
    stand = zeile["kontingent_stand"]
    return limits, jetzt_iso(stand.astimezone(timezone.utc)) if hasattr(stand, "astimezone") else None


async def speichern(db, user_id: str, limits: list[dict]) -> None:
    """Legt den Stand für das Profil ab (die Zeile entsteht bei Bedarf; Schlüssel und Modellwahl bleiben unberührt)."""
    sauber = limits_pruefen(limits)
    await db.execute(
        f"insert into {db.t('model_access')} (user_id, kontingent, kontingent_stand) "
        "values ($1::uuid, $2::jsonb, now()) on conflict (user_id) do update "
        "set kontingent = excluded.kontingent, kontingent_stand = excluded.kontingent_stand",
        user_id, json.dumps(sauber),
    )
