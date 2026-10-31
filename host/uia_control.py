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
from dataclasses import dataclass, field

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

# Webseiten in Browsern (Chrome, Edge, Firefox, Brave): Der Dokumentknoten liegt schon 7 Ebenen unter dem Fenster, der
# Seiteninhalt weitere 4 bis 14 darunter. Unter einem Webdokument gilt deshalb eine eigene Tiefe, und der Teilbaum
# wird mit einem Zwischenspeicher (CacheRequest) in einem Zug gelesen: gemessen etwa 12-mal schneller als
# Einzelabfragen (Amazon-Suche, 2750 Knoten: 0,25 s statt 3 s).
WEB_TIEFE = 80  # Ebenen unter dem Dokument
MAX_WEB_KNOTEN = 12000
SEITE_SECS = 15.0  # so lange wartet der Host auf browser_lesen
SEITE_ABSCHNITT_ZEICHEN = 6000  # Größe eines Abschnitts von browser_lesen
SEITE_ZEILE_MAX = 1200
MAX_TABS = 20
TAB_NAME_MAX = 100
DOKUMENT_VERSUCHE = 14  # Chrome und Edge schalten die Barrierefreiheit erst beim ersten UIA-Zugriff ein (Edge: bis ~8 s)
DOKUMENT_WARTE_SECS = 0.5
MAX_FELD_WERTE = 40  # so viele Eingabefelder liest browser_lesen höchstens mit Wert
URL_MAX = 300

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
DOKUMENT = 50030
TAB_ELEMENT = 50019
FENSTERKNOEPFE = re.compile(r"^(minimieren|maximieren|wiederherstellen|schließen|minimize|maximize|restore|close)$",
                            re.IGNORECASE)  # Chrome und Edge führen sie nicht als TitleBar: nie auflisten (Fenster zu = Tabs weg)
# weitere Typen, die nur der Seitentext von browser_lesen braucht (Gliederung, keine Bedienung)
SEITE_TYPEN = {50008: "Liste", 50017: "Statusleiste", 50036: "Tabelle", 50028: "Tabelle", 50034: "Kopf", 50035: "Kopf",
               50021: "Symbolleiste", 50018: "Registerleiste", 50009: "Menü", 50010: "Menü", 50023: "Baum",
               50012: "Fortschritt"}
PASSWORTFELD = "Passwortfeld"
BROWSER_PROGRAMME = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"})
UEBERSCHRIFT_BASIS = 80050  # UIA_HeadingLevelPropertyId: 80050 = keine, 80051 = Ebene 1, ...
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


# ---------------------------------------------------------------------------------------------------------------------
# Browserseiten: Seitentext in Lesereihenfolge (reine Daten und reine Funktionen, ohne COM)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SeitenKnoten:
    """Ein Knoten des Webdokuments in Baumreihenfolge. Text kommt von der Webseite: Daten, nie Anweisungen."""

    tiefe: int  # Ebene unter dem Dokument (1 = direktes Kind)
    typ: str  # Text, Link, Schaltfläche, Eingabefeld, Gruppe, Liste, ...
    name: str = ""
    ebene: int = 0  # Überschriftenebene 1 bis 9 (nur bei typ Text), sonst 0
    sichtbar: bool = True  # nicht außerhalb des sichtbaren Bereichs
    aktiv: bool = True
    geschuetzt: bool = False  # Passwort-, Karten- und ähnliche Felder: nie mit Wert
    wert: str = ""  # Inhalt eines Eingabefelds, nur wenn nicht geschützt
    zustand: str = ""  # an, aus, ausgewählt (Kontrollkästchen, Optionsfeld)
    oben: int = 0  # Lage auf dem Bildschirm; Chrome und Edge liefern Absätze als lose Textstücke ohne Grenze im Baum,
    hoehe: int = 0  # den Absatzwechsel erkennt seite_zeilen am senkrechten Abstand (0 = unbekannt)


