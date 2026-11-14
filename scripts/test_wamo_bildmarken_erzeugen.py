"""Tests für scripts/wamo_bildmarken_erzeugen.py: Logo für den Dunkelmodus und abgerundetes Symbol der Desktop-App."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_bildmarken_erzeugen as bm  # noqa: E402


def logo_attrappe() -> Image.Image:
    """Schwarze Fläche oben, rote Fläche unten, Rest transparent, mit halbtransparenten Kanten."""
    bild = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for x in range(5, 35):
        for y in range(2, 8):
            bild.putpixel((x, y), (0, 0, 0, 255))
        bild.putpixel((x, 8), (0, 0, 0, 100))
        for y in range(12, 16):
            bild.putpixel((x, y), (0xF4, 0x0E, 0x0E, 255))
        bild.putpixel((x, 16), (0xF4, 0x0E, 0x0E, 90))
    return bild


class LogoDunkelTests(unittest.TestCase):
    def test_schwarz_wird_weiss_rot_bleibt_alpha_bleibt(self):
        hell = logo_attrappe()
        dunkel = bm.logo_dunkel(hell)
        self.assertEqual(dunkel.size, hell.size)
        self.assertEqual(dunkel.mode, "RGBA")
        self.assertEqual(dunkel.getpixel((10, 4)), (255, 255, 255, 255))
        self.assertEqual(dunkel.getpixel((10, 8)), (255, 255, 255, 100))
        self.assertEqual(dunkel.getpixel((10, 13)), (0xF4, 0x0E, 0x0E, 255))
        self.assertEqual(dunkel.getpixel((10, 16)), (0xF4, 0x0E, 0x0E, 90))
        self.assertEqual(dunkel.getpixel((0, 0))[3], 0, "der Hintergrund bleibt transparent")

    def test_kein_dunkles_sichtbares_pixel_im_echten_logo(self):
        daten = bm.logo_dunkel(Image.open(bm.LOGO)).tobytes()
        for i in range(0, len(daten), 4):
            r, g, b, a = daten[i : i + 4]
            if a >= 32 and max(r, g, b) - min(r, g, b) < 60:
                self.assertGreaterEqual(min(r, g, b), 230, "graue oder schwarze Schrift muss weiß sein")


class SymbolRundTests(unittest.TestCase):
    def test_ecken_transparent_mitte_unveraendert(self):
        quadrat = Image.new("RGBA", (256, 256), (255, 255, 255, 255))
        rund = bm.abrunden(quadrat)
        self.assertEqual(rund.size, (256, 256))
        for ecke in ((0, 0), (255, 0), (0, 255), (255, 255), (6, 6)):
            self.assertEqual(rund.getpixel(ecke)[3], 0, f"Ecke {ecke} muss transparent sein")
        self.assertEqual(rund.getpixel((128, 128)), (255, 255, 255, 255))
        self.assertEqual(rund.getpixel((128, 0))[3], 255, "Mitte der Kante bleibt voll")

    def test_abrunden_ist_wiederholbar(self):
        quadrat = Image.new("RGBA", (256, 256), (255, 255, 255, 255))
        einmal = bm.abrunden(quadrat)
        zweimal = bm.abrunden(einmal)
        self.assertEqual(einmal.tobytes(), zweimal.tobytes())

    def test_ico_mit_allen_groessen_und_runden_ecken(self):
        with tempfile.TemporaryDirectory() as tmp:
            png, ico = Path(tmp) / "wamo.png", Path(tmp) / "wamo.ico"
            Image.new("RGBA", (256, 256), (255, 255, 255, 255)).save(png)
            bm.desktop_symbol(png, ico)
            with Image.open(ico) as bild:
                self.assertEqual(set(bild.info["sizes"]), {(s, s) for s in bm.ICO_GROESSEN})
                self.assertEqual(bild.convert("RGBA").getpixel((0, 0))[3], 0)
            with Image.open(png) as bild:
                self.assertEqual(bild.convert("RGBA").getpixel((0, 0))[3], 0)


class DateienImRepoTests(unittest.TestCase):
    def test_pruefen_findet_die_erzeugten_dateien(self):
        self.assertEqual(bm.pruefen(), 0)


if __name__ == "__main__":
    unittest.main()
