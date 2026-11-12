"""Schreibpfad V4 bis V7: Reihenfolge der Prüfungen 2.2, Idempotenz, Schreibrate, Quoten D1, Atomarität D13."""
from __future__ import annotations

import threading
import uuid

import pytest
from hilfen import aendern, anlegen, aufruf, inhalt, kommentieren, status_setzen

from wamo_labor_dienst import schreiben as schreibpfad
from wamo_labor_dienst.regeln import Suche


# ---------- Erfolg ----------
def test_anlegen_liefert_das_ticket_nach_vertrag(db):
    a = anlegen(db)
    assert a.status == 201 and a.wiederholt is False
    assert inhalt(a) == {"nummer": "LAB-30", "titel": "Anmeldung schlägt fehl", "typ": "bug", "prioritaet": "hoch", "status": "zu_erledigen",
                         "beschreibung": "Nach dem Klick passiert nichts.", "version": 1, "im_durchlauf": True}
    assert inhalt(anlegen(db))["nummer"] == "LAB-31"


def test_antwort_ist_utf8_ohne_maskierung(db):
    a = anlegen(db, titel="Größe 😀")
    assert "Größe 😀".encode() in a.koerper and b"\\u" not in a.koerper


def test_aendern_aendert_nur_gesetzte_felder_und_erhoeht_die_version(db):
    a = aendern(db, 12, 1, {"titel": "Neuer Titel", "prioritaet": "hoechste"})
    assert a.status == 200
    t = inhalt(a)
    assert (t["titel"], t["prioritaet"], t["typ"], t["status"], t["version"]) == ("Neuer Titel", "hoechste", "bug", "in_arbeit", 2)
    assert t["beschreibung"] == "Statt einer Meldung bleibt die Seite leer."


def test_gleiche_werte_erhoehen_die_version_trotzdem(db):
    assert inhalt(aendern(db, 12, 1, {"typ": "bug"}))["version"] == 2
    assert inhalt(status_setzen(db, 12, 2, "in_arbeit"))["version"] == 3


def test_status_setzen_erlaubt_alle_uebergaenge(db):
    version = 1
    for status in ("erledigt", "zu_erledigen", "in_arbeit", "in_arbeit", "erledigt"):
        t = inhalt(status_setzen(db, 20, version, status))
        assert t["status"] == status and t["version"] == version + 1
        version = t["version"]


def test_kommentar_anlegen(db):
    a = kommentieren(db, 12, 1, "Ein\nKommentar")
    assert a.status == 201
    d = inhalt(a)
    assert set(d) == {"kommentar_id", "ticket"} and d["kommentar_id"] == 1 and d["ticket"]["version"] == 2
    assert inhalt(kommentieren(db, 20, 1, "Noch einer"))["kommentar_id"] == 2  # Zähler je Platz
    assert db.ticket(12)["kommentare"] == [{"kommentar_id": 1, "text": "Ein\nKommentar"}]


def test_im_durchlauf_nur_fuer_angelegte_tickets(db):
    nummer = inhalt(anlegen(db))["nummer"]
    assert db.ticket(int(nummer[4:]))["im_durchlauf"] is True and db.ticket(12)["im_durchlauf"] is False
    aendern(db, 12, 1, {"titel": "Neu x"})
    assert db.ticket(12)["im_durchlauf"] is False


# ---------- Reihenfolge der Prüfungen ----------
def _fehler(a, status, code):
    assert (a.status, inhalt(a)["fehler"]) == (status, code)
    assert set(inhalt(a)) == {"fehler", "text"}


def test_falscher_durchlauf_ist_409_und_nichts_aendert_sich(db):
    fremd = str(uuid.uuid4())
    _fehler(db.schreiben(aufruf(db, "tickets", {"typ": "bug", "prioritaet": "hoch", "titel": "Abc", "beschreibung": ""}, durchlauf=fremd)), 409, "durchlauf_beendet")
    _fehler(status_setzen(db, 12, 1, "erledigt", durchlauf=fremd), 409, "durchlauf_beendet")
    _fehler(aendern(db, 12, 1, {"titel": "Fremd"}, durchlauf=fremd), 409, "durchlauf_beendet")
    _fehler(kommentieren(db, 12, 1, "Fremd", durchlauf=fremd), 409, "durchlauf_beendet")
    assert db.ticket(12)["version"] == 1 and db.suchen(Suche())["gesamt"] == 6


