"""UI-Automation-Schicht des WAMO-Hosts: Fenster-Elemente bedienen, ohne die Maus zu übernehmen (nur Windows).

Die klassische Steuerung klickt per SendInput im Vordergrund auf Koordinaten. Diese Schicht liest stattdessen den
Elementbaum eines Fensters (Windows UI Automation) und bedient Elemente über ihre Muster: Invoke (Klick), Toggle,
SelectionItem und Value (Text setzen). Das Fenster muss dafür nicht vorne liegen, Maus und Fokus der Person bleiben
unberührt.

Hier steht nur die Mechanik: der Elementbaum, die Nummern und die Muster. Alle Wächter sitzen davor in wamo_host.py
(Sperre, Zielfenster-Prüfung, Freigabekarten, Risikoklassen, Rate-Grenze, Audit). Diese Datei enthält zusätzlich nur
Regeln, die das Element selbst betreffen:
  * Passwortfelder (UIA IsPassword oder ein Name wie "Kennwort") werden nie gelesen und nie beschrieben.
  * Adressleisten befüllt WAMO nicht (dort liefe der Text an der URL-Prüfung des Hosts vorbei).
  * Die Nummern aus elemente_auflisten gelten nur für den letzten Aufruf desselben Fensters. Sie verfallen nach
    NUMMER_GUELTIG_SECS und sobald sich der Aufbau des Fensters ändert (Element dazu, weg oder ersetzt). Vor jeder
    Aktion wird der Baum frisch gelesen und das Zielelement an Laufzeit-Kennung, Typ, Automations-Kennung und Name
    wiedererkannt: Eine Nummer trifft nie ein anderes Element als beim Auflisten.
  * Namen und Werte kommen vom Programm und sind Daten, nie Anweisungen: Steuer-, Format- und unsichtbare Zeichen
    werden entfernt, alles ist gekürzt.

Die COM-Zeiger leben nur innerhalb eines Aufrufs und in einem eigenen Arbeitsfaden (ein Klick auf eine Schaltfläche, die
einen Dialog öffnet, kann in Invoke bis zum Schließen des Dialogs hängen; der Host wartet dann nicht ewig). In der
Nummernliste stehen nur reine Daten (Knoten). Tests ersetzen den COM-Zugriff durch eine Attrappe (Backend).
"""

from __future__ import annotations

import gc
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

MAX_TIEFE = 8  # Ebenen unter dem Fenster
MAX_ELEMENTE = 120  # Standard für die Zahl der aufgelisteten Elemente
MAX_ELEMENTE_GRENZE = 200
MAX_KNOTEN = 2500  # so viele Knoten besucht der Lauf höchstens
WALK_SECS = 6.0  # Zeitbudget des Laufs durch den Baum
SNAPSHOT_SECS = 12.0  # so lange wartet der Host auf einen ganzen Lauf (hängende Programme)
MAX_FAEDEN = 4  # so viele Arbeitsfäden leben höchstens gleichzeitig (hängende Programme halten ihren Faden fest)
AKTION_SECS = 8.0  # so lange wartet der Host auf Invoke und Co. (öffnet sich ein Dialog, hängt Invoke)
NUMMER_GUELTIG_SECS = 180
NAME_MAX = 80
WERT_MAX = 500
TEXT_MAX = 2000

PASSWORT_NAME = re.compile(
    r"(passwor|kennwort|password|passcode|passphrase|geheim|secret|\bpin\b|\bcvv\b|\bcvc\b|\botp\b|\btan\b|"
    r"zugangscode|sicherheitscode|security code|\btoken\b|api[ _-]?key|schlüssel|\biban\b|kreditkarte|card number|"
    r"kartennummer)", re.IGNORECASE)
ADRESSFELD_NAME = re.compile(r"(adress|address|omnibox|location bar|suchleiste|search bar|\burl\b)", re.IGNORECASE)

