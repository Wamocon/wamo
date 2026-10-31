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
    "aufgabe_verteilen": "art", "element_klicken": "beschreibung", "elemente_auflisten": "fenster",
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
                                "auf_bildschirm_klicken", "element_klicken", "element_text_setzen"})
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


# ---------- Lichtrahmen am Laptop ----------
# Der Host meldet nach gelungenen Aktionen am Bildschirm ein Rechteck (Zielfenster), bei Klicks den Punkt und den Monitor,
# nur als ganze Zahlen (host/wamo_host.py _markierung). Der Kern prüft sie noch einmal, versieht sie mit der Gerätekennung
# und reicht sie an die Oberfläche weiter; gezeichnet wird nur auf dem Laptop, der handelt (desktop/main.js vergleicht).
MARKIERUNG_ARTEN = {
    "fenster_fokussieren": "fokus", "tastatur_tippen": "text", "element_text_setzen": "text",
    "tasten_druecken": "tasten", "maus_scrollen": "scrollen",
    "auf_bildschirm_klicken": "klick", "element_klicken": "klick",
}
MARK_POS_MIN, MARK_POS_MAX = -32768, 32767  # wie host/windows_control.py und ui/src/lib/nachrichten.ts
MARK_GROESSE_MAX = 32768
GERAET_RE = re.compile(r"pc-[a-z0-9]+(?:-[a-z0-9]+)*")
RECHNER_RE = re.compile(r"[A-Za-z0-9._-]{1,63}")


def _ganz(wert: object) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool)


def _pos(wert: object) -> bool:
    return _ganz(wert) and MARK_POS_MIN <= wert <= MARK_POS_MAX


def _rechteck(wert: object) -> dict | None:
    """Rechteck {x, y, b, h} aus genau diesen ganzen Zahlen innerhalb der Grenzen, sonst None."""
    if not isinstance(wert, dict) or set(wert) != {"x", "y", "b", "h"}:
        return None
    if not (_pos(wert["x"]) and _pos(wert["y"])):
        return None
    if not all(_ganz(wert[k]) and 1 <= wert[k] <= MARK_GROESSE_MAX for k in ("b", "h")):
        return None
    return {k: wert[k] for k in ("x", "y", "b", "h")}


def _punkt(wert: object, monitor: dict) -> dict | None:
    """Klickpunkt {x, y} nur innerhalb des Monitors."""
    if not isinstance(wert, dict) or set(wert) != {"x", "y"} or not (_pos(wert["x"]) and _pos(wert["y"])):
        return None
    inside = monitor["x"] <= wert["x"] < monitor["x"] + monitor["b"] and monitor["y"] <= wert["y"] < monitor["y"] + monitor["h"]
    return {"x": wert["x"], "y": wert["y"]} if inside else None


def markierung_nachricht(name: str, result: object, geraet: str | None, rechner: str | None) -> dict | None:
    """Servernachricht {"type": "markierung", ...} für den Lichtrahmen oder None. Nur bei status=success eines Werkzeugs
    aus MARKIERUNG_ARTEN und nur mit gültigen Zahlen; geraet ist die Kennung des Laptops (Tunnel je Gerät) oder None,
    rechner sein Rechnername. Ein Titel oder Inhalt wird nie übernommen: nur die festen Felder fenster, monitor, punkt."""
    art = MARKIERUNG_ARTEN.get(name)
    data = _als_dict(result)
    if art is None or not data or data.get("status") != "success":
        return None
    roh = data.get("markierung")
    if not isinstance(roh, dict):
        return None
    fenster, monitor = _rechteck(roh.get("fenster")), _rechteck(roh.get("monitor"))
    if fenster is None or monitor is None:
        return None
    if geraet is not None and not (isinstance(geraet, str) and len(geraet) <= 23 and GERAET_RE.fullmatch(geraet)):
        return None  # eine Kennung, die nicht stimmt, ist kein "unbekannt": dann zeichnet kein Laptop
    nachricht = {
        "type": "markierung", "art": art, "geraet": geraet,
        "rechner": rechner if isinstance(rechner, str) and RECHNER_RE.fullmatch(rechner) else None,
        "fenster": fenster, "monitor": monitor,
    }
    if art == "klick" and (punkt := _punkt(roh.get("punkt"), monitor)) is not None:
        nachricht["punkt"] = punkt
    return nachricht


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
    markierung_von() -> (Gerätekennung, Rechnername) des gesteuerten Laptops: Mit ihm meldet der Reporter nach gelungenen
    Aktionen am Bildschirm den Lichtrahmen (markierung_nachricht). Alle dürfen nach dem Bau der Pipeline gesetzt werden."""

    def __init__(
        self,
        *,
        on_ergebnis: Callable[[str, dict, object], Awaitable[None]] | None = None,
        on_freigabe: Callable[[dict], dict | None] | None = None,
        markierung_von: Callable[[], tuple[str | None, str | None]] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.on_ergebnis = on_ergebnis
        self.on_freigabe = on_freigabe
        self.markierung_von = markierung_von  # (Gerätekennung, Rechnername) des gesteuerten Laptops; None = kein Rahmen

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
            if self.markierung_von is not None and not frame.error:
                try:
                    nachricht = markierung_nachricht(frame.function_name, frame.result, *self.markierung_von())
                except Exception as e:  # noqa: BLE001 (der Rahmen ist nur eine Anzeige)
                    logger.debug("Aktivität: Markierung nicht erstellt ({})", type(e).__name__)
                    nachricht = None
                if nachricht:
                    await self.push_frame(RTVIServerMessageFrame(data=nachricht))
            if self.on_ergebnis is not None and not frame.error:
                try:
                    await self.on_ergebnis(frame.function_name, frame.arguments or {}, frame.result)
                except Exception as e:  # noqa: BLE001 (Beobachter dürfen die Pipeline nie aufhalten)
                    logger.warning("Aktivität: Auswertung von {} fehlgeschlagen ({})", frame.function_name,
                                   type(e).__name__)
        await self.push_frame(frame, direction)
