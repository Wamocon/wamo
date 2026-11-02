"""Vorwärtsbefehle des Ankers: migrieren, bereitstellen, umschalten (Konzept B, C und D.1, Schritt 1.4d).

Der Anker führt nie aus, was eine Anfrage behauptet. Er liest die Anfrage selbst aus der Datenbank, prüft dort noch einmal, dass die Freigabe zu genau
diesem Commit und diesem Tree gehört und nicht widerrufen ist, holt den Stand aus der Quelle (Git-Objekte sind inhaltsadressiert: Commit und Tree
müssen zur Freigabe passen) und legt nur Dateien in einen root-eigenen Arbeitsordner. Alles andere tun feste, per Prüfsumme gesicherte Schritte.

  migrieren <id>      Status veroeffentlicht. Wendet die noch nicht angewendeten Migrationen des freigegebenen Stands an, zuerst im Schema wamo_test, nur bei
                      Erfolg im nächsten (SCHEMAS). Eine schon angewendete Migration mit anderer Prüfsumme bricht ab (unveränderlich nach dem Anwenden). Jede
                      angewendete Datei bekommt ihre Prüfsumme in schema_migrations.sha256 (setzt nur der Anker, ein Trigger sperrt jede Änderung).
  bereitstellen <id>  Status veroeffentlicht. Migrieren, dann das Release des Kerns bauen und auf dem freien Slot starten (noch ohne Umschalten),
                      danach Status bereitgestellt.
  klassifiziere <id>  Status pruefung1 bis pruefung3. Schutzschicht: Art und Treffer des Diffs zwischen Basis und jüngstem Stand (schutz.py), vom Anker in die Datenbank geschrieben.
  umschalten <id>     Status bereitgestellt. Der Kern schaltet auf den bereitgestellten Slot (erst nach der Gesundheitsprobe, mit Nachlauf), danach Status
                      umgeschaltet. Die Bewährung und der Eintrag "letzte gute" folgen nach der Bewährung, nicht hier.

Die Oberfläche (blau/grün) ist hier noch nicht angeschlossen: ihr Bau läuft aus dem Home von wamocon und braucht einen eigenen root-eigenen Schritt.

Mit trocken=True prüft jeder Befehl alles (Datenbank, Quelle, Auszug) und meldet den Plan, ändert aber weder Datenbank noch Kern noch Status.
Die Mitspieler (Datenbank, Quelle, Werk) werden eingesetzt; vorwaerts_echt.py baut die echten, die Tests nehmen Attrappen oder einen Wegwerf-Postgres.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil

import schutz

SCHEMAS = ("wamo_test", "wamo_dev")
UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
VERSION_RE = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
MIGRATION_RE = re.compile(r"(\d{4}_[a-z0-9_]+)\.sql")   # die Rückwege (.down.sql) laufen nie vorwärts
SCHRITTE_ORDNER = ("voice-core", "supabase/migrations")
STATUS = {"migrieren": "veroeffentlicht", "bereitstellen": "veroeffentlicht", "umschalten": "bereitgestellt"}
KLASSIFIZIEREN_STATUS = ("pruefung1", "pruefung2", "pruefung3")


class VorwaertsFehler(Exception):
    """Kurzer Code, nie ein Inhalt aus der Datenbank, dem Repository oder einer Migration."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def release_name(version_final: str, commit: str) -> str:
    """Name des Kern-Releases: v<Version>-<erste 7 Zeichen des Commits> (dieselbe Regel wie rueckweg-kern)."""
    return f"v{version_final}-{commit[:7]}"