def test_beendeter_durchlauf_ist_409(db):
    durchlauf = db.durchlauf()["durchlauf_id"]
    db.durchlauf_beenden()
    _fehler(status_setzen(db, 12, 1, "erledigt", durchlauf=durchlauf), 409, "durchlauf_beendet")


def test_durchlauf_gilt_ohne_beachtung_der_gross_und_kleinschreibung(db):
    assert status_setzen(db, 12, 1, "erledigt", durchlauf=db.durchlauf()["durchlauf_id"].upper()).status == 200


def test_alter_durchlauf_nach_neuem_durchlauf_ist_409(db):
    alt = db.durchlauf()["durchlauf_id"]
    db.neuer_durchlauf(1)
    _fehler(status_setzen(db, 12, 1, "erledigt", durchlauf=alt), 409, "durchlauf_beendet")


def test_unbekanntes_ticket_ist_404_vor_der_version(db):
    _fehler(status_setzen(db, 13, 7, "erledigt"), 404, "ticket_unbekannt")
    _fehler(aendern(db, 9999, 1, {"titel": "Neu x"}), 404, "ticket_unbekannt")
    _fehler(kommentieren(db, 9999, 1, "x"), 404, "ticket_unbekannt")


def test_durchlauf_kommt_vor_ticket_und_ticket_vor_version(db):
    fremd = str(uuid.uuid4())
    _fehler(status_setzen(db, 9999, 1, "erledigt", durchlauf=fremd), 409, "durchlauf_beendet")
    _fehler(status_setzen(db, 12, 999, "erledigt", durchlauf=fremd), 409, "durchlauf_beendet")
    _fehler(status_setzen(db, 12, 999, "erledigt"), 409, "ticket_veraendert")


def test_falsche_version_ist_409_ticket_veraendert_und_aendert_nichts(db):
    status_setzen(db, 12, 1, "erledigt")
    _fehler(status_setzen(db, 12, 1, "zu_erledigen"), 409, "ticket_veraendert")
    _fehler(aendern(db, 12, 1, {"titel": "Neu x"}), 409, "ticket_veraendert")
    _fehler(kommentieren(db, 12, 3, "x"), 409, "ticket_veraendert")
    t = db.ticket(12)
    assert (t["version"], t["status"], t["kommentare"]) == (2, "erledigt", [])


# ---------- Idempotenz ----------
def test_gleicher_schluessel_gleiche_anfrage_gleiche_antwort_ohne_zweites_ticket(db):
    schluessel = str(uuid.uuid4())
    erst = db.schreiben(aufruf(db, "tickets", {"typ": "bug", "prioritaet": "hoch", "titel": "Einmal", "beschreibung": ""}, schluessel=schluessel))
    zweit = db.schreiben(aufruf(db, "tickets", {"typ": "bug", "prioritaet": "hoch", "titel": "Einmal", "beschreibung": ""}, schluessel=schluessel))
    assert erst.wiederholt is False and zweit.wiederholt is True
    assert (zweit.status, zweit.koerper) == (erst.status, erst.koerper)
    assert db.suchen(Suche())["gesamt"] == 7


def test_schluessel_gilt_ohne_beachtung_der_gross_und_kleinschreibung(db):
    schluessel = str(uuid.uuid4())
    anlegen(db, titel="Abc", schluessel=schluessel.upper())
    assert anlegen(db, titel="Abc", schluessel=schluessel.lower()).wiederholt is True


def test_gleicher_schluessel_andere_anfrage_ist_422(db):
    schluessel = str(uuid.uuid4())
    anlegen(db, titel="Erster", schluessel=schluessel)
    a = anlegen(db, titel="Anderer", schluessel=schluessel)
    _fehler(a, 422, "eingabe_ungueltig")
    assert db.suchen(Suche())["gesamt"] == 7


