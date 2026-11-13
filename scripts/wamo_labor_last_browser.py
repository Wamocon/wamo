"""Treiber zum Browser-Worker des Lasttests (scripts/wamo_labor_last.py, nur Standardbibliothek).

Je Sitzung läuft ein Node-Prozess (scripts/wamo-last-browser.cjs) mit einer eigenen Chrome-Instanz, einem virtuellen Mikrofon und einer Aufnahme der
Wiedergabe von WAMO. Python und Worker sprechen über Pipes, eine JSON-Zeile je Anfrage und je Antwort:

  Anfrage   {"id": 7, "cmd": "schritt", ...}
  Antwort   {"id": 7, "ok": true, "daten": {...}}  oder  {"id": 7, "ok": false, "fehler": "kurzer Text"}

Mehrere Anfragen dürfen gleichzeitig offen sein (die Antworten werden über die Nummer zugeordnet): So kann abbrechen eine laufende Anfrage
unterbrechen, ohne zu warten. Alles, was der Worker auf stdout schreibt und kein Protokoll ist, wird ignoriert; sein stderr wird nur als kurzer,
geschwärzter Rest für Fehlermeldungen aufgehoben.

Geheimnisse: Passwort und Code gehen nur in der Anfrage anmelden über die Pipe zum Worker, nie in eine Befehlszeile, Datei oder Ausgabe. Jeder
Fehlertext wird vor der Weitergabe geschwärzt (merke). Der Worker prüft die Adresse zusätzlich selbst, hier wird sie vor dem Start geprüft: nur die
feste Adresse der Oberfläche auf Sokrates-2 (wamo_labor_platz_adressen).
"""
from __future__ import annotations

import collections
import json
import os
import queue
import signal
import subprocess
import threading
from pathlib import Path

from wamo_labor_last_plan import SchrittPlan
from wamo_labor_platz_adressen import HERKUENFTE, adresse_zulaessig

WORKER = Path(__file__).resolve().parent / "wamo-last-browser.cjs"
START_FRIST_S = 120.0
ANMELDE_FRIST_S = 90.0
GESPRAECH_FRIST_S = 75.0
BEENDEN_FRIST_S = 30.0
SCHRITT_RESERVE_S = 30.0  # Zuschlag zu den Fristen des Schritts, bevor Python die Antwort aufgibt
ABBRECHEN_FRIST_S = 5.0
STDERR_REST = 2000


class TreiberFehler(Exception):
    """Ein Fehler des Browser-Workers. Der Text ist kurz und geschwärzt."""


def standard_befehl() -> list[str]:
    return ["node", str(WORKER)]


def _system32(name: str) -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", name)


