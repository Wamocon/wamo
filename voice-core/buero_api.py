"""Büro-Karte: schmale Sicht auf die gemeinsamen Bürogeräte für die Oberfläche.

  GET /wamo/api/buero/status   {"geraete": [...], "unbekannt": 0, "stand": "2026-11-09T08:00:00Z"}

Je Gerät genau diese Felder: id (Kennung), name, gruppe (server, drucker, netz, speicher, medien, steckdosen),
ampel (ok, stoerung, unbekannt), text (Klartext, höchstens 80 Zeichen), tipp (Sprechtipp) und optional kennzahlen
(nur Zahlen mit festen Namen: toner_prozent, runter_mbit, hoch_mbit, lautstaerke, leistung_w, last). Das Feld
"unbekannt" ist die Anzahl der Geräte im Netz, die WAMO nicht kennt (passive Erkennung im Gerätedienst über die Hostliste
der FRITZ!Box, kein Scan), oder null, wenn es keine brauchbare Zahl gibt (nicht eingerichtet, Fehler, zu langsam). Es ist
nur die Zahl: Namen, Adressen und MAC-Adressen der unbekannten Geräte gibt es hier nie; die Liste sieht nur ein Admin und
nur per Sprache (Werkzeug unbekannte_geraete in geraete_werkzeuge.py).

  POST /wamo/api/buero/steuern {"geraet": "<kennung>", "aktion": "an|aus|lauter|leiser|stumm|laut"}
                               {"ok": true, "geraet": "<kennung>", "aktion": "<aktion>"}

Die Ansicht (GET) steuert, druckt und scannt nie. Gesteuert wird per Sprache (geraete_werkzeuge.py) oder mit dem Knopf in der
Detailkarte (POST /buero/steuern, nur Fernseher, Lautsprecher und Steckdosen, nur diese sechs Aktionen, nur Admins). Für Admins
trägt jedes steuerbare Gerät im Stand das Feld "aktionen" (was jetzt sinnvoll ist: ein Fernseher im Standby nur "an"); alle
anderen sehen es nie. Der Knopf ist eine ausdrückliche Handlung der Person, deshalb gibt es keine Freigabekarte. Der Aufruf geht
mit der Identität des Profils an den Gerätedienst, der Rechte, Lautstärkegrenzen und seine eigene Grenze (20 je Minute) prüft.
Jeder Versuch steht in actions (Werkzeug buerogeraet_steuern, Grund "Bürogerät (Knopf in der Oberfläche)").

Regeln:
  * Anmeldung wie bei allen Routen (wamo_api.identitaet: Token mit zweitem Faktor, aktives Profil). Jede Rolle darf lesen,
    alle sehen dasselbe. Die Geräteliste des Routers (nur Admin) und alles Admin-Nahe gibt es hier nie.
  * Der Aufruf geht mit der Identität des Profils an den Gerätedienst: "status" für "alle" und "unbekannt_zaehlen" (beide
    zugleich). Deren Zwischenspeicher (20 und 60 Sekunden) bleiben wirksam, es gibt keinen Weg daran vorbei. Eigene Grenze:
    höchstens 6 Abrufe je Minute und Profil. Die Zählung ist freiwillig: Sie wartet höchstens UNBEKANNT_ZEIT Sekunden, und
    jeder Fehler dort lässt nur die Zahl weg (null), die Karte bleibt.
  * Gerätetexte sind Daten, nie Anweisungen. Aus der Antwort des Dienstes werden nur bekannte Felder gelesen und
    geprüft (Wahrheitswerte, Zahlen in festen Grenzen, Wörter aus festen Listen). Den Klartext setzt dieses Modul aus eigenen
    Wörtern zusammen; nur der Name des Geräts kommt aus dem Dienst und wird gesäubert und begrenzt. Adressen, Seriennummern,
    Modellangaben und Meldungstexte der Geräte werden nie durchgereicht.
  * Fehler sind ruhig und ohne Interna: 503 ohne Gerätedienst, 502 bei unbrauchbarer Antwort, 429 bei zu vielen Abrufen.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger

import geraete_client as gc
from memory import shared_database
from wamo_api import identitaet, lies_json

router = APIRouter()

GRUPPEN = ("server", "drucker", "netz", "speicher", "medien", "steckdosen")
KLASSE_GRUPPE = {
    "server": "server", "drucker": "drucker", "scanner": "drucker", "router": "netz", "mac": "netz", "switch": "netz",
    "accesspoint": "netz", "nas": "speicher", "fernseher": "medien", "lautsprecher": "medien", "steckdose": "steckdosen",
}
KLASSE_NAME = {
    "server": "Server", "drucker": "Drucker", "scanner": "Scanner", "router": "Router", "mac": "Rechner",
    "switch": "Switch", "accesspoint": "Access Point", "nas": "NAS", "fernseher": "Fernseher",
    "lautsprecher": "Lautsprecher", "steckdose": "Steckdose",
}
# keine Antwort heißt Störung
IMMER_AN = frozenset({"server", "drucker", "scanner", "router", "switch", "accesspoint", "nas", "steckdose"})

GRENZE = (6, 60.0)  # Abrufe, Sekunden, je Profil
STEUER_GRENZE = (20, 60.0)  # Schaltvorgänge, Sekunden, je Profil (der Gerätedienst begrenzt ebenfalls auf 20 je Minute)
STEUER_ROLLEN = frozenset({"admin"})  # wer die Knöpfe benutzen darf; alle anderen sehen weder Knöpfe noch dürfen sie POSTen
STEUER_AKTIONEN = ("an", "aus", "lauter", "leiser", "stumm", "laut")  # nur diese sechs; Lautstärke-Zahl und Eingang gibt es hier nie
STEUER_ZEIT = 20.0  # der Dienst gibt sich 15 Sekunden (geraete/wamo_geraete/dienst.py), der Kern wartet etwas länger
PROTOKOLL_ZEIT = 2.0  # das Protokoll hält die Antwort nach einem schon erfolgten Schaltvorgang nie länger auf
MAX_STEUER_BODY = 1024
MAX_GERAETE = 64
MAX_NAME, MAX_TEXT, MAX_TIPP = 60, 80, 120
DIENST_ZEIT = 12.0
UNBEKANNT_ZEIT = 3.0  # so lange wartet die Karte auf die Zahl der unbekannten Geräte (die Messung läuft im Dienst weiter)
MAX_UNBEKANNT = 10_000
KENNUNG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
UNSICHTBAR = frozenset({"Cc", "Cf", "Zl", "Zp"})  # Steuerzeichen, unsichtbare und umlenkende Formatzeichen, Zeilen- und Absatztrenner
KAPUTT = frozenset({"failed", "inactive", "deactivating"})
WIEDERGABE = frozenset({"spielt", "pausiert", "gestoppt", "wechselt", "nichts geladen"})
EINZUG_STOERUNG = frozenset({"Papierstau", "Einzugsfehler", "Klappe offen"})

_versuche: dict[str, deque[float]] = {}
_schaltungen: dict[str, deque[float]] = {}


# ---------- Grenze je Profil ----------
def _drosseln(user_id: str, jetzt: float | None = None, *, speicher: dict[str, deque[float]] | None = None,
              grenze: tuple[int, float] = GRENZE, text: str = "Zu viele Abrufe, bitte kurz warten") -> None:
    anzahl, fenster = grenze
    jetzt = time.monotonic() if jetzt is None else jetzt
    speicher = _versuche if speicher is None else speicher
    q = speicher.setdefault(user_id, deque())
    while q and jetzt - q[0] > fenster:
        q.popleft()
    if len(q) >= anzahl:
        raise HTTPException(429, text)
    q.append(jetzt)
    if len(speicher) > 500:  # abgelaufene Profile aufräumen
        for k in [k for k, d in speicher.items() if not d or jetzt - d[-1] > fenster]:
            del speicher[k]


# ---------- Hilfen ohne Netz ----------
def _satz(wert: object, grenze: int) -> str:
    """Text ohne Steuerzeichen, Leerraum zusammengefasst, höchstens grenze Zeichen (kein Text: leer)."""
    if not isinstance(wert, str):
        return ""
    sichtbar = "".join(" " if unicodedata.category(c) in UNSICHTBAR else c for c in wert)
    return " ".join(sichtbar.split())[:grenze].strip()


def _zahl(wert: object, tief: float, hoch: float) -> float | None:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert) or not tief <= wert <= hoch:
        return None
    return float(wert)


def _dez(wert: float) -> str:
    """Eine Nachkommastelle mit Komma, eine glatte Zahl ohne ",0"."""
    text = f"{wert:.1f}".replace(".", ",")
    return text[:-2] if text.endswith(",0") else text


def _ganz(wert: float) -> int:
    return round(wert)


def _ganzzahl(wert: object, tief: int, hoch: int) -> int | None:
    """Nur echte Ganzzahlen (keine Wahrheitswerte, keine Gleitkommazahlen, keine Texte) im Bereich."""
    return wert if isinstance(wert, int) and not isinstance(wert, bool) and tief <= wert <= hoch else None


def _wahr(roh: dict, schluessel: str) -> bool | None:
    wert = roh.get(schluessel)
    return wert if isinstance(wert, bool) else None


def _kennzahlen(**werte: float | int | None) -> dict:
    return {k: v for k, v in werte.items() if v is not None}


# ---------- Klartext je Geräteklasse: jede Funktion liefert (ampel, text, kennzahlen) ----------
Befund = tuple[str, str, dict]


def _server(d: dict, erreichbar: bool) -> Befund:
    if not erreichbar:
        return "stoerung", "nicht erreichbar", {}
    dienste = d.get("dienste")
    kaputt = sum(1 for v in dienste.values() if isinstance(v, str) and v in KAPUTT) if isinstance(dienste, dict) else 0
    last_roh = d.get("last")
    last = _zahl(last_roh[0], 0, 1e4) if isinstance(last_roh, list) and last_roh else None
    zahlen = _kennzahlen(last=round(last, 1) if last is not None else None)
    if kaputt:
        return "stoerung", f"läuft, {kaputt} {'Dienst' if kaputt == 1 else 'Dienste'} nicht aktiv", zahlen
    return "ok", f"läuft, Last {_dez(last)}" if last is not None else "läuft", zahlen


def _druckergrund(grund: str) -> str | None:
    g = grund.lower()
    if "jam" in g:
        return "Papierstau"
    if g.startswith(("media-empty", "media-needed")):
        return "Papier leer"
    if g.startswith(("toner-empty", "marker-supply-empty")):
        return "Toner leer"
    if g.startswith(("door-open", "cover-open")):
        return "Klappe offen"
    if g.endswith("-error"):
        return "Störung gemeldet"
    return None


def _toner_stand(d: dict) -> int | None:
    """Niedrigster Stand der Tonerkartuschen in Prozent (Resttonerbehälter zählen nicht), None ohne gültige Angabe."""
    marker = d.get("toner")
    staende: list[float] = []
    for m in marker[:8] if isinstance(marker, list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("name"), str):
            continue
        name = m["name"].lower()
        stand = _zahl(m.get("stand_prozent"), 0, 100)
        if "toner" in name and "waste" not in name and stand is not None:
            staende.append(stand)
    return _ganz(min(staende)) if staende else None


def _drucker(d: dict, erreichbar: bool) -> Befund:
    if not erreichbar:
        return "stoerung", "nicht erreichbar", {}
    toner = _toner_stand(d)
    zahlen = _kennzahlen(toner_prozent=toner)
    gruende = d.get("meldungen")
    for g in gruende[:10] if isinstance(gruende, list) else []:
        text = _druckergrund(g) if isinstance(g, str) else None
        if text:
            return "stoerung", text, zahlen
    if toner == 0:
        return "stoerung", "Toner leer", zahlen
    if d.get("nimmt_auftraege") is False:
        return "stoerung", "nimmt keine Aufträge an", zahlen
    zustand = d.get("zustand")
    if zustand == "angehalten":
        return "stoerung", "angehalten", zahlen
    if zustand not in ("bereit", "druckt"):
        return "unbekannt", "Zustand unklar", zahlen
    if toner is None:
        return "ok", zustand, zahlen
    return "ok", f"{zustand}, Toner nur noch {toner} Prozent" if toner <= 10 else f"{zustand}, Toner {toner} Prozent", zahlen


def _scanner(d: dict, erreichbar: bool) -> Befund:
    if not erreichbar:
        return "stoerung", "nicht erreichbar", {}
    zustand, einzug = d.get("zustand"), d.get("einzug")
    if zustand in ("angehalten", "gestört"):
        return "stoerung", zustand, {}
    if zustand not in ("bereit", "scannt", "prüft"):
        return "unbekannt", "Zustand unklar", {}
    if isinstance(einzug, str) and einzug in EINZUG_STOERUNG:
        return "stoerung", f"{zustand}, Einzug: {einzug}", {}
    return "ok", zustand, {}


def _router(d: dict, erreichbar: bool) -> Befund:
    if not erreichbar:
        return "stoerung", "nicht erreichbar", {}
    runter, hoch = _zahl(d.get("runter_mbit"), 0, 1e5), _zahl(d.get("hoch_mbit"), 0, 1e5)
    zahlen = _kennzahlen(runter_mbit=_ganz(runter) if runter is not None else None,
                         hoch_mbit=_ganz(hoch) if hoch is not None else None)
    internet, leitung = d.get("internet"), d.get("leitung")
    if internet == "getrennt" or leitung == "getrennt":
        return "stoerung", "Internet getrennt", zahlen
    if internet == "verbunden":
        return "ok", f"Internet verbunden, {_ganz(runter)} Mbit runter" if runter is not None else "Internet verbunden", zahlen
    if internet == "verbindet":
        return "unbekannt", "Internet wird verbunden", zahlen
    if internet == "nicht eingerichtet":
        return "unbekannt", "Internet nicht eingerichtet", zahlen
    return "unbekannt", "Internet unklar", zahlen


def _netzgeraet(d: dict, erreichbar: bool) -> Befund:
    """Switch oder Access Point (SNMPv3): Ports ohne Kabel sind normal und keine Störung."""
    if not erreichbar:
        return "stoerung", "nicht erreichbar", {}
    gesamt, oben = _ganzzahl(d.get("ports_gesamt"), 0, 1000), _ganzzahl(d.get("ports_oben"), 0, 1000)
    if gesamt and oben is not None and oben <= gesamt:
        return "ok", f"läuft, {oben} von {gesamt} Ports aktiv", {}
    return "ok", "läuft", {}


def _nas(d: dict, erreichbar: bool) -> Befund:
    if not erreichbar:
        return "stoerung", "nicht erreichbar", {}
    if d.get("eingerichtet") is True:
        return "ok", "erreichbar", {}
    return "unbekannt", "erreichbar, W.A.I.S.-Zugang nicht eingerichtet", {}


def _lautstaerke(d: dict) -> tuple[str | None, dict]:
    """Teilsatz "stumm" oder "Lautstärke N" samt Kennzahl, None ohne gültige Angabe."""
    laut = _zahl(d.get("lautstaerke"), 0, 100)
    zahlen = _kennzahlen(lautstaerke=_ganz(laut) if laut is not None else None)
    if d.get("stumm") is True:
        return "stumm", zahlen
    return (f"Lautstärke {_ganz(laut)}" if laut is not None else None), zahlen


def _fernseher(d: dict, erreichbar: bool) -> Befund:
    if not erreichbar:
        return "ok", "aus", {}  # ein Fernseher im Standby antwortet nicht, das ist normal
    an = _wahr(d, "an")
    if an is None:
        return "unbekannt", "keine Angabe", {}
    if not an:
        return "ok", "aus", {}
    gekoppelt = _wahr(d, "gekoppelt")
    if gekoppelt is None:
        return "ok", "an", {}
    if not gekoppelt:
        return "ok", "an, nicht gekoppelt", {}
    laut, zahlen = _lautstaerke(d)
    return "ok", ", ".join(["an, gekoppelt", *([laut] if laut else [])]), zahlen


def _lautsprecher(d: dict, erreichbar: bool) -> Befund:
    if not erreichbar:
        return "unbekannt", "keine Antwort, vielleicht aus", {}
    wiedergabe = d.get("wiedergabe")
    laut, zahlen = _lautstaerke(d)
    teile = ["an", *([wiedergabe] if isinstance(wiedergabe, str) and wiedergabe in WIEDERGABE else []),
             *([laut] if laut else [])]
    return "ok", ", ".join(teile), zahlen


def _steckdose(d: dict, erreichbar: bool) -> Befund:
    if not erreichbar:
        return "stoerung", "nicht erreichbar", {}
    an = _wahr(d, "an")
    if an is None:
        return "unbekannt", "keine Angabe", {}
    watt = _zahl(d.get("leistung_w"), 0, 10000)
    zahlen = _kennzahlen(leistung_w=round(watt, 1) if watt is not None else None)
    if not an:
        return "ok", "aus", zahlen
    return "ok", f"an, {_ganz(watt)} Watt" if watt is not None and _ganz(watt) > 0 else "an", zahlen


def _mac(d: dict, erreichbar: bool) -> Befund:
    return ("ok", "im Netz sichtbar", {}) if erreichbar else ("unbekannt", "nicht sichtbar, vielleicht aus", {})


BEFUNDE = {
    "server": _server, "drucker": _drucker, "scanner": _scanner, "router": _router, "nas": _nas, "fernseher": _fernseher,
    "lautsprecher": _lautsprecher, "steckdose": _steckdose, "mac": _mac, "switch": _netzgeraet, "accesspoint": _netzgeraet,
}
assert set(BEFUNDE) == set(KLASSE_GRUPPE) == set(KLASSE_NAME)


def _ohne_antwort(klasse: str, status: object) -> tuple[str, str]:
    """Befund, wenn der Dienst für das Gerät keine Werte liefert (Fehler, nicht eingerichtet, Grenze)."""
    if status == "nicht_eingerichtet":
        return "unbekannt", "nicht eingerichtet"
    if status == "error":
        if klasse in IMMER_AN:
            return "stoerung", "nicht erreichbar"
        return "unbekannt", "nicht sichtbar, vielleicht aus" if klasse == "mac" else "keine Antwort, vielleicht aus"
    return "unbekannt", "keine Angabe"


# ---------- Sprechtipps ----------
TIPPS = {
    "server": "Sag: Wie geht es den Servern?",
    "drucker": "Sag: Wie ist der Tonerstand am Drucker?",
    "scanner": "Sag: Scanne eine Seite vom Glas",
    "router": "Sag: Ist das Internet in Ordnung?",
    "switch": "Sag: Wie geht es dem Switch?",
    "accesspoint": "Sag: Wie geht es dem Access Point?",
    "nas": "Sag: Suche auf dem NAS nach dem Angebot",
    "lautsprecher": "Sag: Stell den Lautsprecher leiser",
    "mac": "Sag: Welche Bürogeräte sind gerade erreichbar?",
}


def _kurzname(name: str) -> str:
    """Name ohne angehängte Klammer, zum Beispiel "Steckdose 1 (0470)" zu "Steckdose 1"."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()[:40] or name[:40]


