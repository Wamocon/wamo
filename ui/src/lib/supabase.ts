"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { angemeldetBleiben, authSpeicher, type Speicher } from "@/lib/anmeldung";

/** Browser-Client nur fuer die Anmeldung: oeffentlicher Schluessel, nie der service_role-Key. */
let client: SupabaseClient | null = null;

function speicher(name: "localStorage" | "sessionStorage"): Speicher | null {
  try {
    return window[name];
  } catch {
    return null; // gesperrter Speicher: Sitzung lebt dann nur im Speicher der Seite
  }
}

export function getSupabase(): SupabaseClient | null {
  if (typeof window === "undefined") return null;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) return null;
  // "Angemeldet bleiben" (lib/anmeldung.ts): localStorage, ohne Haken sessionStorage
  client ??= createClient(url, key, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      storage: authSpeicher(speicher("localStorage"), speicher("sessionStorage"), angemeldetBleiben),
    },
  });
  return client;
}

/** Access-Token fuer den Voice-Core (getSession erneuert es, wenn es abgelaufen ist). */
export async function accessToken(): Promise<string | null> {
  const supabase = getSupabase();
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}
