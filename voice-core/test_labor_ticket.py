"""Tests der Prüfungen der ticket_*-Werkzeuge im Code (labor_ticket.py): Projekt LAB, Aufzählungen, Längen, Zeichen, Nummernformat, nichts Löschendes.

Rein, ohne Netz. Die Handler mit Freigabe und Dienst stehen in test_labor_werkzeuge.py.
"""

from __future__ import annotations

import pytest

import labor_ticket as t
from labor_vertrag import PRIORITAETEN, STATUS, TYPEN


def ok(ergebnis):
    werte, fehler = ergebnis
    assert fehler is None, fehler
    return werte


def fehler(ergebnis):
    werte, text = ergebnis
    assert werte is None and text, ergebnis
    return text


GUT = {"typ": "bug", "prioritaet": "hoch", "titel": "Anmeldung schlägt fehl", "beschreibung": "Nach dem Klick passiert nichts."}


# ---------- anlegen ----------
def test_anlegen_gueltig():
    assert ok(t.pruefe_anlegen(GUT)) == GUT
    assert ok(t.pruefe_anlegen({k: v for k, v in GUT.items() if k != "beschreibung"}))["beschreibung"] == ""  # Beschreibung ist optional
    assert ok(t.pruefe_anlegen({**GUT, "beschreibung": None}))["beschreibung"] == ""
    assert ok(t.pruefe_anlegen({**GUT, "projekt": "LAB"})) == GUT  # das Projekt darf genannt werden, wenn es LAB ist
    assert ok(t.pruefe_anlegen({**GUT, "titel": "x" * 120, "beschreibung": "y" * 1500}))


@pytest.mark.parametrize("typ", TYPEN)
@pytest.mark.parametrize("prio", PRIORITAETEN)
def test_alle_aufzaehlungen_gehen_durch(typ, prio):
    assert ok(t.pruefe_anlegen({**GUT, "typ": typ, "prioritaet": prio}))["typ"] == typ


@pytest.mark.parametrize("wert", ["fehler", "Bug", "BUG", "bug ", "task", "", None, 1, True, ["bug"], "bug\n"])
def test_ungueltiger_typ_nennt_die_erlaubten_werte(wert):
    text = fehler(t.pruefe_anlegen({**GUT, "typ": wert}))
    assert all(w in text for w in TYPEN)


@pytest.mark.parametrize("wert", ["superdringend", "dringend", "Hoch", "höchste", "p1", "", None, 3, "hoch,mittel"])
def test_ungueltige_prioritaet_nennt_die_erlaubten_werte(wert):
    text = fehler(t.pruefe_anlegen({**GUT, "prioritaet": wert}))
    assert all(w in text for w in PRIORITAETEN)  # J3: der Code lehnt ab und nennt die erlaubten Werte


@pytest.mark.parametrize("titel", ["ab", "", "  ", "x" * 121, None, 5, ["a"], "a\u202e", "\n\n\n"])
def test_titel_laenge_3_bis_120(titel):
    fehler(t.pruefe_anlegen({**GUT, "titel": titel}))


def test_titellaenge_wird_nach_der_normierung_gezaehlt():
    assert ok(t.pruefe_anlegen({**GUT, "titel": "  abc  "}))["titel"] == "abc"
    fehler(t.pruefe_anlegen({**GUT, "titel": "  ab  "}))  # nach der Normierung zu kurz
    assert ok(t.pruefe_anlegen({**GUT, "titel": "a\u200bb"}))["titel"] == "a b"
    fehler(t.pruefe_anlegen({**GUT, "titel": "x" * 100 + " " * 50 + "y" * 21}))  # nach dem Zusammenfassen des Leerraums 122 Zeichen


def test_titel_steuerzeichen_werden_ersetzt_nicht_durchgelassen():
    werte = ok(t.pruefe_anlegen({**GUT, "titel": "Sehr\nwichtig\u202etxt\x07"}))
    assert werte["titel"] == "Sehr wichtig txt"
    assert "\n" not in werte["titel"] and "\u202e" not in werte["titel"] and "\x07" not in werte["titel"]


@pytest.mark.parametrize("beschreibung", ["y" * 1501, 5, ["a"], {"a": 1}, True])
def test_beschreibung_hoechstens_1500(beschreibung):
    fehler(t.pruefe_anlegen({**GUT, "beschreibung": beschreibung}))