@dataclass
class Seite:
    """Was der Browser über seinen aktiven Tab hergibt."""

    fenster_titel: str = ""
    dokument_titel: str = ""
    url: str = ""  # Adresse des Dokuments (Wert des Dokumentknotens) oder, falls es fehlt, der Adressleiste
    tabs: list[tuple[str, bool]] = field(default_factory=list)  # (Titel, aktiv)
    knoten: list[SeitenKnoten] = field(default_factory=list)
    dokument_gefunden: bool = False
    gekuerzt: bool = False  # Knotenobergrenze oder Zeitbudget erreicht


_LUHN = re.compile(r"\d{13,19}")
_IBAN = re.compile(r"[A-Za-z]{2}\d{2}[ ]?[A-Za-z0-9][A-Za-z0-9 ]{9,30}")


def _kartenzahl(wert: str) -> bool:
    ziffern = re.sub(r"[ \-]", "", wert or "")
    if not _LUHN.fullmatch(ziffern):
        return False
    summe = 0
    for i, c in enumerate(reversed(ziffern)):
        d = int(c)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        summe += d
    return summe % 10 == 0


def feld_geschuetzt(name: str, aid: str, passwort: bool, wert: str = "") -> bool:
    """Darf der Wert dieses Eingabefelds nie ins Modell? Passwortfeld (UIA IsPassword), Name oder Kennung wie Kennwort,
    Kartennummer, PIN, Token, und Werte, die wie eine Kartennummer (Luhn) oder IBAN aussehen, auch unter harmlosem Namen."""
    if passwort or PASSWORT_NAME.search(name or "") or PASSWORT_NAME.search(aid or ""):
        return True
    wert = (wert or "").strip()
    return bool(wert) and (_kartenzahl(wert) or bool(_IBAN.fullmatch(wert)))


_URL_GEHEIM = re.compile(r"(token|secret|passw|pwd|sess|auth|key|sig|code|jwt|cred|otp|sid|csrf|ticket|bearer)",
                         re.IGNORECASE)


def url_bereinigen(url: object) -> str:
    """Adresse für das Modell: ohne Zugangsdaten vor dem @, ohne Fragment (dort liegen oft OAuth-Token), Werte von
    Abfrageparametern mit geheim klingenden Namen entfernt, gekürzt. Was keine Adresse ist, wird leer."""
    from urllib.parse import urlsplit

    text = sicherer_text(url, 2000)
    if not text:
        return ""
    try:
        teile = urlsplit(text if "://" in text or text.startswith(("about:", "chrome:", "edge:")) else "http://" + text)
        host = teile.hostname or ""
        port = f":{teile.port}" if teile.port else ""
    except ValueError:
        return ""
    if teile.scheme in ("about", "chrome", "edge", "view-source"):
        return sicherer_text(f"{teile.scheme}://{teile.netloc}{teile.path}" if teile.netloc else f"{teile.scheme}:{teile.path}",
                             URL_MAX)
    if not host:
        return ""
    abfrage = "&".join(f"{teil.partition('=')[0]}=…" if _URL_GEHEIM.search(teil.partition("=")[0]) and "=" in teil else teil
                       for teil in teile.query.split("&") if teil)
    adresse = f"{teile.scheme}://{host}{port}{teile.path}" + (f"?{abfrage}" if abfrage else "")
    return adresse if len(adresse) <= URL_MAX else adresse[: URL_MAX - 1] + "…"


def seitentext(wert: object, grenze: int = SEITE_ZEILE_MAX) -> str:
    """Seitentext als Daten: wie sicherer_text, aber ein Randleerzeichen bleibt (Textstücke einer Zeile werden
    aneinandergehängt), und eckige Klammern werden rund (sonst könnte eine Seite unsere [Link]-Marken nachahmen)."""
    roh = wert if isinstance(wert, str) else ""
    vorn, hinten = roh[:1].isspace(), roh[-1:].isspace()
    text = sicherer_text(roh, grenze).replace("[", "(").replace("]", ")")
    if not text:
        return " " if roh else ""
    return (" " if vorn else "") + text + (" " if hinten else "")


