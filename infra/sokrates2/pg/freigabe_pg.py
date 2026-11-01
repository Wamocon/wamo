"""Echte Datenbank des Freigabe-Dienstes (Rolle wamo_anker, Migration 0013, Konzept E.2, Schritt 1.4b).

Setzt die Schnittstelle freigabe.FreigabeDatenbank um: Admin-Prüfung, Lesen der Anfrage (Status, Art, Commit, Tree) und Schreiben
der einen Freigabe. Die Trigger der Migration prüfen beim Schreiben alles noch einmal (Status, Fassung, Admin, Frische, Sonder,
Wartezeit); ihre Sätze kommen als DatenbankAbgelehnt mit SQLSTATE und Text, und freigabe.py bildet sie auf feste Codes ab.

Die Verbindung kommt aus wamo_pg (Zugangsdatei, verify-full). Das Schema ist ein fester Name (wamo_dev, wamo_test, wamo_prod).
"""

from __future__ import annotations

import re

from freigabe import COMMIT_RE, UUID_RE, DatenbankAbgelehnt, DatenbankFehler
from wamo_pg import Pg, PgFehler

SCHEMA_RE = re.compile(r"^wamo_(dev|test|prod)$")


def _wandeln(e: PgFehler):
    if e.vorlaeufig:
        return DatenbankFehler("Datenbank nicht erreichbar oder überlastet")
    return DatenbankAbgelehnt(e.sqlstate, e.meldung)


class FreigabePg:
    def __init__(self, pg: Pg, schema: str):
        if not SCHEMA_RE.fullmatch(schema):
            raise ValueError("unbekanntes Schema")
        self.pg, self.s = pg, schema

    def _fuehre(self, aufruf):
        try:
            return aufruf()
        except PgFehler as e:
            raise _wandeln(e) from None

    def ist_admin(self, profil: str) -> bool:
        if not UUID_RE.fullmatch(profil):
            return False
        z = self._fuehre(lambda: self.pg.eine(f"select {self.s}.release_ist_admin(%s::uuid)", (profil,)))
        return bool(z and z[0])

    def anfrage_lesen(self, anfrage: str) -> dict | None:
        if not UUID_RE.fullmatch(anfrage):
            return None
        z = self._fuehre(lambda: self.pg.eine(
            f"select status, art, v_commit, tree_id from {self.s}.release_anfragen where id = %s::uuid", (anfrage,)))
        return None if z is None else {"status": z[0], "art": z[1], "v_commit": z[2], "tree_id": z[3]}

    def freigabe_schreiben(self, anfrage: str, freigeber: str, commit_id: str, tree_id: str, aal2_zeit: float,
                           quell_ip: str | None) -> str:
        if not (UUID_RE.fullmatch(anfrage) and UUID_RE.fullmatch(freigeber) and COMMIT_RE.fullmatch(commit_id)
                and COMMIT_RE.fullmatch(tree_id)):
            raise DatenbankAbgelehnt("22P02", "Angaben ungültig")
        z = self._fuehre(lambda: self.pg.eine(
            f"insert into {self.s}.release_freigaben (anfrage_id, freigeber, commit_id, tree_id, aal2_zeit, quell_ip) "
            "values (%s::uuid, %s::uuid, %s, %s, to_timestamp(%s), %s::inet) returning id::text",
            (anfrage, freigeber, commit_id, tree_id, float(aal2_zeit), quell_ip)))
        return z[0]
