"use client";

import { accessToken } from "@/lib/supabase";
import { betriebsartAktuell, kernUrl } from "@/lib/betriebsart";

/**
 * Ablage je Person aus dem Gerätedienst (Scans und vom NAS abgerufene Dateien), über den Kern:
 *   GET /wamo/api/scans, GET /wamo/api/scans/{id}, DELETE /wamo/api/scans/{id}
 * Nur in der Betriebsart Büro (der Gerätedienst läuft auf Sokrates-2). Jede Anfrage trägt das Access-Token; Inhalte
 * vom Kern werden geprüft und nur als Text angezeigt.
 */

export type AblageEintrag = {
  id: string;
  art: "scan" | "nas";
  name: string;
  mime: string;
  groesse: number;
  erstellt: string;
  quelle: string;
};

const ID = /^[0-9a-f]{32}$/;

function pruefe(roh: unknown): AblageEintrag | null {
  if (!roh || typeof roh !== "object") return null;
  const e = roh as Record<string, unknown>;
  if (typeof e.id !== "string" || !ID.test(e.id) || (e.art !== "scan" && e.art !== "nas")) return null;
  return {
    id: e.id,
    art: e.art,
    name: typeof e.name === "string" ? e.name.slice(0, 80) : "Datei",
    mime: typeof e.mime === "string" ? e.mime.slice(0, 60) : "application/octet-stream",
    groesse: typeof e.groesse === "number" && Number.isFinite(e.groesse) ? e.groesse : 0,
    erstellt: typeof e.erstellt === "string" ? e.erstellt.slice(0, 30) : "",
    quelle: typeof e.quelle === "string" ? e.quelle.slice(0, 30) : "",
  };
}

async function anfrage(pfad: string, init: RequestInit = {}): Promise<Response> {
  const basis = kernUrl(betriebsartAktuell());
  const token = await accessToken();
  if (!basis || !token) throw new Error("Nicht angemeldet");
  const antwort = await fetch(`${basis}/wamo/api${pfad}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
  });
  if (antwort.status === 503) throw new Error("Scans gibt es nur im Büro (Gerätedienst auf Sokrates-2).");
  if (!antwort.ok) throw new Error(`Kern antwortet mit ${antwort.status}`);
  return antwort;
}

export async function ablageLaden(): Promise<AblageEintrag[]> {
  const daten: unknown = await (await anfrage("/scans")).json();
  const roh = daten && typeof daten === "object" ? (daten as { eintraege?: unknown }).eintraege : null;
  return Array.isArray(roh) ? roh.map(pruefe).filter((e): e is AblageEintrag => e !== null) : [];
}

/** Lädt eine Datei und bietet sie zum Speichern an (Download über eine Objekt-URL, sofort wieder freigegeben). */
export async function ablageHerunterladen(e: AblageEintrag): Promise<void> {
  if (!ID.test(e.id)) return;
  const blob = await (await anfrage(`/scans/${e.id}`)).blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = e.name || "datei";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}

export async function ablageLoeschen(e: AblageEintrag): Promise<boolean> {
  if (!ID.test(e.id)) return false;
  const daten: unknown = await (await anfrage(`/scans/${e.id}`, { method: "DELETE" })).json();
  return Boolean(daten && typeof daten === "object" && (daten as { ok?: unknown }).ok === true);
}

export function groesseText(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