ABSATZ_LUECKE = 0.6  # Abstand zur Unterkante des Vorgängers, ab dem (in Zeilenhöhen) ein neuer Absatz beginnt
EINZELNE_TYPEN = frozenset({"Schaltfläche", "Teilungsschaltfläche", "Eingabefeld", "Kontrollkästchen", "Optionsfeld",
                            "Auswahlliste", "Menüeintrag", "Schieberegler", "Drehfeld", "Registerkarte", "Bild"})
INLINE_TYPEN = frozenset({"Text", "Link"})


def seite_zeilen(knoten: list[SeitenKnoten], nur_sichtbar: bool = True) -> list[str]:
    """Seitentext in Lesereihenfolge: Überschriften als "# Titel", Absätze als Zeilen, Listeneinträge mit "- ", Links
    als "[Text]", Schaltflächen und Eingabefelder als eigene Zeile "[Schaltfläche: Name]", "[Eingabefeld: Name = "Wert"]".
    Zeilenwechsel an jedem Blockwechsel (Gruppe, Liste, Eintrag, Tabelle ...); Textstücke und Links innerhalb eines
    Blocks bilden eine Zeile. Geschützte Felder erscheinen nie mit Wert."""
    zeilen: list[str] = []
    puffer: list[str] = []
    offen: list[int] = []  # Tiefen der geöffneten Blöcke
    praefix = ""
    ueberspringen: int | None = None  # Teilbaum unterhalb dieser Tiefe weglassen (Name steht schon in der Zeile)
    unten: int | None = None  # Unterkante des letzten Text- oder Linkstücks im aktuellen Block

    def zeile_beenden() -> None:
        nonlocal praefix, unten
        unten = None
        text = " ".join("".join(puffer).split())
        puffer.clear()
        if not text:
            return
        text = text if len(text) <= SEITE_ZEILE_MAX else text[: SEITE_ZEILE_MAX - 1] + "…"
        zeile = praefix + text
        praefix = ""
        if not zeilen or zeilen[-1] != zeile:
            zeilen.append(zeile)

    for k in knoten:
        if ueberspringen is not None:
            if k.tiefe > ueberspringen:
                continue
            ueberspringen = None
        while offen and offen[-1] >= k.tiefe:
            offen.pop()
            zeile_beenden()
        name = seitentext(k.name)
        sichtbar = k.sichtbar or not nur_sichtbar
        if k.typ == "Text" and k.ebene:
            if name.strip():
                ueberspringen = k.tiefe
            if sichtbar and name.strip():
                zeile_beenden()
                puffer.append("#" * min(k.ebene, 6) + " " + name)
                zeile_beenden()
        elif k.typ in INLINE_TYPEN:
            if k.typ == "Link" and name.strip():
                ueberspringen = k.tiefe
            if not sichtbar:
                continue
            if not name.strip():
                if name and puffer:
                    puffer.append(" ")  # Leerzeichen zwischen zwei Links
                continue
            if k.hoehe > 0:
                if unten is not None:
                    luecke = k.oben - unten
                    if luecke > ABSATZ_LUECKE * k.hoehe:  # deutlicher Abstand nach unten: neuer Absatz
                        zeile_beenden()
                    elif luecke >= 0 and puffer and not puffer[-1][-1:].isspace() and not name[:1].isspace():
                        puffer.append(" ")  # nächste Bildschirmzeile desselben Absatzes
                unten = max(unten or 0, k.oben + k.hoehe)
            puffer.append(name if k.typ == "Text" else "[" + " ".join(name.split()) + "]")
        elif k.typ in EINZELNE_TYPEN:
            if name.strip():
                ueberspringen = k.tiefe
            if sichtbar and (name.strip() or k.typ in ("Eingabefeld", "Auswahlliste")):
                zeile_beenden()
                puffer.append(_einzelzeile(k, " ".join(name.split())))
                zeile_beenden()
        else:  # Gruppe, Liste, Eintrag, Tabelle, Bereich, Dokument, ...: Blockwechsel
            zeile_beenden()
            offen.append(k.tiefe)
            if k.typ == "Listeneintrag" and sichtbar:
                praefix = "- "
    zeile_beenden()
    return zeilen


