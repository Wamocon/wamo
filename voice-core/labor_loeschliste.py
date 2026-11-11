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
docs/testlabor-wamo.md, Abschnitt 3.1, und sind keine Sache dieser Datei. Die Spuren in GoTrue (Schema auth: Anmeldeprotokoll und
Anmeldesitzungen mit IP-Adresse) liegen in der Datenbank, aber nicht im Schema von WAMO: Sie stehen unten in AUSSERHALB und
verschwinden mit den Konten am 30.11.2026.

Nur Daten, kein Zugriff auf die Datenbank. Bezeichner stehen in ASCII.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Eine Sitzung eines Laborprofils gilt als beendet, wenn ended_at gesetzt ist oder sie älter ist als die Obergrenze der
#: Sitzungsdauer plus Puffer. Die Funktion labor_loeschen lehnt eine jüngere offene Sitzung ab (vor dem Löschen und noch einmal
#: danach). Kopplung an den Kern: bot.py begrenzt eine Sitzung auf WAMO_MAX_SITZUNG_MIN (Standard 120, per Umgebung änderbar, auch
#: in .env.example). Wer die Umgebungsvariable über den Standard hinaus erhöht, muss diese Konstante UND das Intervall in
#: supabase/migrations/0018_labor_profile.sql mitziehen (beide Stellen müssen übereinstimmen). Der Test
#: test_migration_0018.py::test_sitzungsgrenze_der_loeschfunktion_liegt_ueber_dem_standard_des_kerns stellt sicher, dass der Wert mehr
#: als 10 Minuten über dem Standard liegt.
SITZUNG_MAX_MINUTEN = 135

#: Sperrfrist nach jeder Änderung des Profils (profiles.updated_at, die Funktion lehnt in dieser Zeit mit SQLSTATE 55000 ab). Nach
#: labor_aktiv = false kennt der Kern das Profil noch bis zu PROFILE_HIT_SECS (auth_gate.py, 60 s) aus dem Zwischenspeicher und
#: prüft laufende Sitzungen alle PROFILE_RECHECK_SECS (bot.py, 60 s): rund 2 Minuten, in denen noch eine Sitzung beginnen kann.
#: Das Intervall in 0018_labor_profile.sql muss mit dieser Konstante übereinstimmen; der Test
#: test_sperrfrist_ist_laenger_als_zwischenspeicher_plus_pruefintervall_des_kerns prüft es gegen den Kern.
SPERRFRIST_MINUTEN = 3


@dataclass(frozen=True)
class Ort:
    """Eine Tabelle, die labor_loeschen leert."""

    tabelle: str
    besitzer: tuple[str, ...]  # die Zeile gehört dem Profil, wenn EINE dieser Spalten seine Kennung trägt
    zeit: str  # Spalte für das Zeitfenster eines Durchlaufs (p_ab, p_bis), immer not null
    inhalt: str  # was dort landet, für Menschen
    sitzung: str | None = None  # Spalte mit Verweis auf sessions: Zeilen gelöschter Sitzungen gehen im Fenster mit
    turn: str | None = None  # Spalte mit Verweis auf turns (ohne eigenen Sitzungsbezug): Zeilen zu Turns gelöschter Sitzungen gehen mit


#: Reihenfolge der Löschung: abhängige Zeilen zuerst, sessions zuletzt.
LOESCHLISTE: tuple[Ort, ...] = (
    Ort("feedback", ("user_id",), "created_at", "Rückmeldungen zu Antworten und Aktionen", turn="turn_id"),
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
    "model_access": "Konfiguration, kein Gesprächstext, kann aber einen verschlüsselten Claude-Schlüssel tragen. Für Laborprofile gesperrt: "
                    "labor.py nennt weder die Client-Nachricht modellzugang noch den Pfad /wamo/api/modellzugang in der Erlaubnisliste, "
                    "es entsteht dort keine Zeile. Eine vorhandene Zeile fällt mit dem Profil weg (on delete cascade), wenn die "
                    "Verwaltung die Konten am 30.11.2026 löscht (Plan Q11).",
    "release_anfragen": "Protokoll der Selbständerung, nur für Admins. Ein Laborprofil kommt dort nie vor; on delete restrict "
                        "würde das Löschen des Profils laut stoppen.",
    "release_freigaben": "Protokoll der Selbständerung, nur für Admins, nur anfügbar (Trigger). Gleiche Begründung.",
    "release_ereignisse": "Hash-Kette des Release-Protokolls, nur anfügbar. Gleiche Begründung.",
    "rd_sitzungen": "Protokoll des Remote Desktop, für Laborprofile gesperrt; on delete restrict stoppt laut.",
}

#: Spuren außerhalb des Schemas von WAMO (GoTrue, Schema auth): Die Funktion labor_loeschen kann und soll sie nicht anfassen. Sie
#: gehören zum Konto und verschwinden mit dem Löschen der Laborkonten am 30.11.2026 (Plan Q11). Bis dahin stehen sie hier mit Grund.
AUSSERHALB: dict[str, str] = {
    "auth.audit_log_entries": "Anmeldeprotokoll von GoTrue mit IP-Adresse und E-Mail der Laborkonten (payload). Gehört nicht zum Schema von "
                              "WAMO und hat keinen Fremdschlüssel auf das Konto: Die Verwaltung löscht die Einträge der Laborkonten "
                              "gezielt zusammen mit den Konten am 30.11.2026 (Plan Q11).",
    "auth.sessions": "Anmeldesitzungen von GoTrue mit IP-Adresse und User-Agent. Sie fallen mit dem Konto weg (on delete cascade), wenn "
                     "die Verwaltung die Laborkonten am 30.11.2026 löscht (Plan Q11).",
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
