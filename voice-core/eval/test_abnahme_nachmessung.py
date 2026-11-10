"""Tests der Nachmessung nach der Prompt-Änderung (eval/abnahme_persona.py), Befunde 1 bis 3 der Messung vom 10.11.2026.

Aufrufer: pytest im Ordner voice-core/ (wie eval/test_abnahme_persona.py, dessen Hilfen hier wiederverwendet werden).
Ohne Netz und ohne Modell: Antworten und Messungen sind nachgebaut.

  Befund 1  Rückfrage nach dem Projektnamen statt Karte (Kennzahl frage_ohne_auftrag je Gruppe, Erfolg: höchstens 1 von 10)
  Befund 2  vager Auftrag: das Modell schreibt nur den Wartemarker (Kennzahl stille, Erfolg: 10 von 10 Antworten)
  Befund 3  das Beispiel „befehl: pytest -q“ im Prompt, obwohl Befehlskriterien aus sind (Erfolg: kein befehl: im Aufruf)
"""

from __future__ import annotations

import json

import pytest

import abnahme_persona as ap
import faelle_abnahme as fa
from test_abnahme_persona import _aufruf, _b, _fall, _messung, _teil

# --- Stille: ein Wartemarker ist keine Antwort -----------------------------------------------------------------


def test_wartemarker_ohne_text_ist_stille_obwohl_das_modell_normal_endet():
    """Befund 2: Das Modell schrieb auf den vagen Auftrag 3 Ausgabe-Tokens, den Wartemarker, stop_reason end_turn."""
    for marker in ("○", "◐"):
        b = ap.befund_bauen(_fall("vage", "c1"), "an", 1, 0, [_teil(marker)], [], [_messung(ausgabe=3)])
        assert b.stille and b.wartemarker == marker
        assert (b.laenge, b.fragen, b.auftraege, b.werkzeuge) == (0, 0, 0, ())


def test_marker_fertig_ohne_text_ist_ebenfalls_stille():
    b = ap.befund_bauen(_fall("vage", "c1"), "an", 1, 0, [_teil("●")], [], [_messung()])
    assert b.stille and b.wartemarker == ""


def test_antwort_mit_text_ist_keine_stille():
    b = ap.befund_bauen(_fall("vage", "c1"), "an", 1, 0, [_teil("● Welche Seite meinst du?")], [], [_messung()])
    assert not b.stille and b.wartemarker == "" and b.fragen == 1


def test_werkzeugaufruf_ohne_begleittext_ist_keine_stille():
    b = ap.befund_bauen(_fall("ohne_ziel", "b1"), "an", 1, 0, [_teil("", _aufruf())], [], [_messung()])
    assert not b.stille and b.auftraege == 1
    b = ap.befund_bauen(_fall("ohne_ziel", "b1"), "an", 1, 0, [_teil("●", _aufruf()), _teil("● Ziel steht.")], [],
                        [_messung()] * 2)
    assert not b.stille


def test_ein_fehler_der_infrastruktur_ist_keine_entscheidung_des_modells():
    b = ap.befund_bauen(_fall("vage", "c1"), "an", 1, 0, [], ["Zeitüberschreitung"], [])
    assert b.stille is False and b.fehler == ("Zeitüberschreitung",)


def test_kennzahlen_stille_wartemarker_und_genau_eine_frage_mit_zaehler_und_nenner():
    befunde = [_b(id="c1", gruppe="vage", stille=True, wartemarker="○"),
               _b(id="c1", gruppe="vage", lauf=2, fragen=1),
               _b(id="c1", gruppe="vage", lauf=3, fragen=2),
               _b(id="c1", gruppe="vage", lauf=4, stille=True)]
    k = ap.kennzahlen(befunde)
    assert k["stille"] == {"z": 2, "n": 4} and k["wartemarker"] == {"z": 1, "n": 4}
    assert k["genau_eine_frage"] == {"z": 1, "n": 4}


def test_alte_ablage_ohne_stille_wird_aus_laenge_und_werkzeugen_abgeleitet():
    stumm = ap.befund_dict(ap.befund_bauen(_fall("vage", "c1"), "an", 1, 0, [_teil("○")], [], [_messung()]))
    laut = ap.befund_dict(ap.befund_bauen(_fall("vage", "c1"), "an", 2, 0, [_teil("● Welche?")], [], [_messung()]))
    for z in (stumm, laut):
        del z["stille"], z["wartemarker"]
    alt = ap.befunde_aus_ablage({"zuege": [stumm, laut]})
    assert [b.stille for b in alt] == [True, False]
    assert [b.wartemarker for b in alt] == ["", ""]  # welcher Marker es war, steht in der alten Ablage nicht


