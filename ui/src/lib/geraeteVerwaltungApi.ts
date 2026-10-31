"use client";

import { aal2Frisch } from "@/lib/anmeldung";
import {
  alsGeraete,
  fehlerBehandlung,
  type Aktion,
  type Ergebnis,
  type FehlerBehandlung,
  type Geraet,
} from "@/lib/geraeteVerwaltung";
import { KernFehler, kernJson } from "@/lib/kernApi";
import { accessToken, getSupabase } from "@/lib/supabase";

/**
 * Aufrufe der Geräteverwaltung (voice-core/geraete_admin_api.py) und der frische Code des zweiten Faktors.
 *
 * Ändernde Aktionen nimmt der Kern nur an, wenn der Code der Authenticator-App höchstens zwei Minuten alt ist. Die
 * Oberfläche fragt ihn deshalb bei jeder Aktion im Bestätigungsdialog ab und bestätigt ihn bei Supabase
 * (mfa.challengeAndVerify); das neue Token trägt dann den frischen Zeitstempel im Claim amr. Der Code selbst geht nur an
 * Supabase und wird nirgends gespeichert.
 */

export async function geraeteLaden(): Promise<Geraet[]> {
  const daten = (await kernJson("/geraete/verwaltung")) as { geraete?: unknown } | null;
  return alsGeraete(daten?.geraete);
}

export type CodeErgebnis = { ok: true } | { ok: false; text: string };

/** Code des zweiten Faktors bei Supabase bestätigen. Danach liefert getSession ein Token mit frischem Zeitstempel. */
export async function codeBestaetigen(code: string): Promise<CodeErgebnis> {
  const supabase = getSupabase();
  if (!supabase) return { ok: false, text: "Die Anmeldung ist nicht eingerichtet." };
  try {
    const { data, error } = await supabase.auth.mfa.listFactors();
    if (error) return { ok: false, text: "Der zweite Faktor lässt sich gerade nicht abfragen. Bitte gleich noch einmal versuchen." };
    const faktor = data.totp[0];
    if (!faktor) return { ok: false, text: "Für dieses Konto ist kein Authenticator eingerichtet." };
    const { error: pruefFehler } = await supabase.auth.mfa.challengeAndVerify({ factorId: faktor.id, code });
    if (pruefFehler) {
      // Nur eine echte Ablehnung des Codes heißt "passt nicht"; Netz- und Serverfehler sind etwas anderes
      if (pruefFehler.status === 429) return { ok: false, text: "Zu viele Versuche. Bitte in ein paar Minuten noch einmal." };
      const abgelehnt = pruefFehler.code === "mfa_verification_failed" || pruefFehler.status === 400 || pruefFehler.status === 422;
      return {
        ok: false,
        text: abgelehnt
          ? "Der Code passt nicht. Nimm den aktuellen aus der App."
          : "Der zweite Faktor lässt sich gerade nicht abfragen. Bitte gleich noch einmal versuchen.",
      };
    }
    const token = await accessToken();
    if (!aal2Frisch(token, Date.now())) {
      return { ok: false, text: "Der Code wurde angenommen, ist aber noch nicht im Token angekommen. Bitte gleich noch einmal versuchen." };
    }
    return { ok: true };
  } catch {
    return { ok: false, text: "Der zweite Faktor lässt sich gerade nicht abfragen. Bitte gleich noch einmal versuchen." };
  }
}

export type AktionAnfrage = { kennung: string; aktion: Aktion; grund: string | null; fingerabdruck: string | null };

export type AktionErgebnis = { ok: true; ergebnis: Ergebnis } | ({ ok: false } & FehlerBehandlung);

/** Aktion senden. Wirft nie: Fehler kommen als Behandlung (Text, Dialog offen lassen, Liste neu laden). */
export async function aktionSenden(a: AktionAnfrage): Promise<AktionErgebnis> {
  try {
    const antwort = (await kernJson("/geraete/verwaltung/aktion", "POST", {
      kennung: a.kennung,
      aktion: a.aktion,
      ...(a.grund ? { grund: a.grund } : {}),
      ...(a.fingerabdruck ? { fingerabdruck: a.fingerabdruck } : {}),
    })) as { ok?: unknown; ergebnis?: unknown } | null;
    if (antwort?.ok === true && (antwort.ergebnis === "ok" || antwort.ergebnis === "schon")) {
      return { ok: true, ergebnis: antwort.ergebnis };
    }
    return { ok: false, ...fehlerBehandlung({ status: 502, text: "Unerwartete Antwort des Kerns." }) };
  } catch (e) {
    if (e instanceof KernFehler) return { ok: false, ...fehlerBehandlung({ status: e.status, text: e.message, code: e.code }) };
    return { ok: false, ...fehlerBehandlung({ status: 0, text: "Kern nicht erreichbar." }) };
  }
}
