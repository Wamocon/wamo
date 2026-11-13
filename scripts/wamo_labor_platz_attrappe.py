"""Attrappen für die Tests von scripts/wamo_labor_platz.py (nur für Tests, im Betrieb nie benutzt).

Nichts hier greift auf die echte Registrierung, den echten Zertifikatsspeicher, echte Prozesse oder das Netz zu. Die Attrappen zählen
ihre Schreibzugriffe, damit die Tests Idempotenz und Trockenlauf belegen können, und bieten Haken, mit denen ein Test prüft, was
VOR einer Änderung schon vorhanden sein muss (zum Beispiel die Sicherung).
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Callable


class FakeRegistrierung:
    """Registrierung im Speicher. Wie unter Windows entstehen beim Setzen alle Elternschlüssel, Namen der Schlüssel sind unempfindlich
    gegen Groß und Kleinschreibung (hier klein gespeichert)."""

    def __init__(self, vorhanden: tuple[str, ...] = ("software", "software\\policies")) -> None:
        self._k: dict[tuple[str, str], dict[str, tuple[str, object]]] = {}
        for pfad in vorhanden:
            self._k[("HKCU", pfad)] = {}
        self.schreibzugriffe = 0
        self.vor_schreiben: Callable[[], None] | None = None

    @staticmethod
    def _id(hive: str, pfad: str) -> tuple[str, str]:
        return hive.upper(), pfad.strip("\\").lower()

    def _schreiben(self) -> None:
        self.schreibzugriffe += 1
        if self.vor_schreiben is not None:
            self.vor_schreiben()

    def vorbelegen(self, hive: str, pfad: str, werte: dict[str, tuple[str, object]]) -> None:
        """Zustand VOR dem Test herstellen (zählt nicht als Schreibzugriff)."""
        teile = pfad.strip("\\").split("\\")
        for i in range(1, len(teile) + 1):
            self._k.setdefault(self._id(hive, "\\".join(teile[:i])), {})
        self._k[self._id(hive, pfad)].update(werte)

    def schluessel_vorhanden(self, hive: str, pfad: str) -> bool:
        return self._id(hive, pfad) in self._k

    def werte(self, hive: str, pfad: str) -> dict[str, tuple[str, object]]:
        return dict(self._k.get(self._id(hive, pfad), {}))

    def unterschluessel(self, hive: str, pfad: str) -> list[str]:
        h, p = self._id(hive, pfad)
        vorspann = p + "\\"
        return sorted(k[len(vorspann):] for (kh, k) in self._k
                      if kh == h and k.startswith(vorspann) and "\\" not in k[len(vorspann):])

    def setzen(self, hive: str, pfad: str, name: str, typ: str, wert: object) -> None:
        self._schreiben()
        teile = pfad.strip("\\").split("\\")
        for i in range(1, len(teile) + 1):
            self._k.setdefault(self._id(hive, "\\".join(teile[:i])), {})
        self._k[self._id(hive, pfad)][name] = (typ, wert)

    def wert_loeschen(self, hive: str, pfad: str, name: str) -> None:
        werte = self._k.get(self._id(hive, pfad))
        if werte is not None and name in werte:
            self._schreiben()
            del werte[name]

    def leeren_schluessel_loeschen(self, hive: str, pfad: str) -> bool:
        kennung = self._id(hive, pfad)
        if kennung not in self._k or self._k[kennung] or self.unterschluessel(hive, pfad):
            return False
        self._schreiben()
        del self._k[kennung]
        return True

    def momentaufnahme(self) -> dict[tuple[str, str], dict[str, tuple[str, object]]]:
        return {k: dict(v) for k, v in self._k.items()}


_PEM = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.S)


def sha1_der(pem_text: str) -> str:
    return hashlib.sha1(base64.b64decode("".join(_PEM.search(pem_text).group(1).split()))).hexdigest()


class FakeZertifikatsspeicher:
    """Benutzerspeicher Root im Speicher. Jeder Aufruf ohne -user und Root ist ein Fehler des Tests (nie der Rechnerspeicher)."""

    def __init__(self, vorhanden: tuple[str, ...] = ()) -> None:
        self.speicher: set[str] = set(vorhanden)
        self.aufrufe: list[list[str]] = []
        self.vor_aenderung: Callable[[], None] | None = None
        self.ablehnen = False
        self.aenderungen = 0

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.aufrufe.append(list(args))
        if args[:1] != ["-user"] or "Root" not in args:
            raise AssertionError("certutil nur mit -user und Root erlaubt: " + " ".join(args))
        aktion = args[1]
        if aktion == "-store":
            return (0, "ok") if args[3].lower() in self.speicher else (1, "nicht gefunden")
        if aktion in ("-addstore", "-delstore"):
            if self.vor_aenderung is not None:
                self.vor_aenderung()
            return self._aendern(aktion, args[3])
        raise AssertionError("unbekannte certutil-Aktion: " + aktion)

    def _aendern(self, aktion: str, ziel: str) -> tuple[int, str]:
        if aktion == "-addstore":
            if self.ablehnen:
                return 1, "abgelehnt"
            self.speicher.add(sha1_der(Path(ziel).read_text(encoding="ascii")))
        else:
            self.speicher.discard(ziel.lower())
        self.aenderungen += 1
        return 0, "ok"


class ProzessMitschnitt:
    """Hält jede Befehlszeile fest, die gestartet werden soll. Gestartet wird nie etwas. `bei_start` darf ein Verhalten nachspielen."""

    def __init__(self) -> None:
        self.aufrufe: list[list[str]] = []
        self.bei_start: Callable[[list[str]], None] | None = None

    def __call__(self, args: list[str]) -> None:
        self.aufrufe.append(list(args))
        if self.bei_start is not None:
            self.bei_start(list(args))


class VerknuepfungAttrappe:
    """Legt statt der Verknüpfung eine kleine Textdatei an (Ziel und Argumente), damit Existenz und Inhalt prüfbar sind."""

    def __init__(self) -> None:
        self.aufrufe: list[tuple[Path, str, str, str]] = []

    def __call__(self, pfad: Path, ziel: str, argumente: str, arbeitsordner: str) -> None:
        self.aufrufe.append((pfad, ziel, argumente, arbeitsordner))
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(f"{ziel}\n{argumente}\n{arbeitsordner}\n", encoding="utf-8")