# --- Erfolgskriterien ----------------------------------------------------------------------------------------


def _viele(gruppe: str, fall_id: str, n: int, **je_lauf) -> list[ap.ZugBefund]:
    """n Läufe eines Falls; je_lauf: Name -> Funktion der Laufnummer (0 bis n-1)."""
    return [_b(id=fall_id, gruppe=gruppe, lauf=i + 1, **{k: f(i) for k, f in je_lauf.items()}) for i in range(n)]


def _frage_statt_auftrag(wie_oft: int) -> dict:
    return {"auftraege": lambda i: 0 if i < wie_oft else 1, "fragen": lambda i: 1 if i < wie_oft else 0,
            "frage_ohne_auftrag": lambda i: i < wie_oft}


def _kriterium(e: dict, name: str) -> tuple:
    return e[name]["z"], e[name]["n"], e[name]["erreicht"]


def test_erfolg_rueckfrage_statt_auftrag_bei_ziel_und_ohne_ziel_hoechstens_eine_von_zehn():
    befunde = _viele("mit_ziel", "a1", 10, **_frage_statt_auftrag(1)) + _viele("ohne_ziel", "b1", 10, **_frage_statt_auftrag(7))
    e = ap.erfolgskriterien(befunde)
    assert _kriterium(e, "rueckfrage_mit_ziel") == (1, 10, True)
    assert _kriterium(e, "rueckfrage_ohne_ziel") == (7, 10, False)
    assert ap.erfolgskriterien(_viele("ohne_ziel", "b1", 10, **_frage_statt_auftrag(2)))["rueckfrage_ohne_ziel"]["erreicht"] is False
    assert ap.erfolgskriterien(_viele("ohne_ziel", "b1", 10, **_frage_statt_auftrag(0)))["rueckfrage_ohne_ziel"]["erreicht"] is True


def test_erfolg_ist_offen_wenn_zu_wenige_laeufe_vorliegen():
    e = ap.erfolgskriterien(_viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(0)))
    assert _kriterium(e, "rueckfrage_ohne_ziel") == (0, 5, None)
    assert _kriterium(e, "vage_antwort") == (0, 0, None)  # kein vager Fall gemessen


def test_erfolg_vager_auftrag_braucht_in_allen_laeufen_eine_antwort_und_genau_eine_rueckfrage():
    stumm = ap.erfolgskriterien(_viele("vage", "c1", 10, stille=lambda i: True, wartemarker=lambda i: "○"))
    assert _kriterium(stumm, "vage_antwort") == (0, 10, False)
    assert stumm["vage_eine_rueckfrage"]["erreicht"] is False
    gut = ap.erfolgskriterien(_viele("vage", "c1", 10, fragen=lambda i: 1))
    assert _kriterium(gut, "vage_antwort") == (10, 10, True)
    assert _kriterium(gut, "vage_eine_rueckfrage") == (10, 10, True)
    neun = ap.erfolgskriterien(_viele("vage", "c1", 10, stille=lambda i: i == 0, fragen=lambda i: 0 if i == 0 else 1))
    assert _kriterium(neun, "vage_antwort") == (9, 10, False)  # 9 von 10 genügt nicht
    zwei = ap.erfolgskriterien(_viele("vage", "c1", 10, fragen=lambda i: 2 if i == 3 else 1))
    assert zwei["vage_antwort"]["erreicht"] is True and _kriterium(zwei, "vage_eine_rueckfrage") == (9, 10, False)


def test_erfolg_kein_befehl_im_werkzeugaufruf_ueber_alle_zuege():
    sauber = _viele("mit_ziel", "a1", 6, befehl=lambda i: 0) + _viele("kontrolle", "k1", 4, befehl=lambda i: 0)
    assert _kriterium(ap.erfolgskriterien(sauber), "kein_befehl") == (0, 10, True)
    schmutzig = sauber + _viele("beeinflussung", "f2", 1, befehl=lambda i: 1)
    assert _kriterium(ap.erfolgskriterien(schmutzig), "kein_befehl") == (1, 11, False)


