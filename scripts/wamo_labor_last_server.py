"""Wege des Lasttests zum Server (scripts/wamo_labor_last.py, nur Standardbibliothek): lesende Messung am Kern und feste Aufgaben am Labor-Dienst.

Es gibt zwei Wege, beide über ssh auf Sokrates-2 (SSH-Alias wamo-sokrates2), beide mit einem kleinen Programm, das am Server läuft (Eingabe als
JSON über stdin, Ausgabe eine Zeile JSON):

  Messung (nur lesend, ohne sudo): Das Programm liest die cgroup des Kerns (CPU-Zeit, Speicher, Spitze des Speichers, Zahl der Aufgaben) und
     zählt im Journal des Kerns, was die Auswertung braucht: 429 und Soniox-Fehler, Anfragen an das Modell, Unterbrechungen, Fehler der Pipeline, dazu
     (falls der Kern Latenzzeilen schreibt) die Tokens. Es gibt nur Zahlen zurück, nie eine Journalzeile. Ist das Journal nicht lesbar (weder mit
     noch ohne sudo -n), meldet es das ausdrücklich: Ein unlesbares Journal gilt nie als "kein 429".
  Dienst (Betreuer-Schnittstelle auf 127.0.0.1:18765 am Server, Muster wie scripts/wamo_labor_nachlauf.py): Das Programm läuft über sudo -n, liest den
     Betreuer-Token nur dort und gibt nur den Status und einen Fehlercode zurück. Es kennt genau vier Aufgaben: plaetze (lesen), neuer-durchlauf mit
     Einwilligungsversion, durchlauf-beenden und daten-loeschen, die drei schreibenden nur für die Plätze 1 bis 8. Nie ein Platz-Token, nie ein Text.

Das Programm für den Server steht als Zeichenkette in diesem Modul, damit die Tests es in-process mit Attrappen und einmal mit dem echten
Interpreter ausführen können. Alle Meldungen des Programms sind ASCII, damit die Zeichenkodierung des Servers nie stört.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

import wamo_labor as wl

HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")  # kein führender Strich: ssh hielte ihn für eine Option
MESS_FRIST_S = 45
DIENST_FRIST_S = 120
MAX_AUSGABE = 70000
UNERWARTET = "Die Antwort des Servers ist unerwartet (kein gültiges Ergebnis)."
ZUGRIFF = Callable[[str, str, str, float], wl.Ergebnis]

# Das Programm der Messung am Server. messen() ist von der Außenwelt getrennt (Dateisystem, Journal und Uhr werden hineingereicht).
REMOTE_MESSUNG = r'''
import json, os, re, subprocess, sys, time

CGROUP_WURZEL = "/sys/fs/cgroup/system.slice"
SLOTS = ("a", "b")
MAX_ZEILEN = 200000
JOURNAL_FRIST_S = 40
R429 = re.compile(r"(?i)error code: 429|rate_limit_error|ratelimiterror|too many requests|status(?:_code)?\W{1,3}429\b|http/\d(?:\.\d)? 429\b")
SONIOX = re.compile(r"(?i)soniox")
SONIOX_FEHLER = re.compile(r"(?i)error|fehler|exception|failed|fehlgeschlagen|timeout|konnte nicht|disconnect")
VERWEIGERT = ("insufficient permissions", "not seeing messages from other users")


class Fehler(Exception):
    pass


def zahl(pfad):
    try:
        with open(pfad, encoding="ascii") as datei:
            text = datei.read().strip()
    except (OSError, UnicodeError):
        return None
    return int(text) if text.isdigit() else None


def cpu_usec(pfad):
    try:
        with open(pfad, encoding="ascii") as datei:
            for zeile in datei:
                name, _, wert = zeile.partition(" ")
                if name == "usage_usec" and wert.strip().isdigit():
                    return int(wert)
    except (OSError, UnicodeError):
        pass
    return None


def cgroup_lesen(ordner):
    return {"cpu_usec": cpu_usec(os.path.join(ordner, "cpu.stat")), "speicher": zahl(os.path.join(ordner, "memory.current")),
            "speicher_spitze": zahl(os.path.join(ordner, "memory.peak")), "aufgaben": zahl(os.path.join(ordner, "pids.current"))}


def journal_befehl(slot, seit, bis, sudo):
    cmd = ["journalctl", "-u", "wamo-core@%s.service" % slot, "--since=@%d" % int(seit), "--until=@%d" % (int(bis) + 1),
           "-o", "short-unix", "--no-pager", "-n", str(MAX_ZEILEN)]
    return ["sudo", "-n"] + cmd if sudo else cmd


def journal_holen(slot, seit, bis, starten=subprocess.Popen):
    """Das Journal der Instanz, nur lesend: erst ohne, dann mit sudo -n. (lesbar, Zeilen); lesbar ist falsch, wenn beide Wege scheitern."""
    for sudo in (False, True):
        try:
            prozess = starten(journal_befehl(slot, seit, bis, sudo), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, errors="replace")
            aus, fehler = prozess.communicate(timeout=JOURNAL_FRIST_S)
        except subprocess.TimeoutExpired:
            prozess.kill()
            return False, iter(())
        except OSError:
            return False, iter(())
        if prozess.returncode == 0 and not any(v in fehler for v in VERWEIGERT):
            return True, iter(aus.splitlines())
    return False, iter(())


def token_zaehlen(z, minute, text):
    try:
        daten = json.loads(text.split("LATENZ_TOKEN ", 1)[1])
    except ValueError:
        return
    namen = ("eingabe", "cache_lesen", "cache_schreiben")
    if not isinstance(daten, dict) or any(type(daten.get(n)) is not int or daten[n] < 0 for n in namen):
        return
    z["token_zuege"] += 1
    z["token_eingabe"] += daten["eingabe"]
    z["token_cache_lesen"] += daten["cache_lesen"]
    z["token_cache_schreiben"] += daten["cache_schreiben"]
    minute()["eingabe"] += daten["eingabe"]


def zeile_zaehlen(z, ts, text):
    def minute():
        return z["minuten"].setdefault(str(int(ts // 60) * 60), {"anfragen": 0, "eingabe": 0})
    if "Pipeline: Anfrage an das Modell beginnt" in text:
        z["anfragen"] += 1
        minute()["anfragen"] += 1
    if "Pipeline: Unterbrechung" in text:
        z["unterbrechungen"] += 1
    if "Pipeline: Fehler von" in text:
        z["pipeline_fehler"] += 1
    if R429.search(text):
        z["r429"] += 1
    if SONIOX.search(text) and SONIOX_FEHLER.search(text):
        z["soniox"] += 1
    if "LATENZ_TOKEN " in text:
        token_zaehlen(z, minute, text)


def zaehlen(zeilen, seit, bis):
    z = {"zeilen": 0, "r429": 0, "soniox": 0, "anfragen": 0, "unterbrechungen": 0, "pipeline_fehler": 0, "token_zuege": 0,
         "token_eingabe": 0, "token_cache_lesen": 0, "token_cache_schreiben": 0, "abgeschnitten": False, "minuten": {}}
    for zeile in zeilen:
        if z["zeilen"] >= MAX_ZEILEN:
            z["abgeschnitten"] = True
            break
        stempel, _, text = zeile.partition(" ")
        try:
            ts = float(stempel)
        except ValueError:
            continue
        if seit < ts <= bis:
            z["zeilen"] += 1
            zeile_zaehlen(z, ts, text)
    return z


def zeitpunkt(wert, jetzt):
    if type(wert) not in (int, float) or not 0 < wert <= jetzt:
        raise Fehler("Ungueltiger Zeitpunkt")
    return float(wert)


def messen(d, wurzel, journal, jetzt):
    slot = d.get("slot")
    if slot not in SLOTS:
        raise Fehler("Ungueltiger Slot")
    ordner = os.path.join(wurzel, "wamo-core@%s.service" % slot)
    zeit = jetzt()
    aus = {"zeit": zeit, "aktiv": os.path.isdir(ordner), "kerne": os.cpu_count() or 1, "journal": None, "journal_lesbar": None}
    aus.update(cgroup_lesen(ordner))
    if d.get("seit") is not None:
        seit = zeitpunkt(d["seit"], zeit)
        lesbar, zeilen = journal(slot, seit, zeit)
        aus["journal_lesbar"] = bool(lesbar)
        if lesbar:
            aus["journal"] = zaehlen(zeilen, seit, zeit)
    return aus


def verarbeite(d, wurzel=None, journal=None, jetzt=None):
    try:
        print(json.dumps(messen(d, wurzel or CGROUP_WURZEL, journal or journal_holen, jetzt or time.time)))
        return 0
    except Fehler as e:
        print("Fehler: " + str(e), file=sys.stderr)
    except Exception as e:
        print("Fehler: Unerwarteter Fehler (" + type(e).__name__ + ")", file=sys.stderr)
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

# Das Programm für den Dienst am Server (Muster: scripts/wamo_labor_nachlauf.py, REMOTE_DIENST). Es kennt nur die festen Aufgaben, folgt keiner
# Umleitung, nutzt keinen Proxy und gibt nur Status und Fehlercode zurück.
REMOTE_DIENST = r'''
import json, re, sys, urllib.error, urllib.request

TOKEN_DATEI = "/etc/wamo-labor/betreuer-token"
BASIS = "http://127.0.0.1:18765"
TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{43,200}")
AKTIONEN = ("durchlauf-beenden", "daten-loeschen")
PLAETZE = range(1, 9)
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
    except (OSError, UnicodeError):
        raise Fehler("Der Betreuer-Token ist auf dem Server nicht lesbar (sudo ohne Passwort noetig)")
    if not TOKEN_RE.fullmatch(token):
        raise Fehler("Der Betreuer-Token auf dem Server hat nicht die erwartete Form")
    return token


def aufgabe(d):
    name, platz, version = d.get("aktion"), d.get("platz"), d.get("version")
    if name == "plaetze":
        return "GET", "/api/betreuer/plaetze", None, FRIST_LESEN
    if type(platz) is int and platz in PLAETZE:
        if name in AKTIONEN:
            return "POST", "/api/betreuer/plaetze/%d/%s" % (platz, name), {}, FRIST_SCHREIBEN
        if name == "neuer-durchlauf" and type(version) is int and 1 <= version <= 99:
            return "POST", "/api/betreuer/plaetze/%d/neuer-durchlauf" % platz, {"einwilligung_version": version}, FRIST_SCHREIBEN
    raise Fehler("Ungueltige Aufgabe")


def zaehlung(p):
    """Streng: Jedes Feld muss da sein und den richtigen Typ haben. Ein fehlendes Feld darf nie als null oder falsch gelesen werden."""
    try:
        if not (type(p["platz"]) is int and type(p["tickets"]) is int and type(p["kommentare"]) is int
                and type(p["offen"]) is bool and type(p["hat_platz_token"]) is bool and "einwilligung_version" in p):
            raise ValueError
        return {"platz": p["platz"], "offen": p["offen"], "tickets": p["tickets"], "kommentare": p["kommentare"],
                "einwilligung": p["einwilligung_version"] is not None, "platz_token": p["hat_platz_token"]}
    except (KeyError, TypeError, ValueError):
        raise Fehler("Die Antwort des Dienstes ist unerwartet")


def senden(methode, pfad, daten, token, frist, oeffner):
    anfrage = urllib.request.Request(BASIS + pfad, method=methode, data=None if daten is None else json.dumps(daten).encode(),
                                     headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json"})
    try:
        antwort = oeffner(anfrage, frist)
        status, roh = antwort.status, antwort.read(MAX_ANTWORT)
    except urllib.error.HTTPError as e:
        status, roh = e.code, e.read(MAX_ANTWORT)
    except (urllib.error.URLError, OSError):
        raise Fehler("Der Dienst antwortet nicht auf 127.0.0.1:18765")
    try:
        antwort_daten = json.loads(roh.decode("utf-8")) if roh else {}
    except ValueError:
        antwort_daten = {}
    return status, antwort_daten if isinstance(antwort_daten, dict) else {}


def ergebnis(methode, status, daten):
    aus = {"status": status}
    if status != 200:
        code = daten.get("fehler")
        if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,40}", code):
            aus["fehler"] = code
    elif methode == "GET":
        plaetze = daten.get("plaetze")
        if not isinstance(plaetze, list):
            raise Fehler("Die Antwort des Dienstes ist unerwartet")
        aus["plaetze"] = [zaehlung(p) for p in plaetze]
    return aus  # Antworten auf POST: nur der Status (kein Platz-Token, keine Kennung)


def verarbeite(d, token_datei=TOKEN_DATEI, oeffner=None):
    token = ""
    try:
        methode, pfad, daten, frist = aufgabe(d)
        token = token_lesen(token_datei)
        status, antwort = senden(methode, pfad, daten, token, frist, oeffner or oeffner_bauen())
        print(json.dumps(ergebnis(methode, status, antwort)))
        return 0
    except Fehler as e:
        text = str(e)
        print("Fehler: " + (text.replace(token, "***") if token else text), file=sys.stderr)
        return 1
    except Exception as e:
        print("Fehler: Unerwarteter Fehler (" + type(e).__name__ + "), Einzelheiten werden nicht ausgegeben", file=sys.stderr)
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


class ServerFehler(Exception):
    """Ein Weg zum Server ist gescheitert. Der Text ist für die Person gedacht und enthält nie ein Geheimnis oder einen Gesprächstext."""


@dataclass(frozen=True)
class JournalZaehlung:
    zeilen: int
    r429: int
    soniox: int
    anfragen: int
    unterbrechungen: int
    pipeline_fehler: int
    token_zuege: int
    token_eingabe: int
    token_cache_lesen: int
    token_cache_schreiben: int
    abgeschnitten: bool
    minuten: dict  # Minute (Sekunden seit 1970, auf die Minute abgerundet) -> (Anfragen, Eingabe-Tokens)


@dataclass(frozen=True)
class Messung:
    zeit: float  # Uhr des Servers
    aktiv: bool
    kerne: int
    cpu_usec: int | None
    speicher: int | None
    speicher_spitze: int | None
    aufgaben: int | None
    journal_lesbar: bool | None  # None: das Journal wurde nicht angefragt
    journal: JournalZaehlung | None
    versatz_s: float  # Server minus lokale Uhr (Mitte zwischen Senden und Empfang)


@dataclass(frozen=True)
class DienstPlatz:
    platz: int
    offen: bool
    tickets: int
    kommentare: int
    einwilligung: bool
    platz_token: bool

    @property
    def frei(self) -> bool:
        """Kein Durchlauf, keine Tickets, keine Kommentare, keine Einwilligung: Dort liegt nichts, was der Lasttest berühren könnte."""
        return not (self.offen or self.tickets or self.kommentare or self.einwilligung)


# ---------- ssh ----------

def befehl_fuer(programm: str, sudo: bool = False) -> str:
    kodiert = base64.b64encode(programm.encode("utf-8")).decode("ascii")
    return f"{'sudo -n ' if sudo else ''}python3 -I -c \"import base64;exec(base64.b64decode('{kodiert}').decode('utf-8'))\""


def ssh_ausfuehren(host: str, befehl: str, eingabe: str, frist: float) -> wl.Ergebnis:
    """Ruft einen festen Befehl über ssh auf (BatchMode, kein Passwort, kein Terminal). Die Eingabe geht über stdin, nie als Argument."""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, befehl], input=eingabe, capture_output=True,
                           text=True, encoding="utf-8", timeout=frist)
    except subprocess.TimeoutExpired:
        return wl.Ergebnis(1, "", f"Zeitüberschreitung: Der Weg zum Server antwortete nicht binnen {int(frist)} Sekunden.")
    except OSError as e:
        return wl.Ergebnis(1, "", f"ssh ließ sich nicht starten ({type(e).__name__}).")
    return wl.Ergebnis(r.returncode, r.stdout[:MAX_AUSGABE], r.stderr[:MAX_AUSGABE])


def _kurz(text: str, grenze: int = 200) -> str:
    return "".join(c if c.isprintable() else " " for c in (text or "").strip())[:grenze]


def _host_pruefen(host: str) -> str:
    if not isinstance(host, str) or not HOST_RE.fullmatch(host):
        raise ServerFehler("Ungültiger Host für ssh (Buchstaben, Ziffern, Punkt, Unterstrich, Strich, kein Strich am Anfang)")
    return host


def _aufrufen(ssh: ZUGRIFF, host: str, befehl: str, nutzlast: dict, frist: float) -> dict:
    e = ssh(host, befehl, json.dumps(nutzlast), frist)
    if e.code != 0:
        hinweis = " Am Server fehlt vermutlich sudo ohne Passwort für python3 (sudo -n)." if "sudo:" in e.stderr.lower() else ""
        raise ServerFehler(f"Der Weg zum Server meldete einen Fehler (Ausgang {e.code}): {_kurz(e.stderr) or 'keine Angabe'}.{hinweis}")
    zeilen = e.stdout.strip().splitlines()
    try:
        daten = json.loads(zeilen[-1]) if zeilen else None
    except ValueError:
        daten = None
    if not isinstance(daten, dict):
        raise ServerFehler(UNERWARTET)
    return daten


# ---------- strenge Prüfung der Antworten ----------

def _ganz(d: dict, name: str, minimum: int = 0, leer_ok: bool = False) -> int | None:
    wert = d.get(name)
    if wert is None and leer_ok:
        return None
    if type(wert) is not int or wert < minimum:
        raise ServerFehler(UNERWARTET)
    return wert


def _flag(d: dict, name: str, leer_ok: bool = False) -> bool | None:
    wert = d.get(name)
    if wert is None and leer_ok:
        return None
    if type(wert) is not bool:
        raise ServerFehler(UNERWARTET)
    return wert


def _minuten(roh: object) -> dict:
    if not isinstance(roh, dict):
        raise ServerFehler(UNERWARTET)
    aus = {}
    for schluessel, werte in roh.items():
        if not (isinstance(schluessel, str) and schluessel.isdigit() and isinstance(werte, dict)):
            raise ServerFehler(UNERWARTET)
        aus[int(schluessel)] = (_ganz(werte, "anfragen"), _ganz(werte, "eingabe"))
    return aus


def _journal(roh: object) -> JournalZaehlung | None:
    if roh is None:
        return None
    if not isinstance(roh, dict):
        raise ServerFehler(UNERWARTET)
    zahlen = {n: _ganz(roh, n) for n in ("zeilen", "r429", "soniox", "anfragen", "unterbrechungen", "pipeline_fehler", "token_zuege",
                                          "token_eingabe", "token_cache_lesen", "token_cache_schreiben")}
    return JournalZaehlung(**zahlen, abgeschnitten=_flag(roh, "abgeschnitten"), minuten=_minuten(roh.get("minuten")))


def messung_lesen(daten: dict, versatz_s: float) -> Messung:
    zeit = daten.get("zeit")
    if type(zeit) not in (int, float) or zeit <= 0:
        raise ServerFehler(UNERWARTET)
    return Messung(float(zeit), _flag(daten, "aktiv"), _ganz(daten, "kerne", 1), _ganz(daten, "cpu_usec", leer_ok=True),
                   _ganz(daten, "speicher", leer_ok=True), _ganz(daten, "speicher_spitze", leer_ok=True),
                   _ganz(daten, "aufgaben", leer_ok=True), _flag(daten, "journal_lesbar", leer_ok=True), _journal(daten.get("journal")), versatz_s)


# ---------- die beiden Wege ----------

class Server:
    """Lesende Messung am Kern (Slot a oder b). Die Uhr und der Weg sind austauschbar (Tests setzen Attrappen ein)."""

    def __init__(self, host: str, ssh: ZUGRIFF = ssh_ausfuehren, slot: str = "b", uhr: Callable[[], float] = time.time):
        self.host, self.ssh, self.uhr = _host_pruefen(host), ssh, uhr
        if slot not in ("a", "b"):
            raise ServerFehler("Ungültiger Slot (a oder b)")
        self.slot = slot
        self._befehl = befehl_fuer(REMOTE_MESSUNG)

    def messen(self, seit: float | None) -> Messung:
        """Eine Messung. Mit seit zählt sie zusätzlich das Journal im Fenster (seit, jetzt] auf dem Server."""
        vorher = self.uhr()
        daten = _aufrufen(self.ssh, self.host, self._befehl, {"slot": self.slot, "seit": seit}, MESS_FRIST_S)
        nachher = self.uhr()
        zeit = daten.get("zeit")
        versatz = float(zeit) - (vorher + nachher) / 2 if type(zeit) in (int, float) else 0.0
        return messung_lesen(daten, versatz)


class Dienst:
    """Feste Aufgaben am Labor-Dienst über seine Betreuer-Schnittstelle. Schreibend sind nur die Plätze 1 bis 8."""

    def __init__(self, host: str, ssh: ZUGRIFF = ssh_ausfuehren):
        self.host, self.ssh = _host_pruefen(host), ssh
        self._befehl = befehl_fuer(REMOTE_DIENST, sudo=True)

    def _aufruf(self, nutzlast: dict) -> dict:
        return _aufrufen(self.ssh, self.host, self._befehl, nutzlast, DIENST_FRIST_S)

    def plaetze(self) -> tuple[DienstPlatz, ...]:
        daten = self._aufruf({"aktion": "plaetze"})
        roh = daten.get("plaetze")
        if daten.get("status") != 200 or not isinstance(roh, list) or not all(isinstance(p, dict) for p in roh):
            raise ServerFehler(UNERWARTET)
        plaetze = tuple(DienstPlatz(_ganz(p, "platz", 1), _flag(p, "offen"), _ganz(p, "tickets"), _ganz(p, "kommentare"),
                                    _flag(p, "einwilligung"), _flag(p, "platz_token")) for p in roh)
        if sorted(p.platz for p in plaetze) != list(range(1, 17)):
            raise ServerFehler(UNERWARTET)
        return tuple(sorted(plaetze, key=lambda p: p.platz))

    def _schreiben(self, aktion: str, platz: int, **felder: int) -> None:
        if type(platz) is not int or not 1 <= platz <= 8:
            raise ServerFehler("Ungültiger Platz (nur 1 bis 8 sind Lastplätze)")
        daten = self._aufruf({"aktion": aktion, "platz": platz, **felder})
        status = daten.get("status")
        if status != 200:
            code = daten.get("fehler")
            zusatz = f", {code}" if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,40}", code) else ""
            raise ServerFehler(f"Der Dienst lehnte {aktion} für Platz {platz} ab (HTTP {status}{zusatz}).")

    def durchlauf_oeffnen(self, platz: int, version: int) -> None:
        if type(version) is not int or not 1 <= version <= 99:
            raise ServerFehler("Ungültige Version der Einwilligung (1 bis 99)")
        self._schreiben("neuer-durchlauf", platz, version=version)

    def durchlauf_beenden(self, platz: int) -> None:
        self._schreiben("durchlauf-beenden", platz)

    def daten_loeschen(self, platz: int) -> None:
        self._schreiben("daten-loeschen", platz)
