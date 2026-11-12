"""Protokoll des Dienstes: JSON-Zeilen nur mit Metadaten (D10), nie mit Ticket-, Kommentar- oder Einwilligungstexten und nie mit Tokens.

Zwei Dateien: dienst.jsonl (alle Anfragen und Fehler, rotiert an einer Obergrenze mit einer Sicherung) und betreuer.jsonl (die Spur der
Betreuer-Aufrufe mit gültigem Token: rotiert nie, hat nur eine Obergrenze und überschreibt nie). Anfragen ohne gültigen Zugang werden abgetastet
(abgetastet): je Art, Bereich und Status höchstens wenige Zeilen je Minute, der Rest wird gezählt und als eine Zeile zusammengefasst. So kann eine
Flut unberechtigter Anfragen weder die Betreuer-Spur wegspülen noch die Platte füllen.

Das Schreiben geschieht in einem eigenen Faden hinter einer begrenzten Warteschlange: Die Ereignisschleife wartet nie auf die Platte. Ist die
Warteschlange voll, werden Zeilen verworfen und gezählt. Aufrufer übergeben nur feste Bezeichner und Zahlen; Werte, die keine einfachen Typen
sind, werden verworfen, lange Zeichenketten gekürzt. Ein Protokollfehler stört den Dienst nie. Nach der Aufbewahrung wird alles gelöscht (leeren).
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .modell import iso_zeit

DATEI = "dienst.jsonl"
BETREUER_DATEI = "betreuer.jsonl"
MAX_BYTES = 10 * 1024 * 1024
BETREUER_MAX_BYTES = 5 * 1024 * 1024
WERT_MAX = 80
ABTAST_JE_MINUTE = 5
ABTAST_FENSTER_SECS = 60.0
WARTESCHLANGE_MAX = 5000
LEERLAUF_SECS = 2.0
ABWARTEN_SECS = 10.0


class Protokoll:
    def __init__(self, ordner: Path, uhr: Callable[[], float], max_bytes: int = MAX_BYTES, *, betreuer_max_bytes: int = BETREUER_MAX_BYTES,
                 abtast_je_minute: int = ABTAST_JE_MINUTE, warteschlange_max: int = WARTESCHLANGE_MAX, leerlauf_secs: float = LEERLAUF_SECS):
        self.ordner, self._uhr, self._max, self._betreuer_max = Path(ordner), uhr, max_bytes, betreuer_max_bytes
        self._abtast_max, self._leerlauf = abtast_je_minute, leerlauf_secs
        self._schlange: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=warteschlange_max)
        self._faden: threading.Thread | None = None
        self._start_sperre = threading.Lock()
        self._datei_sperre = threading.Lock()
        self._abtast_sperre = threading.Lock()
        self._abtast: dict[tuple, list[float | int]] = {}  # (art, bereich, status) -> [Beginn, geschrieben, unterdrückt]
        self._verworfen = 0
        self._gemeldet = False

    @property
    def datei(self) -> Path:
        return self.ordner / DATEI

    # ---------- Ereignisse ----------
    def _zeile(self, art: str, felder: dict[str, object]) -> str:
        eintrag: dict[str, object] = {"zeit": iso_zeit(self._uhr()), "art": art}
        for name, wert in felder.items():
            if wert is None or isinstance(wert, (bool, int, float)):
                eintrag[name] = wert
            elif isinstance(wert, str):
                eintrag[name] = wert[:WERT_MAX]
        return json.dumps(eintrag, ensure_ascii=True, separators=(",", ":")) + "\n"

    def _einreihen(self, datei: str, zeile: str) -> None:
        try:
            self._schlange.put_nowait((datei, zeile))
        except queue.Full:
            self._verworfen += 1  # die Zählung genügt, die Ereignisschleife wartet nie
            return
        with self._start_sperre:
            if self._faden is None or not self._faden.is_alive():
                self._faden = threading.Thread(target=self._arbeiten, name="protokoll", daemon=True)
                self._faden.start()

    def ereignis(self, art: str, **felder: object) -> None:
        """Eine Zeile in dienst.jsonl (rotierend)."""
        self._einreihen(DATEI, self._zeile(art, felder))

    def betreuer(self, **felder: object) -> None:
        """Eine Zeile der Betreuer-Spur (betreuer.jsonl): nur für Aufrufe mit gültigem Betreuer-Token, nie abgetastet, nie rotiert."""
        self._einreihen(BETREUER_DATEI, self._zeile("betreuer", felder))

    def abgetastet(self, art: str, **felder: object) -> None:
        """Wie ereignis, aber je (art, bereich, status) nur die ersten Zeilen je Minute. Der Rest wird gezählt und beim Wechsel des Fensters als
        eine Zeile 'abgetastet' mit der Zahl geschrieben."""
        schluessel = (art, felder.get("bereich"), felder.get("status"))
        jetzt = self._uhr()
        fertig: int = 0
        with self._abtast_sperre:
            beginn, geschrieben, unterdrueckt = self._abtast.get(schluessel, [jetzt, 0, 0])
            if jetzt - beginn >= ABTAST_FENSTER_SECS or jetzt < beginn:  # neues Fenster (auch wenn die Uhr zurückgestellt wurde)
                fertig, beginn, geschrieben, unterdrueckt = int(unterdrueckt), jetzt, 0, 0
            schreiben = geschrieben < self._abtast_max
            geschrieben, unterdrueckt = (geschrieben + 1, unterdrueckt) if schreiben else (geschrieben, unterdrueckt + 1)
            self._abtast[schluessel] = [beginn, geschrieben, unterdrueckt]
        if fertig:
            self._zusammenfassung(schluessel, fertig)
        if schreiben:
            self.ereignis(art, **felder)

    def _zusammenfassung(self, schluessel: tuple, anzahl: int) -> None:
        art, bereich, status = schluessel
        self.ereignis("abgetastet", abgelehnt=art, bereich=bereich, status=status, anzahl=anzahl)

    # ---------- Schreiben im eigenen Faden ----------
    def _arbeiten(self) -> None:
        while True:
            try:
                datei, zeile = self._schlange.get(timeout=self._leerlauf)
            except queue.Empty:
                with self._start_sperre:  # nur beenden, wenn nichts mehr wartet (ein Aufrufer startet sonst einen neuen Faden)
                    if self._schlange.empty():
                        self._faden = None
                        return
                continue
            try:
                self._verworfenes_melden()
                self._schreiben(datei, zeile)
            except Exception:  # noqa: BLE001 (ein Protokollfehler darf den Dienst nie stören)
                if not self._gemeldet:  # einmal, ohne Pfad und Einzelheiten
                    self._gemeldet = True
                    print("Protokoll nicht schreibbar", file=sys.stderr)
            finally:
                self._schlange.task_done()

    def _verworfenes_melden(self) -> None:
        if self._verworfen:
            anzahl, self._verworfen = self._verworfen, 0
            self._schreiben(DATEI, self._zeile("verworfen", {"anzahl": anzahl}))

    def _schreiben(self, datei: str, zeile: str) -> None:
        with self._datei_sperre:
            self.ordner.mkdir(parents=True, exist_ok=True)
            pfad = self.ordner / datei
            groesse = pfad.stat().st_size if pfad.exists() else 0
            if datei == BETREUER_DATEI:
                if groesse + len(zeile) > self._betreuer_max:
                    return  # nie überschreiben, nie rotieren
            elif groesse and groesse + len(zeile) > self._max:
                os.replace(pfad, self.ordner / (datei + ".1"))
            with pfad.open("a", encoding="utf-8", newline="\n") as f:
                f.write(zeile)

    # ---------- Ende, Warten, Löschen ----------
    def abwarten(self) -> None:
        """Wartet (höchstens ABWARTEN_SECS), bis alle eingereihten Zeilen geschrieben sind. Für Tests und das Ende der Anwendung."""
        ende = time.monotonic() + ABWARTEN_SECS
        while self._schlange.unfinished_tasks and time.monotonic() < ende:
            time.sleep(0.005)

    def beenden(self) -> None:
        """Schreibt die offenen Zähler der Abtastung und wartet auf den Schreiber."""
        with self._abtast_sperre:
            offen = [(k, int(v[2])) for k, v in self._abtast.items() if v[2]]
            for k, _ in offen:
                self._abtast[k][2] = 0
        for schluessel, anzahl in offen:
            self._zusammenfassung(schluessel, anzahl)
        self.abwarten()

    def leeren(self) -> None:
        """Löscht Protokoll, Sicherung und Betreuer-Spur (Aufbewahrung nach D10)."""
        self.abwarten()
        with self._datei_sperre, self._abtast_sperre:
            self._abtast.clear()
            for name in (DATEI, DATEI + ".1", BETREUER_DATEI):
                with contextlib.suppress(OSError):
                    (self.ordner / name).unlink()