# UIA-Steuerelementtypen -> deutsche Bezeichnung
TYPEN = {
    50000: "Schaltfläche", 50002: "Kontrollkästchen", 50003: "Auswahlliste", 50004: "Eingabefeld", 50005: "Link",
    50007: "Listeneintrag", 50011: "Menüeintrag", 50013: "Optionsfeld", 50015: "Schieberegler", 50016: "Drehfeld",
    50019: "Registerkarte", 50020: "Text", 50024: "Baumeintrag", 50025: "Element", 50026: "Gruppe",
    50029: "Tabelleneintrag", 50030: "Dokument", 50031: "Teilungsschaltfläche", 50033: "Bereich", 50006: "Bild",
}
# Diese Typen gelten ohne weiteres als bedienbar, alle anderen nur mit einem Muster (Invoke, Toggle, ...)
BEDIENBAR = frozenset({
    "Schaltfläche", "Kontrollkästchen", "Auswahlliste", "Eingabefeld", "Link", "Listeneintrag", "Menüeintrag",
    "Optionsfeld", "Schieberegler", "Drehfeld", "Registerkarte", "Baumeintrag", "Tabelleneintrag",
    "Teilungsschaltfläche",
})
TITELLEISTE = 50037  # UIA-Typ TitleBar
PASSWORTFELD = "Passwortfeld"
MUSTER_REIHENFOLGE = ("invoke", "toggle", "auswahl", "aufklappen")  # so wird ein Klick ausgeführt


class UiaFehler(ValueError):
    """Eine Aktion ist nicht möglich oder nicht erlaubt; die Meldung geht an das Sprachmodell (kein Stacktrace)."""


MELDUNG_KEINE_ANTWORT = "Das Programm antwortet nicht auf UI Automation (hängt es?)."
VERALTET = ("Die Elemente-Nummern sind veraltet (das Fenster hat sich geändert oder die Liste ist zu alt). "
            "Bitte elemente_auflisten noch einmal aufrufen.")


