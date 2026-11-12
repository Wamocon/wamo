"""Tests der Inhalte der Labor-Daten (Testlabor 3.6 und 3.8): infra/sokrates2/labor-daten/ wird durch den echten Lader (labor_daten.py) gelesen.

Geprüft wird, was der Lader nicht prüfen kann: dass die Standardfassung der Regeln den Satz "frag bei fehlenden Angaben nach" nicht enthält (die Basis
wird zuerst gemessen) und die strenge Fassung ihn trägt, dass jede der beiden Fassungen mit allen Daten unter die Grenze des Laders passt (die
spätere Datei fiele sonst ganz weg), dass die Kontakte nur Adressen der Testdomäne nennen, dass der Schlusstext nichts erfindet (kein Termin, kein
Preis, keine Adresse), dass die JSON-Datei alle 17 Karten genau einmal enthält und dass die Texte weder Gedankenstriche noch unzulässige Zeichen haben.

Ausführen:  python -m pytest -p no:cacheprovider -q test_labor_daten_inhalt.py   (im Ordner voice-core/)
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import pytest

import labor_daten as ld
import labor_mail
from labor_prompt import DATEN_HINWEIS, REGELN_KOPF, build_labor_prompt

sys.path.insert(0, str(Path(__file__).resolve().parent / "eval"))
import faelle_labor as fl  # noqa: E402

QUELLE = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "labor-daten"
EGAL = ld.Richtlinie(besitzer=None, verboten_modus=0)  # Besitzer und Modus der Testmaschine zählen nicht (auf dem Server gilt die Standard-Richtlinie)
STRENG = "persona-zusatz.streng.md"
LIEFERUNG = {*ld.DATEIEN_ALLE, STRENG, "README.md"}
RESERVE = 150  # so viele Zeichen unter der Gesamtgrenze bleiben frei, damit der Trainer etwas ergänzen kann
SATZ_NACHFRAGEN = "frag bei fehlenden angaben nach"
TESTDOMAENE = "labor.test"  # die Standard-Domäne des Mail-Filters (labor_mail.DOMAIN_STANDARD), der Kern bleibt unverändert
ADRESSE = re.compile(r"[^\s,;<>()\[\]\"']+@[^\s,;<>()\[\]\"']+")


def lies(name: str) -> str:
    return (QUELLE / name).read_text(encoding="utf-8")


def normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def installiert(ziel: Path, persona: str) -> Path:
    """Legt die Dateien so ab, wie das Einrichtungsskript sie auf dem Server ablegt (persona: standard oder streng), und gibt den Ordner zurück."""
    ordner = ziel / "labor"
    ordner.mkdir()
    for name in ld.DATEIEN_DATEN:
        shutil.copyfile(QUELLE / name, ordner / name)
    shutil.copyfile(QUELLE / (STRENG if persona == "streng" else ld.DATEI_REGELN), ordner / ld.DATEI_REGELN)
    return ordner


@pytest.fixture(params=("standard", "streng"))
def geladen(request, tmp_path) -> ld.LaborDaten:
    return ld.laden(str(installiert(tmp_path, request.param)), richtlinie=EGAL)


@pytest.fixture
def standard(tmp_path) -> ld.LaborDaten:
    return ld.laden(str(installiert(tmp_path, "standard")), richtlinie=EGAL)


# ---------------------------------------------------------------- Lieferung und Lader


def test_die_lieferung_besteht_genau_aus_den_vorgesehenen_dateien():
    assert {p.name for p in QUELLE.iterdir()} == LIEFERUNG


def test_der_lader_lehnt_nichts_ab_in_beiden_fassungen(geladen):
    assert geladen.abgelehnt == {}
    assert set(geladen.pruefsummen) == set(ld.DATEIEN_ALLE)
    assert geladen.regeln and [name for name, _ in geladen.daten] == list(ld.DATEIEN_DATEN)


def test_gesamtlaenge_und_regelteil_bleiben_in_den_grenzen_mit_reserve(geladen):
    gesamt = len(geladen.regeln) + sum(len(text) for _, text in geladen.daten)
    assert len(geladen.regeln) <= ld.MAX_REGELN
    assert gesamt <= ld.MAX_GESAMT - RESERVE, f"{gesamt} Zeichen, erlaubt sind {ld.MAX_GESAMT - RESERVE} (Reserve {RESERVE} für den Trainer)"


def test_ohne_reserve_ginge_die_letzte_datei_verloren():
    """Warum es die Reserve gibt: Der Lader nimmt die Dateien der Reihe nach, die letzte (ctfl-zuordnung.json) fiele bei Überschreitung ganz weg."""
    assert ld.DATEIEN_DATEN[-1] == "ctfl-zuordnung.json"


# ---------------------------------------------------------------- Zeichen und Stil


def _textdateien() -> list[Path]:
    return sorted(QUELLE.iterdir()) if QUELLE.is_dir() else []


@pytest.mark.parametrize("pfad", _textdateien(), ids=lambda p: p.name)
def test_nur_zugelassene_zeichen_kein_cr_kein_bom(pfad):
    roh = pfad.read_bytes()
    assert b"\r" not in roh, "Zeilenende mit CR (nur LF)"
    assert not roh.startswith(b"\xef\xbb\xbf"), "BOM"
    for zeichen in roh.decode("utf-8"):
        erlaubt = zeichen in "\n\t " or unicodedata.category(zeichen)[0] in "LMNPS"
        assert erlaubt, f"{pfad.name}: unzulässiges Zeichen U+{ord(zeichen):04X}"


@pytest.mark.parametrize("pfad", _textdateien(), ids=lambda p: p.name)
def test_keine_gedankenstriche(pfad):
    text = pfad.read_text(encoding="utf-8")
    for code in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212):
        assert chr(code) not in text, f"{pfad.name}: Strich U+{code:04X}"
    assert not re.search(r"\S - \S", text), f"{pfad.name}: Bindestrich mit Leerzeichen als Einschub"


def test_echte_umlaute_keine_ersatzschreibung():
    verdaechtig = ("fuer ", "ueber", "koenn", "moecht", "muess", "pruef", "aender", "loesch", "waehr", "naechst", "gueltig", "zulaessig", "qualitaet")
    for pfad in _textdateien():
        text = pfad.read_text(encoding="utf-8").lower()
        for wort in verdaechtig:
            assert wort not in text, f"{pfad.name}: {wort!r}"
    alles = "".join(pfad.read_text(encoding="utf-8") for pfad in _textdateien())
    assert all(zeichen in alles for zeichen in "äöüÄ"), "die Texte sind deutsch und haben echte Umlaute"


def test_keine_datenzeile_taeuscht_eine_grenze_vor():
    """labor_prompt ersetzt Zeilen, die mit --- beginnen, und die Grenzmarke einer Datei darf nie als Text vorkommen."""
    for name in ld.DATEIEN_DATEN:
        text = lies(name)
        assert not re.search(r"(?m)^\s*-{3,}", text), name
        assert "Ende der Daten" not in text and "Regeln für dieses Labor" not in text, name


def test_regeln_haben_keine_platzhalterklammern():
    for name in (ld.DATEI_REGELN, STRENG):
        assert "{" not in lies(name) and "}" not in lies(name), name


# ---------------------------------------------------------------- Regeln (persona-zusatz)


def test_die_standardfassung_hat_den_nachfragesatz_nicht_die_strenge_hat_ihn():
    assert SATZ_NACHFRAGEN not in normal(lies(ld.DATEI_REGELN))
    assert SATZ_NACHFRAGEN in normal(lies(STRENG))


def test_beide_fassungen_nennen_entwurf_werkzeuge_und_unbekannt():
    for name in (ld.DATEI_REGELN, STRENG):
        text = normal(lies(name))
        for muss in ("entwurf", "werkzeug", "unbekannt", "aktuelle_zeit", "erfinde nichts", "klick"):
            assert muss in text, f"{name}: {muss}"


def test_die_regeln_verweisen_auf_die_vorlagen_und_den_schlusstext():
    text = lies(ld.DATEI_REGELN)
    for datei in ("anforderung-vorlage.md", "bericht-vorlage.md", "ausgang.md"):
        assert datei in text, datei


def test_die_strenge_fassung_ist_die_standardfassung_plus_zwei_regeln():
    standard = [z for z in lies(ld.DATEI_REGELN).splitlines() if z.strip()]
    streng = [z for z in lies(STRENG).splitlines() if z.strip()]
    assert streng[: len(standard)] == standard, "die strenge Fassung beginnt mit der Standardfassung, unverändert"
    zusatz = " ".join(streng[len(standard):])
    assert len(streng) == len(standard) + 2
    assert SATZ_NACHFRAGEN in normal(zusatz)
    assert "wegbessern" in zusatz.lower() and "Sicherheit" in zusatz  # Q6: Fehler nicht wegbessern, nur bei Sicherheit eingreifen


# ---------------------------------------------------------------- Kontakte


def test_kontakte_nur_adressen_der_testdomaene():
    text = lies("kontakte.md")
    adressen = ADRESSE.findall(text)
    assert len(adressen) >= 3
    assert len(set(adressen)) == len(adressen), "eine Adresse steht doppelt"
    for adresse in adressen:
        assert re.fullmatch(rf"[a-z0-9]+(?:[._+-][a-z0-9]+)*@{re.escape(TESTDOMAENE)}", adresse), adresse
    assert "Anna Test" in text
    assert not re.search(r"https?://|www\.", text)
    assert not re.search(r"\d", text), "keine Ziffern: keine Telefonnummer, keine Nummer, die echt wirken könnte"
    assert re.findall(r"[\w.-]+\.(?:com|de|org|net|eu|info)\b", text) == []


def test_die_domaene_der_kontakte_ist_die_standard_domaene_des_mail_filters():
    """Der Kern lässt mail_senden nur an die erlaubte Domäne zu (Standard labor_mail.DOMAIN_STANDARD): jede Adresse der Kontakte muss durch den echten Filter."""
    assert TESTDOMAENE == labor_mail.DOMAIN_STANDARD
    adressen = ADRESSE.findall(lies("kontakte.md"))
    assert adressen
    for adresse in adressen:
        assert labor_mail.adresse_erlaubt(adresse, labor_mail.DOMAIN_STANDARD), adresse
    assert {a.partition("@")[2] for a in adressen} == {labor_mail.DOMAIN_STANDARD}


def test_jeder_kontakt_hat_einen_namen_und_die_adresse_passt_zum_namen():
    zeilen = [z for z in lies("kontakte.md").splitlines() if z.startswith("- ")]
    assert len(zeilen) >= 3
    for zeile in zeilen:
        name, _funktion, adresse = (teil.strip() for teil in zeile[2:].split(","))
        vorname, nachname = name.lower().split(" ")
        assert adresse == f"{vorname}.{nachname}@{TESTDOMAENE}", zeile


# ---------------------------------------------------------------- Vorlagen und Wissen


def test_die_anforderungsvorlage_besteht_den_eigenen_pruefer_der_karte_a1():
    """Dieselbe Prüfung wie die Messung der Karte A1: 3 bis 7 Stories, je mindestens 2 Kriterien."""
    assert fl.karte_pruefen("a1", "", [("canvas_zeigen", {"typ": "dokument", "text": lies("anforderung-vorlage.md")})]) == []


def test_die_anforderungsvorlage_nennt_das_muster():
    text = lies("anforderung-vorlage.md")
    assert len(re.findall(r"(?m)^Als <Rolle> möchte ich <Funktion>, damit <Nutzen>\.$", text)) >= 3
    kriterien = re.findall(r"(?m)^- Gegeben .+, wenn .+, dann .+\.$", text)
    assert len(kriterien) >= 6
    assert "3 bis 7" in text


def test_das_glossar_traegt_den_vermerk_und_alle_begriffe_mit_ein_bis_zwei_saetzen():
    text = lies("wissen-istqb.md")
    assert "Entwurf, Fachlichkeit prüft der Trainer" in text.splitlines()[0]
    eintraege = {}
    for zeile in text.splitlines()[1:]:
        assert zeile.startswith("- ") and ": " in zeile, zeile
        begriff, erklaerung = zeile[2:].split(": ", 1)
        eintraege[begriff] = erklaerung
    for begriff in ("Verifikation", "Validierung", "Äquivalenzklasse", "Grenzwert", "Testorakel", "Regressionstest", "Fehlerwirkung", "Fehlerzustand"):
        assert begriff in eintraege, begriff
    for begriff, erklaerung in eintraege.items():
        saetze = [s for s in re.split(r"(?<=[.!?])\s+", erklaerung.strip()) if s]
        assert 1 <= len(saetze) <= 2, f"{begriff}: {len(saetze)} Sätze"
        assert len(erklaerung) <= 220, f"{begriff}: zu lang"


def test_die_berichtsvorlage_hat_die_spalten_des_plans_und_verweist_auf_die_daten():
    text = lies("bericht-vorlage.md")
    kopf = next(z for z in text.splitlines() if z.startswith("| Karte"))
    assert [s.strip() for s in kopf.strip("|").split("|")] == ["Karte", "Eingabe", "erwartet", "tatsächlich", "bestanden", "Ticket", "CTFL"]
    for muss in ("ctfl-zuordnung.json", "ausgang.md", "nicht angegeben", "aktuelle_zeit"):
        assert muss in text, muss


# ---------------------------------------------------------------- Schlusstext (nichts erfinden)


def test_ausgang_hoechstens_400_zeichen_mit_dem_seminarhinweis():
    text = lies("ausgang.md").strip()
    assert 0 < len(text) <= 400
    assert "Mehr zum Seminar erhalten Sie am Stand von WAMOCON" in text


def test_ausgang_erfindet_weder_termin_noch_preis_noch_anmeldung():
    text = lies("ausgang.md")
    assert not re.search(r"\d", text), "keine Ziffer: kein Preis, kein Datum, keine Uhrzeit"
    assert not re.search(r"€|\beuro\b|\beur\b|\bpreis|\bkosten|\bgebühr", text, re.I)
    assert not re.search(r"https?://|www\.|@", text)
    monate = "januar|februar|märz|april|mai|juni|juli|august|september|oktober|november|dezember"
    wochentage = "montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag"
    assert not re.search(rf"\b({monate}|{wochentage})\b", text, re.I)


# ---------------------------------------------------------------- CTFL-Zuordnung


def _json_ohne_doppelte_schluessel(text: str):
    def paare(eintraege):
        schluessel = [k for k, _ in eintraege]
        assert len(schluessel) == len(set(schluessel)), f"doppelter Schlüssel: {sorted(s for s in schluessel if schluessel.count(s) > 1)}"
        return dict(eintraege)

    return json.loads(text, object_pairs_hook=paare)


def test_die_json_datei_ist_gueltig_und_hat_den_vermerk():
    daten = _json_ohne_doppelte_schluessel(lies("ctfl-zuordnung.json"))
    assert set(daten) == {"hinweis", "karten"}
    assert "Entwurf" in daten["hinweis"] and "Trainer" in daten["hinweis"]


def test_die_json_datei_hat_alle_17_karten_genau_einmal():
    karten = _json_ohne_doppelte_schluessel(lies("ctfl-zuordnung.json"))["karten"]
    erwartet = [karte.id.upper() for karte in fl.KARTEN]
    assert len(erwartet) == 17
    assert sorted(karten) == sorted(erwartet)
    assert len({k.upper() for k in karten}) == len(karten), "eine Karte steht in zwei Schreibweisen da"


def test_jede_karte_hat_thema_und_lernziel_als_kurzen_text():
    for kennung, eintrag in _json_ohne_doppelte_schluessel(lies("ctfl-zuordnung.json"))["karten"].items():
        assert set(eintrag) == {"thema", "lernziel"}, kennung
        for feld, wert in eintrag.items():
            assert isinstance(wert, str) and 3 <= len(wert.strip()) <= 60, f"{kennung}.{feld}"
            assert wert == wert.strip(), f"{kennung}.{feld}"


# ---------------------------------------------------------------- Zusammenspiel mit dem Prompt


def test_der_prompt_trennt_regeln_und_daten(standard):
    prompt = build_labor_prompt("Gast", "du", "platz", standard.regeln, standard.daten)
    i_regeln, i_daten = prompt.index(REGELN_KOPF.strip()), prompt.index(DATEN_HINWEIS.strip()[:40])
    assert i_regeln < i_daten
    regelblock, datenblock = prompt[i_regeln:i_daten], prompt[i_daten:]
    assert "Entwurf" in regelblock and "aktuelle_zeit" in regelblock
    for datum in ("@labor.test", "Verifikation", '"karten"', "Gegeben <Ausgangslage>"):
        assert datum not in regelblock, datum  # keine Datenzeile im Regelblock
    for name in ld.DATEIEN_DATEN:
        assert f"--- Daten: {name} ---" in datenblock and f"--- Ende der Daten: {name} ---" in datenblock, name
    assert "@labor.test" in datenblock and "Verifikation" in datenblock
    assert "Erfinde nichts" not in datenblock  # keine Regel im Datenteil


def test_die_kontaktliste_kommt_nur_aus_der_datei_nicht_aus_dem_laborprompt():
    assert "@labor.test" not in build_labor_prompt("Gast", "du")