def test_beschreibung_behaelt_zeilen_und_wird_normiert():
    werte = ok(t.pruefe_anlegen({**GUT, "beschreibung": "Schritt 1\r\nSchritt 2\u202e\n\n  Schritt 3  "}))
    assert werte["beschreibung"] == "Schritt 1\nSchritt 2\n\n  Schritt 3"


def test_projekt_ist_immer_lab():
    for projekt in ("ABC", "lab", "LAB ", "Test-Jira", 5, ["LAB"], ""):
        text = fehler(t.pruefe_anlegen({**GUT, "projekt": projekt}))
        assert "LAB" in text
    assert ok(t.pruefe_anlegen({**GUT, "projekt": None}))  # null heißt nicht angegeben


def test_fehlende_pflichtfelder():
    for feld in ("typ", "prioritaet", "titel"):
        fehler(t.pruefe_anlegen({k: v for k, v in GUT.items() if k != feld}))
    fehler(t.pruefe_anlegen({}))
    for kaputt in (None, "text", 5, [], ()):
        fehler(t.pruefe_anlegen(kaputt))


def test_unbekannte_felder_erreichen_den_dienst_nie():
    werte = ok(t.pruefe_anlegen({**GUT, "geloescht": True, "status": "erledigt", "nummer": "LAB-1", "loeschen": "alle", "version": 9}))
    assert set(werte) == {"typ", "prioritaet", "titel", "beschreibung"}


# ---------- ändern, verschieben, kommentieren ----------
def test_aendern_gueltig_und_nur_gesetzte_felder():
    assert ok(t.pruefe_aendern({"nummer": "LAB-12", "titel": "Neuer Titel"})) == {"nummer": "LAB-12", "titel": "Neuer Titel"}
    werte = ok(t.pruefe_aendern({"nummer": "LAB-12", "titel": "Neuer Titel", "beschreibung": "", "typ": "story", "prioritaet": "niedrig"}))
    assert werte == {"nummer": "LAB-12", "titel": "Neuer Titel", "beschreibung": "", "typ": "story", "prioritaet": "niedrig"}
    assert ok(t.pruefe_aendern({"nummer": "LAB-12", "titel": None, "prioritaet": "hoch"})) == {"nummer": "LAB-12", "prioritaet": "hoch"}


def test_aendern_ohne_aenderung_ist_ein_fehler():
    for args in ({"nummer": "LAB-12"}, {"nummer": "LAB-12", "titel": None}, {"nummer": "LAB-12", "status": "erledigt"}):
        fehler(t.pruefe_aendern(args))


def test_aendern_prueft_jedes_feld_wie_anlegen():
    fehler(t.pruefe_aendern({"nummer": "LAB-12", "titel": "ab"}))
    fehler(t.pruefe_aendern({"nummer": "LAB-12", "typ": "x"}))
    fehler(t.pruefe_aendern({"nummer": "LAB-12", "prioritaet": "superdringend"}))
    fehler(t.pruefe_aendern({"nummer": "LAB-12", "beschreibung": "y" * 1501}))


@pytest.mark.parametrize("nummer", ["LAB-0", "LAB-012", "LAB-10000", "lab-12", "LAB12", "LAB-zwanzig", "ABC-12", "LAB-12 ", " LAB-12", "12", "LAB-1;DROP",
                                    "LAB-１２", "", None, 12, ["LAB-12"], "LAB-12\n", "LAB-12/../x", "LAB-12\u200b"])
def test_nummer_streng(nummer):
    for pruefung, rest in ((t.pruefe_aendern, {"titel": "Neuer Titel"}), (t.pruefe_verschieben, {"status": "erledigt"}),
                           (t.pruefe_kommentar, {"text": "Ein Kommentar"})):
        text = fehler(pruefung({"nummer": nummer, **rest}))
        assert "LAB-12" in text  # die Meldung zeigt die erwartete Form


def test_nummer_gueltig_und_projekt():
    assert ok(t.pruefe_verschieben({"nummer": "LAB-1234", "status": "erledigt"})) == {"nummer": "LAB-1234", "status": "erledigt"}
    for projekt in ("ABC", "lab"):
        fehler(t.pruefe_verschieben({"nummer": "LAB-12", "status": "erledigt", "projekt": projekt}))


@pytest.mark.parametrize("status", STATUS)
def test_verschieben_alle_status(status):
    assert ok(t.pruefe_verschieben({"nummer": "LAB-12", "status": status}))["status"] == status


@pytest.mark.parametrize("status", ["geloescht", "offen", "Erledigt", "done", "", None, 1, "erledigt ", "loeschen", "archiviert"])
def test_verschieben_ungueltiger_status_nennt_die_erlaubten_werte(status):
    text = fehler(t.pruefe_verschieben({"nummer": "LAB-12", "status": status}))
    assert all(w in text for w in STATUS)