def _einzelzeile(k: SeitenKnoten, name: str) -> str:
    if k.typ in ("Eingabefeld", "Auswahlliste"):
        bezeichnung = "Eingabefeld" if k.typ == "Eingabefeld" else "Auswahl"
        if k.geschuetzt:
            return f"[{bezeichnung}: {name or 'ohne Name'} (geschützt, Inhalt nicht gelesen)]"
        wert = " ".join(seitentext(k.wert, 200).split())
        return f"[{bezeichnung}: {name or 'ohne Name'} = „{wert}“]" if wert else f"[{bezeichnung}: {name or 'ohne Name'}, leer]"
    zusatz = (", " + k.zustand) if k.zustand else ""
    zusatz += ", deaktiviert" if not k.aktiv else ""
    return f"[{k.typ}: {name}{zusatz}]"


def abschnitte_bilden(zeilen: list[str], grenze: int = SEITE_ABSCHNITT_ZEICHEN) -> list[str]:
    """Zeilen zu Abschnitten von höchstens etwa grenze Zeichen (nie mitten in einer Zeile getrennt)."""
    abschnitte: list[str] = []
    aktuell: list[str] = []
    laenge = 0
    for z in zeilen:
        if aktuell and laenge + len(z) + 1 > grenze:
            abschnitte.append("\n".join(aktuell))
            aktuell, laenge = [], 0
        aktuell.append(z)
        laenge += len(z) + 1
    if aktuell:
        abschnitte.append("\n".join(aktuell))
    return abschnitte


def seite_aufbereiten(seite: Seite, abschnitt: int = 1, nur_sichtbar: bool = True) -> dict:
    """Ergebnis von browser_lesen aus den Daten der Seite: Titel, Adresse, Tabs und ein Abschnitt Text."""
    if isinstance(abschnitt, bool) or not isinstance(abschnitt, int) or abschnitt < 1:
        raise UiaFehler("abschnitt: ganze Zahl ab 1")
    titel = sicherer_text(seite.dokument_titel or seite.fenster_titel, 200)
    tabs = [{"titel": sicherer_text(t, TAB_NAME_MAX), "aktiv": bool(a)} for t, a in seite.tabs[:MAX_TABS]]
    ergebnis: dict = {"titel": titel, "url": url_bereinigen(seite.url), "tabs": tabs, "tabs_anzahl": len(seite.tabs),
                      "lesbar": bool(seite.dokument_gefunden and seite.knoten)}
    if not ergebnis["lesbar"]:
        ergebnis.update(abschnitt=1, abschnitte=0, text="", gekuerzt=False, nur_sichtbar=bool(nur_sichtbar))
        return ergebnis
    zeilen = seite_zeilen(seite.knoten, nur_sichtbar)
    gefiltert = nur_sichtbar
    if not zeilen and nur_sichtbar:  # Fenster verdeckt oder minimiert: alles gilt als außerhalb, dann den ganzen Text
        zeilen, gefiltert = seite_zeilen(seite.knoten, False), False
    teile = abschnitte_bilden(zeilen)
    if teile and abschnitt > len(teile):
        raise UiaFehler(f"Abschnitt {abschnitt} gibt es nicht (1 bis {len(teile)})")
    ergebnis.update(abschnitt=abschnitt, abschnitte=len(teile), text=teile[abschnitt - 1] if teile else "",
                    gekuerzt=bool(seite.gekuerzt or abschnitt < len(teile)), nur_sichtbar=bool(gefiltert),
                    zeichen_gesamt=sum(len(z) + 1 for z in zeilen))
    return ergebnis


