"""Vorgang eines Entwicklungsauftrags: Schritte aus dem Stream von Claude Code, Freigaben, Kriterien und Ende.

Claude Code läuft mit `-p --output-format stream-json --verbose` (dev_agent.py). Im Stream stehen seine Werkzeugaufrufe:
  * TaskCreate und TaskUpdate (Aufgabenliste): jede Aufgabe wird ein Schritt. Geführt wird eine Karte nach Task-ID. Die ID
    vergibt das Werkzeug; bis sein Ergebnis eintrifft, zählt eine geschätzte, danach die im Ergebnis genannte ("Task #7").
  * TodoWrite (ältere Sitzungen): jeder Aufruf ersetzt die ganze Liste.
  * Ereignisse von Unteragenten (parent_tool_use_id gesetzt) ändern die Liste nie.
Gibt es keine Aufgabenliste, zeigt der Vorgang die Schritte des Auftrags (Job.steps, Werkzeugaufrufe) und täuscht keinen
Fortschritt vor: Die Zahl der Schritte steht dann nicht fest (offenes_ende, gesamt höchstens 0,9, keine Restzeit).

Ein Auftrag hat drei Prüfkriterien: Wächter-Selbsttest bestanden, Lauf ohne Fehler beendet, keine Freigabe offen. Offene
Freigaben des Auftrags setzen den Vorgang auf "wartet auf Freigabe" und tragen die Kennung der vorhandenen Karte
(ui/src/components/Freigaben.tsx); die Karte wird nicht neu gebaut.

Test-Treiber ohne echtes Claude: `python vorgang_stream.py stream.jsonl` spielt einen aufgezeichneten Stream (eine
JSON-Zeile je Ereignis) ein und gibt die Nachrichten an die Oberfläche als Zeilen aus. Eine Beispieldatei liegt unter
testdaten/vorgang_stream.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from loguru import logger

from vorgaenge import Schritt, Vorgaenge, Vorgang, bereinigt, MAX_SCHRITTE

KRIT_SELBSTTEST = "Wächter-Selbsttest bestanden"
KRIT_LAUF = "Lauf ohne Fehler beendet"
KRIT_FREIGABEN = "Keine Freigabe offen"
MAX_AUFGABEN = 100  # Aufgaben der Liste im Speicher
MAX_SCHRITTE_RUECKFALL = 30  # ohne Aufgabenliste: die letzten Schritte des Auftrags
TITEL_ZEICHEN = 160
FREIGABEN_MERKEN = 10  # entschiedene Freigaben, die als Schritt stehen bleiben

STATUS_ABBILDUNG = {"pending": "offen", "in_progress": "laeuft", "completed": "fertig"}
_TASK_NUMMER = re.compile(r"#(\d{1,6})\b")


class _Aufgabe:
    __slots__ = ("titel", "status", "aktiv")

    def __init__(self, titel: str, aktiv: str | None):
        self.titel = titel
        self.status = "offen"
        self.aktiv = aktiv


def _kennung(wert: object) -> str | None:
    """Task-ID als Text (Zahl oder Text, auch mit führendem #), sonst None."""
    if isinstance(wert, bool) or not isinstance(wert, (str, int)):
        return None
    text = str(wert).strip().lstrip("#")
    return text if 0 < len(text) <= 40 else None


def _bloecke(ev: dict) -> list[dict]:
    nachricht = ev.get("message")
    inhalt = nachricht.get("content") if isinstance(nachricht, dict) else None
    return [b for b in inhalt if isinstance(b, dict)] if isinstance(inhalt, list) else []


class TaskListe:
    """Aufgabenliste von Claude Code aus dem Stream. ereignis() meldet, ob sich die Liste geändert hat; es wirft nie."""

    def __init__(self) -> None:
        self._aufgaben: dict[str, _Aufgabe] = {}
        self._wartend: dict[str, str] = {}  # tool_use_id von TaskCreate -> geschätzte Task-ID bis zum Ergebnis
        self._zaehler = 0

    def leer(self) -> bool:
        return not self._aufgaben

    def zuruecksetzen(self) -> None:
        self._aufgaben.clear()
        self._wartend.clear()
        self._zaehler = 0

    def schritte(self) -> list[Schritt]:
        return [Schritt(0, a.titel, a.status, 1.0 if a.status == "fertig" else 0.0,
                        a.aktiv if a.status == "laeuft" else None) for a in self._aufgaben.values()]

    def ereignis(self, ev: object) -> bool:
        if not isinstance(ev, dict):
            return False
        try:
            if ev.get("type") == "assistant":
                return self._aufrufe(ev)
            if ev.get("type") == "user":
                return self._ergebnisse(ev)
        except Exception as e:  # noqa: BLE001 (ein unerwartetes Format darf den Lauf nie stören)
            logger.debug("Vorgänge: Ereignis nicht gelesen ({})", type(e).__name__)
        return False

    # ---------- Aufrufe ----------
    def _aufrufe(self, ev: dict) -> bool:
        if ev.get("parent_tool_use_id"):  # Unteragent: eigene Listen, nicht die des Auftrags
            return False
        geaendert = False
        for block in _bloecke(ev):
            if block.get("type") != "tool_use":
                continue
            eingabe = block.get("input") if isinstance(block.get("input"), dict) else {}
            name = block.get("name")
            if name == "TaskCreate":
                geaendert |= self._anlegen(block.get("id"), eingabe)
            elif name == "TaskUpdate":
                geaendert |= self._aendern(eingabe)
            elif name == "TodoWrite":
                geaendert |= self._ersetzen(eingabe)
        return geaendert

    def _anlegen(self, aufruf_id: object, eingabe: dict) -> bool:
        titel = bereinigt(eingabe.get("subject") or eingabe.get("title") or eingabe.get("description"), TITEL_ZEICHEN)
        if not titel or len(self._aufgaben) >= MAX_AUFGABEN:
            return False
        self._zaehler += 1
        kennung = str(self._zaehler)
        while kennung in self._aufgaben:
            self._zaehler += 1
            kennung = str(self._zaehler)
        self._aufgaben[kennung] = _Aufgabe(titel, bereinigt(eingabe.get("activeForm"), TITEL_ZEICHEN) or None)
        if isinstance(aufruf_id, str) and aufruf_id:
            self._wartend[aufruf_id] = kennung
        return True

    def _aendern(self, eingabe: dict) -> bool:
        aufgabe = self._aufgaben.get(_kennung(eingabe.get("taskId")) or "")
        if aufgabe is None:
            return False
        kennung = _kennung(eingabe.get("taskId")) or ""
        status = eingabe.get("status")
        if status == "deleted":
            del self._aufgaben[kennung]
            return True
        geaendert = False
        if isinstance(status, str) and status in STATUS_ABBILDUNG and aufgabe.status != STATUS_ABBILDUNG[status]:
            aufgabe.status, geaendert = STATUS_ABBILDUNG[status], True
        neuer_titel = bereinigt(eingabe.get("subject"), TITEL_ZEICHEN) if eingabe.get("subject") else ""
        if neuer_titel and neuer_titel != aufgabe.titel:
            aufgabe.titel, geaendert = neuer_titel, True
        aktiv = bereinigt(eingabe.get("activeForm"), TITEL_ZEICHEN) if eingabe.get("activeForm") else ""
        if aktiv and aktiv != aufgabe.aktiv:
            aufgabe.aktiv, geaendert = aktiv, True
        return geaendert

    def _ersetzen(self, eingabe: dict) -> bool:
        todos = eingabe.get("todos")
        if not isinstance(todos, list):
            return False
        neu: dict[str, _Aufgabe] = {}
        for eintrag in todos:
            if not isinstance(eintrag, dict) or len(neu) >= MAX_AUFGABEN:
                continue
            titel = bereinigt(eintrag.get("content") or eintrag.get("subject"), TITEL_ZEICHEN)
            if not titel:
                continue
            a = _Aufgabe(titel, bereinigt(eintrag.get("activeForm"), TITEL_ZEICHEN) or None)
            roh = eintrag.get("status")
            a.status = STATUS_ABBILDUNG.get(roh, "offen") if isinstance(roh, str) else "offen"
            neu[str(len(neu) + 1)] = a
        self._aufgaben = neu
        self._zaehler = len(neu)
        self._wartend.clear()
        return True

    # ---------- Ergebnisse ----------
    def _ergebnisse(self, ev: dict) -> bool:
        geaendert = False
        for block in _bloecke(ev):
            aufruf_id = block.get("tool_use_id")
            if block.get("type") != "tool_result" or not isinstance(aufruf_id, str) or aufruf_id not in self._wartend:
                continue
            geschaetzt = self._wartend.pop(aufruf_id)
            if geschaetzt not in self._aufgaben:
                continue
            if block.get("is_error") is True:  # angelegt wurde nichts
                del self._aufgaben[geschaetzt]
                geaendert = True
                continue
            treffer = _TASK_NUMMER.search(self._ergebnis_text(block.get("content")))
            if treffer and treffer.group(1) != geschaetzt and treffer.group(1) not in self._aufgaben:
                self._umbenennen(geschaetzt, treffer.group(1))
        return geaendert

    @staticmethod
    def _ergebnis_text(inhalt: object) -> str:
        if isinstance(inhalt, str):
            return inhalt[:500]
        if isinstance(inhalt, list):
            return " ".join(str(b.get("text", "")) for b in inhalt if isinstance(b, dict))[:500]
        return ""

    def _umbenennen(self, alt: str, neu: str) -> None:
        """Die ID aus dem Ergebnis des Werkzeugs gilt statt der geschätzten (Reihenfolge der Liste bleibt)."""
        self._aufgaben = {(neu if k == alt else k): a for k, a in self._aufgaben.items()}
        self._zaehler = max(self._zaehler, int(neu))
        for aufruf, kennung in self._wartend.items():
            if kennung == alt:
                self._wartend[aufruf] = neu


def endstatus(status_text: str) -> str:
    """Endstatus des Vorgangs aus dem Statustext des Auftrags (dev_agent.Job.status)."""
    text = str(status_text or "").strip().lower()
    if text.startswith("fertig"):
        return "fertig"
    if text.startswith("abgebrochen"):
        return "abgebrochen"
    return "fehler"


class EntwicklungsVorgang:
    """Der Vorgang eines Entwicklungsauftrags. dev_agent.py ruft: starten (Lauf beginnt), ereignis (jedes Ereignis des
    Streams), lauf_endet_vorlaeufig und lauf_endet (Lauf zu Ende), freigabe_entschieden (Klick auf die Karte)."""

    def __init__(self, vorgaenge: Vorgaenge, titel: str, job_id: str):
        self._reg = vorgaenge
        # Das Canvas des Auftrags (dev_agent._canvas_item, id dev-<job>) wächst während des Laufs: der Vorgang verweist darauf
        self.vorgang: Vorgang = vorgaenge.neu(
            titel, "entwicklung", canvas_id=f"dev-{job_id}", offenes_ende=True,
            kriterien=[KRIT_SELBSTTEST, KRIT_LAUF, KRIT_FREIGABEN],
        )
        self._tasks = TaskListe()
        self._entschieden: list[Schritt] = []
        self._zuletzt: tuple | None = None

    # ---------- Lauf ----------
    def starten(self, fortsetzen: bool) -> None:
        """Ein Lauf beginnt. Fortsetzen (nach einer Freigabe) behält die Liste, ein neuer Lauf beginnt sie neu."""
        if not fortsetzen:
            self._tasks.zuruecksetzen()
            self._entschieden = []
            self._zuletzt = None
            self._reg.schritte_setzen(self.vorgang, [], offenes_ende=True)
            self._reg.freigaben_setzen(self.vorgang, [], None)
        self._reg.status_setzen(self.vorgang, "laeuft")

    def ereignis(self, ev: object, job_schritte: Iterable[str]) -> None:
        """Ein Ereignis des Streams. job_schritte: Job.steps (Rückfall ohne Aufgabenliste). Wirft nie."""
        try:
            if isinstance(ev, dict) and ev.get("type") == "wamo":
                if ev.get("ereignis") == "selbsttest_ok":
                    self._reg.kriterium(self.vorgang, KRIT_SELBSTTEST, True)
                elif ev.get("ereignis") == "selbsttest_fehlgeschlagen":
                    self._reg.kriterium(self.vorgang, KRIT_SELBSTTEST, False, "auf Sokrates-2 fehlgeschlagen")
            self._tasks.ereignis(ev)
            self._abgleichen(job_schritte)
        except Exception as e:  # noqa: BLE001 (die Anzeige darf den Auftrag nie aufhalten)
            logger.warning("Vorgänge: Ereignis nicht ausgewertet ({})", type(e).__name__)

    def _abgleichen(self, job_schritte: Iterable[str]) -> None:
        if not self._tasks.leer():
            schritte, offen = self._tasks.schritte(), False
        else:
            # nur das Ende der Liste ansehen: ein langer Lauf hat tausende Schritte, jedes Ereignis kommt hierher
            ende = job_schritte[-2 * MAX_SCHRITTE_RUECKFALL:] if isinstance(job_schritte, (list, tuple)) \
                else list(job_schritte)[-2 * MAX_SCHRITTE_RUECKFALL:]
            letzte = [t for t in ende if isinstance(t, str) and t.strip()][-MAX_SCHRITTE_RUECKFALL:]
            schritte = [Schritt(0, t, "fertig" if i < len(letzte) - 1 else "laeuft") for i, t in enumerate(letzte)]
            offen = True
        schluessel = (offen, tuple((s.titel, s.status, s.detail) for s in schritte))
        if schluessel != self._zuletzt:
            self._zuletzt = schluessel
            self._reg.schritte_setzen(self.vorgang, schritte[:MAX_SCHRITTE], offenes_ende=offen)

    def verwerfen(self) -> None:
        """Die Person hat den Auftrag auf der Karte verworfen, er lief nie."""
        self._reg.beenden(self.vorgang, "abgebrochen")

    def lauf_endet_vorlaeufig(self) -> None:
        """Der Lauf ist zu Ende, jetzt wird geprüft (Freigaben abrufen): Status pruefen."""
        self._reg.status_setzen(self.vorgang, "pruefen")

    def lauf_endet(self, status_text: str, offen: dict[str, dict]) -> None:
        """Endgültig nach dem Lauf: offene Freigaben (Kennung -> Anfrage) lassen den Vorgang warten, sonst endet er."""
        ende = endstatus(status_text)
        lauf = (ende == "fertig") if not offen else (True if ende == "fertig" else None)
        self._reg.kriterium(self.vorgang, KRIT_LAUF, lauf, None if lauf else bereinigt(status_text, 120))
        self._reg.kriterium(self.vorgang, KRIT_FREIGABEN, not offen, f"{len(offen)} offen" if offen else None)
        if offen:
            self._freigaben_zeigen(offen)
        else:
            self._reg.beenden(self.vorgang, ende)

    # ---------- Freigaben ----------
    def _freigaben_zeigen(self, offen: dict[str, dict]) -> None:
        wartend = [Schritt(0, f"Freigabe: {bereinigt(r.get('grund'), TITEL_ZEICHEN - 10) or 'nötig'}", "wartet_freigabe")
                   for r in offen.values()]
        self._reg.freigaben_setzen(self.vorgang, [*self._entschieden, *wartend], next(iter(offen), None))

    def freigabe_entschieden(self, anfrage: dict, erteilt: bool, rest: dict[str, dict], lauf_folgt: bool = True) -> None:
        """Ein Klick auf die Karte: erteilt (der Lauf wird fortgesetzt) oder abgelehnt. rest: noch offene Freigaben.
        Ohne Rest und ohne folgenden Lauf endet der Vorgang als abgebrochen."""
        grund = bereinigt(anfrage.get("grund"), TITEL_ZEICHEN - 10) or "nötig"
        self._entschieden = [*self._entschieden,
                             Schritt(0, f"Freigabe: {grund}", "fertig" if erteilt else "uebersprungen",
                                     1.0 if erteilt else 0.0)][-FREIGABEN_MERKEN:]
        if rest:
            self._freigaben_zeigen(rest)
            return
        self._reg.freigaben_setzen(self.vorgang, self._entschieden, None)  # wartet nichts mehr: der Lauf geht weiter
        self._reg.kriterium(self.vorgang, KRIT_FREIGABEN, bool(erteilt), None if erteilt else "abgelehnt")
        if not erteilt and not lauf_folgt:
            self._reg.beenden(self.vorgang, "abgebrochen")


# ---------- Test-Treiber ----------
async def einspielen(pfad: str | Path, *, offene_freigaben: dict[str, dict] | None = None,
                     titel: str = "Test-Treiber", takt_s: float = 0.0) -> list[dict]:
    """Spielt einen aufgezeichneten Stream (eine JSON-Zeile je Ereignis) ein und liefert die Nachrichten an die Oberfläche.
    Kaputte Zeilen werden übersprungen. offene_freigaben: Anfragen, die der Auftrag am Ende noch offen hätte."""
    gesendet: list[dict] = []

    async def senden(nachricht: dict) -> None:
        gesendet.append(nachricht)

    reg = Vorgaenge(senden, takt_s=takt_s)
    fuehrung = EntwicklungsVorgang(reg, titel, "treiber")
    await reg.warten()
    fuehrung.starten(fortsetzen=False)
    job_schritte: list[str] = []
    for zeile in Path(pfad).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(zeile)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        for block in _bloecke(ev) if ev.get("type") == "assistant" else []:
            if block.get("type") == "tool_use" and isinstance(block.get("name"), str):
                job_schritte.append(f"Werkzeug: {block['name']}")
        fuehrung.ereignis(ev, job_schritte)
        await reg.warten()  # wie im Lauf: zwischen zwei Ereignissen kommt die Schleife zum Senden
    fuehrung.lauf_endet_vorlaeufig()
    await reg.warten()
    fuehrung.lauf_endet("fertig", offene_freigaben or {})
    await reg.warten()
    return gesendet


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print("Aufruf: python vorgang_stream.py <stream.jsonl>", file=sys.stderr)
        return 2
    pfad = Path(args[0])
    if not pfad.is_file():
        print(f"Datei nicht gefunden: {pfad.name}", file=sys.stderr)
        return 2
    for nachricht in asyncio.run(einspielen(pfad)):
        print(json.dumps(nachricht, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
