# Testlabor 28.11.2026: Datenschutzhinweis und Einwilligung (Fassung 1)

**Stand:** 13.11.2026. Der Text folgt der Entscheidung von Niko vom 13.11.2026: Jeder Besucher bekommt beim Anmelden den Datenschutzhinweis und bestätigt ihn. Die Gespräche werden nur zu Trainingszwecken bei WAMOCON verwendet und nicht weitergegeben. Die Angaben zur Firma, zum Kontakt, zu den Rechten und zur Aufsicht stammen aus der Datenschutzerklärung auf wamocon.com (`/datenschutz`, Stand 5. November 2026, die allgemeine Erklärung gilt daneben weiter). Kein geprüfter Rechtstext, die kurze Prüfung durch eine verantwortliche Person bleibt sinnvoll (Abschnitt 3).

**Wofür er gebraucht wird.** Der Betreuer zeigt dem Besucher den Hinweis (Abschnitt 1) und erfasst die Zustimmung mit der **Versionsnummer** dieses Textes in der Betreuer-Seite des Dienstes (Funktion `neuer-durchlauf`, mit Bestätigungsfeld "Der Besucher hat den Hinweis gelesen und bestätigt"). Der Kern von WAMO startet einen Platz nur, wenn diese Version mindestens `WAMO_LABOR_EINWILLIGUNG_MIN` ist (jetzt 1). Ändert sich der Text inhaltlich, steigt die Versionsnummer, und die Einwilligung muss neu erfasst werden. Dieser Text hat die **Version 1**.

## 1. Text für die Besucher (Aushang, Bildschirm oder Betreuer-Seite)

**Mitmachen am WAMO-Testlabor, bitte kurz lesen und bestätigen**

Du testest heute den Sprachassistenten WAMO der WAMOCON GmbH. Du sprichst mit ihm, und er legt für dich Testtickets in einem Übungs-Jira an. Damit das funktioniert und wir die Veranstaltung auswerten können, verarbeiten wir folgende Daten:

- **Was du sagst und tippst.** Deine Sprache wird in Text umgewandelt, WAMO antwortet mit Text und Stimme. Dafür verarbeiten unsere Dienstleister für Spracherkennung und Sprachausgabe (Soniox) und für das Sprachmodell (Anthropic) deine Sprache und deinen Text in unserem Auftrag, nur für das Gespräch und nur zu diesem Zweck.
- **Was wir speichern.** Den Gesprächstext und die Übungstickets deines Platzes speichern wir auf eigenen Servern von WAMOCON. Es gibt **keine Tonaufnahme**. Bitte nenne keine echten persönlichen Daten, nutze erfundene Namen.
- **Wofür.** Wir verwenden die Gespräche nur zu Trainingszwecken bei WAMOCON, also zur Auswertung des Testlabors und zur Verbesserung von WAMO. Wir geben sie nicht an Dritte weiter.
- **Wie lange.** Wir löschen alle Daten dieses Testlabors spätestens am **11.12.2026**. Das ist 14 Tage nach der Veranstaltung.
- **Deine Rechte.** Du kannst jederzeit Auskunft verlangen, Daten berichtigen oder löschen lassen, die Verarbeitung einschränken, widersprechen, deine Daten in einem gängigen Format erhalten und deine Einwilligung mit Wirkung für die Zukunft widerrufen. Sprich dazu den Betreuer an deinem Platz an, auf Wunsch löschen wir deine Daten sofort. Du kannst dich außerdem bei einer Datenschutzaufsichtsbehörde beschweren, zuständig ist der Hessische Beauftragte für Datenschutz und Informationsfreiheit (datenschutz.hessen.de).
- **Freiwillig.** Die Teilnahme ist freiwillig. Ohne deine Zustimmung kannst du dir WAMO am Betreuer-Platz ansehen.

**Verantwortlich:** WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn, Telefon +49 6196 5838311, E-Mail info@wamocon.com. Ein Datenschutzbeauftragter ist nicht bestellt. Die allgemeine Datenschutzerklärung findest du auf wamocon.com/datenschutz.

