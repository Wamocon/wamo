"""Eigenes Audit des Gerätedienstes: eine JSON-Zeile je Aufruf, OHNE Inhalte (keine Dokumenttexte, keine Scans, keine
Gerätelisten, keine Dateinamen vom NAS, keine Geheimnisse). Zeit, Werkzeug, Gerät, Profil (Behauptung des Kerns),
Rolle, Ergebnis und Dauer genügen zum Nachweis. Ablage im StateDirectory (/var/lib/wamo-geraete/audit.jsonl),
Rotation bei 10 MB, drei ältere Dateien bleiben (rund 12 Monate bei üblicher Nutzung, F17)."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

GRENZE = 10 * 1024 * 1024
ALTE = 3
_SPERRE = threading.Lock()


class Audit:
    def __init__(self, pfad: str | None):
        self.pfad = pfad

    def schreiben(self, eintrag: dict) -> None:
        if not self.pfad:
            return
        zeile = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **eintrag},
                           ensure_ascii=False, default=str) + "\n"
        with _SPERRE:
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