def _tipp(klasse: str, name: str, d: dict) -> str:
    laeuft = d.get("erreichbar") is True and d.get("an") is True
    if klasse == "fernseher":
        if d.get("gekoppelt") is False:
            return "Zum Steuern per Sprache muss der Fernseher erst gekoppelt werden"
        return "Sag: Schalte den Fernseher aus" if laeuft else "Sag: Schalte den Fernseher an"
    if klasse == "steckdose":
        return f"Sag: Schalte {_kurzname(name)} {'aus' if laeuft else 'an'}"
    return TIPPS[klasse]


# ---------- Aktionen je Gerät (nur Admins sehen sie) ----------
def _aktionen(klasse: str, roh: dict, erreichbar: bool | None) -> list[str]:
    """Was der Knopf-Bereich jetzt anbieten darf. Nur, wenn der Dienst Werte geliefert hat. Ein Fernseher im Standby kennt nur "an"
    (der Dienst weckt ihn per Wake-on-LAN oder Taste), ein laufender "aus" und die Lautstärke, ein nicht gekoppelter nichts."""
    if roh.get("status") != "success":
        return []
    if klasse == "fernseher":
        if _wahr(roh, "gekoppelt") is False:
            return []
        if erreichbar is True and _wahr(roh, "an") is True:
            return ["aus", "leiser", "lauter", "stumm", "laut"]
        return ["an"] if _wahr(roh, "an") is False or erreichbar is False else []
    if klasse == "steckdose" and erreichbar is True:
        an = _wahr(roh, "an")
        return [] if an is None else ["aus"] if an else ["an"]
    if klasse == "lautsprecher" and erreichbar is True:
        return ["leiser", "lauter", "stumm", "laut"]
    return []


