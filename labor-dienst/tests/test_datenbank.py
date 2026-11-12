"""Speicher je Platz (SQLite): Seed, Lesen für V1 bis V3 und Board, Betreuer-Wirkungen, zentrale Tokentabelle."""
from __future__ import annotations

import sqlite3

import pytest
from hilfen import anlegen, inhalt, kommentieren

from wamo_labor_dienst.datenbank import PlatzDB
from wamo_labor_dienst.modell import iso_zeit, ticket_ausgabe
from wamo_labor_dienst.regeln import UUID_RE, Suche
from wamo_labor_dienst.seed import SEED

SEED_NUMMERN = [11, 12, 14, 17, 20, 25]


def test_iso_zeit_ist_utc_mit_z():
    assert iso_zeit(1.0) == "1970-01-01T00:00:01Z"
    assert iso_zeit(1_795_858_200.0).endswith("Z")


def test_seed_hat_die_vertragstickets():
    assert [s[0] for s in SEED] == SEED_NUMMERN
    nach_nummer = {s[0]: s for s in SEED}
    assert nach_nummer[11][3] == "erledigt" and nach_nummer[12][3] == "in_arbeit" and nach_nummer[20][3] == "zu_erledigen"


def test_neuer_platz_ist_geschlossen_mit_seed_und_ohne_einwilligung(tmp_path, uhr):
    d = PlatzDB(5, tmp_path / "p.sqlite3", 16 * 1024 * 1024, uhr)
    d.anlegen()
    v1 = d.durchlauf()
    assert set(v1) == {"durchlauf_id", "offen", "einwilligung_version", "einwilligung_zeit"}
    assert UUID_RE.fullmatch(v1["durchlauf_id"])
    assert v1["offen"] is False and v1["einwilligung_version"] is None and v1["einwilligung_zeit"] is None
    s = d.suchen(Suche())
    assert [t["nummer"] for t in s["tickets"]] == [f"LAB-{n}" for n in reversed(SEED_NUMMERN)]
    assert s["gesamt"] == 6
    assert all(t["version"] == 1 and t["im_durchlauf"] is False for t in s["tickets"])


def test_anlegen_zweimal_laesst_den_zustand_unveraendert(db, tmp_path, uhr):
    vorher = db.durchlauf()
    anlegen(db)
    zweit = PlatzDB(3, tmp_path / "platz-03.sqlite3", 16 * 1024 * 1024, uhr)
    zweit.anlegen()
    assert zweit.durchlauf() == vorher
    assert zweit.suchen(Suche())["gesamt"] == 7


def test_neuer_durchlauf_setzt_alles_zurueck_und_erfasst_die_einwilligung(db, uhr):
    alt = db.durchlauf()
    anlegen(db)
    uhr.vor(120)
    db.neuer_durchlauf(7)
    neu = db.durchlauf()
    assert neu["durchlauf_id"] != alt["durchlauf_id"] and neu["offen"] is True
    assert neu["einwilligung_version"] == 7 and neu["einwilligung_zeit"] == iso_zeit(uhr())
    assert db.suchen(Suche())["gesamt"] == 6
    nr = inhalt(anlegen(db))["nummer"]
    assert nr == "LAB-30"  # Zähler wieder ab 30


def test_neuer_durchlauf_leert_idempotenz_und_schreibrate(db, uhr):
    for _ in range(30):
        anlegen(db)
    db.neuer_durchlauf(1)
    assert anlegen(db).status == 201  # keine Schreibrate des vorigen Teilnehmers


def test_durchlauf_beenden_laesst_id_und_einwilligung(db):
    vorher = db.durchlauf()
    db.durchlauf_beenden()
    nachher = db.durchlauf()
    assert nachher["offen"] is False
    assert (nachher["durchlauf_id"], nachher["einwilligung_version"]) == (vorher["durchlauf_id"], vorher["einwilligung_version"])


