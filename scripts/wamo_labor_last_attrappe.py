"""Attrappen für die Tests des Lasttests (scripts/test_wamo_labor_last_*.py, nie im echten Lauf benutzt).

Zeitraffer      echte Zeit, um einen Faktor beschleunigt: Ein Lauf von 15 Minuten dauert so einige Zehntelsekunden, und alle Threads laufen wirklich
Treiber         eine Browserinstanz, die Rohdaten nach einem einstellbaren Verhalten erzeugt (Antwortzeit, Verstummen, Ausfall, 429)
Server, Dienst  der lesende Weg zum Kern und die festen Aufgaben am Labor-Dienst, mit Zustand und mit einstellbaren Fehlern
Kern            die Verwaltung der Laborprofile (wamo_labor.main ruft sie wie run_remote auf), mit den Ausgaben des echten Programms
Umschlag        die Zugangsdaten aus der Umschlag-Datei, ohne Datei (nie die echte)

Alle Attrappen schreiben ihre Aufrufe in ein gemeinsames Protokoll, damit die Tests die Reihenfolge prüfen können (zum Beispiel beim Aufräumen).
"""
from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import wamo_labor as wl
import wamo_labor_faktoren_umschlag as um
import wamo_labor_last_server as sv
from wamo_labor_last_browser import TreiberFehler
from wamo_labor_last_plan import SchrittPlan

GIB = 1024 ** 3
SEED_B32 = base64.b32encode(b"1234567890" * 2).decode()  # der Schlüssel der RFC-Testvektoren


class Zeitraffer:
    """Uhr und Schlafen mit Beschleunigung. Die Zeit ist echt (perf_counter), nur schneller."""

    def __init__(self, faktor: float = 2000.0, start: float = 1_800_000_000.0):
        self.faktor, self._start, self._t0 = faktor, start, time.perf_counter()

    def jetzt(self) -> float:
        return self._start + (time.perf_counter() - self._t0) * self.faktor

    def schlafen(self, sekunden: float) -> None:
        time.sleep(max(0.0, sekunden) / self.faktor)


class Protokoll:
    """Eine threadsichere Liste von (Quelle, Aufruf, Einzelheit)."""

    def __init__(self) -> None:
        self.eintraege: list[tuple] = []
        self._sperre = threading.Lock()

    def add(self, *eintrag: object) -> None:
        with self._sperre:
            self.eintraege.append(tuple(eintrag))

    def namen(self, quelle: str | None = None) -> list[str]:
        return [e[1] for e in self.eintraege if quelle is None or e[0] == quelle]

    def index(self, quelle: str, aufruf: str) -> int:
        return next(i for i, e in enumerate(self.eintraege) if e[0] == quelle and e[1] == aufruf)


def bloecke(von: float, bis: float, schritt: float = 0.04) -> list[list[float]]:
    n = int(round((bis - von) / schritt))
    return [[round(von + i * schritt, 3), round(von + (i + 1) * schritt, 3)] for i in range(max(0, n))]


@dataclass
class Verhalten:
    """Wie sich die Browserinstanzen verhalten. Alles optional, Standard ist eine gesunde Sitzung."""
    erster_ton_s: float = 1.2
    antwort_s: float = 4.0
    clip_s: float = 3.0
    einwurf_verstummen_s: float = 0.7
    start_fehler: dict = field(default_factory=dict)  # Sitzung -> Text: starten scheitert
    anmelde_fehler: dict = field(default_factory=dict)
    gespraech_fehler: dict = field(default_factory=dict)  # Sitzung -> Anzahl Versuche, die scheitern
    schritt_abbruch_ab: dict = field(default_factory=dict)  # Sitzung -> Nummer des Schritts, ab dem das Gespräch weg ist
    r429_ab: dict = field(default_factory=dict)  # Sitzung -> Schritt, ab dem die Antwort ein 429 meldet
    keine_antwort: set = field(default_factory=set)  # Sitzungen, die nie eine Antwort bekommen
    beenden_fehler: dict = field(default_factory=dict)
    schliessen_fehler: dict = field(default_factory=dict)
    erster_ton_je_sitzungen: Callable[[int], float] | None = None  # Anzahl aktiver Sitzungen -> erster Ton in Sekunden