# ---------- Modell je Gerät ----------
def geraet_modell(roh: object) -> dict | None:
    """Ein Gerät aus der Liste des Dienstes als schmale Antwortzeile. None, wenn Kennung oder Klasse nicht passen."""
    if not isinstance(roh, dict):
        return None
    kennung, klasse = roh.get("geraet"), roh.get("klasse")
    if not isinstance(kennung, str) or not KENNUNG_RE.fullmatch(kennung) or not isinstance(klasse, str) \
            or klasse not in KLASSE_GRUPPE:
        return None
    name = _satz(roh.get("name"), MAX_NAME) or KLASSE_NAME[klasse]
    erreichbar = _wahr(roh, "erreichbar")
    if roh.get("status") != "success":
        ampel, text, zahlen = (*_ohne_antwort(klasse, roh.get("status")), {})
    elif erreichbar is None:
        ampel, text, zahlen = "unbekannt", "keine Angabe", {}
    else:
        ampel, text, zahlen = BEFUNDE[klasse](roh, erreichbar)
    modell = {"id": kennung, "name": name, "gruppe": KLASSE_GRUPPE[klasse], "ampel": ampel, "text": text[:MAX_TEXT],
              "tipp": _tipp(klasse, name, roh)[:MAX_TIPP]}
    if zahlen:
        modell["kennzahlen"] = zahlen
    if roh.get("status") == "success" and (aktionen := _aktionen(klasse, roh, erreichbar)):
        modell["aktionen"] = aktionen
    return modell


