/**
 * Fokus rund um die Einlernansicht (kommando/KernOderEinlernen.tsx): Wer die Ansicht öffnet, merkt sich hier seinen Knopf. Nach dem
 * Schließen geht der Fokus dorthin zurück, solange es den Knopf noch gibt und er nicht gesperrt ist, sonst auf die Überschrift des
 * Kerns (kommando/Kernbereich.tsx). Der Knopf in den Einstellungen verschwindet meist mit ihnen, dann gilt die Überschrift.
 */

/** Kennung der (nur für Screenreader sichtbaren) Überschrift des Kernbereichs */
export const KERN_TITEL_ID = "kz-kern-titel";

let oeffner: HTMLElement | null = null;

/** Den Knopf merken, der die Einlernansicht gleich öffnet. */
export function einlernOeffnerMerken(el: HTMLElement | null): void {
  oeffner = el;
}

/** Fokus nach dem Schließen der Einlernansicht setzen (einmalig: der gemerkte Knopf gilt danach nicht mehr). */
export function fokusNachEinlernen(): void {
  const el = oeffner;
  oeffner = null;
  const nutzbar = el !== null && el.isConnected && !(el instanceof HTMLButtonElement && el.disabled);
  (nutzbar ? el : document.getElementById(KERN_TITEL_ID))?.focus();
}