class TreiberAttrappe:
    def __init__(self, sitzung: int, uhr: Zeitraffer, protokoll: Protokoll, verhalten: Verhalten, aktive: Callable[[], int]):
        self.sitzung, self.uhr, self.protokoll, self.v, self.aktive = sitzung, uhr, protokoll, verhalten, aktive
        self.schritte = 0
        self.gespraech = False
        self.abgebrochen = threading.Event()
        self.geheim: list[str] = []
        self.gespraech_versuche = 0
        self.geschlossen = False

    def _log(self, aufruf: str, einzelheit: object = "") -> None:
        self.protokoll.add(f"treiber{self.sitzung}", aufruf, einzelheit)

    def starten(self, url: str, clips_ordner: Path, kopflos: bool = False) -> None:
        self._log("starten", url)
        if self.sitzung in self.v.start_fehler:
            raise TreiberFehler(self.v.start_fehler[self.sitzung])

    def anmelden(self, email: str, passwort: str, code: str) -> None:
        self.geheim += [passwort, code]
        self._log("anmelden", email)
        if self.sitzung in self.v.anmelde_fehler:
            raise TreiberFehler(self.v.anmelde_fehler[self.sitzung])

    def gespraech_starten(self) -> None:
        self._log("gespraech_starten")
        self.gespraech_versuche += 1
        self.schritte = 0
        if self.gespraech_versuche <= self.v.gespraech_fehler.get(self.sitzung, 0):
            raise TreiberFehler("Gespräch ließ sich nicht starten")
        self.gespraech = True

    def murmeln(self, datei: Path | None, gain: float = 0.0) -> None:
        self._log("murmeln", None if datei is None else round(gain, 3))

    def schritt(self, plan: SchrittPlan, start_frist_s: float, ende_frist_s: float, einwurf_ab_s: float, freigabe: str) -> dict:
        self.schritte += 1
        self._log("schritt", plan.karte)
        if self.abgebrochen.is_set():
            raise TreiberFehler("abgebrochen")
        roh = {"clip": plan.clip, "clip_t0": 100.0, "clip_ende": 100.0 + self.v.clip_s, "bot": [], "einwurf": None, "ende_t": 0.0,
               "abbruch": "", "zeitueberschreitung": False, "geschnitten": 0, "r429": 0, "freigabe": "", "konsole": 0}
        ton = self.v.erster_ton_s if self.v.erster_ton_je_sitzungen is None else self.v.erster_ton_je_sitzungen(self.aktive())
        self.uhr.schlafen(self.v.clip_s + ton + self.v.antwort_s)
        ende = roh["clip_ende"]
        if self.sitzung in self.v.keine_antwort:
            roh["zeitueberschreitung"] = True
            roh["ende_t"] = ende + start_frist_s
        elif self.schritte >= self.v.schritt_abbruch_ab.get(self.sitzung, 10 ** 9):
            self.v.schritt_abbruch_ab.pop(self.sitzung, None)  # einmalig: das nächste Gespräch läuft wieder
            roh["abbruch"] = "Gespräch beendet"
            roh["ende_t"] = ende + 1
        else:
            roh["bot"] = bloecke(ende + ton, ende + ton + self.v.antwort_s)
            if plan.einwurf:
                onset = ende + ton + 2.0
                roh["bot"] = bloecke(ende + ton, onset + self.v.einwurf_verstummen_s)
                roh["einwurf"] = {"clip": plan.einwurf_clip, "t0": onset, "ende": onset + 2.0, "bot_seit": 2.0}
                roh["geschnitten"] = 1
            roh["ende_t"] = roh["bot"][-1][1] + 4.0
        if self.schritte >= self.v.r429_ab.get(self.sitzung, 10 ** 9):
            roh["r429"] = 1
        return roh

    def gespraech_beenden(self) -> None:
        self._log("gespraech_beenden")
        if self.sitzung in self.v.beenden_fehler:
            raise TreiberFehler(self.v.beenden_fehler[self.sitzung])
        self.gespraech = False

    def abbrechen(self) -> None:
        self._log("abbrechen")
        self.abgebrochen.set()

    def schliessen(self) -> None:
        self._log("schliessen")
        self.geschlossen = True
        if self.sitzung in self.v.schliessen_fehler:
            raise TreiberFehler(self.v.schliessen_fehler[self.sitzung])


@dataclass
class ServerEinstellung:
    kerne: int = 20
    speicher: Callable[[float], int] = lambda t: 1 * GIB
    cpu_je_sekunde_usec: Callable[[float], float] = lambda t: 500_000.0  # 0,5 s CPU je Sekunde: 50 Prozent eines Kerns
    journal: Callable[[float, float], dict] | None = None  # (seit, bis) -> Felder von JournalZaehlung
    lesbar: bool = True
    aktiv: bool = True
    fehler_ab: int | None = None  # ab dieser Messung scheitert ssh
    versatz_s: float = 0.0


class ServerAttrappe:
    """Ersetzt wamo_labor_last_server.Server (nur messen)."""

    def __init__(self, uhr: Zeitraffer, protokoll: Protokoll, einstellung: ServerEinstellung | None = None):
        self.uhr, self.protokoll, self.e = uhr, protokoll, einstellung or ServerEinstellung()
        self.aufrufe = 0
        self._t0 = uhr.jetzt()

    def messen(self, seit: float | None) -> sv.Messung:
        self.aufrufe += 1
        self.protokoll.add("server", "messen", seit)
        if self.e.fehler_ab is not None and self.aufrufe >= self.e.fehler_ab:
            raise sv.ServerFehler("Der Weg zum Server meldete einen Fehler (Ausgang 255): Connection timed out.")
        t = self.uhr.jetzt()
        cpu = int(self.e.cpu_je_sekunde_usec(t) * (t - self._t0))
        journal = None
        if seit is not None and self.e.lesbar:
            felder = {"zeilen": 0, "r429": 0, "soniox": 0, "anfragen": 0, "unterbrechungen": 0, "pipeline_fehler": 0, "token_zuege": 0,
                      "token_eingabe": 0, "token_cache_lesen": 0, "token_cache_schreiben": 0, "abgeschnitten": False, "minuten": {}}
            felder.update(self.e.journal(seit, t) if self.e.journal else {})
            journal = sv.JournalZaehlung(**felder)
        return sv.Messung(t + self.e.versatz_s, self.e.aktiv, self.e.kerne, cpu, self.e.speicher(t), self.e.speicher(t), 40,
                          self.e.lesbar if seit is not None else None, journal, self.e.versatz_s)


