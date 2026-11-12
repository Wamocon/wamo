"""Parallele Schreibaufrufe (N1 des zweiten Reviews): Schreibrate (30 je Minute) und Mengenquoten (100 Tickets, 300 Kommentare, 1 MB) halten auch dann,
wenn viele Fäden gleichzeitig schreiben. Die Uhr wird erst nach BEGIN IMMEDIATE gelesen, sonst löscht ein Aufruf mit älterer Zeit die Einträge der anderen."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from hilfen import LABORTAG, ANLEGEN, aufruf, inhalt

from wamo_labor_dienst.datenbank import PlatzDB
from wamo_labor_dienst.regeln import Suche

FAEDEN = 32


class FeineUhr:
    """Eine Uhr, die wie echte Zeit weiterläuft: Jede Ablesung ist eine Millisekunde später (sicher für Fäden)."""

    def __init__(self, start: float = LABORTAG, schritt: float = 0.001):
        self.t, self.schritt, self._sperre = start, schritt, threading.Lock()

    def __call__(self) -> float:
        with self._sperre:
            self.t += self.schritt
            return self.t


def _datenbank(tmp_path, uhr) -> PlatzDB:
    db = PlatzDB(3, tmp_path / "platz-03.sqlite3", 16 * 1024 * 1024, uhr)
    db.anlegen()
    db.neuer_durchlauf(1)
    return db


def _parallel(db, aufrufe: list) -> list:
    """Alle Aufrufe gleichzeitig starten (eine Sperre hält die Fäden an der Startlinie), die Antworten in der Reihenfolge der Aufrufe."""
    start = threading.Barrier(FAEDEN)
    warten = threading.Event()

    def lauf(a):
        if not warten.is_set():
            try:
                start.wait(timeout=5)
            except threading.BrokenBarrierError:
                warten.set()
        return db.schreiben(a)

    with ThreadPoolExecutor(max_workers=FAEDEN) as pool:
        return list(pool.map(lauf, aufrufe))


def _anlegen_aufrufe(db, n: int) -> list:
    return [aufruf(db, "tickets", {**ANLEGEN, "titel": f"Parallel {i:03d}"}) for i in range(n)]


def _zaehlen(antworten) -> dict[int, int]:
    z: dict[int, int] = {}
    for a in antworten:
        z[a.status] = z.get(a.status, 0) + 1
    return z


def test_von_80_parallelen_aufrufen_gehen_genau_30_durch_der_rest_ist_429(tmp_path):
    db = _datenbank(tmp_path, FeineUhr())
    antworten = _parallel(db, _anlegen_aufrufe(db, 80))
    assert _zaehlen(antworten) == {201: 30, 429: 50}
    assert db.suchen(Suche())["gesamt"] == 6 + 30
    assert all(inhalt(a)["fehler"] == "quote_erreicht" for a in antworten if a.status == 429)


def test_auch_mit_stehender_uhr_gehen_genau_30_durch(tmp_path):
    from hilfen import Uhr

    db = _datenbank(tmp_path, Uhr())
    assert _zaehlen(_parallel(db, _anlegen_aufrufe(db, 80))) == {201: 30, 429: 50}


def test_auch_mit_zurueckgestellter_uhr_gehen_genau_30_durch(tmp_path):
    """Die Uhr stand vor der Last 5000 Sekunden zu weit und wurde zurückgestellt: Die Einträge aus der Zukunft zählen nicht, die Grenze gilt trotzdem."""
    uhr = FeineUhr()
    db = _datenbank(tmp_path, uhr)
    uhr.t += 5000
    assert _zaehlen(_parallel(db, _anlegen_aufrufe(db, 12))) == {201: 12}
    uhr.t -= 5000  # zurückgestellt: Die 12 Einträge liegen scheinbar in der Zukunft
    assert _zaehlen(_parallel(db, _anlegen_aufrufe(db, 80))) == {201: 30, 429: 50}
    assert db.suchen(Suche())["gesamt"] == 6 + 12 + 30


def test_geringes_zurueckspringen_der_uhr_zwischen_aufrufen_lockert_die_grenze_nicht(tmp_path):
    """Eine Uhr, die um Bruchteile einer Sekunde zurückspringt (Zeitabgleich), darf die Einträge der anderen Aufrufe nicht löschen."""
    zaehler = {"n": 0}
    sperre = threading.Lock()

    def zitternde_uhr() -> float:
        with sperre:
            zaehler["n"] += 1
            return LABORTAG + zaehler["n"] * 0.01 - (0.5 if zaehler["n"] % 3 == 0 else 0.0)

    db = _datenbank(tmp_path, zitternde_uhr)
    antworten = _parallel(db, _anlegen_aufrufe(db, 80))
    assert _zaehlen(antworten)[201] == 30 and db.suchen(Suche())["gesamt"] == 36


class SchrittUhr:
    """Jede Ablesung ist 61 Sekunden später: Das Schreibfenster ist nie voll, die Mengenquoten werden allein geprüft."""

    def __init__(self):
        self.t, self._sperre = LABORTAG, threading.Lock()

    def __call__(self) -> float:
        with self._sperre:
            self.t += 61
            return self.t


def test_ticketquote_100_haelt_bei_parallelen_aufrufen(tmp_path):
    db = _datenbank(tmp_path, SchrittUhr())
    antworten = _parallel(db, _anlegen_aufrufe(db, 140))
    assert _zaehlen(antworten) == {201: 94, 429: 46}
    assert db.suchen(Suche())["gesamt"] == 100 and db.zustand()["tickets"] == 100


def _je_faden(db, nummern: list[int], text: str, runden: int) -> list:
    """Jeder Faden besitzt seine Tickets allein (keine Versionskonflikte) und kommentiert sie der Reihe nach."""
    gruppen = [nummern[i::FAEDEN] for i in range(FAEDEN)]

    def lauf(gruppe: list[int]) -> list:
        antworten = []
        version = {n: 1 for n in gruppe}
        for _ in range(runden):
            for n in gruppe:
                a = db.schreiben(aufruf(db, "kommentare", {"erwartete_version": version[n], "text": text}, n))
                antworten.append(a)
                if a.status == 201:
                    version[n] += 1
        return antworten

    with ThreadPoolExecutor(max_workers=FAEDEN) as pool:
        return [a for liste in pool.map(lauf, gruppen) for a in liste]


def test_kommentarquote_300_haelt_bei_parallelen_aufrufen(tmp_path):
    db = _datenbank(tmp_path, SchrittUhr())
    for a in _anlegen_aufrufe(db, 94):  # 94 neue und 6 Seed-Tickets: 100
        assert db.schreiben(a).status == 201
    nummern = sorted({11, 12, 14, 17, 20, 25} | set(range(30, 124)))
    antworten = _je_faden(db, nummern, "k", 4)  # 100 Tickets mal 4 Runden: 400 Versuche
    zaehlen = _zaehlen(antworten)
    assert zaehlen == {201: 300, 429: 100}
    assert db.zustand()["kommentare"] == 300


def test_textquote_1_mb_haelt_bei_parallelen_aufrufen(tmp_path):
    db = _datenbank(tmp_path, SchrittUhr())
    for i in range(94):
        assert db.schreiben(aufruf(db, "tickets", {**ANLEGEN, "titel": "G" * 120, "beschreibung": "😀" * 1500})).status == 201
    nummern = sorted({11, 12, 14, 17, 20, 25} | set(range(30, 124)))
    antworten = _je_faden(db, nummern, "😀" * 500, 3)  # bis zu 300 Kommentare à 2000 Bytes: Die Textquote greift vor der Kommentarquote
    b = db.board()
    genutzt = sum(len(t["titel"].encode()) + len(t["beschreibung"].encode()) + sum(len(k["text"].encode()) for k in t["kommentare"]) for t in b["tickets"])
    assert genutzt <= 1024 * 1024 and genutzt + 2000 > 1024 * 1024
    assert any(a.status == 429 for a in antworten) and db.zustand()["kommentare"] < 300
    assert all(inhalt(a)["fehler"] == "quote_erreicht" for a in antworten if a.status == 429)


def test_die_uhr_wird_erst_nach_der_schreibsperre_gelesen(tmp_path):
    """Direkter Nachweis: Die Zeit eines Schreibaufrufs wird gelesen, wenn die Transaktion (BEGIN IMMEDIATE) schon offen ist, nie davor."""
    import contextlib

    class Beobachter:
        def __init__(self):
            self.offen, self.ablesungen = False, []

        def __call__(self) -> float:
            self.ablesungen.append(self.offen)
            return LABORTAG

    beobachter = Beobachter()
    db = _datenbank(tmp_path, beobachter)
    original = db._transaktion

    @contextlib.contextmanager
    def beobachtet():
        with original() as conn:
            beobachter.offen = True
            try:
                yield conn
            finally:
                beobachter.offen = False

    db._transaktion = beobachtet
    beobachter.ablesungen.clear()
    db.schreiben(aufruf(db, "tickets", ANLEGEN))
    assert beobachter.ablesungen == [True]