def test_gleicher_schluessel_anderes_ticket_ist_422(db):
    schluessel = str(uuid.uuid4())
    status_setzen(db, 12, 1, "erledigt", schluessel=schluessel)
    _fehler(status_setzen(db, 20, 1, "erledigt", schluessel=schluessel), 422, "eingabe_ungueltig")


def test_wiederholte_statusaenderung_zaehlt_die_version_nur_einmal(db):
    schluessel = str(uuid.uuid4())
    a, b = (status_setzen(db, 12, 1, "erledigt", schluessel=schluessel) for _ in range(2))
    assert a.koerper == b.koerper and db.ticket(12)["version"] == 2


def test_gespeichert_werden_409_und_404_aber_nicht_429(db):
    fremd, schluessel = str(uuid.uuid4()), str(uuid.uuid4())
    erst = status_setzen(db, 12, 1, "erledigt", durchlauf=fremd, schluessel=schluessel)
    zweit = status_setzen(db, 12, 1, "erledigt", schluessel=schluessel)  # jetzt mit dem richtigen Durchlauf: die gespeicherte Antwort gilt
    assert erst.status == 409 and zweit.status == 409 and zweit.wiederholt is True and db.ticket(12)["version"] == 1
    k = str(uuid.uuid4())
    assert status_setzen(db, 13, 1, "erledigt", schluessel=k).status == 404
    assert status_setzen(db, 13, 1, "erledigt", schluessel=k).wiederholt is True


def test_schluessel_laeuft_nach_24_stunden_ab(db, uhr):
    schluessel = str(uuid.uuid4())
    anlegen(db, titel="Alt", schluessel=schluessel)
    uhr.vor(24 * 3600)
    assert anlegen(db, titel="Alt", schluessel=schluessel).wiederholt is True  # genau 24 Stunden gelten noch
    uhr.vor(1)
    neu = anlegen(db, titel="Alt", schluessel=schluessel)
    assert neu.wiederholt is False and neu.status == 201 and db.suchen(Suche())["gesamt"] == 8


def test_idempotenzspeicher_ist_begrenzt(db, uhr, monkeypatch):
    monkeypatch.setattr(schreibpfad, "IDEM_MAX", 5)
    schluessel = []
    for i in range(8):
        schluessel.append(str(uuid.uuid4()))
        status_setzen(db, 12, i + 1, "in_arbeit", schluessel=schluessel[-1])
        uhr.vor(1)
    assert status_setzen(db, 12, 9, "erledigt", schluessel=schluessel[7]).status == 422  # der jüngste Schlüssel ist noch da (andere Anfrage)
    assert status_setzen(db, 12, 9, "erledigt", schluessel=schluessel[0]).status == 200  # der älteste wurde verdrängt, die Anfrage gilt als neu
    assert db.ticket(12)["version"] == 10


# ---------- Schreibrate ----------
def test_schreibrate_30_je_minute_der_31_aufruf_ist_429_und_zaehlt_nicht(db, uhr):
    version = 1
    for _ in range(30):
        version = inhalt(status_setzen(db, 12, version, "in_arbeit"))["version"]
    abgelehnt = status_setzen(db, 12, version, "in_arbeit")
    _fehler(abgelehnt, 429, "quote_erreicht")
    assert db.ticket(12)["version"] == version
    uhr.vor(1)
    _fehler(status_setzen(db, 12, version, "in_arbeit"), 429, "quote_erreicht")
    uhr.vor(59)  # 60 Sekunden nach dem ersten Aufruf: das Fenster ist frei, der abgelehnte Aufruf hat nicht mitgezählt
    assert status_setzen(db, 12, version, "in_arbeit").status == 200


def test_429_wird_nicht_gespeichert_die_wiederholung_fuehrt_erneut_aus(db, uhr):
    for _ in range(30):
        anlegen(db)
    schluessel = str(uuid.uuid4())
    _fehler(anlegen(db, titel="Spaeter", schluessel=schluessel), 429, "quote_erreicht")
    uhr.vor(61)
    a = anlegen(db, titel="Spaeter", schluessel=schluessel)
    assert a.status == 201 and a.wiederholt is False


