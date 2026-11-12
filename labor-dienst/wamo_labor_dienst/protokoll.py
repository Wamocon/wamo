"""Protokoll des Dienstes: JSON-Zeilen nur mit Metadaten (D10), nie mit Ticket-, Kommentar- oder Einwilligungstexten und nie mit Tokens.

Aufrufer übergeben nur feste Bezeichner und Zahlen (Bereich, Methode, Status, Platz, Dauer, Art der Betreuer-Aktion). Werte, die keine einfachen
Typen sind, werden verworfen, lange Zeichenketten gekürzt. Das Schreiben scheitert nie laut: Ein Protokollfehler darf den Dienst nicht stören.
Die Datei rotiert an einer Obergrenze (eine Sicherung) und wird nach der Aufbewahrung gelöscht (Dienst.aufbewahrung_pruefen).
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .modell import iso_zeit

DATEI = "dienst.jsonl"
MAX_BYTES = 10 * 1024 * 1024
WERT_MAX = 80


class Protokoll:
    def __init__(self, ordner: Path, uhr: Callable[[], float], max_bytes: int = MAX_BYTES):
        self.ordner, self._uhr, self._max = Path(ordner), uhr, max_bytes
        self._sperre = threading.Lock()
        self._gemeldet = False

    @property
    def datei(self) -> Path:
        return self.ordner / DATEI

    def ereignis(self, art: str, **felder: object) -> None:
        eintrag: dict[str, object] = {"zeit": iso_zeit(self._uhr()), "art": art}
        for name, wert in felder.items():
            if wert is None or isinstance(wert, (bool, int, float)):
                eintrag[name] = wert
            elif isinstance(wert, str):
                eintrag[name] = wert[:WERT_MAX]
        zeile = json.dumps(eintrag, ensure_ascii=True, separators=(",", ":")) + "\n"
        try:
            with self._sperre:
                self._schreiben(zeile)
        except OSError:
            if not self._gemeldet:  # einmal, ohne Pfad und Einzelheiten
                self._gemeldet = True
                print("Protokoll nicht schreibbar", file=sys.stderr)

    def _schreiben(self, zeile: str) -> None:
        self.ordner.mkdir(parents=True, exist_ok=True)
        groesse = self.datei.stat().st_size if self.datei.exists() else 0
        if groesse and groesse + len(zeile) > self._max:
            os.replace(self.datei, self.ordner / (DATEI + ".1"))
        with self.datei.open("a", encoding="utf-8", newline="\n") as f:
            f.write(zeile)

    def leeren(self) -> None:
        """Löscht Protokoll und Sicherung (Aufbewahrung nach D10)."""
        with self._sperre:
            for name in (DATEI, DATEI + ".1"):
                with contextlib.suppress(OSError):
                    (self.ordner / name).unlink()
