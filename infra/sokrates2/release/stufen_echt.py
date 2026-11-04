"""Die echten Stufen des Release-Dienstes für die Quelle main (Konzept D.3, D.5 und F, Schritt 3b).

EchteStufen baut auf QuellenStufen (Prüfstrecke in der Sandbox) auf und füllt den Rest des Weges einer menschlichen Änderung:

  uebernehmen        Kopf der lokalen Quelle holen, Basis ist der Commit des laufenden Kerns (aus dem Status des Ankers). C1 ist der Kopf. Die Basis muss ein Vorfahre sein.
  pruefen(1, 2)      Prüfstrecke wie in QuellenStufen; dazu die Zeile schutzschicht aus dem Befehl klassifiziere des Ankers (gesperrt ist ein Fehler).
  version_waehlen    V ist C1 (ohne Release-Commit, solange es keinen Schreibweg zur Quelle gibt). Die Version ist die der Paketdateien in V; sie müssen
                     übereinstimmen. Vorschlag und Wahl sind gleich, der Admin sieht sie in der Karte und gibt genau diesen Stand frei.
  pruefen(3)         Prüfstrecke auf V; dazu version_bytegleich (alle Paketdateien nennen version_final) und schutzschicht; Klasse und Treffer kommen vom Anker.
  veroeffentlichen   Die Quelle ist die lokale Quelle, main ist schon dort. Die Stufe prüft nur, dass V (und sein Tree) noch in main liegt.
  bereitstellen      Befehl bereitstellen des Ankers (migrieren, Kern bauen und auf dem freien Slot starten). Der Anker setzt den Status; der Dienst beobachtet ihn.
  umschalten         Befehl umschalten des Ankers.
  bewaehren          Befehl bewaehren des Ankers, eine Probe je Aufruf. Der Anker entscheidet: nach 30 gesunden Minuten bewährt, nach drei schlechten Proben Rückweg.

Nichts hier führt Code aus dem Repository aus, öffnet ein Netz oder spricht mit der Datenbank. Der Anker liest die Anfrage und die Freigabe selbst; diese Stufen geben ihm nur
die Kennung. Fehlerkennungen sind kurze Codes, nie Texte aus Git, dem Anker oder Dateien.

Offen und bewusst nicht Teil dieser Stufen: Release-Commit mit Version und CHANGELOG, Tag und Release auf GitHub (brauchen die zweite GitHub-App), Versionsvorschlag aus den
Commit-Titeln und die Wahl einer höheren Version durch den Admin (kommen mit der Karte, Schritt 1.5b), die Quelle selbst (Agent, Review, Refactor: Stufe 3).
"""

from __future__ import annotations

import json
import re
import threading
import time

from release_anker import AnkerClient, AnkerFehler
from release_automat import KLASSEN, VERSION_RE, version_tupel
from release_stufen import Ergebnis, Kontext
from stufen_quelle import SHA_RE, QuellenStufen

# Paketdateien, die dieselbe Version nennen müssen (Konzept F.5). Pflicht sind die beiden, ohne die kein Release entsteht.
VERSIONSDATEIEN = ("ui/package.json", "desktop/package.json", "voice-core/pyproject.toml", "host/pyproject.toml", "geraete/pyproject.toml")
VERSION_PFLICHT = ("ui/package.json", "voice-core/pyproject.toml")
PROJECT_RE = re.compile(r"(?ms)^\[project\][ \t]*\r?$(.*?)(?=^\[|\Z)")
PYPROJECT_VERSION_RE = re.compile(r'(?m)^[ \t]*version[ \t]*=[ \t]*"([^"\r\n]{1,40})"[ \t]*(?:#.*)?\r?$')
TREFFER_SHA_RE = re.compile(r"[0-9a-f]{64}")
ANZEIGE_PAUSE_S = 30.0   # so lange gilt ein Misserfolg der Anzeige, bevor erneut gerechnet wird
ANZEIGE_ERFOLG_S = 120.0  # so lange gilt ein Erfolg: danach wird neu gerechnet, damit nach einem Wechsel des laufenden Kerns (Basis des Diffs) kein alter Diff stehen bleibt
BEREITS_RE = re.compile(r"status_falsch (\w+) statt")   # Detail des Ankers, wenn ein früherer Lauf schon weiter war


def _code(praefix: str, text: str) -> str:
    return (praefix + re.sub(r"[^a-z0-9_]", "_", str(text).lower()))[:40]


