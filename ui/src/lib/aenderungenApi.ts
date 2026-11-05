"use client";

import { alsStatus, type Status } from "@/lib/aenderungen";
import { KernFehler, kernBasis, kernJson } from "@/lib/kernApi";

/**
 * Abfrage der Karte "Änderungen" (voice-core/release_api.py: GET /wamo/api/release/status, nur Rolle admin). Liest nur: Die Karte ändert nichts am
 * Release-Dienst. Wirft nie; ein Fehler kommt als Text zurück, den die Karte anzeigt.
 */
export type StatusErgebnis = { ok: true; status: Status } | { ok: false; text: string };

export async function statusLaden(): Promise<StatusErgebnis> {
  try {
    const status = alsStatus(await kernJson("/release/status"));
    if (status) return { ok: true, status };
    return { ok: false, text: "Unerwartete Antwort des Kerns." };
  } catch (e) {
    if (e instanceof KernFehler) {
      if (e.status === 403) return { ok: false, text: "Die Änderungen sind nur für Admins mit frischem zweiten Faktor." };
      if (e.status === 429) return { ok: false, text: "Zu viele Abfragen, bitte einen Moment warten." };
      return { ok: false, text: e.message };
    }
    return { ok: false, text: "Kern nicht erreichbar." };
  }
}

// ------------------------------------------------------------------------------------------------ Freigabe (Schritt 1.5b)
export type FreigabeErgebnis = { ok: true; giltS: number | null } | { ok: false; code: string | null; status: number };

const FREIGABE_ZEITLIMIT_MS = 25_000; // Caddy wartet höchstens 20 s auf den Freigabe-Dienst
const ID_HEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HEX40 = /^[0-9a-f]{40}$/;

/**
 * Gibt die Änderung frei: POST /wamo/release/<id>/freigeben beim Freigabe-Dienst des Ankers (über Caddy auf dem Port des Kerns). Der Rumpf enthält
 * genau Commit und Baum, die die Karte gezeigt hat; das Token ist das frische nach der Eingabe des Codes. Kein Cookie, keine Weiterleitung, kein
 * automatischer zweiter Versuch (ob eine Freigabe bei einem Netzfehler ankam, weiß nur der Dienst). Wirft nie.
 */
export async function freigebenSenden(id: string, commit: string, baum: string, token: string): Promise<FreigabeErgebnis> {
  if (!ID_HEX.test(id) || !HEX40.test(commit) || !HEX40.test(baum)) return { ok: false, code: "angaben", status: 400 };
  try {
    const antwort = await fetch(`${kernBasis()}/wamo/release/${id}/freigeben`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ commit_id: commit, tree_id: baum }),
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(FREIGABE_ZEITLIMIT_MS),
    });
    let daten: unknown = null;
    try {
      daten = await antwort.json();
    } catch {
      daten = null;
    }
    const d = typeof daten === "object" && daten !== null ? (daten as Record<string, unknown>) : {};
    if (antwort.ok && d.ok === true) {
      return { ok: true, giltS: typeof d.gilt_s === "number" && Number.isFinite(d.gilt_s) ? d.gilt_s : null };
    }
    return { ok: false, code: typeof d.grund === "string" && /^[a-z_0-9]{1,40}$/.test(d.grund) ? d.grund : null, status: antwort.status };
  } catch {
    return { ok: false, code: null, status: 0 };
  }
}
