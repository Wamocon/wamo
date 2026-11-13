"""`pruefen`: Ist dieser Laptop bereit für das Testlabor? (nur Standardbibliothek, Windows, nur lesend)

Geprüft werden: Platzdatei, Zertifikat der WAMO-CA im Benutzerspeicher, Chrome-Richtlinien, Chrome, die vier Adressen des Servers mit
TLS-Prüfung gegen genau die CA-Datei, die Uhr (Kopf Date des Servers gegen die Uhr des Rechners, höchstens 5 Minuten Abweichung) und das
Mikrofon. Das Mikrofon ist eine Heuristik: Die Eingabegeräte stehen lesbar in der Registrierung (HKLM\\...\\MMDevices\\Audio\\Capture, kein
Administrator nötig, kein PowerShell). Ein aktives Bluetooth-Freisprechgerät bekommt eine Warnung, weil das Freisprechprofil unter
Windows oft nur Stille liefert. Welches Gerät Chrome tatsächlich benutzt, ist von hier aus nicht sicher feststellbar.

Jede Zeile beginnt mit [ok], [WARNUNG] oder [FEHLER]. Ausgang 1 nur bei mindestens einem Fehler, Warnungen allein ergeben 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

import wamo_labor_platz_registrierung as rg
import wamo_labor_platz_zertifikat as zt
from wamo_labor_platz_adressen import HERKUENFTE, adresse_zulaessig
from wamo_labor_platz_start import chrome_finden
from wamo_labor_platz_umgebung import Abruf, AbrufFehler, Umgebung
from wamo_labor_platz_zustand import ZustandFehler, platz_lesen

GRENZE_UHR_S = 300
MM_PFAD = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\MMDevices\\Audio\\Capture"
SCHLUESSEL_BESCHREIBUNG = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"  # kurze Bezeichnung, zum Beispiel "Mikrofon"
SCHLUESSEL_SCHNITTSTELLE = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"  # Name des Geräts, zum Beispiel "Realtek Audio"
BLUETOOTH_MARKEN = ("hands-free", "handsfree", "freisprech", "bluetooth", "bthhfenum", "bthenum", "hfp")
_STUFE = {"ok": "[ok]", "warnung": "[WARNUNG]", "fehler": "[FEHLER]"}


@dataclass(frozen=True)
class Befund:
    stufe: str  # "ok", "warnung" oder "fehler"
    thema: str
    text: str

    def zeile(self) -> str:
        return f"{_STUFE[self.stufe]} {self.thema}: {self.text}"


def _ok(thema: str, text: str) -> Befund:
    return Befund("ok", thema, text)


def _warnung(thema: str, text: str) -> Befund:
    return Befund("warnung", thema, text)


def _fehler(thema: str, text: str) -> Befund:
    return Befund("fehler", thema, text)


def pruefe_zertifikat(umg: Umgebung, sha1: str) -> Befund:
    fp = zt.gruppiert(sha1)
    if zt.im_speicher(umg.certutil, sha1):
        return _ok("Zertifikat", f"WAMO-CA (SHA-1 {fp}) ist im Benutzerspeicher")
    return _fehler("Zertifikat", f"WAMO-CA (SHA-1 {fp}) fehlt im Benutzerspeicher: `einrichten` ausführen")


def pruefe_richtlinien(umg: Umgebung) -> Befund:
    soll = rg.richtlinien()
    abweichend = [r.name for r in soll if rg.lese_zustand(umg.reg, r) != (r.typ, r.wert)]
    if not abweichend:
        return _ok("Richtlinien", f"alle {len(soll)} Chrome-Richtlinien gesetzt (Registrierung, in Chrome nicht gegengeprüft)")
    kurz = ", ".join(abweichend[:5]) + (", ..." if len(abweichend) > 5 else "")
    return _fehler("Richtlinien", f"{len(abweichend)} von {len(soll)} fehlen oder weichen ab ({kurz}): `einrichten` ausführen")


def pruefe_chrome(umg: Umgebung) -> Befund:
    chrome = chrome_finden(umg.chrome_kandidaten)
    if chrome is None:
        return _fehler("Chrome", "Google Chrome nicht gefunden (Edge wird nicht unterstützt)")
    return _ok("Chrome", str(chrome))


def _abrufen(umg: Umgebung, url: str, ca: Path) -> tuple[Befund, Abruf | None]:
    name = url.split("//", 1)[1]
    if not adresse_zulaessig(url):
        return _fehler("Adresse", f"{url} gehört nicht zu den festen Adressen des Labors und wird nicht aufgerufen"), None
    try:
        antwort = umg.abruf(url, ca)
    except AbrufFehler as e:
        art = {"tls": "TLS-Prüfung gegen die WAMO-CA gescheitert", "frist": "keine Antwort innerhalb der Frist"}.get(e.art, "nicht erreichbar")
        return _fehler("Adresse", f"{name}: {art} ({e.text})"), None
    if antwort.status >= 500:
        return _warnung("Adresse", f"{name}: erreichbar, aber der Server meldet Fehler {antwort.status}"), antwort
    return _ok("Adresse", f"{name}: erreichbar (HTTP {antwort.status}), TLS gegen die WAMO-CA geprüft"), antwort


def pruefadressen(board_url: str) -> tuple[str, ...]:
    return (f"{HERKUENFTE[0]}/auth/v1/health", f"{HERKUENFTE[1]}/", f"{HERKUENFTE[2]}/", board_url)


def pruefe_adressen(umg: Umgebung, ca: Path, board_url: str) -> tuple[list[Befund], list[str]]:
    befunde, daten = [], []
    for url in pruefadressen(board_url):
        befund, antwort = _abrufen(umg, url, ca)
        befunde.append(befund)
        if antwort is not None and antwort.datum:
            daten.append(antwort.datum)
    return befunde, daten


def pruefe_uhr(umg: Umgebung, daten: list[str]) -> Befund:
    if not daten:
        return _warnung("Uhr", "nicht prüfbar, weil keine Antwort des Servers den Kopf Date enthielt")
    try:
        server = parsedate_to_datetime(daten[0]).timestamp()
    except (TypeError, ValueError):
        return _warnung("Uhr", "nicht prüfbar, der Kopf Date des Servers ist nicht lesbar")
    abweichung = umg.jetzt() - server
    minuten, sekunden = divmod(int(abs(abweichung)), 60)
    if abs(abweichung) > GRENZE_UHR_S:
        richtung = "vor" if abweichung > 0 else "hinter"
        return _fehler("Uhr", f"Die Uhr geht {minuten} Minuten {sekunden} Sekunden {richtung} der Serveruhr (erlaubt sind höchstens "
                       f"{GRENZE_UHR_S // 60} Minuten). Anmeldung mit Code und Zertifikatsprüfung schlagen sonst fehl: Zeit abgleichen.")
    return _ok("Uhr", f"Abweichung zum Server {int(abs(abweichung))} Sekunden (erlaubt {GRENZE_UHR_S // 60} Minuten)")


# ---------- Mikrofon ----------

def _text(werte: dict, name: str) -> str:
    eintrag = werte.get(name)
    return str(eintrag[1]) if eintrag and eintrag[0] == "sz" else ""


def eingabegeraete(umg: Umgebung) -> list[tuple[str, bool]]:
    """Alle aktiven Eingabegeräte als (Anzeigename, Bluetooth-Freisprechgerät?). Nur lesen."""
    ergebnis = []
    for kennung in umg.reg.unterschluessel("HKLM", MM_PFAD):
        pfad = f"{MM_PFAD}\\{kennung}"
        zustand = umg.reg.werte("HKLM", pfad).get("DeviceState")
        if not zustand or int(zustand[1]) & 0xF != 1:  # 1 = aktiv, 2 deaktiviert, 4 nicht vorhanden, 8 getrennt
            continue
        eigenschaften = umg.reg.werte("HKLM", pfad + "\\Properties")
        kurz, geraet = _text(eigenschaften, SCHLUESSEL_BESCHREIBUNG), _text(eigenschaften, SCHLUESSEL_SCHNITTSTELLE)
        name = f"{kurz} ({geraet})" if kurz and geraet else (kurz or geraet or kennung)
        alles = " ".join(str(w[1]) for w in eigenschaften.values() if w[0] == "sz").lower()
        ergebnis.append((name, any(m in alles for m in BLUETOOTH_MARKEN)))
    return ergebnis


def pruefe_mikrofon(umg: Umgebung) -> list[Befund]:
    geraete = eingabegeraete(umg)
    if not geraete:
        return [_warnung("Mikrofon", "Windows meldet kein aktives Eingabegerät: Headset mit USB oder Klinke anschließen")]
    befunde = []
    for name, bluetooth in geraete:
        if bluetooth:
            befunde.append(_warnung("Mikrofon", f"{name} ist ein Bluetooth-Freisprechgerät. Im Freisprechprofil liefert das Mikrofon unter "
                                    "Windows oft nur Stille: ein Headset mit USB oder Klinke benutzen"))
        else:
            befunde.append(_ok("Mikrofon", name))
    return befunde


# ---------- Ablauf ----------

def _ca_finden(umg: Umgebung, option: str | None) -> tuple[Path | None, bytes | None, str | None]:
    """Die CA-Datei, die genau den festgelegten SHA-256 hat, oder der Grund, warum es keine gibt."""
    try:
        pfad, der = zt.ca_suchen((Path(option),) if option else umg.ca_kandidaten, umg.ca_sha256)
    except zt.CaFehler as e:
        return None, None, str(e)
    return pfad, der, None


def _platz_befund(umg: Umgebung) -> tuple[dict | None, Befund]:
    try:
        daten = platz_lesen(umg)
    except ZustandFehler as e:
        return None, _fehler("Platz", str(e))
    if daten is None:
        return None, _fehler("Platz", f"nicht eingerichtet (keine {umg.platz_datei}): `einrichten --platz N` ausführen")
    return daten, _ok("Platz", f"Platz {daten['platz']} ({umg.platz_datei})")


def _befunde_sammeln(umg: Umgebung, ca_option: str | None) -> list[Befund]:
    daten, platz = _platz_befund(umg)
    ca, der, ca_fehler = _ca_finden(umg, ca_option)
    sha1 = daten["ca_sha1"] if daten else (zt.sha1_hex(der) if der else None)
    befunde = [platz, pruefe_zertifikat(umg, sha1) if sha1 else _fehler("Zertifikat", "unbekannt (weder Platzdatei noch gültige CA-Datei)")]
    befunde += [pruefe_richtlinien(umg), pruefe_chrome(umg)]
    daten_uhr: list[str] = []
    if ca is None:
        befunde.append(_fehler("Adressen", f"{ca_fehler}. TLS lässt sich nicht prüfen"))
    elif daten is None:
        befunde.append(_fehler("Adressen", "ohne gültige Platzdatei werden keine Adressen aufgerufen"))
    else:
        adressen, daten_uhr = pruefe_adressen(umg, ca, daten["board_url"])
        befunde += adressen
    befunde.append(pruefe_uhr(umg, daten_uhr))
    return befunde + pruefe_mikrofon(umg)


def cmd_pruefen(umg: Umgebung, ca_option: str | None = None, warten: bool = False) -> int:
    befunde = _befunde_sammeln(umg, ca_option)
    for b in befunde:
        umg.ausgabe(b.zeile())
    zaehler = {s: sum(b.stufe == s for b in befunde) for s in _STUFE}
    umg.ausgabe(f"Ergebnis: {zaehler['ok']} in Ordnung, {zaehler['warnung']} Warnung(en), {zaehler['fehler']} Fehler.")
    if warten:
        umg.taste()
    return 1 if zaehler["fehler"] else 0