def test_zuruecksetzen_stellt_den_seed_her_und_behaelt_den_durchlauf(db):
    vorher = db.durchlauf()
    nr = inhalt(anlegen(db))["nummer"]
    kommentieren(db, 12, 1, "weg damit")
    db.zuruecksetzen()
    assert db.durchlauf() == vorher
    s = db.suchen(Suche())
    assert s["gesamt"] == 6 and nr not in [t["nummer"] for t in s["tickets"]]
    assert db.ticket(12)["kommentare"] == [] and db.ticket(12)["version"] == 1
    assert inhalt(anlegen(db))["nummer"] == "LAB-30"


def test_daten_loeschen_entfernt_alles_und_schliesst_den_platz(db, tmp_path):
    vorher = db.durchlauf()
    anlegen(db, titel="Geheimer Titel aus dem Test")
    kommentieren(db, 12, 1, "Geheimer Kommentar aus dem Test")
    db.daten_loeschen()
    v1 = db.durchlauf()
    assert v1["offen"] is False and v1["einwilligung_version"] is None and v1["einwilligung_zeit"] is None
    assert v1["durchlauf_id"] != vorher["durchlauf_id"]
    assert db.suchen(Suche()) == {"tickets": [], "gesamt": 0}
    assert db.zustand()["kommentare"] == 0
    for datei in tmp_path.glob("platz-03.sqlite3*"):  # nichts bleibt in der Datei oder im Journal zurück
        assert b"Geheimer" not in datei.read_bytes()


def test_suche_filter_und_sortierung(db):
    anlegen(db, typ="story", prioritaet="hoechste", titel="Neue Story")
    s = db.suchen(Suche(typ="bug"))
    assert [t["nummer"] for t in s["tickets"]] == ["LAB-12", "LAB-11"] and s["gesamt"] == 2
    assert [t["nummer"] for t in db.suchen(Suche(typ="story"))["tickets"]] == ["LAB-30", "LAB-25"]
    assert [t["nummer"] for t in db.suchen(Suche(status="erledigt"))["tickets"]] == ["LAB-11"]
    assert [t["nummer"] for t in db.suchen(Suche(prioritaet="hoechste"))["tickets"]] == ["LAB-30"]
    assert [t["nummer"] for t in db.suchen(Suche(nummer=12))["tickets"]] == ["LAB-12"]
    assert db.suchen(Suche(nummer=13)) == {"tickets": [], "gesamt": 0}
    assert [t["nummer"] for t in db.suchen(Suche(nur_durchlauf=True))["tickets"]] == ["LAB-30"]
    assert db.suchen(Suche(typ="bug", status="in_arbeit"))["gesamt"] == 1  # Filter gelten zusammen
    s = db.suchen(Suche(limit=2))
    assert len(s["tickets"]) == 2 and s["gesamt"] == 7 and s["tickets"][0]["nummer"] == "LAB-30"


def test_suche_ist_woertlich_prozent_unterstrich_und_gegenschraegstrich(db):
    for titel in ("100% fertig", "1000 fertig", "a_b Test", "axb Test", "Pfad C:\\temp"):
        anlegen(db, titel=titel)
    assert [t["titel"] for t in db.suchen(Suche(q="%"))["tickets"]] == ["100% fertig"]
    assert [t["titel"] for t in db.suchen(Suche(q="_"))["tickets"]] == ["a_b Test"]
    assert [t["titel"] for t in db.suchen(Suche(q="a_b"))["tickets"]] == ["a_b Test"]
    assert [t["titel"] for t in db.suchen(Suche(q="\\"))["tickets"]] == ["Pfad C:\\temp"]
    assert db.suchen(Suche(q="%%"))["gesamt"] == 0


def test_suche_ohne_beachtung_der_gross_und_kleinschreibung_auch_bei_umlauten(db):
    anlegen(db, titel="Großer Ärger", beschreibung="Die STRASSE ist gesperrt")
    assert db.suchen(Suche(q="ärger"))["gesamt"] == 1
    assert db.suchen(Suche(q="GROßER"))["gesamt"] == 1
    assert db.suchen(Suche(q="strasse"))["gesamt"] == 1  # Beschreibung zählt mit
    assert db.suchen(Suche(q="Straße"))["gesamt"] == 1


