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
/** So lange nach "Bot bereit" darf die eigene Mikrofonspur auf sich warten lassen, bevor die Oberfläche warnt. */
export const SPUR_MS = 5000;
/** So lange wartet die Oberfläche nach dem Ende einer Spur auf die Ersatzspur (Gerätewechsel), bevor sie warnt. */
export const SPUR_ENDE_MS = 1500;

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
    (istBluetoothGeraet(geraet) ? BLUETOOTH_HINWEIS : "Am Dock mit zugeklapptem Laptop ist das eingebaute Mikrofon meist aus.")
  );
}

/** Bluetooth-Kopfhörer (auch im Freisprech-Profil). Das eingebaute Mikrofon und Kabel-Headsets gehören nicht dazu. */
const BLUETOOTH_GERAET = /bluetooth|airpods|buds|freisprech|hands-?free/i;
export const istBluetoothGeraet = (geraet?: string | null): boolean => Boolean(geraet && BLUETOOTH_GERAET.test(geraet));

/**
 * 12.11.2026: Das Mikrofon eines Bluetooth-Kopfhörers lieferte in WAMO erst keinen Ton, später ging es. Windows wechselt bei Bluetooth zwischen
 * Stereo-Wiedergabe und Freisprech-Betrieb, nur im Freisprech-Betrieb gibt es ein Mikrofon. Das hilft meist: aus- und einschalten,
 * ein zweites verbundenes Bluetooth-Gerät trennen, Ausgabe und Eingabe auf denselben Kopfhörer stellen.
 */
const BLUETOOTH_HINWEIS =
  "Bei einem Bluetooth-Kopfhörer wechselt Windows manchmal nicht in den Freisprech-Betrieb: Schalte den Kopfhörer aus und wieder ein, " +
  "trenne ein zweites verbundenes Bluetooth-Gerät und wähle in Windows für Ausgabe und Eingabe denselben Kopfhörer.";

/** Warnung vom Kern: bei ihm kommt seit einigen Sekunden nur digitale Stille an. */
export function kernStilleText(sekunden: number): string {
  return (
    `Beim Sprachkern kommt seit ${sekunden} Sekunden nur Stille vom Mikrofon an. Wähle unten ein anderes Mikrofon ` +
    "oder beende das Gespräch und starte es neu."
  );
}

/** Warnung vom Kern: es kommen gar keine Audiopakete mehr an (die Sendespur ist abgerissen). */
export function kernFehltText(sekunden: number): string {
  return (
    `Beim Sprachkern kommt seit ${sekunden} Sekunden kein Ton mehr vom Mikrofon an, die Mikrofonspur ist abgerissen. ` +
    "Wähle unten ein Mikrofon aus oder beende das Gespräch und starte es neu."
  );
}

/** Die Spur des Mikrofons ist beendet (Gerät abgezogen oder von Windows entzogen) und es folgte keine neue. */
export function spurEndeText(geraet?: string | null): string {
  const welches = geraet ? ` („${geraet}“)` : "";
  return (
    `Das Mikrofon${welches} wurde getrennt oder von Windows entzogen, WAMO hört dich nicht mehr. Schließe es wieder ` +
    "an oder wähle unten ein anderes Mikrofon."
  );
}

/** Nach dem Verbinden kam nie eine Mikrofonspur an. */
export function keineSpurText(): string {
  return (
    "Es kommt keine Mikrofonspur an, WAMO hört dich nicht. Wähle unten ein Mikrofon aus, prüfe " +
    `${WINDOWS_EINGABE} das Gerät oder beende das Gespräch und starte es neu.`
  );
}

/** Windows verweigert Desktop-Apps das Mikrofon (Datenschutz): dann liefert es nur Stille, ohne Fehler. */
export function windowsGesperrtText(): string {
  return (
    "Windows blockiert das Mikrofon für Desktop-Apps, deshalb kommt nur Stille an. Erlaube den Zugriff in Windows " +
    "unter Einstellungen > Datenschutz und Sicherheit > Mikrofon (Zugriff auf das Mikrofon und Desktop-Apps " +
    "zulassen) und starte das Gespräch neu."
  );
}

/** Zustand von systemPreferences.getMediaAccessStatus("microphone"): nur "denied" und "restricted" sperren. */
export function windowsGesperrt(status: unknown): boolean {
  return status === "denied" || status === "restricted";
}

/** Logzeile (landet in logs/oberflaeche.log der Desktop-App): das Mikrofon liefert echten Ton. */
export function tonLog(geraet: string | null | undefined, spitzeDb: number): string {
  const name = geraet ? geraet.slice(0, 120) : "unbekanntes Gerät";
  return `Mikrofon liefert Ton: ${name}, Spitze ${Math.round(spitzeDb)} dBFS`;
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

// ---------- "Ohne Mikrofon weiter" ----------
/**
 * Wählt die Person bei einer Mikrofon-Warnung "Ohne Mikrofon weiter", bleibt die Stille-Warnung für dieses Gerät aus, auch bei einem
 * neuen Gespräch (12.11.2026: Bei einem Mikrofon, das Windows als "OK" führt und nur Nullen liefert, kam die Warnung bei jedem Start
 * wieder). Das Gedächtnis gilt nur für dieses Fenster bis zum Neuladen und wird nirgends gespeichert. Liefert das Gerät später wieder
 * Ton (pruefeStille meldet "wieder_da"), warnt WAMO bei der nächsten Stille wieder. Andere Fehler (kein Gerät, von Windows gesperrt,
 * abgerissene Spur) bleiben davon unberührt.
 */
const OHNE_MIKROFON_MAX = 20;
const ohneMikrofon = new Set<string>();

/** Schlüssel wie in der Oberfläche (WamoState.mikrofon): die Bezeichnung der Spur, auf 120 Zeichen gekürzt; ohne Namen ein gemeinsamer Eintrag. */
const geraeteSchluessel = (geraet: string | null | undefined): string => (geraet ?? "").trim().slice(0, 120) || "unbekanntes Gerät";

export function stilleAusblenden(geraet: string | null | undefined): void {
  const k = geraeteSchluessel(geraet);
  ohneMikrofon.delete(k); // wieder ans Ende: das zuletzt gewählte Gerät fällt zuletzt heraus
  ohneMikrofon.add(k);
  while (ohneMikrofon.size > OHNE_MIKROFON_MAX) {
    const aeltestes = ohneMikrofon.values().next().value;
    if (aeltestes === undefined) break;
    ohneMikrofon.delete(aeltestes);
  }
}

export const stilleAusgeblendet = (geraet: string | null | undefined): boolean => ohneMikrofon.has(geraeteSchluessel(geraet));

export function stilleWiederAufnehmen(geraet: string | null | undefined): void {
  ohneMikrofon.delete(geraeteSchluessel(geraet));
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
