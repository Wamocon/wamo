"""Die erste echte Stufe des Release-Dienstes: pruefen (Konzept D.3, Schritte 1.2 und 1.3).

QuellenStufen holt die Stände aus dem Git-Spiegel (quelle.py) und schickt sie über den Annahme-Dienst (pruefer_client.py) in die Prüfer-Sandbox. Alle anderen
Stufen bleiben, wie sie sind (NichtVerfuegbar: sie melden ehrlich "stufe_nicht_verfuegbar"), bis ihre Schritte gebaut sind.

Welche Commits geprüft werden (Basis ist immer basis_commit):
    Runde 1  c1_commit
    Runde 2  r_commit, außer der Refactor wurde verworfen (refactor_verworfen) oder fehlt: dann c1_commit
    Runde 3  v_commit (der Release-Commit V). Die Zeilen schutzschicht und version_bytegleich und die Klassifizierung ergänzt EchteStufen (stufen_echt.py).

Ergebnis.daten enthält nur pruefstand (Zeilen des Läufers). Meldet der Läufer windows_pruefung_noetig (Änderungen in host/ oder desktop/, die unter Linux nicht
getestet werden können), kommt die Zeile windows_tests mit "uebersprungen" dazu: Sie ist in der Karte sichtbar und kein stilles "ok".

Wirkungsflächen für die Karte (reine Anzeige, nicht Teil der Hashkette): Nach einem Lauf mit der Zeile wirkungsflaechen (Runde 1 und 2) merkt sich die Stufe die Funde
(treffer, sonder, neue_pakete, geaenderte_dateien, windows_pruefung_noetig) zum Paar (Basis, Stand) im Speicher und, wenn ein Ordner angegeben ist, als kleine JSON-Datei
<basis>-<stand>.json (atomar geschrieben, 0600). Beim Lesen wird streng geprüft: zu große, fremde (anderes Paar, andere Form, zusätzliche Schlüssel) und kaputte Dateien
werden verworfen. Bei der Quelle main ist V gleich C1, die Funde von Runde 1 gelten also auch für V; nur dann findet funde_fuer(basis, v) etwas (Runde 3 hat keine
Wirkungsflächen und überschreibt nichts).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from collections import OrderedDict
from typing import Callable

from release_kette import datei_atomar_schreiben, eigene_uid, lies_datei
from release_stufen import Ergebnis, Kontext, NichtVerfuegbar

SHA_RE = re.compile(r"[0-9a-f]{40}")
ANZEIGE_DATEI_MAX = 16 * 1024      # größer ist keine unserer Dateien
ANZEIGE_DATEI_RE = re.compile(r"[0-9a-f]{40}-[0-9a-f]{40}\.json")
ANZEIGE_AUFBEWAHREN = 24           # so viele Dateien bleiben im Ordner (die neuesten)
ANZEIGE_SPEICHER = 8               # so viele Paare im Speicher
FUND_SCHLUESSEL = frozenset({"treffer", "treffer_anzahl", "sonder", "neue_pakete", "geaenderte_dateien", "windows_pruefung_noetig"})
FUND_TREFFER_SCHLUESSEL = frozenset({"pfad", "zeile", "art", "detail"})


def _ganz(wert: object, grenze: int) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool) and 0 <= wert <= grenze


def funde_pruefen(obj: object) -> dict | None:
    """Streng geprüfte Form der gemerkten Funde oder None. Nur diese Schlüssel, nur diese Typen und Grenzen; das Ergebnis ist eine Kopie."""
    if not isinstance(obj, dict) or set(obj) != FUND_SCHLUESSEL:
        return None
    t = obj["treffer"]
    if not (isinstance(t, list) and len(t) <= 50 and _ganz(obj["treffer_anzahl"], 200) and _ganz(obj["neue_pakete"], 200) and _ganz(obj["geaenderte_dateien"], 10**7)
            and isinstance(obj["sonder"], bool) and isinstance(obj["windows_pruefung_noetig"], bool)):
        return None
    for e in t:
        if not (isinstance(e, dict) and set(e) == FUND_TREFFER_SCHLUESSEL and isinstance(e["pfad"], str) and len(e["pfad"]) <= 300 and _ganz(e["zeile"], 10**7)
                and isinstance(e["art"], str) and len(e["art"]) <= 40 and isinstance(e["detail"], str) and len(e["detail"]) <= 120):
            return None
    return {**obj, "treffer": [dict(e) for e in t]}


def funde_aus_lauf(erg: object) -> dict | None:
    """Zieht die Wirkungsflächen aus dem Ergebnis des Läufers. None, wenn der Lauf sie nicht geprüft hat (Runde 3) oder die Form fremd ist."""
    if not isinstance(erg, dict) or not isinstance(erg.get("pruefstand"), dict) or "wirkungsflaechen" not in erg["pruefstand"]:
        return None
    alle = erg.get("treffer")
    roh = alle.get("wirkungsflaechen") if isinstance(alle, dict) else None
    liste = []
    for t in (roh if isinstance(roh, list) else [])[:50]:
        if isinstance(t, dict):
            liste.append({"pfad": str(t.get("pfad", ""))[:300], "zeile": t["zeile"] if _ganz(t.get("zeile"), 10**7) else 0, "art": str(t.get("art", ""))[:40],
                          "detail": str(t.get("detail", ""))[:120]})
    details = erg.get("details")
    wf = details.get("wirkungsflaechen") if isinstance(details, dict) else None
    n = wf.get("treffer_anzahl") if isinstance(wf, dict) else None
    pakete, dateien = erg.get("neue_pakete"), erg.get("geaenderte_dateien")
    return funde_pruefen({"treffer": liste, "treffer_anzahl": n if _ganz(n, 200) and n >= len(liste) else len(liste), "sonder": erg.get("sonder") is True,
                          "neue_pakete": min(len(pakete), 200) if isinstance(pakete, list) else 0, "geaenderte_dateien": dateien if _ganz(dateien, 10**7) else 0,
                          "windows_pruefung_noetig": erg.get("windows_pruefung_noetig") is True})


class QuellenStufen(NichtVerfuegbar):
    def __init__(self, quelle, pruefen_mit: Callable | None = None, sockpfad: str | None = None, anzeige_ordner: str | None = None):
        import pruefer_client as C
        self.quelle = quelle
        self._pruefen_mit = pruefen_mit or C.pruefen_mit
        self._sockpfad = sockpfad or C.SOCKET
        self._anzeige_ordner = anzeige_ordner   # None: die Funde bleiben nur im Speicher
        self._funde: OrderedDict[tuple[str, str], dict] = OrderedDict()
        self._funde_schloss = threading.Lock()

    # ------------------------------------------------------------ Wirkungsflächen für die Karte (reine Anzeige)
    def _funde_pfad(self, basis: str, neu: str) -> str | None:
        if not self._anzeige_ordner:
            return None
        return os.path.join(self._anzeige_ordner, f"{basis}-{neu}.json")

    def _funde_speichern(self, basis: str, neu: str, funde: dict) -> None:
        with self._funde_schloss:
            self._funde[(basis, neu)] = funde
            self._funde.move_to_end((basis, neu))
            while len(self._funde) > ANZEIGE_SPEICHER:
                self._funde.popitem(last=False)

    def _funde_merken(self, basis: str, neu: str, erg: object) -> None:
        funde = funde_aus_lauf(erg)
        if funde is None:
            return
        self._funde_speichern(basis, neu, funde)
        pfad = self._funde_pfad(basis, neu)
        if pfad is None:
            return
        try:   # die Anzeige ist nie Pflicht: ein Fehler beim Schreiben ändert am Lauf nichts
            os.makedirs(self._anzeige_ordner, mode=0o700, exist_ok=True)
            daten = json.dumps({"v": 1, "basis": basis, "neu": neu, **funde}, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            datei_atomar_schreiben(pfad, daten, sichern=False)
            self._funde_aufraeumen()
        except OSError:
            pass

    def _funde_aufraeumen(self) -> None:
        eintraege = []
        for name in os.listdir(self._anzeige_ordner):
            if ANZEIGE_DATEI_RE.fullmatch(name):
                with contextlib.suppress(OSError):
                    eintraege.append((os.stat(os.path.join(self._anzeige_ordner, name)).st_mtime_ns, name))
        for _, name in sorted(eintraege)[:-ANZEIGE_AUFBEWAHREN]:
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(self._anzeige_ordner, name))

    def funde_fuer(self, basis: object, neu: object) -> dict | None:
        """Die gemerkten Funde der Wirkungsflächen für genau dieses Paar (Speicher, sonst Datei) oder None. Eine Kopie: der Aufrufer ändert nichts am Gemerkten."""
        if not (isinstance(basis, str) and isinstance(neu, str) and SHA_RE.fullmatch(basis) and SHA_RE.fullmatch(neu)):
            return None
        with self._funde_schloss:
            f = self._funde.get((basis, neu))
        if f is None:
            f = self._funde_laden(basis, neu)
            if f is not None:
                self._funde_speichern(basis, neu, f)
        return funde_pruefen(f) if f is not None else None

    def _funde_laden(self, basis: str, neu: str) -> dict | None:
        pfad = self._funde_pfad(basis, neu)
        if pfad is None:
            return None
        try:
            roh = lies_datei(pfad, ANZEIGE_DATEI_MAX, eigene_uid(), True)   # Besitzer, Rechte, Art und Größe; alles andere wirft
            d = json.loads(roh.decode("ascii"))
        except (OSError, ValueError, RecursionError):   # auch UnicodeDecodeError und JSONDecodeError sind ValueError
            return None
        if not (isinstance(d, dict) and set(d) == FUND_SCHLUESSEL | {"v", "basis", "neu"} and d["v"] == 1 and d["basis"] == basis and d["neu"] == neu):
            return None
        return funde_pruefen({k: d[k] for k in FUND_SCHLUESSEL})

    def _neu(self, daten: dict, runde: int) -> str | None:
        if runde == 1:
            return daten.get("c1_commit")
        if runde == 3:
            return daten.get("v_commit")
        if daten.get("refactor_verworfen") or not daten.get("r_commit"):
            return daten.get("c1_commit")
        return daten.get("r_commit")

    def pruefen(self, ctx: Kontext, runde: int) -> Ergebnis:  # pruefung1, pruefung2, pruefung3
        from pruefer_client import PrueferFehler
        from quelle import QuelleFehler
        if runde not in (1, 2, 3):
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
        self._funde_merken(basis, neu, erg)
        pruefstand = dict(erg["pruefstand"])
        if erg.get("windows_pruefung_noetig"):
            pruefstand["windows_tests"] = "uebersprungen"
        return Ergebnis.ok(pruefstand=pruefstand)