@pytest.mark.parametrize("q", ["' OR 1=1 --", "'; DROP TABLE ticket;--", '" OR ""="', "' UNION SELECT NULL--"])
def test_einspritzung_in_der_suche_findet_nichts_und_zerstoert_nichts(db, q):
    assert db.suchen(Suche(q=q)) == {"tickets": [], "gesamt": 0}
    anlegen(db, titel=q)
    assert [t["titel"] for t in db.suchen(Suche(q=q))["tickets"]] == [q]
    assert db.suchen(Suche())["gesamt"] == 7


def test_liste_wird_ueber_60_kb_gekuerzt_und_gesamt_bleibt_wahr(db, uhr):
    for _ in range(10):
        anlegen(db, titel="G" * 100, beschreibung="😀" * 1500)
    s = db.suchen(Suche())
    import json

    groesse = len(json.dumps(s, ensure_ascii=False).encode("utf-8"))
    assert groesse <= 60 * 1024 and 0 < len(s["tickets"]) < 10
    assert s["gesamt"] == 16 and s["tickets"][0]["nummer"] == "LAB-39"  # der Anfang der Liste (neueste zuerst)


def test_ticket_lesen_mit_den_letzten_20_kommentaren_aeltester_zuerst(db, uhr):
    version = 1
    for i in range(1, 23):
        version = inhalt(kommentieren(db, 12, version, f"Kommentar {i}"))["ticket"]["version"]
        if i % 20 == 0:
            uhr.vor(61)
    t = db.ticket(12)
    assert [k["text"] for k in t["kommentare"]] == [f"Kommentar {i}" for i in range(3, 23)]
    assert [k["kommentar_id"] for k in t["kommentare"]] == list(range(3, 23))
    assert set(t) == {"nummer", "titel", "typ", "prioritaet", "status", "beschreibung", "version", "im_durchlauf", "kommentare"}
    assert t["version"] == 23


def test_ticket_unbekannt_ist_none(db):
    assert db.ticket(9999) is None and db.ticket(13) is None


def test_ticket_ausgabe_hat_genau_die_vertragsfelder(db):
    verbindung = sqlite3.connect(db.pfad)
    verbindung.row_factory = sqlite3.Row
    zeile = verbindung.execute("SELECT nummer,titel,typ,prioritaet,status,beschreibung,version,im_durchlauf FROM ticket WHERE nummer=12").fetchone()
    verbindung.close()
    t = ticket_ausgabe(zeile)
    assert t == {"nummer": "LAB-12", "titel": t["titel"], "typ": "bug", "prioritaet": "hoch", "status": "in_arbeit",
                 "beschreibung": t["beschreibung"], "version": 1, "im_durchlauf": False}


def test_board_ist_vollstaendig_und_nur_fuer_diesen_platz(db, uhr):
    nr = inhalt(anlegen(db, titel="Board Titel"))
    kommentieren(db, 12, 1, "Board Kommentar")
    b = db.board()
    assert set(b) == {"platz", "durchlauf", "tickets"} and b["platz"] == 3
    assert b["durchlauf"] == {"offen": True, "einwilligung_version": 1}
    assert [t["nummer"] for t in b["tickets"]] == ["LAB-11", "LAB-12", "LAB-14", "LAB-17", "LAB-20", "LAB-25", nr["nummer"]]
    t12 = next(t for t in b["tickets"] if t["nummer"] == "LAB-12")
    assert set(t12) == {"nummer", "titel", "typ", "prioritaet", "status", "beschreibung", "version", "im_durchlauf", "angelegt", "geaendert", "kommentare"}
    assert t12["version"] == 2 and t12["kommentare"][0]["text"] == "Board Kommentar"
    assert set(t12["kommentare"][0]) == {"kommentar_id", "text", "angelegt"}
    assert t12["geaendert"] == iso_zeit(uhr()) and t12["angelegt"] == iso_zeit(uhr())
    assert "durchlauf_id" not in b["durchlauf"]


