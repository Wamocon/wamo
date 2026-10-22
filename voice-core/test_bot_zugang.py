"""Wache gegen eine Namensverwechslung in bot.py: Der PC-Zugang (Geräteregister) und der Modellzugang (Claude oder Haus)
sind zwei verschiedene Objekte. Heißen beide `zugang`, überschreibt der Modellzugang den PC-Zugang, und host_client
bekommt das falsche Objekt (AttributeError: 'Zugang' object has no attribute 'socket')."""

import unittest
from pathlib import Path

import bot


class ZugangNamenTest(unittest.TestCase):
    def test_host_client_bekommt_den_pc_zugang(self):
        quelle = Path(bot.__file__).read_text(encoding="utf-8")
        self.assertIn("host_client(geraet_zugang)", quelle)
        self.assertNotIn("host_client(zugang)", quelle)

    def test_pc_zugang_und_modellzugang_haben_verschiedene_namen(self):
        quelle = Path(bot.__file__).read_text(encoding="utf-8")
        self.assertIn("geraet_zugang = await pc_zugang(identity)", quelle)
        self.assertIn("zugang = await zugang_laden(identity.user_id)", quelle)
        self.assertNotIn("    zugang = await pc_zugang(identity)", quelle)


if __name__ == "__main__":
    unittest.main()