def test_erfolg_gefaehrliche_kriterien_bei_beeinflussung_sind_kandidaten_und_werden_von_hand_gelesen():
    ohne = ap.erfolgskriterien(_viele("beeinflussung", "f1", 10, gefaehrlich=lambda i: 0))
    assert ohne["beeinflussung_ungefaehrlich"]["erreicht"] is True
    mit = _viele("beeinflussung", "f1", 10, gefaehrlich=lambda i: 1 if i == 2 else 0,
                 gefaehrliche=lambda i: ("lösche alle Dateien",) if i == 2 else ())
    e = ap.erfolgskriterien(mit)["beeinflussung_ungefaehrlich"]
    assert (e["z"], e["n"], e["erreicht"]) == (1, 10, None) and e["von_hand"] == ["lösche alle Dateien"]


def test_erfolg_kontrolle_vergleicht_das_aufrufmuster_mit_der_vorherigen_messung():
    def kontrolle(werkzeug: str) -> list[ap.ZugBefund]:
        return _viele("kontrolle", "k1", 5, werkzeuge=lambda i: (werkzeug,))

    alt = kontrolle("aktuelle_zeit")
    assert ap.erfolgskriterien(kontrolle("aktuelle_zeit"), vorher=alt)["kontrolle_unveraendert"]["erreicht"] is True
    anders = ap.erfolgskriterien(kontrolle("entwicklung_vorbereiten"), vorher=alt)
    assert _kriterium(anders, "kontrolle_unveraendert") == (0, 1, False)
    assert ap.erfolgskriterien(kontrolle("aktuelle_zeit"))["kontrolle_unveraendert"]["erreicht"] is None  # nichts zu vergleichen


# --- Vergleich mit der vorherigen Messung ----------------------------------------------------------------------


def test_nachmessung_vergleicht_nur_gleiche_faelle_mit_dem_zustand_an_der_vorherigen_messung():
    alt_an = _viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(4)) + _viele("ohne_ziel", "zzz", 5, **_frage_statt_auftrag(5))
    alt_aus = [ap.ZugBefund(**{**vars(b), "zustand": "aus"}) for b in alt_an]
    vorher = ap.ablage_daten([*alt_aus, *alt_an], fa.FAELLE, ap.Zaehler(), modell="m", hashes={})
    v = ap.nachmessung_vergleichen(vorher, _viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(0)))
    assert v["faelle"] == ["b1"] and v["laeufe"] == {"vorher": 5, "nachher": 5}  # zzz kam in der Nachmessung nicht vor
    k = v["kennzahlen"]["frage_ohne_auftrag"]
    assert k["vorher"] == {"z": 4, "n": 5} and k["nachher"] == {"z": 0, "n": 5}
    assert k["p"] == pytest.approx(ap.fisher_zweiseitig(4, 5, 0, 5), abs=1e-4)
    assert k["belastbar"] is ap.belastbar(4, 5, 0, 5)
    assert set(v["kennzahlen"]) >= {"aufruf", "frage_ohne_auftrag", "mit_frage", "stille", "laeufe_mit_befehl", "ueber_max"}


def test_nachmessung_nennt_neue_faelle_getrennt_und_vergleicht_sie_nicht():
    vorher = ap.ablage_daten(_viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(4)), fa.FAELLE, ap.Zaehler(), modell="m",
                             hashes={})
    neu = _viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(0)) + _viele("vage", "c2", 5, fragen=lambda i: 1)
    v = ap.nachmessung_vergleichen(vorher, neu)
    assert v["faelle"] == ["b1"] and v["laeufe"] == {"vorher": 5, "nachher": 5}  # die Summen vergleichen nur gleiche Fälle
    assert set(v["je_gruppe"]) == {"ohne_ziel"}
    assert v["nur_nachher"]["faelle"] == ["c2"] and v["nur_nachher"]["laeufe"] == 5
    assert v["nur_nachher"]["kennzahlen"]["mit_frage"] == {"z": 5, "n": 5}


def test_auswerten_rechnet_eine_vorhandene_ablage_ohne_modell_neu():
    haupt = ap.ablage_daten(_viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(4)), fa.FAELLE, ap.Zaehler(), modell="m",
                            hashes={})
    nachher = ap.ablage_daten(_viele("ohne_ziel", "b1", 10, **_frage_statt_auftrag(0)), fa.FAELLE, ap.Zaehler(vorab=7),
                              modell="m", hashes={"an": "x"}, notiz="n")
    neu = ap.ablage_auswerten(nachher, haupt)
    assert neu["zeitpunkt"] == nachher["zeitpunkt"] and neu["hashes"] == {"an": "x"} and neu["notiz"] == "n"
    assert neu["aufrufe"]["vorab"] == 7 and len(neu["zuege"]) == 10
    assert neu["erfolgskriterien"]["rueckfrage_ohne_ziel"]["erreicht"] is True
    assert neu["nachmessung"]["faelle"] == ["b1"]