def test_schreibrate_vor_durchlauf_und_ticket(db):
    for _ in range(30):
        anlegen(db)
    _fehler(status_setzen(db, 9999, 1, "erledigt", durchlauf=str(uuid.uuid4())), 429, "quote_erreicht")


def test_wiederholung_zaehlt_nicht_zur_schreibrate_und_geht_der_rate_voraus(db):
    schluessel = str(uuid.uuid4())
    anlegen(db, schluessel=schluessel)
    for _ in range(29):
        anlegen(db)
    assert anlegen(db, schluessel=schluessel).wiederholt is True  # das Fenster ist voll, die Wiederholung kommt trotzdem
    _fehler(anlegen(db), 429, "quote_erreicht")


def test_409_und_404_zaehlen_zur_schreibrate_mit(db):
    for _ in range(15):
        status_setzen(db, 12, 99, "erledigt")
        status_setzen(db, 13, 1, "erledigt")
    _fehler(anlegen(db), 429, "quote_erreicht")


# ---------- Mengenquoten (D1) ----------
def _frei(uhr, i):
    if i % 25 == 24:
        uhr.vor(61)


def test_ticketquote_100_je_platz_der_seed_zaehlt_mit(db, uhr):
    for i in range(94):
        _frei(uhr, i)
        assert anlegen(db, titel=f"Menge {i:03d}").status == 201
    a = anlegen(db)
    _fehler(a, 429, "quote_erreicht")
    assert db.suchen(Suche())["gesamt"] == 100
    assert status_setzen(db, 12, 1, "erledigt").status == 200  # Ändern geht weiter


def test_kommentarquote_300_je_platz(db, uhr):
    version = 1
    for i in range(300):
        _frei(uhr, i)
        a = kommentieren(db, 12, version, "k")
        assert a.status == 201
        version = inhalt(a)["ticket"]["version"]
    _fehler(kommentieren(db, 12, version, "k"), 429, "quote_erreicht")
    assert db.zustand()["kommentare"] == 300 and db.ticket(12)["version"] == version


def _genutzt(db) -> int:
    b = db.board()
    return sum(len(t["titel"].encode()) + len(t["beschreibung"].encode()) + sum(len(k["text"].encode()) for k in t["kommentare"]) for t in b["tickets"])


def test_textquote_1_mb_und_verkuerzen_bleibt_erlaubt(db, uhr):
    n = 0
    for i in range(94):
        _frei(uhr, n)
        n += 1
        assert anlegen(db, titel="G" * 120, beschreibung="😀" * 1500).status == 201
    version, nummer = 1, 12
    while True:
        _frei(uhr, n)
        n += 1
        a = kommentieren(db, nummer, version, "😀" * 500)
        if a.status != 201:
            break
        version = inhalt(a)["ticket"]["version"]
    _fehler(a, 429, "quote_erreicht")
    genutzt = _genutzt(db)
    assert genutzt <= 1024 * 1024 and genutzt + 2000 > 1024 * 1024
    assert db.zustand()["kommentare"] < 300  # die Textquote greift vor der Kommentarquote
    uhr.vor(61)
    kurz = aendern(db, 12, version, {"titel": "Kürzer", "beschreibung": ""})
    assert kurz.status == 200  # wer weniger Text hat, darf immer ändern
    assert _genutzt(db) < genutzt
    nummer_neu = inhalt(db.schreiben(aufruf(db, "tickets", {"typ": "bug", "prioritaet": "hoch", "titel": "Abc", "beschreibung": ""}))).get("fehler")
    assert nummer_neu == "quote_erreicht"  # die Ticketquote greift zuerst (100)