class Vorwaerts:
    def __init__(self, db, quelle, werk, arbeit: str, schemas: tuple[str, ...] = SCHEMAS):
        self.db, self.quelle, self.werk, self.arbeit, self.schemas = db, quelle, werk, arbeit, schemas

    # ---------------------------------------------------------------- Prüfungen
    def _anfrage(self, befehl: str, kennung: str) -> dict:
        if not UUID_RE.fullmatch(kennung or ""):
            raise VorwaertsFehler("anfrage_ungueltig")
        a = self.db.anfrage(kennung)
        if a is None:
            raise VorwaertsFehler("anfrage_unbekannt")
        if a.get("status") != STATUS[befehl]:
            raise VorwaertsFehler("status_falsch", f"{a.get('status')} statt {STATUS[befehl]}")
        commit, baum, version = a.get("v_commit"), a.get("tree_id"), a.get("version_final")
        if not (isinstance(commit, str) and COMMIT_RE.fullmatch(commit) and isinstance(baum, str) and COMMIT_RE.fullmatch(baum)
                and isinstance(version, str) and VERSION_RE.fullmatch(version)):
            raise VorwaertsFehler("anfrage_unvollstaendig")
        if not self.db.freigabe_gilt(kennung, commit, baum):
            raise VorwaertsFehler("freigabe_ungueltig")   # widerrufen, oder sie gehört zu einem anderen Stand
        if self.quelle.baum_id(commit) != baum:
            raise VorwaertsFehler("quelle_unstimmig")   # die Quelle liefert unter dieser Commit-ID nicht den freigegebenen Tree
        return {"commit": commit, "baum": baum, "version": version, "name": release_name(version, commit)}

    def _auszug(self, kennung: str, commit: str) -> str:
        ordner = os.path.join(self.arbeit, kennung)
        shutil.rmtree(ordner, ignore_errors=True)
        os.makedirs(ordner, mode=0o700)
        try:
            self.quelle.extrahiere(commit, ordner, SCHRITTE_ORDNER)
        except VorwaertsFehler:
            shutil.rmtree(ordner, ignore_errors=True)
            raise
        return ordner

    def _aufraeumen(self, kennung: str) -> None:
        shutil.rmtree(os.path.join(self.arbeit, kennung), ignore_errors=True)

    # ---------------------------------------------------------------- migrieren
    @staticmethod
    def _migrationen(ordner: str) -> list[tuple[str, bytes]]:
        verz = os.path.join(ordner, "supabase", "migrations")
        if not os.path.isdir(verz):
            return []
        aus = []
        for name in sorted(os.listdir(verz)):
            m = MIGRATION_RE.fullmatch(name)
            if m:
                with open(os.path.join(verz, name), "rb") as f:
                    aus.append((m.group(1), f.read()))
        return aus

    def _plan(self, migrationen: list[tuple[str, bytes]]) -> dict[str, list[str]]:
        """Je Schema die Versionen, die noch fehlen. Bricht ab, wenn eine angewendete Datei sich geändert hat."""
        plan: dict[str, list[str]] = {}
        for schema in self.schemas:
            stand = self.werk.migrationsstand(schema)
            offen = []
            for version, inhalt in migrationen:
                if version in stand:
                    if stand[version] and stand[version] != hashlib.sha256(inhalt).hexdigest():
                        raise VorwaertsFehler("migration_veraendert", version)
                else:
                    offen.append(version)
            plan[schema] = offen
        return plan

    def _anwenden(self, migrationen: list[tuple[str, bytes]], plan: dict[str, list[str]]) -> dict[str, list[str]]:
        fertig: dict[str, list[str]] = {}
        nach_version = dict(migrationen)
        for schema in self.schemas:   # wamo_test zuerst; scheitert es, bleibt das nächste Schema unberührt
            fertig[schema] = []
            for version in plan[schema]:
                inhalt = nach_version[version]
                sha = hashlib.sha256(inhalt).hexdigest()
                text = inhalt.decode("utf-8").replace("__SCHEMA__", schema)
                text += f"\nupdate {schema}.schema_migrations set sha256 = '{sha}' where version = '{version}' and sha256 is null;\n"
                self.werk.anwenden(schema, version, text)
                stand = self.werk.migrationsstand(schema)
                if stand.get(version) != sha:
                    raise VorwaertsFehler("migration_nicht_gebucht", version)   # die Datei buchte ihre Version nicht oder die Prüfsumme fehlt
                fertig[schema].append(version)
        return fertig

    def migrieren(self, kennung: str, trocken: bool = False) -> dict:
        a = self._anfrage("migrieren", kennung)
        ordner = self._auszug(kennung, a["commit"])
        try:
            migrationen = self._migrationen(ordner)
            plan = self._plan(migrationen)
            if trocken:
                return {"trocken": True, "plan": plan, "release": a["name"]}
            return {"angewendet": self._anwenden(migrationen, plan), "release": a["name"]}
        finally:
            self._aufraeumen(kennung)

    # ---------------------------------------------------------------- bereitstellen
    def bereitstellen(self, kennung: str, trocken: bool = False) -> dict:
        a = self._anfrage("bereitstellen", kennung)
        ordner = self._auszug(kennung, a["commit"])
        try:
            migrationen = self._migrationen(ordner)
            plan = self._plan(migrationen)
            if trocken:
                return {"trocken": True, "plan": plan, "release": a["name"]}
            fertig = self._anwenden(migrationen, plan)   # erweiternd und zuerst: beide Kern-Fassungen sehen kurz denselben Bestand
            kern = os.path.join(ordner, "voice-core")
            if not os.path.isfile(os.path.join(kern, "bot.py")):
                raise VorwaertsFehler("kern_fehlt")
            slot = self.werk.kern_bereiten(a["name"], kern)
            self.db.status_setzen(kennung, "veroeffentlicht", "bereitgestellt")
            return {"angewendet": fertig, "release": a["name"], "slot": slot}
        finally:
            self._aufraeumen(kennung)

    # ---------------------------------------------------------------- umschalten
    def umschalten(self, kennung: str, trocken: bool = False) -> dict:
        a = self._anfrage("umschalten", kennung)
        if trocken:
            return {"trocken": True, "release": a["name"]}
        slot = self.werk.kern_schalten(a["name"])
        self.db.status_setzen(kennung, "bereitgestellt", "umgeschaltet")
        return {"release": a["name"], "slot": slot, "version": a["version"], "commit": a["commit"]}

    # ---------------------------------------------------------------- klassifizieren
    def klassifizieren(self, kennung: str, trocken: bool = False) -> dict:
        """Schutzschicht (Konzept E.2): Der Anker bildet das Diff zwischen Basis und dem jüngsten Stand der Anfrage selbst aus den Git-Objekten der Quelle und
        schreibt Art und Treffer in die Datenbank (das darf nur er). Vor der Freigabe, in den Stufen pruefung1 bis pruefung3. Die Art lässt sich nur anheben:
        Eine Anfrage, die einmal sonder war, bleibt es."""
        if not UUID_RE.fullmatch(kennung or ""):
            raise VorwaertsFehler("anfrage_ungueltig")
        a = self.db.anfrage_stand(kennung)
        if a is None:
            raise VorwaertsFehler("anfrage_unbekannt")
        if a.get("status") not in KLASSIFIZIEREN_STATUS:
            raise VorwaertsFehler("status_falsch", str(a.get("status")))
        basis = a.get("basis_commit")
        neu = next((a[k] for k in ("v_commit", "r_commit", "c1_commit") if a.get(k)), None)
        if not (isinstance(basis, str) and COMMIT_RE.fullmatch(basis) and isinstance(neu, str) and COMMIT_RE.fullmatch(neu)):
            raise VorwaertsFehler("anfrage_unvollstaendig")
        erg = schutz.klassifizieren(self.quelle.aenderungen(basis, neu))
        art = "sonder" if erg["klasse"] != "normal" or a.get("art") == "sonder" else "normal"
        if not trocken:
            self.db.klassifizierung_setzen(kennung, art, erg["treffer"])
        return {"klasse": erg["klasse"], "art": art, "treffer_anzahl": erg["treffer_anzahl"], "treffer_sha": erg["treffer_sha"], "trocken": trocken or None}

    def ausfuehren(self, befehl: str, kennung: str, trocken: bool = False) -> dict:
        if befehl == "klassifiziere":
            return self.klassifizieren(kennung, trocken)
        if befehl not in STATUS:
            raise VorwaertsFehler("befehl_unbekannt")
        return getattr(self, befehl)(kennung, trocken)
