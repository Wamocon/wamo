"""Tests für scripts/wamo_labor_platz.py (Platz-Skript der Windows-Laptops des Testlabors): Adressen, Richtlinien, einrichten, rueckweg, status.

Aufruf (aus scripts, mit der Python-Umgebung von voice-core):
  D:\\IDEA\\Projekt\\wamo\\voice-core\\.venv\\Scripts\\python.exe -m pytest -q

Die Tests ändern nie den echten Rechner: Registrierung und Zertifikatsspeicher sind Attrappen, Prozesse werden nur mitgeschrieben, und
Fallen (wamo_labor_platz_testwelt.fallen_stellen) lösen einen Fehler aus, wenn doch etwas Echtes angefasst würde.
Die Tests für starten und pruefen stehen in test_wamo_labor_platz_start.py und test_wamo_labor_platz_pruefen.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_platz_testwelt as tw  # noqa: E402

# Feste Liste der Richtlinien, die es für Chrome gibt (Quelle: Richtlinienliste von Chrome Enterprise). Ein Schlüssel außerhalb
# dieser Liste würde von Chrome ignoriert und wäre ein Tippfehler.
GUELTIGE_CHROME_SCHLUESSEL = frozenset({
    "TranslateEnabled", "PasswordManagerEnabled", "BrowserSignin", "SyncDisabled", "HomepageLocation", "HomepageIsNewTabPage",
    "NewTabPageLocation", "RestoreOnStartup", "RestoreOnStartupURLs", "ShowHomeButton", "URLBlocklist", "URLAllowlist",
    "AudioCaptureAllowed", "AudioCaptureAllowedUrls", "VideoCaptureAllowed", "ScreenCaptureAllowed", "DefaultPopupsSetting",
    "DeveloperToolsAvailability", "DownloadRestrictions", "IncognitoModeAvailability", "BrowserGuestModeEnabled",
    "BrowserAddPersonEnabled", "AutofillAddressEnabled", "AutofillCreditCardEnabled", "DefaultGeolocationSetting",
    "DefaultNotificationsSetting", "DefaultWebUsbGuardSetting", "DefaultSerialGuardSetting", "DefaultWebBluetoothGuardSetting",
    "SavingBrowserHistoryDisabled", "MetricsReportingEnabled", "DefaultBrowserSettingEnabled", "ExtensionInstallBlocklist",
    "BackgroundModeEnabled", "HardwareAccelerationModeEnabled",
})
CHROME_PFAD = "Software\\Policies\\Google\\Chrome"


@pytest.fixture(autouse=True)
def _fallen(monkeypatch, tmp_path):
    tw.fallen_stellen(monkeypatch, tmp_path / "echt")


@pytest.fixture
def welt(tmp_path):
    return tw.Welt(tmp_path / "w")


def _regmod():
    import wamo_labor_platz_registrierung as regmod
    return regmod


# ---------- Adressen ----------

@pytest.mark.parametrize("url", [
    "https://192.168.178.136:55445", "https://192.168.178.136:55445/", "https://192.168.178.136:55447/board",
    "https://192.168.178.136:55443/auth/v1/health", "https://192.168.178.136:55444/",
])
def test_feste_adressen_sind_zulaessig(url):
    import wamo_labor_platz_adressen as adr
    assert adr.adresse_zulaessig(url) is True


@pytest.mark.parametrize("url", [
    "http://192.168.178.136:55445", "https://192.168.178.136:55449", "https://192.168.178.136", "https://example.com:55445",
    "https://192.168.178.136.evil.test:55445", "https://user@192.168.178.136:55445", "https://192.168.178.136:55445@evil.test",
    "https://192.168.178.136:55447/board?x=1", "https://192.168.178.136:55447/board#t=abc", "https://192.168.178.136:55445\\@evil",
    "https://192.168.178.136:55445/ x", "ftp://192.168.178.136:55445", "https://192.168.178.136:55447/anderer-pfad",
    "https://192.168.178.136:55444", "HTTPS://192.168.178.136:55445", "https://192.168.178.136:55445/x", "", "   ", None, 55445, b"https://192.168.178.136:55445",
])
def test_fremde_adressen_sind_unzulaessig(url):
    import wamo_labor_platz_adressen as adr
    assert adr.adresse_zulaessig(url) is False


def test_token_muster():
    import wamo_labor_platz_adressen as adr
    assert adr.token_gueltig(tw.TOKEN) is True
    for schlecht in ("", tw.TOKEN[:-1], tw.TOKEN + "0", tw.TOKEN.upper(), "g" * 32, tw.TOKEN + "&x=1", None, 12):
        assert adr.token_gueltig(schlecht) is False


# ---------- Richtlinien ----------

def test_richtlinien_nur_gueltige_chrome_schluessel():
    namen = [r.name for r in _regmod().richtlinien()]
    assert set(namen) <= GUELTIGE_CHROME_SCHLUESSEL
    assert len(namen) == len(set(namen))


def test_richtlinien_enthalten_die_geforderten_einstellungen():
    r = {x.name: (x.typ, x.wert) for x in _regmod().richtlinien()}
    erwartet = {
        "TranslateEnabled": ("dword", 0), "PasswordManagerEnabled": ("dword", 0), "BrowserSignin": ("dword", 0),
        "SyncDisabled": ("dword", 1), "VideoCaptureAllowed": ("dword", 0), "ScreenCaptureAllowed": ("dword", 0),
        "DefaultPopupsSetting": ("dword", 2), "DeveloperToolsAvailability": ("dword", 2), "DownloadRestrictions": ("dword", 3),
        "IncognitoModeAvailability": ("dword", 1), "HomepageLocation": ("sz", "https://192.168.178.136:55445"),
    }
    for name, wert in erwartet.items():
        assert r[name] == wert, name
    assert r["AudioCaptureAllowedUrls"] == ("liste", ("https://192.168.178.136:55445",))
    assert r["URLBlocklist"] == ("liste", ("*",))


def test_richtlinien_listen_nennen_nur_feste_herkuenfte():
    import wamo_labor_platz_adressen as adr
    r = {x.name: x for x in _regmod().richtlinien()}
    erlaubt = set(adr.HERKUENFTE) | {adr.WEITER_ADRESSE}
    assert set(r["URLAllowlist"].wert) == erlaubt
    assert set(r["AudioCaptureAllowedUrls"].wert) <= set(adr.HERKUENFTE)
    for liste in ("RestoreOnStartupURLs",):
        assert all(adr.adresse_zulaessig(u) for u in r[liste].wert)


# ---------- einrichten ----------

def test_trockenlauf_zeigt_den_plan_und_aendert_nichts(welt):
    vorher_reg, vorher_dateien = welt.reg.momentaufnahme(), welt.dateien()
    assert welt.lauf("einrichten", "--trocken", "--platz", "3") == 0
    assert welt.reg.momentaufnahme() == vorher_reg and welt.reg.schreibzugriffe == 0
    assert welt.dateien() == vorher_dateien
    assert not welt.lokal.exists() and not welt.desktop.exists()
    assert welt.zert.aenderungen == 0 and all(a[1] == "-store" for a in welt.zert.aufrufe)
    assert welt.lnk.aufrufe == []
    fp = " ".join(tw.SHA1[i:i + 8] for i in range(0, 40, 8)).upper()
    for muster in ("Trockenlauf", "Platz 3", fp, "TranslateEnabled", "platz.json", "WAMO Labor starten"):
        assert muster in welt.text


def test_plan_warnt_vor_dem_eigenen_konto_und_fehler_zeigen_die_ursache(welt):
    welt.lauf("einrichten", "--trocken", "--platz", "3")
    assert "alle Chrome-Profile" in welt.text and "Kiosk-Konto" in welt.text
    welt.zeilen.clear()

    def kaputt(*_a, **_k):
        raise PermissionError("Zugriff verweigert")
    welt.reg.setzen = kaputt
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 1
    assert "PermissionError" in welt.text and "Zugriff verweigert" in welt.text


def test_einrichten_setzt_alles(welt):
    import wamo_labor_platz_registrierung as regmod
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    for r in regmod.richtlinien():
        assert regmod.lese_zustand(welt.reg, r) == (r.typ, r.wert), r.name
    assert tw.SHA1 in welt.zert.speicher
    daten = json.loads(welt.platz_datei.read_text(encoding="utf-8"))
    assert daten["platz"] == 3 and daten["board_url"].endswith(":55447/board") and daten["wamo_url"].endswith(":55445")
    assert daten["ca_sha1"] == tw.SHA1
    assert welt.profil.is_dir()
    assert all(p.exists() for p in welt.verknuepfungen)
    ziel, argumente, _ = welt.verknuepfungen[0].read_text(encoding="utf-8").splitlines()
    assert ziel.endswith("python.exe") and "wamo_labor_platz.py" in argumente and argumente.endswith("starten --warten")
    assert welt.sicherung.exists()


def test_sicherung_liegt_vor_jeder_aenderung(welt):
    stand: list[bool] = []
    welt.reg.vor_schreiben = lambda: stand.append(welt.sicherung.exists())
    welt.zert.vor_aenderung = lambda: stand.append(welt.sicherung.exists())
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    assert stand and all(stand)


def test_einrichten_ist_idempotent(welt):
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    dateien, schreib, zert, lnk = welt.dateien(), welt.reg.schreibzugriffe, welt.zert.aenderungen, len(welt.lnk.aufrufe)
    welt.zeilen.clear()
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    assert welt.reg.schreibzugriffe == schreib and welt.zert.aenderungen == zert and len(welt.lnk.aufrufe) == lnk
    assert welt.dateien() == dateien
    assert "nichts zu ändern" in welt.text


def test_platz_ist_pflicht_und_wird_geprueft(welt):
    assert welt.lauf("einrichten", "--kiosk-konto") == 2 and "Platznummer" in welt.text
    for schlecht in ("0", "17", "-1", "abc", "3.5", ""):
        assert welt.lauf("einrichten", "--kiosk-konto", "--platz", schlecht) == 2, schlecht
    assert welt.reg.schreibzugriffe == 0 and not welt.lokal.exists()


def test_platz_wird_aus_der_platzdatei_uebernommen_und_darf_wechseln(welt):
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    assert welt.lauf("einrichten", "--kiosk-konto") == 0
    assert json.loads(welt.platz_datei.read_text(encoding="utf-8"))["platz"] == 3
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "5") == 0
    assert json.loads(welt.platz_datei.read_text(encoding="utf-8"))["platz"] == 5


def test_platzdatei_enthaelt_keinen_token(welt):
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    inhalt = welt.platz_datei.read_text(encoding="utf-8")
    assert "token" not in inhalt.lower() and "#t=" not in inhalt


def test_ca_wird_nur_im_benutzerspeicher_importiert(welt):
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    importe = [a for a in welt.zert.aufrufe if a[1] == "-addstore"]
    kopie = str(welt.umg.platz_ordner / "wamo-ca.crt")
    assert len(importe) == 1 and importe[0][:3] == ["-user", "-addstore", "Root"] and importe[0][3] == kopie
    assert all(a[0] == "-user" and "Root" in a for a in welt.zert.aufrufe)


def test_ca_datei_ueber_option_hat_vorrang(welt):
    andere = welt.tmp / "andere.crt"
    andere.write_text(welt.ca.read_text(encoding="ascii"), encoding="ascii", newline="\n")
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3", "--ca-datei", str(andere))
    assert [a[3] for a in welt.zert.aufrufe if a[1] == "-addstore"] == [str(welt.umg.platz_ordner / "wamo-ca.crt")]
    assert tw.SHA1 in welt.zert.speicher


@pytest.mark.parametrize("inhalt", [None, "kein Zertifikat", "-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----\n"])
def test_ca_datei_fehlt_oder_ist_ungueltig(welt, inhalt):
    if inhalt is None:
        welt.ca.unlink()
    else:
        welt.ca.write_text(inhalt, encoding="ascii")
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 2 and "CA-Datei" in welt.text
    assert welt.reg.schreibzugriffe == 0 and welt.zert.aenderungen == 0 and not welt.sicherung.exists()


def test_abgelehnter_import_bricht_vor_den_richtlinien_ab(welt):
    welt.zert.ablehnen = True
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 1
    assert "Zertifikat" in welt.text and welt.reg.schreibzugriffe == 0


def test_vorhandene_ca_wird_nicht_importiert_und_beim_rueckweg_nicht_entfernt(welt):
    welt.zert.speicher.add(tw.SHA1)
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    assert welt.zert.aenderungen == 0
    assert welt.lauf("rueckweg") == 0
    assert tw.SHA1 in welt.zert.speicher and welt.zert.aenderungen == 0


# ---------- rueckweg ----------

def test_rueckweg_stellt_den_ausgangszustand_her(welt):
    vorher = welt.reg.momentaufnahme()
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    assert welt.lauf("rueckweg") == 0
    assert welt.reg.momentaufnahme() == vorher
    assert welt.zert.speicher == set()
    assert not welt.platz_datei.exists() and not welt.sicherung.exists() and not (welt.lokal / "WAMO").exists()
    assert not any(p.exists() for p in welt.verknuepfungen)
    assert welt.profil.is_dir()  # das Profil bleibt, nur --profil-loeschen entfernt es


def test_rueckweg_stellt_vorige_werte_wieder_her(welt):
    welt.reg.vorbelegen("HKCU", CHROME_PFAD, {"TranslateEnabled": ("dword", 1), "HardwareAccelerationModeEnabled": ("dword", 0)})
    welt.reg.vorbelegen("HKCU", CHROME_PFAD + "\\URLBlocklist", {"1": ("sz", "example.org")})
    vorher = welt.reg.momentaufnahme()
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "4") == 0  # zweiter Lauf darf die erste Sicherung nicht überschreiben
    assert welt.reg.werte("HKCU", CHROME_PFAD)["TranslateEnabled"] == ("dword", 0)
    assert welt.lauf("rueckweg") == 0
    assert welt.reg.momentaufnahme() == vorher


def test_rueckweg_zweimal_und_ohne_sicherung(welt):
    assert welt.lauf("rueckweg") == 0 and "nichts zurückzunehmen" in welt.text
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    assert welt.lauf("rueckweg") == 0
    schreib = welt.reg.schreibzugriffe
    assert welt.lauf("rueckweg") == 0 and welt.reg.schreibzugriffe == schreib


def test_rueckweg_trocken_aendert_nichts(welt):
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    dateien, reg, zert = welt.dateien(), welt.reg.momentaufnahme(), set(welt.zert.speicher)
    assert welt.lauf("rueckweg", "--trocken") == 0 and "Trockenlauf" in welt.text
    assert welt.dateien() == dateien and welt.reg.momentaufnahme() == reg and welt.zert.speicher == zert


def test_rueckweg_profil_loeschen_nur_mit_option(welt):
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    (welt.profil / "Default").mkdir()
    (welt.profil / "Default" / "Cookies").write_bytes(b"x")
    welt.lauf("rueckweg")
    assert (welt.profil / "Default" / "Cookies").exists()
    assert welt.lauf("rueckweg", "--profil-loeschen") == 0
    assert not welt.profil.exists()


def test_rueckweg_alle_certutil_aufrufe_nur_benutzerspeicher(welt):
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    welt.lauf("rueckweg")
    assert any(a[1] == "-delstore" for a in welt.zert.aufrufe)
    assert all(a[0] == "-user" and "Root" in a for a in welt.zert.aufrufe)


# ---------- status ----------

def test_status_vor_und_nach_dem_einrichten(welt):
    assert welt.lauf("status") == 1 and "nicht eingerichtet" in welt.text
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    welt.zeilen.clear()
    assert welt.lauf("status") == 0
    assert "Platz 3" in welt.text and "Zertifikat" in welt.text and tw.TOKEN not in welt.text


def test_status_aendert_nichts(welt):
    welt.lauf("einrichten", "--kiosk-konto", "--platz", "3")
    dateien, schreib = welt.dateien(), welt.reg.schreibzugriffe
    welt.lauf("status")
    assert welt.dateien() == dateien and welt.reg.schreibzugriffe == schreib


def test_unbekannter_befehl_ist_ein_fehler(welt):
    with pytest.raises(SystemExit) as e:
        welt.lauf("loeschen")
    assert e.value.code == 2
