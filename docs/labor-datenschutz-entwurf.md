# Testlabor 28.11.2026: Datenschutzhinweis und Einwilligung (Entwurf, Version 1)

**Stand:** Entwurf vom 12.11.2026, geschrieben von WAMO. **Kein geprüfter Rechtstext.** Eine verantwortliche Person muss ihn lesen, anpassen und freigeben. Erst danach darf er Besuchern gezeigt werden.
Was in `[eckigen Klammern]` steht, muss diese Person ausfüllen oder entscheiden.

**Wofür er gebraucht wird.** Der Betreuer erfasst im Dienst des Labors die Einwilligung mit der **Versionsnummer** dieses Textes (Betreuer-Schnittstelle `neuer-durchlauf`). Der Kern von WAMO startet einen Platz nur, wenn diese Version mindestens
`WAMO_LABOR_EINWILLIGUNG_MIN` ist (Standard im Plan: 1). Ändert sich der Text inhaltlich, steigt die Versionsnummer, und die Einwilligung muss neu erfasst werden.

## 1. Text für die Besucher (Aushang oder Bildschirm am Platz)

**Mitmachen am WAMO-Testlabor, bitte kurz lesen**

Du testest heute den Sprachassistenten WAMO der WAMOCON GmbH. Du sprichst mit ihm, und er legt für dich Testtickets in einem Übungs-Jira an. Damit das funktioniert und wir die Veranstaltung auswerten können, verarbeiten wir folgende Daten:

- **Was du sagst und tippst.** Deine Sprache wird in Text umgewandelt, WAMO antwortet mit Text und Stimme. Dafür gehen Sprache und Text an unsere Dienstleister für Spracherkennung und Sprachausgabe (Soniox) und für das Sprachmodell (Anthropic). Die Dienstleister verarbeiten sie nach ihren eigenen Regeln. [Verantwortliche Person: Verträge zur Auftragsverarbeitung und Standort der Verarbeitung nennen.]
- **Was gespeichert wird.** Der Gesprächstext und die Übungstickets deines Platzes werden gespeichert. Es gibt **keine Tonaufnahme** bei uns. Du sollst keine echten persönlichen Daten nennen, nutze erfundene Namen.
- **Wie lange.** Wir löschen alle Daten dieses Labors spätestens am **11.12.2026**. Das ist 14 Tage nach der Veranstaltung.
- **Dein Recht.** Du kannst jederzeit verlangen, dass wir deine Daten sofort löschen, und die Teilnahme beenden. Sprich dazu den Betreuer an deinem Platz an. [Verantwortliche Person: Kontaktstelle für Auskunft und Löschung nennen.]
- **Freiwillig.** Die Teilnahme ist freiwillig. Ohne deine Einwilligung kannst du dir WAMO am Betreuer-Platz ansehen. [Entscheidung: Gibt es diesen Weg, ja oder nein.]

Verantwortlich ist: [Name, Firma, Anschrift, E-Mail der verantwortlichen Person].

**Einwilligung (Version 1):** Ich habe den Hinweis gelesen und willige ein, dass meine Eingaben wie beschrieben verarbeitet werden. [Altersgrenze festlegen, zum Beispiel: Ich bin mindestens 18 Jahre alt, oder eine Aufsichtsperson hat zugestimmt.]

## 2. Was technisch dahintersteht (für die verantwortliche Person)

| Ort | Was landet dort | Löschweg |
| --- | --- | --- |
| Datenbank des Kerns (Tabellen `sessions`, `turns`, `actions`, `canvas_items`, `vorgaenge`) | Gesprächstext, Freigaben und deren Parameter, Testbericht, Entwurfstexte | `labor_loeschen` über `scripts/wamo_labor.py loeschen` |
| Dienst des Labors (Übungs-Jira) | Tickets, Kommentare, Einwilligungsvermerk (Version und Zeit) je Platz und Durchlauf | Betreuer-Funktion "Daten löschen", Aufbewahrung höchstens bis 11.12.2026 |
| Journal des Kerns und des Dienstes | Nur Metadaten (Zeit, Platz, Art), kein Gesprächstext (Stufe INFO) | Rotation des Journals, spätestens bis 11.12.2026 |
| Soniox (Spracherkennung und Sprachausgabe), Anthropic (Sprachmodell) | Sprache und Text während des Gesprächs | nach den Regeln der Anbieter [prüfen: Aufbewahrung bei den Anbietern] |

Nicht gespeichert werden: Tonaufnahmen, Stimmprofile, Gedächtniseinträge (für Laborprofile gesperrt).

## 3. Offene Punkte für die verantwortliche Person

1. Name und Kontakt der verantwortlichen Person und der Stelle für Auskunft und Löschung.
2. Altersgrenze und Umgang mit Minderjährigen.
3. Rechtsgrundlage und Prüfung der Auftragsverarbeitung bei Soniox und Anthropic (Ort der Verarbeitung, Übermittlung in Drittländer).
4. Gibt es einen Weg ohne Einwilligung (nur zuschauen)?
5. Form: Aushang, Bildschirm am Platz oder Papier zum Unterschreiben. Der Betreuer erfasst die Version erst, wenn die Person zugestimmt hat.
