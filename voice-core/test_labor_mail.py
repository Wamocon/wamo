"""Tests von mail_senden (labor_mail.py): reine Bestätigung "gesendet", Domänenfilter im Code, Textgrenzen. Es gibt keinen SMTP-Weg und keinen
Dienstaufruf, und die Prüfungen laufen vor der Karte und noch einmal beim Klick.
"""

from __future__ import annotations

import inspect
import re

import pytest

import labor_mail as m

D = "labor.test"


def pruefe(an, betreff="Hallo", text="Kurzer Text", domain=D):
    return m.pruefe_mail({"an": an, "betreff": betreff, "text": text}, domain)


def ok(ergebnis):
    werte, fehler = ergebnis
    assert fehler is None, fehler
    return werte


def fehler(ergebnis):
    werte, text = ergebnis
    assert werte is None and text, ergebnis
    return text


def test_gueltige_mail():
    assert ok(pruefe("anna.test@labor.test")) == {"an": "anna.test@labor.test", "betreff": "Hallo", "text": "Kurzer Text"}
    for an in ("a@labor.test", "a1@labor.test", "anna.test@labor.test", "anna-test@labor.test", "anna_test@labor.test", "anna+x@labor.test",
               "x" * 64 + "@labor.test"):
        assert ok(pruefe(an))["an"] == an


@pytest.mark.parametrize("an", [
    "info@kunde-beispiel.de",  # M2: fremde Domäne
    "a@labor.test.example", "a@xlabor.test", "a@labor.testx", "a@sub.labor.test", "a@labor.test.", "a@.labor.test", "a@labor..test",  # keine Unterdomäne, kein Punkt
    "a@labor.test@example.org", "a@example.org@labor.test", '"a@example.org"@labor.test', "a@@labor.test",
    "a@example.org,b@labor.test", "a@labor.test,b@labor.test", "a@labor.test b@labor.test", "a@labor.test;b@labor.test",  # mehr als ein Empfänger
    "Anna <a@labor.test>", "<a@labor.test>", "Anna Test (a@labor.test)", "a@labor.test (Anna)", '"Anna" <a@labor.test>',  # Name
    "A@labor.test", "a@LABOR.TEST", "a@Labor.test", "ANNA@LABOR.TEST",  # Großschreibung
    "a@labor.test\n", "a@labor.test\r\nBcc: x@example.org", "a@labor.test\r\n", "a\n@labor.test", "a@labor.test\x00", "\ta@labor.test",  # Zeilenumbruch, Steuerzeichen
    " a@labor.test", "a@labor.test ", "a @labor.test", "a@ labor.test",
    "ä@labor.test", "a@lаbor.test", "a@labor.test\u202e", "a\u200b@labor.test", "a＠labor.test", "a@labor。test", "a@ｌabor.test",  # Unicode
    "a@xn--labor-test.example", "a@xn--abor-8ra.test", "xn--a@labor.test", "a@xn--labor.test",  # Punycode
    "@labor.test", "a@", "a", "", "labor.test", "a@labor", ".a@labor.test", "a.@labor.test", "a..b@labor.test", "-a@labor.test", "a b@labor.test",
    "x" * 65 + "@labor.test", "a%40example.org@labor.test", "a!b@labor.test", "a/b@labor.test", "a\\b@labor.test",
    None, 5, ["a@labor.test"], {"a": 1}, True,
])
def test_adressen_ausserhalb_der_domaene_oder_in_falscher_form_werden_abgelehnt(an):
    fehler(pruefe(an))


def test_domain_aus_der_umgebung(monkeypatch):
    monkeypatch.delenv("WAMO_LABOR_MAIL_DOMAIN", raising=False)
    assert m.erlaubte_domain() == "labor.test"
    monkeypatch.setenv("WAMO_LABOR_MAIL_DOMAIN", "uebung.test")
    assert m.erlaubte_domain() == "uebung.test"
    ok(pruefe("a@uebung.test", domain=m.erlaubte_domain()))
    fehler(pruefe("a@labor.test", domain=m.erlaubte_domain()))