class Backend:
    """Zugriff auf UI Automation. ComUia ist die echte Umsetzung, Tests nehmen eine Attrappe."""

    def seite(self, hwnd: int) -> Seite:
        """Titel, Adresse, Tabs und der Knotenbaum des Webdokuments eines Browserfensters (nur Daten)."""
        raise NotImplementedError

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

    def seite_lesen(self, hwnd: int, abschnitt: int = 1, nur_sichtbar: bool = True) -> dict:
        """Titel, Adresse, Tabs und ein Abschnitt Seitentext eines Browserfensters (nur Lesen, keine Nummern)."""
        if isinstance(abschnitt, bool) or not isinstance(abschnitt, int) or abschnitt < 1:
            raise UiaFehler("abschnitt: ganze Zahl ab 1")

        def lauf(phase: dict):
            phase["p"] = "seite"
            return self.backend.seite(hwnd)  # nur Daten verlassen den Faden

        fertig, seite = self._arbeiten(lauf, SEITE_SECS)
        if not fertig:
            raise UiaFehler(MELDUNG_KEINE_ANTWORT + " Es wurde nichts verändert.")
        return seite_aufbereiten(seite, abschnitt, bool(nur_sichtbar))


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
    if k.nur_lesen and k.typ == "Eingabefeld":  # Links, Dokumente und Co. tragen nur die Adresse schreibgeschützt: kein Hinweis
        zustand.append("schreibgeschützt")
    if zustand:
        eintrag["zustand"] = zustand
    return eintrag


MUSTER_IDS = (("invoke", "UIA_IsInvokePatternAvailablePropertyId"), ("toggle", "UIA_IsTogglePatternAvailablePropertyId"),
              ("auswahl", "UIA_IsSelectionItemPatternAvailablePropertyId"),
              ("wert", "UIA_IsValuePatternAvailablePropertyId"),
              ("aufklappen", "UIA_IsExpandCollapsePatternAvailablePropertyId"))
_TAB_ZUSATZ = re.compile(r"\s+[–-]\s+(Arbeitsspeicher\w*|Memory usage|Speicher\w*)\b.*$", re.IGNORECASE)


