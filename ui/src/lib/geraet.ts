/**
 * Einrichtung des Laptops bei der Anmeldung (nur Desktop-App, nur Betriebsart Büro).
 *
 * Der Ablauf ist eine reine, injizierbare Funktion: Brücke zur Desktop-App (window.wamoDesktop.geraet, desktop/geraet.js)
 * und Kern-Aufruf (kernJson aus lib/kernApi.ts) kommen von außen, damit sich alles ohne Browser und Netz prüfen lässt.
 *
 *   vorbereiten (Schlüssel) -> Herausforderung vom Kern -> signieren (Schlüssel) -> binden (Kern)
 *   -> bei "aktiv": aktivieren (Dateien und Tunnel auf dem PC)
 *
 * Alles, was vom Kern oder von der Brücke kommt, wird streng geprüft (Typ, Länge, Regex), bevor es weitergereicht wird.
 * Angezeigt wird nur Text (gekürzt, ohne Steuerzeichen). Das Gerät bleibt an die Anmeldung gebunden: user_id der
 * Antwort muss die der Sitzung sein. Der private Schlüssel verlässt die Desktop-App nie.
 */

/** Was die Desktop-App über preload.js anbietet (ältere Versionen kennen das nicht). Antworten sind ungeprüft. */
export type GeraetBruecke = {
  vorbereiten: () => Promise<unknown>;
  signieren: (anfrage: { nonce: string; userId: string }) => Promise<unknown>;
  aktivieren: (antwort: BindeAntwort) => Promise<unknown>;
  status: () => Promise<unknown>;
};

export type KernJson = (pfad: string, methode?: "GET" | "POST" | "PUT", body?: unknown) => Promise<unknown>;

/** Geprüfte Antwort des Kerns auf /geraet/binden für ein aktives Gerät (genau diese Felder gehen an die Desktop-App). */
export type BindeAntwort = {
  ergebnis: "neu_aktiv" | "neu_beantragt" | "bekannt";
  status: "aktiv";
  name: string;
  kennung: string;
  slot: number | null;
  stufe: "admin" | "mitglied";
  user_id: string;
  ziel: { benutzer: string; host: string; socket: string; hostschluessel: string };
};

export type EinrichtungsZustand = "nicht_noetig" | "eingerichtet" | "wartet" | "fehler";

export type Einrichtung = { zustand: EinrichtungsZustand; text?: string };

export type TunnelZustand = "verbunden" | "host" | "aus" | "unbekannt";

export type GeraetStatus = { eingerichtet: boolean; kennung: string | null; stufe: "admin" | "mitglied" | null; tunnel: TunnelZustand };

export const WIEDERHOLUNG_MS = 60_000; // wartet und erste Fehler (der Kern erlaubt höchstens 20 Aufrufe je Schritt in 10 Minuten)
export const FEHLER_PAUSE_MS = 300_000; // nach 3 Fehlern in Folge
export const FEHLER_SCHWELLE = 3;
export const STATUS_INTERVALL_MS = 60_000;

const RE_HOSTNAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,62}$/;
const RE_PUBKEY = /^ssh-ed25519 [A-Za-z0-9+/]{68}$/;
const RE_FINGERPRINT = /^SHA256:[A-Za-z0-9+/]{43}$/;
const RE_NONCE = /^[A-Za-z0-9_-]{8,200}$/;
const RE_SIGNATUR = /^[A-Za-z0-9_-]{86}$/;
const RE_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const RE_KENNUNG = /^pc-[a-z0-9]+(?:-[a-z0-9]+)*$/; // wie devices_kennung_chk; höchstens 23 Zeichen
const RE_BENUTZER = /^wamo-t-(0[1-9]|[1-5][0-9]|6[0-4])$/;
const RE_SOCKET = /^\/run\/wamo-pc\/t(0[1-9]|[1-5][0-9]|6[0-4])\/pc\.sock$/;
const OKTETT = "(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])";
const RE_IPV4 = new RegExp(`^${OKTETT}(?:\\.${OKTETT}){3}$`);
const RE_HOSTSCHLUESSEL = /^ssh-ed25519 [A-Za-z0-9+/]{68}$/;

const TEXT_ALLGEMEIN = "Die Einrichtung deines Laptops ist fehlgeschlagen.";
const TEXT_KERN_UNGUELTIG = "Die Antwort des Kerns zur Einrichtung deines Laptops ist ungültig.";
const TEXT_KERN_WEG = "Der Kern ist nicht erreichbar.";
const TEXT_GESPERRT = "Dein Laptop ist gesperrt. Bitte wende dich an einen Admin.";
const TEXT_WIDERRUFEN = "Die Freigabe deines Laptops wurde widerrufen. Bitte wende dich an einen Admin.";

