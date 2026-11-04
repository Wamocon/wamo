"""Schnittstellen der Stufen des Release-Dienstes (Konzept D.3, D.4, Schritt 0.3). Das Füllen folgt in Stufe 1 bis 3.

In diesem Schritt sind die Stufen (Übernehmen, Prüfstrecke, Review, Refactor, Version, Veröffentlichen, Bereitstellen) reine
Schnittstellen mit klaren Ein- und Ausgaben. Der Dienst ruft sie über die Klasse Stufen auf. Ausgeliefert werden zwei
Fassungen: NichtVerfuegbar (jede Stufe meldet "stufe_nicht_verfuegbar", der Standard im Betrieb) und Attrappe (für Tests und
den Rauchtest; sie liefert gültige Ergebnisse oder, was die Probe vorgibt). Kein Aufruf eines Claude-Modells, kein Netz,
kein GitHub, nichts in diesem Modul startet einen Prozess.

Vertrag jeder Stufe:
  * Eingabe ist nur der Kontext (Kennungen, bisherige Werte, Frist, Abbruch-Signal). Eine Stufe liest nie aus der Anfrage
    des Aufrufers Pfade oder Befehle.
  * Sie ist idempotent: Nach einem Absturz ruft der Dienst dieselbe Stufe erneut auf (höchstens VERSUCHE_MAX Läufe).
  * Sie prüft ctx.abgebrochen() und kehrt dann zügig zurück; ein verspätetes Ergebnis wird verworfen.
  * Ergebnis.daten enthält nur Schlüssel aus release_automat.DATEN_SCHLUESSEL; der Dienst prüft Form und Inhalt und lehnt
    alles andere ab. Eine Ausnahme gilt als Fehler (Grund stufe_ausnahme).
  * "wartet" heißt: noch nicht fertig (zum Beispiel wählt der Admin die Version noch), der Dienst fragt später erneut.

Welche Daten welche Stufe liefert, und was der Dienst danach verlangt (Tore):
  uebernehmen     basis_commit, c1_commit
  pruefen(1)      pruefstand (alle Zeilen aus PRUEFSTAND_PFLICHT[1] auf "ok")
  pruefen(2)      dito, Runde 2
  version_waehlen bump, version_vorschlag, version_final, v_commit, tree_id (wartet, bis der Admin die Version bestätigt hat)
  pruefen(3)      pruefstand (PRUEFSTAND_PFLICHT[3]) und die Klassifizierung des Ankers: klasse, treffer_anzahl, treffer_sha
  refaktorieren   r_commit oder refactor_verworfen (Konzept D.4: ändert R das Verhalten, fällt die Strecke auf C1 zurück)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from release_automat import PRUEFSTAND_PFLICHT

# Refactor (Konzept D.4): feste Werkzeuge, feste Hook-Allowlist. Alles, was ein Programm aus dem Projektordner startet,
# ist ausgeschlossen (zum Beispiel stop:format-typecheck, alle Dispatcher, observe, hookify-runtime).
REFACTOR_WERKZEUGE = ("wmc-vibecode-cleanup", "code-simplifier", "refactor-cleaner")
HOOK_ALLOWLIST = ("pre:config-protection", "stop:check-console-log", "pre:write:doc-file-warning")


def hooks_ausserhalb(namen: list[str] | tuple[str, ...]) -> list[str]:
    """Die Hooks aus namen, die nicht in der Allowlist stehen (leer: in Ordnung)."""
    return [n for n in namen if n not in HOOK_ALLOWLIST]


@dataclass(frozen=True)
class Ergebnis:
    art: str  # "ok", "fehler" oder "wartet"
    grund: str | None = None
    daten: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, **daten: object) -> "Ergebnis":
        return cls("ok", None, dict(daten))

    @classmethod
    def fehler(cls, grund: str) -> "Ergebnis":
        return cls("fehler", grund, {})

    @classmethod
    def wartet(cls) -> "Ergebnis":
        return cls("wartet", None, {})


@dataclass(frozen=True)
class Kontext:
    anfrage: str
    quelle: str
    profil: str
    status: str
    versuch: int
    daten: dict  # Kopie der bisher festgehaltenen Werte
    frist: float  # time.monotonic()-Zeitpunkt, bis zu dem die Stufe fertig sein muss
    abbruch: threading.Event

    def abgebrochen(self) -> bool:
        return self.abbruch.is_set() or time.monotonic() > self.frist


class Stufen:
    """Basisklasse mit der Schnittstelle. Jede Methode gehört zu genau einem Zustand (siehe release_automat)."""

    def arbeiten(self, ctx: Kontext) -> Ergebnis:  # arbeit (Quelle selbst, Stufe 3)
        raise NotImplementedError

    def uebernehmen(self, ctx: Kontext) -> Ergebnis:  # uebernommen: Quelle als Commit C1 festhalten
        raise NotImplementedError

    def pruefen(self, ctx: Kontext, runde: int) -> Ergebnis:  # pruefung1, pruefung2, pruefung3
        raise NotImplementedError

    def reviewen(self, ctx: Kontext) -> Ergebnis:  # review (nur lesende Agenten, Stufe 3)
        raise NotImplementedError

    def refaktorieren(self, ctx: Kontext) -> Ergebnis:  # refactor (Commit R, Stufe 3)
        raise NotImplementedError

    def version_waehlen(self, ctx: Kontext) -> Ergebnis:  # versionswahl: Vorschlag, Wahl des Admins, Release-Commit V
        raise NotImplementedError

    def veroeffentlichen(self, ctx: Kontext) -> Ergebnis:  # freigegeben: Branch, Tag, Release, main (Stufe 1.3)
        raise NotImplementedError

    def bereitstellen(self, ctx: Kontext) -> Ergebnis:  # veroeffentlicht: der Anker stellt bereit und migriert
        raise NotImplementedError

    def umschalten(self, ctx: Kontext) -> Ergebnis:  # bereitgestellt: der Anker schaltet um
        raise NotImplementedError

    def bewaehren(self, ctx: Kontext) -> Ergebnis:  # umgeschaltet: Bewährung durch den Anker; "wartet", solange sie läuft
        raise NotImplementedError

    def anzeige(self, daten: dict) -> dict | None:
        """Reine Anzeige für den Befehl status (nur in pruefung3 und wartet_freigabe): der Block pruefung {diff, wirkung, empfehlung} (release_anzeige.py) oder None.
        Kein Tor und kein Teil der Hashkette; ein Fehler oder eine Ausnahme heißt für den Dienst einfach "keine Anzeige"."""
        return None


class NichtVerfuegbar(Stufen):
    """Standard im Betrieb von Schritt 0.3: Keine Stufe ist gefüllt, jede meldet es ehrlich."""

    def _nein(self, *_args: object, **_kw: object) -> Ergebnis:
        return Ergebnis.fehler("stufe_nicht_verfuegbar")

    arbeiten = uebernehmen = pruefen = reviewen = refaktorieren = _nein
    version_waehlen = veroeffentlichen = bereitstellen = umschalten = bewaehren = _nein


def _pruefen_standard(_ctx: Kontext, runde: int) -> Ergebnis:
    daten: dict = {"pruefstand": {name: "ok" for name in PRUEFSTAND_PFLICHT[runde]}}
    if runde == 3:
        daten.update(klasse="normal", treffer_anzahl=0)
    return Ergebnis.ok(**daten)


STANDARD = {
    "arbeiten": Ergebnis.ok(),
    "pruefen": _pruefen_standard,
    "uebernehmen": Ergebnis.ok(basis_commit="a" * 40, c1_commit="b" * 40),
    "reviewen": Ergebnis.ok(),
    "refaktorieren": Ergebnis.ok(r_commit="c" * 40),
    "version_waehlen": Ergebnis.ok(bump="patch", version_vorschlag="0.1.1", version_final="0.1.1", v_commit="d" * 40,
                                   tree_id="e" * 40),
    "veroeffentlichen": Ergebnis.ok(),
    "bereitstellen": Ergebnis.ok(),
    "umschalten": Ergebnis.ok(),
    "bewaehren": Ergebnis.ok(),
}


class Attrappe(Stufen):
    """Stufen für Tests und Rauchtest. skript[name] ist ein Ergebnis, eine Ausnahme (wird geworfen), eine Funktion
    (ctx, *args) oder eine Liste davon (der Reihe nach, der letzte Eintrag wiederholt sich). aufrufe zählt mit."""

    def __init__(self, skript: dict | None = None):
        self.skript = dict(skript or {})
        self.aufrufe: list[tuple] = []
        self._zaehler: dict[str, int] = {}
        self._schloss = threading.Lock()
        self.anzeige_aufrufe = 0   # zählt nur die Anzeige (sie steht nicht in aufrufe, damit Tests der Stufenfolge unverändert bleiben)

    def setze(self, name: str, wert: object) -> None:
        with self._schloss:
            self.skript[name] = wert
            self._zaehler.pop(name, None)

    def _lauf(self, name: str, ctx: Kontext, *args: object) -> Ergebnis:
        with self._schloss:
            self.aufrufe.append((name, *args))
            wert = self.skript.get(name, STANDARD.get(name))
            if isinstance(wert, list):
                i = self._zaehler.get(name, 0)
                self._zaehler[name] = i + 1
                wert = wert[min(i, len(wert) - 1)]
        if isinstance(wert, BaseException):
            raise wert
        if callable(wert):
            return wert(ctx, *args)
        return wert

    def arbeiten(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("arbeiten", ctx)

    def uebernehmen(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("uebernehmen", ctx)

    def pruefen(self, ctx: Kontext, runde: int) -> Ergebnis:
        return self._lauf("pruefen", ctx, runde)

    def reviewen(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("reviewen", ctx)

    def refaktorieren(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("refaktorieren", ctx)

    def version_waehlen(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("version_waehlen", ctx)

    def veroeffentlichen(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("veroeffentlichen", ctx)

    def bereitstellen(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("bereitstellen", ctx)

    def umschalten(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("umschalten", ctx)

    def bewaehren(self, ctx: Kontext) -> Ergebnis:
        return self._lauf("bewaehren", ctx)

    def anzeige(self, daten: dict) -> dict | None:
        """skript["anzeige"]: ein Block, eine Ausnahme (wird geworfen) oder eine Funktion (daten) -> Block. Ohne Eintrag: keine Anzeige."""
        with self._schloss:
            self.anzeige_aufrufe += 1
            wert = self.skript.get("anzeige")
        if isinstance(wert, BaseException):
            raise wert
        return wert(daten) if callable(wert) else wert


# Zuordnung Zustand -> Aufruf (Stufe, Runde); angefragt und wartet_freigabe haben keine eigene Stufe
def aufruf(stufen: Stufen, status: str) -> Callable[[Kontext], Ergebnis] | None:
    tabelle: dict[str, Callable[[Kontext], Ergebnis]] = {
        "arbeit": stufen.arbeiten,
        "uebernommen": stufen.uebernehmen,
        "pruefung1": lambda c: stufen.pruefen(c, 1),
        "review": stufen.reviewen,
        "refactor": stufen.refaktorieren,
        "pruefung2": lambda c: stufen.pruefen(c, 2),
        "versionswahl": stufen.version_waehlen,
        "pruefung3": lambda c: stufen.pruefen(c, 3),
        "freigegeben": stufen.veroeffentlichen,
        "veroeffentlicht": stufen.bereitstellen,
        "bereitgestellt": stufen.umschalten,
        "umgeschaltet": stufen.bewaehren,
    }
    return tabelle.get(status)