def _tab_titel(name: object) -> str:
    """Tab-Titel ohne den Zusatz der Speicheranzeige, den Chrome und Edge an den Namen hängen ("... – Arbeitsspeichernutzung – 24 MB")."""
    return _TAB_ZUSATZ.sub("", sicherer_text(name, 300))


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
        for versuch in range(DOKUMENT_VERSUCHE):
            ergebnis, gekuerzt, web = self._lauf(auto, uia, wurzel, walker, tiefe, maximal, auch_texte)
            # Chrome und Edge schalten die Barrierefreiheit erst beim ersten Zugriff ein: dann fehlt die Webseite noch
            # und erscheint bei der Folgeabfrage (gemessen: erster Lauf 37 Knoten, zweiter 228).
            if web or not self._browser_oberflaeche(wurzel) or versuch == DOKUMENT_VERSUCHE - 1:
                break
            time.sleep(DOKUMENT_WARTE_SECS)
        return ergebnis, gekuerzt

    @staticmethod
    def _browser_oberflaeche(wurzel) -> bool:
        try:
            return wurzel.CurrentFrameworkId in ("Chrome", "Gecko")
        except Exception:  # noqa: BLE001
            return False

    def _lauf(self, auto, uia, wurzel, walker, tiefe: int, maximal: int, auch_texte: bool):
        """Ein Lauf durch den Baum: (Liste aus (Knoten, Handle), gekürzt, wurde ein Webdokument gelesen)."""
        ergebnis: list[tuple[Knoten, object]] = []
        besucht = 0
        gekuerzt = False
        web = False
        ende = time.monotonic() + WALK_SECS
        # Tiefensuche in Baumreihenfolge: (Element, Tiefe, Pfad)
        stapel: list[tuple[object, int, tuple]] = [(wurzel, 0, ())]
        while stapel:
            el, ebene, pfad = stapel.pop()
            if ebene > 0:
                besucht += 1
                try:
                    typ_id = int(el.CurrentControlType)
                    if typ_id == TITELLEISTE:
                        continue  # Systemmenü, Minimieren, Schließen: nie auflisten, auch nicht darunter
                    if typ_id == DOKUMENT and self._ist_webdokument(el):
                        # Webseite eines Browsers: Teilbaum auf einmal (Zwischenspeicher) und eigene Tiefe, das Dokument
                        # selbst steht nicht in der Liste
                        web = True
                        gekuerzt = self._web_knoten(auto, uia, el, pfad, auch_texte, maximal, ergebnis) or gekuerzt
                        continue
                except Exception:  # noqa: BLE001
                    continue
                knoten = self._knoten(uia, el, pfad, auch_texte)
                if knoten is not None and knoten.typ == "Schaltfläche" and ebene <= 6 and FENSTERKNOEPFE.match(knoten.name):
                    knoten = None  # Fensterknöpfe des Browsers (Schließen würde alle Tabs beenden)
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
        return ergebnis, gekuerzt, web

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
        gefunden = []
        for muster, prop in MUSTER_IDS:
            try:
                if el.GetCurrentPropertyValue(getattr(uia, prop)):
                    gefunden.append(muster)
            except Exception:  # noqa: BLE001
                continue
        return tuple(gefunden)

    # ----- Webseiten (Zwischenspeicher) -----
    @staticmethod
    def _ist_webdokument(el) -> bool:
        try:
            return el.CurrentAutomationId == "RootWebArea" or el.CurrentFrameworkId in ("Chrome", "Gecko")
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _cache_anfrage(auto, uia):
        """Alles, was Auflisten und Seitentext brauchen, in einem Zug für den ganzen Teilbaum (Werte nie: Passwörter)."""
        cr = auto.CreateCacheRequest()
        for prop in ("UIA_ControlTypePropertyId", "UIA_NamePropertyId", "UIA_IsOffscreenPropertyId",
                     "UIA_IsPasswordPropertyId", "UIA_AutomationIdPropertyId", "UIA_IsEnabledPropertyId",
                     "UIA_HeadingLevelPropertyId", "UIA_ToggleToggleStatePropertyId", "UIA_BoundingRectanglePropertyId",
                     "UIA_SelectionItemIsSelectedPropertyId", *(p for _, p in MUSTER_IDS)):
            cr.AddProperty(getattr(uia, prop))
        cr.TreeScope = uia.TreeScope_Subtree
        cr.TreeFilter = auto.ControlViewCondition
        return cr

    def _web_knoten(self, auto, uia, dokument, pfad: tuple, auch_texte: bool, maximal: int,
                    ergebnis: list) -> bool:
        """Hängt die bedienbaren Elemente eines Webdokuments an ergebnis an. True, wenn gekürzt wurde."""
        try:
            wurzel = dokument.BuildUpdatedCache(self._cache_anfrage(auto, uia))
        except Exception:  # noqa: BLE001 (Dokument verschwunden oder Browser antwortet nicht)
            return False
        gekuerzt = False
        besucht = 0
        ende = time.monotonic() + WALK_SECS
        stapel = [(wurzel, pfad, 0)]
        while stapel:
            el, p, tiefe = stapel.pop()
            if tiefe > 0:
                besucht += 1
                knoten = self._knoten_cache(uia, el, p, auch_texte)
                if knoten is not None:
                    if len(ergebnis) >= maximal:
                        return True
                    ergebnis.append((knoten, el))
                if besucht >= MAX_WEB_KNOTEN or time.monotonic() > ende:
                    return True
            if tiefe >= WEB_TIEFE:
                continue
            try:
                kinder = el.GetCachedChildren()
                stapel.extend((kinder.GetElement(i), p + (i,), tiefe + 1) for i in range(kinder.Length - 1, -1, -1))
            except Exception:  # noqa: BLE001 (Blatt ohne Kinder)
                continue
        return gekuerzt

    def _knoten_cache(self, uia, el, pfad: tuple, auch_texte: bool) -> Knoten | None:
        """Wie _knoten, aber die Eigenschaften kommen aus dem Zwischenspeicher (kein Prozesswechsel je Eigenschaft)."""
        try:
            if el.CachedIsOffscreen:
                return None
            typ = TYPEN.get(int(el.CachedControlType))
            if typ is None:
                return None
            name = sicherer_text(el.CachedName)
            muster = tuple(m for m, prop in MUSTER_IDS if el.GetCachedPropertyValue(getattr(uia, prop)))
            if typ not in BEDIENBAR and not muster and not (auch_texte and typ in ANZEIGE_TYPEN and name):
                return None
            try:
                rid = tuple(int(x) for x in el.GetRuntimeId())
            except Exception:  # noqa: BLE001
                rid = ()
            zustand, nur_lesen = self._zustand(uia, el, muster)
            return Knoten(rid=rid or ("pfad",) + pfad, typ=typ, name=name,
                          aid=sicherer_text(el.CachedAutomationId, 60), aktiv=bool(el.CachedIsEnabled),
                          passwort=bool(el.CachedIsPassword), nur_lesen=nur_lesen, muster=muster, zustand=zustand)
        except Exception:  # noqa: BLE001
            return None

    # ----- Seite eines Browsers (browser_lesen) -----
    def seite(self, hwnd: int) -> Seite:
        import ctypes

        comtypes, uia = self._anmelden()
        try:
            auto = comtypes.CoCreateInstance(uia.CUIAutomation._reg_clsid_, interface=uia.IUIAutomation,
                                             clsctx=comtypes.CLSCTX_INPROC_SERVER)
            wurzel = auto.ElementFromHandle(ctypes.c_void_p(int(hwnd)))
        except Exception as e:  # noqa: BLE001
            raise UiaFehler(f"Das Fenster ist für UI Automation nicht erreichbar ({type(e).__name__})") from e
        seite = Seite()
        try:
            seite.fenster_titel = sicherer_text(wurzel.CurrentName, 200)
        except Exception:  # noqa: BLE001
            pass
        adresse = ""
        for versuch in range(DOKUMENT_VERSUCHE):
            # Der erste Zugriff schaltet die Barrierefreiheit des Browsers ein; erst die Folgeabfragen liefern die Seite.
            dokument, seite.tabs, adresse = self._geruest(auto, uia, wurzel)
            if dokument is not None:
                seite.dokument_gefunden = True
                self._seite_lesen(auto, uia, dokument, seite)
                if len(seite.knoten) >= 3:
                    break
            if versuch < DOKUMENT_VERSUCHE - 1:
                time.sleep(DOKUMENT_WARTE_SECS)
        if not seite.url:
            seite.url = adresse
        return seite

    def _geruest(self, auto, uia, wurzel):
        """Browser-Oberfläche ohne die Webseite: (Dokument oder None, Tabs, Adresse der Adressleiste). Unter dem
        Dokument wird nie gesucht: Die Seite kann so keine Tabs oder Adressleiste vortäuschen."""
        walker = auto.ControlViewWalker
        tabs: list[tuple[str, bool]] = []
        dokument = None
        edits: list = []
        stapel = [(wurzel, 0)]
        besucht = 0
        while stapel and besucht < 600:
            el, ebene = stapel.pop()
            besucht += 1
            try:
                typ_id = int(el.CurrentControlType)
                if ebene > 0 and typ_id == DOKUMENT:
                    if dokument is None or (el.CurrentAutomationId == "RootWebArea"
                                            and dokument.CurrentAutomationId != "RootWebArea"):
                        dokument = el
                    continue
                if ebene > 0 and typ_id == TAB_ELEMENT and len(tabs) < MAX_TABS:
                    ausgewaehlt = bool(el.GetCurrentPropertyValue(uia.UIA_SelectionItemIsSelectedPropertyId))
                    tabs.append((_tab_titel(el.CurrentName), ausgewaehlt))
                elif ebene > 0 and typ_id == 50004:
                    edits.append(el)
            except Exception:  # noqa: BLE001
                continue
            if ebene >= 16:
                continue
            kinder = []
            try:
                kind = walker.GetFirstChildElement(el)
                while kind and len(kinder) < 200:
                    kinder.append((kind, ebene + 1))
                    kind = walker.GetNextSiblingElement(kind)
            except Exception:  # noqa: BLE001
                pass
            stapel.extend(reversed(kinder))
        adresse = ""
        for el in sorted(edits, key=lambda e: not self._adressfeld(e)):  # Adressleiste zuerst, sonst das erste Feld
            adresse = self._wert(uia, el)
            if adresse:
                break
        return dokument, tabs, adresse

    @staticmethod
    def _adressfeld(el) -> bool:
        try:
            return bool(ADRESSFELD_NAME.search(el.CurrentName or ""))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _wert(uia, el) -> str:
        try:
            muster = el.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
            return str(muster.CurrentValue or "")
        except Exception:  # noqa: BLE001
            return ""

    def _seite_lesen(self, auto, uia, dokument, seite: Seite) -> None:
        """Knotenbaum des Webdokuments (Zwischenspeicher, ein Zug) in seite.knoten, Titel und Adresse des Dokuments."""
        try:
            seite.dokument_titel = sicherer_text(dokument.CurrentName, 200)
            seite.url = self._wert(uia, dokument)
            wurzel = dokument.BuildUpdatedCache(self._cache_anfrage(auto, uia))
        except Exception:  # noqa: BLE001
            return
        knoten: list[SeitenKnoten] = []
        werte = [0]  # so viele Felder wurden schon mit Wert gelesen
        ende = time.monotonic() + WALK_SECS * 2
        stapel = [(wurzel, 0)]
        while stapel:
            el, tiefe = stapel.pop()
            if tiefe > 0:
                if len(knoten) >= MAX_WEB_KNOTEN or time.monotonic() > ende:
                    seite.gekuerzt = True
                    break
                k = self._seiten_knoten(uia, el, tiefe, werte)
                if k is not None:
                    knoten.append(k)
            if tiefe >= WEB_TIEFE:
                seite.gekuerzt = True
                continue
            try:
                kinder = el.GetCachedChildren()
                stapel.extend((kinder.GetElement(i), tiefe + 1) for i in range(kinder.Length - 1, -1, -1))
            except Exception:  # noqa: BLE001
                continue
        seite.knoten = knoten

    def _seiten_knoten(self, uia, el, tiefe: int, werte: list[int]) -> SeitenKnoten | None:
        try:
            typ_id = int(el.CachedControlType)
            typ = TYPEN.get(typ_id) or SEITE_TYPEN.get(typ_id) or "Gruppe"
            if typ in ("Bereich", "Element"):
                typ = "Gruppe"
            name = el.CachedName or ""
            ebene = 0
            if typ == "Text":
                stufe = int(el.GetCachedPropertyValue(uia.UIA_HeadingLevelPropertyId) or 0) - UEBERSCHRIFT_BASIS
                ebene = stufe if 1 <= stufe <= 9 else 0
            sichtbar = not bool(el.CachedIsOffscreen)
            aktiv = bool(el.CachedIsEnabled)
            passwort = bool(el.CachedIsPassword)
            aid = el.CachedAutomationId or ""
            geschuetzt = False
            wert = ""
            zustand = ""
            if typ in ("Eingabefeld", "Auswahlliste"):
                geschuetzt = feld_geschuetzt(name, aid, passwort)
                if sichtbar and not geschuetzt and werte[0] < MAX_FELD_WERTE:
                    werte[0] += 1
                    roh = self._wert(uia, el)
                    if feld_geschuetzt(name, aid, passwort, roh):
                        geschuetzt = True
                    else:
                        wert = roh
            elif typ == "Kontrollkästchen":
                zustand = {0: "aus", 1: "an", 2: "unbestimmt"}.get(int(el.GetCachedPropertyValue(uia.UIA_ToggleToggleStatePropertyId) or 0), "")
            elif typ == "Optionsfeld":
                zustand = "ausgewählt" if el.GetCachedPropertyValue(uia.UIA_SelectionItemIsSelectedPropertyId) else ""
            oben = hoehe = 0
            if typ in INLINE_TYPEN:
                try:
                    _, y, _, h = el.GetCachedPropertyValue(uia.UIA_BoundingRectanglePropertyId)
                    oben, hoehe = int(y), int(h)
                except Exception:  # noqa: BLE001 (ohne Lage bleibt es bei der Reihenfolge im Baum)
                    pass
            return SeitenKnoten(tiefe=tiefe, typ=typ, name=name, ebene=ebene, sichtbar=sichtbar, aktiv=aktiv,
                                geschuetzt=geschuetzt, wert=wert, zustand=zustand, oben=oben, hoehe=hoehe)
        except Exception:  # noqa: BLE001 (Element verschwunden)
            return None

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