def modelle(liste: list) -> list[dict]:
    """Alle brauchbaren Geräte der Liste, in der Reihenfolge des Dienstes, ohne doppelte Kennungen, höchstens 64."""
    aus: list[dict] = []
    gesehen: set[str] = set()
    for roh in liste:
        m = geraet_modell(roh)
        if m is None or m["id"] in gesehen:
            continue
        gesehen.add(m["id"])
        aus.append(m)
        if len(aus) >= MAX_GERAETE:
            break
    return aus


# ---------- Zahl der unbekannten Geräte ----------
async def _zaehlen(kontext: gc.Kontext) -> int | None:
    """Anzahl unbekannter Geräte im Netz oder None. Freiwillig: jeder Fehler (nicht eingerichtet, Router weg, zu langsam,
    unbrauchbare Antwort) lässt nur die Zahl weg und nie die Karte scheitern. Vom Dienst kommt nur die Zahl, sonst nichts."""
    try:
        async with asyncio.timeout(UNBEKANNT_ZEIT):
            antwort, _ = await gc.aufruf("unbekannt_zaehlen", kontext, timeout=UNBEKANNT_ZEIT)
    except Exception as e:  # noqa: BLE001 (die Zahl ist nur eine Zugabe)
        logger.debug("Büro-Karte: keine Zahl der unbekannten Geräte ({})", type(e).__name__)
        return None
    if not isinstance(antwort, dict) or antwort.get("status") != "success":
        return None
    return _ganzzahl(antwort.get("anzahl"), 0, MAX_UNBEKANNT)


