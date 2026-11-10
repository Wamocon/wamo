"""Befehlskriterien (befehl:) auf Sokrates-2: der Kern ruft den Dispatcher-Befehl pruefen, er betritt die Sandbox nie.

Auf Sokrates-2 läuft der Auftrag in einer Sandbox (infra/sokrates2/wamo-job.sh), die der Kern nicht betreten darf, und ein Befehl, den
ein Modell sich ausdenkt, darf nicht mit den Rechten des Kerns laufen. Deshalb führt der Dispatcher ihn aus (Befehl pruefen,
infra/sokrates2/wamo_job_dispatch.py ab Version 6, standardmäßig aus): im Projekt des Auftrags, in einer engeren Sandbox (kein Netz,
kein Token, kein Claude). Dieses Modul ist die Seite des Kerns, sie besteht aus zwei Teilen:

  * RemoteAusfuehrer: startet "ssh ... pruefen <job>" mit dem vorhandenen Schlüssel der Aufträge (dev_agent.ssh_argv, ein
    Zwangsbefehl), schreibt EINE stdin-Zeile {"argv": [...]}, lässt stdin als Lebenslinie offen (EOF oder das Ende von ssh beendet
    den Lauf auf dem Server) und liest EINE JSON-Zeile als Antwort, mit Zeit- und Größengrenze. Er passt in abnahme.Ausfuehrer
    (argv, Ordner) -> (Exit, Ausgabe) und trägt die Markierung entfernt: abnahme.pruefe_befehl prüft dann keine lokalen Pfade.
  * DispatcherPing: Der Dispatcher kann pruefen erst ab Version 6 und nur mit WAMO_PRUEFEN=an. Der Kern fragt deshalb per ping, ob der
    Dispatcher pruefen meldet und dieselbe Allowlist hat (sha256 von abnahme_befehl.py), und merkt sich die Antwort. Das ist
    Komfort (schnelle Ablehnung, ehrliche Beschreibung für das Modell), maßgeblich ist der Dispatcher: er prüft jeden Aufruf neu.

Die Auswertung ist streng. Erfüllt gilt nur bei Prozess-Exit 0 UND ok true UND einem echten int 0 als exit (kein bool, kein float)
UND abgebrochen false UND v gleich 1, und nur bei einer Antwort mit genau den Feldern des Vertrags. Alles andere (ungültiges JSON,
fehlende oder zusätzliche Felder, falsche Typen, eine zweite Zeile, Prozess-Exit 2, 3, 4, 6, 124, 130 oder 255, SSH-Fehler,
Zeitüberschreitung) ist nicht erfüllt, mit einem kurzen Grund aus einer festen Liste (GRUENDE). Text aus der Antwort wird nie als Grund
und nie als Anweisung weitergereicht. Ausnahme ist die Ausgabe eines Befehls, der lief und scheiterte (exit ungleich 0): Sie ist ein
Datenfeld, höchstens 2 KiB, ohne Steuerzeichen, und geht wie lokal in den Hinweis des Kriteriums (Canvas, Vorgang, Prompt der nächsten
Runde von Claude Code, geschwärzt in abnahme.hinweis). Sie geht NIE an das Sprachmodell: dem gehen nur feste Zahlen
(dev_agent.DevAgent._announce).

cancel: Das Auflegen oder ein Abbruch des Kerns beendet ssh und ruft zusätzlich den Dispatcher-Befehl cancel <job> (zweite
Absicherung, wie in dev_agent._run_remote). Ein einzelnes Zeitlimit oder ein Fehler ruft es NICHT: cancel legt im Auftrag eine
Abbruchmarke ab, die jede weitere Prüfung desselben Auftrags abweist (bis zum nächsten Start); ssh zu beenden reicht, denn das Ende
der Lebenslinie beendet die Sandbox auf dem Server.

Messbefund (10.11.2026, Sandbox des Benutzers wamo auf Sokrates-2): Es gibt python3 3.12 (ohne pytest und ohne ruff), node 22, npm,
make, grep, eslint und git. Es FEHLEN pytest, ruff, mypy, tsc, pnpm, yarn, go, cargo und dotnet. Ein Befehl mit einem fehlenden Programm
scheitert mit "nicht gefunden" und das Kriterium gilt als nicht erfüllt. Python-Projekte gehen als uv run --no-sync --offline pytest,
nur wenn im Projekt eine .venv liegt. SANDBOX_HINWEIS sagt das dem Modell, damit es keine Kriterien ableitet, die nie erfüllbar sind.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import sys
import time
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from functools import cache
from pathlib import Path

from loguru import logger

import abnahme_befehl

ANTWORT_VERSION = 1
FELDER = frozenset({"v", "ok", "exit", "abgebrochen", "grund", "dauer_s", "ausgabe", "gekuerzt"})
ZEILE_MAX_BYTES = 8 * 1024  # die stdin-Zeile mit argv samt Zeilenende (so viel nimmt der Dispatcher höchstens an)
ANTWORT_MAX_BYTES = 64 * 1024  # die Antwortzeile: 2 KiB Ausgabe können als JSON bis zu sechsmal so groß werden
AUSGABE_BYTES = 2048
# 120 s Zeitlimit des Befehls, dazu Selbsttest der Sandbox und Nachfrist des Dispatchers (30 s): danach ist die Antwort überfällig
FRIST_S = 240.0
NACHLAUF_S = 10.0  # so lange darf ssh nach der Antwortzeile noch brauchen, bis es endet
REST_FRIST_S = 1.0  # so lange wird nach der Antwortzeile geprüft, ob noch etwas kommt
ABBRUCH_FRIST_S = 15.0  # so lange darf cancel beim Auflegen dauern
PING_FRIST_S = 5.0  # je Aufruf von ping
PING_GUELTIG_S = 300.0  # eine eindeutige Antwort gilt so lange
PING_FEHLER_GUELTIG_S = 30.0  # ein Fehler (nicht erreichbar, keine Antwort) wird nur kurz gemerkt, damit nicht jeder Aufruf ssh startet
PING_MAX_BYTES = 16 * 1024

# Der Grund, warum eine Prüfung nicht erfüllt ist: eine feste Liste, jeder Eintrag höchstens 190 Zeichen (der Hinweis eines Kriteriums
# hat höchstens 200), einzeilig. Nie Text aus der Antwort des Dispatchers.
GRUENDE: dict[str, str] = {
    "keine_antwort": "Die Prüfung auf Sokrates-2 hat nicht geantwortet",
    "antwort": "Die Antwort der Prüfung auf Sokrates-2 war nicht gültig",
    "nicht_ausgefuehrt": "Der Befehl wurde auf Sokrates-2 nicht ausgeführt",
    "abgewiesen": "Sokrates-2 hat den Befehl abgewiesen (Allowlist, Wächter oder Prüfung dort abgeschaltet)",
    "kein_venv": ("nicht möglich: Im Projekt liegt keine .venv, und uv installiert nichts. Formuliere eine Aussage oder "
                  "lege die .venv im Auftrag an"),
    "selbsttest": "Der Selbsttest der Sandbox auf Sokrates-2 ist fehlgeschlagen, der Befehl lief nicht",
    "belegt": "Die Sandbox auf Sokrates-2 war belegt, der Befehl lief nicht",
    "intern": "Im Dispatcher auf Sokrates-2 ist ein Fehler aufgetreten, der Befehl lief nicht",
    "zeitlimit": "Zeitlimit des Befehls auf Sokrates-2 überschritten",
    "zeitlimit_kern": "Keine Antwort von Sokrates-2 innerhalb der Frist, der Befehl wurde abgebrochen",
    "abgebrochen": "Die Prüfung wurde abgebrochen",
    "verbindung": "Die SSH-Verbindung zu Sokrates-2 ist fehlgeschlagen",
    "unerwartet": "Unerwartetes Ende der Prüfung auf Sokrates-2",
    "zu_lang": "Der Befehl ist zu lang für die Prüfung auf Sokrates-2",
    "kern": "Die Prüfung auf Sokrates-2 ist im Kern gescheitert",
}
# Prozess-Exit des Dispatchers (EXIT_* in wamo_job_dispatch.py) auf die Gründe oben
_EXIT_GRUND = {2: "abgewiesen", 3: "selbsttest", 4: "belegt", 6: "intern", 124: "zeitlimit", 130: "abgebrochen", 255: "verbindung"}
# Der Dispatcher begründet "keine .venv" mit diesem Anfang (wamo_job_pruefen.KEIN_VENV); nur der Anfang wird verglichen, nie übernommen
_KEIN_VENV_ANFANG = "nicht möglich: Im Projektordner liegt keine .venv"

# Gründe, warum der Dispatcher pruefen nicht bietet (ping). Ein Satz, ohne Punkt: dev_agent setzt ihn in die Ablehnung ein.
PING_GRUENDE: dict[str, str] = {
    "ohne_pruefen": "Dispatcher ohne pruefen",
    "pruefsumme": "Prüfsumme der Allowlist weicht ab",
    "nicht_erreichbar": "Dispatcher auf Sokrates-2 nicht erreichbar",
}

# Was es in der Sandbox gibt und was nicht (Messbefund 10.11.2026), als Text für das Werkzeug des Sprachmodells. Jedes Beispiel besteht
# die Allowlist (ein Test sichert das).
SANDBOX_BEISPIELE = ("python3 -m unittest discover", "node --test", "npm test", "make test", "grep -q TODO README.md",
                     "uv run --no-sync --offline pytest -q")
SANDBOX_HINWEIS = (
    "Mit dem Präfix „befehl:“ läuft ein einzelner Befehl im Projektordner, in einer Sandbox auf Sokrates-2 ohne Netz, Exit 0 heißt "
    "erfüllt. Dort gibt es nur python3, node, npm, make, grep und eslint; pytest, ruff, mypy, tsc, pnpm, yarn, go, cargo und dotnet "
    "fehlen, ein Kriterium damit wäre nie erfüllbar. Python mit pytest geht nur als „uv run --no-sync --offline pytest“ und nur, wenn "
    "im Projekt eine .venv liegt. Beispiele: " + ", ".join(f"„befehl: {b}“" for b in SANDBOX_BEISPIELE)
    + ". Keine Verkettung mit ; & | oder Backticks."
)

SshArgv = Callable[[list[str]], list[str]]
Beenden = Callable[[asyncio.subprocess.Process], Awaitable[object]]
_UNSICHTBAR = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


# ---------------------------------------------------------------- Zeichen und JSON
def _bereinigt(text: str) -> str:
    """Ohne Steuer-, Format- und unsichtbare Zeichen (Zeilenumbruch und Tabulator bleiben)."""
    return "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in _UNSICHTBAR)


def _ausgabe(text: str) -> str:
    """Die Ausgabe eines Befehls als Datenfeld: ohne Steuerzeichen, höchstens 2 KiB, das Ende bleibt (dort steht das Ergebnis). Ein
    am Anfang angeschnittenes Zeichen fällt weg."""
    kodiert = _bereinigt(text).encode("utf-8")
    return kodiert[-AUSGABE_BYTES:].decode("utf-8", "ignore") if len(kodiert) > AUSGABE_BYTES else kodiert.decode("utf-8")


def _ohne_doppelte(paare: list[tuple[str, object]]) -> dict[str, object]:
    daten: dict[str, object] = {}
    for name, wert in paare:
        if name in daten:
            raise ValueError("doppeltes Feld")
        daten[name] = wert
    return daten


def _objekt(zeile: bytes, grenze: int) -> dict | None:
    """Genau eine JSON-Zeile mit einem Objekt ohne doppelte Felder, sonst None."""
    if not zeile.endswith(b"\n") or len(zeile) > grenze or zeile.count(b"\n") != 1:
        return None
    try:
        daten = json.loads(zeile.decode("utf-8"), object_pairs_hook=_ohne_doppelte)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return daten if isinstance(daten, dict) else None


def _antwort_lesen(zeile: bytes) -> dict | None:
    """Die Antwort von pruefen nach dem Vertrag: genau die Felder v, ok, exit, abgebrochen, grund, dauer_s, ausgabe, gekuerzt, mit
    genau diesen Typen (ein bool ist nie ein int, ein float nie ein int). Alles andere ist None."""
    daten = _objekt(zeile, ANTWORT_MAX_BYTES)
    if daten is None or set(daten) != FELDER:
        return None
    exit_, dauer = daten["exit"], daten["dauer_s"]
    stimmt = (type(daten["v"]) is int and daten["v"] == ANTWORT_VERSION
              and type(daten["ok"]) is bool and type(daten["abgebrochen"]) is bool and type(daten["gekuerzt"]) is bool
              and (exit_ is None or (type(exit_) is int and 0 <= exit_ <= 255))
              and (daten["grund"] is None or isinstance(daten["grund"], str))
              and type(dauer) in (int, float) and math.isfinite(dauer) and dauer >= 0
              and isinstance(daten["ausgabe"], str))
    return daten if stimmt else None


# ---------------------------------------------------------------- Auswertung (rein, ohne Prozess)
def _prozess_grund(prozess_exit: int, antwort: dict | None) -> str:
    schluessel = _EXIT_GRUND.get(prozess_exit, "unerwartet")
    grund = antwort.get("grund") if antwort else None
    if schluessel == "abgewiesen" and isinstance(grund, str) and grund.startswith(_KEIN_VENV_ANFANG):
        schluessel = "kein_venv"
    return GRUENDE[schluessel]


def auswerten(zeile: bytes, prozess_exit: int, *, mehr: bool = False) -> tuple[int | None, str]:
    """(Exit, Ausgabe) im Sinn von abnahme.Ausfuehrer: (0, "") nur bei Prozess-Exit 0, ok true, einem echten int 0 und nicht
    abgebrochen. Ein Befehl, der lief und scheiterte, gibt (sein Exit, seine Ausgabe als Datenfeld). Alles andere ist (None, ein Grund
    aus GRUENDE). mehr: Nach der Antwortzeile kam noch etwas, die Antwort gilt dann als ungültig."""
    antwort = None if mehr else _antwort_lesen(zeile)
    if prozess_exit != 0:
        return None, _prozess_grund(prozess_exit, antwort)
    if antwort is None:
        return None, GRUENDE["antwort" if mehr or zeile.strip() else "keine_antwort"]
    exit_ = antwort["exit"]
    if antwort["abgebrochen"]:
        return None, GRUENDE["abgebrochen"]
    if antwort["ok"] is True and type(exit_) is int and exit_ == 0:
        return 0, ""
    if antwort["ok"] is False and type(exit_) is int and exit_ != 0:
        return exit_, _ausgabe(antwort["ausgabe"])
    return None, GRUENDE["nicht_ausgefuehrt"]


def ping_auswerten(zeile: bytes, prozess_exit: int, lokal: str | None) -> tuple[str | None, bool]:
    """(Schlüssel aus PING_GRUENDE oder None, eindeutig). None heißt: pruefen ist möglich (ok, pruefen true, dieselbe Prüfsumme von
    abnahme_befehl.py). Nicht eindeutig sind Fehler (nicht erreichbar, keine brauchbare Antwort): sie gelten nur kurz."""
    if prozess_exit == 2:  # der Dispatcher lehnt schon den Aufruf ab: er kennt es nicht
        return "ohne_pruefen", True
    daten = _objekt(zeile, PING_MAX_BYTES) if prozess_exit == 0 else None
    if daten is None or daten.get("ok") is not True:
        return "nicht_erreichbar", False
    if daten.get("pruefen") is not True:  # fehlt das Feld (Version 5) oder ist es aus
        return "ohne_pruefen", True
    summe = daten.get("abnahme_befehl_sha256")
    if lokal is None or not isinstance(summe, str) or summe != lokal:
        return "pruefsumme", True
    return None, True


@cache
def lokale_pruefsumme() -> str | None:
    """sha256 der lokalen Datei abnahme_befehl.py (so rechnet auch der Dispatcher: Bytes der Kopie in /opt/wamo/waechter)."""
    try:
        return hashlib.sha256(Path(abnahme_befehl.__file__).read_bytes()).hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------- der Ausführer
class RemoteAusfuehrer:
    """Führt einen Befehl der Abnahme auf Sokrates-2 aus (Dispatcher-Befehl pruefen), für genau einen Auftrag.

    ssh_argv: dev_agent.ssh_argv (Zwangsbefehl-Aufruf mit dem Schlüssel der Aufträge). beenden: beendet den ssh-Prozess samt
    Kindern (dev_agent._kill_tree). abbrechen: ruft den Dispatcher-Befehl cancel <job>, nur beim Auflegen oder Abbruch. Der Aufruf
    wirft nie (außer dem Abbruch selbst): jedes Scheitern ist (None, Grund) oder (Exit, Ausgabe)."""

    entfernt = True  # abnahme.pruefe_befehl prüft dann keine lokalen Pfade: Der Projektordner liegt auf Sokrates-2

    def __init__(self, job_id: str, ssh_argv: SshArgv, *, beenden: Beenden,
                 abbrechen: Callable[[], Awaitable[object]] | None = None, frist_s: float = FRIST_S):
        self._job, self._ssh_argv, self._beenden, self._abbrechen, self._frist_s = job_id, ssh_argv, beenden, abbrechen, frist_s

    async def __call__(self, argv: Sequence[str], ordner: Path) -> tuple[int | None, str]:
        del ordner  # das Projekt kommt auf dem Server aus auftrag.json, nie vom Aufrufer
        try:
            return await self._ausfuehren(argv)
        except asyncio.CancelledError:  # Auflegen oder Abbruch: ssh ist beendet (finally in _ausfuehren), jetzt cancel
            await self._cancel()
            raise
        except Exception as e:  # noqa: BLE001 (nie eine Ausnahme nach außen: nicht erfüllt)
            logger.warning("Abnahme auf Sokrates-2: Prüfung gescheitert ({})", type(e).__name__)
            return None, GRUENDE["kern"]

    async def _cancel(self) -> None:
        if self._abbrechen is None:
            return
        try:
            await asyncio.wait_for(self._abbrechen(), ABBRUCH_FRIST_S)
        except Exception as e:  # noqa: BLE001 (der Abbruch selbst darf an cancel nicht scheitern; TimeoutError gehört dazu)
            logger.warning("Abnahme auf Sokrates-2: cancel nicht möglich ({})", type(e).__name__)

    async def _ausfuehren(self, argv: Sequence[str]) -> tuple[int | None, str]:
        zeile = json.dumps({"argv": list(argv)}, separators=(",", ":")).encode("ascii") + b"\n"
        if len(zeile) > ZEILE_MAX_BYTES:
            return None, GRUENDE["zu_lang"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._ssh_argv(["pruefen", self._job]), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=ANTWORT_MAX_BYTES, start_new_session=sys.platform != "win32")
        except (OSError, RuntimeError, ValueError) as e:  # ssh fehlt, Argument ungültig, Prozess nicht startbar
            logger.warning("Abnahme auf Sokrates-2: ssh nicht startbar ({})", type(e).__name__)
            return None, GRUENDE["verbindung"]
        try:
            return await self._gespraech(proc, zeile)
        finally:
            with contextlib.suppress(Exception):
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.close()  # EOF: auch auf dem Server das Zeichen zum Abbrechen
            await self._beenden(proc)  # ssh samt Kindern; der Dispatcher sieht das Ende der Lebenslinie

    async def _gespraech(self, proc: asyncio.subprocess.Process, zeile: bytes) -> tuple[int | None, str]:
        loop = asyncio.get_running_loop()
        ende = loop.time() + self._frist_s

        def rest() -> float:
            return max(0.001, ende - loop.time())

        try:
            proc.stdin.write(zeile)  # stdin bleibt danach OFFEN (Lebenslinie), es wird erst im finally geschlossen
            await asyncio.wait_for(proc.stdin.drain(), rest())
            roh = await asyncio.wait_for(proc.stdout.readline(), rest())
            code = await asyncio.wait_for(proc.wait(), min(rest(), NACHLAUF_S))
            mehr = await asyncio.wait_for(proc.stdout.read(1), REST_FRIST_S)
        except asyncio.TimeoutError:  # ab Python 3.11 ist das TimeoutError und damit eine OSError: darum vor OSError fangen
            logger.warning("Abnahme auf Sokrates-2: keine Antwort innerhalb der Frist, ssh wird beendet")
            return None, GRUENDE["zeitlimit_kern"]
        except ValueError:  # eine Antwortzeile über der Grenze
            return None, GRUENDE["antwort"]
        except (ConnectionError, OSError) as e:  # BrokenPipeError beim Schreiben, ssh weg
            logger.warning("Abnahme auf Sokrates-2: Verbindung gescheitert ({})", type(e).__name__)
            return None, GRUENDE["verbindung"]
        ergebnis = auswerten(roh, code, mehr=bool(mehr))
        if ergebnis[0] != 0:
            logger.info("Abnahme auf Sokrates-2: nicht erfüllt (Prozess-Exit {}, Befehl {})", code, ergebnis[0])
        return ergebnis


# ---------------------------------------------------------------- der Ping
class DispatcherPing:
    """Weiß, ob der Dispatcher pruefen bietet. grund() liest nur den Cache (blockiert nie, auch nicht im Audiopfad); auffrischen()
    fragt per ping, höchstens frist_s Sekunden je Aufruf, höchstens einmal zugleich, und wirft nie (außer dem Abbruch).

    Eine eindeutige Antwort gilt gueltig_s Sekunden (5 Minuten), ein Fehler nur fehler_gueltig_s. Ohne frische Antwort ist pruefen
    nicht möglich: Fehler heißen nicht möglich. angeboten() ist die letzte eindeutige Antwort auch nach Ablauf, nur für die
    Beschreibung des Werkzeugs (sie entscheidet nie über eine Ausführung)."""

    def __init__(self, ssh_argv: SshArgv, *, beenden: Beenden, frist_s: float = PING_FRIST_S, gueltig_s: float = PING_GUELTIG_S,
                 fehler_gueltig_s: float = PING_FEHLER_GUELTIG_S, uhr: Callable[[], float] = time.monotonic,
                 lokale_sha256: Callable[[], str | None] = lokale_pruefsumme):
        self._ssh_argv, self._beenden, self._frist_s = ssh_argv, beenden, frist_s
        self._gueltig_s, self._fehler_gueltig_s, self._uhr, self._lokal = gueltig_s, fehler_gueltig_s, uhr, lokale_sha256
        self._schluessel: str | None = "nicht_erreichbar"
        self._bis = float("-inf")
        self._zuletzt_moeglich = False
        self._sperre = asyncio.Lock()

    def _frisch(self) -> bool:
        return self._uhr() < self._bis

    def grund(self) -> str | None:
        """None: pruefen ist möglich (frische Antwort). Sonst der Grund aus PING_GRUENDE, auch wenn noch nie gefragt wurde."""
        if not self._frisch():
            return PING_GRUENDE["nicht_erreichbar"]
        return None if self._schluessel is None else PING_GRUENDE[self._schluessel]

    def angeboten(self) -> bool:
        return self._zuletzt_moeglich

    async def auffrischen(self) -> None:
        if self._frisch():
            return
        async with self._sperre:
            if self._frisch():  # ein anderer Aufruf hat es inzwischen geholt
                return
            schluessel, eindeutig = await self._holen()
            self._schluessel = schluessel
            self._bis = self._uhr() + (self._gueltig_s if eindeutig else self._fehler_gueltig_s)
            if eindeutig:
                self._zuletzt_moeglich = schluessel is None
            logger.info("Abnahme auf Sokrates-2: ping, {}", "pruefen möglich" if schluessel is None else PING_GRUENDE[schluessel])

    async def _holen(self) -> tuple[str | None, bool]:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._ssh_argv(["ping"]), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=PING_MAX_BYTES, start_new_session=sys.platform != "win32")
            loop = asyncio.get_running_loop()
            ende = loop.time() + self._frist_s
            roh = await asyncio.wait_for(proc.stdout.readline(), self._frist_s)
            code = await asyncio.wait_for(proc.wait(), max(0.001, ende - loop.time()))
            return ping_auswerten(roh, code, self._lokal())
        except Exception as e:  # noqa: BLE001 (Zeitüberschreitung, ssh fehlt, Zeile zu lang: nicht möglich, nie eine Ausnahme)
            logger.warning("Abnahme auf Sokrates-2: ping gescheitert ({})", type(e).__name__)
            return "nicht_erreichbar", False
        finally:
            if proc is not None:
                await self._beenden(proc)
