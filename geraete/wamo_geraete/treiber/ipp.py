"""Drucker über IPP (Brother MFC-L3750CDW: http://192.168.178.24:631/ipp/print, kein IPPS, kein PDF).

Nur zwei Operationen: Get-Printer-Attributes (feste Attributliste) und Print-Job mit selbst gerasterten Seiten
(raster.py, PWG-Raster oder JPEG). Der Pfad ist fest /ipp/print; ipp/faxout und jeder andere Pfad sind nicht
erreichbar, weil es keinen Parameter dafür gibt. Antworten werden mit Grenzen dekodiert (256 KB, 500 Attribute).
"""

from __future__ import annotations

import struct

from ..http_klein import HttpFehler, anfrage
from ..konfig import Geraet
from ..text import ganzzahl, name, text
from . import GeraetFehler, NichtEingerichtet

PFAD = "/ipp/print"
OP_PRINT_JOB = 0x0002
OP_GET_PRINTER_ATTRIBUTES = 0x000B
STATUS_ATTRIBUTE = (
    "printer-state", "printer-state-reasons", "printer-state-message", "printer-is-accepting-jobs",
    "marker-names", "marker-levels", "marker-colors", "marker-types", "printer-make-and-model",
    "queued-job-count", "media-ready", "document-format-supported", "pwg-raster-document-resolution-supported",
    "pwg-raster-document-type-supported", "print-color-mode-supported", "copies-supported",
)
ZUSTAENDE = {3: "bereit", 4: "druckt", 5: "angehalten"}
MAX_ANTWORT = 256 * 1024
MAX_ATTRIBUTE = 500

# Werte-Tags (RFC 8010)
T_INTEGER, T_BOOLEAN, T_ENUM = 0x21, 0x22, 0x23
T_OCTET, T_DATETIME, T_RESOLUTION, T_RANGE, T_BEGCOL, T_ENDCOL = 0x30, 0x31, 0x32, 0x33, 0x34, 0x37
T_TEXT, T_NAME, T_KEYWORD, T_URI, T_CHARSET, T_LANGUAGE, T_MIME, T_MEMBERNAME = 0x41, 0x42, 0x44, 0x45, 0x47, 0x48, 0x49, 0x4A
G_OPERATION, G_JOB, G_ENDE = 0x01, 0x02, 0x03


def _attr(tag: int, name_: str, werte: list) -> bytes:
    teile = []
    for i, wert in enumerate(werte):
        n = name_.encode("ascii") if i == 0 else b""
        if tag in (T_INTEGER, T_ENUM):
            v = struct.pack(">i", int(wert))
        elif tag == T_BOOLEAN:
            v = b"\x01" if wert else b"\x00"
        else:
            v = str(wert).encode("utf-8")
        teile.append(struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(v)) + v)
    return b"".join(teile)


def kodieren(operation: int, request_id: int, uri: str, operation_attr: list[tuple[int, str, list]],
             job_attr: list[tuple[int, str, list]] | None = None) -> bytes:
    kopf = struct.pack(">BBHI", 2, 0, operation, request_id)
    teile = [kopf, bytes([G_OPERATION]), _attr(T_CHARSET, "attributes-charset", ["utf-8"]),
             _attr(T_LANGUAGE, "attributes-natural-language", ["de"]), _attr(T_URI, "printer-uri", [uri])]
    teile += [_attr(t, n, w) for t, n, w in operation_attr]
    if job_attr:
        teile.append(bytes([G_JOB]))
        teile += [_attr(t, n, w) for t, n, w in job_attr]
    teile.append(bytes([G_ENDE]))
    return b"".join(teile)


