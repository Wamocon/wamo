#!/usr/bin/env python3
"""WAMO Symbole für die Oberfläche als App (iPhone: Zum Home-Bildschirm, Android: Installieren).

Erzeugt aus dem WMC-Logo (ui/public/wmc-logo.png) die Wortmarke in WMC-Rot (#F40E0E) auf Weiß, ohne die kleine
Zeile "IT-Testmanagement" (auf 180 px nicht lesbar):

  ui/public/icons/icon-192.png            Manifest, any
  ui/public/icons/icon-512.png            Manifest, any
  ui/public/icons/icon-maskable-512.png   Manifest, maskable (Wortmarke in der Schutzzone, Rand für Rundung)
  ui/src/app/apple-icon.png               apple-touch-icon 180 x 180 (iOS rundet selbst, deshalb ohne Transparenz)

Nur die Form des Logos (der Alphakanal) wird benutzt, die Farbe setzt das Skript. Voraussetzung: Pillow
(pip install pillow). Aufruf im Repo-Wurzelordner:

  python scripts/wamo_icons_erzeugen.py            # schreibt die vier Dateien
  python scripts/wamo_icons_erzeugen.py --pruefen  # prüft nur, ob sie vorhanden und richtig groß sind (Exit-Code 1)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

WURZEL = Path(__file__).resolve().parent.parent
LOGO = WURZEL / "ui" / "public" / "wmc-logo.png"
ICONS = WURZEL / "ui" / "public" / "icons"
APPLE = WURZEL / "ui" / "src" / "app" / "apple-icon.png"

ROT = (0xF4, 0x0E, 0x0E)
WEISS = (255, 255, 255)

# (Ziel, Kantenlänge, Anteil der Wortmarke an der Breite)
# Maskable: Android schneidet bis auf einen Kreis mit 80 % Durchmesser zu. Die Wortmarke ist rund 3,3 mal so breit wie
# hoch, ihre Ecken liegen bei 68 % Breite noch innerhalb dieses Kreises (Halbdiagonale 0,36 der Kantenlänge, Rand 0,40).
ZIELE: tuple[tuple[Path, int, float], ...] = (
    (ICONS / "icon-192.png", 192, 0.80),
    (ICONS / "icon-512.png", 512, 0.80),
    (ICONS / "icon-maskable-512.png", 512, 0.68),
    (APPLE, 180, 0.80),
)


def wortmarke(logo: Image.Image) -> Image.Image:
    """Alphakanal der Wortmarke ohne die Zeile darunter: Das Logo hat drei Zeilengruppen (obere und untere Hälfte
    der Wortmarke mit WAMOCON dazwischen, dann die Unterzeile). Getrennt wird an der größten Lücke leerer Zeilen."""
    alpha = logo.convert("RGBA").getchannel("A")
    breite, hoehe = alpha.size
    belegt = [alpha.crop((0, y, breite, y + 1)).getbbox() is not None for y in range(hoehe)]
    anfang = next(y for y, b in enumerate(belegt) if b)
    letzte = max(y for y, b in enumerate(belegt) if b)
    # Lücken zwischen belegten Zeilen: (Länge, erste leere Zeile)
    luecken, y = [], anfang
    while y <= letzte:
        if belegt[y]:
            y += 1
            continue
        start = y
        while not belegt[y]:
            y += 1
        luecken.append((y - start, start))
    ende = letzte
    if luecken:
        laenge, start = max(luecken)
        if laenge >= hoehe * 0.06:  # nur eine deutliche Lücke trennt die Unterzeile ab
            ende = start - 1
    block = alpha.crop((0, anfang, breite, ende + 1))
    box = block.getbbox()
    return block.crop(box) if box else block


def symbol(marke: Image.Image, kante: int, anteil: float) -> Image.Image:
    """Quadrat mit weißem Grund und der Wortmarke in WMC-Rot, mittig, mit dem Breitenanteil."""
    ziel_breite = round(kante * anteil)
    ziel_hoehe = max(1, round(marke.height * ziel_breite / marke.width))
    maske = marke.resize((ziel_breite, ziel_hoehe), Image.Resampling.LANCZOS)
    bild = Image.new("RGB", (kante, kante), WEISS)
    bild.paste(Image.new("RGB", maske.size, ROT), ((kante - ziel_breite) // 2, (kante - ziel_hoehe) // 2), maske)
    return bild


def erzeugen() -> list[Path]:
    marke = wortmarke(Image.open(LOGO))
    geschrieben = []
    for pfad, kante, anteil in ZIELE:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        symbol(marke, kante, anteil).save(pfad, format="PNG", optimize=True)
        geschrieben.append(pfad)
    return geschrieben


def pruefen() -> int:
    fehler = 0
    for pfad, kante, _ in ZIELE:
        if not pfad.is_file():
            print(f"fehlt: {pfad.relative_to(WURZEL)}")
            fehler += 1
            continue
        with Image.open(pfad) as bild:
            if bild.size != (kante, kante):
                print(f"falsche Größe {bild.size}: {pfad.relative_to(WURZEL)}")
                fehler += 1
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