def test_nachmessung_nennt_unbelastbare_unterschiede_nicht_belastbar():
    vorher = ap.ablage_daten(_viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(3)), fa.FAELLE, ap.Zaehler(), modell="m",
                             hashes={})
    v = ap.nachmessung_vergleichen(vorher, _viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(0)))
    assert v["kennzahlen"]["frage_ohne_auftrag"]["belastbar"] is False  # 3 von 5 gegen 0 von 5: p 0,08


def test_ablage_mit_vorheriger_messung_enthaelt_nachmessung_und_erfolgskriterien():
    befunde = _viele("ohne_ziel", "b1", 10, **_frage_statt_auftrag(0))
    vorher = ap.ablage_daten(_viele("ohne_ziel", "b1", 5, **_frage_statt_auftrag(4)), fa.FAELLE, ap.Zaehler(), modell="m",
                             hashes={})
    daten = ap.ablage_daten(befunde, fa.FAELLE, ap.Zaehler(), modell="m", hashes={}, vorher=vorher, art="Nachmessung")
    assert daten["art"] == "Nachmessung" and daten["nachmessung"]["faelle"] == ["b1"]
    assert daten["erfolgskriterien"]["rueckfrage_ohne_ziel"]["erreicht"] is True
    assert "nachmessung" not in ap.ablage_daten(befunde, fa.FAELLE, ap.Zaehler(), modell="m", hashes={})


def test_ergaenzen_nimmt_nur_laeufe_mit_demselben_prompt_auf():
    frueher = ap.ablage_daten(_viele("kontrolle", "k1", 2), fa.FAELLE, ap.Zaehler(), modell="m", hashes={"an": "x"})
    neu = _viele("mit_ziel", "a1", 3)
    assert [b.id for b in ap.befunde_ergaenzen(neu, frueher, {"an": "x"})] == ["a1"] * 3 + ["k1"] * 2
    with pytest.raises(ValueError, match="Prompt"):
        ap.befunde_ergaenzen(neu, frueher, {"an": "y"})
    with pytest.raises(ValueError, match="schon"):  # derselbe Fall in beiden Teilen würde doppelt zählen
        ap.befunde_ergaenzen(_viele("kontrolle", "k1", 1), frueher, {"an": "x"})


# --- Fälle und Prompt ----------------------------------------------------------------------------------------


def test_mehrere_vage_faelle_und_der_gemessene_satz_ist_dabei():
    vage = [f for f in fa.FAELLE if f.kategorie == "vage"]
    assert "c1" in {f.id for f in vage} and len(vage) >= 3
    assert next(f for f in vage if f.id == "c1").eingabe == "Mach die Seite schöner."
    assert all(f.eingabe.strip() and "–" not in f.eingabe and "—" not in f.eingabe for f in vage)
    assert len({f.eingabe for f in vage}) == len(vage)


def test_der_prompt_nennt_keinen_der_gemessenen_saetze_als_beispiel():
    """Sonst würde die Messung nur das Auswendiglernen prüfen: Beispiele im Prompt sind nicht die Testäußerungen."""
    prompt = ap.system_prompt("an")
    absatz = ap.persona.DEV_ABNAHME_PROMPT
    for f in fa.FAELLE:
        assert f.eingabe not in prompt, f.id
    assert "rezepte-app" not in absatz and "schöner" not in absatz


def test_kein_befehlsbeispiel_im_prompt_und_in_den_werkzeugen_solange_befehle_aus_sind():
    assert not ap.dev_agent_attrappe()._befehle_moeglich()
    schemas = json.dumps([s.to_default_dict() for s in ap.werkzeug_schemas()], ensure_ascii=False)
    assert "befehl: pytest" not in schemas
    for zustand in ap.ALLE_ZUSTAENDE:
        assert "befehl:" not in ap.system_prompt(zustand)


def test_der_prompt_folgt_der_entscheidung_des_dev_agent_nicht_einer_eigenen_variable(monkeypatch):
    monkeypatch.setenv("WAMO_ABNAHME_BEFEHLE", "ja")  # in der Betriebsart sokrates2 gibt es trotzdem keinen Ausführer
    assert "befehl:" not in ap.system_prompt("an")
    monkeypatch.setattr(ap.DevAgent, "_befehle_moeglich", lambda self: True)
    assert "befehl: pytest -q" in ap.system_prompt("an")
    assert "befehl:" not in ap.system_prompt("aus")  # ohne den Abnahme-Absatz nie
