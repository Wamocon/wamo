"""Schnittstelle des Release-Dienstes zur Datenbank (Migration 0013, Konzept E.1, Schritt 0.3).

Hier steht nur die Schnittstelle und zwei Fassungen: OhneDatenbank (Betrieb von Schritt 0.3: keine Verbindung, jede Frage
scheitert ehrlich mit DatenbankFehlt, der Dienst lehnt dann das Anfordern ab) und AttrappeDatenbank (Tests und Rauchtest, mit
den Regeln der Trigger aus der Migration im Kleinen nachgebaut). Die echte Fassung (Rolle wamo_release, Verbindung über eine
root-eigene Datei, TLS) folgt in Stufe 1. Sie braucht ein Netz; die Unit erlaubt in diesem Schritt nur Unix-Sockets.

Die Rolle wamo_release darf (Migration 0013): Anfragen anlegen (angefordert_von, quelle), die Zustände bis wartet_freigabe,
veroeffentlicht und abgebrochen setzen, die Werte basis_commit, c1_commit, r_commit, v_commit, tree_id, bump,
version_vorschlag, version_final und pruefstand schreiben und Ereignisse anhängen. Art und Schutz-Treffer, Freigaben und alle
Zustände ab freigegeben außer veroeffentlicht gehören dem Anker. Der Dienst fragt deshalb die Zustände des Ankers nur ab.
"""

from __future__ import annotations

import re
import threading
import uuid
from typing import Callable

from release_automat import DB_SCHREIBBAR, ENDZUSTAENDE, OFFENE, REIHENFOLGE, UUID_RE

EREIGNIS_RE = re.compile(r"[a-z0-9_.-]{1,60}")
DB_DATEN_MAX = 4096  # release_ereignisse.daten höchstens 4 KB


class DatenbankFehler(Exception):
    """Vorübergehend: Datenbank nicht erreichbar, Zeitüberschreitung. Der Dienst versucht es später erneut."""


class DatenbankFehlt(DatenbankFehler):
    """Es ist keine Datenbank angeschlossen (Schritt 0.3)."""


class DatenbankAbgelehnt(Exception):
    """Dauerhaft: Eine Regel der Datenbank (Trigger, Rechte, Eindeutigkeit) lehnt ab. code ist der SQLSTATE."""

    def __init__(self, code: str, text: str = ""):
        super().__init__(f"{code} {text}".strip())
        self.code = code


class Datenbank:
    def ist_admin(self, profil: str) -> bool:
        raise NotImplementedError

    def anzahl_admins(self) -> int:
        raise NotImplementedError

    def anfrage_anlegen(self, profil: str, quelle: str) -> str:
        """Legt die Anfrage an (Status angefragt) und gibt die von der Datenbank vergebene Kennung zurück."""
        raise NotImplementedError

    def status_setzen(self, anfrage: str, status: str, daten: dict) -> None:
        """Setzt den Zustand und die Werte aus daten (nur Spalten aus release_automat.DB_SPALTEN)."""
        raise NotImplementedError

    def status_lesen(self, anfrage: str) -> dict | None:
        """{"status", "art", "v_commit", "tree_id"} der Anfrage oder None, wenn es sie nicht gibt."""
        raise NotImplementedError

    def offene_anfragen(self) -> list[dict]:
        """Alle Anfragen, die nicht beendet sind: [{"id", "status"}]. Es darf höchstens eine geben."""
        raise NotImplementedError

    def ereignis_anhaengen(self, anfrage: str | None, ereignis: str, daten: dict, akteur: str | None = None) -> None:
        """Hängt ein Ereignis an die Kette der Datenbank (release_ereignisse). Daten höchstens 4 KB, nie Geheimnisse."""
        raise NotImplementedError


class OhneDatenbank(Datenbank):
    def _fehlt(self, *_args: object, **_kw: object):
        raise DatenbankFehlt("keine Datenbank angeschlossen")

    ist_admin = anzahl_admins = anfrage_anlegen = status_setzen = status_lesen = offene_anfragen = _fehlt
    ereignis_anhaengen = _fehlt


