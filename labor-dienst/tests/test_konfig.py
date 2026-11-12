"""Konfiguration nur über Umgebungsvariablen und Dateien (Auftrag Punkt 7)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from wamo_labor_dienst.konfig import KonfigFehler, Konfig, aus_umgebung, token_aus_datei

KERN = "K" + "a1-_." * 8 + "kern"
BETREUER = "B" + "z9~+=" * 8 + "betreuer"


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    kern, betreuer = tmp_path / "kern.token", tmp_path / "betreuer.token"
    kern.write_text(KERN + "\n", encoding="utf-8")
    betreuer.write_text(BETREUER, encoding="utf-8")
    return {"WAMO_LABOR_DIENST_KERN_TOKEN_DATEI": str(kern), "WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI": str(betreuer), **extra}


def test_standardwerte(tmp_path):
    k = aus_umgebung(_env(tmp_path))
    assert isinstance(k, Konfig)
    assert k.daten == Path("/data")
    assert k.host == "127.0.0.1"
    assert k.port == 8080
    assert k.bis == date(2026, 12, 11)
    assert k.platz_mb == 16
    assert k.kern_token == KERN
    assert k.betreuer_token == BETREUER


def test_werte_aus_der_umgebung(tmp_path):
    k = aus_umgebung(_env(tmp_path, WAMO_LABOR_DIENST_DATEN=str(tmp_path / "d"), WAMO_LABOR_DIENST_HOST="0.0.0.0",
                          WAMO_LABOR_DIENST_PORT="9100", WAMO_LABOR_DIENST_BIS="2026-12-01", WAMO_LABOR_DIENST_PLATZ_MB="8"))
    assert (k.daten, k.host, k.port, k.bis, k.platz_mb) == (tmp_path / "d", "0.0.0.0", 9100, date(2026, 12, 1), 8)


def test_token_aus_datei_streift_leerraum_und_bom(tmp_path):
    datei = tmp_path / "t"
    datei.write_bytes(b"\xef\xbb\xbf" + KERN.encode() + b"\r\n")
    assert token_aus_datei(str(datei), "X") == KERN


@pytest.mark.parametrize("fehlend", ["WAMO_LABOR_DIENST_KERN_TOKEN_DATEI", "WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI"])
def test_fehlende_tokendatei_nennt_die_variable(tmp_path, fehlend):
    env = _env(tmp_path)
    del env[fehlend]
    with pytest.raises(KonfigFehler, match=fehlend):
        aus_umgebung(env)


def test_nicht_lesbare_tokendatei(tmp_path):
    env = _env(tmp_path, WAMO_LABOR_DIENST_KERN_TOKEN_DATEI=str(tmp_path / "gibt-es-nicht"))
    with pytest.raises(KonfigFehler, match="WAMO_LABOR_DIENST_KERN_TOKEN_DATEI"):
        aus_umgebung(env)


@pytest.mark.parametrize("inhalt", ["", "zu-kurz", "a" * 31, "a" * 201, "mit leerzeichen " + "a" * 40, "umlaut-ä" + "a" * 40])
def test_unbrauchbarer_token_wird_abgelehnt_ohne_den_wert_zu_nennen(tmp_path, inhalt):
    env = _env(tmp_path)
    Path(env["WAMO_LABOR_DIENST_KERN_TOKEN_DATEI"]).write_text(inhalt, encoding="utf-8")
    with pytest.raises(KonfigFehler) as e:
        aus_umgebung(env)
    assert inhalt.strip() == "" or inhalt.strip() not in str(e.value)


def test_gleicher_token_fuer_kern_und_betreuer_wird_abgelehnt(tmp_path):
    env = _env(tmp_path)
    Path(env["WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI"]).write_text(KERN, encoding="utf-8")
    with pytest.raises(KonfigFehler, match="verschieden"):
        aus_umgebung(env)


@pytest.mark.parametrize("name,wert", [("WAMO_LABOR_DIENST_PORT", "0"), ("WAMO_LABOR_DIENST_PORT", "70000"), ("WAMO_LABOR_DIENST_PORT", "abc"),
                                       ("WAMO_LABOR_DIENST_BIS", "11.12.2026"), ("WAMO_LABOR_DIENST_BIS", "2026-13-01"),
                                       ("WAMO_LABOR_DIENST_PLATZ_MB", "1"), ("WAMO_LABOR_DIENST_PLATZ_MB", "65"), ("WAMO_LABOR_DIENST_PLATZ_MB", "x"),
                                       ("WAMO_LABOR_DIENST_HOST", "")])
def test_ungueltige_werte_nennen_die_variable(tmp_path, name, wert):
    with pytest.raises(KonfigFehler, match=name):
        aus_umgebung(_env(tmp_path, **{name: wert}))


def test_repr_verraet_keinen_token(tmp_path):
    k = aus_umgebung(_env(tmp_path))
    assert KERN not in repr(k) and BETREUER not in repr(k)
    assert KERN not in str(k)
