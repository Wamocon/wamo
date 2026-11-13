"""Platz-Skript für die Windows-Laptops des WAMO-Testlabors (Paket P8, docs/testlabor-wamo.md 3.9 und docs/labor-platz-einrichten.md).

Läuft ohne Administratorrechte im Kiosk-Konto, nur Standardbibliothek. Aufrufe (Python 3.10 oder neuer):
  python wamo_labor_platz.py einrichten --platz 3 [--trocken] [--ca-datei PFAD]   # CA, Chrome-Richtlinien, Profil, Platzdatei, Verknüpfungen
  python wamo_labor_platz.py starten [--ohne-board]                                # Chrome mit zwei Fenstern: WAMO links, Board rechts
  python wamo_labor_platz.py pruefen [--warten]                                    # Zertifikat, Richtlinien, Chrome, Adressen, Mikrofon, Uhr
  python wamo_labor_platz.py rueckweg [--trocken] [--profil-loeschen]              # alles wieder zurücknehmen
  python wamo_labor_platz.py status                                                # nur lesen

Alles ist idempotent. `--trocken` zeigt den Plan und ändert nichts. Vor jeder Änderung steht der vorige Zustand in der Sicherung
(%LOCALAPPDATA%\\WAMO\\platz-sicherung.json), der Rückweg stellt ihn her. Ausgänge: 0 in Ordnung, 1 Schritt oder Prüfung gescheitert,
2 Eingabe falsch (dann wurde nichts geändert).

Was geändert wird: das Zertifikat der WAMO-CA im Benutzerspeicher Root (nie im Rechnerspeicher), die Chrome-Richtlinien in
HKCU\\Software\\Policies\\Google\\Chrome, der Ordner %LOCALAPPDATA%\\wamo-chrome-labor (Chrome-Profil), die Datei
%LOCALAPPDATA%\\WAMO\\platz.json (Platznummer und Adressen, nie ein Token) und zwei Verknüpfungen auf dem Desktop.

Das Platz-Token (siehe wamo_labor_platz_start.py) wird nie gespeichert: Es wird beim Start ohne Anzeige eingegeben und gelangt über eine
einmalige Weiterleitung auf Loopback ins Board-Fenster, nie über eine Befehlszeile, eine Datei oder die Zwischenablage.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import wamo_labor_platz_registrierung as rg
import wamo_labor_platz_zertifikat as zt
from wamo_labor_platz_umgebung import Umgebung
from wamo_labor_platz_zustand import (PLATZ_MAX, PLATZ_MIN, ZustandFehler, datei_schreiben, json_lesen, json_text, platz_inhalt,
                                      platz_lesen, sicherung_erfassen, sicherung_zusammenfuehren)

EXIT_OK, EXIT_FEHLER, EXIT_EINGABE = 0, 1, 2
FEHLER = (OSError, zt.CaFehler, ZustandFehler)


class EingabeFehler(Exception):
    """Falsche Eingabe, vor der ersten Änderung erkannt."""


@dataclass
class Schritt:
    thema: str
    text: str
    aktion: Callable[[], None] | None  # None: nichts zu tun


def _melden(umg: Umgebung, schritte: list[Schritt], trocken: bool) -> None:
    for s in schritte:
        umg.ausgabe(f"  {s.thema}: {s.text}" + ("" if s.aktion else " (unverändert)"))
    if trocken:
        umg.ausgabe("Trockenlauf: Es wurde nichts geändert.")


def _ausfuehren(umg: Umgebung, schritte: list[Schritt]) -> int:
    getan = 0
    for s in schritte:
        if s.aktion is None:
            continue
        try:
            s.aktion()
        except FEHLER as e:
            umg.ausgabe(f"Fehler bei {s.thema}: {e if isinstance(e, (zt.CaFehler, ZustandFehler)) else type(e).__name__}. "
                        f"Der Rückweg stellt den vorigen Zustand her.")
            return EXIT_FEHLER
        getan += 1
    umg.ausgabe(f"Fertig: {getan} Schritt(e) ausgeführt." if getan else "Fertig: nichts zu ändern.")
    return EXIT_OK


def _platz_bestimmen(umg: Umgebung, wert: str | None) -> int:
    if wert is not None:
        if not re.fullmatch(r"[0-9]{1,2}", wert) or not PLATZ_MIN <= int(wert) <= PLATZ_MAX:
            raise EingabeFehler(f"Platznummer ungültig: erlaubt sind {PLATZ_MIN} bis {PLATZ_MAX}")
        return int(wert)
    daten = platz_lesen(umg)
    if daten is None:
        raise EingabeFehler(f"Platznummer fehlt: --platz N ({PLATZ_MIN} bis {PLATZ_MAX}) beim ersten Einrichten angeben")
    return daten["platz"]


def _ca_datei(umg: Umgebung, option: str | None) -> Path:
    kandidaten = (Path(option),) if option else umg.ca_kandidaten
    for pfad in kandidaten:
        if pfad.is_file():
            return pfad
    raise zt.CaFehler("CA-Datei nicht gefunden (--ca-datei PFAD, im Repo infra/sokrates2/wamo-ca.crt)")


def _schritt_ca(umg: Umgebung, datei: Path, sha1: str, vorhanden: bool) -> Schritt:
    fp = zt.gruppiert(sha1)
    if vorhanden:
        return Schritt("Zertifikat", f"WAMO-CA (SHA-1 {fp}) ist im Benutzerspeicher", None)
    return Schritt("Zertifikat", f"WAMO-CA (SHA-1 {fp}) in den Benutzerspeicher Root importieren. Windows fragt nach: den Fingerabdruck "
                   "mit infra/sokrates2/README.md 5.2 vergleichen und bestätigen", lambda: zt.importieren(umg.certutil, datei, sha1))


def _schritte_richtlinien(umg: Umgebung, soll: tuple[rg.Richtlinie, ...]) -> list[Schritt]:
    schritte, gleich = [], 0
    for r in soll:
        jetzt = rg.lese_zustand(umg.reg, r)
        if jetzt == (r.typ, r.wert):
            gleich += 1
            continue
        text = f"{rg.beschreibe(jetzt)} wird zu {rg.beschreibe((r.typ, r.wert))}"
        schritte.append(Schritt(f"Richtlinie {r.name}", text, lambda r=r: rg.zustand_setzen(umg.reg, r.name, r.typ, r.wert)))
    if gleich:
        schritte.append(Schritt("Richtlinien", f"{gleich} von {len(soll)} Werten sind schon wie vorgesehen", None))
    return schritte


def _schritt_profil(umg: Umgebung) -> Schritt:
    if umg.profil.is_dir():
        return Schritt("Chrome-Profil", f"{umg.profil} ist vorhanden", None)
    return Schritt("Chrome-Profil", f"Ordner {umg.profil} anlegen", lambda: umg.profil.mkdir(parents=True, exist_ok=True))


def _schritt_platzdatei(umg: Umgebung, text: str) -> Schritt:
    try:
        gleich = umg.platz_datei.read_text(encoding="utf-8") == text
    except (OSError, UnicodeDecodeError):
        gleich = False
    if gleich:
        return Schritt("platz.json", f"{umg.platz_datei} ist aktuell", None)
    return Schritt("platz.json", f"{umg.platz_datei} schreiben (Platznummer und Adressen, kein Token)",
                   lambda: datei_schreiben(umg.platz_datei, text) and None)


def _schritte_verknuepfungen(umg: Umgebung, platz_neu: bool) -> list[Schritt]:
    ziele = ((umg.verknuepfungen[0], f'"{umg.skript}" starten', "wamo_labor_platz.py starten"),
             (umg.verknuepfungen[1], f'"{umg.skript}" pruefen --warten', "wamo_labor_platz.py pruefen"))
    schritte = []
    for pfad, argumente, _ in ziele:
        if pfad.exists() and not platz_neu:
            schritte.append(Schritt("Verknüpfung", f"{pfad.name} ist vorhanden", None))
            continue
        schritte.append(Schritt("Verknüpfung", f"{pfad} anlegen",
                                lambda p=pfad, a=argumente: umg.verknuepfung(p, umg.python, a, str(umg.skript.parent))))
    return schritte


def cmd_einrichten(umg: Umgebung, platz_arg: str | None, ca_arg: str | None, trocken: bool) -> int:
    try:
        platz = _platz_bestimmen(umg, platz_arg)
        datei = _ca_datei(umg, ca_arg)
        der = zt.ca_lesen(datei)
    except (EingabeFehler, zt.CaFehler) as e:
        umg.ausgabe(f"Eingabe: {e}")
        return EXIT_EINGABE
    except ZustandFehler as e:
        umg.ausgabe(f"Fehler: {e}")
        return EXIT_FEHLER
    sha1, soll = zt.sha1_hex(der), rg.richtlinien()
    vorhanden = zt.im_speicher(umg.certutil, sha1)
    text = json_text(platz_inhalt(umg, platz, sha1))
    schritte = [_schritt_ca(umg, datei, sha1, vorhanden), *_schritte_richtlinien(umg, soll), _schritt_profil(umg),
                _schritt_platzdatei(umg, text)]
    platz_neu = schritte[-1].aktion is not None
    schritte += _schritte_verknuepfungen(umg, platz_neu)
    umg.ausgabe(("Trockenlauf, " if trocken else "") + f"Plan für Platz {platz}:" + (" (Platz 15 und 16 sind für die Abnahme reserviert)"
                                                                                       if platz >= 15 else ""))
    _melden(umg, schritte, trocken)
    if trocken:
        return EXIT_OK
    try:
        if any(s.aktion for s in schritte):
            alt = json_lesen(umg.sicherung_datei)
            datei_schreiben(umg.sicherung_datei, json_text(sicherung_zusammenfuehren(alt, sicherung_erfassen(umg, soll, sha1, vorhanden))))
    except FEHLER as e:
        umg.ausgabe(f"Fehler bei der Sicherung: {e if isinstance(e, ZustandFehler) else type(e).__name__}. Es wurde nichts geändert.")
        return EXIT_FEHLER
    return _ausfuehren(umg, schritte)


# ---------- rueckweg ----------

def _richtlinien_zuruecksetzen(umg: Umgebung, sich: dict) -> None:
    for r in rg.richtlinien():
        eintrag = sich["richtlinien"].get(r.name)
        if eintrag is not None:
            rg.zustand_wiederherstellen(umg.reg, r.name, eintrag, r.typ)
    if not sich["schluessel"]["chrome"]:
        umg.reg.leeren_schluessel_loeschen(rg.HIVE, rg.CHROME_PFAD)
    if not sich["schluessel"]["google"]:
        umg.reg.leeren_schluessel_loeschen(rg.HIVE, rg.GOOGLE_PFAD)


def _geaenderte_richtlinien(umg: Umgebung, sich: dict) -> list[str]:
    namen = []
    for r in rg.richtlinien():
        eintrag = sich["richtlinien"].get(r.name)
        if eintrag is None:
            continue
        vorher = (eintrag["typ"], tuple(eintrag["wert"]) if eintrag["typ"] == "liste" else eintrag["wert"]) if eintrag["da"] else None
        if rg.lese_zustand(umg.reg, r) != vorher:
            namen.append(r.name)
    return namen


def _platzdatei_zurueck(umg: Umgebung, vorher: str | None) -> None:
    if vorher is None:
        umg.platz_datei.unlink(missing_ok=True)
    else:
        datei_schreiben(umg.platz_datei, vorher)


def _profil_loeschen(umg: Umgebung) -> None:
    if umg.profil.is_symlink():
        raise OSError("Profilordner ist eine Verknüpfung")
    shutil.rmtree(umg.profil)


def _rueckweg_richtlinien(umg: Umgebung, sich: dict) -> Schritt:
    namen = _geaenderte_richtlinien(umg, sich)
    if not namen:
        return Schritt("Richtlinien", "schon auf dem vorigen Stand", None)
    kurz = ", ".join(namen[:4]) + (", ..." if len(namen) > 4 else "")
    return Schritt("Richtlinien", f"{len(namen)} Wert(e) auf den vorigen Stand ({kurz})", lambda: _richtlinien_zuruecksetzen(umg, sich))


def _rueckweg_ca(umg: Umgebung, ca: dict) -> Schritt:
    if ca["vorher_vorhanden"] or not zt.im_speicher(umg.certutil, ca["sha1"]):
        return Schritt("Zertifikat", "bleibt im Speicher (war vorher schon da oder ist nicht mehr da)", None)
    return Schritt("Zertifikat", f"WAMO-CA (SHA-1 {zt.gruppiert(ca['sha1'])}) aus dem Benutzerspeicher entfernen",
                   lambda: zt.entfernen(umg.certutil, ca["sha1"]))


def _rueckweg_platzdatei(umg: Umgebung, vorher: str | None) -> Schritt:
    try:
        aktuell = umg.platz_datei.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        aktuell = None
    if aktuell == vorher:
        return Schritt("platz.json", "schon im vorigen Zustand", None)
    return Schritt("platz.json", "entfernen" if vorher is None else "auf den vorigen Inhalt zurück",
                   lambda: _platzdatei_zurueck(umg, vorher))


def _schritte_rueckweg(umg: Umgebung, sich: dict) -> list[Schritt]:
    schritte = [_rueckweg_richtlinien(umg, sich), _rueckweg_ca(umg, sich["ca"]), _rueckweg_platzdatei(umg, sich["platz_json_vorher"])]
    for pfad in umg.verknuepfungen:
        if not sich["verknuepfungen"].get(pfad.name, True) and pfad.exists():
            schritte.append(Schritt("Verknüpfung", f"{pfad.name} entfernen", lambda p=pfad: p.unlink()))
    return schritte


def _abschluss(umg: Umgebung, sich: dict | None) -> None:
    umg.sicherung_datei.unlink(missing_ok=True)
    if sich is not None and not sich["wamo_ordner_vorher"]:
        try:
            umg.platz_ordner.rmdir()  # nur wenn leer
        except OSError:
            pass


def cmd_rueckweg(umg: Umgebung, trocken: bool, profil_loeschen: bool) -> int:
    try:
        sich = json_lesen(umg.sicherung_datei)
    except ZustandFehler as e:
        umg.ausgabe(f"Fehler: {e}. Die Sicherung wird nicht überschrieben, bitte prüfen.")
        return EXIT_FEHLER
    schritte = _schritte_rueckweg(umg, sich) if sich is not None else []
    if sich is None:
        umg.ausgabe("Keine Sicherung gefunden: Es ist nichts zurückzunehmen.")
    if profil_loeschen and umg.profil.exists():
        schritte.append(Schritt("Chrome-Profil", f"Ordner {umg.profil} mit allen Anmeldedaten löschen", lambda: _profil_loeschen(umg)))
    umg.ausgabe(("Trockenlauf, " if trocken else "") + "Plan für den Rückweg:")
    _melden(umg, schritte, trocken)
    if trocken:
        return EXIT_OK
    rc = _ausfuehren(umg, schritte)
    if rc == EXIT_OK and sich is not None:
        _abschluss(umg, sich)
    return rc


# ---------- status ----------

def cmd_status(umg: Umgebung) -> int:
    try:
        daten = platz_lesen(umg)
    except ZustandFehler as e:
        umg.ausgabe(f"Fehler: {e}")
        return EXIT_FEHLER
    if daten is None:
        umg.ausgabe(f"Platz: nicht eingerichtet (keine {umg.platz_datei})")
        return EXIT_FEHLER
    soll = rg.richtlinien()
    ok_richtlinien = sum(rg.lese_zustand(umg.reg, r) == (r.typ, r.wert) for r in soll)
    ca_da = zt.im_speicher(umg.certutil, daten["ca_sha1"])
    lnk = sum(p.exists() for p in umg.verknuepfungen)
    umg.ausgabe(f"Platz {daten['platz']} (WAMO {daten['wamo_url']}, Board {daten['board_url']})")
    umg.ausgabe(f"Zertifikat der WAMO-CA im Benutzerspeicher: {'ja' if ca_da else 'nein'} (SHA-1 {zt.gruppiert(daten['ca_sha1'])})")
    umg.ausgabe(f"Richtlinien: {ok_richtlinien} von {len(soll)} wie vorgesehen")
    umg.ausgabe(f"Chrome-Profil: {'vorhanden' if umg.profil.is_dir() else 'fehlt'}, Verknüpfungen: {lnk} von 2")
    umg.ausgabe(f"Sicherung für den Rückweg: {'vorhanden' if umg.sicherung_datei.exists() else 'fehlt'}")
    return EXIT_OK if ca_da and ok_richtlinien == len(soll) and lnk == 2 and umg.profil.is_dir() else EXIT_FEHLER


# ---------- Aufruf ----------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wamo_labor_platz.py", description="Platz-Skript für die Laptops des WAMO-Testlabors")
    sub = p.add_subparsers(dest="befehl", required=True)
    e = sub.add_parser("einrichten", help="CA, Chrome-Richtlinien, Profil, Platzdatei und Verknüpfungen einrichten")
    e.add_argument("--platz", metavar="N", help="Platznummer 1 bis 16 (beim ersten Mal nötig)")
    e.add_argument("--ca-datei", metavar="PFAD", help="Datei der WAMO-CA (Standard: wamo-ca.crt neben dem Skript oder im Repo)")
    e.add_argument("--trocken", action="store_true", help="nur den Plan zeigen")
    r = sub.add_parser("rueckweg", help="alles zurücknehmen")
    r.add_argument("--trocken", action="store_true", help="nur den Plan zeigen")
    r.add_argument("--profil-loeschen", action="store_true", help="auch das Chrome-Profil mit den Anmeldedaten löschen")
    sub.add_parser("status", help="Stand zeigen, ändert nichts")
    return p


def _verteilen(umg: Umgebung, a: argparse.Namespace) -> int:
    if a.befehl == "einrichten":
        return cmd_einrichten(umg, a.platz, a.ca_datei, a.trocken)
    if a.befehl == "rueckweg":
        return cmd_rueckweg(umg, a.trocken, a.profil_loeschen)
    return cmd_status(umg)


def main(argv: list[str] | None = None, umg: Umgebung | None = None) -> int:
    args = _parser().parse_args(argv)
    if umg is None:
        from wamo_labor_platz_umgebung import echte_umgebung
        umg = echte_umgebung()
    return _verteilen(umg, args)


if __name__ == "__main__":
    for _strom in (sys.stdout, sys.stderr):
        _strom.reconfigure(errors="replace")
    sys.exit(main())