type Objekt = Record<string, unknown>;

const istObjekt = (v: unknown): v is Objekt => typeof v === "object" && v !== null && !Array.isArray(v);

const istSteuerzeichen = (zeichen: string) => zeichen.charCodeAt(0) < 32 || zeichen.charCodeAt(0) === 127;
const hatSteuerzeichen = (text: string) => Array.from(text).some(istSteuerzeichen);

/** Text zur Anzeige: nur Text, ohne Steuerzeichen, auf 200 Zeichen gekürzt; sonst der Ersatztext. */
export function anzeigeText(v: unknown, ersatz: string): string {
  if (typeof v !== "string") return ersatz;
  const t = Array.from(v, (z) => (istSteuerzeichen(z) ? " " : z)).join("").trim().slice(0, 200);
  return t || ersatz;
}

const fehler = (text: string): Einrichtung => ({ zustand: "fehler", text });

/** Antwort der Brücke: { ok: true, ... } oder { ok: false, text }. */
function brueckeErgebnis(v: unknown): { ok: true; daten: Objekt } | { ok: false; text: string } {
  if (!istObjekt(v)) return { ok: false, text: TEXT_ALLGEMEIN };
  if (v.ok === true) return { ok: true, daten: v };
  return { ok: false, text: anzeigeText(v.text, TEXT_ALLGEMEIN) };
}

/** Strenge Prüfung der Antwort des Kerns für ein aktives Gerät; null, wenn irgendetwas nicht stimmt. */
export function pruefeAktiveAntwort(a: Objekt, userId: string): BindeAntwort | null {
  const ergebnis = a.ergebnis;
  if (ergebnis !== "neu_aktiv" && ergebnis !== "neu_beantragt" && ergebnis !== "bekannt") return null;
  const stufe = a.stufe;
  if (stufe !== "admin" && stufe !== "mitglied") return null;
  const { name, kennung, slot, user_id: besitzer, ziel } = a;
  if (typeof name !== "string" || name.length > 100 || hatSteuerzeichen(name)) return null;
  if (typeof kennung !== "string" || !RE_KENNUNG.test(kennung) || kennung.length > 23) return null;
  if (slot !== null && slot !== undefined && !(typeof slot === "number" && Number.isInteger(slot) && slot >= 1 && slot <= 64)) {
    return null;
  }
  if (typeof besitzer !== "string" || !RE_UUID.test(besitzer) || besitzer !== userId) return null;
  if (!istObjekt(ziel)) return null;
  const { benutzer, host, socket, hostschluessel } = ziel;
  if (typeof benutzer !== "string" || !RE_BENUTZER.test(benutzer)) return null;
  if (typeof host !== "string" || !RE_IPV4.test(host)) return null;
  if (typeof socket !== "string" || !RE_SOCKET.test(socket) || socket !== `/run/wamo-pc/t${benutzer.slice(-2)}/pc.sock`) {
    return null;
  }
  if (typeof hostschluessel !== "string" || !RE_HOSTSCHLUESSEL.test(hostschluessel)) return null;
  return {
    ergebnis,
    status: "aktiv",
    name: name.trim(),
    kennung,
    slot: typeof slot === "number" ? slot : null,
    stufe,
    user_id: besitzer,
    ziel: { benutzer, host, socket, hostschluessel },
  };
}

/** Status der Desktop-App ({ ok: true, eingerichtet, kennung, stufe, tunnel }) prüfen; null bei Unsinn. */
export function pruefeStatusAntwort(v: unknown): GeraetStatus | null {
  if (!istObjekt(v) || v.ok !== true || typeof v.eingerichtet !== "boolean") return null;
  const tunnel = v.tunnel;
  if (tunnel !== "verbunden" && tunnel !== "host" && tunnel !== "aus" && tunnel !== "unbekannt") return null;
  return {
    eingerichtet: v.eingerichtet,
    kennung: typeof v.kennung === "string" && RE_KENNUNG.test(v.kennung) && v.kennung.length <= 23 ? v.kennung : null,
    stufe: v.stufe === "admin" || v.stufe === "mitglied" ? v.stufe : null,
    tunnel,
  };
}

export type EinrichtenOptionen = {
  /** window.wamoDesktop?.geraet; fehlt es (Browser, ältere App), ist nichts zu tun */
  desktop: GeraetBruecke | undefined | null;
  kernJson: KernJson;
  /** id des Supabase-Benutzers der Sitzung */
  userId: string | undefined | null;
};

