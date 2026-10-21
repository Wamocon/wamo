/**
 * Mikrofon: Pegel der eigenen Spur, Stille-Wächter, verständliche Meldungen mit Abhilfe und die Gerätewahl.
 *
 * Hintergrund: Liefert das Standardgerät von Windows nur digitale Stille (Laptop zugeklappt am Dock, Dock- oder
 * Headset-Mikrofon ohne Signal, Stummschaltung am Gerät), läuft das Gespräch scheinbar normal, nur hört WAMO nichts.
 * Die Oberfläche misst deshalb die eigene Spur selbst (SmallWebRTC meldet keinen lokalen Pegel) und sagt nach
 * STILLE_MS klar, was los ist und was hilft. Der Kern meldet zusätzlich, wenn bei ihm nur Stille ankommt.
 *
 * Reine Logik ohne Browser-Abhängigkeit (Tests: mikrofon.test.ts); Zugriff auf Geräte und Audio liegt in useWamo.ts.
 */

/** Unter dieser Spitze (dBFS) gilt ein Messfenster als still. Echte Mikrofone liegen auch im leisen Raum darüber. */
export const STILLE_DBFS = -100;
/** So lange muss die Spur ununterbrochen still sein, bevor die Oberfläche warnt. */
export const STILLE_MS = 3000;

/** Spitze und RMS eines Messfensters. spitzeDb ist -Infinity bei exakt null. */
export function messen(buf: ArrayLike<number>): { rms: number; spitzeDb: number } {
  let summe = 0;
  let spitze = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = buf[i];
    summe += v * v;
    const a = Math.abs(v);
    if (a > spitze) spitze = a;
  }
  const rms = buf.length ? Math.sqrt(summe / buf.length) : 0;
  return { rms, spitzeDb: spitze > 0 ? 20 * Math.log10(spitze) : Number.NEGATIVE_INFINITY };
}

/** Pegel für Stimmlinie, Kern und Rand (gleiche Skala wie WAMOs Pegel: Sprache etwa 0,1 bis 0,3). */
export const anzeigePegel = (rms: number) => Math.min(1, Math.max(0, rms * 2));

export type StilleZustand = { stillSeit: number | null; gemeldet: boolean };

export const neueStille = (): StilleZustand => ({ stillSeit: null, gemeldet: false });

/**
 * Ein Messwert für den Wächter. aktiv: verbunden, Mikrofon nicht stumm geschaltet, Messung läuft.
 * Ergebnis "still" genau einmal je stiller Phase, "wieder_da" einmal, wenn danach wieder Ton kommt oder das
 * Mikrofon abgeschaltet wird (dann ist die Warnung hinfällig), sonst null.
 */
export function pruefeStille(z: StilleZustand, spitzeDb: number, jetzt: number, aktiv: boolean): "still" | "wieder_da" | null {
  if (!aktiv || spitzeDb > STILLE_DBFS) {
    z.stillSeit = null;
    if (z.gemeldet) {
      z.gemeldet = false;
      return "wieder_da";
    }
    return null;
  }
  if (z.stillSeit === null) z.stillSeit = jetzt;
  if (!z.gemeldet && jetzt - z.stillSeit >= STILLE_MS) {
    z.gemeldet = true;
    return "still";
  }
  return null;
}

const WINDOWS_EINGABE = "in Windows unter Einstellungen > System > Sound > Eingabe";

/** Warnung der Oberfläche: die eigene Spur ist seit STILLE_MS still. */
export function stilleText(geraet?: string | null): string {
  const welches = geraet ? ` („${geraet}“)` : "";
  return (
    `Das Mikrofon${welches} liefert keinen Ton, WAMO hört dich nicht. Prüfe die Stummschaltung am Gerät oder ` +
    `Headset, wähle unten ein anderes Mikrofon oder stelle ${WINDOWS_EINGABE} das richtige Gerät ein. ` +
    "Am Dock mit zugeklapptem Laptop ist das eingebaute Mikrofon meist aus."
  );
}

/** Warnung vom Kern: bei ihm kommt seit einigen Sekunden nur digitale Stille an. */
export function kernStilleText(sekunden: number): string {
  return (
    `Beim Sprachkern kommt seit ${sekunden} Sekunden nur Stille vom Mikrofon an. Wähle unten ein anderes Mikrofon ` +
    "oder beende das Gespräch und starte es neu."
  );
}

