"""Prüflogik für den Dispatcher-Befehl pruefen (wamo_job_dispatch.py). Nur Standardbibliothek und abnahme_befehl.py.

Installiert nach /opt/wamo/waechter (root-eigen, wie die Wächter-Kopien) durch wamo-job-einrichten.sh. Der Dispatcher lädt es
erst, wenn WAMO_PRUEFEN=an gesetzt ist. Hier liegt, was nicht in die große Datei des Dispatchers muss und ohne Server prüfbar ist:

  * die stdin-Zeile {"argv": [...]}: genau dieses eine Feld, höchstens 8 KiB, die Allowlist aus abnahme_befehl.py ERNEUT
    (der Kern ist für den Dispatcher nicht vertrauenswürdig);
  * der Lauf (Lauf): der Läufer wamo-job.sh im Modus pruefen als Kindprozess, Ausgabe in einem Ringpuffer, Zeitlimit,
    Abbruch von außen (Ende der Lebenslinie), Kind samt Prozessgruppe beenden;
  * die Antwort: eine JSON-Zeile {"v","ok","exit","abgebrochen","grund","dauer_s","ausgabe","gekuerzt"}. Die Ausgabe ist das
    Ende (höchstens 2048 Byte), geschwärzt, ohne Steuerzeichen und steht nur als JSON-String darin. ok heißt: echter int 0,
    nicht abgebrochen, kein Grund.

Was die Sandbox darf, steht nicht hier, sondern im Läufer (wamo-job.sh --modus pruefen: kein Netz, kein Token, kein Claude).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import abnahme_befehl

ANTWORT_VERSION = 1
ZEILE_MAX_BYTES = 8 * 1024  # die stdin-Zeile mit argv, samt Zeilenende
AUSGABE_BYTES = 2048  # so viel vom Ende der Ausgabe geht in die Antwort
FENSTER_BYTES = 16 * 1024  # so viel vom Ende hält der Puffer, damit die Schwärzung auch kurz vor der Schnittkante greift
ZEITLIMIT_S = 120
NACHFRIST_S = 30.0  # Reserve über das Zeitlimit des Läufers hinaus (timeout --kill-after=15, Start der Einheit)
AUFRUFE_MAX = 40  # je Auftrag (höchstens 5 Runden mit je 5 Kriterien, großzügig)
GRUND_ZEICHEN = 300
KEIN_VENV = ("nicht möglich: Im Projektordner liegt keine .venv, und uv installiert hier nichts. Formuliere das Kriterium "
             "als Aussage oder lege die .venv im Auftrag an")
FELDER = frozenset({"argv"})


class Abgelehnt(Exception):
    """Die Zeile oder der Befehl wird nicht ausgeführt. Der Text nennt nie mehr als einen kurzen Grund."""


# ---------------------------------------------------------------- Zeichen
def _bereinigt(text: str) -> str:
    """Ohne Steuerzeichen (Zeilenumbruch und Tabulator bleiben)."""
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127 and unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp")))


def _einzeilig(text: object, grenze: int) -> str:
    sauber = " ".join(_bereinigt(str(text)).replace("\n", " ").replace("\t", " ").split())
    return sauber if len(sauber) <= grenze else sauber[:grenze - 1].rstrip() + "…"


# ---------------------------------------------------------------- die stdin-Zeile
def _ohne_doppelte(paare: list[tuple[str, object]]) -> dict[str, object]:
    daten: dict[str, object] = {}
    for name, wert in paare:
        if name in daten:
            raise ValueError("doppeltes Feld")
        daten[name] = wert
    return daten


def argv_aus_zeile(raw: bytes) -> list[str]:
    """Die erste stdin-Zeile von pruefen: {"argv": [...]}, genau dieses Feld, höchstens 8 KiB mit Zeilenende, ein
    einzelnes JSON-Objekt ohne doppelte Felder. argv geht durch die Allowlist (abnahme_befehl.argv_grund). Abgelehnt mit einem
    kurzen Grund; der Text enthält nie die rohe Zeile."""
    if not raw.endswith(b"\n") or len(raw) > ZEILE_MAX_BYTES:
        raise Abgelehnt(f"argv-Zeile fehlt oder ist zu lang (mit Zeilenende höchstens {ZEILE_MAX_BYTES // 1024} KiB)")
    if raw.count(b"\n") != 1:
        raise Abgelehnt("argv-Zeile: genau eine Zeile erwartet")
    try:
        daten = json.loads(raw.decode("utf-8"), object_pairs_hook=_ohne_doppelte)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Abgelehnt("argv-Zeile ist kein gültiges JSON") from None
    if not isinstance(daten, dict) or set(daten) != FELDER:
        raise Abgelehnt('argv-Zeile: genau das Feld "argv" erwartet, kein anderes')
    argv = daten["argv"]
    if grund := abnahme_befehl.argv_grund(argv):
        raise Abgelehnt(f"argv: {_einzeilig(grund, 250)}")
    return list(argv)  # type: ignore[call-overload]


def venv_vorhanden(projektdir: Path) -> bool:
    """Liegt im Projektordner eine echte .venv (ein Ordner, kein Link, mit pyvenv.cfg)? Nur die uv-Form fragt danach: uv legt
    hier nichts an und lädt nichts."""
    venv = projektdir / ".venv"
    try:
        return (not venv.is_symlink() and venv.is_dir() and (venv / "pyvenv.cfg").is_file()
                and not (venv / "pyvenv.cfg").is_symlink())
    except OSError:
        return False


# ---------------------------------------------------------------- Ausgabe
class EndPuffer:
    """Das Ende der Ausgabe, höchstens fenster Bytes (zwischendurch bis zum Doppelten). Thread-sicher. verworfen merkt sich,
    dass etwas vor dem Fenster weggefallen ist."""

    def __init__(self, fenster: int = FENSTER_BYTES):
        self._fenster = fenster
        self._daten = bytearray()
        self._verworfen = False
        self._sperre = threading.Lock()

    def add(self, stueck: bytes) -> None:
        with self._sperre:
            self._daten += stueck
            if len(self._daten) > 2 * self._fenster:
                del self._daten[:-self._fenster]
                self._verworfen = True

    def ende(self) -> tuple[bytes, bool]:
        with self._sperre:
            return bytes(self._daten[-self._fenster:]), self._verworfen or len(self._daten) > self._fenster


def ausgabe_text(daten: bytes, verworfen: bool, schwaerzen: Callable[[bytes], bytes]) -> tuple[str, bool]:
    """Die Ausgabe für die Antwort: erst schwärzen (auf dem ganzen Fenster, damit ein Wert nicht an der Schnittkante der
    letzten 2048 Byte überlebt), dann als Text ohne Steuerzeichen, zuletzt das Ende bis höchstens 2048 Byte. Liefert
    (Text, gekürzt). Ein am Anfang angeschnittenes Zeichen fällt weg, nie ein Ersatzzeichen mehr."""
    text = _bereinigt(schwaerzen(daten).decode("utf-8", "replace"))
    kodiert = text.encode("utf-8")
    gekuerzt = verworfen
    if len(kodiert) > AUSGABE_BYTES:
        text = kodiert[-AUSGABE_BYTES:].decode("utf-8", "ignore")
        gekuerzt = True
    return text, gekuerzt


# ---------------------------------------------------------------- Ergebnis und Antwort
@dataclass(frozen=True)
class Ergebnis:
    """Ausgang eines Laufs. rc: Exit des Läufers (None, wenn nie gestartet); abgebrochen: von uns beendet (Zeitlimit, Ende der
    Lebenslinie, Signal); grund: kurzer Text dazu; ausgabe: das Ende der Ausgabe (roh, noch nicht geschwärzt)."""

    rc: int | None
    abgebrochen: bool = False
    grund: str | None = None
    dauer_s: float = 0.0
    ausgabe: bytes = b""
    verworfen: bool = False


def antwort_leer(grund: str, abgebrochen: bool = False) -> dict[str, object]:
    """Antwort für einen Aufruf, der nichts ausgeführt hat (abgewiesen, belegt, Selbsttest, nicht möglich)."""
    return {"v": ANTWORT_VERSION, "ok": False, "exit": None, "abgebrochen": abgebrochen,
            "grund": _einzeilig(grund, GRUND_ZEICHEN), "dauer_s": 0.0, "ausgabe": "", "gekuerzt": False}


def antwort(e: Ergebnis, schwaerzen: Callable[[bytes], bytes]) -> dict[str, object]:
    """Die eine JSON-Zeile (als Dict, Reihenfolge der Felder fest). ok nur bei einem echten int 0 ohne Abbruch und ohne Grund:
    ein bool, ein float, ein Text oder None ist nie ok. Exit 124 (timeout) heißt Zeitlimit, 137 (SIGKILL) Zeitlimit der Einheit
    oder Speichergrenze; beides ist nicht erfüllt."""
    rc, abgebrochen, grund = e.rc, e.abgebrochen, e.grund
    if not abgebrochen and rc == 124 and type(rc) is int:
        abgebrochen, grund = True, "zeitlimit"
    elif not abgebrochen and type(rc) is int and rc == 137 and grund is None:
        grund = "beendet (Zeitlimit der Einheit oder Speichergrenze)"
    exit_ = rc if type(rc) is int and 0 <= rc <= 255 and not abgebrochen else None
    ok = type(exit_) is int and exit_ == 0 and not abgebrochen and grund is None
    text, gekuerzt = ausgabe_text(e.ausgabe, e.verworfen, schwaerzen)
    return {"v": ANTWORT_VERSION, "ok": ok, "exit": exit_, "abgebrochen": abgebrochen,
            "grund": None if grund is None else _einzeilig(grund, GRUND_ZEICHEN), "dauer_s": round(e.dauer_s, 2),
            "ausgabe": text, "gekuerzt": gekuerzt}


# ---------------------------------------------------------------- der Lauf
class Lauf:
    """Ein Prüflauf: der Läufer als Kindprozess (eigene Sitzung), stdin leer, stdout und stderr gemeinsam in einen Ringpuffer.

    stoppen(grund) beendet ihn von jedem Thread aus und ruft stop_einheit (der Dispatcher beendet damit die systemd-Einheit des
    Auftrags, also die Sandbox samt Kindprozessen). Es gilt der erste Grund. Ein Stopp vor dem Start verhindert den Start.
    ausfuehren() blockiert bis zum Ende und wirft nie wegen des Kindes."""

    def __init__(self, argv: Sequence[str], env: dict[str, str] | None, stop_einheit: Callable[[], None], *,
                 zeitlimit_s: float = ZEITLIMIT_S, nachfrist_s: float = NACHFRIST_S, cwd: str | None = None,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen):
        self._argv, self._env, self._cwd = list(argv), env, cwd
        self._stop_einheit = stop_einheit
        self._frist_s = zeitlimit_s + nachfrist_s
        self._popen = popen
        self._sperre = threading.Lock()
        self._gestoppt = threading.Event()
        self._grund: str | None = None
        self._proc: subprocess.Popen | None = None
        self._puffer = EndPuffer()

    @property
    def gestoppt(self) -> bool:
        return self._gestoppt.is_set()

    @property
    def grund(self) -> str | None:
        return self._grund

    def stoppen(self, grund: str) -> None:
        with self._sperre:
            if self._gestoppt.is_set():
                return
            self._grund = grund
            self._gestoppt.set()
        try:
            self._stop_einheit()
        except Exception:  # noqa: BLE001 (die Einheit lässt sich nicht beenden: das Kind trotzdem beenden, nie werfen)
            pass
        proc = self._proc
        if proc is not None:
            self._beenden(proc)

    @staticmethod
    def _beenden(proc: subprocess.Popen) -> None:
        """Prozessgruppe erst mit SIGTERM, nach fünf Sekunden mit SIGKILL. Unter Windows (nur Tests) das Kind selbst."""
        for name in ("SIGTERM", "SIGKILL"):
            sig = getattr(signal, name, None)
            if proc.poll() is not None:  # schon beendet (und eingesammelt): keine Signale an eine womöglich neu vergebene Kennung
                break
            try:
                if hasattr(os, "killpg") and sig is not None:
                    os.killpg(proc.pid, sig)
                elif name == "SIGTERM":
                    proc.terminate()
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError, OSError):
                break
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue

    def _lesen(self, proc: subprocess.Popen) -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            while stueck := stream.read1(65536):
                self._puffer.add(stueck)
        except (OSError, ValueError):
            pass

    def ausfuehren(self) -> Ergebnis:
        start = time.monotonic()
        if self._gestoppt.is_set():
            return Ergebnis(None, True, self._grund, 0.0)
        try:
            proc = self._popen(self._argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=self._env, cwd=self._cwd, start_new_session=os.name == "posix")
        except OSError as e:
            return Ergebnis(None, False, f"Sandbox nicht startbar ({type(e).__name__})", time.monotonic() - start)
        self._proc = proc
        if self._gestoppt.is_set():  # stoppen() kam, bevor der Prozess bekannt war
            self._beenden(proc)
        leser = threading.Thread(target=self._lesen, args=(proc,), daemon=True)
        leser.start()
        try:
            rc = proc.wait(timeout=self._frist_s)
        except subprocess.TimeoutExpired:
            self.stoppen("zeitlimit")
            rc = proc.wait()
        self._proc = None  # das Kind ist eingesammelt: ein spätes stoppen() (Ende der Lebenslinie) signalisiert nichts mehr
        leser.join(timeout=5)  # hält ein Enkel den Pipe-Ausgang offen, zählt, was bis hierhin kam
        ausgabe, verworfen = self._puffer.ende()
        e = Ergebnis(rc if type(rc) is int else None, self._gestoppt.is_set(), self._grund, time.monotonic() - start, ausgabe, verworfen)
        return replace(e, rc=None) if e.abgebrochen else e