def _version_aus(pfad: str, roh: bytes) -> str | None:
    """Die Versionsnummer aus package.json oder pyproject.toml (Abschnitt [project]), oder None, wenn sie fehlt oder keine gültige Nummer ist."""
    try:
        text = roh.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if pfad.endswith(".json"):
        try:
            wert = json.loads(text).get("version")
        except (ValueError, AttributeError, RecursionError):
            return None
    else:
        abschnitt = PROJECT_RE.search(text)
        m = PYPROJECT_VERSION_RE.search(abschnitt.group(1)) if abschnitt else None
        wert = m.group(1) if m else None
    return wert if isinstance(wert, str) and VERSION_RE.fullmatch(wert) else None


def bump_art(alt: str | None, neu: str) -> str:
    """Welche Stelle sich gegenüber dem laufenden Stand geändert hat (patch, wenn nichts zu vergleichen ist oder die Nummer gleich blieb)."""
    if not alt or not VERSION_RE.fullmatch(alt):
        return "patch"
    a, n = version_tupel(alt), version_tupel(neu)
    return "major" if n[0] != a[0] else "minor" if n[1] != a[1] else "patch"


class EchteStufen(QuellenStufen):
    def __init__(self, quelle, anker: AnkerClient, pruefen_mit=None, sockpfad: str | None = None, anzeige_ordner: str | None = None):
        super().__init__(quelle, pruefen_mit, sockpfad, anzeige_ordner)
        self.anker = anker
        self._anzeige_schloss = threading.Lock()
        self._anzeige_cache: dict[tuple[str, str], tuple[dict | None, float]] = {}   # (basis_commit, v_commit) -> (Block oder None, wann erneut versuchen)

    # ------------------------------------------------------------ Hilfen
    def _kern_aktiv(self) -> tuple[str | None, str | None]:
        """(Commit, Version ohne v) des laufenden Kerns laut Anker, sonst (None, None)."""
        try:
            a = self.anker.status()
        except AnkerFehler:
            return None, None
        if not a.ok:
            return None, None
        try:
            aktiv = a.daten["komponenten"]["kern"]["aktiv"]
            commit, version = aktiv["commit"], str(aktiv["version"]).lstrip("v")
        except (KeyError, TypeError):
            return None, None
        return (commit if isinstance(commit, str) and SHA_RE.fullmatch(commit) else None,
                version if VERSION_RE.fullmatch(version) else None)

    def _klasse(self, ctx: Kontext) -> dict | Ergebnis:
        """Die Einordnung des Ankers oder ein Fehlerergebnis. Alles, was nicht genau die erwartete Form hat, ist ein Fehler."""
        try:
            a = self.anker.vorwaerts("klassifiziere", ctx.anfrage)
        except AnkerFehler as e:
            return Ergebnis.fehler(_code("anker_", e.grund))
        if not a.ok:
            return Ergebnis.fehler(_code("anker_", a.grund or "abgelehnt"))
        k = a.daten.get("ergebnis")
        if not (isinstance(k, dict) and k.get("klasse") in KLASSEN and k.get("art") in ("normal", "sonder")
                and isinstance(k.get("treffer_anzahl"), int) and not isinstance(k.get("treffer_anzahl"), bool) and 0 <= k["treffer_anzahl"] <= 200
                and isinstance(k.get("treffer_sha"), str) and TREFFER_SHA_RE.fullmatch(k["treffer_sha"])):
            return Ergebnis.fehler("anker_antwort_form")
        return k

    def _versionen(self, commit: str) -> dict[str, str]:
        """Die Versionen aller vorhandenen Paketdateien in einem Commit. Fehlt eine Pflichtdatei oder ist eine Nummer ungültig, wirft es QuelleFehler."""
        from quelle import QuelleFehler
        aus: dict[str, str] = {}
        for pfad in VERSIONSDATEIEN:
            roh = self.quelle.spiegel.lese_datei(commit, pfad)
            if roh is None:
                if pfad in VERSION_PFLICHT:
                    raise QuelleFehler("versionsdatei_fehlt")
                continue
            v = _version_aus(pfad, roh)
            if v is None:
                raise QuelleFehler("version_ungueltig")
            aus[pfad] = v
        return aus

    # ------------------------------------------------------------ Anzeige für die Karte (kein Tor, nicht in der Hashkette)
    def anzeige(self, daten: dict) -> dict | None:
        """Diff, Wirkungsflächen und Versionsempfehlung für den Befehl status. Gerechnet wird einmal je (basis_commit, v_commit); ein Misserfolg wird 30 Sekunden gemerkt, damit
        ein Abrufen im Takt nicht jedes Mal Git und den Anker belastet. Die Basis des Diffs ist der laufende Kern. Die Empfehlung ist nur eine Warnung: version_vorschlag, bump und
        version_final bleiben unberührt. Bei jedem Misserfolg None (nie ein Text aus Git)."""
        basis_daten, v = daten.get("basis_commit"), daten.get("v_commit")
        if not (isinstance(basis_daten, str) and isinstance(v, str) and SHA_RE.fullmatch(basis_daten) and SHA_RE.fullmatch(v)):
            return None
        schluessel = (basis_daten, v)
        if not self._anzeige_schloss.acquire(blocking=False):
            return None   # eine andere Abfrage rechnet gerade: status wartet nie darauf
        try:
            gemerkt = self._anzeige_cache.get(schluessel)
            if gemerkt is not None and time.monotonic() < gemerkt[1]:
                return json.loads(json.dumps(gemerkt[0])) if gemerkt[0] is not None else None
            try:
                block = self._anzeige_rechnen(basis_daten, v)
            except Exception:  # noqa: BLE001  die Anzeige ist nie Pflicht, ein Fehler heißt: keine Anzeige
                block = None
            self._anzeige_cache[schluessel] = (block, time.monotonic() + (ANZEIGE_ERFOLG_S if block is not None else ANZEIGE_PAUSE_S))
            while len(self._anzeige_cache) > 8:
                self._anzeige_cache.pop(next(iter(self._anzeige_cache)))
            return json.loads(json.dumps(block)) if block is not None else None
        finally:
            self._anzeige_schloss.release()

    def _anzeige_rechnen(self, basis_daten: str, v: str) -> dict | None:
        import release_anzeige as AZ
        from quelle import QuelleFehler
        basis, version = self._kern_aktiv()
        if basis is None:
            return None
        sp = self.quelle.spiegel
        try:
            eintraege, abgeschnitten = sp.diff_uebersicht(basis, v)
            zaehler = sp.titel_stufen(basis, v)
        except QuelleFehler:
            return None
        diff = AZ.diff_block(eintraege, abgeschnitten)
        empf = AZ.empfehlung(zaehler, AZ.ausserhalb_von_docs(eintraege, abgeschnitten), version)
        wirkung = AZ.wirkung_block(self.funde_fuer(basis_daten, v))   # bei der Quelle main ist V gleich C1: die Funde von Runde 1 gelten
        return AZ.pruefung_block(diff, wirkung, empf)

    # ------------------------------------------------------------ Stufen
    def uebernehmen(self, ctx: Kontext) -> Ergebnis:
        from quelle import QuelleFehler
        if ctx.quelle != "main":
            return Ergebnis.fehler("stufe_nicht_verfuegbar")
        if ctx.abgebrochen():
            return Ergebnis.fehler("abgebrochen")
        basis, _ = self._kern_aktiv()
        if basis is None:
            return Ergebnis.fehler("basis_unbekannt")
        try:
            kopf = self.quelle.aktualisieren()
            if kopf == basis:
                return Ergebnis.fehler("nichts_zu_tun")
            if not self.quelle.spiegel.ist_vorfahre(basis, kopf):
                return Ergebnis.fehler("basis_nicht_vorfahre")
        except QuelleFehler as e:
            return Ergebnis.fehler(_code("quelle_", e.grund))
        return Ergebnis.ok(basis_commit=basis, c1_commit=kopf)

    def pruefen(self, ctx: Kontext, runde: int) -> Ergebnis:
        from quelle import QuelleFehler
        if runde not in (1, 2, 3):
            return Ergebnis.fehler("stufe_nicht_verfuegbar")
        erg = super().pruefen(ctx, runde)
        if erg.art != "ok":
            return erg
        stand = dict(erg.daten["pruefstand"])
        if runde == 3:
            try:
                versionen = self._versionen(ctx.daten.get("v_commit", ""))
            except QuelleFehler:
                versionen = {}
            soll = ctx.daten.get("version_final")
            stand["version_bytegleich"] = "ok" if versionen and all(v == soll for v in versionen.values()) else "fehler"
        k = self._klasse(ctx)
        if isinstance(k, Ergebnis):
            return k
        stand["schutzschicht"] = "fehler" if k["klasse"] == "gesperrt" else "ok"
        if runde == 3:
            return Ergebnis.ok(pruefstand=stand, klasse=k["klasse"], treffer_anzahl=k["treffer_anzahl"],
                               **({"treffer_sha": k["treffer_sha"]} if k["treffer_anzahl"] else {}))
        return Ergebnis.ok(pruefstand=stand)

    def version_waehlen(self, ctx: Kontext) -> Ergebnis:
        from quelle import QuelleFehler
        if ctx.quelle != "main":
            return Ergebnis.fehler("stufe_nicht_verfuegbar")
        v = self._neu(ctx.daten, 2)
        if not (isinstance(v, str) and SHA_RE.fullmatch(v)):
            return Ergebnis.fehler("commit_fehlt")
        try:
            versionen = self._versionen(v)
            tree = self.quelle.spiegel.baum(v)
        except QuelleFehler as e:
            return Ergebnis.fehler(_code("quelle_", e.grund))
        if len(set(versionen.values())) != 1:
            return Ergebnis.fehler("versionen_uneinheitlich")
        version = next(iter(versionen.values()))
        _, laufend = self._kern_aktiv()
        return Ergebnis.ok(bump=bump_art(laufend, version), version_vorschlag=version, version_final=version, v_commit=v, tree_id=tree)

    def veroeffentlichen(self, ctx: Kontext) -> Ergebnis:
        from quelle import QuelleFehler
        v, tree = ctx.daten.get("v_commit"), ctx.daten.get("tree_id")
        if not (isinstance(v, str) and isinstance(tree, str)):
            return Ergebnis.fehler("daten_fehlen")
        if ctx.abgebrochen():
            return Ergebnis.fehler("abgebrochen")
        try:
            kopf = self.quelle.aktualisieren()
            if self.quelle.spiegel.baum(v) != tree:
                return Ergebnis.fehler("tree_abweichung")
            if not self.quelle.spiegel.ist_vorfahre(v, kopf):
                return Ergebnis.fehler("nicht_in_main")
        except QuelleFehler as e:
            return Ergebnis.fehler(_code("quelle_", e.grund))
        return Ergebnis.ok()

    def _anker_schritt(self, ctx: Kontext, befehl: str, schon_weiter: tuple[str, ...]) -> Ergebnis:
        """Ruft einen Vorwärtsbefehl. War ein früherer Lauf schon weiter (der Dienst stürzte nach dem Anker ab), ist das in Ordnung: Der Anker setzt den Status, der Dienst beobachtet ihn."""
        if ctx.abgebrochen():
            return Ergebnis.fehler("abgebrochen")
        try:
            a = self.anker.vorwaerts(befehl, ctx.anfrage)
        except AnkerFehler as e:
            return Ergebnis.fehler(_code("anker_", e.grund))
        if a.ok:
            return Ergebnis.ok()
        detail = str(a.daten.get("detail") or "")
        m = BEREITS_RE.search(detail)
        if a.grund == "vorwaerts_fehlgeschlagen" and m and m.group(1) in schon_weiter:
            return Ergebnis.ok()
        return Ergebnis.fehler(_code("anker_", detail.split(" ")[0] or a.grund or "abgelehnt"))

    def bereitstellen(self, ctx: Kontext) -> Ergebnis:
        return self._anker_schritt(ctx, "bereitstellen", ("bereitgestellt", "umgeschaltet"))

    def umschalten(self, ctx: Kontext) -> Ergebnis:
        return self._anker_schritt(ctx, "umschalten", ("umgeschaltet",))

    def bewaehren(self, ctx: Kontext) -> Ergebnis:
        """Eine Probe. Läuft die Bewährung noch oder ist der Anker gerade beschäftigt oder nicht erreichbar, heißt das warten (die Frist des Zustands beendet es sonst über den Rückweg)."""
        if ctx.abgebrochen():
            return Ergebnis.fehler("abgebrochen")
        try:
            a = self.anker.vorwaerts("bewaehren", ctx.anfrage)
        except AnkerFehler:
            return Ergebnis.wartet()
        if not a.ok:
            if a.grund in ("belegt", "nicht_verfuegbar"):
                return Ergebnis.wartet()
            return Ergebnis.fehler(_code("anker_", str(a.daten.get("detail") or "").split(" ")[0] or a.grund or "abgelehnt"))
        stand = (a.daten.get("ergebnis") or {}).get("stand")
        if stand == "bewaehrt":
            return Ergebnis.ok()
        if stand == "laeuft":
            return Ergebnis.wartet()
        return Ergebnis.fehler("bewaehrung_gescheitert")   # zurueckgenommen oder eine unbekannte Antwort
