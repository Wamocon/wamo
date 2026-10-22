"""Tests der Verschlüsselung eigener Claude-Schlüssel (schluessel.py): AES-256-GCM mit Profil-ID als AAD,
Wechsel des Hauptschlüssels, Formatprüfung und Schwärzen. Ohne Netz und ohne Datenbank."""

from __future__ import annotations

import base64

import pytest

import schluessel as s

PROFIL_A = "0f6c2b1e-5d1a-4c3e-9a7b-2e8d4f6a1b3c"
PROFIL_B = "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e"
SCHLUESSEL = "sk-ant-api03-ABCdef_ghi-0123456789jklmnopq-WXyz"


def _tresor(alte=()):
    return s.Tresor(base64.urlsafe_b64decode(s.kek_erzeugen()), alte)


def test_rundreise_und_kein_klartext_im_chiffrat():
    t = _tresor()
    chiffre = t.verschluesseln(PROFIL_A, SCHLUESSEL)
    assert chiffre.startswith(s.KOPF)
    assert SCHLUESSEL.encode() not in chiffre and b"sk-ant-" not in chiffre
    assert t.entschluesseln(PROFIL_A, chiffre) == SCHLUESSEL
    assert t.entschluesseln(PROFIL_A, memoryview(chiffre)) == SCHLUESSEL  # asyncpg liefert bytes, nie str


def test_jedes_chiffrat_ist_anders():
    t = _tresor()
    assert t.verschluesseln(PROFIL_A, SCHLUESSEL) != t.verschluesseln(PROFIL_A, SCHLUESSEL)  # neue Nonce


def test_chiffrat_eines_anderen_profils_nicht_lesbar():
    """Wer Schreibrecht auf die Tabelle hat, kann ein Chiffrat nicht in die Zeile eines anderen Profils kopieren."""
    t = _tresor()
    chiffre = t.verschluesseln(PROFIL_A, SCHLUESSEL)
    with pytest.raises(s.SchluesselFehler, match="anderen Profil"):
        t.entschluesseln(PROFIL_B, chiffre)


def test_falscher_hauptschluessel_und_wechsel():
    alt = _tresor()
    chiffre = alt.verschluesseln(PROFIL_A, SCHLUESSEL)
    neu = _tresor()
    with pytest.raises(s.SchluesselFehler, match="unbekannten Hauptschlüssel"):
        neu.entschluesseln(PROFIL_A, chiffre)
    gewechselt = s.Tresor(neu._aktuell, (alt._aktuell,))
    assert gewechselt.entschluesseln(PROFIL_A, chiffre) == SCHLUESSEL
    assert gewechselt.verschluesseln(PROFIL_A, SCHLUESSEL)[2:6] == bytes.fromhex(neu.kennung)  # neu immer aktuell


@pytest.mark.parametrize("daten", [b"", b"w1", b"xx" + bytes(40), b"w1" + bytes(10)])
def test_kaputtes_chiffrat(daten):
    with pytest.raises(s.SchluesselFehler):
        _tresor().entschluesseln(PROFIL_A, daten)


def test_manipuliertes_chiffrat():
    t = _tresor()
    chiffre = bytearray(t.verschluesseln(PROFIL_A, SCHLUESSEL))
    chiffre[-1] ^= 1
    with pytest.raises(s.SchluesselFehler, match="beschädigt"):
        t.entschluesseln(PROFIL_A, bytes(chiffre))


def test_profil_id_muss_uuid_sein():
    with pytest.raises(s.SchluesselFehler, match="UUID"):
        _tresor().verschluesseln("betreiber", SCHLUESSEL)


def test_from_env(monkeypatch):
    monkeypatch.delenv(s.KEK_ENV, raising=False)
    assert s.Tresor.from_env() is None
    kek = s.kek_erzeugen()
    monkeypatch.setenv(s.KEK_ENV, kek)
    t = s.Tresor.from_env()
    assert t is not None and len(t.kennung) == 8
    monkeypatch.setenv(s.KEK_ALT_ENV, f"{s.kek_erzeugen()}, ")
    assert len(s.Tresor.from_env()._alle) == 2


@pytest.mark.parametrize("wert,text", [
    ("kein-base64-%%%", "Base64"),
    (base64.urlsafe_b64encode(bytes(16)).decode(), "32 Byte"),
])
def test_from_env_unbrauchbar_ohne_wert_in_meldung(monkeypatch, wert, text):
    monkeypatch.setenv(s.KEK_ENV, wert)
    with pytest.raises(s.SchluesselFehler, match=text) as info:
        s.Tresor.from_env()
    assert wert not in str(info.value)


def test_kek_erzeugen():
    a, b = s.kek_erzeugen(), s.kek_erzeugen()
    assert a != b and len(base64.urlsafe_b64decode(a)) == 32


@pytest.mark.parametrize("roh", [SCHLUESSEL, f"  {SCHLUESSEL}\n"])
def test_format_gueltig(roh):
    assert s.format_pruefen(roh) == SCHLUESSEL


@pytest.mark.parametrize("roh", [None, "", "sk-ant-kurz", "sk-proj-0123456789abcdefghijklmn", SCHLUESSEL + " x",
                                 "sk-ant-" + "a" * 300, f"{SCHLUESSEL}\n{SCHLUESSEL}"])
def test_format_ungueltig_ohne_schluessel_in_meldung(roh):
    with pytest.raises(s.SchluesselFehler) as info:
        s.format_pruefen(roh)
    assert "sk-ant-api03" not in str(info.value)


def test_ende_und_schwaerzen():
    assert s.ende(SCHLUESSEL) == "WXyz"
    text = f"Fehler mit {SCHLUESSEL} und Teil sk-ant-api03-ABC sowie sk-ant-kurz"
    sauber = s.schwaerzen(text)
    assert SCHLUESSEL not in sauber and "sk-ant-api03-ABC" not in sauber
    assert sauber.count("sk-ant-…") == 2 and "sk-ant-kurz" in sauber  # unter acht Zeichen kein Schlüssel
    assert s.schwaerzen("nichts") == "nichts"
