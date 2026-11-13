"""Nachlauf des Testlabors: nach dem Labortag alle Laborprofile sperren und alle Daten der 16 Plätze und des Kerns löschen.

Der Befehl nachlauf tut, was im Konzept (docs/testlabor-wamo.md, 5.2, "Nach dem Labortag") und im Runbook (docs/labor-runbook.md, Abschnitt "Nach
dem Labortag") steht, in fünf Schritten und einer festen Reihenfolge:
  1. Zählung davor (nur lesen): Kern (Laborprofile, freigeschaltete, Zeilen) und Dienst (Plätze, offene, Tickets, Kommentare, Einwilligungen, Platz-Token).
     Sie dient als Nachweis und als Vorprobe: Ohne Weg zum Server und ohne gültige Anmeldung ändert der Lauf nichts.
  2. Kern: alle Laborprofile sperren (wamo_labor.py sperren --alle, setzt nur labor_aktiv = false).
  3. Dienst: für die Plätze 1 bis 16 den Durchlauf beenden und die Daten löschen (Betreuer-Schnittstelle des Dienstes, jeder Aufruf einzeln).
     Währenddessen läuft die Sperrfrist des Kerns ab (3 Minuten nach dem Sperren).
  4. Kern: Gesprächsdaten aller Laborprofile löschen (wamo_labor.py loeschen --alle, die Funktion labor_loeschen; das Skript wartet die Sperrfrist selbst
     ab und prüft danach auf null).
  5. Zählung danach (nur lesen). Der Nachweis gilt nur, wenn im Kern und im Dienst alles null ist und kein Laborprofil freigeschaltet ist.
Am Ende stehen die Erinnerungen an die Handarbeit, die kein Skript übernimmt (Umschlag-Datei und ihre Sicherungen, Platz-Skript je Laptop, Löschlauf am
Server, Laborkonten).

Aufrufe (im Repo-Hauptordner, nur Standardbibliothek):
  python scripts/wamo_labor_nachlauf.py nachlauf                    # Trockenlauf: Plan, Zählung davor (nur lesen), Erinnerungen, nichts wird geändert
  python scripts/wamo_labor_nachlauf.py nachlauf --ausfuehren       # führt die Schritte aus, frühestens am 28.11.2026
Optionen: --schema wamo_dev|wamo_test|wamo_prod (Standard wamo_dev, wie bei wamo_labor.py), --host (Standard WAMO_SSH_HOST oder wamo-sokrates2),
--protokoll DATEI (hängt jede Zeile mit Zeit an, nie im Repo), --umschlag-datei (Standard %USERPROFILE%\\.wamo-labor\\umschlag-labor.txt, es werden nur
die Namen der Dateien im Ordner genannt), --vor-dem-labortag (hebt die Sperre vor dem 28.11.2026 auf, zum Beispiel für eine Übung am Testschema).

Wege zum Server (es gibt keine neuen): Der Kern wird wie bei wamo_labor.py über ssh und die Anmeldung der Rolle wamo_admin angesprochen
(wamo_labor.run_remote, Voraussetzung WAMO_ADMIN_DATABASE_URL). Der Dienst wird über seine Betreuer-Schnittstelle auf 127.0.0.1:18765 am Server
angesprochen, und zwar von einem kleinen Programm, das per ssh und sudo -n auf dem Server läuft. Der Betreuer-Token (/etc/wamo-labor/betreuer-token) wird
nur dort gelesen und nur dort benutzt: Er verlässt den Server nie, steht in keiner Befehlszeile und in keiner Ausgabe, und das Programm kennt nur die
festen Aufgaben plaetze, durchlauf-beenden und daten-loeschen für die Plätze 1 bis 16 (nie neuer-durchlauf oder platz-token).

Ausgänge: 0 alles in Ordnung (oder Trockenlauf gelungen), 1 ein Schritt oder der Nachweis ist gescheitert (der Lauf ist wiederholbar, Sperren und Löschen
schaden nie), 2 Aufruf oder Einstellung falsch (nichts wurde am Server angefasst), 130 Abbruch mit Strg+C.
Geheimnisse und Texte: Die Ausgabe und die Protokolldatei enthalten nur Zählungen, Plätze und Schrittnamen, nie ein Passwort, einen Token oder einen
Text aus einem Gespräch oder einem Ticket. Es erscheint nie ein Traceback.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor as wl  # noqa: E402
import wamo_labor_faktoren_umschlag as um  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
EXIT_OK, EXIT_FEHLER, EXIT_EINGABE, EXIT_ABBRUCH = 0, 1, 2, 130
LABORTAG = date(2026, 11, 28)
PLAETZE = tuple(range(1, 17))
SCHEMA_RE = re.compile(r"wamo_(dev|test|prod)")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")  # kein führender Strich: ssh hielte ihn für eine Option
STATUS_RE = re.compile(r"(labor-(?:0[1-9]|1[0-6]|schau)) \| (?:platz|schaufenster) \| (freigeschaltet|gesperrt) \| (\d{1,9}) Zeilen")
SICHERUNG_RE = r"(\.neu|\.sicherung-\d{8}-\d{6}(-\d+)?)"
DIENST_VERSUCHE = 3
SSH_FRIST_S = 180
MAX_AUSGABE = 70000
UNERWARTET_DIENST = "Die Antwort des Dienstes ist unerwartet (kein gültiges Ergebnis der Betreuer-Schnittstelle)."
TITEL = (
    "Zählung davor (nur lesen)",
    "Kern: alle Laborprofile sperren (labor_aktiv = false)",
    "Dienst: 16 Plätze, je Durchlauf beenden und Daten löschen",
    "Kern: Gesprächsdaten aller Laborprofile löschen (labor_loeschen, wartet die Sperrfrist von 3 Minuten ab)",
    "Zählung danach (nur lesen) und Nachweis",
)
SCHRITTE = len(TITEL)

# Das Programm, das auf Sokrates-2 läuft (python3 über sudo -n, Eingabe als JSON über stdin, Ausgabe eine Zeile JSON). Es liest den Betreuer-Token
# nur dort, ruft nur die drei festen Aufgaben auf, folgt keiner Umleitung, nutzt keinen Proxy und gibt nur Zählungen und Fehlercodes zurück.
# Meldungen sind ASCII, damit die Zeichenkodierung des Servers nie stört. verarbeite() ist von der Außenwelt getrennt (Öffner und Tokendatei werden
# hineingereicht), damit der Test es ohne Server ausführen kann.
REMOTE_DIENST = r'''
import json, re, sys, urllib.error, urllib.request

TOKEN_DATEI = "/etc/wamo-labor/betreuer-token"
BASIS = "http://127.0.0.1:18765"
TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{43,200}")
AKTIONEN = ("durchlauf-beenden", "daten-loeschen")
PLAETZE = range(1, 17)
FRIST_LESEN, FRIST_SCHREIBEN, MAX_ANTWORT = 15, 90, 65536


class Fehler(Exception):
    pass


class KeineUmleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def oeffner_bauen():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), KeineUmleitung())
    return lambda anfrage, frist: opener.open(anfrage, timeout=frist)


def token_lesen(pfad):
    try:
        with open(pfad, encoding="utf-8") as datei:
            token = datei.read().strip()
    except OSError:
        raise Fehler("Der Betreuer-Token ist auf dem Server nicht lesbar (sudo ohne Passwort noetig)")
    if not TOKEN_RE.fullmatch(token):
        raise Fehler("Der Betreuer-Token auf dem Server hat nicht die erwartete Form")
    return token


def aufgabe(d):
    name, platz = d.get("aktion"), d.get("platz")
    if name == "plaetze":
        return "GET", "/api/betreuer/plaetze", FRIST_LESEN
    if name in AKTIONEN and type(platz) is int and platz in PLAETZE:
        return "POST", "/api/betreuer/plaetze/%d/%s" % (platz, name), FRIST_SCHREIBEN
    raise Fehler("Ungueltige Aufgabe")


def zaehlung(p):
    try:
        return {"platz": int(p["platz"]), "offen": bool(p["offen"]), "tickets": int(p["tickets"]), "kommentare": int(p["kommentare"]),
                "einwilligung": p.get("einwilligung_version") is not None, "platz_token": bool(p.get("hat_platz_token"))}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Fehler("Die Antwort des Dienstes ist unerwartet")


def senden(methode, pfad, token, frist, oeffner):
    anfrage = urllib.request.Request(BASIS + pfad, method=methode, data=b"{}" if methode == "POST" else None,
                                     headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json"})
    try:
        antwort = oeffner(anfrage, frist)
        status, roh = antwort.status, antwort.read(MAX_ANTWORT)
    except urllib.error.HTTPError as e:
        status, roh = e.code, e.read(MAX_ANTWORT)
    except (urllib.error.URLError, OSError):
        raise Fehler("Der Dienst antwortet nicht auf 127.0.0.1:18765")
    try:
        daten = json.loads(roh.decode("utf-8")) if roh else {}
    except ValueError:
        daten = {}
    return status, daten if isinstance(daten, dict) else {}


def ergebnis(methode, status, daten):
    aus = {"status": status}
    if status != 200:
        code = daten.get("fehler")
        if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,40}", code):
            aus["fehler"] = code
    elif methode == "GET":
        aus["plaetze"] = [zaehlung(p) for p in daten.get("plaetze", [])]
    else:
        aus["platz"] = zaehlung(daten)
    return aus


def verarbeite(d, token_datei=TOKEN_DATEI, oeffner=None):
    token = ""
    try:
        methode, pfad, frist = aufgabe(d)
        token = token_lesen(token_datei)
        status, daten = senden(methode, pfad, token, frist, oeffner or oeffner_bauen())
        print(json.dumps(ergebnis(methode, status, daten)))
        return 0
    except Fehler as e:
        text = str(e)
        print("Fehler: " + (text.replace(token, "***") if token else text), file=sys.stderr)
        return 1


def haupt():
    try:
        d = json.load(sys.stdin)
    except ValueError:
        d = None
    if not isinstance(d, dict):
        print("Fehler: Die Eingabe ist kein JSON-Objekt", file=sys.stderr)
        return 1
    return verarbeite(d)


if __name__ == "__main__":
    sys.exit(haupt())
'''


class NachlaufFehler(Exception):
    """Ein Schritt oder der Nachweis ist gescheitert. Die Meldung ist für die Person gedacht und nennt nie ein Geheimnis oder einen Text."""


class EingabeFehler(Exception):
    """Aufruf oder Einstellung falsch: Es wurde nichts am Server angefasst."""


@dataclass(frozen=True)
class KernStand:
    profile: int
    freigeschaltet: int
    zeilen: int


@dataclass(frozen=True)
class DienstStand:
    plaetze: int
    offen: int
    tickets: int
    kommentare: int
    einwilligungen: int
    tokens: int


# ---------- Zählung lesen ----------
def _kurz(text: str, grenze: int = 300) -> str:
    """Ein Text für die Ausgabe: gekürzt, ohne Steuerzeichen."""
    return "".join(c if c.isprintable() else " " for c in (text or "").strip())[:grenze]


def kern_stand_lesen(text: str) -> KernStand:
    """Liest die Ausgabe von wamo_labor.py status. Unerwartetes wird nie angezeigt (es könnte ein Text sein)."""
    zeilen = [z.strip() for z in text.strip().splitlines() if z.strip()]
    if zeilen == ["Keine Laborprofile."]:
        return KernStand(0, 0, 0)
    treffer = []
    for nr, zeile in enumerate(zeilen, 1):
        passt = STATUS_RE.fullmatch(zeile)
        if not passt:
            raise NachlaufFehler(f"Die Antwort des Servers zur Zählung im Kern ist unerwartet (Zeile {nr}, nicht angezeigt).")
        treffer.append(passt)
    if not treffer:
        raise NachlaufFehler("Die Antwort des Servers zur Zählung im Kern ist leer.")
    return KernStand(len(treffer), sum(t[2] == "freigeschaltet" for t in treffer), sum(int(t[3]) for t in treffer))


def _zahl(platz: dict, name: str) -> int:
    wert = platz.get(name)
    if type(wert) is not int or wert < 0:
        raise NachlaufFehler(UNERWARTET_DIENST)
    return wert


def _flag(platz: dict, name: str) -> bool:
    wert = platz.get(name)
    if type(wert) is not bool:
        raise NachlaufFehler(UNERWARTET_DIENST)
    return wert


def dienst_stand_lesen(plaetze: object) -> DienstStand:
    """Liest die Zählung der Plätze (Antwort des Serverprogramms): genau 16 Plätze, nur Zahlen und Wahrheitswerte."""
    if not isinstance(plaetze, list) or not all(isinstance(p, dict) for p in plaetze):
        raise NachlaufFehler(UNERWARTET_DIENST)
    if sorted(p.get("platz") if type(p.get("platz")) is int else 0 for p in plaetze) != list(PLAETZE):
        raise NachlaufFehler(UNERWARTET_DIENST)
    return DienstStand(
        plaetze=len(plaetze), offen=sum(_flag(p, "offen") for p in plaetze), tickets=sum(_zahl(p, "tickets") for p in plaetze),
        kommentare=sum(_zahl(p, "kommentare") for p in plaetze), einwilligungen=sum(_flag(p, "einwilligung") for p in plaetze),
        tokens=sum(_flag(p, "platz_token") for p in plaetze))


def kern_text(s: KernStand) -> str:
    return f"Kern: {s.profile} Laborprofile, {s.freigeschaltet} freigeschaltet, {s.zeilen} Zeilen"


def dienst_text(s: DienstStand) -> str:
    return (f"Dienst: {s.plaetze} Plätze, {s.offen} offen, {s.tickets} Tickets, {s.kommentare} Kommentare, {s.einwilligungen} Einwilligungen, "
            f"{s.tokens} Platz-Token")


# ---------- Wege zum Server ----------
def dienst_remote(host: str, nutzlast: dict) -> wl.Ergebnis:
    """Ruft das Serverprogramm über ssh und sudo -n auf. Der Betreuer-Token wird nur dort gelesen, hier gibt es keinen."""
    programm = base64.b64encode(REMOTE_DIENST.encode("utf-8")).decode("ascii")
    befehl = f"sudo -n python3 -c \"import base64;exec(base64.b64decode('{programm}').decode('utf-8'))\""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, befehl], input=json.dumps(nutzlast),
                           capture_output=True, text=True, encoding="utf-8", timeout=SSH_FRIST_S)
    except subprocess.TimeoutExpired:
        return wl.Ergebnis(1, "", f"Zeitüberschreitung: Der Weg zum Dienst antwortete nicht binnen {SSH_FRIST_S} Sekunden.")
    except OSError as e:
        return wl.Ergebnis(1, "", f"ssh ließ sich nicht starten ({type(e).__name__}).")
    return wl.Ergebnis(r.returncode, r.stdout[:MAX_AUSGABE], r.stderr[:MAX_AUSGABE])


@dataclass
class Zugriff:
    """Beide Wege zum Server und ihre Einstellungen. Die Wege sind austauschbar (Tests setzen Attrappen ein)."""
    host: str
    schema: str
    kern: Callable[[str, dict], wl.Ergebnis]
    dienst: Callable[[str, dict], wl.Ergebnis]
    schlafen: Callable[[float], None]

    def kern_aufruf(self, aktion: str, **felder: object) -> str:
        try:
            e = self.kern(self.host, {"aktion": aktion, "schema": self.schema, **felder})
        except wl.Fehler as fehler:
            raise NachlaufFehler(_kurz(str(fehler))) from None
        except OSError as fehler:
            raise NachlaufFehler(f"Der Weg zum Server ließ sich nicht öffnen ({type(fehler).__name__}), ist ssh installiert?") from None
        if e.code != 0:
            raise NachlaufFehler(_kurz(e.stderr) or f"Der Server meldete einen Fehler (Ausgang {e.code}).")
        return e.stdout.strip()

    def dienst_aufruf(self, aktion: str, platz: int | None = None) -> dict:
        nutzlast = {"aktion": aktion} if platz is None else {"aktion": aktion, "platz": platz}
        e = self.dienst(self.host, nutzlast)
        if e.code != 0:
            hinweis = " Am Server fehlt vermutlich sudo ohne Passwort für python3 (sudo -n)." if "sudo:" in e.stderr.lower() else ""
            raise NachlaufFehler((_kurz(e.stderr) or f"Der Weg zum Dienst meldete einen Fehler (Ausgang {e.code}).") + hinweis)
        try:
            daten = json.loads(e.stdout)
        except ValueError:
            raise NachlaufFehler(UNERWARTET_DIENST) from None
        if not isinstance(daten, dict) or type(daten.get("status")) is not int:
            raise NachlaufFehler(UNERWARTET_DIENST)
        return daten


# ---------- Protokoll ----------
class Protokoll:
    """Gibt jede Zeile aus und hängt sie, wenn gewünscht, mit Zeit an eine Datei an. Merkt sich den laufenden Schritt für Fehlermeldungen."""

    def __init__(self, ausgabe: Callable[[str], None], datei: Path | None, jetzt: Callable[[], datetime]):
        self.ausgabe, self.datei, self.jetzt = ausgabe, datei, jetzt
        self.schritt = 0

    def _datei(self, zeilen: list[str]) -> None:
        if self.datei is None:
            return
        stempel = self.jetzt().isoformat(timespec="seconds")
        with open(self.datei, "a", encoding="utf-8") as f:
            f.writelines(f"{stempel} {z}\n" for z in zeilen)

    def zeile(self, text: str) -> None:
        zeilen = text.splitlines() or [""]
        for z in zeilen:
            self.ausgabe(z)
        self._datei(zeilen)

    def fehler(self, text: str) -> None:
        print(text, file=sys.stderr)
        self._datei(text.splitlines())

    def kopf(self, nr: int) -> None:
        self.schritt = nr
        self.zeile(f"Schritt {nr} von {SCHRITTE}: {TITEL[nr - 1]}")


# ---------- Schritte ----------
def zaehlung(z: Zugriff, p: Protokoll, vorspann: str) -> tuple[KernStand, DienstStand]:
    kern = kern_stand_lesen(z.kern_aufruf("status", ausfuehren=False))
    antwort = z.dienst_aufruf("plaetze")
    if antwort["status"] != 200:
        raise NachlaufFehler(f"Der Dienst antwortet auf die Zählung mit Status {antwort['status']}.")
    dienst = dienst_stand_lesen(antwort.get("plaetze"))
    p.zeile(f"{vorspann}{kern_text(kern)}. {dienst_text(dienst)}.")
    return kern, dienst


def _dienst_fehlertext(platz: int, aktion: str, status: int) -> str:
    wo = f"Platz {platz}, {aktion}"
    if status in (401, 403):
        return f"{wo}: Der Betreuer-Token am Server wurde vom Dienst nicht angenommen (Status {status})."
    if status == 404:
        return f"{wo}: Der Platz ist dem Dienst nicht bekannt (Status 404)."
    if status == 503:
        return f"{wo}: Die Datei ist nach {DIENST_VERSUCHE} Versuchen nicht bereinigt (Status 503). Den Lauf später erneut starten."
    return f"{wo}: Der Dienst antwortet mit Status {status}."


def _platz_schritt(z: Zugriff, p: Protokoll, platz: int, aktion: str, erfolg: str) -> None:
    for versuch in range(1, DIENST_VERSUCHE + 1):
        status = z.dienst_aufruf(aktion, platz)["status"]
        if status == 200:
            p.zeile(f"  Platz {platz}: {erfolg}")
            return
        if status == 503 and aktion == "daten-loeschen" and versuch < DIENST_VERSUCHE:
            p.zeile(f"  Platz {platz}: Datei noch nicht bereinigt, neuer Versuch {versuch + 1} von {DIENST_VERSUCHE}")
            z.schlafen(2)
            continue
        raise NachlaufFehler(_dienst_fehlertext(platz, aktion, status))


def kern_sperren(z: Zugriff, p: Protokoll) -> None:
    p.kopf(2)
    for zeile in z.kern_aufruf("sperren", alle=True, ausfuehren=True).splitlines()[:5]:
        p.zeile("  " + _kurz(zeile, 120))


def dienst_leeren(z: Zugriff, p: Protokoll) -> None:
    p.kopf(3)
    for platz in PLAETZE:
        _platz_schritt(z, p, platz, "durchlauf-beenden", "Durchlauf beendet")
        _platz_schritt(z, p, platz, "daten-loeschen", "Daten gelöscht")


def kern_loeschen(z: Zugriff, p: Protokoll) -> None:
    p.kopf(4)
    p.zeile("  Das kann bis zu etwa 7 Minuten dauern (Sperrfrist von 3 Minuten nach dem Sperren, das Skript wartet sie selbst ab).")
    for zeile in z.kern_aufruf("loeschen", alle=True, ausfuehren=True).splitlines()[:40]:
        p.zeile("  " + _kurz(zeile, 120))


def nachweis_pruefen(kern: KernStand, dienst: DienstStand) -> None:
    offen = []
    if kern.freigeschaltet:
        offen.append(f"{kern.freigeschaltet} Laborprofile sind noch freigeschaltet")
    if kern.zeilen:
        offen.append(f"im Kern sind noch {kern.zeilen} Zeilen")
    for zahl, name in ((dienst.offen, "offene Durchläufe"), (dienst.tickets, "Tickets"), (dienst.kommentare, "Kommentare"),
                       (dienst.einwilligungen, "Einwilligungen"), (dienst.tokens, "Platz-Token")):
        if zahl:
            offen.append(f"im Dienst noch {zahl} {name}")
    if offen:
        raise NachlaufFehler("Nachweis nicht erbracht: " + "; ".join(offen) + ". Den Lauf wiederholen, bleibt es so, Niko melden.")


# ---------- Plan und Erinnerungen ----------
def plan_zeilen(z: Zugriff, ausfuehren: bool) -> list[str]:
    kopf = f"WAMO Labor-Nachlauf, Schema {z.schema}, Host {z.host}"
    zeilen = [kopf + ("" if ausfuehren else " (Trockenlauf, es wird nichts geändert)"), "Plan:"]
    zeilen += [f"  {nr}. {titel}" for nr, titel in enumerate(TITEL, 1)]
    return zeilen


def _umschlag_dateien(datei: Path) -> list[str]:
    ordner = datei.parent
    if not ordner.is_dir():
        return []
    muster = re.compile(re.escape(datei.name) + "(" + SICHERUNG_RE + ")?")
    try:
        return sorted(p.name for p in ordner.iterdir() if p.is_file() and muster.fullmatch(p.name))
    except OSError:
        return []


def erinnerungen(umschlag_datei: Path) -> list[str]:
    namen = _umschlag_dateien(umschlag_datei)
    gefunden = f"Gefunden in {umschlag_datei.parent} (nur Namen): {', '.join(namen)}." if namen else f"In {umschlag_datei.parent} wurden keine Dateien gefunden."
    return [
        "Erinnerungen (Handarbeit, nicht Teil dieses Laufs):",
        "  1. Umschlag-Datei und ihre Sicherungen enthalten Passwörter und Seeds der Laborprofile. Auf dem PC löschen, sobald die Profile nicht mehr gebraucht",
        f"     werden. {gefunden}",
        "  2. Platz-Skript auf jedem Laptop, im Kiosk-Konto, Chrome vorher schließen: wamo_labor_platz.py rueckweg --profil-loeschen",
        "     (nimmt Richtlinien, Zertifikat und Verknüpfungen zurück und löscht das Chrome-Profil mit der angemeldeten Sitzung des Platzes).",
        "  3. Löschlauf am Server, Frist für alle Daten ist der 11.12.2026:",
        "     ssh wamo-sokrates2 \"sudo bash ~/wamo-infra/wamo-labor-loeschlauf.sh --bestaetigung LABOR-LOESCHEN\"",
        "     (Container, Token, Image und Kern-Einstellungen mit Nachweis; das Skript lässt ihn erst ab dem 12.12.2026 zu, vorher nur mit --vor-der-frist.",
        "     Snapshots und Sicherungen des Servers über den 11.12.2026 hinaus von Hand prüfen.)",
        "  4. Laborkonten löschen und alle Sitzungen widerrufen (Entscheidung Q11, Handgriff am Server, dafür gibt es noch kein Skript).",
    ]


# ---------- Ablauf ----------
def lauf(z: Zugriff, p: Protokoll, *, ausfuehren: bool, umschlag: Path) -> int:
    for zeile in plan_zeilen(z, ausfuehren):
        p.zeile(zeile)
    p.kopf(1)
    kern, dienst = zaehlung(z, p, "Nachweis davor:  ")
    if kern.profile == 0:
        raise NachlaufFehler(f"Es gibt keine Laborprofile im Schema {z.schema} (falsches Schema?).")
    if ausfuehren:
        kern_sperren(z, p)
        dienst_leeren(z, p)
        kern_loeschen(z, p)
        p.kopf(5)
        kern, dienst = zaehlung(z, p, "Nachweis danach: ")
        nachweis_pruefen(kern, dienst)
        p.zeile("Ergebnis: Nachweis erbracht (im Kern und im Dienst ist alles null, kein Laborprofil ist freigeschaltet).")
    else:
        p.zeile("Trockenlauf: Die Schritte 2 bis 5 wurden nicht ausgeführt. Zum Ausführen --ausfuehren angeben.")
    for zeile in erinnerungen(umschlag):
        p.zeile(zeile)
    return EXIT_OK


def _protokoll_pfad(text: str | None) -> Path | None:
    if not text:
        return None
    datei = Path(text).expanduser().resolve()
    if datei == WURZEL or WURZEL in datei.parents:
        raise EingabeFehler("Die Protokolldatei darf nicht im Repo liegen.")
    if not datei.parent.is_dir():
        raise EingabeFehler("Der Ordner der Protokolldatei existiert nicht.")
    return datei


def vorbereiten(args: argparse.Namespace, kern: Callable, dienst: Callable, heute: Callable[[], date], schlafen: Callable,
                jetzt: Callable[[], datetime]) -> tuple[Zugriff, Protokoll, Path]:
    """Prüft alle Angaben, bevor der Server angefasst wird."""
    if not SCHEMA_RE.fullmatch(args.schema):
        raise EingabeFehler("Das Schema muss wamo_dev, wamo_test oder wamo_prod sein.")
    if not HOST_RE.fullmatch(args.host):
        raise EingabeFehler("Der Host ist ungültig (nur Buchstaben, Ziffern, Punkt, Strich und Unterstrich, kein Strich am Anfang).")
    if args.ausfuehren and heute() < LABORTAG and not args.vor_dem_labortag:
        raise EingabeFehler("Ausführen ist erst ab dem Labortag, dem 28.11.2026, erlaubt (zum Üben am Testschema: --vor-dem-labortag).")
    protokoll = Protokoll(print, _protokoll_pfad(args.protokoll), jetzt)
    umschlag = Path(args.umschlag_datei).expanduser() if args.umschlag_datei else um.standard_pfad()
    return Zugriff(args.host, args.schema, kern, dienst, schlafen), protokoll, umschlag


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wamo_labor_nachlauf", description="Nachlauf des Testlabors (Standard: Trockenlauf)")
    sub = p.add_subparsers(dest="befehl", required=True)
    n = sub.add_parser("nachlauf", help="Laborprofile sperren, Daten im Kern und in den 16 Plätzen des Dienstes löschen, Zählung als Nachweis")
    n.add_argument("--ausfuehren", action="store_true", help="wirklich ausführen (ohne dies nur Trockenlauf)")
    n.add_argument("--schema", default="wamo_dev")
    n.add_argument("--host", default=os.environ.get("WAMO_SSH_HOST", "wamo-sokrates2"))
    n.add_argument("--vor-dem-labortag", action="store_true", help="hebt die Sperre vor dem 28.11.2026 auf (zum Üben am Testschema)")
    n.add_argument("--protokoll", help="Datei, an die jede Zeile mit Zeit angehängt wird (nie im Repo)")
    n.add_argument("--umschlag-datei", help="Standard %%USERPROFILE%%\\.wamo-labor\\umschlag-labor.txt (es werden nur Dateinamen genannt)")
    return p


def _utf8() -> None:
    for strom in (sys.stdout, sys.stderr):
        try:
            strom.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _abbruch(p: Protokoll, text: str) -> None:
    ort = f"bei Schritt {p.schritt} von {SCHRITTE}" if p.schritt else "vor dem ersten Schritt"
    p.fehler(f"Abbruch {ort}: {text}")
    if p.schritt <= 1:
        p.fehler("Es wurde nichts geändert.")
    else:
        p.fehler("Der Lauf ist wiederholbar: Sperren und Löschen schaden nie, ein zweiter Lauf macht dort weiter, wo etwas fehlt.")


def main(argv: list[str] | None = None, *, kern: Callable = wl.run_remote, dienst: Callable = dienst_remote, heute: Callable[[], date] = date.today,
         schlafen: Callable = time.sleep, jetzt: Callable[[], datetime] = datetime.now) -> int:
    _utf8()
    args = _parser().parse_args(argv)
    try:
        z, p, umschlag = vorbereiten(args, kern, dienst, heute, schlafen, jetzt)
    except EingabeFehler as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return EXIT_EINGABE
    try:
        return lauf(z, p, ausfuehren=args.ausfuehren, umschlag=umschlag)
    except NachlaufFehler as e:
        _abbruch(p, str(e))
        return EXIT_FEHLER
    except KeyboardInterrupt:
        p.fehler(f"Abgebrochen (Strg+C) bei Schritt {p.schritt} von {SCHRITTE}. Der Lauf ist wiederholbar.")
        return EXIT_ABBRUCH
    except Exception as e:  # noqa: BLE001 (nie ein Traceback: eine Einzelheit könnte ein Geheimnis oder ein Text sein)
        _abbruch(p, f"Unerwarteter Fehler ({type(e).__name__}), Einzelheiten werden nicht ausgegeben.")
        return EXIT_FEHLER


if __name__ == "__main__":
    sys.exit(main())
