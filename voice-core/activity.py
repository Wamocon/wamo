"""Meldet der Oberfläche live, was WAMO gerade tut (Aktivitäts-Zeitleiste).

Sitzt direkt hinter dem LLM. Beim Start eines Werkzeugaufrufs schickt er
{"type": "aktivitaet", "status": "laeuft", ...}, beim Ergebnis "fertig", "abgelehnt", "fehler" oder "wartet".
Argumente werden gekuerzt und nie komplett weitergereicht (getippte Texte koennten privat sein).

Freigaben: Meldet der Host status=needs_approval mit approval_id (kritische PC-Aktion), wird daraus eine
Freigabekarte (job "pc", Zeitleiste "wartet"). Die Karte zeigt, was der Host ausführen wird (grund, detail, ziel
aus seiner Antwort), nicht was das Modell behauptet. on_freigabe (pc_freigaben.PcFreigaben.anfrage) registriert
die Anfrage für diese Sitzung und liefert die fertige Karte; ohne on_freigabe gibt es keine Karte.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from loguru import logger

from pipecat.frames.frames import Frame, FunctionCallResultFrame, FunctionCallsStartedFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

from persona import sichtbar

# Welches Argument je Werkzeug als kurzer Hinweis in der Zeitleiste stehen darf
DETAIL_KEYS = {
    "webseite_oeffnen": "url", "programm_oeffnen": "name", "oeffnen_mit": "pfad",
    "fenster_fokussieren": "fenster", "tasten_druecken": "kombination", "websuche": "anfrage",
    "canvas_zeigen": "titel", "auf_bildschirm_klicken": "ziel", "bildschirm_ansehen": "frage",
    "merken": "art", "entwicklung_vorbereiten": "projekt", "maus_scrollen": "richtung",
}


def _detail(name: str, args: dict) -> str:
    key = DETAIL_KEYS.get(name)
    if key and isinstance(args, dict) and args.get(key):
        return str(args[key])[:80]
    if name == "tastatur_tippen" and isinstance(args, dict):
        return f"{len(str(args.get('text', '')))} Zeichen"
    return ""


APPROVAL_ID_RE = re.compile(r"[0-9a-f]{16}")
FREIGABE_AKTIONEN = ("powershell", "senden", "webseite", "klicken")
# Nur diese Werkzeuge können eine Freigabe-Anfrage des Hosts liefern (auf_bildschirm_klicken über maus_klicken)
FREIGABE_WERKZEUGE = frozenset({"powershell_lesen", "tastatur_tippen", "tasten_druecken", "webseite_oeffnen",
                                "auf_bildschirm_klicken"})
FREIGABE_DETAIL_ZEICHEN = 2000  # wie die Grenze der Oberfläche (ui/src/lib/nachrichten.ts)
FREIGABE_KURZ_ZEICHEN = 200


def _karten_text(wert: object, grenze: int, zeilen: bool = False) -> str | None:
    """Text vom Host für die Karte: ohne Steuer-, Format- und unsichtbare Zeichen (persona.sichtbar), zeilen=False
    ergibt eine Zeile. Länger als grenze: None. Die Karte muss alles zeigen, was ausgeführt wird."""
    text = wert if isinstance(wert, str) else ""
    if zeilen:
        text = "\n".join(sichtbar(z).rstrip() for z in text.replace("\r\n", "\n").split("\n"))
    else:
        text = " ".join(sichtbar(text).split())
    return text if len(text) <= grenze else None


def _als_dict(result) -> dict | None:
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def freigabe_anfrage(name: str, _args: dict, result: object, aktivitaet_id: str = "") -> dict | None:
    """Freigabe-Anfrage aus einem Host-Ergebnis (status=needs_approval mit gültiger approval_id) eines PC-Werkzeugs,
    sonst None. Ist der Inhalt zu lang für die Karte, gibt es keine: freigegeben wird nur, was ganz zu sehen ist."""
    if name not in FREIGABE_WERKZEUGE:
        return None
    data = _als_dict(result)
    if not data or data.get("status") != "needs_approval":
        return None
    aid, aktion = data.get("approval_id"), data.get("aktion")
    if not isinstance(aid, str) or not APPROVAL_ID_RE.fullmatch(aid) or aktion not in FREIGABE_AKTIONEN:
        return None
    roh = data.get("detail") if isinstance(data.get("detail"), str) else ""
    if aktion == "senden":
        roh = roh.replace("\r\n", "\n").replace("\n", "⏎\n").rstrip("\n")  # jedes Enter sendet: sichtbar machen
    detail = _karten_text(roh, FREIGABE_DETAIL_ZEICHEN, zeilen=True)
    grund = _karten_text(data.get("grund"), FREIGABE_KURZ_ZEICHEN)
    ziel = _karten_text(data.get("ziel"), FREIGABE_KURZ_ZEICHEN)
    anlass = _karten_text(data.get("anlass"), FREIGABE_KURZ_ZEICHEN)  # vom Modell, nur als Hinweis
    if not detail or grund is None or ziel is None or anlass is None:
        logger.warning("Freigabe {} ohne Karte: Inhalt fehlt oder ist zu lang", aid)
        return None
    gueltig = data.get("gueltig_s")
    return {
        "id": aid, "werkzeug": str(name)[:60], "aktion": aktion, "grund": grund or "PC-Aktion freigeben",
        "detail": detail, "ziel": ziel, "anlass": anlass, "aktivitaet_id": str(aktivitaet_id)[:100],
        "gueltig_s": gueltig if isinstance(gueltig, int) and not isinstance(gueltig, bool) and 0 < gueltig <= 3600
        else 300,
    }


def freigabe_karte(item: dict, projekt: str) -> dict:
    """Servernachricht für die Oberfläche (ui/src/lib/nachrichten.ts, Typ freigaben, job pc)."""
    return {"type": "freigaben", "job": "pc", "projekt": projekt, "items": [item]}


def _status(result) -> str:
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, json.JSONDecodeError):
        return "fertig"
    if isinstance(data, dict):
        status = str(data.get("status", "")).lower()
        if status == "needs_approval":
            # Nur mit Anfrage-ID gibt es einen Freigabeweg; sonst ist die Aktion einfach nicht ausgeführt
            aid = data.get("approval_id")
            return "wartet" if isinstance(aid, str) and APPROVAL_ID_RE.fullmatch(aid) else "abgelehnt"
        # locked: Bildschirm gesperrt; wartet: eine Freigabe ist offen, Eingaben ruhen
        if status in ("rejected", "blocked", "duplicate", "nicht_gefunden", "locked", "wartet"):
            return "abgelehnt"
        if status == "error" or data.get("ok") is False:
            return "fehler"
    return "fertig"


class ActivityReporter(FrameProcessor):
    """on_ergebnis(name, argumente, ergebnis): sieht jedes Werkzeugergebnis (bot.py: Sperre des PCs).
    on_freigabe(anfrage) -> Karte oder None: macht aus needs_approval eine Freigabekarte (pc_freigaben.py).
    Beide dürfen nach dem Bau der Pipeline gesetzt werden."""

    def __init__(
        self,
        *,
        on_ergebnis: Callable[[str, dict, object], Awaitable[None]] | None = None,
        on_freigabe: Callable[[dict], dict | None] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.on_ergebnis = on_ergebnis
        self.on_freigabe = on_freigabe

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, FunctionCallsStartedFrame):
            for call in frame.function_calls:
                await self.push_frame(RTVIServerMessageFrame(data={
                    "type": "aktivitaet", "id": call.tool_call_id, "name": call.function_name,
                    "detail": _detail(call.function_name, call.arguments), "status": "laeuft",
                }))
        elif isinstance(frame, FunctionCallResultFrame):
            await self.push_frame(RTVIServerMessageFrame(data={
                "type": "aktivitaet", "id": frame.tool_call_id, "name": frame.function_name,
                "detail": _detail(frame.function_name, frame.arguments),
                "status": "fehler" if frame.error else _status(frame.result),
            }))
            if self.on_freigabe is not None and not frame.error:
                try:
                    anfrage = freigabe_anfrage(frame.function_name, frame.arguments or {}, frame.result,
                                               frame.tool_call_id)
                    karte = self.on_freigabe(anfrage) if anfrage else None
                except Exception as e:  # noqa: BLE001 (eine kaputte Anfrage darf die Pipeline nie aufhalten)
                    logger.warning("Aktivität: Freigabekarte nicht erstellt ({})", type(e).__name__)
                    karte = None
                if karte:
                    await self.push_frame(RTVIServerMessageFrame(data=karte))
            if self.on_ergebnis is not None and not frame.error:
                try:
                    await self.on_ergebnis(frame.function_name, frame.arguments or {}, frame.result)
                except Exception as e:  # noqa: BLE001 (Beobachter dürfen die Pipeline nie aufhalten)
                    logger.warning("Aktivität: Auswertung von {} fehlgeschlagen ({})", frame.function_name,
                                   type(e).__name__)
        await self.push_frame(frame, direction)