**Einwilligung (Version 1):** Ich habe den Hinweis gelesen und willige ein, dass meine Eingaben wie beschrieben verarbeitet, gespeichert und zu Trainingszwecken bei WAMOCON verwendet werden. Ich bin mindestens 16 Jahre alt. Bin ich jünger, hat eine Aufsichtsperson (zum Beispiel Eltern oder Lehrkraft) zugestimmt. Ich kann meine Einwilligung jederzeit widerrufen.

## 2. Was technisch dahintersteht (für die verantwortliche Person)

| Ort | Was landet dort | Löschweg |
| --- | --- | --- |
| Datenbank des Kerns (Tabellen `sessions`, `turns`, `actions`, `canvas_items`, `vorgaenge`), nur mit `WAMO_LABOR_TEXT_SPEICHERN=an` und gültiger Einwilligung | Gesprächstext, Freigaben und deren Parameter, Testbericht, Entwurfstexte, jeweils mit der Kennung des Laborprofils | `labor_loeschen` über `scripts/wamo_labor.py loeschen` |
| Dienst des Labors (Übungs-Jira) | Tickets, Kommentare, Einwilligungsvermerk (Version und Zeit) je Platz und Durchlauf | Betreuer-Funktion "Daten löschen", Aufbewahrung höchstens bis 11.12.2026, Löschlauf `wamo-labor-loeschlauf.sh` |
| Journal des Kerns und des Dienstes | Nur Metadaten (Zeit, Platz, Art), kein Gesprächstext (Stufe INFO) | Rotation des Journals, spätestens bis 11.12.2026 |
| Soniox (Spracherkennung und Sprachausgabe), Anthropic (Sprachmodell) | Sprache und Text während des Gesprächs, im Auftrag von WAMOCON | nach den Regeln der Anbieter (siehe Abschnitt 3, Punkt 1) |

Nicht gespeichert werden: Tonaufnahmen, Stimmprofile, Gedächtniseinträge (für Laborprofile gesperrt).

## 3. Offene Punkte für die verantwortliche Person

1. **Auftragsverarbeitung bei Soniox und Anthropic:** Verträge (Art. 28 DSGVO), Ort der Verarbeitung und Übermittlung in Drittländer prüfen und im Hinweis bei Bedarf ergänzen. Die Datenschutzerklärung von wamocon.com beschreibt für den Chatbot der Website ein Modell auf eigener Hardware, das gilt für WAMO nicht: WAMO nutzt die genannten Dienstleister.
2. **Trainingszwecke und Aufbewahrung:** Der Hinweis nennt die Löschfrist 11.12.2026. Sollen Gespräche darüber hinaus für Trainingszwecke bleiben, müssen Hinweis, Löschlauf (D10) und Einwilligungsversion angepasst werden (dann Version 2). Vorerst gilt: Auswertung und Training bis zum 11.12.2026, danach gelöscht.
3. **Altersangabe (entschieden von Niko am 13.11.2026):** Richtwert mindestens 16 Jahre, keine harte Grenze. Wer jünger ist, nimmt mit Zustimmung einer Aufsichtsperson teil (das entspricht Art. 8 DSGVO, in Deutschland gilt 16 für die Einwilligung). Der Text hat die Version 1 behalten, weil bis zu dieser Änderung kein Besucher eingewilligt hat (nur Probeläufe, deren Daten gelöscht sind). Ab dem ersten echten Besucher gilt: Jede inhaltliche Änderung erhöht die Versionsnummer.
4. **Weg ohne Einwilligung:** Der Hinweis nennt "WAMO am Betreuer-Platz ansehen". Das muss am Labortag so möglich sein.
5. **Form:** Der Betreuer zeigt den Text auf der Betreuer-Seite (Ansicht für den Besucher) oder als Aushang und erfasst die Zustimmung erst, wenn die Person sie gegeben hat.
