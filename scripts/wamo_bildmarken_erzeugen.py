#!/usr/bin/env python3
"""Bildmarken für den Dunkelmodus und die Desktop-App erzeugen.

1. ui/public/wmc-logo-dunkel.png: das WMC-Logo für den Dunkelmodus. Grundlage ist ui/public/wmc-logo.png (schwarz und
   rot auf transparent). Schwarze und graue Teile werden weiß, das Rot bleibt, der Hintergrund bleibt transparent. So
   steht das Logo im Dunkelmodus ohne weiße Platte.
2. desktop/assets/wamo.png und desktop/assets/wamo.ico: das Symbol der Desktop-App (Fenster, Taskleiste, Tray,
   Installer) mit abgerundeten Ecken. Die Ecken werden transparent; das Abrunden ist wiederholbar, ein zweiter Lauf
   ändert nichts mehr.

Voraussetzung: Pillow. Aufruf im Repo-Wurzelordner:

  python scripts/wamo_bildmarken_erzeugen.py            # schreibt die Dateien
  python scripts/wamo_bildmarken_erzeugen.py --pruefen  # prüft nur (Exit-Code 1 bei Abweichung)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

WURZEL = Path(__file__).resolve().parent.parent
LOGO = WURZEL / "ui" / "public" / "wmc-logo.png"
LOGO_DUNKEL = WURZEL / "ui" / "public" / "wmc-logo-dunkel.png"
SYMBOL_PNG = WURZEL / "desktop" / "assets" / "wamo.png"
SYMBOL_ICO = WURZEL / "desktop" / "assets" / "wamo.ico"

ICO_GROESSEN = (16, 24, 32, 48, 64, 128, 256)
RADIUS_ANTEIL = 0.18  # Eckenradius im Verhältnis zur Kantenlänge
UEBERABTASTUNG = 4  # Maske in vierfacher Größe zeichnen, dann verkleinern: glatte Kante
GRAU_SPANNE = 60  # Abstand zwischen größtem und kleinstem Farbwert, unter dem ein Pixel als schwarz oder grau gilt
WEISS = bytes((255, 255, 255))


def logo_dunkel(logo: Image.Image) -> Image.Image:
    """Schwarz und Grau werden weiß, farbige Pixel (das Rot) bleiben, der Alphakanal bleibt unverändert."""
    bild = logo.convert("RGBA")
    daten = bytearray(bild.tobytes())
    for i in range(0, len(daten), 4):
        r, g, b, a = daten[i : i + 4]
        if a and max(r, g, b) - min(r, g, b) < GRAU_SPANNE:
            daten[i : i + 3] = WEISS
    return Image.frombytes("RGBA", bild.size, bytes(daten))


def eckenmaske(groesse: tuple[int, int]) -> Image.Image:
    breite, hoehe = groesse
    gross = (breite * UEBERABTASTUNG, hoehe * UEBERABTASTUNG)
    maske = Image.new("L", gross, 0)
    radius = round(min(breite, hoehe) * RADIUS_ANTEIL * UEBERABTASTUNG)
    ImageDraw.Draw(maske).rounded_rectangle((0, 0, gross[0] - 1, gross[1] - 1), radius=radius, fill=255)
    return maske.resize(groesse, Image.Resampling.BOX)


def abrunden(bild: Image.Image) -> Image.Image:
    """Ecken transparent machen. Der Alphakanal wird nur verkleinert (Minimum mit der Maske), daher wiederholbar."""
    rgba = bild.convert("RGBA")
    alpha = ImageChops.darker(rgba.getchannel("A"), eckenmaske(rgba.size))
    rgba.putalpha(alpha)
    return rgba


def desktop_symbol(png: Path, ico: Path) -> None:
    rund = abrunden(Image.open(png))
    rund.save(png, format="PNG", optimize=True)
    rund.save(ico, format="ICO", sizes=[(s, s) for s in ICO_GROESSEN])


def erzeugen() -> list[Path]:
    logo_dunkel(Image.open(LOGO)).save(LOGO_DUNKEL, format="PNG", optimize=True)
    desktop_symbol(SYMBOL_PNG, SYMBOL_ICO)
    return [LOGO_DUNKEL, SYMBOL_PNG, SYMBOL_ICO]


def pruefen() -> int:
    fehler = []
    if not LOGO_DUNKEL.is_file():
        fehler.append(f"fehlt: {LOGO_DUNKEL.relative_to(WURZEL)}")
    else:
        with Image.open(LOGO) as hell, Image.open(LOGO_DUNKEL) as dunkel:
            if logo_dunkel(hell).tobytes() != dunkel.convert("RGBA").tobytes():
                fehler.append(f"veraltet (Logo geändert?): {LOGO_DUNKEL.relative_to(WURZEL)}")
    for pfad in (SYMBOL_PNG, SYMBOL_ICO):
        if not pfad.is_file():
            fehler.append(f"fehlt: {pfad.relative_to(WURZEL)}")
            continue
        with Image.open(pfad) as bild:
            rgba = bild.convert("RGBA")
            if rgba.getpixel((0, 0))[3] != 0 or rgba.getpixel((rgba.width - 1, rgba.height - 1))[3] != 0:
                fehler.append(f"Ecken nicht abgerundet: {pfad.relative_to(WURZEL)}")
            if pfad.suffix == ".ico" and set(bild.info.get("sizes", ())) != {(s, s) for s in ICO_GROESSEN}:
                fehler.append(f"Größen fehlen: {pfad.relative_to(WURZEL)}")
    for zeile in fehler:
        print(zeile)
    return 1 if fehler else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pruefen", action="store_true", help="nur prüfen, nichts schreiben")
    args = parser.parse_args()
    if args.pruefen:
        return pruefen()
    for pfad in erzeugen():
        print(f"geschrieben: {pfad.relative_to(WURZEL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
