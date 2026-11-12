"""Wertebereiche und Prüfungen der Eingaben nach Vertrag 2.3 und Abschnitt 3."""
from __future__ import annotations

import pytest

from wamo_labor_dienst.regeln import (
    DienstFehler,
    Suche,
    json_lesen,
    parse_suche,
    pruefe_aendern,
    pruefe_anlegen,
    pruefe_kommentar,
    pruefe_status,
    text_pruefen,
)


def _fehler(aufruf, *args, **kw) -> DienstFehler:
    with pytest.raises(DienstFehler) as e:
        aufruf(*args, **kw)
    assert e.value.status == 422 and e.value.code == "eingabe_ungueltig"
    return e.value


def test_dienstfehler_traegt_status_code_text_und_allow():
    e = DienstFehler(405, "nicht_erlaubt", "Text", allow="GET")
    assert (e.status, e.code, e.text, e.allow) == (405, "nicht_erlaubt", "Text", "GET")
    assert DienstFehler(404, "x", "y").allow is None


def test_text_laenge_zaehlt_codepunkte_nicht_bytes():
    emoji = "\U0001F600"
    assert text_pruefen(emoji * 120, "titel", zeilen=False, lo=3, hi=120) == emoji * 120
    assert "titel" in _fehler(text_pruefen, emoji * 121, "titel", zeilen=False, lo=3, hi=120).text


@pytest.mark.parametrize("text,lo,hi,ok", [("ab", 3, 120, False), ("abc", 3, 120, True), ("x" * 120, 3, 120, True), ("x" * 121, 3, 120, False),
                                           ("", 0, 1500, True), ("x" * 1501, 0, 1500, False), ("", 1, 500, False), ("x" * 500, 1, 500, True)])
def test_grenzen_der_laenge(text, lo, hi, ok):
    if ok:
        assert text_pruefen(text, "f", zeilen=True, lo=lo, hi=hi) == text
    else:
        _fehler(text_pruefen, text, "f", zeilen=True, lo=lo, hi=hi)


@pytest.mark.parametrize("zeichen", ["\x00", "\x1f", "\x7f", "\x85", "\r", "‮", "​", " ", " ", "", "͸",
                                      "\ud800", "﻿", "\xad", "⁦"])
def test_verbotene_zeichen_werden_abgelehnt_nicht_angepasst(zeichen):
    for zeilen in (False, True):
        _fehler(text_pruefen, f"ab{zeichen}c", "titel", zeilen=zeilen, lo=0, hi=50)


def test_zeilenumbruch_nur_wo_erlaubt():
    assert text_pruefen("a\nb", "beschreibung", zeilen=True, lo=0, hi=50) == "a\nb"
    _fehler(text_pruefen, "a\nb", "titel", zeilen=False, lo=0, hi=50)
    _fehler(text_pruefen, "a\r\nb", "beschreibung", zeilen=True, lo=0, hi=50)


def test_nur_nfc_wird_angenommen():
    assert text_pruefen("Café", "titel", zeilen=False, lo=3, hi=50) == "Café"
    _fehler(text_pruefen, "Café", "titel", zeilen=False, lo=3, hi=50)


@pytest.mark.parametrize("wert", [None, 5, ["a"], {"a": 1}, True, 1.5])
def test_text_muss_ein_string_sein(wert):
    _fehler(text_pruefen, wert, "titel", zeilen=False, lo=0, hi=50)


def test_fehlertext_spiegelt_die_eingabe_nie():
    e = _fehler(text_pruefen, "<script>alert(1)</script>\x00", "titel", zeilen=False, lo=0, hi=50)
    assert "<script>" not in e.text and "alert" not in e.text


# ---------- JSON ----------
def test_json_lesen_gueltig():
    assert json_lesen('{"a": "ä"}'.encode()) == {"a": "ä"}


@pytest.mark.parametrize("roh", [b"", b"{nicht json", b'{"a": 1, "a": 2}', b'{"a": NaN}', b'{"a": Infinity}', b"\xef\xbb\xbf{}", b"\xff\xfe", pytest.param(b"[" * 100000, id="zu-tief"),
                                 pytest.param(b'{"a": ' + b"9" * 5000 + b"}", id="riesige-zahl")])
def test_json_lesen_ungueltig(roh):
    _fehler(json_lesen, roh)


# ---------- Anfragekörper ----------
ANLEGEN = {"typ": "bug", "prioritaet": "hoch", "titel": "Titel", "beschreibung": "Text"}


def test_anlegen_gueltig_und_beschreibung_darf_leer_sein():
    assert pruefe_anlegen(ANLEGEN) == ANLEGEN
    assert pruefe_anlegen({**ANLEGEN, "beschreibung": ""})["beschreibung"] == ""


