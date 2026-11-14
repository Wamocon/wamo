/**
 * Verbindungs-Watchdog: Kommt nach dem Start der Verbindung nicht innerhalb von 20 Sekunden die Betriebsmeldung des
 * Kerns (er sendet sie, sobald die Oberfläche bereit ist), hängt der Aufbau (zum Beispiel Büro-Kern ohne WireGuard).
 * Dann bricht useWamo ab und zeigt eine klare Meldung mit "Erneut verbinden", statt endlos "Verbinde …" zu zeigen.
 *
 * Reine Logik ohne React und ohne Browser (verbindungsfrist.test.ts). Jede Frist gehört zu einer Generation der
 * Verbindung: läuft sie nach einem Trennen oder einem neuen Start ab, passiert nichts (kein Rennen).
 */

export const VERBINDUNG_FRIST_MS = 20_000;

/** Höchstens so oft wird die Frist verlängert, solange die Person die Mikrofonfrage noch nicht beantwortet hat (6 x 20 s). */
export const MAX_VERLAENGERUNGEN = 6;

export type Frist = {
  /** Frist für diese Generation starten; eine laufende Frist wird ersetzt */
  starte: (gen: number) => void;
  /** Frist beenden (Betriebsmeldung da, Trennen, Abmelden) */
  stoppe: () => void;
  laeuft: () => boolean;
};

/**
 * Fristgeber. Läuft die Frist ab, fragt er zuerst verlaengern (zum Beispiel solange der Browser noch auf die Antwort
 * der Person zur Mikrofonfrage wartet): true wartet eine weitere Frist, false ruft beiAblauf auf.
 */
export function erstelleFrist(opts: {
  frist?: number;
  /** aktuelle Generation der Verbindung */
  gen: () => number;
  /** wurde die Betriebsmeldung schon empfangen? */
  fertig: () => boolean;
  /** Soll weiter gewartet werden? verlaengerungen: wie oft die Frist schon verlängert wurde. */
  verlaengern?: (verlaengerungen: number) => boolean | Promise<boolean>;
  /** Die Frist ist verstrichen: abbrechen und melden. */
  beiAblauf: () => void;
  setTimer?: typeof setTimeout;
  loescheTimer?: typeof clearTimeout;
}): Frist {
  const dauer = opts.frist ?? VERBINDUNG_FRIST_MS;
  const set = opts.setTimer ?? setTimeout;
  const loesche = opts.loescheTimer ?? clearTimeout;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let eigene = -1; // Generation der laufenden Frist

  const stoppe = () => {
    if (timer !== null) loesche(timer);
    timer = null;
    eigene = -1;
  };

  const plane = (gen: number, verlaengerungen: number) => {
    stoppe();
    eigene = gen;
    timer = set(() => {
      timer = null;
      // getrennt, neu gestartet oder die Betriebsmeldung ist da: nichts mehr zu tun
      const veraltet = () => opts.gen() !== gen || eigene !== gen || timer !== null || opts.fertig();
      if (veraltet()) return;
      void Promise.resolve(opts.verlaengern?.(verlaengerungen) ?? false).then(
        (weiter) => {
          if (veraltet()) return;
          if (weiter) plane(gen, verlaengerungen + 1);
          else {
            eigene = -1;
            opts.beiAblauf();
          }
        },
        () => {
          if (!veraltet()) {
            eigene = -1;
            opts.beiAblauf();
          }
        },
      );
    }, dauer);
  };

  return { starte: (gen) => plane(gen, 0), stoppe, laeuft: () => timer !== null };
}

/**
 * Soll die Frist verlängert werden? Ja, solange der Browser die Person noch nach dem Mikrofon fragt ("prompt"):
 * Das Warten auf ihre Antwort ist kein hängender Aufbau. Ist der Zustand unbekannt (ältere Browser), läuft die Frist ab.
 */
export function sollVerlaengern(mikrofonZustand: string | null | undefined, verlaengerungen: number): boolean {
  return mikrofonZustand === "prompt" && verlaengerungen < MAX_VERLAENGERUNGEN;
}

/** Meldung bei verstrichener Frist; sagt, was zu prüfen ist, je nach Kern der Betriebsart. */
export function fristMeldung(kern: "buero" | "unterwegs" | null): { art: "verbindung"; text: string } {
  const sekunden = Math.round(VERBINDUNG_FRIST_MS / 1000);
  const text =
    kern === "buero"
      ? `Keine Antwort vom Büro-Kern nach ${sekunden} Sekunden. Bist du im Büro-LAN oder per WireGuard verbunden? Dann bitte erneut verbinden.`
      : kern === "unterwegs"
        ? `Keine Antwort vom Voice-Core auf diesem PC nach ${sekunden} Sekunden. Läuft die Desktop-App? Dann bitte erneut verbinden.`
        : `Keine Antwort von W.A.I.S. nach ${sekunden} Sekunden. Bitte Verbindung prüfen und erneut verbinden.`;
  return { art: "verbindung", text };
}
