"""Echte Datenbank des Release-Dienstes (Rolle wamo_release, Migration 0013, Konzept E.1, Schritt 1).

Setzt die Schnittstelle release_db.Datenbank um. Es gibt keine Spaltenliste aus Eingaben: Geschrieben werden nur die Spalten aus
release_automat.DB_SPALTEN und status, und nur mit den Rechten, die die Rolle in der Migration hat (Art, Treffer, Freigaben und die
Zustände ab freigegeben außer veroeffentlicht gehören dem Anker; die Datenbank lehnt das ohnehin ab). Der Dienst sagt nie, was die
Datenbank gesagt hat: Ablehnungen kommen als DatenbankAbgelehnt mit SQLSTATE, Ausfälle als DatenbankFehler.

Die Verbindung kommt aus wamo_pg (Zugangsdatei, verify-full). Das Schema ist ein fester Name (wamo_dev, wamo_test, wamo_prod).
"""

from __future__ import annotations

import json
import re

from release_automat import DB_SPALTEN, ENDZUSTAENDE, UUID_RE
from release_db import DB_DATEN_MAX, EREIGNIS_RE, Datenbank, DatenbankAbgelehnt, DatenbankFehler
from wamo_pg import Pg, PgFehler

SCHEMA_RE = re.compile(r"^wamo_(dev|test|prod)$")
JSON_SPALTEN = {"pruefstand"}


def _wandeln(e: PgFehler):
    """Vorübergehende Pannen und ein fehlender Server sind DatenbankFehler, Regeln der Datenbank sind DatenbankAbgelehnt."""
    if e.vorlaeufig:
        return DatenbankFehler("Datenbank nicht erreichbar oder überlastet")
    return DatenbankAbgelehnt(e.sqlstate, e.meldung)


class ReleaseDatenbank(Datenbank):
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

    def anzahl_admins(self) -> int:
        z = self._fuehre(lambda: self.pg.eine(f"select {self.s}.release_anzahl_admins()"))
        return int(z[0]) if z else 0

    def anfrage_anlegen(self, profil: str, quelle: str) -> str:
        z = self._fuehre(lambda: self.pg.eine(
            f"insert into {self.s}.release_anfragen (angefordert_von, quelle) values (%s::uuid, %s) returning id::text",
            (profil, quelle)))
        return z[0]

    def status_setzen(self, anfrage: str, status: str, daten: dict) -> None:
        teile, werte = ["status = %s"], [status]
        for k in DB_SPALTEN:  # nur Namen aus der Positivliste, nie aus der Eingabe
            if k not in daten:
                continue
            if k in JSON_SPALTEN:
                teile.append(f"{k} = %s::jsonb")
                werte.append(json.dumps(daten[k], separators=(",", ":"), ensure_ascii=True))
            else:
                teile.append(f"{k} = %s")
                werte.append(daten[k])
        werte.append(anfrage)
        sql = f"update {self.s}.release_anfragen set {', '.join(teile)} where id = %s::uuid returning id"
        if self._fuehre(lambda: self.pg.eine(sql, tuple(werte))) is None:
            raise DatenbankAbgelehnt("23503", "Anfrage nicht gefunden")

    def status_lesen(self, anfrage: str) -> dict | None:
        z = self._fuehre(lambda: self.pg.eine(
            f"select status, art, v_commit, tree_id from {self.s}.release_anfragen where id = %s::uuid", (anfrage,)))
        return None if z is None else {"status": z[0], "art": z[1], "v_commit": z[2], "tree_id": z[3]}

    def offene_anfragen(self) -> list[dict]:
        zeilen = self._fuehre(lambda: self.pg.zeilen(
            f"select id::text, status from {self.s}.release_anfragen where status <> all(%s) order by erstellt_at",
            (sorted(ENDZUSTAENDE),)))
        return [{"id": z[0], "status": z[1]} for z in zeilen]

    def ereignis_anhaengen(self, anfrage: str | None, ereignis: str, daten: dict, akteur: str | None = None) -> None:
        text = json.dumps(daten, separators=(",", ":"), ensure_ascii=True)
        if not EREIGNIS_RE.fullmatch(ereignis) or len(text) > DB_DATEN_MAX:
            raise DatenbankAbgelehnt("23514", "Ereignis ungültig")
        if anfrage is not None and not UUID_RE.fullmatch(anfrage):
            raise DatenbankAbgelehnt("22P02", "Kennung ungültig")
        self._fuehre(lambda: self.pg.zeilen(
            f"insert into {self.s}.release_ereignisse (anfrage_id, akteur, ereignis, daten) "
            "values (%s::uuid, %s::uuid, %s, %s::jsonb)", (anfrage, akteur, ereignis, text)))
