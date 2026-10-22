"""Eigenes Audit des Gerätedienstes: eine JSON-Zeile je Aufruf, OHNE Inhalte (keine Dokumenttexte, keine Scans, keine
Gerätelisten, keine Dateinamen vom NAS, keine Geheimnisse). Zeit, Werkzeug, Gerät, Profil (Behauptung des Kerns),
Rolle, Ergebnis und Dauer genügen zum Nachweis. Ablage im StateDirectory (/var/lib/wamo-geraete/audit.jsonl),
Rotation bei 10 MB, drei ältere Dateien bleiben (rund 12 Monate bei üblicher Nutzung, F17).

Jedes Feld wird gekürzt (Zeichenketten höchstens 64 Zeichen), damit keine Anfrage das Audit aufblähen kann. Abgelehnte
Anfragen und Grenzen (drosseln=True) schreibt es höchstens 30 je Minute; der Rest wird gezählt und als eine Zeile
"weitere abgelehnte Anfragen" nachgetragen.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

GRENZE = 10 * 1024 * 1024
ALTE = 3
FELD_ZEICHEN = 64
DROSSEL_JE_MINUTE = 30
_SPERRE = threading.Lock()


def _kurz(wert):
    if isinstance(wert, str):
        return "".join(c if c.isprintable() else " " for c in wert)[:FELD_ZEICHEN]
    if isinstance(wert, (int, float, bool)) or wert is None:
        return wert
    return str(wert)[:FELD_ZEICHEN]


class Audit:
    def __init__(self, pfad: str | None, uhr=time.monotonic):
        self.pfad = pfad
        self._uhr = uhr
        self._fenster_start = uhr()
        self._geschrieben = 0
        self._verworfen = 0

    def schreiben(self, eintrag: dict, drosseln: bool = False) -> None:
        if not self.pfad:
            return
        with _SPERRE:
            jetzt = self._uhr()
            if jetzt - self._fenster_start >= 60:
                if self._verworfen:
                    self._zeile({"werkzeug": "-", "ergebnis": "weitere abgelehnte Anfragen", "anzahl": self._verworfen})
                self._fenster_start, self._geschrieben, self._verworfen = jetzt, 0, 0
            if drosseln:
                if self._geschrieben >= DROSSEL_JE_MINUTE:
                    self._verworfen += 1
                    return
                self._geschrieben += 1
            self._zeile(eintrag)

    def _zeile(self, eintrag: dict) -> None:
        zeile = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            **{str(k)[:20]: _kurz(v) for k, v in eintrag.items()}},
                           ensure_ascii=False, default=str) + "\n"
        try:
            if os.path.exists(self.pfad) and os.path.getsize(self.pfad) > GRENZE:
                for i in range(ALTE, 0, -1):
                    von = self.pfad if i == 1 else f"{self.pfad}.{i - 1}"
                    if os.path.exists(von):
                        os.replace(von, f"{self.pfad}.{i}")
            fd = os.open(self.pfad, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(zeile)
        except OSError:
            pass  # das Audit darf den Dienst nicht anhalten; systemd-Journal hat die Kurzzeile