def dekodieren(daten: bytes) -> tuple[int, dict[str, list]]:
    """Statuscode und Attribute (alle Gruppen zusammen, Werte als Liste). Sammlungen werden übersprungen."""
    if len(daten) < 9:
        raise GeraetFehler("IPP-Antwort zu kurz")
    _, _, status, _ = struct.unpack(">BBHI", daten[:8])
    attr: dict[str, list] = {}
    i, letzter, tiefe, anzahl = 8, None, 0, 0
    while i < len(daten):
        tag = daten[i]
        i += 1
        if tag <= 0x0F:  # Gruppenbegrenzer
            if tag == G_ENDE:
                break
            continue
        if i + 2 > len(daten):
            raise GeraetFehler("IPP-Antwort kaputt")
        (nlen,) = struct.unpack(">H", daten[i:i + 2])
        i += 2
        n = daten[i:i + nlen].decode("utf-8", "replace")
        i += nlen
        if i + 2 > len(daten):
            raise GeraetFehler("IPP-Antwort kaputt")
        (vlen,) = struct.unpack(">H", daten[i:i + 2])
        i += 2
        wert_roh = daten[i:i + vlen]
        i += vlen
        if len(wert_roh) != vlen:
            raise GeraetFehler("IPP-Antwort kaputt")
        if tag == T_BEGCOL:
            tiefe += 1
            continue
        if tag == T_ENDCOL:
            tiefe = max(0, tiefe - 1)
            continue
        if tiefe > 0 or tag == T_MEMBERNAME:
            continue
        if n:
            letzter = n
        if letzter is None:
            continue
        anzahl += 1
        if anzahl > MAX_ATTRIBUTE:
            break
        if tag in (T_INTEGER, T_ENUM) and vlen == 4:
            wert: object = struct.unpack(">i", wert_roh)[0]
        elif tag == T_BOOLEAN and vlen == 1:
            wert = wert_roh != b"\x00"
        elif tag == T_RESOLUTION and vlen == 9:
            x, y, einheit = struct.unpack(">iiB", wert_roh)
            wert = (x, y, einheit)
        elif tag == T_RANGE and vlen == 8:
            wert = struct.unpack(">ii", wert_roh)
        elif tag in (0x35, 0x36):  # textWithLanguage, nameWithLanguage: Sprache und Text mit eigener Länge
            if vlen < 4:
                continue
            (llen,) = struct.unpack(">H", wert_roh[:2])
            rest = wert_roh[2 + llen:]
            if len(rest) < 2:
                continue
            (tlen,) = struct.unpack(">H", rest[:2])
            wert = rest[2:2 + tlen].decode("utf-8", "replace")
        elif 0x41 <= tag <= 0x49:
            wert = wert_roh.decode("utf-8", "replace")
        else:
            continue
        attr.setdefault(letzter, []).append(wert)
    return status, attr


async def _ipp(geraet: Geraet, nutzlast: bytes, timeout: float = 8.0) -> tuple[int, dict[str, list]]:
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse des Druckers fehlt")
    try:
        antwort = await anfrage(geraet.adresse, geraet.port or 631, "POST", PFAD,
                                kopf={"Content-Type": "application/ipp"}, rumpf=nutzlast, timeout=timeout,
                                max_bytes=MAX_ANTWORT)
    except HttpFehler as e:
        raise GeraetFehler(f"Drucker nicht erreichbar ({e})") from e
    if antwort.status != 200:
        raise GeraetFehler(f"Drucker antwortet mit HTTP {antwort.status}")
    return dekodieren(antwort.rumpf)


def _uri(geraet: Geraet) -> str:
    return f"ipp://{geraet.adresse}:{geraet.port or 631}{PFAD}"


async def attribute(geraet: Geraet) -> dict[str, list]:
    nutzlast = kodieren(OP_GET_PRINTER_ATTRIBUTES, 1, _uri(geraet),
                        [(T_NAME, "requesting-user-name", ["wamo"]), (T_KEYWORD, "requested-attributes", list(STATUS_ATTRIBUTE))])
    status, attr = await _ipp(geraet, nutzlast)
    if status > 0x00FF:
        raise GeraetFehler(f"Drucker meldet IPP-Status {status:#06x}")
    return attr