def _hart_beenden(prozess: subprocess.Popen) -> None:
    """Beendet den Worker samt Chrome und allen Unterprozessen."""
    try:
        if os.name == "nt":
            subprocess.run([_system32("taskkill.exe"), "/pid", str(prozess.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            os.killpg(os.getpgid(prozess.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError, ProcessLookupError):
        pass
    try:
        prozess.kill()
    except OSError:
        pass


class NodeTreiber:
    """Eine Browserinstanz. Alle Methoden sind threadsicher, außer dass zwei Schritte derselben Sitzung nie gleichzeitig laufen sollen."""

    def __init__(self, befehl: list[str], name: str = "browser", schliess_frist_s: float = 15.0):
        self.befehl, self.name, self.schliess_frist_s = list(befehl), name, schliess_frist_s
        self._prozess: subprocess.Popen | None = None
        self._offen: dict[int, queue.Queue] = {}
        self._sperre = threading.Lock()
        self._schreiben = threading.Lock()
        self._nummer = 0
        self._geheim: list[str] = []
        self._stderr: collections.deque[str] = collections.deque(maxlen=STDERR_REST)
        self._beendet = False
        self._leser: list[threading.Thread] = []

    # ---- Zustand ----

    @property
    def gestartet(self) -> bool:
        return self._prozess is not None

    @property
    def beendet(self) -> bool:
        return self._beendet

    def merke(self, *werte: object) -> None:
        """Werte, die nie in einem Text erscheinen dürfen (Passwort, Code, Seed)."""
        with self._sperre:
            for w in werte:
                if isinstance(w, str) and len(w) >= 4 and w not in self._geheim:
                    self._geheim.append(w)

    def _schwaerzen(self, text: str) -> str:
        for g in sorted(self._geheim, key=len, reverse=True):
            text = text.replace(g, "***")
        return "".join(c if c.isprintable() else " " for c in text).strip()[:300]

    # ---- Prozess ----

    def starten_prozess(self) -> None:
        with self._sperre:
            if self._prozess is not None or self._beendet:
                return
            try:
                self._prozess = subprocess.Popen(
                    self.befehl, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    errors="replace", bufsize=1, start_new_session=os.name != "nt", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as e:
                raise TreiberFehler(f"Der Browser-Worker ist nicht gestartet ({type(e).__name__}, ist Node installiert?)") from None
        for ziel in (self._stdout_lesen, self._stderr_lesen):
            leser = threading.Thread(target=ziel, daemon=True, name=f"{self.name}-{ziel.__name__}")
            leser.start()
            self._leser.append(leser)

    def _stdout_lesen(self) -> None:
        prozess = self._prozess
        for zeile in prozess.stdout:
            try:
                d = json.loads(zeile)
            except ValueError:
                continue
            warteschlange = self._offen.get(d.get("id")) if isinstance(d, dict) else None
            if warteschlange is not None:
                warteschlange.put(d)
        self._ende_melden()

    def _stderr_lesen(self) -> None:
        for zeile in self._prozess.stderr:
            self._stderr.extend(zeile)

    def _ende_melden(self) -> None:
        for leser in self._leser:
            if leser is not threading.current_thread():
                leser.join(0.5)
        code = self._prozess.poll()
        if code is None:
            try:
                code = self._prozess.wait(timeout=2)
            except subprocess.TimeoutExpired:
                code = None
        rest = self._schwaerzen("".join(self._stderr)[-200:])
        text = f"Der Browser-Worker {self.name} ist beendet (Ausgang {code})" + (f": {rest}" if rest else "")
        with self._sperre:
            offen = list(self._offen.values())
        for warteschlange in offen:
            warteschlange.put({"ok": False, "fehler": text, "ende": True})

    # ---- Anfragen ----

    def anfrage(self, cmd: str, params: dict | None = None, frist_s: float = 30.0) -> dict:
        """Eine Anfrage an den Worker, mit Frist. Gibt die Daten der Antwort zurück oder wirft TreiberFehler."""
        if self._beendet:
            raise TreiberFehler(f"Der Browser-Worker {self.name} ist beendet")
        self.starten_prozess()
        with self._sperre:
            self._nummer += 1
            nummer = self._nummer
            antwort: queue.Queue = queue.Queue()
            self._offen[nummer] = antwort
        try:
            zeile = json.dumps({"id": nummer, "cmd": cmd, **(params or {})}, ensure_ascii=False)
            try:
                with self._schreiben:
                    self._prozess.stdin.write(zeile + "\n")
                    self._prozess.stdin.flush()
            except (OSError, ValueError):
                raise TreiberFehler(f"Der Browser-Worker {self.name} ist beendet (Eingabe geschlossen)") from None
            return self._warten(antwort, frist_s, cmd)
        finally:
            with self._sperre:
                self._offen.pop(nummer, None)

    def _warten(self, antwort: queue.Queue, frist_s: float, cmd: str) -> dict:
        try:
            d = antwort.get(timeout=frist_s)
        except queue.Empty:
            raise TreiberFehler(f"{cmd}: Die Frist von {frist_s:.0f} s ist überschritten") from None
        if not d.get("ok"):
            raise TreiberFehler(self._schwaerzen(str(d.get("fehler") or f"{cmd}: unbekannter Fehler")))
        daten = d.get("daten")
        return daten if isinstance(daten, dict) else {}

    # ---- die Befehle des Lasttests ----

    def starten(self, url: str, clips_ordner: Path, kopflos: bool = False) -> None:
        if not adresse_zulaessig(url):
            raise TreiberFehler("Adresse nicht zulässig: nur die feste Adresse der Oberfläche auf Sokrates-2 ist erlaubt")
        self.anfrage("start", {"url": url, "herkuenfte": list(HERKUENFTE), "clips_ordner": str(clips_ordner), "kopflos": bool(kopflos)}, START_FRIST_S)

    def anmelden(self, email: str, passwort: str, code: str) -> None:
        self.merke(passwort, code)
        self.anfrage("anmelden", {"email": email, "passwort": passwort, "code": code}, ANMELDE_FRIST_S)

    def gespraech_starten(self) -> None:
        self.anfrage("gespraech_starten", {}, GESPRAECH_FRIST_S)

    def murmeln(self, datei: Path | None, gain: float = 0.0) -> None:
        """Startet das Murmeln (Datei und Verstärkung) oder beendet es (datei None)."""
        self.anfrage("murmeln", {"datei": None if datei is None else str(datei), "gain": float(gain)}, 60.0)

    def schritt(self, plan: SchrittPlan, start_frist_s: float, ende_frist_s: float, einwurf_ab_s: float, freigabe: str) -> dict:
        params = {"clip": plan.clip, "einwurf_clip": plan.einwurf_clip if plan.einwurf else "", "start_frist_s": start_frist_s,
                  "ende_frist_s": ende_frist_s, "einwurf_ab_s": einwurf_ab_s, "freigabe": freigabe}
        return self.anfrage("schritt", params, start_frist_s + ende_frist_s + SCHRITT_RESERVE_S)

    def gespraech_beenden(self) -> None:
        self.anfrage("gespraech_beenden", {}, BEENDEN_FRIST_S)

    def abbrechen(self) -> None:
        """Bricht eine laufende Anfrage des Workers ab (die Wartezeiten im Browser enden sofort). Still, wenn der Worker schon weg ist."""
        if self._beendet or self._prozess is None:
            return
        try:
            self.anfrage("abbrechen", {}, ABBRECHEN_FRIST_S)
        except TreiberFehler:
            pass

    def schliessen(self) -> None:
        """Schließt den Browser und den Worker. Antwortet der Worker nicht, wird er samt Chrome hart beendet. Mehrfach aufrufbar."""
        if self._beendet:
            return
        prozess = self._prozess
        if prozess is not None and prozess.poll() is None:
            try:
                self.anfrage("schliessen", {}, min(5.0, self.schliess_frist_s))
            except TreiberFehler:
                pass
            try:
                prozess.wait(timeout=self.schliess_frist_s)
            except subprocess.TimeoutExpired:
                _hart_beenden(prozess)
        if prozess is not None:
            self._aufraeumen(prozess)
        self._beendet = True

    def _aufraeumen(self, prozess: subprocess.Popen) -> None:
        if prozess.poll() is None:
            _hart_beenden(prozess)
        for strom in (prozess.stdin, prozess.stdout, prozess.stderr):
            try:
                strom.close()
            except (OSError, ValueError):
                pass
        try:
            prozess.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
