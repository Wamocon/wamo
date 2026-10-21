"use client";

import { betriebsartAktuell, kernUrl } from "@/lib/betriebsart";
import { accessToken } from "@/lib/supabase";

/**
 * Aufrufe an die Schnittstellen des Kerns unter /wamo/api (voice-core/wamo_api.py): Hinweise, geteilte Arbeit,
 * Dateien. Ziel ist der Kern der aktuellen Betriebsart (Büro oder Unterwegs, beide stehen in der CSP), angemeldet mit
 * dem Token der Sitzung. Keine Cookies, kein Cache. Fehler kommen als KernFehler mit dem Text des Kerns (der nennt
 * nie interne Details).
 */

export class KernFehler extends Error {
  constructor(
    readonly status: number,
    text: string,
  ) {
    super(text);
  }
}

const ZEITLIMIT_MS = 20_000;
const ZEITLIMIT_DATEI_MS = 120_000;

async function anfrage(pfad: string, init: RequestInit = {}, zeitlimit = ZEITLIMIT_MS): Promise<Response> {
  const basis = kernUrl(betriebsartAktuell());
  if (!basis) throw new KernFehler(0, "Kein Kern eingerichtet");
  const token = await accessToken();
  if (!token) throw new KernFehler(401, "Nicht angemeldet");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const steuerung = new AbortController();
  const uhr = setTimeout(() => steuerung.abort(), zeitlimit);
  try {
    const antwort = await fetch(`${basis}/wamo/api${pfad}`, {
      ...init,
      headers,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      signal: steuerung.signal,
    });
    if (!antwort.ok) {
      let text = `Fehler ${antwort.status}`;
      try {
        const daten: unknown = await antwort.json();
        const detail = (daten as { detail?: unknown })?.detail;
        if (typeof detail === "string" && detail) text = detail.slice(0, 200);
      } catch {
        // kein JSON: Standardtext
      }
      throw new KernFehler(antwort.status, text);
    }
    return antwort;
  } catch (e) {
    if (e instanceof KernFehler) throw e;
    throw new KernFehler(0, steuerung.signal.aborted ? "Zeitüberschreitung" : "Kern nicht erreichbar");
  } finally {
    clearTimeout(uhr);
  }
}

/** JSON lesen oder senden (body wird als JSON verschickt). */
export async function kernJson<T>(pfad: string, methode: "GET" | "POST" | "PUT" = "GET", body?: unknown): Promise<T> {
  const init: RequestInit = { method: methode };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const antwort = await anfrage(pfad, init);
  return (await antwort.json()) as T;
}

/** Datei hochladen (Rohdaten, Name im Header X-Dateiname). */
export async function kernDateiSenden<T>(pfad: string, datei: File): Promise<T> {
  const antwort = await anfrage(
    pfad,
    {
      method: "POST",
      body: datei,
      headers: { "Content-Type": "application/octet-stream", "X-Dateiname": encodeURIComponent(datei.name) },
    },
    ZEITLIMIT_DATEI_MS,
  );
  return (await antwort.json()) as T;
}

/** Datei herunterladen und als Download anbieten (immer als Datei, nie im Fenster geöffnet). */
export async function kernDateiHolen(pfad: string, name: string): Promise<void> {
  const antwort = await anfrage(pfad, {}, ZEITLIMIT_DATEI_MS);
  const blob = await antwort.blob();
  const url = URL.createObjectURL(new Blob([blob], { type: "application/octet-stream" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

// ---------- Typen der Antworten (voice-core/teilen.py, hinweise_api.py) ----------
export type Recht = "lesen" | "bearbeiten";
export type TeilArt = "canvas" | "auftrag" | "datei";

export type Teilung = {
  id: string;
  art: TeilArt;
  recht: Recht;
  titel: string;
  objekt: string;
  person: string;
  name: string;
  hinweis: string | null;
  erstellt_at: string | null;
};

export type Profil = { user_id: string; name: string };

export type DateiEintrag = {
  id: string;
  name: string;
  mime: string;
  groesse: number;
  herkunft: string;
  erstellt_at: string | null;
  fassungen: number;
};

export type Hinweis = {
  id: string;
  art: string;
  titel: string;
  text: string;
  erstellt_at: string;
  gelesen: boolean;
};

/** Größe lesbar: 1,2 MB, 340 KB. */
export function groesseText(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** UUID der Datenbank? (nur solche Canvas-Elemente lassen sich teilen) */
export function istUuid(wert: unknown): wert is string {
  return typeof wert === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(wert);
}