/**
 * Ein Durchlauf der Einrichtung. Wirft nie: Ergebnis ist "eingerichtet" (Gerät aktiv, Dateien geschrieben, Tunnel
 * gestartet), "wartet" (Freigabe durch einen Admin steht aus), "fehler" (mit anzeigbarem Text) oder "nicht_noetig".
 * Der Aufruf ist wiederholbar: der Kern antwortet beim selben Schlüssel mit "bekannt".
 */
export async function einrichten({ desktop, kernJson, userId }: EinrichtenOptionen): Promise<Einrichtung> {
  if (!desktop || !userId) return { zustand: "nicht_noetig" };
  if (!RE_UUID.test(userId)) return fehler("Die Anmeldung enthält keine gültige Benutzerkennung.");
  try {
    // 1. Schlüssel (wird beim ersten Mal erzeugt)
    const vorb = brueckeErgebnis(await desktop.vorbereiten());
    if (!vorb.ok) return fehler(vorb.text);
    const { pubkey, hostname, fingerprint } = vorb.daten;
    if (typeof pubkey !== "string" || !RE_PUBKEY.test(pubkey)) return fehler(TEXT_ALLGEMEIN);
    if (typeof hostname !== "string" || !RE_HOSTNAME.test(hostname)) return fehler(TEXT_ALLGEMEIN);
    if (fingerprint !== undefined && (typeof fingerprint !== "string" || !RE_FINGERPRINT.test(fingerprint))) {
      return fehler(TEXT_ALLGEMEIN);
    }

    // 2. Herausforderung des Kerns
    const herausforderung = await kernJson("/geraet/herausforderung");
    const nonce = istObjekt(herausforderung) ? herausforderung.nonce : undefined;
    if (typeof nonce !== "string" || !RE_NONCE.test(nonce)) return fehler(TEXT_KERN_UNGUELTIG);

    // 3. Signatur mit dem Geräteschlüssel (die Desktop-App signiert nur die feste Bindungsnachricht)
    const sig = brueckeErgebnis(await desktop.signieren({ nonce, userId }));
    if (!sig.ok) return fehler(sig.text);
    const signatur = sig.daten.signatur;
    if (typeof signatur !== "string" || !RE_SIGNATUR.test(signatur)) return fehler(TEXT_ALLGEMEIN);

    // 4. Bindung beim Kern (idempotent)
    const antwort = await kernJson("/geraet/binden", "POST", { pubkey, hostname, nonce, signatur });
    if (!istObjekt(antwort)) return fehler(TEXT_KERN_UNGUELTIG);
    const status = antwort.status;
    if (status === "beantragt") return { zustand: "wartet" };
    if (status === "gesperrt") return fehler(TEXT_GESPERRT);
    if (status === "widerrufen") return fehler(TEXT_WIDERRUFEN);
    if (status !== "aktiv") return fehler(TEXT_KERN_UNGUELTIG);
    const geprueft = pruefeAktiveAntwort(antwort, userId);
    if (!geprueft) return fehler(TEXT_KERN_UNGUELTIG);

    // 5. Dateien schreiben und Tunnel starten (in der Desktop-App)
    const aktiv = brueckeErgebnis(await desktop.aktivieren(geprueft));
    if (!aktiv.ok) return fehler(aktiv.text);
    return { zustand: "eingerichtet" };
  } catch (e) {
    return fehler(anzeigeText(e instanceof Error ? e.message : null, TEXT_KERN_WEG));
  }
}

/**
 * Wie lange bis zum nächsten Durchlauf? null: gar nicht mehr (fertig oder nicht nötig). "wartet" alle 30 s; Fehler
 * zunächst auch alle 30 s, nach 3 Fehlern in Folge nur noch alle 5 Minuten. Nie ohne Pause.
 */
export function naechsteWartezeitMs(zustand: EinrichtungsZustand, fehlerInFolge: number): number | null {
  if (zustand === "wartet") return WIEDERHOLUNG_MS;
  if (zustand === "fehler") return fehlerInFolge >= FEHLER_SCHWELLE ? FEHLER_PAUSE_MS : WIEDERHOLUNG_MS;
  return null;
}

/** Die eine Zeile, die die Oberfläche zeigt (null: alles in Ordnung, nichts anzeigen). */
export function hinweisZeile(zustand: EinrichtungsZustand, text: string | undefined, tunnel: TunnelZustand | null): string | null {
  if (zustand === "wartet") return "Dein Laptop wartet auf die Freigabe durch einen Admin.";
  if (zustand === "fehler") return text || TEXT_ALLGEMEIN;
  if (zustand === "eingerichtet" && tunnel !== null && tunnel !== "verbunden") {
    return "Die Verbindung deines Laptops zu WAMO wird aufgebaut.";
  }
  return null;
}
