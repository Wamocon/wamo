"use client";

import { alsStart, alsUebersicht, endeMeldung, startFehler, type FehlerBehandlung, type Start, type Uebersicht } from "@/lib/fernzugriff";
import { KernFehler, kernJson } from "@/lib/kernApi";

/**
 * Aufrufe des Fernzugriffs (voice-core/rd_api.py). Der frische Code des zweiten Faktors wird wie in der Geräteverwaltung bei
 * Supabase bestätigt (geraeteVerwaltungApi.codeBestaetigen); danach trägt das Token den frischen Zeitstempel, den der Kern prüft.
 * Der Code selbst geht nur an Supabase und wird nirgends gespeichert. Zugangsdaten gibt es hier nicht: Der Kern liefert nur die
 * Adresse der Gateway-Seite mit dem Sitzungstoken.
 */

export async function zieleLaden(): Promise<Uebersicht> {
  const u = alsUebersicht(await kernJson("/rd/ziele"));
  if (!u) throw new KernFehler(502, "Unerwartete Antwort des Kerns.");
  return u;
}

export type StartErgebnis = { ok: true; start: Start } | ({ ok: false } & FehlerBehandlung);

/** Sitzung starten. Wirft nie: Fehler kommen als Behandlung (Text, Dialog offen lassen, Liste neu laden). */
export async function starten(ziel: string, herkunft: string): Promise<StartErgebnis> {
  try {
    const antwort = alsStart(await kernJson("/rd/start", "POST", { ziel }), herkunft);
    if (antwort) return { ok: true, start: antwort };
    return { ok: false, ...startFehler({ status: 502, text: "Unerwartete Antwort des Kerns." }) };
  } catch (e) {
    if (e instanceof KernFehler) return { ok: false, ...startFehler({ status: e.status, text: e.message, code: e.code }) };
    return { ok: false, ...startFehler({ status: 0, text: "Kern nicht erreichbar." }) };
  }
}

export type EndeErgebnis = { ok: true; text: string } | { ok: false; text: string; neuLaden: boolean };

/** Eigene Sitzung beenden (kein Code nötig: Beenden macht nichts auf dem Ziel auf). */
export async function beenden(sitzung: string, anzeige: string): Promise<EndeErgebnis> {
  try {
    const antwort = (await kernJson("/rd/ende", "POST", { sitzung })) as { ok?: unknown; gateway_bestaetigt?: unknown } | null;
    if (antwort?.ok === true) return { ok: true, text: endeMeldung(anzeige, antwort.gateway_bestaetigt !== false) };
    return { ok: false, text: "Unerwartete Antwort des Kerns. Bitte die Liste prüfen.", neuLaden: true };
  } catch (e) {
    if (e instanceof KernFehler) {
      return { ok: false, text: e.message, neuLaden: e.status === 0 || e.status === 404 || e.status >= 500 };
    }
    return { ok: false, text: "Kern nicht erreichbar. Bitte die Liste prüfen.", neuLaden: true };
  }
}