class DienstAttrappe:
    """Ersetzt wamo_labor_last_server.Dienst: 16 Plätze mit Zustand."""

    def __init__(self, protokoll: Protokoll, belegt: dict | None = None, fehler: dict | None = None):
        self.protokoll = protokoll
        self.plaetze_zustand = {n: sv.DienstPlatz(n, False, 0, 0, False, False) for n in range(1, 17)}
        for n, p in (belegt or {}).items():
            self.plaetze_zustand[n] = p
        self.fehler = fehler or {}  # Aufgabe -> Text
        self.versionen: dict[int, int] = {}

    def _wirken(self, aufgabe: str, platz: int | None = None) -> None:
        self.protokoll.add("dienst", aufgabe, platz)
        if aufgabe in self.fehler:
            raise sv.ServerFehler(self.fehler[aufgabe])

    def plaetze(self) -> tuple[sv.DienstPlatz, ...]:
        self._wirken("plaetze")
        return tuple(self.plaetze_zustand[n] for n in range(1, 17))

    def durchlauf_oeffnen(self, platz: int, version: int) -> None:
        self._wirken("durchlauf_oeffnen", platz)
        self.versionen[platz] = version
        self.plaetze_zustand[platz] = sv.DienstPlatz(platz, True, 0, 0, True, True)

    def durchlauf_beenden(self, platz: int) -> None:
        self._wirken("durchlauf_beenden", platz)
        p = self.plaetze_zustand[platz]
        self.plaetze_zustand[platz] = sv.DienstPlatz(platz, False, p.tickets, p.kommentare, p.einwilligung, p.platz_token)

    def daten_loeschen(self, platz: int) -> None:
        self._wirken("daten_loeschen", platz)
        self.plaetze_zustand[platz] = sv.DienstPlatz(platz, False, 0, 0, False, False)


class KernAttrappe:
    """Ersetzt wamo_labor.run_remote: antwortet wie das Programm am Server (gleiche Ausgaben, gleiche Ausgänge)."""

    def __init__(self, protokoll: Protokoll, freigeschaltet: set | None = None, fehler: dict | None = None, vorhanden: int = 9):
        self.protokoll = protokoll
        self.aktiv = set(freigeschaltet or ())
        self.fehler = fehler or {}  # Aktion -> Text
        self.vorhanden = vorhanden
        self.aufrufe: list[dict] = []

    @staticmethod
    def _kennung(nutzlast: dict) -> str:
        return "labor-schau" if nutzlast.get("art") == "schaufenster" else f"labor-{int(nutzlast['platz']):02d}"

    def __call__(self, host: str, nutzlast: dict) -> wl.Ergebnis:
        aktion = nutzlast["aktion"]
        self.aufrufe.append(nutzlast)
        self.protokoll.add("kern", aktion, nutzlast.get("platz"))
        if aktion in self.fehler:
            return wl.Ergebnis(1, "", self.fehler[aktion])
        if aktion == "status":
            zeilen = [f"labor-{n:02d} | platz | {'freigeschaltet' if f'labor-{n:02d}' in self.aktiv else 'gesperrt'} | 0 Zeilen"
                      for n in range(1, self.vorhanden + 1)]
            return wl.Ergebnis(0, "\n".join(zeilen), "")
        kennung = self._kennung(nutzlast)
        if aktion == "aktivieren":
            self.aktiv.add(kennung)
            return wl.Ergebnis(0, f"Laborprofil {kennung} freigeschaltet (labor_aktiv = true).", "")
        if aktion == "sperren":
            self.aktiv.discard(kennung)
            return wl.Ergebnis(0, f"Laborprofil {kennung} gesperrt (labor_aktiv = false).", "")
        return wl.Ergebnis(1, "", f"unbekannte Aktion {aktion}")


class UmschlagAttrappe:
    """Ersetzt wamo_labor_faktoren_umschlag.Umschlag, ohne Datei."""

    def __init__(self, ohne_seed: set | None = None, fehlend: set | None = None):
        self.ohne_seed, self.fehlend = ohne_seed or set(), fehlend or set()
        self.abgefragt: list[str] = []

    def zugang(self, kennung: str) -> um.Zugang:
        self.abgefragt.append(kennung)
        if kennung in self.fehlend:
            raise um.UmschlagFehler(f"Für {kennung} gibt es keine Zeile in der Umschlag-Datei")
        seed = "" if kennung in self.ohne_seed else SEED_B32
        return um.Zugang(kennung, f"{kennung}@wamo.test", f"Pw-{kennung}-geheim", seed)
