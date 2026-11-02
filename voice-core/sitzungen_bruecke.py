"""Ablöse-Brücke zwischen zwei Kern-Instanzen (Schritt 2.3 der Selbstüberarbeitung, Konzept docs/selbstueberarbeitung.md Abschnitt A.3).

Zwei Instanzen (Slot a und b, wamo-core@a und wamo-core@b) laufen kurz nebeneinander. Jede kennt nur die eigenen Sitzungen (sitzungen.py), also
bräuchte eine neue Sitzung auf Instanz b die laufende desselben Profils auf Instanz a nicht zu bemerken: zwei Pipelines sprächen gleichzeitig,
und a hielte die PC-Sperre. Diese Brücke macht die Sitzungen für die andere Instanz sichtbar, ohne Netz und ohne Datenbank:

  <ordner>/<user_id>.<slot>              Datei einer Sitzung dieser Instanz (leer, nur der Name zählt)
  <ordner>/<user_id>.abloesen.<slot>     Marke der neuen Instanz: "deine Sitzung dieses Profils ist abzulösen"

Der Ordner (Standard /run/wamo-core/sitzungen) gehört wamo-core mit Modus 0700. Namen bestehen nur aus einer UUID, dem Slot a oder b und dem Wort
abloesen; alles andere wird ignoriert. Es steht nie ein Token, ein Name oder ein Inhalt in einer Datei. Ohne WAMO_SLOT (alte Unit, Entwicklung)
oder wenn der Ordner sich nicht anlegen lässt, ist die Brücke aus und jede Instanz arbeitet wie bisher allein: das verfehlt höchstens die Ablösung
über Instanzen hinweg, es stört nie eine Sitzung.
"""

from __future__ import annotations

import os
import re
import time

from loguru import logger

SLOTS = ("a", "b")
MARKE_MAX_ALTER_S = 60.0  # ältere Marken sind verwaist und werden gelöscht, nie ausgeführt
ORDNER_STANDARD = "/run/wamo-core/sitzungen"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DATEI_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.([ab])")
_MARKE_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.abloesen\.([ab])")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class Bruecke:
    """Dateien für die Sitzungen dieser Instanz und Marken an die andere. aktiv ist falsch, wenn kein Slot gesetzt ist oder der Ordner fehlt."""

    def __init__(self, ordner: str | None, slot: str | None):
        self.ordner = ordner if ordner and slot in SLOTS else None
        self.slot = slot if self.ordner else None
        if self.ordner:
            try:
                os.makedirs(self.ordner, mode=0o700, exist_ok=True)
            except OSError as e:
                logger.warning("Sitzungsordner {} lässt sich nicht anlegen ({}): Ablösung über Instanzen hinweg ist aus", self.ordner, type(e).__name__)
                self.ordner = self.slot = None

    @classmethod
    def aus_umgebung(cls) -> "Bruecke":
        """Aus WAMO_SLOT (a oder b) und WAMO_SITZUNGEN_DIR; ohne Slot ist die Brücke aus."""
        slot = (os.getenv("WAMO_SLOT") or "").strip()
        if slot not in SLOTS:
            return cls(None, None)
        return cls((os.getenv("WAMO_SITZUNGEN_DIR") or ORDNER_STANDARD).strip(), slot)

    @property
    def aktiv(self) -> bool:
        return self.ordner is not None

    @property
    def andere(self) -> str | None:
        """Der Slot der anderen Instanz."""
        return None if self.slot is None else ("b" if self.slot == "a" else "a")

    def _pfad(self, user_id: str, endung: str) -> str | None:
        if not self.aktiv or not UUID_RE.fullmatch(user_id or ""):
            return None
        return os.path.join(self.ordner, f"{user_id}.{endung}")  # type: ignore[arg-type]

    @staticmethod
    def _anlegen(pfad: str) -> bool:
        try:
            fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | _NOFOLLOW, 0o600)
            os.close(fd)
            return True
        except OSError as e:
            logger.warning("Datei der Sitzungsbrücke ließ sich nicht anlegen ({})", type(e).__name__)
            return False

    @staticmethod
    def _weg(pfad: str) -> None:
        try:
            os.unlink(pfad)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Datei der Sitzungsbrücke ließ sich nicht löschen ({})", type(e).__name__)

    # --- eigene Sitzungen
    def eintragen(self, user_id: str) -> bool:
        p = self._pfad(user_id, self.slot or "")
        return bool(p) and self._anlegen(p)  # type: ignore[arg-type]

    def austragen(self, user_id: str) -> None:
        p = self._pfad(user_id, self.slot or "")
        if p:
            self._weg(p)

    # --- die andere Instanz
    def andere_hat(self, user_id: str) -> bool:
        p = self._pfad(user_id, self.andere or "")
        return bool(p and os.path.lexists(p))

    def marke_setzen(self, user_id: str) -> bool:
        """Marke an die andere Instanz: ihre Sitzung dieses Profils ist abzulösen."""
        p = self._pfad(user_id, f"abloesen.{self.andere}")
        return bool(p) and self._anlegen(p)  # type: ignore[arg-type]

    def marke_loeschen(self, user_id: str, fuer: str | None = None) -> None:
        """Löscht die Marke an die andere Instanz (Standard) oder die an diese (fuer=eigener Slot)."""
        p = self._pfad(user_id, f"abloesen.{fuer or self.andere}")
        if p:
            self._weg(p)

    def marken_fuer_mich(self) -> list[str]:
        """Profile, deren Sitzung auf dieser Instanz abzulösen ist."""
        if not self.aktiv:
            return []
        try:
            namen = os.listdir(self.ordner)
        except OSError:
            return []
        aus = []
        for n in namen:
            m = _MARKE_RE.fullmatch(n)
            if not (m and m.group(2) == self.slot):
                continue
            pfad = os.path.join(self.ordner, n)
            try:
                alter = time.time() - os.stat(pfad).st_mtime
            except OSError:
                continue
            if alter > MARKE_MAX_ALTER_S:
                # eine verwaiste Marke (die neue Sitzung ist längst weg) darf eine spätere, rechtmäßige Sitzung nie ablösen
                self._weg(pfad)
                continue
            aus.append(m.group(1))
        return aus

    # --- Zählung und Aufräumen
    def zaehle(self, slot: str | None = None) -> int:
        """Zahl der Sitzungen eines Slots (Standard: dieser). Für die Anzeige und die Ruheprüfung des Ankers."""
        if not self.aktiv:
            return 0
        ziel = slot or self.slot
        try:
            return sum(1 for n in os.listdir(self.ordner) if (m := _DATEI_RE.fullmatch(n)) and m.group(2) == ziel)
        except OSError:
            return 0

    def aufraeumen(self) -> int:
        """Beim Start: Dateien und Marken dieser Instanz aus einem früheren Lauf löschen (nach einem Absturz bleiben sie liegen)."""
        if not self.aktiv:
            return 0
        n = 0
        try:
            namen = os.listdir(self.ordner)
        except OSError:
            return 0
        for name in namen:
            d, m = _DATEI_RE.fullmatch(name), _MARKE_RE.fullmatch(name)
            if (d and d.group(2) == self.slot) or (m and m.group(2) == self.slot):
                self._weg(os.path.join(self.ordner, name))
                n += 1
        return n
