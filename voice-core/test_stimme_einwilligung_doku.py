"""Der Text der Einwilligung steht zweimal: als Konstante im Kern (stimme_einwilligung.py) und im Abschnitt "Stimmabdruck" der Datenschutzerklärung
(docs/labor-datenschutz-entwurf.md). Dieser Test hält beide gleich und prüft, dass der Abschnitt alles nennt, was die Einwilligung braucht.
Der Hinweistext für die Besucher im Labor (Abschnitt 1) und die Seite des Labor-Dienstes bleiben davon unberührt.

Ausführen:  python -m pytest -q test_stimme_einwilligung_doku.py   (im Ordner voice-core/)
"""

import re
import unittest
from pathlib import Path

from stimme_einwilligung import EINWILLIGUNG_TEXT, EINWILLIGUNG_VERSION

WURZEL = Path(__file__).resolve().parent.parent
DOKUMENT = WURZEL / "docs" / "labor-datenschutz-entwurf.md"
UEBERSCHRIFT = "## 4. Stimmabdruck (nur Büro-Profile, nicht im Testlabor)"


def normal(text: str) -> str:
    """Markdown-Zeichen (**, Rückstriche, Zitatstriche am Zeilenanfang) und Leerraum vereinheitlichen."""
    ohne_zitat = re.sub(r"(?m)^>\s?", "", text)
    return re.sub(r"\s+", " ", ohne_zitat.replace("**", "").replace("`", "")).strip()


def dokument() -> str:
    return DOKUMENT.read_text(encoding="utf-8")


def abschnitt_stimme() -> str:
    text = dokument()
    treffer = re.search(rf"^{re.escape(UEBERSCHRIFT)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert treffer, "Abschnitt Stimmabdruck im Dokument"
    return treffer.group(1)


class EinwilligungsTextTests(unittest.TestCase):
    def test_der_text_im_dokument_ist_die_konstante_im_kern(self):
        self.assertIn(normal(EINWILLIGUNG_TEXT), normal(abschnitt_stimme()))

    def test_die_version_im_dokument_ist_die_version_im_kern(self):
        abschnitt = abschnitt_stimme()
        self.assertRegex(abschnitt, rf"Version `{re.escape(EINWILLIGUNG_VERSION)}`")
        versionen = set(re.findall(r"`(stimme-\d+)`", abschnitt))
        self.assertEqual(versionen, {EINWILLIGUNG_VERSION})  # keine zweite, ältere Fassung im Abschnitt

    def test_der_text_steht_nur_einmal_im_dokument(self):
        self.assertEqual(normal(dokument()).count(normal(EINWILLIGUNG_TEXT)), 1)


class AbschnittTests(unittest.TestCase):
    def test_abschnitt_ist_klar_abgegrenzt_und_steht_nach_den_bisherigen(self):
        text = dokument()
        self.assertEqual(text.count(UEBERSCHRIFT), 1)
        reihenfolge = [text.index(f"## {n}.") for n in (1, 2, 3, 4)]
        self.assertEqual(reihenfolge, sorted(reihenfolge))

    def test_abschnitt_nennt_zweck_grundlage_speicherung_loeschung_und_grenzen(self):
        a = normal(abschnitt_stimme())
        for teil in (
            "Zweck",
            "Art. 9 Abs. 2 lit. a DSGVO",
            "ausdrückliche Einwilligung",
            "freiwillig",
            "ohne Nachteil",
            "192 Zahlen",
            "Nie eine Aufnahme",
            "eigenen Server",
            "speakers",
            "jederzeit",
            "Einstellungen",
            "Widerruf",
            "ersetzt keine Freigabe",
            "Aufnahmen",
            "geklonte",
            "Im Testlabor gibt es keinen Stimmabdruck",
        ):
            self.assertIn(teil, a, teil)

    def test_abschnitt_nennt_die_offenen_punkte_fuer_die_verantwortliche_person(self):
        a = normal(abschnitt_stimme())
        for teil in ("Datenschutz-Folgenabschätzung", "Art. 35", "Sicherungen", "Beschäftigten"):
            self.assertIn(teil, a, teil)

    def test_abschnitt_hat_keine_gedankenstriche_im_fliesstext(self):
        abschnitt = abschnitt_stimme()
        self.assertNotIn("–", abschnitt)
        self.assertNotIn("—", abschnitt)
        self.assertNotIn(" - ", abschnitt)

    def test_hinweis_fuer_die_besucher_im_labor_bleibt_ohne_stimmabdruck(self):
        text = dokument()
        abschnitt_1 = re.search(r"^## 1\. Text für die Besucher[^\n]*\n(.*?)(?=^## )", text, re.S | re.M)
        self.assertTrue(abschnitt_1)
        self.assertNotIn("Stimmabdruck", abschnitt_1.group(1))
        self.assertIn("Einwilligung (Version 1)", abschnitt_1.group(1))

    def test_die_seiten_des_labor_dienstes_erwaehnen_keinen_stimmabdruck(self):
        web = WURZEL / "labor-dienst" / "wamo_labor_dienst" / "web"
        dateien = [p for p in web.rglob("*") if p.is_file() and p.suffix in (".html", ".js", ".css")]
        self.assertTrue(dateien)
        for datei in dateien:
            self.assertNotIn("stimmabdruck", datei.read_text(encoding="utf-8", errors="ignore").lower(), datei.name)


if __name__ == "__main__":
    unittest.main()
