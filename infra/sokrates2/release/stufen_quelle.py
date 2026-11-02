"""Die erste echte Stufe des Release-Dienstes: pruefen (Konzept D.3, Schritte 1.2 und 1.3).

QuellenStufen holt die Stände aus dem Git-Spiegel (quelle.py) und schickt sie über den Annahme-Dienst (pruefer_client.py) in die Prüfer-Sandbox. Alle anderen
Stufen bleiben, wie sie sind (NichtVerfuegbar: sie melden ehrlich "stufe_nicht_verfuegbar"), bis ihre Schritte gebaut sind.

Welche Commits geprüft werden (Basis ist immer basis_commit):
    Runde 1  c1_commit
    Runde 2  r_commit, außer der Refactor wurde verworfen (refactor_verworfen) oder fehlt: dann c1_commit
    Runde 3  noch nicht verfügbar: Sie braucht die Klassifizierung des Ankers (klasse, treffer_anzahl), die es erst mit Schritt 4 gibt.

Ergebnis.daten enthält nur pruefstand (Zeilen des Läufers). Meldet der Läufer windows_pruefung_noetig (Änderungen in host/ oder desktop/, die unter Linux nicht
getestet werden können), kommt die Zeile windows_tests mit "uebersprungen" dazu: Sie ist in der Karte sichtbar und kein stilles "ok".
"""

from __future__ import annotations

import re
from typing import Callable

from release_stufen import Ergebnis, Kontext, NichtVerfuegbar

SHA_RE = re.compile(r"[0-9a-f]{40}")


class QuellenStufen(NichtVerfuegbar):
    def __init__(self, quelle, pruefen_mit: Callable | None = None, sockpfad: str | None = None):
        import pruefer_client as C
        self.quelle = quelle
        self._pruefen_mit = pruefen_mit or C.pruefen_mit
        self._sockpfad = sockpfad or C.SOCKET

    def _neu(self, daten: dict, runde: int) -> str | None:
        if runde == 1:
            return daten.get("c1_commit")
        if daten.get("refactor_verworfen") or not daten.get("r_commit"):
            return daten.get("c1_commit")
        return daten.get("r_commit")

    def pruefen(self, ctx: Kontext, runde: int) -> Ergebnis:  # pruefung1, pruefung2, pruefung3
        from pruefer_client import PrueferFehler
        from quelle import QuelleFehler
        if runde not in (1, 2):
            return Ergebnis.fehler("stufe_nicht_verfuegbar")
        if ctx.abgebrochen():
            return Ergebnis.fehler("abgebrochen")
        basis, neu = ctx.daten.get("basis_commit"), self._neu(ctx.daten, runde)
        if not (isinstance(basis, str) and isinstance(neu, str) and SHA_RE.fullmatch(basis) and SHA_RE.fullmatch(neu)):
            return Ergebnis.fehler("commit_fehlt")
        try:
            erg = self._pruefen_mit(lambda ausgang: self.quelle.spiegel.schreibe_archiv(ausgang, basis, neu), runde, self._sockpfad)
        except PrueferFehler as e:
            return Ergebnis.fehler(("pruefstrecke_" + re.sub(r"[^a-z0-9_]", "_", e.grund.lower()))[:40])
        except QuelleFehler as e:
            return Ergebnis.fehler(("quelle_" + re.sub(r"[^a-z0-9_]", "_", e.grund.lower()))[:40])
        pruefstand = dict(erg["pruefstand"])
        if erg.get("windows_pruefung_noetig"):
            pruefstand["windows_tests"] = "uebersprungen"
        return Ergebnis.ok(pruefstand=pruefstand)