@pytest.mark.parametrize("kaputt", ["", "Labor.Test", "labor", "labor.test.", ".labor.test", "lab or.test", "labor.test\n", "labor..test", "ä.test",
                                    "xn--labor.test", "labor.test,x.test", "a@labor.test", "labor.test/x", "-labor.test", "labor-.test", "x" * 300 + ".test"])
def test_ungueltige_einstellung_erlaubt_keine_mail(monkeypatch, kaputt):
    monkeypatch.setenv("WAMO_LABOR_MAIL_DOMAIN", kaputt)
    domain = m.erlaubte_domain()
    if kaputt == "":
        assert domain == "labor.test"  # leer heißt nicht gesetzt
        return
    assert domain is None  # fail-closed: eine kaputte Einstellung ist nie eine offene
    text = fehler(m.pruefe_mail({"an": "a@labor.test", "betreff": "B", "text": "T"}, domain))
    assert "nicht eingerichtet" in text


def test_betreff_und_text_grenzen():
    assert ok(pruefe("a@labor.test", betreff="x" * 120, text="y" * 1500))
    for betreff in ("", "   ", "x" * 121, None, 5, ["a"]):
        fehler(pruefe("a@labor.test", betreff=betreff))
    for text in ("", "   ", "y" * 1501, None, 5, ["a"]):
        fehler(pruefe("a@labor.test", text=text))  # Mail-Text höchstens 1500 Zeichen


def test_betreff_ist_eine_zeile_und_der_text_normiert():
    w = ok(pruefe("a@labor.test", betreff="Hallo\r\nBcc: x@example.org\u202e", text="Zeile1\r\nZeile2\u200b"))
    assert w["betreff"] == "Hallo Bcc: x@example.org" and "\n" not in w["betreff"] and "\r" not in w["betreff"]
    assert w["text"] == "Zeile1\nZeile2"


def test_unbekannte_felder_werden_nicht_uebernommen():
    w = ok(m.pruefe_mail({"an": "a@labor.test", "betreff": "B", "text": "T", "cc": "x@example.org", "bcc": "y@example.org", "anhang": "x"}, D))
    assert set(w) == {"an", "betreff", "text"}


def test_bestaetigung_prueft_noch_einmal_und_kennt_keinen_dienst():
    werte = {"an": "anna.test@labor.test", "betreff": "Hallo", "text": "Kurzer Text"}
    a = m.bestaetigen(werte, D)
    assert a.ok is True and a.satz == "Mail an anna.test@labor.test gesendet, es ist nichts nach außen gegangen."
    # beim Klick wird der Filter noch einmal angewendet: ein manipulierter gespeicherter Wert wird nicht bestätigt
    for kaputt in ({**werte, "an": "x@example.org"}, {**werte, "an": "a@labor.test\nBcc: x@example.org"}, {**werte, "text": "y" * 1501}, {"an": "a@labor.test"}):
        b = m.bestaetigen(kaputt, D)
        assert b.ok is False and "example.org" not in b.satz
    assert m.bestaetigen(werte, None).ok is False


def test_kein_netz_kein_smtp_kein_dienstaufruf_im_modul():
    quelle = inspect.getsource(m)
    for verboten in ("smtplib", "import socket", "httpx", "aiosmtplib", "requests", "urllib.request", "labor_client", "subprocess", "sendmail"):
        assert verboten not in quelle, verboten
    assert not re.search(r"\bimport\s+(smtplib|socket|httpx|requests|aiohttp|http\.client)\b", quelle)


def test_schema_nennt_klar_dass_es_nur_eine_bestaetigung_ist():
    s = m.mail_schema()
    assert s.name == "mail_senden" and set(s.properties) == {"an", "betreff", "text"} and set(s.required) == {"an", "betreff", "text"}
    assert "Freigabe" in s.description and "nichts" in s.description
    assert "—" not in s.description and "–" not in s.description