def test_aendern_ueber_die_textquote_ist_429_ohne_aenderung(db, uhr):
    n = 0
    for i in range(94):
        _frei(uhr, n)
        n += 1
        anlegen(db, titel="G" * 120, beschreibung="😀" * 1500)
    version = 1
    while True:
        _frei(uhr, n)
        n += 1
        a = kommentieren(db, 12, version, "😀" * 500)
        if a.status != 201:
            break
        version = inhalt(a)["ticket"]["version"]
    uhr.vor(61)
    vorher = db.ticket(11)
    _fehler(aendern(db, 11, 1, {"beschreibung": "😀" * 1500}), 429, "quote_erreicht")
    assert db.ticket(11) == vorher


# ---------- Atomarität (D13) ----------
def test_ein_fehler_im_schreiben_laesst_den_platz_unveraendert(db, monkeypatch):
    original = schreibpfad.json_bytes

    def kaputt(obj):
        raise RuntimeError("Absturz nach dem Schreiben")

    monkeypatch.setattr(schreibpfad, "json_bytes", kaputt)
    with pytest.raises(RuntimeError):
        status_setzen(db, 12, 1, "erledigt")
    with pytest.raises(RuntimeError):
        anlegen(db)
    monkeypatch.setattr(schreibpfad, "json_bytes", original)
    assert db.ticket(12)["version"] == 1 and db.ticket(12)["status"] == "in_arbeit" and db.suchen(Suche())["gesamt"] == 6
    assert inhalt(anlegen(db))["nummer"] == "LAB-30"  # auch der Zähler ist unverändert
    for _ in range(29):  # die abgebrochenen Aufrufe haben die Schreibrate nicht belegt: 30 sind frei, einer ist schon verbraucht
        assert status_setzen(db, 12, 1 + 0, "erledigt").status in (200, 409)


def test_gleichzeitige_aenderungen_mit_derselben_version_genau_eine_gewinnt(db):
    ergebnisse: list = []
    sperre = threading.Barrier(8)

    def lauf():
        sperre.wait()
        ergebnisse.append(status_setzen(db, 12, 1, "erledigt"))

    threads = [threading.Thread(target=lauf) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(a.status for a in ergebnisse) == [200] + [409] * 7
    assert all(inhalt(a)["fehler"] == "ticket_veraendert" for a in ergebnisse if a.status == 409)
    assert db.ticket(12)["version"] == 2


def test_gleichzeitige_anlage_mit_demselben_schluessel_ergibt_ein_ticket(db):
    schluessel = str(uuid.uuid4())
    ergebnisse: list = []
    sperre = threading.Barrier(5)

    def lauf():
        sperre.wait()
        ergebnisse.append(anlegen(db, titel="Parallel", schluessel=schluessel))

    threads = [threading.Thread(target=lauf) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert {a.status for a in ergebnisse} == {201} and len({a.koerper for a in ergebnisse}) == 1
    assert sum(a.wiederholt for a in ergebnisse) == 4 and db.suchen(Suche())["gesamt"] == 7


def test_neuer_durchlauf_waehrend_geschrieben_wird_laesst_nie_altes_zu(db):
    alt = db.durchlauf()["durchlauf_id"]
    db.neuer_durchlauf(2)
    for _ in range(3):
        assert status_setzen(db, 12, 1, "erledigt", durchlauf=alt).status == 409


def test_ticketnummer_ueber_9999_ist_429_nie_ein_ungueltiges_ticket(db):
    import sqlite3

    verbindung = sqlite3.connect(db.pfad)
    verbindung.execute("UPDATE meta SET ticket_zaehler = 10000")
    verbindung.commit()
    verbindung.close()
    _fehler(anlegen(db), 429, "quote_erreicht")
    assert db.suchen(Suche())["gesamt"] == 6


def test_datenbankfehler_ohne_volllauf_wird_zur_ausnahme_und_laesst_den_platz_unveraendert(db, monkeypatch):
    import sqlite3

    def gesperrt(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(schreibpfad, "ausfuehren", gesperrt)
    with pytest.raises(sqlite3.OperationalError):
        anlegen(db)
    monkeypatch.undo()
    assert db.suchen(Suche())["gesamt"] == 6 and anlegen(db).status == 201