def test_zustand_fuer_die_betreuer_ohne_texte(db):
    anlegen(db, titel="Geheim")
    kommentieren(db, 12, 1, "Geheim")
    z = db.zustand()
    assert z == {"platz": 3, "durchlauf_id": db.durchlauf()["durchlauf_id"], "offen": True, "einwilligung_version": 1,
                 "einwilligung_zeit": db.durchlauf()["einwilligung_zeit"], "tickets": 7, "kommentare": 1}


def test_datei_waechst_nie_ueber_die_grenze_und_der_dienst_stuerzt_nicht_ab(tmp_path, uhr):
    grenze = 256 * 1024
    d = PlatzDB(1, tmp_path / "klein.sqlite3", grenze, uhr)
    d.anlegen()
    d.neuer_durchlauf(1)
    ergebnisse = []
    for i in range(90):
        if i % 25 == 24:
            uhr.vor(61)
        a = anlegen(d, titel=f"Titel {i:03d}", beschreibung="😀" * 1500)
        ergebnisse.append(a.status)
        if a.status != 201:
            assert a.status == 429 and inhalt(a)["fehler"] == "quote_erreicht"
            break
    assert 429 in ergebnisse and ergebnisse[0] == 201
    assert (tmp_path / "klein.sqlite3").stat().st_size <= grenze
    assert d.durchlauf()["offen"] is True  # lesen geht weiter
    assert d.suchen(Suche())["gesamt"] == 6 + ergebnisse.count(201)
    d.zuruecksetzen()  # Betreuer räumt auch bei voller Datei auf
    assert d.suchen(Suche())["gesamt"] == 6


def test_zwei_plaetze_teilen_nichts(tmp_path, uhr):
    a, b = PlatzDB(1, tmp_path / "1.sqlite3", 16 << 20, uhr), PlatzDB(2, tmp_path / "2.sqlite3", 16 << 20, uhr)
    for d in (a, b):
        d.anlegen()
        d.neuer_durchlauf(1)
    anlegen(a, titel="Nur auf Platz eins")
    assert a.suchen(Suche())["gesamt"] == 7 and b.suchen(Suche())["gesamt"] == 6
    assert b.suchen(Suche(q="Platz eins"))["gesamt"] == 0
    assert a.durchlauf()["durchlauf_id"] != b.durchlauf()["durchlauf_id"]


# ---------- zentrale Tokentabelle ----------
def test_zentral_speichert_nur_den_hash(zentral, tmp_path):
    token_hash = "ab" * 32
    zentral.token_setzen(4, token_hash)
    assert zentral.platz_von_hash(token_hash) == 4 and zentral.hat_token(4) is True
    assert zentral.platz_von_hash("cd" * 32) is None and zentral.hat_token(5) is False


def test_zentral_erneuern_ersetzt_den_alten_hash(zentral):
    zentral.token_setzen(4, "11" * 32)
    zentral.token_setzen(4, "22" * 32)
    assert zentral.platz_von_hash("11" * 32) is None and zentral.platz_von_hash("22" * 32) == 4


def test_zentral_ein_hash_gehoert_nie_zwei_plaetzen(zentral):
    zentral.token_setzen(1, "11" * 32)
    with pytest.raises(sqlite3.IntegrityError):
        zentral.token_setzen(2, "11" * 32)


def test_zentral_tokens_loeschen(zentral):
    zentral.token_setzen(1, "11" * 32)
    zentral.token_setzen(2, "22" * 32)
    zentral.tokens_loeschen()
    assert zentral.hat_token(1) is False and zentral.platz_von_hash("22" * 32) is None


def test_zentral_gesundheit(zentral):
    assert zentral.pruefen() is True


def test_zentral_token_loeschen_entfernt_nur_den_hash_des_platzes(zentral):
    zentral.token_setzen(1, "11" * 32)
    zentral.token_setzen(2, "22" * 32)
    zentral.token_loeschen(1)
    assert zentral.hat_token(1) is False and zentral.platz_von_hash("11" * 32) is None
    assert zentral.hat_token(2) is True
    zentral.token_loeschen(1)  # ein zweites Mal ist kein Fehler
