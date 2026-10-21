"""Eigene Rasterung für den Drucker, ohne PDF-Interpreter und ohne Ghostscript (F7).

Eingabe ist ein WAMO-Canvas-Inhalt (dokument, code, mermaid als Text, tabelle). Daraus werden A4-Seiten mit Pillow
gezeichnet: WMC-Kopf (Anschrift links, Logo rechts), Inhalt, Fußzeile mit Seitenzahl. Ausgabe als PWG-Raster
(image/pwg-raster, mehrseitig in einem Auftrag; der Brother nimmt 600 dpi, sgray_8 und srgb_8) oder JPEG je Seite.

Zwei Schritte, damit Speicher und Rechenzeit begrenzt bleiben:
  1. layout(): bricht den Text um und verteilt ihn auf Seiten (nur Text, wenige KB). Lehnt Dokumente ab, die nicht in
     MAX_SEITEN Seiten passen, bevor Pillow etwas zeichnet.
  2. Layout.zeichne(n) / Layout.seiten(): zeichnet jeweils eine Seite (bei 600 dpi 35 MB grau, 104 MB Farbe). Der
     Aufrufer kodiert sie sofort und gibt sie frei (pwg_raster verbraucht einen Generator Seite für Seite).
Rechenzeit: Jeder Schritt hat eine Frist (Frist, Standard 60 s); danach Abgelehnt. Wörter werden vor dem Messen in
Stücke von höchstens MAX_WORT Zeichen zerlegt, Zeilen sind auf MAX_ZEILE, Zellen auf MAX_ZELLE Zeichen begrenzt.

Schrift: Liberation Sans Narrow (F18), falls vorhanden (/opt/wamo-geraete/schriften oder Systemschriften), sonst
DejaVu Sans Condensed, sonst Liberation Sans. Höchstens 20 Seiten.
"""

from __future__ import annotations

import io
import os
import re
import struct
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

from .treiber import Abgelehnt

MAX_SEITEN = 20
MAX_TEXT = 60_000
MAX_ZEILE = 2000  # Zeichen je Textzeile; längere Zeilen werden an Leerzeichen geteilt
MAX_WORT = 120  # Wörter werden vor dem Messen in Stücke dieser Länge zerlegt
MAX_SPALTEN = 20
MAX_ZEILEN = 2000
MAX_ZELLE = 120
MAX_AUSGABE = 100 * 1024 * 1024  # kodiertes Raster höchstens
FRIST_LAYOUT_SECS = 60.0
FRIST_ZEICHNEN_SECS = 90.0
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
# Seitenmaße in mm (Ränder wie in layout()); daraus die untere Grenze für den Platzbedarf
SEITE_MM = (210.0, 297.0)
RAND_MM = (20.0, 20.0, 38.0, 22.0)  # links, rechts, oben, unten
ZEILENFAKTOR = 1.35


class Frist:
    """Zeitgrenze für Schleifen in einem Arbeitsthread (ein Thread lässt sich nicht abbrechen)."""

    def __init__(self, secs: float, uhr=time.monotonic):
        self._uhr = uhr
        self._ende = uhr() + secs

    def pruefen(self) -> None:
        if self._uhr() > self._ende:
            raise Abgelehnt("Das Dokument ist zu aufwendig zum Drucken")


@dataclass
class Dokument:
    art: str
    titel: str
    text: str = ""
    spalten: list[str] = field(default_factory=list)
    zeilen: list[list[str]] = field(default_factory=list)


def _sauber(t: object, grenze: int) -> str:
    return _UNSICHTBAR.sub("", t if isinstance(t, str) else "")[:grenze]