def status_aus(attr: dict[str, list]) -> dict:
    """Feste, bereinigte Felder für Kern und Sprachmodell."""
    zustand = ganzzahl((attr.get("printer-state") or [None])[0])
    gruende = [name(g, 40) for g in attr.get("printer-state-reasons", [])[:10] if isinstance(g, str) and g != "none"]
    marker = []
    namen, staende, farben = attr.get("marker-names", []), attr.get("marker-levels", []), attr.get("marker-colors", [])
    for i, n in enumerate(namen[:8]):
        stand = ganzzahl(staende[i], -3, 100) if i < len(staende) else None
        marker.append({"name": name(n, 32), "stand_prozent": stand if stand is not None and stand >= 0 else None,
                       "farbe": name(farben[i], 16) if i < len(farben) else ""})
    return {
        "erreichbar": True,
        "zustand": ZUSTAENDE.get(zustand or 0, "unbekannt"),
        "meldungen": gruende,
        "meldung": text((attr.get("printer-state-message") or [""])[0], 120),
        "nimmt_auftraege": bool((attr.get("printer-is-accepting-jobs") or [False])[0]),
        "warteschlange": ganzzahl((attr.get("queued-job-count") or [0])[0], 0, 10000) or 0,
        "toner": marker,
        "papier": [name(m, 40) for m in attr.get("media-ready", [])[:6]],
        "modell": name((attr.get("printer-make-and-model") or [""])[0], 40),
    }


def faehigkeiten(attr: dict[str, list]) -> dict:
    """Druckweg: was der Drucker an Formaten, Auflösungen und Rastertypen annimmt (für raster.py)."""
    aufloesungen = [x for x, y, e in attr.get("pwg-raster-document-resolution-supported", []) if e == 3 and x == y]
    return {"formate": [f for f in attr.get("document-format-supported", []) if isinstance(f, str)],
            "pwg_aufloesungen": sorted(set(aufloesungen)),
            "pwg_typen": [t for t in attr.get("pwg-raster-document-type-supported", []) if isinstance(t, str)],
            "farbmodi": [m for m in attr.get("print-color-mode-supported", []) if isinstance(m, str)]}


async def status(geraet: Geraet) -> dict:
    return status_aus(await attribute(geraet))


async def drucken(geraet: Geraet, daten: bytes, mime: str, *, titel: str, kopien: int, farbe: bool) -> dict:
    """Ein Print-Job. mime: image/pwg-raster oder image/jpeg. Liefert job-id und Zustand."""
    if mime not in ("image/pwg-raster", "image/jpeg"):
        raise GeraetFehler("Format nicht vorgesehen")
    job = [(T_INTEGER, "copies", [kopien]), (T_KEYWORD, "print-color-mode", ["color" if farbe else "monochrome"]),
           (T_KEYWORD, "sides", ["one-sided"]), (T_KEYWORD, "media", ["iso_a4_210x297mm"])]
    nutzlast = kodieren(OP_PRINT_JOB, 2, _uri(geraet),
                        [(T_NAME, "requesting-user-name", ["wamo"]), (T_NAME, "job-name", [name(titel, 60) or "WAMO"]),
                         (T_MIME, "document-format", [mime])], job) + daten
    status_code, attr = await _ipp(geraet, nutzlast, timeout=60.0)
    if status_code > 0x00FF:
        raise GeraetFehler(f"Drucker lehnt den Auftrag ab (IPP-Status {status_code:#06x})")
    return {"auftrag": ganzzahl((attr.get("job-id") or [0])[0], 0, 10**9) or 0,
            "zustand": {3: "wartet", 4: "angehalten", 5: "druckt", 6: "unterbrochen", 7: "abgebrochen", 8: "abgebrochen", 9: "fertig"}.get(
                ganzzahl((attr.get("job-state") or [0])[0]) or 0, "angenommen")}
