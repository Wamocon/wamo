"""Token-Index im Speicher und Token-Datei ändern sich nur gemeinsam (N3 des zweiten Reviews): Ein entzogener Token darf nie im Speicher wieder gültig werden."""
from __future__ import annotations

import threading
import time

import pytest

from wamo_labor_dienst.app import create_app
from wamo_labor_dienst.datenbank import ZentralDB
from wamo_labor_dienst.sicherheit import token_hash

PLATZ = 3


def _token(verschiebung: int) -> str:
    """32 Hexzeichen aus Zeichen berechnet (kein Literal)."""
    return "".join("0123456789abcdef"[(i * 7 + verschiebung) % 16] for i in range(32))


@pytest.fixture
def dienst(konfig, uhr):
    return create_app(konfig, uhr=uhr, monotonic=uhr.monotonic).state.dienst


def _einig(dienst, tokens: list[str]) -> bool:
    """Im Speicher gültig genau dann, wenn auch in der Datei."""
    return all((dienst.platz_von_token(t) == PLATZ) == (dienst.zentral.platz_von_hash(token_hash(t)) == PLATZ) for t in tokens)


def test_vergeben_und_entziehen_gleichzeitig_hinterlassen_keinen_gueltigen_token_im_speicher_allein(dienst, monkeypatch):
    token = _token(1)
    drin, weiter = threading.Event(), threading.Event()
    original = ZentralDB.token_setzen

    def langsam(self, platz, hash_):
        original(self, platz, hash_)  # in der Datei steht der neue Hash ...
        drin.set()
        weiter.wait(5)  # ... und der Aufruf hält an, bevor der Speicher nachgezogen wird

    monkeypatch.setattr(ZentralDB, "token_setzen", langsam)
    a = threading.Thread(target=lambda: dienst.token_vergeben(PLATZ, token_hash(token)))
    a.start()
    assert drin.wait(5)
    b = threading.Thread(target=lambda: dienst.token_entziehen(PLATZ))  # der Besucherwechsel kommt dazwischen
    b.start()
    time.sleep(0.3)
    weiter.set()
    a.join(10)
    b.join(10)
    assert not a.is_alive() and not b.is_alive()
    assert _einig(dienst, [token])


def test_zufaellige_verschraenkung_laesst_speicher_und_datei_immer_uebereinstimmen(dienst):
    tokens = [_token(i) for i in range(1, 9)]
    start = threading.Barrier(8)
    fehler: list[BaseException] = []

    def lauf(nr: int):
        try:
            start.wait(5)
            for runde in range(25):
                if (nr + runde) % 3 == 0:
                    dienst.token_entziehen(PLATZ)
                else:
                    dienst.token_vergeben(PLATZ, token_hash(tokens[(nr + runde) % 8]))
        except BaseException as e:  # noqa: BLE001
            fehler.append(e)

    faeden = [threading.Thread(target=lauf, args=(n,)) for n in range(8)]
    [f.start() for f in faeden]
    [f.join(60) for f in faeden]
    assert fehler == [] and _einig(dienst, tokens)
    assert sum(dienst.platz_von_token(t) == PLATZ for t in tokens) <= 1  # ein Platz hat höchstens einen gültigen Token


def test_aufbewahrung_und_vergabe_gleichzeitig_bleiben_einig(dienst, uhr):
    from datetime import UTC, datetime

    tokens = [_token(i) for i in range(1, 5)]
    uhr.vor(datetime(2026, 12, 12, 12, tzinfo=UTC).timestamp() - uhr.t)
    start = threading.Barrier(2)

    def vergeben():
        start.wait(5)
        for t in tokens:
            dienst.token_vergeben(PLATZ, token_hash(t))

    def loeschen():
        start.wait(5)
        for _ in range(3):
            dienst.aufbewahrung_pruefen()

    faeden = [threading.Thread(target=vergeben), threading.Thread(target=loeschen)]
    [f.start() for f in faeden]
    [f.join(60) for f in faeden]
    assert _einig(dienst, tokens)