@pytest.mark.parametrize("daten", [
    None, [], "x", {}, {**ANLEGEN, "extra": 1}, {k: v for k, v in ANLEGEN.items() if k != "titel"}, {k: v for k, v in ANLEGEN.items() if k != "beschreibung"},
    {**ANLEGEN, "typ": "BUG"}, {**ANLEGEN, "typ": "fehler"}, {**ANLEGEN, "prioritaet": "dringend"}, {**ANLEGEN, "prioritaet": "Hoch"},
    {**ANLEGEN, "titel": "ab"}, {**ANLEGEN, "titel": "a\nb"}, {**ANLEGEN, "titel": 3}, {**ANLEGEN, "beschreibung": "x" * 1501}, {**ANLEGEN, "geloescht": True}])
def test_anlegen_ungueltig(daten):
    _fehler(pruefe_anlegen, daten)


def test_aendern_gueltig():
    r = pruefe_aendern({"erwartete_version": 3, "felder": {"titel": "Neu", "prioritaet": "hoechste", "typ": "story", "beschreibung": ""}})
    assert r == {"erwartete_version": 3, "felder": {"titel": "Neu", "prioritaet": "hoechste", "typ": "story", "beschreibung": ""}}


@pytest.mark.parametrize("daten", [
    {"felder": {"titel": "Neu"}}, {"erwartete_version": 3}, {"erwartete_version": 3, "felder": {}}, {"erwartete_version": 3, "felder": []},
    {"erwartete_version": 3, "felder": {"status": "erledigt"}}, {"erwartete_version": 3, "felder": {"nummer": "LAB-9"}},
    {"erwartete_version": 3, "felder": {"geloescht": True}}, {"erwartete_version": 3, "felder": {"titel": "Neu", "geloescht": True}},
    {"erwartete_version": 0, "felder": {"titel": "Neu"}}, {"erwartete_version": -1, "felder": {"titel": "Neu"}},
    {"erwartete_version": "3", "felder": {"titel": "Neu"}}, {"erwartete_version": True, "felder": {"titel": "Neu"}},
    {"erwartete_version": 3.0, "felder": {"titel": "Neu"}}, {"erwartete_version": 3, "felder": {"titel": "ab"}},
    {"erwartete_version": 3, "felder": {"typ": "x"}}, {"erwartete_version": 3, "felder": {"titel": "Neu"}, "extra": 1}, None])
def test_aendern_ungueltig(daten):
    _fehler(pruefe_aendern, daten)


def test_status_und_kommentar():
    assert pruefe_status({"erwartete_version": 1, "status": "in_arbeit"}) == {"erwartete_version": 1, "status": "in_arbeit"}
    assert pruefe_kommentar({"erwartete_version": 2, "text": "Ein\nKommentar"}) == {"erwartete_version": 2, "text": "Ein\nKommentar"}
    for daten in ({"erwartete_version": 1, "status": "offen"}, {"erwartete_version": 1}, {"status": "erledigt"},
                  {"erwartete_version": 1, "status": "erledigt", "geloescht": True}):
        _fehler(pruefe_status, daten)
    for daten in ({"erwartete_version": 1, "text": ""}, {"erwartete_version": 1, "text": "x" * 501}, {"erwartete_version": 1, "text": "a\rb"},
                  {"erwartete_version": 1, "text": "ok", "loeschen": True}, {"text": "ok"}):
        _fehler(pruefe_kommentar, daten)


# ---------- Abfrage von V2 ----------
def test_suche_standardwerte():
    assert parse_suche(b"") == Suche()
    s = Suche()
    assert (s.q, s.nummer, s.typ, s.prioritaet, s.status, s.nur_durchlauf, s.limit) == ("", None, None, None, None, False, 10)


def test_suche_alle_parameter():
    s = parse_suche(b"q=%25_&nummer=LAB-12&typ=bug&prioritaet=hoch&status=erledigt&nur_durchlauf=true&limit=5")
    assert s == Suche(q="%_", nummer=12, typ="bug", prioritaet="hoch", status="erledigt", nur_durchlauf=True, limit=5)


def test_suche_q_leer_heisst_kein_filter_und_ist_utf8():
    assert parse_suche(b"q=").q == ""
    assert parse_suche("q=Ärger".encode()).q == "Ärger"
    assert parse_suche(b"q=%C3%84rger").q == "Ärger"
    assert parse_suche(b"q=" + b"x" * 100).q == "x" * 100


@pytest.mark.parametrize("abfrage", [
    pytest.param(b"q=" + b"x" * 101, id="q-101"), b"foo=1", b"typ=bug&typ=story", b"q=a&q=b", b"typ=BUG", b"typ=", b"prioritaet=dringend", b"status=offen",
    b"nur_durchlauf=ja", b"nur_durchlauf=", b"nummer=LAB-012", b"nummer=LAB-0", b"nummer=LAB-10000", b"nummer=lab-12", b"nummer=",
    b"limit=0", b"limit=11", b"limit=abc", b"limit=-1", b"limit=", pytest.param(b"limit=" + b"9" * 6000, id="limit-riesig"), b"q=%FF", b"limit=%EF%BC%91", b"_method=DELETE"])
def test_suche_ungueltig(abfrage):
    _fehler(parse_suche, abfrage)
