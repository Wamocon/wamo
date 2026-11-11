"""Löschliste der Laborprofile (Testlabor 28.11.2026): jeder Ort in der Datenbank, an dem Text oder Spuren eines Laborprofils landen.

Die Liste ist die einzige Quelle für drei Dinge, die zusammenpassen müssen:
  * die SQL-Funktionen labor_loeschen und labor_zaehlen in supabase/migrations/0018_labor_profile.sql (sie löschen und
    zählen genau diese Tabellen, in dieser Reihenfolge),
  * die Verwaltung (labor_verwaltung.py), die das Ergebnis der Funktionen gegen diese Liste prüft,
  * den Test test_labor_loeschen.py, der die Liste gegen ALLE Migrationen auf Vollständigkeit prüft: Jede Tabelle mit einer
    Spalte, die auf profiles(user_id) oder auf auth.users(id) verweist, steht entweder hier in LOESCHLISTE oder mit
    Begründung in AUSNAHMEN, jeder Verweis, der nicht Besitz bedeutet, in FREMDE_VERWEISE. Kommt mit einer späteren Migration
    eine neue Tabelle mit Besitzerspalte dazu, wird dieser Test rot, bis jemand sie hier einträgt und die Funktion erweitert.

Orte außerhalb der Datenbank (Journal des Kerns, auftraege.log, Sicherungen, Dienst der Kollegen, Browser) stehen in
docs/testlabor-wamo.md, Abschnitt 3.1, und sind keine Sache dieser Datei.

Nur Daten, kein Zugriff auf die Datenbank. Bezeichner stehen in ASCII.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Eine Sitzung eines Laborprofils gilt als beendet, wenn ended_at gesetzt ist oder sie älter ist als die Obergrenze der
#: Sitzungsdauer (bot.py: höchstens 120 Minuten) plus Puffer. Die Funktion labor_loeschen lehnt eine jüngere offene Sitzung ab.
SITZUNG_MAX_MINUTEN = 130


@dataclass(frozen=True)
class Ort:
    """Eine Tabelle, die labor_loeschen leert."""

    tabelle: str
    besitzer: tuple[str, ...]  # die Zeile gehört dem Profil, wenn EINE dieser Spalten seine Kennung trägt
    zeit: str  # Spalte für das Zeitfenster eines Durchlaufs (p_ab, p_bis), immer not null
    inhalt: str  # was dort landet, für Menschen
    sitzung: str | None = None  # Spalte mit Verweis auf sessions: Zeilen gelöschter Sitzungen gehen im Fenster mit


#: Reihenfolge der Löschung: abhängige Zeilen zuerst, sessions zuletzt.
LOESCHLISTE: tuple[Ort, ...] = (
    Ort("feedback", ("user_id",), "created_at", "Rückmeldungen zu Antworten und Aktionen"),
    Ort("memories", ("user_id",), "created_at", "Gedächtnis (für Laborprofile gesperrt, wird nie geschrieben)",
        "source_session_id"),
    Ort("speakers", ("user_id",), "enrolled_at", "Stimmabdrücke (für Laborprofile gesperrt)"),
    Ort("teilung_events", ("besitzer", "empfaenger", "durch"), "at", "Protokoll geteilter Arbeit (für Laborprofile gesperrt)"),
    Ort("teilungen", ("besitzer", "empfaenger"), "erstellt_at", "geteilte Arbeit (für Laborprofile gesperrt)"),
    Ort("hinweise", ("empfaenger",), "erstellt_at", "Hinweise an das Profil"),
    Ort("canvas_items", ("user_id",), "created_at", "Canvas, Testbericht, Mailentwurf, Recherche-Ergebnis", "session_id"),
    Ort("actions", ("user_id",), "created_at", "Parameter, Grund und Ergebnis jeder Freigabe, Einwilligungsvermerk",
        "session_id"),
    Ort("turns", ("user_id",), "created_at", "Gesprächstext (content, meta)", "session_id"),
    Ort("vorgaenge", ("user_id",), "gestartet_at", "Titel und Stand von Aufgaben (für Laborsitzungen nicht geschrieben)",
        "session_id"),
    Ort("dateien", ("user_id",), "erstellt_at", "Dateien samt Inhalt (für Laborprofile gesperrt)"),
    Ort("device_events", ("durch",), "at", "Verlauf von Geräten, die das Profil geändert hat (Laborprofile haben keine)"),
    Ort("devices", ("user_id",), "created_at", "Geräte des Profils (Laborprofile haben keine)"),
    Ort("sessions", ("user_id",), "started_at", "eine Zeile je Sitzung, summary"),
)

#: Tabellen mit einer Spalte, die auf ein Profil oder ein Konto verweist, die labor_loeschen NICHT leert, mit Grund.
AUSNAHMEN: dict[str, str] = {
    "profiles": "Das Profil selbst: es bleibt bis zur Kontolöschung nach dem Labortag (Konten löschen, Sitzungen widerrufen).",
    "model_access": "Konfiguration, kein Gesprächstext. Der eigene Zugang des Schau-Profils fällt mit dem Profil weg "
                    "(on delete cascade), wenn die Konten nach dem Labortag gelöscht werden.",
    "release_anfragen": "Protokoll der Selbständerung, nur für Admins. Ein Laborprofil kommt dort nie vor; on delete restrict "
                        "würde das Löschen des Profils laut stoppen.",
    "release_freigaben": "Protokoll der Selbständerung, nur für Admins, nur anfügbar (Trigger). Gleiche Begründung.",
    "release_ereignisse": "Hash-Kette des Release-Protokolls, nur anfügbar. Gleiche Begründung.",
    "rd_sitzungen": "Protokoll des Remote Desktop, für Laborprofile gesperrt; on delete restrict stoppt laut.",
}

#: Verweise auf ein Profil in Tabellen der Löschliste, die kein Besitz sind. Die Funktion löscht dort nichts, weil sie sonst
#: Zeilen anderer Profile träfe. Die Datenbank setzt den Verweis beim Löschen des Profils auf null (on delete set null).
FREMDE_VERWEISE: dict[tuple[str, str], str] = {
    ("memories", "reviewed_by"): "Prüfer eines gemeinsamen Eintrags, ein Laborprofil prüft nie.",
    ("canvas_items", "bearbeitet_von"): "Bearbeiter einer Fassung, die einem anderen Profil gehört.",
    ("dateien", "hochgeladen_von"): "trägt das Kontingent beim Hochladen, die Datei gehört dem Besitzer.",
    ("devices", "freigegeben_von"): "Admin, der ein Gerät freigab.",
    ("devices", "geaendert_von"): "Admin, der den letzten Statuswechsel ausgelöst hat.",
    ("teilungen", "widerrufen_von"): "Beteiligter, der widerrief; die Zeile gehört Besitzer und Empfänger.",
}

#: Spaltennamen, die in einer Tabelle auf einen Besitzer hindeuten. Steht so eine Spalte ohne Verweis (Fremdschlüssel) in
#: einer neuen Tabelle, schlägt der Vollständigkeitstest an, damit sie nicht durchrutscht.
BESITZER_NAMEN: tuple[str, ...] = ("user_id", "profil_id", "besitzer", "empfaenger")


def tabellen() -> tuple[str, ...]:
    """Die Namen der Tabellen in der Reihenfolge der Löschung (so heißen auch die Schlüssel im Ergebnis der Funktionen)."""
    return tuple(o.tabelle for o in LOESCHLISTE)