# ---------- Route ----------
@router.get("/buero/status")
async def status(request: Request, response: Response) -> dict:
    ich = await identitaet(request)
    _drosseln(ich.user_id)
    response.headers["Cache-Control"] = "no-store"
    if not gc.verfuegbar():
        raise HTTPException(503, gc.NUR_IM_BUERO)
    kontext = gc.Kontext(ich.user_id, ich.rolle, None, ich.name)
    try:
        (antwort, _), unbekannt = await asyncio.gather(
            gc.aufruf("status", kontext, geraet="alle", timeout=DIENST_ZEIT), _zaehlen(kontext))
    except gc.GeraeteNichtVerfuegbar as e:
        raise HTTPException(503, str(e)) from e
    if antwort.get("status") == "grenze":
        raise HTTPException(429, "Zu viele Abrufe, bitte kurz warten")
    liste = antwort.get("geraete")
    if antwort.get("status") != "success" or not isinstance(liste, list):
        raise HTTPException(502, "Der Gerätedienst hat nicht brauchbar geantwortet")
    geraete = modelle(liste)
    if ich.rolle not in STEUER_ROLLEN:
        for g in geraete:
            g.pop("aktionen", None)
    return {"geraete": geraete, "unbekannt": unbekannt,
            "stand": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


# ---------- Schalten mit dem Knopf ----------
async def _protokollieren(user_id: str, kennung: str, aktion: str, status: str, ergebnis: str) -> None:
    """Der Versuch steht in actions. Das Protokoll ist ein Nachweis und hält den Schaltvorgang nie auf: jeder Fehler wird nur geloggt."""
    try:
        async with asyncio.timeout(PROTOKOLL_ZEIT):
            await _protokoll_schreiben(user_id, kennung, aktion, status, ergebnis)
    except Exception as e:  # noqa: BLE001 (Nachweis, nicht Voraussetzung)
        logger.warning("Büro-Karte: Schaltvorgang nicht protokolliert ({})", type(e).__name__)


async def _protokoll_schreiben(user_id: str, kennung: str, aktion: str, status: str, ergebnis: str) -> None:
    db = shared_database()
    if db.configured:
        await db.execute(
            f"insert into {db.t('actions')} (user_id, tool, params, reason, risk, status, result, decided_at, finished_at) "
            f"values ($1::uuid, 'buerogeraet_steuern', $2::text::jsonb, 'Bürogerät (Knopf in der Oberfläche)', "
            f"'change'::{db.t('risk_class')}, $3::text::{db.t('action_status')}, $4::text::jsonb, now(), now())",
            user_id, json.dumps({"geraet": kennung, "aktion": aktion, "quelle": "oberflaeche"}), status,
            json.dumps({"status": ergebnis[:20]}))


@router.post("/buero/steuern")
async def steuern(request: Request, response: Response) -> dict:
    ich = await identitaet(request)
    if ich.rolle not in STEUER_ROLLEN:
        raise HTTPException(403, "Bürogeräte schalten dürfen nur Admins")
    _drosseln(ich.user_id, speicher=_schaltungen, grenze=STEUER_GRENZE, text="Zu viele Schaltvorgänge, bitte kurz warten")
    response.headers["Cache-Control"] = "no-store"
    if not gc.verfuegbar():
        raise HTTPException(503, gc.NUR_IM_BUERO)
    body = await lies_json(request, MAX_STEUER_BODY)
    kennung, aktion = body.get("geraet"), body.get("aktion")
    if not isinstance(kennung, str) or not KENNUNG_RE.fullmatch(kennung):
        raise HTTPException(400, "Unbekanntes Gerät")
    if not isinstance(aktion, str) or aktion not in STEUER_AKTIONEN:
        raise HTTPException(400, "Diese Aktion gibt es hier nicht")
    kontext = gc.Kontext(ich.user_id, ich.rolle, None, ich.name)
    try:
        antwort, _ = await gc.aufruf("steuern", kontext, geraet=kennung, argumente={"aktion": aktion}, timeout=STEUER_ZEIT)
    except gc.GeraeteNichtVerfuegbar as e:
        await _protokollieren(ich.user_id, kennung, aktion, "failed", "nicht_erreichbar")
        raise HTTPException(503, str(e)) from e
    status = antwort.get("status") if isinstance(antwort, dict) else None
    await _protokollieren(ich.user_id, kennung, aktion, "done" if status == "success" else "failed", str(status))
    logger.info("Büro-Karte: {} {} durch Profil {} ({})", kennung, aktion, ich.user_id[:8], status)
    if status == "success":
        return {"ok": True, "geraet": kennung, "aktion": aktion}
    if status == "grenze":
        raise HTTPException(429, "Zu viele Schaltvorgänge, bitte kurz warten")
    if status == "rejected":
        raise HTTPException(409, "Das geht mit diesem Gerät nicht")
    if status == "nicht_eingerichtet":
        raise HTTPException(409, "Dieses Gerät ist noch nicht für W.A.I.S. eingerichtet")
    raise HTTPException(502, "Das Gerät hat nicht geantwortet. Ist es erreichbar?")
