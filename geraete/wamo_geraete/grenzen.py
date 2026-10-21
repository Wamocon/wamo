"""Grenzen je Profil und gesamt (Plan 5.4) und ein kurzer Zwischenspeicher für Statusantworten.

  Status       30 Aufrufe je Minute und Profil (gesamt 120)
  Steuern      20 je Minute und Profil (Schalten, Lautstärke, Eingang)
  Drucken      10 Aufträge je Stunde und Profil; Seiten je Tag und Profil und gesamt aus geraete.toml
  Scannen      20 je Stunde und Profil, ein Auftrag zugleich je Scanner
  NAS          30 Suchen oder Abrufe je Minute und Profil
  Ablage       30 Aufrufe je Minute und Profil (Liste, Holen, Löschen)
Die Zähler für Druckseiten überleben einen Neustart (zaehler.json im StateDirectory), die übrigen nicht.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import date

FENSTER = {
    "status": (60.0, 30, 120),
    "steuern": (60.0, 20, 60),
    "druck_auftrag": (3600.0, 10, 40),
    "scan": (3600.0, 20, 60),
    "nas": (60.0, 30, 60),
    "router": (60.0, 6, 12),
    "ablage": (60.0, 30, 90),
}


class GrenzeErreicht(RuntimeError):
    pass


class Grenzen:
    def __init__(self, zaehler_datei: str | None = None, uhr=time.monotonic, heute=date.today):
        self._uhr = uhr
        self._heute = heute
        self._ereignisse: dict[tuple[str, str], deque[float]] = {}
        self._datei = zaehler_datei
        self._seiten: dict[str, int] = {}
        self._tag = ""
        self._laden()

    def _laden(self) -> None:
        if not self._datei:
            return
        try:
            with open(self._datei, encoding="utf-8") as f:
                roh = json.load(f)
            if isinstance(roh, dict) and isinstance(roh.get("seiten"), dict):
                self._tag = str(roh.get("tag", ""))
                self._seiten = {str(k): int(v) for k, v in roh["seiten"].items() if isinstance(v, int)}
        except (OSError, ValueError):
            pass

    def _speichern(self) -> None:
        if not self._datei:
            return
        tmp = f"{self._datei}.neu"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"tag": self._tag, "seiten": self._seiten}, f)
            os.replace(tmp, self._datei)
        except OSError:
            pass

    def pruefen(self, art: str, user_id: str) -> None:
        """Zählt einen Aufruf; wirft GrenzeErreicht, wenn das Profil oder alle zusammen das Fenster ausschöpfen."""
        fenster, je_profil, gesamt = FENSTER[art]
        jetzt = self._uhr()
        for schluessel, grenze in (((art, user_id), je_profil), ((art, "*"), gesamt)):
            q = self._ereignisse.setdefault(schluessel, deque())
            while q and jetzt - q[0] > fenster:
                q.popleft()
            if len(q) >= grenze:
                raise GrenzeErreicht(art)
        self._ereignisse[(art, user_id)].append(jetzt)
        self._ereignisse[(art, "*")].append(jetzt)

    def _tag_pruefen(self) -> None:
        heute = self._heute().isoformat()
        if self._tag != heute:
            self._tag, self._seiten = heute, {}

    def seiten_reservieren(self, user_id: str, seiten: int, je_profil: int, gesamt: int) -> str | None:
        """Seiten vor dem Senden reservieren (prüfen und buchen in einem Schritt, damit zwei gleichzeitige Aufträge das
        Tageslimit nicht gemeinsam überschreiten). Liefert die Tagesmarke für seiten_zurueck() oder None, wenn das
        Limit erreicht wäre."""
        if not self.seiten_frei(user_id, seiten, je_profil, gesamt):
            return None
        self.seiten_buchen(user_id, seiten)
        return self._tag

    def seiten_zurueck(self, user_id: str, seiten: int, tag: str) -> None:
        """Reservierung zurücknehmen, wenn der Druck gescheitert ist (nur am selben Tag)."""
        if tag != self._tag:
            return
        rest = self._seiten.get(user_id, 0) - seiten
        if rest > 0:
            self._seiten[user_id] = rest
        else:
            self._seiten.pop(user_id, None)
        self._speichern()

    def seiten_frei(self, user_id: str, seiten: int, je_profil: int, gesamt: int) -> bool:
        self._tag_pruefen()
        return (self._seiten.get(user_id, 0) + seiten <= je_profil
                and sum(self._seiten.values()) + seiten <= gesamt)

    def seiten_buchen(self, user_id: str, seiten: int) -> None:
        self._tag_pruefen()
        self._seiten[user_id] = self._seiten.get(user_id, 0) + seiten
        self._speichern()


class Zwischenspeicher:
    """Statusantworten je Gerät für ttl Sekunden (der Status gilt für alle Profile gleich)."""

    def __init__(self, ttl: float = 20.0, uhr=time.monotonic):
        self.ttl = ttl
        self._uhr = uhr
        self._werte: dict[str, tuple[float, dict]] = {}

    def holen(self, schluessel: str) -> dict | None:
        eintrag = self._werte.get(schluessel)
        if eintrag and self._uhr() - eintrag[0] <= self.ttl:
            return eintrag[1]
        return None

    def setzen(self, schluessel: str, wert: dict) -> None:
        self._werte[schluessel] = (self._uhr(), wert)

    def vergessen(self, schluessel: str) -> None:
        self._werte.pop(schluessel, None)