/** getUserMedia ist fehlgeschlagen: Meldung je Fehlerart, immer mit Abhilfe. */
export function mikrofonFehlerText(name?: string): string {
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return (
        "Mikrofon gesperrt: WAMO darf das Mikrofon nicht nutzen. In Windows unter Einstellungen > Datenschutz und " +
        "Sicherheit > Mikrofon den Zugriff für Desktop-Apps erlauben (im Browser das Mikrofon für diese Seite " +
        "freigeben), dann das Gespräch neu starten."
      );
    case "NotFoundError":
    case "OverconstrainedError":
      return `Kein Mikrofon gefunden. Mikrofon oder Headset anschließen, ${WINDOWS_EINGABE} prüfen und neu starten.`;
    case "NotReadableError":
    case "AbortError":
      return (
        "Das Mikrofon lässt sich nicht öffnen, vermutlich nutzt ein anderes Programm es exklusiv. Programme mit " +
        "Mikrofon (Teams, Zoom, Aufnahme) schließen oder ein anderes Mikrofon wählen und neu starten."
      );
    default:
      return `Das Mikrofon lässt sich nicht öffnen. ${WINDOWS_EINGABE[0].toUpperCase()}${WINDOWS_EINGABE.slice(1)} das Gerät prüfen und neu starten.`;
  }
}

/** Name eines Fehlers aus onDeviceError (DeviceError mit sourceError) oder einem DOMException-artigen Objekt. */
export function fehlerName(e: unknown): string | undefined {
  if (typeof e !== "object" || e === null) return undefined;
  const quelle = (e as { details?: { sourceError?: unknown } }).details?.sourceError;
  const n = (quelle as { name?: unknown } | undefined)?.name ?? (e as { name?: unknown }).name;
  return typeof n === "string" ? n.slice(0, 40) : undefined;
}

// ---------- Gerätewahl (je Gerät gespeichert) ----------
export const MIKROFON_SCHLUESSEL = "wamo.mikrofon";

export type Mikrofon = { id: string; label: string };

/** Nur echte Geräte mit Kennung; "default" und "communications" sind Aliase von Windows. */
export function geraeteListe(geraete: ArrayLike<{ kind: string; deviceId: string; label: string }>): Mikrofon[] {
  const liste: Mikrofon[] = [];
  for (let i = 0; i < geraete.length; i++) {
    const g = geraete[i];
    if (g.kind !== "audioinput" || !g.deviceId || g.deviceId === "default" || g.deviceId === "communications") continue;
    liste.push({ id: g.deviceId, label: (g.label || "Mikrofon").slice(0, 120) });
  }
  return liste;
}

/** Gewähltes Gerät, wenn es noch angeschlossen ist; sonst null (dann gilt das Standardgerät von Windows). */
export function startGeraet(liste: Mikrofon[], gewuenscht: string | null): string | null {
  return gewuenscht && liste.some((m) => m.id === gewuenscht) ? gewuenscht : null;
}

export function gewaehltesMikrofon(): string | null {
  try {
    const v = localStorage.getItem(MIKROFON_SCHLUESSEL);
    return v && v.length <= 200 ? v : null;
  } catch {
    return null;
  }
}

export function merkeMikrofon(id: string | null) {
  try {
    if (id) localStorage.setItem(MIKROFON_SCHLUESSEL, id);
    else localStorage.removeItem(MIKROFON_SCHLUESSEL);
  } catch {
    // privater Modus o. ä.: dann gilt die Wahl nur für dieses Gespräch
  }
}

// ---------- Sendespur ----------
type Sender = { track: MediaStreamTrack | null; replaceTrack: (t: MediaStreamTrack | null) => Promise<void> };
type Transceiver = { sender: Sender; receiver: { track: { kind: string } | null } };

/**
 * Neue Mikrofonspur auf die laufende Verbindung legen. Der SmallWebRTC-Transport (1.11) ersetzt die Sendespur nur
 * für seinen Daily-Medienmanager; mit dem WavMediaManager bliebe nach einem Gerätewechsel die alte, beendete Spur
 * am Sender, und beim Kern käme gar nichts mehr an. Die PeerConnection ist im Transport nicht öffentlich, deshalb
 * der Zugriff über das Feld "pc"; fehlt es (andere Version, noch keine Verbindung), passiert nichts.
 */
export async function sendeSpurErsetzen(transport: unknown, track: MediaStreamTrack): Promise<"ersetzt" | "gleich" | "kein_sender"> {
  const pc = (transport as { pc?: { getTransceivers?: () => Transceiver[] } | null } | null)?.pc;
  const audio = pc?.getTransceivers?.().find((t) => t.receiver?.track?.kind === "audio");
  if (!audio) return "kein_sender";
  if (audio.sender.track === track) return "gleich";
  await audio.sender.replaceTrack(track);
  return "ersetzt";
}