class AttrappeDatenbank(Datenbank):
    """Bildet die Regeln der Migration 0013 so weit nach, wie der Dienst sie berührt. Fehlverhalten des Dienstes erscheint
    als DatenbankAbgelehnt und in verstoesse. ausfall=True lässt jede Frage mit DatenbankFehler scheitern."""

    def __init__(self, admins: set[str] | None = None, uhr: Callable[[], float] | None = None):
        self.admins = set(admins or ())
        self.anfragen: dict[str, dict] = {}
        self.ereignisse: list[dict] = []
        self.verstoesse: list[str] = []
        self.ausfall = False
        self.aufrufe: list[str] = []
        self._schloss = threading.RLock()

    def _pruefe(self, name: str) -> None:
        self.aufrufe.append(name)
        if self.ausfall:
            raise DatenbankFehler("Datenbank nicht erreichbar")

    def _verstoss(self, code: str, text: str):
        self.verstoesse.append(text)
        raise DatenbankAbgelehnt(code, text)

    def ist_admin(self, profil: str) -> bool:
        with self._schloss:
            self._pruefe("ist_admin")
            return profil in self.admins

    def anzahl_admins(self) -> int:
        with self._schloss:
            self._pruefe("anzahl_admins")
            return len(self.admins)

    def anfrage_anlegen(self, profil: str, quelle: str) -> str:
        with self._schloss:
            self._pruefe("anfrage_anlegen")
            if profil not in self.admins:
                self._verstoss("42501", "nur ein aktiver Admin darf beauftragen")
            if any(a["status"] in OFFENE for a in self.anfragen.values()):
                self._verstoss("23505", "es gibt schon eine offene Anfrage")
            kennung = str(uuid.uuid4())
            self.anfragen[kennung] = {"id": kennung, "status": "angefragt", "profil": profil, "quelle": quelle,
                                      "art": "normal", "v_commit": None, "tree_id": None, "daten": {}}
            return kennung

    def status_setzen(self, anfrage: str, status: str, daten: dict) -> None:
        with self._schloss:
            self._pruefe("status_setzen")
            a = self.anfragen.get(anfrage)
            if a is None:
                self._verstoss("23503", "Anfrage nicht gefunden")
            if status not in DB_SCHREIBBAR and status != a["status"]:
                self._verstoss("42501", f"wamo_release darf den Zustand {status} nicht setzen")
            if a["status"] in ENDZUSTAENDE:
                self._verstoss("42501", "die Anfrage ist abgeschlossen")
            if status != a["status"]:
                von, nach = REIHENFOLGE.index(a["status"]), REIHENFOLGE.index(status) if status in REIHENFOLGE else -1
                if status == "abgebrochen":
                    pass
                elif nach <= von or (von >= REIHENFOLGE.index("wartet_freigabe") and nach != von + 1):
                    self._verstoss("23514", f"Statuswechsel {a['status']} nach {status} nicht erlaubt")
            a["status"] = status
            a["daten"].update(daten)
            a["v_commit"], a["tree_id"] = a["daten"].get("v_commit"), a["daten"].get("tree_id")

    def status_lesen(self, anfrage: str) -> dict | None:
        with self._schloss:
            self._pruefe("status_lesen")
            a = self.anfragen.get(anfrage)
            return None if a is None else {k: a[k] for k in ("status", "art", "v_commit", "tree_id")}

    def offene_anfragen(self) -> list[dict]:
        with self._schloss:
            self._pruefe("offene_anfragen")
            return [{"id": a["id"], "status": a["status"]} for a in self.anfragen.values() if a["status"] in OFFENE]

    def ereignis_anhaengen(self, anfrage: str | None, ereignis: str, daten: dict, akteur: str | None = None) -> None:
        with self._schloss:
            self._pruefe("ereignis_anhaengen")
            if not EREIGNIS_RE.fullmatch(ereignis) or len(str(daten)) > DB_DATEN_MAX:
                self._verstoss("23514", "Ereignis ungültig")
            if anfrage is not None and not UUID_RE.fullmatch(anfrage):
                self._verstoss("22P02", "Kennung ungültig")
            self.ereignisse.append({"anfrage": anfrage, "ereignis": ereignis, "daten": dict(daten), "akteur": akteur})

    # --- Hilfen der Tests: so schreibt der Anker in die Datenbank
    def anker_setzt(self, anfrage: str, status: str, **felder: object) -> None:
        with self._schloss:
            a = self.anfragen[anfrage]
            a["status"] = status
            for k, v in felder.items():
                if k in ("art", "v_commit", "tree_id"):
                    a[k] = v
