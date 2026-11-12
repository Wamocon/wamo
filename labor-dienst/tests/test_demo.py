"""Lokale Vorführung mit Demo-Daten: python -m wamo_labor_dienst.demo (nur Loopback, Wegwerf-Tokens, temporärer Ordner)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wamo_labor_dienst import demo


@pytest.fixture
def vorfuehrung(tmp_path):
    app, zugang = demo.demo_bauen(tmp_path / "demo")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c, zugang


def test_demo_hat_plaetze_mit_tickets_in_allen_spalten_und_kommentaren(vorfuehrung):
    c, zugang = vorfuehrung
    r = c.get("/api/browser/board", headers={"X-Platz-Token": zugang.platz_token[1]})
    assert r.status_code == 200
    board = r.json()
    assert board["platz"] == 1 and board["durchlauf"]["offen"] is True
    stati = {t["status"] for t in board["tickets"]}
    assert stati == {"zu_erledigen", "in_arbeit", "erledigt"} and len(board["tickets"]) >= 9
    assert any(t["kommentare"] for t in board["tickets"]) and any(t["im_durchlauf"] for t in board["tickets"])
    assert {t["prioritaet"] for t in board["tickets"]} >= {"hoch", "hoechste"}


def test_demo_zeigt_dass_nutzertext_text_bleibt(vorfuehrung):
    c, zugang = vorfuehrung
    texte = " ".join(t["titel"] + t["beschreibung"] for t in c.get("/api/browser/board", headers={"X-Platz-Token": zugang.platz_token[1]}).json()["tickets"])
    assert "<b>" in texte and "&" in texte  # HTML-artiger Text steht in den Daten, die Seite zeigt ihn als Text


def test_demo_hat_einen_beendeten_und_geschlossene_plaetze(vorfuehrung):
    c, zugang = vorfuehrung
    beendet = c.get("/api/browser/board", headers={"X-Platz-Token": zugang.platz_token[3]}).json()
    assert beendet["durchlauf"]["offen"] is False and beendet["tickets"]
    uebersicht = c.get("/api/betreuer/plaetze", headers={"Authorization": f"Bearer {zugang.betreuer_token}"}).json()["plaetze"]
    assert len(uebersicht) == 16 and sum(p["offen"] for p in uebersicht) == 2
    assert [p["hat_platz_token"] for p in uebersicht[:4]] == [True, True, True, False]


def test_demo_tokens_haben_die_form_der_echten_und_sind_jedes_mal_neu(tmp_path):
    _, a = demo.demo_bauen(tmp_path / "a")
    _, b = demo.demo_bauen(tmp_path / "b")
    assert a.betreuer_token != b.betreuer_token and a.platz_token[1] != b.platz_token[1]
    assert len(a.platz_token[1]) == 32 and len(a.betreuer_token) >= 43


def test_zugang_zeigt_keine_token_in_repr_und_str(tmp_path):
    _, zugang = demo.demo_bauen(tmp_path / "demo")
    for text in (repr(zugang), str(zugang)):
        assert zugang.betreuer_token not in text and zugang.platz_token[1] not in text


def test_adressen_enthalten_die_token_im_fragment_nie_in_der_abfrage(tmp_path):
    _, zugang = demo.demo_bauen(tmp_path / "demo")
    zeilen = demo.adressen(zugang, "127.0.0.1", 8765)
    assert all("?" not in z for z in zeilen)
    assert any(f"/board#t={zugang.platz_token[1]}" in z for z in zeilen) and any(f"/betreuer#b={zugang.betreuer_token}" in z for z in zeilen)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.org", "::", ""])
def test_demo_lauscht_nur_auf_loopback(host, capsys):
    assert demo.main(["--host", host]) == 2
    assert "Loopback" in capsys.readouterr().err


def test_hilfe_und_falsche_angaben(capsys):
    assert demo.main(["--port", "0"]) == 2 and demo.main(["--port", "abc"]) == 2 and demo.main(["--unbekannt"]) == 2