def sicherer_text(wert: object, grenze: int = NAME_MAX) -> str:
    """Anzeigetext vom Programm als Daten: ohne Steuer-, Format- und unsichtbare Zeichen, eine Zeile, gekürzt."""
    text = unicodedata.normalize("NFKC", wert) if isinstance(wert, str) else ""  # vollbreite Zeichen wie "Ｓｅｎｄｅｎ"
    text = "".join(" " if unicodedata.category(c) in ("Zl", "Zp") or c in "\r\n\t" else c
                   for c in text if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn") or c in "\r\n\t")
    text = " ".join(text.split())
    return text if len(text) <= grenze else text[: grenze - 1] + "…"


ANZEIGE_TYPEN = frozenset({"Text", "Dokument"})  # Anzeigetexte ändern sich ständig und gehören nicht zum Aufbau


def aufbau_von(knoten) -> tuple:
    """Aufbau eines Fensters aus seinen Knoten: bedienbare Elemente und Gruppen; reine Anzeigetexte zählen nicht
    (im Rechner erscheint nach dem ersten Klick ein neuer Ausdruck-Text, das macht keine Nummer ungültig)."""
    return tuple(k.aufbau for k in knoten if k.typ not in ANZEIGE_TYPEN)


@dataclass(frozen=True)
class Knoten:
    """Ein Element des Fensters als reine Daten (keine COM-Zeiger)."""

    rid: tuple  # Laufzeit-Kennung von UI Automation (oder der Pfad im Baum, falls das Programm keine liefert)
    typ: str
    name: str
    aid: str = ""  # Automations-Kennung
    aktiv: bool = True
    passwort: bool = False
    nur_lesen: bool = False
    muster: tuple[str, ...] = ()
    zustand: tuple[str, ...] = ()

    @property
    def schluessel(self) -> tuple:
        """Woran ein Element wiedererkannt wird: Kennung, Typ und Name müssen gleich sein."""
        return (self.rid, self.typ, self.aid, self.name)

    @property
    def aufbau(self) -> tuple:
        """Beitrag zum Aufbau des Fensters (ohne Namen: Titel und Zähler dürfen sich ändern)."""
        return (self.rid, self.typ, self.aid)

    @property
    def geschuetzt(self) -> bool:
        return self.passwort or bool(PASSWORT_NAME.search(self.name) or PASSWORT_NAME.search(self.aid))


@dataclass
class Liste:
    hwnd: int
    programm: str
    zeit: float
    knoten: list[Knoten]
    aufbau: tuple
    auch_texte: bool
    tiefe: int
    gekuerzt: bool = False
    maximal: int = MAX_ELEMENTE


class Backend:
    """Zugriff auf UI Automation. ComUia ist die echte Umsetzung, Tests nehmen eine Attrappe."""

    def momentaufnahme(self, hwnd: int, tiefe: int, maximal: int, auch_texte: bool) -> tuple[list[tuple[Knoten, object]], bool]:
        """Liste aus (Knoten, Handle) in Baumreihenfolge und ob gekürzt wurde. Handles sind nur in diesem Aufruf gültig."""
        raise NotImplementedError

    def tun(self, handle: object, was: str, text: str | None = None) -> str:
        """was: invoke, toggle, auswahl, aufklappen, wert_setzen, wert_lesen. Liefert bei wert_lesen den Wert."""
        raise NotImplementedError

    def faden_ende(self) -> None:
        """Wird am Ende jedes Arbeitsfadens aufgerufen (COM-Abmeldung); die Attrappe braucht nichts."""


class UiaSchicht:
    """Nummernliste je Fenster und die Aktionen darauf. Eine Instanz je Host."""

    def __init__(self, backend: Backend | None = None, uhr: Callable[[], float] = time.monotonic):
        self._backend = backend
        self._uhr = uhr
        self._listen: dict[int, Liste] = {}
        self._lock = threading.RLock()
        self._faeden = threading.BoundedSemaphore(MAX_FAEDEN)

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = ComUia()
        return self._backend

    # ---------- Lauf im Arbeitsfaden ----------
    def _arbeiten(self, fn: Callable[[dict], object], secs: float) -> tuple[bool, object]:
        """Führt fn(phase) in einem eigenen Faden aus. Rückgabe (fertig, Ergebnis). Läuft der Faden nach secs noch,
        ist fertig False und das Ergebnis die Phase, in der er steckt. Der Aufrufer setzt dann phase["abbruch"]
        (unter phase["sperre"]): Ein Faden, der noch vor der Aktion steckt, führt sie später nie mehr aus und
        verändert auch keine Nummern mehr. Läuft er schon in der Aktion, gilt sie als ausgelöst."""
        if not self._faeden.acquire(blocking=False):
            raise UiaFehler("UI Automation ist gerade belegt (ein Programm antwortet nicht). Bitte später noch einmal.")
        phase = {"p": "start", "abbruch": False, "sperre": threading.Lock()}
        ergebnis: dict = {}

        def lauf() -> None:
            try:
                ergebnis["wert"] = fn(phase)
            except BaseException as e:  # noqa: BLE001 (wird im Aufrufer neu geworfen)
                ergebnis["fehler"] = e
            finally:
                try:
                    self.backend.faden_ende()
                finally:
                    self._faeden.release()

        faden = threading.Thread(target=lauf, name="wamo-uia", daemon=True)
        faden.start()
        faden.join(secs)
        if faden.is_alive():
            with phase["sperre"]:
                phase["abbruch"] = True
                return False, phase["p"]
        if "fehler" in ergebnis:
            fehler = ergebnis["fehler"]
            if isinstance(fehler, UiaFehler):
                raise fehler
            raise UiaFehler(f"UI Automation hat nicht geantwortet ({type(fehler).__name__})") from fehler
        return True, ergebnis.get("wert")

    # ---------- Auflisten ----------
    def auflisten(self, hwnd: int, programm: str, tiefe: int = MAX_TIEFE, maximal: int = MAX_ELEMENTE,
                  auch_texte: bool = False) -> Liste:
        tiefe = max(1, min(MAX_TIEFE, int(tiefe)))
        maximal = max(1, min(MAX_ELEMENTE_GRENZE, int(maximal)))

        def lauf(phase: dict):
            phase["p"] = "momentaufnahme"
            paare, gekuerzt = self.backend.momentaufnahme(hwnd, tiefe, maximal, bool(auch_texte))
            return [k for k, _ in paare], gekuerzt  # nur Daten verlassen den Faden, nie COM-Zeiger

        fertig, wert = self._arbeiten(lauf, SNAPSHOT_SECS)
        if not fertig:
            raise UiaFehler(MELDUNG_KEINE_ANTWORT + " Es wurde nichts verändert.")
        knoten, gekuerzt = wert
        liste = Liste(hwnd=hwnd, programm=programm, zeit=self._uhr(), knoten=knoten,
                      aufbau=aufbau_von(knoten), auch_texte=bool(auch_texte), tiefe=tiefe,
                      gekuerzt=gekuerzt, maximal=maximal)
        with self._lock:
            self._listen[hwnd] = liste  # ersetzt die frühere Liste dieses Fensters: ihre Nummern sind damit ungültig
            for h in [h for h, li in self._listen.items() if self._uhr() - li.zeit > NUMMER_GUELTIG_SECS]:
                del self._listen[h]
        return liste

    # ---------- Nummern ----------
    def vergessen(self, hwnd: int) -> None:
        with self._lock:
            self._listen.pop(hwnd, None)

    def holen(self, hwnd: int, programm: str, nummer: object) -> tuple[Liste, Knoten]:
        """Liste und Knoten zu einer Nummer, sonst UiaFehler. Prüft nur die Liste (Alter, Fenster, Nummer); den
        frischen Aufbau prüft ausfuehren()."""
        if isinstance(nummer, bool) or not isinstance(nummer, int):
            raise UiaFehler("element: die Nummer aus elemente_auflisten (ganze Zahl)")
        with self._lock:
            liste = self._listen.get(hwnd)
            if liste is not None and (self._uhr() - liste.zeit > NUMMER_GUELTIG_SECS
                                      or liste.programm.lower() != (programm or "").lower()):
                self._listen.pop(hwnd, None)
                liste = None
        if liste is None:
            raise UiaFehler("Für dieses Fenster gibt es keine gültige Elementliste. Zuerst elemente_auflisten aufrufen.")
        if not 1 <= nummer <= len(liste.knoten):
            raise UiaFehler(f"Element {nummer} gibt es nicht (1 bis {len(liste.knoten)})")
        return liste, liste.knoten[nummer - 1]

    # ---------- Aktionen ----------
    def _frisch(self, hwnd: int, liste: Liste | None, ziel: Knoten, phase: dict) -> tuple[Knoten, object]:
        """Frische Momentaufnahme: stimmt der Aufbau (nur mit liste) und gibt es das Zielelement noch, liefert das
        Handle des Elements aus DIESER Aufnahme. Sonst UiaFehler(VERALTET)."""
        phase["p"] = "momentaufnahme"
        tiefe, maximal, texte = (liste.tiefe, liste.maximal, liste.auch_texte) if liste else (MAX_TIEFE,
                                                                                                MAX_ELEMENTE_GRENZE, True)
        paare, _ = self.backend.momentaufnahme(hwnd, tiefe, maximal, texte)
        if liste is not None and aufbau_von([k for k, _ in paare]) != liste.aufbau:
            self._vergessen_aus_faden(hwnd, phase)
            raise UiaFehler(VERALTET)
        for k, handle in paare:
            if k.schluessel == ziel.schluessel:
                return k, handle
        self._vergessen_aus_faden(hwnd, phase)
        raise UiaFehler(VERALTET)

    def _vergessen_aus_faden(self, hwnd: int, phase: dict) -> None:
        """Wie vergessen, aber nicht aus einem Faden, den der Aufrufer schon aufgegeben hat (er könnte eine inzwischen
        neu aufgelistete Liste löschen)."""
        with phase["sperre"]:
            if not phase["abbruch"]:
                self.vergessen(hwnd)

    def _handeln(self, hwnd: int, liste: Liste | None, ziel: Knoten, was: str, text: str | None, secs: float,
                 vorher: Callable[[Knoten], None] | None = None) -> tuple[bool, object]:
        def lauf(phase: dict):
            k, handle = self._frisch(hwnd, liste, ziel, phase)
            if vorher is not None:
                vorher(k)  # Prüfung am frischen Zustand (aktiv, nicht schreibgeschützt, ...)
            with phase["sperre"]:
                if phase["abbruch"]:
                    raise UiaFehler("abgebrochen")  # der Aufrufer hat schon "nichts passiert" gemeldet
                phase["p"] = "aktion"
            return self.backend.tun(handle, was, text)

        return self._arbeiten(lauf, secs)

    @staticmethod
    def _muster_fuer_klick(k: Knoten) -> str:
        for m in MUSTER_REIHENFOLGE:
            if m in k.muster:
                return m
        raise UiaFehler(f"{k.typ} „{k.name}“ lässt sich nicht ohne Maus bedienen. Nur wenn nötig: auf_bildschirm_klicken.")

    def klicken(self, hwnd: int, liste: Liste | None, ziel: Knoten) -> dict:
        """Klickt per Muster. liste=None (nach einer Freigabe) prüft nur das Element selbst, nicht den ganzen Aufbau."""
        if ziel.geschuetzt:
            raise UiaFehler("Passwortfelder bedient WAMO nicht.")
        muster = self._muster_fuer_klick(ziel)

        def vorher(k: Knoten) -> None:
            if not k.aktiv:
                raise UiaFehler(f"„{k.name}“ ist deaktiviert")

        fertig, wert = self._handeln(hwnd, liste, ziel, muster, None, AKTION_SECS, vorher)
        if not fertig:
            if wert == "aktion":  # Invoke hängt, bis ein Dialog geschlossen wird: der Klick ist ausgelöst
                self.vergessen(hwnd)
                return {"ausgefuehrt": True, "muster": muster, "ruecklauf": False,
                        "hinweis": "Der Klick ist ausgelöst, das Programm antwortet noch nicht (öffnet sich ein Dialog?)."}
            raise UiaFehler(MELDUNG_KEINE_ANTWORT + " Es wurde nichts angeklickt.")
        return {"ausgefuehrt": True, "muster": muster, "ruecklauf": True}

    def text_setzen(self, hwnd: int, liste: Liste | None, ziel: Knoten, text: str) -> dict:
        if not isinstance(text, str) or len(text) > TEXT_MAX:
            raise UiaFehler(f"Text fehlt oder ist länger als {TEXT_MAX} Zeichen")
        pruefe_beschreibbar(ziel)

        def vorher(k: Knoten) -> None:
            pruefe_beschreibbar(k)

        fertig, _ = self._handeln(hwnd, liste, ziel, "wert_setzen", text, AKTION_SECS, vorher)
        if not fertig:
            raise UiaFehler(MELDUNG_KEINE_ANTWORT + " Der Text wurde nicht gesetzt.")
        return {"ausgefuehrt": True, "zeichen": len(text)}

    def lesen(self, hwnd: int, liste: Liste, ziel: Knoten) -> dict:
        if ziel.geschuetzt:
            raise UiaFehler("Passwortfelder liest WAMO nicht.")

        def vorher(k: Knoten) -> None:
            if k.geschuetzt:
                raise UiaFehler("Passwortfelder liest WAMO nicht.")

        if "wert" not in ziel.muster:
            return {"wert": sicherer_text(ziel.name, WERT_MAX), "gekuerzt": False}  # nur der Anzeigename
        fertig, wert = self._handeln(hwnd, liste, ziel, "wert_lesen", None, AKTION_SECS, vorher)
        if not fertig:
            raise UiaFehler(MELDUNG_KEINE_ANTWORT)
        return {"wert": sicherer_text(wert, WERT_MAX), "gekuerzt": len(str(wert or "")) > WERT_MAX}


def pruefe_beschreibbar(k: Knoten) -> None:
    """UiaFehler, wenn in dieses Element kein Text gesetzt werden darf."""
    if k.geschuetzt:
        raise UiaFehler("Passwortfelder beschreibt WAMO nicht.")
    if ADRESSFELD_NAME.search(k.name) or ADRESSFELD_NAME.search(k.aid):
        raise UiaFehler("Adressleisten befüllt WAMO nicht (Webseiten öffnet webseite_oeffnen mit geprüfter Adresse).")
    if "wert" not in k.muster:
        raise UiaFehler(f"{k.typ} „{k.name}“ nimmt keinen Text an")
    if k.nur_lesen:
        raise UiaFehler(f"„{k.name}“ ist schreibgeschützt")
    if not k.aktiv:
        raise UiaFehler(f"„{k.name}“ ist deaktiviert")


def beschreibung(nummer: int, k: Knoten) -> dict:
    """Ein Eintrag der Liste für das Sprachmodell: Nummer, Name, Typ, Zustand (nie ein Wert, nie bei Passwortfeldern)."""
    if k.geschuetzt:
        return {"nr": nummer, "name": k.name, "typ": PASSWORTFELD, "gesperrt": True}
    eintrag: dict = {"nr": nummer, "name": k.name or "(ohne Name)", "typ": k.typ}
    zustand = list(k.zustand)
    if not k.aktiv:
        zustand.insert(0, "deaktiviert")
    if k.nur_lesen:
        zustand.append("schreibgeschützt")
    if zustand:
        eintrag["zustand"] = zustand
    return eintrag


# ---------------------------------------------------------------------------------------------------------------------
# Echter Zugriff über comtypes (MIT) auf UIAutomationCore.dll
# ---------------------------------------------------------------------------------------------------------------------
class ComUia(Backend):
    """UI Automation über comtypes. Jeder Aufruf läuft in einem eigenen Faden mit eigener COM-Anmeldung; COM-Zeiger
    werden nie zwischen Aufrufen aufbewahrt."""

    def __init__(self) -> None:
        self._modul = None
        self._lock = threading.Lock()
        self._lokal = threading.local()

    def _lade(self):
        """comtypes und die generierte Typbibliothek (einmal je Prozess). Fehler heißen UiaFehler."""
        with self._lock:
            if self._modul is None:
                try:
                    import comtypes
                    import comtypes.client
                    comtypes.client.GetModule("UIAutomationCore.dll")
                    from comtypes.gen import UIAutomationClient
                except Exception as e:  # noqa: BLE001 (comtypes fehlt oder kein Windows)
                    raise UiaFehler(f"UI Automation ist auf diesem PC nicht verfügbar ({type(e).__name__})") from e
                self._modul = (comtypes, UIAutomationClient)
        return self._modul

    def _anmelden(self):
        comtypes, uia = self._lade()
        if not getattr(self._lokal, "angemeldet", False):
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
                self._lokal.angemeldet = True
            except OSError:
                pass  # Faden ist schon in einem anderen Modus angemeldet: weiter, aber nicht abmelden
        return comtypes, uia

    def faden_ende(self) -> None:
        """COM-Zeiger freigeben, dann den Faden abmelden (nur wenn er von hier angemeldet wurde)."""
        if self._modul is None or not getattr(self._lokal, "angemeldet", False):
            return
        gc.collect()
        try:
            self._modul[0].CoUninitialize()
        except OSError:
            pass
        self._lokal.angemeldet = False

    def momentaufnahme(self, hwnd: int, tiefe: int, maximal: int, auch_texte: bool):
        import ctypes

        comtypes, uia = self._anmelden()
        try:
            auto = comtypes.CoCreateInstance(uia.CUIAutomation._reg_clsid_, interface=uia.IUIAutomation,
                                             clsctx=comtypes.CLSCTX_INPROC_SERVER)
            wurzel = auto.ElementFromHandle(ctypes.c_void_p(int(hwnd)))
            walker = auto.ControlViewWalker
        except Exception as e:  # noqa: BLE001
            raise UiaFehler(f"Das Fenster ist für UI Automation nicht erreichbar ({type(e).__name__})") from e

        ergebnis: list[tuple[Knoten, object]] = []
        besucht = 0
        gekuerzt = False
        ende = time.monotonic() + WALK_SECS
        # Tiefensuche in Baumreihenfolge: (Element, Tiefe, Pfad)
        stapel: list[tuple[object, int, tuple]] = [(wurzel, 0, ())]
        while stapel:
            el, ebene, pfad = stapel.pop()
            if ebene > 0:
                besucht += 1
                try:
                    if int(el.CurrentControlType) == TITELLEISTE:
                        continue  # Systemmenü, Minimieren, Schließen: nie auflisten, auch nicht darunter
                except Exception:  # noqa: BLE001
                    continue
                knoten = self._knoten(uia, el, pfad, auch_texte)
                if knoten is not None:
                    if len(ergebnis) >= maximal:
                        gekuerzt = True
                        break
                    ergebnis.append((knoten, el))
            if besucht >= MAX_KNOTEN or time.monotonic() > ende:
                gekuerzt = gekuerzt or bool(stapel)
                break
            if ebene >= tiefe:
                continue
            kinder = []
            try:
                kind = walker.GetFirstChildElement(el)
                index = 0
                while kind:
                    if len(kinder) >= 400:
                        gekuerzt = True
                        break
                    kinder.append((kind, ebene + 1, pfad + (index,)))
                    index += 1
                    kind = walker.GetNextSiblingElement(kind)
            except Exception:  # noqa: BLE001 (Programm entzieht sich mitten im Lauf: Rest des Zweigs auslassen)
                pass
            stapel.extend(reversed(kinder))  # erstes Kind zuerst abarbeiten
        return ergebnis, gekuerzt

    def _knoten(self, uia, el, pfad: tuple, auch_texte: bool) -> Knoten | None:
        """Knoten für ein Element oder None, wenn es nicht aufgelistet wird (nicht bedienbar, außerhalb, Fehler)."""
        try:
            typ_id = int(el.CurrentControlType)
            if el.CurrentIsOffscreen:
                return None
            typ = TYPEN.get(typ_id)
            if typ is None:
                return None
            name = sicherer_text(el.CurrentName)
            muster = self._muster(uia, el, typ)
            if typ not in BEDIENBAR and not muster and not (auch_texte and typ in ANZEIGE_TYPEN and name):
                return None
            try:
                rid = tuple(int(x) for x in el.GetRuntimeId())
            except Exception:  # noqa: BLE001
                rid = ()
            passwort = bool(el.CurrentIsPassword)
            zustand, nur_lesen = self._zustand(uia, el, muster)
            return Knoten(rid=rid or ("pfad",) + pfad, typ=typ, name=name, aid=sicherer_text(el.CurrentAutomationId, 60),
                          aktiv=bool(el.CurrentIsEnabled), passwort=passwort, nur_lesen=nur_lesen,
                          muster=muster, zustand=zustand)
        except Exception:  # noqa: BLE001 (Element verschwunden oder verweigert die Auskunft)
            return None

    @staticmethod
    def _muster(uia, el, typ: str) -> tuple[str, ...]:
        ids = (("invoke", "UIA_IsInvokePatternAvailablePropertyId"), ("toggle", "UIA_IsTogglePatternAvailablePropertyId"),
               ("auswahl", "UIA_IsSelectionItemPatternAvailablePropertyId"),
               ("wert", "UIA_IsValuePatternAvailablePropertyId"),
               ("aufklappen", "UIA_IsExpandCollapsePatternAvailablePropertyId"))
        gefunden = []
        for muster, prop in ids:
            try:
                if el.GetCurrentPropertyValue(getattr(uia, prop)):
                    gefunden.append(muster)
            except Exception:  # noqa: BLE001
                continue
        return tuple(gefunden)

    @staticmethod
    def _zustand(uia, el, muster: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
        zustand: list[str] = []
        nur_lesen = False
        try:
            if el.CurrentHasKeyboardFocus:
                zustand.append("fokussiert")
        except Exception:  # noqa: BLE001
            pass
        try:
            if "toggle" in muster:
                p = el.GetCurrentPattern(uia.UIA_TogglePatternId).QueryInterface(uia.IUIAutomationTogglePattern)
                zustand.append({0: "aus", 1: "an", 2: "unbestimmt"}.get(int(p.CurrentToggleState), "unbestimmt"))
            if "auswahl" in muster:
                p = el.GetCurrentPattern(uia.UIA_SelectionItemPatternId).QueryInterface(uia.IUIAutomationSelectionItemPattern)
                if p.CurrentIsSelected:
                    zustand.append("ausgewählt")
            if "aufklappen" in muster:
                p = el.GetCurrentPattern(uia.UIA_ExpandCollapsePatternId).QueryInterface(uia.IUIAutomationExpandCollapsePattern)
                zustand.append({0: "zugeklappt", 1: "aufgeklappt"}.get(int(p.CurrentExpandCollapseState), "teilweise"))
            if "wert" in muster:
                p = el.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
                nur_lesen = bool(p.CurrentIsReadOnly)
        except Exception:  # noqa: BLE001
            pass
        return tuple(zustand), nur_lesen

    def tun(self, handle: object, was: str, text: str | None = None) -> str:
        comtypes, uia = self._anmelden()
        pattern, schnittstelle = {
            "invoke": (uia.UIA_InvokePatternId, uia.IUIAutomationInvokePattern),
            "toggle": (uia.UIA_TogglePatternId, uia.IUIAutomationTogglePattern),
            "auswahl": (uia.UIA_SelectionItemPatternId, uia.IUIAutomationSelectionItemPattern),
            "aufklappen": (uia.UIA_ExpandCollapsePatternId, uia.IUIAutomationExpandCollapsePattern),
            "wert_setzen": (uia.UIA_ValuePatternId, uia.IUIAutomationValuePattern),
            "wert_lesen": (uia.UIA_ValuePatternId, uia.IUIAutomationValuePattern),
        }.get(was, (None, None))
        if pattern is None:
            return ""  # name_lesen: der Name steht schon im Knoten
        try:
            p = handle.GetCurrentPattern(pattern).QueryInterface(schnittstelle)
            if was == "invoke":
                p.Invoke()
            elif was == "toggle":
                p.Toggle()
            elif was == "auswahl":
                p.Select()
            elif was == "aufklappen":
                if int(p.CurrentExpandCollapseState) == 0:
                    p.Expand()
                else:
                    p.Collapse()
            elif was == "wert_setzen":
                p.SetValue(text or "")
            elif was == "wert_lesen":
                return str(p.CurrentValue or "")
        except Exception as e:  # noqa: BLE001
            raise UiaFehler(f"Das Element hat die Aktion nicht angenommen ({type(e).__name__})") from e
        return ""

