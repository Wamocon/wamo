"""Tests für labor_vertrag.py: Wertebereiche, Textnormierung und Längenmaß des Vertrags Kern zu Dienst (Version 1).

Ohne Netz und ohne Datenbank. Der Vertrag steht in docs/labor-dienst-vertrag.md.
"""

from __future__ import annotations

import pytest

import labor_vertrag as v


def test_wertebereiche_wie_im_vertrag():
    assert v.PROJEKT == "LAB"
    assert v.TYPEN == ("bug", "aufgabe", "story")
    assert v.PRIORITAETEN == ("niedrig", "mittel", "hoch", "hoechste")
    assert v.STATUS == ("zu_erledigen", "in_arbeit", "erledigt")
    assert (v.TITEL_MIN, v.TITEL_MAX, v.BESCHREIBUNG_MAX, v.KOMMENTAR_MIN, v.KOMMENTAR_MAX) == (3, 120, 1500, 1, 500)
    assert v.MAIL_TEXT_MAX == 1500 and v.MAIL_BETREFF_MAX == 120 and v.KARTE_MAX == 1900


@pytest.mark.parametrize("nummer", ["LAB-1", "LAB-12", "LAB-9999", "LAB-1234"])
def test_gueltige_nummern(nummer):
    assert v.nummer_gueltig(nummer)


@pytest.mark.parametrize("nummer", [
    "LAB-0", "LAB-012", "LAB-10000", "lab-12", "LAB12", "LAB-", "LAB-1 ", " LAB-1", "LAB-1\n", "XYZ-12", "LAB-１２",
    "LAB-12​", "LAB-1.5", "LAB--1", "LAB-+1", "", None, 12, "LAB-zwanzig",
])
def test_ungueltige_nummern(nummer):
    assert not v.nummer_gueltig(nummer)


def test_text_wird_nfc_und_ohne_steuerzeichen():
    assert v.normiere("Café", zeilen=False) == "Café"  # NFC
    assert v.normiere("a\x00b\x07c\x1b[31md", zeilen=False) == "a b c [31md"
    # Bidi-, Nullbreiten-, Format- und Trennzeichen werden zu Leerzeichen
    for zeichen in ("‮", "​", "⁦", "﻿", " ", " ", "­", "⁠"):
        assert zeichen not in v.normiere(f"x{zeichen}y", zeilen=False)
    assert v.normiere("a‮b", zeilen=False) == "a b"
    assert v.normiere("a\U000e0041b", zeilen=False) == "a b"  # Tag-Zeichen (Format)
    assert v.normiere("ab", zeilen=False) == "a b"  # private Nutzung


def test_einzeilig_ohne_umbruch_und_leerraum_zusammengefasst():
    assert v.normiere("  Zeile1\r\nZeile2\tText x  ", zeilen=False) == "Zeile1 Zeile2 Text x"
    assert v.normiere("a\n\n\nb", zeilen=False) == "a b"


def test_mehrzeilig_behaelt_umbrueche_und_normiert_sie():
    assert v.normiere("a\r\nb\rc\nd", zeilen=True) == "a\nb\nc\nd"
    assert v.normiere("a  \n  b  \n", zeilen=True) == "a\n  b"  # Einrückung bleibt, rechts wird gekürzt
    assert v.normiere("\n\nText\n\n", zeilen=True) == "Text"
    assert "\r" not in v.normiere("a\rb", zeilen=True)


def test_nicht_text_wird_abgelehnt():
    for kaputt in (None, 5, 1.5, ["a"], {"a": 1}, b"abc", True):
        assert v.normiere(kaputt, zeilen=False) is None


def test_emoji_und_umlaute_bleiben():
    assert v.normiere("Größe ✅ 😀 Ä", zeilen=False) == "Größe ✅ 😀 Ä"


def test_utf16_laenge_zaehlt_wie_die_oberflaeche():
    assert v.utf16_len("abc") == 3
    assert v.utf16_len("😀") == 2  # ein Zeichen außerhalb der Grundebene sind zwei Einheiten (JavaScript length)
    assert v.utf16_len("ä😀") == 3
    assert v.utf16_len("") == 0


def test_darstellung_der_werte_ist_eindeutig():
    assert len(set(v.PRIORITAET_ANZEIGE.values())) == len(v.PRIORITAETEN)
    assert len(set(v.STATUS_ANZEIGE.values())) == len(v.STATUS)
    assert len(set(v.TYP_ANZEIGE.values())) == len(v.TYPEN)
    assert set(v.PRIORITAET_ANZEIGE) == set(v.PRIORITAETEN)
    assert set(v.STATUS_ANZEIGE) == set(v.STATUS)
    assert set(v.TYP_ANZEIGE) == set(v.TYPEN)


def test_jeder_fehlercode_des_vertrags_hat_einen_festen_satz():
    codes = {"token_ungueltig", "platz_unbekannt", "ticket_unbekannt", "durchlauf_beendet", "ticket_veraendert",
             "eingabe_ungueltig", "zu_gross", "quote_erreicht", "nicht_erlaubt", "dienst_fehler"}
    assert codes <= set(v.FEHLER_SAETZE)
    assert {"nicht_erreichbar", "zeitueberschreitung", "antwort_ungueltig"} <= set(v.FEHLER_SAETZE)
    for satz in v.FEHLER_SAETZE.values():
        assert satz.endswith(".") and "[" not in satz and "«" not in satz and len(satz) < 160
