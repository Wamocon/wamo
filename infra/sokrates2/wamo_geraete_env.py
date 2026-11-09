#!/usr/bin/python3 -I
"""Zugangsdaten des WAMO-Gerätedienstes auf Sokrates-2: /etc/wamo-geraete/geraete.env (root:wamo-geraete 0640).

Installiert als /usr/local/sbin/wamo-geraete-env durch wamo-geraete-einrichten.sh. Aufruf nur mit sudo.

  wamo-geraete-env setzen        liest KEY=VALUE-Zeilen von stdin (nur WAMO_GERAETE_<ZUGANG>_BENUTZER, _PASSWORT und,
                                 für SNMPv3, _PRIVPASSWORT)
  wamo-geraete-env loeschen K..  entfernt Schlüssel
  wamo-geraete-env liste         zeigt Schlüsselnamen und ob ein Wert gesetzt ist, nie Werte

Beispiel vom PC (Wert nie in der Befehlszeile, nur über stdin):
  ssh sokrates2 "sudo wamo-geraete-env setzen && sudo systemctl restart wamo-geraete" < datei-mit-zeilen
Der Dienst liest die Datei selbst beim Start (nicht als Umgebung): Kindprozesse (ssh, nvidia-smi) sehen die Werte nie.
Werte stehen in einfachen Anführungszeichen; einfache Anführungszeichen, Backslash, Zeilenumbrüche und Steuerzeichen
werden abgelehnt. Die Datei wird atomar ersetzt.
"""

from __future__ import annotations

import grp
import os
import re
import sys
import tempfile

DATEI = "/etc/wamo-geraete/geraete.env"
GRUPPE = "wamo-geraete"
MAX_WERT = 512
MAX_ZEILEN = 50
SCHLUESSEL_RE = re.compile(r"^WAMO_GERAETE_[A-Z][A-Z0-9]{1,15}_(BENUTZER|PASSWORT|PRIVPASSWORT)$")
UNGUELTIG = re.compile(r"['\\\x00-\x1f\x7f]")


def lesen() -> dict[str, str]:
    werte: dict[str, str] = {}
    try:
        with open(DATEI, encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.rstrip("\n")
                if not zeile or zeile.startswith("#") or "=" not in zeile:
                    continue
                name, wert = zeile.split("=", 1)
                if len(wert) >= 2 and wert[0] == "'" and wert[-1] == "'":
                    wert = wert[1:-1]
                werte[name] = wert
    except FileNotFoundError:
        pass
    return werte


def schreiben(werte: dict[str, str]) -> None:
    gid = grp.getgrnam(GRUPPE).gr_gid
    fd, tmp = tempfile.mkstemp(prefix=".geraete.env.", dir=os.path.dirname(DATEI))
    try:
        os.fchown(fd, 0, gid)
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# WAMO-Gerätedienst. Nur über /usr/local/sbin/wamo-geraete-env ändern, nie Werte ausgeben.\n")
            for name in sorted(werte):
                f.write(f"{name}='{werte[name]}'\n")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, DATEI)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def setzen() -> int:
    eingabe = sys.stdin.read(MAX_ZEILEN * (MAX_WERT + 80))
    neu: dict[str, str] = {}
    for nummer, zeile in enumerate(eingabe.splitlines(), 1):
        if nummer > MAX_ZEILEN:
            print("Zu viele Zeilen", file=sys.stderr)
            return 2
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        if "=" not in zeile:
            print(f"Zeile {nummer}: kein KEY=VALUE", file=sys.stderr)
            return 2
        name, wert = zeile.split("=", 1)
        name = name.strip()
        if not SCHLUESSEL_RE.fullmatch(name):
            print(f"Zeile {nummer}: Schlüssel {name[:64]!r} ist nicht erlaubt", file=sys.stderr)
            return 2
        if not wert or len(wert) > MAX_WERT or UNGUELTIG.search(wert):
            print(f"Zeile {nummer}: Wert für {name} ist leer, zu lang oder enthält unzulässige Zeichen", file=sys.stderr)
            return 2
        neu[name] = wert
    if not neu:
        print("Keine Werte erhalten", file=sys.stderr)
        return 2
    werte = lesen()
    werte.update(neu)
    schreiben(werte)
    print("Übernommen: " + ", ".join(sorted(neu)) + " (Werte nicht angezeigt). Danach: systemctl restart wamo-geraete")
    return 0


def loeschen(namen: list[str]) -> int:
    werte = lesen()
    weg = [n for n in namen if n in werte]
    for n in weg:
        del werte[n]
    schreiben(werte)
    print("Entfernt: " + (", ".join(weg) or "nichts"))
    return 0


def liste() -> int:
    for name, wert in sorted(lesen().items()):
        print(f"{name}: {'gesetzt' if wert else 'leer'}")
    return 0


def main(argv: list[str]) -> int:
    if os.geteuid() != 0:
        print("Bitte mit sudo ausführen.", file=sys.stderr)
        return 1
    befehl = argv[1] if len(argv) > 1 else ""
    if befehl == "setzen" and len(argv) == 2:
        return setzen()
    if befehl == "loeschen" and len(argv) > 2:
        return loeschen(argv[2:])
    if befehl == "liste" and len(argv) == 2:
        return liste()
    print("Aufruf: wamo-geraete-env setzen | loeschen KEY... | liste", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
