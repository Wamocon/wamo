"""Ausgangsstand (Seed) je Platz, Vertrag Abschnitt 6. Datenpflege: darf nach dem Freeze geändert werden.

LAB-11 erledigt, LAB-12 in Arbeit, LAB-20 zu erledigen (damit die Verwechslung von zwölf und zwanzig bei einer Sprachaufgabe sichtbar
wird), drei weitere Tickets, der Zähler für neue Tickets beginnt bei 30 (regeln.ZAEHLER_START). Der Dienst setzt den Seed bei jedem neuen
Durchlauf eines Platzes zurück. Je Eintrag: Nummer, Typ, Priorität, Status, Titel, Beschreibung.
"""
from __future__ import annotations

SEED: tuple[tuple[int, str, str, str, str, str], ...] = (
    (11, "bug", "niedrig", "erledigt", "Tippfehler auf der Startseite", "Im Willkommenstext fehlt ein Buchstabe."),
    (12, "bug", "hoch", "in_arbeit", "Login schlägt bei falschem Passwort fehl", "Statt einer Meldung bleibt die Seite leer."),
    (14, "aufgabe", "mittel", "zu_erledigen", "Testdaten für den Regressionslauf anlegen", ""),
    (17, "aufgabe", "niedrig", "zu_erledigen", "Dokumentation der Rollen prüfen", "Seite Rollen und Rechte lesen."),
    (20, "aufgabe", "niedrig", "zu_erledigen", "Hilfetext auf der Startseite ergänzen", ""),
    (25, "story", "mittel", "zu_erledigen", "Export der Ergebnisse als Tabelle", "Als Anwender will ich Ergebnisse exportieren."),
)
