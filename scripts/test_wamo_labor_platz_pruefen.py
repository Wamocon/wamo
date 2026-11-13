"""Tests für `wamo_labor_platz.py pruefen`: Zertifikat, Richtlinien, Chrome, Adressen mit TLS gegen die CA, Uhr, Mikrofon.

Kein Netz: Der Abruf ist eine Attrappe. Die Eingabegeräte stehen in der Attrappe der Registrierung (nur lesend gelesen).
"""
from __future__ import annotations

import json
import sys
from email.utils import formatdate
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_platz_attrappe as at  # noqa: E402
import wamo_labor_platz_testwelt as tw  # noqa: E402
from wamo_labor_platz_umgebung import Abruf, AbrufFehler  # noqa: E402

MM = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\MMDevices\\Audio\\Capture"
BESCHR, SCHNITT = "{a45c254e-df1c-4efd-8020-67d146a850e0},2", "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
FESTE = ("https://192.168.178.136:55443/auth/v1/health", "https://192.168.178.136:55444/", "https://192.168.178.136:55445/",
         "https://192.168.178.136:55447/board")


@pytest.fixture(autouse=True)
def _fallen(monkeypatch, tmp_path):
    tw.fallen_stellen(monkeypatch, tmp_path / "echt")


def _mikrofon(reg, kennung: str, kurz: str, schnittstelle: str, zustand: int = 1) -> None:
    reg.vorbelegen("HKLM", f"{MM}\\{kennung}", {"DeviceState": ("dword", zustand)})
    reg.vorbelegen("HKLM", f"{MM}\\{kennung}\\Properties", {BESCHR: ("sz", kurz), SCHNITT: ("sz", schnittstelle)})


def _datum(abweichung_s: float = 0.0) -> str:
    return formatdate(tw.JETZT + abweichung_s, usegmt=True)


@pytest.fixture
def welt(tmp_path):
    w = tw.Welt(tmp_path / "w")
    assert w.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    w.umg.abruf = at.AbrufAttrappe(Abruf(200, _datum()))
    _mikrofon(w.reg, "{1}", "Mikrofon", "USB-Headset Modell A")
    w.zeilen.clear()
    return w


def _stufen(welt) -> list[str]:
    return [z.split("]")[0].lstrip("[") for z in welt.zeilen if z.startswith("[")]


def test_alles_in_ordnung(welt):
    assert welt.lauf("pruefen") == 0
    assert set(_stufen(welt)) == {"ok"} and len(_stufen(welt)) >= 9
    for thema in ("Zertifikat", "Richtlinien", "Chrome", "Uhr", "Mikrofon", "Platz"):
        assert any(thema in z for z in welt.zeilen), thema


def test_adressen_nur_die_festen_und_mit_ca_datei(welt):
    welt.lauf("pruefen")
    import wamo_labor_platz_adressen as adr
    urls = [u for u, _ in welt.umg.abruf.aufrufe]
    assert sorted(urls) == sorted(FESTE) and all(adr.adresse_zulaessig(u) for u in urls)
    assert {ca for _, ca in welt.umg.abruf.aufrufe} == {str(welt.ca)}


def test_pruefen_aendert_nichts(welt):
    dateien, reg, zert = welt.dateien(), welt.reg.momentaufnahme(), set(welt.zert.speicher)
    schreib, aenderungen = welt.reg.schreibzugriffe, welt.zert.aenderungen
    welt.lauf("pruefen")
    assert welt.dateien() == dateien and welt.reg.momentaufnahme() == reg and welt.zert.speicher == zert
    assert welt.reg.schreibzugriffe == schreib and welt.zert.aenderungen == aenderungen and welt.prozesse.aufrufe == []


def test_veraenderte_platzdatei_wird_gemeldet_und_nie_abgerufen(welt):
    daten = json.loads(welt.platz_datei.read_text(encoding="utf-8"))
    daten["board_url"] = "https://boese.example:55447/board"
    welt.platz_datei.write_text(json.dumps(daten), encoding="utf-8")
    assert welt.lauf("pruefen") == 1 and "verändert" in welt.text
    assert not any("boese" in u for u, _ in welt.umg.abruf.aufrufe)


def test_ohne_einrichten_wird_gemeldet(welt):
    welt.lauf("rueckweg")
    welt.zeilen.clear()
    assert welt.lauf("pruefen") == 1 and "nicht eingerichtet" in welt.text


# ---------- Adressen und TLS ----------

def test_tls_fehler_ist_ein_fehler(welt):
    welt.umg.abruf.antworten[FESTE[2]] = AbrufFehler("tls", "Zertifikat wird nicht als vertrauenswürdig erkannt")
    assert welt.lauf("pruefen") == 1
    zeile = next(z for z in welt.zeilen if "55445" in z)
    assert zeile.startswith("[FEHLER]") and "TLS" in zeile


def test_nicht_erreichbar_ist_ein_fehler_und_die_uhr_bleibt_ungeprueft(welt):
    welt.umg.abruf.standard = AbrufFehler("verbindung", "keine Verbindung")
    assert welt.lauf("pruefen") == 1
    assert any(z.startswith("[WARNUNG]") and "Uhr" in z for z in welt.zeilen)


