// Version des Datenschutzhinweises (docs/labor-datenschutz-entwurf.md, Abschnitt 1) und der Satz, den der Betreuer bestätigt. Die Betreuer-Seite sendet
// diese Version als einwilligung_version, die Seite /datenschutz zeigt denselben Text. Ändert sich der Text inhaltlich, steigt die Nummer hier, im Dokument
// und in der Seite (ein Test hält alle drei zusammen).

import { bestaetigungOk } from "./betreuer-logik.js";

export const DATENSCHUTZ_VERSION = 1;
export const BESTAETIGUNGSTEXT = `Der Besucher hat den Datenschutzhinweis (Version ${DATENSCHUTZ_VERSION}) gelesen und bestätigt.`;

// Der Knopf „Ausführen“ im Dialog „Neuer Durchlauf“ wird nur frei, wenn der Haken gesetzt ist (genau true, kein anderer Wert) UND die Platznummer getippt wurde.
export const neuerDurchlaufFreigabe = (bestaetigt, eingabe, platz) => bestaetigt === true && bestaetigungOk(eingabe, platz);

// Hat der Dienst schon eine höhere Version erfasst, ist diese Seite veraltet: Die Version darf beim Dienst nur wachsen, ein Senden mit der kleineren würde abgelehnt.
export const versionVeraltet = (hoechste) => Number.isInteger(hoechste) && hoechste > DATENSCHUTZ_VERSION;

// Der Körper der Anfrage: genau die Version der Konstante, nie ein getippter Wert.
export const einwilligungKoerper = () => ({ einwilligung_version: DATENSCHUTZ_VERSION });
