"""Chrome-Richtlinien im Benutzerzweig der Registrierung für das Platz-Skript (nur Standardbibliothek, Windows).

Gearbeitet wird nur in HKCU\\Software\\Policies\\Google\\Chrome (kein Administrator nötig). Das ist Zusatzschutz und nicht die Grenze: Ein
Benutzerzweig lässt sich vom Kiosk-Konto selbst ändern, die Grenzen sind der Kern und das Netz (docs/testlabor-wamo.md 3.9).

Die Registrierung steckt hinter der kleinen Schnittstelle `Registrierung`. Die echte Fassung (`WinRegistrierung`) kapselt winreg und
wird nur im Betrieb gebaut, die Tests benutzen eine Attrappe. Werte: "dword" (Zahl), "sz" (Text) und "liste" (Unterschlüssel mit den
Werten "1", "2", ..., so verlangt es Chrome für Listen).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Protocol

from wamo_labor_platz_adressen import HERKUENFTE, WAMO_URL, WEITER_ADRESSE

CHROME_PFAD = "Software\\Policies\\Google\\Chrome"
GOOGLE_PFAD = "Software\\Policies\\Google"
HIVE = "HKCU"


class Registrierung(Protocol):
    def schluessel_vorhanden(self, hive: str, pfad: str) -> bool: ...
    def werte(self, hive: str, pfad: str) -> dict[str, tuple[str, object]]: ...
    def unterschluessel(self, hive: str, pfad: str) -> list[str]: ...
    def setzen(self, hive: str, pfad: str, name: str, typ: str, wert: object) -> None: ...
    def wert_loeschen(self, hive: str, pfad: str, name: str) -> None: ...
    def leeren_schluessel_loeschen(self, hive: str, pfad: str) -> bool: ...


class NichtSicherbar(Exception):
    """Ein vorhandener Wert lässt sich nicht verlustfrei sichern und wiederherstellen (Text für die Person)."""


@dataclass(frozen=True)
class Richtlinie:
    name: str
    typ: str  # "dword", "sz" oder "liste"
    wert: object  # int, str oder tuple[str, ...]


def richtlinien(wamo_url: str = WAMO_URL) -> tuple[Richtlinie, ...]:
    """Alle Richtlinien des Platzes. Jeder Name ist ein Schlüssel, den es für Chrome gibt (ein Test prüft das gegen eine feste Liste)."""
    d, s, liste = "dword", "sz", "liste"
    return (
        Richtlinie("TranslateEnabled", d, 0),
        Richtlinie("PasswordManagerEnabled", d, 0),
        Richtlinie("AutofillAddressEnabled", d, 0),
        Richtlinie("AutofillCreditCardEnabled", d, 0),
        Richtlinie("BrowserSignin", d, 0),
        Richtlinie("SyncDisabled", d, 1),
        Richtlinie("SavingBrowserHistoryDisabled", d, 1),
        Richtlinie("MetricsReportingEnabled", d, 0),
        Richtlinie("DefaultBrowserSettingEnabled", d, 0),
        Richtlinie("BackgroundModeEnabled", d, 0),
        Richtlinie("HomepageLocation", s, wamo_url),
        Richtlinie("HomepageIsNewTabPage", d, 0),
        Richtlinie("NewTabPageLocation", s, wamo_url),
        Richtlinie("ShowHomeButton", d, 0),
        Richtlinie("RestoreOnStartup", d, 4),
        Richtlinie("RestoreOnStartupURLs", liste, (wamo_url,)),
        Richtlinie("URLBlocklist", liste, ("*",)),
        Richtlinie("URLAllowlist", liste, HERKUENFTE + (WEITER_ADRESSE,)),
        Richtlinie("AudioCaptureAllowed", d, 1),
        Richtlinie("AudioCaptureAllowedUrls", liste, (wamo_url,)),
        Richtlinie("VideoCaptureAllowed", d, 0),
        Richtlinie("ScreenCaptureAllowed", d, 0),
        Richtlinie("DefaultPopupsSetting", d, 2),
        Richtlinie("DefaultGeolocationSetting", d, 2),
        Richtlinie("DefaultNotificationsSetting", d, 2),
        Richtlinie("DefaultWebUsbGuardSetting", d, 2),
        Richtlinie("DefaultSerialGuardSetting", d, 2),
        Richtlinie("DefaultWebBluetoothGuardSetting", d, 2),
        Richtlinie("DeveloperToolsAvailability", d, 2),
        Richtlinie("DownloadRestrictions", d, 3),
        Richtlinie("IncognitoModeAvailability", d, 1),
        Richtlinie("BrowserGuestModeEnabled", d, 0),
        Richtlinie("BrowserAddPersonEnabled", d, 0),
        Richtlinie("ExtensionInstallBlocklist", liste, ("*",)),
    )


def _pfad(name: str) -> str:
    return f"{CHROME_PFAD}\\{name}"


def lese_zustand(reg: Registrierung, r: Richtlinie) -> tuple[str, object] | None:
    """Der aktuelle Zustand als (Typ, Wert) oder None, wenn die Richtlinie nicht gesetzt ist."""
    if r.typ == "liste":
        if not reg.schluessel_vorhanden(HIVE, _pfad(r.name)):
            return None
        werte = reg.werte(HIVE, _pfad(r.name))
        reihenfolge = sorted(werte, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n))
        return "liste", tuple(str(werte[n][1]) for n in reihenfolge)
    eintrag = reg.werte(HIVE, CHROME_PFAD).get(r.name)
    return None if eintrag is None else (eintrag[0], eintrag[1])


def _liste_entfernen(reg: Registrierung, name: str) -> None:
    for n in list(reg.werte(HIVE, _pfad(name))):
        reg.wert_loeschen(HIVE, _pfad(name), n)
    reg.leeren_schluessel_loeschen(HIVE, _pfad(name))


def zustand_setzen(reg: Registrierung, name: str, typ: str, wert: object) -> None:
    if typ == "liste":
        _liste_entfernen(reg, name)
        for i, eintrag in enumerate(wert, start=1):  # type: ignore[arg-type]
            reg.setzen(HIVE, _pfad(name), str(i), "sz", eintrag)
    else:
        reg.setzen(HIVE, CHROME_PFAD, name, typ, wert)


def zustand_entfernen(reg: Registrierung, name: str, typ: str) -> None:
    if typ == "liste":
        _liste_entfernen(reg, name)
    else:
        reg.wert_loeschen(HIVE, CHROME_PFAD, name)


def _pruefe_sicherbar(reg: Registrierung, r: Richtlinie) -> None:
    """Gesichert werden nur Zahlen (dword), Texte (sz) und Listen aus Texten mit den Namen 1, 2, ... Alles andere (binär, mehrzeilig,
    erweiterbar, 64 Bit, Unterschlüssel in einer Liste) ginge beim Rückweg verloren: Dann bricht das Einrichten vor jeder Änderung ab."""
    grund = None
    if r.typ == "liste":
        pfad = _pfad(r.name)
        if reg.unterschluessel(HIVE, pfad):
            grund = "enthält Unterschlüssel"
        elif any(t != "sz" or not n.isdigit() for n, (t, _) in reg.werte(HIVE, pfad).items()):
            grund = "enthält Einträge, die keine Texte mit den Namen 1, 2, ... sind"
    else:
        eintrag = reg.werte(HIVE, CHROME_PFAD).get(r.name)
        if eintrag is not None and eintrag[0] not in ("dword", "sz"):
            grund = "hat einen Typ, der nicht gesichert wird (nur Zahl und Text)"
    if grund:
        raise NichtSicherbar(f"Richtlinie {r.name}: Der vorhandene Wert {grund}, er lässt sich nicht verlustfrei sichern. "
                             "Es wurde nichts geändert. Den Wert prüfen und von Hand sichern oder entfernen.")


def zustand_sichern(reg: Registrierung, r: Richtlinie) -> dict:
    """Der Zustand vor der Änderung in einer Form, die in die Sicherungsdatei (JSON) passt."""
    _pruefe_sicherbar(reg, r)
    jetzt = lese_zustand(reg, r)
    if jetzt is None:
        return {"da": False, "typ": r.typ}
    typ, wert = jetzt
    return {"da": True, "typ": typ, "wert": list(wert) if typ == "liste" else wert}  # type: ignore[arg-type]


def zustand_wiederherstellen(reg: Registrierung, name: str, vorher: dict, soll_typ: str) -> None:
    zustand_entfernen(reg, name, soll_typ)
    if vorher.get("da"):
        wert = tuple(vorher["wert"]) if vorher["typ"] == "liste" else vorher["wert"]
        zustand_setzen(reg, name, vorher["typ"], wert)


def beschreibe(zustand: tuple[str, object] | None) -> str:
    if zustand is None:
        return "nicht gesetzt"
    typ, wert = zustand
    return "[" + ", ".join(map(str, wert)) + "]" if typ == "liste" else str(wert)  # type: ignore[arg-type]


class WinRegistrierung:
    """Die echte Registrierung über winreg. Wird nur im Betrieb gebaut, nie in den Tests (dort steht die Attrappe)."""

    def __init__(self, modul: object | None = None) -> None:
        self._w = modul if modul is not None else importlib.import_module("winreg")

    def _hive(self, hive: str):
        return {"HKCU": self._w.HKEY_CURRENT_USER, "HKLM": self._w.HKEY_LOCAL_MACHINE}[hive]

    def _oeffnen(self, hive: str, pfad: str, zugriff: int):
        return self._w.OpenKey(self._hive(hive), pfad, 0, zugriff)

    def schluessel_vorhanden(self, hive: str, pfad: str) -> bool:
        try:
            with self._oeffnen(hive, pfad, self._w.KEY_READ):
                return True
        except OSError:
            return False

    def werte(self, hive: str, pfad: str) -> dict[str, tuple[str, object]]:
        ergebnis: dict[str, tuple[str, object]] = {}
        try:
            with self._oeffnen(hive, pfad, self._w.KEY_READ) as k:
                i = 0
                while True:
                    try:
                        name, wert, art = self._w.EnumValue(k, i)
                    except OSError:
                        return ergebnis
                    ergebnis[name] = ("dword" if art == self._w.REG_DWORD else "sz" if art == self._w.REG_SZ else "andere", wert)
                    i += 1
        except OSError:
            return {}

    def unterschluessel(self, hive: str, pfad: str) -> list[str]:
        namen: list[str] = []
        try:
            with self._oeffnen(hive, pfad, self._w.KEY_READ) as k:
                i = 0
                while True:
                    try:
                        namen.append(self._w.EnumKey(k, i))
                    except OSError:
                        return namen
                    i += 1
        except OSError:
            return []

    def setzen(self, hive: str, pfad: str, name: str, typ: str, wert: object) -> None:
        art = self._w.REG_DWORD if typ == "dword" else self._w.REG_SZ
        with self._w.CreateKeyEx(self._hive(hive), pfad, 0, self._w.KEY_SET_VALUE) as k:
            self._w.SetValueEx(k, name, 0, art, wert)

    def wert_loeschen(self, hive: str, pfad: str, name: str) -> None:
        try:
            with self._oeffnen(hive, pfad, self._w.KEY_SET_VALUE) as k:
                self._w.DeleteValue(k, name)
        except OSError:
            pass  # nicht vorhanden ist der gewünschte Zustand

    def leeren_schluessel_loeschen(self, hive: str, pfad: str) -> bool:
        if not self.schluessel_vorhanden(hive, pfad) or self.werte(hive, pfad) or self.unterschluessel(hive, pfad):
            return False
        try:
            self._w.DeleteKey(self._hive(hive), pfad)
            return True
        except OSError:
            return False