def test_kommentar_1_bis_500():
    assert ok(t.pruefe_kommentar({"nummer": "LAB-12", "text": "x" * 500}))["text"] == "x" * 500
    for text in ("", "   ", "x" * 501, None, 5, ["a"]):
        fehler(t.pruefe_kommentar({"nummer": "LAB-12", "text": text}))
    assert ok(t.pruefe_kommentar({"nummer": "LAB-12", "text": "Zeile\r\nzwei\u202e"}))["text"] == "Zeile\nzwei"


def test_enge_normierung_gegen_riesige_eingaben():
    # eine riesige Eingabe wird vor der Normierung abgewiesen, nicht erst gezählt
    fehler(t.pruefe_anlegen({**GUT, "beschreibung": "x" * 5_000_000}))
    fehler(t.pruefe_kommentar({"nummer": "LAB-12", "text": "x" * 5_000_000}))


# ---------- suchen ----------
def test_suche_gueltig():
    assert ok(t.pruefe_suche({})) == {"limit": 10}
    assert ok(t.pruefe_suche({"anfrage": "Login", "limit": 3})) == {"q": "Login", "limit": 3}
    assert ok(t.pruefe_suche({"nummer": "LAB-12"})) == {"nummer": "LAB-12", "limit": 10}
    assert ok(t.pruefe_suche({"anfrage": "LAB-12"})) == {"nummer": "LAB-12", "limit": 10}  # eine Nummer im Suchtext ist eine Nummer
    assert ok(t.pruefe_suche({"typ": "bug", "prioritaet": "hoch", "status": "in_arbeit", "nur_durchlauf": True})) == {
        "typ": "bug", "prioritaet": "hoch", "status": "in_arbeit", "nur_durchlauf": True, "limit": 10}
    assert ok(t.pruefe_suche({"anfrage": "100%", "typ": None}))["q"] == "100%"  # % wird wörtlich gesucht, der Dienst maskiert


@pytest.mark.parametrize("args", [{"anfrage": "x" * 101}, {"nummer": "LAB-0"}, {"typ": "x"}, {"prioritaet": "x"}, {"status": "x"}, {"limit": 0}, {"limit": 11},
                                  {"limit": "5"}, {"limit": True}, {"nur_durchlauf": "ja"}, {"anfrage": 5}, {"anfrage": ["a"]}])
def test_suche_ungueltig(args):
    fehler(t.pruefe_suche(args))


# ---------- Schemas ----------
def test_schemas_ohne_loeschen_und_mit_aufzaehlungen():
    schemas = {s.name: s for s in t.ticket_schemas()}
    assert set(schemas) == {"ticket_suchen", "ticket_anlegen", "ticket_aendern", "ticket_verschieben", "ticket_kommentieren"}
    for name, s in schemas.items():
        assert not any(w in name for w in ("loesch", "delete", "entfern", "remove"))
        assert not any(w in s.description.lower() for w in ("lösche", "löschen", "delete"))
    anlegen = schemas["ticket_anlegen"].properties
    assert anlegen["typ"]["enum"] == list(TYPEN) and anlegen["prioritaet"]["enum"] == list(PRIORITAETEN)
    assert schemas["ticket_verschieben"].properties["status"]["enum"] == list(STATUS)
    assert set(schemas["ticket_anlegen"].required) == {"typ", "prioritaet", "titel"}
    assert set(schemas["ticket_aendern"].properties) == {"nummer", "titel", "beschreibung", "typ", "prioritaet"}
    # kein Schema bietet Felder, die der Vertrag nicht kennt (Löschen, Version, Durchlauf, Projekt wechseln)
    for s in schemas.values():
        assert not {"version", "erwartete_version", "durchlauf_id", "geloescht", "projekt", "platz", "token"} & set(s.properties)
    for s in schemas.values():
        for text in [s.description, *(p.get("description", "") for p in s.properties.values())]:
            assert "—" not in text and "–" not in text  # keine Gedankenstriche im Text für Menschen und Modell


def test_schreibende_schemas_sagen_dass_nur_die_karte_erscheint():
    for name in ("ticket_anlegen", "ticket_aendern", "ticket_verschieben", "ticket_kommentieren"):
        s = {x.name: x for x in t.ticket_schemas()}[name]
        assert "Freigabe" in s.description and "nie" in s.description.lower()
