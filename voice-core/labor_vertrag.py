"""Festlegungen des Vertrags Kern zu Test-Jira-Dienst (Version 1, docs/labor-dienst-vertrag.md), gemeinsam für Werkzeuge,
Freigabe, Dienstclient und Mail.

Wertebereiche, Grenzen, Textnormierung (Unicode NFC, keine Steuer- und Formatzeichen), das Längenmaß der Freigabekarte
und die festen Sätze je Fehlercode. Reine Funktionen und Konstanten: kein Netz, keine Datenbank, kein Import von bot.py.

Der Kern normiert Text, der Dienst lehnt Abweichendes ab (Vertrag 2.3). Was auf der Freigabekarte steht, ist genau der
normierte Wert, der später ausgeführt wird.
"""

from __future__ import annotations

import os
import re
import unicodedata

PROJEKT = "LAB"
TYPEN = ("bug", "aufgabe", "story")
PRIORITAETEN = ("niedrig", "mittel", "hoch", "hoechste")
STATUS = ("zu_erledigen", "in_arbeit", "erledigt")
TYP_ANZEIGE = {"bug": "Bug", "aufgabe": "Aufgabe", "story": "Story"}
PRIORITAET_ANZEIGE = {"niedrig": "Niedrig", "mittel": "Mittel", "hoch": "Hoch", "hoechste": "Höchste"}
STATUS_ANZEIGE = {"zu_erledigen": "Zu erledigen", "in_arbeit": "In Arbeit", "erledigt": "Erledigt"}

TITEL_MIN, TITEL_MAX = 3, 120
BESCHREIBUNG_MAX = 1500
KOMMENTAR_MIN, KOMMENTAR_MAX = 1, 500
MAIL_BETREFF_MIN, MAIL_BETREFF_MAX = 1, 120
MAIL_TEXT_MIN, MAIL_TEXT_MAX = 1, 1500
# Alle Zeilen einer Freigabekarte zusammen (Befund 6): die Oberfläche kürzt ein Feld bei 2000 UTF-16-Einheiten (ui/src/lib/nachrichten.ts)
# und zeigt dann nicht mehr alles, was ausgeführt wird. Darunter bleibt Luft für Kennungen und Zusätze.
KARTE_MAX = 1900

FRIST_SECS = 120
MAX_OFFEN = 3
MAX_SCHREIBEN = 25

# Vorgabe der Adresse: HTTPS, Vertrag Abschnitt 2. Der Name der Attrappe (nur Tests) endet auf .invalid und löst nie auf.
ATTRAPPE_HOST = "labor-attrappe.invalid"

NUMMER_RE = re.compile(r"LAB-[1-9][0-9]{0,3}")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# Feste Sätze für den Teilnehmer, nie der Text des Dienstes (Vertrag Abschnitt 4)
FEHLER_SAETZE = {
    "token_ungueltig": "Die Verbindung zum Test-Jira ist nicht richtig eingerichtet.",
    "platz_unbekannt": "Dieser Platz ist im Test-Jira nicht bekannt.",
    "ticket_unbekannt": "Dieses Ticket gibt es im Test-Jira nicht.",
    "durchlauf_beendet": "Der Durchlauf ist beendet, es wurde nichts geändert.",
    "ticket_veraendert": "Das Ticket hat sich inzwischen geändert, es wurde nichts geändert.",
    "eingabe_ungueltig": "Das Test-Jira hat die Eingabe nicht angenommen.",
    "zu_gross": "Die Eingabe war zu groß für das Test-Jira.",
    "quote_erreicht": "Das Limit für diesen Platz im Test-Jira ist erreicht.",
    "nicht_erlaubt": "Diese Aktion gibt es im Test-Jira nicht.",
    "dienst_fehler": "Das Test-Jira hatte einen Fehler.",
    "nicht_erreichbar": "Das Test-Jira ist gerade nicht erreichbar.",
    "zeitueberschreitung": "Das Test-Jira hat zu lange nicht geantwortet.",
    "antwort_ungueltig": "Das Test-Jira hat eine unerwartete Antwort gegeben.",
}

# Zeichenklassen, die der Kern nie durchlässt: Steuer-, Format-, Ersatz-, private, nicht zugewiesene Zeichen, Zeilen- und Absatztrenner
_VERBOTEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def nummer_gueltig(nummer: object) -> bool:
    """Streng LAB-<1 bis 4 Ziffern> ohne führende Null (Vertrag 2.3), nur ASCII, ohne Leerraum, ohne Anpassung der Schreibweise."""
    return isinstance(nummer, str) and NUMMER_RE.fullmatch(nummer) is not None


def normiere(text: object, *, zeilen: bool) -> str | None:
    """Text für Felder und Karten: NFC, Steuer-, Format- und unsichtbare Zeichen werden zu Leerzeichen, Umbrüche sind \\n.
    zeilen=False ergibt eine Zeile (Leerraum zusammengefasst), zeilen=True behält Umbrüche und Einrückung, kürzt aber rechts
    und streift leere Zeilen am Anfang und Ende ab. Kein Text (kein str): None."""
    if not isinstance(text, str):
        return None
    t = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    ausgabe: list[str] = []
    for zeichen in t:
        if zeichen == "\n":
            ausgabe.append("\n" if zeilen else " ")
        elif unicodedata.category(zeichen) in _VERBOTEN or (unicodedata.category(zeichen) == "Zs" and zeichen != " "):
            ausgabe.append(" ")
        else:
            ausgabe.append(zeichen)
    t = "".join(ausgabe)
    if not zeilen:
        t = " ".join(t.split())
    else:
        t = "\n".join(zeile.rstrip() for zeile in t.split("\n")).strip("\n")
    return unicodedata.normalize("NFC", t)


def utf16_len(text: str) -> int:
    """Länge in UTF-16-Einheiten, wie JavaScript `length`: so rechnet die Oberfläche beim Kürzen (nachrichten.ts)."""
    return len(text.encode("utf-16-le")) // 2


def env_int(name: str, standard: int, kleinster: int, groesster: int) -> int:
    """Ganze Zahl aus der Umgebung, bei fehlendem oder ungültigem Wert (oder außerhalb der Grenzen) der Standard."""
    roh = (os.getenv(name) or "").strip()
    if not roh or not roh.isascii() or not roh.isdigit():
        return standard
    wert = int(roh)
    return wert if kleinster <= wert <= groesster else standard