@pytest.mark.parametrize("status,stufe", [(200, "ok"), (401, "ok"), (404, "ok"), (503, "WARNUNG")])
def test_jede_antwort_zeigt_erreichbarkeit_nur_serverfehler_warnen(welt, status, stufe):
    welt.umg.abruf.antworten[FESTE[3]] = Abruf(status, _datum())
    assert welt.lauf("pruefen") == 0
    assert next(z for z in welt.zeilen if "55447" in z).startswith(f"[{stufe}]")


def test_fehlende_ca_datei_verhindert_die_adresspruefung(welt):
    welt.ca.unlink()
    assert welt.lauf("pruefen") == 1 and "CA-Datei" in welt.text
    assert welt.umg.abruf.aufrufe == []


# ---------- Uhr ----------

@pytest.mark.parametrize("delta,rc", [(0, 0), (299, 0), (-299, 0), (300, 0), (301, 1), (-301, 1), (3600, 1), (-86400, 1)])
def test_uhr_hoechstens_fuenf_minuten_abweichung(welt, delta, rc):
    welt.umg.abruf.standard = Abruf(200, _datum(delta))
    assert welt.lauf("pruefen") == rc
    if rc:
        assert any(z.startswith("[FEHLER]") and "Uhr" in z for z in welt.zeilen)


def test_uhr_ohne_date_kopf_ist_nur_eine_warnung(welt):
    welt.umg.abruf.standard = Abruf(200, None)
    assert welt.lauf("pruefen") == 0
    assert any(z.startswith("[WARNUNG]") and "Uhr" in z for z in welt.zeilen)


def test_uhr_mit_unlesbarem_date_kopf_ist_nur_eine_warnung(welt):
    welt.umg.abruf.standard = Abruf(200, "gestern gegen Abend")
    assert welt.lauf("pruefen") == 0 and any(z.startswith("[WARNUNG]") and "Uhr" in z for z in welt.zeilen)


# ---------- Mikrofon ----------

def test_bluetooth_freisprechgeraet_wird_gewarnt(welt):
    _mikrofon(welt.reg, "{2}", "Headset", "WH-1000 Hands-Free AG Audio")
    assert welt.lauf("pruefen") == 0
    zeile = next(z for z in welt.zeilen if "Hands-Free" in z and z.startswith("[WARNUNG]"))
    assert "Bluetooth" in zeile and "Freisprech" in zeile and "USB" in zeile and "Klinke" in zeile


@pytest.mark.parametrize("schnittstelle", ["Kopfhörer BTHHFEnum", "Bluetooth Headset X", "Freisprechtelefon Y", "Handsfree Z"])
def test_bluetooth_erkennung_ueber_mehrere_namen(welt, schnittstelle):
    _mikrofon(welt.reg, "{2}", "Headset", schnittstelle)
    welt.lauf("pruefen")
    assert any(z.startswith("[WARNUNG]") and "Bluetooth" in z for z in welt.zeilen)


def test_deaktiviertes_oder_getrenntes_bluetooth_geraet_wird_ignoriert(welt):
    _mikrofon(welt.reg, "{2}", "Headset", "WH-1000 Hands-Free AG Audio", zustand=2)
    _mikrofon(welt.reg, "{3}", "Headset", "WH-2000 Hands-Free AG Audio", zustand=8)
    welt.lauf("pruefen")
    assert not any("Bluetooth" in z for z in welt.zeilen) and "Hands-Free" not in welt.text


def test_nur_usb_mikrofon_ist_in_ordnung_und_wird_genannt(welt):
    welt.lauf("pruefen")
    assert any(z.startswith("[ok]") and "Mikrofon (USB-Headset Modell A)" in z for z in welt.zeilen)


def test_kein_aktives_mikrofon_ist_eine_warnung(tmp_path):
    w = tw.Welt(tmp_path / "w")
    w.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    w.umg.abruf = at.AbrufAttrappe(Abruf(200, _datum()))
    w.zeilen.clear()
    assert w.lauf("pruefen") == 0
    assert any(z.startswith("[WARNUNG]") and "Mikrofon" in z and "kein" in z.lower() for z in w.zeilen)


# ---------- Zertifikat, Richtlinien, Chrome ----------

def test_fehlendes_zertifikat_richtlinien_und_chrome_sind_fehler(welt):
    welt.zert.speicher.clear()
    welt.reg.setzen("HKCU", "Software\\Policies\\Google\\Chrome", "TranslateEnabled", "dword", 1)
    welt.chrome.unlink()
    assert welt.lauf("pruefen") == 1
    for thema in ("Zertifikat", "Richtlinien", "Chrome"):
        assert any(z.startswith("[FEHLER]") and thema in z for z in welt.zeilen), thema
    assert any("TranslateEnabled" in z for z in welt.zeilen)


def test_warten_wartet_bei_pruefen_immer_auf_enter(welt):
    welt.lauf("pruefen", "--warten")
    assert welt.tasten == [1]
    welt.lauf("pruefen")
    assert welt.tasten == [1]


def test_starten_mit_warten_wartet_nur_bei_fehlern(welt):
    welt.lauf("starten", "--warten", "--ohne-board")
    assert welt.tasten == []
    welt.chrome.unlink()
    assert welt.lauf("starten", "--warten") == 1 and welt.tasten == [1]
