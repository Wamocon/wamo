"""Eigene Rasterung für den Drucker, ohne PDF-Interpreter und ohne Ghostscript (F7).

Eingabe ist ein WAMO-Canvas-Inhalt (dokument, code, mermaid als Text, tabelle). Daraus werden A4-Seiten mit Pillow
gezeichnet: WMC-Kopf (Anschrift links, Logo rechts), Inhalt, Fußzeile mit Seitenzahl. Ausgabe als PWG-Raster
(image/pwg-raster, mehrseitig in einem Auftrag; der Brother nimmt 600 dpi, sgray_8 und srgb_8) oder JPEG je Seite.

Schrift: Liberation Sans Narrow (F18), falls vorhanden (/opt/wamo-geraete/schriften oder Systemschriften), sonst
DejaVu Sans Condensed, sonst Liberation Sans. Höchstens 20 Seiten; mehr lehnt seiten() vor dem Druck ab.
"""

from __future__ import annotations

import io
import os
import re
import struct
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

from .treiber import Abgelehnt

MAX_SEITEN = 20
MAX_TEXT = 200_000
MAX_SPALTEN = 20
MAX_ZEILEN = 2000
MAX_ZELLE = 500
ANSCHRIFT = "WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn"
LOGO = os.path.join(os.path.dirname(__file__), "assets", "wmc-logo.png")
SCHRIFT_ORDNER = [
    os.getenv("WAMO_GERAETE_SCHRIFTEN") or "/opt/wamo-geraete/schriften",
    "/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/liberation2",
    "/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts",
]
SCHRIFTEN = {
    "normal": ("LiberationSansNarrow-Regular.ttf", "DejaVuSansCondensed.ttf", "LiberationSans-Regular.ttf",
               "arialn.ttf", "arial.ttf", "DejaVuSans.ttf"),
    "fett": ("LiberationSansNarrow-Bold.ttf", "DejaVuSansCondensed-Bold.ttf", "LiberationSans-Bold.ttf",
             "arialnb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
    "mono": ("LiberationMono-Regular.ttf", "DejaVuSansMono.ttf", "consola.ttf", "cour.ttf"),
}
ARTEN = ("dokument", "code", "mermaid", "tabelle")
_UNSICHTBAR = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")


@dataclass
class Dokument:
    art: str
    titel: str
    text: str = ""
    spalten: list[str] = field(default_factory=list)
    zeilen: list[list[str]] = field(default_factory=list)


def _sauber(t: object, grenze: int) -> str:
    return _UNSICHTBAR.sub("", t if isinstance(t, str) else "")[:grenze]


def dokument_aus(roh: object) -> Dokument:
    """Prüft den Inhalt, den der Kern aus canvas_items mitschickt (Besitz prüft der Kern)."""
    if not isinstance(roh, dict):
        raise Abgelehnt("Dokument fehlt")
    art = roh.get("art")
    if art not in ARTEN:
        raise Abgelehnt("Dieses Canvas-Element lässt sich nicht drucken")
    titel = " ".join(_sauber(roh.get("titel"), 200).split()) or "WAMO"
    if art == "tabelle":
        spalten, zeilen = roh.get("spalten"), roh.get("zeilen")
        if not isinstance(spalten, list) or not isinstance(zeilen, list) or not 1 <= len(spalten) <= MAX_SPALTEN \
                or len(zeilen) > MAX_ZEILEN:
            raise Abgelehnt("Tabelle zu groß oder kaputt")
        return Dokument(art, titel, spalten=[_sauber(s, MAX_ZELLE) for s in spalten],
                        zeilen=[[_sauber(z, MAX_ZELLE) for z in (zeile if isinstance(zeile, list) else [])][:MAX_SPALTEN]
                                for zeile in zeilen])
    text = roh.get("text")
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise Abgelehnt("Text fehlt oder ist zu lang")
    return Dokument(art, titel, text=_sauber(text, MAX_TEXT))


def _schrift(art: str, groesse: int) -> ImageFont.ImageFont:
    for ordner in SCHRIFT_ORDNER:
        for datei in SCHRIFTEN[art]:
            pfad = os.path.join(ordner, datei)
            if os.path.isfile(pfad):
                try:
                    return ImageFont.truetype(pfad, groesse)
                except OSError:
                    continue
    return ImageFont.load_default(groesse)


def schrift_name() -> str:
    """Welche Schrift tatsächlich genutzt wird (für Status und Bericht)."""
    for ordner in SCHRIFT_ORDNER:
        for datei in SCHRIFTEN["normal"]:
            if os.path.isfile(os.path.join(ordner, datei)):
                return datei
    return "Pillow-Standard"


@dataclass
class _Zeile:
    text: str
    schrift: str = "normal"
    pt: float = 10.5
    einzug_mm: float = 0
    abstand_vor: float = 0  # in Zeilenhöhen
    linie: bool = False  # Trennlinie unter der Zeile (Tabellen)
    zellen: list[str] | None = None


def _bloecke(dok: Dokument) -> list[_Zeile]:
    """Rohzeilen mit Stil (vor dem Umbruch)."""
    zeilen = [_Zeile(dok.titel, "fett", 15, abstand_vor=0)]
    if dok.art == "tabelle":
        zeilen.append(_Zeile("", zellen=dok.spalten, schrift="fett", pt=9.5, linie=True, abstand_vor=0.6))
        zeilen += [_Zeile("", zellen=z, pt=9.5, linie=True) for z in dok.zeilen]
        return zeilen
    if dok.art in ("code", "mermaid"):
        zeilen += [_Zeile(z.rstrip() or " ", "mono", 8.5) for z in dok.text.replace("\t", "    ").splitlines()]
        return zeilen
    code = False
    for roh in dok.text.replace("\r\n", "\n").split("\n"):
        z = roh.rstrip()
        if z.strip().startswith("```"):
            code = not code
            continue
        if code:
            zeilen.append(_Zeile(z.replace("\t", "    ") or " ", "mono", 8.5))
            continue
        z = z.replace("**", "").replace("__", "")
        if not z.strip():
            zeilen.append(_Zeile("", pt=6))
        elif m := re.match(r"^(#{1,3})\s+(.*)$", z):
            zeilen.append(_Zeile(m.group(2), "fett", {1: 13.5, 2: 12, 3: 11}[len(m.group(1))], abstand_vor=0.5))
        elif m := re.match(r"^\s*[-*•]\s+(.*)$", z):
            zeilen.append(_Zeile("•  " + m.group(1), einzug_mm=4))
        elif m := re.match(r"^\s*(\d{1,3}[.)])\s+(.*)$", z):
            zeilen.append(_Zeile(f"{m.group(1)}  {m.group(2)}", einzug_mm=4))
        else:
            zeilen.append(_Zeile(z))
    return zeilen


def _umbrechen(text: str, schrift, breite: int) -> list[str]:
    if not text:
        return [""]
    erg: list[str] = []
    for absatz in text.split("\n"):
        woerter = absatz.split(" ")
        akt = ""
        for w in woerter:
            versuch = f"{akt} {w}" if akt else w
            if schrift.getlength(versuch) <= breite:
                akt = versuch
                continue
            if akt:
                erg.append(akt)
            # überlanges Wort hart trennen
            while schrift.getlength(w) > breite and len(w) > 1:
                teil = len(w)
                while teil > 1 and schrift.getlength(w[:teil]) > breite:
                    teil = max(1, teil * 3 // 4)
                erg.append(w[:teil])
                w = w[teil:]
            akt = w
        erg.append(akt)
    return erg


def seiten(dok: Dokument, *, dpi: int = 600, farbe: bool = False) -> list[Image.Image]:
    """A4-Seiten zeichnen. Wirft Abgelehnt, wenn mehr als MAX_SEITEN entstehen."""
    def px(mm: float) -> int:
        return round(mm / 25.4 * dpi)

    def pt(p: float) -> int:
        return max(1, round(p * dpi / 72))

    breite, hoehe = px(210), px(297)
    links, rechts, oben, unten = px(20), px(20), px(38), px(22)
    nutzbar = breite - links - rechts
    modus = "RGB" if farbe else "L"
    weiss = (255, 255, 255) if farbe else 255
    schwarz = (0, 0, 0) if farbe else 0
    grau = (107, 107, 107) if farbe else 107
    rot = (244, 14, 14) if farbe else 60

    # Layout: Zeilen umbrechen und auf Seiten verteilen
    gesetzt: list[list[tuple[_Zeile, list[str] | list[list[str]], int]]] = [[]]
    y = oben
    for z in _bloecke(dok):
        schrift = _schrift(z.schrift, pt(z.pt))
        zeilenhoehe = round(pt(z.pt) * 1.35)
        if z.zellen is not None:
            n = max(1, len(dok.spalten))
            spaltenbreite = nutzbar // n
            zellen = [_umbrechen(" ".join(c.split()), schrift, spaltenbreite - px(2)) for c in (z.zellen + [""] * n)[:n]]
            hoehe_block = max(len(c) for c in zellen) * zeilenhoehe + px(1.5)
            inhalt: list = zellen
        else:
            inhalt = _umbrechen(z.text, schrift, nutzbar - px(z.einzug_mm))
            hoehe_block = len(inhalt) * zeilenhoehe
        vor = round(z.abstand_vor * zeilenhoehe) if gesetzt[-1] else 0
        if y + vor + hoehe_block > hoehe - unten and gesetzt[-1]:
            gesetzt.append([])
            y, vor = oben, 0
            if len(gesetzt) > MAX_SEITEN:
                raise Abgelehnt(f"Das Dokument hätte mehr als {MAX_SEITEN} Seiten")
        gesetzt[-1].append((z, inhalt, y + vor))
        y += vor + hoehe_block

    logo = None
    if os.path.isfile(LOGO):
        with Image.open(LOGO) as roh:
            roh.load()
            logo_h = px(11)
            logo = roh.convert("RGBA").resize((max(1, round(roh.width * logo_h / roh.height)), logo_h), Image.LANCZOS)

    bilder = []
    gesamt = len(gesetzt)
    for nummer, inhalt_seite in enumerate(gesetzt, 1):
        bild = Image.new(modus, (breite, hoehe), weiss)
        d = ImageDraw.Draw(bild)
        kopf = _schrift("normal", pt(8.5))
        d.text((links, px(14)), ANSCHRIFT, font=kopf, fill=grau)
        if logo is not None:
            ziel = logo if farbe else logo.convert("LA")
            maske = ziel.getchannel("A")
            bild.paste(ziel.convert(modus), (breite - rechts - logo.width, px(10)), maske)
        d.line([(links, px(27)), (breite - rechts, px(27))], fill=rot, width=max(1, px(0.35)))
        fuss = _schrift("normal", pt(8.5))
        seite_text = f"Seite {nummer} von {gesamt}"
        d.text((breite - rechts - fuss.getlength(seite_text), hoehe - px(14)), seite_text, font=fuss, fill=grau)
        for z, inhalt, y0 in inhalt_seite:
            schrift = _schrift(z.schrift, pt(z.pt))
            zeilenhoehe = round(pt(z.pt) * 1.35)
            if z.zellen is not None:
                n = max(1, len(dok.spalten))
                spaltenbreite = nutzbar // n
                for i, zelle in enumerate(inhalt):
                    for k, zeile in enumerate(zelle):
                        d.text((links + i * spaltenbreite, y0 + k * zeilenhoehe), zeile, font=schrift, fill=schwarz)
                unten_block = y0 + max(len(c) for c in inhalt) * zeilenhoehe + px(0.5)
                d.line([(links, unten_block), (breite - rechts, unten_block)], fill=grau, width=max(1, px(0.15)))
            else:
                for k, zeile in enumerate(inhalt):
                    d.text((links + px(z.einzug_mm), y0 + k * zeilenhoehe), zeile, font=schrift, fill=schwarz)
        bilder.append(bild)
    return bilder


# ---------- PWG-Raster (PWG 5102.4) ----------
def _pwg_kopf(breite: int, hoehe: int, dpi: int, farbe: bool, gesamt: int) -> bytes:
    k = bytearray(1796)
    k[0:9] = b"PwgRaster"
    struct.pack_into(">II", k, 276, dpi, dpi)            # HWResolution
    struct.pack_into(">I", k, 340, 1)                     # NumCopies (Kopien über IPP)
    struct.pack_into(">II", k, 352, 595, 842)             # PageSize in Punkten (A4)
    bpp = 24 if farbe else 8
    struct.pack_into(">IIIIIIIII", k, 372, breite, hoehe, 0, 8, bpp, breite * bpp // 8, 0, 19 if farbe else 18, 0)
    struct.pack_into(">I", k, 420, 3 if farbe else 1)     # cupsNumColors
    struct.pack_into(">IIIIIIII", k, 452, gesamt, 1, 1, 0, 0, breite, hoehe, 0x00FFFFFF)
    name = b"iso_a4_210x297mm"
    k[1732:1732 + len(name)] = name
    return bytes(k)


def _zeile_kodieren(zeile: bytes, pixel: int) -> bytes:
    """Eine Bildzeile: Wiederholungen (0..127 = n+1 gleiche Pixel) und Literale (257-n Pixel, 2..128).
    Der Wert 128 kommt nie vor."""
    muster = re.compile(rb"(.)\1*" if pixel == 1 else rb"(...)\1*", re.S)
    aus = bytearray()
    literal = bytearray()

    def literal_schreiben():
        while literal:
            stueck = literal[:128 * pixel]
            del literal[:128 * pixel]
            n = len(stueck) // pixel
            if n == 1:
                aus.append(0)
            else:
                aus.append(257 - n)
            aus.extend(stueck)

    for m in muster.finditer(zeile):
        lauf = len(m.group(0)) // pixel
        if lauf == 1:
            literal.extend(m.group(1))
            continue
        literal_schreiben()
        while lauf > 0:
            n = min(128, lauf)
            if n == 1:
                literal.extend(m.group(1))
                literal_schreiben()
            else:
                aus.append(n - 1)
                aus.extend(m.group(1))
            lauf -= n
    literal_schreiben()
    return bytes(aus)


def pwg_raster(bilder: list[Image.Image], dpi: int, farbe: bool) -> bytes:
    teile = [b"RaS2"]
    pixel = 3 if farbe else 1
    for bild in bilder:
        bild = bild.convert("RGB" if farbe else "L")
        breite, hoehe = bild.size
        teile.append(_pwg_kopf(breite, hoehe, dpi, farbe, len(bilder)))
        roh = bild.tobytes()
        zeilenlaenge = breite * pixel
        y = 0
        while y < hoehe:
            zeile = roh[y * zeilenlaenge:(y + 1) * zeilenlaenge]
            wiederholung = 1
            while wiederholung < 256 and y + wiederholung < hoehe and \
                    roh[(y + wiederholung) * zeilenlaenge:(y + wiederholung + 1) * zeilenlaenge] == zeile:
                wiederholung += 1
            teile.append(bytes([wiederholung - 1]) + _zeile_kodieren(zeile, pixel))
            y += wiederholung
    return b"".join(teile)


def pwg_zeilen_dekodieren(daten: bytes, breite: int, hoehe: int, pixel: int) -> bytes:
    """Gegenstück zu pwg_raster für eine Seite ohne Kopf (nur für Tests)."""
    aus = bytearray()
    i = 0
    zeilenlaenge = breite * pixel
    while len(aus) < zeilenlaenge * hoehe:
        wiederholung = daten[i] + 1
        i += 1
        zeile = bytearray()
        while len(zeile) < zeilenlaenge:
            n = daten[i]
            i += 1
            if n < 128:
                zeile.extend(daten[i:i + pixel] * (n + 1))
                i += pixel
            elif n > 128:
                k = 257 - n
                zeile.extend(daten[i:i + k * pixel])
                i += k * pixel
            else:
                raise ValueError("128 kommt nicht vor")
        aus.extend(bytes(zeile) * wiederholung)
    return bytes(aus)


def jpeg(bild: Image.Image) -> bytes:
    puffer = io.BytesIO()
    bild.save(puffer, "JPEG", quality=85, dpi=(600, 600))
    return puffer.getvalue()