def _lange_zeilen_teilen(text: str) -> str:
    """Zeilen über MAX_ZEILE Zeichen an einem Leerzeichen teilen (sonst hart), damit kein Absatz die Umbrechung
    ausbremst."""
    if len(text) <= MAX_ZEILE and "\n" not in text:
        return text
    aus: list[str] = []
    for zeile in text.split("\n"):
        while len(zeile) > MAX_ZEILE:
            schnitt = zeile.rfind(" ", MAX_ZEILE // 2, MAX_ZEILE)
            schnitt = schnitt if schnitt > 0 else MAX_ZEILE
            aus.append(zeile[:schnitt])
            zeile = zeile[schnitt:].lstrip(" ")
        aus.append(zeile)
    return "\n".join(aus)


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
    return Dokument(art, titel, text=_lange_zeilen_teilen(_sauber(text, MAX_TEXT)))


_lokal = threading.local()  # FreeType-Schriften sind nicht thread-sicher: je Arbeitsthread eine eigene Kopie


def _schrift(art: str, groesse: int) -> ImageFont.ImageFont:
    cache = _lokal.__dict__.setdefault("schriften", {})
    if (art, groesse) in cache:
        return cache[(art, groesse)]
    schrift = None
    for ordner in SCHRIFT_ORDNER:
        for datei in SCHRIFTEN[art]:
            pfad = os.path.join(ordner, datei)
            if schrift is None and os.path.isfile(pfad):
                try:
                    schrift = ImageFont.truetype(pfad, groesse)
                except OSError:
                    continue
    schrift = schrift or ImageFont.load_default(groesse)
    if len(cache) >= 32:
        cache.clear()
    cache[(art, groesse)] = schrift
    return schrift


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
    zellen: list[str] | None = None


def _bloecke(dok: Dokument) -> list[_Zeile]:
    """Rohzeilen mit Stil (vor dem Umbruch)."""
    zeilen = [_Zeile(dok.titel, "fett", 15, abstand_vor=0)]
    if dok.art == "tabelle":
        zeilen.append(_Zeile("", zellen=dok.spalten, schrift="fett", pt=9.5, abstand_vor=0.6))
        zeilen += [_Zeile("", zellen=z, pt=9.5) for z in dok.zeilen]
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


def _woerter(absatz: str) -> Iterator[tuple[str, bool]]:
    """Wörter eines Absatzes; lange Wörter in Stücke zu MAX_WORT Zeichen (fortsetzung=True: ohne Leerzeichen davor)."""
    for wort in absatz.split(" "):
        if len(wort) <= MAX_WORT:
            yield wort, False
            continue
        for i in range(0, len(wort), MAX_WORT):
            yield wort[i:i + MAX_WORT], i > 0


def _umbrechen(text: str, schrift, breite: int, frist: Frist | None = None) -> list[str]:
    if not text:
        return [""]
    erg: list[str] = []
    for absatz in text.split("\n"):
        akt = ""
        for w, fortsetzung in _woerter(absatz):
            if frist is not None:
                frist.pruefen()
            trenner = "" if fortsetzung or not akt else " "
            versuch = f"{akt}{trenner}{w}"
            if schrift.getlength(versuch) <= breite:
                akt = versuch
                continue
            if akt:
                erg.append(akt)
            # Stück, das allein nicht passt, hart trennen (höchstens MAX_WORT Zeichen, also wenige Messungen)
            while schrift.getlength(w) > breite and len(w) > 1:
                teil = len(w)
                while teil > 1 and schrift.getlength(w[:teil]) > breite:
                    teil = max(1, teil * 3 // 4)
                erg.append(w[:teil])
                w = w[teil:]
            akt = w
        erg.append(akt)
    return erg


class Layout:
    """Auf Seiten verteilter Text eines Dokuments. zeichne() malt eine einzelne Seite."""

    def __init__(self, dok: Dokument, dpi: int, farbe: bool, gesetzt: list[list[tuple]], breite: int, hoehe: int):
        self.dok, self.dpi, self.farbe = dok, dpi, farbe
        self._gesetzt = gesetzt
        self.breite, self.hoehe = breite, hoehe
        self._logo: Image.Image | None = None

    @property
    def seitenzahl(self) -> int:
        return len(self._gesetzt)

    def _px(self, mm: float) -> int:
        return round(mm / 25.4 * self.dpi)

    def _pt(self, p: float) -> int:
        return max(1, round(p * self.dpi / 72))

    def _logo_bild(self) -> Image.Image | None:
        if self._logo is None and os.path.isfile(LOGO):
            with Image.open(LOGO) as roh:
                roh.load()
                logo_h = self._px(11)
                self._logo = roh.convert("RGBA").resize((max(1, round(roh.width * logo_h / roh.height)), logo_h),
                                                        Image.LANCZOS)
        return self._logo

    def zeichne(self, nummer: int, frist: Frist | None = None) -> Image.Image:
        """Seite nummer (1 bis seitenzahl) als Bild: grau (L) oder Farbe (RGB)."""
        if not 1 <= nummer <= self.seitenzahl:
            raise ValueError("Seite gibt es nicht")
        px, pt = self._px, self._pt
        links, rechts = px(RAND_MM[0]), px(RAND_MM[1])
        nutzbar = self.breite - links - rechts
        modus = "RGB" if self.farbe else "L"
        weiss = (255, 255, 255) if self.farbe else 255
        schwarz = (0, 0, 0) if self.farbe else 0
        grau = (107, 107, 107) if self.farbe else 107
        rot = (244, 14, 14) if self.farbe else 60
        bild = Image.new(modus, (self.breite, self.hoehe), weiss)
        d = ImageDraw.Draw(bild)
        kopf = _schrift("normal", pt(8.5))
        d.text((links, px(14)), ANSCHRIFT, font=kopf, fill=grau)
        if (logo := self._logo_bild()) is not None:
            ziel = logo if self.farbe else logo.convert("LA")
            bild.paste(ziel.convert(modus), (self.breite - rechts - logo.width, px(10)), ziel.getchannel("A"))
        d.line([(links, px(27)), (self.breite - rechts, px(27))], fill=rot, width=max(1, px(0.35)))
        seite_text = f"Seite {nummer} von {self.seitenzahl}"
        d.text((self.breite - rechts - kopf.getlength(seite_text), self.hoehe - px(14)), seite_text, font=kopf, fill=grau)
        for z, inhalt, y0 in self._gesetzt[nummer - 1]:
            if frist is not None:
                frist.pruefen()
            schrift = _schrift(z.schrift, pt(z.pt))
            zeilenhoehe = round(pt(z.pt) * ZEILENFAKTOR)
            if z.zellen is not None:
                n = max(1, len(self.dok.spalten))
                spaltenbreite = nutzbar // n
                for i, zelle in enumerate(inhalt):
                    for k, zeile in enumerate(zelle):
                        d.text((links + i * spaltenbreite, y0 + k * zeilenhoehe), zeile, font=schrift, fill=schwarz)
                unten_block = y0 + max(len(c) for c in inhalt) * zeilenhoehe + px(0.5)
                d.line([(links, unten_block), (self.breite - rechts, unten_block)], fill=grau, width=max(1, px(0.15)))
            else:
                for k, zeile in enumerate(inhalt):
                    d.text((links + px(z.einzug_mm), y0 + k * zeilenhoehe), zeile, font=schrift, fill=schwarz)
        return bild

    def seiten(self, frist: Frist | None = None) -> Iterator[Image.Image]:
        """Seiten nacheinander; jede nur so lange im Speicher, wie der Aufrufer sie hält."""
        for nummer in range(1, self.seitenzahl + 1):
            yield self.zeichne(nummer, frist)


def _mindesthoehe_mm(bloecke: list[_Zeile]) -> float:
    """Untere Grenze des Platzbedarfs: jeder Block braucht mindestens eine Zeile seiner Schrift."""
    return sum(z.pt * ZEILENFAKTOR / 72 * 25.4 for z in bloecke)


def layout(dok: Dokument, *, dpi: int = 600, farbe: bool = False, frist: Frist | None = None) -> Layout:
    """Text umbrechen und auf Seiten verteilen. Wirft Abgelehnt bei mehr als MAX_SEITEN Seiten oder abgelaufener Frist."""
    frist = frist or Frist(FRIST_LAYOUT_SECS)
    bloecke = _bloecke(dok)
    nutzbar_mm = SEITE_MM[1] - RAND_MM[2] - RAND_MM[3]
    if _mindesthoehe_mm(bloecke) > MAX_SEITEN * nutzbar_mm:  # bevor etwas gemessen wird
        raise Abgelehnt(f"Das Dokument hätte mehr als {MAX_SEITEN} Seiten")

    def px(mm: float) -> int:
        return round(mm / 25.4 * dpi)

    def pt(p: float) -> int:
        return max(1, round(p * dpi / 72))

    breite, hoehe = px(SEITE_MM[0]), px(SEITE_MM[1])
    links, rechts, oben, unten = px(RAND_MM[0]), px(RAND_MM[1]), px(RAND_MM[2]), px(RAND_MM[3])
    nutzbar = breite - links - rechts
    gesetzt: list[list[tuple]] = [[]]
    y = oben
    for z in bloecke:
        frist.pruefen()
        schrift = _schrift(z.schrift, pt(z.pt))
        zeilenhoehe = round(pt(z.pt) * ZEILENFAKTOR)
        if z.zellen is not None:
            n = max(1, len(dok.spalten))
            spaltenbreite = nutzbar // n
            zellen = [_umbrechen(" ".join(c.split()), schrift, spaltenbreite - px(2), frist)
                      for c in (z.zellen + [""] * n)[:n]]
            hoehe_block = max(len(c) for c in zellen) * zeilenhoehe + px(1.5)
            inhalt: list = zellen
        else:
            inhalt = _umbrechen(z.text, schrift, nutzbar - px(z.einzug_mm), frist)
            hoehe_block = len(inhalt) * zeilenhoehe
        vor = round(z.abstand_vor * zeilenhoehe) if gesetzt[-1] else 0
        if y + vor + hoehe_block > hoehe - unten and gesetzt[-1]:
            gesetzt.append([])
            y, vor = oben, 0
            if len(gesetzt) > MAX_SEITEN:
                raise Abgelehnt(f"Das Dokument hätte mehr als {MAX_SEITEN} Seiten")
        gesetzt[-1].append((z, inhalt, y + vor))
        y += vor + hoehe_block
    return Layout(dok, dpi, farbe, gesetzt, breite, hoehe)


def seiten(dok: Dokument, *, dpi: int = 600, farbe: bool = False) -> list[Image.Image]:
    """Alle Seiten auf einmal (nur für kleine Dokumente und Tests; der Dienst nutzt layout() und Layout.seiten())."""
    lay = layout(dok, dpi=dpi, farbe=farbe)
    return list(lay.seiten())


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


def pwg_raster(bilder: Iterable[Image.Image], dpi: int, farbe: bool, gesamt: int | None = None,
               frist: Frist | None = None) -> bytes:
    """PWG-Raster aus Seiten. bilder darf ein Generator sein (dann gesamt angeben): jede Seite wird sofort kodiert und
    freigegeben. Wirft Abgelehnt, wenn die Ausgabe MAX_AUSGABE überschreitet oder die Frist abläuft."""
    if gesamt is None:
        bilder = list(bilder)
        gesamt = len(bilder)
    teile = [b"RaS2"]
    groesse = 4
    pixel = 3 if farbe else 1
    for bild in bilder:
        bild = bild.convert("RGB" if farbe else "L")
        breite, hoehe = bild.size
        kopf = _pwg_kopf(breite, hoehe, dpi, farbe, gesamt)
        teile.append(kopf)
        groesse += len(kopf)
        roh = bild.tobytes()
        del bild
        zeilenlaenge = breite * pixel
        y = 0
        while y < hoehe:
            if frist is not None and y % 64 == 0:
                frist.pruefen()
            zeile = roh[y * zeilenlaenge:(y + 1) * zeilenlaenge]
            wiederholung = 1
            while wiederholung < 256 and y + wiederholung < hoehe and \
                    roh[(y + wiederholung) * zeilenlaenge:(y + wiederholung + 1) * zeilenlaenge] == zeile:
                wiederholung += 1
            kodiert = bytes([wiederholung - 1]) + _zeile_kodieren(zeile, pixel)
            teile.append(kodiert)
            groesse += len(kodiert)
            if groesse > MAX_AUSGABE:
                raise Abgelehnt("Das Dokument ist zu umfangreich zum Drucken")
            y += wiederholung
        del roh
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
